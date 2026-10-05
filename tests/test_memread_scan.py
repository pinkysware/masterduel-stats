# -*- coding: utf-8 -*-
"""SafeProcess 的区域遍历与字节搜索 —— 运行时单测（Windows）。

不碰游戏进程：全在 Python **自己**的进程上跑，所以随时可重复。
逻辑跟扫游戏进程一模一样，只是目标换成了自己。
"""
from __future__ import annotations

import ctypes
import os
import sys
import unittest

from core.memread.process import MEM_COMMIT, MemoryRegion, SafeProcess


def _region_of(addr: int, size: int = 0x2000) -> MemoryRegion:
    """把某地址所在的一小段包成 region，用来把搜索限制在附近。"""
    return MemoryRegion(addr & ~0xFFF, size, 0x04, MEM_COMMIT, 0x20000)


@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestRegions(unittest.TestCase):

    def test_nonempty(self):
        with SafeProcess(os.getpid()) as p:
            regs = p.regions()
        self.assertGreater(len(regs), 0)

    def test_all_committed_and_readable(self):
        from core.memread.process import PAGE_NOACCESS
        with SafeProcess(os.getpid()) as p:
            regs = p.regions()
        for r in regs:
            self.assertEqual(r.state, MEM_COMMIT)
            self.assertNotEqual(r.protect, PAGE_NOACCESS)
            self.assertGreater(r.size, 0)

    def test_covers_own_buffer(self):
        buf = ctypes.create_string_buffer(4096)
        addr = ctypes.addressof(buf)
        with SafeProcess(os.getpid()) as p:
            regs = p.regions()
        self.assertTrue(any(r.base <= addr < r.end for r in regs))
        del buf

    def test_sorted_and_disjoint(self):
        with SafeProcess(os.getpid()) as p:
            regs = p.regions()
        for a, b in zip(regs, regs[1:]):
            self.assertLessEqual(a.end, b.base)


@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestScan(unittest.TestCase):

    def test_finds_known_bytes(self):
        buf = ctypes.create_string_buffer(b'X' * 100)
        addr = ctypes.addressof(buf)
        ctypes.memmove(addr + 30, b'ABCDEFGH', 8)
        with SafeProcess(os.getpid()) as p:
            hits = p.scan_all(b'ABCDEFGH', regions=[_region_of(addr)], chunk=16)
        self.assertIn(addr + 30, hits)
        del buf

    def test_pattern_spanning_chunk_boundary(self):
        """块大小 16、pattern 跨在第 2/3 块之间 —— 重叠处理不对就会漏。"""
        buf = ctypes.create_string_buffer(b'X' * 100)
        addr = ctypes.addressof(buf)
        ctypes.memmove(addr + 30, b'ABCDEFGH', 8)   # 30..38，跨 32 这个边界
        with SafeProcess(os.getpid()) as p:
            hits = p.scan_all(b'ABCDEFGH', regions=[_region_of(addr)], chunk=16)
        self.assertIn(addr + 30, hits)
        del buf

    def test_max_hits(self):
        buf = ctypes.create_string_buffer(b'ZZ' * 500)
        addr = ctypes.addressof(buf)
        with SafeProcess(os.getpid()) as p:
            hits = p.scan_all(b'ZZ', max_hits=3,
                              regions=[_region_of(addr)], chunk=64)
        self.assertEqual(len(hits), 3)
        del buf

    def test_not_found(self):
        buf = ctypes.create_string_buffer(b'Y' * 4096)
        addr = ctypes.addressof(buf)
        with SafeProcess(os.getpid()) as p:
            hits = p.scan_all(b'QQQQQQQQ', regions=[_region_of(addr, 0x1000)])
        self.assertEqual(hits, [])
        del buf

    def test_empty_pattern_yields_nothing(self):
        with SafeProcess(os.getpid()) as p:
            self.assertEqual(p.scan_all(b''), [])


@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestTryRead(unittest.TestCase):

    def test_try_read_bad_address_returns_none(self):
        with SafeProcess(os.getpid()) as p:
            self.assertIsNone(p.try_read(0x1, 8))          # 未映射
            here = ctypes.addressof(ctypes.c_int(1))       # 自己进程，必可读
            self.assertIsNotNone(p.try_read(here, 1))

    def test_read_bad_address_raises(self):
        from core.memread.process import MemoryReadError
        with SafeProcess(os.getpid()) as p:
            with self.assertRaises(MemoryReadError):
                p.read(0x1, 8)


@unittest.skipUnless(sys.platform == 'win32', '只在 Windows 上测进程内存')
class TestScanDeadlineAndPrivate(unittest.TestCase):
    """09-26 事故刀①③：扫描墙钟预算（deadline）+ 私有堆过滤（private_regions）。

    背景：游戏进程内存大时单次全量扫描 1-4 分钟，把收局编排拖死 ——
    scan 必须能限时，实例扫描必须能只扫 GC 托管堆。
    """

    def test_deadline_past_stops_immediately(self):
        import time as _time
        buf = ctypes.create_string_buffer(b'ABCDEFGH' * 4)
        addr = ctypes.addressof(buf)
        with SafeProcess(os.getpid()) as p:
            hits = list(p.scan(b'ABCDEFGH', regions=[_region_of(addr)],
                               deadline=_time.monotonic() - 1.0))
        self.assertEqual(hits, [])
        del buf

    def test_deadline_future_still_finds(self):
        import time as _time
        buf = ctypes.create_string_buffer(b'X' * 100)
        addr = ctypes.addressof(buf)
        ctypes.memmove(addr + 30, b'ABCDEFGH', 8)
        with SafeProcess(os.getpid()) as p:
            hits = p.scan_all(b'ABCDEFGH', regions=[_region_of(addr)],
                              deadline=_time.monotonic() + 10.0)
        self.assertIn(addr + 30, hits)
        del buf

    def test_private_regions_all_private_and_nonempty(self):
        from core.memread.process import MEM_PRIVATE
        with SafeProcess(os.getpid()) as p:
            regs = p.private_regions()
        self.assertGreater(len(regs), 0)
        for r in regs:
            self.assertEqual(r.type, MEM_PRIVATE)

    def test_private_regions_covers_own_buffer(self):
        """Python 自己的堆就是私有内存：新分配的 buffer 必须被覆盖。"""
        buf = ctypes.create_string_buffer(4096)
        addr = ctypes.addressof(buf)
        with SafeProcess(os.getpid()) as p:
            regs = p.private_regions()
        self.assertTrue(any(r.base <= addr < r.end for r in regs))
        del buf


if __name__ == '__main__':
    unittest.main()
