# -*- coding: utf-8 -*-
"""诊断包（debug bundle）—— 「导出软件日志方便 debug，数据尽量详细」（2026-10-24）。

## 为什么需要它
用户报问题时，最有价值的证据散落各处且**根本到不了开发者手里**：

* 安装版是 ``pythonw`` 起进程（无控制台，``sys.stdout`` 是 None）——
  界面进程里所有 ``print`` 诊断**全部丢掉**（BUG-B5-04 只是把弹窗改成
  QMessageBox，print 那条线从来没修过）；
* 采集器日志 6.7MB、段位审计 32MB —— 用户不知道该拷哪个、也不会压缩；
* 环境（版本 / 路径 / 探测结果 / 设置 / 库概况）每次都要口头问一遍。

## 口径
* **UI 日志**（:func:`install_ui_log`）：把 stdout/stderr 包装成
  「有终端就 tee、没有（pythonw）就纯写文件」，按**启动轮转**
  （``ui-YYYYMMDD-HHMMSS.log``，保留最近 :data:`UI_LOG_KEEP` 份）。
  在 ``MasterDuelUiApp.__init__`` 最前面装一次（幂等）。
* **诊断包**（:func:`make_bundle`）= 一个 zip：
  - ``env.json`` —— 环境快照（版本 / 平台 / 路径 / 环境自检 / 采集器
    状态 / QSettings 全部键值 / 库概况 / 最近对局概要），全在
    :func:`collect_env`，**任何一项失败都不炸**（失败项写成 error 字符串）；
  - ``logs/…`` —— 各日志**尾部**（大文件截断，截断注记进 ``env.json``；
    小文件全量）。默认上限见 :data:`TAIL_LIMITS`。

  日志**只带尾部**是与「全量备份」（core/backup.py，带走整个数据目录）
  的分工：诊断要的是「刚刚发生了什么」，不是全部历史。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from typing import Dict, Optional

from core import paths

#: UI 日志保留份数（按启动轮转；旧的开机清）
UI_LOG_KEEP = 5

#: 各日志进诊断包的**尾部字节上限**（0 = 全量）。key = 文件名。
TAIL_LIMITS: Dict[str, int] = {
    'duel_collector.log': 1_048_576,        # 1 MiB（全量可到 6.7MB）
    'rank_state_audit.log': 524_288,        # 512 KiB（实测 32MB）
    'end_messages.jsonl': 262_144,          # 256 KiB
    'my_rank.jsonl': 262_144,
    'result_states.jsonl': 262_144,
}

#: 无上限说明的小文件（存在就全量带）
SMALL_FILES = ('first_run.log', 'ui_collector_ctl.log', 'ui_selector.log',
               'duel_modes.jsonl', 'collector_heartbeat.json',
               'tail_state.json')


# ---------------------------------------------------------------- UI 日志

class _Tee:
    """stdout/stderr 的 tee —— 终端 + 文件（终端不存在就只写文件）。"""

    def __init__(self, orig, fh):
        self._orig = orig
        self._fh = fh

    def write(self, s):
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:                              # noqa: BLE001
            pass
        if self._orig is not None:
            try:
                self._orig.write(s)
            except Exception:                          # noqa: BLE001
                pass
        return len(s)

    def flush(self):
        try:
            self._fh.flush()
        except Exception:                              # noqa: BLE001
            pass
        if self._orig is not None:
            try:
                self._orig.flush()
            except Exception:                          # noqa: BLE001
                pass

    def isatty(self):
        return False

    #: pythonw 下 print 可能探测 fileno —— 给个不炸的答案
    def fileno(self):
        if self._orig is not None:
            try:
                return self._orig.fileno()
            except Exception:                          # noqa: BLE001
                pass
        raise io.UnsupportedOperation('fileno')


_UI_LOG_INSTALLED = False


def ui_log_path() -> Optional[str]:
    """当前 UI 日志路径（没装 / 装过但文件没了都返回 None）。"""
    return getattr(ui_log_path, '_current', None)


def ui_log_dir() -> str:
    """UI 日志目录（``DATA_DIR/archive/ui_logs``）。"""
    return paths.data_path('archive', 'ui_logs')


def install_ui_log() -> Optional[str]:
    """装 UI 日志（幂等）—— ``MasterDuelUiApp`` 构造最前面调。

    * 按启动轮转：``ui-YYYYMMDD-HHMMSS.log``，保留最近 :data:`UI_LOG_KEEP` 份；
    * 终端存在就 tee（开发机 ``tools/duel_ui.py`` 照常看得到），
      pythonw（无 stdout）就纯写文件 —— **安装版的 print 从此不丢**；
    * 首行写启动横幅（时间 / pid / 版本 / 平台）。
    """
    global _UI_LOG_INSTALLED
    if _UI_LOG_INSTALLED:
        return ui_log_path()
    try:
        d = ui_log_dir()
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        p = os.path.join(d, 'ui-%s.log' % stamp)
        n = 0
        while os.path.exists(p) and n < 60:            # 同秒双启：避撞名
            n += 1
            p = os.path.join(d, 'ui-%s-%d.log' % (stamp, n))
        fh = open(p, 'a', encoding='utf-8', buffering=1)
        fh.write('---- UI start %s  pid=%d  ver=%s  %s ----\n'
                 % (time.strftime('%Y-%m-%d %H:%M:%S'), os.getpid(),
                    _app_version(), sys.platform))
        sys.stdout = _Tee(sys.stdout, fh)              # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, fh)              # type: ignore[assignment]
        ui_log_path._current = p                        # type: ignore[attr-defined]
        _rotate(d)
        _UI_LOG_INSTALLED = True
        return p
    except Exception:                                  # noqa: BLE001
        return None                                    # 日志系统绝不能炸主程序


def _rotate(d: str) -> None:
    """只留最近 :data:`UI_LOG_KEEP` 份 UI 日志。"""
    try:
        logs = sorted(x for x in os.listdir(d) if x.startswith('ui-')
                      and x.endswith('.log'))
        for old in logs[:-UI_LOG_KEEP]:
            os.remove(os.path.join(d, old))
    except OSError:
        pass


# ---------------------------------------------------------------- 环境快照

def _safe(fn, label):
    """跑一项收集 —— 失败不炸，返回 error 字符串（详细 > 全有）。"""
    try:
        return fn()
    except Exception as e:                             # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}


def _app_version() -> str:
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'core', 'version.py')
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                if line.startswith('__version__'):
                    return line.split('=', 1)[1].strip().strip(chr(39) + chr(34))
    except OSError:
        pass
    return ''


def _tail_bytes(path: str, limit: int) -> tuple:
    """读文件尾部 —— 返回 ``(数据, 是否截断)``。不存在返回 ``(None, False)``。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, False
    try:
        with open(path, 'rb') as f:
            if limit and size > limit:
                f.seek(-limit, os.SEEK_END)
                return f.read(), True
            return open(path, 'rb').read(), False
    except OSError:
        return None, False


def collect_env(storage=None) -> Dict[str, object]:
    """环境快照 —— 每一项独立兜底，**绝不抛**（诊断不能成为新故障源）。"""
    env: Dict[str, object] = {}
    env['generated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    env['app_version'] = _app_version()
    env['python'] = '%d.%d.%d %s' % sys.version_info[:4]
    env['platform'] = sys.platform

    def _qt():
        import PySide6
        from PySide6.QtCore import qVersion
        return {'pyside6': PySide6.__version__, 'qt': qVersion()}
    env['qt'] = _safe(_qt, 'qt')

    def _paths():
        out = {'data_dir': paths.DATA_DIR, 'asset_dir': paths.ASSET_DIR,
               'portable': paths.is_portable()}
        for label, fn in (('paths_ini', paths.read_pref),):
            out[label] = _safe(fn, label)
        try:
            from core.memread import end_message
            out['game_dir'] = end_message.default_game_dir()
        except Exception as e:                         # noqa: BLE001
            out['game_dir'] = 'error: %r' % (e,)
        return out
    env['paths'] = _safe(_paths, 'paths')

    def _env_check():
        from core import ui_state
        return ui_state.env_check(storage=storage)
    env['env_check'] = _safe(_env_check, 'env_check')

    def _collector():
        from core import ui_state
        out = dict(ui_state.collector_status())
        p = paths.data_path('archive', 'duel_collector.log')
        out['log_size'] = os.path.getsize(p) if os.path.exists(p) else 0
        return out
    env['collector'] = _safe(_collector, 'collector')

    def _settings():
        from PySide6.QtCore import QSettings
        s = QSettings('masterduel', 'M7')
        return {k: str(s.value(k, '')) for k in sorted(s.allKeys())}
    env['settings'] = _safe(_settings, 'settings')

    def _db(storage):
        st = storage
        if st is None:
            from core.storage import Storage
            st = Storage()
        out = {}
        rows = _safe(lambda: (st.get_recent_matches(limit=100000) or []),
                     'recent_rows')
        if isinstance(rows, list):
            out['matches_total'] = len(rows)
            out['first_at'] = rows[-1].get('started_at') if rows else None
            out['last_at'] = rows[0].get('started_at') if rows else None
            out['recent'] = [
                {'started_at': m.get('started_at'), 'result': m.get('result'),
                 'mode': m.get('game_mode'), 'my': m.get('my_deck_name'),
                 'opp': m.get('opp_deck_name'),
                 'match_id': m.get('match_id')}
                for m in rows[:15]]
        else:
            out['matches_total'] = 'error'
        out['meta_decks'] = _safe(
            lambda: len(st.list_meta_decks(include_hidden=True) or []), 'meta')
        out['opp_decks'] = _safe(
            lambda: len(st.list_opp_decks() or []), 'opp')
        return out
    env['db'] = _safe(lambda: _db(storage), 'db')

    def _refs():
        p = paths.resolve('mdm_deck_cards.json')
        out = {'path': p, 'exists': os.path.exists(p)}
        if out['exists']:
            try:
                with open(p, encoding='utf-8') as f:
                    d = json.load(f)
                out['fetched_at'] = d.get('fetched_at')
                out['decks'] = len(d.get('decks') or {})
            except (OSError, ValueError) as e:
                out['error'] = repr(e)
        return out
    env['reference'] = _safe(_refs, 'reference')

    def _log_sizes():
        base = paths.data_path('archive')
        out = {}
        for name in os.listdir(base) if os.path.isdir(base) else []:
            p = os.path.join(base, name)
            if os.path.isfile(p):
                out[name] = os.path.getsize(p)
        d = ui_log_dir()
        if os.path.isdir(d):
            out['ui_logs'] = sorted(os.listdir(d))
        return out
    env['archive_files'] = _safe(_log_sizes, 'archive_files')
    return env


# ---------------------------------------------------------------- 诊断包

def make_bundle(dest: str, storage=None) -> Dict[str, object]:
    """生成诊断包 zip → 报告。内容见模块头「口径」。"""
    env = collect_env(storage=storage)
    truncated = {}
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or '.', exist_ok=True)
    n = 0
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('env.json', json.dumps(env, ensure_ascii=False, indent=2,
                                          default=str))
        base = paths.data_path('archive')
        entries = list(TAIL_LIMITS.items()) + [(n, 0) for n in SMALL_FILES]
        for name, limit in entries:
            data, cut = _tail_bytes(os.path.join(base, name), limit)
            if data is None:
                continue
            if cut:
                truncated['logs/' + name] = len(data)
            z.writestr('logs/' + name, data)
            n += 1
        d = ui_log_dir()
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if not name.endswith('.log'):
                    continue
                data, cut = _tail_bytes(os.path.join(d, name), TAIL_LIMITS.get(
                    name, 1_048_576))
                if data is not None:
                    if cut:
                        truncated['logs/ui_logs/' + name] = len(data)
                    z.writestr('logs/ui_logs/' + name, data)
                    n += 1
        if truncated:
            z.writestr('TRUNCATED.json', json.dumps(
                {k: 'tail %d bytes (full file is in the data folder)' % v
                 for k, v in truncated.items()}, ensure_ascii=False, indent=2))
    return {'env': True, 'log_files': n, 'truncated': sorted(truncated),
            'bytes': os.path.getsize(dest), 'dest': dest}

