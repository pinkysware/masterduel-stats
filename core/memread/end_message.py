# -*- coding: utf-8 -*-
"""结算文案 -> 胜负（**已退役**的判据研究；现役判据 = 胜负状态位）。

背景
----
用户口径：**只要 Win / Lose 一个比特**，不做结束方式分类。
而托管内存里能拿到的只有 `DuelEndMessage` 的 4 个文字，
六个候选落点（`WinIcon` / `messageBase` Sprite / `DuelResultViewController` /
`profileData` / `RecordManager`）**都不含胜负** —— 详见 `docs/当前进度.md`。

★★ **2026-09-13 用户拍板：文案判据退役，状态位为唯一判据**。
本模块下方的文案表函数（`derive_result` / `lp_loser` / `is_known`）
**保留但退出生产链路**：只用于历史样本（end_messages.jsonl 老条目）的
mem_result 复算和文案变体研究，不再参与任何判据。
用户另拍板：掉线局胜负未知 -> 之后手动填；游戏一般不大改，
原生偏移的版本复核风险接受；历史日志数据回补归 M3。

★ **2026-09-13 起有了更好的判据——胜负状态位**（不依赖文案、不依赖语言）：
`DuelEndMessage.player0WinIcon/player1WinIcon`（GameObject 包装）→ `+0x10` 取
**Unity 原生 GameObject 指针** → 原生对象 **+0x46/+0x47** 两个字节：
**该玩家赢了 = 1,1；输了 = 0,0**。`player0` 恒为视角方，
所以读我方 icon 的这个字节即可。骰子拉力三结局实测全吻合
（LP0 胜 / LP0 败 / 投降败，见 `data/archive/result_states/`）。
⚠️ 该偏移是 **Unity 原生布局**（不在 metadata 里，没法按字段名查）——
游戏大版本更新后必须复核（用户已接受该风险）。
且 LP 类文案实测有**三种变体**（`{胜}将{败}的LP已削减至0` /
`LP已削减至0` / `对手的LP已削减至0`，后两种无名无方向）——
文案判据比当初以为的脆，这就是状态位当主判据的理由。

★★ **2026-09-15 深夜补两条「时序」事实**（实机探针 `tmp/_probe_winbits.py`，
盯同一个 DuelEndMessage 实例）：

* **胜负位只在结算画面那几秒内可读** —— 23:22:55 读到 `[0x46]=1 [0x47]=1`
  （对手图标亮 = 我输，与文案 `已投降` 一致）；**23:23:03 同一个实例的两个图标
  原生指针已变成 `0x0`**（结算面板的 GameObject 被回收）。
* **文字残留更久**（名字约 2 分钟才清）→ 只按「文字多」挑实例，会挑中一条
  **图标已死**的旧实例、读出 `winbits=None`。**这就是「结果为空」的真因**
  （2026-09-15 采集器连续几局 `result=NULL`，悬浮窗「最近对局」显示 `—`）。

→ 对治两条（都已在生产链路里）：`read_texts()` 给「**胜负位可读**」的候选加分、
只有「文字齐 + 胜负位可读」才提前 break；采集器侧加 **60s 结算窗口**
（整读按 2/6/15/30s 退避 = 一次收局最多 5 次扫描，两次整读之间按实例地址补读胜负位）。

文案表（**备注字段**，非判据）。2026-09-12 深夜攒齐三种，判定成立：

| `messageText`（中文客户端） | 胜负 | 证据 |
|---|---|---|
| `已投降` | **我**投降 → 我输 | 实测一局，日志 `result=Lose` |
| `对手已投降` | 对手投降 → 我赢 | 实测两局（其中一局由日志**独立**判成 `Win`） |
| `{胜者名}将{败者名}的LP已削减至0` | **句首那个名字赢** | 回放实测（`DUELIST A将DUELIST B的LP已削减至0`） |
| `对手的LP已削减至0` | 对手 LP 归零 → 我赢 | 6 例全 win（无名字，靠「对手」二字） |
| `LP已削减至0` | **我** LP 归零 → 我输 | 8 例全 lose（省略主语 = 我方） |
| `由于超出时间在对局中落败` | 我超时 → 我输 | 09-13 12:52 局 |
| `由于网络中断，决斗已经结束` | 对手掉线 → 我赢 | 4 例全 win（⚠️ 方向不在文案里，样本少） |

日文客户端（2026-10-05 用户实机样本）：

| `messageText`（日文客户端） | 胜负 | 证据 |
|---|---|---|
| `相手が降参しました` | 对手投降 → 我赢 | 用户标注（2 张截图） |
| `LPが0になりました` | 我 LP 归零 → 我输 | 用户标注；同构中文 `LP已削减至0` |
| `タイムアウトで敗北しました` | 我超时 → 我输 | 用户标注；同构中文超时文案 |
| `降参しました` | **我**投降 → 我输 | 用户标注（2026-10-05 补） |
| `相手のLPを0にしました` | 对手 LP 归零 → 我赢 | 用户标注（2026-10-05 补） |
| `通信切断によりデュエルが終了しました` | 对手掉线 → 我赢 | 用户标注（⚠️ 同上） |

英文客户端（2026-10-05 用户实机样本）：

| `messageText`（英文客户端） | 胜负 | 证据 |
|---|---|---|
| `Opponent Surrendered` | 对手投降 → 我赢 | 用户标注 |
| `You Surrendered` | **我**投降 → 我输 | 用户标注（2026-10-05 补） |
| `Opponent's LP reduced to 0!` | 对手 LP 归零 → 我赢 | 用户标注 |
| `LP reduced to 0` | 我 LP 归零 → 我输 | 用户标注 |
| `Time limit win` | 我超时胜 → 我赢 | 用户标注 |
| `Time limit loss` | 我超时负 → 我输 | 用户标注（2026-10-05 补） |

⚠️ 英文文案的撇号（`'` / `\u2019`）与结尾感叹号会随字体/编码变 —— 查表前由
`_norm_text()` 归一，表里只写规范形（**别**再把变体一条条塞进表）。

★ 两类投降文案是**不对称**的 —— `已投降` 不带名字、`对手已投降` 带「对手」二字，
所以「谁投降」分得出来。这正是上一棒把它当「唯一缺口」的那一条。

⚠️ 视角约定：`player0Name` = **视角方（我）**（三局都对上：`示例玩家` ×2 /
`DUELIST A`，且与 `profileDataMyself['name']` 同值）。

语言
----
文案判据**分语言维护**（`SURRENDER_RESULT` 投降类 / `SELF_TEXT_RESULT` 自指类
都是硬编码字符串表），换客户端语言会失配。
⚠️ **2026-10-03 起失配不再「安全」**：`derive_result` 返回 None 只是这一票不投，
而 `arbitrate_result` 的**平票规则偏向 winbit**，投降局的 winbit 又可能是
**上一局残留**（见 `arbitrate_result` docstring）—— 中文下 2:1 判对的局，
换个语言可能变成 1:1 平票、按残留 winbit 判反。
补样本仍是最省事的修法：`tools/capture_end_message.py --watch` 会自动记未知文案
并按日志标胜负，把字符串加进上面两张表即可。
已收（2026-10-05）：中文 投降 2 + 固定措辞 4；日文 投降 2 + 固定措辞 4；
英文 投降 2 + 固定措辞 4 —— **三语收齐、无缺口**。

**LP 归零类不依赖语言**：不管哪种语言，「LP 被削到 0」的那个名字就是**败方**，
而败方的名字在文案里总是**紧跟着 LP 字样**（`B的LP` / `B's LP` / `BのLP`）——
这条与语序无关（英语被动句 `B's LP was reduced to 0 by A` 败者也在 LP 前面）。
⚠️ 中文 / 英文已实证（2026-10-05：`LP reduced to 0`[lose] /
`Opponent's LP reduced to 0!`[win]）；日文仍是同构推断。

红线
----
只读公开信息（**屏幕上显示给玩家看的文字**）。本模块是**纯函数**，
不碰进程、不碰红线字段、不做任何写操作。
"""
from __future__ import annotations

import re
import struct
import time
from datetime import datetime
from typing import Optional, Sequence

#: 投降类文案（**必须精确相等** —— `'对手已投降'` 里含有子串 `'已投降'`，
#: 用 `in` / `endswith` 判会把「对手投降」误判成「我输」）。
#: 中文 2 条 + 日文 2 条 + 英文 2 条（2026-10-05 用户实机样本）；撞到再加。
SURRENDER_LOSE = '已投降'
SURRENDER_WIN = '对手已投降'
SURRENDER_RESULT = {SURRENDER_WIN: 'win', SURRENDER_LOSE: 'lose',
                    '相手が降参しました': 'win',        # 日文：对手投降
                    '降参しました': 'lose',            # 日文：我方投降
                    'Opponent Surrendered': 'win',    # 英文：对手投降
                    'You Surrendered': 'lose'}        # 英文：我方投降

#: 「固定措辞」文案 -> 胜负（**不含玩家名**，靠措辞本身定方向）。与
#: `SURRENDER_RESULT` 的区别：投降类靠「对手 / 相手」分方向，这里分两种写法 ——
#: ① **省略主语 = 我方**（`LP已削减至0` / `LP reduced to 0`）；
#: ② 写死「对手 / 相手」= 对手侧（`对手的LP已削减至0` / `相手のLPを0にしました`）。
#: ⚠️ 只收**实证过**的，依据逐条写在下面 —— 别照语言规律批量补。
SELF_TEXT_RESULT = {
    # --- 中文（data/archive/end_messages.jsonl 逐条核对）---
    'LP已削减至0': 'lose',              # 8 例全 lose（我 LP 归零）
    '对手的LP已削减至0': 'win',          # 6 例全 win（对手 LP 归零）
    '由于超出时间在对局中落败': 'lose',    # 09-13 12:52 局，winbits=[0,1]
    # ⚠️ 掉线的方向**不在文案里**（任何一方掉线都可能显示）：依据 = 中文
    #    4/4 全 win + 2026-10-05 用户标注，样本仍少；若自己掉线也显示这条
    #    就会判反 —— 撞到自己掉线的样本时立刻回来改。
    '由于网络中断，决斗已经结束': 'win',
    # --- 日文（2026-10-05 用户实机样本，5 张截图）---
    'LPが0になりました': 'lose',          # 同中文「LP已削减至0」
    'タイムアウトで敗北しました': 'lose',   # 同中文「由于超出时间在对局中落败」
    '通信切断によりデュエルが終了しました': 'win',   # 同中文「由于网络中断…」
    '相手のLPを0にしました': 'win',        # 同中文「对手的LP已削减至0」
    # --- 英文（2026-10-05 用户实机样本，6 张截图）---
    # ⚠️ 表里只写规范形：撇号（' / \u2019）与结尾感叹号由 `_norm_text` 归一。
    "Opponent's LP reduced to 0": 'win',   # 对手 LP 归零（截图带 !）
    'LP reduced to 0': 'lose',             # 我方 LP 归零
    'Time limit win': 'win',               # 超时胜
    'Time limit loss': 'lose',             # 超时负
}

#: 「名字后面紧跟 LP 字样」判法用的标记（败方的名字总是贴着 LP）
LP_MARKERS = ('的LP', "'s LP", '’s LP', 'のLP')

#: 中文客户端的 LP 模板（回退用：拿不到名字时至少能认出「这是 LP 归零类文案」）
LP_DEPLETION_RE = re.compile(r'^(?P<winner>.+?)将(?P<loser>.+?)的LP已削减至0$')

#: 视角方在 `player0`（默认，实测三局）
SIDE_MINE = 0
SIDE_RIVAL = 1

# --- 胜负状态位（2026-09-13 实锤，见模块 docstring）------------------------
#: GameObject 包装对象 -> 原生对象指针的偏移（IL2CPP 对象头 0x10 + m_CachedPtr）
GAMEOBJECT_NATIVE_PTR_OFFSET = 0x10
#: 原生 GameObject 上的胜负位（两字节恒同值；⚠️ Unity 原生布局，版本更新需复核）
WINBIT_OFFSET = 0x46


def winbits_from_native(native_bytes) -> Optional[tuple]:
    """原生 GameObject 的字节 -> ``(我方位, 对方位)``。

    :param native_bytes: 至少覆盖 ``WINBIT_OFFSET + 1`` 的字节串
    :return: 两个 **0/1** 的元组；字节太短/为空、或**取到的不是 0/1** 都返回 None

    ⚠️ 校验 0/1 是 2026-09-15 深夜补的：库里出现过 `[128, 1]` —— 那是**半死实例**
    （图标对象正在被回收）读出来的残值，不挡掉就会写进 `winbits` 列。
    """
    if not native_bytes or len(native_bytes) <= WINBIT_OFFSET:
        return None
    bits = (native_bytes[WINBIT_OFFSET], native_bytes[WINBIT_OFFSET + 1])
    if bits[0] not in (0, 1) or bits[1] not in (0, 1):
        return None
    return bits


def derive_result_from_winbits(mine_bit: int, rival_bit: int,
                               my_side: int = SIDE_MINE) -> Optional[str]:
    """胜负位 -> 视角方胜负。

    两 bit **相同**（都 0 = 两枚 icon 都没亮，或都 1）时信息不足，返回 None ——
    别硬猜（比如结算画面还没画完的瞬间）。
    """
    if mine_bit not in (0, 1) or rival_bit not in (0, 1):
        return None
    if mine_bit == rival_bit:
        return None
    mine_wins = (mine_bit == 1)
    if my_side == SIDE_RIVAL:
        mine_wins = not mine_wins
    return 'win' if mine_wins else 'lose'


def arbitrate_result(winbits=None, duel_code=None, message_text=None):
    """**三判据投票仲裁**（🔴 2026-10-03 事故：投降局 winbit 是上一局残留）。

    实锤现场（record_20261003_193726，用户自己投降认输）：文案「已投降」+ 结算码
    ``2=Lose`` 都说输，但 ``winbits=[1,0]`` 读成了赢 —— 投降结算画面**没有胜负
    图标**，那对字段保留了**上一局**（一场胜局）的值。旧口径「winbit 唯一判据」
    被陈旧值骗了，整局记反。

    仲裁规则（保守，只否决**明确少数**的判据）：

    * 三票里有两票一致 → 取多数（矛盾时记 note，供留痕）；
    * 全不一致（平票）→ 维持旧优先级 ``winbit > duel_code > text``；
    * 只有一票 → 用它。

    :return: ``(result, src, note)`` —— ``result`` 为小写 ``'win'``/``'lose'``
        （认不出给 None）；``src`` ∈ ``'winbit'/'duel_code'/'text'``；
        ``note`` 是矛盾说明（无矛盾给 None）。
    """
    votes = []
    try:
        if winbits and int(winbits[0]) != int(winbits[1]):
            v = derive_result_from_winbits(int(winbits[0]), int(winbits[1]))
            if v:
                votes.append(('winbit', v))
    except (TypeError, ValueError, IndexError):
        pass
    v = result_from_duel_code(duel_code)
    if v:
        votes.append(('duel_code', v.lower()))
    v = derive_result(message_text)
    if v:
        votes.append(('text', v.lower()))
    if not votes:
        return None, None, None
    counts = {}
    for _k, val in votes:
        counts[val] = counts.get(val, 0) + 1
    top, n = max(counts.items(), key=lambda kv: kv[1])
    if n >= 2:
        picked = top
    else:
        picked = votes[0][1]              # 平票：维持 winbit > code > text
    src = next(k for k, val in votes if val == picked)
    if len(counts) > 1:
        note = '判据冲突:%s 取 %s' % (
            '/'.join('%s=%s' % (k, v) for k, v in votes), src)
    else:
        note = None
    return picked, src, note


def result_from_duel_code(code) -> Optional[str]:
    """ClientWork 结算码 -> ``'Win'``/``'Lose'``；winbits 读不到时的兜底判据。

    大小写对齐 matches 列的 DONE_RESULTS（EM 行的 mem_result 要小写，
    调用方自己 ``.lower()``）。口径同 :func:`duel_result.decode_result_code`
    （1 胜 / 2 负，其余一律不猜）。2026-09-16 用户拍板：掉线局（无正常
    结算画面，winbits 恒 None）用它自动兜底。
    ``2 = 负`` 已实机验证（2026-09-16：winbits=(0,1) 判负 + LP0 文案 +
    ``result=2`` 三方一致）；认不出（非 1/2）仍一律 None 不猜。
    """
    from core.memread.duel_result import decode_result_code
    return decode_result_code(code)


def read_winbits(proc, instance: int,
                 icon0_off: int, icon1_off: int) -> Optional[tuple]:
    """从 `DuelEndMessage` 实例读 ``(我方位, 对方位)``。

    :param proc: 只读的 :class:`~core.memread.process.SafeProcess`
    :param instance: DuelEndMessage 实例地址
    :param icon0_off: `player0WinIcon` 字段偏移
    :param icon1_off: `player1WinIcon` 字段偏移
    :return: 两个 0/1 的元组；任何一环读不到（不在结算画面/实例已清）返回 None
    """
    bits = []
    for off in (icon0_off, icon1_off):
        try:
            go, = struct.unpack('<Q', proc.read(instance + off, 8))
            if go < 0x10000:
                return None
            native, = struct.unpack('<Q',
                                    proc.read(go + GAMEOBJECT_NATIVE_PTR_OFFSET, 8))
            if native < 0x10000:
                return None
            bits.append(proc.read(native + WINBIT_OFFSET, 1)[0])
        except Exception:
            return None
    return tuple(bits)


def _side_names(player0_name, player1_name, my_side):
    """按视角返回（我方名, 对手名）。"""
    if my_side == SIDE_RIVAL:
        return player1_name, player0_name
    return player0_name, player1_name


def lp_loser(text: str, names: Sequence[Optional[str]]) -> Optional[str]:
    """找出「名字后面紧跟 LP 字样」的那个名字 —— **LP 归零的是败方**。

    与语序无关：中文 `A将B的LP…`、英语被动 `B's LP was reduced to 0 by A`、
    日语 `AがBのLPを0に…` 里，贴着 LP 的都是败方 B。
    长名优先匹配，免得 `Alice` 撞进 `Alice2` 里。
    """
    if not text:
        return None
    clean = [n.strip() for n in names if n and n.strip()]
    for name in sorted(set(clean), key=len, reverse=True):
        start = 0
        while True:
            i = text.find(name, start)
            if i < 0:
                break
            tail = text[i + len(name): i + len(name) + 8]
            if tail.startswith(LP_MARKERS):
                return name
            start = i + 1
    return None


def _norm_text(s) -> str:
    """文案归一化（**只用于查表**，不改变原值）：直/弯撇号统一、去掉尾部感叹号。

    英文客户端的文案会随字体/编码给撇号（``'`` / ``\u2019``）和结尾感叹号
    （``!``）留变体 —— 逐条列进表里太脆。查表前统一一次，表里只写规范形。
    中文/日文文案不受影响（无 ASCII 撇号，也不用 ``!`` 结尾）。
    """
    return (s or '').strip().replace('\u2019', "'").rstrip('!').rstrip()


def _flip(result: str) -> str:
    return 'lose' if result == 'win' else 'win'


def derive_result(message_text,
                  player0_name: Optional[str] = None,
                  player1_name: Optional[str] = None,
                  my_side: int = SIDE_MINE) -> Optional[str]:
    """把结算文案翻成**视角方**的胜负。

    :param message_text: `DuelEndMessage.messageText` 的 `m_text`
    :param player0_name: `DuelEndMessage.player0Name`（视角方）
    :param player1_name: `DuelEndMessage.player1Name`（对手）
    :param my_side: 哪一侧是「我」（默认 0）
    :return: ``'win'`` / ``'lose'``；**认不出的文案返回 None** ——
             不硬猜，调用方退回日志（内存侧是增量，别拿猜的当真值）
    """
    if not message_text:
        return None
    text = message_text.strip()
    key = _norm_text(text)               # 查表用（撇号 / 结尾感叹号归一）

    # ① 投降类（精确相等；中/日/英各 1~2 条实证）
    if key in SURRENDER_RESULT:
        result = SURRENDER_RESULT[key]
        return result if my_side == SIDE_MINE else _flip(result)

    # ①b 视角方自指类（精确相等；省略主语 = 我方）
    if key in SELF_TEXT_RESULT:
        result = SELF_TEXT_RESULT[key]
        return result if my_side == SIDE_MINE else _flip(result)

    mine, rival = _side_names(player0_name, player1_name, my_side)
    mine, rival = (mine or '').strip(), (rival or '').strip()

    # ② LP 归零类（**语言无关**）：贴着 LP 字样的名字是败方
    if mine and rival:
        loser = lp_loser(text, (mine, rival))
        if loser == mine:
            return 'lose'
        if loser == rival:
            return 'win'

    # ③ 中文 LP 模板（回退：名字不全、或名字与模板对不齐时）
    m = LP_DEPLETION_RE.match(text)
    if m:
        winner = m.group('winner').strip()
        if not mine or not rival:
            return None                      # 名字不全，判不了就别判
        if winner == mine:
            return 'win'
        if winner == rival:
            return 'lose'
        return None                          # 胜者既非我方也非对手 —— 认不出
    return None


def is_known(message_text) -> bool:
    """这个文案**有没有**确定的判据（采样器用它决定要不要退回日志）。

    ⚠️ 与 :func:`derive_result` 的区别：这里**不看名字**，只问「文案认不认识」——
    LP 那类只要格式对就算认识（名字不全时 `derive_result` 仍会返回 None）。
    """
    if not message_text:
        return False
    text = message_text.strip()
    key = _norm_text(text)
    return (key in SURRENDER_RESULT
            or key in SELF_TEXT_RESULT
            or LP_DEPLETION_RE.match(text) is not None)


# ======================================================================
# M6 刀 3：结算画面 reader + 样本行组装
# （自 tools/capture_end_message.py 搬入 —— 常驻采集器 tools/duel_collector.py
#  与 one-shot 工具**共用这一份**，别复制第二份：双实现漂移本项目踩过）
# ======================================================================

import json            # noqa: E402
import os              # noqa: E402
from typing import Dict, List, Optional as _Optional   # noqa: E402

from core.memread.il2cpp import (MetadataHeader, default_metadata_path,   # noqa: E402
                                 find_all_metadata_mirrors, find_typeinfo,
                                 list_fields, read_string_table)
from core.memread.process import SafeProcess, find_pids_by_name           # noqa: E402
from core.memread.il2cpp_obj import read_string as _read_il2cpp_string    # noqa: E402

#: 游戏安装目录默认值（``MD_GAME_DIR`` 环境变量可覆盖）
DEFAULT_GAME_DIR = r'D:\steam\steamapps\common\Yu-Gi-Oh!  Master Duel'

#: ``DuelEndMessage`` 里那几个文本字段（``messageText`` 才是主备注）
TEXT_FIELDS = ('player0Name', 'player1Name', 'messageText', 'nextButtonText')

#: ``DuelEndMessage`` 这个类所在的名字空间（依次尝试）
NAMESPACES = ('YgomGame.Duel', 'YgomGame.Menu', 'Ygom')

#: 实例候选扫描（``_candidates``）的单次墙钟预算（秒）。
#: 09-26 事故：游戏内存大时全量扫描单次要 1-4 分钟，90s 的结算窗口
#: 被一次扫描吃穿、收局编排也被拖死 —— 限定私有堆（刀①）之外再加保险丝。
SCAN_TIME_BUDGET_SEC = 10.0

#: TypeInfo.parent 的偏移（爬父类链用 —— ``list_fields`` 只给本类字段）
PARENT_OFFSET = 0x58
#: 爬父类链的最大层数（TextMeshPro 那串有 7 层）
MAX_PARENT_DEPTH = 12


def default_game_dir() -> str:
    """游戏目录（``MD_GAME_DIR`` 环境变量可覆盖）。"""
    return os.environ.get('MD_GAME_DIR') or DEFAULT_GAME_DIR


def texts_key(texts) -> str:
    """文案签名（EM 去重 / 重启播种用；与 one-shot 时代口径一致）。"""
    return json.dumps(texts or {}, ensure_ascii=False, sort_keys=True)


def is_junk_texts(texts) -> bool:
    """实例销毁瞬间的内存残渣过滤（原 ``_junk``）。

    残渣特征 = messageText 纯数字 / ×开头、双方名字空或纯数字
    （'已投降' 只有 3 个字，**长度不能当判据**！）。
    """
    tx = texts or {}
    mt = (tx.get('messageText') or '').strip()
    if mt.isdigit() or mt.startswith('×'):
        return True
    for k in ('player0Name', 'player1Name'):
        v = (tx.get(k) or '').strip()
        if not v or v.isdigit():
            return True
    return False


def make_record(texts, extra, result=None, label=None, result_src=None):
    """end_messages.jsonl 的一行。胜负判据 = **状态位唯一**（文案只作备注）；
    winbits 读不到 -> mem_result 为 null -> 之后手动补填。"""
    rec = {
        'captured_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'result': (result or '').lower() or None,
        'result_src': result_src,
        'label': (label or '').strip() or None,
        'texts': texts,
        'winbits': extra.get('winbits'),
        'result_note': (texts or {}).get('messageText'),
        'mem_result': None,
        'mem_result_src': None,
        'instance': extra.get('instance'),
        'pid': extra.get('pid'),
    }
    wb = extra.get('winbits')
    if wb:
        got = derive_result_from_winbits(*wb)
        if got:
            rec['mem_result'] = got.lower()
            rec['mem_result_src'] = 'winbit'
    return rec


def _typeinfo_class_name(proc, typeinfo) -> str:
    """TypeInfo -> 类名（名字 char* 在 +0x10）。"""
    if not typeinfo or typeinfo < 0x10000:
        return ''
    try:
        return proc.read_cstring(proc.read_ptr(typeinfo + 0x10), 64)
    except Exception:
        return ''


def all_fields(proc, mirror, typeinfo, max_depth=MAX_PARENT_DEPTH):
    """沿**父类链**收集字段 —— ``list_fields`` 只给本类声明的那几个。

    ⚠️ 碰 Unity 组件（Text / Image / Button…）一律要爬父链，否则会得到
    「字段表是空的」假象。``TypeInfo.parent`` 在 +0x58。
    """
    out, seen, cur = [], set(), typeinfo
    for _ in range(max_depth):
        if not cur or cur < 0x10000 or cur in seen:
            break
        seen.add(cur)
        fields = list_fields(proc, cur, mirror=mirror)
        if fields:
            out.append((_typeinfo_class_name(proc, cur), fields))
        try:
            cur = proc.read_ptr(cur + PARENT_OFFSET)
        except Exception:
            break
    return out


def _text_of_object(proc, mirror, obj, cache) -> _Optional[str]:
    """给一个 Unity 文字对象，沿父链找它的文字（优先 ``m_text``）。"""
    if not obj or obj < 0x10000:
        return None
    try:
        typeinfo = proc.read_ptr(obj)
    except Exception:
        return None
    if not typeinfo or typeinfo < 0x10000:
        return None
    if typeinfo not in cache:
        cache[typeinfo] = all_fields(proc, mirror, typeinfo)
    chain = cache[typeinfo]

    best = None
    for _cname, fields in chain:
        for f in fields:
            if f.offset > 0x800:
                continue
            low = f.name.lower()
            if low == 'm_text':
                rank = 0
            elif 'text' in low or 'string' in low or 'label' in low:
                rank = 1
            else:
                continue
            try:
                raw = proc.read_ptr(obj + f.offset)
            except Exception:
                continue
            s = _read_il2cpp_string(proc, raw)
            if s is None:
                continue
            if rank == 0:
                return s                      # `m_text` 直接命中
            if best is None:
                best = s
    return best


class EndMessageReader(object):
    """只读打开游戏进程，读结算画面上 ``DuelEndMessage`` 的那几个文字。

    **不碰** ``redline.py`` 里的禁区字段（连字段名都不出现在本文件里）。
    """

    def __init__(self, game_dir=None):
        self.game_dir = game_dir or default_game_dir()
        self._proc = None
        self._pid = None
        self._mirror = None
        self._ti = 0
        self._fields = {}
        self._chain_cache = {}
        self._owns_proc = True       # adopt 进来的进程归宿主管，close 不关它
        #: 🔴 2026-10-01 事故刀②：上局命中的实例地址 + 所在区域 —— 10s 扫描
        #: 预算能不能扫到目标区域全看当时内存布局（同一局 5 次整读全落空、
        #: 下一局首读就命中，就是它）。Boehm 堆复用高，隔局地址常还在：
        #: 旧地址一次指针读就能验，旧区域毫秒级扫完。
        self._last_instance = None
        self._last_region = None
        self._regions_used = None

    # --- 连接 / 断开 ---
    def connect(self) -> bool:
        """找进程 + 定位 ``DuelEndMessage`` 的字段表。成功返回 True。"""
        self.close()
        pids = find_pids_by_name('masterduel.exe')
        if not pids:
            return False
        meta = default_metadata_path(self.game_dir)
        proc = None
        try:
            header = MetadataHeader.from_file(meta)
            proc = SafeProcess(pids[0])
            mirrors = find_all_metadata_mirrors(proc, header)
            if not mirrors:
                raise RuntimeError('没找到 metadata 镜像')
            mirror = mirrors[0]
            table = read_string_table(meta, header)
            ti = 0
            for ns in NAMESPACES:
                tis = find_typeinfo(proc, mirror, table, ns, 'DuelEndMessage')
                if tis:
                    ti = tis[0]
                    break
            if not ti:
                raise RuntimeError('没定位到 DuelEndMessage')
            fields = {f.name: f for f in list_fields(proc, ti, mirror=mirror)}
            if 'messageText' not in fields:
                raise RuntimeError('字段表里没有 messageText')
        except Exception:
            if proc is not None:
                try:
                    proc.close()
                except Exception:
                    pass
            return False
        self._proc, self._pid, self._mirror, self._ti, self._fields = \
            proc, pids[0], mirror, ti, fields
        self._chain_cache = {}
        return True

    def adopt(self, proc, mirror, tbl) -> bool:
        """复用宿主（Runtime）已连好的进程/镜像，只补类型定位。

        🔴 2026-10-01 事故刀①：自带 connect() 要**全进程扫 metadata 镜像**
        （`find_all_metadata_mirrors` 无预算，1-4 分钟级），收局 90s 窗口整个
        耗在它上面 —— 10-01 22:40 那局「结果读不出来」就是这么丢的。
        Runtime attach 时镜像/字符串表早就绪，这里只做一次 find_typeinfo +
        字段表（毫秒级）。成功后进程归宿主管（close 不关它）。
        """
        ti = 0
        for ns in NAMESPACES:
            tis = find_typeinfo(proc, mirror, tbl, ns, 'DuelEndMessage')
            if tis:
                ti = tis[0]
                break
        if not ti:
            return False
        try:
            fields = {f.name: f for f in list_fields(proc, ti, mirror=mirror)}
        except Exception:
            return False
        if 'messageText' not in fields:
            return False
        self._proc = proc
        self._pid = getattr(proc, 'pid', None)
        self._mirror = mirror
        self._ti = ti
        self._fields = fields
        self._chain_cache = {}
        self._owns_proc = False
        self._last_instance = None    # 换连接后旧地址不可信
        self._last_region = None
        return True

    def forget(self):
        """宿主 detach 时调用：adopt 来的连接失效了，别再拿着旧句柄。"""
        self._proc = self._mirror = None
        self._pid = self._ti = 0
        self._fields = {}
        self._chain_cache = {}
        self._owns_proc = True
        self._last_instance = None
        self._last_region = None

    def ready(self) -> bool:
        return bool(self._proc is not None and self._fields)

    def close(self):
        if self._owns_proc and self._proc is not None:
            try:
                self._proc.close()
            except Exception:
                pass
        self.forget()

    @property
    def pid(self):
        return self._pid

    def class_name(self):
        return _typeinfo_class_name(self._proc, self._ti) if self._proc else ''

    # --- 读 ---
    def _candidates(self, limit=80) -> List[int]:
        """找出 ``DuelEndMessage`` 的实例地址（全进程扫 TypeInfo 指针）。

        ⚠️ 同类会同时命中好几个，真身靠「哪几个文字非空」来认（``read_texts``）。
        这是**扫描预算制**里允许的第三类扫描（收局时一次，结算画面无感时段）。
        """
        proc = self._proc
        # 刀①：只扫私有堆（C# 实例必在 GC 托管堆上）；目标没实现该方法时
        #（单测假进程）回退全扫。刀③：墙钟预算兜底，扫描开销不再跟内存涨。
        regions = None
        private = getattr(proc, 'private_regions', None)
        if private is not None:
            regions = private() or None
        # 刀②（2026-10-01）：上次命中的区域挪到最前 —— 同一块几 MB 毫秒级
        # 扫完，预算耗尽前一定能覆盖它。
        if regions and self._last_region is not None:
            regions = ([self._last_region]
                       + [r for r in regions
                          if r.base != self._last_region.base])
        self._regions_used = regions
        deadline = time.monotonic() + SCAN_TIME_BUDGET_SEC
        # 上次命中的实例地址直接前置（验证只是两次指针读，零扫描成本）
        hits = []
        if self._last_instance:
            hits.append(self._last_instance)
        hits.extend(proc.scan(struct.pack('<Q', self._ti), regions=regions,
                              deadline=deadline))
        strict, loose = [], []
        seen = set()
        for hit in hits:
            if hit in seen:
                continue
            seen.add(hit)
            if hit in (self._ti + f.offset for f in self._fields.values()):
                continue
            try:
                if proc.read_ptr(hit) != self._ti:
                    continue
            except Exception:
                continue
            loose.append(hit)
            try:
                # 实测有效的启发式：+8（monitor 指针）小得离谱的才是活对象
                if proc.read_ptr(hit + 8) < 0x10000:
                    strict.append(hit)
            except Exception:
                pass
        return (strict or loose)[:limit]

    def read_texts(self):
        """读一次结算画面。

        ⚠️ **选实例时优先「胜负位可读」的那一个** —— 现场实测（2026-09-15）：
        同一个 DuelEndMessage 实例，图标原生指针 8 秒后就被清成 `0x0`
        （结算面板的 GameObject 被回收），而**文字残留更久**（名字约 2 分钟才清）。
        只按「文字多」挑，就可能挑中一条**图标已死**的旧实例 → `winbits=None`
        （那几局 `result` 因此空着，悬浮窗显示 `—`）。

        :return: ``(texts, extra)``；一个文字都没读到就是空 dict
                 （= 不在结算画面 / 实例已销毁）。
        """
        if self._proc is None:
            return {}, {}
        proc = self._proc
        f0 = self._fields.get('player0WinIcon')
        f1 = self._fields.get('player1WinIcon')
        best, best_extra, best_score = {}, {}, 0
        for addr in self._candidates():
            texts = {}
            for name in TEXT_FIELDS:
                fe = self._fields.get(name)
                if fe is None:
                    continue
                try:
                    ptr = proc.read_ptr(addr + fe.offset)
                except Exception:
                    continue
                s = _text_of_object(proc, self._mirror, ptr, self._chain_cache)
                if s:
                    texts[name] = s
            wb = None
            if f0 is not None and f1 is not None:
                try:
                    wb = read_winbits(proc, addr, f0.offset, f1.offset)
                except Exception:
                    wb = None
            # 胜负位可读 = 这条实例**还活着**（+3 分，压过「文字多一条」）
            score = (len(texts) + (2 if texts.get('messageText') else 0)
                     + (3 if wb is not None else 0))
            if score > best_score:
                best, best_score = texts, score
                best_extra = {'instance': '0x%X' % addr, 'pid': self._pid}
                if wb is not None:
                    best_extra['winbits'] = wb
                self._last_instance = addr
                self._last_region = self._region_of(addr)
            if texts.get('messageText') and len(texts) >= 3 and wb is not None:
                break                     # 文字齐 + 胜负位可读 = 真身，不用再找
        return best, best_extra

    def _region_of(self, addr):
        """地址 -> 它所在的私有堆区域（找不到给 None）。"""
        for r in (self._regions_used or []):
            if r.base <= addr < r.end:
                return r
        return None

    def read_winbits(self, instance):
        """只读某个实例的胜负位 -> ``(我方位, 对方位)``；读不到给 None。

        ⚠️ **胜负位只在结算画面那几秒内可读** —— 现场实测（2026-09-15 23:22:55 ->
        23:23:03）：同一个实例的图标原生指针 8 秒后就变成 `0x0`（面板 GameObject
        被回收），而文字能留约 2 分钟。所以要**在窗口内尽早读、且优先选图标可读
        的那个实例**（见 :meth:`read_texts` 的评分）；读不到就给 None，别猜。"""
        if self._proc is None or not instance:
            return None
        f0 = self._fields.get('player0WinIcon')
        f1 = self._fields.get('player1WinIcon')
        if f0 is None or f1 is None:
            return None
        try:
            addr = int(instance, 16) if isinstance(instance, str) else int(instance)
        except Exception:
            return None
        try:
            return read_winbits(self._proc, addr, f0.offset, f1.offset)
        except Exception:
            return None
