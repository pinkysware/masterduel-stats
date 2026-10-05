# -*- coding: utf-8 -*-
"""app_service 单测 —— 状态机是纯逻辑，直接喂采样、看事件与记录。

场景取自实机：一局天梯（模式 3）从开局到 ``pvpFinished`` 收局，
中途走过「回合 1 主要1 → 回合 2 战斗 → LP 4000→0」这条轨迹。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_service import (  # noqa: E402
    DuelSessionTracker,
    EV_DUEL_END,
    EV_DUEL_START,
    EV_LP,
    EV_PHASE,
    EV_PUBLIC_CARDS,
    EV_TURN,
    STAGE_ENDED,
    STAGE_IDLE,
    STAGE_IN_DUEL,
)
from core.memread.duel_state import DuelState  # noqa: E402


def st(turn=0, phase=0, holder=0, near_lp=8000, far_lp=8000):
    return DuelState(turn=turn, phase=phase, turn_holder=holder,
                     near_lp=near_lp, far_lp=far_lp)


def kinds(events):
    return [e.kind for e in events]


class TestLifecycle(unittest.TestCase):

    def setUp(self):
        self.tr = DuelSessionTracker()

    def test_starts_on_instance_appearing(self):
        evs = self.tr.observe('t0', True, game_mode=3, state=st())
        self.assertEqual(kinds(evs), [EV_DUEL_START])
        self.assertEqual(self.tr.stage, STAGE_IN_DUEL)
        self.assertEqual(self.tr.records, [])

    def test_closes_on_pvp_finished(self):
        self.tr.observe('t0', True, game_mode=3, state=st())
        evs = self.tr.observe('t1', True, game_mode=3, finished=1, state=st())
        self.assertEqual(kinds(evs), [EV_DUEL_END])
        self.assertEqual(evs[0].data['end_reason'], 'pvpFinished')
        self.assertEqual(self.tr.stage, STAGE_ENDED)
        self.assertEqual(len(self.tr.records), 1)

    def test_closes_on_instance_gone(self):
        self.tr.observe('t0', True, game_mode=3, state=st())
        evs = self.tr.observe('t1', False)
        self.assertEqual(kinds(evs), [EV_DUEL_END])
        self.assertEqual(evs[0].data['end_reason'], 'instance-gone')
        self.assertEqual(self.tr.stage, STAGE_IDLE)

    def test_no_duel_open_gives_no_end_event(self):
        """主界面一直待着 —— 不该冒出对局结束事件。"""
        self.assertEqual(self.tr.observe('t0', False), [])
        self.assertEqual(self.tr.records, [])

    def test_read_failure_is_not_a_close(self):
        """读失败（in_duel=None）不许当成收局 —— 对局切换那一瞬很常见。"""
        self.tr.observe('t0', True, game_mode=3, state=st())
        self.assertEqual(self.tr.observe('t1', None), [])
        self.assertEqual(self.tr.stage, STAGE_IN_DUEL)
        self.assertEqual(self.tr.records, [])
        # 之后还能正常继续
        evs = self.tr.observe('t2', True, game_mode=3, state=st(turn=1))
        self.assertEqual(kinds(evs), [EV_TURN])

    def test_settlement_screen_does_not_open_new_duel(self):
        """收局后实例还在（结算画面）—— 不该开新局。"""
        self.tr.observe('t0', True, game_mode=3, state=st())
        self.tr.observe('t1', True, game_mode=3, finished=1, state=st())
        for i in range(5):
            evs = self.tr.observe('t%d' % (2 + i), True, game_mode=3,
                                  state=st())
            self.assertEqual(evs, [])
        self.assertEqual(len(self.tr.records), 1)

    def test_new_duel_after_returning_to_menu(self):
        """回主界面（实例消失）之后再开一局 —— 应该是新的一条记录。"""
        self.tr.observe('t0', True, game_mode=3, state=st())
        self.tr.observe('t1', True, game_mode=3, finished=1, state=st())
        self.tr.observe('t2', False)                    # 回主界面
        evs = self.tr.observe('t3', True, game_mode=3, state=st())
        self.assertEqual(kinds(evs), [EV_DUEL_START])
        self.assertEqual(len(self.tr.records), 1)

    def test_brief_instance_visit_is_recorded(self):
        """实例只出现一帧又被收掉 —— 仍然留一条记录（宁可多、不可漏）。"""
        self.tr.observe('t0', True, game_mode=3)          # 没有 state
        evs = self.tr.observe('t1', False)
        self.assertEqual(evs[0].data['end_reason'], 'instance-gone')
        self.assertEqual(len(self.tr.records), 1)
        self.assertEqual(self.tr.records[0]['samples'], 1)

    def test_flush_marks_reason(self):
        self.tr.observe('t0', True, game_mode=3, state=st())
        rec = self.tr.flush('t9')
        self.assertIsNotNone(rec)
        self.assertEqual(rec['end_reason'], 'flushed')
        self.assertIsNone(self.tr.open)


class TestTimeline(unittest.TestCase):

    def setUp(self):
        self.tr = DuelSessionTracker()
        self.tr.observe('t0', True, game_mode=3, state=st())

    def rec(self):
        return self.tr.open

    def test_turn_change_emits_event(self):
        evs = self.tr.observe('t1', True, state=st(turn=1))
        self.assertEqual(kinds(evs), [EV_TURN])
        self.assertEqual(evs[0].data['turn_number'], 2)      # 对外是 1 基
        self.assertEqual(evs[0].data['turn'], 1)             # 原始是 0 基

    def test_phase_change_emits_event(self):
        self.tr.observe('t1', True, state=st(phase=2))
        evs = self.tr.observe('t2', True, state=st(phase=3))
        self.assertEqual(kinds(evs), [EV_PHASE])
        self.assertEqual(evs[0].data['phase_name'], 'battle')

    def test_lp_change_emits_event(self):
        evs = self.tr.observe('t1', True, state=st(near_lp=4000))
        self.assertEqual(kinds(evs), [EV_LP])
        self.assertEqual(evs[0].data['near_lp'], 4000)
        self.assertEqual(evs[0].data['near_lp_before'], 8000)

    def test_no_change_no_event(self):
        """每秒都在采样，但状态没变就不该刷事件。"""
        for i in range(10):
            self.assertEqual(self.tr.observe('t%d' % i, True, state=st()), [])

    def test_timeline_only_records_changes(self):
        st0 = st()
        self.tr.observe('t1', True, state=st0)
        for i in range(10):
            self.tr.observe('t%d' % (2 + i), True, state=st0)
        # 第一帧 1 条 + 后面变化 0 条
        self.assertEqual(len(self.rec().timeline), 1)

    def test_multiple_changes_in_one_frame_emit_several(self):
        self.tr.observe('t1', True, state=st(turn=1))
        evs = self.tr.observe('t2', True, state=st(turn=2, phase=3,
                                                   far_lp=4000))
        self.assertEqual(kinds(evs), [EV_TURN, EV_PHASE, EV_LP])

    def test_samples_counter(self):
        # ⚠️ setUp 里已经 observe 过一帧，所以这里是 1 + 7
        for i in range(7):
            self.tr.observe('t%d' % i, True, state=st())
        self.assertEqual(self.rec().samples, 8)

    def test_state_none_still_counts_samples(self):
        """state 读不到（回放暂停那类）但实例在 —— 仍然是在对局中。

        这里 2 = setUp 那一帧 + 本帧。
        """
        before = len(self.rec().timeline)
        self.tr.observe('t1', True)
        self.assertEqual(self.rec().samples, 2)
        self.assertEqual(len(self.rec().timeline), before)   # 没 state 就不往轨迹里加

    def test_timeline_capped(self):
        tr = DuelSessionTracker()
        tr.MAX_TIMELINE = 5
        tr.observe('t0', True, state=st())
        for i in range(30):
            tr.observe('t%d' % (i + 1), True, state=st(turn=i + 1))
        self.assertLessEqual(len(tr.open.timeline), 5)


class TestPublicCards(unittest.TestCase):

    def setUp(self):
        self.tr = DuelSessionTracker()
        self.tr.observe('t0', True, state=st())

    def test_accumulates_new_only(self):
        evs = self.tr.observe('t1', True, state=st(), public_card_ids=[1, 2, 3])
        self.assertEqual(kinds(evs), [EV_PUBLIC_CARDS])
        self.assertEqual(evs[0].data['new_ids'], [1, 2, 3])
        evs = self.tr.observe('t2', True, state=st(), public_card_ids=[2, 3, 4])
        self.assertEqual(evs[0].data['new_ids'], [4])

    def test_already_seen_gives_no_event(self):
        self.tr.observe('t1', True, state=st(), public_card_ids=[1, 2])
        self.assertEqual(self.tr.observe('t2', True, state=st(),
                                         public_card_ids=[2, 1]), [])

    def test_none_means_not_read(self):
        self.assertEqual(self.tr.observe('t1', True, state=st()), [])

    def test_cards_do_not_roll_back(self):
        """已经公开过的卡不会因为「这次没读到」而被去掉。"""
        self.tr.observe('t1', True, state=st(), public_card_ids=[9])
        self.tr.observe('t2', True, state=st(), public_card_ids=[])
        self.assertEqual(self.tr.open.public_card_ids, [9])


class TestSessionRecord(unittest.TestCase):

    def _run(self):
        tr = DuelSessionTracker()
        tr.observe('2026-09-12 15:00:00', True, game_mode=3, state=st())
        tr.observe('2026-09-12 15:00:05', True, game_mode=3,
                   state=st(turn=1, phase=2))
        tr.observe('2026-09-12 15:00:20', True, game_mode=3,
                   state=st(turn=2, phase=3, near_lp=4000))
        tr.observe('2026-09-12 15:00:30', True, game_mode=3, finished=1,
                   state=st(turn=2, phase=3, near_lp=0))
        return tr.records[0]

    def test_record_shape(self):
        rec = self._run()
        for key in ('started_at', 'ended_at', 'end_reason', 'samples',
                    'game_mode', 'game_modes', 'max_turn', 'timeline',
                    'public_card_ids', 'last_state'):
            self.assertIn(key, rec)

    def test_basic_values(self):
        rec = self._run()
        self.assertEqual(rec['game_mode'], 3)
        self.assertEqual(rec['end_reason'], 'pvpFinished')
        # 4 帧里最后一帧是收局帧，不计入 samples
        self.assertEqual(rec['samples'], 3)

    def test_max_turn(self):
        """轨迹里最大的是 turn=2（0 基）-> 对外 3。"""
        self.assertEqual(self._run()['max_turn'], 3)

    def test_last_state_is_final(self):
        rec = self._run()
        self.assertEqual(rec['last_state']['near_lp'], 0)

    def test_dominant_mode_takes_last(self):
        tr = DuelSessionTracker()
        tr.observe('t0', True, game_mode=3, state=st())
        tr.observe('t1', True, game_mode=21, state=st())
        tr.observe('t2', False)
        self.assertEqual(tr.records[0]['game_mode'], 21)
        self.assertEqual(tr.records[0]['game_modes'], [3, 21])

    def test_empty_session_has_no_mode(self):
        """一帧 state 都没读到（实例刚建、结构还没就绪）—— 模式和回合都是空。"""
        tr = DuelSessionTracker()
        tr.observe('t0', True)          # 没有 state
        tr.observe('t1', False)
        self.assertIsNone(tr.records[0]['game_mode'])
        self.assertIsNone(tr.records[0]['max_turn'])


if __name__ == '__main__':
    unittest.main()
