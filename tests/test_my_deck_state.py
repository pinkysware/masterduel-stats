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
    """假 reader：`scan()` 全扫、`read_at()` 便宜读（可分别设返回值）。"""

    def __init__(self, deck=None, addr=0x200000):
        self.deck, self.addr = deck, addr
        self.scans = 0
        self.reads = 0

    def scan(self):
        self.scans += 1
        return (self.deck, self.addr) if self.deck else (None, None)

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
        reader.deck = None                              # 实例没了
        self.assertIsNone(w.poll(now=1002.0))
        self.assertIsNone(w.poll(now=1100.0))           # 还没到 120s
        self.assertEqual(reader.scans, 1)               # 只扫过最初那一次
        reader.deck = _deck([4007] * 41)
        w.poll(now=1200.0)                              # 到点了 -> 再扫
        self.assertEqual(reader.scans, 2)


if __name__ == '__main__':
    unittest.main()
