# -*- coding: utf-8 -*-
"""数据体检（用户 2026-09-23 定）：一键检测三类数据事故。

## 背景

2026-09-23 用户实测 6 局只统计上 2 局、还有整局漏记 —— 都是**打完才发现**的。
本工具把三类事故变成**主动检测**（设置区一键跑）：

| 类 | 判据 | 说明 |
|---|---|---|
| 结果缺失 | 已结束（有 `ended_at`）但 `result` 为 NULL | EM 结算没抓到；窗口只有几秒，**事后只能手动补** |
| **整局漏记** | record 目录存在、含真对局（有 `ShowFirstPlayer`），库里**没有对应行** | 采集器开局了但没建目录/没入库；证据就是磁盘上这个孤儿目录 |
| 未收口 | 目录 `state=open` | 采集器中途被杀；不收口就永远不会入库 |

「残影/空壳目录」（无 `ShowFirstPlayer`）**不算漏记** —— 那是采集器的正常
副产品，报了只会狼来了（实测分拣前后：9 个孤儿 → 6 个真漏记）。

## 用法

::

    python tools/audit_issues.py            # 人看：分组列出 + 一行总结
    python tools/audit_issues.py --json     # 机器可读（给 UI / 后续工具）
    python tools/audit_issues.py --days 7   # 缺结果只看最近 7 天（默认全部）

退出码：**0 = 没问题；1 = 有问题（要人工看）；2 = 体检本身跑不了**。
1 不是失败 —— 与 `build_card_names.py --check-update` 同款语义，
设置页据此标红（`ui/script_page.ATTENTION_CODES`）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

#: 结果缺失只报最近 N 天（再早的欠账列出来也只是噪音；0 = 不限）
DEFAULT_DAYS = 30


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='数据体检：结果缺失 / 整局漏记 / 未收口')
    ap.add_argument('--json', action='store_true', help='输出 JSON（机器可读）')
    ap.add_argument('--days', type=int, default=DEFAULT_DAYS,
                    help='「缺结果」只看最近 N 天（0=不限；默认 %d）' % DEFAULT_DAYS)
    ap.add_argument('--records-dir', default=None,
                    help='record 目录根（默认 data/archive/duel_records）')
    args = ap.parse_args(argv)

    from core import ui_state
    from core.storage import Storage
    try:
        issues = ui_state.audit_issues(storage=Storage(),
                                       records_dir=args.records_dir)
    except Exception as e:                                   # noqa: BLE001
        print('体检跑不了：%r' % e)
        return 2

    if args.json:
        print(json.dumps(issues, ensure_ascii=False, indent=1))
    else:
        # 缺结果按 --days 过滤（只影响展示与计数；孤儿/open 无时间概念）
        import datetime
        cutoff = None
        if args.days and args.days > 0:
            cutoff = (datetime.datetime.now()
                      - datetime.timedelta(days=args.days)
                      ).strftime('%Y-%m-%d %H:%M:%S')
        mr = [x for x in issues['missing_result']
              if not cutoff or str(x.get('started_at') or '') >= cutoff]

        if mr:
            print('◆ 结果缺失（EM 结算没抓到，需手动补；最近 %s 天）' % (args.days or '全部'))
            for x in mr:
                print('   %s  vs %s  (%s)' % (x.get('started_at'),
                                              x.get('opp_deck') or '未知卡组',
                                              x.get('dir') or '无目录'))
            print()
        if issues['orphan_dirs']:
            print('◆ 整局漏记（磁盘上有对局、库里没有 —— 跑「刷新数据」可补入部分）')
            for x in issues['orphan_dirs']:
                print('   %s（%d 条事件，state=%s）' % (x['dir'], x['n_entries'],
                                                       x.get('state')))
            print()
        if issues['open_dirs']:
            print('◆ 未收口（采集器中途被杀；跑「刷新数据」或 --finalize-stale）')
            for x in issues['open_dirs']:
                print('   %s（%d 条事件）' % (x['dir'], x['n_entries']))
            print()

    total = issues['n_issues']
    print(ui_state.audit_issues_line(issues))
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
