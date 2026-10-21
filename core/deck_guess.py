# -*- coding: utf-8 -*-
"""按「对手露出的卡」反推卡组（反推计划 v2 · 刀 2）。

**纯离线纯函数**：不联网、不碰内存、不读 settings。输入三样东西：

  * `opp_revealed_ids` —— 对手打出的**游戏内卡 id**（日志 / 内存口径都给这个）；
  * 参照库 —— `tools/fetch_mdm_decks.py` 产出的 `data/mdm_deck_cards.json`
    （每个卡组 × 每张卡的使用率 `per`）；
  * 卡名分组 —— 异画卡是**同名不同 id**，必须按名字归一（见下）。

打分口径（研究 §4.1 钉死，别改）::

    主排序 = 命中数（露出的卡里有几张在这个卡组的卡表内）
    次排序 = **tier 表**（Tier1 > Tier2 > Tier3 > Trending > 榜外）—— 2026-10-14 用户拍板
    层内   = naive Bayes 总分  Σ log((per + ALPHA) / (100 + ALPHA))，卡表里没有 = 重罚
    置信度 = top1 卡表能解释的露出卡数 / 露出卡总数（按**名字组**去重后计数）

⚠️ 三处容易写错的地方（都是实测/数学结论）：

1. **别除以命中数**：固定命中数时未命中项是同一个常数，`bayes/n` 只是单调缩放，
   层内排序与纯 bayes 完全一致 —— v1 计划的「消大卡表便宜」是错的。
2. **置信度 ≤ 1 不等于可信**：只露出 1 张通用卡（`Maxx "C"` 出现在 96/98 张卡表里）
   时每个候选都能「解释」它 → 所有候选 confidence = 1/1 = 1.0。所以预填还要卡
   **证据量**（`:func:`prefill_ok`：命中 ≥3 且 露出 ≥5）。
3. **异画按名字组归一**：`22747` 与 `19188` 是同名不同号（S:P 小夜骑士），
   不归一会把同一张卡当两张，分母也会虚高。
"""
from __future__ import annotations

import json
import math
import os

from core.card_names import load as load_names, setcodes as _card_setcodes

from core import paths
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 参照库（`tools/fetch_mdm_decks.py` 产出，gitignore）
DEFAULT_REFERENCE = paths.resolve('mdm_deck_cards.json')

#: **自定义参照库**（`tools/build_custom_ref.py` 产出）—— 键是用户自己的卡组名
#: （如「狱神耀圣」），不随月度刷新被冲掉（AGENTS 铁律 7「自定义条目不被月度清理」）。
#: ⚠️ 与站内快照**分开两个文件**：`fetch_mdm_decks.py --all` 会整表覆盖前者。
DEFAULT_CUSTOM_REFERENCE = paths.resolve('mdm_deck_cards_custom.json')

#: `data/card_data.json`（卡 id → 中文名），分组时的兜底源
CARD_DATA = os.path.join(PROJ, 'data', 'card_data.json')

#: 预填口径 —— 用户 2026-09-11 定：只做 Tier1-3，冷门卡组手填
PRE_FILL_TIERS = ('Tier1', 'Tier2', 'Tier3')

#: 证据量闸门（达不到就只列候选、不预填）
MIN_HITS = 3
#: 给**候选**（比预填弱的一档）的证据下限：至少解释 2 张露出卡。
#: 为什么需要（2026-09-16 实机）：某局只露出 1 张「无限泡影」这种泛用卡，
#: 照样算出 4 条候选、界面把它们置顶 —— 全是噪声，还把搜索结果挤没了。
MIN_SUGGEST_HITS = 2
#: **整副牌**（我方卡组）的候选闸门：至少解释得了这么多张。
#: 为什么比 MIN_SUGGEST_HITS 高得多：输入是 40+15 张，只解释 1-2 张说明根本不是那套牌。
#: 实测标定（2026-09-16，用户正在编辑的「原石青眼白龙」41+15）：真身 Blue-Eyes
#: hits=32/35（解释度 0.91），第二名 Fiendsmith Control 只有 14/35（0.40）——
#: 取 15 正好卡在两者之间（真身远过线，噪声远不过线）。
MIN_OWN_HITS = 15
MIN_REVEALED = 5
MIN_CONFIDENCE = 0.5

#: bayes 平滑系数（研究 §4.1 实测取值）
ALPHA = 2.0

#: **字段校验**：条目名里的字段词，其 setcode 在露出卡里**一张都没出现**时，
#: 有效命中数**砍这么多张**（用户 2026-10-16 拍板「字段当罚分项」）。
#:
#: 为什么需要（2026-10-16 实机·自奏局）：露出 12 张、**零张巳剑**，却判成
#: `Mitsurugi Orcust` —— 它卡表 92 张（含自奏/巳剑/契印/绚岚全轴），靠别的轴
#: + 泛用卡凑到 12 命中，而它名字里的 `Mitsurugi`（巳剑 451）一张没中也不扣分。
#:
#: 为什么是「砍命中」而不是「罚 bayes」：排序第一键是**命中数分档**，
#: 只动 bayes 跨不过档（实测 `Mitsurugi Orcust` 12 命中 vs `Orcust` 9 命中
#: 不同档，罚 bayes 无效）。
#:
#: 为什么砍 2：全量回测（236 局）实测 —— 砍 2 最优（168→174 局），
#: 砍 3/4 不回归（说明不是过拟合），砍 6 会开始误伤。三个已锁案例全绿。
#:
#: ⚠️⚠️ **试过但没做**的口径（2026-10-16，别再试 —— 全量实测都更差）：
#:
#:   1. **「缺字段个数」当排序前置键 / 缺字段者沉底**：能修好当天那两局
#:      （自奏 / 神艺），但全量 **175→151**（73.5%→63.4%），且**破坏两个已锁
#:      案例**（幻影骑士团、阿莱斯特）。原因：条目名只点名一个字段、实际是
#:      多字段的卡组很多（`Phantom Knights Orcust` 名字并列两轴，可那局真的
#:      只该命中幻影轴；`Invoked` 指融合怪字段 244，而主卡 `Aleister the
#:      Invoker` 是另一个字段 481 —— 机器不知道它俩是一家）。**同一个规则
#:      在这份数据上必然二选一**，硬做就是过拟合。
#:   2. **卡表大小归一化**（命中率 = hits/卡表大小、或按卡表大小放大容差）：
#:      全量 **175→155/158**。原因：自奏局里 `Mitsurugi Orcust` 多出的命中是
#:      **别的真实轴**的卡（绚岚 465×2 + 气旋），不是泛用噪声 —— 它 bayes 更好
#:      说明那些卡使用率确实高；按卡表大小罚它会把幻影骑士团那类也一起罚错。
#:
#:   → **已知局限**：露出卡以泛用 + 单轴为主、正解与近邻只差 1~3 张命中时，
#:     纯统计分不开。要真修得做**轴级识别**（每个轴独立卡表 + 判主轴），
#:     那是另一个量级的工程，且与上面的多字段案例天然冲突（用户「之后再研究」）。
FIELD_MISS_CUT = 2

_NAME_CACHE = {}
_DATA_CACHE = {}


def load_reference(path=None):
    """读参照库。文件不在 → 空参照（调用方据此说「没有建议」）。

    ★ BUG-A6-02（2026-10-20）：**文件不在**和**文件在但读不出内容**要能区分 ——
    旧代码两种情况都静默返回 `{}`，界面上表现为「AI 建议全空」且**没有任何提示**
    （用户以为「这局本来就没建议」，实际是参照库没跑过 / 被删了）。
    缺失判据见 :func:`reference_missing`。

    ★ 2026-10-21（C 阶段一）：**自动合并自定义参照库**
    （:data:`DEFAULT_CUSTOM_REFERENCE`，由 `tools/build_custom_ref.py` 从卡组截图生成）。
    支持它之后，游戏内自组的卡组名（「狱神耀圣」）就有了**自己的**逐卡卡表，
    反推能直接判到它 —— 这是「自定义卡组名也是反推候选」的唯一入口。

    * 显式传 `path` 时**不合并**（测试 / 回测要确定性，别被磁盘上的自定义库干扰）；
    * 合并语义 = 名字撞车时**自定义赢**（用户亲手给的卡表比站内统计更权威）；
    * 自定义条目带 ``custom=True`` 标记，供 :func:`sort_candidates` 定档位。
    """
    path = path or DEFAULT_REFERENCE
    if path in _DATA_CACHE:
        return _DATA_CACHE[path]
    data = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    if path == DEFAULT_REFERENCE:
        data = _merge_custom(data)
    _DATA_CACHE[path] = data
    return data


def _read_custom_reference(path=None):
    """读自定义参照库 → ``{卡组名: 条目}``；没有/坏了 → ``{}``（不抛）。

    ⚠️ 自定义库与站内库**同构**（顶层 `version/source/fetched_at/skipped/decks`），
    所以取 ``['decks']``。也兼容早期「顶层直接是名字表」的旧文件 ——
    那种没有 `decks` 键，整份就是名字表。
    """
    path = path or DEFAULT_CUSTOM_REFERENCE
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    decks = data.get('decks')
    return decks if isinstance(decks, dict) else data


def _merge_custom(reference):
    """把自定义库并进参照库（**返回新 dict**，不动入参）。

    ⚠️ 自定义条目的 `tier` 一律清成 ``None``：自定义卡组不在 tier 榜上
    （交接文档 §1.7「不要塞 tier」），硬填一个会污染排序。档位由
    :func:`sort_candidates` 按 ``custom`` 标记单独处理（见 `CUSTOM_TIER_RANK`）。
    """
    custom = _read_custom_reference()
    if not custom:
        return reference
    decks = dict(reference.get('decks') or {})
    for name, d in custom.items():
        if not isinstance(d, dict) or not (d.get('cards')):
            continue
        item = dict(d)
        item['tier'] = None
        item['custom'] = True
        decks[name] = item                     # 名字撞车时自定义赢
    out = dict(reference)
    out['decks'] = decks
    return out


def reference_missing(path=None) -> bool:
    """参照库**根本不存在 / 里面一副卡组都没有**（= 该提示用户去刷新）。

    与 `load_reference()` 返回 `{}` 的区别：那个 `{}` 也可能是「文件坏了」，
    这一条只回答「要不要给用户一句『参照库缺失』的提示」。
    """
    path = path or DEFAULT_REFERENCE
    try:
        if not os.path.exists(path):
            return True
        data = load_reference(path)
        decks = data.get('decks') if isinstance(data, dict) else None
        return not (isinstance(decks, dict) and decks)
    except Exception:                                    # noqa: BLE001
        return True                                      # 读不出来 = 当缺


def clear_reference_cache(path=None) -> None:
    """**清掉参照库的进程内缓存** —— 刷新参照库后必须调，否则界面一直显示旧数据。

    ⚠️ 2026-10-10（1.01 发布前冒烟实测出的真 bug）：`load_reference()` 把
    `data/mdm_deck_cards.json` 永久缓存进 `_DATA_CACHE`，而全仓**没有清缓存入口**
    -> 跑完 `tools/fetch_mdm_decks.py --all` 之后，`deck_composition()` 仍返回
    **刷新前**的卡表（实测把文件改成只剩 3 副，界面照样报 `main=41`）。
    于是看板那个「参照库刷新」按钮（1.01 新增）**刷了不生效**。

    `path=None` = 清全部（默认参照库 + 任何显式路径）。
    UI 侧走 `core.ui_state.clear_reference_cache()`（同一个）。
    """
    _DATA_CACHE.clear()


def load_card_data(path=None):
    """`data/card_data.json` → ``{卡id: 中文名}``（分组兜底用）。"""
    path = path or CARD_DATA
    if path in _NAME_CACHE:
        return _NAME_CACHE[path]
    out = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for it in json.load(f):
                try:
                    out[int(it['id'])] = it.get('name') or ''
                except (KeyError, TypeError, ValueError):
                    continue
    _NAME_CACHE[path] = out
    return out


def build_name_groups(names=None, card_data=None):
    """``{卡id: 名字组}`` —— 同名（中文名优先）的 id 归成一组。

    分组源用 `core.card_names.load()`（14610 条，⊇ `data/card_data.json` 14298 条，
    且与 `display()` / `is_known()` 同源），card_data 只兜底。
    """
    table = load_names() if names is None else names
    groups = {}
    for cid, val in (table or {}).items():
        name = ''
        for v in (val if isinstance(val, (list, tuple)) else [val]):
            if v:
                name = v
                break
        if name:
            groups[int(cid)] = name
    for cid, name in (load_card_data() if card_data is None else card_data).items():
        if name:
            groups.setdefault(int(cid), name)
    return groups


def group_key(card_id, groups):
    """卡 id → 名字组。**查不到的原样留成 ``#id``** —— 它仍占分母（拉低置信度）。"""
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        return '#%s' % card_id
    name = groups.get(cid)
    return name if name else '#%d' % cid


def split_unknown(ids, groups):
    """把露出的卡拆成 ``(认得的, 认不得的)`` —— 认不得的是**卡表过期**的信号（M4）。"""
    known, unknown = [], []
    for i in ids or []:
        try:
            cid = int(i)
        except (TypeError, ValueError):
            unknown.append(i)
            continue
        (known if cid in groups else unknown).append(cid)
    return known, unknown


def expand(ids, groups):
    """按名字组归一 + 去重（保持首次出现顺序）—— 打分与分母都用它。"""
    seen, out = set(), []
    for i in ids or []:
        k = group_key(i, groups)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


#: **共用参照数据的卡组**（用户 2026-10-10 定，原 `ui_state.REF_SHARED_WITH`）：
#: 键 = 我们清单里的独立条目，值 = 它读哪条站内卡组的逐卡使用率。
#: 这张表**只写用户明确说过的**（「共用」），不推断 —— 猜错会让使用率对不上。
#: ★ 2026-10-11 用户拍板：这些条目**也进 AI 建议候选**（`build_index` 注入），
#: 且**同分时排在前**（`sort_candidates` tiebreak）—— 用户原话：
#: 「建议直接出『狱神耀圣』」（此前对手用狱神耀圣时建议只会到「耀圣」一级，
#: 手选才能落到独立条目）。
SHARED_REFERENCE = {
    '狱神耀圣': 'Elfnote',
}


def build_index(reference, groups):
    """参照库 → ``{站内名: {tier, source_name, total, cards:{名字组: per}}}``。

    一个卡组里同一张卡只留一条（异画两种 art 取较大的 per）；**空卡表直接丢**
    （它对打分没有意义，留着还会变成永远 0 分的幽灵候选）。
    """
    out = {}
    for name, d in (reference.get('decks') or {}).items():
        if not isinstance(d, dict):
            continue
        cards = {}
        for cid, info in (d.get('cards') or {}).items():
            per = (info or {}).get('per') if isinstance(info, dict) else info
            if per is None:
                continue
            k = group_key(cid, groups)
            per = int(per)
            if k not in cards or per > cards[k]:
                cards[k] = per
        if not cards:
            continue
        out[name] = {'tier': d.get('tier'),
                     'source_name': d.get('source_name') or name,
                     'total': d.get('total'),
                     #: ★ 自定义参照库的条目（`tools/build_custom_ref.py` 建）
                     'custom': bool(d.get('custom')),
                     'cards': cards}
    # ★ 共用参照数据的独立条目也进候选（2026-10-11 用户拍板）：
    #   卡表与原条目完全一致 -> 打分必然同分 -> 由 `sort_candidates` 的
    #   tiebreak 保证同分时**排在原条目前面**（建议直接出「狱神耀圣」）。
    for shared_name, src_name in SHARED_REFERENCE.items():
        if src_name in out and shared_name not in out:
            out[shared_name] = dict(out[src_name], shared=True)
    return out


def known_deck_names():
    """`meta_decks` 表里真实存在的条目名（含显示名）—— 拿不到返回 None。

    用来把参照库里**我们不认可的条目**剔掉（见 :func:`prepare`）。
    查不到（无库 / 异常）时返回 ``None`` = **不过滤**，宁可多列候选也不给错。

    ⚠️ 2026-10-10 实机（干净版第一局）：**表空 ≠ 不过滤** —— 首跑播种
    （first_run 后台抓 meta 清单，联网要几分钟）完成前表就是空的，这窗口
    期打的局会把参照库里的孤儿条目（用户已删除、如 Branded Elfnote）直接
    列进 AI 建议。用户口径「删了就不该再出现」→ 空表返回**空集** =
    全过滤 = 暂时没有建议（首跑窗口内本来也没有历史局要标，可接受）。
    """
    try:
        from core.storage import Storage
        rows = Storage().list_meta_decks(include_hidden=True) or []
    except Exception:                                    # noqa: BLE001
        return None
    if not rows:
        return set()
    names = set()
    for r in rows:
        # ★ 2026-10-14 用户口径：**engine 条目不进反推候选** ——
        #   站内把混轴叫 `Xxx Engine`，用户不用这个分类（见 AGENTS「engine 行
        #   保留为隐藏条目：选择器 / 标注仍只见 deck_type」）。反推候选就是
        #   选择器的内容，所以这里按 kind 剔掉。
        #   实机起因：DC 杯露出 5 张（3 泛用 + 2 耀圣）时耀圣系 6 分支全部 5/5，
        #   加了 tier 排序后 `Elfnote Engine`（Tier1）压住了用户要的「狱神耀圣」。
        #   ⚠️ 只影响**反推候选**；Tier 表仍列出全部混轴（那是另一条路径）。
        if str(r.get('kind') or '').strip() == 'engine':
            continue
        # ★ BUG-B2A-07：收**五字段 + 别名**，与 `deck_images.build_index` /
        #   AGENTS 铁律 7「比所有叫法」同一口径。原来只有 3 个字段，于是
        #   「用户改过显示名 / 只有别名或日文名对得上」的条目会被当成
        #   「用户不认可」滤掉 → 漏候选；反过来已删条目若英文名仍在隐藏行
        #   里也照样留下。真库当前 119 条参照库条目全在 3 字段里可达
        #   （= 现在不触发），但这是迟早会撞上的口径漂移。
        for k in (r.get('name'), r.get('name_display'), r.get('name_cn'),
                  r.get('name_ja')):
            k = str(k or '').strip()
            if k:
                names.add(k)
        for a in (r.get('aliases') or []):
            k = str(a or '').strip()
            if k:
                names.add(k)
    return names or None


def engine_kinds():
    """`meta_decks` 里 `kind='engine'` 的条目名集合（拿不到返回空集）。

    ★ 2026-10-20：这些条目**绝不能进反推候选**。用户口径（AGENTS 铁律 7）：
    站内把混轴叫 `Xxx Engine`，我们不用这个分类 —— engine 行保留为**隐藏条目**
    只为 Tier 表列出全部混轴，选择器 / 标注**只该见 deck_type**。

    ⚠️ 不能只靠 :func:`known_deck_names` 的 keep 过滤：那个集合收的是
    **所有叫法的并集（含别名）**，而 `Elfnote` 的别名里就有历史遗留的
    `Elfnote Engine` —— 会让 engine 条目被「别名捞回来」，正是实机事故。
    """
    try:
        from core.storage import Storage
        rows = Storage().list_meta_decks(include_hidden=True) or []
    except Exception:                                    # noqa: BLE001
        return set()
    return {str(r.get('name') or '').strip() for r in rows
            if str(r.get('kind') or '').strip() == 'engine'
            and str(r.get('name') or '').strip()}


def _fold_engines(decks):
    """把 `kind='engine'` 的条目按 `source_name` 并进目标条目（返回新 dict）。

    ★ 2026-10-20 用户口径：候选里只显示**具体卡组名**，但混轴的卡表要能用
    （「巳剑黯蜜就取 Yummy Engine 的参照库」）。

    * 目标 = 条目的 ``source_name``；目标条目不在参照库里时**以 engine 条目的
      显示名新建**（例：`Mitsurugi Yummy` 不在库里 -> 用「巳剑黯蜜」这个名字）；
    * 合并语义 = 逐卡 per 取**最大值**（并集，不是平均 —— 那是
      `tools/fetch_mdm_decks.py` 建 engine 条目时的事，这里只负责归并）；
    * 目标已有的 tier / total 保留（那是具体卡组的真身属性）。

    ⚠️ 纯函数、不碰库 —— 单测直接喂 dict 就能锁行为。
    """
    if not isinstance(decks, dict) or not decks:
        return decks
    eng = engine_kinds()
    if not eng:
        return decks
    out = {k: v for k, v in decks.items() if k not in eng}
    for name in sorted(eng):
        d = decks.get(name)
        if not isinstance(d, dict):
            continue                      # 参照库里没有这条 engine（正常）
        target = (d.get('source_name') or '').strip() or name
        #: ⚠️ `source_name` 指向**自己**时没有归并意义（等于自我合并），
        #: 直接丢 —— 否则这条 engine 会原样留在候选里（实测踩过：
        #: 合成参照库里 `Elfnote Engine` 的 source_name 就是它自己）。
        if target == name:
            continue
        #: ★ 自定义条目**永不被 engine 归并**（2026-10-21）：它的 `source_name`
        #: 可能是用户自己写的名字，撞上 engine 条目就等于被吞掉。
        if d.get('custom'):
            continue
        src_cards = d.get('cards') or {}
        if not src_cards:
            continue
        base = out.get(target)
        if base is None:
            #: 目标条目不在参照库 -> 用 engine 的显示名新建（中文名优先，
            #: 例 `Mitsurugi Yummy` -> 「巳剑黯蜜」）
            out[target] = {'tier': d.get('tier'),
                           'source_name': target,
                           'total': d.get('total'),
                           'custom': bool(d.get('custom')),
                           'cards': dict(src_cards)}
            continue
        #: ★ 2026-10-21：目标条目**存在但卡表为空**时，同样整份接管 engine 的
        #: 卡表（不是走下面的 merge —— 那会得到一个空卡表然后让 engine 条目
        #: 被删掉，等于这副牌的数据整个消失）。
        #: 实测来源：`Mitsurugi Yummy`（`source_name=None`、`cards={}`）
        #: 对应 `Yummy Engine`（`source_name='Mitsurugi Yummy'`）。
        if not (base.get('cards') or {}):
            filled = dict(base)
            filled['cards'] = dict(src_cards)
            filled['total'] = base.get('total') or d.get('total')
            filled['source_name'] = base.get('source_name') or target
            out[target] = filled
            continue
        merged = dict(base.get('cards') or {})
        for cid, info in src_cards.items():
            per = (info or {}).get('per') if isinstance(info, dict) else info
            if per is None:
                continue
            old = merged.get(cid)
            old_per = (old or {}).get('per') if isinstance(old, dict) else old
            if old_per is None or per > old_per:
                merged[cid] = info
        base = dict(base)
        base['cards'] = merged
        out[target] = base
    return out


def prepare(reference, groups=None, keep=None):
    """``(参照库索引, 名字组)`` —— CLI 一次准备好、逐局复用。

    ⚠️ ``keep``：**只保留这些条目**（`None` = 全留）。
    参照库 `data/mdm_deck_cards.json` 是**月度快照、只增不减** —— 里面
    仍然留着我已从覆盖表删掉的条目，于是删了也照样出现在候选里
    （2026-10-10 实测：删掉 `Branded Elfnote` 后它仍并列第一，
    而用户早已不用这副牌）。所以这里按 `meta_decks` 过滤一次：
    **用户不认可的条目不进候选**。
    """
    if keep is None:
        keep = known_deck_names()
    # ⚠️ keep 是**空集**也要过滤（= 全滤 = 无候选）—— 首跑播种窗口期的口径
    #    见 `known_deck_names`。所以这里判 `is not None`，不能 `if keep:`。
    #: ★ 2026-10-21（C 阶段一）：**自定义条目豁免 keep 过滤**。
    #:
    #: `keep` 的语义是「用户在 `meta_decks` 里认可的条目」，用来剔掉参照库里
    #: 已删除的孤儿。但自定义库条目（`tools/build_custom_ref.py` 从截图建的）
    #: **就是用户亲手建的** —— 不存在「不认可」，被滤掉只会让刚建好的库静默失效
    #: （实测：只在 `opp_decks`、不在 `meta_decks` 的名字，`prepare()` 后一条不剩）。
    if keep is not None:
        decks = reference.get('decks') or {}
        dropped = [k for k in decks
                   if k not in keep and not (decks[k] or {}).get('custom')]
        if dropped:
            reference = dict(reference)
            reference['decks'] = {
                k: v for k, v in decks.items()
                if k in keep or (v or {}).get('custom')}
    #: ★★ 2026-10-20（用户实报「倒数第二局判成 Elfnote Engine」）：
    #: engine 条目不进候选，但它的**卡表要归到具体卡组名下**。
    #:
    #: 用户口径（2026-10-20 拍板）：「混轴只显示在 Tier 表，具体名字还是要在
    #: 卡组名管理里区分，不显示混轴」—— 所以反推候选里**只出现具体卡组名**
    #: （狱神耀圣 / 巳剑黯蜜 / K9VS / 耀圣杀手旋律），永远不出现「耀圣混轴」。
    #: 但混轴的**卡表数据要能用**：「巳剑黯蜜就取 Yummy Engine 的参照库」
    #: （与狱神耀圣取耀圣混轴的卡表同理）—— 混轴卡表 = 各相关卡组的并集，
    #: 证据比单轴更全。
    #:
    #: 实现：把 engine 条目的 cards 按 `source_name` 合并进目标条目
    #: （取逐卡 per 的**最大值** —— 并集语义），然后删掉 engine 条目本身。
    #:
    #: ⚠️ 顺带修掉一个历史坑：`Elfnote` 的别名里带着 `Elfnote Engine`
    #: （早期「engine 当别名」的口径），靠它 `keep` 会把 engine 条目捞回来。
    #: 所以这里**不依赖 keep**，独立按 `engine_kinds()` 处理。
    decks = _fold_engines(reference.get('decks') or {})
    reference = dict(reference)
    reference['decks'] = decks
    groups = build_name_groups() if groups is None else groups
    return build_index(reference, groups), groups


def load_index(path=None):
    """参照库 JSON → ``(索引, 名字组)``；**文件不在 / 空的返回 ``(None, None)``**。

    这是 CLI 与 M7 UI **唯一**的参照库入口（UI 不该 import `tools/`）——
    拿不到参照库时调用方静默降级成「没有建议」，不影响手标。
    """
    reference = load_reference(path)
    if not (reference.get('decks')):
        return None, None
    return prepare(reference)


def revealed_ids(row):
    """一条 `matches` 行 → 对手露出的卡 id 列表（`opp_revealed_ids` 是 JSON 列）。

    坏数据（空 / 不是数组 / 元素转不成 int）一律当「没有」处理，绝不抛。
    """
    raw = (row or {}).get('opp_revealed_ids')
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def suggest(row, index, groups, prefill_tiers=PRE_FILL_TIERS):
    """一局的候选 —— **CLI 与 M7 UI 共用的这一条路径**。

    没参照库 / 这局没有露出卡（API 补录局）→ 空列表（调用方静默不显示建议）。
    **证据不足也不给**（见 `MIN_SUGGEST_HITS`）—— 少到撑不起统计时，top1 是噪声。
    """
    if index is None:
        return []
    ids = revealed_ids(row)
    if not ids:
        return []
    return [c for c in guess_deck(ids, index, groups, prefill_tiers)
            if int(c.get('hits') or 0) >= MIN_SUGGEST_HITS]


def prefill_ok(hits, revealed, confidence):
    """能不能**预填** —— 三个条件同时成立（少一个就只列候选）。"""
    return (hits >= MIN_HITS and revealed >= MIN_REVEALED
            and confidence >= MIN_CONFIDENCE)


def suggest_own_deck(ids, index, groups, prefill_tiers=PRE_FILL_TIERS):
    """**我方整副牌** -> 原型候选（复用 `guess_deck` 的同一条打分路径）。

    与 `suggest()`（对手露出卡）的区别只有**输入**与**闸门**（`MIN_OWN_HITS`）；
    不写第二份口径。用在哪：卡组编辑界面读到的牌（`core.memread.deck_edit`）——
    编辑中的卡组没有名字/id（实测 `DeckID=0`、`DeckName='Deck'`），只能靠卡表认。
    """
    if index is None or not ids:
        return []
    return [c for c in guess_deck(list(ids), index, groups, prefill_tiers)
            if int(c.get('hits') or 0) >= MIN_OWN_HITS]


def guess_deck(revealed_ids, index, groups, prefill_tiers=PRE_FILL_TIERS):
    """打分并排序，返回候选列表（已按主排序排好）。

    每条候选：``name / tier / in_prefill_set / hits / bayes / confidence /
    explained / revealed / prefill``。**只有 top1 会被标 ``prefill=True``**
    （且必须过闸门 + 在预填口径内）。
    """
    order = expand(revealed_ids, groups)
    n = len(order)
    miss = math.log(ALPHA / (100.0 + ALPHA))
    #: 露出卡覆盖到的 setcode 集合（**字段校验**用，见 `FIELD_MISS_CUT`）——
    #: 只算一次，逐候选复用。
    revealed_sc = _revealed_setcodes(revealed_ids, groups)
    cands = []
    for name, d in index.items():
        cards = d['cards']
        hits, score = 0, 0.0
        for k in order:
            per = cards.get(k)
            if per is None:
                score += miss
            else:
                hits += 1
                score += math.log((per + ALPHA) / (100.0 + ALPHA))
        conf = (float(hits) / n) if n else 0.0
        tier = d.get('tier')
        #: ★ 字段校验：条目名点名的字段，在露出卡里一个都没出现 -> 砍有效命中。
        #: （**豁免规则**在循环外统一算 —— 它要看「全体候选」的最优 per_hit，
        #: 见下面的 `_clear_field_miss_when_dominant`。这里先原样记下缺字段。）
        fm = missing_named_fields(name, revealed_sc)
        cands.append({
            'name': name, 'tier': tier,
            'source_name': d.get('source_name') or name,
            'shared': bool(d.get('shared')),
            #: 自定义参照库条目（用户从卡组截图建的）—— 排序时按 `CUSTOM_TIER_RANK`
            'custom': bool(d.get('custom')),
            #: ★ 自定义条目**永远在预填口径内**（2026-10-21）：`PRE_FILL_TIERS`
            #: 是「只预填 Tier1-3」的老口径，自定义卡组天然没有 tier ——
            #: 不豁免的话它会排到第一却不预填（`prefill=False`），
            #: 界面不标「建议」、CLI 回车也不采纳，等于白排。
            'in_prefill_set': (bool(d.get('custom'))
                               or tier in (prefill_tiers or ())),
            'hits': hits, 'bayes': score, 'confidence': conf,
            'explained': hits, 'revealed': n,
            'sample': d.get('total'),
            #: 名字里「零露面」的字段列表（空 = 都露面了 / 名字无字段词）
            'field_miss': fm,
            #: 有效命中数 = hits − (有缺字段 ? FIELD_MISS_CUT : 0)，排序第一键用它
            'eff_hits': max(0, hits - (FIELD_MISS_CUT if fm else 0)),
            'prefill': False,
        })
    #: ★★ 2026-10-20（天杯龙局）：**「全解释且证据压倒性」的候选豁免字段砍分**。
    #:
    #: 事故：对手露出 5 张（含天杯龙本家 `哆啦哆啦`），`Tenpai Dragon` 命中
    #: **5/5（全部解释）**、bayes −3.55（第二名 −8.05）、per 全 ≥40；但
    #: `Dragon` 在 ygocdb 里映射到 24 个泛用龙族 setcode（真红眼/青眼/电子龙/
    #: 雷龙…），并入 `Tenpai`(426) 后凑出 25 个字段，一个都没露面 -> 砍 2 张
    #: -> eff=3，掉出「差 1 张同档」的档位 -> **输给只命中 4/5、per 均值 38.5
    #: （最低 15）的同调士**。
    #:
    #: 只看 `confidence == 1.0` 不够（实测会掉准确率）：`Dracotail`(7/7)、
    #: `Rose Dragon`(21/21) 这类**大卡表混轴**条目也常常全解释，它们本来
    #: 就该被压。真正的区分点是 **per 强度**（`bayes/hits`，越小越强）：
    #:   * 天杯龙 −0.71 vs 非全解释最佳 −2.01  -> 压倒性，豁免（改对了）
    #:   * 烙印局 −0.91 vs −1.01              -> 几乎一样，不豁免
    #:   * 耀圣杀手旋律局 −0.96 vs −0.56      -> 更差，不豁免
    #: 回测（266 局）：107 -> 108（65.6% -> 66.3%），零退化局。
    _clear_field_miss_when_dominant(cands)
    sort_candidates(cands)
    if cands:
        top = cands[0]
        if top['in_prefill_set'] and prefill_ok(top['hits'], top['revealed'],
                                                top['confidence']):
            top['prefill'] = True
    return cands


#: tier -> 排序权重（**越大越优先**）。用户 2026-10-14 拍板：命中数相同时
#: 按 tier 表排，``Tier1 > Tier2 > Tier3 > Trending > 榜外``。
#:
#: 为什么非加不可（2026-10-14 DC 杯实机）：露出卡少、且泛用卡占多数时，
#: **同一体系的多个分支会全部同分** —— 实测露出 5 张（3 张泛用 + 2 张耀圣），
#: 耀圣系 6 个分支全是 5/5、confidence 全 1.0。此时纯 bayes 会把**榜外的
#: 混轴条目**顶到第一（`Elfnote White Forest` 卡表只有 69 张，命中卡的
#: 使用率更集中 → bayes 更高），而用户口径是「优先榜上的主流卡组」。
#:
#: ⚠️ 试过但**没做**的口径（别再试）：「混轴条目缺轴证据就扣分」。数据不支持 ——
#: 站内基础条目的卡表是**变体并集**（`Elfnote` 150 张已包含白森轴的卡），
#: 混轴条目（`Elfnote White Forest` 69 张）反而是它的**真子集**，既没有
#: 「全库唯一卡」（实测 0 张），也没有「相对基础条目的差集」可算。
#: 命中数**容差**（2026-10-14 用户拍板）：差这么几张以内的命中数视为**同档**，
#: 档内由 tier / bayes 决胜。
#:
#: 为什么需要：卡表大的条目会因为**多收泛用卡**而白捡命中 —— 实测幻影骑士团
#: 那局 `Phantom Knights Orcust`（卡表 126 张）靠「多多迷宝・噗噜海月」
#: 「绚岚之达象」两张 **per 仅 20%** 的泛用卡拿到 19 命中，压过
#: `Phantom Knights`（卡表 47 张）的 18 命中 —— 而后者命中的是**本家卡**
#: 「方解黑骑士」（per=88）。bayes 本来就把低 per 的命中罚得很重
#: （-25.9 vs -10.2），是「命中数优先」把它盖住了。
#: ⚠️ 但命中数**不能整个降权**（试过，错的）：实测会选出「只命中 3 张的
#: Tier1 卡组」（Dracotail / Branded）压过命中 5/5 的正确答案 ——
#: 所以只**吸收 1 张以内的噪声**，大差距仍然按命中数说话。
HITS_TOLERANCE = 1

#: **tier 说话的置信度门槛**（2026-10-14 用户实机第三条）：
#: 只有置信度 >= 这个值时，tier 才排在 bayes 前面。
#:
#: 为什么：tier 是「主流度」，不是「匹配度」—— 它只在**候选之间分不出高下**时
#: 才该当裁判（露出卡全被解释掉、hits 与 bayes 都咬得很紧）。一旦置信度低，
#: 说明证据本身有区分度，此时按 bayes 才对。
#: 实测反例（2026-10-14 22:12，用户报「对方不可能是烙印星辰」）：
#:   `Dracotail` 3/5 bayes **-17.2** Tier1  vs  `Invoked` 3/5 bayes **-8.2** 榜外
#:   —— 置信度 0.6，tier 优先把 Dracotail 顶到第一，而正确解是 Invoked。
#: 反向案例（同日 17:57 耀圣局）：狱神耀圣与 Elfnote White Forest 都是 5/5、
#:   置信度 1.0（都全解释）—— 此时必须靠 tier 选主流，否则榜外的混轴会赢。
TIER_TRUST_CONF = 0.8

TIER_RANK = {'Tier1': 4, 'Tier2': 3, 'Tier3': 2, 'Trending': 1}


#: **自定义参照库条目的排序档位** = 与 `Tier3` 同权（2026-10-21 用户拍板：
#: 「自定义卡组很难是主流卡组本就不在 tier 表」）。
#:
#: 为什么必须有这一条：`tier_rank(None) == 0`（榜外），而排序第一层是
#: `eff_hits` 分档、**层内 tier 先于 bayes** —— 自定义条目没有 tier 就永远
#: 沉在所有站内条目之后。实测（露出耀圣系 6 张、自定义卡表 per 全 100）：
#: 它的 bayes 是 **+0.00**（比第一名 `Elfnote` 的 −0.06 还好），但仍排**第 4**；
#: 给它一个 Tier2 就立刻第 1。
#:
#: **自定义条目与 `Tier2` 同档**（2026-10-21 用户口径 + 全量回测标定）。
#:
#: 用户原话：「自定义卡组很难是主流卡组本就不在 tier 表」—— 所以不能拿
#: `tier_rank(None)=0`（榜外）去判它输。
#:
#: ⚠️⚠️ **但档位本身是个钝器，实测两次翻车，最终只用它做「不垫底」**：
#:
#: | 档位取值 | 全量回测（273 局，官方口径） |
#: |---|---|
#: | 高于全部 tier | 候选内 **80/136**（比基线 110 掉 30 局） |
#: | 与 Tier2 同档 | 候选内 **114/195**（`in_prefill_set` 分母变大，仍偏低） |
#: | **基线（不含自定义）** | 候选内 **136/183** |
#:
#: 根因：**自定义库是「整个卡组的并集」**，卡表比站内单一条目更大、覆盖面更宽，
#: 于是命中数天然虚高一点点。实测「耀圣」（11 副并集，94 张卡）vs 站内
#: `Elfnote Kewl Tune`（51 张卡）在真身是「耀圣杀手旋律」的 19 局上：
#:
#:   耀圣 命中 14/15、bayes **−17.21**   |  KewlTune 15/15、bayes **−0.78**
#:
#: bayes 差 20 倍（卡表大 → 未命中项多 → 惩罚重），但命中数只差 1 张 ——
#: 而 `HITS_TOLERANCE=1` 把差 1 张算**同档**，同档内 tier 先说话，自定义就赢了。
#:
#: → **结论：`custom` 不进 tier 档位**。它只在 `eff_hits` **严格领先**时
#: 才该赢（那是它确实多解释了卡），平局时按原有 tier/bayes 规则走。
#: 保留本常量只为「自定义条目别落到 rank 0」，实际由
#: :func:`sort_candidates` 的 tiebreak 决定（见那里）。
#:
#: 顺带：这也修掉了 `in_prefill_set` 的副作用 —— 自定义条目曾因为
#: 「永远在预填口径内」把 183 局的分母撑到 195，稀释了可比的统计口径。
CUSTOM_TIER_RANK = TIER_RANK['Tier2']


#: 自定义条目的档位**只在这个命中数及以上才生效**（2026-10-21 实测标定）。
#:
#: 为什么需要这条下限：档位抬到「高于全部 tier」之后，**0 命中的自定义条目**
#: 会在「所有候选都是 0 命中」的场合靠档位赢下 top1。
#: 实测 30 组随机露出卡（每组 6 张，与自定义卡表零关系）：
#: **11/30 组的 top1 变成了「狱神耀圣」且 hits=0** ——
#: `suggest()` 的 `MIN_SUGGEST_HITS` 会滤掉它，但 CLI 的 `--guess` 与回测
#: 走的是 `guess_deck` 直通，会看到这个假第一名。
#:
#: 2 = `MIN_SUGGEST_HITS`（「至少解释 2 张露出卡」才算候选）——
#: 档位只在**够格当候选**之后才说话，语义一致。
CUSTOM_RANK_MIN_HITS = 2


def _sort_tier_rank(c):
    """候选的**排序档位** —— 自定义条目**不靠 tier 抬档**。

    见 `CUSTOM_TIER_RANK` 的实测：自定义库是「并集卡表」，命中数天然虚高，
    给它 tier 档位会让它抢走真身（19 局「耀圣杀手旋律」全被判成「耀圣」）。
    所以这里**只返回真实 tier**（自定义条目是 ``None`` → rank 0 = 榜外），
    自定义的优先权交给 :func:`sort_candidates` 的 tiebreak ——
    那一步只影响**完全同分**（同档、同 tier、同 bayes）的情形，风险小得多。
    """
    return tier_rank(c.get('tier'))


def tier_rank(tier):
    """tier 字符串 -> 排序权重；榜外 / None / 未知一律 ``0``（沉到最后）。"""
    return TIER_RANK.get(str(tier or '').strip(), 0)


#: 英文词 -> 该词的**主要 setcode**（懒建、进程内缓存）。建一次约 0.3s。
#: 只取出现最多的那个字段号 —— 词义歧义时（`Phantom` 在 219/27 都有）
#: 取主流那个即可，本判据只用来「发现名字里的字段在露出卡里一张没中」。
_WORD_SC_CACHE = None
#: 分词时忽略的词（不是字段名）
_WORD_STOP = frozenset((
    'the', 'of', 'and', 'a', 'no', 'control', 'engine', 'deck', 'type',
    'pure', 'mix', 'variant',
))


def _word_setcode_index():
    """`{英文词(小写): setcode}` —— 从 ygocdb 卡表反查（懒建 + 缓存）。

    为什么用「英文名里的词」反查：站内条目名是英文（`Mitsurugi Orcust`），
    而 setcode 只能从卡表拿 —— 卡表里 `Mitsurugi` 系的卡 en_name 都带
    `Mitsurugi` 这个词，反过来就能把「名字里的词」映到字段号。
    """
    global _WORD_SC_CACHE
    if _WORD_SC_CACHE is not None:
        return _WORD_SC_CACHE
    import collections as _c
    import re as _re
    from core.card_names import ygocdb as _ygocdb
    words = _c.defaultdict(_c.Counter)
    try:
        db = _ygocdb() or {}
    except Exception:                                    # noqa: BLE001
        db = {}
    for cid, v in db.items():
        en = (v or {}).get('en_name') or ''
        if not en:
            continue
        scs = _card_setcodes(int(cid)) if str(cid).isdigit() else frozenset()
        if not scs:
            continue
        for w in set(_re.findall(r"[A-Za-z][A-Za-z'\-]+", en)):
            if w.lower() in _WORD_STOP:
                continue
            for sc in scs:
                words[w.lower()][sc] += 1
    #: ⚠️ 保留该词的**所有** setcode（不是只取最多的那个）—— 同一个词可能对应
    #: 多个字段：`Invoked` 的融合怪是 244、而主卡 `Aleister the Invoker` 是 481，
    #: 只取最多的 244 会把主卡场次误判成「缺字段」（2026-10-16 实测踩过）。
    #: 但**丢掉占比过低的**（噪声字段，如某个词偶然出现在别的字段卡名里）。
    _WORD_SC_CACHE = {}
    for w, cnt in words.items():
        top = cnt.most_common()
        hi = top[0][1]
        _WORD_SC_CACHE[w] = {sc for sc, n in top if n >= max(1, hi * 0.2)}
    return _WORD_SC_CACHE


def clear_word_setcode_cache():
    """清字段词索引缓存（测试 / 上游卡表刷新后用）。"""
    global _WORD_SC_CACHE
    _WORD_SC_CACHE = None


def name_setcodes(name):
    """条目名 -> 它在名字里点名了的 setcode 列表（去重；认不出的词忽略）。

    一个词可能对应多个字段（`Invoked` -> {244, 481}），这里取**并集** ——
    任一字段露面都算「这个字段出现了」（见 `missing_named_fields`）。
    """
    import re as _re
    idx = _word_setcode_index()
    out = []
    for w in _re.split(r"[\s\-_]+", name or ''):
        if not w or w.lower() in _WORD_STOP:
            continue
        scs = idx.get(w.lower())
        if not scs:
            continue
        for sc in sorted(scs):
            if sc not in out:
                out.append(sc)
    return out


def missing_named_fields(name, revealed_sc):
    """条目名点名的字段里，setcode 在**露出卡**里一个都没出现的那些。

    返回 `[]` = 名字里的字段都露面了（或名字里没有可识别的字段词）。
    """
    return [sc for sc in name_setcodes(name) if sc not in revealed_sc]


def _revealed_setcodes(ids, groups):
    """露出卡 -> 覆盖到的 setcode 集合（字段校验用）。"""
    out = set()
    for cid in ids or []:
        try:
            out |= _card_setcodes(int(cid))
        except (TypeError, ValueError):
            pass
    return out


def _clear_field_miss_when_dominant(cands):
    """「全解释 + per 强度压倒性」的候选豁免字段砍分（**原地修改**）。

    见 :func:`guess_deck` 里那段说明。判据两条同时成立：

    1. ``confidence >= 1.0``（露出的每一张这张卡表里都有 = 没有凑数空间）；
    2. ``bayes/hits`` **严格优于**所有「非全解释」候选里的最佳值 ——
       只赢一点点不算（烙印局 −0.91 vs −1.01 就是这种），必须明显更强
       （天杯龙 −0.71 vs −2.01）。

    ⚠️ ``bayes/hits`` 是**负数**，越接近 0 越强 —— 所以「优于」是 ``>``，
    不是 ``<``（2026-10-20 第一版就写反了，天杯龙一局都没救回来）。

    豁免后 ``field_miss`` 清空、``eff_hits`` 回填成 ``hits``。
    """
    #: ⚠️ 只跟**有竞争力的**非全解释候选比（`conf >= TIER_TRUST_CONF`）——
    #: 只命中 1 张的垃圾候选 per_hit 会低到 −17（如 `Labrynth`），
    #: 拿它当「最佳」等于永远不豁免（2026-10-20 实测踩过）。
    partial = [c for c in cands
               if TIER_TRUST_CONF <= (c.get('confidence') or 0.0) < 1.0
               and c.get('hits')]
    if not partial:
        return
    #: ⚠️ 负数，`max` = **最强**（越接近 0 越好）
    best_partial = max((c['bayes'] / c['hits']) for c in partial)
    for c in cands:
        if not c.get('field_miss'):
            continue
        if (c.get('confidence') or 0.0) < 1.0 or not c.get('hits'):
            continue
        if (c['bayes'] / c['hits']) > best_partial:
            c['field_miss'] = []
            c['eff_hits'] = c['hits']


def sort_candidates(cands, mode='hits'):
    """排序（原地 + 返回同一列表）。

    ``mode='hits'``（默认，用户口径）= 命中数**分档**（差 1 张同档）→
    **置信度 >= `TIER_TRUST_CONF` 时 tier 优先、否则 bayes 优先** → 精确命中数；
    ``mode='bayes_per_hit'`` = 研究里的对照打分器（**回测对照组，保持原样别动**）。
    """
    # tiebreak 里的 `not c.get('shared')`：同分时**共用参照数据的独立条目
    # 排在站内原条目前面**（狱神耀圣 > 耀圣，2026-10-11 用户拍板）。
    if mode == 'bayes_per_hit':
        key = lambda c: (-(c['bayes'] / c['hits']) if c['hits'] else 1e9,  # noqa: E731
                         -c['hits'], not c.get('shared'), c['name'])
    else:
        # ★ 2026-10-14 用户拍板：命中数**分档**（差 HITS_TOLERANCE 张算同档）。
        #   档内**看置信度**：高（都全解释）-> tier 选主流；低（证据有区分度）
        #   -> bayes。见 TIER_TRUST_CONF / HITS_TOLERANCE 的注释。
        bucket = HITS_TOLERANCE + 1

        def key(c):
            #: ★ 第一键用**有效命中**（`eff_hits`，缺字段时已砍过）——
            #: 见 `FIELD_MISS_CUT`。旧候选没有该键时退化成 hits（兼容回测/老调用）。
            head = (-(int(c.get('eff_hits', c['hits'])) // bucket),)
            mid = ((-_sort_tier_rank(c), -c['bayes'])
                   if (c.get('confidence') or 0.0) >= TIER_TRUST_CONF
                   else (-c['bayes'], -_sort_tier_rank(c)))
            #: tiebreak 里 `custom` 排在最前（2026-10-21）：用户亲手给的卡表
            #: 比站内统计更权威 —— 同档同 bayes 时先出「狱神耀圣」而不是「耀圣」。
            return head + mid + (-c['hits'], not c.get('custom'),
                                 not c.get('shared'), c['name'])
    cands.sort(key=key)
    return cands


def find_tail_residue(rows, min_k=2):
    """露出卡体检：**新局开头 k 张 == 上一局结尾 k 张**（可逆序）→ 串味嫌疑。

    `tools/archive_matches.py` 的 `parse_seg` 以前会把上一局的收尾 ACT 行算进
    本局（回扫 60 行抓硬币行那段），2026-09-14 已修（`648d146`），但**历史行还带着
    这个痕迹**。反推整个打分都压在露出卡上，所以回测前必须扫一遍：
    命中的局不进分母，交人工核。

    ``rows``：按时间升序的 ``[{'match_id':…, 'ids':[…]}]``。
    """
    hits = []
    for i in range(1, len(rows)):
        prev_ids = list(rows[i - 1].get('ids') or [])
        cur_ids = list(rows[i].get('ids') or [])
        best = 0
        for k in range(min_k, min(len(prev_ids), len(cur_ids)) + 1):
            if set(prev_ids[-k:]) == set(cur_ids[:k]):
                best = k
        if best:
            hits.append({'prev_match_id': rows[i - 1].get('match_id'),
                         'match_id': rows[i].get('match_id'),
                         'k': best,
                         'cards': cur_ids[:best]})
    return hits
