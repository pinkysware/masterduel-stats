# -*- coding: utf-8 -*-
"""`core/memread/il2cpp_obj.py` 的单测（BUG-B3-08 / B3-09 / B3-10）。

不碰真实进程：用 tests/fakemem_obj 的假内存对象图。
（假内存的块是精确大小，读越界会抛 —— 这正好用来造「短读 / 读失败」。）
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(HERE), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fakemem_obj import (FakeMem, ObjProc, build_boxed_int,        # noqa: E402
                         build_named_typeinfo, build_string)
from core.memread import il2cpp_obj as io2                          # noqa: E402


class _Proc(ObjProc):
    """ObjProc + 能读短块（假内存的 cstring 块只有 len+65 字节）。"""

    def read_cstring(self, addr, n=128):
        try:
            raw = self.read(addr, min(n, 64))
        except Exception:
            return ''
        return raw.split(b'\x00', 1)[0].decode('utf-8', 'replace')


class _TruncatingProc(_Proc):
    """读 ``address`` 时故意少给 ``short_by`` 字节（模拟 ReadProcessMemory 短读）。"""

    def __init__(self, mem, truncate_at=None, short_by=8):
        super(_TruncatingProc, self).__init__(mem)
        self.truncate_at = truncate_at
        self.short_by = short_by

    def read(self, address, size):
        raw = super(_TruncatingProc, self).read(address, size)
        if self.truncate_at is not None and address == self.truncate_at:
            return raw[:-self.short_by] if self.short_by else raw
        return raw


class TestDictItemsShortRead(unittest.TestCase):
    """★ BUG-B3-08：短读必须跳过，不能让 struct.error 逃出函数。"""

    def _dict(self, mem):
        ti = build_named_typeinfo(mem, 'Dictionary`2',
                                  [('_entries', 0x10), ('_count', 0x18)])
        d = mem.alloc(0x28)
        entries = mem.alloc(0x20 + io2.DICT_ENTRY_STRIDE * 4)
        mem.put_u64(d, 0x00, ti)
        mem.put_u64(d, 0x10, entries)
        mem.put_i32(d, 0x18, 2)
        key = build_string(mem, 'GameMode')
        for i in range(2):
            e = 0x20 + i * io2.DICT_ENTRY_STRIDE
            mem.put_i32(entries, e, 0x1234)
            mem.put_u64(entries, e + 8, key)
            mem.put_u64(entries, e + 16, 0x5555)
        return d, entries

    def test_short_read_does_not_raise(self):
        mem = FakeMem()
        d, entries = self._dict(mem)
        proc = _TruncatingProc(mem, truncate_at=entries + 0x20)
        got = io2.dict_items(proc, None, d)          # 不该抛 struct.error
        self.assertEqual(got, [('GameMode', 21845)])  # 短读那条被跳过，另一条照读

    def test_all_entries_short_read_gives_empty(self):
        mem = FakeMem()
        d, entries = self._dict(mem)
        orig = _TruncatingProc.read

        def _always_short(self, address, size):
            raw = ObjProc.read(self, address, size)
            if address in (entries + 0x20, entries + 0x38):
                return raw[:-8]
            return raw

        proc = _TruncatingProc(mem)
        proc.read = _always_short.__get__(proc)
        self.assertEqual(io2.dict_items(proc, None, d), [])

    def test_normal_read_returns_pairs(self):
        mem = FakeMem()
        d, _entries = self._dict(mem)
        got = io2.dict_items(_Proc(mem), None, d)
        self.assertEqual([k for k, _v in got], ['GameMode', 'GameMode'])
        self.assertEqual([v for _k, v in got], [0x5555, 0x5555])


class TestBoxedIntWidth(unittest.TestCase):
    """★ BUG-B3-09：宽度按类名取（Int32 只占 4 字节，+0x14 是对齐填充）。"""

    def _boxed(self, mem, class_name, raw_value, size=8):
        ti = build_named_typeinfo(mem, class_name, [])
        obj = mem.alloc(0x20)
        mem.put_u64(obj, 0x00, ti)
        mem.put(obj, 0x10, int(raw_value).to_bytes(size, 'little'))
        return obj

    def test_int64_reads_eight_bytes(self):
        mem = FakeMem()
        obj = self._boxed(mem, 'Int64', 0x0102030405060708)
        self.assertEqual(io2.boxed_int(_Proc(mem), obj), 0x0102030405060708)

    def test_int32_reads_four_bytes_only(self):
        """填充字节是 0xFF 也不许算进值里（原来按 <q 读会被污染）。"""
        mem = FakeMem()
        ti = build_named_typeinfo(mem, 'Int32', [])
        obj = mem.alloc(0x20)
        mem.put_u64(obj, 0x00, ti)
        mem.put(obj, 0x10, (19).to_bytes(4, 'little'))
        mem.put(obj, 0x14, b'\xff\xff\xff\xff')     # 对齐填充
        self.assertEqual(io2.boxed_int(_Proc(mem), obj), 19)

    def test_unsigned_variants(self):
        mem = FakeMem()
        obj = self._boxed(mem, 'UInt32', 0xFFFFFFFE, size=4)
        self.assertEqual(io2.boxed_int(_Proc(mem), obj), 0xFFFFFFFE)

    def test_unknown_class_is_none(self):
        mem = FakeMem()
        obj = self._boxed(mem, 'String', 0)
        self.assertIsNone(io2.boxed_int(_Proc(mem), obj))

    def test_short_read_is_none(self):
        mem = FakeMem()
        obj = self._boxed(mem, 'Int64', 1)
        proc = _TruncatingProc(mem, truncate_at=obj + 0x10)
        self.assertIsNone(io2.boxed_int(proc, obj))


class _FailingProc(_Proc):
    """指定地址的 read_ptr 抛异常（模拟野指针 / 页被回收）。"""

    def __init__(self, mem, fail_at=()):
        super(_FailingProc, self).__init__(mem)
        self.fail_at = set(fail_at)

    def read_ptr(self, address):
        if address in self.fail_at:
            from core.memread.process import MemoryReadError
            raise MemoryReadError('未映射 0x%X' % address)
        return super(_FailingProc, self).read_ptr(address)


class TestListItemsFailure(unittest.TestCase):
    """★ BUG-B3-10：读失败的项给 None 占位，`int_list` 过滤掉。"""

    def _list(self, mem, size, elems_room=None):
        """搭 List<T>：``_size`` = size，但 ``_items`` 数组只够放 elems_room 个。"""
        room = max(1, elems_room if elems_room is not None else size)
        ti = build_named_typeinfo(mem, 'List`1',
                                  [('_items', 0x10), ('_size', 0x18)])
        lst = mem.alloc(0x28)
        mem.put_u64(lst, 0x00, ti)
        arr = mem.alloc(0x20 + 8 * room)
        mem.put_u64(lst, 0x10, arr)
        mem.put_i32(lst, 0x18, size)
        return lst, arr

    def test_failed_element_is_none_not_zero(self):
        mem = FakeMem()
        lst, arr = self._list(mem, 2)
        good = build_boxed_int(mem, 20500)
        mem.put_u64(arr, 0x20, good)
        mem.put_u64(arr, 0x28, build_boxed_int(mem, 21433))
        # 第二个元素的**指针槽**读不动 -> 那一项必须给 None（不是 0）
        proc = _FailingProc(mem, fail_at=[arr + 0x28])
        got = io2.list_items(proc, None, lst)
        self.assertEqual(got[0], good)
        self.assertEqual(got[1:], [None])

    def test_int_list_drops_failures(self):
        mem = FakeMem()
        lst, arr = self._list(mem, 2)
        mem.put_u64(arr, 0x20, build_boxed_int(mem, 20500))
        mem.put_u64(arr, 0x28, build_boxed_int(mem, 21433))
        proc = _FailingProc(mem, fail_at=[arr + 0x28])
        self.assertEqual(io2.int_list(proc, None, lst), [20500])

    def test_int_list_skips_zero_pointer(self):
        mem = FakeMem()
        lst, arr = self._list(mem, 1)
        mem.put_u64(arr, 0x20, 0)
        self.assertEqual(io2.int_list(_Proc(mem), None, lst), [])

    def test_int_list_normal(self):
        mem = FakeMem()
        lst, arr = self._list(mem, 2)
        mem.put_u64(arr, 0x20, build_boxed_int(mem, 20500))
        mem.put_u64(arr, 0x28, build_boxed_int(mem, 21433))
        self.assertEqual(io2.int_list(_Proc(mem), None, lst), [20500, 21433])


if __name__ == '__main__':
    unittest.main()
