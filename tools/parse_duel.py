# -*- coding: utf-8 -*-
"""解析 untapped log，输出对局复盘（卡 ID -> 中文卡名）

卡库: data/card_data.json (来源 GetDeck, 基于 MD 游戏资源提取, 14298 张)

用法:
    python tools/parse_duel.py --all          # 列出所有对局
    python tools/parse_duel.py                # 最近一局(完整)
    python tools/parse_duel.py --idx 6        # 按序号
    python tools/parse_duel.py --time 18:57   # 按时间
    python tools/parse_duel.py --detail       # 附带卡组构成
"""
import re, json, os, sys, argparse

#: Untapped 日志目录（本机安装位置可能不同 —— 默认 %APPDATA%，可用环境变量覆盖）
HOME = os.path.join(os.environ.get('APPDATA',
                                   os.path.expanduser('~')),
                    'untapped-companion')
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = [os.path.join(HOME, 'log.old.log'), os.path.join(HOME, 'log.log')]
CARDS_JSON = os.path.join(PROJ, 'data', 'card_data.json')
DECKS_JSON = os.path.join(HOME, 'ygom', 'decks.json')

# ---------- 1. 卡名映射 ----------
def build_cardmap():
    with open(CARDS_JSON, encoding='utf-8') as f:
        data = json.load(f)
    return {x['id']: x['name'] for x in data}

CMAP = build_cardmap()

def cname(cid):
    if not cid:
        return '(隐藏)'
    return CMAP.get(cid, '[未知:%d]' % cid)

DECKS = json.load(open(DECKS_JSON, encoding='utf-8'))

# ---------- 2. 读日志 ----------
def read_all_lines():
    lines = []
    for p in LOGS:
        if os.path.exists(p):
            lines.extend(open(p, encoding='utf-8', errors='replace').read().splitlines())
    return lines

ALL = read_all_lines()
re_line = re.compile(r'^\[([\d\-]+ [\d:.]+)\]\s+\[(\w+)\]\s+\[([\w.]+)\]\s+(.*)')

def collect_duels():
    duels, cur = [], None
    for i, l in enumerate(ALL):
        if 'Starting Duel with deckId=' in l:
            m = re_line.match(l)
            mm = re.search(r'deckId=(\S+?),', l)
            cur = dict(start=i, time=(m.group(1) if m else '?'),
                       deck_id=(mm.group(1) if mm else None), result=None, end=None)
        elif 'Game ended' in l and cur is not None:
            mm = re.search(r'result=(\w+)', l)
            cur['result'] = mm.group(1) if mm else '?'
            cur['end'] = i
            duels.append(cur); cur = None
    if cur is not None:
        cur['end'] = len(ALL) - 1
        cur['result'] = cur['result'] or '(未结束)'
        duels.append(cur)
    return duels

DUELS = collect_duels()

# ---------- 3. 解析单局 ----------
def parse_duel(d):
    seg = ALL[d['start']:d['end'] + 1]
    events = []
    for l in seg:
        m = re_line.match(l)
        if not m: continue
        ts, lvl, mod, msg = m.groups()
        if 'is deciding whether to go first' in msg:
            events.append(('COIN', ts, 'Player' if msg.startswith('Player') else 'Opponent'))
        elif 'Turn ' in msg and ' for player ' in msg:
            mm = re.search(r'Turn (\d+) for player (\d+)', msg)
            events.append(('TURN', ts, (int(mm.group(1)), int(mm.group(2)))))
        elif 'Game ended' in msg:
            events.append(('END', ts, None))
        elif 'Starting Duel' in msg:
            continue
        else:
            am = re.match(r'^(\w+): cardId=(\d+) source=(\S+) target=(\S+)', msg)
            if am:
                act, cid, src, tgt = am.groups()
                events.append(('ACT', ts, (act, int(cid), src, tgt)))
    return events

def decide_seat(events, deck_id):
    my_ids = set()
    if deck_id and deck_id in DECKS:
        dk = DECKS[deck_id]
        for k in dk.get('main', {}): my_ids.add(int(k))
        for k in dk.get('extra', {}): my_ids.add(int(k))
    hits = {0: 0, 1: 0}; tot = {0: 0, 1: 0}; vis = {0: 0, 1: 0}
    for kind, ts, p in events:
        if kind == 'ACT' and p[0] == 'DRAW':
            _, cid, src, tgt = p
            s = 0 if src.startswith('P0') else 1
            tot[s] += 1
            if cid: vis[s] += 1
            if cid in my_ids: hits[s] += 1
    if my_ids and (hits[0] or hits[1]):
        return (0 if hits[0] >= hits[1] else 1), '卡组命中率 P0=%d/%d P1=%d/%d' % (hits[0], tot[0], hits[1], tot[1])
    if vis[0] or vis[1]:
        return (0 if vis[0] >= vis[1] else 1), '手牌可见性 P0=%d张 P1=%d张' % (vis[0], vis[1])
    return 0, '无法判定，默认 P0'

# 动作类型 -> 中文
ACT_CN = {
    'DRAW': '抽牌', 'SUMMON': '通常召唤', 'SPSUMMON': '特殊召唤', 'SPSUMMON_LINK': '连接召唤',
    'SPSUMMON_SYNC': '同调召唤', 'SPSUMMON_XYZ': '超量召唤', 'SPSUMMON_FUSION': '融合召唤',
    'HAPPEN': '发动/处理', 'SEARCH': '检索', 'ATTACK': '攻击', 'DISABLE': '效果无效',
    'BREAK': '破坏', 'SENDTOGRAVE': '送墓', 'LOCKON': '指定对象', 'SETV': '盖放',
    'SETH': '盖放(怪兽)', 'HANDOPEN': '公开手牌', 'EXCLUDE': '除外', 'RELEASE': '解放',
    'RETURN': '返回', 'FLIPTOP': '翻开', 'EXPLOSION': '破坏', 'undefined': '移动',
}

def loc_cn(s, seat):
    """位置转中文"""
    side = '我' if s.startswith('P%d' % seat) else '敌'
    zone = s.split('-', 1)[1] if '-' in s else s
    zmap = {'Hand': '手牌', 'Deck': '卡组', 'Grave': '墓地', 'Extra': '额外',
            'Banished': '除外', 'null': ''}
    if zone in zmap:
        return '%s%s' % (side, zmap[zone]) if zmap[zone] else side
    if zone.startswith('Monster'):
        return '%s怪兽区' % side
    if zone.startswith('Magic'):
        return '%s魔陷区' % side
    return '%s%s' % (side, zone)

def report(d, detail=False):
    ev = parse_duel(d)
    seat, reason = decide_seat(ev, d['deck_id'])
    dk = DECKS.get(d['deck_id']) if d['deck_id'] else None
    W = 76

    print('=' * W)
    print('对局复盘')
    print('=' * W)
    print('时间       : %s' % d['time'])
    print('我的卡组   : %s' % ((dk or {}).get('name', '未识别') + (' (%s)' % d['deck_id'] if d['deck_id'] else '')))
    print('对局结果   : %s' % d['result'])
    coin = next((p for k, t, p in ev if k == 'COIN'), None)
    if coin:
        print('硬币       : %s 赢得先手权' % ('我方' if coin == 'Player' else '对手'))
        went_first = (coin == 'Opponent')  # 对手决定 = 对手先手？(待核)
    max_turn = max((p[0] for k, t, p in ev if k == 'TURN'), default=0)
    print('总回合数   : %d' % max_turn)
    print('我方座位   : P%d  [%s]' % (seat, reason))
    print()

    if detail and dk:
        print('-' * W)
        print('我的卡组构成')
        print('-' * W)
        for zone, label in [('main', '主卡组'), ('extra', '额外卡组')]:
            items = dk.get(zone, {})
            if items:
                print('[%s]' % label)
                for k, q in items.items():
                    print('   %d x %s' % (q, cname(int(k))))
        print()

    print('-' * W)
    print('事件流')
    print('-' * W)
    for kind, ts, p in ev:
        tag = ts[11:19]
        if kind == 'COIN':
            print('%s  * %s 赢得先手权' % (tag, '我方' if p == 'Player' else '对手'))
        elif kind == 'TURN':
            print('%s  ==== 第 %d 回合 ====' % (tag, p[0]))
        elif kind == 'END':
            print('%s  **** 对局结束: %s ****' % (tag, d['result']))
        else:
            act, cid, src, tgt = p
            side = '我' if src.startswith('P%d' % seat) else '敌'
            name = cname(cid)
            actname = ACT_CN.get(act, act)
            if tgt in ('null', None):
                loc = loc_cn(src, seat)
                print('%s  [%s] %-10s %-22s  %s' % (tag, side, actname, name, loc))
            else:
                print('%s  [%s] %-10s %-22s  %s -> %s' % (tag, side, actname, name, loc_cn(src, seat), loc_cn(tgt, seat)))
    print('=' * W)

# ---------- main ----------
ap = argparse.ArgumentParser()
ap.add_argument('--all', action='store_true')
ap.add_argument('--time', type=str)
ap.add_argument('--idx', type=int)
ap.add_argument('--detail', action='store_true', help='附带卡组构成')
args = ap.parse_args()

if args.all:
    print('共 %d 局:' % len(DUELS))
    print('%-4s %-20s %-16s %s' % ('#', '时间', '卡组', '结果'))
    for i, d in enumerate(DUELS):
        dk = DECKS.get(d['deck_id']) if d['deck_id'] else None
        print('%-4d %-20s %-16s %s' % (i, d['time'][:19], (dk or {}).get('name', '-'), d['result']))
    sys.exit(0)

target = None
if args.idx is not None:
    target = DUELS[args.idx] if 0 <= args.idx < len(DUELS) else None
elif args.time:
    c = [d for d in DUELS if args.time in d['time']]
    target = c[-1] if c else None
else:
    full = [d for d in DUELS if d['result'] not in (None, '(未结束)')]
    target = full[-1] if full else (DUELS[-1] if DUELS else None)

if not target:
    print('未找到对局'); sys.exit(1)
report(target, args.detail)
