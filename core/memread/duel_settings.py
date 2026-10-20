# -*- coding: utf-8 -*-
"""duelSettings 读取 —— 对手名/模式/开局时间戳（M3 交接清单第 3 项的产品化）。

链路（2026-09-12 实机挖到，tmp/dig_settings*.py 同源逻辑的产品化）::

    DuelClient.instance（静态槽位 +0x00）
      -> <engineInitializer>k__BackingField -> EngineInitializerByServer
      -> <duelSettings>k__BackingField = Dictionary<string, object>（63 项）

已知键：``name``（List<string> 双方名）、``GameMode``、``duel_start_timestamp``。

★ **方向已定案（2026-09-13 实机，2/2 样本；commit fd4f486）**：duelSettings 的
name 列表**按座位排** —— ``names[i] = player i`` 的名字，**不是**固定的「0=对手」。
本模块输出 ``names[0]/names[1]`` + ``names_direction='by-seat'``（见常量
``NAMES_DIRECTION`` —— **全项目唯一的方向开关位**）。
历史教训（P1-2）：只有一局样本时曾以为「name[0]=对手」；**若当初写死进库，
实机那局就会把用户自己的名字写进对手列**，所以定案前一直输出中性对 None。
消费规则在 ``core.duel_record``：seat 判出后 ``opp = names[1-seat]``。
⚠️ 改这个常量 = 改数据语义，改完**必须同步本文件与 tests/test_duel_settings.py
的文档** —— 2026-09-14 就因为文档没跟改，让排查的人误判成「开关没打开」。
"""

from core.memread.il2cpp import find_singleton, find_typeinfo, list_fields
from core.memread.il2cpp_obj import (boxed_int, class_name_of, dict_items,
                                     list_items, read_string)

#: 字段白名单（**按名查偏移**，不在表里 / 查不到都直接失败 —— 偏移不猜）
DUELCLIENT_FIELDS = ('<engineInitializer>k__BackingField',)
INITIALIZER_FIELDS = ('<duelSettings>k__BackingField',)


def _offset(fields, name):
    """字段表里按名取偏移；查不到给 ``None``（**绝不退回 0**）。

    ``fields`` 既接受 ``{名字: FieldEntry}`` 也接受 ``{名字: 偏移}``。

    ★ BUG-B3-06：原来写的是 ``df.get(name, 0)`` —— 偏移 0 正好是 IL2CPP
    对象头的 ``klass`` 指针，真机上它是个**有效地址**，两道地址校验都会放过，
    最后返回一个「全是 None 的 dict」而不是 None，调用方会当成「读到了」。
    """
    for key in (name, name.strip('<>').replace('k__BackingField', '')):
        f = fields.get(key)
        if f is not None:
            return getattr(f, 'offset', f)
    return None

#: 方向（**实机定案 2026-09-13，2/2 样本**）：duelSettings 的 name 列表
#: **按座位排** —— names[i] = player i 的名字，不是固定的「0=对手」。
#: 证据：局 1 seat=0 -> names[0]='示例玩家'（我）；局 3 seat=1 ->
#: names[1]='示例玩家'（我），两局互为异侧且都与用户本人名吻合。
#: 消费规则在 core.duel_record：seat 判出后 opp = names[1-seat]。
#: ⚠️ 这是全项目唯一的方向开关位，别在业务代码里自行判断方向。
NAMES_DIRECTION = 'by-seat' 


def _resolve_value(proc, mirror, v):
    """duelSettings 的 value：字符串 / 装箱整数 / List<string> 三种都试。"""
    s = read_string(proc, v, 120)
    if s is not None:
        return s
    iv = boxed_int(proc, v)
    if iv is not None:
        return iv
    cn = class_name_of(proc, v)
    if cn.startswith('List'):
        names = [read_string(proc, it, 120)
                 for it in (list_items(proc, mirror, v) or [])]
        names = [n for n in names if n is not None]
        if names:
            return names
    return None        # 认不出就 None，不猜（别把垃圾指针当名字）


def read_duel_settings(proc, mirror, tbl, dc_ti=None):
    """读 duelSettings -> 名字**按座位排**的 dict；任一环节断掉给 None（正常态，不炸）。

    返回 ``{"names": [...]|None, "names_direction": "by-seat", "game_mode": int|None,
    "duel_start_ts": int|None}`` —— 即 manifest ``duel_settings`` 键的形状。
    ``names_direction`` 取自常量 ``NAMES_DIRECTION``（不是写死的）。

    :param dc_ti: 已缓存的 DuelClient TypeInfo（审查 P2-1：TypeInfo 查找 =
        全内存扫描，常驻方必须传缓存；None 才自己找）。
    """
    try:
        if dc_ti:
            ti = dc_ti
        else:
            tis = find_typeinfo(proc, mirror, tbl, 'YgomGame.Duel', 'DuelClient')
            ti = tis[0] if tis else 0
        if not ti:
            return None
        dc = find_singleton(proc, ti, slot=0x00)         # instance 槽位 0x00
        if not dc:
            return None
        df = {f.name: f.offset
              for f in list_fields(proc, ti, mirror=mirror)}
        off = _offset(df, '<engineInitializer>k__BackingField')
        if off is None:
            return None                  # 字段名变了 -> 偏移不猜，宁可不读
        init = proc.read_ptr(dc + off)
        if not init or init < 0x10000:
            return None
        ifl = {f.name: f.offset
               for f in list_fields(proc, proc.read_ptr(init), mirror=mirror)}
        off = _offset(ifl, '<duelSettings>k__BackingField')
        if off is None:
            return None
        ds = proc.read_ptr(init + off)
        if not ds or ds < 0x10000:
            return None
        pairs = dict_items(proc, mirror, ds) or []
    except Exception:
        return None
    settings = {}
    for k, v in pairs:
        if k is None:
            continue
        settings[k] = _resolve_value(proc, mirror, v)
    names = settings.get('name')
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        names = None
    mode = settings.get('GameMode')
    ts = settings.get('duel_start_timestamp')
    return {'names': names, 'names_direction': NAMES_DIRECTION,
            'game_mode': mode if isinstance(mode, int) else None,
            'duel_start_ts': ts if isinstance(ts, int) else None}
