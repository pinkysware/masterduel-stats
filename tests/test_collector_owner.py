# -*- coding: utf-8 -*-
"""采集器与界面**同生共死**（2026-10-24 用户拍板）单测 —— 认领制。

机制：界面写 `collector_owner.json` 认领采集器；采集器每拍查 owner ——
owner 不在进入 90 秒宽限（给「重启界面」留认领窗口），到点无人认领退出。
没有 owner 文件 = 独立模式（手动 CLI 不受约束，但界面 ensure 时会接管）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core import paths                                          # noqa: E402
from duel_collector import (owner_alive, read_owner_file,        # noqa: E402
                            write_owner_file, clear_owner_file,
                            OWNER_GRACE_SEC, OWNER_PATH)


class _TmpData(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        d = os.path.join(self._td.name, 'data')
        os.makedirs(os.path.join(d, 'archive'), exist_ok=True)
        p = mock.patch.object(paths, 'DATA_DIR', d)
        p.start()
        self.addCleanup(p.stop)
        # owner 函数在 import 时已把 OWNER_PATH 定格在真路径 —— patch 它
        import duel_collector
        fake = os.path.join(d, 'archive', 'collector_owner.json')
        self._p2 = mock.patch.object(duel_collector, 'OWNER_PATH', fake)
        self._p2.start()
        self.addCleanup(self._p2.stop)


class OwnerFileTest(_TmpData):
    """写 / 读 / 清 + owner_alive 三态。"""

    def test_no_file_is_independent(self):
        self.assertIsNone(read_owner_file())
        self.assertIsNone(owner_alive(read_owner_file()))

    def test_write_read_roundtrip(self):
        write_owner_file(1234, born=99)
        got = read_owner_file()
        self.assertEqual(got['owner_pid'], 1234)
        self.assertEqual(got['owner_born'], 99)

    def test_owner_alive_true_false(self):
        write_owner_file(1234, born=99)
        own = read_owner_file()
        self.assertTrue(owner_alive(own, alive_fn=lambda p, b: True))
        self.assertFalse(owner_alive(own, alive_fn=lambda p, b: False))

    def test_clear(self):
        write_owner_file(1)
        clear_owner_file()
        self.assertIsNone(read_owner_file())

    def test_grace_is_90s(self):
        self.assertEqual(OWNER_GRACE_SEC, 90.0)


class ClaimByUiTest(_TmpData):
    """ui.collector_ctl 的认领链：ensure 在跑也认领 / stop 清理。"""

    def test_ensure_claims_running_collector(self):
        from ui import collector_ctl as ctl
        with mock.patch.object(ctl, 'collector_running', return_value=True), \
             mock.patch.object(ctl, 'start_collector') as sc, \
             mock.patch.object(ctl, '_log'):
            ctl.ensure_collector()
        sc.assert_not_called()                    # 在跑：不重拉
        own = read_owner_file()                   # 但认领了（owner=本测试进程）
        self.assertEqual(own['owner_pid'], os.getpid())

    def test_claim_failure_does_not_raise(self):
        from ui import collector_ctl as ctl
        with mock.patch.object(ctl, '_claim_ownership',
                               side_effect=OSError('boom')):
            ctl._claim_ownership.__wrapped__ if hasattr(
                ctl._claim_ownership, '__wrapped__') else None
        # _claim_ownership 自带兜底：直接调不抛
        with mock.patch('builtins.__import__',
                        side_effect=ImportError('nope')):
            try:
                ctl._claim_ownership()
            except Exception:                     # noqa: BLE001
                self.fail('认领失败不该抛')


class MainLoopGraceLogicTest(unittest.TestCase):
    """主循环宽限状态机（语义三句：主人活=重置 / 死+未宽限=起宽限 /
    死+过宽限=退出）。不跑真 main —— 锁纯函数与常量的组合语义。"""

    def test_semantics(self):
        # 用与 main 相同的判断式跑三种状态（不依赖时间 —— 注入 now）
        import duel_collector as dc
        grace = None
        now = 1000.0
        # 主人活着
        own = {'owner_pid': 1}
        if dc.owner_alive(own, alive_fn=lambda p, b: True):
            grace = None
        self.assertIsNone(grace)
        # 主人死了 -> 起宽限
        own_dead = {'owner_pid': 1}
        if not dc.owner_alive(own_dead, alive_fn=lambda p, b: False):
            if grace is None:
                grace = now + dc.OWNER_GRACE_SEC
        self.assertEqual(grace, 1090.0)
        # 到点 -> 退出条件成立
        self.assertGreaterEqual(now + 91.0, grace)


if __name__ == '__main__':
    unittest.main()
