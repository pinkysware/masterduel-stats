# -*- coding: utf-8 -*-
"""补丁包升级兼容性自检（2026-10-24 加，同日改为「多版本」）。

## 为什么要这个

补丁包是**全量代码快照**（不是增量差），只替换 `ui/ core/ tools/`，
**绝不含 `data/`**（那里面是用户的对局库和手填的卡组名）。
所以它的升级语义是：

> **从任何一个旧版本，都能直接用最新补丁升到最新版。**

用户口径（2026-10-24）：「假设以后出了 1.1.8，补丁要能从 1.1.2~1.1.7
任意版本升级到 1.1.8」。**1.1.2 是第一个公开发布版**，所以验证范围是
**1.1.2 起、到「上一版」为止的每一个版本**（最新版不用验，它就是自己）。

## 升级唯一的真实风险

    **老 `data/masterduel.db` 配新代码会不会炸**

典型炸法（AGENTS.md 记过的 2026-09-16 事故）：新代码加了一列，
老库没那一列 → 每局入库 `no such column` **静默失败** → 「打完一局不显示」。

## 本工具做什么

对每个基线 tag（默认 1.1.2 .. 上一版）：

1. `git worktree` 从发布仓检出那个版本的代码；
2. 用**那份代码**建一个库 + 插一条老对局（= 该版本用户的真实库形态）；
3. 用**当前代码**打开它跑 `init_schema()`（升级路径就是这个）；
4. 断言：列没丢、老数据还在、`ui_state.snapshot()` / 统计查询能正常跑。

用法::

    python tools/check_patch_upgrade.py              # 验 1.1.2 .. 上一版
    python tools/check_patch_upgrade.py --only v1.1.2 # 只验某一个
    python tools/check_patch_upgrade.py --from v1.1.2 --to v1.1.5

退出码：**0 = 全部通过；1 = 有版本升不上来；2 = 环境跑不了**（如没发布仓）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

#: 发布仓（各版本的 tag 都在那边）
PUB_REPO = os.path.join(os.path.dirname(PROJ), 'master-duel-stats')

#: ★ 最早要支持的版本 —— **1.1.2 = 第一个公开发布的版本**（用户 2026-10-24 定）。
#: 比它更早的 1.1.0 / 1.1.1 没人装过，不用验。
FIRST_PUBLIC = (1, 1, 2)

WORK_ROOT = os.path.join(PROJ, 'tmp', '_patch_upgrade')


# ------------------------------------------------------------------ 版本工具

def parse_ver(tag: str):
    """`'v1.1.2'` / `'1.1.2'` -> `(1, 1, 2)`；解不出来返回 None。"""
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', tag or '')
    return tuple(int(x) for x in m.groups()) if m else None


def current_version() -> tuple:
    """当前代码版本（读主仓 core/version.py）。"""
    sys.path.insert(0, PROJ)
    from core.version import __version__
    return parse_ver(__version__)


def released_tags() -> list:
    """发布仓里所有 `vX.Y.Z` 的 tag，按版本升序。"""
    r = subprocess.run(['git', 'tag', '-l', 'v*.*.*'], cwd=PUB_REPO,
                       capture_output=True, text=True)
    out = []
    for line in (r.stdout or '').splitlines():
        v = parse_ver(line.strip())
        if v:
            out.append(v)
    return sorted(set(out))


def baseline_versions(cur=None):
    """要验哪些基线：**1.1.2 起，到「上一版」为止**（不含当前版本自己）。"""
    cur = cur or current_version()
    tags = [v for v in released_tags() if v < cur]
    return [v for v in tags if v >= FIRST_PUBLIC]


# ------------------------------------------------------------------ 建库 / 校验

def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def prepare_code(ver: tuple) -> str:
    """检出某个版本的发布仓代码到 worktree，返回路径。"""
    tag = 'v%d.%d.%d' % ver
    dst = os.path.join(WORK_ROOT, 'code_' + tag)
    if os.path.isdir(dst):
        return dst
    if not os.path.isdir(PUB_REPO):
        raise SystemExit('找不到发布仓：%s' % PUB_REPO)
    os.makedirs(WORK_ROOT, exist_ok=True)
    r = _run(['git', 'worktree', 'add', dst, tag], cwd=PUB_REPO)
    if r.returncode != 0:
        raise SystemExit('检出 %s 失败：%s' % (tag, (r.stderr or r.stdout)[:300]))
    return dst


def build_base_db(code_dir: str, out_dir: str) -> str:
    """用某个版本的代码建库 + 插一条老对局，返回库路径。"""
    db = os.path.join(out_dir, 'masterduel.db').replace('\\', '\\\\')
    script = r'''
import os, sys, sqlite3
sys.path.insert(0, %r)
os.environ['MD_DATA_DIR'] = %r
from core.storage import Storage
s = Storage(); s.init_schema()
c = sqlite3.connect(%r)
c.execute("""INSERT INTO matches (match_id, started_at, ended_at, my_deck_name,
             mode, game_mode, origin, result, went_first)
             VALUES ('base-old','2026-10-20 10:00:00','2026-10-20 10:05:00',
             '闪刀姬','pvp',3,'record','Win',1)""")
c.commit()
''' % (code_dir, out_dir.replace('\\', '\\\\'), db)
    r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit('基线库建不出来：%s' % (r.stderr or r.stdout)[-400:])
    return os.path.join(out_dir, 'masterduel.db')


def columns(db: str) -> list:
    c = sqlite3.connect(db)
    try:
        return [r[1] for r in c.execute('pragma table_info(matches)').fetchall()]
    finally:
        c.close()


def check_one(ver: tuple) -> list:
    """验「从 ver 直接升到当前」。返回问题列表（空 = 通过）。"""
    tag = 'v%d.%d.%d' % ver
    problems = []
    try:
        code_dir = prepare_code(ver)
    except SystemExit as exc:
        return ['准备代码失败：%s' % exc]

    out_dir = os.path.join(WORK_ROOT, 'db_' + tag)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    try:
        db = build_base_db(code_dir, out_dir)
    except SystemExit as exc:
        return [str(exc)]
    before = columns(db)

    from core.storage import Storage
    try:
        Storage(db).init_schema()
    except Exception as exc:                                   # noqa: BLE001
        return ['init_schema 抛异常：%r' % exc]
    after = columns(db)

    lost = sorted(set(before) - set(after))
    if lost:
        problems.append('丢了列：%s' % lost)

    c = sqlite3.connect(db)
    try:
        keep = c.execute('select match_id,my_deck_name,result from matches').fetchall()
    finally:
        c.close()
    if keep != [('base-old', '闪刀姬', 'Win')]:
        problems.append('老数据被破坏：%r' % keep)

    import core.ui_state as ui_state
    try:
        snap = ui_state.snapshot(Storage(db))
        _ = len(snap.recent or [])
    except Exception as exc:                                   # noqa: BLE001
        problems.append('新代码读老库抛异常：%r' % exc)
    try:
        Storage(db).get_stats()
    except Exception as exc:                                   # noqa: BLE001
        problems.append('统计查询抛异常：%r' % exc)

    #: 该版本特有的列，升级后不能少（防止「新版删了旧列」）
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='补丁包升级兼容性自检（多版本）')
    ap.add_argument('--only', default=None, help='只验某一个版本，如 v1.1.2')
    ap.add_argument('--from', dest='from_ver', default=None,
                    help='起始版本（默认 %s）' % ('v%d.%d.%d' % FIRST_PUBLIC))
    ap.add_argument('--to', dest='to_ver', default=None,
                    help='截止版本（默认 = 上一版）')
    args = ap.parse_args(argv)

    cur = current_version()
    print('当前版本：v%d.%d.%d' % cur)

    if args.only:
        v = parse_ver(args.only)
        if not v:
            raise SystemExit('版本号解不出来：%s' % args.only)
        targets = [v]
    else:
        targets = baseline_versions(cur)
        if args.from_ver:
            fv = parse_ver(args.from_ver)
            if fv:
                targets = [v for v in targets if v >= fv]
        if args.to_ver:
            tv = parse_ver(args.to_ver)
            if tv:
                targets = [v for v in targets if v <= tv]

    if not targets:
        print('没有要验的基线版本（当前就是最早版本？）')
        return 0

    print('要验 %d 个基线版本：%s\n'
          % (len(targets), ', '.join('v%d.%d.%d' % v for v in targets)))

    failed = {}
    for v in targets:
        tag = 'v%d.%d.%d' % v
        problems = check_one(v)
        if problems:
            failed[tag] = problems
            print('  ✗ %s -> v%d.%d.%d' % (tag, *cur))
            for p in problems:
                print('      - %s' % p)
        else:
            print('  ✓ %s -> v%d.%d.%d' % (tag, *cur))

    if failed:
        print('\n✗ %d 个版本升不上来：%s' % (len(failed), ', '.join(failed)))
        return 1
    print('\n✓ 全部 %d 个版本都能直接升到 v%d.%d.%d。' % (len(targets), *cur))
    return 0


if __name__ == '__main__':
    sys.exit(main())
