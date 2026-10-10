# -*- coding: utf-8 -*-
"""退出主程序时**一并收掉采集器**（2026-10-10）。

## 为什么需要

`start.bat` / `MDStats.exe` 一次拉起**两个**进程：采集器（`pythonw`，无窗口）
和界面（`tools/duel_ui.py`）。但界面那边点「×」或托盘「退出」时，
`ui.app.MasterDuelUiApp.quit()` 只让**自己**的 `QApplication` 退出 ——
采集器是独立进程，**没人通知它**，于是它继续在后台跑：

* 用户以为全退了，实际还有一个 pythonw 在采集（吃 CPU、占内存）；
* 更糟的是**删不掉目录**：`rm -rf` 报 `Device or resource busy`
  （实测 2026-10-10 清理测试包时踩到，得先 `taskkill` 才能删）；
* 下次 `start.bat` 会拉起第二个采集器 —— 虽然有 pid 文件的双开检测挡着，
  但那只是让新实例退出，用户会看到「启动了却没反应」。

所以这里补上退出联动：**界面退出 → 读采集器的 pid 文件 → 校验 → 结束它**。

## ⚠️ 判据必须复用采集器自己的 `pid_alive`

**不能只按 pid 杀**。Windows 会复用 pid：2026-10-08 事故，10-06 那次采集器的
pid 后来被系统分给了 `svchost`，如果这里照着 pid 文件盲杀，**就会杀掉
一个无辜的系统进程**。所以判据是 `pid_alive(pid, born)`：

1. 镜像名 ∈ {python, pythonw}（不是 svchost 之类）
2. pid 文件里记的创建时间与当前进程一致

两者都过才动手。详见 `tools/duel_collector.py` 的 `pid_alive` 文档。

## 不做的事

* **不删 pid 文件** —— 让采集器自己走优雅退出那条路去清（它 `finally` 里有
  `clear_pid_file()`）。我们只发信号，不越俎代庖改它的收口逻辑。
  ⚠️ 实测 `taskkill`（温和版）**不保证**它跑到 `finally`，所以 pid 文件**通常会留下**。
  这**无害**：下次启动时采集器自己的双开检测会用 `pid_alive` 校验，
  发现「进程已不是采集器」就按陈旧文件继续启动（那条路径本来就处理了，
  见 `tools/duel_collector.py` 启动自愈段）。**不要**在这里替它删 ——
  万一它正跑着，删了 pid 文件会**关掉双开保护**，那才是真事故。
* **不杀别的 python** —— 只认 pid 文件里那一个，且必须过上面两道校验。
"""
from __future__ import annotations

import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 采集器写的 pid 文件（与 `tools/duel_collector.py` 的 `PID_PATH` 同一份）
#: ⚠️ 文件名是 `duel_collector.pid`（**不是** `collector_pid.json`）
PID_PATH = os.path.join(PROJ, 'data', 'archive', 'duel_collector.pid')


def _load_pid() -> dict:
    try:
        with open(PID_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def stop_collector(timeout_ms: int = 3000) -> str:
    """收掉采集器进程。返回一句人话（给日志/状态行用）。

    ``'no-pid-file'``   没有 pid 文件（没起过采集器）
    ``'not-running'``   pid 文件在，但那个进程已经不在了 / 不是采集器
    ``'stopped'``       已正常结束
    ``'forced'``       温和结束没成功，强制杀了
    ``'denied'``        结束被系统拒绝（权限不足）
    """
    data = _load_pid()
    pid = data.get('collector_pid')
    if not pid:
        return 'no-pid-file'

    # ⚠️ 校验逻辑**借采集器自己的**，别在这里重写一份（口径必须唯一）
    try:
        sys.path.insert(0, os.path.join(PROJ, 'tools'))
        from duel_collector import pid_alive
    except Exception:                                    # noqa: BLE001
        pid_alive = None

    born = data.get('collector_born')
    if pid_alive is not None:
        try:
            alive = pid_alive(pid, born)
        except Exception:                                # noqa: BLE001
            alive = False
    else:
        # 兜底：拿不到判据就**不杀**（宁可漏收，也不能盲杀一个 pid 复用进程）
        alive = False

    if not alive:
        return 'not-running'

    return _terminate(int(pid), timeout_ms)


def _terminate(pid: int, timeout_ms: int) -> str:
    """先温和结束（WM_CLOSE），超时再强杀。"""
    if os.name == 'nt':
        return _terminate_win(pid, timeout_ms)
    return _terminate_posix(pid)


def _terminate_win(pid: int, timeout_ms: int) -> str:
    """Windows：先 `taskkill /PID`（无 /F，走正常退出），再等，最后 `/F`。"""
    import subprocess
    try:
        subprocess.run(['taskkill', '/PID', str(pid)],
                       capture_output=True, timeout=max(1, timeout_ms // 1000))
    except Exception:                                    # noqa: BLE001
        pass
    if _wait_gone(pid, timeout_ms):
        return 'stopped'
    try:
        subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
    except Exception:                                    # noqa: BLE001
        pass
    return 'stopped' if _wait_gone(pid, 2000) else 'denied'


def _terminate_posix(pid: int) -> str:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return 'not-running'
    return 'stopped' if _wait_gone(pid, 3000) else 'denied'


def _wait_gone(pid: int, timeout_ms: int) -> bool:
    """进程是否在超时内消失（纯查询，不杀）。"""
    import time
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if not _alive_raw(pid):
            return True
        time.sleep(0.15)
    return not _alive_raw(pid)


def _alive_raw(pid: int) -> bool:
    """裸的「这个 pid 现在还在吗」（**仅用于确认已退出**，不据此决定杀谁）。"""
    if os.name == 'nt':
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(
                h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            #: STILL_ACTIVE = 259
            return bool(ok) and code.value == 259
        except Exception:                                # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True