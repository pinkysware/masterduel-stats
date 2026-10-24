# -*- coding: utf-8 -*-
"""`core.diagnostics`（UI 日志 + 诊断包）单测 —— 全程临时目录。

背景（2026-10-24 用户需求）：「导出软件日志方便 debug，数据尽量详细」。
安装版 pythonw 下 stdout 是 None —— 界面进程的 print 从来没落过盘，
这是诊断包之外顺手补的最大缺口。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import diagnostics, paths                              # noqa: E402


class UiLogTest(unittest.TestCase):
    """install_ui_log：tee 落盘 / 幂等 / 轮转。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        d = os.path.join(self._td.name, 'data')
        os.makedirs(os.path.join(d, 'archive'), exist_ok=True)
        for p in (mock.patch.object(paths, 'DATA_DIR', d),):
            p.start()
            self.addCleanup(p.stop)
        # 保存 / 还原 stdout/stderr 与安装标志（测试间不泄漏）
        self._out, self._err = sys.stdout, sys.stderr
        self._flag = diagnostics._UI_LOG_INSTALLED
        self.addCleanup(self._restore)

    def _restore(self):
        sys.stdout, sys.stderr = self._out, self._err
        diagnostics._UI_LOG_INSTALLED = self._flag
        diagnostics.ui_log_path._current = None       # type: ignore[attr-defined]

    def test_install_writes_and_is_idempotent(self):
        p = diagnostics.install_ui_log()
        self.assertIsNotNone(p)
        self.assertTrue(os.path.isfile(p))
        print('诊断单测的落盘行')
        with open(p, encoding='utf-8') as f:
            txt = f.read()
        self.assertIn('UI start', txt)
        self.assertIn('诊断单测的落盘行', txt)
        p2 = diagnostics.install_ui_log()             # 幂等：不再装第二份
        self.assertEqual(p2, p)
        d = diagnostics.ui_log_dir()
        self.assertEqual(len([x for x in os.listdir(d)
                              if x.endswith('.log')]), 1)

    def test_rotate_keeps_recent(self):
        d = diagnostics.ui_log_dir()
        os.makedirs(d, exist_ok=True)
        for i in range(diagnostics.UI_LOG_KEEP + 2):
            io.open(os.path.join(d, 'ui-2020010%d-000000.log' % i),
                    'w', encoding='utf-8').write('x')
        diagnostics._rotate(d)
        left = sorted(os.listdir(d))
        self.assertEqual(len(left), diagnostics.UI_LOG_KEEP)


class BundleTest(unittest.TestCase):
    """collect_env / make_bundle：键恒定、大文件截断、小文件全量。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        td = self._td.name
        data = os.path.join(td, 'data')
        os.makedirs(os.path.join(data, 'archive'), exist_ok=True)
        app = os.path.join(td, 'app')
        os.makedirs(app, exist_ok=True)
        for p in (mock.patch.object(paths, 'DATA_DIR', data),
                  mock.patch.object(paths, 'ASSET_DIR', app)):
            p.start()
            self.addCleanup(p.stop)

    def _mklog(self, name, size):
        p = os.path.join(paths.DATA_DIR, 'archive', name)
        with open(p, 'wb') as f:
            f.write(b'L' * size)
        return p

    def test_collect_env_keys_stable(self):
        env = diagnostics.collect_env()
        for key in ('generated_at', 'app_version', 'python', 'platform',
                    'paths', 'env_check', 'collector', 'settings', 'db',
                    'reference', 'archive_files'):
            self.assertIn(key, env)

    def test_bundle_truncates_big_and_keeps_small(self):
        self._mklog('duel_collector.log', 5_000_000)          # 超 1MiB 上限
        self._mklog('rank_state_audit.log', 3_000_000)        # 超 512KiB
        small = self._mklog('first_run.log', 100)             # 全量
        dest = os.path.join(self._td.name, 'diag.zip')
        r = diagnostics.make_bundle(dest)
        self.assertTrue(os.path.isfile(dest))
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn('env.json', names)
            self.assertIn('logs/duel_collector.log', names)
            self.assertIn('logs/rank_state_audit.log', names)
            self.assertIn('logs/first_run.log', names)
            self.assertLessEqual(
                len(z.read('logs/duel_collector.log')), 1_048_576)
            self.assertEqual(len(z.read('logs/first_run.log')), 100)
            self.assertIn('TRUNCATED.json', names)
            cut = json.loads(z.read('TRUNCATED.json').decode('utf-8'))
            self.assertIn('logs/duel_collector.log', cut)
            self.assertNotIn('logs/first_run.log', cut)
            env = json.loads(z.read('env.json').decode('utf-8'))
            self.assertIn('app_version', env)
        self.assertEqual(r['log_files'], 3)
        self.assertTrue(small)

    def test_ui_logs_go_into_bundle(self):
        d = diagnostics.ui_log_dir()
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, 'ui-20200101-000000.log'), 'w',
                encoding='utf-8').write('ui log line')
        dest = os.path.join(self._td.name, 'diag.zip')
        diagnostics.make_bundle(dest)
        with zipfile.ZipFile(dest) as z:
            self.assertIn('logs/ui_logs/ui-20200101-000000.log',
                          set(z.namelist()))


if __name__ == '__main__':
    unittest.main()
