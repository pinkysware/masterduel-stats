# -*- coding: utf-8 -*-
"""看板「卡组构成」页（D3）—— 某个卡组的**主卡 / 额外卡图网格**。

入口（用户 2026-09-21 拍板）：**Tier 表上的卡组芯片可点击** → 切到本页；
卡组页「卡组构成」页签也内嵌它。形态照 masterduelmeta 的 `Top Main Deck` /
`Top Extra Deck`：每格一张**完整卡图** + 卡名 + `3x in 100%`
（`at` = 常带张数、`per` = 有百分之几的样本带它）。

2026-09-21 二轮（用户「可以点击但是 UI 需要优化」）改了几处：

* **网格列数随可视区宽度自适应**（原固定 6 列，最小窗口 1200×620 下
  528 > 可视 481 → 必出横向滚动条）—— 本页**不许出现横向滚动条**
  （`ScrollBarAlwaysOff` + 按 `viewport().width()` 算列数，`resizeEvent` 重排）；
* 栏目标题带数量（`Top Main Deck (45)`）；格子加**卡名**（按宽省略 + tooltip）；
* 卡图 62×90 → 84×122；「换卡组」下拉**可搜索**（中文 / 别名 / 日文都能敲）；
* 页头补参照库快照日期 + 「只看常带 ≥N%」筛选 + 排序切换 + `unknown` 小字。

数据由 `core.ui_state.deck_composition()` 备好（**窗口只画、不查库**；
参照库 = `data/mdm_deck_cards.json`，99 副）。**筛选与排序在本页做**
（只动 `per`/`at`/`name`，不重新取数）。卡图走 `core.card_art`
（本地缓存只读；缺的**后台逐张下**，下一张补一张，不卡界面、失败留占位）。

Qt 纪律同 `tier_page.py` / `banlist_page.py`：QSS 按 objectName 限定、
容器自带样式表时要给子 QLabel 显式透明、文字控件高度 ≥ 字体高，
**样式表在字体之前**（`ui/widgets.py` 模块头两条硬规矩）。
"""
from __future__ import annotations

from ui.i18n import tr

import os
import threading
import time

from PySide6.QtCore import QStringListModel, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QCompleter, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ui import theme, widgets

#: 卡图尺寸 —— **完整卡**（59:86 长方形，KeepAspectRatio 不裁切）
ART_W, ART_H = 84, 122
#: 格子宽 = 卡图宽（卡名与计数文字都按它省略）
CELL_W = ART_W
#: 一格里的两行小字（卡名 / 计数）行高
LINE_H = 18
#: 格子（卡图 + 卡名 + 计数）
CELL_H = ART_H + 4 + LINE_H * 2
#: 网格缝隙 = `theme.S_MD`（算列数时要跟着用）
SP = theme.S_MD

#: 「只看常带」阈值选项（值 = `per` 下限，0 = 全看）
PER_CHOICES = ((0, '只看常带 ≥ 0%'), (10, '只看常带 ≥ 10%'),
               (20, '只看常带 ≥ 20%'), (50, '只看常带 ≥ 50%'))
#: 排序选项（key, 标签）
SORT_CHOICES = (('per', '按占比'), ('at', '按张数'), ('name', '按卡名'))


def cols_for_width(width: int) -> int:
    """可视区宽度 → 网格列数（**放得下才给列**，故不会撑出横向滚动条）。

    `n` 列占宽 = ``n*CELL_W + (n-1)*SP`` ≤ `width`，解出 ``n ≤ (width+SP)//(CELL_W+SP)``。
    最小窗口 1200×620 下每栏可视约 471px → **5 列**（5*84+4*12 = 468 ≤ 471 ✓）。
    """
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = 0
    return max(1, (w + SP) // (CELL_W + SP))


def _scaled_art(path: str, w: int, h: int):
    """完整卡图按比例缩放（**不裁切**）；缺图返回 None。"""
    if not path or not os.path.exists(path):
        return None
    img = QImage(path)
    if img.isNull():
        return None
    return QPixmap.fromImage(img.scaled(w, h, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— 与 `tier_page.py` 同款（独立文件不互引）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


def elide(text: str, width: int, size: int = theme.PAGE_F_SMALL) -> str:
    """按**像素宽**省略卡名（tooltip 给全名）。"""
    fm = QFontMetrics(widgets.font(size))
    return fm.elidedText(str(text or ''), Qt.ElideRight, max(8, int(width)))


def count_text(row: dict) -> str:
    """格子下面那行字：`3x in 100%`（`at` 缺了就省略 `3x `）。"""
    per = row.get('per')
    at = row.get('at')
    if at:
        return '%dx in %s%%' % (int(at), int(per))
    return 'in %s%%' % int(per)


def sort_rows(rows: list, key: str = 'per') -> list:
    """按 `per` / `at` / 卡名排序（**不原地改**数据层给的表）。"""
    out = list(rows or [])
    if key == 'at':
        out.sort(key=lambda r: (-(r.get('at') or 0), -(r.get('per') or 0),
                                str(r.get('name') or '')))
    elif key == 'name':
        out.sort(key=lambda r: str(r.get('name') or ''))
    else:
        out.sort(key=lambda r: (-(r.get('per') or 0), -(r.get('at') or 0),
                                str(r.get('name') or '')))
    return out


class CardCell(QWidget):
    """一格：完整卡图 + 卡名（省略） + `Nx in P%`。"""

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.card_id = int(row.get('id') or 0)
        name = str(row.get('name') or '')
        self.setFixedSize(CELL_W, CELL_H)
        self.setToolTip('%s\n%s' % (name, count_text(row)) if name else count_text(row))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.art = QLabel()
        self.art.setFixedSize(ART_W, ART_H)
        self.art.setAlignment(Qt.AlignCenter)
        self.art.setStyleSheet('background: %s; border-radius: 3px; color: %s;'
                               % (theme.SURFACE2, theme.INK_SUBTLE))
        # 卡图还没下回来 → **极淡的省略号占位**（别加 spinner）
        self.art.setText('…')
        self.art.setFont(widgets.font(theme.PAGE_F_BODY))
        pix = _scaled_art(row.get('art') or '', ART_W, ART_H)
        if pix is not None:
            self.art.setPixmap(pix)
        col.addWidget(self.art, 0, Qt.AlignHCenter)

        self.name_lab = QLabel(elide(name, CELL_W))
        self.name_lab.setAlignment(Qt.AlignCenter)
        self.name_lab.setFixedHeight(LINE_H)
        self.name_lab.setStyleSheet('color: %s; background: transparent;'
                                    % theme.INK)
        self.name_lab.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.name_lab.setToolTip(name)
        col.addWidget(self.name_lab)

        self.count_lab = QLabel(count_text(row))
        self.count_lab.setAlignment(Qt.AlignCenter)
        self.count_lab.setFixedHeight(LINE_H)
        self.count_lab.setStyleSheet('color: %s; background: transparent;'
                                     % theme.INK_MUTED)
        self.count_lab.setFont(widgets.font(theme.PAGE_F_SMALL))
        col.addWidget(self.count_lab)

    def set_art(self, path: str) -> None:
        """就地换图（后台下好一张时由页面调；不整页重画）。"""
        pix = _scaled_art(path, ART_W, ART_H)
        if pix is not None:
            self.art.setText('')
            self.art.setPixmap(pix)


class DeckViewPage(QWidget):
    """「卡组构成」页：主卡 / 额外两栏自适应卡图网格 + 可搜索的「换卡组」下拉。"""

    #: 下拉换了卡组（参数 = 站内英文名）—— 窗口据此重新取数
    deck_selected = Signal(str)
    #: 点了「← 回 Tier 表」（**同页切页签** —— Tier 表就是左边那格，
    #: 由 `DecksPage._on_back` 处理；窗口只管转发自己的 `back_requested`）
    back_requested = Signal()
    #: 后台下到一张新图（参数 = 卡 id）
    _art_ready = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._loading = False          # 程序化填下拉时不触发 deck_selected
        self._art_busy = False
        self._per_min = 0
        self._sort = 'per'
        self._cells = {'main': [], 'extra': []}   # 当前显示的格（筛/排之后）
        self._spans = {'main': [], 'extra': []}   # 空态文案（跨列）
        self._cols = {'main': 0, 'extra': 0}
        #: 搜索词（小写）-> 站内英文名 —— 「换卡组」下拉**任何叫法**都能搜
        self._search = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        self.btn_back = QPushButton('← ' + tr('回 Tier 表'))
        self.btn_back.setObjectName('ghost')
        self.btn_back.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.clicked.connect(self.back_requested.emit)
        head.addWidget(self.btn_back)
        self.title = _lab(tr('卡组构成'), 'body', size=theme.PAGE_F_TITLE)
        head.addWidget(self.title, 1)
        self.picker = QComboBox()
        self.picker.setEditable(True)                  # ★ 可搜索（中文/别名/日文）
        self.picker.setInsertPolicy(QComboBox.NoInsert)
        self.picker.setFont(widgets.font(theme.F_BODY))
        self.picker.setMinimumWidth(260)
        # ⚠️ 不能用 `QComboBox` 自带的 completer —— 那个**共用 combo 的 model**，
        #    往里塞搜索词会把卡片列表整个换掉。自己给一个独立 model 的 completer。
        self._comp_model = QStringListModel(self)
        comp = QCompleter(self._comp_model, self)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.activated.connect(self._on_completer_pick)
        self.picker.setCompleter(comp)
        self.picker.currentIndexChanged.connect(self._on_pick)
        self.picker.lineEdit().returnPressed.connect(self._on_return)
        head.addWidget(self.picker)
        outer.addLayout(head)

        # 副标题 + `unknown` 提示 + 筛选控件**挤一行**
        # （P2：最小窗口下每一点纵向空间都值钱；提示空着时直接隐藏，不占位）
        row = QHBoxLayout()
        row.setSpacing(theme.S_MD)
        self.sub = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.sub.setStyleSheet('color: %s; background: transparent;'
                               % theme.INK_MUTED)
        row.addWidget(self.sub)
        row.addStretch(1)
        #: `unknown` 提示（**页头小字**，不用滚到底才看得见）
        self.note = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.note.setStyleSheet('color: %s; background: transparent;'
                                % theme.INK_SUBTLE)
        self.note.hide()
        row.addWidget(self.note)
        self.per_box = QComboBox()
        for val, label in PER_CHOICES:
            self.per_box.addItem(tr(label), val)
        self.per_box.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.per_box.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.per_box)
        self.sort_box = QComboBox()
        for key, label in SORT_CHOICES:
            self.sort_box.addItem(tr(label), key)
        self.sort_box.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.sort_box.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.sort_box)
        outer.addLayout(row)

        cols = QHBoxLayout()
        cols.setSpacing(theme.S_XL)
        self.panels = {}
        self.areas = {}
        self.hosts = {}
        self.titles = {}
        for key, title in (('main', 'Top Main Deck'),
                           ('extra', 'Top Extra Deck')):
            cols.addWidget(self._make_panel(key, title), 1)
        outer.addLayout(cols, 1)
        self._art_ready.connect(self._on_art_ready)

        #: 重排防抖（`resizeEvent` 会连发；0ms 合并成一次）
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(0)
        self._relayout_timer.timeout.connect(self._relayout_all)

    def _make_panel(self, key: str, title: str) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.S_MD)
        lab = _lab(title, 'body', size=theme.PAGE_F_BIG,
                   weight=QFont.Weight.Bold)
        self.titles[key] = lab
        v.addWidget(lab)
        area = QScrollArea()
        area.setObjectName('rowsScroll')          # 无边框（复用 theme.qss 规则）
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        # ★ 硬指标：本页**不得出现横向滚动条**（列数自己算，放不下就减列）
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet('background: %s;' % theme.CANVAS)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SP)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        area.setWidget(host)
        v.addWidget(area, 1)
        self.panels[key] = grid
        self.areas[key] = area
        self.hosts[key] = host
        return box

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        self._data = dict(data or {})
        self._rebuild(fill_picker=True)
        self._warm_art()

    def current_deck(self) -> str:
        return str(self._data.get('name') or '')

    def _clear_cells(self) -> None:
        for key in ('main', 'extra'):
            for w in self._cells.get(key) or []:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            for w in self._spans.get(key) or []:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            self._cells[key] = []
            self._spans[key] = []

    def _rebuild(self, fill_picker: bool = False) -> None:
        self._clear_cells()
        self._fill_header()
        if fill_picker:
            self._fill_picker()
        self._fill_cells()
        self._relayout_all()

    def _fill_header(self) -> None:
        d = self._data
        self.title.setText(str(d.get('display') or d.get('name') or tr('卡组构成')))
        bits = []
        if d.get('tier'):
            bits.append(str(d['tier']))
        if d.get('total'):
            bits.append('%d %s' % (int(d['total']), tr('份样本')))
        if d.get('avg_main'):
            bits.append('%s %s%s' % (tr('平均主卡'), d['avg_main'], tr('张')))
        stamp = str(d.get('ref_fetched_at') or '')[:10]
        if stamp:
            bits.append('%s %s' % (tr('参照库快照'), stamp))      # ★ 交代数据新旧（月度快照）
        self.sub.setText(' · '.join(bits) or tr('（暂无该卡组构成）'))

        unknown = int(d.get('unknown') or 0)
        self.note.setText((tr('%d 张卡没查到类型，未显示（跑一次 '
                              'tools/build_card_names.py --update 试试）')
                           % unknown)
                          if unknown else '')
        self.note.setVisible(bool(unknown))       # 没缺卡就不占那一行
        has = bool(d.get('has_ref'))
        self.per_box.setEnabled(has)
        self.sort_box.setEnabled(has)

    def _fill_picker(self) -> None:
        """填下拉（程序化填充：别触发 `deck_selected`）。

        项的 **data 恒为站内英文名**；`display` 只给人看。搜索词额外收
        别名 / 日文名，喂给**独立 model 的 completer**（`self._search` 负责把
        任意叫法落回站内名）。
        """
        d = self._data
        self._loading = True
        self.picker.clear()
        self._search = {}
        for opt in (d.get('options') or []):
            label = str(opt.get('display') or opt.get('name') or '')
            if opt.get('tier'):
                label += '  · %s' % opt['tier']
            name = str(opt.get('name') or '')
            self.picker.addItem(label, name)
            for word in ([label, name, str(opt.get('display') or '')]
                         + [str(x) for x in (opt.get('extra') or [])]):
                word = word.strip()
                if word:
                    self._search.setdefault(word.lower(), name)
        # 补全候选 = 所有搜索词（下拉本体仍是「显示名 · Tier」，两者互不干扰）
        self._comp_model.setStringList(sorted(self._search))
        idx = self.picker.findData(d.get('name'))
        self.picker.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading = False

    def _resolve_name(self, text: str):
        """把搜索框里的**任意叫法**落到站内英文名（落不到 `None`）。"""
        q = str(text or '').strip()
        if not q:
            return None
        hit = self._search.get(q.lower())
        if hit:
            return hit
        for label_i in range(self.picker.count()):     # 直接撞 item 文本（带 tier 尾）
            if self.picker.itemText(label_i).strip().lower() == q.lower():
                return self.picker.itemData(label_i)
        items = [(k, v) for k, v in self._search.items()]   # 模糊（真匹配器）
        try:
            from core.deck_match import best_score
            best, name = None, None
            for word, nm in items:
                sc = best_score(q, [word])
                if sc is not None and (best is None or sc < best):
                    best, name = sc, nm
            if name:
                return name
        except Exception:                                  # noqa: BLE001
            pass
        return None

    def _fill_cells(self) -> None:
        """按当前筛选 / 排序铺格子（数据层契约不动，只重排重筛）。"""
        d = self._data
        if not d.get('has_ref'):
            self.titles['main'].setText('Top Main Deck')
            self.titles['extra'].setText('Top Extra Deck')
            #: 2026-10-03 用户：长说明换短句（引擎/混轴条目本来就没有构成）
            msg = widgets.label(tr('暂无该卡组构成'), 'label')
            msg.setFont(widgets.font(theme.PAGE_F_BODY))
            msg.setWordWrap(True)
            self._spans['main'].append(msg)
            return
        for key in ('main', 'extra'):
            rows = [r for r in (d.get(key) or [])
                    if int(r.get('per') or 0) >= int(self._per_min)]
            rows = sort_rows(rows, self._sort)
            all_n = len(d.get(key) or [])
            title = 'Top Main Deck' if key == 'main' else 'Top Extra Deck'
            if self._per_min:
                self.titles[key].setText('%s (%d/%d)' % (title, len(rows), all_n))
            else:
                self.titles[key].setText('%s (%d)' % (title, len(rows)))
            if not rows:
                lab = widgets.label(tr('（这一栏是空的）'), 'label')
                lab.setFont(widgets.font(theme.PAGE_F_SMALL))
                self._spans[key].append(lab)
                continue
            self._cells[key] = [CardCell(r) for r in rows]

    # ------------------------------------------------------------ 排布
    def _relayout_all(self) -> None:
        for key in ('main', 'extra'):
            self._relayout(key)

    def _relayout(self, key: str) -> None:
        """把已有的格子按**当前可视宽度**算出的列数重新摆一遍。"""
        grid = self.panels[key]
        area = self.areas[key]
        host = self.hosts[key]
        width = area.viewport().width() or host.width() or CELL_W + SP
        cols = cols_for_width(width)
        self._cols[key] = cols
        cells = self._cells.get(key) or []
        spans = self._spans.get(key) or []
        for w in list(cells) + list(spans):
            grid.removeWidget(w)
        for j in range(max(grid.columnCount(), cols) + 1):
            grid.setColumnStretch(j, 0)
        for i, cell in enumerate(cells):
            grid.addWidget(cell, i // cols, i % cols,
                           Qt.AlignTop | Qt.AlignLeft)
        # 空态文案跨整行（只有「这一栏没格子」时才存在）
        for i, lab in enumerate(spans):
            grid.addWidget(lab, 0 if not cells else i, 0, 1, cols)
        for j in range(cols):
            grid.setColumnStretch(j, 1)

    def _on_filter(self, _idx: int) -> None:
        """筛选 / 排序切换 —— **不重新取数**，只重排重筛。"""
        self._per_min = int(self.per_box.currentData() or 0)
        self._sort = str(self.sort_box.currentData() or 'per')
        if self._data:
            self._rebuild(fill_picker=False)

    def _on_pick(self, _idx: int) -> None:
        if self._loading:
            return
        name = self._resolve_name(self.picker.currentText())
        if not name or name == self.current_deck():
            return
        self.deck_selected.emit(str(name))

    def _on_return(self) -> None:
        """在下拉里敲字回车（可搜索）—— 落不到就什么都不做。"""
        self._emit_for_text(self.picker.currentText())

    def _on_completer_pick(self, text: str) -> None:
        """从补全弹层里选了一条（选中的是**搜索词**，要落回站内名）。"""
        self._emit_for_text(text)

    def _emit_for_text(self, text: str) -> None:
        name = self._resolve_name(text)
        if not name or name == self.current_deck():
            return
        idx = self.picker.findData(name)
        self._loading = True                  # 程序化选中：别被 `_on_pick` 抢跑
        if idx >= 0:
            self.picker.setCurrentIndex(idx)
        self._loading = False
        self.deck_selected.emit(str(name))

    # ------------------------------------------------------------ 尺寸
    def resizeEvent(self, event) -> None:                    # noqa: N802
        super().resizeEvent(event)
        self._relayout_timer.start()             # 防抖：resize 会连发

    # ------------------------------------------------------------ 卡图（后台）
    def _warm_art(self) -> None:
        """后台把缺的卡图逐张下回来（一次一个线程；已在跑就跳过）。"""
        if self._art_busy:
            return
        missing = []
        for key in ('main', 'extra'):
            for r in (self._data.get(key) or []):
                if not r.get('art'):
                    missing.append(int(r['id']))
        if not missing:
            return
        self._art_busy = True

        def work(ids):
            from core import card_art
            todo = list(ids)
            for _round in range(3):      # 偶发失败有 3 轮机会（同禁限表页的做法）
                still = []
                for cid in todo:
                    try:
                        if card_art.fetch_art(cid=cid):
                            self._art_ready.emit(cid)
                        else:
                            still.append(cid)
                    except Exception:                    # noqa: BLE001
                        still.append(cid)
                todo = still
                if not todo:
                    break
                time.sleep(2.0)
            self._art_busy = False

        threading.Thread(target=work, args=(missing,), daemon=True).start()

    def _on_art_ready(self, cid: int) -> None:
        """一张图下好了 → 就地回填（不整页重画、不打断滚动）。"""
        try:
            from core import card_art
            path = card_art.art_path(cid)
        except Exception:                                    # noqa: BLE001
            return
        if not path:
            return
        for key in ('main', 'extra'):
            for r in (self._data.get(key) or []):
                if int(r.get('id') or 0) == int(cid):
                    r['art'] = path
        for cell in self.findChildren(CardCell):
            if cell.card_id == int(cid):
                cell.set_art(path)
