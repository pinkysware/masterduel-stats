# -*- coding: utf-8 -*-
"""duel_collector 单测（M6 刀 3）：内存读取全经 Hooks 打桩，覆盖编排全路径。

重点断言：P2-2（控制器找到才建目录）、§1.3 复用、对局中**零扫描**、
掉线收口（终审 P2）、残影判据（P3-3）、有界退避、EM 去重与播种（P3-1）、
收局链 final + EM + import 失败降级、--emit-state 默认关。
"""

import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from tools.duel_collector import (                                   # noqa: E402
    DuelCollector, Hooks, TypeinfoCache, dir_name_for, import_just_closed,
    self_heal)

TS0 = '2026-09-13 12:00:00'
TS1 = '2026-09-13 12:05:00'
TS2 = '2026-09-13 12:10:00'
OBJ, FL = 1, {'fields': True}
TI = 0x7B

#: 单测里 `Storage` 的替身收到的调用（`infer_rank_for_record` / `repair_rank_for_record`）
DB_CALLS = {'infer': [], 'repair': []}


class FakeStorage(object):
    """★ 收集器单测里的 `Storage` 替身 —— **绝不许让单测写到真库**。

    这两条链会直接 UPDATE `matches`（`infer_rank_for_record` /
    `repair_rank_for_record`），而 record_dir 是**很像真**的目录名
    （`record_20260913_120000`）—— 撞上真库里同目录名的行就会改坏用户段位数据。
    """

    def infer_rank_for_record(self, record_dir, before, after):
        DB_CALLS['infer'].append((record_dir, before, after))
        return 1

    def repair_rank_for_record(self, record_dir, rank, rate):
        DB_CALLS['repair'].append((record_dir, rank, rate))
        return 1


def mk_state(size):
    return {'logbase': {'size': size,
                        'entries': [{'label': 'SeparateLine', 'dataindex': -1}]
                        * size},
            'payload': {}, 'subs': []}


class _FakeProc(object):
    """对象头校验桩：read_ptr(任意地址) 恒返回 TI（= 缓存里的对象是活的）。"""

    def read_ptr(self, addr):
        return TI


class StubHooks(Hooks):

    def __init__(self):
        self.scan_seq = []            # 每次 scan_controller 弹一个
        self.size_seq = []            # 每次 read_logbase_size 弹一个（然后回落 size）
        self.size = None              # read_logbase_size 的返回
        self.state = None             # read_state 的返回
        self.scans = []               # scan_records 的返回
        self.reuse = None
        self.instance_hex = '0xabc'
        self.deck = None
        self.settings = None
        self.rank = None              # read_my_rank 的返回
        self.rank_seq = []            # 每次 read_my_rank 弹一个（然后回落 rank）
        self.em = (None, None)
        self.duel_result = None        # read_duel_result 的返回
        self.duel_players = None       # read_duel_players 的返回
        self.my_deck_state = None      # poll_my_deck 的返回（编辑界面卡组）
        self.winbits = None           # read_winbits 的返回
        self.winbits_seq = []         # 每次 read_winbits 弹一个（然后回落 winbits）
        self.import_err = None
        self.scan_calls = 0
        self.state_calls = 0
        self.finalized = []
        self.imports = []
        self.rank_backfills = []         # backfill_rank 收到的目录名
        self.backfill_result = None      # 补写返回值（None = 没得补）
        self.backfill_err = None         # 非 None -> 抛（验不阻塞）
        self.em_appended = []
        self.state_frames = []
        self.state_files = []
        self.duel_code_backfills = []     # backfill_duel_code 收到的调用
        self.duel_code_backfill_msg = '结果（结算码提前入库）：Win（Win）'
        self.account_stamps = []          # backfill_account 收到的调用（多账号）

    def proc(self):
        return _FakeProc()

    def controller_ti(self):
        return TI

    def scan_controller(self):
        self.scan_calls += 1
        if self.scan_seq:
            return self.scan_seq.pop(0)
        return (OBJ, FL)

    def read_logbase_size(self, obj, fields):
        if self.size_seq:
            return self.size_seq.pop(0)
        return self.size

    def read_instance(self, obj):
        return self.instance_hex

    def read_state(self, obj, fields):
        self.state_calls += 1
        return self.state

    def scan_records(self):
        return list(self.scans), []

    def load_reuse_manifest(self, name):
        return self.reuse

    def snapshot_deck(self):
        return self.deck

    def read_duel_settings(self):
        return self.settings

    def snapshot_peak_deck(self):
        # 巅峰 own-deck（09-28）：默认 None；要测分流时给 self.peak_deck 赋值
        return getattr(self, 'peak_deck', None)

    def read_my_rank(self):
        if self.rank_seq:
            return self.rank_seq.pop(0)
        return self.rank

    def read_end_message(self):
        return self.em

    def read_winbits(self, instance):
        if self.winbits_seq:
            return self.winbits_seq.pop(0)
        return self.winbits

    def read_duel_result(self):
        return self.duel_result

    def backfill_duel_code(self, dir_name, code, code_name):
        self.duel_code_backfills.append((dir_name, code, code_name))
        return self.duel_code_backfill_msg

    def backfill_account(self, dir_name, name, pcode):
        self.account_stamps.append((dir_name, name, pcode))
        return '账号章：%s' % name

    def read_duel_players(self):
        return self.duel_players

    def poll_my_deck(self, now=None):
        return self.my_deck_state

    def append_em(self, rec):
        self.em_appended.append(rec)

    def import_record(self, name):
        self.imports.append(name)
        if self.import_err is not None:
            raise self.import_err
        return {'merged': 1}

    def backfill_rank(self, name):
        self.rank_backfills.append(name)
        if self.backfill_err is not None:
            raise self.backfill_err
        return self.backfill_result

    def finalize(self, dir_path, reason):
        self.finalized.append((dir_path, reason))
        return {}

    def read_duel_state(self):
        return self.state_frames.pop(0) if self.state_frames else None

    def write_duel_state_file(self, frame):
        self.state_files.append(frame)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        os.makedirs(self.records)
        self.st = StubHooks()
        self.lines = []
        self.c = DuelCollector(records_dir=self.records, hooks=self.st,
                               log=self.lines.append)
        #: ---- 全局态隔离（2026-09-18 深夜）----
        #: ① 段位推断状态文件：`rank_state.save()` 默认写 **data/rank_state.json**
        #:    （真机数据！）—— 指到 tmp 去；
        #: ② 直连真库的 Storage：换成记账替身（见 FakeStorage）。
        import core.rank_state as rank_state
        import core.storage as storage_mod
        self._orig_rank_path = rank_state.STATE_PATH
        self._orig_storage = storage_mod.Storage
        rank_state.STATE_PATH = os.path.join(self.tmp, 'rank_state.json')
        storage_mod.Storage = FakeStorage
        DB_CALLS['infer'][:] = []
        DB_CALLS['repair'][:] = []
        self.addCleanup(setattr, rank_state, 'STATE_PATH', self._orig_rank_path)
        self.addCleanup(setattr, storage_mod, 'Storage', self._orig_storage)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest_of(self, name):
        with open(os.path.join(self.records, name, 'manifest.json'),
                  encoding='utf-8') as f:
            return json.load(f)


class TestLifecycle(Base):

    def test_duel_start_no_dir_until_controller(self):
        """P2-2：duel_start 只钉 first_seen；控制器没找到就不建目录。"""
        self.st.scan_seq = [(None, None)]
        self.c.observe(TS0, True, game_mode=3, now=1000.0)
        self.assertIsNotNone(self.c.open_rec)
        self.assertEqual(self.c.open_rec.first_seen_at, TS0)
        self.assertEqual(self.c.open_rec.game_mode, 3)
        self.assertIsNone(self.c.open_rec.dir_path)
        self.assertEqual(os.listdir(self.records), [])      # 没有空目录

    def test_controller_found_creates_v1_manifest(self):
        self.st.scan_seq = [(None, None)]
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, game_mode=3, now=1000.0)
        self.c.observe(TS0, True, game_mode=3, now=1002.0)  # 这拍找到控制器
        name = dir_name_for(TS0)
        man = self.manifest_of(name)
        self.assertEqual(man['manifest_version'], 1)
        self.assertEqual(man['state'], 'open')
        self.assertEqual(man['capture']['first_seen_at'], TS0)
        self.assertEqual(man['capture']['first_logbase_size'], 3)
        self.assertIsNone(man['my_deck_snapshot'])          # 必填可空键要在
        self.assertEqual(man['game_mode'], 3)

    def test_throttle_and_zero_scan_in_duel(self):
        """对局中零扫描（预算制断言）+ 落盘节流（>=write_gap 才全量重抓）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)            # 找到 + 首版落盘
        self.assertEqual(self.st.scan_calls, 1)
        self.st.size = 3
        self.c.observe(TS0, True, 3, now=1004.0)            # size 没变
        self.assertEqual(self.st.state_calls, 1)
        self.st.size = 4
        self.st.state = mk_state(4)
        self.c.observe(TS0, True, 3, now=1004.8)            # 变了但距上次 <5s
        self.assertEqual(self.st.state_calls, 1)
        self.c.observe(TS0, True, 3, now=1008.0)            # 距上次 >=5s
        self.assertEqual(self.st.state_calls, 2)
        self.assertEqual(self.manifest_of(dir_name_for(TS0))['logbase']['size'],
                         4)
        self.assertEqual(self.st.scan_calls, 1)             # 全程零扫描

    def test_reuses_open_dir_three_conditions(self):
        """§1.3：instance 相同 + open + size 单调 -> 复用目录不劈局。"""
        now = datetime.now()
        self.st.scans = [{'name': 'record_20260913_115000', 'state': 'open',
                          'instance': 0xabc,
                          'first_seen': now - timedelta(minutes=5),
                          'last_saved': now - timedelta(seconds=30),
                          'logbase_size': 2}]
        self.st.reuse = {'capture': {'first_seen_at': '2026-09-13 11:50:00'}}
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        name = 'record_20260913_115000'
        man = self.manifest_of(name)
        self.assertEqual(man['capture']['first_seen_at'],
                         '2026-09-13 11:50:00')             # 沿用原锚
        self.assertEqual(man['state'], 'open')
        self.assertFalse(os.path.exists(os.path.join(
            self.records, dir_name_for(TS0))))              # 没建新目录

    def test_pvp_finished_final_em_import(self):
        """收局链顺序：final 落盘 -> EM -> import 增量入库。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = ({'messageText': '对手已投降', 'player0Name': '我',
                       'player1Name': '对手'}, {'winbits': [1, 0],
                                                'instance': '0x9', 'pid': 5})
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        man = self.manifest_of(dir_name_for(TS0))
        self.assertEqual(man['state'], 'final')
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'win')
        # 戳上本局目录名（配对精确认领用，2026-09-16 EM 误配事故回归锁）
        self.assertEqual(self.st.em_appended[0]['record_id'],
                         dir_name_for(TS0))
        self.assertEqual(self.st.imports, [dir_name_for(TS0)])
        self.assertIsNone(self.c.open_rec)

    def test_my_index_goes_into_manifest(self):
        """开局读到的我方座位要落进 manifest（活动局先/后的唯一来源）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.st.settings = {'names': ['对手', '我'],
                            'names_direction': 'by-seat', 'game_mode': 21}
        self.st.duel_players = {'my_index': 1, 'name': ['对手', '我']}
        self.c.observe(TS0, True, 21, now=1000.0)
        self.c.observe(TS0, True, 21, now=1001.0)
        man = self.manifest_of(dir_name_for(TS0))
        self.assertEqual(man['duel_settings']['my_index'], 1)
        self.assertEqual(man['duel_settings']['names'], ['对手', '我'])

    def test_absent_my_index_is_not_invented(self):
        """读不到就不带这个键（缺 = 判据②不可用，不是 0）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.st.settings = {'names': ['对手', '我'],
                            'names_direction': 'by-seat', 'game_mode': 21}
        self.st.duel_players = {'my_index': None}
        self.c.observe(TS0, True, 21, now=1000.0)
        self.c.observe(TS0, True, 21, now=1001.0)
        man = self.manifest_of(dir_name_for(TS0))
        self.assertNotIn('my_index', man['duel_settings'])

    def test_poll_my_deck_logs_when_changed(self):
        """编辑界面读到/换了卡组 -> 出行「当前卡组：…」（用户口径）。"""
        self.st.my_deck_state = {'name': 'Blue-Eyes', 'card_count': 56}
        lines = self.c.observe(TS0, False, now=1000.0)
        self.assertTrue(any('当前卡组：Blue-Eyes' in x for x in lines), lines)
        self.st.my_deck_state = None            # 没变化 -> 不出行
        lines = self.c.observe(TS0, False, now=1002.0)
        self.assertFalse(any('当前卡组' in x for x in lines), lines)

    def test_disconnect_em_falls_back_to_duel_code(self):
        """掉线局（2026-09-16 拍板）：winbits 恒 None，结算码兜底 mem_result。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.st.duel_result = {'result': 1, 'result_name': 'Win'}
        self.st.em = ({'messageText': '由于网络中断，决斗已经结束',
                       'player0Name': '我', 'player1Name': '对手'},
                      {'instance': '0x9', 'pid': 5})   # 没有 winbits
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        for i in range(10):                       # 结算窗口内推进到超时
            self.c.observe(TS1, False, now=1020.0 + i * 10)
        self.assertEqual(len(self.st.em_appended), 1)
        rec = self.st.em_appended[0]
        self.assertIsNone(rec['winbits'])
        self.assertEqual(rec['mem_result'], 'win')
        self.assertEqual(rec['mem_result_src'], 'duel_code')
        self.assertEqual(rec['duel_code'], 1)

    def test_import_failure_degrades(self):
        """入库失败只记日志，绝不炸采集线程（P2-1 降级口径）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.st.import_err = RuntimeError('db locked')
        self.c.observe(TS0, True, 3, now=1000.0)
        lines = self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertIsNone(self.c.open_rec)                  # 收局照常完成
        self.assertTrue(any('import' in x for x in self.lines))

    def test_instance_gone_finalizes(self):
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, False, now=1010.0)              # 回主界面
        self.assertEqual(self.st.finalized,
                         [(os.path.join(self.records, dir_name_for(TS0)),
                           'instance-gone')])
        self.assertIsNone(self.c.open_rec)

    def test_disconnect_timeout_closes(self):
        """终审 P2：连续 in_duel=None >= 120s 才收口（单次跳过不收）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, None, now=1010.0)               # 第一拍 None
        self.assertEqual(self.st.finalized, [])
        self.c.observe(TS1, None, now=1012.0)               # 连续中，未超时
        self.assertEqual(self.st.finalized, [])
        self.c.observe(TS1, None, now=1135.0)               # 连续 125s
        self.assertEqual(self.st.finalized[0][1], 'disconnect-or-unknown')
        self.assertIsNone(self.c.open_rec)

    def test_single_none_then_back_keeps_duel(self):
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, None, now=1002.0)
        self.c.observe(TS1, True, 3, now=1004.0)            # 又读到了
        self.assertIsNotNone(self.c.open_rec)
        self.assertEqual(self.st.finalized, [])

    def test_remnant_controller_skipped(self):
        """审查 P1-1 修订：残影=上下文判据 —— 「刚收局且 size 偏大」或
        「size 相对上一拍回退」；不再用绝对阈值。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)            # 建目录
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)    # 收局
        self.c.observe(TS1, False, now=1015.0)              # 回主界面
        self.st.em = (None, None)
        self.st.scans = []                                  # 没有可复用的 open 目录
        self.st.size_seq = [45, 3]                          # 残影 45 → 新控制器 3
        scans_before = self.st.scan_calls                   # 第一局已用 1 次
        self.c.observe(TS2, True, 3, now=1020.0)            # 45：刚收局+偏大 → 残影
        self.assertIsNone(self.c.open_rec.dir_path)
        self.c.observe(TS2, True, 3, now=1022.0)            # 3 < 45：回退 → 残影
        self.assertIsNone(self.c.open_rec.dir_path)
        self.assertEqual(self.st.scan_calls, scans_before + 2)
        self.assertEqual(self.st.state_calls, 2)            # 第一局 open+final 各一次

    def test_big_size_fresh_start_not_remnant(self):
        """审查 P1-1：没有「刚收局」上下文（进程重启接管半局）→ size 大也建目录。"""
        self.st.size = 200
        self.st.state = mk_state(200)
        self.c.observe(TS0, True, 3, now=1000.0)
        man = self.manifest_of(dir_name_for(TS0))
        self.assertEqual(man['state'], 'open')
        self.assertEqual(man['logbase']['size'], 200)

    def test_restart_reuses_open_dir_big_size(self):
        """验收 §八.4 实锤（审查 P1-1）：中途重启 + 已有 open 目录（size=50）
        + 首见 size=52 -> **先判复用**、命中即复用，残影判据不参与。"""
        now = datetime.now()
        self.st.scans = [{'name': 'record_20260913_115000', 'state': 'open',
                          'instance': 0xabc,
                          'first_seen': now - timedelta(minutes=30),
                          'last_saved': now - timedelta(seconds=20),
                          'logbase_size': 50}]
        self.st.reuse = {'capture': {'first_seen_at': '2026-09-13 11:30:00',
                                     'first_logbase_size': 2}}
        self.st.size = 52
        self.st.state = mk_state(52)
        self.c.observe(TS0, True, 3, now=1000.0)
        man = self.manifest_of('record_20260913_115000')
        self.assertEqual(man['capture']['first_seen_at'], '2026-09-13 11:30:00')
        self.assertEqual(man['state'], 'open')
        self.assertFalse(os.path.exists(os.path.join(
            self.records, dir_name_for(TS0))))              # 没劈局

    def test_finish_rescan_regression_skips_overwrite(self):
        """审查 P3-1：收局兜底补扫 size 回退 = 残影，不覆盖已落盘内容。"""
        self.st.size = 10
        self.st.state = mk_state(10)
        self.c.observe(TS0, True, 3, now=1000.0)            # dir + last_size=10
        self.c.cache.invalidate()
        self.st.scan_seq = [(OBJ, FL)]
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertEqual(self.manifest_of(dir_name_for(TS0))['logbase']['size'],
                         10)                                # 没被 5 覆盖
        self.assertEqual(self.st.finalized,
                         [(os.path.join(self.records, dir_name_for(TS0)),
                           'finish-no-data')])

    def test_instance_gone_captures_em(self):
        """审查 P3-2：instance-gone 收口也顺手抓一次 EM（失败就算了）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.st.em = ({'messageText': '已投降', 'player0Name': '我',
                       'player1Name': '对手'}, {'winbits': [0, 1]})
        self.c.observe(TS1, False, now=1010.0)
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.finalized[0][1], 'instance-gone')

    def test_duel_settings_retry_backfills_when_late(self):
        # 09-27 海皇局锁：duel_start 那拍 settings 未就绪（None），对局中
        # 每拍重试，读到即回填 rec.settings —— 判据(2)才有 my_index 可用。
        self.st.settings = None
        self.c.observe(TS0, True, game_mode=3, now=1000.0)
        self.assertIsNone(self.c.open_rec.settings)
        self.st.settings = {'names': ['A', 'B'],
                            'names_direction': 'by-seat', 'my_index': 0}
        lines = self.c.observe(TS1, True, game_mode=3, now=1002.0)
        self.assertEqual(self.c.open_rec.settings.get('my_index'), 0)
        self.assertTrue(any('duel_settings 补读成功' in x for x in lines),
                        lines)

    def test_duel_settings_retry_no_refill_after_success(self):
        # 读到一次就停（settings 非 None 不再读，别每拍浪费）。
        self.st.settings = {'names': ['A', 'B'], 'my_index': 1}
        self.c.observe(TS0, True, game_mode=3, now=1000.0)
        self.st.settings = None                    # 之后读不到也不影响
        lines = self.c.observe(TS1, True, game_mode=3, now=1002.0)
        self.assertEqual(self.c.open_rec.settings.get('my_index'), 1)
        self.assertFalse(any('duel_settings 补读成功' in x for x in lines))

    def test_snapshot_only_trusted_for_3_19(self):
        """实机发现（验收局 2）：活动局的常规卡组快照是错值（读到大厅的
        闪刀姬）——非 3/19 局存 null；3/19 局保留。"""
        self.st.deck = {'deck_id': 48171157, 'deck_name': '闪刀姬',
                        'ids': [20500] * 3}
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, game_mode=21, now=1000.0)   # 活动局
        self.c.observe(TS0, True, game_mode=21, now=1002.0)
        self.assertIsNone(
            self.manifest_of(dir_name_for(TS0))['my_deck_snapshot'])
        # 收局 -> 回主界面 -> 3/19 局：快照保留
        self.st.em = (None, None)
        self.c.observe(TS1, True, game_mode=21, finished=1, now=1010.0)
        self.c.observe(TS1, False, now=1015.0)
        self.c.observe(TS2, True, game_mode=3, now=1020.0)    # size=3 非残影
        self.assertEqual(self.manifest_of(dir_name_for(TS2))['my_deck_snapshot'],
                         self.st.deck)

    def test_game_mode_takes_last_non_null_observation(self):
        """终审 Q3 前提：写盘的 game_mode 取「最后一次非空观测」。

        否则 duel_start 那拍读到瞬时空值时，一局真天梯会被判成「模式不可信」——
        快照白丢、game_mode 也写了个 null。
        """
        self.st.deck = {'deck_id': 48171157, 'deck_name': '闪刀姬',
                        'ids': [20500] * 3}
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, game_mode=None, now=1000.0)   # 首拍：mode 还没就位（落盘一次）
        self.c.observe(TS0, True, game_mode=3, now=1002.0)      # 随后读到天梯（节流内）
        self.st.size = 4                                        # logbase 又长一条
        self.c.observe(TS0, True, game_mode=None, now=1010.0)   # 这拍真落盘：不许被 None 冲掉
        man = self.manifest_of(dir_name_for(TS0))
        self.assertEqual(man['game_mode'], 3, '最后一次非空观测')
        self.assertEqual(man['my_deck_snapshot'], self.st.deck,
                         '瞬时 None 不该误丢天梯局的快照')

    def test_unknown_mode_snapshot_is_kept_as_evidence(self):
        """模式**始终**未知 -> 层 0 仍留原样快照（证据），采信由出口决定。"""
        self.st.deck = {'deck_id': 1, 'deck_name': '某卡组', 'ids': [20500] * 3}
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, game_mode=None, now=1000.0)
        self.c.observe(TS0, True, game_mode=None, now=1002.0)
        man = self.manifest_of(dir_name_for(TS0))
        self.assertIsNone(man['game_mode'])
        self.assertEqual(man['my_deck_snapshot'], self.st.deck)

    def test_close_current_silent_without_dir(self):
        """审查 P3-7：没开过局就收口，不再打「无 record 目录」的误导日志。"""
        self.assertEqual(self.c.observe(TS1, False, now=1000.0), [])
        self.assertEqual(self.c.observe(TS1, None, now=1002.0), [])

    def test_hunt_limit_switches_to_slow_retry(self):
        """09-27 实锤改：hunt 超时**不再彻底放弃** —— 降频（30s/次）继续找。

        12:04 局控制器 60s 内没出现，之后整局静默丢失（无 record 无入库）。
        超时后找到控制器必须照常建目录续采。
        """
        self.st.scan_seq = [(None, None), (None, None), (None, None),
                            (OBJ, FL)]
        self.st.size_seq = [3, 3]                   # hunt 命中 + 主路径各读一次 size
        self.c.observe(TS0, True, 3, now=1000.0)            # scan 1
        self.c.observe(TS0, True, 3, now=1002.0)            # scan 2
        self.c.observe(TS0, True, 3, now=1063.0)            # 63s：降频第一次（仍 None）
        self.assertGreaterEqual(self.st.scan_calls, 3)
        self.c.observe(TS0, True, 3, now=1090.0)            # 间隔 27s <30s：不扫
        calls_after_gap = self.st.scan_calls
        self.c.observe(TS0, True, 3, now=1126.0)            # 36s 到：scan 找到
        self.assertGreater(self.st.scan_calls, calls_after_gap)
        self.assertIsNotNone(self.c.open_rec.dir_path, '找到控制器要建目录续采')
        self.assertTrue(any('降为每' in x for x in self.lines), self.lines)

    def test_growing_size_is_not_remnant(self):
        """★ 残影判据②不许拒绝**正在长大**的控制器（2026-09-18 用户漏局实锤）。

        新局开局 22s 时上一局刚收（90s 窗口内），真新局的 43→46→52 被当成残影
        连续拒绝 -> 60s hunt 预算烧光 -> 整局没建目录 -> 没落盘没入库。
        """
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)    # 收局（_last_finish_ts）
        self.c.observe(TS1, False, now=1015.0)                  # 大厅一拍（tracker 要靠它认下一局）
        self.st.size_seq = [43, 46, 52]                        # 新局：size 在涨
        self.c.observe(TS2, True, 3, now=1020.0)               # 43：刚收局+偏大 -> 拒
        self.assertIsNone(self.c.open_rec.dir_path)
        self.c.observe(TS2, True, 3, now=1022.0)               # 46 > 43：在涨 -> 收
        self.assertIsNotNone(self.c.open_rec.dir_path, '正在长大的 size 是活局')

    def test_frozen_size_still_rejected_as_remnant(self):
        """真残影（size 不动）照旧拒绝 —— 别把防线整个拆了。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.c.observe(TS1, False, now=1015.0)                  # 大厅一拍
        self.st.size = 46                                       # 冻结的旧局快照
        self.c.observe(TS2, True, 3, now=1020.0)
        self.assertIsNone(self.c.open_rec.dir_path)
        self.c.observe(TS2, True, 3, now=1022.0)               # 还是 46：没涨
        self.assertIsNone(self.c.open_rec.dir_path, 'size 不动的还是残影')

    def test_new_duel_invalidates_cache(self):
        """地址复用防线：新局 duel_start 强制作废控制器缓存（终审 §五.1）。"""
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)    # 收局 1
        self.c.observe(TS1, False, now=1500.0)                  # 回主界面
        self.st.em = (None, None)
        self.st.imports.clear()
        scans_before = self.st.scan_calls
        self.c.observe(TS2, True, 3, now=2000.0)                # 新局 start
        self.c.observe(TS2, True, 3, now=2002.0)                # 需要重扫
        self.assertEqual(self.st.scan_calls, scans_before + 1)
        self.assertEqual(self.c.open_rec.first_seen_at, TS2)


class TestEndMessageDedup(Base):
    """终审 P3-1：残渣过滤 + 签名去重 + 重启播种。"""

    def test_junk_filtered(self):
        self.st.em = ({'messageText': '12345', 'player0Name': '',
                       'player1Name': '12'}, {'pid': 1})
        self.assertIsNone(self.c.capture_end_message())
        self.assertEqual(self.st.em_appended, [])

    def test_dedup_same_texts_once(self):
        self.st.em = ({'messageText': '对手已投降', 'player0Name': '我',
                       'player1Name': '对手'}, {'winbits': [1, 0]})
        self.assertIsNotNone(self.c.capture_end_message())
        self.assertIsNone(self.c.capture_end_message())     # 同文案只记一次
        self.assertEqual(len(self.st.em_appended), 1)

    def test_seed_from_existing_records(self):
        old_texts = {'messageText': '旧', 'player0Name': '我',
                     'player1Name': '对手'}
        self.c.seed_em([{'texts': old_texts}])
        self.st.em = (dict(old_texts), {})
        self.assertIsNone(self.c.capture_end_message())
        self.st.em = ({'messageText': '新文案', 'player0Name': '我',
                       'player1Name': '对手'}, {})
        self.assertIsNotNone(self.c.capture_end_message())

    def test_no_message_text_ignored(self):
        self.st.em = ({'player0Name': '我'}, {})
        self.assertIsNone(self.c.capture_end_message())


class TestEmitState(Base):
    """--emit-state 给 M7 的接口：默认关；开着每拍覆盖写一帧。"""

    def test_off_by_default(self):
        self.st.size = 3
        self.st.state = mk_state(3)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.assertEqual(self.st.state_files, [])

    def test_on_writes_frame_each_tick(self):
        self.st.size = 3
        self.st.state = mk_state(3)
        c = DuelCollector(records_dir=self.records, hooks=self.st,
                          emit_state=True, log=lambda m: None)
        self.st.state_frames = [{'turn': 0}]
        c.observe(TS0, True, 3, now=1000.0)
        self.st.state_frames = [{'turn': 1}]
        c.observe(TS0, True, 3, now=1002.0)
        self.assertEqual(self.st.state_files, [{'turn': 0}, {'turn': 1}])


    def test_no_proc_shadowing(self):
        """实机局 1 教训：Runtime 曾用 self.proc 属性存 SafeProcess，把
        Hooks.proc() 方法遮蔽成不可调用，每拍 TypeError、整局丢数据。
        源码扫描禁止再出现该赋值。"""
        import inspect

        import tools.duel_collector as m
        src = inspect.getsource(m)
        self.assertNotIn('self.proc =', src)
        self.assertNotIn('rt.proc =', src)
        self.assertNotIn('self.proc)', src.replace('self.hooks.proc()', ''))
        # 接口方法仍在且 Runtime 可实例化（不 attach，不碰进程）
        self.assertTrue(callable(m.Runtime.proc))
        rt = m.Runtime('D:\\nowhere', lambda msg: None)
        self.assertIsNone(rt.proc())


class TestTypeinfoCache(unittest.TestCase):
    """审查 P2-1：TypeInfo 记忆化 —— 每类只允许一次全内存扫描。"""

    def test_memoized(self):
        calls = []
        cache = TypeinfoCache(
            lambda ns, name: calls.append((ns, name)) or 0x7B)
        self.assertEqual(cache.get('YgomSystem.Utility', 'ClientWork'), 0x7B)
        self.assertEqual(cache.get('YgomSystem.Utility', 'ClientWork'), 0x7B)
        self.assertEqual(calls, [('YgomSystem.Utility', 'ClientWork')])
        self.assertEqual(cache.misses, 1)


class TestDirName(unittest.TestCase):

    def test_dir_name_format(self):
        self.assertEqual(dir_name_for('2026-09-13 12:00:00'),
                         'record_20260913_120000')


class TestMyDeckLines(Base):
    """我方卡组的三道防线（2026-09-18 九轮，用户需求：换装备卡组不进编辑器也能跟上）。"""

    DECK_SKY = {'deck_id': 1, 'deck_name': '闪刀姬',
                'ids': [21227, 21460, 3434] * 4}
    DECK_BLUE = {'deck_id': 2, 'deck_name': '青眼白龙',
                 'ids': [4007, 89631115, 74641048] * 4}

    def setUp(self):
        super().setUp()
        self.state_path = os.path.join(self.tmp, 'my_deck.json')
        self.c.identify_my_deck = lambda ids: (
            'Sky Striker' if 21227 in list(ids) else 'Blue-Eyes')
        self.st.my_deck_repairs = []

        def _fake_repair(d, n):
            self.st.my_deck_repairs.append((d, n))
            return '卡组确认入库：%s -> %s（本局行）' % (d, n)
        self.st.repair_my_deck = _fake_repair
        self.st.import_rec = []
        self.c.identify = lambda ids: {
            id(self.DECK_SKY): 'Sky Striker',
            id(self.DECK_BLUE): 'Blue-Eyes',
        }.get(id(sorted(set(int(i) for i in ids))))
        import core.my_deck as my_deck
        # ⚠️ `my_deck` 的路径改为**读取时**解析 `MD_MY_DECK_STATE`（2026-09-22），
        #    打模块属性补丁不再生效 —— 改用环境变量。
        self._orig_env = os.environ.get('MD_MY_DECK_STATE')
        os.environ['MD_MY_DECK_STATE'] = self.state_path
        my_deck.save('Blue-Eyes', ids=self.DECK_BLUE['ids'], src='edit',
                     path=self.state_path)

    def tearDown(self):
        import core.my_deck as my_deck
        if self._orig_env is None:
            os.environ.pop('MD_MY_DECK_STATE', None)
        else:
            os.environ['MD_MY_DECK_STATE'] = self._orig_env
        super().tearDown()

    # ---- ① 大厅节流识别 ----
    def test_lobby_check_updates_state_once_per_window(self):
        """09-28 起两拍防抖：第一拍挂候选（**不写**），第二拍同指纹才落盘。"""
        self.st.deck = self.DECK_SKY
        self.c.lobby_check_sec = 60.0
        self.assertIsNone(self.c._lobby_check_at)
        self.c.observe(TS0, False, now=1000.0)          # 第一拍：挂候选
        self.assertEqual(self.c._lobby_check_at, 1000.0)
        import core.my_deck as my_deck
        self.assertEqual(my_deck.current_name(path=self.state_path),
                         'Blue-Eyes')                   # 候选期还没写
        self.assertEqual(self.c.observe(TS0, False, now=1030.0), [])   # 30s 内节流
        lines = self.c.observe(TS0, False, now=1061.0)   # 第二拍：确认落盘
        self.assertTrue(any('大厅识别' in x for x in lines), lines)
        self.assertEqual(my_deck.current_name(path=self.state_path), 'Sky Striker')
        self.assertEqual(self.c.observe(TS0, False, now=1092.0), [])   # 30s 内节流
        self.assertEqual(self.c.observe(TS0, False, now=1123.0), [])   # 同指纹：再确认（空转）
        self.assertEqual(my_deck.current_name(path=self.state_path), 'Sky Striker')

    def test_lobby_check_debounces_transition_noise(self):
        """★ 09-27/28 实锤：回大厅瞬间的**过渡值**让识别抖动（确认条确认了又弹）——
        两拍防抖下指纹变了就作废候选，绝不用过渡值盖 my_deck。"""
        self.st.deck = self.DECK_SKY
        self.c.lobby_check_sec = 60.0
        self.c.observe(TS0, False, now=1000.0)          # 第一拍：挂 Sky Striker 候选
        self.st.deck = {'deck_name': 'Tearlaments', 'ids': [9, 8, 7, 6, 5, 4]}
        lines = self.c.observe(TS0, False, now=1061.0)   # 第二拍指纹变了：作废
        self.assertTrue(any('识别抖动' in x for x in lines), lines)
        import core.my_deck as my_deck
        self.assertEqual(my_deck.current_name(path=self.state_path), 'Blue-Eyes')
        self.assertIsNone(self.c._lobby_pending_fp)

    def test_lobby_check_unknown_deck_never_overwrites(self):
        self.st.deck = {'deck_name': '?', 'ids': [1, 2, 3, 4, 5, 6]}
        self.c.lobby_check_sec = 0.0
        self.c.observe(TS0, False, now=1000.0)
        import core.my_deck as my_deck
        self.assertEqual(my_deck.current_name(path=self.state_path), 'Blue-Eyes')

    # ---- ② 对局内快照重试 ----
    def test_deck_snapshot_retry_gives_up_after_300s(self):
        """★ 审查 P2-3：可信模式但容器整局未就绪 → 300s 后放弃，不再每拍重试。"""
        self.st.deck = None
        self.st.scan_seq = [({'obj': 1}, {'fields': 1})]
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.assertTrue(self.c._deck_snapshot_pending)
        self.c.observe(TS1, True, 3, now=1005.0)
        self.assertTrue(self.c._deck_snapshot_pending)
        lines = self.c.observe(TS1, True, 3, now=1310.0)       # > 300s：放弃
        self.assertFalse(self.c._deck_snapshot_pending)
        self.assertTrue(any('放弃' in x for x in lines), lines)
        self.c.observe(TS1, True, 3, finished=1, now=1315.0)   # 之后再收局

    def test_deck_snapshot_retry_when_null_at_start(self):
        self.st.deck = None                                    # duel_start 没就绪
        self.st.scan_seq = [({'obj': 1}, {'fields': 1})]
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.assertTrue(self.c._deck_snapshot_pending)
        self.st.deck = self.DECK_SKY                           # 对局中就绪
        lines = self.c.observe(TS1, True, 3, now=1010.0)
        self.assertFalse(self.c._deck_snapshot_pending)
        self.assertTrue(any('快照（重试）' in x for x in lines), lines)

    # ---- ③ 第一局确认（打出的卡）----
    def test_confirm_does_not_touch_my_deck(self):
        """★ 审查 P2-2 + 2026-09-18 晚实锤：确认**不再做 revealed 反推**。

        `revealed.p0/p1` 的键是座位号（seat=1 的局 p0 装的是对手 27 张卡），
        用它反推我方卡组会把对手卡组当自己的（实测：K9vs 局被误纠成 HEROs，
        用户原话「我点对手卡组的英雄，我自己的卡组也变成了英雄」）。
        我方卡组的识别只走 ① 大厅节流识别 / 编辑界面观察者。
        """
        import json as _json
        name = dir_name_for(TS0)
        d = os.path.join(self.records, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'duel.json'), 'w', encoding='utf-8') as f:
            _json.dump({'state': 'final',
                        'meta': {'result': 'Win', 'seat': 1},
                        'summary': {'revealed': {'p0': self.DECK_BLUE['ids']}}}, f)
        self.st.rank = None                                    # 探测不启动
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.st.deck = None
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)   # 收局
        self.c.observe(TS1, False, now=1020.0)                 # 确认窗口内
        self.assertEqual(self.st.my_deck_repairs, [],
                         'revealed 反推已撤：不许修我方卡组')
        import core.my_deck as my_deck
        self.assertEqual(my_deck.current_name(path=self.state_path), 'Blue-Eyes')


class TestEmEarlyFlush(unittest.TestCase):
    """★ 2026-09-23 实测事故：文案齐、实例地址有值，但图标对象**从未实例化**
    （观测器全程 wb=None）—— 采集器按「等补读」干等满 90s / 16 次整读，
    结果晚 1 分钟才入库。修法 = 补读计数达 `EM_WB_DRY_TRIES` 仍 None 就落盘。

    ★ 2026-10-01 起补读**每拍一次**（cheap-first：找到文案后不再让 10s 整读
    插队，实报 22:51 局文案到手 41s 后结果才落盘），阈值 2 -> 4（耐心持平）。
    """

    def setUp(self):
        import tempfile, shutil
        self.records = tempfile.mkdtemp(prefix='emflush-')
        self.addCleanup(shutil.rmtree, self.records, True)
        self.st = StubHooks()
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c = DuelCollector(records_dir=self.records, hooks=self.st)

    def test_early_flush_after_two_dry_winbit_reads(self):
        """补读 2 次仍无胜负位 → 当场落盘，不等满窗口。"""
        # 整读返回：文案齐 + 实例地址，但 winbits 恒 None
        self.st.em = ({'player0Name': '我', 'player1Name': '对手',
                       'messageText': '对手已投降'},
                      {'instance': '0xABC', 'winbits': None})
        self.st.winbits = None                     # 补读永远拿不到
        self.st.state_frames = []
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)        # 开局建目录
        lines = c.observe(TS1, True, 3, finished=1, now=1010.0)
        # 收局那一拍：capture_end_message 开挂起并第一次整读 → 有文案无 wb
        # （full_read 命中一次；winbits 桩恒 None）
        self.assertIsNotNone(c._em_pending, '还没到提前落盘的条件时应挂着')
        # 节奏（cheap-first，每拍补读）：1010 收局首读 -> 1012#1 -> 1013#2 ->
        # 1014#3（都拿不到，还差一次）-> 1015#4 -> **当场落盘**
        out = c.observe(TS1, False, now=1012.0)
        self.assertIsNotNone(c._em_pending, '补读 1 次还没到上限')
        out += c.observe(TS1, False, now=1013.0)
        self.assertIsNotNone(c._em_pending, '补读 2 次还没到上限')
        out += c.observe(TS1, False, now=1014.0)
        self.assertIsNotNone(c._em_pending, '补读 3 次还没到上限')
        out += c.observe(TS1, False, now=1015.0)
        self.assertIsNone(c._em_pending, '补读 4 次后该提前收口')
        self.assertTrue(any('仍无胜负位' in x for x in out), out)
        # ★ 找到文案后不该再整读（一次 10s 扫描预算，白烧就慢）
        full_calls = getattr(self.st, 'em_calls', 0)
        out += c.observe(TS1, False, now=1020.0)
        self.assertEqual(getattr(self.st, 'em_calls', 0), full_calls,
                         '落盘后不再整读')

    def test_real_late_assign_still_waited(self):
        """真晚赋值的局（第一次补读就有 wb）不受影响：照样「胜负位就绪」落盘。"""
        self.st.em = ({'player0Name': '我', 'player1Name': '对手',
                       'messageText': '已投降'},
                      {'instance': '0xABC', 'winbits': None})
        self.st.winbits_seq = [(0, 1)]             # 第一次补读就拿到
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        lines = c.observe(TS1, False, now=1012.0)   # 1012 补读 -> 命中
        lines += c.observe(TS1, False, now=1013.0)
        self.assertTrue(any('end_message' in x and 'lose' in x for x in lines),
                        lines)
        self.assertIsNone(c._em_pending)


class TestEmResultArbitration(unittest.TestCase):
    """🔴 2026-10-03 事故回归（悬浮窗标记反了）：投降局 winbit 残留成上一局的
    [1,0]，旧口径 winbit 优先 → 整局记反。落盘 mem_result 改走投票仲裁。"""

    def setUp(self):
        import tempfile, shutil
        self.records = tempfile.mkdtemp(prefix='emarb-')
        self.addCleanup(shutil.rmtree, self.records, True)
        self.st = StubHooks()
        self.st.size = 5
        self.st.state = mk_state(5)
        #: 本局：我投降（文案），结算码 2=负；winbit 却是上一局残留的 [1,0]
        self.st.em = ({'player0Name': '我', 'player1Name': '对手',
                       'messageText': '已投降'},
                      {'instance': '0xABC', 'winbits': [1, 0]})
        self.st.duel_result = {'result': 2, 'result_name': 'Lose'}
        self.st.winbits = (1, 0)              # 补读也是残留值
        self.c = DuelCollector(records_dir=self.records, hooks=self.st)

    def test_text_and_code_overrule_stale_winbit(self):
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        for t in (1012.0, 1014.0, 1016.0, 1018.0, 1020.0):
            if c._em_pending is None:
                break
            c.observe(TS1, False, now=t)
        self.assertTrue(self.st.em_appended, '该落盘一行 EM')
        rec = self.st.em_appended[0]
        self.assertEqual(rec['mem_result'], 'lose')          # 不再被残留 winbit 骗
        self.assertEqual(rec['mem_result_src'], 'duel_code')
        self.assertIn('冲突', rec.get('result_note') or '')


class TestEmCodeOnlyFlush(unittest.TestCase):
    """🔴 2026-10-01 事故回归：整读全程没文案，但**结算码已在手** ——
    旧代码把 code=1/2 直接扔掉（`抓不到`），第一局的结果凭空丢
    （10-01 22:40 局）。现在合成一条无文案记录，胜负走 duel_code 兜底。
    """

    def setUp(self):
        import tempfile, shutil
        self.records = tempfile.mkdtemp(prefix='emcode-')
        self.addCleanup(shutil.rmtree, self.records, True)
        self.st = StubHooks()
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)                  # 整读永远没有文案
        self.st.duel_result = {'result': 1, 'result_name': 'Win'}
        self.c = DuelCollector(records_dir=self.records, hooks=self.st)

    def test_flush_with_duel_code_when_no_texts(self):
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        # 整读节奏：1010/1012/1018/1033/1063 五次用完 -> 名额一空当场落盘
        lines = []
        for t in (1012.0, 1018.0, 1033.0, 1063.0):
            self.assertIsNotNone(c._em_pending, t)
            lines += c.observe(TS1, False, now=t)
        self.assertIsNone(c._em_pending, '整读名额用完当场（1063 拍）落盘')
        self.assertEqual(len(self.st.em_appended), 1)
        rec = self.st.em_appended[0]
        self.assertEqual(rec['duel_code'], 1)
        self.assertEqual(rec['mem_result'], 'win')
        self.assertEqual(rec['mem_result_src'], 'duel_code')
        self.assertIsNone(rec['winbits'])
        self.assertEqual(rec['texts'], {})         # 无文案条目
        self.assertIsNotNone(rec.get('record_id'))
        self.assertTrue(any('无文案' in x for x in lines), lines)
        self.assertEqual(len(self.st.imports), 2)  # 落盘后补跑一次 import

    def test_code_backfills_result_early(self):
        """★ 第五刀：结算码在手 + 整读两次扑空（~20s）→ 结果提前直写库里，
        不等整读名额烧完（实报 23:37 局 83s 才落库，用户看着就是「没结果」）。"""
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertEqual(self.st.duel_code_backfills, [])
        c.observe(TS1, False, now=1012.0)          # 第二次整读扑空 -> 当拍触发
        self.assertEqual(len(self.st.duel_code_backfills), 1)
        self.assertEqual(self.st.duel_code_backfills[0][1], 1)   # code=1
        for t in (1018.0, 1033.0, 1063.0, 1065.0):
            c.observe(TS1, False, now=t)
        self.assertEqual(len(self.st.duel_code_backfills), 1, '只直写一次')
        self.assertEqual(len(self.st.em_appended), 1)            # 收口照常落 EM

    def test_no_code_still_reports_nothing(self):
        self.st.duel_result = None                 # 结算码也没抓到
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        for t in (1012.0, 1018.0, 1033.0, 1063.0, 1093.0):
            c.observe(TS1, False, now=t)
        c.observe(TS1, False, now=1095.0)
        self.assertEqual(self.st.em_appended, [])  # 没判据 = 维持旧行为不落盘


class TestEmPreempt(unittest.TestCase):
    """🔴 2026-10-02 实测事故回归：上一局 EM 窗口没关 + 新局秒投收口 →
    新局的 EM 抓取被旧窗口占坑**整局丢掉**，且旧窗口晚到的整读会把
    **新局的结算画面**读回来、盖着旧局的 record_id 落盘
    （00:10:32 那条：新局文案「对手已投降」配旧局结算码 Lose）。

    修法 = 新局优先：收口时槽位被别的局占着，旧窗口当场收口（凭码落盘），
    槽位让给本局。
    """

    def setUp(self):
        import tempfile, shutil
        self.records = tempfile.mkdtemp(prefix='empreempt-')
        self.addCleanup(shutil.rmtree, self.records, True)
        self.st = StubHooks()
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.duel_result = {'result': 2, 'result_name': 'Lose'}  # 旧局：负
        self.st.em = (None, None)                  # 旧局整读一直没文案
        self.c = DuelCollector(records_dir=self.records, hooks=self.st)

    def test_new_game_close_preempts_old_window(self):
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)                 # 旧局开始
        c.observe(TS1, True, 3, finished=1, now=1010.0)     # 旧局收口 -> 挂起 A
        d1 = os.path.basename(c._em_pending.get('dir'))
        c.observe(TS1, False, now=1010.5)                   # 结算画面（不在对局）
        c.observe(TS1, True, 3, now=1011.0)                 # 新局开始（A 继续等）
        self.assertIsNotNone(c._em_pending)
        c.observe(TS1, True, 3, now=1013.0)                 # 新局建目录（真机有 ~15 拍）
        self.assertIsNotNone(c.open_rec, '新局目录已建')
        # 新局 30s 秒投收口：槽位被 A 占着 -> 当场让位
        self.st.em = ({'player0Name': '我', 'player1Name': '对手',
                       'messageText': '对手已投降'},
                      {'instance': '0xABC', 'winbits': None})
        self.st.winbits = (1, 0)                            # 新局胜负位可读
        c.observe(TS1, True, 3, finished=1, now=1015.0)
        # ① 旧局当场凭码落盘（不丢结果、不蹭新局文案）
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['record_id'], d1)
        self.assertEqual(self.st.em_appended[0]['texts'], {})
        self.assertEqual(self.st.em_appended[0]['duel_code'], 2)
        # ② 新局拿到自己的挂起，照常抓
        self.assertIsNotNone(c._em_pending, '新局该有自己的挂起')
        lines = c.observe(TS1, False, now=1016.0)           # 每拍补读 -> 命中
        self.assertIsNone(c._em_pending)
        self.assertEqual(len(self.st.em_appended), 2)
        self.assertEqual(self.st.em_appended[1]['mem_result'], 'win')
        self.assertEqual(self.st.em_appended[1]['texts']['messageText'],
                         '对手已投降')
        self.assertNotEqual(self.st.em_appended[1]['record_id'], d1)

    def test_same_dir_recapture_still_advances(self):
        """同局的重复 capture（finished 路径 + 收口路径各一次）不许被当成抢占。"""
        c = self.c
        c.observe(TS0, True, 3, now=1000.0)
        c.observe(TS1, True, 3, finished=1, now=1010.0)
        d1 = c._em_pending.get('dir')
        c.capture_end_message(now=1011.0, record_dir=d1)    # 同局再来一拍
        self.assertIs(c._em_pending.get('dir'), d1)
        self.assertEqual(self.st.em_appended, [])           # 没有提前收口


class TestRankProbe(Base):
    """收局段位探测（§5.7 两拍法）：`exp` 变 = 本局结算已写进 ClientWork。

    口径：`pvpFinished` 翻 1 与「ClientWork 被写」谁先谁后没有秒级证据，
    所以当场读一次当基线、之后每拍重读，**exp 一变**就落盘。
    """

    BASE = {'rank': 5, 'rate': 2, 'name': '\u793a\u4f8b\u73a9\u5bb6',
            'pcode': 123456789,
            'exp': 28900, 'need_exp': 50500}
    UP = {'rank': 5, 'rate': 1, 'name': '\u793a\u4f8b\u73a9\u5bb6',
          'exp': 29000, 'need_exp': 50500}

    def _run_duel_with_dir(self, now0=1000.0):
        """跑到收局（目录已建）—— 返回 record 目录名。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=now0)
        self.c.observe(TS1, True, 3, finished=1, now=now0 + 10)
        return dir_name_for(TS0)

    def _rank_file(self, name):
        return os.path.join(self.records, name, 'my_rank_after.json')

    def _read_rank_file(self, name):
        with open(self._rank_file(name), encoding='utf-8') as f:
            return json.load(f)

    def test_account_stamped_on_rank_base(self):
        """★ 多账号（2026-10-03）：收局段位探测读到 profile 就**顺手盖账号章**
        （name + pcode，storage 侧只补空）—— 同一条链，不再多读一次内存。"""
        self.st.rank = dict(self.BASE)          # 收局基线一拍就读到
        name = self._run_duel_with_dir()
        self.assertEqual(self.st.account_stamps,
                         [(name, '示例玩家', 123456789)])

    def test_settles_on_exp_change(self):
        """基线 -> 没变不动 -> exp 一变立刻落盘（rate 2 -> 1 = 升段）。"""
        self.st.rank_seq = [self.BASE, self.BASE, self.UP]
        name = self._run_duel_with_dir()
        self.assertIsNotNone(self.c._rank_probe)              # 已启动、未落盘
        self.assertFalse(os.path.exists(self._rank_file(name)))
        self.c.observe(TS1, False, now=1012.0)                # exp 没变
        self.assertIsNotNone(self.c._rank_probe)
        lines = self.c.observe(TS1, False, now=1014.0)        # exp 变了
        self.assertIsNone(self.c._rank_probe)
        data = self._read_rank_file(name)
        self.assertEqual(data['resolved_by'], 'clientwork-change')
        self.assertEqual(data['base']['rate'], 2)
        self.assertEqual(data['after']['rate'], 1)            # 局后值 = 升段后
        self.assertEqual(data['reads'], 3)
        self.assertTrue(any('\u6bb5\u4f4d' in x for x in lines))

    def test_polls_even_when_tick_cannot_read_duel(self):
        """`in_duel=None`（读失败）那拍**也要**推进探测 —— 否则永远落不了盘。"""
        self.st.rank_seq = [self.BASE, self.UP]
        name = self._run_duel_with_dir()
        self.c.observe(TS1, None, now=1012.0)
        self.assertIsNone(self.c._rank_probe)
        self.assertEqual(self._read_rank_file(name)['resolved_by'],
                         'clientwork-change')

    def test_settles_on_rate_change_without_exp(self):
        """★ 输局不加 exp（用户 2026-09-18 口供：自己投降的输局）——
        但掉段时 rate 会变：exp 没动、rate 变了也要**立即落盘**，
        不然掉段局只能 timeout、after 永远是局前旧值。"""
        drop = dict(self.BASE, rate=3)                     # exp 不变、掉一小段
        self.st.rank_seq = [self.BASE, self.BASE, drop]
        name = self._run_duel_with_dir()
        self.assertIsNotNone(self.c._rank_probe)
        self.c.observe(TS1, False, now=1012.0)             # 还没变
        lines = self.c.observe(TS1, False, now=1014.0)     # rate 变了
        self.assertIsNone(self.c._rank_probe)
        data = self._read_rank_file(name)
        self.assertEqual(data['resolved_by'], 'clientwork-change')
        self.assertEqual(data['after']['rate'], 3)
        self.assertTrue(any('段位' in x for x in lines))

    def test_times_out_when_exp_never_changes(self):
        """exp 一直不动（比如结算没来）→ 超时落盘，记 timeout、settled_at 空。"""
        self.st.rank = self.BASE
        name = self._run_duel_with_dir()
        self.c.observe(TS1, False, now=1105.0)                # > base_at + 90
        data = self._read_rank_file(name)
        self.assertEqual(data['resolved_by'], 'timeout')
        self.assertIsNone(data['settled_at'])
        self.assertEqual(data['after'], self.BASE)

    def test_no_probe_when_rank_unreadable(self):
        """读不到段位 → 不建探测、不落盘、只记日志（**并挂起重试**，见 Bug 1）。"""
        self.st.rank = None
        name = self._run_duel_with_dir()
        self.assertIsNone(self.c._rank_probe)
        self.assertIsNotNone(self.c._rank_base_pending)      # 挂起了，没放弃
        self.assertFalse(os.path.exists(self._rank_file(name)))
        self.assertTrue(any('\u6bb5\u4f4d\u8bfb\u4e0d\u5230' in x
                            for x in self.lines))

    def test_no_probe_without_record_dir(self):
        """没建目录（控制器没找到）→ 没处落盘，不启动探测。"""
        self.st.scan_seq = [(None, None)]
        self.st.rank = self.BASE
        self.c.observe(TS0, True, 3, now=1000.0)
        self.assertIsNone(self.c.open_rec.dir_path)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertIsNone(self.c._rank_probe)

    def test_not_triggered_on_instance_gone(self):
        """只有 `pvpFinished` 收局才读段位；实例消失收口不读（结算还没来）。"""
        self.st.size = 5
        self.st.rank = self.BASE
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, False, now=1010.0)                # instance-gone
        self.assertIsNone(self.c._rank_probe)

    def test_base_hooks_default_is_no_op(self):
        """`Hooks.read_my_rank()` 基类默认 None —— 老单测/无内存环境行为不变。"""
        self.assertIsNone(Hooks().read_my_rank())

    # ---- ★ 落盘后自动补写进库（2026-09-17：以前要手跑 backfill --apply）----
    def test_settled_probe_overwrites_the_row(self):
        """★ 真值到了就压过推断值：结算确认（settled）→ 本局行按探测真值覆盖。

        为什么必须有：本局行收局 2 秒后装的是**推断值**（`_infer_rank_state`），
        而推断规则里那条「跨大段晋级」还没经过实测确认 —— 真值一到就得压过它。
        """
        self.st.rank_seq = [self.BASE, self.UP]
        name = self._run_duel_with_dir()
        lines = self.c.observe(TS1, False, now=1014.0)        # exp 变了 -> 落盘
        self.assertEqual(DB_CALLS['repair'], [(name, 5, 1)])
        self.assertTrue(any('段位复核' in x for x in lines), lines)

    def test_timeout_still_repairs_with_profile_snapshot(self):
        """★ timeout 局也要复核（2026-09-22 二次修正，用户实测两局错显示 IV/V）。

        原守卫「timeout 不许覆盖」的顾虑是「cur 可能还是局前值」—— 但 `cur`
        是落盘那一拍的 **profile 快照（游戏段位页真值）**；rank_state 一旦脱节，
        推断值步步错且 timeout 局永不复核 = 错值永久留在行上（实测：
        真值 6/2 被推断的 6/5、6/4 盖了两局）。新守卫：cur 读得到就覆盖。
        """
        self.st.rank = self.BASE                               # exp 一直不动
        name = self._run_duel_with_dir()
        self.c.observe(TS1, False, now=1105.0)                # 超时落盘
        self.assertEqual(self._read_rank_file(name)['resolved_by'], 'timeout')
        #: cur = BASE = 6/2 —— 以 profile 快照复核（哪怕值没变）
        self.assertEqual(DB_CALLS['repair'], [(name, self.BASE['rank'],
                                               self.BASE['rate'])])

    def test_promotion_already_written_at_baseline_still_settles(self):
        """★ 升段局结算写得快：基线那拍已经是**局后值**，两拍等值不许永远 timeout。

        实测（2026-09-19 00:21 升段局）：推断把行写成 5-1（它漏了前一把的手填胜局），
        探针基线读到的已是 6-5（钻石5），6-5→6-5 判成 timeout -> 只补空 ->
        错的 5-1 留在行上，界面不显示升段箭头。
        修 = cur 的段位与「局前段位」（duel_start 读的）不同也算 settled（真值压推断）。
        """
        from core import rank_state
        rank_state.save({'rank': 5, 'rate': 1, 'win_streak': 3,
                         'lose_streak': 0})                    # 镜像实况：推断链漏了手填胜局
        post = dict(self.BASE, rank=6, rate=5, exp=31750)      # 基线已是钻石5
        self.st.duel_players = {'my_index': 0,
                                'my_rank': {'rank': 5, 'rate': 1}}   # 局前：白金1
        self.st.rank_seq = [post, post]
        name = self._run_duel_with_dir()
        lines = self.c.observe(TS1, False, now=1012.0)
        self.assertEqual(self._read_rank_file(name)['resolved_by'],
                         'clientwork-change')
        self.assertEqual(DB_CALLS['repair'], [(name, 6, 5)])   # 真值覆盖推断
        self.assertTrue(any('段位复核' in x for x in lines), lines)
        from core import rank_state
        self.assertEqual((rank_state.load()['rank'], rank_state.load()['rate']),
                         (6, 5))                               # 状态也校准到钻石5

    def test_no_change_vs_before_keeps_timeout_path(self):
        """段位没动（升不了也掉不了）的局：与局前相同 -> 不误判成 settled。"""
        self.st.duel_players = {'my_index': 0,
                                'my_rank': {'rank': 5, 'rate': 2}}   # 局前 = 局后（段位没动）
        self.st.rank = self.BASE                               # exp/段位全程没动
        name = self._run_duel_with_dir()
        self.c.observe(TS1, False, now=1105.0)
        self.assertEqual(self._read_rank_file(name)['resolved_by'], 'timeout')
        #: resolved_by 仍是 timeout（不误判 settled），但 **profile 快照照常复核**
        self.assertEqual(DB_CALLS['repair'], [(name, self.BASE['rank'],
                                               self.BASE['rate'])])

    def test_backfills_rank_after_write(self):
        """探测落盘后**顺手补进库**，并把结果写进日志。"""
        self.st.rank_seq = [self.BASE, self.UP]
        self.st.backfill_result = '段位入库：rec_x（局后 5-1，局前 5-2）'
        name = self._run_duel_with_dir()
        lines = self.c.observe(TS1, False, now=1014.0)        # exp 变了 -> 落盘
        self.assertEqual(self.st.rank_backfills, [name])      # 用目录名去补
        self.assertTrue(any('段位入库' in x for x in lines))

    def test_backfill_not_called_before_write(self):
        """还没落盘就不该补（值还不准）。"""
        self.st.rank_seq = [self.BASE, self.BASE]
        self._run_duel_with_dir()
        self.c.observe(TS1, False, now=1012.0)                # exp 没变
        self.assertEqual(self.st.rank_backfills, [])

    def test_backfill_none_is_silent(self):
        """没得补（行还没入库 / 已有值）-> 静默，不写误导日志。"""
        self.st.rank_seq = [self.BASE, self.UP]
        self.st.backfill_result = None
        name = self._run_duel_with_dir()
        lines = self.c.observe(TS1, False, now=1014.0)
        self.assertEqual(self.st.rank_backfills, [name])      # 调了
        self.assertTrue(any('段位：' in x for x in lines))     # 但只记探测那行
        self.assertFalse(any('段位入库' in x for x in lines))

    def test_backfill_failure_does_not_block(self):
        """★ 补写抛异常**不许**影响采集 —— 记一行日志、探测结果照落盘。"""
        self.st.rank_seq = [self.BASE, self.UP]
        self.st.backfill_err = RuntimeError('db is locked')
        name = self._run_duel_with_dir()
        lines = self.c.observe(TS1, False, now=1014.0)
        self.assertTrue(os.path.exists(self._rank_file(name)))  # 落盘没受影响
        self.assertEqual(self._read_rank_file(name)['resolved_by'],
                         'clientwork-change')
        self.assertTrue(any('段位补写失败' in x for x in self.lines))

    def test_base_hooks_backfill_is_no_op(self):
        """基类默认 None —— 没接线的环境（老单测）行为不变。"""
        self.assertIsNone(Hooks().backfill_rank('rec_x'))


class _frozen_time(object):
    """把采集器的 `time.time` 临时钉成固定值（守卫按墙钟判窗口）。"""

    def __init__(self, value):
        self._value = value

    def __enter__(self):
        import tools.duel_collector as m
        self._m = m
        self._orig = m.time.time
        m.time.time = lambda: self._value
        return self

    def __exit__(self, *exc):
        self._m.time.time = self._orig
        return False


class TestEmWinbitRetry(Base):
    """收局 EM：**文案就绪但胜负位未就绪** -> 挂起 + 每拍补读。

    实机背景（2026-09-15 深夜）：`pvpFinished` 那一拍 WinIcon 的 GameObject 还没
    赋值 → `winbits=None` → 那两局的 `result` 空着（UI 显示 `—`）。
    修法 = 补读后再 append，**且只 append 一行**（配对规则要一条 EM 一行）。
    """

    TEXTS = {'messageText': '\u5bf9\u624b\u5df2\u6295\u964d',
             'player0Name': '\u6211', 'player1Name': '\u5bf9\u624b'}

    def _finish(self, now=1000.0, extra=None):
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (dict(self.TEXTS),
                      extra if extra is not None
                      else {'instance': '0x9', 'pid': 5})
        self.c.observe(TS0, True, 3, now=now)
        return self.c.observe(TS1, True, 3, finished=1, now=now + 10)

    def test_pending_until_winbits_ready(self):
        self._finish()
        self.assertEqual(self.st.em_appended, [])              # 还没落盘
        self.assertIsNotNone(self.c._em_pending)
        self.assertFalse(any(self.TEXTS['messageText'] in x
                             for x in self.st.em_appended))
        # 挂起期间**不算「见过」** —— 否则补读会被自己的去重挡掉
        from core.memread.end_message import texts_key
        self.assertNotIn(texts_key(self.TEXTS), self.c._em_seen)

    def test_appends_once_after_retry(self):
        self._finish()
        self.st.winbits = (1, 0)
        # 1011 还没到下一次**整读**的退避点（收局当场 +2s）→ 走「按地址补读」那条
        lines = self.c.observe(TS1, False, now=1011.0)
        self.assertIsNone(self.c._em_pending)
        self.assertEqual(len(self.st.em_appended), 1)
        rec = self.st.em_appended[0]
        self.assertEqual(rec['winbits'], [1, 0])
        self.assertEqual(rec['mem_result'], 'win')
        self.assertTrue(any('\u80dc\u8d1f\u4f4d\u5c31\u7eea' in x for x in lines))
        self.assertEqual(self.st.rank_calls if hasattr(self.st, 'rank_calls')
                         else 0, 0)                            # 不碰段位那条线

    def test_timeout_appends_with_text_fallback(self):
        """超时落盘：winbits/结算码都没有，但**文案是有效判据**（2026-10-03
        投票仲裁起）——「对手已投降」判胜（旧口径这里 mem_result 是 None）。"""
        self._finish()
        self.c.observe(TS1, False, now=1100.0)                  # 超出 60s 窗口
        self.assertEqual(len(self.st.em_appended), 1)
        rec = self.st.em_appended[0]
        self.assertIsNone(rec['winbits'])
        self.assertEqual(rec['mem_result'], 'win')
        self.assertEqual(rec['mem_result_src'], 'text')
        self.assertEqual(self.st.em_appended[0]['result_note'],
                         self.TEXTS['messageText'])             # 文案仍记着

    def test_ready_immediately_still_one_row(self):
        lines = self._finish(extra={'winbits': [0, 1], 'instance': '0x9',
                                    'pid': 5})
        self.assertIsNone(self.c._em_pending)
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'lose')

    def test_recapture_while_pending_no_double_row(self):
        """挂起期间再抓一次：只是**推进挂起**，不会多写一行。"""
        self._finish()
        self.assertIsNone(self.c.capture_end_message(now=1011.0))
        self.assertEqual(self.st.em_appended, [])
        self.assertIsNotNone(self.c._em_pending)
        self.st.winbits = (0, 1)
        self.c.observe(TS1, False, now=1013.0)                  # 每拍补读 -> 命中
        self.assertEqual(len(self.st.em_appended), 1)           # 全程只有一行
        self.c.observe(TS1, False, now=1014.0)
        self.assertEqual(len(self.st.em_appended), 1)           # 不会再写第二行
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'lose')

    def test_late_em_triggers_second_import(self):
        """EM 延后落盘 -> **补跑一次 import**（否则晚到的 winbits 进不了库，
        用户看到的还是「结果 —」）。"""
        self._finish()                                   # 收局：import 跑了一次
        self.assertEqual(len(self.st.imports), 1)
        self.st.winbits = (1, 0)
        self.c.observe(TS1, False, now=1011.0)           # EM 落盘
        self.assertEqual(len(self.st.imports), 2)        # 补跑
        self.assertEqual(self.st.imports[-1], dir_name_for(TS0))

    def test_immediate_em_does_not_reimport(self):
        """收局当场就完整落盘 -> 不补跑（行为不变，只 import 一次）。"""
        self._finish(extra={'winbits': [1, 0], 'instance': '0x9', 'pid': 5})
        self.assertEqual(len(self.st.imports), 1)

    def test_waits_for_texts_then_captures(self):
        """① **收局那一刻连文案都没有** —— 2026-09-15 的 22:59 / 23:03 两局就是这样，
        当时直接放弃、一行 EM 没留下。现在窗口内整读补上。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)                               # 结算画面还没画
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertEqual(self.st.em_appended, [])
        self.assertIsNotNone(self.c._em_pending)
        self.st.em = (dict(self.TEXTS),
                      {'winbits': [1, 0], 'instance': '0x9', 'pid': 5})
        self.c.observe(TS1, False, now=1012.0)                  # 退避到点 -> 整读
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'win')

    def test_gives_up_and_logs_when_no_texts(self):
        """整窗口都没等到文案 -> 丢弃 + 记日志（不留半条脏数据）。

        ⚠️ 2026-09-20：窗口 60 → 90s，时间点同步外推 —— 旧值 1090 现在
        **还在窗口内**（整读也还有名额），那时不该放弃。
        """
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.c.observe(TS1, False, now=1120.0)                  # 越过 90s 窗口
        self.assertEqual(self.st.em_appended, [])
        self.assertIsNone(self.c._em_pending)
        self.assertTrue(any('\u6293\u4e0d\u5230' in x for x in self.lines))

    def test_window_not_cut_by_new_duel_start(self):
        """★ 新局开始**不切**上一局没做完的 EM 窗口（2026-09-20 用户实报两局）。

        投降局回大厅只要几秒、紧接着又开下一局 —— 旧代码在 `_start_record`
        里无条件 `_close_stale_em()`，把整读名额还没用完、deadline 也没到的窗口
        一并收口（那两局 result 因此为空）。

        ⚠️ 直接打 `_start_record`（= 生产里 `observe()` 收到开局事件时调的函数）；
        并把 `time.time` 打桩成用例的 now —— 守卫按墙钟判「窗口还在不在」，
        不打桩就会拿真实时间（2026-09-20）去比用例的 1022，必判超时。
        """
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)    # 收局 -> 开窗
        p = self.c._em_pending
        self.assertIsNotNone(p)
        self.assertGreater(p['full_left'], 0)                  # 整读还没做完
        # 12 秒后（窗口内）新局开始：窗口必须留下
        ev = SimpleNamespace(kind=1, data={})
        with _frozen_time(1022.0):
            lines = self.c._start_record(TS2, ev, 3, now=1022.0)
        self.assertIsNotNone(self.c._em_pending, '新局不该切掉上一局的 EM 窗口')
        self.assertTrue(any('继续等' in x for x in lines), lines)
        # 窗口内文案就绪 -> 照常抓到并落盘
        self.st.em = (dict(self.TEXTS),
                      {'winbits': [1, 0], 'instance': '0x9', 'pid': 5})
        self.c.observe(TS2, False, now=1046.0)
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'win')

    def test_window_cut_when_nothing_left_to_try(self):
        """反面：整读做完 + 没实例可补 -> 新局开始**照常收口**（别白占槽位）。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.c._em_pending['full_left'] = 0        # 整读名额用尽、也没文案
        ev = SimpleNamespace(kind=1, data={})
        with _frozen_time(1022.0):
            self.c._start_record(TS2, ev, 3, now=1022.0)
        self.assertIsNone(self.c._em_pending, '没什么可试了就该收口')

    def test_texts_ready_at_30s_retry_still_captured(self):
        """整读预算差一错误（2026-09-19 采证）：full_left 把首读也算了名额，
        5 次整读只做了 4 次，30s 退避那次永远轮不到 -> 没文案时 ~25s 就放弃
        （00:10 烙印局整窗没文案、结果只能手填，就是它）。修后按注释口径
        整读 5 次（0/2/8/23/53s），最后一次抓到晚就绪的文案。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (None, None)                               # 结算画面迟迟不画
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)    # 整读1 -> 退避2s
        self.c.observe(TS1, False, now=1012.0)                  # 整读2 -> 退避6s
        self.c.observe(TS1, False, now=1018.0)                  # 整读3 -> 退避15s
        self.c.observe(TS1, False, now=1033.0)                  # 整读4 -> 退避30s
        self.assertEqual(self.st.em_appended, [])
        self.assertIsNotNone(self.c._em_pending)
        self.st.em = (dict(self.TEXTS),
                      {'winbits': [1, 0], 'instance': '0x9', 'pid': 5})
        self.c.observe(TS1, False, now=1063.0)                  # 整读5：文案+胜负位齐了
        self.assertEqual(len(self.st.em_appended), 1)
        self.assertEqual(self.st.em_appended[0]['mem_result'], 'win')

    def test_no_instance_appends_immediately(self):
        """读不到实例（老 reader / 无内存环境）时行为不变：当场落盘。"""
        self._finish(extra={'pid': 5})
        self.assertIsNone(self.c._em_pending)
        self.assertEqual(len(self.st.em_appended), 1)


class TestImportJustClosed(unittest.TestCase):
    """`import_just_closed` + `self_heal`：**刚被收口的局要补进库**（Bug 3 的另一半）。

    背景（2026-09-18 实锤）：被 kill / 被「停止」干掉的采集器留下 open 目录，
    下次启动把它标成 final 就**再也没人 import** —— 记录永远躺在磁盘上
    （22:48 / 22:53 两局）。只补**这次刚收口的**，不碰历史目录。
    """

    def test_imports_each_name_and_logs(self):
        lines, got = [], []
        done = import_just_closed('/x', ['record_a', 'record_b'], lines.append,
                                  importer=got.append)
        self.assertEqual(done, ['record_a', 'record_b'])
        self.assertEqual(got, ['record_a', 'record_b'])
        self.assertTrue(any('刚收口的 2 局' in x for x in lines), lines)

    def test_one_failure_does_not_stop_the_rest(self):
        lines, got = [], []

        def imp(name):
            if name == 'bad':
                raise RuntimeError('db is locked')
            got.append(name)

        done = import_just_closed('/x', ['bad', 'good'], lines.append,
                                  importer=imp)
        self.assertEqual(done, ['good'])
        self.assertEqual(got, ['good'])
        self.assertTrue(any('补入库失败' in x for x in lines), lines)

    def test_noop_without_names(self):
        lines = []
        self.assertEqual(import_just_closed('/x', [], lines.append,
                                            importer=lambda n: None), [])
        self.assertEqual(lines, [])

    def test_self_heal_imports_what_it_just_finalized(self):
        """端到端：磁盘上留一个 open 目录 -> 启动自愈标 final + 补 import。"""
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        records = os.path.join(tmp, 'duel_records')
        os.makedirs(records)
        #: 造一个 open 目录 —— **用项目自己的写盘函数**（手写 manifest 会漏字段，
        #: `scan_record` 认不出 state，测试就变成假绿）
        import json as _json
        from core.memread import record_capture as rc
        name = 'record_20260913_120000'
        d = os.path.join(records, name)
        rc.write_record(d, mk_state(1), instance_hex='0x1', state='open',
                        first_seen_at=TS0, first_logbase_size=1)
        lines, got = [], []
        #: 游戏没在跑 -> 所有 open 都判 stale
        scans = self_heal(records, [], None, lines.append, importer=got.append)
        self.assertEqual(got, [name], '收口了就必须补 import')
        self.assertEqual(len(scans), 1)
        self.assertTrue(any('启动自愈' in x for x in lines), lines)
        self.assertTrue(any('补进库' in x for x in lines), lines)
        #: 标 final 真的落到了盘上（**用项目自己的读法**判断，别猜 manifest 键名）
        from core import duel_record
        self.assertEqual(duel_record.scan_record(d)['state'], 'final')


class TestCloseCurrentImports(Base):
    """★ Bug 3（实锤）：`instance-gone` / 掉线 / Ctrl-C 收口的局**也要入库**。

    2026-09-18 的 22:48 / 22:53 两局（用户重启采集器时正在打的那两局）走的就是
    这条收口 —— 以前只标 final 不 import，记录**永远躺在磁盘上**（库里没有这两行，
    最后靠手工补）。同一段代码也是「掉线超时」与「Ctrl-C 优雅退出」的收口。
    """

    def _open_duel(self, now=1000.0):
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=now)
        return dir_name_for(TS0)

    def test_instance_gone_close_imports(self):
        name = self._open_duel()
        self.st.imports.clear()
        lines = self.c.observe(TS1, False, now=1010.0)        # 实例消失 -> 收口
        self.assertEqual(self.st.imports, [name], '收口后必须跑一次 import')
        self.assertTrue(any('入库' in x for x in lines), lines)
        self.assertTrue(any('收口' in x for x in lines), lines)

    def test_disconnect_timeout_close_imports(self):
        name = self._open_duel()
        self.st.imports.clear()
        lines = self.c.observe(TS1, None, now=1010.0)         # 读失败
        self.assertEqual(self.st.imports, [])
        lines = self.c.observe(TS1, None, now=1010.0 + 121.0)  # 连续超时 -> 收口
        self.assertEqual(self.st.imports, [name])
        self.assertTrue(any('入库' in x for x in lines), lines)

    def test_ctrl_c_flush_imports(self):
        """`main` 的 finally 走同一条收口（reason='flushed'）。"""
        name = self._open_duel()
        self.st.imports.clear()
        lines = self.c._close_current(TS1, 'flushed', 1010.0)
        self.assertEqual(self.st.imports, [name])
        self.assertTrue(any('入库' in x for x in lines), lines)

    def test_import_failure_does_not_block(self):
        name = self._open_duel()
        self.st.imports.clear()
        self.st.import_err = RuntimeError('db is locked')
        lines = self.c._close_current(TS1, 'flushed', 1010.0)
        self.assertTrue(any('收口' in x for x in lines), lines)   # 收口照样完成
        self.assertTrue(any('import 失败' in x for x in self.lines), self.lines)

    def test_already_finalized_close_is_silent(self):
        """pvpFinished 收过局的再收口：不重复 import（幂等靠这条 + import 自身）。"""
        name = self._open_duel()
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)   # 正常收局
        self.st.imports.clear()
        self.assertEqual(self.c._close_current(TS1, 'flushed', 1020.0), [])
        self.assertEqual(self.st.imports, [])


class TestEmSlotAcrossDuels(Base):
    """★ 2026-09-18 实锤：EM 抓取只有**一个槽位** —— 上一局的窗口还没关就走完的局，
    `capture_end_message` 见槽被占就**整局不抓**（record_20260918_222756 那局一行
    EM 都没留下，winbits / 结算码一起丢，`result` 只能人工补）。

    修法：新局开始 = 上一局的结算画面**早就没了** → 当场收口旧挂起
    （该补的 import 照跑），槽位腾给本局。
    """

    TEXTS = {'messageText': '对手已投降', 'player0Name': '我', 'player1Name': '对手'}
    TEXTS2 = {'messageText': 'LP已削减至0', 'player0Name': '我', 'player1Name': '对手'}

    def _first_duel_pending(self):
        """第一局：文案就绪、胜负位没就绪 → 挂起（槽位被占住）。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.st.em = (dict(self.TEXTS), {'instance': '0x9', 'pid': 5})
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.assertIsNotNone(self.c._em_pending)
        self.assertEqual(self.st.em_appended, [])
        return dir_name_for(TS0)

    def test_new_duel_hands_the_slot_over(self):
        """★ 2026-10-02 语义修订：新局开始时旧窗口「值得等」就**继续跑**
        （09-20 规则，防秒投局结果丢）；占坑问题改在**本局收口时**解决
        （capture 抢占，见 `TestEmPreempt`）。本例旧窗口有文案+实例：
        cheap-first 每拍补读，`EM_WB_DRY_TRIES` 次后自行收口、槽位腾出。
        """
        old = self._first_duel_pending()
        self.st.imports.clear()
        #: 回大厅那一拍（tracker 要靠 `in_duel=False` 才能认下一局的开局；
        #: 实机 22:28:03 那拍就是「先大厅、再新局」）
        self.c.observe(TS1, False, now=1020.0)
        self.assertIsNotNone(self.c._em_pending)               # 还挂着
        self.c.observe(TS2, True, 3, now=1022.0)                # 新局开始
        self.assertIsNotNone(self.c._em_pending, '值得等的旧窗口继续跑')
        self.c.observe(TS2, True, 3, now=1024.0)                # 补读 #3
        self.c.observe(TS2, True, 3, now=1026.0)                # 补读 #4 -> 收口
        self.assertIsNone(self.c._em_pending, '旧窗口自行收口，槽位腾出来')
        self.assertEqual(len(self.st.em_appended), 1)           # 旧 EM 仍然落盘
        self.assertEqual(self.st.em_appended[0]['record_id'], old)
        self.assertEqual(self.st.imports, [old])                # 并且补跑 import

    def test_second_duel_gets_its_own_window(self):
        """收口之后，下一局收局时**还能**开自己的 EM 窗口（这才是修的意义）。"""
        self._first_duel_pending()
        self.c.observe(TS1, False, now=1020.0)                 # 大厅那一拍
        self.c.observe(TS2, True, 3, now=1022.0)               # 新局（旧槽收口）
        self.st.em = (dict(self.TEXTS2),
                      {'winbits': [1, 0], 'instance': '0x9', 'pid': 5})
        self.c.observe(TS2, True, 3, finished=1, now=1030.0)
        self.assertEqual(len(self.st.em_appended), 2)          # 旧的 + 本局的
        self.assertEqual(self.st.em_appended[1]['mem_result'], 'win')
        self.assertEqual(self.st.em_appended[1]['record_id'],
                         dir_name_for(TS2))

    def test_no_pending_is_a_no_op(self):
        self.assertEqual(self.c._close_stale_em(), [])


class TestRankBaseRetry(Base):
    """★ 任务书 Bug 1（P1）：收局那拍读不到段位 → **挂起重试**，不再整局放弃。

    实机（2026-09-18 晚）：21:00 / 22:27 / 22:28 三局，收局那一拍 `read_my_rank`
    读空（对手投降 → 场景切换），探测直接不启动 → `matches.my_rank_*` 整局 NULL；
    事后回主界面又读得到（数据一直都在）。修法 = 挂 60s 窗口、每拍重试一次
    （便宜路径，不扫描），读到就照常走两拍法。
    """

    BASE = {'rank': 5, 'rate': 2, 'exp': 28900}
    UP = {'rank': 5, 'rate': 1, 'exp': 29000}

    def _run_duel_with_dir(self, now0=1000.0):
        """跑到收局（目录已建）—— 返回 record 目录名。"""
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=now0)
        self.c.observe(TS1, True, 3, finished=1, now=now0 + 10)
        return dir_name_for(TS0)

    def _rank_file(self, name):
        return os.path.join(self.records, name, 'my_rank_after.json')

    def test_suspends_instead_of_giving_up(self):
        self.st.rank = None
        name = self._run_duel_with_dir()
        self.assertIsNone(self.c._rank_probe)          # 还没启动
        p = self.c._rank_base_pending
        self.assertIsNotNone(p, '读不到基线要挂起，不是放弃')
        self.assertEqual(p['dir'], os.path.join(self.records, name))
        self.assertEqual(p['deadline'], 1070.0)        # 收局 1010 + 60s
        self.assertTrue(any('挂起' in x for x in self.lines), self.lines)

    def test_late_base_starts_the_two_tick_probe(self):
        """挂起期间读到了 → 照常两拍法（基线 + 局后），照常落盘。"""
        self.st.rank = None
        name = self._run_duel_with_dir()
        self.st.rank_seq = [self.BASE, self.UP]
        self.c.observe(TS1, False, now=1012.0)                # 挂起中读到基线
        self.assertIsNone(self.c._rank_base_pending)
        self.assertIsNotNone(self.c._rank_probe)
        #: 日志走 `self.lines`（`_log`），同 `_start_rank_probe` 的既有口径
        self.assertTrue(any('挂起' in x and '读到' in x for x in self.lines),
                        self.lines)
        self.assertEqual(self.c._rank_probe['reads'], 1)       # 同拍只开机、不判变化
        self.c.observe(TS1, False, now=1014.0)                # rate 变了 -> 落盘
        self.assertIsNone(self.c._rank_probe)
        with open(self._rank_file(name), encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['resolved_by'], 'clientwork-change')
        self.assertEqual(data['after']['rate'], 1)
        self.assertEqual(data['reads'], 2)

    def test_gives_up_after_the_window(self):
        """窗口过了还是读不到 → 放弃（日志可见、不落盘、不留挂起）。"""
        self.st.rank = None
        name = self._run_duel_with_dir()
        self.c.observe(TS1, False, now=1012.0)
        lines = self.c.observe(TS1, False, now=1071.0)        # > 1070
        self.assertIsNone(self.c._rank_base_pending)
        self.assertIsNone(self.c._rank_probe)
        self.assertFalse(os.path.exists(self._rank_file(name)))
        self.assertTrue(any('放弃' in x for x in lines), lines)
        self.assertTrue(any('段位探测放弃' in x for x in self.lines))

    def test_retry_does_not_scan_or_block_other_ticks(self):
        """重试是**便宜路径**（不发扫描），且不挡别的拍（对局中零扫描的铁律）。"""
        self.st.rank = None
        self._run_duel_with_dir()
        scans = self.st.scan_calls
        for i in range(5):
            self.c.observe(TS1, False, now=1012.0 + i)
        self.assertEqual(self.st.scan_calls, scans, '重试不许扫描')

    def test_window_default_is_60s(self):
        from tools.duel_collector import RANK_BASE_WAIT_SEC
        self.assertEqual(RANK_BASE_WAIT_SEC, 60.0)
        self.assertEqual(self.c.rank_base_wait, 60.0)


class TestRankInference(Base):
    """段位推断链（D1 十一 / 十二轮）：胜负实时 → 推断值**同帧**写进本局行。

    ⚠️ 2026-09-18 深夜挖出：这条链**从十二轮起一直空转** —— `_infer_rank_state`
    的守卫写的是 `st.get('streak')`，而 rank_state v2 的键是 `win_streak` /
    `lose_streak`，于是每局都在这儿早退（段位整局 NULL 时少了一层兜底）。
    另一条：**只有天梯局**能推 —— 巅峰/DC/活动局的结果会把 rank_state 带歪。
    """

    def _setup_state(self, **st):
        from core import rank_state
        base = {'rank': 5, 'rate': 2, 'win_streak': 0, 'lose_streak': 0}
        base.update(st)
        rank_state.save(base)

    def _write_duel_json(self, name, result='Win', mode=3):
        d = os.path.join(self.records, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'duel.json'), 'w', encoding='utf-8') as f:
            json.dump({'state': 'final',
                       'meta': {'result': result, 'game_mode': mode},
                       'summary': {'revealed': {}}}, f)

    def _close(self, mode=3, result='Win'):
        """打完一局并推进确认窗口；返回 (目录名, 该拍日志行)。"""
        name = dir_name_for(TS0)
        self._write_duel_json(name, result=result, mode=mode)
        self.st.rank = None          # 探测那条路不启动，专注看推断
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, mode, now=1000.0)
        self.c.observe(TS1, True, mode, finished=1, now=1010.0)
        return name, self.c.observe(TS1, False, now=1020.0)

    def test_ranked_win_writes_inferred_rank(self):
        """白金 2 已 3 连胜 + 这局赢 → 推断升到白金 1，同帧写进本局行。"""
        self._setup_state(rank=5, rate=2, win_streak=3)
        name, lines = self._close()
        self.assertEqual(DB_CALLS['infer'], [(name, (5, 2), (5, 1))])
        self.assertTrue(any('段位（推断）' in x for x in lines), lines)
        self.assertTrue(any('升段' in x for x in lines), lines)
        from core import rank_state
        self.assertEqual((rank_state.load()['rank'], rank_state.load()['rate']),
                         (5, 1))
        self.assertEqual(rank_state.load()['win_streak'], 0)   # 升段后从满额算

    def test_ranked_lose_does_not_promote(self):
        """v3 进度制（09-27）：有进度时输局**扣进度**（ws-1），不掉段、不攒连败。"""
        self._setup_state(rank=5, rate=2, win_streak=2)
        name, lines = self._close(result='Lose')
        self.assertEqual(DB_CALLS['infer'], [(name, (5, 2), (5, 2))])
        from core import rank_state
        st = rank_state.load()
        self.assertEqual(st['win_streak'], 1)    # 进度扣了
        self.assertEqual(st['lose_streak'], 0)   # 保护计数只在进度 0 时累计

    def test_event_duel_never_infers(self):
        """活动局（21）打完**不改天梯段位** —— 推了就把 rank_state 带歪。"""
        self._setup_state(rank=5, rate=2, win_streak=3)
        name, lines = self._close(mode=21)
        self.assertEqual(DB_CALLS['infer'], [])
        self.assertFalse(any('段位（推断）' in x for x in lines), lines)
        from core import rank_state
        self.assertEqual(rank_state.load()['win_streak'], 3)   # 状态没被动

    def test_peak_duel_never_infers(self):
        """巅峰决斗（19）同样不算天梯段位。"""
        self._setup_state(rank=5, rate=2, win_streak=3)
        self._close(mode=19)
        self.assertEqual(DB_CALLS['infer'], [])

    def test_unknown_mode_never_infers(self):
        """模式判不出 → 不猜（`core.modes.group(None)` 不是 ranked）。"""
        self._setup_state(rank=5, rate=2, win_streak=3)
        self._close(mode=None)
        self.assertEqual(DB_CALLS['infer'], [])

    def test_late_result_still_infers(self):
        """★ 结果**迟到**的局（instance-gone 收口 -> EM 挂起收口才进档案）也要推断。

        实测（2026-09-19 01:11 局，圣徒白森）：收口时 duel.json 已可读但 result
        还是 NULL，无条件的 one-shot 把唯一一次推断烧掉 → 该局没段位。
        结果是 20 秒后才由 EM 补跑写进档案的。
        """
        from core import rank_state
        rank_state.save({'rank': 6, 'rate': 5, 'win_streak': 3,
                         'lose_streak': 0})                    # 镜像实况：第 4 胜 = 升段局
        name = dir_name_for(TS0)
        d = os.path.join(self.records, name)
        self._write_duel_json(name, result=None, mode=3)       # 结果还没到
        self.st.rank = None
        self.st.size = 5
        self.st.state = mk_state(5)
        self.c.observe(TS0, True, 3, now=1000.0)
        self.c.observe(TS1, True, 3, finished=1, now=1010.0)
        self.c.observe(TS1, False, now=1020.0)                 # 第一拍：结果没到
        self.assertEqual(DB_CALLS['infer'], [],
                         '结果没到不许把推断机会烧掉')
        self._write_duel_json(name, result='Win', mode=3)      # EM 补跑后结果到了
        self.c.observe(TS1, False, now=1030.0)
        self.assertEqual(len(DB_CALLS['infer']), 1, '结果到了要推一次')
        self.assertEqual(DB_CALLS['infer'][0], (name, (6, 5), (6, 4)))
        self.assertEqual((rank_state.load()['rank'], rank_state.load()['rate']),
                         (6, 4))                               # 推断连状态一起推进（升段）

    def test_uncalibrated_state_is_not_used(self):
        """rank_state 里没有连胜计数（未校准）→ 不编。"""
        from core import rank_state
        rank_state.save({'rank': 5, 'rate': 2})        # 只有段位、没有 streak
        name, lines = self._close()
        self.assertEqual(DB_CALLS['infer'], [])
        self.assertFalse(any('段位（推断）' in x for x in lines), lines)

    def test_inference_runs_once_per_duel(self):
        """确认窗口每拍都在转，但推断**只许跑一次**（否则连胜计数会被多算）。"""
        self._setup_state(rank=5, rate=2, win_streak=0)
        name, _lines = self._close()
        for i in range(3):
            self.c.observe(TS1, False, now=1022.0 + i)
        self.assertEqual(len(DB_CALLS['infer']), 1)


class TestGameGone(Base):
    """09-26 事故刀②：游戏退出时跨进程挂起必须作废。

    实锤链：用户重启游戏后旧局 EM 挂起存活，每拍全量扫描分钟级，
    主循环被拖死 8 分钟（11:42-11:50），下一局 duel_start 晚 4 分钟。
    """

    def test_clears_all_pending(self):
        self.c._em_pending = {'deadline': 0, 'rec': None, 'tries': 1}
        self.c._rank_base_pending = {'dir': 'x', 'at': 0}
        self.c._rank_probe = {'dir': 'x', 'base': {}}
        self.c._deck_snapshot_pending = True
        self.c._deck_pending_at = 0.0
        lines = self.c.on_game_gone()
        self.assertIsNone(self.c._em_pending)
        self.assertIsNone(self.c._rank_base_pending)
        self.assertIsNone(self.c._rank_probe)
        self.assertFalse(self.c._deck_snapshot_pending)
        self.assertIsNone(self.c._deck_pending_at)
        self.assertTrue(any('作废旧局挂起' in x for x in lines))

    def test_noop_when_nothing_pending(self):
        self.assertEqual(self.c.on_game_gone(), [])


# =====================================================================
# 2026-10-08 事故回归：单实例检查被 PID 复用骗过 -> 整场零记录
# =====================================================================
from unittest import mock as _mock                      # noqa: E402
from tools import duel_collector as _dc                 # noqa: E402


class TestSingleInstanceGuard(unittest.TestCase):
    """★ pid 文件是**陈旧判据**，而且 Windows 会**复用 pid**。

    事故（2026-10-08）：10-06 的采集器 pid=15848 退出后 pid 文件留在磁盘；
    10-08 10:26 系统把 15848 分给了 svchost；10:54 新采集器用裸
    ``OpenProcess(SYNCHRONIZE)`` 探测成功 -> 误判「已有采集器在跑」
    -> ``return 2`` -> 当天没有采集器在跑，**用户两局全丢**。
    修法：``pid_alive`` 必须校验「进程镜像名 + 创建时间」。
    """

    def test_recycled_pid_is_not_collector(self):
        """pid 活着但镜像名不是 python（svchost 等）-> 不算采集器。"""
        with _mock.patch.object(_dc, 'proc_image_name',
                                return_value='svchost.exe'):
            self.assertFalse(_dc.pid_alive(15848))

    def test_python_pid_is_collector(self):
        with _mock.patch.object(_dc, 'proc_image_name',
                                return_value='pythonw.exe'):
            self.assertTrue(_dc.pid_alive(4242))

    def test_born_mismatch_rejected(self):
        """镜像名对但创建时间不符 = 同 pid 的另一条命 -> 拒绝。"""
        with _mock.patch.object(_dc, 'proc_image_name',
                                return_value='python.exe'), \
                _mock.patch.object(_dc, 'proc_born', return_value=222):
            self.assertFalse(_dc.pid_alive(4242, born=111))

    def test_born_match_accepted(self):
        with _mock.patch.object(_dc, 'proc_image_name',
                                return_value='python.exe'), \
                _mock.patch.object(_dc, 'proc_born', return_value=111):
            self.assertTrue(_dc.pid_alive(4242, born=111))

    def test_dead_pid_rejected(self):
        with _mock.patch.object(_dc, 'proc_image_name', return_value=None):
            self.assertFalse(_dc.pid_alive(999999))

    def test_empty_pid_rejected(self):
        self.assertFalse(_dc.pid_alive(None))
        self.assertFalse(_dc.pid_alive(0))

    def test_own_pid_accepted_for_real(self):
        """真环境自证：本进程就是 python，必须被判成活采集器。"""
        self.assertTrue(_dc.pid_alive(os.getpid()))


if __name__ == '__main__':
    unittest.main()
