# -*- coding: utf-8 -*-
"""import_duel_records —— 把自有对局记录（record_* 目录）解码入库（M3 主路径）。

流程（docs/对局记录格式.md）
----------------------------
1. 扫描 ``data/archive/duel_records/record_*/``；
2. **同局去重**：instance 相同 + capture 时间邻近 → 保留最完整一份，其余进
   ``merged_from``（目录 ≠ 一局 —— watcher 重启会给同一局新建目录）；
3. ``state=open``（半局/watcher 被杀）的**跳过并提示**，不把截断局当完整局入库；
4. 元数据配对注入 duel.json 的 meta（自动配对只补 NULL，manual 列过的可覆盖）：
   * ``game_mode``：manifest 快照（M6 契约）> duel_modes.jsonl（会话粒度，兜底）> NULL；
   * ``winbits/result/result_note``：end_messages.jsonl **双向窗 + 唯一认领**；
   * **与既有 log/api 行并档**：``find_record_pair`` 命中 → 只回填
     record_dir/winbits/result_note/result（origin 保持 log，防战绩翻倍）；
     配不上才新建 ``origin='record'`` 行（match_id = 'rec:' + record_id）。
5. ``--apply`` 才写 duel.json + 写库；默认 dry-run 只打印。

用法
----
    python tools/import_duel_records.py                 # dry-run，看配对计划
    python tools/import_duel_records.py --apply         # 落 duel.json + 入库
    python tools/import_duel_records.py --records-dir X --db Y
    python tools/import_duel_records.py --record record_XXX --apply   # 单目录增量
    python tools/import_duel_records.py --list-stale    # 列 state=open 的目录
    python tools/import_duel_records.py --finalize-stale --older-than 6h

M6 增量入口：采集器收局后对 final 目录调 ``--record <目录名> --apply``（幂等、
有指纹保护；失败只记日志不阻塞采集）。若同局还有其它目录，之后全量 import
仍会按 §3.2 并档，结果一致。open 目录的终局化通道见 ``--finalize-stale``
（P1-1：被 kill 留下的 open 局不收口就永远不入库）。

unknown：卡表里查不到的卡 id 会记进每局的 `summary.unknown_card_ids`（层 1，
由 core/duel_record.py 判定），报告里跨局汇总提示 —— **这是卡表过期的信号**，
不代表这里失败了。分类处置见 `python tools/build_card_names.py --check-update`。

幂等：重跑（层 0 指纹未变、meta 无新值可补）不写任何东西。
"""

import json
import os
import sys
from datetime import datetime as _dt
from datetime import timedelta as _td

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import duel_record
from core.memread.end_message import (arbitrate_result,
                                     result_from_duel_code)
from core.storage import DECK_SNAPSHOT_TRUSTED_MODES, Storage

DEFAULT_RECORDS = os.path.join(PROJ, 'data', 'archive', 'duel_records')
DEFAULT_END_MESSAGES = os.path.join(PROJ, 'data', 'archive', 'end_messages.jsonl')
DEFAULT_DUEL_MODES = os.path.join(PROJ, 'data', 'archive', 'duel_modes.jsonl')


# ---------------------------------------------------------------- 外部数据源

def load_end_messages(path=DEFAULT_END_MESSAGES):
    """end_messages.jsonl -> [{captured_at(datetime), ...原始字段}]（按时间序）。"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e['_dt'] = duel_record.parse_dt(e.get('captured_at'))
            if e['_dt'] is not None:
                out.append(e)
    out.sort(key=lambda e: e['_dt'])
    return out


def load_duel_modes(path=DEFAULT_DUEL_MODES):
    """duel_modes.jsonl -> [{start, end, game_mode}]（采样会话粒度，兜底源）。"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            row = {
                'start': duel_record.parse_dt(e.get('started_at')),
                'end': duel_record.parse_dt(e.get('ended_at')),
                'game_mode': e.get('game_mode'),
            }
            if row['game_mode'] is not None and row['start'] is not None:
                out.append(row)
    return out


def game_mode_from_duel_modes(first_seen, last_saved, modes):
    """duel 的 capture 区间与会话区间重叠 -> (game_mode, 来源注记)；配不上 (None, None)。"""
    if first_seen is None or last_saved is None:
        return None, None
    for m in modes:
        if m['start'] <= last_saved and (m['end'] or m['start']) >= first_seen:
            return m['game_mode'], 'duel_modes:%s' % duel_record.fmt_dt(m['start'])
    return None, None


def pair_end_messages(duels, entries, window=None):
    """end_messages -> 对局的唯一认领。

    * 窗口：``captured_at ∈ [first_seen − W, last_saved + W]``
      （区间重叠是其子集；one-shot/残影在结算后才建目录，必须双向）；
    * **显式 record_id 优先**（采集器落 EM 时戳上）：有它就直接认领那一局，
      不走下面的启发式 —— 单目录增量 import（``--record``）只带一局，
      原时间窗启发式会认领到相邻局的残影（2026-09-16 实机事故，见
      ``tools/duel_collector.py`` 的 ``_flush_em``）；
    * ★ **戳了 record_id 的条目只归那一局**：别人（含时间窗更近的）一律
      不许认领 —— 收局那一刻就 import 时本局 EM 还没落盘，光靠时间窗必然
      抢上一局的残影；
    * 一条 end_message 只被一局认领、一局最多认领一条；
    * 贪心规则：按 first_seen 序逐局挑「窗口内离 capture 区间最近」的一条，
      已被认领的条目跳过；同窗口多条取最近（同一局的两条结算文案是正常现象）。

    返回 ``claims = {record_id: entry}``。
    """
    w = duel_record.EM_WINDOW_SEC if window is None else window
    claims, used = {}, set()
    # 1) 精确认领：条目自带 record_id（采集器戳的）就认领它自己那一局
    alias = {}                      # 目录名（含同局别名）-> 组的 record_id
    for d in duels:
        for a in (d.get('aliases') or [d['record_id']]):
            alias[a] = d['record_id']
    for e in entries:
        rid = alias.get(e.get('record_id'))
        if rid and rid not in claims:
            claims[rid] = e
            used.add(id(e))
    # 2) 启发式兜底（只认**没戳过 id 的老条目**）
    for d in sorted(duels, key=lambda x: (x['first_seen'] or _dt.min)):
        if d['record_id'] in claims:
            continue
        cands = []
        for e in entries:
            if id(e) in used:
                continue
            if e.get('record_id'):
                continue            # 已戳 id = 别人的条目（见 docstring 铁律）
            t = e['_dt']
            if d['first_seen'] is None or d['last_saved'] is None:
                continue
            lo = d['first_seen'] - _td(seconds=w)
            hi = d['last_saved'] + _td(seconds=w)
            if lo <= t <= hi:
                # 离 [first_seen, last_saved] 区间的距离（越近越可信）
                if t < d['first_seen']:
                    dist = (d['first_seen'] - t).total_seconds()
                elif t > d['last_saved']:
                    dist = (t - d['last_saved']).total_seconds()
                else:
                    dist = 0.0
                cands.append((dist, t, e))
        if not cands:
            continue
        cands.sort(key=lambda x: (x[0], x[1]))
        best = cands[0][2]
        claims[d['record_id']] = best
        used.add(id(best))
    return claims


# ---------------------------------------------------------------- 主流程

def scan_all(records_dir):
    scans, broken = [], []
    if not os.path.isdir(records_dir):
        return scans, broken
    for name in sorted(os.listdir(records_dir)):
        path = os.path.join(records_dir, name)
        if not os.path.isdir(path) or not name.startswith('record_'):
            continue
        try:
            scans.append(duel_record.scan_record(path))
        except Exception as e:      # 坏目录：报出来，不炸整个导入
            broken.append((name, repr(e)))
    return scans, broken


#: `current-deck` 兜底的最大可信时长（秒）：状态文件的识别时刻与本局开始
#: 相差超过它就不再兜底 —— 卡组是能换的，残留状态兜到下一场就是错标
#: （2026-09-22 实测：残留的"白银城"兜到了第二天用闪刀姬打的局）。
MY_DECK_MAX_AGE_SEC = 24 * 3600


def _current_my_deck():
    """「当前卡组」状态（编辑界面读到的，`core.my_deck`）—— 没有给 None。"""
    try:
        from core import my_deck
        return my_deck.load() or None
    except Exception:                                        # noqa: BLE001
        return None


def _meta_display(storage, key):
    """meta 清单的 key（如 ``Blue-Eyes``）-> 中文显示名；查不到给 None。"""
    try:
        for d in (storage.search_meta_decks(key, limit=3) or []):
            if d.get('name') == key or not d.get('name'):
                return d.get('name_cn') or d.get('name_display') or d.get('name')
    except Exception:                                        # noqa: BLE001
        pass
    return None


def process(records_dir=DEFAULT_RECORDS, storage=None, apply=False,
            em_path=DEFAULT_END_MESSAGES, dm_path=DEFAULT_DUEL_MODES,
            only=None):
    """主流程。返回报告 dict；``apply=False`` 时不写 duel.json、不写库。

    :param only: 只处理含该目录名的局（M6 采集器收局后的增量入口）。
        分组仍在**全量扫描**结果上做 —— 同局多目录的并档关系不丢；
        没有匹配目录时报 ``not-found``（空库/手滑都看得见）。
    """
    # ★ 先保证库结构是最新的（老库缺后来加的列就 ALTER 补上）。
    # 为什么必须在这儿：采集器**每局**都直接调本函数入库，它不会自己跑迁移 ——
    # 2026-09-16 事故：加了 `my_deck_name_src` 之后这里没补列，
    # 于是每局入库都 OperationalError（表现为「打完一局最近对局不显示」）。
    if storage is None:
        storage = Storage()
    storage.init_schema()
    scans, broken = scan_all(records_dir)
    groups = duel_record.group_duels(scans)
    if only is not None:
        groups = [g for g in groups if any(s['name'] == only for s in g)]
    entries = load_end_messages(em_path)
    modes = load_duel_modes(dm_path)

    duel_metas = []
    for g in groups:
        main = duel_record.pick_main(g)
        duel_metas.append({
            'record_id': main['name'],
            'first_seen': main['first_seen'],
            'last_saved': main['last_saved'],
            'dir': main['dir'],
            # 同局可能有多个目录（日志轮转/重启）—— EM 上戳的 id 可能是
            # 非主目录，精确认领要认得出来（认到本局即可，claim 键仍是主目录）
            'aliases': [s['name'] for s in g],
        })
    claims = pair_end_messages(duel_metas, entries) if entries else {}
    report = {'broken': broken, 'duels': [], 'new_rows': 0, 'merged': 0,
              'skipped_open': 0, 'skipped_open_dirs': [], 'written_json': 0,
              'unknown_card_ids': []}
    if only is not None and not groups:
        report['duels'].append({'record_id': only, 'action': 'not-found'})
    storage = storage or Storage()
    unknown_all = set()          # M4：跨局合并的未知卡 id

    for g in groups:
        main = duel_record.pick_main(g)
        rid = main['name']
        item = {'record_id': rid, 'merged_from': [], 'state': main['state']}

        if main['state'] != 'final':
            item['action'] = 'skipped-open'
            report['skipped_open'] += 1
            report['skipped_open_dirs'].append(rid)   # P1-1：看得见才收得了口
            report['duels'].append(item)
            continue

        duel = duel_record.decode_record(main)
        duel['merged_from'] = sorted(s['name'] for s in g if s['name'] != rid)
        item['merged_from'] = duel['merged_from']
        # M4：卡表里查不到的卡 id（P1-2：必须出现在常驻路径的日志里）
        item['unknown_card_ids'] = list(
            duel['summary'].get('unknown_card_ids') or [])
        unknown_all.update(item['unknown_card_ids'])

        meta_updates = {}
        sources = duel['meta'].setdefault('sources', {})

        # -- game_mode：manifest 快照（decode 已填）> duel_modes 兜底
        if duel['meta'].get('game_mode') is None:
            mode, src = game_mode_from_duel_modes(
                main['first_seen'], main['last_saved'], modes)
            if mode is not None:
                meta_updates['game_mode'] = mode
                sources['duel_modes'] = src

        # -- 与既有 log/api 行并档（防战绩翻倍）
        # ⚠️ 该局已有自己的 record 行（match_id='rec:'+rid）时**绝不并档**：
        # 这是重跑 import 的幂等前提。实机教训（09-14）：重跑回填时宽窗把
        # 重打局拽进了 30 分钟内另一局（第一次尝试）的 log 行，旧 Win 被
        # 新 Lose 覆盖。
        own = storage.get_match('rec:' + rid)
        if own is not None:
            pair, stage = None, 'own-record-row'
            # 沿用自己行里已入库的时间锚——否则 to_row 出行 started_at=None，
            # upsert 会走 skipped-no-time，幂等更新（如回填 opp_player_name）
            # 根本不会发生。
            duel['meta']['started_at'] = own['started_at']
            duel['meta']['started_at_src'] = own.get('started_at_src') or 'own-row'
        else:
            pair, stage = storage.find_record_pair(
                duel_record.fmt_dt(main['first_seen']))
        if pair:
            item['pair'] = {'match_id': pair['match_id'], 'stage': stage}
            meta_updates['started_at'] = pair['started_at']
            meta_updates['started_at_src'] = 'log'
            meta_updates['ended_at'] = pair.get('ended_at')
            meta_updates['duration_sec'] = pair.get('duration_sec')
            sources['log'] = pair['match_id']
        else:
            item['pair'] = {'stage': stage}
            if own is None and duel['meta'].get('started_at') is None:
                # 采集侧近似（first_seen ≈ 开局，watcher 口径）
                meta_updates['started_at'] = duel_record.fmt_dt(main['first_seen'])
                meta_updates['started_at_src'] = 'first_seen_approx'

        # -- 结束时刻（★ 2026-09-17：record 行也补上）
        # 为什么必需：切轮判据比的是「上一局**结束** → 本局**开局**」——
        # 只比两个开局会被**超长对局**骗（上一局打了 110 分钟，下一局开局
        # 看上去就"隔了 115 分钟"，其实只歇了 5 分钟）。
        # record 来源（内存采集）没有日志的 ``Game ended`` 行，所以拿
        # **记录目录自己的采集区间**当锚：`capture.last_saved_at` = 最后一次
        # 落盘时刻 ≈ 对局结束（实测 16:16:02 开局 / 16:17:16 落盘）。
        # ⚠️ 只补**空**的（有日志 ended_at 时上面已填、更准）；
        # ⚠️ 不会撞「掉线判据」：那条要 `game_mode IS NULL`，而 record 行的
        #    game_mode 必有值（采集时从内存读的）。
        cap = duel.get('capture') or {}
        if (meta_updates.get('ended_at') or duel['meta'].get('ended_at')) is None:
            saved = cap.get('last_saved_at')
            if saved and not cap.get('approx'):
                meta_updates['ended_at'] = saved
                meta_updates.setdefault('duration_sec', None)
                sources['capture'] = saved

        # -- end_messages 认领 → winbits / result / 文案备注
        em = claims.get(rid)
        if em is not None:
            sources['end_messages'] = em.get('captured_at')
            item['end_message'] = em.get('captured_at')
            if em.get('winbits'):
                meta_updates['winbits'] = em['winbits']     # 证据照记
            text = (em.get('texts') or {}).get('messageText')
            #: 🔴 2026-10-03：**三判据投票仲裁**（投降局 winbit 可能是上一局
            #: 残留——记录 20261003_193726 自己投降却被记胜）。文案+结算码
            #: 两票一致时压过陈旧的 winbit；平票维持 winbit > duel_code。
            r, src_, note = arbitrate_result(em.get('winbits'),
                                             em.get('duel_code'), text)
            if r:
                meta_updates['result'] = r.capitalize()
                meta_updates['result_src'] = src_
            if note:
                meta_updates['result_note'] = note
            if text:
                meta_updates['result_note'] = (
                    '%s%s' % (text, '；' + note if note else ''))

        # -- 我方卡组兜底（2026-09-16 用户口径）：编辑界面读过一次的「当前卡组」，
        #    该局自己没读到牌时沿用。**活动局同样要填** —— 用户澄清「天梯已经能自动拿，
        #    这套就是给活动对局用的」，且实机核实过：骰子拉力的活动卡组位 `DRDeck`
        #    里就是他自组的那套（41 主 + 15 额外，与编辑界面读到的卡一致）。
        #    ⚠️ 只在该局**没读到自己的牌**、且**本局不早于记录时刻**时才补（别改写历史局）。
        if not (meta_updates.get('my_deck_name') or duel['meta'].get('my_deck_name')):
            cur = _current_my_deck()
            # ★ 09-28：`current-deck` 兜底只对**天梯（mode=3）**生效 ——
            #   天梯/巅峰是两个独立的卡组槽位（现场枚举 ClientWork 实锤），
            #   巅峰局的「当前卡组」（=天梯那份）不是当局实际用的牌，兜了必错标。
            gm = (duel['meta'].get('game_mode')
                  or meta_updates.get('game_mode'))
            if cur and gm == 19:      # 只禁巅峰（09-28 双槽位实锤）；
                cur = None            # 活动局(21/DRDeck)维持 09-16 口径照兜
            started = meta_updates.get('started_at') or duel['meta'].get('started_at')
            fresh = (not cur) or not cur.get('at') or not started \
                or str(started) >= str(cur['at'])
            # ★ 2026-09-22 实测事故：`my_deck.json` 残留上一场的卡组名（用户换了
            #   卡组但没进编辑界面 / 大厅识别还没纠正），这场局就被 `current-deck`
            #   兜底**错标**。兜底只对「识别时刻之后不久」的局可信 ——
            #   状态文件太旧就不许再兜（宁可留空，别标错）。
            stale = False
            if cur and cur.get('at') and started:
                try:
                    import datetime as _dt
                    at = _dt.datetime.strptime(str(cur['at'])[:19],
                                               '%Y-%m-%d %H:%M:%S')
                    st = _dt.datetime.strptime(str(started)[:19],
                                               '%Y-%m-%d %H:%M:%S')
                    stale = abs((st - at).total_seconds()) > MY_DECK_MAX_AGE_SEC
                except (TypeError, ValueError):
                    stale = False
            if cur and fresh and not stale:
                meta_updates['my_deck_name'] = (_meta_display(storage, cur['name'])
                                                or cur['name'])
                meta_updates['my_deck_name_src'] = 'current-deck'

        # -- went_first：seat 判出后才能映射（不猜）
        seat = duel['meta'].get('seat')
        if seat is not None and duel['summary'].get('first_player') is not None:
            duel['summary']['went_first'] = bool(
                int(seat) == int(duel['summary']['first_player']))

        # -- 手坑两列（2026-10-16）：seat 判出后映射（我/敌）；判不出两列 NULL
        #    ⚠️ hand_trap_sides 返回 (None, None) = 判不出、( [], [] ) = 没用过。
        ht_mine, ht_opp = duel_record.hand_trap_sides(
            duel['summary'].get('hand_traps'), seat)
        item['hand_traps'] = {
            'my': len(ht_mine) if ht_mine is not None else None,
            'opp': len(ht_opp) if ht_opp is not None else None}

        # meta_updates 同步回内存 dict（write_duel_json 落盘 / to_row 出行都要用）
        for k, v in meta_updates.items():
            if v is not None:
                duel['meta'][k] = v

        item['meta_updates'] = {k: v for k, v in meta_updates.items()
                                if k != 'sources'}
        if not apply:
            item['action'] = 'planned'
            report['duels'].append(item)
            continue

        status = duel_record.write_duel_json(main['dir'], duel,
                                             meta_updates=meta_updates)
        item['json'] = status
        item['action'] = 'applied'
        if status != 'skipped':
            report['written_json'] += 1

        if pair:
            actions = storage.apply_record_meta(
                pair['match_id'],
                record_dir=rid,
                winbits=meta_updates.get('winbits'),
                result=meta_updates.get('result'),
                result_src=meta_updates.get('result_src'),
                result_note=meta_updates.get('result_note'),
                my_traps=ht_mine, opp_traps=ht_opp)
            item['db'] = {'merged_into': pair['match_id'], 'actions': actions}
            report['merged'] += 1
        else:
            row = duel_record.to_row(duel)
            if not row.get('started_at'):
                item['db'] = {'upsert': 'skipped-no-time'}
            else:
                existed = storage.get_match(row['match_id']) is not None
                res = storage.upsert_match(row, judge_lack_end=False)
                item['db'] = {'upsert': 'updated' if existed else res,
                              'match_id': row['match_id']}
                if res == 'written' and not existed:
                    report['new_rows'] += 1
        report['duels'].append(item)
    report['unknown_card_ids'] = sorted(unknown_all)   # 跨局合并去重
    return report


def _fmt_ids(ids, limit=8):
    """卡 id 列表压成一行（超了截断，绝不吞信息）。"""
    shown = '、'.join(str(i) for i in ids[:limit])
    return shown + (' …等共 %d 个' % len(ids) if len(ids) > limit else '')


def _print_report(report):
    if report['broken']:
        print('⚠️ 坏目录 %d 个：' % len(report['broken']))
        for name, err in report['broken']:
            print('   %s -> %s' % (name, err))
    print('同局分组：%d 局（state=open 跳过 %d）'
          % (len(report['duels']), report['skipped_open']))
    if report.get('skipped_open_dirs'):
        print('  open 未入库：%s（收口用 --finalize-stale 或采集器启动自愈）'
              % ', '.join(report['skipped_open_dirs']))
    for it in report['duels']:
        line = '· %s' % it['record_id']
        if it.get('merged_from'):
            line += '（并入 %s）' % '、'.join(it['merged_from'])
        line += ' [%s]' % it.get('action') or ''
        print(line)
        if it.get('pair'):
            print('  并档：%s（%s）' % (it['pair'].get('match_id') or '无',
                                       it['pair'].get('stage')))
        if it.get('end_message'):
            print('  end_message：' + it['end_message'])
        upd = it.get('meta_updates') or {}
        if upd:
            print('  注入：' + json.dumps(upd, ensure_ascii=False))
        if it.get('hand_traps'):
            ht = it['hand_traps']
            print('  手坑：我方 %s / 对手 %s'
                  % ('—' if ht.get('my') is None else ht['my'],
                     '—' if ht.get('opp') is None else ht['opp']))
        if it.get('unknown_card_ids'):
            print('  ⚠️ 未知卡 id：%s' % _fmt_ids(it['unknown_card_ids']))
        if it.get('db'):
            print('  库：%s' % json.dumps(it['db'], ensure_ascii=False))
    unk = report.get('unknown_card_ids') or []
    if unk:
        print('⚠️ 卡表里查不到的卡 id %d 个：%s' % (len(unk), _fmt_ids(unk)))
        print('   卡表可能过期 —— 跑 `python tools/build_card_names.py --check-update`：'
              '它会分类告诉你是「上游已收录（跑 --update 可解）」'
              '还是「上游也没有，需重出游戏卡表 card_data.json」')
    print('小结：新建 %d 行 / 并档 %d 局 / duel.json 写 %d 份'
          % (report['new_rows'], report['merged'], report['written_json']))


# ------------------------------------------------- open 目录终局化（M6 P1-1）

def parse_span(s):
    """'21600' / '6h' / '30m' -> 秒。"""
    s = str(s).strip().lower()
    try:
        if s.endswith('h'):
            return float(s[:-1]) * 3600
        if s.endswith('m'):
            return float(s[:-1]) * 60
        return float(s)
    except ValueError:
        raise SystemExit('--older-than 解不了：%r（秒或 6h/30m 形式）' % s)


def _human_age(sec):
    if sec is None:
        return '?'
    sec = int(sec)
    if sec < 90:
        return '%ds' % sec
    if sec < 5400:
        return '%dm' % round(sec / 60)
    return '%.1fh' % (sec / 3600.0)


def _stale_cmd(records_dir, finalize, older_than, only=None):
    """--list-stale / --finalize-stale：open 目录的可见性与收口。"""
    from core.memread import record_capture as rc
    scans, broken = scan_all(records_dir)
    for name, err in broken:
        print('⚠️ 坏目录 %s -> %s' % (name, err))
    open_dirs = [s for s in scans if s['state'] == 'open']
    if only is not None:
        open_dirs = [s for s in open_dirs if s['name'] == only]
    if not open_dirs:
        print('state=open 的目录：无')
        return 0
    now = _dt.now()
    stale = rc.find_stale_open(open_dirs, now, older_than_sec=older_than)
    print('state=open 的目录 %d 个：' % len(open_dirs))
    for s in sorted(open_dirs, key=lambda x: x['last_saved'] or _dt.min):
        age = ((now - s['last_saved']).total_seconds()
               if s['last_saved'] else None)
        print('  %s  instance=%s size=%d  last_saved=%s（%s 前）%s'
              % (s['name'], hex(s['instance']) if s['instance'] else '-',
                 s['logbase_size'], duel_record.fmt_dt(s['last_saved']),
                 _human_age(age),
                 '-> stale' if s['name'] in stale else ''))
    if not finalize:
        return 0
    done = 0
    for s in open_dirs:
        if s['name'] in stale and rc.finalize_open(s['dir'],
                                                   reason='stale-open'):
            done += 1
            print('已标 final：%s（finalized_by=stale-open）' % s['name'])
    print('补标 final %d 个' % done)
    return 0



def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true', help='写 duel.json + 入库')
    ap.add_argument('--record', metavar='DIR_NAME',
                    help='只处理含该目录的局（M6 采集器的增量入口；'
                         '配 --apply 才写库）')
    ap.add_argument('--list-stale', action='store_true',
                    help='列出 state=open 的目录（含最后落盘时间）')
    ap.add_argument('--finalize-stale', action='store_true',
                    help='把 stale 的 open 目录补标 final（判据 --older-than；'
                         'P1-1：不收口就永远不入库）')
    ap.add_argument('--older-than', default='6h',
                    help='--finalize-stale 的时限（秒或 6h/30m 形式，默认 6h）')
    ap.add_argument('--records-dir', default=DEFAULT_RECORDS)
    ap.add_argument('--em-path', default=DEFAULT_END_MESSAGES)
    ap.add_argument('--dm-path', default=DEFAULT_DUEL_MODES)
    ap.add_argument('--db', default=None, help='SQLite 路径（默认 data/masterduel.db）')
    args = ap.parse_args(argv)

    if args.list_stale or args.finalize_stale:
        return _stale_cmd(records_dir=args.records_dir,
                          finalize=args.finalize_stale,
                          older_than=parse_span(args.older_than),
                          only=args.record)
    storage = Storage(args.db) if args.db else Storage()
    report = process(records_dir=args.records_dir, storage=storage,
                     apply=args.apply, em_path=args.em_path,
                     dm_path=args.dm_path, only=args.record)
    _print_report(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
