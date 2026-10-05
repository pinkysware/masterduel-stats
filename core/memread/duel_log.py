# -*- coding: utf-8 -*-
"""逐卡动作流（M1 第四块）—— 读游戏内「对局记录」的 ShowAction 载荷。

为什么需要它
------------
``opp_revealed``（对手打出的卡）是判断对手卡组的**唯一线索**，
而它目前只能从 Untapped 的日志里解析出来。那条日志不是游戏写的 ——
是 Untapped 读游戏内存读出来再打印的（它的原生模块导入表里只有
``OpenProcess`` / ``ReadProcessMemory`` / ``VirtualQueryEx``）。
**所以内存里必然有同一份数据**，落点就是游戏自己的对局记录控制器
``YgomGame.Duel.DuelLogController`` —— 游戏内那个「对局记录」面板的数据源。

本模块把这份数据只读出来，目标是把 ``opp_revealed`` 的数据源从日志换成自研。

元素布局（2026-09-12 离线校准，**日志 JSON 对账 64/64 全等**）
--------------------------------------------------------------
::

    +0x00  LogDataSide   datal : int32 dataint
                                u8 boolbits / u8 databyte0 / u8 databyte1 / u8 datatype
    +0x08  LogDataSide   datar : 同上
    +0x10  LogDataCenter datac : int32 dataint0 / int32 dataint1
                                u8 boolbits / u8 databyte0 / u8 databyte1 / u8 datatype
    +0x1C  uint32 填充（观测值 0 / 1）
    ------------------------------------------------------------------
     共 32 字节，**值类型内联**，不是指针数组

⚠️ 上一棒记的「元素 = 8 字节」是错的（抓快照的探针按 16 字节/元素读了
``arr_len*16`` 字节，实际只覆盖到 64/118 条）。32 字节的判据是
*偏移 32k 处的 datal 与日志第 k 条逐字节相等，64/64*，datar/datac 同样 64/64。

字段语义（用 Untapped 的 ``YgomDuelState.handleAction`` 当真值表，113 组配对）
-----------------------------------------------------------------------------
=========================== ==============================================
字段                        含义
=========================== ==============================================
``dataint & 0xFFFF``        **卡 id**
``dataint >> 16``           同一张卡在**另一套 id** 里的编号（0 = 无）
                            ⚠️ 不是第二张卡 —— 实测 (19188,22747) 与
                            (18732,23494) 各自都是同一张卡的两个 id
``databyte1``               **归属：0 = P0 / 1 = P1**（112/112 全对）
``databyte0``               区域/格位（见 :data:`ZONE_HAND`）
                            ⚠️ **抽卡是例外**：所有 DRAW 都记 13（手牌），
                            哪怕它其实来自卡组 —— 抽卡记的是「卡到了哪儿」，
                            其它动作记的是「卡从哪儿来」
``datatype``                datal/datar 恒 1，datac 恒 3
``boolbits``                卡的**公开状态位**（SHOW/INSIGHT/INDENT/FACE，见下节）
=========================== ==============================================

``datac`` 语义（2026-09-14 钉死动作枚举）
------------------------------------------
拿同一场的 131 条 ``YgomDuelState.handleAction`` 当真值表，113 组配对：

* **``datac.databyte1`` = 动作类型枚举（113/113 无一例外）** ——
  动作流从此能直接输出「这一下是什么动作」，不用再从区域变化反推。
  Untapped 标 ``undefined`` 的 49/51 只是它自己的映射表不全。
* ``datac.boolbits``：bit0 恒 1；bit5(32) ≈ **P1 归属**（110/113，
  3 个反例：2 条 SPSUMMON + Turn 特例 —— 强趋势、非铁律）；
  bit4(16)/bit6(64) 观测到只在特定动作族出现，**语义未解，别猜**。
* ``datac.databyte0`` 恒 0（Turn 特例 = 1）。
* ``dataint0/1``：Turn 条目装的是 **双方 LP**（实测 -3500 / 2900）；
  对手的 HANDOPEN(31) 装过 15/27/67（未解）。

``boolbits`` 语义（2026-09-13 钉死）
------------------------------------
把同一场对局的 128 条 ShowAction 与 Untapped 日志的 131 条
``YgomDuelState.handleAction`` 逐条配对（113 组非 MoveMulti），
交叉出每个 bit 的含义 —— 位名对应 metadata 里的 ``CheckBit_*`` 系列：

* **bit0（1）SHOW**：恒 1 —— 条目有效标志。
* **bit2（4）INSIGHT**：卡面情报**已被公开确认**（CONFIRM / FLIPTOP /
  HANDOPEN 之后、场上表侧、墓地/除外）。SEARCH/DRAW/SETH 那一刻还没公开，
  不带此位。
* **bit3（8）INDENT**：卡**在隐藏区域或盖放状态**（卡组 / 手牌 / 盖放的卡）。
  15/15 无反例：SEARCH 6（卡组→手牌）、DRAW 2（对手视角的抽卡）、
  SETH 2（盖放）、盖放陷阱被 CONFIRM 2、RETURN 回手牌 1、LOCKON 指向盖卡 2。
* **bit1（2）FACE**：✅ **2026-09-13 钉死 = 守备表示**
  （1=守备 / 0=攻击表示或非怪兽卡）。证据（601 组跨 7 局）：
  ATTACK 15/15=0（攻击表示必攻）、DEFTOATKCHANGE 4/4=0、ATKTODEFCHANGE 1/1=1、
  SUMMON（表侧攻召）11/11=0、SPSUMMON 混合（3 守备/11 攻击，合理）、
  魔陷卡 132/132=0、bit1=1 的 59 条全部是纯怪兽卡 0 反例。
  当年「7 条全在怪兽区、z=0 反例」的困惑解开了：**跟格位无关，跟表示形式有关**。
* **bit1（2）FACE（旧观测，已被上条取代）**：观测 7 条全是怪兽区（z=1/2/4）的
  HAPPEN/LOCKON/BREAK/EXPLOSION，疑似「表示形式（表侧/攻击表示）」，
  但有反例（z=0 的 HAPPEN 不带它），样本不足别当判据用。
* 观测组合只有 5 种：1 / 5 / 7 / 9 / 13；bit4 及以上未在 datal 出现
  （datac 的 boolbits 用的是 bit0/4/5/6，语义**未解**，别猜）。

⚠️ `m_LogBaseItemList` ``ShowChain``（2026-09-14 离线解开）
------------------------------------
元素 = **12 字节**：``[u32 连锁序号<<16 | 卡id][u32 归属 0/1][u32 已处理? 0/1]``。
30/30 全是合法卡 id；「归属」与已知卡全一致。首块连锁的叙事完全吻合规则：
登记顺序 1→2→3→4，随后**逆序 4→3→2→1** 处理（后发先至）。
「已处理?」的确切触发时机（登记/结算/单链直结）等更多样本再细分。

``ShowTag`` / ``ShowText``（**未解**，只记观测）
------------------------------------------------
* ``ShowTag``：4 字节，值只有 0/1（本局 12 条里 1×9、0×3）。语义未知。
* ``ShowText``：4 字节 ``dataint``，观测 0x0001000C / 0x0001000D
  （低 16 位 = 12/13，高 16 位 = 1）。像「文本 id」，语义未知。
* 两者都只有个位数样本，**别猜**。

``ShowCardName``（2026-09-13 离线解开）
--------------------------------------
元素 = **6 字节**：``[u16 卡id][u8 归属 0=P0/1=P1][u8 ?][u8 ?][u8 恒0]``。
108/108 全是合法卡 id；「归属」与 ShowAction 的 ``databyte1`` 交叉 **27/27 无反例**
（该局没有双方共用的卡）。byte1/byte2 恒 0/1、只在特定组合出现，
语义未解 —— 等差分样本，**别猜**。以前「没对上」是因为拿 16 字节步长读的；
6 字节步长重读即全解（:func:`parse_card_names`）。

⚠️ m_LogBaseItemList 的元素也是 **32 字节内联 struct**
（+0 loglabel 字符串指针、+8 dataindex int32），按 8 字节步长读只会
每 4 个槽命中 1 个（labels 前 89 条就是这么残的）。
重抓工具：``tmp/regrab_logbase.py``（对局中一条命令拿全 353 条）。

⚠️ 其它载荷数组**不是** 32 字节（``ShowPhase`` 看着像 4 字节 int）。
本模块**只解 ShowAction** —— 它就是 ``opp_revealed`` 的来源，别顺手推广。

红线
----
* 全程只读（``SafeProcess``，权限位 0x0410）；不写内存、不注入、不调用游戏函数；
* 字段偏移**按字段名查**（``list_fields`` 白名单），查不到就抛 ``ValueError``；
* 读的是**游戏已经展示给玩家看的对局记录**（和日志同源），不含隐藏信息。
"""
from __future__ import annotations

import struct
from typing import Iterator, List, NamedTuple, Optional, Tuple

from .reader_base import TypeInfoReader

#: 一条 ShowAction 载荷的字节数（离线校准，见模块 docstring）
ENTRY_SIZE = 32

#: datal / datar / datac 在条目内的偏移
OFF_DATAL = 0x00
OFF_DATAR = 0x08
OFF_DATAC = 0x10

_SIDE = struct.Struct('<iBBBB')      # dataint + 4 个 u8 = 8 字节
_CENTER = struct.Struct('<iiBBBB')   # dataint0 + dataint1 + 4 个 u8 = 12 字节

# --- 区域/格位码（databyte0）-------------------------------------------
# 13/14/15 是拿 handleAction 的 source/target 对出来的，硬证据；
# 0..12 是场上格位（观测到 Monster0/2/4 -> 0/2/4，Magic9/12 -> 9/12），不细分。
ZONE_HAND = 13
ZONE_EXTRA = 14
ZONE_DECK = 15
#: <= 这个值的都是场上格位（怪兽区 / 魔陷区）
ZONE_FIELD_MAX = 12

ZONE_NAMES = {
    ZONE_HAND: 'Hand',
    ZONE_EXTRA: 'Extra',
    ZONE_DECK: 'Deck',
}

#: ``datac.databyte1`` = 动作类型枚举（2026-09-14 钉死，113/113）。
#: 名字沿用 Untapped ``handleAction`` 的写法（它就是本项目的事实真值表）；
#: 19/31 都是「翻开」族 —— 19 观测于 P0 的手牌/卡组顶翻开、31 观测于对手
#: （P1）的手牌翻开，疑似带「谁在看」语义，样本够了再拆。
#: 49/51 是 Untapped 都不认识的动作（只观测到目标=墓地/手牌类）。
#: M6 刀 1 补名（S-1，M3 结项划转）：RELEASE=15 / MOVE=28 / VANISH=29 /
#: ATKTODEFCHANGE=37 / PUT=39 —— 至此表 29 值，已观测枚举不再有裸数字。
ACTION_KIND_NAMES = {
    0: 'Turn',
    1: 'DRAW',
    2: 'SUMMON',
    3: 'SPSUMMON',
    6: 'SPSUMMON_SYNC',
    9: 'SPSUMMON_LINK',
    10: 'ATTACK',
    11: 'HAPPEN',
    12: 'DISABLE',
    13: 'SETH',
    14: 'SETV',
    15: 'RELEASE',
    17: 'CONFIRM',
    18: 'EXCLUDE',
    19: 'FLIPTOP',
    20: 'EXPLOSION',
    21: 'BREAK',
    23: 'LOCKON',
    24: 'SENDTOGRAVE',
    26: 'SEARCH',
    28: 'MOVE',
    29: 'VANISH',
    30: 'EFFECTIMPL',
    31: 'HANDOPEN',
    33: 'RETURN',
    37: 'ATKTODEFCHANGE',
    39: 'PUT',
    49: 'UNKNOWN_A',
    51: 'UNKNOWN_B',
}

#: —— ShowText / ShowTag（2026-09-13 离线研究，详见 docs/未解语义研究.md）——
#: **ShowText**（4B）：`dataint = (1<<16) | 序号`，序号 = 本次游戏启动以来的
#: ShowText 递增计数（游戏重启归 2 重计）；**文本内容本体不在条目里**。
#: **ShowTag**（4B，值 0/1）：位置恒为 ShowAction→ShowTag→ShowCardName 三联组，
#: 且只挂两类前置动作：side=1（对手）的 DRAW、kind=Turn 的条目；
#: 值按局有主值（快投降局全 0、长局多为 1），候选语义「对手的卡是否已知」未钉死。


def action_kind_name(kind: int) -> str:
    """动作枚举 -> 名字；未知值返回 ``'KIND_<n>'``（不丢信息，也不猜）。"""
    return ACTION_KIND_NAMES.get(kind, 'KIND_%d' % kind)

#: ShowChain 载荷的元素尺寸（2026-09-14 离线解开）
CHAIN_SIZE = 12

#: ShowCardName 载荷的元素尺寸（2026-09-13 离线解开）
CARD_NAME_SIZE = 6
#: ShowCardName 里「归属」字节在元素内的偏移（0=P0 / 1=P1）
CARD_NAME_SIDE_OFFSET = 2

#: boolbits 位标志（datal 侧；bit1=FACE 语义待定，别当判据）
BOOL_SHOW = 0x01        # bit0：条目有效（观测恒 1）
BOOL_FACE = 0x02        # bit1：守备表示（1=守备；0=攻击/非怪兽，601 组钉死）
BOOL_INSIGHT = 0x04     # bit2：卡面情报已公开确认
BOOL_INDENT = 0x08      # bit3：卡在隐藏区域 / 盖放状态

#: IL2CPP 托管数组：对象头 0x10 + bounds 0x8 + length 0x8 = 元素从 0x20 起
ARRAY_DATA = 0x20

#: 控制器字段白名单（白名单之外的字段一概不碰）
CONTROLLER_FIELDS = ('m_LogBaseItemList', 'm_DataList_ShowAction', 'm_Host')
#: C# ``List<T>`` 的内部字段名
LIST_FIELDS = ('_items', '_size')
WHITELIST = CONTROLLER_FIELDS + LIST_FIELDS


# ---------------------------------------------------------------- 数据结构

class LogDataSide(NamedTuple):
    """一方的一条日志数据（8 字节内联）。"""

    dataint: int
    boolbits: int
    databyte0: int
    databyte1: int
    datatype: int

    @property
    def card_id(self) -> int:
        """主卡 id（低 16 位）。"""
        return self.dataint & 0xFFFF

    @property
    def alt_id(self) -> int:
        """高 16 位 = 这张卡的**标准版 id**（MD 异画卡是独立条目）；0 = 无。

        ⚠️ 不是第二张卡。实测样本：1257527515 = (19188<<16)|22747，
        都是 S:P小夜骑士 —— 19188 是标准版（在 ygocdb 卡表里），
        22747 是 MD 异画独立条目（不在 ygocdb），且 22747 = 对局里
        实际用的那张（低 16 位到处用的那套 id = 实际卡）。
        异画/标准判定法：同名多 id 组里，**在 ygocdb
        （ygocdb.com.cards/cards.json）有条目的是标准版，不在的是 MD 异画**。
        """
        return (self.dataint >> 16) & 0xFFFF

    @property
    def player(self) -> int:
        """归属：0 = P0、1 = P1（112/112 对账通过）。"""
        return self.databyte1

    @property
    def zone(self) -> int:
        """区域/格位码（见 :data:`ZONE_HAND`）。"""
        return self.databyte0

    @property
    def is_empty(self) -> bool:
        """整条全 0 = 这一侧没有数据（多数条目只有 datal 有值）。"""
        return not (self.dataint or self.boolbits or self.databyte0
                    or self.databyte1 or self.datatype)

    def card_ids(self) -> Tuple[int, ...]:
        """这条涉及的卡 id（丢掉 0）。

        ⚠️ 只返回低 16 位 —— 高 16 位是**同一张卡**的另一套 id（见
        :attr:`alt_id`），算第二张会把对手的多算成一张（实测就多出了
        一张 S:P小夜骑士）。
        """
        return (self.card_id,) if self.card_id else ()


class LogDataCenter(NamedTuple):
    """条目中间那 12 字节（datac）。"""

    dataint0: int
    dataint1: int
    boolbits: int
    databyte0: int
    databyte1: int
    datatype: int


class LogEntry(NamedTuple):
    """一条 ShowAction 载荷（32 字节）。"""

    datal: LogDataSide
    datar: LogDataSide
    datac: LogDataCenter
    padding: int

    def card_ids(self) -> Tuple[int, ...]:
        """datal + datar 里出现过的卡 id（去重、保序）。"""
        out = list(self.datal.card_ids())
        for cid in self.datar.card_ids():
            if cid not in out:
                out.append(cid)
        return tuple(out)


class ActionEvent(NamedTuple):
    """摊平后的一个「卡 × 归属」事件 —— 下游（opp_revealed）要的形状。"""

    index: int        # 载荷序号
    card_id: int
    player: int       # 0 / 1
    zone: int
    side: str         # 'l'（取自 datal）/ 'r'（取自 datar）
    boolbits: int
    datatype: int
    action_kind: int = 0      # datac.databyte1（动作类型枚举，见 ACTION_KIND_NAMES）
    center_boolbits: int = 0  # datac.boolbits（bit5≈P1 归属，bit4/6 未解）


# ---------------------------------------------------------------- 纯解析
# 这一层不碰进程、不联网，全部离线可测（fixture 在 tests/fixtures/duellog_snap/）

def parse_side(buf: bytes, off: int = 0) -> LogDataSide:
    """8 字节 -> :class:`LogDataSide`。"""
    if len(buf) - off < _SIDE.size:
        raise ValueError('需要 %d 字节，只有 %d' % (_SIDE.size, len(buf) - off))
    return LogDataSide(*_SIDE.unpack_from(buf, off))


def parse_center(buf: bytes, off: int = 0) -> LogDataCenter:
    """12 字节 -> :class:`LogDataCenter`。"""
    if len(buf) - off < _CENTER.size:
        raise ValueError('需要 %d 字节，只有 %d' % (_CENTER.size, len(buf) - off))
    return LogDataCenter(*_CENTER.unpack_from(buf, off))


def parse_entry(buf: bytes, off: int = 0) -> LogEntry:
    """:data:`ENTRY_SIZE` 字节 -> :class:`LogEntry`。"""
    if len(buf) - off < ENTRY_SIZE:
        raise ValueError('需要 %d 字节，只有 %d' % (ENTRY_SIZE, len(buf) - off))
    pad, = struct.unpack_from('<I', buf, off + 0x1C)
    return LogEntry(
        datal=parse_side(buf, off + OFF_DATAL),
        datar=parse_side(buf, off + OFF_DATAR),
        datac=parse_center(buf, off + OFF_DATAC),
        padding=pad,
    )


def parse_entries(buf: bytes, count: Optional[int] = None) -> List[LogEntry]:
    """整块载荷数组 -> 按序的条目列表。

    ``count`` 不给就按缓冲区能整除的条数取，余数丢弃 ——
    抓快照时经常读不满整个数组。
    """
    if count is None:
        count = len(buf) // ENTRY_SIZE
    if count < 0:
        raise ValueError('count 不能为负：%r' % count)
    return [parse_entry(buf, i * ENTRY_SIZE) for i in range(count)]


def iter_action_events(entries) -> Iterator[ActionEvent]:
    """条目列表 -> 摊平的事件流（一张卡一条）。

    一条载荷里 datal / datar 各自可能带 1~2 张卡，这里全部摊开，
    ``side`` 记下它来自哪一侧。
    """
    for i, e in enumerate(entries):
        for tag, s in (('l', e.datal), ('r', e.datar)):
            if s.is_empty:
                continue
            for cid in s.card_ids():
                yield ActionEvent(index=i, card_id=cid, player=s.player,
                                  zone=s.zone, side=tag, boolbits=s.boolbits,
                                  datatype=s.datatype,
                                  action_kind=e.datac.databyte1,
                                  center_boolbits=e.datac.boolbits)


def revealed_by(entries, player: int, limit: Optional[int] = None
                ) -> List[int]:
    """某一方露出的卡 id —— 去重、保序，可选截断。

    这就是 ``opp_revealed_ids`` 的形状（``tools/archive_matches.py`` 里记的是
    「ACT 事件里 side != seat 的卡 id，去重保序取前 30」）。
    ``player`` 传 P0/P1；至于是不是对手，由上层结合 ``seat`` 判 ——
    本模块不猜（本项目在「硬币 vs 先后手」上混过概念，这里不重蹈）。
    """
    out, seen = [], set()
    for ev in iter_action_events(entries):
        if ev.player != player or ev.card_id in seen:
            continue
        seen.add(ev.card_id)
        out.append(ev.card_id)
        if limit is not None and len(out) >= limit:
            break
    return out


def zone_name(z: int) -> str:
    """区域码 -> 名字。只有 13/14/15 是对出来的，其它原样返回 ``Zone<n>``。"""
    return ZONE_NAMES.get(z, 'Zone%d' % z)


def is_field_zone(z: int) -> bool:
    """是不是场上格位（0..12）。"""
    return 0 <= z <= ZONE_FIELD_MAX


# ---------------------------------------------------------------- 其它载荷数组
# ⚠️ 各数组的**元素类型不一样**，别拿 ShowAction 的 32 字节去套。
# 下面是这局快照里能对上的三个（判据见各条注释）。

#: ``m_DataList_ShowPhase`` = ``List<int32>``，元素 4 字节，值就是阶段码
PHASE_SIZE = 4
PHASE_NAMES = {
    0: 'draw', 1: 'standby', 2: 'main1', 3: 'battle',
    4: 'main2', 5: 'end', 7: 'turn_init',
}

#: ``m_DataList_ShowTurn`` 的元素 = 24 字节（6 个 int32）
TURN_SIZE = 24

#: ``m_DataList_MoveMultiHeader`` 的元素**也是 32 字节**，跟 ShowAction 同类型。
#: （按 8 字节解时真身出现在 slot 0 / 4，间隔正好 32 —— 一开始被它骗过一次。）
MULTI_HEADER_SIZE = ENTRY_SIZE


class TurnRecord(NamedTuple):
    """一条 ShowTurn：谁的第几个回合 + 当时双方 LP。

    ⚠️ ``lp_a`` / ``lp_b`` 是**中性命名**：本局只有 ``lp_a`` 变过（8000 → 2900），
    而变化发生在 P1 的回合、最终判负 —— 看着像「行动方的 LP」，
    但**只有一个样本**，别当定论（本项目在硬币/先后手上混过概念，这里不重蹈）。
    """

    player: int     # 0 / 1
    turn: int       # **0 基**（0 = 第 1 回合）
    marker: int     # 本局恒为 1，含义未明
    lp_a: int
    lp_b: int
    reserved: int   # 本局恒为 0

    @property
    def turn_number(self) -> int:
        """1 基回合数（给人看的）。"""
        return self.turn + 1


def parse_phases(buf: bytes, count: Optional[int] = None) -> List[int]:
    """``m_DataList_ShowPhase`` -> 阶段码序列。

    判据：本局 23 条解出来是
    ``抽牌→准备→主要1→结束 / 抽牌→准备→主要1→战斗→主要2→结束 / …``
    —— 与 ``core/memread/duel_state.py`` 里**实机校准**的阶段枚举完全吻合，
    等于两条独立链路互证了同一个口径。
    """
    if count is None:
        count = len(buf) // PHASE_SIZE
    return [struct.unpack_from('<i', buf, i * PHASE_SIZE)[0]
            for i in range(count)]


def parse_turns(buf: bytes, count: Optional[int] = None) -> List[TurnRecord]:
    """``m_DataList_ShowTurn`` -> 回合记录序列（元素 24 字节 = 6 个 int32）。

    判据：本局 5 条解出来的玩家序列 ``1,0,1,0,1`` 与日志的
    ``Turn 1..5 for player 1/0/1/0/1`` **5/5 全对**，回合号是 0 基。
    """
    if count is None:
        count = len(buf) // TURN_SIZE
    return [TurnRecord(*struct.unpack_from('<6i', buf, i * TURN_SIZE))
            for i in range(count)]


class CardNameEntry(NamedTuple):
    """ShowCardName 的一条：对局记录里「报过卡名」的事件。"""

    card_id: int
    side: int          # 0 = P0 / 1 = P1
    flag1: int         # 0/1，语义未解
    flag2: int         # 0/1，语义未解


def parse_card_names(blob: bytes, count: int) -> List[CardNameEntry]:
    """解 ShowCardName 载荷（6 字节/元素）。

    :param blob: 原始字节（容量超量读也行，只取前 ``count`` 条）
    :param count: 数组的有效条数
    :raises ValueError: blob 不够 ``count`` 条时
    """
    if len(blob) < count * CARD_NAME_SIZE:
        raise ValueError(
            'ShowCardName 载荷不足：需要 %d 字节，只有 %d'
            % (count * CARD_NAME_SIZE, len(blob)))
    out = []
    for i in range(count):
        cid, side, f1, f2, _pad = struct.unpack_from(
            '<HBBBB', blob, i * CARD_NAME_SIZE)
        out.append(CardNameEntry(card_id=cid, side=side, flag1=f1, flag2=f2))
    return out


class ChainEntry(NamedTuple):
    """ShowChain 的一条：一次连锁的登记/处理。"""

    chain_index: int     # 连锁序号（1 起）
    card_id: int
    side: int            # 0 = P0 / 1 = P1
    flag2: int           # 0/1，疑似「已处理」；确切时机待更多样本


def parse_chains(blob: bytes, count: int) -> List[ChainEntry]:
    """解 ShowChain 载荷（12 字节/元素）。

    :param blob: 原始字节（容量超量读也行，只取前 ``count`` 条）
    :param count: 数组的有效条数
    :raises ValueError: blob 不够 ``count`` 条时
    """
    if len(blob) < count * CHAIN_SIZE:
        raise ValueError(
            'ShowChain 载荷不足：需要 %d 字节，只有 %d'
            % (count * CHAIN_SIZE, len(blob)))
    out = []
    for i in range(count):
        di, a, b = struct.unpack_from('<III', blob, i * CHAIN_SIZE)
        out.append(ChainEntry(chain_index=di >> 16, card_id=di & 0xFFFF,
                              side=a, flag2=b))
    return out


def parse_multi_headers(buf: bytes, count: Optional[int] = None
                        ) -> List[LogEntry]:
    """``m_DataList_MoveMultiHeader`` -> 条目序列（元素 **32 字节**，同 ShowAction）。

    ⚠️ 这里**必须传 ``count``**（= ``List._size``）：bin 是按「容量×16 字节」
    抓的，容量之后的字节是数组外面的内存，按整除取会多读出垃圾条目
    （本局容量 4 / 实际 2，整除会得到 2 条 —— 恰好对；但容量 4 且实际 3 时会多 1 条）。

    判据：本局第 1 条的 ``datal`` = ``dataint=18730, boolbits=9, databyte0=13,
    databyte1=1, datatype=1``，与日志第 1 条 ShowAction 的 ``datal`` 逐字节相等
    （它就是开局那组「公开手牌」的头）；第 2 条对应日志第 6 条（第二组的头）。
    """
    if count is None:
        count = len(buf) // MULTI_HEADER_SIZE
    return [parse_entry(buf, i * MULTI_HEADER_SIZE) for i in range(count)]


# ---------------------------------------------------------------- 实机读数

class DuelLogReader(TypeInfoReader):
    """从 ``YgomGame.Duel.DuelLogController`` 只读 ShowAction 动作流。

    只在**对局中 / 结算画面**有实例（主界面下控制器不存在）。
    用法::

        r = DuelLogReader(proc, mirror=m)
        if r.locate(ti):                       # ti = DuelLogController 的 TypeInfo
            for ev in r.iter_action_events():
                ...
    """

    def __init__(self, proc, mirror=None) -> None:
        super(DuelLogReader, self).__init__(proc, mirror)
        self.controller: int = 0

    # ------------------------------------------------------------ 定位

    def locate(self, typeinfo: int, name: str = 'DuelLogController',
               host_class: str = 'DuelClient') -> bool:
        """找到控制器实例；找到写 ``self.controller`` 并返回 True。

        实例发现 = 全进程搜「指向本类 TypeInfo 的指针」，再**双重过滤**：
        自身类名 == ``name`` 且 ``m_Host`` 指向的对象类名 == ``host_class``。
        单靠类型指针有海量假阳性（实测 165 个候选全是假的），过滤是必需的。
        """
        fields = self._fields_of(typeinfo)
        host_off = self._offset(fields, 'm_Host', WHITELIST)
        if 'm_DataList_ShowAction' not in fields:
            raise ValueError(
                'DuelLogController 字段表里没有 m_DataList_ShowAction —— 不猜偏移'
                '（字段表：%s）' % (', '.join(sorted(fields)) or '(空)'))
        for hit in self.proc.scan(struct.pack('<Q', typeinfo)):
            if hit < 0x10000:
                continue
            try:
                if self.proc.read_ptr(hit) != typeinfo:
                    continue
                if self._class_name(hit) != name:
                    continue
                host = self.proc.read_ptr(hit + host_off)
                if not host or host < 0x10000:
                    continue
                if self._class_name(host) != host_class:
                    continue
            except Exception:
                continue
            self.controller = hit
            return True
        return False

    def _class_name(self, obj: int) -> str:
        """对象的类名（对象头 +0x00 = TypeInfo，TypeInfo +0x10 = name 指针）。"""
        try:
            ti = self.proc.read_ptr(obj)
            return self.proc.read_cstring(self.proc.read_ptr(ti + 0x10), 96)
        except Exception:
            return ''

    # ------------------------------------------------------------ 读数

    def _list_parts(self, obj: int) -> Tuple[int, int]:
        """``List<T>`` -> ``(元素区地址, 条数)``；链路断了给 ``(0, 0)``。

        字段缺失（白名单外 / 字段表里查不到）**照抛** ``ValueError`` ——
        偏移不猜；只有「内存读不动」才按链路断了处理。
        """
        if not obj or obj < 0x10000:
            return (0, 0)
        fields = self._fields_of(self._typeinfo_at(obj))
        items_off = self._offset(fields, '_items', WHITELIST)
        size_off = self._offset(fields, '_size', WHITELIST)
        try:
            items = self.proc.read_ptr(obj + items_off)
            size, = struct.unpack('<i', self.proc.read(obj + size_off, 4))
        except Exception:
            return (0, 0)
        if not items or items < 0x10000 or size <= 0 or size > 200000:
            return (0, 0)
        return (items + ARRAY_DATA, size)

    def read_payload_bytes(self, label: str, entry_size: int = ENTRY_SIZE,
                           limit: Optional[int] = None) -> bytes:
        """任意载荷数组的原始字节（不在对局里返回空串）。

        ⚠️ **元素尺寸必须调用方给** —— 各数组类型不同（ShowAction 32、
        ShowTurn 24、ShowPhase 4…），拿一个尺寸套全部会读飞。

        ``label`` 是 ``ShowAction`` / ``ShowTurn`` 这种，内部拼成
        ``m_DataList_<label>``；字段名不在白名单里照抛 ``ValueError``。
        """
        if not self.controller:
            return b''
        fname = 'm_DataList_%s' % label
        wl = CONTROLLER_FIELDS + (fname,)
        fields = self._fields_of(self._typeinfo_at(self.controller))
        off = self._offset(fields, fname, wl)
        try:
            arr_obj = self.proc.read_ptr(self.controller + off)
        except Exception:
            return b''
        base, size = self._list_parts(arr_obj)
        if not base or not size:
            return b''
        if limit is not None:
            size = min(size, limit)
        try:
            return self.proc.read(base, size * entry_size)
        except Exception:
            return b''

    def read_action_bytes(self, limit: Optional[int] = None) -> bytes:
        """ShowAction 载荷数组的原始字节（不在对局里返回空串）。"""
        return self.read_payload_bytes('ShowAction', ENTRY_SIZE, limit)

    def read_actions(self, limit: Optional[int] = None) -> List[LogEntry]:
        """ShowAction 条目（按序）。"""
        raw = self.read_action_bytes(limit)
        return parse_entries(raw) if raw else []

    def iter_action_events(self, limit: Optional[int] = None
                           ) -> Iterator[ActionEvent]:
        """实机动作事件流（一张卡一条）。"""
        return iter_action_events(self.read_actions(limit))

    def revealed(self, player: int, limit: Optional[int] = 30) -> List[int]:
        """某一方露出的卡 id（去重保序），实机版。"""
        return revealed_by(self.read_actions(), player, limit)
