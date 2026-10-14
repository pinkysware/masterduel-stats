# -*- coding: utf-8 -*-
"""IL2CPP metadata 镜像定位 —— 单测。

分两类：

* **离线**（不需要游戏）：header 解析、字符串表查找。
  真实 metadata 文件的用例会在没装游戏的机器上自动跳过。
* **进程内**（Windows）：在 Python 自己进程里埋一份 metadata 头部，
  看 ``find_metadata_mirror()`` 能不能把它找出来。
  逻辑和扫游戏进程完全一样 —— 只是把目标换成了自己，于是随时能跑。
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import unittest

from core.memread.il2cpp import (
    METADATA_MAGIC,
    TYPEINFO_NAME_OFFSET,
    MetadataHeader,
    MetadataMirror,
    default_metadata_path,
    find_metadata_mirror,
    find_string_offset,
    FIELDINFO_STRIDE,
    TYPEINFO_FIELDS_OFFSET,
    TYPEINFO_STATIC_FIELDS_OFFSET,
    find_singleton,
    read_singleton_slot,
    read_static_value,
    static_fields_addr,
    find_typeinfo,
    list_fields,
    read_string_table,
    typeinfo_pattern,
    verify_typeinfo,
)
from core.memread.process import MEM_COMMIT, MemoryRegion, SafeProcess

GAME_DIR = r'D:\steam\steamapps\common\Yu-Gi-Oh!  Master Duel'
META_PATH = default_metadata_path(GAME_DIR)


def _make_blob(version: int = 31, str_off: int = 0x4000,
               str_size: int = 0x1000) -> ctypes.Array:
    """造一份「长得像 metadata 头部」的缓冲区，供进程内搜索用。"""
    buf = ctypes.create_string_buffer(0x1000)
    struct.pack_into('<I', buf, 0x00, METADATA_MAGIC)
    struct.pack_into('<i', buf, 0x04, version)
    struct.pack_into('<i', buf, 0x08, 0x2000)     # stringLiteralOffset
    struct.pack_into('<i', buf, 0x0C, 0x0100)
    struct.pack_into('<i', buf, 0x10, 0x2100)     # stringLiteralDataOffset
    struct.pack_into('<i', buf, 0x14, 0x0100)
    struct.pack_into('<i', buf, 0x18, str_off)    # stringOffset
    struct.pack_into('<i', buf, 0x1C, str_size)   # stringSize
    return buf


# --------------------------------------------------------------- 字符串表查找

class TestFindStringOffset(unittest.TestCase):

    TBL = b'\x00LogHandler\x00LogHandlerExtra\x00Other\x00'

    def test_exact_match(self):
        self.assertEqual(find_string_offset(self.TBL, 'LogHandler'), 1)

    def test_longer_name_not_shadowed(self):
        # 完整匹配要求两头都是 NUL，所以短名字不会吃掉长名字
        self.assertEqual(find_string_offset(self.TBL, 'LogHandlerExtra'), 12)

    def test_second_entry(self):
        self.assertEqual(find_string_offset(self.TBL, 'Other'), 28)

    def test_missing(self):
        self.assertIsNone(find_string_offset(self.TBL, 'Nope'))

    def test_first_entry_has_no_leading_nul(self):
        self.assertEqual(find_string_offset(b'Alpha\x00Beta\x00', 'Alpha'), 0)

    def test_empty_table(self):
        self.assertIsNone(find_string_offset(b'', 'X'))


# ------------------------------------------------------------ 头部（离线解析）

class TestHeaderShape(unittest.TestCase):

    def setUp(self):
        # 必须一直持有 buf，否则被回收后进程内搜索就找不到了
        self.buf = _make_blob()
        self.hdr = MetadataHeader(bytes(self.buf[:64]))

    def test_is_valid(self):
        self.assertTrue(self.hdr.is_valid)

    def test_fields(self):
        self.assertEqual(self.hdr.version, 31)
        self.assertEqual(self.hdr.string_offset, 0x4000)
        self.assertEqual(self.hdr.string_size, 0x1000)

    def test_signature_is_16_bytes(self):
        self.assertEqual(len(self.hdr.signature), 16)

    def test_bad_magic_is_not_valid(self):
        buf = _make_blob()
        struct.pack_into('<I', buf, 0x00, 0xDEADBEEF)
        self.assertFalse(MetadataHeader(bytes(buf[:64])).is_valid)

    def test_zero_offsets_is_not_valid(self):
        buf = _make_blob(str_off=0, str_size=0)
        self.assertFalse(MetadataHeader(bytes(buf[:64])).is_valid)

    def test_too_short(self):
        with self.assertRaises(ValueError):
            MetadataHeader(b'\x00' * 8)


# --------------------------------------------------- 真实 metadata（有游戏才跑）

@unittest.skipUnless(os.path.exists(META_PATH), '本机没有 Master Duel，跳过真实文件用例')
class TestRealMetadata(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.hdr = MetadataHeader.from_file(META_PATH)
        cls.table = read_string_table(META_PATH, cls.hdr)

    def test_header_valid(self):
        self.assertTrue(self.hdr.is_valid)
        self.assertGreaterEqual(self.hdr.version, 24)

    def test_string_table_is_mostly_ascii(self):
        """字符串表该是可打印 ASCII + NUL。

        这条是 stringOffset/stringSize 没算错的**强证据**：
        偏移一错，读出来的基本是二进制噪声，比例会直接崩掉。
        """
        sample = self.table[:200000]
        printable = sum(1 for b in sample if 0x20 <= b < 0x7F or b == 0)
        self.assertGreater(printable / len(sample), 0.9)

    def test_known_names_present(self):
        for name in ('LogHandler', 'YgomGame', 'Engine'):
            self.assertIsNotNone(find_string_offset(self.table, name), name)

    def test_read_only(self):
        """读 metadata 必须走 open_game_file（模式非 'rb' 会被拦）。"""
        from core.memread.process import open_game_file
        with open_game_file(META_PATH) as fh:
            self.assertEqual(len(fh.read(16)), 16)
        with self.assertRaises(ValueError):
            open_game_file(META_PATH, 'wb')


# ---------------------------------------------------- 进程内搜索（Windows 才跑）

@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestFindMirrorInSelfProcess(unittest.TestCase):

    def setUp(self):
        self.buf = _make_blob()
        self.addr = ctypes.addressof(self.buf)
        self.hdr = MetadataHeader(bytes(self.buf[:64]))
        self.regions = [MemoryRegion(self.addr & ~0xFFF, 0x2000, 0x04,
                                     MEM_COMMIT, 0x20000)]

    def test_finds_own_blob(self):
        with SafeProcess(os.getpid()) as p:
            m = find_metadata_mirror(p, self.hdr, regions=self.regions)
        self.assertIsNotNone(m, '没能在自己进程里找到埋好的 metadata 头')
        self.assertEqual(m.base, self.addr)

    def test_no_false_positive(self):
        """在一块全是 0xAA 的内存里不该「找到」镜像。"""
        other = ctypes.create_string_buffer(b'\xAA' * 0x2000)
        oaddr = ctypes.addressof(other)
        regs = [MemoryRegion(oaddr, 512, 0x04, MEM_COMMIT, 0x20000)]
        with SafeProcess(os.getpid()) as p:
            self.assertIsNone(find_metadata_mirror(p, self.hdr, regions=regs))
        del other  # 保持引用到用完

    def test_addr_translation(self):
        m = MetadataMirror(0x7FF000000000, self.hdr)
        self.assertEqual(m.string_addr('X', b'\x00X\x00'),
                         0x7FF000000000 + 0x4000 + 1)
        self.assertIsNone(m.string_addr('Nope', b'\x00X\x00'))

    def test_string_table_span(self):
        m = MetadataMirror(0x1000, self.hdr)
        start, end = m.string_table_span
        self.assertEqual((start, end), (0x1000 + 0x4000,
                                        0x1000 + 0x4000 + 0x1000))



# ------------------------------------------- TypeInfo 定位（指针配对）

@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestFindTypeInfo(unittest.TestCase):
    """用假的 Il2CppClass 结构验证「搜名字指针配对」这一步。

    真实场景里 name/namespaze 指向 metadata 镜像；这里把 mirror.base 设成 0，
    于是地址就等于表内偏移，不必真把表摆进内存。
    """

    TBL = b'\x00LogHandler\x00YgomGame.Duel\x00'
    NS = 'YgomGame.Duel'
    NAME = 'LogHandler'

    def setUp(self):
        buf = _make_blob(str_off=0, str_size=len(self.TBL))
        self.hdr = MetadataHeader(bytes(buf[:64]))
        # 字符串表要真的摆进可读内存 —— 校验会把指针读回来比对，
        # 指针指向 1、12 这种地址是读不出东西的。
        self.tbl = ctypes.create_string_buffer(self.TBL)
        self.mirror = MetadataMirror(ctypes.addressof(self.tbl), self.hdr)

        base = ctypes.addressof(self.tbl)
        a = self.mirror.string_addr(self.NAME, self.TBL)
        b = self.mirror.string_addr(self.NS, self.TBL)
        self.assertEqual(a, base + 1)
        self.assertEqual(b, base + 12)

        # 造一个假 TypeInfo：name 在 +0x10，namespaze 在 +0x18
        self.ti = ctypes.create_string_buffer(0x100)
        struct.pack_into('<QQ', self.ti, TYPEINFO_NAME_OFFSET, a, b)
        self.ti_addr = ctypes.addressof(self.ti)
        self.regions = [MemoryRegion(self.ti_addr & ~0xFFF, 0x2000, 0x04,
                                     MEM_COMMIT, 0x20000)]

    def test_pattern_is_16_bytes(self):
        pat = typeinfo_pattern(self.mirror, self.TBL, self.NS, self.NAME)
        self.assertIsNotNone(pat)
        self.assertEqual(len(pat), 16)

    def test_unknown_name_gives_no_pattern(self):
        self.assertIsNone(
            typeinfo_pattern(self.mirror, self.TBL, self.NS, 'NoSuchClass'))

    def test_finds_the_fake_typeinfo(self):
        with SafeProcess(os.getpid()) as p:
            hits = find_typeinfo(p, self.mirror, self.TBL, self.NS, self.NAME,
                                 regions=self.regions)
        self.assertEqual(hits, [self.ti_addr])

    def test_unknown_class_finds_nothing(self):
        with SafeProcess(os.getpid()) as p:
            hits = find_typeinfo(p, self.mirror, self.TBL, self.NS, 'NoSuchClass',
                                 regions=self.regions)
        self.assertEqual(hits, [])

    def test_swapped_arguments_find_nothing(self):
        """namespace / name 写反时不该返回垃圾。

        真实踩过：签名是 (namespace, name)，写反了**不报错**，
        只是拿错的两个字符串去拼特征，搜回一堆假命中。
        """
        with SafeProcess(os.getpid()) as p:
            hits = find_typeinfo(p, self.mirror, self.TBL, self.NAME, self.NS,
                                 regions=self.regions)
        self.assertEqual(hits, [])

    def test_verify_can_be_turned_off(self):
        """verify=False 退回「只搜不校验」，结果应当一致（本场景没有噪声）。"""
        with SafeProcess(os.getpid()) as p:
            on = find_typeinfo(p, self.mirror, self.TBL, self.NS, self.NAME,
                               regions=self.regions)
            off = find_typeinfo(p, self.mirror, self.TBL, self.NS, self.NAME,
                                regions=self.regions, verify=False)
        self.assertEqual(on, [self.ti_addr])
        self.assertEqual(off, [self.ti_addr])

    def test_verify_rejects_garbage_address(self):
        with SafeProcess(os.getpid()) as p:
            self.assertFalse(
                verify_typeinfo(p, 0x1, self.mirror, self.TBL, self.NS, self.NAME))

    def test_verify_rejects_closed_handle(self):
        p = SafeProcess(os.getpid())
        p.close()
        self.assertFalse(
            verify_typeinfo(p, self.ti_addr, self.mirror, self.TBL,
                            self.NS, self.NAME))



@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestListFields(unittest.TestCase):
    """自己造一份 fields 数组，验证「连续结构体 + 步长 0x20」的读法。

    真实世界里这一步踩过坑：按指针数组（步长 8）读会得到 0 个字段。
    """

    NAMES = b'alpha\x00beta\x00gamma\x00'
    FIELDS = [('alpha', 0x10, 1), ('beta', 0x18, 2), ('gamma', 0x20, 3)]

    def setUp(self):
        self.names = ctypes.create_string_buffer(self.NAMES)
        na = ctypes.addressof(self.names)
        offs = [0, 6, 11]          # 各名字在 NAMES 里的偏移

        self.arr = ctypes.create_string_buffer(0x200)
        for i, (noff, (_n, off, tok)) in enumerate(zip(offs, self.FIELDS)):
            struct.pack_into('<QQQiI', self.arr, i * FIELDINFO_STRIDE,
                             na + noff, 0, 0, off, tok)

        self.ti = ctypes.create_string_buffer(0x100)
        struct.pack_into('<Q', self.ti, TYPEINFO_FIELDS_OFFSET,
                         ctypes.addressof(self.arr))
        self.ti_addr = ctypes.addressof(self.ti)

    def test_lists_all_fields(self):
        with SafeProcess(os.getpid()) as p:
            got = list_fields(p, self.ti_addr)
        self.assertEqual([f.name for f in got], ['alpha', 'beta', 'gamma'])
        self.assertEqual([f.offset for f in got], [0x10, 0x18, 0x20])
        self.assertEqual([f.token for f in got], [1, 2, 3])

    def test_limit_cuts_the_list(self):
        with SafeProcess(os.getpid()) as p:
            got = list_fields(p, self.ti_addr, limit=2)
        self.assertEqual([f.name for f in got], ['alpha', 'beta'])

    def test_address_points_at_the_struct(self):
        with SafeProcess(os.getpid()) as p:
            got = list_fields(p, self.ti_addr)
        arr = ctypes.addressof(self.arr)
        self.assertEqual(got[0].address, arr)
        self.assertEqual(got[2].address, arr + 2 * FIELDINFO_STRIDE)

    def test_null_fields_pointer_gives_empty(self):
        buf = ctypes.create_string_buffer(0x100)   # +0x80 处是 0
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(list_fields(p, ctypes.addressof(buf)), [])

    def test_unreadable_typeinfo_gives_empty(self):
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(list_fields(p, 0x1), [])


class TestStaticFields(unittest.TestCase):
    """自进程造一个假 TypeInfo：+0xB8 指静态块，块里 +0x08 是 s_instance 槽位。

    真实校准依据（2026-09-12 游戏主界面）：
      * v31 布局 0x68/0x80/0x88/0x90/0x98/0xA0 全部实机校准过，0xB8 是往下推的；
      * Engine 的 s_instance 槽位值 = 0（主界面没有对局引擎实例）。
    """

    def setUp(self):
        self.ti = ctypes.create_string_buffer(0x100)
        self.ti_addr = ctypes.addressof(self.ti)

        self.static = ctypes.create_string_buffer(0x40)
        self.static_addr = ctypes.addressof(self.static)

        self.obj = ctypes.create_string_buffer(0x40)
        self.obj_addr = ctypes.addressof(self.obj)

        struct.pack_into('<Q', self.ti, TYPEINFO_STATIC_FIELDS_OFFSET,
                         self.static_addr)
        struct.pack_into('<Q', self.static, 0x08, self.obj_addr)
        struct.pack_into('<Q', self.obj, 0x00, self.ti_addr)   # 对象头 = klass

    def test_static_fields_addr(self):
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(static_fields_addr(p, self.ti_addr),
                             self.static_addr)

    def test_static_fields_addr_when_absent(self):
        buf = ctypes.create_string_buffer(0x100)      # +0xB8 处是 0
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(static_fields_addr(p, ctypes.addressof(buf)), 0)

    def test_read_static_value(self):
        struct.pack_into('<Q', self.static, 0x10, 0x1122334455667788)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(read_static_value(p, self.ti_addr, 0x10),
                             struct.pack('<Q', 0x1122334455667788))

    def test_read_static_value_without_static_area(self):
        buf = ctypes.create_string_buffer(0x100)
        with SafeProcess(os.getpid()) as p:
            self.assertIsNone(read_static_value(p, ctypes.addressof(buf), 0x08))

    def test_find_singleton(self):
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(find_singleton(p, self.ti_addr), self.obj_addr)

    def test_find_singleton_empty_slot(self):
        """槽位还是 0 —— 主界面下的 Engine 就是这样。"""
        struct.pack_into('<Q', self.static, 0x08, 0)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(find_singleton(p, self.ti_addr), 0)

    def test_find_singleton_rejects_wrong_klass(self):
        """读出来的东西头不是本类的 TypeInfo —— 不能当实例用。"""
        struct.pack_into('<Q', self.obj, 0x00, self.ti_addr + 0x1000)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(find_singleton(p, self.ti_addr), 0)

    def test_find_singleton_reads_custom_slot(self):
        """有的类单例不在 +0x08（例如 DuelClient 的 instance 在 +0x00）。"""
        struct.pack_into('<Q', self.static, 0x00, self.obj_addr)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(find_singleton(p, self.ti_addr, slot=0x00),
                             self.obj_addr)

    # ---- ★ 2026-10-14：三态接口（「读不到」vs「没有实例」）----------------
    def test_read_singleton_slot_ok(self):
        """有实例 -> (addr, True)。"""
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(read_singleton_slot(p, self.ti_addr),
                             (self.obj_addr, True))

    def test_read_singleton_slot_empty_slot_is_ok(self):
        """槽位 0 = **读到了、只是没有实例**（大厅里的 Engine）—— 不是读失败。"""
        struct.pack_into('<Q', self.static, 0x08, 0)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(read_singleton_slot(p, self.ti_addr), (0, True))

    def test_read_singleton_slot_reports_read_failure(self):
        """★ 关键：静态区读不到时必须 ok=False。

        旧接口（`find_singleton`）在这里也返回 0，与「没有实例」**不可区分**
        —— 采集器据此判「不在对局」，内存定位失效时**静默漏局**
        （2026-10-14 19:15 起漏了 1 局以上，日志毫无痕迹）。
        """
        buf = ctypes.create_string_buffer(0x100)     # +0xB8 = 0 -> 没有静态区
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(read_singleton_slot(p, ctypes.addressof(buf)),
                             (0, False))

    def test_find_singleton_still_returns_zero_on_read_failure(self):
        """老接口行为不变（返回 0）—— 别处还在用它。"""
        buf = ctypes.create_string_buffer(0x100)
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(find_singleton(p, ctypes.addressof(buf)), 0)



if __name__ == '__main__':
    unittest.main()
