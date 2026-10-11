# -*- coding: utf-8 -*-
"""按 ygocdb 五套中文字段推定卡组的**中文名 + 别名**，预填进覆盖表。

老办法（AGENTS.md 铁律 7，与 `build_deck_names.py` 的日文推定同一套）：

  1. 用卡组的**站内英文名**去 `ygocdb.com.cards/cards.json` 里找它的卡
     （`en_name` 以它开头）
  2. 每套中文字段（md/sc/cn/nwbbs/cnocg）取「覆盖率 >=60% 的最长前缀」
  3. **中文名 = md_name**（游戏内译名口径；不够线退 sc_name，再退五字段最高分），
     **别名 = 其余字段的叫法**（去重；与覆盖表现有格式一致，实测样例：
     Exosister -> 中文名 驱魔姐妹、别名 救祓少女/驱魔修女）

推不出线的**留空等人填** —— 混轴（Dragon Link / Elfnote White Forest）和
机制名（Stun）没有统一字段；混轴走「字段拼接」兜底（拆英文段各推字段名再拼，
用户 2026-09-16 口径），推不出段的照样放弃，宁缺毋滥。

用法::

    python tools/prefill_deck_names.py                        # 库里缺中文名的站内条目
    python tools/prefill_deck_names.py --only DoomZ Invoked   # 只看/只写这几个
    python tools/prefill_deck_names.py --write                # 追加进覆盖表（已有名字跳过）
"""

import argparse
import os
import sys
from collections import Counter

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import deck_csv                                 # noqa: E402
from core.storage import Storage                          # noqa: E402

YGOCDB = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')
CSV = os.path.join(PROJ, 'data', 'deck_name_cn.csv')

#: 中文字段与优先级（md = 游戏内译名，中文名首选）
FIELDS = ('md_name', 'sc_name', 'cn_name', 'nwbbs_n', 'cnocg_n')
FIELD_LABEL = {'md_name': 'md', 'sc_name': 'sc', 'cn_name': 'cn',
               'nwbbs_n': 'nw', 'cnocg_n': 'ocg'}

#: 卡名里的分隔符 —— 字段名基本在它前面
SEP = '・:：－-‐−～~＜＞<>「」『』【】（）《》〈〉／/｜|·—　 '
#: 脏尾巴（编号/星星/「号」）—— 字段名不以这些收尾
DIRTY = '★☆0123456789０１２３４５６７８９号'

MIN_CARDS = 3     # 命中卡少于这么多张不下结论
MIN_HITS = 3      # 候选前缀至少要这么多张卡撑
MIN_COV = 0.6     # 前缀至少覆盖这么多比例的卡
LOOSE_COV = 0.4   # 放松线（与日文推定同源；取「>=40% 里最长」的那个）


def load_cards(path=YGOCDB):
    """`[(小写英文名, {字段: 值})]`；文件不在返回空表。"""
    import json
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = []
    for rec in (raw.values() if isinstance(raw, dict) else raw):
        en = (rec.get('en_name') or '').strip()
        if not en:
            continue
        out.append((en.lower(),
                    {f: (rec.get(f) or '').strip() for f in FIELDS}))
    return out


def cn_prefixes(name, maxlen=14):
    """一个中文名的前缀候选集合（与日文版思路同源，规则按中文调）。

    * 空格前段（「凭依之风 薇茵」-> 凭依之风）与切到首个分隔符的头段优先；
    * 逐字前缀做兜底；剥掉数字/★/「号」尾巴；
    * **允许 ASCII 收尾**（K9 这类字段中文也照写）—— 这是与日文版最大的差别。
    """
    out = set()
    name = (name or '').strip()
    if not name:
        return out
    seg = name.split(' ')[0].split('　')[0].strip()
    if seg != name and len(seg) >= 2 and seg[-1] not in SEP:
        out.add(seg)
    head = name
    for s in set(SEP):
        if s in head[1:]:
            head = head[:head.index(s, 1)]
    head = head.strip()
    if len(head) >= 2 and head[-1] not in SEP:
        out.add(head)
    for length in range(2, min(maxlen, len(head)) + 1):
        p = head[:length].strip()
        while p and p[-1] in DIRTY:
            p = p[:-1].strip()
        if len(p) >= 2:
            out.add(p)
    return out


def field_guess(low, cards, field):
    """一个英文名前缀 × 一个中文字段 -> ``(叫法, 覆盖率, 命中, 总数)``。

    分母 = 该字段**非空**的命中卡（md 偶尔缺译时不冤枉这个字段）。
    """
    hit = [rec.get(field, '') for en, rec in cards if en.startswith(low)]
    hit = [v for v in hit if v]
    total = len(hit)
    if total < MIN_CARDS:
        return None, 0.0, total, total
    cnt = Counter()
    for v in hit:
        #: 先清洗再去重 —— 同一张卡给同一前缀只贡献一次
        for p in cn_prefixes(v):
            cnt[p] += 1
    pick = None
    for p, c in cnt.items():
        if c < MIN_HITS or c / total < LOOSE_COV:
            continue
        if pick is None or (len(p), c) > (len(pick[0]), pick[1]):
            pick = (p, c)
    if pick is None:
        return None, 0.0, total, total
    return pick[0], pick[1] / total, pick[1], total


def best_field(low, cards, prefer=FIELDS):
    """按优先级挑第一个达线的字段，全不达线给覆盖率最高的。"""
    got = {f: field_guess(low, cards, f) for f in FIELDS}
    for f in prefer:
        v, cov, _h, _t = got[f]
        if v and cov >= MIN_COV:
            return f, got
    best = max(FIELDS, key=lambda f: (got[f][1], got[f][2]))
    v, cov = got[best][0], got[best][1]
    if v and cov >= MIN_COV:
        return best, got
    return None, got


def guess_mixed(name, cards):
    """混轴：英文拆段各推字段名再拼接（用户 2026-09-16「混轴=字段拼接」口径）。

    每段都要在卡表里独立成立（>=MIN_CARDS 张卡）且字段达线（>=MIN_COV），
    有一段不行整个放弃 —— 宁缺毋滥。返回 ``(拼接名, 明细, 覆盖率)`` 或 ``(None, …)``。
    """
    words = name.split()
    if len(words) < 2:
        return None, [], 0.0
    parts, i, covs = [], 0, []
    while i < len(words):
        took = None
        for j in range(len(words), i, -1):
            seg = ' '.join(words[i:j])
            n = sum(1 for en, _r in cards if en.startswith(seg.lower()))
            if n >= MIN_CARDS:
                took = (i, j, n)
                break
        if took is None:
            return None, [], 0.0
        i, j, _n = took
        seg = ' '.join(words[i:j])
        f, got = best_field(seg.lower(), cards)
        if f is None:
            return None, [], 0.0
        v, cov = got[f][0], got[f][1]
        parts.append((seg, FIELD_LABEL.get(f, f), v))
        covs.append(cov)
        i = j
    if len(parts) < 2:
        return None, [], 0.0
    return ''.join(p[2] for p in parts), parts, min(covs)


def prefill_one(name, cards):
    """一个卡组 -> ``(中文名, 别名列表, 是否混轴, 明细行)``。"""
    low = name.lower()
    f, got = best_field(low, cards)
    if f is None:
        mj, mparts, mcov = guess_mixed(name, cards)
        if mj:
            note = '%s=拼(%s) %d%%' % (
                FIELD_LABEL.get(mparts[0][1], mparts[0][1]),
                '+'.join('%s:%s' % (q[0], q[2]) for q in mparts),
                round(mcov * 100))
            return mj, [], True, [note]
        return '', [], False, []
    cn = got[f][0]
    detail = ['%s=%s %d%%(%d/%d)' % (FIELD_LABEL[f], cn,
                                     round(got[f][1] * 100), got[f][2], got[f][3])]
    aliases = []
    for g in FIELDS:
        v, cov = got[g][0], got[g][1]
        if v and cov >= MIN_COV and v != cn and v not in aliases:
            aliases.append(v)
            detail.append('%s=%s %d%%' % (FIELD_LABEL[g], v, round(cov * 100)))
    return cn, aliases, False, detail


def db_missing_names(st):
    """库里的站内条目里**缺中文名**的（预填的主场景）。"""
    return [r['name'] for r in st.list_meta_decks(include_hidden=True)
            if (r.get('source') or '') != 'custom'
            and not (r.get('name_cn') or '').strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description='ygocdb 字段推定中文名，预填覆盖表')
    ap.add_argument('--only', nargs='+', metavar='卡组名',
                    help='只看这几个（默认=库里缺中文名的站内条目）')
    ap.add_argument('--write', action='store_true', help='追加进覆盖表（已有名字跳过）')
    ap.add_argument('--csv', default=CSV, help='覆盖表路径')
    ap.add_argument('--ygocdb', default=YGOCDB, help='ygocdb 卡表路径')
    args = ap.parse_args(argv)

    cards = load_cards(args.ygocdb)
    if not cards:
        print('✘ 找不到 ygocdb 卡表：%s' % os.path.relpath(args.ygocdb, PROJ))
        return 1
    if args.only:
        names = list(args.only)
    else:
        names = db_missing_names(Storage())
        if not names:
            print('库里没有缺中文名的站内条目')
            return 0
        print('[模式] 库内缺中文名 %d 条' % len(names))

    import datetime
    stamp = datetime.date.today().isoformat()
    todo = []
    for name in names:
        cn, aliases, mixed, detail = prefill_one(name, cards)
        flag = ''
        if not cn:
            flag = '⚠ 推不出（留空人填）'
        elif mixed:
            flag = '⚠ 混轴拼接'
        elif len(aliases) < 2:
            flag = '⚠ 字段分歧少'
        print('%-26s %-12s %-24s %s' % (
            name[:26], cn or '——', '/'.join(aliases) or '——',
            '；'.join(detail) if detail else flag))
        if cn:
            todo.append((name, cn, aliases, mixed, detail))

    print()
    print('共 %d 个；可预填 %d；推不出的 %d'
          % (len(names), len(todo), len(names) - len(todo)))
    if not args.write:
        print('[只打印] 加 --write 追加进 %s' % os.path.relpath(args.csv, PROJ))
        return 0

    header, rows = deck_csv.read(args.csv)
    idx = deck_csv.index_map(header)
    have = {deck_csv.get(r, idx, '站内英文名') for r in rows}
    added = 0
    for name, cn, aliases, mixed, detail in todo:
        if name in have:
            print('   = %-26s 覆盖表已有，跳过' % name)
            continue
        row = [''] * len(header)
        deck_csv.set_cell(row, idx, '站内英文名', name)
        deck_csv.set_cell(row, idx, '中文名', cn)
        deck_csv.set_cell(row, idx, '别名', '/'.join(aliases))
        deck_csv.set_cell(row, idx, '备注',
                          '预填待核 %s（%s）' % (stamp, '；'.join(detail)))
        rows.append(row)
        added += 1
    deck_csv.write(args.csv, header, rows)
    print('[写入] %s：追加 %d 行（备注列已标「预填待核」）'
          % (os.path.relpath(args.csv, PROJ), added))
    print('下一步：人手核对 -> python tools/fetch_meta_decks.py --cn-only')
    return 0


if __name__ == '__main__':
    sys.exit(main())
