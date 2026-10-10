# -*- coding: utf-8 -*-
"""按「对手露出的卡」反推卡组（反推计划 v2 · 刀 2）。

**纯离线纯函数**：不联网、不碰内存、不读 settings。输入三样东西：

  * `opp_revealed_ids` —— 对手打出的**游戏内卡 id**（日志 / 内存口径都给这个）；
  * 参照库 —— `tools/fetch_mdm_decks.py` 产出的 `data/mdm_deck_cards.json`
    （每个卡组 × 每张卡的使用率 `per`）；
  * 卡名分组 —— 异画卡是**同名不同 id**，必须按名字归一（见下）。

打分口径（研究 §4.1 钉死，别改）::

    主排序 = 命中数（露出的卡里有几张在这个卡组的卡表内）
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

from core.card_names import load as load_names

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 参照库（`tools/fetch_mdm_decks.py` 产出，gitignore）
DEFAULT_REFERENCE = os.path.join(PROJ, 'data', 'mdm_deck_cards.json')

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

_NAME_CACHE = {}
_DATA_CACHE = {}


def load_reference(path=None):
    """读参照库。文件不在 → 空参照（调用方据此说「没有建议」）。"""
    path = path or DEFAULT_REFERENCE
    if path in _DATA_CACHE:
        return _DATA_CACHE[path]
    data = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    _DATA_CACHE[path] = data
    return data


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
        for k in (r.get('name'), r.get('name_display'), r.get('name_cn')):
            k = str(k or '').strip()
            if k:
                names.add(k)
    return names or None


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
    if keep is not None:
        decks = reference.get('decks') or {}
        dropped = [k for k in decks if k not in keep]
        if dropped:
            reference = dict(reference)
            reference['decks'] = {k: v for k, v in decks.items() if k in keep}
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
        cands.append({
            'name': name, 'tier': tier,
            'source_name': d.get('source_name') or name,
            'shared': bool(d.get('shared')),
            'in_prefill_set': tier in (prefill_tiers or ()),
            'hits': hits, 'bayes': score, 'confidence': conf,
            'explained': hits, 'revealed': n,
            'sample': d.get('total'),
            'prefill': False,
        })
    sort_candidates(cands)
    if cands:
        top = cands[0]
        if top['in_prefill_set'] and prefill_ok(top['hits'], top['revealed'],
                                                top['confidence']):
            top['prefill'] = True
    return cands


def sort_candidates(cands, mode='hits'):
    """排序（原地 + 返回同一列表）。

    ``mode='hits'``（默认，用户口径）= 命中数优先、层内 bayes；
    ``mode='bayes_per_hit'`` = 研究里的对照打分器（回测拿来对比用）。
    """
    # tiebreak 里的 `not c.get('shared')`：同分时**共用参照数据的独立条目
    # 排在站内原条目前面**（狱神耀圣 > 耀圣，2026-10-11 用户拍板）。
    if mode == 'bayes_per_hit':
        key = lambda c: (-(c['bayes'] / c['hits']) if c['hits'] else 1e9,  # noqa: E731
                         -c['hits'], not c.get('shared'), c['name'])
    else:
        key = lambda c: (-c['hits'], -c['bayes'],                        # noqa: E731
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
