# -*- coding: utf-8 -*-
"""段位：编号 ↔ 名称 ↔ 图标（M7 · **纯逻辑，无 GUI 依赖**）。

## 编号映射从哪里来（**不是猜的**）

来自 untapped 前端自己的代码 —— 它的 JS chunk 里有这么一张表：

```js
{1:"Rookie", 2:"Bronze", 3:"Silver", 4:"Gold",
 5:"Platinum", 6:"Diamond", 7:"Master"}
```

这与我们建表注释里的 `5=Platinum / 6=Diamond / 7=Master` **完全吻合**，
所以 `my_rank_before` / `opp_rank_before` 就是这套 1 起的编号。

## 段内小段的方向（**这条是推断**）

`my_rate_before` 取值实测只有 `{2,3,4,5}`。库里 3 次真实升段是
`5-5 → 5-4`、`5-4 → 5-3`、`5-3 → 5-2` → 所以**数字越小段位越高**（5 是该段最低，1 最高）。
这条是**从真实升段样本反推的**，不是从第三方代码抄的；样本只有 3 条，记在心里。

> 显示口径：跟参考图一致用**英文名** `Platinum 2`（untapped 的中文站也是显示英文名）。

## 段内小段的**显示** = 存储值（2026-10-15 更正）

库里 `my_rate_before=2` 那局，游戏里就是 `Master 2` —— **没有偏移**，
`numeral_text` 直接按 1..5 给 I..V。

⚠️ **曾经有过一个「大师段显示 = 内部值 + 1」的偏移，已删**（2026-10-03 加、
2026-10-15 用户更正）。用户给的三局真值（库内 rate ↔ 游戏内）逐局吻合：
`遊城二十代` 2→1 = Master 2→1、`Van Anh` 3→2 = Master 3→2、
`AsuNa` 4→3 = Master 4→3。当初那条「样本」是 09-28 徽章读数**看错了一档**
（同一条 09-28 的记录里，`core/rank_state.py` 一直把 rate 1 叫「大师 I（段内顶）」，
本身就说明 rate 1 = 游戏内 Master I）。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 段位图标目录（7 张 300×300 webp，带透明通道）。
#:
#: ⚠️ **来源 = 游戏本体解包原版**（2026-09-15 起，见 `docs/素材解包说明.md`）：
#:   `assets/.../prefabs/profile/rankicons/large/img_rankicon_01..07_l`（转 webp q95）。
#:   此前一版取自 untapped 静态资源，**已全部替换** —— 别再照旧注释改回去。
#:
#: ⚠️ **只到大段位，不做小段位**（用户 2026-09-14 口径）—— 小段位罗马数字用**文字**画
#: （`numeral_text()`），不引位图（`img_rankicon_tier1..5` 解出来了但缩放会糊，没用）。
#:
#: 但**文字标签里的小段位照常显示**（`Platinum 2` / 升段时 `Platinum 3 → Platinum 2`）——
#: 用户说的是"小段位**图标**没必要"，不是不要小段位数字；参考图里也是带数字的。
ICON_DIR = os.path.join(PROJ, 'data', 'rank_icons')

#: 编号 → 英文名。**来源见模块头，别再怀疑**。
RANK_NAMES = {
    1: 'Rookie',
    2: 'Bronze',
    3: 'Silver',
    4: 'Gold',
    5: 'Platinum',
    6: 'Diamond',
    7: 'Master',
}

#: 从低到高（界面上要按这个顺序排 icon 时用）
RANK_ORDER = tuple(RANK_NAMES[k] for k in sorted(RANK_NAMES))

#: 段内小段的取值范围（5 最低 → 1 最高）
RATE_MIN, RATE_MAX = 1, 5


def rank_name(rank: Optional[int]) -> Optional[str]:
    """`5` -> `'Platinum'`；认不出返回 None（**不猜**）。"""
    try:
        return RANK_NAMES.get(int(rank))
    except (TypeError, ValueError):
        return None


def rank_label(rank: Optional[int], rate: Optional[int] = None) -> str:
    """`(5, 2)` -> `'Platinum 2'`；`(5, None)` -> `'Platinum'`；认不出 -> `''`。

    认不出时返回**空串而不是 `'?'`** —— 界面上那一格就该是空的，不要编。
    """
    name = rank_name(rank)
    if not name:
        return ''
    try:
        r = int(rate)
    except (TypeError, ValueError):
        return name
    if not (RATE_MIN <= r <= RATE_MAX):
        return name
    return '%s %d' % (name, r)


#: 小段位的**罗马数字**（`my_rate_before` 1..5 -> I..V）
ROMAN = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V'}


def numeral_text(rate: Optional[int]) -> str:
    """小段位 -> 罗马数字**文字**（`3` -> `'III'`）；认不出返回 `''`。

    ⚠️ **用文字，不要用图**（用户 2026-09-15 定）。
    游戏里是拿 `img_rankicon_tier1..5` 那几张 48×32 的蒙版叠在徽章上的，
    但那几张**分辨率太低**（我们只要 11px 高），放大就糊；自己画 SVG 又没必要 ——
    罗马数字就 5 个字符，直接用字体画最清晰、任何缩放下都锐利。
    曾经落过 `data/rank_icons/num_1..5.webp`，已删。
    """
    try:
        n = int(rate)
    except (TypeError, ValueError):
        return ''
    return ROMAN.get(n, '')


def icon_path(rank: Optional[int]) -> Optional[str]:
    """段位图标路径（**相对项目根**，正斜杠）；没有就 None。

    认不出编号时**返回 None**（不要回落某个图标 —— 显示错段位比不显示更糟）。
    """
    name = rank_name(rank)
    if not name:
        return None
    p = os.path.join(ICON_DIR, name + '.webp')
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return os.path.relpath(p, PROJ).replace('\\', '/')
    return None


def _key(rank: Optional[int], rate: Optional[int]) -> Optional[Tuple[int, int]]:
    """把 (大段, 小段) 归一成**可比较的键** —— 大段大者高；同段则**小段小者高**。"""
    name = rank_name(rank)
    if not name:
        return None
    r = 0
    try:
        r = int(rate)
    except (TypeError, ValueError):
        r = 0
    if not (RATE_MIN <= r <= RATE_MAX):
        r = 0
    # 小段越大越低 → 取负号，这样"键大 = 段位高"
    return (int(rank), -r)


def best(pairs) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """一堆 ``(rank, rate)`` 里**最高**的那个 —— 总览「最高段位」用（用户 2026-09-18）。

    「最高」沿用 :func:`_key` 的判据（**大段大者高；同段则小段小者高**）；
    ⚠️ `_key` 的形状是 `(rank, -rate)`，**键大 = 段位高**，所以这里取 **max** 不是 min。
    之前只有私有 `_key` 能比，界面层不该伸手拿私有，故补这个公开口。
    一个都认不出返回 None（**不猜**）。
    """
    best_pair = None
    best_key = None
    for item in pairs or ():
        if not item:
            continue
        seq = list(item) + [None, None]
        rank, rate = seq[0], seq[1]
        k = _key(rank, rate)
        if k is None:
            continue
        if best_key is None or k > best_key:
            best_key, best_pair = k, (rank, rate)
    return best_pair


def compare(before: Tuple[Optional[int], Optional[int]],
            after: Tuple[Optional[int], Optional[int]]) -> Optional[str]:
    """比较两次段位 -> ``'up'`` / ``'down'`` / ``'same'`` / ``None``（比不了）。

    用于「升段那局显示 `A → B`」：只有返回 ``'up'``/``'down'`` 才画箭头。
    """
    if not before or not after:            # 有一边没有段位 = 比不了（不猜）
        return None
    a, b = _key(*before), _key(*after)
    if a is None or b is None:
        return None
    if b > a:
        return 'up'
    if b < a:
        return 'down'
    return 'same'
