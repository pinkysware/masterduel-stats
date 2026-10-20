# -*- coding: utf-8 -*-
"""看板「禁限表」页（D3 第二刀）—— 现在的禁限状态 + 变更史。

数据源 = 本地 `banlist_changes` 表（`tools/fetch_banlist.py` **每月手动**刷新，
本页纯只读、不联网）。用户口径（2026-09-12）：**中文只有三个词、不带数字** ——
`Forbidden`=禁止 / `Limited 1`=限制 / `Limited 2`=准限制（能放 2 张）。
状态显示名唯一来源 = `tools/fetch_banlist.status_label`（不在这里另写映射）。

两块：
* **现在的禁限表**（把变更史按时间累计出来）—— 三个分组 + 张数，卡片式列表；
* **最近变更** —— `原状态 → 新状态` + 生效日期（解除限制也显示，信息别吞）。

数据由 `core.ui_state.banlist_page_data()` 备好（**窗口只画、不查库**）。
Qt 纪律同 `tier_page.py`：QSS 按 objectName 限定（**嵌套容器自带样式表时，
全局 `QLabel { background: transparent }` 会失效** —— 芯片黑框那个坑）。
"""
from __future__ import annotations

import os

import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget, QPushButton)

from ui import theme, widgets
from ui.script_runner import ScriptRunner
from ui.i18n import tr

#: 状态顺序 + 语义色（禁止红 / 限制橙 / 准限制黄 —— 与胜负色区分靠文字色，不靠底色）
#: ★ 分组一律走**语义 key**（站内原值 / 'Unlimited'）—— 拿显示标签当 key 在
#: 英文下会整页空白（2026-10-04 实测），显示文案用 `STATUS_KEY_LABEL` + tr。
STATUS_ORDER = ('Forbidden', 'Limited 1', 'Limited 2')
STATUS_COLOR = {'Forbidden': theme.LOSE, 'Limited 1': theme.WARN,
                'Limited 2': theme.WIN, 'Unlimited': theme.WIN}
STATUS_KEY_LABEL = {'Forbidden': '禁止', 'Limited 1': '限制',
                    'Limited 2': '准限制', 'Unlimited': '解除限制'}
_LABEL_TO_KEY = {v: k for k, v in STATUS_KEY_LABEL.items()}


def _row_key(row, field):
    """行的语义状态 key：优先 `<field>_key` 字段；老形状从中文标签反推。"""
    k = row.get(field + '_key')
    if k:
        return k
    return _LABEL_TO_KEY.get(row.get(field) or '', row.get(field) or '')


def _status_lab(key):
    """语义 key → 当前语言的显示文案。"""
    return tr(STATUS_KEY_LABEL.get(key, key))

#: 卡片网格列数
GRID_COLS = 5
CARD_W = 210
#: 卡片高：整卡 28 + 上下留白（2026-09-21 用户：「完整的卡图，包括卡框」）
CARD_H = 36
#: 整卡显示尺寸 —— **完整不裁切**（卡比例 59:86，高 28 → 宽 19）
ART_H = 28
ART_W = 20
#: 最近变更行：整卡高（月份行 + 挤一行的卡阵）
#: 最近变更：整卡 **68x96**（2026-09-21 用户：「卡图放大 100%」）+ 卡下名字
CHG_CARD_H = 122
CHG_ART_H = 96
CHG_ART_W = 68
#: 月份字体（2026-09-21 用户：「日期字体放大 100%」= SMALL 14 -> BODY 15 再两号）
CHG_DATE_F = 18


def _elide_cn(text: str, width: int) -> str:
    """按像素宽省略（卡下的名字可能比卡图宽；tooltip 给全名）。"""
    fm = QFontMetrics(widgets.font(theme.PAGE_F_SMALL))
    return fm.elidedText(str(text or ''), Qt.ElideRight, max(width, 20))


def _scaled_art(path: str, w: int, h: int):
    """完整卡图按比例缩放（**不裁切**：`KeepAspectRatio`）；缺图返回 None。

    ⚠️ `widgets.round_pixmap` 是正方形裁切（卡组图那套），卡绘是 59:86 的长方形
    —— 裁了会把卡面切掉一块（2026-09-21 用户：「不要裁切，用完整卡图」）。
    """
    if not path or not os.path.exists(path):
        return None
    img = QImage(path)
    if img.isNull():
        return None
    return QPixmap.fromImage(img.scaled(w, h, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))


def _full_art_label(path: str, w: int, h: int) -> QLabel:
    """完整卡图标签；缺图给同尺寸空占位（布局不跳）。"""
    lab = QLabel()
    lab.setFixedSize(w, h)
    lab.setStyleSheet('background: %s; border-radius: 2px;' % theme.SURFACE2)
    pix = _scaled_art(path, w, h)
    if pix is not None:
        lab.setPixmap(pix)
    return lab


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— 与 `tier_page.py` 同款（独立文件不互引）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


class BanCard(QWidget):
    """一张卡：**[卡绘] 卡名**（左）+ 状态文字（右，语义色）。纯展示。

    ★ 2026-09-21 用户要求「禁限表需要带卡图」：卡绘走 `core.card_art`（本地缓存
    `data/card_art/`，**没缓存就留空占位** —— 页面另外负责按需下载）。
    """

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(CARD_W, CARD_H)
        self.setObjectName('banCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        # ⚠️ 按 objectName 限定 + 给子 QLabel 显式透明（Qt 就近样式表纪律）
        self.setStyleSheet('QWidget#banCard { background: %s;'
                           ' border: 1px solid %s; border-radius: 4px; }'
                           'QWidget#banCard QLabel { background: transparent;'
                           ' border: none; }'
                           % (theme.SURFACE2, theme.HAIRLINE))
        self.name_en = row.get('name_en') or ''
        name = str(row.get('name') or '')
        self.setToolTip('%s\n%s' % (name, self.name_en))
        hl = QHBoxLayout(self)
        hl.setContentsMargins(5, 0, 8, 0)
        hl.setSpacing(7)
        #: 卡绘：**完整卡图不裁切**（按卡比例缩放；缓存命中直接贴）
        self.art = _full_art_label(row.get('art') or '', ART_W, ART_H)
        hl.addWidget(self.art)
        lab = _lab(name, 'body', size=theme.PAGE_F_BODY)
        lab.setMinimumWidth(0)
        hl.addWidget(lab, 1)
        # ★ BUG-B6-06：`ui_state.banlist_page_data` 给的 `status` 已经是
        #   **当前语言的显示文案**（中文「禁止」/ 英文「Forbidden」），语义 key
        #   在 `status_key`。旧代码拿 `status` 去 `STATUS_COLOR`（键是
        #   `Forbidden/Limited 1/...`）查色 → 中文界面**全部落到兜底灰**，
        #   禁止/限制/准限制三色形同不存在；英文界面 `tr('Forbidden')`
        #   二次翻译查不到 → 显示 `⟦Forbidden⟧`。
        #   现在按语义 key 走：文字用 `_status_lab`、颜色用 `STATUS_COLOR`，
        #   与同文件 `_status_block` / `_summary_text` 一致。
        _skey = _row_key(row, 'status')
        self.status = _lab(_status_lab(_skey), 'body',
                           size=theme.PAGE_F_SMALL)
        self.status.setStyleSheet(
            'color: %s; background: transparent;'
            % STATUS_COLOR.get(_skey, theme.INK_MUTED))
        hl.addWidget(self.status)



    def set_art(self, path: str) -> None:
        """就地换上卡绘（后台下好一张时由页面调；不整页重画）。"""
        pix = _scaled_art(path, ART_W, ART_H)
        if pix is not None:
            self.art.setPixmap(pix)

class BanlistPage(QWidget):
    """「禁限表」页：现在的禁限状态（三分组）+ 最近变更。

    ★ 卡绘（2026-09-21 用户要求「带卡图」）：数据层只给**已缓存**的路径
    （`core.card_art.art_path_by_en`，纯本地不联网）；本页进入后**后台逐张下载**
    缺的那些，下一张补一张（不卡界面、失败静默留占位）。
    """

    #: 后台线程下到一张新图 -> 主线程回填（参数 = 英文名）
    _art_ready = Signal(str)
    #: 「抓取禁限表」跑完且成功（看板接：重取本页数据）
    refreshed = Signal()

    def __init__(self, parent=None, runner_factory=None):
        super().__init__(parent)
        self._data = {}
        self._runner_factory = runner_factory or (
            lambda script, args: ScriptRunner(script, args))
        self._runner = None
        #: 当前网格列数（BUG-B6-08：resizeEvent 重排用）
        self._grid_cols_now = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('禁限表'), 'body', size=theme.PAGE_F_TITLE))
        #: ★ 刷新按钮（2026-10-03 用户：对应界面也要能手动刷新）
        self.btn_refresh = QPushButton(tr('抓取禁限表'))
        self.btn_refresh.setObjectName('modeFilter')
        self.btn_refresh.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.btn_refresh.setFixedHeight(
            self.btn_refresh.fontMetrics().height() + 8)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._run_fetch)
        head.addWidget(self.btn_refresh)
        self.fetch_status = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.fetch_status.setStyleSheet('color: %s; background: transparent;'
                                        % theme.INK_MUTED)
        head.addWidget(self.fetch_status)
        head.addStretch(1)
        self.summary = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.summary.setStyleSheet('color: %s; background: transparent;'
                                   % theme.INK_MUTED)
        head.addWidget(self.summary)
        outer.addLayout(head)

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
        self._art_busy = False
        #: ★ BUG-B6-10：卡绘下载的代次令牌（`set_data` 换代）
        self._art_gen = 0
        self._art_thread = None
        self._art_ready.connect(self._on_art_ready)

    # ------------------------------------------------------------ 抓取（联网）
    def _run_fetch(self) -> None:
        """跑 tools/fetch_banlist.py（联网，十几秒）；跑完重取本页数据。"""
        if self._runner is not None and self._runner.running():
            return
        self.btn_refresh.setEnabled(False)
        self.fetch_status.setText(tr('运行中…'))
        runner = self._runner_factory('tools/fetch_banlist.py', [])
        runner.line.connect(lambda s: self.fetch_status.setText(s[-60:]))
        runner.done.connect(self._on_fetch_done)
        self._runner = runner
        runner.start()

    def _on_fetch_done(self, code) -> None:
        self.btn_refresh.setEnabled(True)
        self.fetch_status.setText(tr('完成') if int(code) == 0
                                  else tr('抓取失败'))
        if int(code) == 0:
            self.refreshed.emit()

    # ------------------------------------------------------------ 卡绘（后台）
    def _warm_art(self) -> None:
        """后台把缺的卡绘逐张下回来（一次一个线程；已在跑就跳过）。"""
        if self._art_busy:
            return
        missing = [r.get('name_en') for r in (self._data.get('current') or [])
                   if r.get('name_en') and not r.get('art')]
        if not missing:
            return
        self._art_busy = True
        #: ★ BUG-B6-10（2026-10-19 审查点名）：`_art_busy` **只置 True 从不复位**
        #:   —— 线程被下面的 `return`（页面销毁 / emit 失败）提前退出时，标志
        #:   永远停在 True，刷新后新卡的卡绘再也下不来（要重启看板）。
        #:   修法 = 整个线程体包 try/finally，**无论怎么退出都复位**；
        #:   外加代次令牌：刷新（`set_data`）换代，旧线程收尾时不抢新线程的状态。
        gen = self._art_gen

        def work(names, my_gen):
            from core import card_art
            try:
                from shiboken6 import isValid
            except Exception:                            # noqa: BLE001
                isValid = lambda _o: True                # noqa: E731

            def alive() -> bool:
                """🔴 2026-10-03 段错误防护：页面销毁后**绝不能再 emit** ——
                往已删除的 QObject 发信号是 access violation（后台线程尤其致命，
                全量测试跑到后面随机崩、栈却指在无关的 Qt 代码上）。"""
                try:
                    return bool(isValid(self))
                except Exception:                        # noqa: BLE001
                    return False

            todo = list(names)
            try:
                for _round in range(3):      # 偶发失败有 3 轮机会（实测有单张第一次 404/超时）
                    still = []
                    for name in todo:
                        if not alive():
                            return
                        try:
                            ok = bool(card_art.fetch_art(name_en=name))
                        except Exception:                # noqa: BLE001
                            ok = False
                        if ok:
                            if not alive():
                                return
                            try:
                                self._art_ready.emit(name)
                            except RuntimeError:         # C++ 对象没了（兜底）
                                return
                        else:
                            still.append(name)
                    todo = still
                    if not todo:
                        break
                    time.sleep(2.0)  # 下一轮前歇口气（别对同一批连续猛拉）
            finally:
                #: 只有当前代次才复位（切数据后旧线程别抢新线程的忙碌位）
                if my_gen == self._art_gen:
                    self._art_busy = False

        #: 留住线程句柄：页面销毁时等它收尾（`shutdown()`）——
        #: 线程还在跑、QApplication 已经销毁的话，最后那次 `emit` 是
        #: access violation（2026-10-03 段错误同款，只是换了个触发点）。
        self._art_thread = threading.Thread(target=work, args=(missing, gen),
                                            daemon=True)
        self._art_thread.start()

    def _on_art_ready(self, name_en: str) -> None:
        """一张图下好了 -> 就地回填（不整页重画，避免打断滚动）。"""
        try:
            from core import card_art
            path = card_art.art_path_by_en(name_en)
        except Exception:                                # noqa: BLE001
            return
        if not path:
            return
        for r in (self._data.get('current') or []):
            if r.get('name_en') == name_en:
                r['art'] = path
        for card in self.findChildren(BanCard):
            if card.name_en == name_en:
                card.set_art(path)

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        self._data = dict(data or {})
        #: ★ BUG-B6-10：刷新 = 换代；旧线程还挂着也不拦新的这批
        self._art_gen += 1
        self._art_busy = False
        self._rebuild()
        self._warm_art()          # 缺的卡绘后台下（不卡界面）

    def shutdown(self) -> None:
        """等后台卡绘线程收尾（页面销毁 / 测试 teardown 前调）。

        没有它的话，线程可能在 `deleteLater` 之后、Qt 已销毁的窗口上 emit
        —— 进程退出时的随机 `Segmentation fault`。
        """
        self._art_gen += 1        # 换代：线程看到代次不符就不会再 emit
        th = getattr(self, '_art_thread', None)
        if th is not None and th.is_alive():
            th.join(timeout=3.0)

    def _clear(self):
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _summary_text(self) -> str:
        d = self._data
        by_key: dict = {}
        for k, n in (d.get('counts') or {}).items():
            kk = _LABEL_TO_KEY.get(k, k)
            by_key[kk] = by_key.get(kk, 0) + int(n or 0)
        parts = ['%s %d' % (_status_lab(k), by_key[k])
                 for k in STATUS_ORDER if by_key.get(k)]
        eff = (d.get('last_effective') or '')[:10]
        txt = tr('共 %d 张受限') % int(d.get('current_total') or 0)
        if parts:
            txt += '（%s）' % ' · '.join(parts)
        if eff:
            txt += tr(' · 最近生效 %s') % eff
        return txt

    def _grid_cols(self) -> int:
        """可视区宽度 → 网格列数（**放得下才给列**，不会撑出横向滚动条）。

        ★ BUG-B6-08：与 `deck_view_page.cols_for_width` 同一口径。
        `n` 列占宽 = ``n*CARD_W + (n-1)*S_SM`` ≤ 视口宽
        → ``n ≤ (w+S_SM)//(CARD_W+S_SM)``。
        """
        try:
            w = int(self.width())
        except Exception:
            w = 0
        if w <= 0:
            w = 1200        # 还没 show（取不到宽度）：按主区最小宽估
        return max(1, (w + theme.S_SM) // (CARD_W + theme.S_SM))

    def resizeEvent(self, event) -> None:                    # noqa: N802
        """窗口变了 → 列数可能变 → 重排整页（数据已在 self._data）。"""
        super().resizeEvent(event)
        cols = self._grid_cols()
        if cols != self._grid_cols_now:
            self._grid_cols_now = cols
            self.set_data(self._data)

    def _status_block(self, status: str, rows) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.S_MD)
        title = _lab('%s · %d %s' % (_status_lab(status), len(rows), tr('张')),
                     'body',
                     size=theme.PAGE_F_OVER_TITLE, weight=QFont.Weight.Bold)
        title.setStyleSheet('color: %s; background: transparent;'
                            % STATUS_COLOR.get(status, theme.INK))
        v.addWidget(title)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.S_SM)
        # ★ BUG-B6-08：列数按**可视宽**算，不再写死 5 列。
        #   5×210 + 4×S_SM > 最小窗口 1200×620 下的视口 → 必出横向滚动条
        #   （用户 2026-09-21 明确要求本页不出横滚）。
        cols = self._grid_cols()
        self._grid_cols_now = cols
        for i, row in enumerate(rows):
            grid.addWidget(BanCard(row), i // cols, i % cols)
        for j in range(cols):
            grid.setColumnStretch(j, 1)
        v.addLayout(grid)
        v.addStretch(1)
        return box

    #: 最近变更的**分组顺序**（2026-10-03 用户改口径：禁止 / 限制 / 准限制 / 解除）
    CHANGE_ORDER = STATUS_ORDER + ('Unlimited',)

    def _chg_card(self, r: dict, name_w: int = 0) -> QWidget:
        """最近变更的一张：**整卡图，名字在卡右侧**（竖排、垂直居中；
        tooltip 全名 —— 2026-09-21 用户：「文字放卡图右边」）。

        ★ `name_w` 给了就**名字列定宽**（2026-10-04 用户：同一行里下一张卡的
        卡图要和别的行对齐 —— 名字列不定宽，第二张起的卡图 x 就随前排名字
        长短漂）。宽度由调用方按本批最长名字实测，不省略不裁字。
        """
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(_full_art_label(r.get('art') or '', CHG_ART_W, CHG_ART_H))
        # role=body（亮白）—— 与下方禁限表卡片同款；label 是灰色（用户不要）
        name = _lab(str(r.get('name') or ''), 'body',
                    size=theme.PAGE_F_SMALL)
        name.setToolTip('%s\n%s' % (r.get('name'), r.get('name_en') or ''))
        if name_w:
            name.setFixedWidth(name_w)
            hl.addWidget(name)
        else:
            hl.addWidget(name, 1)
        return w


    def _recent_block(self, rows) -> QWidget:
        """最近变更：**按「变成什么状态」分组**（组序见 CHANGE_ORDER）、
        同一档位的卡挤一行、只第一行标月份（2026-09-21 用户拍板）。

        ★ 2026-10-04 用户补充：**不同行的卡图要在同一列对齐** ——
        日期列、状态列都定宽（状态列宽 = 四档译文里最宽的那个，一次算好），
        卡图的 x 起点每行相同、名字起点跟着卡图对齐。
        本块整体放在「禁止」分组**上方**（用户：最近变更放在禁止 108 张上方）。
        """
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.S_MD)
        v.addWidget(_lab(tr('最近变更'), 'body', size=theme.PAGE_F_OVER_TITLE,
                         weight=QFont.Weight.Bold))
        # 按目标状态分组（组内按生效时间倒序 —— rows 本来就是倒序的）
        by_status = {s: [] for s in self.CHANGE_ORDER}
        for r in rows:
            by_status.setdefault(_row_key(r, 'to'), []).append(r)
        # 状态列宽 = 四个状态译文里最宽的那个（统一 → 卡图同列对齐）
        fm = QFontMetrics(widgets.font(theme.PAGE_F_SMALL))
        tag_w = max(60, max((fm.horizontalAdvance(_status_lab(s))
                             for s in self.CHANGE_ORDER), default=0) + 12)
        # ★ 名字列宽 = 本批最长名字实测（统一 → 第 2/3/… 张卡图也同列对齐）
        name_w = max((fm.horizontalAdvance(str(r.get('name') or ''))
                      for r in rows), default=0) + 12
        month_shown = False
        for status in self.CHANGE_ORDER:
            group = by_status.get(status) or []
            if not group:
                continue
            line = QWidget()
            hl = QHBoxLayout(line)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(theme.S_MD)
            month = ''
            if not month_shown:
                month = (group[0].get('effective') or '')[:7]
                month_shown = True
            date = _lab(month, 'body', size=CHG_DATE_F)
            date.setFixedWidth(78)
            date.setStyleSheet('color: %s; background: transparent;'
                               % theme.INK_MUTED)
            hl.addWidget(date)
            tag = _lab(_status_lab(status), 'body', size=theme.PAGE_F_SMALL)
            tag.setFixedWidth(tag_w)
            tag.setStyleSheet('color: %s; background: transparent;'
                              % STATUS_COLOR.get(status, theme.INK_MUTED))
            hl.addWidget(tag)
            for r in group:
                hl.addWidget(self._chg_card(r, name_w=name_w))
            hl.addStretch(1)
            v.addWidget(line)
        v.addStretch(1)
        return box

    def _rebuild(self) -> None:
        self._clear()
        d = self._data
        self.summary.setText(self._summary_text())
        cur = list(d.get('current') or [])
        if not cur:
            empty = widgets.label(
                tr('还没有禁限表数据 —— 跑一次 tools/fetch_banlist.py'
                   '（每月手动）就有'), 'label')
            empty.setFont(widgets.font(theme.PAGE_F_BODY))
            empty.setWordWrap(True)
            self.col.addWidget(empty)
            self.col.addStretch(1)
            return
        #: ★ 最近变更在最前（禁止组上方；2026-09-21 用户拍板）——
        #: 只显示最近一个月的变更（太旧的进「禁限变更史」就没有当下意义了）
        recent = [r for r in (d.get('recent') or [])
                  if (r.get('effective') or '')[:7]
                  == (d.get('recent') or [{}])[0].get('effective', '')[:7]]
        if recent:
            self.col.addWidget(self._recent_block(recent))
            self.col.addWidget(widgets.hairline())
        for status in STATUS_ORDER:
            rows = [r for r in cur if _row_key(r, 'status') == status]
            if rows:
                self.col.addWidget(self._status_block(status, rows))
        self.col.addStretch(1)
