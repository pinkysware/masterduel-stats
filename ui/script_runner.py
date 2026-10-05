# -*- coding: utf-8 -*-
"""跑 `tools/` 下脚本的封装 —— QProcess + 逐行日志 + 完成回调（D3 设置区三功能用）。

为什么抽成一份：看板设置区三个功能（抓禁限表 / 卡表更新 / 刷新数据）都要
「起一个脚本 → 边跑边回显 → 跑完刷新界面」，而 `ui/app.py` 里那套是手写的、
**只留最后一行**（用户看不到中间发生了什么，失败时尤其难查）。

⚠️ 只能在**主线程**用（QProcess 的 `readyRead` 要走事件循环）。
⚠️ 命令一律 `sys.executable <项目根相对脚本> <args>`，工作目录钉在项目根 ——
与 `ui/app.py` 现有成法一致（那些脚本都按项目根相对路径找数据）。

用法::

    runner = ScriptRunner('tools/fetch_banlist.py')
    runner.line.connect(log_view.appendPlainText)
    runner.done.connect(on_done)
    runner.start()
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, QProcess, Signal

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ScriptRunner(QObject):
    """跑一个 python 脚本：`line` 逐行吐输出，`done` 给退出码。"""

    #: 一行输出（已 strip 行尾；stdout 与 stderr **合并**—— 失败原因多在 stderr）
    line = Signal(str)
    #: 跑完（参数 = 退出码；正常结束是 0）
    done = Signal(int)

    def __init__(self, script, args=None, parent=None, proc_factory=None):
        super().__init__(parent)
        self.script = script
        self.args = list(args or [])
        self._proc = None
        self._buf = ''
        #: 测试注入点：返回一个「像 QProcess」的对象（不真起进程）
        self._proc_factory = proc_factory or self._make_proc

    def _make_proc(self):
        proc = QProcess(self)
        proc.setWorkingDirectory(PROJ)
        return proc

    def running(self) -> bool:
        """还在跑吗（按钮禁用判据）。"""
        if self._proc is None:
            return False
        try:
            return self._proc.state() != QProcess.NotRunning
        except Exception:                                    # noqa: BLE001
            return False

    def start(self) -> None:
        """起进程（已在跑就忽略 —— 防连点起两个）。"""
        if self.running():
            return
        proc = self._proc_factory()
        self._proc = proc
        proc.setProgram(sys.executable)
        proc.setArguments([self.script] + self.args)
        #: 错误也回显：只读 stdout 的话失败时日志区一片空白，什么都查不出来
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_read)
        proc.finished.connect(self._on_finished)
        proc.start()

    def stop(self) -> None:
        """掐掉（关窗口 / 切 demo 时用）—— 没在跑就什么都不做。"""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.state() != QProcess.NotRunning:
                proc.kill()
        except Exception:                                    # noqa: BLE001
            pass

    # ------------------------------------------------------------ 内部
    def _on_read(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            raw = bytes(proc.readAllStandardOutput())
        except Exception:                                    # noqa: BLE001
            return
        text = self._buf + raw.decode('utf-8', 'replace')
        lines = text.split('\n')
        self._buf = lines.pop()          # 最后一段可能是半行，留着等下次
        for line in lines:
            line = line.strip()
            if line:
                self.line.emit(line)

    def _on_finished(self, code, _status=None) -> None:
        if self._buf.strip():             # 收尾：最后半行也算一行
            self.line.emit(self._buf.strip())
        self._buf = ''
        self.done.emit(int(code))
