# -*- coding: utf-8 -*-
"""core/ranks.py 单测（M7）—— 段位映射、标签、图标、升降段比较。

⚠️ 特别守住「不许编」：认不出段位时 `rank_label()` 返回**空串**、
`icon_path()` 返回 **None**，都不许猜一个出来。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import ranks                                               # noqa: E402


class MappingTest(unittest.TestCase):

    def test_names_match_the_reference_table(self):
        """映射表来自 untapped 前端代码 —— 抄错了会立刻红。"""
        self.assertEqual(ranks.RANK_NAMES, {
            1: 'Rookie', 2: 'Bronze', 3: 'Silver', 4: 'Gold',
            5: 'Platinum', 6: 'Diamond', 7: 'Master'})

    def test_our_db_value_5_is_platinum(self):
        """★ 与建表注释交叉验证：库里 `my_rank_before=5` 必须解释成 Platinum。"""
        self.assertEqual(ranks.rank_name(5), 'Platinum')
        self.assertEqual(ranks.rank_name(6), 'Diamond')
        self.assertEqual(ranks.rank_name(7), 'Master')

    def test_order_is_low_to_high(self):
        self.assertEqual(ranks.RANK_ORDER[0], 'Rookie')
        self.assertEqual(ranks.RANK_ORDER[-1], 'Master')
        self.assertEqual(len(ranks.RANK_ORDER), 7)

    def test_unknown_rank_returns_none_not_a_guess(self):
        for bad in (None, 0, 8, 99, 'abc', ''):
            self.assertIsNone(ranks.rank_name(bad), bad)


class LabelTest(unittest.TestCase):

    def test_label_with_sub_rank(self):
        self.assertEqual(ranks.rank_label(5, 2), 'Platinum 2')
        self.assertEqual(ranks.rank_label(2, 5), 'Bronze 5')

    def test_label_without_sub_rank(self):
        self.assertEqual(ranks.rank_label(5, None), 'Platinum')
        self.assertEqual(ranks.rank_label(5, 0), 'Platinum')     # 越界也退回名字
        self.assertEqual(ranks.rank_label(5, 9), 'Platinum')

    def test_unknown_gives_empty_string(self):
        """★ 认不出就**空着**，不要写 '?' 或编一个 —— 界面那一格该是空的。"""
        self.assertEqual(ranks.rank_label(None, 2), '')
        self.assertEqual(ranks.rank_label(99, 2), '')


class IconTest(unittest.TestCase):

    def test_all_seven_icons_exist(self):
        for rank in sorted(ranks.RANK_NAMES):
            p = ranks.icon_path(rank)
            self.assertIsNotNone(p, '第 %d 段没有图标' % rank)
            self.assertTrue(os.path.exists(os.path.join(PROJ, p)), p)
            self.assertTrue(os.path.getsize(os.path.join(PROJ, p)) > 1000, p)
            self.assertEqual(os.path.basename(p), ranks.RANK_NAMES[rank] + '.webp')

    def test_path_is_relative_with_forward_slash(self):
        p = ranks.icon_path(5)
        self.assertEqual(p, 'data/rank_icons/Platinum.webp')

    def test_unknown_rank_gives_no_icon(self):
        """★ 不回落默认图标 —— 显示错段位比不显示更糟。"""
        self.assertIsNone(ranks.icon_path(None))
        self.assertIsNone(ranks.icon_path(99))


class CompareTest(unittest.TestCase):
    """升降段比较。方向依据：库里 3 次真实升段 5-5→5-4→5-3→5-2。"""

    def test_promotion_within_same_rank_is_up(self):
        """★ 段内小段**变小 = 升段**（真实样本反推的）。"""
        self.assertEqual(ranks.compare((5, 5), (5, 4)), 'up')
        self.assertEqual(ranks.compare((5, 4), (5, 3)), 'up')
        self.assertEqual(ranks.compare((5, 3), (5, 2)), 'up')

    def test_promotion_across_rank_is_up(self):
        self.assertEqual(ranks.compare((5, 1), (6, 5)), 'up')

    def test_demotion(self):
        self.assertEqual(ranks.compare((5, 2), (5, 3)), 'down')
        self.assertEqual(ranks.compare((6, 5), (5, 1)), 'down')

    def test_same(self):
        self.assertEqual(ranks.compare((5, 2), (5, 2)), 'same')

    def test_uncomparable_returns_none(self):
        """最后一局没有 after、或字段缺失 → None（不画箭头）。"""
        self.assertIsNone(ranks.compare((5, 2), (None, None)))
        self.assertIsNone(ranks.compare((None, None), (5, 2)))

    def test_missing_sub_rank_still_comparable(self):
        """只有大段也能比（小段缺失按 0 处理，不影响大段升降判断）。"""
        self.assertEqual(ranks.compare((5, None), (6, None)), 'up')
        self.assertEqual(ranks.compare((5, None), (5, None)), 'same')


if __name__ == '__main__':
    unittest.main()


class DisplayRateTest(unittest.TestCase):
    """段位显示换算（2026-10-03 用户：「以游戏内为准」）。

    实测样本（09-28）：大师段 rate 1 游戏显示 Ⅱ、rate 2 显示 Ⅲ ——
    大师段显示 = 内部值 + 1；白金按内部值直显（5-2 = 游戏内白金 II）。
    """

    def test_master_offset_plus_one(self):
        from core import ranks
        self.assertEqual(ranks.rank_label(7, 1), 'Master 2')
        self.assertEqual(ranks.rank_label(7, 2), 'Master 3')
        self.assertEqual(ranks.numeral_text(1, 7), 'II')
        self.assertEqual(ranks.numeral_text(2, 7), 'III')

    def test_other_ranks_unchanged(self):
        from core import ranks
        self.assertEqual(ranks.rank_label(5, 2), 'Platinum 2')   # 白金实证
        self.assertEqual(ranks.rank_label(6, 4), 'Diamond 4')
        self.assertEqual(ranks.numeral_text(2, 5), 'II')

    def test_master_top_capped_at_v(self):
        from core import ranks
        self.assertEqual(ranks.display_rate(7, 5), 5)
        self.assertEqual(ranks.display_rate(7, 4), 5)

    def test_garbage_passthrough(self):
        from core import ranks
        self.assertIsNone(ranks.display_rate(7, None))
        self.assertEqual(ranks.display_rate(7, 'x'), 'x')
