# -*- coding: utf-8 -*-
"""backfill_cup_dp 单测：扫目录 / 阶段门控 / 只补空 / 无对应对局静默 / apply_one。

口径同 test_backfill_peak_rate.py；多一条 **1 阶段必须跳过**（那是 DLv，不是 DP）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import backfill_cup_dp                                              # noqa: E402
from core.storage import Storage                                    # noqa: E402


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        os.makedirs(self.records)
        self.st = Storage(os.path.join(self.tmp, 'masterduel.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_probe(self, name, data):
        d = os.path.join(self.records, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'cup_dp.json'), 'w',
                  encoding='utf-8', newline='') as f:
            json.dump(data, f, ensure_ascii=False)
        return d

    def _make_row(self, name, **extra):
        row = {'match_id': name,
               'started_at': '2026-10-23 12:29:00.000',
               'game_mode': 12,
               'record_dir': name}
        row.update(extra)
        self.st.upsert_match(row)

    def _get(self, name):
        row = self.st.find_by_record_dir(name)
        return dict(row) if row else None


class TestResolvedTriple(unittest.TestCase):

    def test_ok(self):
        self.assertEqual(
            backfill_cup_dp.resolved_triple(
                {'before': 0, 'after': 1000, 'stage': 2}),
            (0, 1000, 2))

    def test_stage1_rejected(self):
        """★ 1 阶段打的是 DLv，不是 DP —— 必须拒绝（哪怕数据看着合法）。"""
        self.assertIsNone(backfill_cup_dp.resolved_triple(
            {'before': 0, 'after': 20, 'stage': 1}))

    def test_missing_stage_rejected(self):
        self.assertIsNone(backfill_cup_dp.resolved_triple(
            {'before': 0, 'after': 1000}))

    def test_bad_values(self):
        self.assertIsNone(backfill_cup_dp.resolved_triple(
            {'before': None, 'after': 1000, 'stage': 2}))
        self.assertIsNone(backfill_cup_dp.resolved_triple(None))


class TestRun(Base):

    def test_no_probes(self):
        stat = backfill_cup_dp.run(self.records, self.st)
        self.assertEqual(stat['total'], 0)

    def test_ready_and_apply(self):
        self._make_probe('rec_a', {'before': 0, 'after': 1000, 'win': 1,
                                   'stage': 2})
        self._make_row('rec_a')
        stat = backfill_cup_dp.run(self.records, self.st)
        self.assertEqual(stat['ready'], 1)
        # dry-run 不写
        backfill_cup_dp.run(self.records, self.st, apply=True, dry_run=True)
        self.assertIsNone(self._get('rec_a')['cup_dp_after'])
        # 真写
        backfill_cup_dp.run(self.records, self.st, apply=True)
        got = self._get('rec_a')
        self.assertEqual(got['cup_dp_after'], 1000)
        self.assertEqual(got['cup_dp_before'], 0)
        self.assertEqual(got['cup_stage'], 2)

    def test_stage1_skipped(self):
        self._make_probe('rec_b', {'before': 0, 'after': 20, 'stage': 1})
        self._make_row('rec_b')
        stat = backfill_cup_dp.run(self.records, self.st, apply=True)
        self.assertEqual(stat['not_stage2'], 1)
        self.assertIsNone(self._get('rec_b')['cup_dp_after'])

    def test_existing_value_not_overwritten(self):
        self._make_probe('rec_c', {'before': 500, 'after': 1500, 'stage': 2})
        self._make_row('rec_c', cup_dp_before=0, cup_dp_after=1000, cup_stage=2)
        stat = backfill_cup_dp.run(self.records, self.st, apply=True)
        self.assertEqual(stat['skipped_has_value'], 1)
        self.assertEqual(self._get('rec_c')['cup_dp_after'], 1000)

    def test_unmatched(self):
        self._make_probe('rec_d', {'before': 0, 'after': 1000, 'stage': 2})
        stat = backfill_cup_dp.run(self.records, self.st, apply=True)
        self.assertEqual(stat['unmatched'], 1)

    def test_unreadable(self):
        d = os.path.join(self.records, 'rec_e')
        os.makedirs(d)
        with open(os.path.join(d, 'cup_dp.json'), 'w', encoding='utf-8') as f:
            f.write('{ 这不是 json')
        stat = backfill_cup_dp.run(self.records, self.st)
        self.assertEqual(stat['unreadable'], 1)

    def test_apply_one_returns_short_sentence(self):
        self._make_probe('rec_f', {'before': 0, 'after': 1000, 'stage': 2})
        self._make_row('rec_f')
        msg = backfill_cup_dp.apply_one(self.st, self.records, 'rec_f')
        self.assertIsNotNone(msg)
        self.assertIn('DC杯积分入库', msg)
        self.assertIn('0 -> 1000', msg)
        self.assertEqual(self._get('rec_f')['cup_dp_after'], 1000)

    def test_apply_one_twice_is_noop(self):
        """幂等：第二次看到已有值就静默返回 None。"""
        self._make_probe('rec_g', {'before': 0, 'after': 1000, 'stage': 2})
        self._make_row('rec_g')
        self.assertIsNotNone(
            backfill_cup_dp.apply_one(self.st, self.records, 'rec_g'))
        self.assertIsNone(
            backfill_cup_dp.apply_one(self.st, self.records, 'rec_g'))

    def test_apply_one_no_probe_file(self):
        self.assertIsNone(
            backfill_cup_dp.apply_one(self.st, self.records, 'nope'))


if __name__ == '__main__':
    unittest.main()
