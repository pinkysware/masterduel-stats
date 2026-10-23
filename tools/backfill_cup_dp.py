# -*- coding: utf-8 -*-
"""backfill_cup_dp —— 把**采集器收局读到的 DC 杯 DP**（内存）补进库。

数据源
------
``data/archive/duel_records/<记录目录>/cup_dp.json`` ——
`tools/duel_collector.py` 在 **DC 杯 2 阶段**收局后写的（等 `DuelResult` 结算对出现）：

```json
{"before": 0, "after": 1000, "win": 1, "stage": 2,
 "base": {"before": 0, "after": 0, …},   // 开局那拍的上一局对（指纹）
 "waited_sec": 1.0}
```

落点
----
``matches.cup_dp_before`` / ``cup_dp_after`` / ``cup_stage`` ——
**只补空、不覆盖**（DP 没有推断值，不需要段位那套「真值覆盖」守卫，
口径同 `tools/backfill_peak_rate.py` 的 B3）。

⚠️ **1 阶段的局不写**：那时打的是 DLv（段位），不是 DP。采集器已在源头
拦掉（`_start_cup_probe` 检查 stage），这里再守一遍（`stage != 2` 直接跳过）。

用法
----
    python tools/backfill_cup_dp.py --list             # 看有哪些可补
    python tools/backfill_cup_dp.py --apply --dry-run  # 只打印准备写什么
    python tools/backfill_cup_dp.py --apply            # 真写（只补 NULL）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import paths

from core.memread.cup_dp import decode_dp, decode_stage       # noqa: E402
from core.storage import Storage                               # noqa: E402

#: 记录目录根（与采集器默认一致）
DEFAULT_RECORDS = paths.data_path('archive', 'duel_records')
#: 采集器写在每个记录目录里的 DC 杯 DP 文件名
PROBE_NAME = 'cup_dp.json'


def iter_probes(records_dir):
    """扫记录目录里所有 DP 文件 -> ``[{'dir': 名字, 'data': {...}}]``。"""
    out = []
    if not os.path.isdir(records_dir):
        return out
    for name in sorted(os.listdir(records_dir)):
        path = os.path.join(records_dir, name, PROBE_NAME)
        if not os.path.isfile(path):
            continue
        item = {'dir': name}
        try:
            with open(path, encoding='utf-8') as f:
                item['data'] = json.load(f)
        except Exception as e:
            item['error'] = repr(e)
        out.append(item)
    return out


def resolved_triple(data):
    """探测记录 -> ``(before, after, stage)``；读不出给 None。**不猜**。

    ⚠️ `cup_dp.json` 里存的已经是**解码后的值**（采集器落盘时
    `decode_dp_pair` 已过一遍），这里只做值域复核，不是原始键名。
    阶段不是 2 的一律返回 None —— 1 阶段的 DLv 不是 DP。
    """
    stage = decode_stage((data or {}).get('stage'))
    if stage != 2:
        return None
    before = decode_dp((data or {}).get('before'))
    after = decode_dp((data or {}).get('after'))
    if before is None or after is None:
        return None
    return before, after, stage


def apply_one(storage, records_dir, dir_name):
    """把**某个记录目录**的 DP 补进库 -> 短句；没得补给 None。

    采集器收局落盘 `cup_dp.json` 之后**自动调**这一个（与
    `backfill_peak_rate.apply_one` 同一编排位置）。库内没有对应对局
    （import 还没跑 / 失败了）**返回 None 静默** —— 绝不阻塞采集，
    后手还能手跑 ``--apply`` 补。
    """
    path = os.path.join(records_dir, dir_name, PROBE_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    got = resolved_triple(data)
    if got is None:
        return None
    before, after, stage = got
    row = storage.find_by_record_dir(dir_name)
    if row is None:
        return None                       # 行还没入库：留给手跑 --apply
    if row.get('cup_dp_after') is not None:
        return None                       # 已有值：不覆盖
    acts = storage.backfill_cup_dp(row['match_id'], before, after, stage)
    if not acts:
        return None
    return ('DC杯积分入库：%s（%d -> %d，%s阶段；%s）'
            % (dir_name, before, after, stage,
               '，'.join('%s=%s' % (k, v) for k, v in sorted(acts.items()))))


def run(records_dir, storage, apply=False, dry_run=False):
    """核心逻辑（可单测）—— 返回统计 dict。"""
    stat = {'total': 0, 'ready': 0, 'skipped_has_value': 0, 'unmatched': 0,
            'unreadable': 0, 'not_stage2': 0, 'detail': []}
    for p in iter_probes(records_dir):
        stat['total'] += 1
        name = p['dir']
        data = p.get('data')
        raw_stage = decode_stage((data or {}).get('stage'))
        got = resolved_triple(data)
        if got is None:
            if raw_stage is not None and raw_stage != 2:
                stat['not_stage2'] += 1
                stat['detail'].append((name, '—', '—',
                                       '%s阶段不是 DP，跳过' % raw_stage))
            else:
                stat['unreadable'] += 1
                stat['detail'].append((name, '—', '—', '读不出 DP 对'))
            continue
        before, after, stage = got
        want = '%d->%d' % (before, after)
        row = storage.find_by_record_dir(name)
        if row is None:
            stat['unmatched'] += 1
            stat['detail'].append((name, '—', want,
                                   '库内没有对应对局（import 还没跑？）'))
            continue
        old = row.get('cup_dp_after')
        if old is not None:
            stat['skipped_has_value'] += 1
            stat['detail'].append((name, '%d' % old, want, '库里已有值，不覆盖'))
            continue
        stat['ready'] += 1
        if apply and not dry_run:
            acts = storage.backfill_cup_dp(row['match_id'], before,
                                           after, stage)
            note = ','.join('%s=%s' % (k, v) for k, v in sorted(acts.items()))
            stat['detail'].append((name, '—', want, note or '无改动'))
        elif dry_run:
            stat['detail'].append((name, '—', want, '将写入（dry-run）'))
        else:
            stat['detail'].append((name, '—', want, '未写（要加 --apply）'))
    return stat


def cmd_list(args):
    stat = run(args.records_dir, Storage(args.db))
    if not stat['total']:
        print('没有 DC 杯 DP 探测文件（%s）' % args.records_dir)
        return 0
    print('%-26s %-10s %-14s %s' % ('记录目录', '库里现值', '可补值', '说明'))
    for name, old, want, note in stat['detail']:
        print('%-26s %-10s %-14s %s' % (name, old, want, note))
    print('\n共 %d 个：可补 %d，已有值 %d，无对应对局 %d，非2阶段 %d，读不出 %d'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['not_stage2'], stat['unreadable']))
    return 0


def cmd_apply(args):
    stat = run(args.records_dir, Storage(args.db),
               apply=args.apply, dry_run=args.dry_run)
    if not stat['total']:
        print('没有 DC 杯 DP 探测文件（%s）' % args.records_dir)
        return 0
    for name, old, want, note in stat['detail']:
        print('  %-26s %s -> %s  %s' % (name, old, want, note))
    print('\n共 %d 个：写入 %d，已有值 %d，无对应对局 %d，非2阶段 %d，读不出 %d%s'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['not_stage2'], stat['unreadable'],
             '（--dry-run，没真写）' if args.dry_run else ''))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='把 DC 杯 DP 探测补进库（只补空）')
    ap.add_argument('--list', action='store_true', help='只看有哪些可补')
    ap.add_argument('--apply', action='store_true', help='真写库（默认只打印）')
    ap.add_argument('--dry-run', action='store_true', help='配合 --apply：只打印不写')
    ap.add_argument('--records-dir', default=DEFAULT_RECORDS,
                    help='记录目录根（默认 data/archive/duel_records）')
    ap.add_argument('--db', default=None, help='数据库路径（默认 data/masterduel.db）')
    args = ap.parse_args()
    if args.list:
        return cmd_list(args)
    return cmd_apply(args)


if __name__ == '__main__':
    sys.exit(main())
