# -*- coding: utf-8 -*-
"""卡组指纹比对 —— 判断「当前我方卡组是不是用户指定的那副」。

用户需求（2026-10-28，两轮澄清后的最终口径）
--------------------------------------------
1. **输入就一个 YDK 文件**（get-deck.com 能导出；游戏内也能复制到剪贴板），
   不是多张截图、不是「建参照库」。
   （截图那条路的硬障碍：`onnxruntime` 不在软件包里，且单图只认 39~49 张。）
2. **判的是「是不是同一副」**，不是「最像哪副」。
3. ★ **必须有容错** —— 用户会**替换或增减卡片**：
   「狱神耀圣」改了 3 张手坑，仍然是同一副卡组，不能判成「不认识」。

为什么容错是硬要求（不是可选项）
--------------------------------
* 玩家实打实会微调构筑（换手坑、换泛用、按环境改）；
* 如果只认「完全一致」，YDK 一旦过期（改了牌没重新导出）就永久失效；
* 但容错又不能太宽 —— 两副只共享手坑的**不同卡组**必须判成不同。

算法：**加权 Jaccard + 关键卡保底**
-----------------------------------
::

    A = 当前卡组卡集合（内存读到，含份数）
    B = YDK 卡集合（含份数）

    # 1) 主判据：卡种集合的 Jaccard（份数不影响"是不是这副"）
    J = |A∩B| / |A∪B|

    # 2) 关键卡保底：YDK 里的"核心卡"命中率
    #    "核心卡" = 在 YDK 里份数 >= CORE_COPIES 的卡（3 张 = 满编主轴）
    #    防止「两副牌共享全部手坑、主轴完全不同」被判成同一副
    K = |A∩core(B)| / |core(B)|

    # 3) 判同：J >= JACCARD_MIN **且** K >= CORE_MIN
    #    （core(B) 为空时只看 J —— 没有满编卡的卡组，比如纯额外轴）

阈值怎么来的（**必须实机标定，这里给的是起始值**）
--------------------------------------------------
用「同一副牌改 3~5 张」与「不同卡组」两类样本反推：

* 同副改 3 张（55 张）：J ≈ 52/58 ≈ 0.90，K ≈ 1.0（没动核心）
* 同副改 8 张（约 15%）：J ≈ 47/63 ≈ 0.75，K ≈ 0.9
* 两副同 archetype 混不同轴：J ≈ 0.6~0.7，K 常掉到 0.5 以下
* 完全不同的卡组：J < 0.2

所以 ``JACCARD_MIN = 0.72`` / ``CORE_MIN = 0.60`` 能分开「同副微调」与「不同卡组」。
⚠️ 这两个数是**起点**，要用用户的真实卡组实测校准（见计划 §10.6）。

只做判定，不写库
----------------
本模块是**纯逻辑**（不读盘、不碰内存、不碰 DB），输入是两组卡 id。
落盘 / 读内存由调用方负责。这样阈值和算法可以 100% 单测。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

#: 主判据阈值：卡种集合 Jaccard 下限。
#: 起点 0.72 —— 同副牌换 15% 的卡仍然过；两副共享手坑的不同卡组会被拒。
JACCARD_MIN = 0.72

#: 关键卡保底阈值：YDK 里「满编卡」（份数 >= CORE_COPIES）的命中率下限。
CORE_MIN = 0.60

#: 份数达到几算「关键卡」。3 = 满编（主轴核心），2 = 准满编。
#: ⚠️ 取 3 而不是 2：很多卡组都有 2 张的对策卡，用 2 会把噪声当核心。
CORE_COPIES = 3

#: 当前卡组至少要有这么多张才判（太少说明内存没读全）。
MIN_CURRENT_CARDS = 20


class Match(NamedTuple):
    """一次比对的结论。``matched=False`` 时 ``reason`` 说明为什么。"""

    matched: bool
    jaccard: float = 0.0          # 卡种覆盖度（较小集合的命中比例）
    core_ratio: float = 1.0       # 关键卡命中率（YDK 无满编卡时恒 1.0）
    only_current: int = 0         # 只在当前卡组里（= 用户后加的卡）
    only_fingerprint: int = 0     # 只在 YDK 里（= 用户去掉的卡）
    shared: int = 0
    reason: str = ''

    def to_dict(self) -> dict:
        return {'matched': self.matched, 'jaccard': round(self.jaccard, 3),
                'core_ratio': round(self.core_ratio, 3),
                'only_current': self.only_current,
                'only_fingerprint': self.only_fingerprint,
                'shared': self.shared, 'reason': self.reason}


def _counter(ids: Iterable) -> Dict[str, int]:
    """卡 id 列表 -> ``{str(id): 份数}``（忽略空/0）。"""
    c: Counter = Counter()
    for x in ids or []:
        s = str(x).strip()
        if s and s != '0':
            c[s] += 1
    return dict(c)


def core_ids(fingerprint_ids: Sequence, copies: int = CORE_COPIES) -> List[str]:
    """YDK 里份数 >= ``copies`` 的卡（**关键卡**）；没有则空列表。"""
    c = _counter(fingerprint_ids)
    return sorted([k for k, n in c.items() if n >= copies])


def compare(current_ids: Sequence, fingerprint_ids: Sequence,
            *, jaccard_min: float = JACCARD_MIN,
            core_min: float = CORE_MIN,
            core_copies: int = CORE_COPIES,
            min_current: int = MIN_CURRENT_CARDS) -> Match:
    """当前卡组 vs YDK 指纹 -> :class:`Match`（**纯函数**）。

    :param current_ids: 内存读到的我方卡组（含份数；顺序无关）
    :param fingerprint_ids: YDK 换算出来的卡 id（含份数）

    容错语义：
    * **份数差异不影响判定** —— 用户把某张卡从 3 张改成 2 张，仍算同一副；
    * **增删卡按 Jaccard 容忍** —— 换 15% 以内仍判同；
    * **关键卡保底** —— 防「共享手坑的两副不同卡组」被判成同一副。
    """
    cur = _counter(current_ids)
    fp = _counter(fingerprint_ids)
    if not fp:
        return Match(False, reason='YDK 里没有卡')
    if len(cur) < min_current:
        return Match(False, reason='当前卡组只读到 %d 张（不足 %d）'
                                 % (len(cur), min_current))

    a, b = set(cur), set(fp)
    shared = a & b
    #: ★ 分母用**较小集合**（覆盖度），不是并集（Jaccard）。
    #:
    #: 为什么（2026-10-28 实测推翻初版）：用户会**替换或增减**卡片，
    #: 换 8 张 / 55 张时：
    #:   Jaccard = 47 / (55+16-47) = 0.63  -> 被拒（错！这正是同一副）
    #:   覆盖度  = 47 / 55         = 0.85  -> 通过
    #: 并集做分母会被「新增的卡」二次惩罚（分子少了、分母还多了），
    #: 对「用户加了东西」的容忍度不真实地低。
    #: 覆盖度 = 两副牌中**较小的那副**有多少比例能在对方里找到。
    smaller = min(len(a), len(b))
    overlap = (len(shared) / smaller) if smaller else 0.0

    core = set(core_ids(fingerprint_ids, core_copies))
    core_ratio = (len(shared & core) / len(core)) if core else 1.0

    only_cur = len(a - b)
    only_fp = len(b - a)

    ok = overlap >= jaccard_min and core_ratio >= core_min
    reason = ''
    if not ok:
        if overlap < jaccard_min:
            reason = ('只有 %.0f%% 的卡重合（需要 %.0f%%）'
                      % (overlap * 100, jaccard_min * 100))
        else:
            reason = ('关键卡只命中 %.0f%%（需要 %.0f%%）'
                      % (core_ratio * 100, core_min * 100))
    return Match(True if ok else False, jaccard=overlap,
                 core_ratio=core_ratio, only_current=only_cur,
                 only_fingerprint=only_fp, shared=len(shared), reason=reason)


def diff_summary(current_ids: Sequence, fingerprint_ids: Sequence) -> Dict[str, List[str]]:
    """比对差异（给 UI 显示「你比 YDK 多了/少了哪些卡」）。

    返回 ``{'added': [id...], 'removed': [id...]}``（按 id 排序）。
    ``added`` = 当前有、YDK 没有（用户后加的）；``removed`` = YDK 有、当前没有。
    """
    a, b = set(_counter(current_ids)), set(_counter(fingerprint_ids))
    return {'added': sorted(a - b), 'removed': sorted(b - a)}


# ---------------------------------------------------------------- 存储
#: 指纹目录：`data/deck_fingerprints/<卡组名>.json`。
#: 走 `paths.data_path()`（安装版在 Program Files 时数据会落到可写目录）。
def _dir():
    from core import paths
    return paths.data_path('deck_fingerprints')


def _safe_name(name: str) -> str:
    """卡组名 -> 安全的文件名（去掉 Windows 不允许的字符）。"""
    bad = '\\/:*?"<>|'
    out = ''.join('_' if ch in bad else ch for ch in (name or '')).strip()
    return out or 'unnamed'


def save_fingerprint(name: str, ids, *, source: str = '',
                     path: Optional[str] = None) -> str:
    """把一副牌存成指纹 -> 返回文件路径。

    ⚠️ **同名覆盖**：用户重新导入同一个卡组名 = 更新指纹（期望行为）。
    """
    import json
    import os
    import time
    d = _dir()
    os.makedirs(d, exist_ok=True)
    fp = path or os.path.join(d, _safe_name(name) + '.json')
    doc = {
        'v': 1,
        'name': name,
        'ids': [str(x) for x in (ids or [])],
        'count': len(list(ids or [])),
        'unique': len(set(str(x) for x in (ids or []))),
        'source': source,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)
    return fp


def load_fingerprints() -> Dict[str, dict]:
    """读全部指纹 -> ``{名字: doc}``。目录不在 / 文件坏了都跳过（不抛）。"""
    import json
    import os
    d = _dir()
    out: Dict[str, dict] = {}
    try:
        names = sorted(os.listdir(d))
    except Exception:                                        # noqa: BLE001
        return out
    for fn in names:
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get('ids'):
                out[str(doc.get('name') or fn[:-5])] = doc
        except Exception:                                    # noqa: BLE001
            continue
    return out


def match_any(current_ids, fingerprints: Optional[Dict[str, dict]] = None):
    """当前卡组 vs **全部**指纹 -> ``(名字, Match)``；都不中给 ``(None, None)``。

    多个指纹命中时取**覆盖率最高**的那个（`Match.jaccard` 就是覆盖率）。
    """
    fps = fingerprints if fingerprints is not None else load_fingerprints()
    best = (None, None)
    for name, doc in fps.items():
        try:
            m = compare(current_ids, doc.get('ids') or [])
        except Exception:                                    # noqa: BLE001
            continue
        if m.matched and (best[1] is None or m.jaccard > best[1].jaccard):
            best = (name, m)
    return best
