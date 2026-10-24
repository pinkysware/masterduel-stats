# -*- coding: utf-8 -*-
"""看板「导出」页 —— 把对局记录导成 CSV / JSON，**按月 + 按模式**筛选。

用户口径（2026-09-22）：「导出需要在左边栏单独做一项，按月和模式筛选导出」。

* 筛选器与「对局分析」页**同款**（实体按钮 + 下拉菜单），口径也同源：
  * **月份** = 库里实际有对局的月份（`ui_state.export_months()`，与战绩页热力图
    和对局分析页同一份清单）；
  * **模式** = `storage.MODE_GROUP_NAMES`（天梯 / 巅峰 / DC 杯 / 活动）。
* 页面**只画 + 发信号**：取数在 `core.ui_state.export_rows()`，写盘在 app 侧
  （弹保存框）—— M7 纪律：UI 层不查库、不落数据。
* 页上给一份**预览**（前若干行 + 总行数），让用户在点「导出」前确认筛对了。

⚠️ 「硬币」列**已删**（2026-10-03 用户口径）—— 库里 `won_initiative`
仍保留（悬浮窗先后攻还用它），只是不再导出。
"""
from __future__ import annotations

from ui.i18n import tr

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from core.storage import MODE_GROUP_NAMES
from ui import theme, widgets

#: 预览最多显示几行（够看清列对不对即可，别把整页撑爆）
PREVIEW_ROWS = 12


def _lab(text: str, role: str = 'body', size: int = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— 与 `tier_page.py` 同款（独立文件不互引）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


class ExportPage(QWidget):
    """「导出」页：月份 / 模式筛选 + 预览 + [导出 CSV][导出 JSON]。"""

    #: 筛选变了（参数 = ``(month, mode_group)``）—— 窗口据此重取预览
    filter_changed = Signal(object, object)
    #: 点了导出（参数 = ``(fmt, month, mode_group)``）—— app 侧弹框写盘
    export_requested = Signal(str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._month = None
        self._mode = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        outer.setSpacing(theme.S_MD)

        head = QHBoxLayout()
        head.setSpacing(theme.S_MD)
        head.addWidget(_lab(tr('导出'), 'body', size=theme.PAGE_F_TITLE))
        head.addStretch(1)
        self.btn_csv = QPushButton(tr('导出 CSV'))
        self.btn_json = QPushButton(tr('导出 JSON'))
        for btn in (self.btn_csv, self.btn_json):
            btn.setFont(widgets.font(theme.F_BODY))
            btn.setFixedHeight(btn.fontMetrics().height() + 14)
            btn.setMinimumWidth(110)
        self.btn_csv.setToolTip(tr('CSV：Excel 直接双击打开（utf-8 BOM + CRLF）'))
        self.btn_json.setToolTip(tr('JSON：带列名表与导出时间，中文不转义'))
        self.btn_csv.clicked.connect(lambda _c=False: self._emit_export('csv'))
        self.btn_json.clicked.connect(lambda _c=False: self._emit_export('json'))
        head.addWidget(self.btn_csv)
        head.addWidget(self.btn_json)
        outer.addLayout(head)

        note = widgets.label(
            tr('导出的是**对局记录**（一行一局）。日期与时间已分列；')
            + tr('点「导出」写全部列。'), 'muted')
        note.setWordWrap(True)
        note.setMaximumWidth(760)
        outer.addWidget(note)

        filters = QHBoxLayout()
        filters.setSpacing(theme.S_MD)
        self._month_btn = self._filter_button('month')
        self._mode_btn = self._filter_button('mode')
        filters.addWidget(self._month_btn)
        filters.addWidget(self._mode_btn)
        filters.addStretch(1)
        self.count = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.count.setStyleSheet('color: %s; background: transparent;'
                                 % theme.INK_MUTED)
        filters.addWidget(self.count)
        outer.addLayout(filters)

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

        # ★ 2026-10-24 用户：「导出应该能导出**整个软件数据**」—— 卸载重装
        #   （保留数据仍不完全）/ 换机迁移，都靠这一个 zip 带走全部。
        outer.addWidget(widgets.hairline())
        bk_head = QHBoxLayout()
        bk_head.setSpacing(theme.S_MD)
        bk_head.addWidget(_lab(tr('全量备份'), 'body',
                               size=theme.PAGE_F_BODY,
                               weight=QFont.Weight.Medium))
        bk_head.addStretch(1)
        self.btn_backup = QPushButton(tr('备份全部数据…'))
        self.btn_restore = QPushButton(tr('从备份恢复…'))
        self.btn_diag = QPushButton(tr('导出诊断包…'))
        for btn in (self.btn_backup, self.btn_restore, self.btn_diag):
            btn.setFont(widgets.font(theme.F_BODY))
            btn.setFixedHeight(btn.fontMetrics().height() + 14)
        self.btn_backup.setToolTip(
            tr('把全部用户数据（数据库、对局记录、卡组覆盖表、自定义卡组图、')
            + tr('状态与缓存）打包成一个 zip —— 卸载 / 换机前备一份。'))
        self.btn_restore.setToolTip(
            tr('选择备份 zip → 解到暂存区，重启界面后生效（现有数据会先保留，')
            + tr('恢复是同名覆盖、不删其它文件）。'))
        self.btn_diag.setToolTip(
            tr('环境快照（版本/路径/设置/库概况）+ 各日志尾部，打成 zip')
            + tr(' —— 报问题时把这个发给开发者。'))
        self.btn_backup.clicked.connect(self._do_backup)
        self.btn_restore.clicked.connect(self._do_restore)
        self.btn_diag.clicked.connect(self._do_diag)
        bk_head.addWidget(self.btn_backup)
        bk_head.addWidget(self.btn_restore)
        outer.addLayout(bk_head)

        self._preview = []

    # ------------------------------------------------------------ 全量备份
    def _do_backup(self) -> None:
        """备份全部用户数据 → 一个 zip（文件对话框选保存位置）。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import backup
        import os as _os
        import time as _time
        default = 'masterduel-backup-%s.zip' % _time.strftime('%Y%m%d-%H%M')
        dest, _f = QFileDialog.getSaveFileName(
            self, tr('备份全部数据'), default, 'ZIP (*.zip)')
        if not dest:
            return
        if not dest.lower().endswith('.zip'):
            dest += '.zip'
        try:
            r = backup.make_backup(dest)
        except OSError as e:
            QMessageBox.warning(self, tr('备份失败'), '%s' % (e,))
            return
        QMessageBox.information(
            self, tr('备份完成'),
            tr('已备份 %d 个文件（%.1f MB）到：\n%s')
            % (r['files'], r['bytes'] / 1048576.0, _os.path.abspath(dest)))

    def _do_restore(self) -> None:
        """从备份 zip 恢复 —— 先拦采集器，再确认，最后 stage + 提示重启。

        真正应用在**下次启动**（core/backup.apply_pending）：界面 / 采集器
        开着时 SQLite 被锁，热覆盖会 PermissionError，所以这里只解暂存区。
        """
        import zipfile
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import backup
        from core import ui_state
        try:
            hb = ui_state.collector_status()
        except Exception:                                  # noqa: BLE001
            hb = {}
        if hb.get('running'):
            QMessageBox.warning(
                self, tr('采集器正在运行'),
                tr('恢复前请先退出采集器（托盘菜单 → 退出，或任务栏关掉')
                + tr(' Duel Collector）再试 —— 它正锁着数据库。'))
            return
        src, _f = QFileDialog.getOpenFileName(
            self, tr('从备份恢复'), '', 'ZIP (*.zip)')
        if not src:
            return
        try:
            with zipfile.ZipFile(src) as z:
                stamp = backup.read_stamp(z)
        except (OSError, zipfile.BadZipFile) as e:
            QMessageBox.warning(self, tr('这不是有效的备份文件'), '%s' % (e,))
            return
        when = stamp.get('created_at') or '?'
        n = sum(1 for x in zipfile.ZipFile(src).namelist()
                if not x.endswith('/'))
        ret = QMessageBox.question(
            self, tr('确认恢复？'),
            tr('备份时间：%s（%d 个文件）。\n\n恢复是**同名覆盖**：备份里有的')
            + tr('文件会盖掉现在的，备份里没有的**保留不动**。\n现在只解到')
            + tr('暂存区，**重启界面后生效**。'),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            backup.stage_restore(src)
        except (OSError, zipfile.BadZipFile) as e:
            QMessageBox.warning(self, tr('恢复准备失败'), '%s' % (e,))
            return
        QMessageBox.information(
            self, tr('已就绪'),
            tr('备份已解到暂存区。请**重启界面**（设置 → 重启界面）完成恢复。'))

    def _do_diag(self) -> None:
        """导出诊断包（环境快照 + 日志尾部）—— 报问题给开发者用。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from core import diagnostics
        import os as _os
        import time as _time
        default = 'masterduel-diag-%s.zip' % _time.strftime('%Y%m%d-%H%M%S')
        dest, _f = QFileDialog.getSaveFileName(
            self, tr('导出诊断包'), default, 'ZIP (*.zip)')
        if not dest:
            return
        if not dest.lower().endswith('.zip'):
            dest += '.zip'
        try:
            r = diagnostics.make_bundle(dest)
        except Exception as e:                          # noqa: BLE001
            QMessageBox.warning(self, tr('导出失败'), '%r' % (e,))
            return
        cut = ('；截断 %d 个大文件（全量在数据目录）' % len(r['truncated'])
               if r['truncated'] else '')
        QMessageBox.information(
            self, tr('诊断包已导出'),
            tr('环境快照 + %d 个日志文件（%.1f KB）%s。\n%s')
            % (r['log_files'], r['bytes'] / 1024.0, cut,
               _os.path.abspath(dest)))

    # ------------------------------------------------------------ 筛选器
    #: ★ 筛选器 = **key 驱动**（审查 P0-1/P0-4）：分支/路由只认 'month'/'mode'，
    #:   显示文案一律现 tr —— 拿中文显示串当 key 会在 en/ja 下功能错乱。
    _KEY_LABEL = {'month': '月份', 'mode': '模式'}

    def _filter_button(self, key: str) -> QPushButton:
        from ui.i18n import lang
        btn = QPushButton('%s%s%s ▾' % (tr(self._KEY_LABEL[key]),
                                        '：' if lang() == 'cn' else ': ',
                                        tr('全部')))
        btn.setObjectName('modeFilter')            # 复用战绩页筛选的 QSS
        btn.setFont(widgets.font(theme.PAGE_F_SMALL))
        btn.setFixedHeight(btn.fontMetrics().height() + 14)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._show_menu(btn, key))
        return btn

    def _menu_items(self, key: str):
        """下拉项 ``[(值, 显示名)]`` —— 顺序即菜单顺序（显示名现 tr）。"""
        if key == 'month':
            return list(self._data.get('months') or [('', tr('全部月份'))])
        items = [(None, tr('全部模式'))]
        #: ⚠️ 单人模式不进筛选（2026-10-03 用户口径，与战绩页/对局分析页一致）
        items += [(k, tr(v)) for k, v in MODE_GROUP_NAMES.items()
                  if k != 'solo']
        return items

    def _filter_text(self, key: str, value) -> str:
        if key == 'month':
            return value or tr('全部')
        return tr(MODE_GROUP_NAMES.get(value, '全部'))

    def _show_menu(self, btn, key: str) -> None:
        menu = QMenu(btn)
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = self._month if key == 'month' else self._mode
        for k, name in self._menu_items(key):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(k == current)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, v=k, kk=key:
                                  self._pick(kk, v))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _pick(self, key: str, value) -> None:
        if key == 'month':
            if value == self._month:
                return
            self._month = value
        else:
            if value == self._mode:
                return
            self._mode = value
        self._refresh_filter_texts()
        self.filter_changed.emit(self._month, self._mode)

    def _refresh_filter_texts(self) -> None:
        from ui.i18n import lang
        sep = '：' if lang() == 'cn' else ': '
        for key, btn in (('month', self._month_btn), ('mode', self._mode_btn)):
            btn.setText('%s%s%s ▾' % (tr(self._KEY_LABEL[key]), sep,
                                      self._filter_text(key, getattr(
                                          self, '_month' if key == 'month'
                                          else '_mode'))))

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        """窗口喂进来的预览数据（**本页不查库**）。

        形状：``{'months': [(值, 显示名)], 'rows': [导出列 dict], 'total': int}``
        """
        self._data = dict(data or {})
        #: 月份清单变了（比如切了 demo）—— 当前选中的月份若已不在清单里就回落到全部
        months = [m for m, _n in (self._data.get('months') or [])]
        if self._month is not None and (self._month or '') not in months:
            self._month = None
        self._refresh_filter_texts()
        self._rebuild()

    def month(self):
        return self._month

    def mode_group(self):
        return self._mode

    def preview_rows(self):
        """当前预览的行（单测用）。"""
        return list(self._preview)

    def _clear(self) -> None:
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        self._preview = list(self._data.get('rows') or [])
        total = int(self._data.get('total') or len(self._preview))
        self.count.setText(tr('共 %d 局（预览前 %d 行）')
                           % (total, min(total, len(self._preview))))
        has = bool(self._preview)
        self.btn_csv.setEnabled(has or total > 0)
        self.btn_json.setEnabled(has or total > 0)
        if not self._preview:
            empty = widgets.label(
                tr('当前筛选下没有对局 —— 换个月份 / 模式试试'), 'label')
            empty.setFont(widgets.font(theme.PAGE_F_BODY))
            self.col.addWidget(empty)
            self.col.addStretch(1)
            return
        self.col.addWidget(self._header())
        for r in self._preview:
            self.col.addWidget(self._row(r))
        if total > len(self._preview):
            more = _lab(tr('…还有 %d 局（导出时才全部写出）')
                        % (total - len(self._preview)), 'body',
                        size=theme.PAGE_F_SMALL)
            more.setStyleSheet('color: %s; background: transparent;'
                               % theme.INK_SUBTLE)
            self.col.addWidget(more)
        self.col.addStretch(1)

    # ------------------------------------------------------------ 预览表
    #: 预览用哪几列（**不是全部** —— 页面上放不下，点「导出」才是全列）
    PREVIEW_COLS = (('date', '日期', 52), ('time', '时间', 52),
                    ('result', '结果', 48),
                    ('went_first', '先后手', 60), ('my_deck', '我方卡组', 130),
                    ('opp_deck', '对手卡组', 130),
                    ('mode_label', '模式', 84))

    def _col_w(self, key: str) -> int:
        """列宽 = `max(基准, 译文表头宽 + 10)` —— en 的 First/Second 不再截断
        （2026-10-04 用户实测；悬浮窗 `_text_col_w` 同款口径）。"""
        cache = getattr(self, '_w_cache', None)
        if cache is None:
            cache = self._w_cache = {}
        if key not in cache:
            for k, label, w in self.PREVIEW_COLS:
                if k == key:
                    lab = _lab(tr(label), 'muted', size=theme.PAGE_F_SMALL)
                    cache[key] = max(
                        w, lab.fontMetrics().horizontalAdvance(tr(label)) + 10)
        return cache.get(key, 0)

    def _header(self) -> QWidget:
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 2)
        hl.setSpacing(theme.S_SM)
        for _k, label, w in self.PREVIEW_COLS:
            lab = _lab(tr(label), 'muted', size=theme.PAGE_F_SMALL)
            lab.setStyleSheet('color: %s; background: transparent;'
                              % theme.INK_SUBTLE)
            lab.setFixedWidth(self._col_w(_k))
            hl.addWidget(lab)
        hl.addStretch(1)
        return box

    def _row(self, r: dict) -> QWidget:
        box = QWidget()
        box.setFixedHeight(theme.ROW_H if hasattr(theme, 'ROW_H') else 24)
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.S_SM)
        for key, _label, w in self.PREVIEW_COLS:
            val = r.get(key)
            text = '' if val is None else str(val)
            lab = _lab(text, 'body', size=theme.PAGE_F_SMALL)
            lab.setFixedWidth(self._col_w(key))
            lab.setToolTip(text)
            hl.addWidget(lab)
        hl.addStretch(1)
        return box

    # ------------------------------------------------------------ 导出
    def _emit_export(self, fmt: str) -> None:
        self.export_requested.emit(str(fmt), self._month, self._mode)
