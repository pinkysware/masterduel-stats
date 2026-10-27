# -*- coding: utf-8 -*-
"""匹配界面监视器 —— 进匹配界面时通知 UI「该检查我方卡组了」。

为什么单独一个类（用户 2026-10-28 需求）
----------------------------------------
untapped 在**进入匹配界面**那一刻弹卡组识别状态徽章。我们要同步这个机制，
但触发源只能是「界面变化」，而界面变化来自 `data/archive/duel_state.json`
的 `view.in_matching`（采集器每拍写）。

本类只做**边沿检测**（纯状态机，不碰 IO / 不碰 Qt）：
交替喂 `in_matching` 的 True/False，输出「刚刚进入匹配」/「刚刚离开匹配」事件。
这样 UI 侧只需在 `tick()` 里喂一次，逻辑全在单测里锁死。

⚠️ 为什么不在 UI 里直接比对上一拍 —— 因为「进入匹配」和「正在匹配」要分开：
   - **进入**（False -> True）才触发一次「重新识别 + 弹提示」
   - **持续**（True -> True）不重复触发（否则每 2 秒弹一次）
   - **离开**（True -> False）收起提示

用法::

    mon = MatchingMonitor()
    events = mon.feed(in_matching)      # -> ['enter'] / ['leave'] / []
    for ev in events:
        ...
"""
from __future__ import annotations

from typing import List, Optional

#: 事件：进入匹配界面（边沿上升）
EV_ENTER = 'enter'

#: 事件：离开匹配界面（边沿下降）
EV_LEAVE = 'leave'


class MatchingMonitor(object):
    """``in_matching`` 的边沿检测器。

    :param known: 首次喂 ``True`` 时算不算「进入」——
        默认 **True**（用户可能先开软件再进匹配，那一次也该提示）。
        untapped 的 ``prevState === null`` 是**不弹**的，但那是因为它一直
        跟着游戏跑；我们的 UI 可能中途启动，所以默认算进入。
        想严格对齐 untapped 就传 ``known=False``。
    """

    def __init__(self, known: bool = True) -> None:
        self._in = None            # 上一拍的 in_matching；None = 还没喂过
        self._known = known

    @property
    def in_matching(self) -> Optional[bool]:
        """上一拍的值（``None`` = 还没喂过）。"""
        return self._in

    def feed(self, in_matching: Optional[bool]) -> List[str]:
        """喂一拍的 ``in_matching``，返回这一拍**产生**的事件列表。

        ``None``（读不到视图栈）**不改变状态** —— 采集器可能只是那一拍
        没读到，不能当成「离开匹配」把提示收掉。
        """
        if in_matching is None:
            return []
        prev = self._in
        self._in = bool(in_matching)
        if prev is None:
            # 首拍：默认算一次「进入」（用户可能先开软件再进匹配）
            return [EV_ENTER] if (self._known and self._in) else []
        if prev == self._in:
            return []
        return [EV_ENTER] if self._in else [EV_LEAVE]

    def reset(self) -> None:
        """重连采集器 / 换游戏进程时调用（上一拍的值不再可信）。"""
        self._in = None
