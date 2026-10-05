# -*- coding: utf-8 -*-
"""巅峰积分三列的 storage 单测：四处联动的迁移 / upsert 往返 /
KEEP_IF_NEW_NULL 语义 / backfill_peak_rate 只补空。

背景（2026-09-28 计划审查 A1）：加列必须四处联动 —— SCHEMA_SQL（新库）/
ADDED_COLUMNS（老库迁移）/ MATCH_FIELDS（INSERT 列清单，漏了 = 静默丢）/
KEEP_IF_NEW_NULL（重解析不冲掉已回填值）。
"""

import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import MATCH_FIELDS, KEEP_IF_NEW_NULL, Storage    # noqa: E402


def _base_row(match_id='peak01', game_mode=19):
    """一行能过 upsert 最小校验的对局（巅峰局）。"""
    return {'match_id': match_id,
            'started_at': '2026-09-28 12:57:00.000',
            'game_mode': game_mode}


class TestPeakColumns(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'masterduel.db'))
        self.st.init_schema()                # 新库建表（含 peak 三列）

    def _columns(self):
        with self.st._open() as conn:
            return {r[1] for r in conn.execute('PRAGMA table_info(matches)')}

    def _get(self, match_id):
        with self.st._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
        return dict(row) if row is not None else None

    def test_new_db_has_peak_columns(self):
        cols = self._columns()
        self.assertIn('peak_rate_before', cols)
        self.assertIn('peak_rate_after', cols)
        self.assertIn('peak_season', cols)

    def test_match_fields_declared(self):
        # 漏 MATCH_FIELDS = INSERT 列清单里没有 -> 采集器写了也静默丢（最致命）
        for f in ('peak_rate_before', 'peak_rate_after', 'peak_season'):
            self.assertIn(f, MATCH_FIELDS)
            self.assertIn(f, KEEP_IF_NEW_NULL)

    def test_upsert_roundtrip(self):
        row = _base_row()
        row.update({'peak_rate_before': 1507.49, 'peak_rate_after': 1515.62,
                    'peak_season': 57})
        self.st.upsert_match(row)
        got = self._get('peak01')
        self.assertAlmostEqual(got['peak_rate_before'], 1507.49, places=2)
        self.assertAlmostEqual(got['peak_rate_after'], 1515.62, places=2)
        self.assertEqual(got['peak_season'], 57)

    def test_keep_if_new_null(self):
        # 先带值入库，再不带值重解析 upsert -> 已回填的积分**不能被冲掉**
        row = _base_row()
        row.update({'peak_rate_after': 1515.62, 'peak_season': 57})
        self.st.upsert_match(row)
        self.st.upsert_match(_base_row())          # 同 match_id，peak 全 NULL
        got = self._get('peak01')
        self.assertAlmostEqual(got['peak_rate_after'], 1515.62, places=2)
        self.assertEqual(got['peak_season'], 57)

    def test_backfill_only_null(self):
        self.st.upsert_match(_base_row('peak02'))
        acts = self.st.backfill_peak_rate('peak02', 1507.49, 1515.62, 57)
        self.assertEqual(acts.get('peak_rate_after'), 'set')
        # 再补一次不同值 -> 保留旧值，不覆盖
        acts = self.st.backfill_peak_rate('peak02', 1.0, 2.0, 1)
        self.assertEqual(acts.get('peak_rate_after'), 'kept:1515.62')
        self.assertEqual(acts.get('peak_season'), 'kept:57')
        got = self._get('peak02')
        self.assertAlmostEqual(got['peak_rate_before'], 1507.49, places=2)

    def test_backfill_partial_and_empty(self):
        self.st.upsert_match(_base_row('peak03'))
        # season 缺（None）-> 只写有的列
        acts = self.st.backfill_peak_rate('peak03', None, 1515.62, None)
        self.assertEqual(sorted(acts), ['peak_rate_after'])
        # 全 None -> 不动库
        self.assertEqual(self.st.backfill_peak_rate('peak03', None, None, None),
                         {})
        # 行不存在 -> {}
        self.assertEqual(self.st.backfill_peak_rate('nope', 1.0, 2.0, 3), {})


if __name__ == '__main__':
    unittest.main()
