# -*- coding: utf-8 -*-
"""DC 杯 DP 的 UI 单测（2026-10-23）：

* `_page_row` 带 `cup_dp`：只有 **DC 杯 + 2 阶段** + 有值时才给；
* after 优先 / before 兜底；1 阶段恒 None（那是 DLv 不是 DP）；
* 看板段位列画「DP 1000」（**没有图标** —— 用户 2026-09-14 明令 DC 杯不配图标）。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')   # 必须在 Qt import 之前


def _base_row(**kw):
    m = {'match_id': 'c1', 'started_at': '2026-10-23 12:29:00.000',
         'game_mode': 12, 'result': 'Win'}
    m.update(kw)
    return m


class TestPageRowCupDp(unittest.TestCase):

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def test_after_preferred(self):
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=1000, cup_stage=2))
        self.assertEqual(row['cup_dp'], '1000')
        self.assertEqual(row['mode_group'], 'dc')

    def test_before_fallback(self):
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=500, cup_stage=2))
        self.assertEqual(row['cup_dp'], '500')

    def test_stage1_none(self):
        """★ 1 阶段打的是 DLv —— 不许画成 DP。"""
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=20, cup_stage=1))
        self.assertIsNone(row['cup_dp'])

    def test_no_stage_none(self):
        row = self.ui_state._page_row(
            _base_row(cup_dp_after=1000))
        self.assertIsNone(row['cup_dp'])

    def test_no_values_none(self):
        row = self.ui_state._page_row(_base_row(cup_stage=2))
        self.assertIsNone(row['cup_dp'])

    def test_ranked_row_not_dc(self):
        """天梯行的 cup_* 不该被消费（模式列不是 dc）。"""
        row = self.ui_state._page_row(
            _base_row(game_mode=3, cup_dp_after=1000, cup_stage=2))
        self.assertIsNone(row['cup_dp'])

    def test_zero_dp_renders(self):
        """★ DP 0 是合法值（2 阶段刚开）—— 不能当「没有」。"""
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=0, cup_stage=2))
        self.assertEqual(row['cup_dp'], '0')


class TestDashboardRankCellCup(unittest.TestCase):
    """`_rank_cell` 消费 cup_dp：画「DP 1000」，不配图标。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from core import ui_state
        self.ui_state = ui_state

    def test_rank_cell_renders_dp_text(self):
        from PySide6.QtWidgets import QLabel
        from ui.dashboard import MatchRow
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=1000, cup_stage=2))
        w = MatchRow(row, lambda *a: None)
        cell = w._rank_cell()
        texts = [c.text() for c in cell.findChildren(QLabel)]
        # 列宽会把长文本 elide，所以按前缀断言
        self.assertTrue(any(t.startswith('DP') for t in texts), texts)
        self.assertIn('DC杯', cell.toolTip())

    def test_stage1_shows_icon_but_no_score(self):
        """★ 用户 2026-10-23：「现在**统一显示 DC 图标**，一阶段**不显示分数和 DP**」。

        1 阶段打的是 DLv（LV1~LV20），不是积分 —— 那格只有图标，没有数字。
        判据是 `is_dc`（按模式，不分阶段），**不是** `cup_dp`（只有 2 阶段有值）。
        """
        from PySide6.QtWidgets import QLabel
        from ui import dashboard
        row = self.ui_state._page_row(_base_row(cup_stage=1))
        self.assertTrue(row.get('is_dc'))
        self.assertIsNone(row.get('cup_dp'))          # 1 阶段没有积分
        cell = dashboard.MatchRow(row, lambda *a: None)._rank_cell()
        icons = [l for l in cell.findChildren(QLabel)
                 if l.pixmap() is not None and not l.pixmap().isNull()]
        texts = [l.text() for l in cell.findChildren(QLabel) if l.text()]
        self.assertTrue(icons, '1 阶段也该有 DC 图标')
        self.assertEqual(texts, [], '1 阶段不该显示分数或 DP')
        self.assertIn('1 阶段', cell.toolTip())

    def test_stage2_shows_icon_and_score(self):
        """2 阶段：图标 + 分数 + DP（对照上一条）。"""
        from PySide6.QtWidgets import QLabel
        from ui import dashboard
        row = self.ui_state._page_row(
            _base_row(cup_stage=2, cup_dp_after=2048))
        self.assertTrue(row.get('is_dc'))
        cell = dashboard.MatchRow(row, lambda *a: None)._rank_cell()
        texts = [l.text() for l in cell.findChildren(QLabel) if l.text()]
        self.assertTrue(any(t.startswith('2') for t in texts), texts)
        self.assertIn('DP', texts)

    def test_non_dc_row_has_no_is_dc(self):
        row = self.ui_state._page_row(_base_row(game_mode=3))
        self.assertFalse(row.get('is_dc'))

    def test_order_is_icon_score_unit(self):
        """★ 用户 2026-10-23：「应该是 **图标-分数-DP** 这样写，而不是 图标-DP-分数」。"""
        from PySide6.QtWidgets import QLabel
        from ui import dashboard
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=2048, cup_stage=2))
        self.assertEqual(row['cup_dp'], '2048')       # 纯分数
        self.assertEqual(row['cup_dp_unit'], 'DP')    # 单位另给
        cell = dashboard.MatchRow(row, lambda *a: None)._rank_cell()
        seq = []
        for l in cell.findChildren(QLabel):
            pm = l.pixmap()
            if pm is not None and not pm.isNull():
                seq.append('<icon>')
            elif l.text():
                seq.append(l.text())
        #: 图标在最前，`DP` 在最后（中间是分数，列窄时会被 elide 成 '2…'）
        self.assertEqual(seq[0], '<icon>')
        self.assertEqual(seq[-1], 'DP')
        self.assertEqual(len(seq), 3)

    def test_rank_cell_icon_is_transparent_and_fitted(self):
        """★ 用户 2026-10-23 两条实测：图标**不能有灰底**、**不能太小**。

        天梯段位图标是透明底的；模式图标也必须透明（原来是 `SURFACE2` 灰底）。
        并且要**按比例缩放不裁切** —— DC 是 2:1 宽扁图，居中裁方会砍掉左右。
        """
        from PySide6.QtWidgets import QLabel
        from ui import dashboard, theme
        row = self.ui_state._page_row(
            _base_row(cup_dp_before=0, cup_dp_after=2048, cup_stage=2))
        cell = dashboard.MatchRow(row, lambda *a: None)._rank_cell()
        icons = [l for l in cell.findChildren(QLabel)
                 if l.pixmap() is not None and not l.pixmap().isNull()]
        self.assertTrue(icons, 'DC 行该有图标')
        lab = icons[0]
        self.assertIn('transparent', lab.styleSheet())
        self.assertNotIn('SURFACE2', lab.styleSheet())
        #: 尺寸跟着 theme 走，不许退回 18（用户嫌小）
        self.assertGreaterEqual(theme.RANK_ICON_ROW, 22)
        self.assertEqual(lab.width(), theme.RANK_ICON_ROW)

    def test_rank_cell_without_cup_dp_keeps_empty(self):
        from PySide6.QtWidgets import QLabel
        from ui.dashboard import MatchRow
        row = self.ui_state._page_row(_base_row())
        w = MatchRow(row, lambda *a: None)
        cell = w._rank_cell()
        texts = [c.text() for c in cell.findChildren(QLabel)]
        self.assertFalse(any(t.startswith('DP') for t in texts), texts)


class TestFloatingBadgeCentering(unittest.TestCase):
    """悬浮窗段位格：**只有图标时与表头「段位」居中**（用户 2026-10-23）。

    原来是内容组满宽 + 内容贴左，只有图标时图标被推到左边，与表头不对齐。
    现在：**组宽不变**（保「天梯行图标跨行对齐」），但在组内给图标两侧
    各垫一个 stretch，于是图标落在列中央。真实渲染断言见
    `tests/test_ui_floating.BadgeAlignTest.test_icon_only_rows_are_centered_on_the_header`。
    """

    def setUp(self):
        from core import ui_state
        from ui import floating
        self.ui_state = ui_state
        self.floating = floating

    def _row(self, **kw):
        m = {'match_id': 'f1', 'started_at': '2026-10-23 12:29:00',
             'result': 'Win'}
        m.update(kw)
        return self.ui_state._recent_row(m, None, _FakeDecks)

    def test_dc_row_has_icon_and_no_numeral(self):
        """DC 2 阶段：有图标、**没有小段位**（所以走"居中"分支）。"""
        r = self._row(game_mode=12, cup_stage=2, cup_dp_after=2048)
        self.assertTrue(r.get('badge_icon'))
        self.assertFalse(r.get('badge_numeral'))

    def test_peak_row_has_icon_and_no_numeral(self):
        r = self._row(game_mode=19, peak_rate_after=1515.62)
        self.assertTrue(r.get('badge_icon'))
        self.assertFalse(r.get('badge_numeral'))

    def test_ladder_row_has_numeral(self):
        """天梯带罗马数字 -> 不居中（继续贴组左缘、跨行对齐）。"""
        r = self._row(game_mode=3, my_rank_before=7, my_rate_before=4,
                      my_rank_after=7, my_rate_after=4)
        self.assertIn(r.get('badge_numeral'), ('I', 'II', 'III', 'IV', 'V'))


class _FakeDecks(object):
    @staticmethod
    def resolve(name):
        return None


if __name__ == '__main__':
    unittest.main()
