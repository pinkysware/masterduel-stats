# -*- coding: utf-8 -*-
"""`ui/collector_ctl.py` 单测 —— 退出时收掉采集器（2026-10-10）。

盯三件事：
  1. **没 pid 文件 / 进程已不在 → 什么都不做**（不许误杀）
  2. **pid 复用判据**：pid 文件里那个 pid 若已被系统分给别的进程（不是采集器），
     **绝不能杀** —— 2026-10-08 事故：旧 pid 被分给 svchost，盲杀会杀系统进程
  3. 判据**必须借采集器自己的 `pid_alive`**，不在 UI 侧重写一份
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from ui import collector_ctl as cc                        # noqa: E402


class _Stub(object):
    """替身采集器判据：**只认**我们说好那个 pid，其它一律 False。"""

    def __init__(self, alive_pid):
        self.alive_pid = alive_pid
        self.calls = []

    def pid_alive(self, pid, born=None):
        self.calls.append((pid, born))
        return pid == self.alive_pid


class TestStopCollector(unittest.TestCase):
    def setUp(self):
        # ⚠️ 临时目录建在**项目内**（不是系统 TEMP）：Windows 上系统 TEMP
        #    会返回短路径（ADMINI~1），实测导致路径长度判断错乱、文件写不出来。
        _tmp_base = os.path.join(PROJ, 'tmp')
        os.makedirs(_tmp_base, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix='cctl_', dir=_tmp_base)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # ⚠️  读的是**模块级常量** ，
        #    所以这里换掉常量本身； 只是测试自己的便捷引用。
        self._pid_path = os.path.join(self.tmp, 'duel_collector.pid')
        self._orig_pid_path = cc.PID_PATH
        cc.PID_PATH = self._pid_path
        self.addCleanup(setattr, cc, 'PID_PATH', self._orig_pid_path)

    def _write_pid(self, **kw):
        data = {'collector_pid': 4321, 'collector_born': 111, 'game_pid': None}
        data.update(kw)
        with open(self._pid_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def _stub(self, alive_pid, terminate=None):
        """把 `pid_alive` 与结束动作都打桩（**不真杀任何进程**）。"""
        stub = _Stub(alive_pid)
        killed = []

        def fake_terminate(pid, timeout_ms=3000):
            killed.append(pid)
            return terminate or 'stopped'

        orig_alive, orig_term = (
            sys.modules.get('duel_collector'), cc._terminate)
        sys.modules['duel_collector'] = SimpleNamespaceModule(stub)
        self.addCleanup(lambda: sys.modules.__setitem__('duel_collector', orig_alive)
                        if orig_alive else sys.modules.pop('duel_collector', None))
        cc._terminate = fake_terminate
        self.addCleanup(setattr, cc, '_terminate', orig_term)
        return stub, killed

    # ---------------------------------------------------------- 什么都不做
    def test_no_pid_file(self):
        """没有 pid 文件 → 不动手。"""
        _, killed = self._stub(alive_pid=None)
        self.assertEqual(cc.stop_collector(), 'no-pid-file')
        self.assertEqual(killed, [])

    def test_dead_process_not_killed(self):
        """pid 文件在、进程已经不在 → 不动手（陈旧 pid 文件是常态）。"""
        self._write_pid()
        _, killed = self._stub(alive_pid=None)          # 判据：不是活着的采集器
        self.assertEqual(cc.stop_collector(), 'not-running')
        self.assertEqual(killed, [], '陈旧 pid 文件不该触发任何结束动作')

    # ---------------------------------------------------------- 真的收
    def test_stops_live_collector(self):
        """活着的采集器 → 结束它。"""
        self._write_pid()
        _, killed = self._stub(alive_pid=4321)
        self.assertEqual(cc.stop_collector(), 'stopped')
        self.assertEqual(killed, [4321])

    def test_passes_born_to_judgement(self):
        """**创建时间必须一起传给判据** —— pid 复用就靠它区分。"""
        self._write_pid()
        stub, _ = self._stub(alive_pid=4321)
        cc.stop_collector()
        self.assertEqual(stub.calls, [(4321, 111)],
                         '判据必须收到 (pid, born)，否则拦不住 pid 复用')

    # ---------------------------------------------------------- 安全：绝不盲杀
    def test_pid_reused_by_other_process_is_not_killed(self):
        """🔴 pid 被系统复用给别的进程 → **绝不能杀**（2026-10-08 事故）。

        场景：10-06 那次采集器的 pid=4321 早已退出，系统把 4321 分给了
        某个 svchost。pid 文件还留着。此时退出界面**若照 pid 盲杀，
        就会杀掉一个无辜的系统进程**。判据 `pid_alive` 会因镜像名不符返回
        False —— 这里断言我们尊重它。
        """
        self._write_pid()
        _, killed = self._stub(alive_pid=None)   # 镜像名不是 python → 不算采集器
        self.assertEqual(cc.stop_collector(), 'not-running')
        self.assertEqual(killed, [], 'pid 被复用的进程绝不能被杀')

    def test_judgement_failure_means_no_kill(self):
        """判据拿不到（import 失败）→ **不杀**，宁可漏收。"""
        self._write_pid()
        killed = []
        orig_term = cc._terminate
        cc._terminate = lambda p, t=3000: killed.append(p) or 'stopped'
        self.addCleanup(setattr, cc, '_terminate', orig_term)
        # 造一个 import 必然失败的场景：把 duel_collector 从 sys.modules 摘掉
        # 且确保它不在可导入位置（这里直接打桩 _load_pid 正常，验证 pid_alive 分支）
        sys.modules.pop('duel_collector', None)
        orig_path = list(sys.path)
        sys.path[:] = [p for p in sys.path if p != os.path.join(PROJ, 'tools')]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), orig_path))
        try:
            rc = cc.stop_collector()
        except ImportError:
            rc = 'import-failed'
        #: 无论哪种返回，都**不许**触发结束动作
        self.assertEqual(killed, [], '判据不可用时必须放弃，不能盲杀')


class SimpleNamespaceModule(object):
    """`sys.modules` 里塞的替身：暴露 `pid_alive`。"""

    def __init__(self, stub):
        self.pid_alive = stub.pid_alive


if __name__ == '__main__':
    unittest.main()