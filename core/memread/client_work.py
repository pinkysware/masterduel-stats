# -*- coding: utf-8 -*-
"""ClientWork 快照 —— 我方卡组逐卡 id（M6 契约 ``my_deck_snapshot`` 的来源）。

数据源（2026-09-12 实机校准，tmp/read_my_deck.py 同源逻辑的产品化）::

    YgomSystem.Utility.ClientWork.s_data（**全静态类，主界面就有效**，静态区 +0x08）
      ["Deck"]["maindeck_id"]   当前选中的卡组 id（字符串或装箱 Int64）
      ["Deck"]["list"][id]      全部卡组：name / status / et / pick_cards / …
      ["DeckList"][id]          选中卡组的逐卡表：{ m/e/s/t: { ids: List<boxed Int64>, r } }
      （各活动模式各有自己的容器：TDeck/CUPDeck/RDCDeck/EXHDeck/WCSDeck/
        REDeck/DTDeck/DRDeck/VDeck 及各自 *List —— 全都试一遍）

输出就是契约形状 ``{"deck_id","deck_name","ids":[...]}``（座位判据唯一输入，
``core.duel_record.infer_seat`` 消费）。**租借局读不到返回 None**（骰子拉力走
DRDeck、DeckList 未加载）—— 调用方按契约存 null、不阻塞归档，别在结算画面补读
（终审提醒：那时可能已被清理，且没必要）。
"""

from core.memread.il2cpp import (find_typeinfo, list_fields,     # noqa: F401
                                 static_fields_addr)
from core.memread.il2cpp_obj import (boxed_int, dict_items, int_list,
                                     read_string)

#: (元数据容器, 逐卡表容器) —— 天梯是 Deck/DeckList，各活动模式各有自己的键
CONTAINERS = [('Deck', 'DeckList'), ('TDeck', 'TDeckList'),
              ('CUPDeck', 'CUPDeckList'), ('RDCDeck', 'RDCDeckList'),
              ('EXHDeck', 'EXHDeckList'), ('WCSDeck', 'WCSDeckList'),
              ('REDeck', 'REDeckList'), ('DTDeck', 'DTDeckList'),
              ('DRDeck', 'DRDeckList'), ('VDeck', 'VDeckList')]

#: 逐卡表的分段顺序（ids 按此拼接；r = 保留段不进快照）
PART_ORDER = ('m', 'e', 's', 't')


def _sdata_addr(proc, mirror, tbl, cw_ti=None):
    """ClientWork 的 ``s_data`` 静态字段 -> 指针值；读不到给 ``None``。"""
    ti = cw_ti or find_clientwork(proc, mirror, tbl)
    if not ti:
        return None
    fl = {f.name: f.offset for f in list_fields(proc, ti, mirror=mirror)}
    if 's_data' not in fl:
        return None
    base = static_fields_addr(proc, ti)
    sdata = proc.read_ptr(base + fl['s_data'])
    return sdata if sdata and sdata >= 0x10000 else None


def find_clientwork(proc, mirror, tbl):
    """ClientWork 的 TypeInfo；没定位到给 None。"""
    try:
        tis = find_typeinfo(proc, mirror, tbl, 'YgomSystem.Utility',
                            'ClientWork')
    except Exception:
        return None
    return tis[0] if tis else None


def read_containers(proc, mirror, sdata):
    """s_data -> {容器名: {'list': {id: {'name':…}}, 'maindeck_id': …}}。"""
    got = dict(dict_items(proc, mirror, sdata) or [])
    containers = {}
    for cont_name, _lc in CONTAINERS:
        obj = got.get(cont_name)
        if not obj or obj < 0x10000:
            continue
        info = {'list': {}, 'maindeck_id': None}
        for k, v in (dict_items(proc, mirror, obj) or []):
            if k == 'list':
                for did, dv in (dict_items(proc, mirror, v) or []):
                    if not dv or dv < 0x10000:
                        continue
                    name = None
                    for k2, v2 in (dict_items(proc, mirror, dv) or []):
                        if k2 == 'name':
                            name = read_string(proc, v2, 60)
                            break
                    info['list'][did] = {'name': name}
            elif k == 'maindeck_id':
                mid = read_string(proc, v, 40) or boxed_int(proc, v)
                info['maindeck_id'] = mid if mid is not None else v
        containers[cont_name] = info
    return containers


def read_deck_list(proc, mirror, sdata):
    """s_data -> {卡组 id: {m/e/s/t: {'ids': [...], 'r': [...]}}}。"""
    got = dict(dict_items(proc, mirror, sdata) or [])
    deck_list = {}
    for _mc, lc in CONTAINERS:
        dl = got.get(lc)
        if not dl or dl < 0x10000:
            continue
        for did, dv in (dict_items(proc, mirror, dl) or []):
            if not dv or dv < 0x10000:
                continue
            parts = {}
            for part, pv in (dict_items(proc, mirror, dv) or []):
                if not pv or pv < 0x10000:
                    continue
                p2 = dict(dict_items(proc, mirror, pv) or [])
                parts[part] = {'ids': int_list(proc, mirror, p2.get('ids')),
                               'r': int_list(proc, mirror, p2.get('r'))}
            if not parts:
                continue
            if did in deck_list and not any(
                    parts[x]['ids'] for x in parts):
                continue            # 已有同 id 且旧的更有货，不覆盖
            deck_list.setdefault(did, parts)
    return deck_list


def choose_selected(containers):
    """选中的 (卡组 id 字符串, 容器名)。

    ``maindeck_id`` 优先落在「自家清单里有它」的容器（tmp 实测口径：对上自家
    清单的容器最可信）；都对不上才取第一个有 maindeck_id 的。
    """
    fallback = (None, None)
    for cont_name, info in containers.items():
        mid = info.get('maindeck_id')
        if mid is None:
            continue
        if str(mid) in info.get('list', {}):
            return str(mid), cont_name
        if fallback == (None, None):
            fallback = (str(mid), cont_name)
    return fallback


def flatten_ids(parts):
    """{m/e/s/t: {'ids': [...]}} -> 逐卡列表（按 PART_ORDER 拼接，重复保留）。"""
    out = []
    for part in PART_ORDER:
        seg = (parts or {}).get(part) or {}
        out.extend(seg.get('ids') or [])
    return out


def snapshot_my_deck(proc, mirror, tbl, cw_ti=None):
    """快照当前选中卡组 -> 契约形状 dict；读不到返回 None（不炸）。

    返回 ``{"deck_id", "deck_name", "ids": [...]}``；``ids`` 为空（租借局
    DeckList 没加载）也按「读不到」处理 —— 契约口径：存 null、不阻塞归档。

    :param cw_ti: 已缓存的 ClientWork TypeInfo（审查 P2-1：TypeInfo 查找 =
        全内存扫描，常驻方必须传缓存；None 才自己找）。
    """
    try:
        sdata = _sdata_addr(proc, mirror, tbl, cw_ti)
        if not sdata:
            return None
        containers = read_containers(proc, mirror, sdata)
        deck_list = read_deck_list(proc, mirror, sdata)
    except Exception:
        return None
    sel, _src = choose_selected(containers)
    if sel is None:
        return None
    row = {}
    for info in containers.values():
        hit = info.get('list', {}).get(sel)
        if hit:
            row = hit
            break
    ids = flatten_ids(deck_list.get(sel))
    if not ids:
        return None
    try:
        did = int(sel)
    except (TypeError, ValueError):
        did = sel
    return {'deck_id': did, 'deck_name': row.get('name'), 'ids': ids}


def read_ratedeck(proc, mirror, tbl, cw_ti=None):
    """巅峰（Rate Duel）选牌 -> 契约形状 dict；读不到 ``None``。

    2026-10-25 实机定性（10 月版 UI，双槽位选牌）：巅峰槽位就是
    ``s_data.Deck.ratedeck_id``（与天梯槽 ``maindeck_id`` 并列的字段，
    字段名直说了 Rate deck）—— 名字取 ``Deck.list[ratedeck_id].name``，
    逐卡表取 ``DeckList[ratedeck_id]`` 的 ``{m/e/s/t}``。用户巅峰与天梯
    选同一副时两值相同（48171157='闪刀姬'，55 张），分开选时各自独立 ——
    正好补上 09-28「巅峰牌在 DeckEdit 编辑实例」实锤的过期（10 月版选牌
    走 DeckSelectViewController2 列表选择，全程不建编辑实例）。

    ⚠️ 别碰同层的 ``RDeckList``（R 开头容易望文生义）—— 实测它装着一副
    与用户所选无关的完整召唤师（56 张、ulong 键、对局外常驻、非最近对手：
    与 00:02/00:21 局露卡 0 重合），语义未定（疑似租借/推荐预载），
    拿它当快照会整局错标。

    ⚠️ ``ratedeck_id`` 读不到 / 0 / 名字与逐卡表都缺 -> ``None``（宁可
    空，别编）。
    """
    try:
        sdata = _sdata_addr(proc, mirror, tbl, cw_ti)
        if not sdata:
            return None
        got = dict(dict_items(proc, mirror, sdata) or [])
        deck = dict(dict_items(proc, mirror, got.get('Deck')) or {})
        rv = deck.get('ratedeck_id')
        rid = None
        if rv and rv > 0x10000:
            rid = read_string(proc, rv, 60) or boxed_int(proc, rv)
        if rid in (None, '', 0):
            return None
        rid_str = str(rid)
        # 名字：Deck.list[ratedeck_id].name
        name = None
        lst = dict(dict_items(proc, mirror, deck.get('list')) or {})
        entry = lst.get(rid_str)
        if entry and entry > 0x10000:
            for k2, v2 in (dict_items(proc, mirror, entry) or []):
                if (k2 if isinstance(k2, str) else read_string(proc, k2, 30)) == 'name':
                    name = read_string(proc, v2, 60)
                    break
        # 逐卡表：DeckList[ratedeck_id] 的 {m/e/s/t}
        main: list = []
        extra: list = []
        dl = dict(dict_items(proc, mirror, got.get('DeckList')) or {})
        dv = dl.get(rid_str)
        if dv and dv > 0x10000:
            for part, pv in (dict_items(proc, mirror, dv) or []):
                if not pv or pv < 0x10000:
                    continue
                p2 = dict(dict_items(proc, mirror, pv) or {})
                ids = int_list(proc, mirror, p2.get('ids')) or []
                if str(part) == 'e':
                    extra = ids
                elif str(part) == 'm':
                    main = ids
        ids = main + extra
        if not ids:
            return None
        try:
            deck_id = int(rid_str)
        except (TypeError, ValueError):
            deck_id = rid_str
        return {'deck_id': deck_id, 'deck_name': name, 'ids': ids,
                'main': main, 'extra': extra, 'container': 'Ratedeck'}
    except Exception:
        return None
