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
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 段位图标目录（7 张 300×300 webp，带透明通道，共约 138 KB）
#:
#: ⚠️ **只到大段位，不做小段位**（用户 2026-09-14 口径）。
#: 实测两家的图确实不一样：
#:   * **untapped 的 `Master.webp`**（我们用的）—— 徽章上**没有序号**，就是大段位
#:   * masterduelmeta 的 `master-1.webp` —— 徽章底部**画着罗马数字 `I`**，那是小段位
#: 而 masterduelmeta 那边也没有完整的一套（`platinum-1..6` 全 403，`logos/` 目录里
#: 只探到 `ratingfinalicon` / `rateIcon` / `master-1` 三个），所以**不换源**。
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


def display_rate(rank, rate):
    """显示层的小段位换算（2026-10-03 用户：「**以游戏内为准**」）。

    * **大师段（7）游戏内显示 = 内部值 + 1** —— 实测两样本（09-28）：
      rate 1 游戏显示 Ⅱ、rate 2 显示 Ⅲ；白金按内部值直显（5-2 = 游戏内
      白金 II，无差），所以这是**大师段专属偏移**。
    * 上限封在 RATE_MAX（大师保护档 rate 5 → V；与 rate 4 同显是已知代价，
      档位没样本前先这样）。
    """
    try:
        r = int(rate)
    except (TypeError, ValueError):
        return rate
    if int(rank or 0) == 7 and RATE_MIN <= r <= RATE_MAX:
        return min(r + 1, RATE_MAX)
    return r


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
    return '%s %d' % (name, display_rate(rank, r))


#: 小段位的**罗马数字**（`my_rate_before` 1..5 -> I..V）
ROMAN = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V'}


def numeral_text(rate: Optional[int], rank: Optional[int] = None) -> str:
    """小段位 -> 罗马数字**文字**（`3` -> `'III'`）；认不出返回 `''`。

    ⚠️ 大师段走 :func:`display_rate` 的 +1 偏移（2026-10-03 以游戏内为准）——
    徽章上的小档字也要跟游戏一致。

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
    return ROMAN.get(display_rate(rank, n), '')


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
