# -*- coding: utf-8 -*-
"""读「卡组编辑界面里正在编辑的那套牌」（只读）。

为什么需要它（2026-09-16）
--------------------------
`ClientWork.s_data` 里**没有**编辑中的卡组：`DeckList` 是 `0x0`（逐卡表没加载），
`Deck.list` 的条目只有 `deck_id/name/status/et/ct/accessory/pick_cards/regulation_id`
—— **没有卡列表**。正在编辑的那套牌挂在**编辑场景的控制器**上。

定位链路（实机验证，用户当时停在编辑界面）
------------------------------------------
1. metadata 字符串堆（`MetadataHeader.string_offset/size`）里有类名
   ``DeckEditViewController2``；
2. 单指针搜「谁在 +0x10 存着这个类名指针」-> 它的 **TypeInfo**（+0x18 回读 namespaze 佐证）；
3. 用 TypeInfo 指针**全堆搜实例**（对象头 = klass 指针），筛「主卡组非空」的那个。

字段（**运行时按字段名查偏移**，见 `docs/我方卡组识别方案.md` §2.1）：

======================  ==========  =========================================
字段名                   实测偏移     说明
======================  ==========  =========================================
``m_MainDeckCards``      +0x270      主卡组 ``List<T>``
``m_ExtraDeckCards``     +0x278      额外卡组 ``List<T>``
``<m_DeckID>k__BackingField``   +0x2A8  卡组 id（**编辑中实测是 0**）
``<m_DeckName>k__BackingField`` +0x2B0  卡组名（**编辑中实测是占位串 'Deck'**）
======================  ==========  =========================================

⚠️ **上面的偏移只是文档参照值，代码里一个都不写**（2026-10-19 / BUG-B3-01）。
本模块原先直接写死这四个偏移，违反 ``reader_base`` 开头那条红线。游戏更新后
偏移会变，而写死的读法**不会报错**：新位置若正好是另一个 ``List``，读出来的
照样是一串真实卡 id（``_items``/``_size`` 还按名查得到），``MIN_MAIN`` 也过得去
—— 最后写进 ``data/my_deck.json``，座位判反，对手卡组整局记反。
现在改为 :func:`deck_fields` 按名查；查不到就抛 ``ValueError``（偏移不猜）。

★ **列表元素是 28 字节内联数据，首 4 字节 = 游戏内卡 id**
（实测 ``+0 = 4007 → 青眼白龙``；``+4/+8`` 是份数类字段，``+12`` 起是一个 8 字节指针）。
⚠️ 因此**不能**用 :func:`core.memread.il2cpp_obj.list_items`（它按 8 字节**指针**读，
是引用类型列表的口径）—— 必须自己按步长读 ``_items`` 数组。
元素步长是**结构体大小**、不是字段偏移，字段表里查不到，只能保留常量；
读飞的风险由 :func:`parse_card_ids` 的值域过滤（``0 < id < 0x10000``）兜住。

实机样本（2026-09-16，原石青眼白龙）：主 **41** 张 + 额外 **15** 张，卡名与用户口述一致。

代价（**别忽略**）
------------------
找实例要做**一次全堆扫**（实测 `max_hits=4000` 才捞到真实例）。所以本模块**只给
一次性动作调**（用户点按钮 / 打开选择器时试一次），**绝不进采集器的每拍热路径**。

⚠️ 本模块**只读**（`SafeProcess` 权限位硬编码 0x410，没有写入口）。
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional

from core.memread.il2cpp import list_fields
from core.memread.il2cpp_obj import _fields_of

#: 编辑场景控制器的类名（metadata 字符串堆里唯一）
CLASS_NAME = 'DeckEditViewController2'

#: 字段的**候选名字**（按顺序找第一个命中的）。属性在 IL2CPP 里生成
#: ``<名字>k__BackingField``，所以两种形态都认。
FIELD_NAMES: Dict[str, tuple] = {
    'main': ('m_MainDeckCards',),
    'extra': ('m_ExtraDeckCards',),
    'deck_id': ('<m_DeckID>k__BackingField', 'm_DeckID'),
    'deck_name': ('<m_DeckName>k__BackingField', 'm_DeckName'),
}
#: 缺了这两个就不读（卡表是唯一的识别输入）；deck_id/name 缺失只让对应值为 None
REQUIRED_FIELDS = ('main', 'extra')

#: 列表元素步长（字节）—— **内联结构体大小**，不是字段偏移（见模块头）。
ELEMENT_STRIDE = 28
#: 数组数据起点（Il2CppArray：0x10 对象头 + 0x8 bounds + 0x8 max_length）
ARRAY_DATA_OFF = 0x20
#: 「主卡组至少这么多张」才算真的编辑实例（空表的是壳/残留）
MIN_MAIN = 20
#: 找实例的那次全堆扫的上限（实测 60 个命中里只有 3 个像样）
DEFAULT_MAX_HITS = 4000

#: 合法卡 id 区间（游戏内卡 id 是 uint16；2026-10 卡表实测 3002..23808）
MIN_CARD_ID = 1
MAX_CARD_ID = 0x10000

#: 字段表缓存（key = (pid 或 id(proc), TypeInfo)）—— 观察者每拍都会读，
#: 每次都把 176 个 FieldInfo 走一遍太浪费。
_FIELDS_CACHE: Dict[tuple, Dict[str, int]] = {}
_FIELDS_CACHE_MAX = 8


def clear_fields_cache() -> None:
    """清空字段表缓存（测试 / 重新 attach 后用）。"""
    _FIELDS_CACHE.clear()


def deck_fields(proc, mirror, typeinfo) -> Dict[str, int]:
    """**按字段名**查 :data:`FIELD_NAMES` 的偏移 -> ``{'main':…, 'extra':…}``。

    查不到必填字段（main/extra）时抛 ``ValueError`` —— 偏移不猜，宁可不读。
    deck_id / deck_name 不是必填（读不到只让对应值为 None，卡表照读）。

    结果按 ``(进程, TypeInfo)`` 缓存：观察者每拍都要用，不必每拍重列 176 个字段。
    """
    pid = getattr(proc, 'pid', None)
    #: 只按 (pid, TypeInfo) 缓存 —— 没有 pid 的桩进程不缓存（id() 会被回收复用）
    key = (pid, int(typeinfo or 0)) if pid is not None else None
    got = _FIELDS_CACHE.get(key) if key is not None else None
    if got is not None:
        return got
    fl = {f.name: f.offset for f in list_fields(proc, typeinfo, mirror=mirror)}
    out: Dict[str, int] = {}
    for slot, names in FIELD_NAMES.items():
        for name in names:
            if name in fl:
                out[slot] = fl[name]
                break
    missing = [s for s in REQUIRED_FIELDS if s not in out]
    if missing:
        raise ValueError(
            '%s 的字段表里找不到 %s —— 偏移不猜，宁可不读（字段表：%s）'
            % (CLASS_NAME, '/'.join(missing), ', '.join(sorted(fl)) or '(空)'))
    if key is not None:
        if len(_FIELDS_CACHE) >= _FIELDS_CACHE_MAX:
            _FIELDS_CACHE.clear()
        _FIELDS_CACHE[key] = out
    return out


def load_string_table(meta_path: Optional[str] = None):
    """读 metadata 的**字符串堆原始字节** -> ``(header, table)``。

    与 :func:`core.memread.il2cpp.read_string_table` 读的是**同一段**
    （``[string_offset, string_offset + string_size)``）—— 这里只是顺带把
    header 一起返回，别再抄第二份读取实现。
    """
    from core.memread.il2cpp import (MetadataHeader, default_metadata_path,
                                     read_string_table)
    if meta_path is None:
        from core.memread.end_message import default_game_dir
        meta_path = default_metadata_path(default_game_dir())
    header = MetadataHeader.from_file(meta_path)
    return header, read_string_table(meta_path, header)


def parse_card_ids(raw: bytes, size: int, stride: int = ELEMENT_STRIDE) -> List[int]:
    """**纯函数**：``_items`` 数组的原始字节 -> 卡 id 列表（每 ``stride`` 字节取首 int32）。

    长度不够的尾部按读到的算（不抛）。**值域过滤**：只留 ``0 < id < 0x10000``
    —— 步长/布局读飞时那串值多半落不进卡 id 区间，直接丢掉比写进状态文件强。
    """
    out = []
    for i in range(max(0, int(size))):
        off = i * stride
        if off + 4 > len(raw):
            break
        cid = struct.unpack_from('<I', raw, off)[0]
        if MIN_CARD_ID <= cid < MAX_CARD_ID:
            out.append(cid)
    return out


def find_typeinfo(proc, mirror, table, *, max_hits: int = 200) -> Optional[int]:
    """按类名找 ``DeckEditViewController2`` 的 TypeInfo（取**字段最多的**那个）。

    实测同类名有两个 TypeInfo（176 字段的真身 + 0 字段的壳），字段多的是真身。
    """
    addr = mirror.string_addr(CLASS_NAME, table)
    if not addr:
        return None
    cands = []
    for hit in proc.scan(struct.pack('<Q', addr), max_hits=max_hits):
        ti = hit - 0x10
        if ti < 0x10000:
            continue
        try:
            if proc.read_u64(ti + 0x10) != addr:
                continue
        except Exception:                                    # noqa: BLE001
            continue
        cands.append(ti)
    if not cands:
        return None
    return max(cands, key=lambda t: len(list_fields(proc, t, mirror=mirror)))


def read_list_ids(proc, mirror, obj, *, stride: int = ELEMENT_STRIDE) -> List[int]:
    """``List<T>``（内联元素）-> 卡 id 列表；不是列表给 ``[]``。"""
    if not obj or obj < 0x10000:
        return []
    try:
        fl = _fields_of(proc, mirror, obj)
        if '_items' not in fl or '_size' not in fl:
            return []
        items = proc.read_ptr(obj + fl['_items'])
        size = proc.read_u32(obj + fl['_size'])
        if not items or items < 0x10000 or not 0 < size <= 200:
            return []
        raw = proc.read(items + ARRAY_DATA_OFF, size * stride)
    except Exception:                                        # noqa: BLE001
        return []
    return parse_card_ids(raw, size, stride)


def read_edited_deck_auto(pid: Optional[int] = None):
    """便捷入口：自己找游戏进程 + metadata 镜像 -> :func:`read_edited_deck`。

    只给**手动跑的工具**用（内部含一次全堆扫）；常驻方请自己缓存 mirror。
    """
    from core.memread.il2cpp import (MetadataHeader, default_metadata_path,
                                    find_all_metadata_mirrors)
    from core.memread.process import SafeProcess, find_pids_by_name
    pids = list(find_pids_by_name('masterduel.exe')) if pid is None else [pid]
    if not pids:
        return None
    header, table = load_string_table()
    with SafeProcess(pids[0]) as proc:
        mirror = find_all_metadata_mirrors(proc, header)[0]
        return read_edited_deck(proc, mirror, table)


def instance_state(proc, addr, ti) -> Optional[bool]:
    """对象头校验**三态**：``True`` 是本类实例 / ``False`` 不是 / ``None`` 读不到。

    `None`（这一拍读失败）与 `False`（确实不是本类对象）必须分开 ——
    前者只是暂时读不到，不能拿来做「实例已消失」的判决（BUG-B3-14 的根因）。
    """
    if not addr or addr < 0x10000 or not ti:
        return False
    try:
        return proc.read_u64(addr) == ti
    except Exception:                                        # noqa: BLE001
        return None


def is_instance(proc, addr, ti) -> bool:
    """对象头校验：``addr`` 处那个对象是不是 ``ti`` 这个类的（防野指针）。

    读不到按「不是」处理 —— 这个布尔口只给扫描筛选用；
    需要区分三态请用 :func:`instance_state`。
    """
    return instance_state(proc, addr, ti) is True


def read_instance(proc, mirror, addr) -> Optional[Dict[str, object]]:
    """**按实例地址**读那套牌（便宜：不扫描）—— 观察者每拍走的就是这条。

    主卡组不足 :data:`MIN_MAIN` 张当作读不到（壳/残留）。
    """
    try:
        fl = deck_fields(proc, mirror, proc.read_ptr(addr))
    except ValueError:
        return None                  # 字段名查不到 = 偏移不猜（宁可不读）
    except Exception:                                        # noqa: BLE001
        return None
    try:
        main = read_list_ids(proc, mirror, proc.read_ptr(addr + fl['main']))
        extra = read_list_ids(proc, mirror, proc.read_ptr(addr + fl['extra']))
    except Exception:                                        # noqa: BLE001
        return None
    if len(main) < MIN_MAIN:
        return None
    name = None
    if 'deck_name' in fl:
        try:
            from core.memread.il2cpp_obj import read_string
            name = read_string(proc, proc.read_ptr(addr + fl['deck_name']), 40)
        except Exception:                                    # noqa: BLE001
            pass
    did = None
    if 'deck_id' in fl:
        try:
            did = proc.read_u32(addr + fl['deck_id'])
        except Exception:                                    # noqa: BLE001
            did = None
    return {'deck_id': did or None, 'deck_name': name,
            'ids': list(main) + list(extra),
            'container': 'DeckEdit', 'main': list(main), 'extra': list(extra)}


def scan_edited_deck(proc, mirror, table, *,
                     max_hits: int = DEFAULT_MAX_HITS):
    """**全堆扫一次**找实例 -> ``(deck, 实例地址)``；找不到 ``(None, None)``。

    ⚠️ 这是「扫描」，只给一次性动作/低频重发现用（观察者靠 `RESCAN_SEC` 节流）。
    """
    ti = find_typeinfo(proc, mirror, table)
    if not ti:
        return None, None
    for ia in proc.scan(struct.pack('<Q', ti), max_hits=max_hits):
        if not is_instance(proc, ia, ti):
            continue
        deck = read_instance(proc, mirror, ia)
        if deck:
            deck['typeinfo'] = ti
            return deck, ia
    return None, None


def read_edited_deck(proc, mirror, table, *,
                     max_hits: int = DEFAULT_MAX_HITS) -> Optional[Dict[str, object]]:
    """编辑界面里**正在编辑**的那套牌 -> 契约形状 dict；读不到给 ``None``。

    返回 ``{'deck_id', 'deck_name', 'ids', 'container', 'main', 'extra'}``：

    * ``ids`` = 主 + 额外按顺序拼接（与 `client_work.snapshot_my_deck` 同口径）；
    * ``container`` = ``'DeckEdit'``（来源标记，和快照那条路的容器名并列）；
    * ``main`` / ``extra`` 分开留着 —— 识别与排查都用得上。

    ⚠️ 内部是**一次全堆扫**，只给一次性动作调用（见模块头）。
    """
    deck, _addr = scan_edited_deck(proc, mirror, table, max_hits=max_hits)
    return deck
