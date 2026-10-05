# -*- coding: utf-8 -*-
"""看板设置区的**跑脚本页**（D3 第三刀）—— 四个工具页共用一个类。

三个页 = 抓禁限表 / 卡表更新 / 数据体检（09-23 加入；「刷新数据」
2026-10-05 用户拍板移除 —— 常驻采集器自动做，工具本体保留可手动跑）。
（其余五个设置项也已于 09-26 设置页优化时真接主区，老对话框整体下掉。）
各页结构完全一样（标题 + 说明 + 执行按钮
+ **只读多行日志**），差异只有命令与文案 —— 所以命令表集中在 :data:`SCRIPT_COMMANDS`
（**唯一来源**），页面类只有一份。

⚠️ 退出码不能一律当失败：`tools/build_card_names.py --check-update` 的语义是
**1 = 有 B 类未知卡（要人工看）/ 2 = 联网失败**（见 `tools/build_card_names.py`
的 argparse 与 `main()`），照 :func:`exit_note` 的翻译显示。

跑脚本一律经 :class:`ui.script_runner.ScriptRunner`（QProcess，跑时禁用按钮、
逐行回显）；**测试注入 `runner_factory`**，不真起进程。

★ 2026-09-21 第三刀（用户：「**没更新表测试不了**」= 表已最新时点按钮看不出任何变化）：
在本页加三样可观测性，全部落在本文件 ——

1. **页头常驻「当前数据状态」一行**（`status_line`，数据来自
   `core.ui_state.data_status()`）—— **不跑也能核对**；
2. **跑完给「结果摘要」**：日志末尾追加一行重跑 `data_status()` 的渲染
   （有变化看得见，**没变化也说得清**）；
3. **退出码的语义高亮**：`--check-update` 退出码 1 = 有 B 类未知卡（要人工看），
   原来只体现为一行灰字，现在把那一行**标红**。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ui import theme, widgets
from ui.script_runner import ScriptRunner

#: 数据状态的取数 / 渲染都在数据层（纯函数，可单测）；这里只负责画
from core import ui_state

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 三个功能的命令表 —— **唯一来源**（页 key 与 `ui/dashboard.py` 的
#: `SETTINGS_PAGES` 一致）。``runs`` = [(按钮文字, 参数), …]（同一个脚本可能有
#: 「只检查」和「真更新」两个入口）。
SCRIPT_COMMANDS = {
    's_banlist': {
        'title': '抓禁限表',
        'script': 'tools/fetch_banlist.py',
        'desc': '抓 masterduelmeta 的**禁限状态 + 变更史**并落库（联网，约十几秒）。'
                '一个月手动跑一次就够；跑完「禁限表」页即时更新。',
        'runs': [('抓取', [])],
    },
    's_cardtable': {
        'title': '卡表更新',
        'script': 'tools/build_card_names.py',
        'desc': '卡名三语表（data/card_names.json）的上游检查 / 刷新（联网）。'
                '「检查更新」只比对上游 md5 并把查不到的卡 id 分成两类；'
                '「下载更新」刷新快照后重新合成卡表，**卡表刷新后对新卡才有名字**。',
        'runs': [('检查更新', ['--check-update']), ('下载更新', ['--update'])],
    },
    's_audit': {
        'title': '数据体检',
        'script': 'tools/audit_issues.py',
        'desc': '一键检测三类数据事故（离线，秒出）：①结果缺失（结算没抓到，'
                '悬浮窗显示 —）；②整局漏记（磁盘上有对局、库里没有）；'
                '③未收口的记录目录（采集器中途被杀）。'
                '有问题会逐项列出；「漏记/未收口」可手跑 '
                'tools/import_duel_records.py --apply 补入。',
        'runs': [('开始体检', [])],
    },
}

#: 退出码的**特殊语义**（脚本自己定的，别当通用规则）：脚本 -> {码: 说明}
EXIT_NOTES = {
    'tools/build_card_names.py': {
        1: '有 B 类未知卡 id（要人工看一眼，不算失败）',
        2: '联网失败（上游取不到，稍后再试）',
    },
}


#: 哪些退出码「不算失败、但要人工看一眼」—— 界面据此**标红**那一行
ATTENTION_CODES = {
    'tools/build_card_names.py': (1,),        # 1 = 有 B 类未知卡 id
    'tools/audit_issues.py': (1,),            # 1 = 体检发现数据问题（要人工看）
}


def exit_note(script, code) -> str:
    """退出码 → 给人看的一句话（**纯函数**，单测直接锁）。"""
    table = EXIT_NOTES.get(script) or {}
    if code in table:
        return table[code]
    return '成功' if code == 0 else '失败（退出码 %d）' % code


def needs_attention(script, code) -> bool:
    """这个退出码是不是「要人工看」（**纯函数**）—— 是的话日志那行标红。"""
    return int(code) in (ATTENTION_CODES.get(script) or ())
# ⚠️ 09-27：原挂在这儿的 `ScriptPage`（独立工具页）已由看板设置概览页的
# 「运行」按钮 + 页底日志区**整体取代并删除** —— 本文件现在只出
# 命令表（`SCRIPT_COMMANDS`）与两个退出码纯函数（`exit_note` / `needs_attention`）。
