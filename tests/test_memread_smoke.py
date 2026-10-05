# -*- coding: utf-8 -*-
"""SafeProcess 的**运行时**单测（在真实进程上读真实内存）。

跟 test_memread_guard.py 分工不同：
  * guard —— 静态红线（不许出现写内存 API、权限位必须干净）
  * smoke —— 动态功能（真的打开进程、真的读回来）

拿 Python 自己的进程开刀，不依赖游戏运行。非 Windows 平台整体跳过。
"""
from __future__ import annotations

import ctypes
import os
import sys
import unittest

from core.memread import SafeProcess, find_module
from core.memread.process import MemoryReadError


@unittest.skipUnless(sys.platform == 'win32', '需要 Windows 才能读进程内存')
class TestSafeProcessLive(unittest.TestCase):
    def setUp(self):
        self.proc = SafeProcess(os.getpid())

    def tearDown(self):
        self.proc.close()

    def test_access_is_read_only(self):
        self.assertEqual(self.proc.access, 0x0410)

    def test_read_known_bytes(self):
        mark = b'HELLO_MEMREAD_2026'
        buf = ctypes.create_string_buffer(mark)
        self.assertEqual(self.proc.read(ctypes.addressof(buf), len(mark)), mark)

    def test_read_short_buffer(self):
        """只读一部分也要正确。"""
        buf = ctypes.create_string_buffer(b'ABCDEFGH')
        self.assertEqual(self.proc.read(ctypes.addressof(buf), 4), b'ABCD')

    def test_read_zero_size(self):
        buf = ctypes.create_string_buffer(b'x')
        self.assertEqual(self.proc.read(ctypes.addressof(buf), 0), b'')

    def test_read_u32(self):
        v = ctypes.c_uint32(0xDEADBEEF)
        self.assertEqual(self.proc.read_u32(ctypes.addressof(v)), 0xDEADBEEF)

    def test_read_u64(self):
        v = ctypes.c_uint64(0x0102030405060708)
        self.assertEqual(self.proc.read_u64(ctypes.addressof(v)), 0x0102030405060708)

    def test_read_ptr(self):
        v = ctypes.c_uint64(0x1122334455667788)
        self.assertEqual(self.proc.read_ptr(ctypes.addressof(v)), 0x1122334455667788)

    def test_read_cstring_stops_at_nul(self):
        buf = ctypes.create_string_buffer(b'yugioh-md\x00trailing')
        self.assertEqual(self.proc.read_cstring(ctypes.addressof(buf)), 'yugioh-md')

    def test_modules_contains_main_executable(self):
        mods = self.proc.modules()
        self.assertTrue(mods, 'modules() 不应为空')
        names = [m[0].lower() for m in mods]
        self.assertIn(os.path.basename(sys.executable).lower(), names)

    def test_read_module_header_is_mz(self):
        """真的跨进程读到了 PE 头 —— 证明整条链路可用。"""
        mods = self.proc.modules()
        hit = find_module(mods, os.path.basename(sys.executable))
        self.assertIsNotNone(hit)
        self.assertEqual(self.proc.read(hit[1], 2), b'MZ')

    def test_invalid_address_raises(self):
        with self.assertRaises(MemoryReadError):
            self.proc.read(0x1, 16)

    def test_use_after_close_raises(self):
        p = SafeProcess(os.getpid())
        p.close()
        with self.assertRaises(MemoryReadError):
            p.read(0x1000, 8)

    def test_context_manager(self):
        with SafeProcess(os.getpid()) as p:
            mods = p.modules()
            self.assertTrue(mods)
        # 退出 with 后句柄已关


if __name__ == '__main__':
    unittest.main()
