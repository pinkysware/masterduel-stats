# -*- coding: utf-8 -*-
"""capture_my_rank —— **从内存读自己账号的段位**（只读，M-段位线）。

为什么需要它
------------
用户 2026-09-15 拍板：**段位不能靠 Untapped API**（库里 25/54 局的段位全来自
`friendly_rank_before`），必须由本项目自己的只读内存通路拿。
2026-09-15 深夜**锚点找到了**：`ClientWork.s_data["User"]["profile"]` 里直接挂着
`rank`（大段 1..7）/ `rate`（小段 1..5）—— 主界面就有效，走确定性指针链、不靠扫。
详见 `core/memread/rank.py` 的模块头（含「按值域扫」被否掉的证据）。

这个工具干两件事
----------------
1. **一次性读**：现在段位是多少（`--json` 给出机器可读的一行）；
2. **常驻看变化**（`--watch`）：每隔 N 秒读一次，**只在变化时打印**（带墙钟时刻）。

★ 第 2 件是为了回答那个还没验的问题：**`profile` 什么时候刷新** ——
「结算那一刻」就变，还是「回到主界面 / 下次同步」才变。
打一局天梯，把 `--watch` 挂着，就能看到翻转发生在哪个时刻。
（这决定采集器能不能在 `pvpFinished 0→1` 当场读到的就是局后值。）

红线
----
* 只读、只申请 0x0410 权限、不注入、不调用游戏函数（由 `core/memread/` 保证）
* 读的是**自己账号的公开资料**（游戏界面上就显示的那些字段：名字/段位/等级）
* 不动对手任何信息

用法
----
    python tools/capture_my_rank.py                     # 读一次
    python tools/capture_my_rank.py --json              # 读一次（JSON 一行）
    python tools/capture_my_rank.py --watch             # 常驻，变化才打印（默认 2 秒）
    python tools/capture_my_rank.py --watch --interval 1 --minutes 30
    python tools/capture_my_rank.py --watch --log data/archive/my_rank.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.memread.il2cpp import (MetadataHeader, default_metadata_path,   # noqa: E402
                                 find_all_metadata_mirrors, read_string_table)
from core.memread.process import SafeProcess, find_pids_by_name          # noqa: E402
from core.memread.rank import read_profile, read_my_rank                 # noqa: E402
from core.ranks import numeral_text, rank_label                          # noqa: E402

#: 游戏安装目录（要改就改这里）
GAME_DIR = r'D:\steam\steamapps\common\Yu-Gi-Oh!  Master Duel'


def label_of(rec) -> str:
    """`{'rank':5,'rate':2}` -> `'Platinum 2'`；认不出给 `'—'`。

    口径与库里的 `my_rank_before`/`my_rate_before` 显示方式一致（`rank_label`），
    罗马数字只在 `--json` 里额外给一个 `numeral` 字段（对屏幕上的 II 用）。
    """
    if not rec:
        return '—'
    return rank_label(rec.get('rank'), rec.get('rate')) or '—'


def open_proc():
    """连上游戏进程 -> (proc, mirror, table)；没开游戏给 None。"""
    pids = list(find_pids_by_name('masterduel.exe'))
    if not pids:
        return None
    proc = SafeProcess(pids[0])
    meta = default_metadata_path(GAME_DIR)
    if not os.path.isfile(meta):
        proc.close()
        return None
    header = MetadataHeader.from_file(meta)
    table = read_string_table(meta, header)
    mirrors = find_all_metadata_mirrors(proc, header)
    if not mirrors:
        proc.close()
        return None
    return proc, mirrors[0], table


def main() -> int:
    ap = argparse.ArgumentParser(description='只读读自己账号的段位（内存）')
    ap.add_argument('--watch', action='store_true', help='常驻，变化才打印')
    ap.add_argument('--interval', type=float, default=2.0, help='轮询间隔秒（默认 2）')
    ap.add_argument('--minutes', type=float, default=0, help='常驻多少分钟（0=不限）')
    ap.add_argument('--json', action='store_true', help='按 JSON 一行输出')
    ap.add_argument('--log', help='把每次变化追加到这个 jsonl（只读采集，不动真库）')
    args = ap.parse_args()

    conn = open_proc()
    if conn is None:
        print('游戏没开（masterduel.exe / metadata 找不到）')
        return 1
    proc, mirror, table = conn

    def read_once(cw_ti):
        """读一次 -> (rec, cw_ti)。TypeInfo 第一次找到后一直复用（省全内存扫描）。"""
        if cw_ti is None:
            from core.memread.client_work import find_clientwork
            cw_ti = find_clientwork(proc, mirror, table)
        return read_my_rank(proc, mirror, table, cw_ti), cw_ti

    def emit(rec, extra=None, prefix=''):
        row = dict(rec or {})
        row['label'] = label_of(rec)
        if rec:
            row['numeral'] = numeral_text(rec.get('rate'))
        if extra:
            row.update(extra)
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
        else:
            print('%s%s  %s  (rank=%s rate=%s level=%s exp=%s/%s name=%s)'
                  % (prefix, time.strftime('%H:%M:%S'), row['label'],
                     row.get('rank'), row.get('rate'), row.get('level'),
                     row.get('exp'), row.get('need_exp'), row.get('name')))
        if args.log:
            row['at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            with io.open(os.path.join(PROJ, args.log) if not os.path.isabs(args.log)
                         else args.log, 'a', encoding='utf-8', newline='') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    try:
        rec, cw_ti = read_once(None)
        if not args.watch:
            if rec is None:
                print('读不到段位（profile 里没有 rank/rate —— 没打过排位？）')
                return 2
            emit(rec)
            return 0

        # ---- 常驻：变化才打印 ----
        print('常驻观察中（每 %.1fs 一次，Ctrl+C 结束）—— 只读，不打日志、不写库'
              % args.interval)
        emit(rec, prefix='[起始] ')
        last = rec
        t0 = time.time()
        while True:
            time.sleep(max(0.2, args.interval))
            cur, cw_ti = read_once(cw_ti)
            if cur != (last or None):
                emit(cur, prefix='[变化] ', extra={'was': last})
                last = cur
            if args.minutes and (time.time() - t0) > args.minutes * 60:
                print('到时间了（--minutes %.1f），收工' % args.minutes)
                return 0
    except KeyboardInterrupt:
        print('\n手动结束')
        return 0
    finally:
        proc.close()


if __name__ == '__main__':
    sys.exit(main())
