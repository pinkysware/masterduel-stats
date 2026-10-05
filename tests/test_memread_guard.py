# -*- coding: utf-8 -*-
"""红线守卫单测 —— 「只读内存」约束的可执行形式。

用户 2026-09-12 的要求：

    保证工具不会修改 GameAssembly.dll，也不会向 Master Duel 进程注入第三方 DLL。

这里把这些话变成断言。任何一条被破坏，测试立刻红：

* 业务代码里出现写内存 / 注入类 API 或禁用权限位 -> 红
* ``SafeProcess`` 长出写方法、或 ``__init__`` 能传 access -> 红
* 访问掩码里混入任何一个禁用位 -> 红
* 用非 ``'rb'`` 模式打开游戏文件 -> 红
"""
from __future__ import annotations

import inspect
import io
import os
import unittest

from core.memread import (
    FORBIDDEN_ACCESS_BITS,
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    READ_ONLY_ACCESS,
    SafeProcess,
    assert_read_only,
    find_module,
    open_game_file,
)
from core.memread import redline

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSourceScan(unittest.TestCase):
    """静态扫描：业务代码不许出现红线 API / 权限位。"""

    def test_no_forbidden_api_in_sources(self):
        hits = redline.scan_violations(PROJECT_ROOT)
        self.assertEqual(
            hits, [],
            '业务代码里出现了被禁止的 API 或权限位（红线）：\n  '
            + '\n  '.join('%s:%d %s' % h for h in hits[:20])
        )

    def test_scan_actually_scans(self):
        """扫描器本身得能看到文件，否则上面的断言是假绿。"""
        files = list(redline._iter_py_files(PROJECT_ROOT))
        self.assertGreaterEqual(len(files), 5, '扫描到的文件太少，路径可能不对')

    def test_exempt_list_is_minimal(self):
        """豁免名单必须保持极小 —— 否则红线可以被随便绕过。"""
        self.assertLessEqual(
            len(redline.EXEMPT_FILES), 2,
            '豁免文件过多：%s' % sorted(redline.EXEMPT_FILES)
        )
        self.assertIn('redline.py', redline.EXEMPT_FILES)

    def test_forbidden_lists_not_empty(self):
        self.assertGreater(len(redline.FORBIDDEN_APIS), 5)
        self.assertGreater(len(redline.FORBIDDEN_ACCESS_NAMES), 3)
        self.assertGreater(len(redline.FORBIDDEN_STRUCT_FIELDS), 0)


class TestForbiddenStructFields(unittest.TestCase):
    """禁区字段：引擎里那个「本局卡牌全集」（双方卡组，含未公开的卡）。

    2026-09-12 实机定性后用户拍板：整体按禁区处理 —— 回放里它含超出公开范围的卡，
    实时里它只是块被复用的临时内存；而唯一合规用法（只取我方）已被证伪。
    """

    def test_field_name_is_listed(self):
        self.assertIn('cardExistBuffer', redline.FORBIDDEN_STRUCT_FIELDS)

    def test_ui_dir_is_in_scan_dirs(self):
        """★ M7：`ui/` 必须在扫描范围里 —— 它和 core/ 同进程，不能是盲区。"""
        self.assertIn('ui', redline.SCAN_DIRS)

    def test_scan_flags_violation_inside_ui(self):
        """负向验证：`ui/` 下的违规同样要被抓到（别只加进常量、扫描却不认）。"""
        bad = os.path.join(PROJECT_ROOT, 'ui', '_redline_probe.py')
        os.makedirs(os.path.dirname(bad), exist_ok=True)
        with io.open(bad, 'w', encoding='utf-8', newline='') as f:
            f.write('x = fields["cardExistBuffer"]\n')
        try:
            hits = redline.scan_violations(PROJECT_ROOT)
            self.assertTrue(any(h[0].replace('\\', '/').endswith('ui/_redline_probe.py')
                                for h in hits),
                            'ui/ 里的红线没被抓到：%r' % (hits,))
        finally:
            os.remove(bad)

    def test_scan_flags_the_field(self):
        """负向验证：随便哪个源码文件写了这个字段名，扫描必须报出来。"""
        probe_dir = os.path.join('tmp', '_scan_probe')
        abs_dir = os.path.join(PROJECT_ROOT, probe_dir)
        os.makedirs(abs_dir, exist_ok=True)
        bad = os.path.join(abs_dir, 'bad_probe.py')
        with io.open(bad, 'w', encoding='utf-8', newline='') as f:
            f.write('ptr = fields["cardExistBuffer"].offset\n')
        try:
            hits = redline.scan_violations(PROJECT_ROOT, dirs=(probe_dir,))
            self.assertTrue(hits, '扫描器没抓到禁区字段，红线形同虚设')
            self.assertEqual(hits[0][2], 'cardExistBuffer')
            self.assertEqual(hits[0][1], 1, '行号应指向第 1 行')
        finally:
            os.remove(bad)
            os.rmdir(abs_dir)

    def test_scan_ignores_unrelated_name(self):
        """反向对照：别的字段名（同区间的兄弟字段）不该被误报。"""
        probe_dir = os.path.join('tmp', '_scan_probe_ok')
        abs_dir = os.path.join(PROJECT_ROOT, probe_dir)
        os.makedirs(abs_dir, exist_ok=True)
        good = os.path.join(abs_dir, 'ok_probe.py')
        with io.open(good, 'w', encoding='utf-8', newline='') as f:
            f.write('ptr = fields["cardRareBufferPtr"].offset\n')
        try:
            hits = redline.scan_violations(PROJECT_ROOT, dirs=(probe_dir,))
            self.assertEqual(hits, [])
        finally:
            os.remove(good)
            os.rmdir(abs_dir)

    def test_scan_is_substring_based_on_purpose(self):
        """有意为之的"宁严勿宽"：连 ``<禁区字段>Size`` 这种兄弟字段名也会被报出来。

        扫描器是朴素子串匹配（跟禁 API 那套一致），好处是难绕过；
        代价是同一个区间里名字带前缀的字段（例如容量字段 ``...Size``）也会命中。
        这条测试把这个取舍**固定下来** —— 哪天想改成词边界匹配，必须是有意识的决定。
        """
        self.assertIn('cardExistBufferSize',
                      'x = fields["cardExistBufferSize"]')
        self.assertIn('cardExistBuffer',
                      'x = fields["cardExistBufferSize"]')


class TestAccessMask(unittest.TestCase):
    """访问掩码只能是最小的只读组合。"""

    def test_mask_value(self):
        self.assertEqual(READ_ONLY_ACCESS, PROCESS_VM_READ | PROCESS_QUERY_INFORMATION)
        self.assertEqual(READ_ONLY_ACCESS, 0x0410)

    def test_mask_has_no_forbidden_bits(self):
        for name, bit in FORBIDDEN_ACCESS_BITS.items():
            self.assertEqual(READ_ONLY_ACCESS & bit, 0,
                             '访问掩码混入了禁用权限位：%s' % name)

    def test_assert_read_only_accepts_our_mask(self):
        assert_read_only(READ_ONLY_ACCESS)  # 不该抛

    def test_assert_read_only_rejects_write_bits(self):
        for name, bit in FORBIDDEN_ACCESS_BITS.items():
            with self.assertRaises(Exception, msg='应该拒绝含 %s 的掩码' % name):
                assert_read_only(READ_ONLY_ACCESS | bit)

    def test_assert_read_only_rejects_other_masks(self):
        for bad in (0x001F0FFF, 0, PROCESS_VM_READ, PROCESS_QUERY_INFORMATION):
            with self.assertRaises(Exception, msg='应该拒绝掩码 0x%X' % bad):
                assert_read_only(bad)


class TestSafeProcessShape(unittest.TestCase):
    """SafeProcess 的**形状**：只能读，不能写。"""

    def test_no_write_like_methods(self):
        banned = ('write', 'write_memory', 'alloc', 'virtual_alloc', 'inject',
                  'create_thread', 'remote', 'patch', 'protect')
        for name in banned:
            self.assertFalse(
                hasattr(SafeProcess, name),
                'SafeProcess 不应该有 %s() 方法' % name
            )

    def test_init_takes_no_access_argument(self):
        """__init__ 不接受 access 参数 —— 调用方想申请写权限也没有入口。"""
        params = list(inspect.signature(SafeProcess.__init__).parameters)
        self.assertEqual(params[:2], ['self', 'pid'])
        self.assertNotIn('access', params)

    def test_public_api_is_read_only(self):
        for name in dir(SafeProcess):
            if name.startswith('_'):
                continue
            self.assertNotIn('writ', name.lower(),
                             '公开接口里不该出现 write 系方法：%s' % name)

    def test_access_property(self):
        """实例上暴露的 access 恒为只读掩码。"""
        self.assertEqual(SafeProcess.access.fget.__name__, 'access')
        import core.memread.process as proc
        self.assertEqual(proc.READ_ONLY_ACCESS & proc.FORBIDDEN_ACCESS_BITS['PROCESS_VM_WRITE'], 0)


class TestGameFileGuard(unittest.TestCase):
    """游戏安装目录的文件只能以 'rb' 打开。"""

    def setUp(self):
        self.tmp = os.path.join(PROJECT_ROOT, 'tmp', '_test_game_file.bin')
        with io.open(self.tmp, 'wb') as f:
            f.write(b'MZ\x00\x00')

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_read_binary_ok(self):
        with open_game_file(self.tmp, 'rb') as f:
            self.assertEqual(f.read(2), b'MZ')

    def test_write_mode_rejected(self):
        for mode in ('wb', 'r+b', 'w', 'a', 'ab', 'r+', 'w+b'):
            with self.assertRaises(ValueError, msg="模式 %r 应该被拒绝" % mode):
                open_game_file(self.tmp, mode)

    def test_protected_names(self):
        self.assertTrue(redline.is_protected_game_file('C:/x/GameAssembly.dll'))
        self.assertTrue(redline.is_protected_game_file('C:/x/gameassembly.dll'))
        self.assertTrue(redline.is_protected_game_file('C:/x/global-metadata.dat'))
        self.assertFalse(redline.is_protected_game_file('C:/x/masterduel.db'))

    def test_allowed_modes(self):
        self.assertEqual(redline.ALLOWED_GAME_FILE_MODES, ('rb',))


class TestFindModule(unittest.TestCase):
    def test_find_by_name(self):
        mods = [('a.dll', 1, 2), ('GameAssembly.dll', 0x180000000, 99)]
        self.assertEqual(find_module(mods, 'gameassembly.dll')[1], 0x180000000)

    def test_missing(self):
        self.assertIsNone(find_module([('a.dll', 1, 2)], 'nope.dll'))


if __name__ == '__main__':
    unittest.main()
