# -*- coding: utf-8 -*-
"""`tools/capture_duel_mode.py` 的单测 —— 状态机 + 写库配对。

不碰真实游戏进程、不碰真实库：状态机是纯逻辑，写库走临时库（`Storage(tempdir)`）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import capture_duel_mode as cdm  # noqa: E402
from core.storage import (Storage, MODE_GROUP_DC, MODE_GROUP_EVENT,  # noqa: E402
                          MODE_GROUP_PEAK, MODE_GROUP_RANKED, mode_group)

T = '2026-09-12 15:%02d:00'


def mk(mid, started, result='Win', mode='pvp', game_mode=None, **kw):
    """造一条和 `tests/test_storage.py` 同形状的对局行。"""
    row = dict(
        match_id=mid, started_at=started,
        ended_at=started if result != '(未结束)' else None,
        my_deck_id='48171157', my_deck_name='闪刀姬', deck_source='log',
        seat=0, seat_evidence='deck-hit P0=9/9 P1=0/6',
        won_initiative=None, went_first=True, first_player=0,
        result=result, total_turns=5, duration_sec=300,
        opp_deck_id=None, opp_deck_name=None, opp_deck_note=None,
        opp_revealed=[], opp_revealed_ids=[], mode=mode,
    )
    if game_mode is not None:
        row['game_mode'] = game_mode
    row.update(kw)
    return row


class TestTracker(unittest.TestCase):
    """一串采样 -> 一局一条记录；读失败不许当成"收局"。"""

    def test_no_duel_no_record(self):
        tr = cdm.DuelModeTracker()
        for i in range(3):
            self.assertIsNone(tr.observe(T % i, False))
        self.assertEqual(tr.records, [])

    def test_one_duel(self):
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, False)
        self.assertIsNone(tr.observe(T % 1, True, 3))
        self.assertIsNone(tr.observe(T % 2, True, 3))
        rec = tr.observe(T % 3, False)
        self.assertIsNotNone(rec)
        self.assertEqual(rec['started_at'], T % 1)
        self.assertEqual(rec['ended_at'], T % 3)
        self.assertEqual(rec['game_mode'], 3)
        self.assertEqual(rec['samples'], 2)
        self.assertEqual(rec['mode_name'], cdm.mode_name(3))
        self.assertEqual(tr.records, [rec])

    def test_two_duels_in_a_row(self):
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3)
        tr.observe(T % 1, False)
        tr.observe(T % 2, True, 20)      # 活动（骰子拉力那种）
        rec = tr.observe(T % 3, False)
        self.assertEqual([r['game_mode'] for r in tr.records], [3, 20])
        self.assertEqual(rec['game_mode'], 20)

    def test_unreadable_sample_keeps_duel_open(self):
        """`in_duel=None`（读失败）不能当成"对局结束"。"""
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3)
        self.assertIsNone(tr.observe(T % 1, None))
        self.assertIsNotNone(tr.open, '读失败不该把对局收掉')
        rec = tr.observe(T % 2, False)
        self.assertEqual(rec['game_mode'], 3)
        self.assertEqual(rec['samples'], 1)
        self.assertEqual(rec['started_at'], T % 0)

    def test_flush_closes_open_duel(self):
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3)
        rec = tr.flush(T % 5)
        self.assertEqual(rec['started_at'], T % 0)
        self.assertEqual(rec['ended_at'], T % 5)
        self.assertEqual(rec['end_reason'], 'flush')
        self.assertIsNone(tr.flush(T % 6), '没有开着的对局时 flush 返回 None')

    def test_finished_flag_closes_duel(self):
        """`pvpFinished` 变 1 立刻收局 —— 结算画面上实例还活着，不能等它消失。"""
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3, 0)
        rec = tr.observe(T % 1, True, 3, 1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec['end_reason'], 'pvpFinished')
        self.assertEqual(rec['ended_at'], T % 1)
        self.assertEqual(rec['game_mode'], 3)

    def test_result_screen_does_not_open_new_duel(self):
        """收局后实例还在（结算画面）→ 不许再开一局，否则一局会裂成好几条。"""
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3, 0)
        tr.observe(T % 1, True, 3, 1)          # 收局
        for i in (2, 3, 4):
            self.assertIsNone(tr.observe(T % i, True, 3, 0))
        self.assertIsNone(tr.open, '结算画面不该开新局')
        self.assertIsNone(tr.observe(T % 5, False))    # 回菜单：重置
        tr.observe(T % 6, True, 3, 0)                  # 下一局
        rec = tr.observe(T % 7, True, 3, 1)
        self.assertEqual(len(tr.records), 2)
        self.assertEqual(rec['started_at'], T % 6)

    def test_instance_gone_reason(self):
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3, 0)
        rec = tr.observe(T % 1, False)
        self.assertEqual(rec['end_reason'], 'instance-gone')

    def test_duplicate_modes_deduped_and_last_wins(self):
        tr = cdm.DuelModeTracker()
        tr.observe(T % 0, True, 3)
        tr.observe(T % 1, True, 3)
        tr.observe(T % 2, True, 19)
        rec = tr.observe(T % 3, False)
        self.assertEqual(rec['game_modes'], [3, 19])
        self.assertEqual(rec['game_mode'], 19, '多个模式值时取最后一个')

    def test_mode_name(self):
        self.assertEqual(cdm.mode_name(3), 'RANK（排名决斗）')
        self.assertEqual(cdm.mode_name(None), '?')
        self.assertIn('未知', cdm.mode_name(99))
        # 21 在 Untapped 枚举里是占位名 NULL；实测骰子拉力就是它
        self.assertEqual(cdm.mode_name(21), '未命名(21)')

    def test_group_name(self):
        self.assertEqual(cdm.group_name(3), '排名决斗')
        self.assertEqual(cdm.group_name(19), '巅峰决斗')
        self.assertEqual(cdm.group_name(12), 'DC杯')
        self.assertEqual(cdm.group_name(21), '活动决斗')   # 骰子拉力
        self.assertEqual(cdm.group_name(15), '活动决斗')   # Duel Trial
        self.assertEqual(cdm.group_name(None), '判不出')


class TestModeGroup(unittest.TestCase):
    """用户 2026-09-12 口径：只分四类，其余一律"活动决斗"（活动号每月都变）。"""

    def test_three_named_groups(self):
        self.assertEqual(mode_group(3), MODE_GROUP_RANKED)
        self.assertEqual(mode_group(19), MODE_GROUP_PEAK)
        self.assertEqual(mode_group(12), MODE_GROUP_DC)

    def test_everything_else_is_event(self):
        for gm in (0, 4, 7, 11, 13, 15, 20, 21, 99, 12345):
            self.assertEqual(mode_group(gm), MODE_GROUP_EVENT,
                             'game_mode=%s 应该落"活动决斗"' % gm)

    def test_none_is_unknown(self):
        self.assertIsNone(mode_group(None))


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='cdmtest_')
        self.st = Storage(os.path.join(self.d, 'masterduel.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    @staticmethod
    def rec(started, mode=3):
        return {'started_at': started, 'ended_at': started,
                'game_mode': mode, 'mode_name': 'RANK（排名决斗）', 'samples': 3}

    def modes(self):
        return {r['match_id']: r['game_mode']
                for r in self.st.list_matches(limit=50)}


class TestMerge(Base):
    """写库：只补 NULL、不覆盖；配对窗口与 `find_match_near` 一致。"""

    def test_only_fills_null_game_mode(self):
        self.st.upsert_match(mk('null1', '2026-09-12 15:00:00.000'))
        self.st.upsert_match(mk('has1', '2026-09-12 15:10:00.000', game_mode=3))
        # 采样器看到对局的时刻比日志行晚几秒（采样间隔所致）
        stat = cdm.merge_records([self.rec('2026-09-12 15:00:04'),
                                  self.rec('2026-09-12 15:10:04')], self.st)
        self.assertEqual(stat['applied'], 1)
        self.assertEqual(stat['skipped_has_value'], 1)
        self.assertEqual(stat['unmatched'], 0)
        self.assertEqual(self.modes(), {'null1': 3, 'has1': 3})

    def test_unmatched_when_no_row(self):
        stat = cdm.merge_records([self.rec('2026-09-12 15:00:00')], self.st)
        self.assertEqual((stat['applied'], stat['unmatched']), (0, 1))

    def test_no_mode_value_is_counted_separately(self):
        self.st.upsert_match(mk('null1', '2026-09-12 15:00:00.000'))
        rec = self.rec('2026-09-12 15:00:04')
        rec['game_mode'] = None
        stat = cdm.merge_records([rec], self.st)
        self.assertEqual((stat['no_mode'], stat['applied']), (1, 0))
        self.assertEqual(self.modes(), {'null1': None})

    def test_dry_run_writes_nothing(self):
        self.st.upsert_match(mk('null1', '2026-09-12 15:00:00.000'))
        stat = cdm.merge_records([self.rec('2026-09-12 15:00:04')], self.st,
                                 dry_run=True)
        self.assertEqual(stat['applied'], 1)
        self.assertEqual(self.modes(), {'null1': None})

    def test_merge_is_idempotent(self):
        self.st.upsert_match(mk('null1', '2026-09-12 15:00:00.000'))
        recs = [self.rec('2026-09-12 15:00:04')]
        cdm.merge_records(recs, self.st)
        stat = cdm.merge_records(recs, self.st)
        self.assertEqual((stat['applied'], stat['skipped_has_value']), (0, 1))
        self.assertEqual(self.modes(), {'null1': 3})


class TestRecordsIO(unittest.TestCase):
    """JSONL 读写：追加、读回、坏行跳过、不写 CRLF。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='cdmio_')
        self.path = os.path.join(self.d, 'duel_modes.jsonl')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_append_and_load(self):
        self.assertEqual(cdm.load_records(self.path), [])
        cdm.append_record({'started_at': 'a', 'game_mode': 3}, self.path)
        cdm.append_record({'started_at': 'b', 'game_mode': 7}, self.path)
        recs = cdm.load_records(self.path)
        self.assertEqual([r['game_mode'] for r in recs], [3, 7])

    def test_bad_line_skipped(self):
        cdm.append_record({'started_at': 'a', 'game_mode': 3}, self.path)
        with open(self.path, 'a', encoding='utf-8', newline='') as f:
            f.write('这不是 json\n')
        self.assertEqual(len(cdm.load_records(self.path)), 1)

    def test_no_crlf(self):
        """含中文的记录也别写成 CRLF（项目铁律 3）。"""
        cdm.append_record({'started_at': 'a', 'mode_name': '排名决斗'}, self.path)
        with open(self.path, 'rb') as f:
            raw = f.read()
        self.assertNotIn(b'\r\n', raw)


if __name__ == '__main__':
    unittest.main()
