# -*- coding: utf-8 -*-
"""假内存的 IL2CPP 对象图搭建器（client_work / duel_settings / record_capture 单测共用）。

复用 test_memread_public_cards 的 :class:`FakeMem`（块分配、越界读报
``MemoryReadError``）与 :func:`build_typeinfo`（真 ``list_fields`` 直接可读的
假 TypeInfo），补四样东西：System.String（UTF-16）/ 字符串键 Dictionary /
List / 装箱 Int64。搭出来的对象图能被产品代码当真内存读 —— 「假内存整链」。

⚠️ 本文件名不以 test_ 开头，unittest discover 不会把它当测试收集。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(HERE), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_memread_public_cards import FakeMem, FakeProc  # noqa: E402,F401
from test_memread_public_cards import build_typeinfo  # noqa: E402,F401


class ObjProc(FakeProc):
    """加 ``read_cstring``（类名等 C 字符串读取）。"""

    def read_cstring(self, addr, n=128):
        try:
            raw = self.read(addr, n)
        except Exception:
            return ''
        return raw.split(b'\x00', 1)[0].decode('utf-8', 'replace')


def build_named_typeinfo(mem, name, fields):
    """假 TypeInfo（字段表 +0x80）+ 类名（char* 在 +0x10，供 class_name_of）。

    分配 0xC0：给 ``static_fields``（+0xB8）留位 —— 需要静态区的类
    （ClientWork.s_data / DuelClient.instance）由测试自己 ``put_u64(ti, 0xB8, sb)``。
    """
    ti = mem.alloc(0xC0)
    arr = mem.alloc(0x20 * (len(fields) + 1))
    for i, (fname, off) in enumerate(fields):
        e = i * 0x20
        mem.put_u64(arr, e + 0x00, mem.put_str(fname))
        mem.put_i32(arr, e + 0x18, off)
    mem.put_u64(ti, 0x80, arr)
    mem.put_u64(ti, 0x10, mem.put_str(name))
    return ti


def build_string(mem, s):
    """System.String：长度 i32 在 +0x10、UTF-16LE 在 +0x14。"""
    obj = mem.alloc(0x20 + len(s) * 2 + 8)
    mem.put_i32(obj, 0x10, len(s))
    mem.put(obj, 0x14, s.encode('utf-16-le'))
    return obj


def build_str_dict(mem, mapping):
    """Dictionary<string, object>（entry 24B：hash/next/key/value，数据 +0x20）。"""
    ti = build_named_typeinfo(mem, 'Dictionary`2',
                              [('_entries', 0x10), ('_count', 0x18)])
    d = mem.alloc(0x28)
    mem.put_u64(d, 0x00, ti)
    n = len(mapping)
    entries = mem.alloc(0x20 + 24 * max(n, 1))
    mem.put_u64(d, 0x10, entries)
    mem.put_i32(d, 0x18, n)
    for i, (k, v) in enumerate(mapping.items()):
        base = 0x20 + i * 24
        mem.put_i32(entries, base, 0x05A0C0DE)
        mem.put_u64(entries, base + 8, build_string(mem, k))
        mem.put_u64(entries, base + 16, v)
    return d


def build_ref_list(mem, element_addrs):
    """List<object>（元素是指针，如 List<string> / List<boxed Int64>）。"""
    ti = build_named_typeinfo(mem, 'List`1',
                              [('_items', 0x10), ('_size', 0x18)])
    obj = mem.alloc(0x28)
    mem.put_u64(obj, 0x00, ti)
    n = len(element_addrs)
    arr = mem.alloc(0x20 + 8 * max(n, 1))
    mem.put_u64(obj, 0x10, arr)
    mem.put_i32(obj, 0x18, n)
    mem.put_u64(arr, 0x18, n)
    for i, a in enumerate(element_addrs):
        mem.put_u64(arr, 0x20 + i * 8, a)
    return obj


def build_struct_list(mem, elem_size, raw_elems):
    """List<struct>（元素内联，如 LogBaseData 32B / ShowTurn 24B）。"""
    ti = build_named_typeinfo(mem, 'List`1',
                              [('_items', 0x10), ('_size', 0x18)])
    obj = mem.alloc(0x28)
    mem.put_u64(obj, 0x00, ti)
    n = len(raw_elems) // elem_size
    arr = mem.alloc(0x20 + len(raw_elems) + 8)
    mem.put_u64(obj, 0x10, arr)
    mem.put_i32(obj, 0x18, n)
    mem.put_u64(arr, 0x18, n)
    mem.put(arr, 0x20, raw_elems)
    return obj


def build_boxed_int(mem, v):
    """装箱 Int64（类名 'Int64'，值在 +0x10）。"""
    ti = build_named_typeinfo(mem, 'Int64', [])
    obj = mem.alloc(0x20)
    mem.put_u64(obj, 0x00, ti)
    mem.put_u64(obj, 0x10, int(v) & 0xFFFFFFFFFFFFFFFF)
    return obj
