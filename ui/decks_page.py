# -*- coding: utf-8 -*-
"""看板「卡组」页（D3 §2）—— **三个页签**：卡组名管理 / Tier 表 / 卡组构成。

用户口径：
* 2026-09-21 第一次：「卡组页不需要区分对手卡组和我的卡组，应该分**卡组名管理**
  和**卡组构成**两个页签。」
* 2026-09-21 第二次：「把查询下的 Tier 表做到卡组的**卡组构成页签左边**，
  移除查询下的 tier 表」→ 页签变成三个，`ui/tier_page.py` 从「查询」搬到这里。

* **页签 1「卡组名管理」** = `ui/opp_deck_manager.py` 的 `OppDeckManager`（内嵌），
  页头 [获取新卡组名]（Q17）—— 跑脚本的进度窗仍是 `FetchDecksDialog`；
* **页签 2「Tier 表」** = `ui/tier_page.py` 的 `TierPage` **原样内嵌**（从「查询」搬来）；
* **页签 3「卡组构成」** = `ui/deck_view_page.py` 的 `DeckViewPage` **原样内嵌**
  （「换卡组」下拉就在页内，容器不用另加导航）。

⚠️ 我方卡组聚合列表（原来那版）**已删**（D3 §2.2）：同一批数字「战绩」页已经按
我方卡组分组显示（组头带场次/胜负/先后手），这里再放一遍是重复。
数据层 `ui_state.decks_page_data()` 一并删；`merged_deck_stats()` **保留**（战绩页组头在用）。

⚠️ 页签样式取 D2 的「实体按钮 + `QStackedWidget`」（不用 `QTabWidget`）——
保持整页风格统一。
"""
from __future__ import annotations

from ui.i18n import tr

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from ui import theme, widgets
from ui.deck_view_page import DeckViewPage
from ui.floating import WindowButton
from ui.opp_deck_manager import OppDeckManager
from ui.tier_page import TierPage

#: 三个页签：(key, 显示名)。⚠️ **Tier 表在「卡组构成」左边**（用户 2026-09-21 口径：
#: 「把查询下的 Tier 表做到卡组的卡组构成页签左边，移除查询下的 tier 表」）——
#: 看 Tier 榜单和看某副牌的构成本来是同一件事的两个方向，放一起才顺手。
TABS = (('names', '卡组名管理'), ('tier', 'Tier 表'),
        ('composition', '卡组构成'))
#: 默认页签
DEFAULT_TAB = 'names'


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— 与 `tier_page.py` 同款（独立文件不互引）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


class FetchDecksDialog(QDialog):
    """Q17「获取新卡组名」的进度窗（**给 `ui/app.py` 的抓取链路用**）。

    `ui/app.py:fetch_new_decks(dlg)` 认两个属性：``btn_fetch``（跑的时候要禁用）
    与 ``fetch_status``（状态文字）；这里再补一个多行 ``log`` 把脚本输出逐行留下
    （原来只留最后一行，失败时无从查起）。

    ⚠️ **无边框窗必须自己画底**（用户 2026-09-21 实测：「框是透明的，可读性太差」）：
    `WA_TranslucentBackground` 只是让圆角外那块透明，**背景本身得在 `paintEvent`
    里填** —— 少了它整块都是透的，字直接叠在后面的窗口上。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('获取新卡组名'))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(theme.qss())
        self.setMinimumWidth(520)
        self._drag_from = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_SM)
        head = QHBoxLayout()
        head.addWidget(_lab(tr('获取新卡组名'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        #: 自绘的「×」（Windows 原生标题栏是白的，和暗色主题不搭）
        self.btn_close = WindowButton('close')
        self.btn_close.setToolTip(tr('关闭'))
        self.btn_close.clicked.connect(self.reject)
        head.addWidget(self.btn_close)
        lay.addLayout(head)
        desc = widgets.label(tr('抓一次 masterduelmeta 榜单（联网，1~3 分钟），'
                                '再把「不在覆盖表里」的卡组列出来让你勾选填中文名。'),
                             'muted')
        desc.setWordWrap(True)
        lay.addWidget(desc)
        self.fetch_status = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.fetch_status.setStyleSheet('color: %s; background: transparent;'
                                        % theme.INK_MUTED)
        lay.addWidget(self.fetch_status)
        self.log = QPlainTextEdit()
        self.log.setObjectName('scriptLog')
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        self.log.setFont(widgets.font(theme.F_BODY_SM))
        lay.addWidget(self.log)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_fetch = QPushButton(tr('抓取榜单'))
        self.btn_fetch.setDefault(True)
        self.btn_fetch.setFont(widgets.font(theme.F_BODY))
        self.btn_fetch.setFixedHeight(self.btn_fetch.fontMetrics().height() + 16)
        #: ⚠️ 真正的动作由 app 侧接（这里只保证按钮存在、能被禁用）
        foot.addWidget(self.btn_fetch)
        btn_close = QPushButton(tr('关闭'))
        btn_close.clicked.connect(self.reject)
        foot.addWidget(btn_close)
        lay.addLayout(foot)

    # ------------------------------------------------------- 自绘外观/拖动
    def paintEvent(self, ev):                                   # noqa: N802
        """圆角暗色底 + 发丝描边（**无边框窗不画就是透明的**）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    def mousePressEvent(self, ev):                              # noqa: N802
        """按在非控件区域可以**拖走**（无边框窗的标配）。"""
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

    def append(self, text: str) -> None:
        """多行日志（app 侧每读一行就调它）。"""
        self.log.appendPlainText(str(text))


class DecksPage(QWidget):
    """「卡组」页：**卡组名管理** / **卡组构成** 两个页签。"""

    #: 点了「获取新卡组名」（Q17）—— app 侧接
    fetch_requested = Signal()
    #: 点了「导入 YDK」（卡组指纹）—— app 侧接（弹文件框 + 存指纹）
    import_ydk_requested = Signal()
    #: 构成页签里换了卡组（参数 = 站内英文名）—— 窗口据此重新取数
    deck_selected = Signal(str)
    #: 点了「← 回 Tier 表」
    back_requested = Signal()
    #: 「卡组名管理」存盘（覆盖表变了）—— 窗口据此刷新
    saved = Signal()

    def __init__(self, parent=None, csv_path=None, runner_factory=None,
                 image_resolver=None, storage=None):
        super().__init__(parent)
        self._data = {}
        #: 传给「卡组名管理」—— 它换图时要写库（`set_meta_deck_override`）
        self._storage = storage
        self._tab = DEFAULT_TAB
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('卡组'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        self.btn_fetch = QPushButton(tr('获取新卡组名'))
        self.btn_fetch.setFont(widgets.font(theme.F_BODY))
        self.btn_fetch.setFixedHeight(self.btn_fetch.fontMetrics().height() + 14)
        self.btn_fetch.setMinimumWidth(130)
        self.btn_fetch.clicked.connect(lambda _c=False: self.fetch_requested.emit())
        head.addWidget(self.btn_fetch)
        #: ★ 2026-10-28：导入 YDK 建「卡组指纹」——用于识别我方自组卡组
        #: （参照库里没有的冷门卡组）。文件可由 get-deck.com 从卡组图导出。
        self.btn_import_ydk = QPushButton(tr('导入 YDK'))
        self.btn_import_ydk.setFont(widgets.font(theme.F_BODY))
        self.btn_import_ydk.setFixedHeight(
            self.btn_import_ydk.fontMetrics().height() + 14)
        self.btn_import_ydk.setMinimumWidth(110)
        self.btn_import_ydk.setToolTip(tr(
            '导入 .ydk 文件，让软件认出你的自组卡组（参照库里没有的）'))
        self.btn_import_ydk.clicked.connect(
            lambda _c=False: self.import_ydk_requested.emit())
        head.addWidget(self.btn_import_ydk)
        self._tabs_group = QButtonGroup(self)
        self._tabs_group.setExclusive(True)
        self.tab_buttons = {}
        for key, name in TABS:
            btn = QPushButton(tr(name))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            #: 实体按钮样式（同 D2 对局分析页）；选中态用 ACCENT 描边
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
            btn.clicked.connect(lambda _c=False, k=key: self.set_tab(k))
            self._tabs_group.addButton(btn)
            self.tab_buttons[key] = btn
            head.addWidget(btn)
        outer.addLayout(head)

        self.stack = QStackedWidget()
        self.names_page = OppDeckManager(csv_path=csv_path,
                                         runner_factory=runner_factory,
                                         image_resolver=image_resolver,
                                         storage=self._storage)
        self.names_page.saved.connect(self._on_names_saved)
        #: ★ 2026-10-28：「自定义卡组名」行右侧的 [导入 YDK] —— 逐层转给宿主
        self.names_page.import_ydk_requested.connect(
            self.import_ydk_requested.emit)
        #: 页签 2 = Tier 表（从「查询」搬进来的，`ui/tier_page.py` 原样内嵌）——
        #: 点芯片 → 直接切到本页的「卡组构成」页签（不再是跨页跳）
        self.tier_page = TierPage()
        self.tier_page.deck_clicked.connect(self.deck_selected.emit)
        self.view_page = DeckViewPage()
        self.view_page.deck_selected.connect(self.deck_selected.emit)
        self.view_page.back_requested.connect(self._on_back)
        self.stack.addWidget(self.names_page)
        self.stack.addWidget(self.tier_page)
        self.stack.addWidget(self.view_page)
        outer.addWidget(self.stack, 1)
        self.set_tab(DEFAULT_TAB)

    # ------------------------------------------------------------ 页签
    def set_tab(self, key: str) -> bool:
        """切页签（认不出的键一律落回默认页签）。

        返回**是否真的切了** —— 窗口据此决定要不要取数（`ui/dashboard.py:open_deck_view`）。
        """
        want = key if key in dict(TABS) else DEFAULT_TAB
        changed = want != self._tab
        self._tab = want
        for k, btn in self.tab_buttons.items():
            btn.setChecked(k == want)
        self.stack.setCurrentWidget(
            {'names': self.names_page, 'tier': self.tier_page}.get(
                want, self.view_page))
        return changed

    def current_tab(self) -> str:
        return self._tab

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        """「卡组构成」页签的数据（窗口只画、不查库）。"""
        self._data = dict(data or {})
        self.view_page.set_data(self._data)

    def set_tier_data(self, data: dict) -> None:
        """「Tier 表」页签的数据（窗口只画、不查库）。"""
        self.tier_page.set_data(data or {})

    def current_deck(self) -> str:
        return self.view_page.current_deck()

    def _on_back(self) -> None:
        """「← 回 Tier 表」—— **现在是同页切页签**（Tier 表就在左边那格）。"""
        self.set_tab('tier')
        self.back_requested.emit()

    def _on_names_saved(self) -> None:
        """卡组名存盘 → 清单变了，让窗口刷新构成页的下拉 / 显示名。"""
        self.saved.emit()

    def shutdown(self) -> None:
        """宿主销毁前的收口（BUG-B7-07）：把「卡组名管理」里在跑的
        `fetch_meta_decks --cn-only` 掐掉，别留孤儿 QProcess。"""
        stop_fn = getattr(self.names_page, 'shutdown', None)
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:                                # noqa: BLE001
                pass
