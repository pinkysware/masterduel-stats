# -*- coding: utf-8 -*-
"""M7 主控 —— 装配 + 定时探针 + 设置菜单（`docs/M7计划.md` §五 S1）。

职责很窄：

* 起 ``QApplication``、套样式、**双开互斥**（审查 P3-4）
* 一个定时器跑 :func:`core.ui_state.probe`（**便宜探针**，一条 SQL，审查 P2-3）——
  **值变了才拉全量快照**，拉到就推给悬浮窗
* 设置：开机自启开关 / 显示语言 / **调试模式（demo 战绩热切换）** / 退出

数据与写操作都在 ``core/``，这一层不写 SQL、不 import ``core.storage`` 之外的东西。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QLockFile, QProcess, QSettings, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QPainter, QPainterPath,
                           QPen)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QMenu,
                               QMessageBox, QLineEdit, QPushButton,
                               QRadioButton, QScrollArea, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from core import paths
from core import ui_actions, ui_state
from core import deck_guess
from core.deck_images import DeckImages
from core.storage import Storage
from core.ui_demo import DemoSandboxStorage, demo_snapshot
from ui import theme, widgets
from ui.floating import (MAX_ROWS, FloatingWindow, WindowButton, md_app_icon,
                        max_window_width)
from ui.fonts import prepare_app
from ui.i18n import tr
from ui.selector import MAX_SUGGEST, DeckSelectorPanel

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = paths.data_path('.duel_ui.lock')
#: 语言显示名 / 关窗选项的**单一来源在 `ui.theme`**（09-26 设置页优化挪的），
#: 这里按需 import —— 别再在本文件重建第二份。

#: 卡组覆盖表（`data/deck_name_cn.csv`）—— 与 `tools/fetch_meta_decks.py` 的 `CN_CSV` 同一个文件
CN_CSV = paths.resolve('deck_name_cn.csv')

#: 胜负修正菜单（末尾那项 `None` = 清除结论；标签唯一来源 = `ui_actions.RESULT_LABELS`）
RESULT_ACTIONS = ui_actions.RESULT_CHOICES + ((None, '清除结果'),)


def close_action():
    """设置里的「当窗口关闭时」—— 返回 ``(动作, 是否已明确设置过)``。

    ⚠️ ``False`` = 从没设置过 → 点「×」要**先弹一次提示**（用户要求「第一次给提示」），
    并把用户的选择记下来（带「记住我的选择」勾选）。
    """
    raw = settings().value('ui/close_action')
    if raw in [k for k, _ in theme.CLOSE_ACTIONS]:
        return str(raw), True
    return theme.DEFAULT_CLOSE_ACTION, False


def settings() -> QSettings:
    return QSettings('masterduel', 'M7')


def app_entry_point() -> str:
    """开机自启要跑什么：``tools/duel_ui.py``。"""
    return os.path.join(PROJ, 'tools', 'duel_ui.py')


def pythonw() -> str:
    """优先 pythonw.exe —— 不然自启动会闪一个黑框（本机踩过这种坑）。"""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return cand if os.path.exists(cand) else exe


def restart_command(argv=None):
    """重启界面要跑什么 —— ``(exe, args, cwd)``。**纯函数**（单测不真起进程）。

    用 `pythonw`（重启不该闪黑框）+ **本轮进程的原样参数**（`--demo` / `--db` /
    `--interval` 都跟着走）；工作目录钉在项目根（`sys.argv[0]` 是相对的
    `tools/duel_ui.py`）。
    """
    return pythonw(), list(sys.argv if argv is None else argv), PROJ


def demo_from_settings() -> bool:
    """QSettings 里的调试模式开关（`ui/demo`）—— 上次会话留下的状态。"""
    return str(settings().value('ui/demo', '')).lower() in ('true', '1')


class CloseDialog(QDialog):
    """「点击关闭后」—— 参考图那种：**两个单选项 + 「不再提示」+ 取消/确认**。

    ⚠️ 2026-09-15 用户实测「× 点了没用」+「你的文字太啰嗦」：
    原来用 `QMessageBox`，两个按钮**本身就是动作**（点了立刻执行、没有确认），
    文案又是两行长句；现在改成：默认选「退出主程序」，确认才执行，
    下面一个「不再提示」勾选（勾了就落库、以后直接照此执行）。

    ⚠️ **无边框 + 自绘暗色标题**（用户 2026-09-15 点头）：Windows 原生标题栏是**白的**，
    和我们整套暗色主题不搭 → 改成 `FramelessWindowHint` + 半透明圆角 + 自己画标题行
    （标题居中 + 右上 ×），并且**可以拖着走**（按在非控件区域即可）。Esc 仍然 = 取消。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('关闭悬浮窗'))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(theme.qss())
        self.setFixedWidth(300)          # 用户 2026-09-15：「把关闭悬浮窗的 ui 做小」
        self._drag_from = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_SM)

        # ---- 自绘标题行：标题居中 + 右上 ×（左边补等宽留白，标题才是真居中）
        head = QHBoxLayout()
        head.setSpacing(0)
        title = widgets.label(tr('点击关闭后'), 'body', Qt.AlignCenter)
        title.setFont(widgets.font(theme.F_BODY, QFont.Weight.Medium))
        self.btn_close = WindowButton('close')
        self.btn_close.setToolTip(tr('取消'))
        self.btn_close.clicked.connect(self.reject)
        pad = QWidget()
        pad.setFixedWidth(self.btn_close.width())
        head.addWidget(pad)
        head.addStretch(1)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.btn_close)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(theme.S_MD)
        self.rb_tray = widgets.DotRadio(tr('隐藏到任务栏托盘'))
        self.rb_quit = widgets.DotRadio(tr('退出主程序'))
        self.rb_quit.setChecked(True)          # 参考图里默认选「退出」
        grp = QButtonGroup(self)
        grp.addButton(self.rb_tray)
        grp.addButton(self.rb_quit)
        row.addWidget(self.rb_tray)
        row.addStretch(1)
        row.addWidget(self.rb_quit)
        lay.addLayout(row)

        self.cb_remember = widgets.TickBox(tr('不再提示 · 之后直接按此执行'))
        lay.addWidget(self.cb_remember)

        # ⚠️ 不用 `QDialogButtonBox`：它会按平台习惯重排（Windows 是 [确认][取消]），
        # 而参考图是 **[取消][确认]** —— 这里自己排，顺序说了算。
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_cancel = QPushButton(tr('取消'))
        self.btn_cancel.setFixedWidth(72)
        self.btn_cancel.clicked.connect(self.reject)
        foot.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton(tr('确认'))
        self.btn_ok.setFixedWidth(72)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        foot.addWidget(self.btn_ok)
        lay.addLayout(foot)

    # ------------------------------------------------------- 自绘外观/拖动
    def paintEvent(self, ev):                                   # noqa: N802
        """圆角暗色底 + 发丝描边（无边框窗得自己画，和悬浮窗/面板同一套）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    def mousePressEvent(self, ev):                              # noqa: N802
        """按在非控件区域可以**拖走**这个对话框（无边框窗的标配）。"""
        if ev.button() == Qt.LeftButton:
            self._drag_from = (ev.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):                               # noqa: N802
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        self._drag_from = None
        super().mouseReleaseEvent(ev)

    def choice(self) -> str:
        """选的是哪个动作（`tray` / `quit`）。"""
        return 'quit' if self.rb_quit.isChecked() else 'tray'

    def remember(self) -> bool:
        return bool(self.cb_remember.isChecked())


def new_deck_names(meta_names, csv_path):
    """meta 清单里**不在覆盖表**的卡组名（= 设置页要请你补中文名的那批）。

    ⚠️ 覆盖表的匹配靠**站内英文名**那一列（`data/deck_name_cn.csv` 的「站内英文名」）；
    「隐藏=Y」的行也算已收录（用户主动并掉/隐藏的，不再问）。
    """
    from core import deck_csv
    try:
        header, rows = deck_csv.read(csv_path)
    except Exception:
        return sorted({n for n in meta_names if n})
    idx = deck_csv.index_map(header)
    known = {deck_csv.get(r, idx, '站内英文名') for r in rows}
    return sorted({n for n in meta_names if n and n not in known})


class NewDecksDialog(QDialog):
    """新卡组勾选 + 填中文名（用户 2026-09-15 选的做法「乙」）。

    每一行 = [图] [站内英文名] [中文名输入框] [勾选框]；确认后
    **写进 `data/deck_name_cn.csv`**（站内英文名 + 中文名 + 备注），再跑
    `fetch_meta_decks.py --cn-only` 把表贴进库。
    """

    def __init__(self, names, images, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('新卡组'))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(theme.qss())
        self.setFixedWidth(460)
        self.rows = []                       # [(name, 勾选框, 输入框)]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_SM)
        head = QHBoxLayout()
        head.addWidget(widgets.label(tr('这些卡组不在覆盖表里 —— 勾上并填中文名'),
                                     'label'))
        head.addStretch(1)
        self.btn_close = WindowButton('close')
        self.btn_close.clicked.connect(self.reject)
        head.addWidget(self.btn_close)
        lay.addLayout(head)

        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S_XS)
        for name in names:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(theme.S_XS)
            img = ''
            try:
                img = images.resolve(name)
            except Exception:
                pass
            rl.addWidget(widgets.thumb_label(img, theme.THUMB))
            lab = widgets.label(name, 'body')
            lab.setFixedWidth(200)
            rl.addWidget(lab)
            edit = QLineEdit()
            edit.setPlaceholderText(tr('中文名（可留空）'))
            rl.addWidget(edit, 1)
            cb = widgets.TickBox('')
            cb.setChecked(True)              # 默认全勾：新上的基本都是要收的
            rl.addWidget(cb)
            box.addWidget(row)
            self.rows.append((name, cb, edit))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(host)
        area.setFixedHeight(min(len(names), 8) * 34 + 12)
        lay.addWidget(area)

        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_cancel = QPushButton(tr('取消'))
        btn_cancel.clicked.connect(self.reject)
        foot.addWidget(btn_cancel)
        btn_ok = QPushButton(tr('加入覆盖表'))
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        foot.addWidget(btn_ok)
        lay.addLayout(foot)

    def paintEvent(self, ev):                                   # noqa: N802
        """圆角暗色底 + 发丝描边（🔴 无边框窗不画就是**整块透明** ——
        2026-10-03 用户实拍：「获取新卡组名」跑完后的确认弹窗字叠字；
        09-21 在 FetchDecksDialog 修过同一个坑，这里两份定义一起补）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    def picked(self):
        """勾上并填了名字的：[(站内英文名, 中文名)]。"""
        return [(name, edit.text().strip())
                for name, cb, edit in self.rows if cb.isChecked()]


class SettingsDialog(QDialog):
    """设置：开机自启 / 显示语言 / **当窗口关闭时** / 调试模式（demo 战绩）。"""

    def __init__(self, parent=None, on_demo_changed=None,
                 on_fetch_decks=None, on_restart=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('设置'))
        # 用户 2026-09-15：原生白色标题栏和暗色主题不搭 → 无边框 + 自绘（同 `CloseDialog`）
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(360)
        self.setStyleSheet(theme.qss())
        self._on_demo_changed = on_demo_changed   # None = 只写设置不热切（测试用）
        self._on_fetch_decks = on_fetch_decks     # None = 独立/测试：只提示
        self._on_restart_cb = on_restart          # None = 独立/测试：只提示
        self._drag_from = None

        # 无边框 → 自己给一个右上角的 ×（和关闭框/面板同一套按钮）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_MD, theme.S_SM, theme.S_MD, theme.S_MD)
        outer.setSpacing(theme.S_XS)
        head = QHBoxLayout()
        head.addStretch(1)
        self.btn_close = WindowButton('close')
        self.btn_close.clicked.connect(self.reject)
        head.addWidget(self.btn_close)
        outer.addLayout(head)
        form = QFormLayout()
        outer.addLayout(form)
        self.cb_auto = QCheckBox(tr('开机自启'))
        self.cb_auto.setChecked(ui_actions.is_autostart())
        self.cb_auto.toggled.connect(self._on_auto)
        form.addRow(tr('开机自启'), self.cb_auto)

        self.cb_lang = QComboBox()
        # 单一来源在 ui.theme（本文件不再建第二份）—— 2026-10-19 修掉这里的 NameError
        for key in theme.LANGS:
            self.cb_lang.addItem(theme.LANG_NAMES.get(key, key), key)
        cur = str(settings().value('ui/lang', 'cn'))
        idx = self.cb_lang.findData(cur)
        self.cb_lang.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_lang.currentIndexChanged.connect(self._on_lang)
        form.addRow(tr('显示语言'), self.cb_lang)

        # ---- 当窗口关闭时（2026-09-15 用户定：参考图里那个下拉）
        self.cb_close = QComboBox()
        for key, name in theme.CLOSE_ACTIONS:
            self.cb_close.addItem(tr(name), key)
        cur_close, _explicit = close_action()
        idx = self.cb_close.findData(cur_close)
        self.cb_close.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_close.currentIndexChanged.connect(self._on_close_action)
        form.addRow(tr('当窗口关闭时'), self.cb_close)

        # ---- 卡组清单（用户 2026-09-15：M7 的「获取新卡组名」做到设置里）
        self.btn_fetch = QPushButton(tr('获取新卡组名'))
        self.btn_fetch.clicked.connect(self._on_fetch)
        form.addRow(tr('卡组清单'), self.btn_fetch)
        self.fetch_status = widgets.label('', 'label')
        self.fetch_status.setWordWrap(True)
        form.addRow('', self.fetch_status)

        # ---- 重启界面（2026-09-16 用户定）
        # 为什么要有它：改了 UI 代码就得重启进程才生效，而窗口是 `Qt.Tool`（不进任务栏）、
        # 「关」还可能是「隐藏到托盘」—— 手动重启对用户太绕。一键最省事。
        self.btn_restart = QPushButton(tr('重启界面'))
        self.btn_restart.clicked.connect(self._on_restart)
        form.addRow(tr('界面'), self.btn_restart)

        # ---- 调试模式（2026-09-15 用户定）：吃 demo 战绩调样式，不碰真库
        self.cb_demo = QCheckBox(tr('界面吃 demo 战绩（不读不写真库）'))
        self.cb_demo.setChecked(demo_from_settings())
        self.cb_demo.toggled.connect(self._on_demo)
        form.addRow(tr('调试模式'), self.cb_demo)

        hint = widgets.label(tr('语言只影响卡名/卡组名的显示；匹配一律走卡 id。\n'
                             '调试模式立即生效（不用重启）；demo 的统计数字是设计稿\n'
                             '那套（65.6% / 21-11 / 77.8%），一眼可辨；demo 下点对手\n'
                             '卡组格只提示、不标注。'), 'label')
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.addRow('', hint)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        form.addRow(btns)

        self._status = widgets.label('', 'label')
        self._status.setWordWrap(True)
        form.addRow('', self._status)

    def _on_auto(self, on: bool) -> None:
        ok = ui_actions.set_autostart(on, target=pythonw(),
                                      arguments=app_entry_point(),
                                      workdir=PROJ)
        if ok:
            self._status.setText(tr('已开启开机自启') if on else tr('已关闭开机自启'))
        else:
            self._status.setText(tr('设置失败 —— 没能写入「启动」目录'))
            self.cb_auto.blockSignals(True)
            self.cb_auto.setChecked(ui_actions.is_autostart())   # 回滚显示
            self.cb_auto.blockSignals(False)

    def _on_lang(self, _idx: int) -> None:
        settings().setValue('ui/lang', self.cb_lang.currentData())

    def _on_close_action(self, _idx: int) -> None:
        settings().setValue('ui/close_action', self.cb_close.currentData())

    def _on_restart(self) -> None:
        """点了「重启界面」——真正的实现在 `MasterDuelUiApp`（要起进程 + 退自己）。"""
        if self._on_restart_cb is None:
            self._status.setText(tr('（独立模式：这里会重启界面）'))
            return
        self._status.setText(tr('正在重启界面…'))
        self._on_restart_cb()

    def _on_fetch(self) -> None:
        """跑「获取新卡组名」——真正的实现在 `MasterDuelUiApp`（要联网 + QProcess）。"""
        if self._on_fetch_decks is None:
            self.fetch_status.setText(tr('（独立模式：这里会联网抓榜单）'))
            return
        self._on_fetch_decks(self)

    # ------------------------------------------------------- 外观/拖动
    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._drag_from = (ev.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):                               # noqa: N802
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        self._drag_from = None
        super().mouseReleaseEvent(ev)

    def _on_demo(self, on: bool) -> None:
        if self._on_demo_changed is not None:
            self._on_demo_changed(on)          # app 负责持久化 + 热切换
        else:                                  # 独立/测试：只落设置
            settings().setValue('ui/demo', bool(on))


class MasterDuelUiApp(object):
    """把 Storage / 快照 / 悬浮窗 装起来跑。"""

    def __init__(self, argv=None, db_path: Optional[str] = None,
                 interval_ms: int = 2000, demo: bool = False) -> None:
        self.qapp = QApplication(argv or sys.argv)
        self.qapp.setApplicationName('Master Duel 战绩')
        self.qapp.setQuitOnLastWindowClosed(False)
        # ⚠️ **必须在建任何控件之前**注册自带字体（`data/fonts/`，见 ui/fonts.py）
        # ★ 注册字体 + 上 QSS 走**唯一入口**（与出图脚本同一条路径，见 ui/fonts）
        prepare_app(self.qapp)
        #: ★ **应用图标设在 QApplication 上**（2026-10-10 用户实测：
        #:   「任务栏的 UI 还是 python 默认图标」）。之前只设了悬浮窗的
        #:   `setWindowIcon`，而悬浮窗是 `Qt.Tool`（有意不进任务栏）——
        #:   任务栏 / Alt-Tab / 窗口列表取的是**进程级**图标，没设就显示
        #:   Python 的默认图标。设在这里，**所有窗口 + 任务栏 + Alt-Tab** 同一枚。
        from ui.floating import md_app_icon
        self.qapp.setWindowIcon(md_app_icon())

        # 调试模式（--demo 参数 或 设置里勾了）：不启探针；界面吃 demo 战绩，
        # Storage 换成**沙盒** —— 参考数据（meta 清单/卡组图）只读真库，
        # 标注写入内存（重启还原），真库永不被写（2026-09-15 用户定）。
        self._db_path = db_path
        self._demo = bool(demo) or demo_from_settings()
        self.storage = self._make_storage(self._demo)
        self.last_key = None

        self.win = self._build_floating()

        # M5 反推参照库（99 副）——拿不到就 None，选择器面板静默没有「建议」
        try:
            self._guess_index, self._guess_groups = deck_guess.load_index()
        except Exception:
            self._guess_index, self._guess_groups = None, None
        self._panel = None                     # 当前打开的选择器面板（防 GC）
        self._dash_win = None                 # 看板主窗口（同样要持引用防 GC）
        self._sug_cache = {}                   # opp_revealed_ids -> 建议候选（防每拍重算）
        self._imgs = None                      # DeckImages（懒建）

        self.timer = QTimer()
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.tick)

        # ---- 首跑播种（2026-10-10）：库里没有 meta_decks 就后台联网抓一次 ----
        # 发布包**故意不带** `masterduel.db`（运行期数据），首次打开时卡组清单
        # / 选择器 / Tier 表全是空的，卡组图也没有。这里补上那条链路：
        # 后台跑 `fetch_meta_decks.py`（清单 + 下图），静默失败、不阻塞界面。
        # 详见 `ui/first_run.py` 的模块文档。
        self._firstrun_proc = None
        #: 「重启界面」期间为 True —— 那时**不许**收采集器（见 `restart_ui`）。
        self._restarting = False
        #: 采集器已经收过了（`quit` 与 `aboutToQuit` 各叫一次，只收一次）
        self._collector_stopped = False
        try:
            from ui.first_run import ensure_meta_decks
            ensure_meta_decks(self)
        except Exception as _e:                           # noqa: BLE001
            #: ⚠️ **必须留痕，不能静默吞掉**（2026-10-10 实机事故）：
            #:   这里原本是裸 `pass`，首跑播种抛的 `TypeError` 被它吃掉，
            #:   界面上**什么迹象都没有**（不报错、不弹窗），只有
            #:   `data/archive/first_run.log` 停在「开始」那一行、
            #:   `meta_decks` 永远 0 行 —— 表现为「卡组选择器搜不到任何卡组」，
            #:   极难定位。写进日志，下次有人报这个现象就能一眼看到。
            try:
                from ui.first_run import _log
                _log('[first_run] 启动失败（被吞掉的异常）：%r' % (_e,))
            except Exception:                            # noqa: BLE001
                pass

        # ---- 退出兜底：QApplication 退出必经这里（2026-10-10）----
        #: 标题栏「×」和托盘「退出」都走 `quit()`，那里已经收了采集器；
        #: 但**还有别的路子会让 QApplication 退出**（Windows 会话注销、
        #: `qapp.quit()` 被别处调用、界面崩了走 Qt 的默认退出…）——
        #: 那几条路不经过 `quit()`，采集器就会留下。
        #: 幂等：`_stop_collector` 跑两次只是「第二次发现进程已不在」。
        self.qapp.aboutToQuit.connect(self._stop_collector)

        # ---- 启动兜底：采集器不在就把它拉起来（2026-10-10）----
        #: `start.bat` / `MDStats.exe` 是一次性拉起两个进程，**正常路径不用管**。
        #: 但上次界面退出会收掉采集器（`_stop_collector`），而这一侧若没连带
        #: 把它带起来，采集器就**永远缺席** —— 表现为「打了一局，界面零记录」，
        #: 日志里连一行启动记录都没有（2026-10-10 实机撞到）。
        #: ⚠️ **必须延迟几秒**再查：`start.bat` 里采集器是先起的、界面后起，
        #:    界面起来那一刻采集器可能正在写 pid 文件 —— 查太早会误判「不在」
        #:    而拉起第二个。留 6 秒余量。
        if not self._demo:
            QTimer.singleShot(6000, self._ensure_collector)

    def _ensure_collector(self) -> None:
        """启动后自查：采集器不在就拉一个（**幂等 + 不抛**）。"""
        try:
            from ui.collector_ctl import ensure_collector
            ensure_collector()
        except Exception:                                # noqa: BLE001
            pass

    # ------------------------------------------------------------ 主循环
    def current_account(self) -> Optional[str]:
        """悬浮窗跟随看板的账号选择（QSettings ``dash/account``，2026-10-03）。

        每拍读一次（QSettings 内存缓存，开销可忽略）—— 看板里切了账号，
        下一拍悬浮窗就跟着换人，不用重启。
        """
        try:
            return settings().value('dash/account') or None
        except Exception:
            return None

    def tick(self) -> None:
        """便宜探针 -> 变了才拉全量（审查 P2-3）。"""
        if self._demo:                     # 调试模式：探针停着，兜底防误触
            return
        self._auto_show_tick()             # 游戏启动 → 自动弹悬浮窗（2026-10-05）
        p = ui_state.probe(self.storage)
        key = p.key() if p is not None else None
        #: ★ 多账号（2026-10-03）：账号切换也触发全量刷新（探针键不含它）
        acct = self.current_account()
        if acct != getattr(self, '_last_account', None):
            self._last_account = acct
            self.last_key = None           # 强制 refresh
        if key == self.last_key:
            return
        self.last_key = key
        self.refresh()

    def _auto_show_tick(self) -> None:
        """游戏启动 → 自动弹悬浮窗（设置 ``ui/auto_show_floating``，默认开）。

        只在 **False→True 跳变**时弹（UI 启动时游戏已经在跑 = 不弹；
        用户手动收起后游戏没退 = 不再打扰）。判据优先采集器心跳里的
        ``game_pid``（读一个文件，便宜）；心跳不新鲜（采集器没跑）才自己
        扫进程（``find_pids_by_name``，只读枚举，限流 15s 一次）。
        """
        if str(settings().value('ui/auto_show_floating', '1')) in ('0', 'false'):
            return
        running = self._game_running()
        prev = getattr(self, '_game_was_running', None)
        self._game_was_running = running
        if running and prev is False and not self.win.isVisible():
            self.win.show()
            self.win.raise_()

    def _game_running(self) -> bool:
        """游戏在不在跑 —— 心跳优先（便宜），没采集器才扫进程（限流）。"""
        status = ui_state.collector_status()
        if status.get('running'):
            return bool(status.get('game_pid'))
        import time
        now = time.time()
        if now - getattr(self, '_last_game_scan', -1e9) < 15:
            return bool(getattr(self, '_game_running_cache', False))
        self._last_game_scan = now
        try:
            from core.memread.process import find_pids_by_name
            found = bool(find_pids_by_name('masterduel.exe'))
        except Exception:                                      # noqa: BLE001
            found = False
        self._game_running_cache = found
        return found

    def refresh(self) -> None:
        # 调试模式吃 demo 战绩（9 行全形态）；正常模式吃真库快照。
        # ⚠️ `recent_limit=MAX_ROWS`：一次取够 10 局，行区才能滚动 / 拖高看更多
        #    （取太少的话滚动条里只有 3 行，用户拖高也没东西可看）。
        snap = (demo_snapshot() if self._demo
                else ui_state.snapshot(self.storage, recent_limit=MAX_ROWS,
                                       account=self.current_account()))
        self._attach_suggestions(getattr(snap, 'recent', None))
        self.win.set_snapshot(snap)
        self.win.refresh_deck_banner(
            lambda name: self._deck_images().resolve(name))
        self._snap = snap
        self.refresh_dashboard()

    def _deck_images(self):
        """卡图解析器（懒建）—— 给建议态配图用。"""
        # ⚠️ `getattr`：单测用 `__new__` 造**裸对象**（不走 `__init__`），别假设属性都在
        if getattr(self, '_imgs', None) is None:
            from core.deck_images import DeckImages
            self._imgs = DeckImages(self.storage)
        return self._imgs

    @staticmethod
    def _sug_key(raw):
        """建议缓存的键 —— `opp_revealed_ids` 在库里是 JSON 文本、**读出后是 list**
        （`storage` 会把 JSON 列 load 成对象），而 list 不能当字典键。

        ⚠️ 2026-09-16 实机启动即崩（`TypeError: unhashable type: 'list'`）——
        demo 数据里它是字符串，所以**单测全绿也拦不住**，必须统一归一化。
        """
        if isinstance(raw, (list, tuple)):
            return tuple(str(x) for x in raw)
        return (str(raw) if raw else '')

    def _attach_suggestions(self, rows) -> None:
        """给**未标注**的最近对局挂上 M5 反推建议（悬浮窗那一格直接用）。

        * 只对未标注的行算（已标注不需要）；
        * `opp_revealed_ids` 是**唯一输入** → 按它缓存：刷新每 2 秒一次，
          `deck_guess.suggest()` 不该每拍都跑；
        * 建议的图一并解析好（`DeckImages.resolve`，配不出回落悠悠）。
        """
        from ui.selector import deck_display
        lang = str(settings().value('ui/lang', 'cn'))
        cache = getattr(self, '_sug_cache', None)
        if cache is None:                        # 裸对象（单测）也走得通
            cache = self._sug_cache = {}
        for row in (rows or []):
            if row.get('opp_known'):
                continue
            key = self._sug_key(row.get('opp_revealed_ids'))
            if key not in cache:
                cands = self.guess_for(row)
                cache[key] = cands[0] if cands else None
            cand = cache[key]
            if not cand:
                continue
            row['suggest_deck'] = cand
            row['suggest_name'] = deck_display(cand, lang) or cand.get('name')
            row['suggest_img'] = self._deck_images().resolve(cand.get('name'))

    def on_accept_suggest(self, row: dict) -> None:
        """悬浮窗那格显示着**建议的卡组** → 点一下即标注（用户 2026-09-16 定）。

        走的还是 `ui_actions.mark_meta_deck`（与面板同一条链路：先 `upsert_opp_deck`
        换 id 再绑）；没有建议就退回开面板（兜底）。
        """
        deck = (row or {}).get('suggest_deck')
        if not deck:
            self.on_pick_deck(row or {})
            return
        ok, msg = ui_actions.mark_meta_deck(
            self.storage, (row or {}).get('match_id'), deck,
            disp=(row or {}).get('suggest_name') or deck.get('name'))
        if not ok:
            QMessageBox.warning(self.win, tr('标注失败'), msg)
            return
        self.refresh()

    def on_my_deck_pick(self, anchor=None) -> None:
        """点了「我方卡组」确认条的卡图 → 弹卡组选择器，**手动换我方卡组**。

        与对手卡组格的选择器**同一个面板**（预填当前卡组、搜索/别名/日文都能搜）；
        确认后写 `my_deck.json`（`src='manual'`）—— 采集器自动识别错了能手动纠。
        ⚠️ 确认条随探针（`my_deck_fp` 进 key）立刻刷新成新卡组。
        """
        from ui.selector import DeckSelectorPanel
        row = {'match_id': None,
               'my_deck': None, 'my_deck_img': None,
               'opp_deck': None, 'opp_deck_img': None,
               'opp_known': False, 'opp_revealed_ids': None,
               'opp_deck_id': None}
        panel = DeckSelectorPanel(
            self.storage, row=row,
            lang=str(settings().value('ui/lang', 'cn')),
            suggestions=[], parent=anchor if isinstance(anchor, QWidget) else self.win)
        panel.move(self.win.x() + self.win.width() + 8, self.win.y())
        panel.confirmed.connect(lambda deck: self._set_my_deck(deck, panel))
        panel.show()
        self._panel = panel                    # 只留引用，别被 GC 掉

    def _set_my_deck(self, deck: dict, panel=None) -> None:
        """选择器确认 → **手动覆盖**当前我方卡组状态（`src='manual'`）。"""
        from core import my_deck
        from ui.selector import deck_display
        lang = str(settings().value('ui/lang', 'cn'))
        my_deck.save(deck.get('name'), src='manual',
                     extra={'display': deck_display(deck, lang)})
        if panel is not None:
            panel.close()
        self.refresh()                          # 探针 key 也会变（双保险）

    def on_row_my_deck_pick(self, row: dict) -> None:
        """点了行内我方卡组格 → 弹选择器，**修正那一局**显示的卡组（09-28）。

        与确认条的 `on_my_deck_pick` 区别：那个改**当前卡组状态**（my_deck.json），
        这个改**该局记录**的 `my_deck_name`（matches 行）——
        巅峰/快照缺失等场景的错标靠它修。
        """
        from ui.selector import DeckSelectorPanel
        panel = DeckSelectorPanel(
            self.storage, row=dict(row),
            lang=str(settings().value('ui/lang', 'cn')),
            suggestions=[], parent=self.win,
            prefill_name=row.get('my_deck') or None)   # 预填 = 该局现值（09-28）
        panel.move(self.win.x() + self.win.width() + 8, self.win.y())
        panel.confirmed.connect(
            lambda deck: self._set_row_my_deck(deck, row, panel))
        panel.show()
        self._panel = panel                    # 只留引用，别被 GC 掉

    def _set_row_my_deck(self, deck: dict, row: dict, panel=None) -> None:
        """选择器确认 → 只改**该局**的 my_deck_name（冗余快照列）。

        ★ 顺带刷新 `my_deck.json`（2026-10-05 用户：首局未识别手选后，下一局
        不该再问）—— 手选也是「这就是我的卡组」的表态：fresh 时间戳让 24h
        闸门放行，指纹变化让确认条走确认流程。"""
        try:
            from core import my_deck
            my_deck.save(str(deck.get('name') or ''), src='manual')
        except Exception:                                      # noqa: BLE001
            pass
        from ui.selector import deck_display
        lang = str(settings().value('ui/lang', 'cn'))
        name = deck_display(deck, lang) or deck.get('name') or ''
        n = self.storage.set_my_deck_for_record(row.get('match_id'), name)
        if panel is not None:
            panel.close()
        if n:
            self.refresh()                     # 重拉快照 → 行上立即可见

    def on_pick_deck(self, row: dict, anchor=None, parent=None) -> None:
        """点了对手卡组格 → 打开**附着面板**（2026-09-15 用户定的形态）。

        ⚠️ **不再用 `QDialog.exec()`**：那会开独立窗口、还会冻住事件循环。
        面板是 `Qt.Popup`，贴在窗口右侧、与 `anchor`（被点的那一行）对齐；
        点面板外面 / Esc = 取消，**什么都不写**（「标记未知」已按终审砍掉）。
        * 已标注行也会进来 —— 面板预填当前卡组，选新的覆盖；
        * 调试模式下 storage 是沙盒：面板完整走通，标注进内存、真库不被写。
        """
        panel = DeckSelectorPanel(
            self.storage, row=row,
            lang=str(settings().value('ui/lang', 'cn')),
            suggestions=self.guess_for(row), parent=parent or self.win)
        panel.confirmed.connect(lambda deck: self._mark(row, deck))
        if anchor is not None:
            panel.show_near(anchor)
        else:
            panel.show()
        self._panel = panel                    # 只留引用，别被 GC 掉

    # ------------------------------------------------------- 关闭（× 按钮）
    def on_close_requested(self) -> None:
        """标题条「×」——**按设置决定**怎么关；没设置过就先弹一次提示（用户 2026-09-15 定）。"""
        action, explicit = close_action()
        if not explicit:
            action, remember = self.ask_close_action()
            if action is None:                  # 用户把对话框关了 = 什么都不做
                return
            if remember:
                settings().setValue('ui/close_action', action)
        self._do_close(action)

    def _do_close(self, action: str) -> None:
        if action == 'quit':
            self.quit()
        else:
            self.win.hide()                     # 隐藏到托盘（进程继续统计）

    def ask_close_action(self):
        """首次点「×」的提示 —— 返回 ``(动作, 是否记住)``；关窗返回 ``(None, False)``。

        做成独立方法便于测试打桩（别在测试里真弹模态框）。
        """
        dlg = CloseDialog(self.win)
        if dlg.exec() != QDialog.Accepted:      # 取消 / Esc / 关窗 = 什么都不做
            return None, False
        return dlg.choice(), dlg.remember()

    def guess_for(self, row: dict) -> list:
        """M5 反推候选（选择器面板的「建议」置顶）—— 拿不到参照库就空表。

        参照库在 `__init__` 里加载一次（99 副，595KB JSON）；`deck_guess` 的
        docstring 写明这是「CLI 与 M7 UI 共用的这一条路径」。

        ⚠️ `suggest()` 返回的是**全量**（实测 ~101 条，含 0 命中的）——
        这里先滤掉 0 命中、再截到 `MAX_SUGGEST`（5）条，否则整张清单都会被标「建议」。

        ⚠️ 候选 dict 是打分器出的，**只有站内英文名**（`name`）。这里给每条补上
        meta 清单的显示字段（`name_cn` 等）—— 不补的话，`deck_display()` 会
        回落到英文站内名：**悬浮窗建议名是英文、一键采纳落库的 `opp_deck_name`
        也是英文**（2026-09-16 实机事故：库里全成了 'Blue-Eyes'/'Exosister'）。
        """
        #: 🔴 空索引要**重载**（2026-10-11 实机回归）：首跑播种完成前
        #: `meta_decks` 表是空的，「空表 = 全过滤」口径让启动那一刻加载到的
        #: `_guess_index` 是个**空 dict**（不是 None）—— 且它只在启动时加载
        #: 一次，于是首跑窗口启动的进程**永远没有建议**（实测第一局对手烙印，
        #: 建议一格空白）。这里发现空索引就节流重试：播种完成后自动恢复。
        if getattr(self, '_guess_index', None) is None:
            return []
        if not self._guess_index:
            now = time.monotonic()
            if now - getattr(self, '_guess_reload_at', -1e9) < 30.0:
                return []
            self._guess_reload_at = now
            try:
                idx, groups = deck_guess.load_index()
            except Exception:                            # noqa: BLE001
                return []
            if not idx:
                return []
            self._guess_index, self._guess_groups = idx, groups
            self._sug_cache.clear()     # 之前以「无建议」缓存的局全部重算
        try:
            cands = deck_guess.suggest(row, self._guess_index,
                                       self._guess_groups)
        except Exception:
            return []
        out = [c for c in cands if c.get('hits')][:MAX_SUGGEST]
        meta = {d.get('name'): d
                for d in (self.storage.list_meta_decks(include_hidden=False)
                          or [])}
        for c in out:
            full = meta.get(c.get('name'))
            if full:
                for k in ('name_cn', 'name_display', 'name_ja', 'aliases'):
                    if full.get(k) and not c.get(k):
                        c[k] = full[k]
        return out

    def on_pick_result(self, row: dict, anchor=None, parent=None) -> None:
        """点了胜负格 → 弹「胜利 / 失败 / 平局 / 清除结果」（任务书 Bug 2 的兜底）。

        为什么需要：对手**秒投**的局采集侧拿不到任何胜负证据（winbits /
        ClientWork 结算码 / 结算文案全空），`result` 空着就不进统计 ——
        这几局只能人工补（`docs/Bug修复任务书-20260918.md` Bug 2）。

        ⚠️ 写库走 `core.ui_actions.repair_result`（**纯 Python**，界面只发意图）；
        落的是 `result_src='manual'`，重跑 `import_duel_records.py --apply`
        **冲不掉**（用户 2026-09-18 深夜口径：人工优先于自动解析）。
        """
        mid = (row or {}).get('match_id')
        if not mid:
            return
        menu = QMenu(parent or self.win)
        for value, label in RESULT_ACTIONS:
            act = menu.addAction(tr(label))
            act.triggered.connect(
                lambda _checked=False, v=value: self._apply_result(row, v))
        pos = None
        if anchor is not None:
            try:
                pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
            except Exception:
                pos = None
        menu.exec(pos if pos is not None else QCursor.pos())

    def _apply_result(self, row: dict, result) -> None:
        """修正菜单选中后写库 + 立刻重画（不必等下一拍探针）。"""
        ok, msg = ui_actions.repair_result(self.storage,
                                           (row or {}).get('match_id'), result)
        if not ok:
            QMessageBox.warning(self.win, tr('修正失败'), msg)
            return
        self.refresh()

    def _mark(self, row: dict, deck: dict) -> None:
        """面板确认后写库 —— 口径与 M5 CLI 完全一致（`mark_meta_deck` 内部换 id）。

        `disp` 传**界面上显示的那个名字**（按当前显示语言算），行上才会跟面板一致。
        """
        from ui.selector import deck_display
        lang = str(settings().value('ui/lang', 'cn'))
        ok, msg = ui_actions.mark_meta_deck(self.storage, row.get('match_id'),
                                           deck, disp=deck_display(deck, lang))
        if not ok:
            QMessageBox.warning(self.win, tr('标注失败'), msg)
            return
        self.refresh()

    def _make_storage(self, sandbox: bool):
        """正常 = 真库 Storage；调试 = 沙盒（只读参考数据，标注进内存）。"""
        real = Storage(self._db_path) if self._db_path else Storage()
        if not sandbox:
            # ★ 建表（2026-10-10 实机测试）：全新解压时库里**一张表都没有**，
            #   界面会显示「零战绩」而不是报错 —— 分不清是坏了还是本来就空。
            #   建表幂等、很便宜，UI 先跑起来时建一份最稳（双击 exe 时
            #   采集器未必已经起来）。
            try:
                real.init_schema()
            except Exception:                            # noqa: BLE001
                pass                                    # 读不到就等采集器建
        return DemoSandboxStorage(real) if sandbox else real

    # ------------------------------------------------------ S6 统计窗
    # ---------------------------------------------------------- 看板（S7）
    def floating_relayout(self) -> None:
        """悬浮窗按新设置**重排列**（隐藏了哪几列就立刻生效，不���重启）。

        窗口宽下限也跟着变（列少了 -> 窗口可以更窄），所以先按新设置重新
        定尺寸、再强制走一遍 `_relayout_columns`（它的「列宽没变就跳过」缓存
        会被 `_last_layout` 的新宽度自然打破）。
        """
        win = self.win
        try:
            from ui import floating as _fl
            #: 列少了 -> 窗口下限/上限都重算（否则留一圈空白，见用户 2026-10-17）
            _fl.invalidate_width_cache()
            win.setMinimumWidth(_fl.window_width())
            # ★ BUG-B5-09：注释写「下限/上限都重算」，原来却只有 setMinimumWidth
            #   —— 上限是构造时注入的 `_max_width`，没人再算，于是藏掉三列后
            #   窗口仍能拖到「五列都显示时」的宽度，右侧一大片留白。
            #   `max_window_width()` 本身**就是**按 `float_hidden_cols()` 算的，
            #   只是缺这一句调用。
            _set_max = getattr(win, 'set_max_width', None)
            if callable(_set_max):
                _set_max(_fl.max_window_width(self.deck_name_pool()))
            win._last_layout = None          # 强制重排（别被缓存骗过去）
            win._relayout_columns()
        except Exception:                                # noqa: BLE001
            pass

    def toggle_floating(self) -> bool:
        """看板「悬浮窗」页的开关（2026-10-03）：显示/隐藏悬浮窗。

        返回切换后的可见状态（看板按钮文案跟着换）。隐藏 ≠ 关闭程序 ——
        托盘/这里都能再唤回来。
        """
        if self.win.isVisible():
            self.win.hide()
            visible = False
        else:
            self.win.show()
            self.win.raise_()
            self.win.activateWindow()
            visible = True
        #: ★ 落盘（2026-10-03）：启动时悬浮窗显不显示，跟「悬浮窗」页的开关走
        settings().setValue('dash/floating_visible',
                            '1' if visible else '0')
        return visible

    def _build_floating(self) -> FloatingWindow:
        """造悬浮窗并接好全部信号。

        文案是构造期烘进控件的 —— 切语言实时切换（2026-10-04 用户口径）=
        重建窗口，所以建窗 + 接线必须是一个可重入的方法（`_rebuild_windows`）。
        """
        win = FloatingWindow()
        #: ★ 窗口图标 = 应用图标（2026-10-10 用户：「任务栏图标还是 python 脚本
        #:   默认图标」）。以前**只有托盘**设了（`md_app_icon`），任务栏 / 标题栏 /
        #:   Alt-Tab 全是 Qt 默认 —— 现在与托盘、看板**同一枚**。
        win.setWindowIcon(md_app_icon())
        # 宽度**上限** = 最长的卡组名能完整显示（三语名字池由数据层收）
        # —— 用户 2026-09-15 定：左右最多拉到这里，再宽是纯留白
        win.set_max_width(
            max_window_width(ui_state.deck_name_pool(self.storage)))
        win.settings_requested.connect(self.open_settings)
        win.dashboard_requested.connect(self.open_dashboard)
        win.hide_requested.connect(win.hide)
        win.quit_requested.connect(self.quit)
        win.close_requested.connect(self.on_close_requested)
        win.pick_deck.connect(self.on_pick_deck)
        win.my_deck_pick.connect(self.on_my_deck_pick)   # 确认条卡图（2026-09-22）
        win.row_my_deck_pick.connect(self.on_row_my_deck_pick)  # 行级修正（09-28）
        win.accept_suggest.connect(self.on_accept_suggest)
        win.result_pick.connect(self.on_pick_result)
        return win

    def _apply_lang_switch(self) -> None:
        """设置页切了语言 —— **原地切换**（2026-10-05 用户：窗口不关、
        同一界面里文字直接变；推翻 10-04 的「重建窗口」方案）。

        reset 进程内语言缓存，悬浮窗 / 看板各自 `retranslate()`：
        内部页控件重建，窗口几何、折叠态、当前页全部保留。
        """
        from ui.i18n import reset_lang
        reset_lang()
        if getattr(self, '_dash_win', None) is not None:
            self._dash_win.retranslate()   # 先看板（用户眼睛在这，遮罩先出）
        self.win.retranslate()
        self.refresh()

    def open_dashboard(self, page: Optional[str] = None) -> None:
        """托盘「看板…」/ 悬浮窗「设置…」都进这里。

        **必须持引用防 GC**（照 `self._panel` 的先例）；`page=None` 则保持上次所在页。
        """
        from ui.dashboard import DashboardWindow
        if getattr(self, '_dash_win', None) is None:
            self._dash_win = DashboardWindow(
                on_restart=self.restart_ui,
                on_set_autostart=self.dash_set_autostart,
                on_lang_changed=self._apply_lang_switch,
                on_toggle_floating=self.toggle_floating,
                #: 悬浮窗「隐藏某列」改了 -> 立刻重排（用户 2026-10-17：实时生效）
                on_floating_layout_changed=self.floating_relayout,
                on_pick_deck=self.on_dash_pick_deck,
                loader=self.dashboard_loader(),
                on_pick_result=self.on_dash_pick_result,
                analysis_loader=self.dashboard_analysis_loader(),
                tier_loader=self.dashboard_tier_loader(),
                banlist_loader=self.dashboard_banlist_loader(),
                deckview_loader=self.dashboard_deckview_loader(),
                on_fetch_decks=self.fetch_new_decks_dialog,      # Q17
                opp_deck_images=self._deck_images().resolve,     # 卡组名管理页签的行图
                storage=self.storage,                            # 换图写库用
                collector_loader=self.dashboard_collector_status,  # 标题栏采集器状态
                export_saver=self.export_matches,                  # 导出写盘（D4c）
                export_loader=self.dashboard_export_loader())      # 导出页预览（D4c）
        win = self._dash_win
        win.set_loader(self.dashboard_loader())      # demo 开关热切换也走这里
        win.set_analysis_loader(self.dashboard_analysis_loader())
        win.set_tier_loader(self.dashboard_tier_loader())
        win.set_banlist_loader(self.dashboard_banlist_loader())
        win.set_deckview_loader(self.dashboard_deckview_loader())
        win.set_collector_loader(self.dashboard_collector_status())
        win.set_export_loader(self.dashboard_export_loader())
        win.reload()
        if page:
            win.open_page(page)
        win.set_floating_visible(self.win.isVisible())
        win.show()
        win.raise_()
        win.activateWindow()

    def dashboard_loader(self):
        """看板「战绩」页的数据源 —— **返回可调用对象**（窗口自己不碰 storage）。

        demo 模式吃 `core.ui_demo.demo_stats_page`（与悬浮窗同一条纪律）；
        正常模式走 `ui_state.stats_page_data()`，再补上**卡组图**（图要经
        `DeckImages.resolve`，那是「卡组名 → 图」的唯一入口，别直查文件名）。
        """
        if self._demo:
            from core.ui_demo import demo_stats_page
            return demo_stats_page

        def load(mode_group=None, month=None, account=None):
            #: ``mode_group`` = 看板「战绩总览」的模式筛选（None = 全部）
            #: ``month`` = 热力图月卡切的月（'YYYY-MM' / None = 本月，2026-10-01）
            #: ``account`` = 多账号筛选（2026-10-03，None = 全部/未分账号）
            data = ui_state.stats_page_data(self.storage, mode_group=mode_group,
                                            month=month, account=account)
            imgs = self._deck_images()
            unmarked = []
            for name, st, rows in data.get('groups') or []:
                try:
                    st['img'] = imgs.resolve(name)
                except Exception:
                    st['img'] = None
                for r in rows:
                    try:
                        r['opp_deck_img'] = (imgs.resolve(r['opp_deck'])
                                             if r.get('opp_deck') else None)
                    except Exception:
                        r['opp_deck_img'] = None
                    if not r.get('opp_known'):
                        unmarked.append(r)
            # 🔴 2026-10-11 用户口径：「看板的对手卡组应该和悬浮窗相同显示」——
            # 未标注的行也挂 M5 反推建议（走同一份 `_sug_cache`，不重算）。
            # OppDeckChip 会显示建议名/图 + 琥珀 `!`（未确认态）。
            self._attach_suggestions(unmarked)
            return data
        return load

    def dashboard_analysis_loader(self):
        """看板「对局分析」页的数据源（D2，**返回可调用对象**，同 `dashboard_loader`）。

        demo 模式吃 `core.ui_demo.demo_analysis_page`（与真函数同签名直传）；
        正常模式走 `ui_state.cross_matrix()`，再补**卡组图**（`DeckImages.resolve`，
        列头与行头的图标都要；图是「卡组名 → 图」的唯一入口，别直查文件名）。
        口径恒为主口径 —— 页头警示由数据层拼好，这里不加戏。
        """
        if self._demo:
            from core.ui_demo import demo_analysis_page
            return demo_analysis_page

        def load(went_first=None, month=None, mode_group=None):
            data = ui_state.cross_matrix(self.storage, went_first=went_first,
                                         month=month, mode_group=mode_group)
            imgs = self._deck_images()
            for a in data.get('col_avgs') or []:
                a['img'] = imgs.resolve(a.get('deck'))
            for r in data.get('rows') or []:
                r['img'] = imgs.resolve(r.get('deck'))
            #: ★ 2026-10-16：手坑汇总表每行 = 一副我方卡组 —— 补卡组图
            #: （`DeckImages.resolve` 是「卡组名 → 图」的唯一入口）。
            ht = data.get('hand_traps') or {}
            for key in ('second_my', 'first_opp'):
                for r in ht.get(key) or []:
                    try:
                        r['img'] = imgs.resolve(r.get('deck'))
                    except Exception:                        # noqa: BLE001
                        r['img'] = None
            return data
        return load

    def dashboard_banlist_loader(self):
        """看板「禁限表」页的数据源（D3 第二刀，只读 banlist_changes）。

        demo 吃 `core.ui_demo.demo_banlist_page`；真库走 `ui_state.banlist_page_data`。
        """
        if self._demo:
            from core.ui_demo import demo_banlist_page
            return demo_banlist_page

        def load():
            return ui_state.banlist_page_data(self.storage)
        return load

    def dashboard_tier_loader(self):
        """看板「Tier 表」页的数据源（D3 第一刀，只读 meta_decks）。

        demo 吃 `core.ui_demo.demo_tier_page`；真库走 `ui_state.tier_page_data()`
        再补**卡组图**（`DeckImages.resolve`，中文名/英文名/别名都能解析）。
        """
        if self._demo:
            from core.ui_demo import demo_tier_page
            return demo_tier_page

        def load():
            data = ui_state.tier_page_data(self.storage)
            imgs = self._deck_images()
            for s in data.get('sections') or []:
                for d in s.get('decks') or []:
                    try:
                        d['img'] = imgs.resolve(d.get('name'))
                    except Exception:
                        d['img'] = None
            return data
        return load

    def export_matches(self, fmt='csv', month=None, mode_group=None) -> None:
        """导出对局记录（D4c）—— 弹保存框 → `ui_state.write_export` 落盘。

        ⚠️ **窗口只发信号、这里才写文件**（M7 纪律：UI 层不直接落数据）。
        口径：`ui_state.export_rows()` 复用战绩页那条路，所以**导出 = 预览里那批**
        （同一个月份 + 模式筛选）。

        ★ BUG-B5-04：失败/成功都改用 `QMessageBox` —— 原来用 `print()`，
        而安装版是 `pythonw` 起进程（**没有控制台**，`sys.stdout` 是 `None`），
        四条分支要么 `AttributeError` 要么什么也看不见，对用户都是**静默**。
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import ui_state

        _parent = getattr(self, '_dash_win', None) or self.win
        try:
            rows = ui_state.export_rows(self.storage, mode_group=mode_group,
                                        month=month)
        except Exception as exc:                             # noqa: BLE001
            QMessageBox.warning(_parent, tr('导出失败'),
                                tr('取数失败：%s') % exc)
            return
        if not rows:
            QMessageBox.information(_parent, tr('没有可导出的对局'),
                                    tr('当前筛选下没有对局，未生成文件。'))
            return
        default = os.path.join(PROJ, ui_state.export_default_name(fmt))
        filt = ('CSV (*.csv)' if fmt == 'csv' else 'JSON (*.json)')
        path, _sel = QFileDialog.getSaveFileName(
            _parent, tr('导出对局记录'), default, filt)
        if not path:
            return
        try:
            ui_state.write_export(path, rows, fmt=fmt)
        except Exception as exc:                             # noqa: BLE001
            QMessageBox.warning(_parent, tr('导出失败'),
                                tr('写盘失败：%s') % exc)
            return
        QMessageBox.information(_parent, tr('导出完成'),
                                tr('%d 局已导出到：\n%s') % (len(rows), path))

    def dashboard_export_loader(self):
        """导出页的**预览**数据（D4c）—— 收 ``(month, mode_group)``。

        只给前若干行（够看清列对不对）；点「导出」时再全量取（`export_matches`）。
        返回的 ``months`` 与对局分析页同源（`ui_state.export_months`）。
        """
        from core import ui_state

        def load(month=None, mode_group=None):
            rows = ui_state.export_rows(self.storage, mode_group=mode_group,
                                        month=month)
            return {'months': ui_state.export_months(self.storage),
                    'rows': rows[:12],
                    'total': len(rows)}
        return load

    def dashboard_collector_status(self):
        """标题栏的「采集器状态」（D4b）—— 纯只读（读心跳文件，不碰进程）。

        ⚠️ M7 纪律：**UI 不查进程**。这里只读 `ui_state.collector_status()` 的
        心跳时间戳；判据与"为什么不查 pid"见那个函数的 docstring。
        """
        def load():
            return ui_state.collector_status()
        return load

    def dashboard_deckview_loader(self):
        """看板「卡组构成」页的数据源（D3 剩余）—— 收**站内英文名**。

        demo 吃 `core.ui_demo.demo_deck_composition`；真库走
        `ui_state.deck_composition()`（卡图由那一层查本地缓存，缺的页面在后台下）。
        """
        if self._demo:
            from core.ui_demo import demo_deck_composition
            return demo_deck_composition

        def load(ref_name=None):
            return ui_state.deck_composition(self.storage, ref_name)
        return load

    # ------------------------------------------------- Q14 / Q17（卡组页两个入口）
    def fetch_new_decks_dialog(self) -> None:
        """Q17「获取新卡组名」（卡组页那个按钮）—— 复用 `fetch_new_decks()` 那条链路。"""
        from ui.decks_page import FetchDecksDialog
        dlg = FetchDecksDialog(parent=getattr(self, '_dash_win', None) or self.win)
        dlg.btn_fetch.clicked.connect(lambda: self.fetch_new_decks(dlg))
        dlg.exec()

    def on_dash_pick_deck(self, row: dict, anchor=None) -> None:
        """看板里点对手卡组格 → **复用悬浮窗那条链路**（同一个选择器面板）。

        ⚠️ 面板是 `Qt.Popup`，parent 必须是**承载它的那个窗口**（这里是看板），
        否则会浮到屏幕角落或跟着悬浮窗跑。
        """
        self.on_pick_deck(row, anchor, parent=self._dash_win)

    def on_dash_pick_result(self, row: dict, anchor=None) -> None:
        """看板里点胜负徽章 → **复用悬浮窗那条链路**（同一个修正菜单）。

        ⚠️ 菜单 parent 必须是**看板窗口**，否则它会跟着悬浮窗跑（同 `on_dash_pick_deck`）。
        """
        self.on_pick_result(row, anchor, parent=self._dash_win)

    def refresh_dashboard(self) -> None:
        """看板开着就顺手刷一下（每 2 秒的探针触发）—— 没开就什么都不做。"""
        win = getattr(self, '_dash_win', None)
        if win is not None and win.isVisible():
            win.reload()

    def open_settings(self) -> None:
        """悬浮窗右键「设置…」→ **打开看板并进设置页**（用户 2026-09-18 口径 Q18）。"""
        from ui.dashboard import SETTINGS_KEY
        self.open_dashboard(page=SETTINGS_KEY)

    def dash_set_autostart(self, on: bool) -> bool:
        """看板「开机自启」开关 → 写「启动」目录。失败由页面回滚显示。"""
        return ui_actions.set_autostart(bool(on), target=pythonw(),
                                        arguments=app_entry_point(),
                                        workdir=PROJ)

    def restart_ui(self) -> None:
        """一键重启：**先起新进程，再退自己**（顺序不能反 —— 反了就没有进程可退）。

        新进程会在单实例锁上等最多 5 秒（`acquire_single_instance_lock`），
        够本进程把锁放掉，所以不会撞「已经在跑了」。
        """
        exe, args, cwd = restart_command()
        #: ★ 先置标记再起新进程（2026-10-10）：`qapp.quit()` 会触发
        #:   `aboutToQuit` -> `_stop_collector`，那会把采集器杀掉，
        #:   正在打的那局整局丢。重启界面只换 UI 进程，采集器**必须留着**。
        self._restarting = True
        QProcess.startDetached(exe, args, cwd)
        self.qapp.quit()

    # ------------------------------------------------- 「获取新卡组名」（S3.5）
    def fetch_new_decks(self, dlg) -> None:
        """设置页那个按钮：跑 `tools/fetch_meta_decks.py`（**不卡界面**）→ 列新卡组请你补中文名。

        * `QProcess` 起脚本，stdout 逐行回显到设置页状态文字；
        * 跑完算「meta 清单里不在覆盖表」的卡组 → `NewDecksDialog`（乙：勾选 + 填中文名）；
        * 确认后写 `data/deck_name_cn.csv`，再跑 `--cn-only` 把表贴进库。
        """
        from PySide6.QtCore import QProcess

        dlg.btn_fetch.setEnabled(False)
        dlg.fetch_status.setText(tr('正在抓取榜单…（联网，1~3 分钟）'))
        proc = QProcess(dlg)
        proc.setWorkingDirectory(PROJ)
        #: 失败原因多半在 stderr —— 合并回显，别只看 stdout（D3：卡组页那个框要日志）
        proc.setProcessChannelMode(QProcess.MergedChannels)
        self._fetch_buf = []

        def log_line(text):
            """有多行日志区就整段留下（`ui/decks_page.FetchDecksDialog`）。"""
            if hasattr(dlg, 'append'):
                try:
                    dlg.append(text)
                except Exception:                            # noqa: BLE001
                    pass

        def on_out():
            raw = bytes(proc.readAllStandardOutput()).decode('utf-8', 'replace')
            for line in raw.splitlines():
                if line.strip():
                    self._fetch_buf.append(line.strip())
                    log_line(line.strip())
            # ★ BUG-B5-15：可能只收到空白行（缓冲凑不满一行）——
            #   原来 `[-1]` 会 IndexError，弹窗卡在「正在抓取」不动。
            if self._fetch_buf:
                dlg.fetch_status.setText(self._fetch_buf[-1][:80])

        def on_done(code, _status):
            dlg.btn_fetch.setEnabled(True)
            if code != 0:
                dlg.fetch_status.setText(tr('抓取失败（退出码 %d）') % code)
                return
            dlg.fetch_status.setText(tr('抓取完成，正在比对覆盖表…'))
            self._show_new_decks(dlg)

        proc.readyReadStandardOutput.connect(on_out)
        proc.finished.connect(on_done)
        proc.start(sys.executable, [os.path.join('tools', 'fetch_meta_decks.py'),
                                    '--no-images'])
        self._fetch_proc = proc

    def _show_new_decks(self, dlg) -> None:
        """比对覆盖表 → 勾选/填名 → 写 CSV → `--cn-only` 进库。"""
        from core.deck_csv import index_map, read, set_cell, write

        names = [d.get('name') for d in (self.storage.list_meta_decks() or [])]
        fresh = new_deck_names(names, CN_CSV)
        if not fresh:
            #: ★ 2026-10-12：没有新名字也要走补图 —— Q17 主抓取是 `--no-images`，
            #: 新进库的站内卡组（大师一∪新卡口径那批）靠这一步拿卡图。
            #: 原来直接 return：预填覆盖表后这条路必走 → 卡图永远缺（实测踩过）
            dlg.fetch_status.setText(tr('没有新卡组 —— 顺手补缺失的卡图…'))
            self._push_cover_table(dlg)
            return
        picker = NewDecksDialog(fresh, DeckImages(self.storage), parent=dlg)
        if picker.exec() != QDialog.Accepted:
            dlg.fetch_status.setText(tr('已取消（%d 个新卡组没收录）') % len(fresh))
            return
        picked = picker.picked()
        if not picked:
            dlg.fetch_status.setText(tr('一个都没勾'))
            return
        header, rows = read(CN_CSV)
        idx = index_map(header)
        for name, cn in picked:
            row = ['' for _ in header]
            set_cell(row, idx, '站内英文名', name)
            set_cell(row, idx, '中文名', cn)
            set_cell(row, idx, '备注', '设置页新增')
            rows.append(row)
        write(CN_CSV, header, rows)
        dlg.fetch_status.setText(tr('已写入覆盖表 %d 条，正在贴进库…') % len(picked))
        self._push_cover_table(dlg)

    def _push_cover_table(self, dlg) -> None:
        """跑 `fetch_meta_decks.py --cn-only`（只贴覆盖表、不联网）→ **再补缺图** → 刷新。

        ★ 2026-10-08：贴完库后追加一次 `--missing-images` —— Q17 抓榜单时是
        `--no-images`，新加进来的**站内卡组**（`Phantom Knight Orcust` 那类）
        本来会一直没图；补这一步它们就有卡图了。补图失败不回滚贴库（库已经好了）。
        """
        from PySide6.QtCore import QProcess

        proc = QProcess(dlg)
        proc.setWorkingDirectory(PROJ)

        def after_images(code, _status):
            dlg.fetch_status.setText(
                tr('完成（%s）') % (tr('成功') if code == 0 else tr('抓取失败')))
            try:
                self.refresh()
            except Exception:
                pass

        def on_done(code, _status):
            if code != 0:
                after_images(code, _status)
                return
            dlg.fetch_status.setText(tr('已贴进库，正在补缺失的卡图…'))
            img = QProcess(dlg)
            img.setWorkingDirectory(PROJ)
            img.setProcessChannelMode(QProcess.MergedChannels)
            #: 补图失败也只是提示（贴库已经成功，别把整体判成失败）
            img.finished.connect(lambda c, _s: after_images(0, _s))
            img.start(sys.executable,
                      [os.path.join('tools', 'fetch_meta_decks.py'),
                       '--missing-images'])
            self._img_proc = img

        proc.finished.connect(on_done)
        proc.start(sys.executable, [os.path.join('tools', 'fetch_meta_decks.py'),
                                    '--cn-only'])
        self._push_proc = proc

    def set_demo_mode(self, on: bool) -> None:
        """调试模式**热切换**（设置里勾选即时生效，QSettings 持久化）。

        开 = 停探针、Storage 换沙盒（只读参考数据，标注进内存）、界面吃 demo；
        关 = 换回真库 Storage、重启探针、立即拉真库快照。
        """
        on = bool(on)
        if on == self._demo:
            return
        self._demo = on
        settings().setValue('ui/demo', on)
        self.last_key = None
        if on:
            self.timer.stop()
        else:
            self.timer.start()
        self.storage = self._make_storage(on)
        self.refresh()

    def quit(self) -> None:
        """退出前存位置与尺寸（拖完直接退出时，QSettings 可能还没落盘）。"""
        try:
            self.win.save_pos()
            self.win.save_size()
        except Exception:
            pass
        #: 采集器由 **`aboutToQuit` → `_stop_collector`** 收（启动时连的兜底），
        #: 这里**不再显式调一遍** —— 收一次要等进程真的消失（实测 ~0.7s），
        #: 连等两轮会让「关闭很慢」（用户 2026-10-10 实机反馈）。
        self.qapp.quit()

    def _stop_collector(self) -> None:
        """收掉采集器（**幂等 + 不抛**）。demo 模式与「隐藏到托盘」都不走这里。

        🔴 **「重启界面」期间一律不收**（2026-10-10 用户实机事故）：
        `restart_ui()` 走的是 `qapp.quit()`，会触发 `aboutToQuit` ->
        采集器被连带杀掉，而新界面起来时那一局**正在进行**。
        表现：点一次「重启界面」，正在打的那局整局丢失 —— 实测第五局
        （20:47 胜）就是这么没的：重启后日志只剩「控制器 60s 未找到」，
        record 目录一个都没建。
        「重启界面」只该换 UI 进程，采集器是**另一个进程**，跟它无关。
        """
        if getattr(self, '_demo', False):
            return
        if getattr(self, '_restarting', False):
            return          # 重启界面 ≠ 退出程序：采集器留着继续采
        if getattr(self, '_collector_stopped', False):
            return          # 已经收过了（quit + aboutToQuit 会各叫一次）
        try:
            from ui.collector_ctl import stop_collector
            how = stop_collector()
            #: 标记置**无论收没收到** —— 否则 `aboutToQuit` 那轮又会空等一次
            self._collector_stopped = True
            if how not in ('no-pid-file', 'not-running'):
                print('[退出] 采集器：%s' % how)
        except Exception:                                # noqa: BLE001
            pass

    def run(self) -> int:
        self.refresh()
        #: ★ 2026-10-03 启动链（用户：启动.bat 一键 = 采集器 + 看板；
        #:   悬浮窗显不显示跟「悬浮窗」页的开关走，托盘永远在）
        if settings().value('dash/floating_visible', '1') not in ('0', 'false'):
            self.win.show()
        self.open_dashboard('stats')
        if not self._demo:                 # demo 不跑探针（热切回真库时会再启）
            self.timer.start()
        return self.qapp.exec()


def acquire_single_instance_lock() -> Optional[QLockFile]:
    """双开互斥（审查 P3-4）：双开 = 双份弹窗 + 双写位置。

    返回锁对象（**必须持有引用**，被 GC 掉锁就没了）；拿不到返回 None。
    """
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    lock = QLockFile(LOCK_PATH)
    lock.setStaleLockTime(30_000)
    # 宽限 5 秒：设置里的「重启界面」是**先起新进程、旧进程才退**，新进程要等锁。
    # （正常双开仍会 5 秒后退出并提示，只是提示晚一点。）
    if not lock.tryLock(5000):
        return None
    return lock


def main(db_path: Optional[str] = None, interval_ms: int = 2000,
         argv=None, demo: bool = False) -> int:
    """入口：起应用 + 抢单实例锁 + 跑主循环。

    ★ BUG-B5-02：单实例锁必须**在构造之前**抢。
    原来顺序是「先 `MasterDuelUiApp(...)` 再抢锁」—— 而构造函数里全是副作用：
    `_make_storage()`（建库 + `init_schema`）、`ensure_meta_decks()`
    （跑数据迁移，并在 meta_decks 为空时**起一个联网抓榜单的子进程**）、
    `_build_floating()`（`tray.show()`，第二个托盘图标会真的出现）。
    全新安装时双击两次 = 两个 `fetch_meta_decks.py` 并发写库写图。
    `restart_ui()` 的 5 秒宽限够新进程等锁，提前抢不会破坏「重启界面」。
    """
    lock = acquire_single_instance_lock()
    if lock is None:
        QMessageBox.information(None, tr('已经在跑了'),
                                tr('悬浮窗已经在运行（看屏幕右上角）。'))
        return 1
    app = MasterDuelUiApp([sys.argv[0]] if argv is None else argv,
                          db_path=db_path, interval_ms=interval_ms, demo=demo)
    app._lock = lock                     # 持住，别被回收
    return app.run()


if __name__ == '__main__':
    sys.exit(main())
