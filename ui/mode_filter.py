# -*- coding: utf-8 -*-
"""模式多选筛选按钮（Excel 式打勾）—— 战绩页与对局分析**各自独立**使用。

用户 2026-10-14 原话：「这几个模式做成 excel 那种可以打勾筛选的形式。
比如我勾选排名决斗和 DC 杯，对局分析就显示这些模式的对局」。
「对局分析和战绩页各自独立筛选」—— 所以两边**各持一个实例**、各存各的 QSettings，
本控件本身**不碰存储**（存什么 key、什么时候存由页面决定）。

## 三种状态（2026-10-15 定案）

| 菜单勾选 | `selected()`（数据值） | 含义 |
|---|---|---|
| 四个全勾（默认） | ``[]`` | **全部模式** = 四组并集（天梯+巅峰+DC+活动） |
| 勾了一部分 | ``['ranked','dc']`` | 只算这些模式（并集） |
| 一个都没勾 | ``[MODE_SELECT_NONE]`` | **未选择模式** → 0 场（不是「全部」） |

★ **「全部」是主开关**（用户 2026-10-15：「全部高亮再点全部应该全部取消」）：
勾 = 四模式全勾（全部高亮）；取消 = 四模式全取消（= 未选择）。

★ **实时刷新**（用户 2026-10-15：「选完必须点一下才刷新」）：**每勾一下就发
`changed`**，不用关菜单 —— 所以菜单得**点一项不关闭**。

★ **点一项不关闭**：用 `QWidgetAction` 装 `QCheckBox` 列表；`QMenu.exec()` 里点普通
checkable action 会**立刻关闭**（Qt 默认行为），所以不能用 `addAction().setCheckable`。

⚠️ 语义基石：**空列表 = 「全部模式」= 四组并集**（`mode_group_clause([])`）。
它**不是**主口径（只天梯+巅峰）—— 以前「全部」传 ``None``，而
`get_cross_stats(None)` 会走主口径 —— 那正是「打了 35 局 DC 杯、对局分析还是空」
的根因（2026-10-14 用户报）。
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QMenu, QPushButton, QVBoxLayout,
                               QWidget, QWidgetAction)

from core.storage import MODE_GROUP_NAMES, MODE_SELECT_NONE
from ui import theme, widgets
from ui.i18n import tr

#: 菜单里的分类顺序（`solo` 不进筛选 —— 2026-10-03 用户口径：单人模式不算对局）
FILTER_KEYS = tuple(k for k in MODE_GROUP_NAMES if k != 'solo')


def as_keys(keys) -> List[str]:
    """字符串 / None / 列表 -> 合法 key 列表（**向后兼容旧的单值调用**）。

    ⚠️ 必需：单测与老代码还在传 `'dc'` 这种**字符串**；直接迭代字符串会得到
    `'d'`/`'c'` 两个字符（2026-10-14 踩过，22 项测试当场变红）。
    ⚠️ **`MODE_SELECT_NONE` 哨兵要保留**（不能被 FILTER_KEYS 过滤掉）。
    """
    if not keys:
        return []
    if isinstance(keys, str):
        if keys == MODE_SELECT_NONE:
            return [MODE_SELECT_NONE]
        return [keys] if keys in FILTER_KEYS else []
    return [k for k in keys if k in FILTER_KEYS or k == MODE_SELECT_NONE]


def label_for(keys) -> str:
    """勾选列表 -> 按钮文字（空 = 「全部模式」；哨兵 = 「未选择模式」）。"""
    keys = as_keys(keys)
    if MODE_SELECT_NONE in keys:
        return tr('未选择模式')
    if not keys:
        return tr('全部模式')
    if len(keys) == 1:
        return tr(MODE_GROUP_NAMES[keys[0]])
    if len(keys) == len(FILTER_KEYS):
        return tr('全部模式')
    return tr('%s 等 %d 项') % (tr(MODE_GROUP_NAMES[keys[0]]), len(keys))


class ModeFilterButton(QPushButton):
    """「全部模式 ▾」—— 点开是可打勾的多选菜单（点一项**不关闭**、**实时生效**）。"""

    changed = Signal(object)          # 勾选的数据值（[] = 全部；[NONE] = 未选择）

    def __init__(self, parent=None, prefix: str = ''):
        super().__init__(parent)
        self.setObjectName('modeFilter')
        #: 按钮文字前缀（战绩页空；对局分析页给「模式：」以与日期按钮对齐）
        self._prefix = prefix
        self.setFont(widgets.font(theme.PAGE_F_SMALL))
        self.setFixedHeight(self.fontMetrics().height() + 14)
        self.setCursor(Qt.PointingHandCursor)
        #: **勾选态** = FILTER_KEYS 的子集。空集 = 一个都没勾（未选择模式）；
        #: 全集 = 全部模式。⚠️ 空集与全集是**两个不同的数据值**（见 selected()）。
        self._checked: set = set(FILTER_KEYS)
        self.clicked.connect(self._show_menu)
        self._refresh_text()

    # -- 状态 ----------------------------------------------------------
    def selected(self) -> List[str]:
        """勾选态 -> **数据值**：全集 = ``[]``（全部模式）；空集 = ``[NONE]``；否则列 key。"""
        if not self._checked:
            return [MODE_SELECT_NONE]
        if self._checked >= set(FILTER_KEYS):
            return []
        return [k for k in FILTER_KEYS if k in self._checked]

    def set_selected(self, keys) -> None:
        """外部设置（读 QSettings / set_data 后调）—— **不发 `changed`**。

        ★ **只在数据值真的变了时才动勾选**：实时刷新下 `set_data` 会频繁回调，
        无脑重置会把菜单里刚勾的状态冲掉（勾一下就被打回）。
        """
        keys = as_keys(keys)
        if self._canon(self.selected()) == self._canon(keys):
            return                        # 数据值没变 -> 别动界面（含菜单里正勾着的状态）
        if MODE_SELECT_NONE in keys:
            self._checked = set()
        elif not keys:
            self._checked = set(FILTER_KEYS)
        else:
            self._checked = set(keys)
        self._refresh_text()

    @staticmethod
    def _canon(keys) -> frozenset:
        """数据值 -> **规范化指纹**（空列表 / 全勾都算「全部模式」= 同一枚空集）。"""
        ks = as_keys(keys)
        if MODE_SELECT_NONE in ks:
            return frozenset({MODE_SELECT_NONE})
        if not ks or len(ks) >= len(FILTER_KEYS):
            return frozenset()
        return frozenset(ks)

    # -- 内部 ----------------------------------------------------------
    def _refresh_text(self) -> None:
        self.setText('%s%s ▾' % (self._prefix, label_for(self.selected())))

    def _toggle(self, key: str, on: bool) -> None:
        """勾/取消一项（只改本地状态 + 按钮文字；信号由菜单那层发）。"""
        if on:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._refresh_text()

    def _show_menu(self) -> None:
        """弹菜单：`QWidgetAction` 装 `QCheckBox` 列表 —— **点一项不关闭、实时生效**。

        `exec()` 是**模态**的，勾选期间的 `changed` 会在它自己的事件循环里派发
        （页面随之重建）—— 所以按钮实例必须**跨重建存活**，否则菜单会被销毁
        （见 `StatsPage._detach_mode_btn`）。
        """
        menu, _host = self._build_menu()
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))

    def _build_menu(self):
        """建菜单（**不 exec** —— 拆出来才测得动，`exec()` 是模态阻塞的）。

        返回 ``(menu, host)``；``host`` 里是那串 `QCheckBox`（「全部」+ 各模式）。
        """
        menu = QMenu(self)
        wa = QWidgetAction(menu)
        host = QWidget()
        host.setObjectName('modeFilterMenu')
        #: 点 checkbox 别让事件冒泡到 QMenu（否则可能被当成「选中某项」而关菜单）
        host.setAttribute(Qt.WA_NoMousePropagation, True)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        #: 「全部」= 主开关：勾 = 四模式全勾；取消 = 四模式全取消（用户 2026-10-15）
        all_cb = QCheckBox(tr('全部'))
        cb_by_key = {}
        for key in FILTER_KEYS:
            cb_by_key[key] = QCheckBox(tr(MODE_GROUP_NAMES[key]))

        def _sync() -> None:
            for k, cb in cb_by_key.items():
                cb.blockSignals(True)
                cb.setChecked(k in self._checked)
                cb.blockSignals(False)
            all_cb.blockSignals(True)
            all_cb.setChecked(self._checked >= set(FILTER_KEYS))
            all_cb.blockSignals(False)

        def _emit() -> None:
            self.changed.emit(self.selected())        #: ★ 实时刷新：勾一下就发

        def _on_all(checked: bool) -> None:
            self._checked = set(FILTER_KEYS) if checked else set()
            self._refresh_text()
            _sync()
            _emit()

        def _on_key(key: str, checked: bool) -> None:
            self._toggle(key, checked)
            _sync()
            _emit()

        _sync()                                      # 初始态（默认 = 全部高亮）
        all_cb.toggled.connect(_on_all)
        lay.addWidget(all_cb)
        #: 细分隔线（别用 `QMenu.addSeparator` —— 它在 action 之间、不在 widget 内部）
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet('background: %s;' % theme.HAIRLINE)
        lay.addWidget(sep)
        for key in FILTER_KEYS:
            cb = cb_by_key[key]
            cb.toggled.connect(lambda on, k=key: _on_key(k, on))
            lay.addWidget(cb)
        wa.setDefaultWidget(host)
        menu.addAction(wa)
        return menu, host


def parse_setting(raw) -> List[str]:
    """QSettings 里的字符串 -> key 列表（逗号分隔；空/坏值 = 全部）。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = str(raw).split(',')
    out = []
    for k in (x.strip() for x in items):
        if k == MODE_SELECT_NONE or k in FILTER_KEYS:
            out.append(k)
    return out


def dump_setting(keys) -> str:
    """key 列表 -> QSettings 字符串（**全勾/空都存空串** = 全部；哨兵原样存）。"""
    keys = as_keys(keys)
    if MODE_SELECT_NONE in keys:
        return MODE_SELECT_NONE
    return '' if len(keys) == len(FILTER_KEYS) else ','.join(keys)
