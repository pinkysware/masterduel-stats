# -*- coding: utf-8 -*-
"""看板「礼盒」页 —— 读游戏内存里的未领取礼盒条目（2026-09-19 用户定）。

⚠️ 这是 M7「看板进程不读内存」决策的**唯一破例口子**：
点「读取礼盒」才开一次进程、读完就关，**不开定时器、不进页面自动读**
（设计定稿见 ``docs/礼盒看板设计.md``）。调试模式下不碰进程，只给 demo 数据。
读取成功会写 ``data/present_box_cache.json``，下次开看板先显示上次结果。

汇总表口径（2026-09-19 用户反馈「比对话里的 markdown 表还丑」重做）：
**固定列宽对齐**（日期 100 / 条数 90 / 小计 96 右对齐）+ **等宽数字**（tnum）
+ 最早到期**整行** WARN_SOFT 高亮（不是只染文字）+ 底部**合计行与列对齐**。

Qt 纪律同 ``dashboard.py`` 模块头：QSS 不写 ``font-*`` / 文字控件高度 ≥ 字体高 /
**先样式表后 setFont**（样式表会把字体弹回去）/ Qt 名字一律模块级 import。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from core import ui_demo
from core.memread import present_box as pb
from ui import theme, widgets
from ui.i18n import tr

#: 会话内缓存的 PresentBoxViewController TypeInfo 地址（0 = 重新定位）
_VC_TI = 0

STATUS_IDLE = '点「读取礼盒」从游戏内存读一次（约 10-15 秒；游戏需进过礼盒页面）'

#: 汇总表的固定列宽（表头 / 数据行 / 合计行三处共用，错一格就「歪」）
DATE_W = 100                     # 领取期限
COUNT_W = 90                     # 条数（+最早到期胶囊）
QTY_W = 96                       # 数量小计（右对齐）
CARD_MAX_W = 560                 # 汇总卡最宽（用户 2026-09-19：别铺满整栏）
ROW_H_PAD = 7                    # 行内上下留白（文字高 + 2×7 ≈ 舒服的行高）


def _demo_on() -> bool:
    """与 app.demo_from_settings() 同口径（不 import app，避免循环）。"""
    return str(QSettings('masterduel', 'M7').value('ui/demo', '')).lower() \
        in ('true', '1')


def _live_provider() -> Dict[str, object]:
    """真读一次内存并写缓存（后台线程里跑，别在主线程调）。"""
    global _VC_TI
    rows, ti, err = pb.read_present_box_live(_VC_TI)
    _VC_TI = ti
    if not err and rows is not None:
        try:
            pb.save_cache(pb.CACHE_PATH, rows)
        except OSError:
            pass                        # 缓存写失败不挡显示
    return {'rows': rows, 'read_at': time.time(), 'error': err}


def _demo_provider() -> Dict[str, object]:
    """调试模式：demo 数据，绝不碰进程。"""
    return {'rows': list(ui_demo.DEMO_PRESENT_ROWS),
            'read_at': time.time(), 'error': ''}


def _fmt_time(ts: float) -> str:
    return time.strftime('%m-%d %H:%M', time.localtime(ts))


def _time_part(limit: str) -> str:
    parts = (limit or '').split(' ')
    return parts[1] if len(parts) > 1 else ''


def _lab(text: str, size: int = theme.F_BODY, color: Optional[str] = None,
         weight=QFont.Weight.Normal, role: str = 'body',
         align=Qt.AlignVCenter | Qt.AlignLeft, tabular: bool = False) -> QLabel:
    """定高文字标签：颜色走样式表（先）、字体最后设（防被弹回去）。"""
    lab = widgets.label(text, role, align)
    if color:
        lab.setStyleSheet('color: %s;' % color)
    lab.setFont(widgets.font(size, weight, tabular=tabular))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


class _DayRow(QFrame):
    """汇总表的一行：日期 / 条数（+最早到期胶囊）/ 数量小计。

    最早到期那行**整行** WARN_SOFT 高亮（圆角块），不只染文字。
    列宽用固定值（DATE_W / COUNT_W / QTY_W），表头和数据行共用一套。
    """

    def __init__(self, day: dict, first: bool, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(_lab('0').fontMetrics().height() + ROW_H_PAD * 2)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, ROW_H_PAD, theme.S_SM, ROW_H_PAD)
        lay.setSpacing(theme.S_SM)

        self._date = _lab('', theme.F_BODY, tabular=True,
                          weight=QFont.Weight.Medium)
        self._date.setFixedWidth(DATE_W)
        self._n = _lab('', theme.F_BODY, color=theme.INK_MUTED)
        self._n.setFixedWidth(COUNT_W)
        self._chip = widgets.value_chip(tr('最早到期'), fg=theme.WARN,
                                        bg=theme.WARN_SOFT)
        # 当天最早到点时刻（用户 2026-09-19 定：显示在「最早到期」右边）
        self._at = _lab('', theme.F_BODY, color=theme.WARN, tabular=True)
        self._qty = _lab('', theme.F_METRIC_SM, tabular=True,
                         weight=QFont.Weight.Medium,
                         align=Qt.AlignVCenter | Qt.AlignRight)
        self._qty.setFixedWidth(QTY_W)
        lay.addWidget(self._date, 0, Qt.AlignLeft)
        lay.addWidget(self._n, 0, Qt.AlignLeft)
        lay.addWidget(self._chip, 0, Qt.AlignLeft)
        lay.addWidget(self._at, 0, Qt.AlignLeft)
        lay.addStretch(1)
        lay.addWidget(self._qty, 0, Qt.AlignRight)
        self.set_day(day, first)

    def set_day(self, day: dict, first: bool) -> None:
        color = theme.WARN if first else theme.INK
        self._date.setText(day['date'])
        self._n.setText(tr('%d 条') % day['n'])
        self._qty.setText('×%d' % day['qty'])
        self._chip.setVisible(first)
        self._at.setVisible(first)
        self._at.setText(day.get('at') or '')
        # ⚠️ background: transparent 必须带 —— 全局 QSS 给 role 标签配了底色，
        #    高亮行上会显出一块块深色底（自检出图放大抓到）
        self._date.setStyleSheet('color: %s; background: transparent;' % color)
        self._n.setStyleSheet('color: %s; background: transparent;'
                              % (theme.WARN if first else theme.INK_MUTED))
        self._qty.setStyleSheet('color: %s; background: transparent;' % color)
        self._date.setFont(widgets.font(theme.F_BODY, QFont.Weight.Medium,
                                        tabular=True))
        self._qty.setFont(widgets.font(theme.F_METRIC_SM, QFont.Weight.Medium,
                                       tabular=True))
        if first:                       # 整行高亮：soft 底 + 圆角（QFrame）
            self.setStyleSheet('background: %s; border-radius: %dpx;'
                               % (theme.WARN_SOFT, theme.R_XS))
        else:
            self.setStyleSheet('')


class _TotalRow(QWidget):
    """合计行：左「合计 N 条」，右数量与上面的「小计」列对齐。"""

    def __init__(self, total_n: int, total_qty: int, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_lab('0').fontMetrics().height() + ROW_H_PAD * 2)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, ROW_H_PAD, theme.S_SM, ROW_H_PAD)
        lay.setSpacing(theme.S_SM)
        left = _lab(tr('合计 %d 条') % total_n, theme.F_BODY,
                    weight=QFont.Weight.Medium)
        lay.addWidget(left, 0, Qt.AlignLeft)
        lay.addStretch(1)
        qty = _lab('×%d' % total_qty, theme.F_METRIC_SM, tabular=True,
                   weight=QFont.Weight.Medium,
                   align=Qt.AlignVCenter | Qt.AlignRight)
        qty.setFixedWidth(QTY_W)
        lay.addWidget(qty, 0, Qt.AlignRight)


def _summary_head_row() -> QWidget:
    """汇总表头（列宽与数据行一致：日期 / 条数 / 撑开 / 小计右）。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(theme.S_SM, theme.S_XS, theme.S_SM, 0)
    lay.setSpacing(theme.S_SM)
    date = _lab(tr('领取期限'), theme.F_LABEL, color=theme.INK_SUBTLE)
    date.setFixedWidth(DATE_W)
    count = _lab(tr('条数'), theme.F_LABEL, color=theme.INK_SUBTLE)
    count.setFixedWidth(COUNT_W)
    qty = _lab(tr('小计'), theme.F_LABEL, color=theme.INK_SUBTLE,
               align=Qt.AlignVCenter | Qt.AlignRight)
    qty.setFixedWidth(QTY_W)
    lay.addWidget(date, 0, Qt.AlignLeft)
    lay.addWidget(count, 0, Qt.AlignLeft)
    lay.addStretch(1)
    lay.addWidget(qty, 0, Qt.AlignRight)
    return w


class _ItemRow(QWidget):
    """明细的一行：期限时分 · 物品 ×数量 · 备注。"""

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_LG, 2, theme.S_SM, 2)
        lay.setSpacing(theme.S_MD)
        gem = (row.get('category') == pb.CAT_GEM
               and row.get('item_id') == pb.ITEM_GEM)
        what = pb.item_label(row.get('category', 0), row.get('item_id', 0))
        text = '%s　%s ×%d' % (_time_part(row.get('limit_date', '')) or '—',
                               what, row.get('quantity') or 0)
        lay.addWidget(_lab(text, theme.F_BODY_SM,
                           color=theme.INK if gem else theme.INK_MUTED),
                      0, Qt.AlignLeft)
        msg = row.get('message') or ''
        if msg:
            lay.addWidget(_lab(msg, theme.F_BODY_SM, color=theme.INK_SUBTLE),
                          1, Qt.AlignLeft)
        else:
            lay.addStretch(1)


class PresentPage(QWidget):
    """礼盒页：读取按钮 + 按期限日期分组的汇总 + 可展开的逐条明细。"""

    read_done = Signal(object)          # 后台线程 → 主线程的结果投递

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._show_detail = False
        self._rows: List[dict] = []
        self._has_result = False
        self._provider: Optional[Callable[[], dict]] = None
        self.read_done.connect(self._on_done)
        self._build()
        self._load_cache()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)
        lay.addWidget(_lab(tr('礼盒'), theme.F_STAT,
                           weight=QFont.Weight.Medium))
        lay.addWidget(_lab(tr('未领取的礼盒物品 · 按领取期限分组（最早到期在最上）'),
                           theme.F_METRIC_SM, role='muted'))

        # 操作条：读取按钮 + 状态行
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(theme.S_MD)
        self._btn = QPushButton(tr('读取礼盒'))
        self._btn.setFixedHeight(self._btn.fontMetrics().height() + 14)
        self._btn.clicked.connect(self._on_read)
        bl.addWidget(self._btn, 0, Qt.AlignLeft)
        self._status = _lab(tr(STATUS_IDLE), theme.F_BODY_SM,
                            color=theme.INK_MUTED)
        bl.addWidget(self._status, 1, Qt.AlignLeft)
        lay.addWidget(bar)

        # 结果区（滚动）：汇总卡 + 明细开关 + 明细
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, 0, theme.S_SM, 0)
        self._body_lay.setSpacing(theme.S_XS)
        self._summary_card = widgets.panel('dashPresent', pad=theme.S_MD,
                                           radius=theme.R_MD,
                                           bg=theme.SURFACE2)
        # ⚠️ panel() 自带 QVBoxLayout（pad 做过边距）—— 再套一个会被 Qt 拒装，
        #    内容整卡不可见（2026-09-19 自检出图踩过）
        self._sum_lay = self._summary_card.layout()
        self._sum_lay.setSpacing(0)
        # 表格内容就 400px 出头，铺满整栏太空（用户 2026-09-19：缩窄）
        self._summary_card.setMaximumWidth(CARD_MAX_W)
        self._detail_toggle = QPushButton(tr('显示明细'))
        self._detail_toggle.setFixedHeight(
            self._detail_toggle.fontMetrics().height() + 10)
        self._detail_toggle.clicked.connect(self._toggle_detail)
        self._detail_toggle.setVisible(False)
        self._detail_box = QWidget()
        self._detail_lay = QVBoxLayout(self._detail_box)
        self._detail_lay.setContentsMargins(0, theme.S_SM, 0, 0)
        self._detail_lay.setSpacing(2)
        self._detail_box.setVisible(False)
        self._body_lay.addWidget(self._summary_card)
        self._body_lay.addWidget(self._detail_toggle, 0, Qt.AlignLeft)
        self._body_lay.addWidget(self._detail_box)
        self._body_lay.addStretch(1)
        self._scroll.setWidget(body)
        lay.addWidget(self._scroll, 1)

    # ------------------------------------------------------------ 数据进/出
    def set_provider(self, fn: Callable[[], dict]) -> None:
        """注入读取函数（demo 热切换由 dashboard 的工厂在每次读取时决定）。"""
        self._provider = fn

    def _load_cache(self) -> None:
        """先亮出上次的结果（「数据截至 X」）；调试模式没缓存也给 demo。"""
        cached = pb.load_cache(pb.CACHE_PATH)
        if cached:
            self._apply(cached['rows'], cached['read_at'], '', cached=True)
        elif _demo_on():
            self._apply(ui_demo.DEMO_PRESENT_ROWS, time.time(), '', cached=True)
        else:
            self._render()      # 冷启动：也要渲染一次（空态引导文案在 summary 里）

    def _on_read(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._btn.setEnabled(False)
        self._set_status(tr('读取中……（全进程扫一次，约 10-15 秒）'),
                         theme.INK_MUTED)
        provider = self._provider or (_demo_provider if _demo_on()
                                      else _live_provider)
        threading.Thread(target=self._work, args=(provider,),
                         daemon=True).start()

    def _work(self, provider: Callable[[], dict]) -> None:
        try:
            result = provider()
        except Exception as exc:            # 线程里不能把看板带崩
            result = {'rows': None, 'read_at': time.time(),
                      'error': tr('读取出错：%s') % exc}
        self.read_done.emit(result)

    def _on_done(self, result: dict) -> None:
        self._busy = False
        self._btn.setEnabled(True)
        err = result.get('error') or ''
        rows = result.get('rows')
        if err or rows is None:
            self._set_status(err or tr('读取失败'), theme.WARN)
            return
        self._apply(rows, result.get('read_at') or time.time(), err)

    # ------------------------------------------------------------ 渲染
    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet('color: %s;' % color)
        self._status.setFont(widgets.font(theme.F_BODY_SM))

    def _apply(self, rows: List[dict], read_at: float, err: str,
               cached: bool = False) -> None:
        self._rows = rows or []
        self._has_result = True
        head = (tr('数据截至 %s') % _fmt_time(read_at) if cached
                else _fmt_time(read_at))
        self._set_status(head + ('　·　' + err if err else ''),
                         theme.WARN if err else theme.INK_MUTED)
        self._render()

    def _render(self) -> None:
        self._render_summary()
        self._render_detail()
        has = bool(self._rows)
        self._detail_toggle.setVisible(has)
        self._detail_box.setVisible(has and self._show_detail)
        self._detail_toggle.setText(tr('收起明细') if self._show_detail
                                    else tr('显示明细'))

    def _render_summary(self) -> None:
        while self._sum_lay.count():
            item = self._sum_lay.takeAt(0)
            w = item.widget() if item else None
            if w:
                # setParent(None) 立即摘除 —— 只 deleteLater 的话旧控件要等
                # 下个事件循环才消失，重渲染瞬间会叠在原位（自检出图踩过）
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self._has_result:
            # 冷启动：没缓存也没读过 —— 给一句引导，别留一张空白卡
            self._sum_lay.addWidget(_lab(
                tr('还没有数据 —— 点上面的「读取礼盒」，从游戏内存读一次'),
                theme.F_BODY, role='muted'))
            self._sum_lay.addSpacing(ROW_H_PAD)
            return
        if not self._rows:
            self._sum_lay.addWidget(_lab(
                tr('礼盒是空的（或者已经全领了）'),
                theme.F_BODY, role='muted'))
            self._sum_lay.addSpacing(ROW_H_PAD)
            return
        summary = pb.summarize(self._rows)
        self._sum_lay.addWidget(_summary_head_row())
        last = len(summary['days']) - 1
        for i, d in enumerate(summary['days']):
            self._sum_lay.addWidget(_DayRow(d, first=(i == 0)))
            if i < last:                    # 行间发丝；高亮行底下不画，免得脏
                self._sum_lay.addWidget(widgets.hairline())
        self._sum_lay.addSpacing(theme.S_XS)
        self._sum_lay.addWidget(_TotalRow(summary['total_n'],
                                          summary['total_qty']))

    def _render_detail(self) -> None:
        while self._detail_lay.count():
            item = self._detail_lay.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        by_day: Dict[str, List[dict]] = {}
        for r in self._rows:
            by_day.setdefault(pb.limit_date_part(r.get('limit_date', '')),
                              []).append(r)
        for day, items in by_day.items():
            cap = _lab(day, theme.F_LABEL, color=theme.INK_SUBTLE)
            self._detail_lay.addWidget(cap)
            for r in items:
                self._detail_lay.addWidget(_ItemRow(r))

    def _toggle_detail(self) -> None:
        self._show_detail = not self._show_detail
        self._detail_box.setVisible(bool(self._rows) and self._show_detail)
        self._detail_toggle.setText(tr('收起明细') if self._show_detail
                                    else tr('显示明细'))
