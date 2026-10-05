# -*- coding: utf-8 -*-
"""看板「对局分析」页（D2）—— 我方卡组 × 对手卡组 的胜率矩阵。

设计定稿（``docs/M7对局分析矩阵计划.md`` + M7看板计划 §2.4 v4/v5，2026-09-19）：

* **列 = 我方卡组**（少的做列）、**行 = 对手卡组**；左上角格对角线标注两轴；
* 列头 = [卡图] 卡组名；列头下第一行 = 各列我方卡组的**综合胜率**（随页签变）；
  行头 = [卡图] 对手卡组名 + **场数**（只显示数字，挤在行头右侧不占新列）；
* **三页签**（综合 / 先攻 / 后攻）+ **筛选器**（日期按月 / 模式四类，
  v5 用户拍板：不做段位筛选）；行列轴在当前筛选的综合口径下**算一次并固定**，
  切页签只换格内数据 —— 不跳版、不重排；
* 格 = **分档软染**（≥70% 绿 / 50~69% 黄 / <50% 红，低饱和 tint）+ 胜率% 主字 +
  小字「n场 w-l」；**空格不画卡片**，只留一个淡「—」；
* 镜像局当普通格画；点击跳转不做（Q10 定稿），细节全走 tooltip。

数据由 `core.ui_state.cross_matrix()` 备好（**窗口只画、不查库**），
卡图路径由 app 侧 loader 注入（``img``）。
Qt 纪律同 ``dashboard.py`` / ``present_page.py`` 模块头：QSS 不写 ``font-*`` /
文字控件高度 ≥ 字体高 / **先样式表后 setFont** / Qt 名字一律模块级 import。
"""
from __future__ import annotations

from ui.i18n import tr

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core.storage import MODE_GROUP_NAMES
from ui import theme, widgets

#: 三页签（顺序即显示顺序；值 = `went_first` 语义，与 `cross_matrix` 同签名）
TABS = ((None, '综合胜率'), (True, '先攻胜率'), (False, '后攻胜率'))

#: 胜率分档（v5 用户口径：颜色只代表胜率区间，柔和不过饱和）
BUCKETS = ((70.0, 'WIN'), (50.0, 'WARN'), (0.0, 'LOSE'))

#: 矩阵固定列宽（错一格就「歪」，同礼盒页汇总表的教训）
CELL_W = 88              # 数据格 / 列头宽
CELL_H = 54              # 数据格高（主字 + 小字两行）
NAME_W = 110             # 行头（对手卡组）文字区宽
COUNT_W = 40             # 行头内场数小字宽度（不占独立列，v4b）
AVG_W = 96               # 列汇总格宽（首行「综合胜率」）
HEAD_W = NAME_W + COUNT_W   # 左侧固定区总宽（对角线角格 / 行头共用）
#: 行/列头卡组名的**最大文字宽**（超宽省略 + tooltip 全名）——
#: 「耀圣杀手旋律」/片假名这类长名别把列撑爆（2026-10-05 用户）
NAME_MAX = 140
HEAD_H = 36              # 列头行高（要装下 [卡图] 卡组名）
GRID_GAP = 1             # 格间距（分隔线画在缝里，v5b：要明显的表格线）


def _bucket_color(rate) -> str:
    """胜率 → 分档语义色（None = 中性灰）。"""
    if rate is None:
        return theme.NEUTRAL
    for floor, key in BUCKETS:
        if rate >= floor:
            return getattr(theme, key)
    return theme.LOSE


def _rate_text(rate) -> str:
    """胜率 → 显示文本：整数就不带小数（50%），否则留 1 位（46.6%）。"""
    if rate is None:
        return '-'
    r = float(rate)
    return ('%.0f%%' % r) if abs(r - round(r)) < 0.05 else ('%.1f%%' % r)


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— **高度跟着字体算**（矮一点就垂直裁字）。

    与 ``dashboard.py`` 的 `_lab` 同款（独立文件不互引，Qt 纪律同模块头）。
    """
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


class MatrixCell(QWidget):
    """矩阵的一格：**分档软染底 + 主字胜率% + 小字场次**；空格只留淡「—」。

    自绘走 paintEvent（照 `dashboard.HeatCell` 的路线）—— 不用 QSS 动态底色。
    颜色**只代表胜率区间**（≥70 绿 / 50~69 黄 / <50 红），低饱和 tint，
    不做成警告系统（v5 用户口径）。
    ``kind='avg'`` = 首行列汇总：中性深底 + `win_rate_color` 文字。
    """

    def __init__(self, cell=None, kind: str = 'cell', row_deck: str = '',
                 parent=None):
        super().__init__(parent)
        self._kind = kind
        self._cell = cell
        self._row_deck = row_deck
        #: 定高**不定宽** —— 列宽跟着卡组名全文自适应（v5b：名字必须显示全）
        self.setFixedHeight(CELL_H)
        self.setMinimumWidth(AVG_W if kind == 'avg' else CELL_W)
        if cell is not None:
            self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        c = self._cell or {}
        n = int(c.get('n') or 0)
        if not n:
            return tr('当前筛选 / 页签下没有对局')
        if self._kind == 'avg':
            return tr('合计 %d 场（%d 胜）') % (n, int(c.get('w') or 0))
        return (tr('对手：%s\n我方：%s\n\n胜率：%s\n对局：%d\n战绩：%d胜 %d负%s')
                % (self._row_deck, c.get('deck'), _rate_text(c.get('rate')),
                   n, int(c.get('w') or 0), int(c.get('l') or 0),
                   (tr(' %d平') % int(c.get('d') or 0))
                   if int(c.get('d') or 0) else ''))

    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        c = self._cell
        n = int((c or {}).get('n') or 0)
        p.setPen(Qt.NoPen)
        if self._kind == 'avg':
            p.setBrush(QColor(theme.SURFACE3))
            p.drawRect(QRectF(0, 0, w, h))
        elif c is None or not n:
            #: 空格不画色块（底板的实色背景透上来），只留淡「—」
            p.setPen(QColor(theme.INK_MUTED))
            p.setFont(widgets.font(theme.PAGE_F_SMALL))
            p.drawText(QRectF(0, 0, w, h),
                       Qt.AlignHCenter | Qt.AlignVCenter, '—')
            return
        else:
            rate = c.get('rate')
            p.setBrush(QColor(theme.SURFACE2))
            p.drawRect(QRectF(0, 0, w, h))
            tint = QColor(_bucket_color(None if rate is None
                                        else float(rate)))
            tint.setAlpha(85)                # 软染（用户：太暗看不清，提亮一档）
            p.setBrush(tint)
            p.drawRect(QRectF(0, 0, w, h))

        if self._kind == 'avg':
            main = _rate_text((c or {}).get('rate'))
            main_color = theme.win_rate_color((c or {}).get('rate'))
            f_main = widgets.font(theme.PAGE_F_OVER_TITLE,
                                  weight=QFont.Weight.Bold)
            p.setPen(QColor(main_color))
            p.setFont(f_main)
            p.drawText(QRectF(0, 0, w, h),
                       Qt.AlignHCenter | Qt.AlignVCenter, main)
            return
        main = _rate_text(c.get('rate'))
        sub = tr('%d场 %d-%d') % (n, int(c.get('w') or 0), int(c.get('l') or 0))
        p.setPen(QColor(theme.INK))
        p.setFont(widgets.font(theme.PAGE_F_OVER_TITLE))
        fh = p.fontMetrics().height()
        p.drawText(QRectF(0, h * 0.14, w, fh),
                   Qt.AlignHCenter | Qt.AlignVCenter, main)
        p.setPen(QColor(theme.INK_MUTED))
        p.setFont(widgets.font(theme.PAGE_F_SMALL))
        p.drawText(QRectF(0, h * 0.14 + fh + 2, w, fh),
                   Qt.AlignHCenter | Qt.AlignVCenter, sub)


class CornerHeader(QWidget):
    """矩阵**左上角的对角线标注格**（v4，用户 2026-09-19 拍板）：

    右上「我方卡组」沿列头方向、左下「对手卡组」沿行头方向，对角线分两轴 ——
    Untapped 对阵表同款。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(HEAD_W, HEAD_H)

    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        p.setPen(QColor(theme.HAIRLINE_STRONG))
        p.drawLine(QPointF(0, 0), QPointF(w, h))
        p.setFont(widgets.font(theme.PAGE_F_SMALL))
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(0, 0, w - 6, h * 0.55),
                   Qt.AlignRight | Qt.AlignVCenter, tr('我方卡组'))
        p.drawText(QRectF(6, h * 0.45, w - 6, h * 0.55),
                   Qt.AlignLeft | Qt.AlignVCenter, tr('对手卡组'))


class _GridHost(QWidget):
    """矩阵底板（v5b）：**实色背景**（不透明，用户拍板）+ **明显分隔线**。

    横线画在每两行之间、竖线画在每两列之间，另加表格四边框 ——
    线画在格间距的缝里（GRID_GAP=1），不会被格子的 paintEvent 盖住。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('background: %s;' % theme.SURFACE1)

    def attach(self, grid):
        """装布局（在 QGridLayout(parent) 之后调；paintEvent 用它取格位）。"""
        self._grid = grid

    def paintEvent(self, ev):                                   # noqa: N802
        g = self._grid
        rows, cols = g.rowCount(), g.columnCount()
        if rows < 1 or cols < 1:
            return
        left = g.cellRect(0, 0).left()
        right = g.cellRect(0, cols - 1).right()
        top = g.cellRect(0, 0).top()
        bottom = g.cellRect(rows - 1, 0).bottom()
        p = QPainter(self)
        p.setPen(QColor(theme.HAIRLINE_STRONG))
        for i in range(rows):                    # 横线（行与行之间 + 上沿）
            y = g.cellRect(i, 0).top() - 1
            p.drawLine(left, y, right, y)
        p.drawLine(left, bottom + 1, right, bottom + 1)
        for j in range(cols):                    # 竖线（列与列之间 + 左沿）
            x = g.cellRect(0, j).left() - 1
            p.drawLine(x, top, x, bottom + 1)
        p.drawLine(right + 1, top, right + 1, bottom + 1)


class AnalysisPage(QWidget):
    """「对局分析」页：页头（标题 + 三页签 + 筛选器）+ 口径警示 + 可滚动矩阵。"""

    #: 页签切换（``None``/``True``/``False``，与 `cross_matrix` 同语义）——
    #: 窗口记住状态并带参重取（照 `set_mode_group` 模式，页只上报不查库）
    scope_selected = Signal(object)
    #: 日期筛选（'YYYY-MM' 或 None=全部，v5）—— 月份清单 = 战绩页热力图那批
    month_selected = Signal(object)
    #: 模式筛选（MODE_GROUPS key 或 None=全部，v5；不做段位筛选）
    mode_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._scope = None
        self._month_btn = None
        self._mode_btn = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('对局分析'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        self._tabs_group = QButtonGroup(self)
        self._tabs_group.setExclusive(True)
        self._tab_buttons = {}
        for value, name in TABS:
            btn = QPushButton(tr(name))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            #: 实体按钮样式（v5d 用户拍板：让人一眼看出能点）；选中态用 ACCENT
            #: 描边 —— theme.ACCENT 的纪律就是「tab 选中」这类用途
            btn.setStyleSheet('QPushButton { background: %s;'
                              ' border: 1px solid %s; border-radius: 4px;'
                              ' padding: 3px 12px; color: %s; }'
                              'QPushButton:checked { background: %s;'
                              ' border: 1px solid %s; color: %s; }'
                              % (theme.SURFACE2, theme.HAIRLINE,
                                 theme.INK_MUTED, theme.SURFACE3,
                                 theme.ACCENT, theme.INK))
            btn.setFont(widgets.font(theme.PAGE_F_SMALL))
            btn.setFixedHeight(btn.fontMetrics().height() + 14)
            btn.clicked.connect(lambda _c=False, v=value:
                                self._pick_scope(v))
            self._tabs_group.addButton(btn)
            self._tab_buttons[value] = btn
            head.addWidget(btn)
        self._tab_buttons[None].setChecked(True)
        outer.addLayout(head)

        #: 筛选器行（v5）：高度压在 ~32px，只承担功能不抢表格的视觉重点
        filters = QHBoxLayout()
        filters.setSpacing(theme.S_MD)
        self._month_btn = self._filter_button('date')
        self._mode_btn = self._filter_button('mode')
        filters.addWidget(self._month_btn)
        filters.addWidget(self._mode_btn)
        filters.addStretch(1)
        outer.addLayout(filters)

        self.scroll = QScrollArea()
        self.scroll.setObjectName('rowsScroll')       # 无边框（复用 theme.qss 规则）
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.host = _GridHost()                  # 实色底 + 明显分隔线（v5b）
        self.grid = QGridLayout(self.host)       # ★ 布局必须装在底板上
        self.host.attach(self.grid)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(GRID_GAP)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

    # ------------------------------------------------------------ 筛选器
    _KEY_LABEL = {'date': '日期', 'mode': '模式'}

    def _filter_button(self, key: str) -> QPushButton:
        from ui.i18n import lang
        btn = QPushButton('%s%s%s ▾' % (tr(self._KEY_LABEL[key]),
                                        '：' if lang() == 'cn' else ': ',
                                        tr('全部')))
        btn.setObjectName('modeFilter')            # 复用战绩页筛选的 QSS
        btn.setFont(widgets.font(theme.PAGE_F_SMALL))
        # ⚠️ 全局 QSS 给 QPushButton 配了 6px 垂直内边距，余量给少一个字就被裁
        btn.setFixedHeight(btn.fontMetrics().height() + 14)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._show_menu(btn, key))
        return btn

    def _menu_items(self, key: str):
        """下拉项（值, 显示名）—— **顺序即菜单顺序**（显示名现 tr）。"""
        if key == 'date':
            return [(None, tr('全部'))] + [(m, m) for m in
                                           (self._data.get('months') or [])]
        #: ⚠️ 单人模式不进筛选（2026-10-03 用户口径）
        items = [(None, tr('全部'))]
        items += [(k, tr(v)) for k, v in MODE_GROUP_NAMES.items()
                  if k != 'solo']
        return items

    def _filter_text(self, key: str, value) -> str:
        if key == 'date':
            return value or tr('全部')
        return tr(MODE_GROUP_NAMES.get(value, '全部'))

    def _show_menu(self, btn, key: str) -> None:
        menu = QMenu(btn)
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = (self._data.get('month') if key == 'date'
                   else self._data.get('mode_group'))
        signal = (self.month_selected if key == 'date'
                  else self.mode_selected)
        for k, name in self._menu_items(key):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(k == current)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, kk=k: signal.emit(kk))
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    def _refresh_filter_texts(self) -> None:
        from ui.i18n import lang
        sep = '：' if lang() == 'cn' else ': '
        self._month_btn.setText('%s%s%s ▾'
                                % (tr(self._KEY_LABEL['date']), sep,
                                   self._filter_text('date',
                                                     self._data.get('month'))))
        self._mode_btn.setText('%s%s%s ▾'
                               % (tr(self._KEY_LABEL['mode']), sep,
                                  self._filter_text('mode',
                                                    self._data.get('mode_group'))))

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        self._data = dict(data or {})
        self._scope = self._data.get('went_first')
        for value, btn in self._tab_buttons.items():
            btn.setChecked(value == self._scope)
        self._refresh_filter_texts()
        self._rebuild()

    def _pick_scope(self, value) -> None:
        if value == self._scope:
            return
        self.scope_selected.emit(value)

    def _clear(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _col_head(self, name, img) -> QWidget:
        """列头 = [卡图] 卡组名（图在文字左边；**名字显示全**，v5b）。"""
        w = QWidget()
        w.setFixedHeight(HEAD_H)
        name = str(name or '')
        fm = QFontMetrics(widgets.font(theme.PAGE_F_SMALL))
        #: ★ 名字有**最大宽**（超宽省略，tooltip 全名 —— 2026-10-05 用户）
        text = fm.elidedText(name, Qt.ElideRight, NAME_MAX)
        w.setMinimumWidth(max(CELL_W, min(fm.horizontalAdvance(name), NAME_MAX)
                              + 48))
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 0, 2, 0)
        hl.setSpacing(5)
        hl.addWidget(widgets.thumb_label(img or '', 22))
        lab = _lab(text, 'body', size=theme.PAGE_F_SMALL)
        lab.setToolTip(name)
        hl.addWidget(lab, 1)
        return w

    def _row_head(self, row: dict) -> QWidget:
        """行头 = [卡图] 对手卡组名 + 场数小字（不占独立列；**名字显示全**，v5b）。"""
        n = int(row.get('n') or 0)
        w = QWidget()
        w.setFixedHeight(CELL_H)
        name = str(row.get('deck') or '')
        fm = QFontMetrics(widgets.font(theme.PAGE_F_BODY))
        #: ★ 名字有**最大宽**（超宽省略，tooltip 全名 —— 2026-10-05 用户）
        text = fm.elidedText(name, Qt.ElideRight, NAME_MAX)
        w.setMinimumWidth(max(HEAD_W, min(fm.horizontalAdvance(name), NAME_MAX)
                              + 70))
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 0, 6, 0)
        hl.setSpacing(6)
        hl.addWidget(widgets.thumb_label(row.get('img') or '', 22))
        lab = _lab(text, 'body', size=theme.PAGE_F_BODY)
        lab.setToolTip(name)
        hl.addWidget(lab, 1)
        cnt = _lab(str(n), 'body', size=theme.PAGE_F_SMALL)
        cnt.setToolTip(tr('%s · %d 场') % (row.get('deck'), n))
        hl.addWidget(cnt)
        return w

    def _rebuild(self) -> None:
        self._clear()
        cols = list(self._data.get('cols') or [])
        rows = list(self._data.get('rows') or [])
        if not rows or not cols:
            empty = widgets.label(tr('还没有可统计的对局 —— 打完天梯/巅峰对局并在'
                                  '「战绩」页标注对手卡组后，这里会出对阵矩阵'),
                                  'label')
            empty.setFont(widgets.font(theme.PAGE_F_BODY))
            empty.setWordWrap(True)
            self.grid.addWidget(empty, 0, 0)
            return
        avgs = {a.get('deck'): a for a in (self._data.get('col_avgs') or [])}
        #: 列头行：左上角**对角线标注格** + 各我方卡组列头（[卡图] 名字）
        self.grid.addWidget(CornerHeader(), 0, 0)
        for j, my in enumerate(cols):
            self.grid.addWidget(self._col_head(my, (avgs.get(my) or {})
                                               .get('img')), 0, j + 1)
        #: 列头下第一行 = 我方卡组的综合胜率（行头文字随页签变，值随页签重算）
        scope_lab = _lab(tr(dict(TABS).get(self._scope, '综合胜率')),
                         'body', size=theme.PAGE_F_SMALL)
        scope_lab.setMinimumWidth(HEAD_W)
        self.grid.addWidget(scope_lab, 1, 0)
        for j, my in enumerate(cols):
            self.grid.addWidget(MatrixCell(avgs.get(my) or {}, kind='avg'),
                                1, j + 1)
        #: 对手卡组行（行头 = [卡图] 名字 + 场数）
        for i, row in enumerate(rows):
            self.grid.addWidget(self._row_head(row), i + 2, 0)
            by_my = {c.get('deck'): c for c in (row.get('cells') or [])}
            for j, my in enumerate(cols):
                self.grid.addWidget(MatrixCell(by_my.get(my),
                                               row_deck=str(row.get('deck')
                                                             or '')),
                                    i + 2, j + 1)
        #: 🔴 2026-10-03 实测事故：切日期后第二列撑爆 —— stretch 是**累积**的：
        #:   10 月只有闪刀姬一个我方卡组（单列 -> 它的列 stretch=1），切回 9 月
        #:   残留还在，多余宽度全灌进那一列。重建前先清零全部行列 stretch。
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        self.grid.setColumnStretch(len(cols) + 1, 1)
        self.grid.setRowStretch(len(rows) + 2, 1)
