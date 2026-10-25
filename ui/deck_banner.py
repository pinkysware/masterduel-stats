# -*- coding: utf-8 -*-
"""D4 后追加（用户 2026-09-22）：悬浮窗顶部的「我方卡组」提示条。

演进（同日三轮）：
1. 初版：文字确认条（未获取 → 提示去编辑卡组；读到 → [图] 名字 [确认]）；
2. 用户实测「在匹配界面换卡组确认条不动」→ 便宜探针加 `my_deck` 指纹（已修）；
3. **本版（用户拍板）**：「应该改成 **我方卡组：卡组卡图(不带名字)，刷新键，确认。
   点击卡组卡图弹出卡组选择器。这样能手动更换我方卡组**」——
   * 文字标签换成固定标题「我方卡组：」（不再复读卡组名）；
   * **卡图可点** → 浮窗弹 `DeckSelectorPanel`（与对手卡组格同一个面板）；
     选中后**手动覆盖** `my_deck.json`（`src='manual'`）—— 识别错了能手动纠；
   * 加**刷新键**：手动触发一次重读（自动识别没跟上时的兜底）。

确认语义不变：点「确认」把当前状态指纹记进 QSettings，横幅收起；
状态再变（自动识别 / 手动换）会重新出现。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ui import theme, widgets
from ui.i18n import tr

#: 确认状态记录的键（QSettings 由调用方给；这里只管键名）
CONFIRM_KEY = 'my_deck/confirmed_fingerprint'


def deck_state_fingerprint(state: dict) -> str:
    """状态文件的「身份」：名字 + 指纹（没指纹就用时刻）—— 确认是否还有效。"""
    if not state or not state.get('name'):
        return ''
    base = str(state.get('name') or '')
    fp = str(state.get('ids_fingerprint') or '')
    return '%s|%s' % (base, fp) if fp else base


def banner_mode(state: dict, confirmed: str) -> str:
    """提示条当前该显示什么（**纯函数**，单测锁）。

    * ``'hidden'`` —— 不显示（已确认过当前这套）；
    * ``'waiting'`` —— 还没有名字（提示「点卡图手动选」）；
    * ``'confirm'`` —— [图] 刷新 [确认]（读到了，等确认）。

    ⚠️ 本版横幅**常驻可见**（它已经是「我方卡组」的操作位，不只是提示）——
    hidden 只在「确认过当前这套」时出现。
    """
    if not state or not state.get('name'):
        return 'waiting'
    if confirmed and confirmed == deck_state_fingerprint(state):
        return 'hidden'
    return 'confirm'


class _PickableThumb(QWidget):
    """可点的卡图格（把 `thumb_label` 包一层，点击整格生效）。"""

    clicked = Signal()

    def __init__(self, size: int = theme.THUMB, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._img = None

    def set_image(self, path: str, dim: bool = False) -> None:
        """换图（重建内部 QLabel —— `thumb_label` 是一次性控件，别复用）。"""
        old = self._img
        if not path:
            from core.deck_images import DEFAULT_IMAGE
            path = DEFAULT_IMAGE
        self._img = widgets.thumb_label(path, self.width(),
                                        opacity=0.5 if dim else 1.0)
        self._img.setParent(self)
        self._img.move(0, 0)
        self._img.setAttribute(Qt.WA_TransparentForMouseEvents)
        # ⚠️ 不 show() 不行：child widget 换 parent 后**默认隐藏**
        #    （用户实测「点刷新不显示当前卡组」的根因 —— pixmap 都读到了，
        #     就是不上屏）。
        self._img.show()
        if old is not None:
            old.hide()
            old.setParent(None)
            old.deleteLater()

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)


class DeckConfirmBanner(QWidget):
    """悬浮窗顶部的「我方卡组」条：卡图（可点换卡组）+ 刷新 + 确认。"""

    #: 点了「确认」（浮窗据此把当前状态记成已确认）
    confirmed = Signal()
    #: 点了卡图（浮窗据此弹卡组选择器）
    pick_requested = Signal(object)
    #: 点了「刷新」（浮窗据此重读状态）
    refresh_requested = Signal()
    #: 显隐变了（浮窗要联动发丝线 + 重算窗口高度）
    visibility_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('deckBanner')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            'QWidget#deckBanner { background: %s;'
            ' border-bottom: 1px solid %s; }'
            'QWidget#deckBanner QLabel { background: transparent; }'
            % (theme.SURFACE2, theme.HAIRLINE))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, 4, theme.S_SM, 4)
        lay.setSpacing(theme.S_SM)

        self.title = widgets.label(tr('我方卡组：'), 'body')
        lay.addWidget(self.title)

        self._img_path = None
        self._known = False
        #: 卡图 = **按键**（点了弹卡组选择器，手动换我方卡组）
        self.thumb = _PickableThumb()
        # ⚠️ `clicked`（无参）不能直连 `pick_requested`（收 object）——
        #    emit 0 参会 TypeError（PySide 不允许信号直连"需要参数"的信号）。
        self.thumb.clicked.connect(lambda: self.pick_requested.emit(None))
        self.thumb.setCursor(Qt.PointingHandCursor)
        self.thumb.set_image('', dim=True)
        lay.addWidget(self.thumb)

        self.label = widgets.label('', 'body')
        lay.addWidget(self.label, 1)

        #: ⚠️ 按钮文字被裁（用户实测）：全局 QSS 给 `QPushButton` 的
        #:    `padding: 6px 12px` 在小高度按钮里把字上下切掉 —— 与
        #:    「卡组名管理」输入框同款坑。给本条的按钮**专用紧样式**：
        #:    只写颜色/边框/内边距，**不写 font-**（widgets.py 硬规矩①），
        #:    且**先 setStyleSheet 再 setFont**（硬规矩②）。
        BTN_SS = ('QPushButton { background: %s; border: 1px solid %s;'
                  ' border-radius: %dpx; padding: 1px 10px; color: %s; }'
                  'QPushButton:hover { border: 1px solid %s; }'
                  % (theme.SURFACE2, theme.HAIRLINE_STRONG, theme.R_XS,
                     theme.INK, theme.HAIRLINE_FOCUS))
        fm = widgets.font(theme.F_BODY_SM).pixelSize()
        btn_h = fm + 10
        self.btn_refresh = QPushButton(tr('刷新'))
        self.btn_refresh.setStyleSheet(BTN_SS)
        self.btn_refresh.setFont(widgets.font(theme.F_BODY_SM))
        self.btn_refresh.setFixedHeight(btn_h)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setToolTip(tr('重新读取当前卡组（自动识别没跟上时用）'))
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        lay.addWidget(self.btn_refresh)

        self.btn_ok = QPushButton(tr('确认'))
        self.btn_ok.setStyleSheet(BTN_SS)
        self.btn_ok.setFont(widgets.font(theme.F_BODY_SM))
        self.btn_ok.setFixedHeight(btn_h)
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.clicked.connect(self.confirmed.emit)
        lay.addWidget(self.btn_ok)

        self.hide()                       # 构造完默认收起（update_state 决定显隐）

    # ------------------------------------------------------------ 状态
    def set_visible(self, on: bool) -> None:
        """显/隐并**通知浮窗**（setVisible 不发事件，得自己喊一声）。"""
        changed = on != self.isVisible()
        self.setVisible(on)
        if changed:
            self.visibility_changed.emit(on)

    def update_state(self, state: dict, confirmed: str, img: str = '') -> str:
        """按当前状态刷提示条；返回模式（hidden/waiting/confirm）。"""
        mode = banner_mode(state, confirmed)
        if mode == 'hidden':
            self.set_visible(False)
            return mode
        self.set_visible(True)
        known = bool(state.get('name'))
        if not known or self._img_path != (img or '') or self._known != known:
            self._known = known
            self._img_path = img or ''
            self.thumb.set_image(self._img_path, dim=not known)
        if mode == 'waiting':
            self.label.setText(
                tr('点卡图手动选；或点开游戏「编辑牌组」再返回即可识别'))
            self.label.setToolTip(tr(
                '天梯 / 巅峰自动识别；DC 杯与活动局的卡表只在「编辑牌组」'
                '打开期间可读 —— 进游戏点开「编辑牌组」再返回，'
                '各模式都能识别/兜底'))
            self.btn_ok.hide()
        else:
            #: 用户 2026-09-22 拍板：**不带名字**（卡图即信息，格子窄）
            self.label.setText('')
            self.btn_ok.show()
        return mode
