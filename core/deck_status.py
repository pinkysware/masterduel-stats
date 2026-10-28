# -*- coding: utf-8 -*-
"""我方卡组「识别状态」判定 —— 进匹配界面时提示用户的那套三态。

为什么需要它（用户 2026-10-28 口径）
------------------------------------
untapped 在**进入匹配界面时**会弹一个状态徽章：
- 识别成功 -> 绿勾 + 「准备好决斗了」（弹 ~2.5 秒后收起）
- 识别失败 -> 红叉 + 「未知牌组」+ 一行具体怎么办（三种文案，按卡组 id 前缀分）

我们要把这个机制同步到自己的悬浮窗横幅（`ui/deck_banner.py`）。
本模块只做**纯判定**（不碰 UI、不碰内存），便于单测锁死。

untapped 的原始判据（`Core.js` 的 `updateCurrentDeck`）::

    selectedState = deck                       ? 'known'
      : id.startsWith('EXH_') || id.includes('Loaner') ? 'unknown_reselect'
      : id.startsWith('DT_')  || id.startsWith('CUP_') ? 'unknown_edit'
      :                                                  'unknown'

三种 unknown 的差别只在**给用户的文案**（要不要让用户重新选/去编辑）。

我们自己的口径
--------------
我们的 ``my_deck.json`` 里没存卡组 id 的前缀（存的是识别出来的**名字** +
``source``）。所以不能照抄前缀判据。

★★ **判据是「卡表有没有归到参照库」，不是「有没有名字」**（用户 2026-10-28）：
卡组名是玩家随手起的（「烙印40」「狱神神艺」），**有名字 ≠ 认出来了**。

=============== =====================================================
状态             条件
=============== =====================================================
``known``        ``name`` 非空，``src`` 可信，**且**
                 ``game_name`` 为空或不等于 ``name``（= 名字来自卡表反推）
``unknown_live`` 读到了卡表（``ids_fingerprint`` 有值）但没归到参照库：
                 ① 没名字；或 ② ``game_name == name``（只是把游戏内名抬上来）
``unknown``      没名字也没指纹（完全没读到）
=============== =====================================================

⚠️ 历史：本文档头部曾写「`known` = 有名字 + `src` 可信」——
**那是旧口径，已被上面这张表取代**（2026-10-28 审查意见 P2-1 抓到的
「文档改了、代码改了、docstring 没改」）。改判据务必同步这里。

⚠️ **这里不判断「该不该让用户去编辑卡组」** —— 那是 UI 文案的事，
   按模式（DC 杯/活动局要编辑，天梯不需要）在 `hint_for()` 里派生。
"""
from __future__ import annotations

from typing import Optional

#: 状态值（与 untapped 的语义对齐，但名字用我们自己的口径）
STATE_KNOWN = 'known'
STATE_UNKNOWN_LIVE = 'unknown_live'   # 读到了卡表但没识别出名字
STATE_UNKNOWN = 'unknown'             # 什么都没读到

#: 认为「可信」的来源（`my_deck.json` 的 ``src`` 字段）。
#: ⚠️ 与 `core.storage.AUTO_MY_DECK_NAME_SRCS` **不是同一个概念**：
#:    那个是「入库时可被自动刷新覆盖的来源」，这里是「UI 上可以放心显示」。
#:    例如 ``log-deck``（活动租借卡组名）在 UI 上完全可以显示，
#:    但它入库时是要被自动刷新覆盖的 —— 两者别混。
TRUSTED_SOURCES = frozenset({
    'm5-guess',          # 自动识别（编辑界面/巅峰 ratedeck/DC 杯 CUPDeckList）
    'manual',            # 用户手动选
    'manual-fix',        # 手动修正
    'log-deck',          # 活动/租借局从日志读的卡组名
    'current-deck',      # 当前卡组兜底（24h 内）
})

#: 给用户的下一步提示（**中文原文就是 i18n 键**，见 `ui/i18n.py` 的约定）。
#: 放在模块顶层而不是内联字符串，是为了让单测能直接断言、也避免两处写岔。
_HINT_PICK_NAME = '卡表已读到 —— 点卡图手动选名字'
_HINT_RESELECT = '点卡组框，重新选一次卡组'
_HINT_EDIT = '点卡组框，「编辑牌组」后返回'
_HINT_CHANGE = '点卡组框，「更换牌组」后重新选'


#: 模式 -> 是否需要用户去「编辑卡组」才能读卡表。
#: DC 杯（12）/ 活动局（mode_group == 'event'）的卡表**只在编辑界面期间可读**，
#: 这点已由 2026-10-25 实机确认（DC 杯 `CUPDeckList` 退出编辑就清零）。
MODES_NEED_EDIT = frozenset({'dc', 'event'})


def deck_state(state: Optional[dict]) -> str:
    """``my_deck.json`` 的状态 dict -> 三态之一（**纯函数**）。

    :param state: ``core.my_deck.load()`` 的返回值（可能为 ``{}`` / ``None``）

    ★ **判据是「卡表有没有归到参照库」，不是「有没有名字」**（用户 2026-10-28）：
    卡组名是玩家随手起的（「烙印40」「狱神神艺」），有名字**不等于**认出来了。
    识别成功 = 名字来自卡表反推（`game_name` 与 `name` 不同，或没有 `game_name`
    但有 `confidence`）；只有游戏内名字、卡表没归上 -> 仍算「未识别完整」。
    """
    if not state:
        return STATE_UNKNOWN
    name = str(state.get('name') or '').strip()
    src = str(state.get('src') or '').strip()
    has_ids = bool(state.get('ids_fingerprint'))
    game_name = str(state.get('game_name') or '').strip()
    if not name:
        return STATE_UNKNOWN_LIVE if has_ids else STATE_UNKNOWN
    # 名字可信（来源在名单里）**且**不是「原样照抄游戏内名」才算 known。
    # `game_name` 存在且与 name 相同 = 卡表没归上，只是把游戏内名字抬了上来。
    if src in TRUSTED_SOURCES or not src:
        if game_name and game_name == name:
            return STATE_UNKNOWN_LIVE      # 读到了卡表，但没归到参照库
        return STATE_KNOWN
    return STATE_UNKNOWN_LIVE if has_ids else STATE_UNKNOWN


def hint_for(state_value: str, mode_group: Optional[str] = None) -> str:
    """状态 + 模式 -> **给用户的下一步提示**（纯函数，返回 i18n key）。

    untapped 的三种文案对应关系：

    ====================== ============================================
    untapped               我们
    ====================== ============================================
    ``unknown_reselect``   活动/租借局 -> ``deck_hint_reselect``
                           （「点卡组框重新选」）
    ``unknown_edit``       DC 杯 -> ``deck_hint_edit``
                           （「点卡组框 → 编辑牌组 → 返回」）
    ``unknown``            其它 -> ``deck_hint_change``
                           （「点卡组框 → 更换牌组 → 重新选」）
    ====================== ============================================

    ``known`` 不需要提示，返回空串。
    """
    if state_value == STATE_KNOWN:
        return ''
    if state_value == STATE_UNKNOWN_LIVE:
        # 读到了卡表但没名字 —— 不用让用户做任何操作，手动选一下名字即可
        return _HINT_PICK_NAME
    #: ⚠️ 这个分支必须**先查常量**再用 —— 原来 `MODES_NEED_EDIT` 定义了却没人用
    #: （审查意见 P3-4 抓到的死常量）。它表达的是「**这些模式的卡表只在编辑
    #: 界面期间可读**」这个实机事实（DC 杯 `CUPDeckList` 退出编辑即清零，
    #: 2026-10-25 确认），所以放在判定链的最前面：不在这个集合里的模式
    #: 根本不该提示用户「去编辑牌组」（天梯/巅峰编辑与否都能读）。
    if mode_group not in MODES_NEED_EDIT:
        return _HINT_CHANGE
    #: 下面这两条只在「确实需要去编辑」的模式里分文案
    if mode_group == 'event':
        return _HINT_RESELECT          # 活动/租借局：重新选一次即可
    if mode_group == 'dc':
        return _HINT_EDIT             # DC 杯：必须进编辑界面才读得到卡表
    return _HINT_CHANGE


def should_announce(prev: Optional[str], curr: str) -> bool:
    """是否该弹「准备好决斗了」那种一次性提示（**纯函数**）。

    untapped 的判据（`YgomDeckStatus` 的 effect）::

        if (state === 'known' && prevState !== 'known' && prevState !== null)

    即：**从「非 known 的已知状态」变成 known** 才弹。
    ``prevState === null``（第一次进界面）**不弹** —— 避免一进匹配界面就闪一下。

    我们照抄这个语义，但允许 ``prev is None`` 之外的输入：
    - ``prev is None`` -> 不弹（首次观测，没有「变成」这件事）
    - ``prev == 'known'`` -> 不弹（本来就是好的）
    - 其它 -> 弹
    """
    if prev is None:
        return False
    if prev == STATE_KNOWN:
        return False
    return curr == STATE_KNOWN
