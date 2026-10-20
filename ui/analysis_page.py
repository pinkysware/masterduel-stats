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
                               QStackedWidget, QVBoxLayout, QWidget)

from core.storage import MODE_GROUP_NAMES
from ui import theme, widgets

#: 三页签（顺序即显示顺序；值 = `went_first` 语义，与 `cross_matrix` 同签名）
TABS = ((None, '综合胜率'), (True, '先攻胜率'), (False, '后攻胜率'))

#: 本页的**两个视图**（P2-1 的解法：手坑汇总**不能**塞进 `TABS` —— 那三个页签的
#: 值就是 `went_first`，塞进去等于传非法值，且两者内容模型完全不同）。
#: 用与页签**并排的第二个控件**切视图，不动现有三页签。
VIEWS = (('matrix', '对阵矩阵'), ('traps', '手坑汇总'))

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
            # ★ BUG-B6-05：`cross_matrix` 的 `col_avgs[].rate` 是 **0–100**
            #   （`w/n*100`），而 `win_rate_color` 的契约是 **0–1**。
            #   直接传 0-100 会让任何胜率都 > 0.5 → **汇总格恒绿**
            #   （12% 也显示成"胜率高"），而同一页的格子本体用
            #   `_bucket_color`（按 0-100、70/50 阈值）是对的 —— 视觉自相矛盾。
            _rate = (c or {}).get('rate')
            main_color = theme.win_rate_color(
                None if _rate is None else float(_rate) / 100.0)
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
    """矩阵**左上角的对角线标注格**（v4，2026-09-19；2026-10-15 用户调换两标签）。

    右上「我方卡组」沿列头方向；**左下 = 当前页签名**（综合胜率 / 先攻胜率 /
    后攻胜率）—— 用户 2026-10-15：「把对手卡组和综合胜率（先攻/后攻胜率）交换」，
    于是「对手卡组」搬到下面那行的行首（贴着对手行更清楚），页签名落到角格里。
    """

    def __init__(self, scope_label=None, parent=None):
        super().__init__(parent)
        #: 左下文字 = 当前页签名（`综合胜率` / `先攻胜率` / `后攻胜率`）
        self._scope = scope_label or tr('综合胜率')
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
                   Qt.AlignLeft | Qt.AlignVCenter, self._scope)


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


#: 手坑汇总表：列 = `[卡组] + 12 张手坑`。每列 `(表头, 最小宽)`。
#: ⚠️ 12 张坑都进表（用户要「12 张手坑」齐全）；卡组列宽些（带图）。
TRAP_DECK_W = 118
#: 每张手坑列：表头 = **卡图**（中文名太长，12 列放不下；图 + tooltip 全名），
#: 格 = `胜率` + 小字 `N场` 两行。58px 够放 `33.3%`@13 与 22px 图。
TRAP_TRAP_W = 58
TRAP_ROW_H = 44           # 两行式（胜率 + 场数）-> 行更高
TRAP_HEAD_H = 34          # 对角标注格要高一点（两行文字 + 斜线）
TRAP_GAP = 2
#: 角格宽（照矩阵 `HEAD_W` 的做法：卡组列够画「我方卡组 / 胜率」两行文字）
TRAP_HEAD_W = 118


def _elide(text: str, width: int, size: int) -> str:
    """超宽就截断加省略号（同 `dashboard._elide`；本文件不引 dashboard）。"""
    fm = QFontMetrics(widgets.font(size))
    return fm.elidedText(str(text or ''), Qt.ElideRight, int(width))


def _trap_img(key) -> str:
    """手坑 key -> **纯卡绘**缓存路径（本地，不联网）；没有返回空串（留占位）。"""
    try:
        from core import hand_traps as _ht
        from core import card_art
        return card_art.cropped_path(_ht.standard_id(key)) or ''
    except Exception:                                        # noqa: BLE001
        return ''


def _cell_texts(cell) -> tuple:
    """一个手坑格 -> `(主行, 副行)`：主 = 胜率，副 = `N场`（用户要带场数）。"""
    if not cell or not int(cell.get('n') or 0):
        return ('—', '')
    rate = cell.get('rate')
    main = _rate_text(rate) if rate is not None else '—'
    return (main, tr('%d 场') % int(cell.get('n') or 0))


class TrapCorner(QWidget):
    """手坑表左上角的**标注格**（2026-10-18 起不再画对角线，用户说太突兀）：

    * **上 = 「对手手坑」（先手表）/「我方手坑」（后手表）**，**贴最右**；
    * **下 = 「我方卡组」**，左对齐（与行首卡组格同侧）。

    ⚠️ 与矩阵的摆法**相反**（矩阵上是我方卡组）：用户 2026-10-17 给了图，照图。
    """

    def __init__(self, trap_side=None, parent=None):
        super().__init__(parent)
        #: 右上那句：先手表 = `对手手坑`、后手表 = `我方手坑`
        self._side = trap_side or tr('对手手坑')
        self.setMinimumSize(TRAP_HEAD_W, TRAP_HEAD_H)

    def paintEvent(self, ev):                               # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        #: ★ 2026-10-18 用户：「去掉分隔线吧太突兀了，把对手手坑/我方手坑
        #: **向右对齐贴到这个表头框的最右**」—— 不再画对角线。
        p.setFont(widgets.font(theme.PAGE_F_SMALL))
        p.setPen(QColor(theme.INK))
        # 上：这一列是谁的手坑 —— **贴最右**（w - 2，只留 2px 呼吸）
        p.drawText(QRectF(0, 0, w - 2, h * 0.55),
                   Qt.AlignRight | Qt.AlignVCenter, self._side)
        # 下：行头是我方卡组（左对齐，与行首卡组格同侧）
        p.drawText(QRectF(2, h * 0.45, w - 4, h * 0.55),
                   Qt.AlignLeft | Qt.AlignVCenter, tr('我方卡组'))



class HandTrapTable(QWidget):
    """一张手坑表：行 = 我方卡组，列 = 12 张手坑，格 = 我的胜率（+ 小字场数）。

    * 表头行：`卡组 | <12 张坑名>`；坑名带组色小标（抽/锁/无/解）省地方；
    * 格：胜率（分档染色）+ 小字 `N场`；**没发生过的格**画淡 `—`；
    * 列宽按视口自适应（12 列 + 卡组列；太窄就横向滚动，不硬挤）。
    """

    def __init__(self, traps, side='对手手坑', parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('HandTrapTable { background: %s;'
                           ' border: 1px solid %s; border-radius: 6px; }'
                           % (theme.SURFACE1, theme.HAIRLINE))
        self._traps = list(traps or [])
        #: 角格右上那句（`对手手坑` / `我方手坑`）
        self._side = side
        self._rows_data = []
        self._widths = tuple(self._calc_widths(self._avail()))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_XS)
        self._head = QWidget()
        self._head.setFixedHeight(TRAP_HEAD_H)
        lay.addWidget(self._head)
        lay.addWidget(widgets.hairline())
        self.rows_box = QVBoxLayout()
        self.rows_box.setContentsMargins(0, 0, 0, 0)
        self.rows_box.setSpacing(2)
        lay.addLayout(self.rows_box)
        self._build_head()

    # ------------------------------------------------------------ 列宽
    def _avail(self) -> int:
        """表内可用宽。⚠️ 要减**竖滚动条宽** —— 内容一高 vbar 出现、表宽瞬间少 ~14px，
        不预留就会冒横向滚动条（与战绩页同一坑，2026-10-16 实测）。"""
        vp = self.width() or (self.parent().width() if self.parent() else 0)
        vsb = 0
        try:
            par = self.parent()
            while par is not None and not hasattr(par, 'verticalScrollBar'):
                par = par.parent()
            if par is not None:
                vsb = par.verticalScrollBar().sizeHint().width()
        except Exception:                                    # noqa: BLE001
            vsb = 0
        return int(vp - vsb - theme.S_MD * 2 - theme.S_XS * 2)

    def _calc_widths(self, avail: int) -> tuple:
        """卡组列固定，12 张坑列**平分**剩余（不够就各自最小宽，横向滚）。

        ⚠️ N 个控件 = **N-1 个间距**（卡组 + 12 坑 = 13 控件 -> 12 个间距）；
        余数要**补到前几列**，否则合计差几像素（测试锁「宽了必须铺满」）。
        """
        n = len(self._traps)
        if n == 0:
            return (TRAP_DECK_W,)
        gaps = n * TRAP_GAP                 # 13 控件 -> 12 间距
        need = TRAP_DECK_W + n * TRAP_TRAP_W + gaps
        if avail <= need:
            return (TRAP_DECK_W,) + (TRAP_TRAP_W,) * n
        extra = avail - need
        each, rem = divmod(extra, n)
        cols = [TRAP_DECK_W] + [TRAP_TRAP_W + each] * n
        for i in range(rem):               # 余数补到前几列，保证合计 == avail
            cols[i + 1] += 1
        return tuple(cols)

    def _apply_widths(self) -> None:
        w = self._calc_widths(self._avail())
        if w == self._widths:
            return
        self._widths = w
        self._build_head()
        self.set_rows(self._rows_data)

    def resizeEvent(self, ev) -> None:                    # noqa: N802
        super().resizeEvent(ev)
        self._apply_widths()

    # ------------------------------------------------------------ 表头/行
    def _build_head(self) -> None:
        old = self._head.layout()
        if old is None:
            old = QHBoxLayout(self._head)
            old.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
            old.setSpacing(TRAP_GAP)
        while old.count():
            it = old.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        #: 左上 = **对角标注格**（照矩阵；右上「我方卡组」、左下本表页签名）
        old.addWidget(TrapCorner(self._side))
        for i, t in enumerate(self._traps):
            #: ★ 表头 = **卡图**（中文手坑名太长，12 列放不下）——
            #: 图走 `core.card_art.cropped_path`（纯卡绘，与战绩页同一族），
            #: 没缓存留空占位；**全名进 tooltip**。
            cell = QWidget()
            cell.setFixedWidth(int(self._widths[i + 1]))
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(0)
            cl.addStretch(1)
            cl.addWidget(widgets.thumb_label(_trap_img(t.get('key')), 22,
                                             radius=theme.R_XS))
            cl.addStretch(1)
            cell.setToolTip(t.get('name') or '')
            old.addWidget(cell)
        old.addStretch(1)

    def set_rows(self, rows) -> None:
        self._rows_data = list(rows or [])
        while self.rows_box.count():
            it = self.rows_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for r in self._rows_data:
            self.rows_box.addWidget(self._row(r))

    def _row(self, row: dict) -> QWidget:
        w = QWidget()
        w.setFixedHeight(TRAP_ROW_H)
        hl = QHBoxLayout(w)
        hl.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
        hl.setSpacing(TRAP_GAP)
        wids = self._widths
        # 卡组格（行头，与左上角格同宽）
        deck_box = QWidget()
        dl = QHBoxLayout(deck_box)
        dl.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
        dl.setSpacing(theme.S_XS)
        dl.addWidget(widgets.thumb_label(row.get('img') or '', 20,
                                         radius=theme.R_XS))
        name = str(row.get('deck') or '')
        lab = _lab(_elide(name, wids[0] - 40, theme.PAGE_F_BODY), 'body',
                   size=theme.PAGE_F_BODY)
        lab.setToolTip(name)
        dl.addWidget(lab)
        dl.addStretch(1)
        deck_box.setFixedWidth(int(wids[0]))
        hl.addWidget(deck_box)
        cells = row.get('cells') or {}
        for i, t in enumerate(self._traps):
            cell = cells.get(t.get('key')) or {}
            main, sub = _cell_texts(cell)
            color = (_bucket_color(cell.get('rate'))
                     if int(cell.get('n') or 0) else theme.NEUTRAL)
            hl.addWidget(_two_line_cell(main, sub, wids[i + 1], color))
        hl.addStretch(1)
        return w


def _two_line_cell(main: str, sub: str, width: int, main_color: str,
                   parent=None) -> QWidget:
    """两行格：主行（胜率）+ 副行（小字场数），整体居中定宽。"""
    box = QWidget(parent)
    box.setFixedWidth(int(width))
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    mlab = _lab(main, 'body', size=theme.PAGE_F_SMALL)
    mlab.setAlignment(Qt.AlignCenter)
    mlab.setStyleSheet('color: %s; background: transparent;' % main_color)
    lay.addWidget(mlab)
    slab = _lab(sub or '', 'label', size=theme.PAGE_F_SMALL - 2)
    slab.setAlignment(Qt.AlignCenter)
    slab.setStyleSheet('color: %s; background: transparent;' % theme.INK_MUTED)
    lay.addWidget(slab)
    lay.addStretch(1)
    return box


class _TrapTableBlock(QWidget):
    """一张表 + 标题行。标题 = 「先手胜率」/「后手胜率」（用户 2026-10-17 定）。

    表头角格照用户给的图：**右上 = 对手手坑（先手表）/ 我方手坑（后手表）**、
    左下 = 我方卡组。
    """

    def __init__(self, title: str, traps, side: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.S_XS)
        head = QHBoxLayout()
        head.setSpacing(theme.S_SM)
        #: 标题（先手 / 后手）
        head.addWidget(_lab(title, 'body', size=theme.PAGE_F_BODY,
                            weight=QFont.Weight.Medium))
        #: ⚠️ **不加说明小字**（用户 2026-10-17：「不要在表格标题加说明」）——
        #: 标题只留「先手」/「后手」；角格照矩阵（右上我方卡组 / 左下我的胜率）。
        head.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(head)
        lay.addWidget(wrap)
        self.table = HandTrapTable(traps, side=side)
        lay.addWidget(self.table)

    def set_rows(self, rows) -> None:
        self.table.set_rows(rows)


class TrapSummaryView(QWidget):
    """「手坑汇总」视图体（可滚动）：**两张表**（我后手·我用 / 我先手·敌用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S_MD)
        self._note = _lab('', 'muted', size=theme.PAGE_F_SMALL)
        outer.addWidget(self._note)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('rowsScroll')
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

    def _clear(self) -> None:
        """清空整块（含旧表的空态标签 + 每次追加的 stretch —— 见漂移 bug 注释）。"""
        while self.col.count():
            it = self.col.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def set_data(self, data: dict) -> None:
        data = dict(data or {})
        self._clear()
        n_games = int(data.get('n_games') or 0)
        n_cal = int(data.get('n_calibre') or 0)
        if n_cal and n_games < n_cal:
            self._note.setText(
                tr('共 %d 场入统计 / %d 场有逐卡记录（其余为 API 补录或座位/回合判不出）')
                % (n_games, n_cal))
        else:
            self._note.setText(tr('共 %d 场入统计' % n_games))
        traps = list(data.get('traps') or [])
        #: ★ 用户 2026-10-17：先手在**上**、后手在下；标题只写「先手」/「后手」；
        #: 「谁的手坑」交给表头角格（左下那句）+ 标题右侧的小字说明。
        specs = ((tr('先手胜率'), '对手手坑', 'first_opp'),
                 (tr('后手胜率'), '我方手坑', 'second_my'))
        any_row = False
        for title, side, key in specs:
            rows = list(data.get(key) or [])
            blk = _TrapTableBlock(title, traps, side)
            blk.set_rows(rows)
            self.col.addWidget(blk)
            any_row = any_row or bool(rows)
        if not any_row:
            self.col.insertWidget(
                0, _lab(tr('还没有可统计的对局（需要逐卡记录 + 先/后已知）'),
                        'muted', size=theme.PAGE_F_BODY))
        self.col.addStretch(1)


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
        self._mode_filter = None          # 多选控件实例（`_filter_button('mode')` 里建）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('对局分析'), 'body', size=theme.PAGE_F_TITLE))
        #: ★ 视图切换（2026-10-16）：对阵矩阵 / 手坑汇总 —— **不能**塞进下面三页签
        #: （页签值 = went_first）。放在标题右边、页签左边。
        self._view = 'matrix'
        self._view_buttons = {}
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        for vkey, vname in VIEWS:
            vb = QPushButton(tr(vname))
            vb.setCheckable(True)
            vb.setCursor(Qt.PointingHandCursor)
            vb.setStyleSheet('QPushButton { background: transparent;'
                             ' border: 1px solid %s; border-radius: 4px;'
                             ' padding: 3px 10px; color: %s; }'
                             'QPushButton:checked { background: %s;'
                             ' border: 1px solid %s; color: %s; }'
                             % (theme.HAIRLINE, theme.INK_MUTED,
                                theme.SURFACE3, theme.ACCENT, theme.INK))
            vb.setFont(widgets.font(theme.PAGE_F_SMALL))
            vb.setFixedHeight(vb.fontMetrics().height() + 14)
            vb.clicked.connect(lambda _c=False, v=vkey: self._pick_view(v))
            self._view_group.addButton(vb)
            self._view_buttons[vkey] = vb
            head.addWidget(vb)
        self._view_buttons['matrix'].setChecked(True)
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
        #: 手坑视图要隐藏「三页签 + 筛选器」那一行？不 —— 筛选器（月/模式）两视图共用，
        #: 只隐藏三页签（它们只对矩阵有意义）。存页签载体以便切换显隐。
        self._filters_row = filters

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
        #: ★ 两个视图用 QStackedWidget 切（2026-10-16）：0=矩阵（原样）1=手坑汇总
        self.trap_view = TrapSummaryView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.scroll)
        self.stack.addWidget(self.trap_view)
        outer.addWidget(self.stack, 1)

    # ------------------------------------------------------------ 筛选器
    _KEY_LABEL = {'date': '日期', 'mode': '模式'}

    def _filter_button(self, key: str) -> QPushButton:
        from ui.i18n import lang
        #: ★ 2026-10-14 用户：「这几个模式做成 excel 那种可以打勾筛选的形式」
        #:   —— 模式用**多选**控件（日期仍是单选下拉）。
        #:   ⚠️ 本页的筛选**与战绩页各自独立**（用户原话），两边各存各的 QSettings。
        if key == 'mode':
            from ui.mode_filter import ModeFilterButton
            sep = '：' if lang() == 'cn' else ': '
            btn = ModeFilterButton(prefix='%s%s' % (tr('模式'), sep))
            btn.set_selected(self._data.get('mode_group') or [])
            btn.changed.connect(self.mode_selected.emit)
            self._mode_filter = btn
            return btn
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
        #: 模式是**多选**（列表）—— 用公共的 label_for（空 = 全部模式）
        from ui.mode_filter import label_for
        return label_for(value)

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
        #: 模式是多选控件（`ModeFilterButton`）—— **它自己的文字自己管**
        #: （含「模式：」前缀与勾选摘要）。这里只把数据层的勾选**同步进去**
        #: （切页/换语言/切 demo 后按钮不该停在旧选择上）。
        mt = getattr(self, '_mode_filter', None)
        if mt is not None:
            mt.set_selected(self._data.get('mode_group') or [])

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        self._data = dict(data or {})
        self._scope = self._data.get('went_first')
        for value, btn in self._tab_buttons.items():
            btn.setChecked(value == self._scope)
        self._refresh_filter_texts()
        self.trap_view.set_data(self._data.get('hand_traps') or {})
        self._rebuild()
        self._apply_view()

    def _pick_view(self, view: str) -> None:
        """切视图（矩阵 / 手坑汇总）—— 只换中间那块 + 页签显隐，不重取数据。"""
        view = view if view in ('matrix', 'traps') else 'matrix'
        if view == self._view:
            return
        self._view = view
        self._apply_view()

    def _apply_view(self) -> None:
        """按当前视图切 stack + 显隐三页签（三页签只对矩阵有意义）。"""
        self.stack.setCurrentIndex(1 if self._view == 'traps' else 0)
        for btn in self._tab_buttons.values():
            btn.setVisible(self._view == 'matrix')

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
        #: 当前页签名（综合胜率 / 先攻胜率 / 后攻胜率）—— 2026-10-15 起它进**角格**
        #: （用户：「把对手卡组和综合胜率交换」）。
        scope_name = tr(dict(TABS).get(self._scope, '综合胜率'))
        #: 列头行：左上角**对角线标注格**（右上「我方卡组」/ 左下 = 页签名）
        #: + 各我方卡组列头（[卡图] 名字）
        self.grid.addWidget(CornerHeader(scope_name), 0, 0)
        for j, my in enumerate(cols):
            self.grid.addWidget(self._col_head(my, (avgs.get(my) or {})
                                               .get('img')), 0, j + 1)
        #: 列头下第一行 = 各列我方卡组的**该页签汇总**；行首标签 = 「对手卡组」
        #: （用户 2026-10-15 与角格里的页签名对调 —— 这一行往下全是按对手排的行）。
        opp_lab = _lab(tr('对手卡组'), 'body', size=theme.PAGE_F_SMALL)
        opp_lab.setMinimumWidth(HEAD_W)
        self.grid.addWidget(opp_lab, 1, 0)
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
