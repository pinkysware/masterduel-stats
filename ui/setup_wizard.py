# -*- coding: utf-8 -*-
"""**首启引导窗**（2026-10-24「一键可用」）——装完第一次打开的环境设置界面。

## 为什么需要

用户（机主 2026-10-24）：「首次启动后应该有一个环境设置界面，确保首次启动后
能正常运行。我需要的是安装后能一键使用的软件。」

以前第一次打开是「黑盒」：卡组清单要等联网播种、游戏目录能不能找到没人告诉
用户、采集器起没起也不可见 —— 出了问题（如 v1.1.3 的「搜不到卡组」）用户
只能干瞪眼。这个窗口把**关键环境状态**摆到第一次打开的第一屏：

1. 数据目录 —— 存哪、可改（复用设置页的 paths.ini 逻辑）
2. 游戏 —— 自动探测（进程反查 / Steam 库）；没找到可手动选（写 game_dir.ini）
3. 采集器 —— 心跳状态 + 「立即启动」
4. 卡组清单 —— 离线基线已灌多少条

## 触发与重开

* 首启：``ui.app.run()`` 里调 :func:`maybe_show_setup` —— QSettings
  ``setup/done`` **未写**（值与当前数据目录绑定，换目录/重装会重新弹，
  与 ``first_run._dir_mark`` 同款模式）时弹出，非模态（不打断悬浮窗）。
* 重开：设置页顶部「环境自检」按钮（`SettingsIndexPage`）。
* 「开始使用」与「稍后再说」都写标志 —— 用户明确表态过就不再打扰。
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea,
                               QFrame, QVBoxLayout, QWidget)

from core import paths, ui_state
from ui import theme, widgets
from ui.i18n import tr

#: QSettings 里「引导完成」的键（值 = `_dir_mark()`，换目录重装重新弹）
DONE_KEY = 'setup/done'


def _dir_mark() -> str:
    """与 `ui.first_run._dir_mark` 同款：当前数据目录的标识。"""
    try:
        from ui.first_run import _dir_mark as _m
        return _m()
    except Exception:                                      # noqa: BLE001
        return os.path.normcase(os.path.abspath(paths.DATA_DIR))


def setup_needed() -> bool:
    """第一次（在这个数据目录）打开吗 —— 引导窗该不该弹。"""
    from PySide6.QtCore import QSettings
    s = QSettings('masterduel', 'M7')
    return str(s.value(DONE_KEY) or '') != _dir_mark()


def mark_done() -> None:
    from PySide6.QtCore import QSettings
    QSettings('masterduel', 'M7').setValue(DONE_KEY, _dir_mark())


# ---------------------------------------------------------------- 行控件
class _CheckRow(QWidget):
    """一行状态：状态竖条 + 名称 + 详情 + 右侧动作按钮。

    （设置页的 ``SettingRow`` 在 dashboard 里、且带它自己的样式语义，
    这里是引导窗的轻量版 —— 结构一样但独立，别互相拖拽。）
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName('setupRow')
        self.setStyleSheet(
            '#setupRow { background: %s; border-radius: %dpx; }'
            % (theme.SURFACE2, theme.R_MD))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_SM)
        self._bar = widgets.bar(theme.WIN, width=theme.BAR_W)
        self._bar.setFixedHeight(theme.PAGE_ROW_H)
        lay.addWidget(self._bar)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)
        self._title = widgets.label(title, role='label',
                                    align=Qt.AlignVCenter | Qt.AlignLeft)
        self._title.setFont(widgets.font(theme.F_BODY))
        col.addWidget(self._title)
        self._detail = widgets.label('', role='muted',
                                     align=Qt.AlignVCenter | Qt.AlignLeft)
        self._detail.setFont(widgets.font(theme.F_BODY_SM))
        self._detail.setWordWrap(True)
        col.addWidget(self._detail)
        lay.addLayout(col, 1)
        self._btn = QPushButton()
        self._btn.setFont(widgets.font(theme.F_BODY_SM))
        self._btn.setFixedHeight(self._btn.fontMetrics().height() + 10)
        self._btn.hide()
        lay.addWidget(self._btn)

    def set_state(self, ok: bool, detail: str, tip: str = '') -> None:
        #: bar() 是裸 QWidget（没有 setColor）—— 换色重设样式表，与实现同式
        self._bar.setStyleSheet(
            'background: %s; border-radius: %dpx;'
            % (theme.WIN if ok else theme.WARN,
               max(theme.BAR_W // 2, 1)))
        self._detail.setText(detail)
        if tip:
            self._detail.setToolTip(tip)

    def set_action(self, text: str, fn) -> None:
        if not text:
            self._btn.hide()
            return
        self._btn.setText(text)
        self._btn.clicked.connect(fn)
        self._btn.show()


# ---------------------------------------------------------------- 向导
class SetupWizard(QDialog):
    """首启引导（非模态；关掉不退出程序）。"""

    def __init__(self, parent=None, on_refresh=None):
        super().__init__(parent)
        self.setWindowTitle(tr('欢迎使用 Master Duel Stats'))
        self.setModal(False)
        self.setMinimumWidth(560)
        self._on_refresh = on_refresh
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_LG, theme.S_LG, theme.S_LG, theme.S_LG)
        lay.setSpacing(theme.S_MD)

        title = widgets.label(tr('欢迎使用 Master Duel Stats'),
                              role='title',
                              align=Qt.AlignVCenter | Qt.AlignLeft)
        title.setFont(widgets.font(theme.F_STAT))
        lay.addWidget(title)
        intro = widgets.label(
            tr('只读统计工具：读游戏日志与公开内存，不写入、不注入。'
               '先确认下面几项都正常，之后就能自动记录对局。'),
            role='muted', align=Qt.AlignVCenter | Qt.AlignLeft)
        intro.setWordWrap(True)
        intro.setFont(widgets.font(theme.F_BODY))
        lay.addWidget(intro)
        lay.addWidget(widgets.hairline())

        #: 四行状态（顺序固定；数据在 refresh() 里填）
        self.row_data = _CheckRow(tr('数据目录'))
        self.row_game = _CheckRow(tr('游戏'))
        self.row_collector = _CheckRow(tr('采集器'))
        self.row_decks = _CheckRow(tr('卡组清单'))
        for r in (self.row_data, self.row_game, self.row_collector,
                  self.row_decks):
            lay.addWidget(r)

        lay.addStretch(1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        later = QPushButton(tr('稍后再说'))
        later.setFont(widgets.font(theme.F_BODY))
        later.clicked.connect(self._finish)
        btns.addWidget(later)
        go = QPushButton(tr('开始使用'))
        go.setFont(widgets.font(theme.F_BODY))
        go.setDefault(True)
        go.clicked.connect(self._finish)
        btns.addWidget(go)
        lay.addLayout(btns)

        self._wire_actions()
        self.refresh()

    # ---- 行为 ----
    def _wire_actions(self) -> None:
        self.row_data.set_action(tr('更改…'), self._pick_data_dir)
        self.row_game.set_action(tr('手动选择…'), self._pick_game_dir)
        self.row_collector.set_action(tr('立即启动'), self._start_collector)

    def refresh(self) -> None:
        """重拉环境状态（向导打开期间采集器起好了、游戏开了都会变绿）。"""
        st = ui_state.env_check()
        d = st.get('data_dir') or {}
        self.row_data.set_state(bool(d.get('ok')),
                                str(d.get('path') or ''),
                                tip=tr('对局数据库与记录都存这里。'))
        g = st.get('game') or {}
        if g.get('found'):
            detail = str(g.get('dir') or '')
        else:
            detail = tr('未找到（启动游戏后自动识别，或手动选择）')
        if g.get('running'):
            detail += ' · ' + tr('游戏运行中')
        self.row_game.set_state(bool(g.get('found')), detail)
        c = st.get('collector') or {}
        self.row_collector.set_state(
            bool(c.get('running')),
            tr('运行中') if c.get('running') else tr('未运行'),
            tip=str(c.get('at') or ''))
        m = st.get('meta_decks') or {}
        n = int(m.get('count') or 0)
        self.row_decks.set_state(
            n > 0,
            tr('%d 条（可搜索 / Tier 表）') % n if n > 0
            else tr('空 —— 等待联网更新或检查 data/meta_decks_seed.json'))

    # ---- 动作 ----
    def _pick_data_dir(self) -> None:
        """选数据目录：写 paths.ini，提示重启（与设置页同一条纪律）。"""
        start = paths.read_pref() or paths.DATA_DIR
        d = QFileDialog.getExistingDirectory(
            self, tr('选择「游戏对局数据」存放目录'), start)
        if not d:
            return
        d = os.path.normpath(d)
        if os.path.normcase(d) == os.path.normcase(paths.ASSET_DIR):
            QMessageBox.warning(self, tr('不能选这个目录'),
                                tr('不能选这个目录'))
            return
        if paths.write_pref(d):
            QMessageBox.information(
                self, tr('已保存'),
                tr('已保存。请重启程序后生效；现有数据不会自动搬迁。'))
        else:
            QMessageBox.warning(self, tr('保存失败'), tr('保存失败'))

    def _pick_game_dir(self) -> None:
        """手动选游戏安装目录（含 masterduel_Data 的那一层）。"""
        d = QFileDialog.getExistingDirectory(
            self, tr('选择游戏安装目录（含 masterduel_Data）'), '')
        if not d:
            return
        d = os.path.normpath(d)
        if not os.path.isdir(os.path.join(d, 'masterduel_Data')):
            QMessageBox.warning(
                self, tr('这个目录不对'),
                tr('这个目录不对：里面没有 masterduel_Data。'
                   '选游戏本体所在的那一层（和 masterduel.exe 同级）。'))
            return
        if paths.write_game_dir_pref(d):
            from core.memread import end_message as _em
            _em._game_dir_cache = None        # 让探测立即吃到新目录
            self.refresh()
        else:
            QMessageBox.warning(self, tr('保存失败'), tr('保存失败'))

    def _start_collector(self) -> None:
        try:
            from ui.collector_ctl import ensure_collector
            ensure_collector()
        except Exception:                                    # noqa: BLE001
            pass
        # 给它一两秒写心跳再刷新（QTimer 单发，别阻塞 UI）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2500, self.refresh)

    def _finish(self) -> None:
        mark_done()
        self.close()


# ---------------------------------------------------------------- 挂载
def maybe_show_setup(app) -> Optional[SetupWizard]:
    """首启（或换数据目录后）弹引导窗；返回窗口实例（调用方持引用防 GC）。

    demo 模式不弹（吃假数据，不该动真环境）。
    """
    if getattr(app, '_demo', False):
        return None
    try:
        if not setup_needed():
            return None
    except Exception:                                      # noqa: BLE001
        return None
    try:
        wiz = SetupWizard()
    except Exception:                                      # noqa: BLE001
        return None
    app._setup_wizard = wiz
    wiz.show()
    return wiz
