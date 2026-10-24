# -*- coding: utf-8 -*-
"""`core/my_deck.py`（当前卡组状态）+ `core/memread/my_deck_watch.py`（观察者）的单测。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import my_deck                                                 # noqa: E402
from core.memread import my_deck_watch as mw                             # noqa: E402


class TestFingerprint(unittest.TestCase):

    def test_order_and_dupes_do_not_matter(self):
        """换了出牌顺序 / 份数不算「换牌」—— 指纹按**排序**后的 id 算。"""
        self.assertEqual(my_deck.fingerprint([1, 2, 3]),
                         my_deck.fingerprint([3, 1, 2]))
        self.assertEqual(my_deck.fingerprint([4007, 4007, 8933]),
                         my_deck.fingerprint([4007, 8933]))

    def test_different_cards_differ(self):
        self.assertNotEqual(my_deck.fingerprint([1, 2]), my_deck.fingerprint([1, 2, 3]))

    def test_empty_is_stable(self):
        self.assertEqual(my_deck.fingerprint([]), my_deck.fingerprint(None))


class TestStateFile(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, 'my_deck.json')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_save_load_roundtrip(self):
        self.assertTrue(my_deck.is_first_use(self.path))
        my_deck.save('Blue-Eyes', ids=[4007, 8933], src='m5-guess', path=self.path)
        got = my_deck.load(self.path)
        self.assertEqual(got['name'], 'Blue-Eyes')
        self.assertEqual(got['src'], 'm5-guess')
        self.assertEqual(got['card_count'], 2)
        self.assertEqual(got['ids_fingerprint'], my_deck.fingerprint([4007, 8933]))
        self.assertFalse(my_deck.is_first_use(self.path))
        self.assertEqual(my_deck.current_name(self.path), 'Blue-Eyes')

    def test_broken_file_is_empty_not_crash(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{不是 JSON')
        self.assertEqual(my_deck.load(self.path), {})
        self.assertTrue(my_deck.is_first_use(self.path))


class _StubReader(object):
    """假 reader：`scan()` 全扫、`read_at()` 便宜读（可分别设返回值）。

    ``state`` 模拟三态口（``'ok' / 'error' / 'gone'``，见 BUG-B3-14）；
    设成 None 时走**旧式单值口**（只实现 ``read_at`` 的 reader）。
    """

    def __init__(self, deck=None, addr=0x200000, state=None):
        self.deck, self.addr = deck, addr
        self.state = state
        self.scans = 0
        self.reads = 0

    def scan(self):
        self.scans += 1
        return (self.deck, self.addr) if self.deck else (None, None)

    def _resolved_state(self):
        if self.state is not None:
            return self.state
        return 'ok' if self.deck is not None else 'gone'

    def read_at_state(self, addr):
        self.reads += 1
        if self._resolved_state() == 'ok' and self.deck is None:
            return None, 'error'
        return self.deck, self._resolved_state()

    def read_at(self, addr):
        self.reads += 1
        return self.deck


def _deck(ids):
    return {'ids': ids, 'main': ids, 'extra': [], 'container': 'DeckEdit'}


class TestWatcher(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, 'my_deck.json')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _watch(self, reader, identify=None, rescan=120.0):
        return mw.MyDeckWatcher(reader, state_path=self.path,
                                identify=identify or (lambda ids: 'Blue-Eyes'),
                                rescan_sec=rescan)

    def test_first_scan_records_deck(self):
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader)
        got = w.poll(now=1000.0)
        self.assertEqual(got['name'], 'Blue-Eyes')
        self.assertEqual(reader.scans, 1)
        self.assertEqual(my_deck.current_name(self.path), 'Blue-Eyes')

    def test_same_deck_is_not_reidentified(self):
        """同一套牌反复进出编辑界面 -> 不重复识别、不写状态。"""
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader, identify=lambda ids: 'A')
        w.poll(now=1000.0)
        # 便宜路径（缓存实例）连读两次，指纹没变
        self.assertIsNone(w.poll(now=1002.0))
        self.assertIsNone(w.poll(now=1004.0))
        self.assertEqual(reader.reads, 2)
        self.assertEqual(reader.scans, 1)

    def test_changed_cards_reidentify(self):
        """★ 用户口径：进了编辑界面**换牌了** -> 再识别一次。"""
        reader = _StubReader(_deck([4007] * 41))
        names = iter(['Blue-Eyes', 'Sky Striker'])
        w = self._watch(reader, identify=lambda ids: next(names))
        w.poll(now=1000.0)
        reader.deck = _deck([4007] * 40 + [9999])       # 换了一张
        got = w.poll(now=1002.0)
        self.assertEqual(got['name'], 'Sky Striker')
        self.assertEqual(my_deck.current_name(self.path), 'Sky Striker')

    def test_unidentifiable_does_not_overwrite(self):
        reader = _StubReader(_deck([1] * 41))
        w = self._watch(reader, identify=lambda ids: 'Blue-Eyes')
        w.poll(now=1000.0)
        reader.deck = _deck([2] * 41)
        w2 = self._watch(reader, identify=lambda ids: None)
        self.assertIsNone(w2.poll(now=1002.0))
        self.assertEqual(my_deck.current_name(self.path), 'Blue-Eyes')

    def test_rescan_is_throttled_after_instance_dies(self):
        """离开编辑界面后**不许每拍全堆扫**：要等 RESCAN_SEC。"""
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader, rescan=120.0)
        w.poll(now=1000.0)
        reader.state = 'gone'                           # 实例真的没了
        reader.deck = None
        self.assertIsNone(w.poll(now=1002.0))
        self.assertIsNone(w.poll(now=1100.0))           # 还没到 120s
        self.assertEqual(reader.scans, 1)               # 只扫过最初那一次
        reader.state = None
        reader.deck = _deck([4007] * 41)
        w.poll(now=1200.0)                              # 到点了 -> 再扫
        self.assertEqual(reader.scans, 2)

    def test_transient_read_failure_keeps_instance(self):
        """★ BUG-B3-14：一次瞬时读失败**不许**让实例地址作废（120s 盲区）。"""
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader, rescan=120.0)
        self.assertEqual(w.poll(now=1000.0)['name'], 'Blue-Eyes')
        inst = w._inst
        reader.state = 'error'                          # 这一拍读失败
        self.assertIsNone(w.poll(now=1002.0))
        self.assertEqual(w._inst, inst, '读失败不该作废实例地址')
        # 下一拍照旧走便宜路径，**不扫**；读好了就接着用
        reader.state = None
        self.assertIsNone(w.poll(now=1004.0))           # 同一套牌 -> 不重复识别
        self.assertEqual(reader.scans, 1)
        self.assertEqual(w._inst, inst)

    def test_changed_cards_after_transient_failure_still_detected(self):
        """瞬时失败后用户真换了牌 -> 下一拍必须能识别到（不是等 120s）。"""
        reader = _StubReader(_deck([4007] * 41))
        names = iter(['Blue-Eyes', 'Sky Striker'])
        w = self._watch(reader, identify=lambda ids: next(names))
        w.poll(now=1000.0)
        reader.state = 'error'
        self.assertIsNone(w.poll(now=1002.0))
        reader.deck = _deck([4007] * 40 + [9999])
        reader.state = None
        got = w.poll(now=1004.0)
        self.assertEqual(got['name'], 'Sky Striker')
        self.assertEqual(reader.scans, 1)               # 一次全扫都没多花

    def test_peek_bypasses_fingerprint_shortcircuit(self):
        """巅峰快照语义：同一副牌 peek 也要返回值（poll 会被同指纹短路）。

        2026-10-24 实锤：大厅编辑过的牌已写 my_deck.json，巅峰编辑实例读到
        同一副 -> poll() 恒 None -> 巅峰局我方卡组整局空（3/3 局必现）。
        """
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader)
        self.assertIsNotNone(w.poll(now=1000.0))
        self.assertIsNone(w.poll(now=1002.0))           # 同指纹 -> poll 短路
        got = w.peek(now=1004.0)
        self.assertEqual(got['ids'], [4007] * 41)       # peek 不短路

    def test_peek_after_gone_rescans_with_throttle(self):
        """peek 的便宜路径/节流与 poll 同拍：gone 作废地址、120s 后才重扫。"""
        reader = _StubReader(_deck([4007] * 41))
        w = self._watch(reader)
        self.assertIsNotNone(w.poll(now=1000.0))
        reader.deck = None                              # 离开编辑界面
        self.assertIsNone(w.peek(now=1002.0))           # gone -> 作废 + 节流
        reader.deck = _deck([4007] * 41)
        self.assertIsNone(w.peek(now=1100.0))           # 还没到 120s
        self.assertIsNotNone(w.peek(now=1200.0))        # 到点重扫 -> 有值
        self.assertEqual(reader.scans, 2)


if __name__ == '__main__':
    unittest.main()
