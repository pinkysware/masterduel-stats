# -*- coding: utf-8 -*-
"""
archive_matches.py — 独立对局存档器

把 Untapped 日志里解析出的对局信息，写进**本项目自己的存档文件**。
完全不修改 Untapped 的任何文件，纯只读，不干扰它工作。

存档内容（每局一条 JSON）：
    match_id / 开始时间 / 结束时间 / 我方座位 / 我的卡组 / 先后手
    结果 / 回合数 / 对手卡组(手填) / 对手露出的卡 / 用时

★ M2 起：SQLite 是唯一真源
==========================
本脚本不再写 jsonl，所有数据经 `core/storage.py` 落到 `data/masterduel.db`。
`data/archive/matches.jsonl` 已备份为 `matches.jsonl.v1.bak`，只供人工查阅。
详见 docs/M2计划.md §1.2。

★ M2.1 起：对局模式过滤
========================
日志里的 `Starting Duel` **不只代表天梯对局**。本脚本按「进入 `DuelClient` 之前
那一次界面跳转」判定模式（见 `classify_mode`）：

    观战/回放 DuelLive     -> live   不是对局，**不入库**，同步时还会清掉历史误录
    单人模式 Solo          -> solo   入库，但**不进统计**
    天梯 / 活动赛 DuelStart -> pvp    正常统计；**没有结束标记（中途退出/掉线）直接判负**
    判不出（日志已轮转）    -> unknown 仍按对局统计，不能丢

用法:
    python tools/archive_matches.py                # 增量同步（推荐，随时可跑）
    python tools/archive_matches.py --sync         # 同上（显式写法）
    python tools/archive_matches.py --stats        # 只看统计，不写库
    python tools/archive_matches.py --today        # 只看今日战绩

存档路径:
    data/masterduel.db             ★ 唯一真源（SQLite）
    data/archive/state.json        增量游标（记录已处理到哪个文件多少字节）
"""
import re
import json
import os
import sys
import argparse
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, date

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.storage import (Storage, day_range, counted_clause,  # noqa: E402
                          MODE_PVP, MODE_SOLO, MODE_LIVE, MODE_UNKNOWN,
                          MODE_GROUP_NAMES)

HOME = os.path.join(os.environ.get('APPDATA',
                                   os.path.expanduser('~')),
                    'untapped-companion')
LOGS = [os.path.join(HOME, 'log.old.log'), os.path.join(HOME, 'log.log')]
CARDS_JSON = os.path.join(PROJ, 'data', 'card_data.json')
DECKS_JSON = os.path.join(HOME, 'ygom', 'decks.json')
ARCHIVE_DIR = os.path.join(PROJ, 'data', 'archive')
MATCHES = os.path.join(ARCHIVE_DIR, 'matches.jsonl')   # v1 路径，已退役（见模块头）
STATE = os.path.join(ARCHIVE_DIR, 'state.json')
OPP_DECKS = os.path.join(ARCHIVE_DIR, 'opp_decks.json')   # 对手卡组字典（手填累积）

# 玩家自己的 deckId 白名单来源：untapped decks.json 里的都是自己的卡组
# 但当我们从 Selected deck changed 兜底取 id 时，可能是 DR_* 那种活动卡组，
# 也一并接受，只要它出现在 decks.json 里就认为是"我的"。

RE_LINE = re.compile(r'^\[([\d\-]+ [\d:.]+)\]\s+\[(\w+)\]\s+\[([\w.]+)\]\s+(.*)')
RE_ACT = re.compile(r'^(\w+): cardId=(\d+) source=(\S+) target=(\S+)')

#: 「进入对局」的那次界面跳转：<来源界面> -> <某个 DuelClient>。
#: 用它区分天梯 / 单人 / 观战回放 —— 实测这是唯一可靠的判据，
#: `deckId=null` 不行（观战和单人都会是 null）。
RE_ENTRY = re.compile(r'ygomCurrentViewWatcher\]\s+(\S+)\s+->\s+(\S*DuelClient)\s*$')


def classify_mode(entry_view):
    """由「进入 DuelClient 的来源界面」判定对局模式。

    实测（2026-09-11，12 条 `Starting Duel` 全部验证通过）：

        DuelLiveViewController           -> 'live'  观战/回放，**不是对局**
        ProfileReplayViewController      -> 'live'  自己资料里的回放，**不是对局**
        ColosseumHistoryViewController   -> 'live'  活动「对战记录」里点回放，**不是对局**
        SoloStartProductionViewController -> 'solo'  单人模式（打 NPC）
        DuelStartViewController          -> 'pvp'   天梯 / 活动赛
        其它 / 判定不出                   -> 'unknown'

    ⚠️ `ProfileReplay` / `History` 这两条是 2026-09-12 补的：一直漏判，导致**回放被
    归档成 `mode='unknown'` 的行**（实测当天 3 + 2 局）—— 不入统计（无害）但多出行数。
    判据：**从任何「回放列表/对战记录」界面进 DuelClient 都是看回放**；
    真正的活动对局是从 `PvpMenuMatchingViewController -> DuelStartViewController` 进的。
    """
    if not entry_view:
        return MODE_UNKNOWN
    if 'DuelLive' in entry_view:
        return MODE_LIVE
    if 'Replay' in entry_view:
        return MODE_LIVE
    if 'History' in entry_view:
        return MODE_LIVE
    if 'Solo' in entry_view:
        return MODE_SOLO
    if 'DuelStart' in entry_view:
        return MODE_PVP
    return MODE_UNKNOWN


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


CMAP = {x['id']: x['name'] for x in load_json(CARDS_JSON, [])}
DECKS = load_json(DECKS_JSON, {}) or {}
OPP = load_json(OPP_DECKS, {}) or {}


def cname(cid):
    return CMAP.get(cid) or ('[未知:%d]' % cid if cid else '(隐藏)')


# ---------------------------------------------------------------- 读日志
def read_log_with_offsets():
    """返回 [(path, text)]，按 LOGS 顺序。"""
    out = []
    for p in LOGS:
        if os.path.exists(p):
            with open(p, encoding='utf-8', errors='replace') as f:
                out.append((p, f.read()))
    return out


def split_matches(lines):
    """把日志行切成对局段。

    起点信号（按优先级）：
      1. 'Starting Duel with deckId='
    终点信号：
      'Game ended: result=...' 或下一条 'Starting Duel'
    同时收集段内的 'Selected deck changed:' 作为 deckId 兜底。

    并为每局记下 `entry_view` —— `Starting Duel` 之前**最后一次进入 `DuelClient`**
    的来源界面，用来判定这局是天梯 / 单人 / 观战回放（见 classify_mode）。
    """
    matches = []
    cur = None
    last_selected = None      # (line_no, deck_id, timestamp)
    last_duel_entry = None    # 最近一次 "X -> DuelClient" 的 X

    for i, l in enumerate(lines):
        # ⚠️ 必须用 search：日志行前面还有 `[时间戳] [info] [模块]`
        mv = RE_ENTRY.search(l)
        if mv:
            last_duel_entry = mv.group(1)

        if 'Selected deck changed:' in l:
            m = re.search(r'Selected deck changed:\s*(\S+)', l)
            t = RE_LINE.match(l)
            if m:
                last_selected = dict(line=i, deck_id=m.group(1),
                                     time=(t.group(1) if t else None))

        if 'Starting Duel with deckId=' in l:
            if cur is not None:          # 上一局没正常结束，先收尾
                cur['end_line'] = i - 1
                cur['result'] = cur.get('result') or '(未结束)'
                matches.append(cur)
            m = RE_LINE.match(l)
            mm = re.search(r'deckId=(\S+?),', l)
            raw = mm.group(1) if mm else 'null'
            deck_id = None if raw in ('null', 'None', '') else raw
            cur = dict(
                start_line=i, end_line=None,
                start_time=(m.group(1) if m else None),
                end_time=None,
                deck_id_raw=deck_id,
                deck_id=None,
                deck_source=None,
                result=None,
                entry_view=last_duel_entry,
                selected_before=(last_selected.copy() if last_selected else None),
            )
        elif 'Game ended' in l and cur is not None:
            m = RE_LINE.match(l)
            mm = re.search(r'result=(\w+)', l)
            cur['result'] = mm.group(1) if mm else '?'
            cur['end_time'] = m.group(1) if m else None
            cur['end_line'] = i
            matches.append(cur)
            cur = None

    if cur is not None:
        cur['end_line'] = len(lines) - 1
        cur['result'] = cur.get('result') or '(未结束)'
        matches.append(cur)
    return matches


def resolve_deck(seg):
    """决定这局用的卡组 id。优先日志直读，否则回退 Selected deck changed。"""
    did = seg.get('deck_id_raw')
    if did:
        return did, 'log'
    sel = seg.get('selected_before')
    if sel:
        return sel['deck_id'], 'selected-fallback'
    return None, 'unknown'


def parse_seg(seg, lines):
    """解析一局的完整事件与统计。"""
    did, src = resolve_deck(seg)
    seg['deck_id'], seg['deck_source'] = did, src

    # 硬币行（'is deciding whether to go first'）出现在 'Starting Duel' **之前**
    # （实测差几秒），所以往前多扫 60 行避免漏掉。
    # ⚠️ 回扫区（start_line 之前的行）**只认 COIN**：60 行可能扫进**上一局的收尾**
    #    ——那里的 ACT/TURN/END 属于上一局（ACT 会变成下一局 opp_revealed 的
    #    前缀卡、TURN 会抬高 total_turns）。2026-09-13 实测：12:05 局的前缀
    #    混入了 12:01 的 HANDOPEN/SPSUMMON（33 对相邻局里 3 对中招）。
    lo = max(0, seg['start_line'] - 60)
    events = []
    for i, l in enumerate(lines[lo:seg['end_line'] + 1]):
        cur = lo + i
        m = RE_LINE.match(l)
        if not m:
            continue
        ts, lvl, mod, msg = m.groups()
        if 'is deciding whether to go first' in msg:
            events.append(('COIN', ts, 'Player' if msg.startswith('Player') else 'Opponent'))
        elif cur < seg['start_line']:
            continue                      # 回扫区只认 COIN（见上）
        elif re.search(r'Turn \d+ for player \d+', msg):
            mm = re.search(r'Turn (\d+) for player (\d+)', msg)
            events.append(('TURN', ts, (int(mm.group(1)), int(mm.group(2)))))
        elif 'Game ended' in msg:
            events.append(('END', ts, None))
        else:
            am = RE_ACT.match(msg)
            if am:
                act, cid, s, t = am.groups()
                events.append(('ACT', ts, (act, int(cid), s, t)))

    # --- 座位判定 ---
    my_ids = set()
    if did and did in DECKS:
        dk = DECKS[did]
        for k in dk.get('main', {}):
            my_ids.add(int(k))
        for k in dk.get('extra', {}):
            my_ids.add(int(k))

    hits = {0: 0, 1: 0}
    tot = {0: 0, 1: 0}
    vis = {0: 0, 1: 0}
    for kind, ts, p in events:
        if kind == 'ACT' and p[0] == 'DRAW':
            _, cid, s, _t = p
            side = 0 if s.startswith('P0') else 1
            tot[side] += 1
            if cid:
                vis[side] += 1
            if cid in my_ids:
                hits[side] += 1

    if my_ids and (hits[0] or hits[1]):
        seat = 0 if hits[0] >= hits[1] else 1
        seat_src = 'deck-hit P0=%d/%d P1=%d/%d' % (hits[0], tot[0], hits[1], tot[1])
    elif vis[0] or vis[1]:
        seat = 0 if vis[0] >= vis[1] else 1
        seat_src = 'visibility P0=%d P1=%d' % (vis[0], vis[1])
    else:
        seat, seat_src = 0, 'unknown'

    # --- 硬币 / 先后手 ---
    #
    # ⚠️ 重要更正（2026-09-10，用 Untapped API 全量 859 场天梯交叉验证）：
    #
    #   日志里的 'is deciding whether to go first' 语义是
    #        "<主语> 正在决定是否先攻"  == 「主语赢得了硬币，获得选择权」，
    #   **不是**「主语先攻」。
    #
    #   实测在 API 侧：
    #       我方获选择权 372/859 = 43.3%   → 当你拿到选择权时，99.7% 都选了先攻
    #       我方实际先攻 434/859 = 50.5%   → 对方拿到选择权时，有 12.9% 会选后手，让你先攻
    #   所以「实际先攻」≈ 50%，是公平的；而「拿到选择权」只有 43.3%（z=-3.92, p<0.0001）
    #   显著偏低 —— 用户长期怀疑的"先手率异常"其实出在硬币这一环。
    #
    #   结论：
    #     * went_first（实际先攻）用 Turn 1 归属判定，**可靠且覆盖 100% 对局**。
    #     * won_initiative（硬币选择权）依赖日志里的 'deciding whether' 行，
    #       但实测极稀疏（log.log 5489 行里只有 3 次），只能偶尔拿到，
    #       所以存档里该字段常为 null —— 不要用它做统计。
    #
    #   因此这里**两个概念分开存**，不要混：
    #       won_initiative : 我方是否获得先后手选择权（稀疏，常 null）
    #       went_first     : 我方是否实际先攻（Turn 1 归属，可靠）
    coin = next((p for k, t, p in events if k == 'COIN'), None)
    won_initiative = None
    if coin == 'Player':
        won_initiative = True
    elif coin == 'Opponent':
        won_initiative = False

    # 实际先攻：用 Turn 1 归属，比硬币措辞可靠
    first_player = None
    for kind, ts, p in events:
        if kind == 'TURN' and p[0] == 1:
            first_player = p[1]
            break
    went_first = None
    if first_player is not None:
        went_first = (first_player == seat)

    # --- 回合数 ---
    max_turn = max((p[0] for k, t, p in events if k == 'TURN'), default=0)

    # --- 对手露出的卡（去重，保留出现顺序）---
    opp_cards = []
    seen = set()
    for kind, ts, p in events:
        if kind != 'ACT':
            continue
        act, cid, s, t = p
        side = 0 if s.startswith('P0') else 1
        if side != seat and cid and cid not in seen:
            seen.add(cid)
            opp_cards.append(cid)

    # --- 用时 ---
    dur = None
    if seg.get('start_time') and seg.get('end_time'):
        try:
            a = datetime.strptime(seg['start_time'][:19], '%Y-%m-%d %H:%M:%S')
            b = datetime.strptime(seg['end_time'][:19], '%Y-%m-%d %H:%M:%S')
            dur = int((b - a).total_seconds())
        except Exception:
            pass

    # --- 稳定 match_id：用开始时间 + 卡组哈希 ---
    seed = '%s|%s|%s' % (seg.get('start_time'), did, seat)
    mid = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]

    return dict(
        match_id=mid,
        started_at=seg.get('start_time'),
        ended_at=seg.get('end_time'),
        result=seg.get('result'),
        my_deck_id=did,
        my_deck_name=(DECKS.get(did) or {}).get('name') if did else None,
        deck_source=src,
        mode=classify_mode(seg.get('entry_view')),
        seat=seat,
        seat_evidence=seat_src,
        won_initiative=won_initiative,
        went_first=went_first,
        first_player=first_player,
        total_turns=max_turn,
        duration_sec=dur,
        opp_deck_id=None,          # 待人工填写
        opp_deck_name=None,        # 待人工填写
        opp_revealed=[cname(c) for c in opp_cards][:30],
        opp_revealed_ids=opp_cards[:30],
    )


def archive_all(lines, verbose=True):
    segs = split_matches(lines)
    rows = [parse_seg(s, lines) for s in segs]
    rows.sort(key=lambda r: r.get('started_at') or '')
    if verbose:
        print('[解析] 共 %d 局' % len(rows))
    return rows


# ---------------------------------------------------------------- 存档 IO
# v1 时代这里有 load_archive / write_archive / merge_archive 三个函数，直接读写
# data/archive/matches.jsonl。M2 起改由 core.storage 负责：
#
#     load_archive()  ->  storage.list_matches(limit=None)
#     write_archive() ->  storage.upsert_match()（逐个 upsert，天然幂等）
#     merge_archive() ->  删除：合并语义已在 SQL 的 ON CONFLICT 里实现，
#                         且"新值为 NULL 就保留旧值"覆盖了人工标注的保护
#
# ⚠️ 解析逻辑（split_matches / resolve_deck / parse_seg / archive_all）一字未动。
def open_storage():
    """打开唯一真源，并确保表结构就位。"""
    st = Storage()
    st.init_schema()
    return st


def warn_unsynced(st, rows):
    """日志里解析出来、但库里还没有的对局 -> 提示跑 --sync。

    与 v1 的差别：v1 的 --stats 会把新解析结果隐式并进统计，M2 起统计只认库，
    所以这里只提示、不偷偷写库。库是最新的时候什么都不输出。

    ⚠️ 判据必须和 upsert 的身份口径一致：同一局可能因为日志轮转导致
    match_id 漂移（见 core.storage.upsert_match），所以 **match_id 或 started_at
    任一命中**就算"已在库"。另外**观战/回放本来就不入库**，不能算"未同步"。
    """
    if not rows:
        return
    db = st.list_matches(limit=None)
    known_ids = {r['match_id'] for r in db}
    known_times = {r['started_at'] for r in db}
    # ⚠️ solo 也排除：单人局**本来就不入库**（用户 2026-10-08 口径），
    #    不排除的话每次 --stats 都会提示「有 N 局尚未入库」，而 --sync 又不写。
    fresh = [r['match_id'] for r in rows
             if r.get('mode') not in (MODE_LIVE, MODE_SOLO)
             and r['match_id'] not in known_ids
             and r['started_at'] not in known_times]
    if fresh:
        print('[提示] 日志里有 %d 局尚未入库，跑 --sync 同步' % len(fresh))



# ------------------------------------------------- S-4：record/log 混用闸门
#: 同局 record 行与 log 行的「疑似混用」时间窗（秒）。
#: 口径出处：docs/对局记录格式.md §6b.2（两路径不许对同一局混用）+
#: docs/M6计划.md §四.2（判据细化：已并档行重跑不算混用）。
S4_WINDOW_SEC = 60


def _parse_ts(ts):
    """started_at -> datetime（日志行带毫秒、record 行秒级，两种都认）。"""
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(ts, fmt)
        except (TypeError, ValueError):
            continue
    return None


def find_record_mismatch(db_rows, match_id, started_at,
                         window=S4_WINDOW_SEC):
    """S-4 闸门：新 log 行会不会撞上库内 record 行（同局两路径各记一行）。

    三条件**同时满足**才算混用（终审口径，缺一放行）：
      1. 本次 upsert 的 ``match_id`` 不在库内 —— match_id 命中（含已并档的
         record 行）只是正常的 upsert 更新，不是混用；
      2. 库内存在 ``record_dir`` 非空的行；
      3. 两行 ``started_at`` 相差 ≤ ``window`` 秒。
    命中返回那条库内行（供打印明细），否则 None。
    """
    cand = _parse_ts(started_at)
    if cand is None:
        return None
    if any(row.get('match_id') == match_id for row in db_rows):
        return None
    for row in db_rows:
        if not row.get('record_dir'):
            continue
        db_ts = _parse_ts(row.get('started_at'))
        if db_ts is not None and abs((db_ts - cand).total_seconds()) <= window:
            return row
    return None


# ---------------------------------------------------------------- 统计
def _pct(x):
    """0.75 -> '75.0%'；None -> '-'（与 v1 的输出格式一致）。"""
    return '%.1f%%' % (100 * x) if x is not None else '-'


def print_scope(st):
    """打印统计口径 + 被排除的局数。

    口径是「只算排名决斗 + 巅峰决斗」（用户 2026-09-11 定），依赖
    `tools/fetch_untapped_api.py --apply` 回填的 `game_mode`。
    """
    with st._open() as conn:
        n_all = conn.execute('SELECT COUNT(*) AS n FROM matches').fetchone()['n']
        n_ok = conn.execute('SELECT COUNT(*) AS n FROM matches WHERE '
                            + counted_clause()).fetchone()['n']
    if n_all:
        print('（口径：只算排名决斗 + 巅峰决斗；另有 %d 局属其它模式或判不出，未计入）'
              % (n_all - n_ok))


def show_group_stats(rows):
    """按模式分类的对照表（用户 2026-09-13 口径：杯赛单独统计、不入主口径）。"""
    print('=== 按模式分类（主口径 = 天梯 + 巅峰；杯赛单独统计）===')
    print('  %-7s %-16s %6s %6s %6s %8s' % ('分类', '名称', '场次', '胜', '负', '胜率'))
    for key, name, s in rows:
        n = s['n_total']
        print('  %-7s %-16s %6d %6d %6d %8s'
              % (key, name, n, s['n_win'], s['n_lose'],
                 ('%.1f%%' % (100.0 * s['n_win'] / n)) if n else '-'))
    main = sum(s['n_total'] for k, _n, s in rows if k in ('ranked', 'peak'))
    print('（主口径 = 天梯组 + 巅峰组 = %d 场；杯赛组行数少或为 0 属正常 ——'
          ' 库里只有采集到的局，用户 2026-09-13 决定不补历史）' % main)


def show_stats(stats, dist, today_only=False, title=None):

    """打印统计。数字全部来自 SQLite（唯一真源）。

    stats 结构见 core.storage.get_stats；dist 是 [(卡组名, 场次)]。
    输出格式与 v1 的 show_stats 逐字节一致（**默认路径一个字都不改**；
    ``title`` 只有「按分类单独统计」那条路才传）。
    """
    if title:
        print('=== %s ===' % title)
    elif today_only:
        print('=== 今日战绩 (%s) ===' % stats['date'])
    else:
        print('=== 全部战绩 ===')

    n = stats['n_total']
    if not n:
        print('暂无已结束对局')
        return

    print('总场次 %d   胜 %d   负 %d   平 %d   胜率 %.1f%%' % (
        n, stats['n_win'], stats['n_lose'], stats['n_draw'],
        100 * stats['n_win'] / n))

    if stats['first_n'] or stats['second_n']:
        print('实际先攻 %d 场 胜率 %s   后攻 %d 场 胜率 %s   先手率 %s' % (
            stats['first_n'], _pct(stats['first_rate']),
            stats['second_n'], _pct(stats['second_rate']),
            _pct(stats['first_hand_pct'])))

    if stats['initiative_n']:
        print('赢得硬币选择权 %d 场（%.1f%%）' % (
            stats['initiative_won'], 100 * stats['initiative_rate']))

    print('卡组分布: ' + '  '.join('%s×%d' % (k, v) for k, v in dist))

    print('对手卡组已标注 %d / %d（待标注 %d）' % (
        n - stats['unmarked'], n, stats['unmarked']))


# ---------------------------------------------------------------- main
def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

    ap = argparse.ArgumentParser(description='独立对局存档器（SQLite 版）')
    ap.add_argument('--sync', action='store_true',
                    help='增量同步日志到 SQLite（默认行为，只 upsert 不删数据）')
    ap.add_argument('--rebuild', action='store_true',
                    help='【已废弃】等价于 --sync（同样走 S-4 混用闸门）')
    ap.add_argument('--force', action='store_true',
                    help='S-4 闸门放行：疑似 record/log 混用的行也强行写入'
                         '（默认跳过。代价不对称：误跳过多跑一次 --force 就行，'
                         '误写入=战绩静默翻倍）')
    ap.add_argument('--stats', action='store_true',
                    help='只看统计（不写库）；--group-stats 看按分类对照表')
    ap.add_argument('--today', action='store_true', help='只看今日（不写库）')
    ap.add_argument('--mode-group', default=None, metavar='GROUP',
                    help='只算某一类：ranked / peak / dc / event / all'
                         '（杯赛按用户口径单独统计，不进主口径）')
    ap.add_argument('--group-stats', action='store_true',
                    help='打印「按模式分类」对照表'
                         '（天梯/巅峰/DC 杯/活动杯赛各一行）')
    args = ap.parse_args()

    if args.rebuild:
        print('[警告] --rebuild 已废弃：SQLite 版永远只做 upsert，'
              '不会删数据、也不会丢人工标注。已按 --sync 执行。')

    st = open_storage()
    pairs = read_log_with_offsets()
    all_lines = []
    for p, text in pairs:
        all_lines.extend(text.splitlines())
    rows = archive_all(all_lines, verbose=not args.stats)

    if args.stats or args.today or args.mode_group or args.group_stats:
        # 只读模式：统计数字全部来自 SQLite
        print_scope(st)
        if args.today:
            stats = st.get_today_stats()
            dist = st.get_deck_distribution(*day_range(stats['date']))
        else:
            stats = st.get_stats()
            dist = st.get_deck_distribution()
        if args.group_stats:
            show_group_stats(st.get_group_stats())
            return
        if args.mode_group is not None:
            g = args.mode_group
            label = MODE_GROUP_NAMES.get(g, '全部模式')
            show_stats(st.get_stats(mode_group=g), st.get_deck_distribution(),
                       title='单独统计：%s（%s）' % (label, g))
            return
        warn_unsynced(st, rows)
        show_stats(stats, dist, today_only=args.today)
        return

    # 写入模式：全部走 upsert（幂等，重复跑不会重复插）
    db_rows = st.list_matches(limit=None)   # S-4 闸门的库内快照（循环外一次读）
    skipped = []
    removed = []
    gated = []          # S-4：疑似 record/log 混用被拦下的行
    n_written = 0
    n_live = n_solo = n_pvp = n_unknown = 0
    for r in rows:
        m = r.get('mode')
        if m == MODE_LIVE:
            n_live += 1
        elif m == MODE_SOLO:
            n_solo += 1
        elif m == MODE_PVP:
            n_pvp += 1
        else:
            n_unknown += 1
        if m != MODE_LIVE and not args.force:
            bad = find_record_mismatch(db_rows, r.get('match_id'),
                                       r.get('started_at'))
            if bad is not None:
                gated.append(r)
                print('[闸门] S-4：%s（started_at=%s）与库内 record 行 %s'
                      '（record_dir=%s，started_at=%s）相距 ≤%ds，'
                      '疑似 record/log 对同一局混用，已跳过'
                      % (r['match_id'], r.get('started_at'), bad['match_id'],
                         bad['record_dir'], bad['started_at'], S4_WINDOW_SEC))
                continue
        status = st.upsert_match(r)
        if status == 'written':
            n_written += 1
        elif status == 'skipped':
            skipped.append(r['match_id'])
        elif status == 'skipped-live':
            # 观战/回放不是对局：库里若曾误录过，清掉（id 或开始时间命中都算）
            n = st.delete_match(r['match_id'], r['started_at'])
            if n:
                removed.append(r['started_at'])
        elif status == 'skipped-solo':
            # 单人（PvE）不入库（用户 2026-10-08 口径）—— 库里历史误录的清掉
            n = st.delete_match(r['match_id'], r['started_at'])
            if n:
                removed.append(r['started_at'])
    print('[增量] 写入/更新 %d 局，存档共 %d 局' % (n_written, st.count()))
    print('[模式] 天梯/活动 %d 局、单人 %d 局、观战回放 %d 局、判不出 %d 局'
          '（观战不入库）' % (n_pvp, n_solo, n_live, n_unknown))
    if skipped:
        print('[提示] %d 局与库内记录同开始时间但 match_id 不同'
              '（卡组/座位解析随日志轮转变化），已跳过：%s'
              % (len(skipped), ', '.join(skipped[:5])))
    if gated:
        print('[闸门] S-4：共拦下 %d 局疑似混用（该局应走 record 入库；'
              '确要写日志行用 --force）' % len(gated))
    if removed:
        print('[清理] 移除 %d 局此前误录的观战/回放记录：%s'
              % (len(removed), ', '.join(x[11:19] for x in removed[:5])))
    print('[存档] %s' % st.db_path)
    print()
    show_stats(st.get_stats(), st.get_deck_distribution())
    print()
    today = date.today().isoformat()
    show_stats(st.get_today_stats(today),
               st.get_deck_distribution(*day_range(today)), today_only=True)


if __name__ == '__main__':
    main()
