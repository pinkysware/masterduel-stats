# -*- coding: utf-8 -*-
"""抓 Untapped 公开 API + 与本项目数据库对账。

【来源】这套方法来自兄弟项目 `../游戏王战绩统计`（其 `scripts/fetch_api.py`
与 `WORKFLOW.md` 已实测）。要点：

* 该 API **完全公开只读** —— 不需要登录、不需要 Cookie、不需要浏览器
* 端点**绝不能带 query 参数**，带上会 404
* 一次返回**整个赛季**（实测 1680 条 / 2.2 MB），按月份自行过滤

【这个脚本在干什么】

1. 抓 API 原始 JSON 落盘（`data/untapped_api_raw.json`）
2. `--check`：把本项目 `masterduel.db` 里的每一局与 API 逐条对账
   （按开始时间就近匹配），报告**结果/回合数不一致**和**API 里没有的局**

【API 的覆盖边界 —— 别当成全量真源】

实测（2026-09-11）：API 只覆盖**客户端成功上传**的对局。以下都不会出现：

* **中途退出 / 掉线**的局（客户端没走完上报流程）
* 部分活动赛 / 单人模式（09-09 的 Dice Rally 与 Solo，API 里一条都没有）
* 观战回放（本就不该有）

所以「API 里没有」≠「不是对局」。它适合**验证**，不适合当唯一真源。

用法::

    python tools/fetch_untapped_api.py              # 抓取并落盘
    python tools/fetch_untapped_api.py --check      # 抓取 + 与数据库对账
    python tools/fetch_untapped_api.py --check --skip-fetch   # 用已落盘的 JSON 对账
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import atomic                                            # noqa: E402
from core.storage import (Storage, DONE_RESULTS, GAME_MODE_NAMES,  # noqa: E402
                          MODE_PVP, MODE_SOLO, MODE_LIVE,
                          ORIGIN_API, ORIGIN_LOG)

RAW_PATH = os.path.join(PROJ, 'data', 'untapped_api_raw.json')

#: Untapped 账号标识 —— **不进仓库**（2026-10-05 发布审查 P0-1）：
#: 环境变量 ``UNTAPPED_USER_ID`` / ``UNTAPPED_PLAYER_ID``，或
#: ``data/untapped_ids.json``（``{"user_id": ..., "player_id": ...}``，已 gitignore）。
_IDS_FILE = os.path.join(PROJ, 'data', 'untapped_ids.json')


def _load_ids():
    uid = os.environ.get('UNTAPPED_USER_ID')
    pid = os.environ.get('UNTAPPED_PLAYER_ID')
    if uid and pid:
        return str(uid), str(pid)
    try:
        with open(_IDS_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return d.get('user_id'), d.get('player_id')
    except Exception:
        return None, None


USER_ID, PLAYER_ID = _load_ids()


def _api_url():
    if not USER_ID or not PLAYER_ID:
        raise RuntimeError(
            'Untapped 账号标识未配置：设环境变量 UNTAPPED_USER_ID / '
            'UNTAPPED_PLAYER_ID，或建 data/untapped_ids.json '
            '{"user_id": "...", "player_id": "..."}（见 README）')
    return ('https://api.ygom.untapped.gg/api/v1/games/users/'
            '%s/players/%s/free' % (USER_ID, PLAYER_ID))

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/153.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://ygom.untapped.gg/',
    'Origin': 'https://ygom.untapped.gg',
}

#: game_mode 名称（完整枚举从 Untapped 网页 JS 里抠出来，见 GAME_MODE_NAMES）
MODE_NAMES = GAME_MODE_NAMES

#: API 的 match_start 比日志里的 `Starting Duel` 晚约 10 秒，匹配窗口放宽到 180 秒
MATCH_WINDOW = 180


# ------------------------------------------------------------------ 抓取
def http_get(url, timeout=90, retries=4):
    last = None
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            last = 'HTTP %s %s' % (e.code, e.reason)
            if e.code == 404:
                raise RuntimeError('API 404 —— 端点或 user/player id 变了')
            if e.code in (401, 403):
                raise RuntimeError('API %s —— 端点可能改为需要鉴权' % e.code)
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
        if i < retries:
            import time
            time.sleep(2 * i)
    raise RuntimeError('抓取失败（重试 %d 次）：%s' % (retries, last))


def parse_games(text):
    """兼容双重编码 / {'data': [...]} 包裹。"""
    data = json.loads(text)
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        for k in ('data', 'games', 'results', 'matches'):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        raise RuntimeError('API 返回的不是列表：%s' % type(data))
    return data


def fetch(out_path=RAW_PATH):
    """抓一次原始 JSON 落盘（**原子写** —— ★ BUG-B9A-12）。

    2.2MB 直接覆盖时若写到一半崩，`load_api` 会解析出一份坏文件、报出
    「API 返回异常」这种误导性错误。现在要么全成功、要么原文件不变。
    """
    text = http_get(_api_url())
    games = parse_games(text)                 # 先校验完整性，再落盘
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if not atomic.write_text(out_path, text):
        raise RuntimeError('写入失败：%s（原文件未改动）' % out_path)
    return out_path, games


def load_api(path=RAW_PATH):
    with open(path, encoding='utf-8') as f:
        return parse_games(f.read())


# ------------------------------------------------------------------ 对账
def _local_dt(ts):
    return datetime.datetime.fromtimestamp(ts)


def _api_result(m):
    return 'Win' if m.get('friendly_team_id') == m.get('winning_team_id') else 'Lose'


def _api_mode(gm):
    """API 的 game_mode -> 本项目的 coarse mode。"""
    if gm == 9:                 # SOLO_SINGLE
        return MODE_SOLO
    if gm in (6, 7):            # AUDIENCE / REPLAY
        return MODE_LIVE
    return MODE_PVP


def api_to_row(g):
    """把一条 API 对局映射成本项目 matches 表的一行。

    ⚠️ 字段对应关系（踩过坑）：
      * `friendly_team_id`       -> `seat`（我方座位号）
      * `initiative_player_id`   -> `won_initiative`（**硬币/选择权**）
      * `duels[0].first_player_id` -> `went_first` / `first_player`（**实际先攻**）
      * `match_start` 比日志的 `Starting Duel` 晚约 10 秒 —— 判重靠
        `Storage.find_match_near()`，不要用精确时间相等
      * `match_id` 用 API 的 UUID，加 `api:` 前缀，和日志解析的 sha1 主键区分开
    """
    ft = g.get('friendly_team_id')
    d0 = (g.get('duels') or [{}])[0]
    ts = g.get('match_start') or 0
    gm = g.get('game_mode')
    return {
        'match_id': 'api:%s' % g.get('match_id'),
        'started_at': datetime.datetime.fromtimestamp(ts).strftime(
            '%Y-%m-%d %H:%M:%S.000'),
        'ended_at': None,          # API 没有结束时刻；日志那边也没见过这局
        'my_deck_id': g.get('friendly_deck_id'),
        'my_deck_name': g.get('friendly_deck_name'),
        'deck_source': ORIGIN_API,
        'mode': _api_mode(gm),
        'game_mode': gm,
        'origin': ORIGIN_API,
        'seat': ft,
        'seat_evidence': 'api',
        'won_initiative': (g.get('initiative_player_id') == ft),
        'went_first': (d0.get('first_player_id') == ft),
        'first_player': d0.get('first_player_id'),
        'result': _api_result(g),
        'total_turns': d0.get('turn_count'),
        'duration_sec': d0.get('duel_duration_seconds'),
        'opp_deck_id': None,
        'opp_deck_name': None,
        'opp_deck_note': None,
        'opp_player_name': g.get('opponent_player_name'),
        # 多账号（2026-10-03 用户：API 补的局都是「示例玩家」的号）——
        # 每局自带 friendly_player_name，入库顺手盖章（upsert 只补空）
        'account_name': g.get('friendly_player_name'),
        'my_rank_before': g.get('friendly_rank_before'),
        'my_rate_before': g.get('friendly_rate_before'),
        'my_rank_after': g.get('friendly_rank_after'),
        'my_rate_after': g.get('friendly_rate_after'),
        'opp_rank_before': g.get('opponent_rank_before'),
        'opp_rate_before': g.get('opponent_rate_before'),
        'finish_type': d0.get('finish_type'),
        'opp_revealed': [],
        'opp_revealed_ids': [],
    }


def import_games(storage, games, since=None, until=None, log=print):
    """把 API 里**库里还没有**的对局补录进来。返回 (补录数, 已存在数, 跳过数)。

    判重分两层，缺一不可：

    1. **`match_id` 精确命中**（`api:<uuid>`）→ 已经补录过，跳过。
       这样反复跑不会重复插入。
    2. **日志行时间邻近**（`find_match_near(..., origin='log')`）→ 这局日志
       已经采到了，不要再补一份。

    ⚠️ 第 2 步**只跟日志行比**，且窗口很窄（±30 秒）：实测同一天连着打的两局
    最短只隔 **43 秒**，窗口一放宽就会把两局误判成同一局（真的踩过：16 局只补进 12 局）。
    """
    n_new = n_have = n_skip = 0
    for g in sorted(games, key=lambda m: m['match_start']):
        d = _local_dt(g['match_start']).date()
        if since and d < since:
            n_skip += 1
            continue
        if until and d > until:
            n_skip += 1
            continue
        row = api_to_row(g)
        if storage.get_match(row['match_id']) is not None:
            n_have += 1
            continue
        if storage.find_match_near(row['started_at'], origin=ORIGIN_LOG) is not None:
            n_have += 1
            continue
        storage.upsert_match(row)
        n_new += 1
    log('[补录] 新入库 %d 局，库里已有 %d 局，按日期跳过 %d 局'
        % (n_new, n_have, n_skip))
    return n_new, n_have, n_skip


def _db_ts(row):
    return datetime.datetime.strptime(row['started_at'][:19],
                                      '%Y-%m-%d %H:%M:%S').timestamp()


def check(storage, games, log=print):
    """把库里的每一局与 API 逐条对账。返回差异条数。"""
    api = sorted(games, key=lambda m: m['match_start'])
    rows = sorted(storage.list_matches(limit=None),
                  key=lambda r: r['started_at'])
    n_diff = 0
    not_in_api = []
    for r in rows:
        t = _db_ts(r)
        near = [m for m in api if abs(m['match_start'] - t) <= MATCH_WINDOW]
        if not near:
            not_in_api.append(r)
            continue
        m = min(near, key=lambda x: abs(x['match_start'] - t))
        a_res, a_turns = _api_result(m), (m.get('duels') or [{}])[0].get('turn_count')
        a_mode = MODE_NAMES.get(m.get('game_mode'), 'mode=%s' % m.get('game_mode'))
        bits = ['%s vs %s' % (a_mode, m.get('opponent_player_name'))]
        if a_res != r['result']:
            bits.append('**结果不一致 API=%s 库=%s**' % (a_res, r['result']))
            n_diff += 1
        if a_turns is not None and a_turns != r['total_turns']:
            bits.append('**回合不一致 API=%s 库=%s**' % (a_turns, r['total_turns']))
            n_diff += 1
        log('   %-19s %-6s <- %s' % (r['started_at'][:19], r['result'],
                                     '  '.join(bits)))
    log('')
    log('[对账] 与 API 命中的按上；不一致 %d 处' % n_diff)
    if not_in_api:
        log('[对账] API 里没有这 %d 局（观战/掉线/未上传 —— 不一定是问题）：'
            % len(not_in_api))
        for r in not_in_api:
            log('        %s  %s  mode=%s' % (r['started_at'][:19], r['result'],
                                             r['mode']))
    log('[对账] 库内 %d 局，API %d 局' % (len(rows), len(api)))
    return n_diff


# ------------------------------------------------------------------ main
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='抓 Untapped 公开 API + 与本库对账')
    ap.add_argument('--check', action='store_true', help='与 masterduel.db 对账')
    ap.add_argument('--apply', action='store_true',
                    help='把 API 的 game_mode 回填进库（统计只算 3=排名决斗 / 19=巅峰决斗）')
    ap.add_argument('--import-missing', action='store_true',
                    help='把 API 里、库里还没有的对局补录进库')
    ap.add_argument('--since', default=None, metavar='YYYY-MM-DD',
                    help='配合 --import-missing：只补录该日期之后的对局')
    ap.add_argument('--skip-fetch', action='store_true', help='用已落盘 JSON')
    ap.add_argument('--out', default=RAW_PATH, help='原始 JSON 落盘路径')
    args = ap.parse_args(argv)

    if args.skip_fetch:
        if not os.path.exists(args.out):
            print('[错误] 没有已落盘的 %s' % args.out)
            return 2
        games = load_api(args.out)
        print('[读取] %s（%d 条）' % (args.out, len(games)))
    else:
        path, games = fetch(args.out)
        days = sorted({_local_dt(m['match_start']).date() for m in games})
        print('[抓取] %d 条，覆盖 %s ~ %s' % (len(games), days[0], days[-1]))
        print('[抓取] 已保存 %s' % path)

    if not args.check and not args.apply and not args.import_missing:
        return 0
    st = Storage()
    st.init_schema()

    if args.import_missing:
        since = (datetime.date.fromisoformat(args.since) if args.since else None)
        import_games(st, games, since=since)

    if args.apply:
        hit, miss = st.sync_api_meta(games)
        print('[回填] API 元数据命中 %d 局；库里仍有 %d 局没有 game_mode'
              '（API 里没有这局）' % (hit, miss))
        s = st.get_stats()
        print('[统计] 只算排名决斗+巅峰决斗：%d 场 %d 胜 %d 负，胜率 %.1f%%'
              % (s['n_total'], s['n_win'], s['n_lose'], 100 * (s['win_rate'] or 0)))

    if not args.check:
        return 0
    print()
    print('=== 与 masterduel.db 对账 ===')
    return 1 if check(st, games) else 0


if __name__ == '__main__':
    sys.exit(main())
