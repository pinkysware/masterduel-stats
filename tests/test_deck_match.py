# -*- coding: utf-8 -*-
"""core/deck_match 的单测 —— 纯逻辑，不需要数据库。

覆盖：归一化（全角/大小写/标点）、四级匹配、多字段取最优、别名拆分。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.deck_match import (normalize, match_score, best_score,  # noqa: E402
                             split_aliases)


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_strip_punct(self):
        self.assertEqual(normalize('Pumpking Zombies'), 'pumpkingzombies')
        self.assertEqual(normalize('R.B.'), 'rb')
        self.assertEqual(normalize('Gravekeeper\'s'), 'gravekeepers')
        self.assertEqual(normalize('Snake-Eye  Yummy'), 'snakeeyeyummy')

    def test_fullwidth_to_halfwidth(self):
        """NFKC：全角字母数字先转半角，再去标点。"""
        self.assertEqual(normalize('Ｒ．Ｂ．'), 'rb')
        self.assertEqual(normalize('Ｔier１'), 'tier1')

    def test_chinese_kept(self):
        self.assertEqual(normalize('南瓜王'), '南瓜王')
        self.assertEqual(normalize('不 死 均'), '不死均')

    def test_empty(self):
        self.assertEqual(normalize(None), '')
        self.assertEqual(normalize(''), '')
        self.assertEqual(normalize('   '), '')


class TestMatchScore(unittest.TestCase):
    TEXT = 'Pumpking Zombies'

    def test_exact(self):
        self.assertEqual(match_score('pumpking zombies', self.TEXT), 0)
        self.assertEqual(match_score('PumpkingZombies', self.TEXT), 0)

    def test_prefix(self):
        self.assertEqual(match_score('pump', self.TEXT), 1)

    def test_substring(self):
        self.assertEqual(match_score('zomb', self.TEXT), 2)

    def test_fuzzy(self):
        """漏字 / 拼错也要能命中（等级 3）。"""
        self.assertEqual(match_score('pumpkinzombi', self.TEXT), 3)
        self.assertEqual(match_score('pumpkingzombis', self.TEXT), 3)

    def test_reordered_abbreviation(self):
        """★ 换序也要命中 —— difflib 是按顺序比的，'vsk9' 对 'k9vs' 只有 0.50。"""
        self.assertEqual(match_score('VSK9', 'K9VS'), 4)
        self.assertEqual(match_score('vsk9', 'k9vs'), 4)
        self.assertEqual(match_score('vsk9', 'Vanquish Soul K9'), 4)

    def test_chinese_reorder(self):
        self.assertEqual(match_score('圣耀', '烙印耀圣'), 4)
        self.assertEqual(match_score('耀圣烙', '烙印耀圣'), 4)

    def test_subset_needs_all_chars(self):
        """少一个字就不算第 4 级 —— 否则会到处误命中。

        注意区分：'烙印耀圣甲' 多一个字，会落到**第 3 级近似**（ratio 0.888），
        而不是第 4 级换序 —— 这行为是对的。
        """
        self.assertIsNone(match_score('珠泪', '泪冠哀歌'))
        self.assertEqual(match_score('烙印耀圣甲', '烙印耀圣'), 3)

    def test_reorder_is_lowest_priority(self):
        """换序只能排最后；顺序正确的应该赢。"""
        self.assertLess(match_score('k9vs', 'k9vs'), match_score('vsk9', 'k9vs'))
        self.assertLess(match_score('烙印', '烙印耀圣'), match_score('印烙', '烙印耀圣'))

    def test_single_char_does_not_use_reorder(self):
        """一个字符不走第 4 级（MIN_ANAGRAM_LEN=2），免得到处命中。"""
        self.assertIsNone(match_score('x', 'yz'))

    def test_no_match(self):
        self.assertIsNone(match_score('蓝眼', self.TEXT))
        self.assertIsNone(match_score('xyz', self.TEXT))

    def test_empty_query(self):
        self.assertIsNone(match_score('', self.TEXT))
        self.assertIsNone(match_score('   ', self.TEXT))

    def test_chinese_exact_and_substring(self):
        self.assertEqual(match_score('南瓜王', '南瓜王'), 0)
        self.assertEqual(match_score('南瓜', '南瓜王'), 1)
        self.assertEqual(match_score('瓜王', '南瓜王'), 2)


class TestBestScore(unittest.TestCase):
    def test_takes_best_across_fields(self):
        """别名命中应该和正式名一样算数。"""
        fields = ['Pumpking Zombies', None, '南瓜王', 'Zombies', '不死均']
        self.assertEqual(best_score('不死均', fields), 0)
        self.assertEqual(best_score('pumpking', fields), 1)
        self.assertIsNone(best_score('完全没有的东西', fields))

    def test_ignores_none_and_empty(self):
        self.assertEqual(best_score('南瓜王', [None, '', '南瓜王']), 0)
        self.assertIsNone(best_score('南瓜王', [None, '']))
        self.assertIsNone(best_score('南瓜王', None))


class TestSplitAliases(unittest.TestCase):
    def test_separators(self):
        self.assertEqual(split_aliases('Zombies / 不死均'), ['Zombies', '不死均'])
        self.assertEqual(split_aliases('A、B、C'), ['A', 'B', 'C'])
        self.assertEqual(split_aliases('A,B'), ['A', 'B'])
        self.assertEqual(split_aliases('A|B;C'), ['A', 'B', 'C'])

    def test_strips_and_drops_empty(self):
        self.assertEqual(split_aliases('  A /  / B  '), ['A', 'B'])

    def test_empty(self):
        self.assertEqual(split_aliases(''), [])
        self.assertEqual(split_aliases(None), [])


if __name__ == '__main__':
    unittest.main()
