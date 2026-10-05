# -*- coding: utf-8 -*-
"""M7 —— 对手卡组选择器（**附着面板**，2026-09-15 改版）。

入口：悬浮窗「最近对局」行的**对手卡组格**（图 + 名字整格一个按键，
:class:`ui.floating.OppDeckCell`）。拍板（2026-09-15 两轮问答 + 改版）：

* 入口 = 对手卡组格整格按键；**已标注行也可点** —— 面板**预填当前卡组**，选新的覆盖；
* ⚠️ **`Qt.Popup` 里打不进中文**（2026-09-16 用户实测「输入框只能输英文」）：
  这是 Qt 的已知问题 —— `Qt::Popup` 窗口里的 `QLineEdit` **切不了输入法**。
  修法（见 :meth:`DeckSelectorPanel._activate_ime`）：窗口与两个输入框都标
  `WA_InputMethodEnabled`，show 时 `activateWindow()`，**并且文本一变再推一次**
  （否则把内容删空之后就又打不进中文）。搜索框与「自定义卡组名」都吃这条。
* ⚠️ **不再开独立窗口**：面板是贴悬浮窗右侧的 `Qt.Popup`（见 :class:`DeckSelectorPanel`），
  点外面 / Esc = 取消，**什么都不写**；
* **没有「标记未知」按钮**（终审已砍，见 :func:`core.ui_actions.mark`）。

绑定口径与 M5 CLI（``tools/mark_opponent.py`` 的 ``bind()``）**完全一致**：

    search_meta_decks -> upsert_opp_deck(站内名, aliases) 拿 opp_decks id
    -> mark(opp_deck_id=oid, name=显示名)

⚠️ ``matches.opp_deck_id`` 外键指向 **opp_decks** 表（不是 meta_decks），
所以选择器选定后不能直接把 ``meta_deck_id`` 写进 matches —— 必须先
:meth:`Storage.upsert_opp_deck` 换 id。这一步封装在
:func:`core.ui_actions.mark_meta_deck`，本模块只负责「选出来」。

显示名口径 = **只显示设置里的语言**（:func:`deck_display`，`ui/lang`）：
cn = 中文名 → 显示名 → 站内名；en = 显示名 → 站内名；jp = 日文名 → 显示名 → 站内名。
别名只参与**搜索**，不显示。
"""
from __future__ import annotations

import datetime
import io
import os
from typing import List, Optional

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import (QColor, QFontMetrics, QPainter, QPainterPath,
                           QPen)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from core import pinyin_match
from core.deck_images import DeckImages
from core.ui_actions import UNKNOWN_DECK_NAME        # 常量唯一来源（S6 审查 §3.5）
from ui import theme, widgets
from ui.i18n import tr

#: 列表一次最多铺多少行（2026-09-15 用户定：最多 5 行）
MAX_ROWS = 5
#: M5 反推候选最多置顶几条（用户：「搜索框最多列 5 个预选卡组」）
#: ⚠️ `deck_guess.suggest()` 返回的是**全量**（实测 ~101 条，含 0 命中的），
#: 必须在这里截断，否则整张清单都会被标成「建议」（踩过）。
MAX_SUGGEST = 5
#: 选择器**最后那一条**（用户 2026-09-15 定）。
#: 点它 = 用一个**专门的卡组条目**记下来（`upsert_opp_deck('未知卡组')` + `mark`），
#: 于是这局**离开待标注队列**、行上显示「未知卡组」—— 不用动 schema。
#: ⚠️ 常量**不在这里定义**（2026-09-17 起）：唯一来源是
#:    `core.ui_actions.UNKNOWN_DECK_NAME`，上面已 import 进来。
#:    回归锁 `tests/test_ui_selector.py::test_unknown_name_matches_ui`。


def _log(msg: str) -> None:
    """逐条记下选择器上的动作 -> ``data/archive/ui_selector.log``（best-effort）。

    这类「点完成没反应」只有**真机的焦点/事件顺序**才复现，无头单测抓不到；
    留一行日志，出问题时直接看时序，不用猜。
    """
    import time
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, 'data', 'archive', 'ui_selector.log')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, 'a', encoding='utf-8', newline='') as f:
            f.write('%s  %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        # 日志失败不该影响界面（默认吞掉），但要能看见它坏：
        if os.environ.get('DUEL_UI_LOG_DEBUG'):
            import traceback
            traceback.print_exc()
#: 「自定义卡组名」那一条的占位文案（用户 2026-09-16 定：放在「未知卡组」**上面**）
CUSTOM_PLACEHOLDER = '自定义卡组名…'         # 输入框的占位（没填名字时显示）


def custom_deck(name: str) -> dict:
    """「自定义卡组名」的伪 meta 行（字段与 meta 清单同形，走同一条 mark 链路）。

    名字就是用户打的字；图由 `DeckImages.resolve(name)` 按名字配
    （命中别名 / 模糊 / 文件名就用它，配不出回落**悠悠**默认图）。
    """
    name = (name or '').strip()
    return {'name': name, 'name_cn': name, 'name_display': name,
            'name_ja': name, 'aliases': [], 'tier': None, 'custom': True}


def unknown_deck() -> dict:
    """「未知卡组」那条的伪 meta 行（字段与 meta 清单同形，走同一条 mark 链路）。"""
    return {'name': UNKNOWN_DECK_NAME, 'name_cn': UNKNOWN_DECK_NAME,
            'name_display': UNKNOWN_DECK_NAME, 'name_ja': UNKNOWN_DECK_NAME,
            'aliases': [], 'tier': None}
#: 面板与悬浮窗之间的缝（贴右侧定位用）
PANEL_GAP = 8

#: 语言 → 名字字段的优先级（**只显示一种语言**；别名不显示，只用于搜索）
NAME_KEYS = {
    'cn': ('name_cn', 'name_display', 'name'),
    'en': ('name_display', 'name'),
    'jp': ('name_ja', 'name_display', 'name'),
}


def deck_display(deck: dict, lang: str = 'cn') -> str:
    """卡组显示名 —— **只显示 `lang` 那一种语言**（用户 2026-09-15 定，D2）。

    ⚠️ 语言只影响**显示**：搜索仍按中/英/日/别名五字段匹配（`Storage.search_meta_decks`）。
    ⚠️ 别名**不显示**（D4 删掉了次名行），只参与搜索。
    """
    keys = NAME_KEYS.get(lang) or NAME_KEYS['cn']
    for key in keys:
        if deck.get(key):
            return str(deck[key])
    return ''


class CustomNameRow(QFrame):
    """选择器里那条「**自定义卡组名**」—— 输入框 + 右侧「确定」（用户 2026-09-16 定稿）。

    * 位置：**「未知卡组」上面**（`_refill` 保证）；右侧「确定」按钮占**「建议」那一列**
      （边距/间距必须与 `DeckRow` 逐字一致，否则不在同一列）；
    * **边打边配图**：`DeckImages.resolve(输入)` —— 命中别名/模糊/文件名就用它的图，
      配不出回落**悠悠**默认图（口径与全项目一致）；
    * **输入框有字 = 选中这一条**（`text_changed` → 面板 `_on_custom_text`）——
      所以三条路都能确认：右边「确定」/ 输入框回车 / 面板顶上的「确定」；
    * 确认后清空输入框。

    ⚠️ 2026-09-16 回退说明：中间试过「显示态 + 编辑按钮」两态（点了才变输入框），
    真机上「完成」按钮与 `QLineEdit` 的 `FocusOut` 时序打架、多次修不干净 ——
    用户拍板**回退成这一版**：输入框常显，右边一个「确定」按钮，别再引入状态机。
    """

    submitted = Signal(str)       # 确认（右侧「确定」/ 回车）
    ime_keep = Signal(str)        # 用户敲字（含删空）—— 面板拿它保输入法
    text_changed = Signal(str)    # 文本变化 —— 面板据此把这条当「选中的那条」
    clicked = Signal()            # 点整行（与 DeckRow 同语义）
    double_clicked = Signal()     # 双击（与 DeckRow 同语义：选中并确认）

    def __init__(self, resolve_img=None, text: str = '', parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.ROW_H)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('CustomNameRow { background: %s; border-radius: %dpx; }'
                           % (theme.SURFACE2, theme.R_SM))
        self._resolve_img = resolve_img

        lay = QHBoxLayout(self)
        # ⚠️ 边距/间距必须与 `DeckRow` **逐字一致** —— 否则右侧按钮不在「建议」那一列
        lay.setContentsMargins(theme.S_SM, 0, theme.S_SM, 0)
        lay.setSpacing(theme.S_SM)
        self.thumb = widgets.thumb_label('', theme.THUMB)
        lay.addWidget(self.thumb)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr(CUSTOM_PLACEHOLDER))
        self.edit.setText(text or '')
        self.edit.textChanged.connect(self._on_text)
        self.edit.returnPressed.connect(self._on_return)
        self.edit.textEdited.connect(self.ime_keep.emit)
        self.edit.textChanged.connect(self.text_changed)
        lay.addWidget(self.edit, 1)

        # 右侧「确定」—— 与 DeckRow 的「建议」标记同一列；**NoFocus** 别抢输入框焦点
        self.btn_confirm = QPushButton(tr('确定'))
        self.btn_confirm.setCursor(Qt.PointingHandCursor)
        self.btn_confirm.setFont(widgets.font(theme.F_LABEL))
        self.btn_confirm.setFixedHeight(self.btn_confirm.fontMetrics().height() + 2)
        self.btn_confirm.setFocusPolicy(Qt.NoFocus)
        self.btn_confirm.setStyleSheet(
            'QPushButton { background: transparent; border: 0; padding: 0 2px;'
            ' color: %s; }'
            'QPushButton:hover { color: %s; }'
            % (theme.INK_SUBTLE, theme.INK))
        self.btn_confirm.clicked.connect(self._on_return)
        lay.addWidget(self.btn_confirm)

        self._on_text(self.edit.text())

    # ------------------------------------------------------------ 接口
    def text(self) -> str:
        return self.edit.text()

    def set_text(self, text: str) -> None:
        self.edit.setText(text or '')

    def set_selected(self, on: bool) -> None:
        """选中态 —— 与 `DeckRow.set_selected` 同一套视觉（面板 `_apply_selection` 调）。"""
        self.setStyleSheet(
            'CustomNameRow { background: %s; border-radius: %dpx; }'
            % (theme.SURFACE3 if on else theme.SURFACE2, theme.R_SM))

    def _on_text(self, text: str) -> None:
        """边打边换图（空 = 默认图）。"""
        name = (text or '').strip()
        path = ''
        if name and self._resolve_img is not None:
            try:
                path = self._resolve_img(name) or ''
            except Exception:
                path = ''
        if not path:
            from core.deck_images import DEFAULT_IMAGE
            path = DEFAULT_IMAGE
        # ⚠️ 用 `round_pixmap` 直接算图，**不要**当场造一个 QLabel 再取它的 pixmap
        #    （那种一次性控件会让 PySide6 在重建列表时踩到已释放的对象 -> 段错误）
        pix = widgets.round_pixmap(path, theme.THUMB, theme.R_XS)
        if pix is not None:
            self.thumb.setPixmap(pix)
        self.edit.setToolTip(tr('填好点右边「确定」或按回车') if name else '')

    def _on_return(self) -> None:
        """确认（右侧「确定」/ 回车）：有名字就交出去，没名字把焦点给输入框。"""
        name = self.edit.text().strip()
        _log('custom 确认(text=%r)' % name)
        if not name:
            self.edit.setFocus()
            return
        self.submitted.emit(name)
        self.edit.clear()               # 确认后清空（下次打开是干净的）

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):                        # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(ev)


class DeckRow(QFrame):
    """选择器里的一行：图 + **一个**名字（当前语言）+ 可选的「建议」小标记。

    独立窗口里没有拖窗冲突，**按下即选中**；双击 = 选中并确认。

    ⚠️ 2026-09-15 用户要求精简（D2/D4）：**没有次名行、没有 tier 胶囊**，
    名字只显示 `lang` 那一种；行高 46 → 34，图 40 → `theme.THUMB`（26，不然塞不进）。
    """

    clicked = Signal(object)              # 带自身
    double_clicked = Signal(object)

    def __init__(self, deck: dict, img: str, lang: str = 'cn',
                 suggested: bool = False, parent=None):
        super().__init__(parent)
        self.deck = deck
        self._selected = False
        self._suggested = bool(suggested)
        self.setFixedHeight(theme.ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, 0, theme.S_SM, 0)
        lay.setSpacing(theme.S_SM)
        lay.addWidget(widgets.thumb_label(img, theme.THUMB))

        disp = deck_display(deck, lang)
        if disp == UNKNOWN_DECK_NAME:
            disp = tr(disp)          # 只翻显示；写库仍是原串
        self.name_label = widgets.label(disp, 'body')
        lay.addWidget(self.name_label, 1)
        if suggested:          # M5 反推候选置顶时的小标记（免得以为是自己搜出来的）
            mark = widgets.label(tr('建议'), 'label',
                                 Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(mark)
        self.set_selected(False)

    def is_suggestion(self) -> bool:
        """这一行是不是 M5 反推候选（面板把它置顶并标「建议」）。"""
        return self._suggested

    def set_selected(self, on: bool) -> None:
        """选中 = SURFACE3 底；没选 = 透明（悬停高亮交给全局 hover）。"""
        self._selected = bool(on)
        self.setStyleSheet(
            'DeckRow { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; }'
            % (theme.SURFACE3 if on else 'transparent',
               theme.HAIRLINE if on else 'transparent', theme.R_SM))

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):                        # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.double_clicked.emit(self)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


class DeckSelectorPanel(QWidget):
    """贴悬浮窗右侧的卡组选择面板（2026-09-15 用户定的形态，替代独立对话框）。

    * **不是** `QDialog`：`Qt.Popup` + 无边框 + 半透明圆角 —— 点面板外面就关
      （= 取消，**不写库**），也不再阻塞事件循环；
    * 第一行 = **搜索框 + 取消 + 确定**（同一行，省垂直空间）；
    * 列表 ≤ `MAX_ROWS`（5）行，M5 反推候选**置顶**且带「建议」标记；
    * 名字只显示 `lang` 那一种语言；
    * 没有对局上下文头、没有页脚清单说明（用户要求删）。

    绑定口径与 M5 CLI 完全一致（与旧的 `DeckSelectorDialog` 同一套）：
    ``confirm`` 出去的是 **meta 行 dict**，写库仍由 :func:`core.ui_actions.mark_meta_deck`
    完成（先 `upsert_opp_deck` 换 id，再 `mark`）。
    """

    confirmed = Signal(object)      # 确认：带选中的 meta 行 dict
    cancelled = Signal()            # 取消 / 点外面 / Esc

    def __init__(self, storage, row: Optional[dict] = None, lang: str = 'cn',
                 suggestions: Optional[List[dict]] = None, parent=None,
                 prefill_name: Optional[str] = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(theme.qss())
        self.setFixedWidth(theme.SEL_W)
        self.storage = storage
        self.row = row or {}
        self.lang = lang or 'cn'
        self.chosen: Optional[dict] = None
        self._done = False                       # 已经 confirmed / cancelled 过
        self._anchor_row = None

        self._decks = list(storage.list_meta_decks(include_hidden=False) or [])
        self._imgs = DeckImages(storage)
        self._prefill_name = (prefill_name or '').strip() or None
        self._sug_names = [d.get('name') for d in (suggestions or [])[:MAX_SUGGEST]
                           if d and d.get('name')]
        self._row_widgets: List[DeckRow] = []
        self._empty_label: Optional[QLabel] = None
        # ★ 「自定义卡组名」那一条**只建一次**（用户 2026-09-16）：
        #   * 重建会丢掉已经打好的名字；
        #   * 更要紧：反复建/删含 `QLineEdit` 的行会踩到 PySide6 的坑
        #     —— 构造 Qt 对象时撞上 GC 就段错误（实机 faulthandler 栈为证，
        #     与 `ui/widgets.py` 那条「禁止函数内 import PySide6」同源）。
        # ⚠️ 它建得比下面的搜索框还早，所以 `_enable_ime()` 要放到 `__init__` **末尾**
        #    （早调会 AttributeError：self.search 还没有）
        self._custom_row: Optional[CustomNameRow] = CustomNameRow(self._resolve_img)
        self._custom_row.submitted.connect(self._confirm_custom)
        self._custom_row.ime_keep.connect(self._keep_ime_alive)
        self._custom_row.text_changed.connect(self._on_custom_text)
        self._custom_row.clicked.connect(self._pick_custom)
        self._custom_row.double_clicked.connect(self._confirm_custom_row)
        self._preselect()
        #: 悬浮窗动了/变了尺寸 → 自己跟着挪（**不靠父窗口记账**）
        if parent is not None:
            parent.installEventFilter(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        root.setSpacing(theme.S_XS)

        # ---- 第一行：搜索框 + 取消 + 确定（D5：同一行，省垂直空间）
        self.search_row = QWidget()
        srow = QHBoxLayout(self.search_row)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(theme.S_XS)
        self.search = QLineEdit()
        #: ★ 拼音提示只在中文界面出现（拼音是**中文独有**功能，2026-09-19 用户拍板：
        #:    切到其他语言关闭 —— 不光搜不到，提示也不给）
        self.search.setPlaceholderText(
            tr('搜索卡组：中 / 英 / 日 / 别名 / 拼音') if self.lang == 'cn'
            else tr('搜索卡组：中 / 英 / 日 / 别名'))
        self.search.textChanged.connect(self._refill)
        self.search.textEdited.connect(self._keep_ime_alive)   # 保输入法活着
        srow.addWidget(self.search, 1)
        self.btn_cancel = QPushButton(tr('取消'))
        self.btn_cancel.clicked.connect(self._on_cancel)
        srow.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton(tr('确定'))
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._on_ok)
        srow.addWidget(self.btn_ok)
        root.addWidget(self.search_row)

        # ---- 提示行：**只在已标注时**显示（未标注不占地方）
        self.hint = widgets.label('', 'label')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        host = QWidget()
        self.rows_lay = QVBoxLayout(host)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(theme.S_XS)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)

        self._refill('')
        self._enable_ime()          # 控件都建齐了再标（构造时就要有，别等 show）

    def _resolve_img(self, name: str) -> str:
        """按名字配图（命中别名/模糊/文件名就用它，配不出回落悠悠）。"""
        try:
            return self._imgs.resolve(name)
        except Exception:
            return ''

    def custom_row(self):
        """当前那条「自定义卡组名」行（测试用）。"""
        return self._custom_row

    def _on_custom_text(self, text: str) -> None:
        """自定义输入框里**有字 = 选中了「自定义卡组名」这一条**（用户 2026-09-16）。

        ⚠️ 它**不是 `DeckRow`**，所以不参与 `_apply_selection` 的高亮；而顶上那个
        「确定」只看 `chosen` —— 结果用户打完字按「确定」没反应（实测反馈）。
        这里让文本本身成为选择：回车、点「确定」、双击都能走通。
        清空输入 = 撒掉这条选择（避免「看着没选却写了库」）。
        """
        has = bool((text or '').strip())
        if has:
            self.chosen = custom_deck(text)
        elif isinstance(self.chosen, dict) and self.chosen.get('custom'):
            self.chosen = None
        self._apply_selection()

    def _confirm_custom(self, name: str) -> None:
        """回车：用这个名字建立并标注（伪 meta 行走同一条 mark 链路）。"""
        name = (name or '').strip()
        _log('panel._confirm_custom(%r)' % name)
        if not name:
            return
        self.chosen = custom_deck(name)
        self._apply_selection()
        self._on_ok()
        self._custom_row.edit.clear()      # 确认后清空，下次打开是干净的

    # ------------------------------------------------------------- 自绘
    def paintEvent(self, ev):                                   # noqa: N802
        """面板底：圆角 + 发丝描边（它是个半透明顶层窗，得自己画）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    # ------------------------------------------------------------- 定位
    def eventFilter(self, obj, ev):                             # noqa: N802
        """父窗口移动/缩放时重新定位。

        `Qt.Popup` 不会自动跟随父窗口，所以这里挂事件过滤器自己跟
        —— 比「父窗口里存一份 `_panel` 再回调」干净（谁打开谁负责）。
        """
        if obj is self.parentWidget() and ev.type() in (QEvent.Move,
                                                       QEvent.Resize):
            if self.isVisible():
                self._reposition()
        return super().eventFilter(obj, ev)

    def show_near(self, anchor_row) -> None:
        """贴到悬浮窗**右侧**、与 `anchor_row` 顶对齐；右边放不下就翻左侧。"""
        self._anchor_row = anchor_row
        self.show()
        self._reposition()

    def showEvent(self, ev):                                    # noqa: N802
        """弹出时把焦点给搜索框 + **激活输入法** —— 打开就能直接打字（含中文）。

        ⚠️ `Qt.Popup` 不会自动把焦点交给里面的 `QLineEdit`（实测 `focusWidget()`
        是 None、`search.hasFocus()` 是 False）—— 不显式要一次，用户得先点一下才能输入。
        """
        super().showEvent(ev)
        self._activate_ime()
        self.search.setFocus()

    # ------------------------------------------------------------- 输入法
    def _enable_ime(self) -> None:
        """面板与两个输入框都标 `WA_InputMethodEnabled`（构造时就要标，别等 show）。"""
        for w in (self, self.search, getattr(self._custom_row, 'edit', None)):
            if w is not None:
                w.setAttribute(Qt.WA_InputMethodEnabled, True)

    def _activate_ime(self) -> None:
        """让**输入法**在这个 `Qt.Popup` 里可用（否则只能敲英文）。

        Qt 已知问题：`Qt::Popup` 窗口内的 `QLineEdit` **切不了输入法**（中文打不进去）。
        实测有效的三步：

        1. 窗口与每个输入框都标 `WA_InputMethodEnabled`；
        2. `activateWindow()` —— 弹出来后要显式激活一次；
        3. **文本一变再推一次**（见 :meth:`_keep_ime_alive`）—— 否则把内容删空之后
           就又打不进中文了（社区实测的坑）。
        """
        self._enable_ime()
        if self.isVisible():
            self.activateWindow()

    def _keep_ime_alive(self, text: str) -> None:
        """用户敲字后轻推一下窗口 —— `Qt.Popup` 的输入法会断（见 `_activate_ime`）。

        ⚠️ 只在**文本变空**时推：每次都 activateWindow() 会让焦点抖
        （`textEdited` 只由用户输入触发，程序 `setText` 不会进来）。
        """
        if not text and self.isVisible():
            self.activateWindow()

    def _reposition(self) -> None:
        """算面板位置 —— ★ 贴着**被点的那一格**的右侧（打开时、锚点/窗口动了都要调）。

        ★ 用户 2026-09-18 五轮：「点击卡组名后的卡组选择器直接出现在**点击卡组的右侧**，
        **而不是整个 UI 外**」。原实现取的是 `parentWidget()`（窗口）的右缘 ——
        看板比悬浮窗宽得多，面板会整块跑到窗口外面去。现在取 anchor 的**全局矩形**：

            x = anchor 右缘 + PANEL_GAP      y = anchor 顶

        右边放不下 → 翻到左侧；再不够才夹进屏幕。
        ⚠️ 悬浮窗那条路传进来的 anchor 是**占满窗口宽**的行控件 → 与旧行为等价（贴窗口右侧）。
        """
        win = self.parentWidget()
        row = self._anchor_row
        ref = row if row is not None else win
        if ref is None:
            return
        screen = ref.screen().availableGeometry()
        if row is not None:
            top_left = row.mapToGlobal(row.rect().topLeft())
            left, top, width = int(top_left.x()), int(top_left.y()), row.width()
        else:
            left, top, width = win.x(), win.y(), win.width()
        x = left + width + PANEL_GAP
        if x + self.width() > screen.right():        # 右边放不下 → 翻到左侧
            x = left - self.width() - PANEL_GAP
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(top, screen.bottom() - self.height()))
        self.move(int(x), int(y))

    # ------------------------------------------------------------- 预填
    def _pre_deck(self) -> Optional[dict]:
        """按 ``row['opp_deck_id']``（opp_decks id）定位 meta 清单条目。"""
        oid = self.row.get('opp_deck_id')
        if not oid:
            return None
        try:
            opp_decks = self.storage.list_opp_decks()
        except Exception:
            return None
        name = next((n for c_oid, n, _a, _c in opp_decks if c_oid == oid), None)
        if not name:
            return None
        return self._meta_by_name(name)

    def _preselect(self) -> None:
        """已标注行打开时预填当前卡组（选中但不写库，用户可换）。"""
        self.chosen = self._pre_deck()

    def _meta_by_name(self, name: str) -> Optional[dict]:
        return next((d for d in self._decks if d.get('name') == name), None)

    def _current_text(self) -> str:
        """「当前标注」提示 —— 只有已标注时才显示（省空间）。"""
        pre = self.chosen if self.chosen is not None else self._pre_deck()
        if pre is not None:
            return (tr('当前已标注：%s —— 选别的会覆盖，点外面则不改')
                    % deck_display(pre, self.lang))
        return ''

    # ------------------------------------------------------------- 列表
    def _img_of(self, deck: dict) -> str:
        try:
            return self._imgs.resolve(deck.get('name'))
        except Exception:
            return ''

    def _ordered(self, decks, inject=True) -> List[dict]:
        """候选置顶（**按候选顺序 = 分数序**）→ 其余保持原顺序；总长截到 `MAX_ROWS`。

        * 无搜索词（`inject=True`）：候选不在清单里也要按名字补回来（用户 2026-09-15 定）。
        * **有搜索词（`inject=False`）：不补回** —— 补回会把 4 个格子占满，把真正的
          匹配挤掉（2026-09-16 实机：搜「小丑」显示的全是候选，一条匹配都看不见）。
          候选若**本身也匹配**，仍在匹配集里、照旧置顶并带「建议」标记。
        ⚠️ 顺序必须跟 `suggest()` 一致（第一名就是 M5 会替你确认的那个）。
        """
        by_name = {d.get('name'): d for d in decks}
        sug = []
        if self._prefill_name:                     # 09-28：该局现值置顶（修正入口）
            d = (by_name.get(self._prefill_name)
                 or (self._meta_by_name(self._prefill_name) if inject else None))
            if d is not None:
                sug.append(d)
        for name in self._sug_names:
            d = by_name.get(name) or (self._meta_by_name(name) if inject else None)
            if d is not None:
                sug.append(d)
        rest = [d for d in decks
                if d.get('name') not in self._sug_names
                and d.get('name') != self._prefill_name]
        return (sug + rest)[:MAX_ROWS]

    def _first_match_today(self) -> bool:
        """今天还没标过任何一局 → True（= 今天第一次打，那就拿榜单前几当「常选」）。"""
        try:
            today = datetime.date.today().isoformat()
            for m in (self.storage.get_recent_matches(limit=30) or []):
                if (str(m.get('started_at') or '').startswith(today)
                        and m.get('opp_deck_id')):
                    return False
        except Exception:
            pass
        return True

    def _common_decks(self, n: int, exclude=()) -> List[dict]:
        """中间那几条：**我标过的对手卡组**（按次数降序）；今天第一次打 → Tier 榜前三。

        `exclude` = 已经排在它前面的名字（AI 建议那条）—— **必须跳过**，否则会重复。
        """
        if self._first_match_today():
            pool = list(self._decks)              # meta 清单本来就是 tier / power 序
        else:
            pool = []
            try:
                pairs = [(int(t[3] or 0), t[1])
                         for t in (self.storage.list_opp_decks() or [])
                         if len(t) > 3 and t[1] and t[1] != UNKNOWN_DECK_NAME]
                pairs.sort(key=lambda p: -p[0])
                pool = [d for d in (self._meta_by_name(nm)
                                    for _c, nm in pairs) if d is not None]
            except Exception:
                pool = list(self._decks)
        out: List[dict] = []
        for d in pool:
            if len(out) >= n:
                break
            name = d.get('name')
            if name in exclude or any(name == o.get('name') for o in out):
                continue
            out.append(d)
        return out

    def _default_rows(self) -> List[dict]:
        """非搜索态的五条：**AI 建议(1) + 常选(3) + 未知卡组(1)**（用户 2026-09-15 定）。

        * 第 1 条 = M5 反推 **top1**（没有候选就省掉，后面几条补上来）；
        * 中间 3 条 = 我标过的对手卡组（按次数）；今天第一次打 → Tier 榜前三；
        * 最后 = 「未知卡组」（:data:`UNKNOWN_DECK_NAME`）。
        """
        out: List[dict] = []
        top = self._sug_names[0] if self._sug_names else None
        d = self._meta_by_name(top) if top else None
        if d is not None:
            out.append(d)
        out.extend(self._common_decks(3, exclude={d.get('name')} if d else set()))
        out.append(unknown_deck())
        rows: List[dict] = []
        seen = set()
        for d in out:                      # 去重：候选可能也在「常选」里
            name = d.get('name')
            if name and name not in seen:
                seen.add(name)
                rows.append(d)
        return rows[:MAX_ROWS]

    def _fit_width(self, decks) -> None:
        """面板宽**跟着内容走**（用户 2026-09-15：「搜索框太宽，和卡组名贴着就行」）。

        取「最宽的卡组名 + 图 + 「建议」标记 + 内边距」，夹在 [SEL_W_MIN, SEL_W]。
        """
        fm = QFontMetrics(widgets.font(theme.F_BODY))
        widest = max([fm.horizontalAdvance(deck_display(d, self.lang))
                      for d in decks] or [0])
        mark = QFontMetrics(widgets.font(theme.F_LABEL)).horizontalAdvance(tr('建议'))
        need = (theme.S_MD * 2 + theme.THUMB + theme.S_SM + widest
                + theme.S_SM + mark + theme.S_SM)
        self.setFixedWidth(max(theme.SEL_W_MIN, min(theme.SEL_W, int(need))))

    def _refill(self, query: str) -> None:
        """铺列表。

        * 空词 = **AI 建议 + 常选 + 未知卡组**（见 `_default_rows`）；
        * 有词 = 搜索结果（**候选不补回**，见 `_ordered`）+ 末尾固定一条「未知卡组」。
        """
        query = (query or '').strip()
        if query:
            decks = list(self.storage.search_meta_decks(query, limit=60) or [])
            #: ★ 拼音/首字母（**中文独有**，lang=cn 才启用；2026-09-19 用户需求）：
            #:    闪刀姬敲 ``sdj``（首字母）或 ``shandaoji``（全拼）都能查到。
            #:    口径 = 拼音键（只取汉字）的子串；普通命中在前、拼音命中殿后（按
            #:    站内名去重）。候选用 ``self._decks``（构造时已取过，别再查一遍库）。
            if (self.lang == 'cn' and pinyin_match.available()
                    and pinyin_match.is_pinyin_query(query)):
                #: ★ 拼音命中排**前面**（用户 2026-09-19 实测：`ly` 搜不到烙印）——
                #:    纯字母查询里直配的**模糊**命中可以很多（`ly` 撞上 Purrely /
                #:    Fairy Tail… 十来个），拼音命中追加在后面会被 5 行的显示上限
                #:    挤没。cn 界面敲纯字母基本就是拼音意图，让它优先。
                py_hits, seen = [], {d.get('name') for d in decks}
                for d in self._decks:
                    if d.get('name') in seen:
                        continue
                    if pinyin_match.match(query, d.get('name_cn'),
                                          d.get('name_display'),
                                          *(d.get('aliases') or [])):
                        py_hits.append(d)
                        seen.add(d.get('name'))
                decks = py_hits + decks
            rows = self._ordered(decks, inject=False)[:MAX_ROWS - 1] + [unknown_deck()]
        else:
            decks = self._decks
            rows = self._default_rows()
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            w = item.widget()
            # ⚠️ 自定义行**不删**（它由面板持有，下面对「未知卡组」时会重新 add）
            if w is not None and w is not self._custom_row:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._row_widgets = []
        self._empty_label = None
        if query and not decks:
            self._empty_label = widgets.label(
                tr('没有匹配「%s」的卡组 —— 点外面关闭，标注不变') % query,
                'muted', Qt.AlignCenter)
            self._empty_label.setFixedHeight(theme.ROW_H)
            self.rows_lay.addWidget(self._empty_label)
        for d in rows:
            # ★ 用户 2026-09-16 定：**「未知卡组」上面**那条 = 自定义卡组名（行内输入）
            if d.get('name') == UNKNOWN_DECK_NAME:
                self.rows_lay.addWidget(self._custom_row)      # 复用同一个
            w = DeckRow(d, self._img_of(d), lang=self.lang,
                        suggested=d.get('name') in self._sug_names)
            w.clicked.connect(self._pick)
            w.double_clicked.connect(self._confirm)
            self.rows_lay.addWidget(w)
            self._row_widgets.append(w)
        self.rows_lay.addStretch(1)
        n = max(len(rows) + (1 if self._custom_row is not None else 0)
                + (1 if self._empty_label is not None else 0), 1)
        self._scroll.setFixedHeight(n * theme.ROW_H + theme.S_XS * 2)
        self._fit_width(rows)
        self.hint.setText(self._current_text())
        self.hint.setVisible(bool(self.hint.text()))
        self._apply_selection()
        self.adjustSize()

    def deck_rows(self) -> List[DeckRow]:
        """当前列表里的行（测试用）。"""
        return list(self._row_widgets)

    def empty_text(self) -> str:
        return self._empty_label.text() if self._empty_label is not None else ''

    def _apply_selection(self) -> None:
        for w in self._row_widgets:
            w.set_selected(w.deck is self.chosen)
        # 自定义那条不是 DeckRow —— 单独给它上选中态（用户 2026-09-16）
        if self._custom_row is not None:
            self._custom_row.set_selected(
                bool(isinstance(self.chosen, dict) and self.chosen.get('custom')))
        self.btn_ok.setEnabled(self.chosen is not None)

    def _pick_custom(self) -> None:
        """点自定义那条（显示态）= 选中它 —— 与别的卡组行同一语义。

        还没起名字（灰字占位）时无可选，什么都不做。
        """
        name = (self._custom_row.text() or '').strip() if self._custom_row else ''
        if not name:
            return
        self.chosen = custom_deck(name)
        self._apply_selection()

    def _confirm_custom_row(self) -> None:
        """双击自定义那条 = 选中并确认（与 DeckRow 的双击同语义）。"""
        self._pick_custom()
        if self.chosen is not None:
            self._on_ok()

    # ------------------------------------------------------------- 选择/关闭
    def _pick(self, row: DeckRow) -> None:
        self.chosen = row.deck
        self._apply_selection()

    def _confirm(self, row: DeckRow) -> None:
        self._pick(row)
        self._on_ok()

    def _on_ok(self) -> None:
        if self.chosen is None:
            return
        self._done = True
        self.confirmed.emit(self.chosen)
        self.close()

    def _on_cancel(self) -> None:
        self._done = True
        self.cancelled.emit()
        self.close()

    def keyPressEvent(self, ev):                                # noqa: N802
        if ev.key() == Qt.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):                                   # noqa: N802
        """点面板外面（`Qt.Popup`）直接关 —— 也要走「取消」的语义。"""
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(ev)
