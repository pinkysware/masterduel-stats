# -*- coding: utf-8 -*-
"""抓 masterduelmeta 的 tier list，落 meta_decks + 下载卡组图。

数据源：https://www.masterduelmeta.com/tier-list

  * 服务端渲染的 **HTML**，不用 API、不用浏览器（该站 api/v1/cards 的 start
    分页参数是坏的，别走那条路）
  * `robots.txt` **只禁 `/cdn-cgi/`** —— 抓此页不违规
  * 页面含两个榜单：**强度排行**(Power Rankings) 与 **使用率排行**(Popularity Rankings)
  * **TCG/OCG 只是标签文字，页内无内容** —— 天然排除，无需额外过滤
  * 卡组图托管在 `imgserv.duellinksmeta.com/v2/mdm/{deck-type|engines}/<名>`，
    按名字寻址、可用 `?portrait=true&width=N` 取指定尺寸

  * **top-decks 近 N 天牌表**（2026-10-12 加，用户拍板）：一条请求拿回窗口内
    全部牌表，前端聚合出「大师一 ∪ 新卡」口径的卡组，**只加库里没有的** ——
    这两张口径是纯前端过滤（browser 实测点击不发请求），脚本直接打 API 等价复现

`kind` 的语义（用户口径）：
  * `deck_type` = 单一主题卡组，如 Dracotail
  * `engine`    = **混合卡组**，如 Kewl Tune + Elfnote 混编，哪边卡多归哪边的 engine

用法::

    python tools/fetch_meta_decks.py               # 抓取 -> 落库 -> 下图
    python tools/fetch_meta_decks.py --dry-run     # 只打印，不写库不下图
    python tools/fetch_meta_decks.py --no-images   # 不下载图片
    python tools/fetch_meta_decks.py --html FILE   # 用本地 HTML（离线复现）

**主体零网络请求** —— 只有手动跑这个脚本时才联网。建议按月跑一次。
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import deck_csv                 # noqa: E402
from core.deck_match import split_aliases  # noqa: E402
from core.storage import Storage, SOURCE_CUSTOM  # noqa: E402

TIER_URL = 'https://www.masterduelmeta.com/tier-list'
#: 站内的公开 JSON API（**没有文档**，是前端自己在用的；低频跑、容错、可回退 HTML）
API_URL = 'https://www.masterduelmeta.com/api/v1/deck-types'
#: 只取这三个字段，一条 200 字节左右
API_FIELDS = 'name,tournamentPower,popRank,masterPopRank'
#: top-decks 全量端点（top-decks 页面自己用的；一条请求拿回窗口内全部牌表）
TOPDECKS_URL = 'https://www.masterduelmeta.com/api/v1/top-decks'
#: 近 N 天新卡清单（NEW CARDS 口径的匹配集；站上页面同款参数）
NEWCARDS_URL = 'https://www.masterduelmeta.com/api/v1/cards'
#: 大师一 ∪ 新卡 口径的默认窗口天数（用户 2026-10-12 拍板 30；--days 可调）
TOPDECK_DAYS = 30
#: 站内**全量** deck-types 名单的落盘位置 —— 管理页分组判据
#: （站内**有**这个名字 = 爬取的卡组；没有 = 自定义。随抓取顺手刷新）
SITE_NAMES_PATH = os.path.join(PROJ, 'data', 'site_deck_names.json')
#: tournamentPower -> tier。区间就是榜单页上写的那四档
#: （Tier1 ≥12 / Tier2 7~12 / Tier3 3~7 / Trending 1~3）
TIER_CUTS = ((12.0, 'Tier1'), (7.0, 'Tier2'), (3.0, 'Tier3'))
IMG_HOST = 'https://imgserv.duellinksmeta.com'
IMG_WIDTH = 200
IMG_DIR = os.path.join(PROJ, 'data', 'deck_images')
#: 自定义条目的图放这里 —— 站内没有、**不可再生**，所以进版本库
IMG_CUSTOM_DIR = os.path.join(PROJ, 'data', 'deck_images_custom')
#: 缺图时的**默认卡图**（用户提供，200x200 webp）——
#: 用户手动新增的卡组默认用它；站内图下载失败也回退到它
DEFAULT_IMAGE = os.path.join(IMG_CUSTOM_DIR, '_default.webp')
CN_CSV = os.path.join(PROJ, 'data', 'deck_name_cn.csv')        # ★ 手改这个（Excel 好开）
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# ---- 解析用正则 ----
RE_TIER_MARK = re.compile(r'alt="(Tier\s*\d+|Trending)"')
RE_ANCHOR = re.compile(
    r'<a[^>]+href="/tier-list/(engines|deck-types)/([^"]+)"[^>]*>(.*?)</a>', re.S)
RE_LABEL = re.compile(r'class="label[^"]*">([^<]+)<')
RE_IMG = re.compile(r'srcset="(https://imgserv[^"\s]+)')
RE_POWER = re.compile(r'Power:\s*<b>([\d.]+)</b>')
RE_POP = re.compile(r'Popularity:\s*<strong>([\d.]+)%</strong>')
RE_BAD_FN = re.compile(r'[\\/:*?"<>|\s]+')


def fetch(url, retries=3, timeout=45):
    """GET 一个 URL，返回 (bytes, content_type)。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get('Content-Type', '')
        except Exception as e:                      # noqa: BLE001
            last = e
            if i + 1 < retries:
                time.sleep(2 * (i + 1))
    raise RuntimeError('抓取失败 %s: %s' % (url, last))


def tier_of(power):
    """比赛强度分 -> tier。没分就是 ``None``（榜单外）。"""
    if power is None:
        return None
    for cut, name in TIER_CUTS:
        if power >= cut:
            return name
    return 'Trending'


def fetch_api(params, base=API_URL):
    """打一次 API，**统一成 list** 返回。

    ⚠️ 这个接口有个怪脾气：`limit=1` 时返回的是**单个对象**，不是只有一条的数组。
    """
    url = base + '?' + urllib.parse.urlencode(params)
    body, ctype = fetch(url)
    data = json.loads(body.decode('utf-8'))
    if isinstance(data, dict):
        return [data]
    return data or []


def fetch_from_api():
    """走 API 拿榜单 + 使用率，返回与 `parse_tier_list` **同形状**的 ``(rows, orphan)``。

    两条请求：
      * ``tournamentPower[$gte]=1`` —— 完整榜单（= 页面上「Power: x」那批）
      * ``popRank[$gt]=0`` —— 有使用率数据的卡组（``popRank`` 就是页面上的 %）

    ⚠️ 别拿 ``masterPopRank`` 当这个筛子 —— 它是**大师一段位的排名**，会漏掉
    `Blue-Eyes` / `Radiant Typhoon Zoodiac` 这种（实测 2026-09-12，两个筛子都返回
    20 条但**不是同一批**）。

    比爬 HTML 稳：不用猜页面结构；而且**没有 engine 行**（engine 是站内的展示分组、
    API 里压根不存在）—— 正好合我们的口径（见 AGENTS.md 铁律 7）。
    """
    tier_raw = fetch_api({'tournamentPower[$gte]': 1, 'limit': '40',
                          'fields': API_FIELDS})
    pop_raw = fetch_api({'popRank[$gt]': 0, 'limit': '100',
                         'fields': API_FIELDS})
    rows = [dict(name=r['name'], kind='deck_type',
                 tier=tier_of(r.get('tournamentPower')),
                 power=r.get('tournamentPower'),
                 popularity=r.get('popRank') or None,   # 榜单行自己就带使用率
                 slug=urllib.parse.quote(r['name']), img_url=None)
            for r in tier_raw if r.get('name')]
    by_name = {r['name']: r for r in rows}
    orphan = []
    for r in pop_raw:
        name = r.get('name')
        if not name:
            continue
        hit = by_name.get(name)
        if hit is not None:
            hit['popularity'] = r.get('popRank') or hit['popularity']
        else:                       # 只在使用率榜出现、榜单没有 —— 按口径不收
            orphan.append(dict(name=name, kind='deck_type', tier=None,
                               power=r.get('tournamentPower'),
                               popularity=r.get('popRank'),
                               slug=urllib.parse.quote(name), img_url=None))
    return rows, orphan


def fetch_site_deck_names():
    """站内**全量** deck-types 名字（一条请求，592 个 / 2026-10-12 实测）。

    管理页「爬取 / 自定义」分组判据用：站内**有** = 爬取的卡组。
    与 top-decks 口径无关 —— 每次抓取都顺手刷新这份名单。
    """
    rows = fetch_api({'limit': '3000', 'fields': 'name'})
    return sorted({(r.get('name') or '').strip() for r in rows} - {''})


def write_site_names(names, path=SITE_NAMES_PATH):
    """落盘站内名单（幂等整文件覆盖）。"""
    import datetime
    payload = {'version': 1,
               'fetched_at': datetime.datetime.now().isoformat(timespec='seconds'),
               'source': 'masterduelmeta /api/v1/deck-types?limit=3000',
               'names': sorted(set(names))}
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(chr(10))
    return path


def fetch_topdecks(days=TOPDECK_DAYS):
    """top-decks 窗口内**全部牌表**（一条请求，实测 30 天约 1100 副 / 350KB）。

    页面上的 MASTER I / NEW CARDS 是纯前端过滤 —— 口径聚合在本地做，
    另带 ``rankedType``（打到的大师段位）与完整 main/extra/side 卡表。
    """
    return fetch_api({'created[$gte]': '(days-%d)' % days,
                      'fields': '-_id,-__v,-notes', 'limit': '0'},
                     base=TOPDECKS_URL)


def fetch_new_card_ids(days=TOPDECK_DAYS):
    """近 N 天的新卡 id 集合（站内 ``_id``，top-decks 卡表项引用的就是它）。"""
    rows = fetch_api({'release[$gte]': '(days-%d)' % days,
                      'alternateArt[$ne]': 'true', 'fields': 'name',
                      'limit': '0'}, base=NEWCARDS_URL)
    return {r.get('_id') for r in rows if r.get('_id')}


def _topdeck_cards_have_new(deck, new_ids):
    """一副牌的 main/extra/side 里有没有新卡（卡引用是对象，兜底裸 id）。"""
    for part in ('main', 'extra', 'side'):
        for e in (deck.get(part) or []):
            ref = (e or {}).get('card')
            cid = ref.get('_id') if isinstance(ref, dict) else ref
            if cid in new_ids:
                return True
    return False


def topdeck_union_rows(decks, new_card_ids):
    """**大师一 ∪ 新卡**口径聚合（纯函数，可单测）。

    * MASTER I：``rankedType.name == 'Master I'``（打到大师一的牌表）
    * NEW CARDS：main/extra/side 含任一窗口内新卡
    * 返回 ``(rows, stats)``：rows 与榜单行同形（tier 恒 None —— **只是候选**，
      进不进库由调用方按「库里有没有」过滤），另带 ``master1`` / ``new_cards``
      命中数；stats = (牌表总数, 大师一副数, 新卡副数)
    """
    from collections import Counter
    mi, nc = Counter(), Counter()
    for d in decks:
        name = ((d.get('deckType') or {}).get('name') or '').strip()
        if not name:
            continue
        if ((d.get('rankedType') or {}).get('name') == 'Master I'):
            mi[name] += 1
        if new_card_ids and _topdeck_cards_have_new(d, new_card_ids):
            nc[name] += 1
    stats = (len(decks), sum(mi.values()), sum(nc.values()))
    rows = []
    for name in sorted(set(mi) | set(nc)):
        rows.append(dict(name=name, kind='deck_type', tier=None, power=None,
                         popularity=None, slug=urllib.parse.quote(name),
                         img_url=None, master1=mi.get(name, 0),
                         new_cards=nc.get(name, 0)))
    return rows, stats


def topdeck_known_lower(tier_names, db_rows, ovr):
    """口径行去重的「跳过名」集合（小写）—— 只有**会撞名冲突的**才算已知。

    * 榜单行名（all_rows 里已有同名，再加就重复）；
    * 库里 **custom 条目**与**混轴（engine）行**的全部叫法 —— 口径行不该
      顶掉用户自建/隐藏条目（``Banquet FTK``=结界像、``Mitsurugi Yummy``
      是 engine 行显示名，这类）；
    * 覆盖表的**别名/显示英文名**（``Stun`` 挂结界像这类并行口径 ——
      别名多半还没进库，不查 CSV 会被误当新卡组建出来）。

    ⚠️ 库里**普通站内条目不算已知** —— 上一窗口的口径行必须继续进
    all_rows，否则 `replace_meta_decks(prune)` 会把它们当「掉出榜单」删掉
    （2026-10-12 实测事故：16 条被 prune 掉又被 custom_rows 重建成 custom）。
    ⚠️ 覆盖表的**站内英文名键不算** —— 那是「这副牌要建条目」的意思。
    """
    known = {n.lower() for n in tier_names}
    for r in db_rows or []:
        if ((r.get('source') or '') == 'custom'
                or (r.get('kind') or '') == 'engine'):
            known.add((r.get('name') or '').lower())
            known.add((r.get('name_display') or '').lower())
            known |= {a.lower() for a in (r.get('aliases') or [])}
    for o in (ovr or {}).values():
        known |= {a.lower() for a in (o.get('aliases') or [])}
        if o.get('display'):
            known.add(o['display'].lower())
    return known


def split_topdeck_new(rows, known_lower):
    """把口径行分成「库里没有的」和「已有的」（纯函数）。

    ``known_lower`` = **小写化的全部叫法**集合 —— 调用方要把 meta_decks 的
    name / name_display / aliases 都丢进来（``Mitsurugi Yummy`` 是隐藏 engine 行
    ``Yummy Engine`` 的显示名，不能当新条目重复建）。
    """
    fresh = [r for r in rows if r['name'].lower() not in known_lower]
    seen = [r['name'] for r in rows if r['name'].lower() in known_lower]
    return fresh, seen


def diff_with_db(st, rows):
    """跟库里的同名字段对账，返回 ``(比了几条, [(名字, 字段, 库里的, 抓到的)])``。"""
    have = {r['name']: r for r in st.list_meta_decks(include_hidden=True)}
    n, diffs = 0, []
    for r in rows:
        old = have.get(r['name'])
        if old is None:
            continue
        n += 1
        for field in ('tier', 'power', 'popularity'):
            if old.get(field) != r.get(field):
                diffs.append((r['name'], field, old.get(field), r.get(field)))
    return n, diffs


def parse_tier_list(html):
    """从 tier list 页面提取全部卡组。

    返回 ``(rows, orphan_popularity)``：
      * ``rows`` —— 强度榜全部条目（含 tier/power，能对上的还带 popularity）
      * ``orphan_popularity`` —— 只在使用率榜出现、强度榜没有的卡组
    """
    marks = [(m.start(), m.group(1).replace(' ', ''))
             for m in RE_TIER_MARK.finditer(html)]
    if not marks:
        raise ValueError('页面里找不到 tier 标记（alt="Tier N"/"Trending"）—— 站点结构可能变了')
    power_start = marks[0][0]

    pop_start = html.find('deck-type-container', power_start)
    if pop_start == -1:
        raise ValueError('找不到使用率区块（deck-type-container）—— 站点结构可能变了')

    power_html = html[power_start:pop_start]
    pop_html = html[pop_start:]

    # --- 强度排行：每条锚点到下一条锚点之间算一条记录 ---
    rows = []
    hits = list(RE_ANCHOR.finditer(power_html))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(power_html)
        rec = power_html[m.start():end]
        lab = RE_LABEL.search(rec)
        if not lab:
            continue
        tier = None
        for pos, tname in marks:
            if pos - power_start < m.start():
                tier = tname
        img = RE_IMG.search(rec)
        pw = RE_POWER.search(rec)
        rows.append(dict(
            name=unescape(lab.group(1).strip()),
            kind='engine' if m.group(1) == 'engines' else 'deck_type',
            tier=tier,
            power=float(pw.group(1)) if pw else None,
            popularity=None,
            slug=unescape(m.group(2)),
            img_url=unescape(img.group(1)).split('?')[0] if img else None,
        ))

    # --- 使用率排行：合并进已有条目 ---
    by_key = {(r['name'], r['kind']): r for r in rows}
    orphan = []
    for chunk in pop_html.split('<div class="deck-type-container')[1:]:
        lab = RE_LABEL.search(chunk)
        if not lab:
            continue
        name = unescape(lab.group(1).strip())
        pp = RE_POP.search(chunk)
        val = float(pp.group(1)) if pp else None
        hit = by_key.get((name, 'deck_type')) or by_key.get((name, 'engine'))
        if hit is not None:
            hit['popularity'] = val
        else:
            img = RE_IMG.search(chunk)
            orphan.append(dict(
                name=name, kind='deck_type', tier=None, power=None,
                popularity=val, slug=name,
                img_url=unescape(img.group(1)).split('?')[0] if img else None))
    return rows, orphan


def fetch_engine_rows():
    """抓 tier-list 页补 **engine 行**（API 里没有 engine；用户 2026-09-20 拍板：
    Tier 表要列出全部混轴）。复用 `parse_tier_list`，只取 kind='engine' 的行。
    失败返回 []（engine 缺席不算失败 —— Tier 表少几颗芯片而已，别挡住主流程）。
    """
    try:
        print('[抓取] %s（补 engine 行，API 里没有）' % TIER_URL)
        body, _ctype = fetch(TIER_URL)
        rows, _orphan = parse_tier_list(body.decode('utf-8', 'replace'))
        eng = [r for r in rows if r.get('kind') == 'engine']
        print('  engine %d 条' % len(eng))
        return eng
    except Exception as e:
        print('  ⚠ engine 行抓取失败（本次跳过）：%s' % e)
        return []


def _cn_base_for_engine(name, deck_rows):
    """engine 中文名 = **基准卡组中文名 + 「混轴」**（Elfnote Engine -> 耀圣混轴）。

    基准 = 去掉结尾 ' Engine'；先按站内名精确匹配 deck_type 行，
    找不到再查已有库（Storage），再找不到返回 None（显示退回英文名）。
    """
    base = re.sub(r'\s*Engine\s*$', '', name).strip()
    rows = [r for r in deck_rows if r.get('kind') == 'deck_type']
    try:
        rows = rows + [r for r in Storage().list_meta_decks(include_hidden=True)
                       if r.get('kind') != 'engine']
    except Exception:
        pass

    def _aliases(r):
        a = r.get('aliases')
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except Exception:
                a = [a] if a else []
        return a or []

    for r in rows:                           # ① 站内名（基准名或 engine 全名）精确命中
        if r.get('name') in (base, name):
            return r.get('name_cn') or base
    for r in rows:                           # ② 别名 / 显示英文名命中（Yummy Engine -> 巳剑黯蜜）
        if name == r.get('name_display') or name in _aliases(r):
            return r.get('name_cn') or base
    return None


def _read_text(path):
    """按 utf-8-sig -> gb18030 顺序尝试解码（Excel 可能存成 GBK）。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _norm_tier(s):
    """把 CSV 里的分组写法归一成库里的值。

    归一规则（用户 2026-09-11 定：**一律不带空格**）：

        'Tier 1' / 'tier2'  -> 'Tier1' / 'Tier2'
        'Tier4' / 't4' / '其它' / '其他'  -> 'Trending'

    合法值只有四个：``Tier1`` / ``Tier2`` / ``Tier3`` / ``Trending``。
    """
    v = (s or '').replace(' ', '').strip()
    if not v:
        return None
    low = v.lower()
    if low in ('tier4', 't4') or v in ('其它', '其他'):
        return 'Trending'
    m = re.fullmatch(r'tier0*(\d+)', low)
    if m:
        return 'Tier' + m.group(1)
    return v


def custom_image_for(name):
    """**只查覆盖目录** ``data/deck_images_custom/``，返回绝对路径或 None。

    这个目录里的图有两种用途：

    1. **自定义条目**的图（站内没有的卡组，图不可再生）
    2. **用户手动替换掉的站内图** —— 只想换图、不想换条目时用；
       站内重抓不会覆盖它（见 :func:`download_images`）
    """
    base = safe_filename(name)
    for ext in ('.webp', '.png', '.jpg', '.jpeg'):
        p = os.path.join(IMG_CUSTOM_DIR, base + ext)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def _rel_or_abs(p):
    """尽量给相对项目根的路径；跨盘符时给绝对路径。"""
    try:
        return os.path.relpath(p, PROJ).replace('\\', '/')
    except ValueError:
        return p.replace('\\', '/')


def local_image_for(name):
    """按约定找本地卡组图，返回相对项目根的路径（找不到返回 None）。

    先找 ``data/deck_images_custom/``（自定义条目 + 手动替换的图，**不可再生**、
    进版本库），再找 ``data/deck_images/``（站内下载的，可再生）。
    文件名都用卡组名（空格等替换成下划线）。
    """
    # ⚠️ 实现已搬到 core，这里只委托 —— **口径只有一份**。
    # 区别记住：本函数是「**文件名直查**」（工具链用，不需要数据库）；
    # 而 core.deck_images.DeckImages.resolve() 是「**名字解析链**」
    # （要 storage 查别名索引，能把中文名 闪刀姬 解析到 Sky_Striker.webp），面向界面。
    from core import deck_images
    p = deck_images.find_image_file(name)
    return deck_images.rel_or_abs(p) if p else None


def load_overrides(path=CN_CSV):
    """读卡组覆盖表，返回 ``({站内名: {...}}, 待核名单)``。

    列的读法统一交给 :mod:`core.deck_csv`（**表头认列**，容错见那边）：

    ``站内英文名 / 中文名 / 日文名 / 别名 / 分组 / 隐藏 / 备注``
    （显示英文名列已删 —— 2026-10-12，见 core/deck_csv.py 模块头）

    * 站内没有的名字 = **自定义卡组**（我们自己建的）
    * 分组：站内**有**分组的以站内为准；站内没有的、以及自定义条目才用这一列
    * 容错：BOM / GBK / 多出来的列 / 空行 / 缺列，老版 7 列（无「日文名」）照读
    """
    header, rows = deck_csv.read(path)
    idx = deck_csv.index_map(header)
    ovr, todo = {}, []
    for row in rows:
        name = deck_csv.get(row, idx, '站内英文名')
        if not name or name.startswith('_'):
            continue                            # 说明行
        note = deck_csv.get(row, idx, '备注')
        ovr[name] = dict(cn=deck_csv.get(row, idx, '中文名'),
                         jp=deck_csv.get(row, idx, '日文名'),
                         display=deck_csv.get(row, idx, '显示英文名'),
                         aliases=split_aliases(deck_csv.get(row, idx, '别名')),
                         tier=_norm_tier(deck_csv.get(row, idx, '分组')),
                         hidden=deck_csv.get(row, idx, '隐藏').upper().startswith('Y'),
                         note=note)
        if '待核' in note:
            todo.append(name)
    return ovr, todo


def custom_rows(ovr, known_names):
    """CSV 里有、站内抓取里没有的名字 = **自定义卡组**，返回可直接落库的行。

    图按约定从 ``data/deck_images/`` 找（见 :func:`local_image_for`）。
    """
    out = []
    for name, o in ovr.items():
        if name in known_names or o['hidden']:
            continue
        out.append(dict(
            name=name, kind='deck_type',
            name_cn=o['cn'], name_ja=o.get('jp') or None,
            name_display=o['display'],
            aliases=list(o.get('aliases') or []), hidden=0,
            tier=o['tier'], power=None, popularity=None, slug=None,
            img_url=None, img_file=local_image_for(name),
            source=SOURCE_CUSTOM))
    return out


def apply_overrides(rows, ovr):
    """把覆盖项贴到 rows 上。**不在表里的名字整项不动**（传 None，库里保留旧值）。

    在表里但留空 = 明确清空（传空串），这样「删掉一个填错的中文名」才有效。

    分组的分工：
      * 站内**有**分组的，以站内为准（每月会跟着更新，覆盖表说了不算）
      * 站内**没有**分组的（例如只出现在使用率榜的那些），才用覆盖表里的兜底
    """
    unknown = []
    for r in rows:
        o = ovr.get(r['name'])
        if o is None:
            unknown.append(r['name'])
            continue
        r['name_cn'] = o['cn']
        r['name_ja'] = o.get('jp', '')
        r['name_display'] = o['display']
        r['aliases'] = list(o.get('aliases') or [])
        r['hidden'] = 1 if o['hidden'] else 0
        if not r.get('tier') and o.get('tier'):
            r['tier'] = o['tier']
    return unknown


def display_name(row):
    """选择框里该显示什么名字（中文优先，其次改名，最后站内名）。"""
    return row.get('name_cn') or row.get('name_display') or row['name']


def safe_filename(name):
    """卡组名 -> 安全的文件名（不含扩展名）。"""
    s = RE_BAD_FN.sub('_', name).strip('._')
    return s or 'unnamed'


def image_url_for(row, width=IMG_WIDTH):
    """按 (kind, 名字) 构造标准尺寸的图片 URL。

    图片是**按名字寻址**的（deck-type/Dracotail、engines/<hash>），
    这里优先用页面里给的原 URL 换宽度；没有则按 kind 用名字拼。
    """
    if row.get('img_url'):
        return '%s?portrait=true&width=%d' % (row['img_url'], width)
    seg = 'engines' if row['kind'] == 'engine' else 'deck-type'
    # safe='' 必须的：卡组名里可能有 `/`（如 `D/D/D`、`R.B.`），
    # 默认 safe='/' 不转义斜杠，会把 URL 路径切碎
    name = urllib.parse.quote(row['name'], safe='')
    return '%s/v2/mdm/%s/%s?portrait=true&width=%d' % (IMG_HOST, seg, name, width)


EXT_BY_CTYPE = {
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/jpg': '.jpg',
    'image/webp': '.webp', 'image/gif': '.gif',
}


def download_images(rows, dest=IMG_DIR, width=IMG_WIDTH, verbose=True):
    """把卡组图下载到本地。返回 {name: 相对路径}。

    已存在且非空的就跳过（省流量、支持断点续抓）。
    """
    os.makedirs(dest, exist_ok=True)
    got, skipped, failed = {}, 0, []
    for r in rows:
        # 覆盖目录里有同名图 = 用户手动替换过 —— 优先用它，别下站内图盖掉
        ov = custom_image_for(r['name'])
        if ov is not None:
            got[r['name']] = _rel_or_abs(ov)
            skipped += 1
            continue
        base = safe_filename(r['name'])
        hit = None
        for ext in ('.png', '.webp', '.jpg'):
            p = os.path.join(dest, base + ext)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                hit = p
                break
        if hit:
            got[r['name']] = os.path.relpath(hit, PROJ).replace('\\', '/')
            skipped += 1
            continue
        try:
            body, ctype = fetch(image_url_for(r, width))
            ext = EXT_BY_CTYPE.get(ctype.split(';')[0].strip().lower(), '.png')
            p = os.path.join(dest, base + ext)
            with open(p, 'wb') as f:
                f.write(body)
            got[r['name']] = os.path.relpath(p, PROJ).replace('\\', '/')
            if verbose:
                print('   ↓ %-28s %6d B  %s' % (r['name'][:28], len(body), ext))
        except Exception as e:                      # noqa: BLE001
            # 🔴 下不到就**留空**，绝不把 `DEFAULT_IMAGE` 当成真图写进库
            #（2026-10-10 修的真事故）：占位图一旦进了 `img_file`，别名索引会永远
            # 命中它，用户后来把真图下载回来也显示不出来。留空则 `missing_image_rows`
            # 下次仍会把它列出来补图。界面侧由 `DeckImages.resolve` 兜底成默认图。
            failed.append((r['name'], str(e)[:60]))
    return got, skipped, failed


def show_cn_table(rows, ovr, todo_note=()):
    """打印核对表：站内名 / 中文名 / 日文名 / 状态。"""
    todo = set(todo_note)
    print('%-26s %-12s %-18s %s'
          % ('站内英文名', '中文名', '日文名', '状态'))
    print('-' * 80)
    for r in sorted(rows, key=lambda x: (x.get('tier') or 'z', x['name'])):
        o = ovr.get(r['name']) or {}
        notes = []
        if o.get('hidden'):
            notes.append('已隐藏')
        if not o.get('cn'):
            notes.append('★待补')
        elif r['name'] in todo:
            notes.append('待核')
        if not o.get('jp'):
            notes.append('★缺日文')
        if r.get('kind') == 'engine':
            notes.append('engine')
        print('%-26s %-12s %-18s %s' % (
            r['name'][:25], o.get('cn') or '——',
            o.get('jp') or '——', ' '.join(notes)))


def missing_image_rows(st, exists=None):
    """库里**缺图**的站内条目 -> 可直接喂 `download_images` 的最小行表。

    判「缺」= `img_file` 空，**或**那个文件已经不在了（清理过 / 换过机器）。
    自定义条目（source='custom'）**不碰** —— 它们的图是我们自己放的，站内没有。
    """
    exists = exists or (lambda p: os.path.exists(os.path.join(PROJ, p)))
    out = []
    for r in st.list_meta_decks(include_hidden=True):
        if (r.get('source') or '') == SOURCE_CUSTOM:
            continue
        p = r.get('img_file') or ''
        if p and exists(p):
            continue
        out.append({'name': r['name'], 'kind': r.get('kind') or 'deck_type',
                    'img_url': r.get('img_url')})
    return out


#: API 里每条卡组的**代表卡**字段名（`deck-types.card` = 该卡组的门面卡；
#: 站内那张 `deck-type/<名字>` 图就是它 —— 2026-10-08 实测相关性 0.90~0.96）
REP_CARD_API = 'https://www.masterduelmeta.com/api/v1/deck-types'

def representative_card_id(deck_name):
    """某卡组的**代表卡**（游戏内卡 id / gameId）；拿不到返回 None。

    两步：`deck-types?name=X` 拿 `card`（ObjectId）→ `cards?_id=` 换成 `gameId`
    （游戏内卡 id，与我们卡表同一套编号）。**只读公开 API，低频。**
    """
    try:
        recs = fetch_api({'name': deck_name, 'fields': 'name,card'})
    except Exception:                                    # noqa: BLE001
        return None
    #: `fetch_api` 统一返回 list（`limit=1` 的怪脾气它已经吞掉了）
    oid = (recs[0] if recs else {}).get('card')
    if not oid:
        return None
    try:
        body, _ct = fetch('https://www.masterduelmeta.com/api/v1/cards'
                          '?_id=%s&fields=name,gameId' % oid)
        cards = json.loads(body.decode('utf-8', 'replace'))
    except Exception:                                    # noqa: BLE001
        return None
    if isinstance(cards, list):
        cards = cards[0] if cards else None
    gid = (cards or {}).get('gameId')
    return int(gid) if gid else None


def fetch_by_representative(deck_name):
    """站内卡组图取不到时的兜底：拿**代表卡的卡绘** -> 覆盖目录里的图路径。

    返回 ``(相对路径, 说明)``；失败返回 ``(None, 原因)``。
    """
    from core import deck_images
    cid = representative_card_id(deck_name)
    if not cid:
        return None, '拿不到代表卡'
    tmp = deck_images.fetch_card_art(cid)
    if not tmp:
        return None, '代表卡 #%s 的卡绘取不到' % cid
    try:
        #: 覆盖目录里落**卡组名**（`resolve` 靠文件名直查命中；自组卡组也能用）
        rel = deck_images.save_custom_image(deck_name, tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not rel:
        return None, '存图失败'
    return rel, '代表卡 #%s' % cid


def fetch_missing_images():
    """补库里缺的卡组图（联网）。返回退出码（0 = 全部补上）。

    两条腿：① 站内 `deck-type/<名字>` 图；② 取不到就**用代表卡的卡绘**兜底
    （2026-10-08：卡组图 == 代表卡卡绘，实测相关性 0.90~0.96）。
    """
    st = Storage()
    st.init_schema()
    rows = missing_image_rows(st)
    if not rows:
        print('[缺图] 没有缺图的站内条目')
        return 0
    print('[缺图] %d 条：%s' % (len(rows), '、'.join(r['name'] for r in rows)))
    got, _skipped, failed = download_images(rows)
    fixed, still = 0, []
    for r in rows:
        name = r['name']
        rel = got.get(name)
        failed_names = {f[0] for f in failed}
        if not rel or name in failed_names:
            #: 站内图失败 —— 走代表卡兜底
            rel2, why = fetch_by_representative(name)
            if rel2:
                rel = rel2
                print('   ⇄ %-28s 站内图取不到，改用%s' % (name[:28], why))
        if rel:
            st.set_meta_deck_override(name, img_file=rel)
            fixed += 1
        else:
            still.append(name)
    print('[缺图] 补上 %d，失败 %d' % (fixed, len(still)))
    for name, err in failed:
        print('   ✗ %-28s %s' % (name[:28], err))
    return 1 if still else 0


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                               # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description='抓 masterduelmeta tier list')
    ap.add_argument('--dry-run', action='store_true', help='只打印，不写库不下图')
    ap.add_argument('--no-images', action='store_true', help='不下载图片')
    ap.add_argument('--html', help='用本地 HTML 文件（离线复现）')
    ap.add_argument('--source', choices=('api', 'html'), default='api',
                    help='榜单来源：api（默认，两条请求拿全）/ html（老路，兜底）')
    ap.add_argument('--keep-stale', action='store_true',
                    help='不清理已掉出榜单的旧条目')
    ap.add_argument('--cn-only', action='store_true',
                    help='只把 data/deck_name_cn.csv 应用到库里（不联网、不违规）')
    ap.add_argument('--missing-images', action='store_true',
                    help='只补库里缺的卡组图（联网；不抓榜单、不动覆盖表）')
    ap.add_argument('--days', type=int, default=TOPDECK_DAYS,
                    help='大师一∪新卡 口径的窗口天数（默认 30）')
    ap.add_argument('--no-topdecks', action='store_true',
                    help='不抓 top-decks 口径（只走两张榜单，老行为）')
    args = ap.parse_args()

    # ---- 0.5 只补缺图（Q17 贴完覆盖表后接着跑；2026-10-08）----
    if args.missing_images:
        return fetch_missing_images()

    # ---- 0. 只应用覆盖表（不联网）----
    if args.cn_only:
        st = Storage()
        st.init_schema()
        ovr, todo_note = load_overrides()

        rows = st.list_meta_decks(include_hidden=True)
        site = [r for r in rows if (r['source'] or '') != SOURCE_CUSTOM]
        old_custom = [r for r in rows if (r['source'] or '') == SOURCE_CUSTOM]

        hit = 0
        img_ovr = 0
        for r in site:
            o = ovr.get(r['name'])
            if o is None:
                continue
            # 覆盖目录里有同名图 = 用户手动换过图 —— 换掉库里的 img_file
            ov = custom_image_for(r['name'])
            img = _rel_or_abs(ov) if ov is not None else None
            if img and img != r['img_file']:
                img_ovr += 1
            st.set_meta_deck_override(r['name'], name_cn=o['cn'],
                                      name_ja=o.get('jp') or None,
                                      name_display=o['display'],
                                      aliases=o.get('aliases') or [],
                                      hidden=o['hidden'], kind=r['kind'],
                                      img_file=img)
            # 分组：站内给的不动，只把历史遗留的非法值（如「其它」）归一
            st.fix_legacy_tier(r['name'], o.get('tier'))
            hit += 1

        # 自定义条目：CSV 定义的全量重建；CSV 里删掉的，库里也删掉
        want = custom_rows(ovr, {r['name'] for r in site})
        if want:
            st.replace_meta_decks(want, prune=False)
        want_names = {r['name'] for r in want}
        removed = 0
        for r in old_custom:
            if r['name'] not in want_names:
                st.delete_meta_deck(r['name'], r['kind'])
                removed += 1

        print('[覆盖表] 站内命中 %d / %d；自定义条目 %d 个（清理 %d）'
              % (hit, len(site), len(want), removed))
        if img_ovr:
            print('[换图] %d 个站内条目的图来自 deck_images_custom/（手动替换）'
                  % img_ovr)

        # ---- 清掉历史污染：img_file 指向默认图的（2026-10-10 修的真事故）----
        # `find_image_file` 以前拿默认图兜底，`custom_rows` 分不清「找到真图」与
        # 「拿到兜底」，就把 `_default.webp` 当成真图写进了库。库里存着它以后，
        # `DeckImages.resolve` 第 1 环的别名索引会永远命中默认图 —— 用户后来把
        # 真图下载回来也显示不出来。清成空串后 `resolve` 会重新走真图链。
        polluted = [r['name'] for r in st.list_meta_decks(include_hidden=True)
                    if (r.get('img_file') or '') == _rel_or_abs(DEFAULT_IMAGE)]
        for nm in polluted:
            st.set_meta_deck_override(nm, img_file='')
        if polluted:
            print('[修图] 清掉 %d 条指向默认图的假图（改回空，等真图/兜底图接管）: %s'
                  % (len(polluted), '、'.join(polluted[:12])
                     + ('…' if len(polluted) > 12 else '')))
        print()
        show_cn_table(st.list_meta_decks(include_hidden=True), ovr, todo_note)
        blank = [r['name'] for r in site
                 if not (ovr.get(r['name']) or {}).get('cn')
                 and not (ovr.get(r['name']) or {}).get('hidden')]
        if blank:
            print()
            print('还有 %d 个没中文名 —— 打开 %s 补，再跑一次本命令'
                  % (len(blank), os.path.relpath(CN_CSV, PROJ)))
        return

    # ---- 1. 取数据（默认走 API；--html / --source html 保留老路）----
    if args.html:
        print('[读本地] %s' % args.html)
        html = io.open(args.html, encoding='utf-8').read()
        print('  页面 %d 字节' % len(html))
        rows, orphan = parse_tier_list(html)
    elif args.source == 'html':
        print('[抓取] %s（HTML 老路）' % TIER_URL)
        body, ctype = fetch(TIER_URL)
        html = body.decode('utf-8', 'replace')
        print('  页面 %d 字节' % len(html))
        rows, orphan = parse_tier_list(html)
    else:
        print('[抓取] %s（API）' % API_URL)
        rows, orphan = fetch_from_api()
    # 只收录 **tier list** 的卡组（用户 2026-09-11 口径）。
    # 页面上另有一块「使用率/强度排行」，那里面只出现在排行榜、Tier 榜没有的卡组
    # （orphan）**一律不收** —— 之前它们会被当 tier=None 的条目加进来。
    # popularity 仍然合并到 tier 条目上（只是给已有条目补数据，不会新增条目）。
    all_rows = rows
    print('  Tier 榜 %d 条；另有 %d 条只出现在排行榜、**已按口径丢弃**%s'
          % (len(rows), len(orphan),
             ('：' + '、'.join(r['name'] for r in orphan)) if orphan else ''))

    # ---- 1.5 大师一 ∪ 新卡 口径（2026-10-12 用户拍板：重点录入这两类）----
    # 只加**库里没有的**（已知卡组走榜单/覆盖表链路，别拿 tier=None 的口径行
    # 冲掉现有 tier）。掉出窗口的口径行由 replace_meta_decks 的 prune 清掉 ——
    # 滚动窗口语义；想留底用 --keep-stale。
    if args.source == 'api' and not args.html and not args.no_topdecks:
        try:
            tdecks = fetch_topdecks(args.days)
            tids = fetch_new_card_ids(args.days)
            trows, tstats = topdeck_union_rows(tdecks, tids)
            st_probe = Storage()
            ovr_td, _td_note = load_overrides()
            known = topdeck_known_lower([r['name'] for r in rows],
                                        st_probe.list_meta_decks(include_hidden=True),
                                        ovr_td)
            fresh, seen = split_topdeck_new(trows, known)
            orph = {r['name']: r for r in orphan}
            for r in fresh:                      # 使用率榜上的口径卡组带上 pop
                o = orph.get(r['name'])
                if o:
                    r['popularity'] = o.get('popularity')
                    r['power'] = o.get('power')
            all_rows.extend(fresh)
            print('  [大师一∪新卡] %d 天窗口：牌表 %d 副（大师一 %d / 新卡 %d）'
                  '→ 口径 %d 卡组，库外新增 %d%s'
                  % (args.days, tstats[0], tstats[1], tstats[2], len(trows),
                     len(fresh), ('：' + '、'.join(r['name'] for r in fresh))
                     if fresh else ''))
        except Exception as e:                               # noqa: BLE001
            print('  [大师一∪新卡] 抓取失败，跳过（%s）' % e)

    # ---- 1.6 站内全量名单（管理页「爬取 / 自定义」分组判据）----
    if args.source == 'api' and not args.html:
        try:
            snames = fetch_site_deck_names()
            if not args.dry_run:
                write_site_names(snames)
            print('  [站内名单] 全量 %d 个 deck-types%s'
                  % (len(snames), '' if args.dry_run
                     else '（已存 %s）' % os.path.relpath(SITE_NAMES_PATH, PROJ)))
        except Exception as e:                               # noqa: BLE001
            print('  [站内名单] 抓取失败，跳过（%s）' % e)

    tiers = {}
    for r in all_rows:
        tiers.setdefault(r['tier'] or '(无)', []).append(r)
    print()
    for tname in ('Tier1', 'Tier2', 'Tier3', 'Trending', '(无)'):
        if tname not in tiers:
            continue
        items = tiers[tname]
        print('  %-9s %2d 条  %s' % (tname, len(items),
                                    '、'.join(x['name'] for x in items[:6])
                                    + ('…' if len(items) > 6 else '')))
    n_eng = sum(1 for r in all_rows if r['kind'] == 'engine')
    print()
    print('  合计 %d 条（engine %d / deck_type %d）'
          % (len(all_rows), n_eng, len(all_rows) - n_eng))

    if args.dry_run:
        print()
        print('%-9s %-10s %-26s %-7s %s' % ('tier', 'kind', 'name', 'power', 'pop%'))
        print('-' * 66)
        for r in all_rows:
            print('%-9s %-10s %-26s %-7s %s' % (
                r['tier'] or '-', r['kind'], r['name'][:25],
                r['power'] if r['power'] is not None else '-',
                r['popularity'] if r['popularity'] is not None else '-'))
        print()
        # 跟库里现有的同名条目对账（只读）—— 换数据源时靠这个确认没抓歪
        try:
            st = Storage()
            n, diffs = diff_with_db(st, all_rows)
            print('[对账] 与库内同名条目比了 %d 条：%s'
                  % (n, '完全一致' if not diffs else '%d 处不同' % len(diffs)))
            for name, field, old, new in diffs[:20]:
                print('   %-28s %-11s 库=%-8s 抓到=%s' % (name[:28], field, old, new))
            if len(diffs) > 20:
                print('   …还有 %d 处' % (len(diffs) - 20))
        except Exception as e:                       # noqa: BLE001
            print('[对账] 跳过（%s）' % e)
        print()
        print('[dry-run] 未写库、未下图')
        return

    # ---- 2.5 贴覆盖项（中文名 / 改名 / 隐藏）----
    # 必须在**下图之前**做：engine 那一步要按覆盖表的「显示英文名」决定去留，
    # 而且被丢弃的 engine 不该再下图（否则会留下没人引用的孤儿图）。
    ovr, todo_note = load_overrides()
    unknown = apply_overrides(all_rows, ovr)

    # ---- 2.6 engine 行保留（2026-09-20 用户拍板，**推翻** 09-11 的「丢弃」口径）----
    # Tier 表要列出全部混轴：engine 行**保留但 hidden=1**（选择器 / 标注仍只见
    # deck_type），中文名 = 基准卡组中文名 + 「混轴」（Elfnote Engine -> 耀圣混轴）。
    # 覆盖表给了「显示英文名」的仍按覆盖表走 —— ⚠️ 该列已删（2026-10-12），
    # 此规则只剩老文件兼容意义；Yummy Engine 的 Mitsurugi Yummy 已进别名。
    # API 模式的 rows 里没有 engine（站内 API 不返回）—— 单独抓 tier-list 页补。
    deck_rows = [r for r in all_rows if r.get('kind') != 'engine']
    eng_rows = [r for r in all_rows if r.get('kind') == 'engine']
    if not eng_rows and not args.html and args.source != 'html':
        eng_rows = fetch_engine_rows()
    kept = []
    for r in eng_rows:
        if (ovr.get(r['name']) or {}).get('display'):
            continue          # 用户已改造成独立条目（deck_type 里有），engine 行丢弃
        r['hidden'] = 1
        if not r.get('name_cn'):
            base = _cn_base_for_engine(r['name'], deck_rows)
            r['name_cn'] = (base + '混轴') if base else None
        kept.append(r)
    if kept:
        print('  [engine] 保留 %d 条（hidden，中文名=基准+混轴）：%s'
              % (len(kept), '、'.join(
                  (r.get('name_cn') or r['name']) for r in kept)))
    all_rows = deck_rows + kept

    # ---- 3. 下图 ----
    if args.no_images:
        print()
        print('[跳过] 不下载图片')
    else:
        print()
        print('[下图] -> %s（已有则跳过）' % os.path.relpath(IMG_DIR, PROJ))
        imgs, skipped, failed = download_images(all_rows)
        for r in all_rows:
            r['img_file'] = imgs.get(r['name'])
        print('  新下 %d 张，跳过 %d 张，失败 %d 张'
              % (len(imgs) - skipped, skipped, len(failed)))
        for name, err in failed:
            print('   ✗ %-28s %s' % (name[:28], err))

    customs = custom_rows(ovr, {r['name'] for r in all_rows})
    all_rows.extend(customs)
    n_cn = sum(1 for r in all_rows if r.get('name_cn'))
    n_hid = sum(1 for r in all_rows if r.get('hidden'))
    print()
    print('[覆盖表] 中文名 %d / %d；隐藏 %d 个；自定义条目 %d 个'
          % (n_cn, len(all_rows), n_hid, len(customs)))
    for r in customs:
        print('   + 自定义: %s / %s  图=%s'
              % (r['name'], r['name_cn'] or '—', r['img_file'] or '**无**'))
    if unknown:
        print('   ⚠ 不在覆盖表里的（保持原样）：%s' % '、'.join(unknown))
    # （engine 不再丢弃 —— 2026-09-20 起保留为 hidden 条目，见 2.6）

    # ---- 4. 落库 ----
    print()
    st = Storage()
    st.init_schema()
    ins, upd, pruned = st.replace_meta_decks(all_rows, prune=not args.keep_stale)
    print('[落库] 新增 %d，更新 %d，清理 %d；现共 %d 条'
          % (ins, upd, pruned, st.count_meta_decks()))

    # 顺手落一份原始快照，便于回溯
    snap = os.path.join(PROJ, 'tmp', 'meta_decks_snapshot.json')
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    with io.open(snap, 'w', encoding='utf-8', newline='') as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print('[快照] %s' % os.path.relpath(snap, PROJ))


if __name__ == '__main__':
    main()
