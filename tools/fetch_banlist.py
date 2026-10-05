# -*- coding: utf-8 -*-
"""抓禁限卡变更史（masterduelmeta 公开 API），落库 + 打印。

数据源：``GET https://www.masterduelmeta.com/api/v1/banlist-changes``
（61 条公告，从 2022-05 到现在；一条公告里带若干张卡的 `from` / `to`）

* `to_status` 是 `NULL` = **解除限制**（回到无限制）
* 卡 id 是**站内的 `_id`**，不是游戏内卡 id —— 中文名 / 日文名靠公告里自带的
  **英文卡名**反查 `data/card_names.json`（`--lang cn|en|jp` 切语言）

用法::

    python tools/fetch_banlist.py              # 抓取 + 落库
    python tools/fetch_banlist.py --list 20     # 看最近 20 条变更（按生效时间倒序）
    python tools/fetch_banlist.py --current     # 看「现在还在禁限表上的卡」
    python tools/fetch_banlist.py --current --lang jp
    python tools/fetch_banlist.py --dry-run     # 只抓不写

**手动按月跑一次**（跟 `fetch_meta_decks.py` 一样），不做定时任务。
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.card_names import LANGS, load as load_card_names   # noqa: E402
from core.storage import Storage                            # noqa: E402

URL = 'https://www.masterduelmeta.com/api/v1/banlist-changes'
#: 当前禁限状态（**权威**）：cards API 的 `banStatus` 字段
#: ⚠️ 别用变更史累计算「现在禁什么」—— 那个算法漏得出奇（2026-09-21 实报：
#:    简易融合的变更史停在 2023「限制→禁止」，站内实际是限制；另有 77 张
#:    老禁卡在变更史里从未出现过）。
CARDS_URL = 'https://www.masterduelmeta.com/api/v1/cards'
CARDS_PAGE = 3000
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

#: 站内状态 -> 中文说法。**中文就这三个词，不带数字**（用户 2026-09-12 定）：
#: `Forbidden`=禁止 / `Limited 1`=限制 / `Limited 2`=准限制。
#: ⚠️ 别把 Limited 2 念成禁止 —— 那是准限制（能放 2 张）。
STATUS_CN = {'Forbidden': '禁止', 'Limited 1': '限制', 'Limited 2': '准限制'}


def status_label(value, lang='cn'):
    """状态显示名：``None`` = 解除限制；`lang='en'` 时保持站内原样。"""
    if not value:
        return '解除限制'
    if lang == 'en':
        return value
    return STATUS_CN.get(value, value)


def fetch(retries=3, timeout=45):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(URL, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:                              # noqa: BLE001
            last = e
            if i + 1 < retries:
                import time
                time.sleep(2 * (i + 1))
    raise RuntimeError('抓取失败 %s：%s' % (URL, last))


def fetch_current(retries=3, timeout=45):
    """抓**当前禁限状态**（翻页拉 cards，只留 `banStatus` 非空的）。

    返回 ``[(英文卡名, 站内状态)]``。翻页只能用 `skip`（`start=` 坏）；
    偶发 SSL EOF 交给 `_get_json` 重试。
    """
    out, skip = [], 0
    while skip < 30000:                    # 站内 1.4 万张，留足余量
        url = ('%s?fields=name,banStatus&limit=%d&skip=%d'
               % (CARDS_URL, CARDS_PAGE, skip))
        rows = _get_json(url, retries=retries, timeout=timeout)
        if not rows:
            break
        for c in rows:
            name, status = (c.get('name') or '').strip(), c.get('banStatus')
            if name and status:
                out.append((name, status))
        skip += len(rows)
    return out


def _get_json(url, retries=3, timeout=45):
    """GET -> JSON；带重试（站内偶发 SSL EOF）。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:                              # noqa: BLE001
            last = e
            if i + 1 < retries:
                import time
                time.sleep(2 * (i + 1))
    raise RuntimeError('抓取失败 %s：%s' % (url, last))


def parse(data):
    """API 原始数据 -> 可以直接落库的行（一条公告 × 一张卡 = 一行）。"""
    rows = []
    for rec in data or []:
        changes = rec.get('changes') or []
        url = (rec.get('linkedArticle') or {}).get('url')
        for ch in changes:
            card = ch.get('card') or {}
            if not card.get('_id'):
                continue
            rows.append(dict(
                announce_id=rec.get('_id') or '',
                card_mdm_id=card['_id'],
                card_en=card.get('name'),
                from_status=ch.get('from'),
                to_status=ch.get('to'),
                effective=rec.get('date'),
                announced=rec.get('announced'),
                url=url))
    return rows


def build_reverse(table):
    """英文卡名 -> (中文, 英文, 日文)；只做小写归一，够用。"""
    rev = {}
    for names in table.values():
        if names[1]:
            rev.setdefault(names[1].strip().lower(), names)
    return rev


def show_name(en, lang, rev):
    if lang == 'en' or not en:
        return en or '?'
    names = rev.get(en.strip().lower())
    if not names:
        return en
    idx = LANGS.index(lang)
    return names[idx] or names[0] or en


def main(argv=None):
    ap = argparse.ArgumentParser(description='禁限卡变更史：抓取 / 查看')
    ap.add_argument('--list', nargs='?', const=15, type=int, metavar='N',
                    help='看最近 N 条变更（默认 15）')
    ap.add_argument('--current', action='store_true', help='看当前还在禁限表上的卡')
    ap.add_argument('--since', metavar='YYYY-MM-DD', help='只看这天起的变更')
    ap.add_argument('--lang', choices=list(LANGS), default='cn',
                    help='卡名语言：cn（默认）/ en / jp')
    ap.add_argument('--dry-run', action='store_true', help='只抓不写库')
    ap.add_argument('--db', help='换个库（默认 data/masterduel.db）')
    args = ap.parse_args(argv)

    st = Storage(args.db) if args.db else Storage()
    st.init_schema()
    rev = build_reverse(load_card_names())

    if args.list is None and not args.current:
        print('[抓取] %s' % URL)
        rows = parse(fetch())
        print('  %d 条变更' % len(rows))
        if args.dry_run:
            print('[dry-run] 未写库')
        else:
            ins, stale = st.replace_banlist_changes(rows)
            print('[落库] 变更史 写入 %d，清掉 %d；现共 %d 条'
                  % (ins, stale, len(st.list_banlist_changes())))

        #: ★ 当前状态走 **cards 的 banStatus**（权威）—— 见 CARDS_URL 注释
        print('[抓取] %s（当前禁限状态）' % CARDS_URL)
        cur = fetch_current()
        print('  受限 %d 张' % len(cur))
        if not args.dry_run:
            ins2, stale2 = st.replace_banlist_current(
                [{'card_en': n, 'status': s} for n, s in cur])
            print('[落库] 当前状态 写入 %d，清掉 %d' % (ins2, stale2))
        return 0

    if args.current:
        now = st.banlist_current()
        print('当前禁限表（%d 张）' % len(now))
        last_status = None
        for en, status, eff in now:
            if status != last_status:
                head = status_label(status, args.lang)
                if args.lang != 'en' and head != status:
                    head = '%s · %s' % (head, status)      # 中英都给，免得念错
                print('  【%s】' % head)
                last_status = status
            print('    %-40s %s（生效 %s）'
                  % (show_name(en, args.lang, rev), en if args.lang != 'en' else '',
                     (eff or '')[:10]))
        if not now:
            print('  （空 —— 先跑一次不带参数的抓取）')
        return 0

    rows = st.list_banlist_changes(limit=args.list, since=args.since)
    print('最近 %d 条变更（按生效时间倒序）' % len(rows))
    last_ann = None
    for r in rows:
        ann = (r.get('announced') or '')[:10]
        if ann != last_ann:
            print('  ── 公告 %s（生效 %s）'
                  % (ann, (r.get('effective') or '?')[:10]))
            last_ann = ann
        print('     %-12s -> %-12s %-38s %s'
              % (status_label(r.get('from_status'), args.lang) if r.get('from_status')
                 else '—',
                 status_label(r.get('to_status'), args.lang),
                 show_name(r.get('card_en'), args.lang, rev),
                 r.get('card_en') if args.lang != 'en' else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
