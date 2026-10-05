# -*- coding: utf-8 -*-
"""backfill_peak_rate 单测：apply_one 的「只补空 / 已有值不动 / 无对应对局静默」。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import backfill_peak_rate as bpr                                     # noqa: E402
from core.storage import Storage                                     # noqa: E402


class TestApplyOne(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        self.recd = os.path.join(self.records, '20260928_130000')
        os.makedirs(self.recd)
        self.st = Storage(os.path.join(self.tmp, 'masterduel.db'))
        self.st.init_schema()
        self.st.upsert_match({'match_id': 'm1',
                              'started_at': '2026-09-28 13:00:00.000',
                              'game_mode': 19,
                              'record_dir': '20260928_130000'})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _probe(self, **kw):
        data = {'before': 1507.49, 'after': 1515.62, 'season': 57}
        data.update(kw)
        with open(os.path.join(self.recd, bpr.PROBE_NAME), 'w',
                  encoding='utf-8', newline='') as f:
            f.write(json.dumps(data, ensure_ascii=False))

    def test_apply_one_sets_columns(self):
        self._probe()
        note = bpr.apply_one(self.st, self.records, '20260928_130000')
        self.assertIn('1507.49 -> 1515.62', note)
        with self.st._open() as conn:
            row = dict(conn.execute('SELECT * FROM matches').fetchone())
        self.assertAlmostEqual(row['peak_rate_before'], 1507.49, places=2)
        self.assertAlmostEqual(row['peak_rate_after'], 1515.62, places=2)
        self.assertEqual(row['peak_season'], 57)

    def test_apply_one_skips_when_has_value(self):
        self._probe()
        self.st.backfill_peak_rate('m1', 1.0, 2.0, 1)     # 先占位
        self.assertIsNone(bpr.apply_one(self.st, self.records,
                                        '20260928_130000'))
        with self.st._open() as conn:
            row = dict(conn.execute('SELECT * FROM matches').fetchone())
        self.assertAlmostEqual(row['peak_rate_after'], 2.0, places=2)

    def test_apply_one_silent_without_match(self):
        self._probe()
        self.assertIsNone(bpr.apply_one(self.st, self.records, 'no_such_dir'))

    def test_apply_one_silent_without_probe(self):
        self.assertIsNone(bpr.apply_one(self.st, self.records,
                                        '20260928_130000'))

    def test_run_stats(self):
        self._probe()
        stat = bpr.run(self.records, self.st, apply=True)
        self.assertEqual(stat['ready'], 1)
        stat = bpr.run(self.records, self.st, apply=True)   # 幂等：第二次已无得补
        self.assertEqual(stat['skipped_has_value'], 1)


if __name__ == '__main__':
    unittest.main()
