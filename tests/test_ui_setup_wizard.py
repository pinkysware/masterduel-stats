# -*- coding: utf-8 -*-
"""ui.setup_wizard（首启引导窗）的测试 —— 2026-10-24「一键可用」。

照 `tests/test_ui_app.py` 的模式：QApplication 全进程一份（offscreen），
需要窗口的用例真建；判定逻辑用打桩。
"""
import os
import sys
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication                      # noqa: E402

from ui import setup_wizard as sw                              # noqa: E402

_APP = None


def _qapp():
    """复用进程内已有的 QApplication（全量跑时别的模块先建过 ——
    Qt 单例再建会抛 `libshiboken` 错）。"""
    global _APP
    inst = QApplication.instance()
    if inst is not None:
        return inst
    _APP = QApplication([])
    return _APP


class SetupNeededTest(unittest.TestCase):
    """首启判定：标志与当前数据目录绑定（换目录/重装会重新弹）。"""

    def test_unwritten_means_needed(self):
        with mock.patch.object(sw, '_dir_mark', return_value='D:/mydata'), \
             mock.patch('PySide6.QtCore.QSettings') as QS:
            inst = QS.return_value
            inst.value.return_value = None
            self.assertTrue(sw.setup_needed())

    def test_matching_mark_means_done(self):
        with mock.patch.object(sw, '_dir_mark', return_value='D:/mydata'), \
             mock.patch('PySide6.QtCore.QSettings') as QS:
            inst = QS.return_value
            inst.value.return_value = 'D:/mydata'
            self.assertFalse(sw.setup_needed())

    def test_stale_mark_from_old_dir_means_needed(self):
        """别的目录写的标志（旧值 1 / 别的路径）→ 要重新弹。"""
        for stale in (1, '1', 'C:/other/place'):
            with mock.patch.object(sw, '_dir_mark',
                                   return_value='D:/mydata'), \
                 mock.patch('PySide6.QtCore.QSettings') as QS:
                inst = QS.return_value
                inst.value.return_value = stale
                self.assertTrue(sw.setup_needed(), '旧标志 %r 不该挡引导' % stale)

    def test_maybe_show_skips_when_done(self):
        class App:
            _demo = False
        with mock.patch.object(sw, 'setup_needed', return_value=False):
            self.assertIsNone(sw.maybe_show_setup(App()))

    def test_maybe_show_skips_demo(self):
        class App:
            _demo = True
        with mock.patch.object(sw, 'setup_needed', return_value=True):
            self.assertIsNone(sw.maybe_show_setup(App()))


class WizardWindowTest(unittest.TestCase):
    """真建窗口（offscreen）：四行状态 + refresh 不抛。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _qapp()

    def test_builds_and_refreshes(self):
        wiz = sw.SetupWizard()
        self.addCleanup(wiz.close)
        self.assertEqual(wiz.row_data._title.text(), '数据目录')
        self.assertEqual(wiz.row_decks._title.text(), '卡组清单')
        wiz.refresh()          # 环境再拉一遍，不抛即可
        self.assertTrue(wiz.row_decks._detail.text())

    def test_finish_marks_done(self):
        with mock.patch('PySide6.QtCore.QSettings') as QS:
            wiz = sw.SetupWizard()
            self.addCleanup(wiz.close)
            wiz._finish()
            QS.return_value.setValue.assert_called_once()


if __name__ == '__main__':
    unittest.main()
