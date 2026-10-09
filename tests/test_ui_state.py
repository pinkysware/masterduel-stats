# -*- coding: utf-8 -*-
"""core/ui_state.py 单测（M7 · S0）—— 纯逻辑，不碰真库、不碰 GUI。

重点盯「待录入」四条判据（审查 P1-3）：
积压不触发 / 新局触发 / 稍后不再触发 / 标注后重锚。
"""
import datetime
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import card_names as ui_state_cn                             # noqa: E402
from core import storage as core_storage                               # noqa: E402
from core import ui_actions                                            # noqa: E402
from core import ui_state                                              # noqa: E402

#: ⚠️ `ui/theme.py` 与 `ui/selector.py` 都是**纯 Python / 只依赖 PySide6 常量**的模块；
#: D4 从已删的 S6 统计窗单测里搬来了两组锁（伪条目名单 / 热力色阶），
#: 它们**不需要 QApplication**，所以在这里导入是安全的。
from ui import selector, theme                                          # noqa: E402


class _LangCnMixin(object):
    """把 `ui/lang` 锁回 cn（跑完还原）。

    ui_state 的显示名 / 状态字现在**按界面语言**出（实时切换，2026-10-04），
    会经 `ui.i18n` 读真实 QSettings —— 用户机器上可能已是 en，
    不锁就随机器状态翻车（测试封闭性）。
    """

    _FACTORY_PREV = 'unset'

    def setUp(self):
        import tempfile
        from PySide6.QtCore import QSettings
        import ui.i18n as _i18n
        cls = type(self)
        if cls._FACTORY_PREV == 'unset':   # 同类多个用例共用一份临时 INI
            cls._FACTORY_PREV = _i18n._SETTINGS_FACTORY
            cls._INI = tempfile.mkdtemp(prefix='m7-langcn-')
        _i18n._SETTINGS_FACTORY = staticmethod(
            lambda: QSettings(os.path.join(cls._INI, 'ui.ini'),
                              QSettings.IniFormat))
        from ui.i18n import reset_lang
        reset_lang()
        super().setUp()

    def tearDown(self):
        if getattr(self, '_FACTORY_PREV', 'unset') not in ('unset', None, False):
            pass                           # 类级状态留给下一用例复用，进程结束即弃
        super().tearDown()


def _row(rid, mid, **kw):
    row = {'rowid': rid, 'match_id': mid, 'started_at': '2026-09-13 19:00:00',
           'my_deck_name': '闪刀姬', 'result': 'Win', 'opp_deck_id': None}
    row.update(kw)
    return row


class FakeStorage(object):
    """只实现 ui_state 用得到的那几个方法；``fail`` 打开时全部抛异常。"""

    def __init__(self, rows=None, unmarked=None, fail=False, **over):
        self._rows = list(rows or [])
        self._unmarked = list(unmarked if unmarked is not None else self._rows)
        self.fail = fail
        self.calls = []
        self.over = over                       # 可覆盖单个方法的返回值

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError('db is locked')

    def count(self):
        self._maybe_fail()
        return self.over.get('count', len(self._rows))

    def backfill_pending(self):
        """段位/结果的事后回填欠账（2026-09-18 八轮进探针键）。"""
        return self.over.get('backfill_pending', 0)

    def max_rowid(self):
        self._maybe_fail()
        return self.over.get('max_rowid', 0)

    def get_stats(self, since=None, until=None, mode_group=None,
                  mode_scope=None, account=None):
        self.last_scope = mode_scope
        self.last_since = since
        self.last_until = until          # 口径断言要（本月窗口的月末）
        self._maybe_fail()
        return self.over.get('get_stats', {'n_total': len(self._rows),
                                           'unmarked': len(self._unmarked)})

    def session_start(self):
        """本轮起点。默认给一个「还活着」的值；``over`` 里可覆盖成 None = 已超时。"""
        self._maybe_fail()
        return self.over.get('session_start', '2026-09-13 19:00:00')

    def get_today_stats(self):
        self._maybe_fail()
        return self.over.get('get_today_stats', {'date': '2026-09-13',
                                                 'n_total': 0})

    def get_stats_by_deck(self, since=None, until=None, mode_group=None,
                          account=None):
        """⚠️ 假方法**必须带真方法的全部参数** —— 漏一个就会让上层抛异常被
        `except` 兜住，症状是「统计全空」而不是报错（记忆里记过这条坑）。"""
        self.last_deck_group = mode_group
        self._maybe_fail()
        if mode_group is not None:
            return list(self.over.get('by_deck_' + mode_group, []))
        return self.over.get('get_stats_by_deck', [('闪刀姬', {'n_total': 1})])

    def get_daily_stats(self, since=None, until=None, mode_group=None,
                        account=None):
        self.last_daily_group = mode_group
        self._maybe_fail()
        if mode_group is not None:
            return dict(self.over.get('daily_' + mode_group, {}))
        return self.over.get('get_daily_stats', {'2026-09-13': {'n': 1, 'w': 1,
                                                                'l': 0, 'd': 0}})

    def get_group_stats(self, since=None, until=None, groups=None):
        self._maybe_fail()
        return list(self.over.get('groups', []))

    def get_cross_stats(self, since=None, until=None, went_first=None,
                        mode_group=None):
        self._maybe_fail()
        self.last_went_first = went_first   # D2：页签参数要真的传到 storage
        self.last_mode_group = mode_group   # D2 v5：模式筛选也要真的传到
        self.last_cross_since = since       # D2 v5：月份窗口也要真的传到
        self.last_cross_until = until
        if went_first is None:
            return list(self.over.get('cross', []))
        #: 页签切片走 over 里的 ``cross_first`` / ``cross_second``（没给就退化成综合）
        return list(self.over.get('cross_first' if went_first
                                  else 'cross_second',
                                  self.over.get('cross', [])))

    def get_unmarked_matches(self, limit=50):
        self._maybe_fail()
        return list(self._unmarked)[:limit]

    def get_recent_matches(self, limit=10, account=None):
        self.last_account = account
        rows = list(self.over.get('recent', self._rows))
        if account:
            rows = [r for r in rows if r.get('account_name') == account]
        return rows[:int(limit)]

    def account_options(self):
        return self.over.get('account_options', [])
        self._maybe_fail()
        return list(self.over.get('recent', self._rows))[:int(limit)]

    def banlist_current(self):
        self._maybe_fail()
        return list(self.over.get('banlist_current', []))

    def list_banlist_changes(self, limit=None, since=None):
        self._maybe_fail()
        return list(self.over.get('banlist_changes', []))

    def banlist_current_status(self):
        """与真 storage 同语义：新表空 -> **自己回落**到旧算法（别抛 ——
        抛了会被数据层的外层 except 吞掉，回落根本走不到）。"""
        self._maybe_fail()
        got = self.over.get('banlist_status')
        if got is None:                      # 新表空 -> 回落旧算法（真 storage 同款）
            return [(n, s) for n, s, _e in self.banlist_current()]
        return list(got)

    def list_meta_decks(self, tiers=None, kinds=None, include_hidden=False):
        """`build_recent` / `tier_page_data`（D3）共用；新键 `meta_decks` 优先。"""
        if 'meta_decks' in self.over:
            return list(self.over.get('meta_decks'))
        return list(self.over.get('meta', []))

    def list_opp_decks(self):
        return list(self.over.get('opp', []))

    def unmarked_since(self, rowid, limit=50):
        self._maybe_fail()
        self.calls.append(rowid)
        out = [r for r in self._unmarked if r['rowid'] > rowid]
        return sorted(out, key=lambda r: r['rowid'], reverse=True)[:limit]


class RevealedIdsTest(unittest.TestCase):
    """★ M5 反推要 `opp_revealed_ids` —— 行 dict 里必须带上（2026-09-15 发现的缺口）。"""

    def test_recent_row_carries_revealed_ids(self):
        rows = [_row(1, 'm1', opp_revealed_ids='[19507, 19858]'),
                _row(2, 'm2', started_at='2026-09-13 20:00:00')]
        st = FakeStorage(rows=rows, **{'recent': rows})
        out = ui_state.build_recent(st, limit=2)
        self.assertEqual(out[0].get('opp_revealed_ids'), '[19507, 19858]')
        self.assertIsNone(out[1].get('opp_revealed_ids'))


class DeckNamePoolTest(unittest.TestCase):
    """★ 宽度上限要用名字池 —— 两种数据形状（dict / tuple）都得认。"""

    def test_collects_meta_and_opp_names(self):
        meta = [{'name': 'Branded', 'name_cn': '烙印', 'name_display': '烙印星辰',
                 'name_ja': '烙印', 'aliases': ['BrandedDracotail']}]
        opp = [(1, 'Dracotail', ['星辰'], 3)]
        pool = ui_state.deck_name_pool(FakeStorage(rows=[], **{
            'meta': meta, 'opp': opp}))
        for want in ('Branded', '烙印', '烙印星辰', 'BrandedDracotail',
                     'Dracotail', '星辰'):
            self.assertIn(want, pool)

    def test_never_raises(self):
        self.assertEqual(ui_state.deck_name_pool(FakeStorage(fail=True)), [])


class RecentLimitTest(unittest.TestCase):
    """★ D2：数据层一次取 10 局（UI 才能拖高 / 滚动看到更多）。"""

    @staticmethod
    def _rows(n=12):
        return [_row(i, 'm%d' % i, started_at='2026-09-13 19:%02d:00' % i)
                for i in range(1, n + 1)]

    def test_snapshot_honours_recent_limit(self):
        rows = self._rows()
        st = FakeStorage(rows=rows, **{'recent': rows})
        self.assertEqual(len(ui_state.snapshot(st, recent_limit=10).recent), 10)

    def test_build_recent_asks_for_one_extra_row(self):
        """多要一行是**故意的**：最老那行的「局后段位」靠它上一局倒推。"""
        rows = self._rows(3)
        st = FakeStorage(rows=rows, **{'recent': rows})
        self.assertEqual(len(ui_state.build_recent(st, limit=2)), 2)

    def test_default_limit_is_still_three(self):
        """默认值不动 —— 显式传参才取 10（别偷偷全区改口径）。"""
        rows = self._rows(12)
        st = FakeStorage(rows=rows, **{'recent': rows})
        self.assertEqual(len(ui_state.snapshot(st).recent), 3)


class ProbeTest(unittest.TestCase):

    def test_probe_reads(self):
        st = FakeStorage(rows=[_row(1, 'a'), _row(2, 'b')], unmarked=[_row(2, 'b')],
                         **{'max_rowid': 2})
        p = ui_state.probe(st)
        self.assertEqual((p.n_total, p.max_rowid, p.unmarked), (2, 2, 1))

    def test_probe_never_raises(self):
        self.assertIsNone(ui_state.probe(FakeStorage(fail=True)))

    def test_probe_counts_backfill_pending(self):
        """★ 回填欠账必须进探针键（2026-09-18 八轮）：段位/结果是收局后
        UPDATE 回去的（不动 rowid/n_total），不带这个键界面永远挂着空段位。"""
        st = FakeStorage(rows=[_row(1, 'a')], backfill_pending=2)
        p = ui_state.probe(st)
        self.assertEqual(p.backfill, 2)
        st.over['backfill_pending'] = 0
        self.assertNotEqual(p.key(), ui_state.probe(st).key())

    def test_probe_equality_by_key(self):
        a = ui_state.Probe(1, 2, 3)
        b = ui_state.Probe(1, 2, 3)
        self.assertEqual(a, b)
        b.unmarked = 4
        self.assertNotEqual(a, b)

    def test_boot_anchor_failure_is_zero(self):
        self.assertEqual(ui_state.boot_anchor(FakeStorage(fail=True)), 0)


class PendingTest(unittest.TestCase):
    """★ 审查 P1-3 的四条判据。"""

    def test_backlog_does_not_trigger(self):
        """① 历史积压（rowid <= 锚点）**不触发**。"""
        old = [_row(10, 'old1'), _row(11, 'old2')]
        st = FakeStorage(unmarked=old)
        self.assertIsNone(ui_state.pending_match(st, boot_rowid=20))

    def test_new_match_triggers(self):
        """② 启动后入库的新局触发。"""
        st = FakeStorage(unmarked=[_row(10, 'old'), _row(21, 'new')])
        row = ui_state.pending_match(st, boot_rowid=20)
        self.assertIsNotNone(row)
        self.assertEqual(row['match_id'], 'new')

    def test_picks_newest(self):
        st = FakeStorage(unmarked=[_row(21, 'a'), _row(25, 'b'), _row(22, 'c')])
        self.assertEqual(ui_state.pending_match(st, 20)['match_id'], 'b')

    def test_snoozed_skipped(self):
        """③ 「稍后」过的不再弹（下一新局照弹）。"""
        st = FakeStorage(unmarked=[_row(25, 'skipme'), _row(22, 'showme')])
        row = ui_state.pending_match(st, 20, snoozed=['skipme'])
        self.assertEqual(row['match_id'], 'showme')

    def test_all_snoozed_means_no_popup(self):
        st = FakeStorage(unmarked=[_row(25, 'a'), _row(22, 'b')])
        self.assertIsNone(ui_state.pending_match(st, 20, snoozed=['a', 'b']))

    def test_event_matches_are_filtered_upstream(self):
        """④ 活动局不在口径内 —— unmarked_since 交给 storage 保证，这里只验契约。"""
        st = FakeStorage(unmarked=[_row(21, 'ranked', game_mode=3)])
        self.assertEqual(ui_state.pending_match(st, 20)['match_id'], 'ranked')
        st2 = FakeStorage(unmarked=[])            # 活动局被 storage 过滤掉
        self.assertIsNone(ui_state.pending_match(st2, 20))

    def test_never_raises(self):
        self.assertIsNone(ui_state.pending_match(FakeStorage(fail=True), 0))

    def test_next_anchor_moves_forward(self):
        st = FakeStorage(**{'max_rowid': 42})
        self.assertEqual(ui_state.next_anchor(st), 42)
        self.assertEqual(ui_state.next_anchor(FakeStorage(fail=True), 7), 7)


class SnapshotTest(unittest.TestCase):

    def test_idle_when_nothing_new(self):
        st = FakeStorage(unmarked=[_row(10, 'old')], **{'max_rowid': 10})
        snap = ui_state.snapshot(st)
        self.assertEqual(snap.mode, ui_state.MODE_IDLE)
        self.assertIsNone(snap.pending)
        self.assertFalse(snap.is_pending)

    def test_pending_when_new_unmarked(self):
        st = FakeStorage(unmarked=[_row(10, 'old'), _row(21, 'new')],
                         **{'max_rowid': 21})
        snap = ui_state.snapshot(st, boot_rowid=20)
        self.assertEqual(snap.mode, ui_state.MODE_PENDING)
        self.assertTrue(snap.is_pending)
        self.assertEqual(snap.pending['match_id'], 'new')

    def test_snapshot_uses_duel_scope(self):
        """★ 2026-09-16：总览/先后攻统计走**界面口径**（天梯+巅峰+活动）。

        实机症状：打骰子拉力时两块全是 0（活动局被主口径滤掉了）。
        """
        st = FakeStorage(unmarked=[_row(10, 'old')], **{'max_rowid': 10})
        ui_state.snapshot(st)
        self.assertEqual(st.last_scope, 'duel')

    def test_expired_session_asks_for_empty_window(self):
        """★ 本轮超时（隔 ≥60 分钟没打）-> 拿**不可能命中的起点**去查。

        用户 2026-09-16 晚定的形态是「**新一轮还没开打**」：胜负 `0-0`、
        先攻/后攻 `0`、比率 `-` —— 这些零值由 `get_stats` 统一产
        （真实形状的端到端断言在 `tests/test_stats_scope.py`，那里是真库）。

        ⚠️ 这里守的是 `ui_state` 的契约：**绝不能**把超时当成 `since=None`
        （那是「不限时间」= **全库**数字冒充本轮，实机踩到）。
        """
        st = FakeStorage(unmarked=[_row(10, 'old')], **{'max_rowid': 10,
                                                        'session_start': None})
        ui_state.snapshot(st)
        self.assertEqual(st.last_since, ui_state.NO_SESSION_SINCE)
        self.assertIsNotNone(st.last_since)              # 不是「不限时间」

    def test_no_session_since_is_a_future_instant(self):
        """那个起点必须是**真的查不到东西**的未来时刻（不是在库里碰巧没有）。"""
        self.assertGreater(ui_state.NO_SESSION_SINCE, '9999-01-01 00:00:00')

    def test_live_session_keeps_stats(self):
        """本轮还活着 -> 照常按本轮起点统计（对照上一条，防改过头）。"""
        st = FakeStorage(unmarked=[_row(10, 'old')], **{'max_rowid': 10})
        snap = ui_state.snapshot(st)
        self.assertNotEqual(snap.stats, {})
        self.assertEqual(st.last_since, '2026-09-13 19:00:00')

    def test_recent_survives_expired_session(self):
        """「最近对局」**不随本轮清空**（它本来就叫「最近对局」，跨轮照显示）。"""
        rows = [_row(1, 'm1', started_at='2026-09-16 16:16:02')]
        st = FakeStorage(rows=rows, **{'max_rowid': 1, 'session_start': None})
        snap = ui_state.snapshot(st)
        self.assertEqual(len(snap.recent), 1)            # 照显示
        self.assertEqual(st.last_since, ui_state.NO_SESSION_SINCE)  # 只见统计被换窗口

    def test_probe_key_follows_session(self):
        """★ 探针必须把「本轮起点」纳入 key —— 否则窗口开着跨过 60 分钟不刷新。"""
        alive = ui_state.Probe(1, 2, 3, session='2026-09-16 16:16:02')
        expired = ui_state.Probe(1, 2, 3, session=None)
        self.assertNotEqual(alive, expired)          # 前三个字段全同也要能区分
        self.assertNotEqual(alive.key(), expired.key())
        self.assertEqual(alive, ui_state.Probe(1, 2, 3, session='2026-09-16 16:16:02'))

    def test_probe_session_from_storage(self):
        st = FakeStorage(**{'max_rowid': 1, 'session_start': None})
        self.assertIsNone(ui_state.probe(st).session)
        st2 = FakeStorage(**{'max_rowid': 1})
        self.assertEqual(ui_state.probe(st2).session, '2026-09-13 19:00:00')

    def test_probe_survives_missing_session_start(self):
        """老桩类没有 session_start() -> 探针降级成「不判轮次」，不许整条失败。"""
        class _Bare(object):
            """只有探针起码要用的三个方法（模拟老测试桩）。"""

            def count(self):
                return 1

            def max_rowid(self):
                return 1

            def get_stats(self, since=None, until=None, mode_group=None,
                          mode_scope=None):
                return {'unmarked': 0}

        p = ui_state.probe(_Bare())
        self.assertIsNotNone(p)
        self.assertIsNone(p.session)                 # 降级：不判轮次

    def test_snapshot_fills_sections(self):
        st = FakeStorage()
        snap = ui_state.snapshot(st)
        self.assertEqual(snap.by_deck, [('闪刀姬', {'n_total': 1})])
        self.assertIn('2026-09-13', snap.heatmap)
        self.assertEqual(snap.unmarked_count, 0)
        self.assertIsNotNone(snap.probe)

    def test_snapshot_survives_broken_storage(self):
        snap = ui_state.snapshot(FakeStorage(fail=True))
        self.assertEqual(snap.mode, ui_state.MODE_IDLE)
        self.assertEqual(snap.by_deck, [])
        self.assertIsNone(snap.probe)

    def test_snoozed_flows_through(self):
        st = FakeStorage(unmarked=[_row(21, 'a')], **{'max_rowid': 21})
        self.assertEqual(ui_state.snapshot(st, boot_rowid=20).mode,
                         ui_state.MODE_PENDING)
        self.assertEqual(
            ui_state.snapshot(st, boot_rowid=20, snoozed=['a']).mode,
            ui_state.MODE_IDLE)


class DiffTest(unittest.TestCase):

    def test_prev_none_means_all_changed(self):
        cur = ui_state.snapshot(FakeStorage())
        self.assertEqual(ui_state.diff(None, cur), list(ui_state.SECTIONS))

    def test_nothing_changed(self):
        a = ui_state.snapshot(FakeStorage())
        b = ui_state.snapshot(FakeStorage())
        self.assertEqual(ui_state.diff(a, b), [])

    def test_only_mode_changed(self):
        st = FakeStorage(unmarked=[_row(21, 'x')], **{'max_rowid': 21})
        a = ui_state.snapshot(st, boot_rowid=20, snoozed=['x'])   # idle
        b = ui_state.snapshot(st, boot_rowid=20)                  # pending
        self.assertEqual(ui_state.diff(a, b), ['mode', 'pending'])


class HeatmapTest(unittest.TestCase):

    def test_months_of(self):
        daily = {'2026-09-02': {'n': 1}, '2026-08-30': {'n': 2}}
        self.assertEqual(ui_state.months_of(daily), ['2026-08', '2026-09'])
        self.assertEqual(ui_state.months_of(None), [])

    def test_matrix_shape(self):
        """2026-09 有 30 天，9/1 是周二 -> 第一周前 1 格留空。"""
        daily = {'2026-09-01': {'n': 3, 'w': 2, 'l': 1, 'd': 0}}
        m = ui_state.month_matrix(daily, '2026-09')
        self.assertTrue(m)
        self.assertEqual(len(m[0]), 7)
        self.assertIsNone(m[0][0])                  # 周一为空
        self.assertEqual(m[0][1]['day'], 1)
        self.assertEqual(m[0][1]['w'], 2)
        # 全月格数 = 偏移 + 天数 + 尾部补空
        self.assertEqual(sum(1 for w in m for c in w if c), 30)

    def test_missing_day_is_zero_cell(self):
        m = ui_state.month_matrix({}, '2026-09')
        self.assertEqual(m[0][1], {'n': 0, 'w': 0, 'l': 0, 'd': 0,
                                   'day': 1, 'date': '2026-09-01'})

    def test_bad_month_returns_empty(self):
        """只有月份串非法才返回空 —— ``daily=None`` 是「这月没对局」，要给整月零格。"""
        self.assertEqual(ui_state.month_matrix({'2026-09-01': {}}, ''), [])
        self.assertEqual(ui_state.month_matrix(None, ''), [])
        self.assertEqual(ui_state.month_matrix(None, '2026'), [])

    def test_no_data_month_still_draws_grid(self):
        m = ui_state.month_matrix(None, '2026-09')
        self.assertTrue(m)
        self.assertEqual(sum(1 for w in m for c in w if c), 30)
        self.assertEqual(m[0][1]['n'], 0)


class NoGuiDependencyTest(unittest.TestCase):
    """★ S0 的核心纪律：**数据层不许引任何 GUI 框架**。

    框架（PySide6 / PyQt / tkinter）定了也不许在这里 import —— 一旦引了，
    这一层就再也单测不动，M7「能单测的逻辑全放 core/」的设计就废了。
    """

    BANNED = ('PySide6', 'PyQt5', 'PyQt6', 'tkinter', 'wx')

    def _imports(self, module):
        import io
        with io.open(module.__file__, encoding='utf-8') as f:
            src = f.read()
        out = []
        for line in src.split('\n'):
            s = line.strip()
            if s.startswith('import ') or s.startswith('from '):
                out.append(s)
        return out

    def test_ui_state_has_no_gui_import(self):
        for line in self._imports(ui_state):
            for bad in self.BANNED:
                self.assertNotIn(bad, line,
                                 'ui_state.py 引了 GUI 框架：%s' % line)

    def test_ui_actions_has_no_gui_import(self):
        import core.ui_actions as ui_actions
        for line in self._imports(ui_actions):
            for bad in self.BANNED:
                self.assertNotIn(bad, line,
                                 'ui_actions.py 引了 GUI 框架：%s' % line)

    def test_modules_importable_without_gui(self):
        """能 import 进来本身就证明没有 GUI 硬依赖。"""
        import core.ui_actions as ui_actions
        self.assertTrue(hasattr(ui_state, 'snapshot'))
        self.assertTrue(hasattr(ui_actions, 'mark'))



class S6StatsWindowDataTest(unittest.TestCase):
    """S6 统计窗的数据层（★ 口径同源 —— 审查意见 P1-2 / 计划 §3.3 那个坑的回归锁）。"""

    #: 四组（含两组 0 场，形状照真库）
    GRP = [('ranked', '排名决斗', {'n_total': 33}),
           ('peak', '巅峰决斗', {'n_total': 0}),
           ('dc', 'DC杯', {'n_total': 0}),
           ('event', '活动决斗', {'n_total': 20})]

    def _st(self, **over):
        return FakeStorage(**over)

    # ---------------------------------------------------------- 口径
    def test_interface_stats_all_uses_duel_scope(self):
        """★「全部」必须走 `mode_scope='duel'`（界面口径），**不是** `mode_group='all'`。

        `'all'` = 「不加模式条件」→ 会把 `game_mode IS NULL` 的局也算进来
        （真库实测 73 场 vs 界面口径 53 场）。这一步错了，整窗数字全错但**不报错**。
        """
        st = self._st()
        ui_state.interface_stats(st)
        self.assertEqual(st.last_scope, 'duel')
        st2 = self._st()
        ui_state.interface_stats(st2, 'event')
        # ★ 2026-09-18 七轮：interface_stats 不再拆两支 —— scope 恒传 'duel'，
        # `_stats_where` 里 mode_group 优先于 scope（给了分组就按分组算），语义不变。
        self.assertEqual(st2.last_scope, 'duel')
        self.assertEqual(st2.over.get('get_stats', {'n_total': 0})['n_total'],
                         len(st2._rows))

    def test_merged_daily_sums_all_groups(self):
        """「按月」= 逐组取 by-day 再合并（与四组表构造同源）。"""
        st = self._st(groups=self.GRP,
                      daily_ranked={'2026-09-02': {'n': 3, 'w': 2, 'l': 1, 'd': 0},
                                    '2026-09-05': {'n': 2, 'w': 1, 'l': 1, 'd': 0}},
                      daily_event={'2026-09-02': {'n': 1, 'w': 0, 'l': 1, 'd': 0},
                                   '2026-09-07': {'n': 4, 'w': 3, 'l': 1, 'd': 0}})
        daily = ui_state.merged_daily(st)
        self.assertEqual(daily['2026-09-02'], {'n': 4, 'w': 2, 'l': 2, 'd': 0})
        self.assertEqual(daily['2026-09-05'], {'n': 2, 'w': 1, 'l': 1, 'd': 0})
        self.assertEqual(sum(v['n'] for v in daily.values()), 10)

    def test_merged_daily_single_group_is_one_query(self):
        st = self._st(daily_event={'2026-09-07': {'n': 4, 'w': 3, 'l': 1, 'd': 0}})
        self.assertEqual(ui_state.merged_daily(st, 'event'),
                         {'2026-09-07': {'n': 4, 'w': 3, 'l': 1, 'd': 0}})

    def test_merged_deck_stats_merges_same_deck_across_groups(self):
        """同一副卡组在天梯 + 活动都打过 → 必须**合成一行**（否则「按卡组」是碎的）。"""
        st = self._st(groups=self.GRP,
                      by_deck_ranked=[('闪刀姬', {'n_total': 3, 'n_win': 2, 'n_lose': 1,
                                                  'first_n': 2, 'first_wins': 2,
                                                  'second_n': 1, 'second_wins': 0,
                                                  'first_at': '2026-09-02 10:00:00',
                                                  'last_at': '2026-09-02 11:00:00'})],
                      by_deck_event=[('闪刀姬', {'n_total': 1, 'n_win': 0, 'n_lose': 1,
                                                 'first_n': 1, 'first_wins': 0,
                                                 'second_n': 0, 'second_wins': 0,
                                                 'first_at': '2026-09-01 09:00:00',
                                                 'last_at': '2026-09-07 09:00:00'})])
        rows = ui_state.merged_deck_stats(st)
        self.assertEqual(len(rows), 1)
        name, s = rows[0]
        self.assertEqual(name, '闪刀姬')
        self.assertEqual(s['n_total'], 4)
        self.assertEqual((s['n_win'], s['n_lose']), (2, 2))
        self.assertAlmostEqual(s['win_rate'], 0.5)           # 比率**重算**，不是相加
        self.assertAlmostEqual(s['first_rate'], 2 / 3)
        self.assertEqual(s['second_rate'], 0.0)
        self.assertEqual(s['first_at'], '2026-09-01 09:00:00')   # min
        self.assertEqual(s['last_at'], '2026-09-07 09:00:00')    # max

    def test_merged_deck_stats_sorted_by_count_desc(self):
        st = self._st(groups=self.GRP,
                      by_deck_ranked=[('A', {'n_total': 1, 'first_at': '2026-09-01'}),
                                      ('B', {'n_total': 5, 'first_at': '2026-09-02'})])
        self.assertEqual([n for n, _s in ui_state.merged_deck_stats(st)], ['B', 'A'])

    # ---------------------------------------------------------- 伪条目过滤
    def test_cross_hides_pseudo_by_default(self):
        st = self._st(cross=[('K9vs', '未知卡组', 16, 12, 4, 0),
                             ('青眼白龙', '(未标注)', 3, 1, 2, 0),
                             ('闪刀姬', '烙印', 2, 0, 2, 0)])
        got = ui_state.stats_window_data(st)['cross']
        self.assertEqual([r[1] for r in got], ['烙印'])

    def test_cross_show_pseudo_keeps_them(self):
        st = self._st(cross=[('K9vs', '未知卡组', 16, 12, 4, 0),
                             ('青眼白龙', '(未标注)', 3, 1, 2, 0),
                             ('闪刀姬', '烙印', 2, 0, 2, 0)])
        got = ui_state.stats_window_data(st, show_pseudo=True)['cross']
        self.assertEqual(len(got), 3)

    def test_mix_axis_label_is_not_filtered(self):
        """「混轴卡组」照常显示（用户 2026-09-17：该条目弃用 ≠ 隐藏存量）。"""
        st = self._st(cross=[('闪刀姬', '混轴卡组', 2, 2, 0, 0)])
        self.assertEqual(len(ui_state.stats_window_data(st)['cross']), 1)

    # ---------------------------------------------------------- 形状与容错
    def test_shape_keys_and_group_order(self):
        st = self._st(groups=self.GRP)
        d = ui_state.stats_window_data(st)
        for key in ('mode_group', 'overview', 'groups', 'daily', 'months',
                    'cross', 'decks', 'cross_scope'):
            self.assertIn(key, d)
        self.assertEqual([g[0] for g in d['groups']],
                         ['ranked', 'peak', 'dc', 'event'])
        self.assertEqual(d['cross_scope'], ui_state.CROSS_SCOPE_NOTE)

    def test_months_come_from_daily(self):
        st = self._st(groups=self.GRP,
                      daily_ranked={'2026-08-31': {'n': 1, 'w': 1, 'l': 0, 'd': 0},
                                    '2026-09-02': {'n': 1, 'w': 1, 'l': 0, 'd': 0}})
        d = ui_state.stats_window_data(st)
        self.assertEqual(d['months'], ['2026-08', '2026-09'])

    def test_failure_returns_empty_shape_not_raise(self):
        """★ 数据层炸了也必须给**完整键的空结构**（界面照常画，只是各块「暂无数据」）。"""
        st = FakeStorage(fail=True)
        d = ui_state.stats_window_data(st)
        self.assertEqual(d['overview'], {})
        self.assertEqual(d['groups'], [])
        self.assertEqual(d['daily'], {})
        self.assertEqual(d['months'], [])
        self.assertEqual(d['cross'], [])
        self.assertEqual(d['decks'], [])


class MatchGroupsRoundTest(unittest.TestCase):
    """★ 用户 2026-09-18 口径：**对局记录 = 本月数据 + 按轮次分组**。

    「按轮」是**分组方式**，不是「只显示本轮」：
    同一副卡组跨轮要分成两组、**两组都显示**（今天 10 把闪刀姬 + 下一轮 2 把 = 两组）。
    """

    ROWS = [
        {'match_id': 'aug', 'started_at': '2026-08-20 10:00:00', 'mode': 'pvp',
         'game_mode': 3, 'ended_at': '2026-08-20 10:10:00', 'result': 'Win',
         'my_deck_name': '闪刀姬', 'went_first': True},
        {'match_id': 'r1', 'started_at': '2026-09-14 20:10:00', 'mode': 'pvp',
         'game_mode': 3, 'ended_at': '2026-09-14 20:20:00', 'result': 'Win',
         'my_deck_name': '闪刀姬', 'went_first': True},
        {'match_id': 'r2', 'started_at': '2026-09-14 20:40:00', 'mode': 'pvp',
         'game_mode': 3, 'ended_at': '2026-09-14 20:50:00', 'result': 'Lose',
         'my_deck_name': '闪刀姬', 'went_first': False},
        {'match_id': 'r3', 'started_at': '2026-09-16 22:44:00', 'mode': 'pvp',
         'game_mode': 3, 'ended_at': '2026-09-16 22:50:00', 'result': 'Win',
         'my_deck_name': '闪刀姬', 'went_first': True},
        {'match_id': 'r4', 'started_at': '2026-09-16 23:10:00', 'mode': 'pvp',
         'game_mode': 3, 'ended_at': '2026-09-16 23:20:00', 'result': 'Win',
         'my_deck_name': 'K9vs', 'went_first': True},
    ]
    WIN = ('2026-09-01 00:00:00', '2026-09-30 23:59:59')

    def _groups(self, current_start='2026-09-16 22:44:00'):
        st = FakeStorage(recent=self.ROWS)
        return ui_state.match_groups(st, since=self.WIN[0], until=self.WIN[1],
                                     current_start=current_start)

    def test_same_deck_in_two_rounds_becomes_two_groups(self):
        groups = self._groups()
        self.assertEqual([n for n, _s, _r in groups], ['K9vs', '闪刀姬', '闪刀姬'])
        labels = [s['round_label'] for _n, s, _r in groups]
        #: 上一轮的标签用**该轮第一局的开局时刻**（这里是 20:10，不是整点）
        self.assertEqual(labels, ['本轮', '本轮', '09-14 20:10 起'])
        self.assertEqual([s['is_current'] for _n, s, _r in groups],
                         [True, True, False])
        self.assertEqual([s['n_total'] for _n, s, _r in groups], [1, 1, 2])

    def test_out_of_month_rows_excluded(self):
        """8 月那局不进任何组（数据范围 = 本月）。"""
        ids = [r['match_id'] for _n, _s, rows in self._groups() for r in rows]
        self.assertNotIn('aug', ids)
        self.assertEqual(len(ids), 4)

    def test_rows_desc_inside_group(self):
        groups = self._groups()
        oldest = [g for g in groups if g[1]['round_label'].startswith('09-14')][0]
        self.assertEqual([r['match_id'] for r in oldest[2]], ['r2', 'r1'])

    def test_rounds_split_by_sixty_minute_gap(self):
        """59 分钟算同一轮、61 分钟算新一轮（边界）。"""
        def rows(gap_min):
            base = [{'match_id': 'a', 'started_at': '2026-09-16 10:00:00',
                     'mode': 'pvp', 'game_mode': 3,
                     'ended_at': '2026-09-16 10:10:00', 'result': 'Win',
                     'my_deck_name': '甲', 'went_first': True}]
            h, m = 10 + (10 + gap_min) // 60, (10 + gap_min) % 60
            base.append({'match_id': 'b', 'started_at': '2026-09-16 %02d:%02d:00' % (h, m),
                         'mode': 'pvp', 'game_mode': 3, 'result': 'Win',
                         'my_deck_name': '甲', 'went_first': True})
            return base
        # 10:10 结束 → 11:09 开局 = 59 分钟 => 同一轮（一组）
        g = ui_state.match_groups(FakeStorage(recent=rows(59)))
        self.assertEqual(len(g), 1)
        # 11:11 开局 = 61 分钟 => 新一轮（两组，同卡组）
        g2 = ui_state.match_groups(FakeStorage(recent=rows(61)))
        self.assertEqual(len(g2), 2)



class PageRowRankScopeTest(unittest.TestCase):
    """★ 对局记录的一行（`_page_row`）—— **段位只在天梯局给**。

    用户 2026-09-18：「对局记录应该和悬浮窗的最近对局的显示相同，
    **为什么你的最近对局在活动局也显示了段位？**」

    实测背景：活动局（`game_mode=21`）在库里 14/21 行带着 `my_rank_after` ——
    那是别处补回来的值，拿它当"这局的段位"就是错的。判据必须与
    `_recent_row`（悬浮窗那条路）**同源**：都走 `core.modes.group()`。
    """

    ROW = {
        'match_id': 'm1', 'started_at': '2026-09-16 23:45:00',
        'result': 'Win', 'went_first': True, 'opp_player_name': 'player1',
        'total_turns': 3, 'my_deck_name': '闪刀姬', 'opp_deck_name': '烙印',
        'my_rank_before': 5, 'my_rate_before': 2,
        'my_rank_after': 5, 'my_rate_after': 1,
    }

    def _row(self, **kw):
        d = dict(self.ROW)
        d.update(kw)
        return d

    def test_ranked_keeps_rank(self):
        """天梯局照旧：段位 + 小段位罗马数字 + 升降段（+ 完整名进 label）。"""
        r = ui_state._page_row(self._row(game_mode=3))
        self.assertEqual(r['mode_group'], 'ranked')
        self.assertEqual(r['mode_label'], '排名决斗')
        self.assertEqual(r['rank_label_after'], 'Platinum 1')
        self.assertEqual(r['rank_delta'], 'up')
        self.assertEqual(r['badge_numeral_after'], 'I')
        self.assertTrue(r['rank_icon_after'])

    def test_other_modes_drop_rank(self):
        """★ 巅峰 / DC / 活动局**整行不留段位**（界面那一格必须是空的）。"""
        for gm, group, label in ((19, 'peak', '巅峰决斗'),
                                 (12, 'dc', 'DC杯'),
                                 (21, 'event', '活动决斗')):
            r = ui_state._page_row(self._row(game_mode=gm))
            self.assertEqual(r['mode_group'], group)
            self.assertEqual(r['mode_label'], label)
            self.assertIsNone(r['rank_before'], gm)
            self.assertIsNone(r['rank_after'], gm)
            self.assertIsNone(r['rank_delta'], gm)
            self.assertIsNone(r['rank_icon_after'], gm)
            self.assertEqual(r['rank_label_before'], '', gm)
            self.assertEqual(r['rank_label_after'], '', gm)
            self.assertEqual(r['badge_numeral'], '', gm)
            self.assertEqual(r['badge_numeral_after'], '', gm)

    def test_unknown_mode_is_blank(self):
        """模式判不出的行（历史遗留）：段位空、模式名空 —— **不猜**。"""
        r = ui_state._page_row(self._row(game_mode=None))
        self.assertIsNone(r['mode_group'])
        self.assertEqual(r['mode_label'], '')
        self.assertIsNone(r['rank_after'])

    def test_match_groups_rows_follow_same_rule(self):
        """集成：`match_groups()` 产出的行也守同一条（不只是单元层成立）。"""
        rows = [self._row(match_id='a', game_mode=3),
                self._row(match_id='b', game_mode=21, result='Lose',
                          started_at='2026-09-16 23:50:00')]
        st = FakeStorage(rows=rows)
        groups = ui_state.match_groups(st, since='2026-09-01 00:00:00')
        got = {r['match_id']: r for _n, _s, rs in groups for r in rs}
        self.assertEqual(sorted(got), ['a', 'b'])
        self.assertTrue(got['a']['rank_after'])
        self.assertIsNone(got['b']['rank_after'])
        self.assertEqual(got['b']['mode_label'], '活动决斗')


class ModeFilterTest(unittest.TestCase):
    """★ 战绩总览的模式筛选（用户 2026-09-18 七轮：参考 Untapped 的模式下拉）。

    对局记录必须跟筛选走，且**天梯组含掉线兜底局**（SQL 端 `ranked` 组含它，
    Python 端 `modes.group(None)` 是 None —— 不补就"总览 33、记录 32"对不上）。
    """

    def _row(self, **kw):
        d = {'match_id': 'm', 'started_at': '2026-09-16 23:45:00',
             'result': 'Win', 'went_first': True, 'opp_player_name': 'X',
             'total_turns': 3, 'my_deck_name': '闪刀姬', 'opp_deck_name': '烙印',
             'my_rank_before': 5, 'my_rate_before': 2,
             'my_rank_after': 5, 'my_rate_after': 1}
        d.update(kw)
        return d

    def test_match_groups_mode_filter(self):
        rows = [self._row(match_id='a', game_mode=3),
                self._row(match_id='b', game_mode=21, result='Lose',
                          started_at='2026-09-16 23:50:00'),
                self._row(match_id='c', game_mode=None, result='Lose',
                          started_at='2026-09-16 23:55:00',
                          mode='pvp', ended_at=None)]
        st = FakeStorage(rows=rows)
        every = ui_state.match_groups(st, since='2026-09-01 00:00:00')
        self.assertEqual({r['match_id'] for _n, _s, rs in every for r in rs},
                         {'a', 'b', 'c'})
        ranked = ui_state.match_groups(st, since='2026-09-01 00:00:00',
                                       mode_group='ranked')
        self.assertEqual({r['match_id'] for _n, _s, rs in ranked for r in rs},
                         {'a', 'c'}, 'ranked 组要含掉线兜底局')
        event = ui_state.match_groups(st, since='2026-09-01 00:00:00',
                                      mode_group='event')
        self.assertEqual({r['match_id'] for _n, _s, rs in event for r in rs},
                         {'b'})

    def test_stats_page_data_carries_mode_group(self):
        st = FakeStorage(rows=[self._row(match_id='a', game_mode=3)])
        d = ui_state.stats_page_data(st, mode_group='ranked', month='2026-09')
        self.assertEqual(d['mode_group'], 'ranked')
        self.assertIsNotNone(d['best_rank'])
        d2 = ui_state.stats_page_data(st, mode_group='event', month='2026-09')
        self.assertIsNone(d2['best_rank'],
                          '筛到非天梯模式时没有真段位，最高段位该是空')


class RankPendingTest(unittest.TestCase):
    """★ 段位「获取中」窗口（用户 2026-09-18 晚：结果先出、段位晚 ~90s ——
    让时差可见，而不是看起来像丢了）。"""

    def test_window_and_modes(self):
        import datetime
        fmt = '%Y-%m-%d %H:%M:%S'
        now = datetime.datetime.now()
        near = (now - datetime.timedelta(seconds=30)).strftime(fmt)
        old = (now - datetime.timedelta(hours=2)).strftime(fmt)
        self.assertTrue(ui_state.rank_pending(near, 3))
        self.assertFalse(ui_state.rank_pending(near, 21), '活动局没有段位探测')
        self.assertFalse(ui_state.rank_pending(near, None))
        self.assertFalse(ui_state.rank_pending(old, 3), '窗口外=真没有')
        self.assertFalse(ui_state.rank_pending(None, 3))
        self.assertFalse(ui_state.rank_pending('garbage', 3))

    def test_rows_carry_rank_pending(self):
        st = FakeStorage(rows=[{'match_id': 'm', 'started_at': '2026-09-18 21:00:00',
                                'ended_at': '2026-09-18 21:01:00', 'mode': 'pvp',
                                'game_mode': 3, 'result': 'Win',
                                'my_deck_name': 'K9vs'}])
        rec = ui_state.build_recent(st, limit=1)
        self.assertIn('rank_pending', rec[0])


class StatsScopeTest(unittest.TestCase):
    """★ 两套口径**同页共存**（用户 2026-09-18）：

    * 「战绩总览」= **本月**（`month_window()` → since 月初 / until 月末）；
    * 「对局记录」= **本轮**（`stats_since('session')`，不统计上一轮）。
    """

    ROWS = [
        {'match_id': 'aug', 'started_at': '2026-08-20 10:00:00', 'mode': 'pvp',
         'game_mode': 3, 'result': 'Win', 'my_deck_name': '甲', 'went_first': True},
        {'match_id': 'sep_a', 'started_at': '2026-09-10 10:00:00', 'mode': 'pvp',
         'game_mode': 3, 'result': 'Win', 'my_deck_name': '甲', 'went_first': True},
        {'match_id': 'sep_b', 'started_at': '2026-09-16 20:00:00', 'mode': 'pvp',
         'game_mode': 3, 'result': 'Lose', 'my_deck_name': '乙', 'went_first': False},
    ]

    def test_month_window(self):
        self.assertEqual(ui_state.month_window('2026-09-18'),
                         ('2026-09', '2026-09-01 00:00:00', '2026-09-30 23:59:59'))
        self.assertEqual(ui_state.month_window('2026-12-31')[2],
                         '2026-12-31 23:59:59')
        self.assertEqual(ui_state.month_window('2024-02-10')[2],
                         '2024-02-29 23:59:59')          # 闰年 2 月

    def test_overview_asks_for_the_month_window(self):
        st = FakeStorage(recent=self.ROWS)
        st.over['session_start'] = '2026-09-16 00:00:00'
        ui_state.stats_page_data(st, month='2026-09')   # 钉月，不依赖当天日期
        self.assertTrue(st.last_since.startswith('2026-09-01'), st.last_since)
        self.assertTrue(st.last_until.startswith('2026-09-30'), st.last_until)

    def test_records_are_month_scoped(self):
        """对局记录的数据范围 = **本月**（8 月那条不算），但仍**按轮分组**。"""
        st = FakeStorage(recent=self.ROWS)
        st.over['session_start'] = '2026-09-16 00:00:00'
        d = ui_state.stats_page_data(st, month='2026-09')     # 钉月，不依赖当天日期
        ids = [r['match_id'] for _n, _s, rows in d['groups'] for r in rows]
        self.assertEqual(sorted(ids), ['sep_a', 'sep_b'])     # 8 月那条不算
        self.assertEqual(d['row_total'], 2)

    def test_stats_page_data_carries_account(self):
        """多账号（2026-10-03）：``account`` 贯穿到取数 + accounts 清单随行。"""
        st = FakeStorage(recent=[
            dict(self.ROWS[1], account_name='示例玩家'),
            dict(self.ROWS[2], account_name='示例玩家2')])
        st.over['account_options'] = [('示例玩家', 1, ''),
                                      ('示例玩家2', 1, '')]
        d = ui_state.stats_page_data(st, account='示例玩家',
                                     month='2026-09')   # 行都是 9 月的，钉月
        self.assertEqual(d['account'], '示例玩家')
        self.assertEqual(st.last_account, '示例玩家')
        ids = [r['match_id'] for _n, _s, rows in d['groups'] for r in rows]
        self.assertEqual(ids, ['sep_a'])          # 只剩该账号的局
        self.assertEqual([a[0] for a in d['accounts']],
                         ['示例玩家', '示例玩家2'])
        # 未传 = 全部（含未分账号的历史局）
        d2 = ui_state.stats_page_data(st)
        self.assertIsNone(d2['account'])
        self.assertIsNone(st.last_account)

    def test_snapshot_follows_account(self):
        """悬浮窗快照跟账号（2026-10-03）：stats/by_deck/heatmap/recent 全筛。

        ⚠️ 行造在 9 月但 snapshot 不钉月 —— 它的口径是「本轮/今日」，
        这里只验「account 传到了取数、recent 只剩该账号的局」。
        """
        st = FakeStorage(recent=[
            dict(self.ROWS[1], account_name='示例玩家'),
            dict(self.ROWS[2], account_name='示例玩家2')])
        st.over['session_start'] = '2026-09-16 00:00:00'
        snap = ui_state.snapshot(st, recent_limit=10, account='示例玩家')
        #（⚠️ 不断言 last_account：快照后段的 current_rank 等也会再查
        #   get_recent_matches（不带 account），把它覆盖掉了）
        names = [r.get('account_name') for r in snap.recent]
        self.assertNotIn('示例玩家2', names)

    def test_demo_shape_has_month_keys(self):
        from core import ui_demo
        d = ui_demo.demo_stats_page()
        for key in ('month', 'month_since', 'session_since', 'session_live'):
            self.assertIn(key, d)


class StatsMonthSwitchTest(unittest.TestCase):
    """2026-10-01 用户：「点热力图切换月份查看」—— 数据层 ``month`` 参数。

    切月只动总览 / 最高段位 / 对局记录三块；热力图（daily/months）仍画全部历史。
    """

    ROWS = [
        {'match_id': 'aug', 'started_at': '2026-08-20 10:00:00', 'mode': 'pvp',
         'game_mode': 3, 'result': 'Win', 'my_deck_name': '甲', 'went_first': True},
        {'match_id': 'sep', 'started_at': '2026-09-10 10:00:00', 'mode': 'pvp',
         'game_mode': 3, 'result': 'Lose', 'my_deck_name': '乙', 'went_first': False},
    ]

    def test_view_a_past_month(self):
        st = FakeStorage(recent=self.ROWS)
        st.over['session_start'] = '2026-09-16 00:00:00'
        d = ui_state.stats_page_data(st, month='2026-08')
        self.assertEqual(d['month'], '2026-08')
        ids = [r['match_id'] for _n, _s, rows in d['groups'] for r in rows]
        self.assertEqual(ids, ['aug'])                     # 只有 8 月那局
        self.assertTrue(st.last_since.startswith('2026-08-01'), st.last_since)
        self.assertTrue(st.last_until.startswith('2026-08-31'), st.last_until)

    def test_none_means_current_month(self):
        st = FakeStorage(recent=self.ROWS)
        d = ui_state.stats_page_data(st)
        self.assertEqual(d['month'], ui_state.month_window()[0])

    def test_bad_month_falls_back_to_current(self):
        st = FakeStorage(recent=self.ROWS)
        d = ui_state.stats_page_data(st, month='garbage')
        self.assertEqual(d['month'], ui_state.month_window()[0])

    def test_daily_still_all_history(self):
        st = FakeStorage(recent=self.ROWS)
        d = ui_state.stats_page_data(st, month='2026-08')
        self.assertIn('daily', d)
        self.assertIn('months', d)


class MatchGroupsTest(unittest.TestCase):
    """D1 数据层：`match_groups` / `stats_page_data`（★ 口径必须与组头一致）。"""

    def _st(self, **over):
        return FakeStorage(**over)

    def _rows(self):
        base = {'started_at': '2026-09-16 23:45:00', 'mode': 'pvp',
                'game_mode': 3, 'ended_at': '2026-09-16 23:50:00',
                'my_deck_name': '闪刀姬', 'result': 'Win', 'went_first': True,
                'opp_player_name': 'player1', 'total_turns': 2,
                'opp_deck_name': '烙印', 'my_rank_before': 5, 'my_rate_before': 3,
                'my_rank_after': 5, 'my_rate_after': 2}
        a = dict(base, match_id='m1')
        b = dict(base, match_id='m2', result='Lose', went_first=False)
        # ③ 结果为空（掉线在结算前）= 不算
        c = dict(base, match_id='m3', result=None)
        # ④ 模式判不出、也不是掉线兜底（观战/单人）= 不算
        d = dict(base, match_id='m4', mode='solo', game_mode=None)
        # ⑤ 掉线兜底那一段（game_mode NULL + pvp + 没有 ended_at）= **要算**
        e = dict(base, match_id='m5', game_mode=None, ended_at=None,
                 result='Lose')
        # ⑥ 我方卡组读不出 → 归 '(未识别)'
        f = dict(base, match_id='m6', my_deck_name='')
        return [a, b, c, d, e, f]

    def test_split_datetime(self):
        self.assertEqual(ui_state.split_datetime('2026-09-16 23:45:00'),
                         ('09-16', '23:45'))
        self.assertEqual(ui_state.split_datetime(''), ('', ''))
        self.assertEqual(ui_state.split_datetime(None), ('', ''))

    def test_rows_filtered_by_interface_calibre(self):
        st = self._st(recent=self._rows(),
                      by_deck_ranked=[('闪刀姬', {'n_total': 3})])
        groups = ui_state.match_groups(st)
        ids = [r['match_id'] for _n, _s, rows in groups for r in rows]
        self.assertIn('m1', ids)
        self.assertIn('m5', ids)          # 掉线兜底那条要收
        self.assertNotIn('m3', ids)       # result 空
        self.assertNotIn('m4', ids)       # 观战/单人

    def test_row_total_equals_group_total(self):
        """★ 组头（`merged_deck_stats`）与逐局行**必须对得上** —— 这是本页最容易漂的一条。"""
        st = self._st(recent=self._rows(),
                      by_deck_ranked=[('闪刀姬', {'n_total': 3}),
                                      ('(未识别)', {'n_total': 1})])
        d = ui_state.stats_page_data(st)
        self.assertEqual(d['row_total'], d['group_total'])

    def test_unknown_my_deck_goes_to_sentinel(self):
        st = self._st(recent=self._rows(),
                      by_deck_ranked=[('(未识别)', {'n_total': 1})])
        groups = ui_state.match_groups(st)
        names = [n for n, _s, _r in groups]
        self.assertIn(ui_state.MY_DECK_UNKNOWN, names)

    def test_best_rank_uses_max(self):
        st = self._st(recent=self._rows(), by_deck_ranked=[])
        d = ui_state.stats_page_data(st, month='2026-09')    # 钉月（行都是 9 月的）
        self.assertEqual(d['best_rank'], (5, 2))     # 5-2 比 5-3 高（小段小者高）

    def test_failure_returns_empty_shape(self):
        d = ui_state.stats_page_data(FakeStorage(fail=True))
        for key in ('overview', 'best_rank', 'daily', 'months', 'groups'):
            self.assertIn(key, d)
        self.assertEqual(d['groups'], [])

class SoloExclusionTest(unittest.TestCase):
    """单人模式（game_mode=9 / Duel Trial 15）不进悬浮窗、不进行口径（09-26 实报）。"""

    def test_build_recent_skips_solo(self):
        """连打几局单人，别把真正的最近对局挤出去。"""
        solo = [_row(i, 's%d' % i, game_mode=9,
                     started_at='2026-09-26 13:2%d:00' % i) for i in (7, 8)]
        real = [_row(9, 'm9', game_mode=3,
                     started_at='2026-09-26 13:21:00')]
        rows = list(reversed(solo + real))          # DESC：新的在前
        st = FakeStorage(rows=rows, **{'recent': rows})
        out = ui_state.build_recent(st, limit=2)
        self.assertEqual([r['match_id'] for r in out], ['m9'])

    def test_in_duel_calibre_excludes_pve(self):
        self.assertFalse(ui_state._in_duel_calibre({'game_mode': 9}))
        self.assertTrue(ui_state._in_duel_calibre({'game_mode': 15}))   # Duel Trial 算活动
        self.assertTrue(ui_state._in_duel_calibre({'game_mode': 21}))
        self.assertTrue(ui_state._in_duel_calibre({'game_mode': 3}))




class ReferenceCacheInvalidationTest(unittest.TestCase):
    """★ 参照库刷新后必须读到**新快照**（2026-10-10 真 bug 回归锁）。

    用**真参照库文件**做往返（改文件 → 清缓存 → 读），比桩更接近真实发布场景。
    """

    def _real_ref(self):
        from core import deck_guess
        return deck_guess.DEFAULT_REFERENCE

    def test_clear_makes_the_new_file_visible(self):
        import json
        import shutil
        import tempfile
        from core.storage import Storage
        from core.ui_state import clear_reference_cache, deck_composition

        path = self._real_ref()
        if not os.path.exists(path):
            self.skipTest('参照库不在（干净克隆？）—— 跳过')
        bak = tempfile.mktemp(suffix='.json')
        shutil.copy(path, bak)
        try:
            st = Storage()
            before = deck_composition(st, 'Sky Striker')
            self.assertTrue(before.get('has_ref'), '前提：刷新前应能取到构成')
            n_before = len(before.get('main') or [])

            #: 把参照库改成只剩 3 副 —— 模拟「刷新后内容变了」
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            data['decks'] = list(data['decks'].items())[:3]
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)

            #: 不清缓存 -> 仍旧数据（这正是原来的 bug，所以它必须是前提）
            self.assertEqual(len(deck_composition(st, 'Sky Striker')
                                 .get('main') or []), n_before)

            #: 清了 -> 读到新快照
            clear_reference_cache()
            fresh = deck_composition(st, 'Sky Striker')
            self.assertNotEqual(len(fresh.get('main') or []), n_before,
                                '清缓存后仍返回旧卡表 —— 参照库刷新还是不生效')
        finally:
            shutil.copy(bak, path)
            os.remove(bak)
            clear_reference_cache()

    def test_clear_is_idempotent(self):
        """UI 侧无条件会调一次；重复调 / 没读过就调都不许抛。"""
        from core.ui_state import clear_reference_cache
        clear_reference_cache()
        clear_reference_cache()

    def test_ui_entry_clears_deck_guess_cache(self):
        """UI 侧入口必须**真的**清掉 `deck_guess` 的缓存（不是自己另开一份）。"""
        from core import deck_guess
        from core.ui_state import clear_reference_cache
        deck_guess._DATA_CACHE['probe'] = {'decks': {}}
        clear_reference_cache()
        self.assertEqual(deck_guess._DATA_CACHE, {})

if __name__ == '__main__':
    unittest.main()


class CrossMatrixTest(unittest.TestCase):
    """D2 `cross_matrix`：透视 / 伪条目两轴隐藏 / 均胜率加权 / 页签语义 / 零值形态。

    ★ v4 方向（2026-09-19 用户拍板）：**列 = 我方卡组（少的做列）、行 = 对手卡组**；
    每列汇总（我方卡组的综合胜率）在 ``col_avgs``，UI 放列头下第一行。
    """

    #: 综合 = 4 对局格：K9vs×烙印 4 场 3 胜、K9vs×珠泪 1 场 0 胜、闪刀姬×烙印 2 场 1 胜
    #: + 镜像 闪刀姬×闪刀姬 1 场 0 胜；两条伪条目（隐藏后消失）
    CROSS = [
        ('K9vs', '未知卡组', 8, 6, 2, 0),        # 伪条目（对手轴 -> 行隐藏）
        ('(未识别)', '烙印', 2, 2, 0, 0),        # 伪条目（我方轴 -> 列隐藏）
        ('K9vs', '烙印', 4, 3, 1, 0),
        ('K9vs', '珠泪', 1, 0, 1, 0),
        ('闪刀姬', '烙印', 2, 1, 1, 0),
        ('闪刀姬', '闪刀姬', 1, 0, 1, 0),        # 镜像局 = 真实战绩
    ]
    #: 先攻页签切片（同格子的先攻部分）
    CROSS_FIRST = [
        ('K9vs', '烙印', 2, 2, 0, 0),
        ('K9vs', '珠泪', 1, 0, 1, 0),
        ('闪刀姬', '烙印', 1, 0, 1, 0),
        ('闪刀姬', '闪刀姬', 1, 0, 1, 0),
    ]
    CROSS_SECOND = [
        ('K9vs', '烙印', 2, 1, 1, 0),
        ('闪刀姬', '烙印', 1, 1, 0, 0),
    ]

    def test_shape_and_orientation(self):
        """**列 = 我方卡组、行 = 对手卡组**；行列按总场次降序；镜像当普通格。"""
        d = ui_state.cross_matrix(FakeStorage(cross=self.CROSS))
        self.assertEqual(sorted(d), sorted(['note', 'went_first', 'month',
                                            'mode_group', 'cols', 'rows',
                                            'col_avgs', 'months',
                                            'total_calibre',
                                            'total_in_matrix']))
        self.assertEqual(d['cols'], ['K9vs', '闪刀姬'])          # 列 = 我方
        self.assertEqual([r['deck'] for r in d['rows']],
                         ['未知卡组', '烙印', '珠泪', '闪刀姬'])  # 行 = 对手（未知卡组 09-28 起显示）
        unknown = d['rows'][0]                   # 未知卡组行（09-28 起显示）
        self.assertEqual([(c['deck'], c['n'], c['w'], c['rate'])
                          for c in unknown['cells']],
                         [('K9vs', 8, 6, 75.0)])
        brand = d['rows'][1]                                     # 烙印行
        self.assertEqual([(c['deck'], c['n'], c['w'], c['rate'])
                          for c in brand['cells']],
                         [('K9vs', 4, 3, 75.0), ('闪刀姬', 2, 1, 50.0)])
        mirror = [c for c in d['rows'][3]['cells'] if c['deck'] == '闪刀姬']
        self.assertEqual(len(mirror), 1)          # 镜像局照常在矩阵里（行序：未知卡组后）
        self.assertEqual(d['rows'][1]['n'], 6)    # 行场数（烙印 4+2，v4b 场数列）

    def test_pseudo_hidden_both_axes_and_pruned(self):
        """09-28 用户口径：**未知卡组要显示**（巅峰局看不了对面）；
        仍隐藏的只剩我方「(未识别)」与对手「(未标注)」。"""
        d = ui_state.cross_matrix(FakeStorage(cross=self.CROSS))
        self.assertEqual(d['total_calibre'], 18)
        self.assertEqual(d['total_in_matrix'], 16)  # 18 - 2(未识别)
        self.assertIn('未知卡组', [r['deck'] for r in d['rows']])
        self.assertNotIn('(未识别)', d['cols'])
        self.assertIn('主口径 18 场 · 矩阵内 16 场，已隐藏未标注', d['note'])

    def test_col_avg_is_weighted_not_simple_mean(self):
        """列汇总（我方综合胜率）= 场次加权 Σw/Σn（4 场 75% + 1 场 0% → 60%）。"""
        d = ui_state.cross_matrix(FakeStorage(cross=self.CROSS))
        self.assertEqual(d['col_avgs'][0],
                         {'deck': 'K9vs', 'n': 13, 'w': 9,
                          'rate': 9 / 13 * 100.0})   # 含未知卡组行（09-28）
        self.assertEqual(d['col_avgs'][1]['deck'], '闪刀姬')
        self.assertEqual((d['col_avgs'][1]['n'], d['col_avgs'][1]['w']),
                         (3, 1))
        self.assertAlmostEqual(d['col_avgs'][1]['rate'], 100 / 3)

    def test_tabs_share_fixed_axis(self):
        """先攻/后攻复用综合的行列轴（P2-1）：轴不变、只换格内数据。"""
        base = ui_state.cross_matrix(FakeStorage(cross=self.CROSS))
        st = FakeStorage(cross=self.CROSS, cross_first=self.CROSS_FIRST,
                         cross_second=self.CROSS_SECOND)
        for wf in (True, False):
            d = ui_state.cross_matrix(FakeStorage(
                cross=self.CROSS, cross_first=self.CROSS_FIRST,
                cross_second=self.CROSS_SECOND), went_first=wf)
            self.assertEqual(d['cols'], base['cols'])
            self.assertEqual([r['deck'] for r in d['rows']],
                             [r['deck'] for r in base['rows']])
        first = ui_state.cross_matrix(st, went_first=True)
        self.assertEqual([(a['deck'], a['n'], a['w'])
                          for a in first['col_avgs']],
                         [('K9vs', 3, 2), ('闪刀姬', 2, 0)])
        self.assertAlmostEqual(first['col_avgs'][0]['rate'], 200 / 3)
        self.assertEqual(first['col_avgs'][1]['rate'], 0.0)
        self.assertEqual(st.last_went_first, True)   # 参数真传到了 storage

    def test_month_and_mode_filters_reach_storage(self):
        """v5 筛选器：月份 -> since/until、模式 -> mode_group，都真传到 storage。"""
        st = FakeStorage(cross=self.CROSS)
        d = ui_state.cross_matrix(st, month='2026-09', mode_group='ranked')
        self.assertEqual(d['month'], '2026-09')
        self.assertEqual(d['mode_group'], 'ranked')
        self.assertEqual(st.last_cross_since, '2026-09-01 00:00:00')
        self.assertEqual(st.last_cross_until, '2026-09-30 23:59:59')
        self.assertEqual(st.last_mode_group, 'ranked')

    def test_fail_returns_zero_shape(self):
        """库炸了不抛：零值形态、note 只剩口径文案。"""
        d = ui_state.cross_matrix(FakeStorage(fail=True))
        self.assertEqual(d['rows'], [])
        self.assertEqual(d['cols'], [])
        self.assertEqual(d['col_avgs'], [])
        self.assertIn('天梯 + 巅峰', d['note'])


class TierPageDataTest(_LangCnMixin, unittest.TestCase):
    """D3 `tier_page_data`：分组 / 中文名优先 / 脏 tier 不显示 / 零值形态。"""

    META = [
        {'name': 'Dracotail', 'name_cn': '烙印星辰', 'name_display': None,
         'tier': 'Tier1', 'power': 16.5},
        {'name': 'Kewl Tune Engine', 'name_cn': '杀手旋律混轴',
         'name_display': None, 'tier': 'Tier2', 'power': 14.5,
         'kind': 'engine', 'hidden': 1},     # hidden engine = 要进 Tier 表
        {'name': 'Darklord', 'name_cn': '堕天使', 'name_display': None,
         'tier': 'Trending', 'power': 1.0, 'kind': 'deck_type',
         'hidden': 1},                        # hidden 的普通条目 = 照旧不进
        {'name': 'Branded Elfnote', 'name_cn': '烙印耀圣',
         'name_display': None, 'tier': 'Tier2', 'power': None,
         'kind': 'deck_type', 'hidden': 0},   # 无 power = 一律落 TRENDING（v6）
        {'name': 'Branded', 'name_cn': None, 'name_display': 'Branded',
         'tier': 'Tier2', 'power': 8.5},
        {'name': 'Maliss', 'name_cn': None, 'name_display': None,
         'tier': 'Trending', 'power': 1.0},
        {'name': 'BadTier', 'name_cn': None, 'name_display': None,
         'tier': 'Tier99', 'power': 9.9},          # 脏 tier -> 不显示
    ]

    def test_sections_and_display_name(self):
        """四节齐全；显示名 = 中文名 -> 显示英文名 -> 站内名；脏 tier 剪掉。"""
        d = ui_state.tier_page_data(FakeStorage(meta_decks=self.META))
        self.assertEqual([s['tier'] for s in d['sections']],
                         ['Tier1', 'Tier2', 'Tier3', 'Trending'])
        self.assertEqual(d['sections'][0]['decks'],
                         [{'name': 'Dracotail', 'display': '烙印星辰',
                           'power': 16.5}])
        self.assertEqual([x['display'] for x in d['sections'][1]['decks']],
                         ['杀手旋律混轴', 'Branded'])   # hidden engine 进、且排最前
        # ★ 无 power 的卡组移到 TRENDING（tier 字段陈旧不作数，v6 用户拍板）
        self.assertNotIn('烙印耀圣', [x['display']
                                      for x in d['sections'][1]['decks']])
        self.assertIn('烙印耀圣', [x['display']
                                   for x in d['sections'][3]['decks']])
        self.assertNotIn('堕天使', [x['display']
                                    for x in d['sections'][3]['decks']])
        self.assertIn('Maliss', [x['display']
                                  for x in d['sections'][3]['decks']])
        self.assertEqual(d['deck_total'], 5)       # 脏 tier / hidden deck_type 不计

    def test_fail_returns_zero_shape(self):
        """库炸了不抛：四节照在、decks 全空。"""
        d = ui_state.tier_page_data(FakeStorage(fail=True))
        self.assertEqual(len(d['sections']), 4)
        self.assertTrue(all(not s['decks'] for s in d['sections']))
        self.assertEqual(d['deck_total'], 0)


class BanlistPageDataTest(_LangCnMixin, unittest.TestCase):
    """D3 `banlist_page_data`：状态走**站内 banStatus**（v2）/ 变更史 / 零值形态。

    ⚠️ 2026-09-21 换数据源：旧算法「按变更史累计」漏得出奇（用户实报简易结合：
    变更史停在 2023「限制→禁止」，站内实际是限制）—— 现在当前状态一律来自
    `banlist_current_status()`（新表 `banlist_current`），表空才回落旧算法。
    """

    STATUS = (
        ('Snake-Eye Ash', 'Forbidden'),
        ('Rescue-ACE Air Lifter', 'Limited 1'),
        ('Kashtira Fenrir', 'Limited 2'),
        ('Instant Fusion', 'Limited 1'),
    )
    CHANGES = (
        {'card_en': 'Snake-Eye Ash', 'from_status': 'Limited 1',
         'to_status': 'Forbidden', 'effective': '2026-09-03T07:30:00.000Z'},
        {'card_en': 'Old Card', 'from_status': 'Forbidden',
         'to_status': None, 'effective': '2026-06-01T07:30:00.000Z'},
    )

    def test_current_from_ban_status(self):
        """★ 当前状态来自 `banlist_current_status`；中文三词不带数字。"""
        d = ui_state.banlist_page_data(
            FakeStorage(banlist_status=self.STATUS,
                        banlist_changes=self.CHANGES))
        self.assertEqual(d['current_total'], 4)
        self.assertEqual(d['counts'], {'禁止': 1, '限制': 2, '准限制': 1})
        # 数据层**保持 storage 返回的原序**（真 storage 按 禁止→限制→准限制 排好；
        # 桩按构造顺序）—— 不在这里重排，排序责任在 storage（单测在 test_banlist）
        self.assertEqual([(x['name_en'], x['status']) for x in d['current']],
                         [('Snake-Eye Ash', '禁止'),
                          ('Rescue-ACE Air Lifter', '限制'),
                          ('Kashtira Fenrir', '准限制'),
                          ('Instant Fusion', '限制')])
        self.assertEqual(d['last_effective'], '2026-09-03T07:30:00.000Z')

    def test_recent_changes_include_unban(self):
        """变更史：`解除限制` 也显示（from -> to 两端都给，信息别吞）。"""
        d = ui_state.banlist_page_data(
            FakeStorage(banlist_status=self.STATUS,
                        banlist_changes=self.CHANGES))
        self.assertEqual(len(d['recent']), 2)
        self.assertEqual(d['recent'][1]['to'], '解除限制')

    def test_empty_status_table_falls_back(self):
        """新表空（老库）-> 回落变更史累计，别给空表。"""
        st = FakeStorage(
            banlist_current=[
                ('Old Card', 'Forbidden', '2026-06-01T07:30:00.000Z')],
            banlist_changes=self.CHANGES)
        d = ui_state.banlist_page_data(st)
        self.assertEqual(d['current_total'], 1)
        self.assertEqual(d['current'][0]['name_en'], 'Old Card')
        self.assertEqual(d['current'][0]['status'], '禁止')

    def test_fail_returns_zero_shape(self):
        d = ui_state.banlist_page_data(FakeStorage(fail=True))
        self.assertEqual(d['current'], [])
        self.assertEqual(d['recent'], [])
        self.assertEqual(d['current_total'], 0)
        self.assertEqual(d['counts'], {})


# ===================================================== 卡组构成 + 卡组页（D3 剩余）

#: 参照库 demo（形状 = `data/mdm_deck_cards.json` 的 decks 段；
#: 每张卡是不是额外由打桩的 `ygocdb()` 决定，见 `DeckCompositionTest`）
REF_DECKS = {
    'Sky Striker': {'tier': 'Tier3', 'name_cn': '闪刀姬', 'total': 21,
                    'avgMainSize': 40, 'source_name': 'Sky Striker',
                    'cards': {'12950': {'per': 100, 'at': 3},
                              '13670': {'per': 100, 'at': 3},
                              '17034': {'per': 86, 'at': 2},
                              '21600': {'per': 100, 'at': 1},
                              '19011': {'per': 95, 'at': 1},
                              '888888881': {'per': 62, 'at': 1},
                              '888888882': {'per': 20, 'at': 1}}},
    'Branded': {'tier': 'Tier2', 'name_cn': '烙印', 'total': 22,
                'avgMainSize': 40, 'source_name': 'Branded',
                'cards': {'15994': {'per': 100, 'at': 2}}},
}


class _RefStorage(object):
    """卡组构成 / 卡组页用的桩：显示名 + 模糊搜索 + 我方卡组聚合。

    ``groups`` 给了就按 mode_group 切片（`merged_deck_stats` 会逐组调）；
    不给则每组都返回同一份 ``stats``。
    """

    def __init__(self, stats=None, fail=False, groups=None):
        self._stats = list(stats or [])
        self._fail = fail
        self._groups = dict(groups or {})
        self.deck_calls = []          # (mode_group,) 观察点 —— 锁「界面口径」

    def meta_deck_display_map(self, include_hidden=True):
        return {name: d.get('name_cn') or name for name, d in REF_DECKS.items()}

    def search_meta_decks(self, query, limit=20, include_hidden=False):
        q = (query or '').strip()
        for name, d in REF_DECKS.items():
            if q and q in (name, d.get('name_cn') or ''):
                return [{'name': name, 'name_display': None,
                         'name_cn': d.get('name_cn')}]
        return []

    def get_stats_by_deck(self, since=None, until=None, mode_group=None,
                          account=None):
        if self._fail:
            raise RuntimeError('db is locked')
        self.deck_calls.append(mode_group)
        if self._groups:
            return list(self._groups.get(mode_group, []))
        return list(self._stats)


def _typed_ygocdb():
    """打桩的 ygocdb：12950/13670/17034 = 主卡，21600/19011/15994 = 额外。"""
    doc = {}
    for cid in ('12950', '13670', '17034', '999999991'):
        doc[cid] = {'data': {'type': 0x21}}
    for cid in ('21600', '19011', '15994'):
        doc[cid] = {'data': {'type': 0x4000000 | 0x21}}
    return doc


@unittest.skipUnless(hasattr(ui_state, 'deck_composition'), '还没实现')
class DeckCompositionTest(_LangCnMixin, unittest.TestCase):
    """D3 `deck_composition`：主/额外分组 + 排序 + 同名去重 + unknown 计数。"""

    def setUp(self):
        super().setUp()
        self._patch = mock.patch.object(ui_state_cn, 'ygocdb',
                                        return_value=_typed_ygocdb())
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _comp(self, name='Sky Striker', **kw):
        return ui_state.deck_composition(
            _RefStorage(), name, reference={'decks': REF_DECKS}, **kw)

    def test_main_extra_split_and_sort(self):
        """位掩码分组；`per` 降序（截图口径）。"""
        d = self._comp()
        self.assertEqual([r['id'] for r in d['main']], [12950, 13670, 17034])
        self.assertEqual([r['id'] for r in d['extra']], [21600, 19011])
        self.assertTrue(all('per' in r and 'at' in r and 'art' in r
                            for r in d['main'] + d['extra']))

    def test_same_name_dedup_keeps_higher_per(self):
        """同名（异画）只留 per 更高的那条 —— 与打分器 build_index 同口径。

        两张卡得**同名**才谈得上去重（显示名 = `card_names.display` 的产物），
        所以这里把 `display` 也一起打桩成同名。
        """
        ref = {'decks': {'X': {'tier': 'Tier3', 'total': 5,
                               'cards': {'12950': {'per': 10, 'at': 2},
                                         '17034': {'per': 80, 'at': 3}}}}}
        with mock.patch.object(ui_state_cn, 'display',
                               side_effect=lambda cid, lang='cn':
                                   '同名卡' if int(cid) in (12950, 17034) else None):
            d = ui_state.deck_composition(_RefStorage(), 'X', reference=ref)
        self.assertEqual(len(d['main']), 1)
        self.assertEqual(d['main'][0]['per'], 80)

    def test_unknown_counted_not_shown(self):
        """卡表查不到类型的**只计数、不显示**（不猜主卡还是额外）。"""
        d = self._comp()
        self.assertEqual(d['unknown'], 2)
        self.assertNotIn(888888881, [r['id'] for r in d['main'] + d['extra']])

    def test_per_min_filter(self):
        d = self._comp(per_min=90)
        self.assertEqual([r['id'] for r in d['main']], [12950, 13670])
        self.assertEqual([r['id'] for r in d['extra']], [21600, 19011])

    def test_missing_deck_gives_zero_shape(self):
        d = self._comp('No Such Deck')
        self.assertFalse(d['has_ref'])
        self.assertEqual(d['main'], [])
        self.assertEqual(d['extra'], [])
        self.assertEqual(d['total'], 0)
        self.assertTrue(d['options'])          # 下拉照常有参照库全量

    def test_alias_resolved_via_storage_search(self):
        """中文名也能进（走 `search_meta_decks` 落到站内英文名）。"""
        d = self._comp('闪刀姬')
        self.assertEqual(d['name'], 'Sky Striker')
        self.assertEqual(d['display'], '闪刀姬')
        self.assertEqual(d['tier'], 'Tier3')

    def test_display_prefers_cover_table(self):
        """显示名 = 覆盖表口径（`meta_deck_display_map`）优先于参照库的 name_cn。"""
        st = _RefStorage()
        # _display_map 走 list_meta_decks（覆盖表行按语言取名），桩也走这条路
        st.list_meta_decks = lambda **kw: [
            {'name': 'Sky Striker', 'name_cn': '闪刀', 'name_display': None,
             'name_ja': None}]
        d = ui_state.deck_composition(st, 'Sky Striker',
                                      reference={'decks': REF_DECKS})
        self.assertEqual(d['display'], '闪刀')

    def test_card_table_boom_gives_zero_shape(self):
        """卡表炸了不抛：全进 unknown，主/额外空。"""
        with mock.patch.object(ui_state_cn, 'ygocdb',
                               side_effect=RuntimeError('boom')):
            d = self._comp()
        self.assertTrue(d['has_ref'], '参照库没炸，炸的是卡表 —— 空态只该发生在参照库缺')
        self.assertEqual(d['main'], [])
        self.assertEqual(d['extra'], [])
        self.assertEqual(d['unknown'], 7)


class DataStatusTest(_LangCnMixin, unittest.TestCase):
    """D3 §3.1 `data_status` / `data_status_line` —— 设置区页头那行状态。

    用户难处：「表已最新时点按钮看不出变化」→ 页头常驻这一行，**不跑也能核对**。
    """

    class _St(object):
        def __init__(self, changes=None, current=None, matches=None,
                     boom=False):
            self._changes = list(changes or [])
            self._current = list(current or [])
            self._matches = list(matches or [])
            self._boom = boom

        def list_banlist_changes(self, limit=None):
            if self._boom:
                raise RuntimeError('db locked')
            return list(self._changes)

        def banlist_current_status(self):
            if self._boom:
                raise RuntimeError('db locked')
            return list(self._current)

        def count(self):
            if self._boom:
                raise RuntimeError('db locked')
            return len(self._matches)

        def list_matches(self, limit=None):
            if self._boom:
                raise RuntimeError('db locked')
            return list(self._matches)

    def test_keys_are_locked(self):
        """键集合锁死（页面按这三段渲染）。"""
        d = ui_state.data_status(self._St())
        self.assertEqual(set(d), {'card_table', 'banlist', 'matches'})
        self.assertEqual(set(d['card_table']),
                         {'count', 'generated_at', 'path'})
        self.assertEqual(set(d['banlist']),
                         {'changes', 'current', 'last_effective'})
        self.assertEqual(set(d['matches']), {'count', 'last_ended'})

    def test_banlist_and_matches_from_storage(self):
        st = self._St(
            changes=[{'effective': '2026-09-03', 'card_en': 'A'},
                     {'effective': '2026-08-01', 'card_en': 'B'}],
            current=[('A', 'Forbidden'), ('B', 'Limited 1')],
            matches=[{'started_at': '2026-09-20T10:00:00',
                      'ended_at': '2026-09-20T10:20:00'},
                     {'started_at': '2026-09-21T12:30:00',
                      'ended_at': '2026-09-21T12:34:00'}])
        d = ui_state.data_status(st)
        self.assertEqual(d['banlist']['changes'], 2)
        self.assertEqual(d['banlist']['current'], 2)
        self.assertEqual(d['banlist']['last_effective'], '2026-09-03')
        self.assertEqual(d['matches']['count'], 2)
        self.assertEqual(d['matches']['last_ended'], '2026-09-21 12:34')

    def test_last_ended_falls_back_to_started(self):
        """没有 `ended_at`（掉线局 / API 补录）就用开工时刻，别留空。"""
        st = self._St(matches=[{'started_at': '2026-09-21T09:05:00'}])
        self.assertEqual(ui_state.data_status(st)['matches']['last_ended'],
                         '2026-09-21 09:05')

    def test_storage_boom_does_not_raise(self):
        """★ 数据层炸了**不抛**（全项目纪律）—— 各段给 None，其余照常。"""
        d = ui_state.data_status(self._St(boom=True))
        self.assertIsNone(d['banlist']['changes'])
        self.assertIsNone(d['banlist']['current'])
        self.assertIsNone(d['matches']['count'])
        self.assertIsNone(d['matches']['last_ended'])

    def test_no_storage_is_safe(self):
        d = ui_state.data_status(None)
        self.assertIsNone(d['banlist']['changes'])
        self.assertIsNone(d['matches']['count'])

    def test_card_table_reads_real_file_keys(self):
        """卡表段读**真文件**的顶层 `generated_at` / `counts.total`。"""
        d = ui_state.data_status(None)
        ct = d['card_table']
        self.assertTrue(ct['path'].endswith('card_names.json'))
        if ct['count'] is not None:            # 干净克隆上文件可能不在
            self.assertIsInstance(ct['count'], int)
            self.assertGreater(ct['count'], 0)

    # ---- 渲染成一行 ----
    def test_line_has_all_three_parts(self):
        line = ui_state.data_status_line({
            'card_table': {'count': 14615, 'generated_at': '2026-09-19T21:50:48'},
            'banlist': {'current': 207, 'last_effective': '2026-09-03'},
            'matches': {'count': 35, 'last_ended': '2026-09-21 12:34'}})
        self.assertIn('卡表 14615 张（2026-09-19 更新）', line)
        self.assertIn('禁限 207 张（最近 2026-09-03）', line)
        self.assertIn('对局 35 场（最近 2026-09-21 12:34）', line)

    def test_line_placeholder_when_empty(self):
        """缺项写「—」/「无记录」/「还没打过」—— **绝不吞掉整行**。"""
        line = ui_state.data_status_line(ui_state.data_status(None))
        self.assertIn('卡表', line)
        self.assertIn('禁限', line)
        self.assertIn('对局', line)
        self.assertIn('—', line)

    def test_line_tolerates_garbage(self):
        for bad in (None, {}, 'nonsense', {'card_table': None}):
            line = ui_state.data_status_line(bad)
            self.assertTrue(line)


# ============================================================ 口径常量与色阶
# （D4 2026-09-22：从已删的 `tests/test_ui_stats_window.py` 搬来 ——
#   `ui/stats_window.py` 按 Q2 口径删掉了，但这两组锁与窗口无关，不能跟着消失。）

class PseudoConstantTest(unittest.TestCase):
    """伪条目名单 —— 名单错了界面**不会报错**，只会安静地显示错东西。"""

    def test_unknown_name_has_single_source(self):
        """常量唯一来源（S6 审查 §3.5）：选择器与后端必须是同一个值。"""
        self.assertEqual(selector.UNKNOWN_DECK_NAME, ui_actions.UNKNOWN_DECK_NAME)
        self.assertEqual(ui_actions.UNKNOWN_DECK_NAME, '未知卡组')

    def test_unmarked_label_matches_storage_sql(self):
        """★ `'(未标注)'` 的唯一真源是 SQL 里的字面量（那边没有 Python 常量）。

        不锁这条：将来 `core/storage.OPP_DECK_EXPR` 换个写法，
        「默认隐藏未标注」就会**静默失效**（不报错、只是不再隐藏）—— 审查 P2-2。
        """
        self.assertIn('(未标注)', core_storage.OPP_DECK_EXPR)
        self.assertIn('(未标注)', ui_actions.NON_DECK_LABELS)

    def test_mix_axis_is_not_hidden(self):
        """「混轴卡组」不在隐藏名单里（用户 2026-09-17：条目弃用 ≠ 藏存量）。"""
        self.assertNotIn('混轴卡组', ui_actions.NON_DECK_LABELS)

    def test_non_deck_labels_all_come_from_unknown_const(self):
        self.assertIn(ui_actions.UNKNOWN_DECK_NAME, ui_actions.NON_DECK_LABELS)


class HeatColorTest(unittest.TestCase):
    """月度热力图的色阶（纯函数，`ui/theme.py`）。"""

    def test_zero_matches_are_neutral(self):
        """★ 0 场**绝不染绿** —— 染了会被读成「那天赢过」。"""
        self.assertEqual(theme.heat_color(0), theme.HEAT_LEVELS[0])
        self.assertNotIn('63,185,80', theme.heat_color(0))

    def test_scale_and_cap(self):
        self.assertEqual(theme.heat_color(1), theme.HEAT_LEVELS[1])
        self.assertEqual(theme.heat_color(99), theme.HEAT_LEVELS[-1])
        self.assertEqual(theme.heat_color(-5), theme.HEAT_LEVELS[0])
        self.assertEqual(theme.heat_color(None), theme.HEAT_LEVELS[0])
        self.assertEqual(theme.heat_color('3'), theme.HEAT_LEVELS[3])

    def test_heat_max(self):
        self.assertEqual(theme.heat_max({'a': {'n': 2}, 'b': {'n': 5}}), 5)
        self.assertEqual(theme.heat_max({}), 0)
        self.assertEqual(theme.heat_max(None), 0)
        self.assertEqual(theme.heat_max({'a': {'n': None}}), 0)


# ===================================================== D4：采集器状态 / 导出
# （2026-09-22：D4 收尾两件 —— 采集器状态显示、导出 CSV/JSON）

class CollectorStatusTest(_LangCnMixin, unittest.TestCase):
    """`collector_status` / `collector_status_line` —— 看板标题栏那行状态。

    ⚠️ 判据 = **心跳文件的新鲜度**（不是 pid 存活、也不是日志 mtime）：
    pid 文件只在启动写、只在优雅退出删（被 kill 会留 stale），
    日志只在有事件时写（待机不写）—— 两条都不适合做判据。
    """

    def setUp(self):
        super().setUp()
        self.d = tempfile.mkdtemp(prefix='hb-')
        self.path = os.path.join(self.d, 'collector_heartbeat.json')
        self._patch = mock.patch.object(ui_state, '_PROJ_ROOT', self.d)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        import shutil
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, 'data', 'archive'), exist_ok=True)
        self.real = os.path.join(self.d, 'data', 'archive',
                                 'collector_heartbeat.json')

    def _write(self, **kw):
        import json
        with io.open(self.real, 'w', encoding='utf-8') as f:
            json.dump(kw, f)

    def test_missing_file_is_not_running(self):
        s = ui_state.collector_status()
        self.assertFalse(s['running'])
        self.assertIsNone(s['at'])
        self.assertEqual(ui_state.collector_status_line(s), '采集器：未运行')

    def test_fresh_heartbeat_is_running(self):
        now = datetime.datetime(2026, 9, 22, 10, 0, 0)
        self._write(at='2026-09-22 09:59:57', in_duel=True, game_pid=1234)
        s = ui_state.collector_status(now=now)
        self.assertTrue(s['running'])
        self.assertEqual(s['age_sec'], 3)
        self.assertTrue(s['in_duel'])
        self.assertEqual(s['game_pid'], 1234)
        self.assertIn('对局中', ui_state.collector_status_line(s))

    def test_stale_heartbeat_is_not_running(self):
        """★ 被杀掉的采集器：心跳不再更新 → 自然过期 → 显示「未运行」。

        （这正是 pid 文件做不到的 —— 它会被留下来，看着像「还在跑」。）
        """
        now = datetime.datetime(2026, 9, 22, 10, 0, 0)
        self._write(at='2026-09-22 09:50:00')
        s = ui_state.collector_status(now=now)
        self.assertFalse(s['running'])
        self.assertEqual(s['age_sec'], 600)
        self.assertIn('未运行', ui_state.collector_status_line(s))
        self.assertIn('最后心跳', ui_state.collector_status_line(s))

    def test_boundary_is_inclusive(self):
        """恰好到阈值还算「在跑」（`<=`，别写成 `<`）。"""
        now = datetime.datetime(2026, 9, 22, 10, 0, 0)
        at = (now - datetime.timedelta(seconds=ui_state.HEARTBEAT_STALE_SEC))
        self._write(at=at.strftime('%Y-%m-%d %H:%M:%S'))
        self.assertTrue(ui_state.collector_status(now=now)['running'])

    def test_idle_still_counts_as_running(self):
        """待机（游戏没开）**也算在跑** —— 采集器进程活着，只是没对局。"""
        now = datetime.datetime(2026, 9, 22, 10, 0, 0)
        self._write(at='2026-09-22 09:59:59', in_duel=False)
        s = ui_state.collector_status(now=now)
        self.assertTrue(s['running'])
        self.assertIn('待机中', ui_state.collector_status_line(s))

    def test_garbage_file_does_not_raise(self):
        with io.open(self.real, 'w', encoding='utf-8') as f:
            f.write('not json at all')
        s = ui_state.collector_status()
        self.assertFalse(s['running'])
        self.assertIsNone(s['age_sec'])

    def test_bad_timestamp_does_not_raise(self):
        self._write(at='看不懂的时间')
        s = ui_state.collector_status()
        self.assertFalse(s['running'])
        self.assertIsNone(s['age_sec'])

    def test_line_tolerates_garbage(self):
        for bad in (None, {}, 'x', {'running': True}):
            self.assertTrue(ui_state.collector_status_line(bad))


class ExportTest(unittest.TestCase):
    """`export_rows` / `write_export` —— D4「导出（CSV/JSON，日期时间已分列）」。"""

    class _St(object):
        def __init__(self, rows):
            self._rows = list(rows)

        def get_recent_matches(self, limit=100, account=None):
            return list(self._rows)

        def get_stats_by_deck(self, since=None, until=None, mode_group=None,
                          account=None):
            return []

    def _row(self, **kw):
        base = {'match_id': 'm1', 'result': 'Win', 'went_first': True,
                'started_at': '2026-09-16 23:27:00', 'ended_at':
                '2026-09-16 23:41:00', 'my_deck_name': '闪刀姬',
                'opp_player_name': '对手甲', 'opp_deck_name': '烙印',
                'game_mode': 3, 'total_turns': 5,
                'my_rank_before': 12, 'my_rate_before': 1,
                'my_rank_after': 12, 'my_rate_after': 2}
        base.update(kw)
        return base

    def test_columns_are_the_documented_ones(self):
        self.assertEqual([k for k, _l in ui_state.EXPORT_COLUMNS][:3],
                         ['date', 'time', 'result'])
        labels = [l for _k, l in ui_state.EXPORT_COLUMNS]
        self.assertIn('日期', labels)
        self.assertIn('时间', labels)

    def test_date_and_time_are_separate_columns(self):
        """★ Q16 口径：日期 / 时间**已分列**（用户要求，导出不用再拆字符串）。"""
        rows = ui_state.export_rows(self._St([self._row()]), mode_group='ranked')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '09-16')
        self.assertEqual(rows[0]['time'], '23:27')

    def test_values_are_translated_for_humans(self):
        rows = ui_state.export_rows(self._St([self._row()]), mode_group='ranked')
        self.assertEqual(rows[0]['result'], '胜')
        self.assertEqual(rows[0]['went_first'], '先攻')
        self.assertEqual(rows[0]['my_deck'], '闪刀姬')

    def test_lose_and_second(self):
        rows = ui_state.export_rows(
            self._St([self._row(result='Lose', went_first=False)]),
            mode_group='ranked')
        self.assertEqual(rows[0]['result'], '负')
        self.assertEqual(rows[0]['went_first'], '后攻')

    def test_unknown_opp_deck_marked_not_labeled(self):
        rows = ui_state.export_rows(
            self._St([self._row(opp_deck_name=None)]), mode_group='ranked')
        self.assertEqual(rows[0]['opp_known'], '否')

    def test_sorted_newest_first(self):
        rows = ui_state.export_rows(self._St([
            self._row(match_id='old', started_at='2026-09-10 10:00:00'),
            self._row(match_id='new', started_at='2026-09-16 10:00:00'),
        ]), mode_group='ranked')
        self.assertEqual([r['match_id'] for r in rows], ['new', 'old'])

    def test_storage_failure_gives_empty_not_raise(self):
        class Boom(object):
            def get_recent_matches(self, limit=100, account=None):
                raise RuntimeError('db locked')
        self.assertEqual(ui_state.export_rows(Boom()), [])

    # ---- 写盘 ----
    def test_csv_has_bom_and_crlf(self):
        """★ CSV 用 **utf-8-sig + CRLF** —— Excel 双击直接认（无 BOM 会乱码）。"""
        d = tempfile.mkdtemp(prefix='exp-')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        rows = ui_state.export_rows(self._St([self._row()]), mode_group='ranked')
        path = ui_state.write_export(os.path.join(d, 'a.csv'), rows)
        raw = open(path, 'rb').read()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'), '缺 BOM')
        self.assertIn(b'\r\n', raw, '该用 CRLF（Excel 认）')
        text = io.open(path, encoding='utf-8-sig').read()
        self.assertIn('日期', text.splitlines()[0])
        self.assertIn('闪刀姬', text)

    def test_json_keeps_chinese_readable(self):
        """★ JSON 用 `ensure_ascii=False` —— 中文照原样，别转义。"""
        import json
        d = tempfile.mkdtemp(prefix='exp-')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        rows = ui_state.export_rows(self._St([self._row()]), mode_group='ranked')
        path = ui_state.write_export(os.path.join(d, 'a.json'), rows)
        raw = open(path, 'rb').read().decode('utf-8')
        self.assertIn('闪刀姬', raw)
        payload = json.loads(raw)
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['rows'][0]['result'], '胜')
        self.assertEqual([c['label'] for c in payload['columns']][:2],
                         ['日期', '时间'])

    def test_format_inferred_from_extension(self):
        d = tempfile.mkdtemp(prefix='exp-')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        self.assertTrue(ui_state.write_export(
            os.path.join(d, 'x.json'), []).endswith('.json'))
        self.assertTrue(ui_state.write_export(
            os.path.join(d, 'x.csv'), []).endswith('.csv'))

    def test_default_name_has_timestamp_and_ext(self):
        name = ui_state.export_default_name(
            'csv', now=datetime.datetime(2026, 9, 22, 10, 30))
        self.assertEqual(name, 'duel_export_20260922_1030.csv')
        self.assertTrue(ui_state.export_default_name(
            'json', now=datetime.datetime(2026, 9, 22, 10, 30)).endswith('.json'))


    # ---- 硬币列已删（2026-10-03 用户：导出把硬币列删除）
    def test_coin_column_removed(self):
        """「硬币」列**从导出里删掉**（2026-10-03 用户口径）。

        库里 `won_initiative` 仍保留（悬浮窗先后攻还用它），只是不导出；
        「先后手」列照旧（两个字段本来就不是一个概念）。
        """
        keys = [k for k, _l in ui_state.EXPORT_COLUMNS]
        self.assertNotIn('coin', keys)
        self.assertIn('went_first', keys)
        self.assertFalse(hasattr(ui_state, 'EXPORT_COIN_CN'))
        rows = ui_state.export_rows(
            self._St([self._row(won_initiative=True, went_first=False)]),
            mode_group='ranked')
        self.assertNotIn('coin', rows[0])
        self.assertEqual(rows[0]['went_first'], '后攻')

    # ---- 按月（用户 2026-09-22：「按月和模式筛选导出」）
    def test_month_filters_rows(self):
        rows = ui_state.export_rows(
            self._St([self._row(started_at='2026-09-16 10:00:00'),
                      self._row(match_id='aug',
                                started_at='2026-08-16 10:00:00')]),
            mode_group=None, month='2026-09')
        self.assertEqual([r['match_id'] for r in rows], ['m1'])

    def test_bad_month_does_not_filter_away_everything(self):
        """月份串看不懂 → **不筛**（宁可多给，也别给空表）。"""
        rows = ui_state.export_rows(self._St([self._row()]), month='乱写的')
        self.assertEqual(len(rows), 1)

    def test_export_months_lists_all_then_real_months(self):
        ms = ui_state.export_months(self._St([]))
        self.assertEqual(ms[0], ('', '全部月份'))

    def test_csv_row_count_matches(self):
        import csv as _csv
        d = tempfile.mkdtemp(prefix='exp-')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        rows = ui_state.export_rows(self._St([self._row(), self._row(
            match_id='m2')]), mode_group='ranked')
        path = ui_state.write_export(os.path.join(d, 'a.csv'), rows)
        with io.open(path, encoding='utf-8-sig', newline='') as f:
            got = list(_csv.reader(f))
        self.assertEqual(len(got), len(rows) + 1)          # +1 表头
