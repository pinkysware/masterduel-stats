# -*- coding: utf-8 -*-
"""step 2 —— 只读「当前对双方公开的卡」，输出卡 id。

目标
----
给 M5/M6 提供「此刻哪些卡是公开的」：场上表侧的卡 + 墓地里的卡。
这条线将来替代/补充日志的 ``opp_revealed``（对手打出的卡）。

判据（2026-09-12 实机差分固化，四组样本、双方各有表侧/里侧，全部对上）
--------------------------------------------------------------------
引擎侧每张卡有两处状态（链路见下）：

* ``currentEngineData.cardAttribute``：P0/P1 各一本 ``Dictionary<uid, 标志>``，
  标志的**低字节**编码区域（两本字典的低位一致，``0x8000`` 位只是
  「出现在非本方那本字典」的标记，读时无关紧要）：

  ========  =====================================================
  低字节    含义
  ========  =====================================================
  ``0x01``  在场上（本方那半场）
  ``0x41``  在场上（对方那半场；``0x40`` 位）
  ``0x08``  墓地（观测值 ``0x2008`` / ``0xA008`` 的低字节）
  ``0x02``  手牌（隐藏区，细分未验）
  ``0x04``  牌组（隐藏区）
  ``0x10``  额外牌组（隐藏区）
  ========  =====================================================

* 卡对象的 ``isFace``（byte）：``1`` = 表侧、``0`` = 里侧。

于是 **公开集 =（在场上 且 isFace=1）∪ 墓地**。里侧、手牌、牌组、额外
**一律不输出、不落盘**（下游也拿不到）。

已知局限（有样本再补判据，宁可漏、不可多）：

* 表侧除外区还没有实测样本，暂不收；
* 对手手牌被效果翻开（公开手牌）暂不收 —— 按区域筛只认「场上/墓地」。

链路（全部实机验证过，2026-09-12）
------------------------------------
::

    Engine(+0x08 s_instance).threadWork(+0x128)
      -> threadWork.currentEngineData(+0x10)
           uidTbl        : Dictionary<uid, 槽位>
           cardAttribute : Dictionary[]（长度 2 = P0/P1 各一本 uid -> 标志）
           uidBases      : 指针数组，uidBases[槽位] -> 卡对象
                +0x14 nPos（高 16 位 = 卡序号，低 16 位 = 区域码，读时要 mask）
                +0x18 wUid
                +0x20 stProp 指针 -> { +0x10 CardID(int32), +0x18 Owner(byte) }
                +0x28 isFace(byte) / +0x29 isTurn(byte)

红线
----
* 全程只读（``SafeProcess``，权限位 0x0410）；
* 字段偏移**按字段名查**（``list_fields``），只认白名单里的名字，
  查不到就抛 ``ValueError`` —— 绝不猜偏移、绝不按数字偏移硬读，
  这样天然绕开禁区字段（那个「本局卡牌全集」字段连名字都不许出现在
  业务代码里，见 ``redline.py``）；
* 不调用游戏函数（``EngineApiUtil.IsCardKnown`` 等是游戏侧 API，
  调用 = 远程执行 = 注入，红线明令禁止）；
* 本模块不落盘任何东西，落盘由调用方决定。
"""
from __future__ import annotations

import struct
from typing import Dict, List, NamedTuple, Optional

from .reader_base import TypeInfoReader
from .process import SafeProcess

# ------------------------------------------------------------------ 判据

#: attr 低字节 = 在场上（本方半场）
ATTR_LOW_FIELD_SELF = 0x01
#: attr 低字节 = 在场上（对方半场，0x40 位）
ATTR_LOW_FIELD_OPP = 0x41
#: attr 低字节 = 墓地（观测值 0x2008 / 0xA008 的低字节）
ATTR_LOW_GRAVE = 0x08

ZONE_FIELD = 'field'
ZONE_GRAVE = 'grave'


def zone_of(attr: Optional[int]) -> Optional[str]:
    """按 ``cardAttribute`` 标志的低字节判区域（公开区返回名字，隐藏区 None）。

    隐藏区（手牌/牌组/额外/里侧的编码细节）**故意**不细分 ——
    本模块只关心「是不是公开区」，隐藏区一律当 None。
    """
    if attr is None:
        return None
    low = attr & 0xFF
    if low in (ATTR_LOW_FIELD_SELF, ATTR_LOW_FIELD_OPP):
        return ZONE_FIELD
    if low == ATTR_LOW_GRAVE:
        return ZONE_GRAVE
    return None


def is_public_zone(attr: Optional[int], is_face: int) -> bool:
    """这张卡当前是否对双方公开 —— step 2 的核心判据。

    * 在场上（低字节 ``0x01``/``0x41``）且 ``isFace == 1`` -> 公开（表侧）
    * 墓地（低字节 ``0x08``）-> 恒公开（墓地没有里侧概念，不看 isFace）
    * 其它（手牌/牌组/额外、里侧盖放）-> 不公开
    """
    zone = zone_of(attr)
    if zone == ZONE_GRAVE:
        return True
    if zone == ZONE_FIELD:
        return bool(is_face)
    return False


# ------------------------------------------------------------------ 内存布局
# （全部实机校准过；出处：tmp/ 差分探针，2026-09-12）

#: 卡对象（uidBases[槽位] 指向的那块）
CARD_NPOS = 0x14        # int32：高 16 位 = 卡序号，低 16 位 = 区域码
CARD_WUID = 0x18        # int32：uid（= uidTbl 的 key）
CARD_STPROP = 0x20      # 指针 -> 卡属性
CARD_ISFACE = 0x28      # byte：1 = 表侧
CARD_READ_LEN = 0x2A    # 读卡对象时读这么长（覆盖到 isFace/isTurn）

#: stProp（卡属性）里我们唯一关心的两个字段
STPROP_CARDID = 0x10    # int32：卡 id（游戏内 id，如 20500）
STPROP_OWNER = 0x18     # byte：0 = 我方 / 1 = 对方
STPROP_READ_LEN = 0x19

#: IL2CPP 托管数组的通用布局（对象头 0x10 + bounds 0x8 + length）
ARRAY_LENGTH = 0x18     # 元素个数（uintptr 宽度，这里按 8 字节读）
ARRAY_DATA = 0x20       # 元素起始

#: C# ``Dictionary<int,int>`` 的布局（IL2CPP，int 为 4 字节）
DICT_ENTRIES = 0x18     # entries 数组指针
DICT_COUNT = 0x20       # int：有效条目数
DICT_ENTRY_STRIDE = 0x10
DICT_ENTRY_HASH = 0x00  # hashCode；被删条目为 0
DICT_ENTRY_KEY = 0x08
DICT_ENTRY_VAL = 0x0C
#: 多读几个 entry 的余量：entries 里有 free slot 时 count 会小于占位数
DICT_EXTRA = 4


class CardTableOffsets(NamedTuple):
    """``currentEngineData`` 类里三张表的字段偏移（按字段名从 ``list_fields`` 查出）。"""

    uid_tbl: int
    uid_bases: int
    card_attribute: int


class RawCard(NamedTuple):
    """一张卡的原始状态（内部/诊断用 —— 含隐藏区信息，别直接落盘）。"""

    uid: int
    card_id: Optional[int]    # stProp 未填时是 None
    owner: Optional[int]      # 0 = 我方 / 1 = 对方
    n_pos: int                # 原始值（高 16 位是卡序号，用的时候要 mask）
    is_face: int
    attr: Optional[int]       # cardAttribute 标志（P0/P1 两本已合并）


class PublicCard(NamedTuple):
    """一张**当前公开**的卡 —— 本模块对外的唯一输出。"""

    uid: int
    card_id: int
    owner: int                # 0 = 我方 / 1 = 对方
    zone: str                 # 'field'（场上表侧）/ 'grave'（墓地）


# ------------------------------------------------------------------ 底层读取

def _read_dict_int_int(proc: SafeProcess, addr: int) -> Dict[int, int]:
    """读一本 ``Dictionary<int,int>``（entries 里 hashCode 非 0 的才算数）。"""
    out: Dict[int, int] = {}
    if not addr:
        return out
    raw_head = proc.try_read(addr + DICT_ENTRIES, 8 + 4)
    if not raw_head or len(raw_head) < 12:
        return out
    entries = struct.unpack_from('<Q', raw_head, 0)[0]
    count = struct.unpack_from('<i', raw_head, 8)[0]
    if not entries or count <= 0:
        return out
    blob = proc.try_read(entries + ARRAY_DATA,
                         DICT_ENTRY_STRIDE * (count + DICT_EXTRA))
    if not blob:
        return out
    n = min(count + DICT_EXTRA, len(blob) // DICT_ENTRY_STRIDE)
    for i in range(n):
        h, _nxt, key, val = struct.unpack_from('<iiii', blob,
                                               i * DICT_ENTRY_STRIDE)
        if h:
            out[key] = val
        if len(out) >= count:
            break
    return out


def _array_len(proc: SafeProcess, arr: int) -> int:
    """IL2CPP 托管数组的元素个数。"""
    raw = proc.try_read(arr + ARRAY_LENGTH, 8)
    if not raw or len(raw) < 8:
        return 0
    return int(struct.unpack_from('<Q', raw, 0)[0])


def read_card_table(proc: SafeProcess, ced: int,
                    offsets: CardTableOffsets) -> List[RawCard]:
    """遍历 ``currentEngineData`` 的三张表，读出每张卡的原始状态。

    以 ``uidTbl`` 为准（它在，卡才在）；单张卡读失败就跳过，
    不会因为一张坏卡丢掉整张表。
    """
    uid_slot = _read_dict_int_int(proc, _field_ptr(proc, ced, offsets.uid_tbl))
    if not uid_slot:
        return []

    # cardAttribute：P0/P1 两本合并（两本低位一致，合起来更抗缺条目）
    attr_by_uid: Dict[int, int] = {}
    ca_ptr = _field_ptr(proc, ced, offsets.card_attribute)
    if ca_ptr:
        n = _array_len(proc, ca_ptr)
        for i in range(min(n, 4)):
            try:
                d = proc.read_ptr(ca_ptr + ARRAY_DATA + i * 8)
            except Exception:
                d = 0
            if d:
                for k, v in _read_dict_int_int(proc, d).items():
                    # 两本低位一致（只差 0x8000 位）：先到的（P0）优先，P1 补缺
                    attr_by_uid.setdefault(int(k), v)

    ub_ptr = _field_ptr(proc, ced, offsets.uid_bases)
    if not ub_ptr:
        return []

    cards: List[RawCard] = []
    for uid in sorted(uid_slot):
        slot = uid_slot[uid]
        try:
            obj = proc.read_ptr(ub_ptr + ARRAY_DATA + slot * 8)
        except Exception:
            continue
        if not obj:
            continue
        raw = proc.try_read(obj, CARD_READ_LEN)
        if not raw or len(raw) < CARD_READ_LEN:
            continue
        sp = struct.unpack_from('<Q', raw, CARD_STPROP)[0]
        card_id: Optional[int] = None
        owner: Optional[int] = None
        if sp:
            spb = proc.try_read(sp, STPROP_READ_LEN)
            if spb and len(spb) >= STPROP_READ_LEN:
                card_id = struct.unpack_from('<i', spb, STPROP_CARDID)[0]
                owner = spb[STPROP_OWNER]
        cards.append(RawCard(
            uid=int(uid),
            card_id=card_id,
            owner=owner,
            n_pos=struct.unpack_from('<i', raw, CARD_NPOS)[0],
            is_face=raw[CARD_ISFACE],
            attr=attr_by_uid.get(int(uid)),
        ))
    return cards


def read_public_cards(proc: SafeProcess, ced: int,
                      offsets: CardTableOffsets) -> List[PublicCard]:
    """读「当前公开的卡」—— 本模块的主入口（底层版，偏移已解析好时用）。

    只输出通过 :func:`is_public_zone` 判据的卡；隐藏区的卡在这一步就被丢掉。
    """
    out: List[PublicCard] = []
    for c in read_card_table(proc, ced, offsets):
        if not is_public_zone(c.attr, c.is_face):
            continue
        if not c.card_id:
            # stProp 还没填（理论上公开区的卡都已入场，这里只是兜底）
            continue
        out.append(PublicCard(
            uid=c.uid,
            card_id=c.card_id,
            owner=int(c.owner or 0),
            zone=zone_of(c.attr) or ZONE_FIELD,
        ))
    return out


def _field_ptr(proc: SafeProcess, obj: int, offset: int) -> int:
    """按偏移读一个指针字段；失败给 0（调用方按空表处理）。"""
    try:
        return proc.read_ptr(obj + offset)
    except Exception:
        return 0


# ------------------------------------------------------------------ 上层封装

class PublicCardReader(TypeInfoReader):
    """一站式读取：``Engine`` 实例 -> 当前公开卡列表。

    用法（真机）::

        with SafeProcess(pid) as proc:
            mirror = find_all_metadata_mirrors(proc, header)[0]
            stbl = read_string_table(meta_path, header)
            ti = find_typeinfo(proc, mirror, stbl, 'YgomGame.Duel', 'Engine')[0]
            eng = find_singleton(proc, ti, slot=0x08)
            cards = PublicCardReader(proc, mirror).read(ti, eng)

    不在对局（``Engine`` 实例为空 / ``threadWork`` 为空，比如主界面、
    回放暂停在加载点）时返回空列表，不抛异常。
    """

    #: 只按这些**字段名**取偏移 —— 白名单之外的字段一概不碰。
    #: 这是红线设计：按名字取，查不到就失败，绝不会误读到禁区字段。
    ENGINE_FIELDS = ('threadWork',)
    THREADWORK_FIELDS = ('currentEngineData',)
    CED_FIELDS = ('uidTbl', 'uidBases', 'cardAttribute')

    # ------------------------------------------------------------ 主入口

    def read(self, engine_typeinfo: int, engine: int) -> List[PublicCard]:
        """读当前公开的卡。返回按 uid 升序；不在对局时是空列表。"""
        if not engine:
            return []
        tw = self._ptr_by_name(engine, 'threadWork', self.ENGINE_FIELDS,
                               typeinfo=engine_typeinfo)
        if not tw:
            return []
        ced = self._ptr_by_name(tw, 'currentEngineData',
                                self.THREADWORK_FIELDS)
        if not ced:
            return []
        return self.read_ced(ced)

    def read_ced(self, ced: int) -> List[PublicCard]:
        """``currentEngineData`` 地址已知时的入口（诊断脚本用）。"""
        if not ced:
            return []
        ti = self.proc.read_ptr(ced)
        if not ti:
            return []
        fields = self._fields_of(ti)
        offsets = CardTableOffsets(
            uid_tbl=self._offset(fields, 'uidTbl', self.CED_FIELDS),
            uid_bases=self._offset(fields, 'uidBases', self.CED_FIELDS),
            card_attribute=self._offset(fields, 'cardAttribute',
                                        self.CED_FIELDS),
        )
        return read_public_cards(self.proc, ced, offsets)
