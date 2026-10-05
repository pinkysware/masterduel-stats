# -*- coding: utf-8 -*-
"""S-4 闸门单测：record/log 两路径不许对同一局混用（M6 计划 §四.2）。

判据三条件同时满足才算混用（缺一放行）：
  1. 本次 upsert 的 match_id 不在库内（已并档行重跑 = 正常更新，不误伤）；
  2. 库内存在 record_dir 非空的行；
  3. started_at 相差 ≤ S4_WINDOW_SEC（60s）。
全部用临时目录建库，不碰真实 data/masterduel.db。
"""

import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core.storage import Storage  # noqa: E402
import archive_matches as am  # noqa: E402

T0 = '2026-09-13 12:00:00'            # record 行的秒级锚
T0_MS = '2026-09-13 12:00:30.650'     # log 行的毫秒级形态（真实日志长这样）


def _record_row(match_id='rec:record_x', started_at=T0):
    return {'match_id': match_id, 'started_at': started_at, 'mode': 'pvp',
            'origin': 'record', 'record_dir': 'record_x', 'result': 'Win'}


def _log_row(match_id='sha1_new', started_at=T0_MS):
    return {'match_id': match_id, 'started_at': started_at, 'mode': 'pvp',
            'result': 'Win'}


class TestFindRecordMismatch(unittest.TestCase):
    """纯函数判据矩阵（不碰库）。"""

    def test_collision_within_window(self):
        """record 行 + 时间邻近 + match_id 新 -> 混用，返回那条库内行。"""
        db = [dict(_record_row())]
        bad = am.find_record_mismatch(db, 'sha1_new', T0_MS)
        self.assertIsNotNone(bad)
        self.assertEqual(bad['record_dir'], 'record_x')

    def test_same_match_id_not_gated(self):
        """match_id 命中（含已并档的 record 行）= 正常 upsert 更新，不误伤。"""
        db = [dict(_record_row(match_id='sha1_new'))]
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_new', T0_MS))

    def test_boundary_60s(self):
        """窗宽边界：≤60s 拦、>60s 放（log 行毫秒形态 vs record 秒级）。"""
        db = [dict(_record_row())]
        edge = '2026-09-13 12:01:00.000'      # 恰好 60s
        self.assertIsNotNone(am.find_record_mismatch(db, 'sha1_new', edge))
        beyond = '2026-09-13 12:01:01.000'    # 61s
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_new', beyond))

    def test_no_record_rows_not_gated(self):
        """库里没有 record 行（纯 log/api 时代）-> 闸门永不触发。"""
        db = [{'match_id': 'other', 'started_at': T0, 'record_dir': None}]
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_new', T0_MS))

    def test_bad_started_at_not_gated(self):
        """started_at 缺失/解析不了 -> 不硬猜，放行。"""
        db = [dict(_record_row())]
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_new', None))
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_new', '垃圾'))


class TestGateAgainstRealStorage(unittest.TestCase):
    """与真库对上：list_matches 的行形状能直接喂闸门。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='m6s4_')
        self.st = Storage(os.path.join(self.d, 'masterduel.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_gate_sees_record_row_from_db(self):
        self.st.upsert_match(_record_row())
        self.st.upsert_match(_log_row(match_id='sha1_old',
                                      started_at='2026-09-13 11:00:00'))
        db = self.st.list_matches(limit=None)
        # 库内已有同 match_id 的 log 行 -> 放行（正常更新）
        self.assertIsNone(am.find_record_mismatch(db, 'sha1_old', T0_MS))
        # 全新 log 行撞 record 行 -> 拦下，明细带 record_dir
        bad = am.find_record_mismatch(db, 'sha1_new', T0_MS)
        self.assertIsNotNone(bad)
        self.assertEqual(bad['record_dir'], 'record_x')


if __name__ == '__main__':
    unittest.main()
