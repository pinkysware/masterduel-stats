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

实测字段表（176 个字段里挑出来的，见 `docs/我方卡组识别方案.md` §2.1）：

======  ========  ====================================================
字段      偏移       说明
======  ========  ====================================================
m_MainDeckCards   +0x270  主卡组 ``List<T>``
m_ExtraDeckCards  +0x278  额外卡组 ``List<T>``
<m_DeckID>        +0x2A8  卡组 id（**编辑中实测是 0**：新组/未保存时没有 id）
<m_DeckName>      +0x2B0  卡组名（**编辑中实测是占位串 ``'Deck'``**）
======  ========  ====================================================

★ **列表元素是 28 字节内联数据，首 4 字节 = 游戏内卡 id**
（实测 ``+0 = 4007 → 青眼白龙``；``+4/+8`` 是份数类字段，``+12`` 起是一个 8 字节指针）。
⚠️ 因此**不能**用 :func:`core.memread.il2cpp_obj.list_items`（它按 8 字节**指针**读，
是引用类型列表的口径）—— 必须自己按步长读 ``_items`` 数组。

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
#: 字段偏移（实测，见模块头）
F_MAIN, F_EXTRA = 0x270, 0x278
F_DECK_ID, F_DECK_NAME = 0x2A8, 0x2B0
#: 列表元素步长（字节）—— 首 int32 是卡 id
ELEMENT_STRIDE = 28
#: 数组数据起点（Il2CppArray：0x10 对象头 + 0x8 bounds + 0x8 max_length）
ARRAY_DATA_OFF = 0x20
#: 「主卡组至少这么多张」才算真的编辑实例（空表的是壳/残留）
MIN_MAIN = 20
#: 找实例的那次全堆扫的上限（实测 60 个命中里只有 3 个像样）
DEFAULT_MAX_HITS = 4000


def load_string_table(meta_path: Optional[str] = None):
    """读 metadata 的**字符串堆原始字节** -> ``(header, table)``。

    `mirror.string_addr(name, table)` 要的就是它（`read_string_table()` 那个是
    另一张表，拿它查类名会 0 命中 —— 实机踩过）。
    """
    from core.memread.il2cpp import MetadataHeader, default_metadata_path
    from core.memread.process import open_game_file
    if meta_path is None:
        from core.memread.end_message import default_game_dir
        meta_path = default_metadata_path(default_game_dir())
    header = MetadataHeader.from_file(meta_path)
    with open_game_file(meta_path, 'rb') as f:
        f.seek(header.string_offset)
        return header, f.read(header.string_size)


def parse_card_ids(raw: bytes, size: int, stride: int = ELEMENT_STRIDE) -> List[int]:
    """**纯函数**：``_items`` 数组的原始字节 -> 卡 id 列表（每 ``stride`` 字节取首 int32）。

    长度不够的尾部按读到的算（不抛）。
    """
    out = []
    for i in range(max(0, int(size))):
        off = i * stride
        if off + 4 > len(raw):
            break
        out.append(struct.unpack_from('<I', raw, off)[0])
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


def is_instance(proc, addr, ti) -> bool:
    """对象头校验：``addr`` 处那个对象是不是 ``ti`` 这个类的（防野指针）。"""
    if not addr or addr < 0x10000 or not ti:
        return False
    try:
        return proc.read_u64(addr) == ti
    except Exception:                                        # noqa: BLE001
        return False


def read_instance(proc, mirror, addr) -> Optional[Dict[str, object]]:
    """**按实例地址**读那套牌（便宜：不扫描）—— 观察者每拍走的就是这条。

    主卡组不足 :data:`MIN_MAIN` 张当作读不到（壳/残留）。
    """
    try:
        main = read_list_ids(proc, mirror, proc.read_u64(addr + F_MAIN))
        extra = read_list_ids(proc, mirror, proc.read_u64(addr + F_EXTRA))
    except Exception:                                        # noqa: BLE001
        return None
    if len(main) < MIN_MAIN:
        return None
    name = None
    try:
        from core.memread.il2cpp_obj import read_string
        name = read_string(proc, proc.read_u64(addr + F_DECK_NAME), 40)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        did = proc.read_u32(addr + F_DECK_ID)
    except Exception:                                        # noqa: BLE001
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
