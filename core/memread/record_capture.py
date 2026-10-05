# -*- coding: utf-8 -*-
"""层 0 采集器内核 —— DuelLogController -> record_* 目录（M6 三 watcher 合一）。

tmp/save_duel_record.py + tmp/watch_save_duel_record.py 的内核搬入（产品化）：

* **原始字节全保存、解码以后慢慢来** —— 未解语义数组照样存原始字节；
* manifest 走 M6 契约 v1（``docs/对局记录格式.md`` §1.2）：``manifest_version`` /
  ``capture{first_seen_at,last_saved_at,first_logbase_size}`` / ``state`` /
  ``my_deck_snapshot`` / ``game_mode`` / ``duel_settings``（中性对，可选键）；
* **「一局一目录」复用三条件**（契约 §1.3）与 **stale open 判据**是纯函数，
  可单测；它们是 import 侧 ``group_duels`` / 终局化通道（P1-1）的采集侧对应物；
* :class:`ControllerCache` 只负责「省扫描」，对象头校验防野指针；
  **地址跨局复用的换局判定归状态机**（duel_start 强制作废缓存），不在本模块；
* 收尾：对局中「logbase.size 变大才全量落盘（同目录覆盖）」、收局标 final、
  掉线/优雅退出的终局化口径见计划 §2.1。

⚠️ 已知元素尺寸从 :mod:`core.memread.duel_log` import（契约 §2.2 同源，
回归单测盯着，别在这里手抄数字）。
"""
import json
import os
import struct
import time
from datetime import datetime

from core.duel_record import MERGE_GAP_SEC, load_manifest, state_of
from core.memread.duel_log import (CARD_NAME_SIZE, CHAIN_SIZE, ENTRY_SIZE,
                                   MULTI_HEADER_SIZE, PHASE_SIZE, TURN_SIZE)
from core.memread.il2cpp import list_fields
from core.memread.il2cpp_obj import MIN_ADDR, read_string

#: IL2CPP 数组：对象头 0x10 + bounds 8 + length 8 = 数据从 0x20 起
ARRAY_DATA = 0x20
#: m_LogBaseItemList 的元素尺寸（+0 label 字符串指针 +8 dataindex）
LOGBASE_ELEMENT = 32
#: 未解语义数组的超量读取：容量 × 这个上限
UNKNOWN_STRIDE_MAX = 32
#: 单数组最大读取字节数（防容量被垃圾值撑爆）
MAX_ARRAY_BYTES = 8 << 20
#: 未知 stride 数组的容量读取上限
CAP_READ_LIMIT = 4096

#: 各载荷数组的已知元素尺寸 —— **与 duel_log.py 同源**（数字只在那边钉死）
KNOWN_SIZES = {
    'ShowAction': ENTRY_SIZE,
    'ShowTurn': TURN_SIZE,
    'ShowPhase': PHASE_SIZE,
    'ShowChain': CHAIN_SIZE,
    'ShowCardName': CARD_NAME_SIZE,
    'ShowText': 4,
    'ShowTag': 4,
    'ChoiceFirstPlayer': 4,
    'MoveMultiHeader': MULTI_HEADER_SIZE,
    'MoveMulti': 8,               # 指针数组
}
#: 抓取顺序（与 tmp 内核一致）
PAYLOAD_ARRAYS = ('ShowTurn', 'ShowAction', 'ShowChain', 'ShowCardName',
                  'ShowText', 'ShowPhase', 'ShowTag', 'MoveMultiHeader',
                  'MoveMulti', 'ChoiceFirstPlayer')

#: manifest 契约版本（新增可选键不升版本；改语义/删键才升，见计划 §三）
MANIFEST_VERSION = 1
#: stale open 的默认时限（启动自愈 / import --finalize-stale 共用）
STALE_OPEN_SEC = 6 * 3600


# ---------------------------------------------------------------- 内存读取

def read_sysstr(proc, addr, maxlen=64):
    """loglabel 是 System.String（UTF-16）不是 C 字符串 —— tmp 内核踩过的坑。"""
    return read_string(proc, addr, maxlen=maxlen, min_printable=1.0)


def list_meta(proc, mirror, list_obj):
    """List<T> -> (count, items_addr)。字段按名查，不硬编码；读不到 (0, 0)。"""
    if not list_obj or list_obj < MIN_ADDR:
        return 0, 0
    try:
        fl = {f.name: f.offset
              for f in list_fields(proc, proc.read_ptr(list_obj),
                                   mirror=mirror)}
        if '_items' not in fl or '_size' not in fl:
            return 0, 0
        items = proc.read_ptr(list_obj + fl['_items'])
        size = struct.unpack('<i', proc.read(list_obj + fl['_size'], 4))[0]
    except Exception:
        return 0, 0
    return (size, items) if size > 0 and items and items >= MIN_ADDR else (0, 0)


def array_capacity(proc, items):
    """IL2CPP 数组的长度在 +0x18。"""
    try:
        n, = struct.unpack('<q', proc.read(items + 0x18, 8))
        return max(0, n)
    except Exception:
        return 0


def find_controller(proc, mirror, tbl, ti):
    """双重过滤找 DuelLogController 实例（自身类名 + m_Host 类名）。

    这是唯一需要全内存扫描的地方（控制器无静态单例）；调用方拿到结果后
    缓存进 :class:`ControllerCache`，对局中只做对象头校验 + 顺指针读。
    """
    try:
        fl = {f.name: f.offset for f in list_fields(proc, ti, mirror=mirror)}
    except Exception:
        return None, None
    if 'm_Host' not in fl:
        return None, None
    for hit in proc.scan(struct.pack('<Q', ti)):
        try:
            if proc.read_ptr(hit) != ti:
                continue
            if _class_name(proc, hit) != 'DuelLogController':
                continue
            host = proc.read_ptr(hit + fl['m_Host'])
            if not host or host < MIN_ADDR:
                continue
            if _class_name(proc, host) != 'DuelClient':
                continue
            return hit, fl
        except Exception:
            continue
    return None, None


def _class_name(proc, obj):
    """对象头 TypeInfo 的类名（``Il2CppClass.name`` 是 char*，在 +0x10）。"""
    if not obj or obj < MIN_ADDR:
        return ''
    try:
        ti = proc.read_ptr(obj)
        return proc.read_cstring(proc.read_ptr(ti + 0x10), 96)
    except Exception:
        return ''


class ControllerCache(object):
    """DuelLogController 实例地址缓存（省扫描用）。

    ``valid()`` 做对象头校验（``*obj == TypeInfo``）防野指针；
    ⚠️ 校验通过 ≠ 还是同一局 —— 换局判定归状态机（duel_start 事件强制作废）。
    """

    def __init__(self):
        self.obj = None
        self.fields = None

    def valid(self, proc, ti):
        if not self.obj:
            return False
        try:
            return proc.read_ptr(self.obj) == ti
        except Exception:
            return False

    def store(self, obj, fields):
        self.obj, self.fields = obj, fields

    def invalidate(self):
        self.obj = None
        self.fields = None


def read_state(proc, mirror, obj, fl=None):
    """把控制器当前的对局记录读成内存态（不落盘）。

    返回 ``{'logbase': {...}, 'payload': {数组名: {..., 'raw': bytes}},
    'subs': [...]}``；控制器没就绪（logbase 空）给 None。
    """
    if fl is None:
        fl = {f.name: f.offset
              for f in list_fields(proc, proc.read_ptr(obj), mirror=mirror)}
    size, items = list_meta(proc, mirror,
                            proc.read_ptr(obj + fl['m_LogBaseItemList']))
    if not size or not items:
        return None
    raw = proc.read(items + ARRAY_DATA, size * LOGBASE_ELEMENT)
    entries = []
    for i in range(size):
        sptr, = struct.unpack_from('<Q', raw, i * LOGBASE_ELEMENT)
        didx, = struct.unpack_from('<i', raw, i * LOGBASE_ELEMENT + 8)
        entries.append({'label': read_sysstr(proc, sptr), 'dataindex': didx})
    payload = {}
    for lb in PAYLOAD_ARRAYS:
        key = 'm_DataList_%s' % lb
        if key not in fl:
            continue
        psize, pitems = list_meta(proc, mirror, proc.read_ptr(obj + fl[key]))
        if not psize or not pitems:
            payload[lb] = {'count': psize or 0}
            continue
        stride = KNOWN_SIZES.get(lb)
        if stride:
            read_n = psize * stride
        else:
            read_n = (min(array_capacity(proc, pitems), CAP_READ_LIMIT)
                      * UNKNOWN_STRIDE_MAX)
        if read_n <= 0 or read_n > MAX_ARRAY_BYTES:
            payload[lb] = {'count': psize}
            continue
        praw = proc.read(pitems + ARRAY_DATA, read_n)
        payload[lb] = {'count': psize, 'stride': stride,
                       'bin': 'm_DataList_%s.bin' % lb,
                       'bytes': len(praw), 'raw': praw}
    subs = []
    mm = payload.get('MoveMulti') or {}
    if mm.get('count') and mm.get('raw'):
        praw = mm['raw']
        for i in range(mm['count']):
            sub, = struct.unpack_from('<Q', praw, i * 8)
            if sub < MIN_ADDR:
                continue
            scap = array_capacity(proc, sub)
            if scap <= 0 or scap > CAP_READ_LIMIT:
                continue
            sraw = proc.read(sub + ARRAY_DATA, scap * UNKNOWN_STRIDE_MAX)
            subs.append({'index': i, 'capacity': scap,
                         'bin': 'm_MoveMulti_sub_%d.bin' % i,
                         'bytes': len(sraw), 'raw': sraw})
    return {'logbase': {'size': size, 'entries': entries},
            'payload': payload, 'subs': subs}


# ---------------------------------------------------------------- 层 0 落盘

def write_record(out_dir, state_data, *, instance_hex, state, first_seen_at,
                 first_logbase_size, last_saved_at=None, note='',
                 my_deck_snapshot=None, game_mode=None, duel_settings=None):
    """把 :func:`read_state` 的结果落成契约 v1 目录（同目录覆盖 = 同局继续长）。

    ``my_deck_snapshot`` 是契约必填可空键 —— 读不到也要写 ``null`` 占位；
    ``duel_settings`` 是 v2 新增可选键（中性对，方向未定不写方向性语义）。
    返回写出的 manifest dict。
    """
    os.makedirs(out_dir, exist_ok=True)
    now = last_saved_at or time.strftime('%Y-%m-%d %H:%M:%S')
    man = {
        'manifest_version': MANIFEST_VERSION,
        'saved_at': now,               # 兼容存量键（= capture.last_saved_at）
        'note': note or '',
        'instance': instance_hex,
        'capture': {'first_seen_at': first_seen_at, 'last_saved_at': now,
                    'first_logbase_size': first_logbase_size},
        'state': state,
        'element_sizes': dict(KNOWN_SIZES),
        'unknown_stride_max': UNKNOWN_STRIDE_MAX,
        'logbase_element': LOGBASE_ELEMENT,
        'logbase': {'size': state_data['logbase']['size'],
                    'entries': state_data['logbase']['entries']},
        'payload': {},
        'my_deck_snapshot': my_deck_snapshot,
        'game_mode': game_mode,
        'duel_settings': duel_settings,
    }
    payload_out = {}
    for lb, info in state_data['payload'].items():
        raw = info.get('raw')
        if raw is not None:
            with open(os.path.join(out_dir, info['bin']), 'wb') as f:
                f.write(raw)
        payload_out[lb] = {k: v for k, v in info.items() if k != 'raw'}
    man['payload'] = payload_out
    subs = []
    for sub in state_data.get('subs') or []:
        with open(os.path.join(out_dir, sub['bin']), 'wb') as f:
            f.write(sub['raw'])
        subs.append({k: v for k, v in sub.items() if k != 'raw'})
    if subs:
        man['move_multi_subs'] = subs
    _dump_manifest(out_dir, man)
    return man


def _dump_manifest(out_dir, man):
    with open(os.path.join(out_dir, 'manifest.json'), 'w',
              encoding='utf-8', newline='') as f:
        json.dump(man, f, ensure_ascii=False, indent=1)


# ------------------------------------------- 一局一目录：复用与终局化（纯函数）

def find_reusable_dir(scans, instance, now, logbase_size, gap_sec=None):
    """契约 §1.3 三条件：可复用的 open 目录名，复用不了给 None。

    **缺一新建、宁可多并、不可静默覆盖**：
    1. 对方目录 ``state=open``（final 的绝不碰）；
    2. ``instance`` 相同 且 时间邻近（``now`` 距 capture 区间 ≤ gap，默认
       :data:`MERGE_GAP_SEC`）；
    3. **单调性**：新观测 ``logbase_size`` ≥ 该目录已落盘 size
       （同局只增不减；新局从 1~5 条重新爬 —— 地址被复用的熵源判据）。

    :param scans: :func:`core.duel_record.scan_record` 的结果列表。
    多个可复用时取 ``last_saved`` 最新的。
    """
    gap = MERGE_GAP_SEC if gap_sec is None else gap_sec
    inst = _norm_instance(instance)
    if inst is None or not logbase_size:
        return None
    best = None
    for s in scans or []:
        if s.get('state') != 'open' or s.get('instance') != inst:
            continue
        old_size = s.get('logbase_size')
        if old_size is None or logbase_size < old_size:
            continue                      # size 回退 = 地址被新局复用
        fs, ls = s.get('first_seen'), s.get('last_saved')
        if fs is None or ls is None:
            continue
        if now >= ls:
            delta = (now - ls).total_seconds()
        elif now <= fs:
            delta = (fs - now).total_seconds()
        else:
            delta = 0.0                   # 还在 capture 区间内
        if delta > gap:
            continue
        if best is None or (s.get('last_saved') or datetime.min) > \
                (best.get('last_saved') or datetime.min):
            best = s
    return best['name'] if best else None


def find_stale_open(scans, now, older_than_sec=STALE_OPEN_SEC, all_open=False):
    """该补标 final 的 open 目录名列表（终局化通道 P1-1 的判据）。

    * ``all_open=True``（采集器启动、pid 变化 / 游戏没在跑 —— 终审 P3-2 口径）：
      全部 open 都算 stale（免启动期内存扫描）；
    * 否则按时间：``last_saved`` 距 ``now`` 超过 ``older_than_sec`` 才算
      （游戏一直开着、采集器重启的场景）。
    """
    out = []
    for s in scans or []:
        if s.get('state') != 'open':
            continue
        if all_open:
            out.append(s['name'])
            continue
        ls = s.get('last_saved')
        if ls is None or (now - ls).total_seconds() > older_than_sec:
            out.append(s['name'])
    return out


def finalize_open(record_dir, reason='stale-open'):
    """把 ``state=open`` 的目录补标 ``final``（I/O）。

    manifest 加 ``finalized_by``、note 追加终局化痕迹；已经是 final 的目录
    不动（返回 None，幂等）。掉线局收口 / 启动自愈 / ``--finalize-stale`` 共用。
    """
    man = load_manifest(record_dir)
    st, _src = state_of(man)
    if st != 'open':
        return None
    man['state'] = 'final'
    man['finalized_by'] = reason
    tag = 'finalized:%s@%s' % (reason, time.strftime('%Y-%m-%d %H:%M:%S'))
    old_note = man.get('note') or ''
    if tag not in old_note:
        man['note'] = (old_note + '；' if old_note else '') + tag
    _dump_manifest(record_dir, man)
    return man


def _norm_instance(v):
    """hex 串 / int -> int；解不出 None（与 duel_record._norm_instance 同口径）。"""
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return None
