# -*- coding: utf-8 -*-
"""反推卡组的**参照库**抓取（反推计划 v2 · 刀 1）。

抓什么（masterduelmeta 的公开 JSON API，**手动按月跑一次**）：

  1. ``/api/v1/cards?fields=_id,gameId&limit=3000&skip=N``
     —— 站内卡 id（24 位十六进制）→ **游戏内卡 id**（日志里的 `opp_revealed_ids`）。
     单页上限 3000，翻页只能用 ``skip``（``start=`` 无效）。
  2. ``/api/v1/deck-types?name=<查名>&fields=name,deckBreakdown``
     —— 每个卡组的逐卡使用率：``cards[].per`` = 有百分之几的样本带这张卡。

**查名规则**：``name_display or name`` —— 覆盖表的「显示英文名」列就是干这个的
（实测 ``Yummy Engine`` → ``Mitsurugi Yummy``）。站内查不到、没有 breakdown、卡表为空
的条目**一律跳过并打印**，不进参照库（**空卡表不许进打分**）。

输出 ``data/mdm_deck_cards.json``（**gitignore**，可再生）::

    {"version": 1, "fetched_at": "...", "decks": {
        "Dracotail": {"tier": "Tier1", "source_name": "Dracotail",
                      "name_cn": "烙印星辰", "total": 25, "avgMainSize": 40,
                      "cards": {"20500": {"per": 100, "at": 3, "aboveThresh": true}}}},
     "skipped": [{"name": "Branded Elfnote", "query": "Branded Elfnote",
                  "reason": "site-not-found"}]}

用法::

    python tools/fetch_mdm_decks.py --all              # 全清单（~98 条，1-3 分钟）
    python tools/fetch_mdm_decks.py --deck Dracotail   # 单查（调试用）
    python tools/fetch_mdm_decks.py --all --dry-run    # 联网但只打印，不落盘

纪律：低频（每月一次）+ 串行 + 间隔（``--delay``，默认 0.3s）+ 本地缓存。
跟 `tools/fetch_meta_decks.py` 同一套节奏，**不做定时任务**。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import Storage                             # noqa: E402
from tools.fetch_meta_decks import fetch_api                 # noqa: E402

API_BASE = 'https://www.masterduelmeta.com/api/v1/'
CARDS_URL = API_BASE + 'cards'
DECKS_URL = API_BASE + 'deck-types'

#: 卡表 JSON 的版本号（结构变了就 +1，读的一侧据此兼容）
VERSION = 2

DEFAULT_OUT = os.path.join(PROJ, 'data', 'mdm_deck_cards.json')

#: 卡表页大小（服务端单页上限就是 3000）
PAGE = 3000


def query_name(row):
    """参照库要拿哪个名字去站内查 —— **显示英文名优先**，其次站内名。

    这条规则不是可选的：`Yummy Engine`（我们改名过的自定义条目）在站内叫
    `Mitsurugi Yummy`，拿显示名去查才有卡表。
    """
    return (row.get('name_display') or '').strip() or (row.get('name') or '').strip()


def fetch_cards_map(delay=0.3, page=PAGE, max_pages=200, verbose=True):
    """翻页拉全站卡表 → ``{站内 _id: 游戏内 gameId}``（值都是 int）。

    ★ BUG-B9A-08：`max_pages` 从 10 提到 **200**。站内卡表现在约 1.46 万条
    （10 页 × 3000 = 3 万刚好够），但每出一次大卡池就会涨 ——
    超过 3 万条时旧代码**静默截断**映射表，后果是某些卡按名字查不到 gameId
    （`Nx in P%` 那页直接少卡，且没有任何提示）。
    现在 200 页是**安全阀**而不是业务上限：正常靠「不满页就停」收尾
    （下面那行 `if len(rows) < page: break` 本来就在），
    真撞到 200 页说明有异常，会打一行告警。
    """
    out, skip = {}, 0
    for n in range(max_pages):
        rows = fetch_api({'fields': '_id,gameId', 'limit': page, 'skip': skip},
                         base=CARDS_URL)
        if not rows:
            break
        for r in rows:
            cid, gid = r.get('_id'), r.get('gameId')
            if not cid or gid in (None, ''):
                continue
            try:
                out[cid] = int(gid)
            except (TypeError, ValueError):
                continue
        if verbose:
            print('  cards 第 %d 页 +%d（累计 %d 条）' % (n + 1, len(rows), len(out)))
        if len(rows) < page:
            break
        skip += len(rows)
        time.sleep(delay)
    else:
        #: 翻完 max_pages 还没见到不满页 —— 异常（站内卡表不可能 60 万条），
        #: 响亮告警而不是静默返回半份映射表（BUG-B9A-08）
        print('⚠️ cards 翻页达到上限 %d 页仍未结束（累计 %d 条）—— '
              '映射表可能不完整，请检查站内接口' % (max_pages, len(out)))
    return out


def fetch_deck(query, cards_map):
    """按名字查一个卡组 → ``(deck_dict, None)`` 或 ``(None, 原因)``。"""
    rows = fetch_api({'name': query, 'fields': 'name,deckBreakdown'}, base=DECKS_URL)
    if not rows:
        return None, 'site-not-found'
    bd = (rows[0].get('deckBreakdown') or {})
    raw = bd.get('cards') or []
    if not raw:
        return None, 'no-breakdown'
    cards, unmapped = {}, 0
    for c in raw:
        gid = cards_map.get(c.get('card'))
        per = c.get('per')
        if gid is None or per is None:
            unmapped += 1
            continue
        try:
            per = int(per)
        except (TypeError, ValueError):
            unmapped += 1
            continue
        cards[str(gid)] = {'per': per, 'at': c.get('at'),
                           'aboveThresh': bool(c.get('aboveThresh'))}
    if not cards:
        return None, 'empty'
    return {'total': bd.get('total'),
            'avgMainSize': bd.get('avgMainSize'),
            'cards': cards,
            'unmapped_cards': unmapped}, None


#: 混轴（engine）端点 —— 站上「Core Engine Cards」的数据来源
ENGINES_URL = API_BASE + 'engines'
#: 我们清单里混轴行的后缀（`Elfnote Engine` → 站上叫 `Elfnote`）
ENGINE_SUFFIX = ' Engine'


def engine_query_names(row):
    """混轴行 → 站上 engine 的**候选查名**（按可能性排序，去重）。

    站上 engine 名 = 我们去掉 `` Engine`` 后缀的那个（``Elfnote Engine`` → ``Elfnote``）；
    另外把覆盖表里的**别名**也算上（``Branded Elfnote`` 的别名就是 ``Elfnote Engine``
    —— 用户 2026-09-11 把这条改名过，两条指向同一个引擎）。
    """
    out, seen = [], set()
    cands = [row.get('name_display'), row.get('name')]
    cands += list(row.get('aliases') or [])
    for c in cands:
        c = (c or '').strip()
        if not c:
            continue
        variants = []
        if c.endswith(ENGINE_SUFFIX):
            #: 站上 engine 名 = 我们去掉 ` Engine` 后缀的那个 —— **优先试它**
            variants.append(c[:-len(ENGINE_SUFFIX)].strip())
        variants.append(c)
        for v in variants:
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
    return out


def looks_like_engine(row) -> bool:
    """这一行像不像混轴 —— 决定直查失败后**要不要**再试 `/engines`。

    判据：``kind == 'engine'``，或者名字 / 别名里带 `` Engine`` 后缀
    （``Branded Elfnote`` 的别名就是 ``Elfnote Engine``，它其实也是混轴）。

    ⚠️ 不给普通卡组也试：那会让每一条查不到的普通卡组**多打一次 API**
    （低频手动跑，但没必要的请求还是不发）。
    """
    if (row.get('kind') or '') == 'engine':
        return True
    names = [row.get('name'), row.get('name_display')]
    names += list(row.get('aliases') or [])
    return any((n or '').strip().endswith(ENGINE_SUFFIX) for n in names)


def combine_engine_decks(per_decks):
    """把多个相关卡组的逐卡 `per` **合并成引擎卡表**（**纯函数**，可单测）。

    口径 = 站上「Core Engine Cards」的 ``NN%avg``：**每张卡在其出现的卡组上取平均**
    （不在某副牌里就不计入分母 —— 拿「全部相关卡组」当分母会把只在 1 副里 100%
    的卡压成 25%，与站上对不上，已实测）。

    返回 ``(cards, totals)``：``cards`` 形如 ``{卡id: {'per': 平均, 'at': 最大}}``,
    ``totals`` = 各卡组 ``(total, avgMainSize)`` 的原始列表（算样本数用）。
    """
    acc = {}
    for per_map in per_decks:
        for cid, info in (per_map or {}).items():
            per = info.get('per') if isinstance(info, dict) else info
            at = info.get('at') if isinstance(info, dict) else None
            if per is None:
                continue
            slot = acc.setdefault(str(cid), {'sum': 0.0, 'n': 0, 'at': None})
            slot['sum'] += float(per)
            slot['n'] += 1
            if at is not None:
                slot['at'] = max(slot['at'] or 0, int(at))
    cards = {cid: {'per': int(round(slot['sum'] / slot['n'])), 'at': slot['at'],
                   'aboveThresh': False}
             for cid, slot in acc.items() if slot['n']}
    return cards


def fetch_engine(candidates, cards_map, delay=0.3, verbose=True):
    """按候选查名找站上的 engine → 拉它的相关卡组 → 合并成一份卡表。

    返回 ``(payload, None)`` 或 ``(None, 原因)``；``payload`` 与普通卡组的同形
    （``total`` / ``avgMainSize`` / ``cards``），外加 ``engine_of`` = 相关卡组名列表。
    """
    hit = None
    for q in candidates:
        rows = fetch_api({'name': q, 'limit': 5}, base=ENGINES_URL)
        rows = rows if isinstance(rows, list) else ([rows] if rows else [])
        for r in rows:
            if (r.get('name') or '').strip().lower() == q.lower():
                hit = r
                break
        if hit:
            break
    if hit is None:
        return None, 'engine-not-found'

    related = [(d.get('name') or '').strip()
               for d in (hit.get('relatedDeckTypes') or [])]
    related = [n for n in related if n]
    if not related:
        return None, 'engine-no-decks'

    per_decks, totals, mains = [], [], []
    for nm in related:
        try:
            got, err = fetch_deck(nm, cards_map)
        except Exception as e:                               # noqa: BLE001
            got, err = None, 'fetch-error: %s' % e
        if err or not got:
            if verbose:
                print('        ⊘ 引擎子卡组 %-24s %s' % (nm, err or 'empty'))
            continue
        per_decks.append(got['cards'])
        if got.get('total'):
            totals.append(int(got['total']))
        if got.get('avgMainSize'):
            mains.append(int(got['avgMainSize']))
        if delay:
            time.sleep(delay)

    cards = combine_engine_decks(per_decks)
    if not cards:
        return None, 'engine-empty'
    return {'total': sum(totals) or None,
            'avgMainSize': (int(round(sum(mains) / float(len(mains))))
                            if mains else None),
            'cards': cards,
            'engine_of': related,
            'engine_name': hit.get('name')}, None


def build_reference(st, only=(), delay=0.3, verbose=True):
    """把 `meta_decks` **全清单**过一遍，产出参照库 payload。

    ``only`` 给名字列表时只查这几个（调试用；按站内名精确匹配）。
    """
    # ⚠️ hidden 的 **engine 行也要拉**（2026-09-21：混轴在站上是独立的
    #     端点，Tier 表会列出它们 → 点芯片必须能看到构成）。
    #    其余 hidden（手工隐藏的条目）照旧不拉。
    rows = [r for r in st.list_meta_decks(include_hidden=True)
            if not (r.get('hidden') and r.get('kind') != 'engine')]
    if only:
        want = set(only)
        rows = [r for r in rows if r.get('name') in want]
        for m in sorted(want - {r.get('name') for r in rows}):
            print('  ⊘ 清单里没有「%s」' % m)

    if verbose:
        print('参照库：清单 %d 条，开始拉 card id 映射……' % len(rows))
    cards_map = fetch_cards_map(delay=delay, verbose=verbose)
    if verbose:
        print('  card id 映射 %d 条\n' % len(cards_map))

    decks, skipped = {}, []
    for i, r in enumerate(rows, 1):
        q = query_name(r)
        try:
            got, err = fetch_deck(q, cards_map)
        except Exception as e:                              # noqa: BLE001
            got, err = None, 'fetch-error: %s' % e
        if err:
            # ★ 混轴（engine）：站上**没有** `<名> Engine` 这个 deck-type，
            #   数据在独立的 `/engines` 端点（「Core Engine Cards」）。
            #   直查失败就改走它 —— 否则点混轴芯片只会得到「参照库里没有这副牌」。
            eng_q = engine_query_names(r) if looks_like_engine(r) else []
            if eng_q:
                if verbose:
                    print('        ↻ 直查失败（%s），试 engine 端点：%s'
                          % (err, ' / '.join(eng_q[:2])))
                try:
                    got, err2 = fetch_engine(eng_q, cards_map, delay=delay,
                                             verbose=verbose)
                except Exception as e:                      # noqa: BLE001
                    got, err2 = None, 'engine-error: %s' % e
                if got:
                    err = None
                else:
                    err = '%s / %s' % (err, err2)
        if err:
            skipped.append({'name': r.get('name'), 'query': q, 'reason': err})
            print('  ⊘ [%3d/%d] %-28s 查名=%-24s %s'
                  % (i, len(rows), r.get('name'), q, err))
        else:
            got.update({'tier': r.get('tier'),
                        'source_name': got.get('engine_name') or q,
                        'name_cn': r.get('name_cn'),
                        'src': ('masterduelmeta/engine'
                                if got.get('engine_of') else 'masterduelmeta')})
            decks[r.get('name')] = got
            if verbose:
                print('  ✔ [%3d/%d] %-28s %s（样本 %s，卡位 %d%s）'
                      % (i, len(rows), r.get('name'), q, got.get('total'),
                         len(got['cards']),
                         '，未映射 %d' % got['unmapped_cards']
                         if got.get('unmapped_cards') else ''))
        if i < len(rows):
            time.sleep(delay)

    return {
        'version': VERSION,
        'fetched_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'source': 'masterduelmeta /api/v1',
        'decks': decks,
        'skipped': skipped,
    }


def write_reference(payload, path=DEFAULT_OUT):
    """落盘（幂等：同名文件直接覆盖）。**只走 tools**，core 不联网也不写数据。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')
    return path


def main(argv=None):
    try:                                              # GBK 管道打 ✔ 会崩（2026-10-12）
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        prog='fetch_mdm_decks.py',
        description='抓反推卡组的参照库（masterduelmeta 公开 API，手动按月跑）')
    ap.add_argument('--db', help='换个库（默认 data/masterduel.db）')
    ap.add_argument('--all', action='store_true', help='全清单（~98 条）')
    ap.add_argument('--deck', action='append', default=[], metavar='名',
                    help='只查这几个（可重复；站内名/中文名/别名都认）')
    ap.add_argument('--delay', type=float, default=0.3,
                    help='每个卡组之间的间隔秒数（默认 0.3）')
    ap.add_argument('--out', default=DEFAULT_OUT, help='输出路径')
    ap.add_argument('--dry-run', action='store_true', help='联网但只打印，不落盘')
    args = ap.parse_args(argv)

    if not args.all and not args.deck:
        ap.print_help()
        return 1

    st = Storage(args.db) if args.db else Storage()
    only = []
    for q in args.deck:                      # 中文名 / 别名也认
        hits = st.search_meta_decks(q, limit=1)
        if hits:
            only.append(hits[0]['name'])
        else:
            print('✘ 清单里找不到「%s」' % q)
            return 1

    payload = build_reference(st, only=only, delay=args.delay)
    n, sk = len(payload['decks']), payload['skipped']
    print('\n参照库：%d 条可用' % n)
    if sk:
        print('  跳过 %d 条：' % len(sk))
        for s in sk:
            print('    - %s（查名 %s）：%s' % (s['name'], s['query'], s['reason']))
    if args.dry_run:
        print('（--dry-run：没写文件）')
        return 0
    path = write_reference(payload, args.out)
    print('已写入 %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
