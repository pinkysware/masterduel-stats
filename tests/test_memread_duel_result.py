# -*- coding: utf-8 -*-
"""`core/memread/duel_result.py` 的**纯逻辑**单测（不碰进程）。

两个口径都要盯住：
* **结算码** `1 = 胜 / 2 = 负`（与 `DuelHistory[i].res` 同口径）—— 认不出**必须 None**，
  绝不硬猜（写错胜负比空着更糟，见模块头）；
* **段位对** `{'rank','rate'}` 值域 1..7 / 1..5（与 `core.ranks` 同口径）。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.memread import duel_result as dr                         # noqa: E402


class DecodeResultCodeTest(unittest.TestCase):

    def test_win_and_lose(self):
        self.assertEqual(dr.decode_result_code(1), 'Win')
        self.assertEqual(dr.decode_result_code(2), 'Lose')

    def test_string_numbers(self):
        """真机上见过字符串形态的装箱值。"""
        self.assertEqual(dr.decode_result_code('1'), 'Win')

    def test_unknown_is_none(self):
        """⚠️ 关键：认不出**必须 None**（0/3/99/空 都不猜）。"""
        for bad in (0, 3, 99, -1, None, '', 'win', [], {}):
            self.assertIsNone(dr.decode_result_code(bad), bad)


class DecodeRankPairTest(unittest.TestCase):

    def test_real_sample(self):
        """实机 `Duel.rank[i]`（升段前）：白金2。"""
        self.assertEqual(dr.decode_rank_pair({'rank': 5, 'rate': 2}),
                         {'rank': 5, 'rate': 2})

    def test_out_of_range_is_none(self):
        for bad in ({'rank': 0, 'rate': 2}, {'rank': 8, 'rate': 2},
                    {'rank': 5, 'rate': 0}, {'rank': 5, 'rate': 6}):
            self.assertIsNone(dr.decode_rank_pair(bad), bad)

    def test_missing_or_garbage_is_none(self):
        for bad in ({}, None, {'rank': 5}, {'rate': 2},
                    {'rank': 'Platinum', 'rate': 2}, {'rank': [], 'rate': 2}):
            self.assertIsNone(dr.decode_rank_pair(bad), bad)

    def test_string_numbers_accepted(self):
        self.assertEqual(dr.decode_rank_pair({'rank': '5', 'rate': '1'}),
                         {'rank': 5, 'rate': 1})


class FirstRankPairTest(unittest.TestCase):

    def test_takes_first_valid(self):
        items = [None, {}, {'rank': 5, 'rate': 1}, {'rank': 6, 'rate': 3}]
        self.assertEqual(dr.first_rank_pair(items), {'rank': 5, 'rate': 1})

    def test_empty(self):
        self.assertIsNone(dr.first_rank_pair([]))
        self.assertIsNone(dr.first_rank_pair(None))


class ConstantsTest(unittest.TestCase):

    def test_codes_match_history_convention(self):
        """口径锚：`DuelHistory[i].res` 与真库逐条对上过（1 胜 / 2 负）。"""
        self.assertEqual((dr.RESULT_WIN, dr.RESULT_LOSE), (1, 2))


if __name__ == '__main__':
    unittest.main()
