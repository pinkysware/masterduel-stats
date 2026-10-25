# -*- coding: utf-8 -*-
"""tools/import_duel_records.py 的单测（M3 入库主路径）。

用 fixture record 目录 + 临时库，覆盖：同局去重、state=open 跳过、
log 行并档（不新增行）、end_messages 认领注入、duel_modes 兜底、幂等重跑。
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import duel_collector as dc                                       # noqa: E402
import import_duel_records as imp                                 # noqa: E402
from core.storage import Storage                                  # noqa: E402

def read_json(path):
    """读 JSON（用 with —— 别在测试里留 ResourceWarning）。"""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


FIX_MIN = os.path.join(PROJ, 'tests', 'fixtures', 'duel_records',
                       'record_20260913_122146')


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        self.db = os.path.join(self.tmp, 'test.db')
        self.em = os.path.join(self.tmp, 'end_messages.jsonl')
        self.dm = os.path.join(self.tmp, 'duel_modes.jsonl')
        os.makedirs(self.records)
        self.storage = Storage(self.db)
        self.storage.init_schema()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 工具 ---------------------------------------------------------

    def make_record(self, name, instance='0x1cdf158f3f0', saved_at=None,
                    state=None, versioned=False, my_deck=None):
        """从最小 fixture 拷一份 record，改目录名/instance/saved_at。"""
        dst = os.path.join(self.records, name)
        shutil.copytree(FIX_MIN, dst, copy_function=shutil.copy2)
        man_path = os.path.join(dst, 'manifest.json')
        with open(man_path, 'r', encoding='utf-8') as f:
            man = json.load(f)
        man['instance'] = instance
        if saved_at:
            man['saved_at'] = saved_at
        if versioned:
            man['manifest_version'] = 1
            # record_YYYYMMDD_HHMMSS -> 'YYYY-MM-DD HH:MM:SS'（第 15 位是下划线）
            fs = (name[7:11] + '-' + name[11:13] + '-' + name[13:15]
                  + ' ' + name[16:18] + ':' + name[18:20] + ':'
                  + name[20:22])
            man['capture'] = {'first_seen_at': fs,
                              'last_saved_at': saved_at or fs}
            if state:
                man['state'] = state
            # state 不给 = 契约 v1 违反（有版本号没 state）-> open
        if my_deck:
            man['my_deck_snapshot'] = my_deck
        with open(man_path, 'w', encoding='utf-8', newline='') as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        return dst

    def write_em(self, entries):
        with open(self.em, 'w', encoding='utf-8', newline='') as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')

    def write_dm(self, rows):
        with open(self.dm, 'w', encoding='utf-8', newline='') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    def run_import(self, apply=False):
        return imp.process(records_dir=self.records, storage=self.storage,
                           apply=apply, em_path=self.em, dm_path=self.dm)

    def row_count(self):
        # ⚠️ `with sqlite3.connect(...)` **不关连接**（只管提交/回滚）——
        # 在 -X dev 下会报 unclosed database，所以显式 close。
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
        finally:
            conn.close()

    def row_by_id(self, mid):
        return self.storage.get_match(mid)


class TestPairingRules(unittest.TestCase):
    """EM 配对铁律（2026-09-16 误配事故）：戳了 record_id 的条目只归那一局。

    事故形态：骰子拉力连打 + 采集器「收局那一刻就 import」（此时本局 EM 还没
    落盘）→ 时间窗启发式认领了**上一局的残影** → 胜局写成 Lose、文案也串了。
    """

    def _e(self, cap, rid=None, **kw):
        e = {'captured_at': cap, '_dt': imp.duel_record.parse_dt(cap)}
        if rid:
            e['record_id'] = rid
        e.update(kw)
        return e

    def _d(self, name, first, last, aliases=None):
        return {'record_id': name,
                'first_seen': imp.duel_record.parse_dt(first),
                'last_saved': imp.duel_record.parse_dt(last),
                'aliases': aliases}

    def test_exact_id_beats_nearer_residue(self):
        """本局 EM 更远也得认领自己那条（残影不许抢）。"""
        prev = self._e('2026-09-16 11:52:53', rid='record_prev',
                       winbits=[0, 1])
        mine = self._e('2026-09-16 11:57:40', rid='record_mine',
                       winbits=[1, 0])
        duels = [self._d('record_mine', '2026-09-16 11:53:28',
                         '2026-09-16 11:57:22')]
        claims = imp.pair_end_messages(duels, [prev, mine])
        self.assertIs(claims['record_mine'], mine)

    def test_when_own_em_missing_claim_nothing(self):
        """★ 时序漏洞：收局就 import、本局 EM 还没落盘 -> 宁缺勿串。"""
        prev = self._e('2026-09-16 11:52:53', rid='record_prev',
                       winbits=[0, 1])
        duels = [self._d('record_mine', '2026-09-16 11:53:28',
                         '2026-09-16 11:57:22')]
        claims = imp.pair_end_messages(duels, [prev])
        self.assertEqual(claims, {})

    def test_alias_dir_claims_the_group(self):
        """同局多目录：EM 戳的是非主目录，也要认到本局（claim 键是主目录）。"""
        e = self._e('2026-09-16 12:00:00', rid='record_alt')
        duels = [self._d('record_main', '2026-09-16 11:59:00',
                         '2026-09-16 12:00:30',
                         aliases=['record_main', 'record_alt'])]
        claims = imp.pair_end_messages(duels, [e])
        self.assertIs(claims['record_main'], e)

    def test_legacy_entry_still_uses_window(self):
        """老条目（没戳 id）行为不变 —— 100+ 条历史 EM 靠这条。"""
        e = self._e('2026-09-16 12:00:00')
        duels = [self._d('record_main', '2026-09-16 11:59:00',
                         '2026-09-16 12:00:30')]
        claims = imp.pair_end_messages(duels, [e])
        self.assertIs(claims['record_main'], e)


class TestDedupAndState(Base):

    def test_two_dirs_same_duel_merge(self):
        """watcher 重启给同一局建了两个目录 -> 一局一条，merged_from 记录。"""
        self.make_record('record_20260913_120100', instance='0xaaa',
                         saved_at='2026-09-13 12:01:30')
        self.make_record('record_20260913_120200', instance='0xaaa',
                         saved_at='2026-09-13 12:02:00')
        report = self.run_import(apply=True)
        self.assertEqual(len(report['duels']), 1)
        self.assertEqual(report['duels'][0]['merged_from'],
                         ['record_20260913_120100'])
        self.assertEqual(self.row_count(), 1)
        row = self.row_by_id('rec:record_20260913_120200')   # saved_at 最晚为主
        self.assertIsNotNone(row)
        self.assertEqual(row['origin'], 'record')

    def test_state_open_skipped(self):
        """契约 v1 没写 state = 半局 -> 跳过，不入库。"""
        self.make_record('record_20260913_120100', versioned=True,
                         state=None)
        report = self.run_import(apply=True)
        self.assertEqual(report['skipped_open'], 1)
        self.assertEqual(self.row_count(), 0)


class TestPairing(Base):

    def test_paired_log_row_deck_untouched(self):
        """并档到 log 行时**不许碰卡组列**（终审清单 5 第 4 条）。"""
        self.storage.upsert_match({
            'match_id': 'logdeck', 'origin': 'log',
            'started_at': '2026-09-13 12:00:40.000',
            'ended_at': '2026-09-13 12:05:00.000',
            'result': 'Win', 'mode': 'pvp',
            'my_deck_id': '999', 'my_deck_name': '日志卡组',
            'deck_source': 'log'})
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:03:00')
        self.run_import(apply=True)
        row = self.row_by_id('logdeck')
        self.assertEqual(row['my_deck_id'], '999')
        self.assertEqual(row['my_deck_name'], '日志卡组')
        self.assertEqual(row['deck_source'], 'log')
        self.assertEqual(row['record_dir'], 'record_20260913_120045')

    def test_merge_into_log_row_no_new_row(self):
        """同局既有 log 行 -> 只回填 record_dir，不新增行（防战绩翻倍）。"""
        self.storage.upsert_match({'match_id': 'logx',
                                   'started_at': '2026-09-13 12:00:40.000',
                                   'ended_at': '2026-09-13 12:05:00.000',
                                   'result': 'Win', 'origin': 'log',
                                   'mode': 'pvp'})
        # 目录名 12:00:45 -> 紧窗 [12:00:15, 12:00:50] 命中 logx
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:03:00')
        report = self.run_import(apply=True)
        self.assertEqual(self.row_count(), 1)          # 关键断言
        self.assertEqual(report['merged'], 1)
        row = self.row_by_id('logx')
        self.assertEqual(row['record_dir'], 'record_20260913_120045')
        self.assertEqual(row['origin'], 'log')         # origin 保持 log

    def test_end_message_claim(self):
        """end_message 双向窗认领 -> winbits/result/文案注入。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_em([{'captured_at': '2026-09-13 12:05:30',
                        'winbits': [1, 0], 'mem_result': 'win',
                        'texts': {'messageText': '对手的LP已削减至0'}}])
        self.run_import(apply=True)
        duel = read_json(os.path.join(
            self.records, 'record_20260913_120045', 'duel.json'))
        self.assertEqual(duel['meta']['winbits'], [1, 0])
        self.assertEqual(duel['meta']['result'], 'Win')
        self.assertEqual(duel['meta']['result_note'], '对手的LP已削减至0')
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'winbit')

    def test_record_id_beats_neighbour_residue(self):
        """回归锁（2026-09-16 事故）：EM 带 record_id 时必须认领自己那条。

        场景 = 骰子拉力连打 + 单目录增量 import：上一局的 EM（残影）离得更近，
        纯时间窗启发式会认领它 -> 结果/文案全串（实机：胜局被写成 Lose）。
        """
        # 本局：first_seen 12:01:45 / last_saved 12:02:00
        self.make_record('record_20260913_120145', instance='0xccc',
                         saved_at='2026-09-13 12:02:00')
        self.write_em([
            # 上一局残影：离 first_seen 45s（比本局自己的 EM 更近）
            {'captured_at': '2026-09-13 12:01:00', 'winbits': [0, 1],
             'mem_result': 'lose',
             'texts': {'messageText': 'LP已削减至0'}},
            # 本局自己的 EM：离 last_saved 60s，但戳了 record_id
            {'captured_at': '2026-09-13 12:03:00', 'winbits': [1, 0],
             'mem_result': 'win', 'record_id': 'record_20260913_120145',
             'texts': {'messageText': '对手已投降'}}])
        self.run_import(apply=True)
        duel = read_json(os.path.join(
            self.records, 'record_20260913_120145', 'duel.json'))
        self.assertEqual(duel['meta']['winbits'], [1, 0])
        self.assertEqual(duel['meta']['result'], 'Win')
        self.assertEqual(duel['meta']['result_note'], '对手已投降')
        self.assertEqual(duel['meta']['sources']['end_messages'],
                         '2026-09-13 12:03:00')

    def test_migrates_old_db_schema(self):
        """★ 老库缺后来加的列 -> process() 先补列（2026-09-16 事故回归锁）。

        事故形态：给 matches 加列后，采集器那条入库路径不跑 init_schema，
        每局入库都 `no such column` -> 用户看到「打完一局最近对局不显示」。
        """
        self.make_record('record_20260916_150000', versioned=True,
                         state='final')
        with sqlite3.connect(self.db) as c:
            c.execute('ALTER TABLE matches DROP COLUMN my_deck_name_src')
        self.run_import(apply=True)          # 不该抛
        with sqlite3.connect(self.db) as c:
            cols = [r[1] for r in c.execute('PRAGMA table_info(matches)')]
        self.assertIn('my_deck_name_src', cols, '旧库必须被补列')

    def test_my_deck_filled_from_current_deck(self):
        """空名 -> 用「当前卡组」补（src=current-deck）。

        ★ 2026-10-25 用户拍板：**全模式放开**（自动识别没有卡组，再用手动填的）
        —— 天梯 / 巅峰 / 活动的用例分别在下面三个测试。
        """
        name = self.make_record('record_20260916_140000', versioned=True,
                                state='final')
        self._set_mode(name, 3)
        with mock.patch.object(imp, '_current_my_deck',
                               return_value={'name': 'Blue-Eyes',
                                             'at': '2026-09-16 13:00:00'}):
            self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260916_140000')
        self.assertEqual(row['my_deck_name'], 'Blue-Eyes')   # 临时库没 meta 清单 -> 落回 key
        self.assertEqual(row['my_deck_name_src'], 'current-deck')

    def test_my_deck_filled_for_peak_too(self):
        """★ 巅峰（19）也兜 —— 2026-10-25 放开（旧口径「只禁巅峰」作废：
        专用槽通道已通，空快照只剩 rare 场景，手动填的值即用户声明的当前
        卡组，错标随时可在悬浮窗卡图重选）。"""
        name = self.make_record('record_20260916_140050', versioned=True,
                                state='final')
        self._set_mode(name, 19)
        with mock.patch.object(imp, '_current_my_deck',
                               return_value={'name': 'Blue-Eyes',
                                             'at': '2026-09-16 13:00:00'}):
            self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260916_140050')
        self.assertEqual(row['my_deck_name'], 'Blue-Eyes')
        self.assertEqual(row['my_deck_name_src'], 'current-deck')

    def test_my_deck_filled_for_event_mode_too(self):
        """★ 活动局**同样**用「当前卡组」填 —— 用户澄清「这套就是给活动对局用的」，
        且实机核实骰子拉力的活动卡组位就是用户自组那套（41+15 与编辑界面一致）。"""
        name = self.make_record('record_20260916_140100', versioned=True,
                                state='final')
        self._set_mode(name, 21)
        with mock.patch.object(imp, '_current_my_deck',
                               return_value={'name': 'Blue-Eyes',
                                             'at': '2026-09-16 13:00:00'}):
            self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260916_140100')
        self.assertEqual(row['my_deck_name'], 'Blue-Eyes')
        self.assertEqual(row['my_deck_name_src'], 'current-deck')

    def test_my_deck_not_filled_for_older_match(self):
        """本局早于「当前卡组」记录时刻 -> **不补**（别改写历史局）。"""
        name = self.make_record('record_20260916_120000', versioned=True,
                                state='final')
        self._set_mode(name, 3)
        with mock.patch.object(imp, '_current_my_deck',
                               return_value={'name': 'Blue-Eyes',
                                             'at': '2026-09-16 13:00:00'}):
            self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260916_120000')
        self.assertIsNone(row['my_deck_name'])

    def _set_mode(self, name, mode):
        """把 fixture 的 manifest 补一个 game_mode（fixture 里是 None）。"""
        path = os.path.join(self.records, name, 'manifest.json')
        with open(path, encoding='utf-8') as f:
            man = json.load(f)
        man['game_mode'] = mode
        with open(path, 'w', encoding='utf-8', newline='') as f:
            json.dump(man, f, ensure_ascii=False, indent=1)

    def test_end_message_duel_code_fallback(self):
        """掉线局兜底（2026-09-16 拍板）：winbits 缺席 -> 结算码判胜负。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_em([{'captured_at': '2026-09-13 12:05:30',
                        'winbits': None, 'mem_result': None,
                        'duel_code': 1, 'duel_code_name': 'Win',
                        'duel_code_src': 'clientwork',
                        'texts': {'messageText':
                                  '由于网络中断，决斗已经结束'}}])
        self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'duel_code')
        self.assertIsNone(row['winbits'])

    def test_end_message_duel_code_loses_to_winbit(self):
        """优先级：winbits 能判出时结算码只当证据，不改写结果。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_em([{'captured_at': '2026-09-13 12:05:30',
                        'winbits': [1, 0], 'mem_result': 'win',
                        'duel_code': 2, 'duel_code_name': 'Lose',
                        'texts': {'messageText': '对手已投降'}}])
        self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'winbit')

    def test_end_message_residue_claimed_at_W300(self):
        """终审预期表钉死（W=300）：结算残影（em 比目录早 4.5 分钟）必须被认领。"""
        self.make_record('record_20260913_121829', instance='0xccc',
                         saved_at='2026-09-13 12:20:02')
        self.write_em([{'captured_at': '2026-09-13 12:13:57',
                        'winbits': [1, 0],
                        'texts': {'messageText': '对手的LP已削减至0'}}])
        self.run_import(apply=True)
        duel = read_json(os.path.join(
            self.records, 'record_20260913_121829', 'duel.json'))
        self.assertEqual(duel['meta']['winbits'], [1, 0])
        self.assertEqual(duel['meta']['result'], 'Win')

    def test_end_message_out_of_window(self):
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_em([{'captured_at': '2026-09-13 12:30:00',
                        'winbits': [1, 0]}])
        self.run_import(apply=True)
        duel = read_json(os.path.join(
            self.records, 'record_20260913_120045', 'duel.json'))
        self.assertIsNone(duel['meta']['winbits'])
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertIsNone(row['result'])               # 不硬猜

    def test_game_mode_from_duel_modes(self):
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_dm([{'started_at': '2026-09-13 11:55:00',
                        'ended_at': '2026-09-13 12:15:00',
                        'game_mode': 21}])
        self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertEqual(row['game_mode'], 21)
        self.assertEqual(row['mode'], 'pvp')

    def test_unfinished_not_forced_lose(self):
        """record 行判不出胜负不许被"没看到结束"判负。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.run_import(apply=True)
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertIsNone(row['result'])


    def test_opp_name_by_seat(self):
        """M6 定案（2/2 实机）：duelSettings names 按座位排 ->
        seat 判出后 opp = names[1-seat]；seat 判不出保持 NULL。"""
        dst = self.make_record('record_20260913_120045', instance='0xbbb',
                               saved_at='2026-09-13 12:10:00',
                               my_deck={'deck_id': '48600670',
                                        'deck_name': '异画',
                                        'ids': [21341, 12950]})
        mp = os.path.join(dst, 'manifest.json')
        man = read_json(mp)
        man['duel_settings'] = {'names': ['player1', '示例玩家'],
                                'names_direction': 'by-seat',
                                'game_mode': 3, 'duel_start_ts': 1789298552}
        with open(mp, 'w', encoding='utf-8', newline='') as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        self.run_import(apply=True)
        duel = read_json(os.path.join(dst, 'duel.json'))
        self.assertEqual(duel['meta']['seat'], 1)      # 同 TestSeatIntegration
        self.assertEqual(duel['meta']['opp_player_name'], 'player1')
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertEqual(row['opp_player_name'], 'player1')

    def test_opp_name_by_seat_without_seat_stays_null(self):
        """seat 判不出（无快照）-> 名字保持 NULL，不硬猜。"""
        dst = self.make_record('record_20260913_120045', instance='0xbbb',
                               saved_at='2026-09-13 12:10:00')
        mp = os.path.join(dst, 'manifest.json')
        man = read_json(mp)
        man['duel_settings'] = {'names': ['A', '示例玩家'],
                                'names_direction': 'by-seat',
                                'game_mode': 3, 'duel_start_ts': None}
        with open(mp, 'w', encoding='utf-8', newline='') as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        self.run_import(apply=True)
        duel = read_json(os.path.join(dst, 'duel.json'))
        self.assertIsNone(duel['meta']['seat'])
        self.assertIsNone(duel['meta']['opp_player_name'])


class TestUnknownCardIds(Base):
    """M4：unknown 卡 id 的出口（P1-2 —— 常驻路径也得看得见）。"""

    @staticmethod
    def _blind_table():
        """把卡名表换成空表 -> 本局用到的卡全判 unknown（确定性，不碰真表）。"""
        return mock.patch.object(imp.duel_record, 'card_name_table',
                                 lambda *a, **k: {})

    def _duel_json(self, name):
        with open(os.path.join(self.records, name, 'duel.json'),
                  encoding='utf-8') as f:
            return json.load(f)

    def test_report_layer1_and_hint(self):
        name = 'record_20260913_120045'
        self.make_record(name, instance='0xbbb', saved_at='2026-09-13 12:10:00')
        with self._blind_table():
            report = self.run_import(apply=True)
        unk = report['unknown_card_ids']
        self.assertTrue(unk, '空卡表 -> 不可能没有 unknown')
        self.assertEqual(unk, sorted(set(unk)), '要排序去重')
        self.assertEqual(report['duels'][0]['unknown_card_ids'], unk)
        # 层 1 duel.json 也记着（M7 直接消费这个字段）
        self.assertEqual(self._duel_json(name)['summary']['unknown_card_ids'], unk)
        # 提示段必须出现，而且要指向分类诊断（不是让人反复重跑 build）
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            imp._print_report(report)
        out = buf.getvalue()
        self.assertIn('卡表里查不到的卡 id', out)
        self.assertIn('--check-update', out)

    def test_union_across_duels(self):
        """两局各带 unknown -> 报告里是跨局合并去重的并集。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.make_record('record_20260913_130500', instance='0xccc',
                         saved_at='2026-09-13 13:15:00')
        with self._blind_table():
            report = self.run_import(apply=True)
        per = [d['unknown_card_ids'] for d in report['duels']]
        self.assertEqual(len(per), 2)
        merged = sorted(set(sum(per, [])))
        self.assertEqual(report['unknown_card_ids'], merged)
        self.assertEqual(len(merged), len(set(merged)), '不许重复')

    def test_collector_summary_has_segment(self):
        """采集器摘要（--log / 控制台）里也要有这一段（P1-2 的关键）。"""
        line = dc._summarize_import({'merged': 1,
                                     'unknown_card_ids': [22747, 3002]})
        self.assertIn('卡表里查不到 2 个卡 id', line)
        self.assertIn('22747', line)
        self.assertIn('--check-update', line)
        self.assertEqual(dc._summarize_import({'merged': 1}), '并档 1')


    def test_reimport_skips_pairing_when_own_row_exists(self):
        """实机教训（09-14）：record 已有自己的行时，重跑 import 不得被
        宽窗拽去并档别的 log 行（重打局被并进第一次尝试的 log 行，
        旧 Win 被新 Lose 覆盖）。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.run_import(apply=True)                     # 先建 rec 行
        # 另一局（不同对局）的 log 行落在宽窗 [11:30:45, 12:00:15] 内
        self.storage.upsert_match({'match_id': 'otherlog',
                                   'started_at': '2026-09-13 11:50:00',
                                   'ended_at': '2026-09-13 11:55:00',
                                   'result': 'Win', 'origin': 'log',
                                   'mode': 'pvp'})
        report = self.run_import(apply=True)            # 重跑
        item = [d for d in report['duels']
                if d['record_id'] == 'record_20260913_120045'][0]
        self.assertEqual(item['pair']['stage'], 'own-record-row')
        row = self.row_by_id('otherlog')
        self.assertIsNone(row['record_dir'])            # 没被并档
        self.assertEqual(row['result'], 'Win')          # 没被覆盖
        self.assertIsNotNone(self.row_by_id('rec:record_20260913_120045'))


class TestIdempotent(Base):

    def test_rerun_no_writes(self):
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.write_em([{'captured_at': '2026-09-13 12:05:30',
                        'winbits': [0, 1], 'texts': {'messageText': '已投降'}}])
        r1 = self.run_import(apply=True)
        self.assertEqual(r1['new_rows'], 1)
        jp = os.path.join(self.records, 'record_20260913_120045', 'duel.json')
        snap1 = read_bytes(jp)
        n1 = self.row_count()
        r2 = self.run_import(apply=True)               # 重跑
        self.assertEqual(r2['written_json'], 0)        # duel.json 不重写
        self.assertEqual(r2['new_rows'], 0)            # 不新增行
        self.assertEqual(read_bytes(jp), snap1)
        self.assertEqual(self.row_count(), n1)

    def test_dry_run_touches_nothing(self):
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.run_import(apply=False)
        self.assertEqual(self.row_count(), 0)
        self.assertFalse(os.path.exists(os.path.join(
            self.records, 'record_20260913_120045', 'duel.json')))


class TestSeatIntegration(Base):

    def test_snapshot_seats_and_revealed_mapping(self):
        """带 my_deck_snapshot 的 record：seat 判出 + opp_revealed 映射。"""
        # 122146 的 revealed：p0 与 p1 两份；快照给 p1 的两张 -> seat=1
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00',
                         my_deck={'deck_id': '48600670', 'deck_name': '异画',
                                  'ids': [21341, 12950]})
        self.run_import(apply=True)
        duel = read_json(os.path.join(
            self.records, 'record_20260913_120045', 'duel.json'))
        self.assertEqual(duel['meta']['seat'], 1)
        self.assertIn('snapshot-hit', duel['meta']['seat_evidence'])
        self.assertEqual(duel['meta']['my_deck_id'], '48600670')
        # 对手侧 = 非 seat 侧 = p0
        self.assertEqual(duel['summary']['opp_revealed_ids'],
                         duel['summary']['revealed']['p0'])
        row = self.row_by_id('rec:record_20260913_120045')
        self.assertFalse(row['went_first'])            # first_player=0, seat=1


class TestSingleDirAndStale(Base):
    """M6 增量入口与 open 终局化（P1-1 / P2-1）。"""

    def test_only_processes_requested_dir(self):
        """--only：分组在全量扫描上做（并档关系不丢），但只入库指定的局。"""
        self.make_record('record_20260913_120045', instance='0xbbb',
                         saved_at='2026-09-13 12:10:00')
        self.make_record('record_20260913_130045', instance='0xccc',
                         saved_at='2026-09-13 13:10:00')
        report = imp.process(records_dir=self.records, storage=self.storage,
                             apply=True, em_path=self.em, dm_path=self.dm,
                             only='record_20260913_120045')
        self.assertEqual([d['record_id'] for d in report['duels']],
                         ['record_20260913_120045'])
        self.assertEqual(self.row_count(), 1)

    def test_only_not_found(self):
        """--only 没匹配上 -> 明确的 not-found 信号，不是空报告。"""
        report = imp.process(records_dir=self.records, storage=self.storage,
                             apply=True, em_path=self.em, dm_path=self.dm,
                             only='record_2099')
        self.assertEqual(report['duels'][0]['action'], 'not-found')

    def test_skipped_open_lists_dir_names(self):
        """P1-1：open 跳过不再只报计数，目录名要看得见（才收得了口）。"""
        self.make_record('record_20260913_120100', versioned=True, state=None)
        report = self.run_import(apply=True)
        self.assertEqual(report['skipped_open_dirs'],
                         ['record_20260913_120100'])

    def test_stale_open_finalize_roundtrip(self):
        """时间判据 -> finalize_open 标 final + 痕迹 -> scan 认成 final、幂等。"""
        from datetime import datetime
        from core import duel_record
        from core.memread import record_capture as rc
        self.make_record('record_20260913_120100', instance='0xddd',
                         saved_at='2026-09-13 12:01:30', versioned=True,
                         state='open')
        name = 'record_20260913_120100'
        d = os.path.join(self.records, name)
        now = datetime(2026, 9, 13, 20, 0, 0)          # 距 last_saved 8h
        scans = [duel_record.scan_record(d)]
        self.assertIn(name, rc.find_stale_open(scans, now))
        self.assertNotIn(name, rc.find_stale_open(
            scans, datetime(2026, 9, 13, 13, 0, 0)))   # 1h 前不算 stale
        man = rc.finalize_open(d, reason='stale-open')
        self.assertEqual(man['state'], 'final')
        self.assertEqual(man['finalized_by'], 'stale-open')
        self.assertEqual(duel_record.scan_record(d)['state'], 'final')
        self.assertIsNone(rc.finalize_open(d))         # 幂等

    def test_parse_span(self):
        self.assertEqual(imp.parse_span('6h'), 21600.0)
        self.assertEqual(imp.parse_span('30m'), 1800.0)
        self.assertEqual(imp.parse_span('21600'), 21600.0)


if __name__ == '__main__':
    unittest.main()

class RecordDurationTest(unittest.TestCase):
    """★ BUG-B8-17：record 行的「时长」列不再恒为 None。

    record 来源没有日志的 `Game ended`，但「开始」与
    `capture.last_saved_at`（≈ 结束）都在手上 —— 时长可以算。
    """

    def test_duration_from_capture_last_saved(self):
        import import_duel_records as idr
        got = idr._duration_sec('2026-10-17 23:30:00', '2026-10-17 23:32:30')
        self.assertEqual(got, 150)

    def test_duration_none_when_missing(self):
        import import_duel_records as idr
        self.assertIsNone(idr._duration_sec(None, '2026-10-17 23:32:30'))
        self.assertIsNone(idr._duration_sec('2026-10-17 23:30:00', None))

    def test_duration_none_when_unparsable(self):
        import import_duel_records as idr
        self.assertIsNone(idr._duration_sec('not-a-time', '2026-10-17 23:32:30'))

    def test_duration_none_when_negative(self):
        """结束早于开始（时钟回拨 / 数据错）= 不编。"""
        import import_duel_records as idr
        self.assertIsNone(idr._duration_sec('2026-10-17 23:32:30',
                                            '2026-10-17 23:30:00'))

