# -*- coding: utf-8 -*-
"""「卡组名管理」（D3 §2）—— 直接改**覆盖表 CSV**，**内嵌**在「卡组」页的页签里。

用户口径（2026-09-21）：「卡组页不需要区分对手卡组和我的卡组，应该分**卡组名管理**
和**卡组构成**两个页签。」→ 原来那个 Q14 弹窗（`OppDeckDialog`）拆成
**可内嵌的 `OppDeckManager(QWidget)`**，弹窗本身**删掉**。

口径没变的部分（照原 Q14 文档）：

* 改的是覆盖表 `data/deck_name_cn.csv`（**唯一真源**），保存后自动跑一次
  `tools/fetch_meta_decks.py --cn-only` 把表贴进库（**不联网**，只把 CSV 写进
  `meta_decks`）—— 所以在这里改完，选择器 / 看板立刻生效。
* 能改四样：**中文名 / 别名 / 显示英文名 / 隐藏**；「站内英文名」是**连接键**只读。
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
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)
from ui.i18n import tr

from core import deck_csv
from ui import theme, widgets
from ui.script_runner import ScriptRunner

#: 覆盖表的默认位置（真源；`core.deck_csv.CN_CSV` 是唯一来源）
DEFAULT_CSV = deck_csv.CN_CSV
#: 保存后跑这条命令把表贴进库（**不联网**，只读 CSV 写库）
PUSH_SCRIPT = 'tools/fetch_meta_decks.py'
PUSH_ARGS = ['--cn-only']

#: 名字列宽（表头与行共用，保证对齐）
NAME_W = 180
#: 缩略图边长（与 `widgets.thumb_label` 的默认一致）
THUMB = 22
#: 三列宽度比例（中文名 / 别名 / 显示英文名）
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
    """表头：`[图] 站内英文名 | 中文名 | 别名 | 显示英文名 | 隐藏`。

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
                ('中文名', '别名', '显示英文名'),
                (tr('中文名'), tr('别名（多个用 / 分隔）'),
                 tr('显示英文名（留空 = 用站内名）')),
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


class OppDeckRow(QWidget):
    """一行：`[图] 站内英文名 | 中文名 | 别名 | 显示英文名 | 隐藏 | 删`。"""

    #: 点了这一行的「删除」（参数 = 自己）—— 由管理器负责真的从表里拿掉
    deleted = Signal(object)

    def __init__(self, row: list, idx: dict, img: str = '', parent=None):
        super().__init__(parent)
        self.idx = idx
        self.csv_row = row
        self.setFixedHeight(row_h())
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(theme.S_XS)
        #: 缩略图（留个引用：单测要验「新加的行有默认图」，以后换图也用得上）
        self.thumb = widgets.thumb_label(img or '', THUMB)
        self.thumb.setToolTip(tr('卡图（新卡组默认用悠悠；'
                                 '想换就放一张进 data/deck_images_custom/）'))
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
        self.en = QLineEdit(deck_csv.get(row, idx, '显示英文名'))
        self.en.setPlaceholderText(tr('留空 = 用站内名'))
        for edit in (self.cn, self.alias, self.en):
            edit.setFont(widgets.font(theme.F_BODY_SM))
            edit.setFixedHeight(_edit_h())          # ★ 按字高算，别写死 22
            edit.setStyleSheet(_edit_style())       # ★ 紧内边距，别用全局那套
        hl.addWidget(self.cn, COL_STRETCH[0])
        hl.addWidget(self.alias, COL_STRETCH[1])
        hl.addWidget(self.en, COL_STRETCH[2])
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
        """搜索过滤（站内名 / 中文名 / 别名 / 显示英文名 / 日文名 都认）。"""
        q = (query or '').strip().lower()
        if not q:
            return True
        parts = [self.cn.text(), self.en.text(), self.alias.text(),
                 deck_csv.get(self.csv_row, self.idx, '站内英文名'),
                 deck_csv.get(self.csv_row, self.idx, '日文名'),
                 deck_csv.get(self.csv_row, self.idx, '备注')]
        return any(q in str(p or '').lower() for p in parts)

    def apply_to(self, row: list, idx: dict) -> None:
        """把界面上的值写回 CSV 行（就地改）。"""
        deck_csv.set_cell(row, idx, '中文名', self.cn.text().strip())
        deck_csv.set_cell(row, idx, '别名', self.alias.text().strip())
        deck_csv.set_cell(row, idx, '显示英文名', self.en.text().strip())
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


class OppDeckManager(QWidget):
    """卡组名管理（**内嵌**形态）：改覆盖表 → 存盘 → `--cn-only` 进库。

    ⚠️ 这类控件从**对话框**搬来的，只保留「列表 + 编辑 + 保存」——
    窗口标题 / 无边框 / 关闭按钮那些**对话框专属**的东西一律不要。
    """

    #: 已成功写盘（（可选）进库也成功）—— 调用方据此刷新界面
    saved = Signal()

    def __init__(self, csv_path=None, parent=None, runner_factory=None,
                 image_resolver=None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path or DEFAULT_CSV
        self._resolver = image_resolver
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

        self.rows_ui = []
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
            self.rows_ui.append(item)
            self.col.addWidget(item)
        self.col.addStretch(1)

        #: 「自定义卡组名」那一行（**放在最上面**，与悬浮窗选择器同款：
        #: 用户要「加新卡组」时第一眼就能看到）
        hide_w = self.rows_ui[0].hidden.sizeHint().width() if self.rows_ui else 0
        self.new_row = NewDeckRow(add_w=hide_w)
        self.new_row.submitted.connect(self.add_custom)
        self.col.insertWidget(0, self.new_row)
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

    # ------------------------------------------------------------ 过滤
    def _apply_filter(self, text: str) -> None:
        shown = 0
        for item in self.rows_ui:
            ok = item.matches(text)
            item.setVisible(ok)
            shown += 1 if ok else 0
        self.status.setText(tr('%d / %d 条') % (shown, len(self.rows_ui)))

    # ------------------------------------------------------------ 保存
    def save(self) -> None:
        """写回 CSV（+ 起 `--cn-only` 把表贴进库）。失败**不抛**，状态行说明。"""
        for item in self.rows_ui:
            item.apply_to(item.csv_row, self.idx)
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
