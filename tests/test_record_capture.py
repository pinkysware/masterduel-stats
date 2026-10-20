# -*- coding: utf-8 -*-
"""record_capture 单测（M6 刀 2）：契约 v1 manifest、目录复用三条件、
stale 判据、finalize 幂等、FakeMem 抓取整链（read_state -> write_record ->
duel_record 解码回读）。
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core import duel_record                                        # noqa: E402
from core.memread import record_capture as rc                       # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_named_typeinfo,    # noqa: E402
                         build_string, build_struct_list)

NOW = datetime(2026, 9, 13, 12, 10, 0)


def _scan(instance, state, fs, ls, size, name='record_x'):
    return {'name': name, 'state': state,
            'instance': int(instance, 16) if instance else None,
            'first_seen': fs, 'last_saved': ls, 'logbase_size': size}


class TestReuseThreeConditions(unittest.TestCase):
    """契约 §1.3 三条件：缺一新建、宁可多并、不可静默覆盖。"""

    def setUp(self):
        self.s = [_scan('0xaaa', 'open',
                        NOW - timedelta(minutes=5),
                        NOW - timedelta(seconds=30), 10)]

    def test_reuse_open_same_instance_near(self):
        self.assertEqual(rc.find_reusable_dir(self.s, '0xaaa', NOW, 12),
                         'record_x')

    def test_final_never_reused(self):
        self.s[0]['state'] = 'final'
        self.assertIsNone(rc.find_reusable_dir(self.s, '0xaaa', NOW, 12))

    def test_instance_differs_new_dir(self):
        """地址被新局复用但 size 判据兜底前，instance 不同就直接新建。"""
        self.assertIsNone(rc.find_reusable_dir(self.s, '0xbbb', NOW, 12))

    def test_size_regression_new_dir(self):
        """单调性：新观测 size 回退 = 地址被新局复用，必须新建。"""
        self.assertIsNone(rc.find_reusable_dir(self.s, '0xaaa', NOW, 9))

    def test_gap_exceeded_new_dir(self):
        old = NOW - timedelta(seconds=rc.MERGE_GAP_SEC + 1)
        self.s[0]['last_saved'] = old
        self.assertIsNone(rc.find_reusable_dir(self.s, '0xaaa', NOW, 12))

    def test_picks_latest_saved(self):
        self.s.append(_scan('0xaaa', 'open',
                            NOW - timedelta(minutes=3),
                            NOW - timedelta(seconds=10), 8,
                            name='record_newer'))
        self.assertEqual(rc.find_reusable_dir(self.s, '0xaaa', NOW, 9),
                         'record_newer')

    def test_no_instance_no_reuse(self):
        self.assertIsNone(rc.find_reusable_dir(self.s, None, NOW, 12))
        self.assertIsNone(rc.find_reusable_dir(self.s, '垃圾', NOW, 12))


class TestStaleOpen(unittest.TestCase):
    """终局化通道（P1-1）的判据。"""

    def test_all_open_flag(self):
        """终审 P3-2：pid 变化/游戏未运行 -> 全部 open 即 stale。"""
        scans = [_scan('0xaaa', 'open', NOW - timedelta(minutes=1),
                       NOW - timedelta(seconds=10), 5, name='fresh'),
                 _scan('0xbbb', 'final', NOW - timedelta(hours=9),
                       NOW - timedelta(hours=8), 5, name='done')]
        self.assertEqual(rc.find_stale_open(scans, NOW, all_open=True),
                         ['fresh'])

    def test_time_threshold(self):
        scans = [_scan('0xaaa', 'open', NOW - timedelta(hours=9),
                       NOW - timedelta(hours=7), 5, name='old'),
                 _scan('0xbbb', 'open', NOW - timedelta(minutes=2),
                       NOW - timedelta(seconds=30), 5, name='fresh'),
                 _scan('0xccc', 'final', NOW - timedelta(hours=9),
                       NOW - timedelta(hours=8), 5, name='done')]
        self.assertEqual(rc.find_stale_open(scans, NOW), ['old'])

    def test_missing_last_saved_is_stale(self):
        s = _scan('0xaaa', 'open', None, None, 5)
        self.assertEqual(rc.find_stale_open([s], NOW), [s['name']])


class TestFinalizeOpen(unittest.TestCase):
    """finalize_open：标 final + finalized_by + note 痕迹，幂等。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = os.path.join(self.tmp, 'record_20260913_120000')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, state):
        state_data = {'logbase': {'size': 2, 'entries': [
            {'label': 'ShowTurn', 'dataindex': 0},
            {'label': 'SeparateLine', 'dataindex': -1}]},
            'payload': {}, 'subs': []}
        return rc.write_record(
            self.d, state_data, instance_hex='0xabc', state=state,
            first_seen_at='2026-09-13 12:00:00', first_logbase_size=2)

    def test_finalize_marks_and_keeps_keys(self):
        self._make('open')
        man = rc.finalize_open(self.d, reason='stale-open')
        self.assertEqual(man['state'], 'final')
        self.assertEqual(man['finalized_by'], 'stale-open')
        self.assertIn('finalized:stale-open', man['note'])
        self.assertEqual(man['manifest_version'], 1)     # 其余键不动
        reread = duel_record.load_manifest(self.d)
        self.assertEqual(reread['state'], 'final')

    def test_finalize_final_is_noop(self):
        self._make('final')
        self.assertIsNone(rc.finalize_open(self.d))
        self._make('open')
        rc.finalize_open(self.d)
        self.assertIsNone(rc.finalize_open(self.d))      # 幂等


class TestWriteRecordContract(unittest.TestCase):
    """write_record 产出 = 契约 v1：decode 端（duel_record）能直接吃。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = os.path.join(self.tmp, 'record_20260913_120000')
        self.raw_turn = struct.pack('<6i', 1, 0, 1, 8000, 8000, 0)
        self.state_data = {
            'logbase': {'size': 3, 'entries': [
                {'label': 'ShowTurn', 'dataindex': 0},
                {'label': 'SeparateLine', 'dataindex': -1},
                {'label': 'ShowAction', 'dataindex': 0}]},
            'payload': {
                'ShowTurn': {'count': 1, 'stride': 24,
                             'bin': 'm_DataList_ShowTurn.bin',
                             'bytes': 24, 'raw': self.raw_turn},
                'ShowAction': {'count': 0}},
            'subs': [],
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, **kw):
        params = dict(instance_hex='0xabc', state='final',
                      first_seen_at='2026-09-13 12:00:00',
                      first_logbase_size=3, my_deck_snapshot=None,
                      game_mode=3, duel_settings=None)
        params.update(kw)
        return rc.write_record(self.d, self.state_data, **params)

    def test_manifest_v1_keys(self):
        man = self._write(duel_settings={'names': ['A', 'B'],
                                         'names_direction': None,
                                         'game_mode': 3,
                                         'duel_start_ts': 123})
        self.assertEqual(man['manifest_version'], 1)
        self.assertEqual(man['capture']['first_logbase_size'], 3)
        self.assertEqual(man['capture']['first_seen_at'],
                         '2026-09-13 12:00:00')
        self.assertEqual(man['capture']['last_saved_at'], man['saved_at'])
        self.assertIsNone(man['my_deck_snapshot'])       # 必填可空：键要在
        self.assertEqual(man['game_mode'], 3)
        self.assertEqual(man['duel_settings']['names'], ['A', 'B'])
        self.assertTrue(os.path.isfile(os.path.join(
            self.d, 'm_DataList_ShowTurn.bin')))

    def test_decode_roundtrip(self):
        self._write()
        scan = duel_record.scan_record(self.d)
        self.assertEqual(scan['state'], 'final')
        self.assertEqual(scan['state_src'], 'manifest')
        self.assertEqual(scan['instance'], 0xabc)
        self.assertFalse(scan['capture_approx'])         # capture 键就位
        self.assertEqual(scan['logbase_size'], 3)
        duel = duel_record.decode_record(scan)
        self.assertEqual(duel['summary']['total_turns'], 1)
        self.assertEqual(duel['summary']['first_player'], 1)
        self.assertEqual(duel['meta']['game_mode'], 3)
        self.assertIsNone(duel['meta']['opp_player_name'])   # 方向未定不写

    def test_opp_name_only_when_direction_confirmed(self):
        """P1-2：names_direction 明确才把 names[0] 当对手名。"""
        self._write(duel_settings={'names': ['对手A', '我'],
                                   'names_direction': 'index0=opp',
                                   'game_mode': None, 'duel_start_ts': None})
        duel = duel_record.decode_record(duel_record.scan_record(self.d))
        self.assertEqual(duel['meta']['opp_player_name'], '对手A')
        self._write(duel_settings={'names': ['对手A', '我'],
                                   'names_direction': None,
                                   'game_mode': None, 'duel_start_ts': None})
        duel = duel_record.decode_record(duel_record.scan_record(self.d))
        self.assertIsNone(duel['meta']['opp_player_name'])


class TestFakeMemCapture(unittest.TestCase):
    """FakeMem 整链：控制器 -> read_state -> write_record -> 解码回读。"""

    def setUp(self):
        self.mem = FakeMem()
        self.proc = ObjProc(self.mem)
        self.tmp = tempfile.mkdtemp()
        self.d = os.path.join(self.tmp, 'record_20260913_120000')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_controller(self):
        ti = build_named_typeinfo(self.mem, 'DuelLogController',
                                  [('m_LogBaseItemList', 0x10),
                                   ('m_DataList_ShowTurn', 0x18)])
        label_turn = build_string(self.mem, 'ShowTurn')
        label_sep = build_string(self.mem, 'SeparateLine')
        entry = lambda ptr, di: (struct.pack('<Q', ptr)
                                 + struct.pack('<i', di) + b'\x00' * 20)
        lb_raw = entry(label_turn, 0) + entry(label_sep, -1)
        lb = build_struct_list(self.mem, 32, lb_raw)
        turn_raw = struct.pack('<6i', 1, 0, 1, 8000, 8000, 0)
        st = build_struct_list(self.mem, 24, turn_raw)
        obj = self.mem.alloc(0x28)
        self.mem.put_u64(obj, 0x00, ti)
        self.mem.put_u64(obj, 0x10, lb)
        self.mem.put_u64(obj, 0x18, st)
        return obj

    def test_read_state_and_roundtrip(self):
        obj = self._build_controller()
        state = rc.read_state(self.proc, None, obj)
        self.assertEqual(state['logbase']['size'], 2)
        self.assertEqual(state['logbase']['entries'][0]['label'], 'ShowTurn')
        self.assertEqual(state['logbase']['entries'][1]['dataindex'], -1)
        self.assertEqual(state['payload']['ShowTurn']['count'], 1)
        self.assertEqual(len(state['payload']['ShowTurn']['raw']), 24)
        rc.write_record(self.d, state, instance_hex='0xfff', state='open',
                        first_seen_at='2026-09-13 12:00:00',
                        first_logbase_size=2)
        duel = duel_record.decode_record(duel_record.scan_record(self.d))
        self.assertEqual(duel['summary']['total_turns'], 1)
        self.assertEqual(duel['summary']['lp_final'], {'a': 8000, 'b': 8000})

    def test_read_state_empty_logbase(self):
        """控制器在但记录还空（开局一瞬间）-> None，调用方继续重试。"""
        ti = build_named_typeinfo(self.mem, 'DuelLogController',
                                  [('m_LogBaseItemList', 0x10)])
        lb = build_struct_list(self.mem, 32, b'')
        obj = self.mem.alloc(0x20)
        self.mem.put_u64(obj, 0x00, ti)
        self.mem.put_u64(obj, 0x10, lb)
        self.assertIsNone(rc.read_state(self.proc, None, obj))

    def test_find_controller_requires_host_field(self):
        ti = build_named_typeinfo(self.mem, 'DuelLogController', [])
        self.assertEqual(rc.find_controller(self.proc, None, 'tbl', ti),
                         (None, None))

    def test_logbase_size_over_cap_returns_none(self):
        """★ BUG-B3-03：垃圾 `_size`（2^31-1）不许拿去分配 68 GB。"""
        obj = self._build_controller()
        lb_obj = int.from_bytes(self.mem.load(obj + 0x10, 8), 'little')
        self.mem.put_i32(lb_obj, 0x18, 0x7FFFFFFF)   # _size 被垃圾值撑爆
        self.assertIsNone(rc.read_state(self.proc, None, obj))


class TestFindControllerBudget(unittest.TestCase):
    """★ BUG-B3-02：扫描必须有界（私有堆 + 墙钟预算）。"""

    class _Region(object):
        def __init__(self, name):
            self.base = name
            self.name = name

    class _ScanProc(ObjProc):
        """真 ObjProc（list_fields 要读字段表）+ 记录 scan 入参。"""

        def __init__(self, mem, hits=()):
            super(TestFindControllerBudget._ScanProc, self).__init__(mem)
            self.calls = []
            self._hits = list(hits)
            self.regions = [TestFindControllerBudget._Region('REGION-A'),
                            TestFindControllerBudget._Region('REGION-B')]

        def private_regions(self):
            return list(self.regions)

        def scan(self, pattern, max_hits=None, chunk=None, regions=None,
                 deadline=None):
            self.calls.append({'regions': regions, 'deadline': deadline})
            return iter(self._hits)

    def setUp(self):
        self.mem = FakeMem()
        self.ti = build_named_typeinfo(self.mem, 'DuelLogController',
                                       [('m_Host', 0x10)])

    def test_scan_is_limited_to_private_heap_with_deadline(self):
        proc = self._ScanProc(self.mem)
        self.assertEqual(rc.find_controller(proc, None, 'tbl', self.ti),
                         (None, None))
        self.assertEqual(len(proc.calls), 1)
        call = proc.calls[0]
        self.assertEqual([r.base for r in call['regions']],
                         ['REGION-A', 'REGION-B'])
        self.assertIsNotNone(call['deadline'], '没有墙钟预算 —— 会卡死采集器')

    def test_last_region_is_moved_to_front(self):
        """上次命中的区域前置，预算耗尽前一定覆盖它（与 end_message 同款）。"""
        proc = self._ScanProc(self.mem)
        rc.find_controller(proc, None, 'tbl', self.ti,
                           last_region=proc.regions[1])
        self.assertEqual([r.base for r in proc.calls[0]['regions']],
                         ['REGION-B', 'REGION-A'])

    def test_falls_back_to_full_scan_without_private_regions(self):
        """假进程没实现 private_regions -> regions=None（scan 自己回退全扫）。"""

        class _Plain(ObjProc):
            def scan(self, pattern, max_hits=None, regions=None, deadline=None):
                self.seen = regions
                return iter(())

        proc = _Plain(self.mem)
        rc.find_controller(proc, None, 'tbl', self.ti)
        self.assertIsNone(proc.seen)


class TestSameSourceSizes(unittest.TestCase):
    """KNOWN_SIZES 与 duel_record.ARRAY_SIZES 同源（契约 §2.2，漂移即红）。"""

    def test_overlapping_keys_match(self):
        for k, v in duel_record.ARRAY_SIZES.items():
            if k in rc.KNOWN_SIZES:
                self.assertEqual(rc.KNOWN_SIZES[k], v,
                                 '尺寸漂移：%s' % k)

class TestManifestAtomic(unittest.TestCase):
    """★ BUG-B8-10：manifest 走原子写。"""

    def test_dump_manifest_overwrites_and_writes_tmp_first(self):
        tmp = tempfile.mkdtemp(prefix='recman_')
        self.addCleanup(shutil.rmtree, tmp, True)
        rc._dump_manifest(tmp, {'a': 1})
        p = os.path.join(tmp, 'manifest.json')
        self.assertTrue(os.path.exists(p))
        rc._dump_manifest(tmp, {'a': 2})
        with open(p, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'a': 2})
        self.assertEqual([n for n in os.listdir(tmp) if n.endswith('.tmp')], [])

    def test_dump_manifest_failure_is_loud(self):
        tmp = tempfile.mkdtemp(prefix='recman2_')
        self.addCleanup(shutil.rmtree, tmp, True)
        with self.assertRaises(OSError):
            rc._dump_manifest(os.path.join(tmp, 'no-such-dir'), {'a': 1})




if __name__ == '__main__':
    unittest.main()
