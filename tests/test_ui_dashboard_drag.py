# -*- coding: utf-8 -*-
"""★ 2026-10-22：看板「拖到最小高度后，每次拖拽高度都变大」的回归锁。

## 事故还原（用户实报）

「我把看板的高度拖到最小，然后每次拖拽看板，它的高度都会变大。
应该调整后保持不变。」

真机 `dash/size` 当时 = `QSize(1200, 620)`（正是 `MIN_H`）。

## 根因（三环相扣，实测复现）

1. **标题栏顶部 8px 与「上边缩放区」重叠**：
   `edges_at()` 的上边命中带是 `pos.y() <= RESIZE_MARGIN(8)`，
   标题栏几何 = `QRect(1, 1, 1198, 34)` → 标题栏 y∈[1,8] **同时**落在
   上边缩放区里。在那儿按下 → 进入**缩放态**（不是拖动态）。
2. **`_manual_resize` 残留**：
   缩放态的按下被 `eventFilter` 吞掉（`return True`），松开事件就发到
   **那个子控件**（标题栏）→ 走 `_title_release`，它只清 `_drag_from`，
   **`_manual_resize` 留着**。
3. **残留后拖看板 = 缩放**：
   下次在标题栏正常位置按住拖动 → `eventFilter` 看到
   `_manual_resize is not None` → 优先走 `_apply_manual_resize`
   （而不是 `_title_move`）→ 用「上次缩放的起始几何 + 本次位移」重算矩形
   → 窗口被**纵向缩放**，高度随拖动距离变大。

实测（修复前）：初始 620 高，残留态下往上拖 100px → **620 → 706**。

## 本文件盯住三件事

1. 标题栏**整条**都是拖动区 → 不进缩放态。
2. 缩放态的松手**必须清干净**（不留 `_manual_resize`）。
3. 拖动看板**不改变高度**（调整后保持不变）。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')      # 必须在 Qt import 之前

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    from ui import dashboard


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DashboardDragSizeTest(unittest.TestCase):
    """看板拖动 / 缩放的几何契约。"""

    def setUp(self):
        import tempfile
        from PySide6.QtCore import QSettings
        self._app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.mkdtemp(prefix='m7-dash-drag-')
        path = os.path.join(self._tmp, 'ui.ini')
        self._orig_settings = dashboard.settings
        dashboard.settings = lambda: QSettings(path, QSettings.IniFormat)
        import ui.i18n as _i18n
        self._orig_i18n = _i18n._SETTINGS_FACTORY
        _i18n._SETTINGS_FACTORY = staticmethod(
            lambda: QSettings(path, QSettings.IniFormat))
        self.win = dashboard.DashboardWindow()
        self.win.resize(dashboard.MIN_W, dashboard.MIN_H)
        self.win.show()
        QApplication.processEvents()

    def tearDown(self):
        import shutil
        import ui.i18n as _i18n
        self.win.deleteLater()
        dashboard.settings = self._orig_settings
        _i18n._SETTINGS_FACTORY = self._orig_i18n
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------------------------------------------------------- 工具
    def _press(self, widget, local, glob):
        widget.mousePressEvent = widget.mousePressEvent  # no-op（保持原样）
        ev = QMouseEvent(QEvent.MouseButtonPress, local, glob,
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(widget, ev)
        QApplication.processEvents()

    def _release(self, widget, local, glob):
        ev = QMouseEvent(QEvent.MouseButtonRelease, local, glob,
                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(widget, ev)
        QApplication.processEvents()

    def _move(self, widget, local, glob):
        ev = QMouseEvent(QEvent.MouseMove, local, glob,
                         Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(widget, ev)
        QApplication.processEvents()

    def _title_click(self, y, dy=0.0):
        """在标题栏 y 处按下 -> 松开（可选带 dy 位移的移动）。"""
        bar = self.win._title_bar
        local = QPointF(400, y)
        glob = self.win.mapToGlobal(QPoint(400, y)).toPointF()
        self._press(bar, local, glob)
        if dy:
            self._move(bar, local, glob + QPointF(0, dy))
        self._release(bar, local, glob + QPointF(0, dy))

    # ---------------------------------------------------------- 用例
    def test_title_bar_press_never_enters_resize(self):
        """🔴 标题栏**顶部**（缩放区里）按下 → 不进缩放态。

        原来标题栏 y∈[1,8] 落在上边缩放区 → 按下就变成缩放，
        用户以为在拖标题栏挪窗口，实际在缩放窗口。

        ⚠️ 判据要看**按下那一瞬间**的状态（`eventFilter` 里刚设上的），
        不能只看松开之后 —— 松开时 `_title_press` 的清理也会把它清掉，
        那样就分不清是哪一处修复在起作用了。
        """
        bar = self.win._title_bar
        local = QPointF(400, 3)
        glob = self.win.mapToGlobal(QPoint(400, 3)).toPointF()
        self._press(bar, local, glob)
        # ★ 按下这一刻：`_title_press` 还没跑（它在事件到达标题栏时），
        #   但 `eventFilter` 已经判过边缘 —— 缩放态若被建立，此刻就在。
        self.assertIsNone(self.win._manual_resize,
                         '标题栏顶部按下进了缩放态（应该只是拖动）')

    def test_event_filter_does_not_treat_title_bar_as_edge(self):
        """🔴 `eventFilter` 必须把标题栏**整条**排除在边缘判定之外。

        这是「标题栏顶部 8px 与上边缩放区重叠」那一条的直接锁：
        事件来自标题栏时**根本不该**做 `edges_at()` 判定。
        （`eventFilter` 对标题栏直接 `return False` 放行。）
        """
        bar = self.win._title_bar
        # 标题栏顶部那几像素**确实**落在上边缩放区（前提成立）
        self.assertEqual(self.win.edges_at(QPoint(400, 3)), Qt.TopEdge,
                         '前提变了：标题栏顶部不再落在缩放区')
        # 但事件来自标题栏时，不该进缩放态
        local = QPointF(400, 3)
        glob = self.win.mapToGlobal(QPoint(400, 3)).toPointF()
        self._press(bar, local, glob)
        self.assertIsNone(self.win._manual_resize,
                         '标题栏顶部按下进了缩放态 —— eventFilter 没收紧')

    def test_release_clears_the_resize_state(self):
        """🔴 松开必须清干净缩放态（原来 `_title_release` 只清 `_drag_from`）。

        ⚠️ **直接调 `_title_release`** —— 走完整「按下->松开」的话，
        按下那一步的 `_title_press` 清理会**先把残留清掉**，
        于是这条用例就测不到 release 的清理了（写这条时踩过）。
        """
        # 手动塞一个残留态，模拟「上次缩放没清干净」
        self.win._manual_resize = {'edges': Qt.TopEdge,
                                   'press': QPoint(0, 0),
                                   'geom': self.win.geometry()}
        self.win._title_release(None)
        self.assertIsNone(self.win._manual_resize,
                         '松开后缩放态残留 —— 下次拖看板会被当成缩放')

    def test_title_press_clears_stale_resize_state(self):
        """🔴 进拖动态前也要清残留（`_title_press` 的职责）。

        ⚠️ **直接调 `_title_press`** —— 走完整事件流的话，
        `eventFilter` 那一处（另一条修复）会先清掉残留，
        于是这条用例就测不到 `_title_press` 的清理了。
        """
        self.win._manual_resize = {'edges': Qt.TopEdge,
                                   'press': QPoint(0, 0),
                                   'geom': self.win.geometry()}
        # 构造一个最小的 press 事件（`_title_press` 只看 button）
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(400, 17),
                         QPointF(0, 0), Qt.LeftButton, Qt.LeftButton,
                         Qt.NoModifier)
        self.win._title_press(ev)
        self.assertIsNone(self.win._manual_resize,
                         '进拖动态时没清残留缩放态')

    def test_drag_does_not_change_height(self):
        """🔴 用户实报：拖看板**高度不该变**（调整后保持不变）。

        修复前：残留态下往上拖 100px → 高度 620 → 706。
        """
        h0 = self.win.height()
        self._title_click(17, dy=-100)
        self.assertEqual(self.win.height(), h0,
                         '拖看板把高度改了：%d -> %d' % (h0, self.win.height()))

    def test_drag_after_stale_resize_keeps_height(self):
        """🔴 带残留态拖看板，高度也必须不变（这正是用户遇到的路径）。"""
        self.win._manual_resize = {'edges': Qt.TopEdge,
                                   'press': QPoint(0, 0),
                                   'geom': self.win.geometry()}
        h0 = self.win.height()
        bar = self.win._title_bar
        local = QPointF(400, 17)
        glob = self.win.mapToGlobal(QPoint(400, 17)).toPointF()
        self._press(bar, local, glob)
        self._move(bar, local, glob + QPointF(0, -100))
        self.assertEqual(self.win.height(), h0,
                         '残留态下拖看板把高度改了：%d -> %d'
                         % (h0, self.win.height()))

    def test_title_bar_is_whole_drag_zone(self):
        """标题栏**整条**（上到下）都是拖动区，不参与边缘缩放判定。"""
        bar = self.win._title_bar
        for y in (1, 4, 8, 16, 30):
            self.win._manual_resize = None
            self._title_click(y)
            self.assertIsNone(self.win._manual_resize,
                             '标题栏 y=%d 按下进了缩放态' % y)

    def test_edge_resize_still_works(self):
        """🔴 修 bug **不能**把缩放功能弄坏：底边拖动仍能改高度。"""
        self.win.resize(dashboard.MIN_W, 800)
        h0 = self.win.height()
        rail = self.win.childAt(10, self.win.height() - 3)
        self.assertIsNotNone(rail)
        local = QPointF(10, self.win.height() - 3)
        glob = self.win.mapToGlobal(QPoint(10, self.win.height() - 3)).toPointF()
        self._press(rail, local, glob)
        self.assertIsNotNone(self.win._manual_resize,
                             '底边按下没进缩放态 —— 缩放功能被弄坏了')
        self._move(rail, local, glob + QPointF(0, 100))
        self.assertGreater(self.win.height(), h0,
                           '底边往下拖高度没变大 —— 缩放功能被弄坏了')
        self._release(rail, local, glob + QPointF(0, 100))
        self.assertIsNone(self.win._manual_resize,
                         '松开后缩放态没清干净')


if __name__ == '__main__':
    unittest.main()
