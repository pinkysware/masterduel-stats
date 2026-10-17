# -*- coding: utf-8 -*-
"""backfill_peak_rate —— 把**采集器收局读到的巅峰积分**（内存）补进库。

数据源
------
``data/archive/duel_records/<记录目录>/peak_rate.json`` ——
`tools/duel_collector.py` 在巅峰局收局后写的（等 `DuelResult` 结算对出现）：

```json
{"before": 1507.49, "after": 1515.62, "season": 57,
 "base": {"before": 1500.0, "after": 1507.49, …},   // 开局那拍的上一局对（指纹）
 "waited_sec": 2.0}
```

落点
----
``matches.peak_rate_before`` / ``peak_rate_after`` / ``peak_season`` ——
**只补空、不覆盖**（积分没有推断值，不需要段位那套「真值覆盖」守卫，
2026-09-28 计划审查 B3）。

用法
----
    python tools/backfill_peak_rate.py --list             # 看有哪些可补
    python tools/backfill_peak_rate.py --apply --dry-run  # 只打印准备写什么
    python tools/backfill_peak_rate.py --apply            # 真写（只补 NULL）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from core import paths
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.memread.peak_rate import decode_peak_value, decode_season  # noqa: E402
from core.storage import Storage                                    # noqa: E402

#: 记录目录根（与采集器默认一致）
DEFAULT_RECORDS = paths.data_path('archive', 'duel_records')
#: 采集器写在每个记录目录里的巅峰积分文件名
PROBE_NAME = 'peak_rate.json'


def iter_probes(records_dir):
    """扫记录目录里所有巅峰积分文件 -> ``[{'dir': 名字, 'data': {...}}]``。"""
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


def resolved_pair(data):
    """探测记录 -> ``(before, after, season)``；读不出给 None。**不猜**。

    ⚠️ `peak_rate.json` 里存的已经是**解码后的 before/after**（采集器落盘时
    `decode_peak_pair` 已过一遍），这里只做值域复核，不是原始键名。
    """
    before = decode_peak_value((data or {}).get('before'))
    after = decode_peak_value((data or {}).get('after'))
    if before is None or after is None:
        return None
    return before, after, decode_season((data or {}).get('season'))


def apply_one(storage, records_dir, dir_name):
    """把**某个记录目录**的巅峰积分补进库 -> 短句；没得补给 None。

    采集器收局落盘 `peak_rate.json` 之后**自动调**这一个（与段位的
    `backfill_my_rank.apply_one` 同一编排位置）。库内没有对应对局
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
    got = resolved_pair(data)
    if got is None:
        return None
    before, after, season = got
    row = storage.find_by_record_dir(dir_name)
    if row is None:
        return None                       # 行还没入库：留给手跑 --apply
    if row.get('peak_rate_after') is not None:
        return None                       # 已有值：不覆盖
    acts = storage.backfill_peak_rate(row['match_id'], before, after, season)
    if not acts:
        return None
    return ('巅峰积分入库：%s（%.2f -> %.2f，%s）'
            % (dir_name, before, after,
               '，'.join('%s=%s' % (k, v) for k, v in sorted(acts.items()))))


def run(records_dir, storage, apply=False, dry_run=False):
    """核心逻辑（可单测）—— 返回统计 dict。"""
    stat = {'total': 0, 'ready': 0, 'skipped_has_value': 0, 'unmatched': 0,
            'unreadable': 0, 'detail': []}
    for p in iter_probes(records_dir):
        stat['total'] += 1
        name = p['dir']
        got = resolved_pair(p.get('data'))
        if got is None:
            stat['unreadable'] += 1
            stat['detail'].append((name, '—', '—', '读不出积分对'))
            continue
        before, after, season = got
        want = '%.2f->%.2f' % (before, after)
        row = storage.find_by_record_dir(name)
        if row is None:
            stat['unmatched'] += 1
            stat['detail'].append((name, '—', want, '库内没有对应对局（import 还没跑？）'))
            continue
        old = row.get('peak_rate_after')
        if old is not None:
            stat['skipped_has_value'] += 1
            stat['detail'].append((name, '%.2f' % old, want, '库里已有值，不覆盖'))
            continue
        stat['ready'] += 1
        if apply and not dry_run:
            acts = storage.backfill_peak_rate(row['match_id'], before,
                                              after, season)
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
        print('没有巅峰积分探测文件（%s）' % args.records_dir)
        return 0
    print('%-26s %-10s %-14s %s' % ('记录目录', '库里现值', '可补值', '说明'))
    for name, old, want, note in stat['detail']:
        print('%-26s %-10s %-14s %s' % (name, old, want, note))
    print('\n共 %d 个：可补 %d，已有值 %d，无对应对局 %d，读不出 %d'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['unreadable']))
    return 0


def cmd_apply(args):
    stat = run(args.records_dir, Storage(args.db),
               apply=args.apply, dry_run=args.dry_run)
    if not stat['total']:
        print('没有巅峰积分探测文件（%s）' % args.records_dir)
        return 0
    for name, old, want, note in stat['detail']:
        print('  %-26s %s -> %s  %s' % (name, old, want, note))
    print('\n共 %d 个：写入 %d，已有值 %d，无对应对局 %d，读不出 %d%s'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['unreadable'],
             '（--dry-run，没真写）' if args.dry_run else ''))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='把巅峰积分探测补进库（只补空）')
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
