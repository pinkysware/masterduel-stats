# -*- coding: utf-8 -*-
"""M7 悬浮窗入口 —— ``python tools/duel_ui.py``。

放在 ``tools/`` 而不是仓库根：既和既有 CLI 一致，也让它落在
`core/memread/redline.py` 的静态扫描面里（工具脚本也是业务代码）。

用法::

    python tools/duel_ui.py                  # 常显悬浮窗（读真库）
    python tools/duel_ui.py --demo           # ★ 做 UI 用 demo 战绩（不碰真库）
    python tools/duel_ui.py --db tmp/x.db    # 指定库（造假数据试界面用）
    python tools/duel_ui.py --interval 1000  # 探针间隔（毫秒，默认 2000）

⚠️ ``--demo`` 的数据来自 :mod:`core.ui_demo`（9 行覆盖全形态：升段/掉段/
跨段/巅峰/未标注/缺图/长名字…）。**做 UI 一律先拿它画** —— 真库最近三局
没段位数据、多数未标注，拿真库迭代会被数据形态带偏（2026-09-15 用户定）。
"""
from __future__ import annotations

import argparse
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)


def run_demo() -> int:
    """demo 模式 = 完整应用 + demo 数据源（设置里可随时热切回真库）。

    2026-09-15 起不再是独立简装路径：设置里加了「调试模式」开关，
    两边汇成同一条实现（ui.app.MasterDuelUiApp，demo=True 不建 Storage）。
    """
    try:
        from ui.app import main as run_ui
    except ImportError as exc:                 # 没装 GUI 依赖时说人话
        print('启动失败：%s' % exc)
        print('GUI 依赖没装。装一次即可：')
        print('  %s -m pip install PySide6' % sys.executable)
        return 2
    return run_ui(demo=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='M7 悬浮窗（只读库，不碰内存）')
    ap.add_argument('--demo', action='store_true',
                    help='吃 core/ui_demo.py 的 demo 战绩（做 UI 用，不碰真库）')
    ap.add_argument('--db', help='指定 SQLite（默认 data/masterduel.db）')
    ap.add_argument('--interval', type=int, default=2000,
                    help='便宜探针间隔，毫秒（默认 2000）')
    args = ap.parse_args(argv)

    if args.demo:
        return run_demo()

    try:
        from ui.app import main as run_ui
    except ImportError as exc:                 # 没装 GUI 依赖时说人话
        print('启动失败：%s' % exc)
        print('GUI 依赖没装。装一次即可：')
        print('  %s -m pip install PySide6' % sys.executable)
        return 2
    return run_ui(db_path=args.db, interval_ms=args.interval)


if __name__ == '__main__':
    sys.exit(main())
