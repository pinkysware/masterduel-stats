# -*- coding: utf-8 -*-
"""打包脚本的静态锁（2026-10-18）。

三条锁，都是**已经踩过的坑**：

1. **补丁包绝不能含 `data/`** —— 那里面有用户手填的卡组名、刷过的卡表、
   以及对局记录；补丁包含它就等于每次更新都覆盖用户数据。
2. **安装包的卸载段不能有 `RMDir /r "$INSTDIR\\data"`** —— 实测会连对局记录
   一起删，且静默卸载不问。2026-10-18 修过一次，这条防它被改回去。
3. **安装包的 `SecCore` 必须给四个用户文件 `SetOverwrite off`** ——
   否则重装会静默覆盖用户改过的 `deck_name_cn.csv` 等（实测复现）。

⚠️ 这些是 `packaging/*.nsi` / `*.py` 的文本断言，不真打包 —— 秒级。
"""
import io
import os
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(PROJ, 'packaging')
NSI = os.path.join(PACKAGING, 'installer.nsi')

#: ⚠️ 发布仓**不带 `packaging/`**（打包脚本是本地构建工具，不进公开仓库）。
#: 与「依赖 ygocdb 卡表的测试」同一套路：缺文件就 skip，别误报红。
#: 主仓有 `packaging/` 时照常全跑。
_HAS_PACKAGING = os.path.isdir(PACKAGING)


def _read(path):
    with io.open(path, encoding='utf-8', newline='') as f:
        return f.read()


@unittest.skipUnless(_HAS_PACKAGING, '发布仓不带 packaging/（本地构建工具）')
class PatchPackageTest(unittest.TestCase):
    """`build_patch.py` 的补丁清单绝不含 data/。"""

    def setUp(self):
        import sys
        if PROJ not in sys.path:
            sys.path.insert(0, PROJ)

    def test_patch_entries_exclude_data(self):
        from packaging.build_patch import patch_entries
        entries = patch_entries()
        self.assertTrue(entries, '补丁清单为空 —— 发布仓路径不对？')
        leaked = [e for e in entries
                  if e == 'data' or e.startswith('data/')]
        self.assertEqual(leaked, [], '补丁包混进了 data/（会覆盖用户数据）')

    def test_patch_entries_exclude_runtime(self):
        from packaging.build_patch import patch_entries
        entries = patch_entries()
        for bad in ('python/', 'tests/', 'assets/', 'MDStats.exe',
                    'start.bat', 'launch.bat'):
            hit = [e for e in entries
                   if e == bad.rstrip('/') or e.startswith(bad)]
            self.assertEqual(hit, [], '补丁包混进了 %s' % bad)

    def test_patch_entries_include_code(self):
        from packaging.build_patch import patch_entries
        entries = patch_entries()
        #: ⚠️ 补丁清单走的是**发布仓**（`SRC_REPO`），而 `core/version.py` 是
        #: 2026-10-18 新加的 —— 发布仓同步之前那些新文件本来就不在里面。
        #: 所以这里只锁**发布仓一定有的老文件**，免得测试变成「必须先同步」的门。
        for need in ('ui/app.py', 'core/storage.py', 'tools/duel_ui.py'):
            self.assertIn(need, entries)


@unittest.skipUnless(_HAS_PACKAGING, '发布仓不带 packaging/（本地构建工具）')
class InstallerScriptTest(unittest.TestCase):
    """`installer.nsi` 的两条数据安全锁。"""

    @classmethod
    def setUpClass(cls):
        cls.nsi = _read(NSI)

    def test_uninstall_does_not_delete_data_unconditionally(self):
        """★ 卸载段不能**无条件**删 `$INSTDIR\\data`（会删光战绩，且静默不问）。

        「问一句、用户点 Yes 才删」是可以的（数据去留该由用户定）；
        违规的是**静默路径上也删**。所以判据是顺序：
        `IfSilent` 的兜底跳转必须出现在第一条 `RMDir /r "$INSTDIR\\data"` 之前。
        """
        idx = self.nsi.find('Section "Uninstall"')
        self.assertGreater(idx, 0, '找不到 Uninstall 段')
        tail = self.nsi[idx:]
        dele = tail.find('RMDir /r "$INSTDIR\\data"')
        if dele < 0:
            return                      # 完全不删 = 最安全
        guard = tail.find('IfSilent')
        self.assertGreaterEqual(
            guard, 0,
            '卸载段有删 data 的动作，但没有 IfSilent 守卫 —— 静默卸载会连问都不问')
        self.assertLess(guard, dele,
                        'IfSilent 守卫必须在删 data 之前（静默路径不能走到删除）')
        #: 删除那行前面必须有确认框（用户点 Yes 才删）
        before = tail[:dele]
        self.assertIn('MessageBox', before, '删 data 前没有确认框')

    def test_uninstall_keeps_silent_safe(self):
        """静默卸载（/S）必须**无条件保留**数据（不能弹框、不能删）。"""
        idx = self.nsi.find('Section "Uninstall"')
        tail = self.nsi[idx:]
        self.assertIn('IfSilent', tail,
                      '卸载段少了 IfSilent 分支 —— 静默卸载可能会删数据')

    def test_upgrade_preserves_user_data_files(self):
        """★ 安装段必须对这四个月用户文件 `SetOverwrite off`（只补缺失）。"""
        for name in ('deck_name_cn.csv', 'card_names.json',
                     'mdm_deck_cards.json', 'site_deck_names.json'):
            self.assertIn('/x %s' % name, self.nsi,
                          'File /r 没排除 %s —— 重装会覆盖用户改过的版本' % name)
            self.assertIn('File "${SRC}\\data\\%s"' % name, self.nsi,
                          '缺少 %s 的「只补缺失」那条' % name)
        self.assertIn('SetOverwrite off', self.nsi)
        self.assertIn('SetOverwrite on', self.nsi)

    def test_outdir_overridable(self):
        """`OutFile` 必须能用 `/DOUTDIR=` 改 —— 否则语法检查会覆盖正式产物。

        2026-10-18 真踩过：拿 `/DSRC=<测试目录>` 编译一次，把 130MB 的
        正式 Setup-1.0.7.exe 覆盖成了 415KB 的空壳。
        """
        self.assertIn('!ifndef OUTDIR', self.nsi)
        self.assertIn('OutFile "${OUTDIR}', self.nsi)


@unittest.skipUnless(_HAS_PACKAGING, '发布仓不带 packaging/（本地构建工具）')
class QtTrimSharedTest(unittest.TestCase):
    """三个打包脚本必须共用同一份白名单（别抄第二份）。"""

    def test_scripts_use_shared_helper(self):
        for name in ('build_installer.py', 'build_full_zip.py'):
            text = _read(os.path.join(PACKAGING, name))
            self.assertIn('copy_pyside6', text,
                          '%s 没走 core.qt_trim_copy 的白名单复制' % name)
            self.assertNotIn("copytree(s, site + '/PySide6')", text,
                             '%s 还在整包复制 PySide6' % name)

    def test_scripts_read_version_module(self):
        for name in ('build_installer.py', 'build_full_zip.py', 'build_patch.py'):
            text = _read(os.path.join(PACKAGING, name))
            self.assertIn("core', 'version.py'", text,
                          '%s 没读 core/version.py（版本号会漂）' % name)


if __name__ == '__main__':
    unittest.main()
