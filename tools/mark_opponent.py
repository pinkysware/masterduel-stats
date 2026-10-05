# -*- coding: utf-8 -*-
"""标注对局里对手的卡组 —— M5 的核心，也是本项目真正的立项理由。

Untapped 公开 API **没有**对手卡组（`opponent_revealed_deckstring` 恒为 null，
实测 0/1680 局）；唯一的线索是**对手打出的卡**（`opp_revealed`，只有日志解析
的局才有，API 补录的局是空的）。所以流程是：

    看本工具列出的「对手露出的卡」→ 猜一套 → 回游戏 / 记忆里确认 → 绑定

绑完 `matches.opp_deck_id / opp_deck_name` 才有值，`--cross` 才能给出
「我拿这套打谁胜率多少」—— 这是 Untapped 网站给不了的东西。

用法::

    python tools/mark_opponent.py --list                # 待标注队列（默认 20 条）
    python tools/mark_opponent.py --list 100            # 看全部
    python tools/mark_opponent.py --list --marked       # 已经标过的（撤销前先看这个）
    python tools/mark_opponent.py --set 3 烙印           # 把第 3 条绑到「烙印」
    python tools/mark_opponent.py --set 7a3f 珠泪         # 也可以用 match_id 前缀
    python tools/mark_opponent.py --set 3 烙印 --note 先手两回合被展开
    python tools/mark_opponent.py --set 3 --clear        # 撤销第 3 条的标注
    python tools/mark_opponent.py --set 9 自研卡组 --new   # 清单里没有 -> 新建一条
    python tools/mark_opponent.py --interactive         # 逐局标注（回车跳过 / q 退出）
    python tools/mark_opponent.py --cross               # 我 × 对手 战绩交叉表
    python tools/mark_opponent.py --decks               # 我的对手卡组表（按打过几局排序）

卡组名单来自 `meta_decks`（`tools/fetch_meta_decks.py` 按月抓一次），
名字匹配走 `Storage.search_meta_decks()` —— 别名 / 大小写 / 标点 / 拼错都能命中
（输「珠泪」命中「泪冠哀歌」，输「对击斗魂」命中 K9VS）。
"""

import argparse
import collections
import json
import os
import sys
import unicodedata

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.card_names import LANGS, is_known, load as load_card_names  # noqa: E402
from core import deck_guess as dg                      # noqa: E402
from core.storage import Storage        # noqa: E402

#: 显示卡名用哪种语言（`--lang` 覆盖；跟匹配完全无关）
LANG = 'cn'

#: 队列里每局最多列几张对手露出的卡（再多也看不出所以然，还刷屏）
MAX_REVEAL_SHOWN = 10

#: `--guess` 默认打印几条候选
GUESS_TOP = 3

#: 参照库路径（`tools/fetch_mdm_decks.py` 的产物；测试会覆盖它）
REFERENCE_PATH = dg.DEFAULT_REFERENCE

#: 回测**剔除清单**（写死；逐局打印原因）—— 见 docs/反推卡组计划.md §三.5
EXCLUDED_MATCHES = {
    'dd7650f9d39b5c39': '露出卡被上一局污染（2026-09-14 核实；标签本身是对的）',
}

#: 这些标签**没有单一真身**，不能当回测真值
GENERIC_LABELS = ('混轴卡组',)


# ---------------------------------------------------------------- 显示
def fmt_time(ts):
    """'2026-09-10 23:13:18.837' -> '09-10 23:13'。"""
    return ts[5:16] if ts else '??-?? ??:??'


def fmt_result(res):
    return {'Win': '胜', 'Lose': '负', 'Draw': '平'}.get(res, res or '?')


def pct(w, n, extra=''):
    """胜率字符串，0 场不算（返回空串）。"""
    return '%.1f%%%s' % (100.0 * w / n, extra) if n else extra.lstrip()


def fmt_revealed(rev):
    """对手露出的卡压成一行。"""
    rev = rev or []
    if not rev:
        return '（无记录 —— API 补录的局没有这个字段，只能靠记忆 / 回游戏看）'
    shown = '、'.join(rev[:MAX_REVEAL_SHOWN])
    if len(rev) > MAX_REVEAL_SHOWN:
        shown += ' …等共 %d 张' % len(rev)
    return shown


def fmt_revealed_ids(ids, snapshot=None, names=None):
    """按当前语言把「对手露出的卡 id」翻成卡名（`--lang` 切语言）。

    一层层退：目标语言的卡名 → 库里那份中文快照（跟 ids **同序同长**）→ `#id`。
    绝不吞信息。

    ⚠️ 卡表里**查不到**的 id 会标成 `#<id>（未知）`（M4：这是**卡表过期**的信号，
    跟「只是没翻译出来」是两回事 —— 跑 `tools/build_card_names.py --check-update`）。
    """
    if not ids:
        return None
    table = names if names is not None else load_card_names()
    out = []
    for i, cid in enumerate(ids[:MAX_REVEAL_SHOWN]):
        name = None
        if table:
            from core.card_names import display
            name = display(cid, LANG, table)
        if not name and snapshot and i < len(snapshot):
            name = snapshot[i]
        if name:
            out.append(name)
            continue
        # 卡表里查不到 -> 显式标出来（别让人以为只是「这个名字没翻出来」）
        mark = '' if (table and is_known(cid, table)) else '（未知）'
        out.append('#%s%s' % (cid, mark))
    tail = '' if len(ids) <= MAX_REVEAL_SHOWN else ' …等共 %d 张' % len(ids)
    return '、'.join(out) + tail


def fmt_match(row, idx=None):
    """一局的抬头行。"""
    return '%s%s  %s  我方 %s  对手 %s  #%s' % (
        '[%2d] ' % idx if idx is not None else '',
        fmt_time(row.get('started_at')),
        fmt_result(row.get('result')),
        row.get('my_deck_name') or '(未识别)',
        row.get('opp_player_name') or '(对手名未知)',
        (row.get('match_id') or '')[:8])


def show_queue(rows, marked=False):
    """打印队列：待标注（默认）或已标注（撤销时看）。"""
    if not rows:
        print('已标注队列是空的 —— 还没标过任何一局。' if marked
              else '待标注队列是空的 —— 全标完了。跑 --cross 看战绩。')
        return
    print('%s %d 局（最新在最上面）' % ('已标注' if marked else '待标注', len(rows)))
    print('-' * 76)
    for i, r in enumerate(rows, 1):
        line = fmt_match(r, i)
        if marked:
            line += '  → %s' % (r.get('opp_deck_name') or '(空)')
        print(line)
        if not marked:
            print('     露出：' + (fmt_revealed_ids(r.get('opp_revealed_ids'),
                                                    r.get('opp_revealed'))
                                    or fmt_revealed(r.get('opp_revealed'))))


# ---------------------------------------------------------------- 解析
def match_by_token(rows, token):
    """把 ``--set`` 的目标解析成一行。返回 ``(row, err)``。

    纯数字且 ≤3 位 = 上面那份列表的**序号**；其余按 ``match_id`` 前缀唯一匹配。
    """
    tok = (token or '').strip()
    if not tok:
        return None, '没给序号 / match_id'
    if tok.isdigit() and len(tok) <= 3:
        idx = int(tok)
        if 1 <= idx <= len(rows):
            return rows[idx - 1], None
        return None, '序号 %d 越界（这份列表只有 %d 条，先跑 --list 看一眼）' % (
            idx, len(rows))
    hits = [r for r in rows if (r.get('match_id') or '').startswith(tok)]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, '这份列表里没有 match_id 以 %s 开头的局' % tok
    return None, 'match_id 前缀 %s 撞了 %d 局，写长一点' % (tok, len(hits))


def deck_display(deck):
    """对手卡组进统计时用的显示名：中文名 > 显示英文名 > 站内名。"""
    return deck.get('name_cn') or deck.get('name_display') or deck['name']


def resolve_deck(st, query):
    """把用户敲的名字解析成**唯一**一条 meta_decks 记录。

    返回 ``(deck, err)`` —— 一条都匹配不到、或匹配到多条，都算 err（附候选）。
    先搜可见条目；搜不到再带上隐藏条目（engine 那类手工隐藏的允许手输）。
    """
    q = (query or '').strip()
    if not q:
        return None, '卡组名是空的'
    hits = st.search_meta_decks(q, limit=5)
    if not hits:
        hits = st.search_meta_decks(q, limit=5, include_hidden=True)
    if not hits:
        return None, '卡组清单里没有「%s」' % q
    exact = [h for h in hits
             if q in (h.get('name'), h.get('name_display'), h.get('name_cn'))]
    if len(exact) == 1:
        return exact[0], None
    if len(hits) == 1:
        return hits[0], None
    return None, '「%s」匹配到多条，写清楚点：%s' % (
        q, '、'.join('%s（%s）' % (h['name'], h.get('name_cn') or '-')
                     for h in hits))


def bind(st, row, name, note=None, allow_new=False):
    """把一局绑到卡组 `name`。返回 ``(ok, msg)``。"""
    mid = row['match_id']
    deck, err = resolve_deck(st, name)
    if err:
        if not allow_new:
            return False, err + '（要当新卡组建，加 --new；或在交互模式里用 +名字）'
        new_name = name.strip()
        oid = st.upsert_opp_deck(new_name)
        st.mark_opp_deck(mid, opp_deck_id=oid, name=new_name, note=note)
        return True, '新建了自己的对手卡组「%s」，并绑到 %s' % (new_name, fmt_match(row))
    disp = deck_display(deck)
    oid = st.upsert_opp_deck(deck['name'], aliases=deck.get('aliases'))
    st.mark_opp_deck(mid, opp_deck_id=oid, name=disp, note=note)
    return True, '已绑定 %s -> %s' % (fmt_match(row), disp)


# ---------------------------------------------------------------- 命令
def cmd_cross(st, since=None):
    """我 × 对手 的交叉战绩（本工具存在的意义）。"""
    rows = st.get_cross_stats(since=since)
    if not rows:
        print('库里没有符合统计口径的已结束对局。')
        return
    groups = {}
    for my, opp, n, w, l, d in rows:
        groups.setdefault(my, []).append((opp, n, w, l, d))
    total = sum(r[2] for r in rows)
    marked = sum(r[2] for r in rows if r[1] != '(未标注)')
    print('我 × 对手 交叉战绩%s —— 统计口径：排名决斗 + 巅峰决斗'
          % ('（自 %s 起）' % since if since else ''))
    print('共 %d 场：已标注 %d / 未标注 %d' % (total, marked, total - marked))
    if not marked:
        print('（一条都还没标 —— 先跑 --interactive）')
        return
    order = sorted(groups, key=lambda k: -sum(x[1] for x in groups[k]))
    for my in order:
        items = groups[my]
        n_all = sum(x[1] for x in items)
        w_all = sum(x[2] for x in items)
        l_all = sum(x[3] for x in items)
        print()
        print('=== 我方 %s —— %d 场 %d 胜 %d 负（%s）'
              % (my, n_all, w_all, l_all, pct(w_all, n_all)))
        for opp, n, w, l, d in items:
            tail = '  平 %d' % d if d else ''
            print('    - %s：%d 场 %d 胜 %d 负%s  胜率 %s'
                  % (opp, n, w, l, tail, pct(w, n)))


def cmd_decks(st):
    """我自己的对手卡组表（按打过几局排序）。"""
    rows = st.list_opp_decks()
    if not rows:
        print('还没标注过任何对手卡组 —— 跑 --interactive。')
        return
    print('我的对手卡组表（%d 条，按打过几局排序）' % len(rows))
    for oid, name, aliases, n in rows:
        print('  [%3d] %-3d 局  %s%s'
              % (oid, n, name,
                 '   别名：' + '/'.join(aliases) if aliases else ''))


# ---------------------------------------------------------------- 反推卡组（M5b）
def load_guesser(path=None):
    """读参照库 → ``(索引, 名字组)``；没有参照库返回 ``(None, None)``。

    实现已经搬进 `core.deck_guess.load_index()`（**M7 UI 不该 import tools/**），
    这里留个薄壳给 CLI 用。
    """
    return dg.load_index(path or REFERENCE_PATH)


def reveal_ids(row):
    """把 `opp_revealed_ids`（JSON 列）解析成 int 列表；坏数据当没有。"""
    return dg.revealed_ids(row)


def deck_display_map(st, include_hidden=True):
    """站内名 → 显示名（中文名 > 显示英文名 > 站内名）—— 薄壳，实现在 Storage。"""
    return st.meta_deck_display_map(include_hidden=include_hidden)


def fmt_deck(name, names=None):
    """`雷火剑（Ryzeal Mitsurugi）`—— 中文名优先，站内名不同才附在后面。"""
    disp = disp_name(name, names)
    return disp if disp == name else '%s（%s）' % (disp, name)


def disp_name(name, names=None):
    """只给显示名（中文名优先）—— 候选表已经带过站内名了，判语里别重复括号。"""
    return (names or {}).get(name) or name


def _width(s):
    """终端显示宽度（汉字/全角算 2 列）—— 不然候选表会被中文顶歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
               for ch in s)


def _pad(s, n):
    return s + ' ' * max(0, n - _width(s))


def match_any_by_token(st, token):
    """`--guess` 的目标：**数字 = 待标注队列序号**，其余 = match_id 前缀（全库）。

    跟 `--set` 的差别：`--set` 只在待标注队列里找，而反推要能指到**任意一局**
    （不在统计口径里的局根本不进队列，见反推计划 §四 P2-4）。

    另有 ``last``（也认「最新」）—— **库里最新一局有露出卡的**，专门给
    「看完回放马上验一把」用：回放是 `mode='live'`、`game_mode` 为空，
    **不进待标注队列**，只能这么指。
    """
    tok = (token or '').strip()
    if not tok:
        return None, '没给序号 / match_id'
    if tok.lower() == 'last' or tok in ('最新', '最新一局'):
        rows = [r for r in st.list_matches(limit=None) if reveal_ids(r)]
        if not rows:
            return None, '库里还没有带露出卡的对局'
        return rows[-1], None                  # list_matches 是升序，最后一条最新
    if tok.isdigit() and len(tok) <= 3:
        return match_by_token(st.get_unmarked_matches(1000), tok)
    hits = [r for r in st.list_matches(limit=None)
            if (r.get('match_id') or '').startswith(tok)]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, '库里没有 match_id 以 %s 开头的局' % tok
    return None, 'match_id 前缀 %s 撞了 %d 局，写长一点' % (tok, len(hits))


def fmt_candidates(cands, limit=GUESS_TOP, names=None):
    """候选表。**口径外**的显式标出来（那种只显示、不预填）。"""
    if not cands:
        return '  （没有候选）'
    out = []
    for i, c in enumerate(cands if limit is None else cands[:limit], 1):
        tag = '' if c['in_prefill_set'] else '（预填口径外）'
        mark = '  ← 建议' if c.get('prefill') else ''
        out.append('  %d) %s [%s]%s  命中 %d/%d（%.0f%%）%s'
                   % (i, _pad(fmt_deck(c['name'], names), 28),
                      c['tier'] or '未分级', tag,
                      c['hits'], c['revealed'], 100 * c['confidence'], mark))
    return '\n'.join(out)


def suggest_for(row, index, groups):
    """一局的候选（没参照库 / 没露出卡 → 空列表）。"""
    return dg.suggest(row, index, groups)


def _verdict(cands, names=None):
    """一句话结论 —— **别把预填说成「已确定」**。"""
    if not cands:
        return '没有建议（参照库是空的？跑 tools/fetch_mdm_decks.py --all）。'
    top = cands[0]
    if top['prefill']:
        return '建议：%s（命中 %d/%d，置信度 %.0f%%）—— 回车即可采纳。' % (
            disp_name(top['name'], names), top['hits'], top['revealed'],
            100 * top['confidence'])
    if not top['in_prefill_set']:
        return ('最像的是「%s」（%s，**预填口径外**）—— 只列不填，请手标。'
                % (disp_name(top['name'], names), top['tier'] or '未分级'))
    miss = []
    if top['hits'] < dg.MIN_HITS:
        miss.append('命中 %d < %d' % (top['hits'], dg.MIN_HITS))
    if top['revealed'] < dg.MIN_REVEALED:
        miss.append('露出 %d < %d' % (top['revealed'], dg.MIN_REVEALED))
    if top['confidence'] < dg.MIN_CONFIDENCE:
        miss.append('置信度 %.0f%% < %.0f%%'
                    % (100 * top['confidence'], 100 * dg.MIN_CONFIDENCE))
    return '证据不足（%s）—— 只列不填，请手标。' % '，'.join(miss)


def cmd_guess(st, token, index, groups, limit=GUESS_TOP):
    """`--guess <序号|match_id 前缀>`：打印一局的候选。"""
    row, err = match_any_by_token(st, token)
    if err:
        print('✘ ' + err)
        return 1
    print(fmt_match(row))
    print('露出：' + (fmt_revealed_ids(row.get('opp_revealed_ids'),
                                      row.get('opp_revealed'))
                      or fmt_revealed(row.get('opp_revealed'))))
    ids = reveal_ids(row)
    if not ids:
        print('这局没有「对手露出的卡」（API 补录局）→ 反推帮不上，手标。')
        return 0
    if index is None:
        print('没有参照库 —— 先跑：python tools/fetch_mdm_decks.py --all')
        return 1
    cands = dg.guess_deck(ids, index, groups)
    names = deck_display_map(st)
    known, unknown = dg.split_unknown(ids, groups)
    print()
    print('候选（主排序 = 命中数，层内 = bayes）：')
    print(fmt_candidates(cands, limit=limit, names=names))
    if unknown:
        print('  ⚠ 露出里 %d 张查不到名字（卡表过期？跑 '
              'tools/build_card_names.py --check-update）：%s'
              % (len(unknown), '、'.join('#%s' % u for u in unknown)))
    print()
    print(_verdict(cands, names))
    return 0


def cmd_backtest(st, index, groups, limit=None):
    """`--guess --backtest`：已标注局全量回测（四分组 + 剔除原因 + 打分器对照）。"""
    if index is None:
        print('没有参照库 —— 先跑：python tools/fetch_mdm_decks.py --all')
        return 1
    rows = [r for r in st.list_matches(limit=limit)
            if r.get('opp_deck_id') and reveal_ids(r)]
    if not rows:
        print('没有「已标注 + 有露出卡」的局可回测。')
        return 0

    # 体检扫**全库有露出卡的局**（不只已标注的）—— 串味会影响将来所有反推，
    # 未标注的局照样要摆出来（例：09-13 12:05 就不是已标注局）。
    all_rows = [r for r in st.list_matches(limit=limit) if reveal_ids(r)]
    ids_rows = [{'match_id': r['match_id'], 'ids': reveal_ids(r)} for r in all_rows]
    residue = dg.find_tail_residue(ids_rows)
    if residue:
        print('⚠ 露出卡体检：发现 %d 处「新局开头 = 上一局结尾」残留'
              '（`parse_seg` 回扫区的历史遗留，2026-09-14 已修 `648d146`；'
              '全库 %d 局有露出卡）：' % (len(residue), len(all_rows)))
        for h in residue:
            labeled = any(r['match_id'] == h['match_id'] for r in rows)
            print('    %s → %s：前 %d 张 %s%s'
                  % (h['prev_match_id'][:8], h['match_id'][:8], h['k'],
                     '、'.join(dg.group_key(c, groups) for c in h['cards']),
                     '' if labeled else '（未标注局，不进回测分母）'))
        print()

    print('回测：%d 局（已标注 + 有露出卡）\n' % len(rows))
    names = deck_display_map(st)
    stat = collections.OrderedDict()
    for k in ('候选内', '口径外', '清单外', '剔除'):
        stat[k] = {'n': 0, 'hit': 0, 'nopre': 0, 'list': []}
    other = {'n': 0, 'hit': 0}          # bayes/n 主排序对照

    for r in rows:
        label = r.get('opp_deck_name') or ''
        ids = reveal_ids(r)
        reason = EXCLUDED_MATCHES.get(r['match_id'])
        truth, terr = (None, None)
        if label and label not in GENERIC_LABELS:
            deck, terr = resolve_deck(st, label)
            truth = deck['name'] if deck else None
        if reason is None and label in GENERIC_LABELS:
            reason = '无单一真身（混轴）'

        cands = dg.guess_deck(ids, index, groups)
        top = cands[0] if cands else None
        cands_b = dg.sort_candidates(list(cands), mode='bayes_per_hit')
        top_b = cands_b[0] if cands_b else None

        if reason:
            key = '剔除'
        elif truth is None:
            key = '清单外'
        elif cands and _tier_of(st, truth, index) in dg.PRE_FILL_TIERS:
            key = '候选内'
        else:
            key = '口径外'

        hit = bool(truth and top and top['name'] == truth)
        stat[key]['n'] += 1
        if key != '剔除':
            other['n'] += 1
            if truth and top_b and top_b['name'] == truth:
                other['hit'] += 1
        if hit:
            stat[key]['hit'] += 1
        if top and not top['prefill']:
            stat[key]['nopre'] += 1

        line = '%s  %-12s → 真身 %s | top1 %s（命中 %d/%d）%s' % (
            r['started_at'][5:16], label or '(空)',
            fmt_deck(truth, names) if truth else '？',
            fmt_deck(top['name'], names) if top else '—',
            top['hits'] if top else 0, top['revealed'] if top else 0,
            '✓' if hit else ('✗' if key != '剔除' else ''))
        stat[key]['list'].append('%s%s' % (line, '  [剔除：%s]' % reason if reason else ''))

    for key in ('候选内', '口径外', '清单外'):
        d = stat[key]
        for ln in d['list']:
            print('  ' + ln)
    if stat['剔除']['list']:
        print()
        for ln in stat['剔除']['list']:
            print('  ' + ln)
    print()
    for key, d in stat.items():
        if not d['n']:
            continue
        if key == '剔除':
            print('  %s：%d 局（不进分母）' % (key, d['n']))
        else:
            print('  %s：%d 局，top1 命中 %d（%s），其中 %d 局被闸门拦（不预填）'
                  % (key, d['n'], d['hit'], pct(d['hit'], d['n']), d['nopre']))
    print()
    print('  对照：命中数主排序 top1 命中 %d/%d；bayes/n 主排序 %d/%d'
          % (sum(d['hit'] for k, d in stat.items() if k != '剔除'), other['n'],
             other['hit'], other['n']))
    return 0


def _tier_of(st, meta_name, index):
    """候选的 tier（拿不准就按 meta 清单查）。"""
    if meta_name in index:
        return index[meta_name].get('tier')
    deck = st.get_meta_deck(meta_name)
    return (deck or {}).get('tier')


def cmd_interactive(st, index=None, groups=None):
    """逐局标注：边看对手露出的卡边挑（有参照库时顺带给候选 + 预填）。

    分支表**写死**（见 docs/反推卡组计划.md §四 P2-4）：

    * 回车 —— **有建议**就采纳它；**没建议**就跳过（**原语义不变**）；
    * `1`~`3` —— 采纳第 N 条候选（口径外的也能采纳：手选就是明确的）；
    * `?`（裸）—— 看全部候选；`?词` —— 搜卡组（**原语义**）；
    * `+名字` —— 新建；名字 / 别名 —— 绑定；`q` —— 退出（都是原语义）。
    """
    rows = st.get_unmarked_matches(limit=1000)
    if not rows:
        print('待标注队列是空的 —— 全标完了。')
        return 0
    hint = ('回车 = 采纳建议 / 跳过，q = 退出，? = 看全部候选，?词 = 搜卡组，'
            '+名字 = 新建卡组。' if index is not None else
            '回车 = 跳过，q = 退出，?词 = 搜卡组，+名字 = 新建卡组。')
    print('共 %d 局待标注。%s' % (len(rows), hint))
    names = deck_display_map(st) if index is not None else {}
    done = 0
    for i, row in enumerate(rows, 1):
        print()
        print('---- [%d/%d] ----' % (i, len(rows)))
        print(fmt_match(row))
        print('露出：' + (fmt_revealed_ids(row.get('opp_revealed_ids'),
                                          row.get('opp_revealed'))
                          or fmt_revealed(row.get('opp_revealed'))))
        cands = suggest_for(row, index, groups)
        prefill = next((c for c in cands if c.get('prefill')), None)
        if cands:
            print('候选：')
            print(fmt_candidates(cands, limit=GUESS_TOP, names=names))
            print('  （回车 = 采纳「%s」）' % disp_name(prefill['name'], names)
                  if prefill else '  （%s）' % _verdict(cands, names))
        while True:
            try:
                ans = input('卡组名 > ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print('退出。本次标了 %d 局。' % done)
                return 0
            if ans.lower() in ('q', 'quit', 'exit'):
                print('退出。本次标了 %d 局。' % done)
                return 0
            if not ans:
                if prefill:                 # 空回车 = 采纳建议（**只在有建议时**）
                    ok, msg = bind(st, row, prefill['name'])
                    print(('  ✔ ' if ok else '  ✘ ') + msg)
                    if ok:
                        done += 1
                        break
                    continue
                break                       # 没建议 -> 跳过（原语义）
            if ans == '?':                  # 裸 ? = 看全部候选（含口径外）
                print(fmt_candidates(cands, limit=None))
                continue
            if ans.startswith('?'):
                for h in st.search_meta_decks(ans[1:].strip(), limit=8):
                    print('    %s（%s）  %s'
                          % (h['name'], h.get('name_cn') or '-',
                             h.get('tier') or '未分级'))
                continue
            if ans.isdigit() and 1 <= int(ans) <= len(cands):
                ok, msg = bind(st, row, cands[int(ans) - 1]['name'])
                print(('  ✔ ' if ok else '  ✘ ') + msg)
                if ok:
                    done += 1
                    break
                continue
            allow_new = ans.startswith('+')
            ok, msg = bind(st, row, ans[1:] if allow_new else ans,
                           allow_new=allow_new)
            print(('  ✔ ' if ok else '  ✘ ') + msg)
            if ok:
                done += 1
                break
    print()
    print('队列走完了。本次标了 %d 局。' % done)
    return 0


EPILOG = """\
例子：
  --list 100                 看全部待标注的局
  --list --marked            看已经标过的局（撤销前先看这个）
  --set 3 烙印                把待标注列表第 3 条绑到「烙印」
  --set 3 --clear            撤销待标注列表第 3 条的标注
  --set 7a3f 珠泪 --note 掉线  按 match_id 前缀绑，带备注
  --interactive              逐局标注
  --guess 3                  猜第 3 局的对手卡组（序号 = 待标注队列；也可给 match_id 前缀）
  --guess last               猜**最新一局**（看完回放立刻验用；回放不进待标注队列）
  --guess 7a3f --backtest    全量回测（已标注局 top1 命中率，四分组 + 剔除原因）
  --cross                    我 × 对手 交叉战绩
  --decks                    我的对手卡组表
  --unknown-before 2026-09-06  把该日期之前的待标注局全标成「未知卡组」
                             （游戏内记录已过期、永远查不回来；配 --dry-run 先看）
"""


def cmd_unknown_before(st, before, dry_run=False):
    """把 `before` **之前**的待标注局全部标成「未知卡组」（用户 2026-09-17 口径）。

    用途：M5 反推要靠**对手露出的卡**，而那只有近期对局才有
    （日志会轮转、游戏内对战记录也只有最近一段）。更早的局**永远查不回来** ——
    与其让它们永远挂在待标注队列里当噪声（「还剩多少没标」永远不等于 0），
    不如明确记成「未知卡组」。

    ⚠️ **只动 `started_at < before` 且未标注的局** —— 不碰更新的局，
    也不碰已标注的（不会覆盖真名）。
    ⚠️ 「未知卡组」与「混轴卡组」是**两个独立条目**（混轴只是统称，
    用户之后会用自定义卡组名替换成具体名字），这里只写前者。
    """
    from core import ui_actions
    rows = [r for r in st.get_unmarked_matches(1000)
            if str(r.get('started_at') or '') < before]
    if not rows:
        print('没有 %s 之前的待标注局（不用动）' % before)
        return 0
    print('%s 之前的待标注局 %d 条：' % (before, len(rows)))
    for r in rows:
        print('   %s' % fmt_match(r))
    if dry_run:
        print('\n[dry-run] 会把上面这些标成「未知卡组」；去掉 --dry-run 才写库')
        return 0
    done, failed = 0, 0
    for r in rows:
        ok, _msg = ui_actions.mark_unknown(st, r['match_id'],
                                           note='游戏内记录已过期')
        done += 1 if ok else 0
        failed += 0 if ok else 1
    print('\n已标「未知卡组」%d 条%s'
          % (done, ('，失败 %d 条' % failed) if failed else ''))
    print('⚠️ 它们挂在一个叫「未知卡组」的条目下 —— 那**不是真卡组**，'
          '看「我×对手」交叉战绩时记得跳过它')
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='mark_opponent.py',
        description='标注对局里对手的卡组（M5）—— 补上 Untapped 没有的那一半数据',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG)
    ap.add_argument('--list', nargs='?', const=20, type=int, metavar='N',
                    help='打印队列（默认 20 条；配 --marked 看已标注的）')
    ap.add_argument('--marked', action='store_true',
                    help='把 --list 切到「已标注」队列')
    ap.add_argument('--set', nargs='+', metavar='目标 [卡组名]',
                    help='绑定一局：目标 = 列表序号或 match_id 前缀')
    ap.add_argument('--clear', action='store_true',
                    help='配合 --set：撤销这局的标注')
    ap.add_argument('--note', help='配合 --set：备注（如「掉线判负」）')
    ap.add_argument('--new', action='store_true',
                    help='配合 --set：清单里没有这个名字 -> 新建一条自己的对手卡组')
    ap.add_argument('--unknown-before', metavar='YYYY-MM-DD',
                    help='把该日期**之前**的待标注局全部标成「未知卡组」'
                         '（游戏内记录已过期、永远查不回来；先加 --dry-run 看范围）')
    ap.add_argument('--dry-run', action='store_true',
                    help='配合 --unknown-before：只打印将要标哪些局，不写库')
    ap.add_argument('--interactive', action='store_true', help='逐局标注')
    ap.add_argument('--guess', nargs='?', const='', metavar='目标',
                    help='猜卡组：目标 = 待标注队列序号 或 match_id 前缀'
                         '（配 --backtest 时可不给）')
    ap.add_argument('--backtest', action='store_true',
                    help='配合 --guess：已标注局全量回测')
    ap.add_argument('--reference', metavar='路径',
                    help='参照库路径（默认 data/mdm_deck_cards.json）')
    ap.add_argument('--cross', action='store_true', help='我 × 对手 交叉战绩')
    ap.add_argument('--decks', action='store_true', help='我的对手卡组表')
    ap.add_argument('--since', metavar='YYYY-MM-DD',
                    help='--cross 只算这天起（含当天）')
    ap.add_argument('--lang', choices=list(LANGS), default='cn',
                    help='卡名显示语言：cn 中文（默认）/ en 英文 / jp 日文')
    ap.add_argument('--db', help='换个库（默认 data/masterduel.db）')
    args = ap.parse_args(argv)

    global LANG
    LANG = args.lang
    if (args.list is None and not args.set and not args.interactive
            and not args.cross and not args.decks
            and not args.unknown_before
            and args.guess is None and not args.backtest):
        ap.print_help()
        return 1

    st = Storage(args.db) if args.db else Storage()
    rc = 0
    if args.unknown_before:
        rc = cmd_unknown_before(st, args.unknown_before, args.dry_run) or rc
    if args.set:
        token, rest = args.set[0], args.set[1:]
        if args.clear:
            rows = st.get_marked_matches(50)
            row, err = match_by_token(rows, token)
            if err:
                print('✘ ' + err)
                return 1
            st.clear_opp_deck(row['match_id'])
            print('已撤销：%s' % fmt_match(row))
        else:
            deck_name = ' '.join(rest).strip()
            if not deck_name:
                print('✘ --set 需要「目标 + 卡组名」，比如 --set 3 烙印')
                return 1
            rows = st.get_unmarked_matches(1000)
            row, err = match_by_token(rows, token)
            if err:
                print('✘ ' + err)
                return 1
            ok, msg = bind(st, row, deck_name, note=args.note,
                           allow_new=args.new)
            print(('✔ ' if ok else '✘ ') + msg)
            rc = 0 if ok else 1
    if args.list is not None:
        rows = (st.get_marked_matches(args.list) if args.marked
                else st.get_unmarked_matches(args.list))
        show_queue(rows, marked=args.marked)
    if args.decks:
        cmd_decks(st)
    if args.cross:
        cmd_cross(st, since=args.since)
    if args.guess is not None or args.backtest:
        index, groups = load_guesser(args.reference)
        if args.backtest:
            rc = cmd_backtest(st, index, groups) or rc
        elif not args.guess:
            print('✘ --guess 需要目标（序号或 match_id 前缀）；只跑回测请加 --backtest')
            rc = 1
        else:
            rc = cmd_guess(st, args.guess, index, groups) or rc
    if args.interactive:
        index, groups = load_guesser(args.reference)
        rc = cmd_interactive(st, index, groups) or rc
    return rc


if __name__ == '__main__':
    sys.exit(main())
