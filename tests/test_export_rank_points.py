# -*- coding: utf-8 -*-
"""导出页「段位列」支持巅峰积分 / DC 杯 DP（用户 2026-10-23）。

口径：
* 天梯局 -> 段位名（原样，不能被动到）
* 巅峰局 -> `'1507.49'`（2 位小数）
* DC 杯 **2 阶段** -> `'DP 1000'`
* DC 杯 1 阶段 / 活动局 -> 空（DLv 不是积分）

落点：`_page_row` 的 `rank_points_before/after` + `export_rows` 的
`rank_label_before/after`（三列里的前两列；`rank_delta` 保持段位语义）。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _row(**kw):
    m = {'match_id': 'x1', 'result': 'Win', 'went_first': True,
         'started_at': '2026-10-23 12:29:00', 'ended_at': '2026-10-23 12:44:00',
         'my_deck_name': '闪刀姬', 'opp_deck_name': '烙印'}
    m.update(kw)
    return m


class _St(object):
    """最小 storage 桩：只给 export_rows 要的两个方法。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def get_recent_matches(self, limit=100, account=None):
        return list(self._rows)

    def get_stats_by_deck(self, since=None, until=None, mode_group=None,
                          account=None):
        return []


class TestRankPointsPure(unittest.TestCase):
    """`_rank_points`（纯函数）。"""

    def setUp(self):
        from core import ui_state
        self.f = ui_state._rank_points

    def test_peak_before_after(self):
        m = _row(peak_rate_before=1507.49, peak_rate_after=1515.62)
        self.assertEqual(self.f(m, 'peak', 'before'), '1507.49')
        self.assertEqual(self.f(m, 'peak', 'after'), '1515.62')

    def test_peak_missing_side(self):
        m = _row(peak_rate_after=1515.62)
        self.assertIsNone(self.f(m, 'peak', 'before'))
        self.assertEqual(self.f(m, 'peak', 'after'), '1515.62')

    def test_dc_stage2(self):
        m = _row(cup_stage=2, cup_dp_before=0, cup_dp_after=1000)
        self.assertEqual(self.f(m, 'dc', 'before'), 'DP 0')
        self.assertEqual(self.f(m, 'dc', 'after'), 'DP 1000')

    def test_dc_stage1_gives_none(self):
        """★ 1 阶段打的是 DLv（段位），不许当积分写进段位列。"""
        m = _row(cup_stage=1, cup_dp_before=19, cup_dp_after=20)
        self.assertIsNone(self.f(m, 'dc', 'before'))
        self.assertIsNone(self.f(m, 'dc', 'after'))

    def test_ranked_has_no_points(self):
        """天梯局不走积分（走 rank_label）。"""
        m = _row(peak_rate_after=1500.0, cup_dp_after=1000, cup_stage=2)
        self.assertIsNone(self.f(m, 'ranked', 'after'))

    def test_event_has_no_points(self):
        m = _row(peak_rate_after=1500.0)
        self.assertIsNone(self.f(m, 'event', 'after'))

    def test_bad_values(self):
        m = _row(peak_rate_after='abc', cup_stage=2, cup_dp_after='xyz')
        self.assertIsNone(self.f(m, 'peak', 'after'))
        self.assertIsNone(self.f(m, 'dc', 'after'))

    def test_zero_dp_is_valid(self):
        m = _row(cup_stage=2, cup_dp_before=0, cup_dp_after=0)
        self.assertEqual(self.f(m, 'dc', 'before'), 'DP 0')
        self.assertEqual(self.f(m, 'dc', 'after'), 'DP 0')


class TestExportRankColumns(unittest.TestCase):
    """`export_rows` 的段位两列按模式挑值。"""

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def _export(self, **kw):
        rows = self.ui_state.export_rows(_St([_row(**kw)]))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_ranked_unchanged(self):
        """★ 天梯局的三列**必须原样**（这是回归防线）。"""
        r = self._export(game_mode=3, my_rank_before=6, my_rate_before=2,
                         my_rank_after=6, my_rate_after=1)
        self.assertIn('Diamond', r['rank_label_before'])
        self.assertIn('Diamond', r['rank_label_after'])
        self.assertEqual(r['rank_delta'], '升段')

    def test_peak_writes_points(self):
        r = self._export(game_mode=19, peak_rate_before=1507.49,
                         peak_rate_after=1515.62)
        self.assertEqual(r['rank_label_before'], '1507.49')
        self.assertEqual(r['rank_label_after'], '1515.62')

    def test_peak_single_side(self):
        r = self._export(game_mode=19, peak_rate_after=1515.62)
        self.assertEqual(r['rank_label_before'], '')
        self.assertEqual(r['rank_label_after'], '1515.62')

    def test_dc_stage2_writes_dp(self):
        r = self._export(game_mode=12, cup_stage=2, cup_dp_before=0,
                         cup_dp_after=1000)
        self.assertEqual(r['rank_label_before'], 'DP 0')
        self.assertEqual(r['rank_label_after'], 'DP 1000')

    def test_dc_stage1_empty(self):
        r = self._export(game_mode=12, cup_stage=1, cup_dp_before=19,
                         cup_dp_after=20)
        self.assertEqual(r['rank_label_before'], '')
        self.assertEqual(r['rank_label_after'], '')

    def test_dc_without_points_empty(self):
        r = self._export(game_mode=12, cup_stage=2)
        self.assertEqual(r['rank_label_before'], '')
        self.assertEqual(r['rank_label_after'], '')

    def test_peak_does_not_leak_cup_points(self):
        """一行不该同时出现两种积分（模式决定取哪个）。"""
        r = self._export(game_mode=19, peak_rate_after=1515.62,
                         cup_stage=2, cup_dp_after=1000)
        self.assertEqual(r['rank_label_after'], '1515.62')

    def test_headers_say_points_too(self):
        """表头要说明这一列可能是积分（用户拿去 Excel 看，光写「段位」会困惑）。"""
        labels = dict(self.ui_state.EXPORT_COLUMNS)
        self.assertEqual(labels['rank_label_before'], '局前段位/积分')
        self.assertEqual(labels['rank_label_after'], '局后段位/积分')
        # 键名不许动（数据层兼容）
        self.assertIn('rank_label_before',
                      [k for k, _l in self.ui_state.EXPORT_COLUMNS])

    def test_csv_carries_points(self):
        """落盘（CSV）里也要能看见 —— 用户导出就是要拿去用的。"""
        import io
        import tempfile
        d = tempfile.mkdtemp(prefix='exp-')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        rows = self.ui_state.export_rows(_St([
            _row(game_mode=19, peak_rate_after=1515.62),
            _row(match_id='c1', game_mode=12, cup_stage=2,
                 cup_dp_after=1000),
        ]))
        path = self.ui_state.write_export(os.path.join(d, 'a.csv'), rows)
        text = io.open(path, encoding='utf-8-sig').read()
        self.assertIn('1515.62', text)
        self.assertIn('DP 1000', text)


if __name__ == '__main__':
    unittest.main()
