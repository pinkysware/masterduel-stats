# -*- coding: utf-8 -*-
"""M6 应用服务 —— 对局状态机（内存侧实时主采样）。

它在整条链里的位置
------------------
::

    内存（一手）  core/memread/duel_state.py   回合 / 阶段 / 双方 LP
                  core/memread/public_cards.py 当前公开的卡
                  （gameMode / pvpFinished 也在 Engine 实例上）

                  ↓ 每秒喂一次

    ★ 本模块      DuelSessionTracker            状态机 + 一局的轨迹
                  ↓
    落盘/入库     下一刀接：data/archive/duel_sessions.jsonl + Storage

**为什么状态机是内存线的关口**：在此之前内存只是个「探针」（要人手动跑脚本看数）。
做成常驻状态机之后，它才能自己判断「一局开始了 / 打完了」，
把整局的轨迹自动归档 —— 那才是接进产品。

状态与迁移（判据全部实机验证过）
--------------------------------
::

    空闲 ──Engine 实例出现──────────> 对局中
                                        │  每秒采样：回合 / 阶段 / LP / 公开卡
                                        │  只记「变化点」，不记每秒全量
                                        ▼
                                    pvpFinished: 0 -> 1
                                        │  ← 收局信号
                                        ▼
                                    空闲（这一局归档到 records）

⚠️ **收局为什么不能用「实例消失」**：实测对局打完后 ``Engine`` 实例还会在
结算画面上活好几分钟，等它消失太迟，连续两局会被并成一条。
``pvpFinished``（+0x120）由 0 变 1 才是及时信号。

⚠️ **一次读失败不算收局**：对局切换的一瞬间读不到实例是常态。
``in_duel=None``（读失败）直接跳过，不许当成「对局结束」。

⚠️ **结算画面上不许开新局**：靠 ``_closed_by_finish`` 挡住，
直到实例真的消失（回到主界面）才允许开下一局。

**胜负结果不在这里** —— ``pvpFinished`` 只说明「打完了」，不知道谁赢。
胜负仍由日志（``Game ended: result=Win/Lose``）提供，
本模块的产出与它按时间窗口配对（见 ``docs/当前进度.md`` 的「内存是增强不是替换」）。
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Sequence

from .memread.duel_state import DuelState

#: 对局状态机的三个状态
STAGE_IDLE = 'idle'        # 空闲（没在对局里）
STAGE_IN_DUEL = 'in_duel'  # 对局中
STAGE_ENDED = 'ended'      # 刚打完（本帧刚收到收局信号）

#: 事件类型（字符串常量，落盘友好）
EV_DUEL_START = 'duel_start'
EV_TURN = 'turn'
EV_PHASE = 'phase'
EV_LP = 'lp'
EV_PUBLIC_CARDS = 'public_cards'
EV_DUEL_END = 'duel_end'


class DuelEvent(NamedTuple):
    """状态机推给上层的一件事。``data`` 只放这一件事相关的字段。"""

    kind: str
    at: str
    data: Dict[str, object]


class DuelSession(object):
    """一局的**内存侧**记录。

    与 ``matches`` 表那条记录的关系：本对象只装内存看得见的东西
    （模式、回合轨迹、LP 轨迹、公开卡累积），
    胜负 / 我方卡组 / 对手名 这些日志独有的字段**不在这里**，
    由日志侧那条记录补 —— 两边按时间窗口配对。
    """

    def __init__(self, started_at: str) -> None:
        self.started_at = started_at
        self.ended_at: Optional[str] = None
        self.end_reason: Optional[str] = None      # pvpFinished / instance-gone
        self.game_modes: List[int] = []
        #: 采样到的帧数。**不含收局那一帧** —— 收局帧走的是另一条分支
        #: （它只负责把 ``last_state`` 定成终局状态，不再计样本数）。
        self.samples = 0
        #: 状态变化点（不记每秒全量）。每项形如
        #: ``{'at','turn','phase','holder','near_lp','far_lp'}``
        self.timeline: List[Dict[str, object]] = []
        #: 公开卡的累积（按首次出现的先后）。只存卡 id，不存别的。
        self.public_card_ids: List[int] = []
        #: 最后已知的一帧（收局时写进去，便于事后看终局状态）
        self.last_state: Optional[Dict[str, object]] = None

    # ------------------------------------------------------------ 派生

    @property
    def max_turn(self) -> Optional[int]:
        """本局到达过的最大回合数（**1 基**）。没采到就给 None。"""
        best = None
        for row in self.timeline:
            n = row.get('turn')
            if isinstance(n, int) and (best is None or n > best):
                best = n
        if best is None and self.last_state:
            n = self.last_state.get('turn')
            if isinstance(n, int):
                best = n
        return None if best is None else best + 1

    @property
    def game_mode(self) -> Optional[int]:
        """本局的模式：只认「唯一那个」，多个就取最后一个（与旧工具一致）。"""
        if not self.game_modes:
            return None
        return self.game_modes[-1]

    @property
    def duration_sec(self) -> Optional[float]:
        return None      # 时间戳是字符串，交给调用方算（本模块不解析时间）

    def to_dict(self) -> Dict[str, object]:
        return {
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'end_reason': self.end_reason,
            'samples': self.samples,
            'game_modes': list(self.game_modes),
            'game_mode': self.game_mode,
            'max_turn': self.max_turn,
            'timeline': list(self.timeline),
            'public_card_ids': list(self.public_card_ids),
            'last_state': self.last_state,
        }


# ---------------------------------------------------------------- 状态机


class DuelSessionTracker(object):
    """把「一串采样」压成「一局一条记录 + 一串事件」。

    **纯逻辑** —— 不碰内存、不碰磁盘、不解析时间戳，随便喂随便测。

    用法::

        tr = DuelSessionTracker()
        for ts, sample in samples:
            for ev in tr.observe(ts, sample.in_duel, sample.game_mode,
                                 sample.finished, sample.state,
                                 sample.public_card_ids):
                ...  # 推给 UI / 落盘
        tr.records          # 打完的每一局（``DuelSession.to_dict()``）
    """

    #: 一局最多记多少条轨迹（防止异常长局把内存吃光；
    #: 正常一局变化点也就几百条）
    MAX_TIMELINE = 5000

    def __init__(self) -> None:
        self.records: List[Dict[str, object]] = []
        self.open: Optional[DuelSession] = None
        self.stage: str = STAGE_IDLE
        self._closed_by_finish = False
        self._prev: Optional[DuelState] = None
        self._seen_public: set = set()

    # ------------------------------------------------------------ 主入口

    def observe(self, timestamp: str, in_duel: Optional[bool],
                game_mode: Optional[int] = None,
                finished: Optional[int] = None,
                state: Optional[DuelState] = None,
                public_card_ids: Optional[Sequence[int]] = None
                ) -> List[DuelEvent]:
        """喂一次采样，返回本次产生的事件（可能是空列表）。

        :param in_duel: ``Engine`` 实例在不在。``None`` = 这次没读成（不算收局）
        :param finished: ``pvpFinished``，1 = 这局已经打完
        :param state: :class:`~core.memread.duel_state.DuelState`
        :param public_card_ids: 当前公开的卡 id（``None`` = 这次没读）
        """
        events: List[DuelEvent] = []

        if in_duel is None:
            return events                      # 读失败 —— 什么都不做

        if not in_duel:
            self._closed_by_finish = False
            rec = self._close(timestamp, 'instance-gone')
            if rec is not None:
                events.append(DuelEvent(EV_DUEL_END, timestamp,
                                        {'end_reason': 'instance-gone',
                                         'session': rec}))
            self.stage = STAGE_IDLE
            self._prev = None
            self._seen_public = set()
            return events

        # ---- 实例在 ----
        if finished:
            rec = self._close(timestamp, 'pvpFinished')
            self._closed_by_finish = True
            self.stage = STAGE_ENDED
            if rec is not None:
                rec['last_state'] = state.to_dict() if state else None
                events.append(DuelEvent(EV_DUEL_END, timestamp,
                                        {'end_reason': 'pvpFinished',
                                         'session': rec}))
            return events

        if self._closed_by_finish:
            # 结算画面：实例还在，但这不是新的一局
            return events

        # 开新局
        if self.open is None:
            self.open = DuelSession(timestamp)
            events.append(DuelEvent(EV_DUEL_START, timestamp,
                                    {'at': timestamp}))
        sess = self.open
        self.stage = STAGE_IN_DUEL
        sess.samples += 1

        if game_mode is not None and int(game_mode) not in sess.game_modes:
            sess.game_modes.append(int(game_mode))

        events.extend(self._track_state(timestamp, sess, state))
        events.extend(self._track_public(timestamp, sess, public_card_ids))
        return events

    # ------------------------------------------------------------ 轨迹

    def _track_state(self, timestamp: str, sess: DuelSession,
                     state: Optional[DuelState]) -> List[DuelEvent]:
        """只记「变化点」—— 回合/阶段/LP 哪一个变了就落一条。"""
        events: List[DuelEvent] = []
        if state is None:
            return events

        prev = self._prev
        self._prev = state
        row = {
            'at': timestamp,
            'turn': state.turn,
            'phase': state.phase,
            'phase_name': state.phase_name,
            'holder': state.turn_holder,
            'near_lp': state.near_lp,
            'far_lp': state.far_lp,
        }
        sess.last_state = row

        if prev is None:
            # 第一帧：记下来（但不必单独发事件，DUEL_START 已经发了）
            if len(sess.timeline) < self.MAX_TIMELINE:
                sess.timeline.append(row)
            return events

        if state.turn != prev.turn:
            events.append(DuelEvent(EV_TURN, timestamp, {
                'turn': state.turn,
                'turn_number': state.turn_number,
                'holder': state.turn_holder,
            }))
        if state.phase != prev.phase:
            events.append(DuelEvent(EV_PHASE, timestamp, {
                'phase': state.phase,
                'phase_name': state.phase_name,
                'turn': state.turn,
            }))
        if (state.near_lp != prev.near_lp) or (state.far_lp != prev.far_lp):
            events.append(DuelEvent(EV_LP, timestamp, {
                'near_lp': state.near_lp,
                'far_lp': state.far_lp,
                'near_lp_before': prev.near_lp,
                'far_lp_before': prev.far_lp,
                'turn': state.turn,
                'phase_name': state.phase_name,
            }))

        if events and len(sess.timeline) < self.MAX_TIMELINE:
            sess.timeline.append(row)
        return events

    def _track_public(self, timestamp: str, sess: DuelSession,
                      ids: Optional[Sequence[int]]) -> List[DuelEvent]:
        """公开卡只累积「新出现的」—— 收起牌不回退（已经公开过的就是公开过了）。"""
        events: List[DuelEvent] = []
        if ids is None:
            return events
        fresh = [int(i) for i in ids if int(i) not in self._seen_public]
        if not fresh:
            return events
        for i in fresh:
            self._seen_public.add(i)
            sess.public_card_ids.append(i)
        events.append(DuelEvent(EV_PUBLIC_CARDS, timestamp, {'new_ids': fresh}))
        return events

    # ------------------------------------------------------------ 收局

    def _close(self, timestamp: str, reason: str) -> Optional[Dict[str, object]]:
        """收局；没有开着的对局就返回 None。

        ⚠️ 一局必须在**采到过至少一帧**之后才允许收 —— 否则
        「实例一闪而过」也会记成一条空记录。
        """
        if self.open is None:
            return None
        sess = self.open
        if sess.samples <= 0:
            self.open = None
            return None
        self.open = None
        sess.ended_at = timestamp
        sess.end_reason = reason
        rec = sess.to_dict()
        self.records.append(rec)
        return rec

    def flush(self, timestamp: str) -> Optional[Dict[str, object]]:
        """外部喊停（工具被 Ctrl-C、进程要退）时把开着的对局收掉。

        跟 ``_close`` 的区别：``end_reason`` 标明是外力收的，
        这样事后能看出「这条记录不完整」。
        """
        return self._close(timestamp, 'flushed')
