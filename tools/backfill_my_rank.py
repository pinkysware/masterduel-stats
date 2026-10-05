# -*- coding: utf-8 -*-
"""backfill_my_rank —— 把**采集器收局读到的段位**（内存）补进库。

数据源
------
``data/archive/duel_records/<记录目录>/my_rank_after.json`` ——
`tools/duel_collector.py` 在 `pvpFinished` 收局时按 **§5.7 两拍法** 写下的：

```json
{"base": {"rank": 5, "rate": 2, "exp": 28900, …},     // 收局当场那一次的读数
 "after": {"rank": 5, "rate": 1, "exp": 29000, …},     // exp 变化后（= 局后值）
 "settled_at": 1789…, "resolved_by": "exp-change", "reads": 3}
```

落点
----
``matches.my_rank_after`` / ``my_rate_after`` —— **只补空，不覆盖**
（口径同 `apply_record_meta`）。这是「段位不再依赖 Untapped API」的入库口：
库里那两个字段以前**只**由 `tools/fetch_untapped_api.py` 写。

为什么要单独一步、不塞在采集器里
--------------------------------
值要到「结算写进 ClientWork 之后」才准 → 收局那一刻还不知道，所以采集器只落盘、
由这个工具事后补写（幂等：多跑几次无副作用，`import` 迟到的行下次也能补上）。

用法
----
    python tools/backfill_my_rank.py --list                  # 看有哪些可补
    python tools/backfill_my_rank.py --apply --dry-run       # 只打印准备写什么
    python tools/backfill_my_rank.py --apply                 # 真写（只补 NULL）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.storage import Storage  # noqa: E402

#: 记录目录根（与采集器默认一致）
DEFAULT_RECORDS = os.path.join(PROJ, 'data', 'archive', 'duel_records')
#: 采集器写在每个记录目录里的段位文件名
PROBE_NAME = 'my_rank_after.json'


def iter_probes(records_dir):
    """扫记录目录里所有段位探测文件 -> ``[{'dir': 名字, 'data': {...}}]``。"""
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


def resolved_rank(data):
    """探测记录 -> ``(rank, rate, rec)``；读不出给 None。

    取 ``after``（局后值）；``after`` 读不到就退回 ``base``
    （= 这一局段位没变，前后本来就一样）。**不猜**：两者都缺就给 None。
    """
    for key in ('after', 'base'):
        rec = (data or {}).get(key) or {}
        if rec.get('rank') is not None and rec.get('rate') is not None:
            return int(rec['rank']), int(rec['rate']), rec
    return None


def resolved_before(data):
    """探测记录 -> ``(rank, rate)`` 局前值；没有给 None。

    来源 = `Duel.rank`（开局那一拍读的，见 `core/memread/duel_result.py`）。
    老产物（本键加入之前写的）没有它 → None，工具照常只补 after。
    """
    rec = (data or {}).get('before') or {}
    if rec.get('rank') is not None and rec.get('rate') is not None:
        return int(rec['rank']), int(rec['rate'])
    return None


def apply_one(storage, records_dir, dir_name):
    """把**某个记录目录**的段位探测补进库 -> 说明文字；没得补给 ``None``。

    采集器收局落盘 `my_rank_after.json` 之后**自动调**这一个
    （见 `duel_collector._poll_rank_probe`）—— 这样用户不用再手跑
    ``--apply``。判据与 CLI 完全共用下面两个解析助手，**别再抄第二份**。

    返回 ``'段位 5-2 -> 5-1（after=set,before=set）'`` 这样的短句（写进采集器
    日志）；库里没有对应对局（import 还没跑 / 失败了）**返回 None 静默** ——
    采集器的铁律是**绝不因为补写失败而阻塞采集**，后手还能手跑 ``--apply`` 补。
    """
    path = os.path.join(records_dir, dir_name, PROBE_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    got = resolved_rank(data)
    if got is None:
        return None
    rank, rate, _rec = got
    before = resolved_before(data)
    row = storage.find_by_record_dir(dir_name)
    if row is None:
        return None                       # 行还没入库：留给手跑 --apply
    old, old_before = row.get('my_rank_after'), row.get('my_rank_before')
    if old is not None and (before is None or old_before is not None):
        return None                       # 两边都有值：不用动（也不覆盖）
    acts = []
    if old is None:
        got_acts = storage.backfill_my_rank_after(row['match_id'], rank, rate)
        acts.append('局后 ' + ('%d-%d' % (rank, rate) if got_acts else '无改动'))
    if before is not None and old_before is None:
        got_acts = storage.backfill_my_rank_before(row['match_id'],
                                                   before[0], before[1])
        acts.append('局前 ' + ('%d-%d' % before if got_acts else '无改动'))
    if not acts:
        return None
    return '段位入库：%s（%s）' % (dir_name, '，'.join(acts))


def run(records_dir, storage, apply=False, dry_run=False):
    """核心逻辑（可单测）—— 返回统计 dict。"""
    stat = {'total': 0, 'ready': 0, 'skipped_has_value': 0, 'unmatched': 0,
            'unreadable': 0, 'detail': []}
    for p in iter_probes(records_dir):
        stat['total'] += 1
        name = p['dir']
        got = resolved_rank(p.get('data'))
        if got is None:
            stat['unreadable'] += 1
            stat['detail'].append(
                (name, '—', '—', '读不出段位（%s）' % (p.get('error') or '没有值')))
            continue
        rank, rate, _rec = got
        before = resolved_before(p.get('data'))
        want = '%d-%d' % (rank, rate)
        row = storage.find_by_record_dir(name)
        if row is None:
            stat['unmatched'] += 1
            stat['detail'].append((name, '—', want, '库内没有对应对局（import 还没跑？）'))
            continue
        old = row.get('my_rank_after')
        old_before = row.get('my_rank_before')
        if old is not None and (before is None or old_before is not None):
            stat['skipped_has_value'] += 1
            stat['detail'].append(
                (name, '%s-%s' % (old, row.get('my_rate_after')), want,
                 '库里已有值，不覆盖'))
            continue
        stat['ready'] += 1
        notes = []
        if apply and not dry_run:
            if old is None:
                acts = storage.backfill_my_rank_after(row['match_id'], rank, rate)
                notes.append(','.join('%s=%s' % (k, v)
                                      for k, v in sorted(acts.items())) or '无改动')
            if before is not None and old_before is None:
                acts = storage.backfill_my_rank_before(row['match_id'],
                                                       before[0], before[1])
                notes.append('局前 ' + (','.join(
                    '%s=%s' % (k, v) for k, v in sorted(acts.items())) or '无改动'))
        elif dry_run:
            notes.append('将写入（dry-run）')
        else:
            notes.append('未写（要加 --apply）')
        stat['detail'].append((name, '—', want, ' / '.join(notes)))
    return stat


def cmd_list(args):
    stat = run(args.records_dir, Storage(args.db))
    if not stat['total']:
        print('没有段位探测文件（%s）' % args.records_dir)
        return 0
    print('%-26s %-8s %-8s %s' % ('记录目录', '库里现值', '可补值', '说明'))
    for name, old, want, note in stat['detail']:
        print('%-26s %-8s %-8s %s' % (name, old, want, note))
    print('\n共 %d 个探测文件：可补 %d，库里已有值 %d，无对应对局 %d，读不出 %d'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['unreadable']))
    return 0


def cmd_apply(args):
    stat = run(args.records_dir, Storage(args.db),
               apply=args.apply, dry_run=args.dry_run)
    if not stat['total']:
        print('没有段位探测文件（%s）' % args.records_dir)
        return 0
    for name, old, want, note in stat['detail']:
        print('  %-26s %s -> %s  %s' % (name, old, want, note))
    print('\n共 %d 个探测文件：写入 %d，库里已有值 %d，库内没有对应对局 %d，读不出 %d%s'
          % (stat['total'], stat['ready'], stat['skipped_has_value'],
             stat['unmatched'], stat['unreadable'],
             '（--dry-run，没真写）' if args.dry_run else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='把采集器读到的段位（内存）补进 my_rank_after / my_rate_after')
    ap.add_argument('--records-dir', default=DEFAULT_RECORDS,
                    help='记录目录根（默认 data/archive/duel_records）')
    ap.add_argument('--db', default=None, help='数据库路径（默认 data/masterduel.db）')
    ap.add_argument('--list', action='store_true', help='只看现状，不写')
    ap.add_argument('--apply', action='store_true', help='写进库（只补 NULL，不覆盖）')
    ap.add_argument('--dry-run', action='store_true', help='搭配 --apply：只打印')
    args = ap.parse_args(argv)

    if args.list or not args.apply:
        return cmd_list(args)
    return cmd_apply(args)


if __name__ == '__main__':
    sys.exit(main())
