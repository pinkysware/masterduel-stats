# -*- coding: utf-8 -*-
"""看板「Tier 表」页（D3 第一刀）—— masterduelmeta tierlist 的本地只读视图。

数据源 = 本地 `meta_decks` 表（`tools/fetch_meta_decks.py` **每月手动**刷新，
本页**纯只读、不联网**）；分组与 Power 区间照站内 tierlist 口径
（TIER 1 / 2 / 3 / TRENDING，见 `core.ui_state.TIER_SECTIONS`）。

布局照参考截图：每节一个标题（TIER N）+ 说明行 + 卡组芯片网格
（[卡图] 显示名，Power 小字）；显示名 = **中文名优先**（name_cn → name_display
→ name），卡图路径由 app 侧 loader 注入（``img``）。

数据由 `core.ui_state.tier_page_data()` 备好（**窗口只画、不查库**）。
Qt 纪律同 ``dashboard.py`` / ``analysis_page.py`` 模块头：QSS 不写 ``font-*`` /
文字控件高度 ≥ 字体高 / **先样式表后 setFont** / Qt 名字一律模块级 import。
"""
from __future__ import annotations

from ui.i18n import tr

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget)

from ui import theme, widgets

#: 芯片网格列数（参考截图是 4 列；窗口更宽时也就 4 列，不再多分）
GRID_COLS = 4

#: 芯片尺寸（名字要显示全 —— v5b 的教训，这里一开始就按全文自适应）
CHIP_H = 50
CHIP_MIN_W = 250
THUMB = 36


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— 与 ``dashboard.py`` 的 `_lab` 同款（独立文件不互引）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


def _power_text(power) -> str:
    """Power → 显示文本：`16.5` / `1.0`；无值 = `—`。"""
    if power is None:
        return 'Power: —'
    return 'Power: %.1f' % float(power)


class TierChip(QWidget):
    """一张卡组芯片：`[卡图] 显示名 + Power 小字` —— **点一下看卡组构成**。

    ★ 2026-09-21 用户：「在 tier 表里点击这个卡组就能出现卡组构成」→ `clicked`
    发的是**站内英文名**（参照库/构成页的键），窗口接住后切到「卡组构成」页。
    样式一个字没动（只是能点了）+ 鼠标变手型。
    """

    #: 点了这张芯片（参数 = 站内英文名）
    clicked = Signal(str)

    def __init__(self, deck: dict, parent=None):
        super().__init__(parent)
        self.ref_name = str(deck.get('name') or '')
        self.setFixedHeight(CHIP_H)
        self.setMinimumWidth(CHIP_MIN_W)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr('看 %s 的卡组构成（主卡 / 额外）') % self.ref_name
                        if self.ref_name else '')
        self.setAttribute(Qt.WA_StyledBackground, True)
        # ⚠️ 样式表必须按 objectName 限定 —— QWidget 的 styleSheet 会**级联给
        # 子控件**，裸写会让每个 QLabel 都套上边框底色（用户截图里的「文本框」）
        self.setObjectName('tierChip')
        self.setStyleSheet('QWidget#tierChip { background: %s;'
                           ' border: 1px solid %s; border-radius: 4px; }'
                           'QWidget#tierChip QLabel { background: transparent;'
                           ' border: none; }'
                           % (theme.SURFACE2, theme.HAIRLINE))
        # ⚠️ Qt「就近样式表」：芯片一旦自带 styleSheet，应用全局的
        # `QLabel { background: transparent }` 对其子树**失效**，名字标签会画
        # 出调色板底色（黑框）—— 必须在芯片样式里显式给子 QLabel 透明。
        name = str(deck.get('display') or deck.get('name') or '')
        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 6, 10, 6)
        hl.setSpacing(10)
        hl.addWidget(widgets.thumb_label(deck.get('img') or '', THUMB))
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        name_lab = _lab(name, 'body', size=theme.PAGE_F_BODY,
                        weight=QFont.Weight.Medium)
        name_lab.setToolTip(str(deck.get('name') or ''))    # 站内英文名
        col.addWidget(name_lab)
        power_lab = _lab(_power_text(deck.get('power')), 'body',
                         size=theme.PAGE_F_SMALL)
        power_lab.setStyleSheet('color: %s; background: transparent;'
                                % theme.INK_MUTED)
        col.addWidget(power_lab)
        hl.addLayout(col, 1)

    def mousePressEvent(self, ev) -> None:
        """左键点一下 = 看构成（右键/其它键不做事，别抢上下文菜单）。"""
        if ev.button() == Qt.LeftButton and self.ref_name:
            self.clicked.emit(self.ref_name)
            ev.accept()
            return
        super().mousePressEvent(ev)


class TierPage(QWidget):
    """「Tier 表」页：TIER 1 / 2 / 3 / TRENDING 四节 + 卡组芯片网格。

    ★ 芯片现在**可点击**（2026-09-21）：`deck_clicked` 转发站内英文名给窗口，
    窗口切到「卡组构成」页（`ui/deck_view_page.py`）。
    """

    #: 点了某个卡组芯片（参数 = 站内英文名）
    deck_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('Tier 表'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        src = _lab(tr('数据源：masterduelmeta（每月手动跑 fetch_meta_decks 刷新）'),
                   'body', size=theme.PAGE_F_SMALL)
        src.setStyleSheet('color: %s; background: transparent;'
                          % theme.INK_MUTED)
        head.addWidget(src)
        outer.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setObjectName('rowsScroll')       # 无边框（复用 theme.qss 规则）
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.host.setAttribute(Qt.WA_StyledBackground, True)
        self.host.setStyleSheet('background: %s;' % theme.CANVAS)
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(theme.S_XL)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        self._data = dict(data or {})
        self._rebuild()

    def _clear(self):
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _section(self, sec: dict) -> QWidget:
        """一节：TIER N 标题 + 说明 + 芯片网格（4 列）。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.S_MD)
        v.addWidget(_lab(tr(str(sec.get('title') or '')), 'body',
                         size=theme.PAGE_F_BIG, weight=QFont.Weight.Bold))
        v.addWidget(_lab(tr(str(sec.get('desc') or '')), 'body',
                         size=theme.PAGE_F_SMALL))
        decks = list(sec.get('decks') or [])
        if not decks:
            empty = _lab(tr('（本月该分组没有卡组）'), 'body',
                         size=theme.PAGE_F_SMALL)
            empty.setStyleSheet('color: %s; background: transparent;'
                                % theme.INK_MUTED)
            v.addWidget(empty)
            v.addStretch(1)
            return box
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.S_MD)
        for i, deck in enumerate(decks):
            chip = TierChip(deck)
            chip.clicked.connect(self.deck_clicked.emit)
            grid.addWidget(chip, i // GRID_COLS, i % GRID_COLS)
        for j in range(GRID_COLS):
            grid.setColumnStretch(j, 1)
        v.addLayout(grid)
        v.addStretch(1)
        return box

    def _rebuild(self) -> None:
        self._clear()
        sections = list(self._data.get('sections') or [])
        if not any(s.get('decks') for s in sections):
            empty = widgets.label(tr('还没有 Tier 数据 —— 跑一次 '
                                  'tools/fetch_meta_decks.py（每月手动）就有'),
                                  'label')
            empty.setFont(widgets.font(theme.PAGE_F_BODY))
            empty.setWordWrap(True)
            self.col.addWidget(empty)
            self.col.addStretch(1)
            return
        for sec in sections:
            self.col.addWidget(self._section(sec))
        self.col.addStretch(1)
