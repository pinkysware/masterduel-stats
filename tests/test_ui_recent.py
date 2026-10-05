# -*- coding: utf-8 -*-
"""最近几场的数据层单测（M7 改版）—— `build_recent` / `current_rank` / `get_recent_matches`。

盯三件事：
1. **顺序**：最近的在最前（`get_recent_matches` 是 DESC；`list_matches` 是 ASC，
   拿错会取到几个月前的老局 —— 2026-09-14 实测踩过）；
2. **升段推导**：`after` 用时间上更晚那一局的 `before`（库里只有 before）；
3. **不炸**：库锁了 / 字段缺了，退回空列表，不许抛。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import ui_state as U                                       # noqa: E402


class FakeDecks(object):
    """假的卡图解析器 —— 把数据层和素材层解耦，测起来才快。"""

    def resolve(self, name):
        return ('data/deck_images/%s.webp' % name) if name else \
            'data/deck_images_custom/_default.webp'


def m(started, result='Win', mode=3, rank=None, rate=None,
      my_deck='闪刀姬', opp_deck=None, opp_player='某人', mid=None):
    return {'match_id': mid or ('m' + started[-5:]),
            'started_at': started, 'result': result, 'game_mode': mode,
            'my_rank_before': rank, 'my_rate_before': rate,
            'my_deck_name': my_deck, 'opp_deck_name': opp_deck,
            'opp_player_name': opp_player}


class FakeStorage(object):
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.calls = []

    def get_recent_matches(self, limit=10, account=None):
        self.calls.append(limit)
        if self.fail:
            raise RuntimeError('db locked')
        return self.rows[:limit]


class OrderTest(unittest.TestCase):
    """★ 顺序：DESC 拿到的第一条就是最新那场。"""

    def setUp(self):
        # DESC：最新在前
        self.rows = [m('2026-09-13 19:53'), m('2026-09-13 19:22'),
                     m('2026-09-12 15:30'), m('2026-08-01 10:00')]
        self.st = FakeStorage(self.rows)

    def test_newest_first(self):
        rec = U.build_recent(self.st, limit=3, decks=FakeDecks())
        self.assertEqual([r['started_at'][:10] for r in rec],
                         ['2026-09-13', '2026-09-13', '2026-09-12'])

    def test_limit_is_respected(self):
        self.assertEqual(len(U.build_recent(self.st, limit=2,
                                            decks=FakeDecks())), 2)
        self.assertEqual(len(U.build_recent(self.st, limit=10,
                                            decks=FakeDecks())), 4)

    def test_asks_storage_for_a_wider_window(self):
        """多拉一段（limit*4+8）：滤掉单人局后仍够 limit+1（after 倒推 + 单人防挤占）。"""
        U.build_recent(self.st, limit=3, decks=FakeDecks())
        self.assertEqual(self.st.calls[-1], 3 * 4 + 8)

    def test_uses_get_recent_matches_not_list_matches(self):
        """★ 回归：必须走 DESC 的 `get_recent_matches`，

        用 `list_matches()`（ASC）会取到几个月前的老局 —— 已踩过。
        """
        class OnlyRecent(object):
            def get_recent_matches(self, limit=10, account=None):
                return [m('2026-09-13 19:53')]

            def list_matches(self, *a, **kw):
                raise AssertionError('不该调 list_matches')
        rec = U.build_recent(OnlyRecent(), decks=FakeDecks())
        self.assertEqual(len(rec), 1)


class FieldTest(unittest.TestCase):

    def setUp(self):
        self.st = FakeStorage([m('2026-09-13 19:53', opp_deck='烙印',
                                 opp_player='毛玉')])
        self.row = U.build_recent(self.st, decks=FakeDecks())[0]

    def test_basic_fields(self):
        self.assertEqual(self.row['time'], '19:53')
        self.assertEqual(self.row['day'], '09-13')
        self.assertEqual(self.row['result'], 'Win')
        self.assertEqual(self.row['opp_deck'], '烙印')
        self.assertTrue(self.row['opp_known'])
        self.assertEqual(self.row['opp_player'], '毛玉')

    def test_unmarked_opponent(self):
        st = FakeStorage([m('2026-09-13 19:53', opp_deck=None)])
        row = U.build_recent(st, decks=FakeDecks())[0]
        self.assertFalse(row['opp_known'])
        self.assertIn('_default', row['opp_deck_img'])

    def test_missing_player_name_is_none(self):
        st = FakeStorage([m('2026-09-13 19:53', opp_player=None)])
        self.assertIsNone(U.build_recent(st, decks=FakeDecks())[0]
                          ['opp_player'])

    def test_deck_image_resolved_for_both_sides(self):
        """两边都**过了解析器**（这里用假的，所以路径里带原名）。

        真实的「中文名 -> Sky_Striker.webp」那步在 `tests/test_deck_images.py` 里测。
        """
        self.assertIn('闪刀姬', self.row['my_deck_img'])
        self.assertIn('烙印', self.row['opp_deck_img'])

    def test_badge_absent_without_rank_data(self):
        self.assertIsNone(self.row['badge_icon'])
        self.assertEqual(self.row['badge_text'], '')


class RankDeltaTest(unittest.TestCase):
    """升段的 `after` = 时间上更晚那一局的 `before`（库里只有 before）。"""

    def test_promotion_is_attributed_to_the_earlier_game(self):
        """★ 09-02 11:56 那局打完升段 —— 判据是**它后一局**的 before 变了。

        ⚠️ 所以箭头画在 `rec[1]`（11:56 那局）上，**不是** `rec[0]`：
        `rec[0]` 是最新一局，没有"下一局"，算不出 after。
        """
        rows = [  # DESC：新 -> 旧
            m('2026-09-02 11:57', rank=5, rate=4),      # 它的 before = 升段后的值
            m('2026-09-02 11:56', rank=5, rate=5),      # ← 升段发生在这一局
            m('2026-09-02 11:34', rank=5, rate=5),
        ]
        rec = U.build_recent(FakeStorage(rows), decks=FakeDecks())
        self.assertIsNone(rec[0]['rank_delta'], '最新一局没有 after，不该有箭头')
        self.assertEqual(rec[1]['rank_delta'], 'up')     # 5-5 -> 5-4
        self.assertEqual(rec[1]['rank_label_before'], 'Platinum 5')
        self.assertEqual(rec[1]['rank_label_after'], 'Platinum 4')
        self.assertEqual(rec[2]['rank_delta'], 'same',
                         '最老那局和后一局同段位 -> same（不该画箭头）')

    def test_demotion(self):
        rows = [m('2026-09-02 12:00', rank=5, rate=3),
                m('2026-09-02 11:56', rank=5, rate=2)]
        rec = U.build_recent(FakeStorage(rows), decks=FakeDecks())
        # 5-2 -> 5-3 = 小段数字变大 = 掉段，画在 11:56 那局上
        self.assertEqual(rec[1]['rank_delta'], 'down')

    def test_newest_row_uses_memory_after(self):
        """★ 最新一局没有「下一局」可倒推 —— 内存给了 `my_rank_after` 就用它。

        实机踩到：升到白金1 但那一行还是旧段位/空（2026-09-15 深夜）。
        """
        row = m('2026-09-15 23:45', mid='newest', rank=5, rate=2)
        row['my_rank_after'] = 5
        row['my_rate_after'] = 1
        st = FakeStorage([row])
        recent = U.build_recent(st, limit=3, decks=FakeDecks())
        got = recent[0]
        self.assertEqual(got['rank_before'], (5, 2))
        self.assertEqual(got['rank_after'], (5, 1))          # 内存值优先
        self.assertEqual(got['rank_delta'], 'up')            # 界面画 ↑
        self.assertEqual(got['rank_icon_after'],
                         U.R.icon_path(5) if hasattr(U, 'R') else
                         __import__('core.ranks', fromlist=['x']).icon_path(5))

    def test_newest_row_has_no_after(self):
        """最新那局没有"下一局"，所以算不出 after —— 不许编一个。"""
        rows = [m('2026-09-13 19:53', rank=5, rate=2)]
        rec = U.build_recent(FakeStorage(rows), decks=FakeDecks())
        self.assertIsNone(rec[0]['rank_after'])
        self.assertIsNone(rec[0]['rank_delta'])
        self.assertEqual(rec[0]['rank_label_after'], '')
        self.assertIsNone(rec[0]['rank_icon_after'])

    def test_promotion_carries_the_after_icon(self):
        rows = [m('2026-09-02 11:57', rank=5, rate=4),
                m('2026-09-02 11:56', rank=5, rate=5)]
        rec = U.build_recent(FakeStorage(rows), decks=FakeDecks())
        self.assertEqual(rec[1]['rank_icon_after'], 'data/rank_icons/Platinum.webp')

    def test_non_ladder_modes_never_compare_ranks(self):
        """★ 巅峰/DC/活动局**不算段位变化**（2026-09-15 用户口径：
        巅峰赛只显示巅峰图标，没有段位数字、也没有升降箭头）。

        `r_after` 取的是「下一局」开打前的段位 —— 巅峰局的下一局多半是天梯，
        跨模式比出来只会是**错的升降段**（会拿天梯图标顶掉巅峰图标）。
        """
        for mode in (19, 12, 21):                # 巅峰 / DC 杯 / 活动（骰子拉力）
            rows = [m('2026-09-02 11:57', mode=mode, rank=5, rate=4),
                    m('2026-09-02 11:56', mode=mode, rank=5, rate=5)]
            rec = U.build_recent(FakeStorage(rows), decks=FakeDecks())
            for r in rec:
                self.assertIsNone(r['rank_after'], 'mode=%r' % mode)
                self.assertIsNone(r['rank_delta'], 'mode=%r' % mode)
                self.assertEqual(r['rank_label_after'], '', 'mode=%r' % mode)
                self.assertIsNone(r['rank_icon_after'], 'mode=%r' % mode)
            if mode == 19:                       # 巅峰局的标识位仍是巅峰图标
                self.assertEqual(rec[1]['badge_icon'],
                                 'data/mode_icons/peak.webp')
                self.assertEqual(rec[1]['badge_text'], '巅峰决斗')


class CurrentRankTest(unittest.TestCase):
    """段位字段只有 25/54 有值 —— 最新几局常是空的，要往前扫。"""

    def test_uses_latest_match_that_has_rank(self):
        rows = [m('2026-09-13 19:53'),                    # 没段位
                m('2026-09-10 23:08', rank=5, rate=2)]    # 有
        got = U.current_rank(FakeStorage(rows))
        self.assertEqual(got['label'], 'Platinum 2')
        self.assertEqual(got['icon'], 'data/rank_icons/Platinum.webp')

    def test_none_when_nothing_has_rank(self):
        self.assertIsNone(U.current_rank(FakeStorage([m('2026-09-13 19:53')])))

    def test_none_on_empty(self):
        self.assertIsNone(U.current_rank(FakeStorage([])))

    def test_never_raises(self):
        self.assertIsNone(U.current_rank(FakeStorage(fail=True)))


class RobustnessTest(unittest.TestCase):

    def test_empty_db_gives_empty_list(self):
        self.assertEqual(U.build_recent(FakeStorage([]), decks=FakeDecks()), [])

    def test_storage_failure_gives_empty_list(self):
        """★ 库锁了不能抛 —— UI 崩在游戏上面是最糟的失败模式。"""
        self.assertEqual(
            U.build_recent(FakeStorage(fail=True), decks=FakeDecks()), [])

    def test_one_bad_row_does_not_kill_the_list(self):
        """单行解析失败就跳过那一行，别把整列拖没。"""
        class HalfBroken(FakeDecks):
            def resolve(self, name):
                if name == '坏卡组':
                    raise RuntimeError('boom')
                return super().resolve(name)
        rows = [m('2026-09-13 19:53', opp_deck='坏卡组'),
                m('2026-09-13 19:22')]
        rec = U.build_recent(FakeStorage(rows), decks=HalfBroken())
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]['time'], '19:22')

    def test_snapshot_survives_broken_storage(self):
        class Broken(object):
            def get_recent_matches(self, limit=10, account=None):
                raise RuntimeError('nope')

            def get_stats(self, since=None, until=None, mode_group=None,
                                     mode_scope=None):
                raise RuntimeError('nope')

            def get_today_stats(self):
                raise RuntimeError('nope')

            def get_stats_by_deck(self):
                raise RuntimeError('nope')

            def get_daily_stats(self):
                raise RuntimeError('nope')

            def get_unmarked_matches(self, limit=100):
                raise RuntimeError('nope')

            def max_rowid(self):
                return 0
        snap = U.snapshot(Broken())
        self.assertEqual(snap.recent, [])
        self.assertIsNone(snap.my_rank)
        self.assertEqual(snap.mode, U.MODE_IDLE)


class RealDbTest(unittest.TestCase):
    """拿真库跑一遍（只读）—— 保证顺序和字段在真实数据上立得住。"""

    def setUp(self):
        from core.storage import Storage
        self.st = Storage()

    def test_recent_is_descending(self):
        rec = U.build_recent(self.st, limit=3)
        starts = [r['started_at'] for r in rec]
        self.assertEqual(starts, sorted(starts, reverse=True),
                         'recent 必须是新 -> 旧')

    def test_recent_rows_have_required_keys(self):
        need = {'match_id', 'started_at', 'day', 'time', 'is_today', 'result',
                'went_first', 'my_deck_img', 'opp_deck_img', 'opp_known',
                'badge_icon', 'badge_text', 'rank_delta', 'badge_numeral',
                'badge_numeral_after'}
        for r in U.build_recent(self.st, limit=3):
            self.assertTrue(need <= set(r), need - set(r))

    def test_storage_get_recent_matches_is_desc(self):
        rows = self.st.get_recent_matches(limit=5)
        starts = [r['started_at'] for r in rows]
        self.assertEqual(starts, sorted(starts, reverse=True))


if __name__ == '__main__':
    unittest.main()
