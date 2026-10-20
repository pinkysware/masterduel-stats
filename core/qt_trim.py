# -*- coding: utf-8 -*-
r"""精简 Qt 依赖集（2026-10-18，为一键安装包 / full 便携包 / 补丁包共用）。

## 为什么要这一份

PySide6 整包含 **451MB**，而本项目只 import ``QtCore`` / ``QtGui`` /
``QtWidgets`` 三个模块（全项目 grep 实测：29/24/21 处，连 ``QtNetwork`` /
``QtSvg`` 都没有一处 import）。剩下的全是 Qt3D / QtCharts / QtWebEngine /
QML / 翻译 / 资源文件。

按白名单复制后 PySide6 降到 **57MB**，运行时合计从 ~470MB 降到 ~80MB，
压缩后的安装包从 130MB 降到 ~37MB（PowerShell Deflate 实测 39MB，lzma 应更小）。

⚠️ **白名单不是「代码 import 了什么」** —— 只留 Core/Gui/Widgets 三个 dll 会
``ImportError: DLL load failed``（2026-10-18 实测踩过）。PySide6 的这三个模块
在 Windows 上会**连带加载** Network/Svg/DBus/OpenGL/PrintSupport/Concurrent，
所以下面每个都留着。

⚠️ **删掉的两个大头**（全项目 grep 0 命中，实测可删）：
``Qt6WebEngineCore.dll`` **195MB**（整个包里最大的单文件）、``qml/`` **23MB**。
另外砍 ``translations/`` 14MB、``resources/`` 10MB、``opengl32sw.dll`` 20MB
（软件 OpenGL 兜底，纯 QWidget + raster 用不到；远程桌面/虚拟机若起不来再加回来）。

⚠️ **本模块只被打包脚本用**（不进包、不被运行期 import）——
所以放在 ``core/`` 但**不 import Qt**，纯路径与文件名清单。
"""
from __future__ import annotations

import os

#: 要保留的 Qt6 dll（**5 个**）。
#:
#: ★ BUG-B2A-18（2026-10）：原先这里还列着 ``Qt6Concurrent`` / ``Qt6DBus`` /
#:   ``Qt6OpenGL`` / ``Qt6OpenGLWidgets`` / ``Qt6PrintSupport`` —— 而这 5 个
#:   **同时在 :data:`QT_RETIRED_TOP` 里**（「只留 .pyd，dll 不保留」）。
#:   一个文件既在「要删」又在「要拷」的白名单里，补丁 / 增量场景必然误判。
#:   现在以退役名单为准：**这 5 个 dll 不再复制**（模块级断言盯着，见文件尾）。
#:
#: ★ 2026-10-19 用户拍板「确认是无用文件就删」：对应 pyd 也一并从
#:   :data:`QT_PYDS` 移除（**10 个 -> 6 个**，约省 9.6MB）。
#:   实测（无这 5 个 dll）：``QtDBus / QtOpenGL / QtOpenGLWidgets /
#:   QtPrintSupport`` 的 pyd 会 ``DLL load failed``；本项目**从不 import**
#:   它们（全项目 grep 0 命中，只有本文件与测试的白名单断言提到）。
#:   ``QtConcurrent.pyd`` 虽仍可 import（只依赖 Qt6Core），但同样零引用 ——
#:   一并删掉，少一个「能 import 但没人用」的迷惑项。
#:   ⚠️ 将来若真要用这几个模块：把 dll 从 ``QT_RETIRED_TOP`` 拿掉 + pyd 加回这里，
#:      模块级断言会立刻提示两边冲突。
QT_DLLS = ('Qt6Core', 'Qt6Gui', 'Qt6Widgets', 'Qt6Network', 'Qt6Svg')

#: 要保留的 PySide6 绑定模块（**6 个**，约 12.4MB）。
#: 只保留**本项目真正 import 的**：QtCore / QtGui / QtWidgets / QtNetwork /
#: QtSvg + 它们绕不开的依赖。别的模块 import 了也是死重量。
QT_PYDS = ('QtCore', 'QtGui', 'QtWidgets', 'QtNetwork', 'QtSvg')

#: PySide6 包里**除白名单外还要保留**的顶层文件
#: （``__init__.py`` 是入口；``pyside6.abi3.dll`` 是**绑定层的运行时本体** ——
#:   少了它 `import QtWidgets` 立刻 `DLL load failed`，2026-10-18 实测踩过；
#:   ``.json`` 是 wheel 元数据，删了 pip 会认不出包装了什么）
QT_KEEP_FILES = ('__init__.py', 'pyside6.abi3.dll')

#: 要保留的插件。值 = 文件名（不含目录）；``None`` = 该目录整个留下。
QT_PLUGINS = {
    'platforms':    ('qwindows.dll', 'qminimal.dll', 'qoffscreen.dll'),
    'styles':       ('qmodernwindowsstyle.dll',),
    #: 卡图是 webp/jpg/png、图标是 ico —— 这 8 个共 1.9MB，全留最稳
    'imageformats': ('qico.dll', 'qgif.dll', 'qjpeg.dll', 'qwebp.dll',
                     'qsvg.dll', 'qtiff.dll', 'qtga.dll', 'qwbmp.dll'),
    'iconengines':  None,
}

#: **确定退役**的顶层文件/目录（打包前先删，防「升级用户留着旧的大文件」）
#: ⚠️ 顺序很重要：先删后拷，不然删的可能是刚拷进去的新文件。
QT_RETIRED_TOP = (
    'Qt6WebEngineCore.dll',
    'Qt6WebEngineQuick.dll',
    'Qt63DAnimation.dll', 'Qt63DCore.dll', 'Qt63DExtras.dll',
    'Qt63DInput.dll', 'Qt63DLogic.dll', 'Qt63DQuick.dll',
    'Qt63DQuickAnimation.dll', 'Qt63DQuickExtras.dll',
    'Qt63DQuickInput.dll', 'Qt63DQuickLogic.dll',
    'Qt63DQuickRender.dll', 'Qt63DScene2D.dll', 'Qt63DScene3D.dll',
    'Qt63DRender.dll',
    'Qt6Charts.dll', 'Qt6ChartsQml.dll', 'Qt6DataVisualization.dll',
    'Qt6DataVisualizationQml.dll', 'Qt6Multimedia.dll',
    'Qt6MultimediaQuick.dll', 'Qt6MultimediaWidgets.dll',
    'Qt6Pdf.dll', 'Qt6PdfQuick.dll', 'Qt6Designer.dll',
    'Qt6DesignerComponents.dll', 'Qt6Help.dll', 'Qt6HttpServer.dll',
    'Qt6Location.dll', 'Qt6NetworkAuth.dll', 'Qt6Nfc.dll',
    'Qt6Bluetooth.dll', 'Qt6Graphs.dll', 'Qt6GraphsWidgets.dll',
    'Qt6Lottie.dll', 'Qt6LottieVectorImageGenerator.dll',
    'Qt6LottieVectorImageHelpers.dll', 'Qt6Quick.dll',
    'Qt6Quick3D.dll', 'Qt6Quick3DUtils.dll', 'Qt6QuickControls2.dll',
    'Qt6QuickDialogs2.dll', 'Qt6QuickLayouts.dll', 'Qt6QuickParticles.dll',
    'Qt6QuickShapes.dll', 'Qt6QuickTemplates2.dll', 'Qt6QuickTest.dll',
    'Qt6QuickTimeline.dll', 'Qt6QuickWidgets.dll', 'Qt6Quick3DAssetImport.dll',
    'Qt6Quick3DEffects.dll', 'Qt6Quick3DHelpers.dll',
    'Qt6Quick3DPhysics.dll', 'Qt6Quick3DRuntimeRender.dll',
    'Qt6SpatialAudio.dll', 'Qt6TextToSpeech.dll', 'Qt6VirtualKeyboard.dll',
    'Qt6WebChannel.dll', 'Qt6WebSockets.dll', 'Qt6WebView.dll',
    'Qt6WebViewQuick.dll', 'Qt6Xml.dll', 'Qt6StateMachine.dll',
    'Qt6Scxml.dll', 'Qt6SerialPort.dll', 'Qt6SerialBus.dll',
    'Qt6Positioning.dll',
    # ⚠️ 下面这 5 个 dll 与 `QT_DLLS` 曾是「既删又拷」的矛盾体（BUG-B2A-18）。
    #    注释说「只留 .pyd」，现在白名单也照此执行 —— **别把它们加回 QT_DLLS**。
    'Qt6PrintSupport.dll', 'Qt6OpenGL.dll', 'Qt6OpenGLWidgets.dll',
    'Qt6Concurrent.dll', 'Qt6DBus.dll',
    'opengl32sw.dll',
    'pyside6qml.abi3.dll',
)
QT_RETIRED_DIRS = ('qml', 'translations', 'resources', 'metatypes')

#: 打包时要排除的 PySide6 顶层文件模式（**按后缀**）
QT_SKIP_SUFFIX = ('.pyi', '.lib')


#: ★ BUG-B2A-18 的模块级断言：**同一个 dll 不能既在「要拷」又在「要删」清单里**。
#: 放在模块级（而不只在单测里）是因为打包脚本 import 本模块就会执行它 ——
#: 自相矛盾必须**当场响亮失败**，不能等打出一个少 dll 的安装包才发现。
#: （用 raise 而不是 `assert`：`python -O` 会把 assert 整段删掉。）
_RETIRED_STEMS = frozenset(os.path.splitext(n)[0] for n in QT_RETIRED_TOP)
_BOTH = tuple(sorted(set(QT_DLLS) & _RETIRED_STEMS))
if _BOTH:
    raise AssertionError(
        'core.qt_trim 自相矛盾：这些 dll 同时在 QT_DLLS（要拷）与 '
        'QT_RETIRED_TOP（要删）里：%s' % '、'.join(_BOTH))


def wanted_top_file(name: str) -> bool:
    """PySide6 顶层这个文件要不要复制（不含目录）。"""
    if name.endswith(QT_SKIP_SUFFIX):
        return False
    if name in QT_KEEP_FILES:
        return True
    stem = os.path.splitext(name)[0]
    if name.endswith('.dll'):
        return stem in QT_DLLS
    if name.endswith('.pyd'):
        return stem in QT_PYDS
    if name.endswith('.json'):
        return True                     # wheel 元数据（PySide6_*.json）
    return False


def wanted_plugin_file(subdir: str, name: str) -> bool:
    """插件目录里这个文件要不要复制。"""
    keep = QT_PLUGINS.get(subdir)
    if keep is None:
        return True                    # 该目录整个留
    return name in keep


def retired_paths(pyside_dir: str) -> list:
    """列出**确定存在**的退役文件/目录（供打包脚本先删后拷）。

    只列**磁盘上真有的** —— 免得每次打包都对一堆不存在的路径调 Delete。
    """
    out = []
    for name in QT_RETIRED_TOP:
        p = os.path.join(pyside_dir, name)
        if os.path.exists(p):
            out.append(p)
    for name in QT_RETIRED_DIRS:
        p = os.path.join(pyside_dir, name)
        if os.path.isdir(p):
            out.append(p)
    return out


def manifest(pyside_dir: str) -> list:
    """按白名单列出要复制的文件（相对 ``pyside_dir``）—— 单测直接锁它。"""
    out = []
    for root, dirs, files in os.walk(pyside_dir):
        rel = os.path.relpath(root, pyside_dir)
        if rel == '.':
            for f in sorted(files):
                if wanted_top_file(f):
                    out.append(f)
            dirs[:] = [d for d in sorted(dirs)
                       if d == 'plugins' or d in QT_RETIRED_DIRS]
            continue
        top = rel.split(os.sep)[0]
        if top != 'plugins':
            dirs[:] = []
            continue
        sub = rel.split(os.sep)[1] if len(rel.split(os.sep)) > 1 else None
        if sub is None:
            dirs[:] = [d for d in sorted(dirs) if d in QT_PLUGINS]
            continue
        if sub not in QT_PLUGINS:
            dirs[:] = []
            continue
        for f in sorted(files):
            if wanted_plugin_file(sub, f):
                out.append(os.path.join(rel, f).replace(os.sep, '/'))
        dirs[:] = []
    return sorted(out)
