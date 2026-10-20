# -*- coding: utf-8 -*-
"""对战模式：`game_mode` → 分类 / 名称 / 图标（M7 · 纯逻辑，无 GUI 依赖）。

**分类口径不在这里** —— 复用 `core.storage.mode_group()` 与 `MODE_GROUP_NAMES`
（`{3:'ranked', 19:'peak', 12:'dc'}`，其余算 `event`）。本模块只管**显示**：
名称和图标，别的别往这儿塞。

## 图标从哪来

**游戏本体解包原版**（2026-09-15 起，见 `docs/素材解包说明.md`）：

    assets/.../prefabs/profile/rankicons/large/img_rateicon_01_l  ->  peak.webp

⚠️ 文件名是 **`rate`icon**（不是 `rank`icon）—— 按 `img_rankicon` 过滤会漏掉它。
★ 2026-09-15（commit d3d0479）起**全部换成游戏本体解包原版**，第三方 CDN 那版已废。
`tools/fetch_mode_icons.py` 是更早的 CDN 下载器（**遗留工具**，没有弃用闸门）——
跑 `--force` 会把 `peak.webp` 覆盖回旧来源，**别再运行它**。

⚠️ **不要再找「天梯模式图标」了**（用户 2026-09-14 澄清）：天梯那一行的标识
就是**该局的段位图标**（`core.ranks.icon_path`），不用单独一个模式图标。
**一个「等级标识位」，天梯给段位图标、巅峰给巅峰图标** —— 见 :func:`badge`。
"""
from __future__ import annotations

import os
from typing import Optional

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 模式图标目录。文件名 = `storage.MODE_GROUP_*` 的值（ranked / peak / dc / event）
ICON_DIR = os.path.join(PROJ, 'data', 'mode_icons')

#: **需要图标**的模式分类（用户 2026-09-14 定）：
#: * `ranked`（天梯）**不需要** —— 那一行的标识用段位图标（见 :func:`badge`）
#: * `dc`（DC 杯）/ `event`（活动杯赛）**也不需要** —— 用户原话「dc杯和活动不需要图标」
#: 所以真正需要图的只有 `peak`（巅峰决斗）一个。
WANTED_ICONS = ('peak',)


def group(game_mode) -> Optional[str]:
    """`game_mode` → 分类（ranked/peak/dc/event）；判不出返回 None。

    **转发** `core.storage.mode_group`，口径只有一份。
    """
    from core.storage import mode_group as _mg
    try:
        return _mg(game_mode)
    except Exception:
        return None


def group_name(group_key: Optional[str]) -> str:
    """`'peak'` → `'巅峰决斗'`；认不出返回 `''`（**不编**）。"""
    if not group_key:
        return ''
    from core.storage import MODE_GROUP_NAMES
    return MODE_GROUP_NAMES.get(group_key, '')


def label(game_mode) -> str:
    """`19` → `'巅峰决斗'`；认不出 → `''`。"""
    return group_name(group(game_mode))


def icon_path(group_key: Optional[str]) -> Optional[str]:
    """分类 → 图标路径（**相对项目根**）；没有就 None。

    ⚠️ 返回 None 时界面**不要回落别的图标** —— 显示错模式比不显示更糟。
    """
    if not group_key:
        return None
    p = os.path.join(ICON_DIR, '%s.webp' % group_key)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return os.path.relpath(p, PROJ).replace('\\', '/')
    return None


def icon_for(game_mode) -> Optional[str]:
    """`game_mode` → 图标路径；该模式还没图就 None。"""
    return icon_path(group(game_mode))


def missing_icons() -> list:
    """还没图标的分类（给「缺什么素材」这类提示用）。

    ⚠️ 别再往里加 `ranked` / `dc` / `event` —— 用户 2026-09-14 明确
    「天梯用段位图标、DC 杯和活动不需要图标」。这个函数现在是"素材有没有丢"的自检。
    """
    return sorted(k for k in WANTED_ICONS if icon_path(k) is None)


def badge(game_mode, rank=None, rate=None):
    """某一行的**「等级标识」→ (图标路径 or None, 文字)**。

    这是界面那一格该调的东西 —— **一个位置，两种含义**：

    ============  ==================================  ==================
    模式          图标                                文字
    ============  ==================================  ==================
    天梯 ranked   `core.ranks.icon_path(rank)`        `Platinum 2`
    巅峰 peak     `data/mode_icons/peak.webp`         巅峰决斗
    DC / 活动     对应模式图标（有就显示）               DC 杯 / 活动杯赛
    ============  ==================================  ==================

    用户口径（2026-09-14）：**巅峰赛没有分段，它那个图标就相当于天梯里"最高段位"那一档的位置**
    —— 所以天梯给段位图标、巅峰给巅峰图标，不用两个图标叠着显示。
    """
    from core import ranks
    g = group(game_mode)
    if g == 'ranked':
        return ranks.icon_path(rank), ranks.rank_label(rank, rate)
    if g == 'peak':
        # 巅峰赛自带段位文字就用模式名（它不分小段）
        return icon_path('peak'), group_name('peak')
    return icon_path(g), group_name(g)
