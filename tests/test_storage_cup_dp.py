# -*- coding: utf-8 -*-
"""DC 杯 DP 三列的 storage 单测：四处联动的迁移 / upsert 往返 /
KEEP_IF_NEW_NULL 语义 / backfill_cup_dp 只补空。

背景（口径同 test_storage_peak_rate.py）：加列必须四处联动 ——
SCHEMA_SQL（新库）/ ADDED_COLUMNS（老库迁移）/ MATCH_FIELDS（INSERT 列清单，
漏了 = 静默丢）/ KEEP_IF_NEW_NULL（重解析不冲掉已回填值）。
"""

import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import (ADDED_COLUMNS, KEEP_IF_NEW_NULL,           # noqa: E402
                          MATCH_FIELDS, Storage)


def _base_row(match_id='cup01', game_mode=12):
    """一行能过 upsert 最小校验的对局（DC 杯局）。"""
    return {'match_id': match_id,
            'started_at': '2026-10-23 12:29:00.000',
            'game_mode': game_mode}


class TestCupColumns(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'masterduel.db'))
        self.st.init_schema()

    def _columns(self):
        with self.st._open() as conn:
            return {r[1] for r in conn.execute('PRAGMA table_info(matches)')}

    def _get(self, match_id):
        with self.st._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
        return dict(row) if row is not None else None

    def test_new_db_has_cup_columns(self):
        cols = self._columns()
        self.assertIn('cup_dp_before', cols)
        self.assertIn('cup_dp_after', cols)
        self.assertIn('cup_stage', cols)

    def test_four_way_wiring(self):
        """四处联动必须都在 —— 这是 2026-09-16 事故的教训。"""
        self.assertIn('cup_dp_before', MATCH_FIELDS)
        self.assertIn('cup_dp_after', MATCH_FIELDS)
        self.assertIn('cup_stage', MATCH_FIELDS)
        self.assertIn('cup_dp_before', KEEP_IF_NEW_NULL)
        self.assertIn('cup_dp_after', KEEP_IF_NEW_NULL)
        self.assertIn('cup_stage', KEEP_IF_NEW_NULL)
        names = {n for n, _ in ADDED_COLUMNS}
        self.assertIn('cup_dp_before', names)
        self.assertIn('cup_dp_after', names)
        self.assertIn('cup_stage', names)

    def test_old_db_migrates(self):
        """老库（没这三列）跑 init_schema 后必须长出来。

        ⚠️ 用「建全库 -> DROP 掉三列」模拟老库 —— 手写一份残缺 CREATE TABLE
        会连别的必填列都没有，测出来的是「建表脚本错」而不是「迁移没跑」。
        """
        path = os.path.join(self.tmp, 'old.db')
        fresh = Storage(path)
        fresh.init_schema()
        with fresh._open() as c:
            for col in ('cup_dp_before', 'cup_dp_after', 'cup_stage'):
                c.execute('ALTER TABLE matches DROP COLUMN %s' % col)
        with fresh._open() as c:
            cols = {r[1] for r in c.execute('PRAGMA table_info(matches)')}
        self.assertNotIn('cup_dp_before', cols)      # 确认真的「老」了
        old = Storage(path)
        old.init_schema()                            # 迁移
        with old._open() as c:
            cols = {r[1] for r in c.execute('PRAGMA table_info(matches)')}
        self.assertIn('cup_dp_before', cols)
        self.assertIn('cup_dp_after', cols)
        self.assertIn('cup_stage', cols)

    def test_upsert_roundtrip(self):
        """采集器写入 -> 读回来一模一样（漏 MATCH_FIELDS 这里就红）。"""
        row = _base_row()
        row.update(cup_dp_before=0, cup_dp_after=1000, cup_stage=2)
        self.st.upsert_match(row)
        got = self._get('cup01')
        self.assertEqual(got['cup_dp_before'], 0)
        self.assertEqual(got['cup_dp_after'], 1000)
        self.assertEqual(got['cup_stage'], 2)

    def test_backfill_sets_null(self):
        self.st.upsert_match(_base_row())
        acts = self.st.backfill_cup_dp('cup01', 0, 1000, 2)
        self.assertEqual(acts.get('cup_dp_before'), 'set')
        self.assertEqual(acts.get('cup_dp_after'), 'set')
        self.assertEqual(acts.get('cup_stage'), 'set')
        got = self._get('cup01')
        self.assertEqual(got['cup_dp_after'], 1000)

    def test_backfill_keeps_existing(self):
        """只补空 —— 已有值一律 kept，绝不覆盖。"""
        row = _base_row()
        row.update(cup_dp_before=0, cup_dp_after=1000, cup_stage=2)
        self.st.upsert_match(row)
        acts = self.st.backfill_cup_dp('cup01', 500, 1500, 2)
        self.assertEqual(acts['cup_dp_after'], 'kept:1000')
        got = self._get('cup01')
        self.assertEqual(got['cup_dp_after'], 1000)

    def test_backfill_unchanged(self):
        row = _base_row()
        row.update(cup_dp_before=0, cup_dp_after=1000, cup_stage=2)
        self.st.upsert_match(row)
        acts = self.st.backfill_cup_dp('cup01', 0, 1000, 2)
        self.assertEqual(acts['cup_dp_after'], 'unchanged')

    def test_backfill_all_none(self):
        self.st.upsert_match(_base_row())
        self.assertEqual(self.st.backfill_cup_dp('cup01', None, None, None), {})

    def test_backfill_missing_row(self):
        self.assertEqual(self.st.backfill_cup_dp('nope', 0, 1000, 2), {})

    def test_backfill_bad_stage_skipped(self):
        """stage 只认 1/2 —— 别的值当 None 跳过（防调用方算错写进库）。"""
        self.st.upsert_match(_base_row())
        acts = self.st.backfill_cup_dp('cup01', 0, 1000, 9)
        self.assertNotIn('cup_stage', acts)
        self.assertEqual(acts.get('cup_dp_after'), 'set')

    def test_reparse_keeps_backfilled(self):
        """重解析 upsert（新值 NULL）不能把已回填的 DP 冲掉。"""
        row = _base_row()
        row.update(cup_dp_before=0, cup_dp_after=1000, cup_stage=2)
        self.st.upsert_match(row)
        again = _base_row()          # 不带 cup_* -> None
        self.st.upsert_match(again)
        got = self._get('cup01')
        self.assertEqual(got['cup_dp_after'], 1000)
        self.assertEqual(got['cup_stage'], 2)


if __name__ == '__main__':
    unittest.main()
