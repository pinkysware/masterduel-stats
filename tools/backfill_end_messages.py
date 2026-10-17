# -*- coding: utf-8 -*-
"""backfill_end_messages —— 历史回补：end_messages.jsonl 的 winbits/文案 → 既有对局行。

背景（用户拍板四连）
--------------------
胜负判据 = WinIcon 状态位（winbit），**文案判据退役**。历史 log/api 行里的
result 有的是当年文案推的、可能判反 —— winbit 优先、允许覆盖（冲突记
result_note）；``data/archive/end_messages.jsonl`` 里 **16 条样本只有后 7 条
带 winbits**，没带的只能补 ``result_note``（文案只记录不判断），
胜负走 ``--set-result`` 手动补。

配对规则（docs/对局记录格式.md §5.2）
------------------------------------
* 窗口：``|ended_at − captured_at| ≤ W``（默认 60 秒；结算画面秒级抓取，
  实测 captured_at 比 ended_at 晚 0~几十秒）；
* **唯一认领**：一条 end_message 只配一行、一行只认领一条；
  两行等距/两候选相邻 <60 秒 → 歧义放弃（不猜）；
* 目标行必须有 ``ended_at``（掉线局没有，留给手动补填）。

用法
----
    python tools/backfill_end_messages.py --list          # 只看配对计划（默认）
    python tools/backfill_end_messages.py --apply         # 写库（只补 NULL + winbit 覆盖规则）
    python tools/backfill_end_messages.py --set-result <match_id> Win|Lose
    python tools/backfill_end_messages.py --window 90     # 放宽窗口
"""

import json
import os
import sys
from datetime import datetime as _dt

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import paths
from core import duel_record
from core.storage import Storage, DONE_RESULTS

DEFAULT_EM = paths.data_path('archive', 'end_messages.jsonl')


def load_entries(path=DEFAULT_EM):
    """end_messages.jsonl -> [{captured_at(datetime), ...原始字段}]（按时间序）。"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e['_dt'] = duel_record.parse_dt(e.get('captured_at'))
            if e['_dt'] is not None:
                out.append(e)
    out.sort(key=lambda e: e['_dt'])
    return out


def _ended_ts(row):
    v = row.get('ended_at')
    if not v:
        return None
    try:
        return _dt.strptime(v[:19], '%Y-%m-%d %H:%M:%S').timestamp()
    except ValueError:
        return None


def pair_rows(entries, rows, window=60):
    """entries × matches 行 -> 唯一认领。返回 ({match_id: entry}, [歧义记录])。

    * 目标行必须有 ended_at；
    * 取 |ended_at − captured_at| 最小者；距离并列/已在用 -> 试次近；
    * 行已被认领 -> 该 entry 落空（同一局的两条结算文案是正常现象，第一条赢）。
    """
    claims, used, ambiguous = {}, set(), []
    cand_rows = [r for r in rows if _ended_ts(r) is not None]
    for e in entries:
        t = e['_dt'].timestamp()
        cands = sorted(
            ((abs(_ended_ts(r) - t), _ended_ts(r), r) for r in cand_rows
             if abs(_ended_ts(r) - t) <= window),
            key=lambda x: (x[0], x[1]))
        if not cands:
            continue
        if len(cands) >= 2 and cands[1][0] - cands[0][0] < 1e-9:
            ambiguous.append((e.get('captured_at'), 'equidistant-rows'))
            continue
        target = None
        for dist, _, r in cands:
            if r['match_id'] not in used:
                target = r
                break
        if target is None:
            ambiguous.append((e.get('captured_at'), 'rows-already-claimed'))
            continue
        used.add(target['match_id'])
        claims[target['match_id']] = e
    return claims, ambiguous


def backfill(storage, entries, rows, apply=False, window=60):
    """按认领结果回填。返回报告 dict；``apply=False`` 只打印。

    ``entries`` 可以是 :func:`load_entries` 的产物，也可以是原始 jsonl dict
    （缺 ``_dt`` 时按 ``captured_at`` 现场解析）。"""
    for e in entries:
        if e.get('_dt') is None:
            e['_dt'] = duel_record.parse_dt(e.get('captured_at'))
    entries = [e for e in entries if e.get('_dt') is not None]
    claims, ambiguous = pair_rows(entries, rows, window=window)
    by_id = {r['match_id']: r for r in rows}
    report = {'claims': [], 'ambiguous': ambiguous, 'applied': 0,
              'unpaired': 0}
    claimed_ids = set(claims)
    for r in rows:
        if _ended_ts(r) is not None and r['match_id'] not in claimed_ids:
            report['unpaired'] += 1
    for match_id, e in sorted(claims.items()):
        row = by_id[match_id]
        plan = {
            'match_id': match_id,
            'captured_at': e.get('captured_at'),
            'ended_at': row.get('ended_at'),
            'winbits': e.get('winbits'),
            'result_note': (e.get('texts') or {}).get('messageText'),
            'row_has_winbits': row.get('winbits') is not None,
        }
        if not apply:
            report['claims'].append(plan)
            continue
        actions = storage.apply_record_meta(
            match_id,
            winbits=e.get('winbits'),
            result=duel_record.derive_result(e.get('winbits')),
            result_src='winbit',
            result_note=plan['result_note'])
        plan['actions'] = actions
        report['claims'].append(plan)
        if actions:
            report['applied'] += 1
    return report


def _print_report(report):
    for c in report['claims']:
        print('· %(captured_at)s -> %(match_id)s（ended %(ended_at)s）' % c)
        print('  winbits=%(winbits)s 行内已有=%(row_has_winbits)s 文案=%(result_note)s' % c)
        if 'actions' in c:
            print('  动作：%s' % json.dumps(c['actions'], ensure_ascii=False))
    for t, why in report['ambiguous']:
        print('？歧义放弃：%s（%s）' % (t, why))
    print('小结：认领 %d 条 / 歧义 %d / 有 ended 却没配上的行 %d'
          % (len(report['claims']), len(report['ambiguous']),
             report['unpaired']))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true', help='写库')
    ap.add_argument('--list', action='store_true', help='同默认 dry-run')
    ap.add_argument('--window', type=int, default=60,
                    help='|ended_at − captured_at| 窗口秒数（默认 60）')
    ap.add_argument('--em-path', default=DEFAULT_EM)
    ap.add_argument('--db', default=None)
    ap.add_argument('--set-result', nargs=2, metavar=('MATCH_ID', 'WIN|LOSE'),
                    help='手动补填胜负（判不出结局的掉线/平局/无 winbits 局）')
    args = ap.parse_args(argv)

    storage = Storage(args.db) if args.db else Storage()
    if args.set_result:
        match_id, result = args.set_result
        result = {'win': 'Win', 'lose': 'Lose'}.get(result.lower(), result)
        n = storage.set_result(match_id, result)
        print('%s：更新 %d 行（result=%s, src=manual）' % (match_id, n, result))
        return 0

    entries = load_entries(args.em_path)
    rows = [_r for _r in storage.list_matches(limit=10 ** 9)]
    report = backfill(storage, entries, rows, apply=args.apply,
                      window=args.window)
    _print_report(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
