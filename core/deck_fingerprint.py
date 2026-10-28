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

    # 1) 主判据：**覆盖率**（不是 Jaccard！）
    overlap = |A∩B| / min(|A|, |B|)

    # 2) 关键卡保底：YDK 里的"核心卡"命中率
    #    "核心卡" = 在 YDK 里份数 >= CORE_COPIES 的卡（3 张 = 满编主轴）
    #    防止「两副牌共享全部手坑、主轴完全不同」被判成同一副
    K = |A∩core(B)| / |core(B)|        （core 为空时 K 恒 1.0 —— 保底不生效）

    # 3) 判同：overlap >= OVERLAP_MIN **且** K >= CORE_MIN

为什么分母是 min(|A|,|B|) 而不是并集（2026-10-28 实测 + 审查意见）
------------------------------------------------------------------
「用户会替换**或增减**卡片」是硬要求。用并集做分母时，用户**加**卡会被
二次惩罚（分子不涨、分母还涨）：

* 换 8 张（55 张 / 35 种的卡组）：覆盖率 = 27/35 = 0.77  ✅ 通过
  同一场景的 Jaccard = 27/(35+8) = 0.63  ❌ 会被误拒
* 加 5 张：覆盖率不变（分母取较小集合 = YDK 那份），✅ 通过
  Jaccard 会掉 —— 这就是不用并集的理由。

阈值表 —— **基于「55 张 / 35 种」的真实卡组结构**
--------------------------------------------------
⚠️ 覆盖率的分母是**卡种数**，不是张数。真实卡组 55 张只有 ~35 种
（40 主里常有十来张是 3 份），所以同一组数字在「55 张各 1 份」的合成数据上
会明显不同（后者 55 种，分母更大、覆盖率更低）。**引用这组数时必须写明基准**：

| 改动 | 覆盖率（35 种基准） | 判定 |
|---|---|---|
| 换 3 张 | 0.914 | ✅ |
| 换 5 张 | 0.857 | ✅ |
| **换 8 张** | **0.771** | ✅ |
| 换 12 张 | 0.657 | ❌ |
| 换 15 张 | 0.571 | ❌ |
| 换 20 张 | 0.429 | ❌ |
| 加 / 减 5 张 | 1.000 | ✅ |

即 ``OVERLAP_MIN = 0.72`` 的分界线在「**换掉 35 种里的 9~10 种**」附近。
⚠️ 这是**起点值**，要用用户的真实 YDK 实测校准（见计划 §10.6）。
⚠️ 与「两副共享手坑的不同卡组」要能分开 —— 那种情况覆盖率常到 0.5~0.7，
靠 `CORE_MIN` 拦（主轴满编卡一张不中）。

只做判定，不写库
----------------
本模块是**纯逻辑**（不读盘、不碰内存、不碰 DB），输入是两组卡 id。
落盘 / 读内存由调用方负责。这样阈值和算法可以 100% 单测。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

#: 主判据阈值：**覆盖率**下限。
#: ⚠️ 名字曾经叫 `JACCARD_MIN` —— **名不副实**（2026-10-28 审查意见 P2-3）：
#: 本模块算的是「较小集合的命中比例」（覆盖率），不是 Jaccard（并集做分母）。
#: 两者差别很大：55 张 / 35 种的卡组换 8 张，覆盖率 0.77 会通过，
#: Jaccard 只有 0.53 会被拒。用错名字会让下一个人把阈值当 Jaccard 调紧。
#: `JACCARD_MIN` 保留为**废弃别名**（防外部引用断掉），新代码用 `OVERLAP_MIN`。
OVERLAP_MIN = 0.72
JACCARD_MIN = OVERLAP_MIN       #: 废弃别名，等价于 :data:`OVERLAP_MIN`

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
    #: 卡种**覆盖率**（较小集合的命中比例）。
    #: ⚠️ 字段名曾是 ``jaccard`` —— 名不副实（2026-10-28 审查意见 P2-3），
    #: 已改 ``overlap``；``jaccard`` 保留为只读属性做兼容。
    overlap: float = 0.0
    core_ratio: float = 1.0       # 关键卡命中率（YDK 无满编卡时恒 1.0）
    only_current: int = 0         # 只在当前卡组里（= 用户后加的卡）
    only_fingerprint: int = 0     # 只在 YDK 里（= 用户去掉的卡）
    shared: int = 0
    reason: str = ''

    @property
    def jaccard(self) -> float:
        """**废弃别名**（旧名把覆盖率叫成了 Jaccard）。新代码用 :attr:`overlap`。"""
        return self.overlap

    def to_dict(self) -> dict:
        return {'matched': self.matched, 'overlap': round(self.overlap, 3),
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
            *, overlap_min: float = OVERLAP_MIN,
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

    #: ⚠️ **`core` 为空时保底不生效**（2026-10-28 审查意见 P2-4）：
    #: YDK 里一张「满编 3 份」的卡都没有时（限卡环境 / 主轴只放 1~2 张 /
    #: 纯额外轴），`core_ratio` 直接给 1.0 —— 等于只靠覆盖率判。
    #: 这是**已知取舍**，不是 bug：那种卡组没有「主轴」这个概念，
    #: 强行找一个保底判据反而会误拒。`Match.reason` 里也不会因此报错。
    #: 要收紧的话，方向是「份数 >= 2」或「主卡组前 N 种」，见计划文档。
    core = set(core_ids(fingerprint_ids, core_copies))
    core_ratio = (len(shared & core) / len(core)) if core else 1.0

    only_cur = len(a - b)
    only_fp = len(b - a)

    ok = overlap >= overlap_min and core_ratio >= core_min
    reason = ''
    if not ok:
        if overlap < overlap_min:
            reason = ('只有 %.0f%% 的卡重合（需要 %.0f%%）'
                      % (overlap * 100, overlap_min * 100))
        else:
            reason = ('关键卡只命中 %.0f%%（需要 %.0f%%）'
                      % (core_ratio * 100, core_min * 100))
    return Match(True if ok else False, overlap=overlap,
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
    """指纹目录。

    ⚠️ `paths.DATA_DIR` 是 **import 期定格的常量**，测试改 `MD_DATA_DIR`
    对它无效（所有测试都写进真 `data/`，互相污染）。所以这里支持
    `MD_FINGERPRINT_DIR` 直接覆盖 —— 单测用它指向临时目录。
    """
    import os
    env = os.environ.get('MD_FINGERPRINT_DIR')
    if env:
        return env
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
