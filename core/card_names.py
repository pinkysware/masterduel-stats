# -*- coding: utf-8 -*-
"""卡名三语表（中文 / 英文 / 日文）—— **只用于显示，匹配一律走卡 id**。

数据是 `data/card_names.json`，由 `tools/build_card_names.py` 从
`ygocdb.com.cards/cards.json`（+ `data/card_data.json` 兜底）生成。
格式：``{"v": 1, "langs": ["cn", "en", "jp"], "counts": {...},
"names": {"20500": ["多多迷宝・软绵雀精", "Mulcharmy Fuwalos",
"マルチャミー・フワロス"], ...}}``

用法::

    from core.card_names import LANGS, load, display, is_known

    table = load()                     # {卡id: (中文, 英文, 日文)}
    display(20500, 'jp', table)        # 'マルチャミー・フワロス'
    display(20500, 'en', table)        # 'Mulcharmy Fuwalos'
    is_known(20500, table)             # True；查不到 = 卡表过期（M4 的 unknown 判据）

**缺哪个语言就顺延**：请求的语言 → 中文 → 英文 → 日文；全空返回 ``None``，
交给调用方兜底（例如回到 `matches.opp_revealed` 里那份中文快照）。

⚠️ 语言**不影响任何匹配逻辑** —— 对局、卡组、统计全靠 id 关联（见 AGENTS.md
铁律 7 的「多语言」段）。
"""

import json
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 语言顺序（也是 `data/card_names.json` 里数组的顺序）
LANGS = ('cn', 'en', 'jp')

DEFAULT_PATH = os.path.join(PROJ, 'data', 'card_names.json')

_CACHE = {}


def load(path=None):
    """读卡名表，返回 ``{卡id: (中文, 英文, 日文)}``；文件不在就返回空表。

    按路径缓存（同一个进程里反复调用不会重复读盘）。
    """
    path = path or DEFAULT_PATH
    if path in _CACHE:
        return _CACHE[path]
    table = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        raw = data.get('names') if isinstance(data, dict) and 'names' in data \
            else data
        for key, val in (raw or {}).items():
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(val, str):                 # 容错：老的「只有中文」形状
                val = [val, '', '']
            val = list(val) + [''] * (len(LANGS) - len(val))
            table[cid] = tuple((v or '').strip() for v in val[:len(LANGS)])
    _CACHE[path] = table
    return table


def load_fresh(path=None):
    """读盘并**刷新缓存**（常驻进程用）。

    `load()` 是纯缓存：常驻采集器里对局解码必须看到**最新**的卡表
    （用户刚跑完 `--update` 就该生效，别等重启进程），所以这里先失效再读。
    顺带把缓存换新，之后 `display()` / `to_row()` 也就一致了。
    """
    _CACHE.pop(path or DEFAULT_PATH, None)
    return load(path)


def clear_cache():
    """清掉缓存（测试 / 重新生成文件后用）。"""
    _CACHE.clear()


def display_all(card_id, table=None):
    """返回三元组；没这张卡返回 ``('', '', '')``。"""
    table = load() if table is None else table
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        return ('', '', '')
    return table.get(cid, ('', '', ''))


def display(card_id, lang='cn', table=None):
    """取指定语言的卡名；缺就顺延（请求的 → 中文 → 英文 → 日文），全空返回 None。"""
    names = display_all(card_id, table)
    if lang not in LANGS:
        lang = 'cn'
    first = LANGS.index(lang)
    order = [first] + [i for i in range(len(LANGS)) if i != first]
    for i in order:
        if names[i]:
            return names[i]
    return None


def is_known(card_id, table=None):
    """这张卡 id 在表里吗（``int`` / ``str`` 都认）。

    **这是 M4 的 unknown 判据**：对局里解出来的卡 id 查不到名字，就意味着
    这张卡不在我们的卡表里（新卡 / 新异画，卡表过期），调用方据此提示重跑
    ``tools/build_card_names.py`` —— 而不是只静默显示一个 ``#id``。

    ⚠️ 表文件不存在（或传进来空表）时一律返回 ``False``：**宁可提示，不静默**。
    """
    table = load() if table is None else table
    try:
        return int(card_id) in table
    except (TypeError, ValueError):
        return False


def count(table=None):
    """表里有多少张卡。"""
    table = load() if table is None else table
    return len(table)


# ===================================================== 卡类型（主卡 / 额外）
# `data/card_names.json` **只有名字**，没有卡类型 —— 主卡组 / 额外卡组的分组要用
# ygocdb 原始卡表里每张卡的 `data.type`（YGO 类型位掩码，实测：灰流丽 4129、
# 闪刀姬-飒天 67108897）。所以这里单开一个**只读**入口（同 `load()` 一样按路径缓存，
# 13MB JSON 全进程只解析一次；`core/card_art` 也走它，别各自读一遍）。

#: ygocdb 原始卡表（项目根下的快照，gitignore）
YGOCDB_PATH = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')

#: **额外卡组**的类型位：融合 / 同调 / 超量 / 连接（YGO 惯例，位掩码集中在这里）
EXTRA_TYPE_MASK = 0x40 | 0x2000 | 0x100000 | 0x4000000

_YGO_CACHE = {}


def ygocdb(path=None) -> dict:
    """ygocdb 原始卡表 ``{游戏内卡id(str): {..., 'data': {'type': …}}}``。

    文件不在 / 坏了 → **空 dict**（调用方据此降级，不抛）。
    """
    path = path or YGOCDB_PATH
    if path in _YGO_CACHE:
        return _YGO_CACHE[path]
    doc = {}
    try:
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                doc = raw
    except Exception:                                        # noqa: BLE001
        doc = {}
    _YGO_CACHE[path] = doc
    return doc


def clear_ygocdb_cache():
    """清掉 ygocdb 缓存（测试 / 上游刷新后用）。"""
    _YGO_CACHE.clear()


def card_type(card_id, path=None):
    """这张卡的 **YGO 类型位掩码**（ygocdb `data.type`）；查不到返回 ``None``。

    ``None`` = **卡表里没有这张卡**（卡表过期 / 不是游戏内 id）—— 与
    :func:`is_known` 同源判据，调用方据此提示重跑 `tools/build_card_names.py`。
    """
    try:
        key = str(int(card_id))
    except (TypeError, ValueError):
        return None
    card = ygocdb(path).get(key)
    if not isinstance(card, dict):
        return None
    data = card.get('data')
    raw = data.get('type') if isinstance(data, dict) else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_extra(card_id, path=None) -> bool:
    """这张卡进**额外卡组**吗（融合 / 同调 / 超量 / 连接，见 :data:`EXTRA_TYPE_MASK`）。

    ⚠️ 类型查不到时返回 ``False``（当主卡处理）—— 要区分「主卡」与「查不到」，
    自己看 :func:`card_type` 是不是 ``None``（卡组构成页就是这么计 `unknown` 的）。
    """
    t = card_type(card_id, path)
    return bool(t & EXTRA_TYPE_MASK) if t is not None else False
