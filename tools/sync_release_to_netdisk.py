# -*- coding: utf-8 -*-
"""把 GitHub Release 的产物同步到百度网盘 `/Masterduel-stats`。

**为什么要有这个**：百度网盘 MCP 只有 `file_upload_by_url`（给 URL 自己去下）
和 `file_upload_by_content`（纯文本 ≤2 万字），**没有「传本机文件」的工具**。
所以同步只能绕一圈：先让 GitHub Release 挂上产物，再让百度服务器回源下载。

⚠️ **必须先把 GitHub 的 302 跳转解开**（2026-10-23 实测）：
   直接喂 `https://github.com/.../releases/download/<tag>/<file>`
   → 百度回 `errno=1003 上传文件失败`（它不跟 302）
   先 HEAD 跟随跳转拿到 `release-assets.githubusercontent.com` 的
   **签名直链**（带 `sig=` / `se=` 过期时间）
   → 上传成功

签名直链的 `se=` 有效期约 1 小时，所以**每传一个文件前现解一次**，
不要提前批量解好。

用法::

    python tools/sync_release_to_netdisk.py --list          # 只对账：Release vs 网盘
    python tools/sync_release_to_netdisk.py --dry-run       # 打印要传什么，不传
    python tools/sync_release_to_netdisk.py                 # 真传（默认 tag = core/version.py）
    python tools/sync_release_to_netdisk.py --tag v1.1.2    # 指定版本
    python tools/sync_release_to_netdisk.py --only patch    # 只传文件名含 patch 的
    python tools/sync_release_to_netdisk.py --verify        # 传完回读核对大小

Token 来源（按优先级）：
    1. 环境变量 `BAIDU_NETDISK_TOKEN`
    2. `~/.zcode/cli/config.json` 的
       `mcp.servers.baidu-netdisk.headers.Authorization`（去掉 `Bearer ` 前缀）

网盘上没有「删除」工具 —— 本脚本**绝不覆盖**：同名文件已存在就跳过，
大小不一致只告警。要重传请先在网盘界面手动删掉那个文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import requests

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZCODE_CFG = os.path.join(os.path.expanduser('~'), '.zcode', 'cli', 'config.json')

REPO = 'pinkysware/masterduel-stats'
SSE_URL = 'https://mcp-pan.baidu.com/sse'
NETDISK_DIR = '/Masterduel-stats'

#: 一个 37MB 的包走百度回源，实测要几十秒；给足余量。
UPLOAD_TIMEOUT = 1800
SSE_READ_TIMEOUT = 1900


# ---------------------------------------------------------------- 版本 / 产物

def current_version() -> str:
    """读主仓 core/version.py 的 __version__（版本号唯一真源）。"""
    path = os.path.join(PROJ, 'core', 'version.py')
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('__version__'):
                return line.split('=', 1)[1].strip().strip('\'"')
    raise SystemExit('core/version.py 里没找到 __version__')


def release_assets(tag: str) -> list[dict]:
    """拉某个 tag 的 Release，返回产物列表（公开仓库，不需要 token）。"""
    url = 'https://api.github.com/repos/%s/releases/tags/%s' % (REPO, tag)
    r = requests.get(url, timeout=30,
                     headers={'Accept': 'application/vnd.github+json'})
    if r.status_code == 404:
        raise SystemExit('GitHub 上找不到 Release %s（tag 推了吗？Release 建了吗？）' % tag)
    r.raise_for_status()
    return r.json()['assets']


def signed_url(asset_url: str) -> str:
    """跟随 GitHub 的 302，拿 release-assets 的签名直链。"""
    r = requests.head(asset_url, allow_redirects=True, timeout=60)
    r.raise_for_status()
    return r.url


# ---------------------------------------------------------------- MCP 客户端

def netdisk_token() -> str:
    tok = os.environ.get('BAIDU_NETDISK_TOKEN', '').strip()
    if tok:
        return tok
    if not os.path.exists(ZCODE_CFG):
        raise SystemExit('找不到 %s，也没设 BAIDU_NETDISK_TOKEN' % ZCODE_CFG)
    with open(ZCODE_CFG, encoding='utf-8') as f:
        cfg = json.load(f)
    auth = (cfg.get('mcp', {}).get('servers', {})
            .get('baidu-netdisk', {}).get('headers', {}).get('Authorization', ''))
    if not auth:
        raise SystemExit('ZCode 配置里没有 baidu-netdisk 的 Authorization 头')
    return auth.split('Bearer ', 1)[-1].strip()


async def _call(tool: str, args: dict, timeout: float = 120):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(SSE_URL,
                          headers={'Authorization': 'Bearer ' + netdisk_token()},
                          timeout=60, sse_read_timeout=SSE_READ_TIMEOUT) as (rd, wr):
        async with ClientSession(rd, wr) as s:
            await asyncio.wait_for(s.initialize(), 60)
            res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
            out = []
            for c in res.content:
                txt = getattr(c, 'text', None)
                if txt:
                    try:
                        out.append(json.loads(txt))
                    except json.JSONDecodeError:
                        out.append({'raw': txt})
            return out[0] if len(out) == 1 else out


def list_netdisk(directory: str = NETDISK_DIR) -> dict[str, dict]:
    """返回 {文件名: 条目}。目录不存在时返回空（MCP 会报错，视作空目录）。"""
    try:
        res = asyncio.run(_call('file_list', {'dir': directory}))
    except Exception as exc:  # noqa: BLE001 - 目录不存在不该炸掉整个对账
        print('  （读网盘目录失败，按空处理：%s）' % str(exc)[:120])
        return {}
    if isinstance(res, dict) and res.get('errno') not in (None, 0):
        print('  （网盘返回 errno=%s，按空处理）' % res.get('errno'))
        return {}
    items = (res or {}).get('data', {}).get('list', []) or []
    return {it['filename']: it for it in items}


def upload(url: str, filename: str, directory: str = NETDISK_DIR) -> dict:
    return asyncio.run(_call('file_upload_by_url',
                             {'url': url, 'dir': directory, 'filename': filename},
                             timeout=UPLOAD_TIMEOUT))


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description='GitHub Release -> 百度网盘同步')
    ap.add_argument('--tag', default=None, help='默认 = core/version.py 的版本号加 v')
    ap.add_argument('--dir', default=NETDISK_DIR, help='网盘目标目录')
    ap.add_argument('--only', default=None, help='只处理文件名含该子串的产物')
    ap.add_argument('--list', action='store_true', help='只对账，不上传')
    ap.add_argument('--dry-run', action='store_true', help='打印计划，不上传')
    ap.add_argument('--verify', action='store_true', help='传完回读核对大小')
    args = ap.parse_args()

    tag = args.tag or ('v' + current_version())
    print('版本：%s' % tag)

    assets = release_assets(tag)
    if not assets:
        raise SystemExit('Release %s 没有任何产物' % tag)
    if args.only:
        assets = [a for a in assets if args.only in a['name']]
        if not assets:
            raise SystemExit('没有文件名含 %r 的产物' % args.only)

    print('Release 产物 %d 个：' % len(assets))
    for a in assets:
        print('  %-45s %10.1f KB' % (a['name'], a['size'] / 1024))

    print('\n网盘 %s：' % args.dir)
    existing = list_netdisk(args.dir)
    for name, it in sorted(existing.items()):
        print('  %-45s %10.1f KB' % (name, (it.get('size') or 0) / 1024))

    todo, skip = [], []
    for a in assets:
        have = existing.get(a['name'])
        if have is None:
            todo.append(a)
        elif (have.get('size') or 0) == a['size']:
            skip.append((a, '已存在，大小一致'))
        else:
            skip.append((a, '已存在但大小不同（%s vs %s）—— 要重传请先手动删'
                          % (have.get('size'), a['size'])))

    print('\n待传 %d 个，跳过 %d 个：' % (len(todo), len(skip)))
    for a in todo:
        print('  + %s' % a['name'])
    for a, why in skip:
        print('  = %s（%s）' % (a['name'], why))

    if args.list or args.dry_run:
        print('\n%s：没有上传。' % ('--list' if args.list else '--dry-run'))
        return 0
    if not todo:
        print('\n没有需要上传的文件。')
        return 0

    print()
    failed = []
    for i, a in enumerate(todo, 1):
        name = a['name']
        print('[%d/%d] %s' % (i, len(todo), name))
        # 每个文件现解一次签名直链（1 小时会过期）
        try:
            direct = signed_url(a['browser_download_url'])
        except Exception as exc:  # noqa: BLE001
            print('   解析直链失败：%s' % str(exc)[:160])
            failed.append(name)
            continue
        try:
            res = upload(direct, name, args.dir)
        except Exception as exc:  # noqa: BLE001
            print('   上传异常：%s' % str(exc)[:160])
            failed.append(name)
            continue
        if isinstance(res, dict) and res.get('errno') not in (None, 0):
            print('   上传失败：errno=%s %s'
                  % (res.get('errno'), res.get('show_msg') or ''))
            failed.append(name)
            continue
        got = (res.get('data') or {}).get('size')
        print('   OK  size=%s  path=%s'
              % (got, (res.get('data') or {}).get('path')))

    if args.verify:
        print('\n回读核对：')
        after = list_netdisk(args.dir)
        for a in assets:
            it = after.get(a['name'])
            if it is None:
                print('  ✗ %s 不在目录里' % a['name'])
            elif (it.get('size') or 0) == a['size']:
                print('  ✓ %s %d 字节' % (a['name'], a['size']))
            else:
                print('  ! %s 大小不符：网盘 %s vs Release %s'
                      % (a['name'], it.get('size'), a['size']))

    if failed:
        print('\n失败 %d 个：%s' % (len(failed), ', '.join(failed)))
        return 1
    print('\n全部完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
