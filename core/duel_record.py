# -*- coding: utf-8 -*-
"""对局记录三层格式的层 0→层 1 解码（M3）。

格式定义见 ``docs/对局记录格式.md``。本模块只做「读 record 目录 + 解码 + 组织」：
不碰进程、不联网、不写 matches 表（入库在 ``tools/import_duel_records.py``）。

三层：
* 层 0 ``record_*/`` 原始字节（真源，采集器产物）——本模块只读；
* 层 1 ``duel.json`` 结构层（同目录落盘，可幂等重解、meta 合并保留）——本模块读写；
* 层 2 ``matches`` 表 —— 见 storage（本模块只提供 :func:`to_row` 出行）。

★ 截断铁律：解码一律 ``count × 元素尺寸``（:data:`ARRAY_SIZES`），
绝不能按 bin 的 bytes/len 反推条数 —— 抓取侧经常超量读，尾巴是数组外的内存垃圾。
★ 身份：``record_id`` = 目录名；``match_id = 'rec:' + record_id``；
**目录 ≠ 一局**（watcher 重启会给同一局新建目录），同局分组见 :func:`group_duels`。
"""

import copy
import hashlib
import json
import os
import re
import struct
from datetime import datetime

from core import card_names as _cn
from core import hand_traps as _ht
from core.memread import duel_log as _dl
from core.memread.end_message import derive_result_from_winbits
from core.storage import (DECK_SNAPSHOT_TRUSTED_MODES,
                          MODE_GROUP_BY_GAME_MODE, MODE_GROUP_SOLO)

DUEL_JSON_NAME = 'duel.json'
FORMAT_VERSION = 1

#: 卡组名归一的**进程内缓存**（BUG-B2A-11）：
#: 名 -> 显示名（查不到 = None）。
#: 全量重跑 `to_row()` 时每行新建 Storage + 查一次库是线性放大。
_DISPLAY_NAME_CACHE = {}
#: 懒建的 Storage 单例（仅 `_display_deck_name` 用；建构不跑 init_schema）
_DISPLAY_STORAGE = None

#: 同局分组的最大间隔（capture 区间不重叠但间隔 ≤ 此值仍算同一局）
MERGE_GAP_SEC = 900
#: end_messages 双向窗（秒）——one-shot/残影在结算后才建目录，必须双向。
#: ⚠️ 终审钉死 W=300（docs/M3计划终审意见.md §二：按此值四局 4/4 认领，
#: 不许调参数凑）；回填工具的 ended 侧窗口是另一回事（秒级，保持 60）。
EM_WINDOW_SEC = 300
#: infer_seat 的判定阈值（见 docs/对局记录格式.md §5.1）
SEAT_MIN_COVER = 0.6
SEAT_MIN_MARGIN = 0.2
#: 最少命中张数 —— 单张泛用卡（灰流丽之类）撞上快照不成证据
SEAT_MIN_HITS = 2

#: label -> 载荷数组名。**logbase 的 label 与数组名不同名的就这三组**；
#: ``SeparateLine`` 无载荷；其余 label 与数组同名。
LABEL_TO_ARRAY = {
    'ShowFirstPlayer': 'ChoiceFirstPlayer',
    'CardMoveMultiHeader': 'MoveMultiHeader',
    'CardMoveMulti': 'MoveMulti',
    'SeparateLine': None,
}

#: 各载荷数组的元素尺寸（与 ``core/memread/duel_log.py`` 的常量同源；
#: 单测 ``tests/test_duel_record.py`` 盯着两边一致，漂移即红）
ARRAY_SIZES = {
    'ShowAction': _dl.ENTRY_SIZE,          # 32
    'MoveMultiHeader': _dl.MULTI_HEADER_SIZE,  # 32
    'ShowTurn': _dl.TURN_SIZE,             # 24
    'ShowPhase': _dl.PHASE_SIZE,           # 4
    'ShowChain': _dl.CHAIN_SIZE,           # 12
    'ShowCardName': _dl.CARD_NAME_SIZE,    # 6
    'ShowText': 4,
    'ShowTag': 4,
    'ChoiceFirstPlayer': 4,
    'MoveMulti': 8,                        # 指针数组（未解，raw）
}

#: 4 字节 raw int 的未解数组（events 里只存 raw 值，登记在案不猜）
RAW_INT_LABELS = ('ShowText', 'ShowTag', 'ChoiceFirstPlayer')
#: raw hex 的未解数组
RAW_HEX_LABELS = ('MoveMulti', 'MoveMultiHeader')

_DIRNAME_RE = re.compile(r'record_(\d{8})_(\d{6})$')


# ---------------------------------------------------------------- 小工具

def parse_dt(s):
    """'YYYY-MM-DD HH:MM:SS' -> datetime；解不出返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def dirname_time(record_dir):
    """record_YYYYMMDD_HHMMSS -> datetime（目录创建 = 首见时刻的近似）。"""
    m = _DIRNAME_RE.search(os.path.basename(os.path.normpath(record_dir)))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
    except ValueError:
        return None


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(record_dir):
    with open(os.path.join(record_dir, 'manifest.json'), 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


def capture_group(record_dir, manifest=None):
    """capture 时间组；legacy（无 capture 键）用目录名 + saved_at 近似。

    ⚠️ 目录名时间对 watcher 是「首见时刻（≈开局）」、对 one-shot 是「结算时刻」，
    两者含义不同 —— 精确锚点必须看 manifest 的 ``capture`` 键（M6 契约）。
    """
    manifest = manifest if manifest is not None else load_manifest(record_dir)
    cap = manifest.get('capture') or {}
    fs, ls = cap.get('first_seen_at'), cap.get('last_saved_at')
    approx = not (fs and ls)
    if approx:
        fs = fmt_dt(dirname_time(record_dir)) or manifest.get('saved_at')
        ls = manifest.get('saved_at') or fs
    return {'first_seen_at': fs, 'last_saved_at': ls, 'approx': approx,
            'first_logbase_size': cap.get('first_logbase_size')}


def fmt_dt(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else None


def state_of(manifest):
    """(state, state_src)。

    * 有 ``state`` 键 -> 原样（'open' / 'final'）；
    * 有 ``manifest_version`` 但没写 state -> 'open'（契约违反，import 会跳过）；
    * 都没有 -> legacy 存量，视为 'final'（``state_src='legacy'``）。
    """
    st = manifest.get('state')
    if st in ('open', 'final'):
        return st, 'manifest'
    if 'manifest_version' in manifest:
        return 'open', 'missing-but-versioned'
    return 'final', 'legacy'


def fingerprint(record_dir, card_names_file=None):
    """层 0 全部文件的 {名字: {size, mtime, md5}} + 一条**合成条目** `__card_names__`。

    `duel.json` 本身不计入（它是产物）；`__card_names__` 指向 `data/card_names.json`
    —— **卡表必须进指纹**：卡表一变（新卡收录 / `--update` 刷新）就要让
    `write_duel_json` 走「整份重写」分支，`summary.unknown_card_ids` 才会刷新
    （M4 审查 P1-1）。卡表没变时指纹不变，幂等捷径照走（不回归）。
    """
    out = {}
    for name in sorted(os.listdir(record_dir)):
        if name == DUEL_JSON_NAME:
            continue
        path = os.path.join(record_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        out[name] = {'size': st.st_size, 'mtime': round(st.st_mtime, 3),
                     'md5': _md5(path)}
    path = card_names_file or _cn.DEFAULT_PATH
    try:
        st = os.stat(path)
    except OSError:                     # 卡表不在也要留痕（别悄悄退化）
        out[CARD_NAMES_KEY] = {'size': None, 'mtime': None, 'md5': None}
    else:
        out[CARD_NAMES_KEY] = {'size': st.st_size, 'mtime': round(st.st_mtime, 3),
                               'md5': _md5(path)}
    return out


def scan_record(dir_path):
    """读一份 record 目录的基本信息（解码/分组共用，manifest 只 load 一次）。"""
    manifest = load_manifest(dir_path)
    state, state_src = state_of(manifest)
    cap = capture_group(dir_path, manifest)
    lb = manifest.get('logbase') or {}
    return {
        'dir': dir_path,
        'name': os.path.basename(os.path.normpath(dir_path)),
        'manifest': manifest,
        'state': state,
        'state_src': state_src,
        'first_seen': parse_dt(cap['first_seen_at']),
        'last_saved': parse_dt(cap['last_saved_at']),
        'capture_approx': cap['approx'],
        #: 首见时的 logbase size（M6 契约）—— 同局延续性判据用（见 _logbase_continues）
        'first_logbase_size': cap.get('first_logbase_size'),
        'logbase_size': lb.get('size') or len(lb.get('entries') or []),
        #: ★ BUG-B2A-13：**两个 size 是否都可用**。
        #:   它们取自不同时点（first = capture 首见时 / last = logbase 落盘时），
        #:   缺一个就是「判不出续不续」（与「判出不是延续」不同事）。
        #:   `_logbase_continues` 拋异常前先看这个标志，日志/单测才能分开记。
        'continues_known': (cap.get('first_logbase_size') is not None
                            and (lb.get('size') is not None
                                 or bool(lb.get('entries')))),
        'instance': _norm_instance(manifest.get('instance')),
    }


def _norm_instance(v):
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 同局分组

def group_duels(scans, gap_sec=None):
    """把 scan_record 结果分成「局」：instance 相同且时间邻近的归一组。

    返回 ``[[scan, ...], ...]``，每组内按 first_seen 排序；
    调用方用 :func:`pick_main` 从组里挑主记录。
    """
    gap = MERGE_GAP_SEC if gap_sec is None else gap_sec
    groups = []
    for it in sorted(scans, key=lambda x: (x['instance'] is None,
                                           x['instance'] or 0,
                                           x['first_seen'] or datetime.min)):
        target = None
        for g in groups:
            if g[0]['instance'] is not None and g[0]['instance'] == it['instance'] \
                    and _interval_near(g, it, gap) and _logbase_continues(g, it):
                target = g
                break
        if target is None:
            groups.append([it])
        else:
            target.append(it)
    return groups


def _logbase_continues(group, item):
    """item 是不是 group 那局的**延续** —— 而不是「地址被复用的新局」。

    同局多目录（watcher 重启 / one-shot 残影）的 logbase 是**同一份的延续**：
    新目录的首见 size ≥ 旧目录已落盘的 size（单调不减）。
    ⚠️ 游戏**会复用控制器的内存地址**（实测 2026-09-18：233129 与 234153 两局
    同 instance、隔 8 分钟）—— 只按「instance + 时间邻近」分组会把两局并成一局，
    `pick_main` 挑 size 大的，**另一局被静默吞掉**（用户漏局事故的根因之一）。
    判不出（老 manifest 没记 `first_logbase_size`）→ 按旧口径放行（别把老数据判碎）。
    """
    for m in group:
        prev_last = m.get('logbase_size')
        nxt_first = item.get('first_logbase_size')
        if nxt_first is None or prev_last is None:
            return True                         # 判不出：保持旧口径
        if nxt_first >= prev_last:
            return True                         # 延续（或至少没回退）
    return False                                # 首见 size 回退 = 地址被复用的新局


def _interval_near(group, item, gap):
    for m in group:
        a0, a1 = m['first_seen'], m['last_saved']
        b0, b1 = item['first_seen'], item['last_saved']
        if None in (a0, a1, b0, b1):
            continue
        # 区间重叠，或间隔 <= gap
        if a0 <= b1 and b0 <= a1:
            return True
        delta = (b0 - a1).total_seconds() if b0 > a1 else (a0 - b1).total_seconds()
        if delta <= gap:
            return True
    return False


def pick_main(group):
    """同局多目录挑主记录：logbase.size 最大 -> saved_at 最晚 -> 目录名。"""
    return sorted(group, key=lambda s: (s['logbase_size'], s['last_saved']
                                        or datetime.min, s['name']))[-1]


# ---------------------------------------------------------------- unknown 卡 id（M4）

#: 指纹里的**合成条目**名（不是真文件）：卡名表 `data/card_names.json` 的状态。
#: M4 审查 P1-1：卡表不在指纹里 -> 换了卡表重跑 import 走 skipped 捷径 ->
#: `summary.unknown_card_ids` 永远停在旧值。纳入指纹才有「卡表一变就整份重写」。
CARD_NAMES_KEY = '__card_names__'


def card_name_table(card_names_file=None):
    """卡名表（**读盘刷新**，见 `card_names.load_fresh`）；路径不给用默认表。"""
    return _cn.load_fresh(card_names_file)


def collect_card_ids(events, summ):
    """**已解码字段**里出现过的全部卡 id（去重排序）。

    覆盖范围 = `ShowAction.cards[].card_id` / `ShowChain.card_id` /
    `ShowCardName.card_id` / `summary.revealed`（p0+p1）。
    ⚠️ `MoveMulti` 子条目还是 raw 字节（未解），**不在统计内** ——
    别据此承诺「全卡面无遗漏」（docs/对局记录格式.md §6）。
    ⚠️ `ShowAction.cards[].alt_id`（异画的标准版 id）也不算：对局里实际用的
    是 `card_id`（低 16 位），检查高 16 位只会凭空造出误报。
    """
    ids = set()
    for e in events:
        cid = e.get('card_id')
        if isinstance(cid, int):
            ids.add(cid)
        for c in e.get('cards') or []:
            cid = c.get('card_id')
            if isinstance(cid, int):
                ids.add(cid)
    rev = (summ or {}).get('revealed') or {}
    for side in ('p0', 'p1'):
        for cid in rev.get(side) or []:
            if isinstance(cid, int):
                ids.add(cid)
    return sorted(ids)


def find_unknown_card_ids(events, summ, table=None):
    """卡表里查不到的卡 id（排序）—— **卡表过期的信号**（M4）。

    卡表 = 游戏卡表 ∪ ygocdb 的并集（`data/card_names.json`），所以
    「查不到」只可能是**两个源都没有**（新卡 / 新异画），不是 join 漏了。
    """
    return [cid for cid in collect_card_ids(events, summ)
            if not _cn.is_known(cid, table)]


# ---------------------------------------------------------------- 手坑识别
# 见 docs/手坑识别计划.md（v4）。三条定案：
#   ① 「使用」判据 = kind ∈ 发动族 且 zone ∈ {Hand} ∪ {Zone7..12}
#      （泡影是陷阱卡，盖后开的 zone 是魔陷区 —— 只认 Hand 会漏 43%）；
#   ② 排除 DRAW / SETV / SETH / SEARCH / RETURN / SENDTOGRAVE（用户 Q2：只算从手牌打出）；
#   ③ 一次发动 = 同卡同侧、seq 间隔 ≤ HAND_TRAP_GAP 的连续记录
#      （HAPPEN 与 UNKNOWN_A/B 是同一次发动的两条记录；用**差分验证**定的平台区）。
#      ⚠️ 计划 §3.3b 正文写的「同卡连续（R2）」措辞与它自己的样表**互相矛盾** ——
#      seq153/seq171 两次泡影、seq275/306 两次遮蒙，R2 会各并成 1 次，与手核样本
#      「= 2 次」冲突。**本实现取 seq 间隔规则**（平台区 [3,8] 内结果一致），
#      过一个事件内 l/r 双侧重复也按间隔 0 合并（不会算成两次）。

#: 「发动族」动作（与 docs/手坑识别计划.md §3.6 的实测定案一致）
HAND_TRAP_KINDS = frozenset(('HAPPEN', 'UNKNOWN_A', 'UNKNOWN_B', 'EFFECTIMPL'))
#: 允许的区域：手牌 ∪ 场上魔陷区（7~12）—— 泡影盖后开发动在魔陷区
HAND_TRAP_ZONES = frozenset(
    ['Hand'] + ['Zone%d' % i for i in range(7, 13)])
#: 一次发动的 seq 间隔上界（平台区 [3,8] 内结果一致，取上界）
HAND_TRAP_GAP = 8
#: 回合号上限（正常一局 ≤ 20；record_20261010_203819 有 turn=597 脏值）。
#: ⚠️ 名字带前缀：这是**手坑回合判据**的上限，不是通用的局回合上限。
HAND_TRAP_MAX_TURN = 30
#: 统计范围 = **前两回合**（用户 2026-10-16 明确：只统计前 2 回合）。
#: ⚠️ 语义是「本局的第 1、2 个回合」= turn 0（先攻方首回合）+ turn 1（后攻方首回合），
#: **不是**「双方各自的第二回合」（那是 turn 2/3，用户不要 —— 我先前按 §3.4 的
#: 分类误收了 0..3，实测被用户纠正）。计划 §5.4.2 的定版统计本来也是按 turn 0..1 算的。
HAND_TRAP_TURNS = (0, 1)


def summarize_hand_traps(events):
    """事件流 -> 手坑证据（**只存证据，不存结论**，可重判）。

    输出::

        {'activations': [{'seq','turn','player','key','id','kind','zone'}, ...],
         'has_turns': bool,     # 本局有没有可用的 ShowTurn（无 -> 两列 NULL）
         'dirty_turn': int,     # 被回合上限滤掉的脏 ShowTurn 条数（可观测）
         'n': int}

    * ``turn`` 取「最近一条前置 ShowTurn」（§5.6）；无前置/脏回合号为 ``None``；
    * ``player`` 用 **per-card 的 ``side.player``**（P0/P1），不要用事件级 player
      （双侧事件会误归，见 §5.1 / 审查 P3-4）；
    * ``id`` 是**标准 id**（异画与标准归一到同一张，展示/卡图都用它）。
    """
    acts = []
    cur_turn = None
    has_turn = False
    dirty = 0
    last = {}                       # (player, key) -> 上一次计入的 seq
    for e in events:
        label = e.get('label')
        if label == 'ShowTurn':
            t = e.get('turn')
            if isinstance(t, int):
                if 0 <= t <= HAND_TRAP_MAX_TURN:
                    cur_turn = t
                    has_turn = True
                else:
                    dirty += 1      # 脏回合号：忽略，不污染其后事件
            continue
        if label != 'ShowAction':
            continue
        if e.get('kind_name') not in HAND_TRAP_KINDS:
            continue
        if e.get('zone_name') not in HAND_TRAP_ZONES:
            continue
        seq = e.get('seq')
        seen = set()                # 同一事件里 l/r 双侧重复 -> 只算一次
        for c in e.get('cards') or []:
            key = _ht.key_of(c.get('card_id'))
            if key is None:
                continue
            pl = c.get('player')
            k = (pl, key)
            if k in seen:
                continue
            seen.add(k)
            prev = last.get(k)
            if (prev is not None and seq is not None
                    and 0 <= seq - prev <= HAND_TRAP_GAP):
                continue            # 同一次发动的附属记录（seq 间隔在平台区）
            last[k] = seq
            acts.append({
                'seq': seq, 'turn': cur_turn, 'player': pl,
                'key': key, 'id': _ht.standard_id(key),
                'kind': e.get('kind_name'), 'zone': e.get('zone_name'),
            })
    return {'activations': acts, 'has_turns': has_turn,
            'dirty_turn': dirty, 'n': len(acts)}


def hand_trap_sides(summary, seat, turns=None):
    """手坑证据 + 座位 -> ``(my_traps, opp_traps)``（**结论**，入库的两列）。

    每项 ``{'turn', 'id', 'key', 'name_cn'}``（turn 升序、同 turn 按 seq 升序，
    顺序即事件顺序）。判不出座位 / 本局没有可用回合 -> ``(None, None)``
    （两列都 NULL，界面显示 ``—``；这与「确实没用过」的空列表 ``[]`` 不同）。
    """
    turns = HAND_TRAP_TURNS if turns is None else turns
    if seat is None or not summary or not summary.get('has_turns'):
        return None, None
    mine, opp = [], []
    for a in summary.get('activations') or []:
        t = a.get('turn')
        if t not in turns or a.get('id') is None:
            continue
        item = {'turn': t, 'id': a['id'], 'key': a['key'],
                'name_cn': _ht.name_cn(a['key'])}
        (mine if a.get('player') == seat else opp).append(item)
    return mine, opp


# ---------------------------------------------------------------- 解码

def decode_record(scan, card_names_file=None, my_deck_name=None,
                  my_account=None):
    """层 0 -> 层 1 的 duel dict（meta 只含层 0 能自给的，外部配对另注入）。"""
    record_dir = scan['dir']
    man = scan['manifest']
    payloads = man.get('payload') or {}
    bin_cache = {}

    #: ★ BUG-B2A-22：**「bin 文件缺失」与「bin 是空的」不再混同**。
    #:   两者都让事件退化成 `truncated`，但根因完全不同（采集器没写出来 vs
    #:   真没数据）—— 记进 `summary['bin_problems']` / 事件 `bin_missing` 才有出口。
    bin_problems = {}

    def bin_for(arr):
        if arr not in bin_cache:
            info = payloads.get(arr) or {}
            name = info.get('bin')
            path = os.path.join(record_dir, name) if name else None
            data = b''
            if not name:
                bin_problems[arr] = 'not-declared'      # manifest 里没说有 bin
            elif not os.path.exists(path):
                bin_problems[arr] = 'missing:' + name   # 声明了但文件不在
            else:
                try:
                    with open(path, 'rb') as f:      # 审查 P3-4：别漏关文件
                        data = f.read()
                except OSError as e:
                    bin_problems[arr] = 'unreadable:%s' % e
                    data = b''
                else:
                    if not data:
                        bin_problems[arr] = 'empty:' + name   # 文件在但是空的
            bin_cache[arr] = data
        return bin_cache[arr]

    events = []
    for seq, ent in enumerate(man.get('logbase', {}).get('entries') or []):
        label = ent.get('label')
        di = ent.get('dataindex')
        ev = {'seq': seq, 'label': label, 'dataindex': di}
        arr = LABEL_TO_ARRAY.get(label, label)
        if arr is None:                      # SeparateLine
            events.append(ev)
            continue
        size = ARRAY_SIZES.get(arr)
        if size is None:                     # 不认识的 label：登记，不猜
            ev['unknown_label'] = True
            events.append(ev)
            continue
        info = payloads.get(arr) or {}
        count = info.get('count')
        buf = bin_for(arr)
        off = (di or 0) * size
        if (di is None or di < 0
                or (count is not None and di >= count)
                or off + size > len(buf)):
            ev['truncated'] = True           # 载荷缺失/越界：留痕不炸
            #: ★ BUG-B2A-22：文件级问题（缺失/空/读不了）另外标明 —— 与
            #: `dataindex` 越界（数据级）区分开
            if arr in bin_problems:
                ev['bin_missing'] = bin_problems[arr]
            events.append(ev)
            continue
        _append_event(ev, label, arr, buf, off, count, di, size=size)
        events.append(ev)

    summ = _summary(events, payloads, bin_for)
    if bin_problems:
        #: 只登记**真被用到**的数组（没出现的 label 不算问题）
        used = {LABEL_TO_ARRAY.get(e.get('label'), e.get('label'))
                for e in events}
        summ['bin_problems'] = {k: v for k, v in sorted(bin_problems.items())
                                if k in used}
    else:
        summ['bin_problems'] = {}
    meta = _default_meta(scan, man)
    ds = man.get('duel_settings') or None
    # 座位判据 ① = 我方卡组快照（层 0 自给；判不出 NULL 不硬猜）
    snap = man.get('my_deck_snapshot')
    if snap and snap.get('ids'):
        seat, ev = infer_seat(snap['ids'], summ['revealed'])
        meta['seat'] = seat
        meta['seat_evidence'] = ev
    # 座位判据 ②（2026-09-16 追加）= ClientWork 的 `my_index`（采集器开局那拍读的）。
    # 为什么需要：活动局（骰子拉力等）没有可信快照 -> 判据①必然空，而「先/后」
    # 与对手名都挂在 seat 上，于是活动局一直显示 `—`。只认 0/1，其余不采。
    my_idx = (ds or {}).get('my_index')
    if my_idx not in (0, 1):
        my_idx = None
    if meta['seat'] is None and my_idx is not None:
        meta['seat'] = int(my_idx)
        meta['seat_evidence'] = 'clientwork-myid'
    elif my_idx is not None and meta['seat'] != my_idx:
        # 两个来源打架：保留快照那条（已验证过），但把冲突写进 evidence ——
        # 这是「ClientWork 下标空间 == 对局 seat 空间」的实机探针，别静默吞。
        meta['seat_evidence'] = '%s;clientwork-myid=%d 冲突' % (
            meta['seat_evidence'], int(my_idx))
        #: ★ BUG-B2A-14：**可观测字段**（不只是给人看的 evidence 文本）——
        #:   `seat` 判错的连带面很大（opp_revealed / went_first / 两列手坑），
        #:   所以冲突要能被打包工具筛出来复核。真库当前 0 行（无冲突样本）。
        meta['seat_conflict'] = True
    #: 座位判据 ③（2026-10-24 用户实报 21:48 局）：快照与 ClientWork 都
    #:    没读到，但 meta 里有**我方卡组名**（current-deck 兜底等来源）——
    #:    用它在参照库（mdm_deck_cards.json，纯本地）查卡表当快照替身，
    #:    再跑同一套 infer_seat 容错判定。为什么可行：我方卡组的卡只可能
    #:    出现在我那一侧的已见卡里；镜像局（两侧同卡组）照样歧义判 None，
    #:    安全性不变。参照库没有这个卡组（站外自组）就不猜。
    #:    ⚠️ decode 阶段 meta.my_deck_name 多半还空着（current-deck 是入库层
    #:    兜底的）—— 所以 decode_record 有 ``my_deck_name`` 注入参数，
    #:    入库层把兜底后的名字传进来重判（见 tools/import_duel_records.py）。
    _mdn = meta.get('my_deck_name') or my_deck_name
    if meta['seat'] is None and _mdn:
        try:
            ref_ids = _ref_ids_for(_mdn)
            if ref_ids:
                seat, ev = infer_seat(ref_ids, summ['revealed'])
                if seat is not None:
                    meta['seat'] = seat
                    meta['seat_evidence'] = 'ref-deck:' + ev
        except Exception:                                # noqa: BLE001
            pass
    #: 座位判据 ④（2026-10-24 同局第二证据）：duelSettings.names 方向已定
    #:    （by-seat，2/2 实机）且**我方账号名**（库内 account_name，入库层注入）
    #:    能对上某个座位名 —— 直接定座位。这比卡表交集更硬（游戏自己排的名）。
    #:    names[seat] == 我方名 -> seat 就是它；对不上不猜。
    if meta['seat'] is None and my_account             and ds and ds.get('names_direction') == 'by-seat':
        _names = [str(n or '') for n in (ds.get('names') or [])]
        if len(_names) == 2:
            me = str(my_account)
            for i, n in enumerate(_names):
                if n == me:
                    meta['seat'] = int(i)
                    meta['seat_evidence'] = 'account-name:names[%d]==%s' % (i, me)
                    break
    if meta['seat'] is not None:
        summ['opp_revealed_ids'] = summ['revealed'][
            'p1' if meta['seat'] == 0 else 'p0']
    # 对手名（M6 定案：duelSettings names 按座位排，2/2 实机样本）——
    # seat 判出后 opp = names[1-seat]；seat 判不出保持 NULL（不硬猜）。
    if ds and ds.get('names_direction') == 'by-seat' and meta['seat'] is not None:
        _names = ds.get('names') or []
        if len(_names) == 2:
            opp = _names[1 - int(meta['seat'])]
            if opp:
                meta['opp_player_name'] = opp
    # M4：卡表里查不到的卡 id（unknown = 卡表过期的信号；只统计已解码字段）。
    # 卡表一变 -> 指纹变 -> 整份重写 -> 这里的值跟着刷新（P1-1）。
    summ['unknown_card_ids'] = find_unknown_card_ids(
        events, summ, card_name_table(card_names_file))

    duel = {
        'format_version': FORMAT_VERSION,

        'record_id': scan['name'],
        'merged_from': [],
        'decoded_at': fmt_dt(datetime.now()),
        'state': scan['state'],
        'state_src': scan['state_src'],
        'capture': capture_group(record_dir, man),
        'source_fingerprint': fingerprint(record_dir, card_names_file),
        'meta': meta,
        'summary': summ,
        'events': events,
    }
    return duel


def _append_event(ev, label, arr, buf, off, count, di, size=None):
    """按 label 类型把载荷解进事件 dict（就地修改）。

    ``size``：调用方**已经校验过**的单条元素字节数（来自 :data:`ARRAY_SIZES`）。
    ★ BUG-B2A-10：raw 分支原来用 `size_of(arr)` 二次查表 —— 同一个 dict 的
    两次查询，`size_of` 又是公开函数，别处一改就与校验过的 `size` 漂移。
    现在统一用传进来的这一份；调用方没给才回退查表（只为老签名兼容）。
    """
    if label == 'ShowAction':
        entry = _dl.parse_entry(buf, off)
        for tag, side in (('l', entry.datal), ('r', entry.datar)):
            if side.is_empty:
                continue
            for cid in side.card_ids():
                events_multi = ev.setdefault('cards', [])
                events_multi.append({
                    'card_id': cid,             # 已是低 16 位
                    'alt_id': side.alt_id,      # 高 16 位 = 标准版（单独字段）
                    'player': side.player,
                    'side': tag,
                })
        if 'cards' in ev:
            first = entry.datal if not entry.datal.is_empty else entry.datar
            ev['kind'] = entry.datac.databyte1
            ev['kind_name'] = _dl.action_kind_name(entry.datac.databyte1)
            ev['zone'] = first.zone
            ev['zone_name'] = _dl.zone_name(first.zone)
            ev['player'] = first.player
            ev['boolbits'] = first.boolbits
            ev['flags'] = {
                'show': bool(first.boolbits & _dl.BOOL_SHOW),
                'insight': bool(first.boolbits & _dl.BOOL_INSIGHT),
                'indent': bool(first.boolbits & _dl.BOOL_INDENT),
                # bit1 FACE 语义待定，不进 flags（别猜）
            }
        else:
            ev['empty'] = True
        return
    if label == 'ShowTurn':
        tr = _dl.parse_turns(buf, count)[di]
        ev.update({'player': tr.player, 'turn': tr.turn,
                   'lp_a': tr.lp_a, 'lp_b': tr.lp_b})
        return
    if label == 'ShowPhase':
        code = _dl.parse_phases(buf, count)[di]
        ev.update({'phase': code,
                   'phase_name': _dl.PHASE_NAMES.get(code, 'Phase%d' % code)})
        return
    if label == 'ShowChain':
        ce = _dl.parse_chains(buf, count)[di]
        ev.update({'chain_index': ce.chain_index, 'card_id': ce.card_id,
                   'side': ce.side, 'flag2': ce.flag2})
        return
    if label == 'ShowCardName':
        ce = _dl.parse_card_names(buf, count)[di]
        ev.update({'card_id': ce.card_id, 'side': ce.side,
                   'flag1': ce.flag1, 'flag2': ce.flag2})
        return
    if label in RAW_INT_LABELS or arr in RAW_INT_LABELS:
        ev['raw'], = struct.unpack_from('<i', buf, off)
        return
    if label in RAW_HEX_LABELS or arr in RAW_HEX_LABELS:
        n = size if size is not None else size_of(arr)
        ev['raw_hex'] = bytes(buf[off:off + n]).hex()
        return
    ev['unknown_label'] = True


def size_of(arr):
    return ARRAY_SIZES.get(arr)


def _default_meta(scan, man):
    """层 0 能自给的 meta；外部（时间/胜负/模式）由 import 工具注入。"""
    snap = man.get('my_deck_snapshot') or None
    meta = {
        'started_at': None, 'started_at_src': None,
        'ended_at': None, 'duration_sec': None,
        'game_mode': man.get('game_mode'),       # M6 契约：采集时顺手写
        'mode_group': None,
        'result': None, 'result_src': None,
        'winbits': None, 'result_note': None,
        'opp_player_name': None,
        'my_deck_id': None, 'my_deck_name': None, 'my_deck_name_src': None,
        'seat': None, 'seat_evidence': None,
        #: ★ BUG-B2A-14：座位两来源（快照 vs ClientWork）冲突标记 ——
        #: True 才有意义，None = 没冲突/判不出。见 decode_record 的冲突分支。
        'seat_conflict': None,
        'note': man.get('note'),
        'sources': {'log': None, 'end_messages': None,
                    'duel_modes': None, 'capture': None, 'manual': []},
    }
    if snap:
        meta['my_deck_id'] = snap.get('deck_id')
        meta['my_deck_name'] = snap.get('deck_name')
    # 对手名（M6 duel_settings 中性对）：**方向定案前绝不写方向性语义** ——
    # matches.opp_player_name 的既有语义是「对手玩家名」，方向搞反 = 张冠李戴
    # （docs/M6计划.md §三 P1-2 / docs/M6计划终审意见.md §二）。方向实机定案
    # （M6 验收 5）后把 names_direction 写进契约，这里才允许按方向取名。
    ds = man.get('duel_settings') or None
    if ds and ds.get('names_direction') == 'index0=opp':
        names = ds.get('names') or []
        if names and names[0]:
            meta['opp_player_name'] = names[0]
    return meta


#: `ShowTurn` 残渣判据的 LP 合法区间。
#: 上界 20000：实测真库有局 LP 到 10000（规则伤害/支付后回血），
#: 设 8000 会误伤 24 局（实测踩过）；下界 1：没有正常局首回合 LP 是 0。
TURN_LP_MAX = 20000


def _turn_entry_is_residue(t):
    """单条 `ShowTurn` 是不是**控制器内存残渣**（是 -> True）。

    判据（全库实测，见 :func:`_first_turn_is_residue`）：
    `player ∈ {0,1}` + `0 ≤ turn < 200` + `1 ≤ lp ≤ TURN_LP_MAX`。
    **不含**「双方 LP 相等」那条 —— 那是只对**首条**成立的额外判据
    （对局中途双方 LP 本来就会不同）。
    """
    try:
        player = int(t.get('player'))
        turn = int(t.get('turn'))
        lp_a = int(t.get('lp_a'))
        lp_b = int(t.get('lp_b'))
    except (TypeError, ValueError, AttributeError):
        return True                      # 读不出数 = 不可信
    if player not in (0, 1):
        return True
    if not (0 <= turn < 200):
        return True
    return not (1 <= lp_a <= TURN_LP_MAX and 1 <= lp_b <= TURN_LP_MAX)


def _first_turn_is_residue(turns):
    """首条 `ShowTurn` 是不是**控制器内存残渣**（是 -> True）。

    ★ 2026-10-20（用户实报「我先手却判成后手」）：`record_20261020_215925`
    那局日志明写 `Turn 1 for player 1`（我方先手），但内存侧 `ShowTurn`
    解出 `player=0 / turn=1 / marker=2 / lp_a=2 / lp_b=3` —— 24 字节里装的
    全是残渣（控制器内存被复用，`m_DataList_ShowTurn` 没被本局写过）。
    于是 `first_player=0`、`went_first` 判成后手。

    在单条判据之外再加一条**只对首条成立**的：**对局首回合双方 LP 必须相等**
    —— 开局双方同 LP，速决胜斗（500/2000/4000）也相等；残渣则是
    `(622,0)` / `(597,1)` / `(0,0)` / `(2,3)` 这种不相等或离谱的值。
    实测这一条能抓住「单条判据看不出来」的残渣（21:59 那局每条单独看都合法）。
    """
    if not turns:
        return False                     # 没回合事件 = 另一回事（不猜）
    t = turns[0]
    if _turn_entry_is_residue(t):
        return True
    try:
        return int(t.get('lp_a')) != int(t.get('lp_b'))
    except (TypeError, ValueError):
        return True


def _turns_is_residue(turns):
    """整条 `ShowTurn` 链里**任一条**是残渣 -> True（是 -> 整条作废）。

    ★ 2026-10-20 追加：光查首条不够 —— `record_20261010_203819` 首条
    `lp=(597,1)` 已被首条判据抓住，但它**后面几条同样污染**
    （`player=1304766336` / `lp_b=-1737342400`），`total_turns` 和 `lp_final`
    都是垃圾。用户在 untapped 上查到那局是「先手 5 回合」，而库里记成后手。

    全库实测（322 局）：整链校验命中 **5 局**，全是真残渣、零误伤
    （LP 上界放到 20000 之后；设 8000 会误伤 24 局正常对局）。
    """
    if not turns:
        return False
    return any(_turn_entry_is_residue(t) for t in turns)         or _first_turn_is_residue(turns)


def _summary(events, payloads, bin_for):
    turns = [e for e in events
             if e['label'] == 'ShowTurn' and 'player' in e]
    #: ★ 2026-10-20：ShowTurn 链里有控制器残渣时**整条作废**
    #: （`first_player` / `total_turns` / `lp_final` 全部不可信），
    #: 宁可判不出（None）也不要把残渣当先手证据 —— 用户实报「我先手却判成
    #: 后手」就是这么来的（record_20261020_215925 / record_20261010_203819）。
    if _turns_is_residue(turns):
        turns = []
    info = payloads.get('ShowAction') or {}
    entries = []
    if info.get('count'):
        buf = bin_for('ShowAction')
        try:
            entries = _dl.parse_entries(buf, info['count'])
        except (ValueError, struct.error):
            entries = []
    revealed = {
        'p0': _dl.revealed_by(entries, 0),
        'p1': _dl.revealed_by(entries, 1),
    }
    summ = {
        'first_player': turns[0]['player'] if turns else None,
        'went_first': None,          # seat 判出后由 import 工具映射（不猜）
        'total_turns': len(turns),
        # ⚠️ lp_a/lp_b 是中性命名（哪边是 P0 未验证，别混概念）
        'lp_final': ({'a': turns[-1]['lp_a'], 'b': turns[-1]['lp_b']}
                     if turns else None),
        'revealed': revealed,
        'opp_revealed_ids': None,    # seat 判出后才映射
        # 2026-10-10 实机（record_20261010_193859）：控制器内存被复用时
        # read_sysstr 打印性过滤会让 label 变 None/乱码单字，None 混进
        # sorted() 直接 TypeError（'<' not supported str/None）—— 整局
        # import 失败。归一成 '#unknown' 保排序，计数照留（不吞信息）。
        'counts': dict(sorted(
            (('#unknown' if k is None else k), v)
            for k, v in _count_labels(events).items())),
    }
    summ['hand_traps'] = summarize_hand_traps(events)
    return summ


def _count_labels(events):
    out = {}
    for e in events:
        out[e['label']] = out.get(e['label'], 0) + 1
    return out


# ---------------------------------------------------------------- 座位

#: 参照库卡表缓存（判据 ③ 专用；进程内一份，第一次用时加载）
_REF_DECK_CACHE = {}


def _ref_ids_for(deck_name):
    """我方卡组名 -> 参照库卡表的卡 id 集合（查不到返回 None；**不抛**）。

    名字先过一遍「显示名」常见叫法（meta 清单的 name / name_cn）再查。
    """
    if deck_name in _REF_DECK_CACHE:
        return _REF_DECK_CACHE[deck_name]
    out = None
    try:
        import json
        import os
        from core import paths
        p = paths.resolve('mdm_deck_cards.json')
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                decks = (json.load(f) or {}).get('decks') or {}
            # 直接名 / 别的条目的 name_cn 同名（current-deck 兜底的是显示名）
            hit = decks.get(deck_name)
            if hit is None:
                for dn, info in decks.items():
                    if isinstance(info, dict) and (
                            info.get('name_cn') == deck_name
                            or dn == deck_name):
                        hit = info
                        break
            if isinstance(hit, dict):
                cards = hit.get('cards') or {}
                out = set(str(k) for k in cards)
    except Exception:                                    # noqa: BLE001
        out = None
    _REF_DECK_CACHE[deck_name] = out
    return out


def infer_seat(snapshot_ids, revealed):
    """纯函数：我方卡组快照 vs 双方已见卡 -> (seat, evidence)。

    **容错匹配**：算「该侧已见卡被快照覆盖的包含度」，不要求精确子集
    （骰子拉力的换卡、手坑抽换会让子集判据失败）。
    **歧义判 None**：覆盖率不足或两侧接近（镜像局）都不猜 —— 座位错了
    opp_revealed 就全错，宁可 NULL（result 不依赖 seat，winbit 是视角方的）。
    """
    snap = set(snapshot_ids or [])
    if not snap:
        return None, 'no-snapshot'
    scores, hits = {}, {}
    for side in ('p0', 'p1'):
        cards = set(revealed.get(side) or [])
        if not cards:
            continue
        hits[side] = len(snap & cards)
        scores[side] = hits[side] / float(len(cards))
    if not scores:
        return None, 'no-revealed-cards'
    best = max(scores, key=scores.get)
    others = [v for k, v in scores.items() if k != best]
    margin = scores[best] - (max(others) if others else 0.0)
    fmt = lambda s: '%s=%d/%d' % (s.upper(), hits.get(s, 0),
                                  len(set(revealed.get(s) or [])))
    evidence = 'snapshot-hit %s %s cover=%.2f margin=%.2f' % (
        fmt('p0'), fmt('p1'), scores[best], margin)
    if hits[best] < SEAT_MIN_HITS:
        return None, evidence + ' -> thin-evidence'
    if scores[best] < SEAT_MIN_COVER:
        return None, evidence + ' -> low-coverage'
    if margin < SEAT_MIN_MARGIN:
        return None, evidence + ' -> ambiguous'
    seat = 0 if best == 'p0' else 1
    return seat, evidence


# ---------------------------------------------------------------- duel.json 读写

def read_duel_json(record_dir):
    path = os.path.join(record_dir, DUEL_JSON_NAME)
    try:
        with open(path, 'rb') as f:
            return json.loads(f.read().decode('utf-8'))
    except (OSError, ValueError):
        return None


def _dump_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=1)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text + '\n')


def _merge_meta(base, updates, manual_keys):
    """updates 只补 base 里为 None 的键；manual 列表里的键可覆盖。"""
    merged = copy.deepcopy(base)
    changed = False
    for k, v in (updates or {}).items():
        if v is None:
            continue
        old = merged.get(k, None)
        if old is None or k in (manual_keys or ()):
            if old != v:
                changed = True
                merged[k] = v
    return merged, changed


def write_duel_json(record_dir, decoded, meta_updates=None):
    """层 1 落盘。返回 'written' / 'updated' / 'skipped'。

    * **指纹没变且 meta 无新增** -> 'skipped'（不写文件，防 mtime 抖动）；
    * 指纹变了 -> 用新解码结果重写，但 **meta 合并保留**（只补 NULL、
      ``meta.sources.manual`` 列过的键可覆盖）—— 幂等重解不丢已注入的 meta；
    * 指纹没变但 meta 有新值可补 -> 'updated'。
    """
    path = os.path.join(record_dir, DUEL_JSON_NAME)
    old = read_duel_json(record_dir)
    old_meta = (old or {}).get('meta') or {}
    manual_keys = ((old_meta.get('sources') or {}).get('manual') or [])
    if old is not None and old.get('source_fingerprint') == decoded.get(
            'source_fingerprint'):
        merged_meta, meta_changed = _merge_meta(
            _default_meta_from(old_meta), meta_updates, manual_keys)
        if not meta_changed:
            return 'skipped'
        old['meta'] = merged_meta
        # summary 是**派生值**（seat 变了 went_first/opp_revealed_ids 就该跟着变），
        # 所以这里要连 decoded 的 summary 一起刷 —— 只写 meta 会留下
        # 「DB 有、层 1 文件没有」的落差（2026-09-16 实机：went_first 就卡在这）。
        if decoded.get('summary') is not None:
            old['summary'] = decoded['summary']
        _dump_json(path, old)
        return 'updated'
    data = dict(decoded)
    # base = 默认 + 既有值（重解不丢已注入） + 层 0 自给值垫底
    base = _default_meta_from(old_meta)
    for k, v in (decoded.get('meta') or {}).items():
        if v is None:
            continue
        cur = base.get(k)
        if isinstance(v, dict) and isinstance(cur, dict):
            # sources 之类的嵌套 dict：逐键垫底，不整体覆盖
            for sk, sv in v.items():
                if cur.get(sk) is None and sv is not None:
                    cur[sk] = sv
        elif cur is None:
            base[k] = v
    data['meta'], _ = _merge_meta(base, meta_updates, manual_keys)
    _dump_json(path, data)
    return 'written'


def _default_meta_from(old_meta):
    """给既有 meta 补齐缺的键（老版本 duel.json 兼容）。"""
    base = _default_meta(None, {})
    base.update({k: v for k, v in (old_meta or {}).items()})
    src = dict(base.get('sources') or {})
    src.setdefault('log', None)
    src.setdefault('end_messages', None)
    src.setdefault('duel_modes', None)
    src.setdefault('capture', None)
    src.setdefault('manual', [])
    base['sources'] = src
    return base


# ---------------------------------------------------------------- 层 2 出行

_RESULT_LABEL = {'win': 'Win', 'lose': 'Lose'}


def _mode_of(game_mode):
    """game_mode -> matches.mode。

    * ``7``  = 观战 / 回放 -> ``live``（**不是对局**，不入库）
    * ``9``  = 单人模式 -> ``solo``（2026-09-26 用户实测拍板；id 口径见
      ``core.storage.MODE_GROUP_BY_GAME_MODE``）
    * 其余（天梯 3 / 巅峰 19 / DC 12 / 各活动）-> ``pvp``
    * 判不出 -> ``unknown``

    ⚠️ 2026-10-08 抓到：这里原先**只认 7**，把 ``game_mode=9`` 的行全记成了
    ``pvp``（库里 8 行），与日志线的 ``solo`` 对不上 —— 用户 10-07 的单人局
    就是这么暴露的。**单人的 id 一律问 `core.storage`，别在这儿硬编码。**
    """
    if game_mode is None:
        return 'unknown'
    gm = int(game_mode)
    if gm == 7:
        return 'live'
    if MODE_GROUP_BY_GAME_MODE.get(gm) == MODE_GROUP_SOLO:
        return 'solo'
    return 'pvp'


def derive_result(winbits):
    """winbits（[player0 位, player1 位]，player0 恒视角方）-> 'Win'/'Lose'/None。

    两 bit 相同 -> None 不硬猜（掉线/平局）；大小写对齐 matches 的 DONE_RESULTS。
    """
    if not winbits or len(winbits) < 2:
        return None
    r = derive_result_from_winbits(winbits[0], winbits[1])
    return _RESULT_LABEL.get(r)


def _display_deck_name(name):
    """卡组名（**任意叫法**）→ meta 清单里的中文显示名；查不到返回 None。

    给 `to_row` 的快照名字归一用：游戏内自组名（如「狱神神艺」）落在覆盖表
    某条的别名上时，库内应存**显示名**（「神艺」），这样界面搜索 / 配图 /
    统计分桶都走同一条路。与 `tools/import_duel_records._meta_display` 同口径，
    但那边要 storage 实例，这里懒建一个（只在有快照名时调，开销可忽略）。

    ⚠️ 查不到**必须**返回 None（调用方保留原值）—— 自组名在参照库里没有时，
    原样保留比丢信息。
    ⚠️ 命中判据是「**五字段里有一模一样的叫法**」（name / name_display /
    name_cn / name_ja / aliases），不是模糊匹配 —— `search_meta_decks` 会做
    近似匹配，别名「狱神神艺」命中时 `name` 是 `Artmage`（≠查询词），
    只比 `name` 会漏掉全部别名命中（2026-10-17 踩过）。
    """
    key = str(name).strip()
    if not key:
        return None
    if key in _DISPLAY_NAME_CACHE:
        return _DISPLAY_NAME_CACHE[key]
    got = None
    try:
        global _DISPLAY_STORAGE
        if _DISPLAY_STORAGE is None:
            from core.storage import Storage
            _DISPLAY_STORAGE = Storage()
        storage = _DISPLAY_STORAGE
        for d in (storage.search_meta_decks(key, limit=5) or []):
            names = [str(d.get(k) or '').strip()
                     for k in ('name', 'name_display', 'name_cn', 'name_ja')]
            names += [str(a or '').strip()
                      for a in (d.get('aliases') or [])]
            if key in [n for n in names if n]:
                got = (d.get('name_cn') or d.get('name_display')
                       or d.get('name'))
                break
    except Exception:                                        # noqa: BLE001
        return None                    # 读失败不缓存（下次还要试）
    _DISPLAY_NAME_CACHE[key] = got
    return got


def refresh_display_name_cache() -> None:
    """清掉卡组名归一缓存（正式库据变了、要重算时调）。"""
    _DISPLAY_NAME_CACHE.clear()


def to_row(duel):
    """duel.json -> matches 行形状（origin='record'；入库判定在 import 工具）。"""
    from core.card_names import display
    meta = duel.get('meta') or {}
    summ = duel.get('summary') or {}
    seat = meta.get('seat')
    first_player = summ.get('first_player')
    opp_ids = list(summ.get('opp_revealed_ids') or []) \
        if seat is not None else None
    opp_names = None
    if opp_ids:
        opp_names = [display(cid, 'cn') or ('#%d' % cid) for cid in opp_ids]
    result = meta.get('result') or derive_result(meta.get('winbits'))
    result_src = meta.get('result_src') or (
        'winbit' if result and meta.get('winbits') else None)
    went_first = None
    if seat is not None and first_player is not None:
        went_first = bool(int(seat) == int(first_player))
    # 卡组快照只在「玩家必选常规卡组」的模式里可信（活动局读到的是大厅卡组）——
    # 判据用**注入/兜底之后的 game_mode**（这是本函数独有的信息优势，见
    # docs/活动局快照防线方案.md §七）。判不出（None）也不采信，与采集侧同口径。
    # 采信规则（2026-09-16 细化）：**模式/容器只约束「快照来源」的名字** ——
    # * 来源是「当前卡组」（编辑界面读的）或 M5 整副牌猜的 -> 一律采信（活动局正是靠它们）；
    # * 来源是快照 -> 老口径：3/19 信；其它模式只有「不是大厅 Deck 容器」才信（老坑见
    #   docs/活动局快照防线方案.md），这样大厅卡组冒充活动卡组的情况仍然挡得住。
    _src = meta.get('my_deck_name_src')
    _snap = meta.get('my_deck_snapshot') or {}
    _cont = _snap.get('container') if isinstance(_snap, dict) else None
    _mode = meta.get('game_mode')
    if _src in ('current-deck', 'm5-guess'):
        trusted = True
    elif _mode in DECK_SNAPSHOT_TRUSTED_MODES:
        trusted = True
    elif _mode is not None and _cont and _cont != 'Deck':
        trusted = True
    else:
        trusted = False
    _ht = summ.get('hand_traps') or {}
    _ht_mine, _ht_opp = hand_trap_sides(_ht, seat)
    snap_id = meta.get('my_deck_id') if trusted else None
    snap_name = meta.get('my_deck_name') if trusted else None
    # ★ 2026-10-17：快照名字也可能是**游戏内自组名**（用户卡组叫「狱神神艺」，
    #   站内条目叫 `Artmage`）—— 原样进库的话界面既搜不到也配不到图。
    #   所以先过一次 meta 清单的显示名归一（`_meta_display` 同一条路径）；
    #   查不到（真是自组的、参照库没有）就保留原值，绝不丢信息。
    if snap_name and snap_id is not None:
        snap_name = _display_deck_name(snap_name) or snap_name
    return {
        'match_id': 'rec:' + duel['record_id'],
        'record_dir': duel['record_id'],
        'started_at': meta.get('started_at'),
        'ended_at': meta.get('ended_at'),
        'duration_sec': meta.get('duration_sec'),
        'my_deck_id': snap_id,
        'my_deck_name': snap_name,
        # 名字来源（2026-09-16）：snapshot-name / m5-guess / current-deck —— 猜的必须留痕
        'my_deck_name_src': meta.get('my_deck_name_src'),
        # 三值：snapshot=读到且已采信 / snapshot-untrusted=读到但模式不可信（活动局）/ unknown=没读到
        'deck_source': ('snapshot' if snap_id else
                        ('snapshot-untrusted' if meta.get('my_deck_id')
                         else 'unknown')),
        'mode': _mode_of(meta.get('game_mode')),
        'game_mode': meta.get('game_mode'),
        'origin': 'record',
        'seat': seat,
        'seat_evidence': meta.get('seat_evidence'),
        'won_initiative': None,
        'went_first': went_first,
        'first_player': first_player,
        'result': result,
        'result_src': result_src,
        'total_turns': summ.get('total_turns'),
        'opp_player_name': meta.get('opp_player_name'),
        'result_note': meta.get('result_note'),
        'winbits': meta.get('winbits'),
        'opp_revealed': opp_names,
        'opp_revealed_ids': opp_ids,
        # 手坑两列（JSON 文本存 list；seat 判不出 -> None 两列 NULL）
        'my_traps': _ht_mine,
        'opp_traps': _ht_opp,
    }
