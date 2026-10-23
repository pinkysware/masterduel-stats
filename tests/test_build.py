# -*- coding: utf-8 -*-
"""打包脚本的静态锁（2026-10-18）。

三条锁，都是**已经踩过的坑**：

1. **补丁包绝不能含 `data/`** —— 那里面有用户手填的卡组名、刷过的卡表、
   以及对局记录；补丁包含它就等于每次更新都覆盖用户数据。
2. **安装包的卸载段不能有 `RMDir /r "$INSTDIR\\data"`** —— 实测会连对局记录
   一起删，且静默卸载不问。2026-10-18 修过一次，这条防它被改回去。
3. **安装包的 `SecCore` 必须给四个用户文件 `SetOverwrite off`** ——
   否则重装会静默覆盖用户改过的 `deck_name_cn.csv` 等（实测复现）。

⚠️ 这些是 `build/*.nsi` / `*.py` 的文本断言，不真打包 —— 秒级。
"""
import io
import os
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJ, 'build')
NSI = os.path.join(BUILD, 'installer.nsi')

#: ⚠️ 发布仓**不带 `build/`**（打包脚本是本地构建工具，不进公开仓库）。
#: 与「依赖 ygocdb 卡表的测试」同一套路：缺文件就 skip，别误报红。
#: 主仓有 `build/` 时照常全跑。
_HAS_BUILD = os.path.isdir(BUILD)


def _read(path):
    with io.open(path, encoding='utf-8', newline='') as f:
        return f.read()


@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/（本地构建工具）')
class PatchPackageTest(unittest.TestCase):
    """`build_patch.py` 的补丁清单绝不含 data/。"""

    def setUp(self):
        import sys
        if PROJ not in sys.path:
            sys.path.insert(0, PROJ)

    def test_patch_entries_exclude_user_data(self):
        """★ 补丁包**绝不带用户数据**（会覆盖用户手填的表 / 对局记录）。

        ⚠️ 2026-10-23 放宽：程序的**自带素材**（`ASSET_DIRS` 白名单：图标目录）
        允许进包 —— 否则用补丁升级的用户看不到 v1.1.2 新加的 DC 图标。
        但**用户数据**（`deck_name_cn.csv` / `card_names.json` / `archive/` …）
        仍然一个都不许进。
        """
        from build.build_patch import ASSET_DIRS, patch_entries
        entries = patch_entries()
        self.assertTrue(entries, '补丁清单为空 —— 发布仓路径不对？')
        allowed = tuple(d + '/' for d in ASSET_DIRS)
        leaked = [e for e in entries
                  if (e == 'data' or e.startswith('data/'))
                  and not e.startswith(allowed)]
        self.assertEqual(leaked, [], '补丁包混进了用户数据')

    def test_patch_entries_never_include_user_data_files(self):
        """逐个盯死**用户数据文件**：它们进了包就是覆盖用户的东西。"""
        from build.build_patch import patch_entries
        entries = set(patch_entries())
        for bad in ('data/deck_name_cn.csv', 'data/card_names.json',
                    'data/card_names.csv', 'data/masterduel.db',
                    'data/deck_name_cn.xlsx'):
            self.assertNotIn(bad, entries, '补丁包混进了用户数据：%s' % bad)
        #: `archive/`（对局记录）与 `card_art/`（缓存）也必须没有
        for e in entries:
            self.assertFalse(e.startswith('data/archive/'),
                             '补丁包混进了对局记录：%s' % e)
            self.assertFalse(e.startswith('data/card_art/'),
                             '补丁包混进了卡图缓存：%s' % e)

    def test_patch_entries_include_asset_whitelist(self):
        """自带素材白名单里的图标必须进包（否则新素材跟着代码发了却没图）。"""
        from build.build_patch import ASSET_DIRS, patch_entries
        entries = set(patch_entries())
        icons = [e for e in entries
                 if e.startswith(tuple(d + '/' for d in ASSET_DIRS))]
        self.assertTrue(icons, '自带素材一个都没进包')
        self.assertIn('data/mode_icons/dc.webp', entries,
                      'v1.1.2 的 DC 图标没进补丁包')

    def test_patch_entries_exclude_runtime(self):
        from build.build_patch import patch_entries
        entries = patch_entries()
        for bad in ('python/', 'tests/', 'assets/', 'MDStats.exe',
                    'start.bat', 'launch.bat'):
            hit = [e for e in entries
                   if e == bad.rstrip('/') or e.startswith(bad)]
            self.assertEqual(hit, [], '补丁包混进了 %s' % bad)

    def test_patch_entries_include_code(self):
        from build.build_patch import patch_entries
        entries = patch_entries()
        #: ⚠️ 补丁清单走的是**发布仓**（`SRC_REPO`），而 `core/version.py` 是
        #: 2026-10-18 新加的 —— 发布仓同步之前那些新文件本来就不在里面。
        #: 所以这里只锁**发布仓一定有的老文件**，免得测试变成「必须先同步」的门。
        for need in ('ui/app.py', 'core/storage.py', 'tools/duel_ui.py'):
            self.assertIn(need, entries)


@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/（本地构建工具）')
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


@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/（本地构建工具）')
class QtTrimSharedTest(unittest.TestCase):
    """三个打包脚本必须共用同一份白名单（别抄第二份）。"""

    def test_scripts_use_shared_helper(self):
        for name in ('build_installer.py', 'build_full_zip.py'):
            text = _read(os.path.join(BUILD, name))
            self.assertIn('copy_pyside6', text,
                          '%s 没走 core.qt_trim_copy 的白名单复制' % name)
            self.assertNotIn("copytree(s, site + '/PySide6')", text,
                             '%s 还在整包复制 PySide6' % name)

    def test_scripts_read_version_module(self):
        for name in ('build_installer.py', 'build_full_zip.py', 'build_patch.py'):
            text = _read(os.path.join(BUILD, name))
            self.assertIn("core', 'version.py'", text,
                          '%s 没读 core/version.py（版本号会漂）' % name)


@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/（本地构建工具）')
class InstallerPathsTest(unittest.TestCase):
    """★ BUG-B0-01：四个本机绝对路径不能再写死在源码里。"""

    def setUp(self):
        self.src = _read(os.path.join(BUILD, 'build_installer.py'))

    def test_no_hardcoded_machine_paths(self):
        for bad in ('E:/LMStudio', 'C:/Users/Administrator', 'D:/ProgramData'):
            self.assertNotIn(bad, self.src, '硬编码了本机路径：%s' % bad)

    def test_paths_are_overridable(self):
        for env, opt in (('MD_PUB_REPO', '--pub-repo'),
                         ('MD_BUILD_ROOT', '--build-root'),
                         ('MD_SITE_PACKAGES', '--site-packages'),
                         ('MD_MAKENSIS', '--makensis'),
                         ('MD_EMBED_ZIP', '--embed-zip')):
            self.assertIn(env, self.src, '缺环境变量 %s' % env)
            self.assertIn(opt, self.src, '缺参数 %s' % opt)

    def test_missing_path_gives_guidance(self):
        self.assertIn('SystemExit', self.src)
        self.assertIn('找不到', self.src)



if __name__ == '__main__':
    unittest.main()

@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/')
class UninstallJumpAndPidCheckTest(unittest.TestCase):
    """★ BUG-B0-02 / B0-04（2026-10-20）：两个**会静默做错事**的脚本写法。

    B0-02：卸载段的 MessageBox 用相对跳转（`IDNO +2` / `IfFileExists ... 0 +4`）——
            中间插一行就跳错，最坏情况是用户点「否」却删了战绩。
    B0-04：`launch_installed.bat` 的 pid 判活读的是 **JSON 文件整行**，
            且用 `find "%OLDPID%"` 子串匹配 —— 永远判成「没在跑」，
            于是每次启动都开第二个采集器（两个进程写同一个库）。
    """

    def _code_lines(self, text, comment_prefix=';'):
        """去掉注释行（注释里提到旧写法是允许的，只有真代码才算）。"""
        out = []
        for line in text.split(chr(10)):
            s = line.strip()
            if s.startswith(comment_prefix) or s.startswith('rem '):
                continue
            out.append(line)
        return chr(10).join(out)
    def test_uninstall_has_no_relative_jumps(self):
        nsi = self._code_lines(_read(NSI))
        body = nsi[nsi.index('Section "Uninstall"'):]             if 'Section "Uninstall"' in nsi else nsi
        import re
        #: 相对跳转 = `+N` / `-N` 作为跳转目标（`IDNO +2` / `0 +4`）
        bad = re.findall(r'(?:IDNO|IDYES|IfFileExists "[^"]*")\s+[+-]\d+',
                         body)
        self.assertEqual(bad, [], '卸载段还有相对跳转：%r（改行就跳错）' % bad)

    def test_uninstall_uses_named_labels(self):
        nsi = _read(NSI)
        self.assertIn('data_instdir_skip:', nsi)
        self.assertIn('data_localappdata_skip:', nsi)
        self.assertIn('IDNO data_instdir_skip', nsi)
        self.assertIn('IDNO data_localappdata_skip', nsi)

    def test_launcher_uses_collector_running(self):
        bat = self._code_lines(_read(os.path.join(BUILD,
                                                  'launch_installed.bat')))
        self.assertIn('collector_ctl.collector_running()', bat,
                      'pid 判活必须走 collector_running()（唯一实现）')
        self.assertNotIn('tasklist', bat, '不许再手搓 tasklist 判活')
        self.assertNotIn('set /p OLDPID', bat,
                         'pid 文件是 JSON，读整行去比永远匹配不上')

    def test_launcher_is_ascii(self):
        """launch_installed.bat 必须是 ASCII（cmd 代码页，见文件头注释）。"""
        raw = open(os.path.join(BUILD, 'launch_installed.bat'), 'rb').read()
        raw.decode('ascii')

    def test_launcher_survives_path_with_spaces(self):
        """★ 装到 `C:\\Program Files\\...`（默认目录，**带空格**）必须能启动。

        根因（2026-10-24 用户实机首报，已复现）：`for /f ... in (`命令`)` 会
        二次解析命令行，带引号的 exe 路径在空格处被截断：

            'C:\\Program' 不是内部或外部命令
            → DATA_DIR 空 → [ERROR] could not determine the duel data folder

        **每个装到默认位置的用户都打不开程序**。修法：先重定向到临时文件再读。
        这里锁两件事：① 不许再用反引号命令替换；② 必须走「重定向到文件」。
        """
        bat = self._code_lines(_read(os.path.join(BUILD,
                                                  'launch_installed.bat')))
        self.assertNotIn('in (`', bat,
                         '命令替换（反引号）在带空格路径下会被截断 —— '
                         '改用重定向到临时文件再 for /f 读文件')
        self.assertIn('> "%MD_TMP%"', bat,
                      'DATA_DIR 必须先重定向到文件再读')
        self.assertIn('in ("%MD_TMP%")', bat,
                      'DATA_DIR 必须从文件读，不是从命令读')
        #: 判活那条同样受影响（同一个 bug 的第二个受害者）
        self.assertNotIn("in (`\"%PY%\" -c \"import sys;sys.path.insert(0,'.');"
                         "from ui import collector_ctl", bat)
        self.assertIn('> "%MD_TMP2%"', bat, 'RUNNING 也要走文件')

