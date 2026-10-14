# -*- coding: utf-8 -*-
"""⛔ **已弃用（2026-10-15）** —— 段位/模式图标已改用**游戏本体解包原版**，
不再从任何第三方站点抓取。**别再跑本脚本的下载动作**：它会把
`data/rank_icons/*.webp` 覆盖回旧来源（untapped 静态资源）。

现状（真源说明见 `docs/素材解包说明.md` 与 `core/ranks.py` / `core/modes.py` 模块头）：

    data/rank_icons/*.webp   7 个大段位  <- 游戏本体 img_rankicon_01..07_l
    data/mode_icons/peak.webp           <- 游戏本体 img_rateicon_01_l
    data/coin_icons/*.png               <- 游戏本体 duelcointoss

保留本文件只为 `--list`（看现状）；下载路径已**主动拒绝**。
如需重新导出游戏素材，走解包流程（AssetStudioMod），不要走这里。

---

以下为**旧说明（历史存档）**：下载 7 个段位图标到 `data/rank_icons/`。

用法::

    python tools/fetch_rank_icons.py            # 缺哪个补哪个（幂等）
    python tools/fetch_rank_icons.py --force    # 全部重下
    python tools/fetch_rank_icons.py --list     # 只看现状，不联网

## 图标从哪来

untapped 的**静态资源**（Next.js 的 chunks 里嵌着图标 URL）：

    https://static.ygom.untapped.gg/_next/static/images/<Rank>-<hash>.webp

文件名带内容 hash，**所以硬编码 URL 会随它发版失效** —— 本脚本的做法是
**先去它的 JS chunk 里现场解析出 URL**，再下载。这样它换 hash 我们也不会断。

顺带还能拿到**官方的编号映射表**（chunk 里就有）：

    {1:"Rookie", 2:"Bronze", 3:"Silver", 4:"Gold",
     5:"Platinum", 6:"Diamond", 7:"Master"}

这条与 `core/ranks.py` 的 `RANK_NAMES` 一致；脚本会**当场核对**，不一致会喊。

## ⚠️ 边界（AGENTS.md 铁律 4）

**只取静态素材，不取战绩数据。** 战绩仍然只来自日志 + 内存。
`Network\\Cookies` / `Session Storage` 一律不读。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import ranks                                               # noqa: E402

PAGE = ('https://ygom.untapped.gg/zh/meta/tier-list'
        '?deckBuilding=all&rankRange=BRONZE_TO_MASTER'
        '&timeFrame=CurrentMeta&viewArchetypesBy=MostPopular')
HOST = 'https://static.ygom.untapped.gg'
DST = ranks.ICON_DIR
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

RE_ICON = re.compile(
    re.escape(HOST) + r'/_next/static/images/([A-Za-z]+)-[0-9a-f]+\.webp')
RE_MAP = re.compile(r'\{\s*1:"Rookie".{0,120}?7:"Master"\s*\}')


def get(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def discover(verbose: bool = True):
    """去 untapped 的 JS chunk 里现场解析图标 URL -> ``{段位名: url}``。"""
    html = get(PAGE)
    chunks = sorted(set(re.findall(r'src="([^"]+\.js)"', html)))
    if verbose:
        print('页面 JS chunk: %d 个' % len(chunks))

    icons, parts = {}, []
    for u in chunks:
        try:
            text = get(u)
        except Exception:
            continue
        parts.append(text)                      # ⚠️ 必须累加：映射表可能在任一 chunk 里
        for m in RE_ICON.finditer(text):
            icons.setdefault(m.group(1), m.group(0))
    return icons, ''.join(parts)


def check_mapping(blob: str) -> None:
    """核对我们 `core/ranks.py` 的映射表跟它代码里的一致（不一致就喊）。"""
    m = RE_MAP.search(blob or '')
    if not m:
        print('  ⚠️ 没能从 chunk 里读到官方映射表（不影响下载，只是没核对上）')
        return
    got = dict(re.findall(r'(\d+):"([A-Za-z]+)"', m.group(0)))
    theirs = {int(k): v for k, v in got.items()}
    ours = dict(ranks.RANK_NAMES)
    if theirs == ours:
        print('  ✅ 段位映射与 core/ranks.py 一致：%s' % theirs)
    else:
        print('  ❌ 段位映射**对不上**了！')
        print('     它 :', theirs)
        print('     我们:', ours)
        print('     → 先改 core/ranks.py 再继续')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='下载段位图标（只取素材，不取战绩）')
    ap.add_argument('--force', action='store_true', help='全部重下')
    ap.add_argument('--list', action='store_true', help='只看现状，不联网')
    args = ap.parse_args(argv)

    os.makedirs(DST, exist_ok=True)

    if args.list:
        print('本地段位图标（%s）：' % os.path.relpath(DST, PROJ))
        for r, n in sorted(ranks.RANK_NAMES.items()):
            p = os.path.join(DST, n + '.webp')
            sz = os.path.getsize(p) if os.path.exists(p) else 0
            print('  %d  %-9s %s' % (r, n, ('%d bytes' % sz) if sz else '缺'))
        return 0

    #: ⛔ 2026-10-15 弃用：图标已改用游戏本体解包原版，禁止再用本脚本覆盖。
    print('⛔ 本脚本已弃用 —— 段位/模式图标改用**游戏本体解包原版**，')
    print('   跑下载会把 data/rank_icons/ 覆盖回旧来源（untapped）。')
    print('   现状请看 `--list`；要重导素材走解包流程（见 docs/素材解包说明.md）。')
    return 2

    icons, blob = discover()
    print('chunk 里解析到段位图标: %s' % ', '.join(sorted(icons)))
    check_mapping(blob)

    print()
    got = miss = 0
    for num, name in sorted(ranks.RANK_NAMES.items()):
        dst = os.path.join(DST, name + '.webp')
        if not args.force and os.path.exists(dst) and os.path.getsize(dst) > 0:
            print('  ✅ %-9s 已存在，跳过' % name)
            got += 1
            continue
        url = icons.get(name)
        if not url:
            print('  ❌ %-9s chunk 里没找到图标' % name)
            miss += 1
            continue
        try:
            data = urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': UA}),
                timeout=40).read()
            open(dst, 'wb').write(data)
            print('  ⬇️  %-9s %d bytes' % (name, len(data)))
            got += 1
        except Exception as exc:
            print('  ❌ %-9s 下载失败: %s' % (name, str(exc)[:60]))
            miss += 1

    print()
    print('完成：%d 个就位，%d 个失败' % (got, miss))
    print('目录：%s' % os.path.relpath(DST, PROJ))
    return 0 if miss == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
