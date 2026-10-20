# -*- coding: utf-8 -*-
"""capture_duel_mode —— 常驻只读采样：把每一局的「游戏模式」记下来（M1 内存线 step 1）。

为什么需要它
------------
日志**判不出** `game_mode`（3=排名决斗 / 19=巅峰决斗）：库里 19 局日志对局有 10 局是
NULL，其中 3 局看起来就是天梯，现在被挡在统计口径外。Untapped API 能补，但要求
「这局在它记录里」，而且 API 是**按月手动跑**。

而 `Engine` 实例里的 `gameMode` 字段（偏移 +0xCC）是**屏幕上本来就有的公开信息**，
对局进行中读一次就能确定这局算什么模式。已实测：

* `3` = 排名决斗（2026-09-12 用户当场确认 15:01 那局是天梯）
* `7` = 回放
* 其余取值见 `core/storage.GAME_MODE_NAMES`（15=DUEL_TRIAL、20=RDC…）

红线
----
* 只读、只申请 0x0410 权限、不注入（由 `core/memread/` 保证，单测盯着）
* **只读公开字段**：对局实例是否存在 + `gameMode` / `isOnlineMode`
* 引擎里那个「本局卡牌全集」字段在 `core/memread/redline.py` 的禁区名单里，
  本工具**不碰它**（连字段名都不许出现 —— 静态扫描会红）

收局的判据（2026-09-12 实测）
------------------------------
光看「实例消失」**不够**：对局 15:45:22 就结束了，Engine 实例在结算画面上
又活了 2 分钟以上。真正及时的信号是 **`pvpFinished` 由 0 变 1**（实测对局中是 0、
结算后是 1）。所以：

* `pvpFinished` 变 1 → 立刻收局（记 `end_reason='pvpFinished'`）
* 实例消失     → 也收局（记 `end_reason='instance-gone'`）
* 收局后实例还在（结算画面）→ **不许再开新局**，否则一局会裂成好几条

用法
----
    python tools/capture_duel_mode.py --minutes 30       # 常驻采样（打到哪记到哪）
    python tools/capture_duel_mode.py --list             # 看看记到了什么
    python tools/capture_duel_mode.py --apply            # 写进库（只补 NULL，不覆盖）
    python tools/capture_duel_mode.py --apply --dry-run  # 只打印准备写什么

输出：``data/archive/duel_modes.jsonl``（append-only，一行一局）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import time
from typing import NamedTuple, Optional

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import paths

from core.storage import (GAME_MODE_NAMES, MODE_GROUP_NAMES, Storage,
                          mode_group)                      # noqa: E402
from core.memread.end_message import default_game_dir            # noqa: E402
from core.memread.il2cpp import (                          # noqa: E402
    MetadataHeader, default_metadata_path, find_all_metadata_mirrors,
    find_typeinfo, list_fields, read_singleton_slot, read_string_table)
from core.memread.process import SafeProcess, find_pids_by_name  # noqa: E402


#: 游戏安装目录（`MD_GAME_DIR` 环境变量可覆盖 —— 与 `end_message.default_game_dir()`
#: 同一口径。★ BUG-B8-15：原先写死本机 `D:/steam/...`）
GAME_DIR = default_game_dir()

#: 采样记录落盘位置（append-only）
OUT_PATH = paths.data_path('archive', 'duel_modes.jsonl')

#: 实例里的字段名（**都是公开信息**）
FIELD_MODE = 'gameMode'
FIELD_ONLINE = 'isOnlineMode'
FIELD_FINISHED = 'pvpFinished'


class Sample(NamedTuple):
    """一次采样的结果。`in_duel=None` = 这次没读成（游戏没开/结构没就绪）。"""

    in_duel: Optional[bool]
    game_mode: Optional[int]
    is_online: Optional[int]
    finished: Optional[int]      # `pvpFinished`：1 = 这局已经打完了


def mode_name(value):
    """`game_mode` 数字 -> 人类可读名字（认不出就原样返回）。

    ⚠️ `21` 在 Untapped 枚举里叫 `NULL`（占位名）—— 2026-09-12 实测
    **骰子拉力（活动赛）就是 21**，所以显示成「未命名」，别让人误以为读失败了。
    """
    if value is None:
        return '?'
    name = GAME_MODE_NAMES.get(int(value))
    if not name:
        return '未知(%s)' % value
    if name == 'NULL':
        return '未命名(%s)' % value
    return name


def group_name(value):
    """`game_mode` -> 四分类的中文名（天梯 / 巅峰 / DC 杯 / 活动杯赛）。"""
    grp = mode_group(value)
    return MODE_GROUP_NAMES.get(grp, '判不出') if grp else '判不出'


# ---------------------------------------------------------- 状态机（可单测）


class DuelModeTracker(object):
    """把「一串采样」压成「一局一条记录」。

    纯逻辑、不碰内存也不碰磁盘，方便单测：喂 `observe()`，读 `records`。

    * 实例出现 -> 开一局（记住第一次看到的时刻）
    * 实例一直在 -> 累计采样、收集观察到的模式值
    * 实例消失 -> 收局，返回这条记录
    """

    def __init__(self):
        self.records = []
        self.open = None
        self._closed_by_finish = False

    def observe(self, timestamp, in_duel, game_mode=None, finished=None):
        """喂一次采样。收局时返回记录 dict，否则返回 None。

        `finished=1`（`pvpFinished`）表示这局打完了 —— 结算画面上实例还在，
        所以必须靠这个信号收局，不能等实例消失。
        """
        if in_duel is None:
            return None            # 这一次没读成 —— 不许当成"对局结束"
        if not in_duel:
            self._closed_by_finish = False
            return self._close(timestamp, 'instance-gone')
        if finished:
            rec = self._close(timestamp, 'pvpFinished')
            self._closed_by_finish = True
            return rec
        if self._closed_by_finish:
            return None            # 结算画面：实例还在，但这不是新的一局
        if in_duel:
            if self.open is None:
                self.open = {'started_at': timestamp, 'game_modes': [],
                             'samples': 0}
            self.open['samples'] += 1
            if game_mode is not None and int(game_mode) not in self.open['game_modes']:
                self.open['game_modes'].append(int(game_mode))
            return None
        return None

    def _close(self, timestamp, reason):
        """收局；没有开着的对局就返回 None。"""
        if self.open is None:
            return None
        rec = self.open
        self.open = None
        rec['ended_at'] = timestamp
        rec['end_reason'] = reason
        rec['game_mode'] = self.dominant_mode(rec)
        rec['mode_name'] = mode_name(rec['game_mode'])
        self.records.append(rec)
        return rec

    @staticmethod
    def dominant_mode(rec):
        """一局里观察到的模式值：只认「唯一那个」；出现多个就取最后一个。

        实测同一局不会变模式；真变了说明中间可能隔着别的对局，
        宁可退回最后一个，也不要写成"两个模式都算"。
        """
        modes = rec.get('game_modes') or []
        if not modes:
            return None
        return modes[-1] if len(modes) > 1 else modes[0]

    def flush(self, timestamp):
        """采样结束时把还没收口的对局收掉（游戏被关 / Ctrl+C 时用）。"""
        return self._close(timestamp, 'flush')


# ---------------------------------------------------------------- 记录读写


def load_records(path=OUT_PATH):
    """读回已记录的对局（一行一个 JSON；坏行跳过）。"""
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_record(rec, path=OUT_PATH):
    """追加一条记录（**显式用 \\n**，别让 Windows 写成 CRLF）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'a', encoding='utf-8', newline='') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


# ---------------------------------------------------------------- 内存读取


class DuelModeReader(object):
    """只读打开游戏进程，按需读「实例是否存在 + gameMode」。

    只做两件事：解析结构（进程 -> metadata 镜像 -> Engine TypeInfo -> 字段偏移）、
    按偏移读 int32/byte。**不碰禁区字段。**
    """

    def __init__(self, game_dir=None):
        #: ⚠️ 默认值在**调用时**解析（不是 import 时）——否则进程起来后再设
        #: `MD_GAME_DIR` 就不生效了。
        self.game_dir = game_dir or default_game_dir()
        self._proc = None
        self._pid = None
        self._fields = None
        self._ti = None
        self._mode_off = None
        self._online_off = None
        self._finished_off = None

    def connect(self):
        """找进程 + 解析结构。成功返回 True。"""
        self.close()
        pids = find_pids_by_name('masterduel.exe')
        if not pids:
            return False
        meta = default_metadata_path(self.game_dir)
        proc = None
        try:
            header = MetadataHeader.from_file(meta)
            proc = SafeProcess(pids[0])
            mirrors = find_all_metadata_mirrors(proc, header)
            if not mirrors:
                raise RuntimeError('没找到 metadata 镜像')
            stbl = read_string_table(meta, header)
            tis = find_typeinfo(proc, mirrors[0], stbl, 'YgomGame.Duel', 'Engine')
            if not tis:
                raise RuntimeError('没定位到 Engine')
            fields = {f.name: f for f in list_fields(proc, tis[0],
                                                     mirror=mirrors[0])}
            if FIELD_MODE not in fields:
                raise RuntimeError('字段表里没有 %s' % FIELD_MODE)
        except Exception:
            if proc is not None:
                try:
                    proc.close()
                except Exception:
                    pass
            return False
        self._proc, self._pid, self._fields, self._ti = proc, pids[0], fields, tis[0]
        self._mode_off = fields[FIELD_MODE].offset
        self._online_off = (fields[FIELD_ONLINE].offset
                            if FIELD_ONLINE in fields else None)
        self._finished_off = (fields[FIELD_FINISHED].offset
                              if FIELD_FINISHED in fields else None)
        return True

    def close(self):
        if self._proc is not None:
            try:
                self._proc.close()
            except Exception:
                pass
        self._proc = self._fields = self._ti = None
        self._pid = None

    @property
    def pid(self):
        return self._pid

    def sample(self):
        """返回 `Sample(in_duel, game_mode, is_online, finished)`。

        `in_duel` 为 None 表示**这次没读成**（游戏没开 / 结构没就绪）——
        调用方应当忽略这一拍，而不是当成"对局结束"。
        """
        if self._proc is None:
            return Sample(None, None, None, None)
        try:
            obj, ok = read_singleton_slot(self._proc, self._ti, slot=0x08)
            if not ok:
                #: ★ BUG-B8-15b：「读失败」不能当成「实例消失」 —— 读失败 = 这一拍
                #: 没读成（调用方应忽略），实例消失 = 真的不在对局。
                return Sample(None, None, None, None)
            if not obj:
                return Sample(False, None, None, None)
            blob = self._proc.read(obj, 0x140)
            mode = struct.unpack_from('<i', blob, self._mode_off)[0]
            online = None
            if self._online_off is not None:
                online = blob[self._online_off]
            finished = None
            if self._finished_off is not None:
                finished = struct.unpack_from('<i', blob, self._finished_off)[0]
        except Exception:
            return Sample(None, None, None, None)
        return Sample(True, mode, online, finished)


# ---------------------------------------------------------------- 写库


def merge_records(records, storage, dry_run=False, lookback=30, lookahead=5):
    """把采样记录里的模式值写进库（**只补 NULL，不覆盖**）。

    配对复用 `Storage.find_match_near`（日志行比实际开始早 0~10 秒那套窗口）。
    返回统计 dict。
    """
    stat = {'total': 0, 'applied': 0, 'skipped_has_value': 0,
            'unmatched': 0, 'no_mode': 0, 'detail': []}
    for rec in records:
        stat['total'] += 1
        mode = rec.get('game_mode')
        if mode is None:
            stat['no_mode'] += 1
            continue
        started = rec.get('started_at')
        if not started:
            stat['unmatched'] += 1
            continue
        row = storage.find_match_near(started, lookback=lookback,
                                      lookahead=lookahead)
        if row is None:
            stat['unmatched'] += 1
            stat['detail'].append((started, mode, '库里没有对应对局'))
            continue
        if row.get('game_mode') is not None:
            stat['skipped_has_value'] += 1
            stat['detail'].append((started, mode,
                                   '库内已有 %s，不覆盖' % row['game_mode']))
            continue
        if not dry_run:
            storage.set_game_mode(row['match_id'], mode)
        stat['applied'] += 1
        stat['detail'].append((started, mode, '-> %s (%s)'
                               % (row['started_at'], str(row['match_id'])[:8])))
    return stat


# ---------------------------------------------------------------- CLI


def cmd_watch(args):
    reader = DuelModeReader()
    tracker = DuelModeTracker()
    print('只读采样中：interval=%ss  samples=%s  out=%s'
          % (args.interval, args.samples, OUT_PATH))
    tries = 0
    for i in range(args.samples):
        if reader.pid is None:
            tries += 1
            if not reader.connect():
                if tries == 1 or tries % 12 == 0:
                    print('[%s] 游戏没开 / 结构还没就绪，等一等…'
                          % time.strftime('%H:%M:%S'))
                if i + 1 < args.samples:
                    time.sleep(args.interval)
                continue
            print('[%s] 已连上 pid=%d（只读）'
                  % (time.strftime('%H:%M:%S'), reader.pid))
        sp = reader.sample()
        in_duel, mode, online = sp.in_duel, sp.game_mode, sp.is_online
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        if in_duel is None:
            reader.close()          # 读失败：可能游戏关了/重开，丢连接重来
            if i + 1 < args.samples:
                time.sleep(args.interval)
            continue
        rec = tracker.observe(now, in_duel, mode, sp.finished)
        if args.verbose:
            print('[%s] %s' % (now, ('%s mode=%s(%s) online=%s'
                                     % ('已打完' if sp.finished else '对局中',
                                        mode, mode_name(mode), online))
                               if in_duel else '不在对局'))
        if rec is not None:
            append_record(rec)
            print('[%s] 记下一局：%s -> %s  mode=%s(%s)  采样 %d 次  (%s)'
                  % (now, rec['started_at'], rec['ended_at'],
                     rec['game_mode'], rec['mode_name'], rec['samples'],
                     rec.get('end_reason', '?')))
        if i + 1 < args.samples:
            time.sleep(args.interval)
    rec = tracker.flush(time.strftime('%Y-%m-%d %H:%M:%S'))
    if rec is not None:
        append_record(rec)
        print('收尾记下一局：%s -> %s  mode=%s(%s)'
              % (rec['started_at'], rec['ended_at'], rec['game_mode'],
                 rec['mode_name']))
    reader.close()
    return 0


def cmd_list(args):
    records = load_records()
    if not records:
        print('还没有记录（先跑 --minutes N 采样）')
        return 0
    print('共 %d 条记录：%s' % (len(records), OUT_PATH))
    for rec in records[-args.limit:]:
        print('  %s -> %s  mode=%s(%s) [%s]  采样 %d 次  (%s)'
              % (rec.get('started_at'), rec.get('ended_at'),
                 rec.get('game_mode'), rec.get('mode_name', '?'),
                 group_name(rec.get('game_mode')),
                 rec.get('samples', 0), rec.get('end_reason', '?')))
    return 0


def cmd_apply(args):
    records = load_records()
    if not records:
        print('没有可写库的记录')
        return 0
    storage = Storage(args.db)
    # ★ BUG-B8-18：`--apply` 没跑 `init_schema()` —— 新库上直接
    #   `no such table: matches`。这里补上（幂等，老库只会 ALTER 补列）。
    storage.init_schema()
    stat = merge_records(records, storage, dry_run=args.dry_run)
    for started, mode, note in stat['detail']:
        print('  %s  mode=%s(%s)  %s'
              % (started, mode, mode_name(mode), note))
    print('\n共 %d 条记录：写入 %d，库里已有值 %d，库内没有对应对局 %d，无模式值 %d%s'
          % (stat['total'], stat['applied'], stat['skipped_has_value'],
             stat['unmatched'], stat['no_mode'],
             '（--dry-run，没真写）' if args.dry_run else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='常驻只读采样游戏模式（排名/巅峰/活动…）')
    ap.add_argument('--minutes', type=float, default=0,
                    help='采样多少分钟（默认 0 = 只采一次）')
    ap.add_argument('--samples', type=int, default=0, help='采多少次（优先于 --minutes）')
    ap.add_argument('--interval', type=float, default=5.0, help='采样间隔秒（默认 5）')
    ap.add_argument('--verbose', action='store_true', help='每次都打印一行')
    ap.add_argument('--list', action='store_true', help='打印已记录的对局')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--apply', action='store_true', help='把记录写进库（只补 NULL）')
    ap.add_argument('--dry-run', action='store_true', help='搭配 --apply：只打印')
    ap.add_argument('--db', default=None, help='库路径（默认 data/masterduel.db）')
    args = ap.parse_args(argv)

    if args.list:
        return cmd_list(args)
    if args.apply:
        return cmd_apply(args)
    if args.samples <= 0:
        # ★ BUG-B8-18：`--interval 0` 会 ZeroDivisionError。间隔必须为正；
        #   给了 0/负数就走一个安全默认（5s，与 --interval 的默认值一致），
        #   并在 stderr 说明，而不是抛栈。
        interval = float(args.interval or 0)
        if interval <= 0:
            print('⚠️ --interval 必须 > 0，已按默认 5s 处理', file=sys.stderr)
            interval = 5.0
            args.interval = interval
        args.samples = (int(args.minutes * 60 / interval)
                        if args.minutes > 0 else 1)
    return cmd_watch(args)


if __name__ == '__main__':
    sys.exit(main())
