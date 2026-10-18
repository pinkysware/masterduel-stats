# -*- coding: utf-8 -*-
"""版本常量与 Qt 精简白名单的单测（2026-10-18）。

两条线：

1. `core.version` —— 语义化比较（**`1.0.10 > 1.0.9` 那个字符串比较坑必须锁**）；
2. `core.qt_trim` —— 白名单的**正向 + 反向**断言，且清单里每个文件都要在真
   site-packages 里存在（防白名单写错字母 → 装出缺 dll 的包）。

⚠️ 后半类用例**依赖本机 site-packages 里有 PySide6** —— 没有就 skip
（与 `test_deck_guess` 依赖 ygocdb 卡表同一套路：缺真数据不误报）。
"""
import os
import unittest

from core import version
from core import qt_trim

#: 本机 site-packages（打包机才有；没有 PySide6 的环境跳过「真目录」类用例）
_SITE = ('C:/Users/Administrator/.workbuddy/binaries/python/versions/'
         '3.13.12/Lib/site-packages')
_PYSIDE = os.path.join(_SITE, 'PySide6')
_HAS_PYSIDE = os.path.isdir(_PYSIDE)


class ParseTest(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(version.parse('1.0.7'), (1, 0, 7))
        self.assertEqual(version.parse('10.20.30'), (10, 20, 30))

    def test_string_compare_trap(self):
        """★ `1.0.10` 必须**大于** `1.0.9`（字符串比较下为假，这是本测试存在的理由）。"""
        self.assertEqual(version.compare('1.0.10', '1.0.9'), 1)
        self.assertEqual(version.compare('1.0.9', '1.0.10'), -1)
        self.assertTrue(version.parse('1.0.10') > version.parse('1.0.9'))

    def test_v_prefix(self):
        """GitHub tag 是 `v1.0.8` 这种形态。"""
        self.assertEqual(version.parse('v1.0.8'), (1, 0, 8))
        self.assertEqual(version.compare('v1.0.8', '1.0.7'), 1)

    def test_short_and_garbage(self):
        self.assertEqual(version.parse('1.0'), (1, 0, 0))
        self.assertEqual(version.parse('1.0.8-beta'), (1, 0, 8))
        self.assertEqual(version.parse('1.0.8+build5'), (1, 0, 8))

    def test_bad_input_never_raises(self):
        for bad in (None, '', '   ', 'abc', '.', '...', 'x.y.z', '-'):
            self.assertEqual(version.parse(bad), (0, 0, 0), repr(bad))

    def test_compare_three_ways(self):
        self.assertEqual(version.compare('1.0.7', '1.0.7'), 0)
        self.assertEqual(version.compare('1.0.6', '1.0.7'), -1)
        self.assertEqual(version.compare('1.0.8', '1.0.7'), 1)

    def test_is_newer(self):
        cur = version.__version__
        major, minor, patch = version.parse(cur)
        self.assertTrue(version.is_newer('%d.%d.%d' % (major, minor, patch + 1)))
        self.assertFalse(version.is_newer(cur))
        self.assertFalse(version.is_newer('%d.%d.%d' % (major, minor, patch - 1)))

    def test_version_shape(self):
        """`__version__` 必须是 X.Y.Z（打包脚本与 README 都按这个形状拼名字）。"""
        self.assertRegex(version.__version__, r'^\d+\.\d+\.\d+$')


@unittest.skipUnless(_HAS_PYSIDE, '本机没有 PySide6（打包机才有）')
class QtTrimManifestTest(unittest.TestCase):
    """白名单在真 PySide6 目录上的正/反断言。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = qt_trim.manifest(_PYSIDE)

    def test_not_empty(self):
        self.assertTrue(self.manifest, '白名单跑出空清单 —— 路径不对？')

    def test_every_entry_exists(self):
        """★ 清单里每个文件都必须在磁盘上存在（防白名单写错字母）。"""
        missing = [n for n in self.manifest
                   if not os.path.exists(os.path.join(_PYSIDE, n))]
        self.assertEqual(missing, [], '白名单里有不存在的文件')

    def test_has_core_qt(self):
        for n in ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
                  'Qt6Network.dll', 'Qt6Svg.dll', 'Qt6DBus.dll',
                  'Qt6Concurrent.dll', 'Qt6OpenGL.dll',
                  'Qt6OpenGLWidgets.dll', 'Qt6PrintSupport.dll',
                  'QtCore.pyd', 'QtGui.pyd', 'QtWidgets.pyd',
                  'QtNetwork.pyd', 'QtSvg.pyd', 'QtDBus.pyd',
                  'QtConcurrent.pyd', 'QtOpenGL.pyd',
                  'QtOpenGLWidgets.pyd', 'QtPrintSupport.pyd',
                  '__init__.py'):
            self.assertIn(n, self.manifest, n)

    def test_has_binding_runtime(self):
        """★ `pyside6.abi3.dll` 是绑定层本体 —— 漏了 `import QtWidgets` 直接
        `ImportError: DLL load failed`（2026-10-18 实测踩过，锁住别再漏）。"""
        self.assertIn('pyside6.abi3.dll', self.manifest)

    def test_has_platform_plugin(self):
        """★ 少 `qwindows.dll` 装上去就「no Qt platform plugin」（import 阶段查不出）。"""
        for n in ('plugins/platforms/qwindows.dll',
                  'plugins/styles/qmodernwindowsstyle.dll',
                  'plugins/imageformats/qwebp.dll',
                  'plugins/imageformats/qjpeg.dll',
                  'plugins/imageformats/qico.dll'):
            self.assertIn(n, self.manifest, n)

    def test_no_retired_fat(self):
        """反向断言：退役的大件一个都不能在。"""
        for n in self.manifest:
            base = os.path.basename(n)
            self.assertFalse(base.startswith('Qt3D'), n)
            self.assertFalse(base.startswith('Qt63D'), n)
            self.assertFalse(base.startswith('Qt6Web'), n)
            self.assertFalse(base.startswith('Qt6Quick'), n)
            self.assertFalse(base.startswith('Qt6Charts'), n)
            self.assertFalse(base.startswith('Qt6Multimedia'), n)
            self.assertFalse(base.startswith('Qt6Pdf'), n)
            self.assertFalse(base.startswith('Qt6Data'), n)
            self.assertFalse(n.endswith('.pyi'), n)
            self.assertFalse(n.endswith('.lib'), n)
            self.assertFalse(n.startswith('qml/'), n)
            self.assertFalse(n.startswith('translations/'), n)
            self.assertFalse(n.startswith('resources/'), n)
            self.assertFalse(n.startswith('metatypes/'), n)
        self.assertNotIn('Qt6WebEngineCore.dll', self.manifest)
        self.assertNotIn('opengl32sw.dll', self.manifest)

    def test_total_size_sane(self):
        """精简后 PySide6 应远小于整包的 451MB（上限 120MB，防白名单被人为放宽）。"""
        total = sum(os.path.getsize(os.path.join(_PYSIDE, n))
                    for n in self.manifest)
        mb = total / 1048576.0
        self.assertLess(mb, 120, 'PySide6 精简后 %.1fMB，超过上限' % mb)

    def test_retired_paths_exist_on_disk(self):
        """退役清单里的路径在本机都真存在（WebEngine/qml 是大头，必须列进去）。"""
        retired = qt_trim.retired_paths(_PYSIDE)
        names = [os.path.basename(p) for p in retired]
        self.assertIn('Qt6WebEngineCore.dll', names)
        self.assertIn('qml', names)
        for p in retired:
            self.assertTrue(os.path.exists(p), p)


if __name__ == '__main__':
    unittest.main()
