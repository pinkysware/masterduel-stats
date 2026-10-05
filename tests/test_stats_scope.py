# -*- coding: utf-8 -*-
"""统计口径 —— **按「每一轮（会话）」** 的单测（用户 2026-09-16 定）。

用**对局间隔**切轮（相邻两局间隔 ≥ 60 分钟 = 新的一轮），**不监听游戏进程**
（悬浮窗口径 = 只读库、不碰内存）。关掉游戏一般隔很久才再开，效果等同于
「每次开游戏算一轮」，而且**随时打开窗口都能算出来** —— 不需要常驻任务，
也不需要「凌晨 2 点跑一次」那种定时器。

真库不好造边界，所以 `session_start_of` 做成**纯函数**，这里直接喂字符串列表。
"""
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.storage import (                                                     # noqa: E402
    SESSION_GAP_MIN, Storage, end_time_of, session_start_of)

DAY = '2026-09-15'


def _at(hour, minute):
    return '%s %02d:%02d:00' % (DAY, hour, minute)


class SessionStartOfTest(unittest.TestCase):
    """切轮的判据（纯函数）。"""

    def test_empty(self):
        self.assertIsNone(session_start_of([]))
        self.assertIsNone(session_start_of(None))

    def test_single_match(self):
        self.assertEqual(session_start_of([_at(22, 0)]), _at(22, 0))

    def test_within_gap_is_one_session(self):
        """间隔 59 分钟 = 同一轮 -> 起点是第一局。"""
        self.assertEqual(session_start_of([_at(22, 0), _at(22, 59)]), _at(22, 0))

    def test_over_gap_splits(self):
        """间隔 61 分钟 = 断开 -> 本轮从第二局开始。"""
        self.assertEqual(session_start_of([_at(22, 0), _at(23, 1)]), _at(23, 1))

    def test_exactly_60_minutes_splits(self):
        """正好 60 分钟算断开（`>=` 阈值）。"""
        self.assertEqual(session_start_of([_at(22, 0), _at(23, 0)]), _at(23, 0))

    def test_last_session_wins(self):
        """22:00 -> 22:10（同轮）-> 23:30（断开）-> 23:40（同轮）=> 本轮起点 = 23:30。"""
        got = session_start_of([_at(22, 0), _at(22, 10), _at(23, 30), _at(23, 40)])
        self.assertEqual(got, _at(23, 30))

    def test_bad_values_are_skipped(self):
        """坏数据不该让统计全挂。"""
        got = session_start_of([None, '', 'garbage', _at(9, 0), _at(9, 5)])
        self.assertEqual(got, _at(9, 0))

    def test_millisecond_form_is_accepted(self):
        """日志行有时带毫秒（`19:12:43.227`）。"""
        got = session_start_of(['2026-09-13 19:12:43.227', '2026-09-13 19:20:00'])
        self.assertEqual(got, '2026-09-13 19:12:43')

    def test_default_gap_is_60(self):
        self.assertEqual(SESSION_GAP_MIN, 60)


class EndTimeTest(unittest.TestCase):
    """`end_time_of` —— 一局的结束时刻（切轮判据的输入）。"""

    def test_prefers_ended_at(self):
        self.assertEqual(end_time_of('2026-09-15 22:00:00',
                                     ended_at='2026-09-15 22:50:00',
                                     duration_sec=999),
                         '2026-09-15 22:50:00')

    def test_falls_back_to_duration(self):
        """没有 ended_at 有时长 -> started_at + duration。"""
        self.assertEqual(end_time_of('2026-09-15 22:00:00', duration_sec=600),
                         '2026-09-15 22:10:00')

    def test_falls_back_to_started(self):
        """两样都没有（老数据）-> 退回开局 = 老口径，不猜、也不丢这一局。"""
        self.assertEqual(end_time_of('2026-09-15 22:00:00'),
                         '2026-09-15 22:00:00')

    def test_bad_values_ignored(self):
        self.assertEqual(end_time_of('2026-09-15 22:00:00', ended_at='garbage',
                                     duration_sec='x'),
                         '2026-09-15 22:00:00')
        self.assertIsNone(end_time_of(None))
        self.assertIsNone(end_time_of('garbage'))
        # 结束早于开始 = 坏数据 -> 退回开局（不能算出负时长）
        self.assertEqual(end_time_of('2026-09-15 22:00:00',
                                     ended_at='2026-09-15 21:00:00'),
                         '2026-09-15 22:00:00')

    def test_zero_duration_is_ignored(self):
        self.assertEqual(end_time_of('2026-09-15 22:00:00', duration_sec=0),
                         '2026-09-15 22:00:00')


class SessionEndGapTest(unittest.TestCase):
    """★ 切轮看「上一局**结束** → 本局**开局**」—— **超长对局不假切轮**。

    用户 2026-09-17 提问引出：一局打很久时，按「两个开局相减」算出的间隔会
    凭空多出这局的时长，于是**连着打的局被误判成「歇了一轮」**（统计被劈开）。
    """

    def test_long_previous_duel_does_not_split(self):
        """上一局 21:00→22:50（110 分钟），本局 22:55 开局 = 只歇 5 分钟。

        按开局算 = 115 分钟（会误切轮）；按结束算 = 5 分钟（同一轮）。
        """
        pairs = [('2026-09-15 21:00:00', '2026-09-15 22:50:00'),
                 ('2026-09-15 22:55:00', '2026-09-15 23:05:00')]
        self.assertEqual(session_start_of(pairs, now='2026-09-15 23:10:00'),
                         '2026-09-15 21:00:00')          # ★ 同一轮，起点是 21:00

    def test_same_pair_would_split_without_end_times(self):
        """对照：不给结束时刻（老口径）同一组数据**会**误切轮 —— 证明这判据有用。"""
        starts_only = ['2026-09-15 21:00:00', '2026-09-15 22:55:00']
        self.assertEqual(session_start_of(starts_only,
                                          now='2026-09-15 23:10:00'),
                         '2026-09-15 22:55:00')          # ❌ 被劈开了

    def test_real_break_still_splits(self):
        """真的歇久了（上一局结束 21:05 → 本局 23:00 开局）-> 照常切轮。"""
        pairs = [('2026-09-15 21:00:00', '2026-09-15 21:05:00'),
                 ('2026-09-15 23:00:00', '2026-09-15 23:10:00')]
        self.assertEqual(session_start_of(pairs, now='2026-09-15 23:20:00'),
                         '2026-09-15 23:00:00')

    def test_in_progress_row_does_not_clear_midway(self):
        """★ 正在打的那局**不该被判「本轮结束」**（打到一半统计清零是最坏症状）。

        为什么不会发生：库里**只有已结束的局**（record 目录 `state='open'`
        的不入库，见 `import_duel_records`），所以「最新一局」的结束时刻总是有值、
        不会出现「拿开局当结束、于是自己把自己算成空闲」的情况。
        这里用「明确给了结束时刻」的进行中局验证判据本身没问题。
        """
        pairs = [('2026-09-15 21:50:00', '2026-09-15 21:55:00'),
                 ('2026-09-15 22:00:00', '2026-09-15 23:29:00')]  # 打了 89 分钟
        # 现在 23:30 = 刚结束 1 分钟 -> 本轮活着（不能因为"这局打得久"就清空）
        self.assertEqual(session_start_of(pairs, now='2026-09-15 23:30:00'),
                         '2026-09-15 21:50:00')

    def test_long_duel_then_idle_still_clears(self):
        """长局结束之后真的歇久了 -> 照常清空（不是"因为局长就永远不清"）。"""
        pairs = [('2026-09-15 22:00:00', '2026-09-15 23:29:00')]
        self.assertIsNone(session_start_of(pairs, now='2026-09-16 00:30:00'))

    def test_mixed_old_and_new_rows(self):
        """老式「只有开局」的行与新式二元组可以混着喂（兼容存量数据）。"""
        mixed = [('2026-09-15 21:00:00', '2026-09-15 21:05:00'),
                 '2026-09-15 23:00:00']
        # 21:05 结束 -> 23:00 开局 = 115 分钟 -> 切轮（新式行也参与比较）
        self.assertEqual(session_start_of(mixed, now='2026-09-15 23:10:00'),
                         '2026-09-15 23:00:00')


class SessionTailGapTest(unittest.TestCase):
    """★ **尾部空档**：最后一局距今 ≥60 分钟 = 本轮结束（2026-09-16 晚补的判据）。

    没这条时的实机事故：下午 16:16 打完最后一局、晚上 23:34 打开悬浮窗，
    中间 7 小时空档**没有任何一对相邻局能体现**→ 仍返回下午那轮起点，
    总览/先后攻一直显示旧数字（用户报「和之前设计的不一样，说好一小时后清空」）。
    """

    SAME_ROUND = [_at(22, 0), _at(22, 30), _at(22, 59)]

    def test_alive_within_gap(self):
        """最后一局 59 分钟前 -> 本轮还活着，起点照常。"""
        self.assertEqual(session_start_of(self.SAME_ROUND, now=_at(23, 58)),
                         _at(22, 0))

    def test_expired_at_exactly_60(self):
        """正好 60 分钟 = 超时（与切轮同一条 `>=` 阈值）。"""
        self.assertIsNone(session_start_of(self.SAME_ROUND, now=_at(23, 59)))

    def test_expired_over_gap(self):
        """隔了 7 小时 -> 本轮结束（返回 None，统计该清空）。"""
        self.assertIsNone(
            session_start_of(['2026-09-16 16:16:02'], now='2026-09-16 23:34:00'))

    def test_now_accepts_datetime_too(self):
        import datetime as _dt
        got = session_start_of(self.SAME_ROUND,
                               now=_dt.datetime(2026, 9, 15, 23, 58, 0))
        self.assertEqual(got, _at(22, 0))

    def test_without_now_no_tail_check(self):
        """不给 `now` = **不判尾部**（纯切轮）—— 老调用方/老单测行为不变。"""
        self.assertEqual(session_start_of(self.SAME_ROUND), _at(22, 0))

    def test_expired_returns_none_even_with_single_match(self):
        """只有一局、且那是很久以前 -> 同样算本轮结束。"""
        self.assertIsNone(session_start_of(['2026-09-16 16:16:02'],
                                           now='2026-09-16 18:00:00'))

    def test_last_session_wins_before_expiry(self):
        """刚打完最后一局（1 分钟前）-> 仍是「取最后一轮」。"""
        times = [_at(22, 0), _at(22, 10), _at(23, 30), _at(23, 40)]
        self.assertEqual(session_start_of(times, now=_at(23, 41)), _at(23, 30))


class StorageSessionStartTest(unittest.TestCase):
    """走真实 Storage（临时库）—— 只验「读得到、间隔对」。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='statsscope_')
        self.st = Storage(os.path.join(self.d, 'masterduel.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _add(self, started_at):
        with self.st._open() as conn:
            conn.execute(
                'INSERT INTO matches (match_id, started_at) VALUES (?, ?)',
                ('m' + started_at.replace(' ', 'T'), started_at))

    def test_empty_db(self):
        self.assertIsNone(self.st.session_start(now=_at(23, 0)))

    def test_one_session(self):
        self._add(_at(22, 0))
        self._add(_at(22, 30))
        self.assertEqual(self.st.session_start(now=_at(23, 0)), _at(22, 0))

    def test_gap_splits(self):
        self._add(_at(22, 0))
        self._add(_at(23, 30))
        self.assertEqual(self.st.session_start(now=_at(23, 45)), _at(23, 30))

    def test_null_started_at_is_ignored(self):
        """`started_at` 是 NOT NULL，这里只保证查询条件把空串排掉后仍能算。"""
        self._add(_at(22, 0))
        self.assertEqual(self.st.session_start(now=_at(22, 30)), _at(22, 0))

    def test_uses_wall_clock_by_default(self):
        """不给 `now` 时用**本机当前时刻** —— 老数据（很久以前）自然算「本轮已结束」。"""
        self._add(_at(22, 0))
        self.assertIsNone(self.st.session_start())


class ExpiredSessionShapeTest(unittest.TestCase):
    """★ 本轮超时后**统计该长什么样**（用户 2026-09-16 晚定的形态）。

    要的是「**新一轮还没开打**」：胜负 `0-0`、先攻/后攻 `0`、比率 `-`。
    这里用**真库**验 —— 零值形状由 `get_stats` 产，桩类产不出来。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='statsscope_')
        self.st = Storage(os.path.join(self.d, 'masterduel.db'))
        self.st.init_schema()
        # 下午打完的三局（有胜有负、有先有后）—— 用来证明「超时后不显示它们」
        self._add('2026-09-16 16:00:00', 'Win', 1)
        self._add('2026-09-16 16:05:00', 'Lose', 0)
        self._add('2026-09-16 16:16:02', 'Win', 1)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _add(self, started_at, result=None, went_first=None, game_mode=3):
        """`game_mode=3`（天梯）—— 界面口径 `duel_clause()` 才认得（默认 3）。"""
        with self.st._open() as conn:
            conn.execute(
                'INSERT INTO matches (match_id, started_at, result, went_first,'
                ' game_mode) VALUES (?, ?, ?, ?, ?)',
                ('m' + started_at.replace(' ', 'T'), started_at, result,
                 went_first, game_mode))

    def _zero(self):
        """本轮超时 -> 换「不可能命中的起点」查（`ui_state.snapshot` 的做法）。"""
        from core.ui_state import NO_SESSION_SINCE
        return self.st.get_stats(since=NO_SESSION_SINCE, mode_scope='duel')

    def test_counts_are_zero_and_rates_are_none(self):
        self.assertIsNone(self.st.session_start(now='2026-09-16 23:34:00'))
        st = self._zero()
        # 计数从 0 起
        self.assertEqual(st['n_win'], 0)
        self.assertEqual(st['n_lose'], 0)
        self.assertEqual(st['first_n'], 0)
        self.assertEqual(st['second_n'], 0)
        # 比率是 None -> 界面画 `-`（0% 是假的）
        self.assertIsNone(st['win_rate'])
        self.assertIsNone(st['first_hand_pct'])
        self.assertIsNone(st['first_rate'])
        self.assertIsNone(st['second_rate'])

    def test_not_the_whole_db(self):
        """★ 对照：**不限时间**（`since=None`）会拿到全库 3 局 —— 那正是要避免的谎。"""
        all_db = self.st.get_stats(mode_scope='duel')
        self.assertEqual(all_db['n_total'], 3)
        self.assertEqual(self._zero()['n_total'], 0)

    def test_alive_session_still_counts(self):
        """本轮还活着（最后一局 30 分钟前）-> 三局照算（防改过头）。"""
        self.assertEqual(self.st.session_start(now='2026-09-16 16:46:00'),
                         '2026-09-16 16:00:00')
        alive = self.st.get_stats(since='2026-09-16 16:00:00', mode_scope='duel')
        self.assertEqual(alive['n_total'], 3)
        self.assertEqual(alive['n_win'], 2)


if __name__ == '__main__':
    unittest.main()
