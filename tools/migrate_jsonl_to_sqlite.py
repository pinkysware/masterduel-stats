# -*- coding: utf-8 -*-
"""M2 迁移脚本：data/archive/matches.jsonl -> data/masterduel.db

**幂等**：可以反复运行 —— 不会重复插入，也不会丢人工标注。
迁移**不改动**源 jsonl，只是把内容复制进 SQLite。

用法::

    python tools/migrate_jsonl_to_sqlite.py                  # 迁移 + 自动对账
    python tools/migrate_jsonl_to_sqlite.py --dry-run        # 只看要做什么
    python tools/migrate_jsonl_to_sqlite.py --verify         # 只对账，不写库
    python tools/migrate_jsonl_to_sqlite.py --jsonl <path>   # 指定源文件
    python tools/migrate_jsonl_to_sqlite.py --db <path>      # 指定目标库
    python tools/migrate_jsonl_to_sqlite.py --decks <path>   # 指定 decks.json

对账口径：jsonl 里出现过的每一个字段，都要与从库里读回来的值**完全相等**
（布尔 True/False、null、JSON 数组都算）。任何差异都会逐条打印，
并且进程以退出码 1 结束。
"""

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import Storage, MATCH_FIELDS  # noqa: E402

DEFAULT_JSONL = os.path.join(PROJ, 'data', 'archive', 'matches.jsonl')
#: M2 完成后 jsonl 会改名为这个（备份，退出代码路径）
BACKUP_JSONL = DEFAULT_JSONL + '.v1.bak'


def resolve_jsonl(path=None):
    """定位迁移源：优先 matches.jsonl，其次 v1 备份 matches.jsonl.v1.bak。

    M2 之后源文件已改名为 .v1.bak，但"从备份重迁一次"仍然要能用，
    所以这里做回退。显式传了 path 就以 path 为准。
    """
    if path:
        return path
    for cand in (DEFAULT_JSONL, BACKUP_JSONL):
        if os.path.exists(cand):
            return cand
    return DEFAULT_JSONL


# ------------------------------------------------------------------ 读源
def load_jsonl(path):
    """读 jsonl，返回 [dict]。空行跳过，坏行跳过（并警告）。"""
    rows = []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as e:
                print('[警告] 第 %d 行不是合法 JSON，已跳过：%s' % (i, e))
    return rows


def find_decks_json():
    """定位 untapped 的 decks.json。

    ⚠️ 坑：Git Bash 里 `%APPDATA%` 可能是空串，所以逐级回退
       APPDATA -> USERPROFILE\\AppData\\Roaming -> ~
    """
    cands = []
    appdata = os.environ.get('APPDATA')
    if appdata:
        cands.append(os.path.join(appdata, 'untapped-companion', 'ygom',
                                  'decks.json'))
    up = os.environ.get('USERPROFILE')
    if up:
        cands.append(os.path.join(up, 'AppData', 'Roaming', 'untapped-companion',
                                  'ygom', 'decks.json'))
    cands.append(os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming',
                              'untapped-companion', 'ygom', 'decks.json'))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def load_decks(path):
    """读 decks.json -> [(deck_id, name)]。格式不认识就返回空表。"""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print('[卡组] 读取失败：%s（%s）' % (path, e))
        return []

    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                out.append((str(k), v.get('name') or str(k)))
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, dict):
                did = v.get('deck_id') or v.get('id')
                if did:
                    out.append((str(did), v.get('name') or str(did)))
    return out


# ------------------------------------------------------------------ 迁移
def migrate(jsonl_path, storage, decks_path=None, dry_run=False, log=print):
    """把 jsonl 灌进 SQLite。返回统计数据 dict。"""
    rows = load_jsonl(jsonl_path)
    storage.init_schema()
    decks = load_decks(decks_path) if decks_path else []
    log('[卡组] my_decks 登记 %d 套%s'
        % (len(decks),
           '' if decks_path else '（未找到 decks.json，缺失的卡组名由对局自动补占位行）'))
    if dry_run:
        ids = {r.get('match_id') for r in rows}
        log('[预演] 将 upsert %d 局（去重后 %d 个 match_id）到 %s'
            % (len(rows), len(ids), storage.db_path))
        log('[预演] 未写入任何数据')
        return {'n_rows': len(rows), 'n_unique': len(ids), 'dry_run': True}
    for deck_id, name in decks:
        storage.upsert_my_deck(deck_id, name, 'decks_json')
    for r in rows:
        storage.upsert_match(r)
    log('[写入] upsert %d 局 -> %s' % (len(rows), storage.db_path))
    return {'n_rows': len(rows),
            'n_unique': len({r.get('match_id') for r in rows}),
            'dry_run': False}


# ------------------------------------------------------------------ 对账
def verify(jsonl_path, storage, log=print, max_detail=20):
    """逐字段对账。返回 (字段差异条数, 比对字段数, 库里缺失条数)。

    ⚠️ 「库里缺失」不算差异：观战/回放记录会被 `--sync` 主动清理掉，
    而 v1 jsonl 里仍然有它们 —— 那是预期行为，不是数据丢了。
    """
    rows = load_jsonl(jsonl_path)
    diffs = []
    checked = 0
    missing = []
    for r in rows:
        mid = r.get('match_id')
        got = storage.get_match(mid)
        if got is None:
            missing.append(mid)
            continue
        for key, want in r.items():
            if key not in MATCH_FIELDS:
                continue          # jsonl 里有、但 schema 有意不落的字段
            checked += 1
            have = got.get(key)
            if have != want:
                diffs.append((mid, key, want, have))
    n_db = storage.count()
    if missing:
        log('[对账] 库里没有这 %d 局（观战清理/结果修正属预期）：%s'
            % (len(missing), ', '.join(missing[:5])))
    if diffs:
        log('[对账] 发现 %d 处差异（最多显示 %d 处）：' % (len(diffs), max_detail))
        for mid, key, want, have in diffs[:max_detail]:
            log('   %s  %-16s jsonl=%r  db=%r' % (mid, key, want, have))
    else:
        log('[对账] 逐字段比对 %d 个字段，0 差异' % checked)
    log('[对账] 库内共 %d 局，源文件 %d 行' % (n_db, len(rows)))
    return len(diffs), checked, len(missing)


# ------------------------------------------------------------------ main
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

    ap = argparse.ArgumentParser(description='jsonl -> SQLite 迁移（幂等）')
    ap.add_argument('--jsonl', default=None,
                    help='源 jsonl 路径（默认 matches.jsonl，其次 .v1.bak 备份）')
    ap.add_argument('--db', default=None, help='目标 SQLite 路径')
    ap.add_argument('--decks', default=None, help='decks.json 路径（默认自动定位）')
    ap.add_argument('--dry-run', action='store_true', help='只预演，不写库')
    ap.add_argument('--verify', action='store_true', help='只对账，不写库')
    ap.add_argument('--force', action='store_true',
                    help='库里已有数据时仍强制重灌（会把已清理的观战记录带回来）')
    args = ap.parse_args(argv)

    src = resolve_jsonl(args.jsonl)
    if not os.path.exists(src):
        print('[错误] 找不到源文件：%s' % src)
        return 2
    if args.jsonl is None and src != DEFAULT_JSONL:
        print('[源] %s（已迁移的 v1 备份）' % src)

    storage = Storage(args.db)
    if args.verify:
        storage.init_schema()
        n_diff, _, _ = verify(src, storage)
        return 1 if n_diff else 0

    if storage.count() and not args.force:
        print('[跳过] 库里已有 %d 局数据 —— v1 jsonl 是一次性导入源，不再重灌。'
              % storage.count())
        print('        重灌会把**已清理的观战记录**和**已修正的结果**带回来。')
        print('        只想知道有没有差异：--verify；确实要重灌：--force。')
        return 0

    info = migrate(src, storage,
                   decks_path=(args.decks or find_decks_json()),
                   dry_run=args.dry_run)
    if info['dry_run']:
        return 0

    n_diff, _, _ = verify(src, storage)
    print()
    if n_diff:
        print('迁移完成：%d 局，%d 差异（见上）' % (info['n_rows'], n_diff))
        return 1
    print('迁移完成：%d 局，0 差异' % info['n_rows'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
