# -*- coding: utf-8 -*-
"""「导入 YDK」对话框 —— 把一副牌的 YDK 文件存成**卡组指纹**。

用户需求（2026-10-28）
----------------------
我方用参照库里没有的自组卡组时认不出来。让用户导入一个 `.ydk` 文件当指纹，
之后识别我方卡组时比对「是不是这副」（容错细节见 `core.deck_fingerprint`）。

YDK 从哪来：
* **get-deck.com**（查卡页有链接）：上传卡组截图 → 导出 YDK；
* 游戏内把卡组复制到剪贴板（也是密码列表）。

⚠️ 本对话框**不做识别**（那是 get-deck 的事）——它只负责：
读文件 → 解析 → 预览（多少张 / 主卡额外各多少）→ 存指纹。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout)

from ui import theme, widgets
from ui.i18n import tr


class YdkImportDialog(QDialog):
    """导入 YDK -> 卡组指纹。

    用法::

        dlg = YdkImportDialog()
        if dlg.exec() == QDialog.Accepted:
            name, ids = dlg.result_name, dlg.result_ids
    """

    def __init__(self, parent=None, path: str = '',
                 initial_name: str = ''):
        super().__init__(parent)
        self.setWindowTitle(tr('导入 YDK'))
        self.setModal(True)
        self.resize(560, 340)

        self.result_name: str = ''
        self.result_ids: list = []
        self._deck = None
        self._unknown: list = []
        self._path = ''

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)

        lay.addWidget(_lab(tr('导入 YDK'), 'title', size=theme.F_STAT))
        tip = _lab(tr(
            'YDK 可从 get-deck.com 把卡组截图识别后导出（查卡页有链接）。\n'
            '导入后，软件识别我方卡组时会比对「是不是这副」。'), 'muted')
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # ---- 文件行
        row = QHBoxLayout()
        row.setSpacing(theme.S_MD)
        self.ed_path = QLineEdit(path)
        self.ed_path.setPlaceholderText(tr('选择一个 .ydk 文件…'))
        self.ed_path.setReadOnly(True)
        row.addWidget(self.ed_path, 1)
        self.btn_browse = QPushButton(tr('浏览…'))
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        lay.addLayout(row)

        # ---- 名字行
        named = QHBoxLayout()
        named.setSpacing(theme.S_MD)
        named.addWidget(_lab(tr('卡组名：'), 'body'))
        self.ed_name = QLineEdit(initial_name)
        self.ed_name.setPlaceholderText(tr('这副牌在软件里叫什么'))
        named.addWidget(self.ed_name, 1)
        lay.addLayout(named)

        # ---- 预览
        self.lbl_preview = _lab('', 'body')
        self.lbl_preview.setWordWrap(True)
        lay.addWidget(self.lbl_preview)
        self.lbl_warn = _lab('', 'muted')
        self.lbl_warn.setWordWrap(True)
        lay.addWidget(self.lbl_warn)

        lay.addStretch(1)

        # ---- 按钮
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_ok = QPushButton(tr('导入'))
        self.btn_ok.setDefault(True)
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._accept)
        foot.addWidget(self.btn_ok)
        self.btn_cancel = QPushButton(tr('取消'))
        self.btn_cancel.clicked.connect(self.reject)
        foot.addWidget(self.btn_cancel)
        lay.addLayout(foot)

        if path:
            self._load(path)

    # ------------------------------------------------------------ 交互
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr('选择 YDK 文件'), '',
            tr('YDK 卡组文件 (*.ydk);;所有文件 (*.*)'))
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        """读文件并刷新预览（失败时把原因写在预览行，**不弹二次框**）。"""
        from core import ydk as Y
        self._path = path
        self.ed_path.setText(path)
        deck, err = Y.read_ydk(path)
        if deck is None:
            self._deck = None
            self.lbl_preview.setText('')
            self.lbl_warn.setText(err)
            self.btn_ok.setEnabled(False)
            return
        self._deck = deck
        ids = Y.to_game_ids(deck.all_ids)
        self._unknown = Y.unknown_codes(deck.all_ids)
        self.lbl_preview.setText(
            tr('主卡组 %d 张 · 额外 %d 张 · 换算后共 %d 张（%d 种）')
            % (len(deck.main), len(deck.extra), len(ids), len(set(ids))))
        if self._unknown:
            self.lbl_warn.setText(
                tr('有 %d 张卡软件认不出（卡表里没有），这些不会参与比对：%s')
                % (len(self._unknown), ', '.join(self._unknown[:8])))
        else:
            self.lbl_warn.setText('')
        #: 没名字时用文件名兜底（用户可改）
        if not self.ed_name.text().strip():
            import os
            self.ed_name.setText(
                os.path.splitext(os.path.basename(path))[0])
        self.btn_ok.setEnabled(bool(ids) and bool(self.ed_name.text().strip()))

    def _accept(self) -> None:
        from core import ydk as Y
        name = self.ed_name.text().strip()
        if not (self._deck and name):
            return
        ids = Y.to_game_ids(self._deck.all_ids)
        if not ids:
            self.lbl_warn.setText(tr('这个文件里的卡软件一张都认不出'))
            return
        self.result_name = name
        self.result_ids = ids
        self.accept()


def _lab(text: str, role: str = 'body', size=None) -> QLabel:
    """定高标签（高度跟字体算，矮了会裁字 —— 与 dashboard 的 `_lab` 同规矩）。"""
    lab = widgets.label(text, role)
    if size:
        lab.setFont(widgets.font(size))
    return lab
