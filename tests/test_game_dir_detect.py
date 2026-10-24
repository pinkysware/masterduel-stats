# -*- coding: utf-8 -*-
"""游戏目录自动探测的测试（2026-10-24「一键可用」）。

背景：`default_game_dir()` 之前是「环境变量 or 写死的 D 盘路径」——
任何游戏不装在那个位置的机器上，采集器都读不到 global-metadata.dat，
attach 永远失败 = 统计功能全废。现在按
环境变量 → game_dir.ini → 进程反查 → Steam 库 → 旧常量 兜底。
"""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.memread import end_message as em                  # noqa: E402
from core.memread.process import exe_path                   # noqa: E402

BS = chr(92)
NL = chr(10)


class ExePathTest(unittest.TestCase):
    """exe_path(pid)：拿进程可执行文件全路径（只读 0x1000 权限）。"""

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_current_process(self):
        """拿当前进程自己的路径 —— 最容易验证且一定有权限。"""
        p = exe_path(os.getpid())
        self.assertTrue(p, '拿不到自己的 exe 路径')
        self.assertTrue(os.path.isfile(p), '路径不指向真实文件：%r' % p)

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_dead_pid_returns_none(self):
        """不存在的 pid → None（不抛）。"""
        self.assertIsNone(exe_path(99999999))


class PriorityTest(unittest.TestCase):
    """default_game_dir() 的优先级：环境变量 > game_dir.ini > 自动探测。"""

    def setUp(self):
        self._old_cache = em._game_dir_cache
        em._game_dir_cache = None
        self._old_env = os.environ.pop('MD_GAME_DIR', None)
        self.tmp = tempfile.mkdtemp(prefix='gamedir_')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        em._game_dir_cache = self._old_cache
        from core import paths
        paths.write_game_dir_pref('')
        if self._old_env is not None:
            os.environ['MD_GAME_DIR'] = self._old_env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_var_wins(self):
        os.environ['MD_GAME_DIR'] = r'X:\env\override'
        self.assertEqual(em.default_game_dir(), r'X:\env\override')

    def test_ini_beats_autodetect(self):
        """game_dir.ini（向导手动选的）优先于进程反查/Steam。"""
        fake = os.path.join(self.tmp, 'MyGame')
        os.makedirs(os.path.join(fake, 'masterduel_Data'))
        from core import paths
        self.assertTrue(paths.write_game_dir_pref(fake))
        with mock.patch.object(em, '_game_dir_from_process',
                               return_value=r'Y:\from\process'):
            self.assertEqual(em.default_game_dir(), fake)

    def test_process_lookup_beats_steam(self):
        with mock.patch.object(em, '_game_dir_from_process',
                               return_value=r'Z:\from\process'), \
             mock.patch.object(em, '_steam_library_dirs',
                               return_value=[r'W:\steamlib']):
            self.assertEqual(em.default_game_dir(), r'Z:\from\process')

    def test_fallback_to_constant(self):
        """全都拿不到 → 旧常量兜底（不抛、返回 str）。"""
        with mock.patch.object(em, '_game_dir_from_process',
                               return_value=None), \
             mock.patch.object(em, '_steam_library_dirs',
                               return_value=[]):
            self.assertEqual(em.default_game_dir(), em.DEFAULT_GAME_DIR)

    def test_result_is_cached(self):
        """命中自动探测后记缓存（游戏装哪不会变）。"""
        em._game_dir_cache = r'Q:\cached'
        with mock.patch.object(em, '_game_dir_from_process',
                               return_value=None) as probe:
            self.assertEqual(em.default_game_dir(), r'Q:\cached')
            probe.assert_not_called()


class SteamHelpersTest(unittest.TestCase):
    """Steam 库解析：没装 Steam 的机器返回空表（不抛）。"""

    def test_returns_list_never_raises(self):
        self.assertIsInstance(em._steam_library_dirs(), list)

    def test_vdf_path_regex(self):
        """libraryfolders.vdf 的 "path" 行解析（正则抽出来直测）。"""
        TB = chr(9)
        text = ('"libraryfolders"' + NL + '{' + NL
                + TB + '"1"' + NL + TB + '{' + NL
                + TB + TB + '"path"' + TB + TB + '"D:'
                + BS + BS + 'SteamLibrary"' + NL
                + TB + '}' + NL + '}' + NL)
        pat = re.compile('"path"' + BS + 's+"([^"]+)"')
        got = [m.group(1) for m in pat.finditer(text)]
        self.assertEqual([g.replace(BS * 2, BS) for g in got],
                         ['D:' + BS + 'SteamLibrary'])


if __name__ == '__main__':
    unittest.main()
