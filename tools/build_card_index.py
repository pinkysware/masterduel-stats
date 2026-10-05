# -*- coding: utf-8 -*-
"""从 ygocdb 原始卡表生成**精简卡表** `data/card_index.json`（随发布仓库附带）。

【为什么需要】（2026-10-05 发布审核）

`core/card_names.card_type()`（判主卡 / 额外卡组）和 `core/card_art`
（游戏 id → Konami 密码、英文名 → Konami 密码，禁限表用）都依赖 ygocdb 原始卡表
`ygocdb.com.cards/cards.json`（13MB，`.gitignore` 排除）。

发布仓库不带这个包 → 干净克隆上 `card_type()` 全返回 None →
`core/ui_state.deck_composition()` 把每一张卡都算成 `unknown` 丢掉 →
**「卡组构成」/「参照库」页整页空白**（实测 `unknown=17/17`）。

这里只抽**运行时真正要的三个字段**，体积缩到约 1/15，随仓库发布；
`core.card_names.ygocdb()` 在完整包不在时自动回退读它。

【用法】::

    python tools/build_card_index.py            # 读默认 ygocdb 包，写 data/card_index.json
    python tools/build_card_index.py --check    # 只比对，不写盘
    python tools/build_card_index.py --src <cards.json> --out <card_index.json>

【上游更新时】ygocdb 卡表刷新后（见 `tools/build_card_names.py --update`），
重跑本脚本即可；它是幂等的。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.card_names import CARD_INDEX_PATH, YGOCDB_PATH  # noqa: E402

#: 精简卡表保留的顶层字段（运行时读这些：见 card_art._load / card_names.card_type）
KEEP_TOP = ('id', 'en_name')


def build(src: str = YGOCDB_PATH) -> dict:
    """读原始卡表 → 精简结构 ``{cid(str): {id?, en_name?, data:{type}?}}``。"""
    with open(src, encoding='utf-8') as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError('原始卡表顶层不是 dict：%s' % src)

    out = {}
    for cid, card in raw.items():
        if not isinstance(card, dict):
            continue
        entry = {k: card[k] for k in KEEP_TOP if card.get(k) not in (None, '')}
        data = card.get('data')
        ctype = data.get('type') if isinstance(data, dict) else None
        if ctype is not None:
            entry['data'] = {'type': ctype}
        out[str(cid)] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description='从 ygocdb 原始卡表生成精简卡表 data/card_index.json')
    ap.add_argument('--src', default=YGOCDB_PATH, help='原始卡表（默认 %(default)s）')
    ap.add_argument('--out', default=CARD_INDEX_PATH, help='输出路径（默认 %(default)s）')
    ap.add_argument('--check', action='store_true', help='只比对现有文件，不写盘')
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print('✘ 原始卡表不存在：%s' % args.src)
        print('  先拿到 ygocdb 快照（见 tools/build_card_names.py 顶部说明）再跑本脚本。')
        return 2

    index = build(args.src)
    payload = json.dumps(index, ensure_ascii=False, separators=(',', ':'))

    if args.check:
        if not os.path.exists(args.out):
            print('✘ 精简卡表不存在：%s' % args.out)
            return 1
        with open(args.out, encoding='utf-8') as f:
            cur = f.read()
        same = cur == payload
        print('%s 现有文件与重建结果%s' % ('✔' if same else '✘', '一致' if same else '不一致'))
        return 0 if same else 1

    with open(args.out, 'w', encoding='utf-8', newline='') as f:
        f.write(payload)
    size_kb = os.path.getsize(args.out) / 1024.0
    print('✔ 写出 %s（%d 张卡，%.0f KB）' % (args.out, len(index), size_kb))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
