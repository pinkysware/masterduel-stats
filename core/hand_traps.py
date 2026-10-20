# -*- coding: utf-8 -*-
"""手坑清单（**纯数据**，不含判据逻辑）。

用户 2026-10-16 圈定的 **12 张 / 16 个 id**（按功能分四组），
用于「本局前两回合双方是否使用了手坑」的统计（见
``docs/手坑识别计划.md``）。

⚠️ **4 张有异画**（`Ash` / `Belle` / `Veiler` / `朔夜时雨`）—— 异画 id 与标准 id
归到同一个 key（识别时按 :data:`TRAP_BY_ID` 反查，标准与异画算同一张）。
异画 id 由用户在卡组编辑页实测确认；`data/card_names.json` 里它们只有中文名
（`en_name` 为空）、`ygocdb` 卡表里**根本没有**这几条，所以
**不能用 ygocdb 判异画**，只能靠 `card_names.json`（单测锁着 `tests/test_hand_traps.py`）。

⚠️ **不要用对局事件里的 `alt_id` 判异画**（`core/memread/duel_log.py`）——
它在异画事件里常为 0（2026-10-19 按 310 份 `duel.json` 重算：Ash 异画 126 次
只有 16 次带值、Belle 164/17、Veiler 19/1、时雨 23/3），且全库有 2 组名对不上
（`12749 十二兽 鼠骑士` ← `12789 十二兽 猪弓箭`、`12200 魅影骑士团破手套` ←
`11385 暗反叛超量龙`），只能当交叉校验，不能当判据。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

#: 12 张手坑。key 是内部标识（稳定，别改——库里/测试都引用它）。
#: 每张: ``name_cn``（**必须**等于 ``data/card_names.json`` 里该 id 的中文名，
#: 单测按此锁定）、``name_en``（展示/分组用）、``group``、``ids``（标准 id + 异画 id）。
TRAPS: Dict[str, Dict[str, object]] = {
    # 抽牌系
    'maxx':     {'name_cn': '增殖的Z',            'name_en': 'Maxx "C"',
                 'group': 'draw',   'ids': (9455,)},
    'fuwalos':  {'name_cn': '多多迷宝・软绵雀精',  'name_en': 'Mulcharmy Fuwalos',
                 'group': 'draw',   'ids': (20500,)},
    'purulia':  {'name_cn': '多多迷宝・噗噜海月',  'name_en': 'Mulcharmy Purulia',
                 'group': 'draw',   'ids': (20206,)},
    # 封锁系
    'droll':    {'name_cn': '抽牌小丑与封锁鸟',    'name_en': 'Droll & Lock Bird',
                 'group': 'lock',   'ids': (9279,)},
    # 无效系
    'ash':      {'name_cn': '灰流丽',             'name_en': 'Ash Blossom & Joyous Spring',
                 'group': 'negate', 'ids': (12950, 3891)},
    'imperm':   {'name_cn': '无限泡影',           'name_en': 'Infinite Impermanence',
                 'group': 'negate', 'ids': (13631,)},
    'veiler':   {'name_cn': '效果遮蒙者',         'name_en': 'Effect Veiler',
                 'group': 'negate', 'ids': (8933, 21224)},
    'belle':    {'name_cn': '屋敷童子',           'name_en': 'Ghost Belle & Haunted Mansion',
                 'group': 'negate', 'ids': (13587, 3892)},
    'mourner':  {'name_cn': '朔夜时雨',           'name_en': 'Ghost Mourner & Moonlit Chill',
                 'group': 'negate', 'ids': (15022, 3416)},
    # 解场系
    'fydraulis': {'name_cn': '调和之天救龙',      'name_en': 'Fydraulis Harmonia',
                  'group': 'remove', 'ids': (22533,)},
    'ogre':     {'name_cn': '幽鬼兔',             'name_en': 'Ghost Ogre & Snow Rabbit',
                 'group': 'remove', 'ids': (11708,)},
    'nibiru':   {'name_cn': '原始生命态 尼比鲁',   'name_en': 'Nibiru, the Primal Being',
                 'group': 'remove', 'ids': (14741,)},
}

#: 四组：key -> 中文组名（顺序即展示顺序）
GROUP_NAMES_CN: Dict[str, str] = {
    'draw':   '抽牌系',
    'lock':   '封锁系',
    'negate': '无效系',
    'remove': '解场系',
}

#: 组 -> 该组的 key 元组（顺序 = `TRAPS` 里的定义顺序）
TRAP_GROUPS: Dict[str, Tuple[str, ...]] = {
    g: tuple(k for k, v in TRAPS.items() if v['group'] == g)
    for g in GROUP_NAMES_CN
}

#: id -> key 反查（识别时用；标准与异画都落到同一个 key）
TRAP_BY_ID: Dict[int, str] = {
    cid: k for k, v in TRAPS.items() for cid in v['ids']  # type: ignore[union-attr]
}

#: 全部 16 个 id（集合，判「是不是手坑」用）
ALL_TRAP_IDS = frozenset(TRAP_BY_ID)

#: 每张手坑的**标准 id**（展示/卡图用；异画与标准共用同一张卡图）
_STANDARD_ID: Dict[str, int] = {k: v['ids'][0] for k, v in TRAPS.items()}  # type: ignore[index]


def key_of(card_id) -> Optional[str]:
    """卡 id -> 手坑 key；不是手坑返回 ``None``（**异画与标准返回同一个 key**）。"""
    try:
        return TRAP_BY_ID.get(int(card_id))
    except (TypeError, ValueError):
        return None


def standard_id(key: str) -> Optional[int]:
    """手坑 key -> 标准 id（展示/取卡图用）；未知 key 返回 ``None``。"""
    return _STANDARD_ID.get(key)


def name_cn(key: str) -> Optional[str]:
    """手坑 key -> 中文名；未知 key 返回 ``None``。"""
    d = TRAPS.get(key)
    return d['name_cn'] if d else None  # type: ignore[return-value]


def group_of(key: str) -> Optional[str]:
    """手坑 key -> 组 key；未知 key 返回 ``None``。"""
    d = TRAPS.get(key)
    return d['group'] if d else None  # type: ignore[return-value]


def groups() -> Tuple[str, ...]:
    """组的顺序（展示用）。"""
    return tuple(GROUP_NAMES_CN)
