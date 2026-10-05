# -*- coding: utf-8 -*-
"""`core/memread/rank.py` 的**纯逻辑**单测（不需要游戏、不碰进程）。

两块：

* `DecodeProfileTest` —— **现役**：`User.profile` 的原始值 -> 段位 dict 的判据
  （实机那次读到的就是 `rank=5 / rate=2` = 白金2）；
* `LegacyRecordTest` —— **考古**：上一轮「按值域扫那条记录」的判据还在，
  但它已被复核否掉（同地址复读成 `[5,1600,1]`），只留着当证据。

活内存那半由 `tools/capture_my_rank.py` 在实机上读（主界面就有效）。
"""
import os
import struct
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.memread import rank as rank_mod                        # noqa: E402


class DecodeProfileTest(unittest.TestCase):
    """现役判据：`rank` ∈ 1..7 且 `rate` ∈ 1..5，缺一不可。"""

    def test_real_sample_decodes(self):
        """实机 `User.profile` 那次（白金2）—— 逐项对上用户界面。"""
        got = rank_mod.decode_profile({'name': '示例玩家', 'rank': 5, 'rate': 2,
                                       'level': 36, 'pcode': 123456789})
        self.assertEqual(got['rank'], 5)
        self.assertEqual(got['rate'], 2)
        self.assertEqual(got['name'], '示例玩家')
        self.assertEqual(got['level'], 36)
        self.assertEqual(got['pcode'], 123456789)

    def test_only_rank_and_rate_are_required(self):
        """没有 name/level/pcode 也算数（只补已有的键，不塞 None）。"""
        got = rank_mod.decode_profile({'rank': 7, 'rate': 1})
        self.assertEqual(got, {'rank': 7, 'rate': 1})

    def test_missing_key_is_rejected(self):
        for values in ({}, None, {'rank': 5}, {'rate': 2}, {'name': 'x'}):
            self.assertIsNone(rank_mod.decode_profile(values), values)

    def test_out_of_range_is_rejected(self):
        for rank in (0, 8, 99, -1):
            self.assertIsNone(rank_mod.decode_profile({'rank': rank, 'rate': 2}), rank)
        for rate in (0, 6, 99, -2):
            self.assertIsNone(rank_mod.decode_profile({'rank': 5, 'rate': rate}), rate)

    def test_string_numbers_are_accepted(self):
        """`rank` 走的是装箱整数，但真机上也见过字符串形态 —— 两位数能转就转。"""
        got = rank_mod.decode_profile({'rank': '5', 'rate': '2'})
        self.assertEqual((got['rank'], got['rate']), (5, 2))

    def test_garbage_is_rejected(self):
        for bad in ({'rank': 'Platinum', 'rate': 2}, {'rank': 5, 'rate': None},
                    {'rank': None, 'rate': 2}, {'rank': [], 'rate': 2}):
            self.assertIsNone(rank_mod.decode_profile(bad), bad)

    def test_edges(self):
        self.assertEqual(rank_mod.decode_profile({'rank': 1, 'rate': 5}),
                         {'rank': 1, 'rate': 5})
        self.assertEqual(rank_mod.decode_profile({'rank': 7, 'rate': 1}),
                         {'rank': 7, 'rate': 1})


class LegacyRecordTest(unittest.TestCase):
    """考古区：上一轮那条「记录」的判据（**已否掉，别接线**）。

    ⚠️ 它现在只证明「当年的判据长什么样」，**不代表段位读法** ——
    段位读法见 `DecodeProfileTest` / `read_my_rank`。
    """

    class _Region(object):
        def __init__(self, base, size):
            self.base, self.size = base, size
            self.protect, self.type = 0x04, 0x20000

    class _Proc(object):
        def __init__(self, blob, base=0x1000):
            self.blob, self.base = blob, base

        def try_read(self, addr, size):
            off = addr - self.base
            if off < 0 or off + size > len(self.blob):
                return None
            return self.blob[off:off + size]

        def regions(self):
            return [LegacyRecordTest._Region(self.base, len(self.blob))]

    @staticmethod
    def _rec(r, t, s, tail=b'\x00' * 0x10):
        return struct.pack('<3i', r, t, s) + tail

    def test_real_shape_decodes(self):
        self.assertEqual(rank_mod.decode_record(self._rec(5, 2, 1)), (5, 2, 1))

    def test_rank_out_of_range_is_rejected(self):
        for bad in (0, 8, 99, -1):
            self.assertIsNone(rank_mod.decode_record(self._rec(bad, 2, 1)), bad)

    def test_tier_out_of_range_is_rejected(self):
        for bad in (0, 6, 99, -2):
            self.assertIsNone(rank_mod.decode_record(self._rec(5, bad, 1)), bad)

    def test_streak_out_of_range_is_rejected(self):
        self.assertIsNone(rank_mod.decode_record(self._rec(5, 2, 100)))
        self.assertIsNone(rank_mod.decode_record(self._rec(5, 2, -1)))

    def test_tail_must_be_zeros(self):
        """后面不是一片 0 就不是这条记录 —— 这是当年压噪声的关键判据。"""
        self.assertIsNone(rank_mod.decode_record(
            self._rec(5, 2, 1, tail=b'\x07\x00\x00\x00' * 4)))

    def test_short_buffer_is_rejected(self):
        self.assertIsNone(rank_mod.decode_record(b'\x05\x00\x00\x00'))
        self.assertIsNone(rank_mod.decode_record(b''))

    def test_master_and_rookie_edges(self):
        self.assertEqual(rank_mod.decode_record(self._rec(7, 1, 0)), (7, 1, 0))
        self.assertEqual(rank_mod.decode_record(self._rec(1, 5, 12)), (1, 5, 12))

    def test_todays_read_is_rejected(self):
        """★ 否掉的证据：2026-09-15 深夜同地址复读成 `[5, 1600, 1]`。

        `1600` 超出小段值域 → 判据自己就否了它 = 当年那个命中是巧合。
        """
        self.assertIsNone(rank_mod.decode_record(
            self._rec(5, 1600, 1)))

    def test_finds_the_record_in_a_synthetic_blob(self):
        # ⚠️ 前面**要有非零数据**（实机那条记录前面是 `[1863,1542,746,0]`）
        blob = b'\xAA' * 32 + self._rec(5, 2, 1) + b'\x00' * 16
        self.assertEqual(rank_mod.find_candidates(self._Proc(blob)),
                         [self._Proc(blob).base + 32])

    def test_no_record_returns_empty(self):
        self.assertEqual(rank_mod.find_candidates(self._Proc(b'\x11' * 64)), [])

    def test_shifted_window_is_merged(self):
        """错位 4 字节的窗口会被一起匹配 → 簇合并只留最早那个（记录真正的起点）。"""
        blob = b'\xAA' * 32 + self._rec(5, 2, 1) + b'\x00' * 16
        self.assertEqual(rank_mod.find_candidates(self._Proc(blob)),
                         [self._Proc(blob).base + 32])


if __name__ == '__main__':
    unittest.main()
