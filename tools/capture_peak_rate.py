# -*- coding: utf-8 -*-
"""capture_peak_rate —— **从内存读巅峰决斗积分**（只读，一次性工具）。

读两个落点（见 `core/memread/peak_rate.py` 模块头）：

* **结算对**（`DuelResult.resultInfo.RateDuel.rate`）—— 本局 before/after，
  结算画面后出现、存活到下一局开局；
* **常驻值**（`RateDuel.<赛季>.info`）—— 当前积分 / TOP% / 连胜，
  回菜单才刷新。

用法
----
    python tools/capture_peak_rate.py                # 读一次（两个落点）
    python tools/capture_peak_rate.py --json         # 机器可读一行
    python tools/capture_peak_rate.py --watch        # 常驻，变化才打印（1 秒一拍）
    python tools/capture_peak_rate.py --watch --minutes 30

红线：只读、0x0410、不注入；读的是自己账号屏幕可见的积分。
"""
from __future__ import annotations

import argparse
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
from core.memread.end_message import default_game_dir                # noqa: E402
from core.memread.peak_rate import read_peak_result, read_peak_standing  # noqa: E402

#: 游戏安装目录（要改就改这里；与 tools/capture_my_rank.py 同源）
# ★ BUG-B9A-14：游戏目录不再写死 `D:\steam\...` —— 改走
#   `end_message.default_game_dir()`（认 `MD_GAME_DIR` 环境变量 + 常见盘位探测），
#   换盘 / 换 Steam 库也能用。原来三份工具各抄一份路径。
GAME_DIR = default_game_dir()


def open_proc():
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


def read_once(cw_ti):
    """读两个落点 -> (result, standing, cw_ti)。TypeInfo 找到后一直复用。"""
    if cw_ti is None:
        from core.memread.client_work import find_clientwork
        cw_ti = find_clientwork(proc, mirror, table)
    return (read_peak_result(proc, mirror, table, cw_ti),
            read_peak_standing(proc, mirror, table, cw_ti), cw_ti)


def fmt(rec):
    if not rec:
        return '—'
    s = '%.2f' % rec['rate'] if rec.get('rate') is not None else '—'
    parts = [s]
    if rec.get('distribution') is not None:
        parts.append('TOP%d%%' % rec['distribution'])
    if rec.get('con_win') is not None:
        parts.append('%d连胜' % rec['con_win'])
    if rec.get('season') is not None:
        parts.append('S%d' % rec['season'])
    return ' '.join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description='只读读巅峰决斗积分（内存）')
    ap.add_argument('--watch', action='store_true', help='常驻，变化才打印')
    ap.add_argument('--interval', type=float, default=1.0, help='轮询间隔秒')
    ap.add_argument('--minutes', type=float, default=0, help='常驻多少分钟（0=不限）')
    ap.add_argument('--json', action='store_true', help='JSON 一行输出')
    args = ap.parse_args()

    global proc, mirror, table
    conn = open_proc()
    if conn is None:
        print('游戏没开（masterduel.exe / metadata 找不到）')
        return 1
    proc, mirror, table = conn

    try:
        result, standing, cw_ti = read_once(None)
        if not args.watch:
            if not result and not standing:
                print('读不到巅峰积分（没进过巅峰界面 / 没打过巅峰局？）')
                return 2
            if args.json:
                print(json.dumps({'result': result, 'standing': standing},
                                 ensure_ascii=False))
            else:
                print('本局结算：%s -> %s（赛季 %s）'
                      % (result.get('before') if result else '—',
                         result.get('after') if result else '—',
                         result.get('season') if result else '—'))
                print('当前常驻：%s' % fmt(standing))
            return 0

        print('常驻观察中（每 %.1fs 一拍，Ctrl+C 结束）—— 打一局巅峰看翻转时刻'
              % args.interval)
        last = (result, standing)
        print('[起始] 结算对：%s | 常驻：%s'
              % (fmt({'rate': last[0].get('after')} if last[0] else None),
                 fmt(last[1])))
        t0 = time.time()
        while True:
            time.sleep(max(0.2, args.interval))
            cur = read_once(cw_ti)[:2]
            if cur != last:
                print('[%s] 结算对：%r | 常驻：%r' % (time.strftime('%H:%M:%S'),
                                                     cur[0], cur[1]))
                last = cur
            if args.minutes and (time.time() - t0) > args.minutes * 60:
                print('到时间了，收工')
                return 0
    except KeyboardInterrupt:
        print('\n手动结束')
        return 0
    finally:
        proc.close()


if __name__ == '__main__':
    sys.exit(main())
