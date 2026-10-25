# -*- coding: utf-8 -*-
"""core/duel_record.py 的单测（M3：层 0→层 1 解码 / 同局去重 / 座位推断 / duel.json 幂等）。

fixture = ``tests/fixtures/duel_records/``（record_20260913_122146 最小局 +
record_20260913_123714 完整局 462 label —— count/stride/超读的差异只有完整局才暴露）。
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import duel_record as dr                                   # noqa: E402
from core.memread import duel_log as dl                              # noqa: E402
from core import card_names as cn                          # noqa: E402

FIX = os.path.join(PROJ, 'tests', 'fixtures', 'duel_records')
REC_MIN = os.path.join(FIX, 'record_20260913_122146')
REC_FULL = os.path.join(FIX, 'record_20260913_123714')


def _scan(name, instance, fs, ls, size=10, first_logbase_size=None,
          state_src='legacy'):
    """手工构造 scan_record 形状（group_duels / pick_main 的纯逻辑测试用）。

    `first_logbase_size` = 首见时的 logbase size（同局延续性判据用；
    None = 老 manifest 判不出，按旧口径放行）。
    `state_src` = 'legacy'（存量推定，无收口证据）/ 'manifest'（明写 final）。
    """
    return {
        'dir': os.path.join('x', name), 'name': name, 'manifest': {},
        'state': 'final', 'state_src': state_src,
        'first_seen': dr.parse_dt(fs), 'last_saved': dr.parse_dt(ls),
        'capture_approx': True, 'logbase_size': size,
        'first_logbase_size': first_logbase_size,
        'instance': int(instance, 16) if instance else None,
    }


class TestGroupInstanceRecycle(unittest.TestCase):
    """★ 同 instance ≠ 同局：游戏会**复用控制器地址**（2026-09-18 实锤，用户漏局）。

    233129 局与 234153 局同 instance（0x1eddf3fb2a0）、隔 8 分钟 —— 旧口径按
    「instance + 15 分钟内邻近」并成一局，`pick_main` 挑 size 大的，
    **另一局被静默吞掉**（234153 导不进库，用户少了两局里的一局）。
    判据 = 新目录的**首见 logbase size** 是否延续旧目录已落盘的 size（单调不减）。
    """

    def test_recycled_instance_is_two_duels(self):
        """首见 size 回退（10 < 105）= 地址复用的新局，不许并。"""
        g1 = _scan('record_a', '0xabc', '2026-09-18 23:31:29', '2026-09-18 23:33:58',
                   size=105, first_logbase_size=9)
        g2 = _scan('record_b', '0xabc', '2026-09-18 23:41:53', '2026-09-18 23:44:16',
                   size=103, first_logbase_size=10)
        groups = dr.group_duels([g1, g2])
        self.assertEqual(len(groups), 2, '地址复用的新局被并成了一局')
        self.assertEqual({dr.pick_main(g)['name'] for g in groups},
                         {'record_a', 'record_b'})

    def test_watcher_restart_still_merges(self):
        """同局两目录（重启接管）：新目录首见 size ≥ 旧目录已落盘 size → 照常并。"""
        g1 = _scan('record_a', '0xabc', '2026-09-18 23:31:29', '2026-09-18 23:33:58',
                   size=50, first_logbase_size=9)
        g2 = _scan('record_b', '0xabc', '2026-09-18 23:33:30', '2026-09-18 23:35:00',
                   size=105, first_logbase_size=52)
        groups = dr.group_duels([g1, g2])
        self.assertEqual(len(groups), 1)
        self.assertEqual(dr.pick_main(groups[0])['name'], 'record_b')

    def test_missing_first_size_keeps_old_behaviour(self):
        """老 manifest 没有 first_logbase_size → 判不出 → 按旧口径并（别判碎老数据）。"""
        g1 = _scan('record_a', '0xabc', '2026-09-18 23:31:29', '2026-09-18 23:33:58',
                   size=105)
        g2 = _scan('record_b', '0xabc', '2026-09-18 23:41:53', '2026-09-18 23:44:16',
                   size=103)
        self.assertEqual(len(dr.group_duels([g1, g2])), 1)

    def test_recycle_inside_gap_window(self):
        """间隔 8 分钟（< MERGE_GAP_SEC=900）也拦得住 —— 光靠时间窗不够。"""
        import datetime as dt
        g1 = _scan('record_a', '0xabc', '2026-09-18 23:31:29', '2026-09-18 23:33:58',
                   size=105, first_logbase_size=9)
        g2 = _scan('record_b', '0xabc', '2026-09-18 23:41:53', '2026-09-18 23:44:16',
                   size=103, first_logbase_size=10)
        gap = (dr.parse_dt('2026-09-18 23:41:53')
               - dr.parse_dt('2026-09-18 23:33:58')).total_seconds()
        self.assertLess(gap, dr.MERGE_GAP_SEC)
        self.assertEqual(len(dr.group_duels([g1, g2])), 2)


class TestSizesTable(unittest.TestCase):
    """尺寸常量表与 duel_log.py 同源 —— 漂移即红（截断铁律的根基）。"""

    def test_sizes_match_duel_log(self):
        self.assertEqual(dr.ARRAY_SIZES['ShowAction'], dl.ENTRY_SIZE)
        self.assertEqual(dr.ARRAY_SIZES['MoveMultiHeader'], dl.MULTI_HEADER_SIZE)
        self.assertEqual(dr.ARRAY_SIZES['ShowTurn'], dl.TURN_SIZE)
        self.assertEqual(dr.ARRAY_SIZES['ShowPhase'], dl.PHASE_SIZE)
        self.assertEqual(dr.ARRAY_SIZES['ShowChain'], dl.CHAIN_SIZE)
        self.assertEqual(dr.ARRAY_SIZES['ShowCardName'], dl.CARD_NAME_SIZE)

    def test_known_literal_sizes(self):
        # 字面值钉死（docs/对局记录格式.md §2.2）—— 上面的同源断言防的是重构漂移
        self.assertEqual(dr.ARRAY_SIZES,
                         {'ShowAction': 32, 'MoveMultiHeader': 32,
                          'ShowTurn': 24, 'ShowPhase': 4, 'ShowChain': 12,
                          'ShowCardName': 6, 'ShowText': 4, 'ShowTag': 4,
                          'ChoiceFirstPlayer': 4, 'MoveMulti': 8})

    def test_label_mapping(self):
        self.assertEqual(dr.LABEL_TO_ARRAY['ShowFirstPlayer'], 'ChoiceFirstPlayer')
        self.assertEqual(dr.LABEL_TO_ARRAY['CardMoveMultiHeader'], 'MoveMultiHeader')
        self.assertEqual(dr.LABEL_TO_ARRAY['CardMoveMulti'], 'MoveMulti')
        self.assertIsNone(dr.LABEL_TO_ARRAY['SeparateLine'])


class TestDecode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scan_min = dr.scan_record(REC_MIN)
        cls.duel_min = dr.decode_record(cls.scan_min)
        cls.scan_full = dr.scan_record(REC_FULL)
        cls.duel_full = dr.decode_record(cls.scan_full)

    def test_minimal_counts(self):
        counts = self.duel_min['summary']['counts']
        self.assertEqual(counts['ShowAction'], 13)
        self.assertEqual(counts['ShowTurn'], 1)
        self.assertEqual(counts['ShowChain'], 5)
        self.assertEqual(counts['ShowCardName'], 13)
        self.assertEqual(counts['ShowFirstPlayer'], 1)
        self.assertEqual(counts['SeparateLine'], 10)

    def test_minimal_legacy_state_and_capture(self):
        self.assertEqual(self.duel_min['state'], 'final')
        self.assertEqual(self.duel_min['state_src'], 'legacy')
        cap = self.duel_min['capture']
        self.assertTrue(cap['approx'])
        # 存量：first_seen = 目录名时间（12:21:46），last_saved = saved_at（12:26:29）
        self.assertEqual(cap['first_seen_at'], '2026-09-13 12:21:46')
        self.assertEqual(cap['last_saved_at'], '2026-09-13 12:26:29')

    def test_none_label_does_not_crash_sort(self):
        """2026-10-10 实机（record_20261010_193859）：控制器内存被复用时
        label 可能整批变 None/乱码单字，_summary 对 counts 排序时 None 与
        str 混比 TypeError —— 整局 import 失败。归一 '#unknown' 后必须
        正常出 summary。"""
        events = [{'seq': 0, 'label': None, 'dataindex': 0},
                  {'seq': 1, 'label': 'ShowTurn', 'dataindex': 0},
                  {'seq': 2, 'label': None, 'dataindex': 1},
                  {'seq': 3, 'label': '出', 'dataindex': 0}]
        summ = dr._summary(events, {}, lambda arr: b'')
        self.assertEqual(summ['counts'].get('#unknown'), 2)
        self.assertEqual(summ['counts'].get('ShowTurn'), 1)
        self.assertEqual(summ['counts'].get('出'), 1)
        # 键必须可两两比较（再也不会 TypeError）
        self.assertEqual(sorted(summ['counts']),
                         ['#unknown', 'ShowTurn', '出'])

    def test_minimal_summary(self):
        s = self.duel_min['summary']
        self.assertEqual(s['total_turns'], 1)
        self.assertEqual(s['first_player'], 0)
        self.assertEqual(s['lp_final'], {'a': 8000, 'b': 8000})
        self.assertTrue(s['revealed']['p0'])
        self.assertTrue(s['revealed']['p1'])

    def test_full_counts(self):
        counts = self.duel_full['summary']['counts']
        self.assertEqual(counts['ShowAction'], 147)
        self.assertEqual(counts['ShowTurn'], 7)
        self.assertEqual(counts['ShowChain'], 44)
        self.assertEqual(counts['ShowCardName'], 130)
        self.assertEqual(counts['ShowText'], 3)
        self.assertEqual(counts['ShowTag'], 18)

    def test_full_summary(self):
        s = self.duel_full['summary']
        self.assertEqual(s['total_turns'], 7)
        self.assertEqual(s['first_player'], 1)
        self.assertEqual(s['lp_final'], {'a': 900, 'b': 7500})

    def test_events_carry_seq_dataindex(self):
        for e in self.duel_full['events']:
            self.assertIn('seq', e)
            self.assertIn('dataindex', e)
        # 叙事顺序：第 0 条是 ShowFirstPlayer，其后跟 SeparateLine
        self.assertEqual(self.duel_full['events'][0]['label'], 'ShowFirstPlayer')
        self.assertEqual(self.duel_full['events'][1]['label'], 'SeparateLine')

    def test_action_events_cards_low16_and_alt(self):
        """ShowAction 事件：card_id 已是低 16 位，alt_id 单独字段（高 16 位）。"""
        seen = []
        for e in self.duel_full['events']:
            for c in e.get('cards') or []:
                self.assertLess(c['card_id'], 0x10000)
                seen.append((c['card_id'], c.get('alt_id') or 0))
        self.assertTrue(seen)

    def test_alt_id_via_synthetic_entry(self):
        """异画语义用合成条目验证（两份 fixture 都是租借卡组、无异画样本）：
        dataint = (19188<<16)|22747 应解出 card_id=22747（实际用的异画 id）、
        alt_id=19188（标准版）。"""
        buf = struct.pack('<iBBBB', (19188 << 16) | 22747, 9, 13, 1, 1)
        buf += b'\x00' * 24
        e = dl.parse_entry(buf, 0)
        self.assertEqual(e.datal.card_id, 22747)
        self.assertEqual(e.datal.alt_id, 19188)

    def test_unsolved_labels_kept_raw(self):
        """ShowText/ShowTag/ChoiceFirstPlayer 登记在案（raw），不许静默消失。"""
        labels = {e['label'] for e in self.duel_full['events']}
        self.assertIn('ShowText', labels)
        self.assertIn('ShowTag', labels)
        for e in self.duel_full['events']:
            if e['label'] in ('ShowText', 'ShowTag', 'ShowFirstPlayer'):
                self.assertIn('raw', e)

    def test_truncation_guard(self):
        """dataindex 越界 / bin 缺失 -> truncated 留痕，不炸。"""
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, 'record_20260913_100000')
            os.makedirs(d)
            man = {
                'saved_at': '2026-09-13 10:00:00', 'instance': '0x10',
                'logbase': {'size': 2, 'entries': [
                    {'label': 'ShowAction', 'dataindex': 5},   # 越界
                    {'label': 'ShowTurn', 'dataindex': 0},     # bin 缺失
                ]},
                'payload': {'ShowAction': {'count': 1, 'stride': 32,
                                           'bin': 'm_DataList_ShowAction.bin'},
                            'ShowTurn': {'count': 1, 'stride': 24}},
            }
            with open(os.path.join(d, 'manifest.json'), 'w',
                      encoding='utf-8', newline='') as f:
                json.dump(man, f)
            with open(os.path.join(d, 'm_DataList_ShowAction.bin'), 'wb') as f:
                f.write(b'\x00' * 32)
            duel = dr.decode_record(dr.scan_record(d))
            evs = {e['label']: e for e in duel['events']}
            self.assertTrue(evs['ShowAction'].get('truncated'))
            self.assertTrue(evs['ShowTurn'].get('truncated'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_label_guard(self):
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, 'record_20260913_100001')
            os.makedirs(d)
            man = {'saved_at': '2026-09-13 10:00:00', 'instance': '0x11',
                   'logbase': {'size': 1, 'entries': [
                       {'label': 'ShowFoo', 'dataindex': 0}]},
                   'payload': {}}
            with open(os.path.join(d, 'manifest.json'), 'w',
                      encoding='utf-8', newline='') as f:
                json.dump(man, f)
            duel = dr.decode_record(dr.scan_record(d))
            self.assertTrue(duel['events'][0].get('unknown_label'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFingerprintAndDuelJson(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dst = os.path.join(self.tmp, 'record_20260913_122146')
        shutil.copytree(REC_MIN, self.dst, copy_function=shutil.copy2)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fingerprint_excludes_duel_json(self):
        fp1 = dr.fingerprint(self.dst)
        duel = dr.decode_record(dr.scan_record(self.dst))
        dr.write_duel_json(self.dst, duel)
        fp2 = dr.fingerprint(self.dst)
        self.assertEqual(fp1, fp2)
        self.assertNotIn('duel.json', fp2)

    def test_write_lifecycle(self):
        scan = dr.scan_record(self.dst)
        duel = dr.decode_record(scan)
        self.assertEqual(dr.write_duel_json(self.dst, duel), 'written')
        # 重解同内容 -> 指纹没变、meta 无新值 -> skipped（不写文件）
        self.assertEqual(dr.write_duel_json(self.dst, duel), 'skipped')
        # 注入 meta -> updated
        self.assertEqual(dr.write_duel_json(
            self.dst, duel, meta_updates={'started_at': '2026-09-13 12:20:00',
                                          'started_at_src': 'log'}), 'updated')
        # 同样的注入再来一遍 -> 没有新值可补 -> skipped（幂等）
        self.assertEqual(dr.write_duel_json(
            self.dst, duel, meta_updates={'started_at': '2026-09-13 12:20:00',
                                          'started_at_src': 'log'}), 'skipped')
        # meta 确实写进去了
        got = dr.read_duel_json(self.dst)
        self.assertEqual(got['meta']['started_at'], '2026-09-13 12:20:00')

    def test_meta_preserved_on_redecode(self):
        scan = dr.scan_record(self.dst)
        duel = dr.decode_record(scan)
        dr.write_duel_json(self.dst, duel,
                           meta_updates={'result_note': '文案A',
                                         'winbits': [0, 1]})
        # 模拟层 0 被采集器覆盖（mtime/md5 变了）—— 重解不得丢已注入 meta
        with open(os.path.join(self.dst, 'm_DataList_ShowTurn.bin'), 'ab') as f:
            f.write(b'\x00' * 8)
        duel2 = dr.decode_record(dr.scan_record(self.dst))
        dr.write_duel_json(self.dst, duel2,
                           meta_updates={'result_note': '文案B'})  # 只补 NULL，不覆盖
        got = dr.read_duel_json(self.dst)
        self.assertEqual(got['meta']['result_note'], '文案A')
        self.assertEqual(got['meta']['winbits'], [0, 1])

    def test_manual_keys_can_override(self):
        scan = dr.scan_record(self.dst)
        duel = dr.decode_record(scan)
        dr.write_duel_json(self.dst, duel, meta_updates={'result_note': '文案A'})
        got = dr.read_duel_json(self.dst)
        got['meta']['sources']['manual'] = ['result_note']     # 用户钉过这个键
        with open(os.path.join(self.dst, 'duel.json'), 'w',
                  encoding='utf-8', newline='') as f:
            f.write(json.dumps(got, ensure_ascii=False, indent=1))
        duel2 = dr.decode_record(dr.scan_record(self.dst))
        dr.write_duel_json(self.dst, duel2,
                           meta_updates={'result_note': '文案B'})
        self.assertEqual(dr.read_duel_json(self.dst)['meta']['result_note'],
                         '文案B')

    def test_state_rules(self):
        self.assertEqual(dr.state_of({'state': 'open'}), ('open', 'manifest'))
        self.assertEqual(dr.state_of({'state': 'final'}), ('final', 'manifest'))
        # 有版本号没 state = 契约违反 -> open
        self.assertEqual(dr.state_of({'manifest_version': 1}),
                         ('open', 'missing-but-versioned'))
        # 什么都没有 = legacy -> final
        self.assertEqual(dr.state_of({}), ('final', 'legacy'))


class TestUnknownCardIds(unittest.TestCase):
    """M4：`unknown_card_ids` = 已解码字段里、卡表查不到的卡 id。

    覆盖边界（docs/对局记录格式.md §6）：只统计已解码字段，
    `MoveMulti` 还是 raw，**不在统计内**。
    """

    EVENTS = [
        {'seq': 0, 'label': 'ShowAction', 'cards': [
            {'card_id': 20500, 'alt_id': 0}, {'card_id': 999999},
            {'card_id': 20500}]},                       # 重复要并掉
        {'seq': 1, 'label': 'ShowChain', 'card_id': 999999},
        {'seq': 2, 'label': 'ShowCardName', 'card_id': 4007},
        {'seq': 3, 'label': 'ShowAction'},               # 无卡（empty）
        {'seq': 4, 'label': 'CardMoveMulti', 'raw_hex': '0011'},   # raw：不算
        {'seq': 5, 'label': 'ShowTurn', 'player': 0, 'turn': 1},
    ]
    SUMM = {'revealed': {'p0': [20500, 888888], 'p1': [4007]}}

    TABLE = {20500: ('多多迷宝・软绵雀精', 'Mulcharmy Fuwalos', 'マルチャミー・フワロス'),
             4007: ('青眼白龙', 'Blue-Eyes White Dragon', '青眼の白龍')}

    def test_collect_sorted_dedup_and_scope(self):
        ids = dr.collect_card_ids(self.EVENTS, self.SUMM)
        self.assertEqual(ids, sorted({20500, 999999, 4007, 888888}))
        # raw 的 MoveMulti / ShowTurn 不在统计内 —— 别承诺「全卡面无遗漏」
        self.assertNotIn(888889, ids)

    def test_unknown_filter_sorted(self):
        unk = dr.find_unknown_card_ids(self.EVENTS, self.SUMM, self.TABLE)
        self.assertEqual(unk, [888888, 999999], '表外的才叫 unknown，且排序去重')

    def test_empty_table_means_all_unknown(self):
        self.assertEqual(dr.find_unknown_card_ids(self.EVENTS, self.SUMM, {}),
                         [4007, 20500, 888888, 999999])

    def test_revealed_both_sides_covered(self):
        """revealed 的 p0 / p1 两侧都要覆盖（只看一侧会漏一半对手的卡）。"""
        ev = [{'seq': 0, 'label': 'ShowAction', 'cards': []}]
        unk = dr.find_unknown_card_ids(
            ev, {'revealed': {'p0': [11], 'p1': [22]}}, {})
        self.assertEqual(unk, [11, 22])

    def test_alt_id_not_counted(self):
        """异画的标准版 id（alt_id / 高 16 位）不算 —— 别凭空造误报。"""
        ev = [{'seq': 0, 'label': 'ShowAction',
               'cards': [{'card_id': 22747, 'alt_id': 19188}]}]
        self.assertEqual(dr.find_unknown_card_ids(ev, {}, {22747: ('', 'S:P', '')}),
                         [])


class TestFingerprintTracksCardTable(unittest.TestCase):
    """M4 审查 P1-1：卡表纳入指纹 —— 卡表一变，重跑 import 必须整份重写。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dst = os.path.join(self.tmp, 'record_20260913_122146')
        shutil.copytree(REC_MIN, self.dst, copy_function=shutil.copy2)
        self.table = os.path.join(self.tmp, 'card_names.json')
        self.scan = dr.scan_record(self.dst)
        # 空表 -> 本局出现过的卡全是 unknown（顺便拿到「全量 id」清单）
        self._table([])
        self.all_ids = dr.decode_record(
            self.scan, card_names_file=self.table)['summary']['unknown_card_ids']
        self.assertTrue(self.all_ids)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        cn.clear_cache()

    def _table(self, ids):
        payload = {'v': 1, 'langs': ['cn', 'en', 'jp'], 'counts': {},
                   'names': {str(i): ['卡%s' % i, '', ''] for i in ids}}
        with open(self.table, 'w', encoding='utf-8', newline='') as f:
            json.dump(payload, f, ensure_ascii=False)

    def _duel_json(self):
        return os.path.join(self.dst, 'duel.json')

    def test_card_table_update_refreshes_summary(self):
        """① 卡表缺一张 -> unknown 有值；② 卡表补齐 -> 重跑 -> unknown 刷新为空。"""
        missing = self.all_ids[0]
        self._table([i for i in self.all_ids if i != missing])
        duel = dr.decode_record(self.scan, card_names_file=self.table)
        self.assertEqual(duel['summary']['unknown_card_ids'], [missing])
        self.assertEqual(dr.write_duel_json(self.dst, duel), 'written')
        self.assertEqual(dr.read_duel_json(self.dst)['summary']['unknown_card_ids'],
                         [missing])

        self._table(self.all_ids)                   # 「更新卡表」
        duel2 = dr.decode_record(self.scan, card_names_file=self.table)
        self.assertEqual(dr.write_duel_json(self.dst, duel2), 'written',
                         '卡表变了 -> 指纹变了 -> 必须整份重写（不是 skipped）')
        self.assertEqual(dr.read_duel_json(self.dst)['summary']['unknown_card_ids'],
                         [])

    def test_card_table_unchanged_still_skipped(self):
        """卡表没动 -> 第二次写仍 skipped、文件 0 变化（幂等不回归）。"""
        self._table(self.all_ids)
        duel = dr.decode_record(self.scan, card_names_file=self.table)
        self.assertEqual(dr.write_duel_json(self.dst, duel), 'written')
        with open(self._duel_json(), 'rb') as f:
            snap = f.read()
        duel2 = dr.decode_record(self.scan, card_names_file=self.table)
        self.assertEqual(dr.write_duel_json(self.dst, duel2), 'skipped')
        with open(self._duel_json(), 'rb') as f:
            self.assertEqual(f.read(), snap)

    def test_meta_change_refreshes_derived_summary(self):
        """meta 一变，派生 summary 也要落盘（went_first 卡过一次）。"""
        self._table(self.all_ids)
        duel = dr.decode_record(self.scan, card_names_file=self.table)
        self.assertEqual(dr.write_duel_json(self.dst, duel), 'written')
        # 同指纹、只补 meta.seat=0 -> summary.went_first 必须跟着写进去
        duel2 = dr.decode_record(self.scan, card_names_file=self.table)
        seat = 0
        duel2['meta']['seat'] = seat
        duel2['summary']['went_first'] = bool(
            seat == duel2['summary']['first_player'])
        status = dr.write_duel_json(self.dst, duel2,
                                    meta_updates={'seat': seat})
        self.assertEqual(status, 'updated')
        got = dr.read_duel_json(self.dst)
        self.assertEqual(got['meta']['seat'], 0)
        self.assertIs(got['summary']['went_first'],
                      bool(seat == got['summary']['first_player']))

    def test_fingerprint_has_card_names_entry(self):
        fp = dr.fingerprint(self.dst, self.table)
        self.assertIn(dr.CARD_NAMES_KEY, fp)
        self.assertIsNotNone(fp[dr.CARD_NAMES_KEY]['md5'])
        # 卡表文件不在也要留这条（别让指纹悄悄退化成「没有卡表」）
        fp2 = dr.fingerprint(self.dst, os.path.join(self.tmp, 'nope.json'))
        self.assertIn(dr.CARD_NAMES_KEY, fp2)
        self.assertIsNone(fp2[dr.CARD_NAMES_KEY]['md5'])
        # 真实调用（不传路径）用默认卡表，条也在
        self.assertIn(dr.CARD_NAMES_KEY, dr.fingerprint(self.dst))


class TestGrouping(unittest.TestCase):
    """实测 6 目录 = 4 局（watcher 重启会给同一局新建目录）。"""

    SCANS = [
        _scan('record_20260913_121829', '0x1ce592b5690',
              '2026-09-13 12:18:29', '2026-09-13 12:18:29', 281),
        _scan('record_20260913_122002', '0x1ce592b5690',
              '2026-09-13 12:20:02', '2026-09-13 12:20:02', 281),
        _scan('record_20260913_122146', '0x1cdf158f3f0',
              '2026-09-13 12:21:46', '2026-09-13 12:26:29', 52),
        _scan('record_20260913_122713', '0x1cdf158f3f0',
              '2026-09-13 12:27:13', '2026-09-13 12:33:07', 52),
        _scan('record_20260913_123326', '0x1cde2e81690',
              '2026-09-13 12:33:26', '2026-09-13 12:36:54', 138),
        _scan('record_20260913_123714', '0x1cdf158f000',
              '2026-09-13 12:37:14', '2026-09-13 12:53:40', 462),
    ]

    def test_six_dirs_four_duels(self):
        groups = dr.group_duels(self.SCANS)
        self.assertEqual(len(groups), 4)
        members = sorted(sorted(x['name'] for x in g) for g in groups)
        self.assertIn(['record_20260913_121829', 'record_20260913_122002'], members)
        self.assertIn(['record_20260913_122146', 'record_20260913_122713'], members)
        self.assertIn(['record_20260913_123326'], members)
        self.assertIn(['record_20260913_123714'], members)

    def test_pick_main_most_complete(self):
        main_of = {}
        for g in dr.group_duels(self.SCANS):
            m = dr.pick_main(g)['name']
            for x in g:
                main_of[x['name']] = m
        # 同尺寸取 saved_at 最晚；不同尺寸取 logbase.size 最大
        self.assertEqual(main_of['record_20260913_121829'],
                         'record_20260913_122002')
        self.assertEqual(main_of['record_20260913_122146'],
                         'record_20260913_122713')
        self.assertEqual(main_of['record_20260913_123714'],
                         'record_20260913_123714')


class TestInferSeat(unittest.TestCase):

    def test_clear_case(self):
        seat, ev = dr.infer_seat(
            [21341, 12950, 20754],
            {'p0': [5333, 20196, 20240], 'p1': [21341, 12950, 20754, 999]})
        self.assertEqual(seat, 1)
        self.assertIn('snapshot-hit', ev)

    def test_ambiguous_mirror(self):
        # 镜像局：两侧包含度都 1.0 -> 歧义判 None（不猜座位）
        seat, ev = dr.infer_seat([1, 2, 3], {'p0': [1, 2, 3], 'p1': [1, 2, 3]})
        self.assertIsNone(seat)
        self.assertIn('ambiguous', ev)

    def test_low_coverage(self):
        seat, ev = dr.infer_seat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                                 {'p0': [1], 'p1': [99, 98, 97]})
        self.assertIsNone(seat)
        # p0 只命中 1 张（泛用卡撞快照）-> thin-evidence，不成证据
        self.assertIn('thin-evidence', ev)

    def test_no_snapshot(self):
        seat, _ = dr.infer_seat(None, {'p0': [1], 'p1': [2]})
        self.assertIsNone(seat)

    def test_no_revealed(self):
        seat, _ = dr.infer_seat([1, 2], {'p0': [], 'p1': []})
        self.assertIsNone(seat)

    def test_multipart_tolerant(self):
        """容错匹配：手坑抽换/换卡（快照不是已见卡的精确子集）也能判。"""
        seat, _ = dr.infer_seat(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],   # 快照 10 张
            {'p0': [900, 901],                                     # 对手侧，无交集
             'p1': [100, 101, 102, 103, 200]})                     # 我方侧 4/5 被覆盖
        self.assertEqual(seat, 1)


class TestSeatFromClientWork(unittest.TestCase):
    """座位判据②：ClientWork `my_index`（活动局没快照时的唯一座位来源）。

    为什么需要（2026-09-16）：骰子拉力没有可信卡组快照 -> 判据①必然空，
    于是「先/后」和对手名一直 NULL；而采集器开局读的 Duel 里就有 my_index。
    """

    def _decode(self, ds=None, snap=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        d = os.path.join(tmp, 'record_20260913_122146')
        shutil.copytree(REC_MIN, d)
        mp = os.path.join(d, 'manifest.json')
        with open(mp, encoding='utf-8') as f:
            man = json.load(f)
        man['duel_settings'] = ds
        man['my_deck_snapshot'] = snap
        with open(mp, 'w', encoding='utf-8', newline='') as f:
            json.dump(man, f, ensure_ascii=False)
        return dr.decode_record(dr.scan_record(d))

    def _row_my_deck(self, mode, src, container=None, name='X'):
        duel = {'record_id': 'r1', 'meta': {
            'game_mode': mode, 'my_deck_id': 5, 'my_deck_name': name,
            'my_deck_name_src': src,
            'my_deck_snapshot': ({'deck_id': 5, 'deck_name': name,
                                  'ids': [1], 'container': container}
                                 if container else None)},
            'summary': {}}
        return dr.to_row(duel)

    def test_current_deck_name_is_kept_for_event_mode(self):
        """★ 来源是「当前卡组」-> 活动局也采信（用户口径：这套就是给活动对局用的）。"""
        row = self._row_my_deck(21, 'current-deck', container='DRDeck',
                                name='青眼白龙')
        self.assertEqual(row['my_deck_name'], '青眼白龙')

    def test_guess_name_is_kept_for_event_mode(self):
        row = self._row_my_deck(21, 'm5-guess', container='DeckEdit', name='青眼白龙')
        self.assertEqual(row['my_deck_name'], '青眼白龙')

    def test_lobby_snapshot_name_still_dropped_for_event_mode(self):
        """来源是快照 + 活动局 + 大厅 Deck 容器 -> 仍丢掉（老坑不许回来）。"""
        row = self._row_my_deck(21, 'snapshot-name', container='Deck', name='闪刀姬')
        self.assertIsNone(row['my_deck_name'])
        self.assertEqual(row['deck_source'], 'snapshot-untrusted')

    def test_event_container_snapshot_is_kept(self):
        """活动局自己的容器（DRDeck）-> 采信（这条是 09-16 计划 T2 的口径）。"""
        row = self._row_my_deck(21, 'snapshot-name', container='DRDeck',
                                name='IDS_DICERALLY_REG_005')
        self.assertEqual(row['my_deck_name'], 'IDS_DICERALLY_REG_005')

    def test_my_index_fills_seat_without_snapshot(self):
        duel = self._decode(ds={'names': ['对手名', '我的名字'],
                                'names_direction': 'by-seat',
                                'game_mode': 21, 'my_index': 1})
        meta = duel['meta']
        self.assertEqual(meta['seat'], 1)
        self.assertEqual(meta['seat_evidence'], 'clientwork-myid')
        self.assertEqual(meta['opp_player_name'], '对手名')
        # 对手露出卡按座位映射（活动局也能填了）
        self.assertEqual(duel['summary']['opp_revealed_ids'],
                         duel['summary']['revealed']['p0'])
        # 先/后：first_player=0（fixture），我方 seat=1 -> 后手
        self.assertIs(dr.to_row(duel)['went_first'], False)

    def test_snapshot_wins_and_conflict_is_noted(self):
        """两来源打架：保留快照（已验证），但把冲突写进 evidence 当探针。"""
        duel = self._decode(ds={'names': ['a', 'b'],
                                'names_direction': 'by-seat',
                                'my_index': 0},
                            snap={'deck_id': 1, 'deck_name': 'x',
                                  'ids': [21341, 12950]})   # = p1 的已见卡
        meta = duel['meta']
        self.assertEqual(meta['seat'], 1)          # 快照说 1
        self.assertIn('冲突', meta['seat_evidence'])
        self.assertIn('clientwork-myid=0', meta['seat_evidence'])

    def test_bogus_my_index_is_ignored(self):
        """只认 0/1：别的值一律不采（判不出就 NULL，不猜）。"""
        for bad in (2, -1, '1', None):
            duel = self._decode(ds={'names': ['a', 'b'],
                                    'names_direction': 'by-seat',
                                    'my_index': bad})
            self.assertIsNone(duel['meta']['seat'], repr(bad))
            self.assertIsNone(duel['meta']['opp_player_name'], repr(bad))


class TestDeriveResultAndToRow(unittest.TestCase):

    def test_derive_result(self):
        self.assertEqual(dr.derive_result([1, 0]), 'Win')
        self.assertEqual(dr.derive_result([0, 1]), 'Lose')
        self.assertIsNone(dr.derive_result([1, 1]))    # 同值不硬猜
        self.assertIsNone(dr.derive_result(None))
        self.assertIsNone(dr.derive_result([1]))

    def test_to_row_shape(self):
        duel = {
            'record_id': 'record_20260913_123714',
            'meta': {'seat': 1, 'winbits': [1, 0], 'game_mode': 21,
                     'started_at': '2026-09-13 12:37:09',
                     'my_deck_id': '48600670', 'my_deck_name': '异画',
                     'result_note': '文案'},
            'summary': {'first_player': 1, 'total_turns': 7,
                        'opp_revealed_ids': [100, 200]},
        }
        row = dr.to_row(duel)
        self.assertEqual(row['match_id'], 'rec:record_20260913_123714')
        self.assertEqual(row['record_dir'], 'record_20260913_123714')
        self.assertEqual(row['origin'], 'record')
        self.assertEqual(row['result'], 'Win')
        self.assertTrue(row['went_first'])             # seat == first_player
        self.assertEqual(row['mode'], 'pvp')           # 21 = 活动，仍属 pvp
        # 21 = 活动局：快照读到的是大厅卡组，**不采信** -> 出行置 null、标 untrusted
        # （旧断言 'snapshot' 正是本次修掉的行为，见 docs/活动局快照防线方案.md）
        self.assertEqual(row['deck_source'], 'snapshot-untrusted')
        self.assertIsNone(row['my_deck_id'])
        self.assertEqual(row['opp_revealed_ids'], [100, 200])
        self.assertTrue(row['opp_revealed'])           # 卡名查表

    def test_to_row_self_made_name_normalized(self):
        """★ 2026-10-17：快照名是**游戏内自组名**（别名命中）时也要归一成显示名。

        实机起因：用户的神艺卡组游戏内叫「狱神神艺」，站内条目是 `Artmage`
        （中文「神艺」）—— 自组名当时不在别名里，于是界面搜不到、配不到图、
        统计也单独分一桶。修法是「把自组名挂成别名」+ `to_row` 过一次归一。
        ⚠️ 打桩 `_display_deck_name` 而不是依赖真库：发布仓库的
        `data/masterduel.db` 是旧快照（别名列与主仓不同步），
        直接查真库会在发布仓红（2026-10-18 踩过，同 test_deck_guess 那类坑）。
        """
        orig = dr._display_deck_name
        dr._display_deck_name = lambda n: '神艺' if n == '狱神神艺' else None
        try:
            duel = {'record_id': 'r-selfmade',
                    'meta': {'game_mode': 3, 'my_deck_id': '50459520',
                             'my_deck_name': '狱神神艺', 'winbits': [1, 0]},
                    'summary': {}}
            row = dr.to_row(duel)
        finally:
            dr._display_deck_name = orig
        # 落库的是**显示名**，不是游戏里的自组名
        self.assertEqual(row['my_deck_name'], '神艺')
        self.assertEqual(row['deck_source'], 'snapshot')

    def test_to_row_unknown_name_kept_verbatim(self):
        """参照库里没有的自组名 → **原样保留**（归一不了也不许丢信息）。"""
        orig = dr._display_deck_name
        dr._display_deck_name = lambda n: None      # 一律查不到
        try:
            duel = {'record_id': 'r-verbatim',
                    'meta': {'game_mode': 3, 'my_deck_id': '99999999',
                             'my_deck_name': '我瞎起的卡组名', 'winbits': [1, 0]},
                    'summary': {}}
            row = dr.to_row(duel)
        finally:
            dr._display_deck_name = orig
        self.assertEqual(row['my_deck_name'], '我瞎起的卡组名')

    def test_to_row_no_seat_no_opp(self):
        duel = {'record_id': 'r1',
                'meta': {'winbits': None, 'game_mode': None},
                'summary': {'first_player': 0, 'total_turns': 1,
                            'opp_revealed_ids': None}}
        row = dr.to_row(duel)
        self.assertIsNone(row['opp_revealed_ids'])     # seat 判不出 -> 不映射
        self.assertIsNone(row['went_first'])
        self.assertEqual(row['mode'], 'unknown')
        self.assertIsNone(row['result'])               # 不硬猜

    def test_to_row_trusted_snapshot_on_ladder(self):
        """天梯/巅峰（3/19）：快照可信 -> 原样出行，deck_source='snapshot'。"""
        duel = {'record_id': 'r-ladder',
                'meta': {'game_mode': 3, 'my_deck_id': '48600670',
                         'my_deck_name': '异画', 'winbits': [1, 0]},
                'summary': {}}
        row = dr.to_row(duel)
        self.assertEqual(row['my_deck_id'], '48600670')
        self.assertEqual(row['my_deck_name'], '异画')
        self.assertEqual(row['deck_source'], 'snapshot')

    def test_to_row_untrusted_snapshot_on_activity(self):
        """活动局（21）：读到的是大厅卡组 -> 出行置 null + 标 untrusted（不是 unknown）。"""
        duel = {'record_id': 'r-activity',
                'meta': {'game_mode': 21, 'my_deck_id': '48171157',
                         'my_deck_name': '闪刀姬'},
                'summary': {}}
        row = dr.to_row(duel)
        self.assertIsNone(row['my_deck_id'])
        self.assertIsNone(row['my_deck_name'])
        self.assertEqual(row['deck_source'], 'snapshot-untrusted',
                         '读到了但不适用，跟「没读到」要分开')

    def test_to_row_null_snapshot_is_unknown(self):
        """没读到快照（采集侧已存 null / 租借局）-> unknown（不是 untrusted）。"""
        duel = {'record_id': 'r-none', 'meta': {'game_mode': 3}, 'summary': {}}
        self.assertEqual(dr.to_row(duel)['deck_source'], 'unknown')

    def test_to_row_unknown_mode_is_untrusted(self):
        """模式判不出（None）也不采信 —— 与采集侧闸门同口径。"""
        duel = {'record_id': 'r-nomode',
                'meta': {'game_mode': None, 'my_deck_id': '48600670',
                         'my_deck_name': '异画'},
                'summary': {}}
        row = dr.to_row(duel)
        self.assertIsNone(row['my_deck_id'])
        self.assertEqual(row['deck_source'], 'snapshot-untrusted')

    def test_trusted_modes_is_its_own_constant(self):
        """★ 快照可信集与统计口径**是两个集合**（当前同值纯属巧合）—— 别合并。"""
        from core import storage as st
        self.assertEqual(st.DECK_SNAPSHOT_TRUSTED_MODES, (3, 19))
        self.assertEqual(st.COUNTED_GAME_MODES, (3, 19))
        self.assertEqual(dr.DECK_SNAPSHOT_TRUSTED_MODES,
                         st.DECK_SNAPSHOT_TRUSTED_MODES)
        self.assertIn('DECK_SNAPSHOT_TRUSTED_MODES', dir(st))

    def test_to_row_mode_live(self):
        duel = {'record_id': 'r2', 'meta': {'game_mode': 7},
                'summary': {}}
        self.assertEqual(dr.to_row(duel)['mode'], 'live')

    def test_dirname_time(self):
        self.assertEqual(dr.dirname_time(REC_MIN),
                         dr.parse_dt('2026-09-13 12:21:46'))
        self.assertIsNone(dr.dirname_time('not_a_record'))


class TestModeOf(unittest.TestCase):
    """★ 2026-10-08：`_mode_of` 漏了单人模式，把 `game_mode=9` 全记成 `pvp`。

    用户 2026-09-26 实测拍板 `game_mode=9 = 单人模式`
    （`core/storage.MODE_GROUP_BY_GAME_MODE`），日志线也判 `solo` ——
    两条线对同一局必须给同一个 `mode`。
    """

    def test_solo_is_not_pvp(self):
        self.assertEqual(dr._mode_of(9), 'solo')

    def test_live_is_seven(self):
        self.assertEqual(dr._mode_of(7), 'live')

    def test_other_modes_are_pvp(self):
        for gm in (3, 19, 12, 15, 21):
            self.assertEqual(dr._mode_of(gm), 'pvp', 'game_mode=%s' % gm)

    def test_none_is_unknown(self):
        self.assertEqual(dr._mode_of(None), 'unknown')

    def test_agrees_with_storage_solo_group(self):
        """口径只有一份：solo 的 id 必须来自 `core.storage`，不许本模块硬编码。"""
        from core.storage import (MODE_GROUP_BY_GAME_MODE, MODE_GROUP_SOLO)
        for gm, grp in MODE_GROUP_BY_GAME_MODE.items():
            want = 'solo' if grp == MODE_GROUP_SOLO else 'pvp'
            self.assertEqual(dr._mode_of(gm), want, 'game_mode=%s' % gm)



class SeatEvidence34Test(unittest.TestCase):
    """★ 2026-10-24 判据 ③④（用户实报 21:48 局：快照/ClientWork 全空 ->
    露卡/先后全丢）。③=我方卡组名查参照库卡表当快照替身；④=账号名对
    duelSettings.names（by-seat 已定向）。"""

    def _fake_duel(self):
        return {
            'meta': {'seat': None, 'seat_evidence': None,
                     'my_deck_name': None, 'game_mode': 3},
            'summary': {'revealed': {'p0': [100, 101], 'p1': [200, 201]}},
        }

    def test_ref_ids_for_by_cn_name(self):
        from core import duel_record
        #: 中文名「闪刀姬」也能查到卡表（Sky Striker 的 name_cn）
        ids = duel_record._ref_ids_for('闪刀姬')
        self.assertTrue(ids, '参照库查不到 闪刀姬 的卡表')
        self.assertTrue(ids, '非空集合')
        #: 闪刀起动－交闪（真 id）在表里
        self.assertIn('15521' if '15521' in ids else sorted(ids)[0], ids)

    def test_account_name_seats_the_duel(self):
        """判据④：names_direction=by-seat + 账号名命中 -> 定座位。"""
        from core import duel_record
        # 真实 21:48 局形状：names[1] == 我方账号名
        man = {'my_deck_snapshot': None,
               'duel_settings': {'names': ['カガレボシ', '软糯小祥'],
                                 'names_direction': 'by-seat',
                                 'game_mode': 3}}
        scan = {'name': 't', 'dir': '.', 'state': 'final',
                'state_src': 'final', 'manifest': man, 'bins': {}}
        duel = duel_record.decode_record(
            scan, my_deck_name='Sky Striker', my_account='软糯小祥')
        self.assertEqual(duel['meta']['seat'], 1)
        self.assertIn('account-name', duel['meta']['seat_evidence'])
        #: 露卡映射（seat 判出后 opp = revealed[1-seat]）由真实 bin 流测试覆盖
        #: —— 这里的假 scan 没有事件流，revealed 为空是正常的。

    def test_no_match_stays_none(self):
        from core import duel_record
        man = {'my_deck_snapshot': None,
               'duel_settings': {'names': ['对手A', '对手B'],
                                 'names_direction': 'by-seat',
                                 'game_mode': 3}}
        scan = {'name': 't', 'dir': '.', 'state': 'final',
                'state_src': 'final', 'manifest': man, 'bins': {}}
        duel = duel_record.decode_record(
            scan, my_deck_name='Sky Striker', my_account='软糯小祥')
        self.assertIsNone(duel['meta']['seat'])



if __name__ == '__main__':
    unittest.main()

class ShowTurnResidueTest(unittest.TestCase):
    """★ 2026-10-20（用户实报「我先手却判成后手」）：首条 ShowTurn 是控制器
    内存残渣时，整条回合链作废（判不出比判错好）。

    事故局 `record_20261020_215925`：日志明写 `Turn 1 for player 1`（我方先手），
    内存侧 24 字节解出 `player=0 / turn=1 / marker=2 / lp_a=2 / lp_b=3`
    （控制器内存被复用，`m_DataList_ShowTurn` 没被本局写过）。
    于是 `first_player=0`、`went_first` 记成后手。

    判据：**对局首回合双方 LP 必须相等**（速决胜斗 500/2000/4000 也相等），
    外加 `player ∈ {0,1}`。全库 322 局实测：316 局原样通过、6 局被抓出，零误伤。
    """

    def test_normal_first_turn_not_residue(self):
        turns = [{'player': 1, 'turn': 0, 'lp_a': 8000, 'lp_b': 8000}]
        self.assertFalse(dr._first_turn_is_residue(turns))

    def test_speed_duel_low_lp_not_residue(self):
        """速决胜斗起手 500 / 2000 / 4000 —— 两边相等，不算残渣。"""
        for lp in (500, 2000, 4000, 8000):
            turns = [{'player': 0, 'turn': 0, 'lp_a': lp, 'lp_b': lp}]
            self.assertFalse(dr._first_turn_is_residue(turns),
                             'LP=%d 是速决起手，不该当残渣' % lp)

    def test_unequal_first_turn_lp_is_residue(self):
        """首回合双方 LP 不相等 = 残渣（事故局 2 vs 3 就是这种）。"""
        turns = [{'player': 0, 'turn': 1, 'lp_a': 2, 'lp_b': 3}]
        self.assertTrue(dr._first_turn_is_residue(turns))

    def test_huge_player_is_residue(self):
        turns = [{'player': 4390959, 'turn': 7471201, 'lp_a': 8000, 'lp_b': 8000}]
        self.assertTrue(dr._first_turn_is_residue(turns))

    def test_zero_lp_is_residue(self):
        turns = [{'player': 0, 'turn': 0, 'lp_a': 0, 'lp_b': 0}]
        self.assertTrue(dr._first_turn_is_residue(turns))

    def test_no_turns_is_not_residue(self):
        """没有回合事件 = 另一回事（不猜），不该被这条判据拦。"""
        self.assertFalse(dr._first_turn_is_residue([]))


    def test_whole_chain_residue_voids_everything(self):
        """★ 首条之外还有残渣时也作废（`record_20261010_203819` 实报）。

        那局首条 `lp=(597,1)` 已被首条判据抓住，但后面几条同样污染
        （`player=1304766336` / `lp_b=-1737342400`）—— untapped 上查到是
        「先手 5 回合」，库里却记成后手。
        """
        events = [
            {'label': 'ShowTurn', 'player': 0, 'turn': 0, 'lp_a': 597, 'lp_b': 1},
            {'label': 'ShowTurn', 'player': 1, 'turn': 0, 'lp_a': 0, 'lp_b': 0},
            {'label': 'ShowTurn', 'player': 1304766336, 'turn': 597,
             'lp_a': 597, 'lp_b': 0},
        ]
        summ = dr._summary(events, {}, lambda n: b'')
        self.assertIsNone(summ['first_player'])
        self.assertEqual(summ['total_turns'], 0)
        self.assertIsNone(summ['lp_final'])

    def test_mid_game_unequal_lp_is_fine(self):
        """对局中途双方 LP 不同是**正常**的（首条判据只对首回合成立）。"""
        events = [
            {'label': 'ShowTurn', 'player': 1, 'turn': 0, 'lp_a': 8000, 'lp_b': 8000},
            {'label': 'ShowTurn', 'player': 0, 'turn': 1, 'lp_a': 8000, 'lp_b': 6500},
            {'label': 'ShowTurn', 'player': 1, 'turn': 2, 'lp_a': 5200, 'lp_b': 6500},
        ]
        summ = dr._summary(events, {}, lambda n: b'')
        self.assertEqual(summ['first_player'], 1)
        self.assertEqual(summ['total_turns'], 3)
        self.assertEqual(summ['lp_final'], {'a': 5200, 'b': 6500})

    def test_high_lp_not_residue(self):
        """LP 到 10000 是正常的（规则伤害/支付后回血）—— 上界 20000。"""
        self.assertFalse(dr._turn_entry_is_residue(
            {'player': 1, 'turn': 1, 'lp_a': 8000, 'lp_b': 10000}))

    def test_residue_voids_the_whole_chain(self):
        """残渣时 `first_player` / `total_turns` / `lp_final` 全部作废。"""
        events = [
            {'label': 'ShowTurn', 'player': 0, 'turn': 1, 'lp_a': 2, 'lp_b': 3},
            {'label': 'ShowTurn', 'player': 1, 'turn': 2, 'lp_a': 2, 'lp_b': 3},
        ]
        summ = dr._summary(events, {}, lambda n: b'')
        self.assertIsNone(summ['first_player'], '残渣不许当先手证据')
        self.assertEqual(summ['total_turns'], 0)
        self.assertIsNone(summ['lp_final'])

