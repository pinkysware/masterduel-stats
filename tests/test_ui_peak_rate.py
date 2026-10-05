# -*- coding: utf-8 -*-
"""巅峰积分的 UI 单测（2026-09-28 **二口径**）：

* **悬浮窗回退**：段位列只显示巅峰图标（原样）—— `_recent_row` 不再覆写
  badge_icon / badge_numeral，积分只作为 `peak_rate` 字段随行携带；
* **看板战绩页**：`_page_row` 也带 `peak_rate`，段位列的巅峰行画
  「巅峰图标 + 积分数字」（ui/dashboard._rank_cell 消费）；
* after 优先 / before 兜底；非巅峰行恒 None；天梯行完全不受影响。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')   # 必须在 Qt import 之前


def _base_row(**kw):
    m = {'match_id': 'p1', 'started_at': '2026-09-28 12:00:00.000',
         'game_mode': 19, 'result': 'Win'}
    m.update(kw)
    return m


class FakeDecks:
    @staticmethod
    def resolve(name):
        return None


class TestRecentRowPeak(unittest.TestCase):
    """`_recent_row`：悬浮窗数据源 —— 只带字段，不覆写徽章。"""

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def test_peak_row_keeps_icon_and_carries_rate(self):
        from core import modes
        row = self.ui_state._recent_row(
            _base_row(peak_rate_after=1515.62, peak_rate_before=1507.49),
            None, FakeDecks)
        self.assertEqual(row['badge_icon'], modes.icon_path('peak'))
        self.assertEqual(row['badge_text'], '巅峰决斗')
        self.assertEqual(row['badge_numeral'], '')      # 不显示积分数字
        self.assertEqual(row['peak_rate'], '1515.62')   # 只随行携带

    def test_peak_falls_back_to_before(self):
        row = self.ui_state._recent_row(_base_row(peak_rate_before=1507.49),
                                        None, FakeDecks)
        self.assertEqual(row['peak_rate'], '1507.49')

    def test_peak_without_rate(self):
        from core import modes
        row = self.ui_state._recent_row(_base_row(), None, FakeDecks)
        self.assertEqual(row['badge_icon'], modes.icon_path('peak'))
        self.assertIsNone(row['peak_rate'])

    def test_ranked_row_has_no_peak_rate(self):
        m = _base_row(game_mode=3, my_rank_before=5, my_rate_before=2,
                      peak_rate_after=1515.62)
        row = self.ui_state._recent_row(m, None, FakeDecks)
        self.assertIsNone(row['peak_rate'])
        self.assertEqual(row['badge_numeral'], 'II')


class TestPageRowPeak(unittest.TestCase):
    """`_page_row`：看板战绩页数据源 —— 巅峰行带 peak_rate 供段位列画图标+积分。"""

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def test_peak_row_carries_rate(self):
        row = self.ui_state._page_row(
            _base_row(peak_rate_after=1515.62, peak_rate_before=1507.49))
        self.assertEqual(row['peak_rate'], '1515.62')
        self.assertIsNone(row['rank_icon_after'])       # 天梯段位判据照旧为空

    def test_peak_falls_back_to_before(self):
        row = self.ui_state._page_row(_base_row(peak_rate_before=1507.49))
        self.assertEqual(row['peak_rate'], '1507.49')

    def test_peak_without_rate(self):
        row = self.ui_state._page_row(_base_row())
        self.assertIsNone(row['peak_rate'])

    def test_ranked_row_has_no_peak_rate(self):
        row = self.ui_state._page_row(_base_row(game_mode=3, my_rank_before=5,
                                                my_rate_before=2))
        self.assertIsNone(row['peak_rate'])


class TestDashboardRankCellPeak(unittest.TestCase):
    """`_rank_cell` 消费 peak_rate：巅峰图标 + 积分（需要 QApplication）。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def test_rank_cell_renders_icon_and_score(self):
        """巅峰行段位格 = 巅峰图标 + 积分数字，tooltip 全称。"""
        from PySide6.QtWidgets import QLabel
        from ui.dashboard import MatchRow
        row = self.ui_state._page_row(_base_row(peak_rate_after=1515.62))
        w = MatchRow(row, lambda *a: None)
        cell = w._rank_cell()
        texts = [c.text() for c in cell.findChildren(QLabel)]
        self.assertIn('1515.62', texts)
        self.assertNotIn('巅峰决斗', texts)      # 文字不再当 label（tooltip 才有）
        self.assertEqual(cell.toolTip(), '巅峰决斗 1515.62')
        # 有图标子件（pixmap 非空的 QLabel）
        self.assertTrue(any(not c.pixmap().isNull()
                            for c in cell.findChildren(QLabel)
                            if c.pixmap() is not None))

    def test_rank_cell_without_peak_keeps_empty(self):
        """没积分的巅峰行段位格保持空（原样，不画「巅峰决斗」文字）。"""
        from PySide6.QtWidgets import QLabel
        from ui.dashboard import MatchRow
        row = self.ui_state._page_row(_base_row())
        cell = MatchRow(row, lambda *a: None)._rank_cell()
        texts = [c.text() for c in cell.findChildren(QLabel)]
        self.assertNotIn('巅峰决斗', texts)
        self.assertIsNone(cell.toolTip() or None)


if __name__ == '__main__':
    unittest.main()
