# -*- coding: utf-8 -*-
"""`core/memread/deck_edit.py` 的单测。

真实内存没法在测试里造（要开游戏），所以：
* **纯函数** `parse_card_ids` 用合成字节测（含 28 字节步长、截断、值域过滤）；
* **字段偏移按名查**（BUG-B3-01 的核心）用 FakeMem 搭的假 TypeInfo 测；
* 找实例/读列表那两段用**打桩的 proc + monkeypatch** 测「筛非空实例、取字段多的 TI」。
"""

import os
import struct
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core.memread import deck_edit as de                              # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_named_typeinfo)      # noqa: E402


def _elem(card_id, num=1, ptr=0x00000007_6A41A2C1, tail=(4, 0), stride=28):
    """造一个 28 字节元素的原始字节（首 int32 = 卡 id，布局同实测）。"""
    b = struct.pack('<IIIQ', card_id, num, 1, ptr)
    for t in tail:
        b += struct.pack('<I', t)
    return b.ljust(stride, b'\x00')


class TestParseCardIds(unittest.TestCase):

    def test_stride_28_takes_first_int32(self):
        ids = [4007, 8933, 12292]
        raw = b''.join(_elem(i) for i in ids)
        self.assertEqual(de.parse_card_ids(raw, 3), ids)

    def test_tail_short_is_tolerated(self):
        """原始字节不够长（读窗口边界）-> 只解得出的部分，不抛。"""
        raw = _elem(4007) + b'\x01\x02'
        self.assertEqual(de.parse_card_ids(raw, 3), [4007])

    def test_zero_size(self):
        self.assertEqual(de.parse_card_ids(b'', 0), [])

    def test_stride_is_configurable(self):
        raw = struct.pack('<II', 4007, 8933)          # 4 字节步长
        self.assertEqual(de.parse_card_ids(raw, 2, stride=4), [4007, 8933])

    def test_out_of_range_ids_dropped(self):
        """★ BUG-B3-01：读飞时那串值多半不是卡 id —— 0 / 超过 0xFFFF 全丢。"""
        raw = b''.join(_elem(i) for i in (0, 4007, 0x10000, 0xFFFFFFFF, 23808))
        self.assertEqual(de.parse_card_ids(raw, 5), [4007, 23808])


class TestDeckFields(unittest.TestCase):
    """★ BUG-B3-01：偏移必须**按字段名**查，查不到抛 ValueError。"""

    def setUp(self):
        de.clear_fields_cache()
        self.addCleanup(de.clear_fields_cache)

    def _scene(self, fields):
        mem = FakeMem()
        ti = build_named_typeinfo(mem, de.CLASS_NAME, fields)
        return ObjProc(mem), ti

    def test_offsets_found_by_name(self):
        proc, ti = self._scene([
            ('m_MainDeckCards', 0x270), ('m_ExtraDeckCards', 0x278),
            ('<m_DeckID>k__BackingField', 0x2A8),
            ('<m_DeckName>k__BackingField', 0x2B0)])
        got = de.deck_fields(proc, None, ti)
        self.assertEqual(got, {'main': 0x270, 'extra': 0x278,
                               'deck_id': 0x2A8, 'deck_name': 0x2B0})

    def test_plain_property_names_also_accepted(self):
        """属性也可能直接以 m_DeckID / m_DeckName 出现（两种形态都认）。"""
        proc, ti = self._scene([
            ('m_MainDeckCards', 0x230), ('m_ExtraDeckCards', 0x238),
            ('m_DeckID', 0x260), ('m_DeckName', 0x268)])
        self.assertEqual(de.deck_fields(proc, None, ti),
                         {'main': 0x230, 'extra': 0x238,
                          'deck_id': 0x260, 'deck_name': 0x268})

    def test_missing_required_raises(self):
        """字段名查不到 = 偏移不猜 -> ValueError（绝不能退回某个数字）。"""
        proc, ti = self._scene([('m_MainDeckCards', 0x270)])
        with self.assertRaises(ValueError) as ctx:
            de.deck_fields(proc, None, ti)
        self.assertIn('偏移不猜', str(ctx.exception))

    def test_optional_fields_may_be_missing(self):
        """deck_id / deck_name 不是必填（卡表才是识别输入）。"""
        proc, ti = self._scene([('m_MainDeckCards', 0x270),
                                ('m_ExtraDeckCards', 0x278)])
        self.assertEqual(de.deck_fields(proc, None, ti),
                         {'main': 0x270, 'extra': 0x278})

    def test_cached_by_pid_and_typeinfo(self):
        """字段表按 (pid, TypeInfo) 缓存 —— 观察者每拍都会读。"""
        calls = []
        orig = de.list_fields
        de.list_fields = lambda p, ti, mirror=None: (calls.append(ti)
                                                     or orig(p, ti, mirror))
        try:
            mem = FakeMem()
            ti = build_named_typeinfo(mem, de.CLASS_NAME,
                                      [('m_MainDeckCards', 0x270),
                                       ('m_ExtraDeckCards', 0x278)])
            proc = ObjProc(mem)
            proc.pid = 4242
            de.deck_fields(proc, None, ti)
            de.deck_fields(proc, None, ti)
            self.assertEqual(calls, [ti])
        finally:
            de.list_fields = orig

    def test_no_hardcoded_offset_constants(self):
        """B3-01 的回归锁：模块里不许再出现 F_MAIN / F_EXTRA 这类写死偏移。"""
        for name in ('F_MAIN', 'F_EXTRA', 'F_DECK_ID', 'F_DECK_NAME'):
            self.assertFalse(hasattr(de, name), '%s 不该再存在' % name)


class _StubProc(object):
    """假进程：只实现 deck_edit 用到的那几个口。"""

    def __init__(self, pointers=(), u64=None, u32=None, blobs=None, reads=None):
        self._pointers = list(pointers)
        self._u64 = dict(u64 or {})
        self._u32 = dict(u32 or {})
        self._blobs = dict(blobs or {})
        self._reads = dict(reads or {})

    def scan(self, pattern, max_hits=None):
        return iter(self._pointers[:max_hits or len(self._pointers)])

    def read_u64(self, addr):
        return self._u64[addr]

    def read_u32(self, addr):
        return self._u32[addr]

    def read_ptr(self, addr):
        return self._u64[addr]

    def read(self, addr, size):
        return self._blobs.get(addr, b'\x00' * size)[:size]


class TestFindTypeinfo(unittest.TestCase):

    def test_picks_the_one_with_more_fields(self):
        """同类名两个 TypeInfo（176 字段真身 + 0 字段壳）-> 取字段多的。"""
        shell, real = 0x130000, 0x240000
        proc = _StubProc(pointers=[shell + 0x10, real + 0x10],
                         u64={shell + 0x10: 0xAA, real + 0x10: 0xAA})
        mirror = type('M', (), {'string_addr': staticmethod(lambda n, t: 0xAA)})()
        orig = de.list_fields
        de.list_fields = lambda p, ti, mirror=None: [1] * (176 if ti == real else 0)
        try:
            self.assertEqual(de.find_typeinfo(proc, mirror, b''), real)
        finally:
            de.list_fields = orig


class TestReadListIds(unittest.TestCase):

    def test_reads_inline_elements(self):
        obj, items = 0x150000, 0x260000
        raw = b''.join(_elem(i) for i in (4007, 8933))
        proc = _StubProc(u64={obj + 0x8: items}, u32={obj + 0x10: 2},
                         blobs={items + de.ARRAY_DATA_OFF: raw})
        orig = de._fields_of
        de._fields_of = lambda p, m, o: {'_items': 0x8, '_size': 0x10}
        try:
            self.assertEqual(de.read_list_ids(proc, None, obj), [4007, 8933])
        finally:
            de._fields_of = orig

    def test_not_a_list(self):
        orig = de._fields_of
        de._fields_of = lambda p, m, o: {'foo': 0x8}
        try:
            self.assertEqual(de.read_list_ids(_StubProc(), None, 0x150000), [])
        finally:
            de._fields_of = orig


class TestReadEditedDeck(unittest.TestCase):

    #: 测试里的字段偏移（**由名字查到** —— 这里只是被 monkeypatch 的返回值）
    FIELDS = {'main': 0x270, 'extra': 0x278,
              'deck_id': 0x2A8, 'deck_name': 0x2B0}

    def test_skips_empty_instance(self):
        """第一个实例的卡表是空的（壳）-> 继续找，返回**非空**的那个。"""
        empty, real = 0x170000, 0x280000
        proc = _StubProc(pointers=[empty, real],
                         u64={empty + self.FIELDS['deck_name']: 0x290000,
                              real + self.FIELDS['deck_name']: 0x290000},
                         u32={empty + self.FIELDS['deck_id']: 0,
                              real + self.FIELDS['deck_id']: 7})
        orig_ti, orig_list = de.find_typeinfo, de.read_list_ids
        orig_fields = de.deck_fields
        seen_ti = []

        def _fields(p, m, ti):
            seen_ti.append(ti)
            return dict(self.FIELDS)

        de.find_typeinfo = lambda *a, **kw: 0x1234
        de.deck_fields = _fields
        de.read_list_ids = (lambda p, m, o, **kw:
                            {0xA: [1, 2, 3], 0xB: [4007] * 41,
                             0xC: [8933] * 15}[o])
        proc._u64[empty + self.FIELDS['main']] = 0xA   # 空壳：只有 3 张 -> 被 MIN_MAIN 挡掉
        proc._u64[empty + self.FIELDS['extra']] = 0xA
        proc._u64[real + self.FIELDS['main']] = 0xB    # 真身：41 + 15
        proc._u64[real + self.FIELDS['extra']] = 0xC
        proc._u64[empty] = 0x1234                      # 对象头 = TypeInfo（校验要过）
        proc._u64[real] = 0x1234
        from core.memread import il2cpp_obj as io2
        orig_rs = io2.read_string
        io2.read_string = lambda p, a, n: 'Deck'
        try:
            got = de.read_edited_deck(proc, None, b'', max_hits=10)
            self.assertIsNotNone(got)
            self.assertEqual(got['container'], 'DeckEdit')
            self.assertEqual(got['deck_id'], 7)
            self.assertEqual(len(got['main']), 41)
            self.assertEqual(len(got['extra']), 15)
            self.assertEqual(len(got['ids']), 56)
            # 读实例必须**经字段名查偏移**（B3-01 的落地点）
            self.assertEqual(seen_ti, [0x1234, 0x1234])
        finally:
            de.find_typeinfo, de.read_list_ids = orig_ti, orig_list
            de.deck_fields = orig_fields
            io2.read_string = orig_rs

    def test_nothing_found_gives_none(self):
        orig = de.find_typeinfo
        de.find_typeinfo = lambda *a, **kw: None
        try:
            self.assertIsNone(de.read_edited_deck(_StubProc(), None, b''))
        finally:
            de.find_typeinfo = orig

    def test_missing_fields_give_none_not_wrong_deck(self):
        """字段名变了 -> read_instance 返回 None（绝不拿退回的偏移去读）。"""
        proc = _StubProc(u64={0x170000: 0x1234})
        orig = de.deck_fields

        def _boom(p, m, ti):
            raise ValueError('偏移不猜')

        de.deck_fields = _boom
        try:
            self.assertIsNone(de.read_instance(proc, None, 0x170000))
        finally:
            de.deck_fields = orig


class TestInstanceState(unittest.TestCase):
    """★ BUG-B3-14 的底座：对象头校验给三态（True / False / None）。"""

    def test_true_for_same_class(self):
        proc = _StubProc(u64={0x170000: 0x1234})
        self.assertIs(de.instance_state(proc, 0x170000, 0x1234), True)

    def test_false_for_other_class(self):
        proc = _StubProc(u64={0x170000: 0x9999})
        self.assertIs(de.instance_state(proc, 0x170000, 0x1234), False)

    def test_none_when_read_fails(self):
        """读不到 ≠ 不是本类实例 —— 这是 B3-14 那次 120 秒盲区的根因。"""
        self.assertIsNone(de.instance_state(_StubProc(), 0x170000, 0x1234))
        # 布尔口把「读不到」归到 False（扫描筛选的旧语义），三态口才分得开
        self.assertFalse(de.is_instance(_StubProc(), 0x170000, 0x1234))


if __name__ == '__main__':
    unittest.main()
