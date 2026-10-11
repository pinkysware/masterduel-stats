# -*- coding: utf-8 -*-
"""「卡组名管理」（D3 §2）—— 直接改**覆盖表 CSV**，**内嵌**在「卡组」页的页签里。

用户口径（2026-09-21）：「卡组页不需要区分对手卡组和我的卡组，应该分**卡组名管理**
和**卡组构成**两个页签。」→ 原来那个 Q14 弹窗（`OppDeckDialog`）拆成
**可内嵌的 `OppDeckManager(QWidget)`**，弹窗本身**删掉**。

口径没变的部分（照原 Q14 文档）：

* 改的是覆盖表 `data/deck_name_cn.csv`（**唯一真源**），保存后自动跑一次
  `tools/fetch_meta_decks.py --cn-only` 把表贴进库（**不联网**，只把 CSV 写进
  `meta_decks`）—— 所以在这里改完，选择器 / 看板立刻生效。
* 能改四样：**中文名 / 别名 / 日文名 / 隐藏**；「站内英文名」是**连接键**只读。
*（2026-10-12 用户：编辑位原是「显示英文名」，完全用不上 —— 换成日文名。
*  数据列「显示英文名」**保留**（参照库改名机制还靠它，Yummy Engine 一行在用），
*  只是页面不再露出；要改走 Excel。）
* ⚠️ 清单来自 CSV 本身（不查库）：库是 CSV 的投影，改库没意义（下次 `--cn-only` 就冲掉了）。

★ 2026-09-21 二轮（用户实测五条，这里是其中三条）：
1. **补表头**（原来没有，一排输入框不知道哪个是哪个列）；
2. **输入框文字不再被裁**（原来行高 30、编辑框高 22，而 QSS 是 `padding: 6px 10px`
   → 内容区只剩 10px，12px 的字**上下都被切**）；行高与内边距都按字体算；
3. **新增「自定义卡组名」**（与悬浮窗选择器同款）—— 打一个名字加进覆盖表，
   它就成了**自定义条目**（`source='custom'`，`fetch_meta_decks --cn-only` 会建）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtCore import QStringListModel
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)
from ui.i18n import tr

from core import deck_csv, deck_images
from ui import theme, widgets
from ui.floating import WindowButton
from ui.script_runner import ScriptRunner

#: 项目根（卡图路径相对项目根，存图时要拼回来）
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 站内全量名单（分组判据的数据源）—— fetch_meta_decks.SITE_NAMES_PATH 同一份
_SITE_NAMES_PATH = os.path.join(PROJ, 'data', 'site_deck_names.json')
#: 覆盖表的默认位置（真源；`core.deck_csv.CN_CSV` 是唯一来源）
DEFAULT_CSV = deck_csv.CN_CSV
#: 保存后跑这条命令把表贴进库（**不联网**，只读 CSV 写库）
PUSH_SCRIPT = 'tools/fetch_meta_decks.py'
PUSH_ARGS = ['--cn-only']

#: 名字列宽（表头与行共用，保证对齐）
NAME_W = 180
#: 缩略图边长（与 `widgets.thumb_label` 的默认一致）
THUMB = 22
#: 三列宽度比例（中文名 / 别名 / 日文名）
#: ⚠️ 2026-09-21 用户：「缩小中文名的列宽」—— 中文名两个字就够，别名常一串
#:    （`METABEAT / Barrier Statue / 深渊`），所以 3:4:3 → **2:5:3**。
COL_STRETCH = (2, 5, 3)
#: 「删除」按钮的宽度（表头与行共用，保证对齐；0 = 那一行没有按钮）
DEL_W = 28
#: 编辑框的内边距（**必须比 QSS 的 `6px 10px` 小** —— 那是给大控件用的，
#: 在 30px 高的行里会把 12px 的字上下切掉）
EDIT_PAD_V = 2
EDIT_PAD_H = 8


def _edit_h() -> int:
    """行内编辑框的高度 = 字高 + 上下内边距 + 一点余量（**别写死数字**）。

    ⚠️ **只能在有 `QApplication` 之后调**（`widgets.font` 要 QFont 环境）——
    所以不能拿它算模块级常量，见 :func:`row_h`。
    """
    fm = QFontMetrics(widgets.font(theme.F_BODY_SM))
    return fm.height() + EDIT_PAD_V * 2 + 4


def row_h() -> int:
    """一行的高度（编辑框 + 上下各 2px）。

    ⚠️ 是**函数**不是常量：`_edit_h()` 依赖 QFont，模块导入期还没有 QApplication
    （写成模块级常量会在 import 时就崩，且报的是 `Must construct a QGuiApplication`）。
    """
    return _edit_h() + 4


def _spacer(width: int) -> QWidget:
    """定宽**不可见占位控件** —— 用来对齐表头与行里的某一列。

    ⚠️ 必须用**真控件**，不能用 `QLayout.addSpacing()`：`addSpacing` 占的位置
    **不参与** `setSpacing()` 的项间距计算，后面那一项的起点会比预期**少一个
    spacing**（实测正好差 4px = `theme.S_XS`，表头与输入框就错开了）。
    """
    w = QWidget()
    w.setFixedWidth(int(width))
    return w


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


def _edit_style() -> str:
    """行内编辑框的样式：**比全局 QSS 更紧的内边距**（否则字被裁）。

    ⚠️ 只写颜色 / 内边距，**不写 font-**（`ui/widgets.py` 模块头硬规矩①）。
    """
    return ('QLineEdit { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; padding: %dpx %dpx; color: %s; }'
            'QLineEdit:focus { border: 1px solid %s; }'
            % (theme.SURFACE2, theme.HAIRLINE_STRONG, theme.R_XS,
               EDIT_PAD_V, EDIT_PAD_H, theme.INK, theme.HAIRLINE_FOCUS))


def _del_style() -> str:
    """「删除」按钮样式：**紧内边距 + 悬停变红**（别用全局那套大内边距）。

    ⚠️ 不写 `font-`（`ui/widgets.py` 模块头硬规矩①）。
    """
    return ('QPushButton { background: transparent; border: 1px solid %s;'
            ' border-radius: %dpx; padding: 0; color: %s; }'
            'QPushButton:hover { border: 1px solid %s; color: %s; }'
            % (theme.HAIRLINE, theme.R_XS, theme.INK_SUBTLE,
               theme.LOSE, theme.LOSE))


class OppDeckHeaderRow(QWidget):
    """表头：`[图] 站内英文名 | 中文名 | 别名 | 日文名 | 隐藏`。

    ⚠️ 边距 / 间距 / 各列宽 / 伸缩比必须与 :class:`OppDeckRow` **逐字一致**，
    否则表头与下面那些输入框对不齐（本页用户实测反馈第 1 条就是因为没有表头）。
    """

    def __init__(self, hide_w: int = 0, del_w: int = 0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_lab('').fontMetrics().height() + 6)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(theme.S_XS)
        hl.addWidget(_spacer(THUMB))             # 让开缩略图那一格
        name = _lab(tr('站内英文名'), 'muted', size=theme.PAGE_F_SMALL)
        name.setToolTip(tr('站内英文名（连接键，只读 —— 要改名字请新建条目）'))
        name.setFixedWidth(NAME_W)
        name.setStyleSheet('color: %s; background: transparent;' % theme.INK_SUBTLE)
        hl.addWidget(name)
        for text, tip, stretch in zip(
                ('中文名', '别名', '日文名'),
                (tr('中文名'), tr('别名（多个用 / 分隔）'),
                 tr('日文名（留空 = 未填）')),
                COL_STRETCH):
            lab = _lab(tr(text), 'muted', size=theme.PAGE_F_SMALL)
            lab.setStyleSheet('color: %s; background: transparent;'
                              % theme.INK_SUBTLE)
            lab.setToolTip(tip)
            # ⚠️ **必须让标签不参与列宽分配**：`QLabel` 的 minimumSizeHint 是**文字
            #    宽度**，不加这一句「显示英文名」这种长标签会把列撑宽几个像素，
            #    表头与下面的输入框就错开（实测 2~4px）。`Ignored` = 宽度全听布局的。
            lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            hl.addWidget(lab, stretch)
        if hide_w:
            hl.addWidget(_spacer(hide_w))
        if del_w:
            lab = _lab(tr('删'), 'muted', size=theme.PAGE_F_SMALL,
                       align=Qt.AlignCenter)
            lab.setStyleSheet('color: %s; background: transparent;'
                              % theme.INK_SUBTLE)
            lab.setToolTip(tr('删除这一行（保存后才真正从覆盖表移除）'))
            lab.setFixedWidth(int(del_w))
            hl.addWidget(lab)


class ClickableThumb(QLabel):
    """可点的卡图格 —— 点一下 = 给这个卡组换图（用户 2026-10-08）。

    用它替掉 `widgets.thumb_label`（那个是纯展示的 QLabel）。
    """

    clicked = Signal()

    def __init__(self, path: str = '', size: int = THUMB, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._size = int(size)
        self.setStyleSheet('background: %s; border-radius: %dpx;'
                           % (theme.SURFACE2, theme.R_XS))
        self.set_pixmap(path)

    def set_pixmap(self, path: str) -> None:
        if not path:
            self.clear()
            return
        pix = widgets.round_pixmap(path, self._size, theme.R_XS)
        if pix is not None:
            self.setPixmap(pix)
        else:
            self.clear()

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)


#: 卡图**预览**的高度（整张卡 59:86，按高定宽 —— 别裁切卡面）
PREVIEW_H = 96


def card_preview_pixmap(path, h: int = PREVIEW_H):
    """卡绘 -> **保持长宽比**的预览图（读不出来返回 None）。

    ⚠️ 不能走 `widgets.round_pixmap` —— 那个**居中裁成正方形**（给 26² 的方卡图用的），
    整张卡是 59:86 的长方形，裁方会把卡面切掉一截。
    """
    if not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(PROJ, path)
    px = QPixmap(full)
    if px.isNull():
        return None
    return px.scaled(10000, int(h), Qt.KeepAspectRatio, Qt.SmoothTransformation)


class CardPickRow(QWidget):
    """卡列表的一行：**卡名在左 + 右侧「确认」按钮**（选中这一行才出现）。

    用户 2026-10-08：「下方搜索栏的卡名**点击一次**在右边出现确认按钮」。
    ⚠️ 这一行**不下载任何卡图** —— 上一版每行下缩略图（一次预热 12 张），
    用户反馈「下的图太多了」，已回退。只有点「确认」的那张卡才会去取图。
    """

    #: 点了「确认」（参数 = 自己）
    confirmed = Signal(object)

    def __init__(self, cid: int, name: str, parent=None):
        super().__init__(parent)
        self.card_id = int(cid)
        self.card_name = name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, 2, theme.S_SM, 2)
        lay.setSpacing(theme.S_SM)
        #: 只显示卡名 —— 用户 2026-10-08：「去掉卡名后的括号和数字」
        self.label = widgets.label(name, 'body')
        # ⚠️ 标签**必须可收缩**（实测踩过）：长卡名会让 `sizeHint()` 变成文字全宽，
        #    把行撑得比列表视口宽 —— 右侧按钮就跑到可视区外（要横向滚动才点得到）。
        #    与 `ui/selector.py` 的 `DeckRow.name_label` 是同一个坑、同一个修法。
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.label, 1)
        self.btn_confirm = QPushButton(tr('确认'))
        self.btn_confirm.setFont(widgets.font(theme.F_BODY_SM))
        #: ⚠️ **必须套紧样式**（用户 2026-10-08：「确认按钮显示不全」）：
        #:    全局 QSS 的 `QPushButton { padding: 6px 12px }` 在这个 ~18px 高的
        #:    行内按钮上只剩 6px 内容区，12px 的字**上下被裁** —— 与项目里输入框
        #:    被裁是同一个坑（老规矩：行内小控件一律覆写内边距）。
        fm = self.btn_confirm.fontMetrics()
        self.btn_confirm.setStyleSheet(
            'QPushButton { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; padding: 2px 8px; color: %s; }'
            'QPushButton:hover { border: 1px solid %s; }'
            % (theme.SURFACE2, theme.HAIRLINE, theme.R_XS, theme.INK,
               theme.HAIRLINE_STRONG))
        #: 宽 = 字宽 + 左右内边距（8×2）+ 描边 2 —— 别让它把字挤掉
        self.btn_confirm.setFixedSize(
            fm.horizontalAdvance(tr('确认')) + 20, fm.height() + 6)
        self.btn_confirm.setCursor(Qt.PointingHandCursor)
        #: ⚠️ `NoFocus` —— 否则点它会先触发列表的选中变化，把这一行重建掉
        #:（`ui/opp_deck_manager` 的老规矩：行内按钮一律别抢焦点）
        self.btn_confirm.setFocusPolicy(Qt.NoFocus)
        self.btn_confirm.clicked.connect(self._emit_confirmed)
        self.btn_confirm.hide()                  # 默认隐藏，选中这一行才显示
        lay.addWidget(self.btn_confirm, 0)
        self._max_w = 0

    def _emit_confirmed(self) -> None:
        self.confirmed.emit(self)

    def set_selected(self, on: bool) -> None:
        """选中态 —— 右侧「确认」按钮显示 / 隐藏。"""
        on = bool(on)
        self.btn_confirm.setVisible(on)
        self.setStyleSheet(
            'CardPickRow { background: %s; border-radius: %dpx; }'
            % (theme.SURFACE3 if on else 'transparent', theme.R_XS))

    def cap_width(self, width: int) -> None:
        """把行宽**夹到列表视口宽** —— `itemWidget` 按 `sizeHint()` 定宽，不限就溢出。"""
        width = max(int(width), 120)
        if width == self._max_w:
            return
        self._max_w = width
        self.setFixedWidth(width)
        self.updateGeometry()

    def sizeHint(self):                                     # noqa: N802
        hint = super().sizeHint()
        if self._max_w:
            hint.setWidth(self._max_w)
        return hint


class SetImageDialog(QDialog):
    """「设置卡图」对话框（用户 2026-10-08 第二版口径）。

    三条路：**用某张卡的卡图**（主路，可搜中文卡名）/ 从站内卡组取图 / 还原为默认图。
    ⚠️ 「选择本地图片…」已按用户要求**移除**。

    ⚠️ 无边框窗**必须自己画底**（`WA_TranslucentBackground` 只让圆角外透明）——
    同一个坑在 `FetchDecksDialog` / `NewDecksDialog` 都踩过。
    """

    def __init__(self, name: str, current: str = '', parent=None,
                 deck_options=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('设置卡图'))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(theme.qss())
        self.setFixedWidth(400)
        self.deck_name = name
        self.choice = None      #: 'card' / 'site' / 'reset'
        self.card_id = None     #: 'card' 分支选中的游戏内卡 id
        self.image_path = ''    #: 'card' 分支取到的图（临时文件）
        self.site_name = ''
        #: 站内下拉候选：``[(显示名, 站内英文名)]``（由调用方喂，UI 不查库）
        self._options = list(deck_options or [])
        self._drag_from = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_SM)
        head = QHBoxLayout()
        head.addWidget(widgets.label(tr('设置卡图'), 'body'))
        head.addStretch(1)
        btn_x = WindowButton('close')
        btn_x.clicked.connect(self.reject)
        head.addWidget(btn_x)
        lay.addLayout(head)
        who = widgets.label(tr('卡组：%s') % name, 'muted')
        who.setWordWrap(True)
        lay.addWidget(who)

        # ---- 卡图预览 + 名字（名字在卡图**下方**）+ [获取卡图][保存] 同一行
        self.preview = QLabel()
        self.preview.setFixedHeight(PREVIEW_H)
        self.preview.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.picked_label = widgets.label(name, 'body', Qt.AlignCenter)
        self.picked_label.setWordWrap(True)
        lay.addWidget(self.picked_label)
        act = QHBoxLayout()
        act.setSpacing(theme.S_SM)
        act.addStretch(1)
        self.btn_fetch = QPushButton(tr('获取卡图'))
        self.btn_fetch.setToolTip(tr('取选中那张卡的卡图（只看，不落盘）'))
        self.btn_fetch.clicked.connect(self._fetch_card_art)
        act.addWidget(self.btn_fetch)
        self.btn_save = QPushButton(tr('保存'))
        self.btn_save.setDefault(True)
        self.btn_save.setToolTip(tr('把上面这张图设为该卡组的卡图'))
        self.btn_save.clicked.connect(self._save)
        act.addWidget(self.btn_save)
        act.addStretch(1)
        lay.addLayout(act)

        # ---- 主路：搜卡名挑一张卡
        lay.addWidget(widgets.label(tr('用某张卡的卡图（推荐）：'), 'muted'))
        self.card_search = QLineEdit()
        self.card_search.setPlaceholderText(tr('搜卡名，如 灰流丽 / 闪刀姬－篝'))
        self.card_search.setFont(widgets.font(theme.F_BODY_SM))
        self.card_search.setFixedHeight(self.card_search.fontMetrics().height() + 8)
        self.card_search.setStyleSheet(
            'QLineEdit { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; padding: 2px 8px; color: %s; }'
            % (theme.SURFACE2, theme.HAIRLINE_STRONG, theme.R_XS, theme.INK))
        self.card_search.textChanged.connect(self._on_card_query)
        lay.addWidget(self.card_search)
        self.card_list = QListWidget()
        self.card_list.setFont(widgets.font(theme.F_BODY_SM))
        self.card_list.setFixedHeight(132)
        self.card_list.setStyleSheet(
            'QListWidget { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; }'
            % (theme.SURFACE2, theme.HAIRLINE, theme.R_XS))
        #: 横向滚动条一律不要（行宽夹到视口宽后不该出现）
        self.card_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_list.itemClicked.connect(self._on_row_clicked)
        lay.addWidget(self.card_list)
        self._rows = []                      #: 当前列表里的 CardPickRow
        self._selected_id = None

        # ---- 次路：自组卡组 ≈ 某站内卡组时，借它的图（**可搜索下拉**，不要求英文）
        self.btn_site = QPushButton(tr('从站内卡组取图…'))
        self.btn_site.setToolTip(tr('选一个卡组，取它那张 200×200 的卡图'
                                    '（可搜中文名 / 别名 / 日文名）'))
        self.btn_site.clicked.connect(self._ask_site)
        lay.addWidget(self.btn_site)
        self.site_row = QWidget()
        sr = QHBoxLayout(self.site_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(theme.S_XS)
        self.site_picker = QComboBox()
        self.site_picker.setEditable(True)
        self.site_picker.setInsertPolicy(QComboBox.NoInsert)
        self.site_picker.setFont(widgets.font(theme.F_BODY_SM))
        self.site_picker.setMinimumWidth(200)
        self._comp_model = QStringListModel(self)
        comp = QCompleter(self._comp_model, self)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.site_picker.setCompleter(comp)
        self._fill_picker()
        sr.addWidget(self.site_picker, 1)
        btn_ok_site = QPushButton(tr('取图'))
        btn_ok_site.setFocusPolicy(Qt.NoFocus)
        btn_ok_site.clicked.connect(self._take_site)
        sr.addWidget(btn_ok_site)
        self.site_row.hide()
        lay.addWidget(self.site_row)
        self.btn_reset = QPushButton(tr('还原为默认图'))
        self.btn_reset.clicked.connect(self._reset)
        lay.addWidget(self.btn_reset)

        self.hint = widgets.label('', 'muted')
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        foot = QHBoxLayout()
        foot.addStretch(1)
        btn_cancel = QPushButton(tr('取消'))
        btn_cancel.clicked.connect(self.reject)
        foot.addWidget(btn_cancel)
        lay.addLayout(foot)

        self._show_preview(current)          # 先显示当前卡图

    # ------------------------------------------------------------ 预览
    def _show_preview(self, path: str, label: str = None) -> None:
        """把一张图放进预览框（配不出就清空）；`label` 给了就同时改名字行。"""
        pix = card_preview_pixmap(path)
        if pix is None:
            self.preview.clear()
        else:
            self.preview.setPixmap(pix)
        if label is not None:
            self.picked_label.setText(label)

    # ------------------------------------------------------------ 选卡
    def _on_card_query(self, text: str) -> None:
        """边打边搜卡（`core.card_names.search`，中文/英文/日文都认）。

        ⚠️ **只列「有卡绘」的卡**：衍生卡 / 老 id（如「狱炎机衍生物」「灰流丽#3891」）
        不在 ygocdb 里、取图必然 404 —— 列出来只会让用户白点一次（实测踩过）。
        """
        from core import card_art, card_names
        self.card_list.clear()
        self._rows = []
        self._selected_id = None
        shown = 0
        for cid in card_names.search(text, limit=120):
            if not card_art.has_art(cid):
                continue
            name = card_names.display(cid, 'cn') or ('#%s' % cid)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, int(cid))
            self.card_list.addItem(item)
            row = CardPickRow(cid, name)
            row.confirmed.connect(self._on_row_confirmed)
            row.cap_width(self._list_view_w())
            item.setSizeHint(row.sizeHint())
            self.card_list.setItemWidget(item, row)
            self._rows.append(row)
            shown += 1
            if shown >= 40:
                break

    def _list_view_w(self) -> int:
        """卡列表的**可视区宽**（视口再留一点内边距，别贴着边框）。"""
        return max(self.card_list.viewport().width() - 2 * theme.S_SM, 120)

    def _on_row_clicked(self, item) -> None:
        """点列表里的一行 -> **该行右边出现「确认」按钮**（其余行收起）。"""
        cid = int(item.data(Qt.UserRole) or 0)
        self._selected_id = cid
        self._apply_row_selection()

    def _apply_row_selection(self) -> None:
        for row in self._rows:
            row.set_selected(row.card_id == self._selected_id)

    def _on_row_confirmed(self, row) -> None:
        """点了某行的「确认」-> **名字显示到上方卡图的下方**（还没取图）。"""
        self._selected_id = int(row.card_id)
        self._apply_row_selection()
        self.card_id = int(row.card_id)
        self.image_path = ''
        self._show_preview('', row.card_name)     # 图先清空，等「获取卡图」
        self.hint.setText(tr('已选「%s」—— 点「获取卡图」看效果，「保存」生效')
                          % row.card_name)

    # ------------------------------------------------------------ 取图 / 保存
    def _fetch_card_art(self) -> None:
        """「获取卡图」：取**选中那张卡**的卡绘放进预览（只下一张）。"""
        if not self.card_id:
            self.hint.setText(tr('先在下面选一张卡（点一行，再点它右边的「确认」）'))
            return
        from core import deck_images
        self.hint.setText(tr('正在获取卡图…'))
        self.btn_fetch.setEnabled(False)
        try:
            tmp = deck_images.fetch_card_art(self.card_id)
        finally:
            self.btn_fetch.setEnabled(True)
        if not tmp:
            self.hint.setText(tr('取不到那张卡的卡绘（卡表里没有 / 网络不通）'))
            return
        self.image_path = tmp
        self._show_preview(tmp)
        self.hint.setText(tr('卡图已就绪 —— 点「保存」生效'))

    # ------------------------------------------------------------ 收尾
    def _cleanup_tmp(self) -> None:
        """删掉**本对话框下载的**临时卡图（谁建谁删 —— 别让调用方猜）。

        ⚠️ 踩过的坑：曾经让管理器在 `finally` 里删 `dlg.image_path`，
        结果把**调用方自己的图**也删了（测试里正好是真 `_default.webp`，
        直接把默认卡图删没了）。所有权必须留在创建者手里。
        """
        p = self.image_path
        #: 只删我们自己在系统临时目录里下的那种
        if p and os.path.basename(p).startswith('mdm-cardart-'):
            try:
                os.remove(p)
            except OSError:
                pass
        self.image_path = ''

    def done(self, code: int) -> None:                          # noqa: N802
        """关窗（确认 / 取消 / Esc / 点 ×）都走这里 —— 顺带收掉**没被采用**的临时图。

        `choice == 'card'` 且已确认时，那张图由 `take_image_path()` 先取走
        （本对象置空），所以这里 `image_path` 非空 = 用户取了图又取消 -> 删掉。
        """
        self._cleanup_tmp()
        super().done(code)

    def take_image_path(self) -> str:
        """把已取好的卡图路径**交出去**（调用方接手清理责任）。"""
        p = self.image_path
        self.image_path = ''
        return p

    def _save(self) -> None:
        """「保存」：把预览这张图设为卡组卡图。

        没先点「获取卡图」时**就地补一次**（省得用户以为白点了）。
        """
        if not self.card_id:
            self.hint.setText(tr('先在下面选一张卡（点一行，再点它右边的「确认」）'))
            return
        if not self.image_path:
            self._fetch_card_art()
            if not self.image_path:
                return
        self.choice = 'card'
        self.accept()

    # ------------------------------------------------------------ 站内 / 还原
    def _fill_picker(self) -> None:
        """灌站内下拉候选（显示友好名，`itemData` 存站内英文名）。"""
        words = []
        for label, site in self._options:
            self.site_picker.addItem(label, site)
            if label:
                words.append(label)
            if site and site != label:
                words.append(site)
        self._comp_model.setStringList(sorted(set(words)))

    def _ask_site(self) -> None:
        self.site_row.setVisible(not self.site_row.isVisible())
        if self.site_row.isVisible():
            self.site_picker.setFocus()

    def _take_site(self) -> None:
        site = self.site_picker.itemData(self.site_picker.currentIndex()) or ''
        if not site:
            site = (self.site_picker.currentText() or '').strip()
        if not site:
            self.hint.setText(tr('请选一个卡组'))
            return
        self.choice, self.site_name = 'site', site
        self.accept()

    def _reset(self) -> None:
        self.choice = 'reset'
        self.accept()

    # ------------------------------------------------------- 自绘/拖动
    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.R_LG, theme.R_LG)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

    def mousePressEvent(self, ev):                              # noqa: N802
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


class OppDeckRow(QWidget):
    """一行：`[图] 站内英文名 | 中文名 | 别名 | 日文名 | 隐藏 | 删`。"""

    #: 点了这一行的「删除」（参数 = 自己）—— 由管理器负责真的从表里拿掉
    deleted = Signal(object)
    #: 点了这一行的卡图（参数 = 自己）—— 管理器弹「设置卡图」
    image_clicked = Signal(object)

    def __init__(self, row: list, idx: dict, img: str = '', parent=None):
        super().__init__(parent)
        self.idx = idx
        self.csv_row = row
        self.setFixedHeight(row_h())
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(theme.S_XS)
        #: 缩略图（留个引用：单测要验「新加的行有默认图」，也用于换图）
        #: ★ 2026-10-08：改成**可点** —— 点它 = 给这个卡组设置卡图
        self.thumb = ClickableThumb(img or '', THUMB)
        self.thumb.setToolTip(tr('点一下给这个卡组设置卡图'
                                 '（本地图片 / 从站内取图 / 还原）'))
        self.thumb.clicked.connect(lambda _c=False: self.image_clicked.emit(self))
        hl.addWidget(self.thumb)
        name = _lab(deck_csv.get(row, idx, '站内英文名'), 'body',
                    size=theme.PAGE_F_SMALL)
        name.setFixedWidth(NAME_W)
        name.setToolTip(deck_csv.get(row, idx, '站内英文名'))
        hl.addWidget(name)
        self.cn = QLineEdit(deck_csv.get(row, idx, '中文名'))
        self.cn.setPlaceholderText(tr('中文名'))
        self.alias = QLineEdit(deck_csv.get(row, idx, '别名'))
        self.alias.setPlaceholderText(tr('别名（多个用 / 分隔）'))
        #: ★ 2026-10-12 用户：这格原来是「显示英文名」（几乎没人用）——
        #: 换成**日文名**（CSV 里早就有这列，页面上一直没露出来）。
        #: 「显示英文名」列不在页面出现，要改走 Excel。
        self.jp = QLineEdit(deck_csv.get(row, idx, '日文名'))
        self.jp.setPlaceholderText(tr('日文名'))
        for edit in (self.cn, self.alias, self.jp):
            edit.setFont(widgets.font(theme.F_BODY_SM))
            edit.setFixedHeight(_edit_h())          # ★ 按字高算，别写死 22
            edit.setStyleSheet(_edit_style())       # ★ 紧内边距，别用全局那套
        hl.addWidget(self.cn, COL_STRETCH[0])
        hl.addWidget(self.alias, COL_STRETCH[1])
        hl.addWidget(self.jp, COL_STRETCH[2])
        self.hidden = widgets.TickBox(tr('隐藏'))
        self.hidden.setFont(widgets.font(theme.F_BODY_SM))
        self.hidden.setChecked(deck_csv.get(row, idx, '隐藏').upper() == 'Y')
        self.hidden.setToolTip(tr('勾上 = 隐藏这个条目（选择器 / 标注里见不到，'
                                  '但库里的记录还在）'))
        hl.addWidget(self.hidden)
        #: 「删除」—— 点了**只是从界面上拿掉这一行**，点「保存」才真写进 CSV
        self.btn_del = QPushButton('✕')
        self.btn_del.setFont(widgets.font(theme.F_BODY_SM))
        self.btn_del.setFixedSize(DEL_W, _edit_h())
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFocusPolicy(Qt.NoFocus)      # 别抢输入框焦点
        self.btn_del.setStyleSheet(_del_style())
        self.btn_del.setToolTip(tr('从覆盖表里删掉「%s」')
                                % deck_csv.get(row, idx, '站内英文名'))
        self.btn_del.clicked.connect(lambda _c=False: self.deleted.emit(self))
        hl.addWidget(self.btn_del)

    def matches(self, query: str) -> bool:
        """搜索过滤（站内名 / 中文名 / 别名 / 日文名 都认）。"""
        q = (query or '').strip().lower()
        if not q:
            return True
        parts = [self.cn.text(), self.jp.text(), self.alias.text(),
                 deck_csv.get(self.csv_row, self.idx, '站内英文名'),
                 deck_csv.get(self.csv_row, self.idx, '日文名'),
                 deck_csv.get(self.csv_row, self.idx, '备注')]
        return any(q in str(p or '').lower() for p in parts)

    def apply_to(self, row: list, idx: dict) -> None:
        """把界面上的值写回 CSV 行（就地改）。"""
        deck_csv.set_cell(row, idx, '中文名', self.cn.text().strip())
        deck_csv.set_cell(row, idx, '别名', self.alias.text().strip())
        deck_csv.set_cell(row, idx, '日文名', self.jp.text().strip())
        deck_csv.set_cell(row, idx, '隐藏', 'Y' if self.hidden.isChecked() else '')


class NewDeckRow(QWidget):
    """「**自定义卡组名**」那一行（与悬浮窗选择器同款）—— 打名字 → 加进覆盖表。

    加进来的名字**不在站内清单里**，所以 `fetch_meta_decks --cn-only` 会把它建成
    **自定义条目**（`source='custom'`）—— 与用户手改 CSV 的效果完全一样，
    图放 `data/deck_images_custom/`（没有就用默认卡图）。
    """

    #: 点了「添加」（参数 = 名字）
    submitted = Signal(str)

    def __init__(self, add_w: int = 0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(row_h())
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(theme.S_XS)
        self.thumb = widgets.thumb_label('', THUMB)
        hl.addWidget(self.thumb)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr('自定义卡组名…（打一个名字，点「添加」'
                                        '加进覆盖表）'))
        self.edit.setFont(widgets.font(theme.F_BODY_SM))
        self.edit.setFixedHeight(_edit_h())
        self.edit.setStyleSheet(_edit_style())
        self.edit.textChanged.connect(self._on_text)
        self.edit.returnPressed.connect(self._on_return)
        hl.addWidget(self.edit, 1)
        self.btn_add = QPushButton(tr('添加'))
        self.btn_add.setFont(widgets.font(theme.F_BODY_SM))
        self.btn_add.setFixedHeight(_edit_h())
        self.btn_add.setMinimumWidth(56)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setFocusPolicy(Qt.NoFocus)   # 别抢输入框焦点
        self.btn_add.clicked.connect(self._on_return)
        hl.addWidget(self.btn_add)
        if add_w:                    # 与表头 / 其它行的「隐藏」列对齐
            hl.addWidget(_spacer(add_w))
        self._on_text('')

    def text(self) -> str:
        return self.edit.text().strip()

    def _on_text(self, text: str) -> None:
        """边打边配图（与悬浮窗「自定义卡组名」同款：配不出回落默认卡图）。"""
        from core.deck_images import DEFAULT_IMAGE
        pix = widgets.round_pixmap(DEFAULT_IMAGE, THUMB, theme.R_XS)
        if pix is not None:
            self.thumb.setPixmap(pix)

    def _on_return(self) -> None:
        name = self.text()
        if not name:
            self.edit.setFocus()
            return
        self.submitted.emit(name)
        self.edit.clear()


# 「我加的自定义卡组」不再有独立行类（2026-10-12 用户：格式要和上方一致）
# —— 直接用 `OppDeckRow`（可编辑、有删除钮），区别只在：还没进覆盖表，
# 点「保存」才并进去。见 `OppDeckManager._append_custom_row`。


class OppDeckManager(QWidget):
    """卡组名管理（**内嵌**形态）：改覆盖表 → 存盘 → `--cn-only` 进库。

    ⚠️ 这类控件从**对话框**搬来的，只保留「列表 + 编辑 + 保存」——
    窗口标题 / 无边框 / 关闭按钮那些**对话框专属**的东西一律不要。
    """

    #: 已成功写盘（（可选）进库也成功）—— 调用方据此刷新界面
    saved = Signal()

    def __init__(self, csv_path=None, parent=None, runner_factory=None,
                 image_resolver=None, storage=None, custom_names=None,
                 site_names=None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path or DEFAULT_CSV
        self._resolver = image_resolver
        #: 我加过、但不在覆盖表里的自定义卡组名（`opp_decks` 里那些）——
        #: 列成只读行，让它们的卡图也能在这里设。传 None = 从 storage 现算。
        self._custom_names_arg = custom_names
        #: 站内 deck-types 名单（分组判据，见 _SITE_NAMES_PATH）——
        #: 传 None = 从 data/site_deck_names.json 现读；文件也没有 = 全当站内
        self._site_names_arg = site_names
        #: 换图要写库（`set_meta_deck_override`）—— 不传 = 纯 CSV 模式
        #: （单测走这条，不碰真库 / 真图）
        self._storage = storage
        self._runner_factory = runner_factory or (
            lambda script, args: ScriptRunner(script, args))
        self._runner = None
        try:
            self.header, self.rows = deck_csv.read(self.csv_path)
        except Exception:                                    # noqa: BLE001
            #: 表读不出来（被占 / 权限）也别炸构造 —— 界面照开，保存时会再报
            self.header, self.rows = list(deck_csv.COLUMNS), []
        self.idx = deck_csv.index_map(self.header)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S_SM)
        desc = widgets.label(tr('改的是**覆盖表**（%s）；保存后自动跑一次 '
                                'fetch_meta_decks.py --cn-only 把表贴进库。'
                                '「站内英文名」是连接键、只读。') % self.csv_path,
                             'muted')
        desc.setWordWrap(True)
        outer.addWidget(desc)

        search = QHBoxLayout()
        search.addWidget(_lab(tr('搜索'), 'body', size=theme.PAGE_F_SMALL))
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText(tr('卡组名 / 中文名 / 别名 / 日文名'
                                               '（留空 = 全部）'))
        self.edit_search.setFont(widgets.font(theme.F_BODY_SM))
        self.edit_search.setFixedHeight(_edit_h())
        # ⚠️ **必须给搜索框也套紧样式** —— 只设高度不够：全局 QSS 的
        #    `QLineEdit { padding: 6px 10px }` 在 20px 高的框里只剩 6px 内容区，
        #    12px 的字被上下切（用户实测反馈「搜索框的文字显示不全」）。
        self.edit_search.setStyleSheet(_edit_style())
        self.edit_search.textChanged.connect(self._apply_filter)
        search.addWidget(self.edit_search, 1)
        outer.addLayout(search)

        area = QScrollArea()
        area.setObjectName('rowsScroll')
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, True)
        host.setStyleSheet('background: %s;' % theme.CANVAS)
        self.col = QVBoxLayout(host)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(2)
        area.setWidget(host)
        outer.addWidget(area, 1)

        #: 分组判据 = **站内有没有这个 deck-type**（名单来自
        #: `data/site_deck_names.json`，fetch_meta_decks 每次抓取顺手刷新）——
        #: ⚠️ 不是 meta 的 source 字段：手动收录的站内卡组（Ancient Gear 这批）
        #: source 也是 custom，按它分组会把自定义组撑到 80 行（实测事故）。
        #: 无名单文件 / 无参数时 = 全当站内（不出分隔行）。
        self._site_names = set()
        try:
            if self._site_names_arg is not None:
                raw_names = list(self._site_names_arg)
            else:
                import json
                with open(_SITE_NAMES_PATH, encoding='utf-8') as f:
                    raw_names = json.load(f).get('names') or []
            self._site_names = {str(n).strip().lower()
                                for n in (raw_names or [])}
        except Exception:                                    # noqa: BLE001
            self._site_names = set()

        site_items, custom_csv_ui = [], []
        for row in self.rows:
            name = deck_csv.get(row, self.idx, '站内英文名')
            img = ''
            if callable(self._resolver):
                try:
                    img = self._resolver(name) or ''
                except Exception:                            # noqa: BLE001
                    img = ''
            item = OppDeckRow(row, self.idx, img=img)
            item.deleted.connect(self.remove_row)
            item.image_clicked.connect(self.set_deck_image)
            #: 站内判定（2026-10-12 用户：这 3 条要归爬取组）——
            #: ① 全名直接查（D/D/D 这种**名字自带斜杠**的，不能当多值拆掉）；
            #: ② 多值拆分（SkyStriker/Sky Striker）；③ 别名命中也算
            #: （Yummy Engine 的别名就是站内名 Mitsurugi Yummy）
            name_low = (name or '').strip().lower()
            parts = [p.strip().lower()
                     for p in (name or '').replace('/', chr(10)).split(chr(10))
                     if p.strip()]
            alias_low = [p.strip().lower() for p in
                         (deck_csv.get(row, self.idx, '别名') or '')
                         .replace('/', chr(10)).replace('、', chr(10))
                         .split(chr(10)) if p.strip()]
            in_site = (name_low in self._site_names
                       or any(p in self._site_names for p in parts)
                       or any(a in self._site_names for a in alias_low))
            if (bool(parts) and bool(self._site_names) and not in_site):
                custom_csv_ui.append(item)
            else:
                site_items.append(item)
        #: ★ rows_ui 跟随**显示顺序**（站内在前），不是 CSV 顺序（用户 2026-10-12：
        #:   自定义卡组和爬取的卡组之间要分隔行 —— 见下面 custom_divider）
        self.rows_ui = site_items + custom_csv_ui
        for item in site_items:
            self.col.addWidget(item)
        #: ★ 分隔行：爬取的卡组 / 自定义卡组 别再混在一起
        self.custom_divider = self._make_divider()
        self.col.addWidget(self.custom_divider)
        for item in custom_csv_ui:
            self.col.addWidget(item)
        self.custom_csv_ui = custom_csv_ui
        self.col.addStretch(1)

        #: 「自定义卡组名」那一行（**放在最上面**，与悬浮窗选择器同款：
        #: 用户要「加新卡组」时第一眼就能看到）
        hide_w = self.rows_ui[0].hidden.sizeHint().width() if self.rows_ui else 0
        self.new_row = NewDeckRow(add_w=hide_w)
        self.new_row.submitted.connect(self.add_custom)
        self.col.insertWidget(0, self.new_row)

        #: 我加过、但不在覆盖表里的自定义卡组（只读行；卡图可点）
        self.custom_rows_ui = []
        self.custom_label = widgets.label(
            tr('我加的自定义卡组（还没进覆盖表 —— 可直接编辑，点「保存」并进去）'),
            'muted')
        self.custom_label.setContentsMargins(0, theme.S_SM, 0, 0)
        for nm in self._load_custom_names():
            self._append_custom_row(nm)
        #: 表头（在**滚动区内**最顶上 —— 与下面的输入框天然对齐，
        #: 放外面的话滚动条一出现就错位）
        self.header_row = OppDeckHeaderRow(hide_w=hide_w, del_w=DEL_W)
        self.col.insertWidget(0, self.header_row)

        foot = QHBoxLayout()
        self.status = _lab(tr('%d 条') % len(self.rows_ui), 'body',
                           size=theme.PAGE_F_SMALL)
        self.status.setStyleSheet('color: %s; background: transparent;'
                                  % theme.INK_MUTED)
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.btn_save = QPushButton(tr('保存'))
        self.btn_save.setDefault(True)
        self.btn_save.setFont(widgets.font(theme.F_BODY))
        self.btn_save.setFixedHeight(self.btn_save.fontMetrics().height() + 16)
        self.btn_save.setMinimumWidth(120)
        self.btn_save.setToolTip(tr('把改动写回覆盖表 CSV，并跑一次 '
                                    'fetch_meta_decks --cn-only 贴进库'))
        self.btn_save.clicked.connect(self.save)
        foot.addWidget(self.btn_save)
        outer.addLayout(foot)
        #: 初始状态行 —— 只读行（我加的自定义卡组）也要算进去；
        #: 没有只读行时保持老样子（`N 条`），别让常见情形看起来变了。
        self._refresh_status(len(self.rows_ui), len(self.rows_ui))
        #: 没有自定义行时分隔行不露（_apply_filter 只在打字时管它）
        self.custom_divider.setVisible(bool(self.custom_csv_ui))

    def _make_divider(self):
        """分组分隔行：「↓ 自定义卡组」—— 上面是爬取的、下面是自己建的。"""
        lab = widgets.label(tr('—— 自定义卡组 ——'), 'muted')
        lab.setContentsMargins(0, theme.S_SM, 0, theme.S_XS)
        return lab

    # ------------------------------------------------------------ 自定义卡组名
    def add_custom(self, name: str) -> bool:
        """把一个**自定义卡组名**加进覆盖表（内存里；保存才落盘）。

        * 已存在（站内名 / 中文名 / 别名任一撞上）→ 不重复加，状态行说明；
        * 新行：``站内英文名 = 中文名 = 用户打的字``（`custom_rows` 会把它
          建成 `source='custom'` 条目 —— 与用户手改 CSV 完全等价）。
        返回**是否真的加了一行**。
        """
        name = (name or '').strip()
        if not name:
            return False
        if self._find_existing(name) is not None:
            self.status.setText(tr('「%s」已经在表里了（没重复加）') % name)
            return False
        row = [''] * len(self.header)
        deck_csv.set_cell(row, self.idx, '站内英文名', name)
        deck_csv.set_cell(row, self.idx, '中文名', name)
        self.rows.append(row)
        # ⚠️ 新加的卡组**站内 / 覆盖目录都还没有图** → 必须显式回落默认卡图，
        #    否则那一格是空的（用户实测反馈「我添加的卡组没有默认卡图悠悠」）。
        item = OppDeckRow(row, self.idx, img=self._default_img_for(name))
        item.deleted.connect(self.remove_row)
        item.image_clicked.connect(self.set_deck_image)
        self.rows_ui.append(item)
        # 插到 stretch 之前、列表末尾
        self.col.insertWidget(self.col.count() - 1, item)
        self._apply_filter(self.edit_search.text())
        self.status.setText(tr('已加「%s」—— 点「保存」写进覆盖表并贴进库') % name)
        return True

    def remove_row(self, item) -> bool:
        """把一行从**界面 + 待写盘的表**里拿掉（点「保存」才真落盘）。

        ⚠️ 用户口径是「点击删除这行卡组」—— 这里是**删除这一行条目**：
        行从 `rows` / 界面里移除，保存时 `deck_csv.write` 就不会再写它；
        下一条 `--cn-only` 会把对应的**自定义条目**从库里也删掉
        （站内条目只是不再被覆盖，站内榜还在，属于预期）。

        返回**是否真的删了**。
        """
        if item is None or item not in self.rows_ui:
            return False
        name = deck_csv.get(item.csv_row, self.idx, '站内英文名')
        row = item.csv_row
        self.rows_ui.remove(item)
        if row in self.rows:
            self.rows.remove(row)
        self.col.removeWidget(item)
        item.hide()
        item.setParent(None)
        item.deleteLater()
        self._apply_filter(self.edit_search.text())
        self.status.setText(tr('已删除「%s」—— 点「保存」写回覆盖表') % name)
        return True

    # ------------------------------------------------------------ 设置卡图
    # ------------------------------------------------------------ 设置卡图
    def set_deck_image(self, item) -> None:
        """点某行的卡图 → 弹「设置卡图」（本机图片 / 站内取图 / 还原）。

        用户 2026-10-08：「自定义卡组没有卡组卡图」—— 原来只有 CLI、且只认
        meta 表条目；这里给界面一条路（自组卡组名也能设）。
        """
        name = (getattr(item, 'deck_name', None)
                or deck_csv.get(item.csv_row, self.idx, '站内英文名') or '')
        if not name:
            return
        cur = ''
        if callable(self._resolver):
            try:
                cur = self._resolver(name) or ''
            except Exception:                            # noqa: BLE001
                cur = ''
        dlg = SetImageDialog(name, cur, parent=self.window(),
                             deck_options=self._deck_options())
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return
        tmp = None
        try:
            if dlg.choice == 'reset':
                self._reset_deck_image(item, name)
                return
            if dlg.choice == 'card':
                #: ★ 主路：挑一张卡（卡组图 = 代表卡的卡绘）。中文搜卡名即可。
                #: 图通常**已经在对话框里取好了**（「获取卡图」/ 直接「保存」都取过）；
                #: 万一没有（旧调用方）就现取一次。
                src = dlg.take_image_path()       #: 谁用谁接手清理（对话框置空）
                tmp = src if src else None
                if not src or not os.path.exists(src):
                    tmp = src = deck_images.fetch_card_art(dlg.card_id)
                if not src:
                    self.status.setText(
                        tr('取不到那张卡的卡绘（卡表里没有 / 网络不通）'))
                    return
            elif dlg.choice == 'site':
                tmp = deck_images.fetch_site_deck_image(dlg.site_name)
                if not tmp:
                    self.status.setText(
                        tr('站内没有「%s」这张图（自组混轴名会 404；'
                           '试试最接近的站内卡组名）') % dlg.site_name)
                    return
                src = tmp
            else:
                src = dlg.image_path
            #: 统一规格落盘（200×200 webp）—— 归一化在 `save_custom_image` 里
            #: （core 一份实现，CLI 与 UI 共用；Pillow 没有就走 Qt）
            rel = deck_images.save_custom_image(name, src)
            if not rel:
                self.status.setText(tr('设置卡图失败（图读不出来？）'))
                return
            self._write_back(img_rel=rel, name=name)
            full = os.path.join(PROJ, rel.replace('/', os.sep))
            #: ★ 换图必须**先清掉这个路径的 pixmap 缓存**（同名的图被原地覆盖，
            #:   不清就一直显示旧图 —— 2026-10-08 用户：「必须重启 UI 才会修改」）。
            widgets.forget_pixmap(full)
            item.thumb.set_pixmap(full)
            #: 用户 2026-10-08：「红框标出来的框没意义」—— 不回显文件路径。
            self.status.setText(tr('已设置「%s」的卡图') % name)
        except Exception as exc:                         # noqa: BLE001
            self.status.setText(tr('设置卡图失败：%s') % exc)
        finally:
            #: ⚠️ **只删「卡组图」这条路自己下载的临时文件**（`mdm-cardart-*`）——
            #: 别的分支的路径可能是调用方自己的图（实测踩过：删掉了真 `_default.webp`）
            if tmp and os.path.exists(tmp) and                     os.path.basename(tmp).startswith('mdm-cardart-'):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _reset_deck_image(self, item, name: str) -> None:
        """还原：删掉覆盖目录里的图，回到站内图 / 默认图。"""
        removed = deck_images.remove_custom_image(name)
        back = deck_images.find_image_file(name)
        #: 还原同样要清缓存（覆盖图被删；`round_pixmap` 连**缺图也缓存 None**）
        widgets.forget_pixmap(name)
        widgets.forget_pixmap(os.path.join(PROJ, name))
        #: ⚠️ **回落到空串而不是 None**（2026-10-10 修的真事故）：
        #: `set_meta_deck_override` 约定 None = 「这项没意见、不动」，空串 = 「清空」。
        #: 库里若存着 `_default.webp` 当假图（历史污染），这里不清掉就永远显示默认图；
        #: 清空后 `DeckImages.resolve` 会重新走真图链，装了 `deck_images/` 就能显示真图。
        self._write_back(img_rel=(deck_images.rel_or_abs(back) if back else ''),
                         name=name)
        if callable(self._resolver):
            try:
                item.thumb.set_pixmap(self._resolver(name) or '')
            except Exception:                            # noqa: BLE001
                item.thumb.set_pixmap('')
        self.status.setText(tr('已还原「%s」的卡图%s')
                            % (name, ('（删了 %s）' % '、'.join(removed))
                               if removed else ''))

    def _deck_options(self) -> list:
        """「从站内卡组取图」下拉的候选 ``[(显示名, 站内英文名)]``。

        显示名 = 中文名 → 显示名 → 站内名（用户按中文找）；下拉可搜，
        所以中文 / 别名 / 日文都能命中，**不用记英文**。
        """
        st = self._storage
        if st is None:
            return []
        try:
            rows = st.list_meta_decks(include_hidden=False) or []
        except Exception:                                # noqa: BLE001
            return []
        out = []
        for d in rows:
            site = d.get('name') or ''
            if not site:
                continue
            label = (d.get('name_cn') or d.get('name_display') or site)
            out.append((label, site))
        return out

    def _write_back(self, name: str, img_rel) -> None:
        """把 img_file 写进库里的同名条目（**库里没有这条就跳过** —— 自组卡组名
        不在 meta 表，`DeckImages.resolve` 靠文件名直查命中，不需要写库）。

        ⚠️ 站内条目必须写：`resolve()` 第 1 环的别名索引用的是库里的 `img_file`，
        不写的话站内那张旧图会一直盖住新换的（CLI 的 `cmd_set` 同理）。
        ⚠️ `img_rel` **传空串 = 清空该列**（还原卡图时用；`set_meta_deck_override`
        约定 None = 不动、空串 = 清空）。只有 ``None`` 才表示「别碰」。
        """
        st = self._storage
        if st is None or img_rel is None:
            return
        try:
            row = next((d for d in st.list_meta_decks(include_hidden=True)
                        if d.get('name') == name), None)
            if row is not None:
                st.set_meta_deck_override(name, img_file=img_rel)
        except Exception:                                # noqa: BLE001
            pass

    def _default_img_for(self, name: str) -> str:
        """新加卡组的图：先按名字配（`DeckImages.resolve`），配不出用**默认卡图**。

        ⚠️ `resolve()` 自己已经会在配不出时回落 `_default.webp`；这里再兜一层，
        是因为新加的行**还没进库** —— 稳妥起见直接确认拿到一个存在的文件，
        别让那一格空着。
        """
        if callable(self._resolver):
            try:
                got = self._resolver(name) or ''
                if got and os.path.exists(got):
                    return got
            except Exception:                                # noqa: BLE001
                pass
        from core.deck_images import DEFAULT_IMAGE
        return DEFAULT_IMAGE if os.path.exists(DEFAULT_IMAGE) else ''

    def _find_existing(self, name: str):
        """表里已有这个叫法吗（站内名 / 中文名 / 别名 / 显示名 任一命中）。"""
        q = name.strip().lower()
        if not q:
            return None
        for item in self.rows_ui:
            cells = [deck_csv.get(item.csv_row, self.idx, col)
                     for col in ('站内英文名', '中文名', '显示英文名', '日文名')]
            cells += [deck_csv.get(item.csv_row, self.idx, '别名')]
            for cell in cells:
                for part in str(cell or '').replace('/', '\n').split('\n'):
                    if part.strip().lower() == q:
                        return item
        return None

    # ------------------------------------------------ 我加的自定义卡组（只读）
    def _load_custom_names(self):
        """`opp_decks` 里**不在覆盖表、也不在 meta 任何叫法里**的名字。

        ⚠️ 判据要全：`烙印`（Branded 的中文名）、`Atlantean`（Mermail Atlantean
        的别名）都在 `opp_decks` 里，但它们**已经有条目**，不该再列一行。
        """
        st = self._storage
        if self._custom_names_arg is not None:
            own = list(self._custom_names_arg)
        elif st is None:
            return []
        else:
            try:
                own = [t[1] for t in (st.list_opp_decks() or [])
                       if len(t) > 1 and t[1]]
            except Exception:                            # noqa: BLE001
                return []
        csv_names = {deck_csv.get(r, self.idx, '站内英文名') for r in self.rows}
        csv_names |= {deck_csv.get(r, self.idx, c)
                      for r in self.rows for c in ('中文名', '显示英文名')}
        for r in self.rows:
            csv_names |= set(str(deck_csv.get(r, self.idx, '别名') or '')
                             .replace('/', '\n').split('\n'))
        meta_keys = set()
        try:
            for d in (st.list_meta_decks(include_hidden=True) if st else []):
                for k in (d.get('name'), d.get('name_cn'), d.get('name_display'),
                          d.get('name_ja')):
                    if k:
                        meta_keys.add(str(k).strip())
                for a in (d.get('aliases') or []):
                    meta_keys.add(str(a).strip())
        except Exception:                                # noqa: BLE001
            pass
        skip = {'未知卡组', '混轴卡组'}
        seen, out = set(), []
        for nm in own:
            nm = (nm or '').strip()
            if (not nm or nm in skip or nm in seen
                    or nm in csv_names or nm in meta_keys):
                continue
            seen.add(nm)
            out.append(nm)
        return out

    def _append_custom_row(self, name: str):
        """追加一条**自定义行**（列表末尾、stretch 之前）。

        ⚠️ 2026-10-12 用户口径：「自定义卡组格式应该和上方卡组一致」——
        所以不再用只读行，直接给 `OppDeckRow`（可编辑中文名/别名、可删），
        数据是一行**合成 CSV 行**（站内英文名 = 中文名 = 名字，还没进表）；
        点「保存」时由 `save()` 并进覆盖表（转正）。`deck_name` 属性保留 ——
        单测和 `set_deck_image` 都靠它取名字。
        """
        img = ''
        if callable(self._resolver):
            try:
                img = self._resolver(name) or ''
            except Exception:                            # noqa: BLE001
                img = ''
        row = [''] * len(self.header)
        deck_csv.set_cell(row, self.idx, '站内英文名', name)
        deck_csv.set_cell(row, self.idx, '中文名', name)
        item = OppDeckRow(row, self.idx, img=img or self._default_img_for(name))
        item.deck_name = name
        item.deleted.connect(self._remove_custom_row)
        item.image_clicked.connect(self.set_deck_image)
        self.custom_rows_ui.append(item)
        if self.custom_label.parent() is None:
            self.col.insertWidget(self.col.count() - 1, self.custom_label)
        self.col.insertWidget(self.col.count() - 1, item)
        return item

    def _remove_custom_row(self, item) -> bool:
        """把一条**自定义行**从界面拿掉（还没进覆盖表 —— 不涉及 CSV）。"""
        if item is None or item not in self.custom_rows_ui:
            return False
        self.custom_rows_ui.remove(item)
        self.col.removeWidget(item)
        item.hide()
        item.setParent(None)
        item.deleteLater()
        self._apply_filter(self.edit_search.text())
        self.status.setText(tr('已移除「%s」（还没进覆盖表，不用保存）')
                            % (getattr(item, 'deck_name', '') or ''))
        return True

    # ------------------------------------------------------------ 过滤
    def _refresh_status(self, shown: int, total: int) -> None:
        """状态行 —— 有只读行时单独交代一句（它们不在覆盖表里）。"""
        if self.custom_rows_ui:
            self.status.setText(
                tr('%d / %d 条（其中 %d 条待保存进覆盖表）')
                % (shown, total, len(self.custom_rows_ui)))
        elif shown == total:
            self.status.setText(tr('%d 条') % total)
        else:
            self.status.setText(tr('%d / %d 条') % (shown, total))

    def _apply_filter(self, text: str) -> None:
        shown = 0
        for item in self.rows_ui:
            ok = item.matches(text)
            item.setVisible(ok)
            shown += 1 if ok else 0
        c_shown = 0
        for item in self.custom_rows_ui:
            ok = item.matches(text)
            item.setVisible(ok)
            c_shown += 1 if ok else 0
        self.custom_label.setVisible(bool(self.custom_rows_ui) and c_shown > 0)
        csv_c_shown = sum(1 for item in self.custom_csv_ui
                          if item.isVisibleTo(self))
        self.custom_divider.setVisible(bool(self.custom_csv_ui)
                                       and csv_c_shown > 0)
        self._refresh_status(shown + c_shown,
                             len(self.rows_ui) + len(self.custom_rows_ui))

    # ------------------------------------------------------------ 保存
    def save(self) -> None:
        """写回 CSV（+ 起 `--cn-only` 把表贴进库）。失败**不抛**，状态行说明。"""
        for item in self.rows_ui:
            item.apply_to(item.csv_row, self.idx)
        #: 自定义行（我加的、还没进表的）一并并入 —— 与上方行同格式之后，
        #: 「保存」就是它们的转正入口（用户 2026-10-12）
        for item in self.custom_rows_ui:
            item.apply_to(item.csv_row, self.idx)
            self.rows.append(item.csv_row)
        try:
            deck_csv.write(self.csv_path, self.header, self.rows)
        except Exception as exc:                             # noqa: BLE001
            self.status.setText(tr('写盘失败：%s') % exc)
            return
        self.status.setText(tr('已写入 %s，正在贴进库…') % self.csv_path)
        self.saved.emit()
        runner = self._runner_factory(PUSH_SCRIPT, list(PUSH_ARGS))
        runner.done.connect(self._on_pushed)
        self._runner = runner
        self.btn_save.setEnabled(False)
        runner.start()

    def _on_pushed(self, code: int) -> None:
        self.btn_save.setEnabled(True)
        if int(code) == 0:
            self.status.setText(tr('已保存并贴进库（选择器 / 看板即时生效）'))
        else:
            self.status.setText(
                tr('已保存到 CSV，但贴进库失败（退出码 %d）—— 可手动跑 %s %s')
                % (int(code), PUSH_SCRIPT, ' '.join(PUSH_ARGS)))
