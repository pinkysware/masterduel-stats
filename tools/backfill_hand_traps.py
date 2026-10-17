# -*- coding: utf-8 -*-
"""backfill_hand_traps —— 给存量的 record 局补「手坑两列」（2026-10-16）。

🔴 为什么必须单独一个工具（审查 P1-3，实测过）：
`core/duel_record.write_duel_json` 的幂等捷径 —— 「指纹没变且 meta 无新增」直接
`return 'skipped'` —— 而手坑新键在 `summary` 里、**不进** `_merge_meta` 的判断，
所以存量 `duel.json` 重跑 `import_duel_records.py` **一律 skipped**、
永远不会补上 `summary.hand_traps`。本工具**直接读改写 `duel.json`**。

做两件事（都幂等）：
  1. 把 `summary.hand_traps` 塞进每个 record 目录的 `duel.json`
     （**只改 summary 这一个键**，其余原样写回 —— 不重刷别的派生值）；
  2. 按 `record_dir` 把两列**结论**写进 `matches`（**只写判得出的**：
     seat 或回合缺一就整列为 NULL，那行不动）。

⚠️ **不动 `matches` 的其它列** —— 本工具只负责手坑两列；胜负/时间等由
`import_duel_records.py` 负责。同局若还没入库，这里只补 duel.json，
下次 import 会带上。

用法
----
    python tools/backfill_hand_traps.py              # 只看计划（默认 dry-run）
    python tools/backfill_hand_traps.py --apply      # 真写 duel.json + 库
    python tools/backfill_hand_traps.py --records-dir X --db Y
    python tools/backfill_hand_traps.py --record record_XXX --apply   # 单目录
"""

import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import duel_record
from core.storage import Storage

DEFAULT_RECORDS = os.path.join(PROJ, 'data', 'archive', 'duel_records')


def _dump_duel_json(record_dir, data):
    """原样写回 duel.json（同 `duel_record._dump_json` 格式；LF 行尾）。"""
    path = os.path.join(record_dir, duel_record.DUEL_JSON_NAME)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text + '\n')


def backfill_one(record_dir, apply=False):
    """补一个 record 目录的 duel.json。

    返回 ``(状态, mine, opp)``（mine/opp 是**结论列表**或 None）。

    状态：
      * ``'no-json'``   还没 duel.json（先跑 import_duel_records.py）；
      * ``'no-events'`` duel.json 没有 events（老格式/损坏）；
      * ``'up-to-date'``已有该键（幂等跳过，仍返回两侧结论供写库）；
      * ``'planned'``   dry-run；
      * ``'written'``   已写盘。
    """
    old = duel_record.read_duel_json(record_dir)
    if old is None:
        return 'no-json', None, None
    events = old.get('events')
    summary = old.get('summary')
    if not isinstance(events, list) or not events \
            or not isinstance(summary, dict):
        return 'no-events', None, None
    ht = duel_record.summarize_hand_traps(events)
    seat = (old.get('meta') or {}).get('seat')
    mine, opp = duel_record.hand_trap_sides(ht, seat)
    if summary.get('hand_traps') == ht:
        return 'up-to-date', mine, opp
    if not apply:
        return 'planned', mine, opp
    summary['hand_traps'] = ht
    _dump_duel_json(record_dir, old)
    return 'written', mine, opp


def run(records_dir=DEFAULT_RECORDS, storage=None, apply=False, only=None):
    """扫全部 record 目录：补 duel.json + 写库两列。返回报告 dict。"""
    if storage is None:
        storage = Storage()
    storage.init_schema()                   # 老库 ALTER 补两列
    report = {'dirs': [], 'written_json': 0, 'rows_updated': 0,
              'n_dirs': 0, 'skipped': 0}
    if not os.path.isdir(records_dir):
        return report
    for name in sorted(os.listdir(records_dir)):
        path = os.path.join(records_dir, name)
        if not os.path.isdir(path) or not name.startswith('record_'):
            continue
        if only is not None and name != only:
            continue
        report['n_dirs'] += 1
        status, mine, opp = backfill_one(path, apply=apply)
        item = {'record_dir': name, 'json': status,
                'counts': {'my': None if mine is None else len(mine),
                           'opp': None if opp is None else len(opp)}}
        if status == 'written':
            report['written_json'] += 1
        if status in ('no-json', 'no-events'):
            report['skipped'] += 1
        # 写库（只在 apply；两列至少一个判得出）
        if apply and (mine is not None or opp is not None):
            n = storage.set_hand_traps_by_record(name, mine, opp)
            item['rows'] = n
            report['rows_updated'] += n
        report['dirs'].append(item)
    return report


def _print_report(report):
    for it in report['dirs']:
        c = it.get('counts') or {}
        extra = ''
        if c.get('my') is not None or c.get('opp') is not None:
            extra = '  手坑：我 %s / 敌 %s' % (
                '—' if c.get('my') is None else c['my'],
                '—' if c.get('opp') is None else c['opp'])
        if 'rows' in it:
            extra += '（写库 %d 行）' % it['rows']
        print('· %-28s [%s]%s' % (it['record_dir'], it['json'], extra))
    print('小结：扫描 %d 目录 / duel.json 写 %d / 库更新 %d 行 / 跳过 %d'
          % (report['n_dirs'], report['written_json'],
             report['rows_updated'], report['skipped']))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true',
                    help='真写 duel.json + 库（默认 dry-run 只打印）')
    ap.add_argument('--record', metavar='DIR_NAME', help='只处理一个目录')
    ap.add_argument('--records-dir', default=DEFAULT_RECORDS)
    ap.add_argument('--db', default=None, help='SQLite 路径（默认 data/masterduel.db）')
    args = ap.parse_args(argv)
    storage = Storage(args.db) if args.db else Storage()
    report = run(records_dir=args.records_dir, storage=storage,
                 apply=args.apply, only=args.record)
    _print_report(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
