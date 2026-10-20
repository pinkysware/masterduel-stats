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
import time
from typing import Optional
from core import paths

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(msg: str) -> None:
    """写一行日志到 `DATA_DIR/archive/ui_collector_ctl.log`（失败静默）。

    为什么必须有：`start_collector()` 成功拉起进程后要留痕 —— 少了它，
    调用方（`ui/app.py` 的 `_ensure_collector`）只能用 `except: pass` 吞掉一切，
    「到底拉起来没有」无从判断（2026-10-18 BUG-003）。
    """
    try:
        d = os.path.join(paths.data_path('archive'))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'ui_collector_ctl.log'), 'a',
                  encoding='utf-8', newline='') as f:
            f.write('%s  %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


#: 采集器写的 pid 文件（与 `tools/duel_collector.py` 的 `PID_PATH` 同一份）
#: ⚠️ 文件名是 `duel_collector.pid`（**不是** `collector_pid.json`）
PID_PATH = paths.data_path('archive', 'duel_collector.pid')


def _load_pid() -> dict:
    try:
        with open(PID_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def stop_collector(timeout_ms: int = 1500) -> str:
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


def collector_running() -> bool:
    """采集器**现在**是否在跑（读 pid 文件 + 严格判据）。"""
    data = _load_pid()
    pid = data.get('collector_pid')
    if not pid:
        return False
    try:
        sys.path.insert(0, os.path.join(PROJ, 'tools'))
        from duel_collector import pid_alive
    except Exception:                                    # noqa: BLE001
        # 拿不到判据时**保守当成在跑** —— 别因为判断不了就把采集器再拉一个
        return _alive_raw(int(pid))
    try:
        return bool(pid_alive(pid, data.get('collector_born')))
    except Exception:                                    # noqa: BLE001
        return False


def start_collector(minutes: int = 0, interval: int = 2.0) -> Optional[int]:
    """**拉起一个采集器进程**（返回 pid；失败返回 None）。

    为什么需要（2026-10-10 实机事故）：界面退出时会收掉采集器（见模块头），
    但**重新启动的那一侧不一定带着它** —— `start.bat` / `MDStats.exe` 是一次性
    拉起两个，用户若只起了界面（或上次是异常退出），采集器就没了：
    表现为「打了一局，界面零记录」，而日志里连一行启动记录都没有。

    所以界面启动时自查一遍：**不在就把它拉起来**（见 `ensure_collector`）。
    """
    if collector_running():
        return None
    script = os.path.join(PROJ, 'tools', 'duel_collector.py')
    if not os.path.exists(script):
        return None
    import subprocess
    #: 无窗口启动（对齐 start.bat 的 `start "" "%PYW%"`）。
    #: ⚠️ **不要加 `DETACHED_PROCESS`**（2026-10-10 实测踩过）：加了它
    #:    `Popen` 返回的是那个「脱离的启动壳」的 pid，**不是**采集器真正的 pid
    #:    —— 实测返回 None / 对不上号。`CREATE_NO_WINDOW` 本身就够安静了。
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    #: 🔴 **必须用 pythonw**（2026-10-10 实机事故）：`sys.executable` 在开发环境
    #:   是 `python.exe`（**带控制台**），界面退出时补拉的采集器就**闪一个黑框**
    #:   —— 用户原话「退出后会闪一个黑框」。项目里早有 `pythonw()` 这个函数
    #:   （`ui/app.py`，当初为「重启界面不闪黑框」写的），这里复用它。
    exe = sys.executable
    _w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if os.path.exists(_w):
        exe = _w
    args = [exe, script, '--minutes', str(int(minutes)),
            '--interval', str(interval)]
    try:
        proc = subprocess.Popen(args, cwd=PROJ, close_fds=True,
                                creationflags=flags)
    except Exception:                                    # noqa: BLE001
        return None
    _log('[collector_ctl] 采集器不在，已拉起 pid=%s' % proc.pid)
    return proc.pid


def ensure_collector(minutes: int = 0) -> Optional[int]:
    """界面启动时的保险：**采集器不在就拉起来**（在则什么都不做）。"""
    try:
        if collector_running():
            return None
        return start_collector(minutes=minutes)
    except Exception:                                    # noqa: BLE001
        return None


def _terminate(pid: int, timeout_ms: int) -> str:
    """先温和结束（WM_CLOSE），超时再强杀。"""
    if os.name == 'nt':
        return _terminate_win(pid, timeout_ms)
    return _terminate_posix(pid)


def _kill_win_native(pid: int) -> bool:
    """用 Win32 API 结束进程 —— **不起子进程、不碰控制台**（返回是否成功）。

    🔴 **为什么不用 `taskkill`**（2026-10-10 实机事故，两个坑）：
    1. `taskkill` 本身是**控制台程序**。用 `subprocess.run` 调它会**分配一个
       控制台窗口** —— 用户看到的就是「退出时闪一个黑框」。本进程是无窗口的
       （pythonw），子进程默认不继承控制台，于是每调一次就闪一次。
    2. 不带 `/F` 的 `taskkill` 发 WM_CLOSE，对无窗口进程**完全无效** ——
       采集器是 pythonw，没有窗口可关。实测温和版 **3.96 秒**、`/F` 版 **0.73 秒**，
       每次退出都在空等（用户当时反馈「关闭很慢」）。

    `OpenProcess(终止位)` + `TerminateProcess` 是同一件事
    **不经过任何外部程序**：没有控制台、没有窗口、瞬时完成。
    """
    try:
        import ctypes
        from core.memread.redline import TERMINATE_ACCESS_BIT
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(TERMINATE_ACCESS_BIT, False, int(pid))
        if not h:
            return False
        try:
            return bool(k32.TerminateProcess(h, 1))
        finally:
            k32.CloseHandle(h)
    except Exception:                                    # noqa: BLE001
        return False


def _terminate_win(pid: int, timeout_ms: int) -> str:
    """Windows：**直接**结束进程，等它消失。

    走 :func:`_kill_win_native`（Win32 API），**不起 `taskkill` 子进程** ——
    后者是控制台程序，会在退出瞬间闪一个黑框（用户 2026-10-10 实机反馈）。

    强制结束不丢数据：对局数据逐拍落盘、pid 文件与心跳都是「尽力写」；
    万一留下 `state=open` 的记录目录，下次启动的 `import_just_closed` 会收口，
    **不依赖进程优雅退出**。
    """
    if not _kill_win_native(pid):
        # Win32 失败（权限不足 / 进程已没了）—— 再退回 taskkill 兜底
        import subprocess
        try:
            subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                           capture_output=True, timeout=2)
        except Exception:                                # noqa: BLE001
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