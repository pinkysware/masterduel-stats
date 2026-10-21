# -*- coding: utf-8 -*-
"""从**识别缓存**批量建自定义参照库（带标签一致性校验）。

## 和 `build_custom_ref.py` 的分工

| 工具 | 输入 | 用在哪 |
|---|---|---|
| `build_custom_ref.py` | `data/deck_images_custom/<名字>/` 里的图 | **日常**：用户放几张截图 → 建一个库 |
| 本工具 | 识别结果 JSON（`{楼层: {deck_main, imgs:[{ids:[...]}]}}`） | **批量**：从贴吧帖子一次性铺多个卡组 |

两者共用 `build_custom_ref.aggregate()` / `gate_ok()` / 闸门常量 —— **口径只有一份**。

## ★ 标签一致性校验（本工具的核心）

贴吧楼层有**多值标签**（`码丽丝|绚岚`、`绚岚|闪刀姬` 等），那是混轴构筑：
同一张图既归「码丽丝」也归「绚岚」。直接把图按标签塞进各个库会互相污染 ——
实测「绚岚」库 82 张卡里有 60 张是**闪刀姬**的卡（那层标签是 `绚岚|闪刀姬`）。

### 判据是**相对**的，不是绝对的

一开始试过「与站内同名卡表重合 >= 0.35 就收」，**不成立**：小轴混轴里
小轴那一侧照样过线 —— 实测 `码丽丝|绚岚` 那层的图，
与 Maliss 重合 0.86、与 Radiant Typhoon 重合 **0.41**，两条都过 0.35，
于是同一张图被塞进两个库。

真正的规律是**同一层内多个标签之间有明显落差**：

| 楼层 | 标签 | 各标签的实测重合率 |
|---|---|---|
| F67 | 码丽丝 / 绚岚 | Maliss **0.86** vs Radiant Typhoon 0.41 |
| F95 | 码丽丝 / 绚岚 | Maliss **1.00** vs Radiant Typhoon 0.24 |
| F101 | 绚岚 / 闪刀姬 | Sky Striker **1.00** vs Radiant Typhoon 0.31 |
| F61 | 耀圣 / 黯蜜 | Yummy **0.97** vs Elfnote 0.92（**真·两边都像**）|

所以：**只把图归给同层里重合率最高的那个标签**，并且要求
``best >= 第二名 + MARGIN``（默认 0.15）；差距不够（F61 那种真混轴）
就**整层都不收** —— 那副牌确实两边都是，塞进哪个库都是污染。

站内没有对应卡表的标签（自定义卡组名）**不参与竞争**，也不受影响。
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from tools.build_custom_ref import (aggregate, gate_ok, load_custom,   # noqa: E402
                                    save_custom, CUSTOM_REF)

#: 与标签对应卡表的最低重合率（见模块头）
MIN_LABEL_OVERLAP = 0.35
#: **领先幅度**：最高的那个标签必须比第二名高这么多，否则算「真混轴」整层不收。
#: 实测标定：F67 落差 0.45 / F95 0.76 / F101 0.69（都该收）、
#:          F61 落差 0.05（真混轴，不该收）。0.15 落在两者之间。
LABEL_MARGIN = 0.15


def _group_sets(reference, groups):
    """站内参照库 → ``{卡组名: 名字组集合}``。"""
    from core import deck_guess as dg
    out = {}
    for name, d in (reference.get('decks') or {}).items():
        cards = (d or {}).get('cards') or {}
        if not cards:
            continue
        out[name] = {dg.group_key(c, groups) for c in cards}
    return out


def _best_target(name, ref_sets, alias_of=None):
    """标签名 → 站内参照库里最可能对应的那几个卡组名（用于校验）。"""
    if alias_of and name in alias_of:
        return [alias_of[name]] if alias_of[name] in ref_sets else []
    return [name] if name in ref_sets else []


def label_overlap(ids, ref_sets, groups, name, alias_of=None, storage=None):
    """识别出的卡 → 与「标签名下卡表」的重合率；无从比较时返回 ``None``。

    先按标签名精确找站内卡表；找不到就走 `storage.search_meta_decks` 的
    别名/中文名映射（和 `deck_composition` 同一套口径）。
    """
    from core import deck_guess as dg
    mine = {dg.group_key(c, groups) for c in ids}
    if not mine:
        return None
    targets = _best_target(name, ref_sets, alias_of)
    if not targets and storage is not None:
        try:
            hits = storage.search_meta_decks(name, limit=3) or []
        except Exception:                                    # noqa: BLE001
            hits = []
        targets = [h.get('name') for h in hits if h.get('name') in ref_sets]
    if not targets:
        return None                      # 站内没有 -> 不比
    best = 0.0
    for t in targets:
        theirs = ref_sets.get(t) or set()
        if theirs:
            best = max(best, len(mine & theirs) / float(len(mine)))
    return best


def build_from_cache(cache, reference=None, storage=None, name_filter=None,
                     min_overlap=MIN_LABEL_OVERLAP, verbose=True):
    """识别缓存 → ``{卡组名: 条目}``。返回 ``(built, skipped_report)``。"""
    from core import deck_guess as dg
    groups = dg.build_name_groups()
    if reference is None:
        reference = dg.load_reference()
    ref_sets = _group_sets(reference, groups)

    per_deck = collections.defaultdict(list)
    reasons = collections.Counter()
    for fl, v in (cache or {}).items():
        raw = str(v.get('deck_main') or '').strip()
        if not raw:
            continue
        names = [x.strip() for x in raw.split('|') if x.strip()]
        for i, im in enumerate(v.get('imgs') or []):
            if not isinstance(im, dict) or 'err' in im:
                continue
            ids = im.get('ids') or []
            if not ids:
                continue
            u = len(set(ids))
            ratio = len(ids) / u if u else 0.0
            ok, why = gate_ok({'n_ids': len(ids), 'dup_ratio': ratio})
            if not ok:
                reasons[why.split(' ')[0] + ' ' + why.split(' ')[1] if ' ' in why else why] += 1
                continue
            #: ★ **同层标签相对竞争**（见模块头）：每张图只归一个标签。
            scored = []
            for name in names:
                ov = label_overlap(ids, ref_sets, groups, name, storage=storage)
                scored.append((name, ov))
            comparable = [(n, o) for n, o in scored if o is not None]
            if len(comparable) > 1:
                comparable.sort(key=lambda p: -p[1])
                best_name, best = comparable[0]
                second = comparable[1][1]
                if best < min_overlap:
                    reasons['标签对不上：%s' % best_name] += 1
                    continue
                if best - second < LABEL_MARGIN:
                    reasons['真混轴（两标签几乎一样像）'] += 1
                    continue
                winners = [best_name]
            elif len(comparable) == 1:
                only_name, only = comparable[0]
                if only < min_overlap:
                    reasons['标签对不上：%s' % only_name] += 1
                    continue
                winners = [only_name]
            else:
                #: 全都比不了（站内没有这些卡表 = 都是自定义名）—— 按原标签收
                winners = list(names)
            for name in winners:
                if name_filter and name != name_filter:
                    continue
                per_deck[name].append((fl, i, ids))

    built = {}
    for name, shots in per_deck.items():
        deck = aggregate([ids for _f, _i, ids in shots], name=name)
        if deck:
            built[name] = deck
    return built, reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description='识别缓存 → 自定义参照库（带标签校验）')
    ap.add_argument('cache', help='识别结果 JSON（{楼层: {deck_main, imgs:[{ids}]}}）')
    ap.add_argument('--deck', help='只建这一个卡组名')
    ap.add_argument('--min-overlap', type=float, default=MIN_LABEL_OVERLAP,
                    help='标签一致性下限（默认 %.2f）' % MIN_LABEL_OVERLAP)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--replace', action='store_true',
                    help='用本次结果**替换**整个自定义库（默认是合并）')
    args = ap.parse_args(argv)

    with io.open(args.cache, encoding='utf-8') as f:
        cache = json.load(f)
    from core.storage import Storage
    built, reasons = build_from_cache(cache, storage=Storage(),
                                      name_filter=args.deck,
                                      min_overlap=args.min_overlap)
    if not built:
        print('没有可建库的卡组')
        for k, n in reasons.most_common(10):
            print('  %-30s %d 张图' % (k, n))
        return 1

    print('%-16s %5s %7s %s' % ('卡组名', '样本', '卡数', '口径'))
    print('-' * 48)
    for name in sorted(built, key=lambda n: -built[n]['samples']):
        d = built[name]
        print('%-16s %5d %7d %s' % (name, d['samples'], len(d['cards']),
                                    'provisional' if d['provisional'] else 'per=真实%'))
    if reasons:
        print('\n被丢弃的图：')
        for k, n in reasons.most_common(10):
            print('  %-32s %d 张' % (k, n))

    if args.dry_run:
        print('\n[dry-run] 没落盘')
        return 0
    decks = {} if args.replace else load_custom()
    decks.update(built)
    save_custom(decks)
    print('\n已写入 %d 条 -> %s' % (len(decks), CUSTOM_REF))
    return 0


if __name__ == '__main__':
    sys.exit(main())
