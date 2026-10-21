# -*- coding: utf-8 -*-
"""卡组**中文名预填**（给 `ui.app.NewDecksDialog` 用）。

## 为什么要有这个

「获取新卡组名」弹窗里每一行都是空的「中文名（可留空）」，用户要一个个手打。
但这些名字大多能从卡表里**推出来** —— 用户 2026-10-21 的口径：
「中文名能不能预填」。

## 三层预填（越靠前越可信）

| 层 | 判据 | 例 |
|---|---|---|
| **1 覆盖表** | `deck_name_cn.csv` 里已经有这个站内英文名 | `Traptrix` → 虫惑魔 |
| **2 前缀法** | 卡名里覆盖率 ≥60% 的最长中文前缀 | `Spright` → 卫星闪灵 |
| **3 留空** | 推不出来 / 覆盖率不够 / 样本不足 | `Charmer` / `Insect` |

⚠️ **宁可留空也不猜歪**：`Charmer` 的卡叫「地灵使 奥丝」「水灵使 艾莉娅」，
共同前缀只有「灵使」两个字、覆盖率也不够 —— 硬猜会给出「灵使」当卡组名，
而用户口径里 `Magistus Charmer` 才叫「灵使」（`Charmer` 是另一个条目）。
同理 `Insect`（卡叫「巨大蚂蚁」「昆虫女王」，前缀根本没有共同点）。

## 为什么不用「后缀 / 中间词」

试过：`Traptrix` 的卡叫「阿特拉之虫惑魔」，取「之」后面的「虫惑魔」能命中，
但同一套规则套到 `Labrynth` 会推出「白银之」（卡叫「白银之迷宫城」）、
`Ancient Gear` 推出「古代的」。**后缀法在这些数据上不可靠**，只用前缀。

## 用法

    from core.deck_name_guess import prefill_cn
    guess, why = prefill_cn('Spright')
    # -> ('卫星闪灵', '前缀法 100% (11 张卡)')
    guess, why = prefill_cn('Charmer')
    # -> (None, '样本不足（命中 2 张卡）')
"""
from __future__ import annotations

import io
import os
from collections import Counter

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 卡名里要切掉的尾巴（分隔符 / 助词）。
#: ⚠️ 中点有**两个码位**，都必须收：
#:   * `·` U+00B7 MIDDLE DOT（ygocdb 里 `卫星闪灵·蓝色喷流灵` 用的就是它）
#:   * `・` U+30FB KATAKANA MIDDLE DOT（日文名里常见）
#: 只收后者的话 `卫星闪灵·...` 切不开，前缀法会推出带中点的垃圾结果
#: （2026-10-21 实测踩到）。
STRIP = ('　 ·・:：－-‐−～~＜＞<>'
         '「」『』【】♪☆★の之')

#: 命中卡少于这么多张就不敢下结论
MIN_CARDS = 3
#: 候选前缀至少要这么多张卡撑
MIN_HITS = 3
#: 前缀至少要覆盖这么多比例的卡
MIN_COV = 0.6
#: 短于这个汉字数的结论**只有在覆盖率 100% 时才采信**
#: （2 个汉字的「灵使」「魔妖」这类太容易撞名）
MIN_LEN_ALWAYS_OK = 3

_CARDS_CACHE = {}
_CSV_CACHE = {}


#: 三语卡名表的卡表段键名（`data/card_names.json`）
_NAMES_KEY = 'names'


def _cn_from_names_json(path=None):
    """从 `data/card_names.json` 取 ``[(en_name, cn_name)]``。

    ★ 2026-10-21：这是**发布仓也带**的数据源（进版本库），而
    `ygocdb.com.cards/cards.json` 是 gitignore 的、**只在开发机有** ——
    预填若只读后者，发布版里会静默失效（实测：干净仓库上推 `Spright`
    得到「样本不足」，而这副牌明明有卡）。所以优先用这个，ygocdb 兜底。

    ⚠️ 结构是 ``{"names": {"<cid>": [cn, en, jp]}}``（5 个顶层键之一，见
    AGENTS 铁律 7）—— **卡表在 `names` 里**，直接 `["20500"]` 只会拿到顶层键。
    """
    import json
    from core import paths
    path = path or paths.resolve('card_names.json')
    if not path or not os.path.exists(path):
        return []
    try:
        with io.open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    names = (data or {}).get(_NAMES_KEY) if isinstance(data, dict) else None
    if not isinstance(names, dict):
        return []
    out = []
    for val in names.values():
        if not isinstance(val, (list, tuple)) or len(val) < 2:
            continue
        cn = (val[0] or '').strip()
        en = (val[1] or '').strip()
        if cn and en:
            out.append((en, cn))
    return out


def _cn_cards():
    """``[(en_name, cn_name)]`` —— 优先 `card_names.json`，ygocdb 兜底。"""
    if 'v' in _CARDS_CACHE:
        return _CARDS_CACHE['v']
    out = _cn_from_names_json()
    if not out:                                    # 发布仓没有 card_names.json 时
        try:
            from core.card_names import ygocdb
            db = ygocdb() or {}
        except Exception:                                # noqa: BLE001
            db = {}
        for v in db.values():
            en = ((v or {}).get('en_name') or '').strip()
            cn = ((v or {}).get('cn_name') or '').strip()
            if en and cn:
                out.append((en, cn))
    _CARDS_CACHE['v'] = out
    return out


def clear_cache() -> None:
    """清缓存（卡表刷新后 / 测试用）。"""
    _CARDS_CACHE.clear()
    _CSV_CACHE.clear()


def _existing_map():
    """覆盖表里已有的 ``{站内英文名: 中文名}``（进程内缓存）。"""
    if 'v' in _CSV_CACHE:
        return _CSV_CACHE['v']
    out = {}
    try:
        from core import deck_csv
        header, rows = deck_csv.read(deck_csv.CN_CSV)
        idx = deck_csv.index_map(header)
        for r in rows:
            en = deck_csv.get(r, idx, '站内英文名').strip()
            cn = deck_csv.get(r, idx, '中文名').strip()
            if en and cn:
                out[en] = cn
    except Exception:                                    # noqa: BLE001
        pass
    _CSV_CACHE['v'] = out
    return out


def _cut(n: str) -> str:
    """切掉卡名里的分隔符尾巴 —— ``卫星闪灵·蓝色喷流灵`` → ``卫星闪灵``。"""
    for sep in STRIP:
        if sep in n[1:]:
            n = n[:n.index(sep, 1)]
    return n.strip()


def _prefix_candidates(cn: str):
    """一个中文卡名 -> 它的所有合法前缀（长度 ≥2、不以分隔符收尾）。"""
    n = _cut(cn)
    out = []
    for length in range(2, min(10, len(n)) + 1):
        p = n[:length]
        if p and p[-1] not in STRIP:
            out.append(p)
    return out


def guess_cn(deck_en: str):
    """前缀法推定中文名 -> ``(中文名 | None, 覆盖率, 命中卡数)``。

    只认「**以站内英文名开头**」的卡（`Spright` → `Spright Blue` 这类）。
    混轴卡组（`Branded Elfnote`）一张都匹配不到，直接返回 ``None``。
    """
    low = (deck_en or '').lower().strip()
    if not low:
        return None, 0.0, 0
    hit = [cn for en, cn in _cn_cards() if en.lower().startswith(low)]
    if len(hit) < MIN_CARDS:
        return None, 0.0, len(hit)
    cnt = Counter()
    for cn in hit:
        for p in _prefix_candidates(cn):
            cnt[p] += 1
    total = len(hit)
    pick = None
    for p, c in cnt.items():
        cov = c / float(total)
        if c < MIN_HITS or cov < MIN_COV:
            continue
        #: 长名字优先（`卫星闪灵` 比 `卫星` 好），但差得不多时看覆盖率
        score = (cov, min(len(p), 8))
        if pick is None or score > pick[1]:
            pick = (p, score, cov)
    if pick is None:
        return None, 0.0, total
    return pick[0], pick[2], total


def prefill_cn(deck_en: str):
    """给新卡组预填中文名 -> ``(中文名 | None, 依据说明)``。

    依据说明是**给人看的**（弹窗里做 tooltip / 日志），拿不到就说清为什么。
    """
    name = (deck_en or '').strip()
    if not name:
        return None, '没有名字'
    existing = _existing_map()
    if name in existing:
        return existing[name], '覆盖表已有'
    guess, cov, total = guess_cn(name)
    if not guess:
        if total < MIN_CARDS:
            return None, '样本不足（命中 %d 张卡）' % total
        return None, '推不出（%d 张卡里没有 ≥%.0f%% 的共同前缀）' % (total, MIN_COV * 100)
    #: 短名字（2 个汉字）只在**全覆盖**时才敢用 —— 「灵使」「魔妖」这类太容易撞
    if len(guess) < MIN_LEN_ALWAYS_OK and cov < 0.999:
        return None, ('推定「%s」但只有 %.0f%% 覆盖、且只有 %d 个字，不敢用'
                      % (guess, cov * 100, len(guess)))
    return guess, '前缀法 %.0f%%（%d 张卡）' % (cov * 100, total)


def prefill_many(names):
    """批量预填 -> ``{站内英文名: (中文名 | None, 依据)}``。"""
    return {n: prefill_cn(n) for n in (names or [])}
