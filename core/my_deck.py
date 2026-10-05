# -*- coding: utf-8 -*-
"""「当前我方卡组」状态（本机数据：``data/my_deck.json``）。

为什么有它（用户 2026-09-16 口径）
----------------------------------
卡组编辑界面**读一次就够了**：之后不再进编辑界面就**一直沿用**这个卡组名；
进了编辑界面**且换牌了**（卡表指纹变）才重新识别一次。所以需要一个「当前卡组」的落脚点。

⚠️ 这**不是**战绩数据：只是「本机 UI 用的当前卡组」状态（`data/` 下的本机文件，不入库）。
   真源仍是 `matches` 表 —— 补进库的行由 import 侧写 `my_deck_name` / `my_deck_name_src`。
"""

import hashlib
import json
import os
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 状态文件（本机数据；`MD_MY_DECK_STATE` 可覆盖，单测用临时文件）
#: 状态文件路径。⚠️ **读的时候**再解析环境变量（别在 import 期定格）——
#: 测试要把 `MD_MY_DECK_STATE` 指到临时文件，而测试模块 import 顺序不定；
#: import 期定格的话，先 import 的测试会读到用户真实状态（2026-09-22 实测）。
DEFAULT_STATE_PATH = os.path.join(PROJ, 'data', 'my_deck.json')


def _state_path() -> str:
    return os.environ.get('MD_MY_DECK_STATE') or DEFAULT_STATE_PATH


def fingerprint(ids):
    """卡表指纹：**排序后**的卡 id 拼接 -> md5 前 16 位。

    排序 = 与「出牌顺序/份数」无关 —— 只是换了个顺序不该算「换牌」。
    """
    norm = sorted({int(i) for i in (ids or []) if i})   # set：份数变化不算换牌
    return hashlib.md5(','.join(str(i) for i in norm).encode('ascii')).hexdigest()[:16]


def load(path=None):
    """读状态；文件不在/坏了 -> ``{}``（绝不抛）。"""
    p = path or _state_path()
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                            # noqa: BLE001
        return {}


def save(name, ids=None, src='m5-guess', path=None, extra=None):
    """写状态（覆盖）。返回写进去的 dict。"""
    p = path or _state_path()
    rec = {'name': name, 'src': src,
           'at': time.strftime('%Y-%m-%d %H:%M:%S')}
    if ids is not None:
        rec['ids_fingerprint'] = fingerprint(ids)
        rec['card_count'] = len(list(ids))
    if extra:
        rec.update(extra)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    except Exception:                                            # noqa: BLE001
        pass
    return rec


def state_fingerprint(path=None) -> str:
    """状态文件的「身份」：`名字|指纹`（没有给 ''）—— 给便宜探针判「变没变」。

    ⚠️ 与 `ui.deck_banner.deck_state_fingerprint`（纯函数版）**同语义**：
    采集器识别出新卡组 → `save()` 覆盖状态文件 → 指纹变 → 探针 key 变 →
    悬浮窗拉全量、确认条刷新（2026-09-22 用户实测「换了卡组提示条不动」的修复）。
    """
    d = load(path) or {}
    if not d.get('name'):
        return ''
    base = str(d.get('name'))
    fp = str(d.get('ids_fingerprint') or '')
    return '%s|%s' % (base, fp) if fp else base


def current_name(path=None):
    """当前卡组名（识别出的原型名，如 ``Blue-Eyes``）；没有给 None。"""
    return (load(path) or {}).get('name') or None


def is_first_use(path=None):
    """还没取过卡组信息（= 悬浮窗该提示「进卡组编辑界面」）。"""
    return not current_name(path)
