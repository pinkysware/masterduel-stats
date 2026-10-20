# -*- coding: utf-8 -*-
"""record 侧位 vs 日志 seat 真值的对账（2026-09-13 实测 3/3 一致）。

思路：存量 record 没有我方卡组快照（infer_seat 跑不了），但日志行有
seat 真值 —— 对手露出的卡（opp_revealed_ids）应该与 record 中
「非 seat 侧」的 revealed 吻合，借此验证两侧的编号体系对齐。
"""

import json
import os
import sqlite3
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import duel_record as dr
from core import paths

# ★ BUG-B9A-09：改走 `paths.data_path` —— 安装版（Program Files）下
#   `PROJ/data` 不可写，DATA_DIR 在 `%LOCALAPPDATA%`，写死 PROJ 会「库不在」。
DB = paths.data_path('masterduel.db')
RECORDS = paths.data_path('archive', 'duel_records')

PAIRS = [
    ('record_20260913_123326', '13cac4a6ef3b1445'),
    ('record_20260913_123714', 'e3eae5e51500f4ea'),
    ('record_20260913_122146', '872c9bdcdc11f421'),
]

if not os.path.isfile(DB):
    raise SystemExit('库不在：%s（先跑 tools/archive_matches.py --sync）' % DB)
if not os.path.isdir(RECORDS):
    raise SystemExit('record 目录不在：%s' % RECORDS)

conn = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
conn.row_factory = sqlite3.Row
ok = 0
for rec, mid in PAIRS:
    duel = dr.decode_record(dr.scan_record(os.path.join(RECORDS, rec)))
    log = dict(conn.execute(
        'SELECT seat, went_first, first_player, opp_revealed_ids FROM matches'
        ' WHERE match_id=?', (mid,)).fetchone())
    opp_log = set(json.loads(log['opp_revealed_ids'] or '[]'))
    r = duel['summary']['revealed']
    cov = {side: (len(opp_log & set(ids)) / len(opp_log) if opp_log else 0.0)
           for side, ids in r.items()}
    truth_opp = 'p0' if log['seat'] == 1 else 'p1'
    best = max(cov, key=lambda k: cov[k])
    hit = best == truth_opp
    ok += hit
    print('%s | log seat=%s | p0覆盖=%.2f p1覆盖=%.2f | 真值对手侧=%s -> %s'
          % (rec, log['seat'], cov['p0'], cov['p1'], truth_opp,
             '一致' if hit else '不一致'))
print('%d/%d 一致' % (ok, len(PAIRS)))
