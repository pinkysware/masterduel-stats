# -*- coding: utf-8 -*-
"""礼盒（Present Box）只读读取 —— 看板「礼盒」页的数据源。

⚠️ 这是 M7「看板进程不读内存」决策的**唯一破例口子**（用户 2026-09-19 拍板
「看板直读」）：看板里点「读取礼盒」按钮 → 手动触发一次读取 → 读完关句柄。
**不开定时器、不常驻扫描**。数据是「自己账号、屏幕上本来就可见」的礼盒条目，
不碰对战未公开信息，红线扫描照常覆盖本文件。

内存结构（2026-09-19 实机校准，详见 docs/礼盒看板设计.md）：
* 数据挂在 ``YgomGame.Menu.PresentBoxViewController`` 的实例字段 ``dataList``
  （字符串表里**没有**独立的 ``PresentBox`` 条目，别按这个名字找）；
* ``dataList`` = ``List<Data>``，元素类名就叫 ``Data``：
  pID / itemCategory / itemID / quantity (i32) + message / limitDate (String) + isPeriod；
* 宝石 = ``itemCategory==1 & itemID==1``；
* ⚠️ FieldInfo.offset **已含 0x10 对象头**，读值直接 ``obj + offset``
  （与 ``il2cpp_obj.py`` 同口径）。
"""
from __future__ import annotations

import json
import os
import re
import struct
import time
from typing import Dict, List, Optional, Tuple

from .il2cpp import (MetadataHeader, find_all_metadata_mirrors, list_fields,
                     read_string_table, TYPEINFO_NAME_OFFSET,
                     TYPEINFO_NS_OFFSET)
from .il2cpp_obj import MIN_ADDR, class_name_of, list_items, read_string
from .process import SafeProcess, find_pids_by_name

from core import paths

#: 视图控制器类（礼盒数据挂在它的 dataList 上）
VC_CLASS = 'PresentBoxViewController'
VC_NS = 'YgomGame.Menu'
#: 列表元素类名（就叫 Data，泛型 List 的元素）
DATA_CLASS = 'Data'
LIST_CLASS = 'List`1'
#: 宝石的 (itemCategory, itemID)（实测 67/67 条都是）
CAT_GEM, ITEM_GEM = 1, 1

#: 默认缓存路径（读成功才写；看板下次启动先显示上次结果）。
#: ★ BUG-B3-04：必须走 `paths.data_path` —— 裸相对路径按**进程 CWD** 解析，
#:   安装版（Program Files）会写到只读的安装目录、静默失效。
CACHE_PATH = paths.data_path('present_box_cache.json')

#: 元素读取的字段白名单（缺哪个都直接失败，不猜）
_REQUIRED_FIELDS = ('pID', 'itemCategory', 'itemID', 'quantity',
                    'message', 'limitDate')


# ---------------------------------------------------------------- 纯函数（单测重点）

def normalize_limit(s: Optional[str]) -> str:
    """limitDate 归一：日期与时间之间的 U+00A0（\\xa0）换成普通空格。"""
    if not s:
        return ''
    return s.replace('\xa0', ' ').strip()


def limit_date_part(limit: str) -> str:
    """『2026/10/6 19:01』 -> 『2026/10/6』（没有时间部分的原样返回）。"""
    return (limit or '').split(' ')[0]


def item_label(category: int, item_id: int) -> str:
    """物品显示名。宝石有名字；其它类别先按 (cat, id) 裸显示，等真出现再补映射。"""
    if category == CAT_GEM and item_id == ITEM_GEM:
        return '宝石'
    return '物品(cat %d id %d)' % (category, item_id)


def _time_of(limit: str) -> str:
    """『2026/10/6 19:01』 -> 『19:01』（没有时间部分给 ''）。"""
    parts = (limit or '').split(' ')
    return parts[1] if len(parts) > 1 else ''


def _day_sort_key(day: str) -> tuple:
    """『2026/10/6』 -> (0, 2026, 10, 6)；『（无期限）』等解析不了的 -> (1,)。"""
    m = re.match(r'(\d{4})/(\d{1,2})/(\d{1,2})$', day or '')
    if not m:
        return (1,)
    return (0, int(m.group(1)), int(m.group(2)), int(m.group(3)))


def summarize(rows: List[dict]) -> dict:
    """按期限**日期**分组聚合。最早到期的排最上（页面上标红）。

    每天还带 ``at`` = 当天**最早**的到点时刻（『19:01』；没时刻给 ''）——
    最早到期那行在页面上显示它。

    :param rows: :func:`read_items` 产出的条目（同形状的 dict 也行）
    :return: ``{'total_n', 'total_qty', 'days': [{'date', 'n', 'qty', 'at'}]}``
             空礼盒 → ``{'total_n': 0, 'total_qty': 0, 'days': []}``
    """
    acc: Dict[str, List[Tuple[int, str]]] = {}   # day -> [(数量, 到点时刻)]
    for r in rows:
        day = limit_date_part(r.get('limit_date', ''))
        if not day:
            day = '（无期限）'
        acc.setdefault(day, []).append(
            (int(r.get('quantity') or 0), _time_of(r.get('limit_date', ''))))
    days = [{'date': d, 'n': len(v), 'qty': sum(q for q, _ in v),
             'at': min((t for _, t in v if t), default='')}
            for d, v in acc.items()]
    # ⚠️ 按**数值**升序，别按字符串 —— YYYY/M/D 单位数日期的字典序是错的
    # （'2026/10/12' < '2026/10/6'，2026-09-19 自检出图抓到）；解析不了的排最后
    days.sort(key=lambda d: _day_sort_key(d['date']))
    return {'total_n': len(rows),
            'total_qty': sum(d['qty'] for d in days),
            'days': days}


def save_cache(path: str, rows: List[dict], read_at: Optional[float] = None) -> None:
    """把一次成功的读取写进缓存（JSON，UTF-8）。"""
    payload = {'read_at': read_at if read_at is not None else time.time(),
               'rows': rows}
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False)


def load_cache(path: str) -> Optional[dict]:
    """读缓存。没有 / 坏了 / 形状不对 → None（调用方当「没缓存」处理）。"""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('rows'), list):
        return None
    if not isinstance(payload.get('read_at'), (int, float)):
        return None
    return payload


# ---------------------------------------------------------------- 读取链（只读）

def find_vc_typeinfo(proc: SafeProcess, mirror, stbl: bytes) -> int:
    """定位 PresentBoxViewController 的 TypeInfo；找不到返回 0。

    全进程搜「指向类名字符串的指针」→ 回读 name/namespaze 校验
    （字符串表里没有独立 ``PresentBox``，别搜那个名字）。
    """
    addr = mirror.string_addr(VC_CLASS, stbl)
    if addr is None:
        return 0
    lo, hi = mirror.string_table_span
    for hit in proc.scan_all(struct.pack('<Q', addr), max_hits=64):
        ti = hit - TYPEINFO_NAME_OFFSET
        try:
            p_name = proc.read_ptr(ti + TYPEINFO_NAME_OFFSET)
            p_ns = proc.read_ptr(ti + TYPEINFO_NS_OFFSET)
        except Exception:
            continue
        if not (lo <= p_name < hi and lo <= p_ns < hi):
            continue
        ns = proc.read_cstring(p_ns, 96)
        if ns == VC_NS:
            return ti
    return 0


def _field_map(proc: SafeProcess, mirror, typeinfo: int) -> Dict[str, int]:
    """TypeInfo -> {字段名: 偏移}（偏移已含对象头，直接 obj+offset 用）。"""
    return {f.name: f.offset for f in list_fields(proc, typeinfo, mirror=mirror)}


def pick_instance(proc: SafeProcess, mirror, vc_ti: int,
                  vc_fields: Dict[str, int]) -> int:
    """从 klass==TypeInfo 的命中里挑出**活的**视图控制器实例；没有返回 0。

    真身判据（2026-09-19 实机：36 个命中里只有 1 个过）：
    dataList 指向一个能读的 ``List`1``；列表**非空**时还要求第一个元素类名是
    ``Data``。列表为空也算真身（= 礼盒真空了，别跟「没进过礼盒页」混淆）。
    """
    dl_off = vc_fields.get('dataList')
    if dl_off is None:
        return 0
    for hit in proc.scan_all(struct.pack('<Q', vc_ti), max_hits=256):
        if hit % 8:
            continue
        try:
            if proc.read_ptr(hit) != vc_ti:
                continue
            lst = proc.read_ptr(hit + dl_off)
        except Exception:
            continue
        if not lst or lst < MIN_ADDR:
            continue
        if class_name_of(proc, lst) != LIST_CLASS:
            continue
        items = list_items(proc, mirror, lst)
        if items is None:
            continue
        if items and (not items[0] or items[0] < MIN_ADDR
                      or class_name_of(proc, items[0]) != DATA_CLASS):
            continue
        return hit
    return 0


def _read_i32(proc: SafeProcess, addr: int) -> int:
    """读一个 i32；短读抛 ValueError（由 :func:`read_element` 统一兜住）。"""
    raw = proc.read(addr, 4)
    if not raw or len(raw) < 4:
        raise ValueError('i32 短读 addr=0x%X' % addr)
    return struct.unpack('<i', raw)[0]


def read_element(proc: SafeProcess, obj: int, fields: Dict[str, int]) -> Optional[dict]:
    """读一条礼盒条目（Data 对象）。缺字段 / 读炸 -> None（调用方跳过该条）。

    ★ BUG-B3-15：这三个 i32 原来用**一次** ``struct.unpack('<iii', …, 12)``
    读出来，隐含「itemCategory / itemID / quantity 在对象里紧挨着、顺序固定」。
    字段重排或中间插了新字段时，``quantity`` 会静默变成另一个 int（比如
    ``isPeriod``），礼盒数量直接算错且没有任何校验。现在各按**自己的偏移**读。
    """
    try:
        missing = [n for n in _REQUIRED_FIELDS if n not in fields]
        if missing:
            return None
        cat = _read_i32(proc, obj + fields['itemCategory'])
        item_id = _read_i32(proc, obj + fields['itemID'])
        qty = _read_i32(proc, obj + fields['quantity'])
        msg = read_string(proc, proc.read_ptr(obj + fields['message']), 160)
        lim = read_string(proc, proc.read_ptr(obj + fields['limitDate']), 60)
        # pID 在 i32 段的最前面，一起读（校验对象确实是礼盒条目形状）
        pid = _read_i32(proc, obj + fields['pID'])
    except Exception:
        return None
    return {'pID': pid, 'category': cat, 'item_id': item_id, 'quantity': qty,
            'limit_date': normalize_limit(lim), 'message': msg or ''}


def read_items(proc: SafeProcess, mirror, stbl: bytes,
               known_vc_ti: int = 0) -> Tuple[List[dict], int, str]:
    """主链：礼盒全部条目。返回 ``(rows, vc_ti, state)``。

    state 说明「没读到」是哪种没读到（给用户的话不一样）：
    * ``'no_class'``    —— TypeInfo 都没找到（游戏没加载到这个界面）
    * ``'no_instance'`` —— 类在、活实例不在 = **本次启动没进过礼盒页**
    * ``'broken'``      —— 真身在、列表也有货，但一条都解析不出来（结构变了？）
    * ``'ok'``          —— 拿到 rows（空礼盒 rows=[] 也是 ok）
    vc_ti 供调用方会话内缓存；给了但已失效会自动重找。
    """
    vc_ti = known_vc_ti
    if vc_ti:
        try:
            p_name = proc.read_ptr(vc_ti + TYPEINFO_NAME_OFFSET)
            ok = (mirror.base <= p_name < mirror.base + 0x2000000
                  and proc.read_cstring(p_name, 64) == VC_CLASS)
        except Exception:
            ok = False
        if not ok:
            vc_ti = 0
    if not vc_ti:
        vc_ti = find_vc_typeinfo(proc, mirror, stbl)
    if not vc_ti:
        return [], 0, 'no_class'
    vc_fields = _field_map(proc, mirror, vc_ti)
    obj = pick_instance(proc, mirror, vc_ti, vc_fields)
    if not obj:
        return [], vc_ti, 'no_instance'
    try:
        lst = proc.read_ptr(obj + vc_fields['dataList'])
    except Exception:
        return [], vc_ti, 'no_instance'
    items = list_items(proc, mirror, lst)
    if items is None:
        return [], vc_ti, 'no_instance'
    if not items:
        return [], vc_ti, 'ok'            # 真身在、列表空 = 礼盒确实是空的
    rows: List[dict] = []
    for it in items:
        if not it or it < MIN_ADDR:
            continue
        e_fields = _field_map(proc, mirror, proc.read_ptr(it))
        row = read_element(proc, it, e_fields)
        if row is not None:
            rows.append(row)
    if not rows:
        return [], vc_ti, 'broken'
    return rows, vc_ti, 'ok'


def _candidate_game_dirs() -> List[str]:
    """游戏目录候选 —— 与 `end_message.default_game_dir()` 同一口径。

    ★ BUG-B3-05：这里原先写死本机 `D:/steam/...` 绝对路径，`MD_GAME_DIR` 对它完全无效
    （换盘 / 换 Steam 库的用户永远读不到卡表镜像）。现在只走那一份口径。
    """
    from .end_message import default_game_dir
    return [default_game_dir()]


def game_metadata_missing_reason() -> str:
    """`find_game_metadata` 找不到镜像时，**真实原因**是「目录不对」还是「游戏没就绪」。

    用户提示语要能区分两者 —— 否则目录配错的人会一直等「游戏加载完」。
    """
    from .il2cpp import default_metadata_path
    dirs = _candidate_game_dirs()
    exist = [d for d in dirs if os.path.exists(default_metadata_path(d))]
    if not exist:
        return ('没找到游戏的卡表文件（%s）—— 请设置环境变量 MD_GAME_DIR '
                '指向 Master Duel 安装目录' % '、'.join(
                    default_metadata_path(d) for d in dirs))
    return '没找到游戏内存里的卡表镜像（游戏还没加载完？）'


def find_game_metadata(proc: SafeProcess):
    """游戏进程 -> (header, mirror, stbl)；找不到镜像返回 None。"""
    from .il2cpp import default_metadata_path
    for game_dir in _candidate_game_dirs():
        meta = default_metadata_path(game_dir)
        if not os.path.exists(meta):
            continue
        header = MetadataHeader.from_file(meta)
        if not header.is_valid:
            continue
        mirrors = find_all_metadata_mirrors(proc, header)
        if mirrors:
            stbl = read_string_table(meta, header)
            return header, mirrors[0], stbl
    return None


def read_present_box_live(known_vc_ti: int = 0) -> Tuple[Optional[List[dict]], int, str]:
    """看板按钮调的一步式入口（自己开进程自己关）。

    :return: ``(rows|None, vc_ti, error)``
             成功 rows 非 None（空礼盒 = []、error=''）；失败 rows=None、
             error 是给用户看的中文（区别「没开游戏」「没进过礼盒页」「结构变了」）。
    """
    pids = find_pids_by_name('masterduel.exe')
    if not pids:
        return None, known_vc_ti, '没有检测到游戏进程 —— 请先启动 Master Duel'
    with SafeProcess(pids[0]) as proc:
        found = find_game_metadata(proc)
        if not found:
            return None, known_vc_ti, game_metadata_missing_reason()
        _, mirror, stbl = found
        rows, vc_ti, state = read_items(proc, mirror, stbl, known_vc_ti)
    if state == 'no_instance':
        return None, vc_ti, '还没读到礼盒数据 —— 请进一次游戏的礼盒页面再读'
    if state == 'no_class':
        return None, 0, '没找到礼盒界面 —— 请进一次游戏的礼盒页面再读'
    if state == 'broken':
        return None, vc_ti, '礼盒数据读不出来（游戏可能更新了内部结构，工具需要跟一次）'
    return rows, vc_ti, ''
