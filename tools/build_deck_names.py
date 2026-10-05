# -*- coding: utf-8 -*-
"""按「老办法」推定卡组的**日文名**，写进覆盖表的「日文名」列（默认只填空）。

老办法（跟当初提中文名 / 别名是同一套，见 AGENTS.md 铁律 7）：

1. 用卡组的**站内英文名**去 `ygocdb.com.cards/cards.json` 里找它的卡
   （`en_name` 以它开头；找不到再退化成「包含」）
2. 在这些卡的 `jp_name` 里找**覆盖率 ≥60% 的最长前缀** —— 那就是这个字段的日文名
   （例：`Branded` → 烙印、`Tearlaments` → ティアラメンツ、`Sky Striker` → 閃刀姫）

拿不准的（找不到卡、覆盖率低于 80%、命中卡少于 3 张）会**标出来**，等人手改。

用法::

    python tools/build_deck_names.py                 # 只打印，不动文件
    python tools/build_deck_names.py --write         # 把空的「日文名」填上
    python tools/build_deck_names.py --write --force # 连已有值一起覆盖
    python tools/build_deck_names.py --only Dracotail Branded   # 只看这几个

写完记得跑 `python tools/fetch_meta_decks.py --cn-only` 让它进库。
"""

import argparse
import os
import sys
from collections import Counter

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import deck_csv                 # noqa: E402

YGOCDB = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')
CSV = os.path.join(PROJ, 'data', 'deck_name_cn.csv')

#: 前缀里要去掉的尾巴（分隔符 / 助词）—— 「墓守の司令官」要归成「墓守」
STRIP = '　 ・:：－-‐−～~＜＞<>「」『』【】♪☆★の之'

#: 结构切割点：卡名里出现这些字，字段名基本就在它前面（`ドラゴンメイドのお召し替え`）
CUT = 'の之'

#: 命中卡少于这么多张就不敢下结论
MIN_CARDS = 3
#: 候选前缀至少要这么多张卡撑（免得挑出「ヤミーズメント」这种只出现 2 次的怪东西）
MIN_HITS = 3
#: 前缀至少要覆盖这么多比例的卡（宁可短一点，也别猜歪）
MIN_COV = 0.6
#: 放松线：完全够不着 MIN_COV 时，用「覆盖率最高的最长前缀」。
#: 例：Sky Striker 的卡分 閃刀姫/閃刀術式/閃刀機… 三种，共同前缀只有「閃刀」（100%），
#: 但正解是「閃刀姫」（11/26 = 42%）—— 取「≥40% 里最长的那个」才对。
LOOSE_COV = 0.4
#: 低于这个覆盖率就标出来让人看一眼
WARN_COV = 0.8


def load_cards(path=YGOCDB):
    """`[(英文名, 日文名)]`；文件不在就返回空表。"""
    import json
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = []
    for rec in (raw.values() if isinstance(raw, dict) else raw):
        en = (rec.get('en_name') or '').strip()
        jp = (rec.get('jp_name') or rec.get('jp_ruby') or '').strip()
        if en and jp:
            out.append((en, jp))
    return out


def prefixes(name, maxlen=12):
    """一个日文名的所有前缀候选（顺手去掉尾部的分隔符）。

    **以分隔符 / ASCII 字符结尾的候选直接扔掉** —— 否则
    「糾罪巧－A'－愤怒」会推出 `糾罪巧－A`（5/8）这种把卡名后缀当字段名的鬼东西，
    而正解是 `糾罪巧`。日文字段名不会以 `A` / `-` / `'` 收尾。
    """
    out = []
    name = name.strip()
    # 有空格的话，**空格前面那一段通常就是字段名**（`道化の一座 ホワイトフェイス`）
    seg = name.split(' ')[0].split('　')[0].strip()
    if seg != name and len(seg) >= 2 and seg[-1] not in STRIP and not seg[-1].isascii():
        out.append(seg)
    head = name
    for sep in CUT + '・－-‐−～~＜＞<> 　':
        if sep in head[1:]:
            head = head[:head.index(sep, 1)]
    if head and head[-1] not in STRIP and len(head) >= 2:
        out.append(head)              # 分隔符切出来的「正解」优先
    if 'の' in name[1:]:
        tail = name[name.rindex('の') + 1:]
        # 「アトラの蟲惑魔」这种字段名在**后面**（Traptrix = 蟲惑魔）
        if len(tail) >= 2 and tail[-1] not in STRIP and not tail[-1].isascii():
            out.append(tail)
    for cut in CUT:                     # 先做结构切割，再谈前缀
        if cut in name[1:]:
            name = name[:name.index(cut, 1)]
    for length in range(1, min(maxlen, len(name)) + 1):
        p = name[:length].rstrip(STRIP)
        if not p or p[-1] in STRIP or p[-1].isascii():
            continue
        out.append(p)
    return out


def guess(name, cards, min_cov=MIN_COV, maxlen=12):
    """推定 `name` 的日文名 -> ``(日文名, 覆盖率, 命中数, 总匹配数)``。

    只用「**以站内英文名开头**」的卡（`Branded` 找 `Branded in Red` 这类）。
    混轴卡组（`Branded Elfnote`、`Vanquish Soul K9`）本来就不该猜 —— 一张都匹配不到，
    直接留空给人填，比瞎猜强。
    """
    low = name.lower()
    hit = [jp for en, jp in cards if en.lower().startswith(low)]
    if len(hit) < MIN_CARDS:
        return None, 0.0, len(hit), len(hit)
    cnt = Counter()
    for jp in hit:
        for p in set(prefixes(jp, maxlen)):
            cnt[p] += 1
    total = len(hit)
    pick = None
    for p, c in cnt.items():
        if c < MIN_HITS or c / total < LOOSE_COV:
            continue
        if pick is None or (len(p), c) > (len(pick[0]), pick[1]):
            pick = (p, c)
    if pick:
        return pick[0], pick[1] / total, pick[1], total
    return None, 0.0, 0, total


def ensure_column(header, rows, column='日文名', after='中文名'):
    """表里没有这一列就插进去（放在 `after` 后面），返回 `(header, rows, 是否新加)`。"""
    idx = deck_csv.index_map(header)
    if column in idx:
        return header, rows, False
    at = idx.get(after, len(header) - 1) + 1
    header = list(header)
    header.insert(at, column)
    for r in rows:
        r.insert(at, '')
    return header, rows, True


def field_jp(en_part, cards, known=None):
    """一个英文字段（如 `Kewl Tune`）-> 它的日文字段名。

    口径（用户 2026-09-16 拍板）：**推不出缩写就叫法，按字段名填** ——
    即「卡名里的统一前缀/后缀」，不做日语圈习惯修正。

    两层来源（**覆盖表已有值优先** —— 里面躺着用户拍过板的口径，
    如 `Elfnote`=エルフェンノーツ / `Radiant Typhoon`=絢嵐）：
    1. 覆盖表里同站内英文名那一行的「日文名」（``known`` 映射）；
    2. 卡表推定：`en_name` **以该字段开头**（`Kewl Tune Mix`）→ jp_name 字段候选
       （`の` 前段 / `・`/空格 前段 / 整名前缀），选覆盖最高、最长那个。

    返回 ``(字段日文名, 覆盖率, 命中数, 总匹配数)``；推不出给 ``(None, 0, n, n)``。
    """
    if known and known.get(en_part):
        return known[en_part], 1.0, MIN_CARDS, MIN_CARDS
    low = en_part.lower()
    hit = [jp for en, jp in cards if en.lower().startswith(low)]
    total = len(hit)
    if total < MIN_CARDS:
        return None, 0.0, total, total
    cnt = Counter()
    for jp in hit:
        # ⚠️ 清洗**之后**再去重 —— 原始候选「巳剣之尊/巳剣」洗完都是「巳剣」，
        # 不去重的话一张卡给同一字段贡献 2 次，覆盖率会 >1（实机踩过：17/11）
        cands = {q for q in (_clean_field(p) for p in prefixes(jp, maxlen=14))
                 if q}
        for p in cands:
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


#: 字段名里的脏尾巴：ASCII 字母数字 / 全角数字 / ★☆ / 编号记号 ——
#: 「K９－１７号」「ヤミー★スナッチー」的字段是 K９ / ヤミー，尾巴不是字段的一部分
_DIRTY_TAIL = '★☆01234567890１２３４５６７８９'


def _clean_field(p):
    """一个字段候选的清洗：剥 ASCII/数字/★ 尾巴；「たる」（文语连体词）剥掉。

    ⚠️ 脏字符可能在**中间**（`ヤミー★スナッチー` 的 ★、`K９－１７号` 的数字）
    —— 截断到第一个脏字符，不是只 strip 两端。
    """
    p = p.strip(STRIP)
    if p.endswith('たる'):
        p = p[:-2].strip(STRIP)
    for i, ch in enumerate(p):
        if ch in _DIRTY_TAIL and i >= 2:
            p = p[:i].strip(STRIP)
            break
    if p.endswith('号'):                   # 「K９－１７号」的编号标记
        p = p[:-1].strip(STRIP + _DIRTY_TAIL)
    return p if len(p) >= 2 else None


def guess_mixed(name, cards, known=None):
    """混轴卡组的日文名 = **两个字段的日文名拼接**（用户 2026-09-16 定）。

    1. 英文名拆段（`Elfnote Kewl Tune` -> `Elfnote` + `Kewl Tune`）：
       从**最长前缀**开始贪心 —— 每段都要在卡表里独立成立（≥MIN_CARDS 张卡）；
    2. 每段走 `field_jp()` 推日文字段名（覆盖表已有值优先）；有一段推不出
       = 整个失败（宁缺毋滥）；
    3. 拼接顺序跟英文名一致；每段覆盖率必须 ≥MIN_COV —— 单字段误拆
       （`Rose Dragon` -> Rose+Dragon）在段级就过不了线，自然挡掉；
       覆盖表已有值的段（`K9`=K９）按 1.0 算。
    返回 ``(日文名, 覆盖率, 命中数, 总匹配数)``。
    """
    words = name.split()
    if len(words) < 2:
        return None, 0.0, 0, 0
    parts = []                            # [(英文段, 日文, cov, hit, total)]
    i = 0
    while i < len(words):
        # 贪心：取「在卡表里独立成立」的最长前缀段（单段 >= MIN_CARDS 才算成立）
        took = None
        for j in range(len(words), i, -1):
            seg = ' '.join(words[i:j])
            low = seg.lower()
            n = sum(1 for en, _jp in cards if en.lower().startswith(low))
            if n >= MIN_CARDS:
                took = (i, j, n)
                break
        if took is None:
            return None, 0.0, 0, 0        # 某段在卡表里根本不成立 -> 放弃
        i, j, _n = took
        seg = ' '.join(words[i:j])
        jp, cov, hit, total = field_jp(seg, cards, known)
        if not jp or cov < MIN_COV:
            return None, 0.0, 0, 0        # 用户口径「按字段填」——推不出字段的放弃
        parts.append((seg, jp, cov, hit, total))
        i = j
    if len(parts) < 2:
        return None, 0.0, 0, 0            # 拆不出两段 = 不是混轴，让 guess() 处理
    jp = ''.join(p[1] for p in parts)
    hit = min(p[3] for p in parts)
    total = max(p[4] for p in parts)
    cov = min(p[2] for p in parts)
    return jp, cov, hit, total


def main(argv=None):
    ap = argparse.ArgumentParser(description='推定卡组日文名，填进覆盖表')
    ap.add_argument('--write', action='store_true', help='写回 CSV（默认只打印）')
    ap.add_argument('--force', action='store_true', help='连已有值一起覆盖')
    ap.add_argument('--only', nargs='+', metavar='卡组名', help='只看这几个卡组')
    ap.add_argument('--csv', default=CSV, help='覆盖表路径')
    ap.add_argument('--ygocdb', default=YGOCDB, help='ygocdb 卡表路径')
    args = ap.parse_args(argv)

    cards = load_cards(args.ygocdb)
    if not cards:
        print('✘ 找不到 ygocdb 卡表：%s' % os.path.relpath(args.ygocdb, PROJ))
        return 1
    header, rows = deck_csv.read(args.csv)
    idx = deck_csv.index_map(header)
    if '站内英文名' not in idx:
        print('✘ 覆盖表里没有「站内英文名」列，读不出来')
        return 1
    only = {n.lower() for n in args.only} if args.only else None
    # 混轴拼接用的「字段日文名」来源：覆盖表里已有值（**含用户拍板修正**，
    # 如 Elfnote=エルフェンノーツ / Radiant Typhoon=絢嵐 —— 卡表推的反而错）
    known = {deck_csv.get(r, idx, '站内英文名'): deck_csv.get(r, idx, '日文名')
             for r in rows}
    known = {k: v for k, v in known.items() if k and v}

    todo, warn = [], []
    for row in rows:
        name = deck_csv.get(row, idx, '站内英文名')
        if not name:
            continue
        if only and name.lower() not in only:
            continue
        mixed = False
        old = deck_csv.get(row, idx, '日文名')
        jp, cov, hit, total = guess(name, cards)
        if not jp:
            # 混轴兜底（用户 2026-09-16 定）：拆两段各推字段名，推不出字段的才放弃
            jp2, cov2, hit2, total2 = guess_mixed(name, cards, known)
            if jp2:
                jp, cov, hit, total = jp2, cov2, hit2, total2
                mixed = True
        flag = ''
        if not jp:
            flag = '⚠ 找不到'
            warn.append(name)
        elif cov < WARN_COV:
            flag = '⚠ 覆盖低'
            warn.append(name)
        print('%-30s %-12s %-18s %-10s %s'
              % (name[:30], deck_csv.get(row, idx, '中文名')[:12], jp or '',
                 old or '', ('%d/%d%s' % (hit, total, ' 拼' if mixed else ''))
                 if total else flag))
        if jp and (not old or args.force):
            todo.append((row, jp))
    print()
    print('共 %d 行；可填 %d 行；需要人看的 %d 个：%s'
          % (len(rows), len(todo), len(warn), '、'.join(warn[:12]) or '无'))
    if not args.write:
        print('[只打印] 加 --write 才写回 %s' % os.path.relpath(args.csv, PROJ))
        return 0
    header, rows, added = ensure_column(header, rows)
    idx = deck_csv.index_map(header)
    for row, jp in todo:
        deck_csv.set_cell(row, idx, '日文名', jp)
    deck_csv.write(args.csv, header, rows)
    print('[写入] %s%s（%s）'
          % (os.path.relpath(args.csv, PROJ),
             '（新加「日文名」列）' if added else '',
             ('%d 行填了日文名' % len(todo)) if todo else '没有可填的行'))
    print('下一步：python tools/fetch_meta_decks.py --cn-only')
    return 0


if __name__ == '__main__':
    sys.exit(main())
