# -*- coding: utf-8 -*-
"""`script_runner.utf8_env_pairs` 单测 —— 子进程环境钉 UTF-8。

背景（2026-10-12 实测事故）：「参照库刷新」在 UI 里跑
`fetch_mdm_decks.py --all`，QProcess 管道里子进程 stdout 默认 GBK，
打印 `✔` 直接 UnicodeEncodeError（退出码 1，日志区只有半屏乱码）。
本类按 UTF-8 解码，所以子进程必须钉成 UTF-8。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from ui.script_runner import utf8_env_pairs             # noqa: E402


class TestUtf8EnvPairs(unittest.TestCase):
    def test_pins_utf8(self):
        env = utf8_env_pairs({'PATH': 'x'})
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        self.assertEqual(env['PYTHONUTF8'], '1')

    def test_preserves_base(self):
        env = utf8_env_pairs({'PATH': 'x', 'PYTHONUTF8': '0'})
        self.assertEqual(env['PATH'], 'x')
        #: 已有的键会被覆盖成 UTF-8（钉死，不沿用继承值）
        self.assertEqual(env['PYTHONUTF8'], '1')

    def test_from_os_environ(self):
        env = utf8_env_pairs()
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        #: 不丢系统环境（QProcessEnvironment 要在系统环境上追加）
        self.assertIn('PATH', env)


if __name__ == '__main__':
    unittest.main()
