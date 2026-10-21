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
        #: 当前网格列数（resizeEvent 重排用；None = 还没建过网格）
        self._grid = None
        self._grid_cols_now = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('Tier 表'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        #: ★ 2026-10-21（用户：「Tier 表过期了，但我没找到更新按钮」）——
        #: 页头**必须显示快照日期**，否则看不出数据新旧（原本文案只说
        #: 「每月手动刷新」，等于没有时间信息）。日期由 `set_data()` 按
        #: `tier_page_data()['fetched_at']` 填。
        self.src_label = _lab(tr('数据源：masterduelmeta（每月手动跑 fetch_meta_decks 刷新）'),
                              'body', size=theme.PAGE_F_SMALL)
        self.src_label.setStyleSheet('color: %s; background: transparent;'
                                     % theme.INK_MUTED)
        head.addWidget(self.src_label)
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
        self._apply_snapshot_label()
        self._rebuild()

    def _apply_snapshot_label(self) -> None:
        """页头那行带上**快照日期** —— 用户据此判断要不要重抓。

        没日期时保留原文案（不是「数据缺失」，是这份库没有 `fetched_at`）。
        """
        stamp = str(self._data.get('fetched_at') or '').strip()
        if not stamp:
            return
        self.src_label.setText(
            tr('数据源：masterduelmeta · 快照 %s（每月手动跑 fetch_meta_decks 刷新）')
            % stamp)

    def _clear(self):
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _grid_cols(self) -> int:
        """可视区宽度 → 网格列数（**放得下才给列**，不会撑出横向滚动条）。

        ★ BUG-B6-07：与 `deck_view_page.cols_for_width` 同一口径。
        `n` 列占宽 = ``n*CHIP_MIN_W + (n-1)*S_MD`` ≤ 视口宽，
        解出 ``n ≤ (w+S_MD)//(CHIP_MIN_W+S_MD)``。
        最小窗口 1200×620 下视口约 974px → ``(974+12)//262 = 3`` 列。
        """
        try:
            w = int(self.viewport().width()) if hasattr(self, 'viewport') else 0
        except Exception:
            w = 0
        if w <= 0:                          # 还没 show（取不到 viewport）：用保守值
            w = max(320, self.width() or 0)
        return max(1, (w + theme.S_MD) // (CHIP_MIN_W + theme.S_MD))

    def resizeEvent(self, event) -> None:                    # noqa: N802
        """窗口变了 → 列数可能变 → 重排（防抖合并成一次）。"""
        super().resizeEvent(event)
        if self._grid is None:
            return
        cols = self._grid_cols()
        if cols != self._grid_cols_now:
            self._grid_cols_now = cols
            self._rebuild_grid()

    def _rebuild_grid(self) -> None:
        """按当前列数重建所有芯片网格（列数变化时才调）。"""
        # 简单可靠：整页重铺（数据已在 self._data 里，几百个芯片也就毫秒级）
        self.set_data(self._data)

    def _section(self, sec: dict) -> QWidget:
        """一节：TIER N 标题 + 说明 + 芯片网格（列数随视口）。"""
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
        # ★ BUG-B6-07：列数按**可视宽**算，不再写死 4 列。
        #   4×250 + 3×12 = 1036px > 最小窗口 1200×620 下的视口 974px
        #   → 必出横向滚动条、右侧芯片被裁（deck_view_page 早有同款修法）。
        cols = self._grid_cols()
        self._grid_cols_now = cols
        for i, deck in enumerate(decks):
            chip = TierChip(deck)
            chip.clicked.connect(self.deck_clicked.emit)
            grid.addWidget(chip, i // cols, i % cols)
        for j in range(cols):
            grid.setColumnStretch(j, 1)
        v.addLayout(grid)
        self._grid = grid                    # resizeEvent 重排用
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
