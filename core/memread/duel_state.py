# -*- coding: utf-8 -*-
"""只读「公开对局状态」—— 回合 / 阶段 / 谁在行动 / 双方 LP。

这些**全是屏幕上就有的信息**，不碰任何卡表，是 M6 状态机的实时输入。

链路（2026-09-12 实机逐段校准，用户播回放配合报屏上数字）
------------------------------------------------------------
::

    # 回合 / 阶段
    Engine(+0x08 s_instance).threadWork(+0x128)
      -> threadWork.currentEngineData(+0x10)
      -> ced.duelInfo(+0x10)
           nTurnNum      int32  **0 基**（0 = 第 1 回合）
           nCurrentPhase int32  0=抽牌 1=准备 2=主要1 3=战斗 4=主要2 5=结束 7=回合初始化
           bWhichTurnNow int32  0 = P0（先手）/ 1 = P1
           nCurrentStep  int32  战斗内步骤（进战斗时 = 3）
           nCurrentDmgStep int32 2=伤害前 3=伤害计算（LP 就在这里变） 4=伤害后
           nMovablePhase int32  当前可操作的阶段

    # 双方 LP
    DuelClient(+0x00 instance).<duelHUD>k__BackingField(+0xC8)
      -> <nearLPCounter>k__BackingField(+0x1F8) / <farLPCounter>k__BackingField(+0x200)
           m_LPCurrent    int32  当前 LP
           m_LPBeforeTrans int32 这次变化前的 LP

校准依据：用户报「第三回合对方战斗阶段」↔ ``nTurnNum=2`` / ``nCurrentPhase=3``；
那局 LP 4000 → 0 的瞬间正好落在 ``phase=3`` + ``nCurrentDmgStep=3``（伤害计算）。

⚠️ **near / far ≠ 我 / 对手**
    它表示的是「在屏幕的哪一侧」，**不是**归属。回放里 near 一般是主人（录制者），
    但实时对局里会不会换边**没有验证过**，所以本模块只给 near/far 这种中性名字，
    谁是谁交给上层结合座位（``matches.seat``）判断。
    —— 本项目已经在「硬币 vs 先后手」上混过一次概念，这里不再重蹈。

红线
----
* 全程只读（``SafeProcess``，权限位 0x0410）；
* 偏移**按字段名查**、只认白名单，查不到抛 ``ValueError``（见 ``reader_base``）；
* **不整块读实例** —— 逐个字段取，避免碰到 ``Engine`` 里的禁区字段；
* 不调用游戏函数；
* 本模块不落盘，落盘由调用方决定。
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

from .reader_base import TypeInfoReader

#: 阶段编号 -> 名字（实机校准，用户报屏上阶段对号确认）
PHASE_NAMES: Dict[int, str] = {
    0: 'draw',        # 抽牌
    1: 'standby',     # 准备
    2: 'main1',       # 主要阶段 1
    3: 'battle',      # 战斗
    4: 'main2',       # 主要阶段 2
    5: 'end',         # 结束
    7: 'init',        # 回合初始化
}


class DuelState(NamedTuple):
    """一帧公开对局状态。读不到的项一律 ``None``（不拿 0 冒充）。"""

    turn: Optional[int] = None          # nTurnNum，**0 基**
    phase: Optional[int] = None         # nCurrentPhase
    turn_holder: Optional[int] = None   # bWhichTurnNow，0=P0（先手）1=P1
    step: Optional[int] = None          # nCurrentStep
    dmg_step: Optional[int] = None      # nCurrentDmgStep
    movable_phase: Optional[int] = None  # nMovablePhase
    near_lp: Optional[int] = None       # 近侧 LP（屏幕上那一侧，未必是我）
    far_lp: Optional[int] = None        # 远侧 LP
    near_lp_before: Optional[int] = None
    far_lp_before: Optional[int] = None

    @property
    def turn_number(self) -> Optional[int]:
        """人看的回合数（**1 基**）。"""
        return None if self.turn is None else self.turn + 1

    @property
    def phase_name(self) -> Optional[str]:
        return PHASE_NAMES.get(self.phase) if self.phase is not None else None

    def to_dict(self) -> Dict[str, object]:
        """给落盘/日志用的扁平 dict（带派生的回合数与阶段名）。"""
        d = dict(self._asdict())
        d['turn_number'] = self.turn_number
        d['phase_name'] = self.phase_name
        return d


class DuelStateReader(TypeInfoReader):
    """``Engine`` + ``DuelClient`` 实例 -> :class:`DuelState`。

    用法（真机）::

        with SafeProcess(pid) as proc:
            mirror = find_all_metadata_mirrors(proc, header)[0]
            stbl = read_string_table(meta_path, header)
            eng_ti = find_typeinfo(proc, mirror, stbl, 'YgomGame.Duel', 'Engine')[0]
            dc_ti = find_typeinfo(proc, mirror, stbl, 'YgomGame.Duel', 'DuelClient')[0]
            r = DuelStateReader(proc, mirror)
            st = r.read(eng_ti, find_singleton(proc, eng_ti, slot=0x08),
                        dc_ti, find_singleton(proc, dc_ti, slot=0x00))

    不在对局、或链路中途断了（回放暂停在加载点等）时，
    对应字段是 ``None``，**不抛异常** —— 采样器每秒都在读，不能炸。
    """

    #: 只按这些**字段名**取偏移，白名单外一概不碰（红线设计）。
    ENGINE_FIELDS = ('threadWork',)
    THREADWORK_FIELDS = ('currentEngineData',)
    CED_FIELDS = ('duelInfo',)
    DUELINFO_FIELDS = ('nTurnNum', 'nCurrentPhase', 'bWhichTurnNow',
                       'nMovablePhase', 'nCurrentStep', 'nCurrentDmgStep')
    DUELCLIENT_FIELDS = ('duelHUD',)
    DUELHUD_FIELDS = ('nearLPCounter', 'farLPCounter')
    LP_FIELDS = ('m_LPCurrent', 'm_LPBeforeTrans')

    #: (DuelState 字段名, duelInfo 里的字段名)
    _TURN_MAP = (('turn', 'nTurnNum'),
                 ('phase', 'nCurrentPhase'),
                 ('turn_holder', 'bWhichTurnNow'),
                 ('movable_phase', 'nMovablePhase'),
                 ('step', 'nCurrentStep'),
                 ('dmg_step', 'nCurrentDmgStep'))

    # ------------------------------------------------------------ 内部

    def read_turn(self, engine_typeinfo: int, engine: int) -> Dict[str, Optional[int]]:
        """只读回合一侧。链路断在哪，哪之后就是空 dict。"""
        out: Dict[str, Optional[int]] = {}
        tw = self._ptr_by_name(engine, 'threadWork', self.ENGINE_FIELDS,
                               typeinfo=engine_typeinfo)
        if not tw:
            return out
        ced = self._ptr_by_name(tw, 'currentEngineData', self.THREADWORK_FIELDS)
        if not ced:
            return out
        di = self._ptr_by_name(ced, 'duelInfo', self.CED_FIELDS)
        if not di:
            return out
        for attr, name in self._TURN_MAP:
            out[attr] = self._i32(di, name, self.DUELINFO_FIELDS)
        return out

    def read_lp(self, duelclient_typeinfo: int,
                duelclient: int) -> Dict[str, Optional[int]]:
        """只读 LP。``near``/``far`` 指屏幕方位，**不是归属**。"""
        out: Dict[str, Optional[int]] = {}
        hud = self._ptr_by_name(duelclient, 'duelHUD', self.DUELCLIENT_FIELDS,
                                typeinfo=duelclient_typeinfo)
        if not hud:
            return out
        for side, fname in (('near', 'nearLPCounter'), ('far', 'farLPCounter')):
            obj = self._ptr_by_name(hud, fname, self.DUELHUD_FIELDS)
            if not obj:
                continue
            lp = self._i32(obj, 'm_LPCurrent', self.LP_FIELDS)
            before = self._i32(obj, 'm_LPBeforeTrans', self.LP_FIELDS)
            if lp is not None:
                out[side + '_lp'] = lp
            if before is not None:
                out[side + '_lp_before'] = before
        return out

    # ------------------------------------------------------------ 主入口

    def read(self, engine_typeinfo: int, engine: int,
             duelclient_typeinfo: Optional[int] = None,
             duelclient: int = 0) -> DuelState:
        """读一帧。``engine`` 为空（不在对局）就直接返回全空的 :class:`DuelState`。"""
        values: Dict[str, Optional[int]] = {}
        if engine:
            values.update(self.read_turn(engine_typeinfo, engine))
        if duelclient:
            values.update(self.read_lp(duelclient_typeinfo or 0, duelclient))
        return DuelState(**{f: values.get(f) for f in DuelState._fields})
