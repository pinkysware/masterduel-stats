# -*- coding: utf-8 -*-
"""下载模式图标（天梯 / 巅峰决斗 / DC 杯 / 活动）—— **手动跑，幂等**。

用法::

    python tools/fetch_mode_icons.py            # 缺哪个补哪个
    python tools/fetch_mode_icons.py --force    # 全部重下
    python tools/fetch_mode_icons.py --list     # 只看现状，不联网

## 图标从哪来

masterduelmeta 用的 CDN：

    https://s3.duellinksmeta.com/mdm_img/content/logos/<名>.webp

页面上是经 `wsrv.nl` 代理的（`?url=<真源>&w=150&output=webp`），
**代理只是缩图，取 `url=` 后面的真源即可**，别去抓代理。

## ⚠️ 为什么是「名字表」而不是「猜」

CDN 上的文件名是**手工传的**，没有规律可循 ——
已确认的两个是 `ratingfinalicon.webp` 和 `rateIcon.webp`，
但同目录下试了 60 个「词根 × 后缀」组合**全部 403**，所以天梯那个推不出来。

→ 表里填的是**已确认**的名字；**要加新的，从浏览器网络面板里拿真源 URL 再填进来**。
（`--list` 会告诉你还缺哪个。）

## ⚠️ 边界（AGENTS.md 铁律 4）

**只取静态素材，不取战绩数据。**
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import modes                                                # noqa: E402
from core.storage import MODE_GROUP_NAMES                             # noqa: E402

BASE = 'https://s3.duellinksmeta.com/mdm_img/content/logos/'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

#: 分类 -> CDN 上的文件名（**已确认的才填**）。
#: ⚠️ `ranked` 还空着：CDN 上的名字猜不出来（试了 60 个组合全 403），
#:    需要从浏览器网络面板拿真源 URL 再补。
ICON_SOURCE = {
    'peak': 'ratingfinalicon',      # 巅峰决斗（用户 2026-09-14 给出）
    # 'ranked': '???',              # 天梯 —— 待补
    # 'dc': '???',                  # DC 杯 —— 待补
}


def fetch(name: str, timeout: int = 30):
    req = urllib.request.Request(BASE + name + '.webp',
                                 headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
    except Exception as e:
        return None, str(e)[:60]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='下载模式图标（只取素材，不取战绩）')
    ap.add_argument('--force', action='store_true', help='全部重下')
    ap.add_argument('--list', action='store_true', help='只看现状，不联网')
    args = ap.parse_args(argv)

    os.makedirs(modes.ICON_DIR, exist_ok=True)

    if args.list:
        print('模式图标（%s）：' % os.path.relpath(modes.ICON_DIR, PROJ))
        for key in sorted(MODE_GROUP_NAMES):
            p = modes.icon_path(key)
            src = ICON_SOURCE.get(key, '—')
            if p:
                mark, note = '✅', ''
            elif key == 'ranked':
                mark, note = '➖', '（天梯用段位图标，不需要模式图标）'
            elif key in ('dc', 'event'):
                mark, note = '➖', '（用户口径：不需要图标）'
            else:
                mark, note = '❌', '（需要但没有 —— CDN 名字未知，得从浏览器网络面板取）'
            print('  %s %-8s %-14s 源=%-20s %s %s'
                  % (mark, key, MODE_GROUP_NAMES[key], src,
                     ('%d B' % os.path.getsize(os.path.join(PROJ, p))) if p else '缺',
                     note))
        return 0

    got = miss = 0
    for key in sorted(MODE_GROUP_NAMES):
        dst = os.path.join(modes.ICON_DIR, key + '.webp')
        if not args.force and os.path.exists(dst) and os.path.getsize(dst) > 0:
            print('  ✅ %-8s 已存在，跳过' % key)
            got += 1
            continue
        src = ICON_SOURCE.get(key)
        if not src:
            print('  ❔ %-8s 还没有 CDN 文件名 —— 从浏览器网络面板取一个再填进表里' % key)
            miss += 1
            continue
        status, data = fetch(src)
        if status == 200 and len(data) > 300:
            open(dst, 'wb').write(data)
            print('  ⬇️  %-8s <- %-22s %d B' % (key, src + '.webp', len(data)))
            got += 1
        else:
            print('  ❌ %-8s 下载失败 HTTP %s' % (key, status))
            miss += 1

    print()
    print('完成：%d 个就位，%d 个待补' % (got, miss))
    if modes.missing_icons():
        print('还缺（**只有巅峰决斗需要图标**）：%s' % ', '.join(modes.missing_icons()))
    else:
        print('✅ 需要的模式图标都齐了（只有巅峰决斗需要）')
    return 0 if miss == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
