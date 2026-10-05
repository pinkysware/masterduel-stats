# -*- coding: utf-8 -*-
"""读「卡组编辑界面里我正在编辑的卡组」-> M5 判定是什么卡组（**只读，不写库**）。

用法（游戏停在卡组编辑界面时）：

    python tools/read_my_deck.py            # 卡表 + 判定 + 卡图
    python tools/read_my_deck.py --json     # 机读输出

⚠️ 内部要做**一次全堆扫描**（找编辑控制器实例）—— 只适合**手动跑**；
   悬浮窗里那次「点开选择器时读一下」是同一个道理（一次性动作，不进热路径）。
⚠️ 只读：读的是**你自己正在编辑的卡组**，不碰对手任何信息。
"""

import argparse
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import card_names as cn                                   # noqa: E402
from core import deck_guess as dg                                   # noqa: E402
from core.memread import deck_edit                                  # noqa: E402


def _display_name(cand, lang='cn'):
    """候选 -> 当前语言的卡组名（打分器候选**不带** name_cn，得回查一次库里）。"""
    name = (cand or {}).get('name')
    if not name:
        return None
    try:
        from core.storage import Storage
        rows = Storage().search_meta_decks(name, limit=1) or []
        if rows:
            d = rows[0]
            key = {'cn': 'name_cn', 'jp': 'name_ja', 'en': 'name'}[lang]
            return d.get(key) or d.get('name_cn') or d.get('name')
    except Exception:                                           # noqa: BLE001
        pass
    return name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true', help='机读输出')
    ap.add_argument('--lang', default='cn', choices=('cn', 'en', 'jp'))
    args = ap.parse_args(argv)

    deck = deck_edit.read_edited_deck_auto()
    if deck is None:
        print('读不到 —— 请把游戏停在**卡组编辑界面**（新建/改卡组的那个画面）再跑一次。')
        return 1
    ids = list(deck.get('ids') or [])
    main, extra = deck.get('main') or [], deck.get('extra') or []
    idx, groups = dg.load_index()
    cands = dg.suggest_own_deck(ids, idx, groups) if idx else []
    top = cands[0] if cands else None
    img = None
    if top:
        try:
            from core.deck_images import DeckImages
            from core.storage import Storage
            img = DeckImages(Storage()).resolve(top.get('name'))
        except Exception:                                           # noqa: BLE001
            img = None
    disp = _display_name(top, args.lang) if top else None
    if args.json:
        print(json.dumps({'deck_id': deck.get('deck_id'),
                          'verdict_display': disp,
                          'deck_name': deck.get('deck_name'),
                          'main': main, 'extra': extra,
                          'verdict': top, 'image': img,
                          'candidates': cands[:5]}, ensure_ascii=False, indent=1))
        return 0

    print('主卡组 %d 张：' % len(main))
    for i in main:
        print('   %-8s %s' % (i, cn.display(i, args.lang)))
    print('额外卡组 %d 张：' % len(extra))
    for i in extra:
        print('   %-8s %s' % (i, cn.display(i, args.lang)))
    print()
    if not idx:
        print('没有参照库（data/mdm_deck_cards.json）—— 先跑 tools/fetch_mdm_decks.py --all')
        return 0
    if not cands:
        print('认不出来（没有一套牌解释得了这么多张）—— 大概率是自制/混轴卡组，'
              '在悬浮窗那格点 `!` 手动选一个吧。')
        return 0
    print('判定：%s' % disp)
    print('      英文名 %s / tier %s' % (top.get('name'), top.get('tier')))
    print('      解释度 %.2f（%s/%s 张）' % (top.get('confidence') or 0.0,
                                        top.get('hits'), top.get('revealed')))
    print('卡图：%s' % (img or '（没配上，可在 data/deck_images_custom/ 放同名图）'))
    if len(cands) > 1:
        print('次选：%s' % '、'.join(
            '%s(%s/%s)' % (c.get('name'), c.get('hits'), c.get('revealed'))
            for c in cands[1:4]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
