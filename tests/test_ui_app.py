# -*- coding: utf-8 -*-
"""ui.app 调试模式（demo 战绩热切换 + 沙盒全流程）契约（2026-09-15）。

规矩：
* QSettings 一律 patch 成测试专属（masterduel_test/M7_test），结束 clear
  —— 绝不碰真实注册表键 `masterduel/M7`；Windows 注册表把 bool 存成
  'true'/'false' 字符串，读回一律走 `demo_from_settings()` 归一化；
* Storage 只用临时库；MasterDuelUiApp 用 ``__new__`` 裸对象
  （QApplication 全进程一份，不能整个构造 —— 同 test_ui_selector.OnPickDeckTest）；
* ⚠️ 对话框必须**持引用**（``dlg = self._dlg()``）再摸子控件 —— 内联链式
  ``SettingsDialog(...).cb_demo`` 会在表达式中间被 GC，shiboken 报
  "Internal C++ object already deleted"（2026-09-15 踩过，tmp/_dbg_settings_dlg.py 复现）；
* demo 快照来自 core.ui_demo（9 行），断言 recent 行数即可分辨数据源；
* demo 沙盒 = 标注写内存（_MARKS / 负数 opp_deck_id），真库一字节不写；
  ⚠️ 动了沙盒全局态必须 ``demo_reset_marks()`` 收尾，否则泄漏到别的测试。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import io
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

try:
    from PySide6.QtCore import QObject, QSettings, Qt, Signal
    from PySide6.QtWidgets import QApplication, QLabel
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from ui import app as app_mod                         # noqa: E402
from ui import opp_deck_manager                       # noqa: E402
from core import deck_csv as deck_csv_mod             # noqa: E402
from core import ui_demo as ui_demo_mod               # noqa: E402

TEST_QSETTINGS = dict(organization='masterduel_test', application='M7_test')

_APP = None


def _app():
    """QApplication 是进程级单例 —— 整个测试模块共用一份。"""
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class _FakeWin(object):
    """记录 set_snapshot 的假悬浮窗。"""

    def __init__(self):
        self.snapshots = []
        self.hidden = False          # 「×」→ 隐藏到托盘 的观察点

    def set_snapshot(self, snap):
        self.snapshots.append(snap)

    def refresh_deck_banner(self, img_resolver=None):
        """卡组确认条（2026-09-22）—— 假窗只记录调用。"""
        self.banner_refreshes = getattr(self, 'banner_refreshes', 0) + 1

    def hide(self):
        self.hidden = True


class _FakeTimer(object):
    """记录 start/stop 的假定时器。"""

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SettingsPatchedTest(unittest.TestCase):
    """公共基类：QSettings 指到测试专属键，结束 clear。"""

    def setUp(self):
        _app()
        self._patch = mock.patch.object(
            app_mod, 'settings',
            lambda: QSettings(TEST_QSETTINGS['organization'],
                              TEST_QSETTINGS['application']))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # ★ i18n 的 QSettings 也指到测试专属键，并重置进程内语言缓存
        #   （否则 tr() 读用户真机 ui/lang：en 机器上变英文 + 缓存毒化后续模块）
        import ui.i18n as _i18n
        self._i18n_patch = mock.patch.object(
            _i18n, '_SETTINGS_FACTORY',
            staticmethod(lambda: QSettings(TEST_QSETTINGS['organization'],
                                           TEST_QSETTINGS['application'])))
        self._i18n_patch.start()
        self.addCleanup(self._i18n_patch.stop)
        _i18n.reset_lang()
        self.addCleanup(_i18n.reset_lang)

        def _clear():
            QSettings(TEST_QSETTINGS['organization'],
                      TEST_QSETTINGS['application']).clear()
        _clear()
        self.addCleanup(_clear)
        #: ★ 2026-10-22：段位推断状态文件也必须隔离 ——
        #: `core.rank_state.save()` 默认写 **真机** `data/rank_state.json`
        #: （`STATE_PATH` 是**模块级常量**，不跟 DATA_DIR 走）。
        #: 本模块的「点结果格」用例会走 `ui_actions.repair_result` →
        #: `_infer_rank_after_manual` → `rank_state.save()` —— 不隔离的话
        #: **跑一次本文件就把用户的连胜/连败计数和段位改掉**
        #: （实锤：2026-10-21 22:53 全量跑单测，真机状态从 7-4 被写成 7-5、
        #:  `lose_streak` 一路涨到 107 —— 后面 10-22 的局因此全被判错）。
        import core.rank_state as _rank_state
        self._rank_path = _rank_state.STATE_PATH
        _rank_state.STATE_PATH = os.path.join(tempfile.mkdtemp(),
                                               'rank_state.json')
        self.addCleanup(setattr, _rank_state, 'STATE_PATH', self._rank_path)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DemoFromSettingsTest(SettingsPatchedTest):
    """demo_from_settings() —— QSettings 里 ui/demo 的读取。"""

    def test_default_is_false(self):
        self.assertFalse(app_mod.demo_from_settings())

    def test_true_and_one_count(self):
        for v in ('true', 'True', '1'):
            app_mod.settings().setValue('ui/demo', v)
            self.assertTrue(app_mod.demo_from_settings(), v)

    def test_false_and_junk_count_as_false(self):
        for v in ('false', '', 'junk'):
            app_mod.settings().setValue('ui/demo', v)
            self.assertFalse(app_mod.demo_from_settings(), v)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SetDemoModeTest(SettingsPatchedTest):
    """MasterDuelUiApp.set_demo_mode —— 热切换的状态机。"""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, 'masterduel.db')
        self.win = _FakeWin()
        self.timer = _FakeTimer()

    def _appobj(self, demo, with_storage=False):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._demo = demo
        a._db_path = self.db_path
        a.storage = app_mod.Storage(self.db_path) if with_storage else None
        a.last_key = None
        a.win = self.win
        a.timer = self.timer
        return a

    def _last_recent(self):
        return self.win.snapshots[-1].recent

    def test_leave_demo_creates_storage_starts_timer_real_snapshot(self):
        a = self._appobj(demo=True)
        a.set_demo_mode(False)
        self.assertIsInstance(a.storage, app_mod.Storage)
        self.assertEqual(self.timer.started, 1)
        self.assertEqual(len(self._last_recent()), 0)      # 空库 = 空 recent
        self.assertFalse(app_mod.demo_from_settings())

    def test_enter_demo_swaps_to_sandbox_stops_timer_demo_snapshot(self):
        a = self._appobj(demo=False, with_storage=True)
        a.set_demo_mode(True)
        # 沙盒包真库：选择器能完整走通，真库却一个字节不写（2026-09-15 起）
        self.assertIsInstance(a.storage, app_mod.DemoSandboxStorage)
        self.assertIsInstance(a.storage._real, app_mod.Storage)
        self.assertEqual(self.timer.stopped, 1)
        self.assertEqual(len(self._last_recent()), 9)      # demo 9 行全形态
        self.assertTrue(app_mod.demo_from_settings())

    def test_same_state_is_noop(self):
        a = self._appobj(demo=True)
        a.set_demo_mode(True)                              # 已是 demo，别动
        self.assertEqual(self.timer.stopped, 0)
        self.assertEqual(self.win.snapshots, [])

    def test_roundtrip(self):
        a = self._appobj(demo=False, with_storage=True)
        a.set_demo_mode(True)
        a.set_demo_mode(False)
        self.assertIsInstance(a.storage, app_mod.Storage)
        self.assertEqual(self.timer.started, 1)
        self.assertEqual(self.timer.stopped, 1)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DemoGuardTest(SettingsPatchedTest):
    """demo 模式下的守卫：tick 不探、标注进内存沙盒。"""

    def setUp(self):
        super().setUp()
        # 动过 _MARKS / _SANDBOX_OPP 全局态必须清 —— 防泄漏到别的测试
        self.addCleanup(ui_demo_mod.demo_reset_marks)

    def _appobj(self, demo, storage=None):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._demo = demo
        a._db_path = None
        a.storage = storage
        a.last_key = None
        a.win = _FakeWin()
        a.timer = _FakeTimer()
        return a

    def test_tick_is_noop_in_demo(self):
        a = self._appobj(demo=True)

        def _boom(_storage):
            raise AssertionError('demo 模式不该探真库')

        with mock.patch.object(app_mod.ui_state, 'probe', _boom):
            a.tick()                                       # 不抛 = 提前返回了

    def test_pick_deck_in_demo_full_sandbox_flow(self):
        """demo 下选择器**完整走通**（2026-09-15 沙盒版，替代旧 info-box 拦截）。

        搜 meta -> 选 -> 标注 全程：标注进内存沙盒、行上立即显示，
        真库的 opp_decks / matches 一个字节不动。
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, 'sandbox.db')
        real = app_mod.Storage(db)
        real.init_schema()
        n_decks_before = len(real.list_opp_decks())
        a = self._appobj(
            demo=True,
            storage=app_mod.DemoSandboxStorage(real))

        fake_deck = {'name': 'Maliss', 'name_cn': '码丽丝',
                     'aliases': ['Maliss']}

        class _FakePanel(QObject):
            """面板替身：不上屏，靠 `confirmed` 信号驱动（旧对话框是 exec 打桩）。"""

            confirmed = Signal(object)
            cancelled = Signal()

            def __init__(self, *args, **kw):
                super().__init__()          # parent 可能是测试桩（非 QObject），别传给 Qt
                self.parent = kw.get('parent')

            def show_near(self, anchor):
                pass

            def show(self):
                pass

        with mock.patch.object(app_mod, 'DeckSelectorPanel', _FakePanel):
            a.on_pick_deck({'match_id': 'demo0003'}, anchor=None)
            a._panel.confirmed.emit(fake_deck)          # 用户点了「确定」

        # 1) 刷新过、仍是 demo 9 行
        self.assertEqual(len(a.win.snapshots), 1)
        self.assertEqual(len(a.win.snapshots[-1].recent), 9)
        # 2) demo0003 行上立即显示标注（选择器里的显示名 = 中文名）
        row3 = [r for r in a.win.snapshots[-1].recent
                if r.get('match_id') == 'demo0003'][0]
        self.assertTrue(row3.get('opp_known'))
        self.assertEqual(row3.get('opp_deck'), '码丽丝')
        # 3) 真库 opp_decks 行数不变（upsert 进了沙盒）
        self.assertEqual(len(real.list_opp_decks()), n_decks_before)
        # 4) 沙盒视角多出负数 id 条目
        ids = [r[0] for r in a.storage.list_opp_decks()]
        self.assertTrue(any(i < 0 for i in ids))       # 沙盒负数条目在

    # ------------------------------------------------ 结果格修正（Bug 2 兜底）
    class _Act(QObject):
        triggered = Signal(bool)

    class _AutoMenu(QObject):
        """QMenu 替身：记下菜单项，`exec` 时**自动点指定那一项**（模拟用户选择）。"""

        def __init__(self, parent=None):
            super().__init__()
            self.items = []

        def addAction(self, label):
            act = DemoGuardTest._Act()
            self.items.append((label, act))
            return act

        def exec(self, pos=None):                              # noqa: A003
            for label, act in self.items:
                if label == self._auto:
                    act.triggered.emit(False)
            return None

    def _auto_menu(self, label):
        cls = self._AutoMenu

        class _Menu(cls):
            _auto = label

        return _Menu

    def test_pick_result_writes_manual(self):
        """点结果格 → 菜单选中 → 写库（`result_src='manual'`，人工优先）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = app_mod.Storage(os.path.join(tmp.name, 'a.db'))
        st.init_schema()
        st.upsert_match({'match_id': 'm1', 'started_at': '2026-09-18 23:00:00',
                         'origin': 'log', 'mode': 'pvp', 'result': None})
        a = self._appobj(demo=False, storage=st)

        with mock.patch.object(app_mod, 'QMenu', self._auto_menu('失败')):
            a.on_pick_result(st.get_match('m1'))

        row = st.get_match('m1')
        self.assertEqual(row['result'], 'Lose')
        self.assertEqual(row['result_src'], 'manual')
        self.assertEqual(len(a.win.snapshots), 1)              # 改完立刻重画

    def test_pick_result_without_match_id_is_noop(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = app_mod.Storage(os.path.join(tmp.name, 'b.db'))
        st.init_schema()
        a = self._appobj(demo=False, storage=st)
        with mock.patch.object(app_mod, 'QMenu', self._auto_menu('失败')):
            a.on_pick_result({})                               # 没有 match_id
        self.assertEqual(a.win.snapshots, [])

    def test_clear_result_writes_null(self):
        """「清除结果」= 撤销修正（result 与 src 一起置 NULL，自动解析可以再填）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = app_mod.Storage(os.path.join(tmp.name, 'c.db'))
        st.init_schema()
        st.upsert_match({'match_id': 'm1', 'started_at': '2026-09-18 23:00:00',
                         'origin': 'log', 'mode': 'pvp', 'result': 'Win'})
        a = self._appobj(demo=False, storage=st)
        with mock.patch.object(app_mod, 'QMenu', self._auto_menu('清除结果')):
            a.on_pick_result(st.get_match('m1'))
        row = st.get_match('m1')
        self.assertIsNone(row['result'])
        self.assertIsNone(row['result_src'])

    def test_attach_suggestions_accepts_list_revealed_ids(self):
        """★ 真库里 `opp_revealed_ids` **读出后是 list**（JSON 列被 load 成对象），
        而 list 不能当字典键 —— 2026-09-16 实机启动就崩在这（demo 里是字符串，
        所以单测全绿也拦不住）。缓存键必须归一化。
        """
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._sug_cache = {}
        a._guess_index = None                      # 没有参照库 -> 建议恒为 None
        a._guess_groups = None
        rows = [{'opp_known': False, 'opp_revealed_ids': [22568, 22582]},
                {'opp_known': False, 'opp_revealed_ids': '[1, 2]'},
                {'opp_known': True, 'opp_revealed_ids': None}]
        a._attach_suggestions(rows)                # 不许抛
        self.assertEqual(len(a._sug_cache), 2)     # 已标注那行不进缓存（设计如此）
        self.assertTrue(all(k == k for k in a._sug_cache))   # 键必须可哈希

    def test_guess_for_reloads_empty_index_and_clears_cache(self):
        """🔴 回归锁：**空索引要重载**（2026-10-11 实机，第一局无建议）。

        首跑播种完成前 meta_decks 表是空的，「空表 = 全过滤」让启动那一刻
        加载到的 `_guess_index` 是**空 dict**（不是 None），且它只在启动时
        加载一次 —— 首跑窗口启动的进程永远没有建议。guess_for 必须节流
        重载，重载成功后清空 `_sug_cache`（旧条目都是以「无建议」缓存的）。
        """
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._sug_cache = {(): None}                  # 旧口径留下的「无建议」缓存
        a._guess_index = {}                        # 空 dict = 触发重载（不是 None）
        a._guess_groups = None
        a._guess_reload_at = -1e9                  # 上次重载：很久以前
        a.storage = mock.Mock(**{'list_meta_decks.return_value': []})
        fake_index = {'Blue-Eyes': {'tier': 1, 'cards': {'青眼': 60}}}
        with mock.patch.object(app_mod.deck_guess, 'load_index',
                               return_value=(fake_index, {'g': 1})) as li:
            out = a.guess_for({'opp_revealed_ids': [1, 2]})
        self.assertEqual(a._guess_index, fake_index, '重载结果要替换空索引')
        self.assertEqual(a._sug_cache, {}, '重载成功必须清「无建议」缓存')
        li.assert_called_once()
        # 第二拍：索引已非空，不再触发重载
        with mock.patch.object(app_mod.deck_guess, 'load_index') as li2:
            a.guess_for({'opp_revealed_ids': [1, 2]})
            li2.assert_not_called()

    def test_guess_for_reloads_empty_index_throttled(self):
        """重载有 30s 节流：刚试过失败就别每拍都砸磁盘。"""
        import time as _time
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._sug_cache = {}
        a._guess_index = {}
        a._guess_groups = None
        a._guess_reload_at = _time.monotonic()     # 刚刚重载过 -> 节流窗口内
        with mock.patch.object(app_mod.deck_guess, 'load_index') as li:
            a.guess_for({'opp_revealed_ids': [1, 2]})
            li.assert_not_called()

    def test_guess_for_fills_display_names_from_meta(self):
        """★ 候选必须带上 meta 清单的显示字段 —— 不补的话 `deck_display()`
        回落到英文站内名：悬浮窗建议名是英文、**一键采纳落库的 `opp_deck_name`
        也是英文**（2026-09-16 实机：库里全成了 'Blue-Eyes'/'Exosister'）。
        """
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._sug_cache = {}
        a._guess_index = {'Blue-Eyes': {}}
        a._guess_groups = None
        # 打桩 suggest()：返回只有英文名的打分候选（deck_guess 的真实形态）
        fake_cand = [{'name': 'Blue-Eyes', 'hits': 3, 'revealed': 5,
                      'confidence': 0.6, 'bayes': -1.0, 'tier': 1,
                      'source_name': 'Blue-Eyes', 'in_prefill_set': True,
                      'explained': 3, 'sample': 10, 'prefill': True}]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = app_mod.Storage(os.path.join(tmp.name, 'g.db'))
        st.init_schema()
        st.replace_meta_decks([{'name': 'Blue-Eyes', 'name_cn': '青眼白龙',
                                'tier': 1}])
        try:
            with mock.patch.object(app_mod.deck_guess, 'suggest',
                                   return_value=fake_cand):
                a.storage = st
                out = a.guess_for({'opp_revealed_ids': [1, 2, 3]})
        finally:
            st.close() if hasattr(st, 'close') else None
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'Blue-Eyes')
        # ★ meta 清单里有中文名 -> 候选必须补上 -> deck_display 出中文
        self.assertTrue(out[0].get('name_cn'), '候选缺 name_cn：%r' % out[0])
        from ui.selector import deck_display
        self.assertNotEqual(deck_display(out[0], 'cn'), 'Blue-Eyes')

    def test_refresh_in_demo_uses_demo_snapshot(self):
        a = self._appobj(demo=True)
        a.refresh()
        self.assertEqual(len(a.win.snapshots[-1].recent), 9)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class CloseActionTest(SettingsPatchedTest):
    """★ 用户 2026-09-15：标题条「×」按设置关；**没设置过就先弹一次提示**（带「记住」）。"""

    def _appobj(self):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a.storage = None
        a.win = _FakeWin()
        a.quit = lambda: setattr(a.win, 'quit_called', True)
        return a

    def test_default_setting_is_tray_and_marked_unset(self):
        action, explicit = app_mod.close_action()
        self.assertEqual(action, 'tray')
        self.assertFalse(explicit, '从没设置过 = 还没明确选择（要弹提示）')

    def test_explicit_setting_is_used(self):
        app_mod.settings().setValue('ui/close_action', 'quit')
        self.assertEqual(app_mod.close_action(), ('quit', True))

    def test_tray_action_hides_window(self):
        app_mod.settings().setValue('ui/close_action', 'tray')
        a = self._appobj()
        a.on_close_requested()
        self.assertTrue(a.win.hidden)
        self.assertFalse(getattr(a.win, 'quit_called', False))

    def test_quit_action_quits(self):
        app_mod.settings().setValue('ui/close_action', 'quit')
        a = self._appobj()
        a.on_close_requested()
        self.assertTrue(getattr(a.win, 'quit_called', False))
        self.assertFalse(a.win.hidden)

    def test_first_time_asks_and_remembers(self):
        """第一次点 × → 弹提示；勾了「记住」就把选择落库。"""
        a = self._appobj()
        a.ask_close_action = lambda: ('quit', True)
        a.on_close_requested()
        self.assertTrue(getattr(a.win, 'quit_called', False))
        self.assertEqual(app_mod.settings().value('ui/close_action'), 'quit')

    def test_first_time_without_remember_keeps_asking(self):
        a = self._appobj()
        a.ask_close_action = lambda: ('tray', False)
        a.on_close_requested()
        self.assertTrue(a.win.hidden)
        self.assertIsNone(app_mod.settings().value('ui/close_action'),
                          '没勾记住就不该落库')

    def test_dismissed_dialog_does_nothing(self):
        """把关窗对话框直接关掉 → 什么都不做（不隐藏、不退出）。"""
        a = self._appobj()
        a.ask_close_action = lambda: (None, False)
        a.on_close_requested()
        self.assertFalse(a.win.hidden)
        self.assertFalse(getattr(a.win, 'quit_called', False))

    def test_dashboard_has_the_close_choice(self):
        """设置里要有「当窗口关闭时」，两个选项都要在（用户要求；09-26 起在看板里）。"""
        from ui import theme
        self.assertEqual([k for k, _l in theme.CLOSE_ACTIONS], ['tray', 'quit'])
        # 看板侧行为在 tests/test_ui_dashboard.py 的 close_choice 用例里锁 ——
        # 这里只锁常量单一来源（ui.theme），别再出现第二份选项表。
        self.assertEqual(theme.DEFAULT_CLOSE_ACTION, 'tray')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class CloseDialogTest(SettingsPatchedTest):
    """★ 参考图那种「点击关闭后」对话框（2026-09-15 用户：「你的文字太啰嗦」）。

    两个**单选项** + 一个「不再提示」勾选 + 取消/确认；默认选「退出主程序」。
    """

    def test_shape_and_defaults(self):
        dlg = app_mod.CloseDialog()
        self.assertTrue(dlg.rb_quit.isChecked(), '默认选「退出主程序」（参考图）')
        self.assertFalse(dlg.rb_tray.isChecked())
        self.assertEqual(dlg.choice(), 'quit')
        self.assertFalse(dlg.remember(), '「不再提示」默认不勾')
        self.assertIn('不再提示', dlg.cb_remember.text())
        self.assertEqual(dlg.btn_ok.text(), '确认')
        self.assertEqual(dlg.btn_cancel.text(), '取消')
        texts = [lab.text() for lab in dlg.findChildren(QLabel)]
        self.assertIn('点击关闭后', texts)
        dlg.deleteLater()

    def test_radios_are_exclusive(self):
        dlg = app_mod.CloseDialog()
        dlg.rb_tray.setChecked(True)
        self.assertEqual(dlg.choice(), 'tray')
        self.assertFalse(dlg.rb_quit.isChecked())
        dlg.cb_remember.setChecked(True)
        self.assertTrue(dlg.remember())
        dlg.deleteLater()

    def test_dialog_is_frameless_with_its_own_dark_title(self):
        """★ 用户 2026-09-15 点头：「白色原生标题栏换成自绘暗色标题」。

        = 无边框 + 半透明圆角底 + **自绘的 ×**（复用 `floating.WindowButton`）。
        """
        dlg = app_mod.CloseDialog()
        self.assertTrue(dlg.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(dlg.testAttribute(Qt.WA_TranslucentBackground))
        self.assertEqual(dlg.btn_close.kind, 'close')
        self.assertLessEqual(dlg.width(), 320, '要小')
        dlg.deleteLater()

    def test_title_bar_close_button_rejects(self):
        """右上那个 × = 取消（不执行任何动作）。"""
        dlg = app_mod.CloseDialog()
        dlg.btn_close.clicked.emit()
        self.assertEqual(dlg.result(), app_mod.QDialog.Rejected)
        dlg.deleteLater()

    def test_cancel_returns_nothing(self):
        """取消 = 什么都不做（旧 QMessageBox 是「点按钮立刻执行」，没有确认这一步）。"""
        a = self._appobj_ask()
        with mock.patch.object(app_mod, 'CloseDialog', _FakeCloseDlg(
                accepted=False, choice='quit', remember=True)):
            self.assertEqual(a.ask_close_action(), (None, False))

    def test_confirm_returns_choice(self):
        a = self._appobj_ask()
        with mock.patch.object(app_mod, 'CloseDialog', _FakeCloseDlg(
                accepted=True, choice='tray', remember=True)):
            self.assertEqual(a.ask_close_action(), ('tray', True))

    def _appobj_ask(self):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a.win = None
        return a


class _FakeCloseDlg(object):
    """CloseDialog 替身：`exec()` 打桩，避免测试里真弹模态框。"""

    def __init__(self, accepted=True, choice='quit', remember=False):
        self._accepted = accepted
        self._choice = choice
        self._remember = remember

    def __call__(self, parent=None):            # 当类用：mock.patch 里替换的是类
        return self

    def exec(self):
        return app_mod.QDialog.Accepted if self._accepted else app_mod.QDialog.Rejected

    def choice(self):
        return self._choice

    def remember(self):
        return self._remember


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class NewDecksFeatureTest(SettingsPatchedTest):
    """★ S3.5「获取新卡组名」做到设置里（用户 2026-09-15 定 + 选「乙」）。"""

    def test_new_deck_names_skips_the_ones_already_in_the_table(self):
        """覆盖表里有的（站内英文名那列）就不该再问 —— 用真表只读验。"""
        fresh = app_mod.new_deck_names(
            ['Branded', 'Dracotail', '一个不存在的卡组xyz'],
            app_mod.CN_CSV)
        self.assertNotIn('Branded', fresh)
        self.assertNotIn('Dracotail', fresh)
        self.assertIn('一个不存在的卡组xyz', fresh)

    def test_missing_table_does_not_crash(self):
        self.assertEqual(app_mod.new_deck_names(['X'], '不存在的文件.csv'), ['X'])

    def test_new_decks_dialog_picks_checked_with_names(self):
        """「乙」：每行一个中文名输入框 + 勾选；`picked()` 只回勾上的。"""
        class _Imgs:
            def resolve(self, _name):
                return ''

        dlg = app_mod.NewDecksDialog(['Alpha', 'Beta'], _Imgs())
        self.assertEqual([n for n, _cb, _e in dlg.rows], ['Alpha', 'Beta'])
        for _n, cb, _e in dlg.rows:
            cb.setChecked(False)
        name, cb, edit = dlg.rows[1]
        cb.setChecked(True)
        edit.setText(' 贝塔 ')
        self.assertEqual(dlg.picked(), [(name, '贝塔')])
        dlg.deleteLater()


class _FakeFetchDlg(object):
    """`_show_new_decks` 认的最小对话框（只有状态行）。"""

    def __init__(self):
        self.fetch_status = QLabel()


class _StubRunner(QObject):
    """不真起进程的 `ScriptRunner` 替身（手动 `done.emit`）。"""

    done = Signal(int)

    def __init__(self, script, args=None):
        super().__init__()
        self.script = script
        self.args = list(args or [])
        self.started = False

    def running(self):
        return self.started

    def start(self):
        self.started = True


def _cover_csv(tmpdir, rows=(('Branded', '烙印', '', '', 'Tier2', '', ''),
                             ('Odion', '利希德', '', '', 'Trending', 'Y', ''))):
    """造一份**临时**覆盖表（7 列表头 + 两行）。"""
    path = os.path.join(tmpdir, 'deck_name_cn.csv')
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(','.join(deck_csv_mod.COLUMNS) + '\n')
        for r in rows:
            f.write(','.join(r) + '\n')
    return path


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class OppDeckManagerTest(SettingsPatchedTest):
    """「卡组名管理」（D3 §2）—— 改**覆盖表 CSV**，保存后自动 `--cn-only`。

    ⚠️ D3 §2 前这是 `OppDeckDialog`（**弹窗**，Q14）；现在它是内嵌在
    「卡组」页页签 1 里的 `OppDeckManager`（`ui/opp_deck_manager.py`）。
    行为（读表 / 编辑 / 保存 / 搜索 / 贴库）**一条没变**，所以测试照搬。
    """

    def _dlg(self, path):
        made = []

        def factory(script, args):
            r = _StubRunner(script, args)
            made.append(r)
            return r
        dlg = opp_deck_manager.OppDeckManager(csv_path=path,
                                              runner_factory=factory)
        return dlg, made

    def test_rows_loaded_from_csv(self):
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        dlg, _made = self._dlg(_cover_csv(d))
        self.assertEqual(len(dlg.rows_ui), 2)
        self.assertEqual(dlg.rows_ui[0].cn.text(), '烙印')
        self.assertTrue(dlg.rows_ui[1].hidden.isChecked(), 'Odion 隐藏=Y')
        self.assertFalse(dlg.rows_ui[0].hidden.isChecked())
        dlg.deleteLater()

    def test_edit_and_save_writes_csv_and_pushes(self):
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        path = _cover_csv(d)
        dlg, made = self._dlg(path)
        dlg.rows_ui[0].cn.setText(' 烙印之怒 ')
        dlg.rows_ui[0].alias.setText('Brandy / 烙')
        dlg.save()
        self.assertTrue(made and made[0].started, '保存后要接着把表贴进库')
        self.assertEqual(made[0].script, 'tools/fetch_meta_decks.py')
        self.assertEqual(made[0].args, ['--cn-only'])
        header, rows = deck_csv_mod.read(path)
        idx = deck_csv_mod.index_map(header)
        by_name = {deck_csv_mod.get(r, idx, '站内英文名'): r for r in rows}
        self.assertEqual(deck_csv_mod.get(by_name['Branded'], idx, '中文名'),
                         '烙印之怒', 'strip 过的值才落盘')
        self.assertEqual(deck_csv_mod.get(by_name['Branded'], idx, '别名'),
                         'Brandy / 烙')
        dlg.deleteLater()


    def test_unchecking_hidden_persists(self):
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        path = _cover_csv(d)
        dlg, made = self._dlg(path)
        dlg.rows_ui[1].hidden.setChecked(False)
        dlg.save()
        made[0].done.emit(0)
        header, rows = deck_csv_mod.read(path)
        idx = deck_csv_mod.index_map(header)
        by_name = {deck_csv_mod.get(r, idx, '站内英文名'): r for r in rows}
        self.assertEqual(deck_csv_mod.get(by_name['Odion'], idx, '隐藏'), '')
        self.assertIn('已保存并贴进库', dlg.status.text())
        dlg.deleteLater()

    def test_search_filter_counts(self):
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        dlg, _made = self._dlg(_cover_csv(d))
        dlg.edit_search.setText('烙印')
        self.assertIn('1 / 2', dlg.status.text())
        self.assertTrue(dlg.rows_ui[0].matches('烙印'))
        self.assertFalse(dlg.rows_ui[1].matches('烙印'))
        self.assertTrue(dlg.rows_ui[1].matches('Odion'), '站内英文名也算')
        dlg.deleteLater()

    def test_save_failure_does_not_raise(self):
        """写盘失败（比如目录根本不存在）只报状态，不炸、不起进程。"""
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        dlg, made = self._dlg(os.path.join(d, 'nope', 'deck_name_cn.csv'))
        dlg.save()
        self.assertEqual(made, [])
        self.assertIn('写盘失败', dlg.status.text())
        dlg.deleteLater()

    def test_save_blocked_when_csv_read_failed(self):
        """★ BUG-B6-01：覆盖表**读失败**过 -> save() 必须禁写。

        否则会把「只剩表头」的空表盖掉用户手填的 125 行（不可再生、且不报错）。
        """
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        dlg, made = self._dlg(_cover_csv(d))
        # 模拟构造期读表失败（被占用 / 权限）
        dlg._load_ok = False
        dlg._load_error = 'PermissionError: 被占用'
        dlg.save()
        self.assertEqual(made, [], '读失败后 save 不该起脚本进程')
        self.assertIn('已禁止写入', dlg.status.text())
        dlg.deleteLater()

    def test_push_failure_reports_exit_code(self):
        d = tempfile.mkdtemp(prefix='opp-decks-')
        self.addCleanup(shutil.rmtree, d, True)
        path = _cover_csv(d)
        dlg, made = self._dlg(path)
        dlg.save()
        made[0].done.emit(3)
        self.assertIn('退出码 3', dlg.status.text())
        dlg.deleteLater()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ShowNewDecksWritePathTest(SettingsPatchedTest):
    """★ 回归锁：`_show_new_decks` 一路走到**写盘**。

    以前这里用了个全仓未定义的 `CSV_PATH`（应为 `CN_CSV`），一写就 NameError；
    旧测试只测纯函数与对话框、没走写盘那步，所以一直全绿 —— 这条补上那个洞。
    """

    def test_writes_the_new_deck_into_the_cover_table(self):
        d = tempfile.mkdtemp(prefix='cn-csv-')
        self.addCleanup(shutil.rmtree, d, True)
        csv_path = _cover_csv(d)
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._demo = False
        pushed = []
        a._push_cover_table = lambda dlg: pushed.append(True)

        class _FakeStorage(object):
            def list_meta_decks(self, *args, **kw):
                return [{'name': 'Branded'}, {'name': 'Fresh Deck'}]

        a.storage = _FakeStorage()

        class _FakePicker(object):
            def __init__(self, names, imgs, parent=None):
                pass

            def exec(self):
                return app_mod.QDialog.Accepted

            def picked(self):
                return [('Fresh Deck', '新卡组')]

        dlg = _FakeFetchDlg()
        with mock.patch.object(app_mod, 'CN_CSV', csv_path), \
                mock.patch.object(app_mod, 'NewDecksDialog', _FakePicker), \
                mock.patch.object(app_mod, 'DeckImages', lambda storage: None):
            a._show_new_decks(dlg)
        self.assertTrue(pushed, '写完覆盖表要接着跑 --cn-only 进库')
        header, rows = deck_csv_mod.read(csv_path)
        idx = deck_csv_mod.index_map(header)
        names = [deck_csv_mod.get(r, idx, '站内英文名') for r in rows]
        self.assertIn('Fresh Deck', names)
        fresh = rows[names.index('Fresh Deck')]
        self.assertEqual(deck_csv_mod.get(fresh, idx, '中文名'), '新卡组')
        self.assertEqual(deck_csv_mod.get(fresh, idx, '备注'), '设置页新增')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class AutoAdoptTest(unittest.TestCase):
    """★ 2026-10-24 用户需求：高置信建议自动入库（不用手点确认）。

    三条安全线：conf>=0.85 / hits>=4 / 第一名明显胜出（并列不选边）。
    """

    def _c(self, name, hits, conf, bayes=-3.0):
        return {'name': name, 'hits': hits, 'confidence': conf, 'bayes': bayes}

    def test_high_confidence_adopts(self):
        got = app_mod.auto_adoptable(
            [self._c('狱神耀圣', 7, 1.0), self._c('Elfnote', 5, 0.7, -8.0)])
        self.assertIsNotNone(got)
        self.assertEqual(got['name'], '狱神耀圣')

    def test_three_hit_tier_rules(self):
        """★ 3 张档（2026-10-24 深夜二降门槛 4->3）：唯一解释者放行、
        并列（多半是泛用全中）拒绝；4 张起才允许并列选边。"""
        #: 白森1+耀圣2 的口径局：第一名 3/3 且第二名 hits 更低 —— 自动
        self.assertIsNotNone(app_mod.auto_adoptable(
            [self._c('耀圣白森', 3, 1.0, -2.0), self._c('耀圣', 2, 0.6, -9.0)]))
        #: 三张泛用恰好全中：几十个卡组完全并列 —— 不自动
        self.assertIsNone(app_mod.auto_adoptable(
            [self._c('X', 3, 1.0, -2.0), self._c('Y', 3, 1.0, -2.0),
             self._c('Z', 3, 1.0, -2.0)]))
        #: 2 张 —— 样本太少
        self.assertIsNone(app_mod.auto_adoptable(
            [self._c('X', 2, 1.0), self._c('Y', 1, 0.5, -9.0)]))

    def test_low_confidence_rejected(self):
        #: 捕食植物 0.88 / 小丑戏帮 0.84 会过（>0.80）；结界像 0.67 不自动
        self.assertIsNotNone(app_mod.auto_adoptable(
            [self._c('Predaplant', 7, 0.88, -4.0), self._c('Z', 5, 0.6, -9.0)]))
        self.assertIsNotNone(app_mod.auto_adoptable(
            [self._c('Clown Crew', 16, 0.84, -4.0), self._c('Z', 5, 0.6, -9.0)]))
        self.assertIsNone(app_mod.auto_adoptable(
            [self._c('Banquet', 5, 0.67, -5.0), self._c('Z', 4, 0.5, -9.0)]))

    def test_tie_picks_more_specific_cn_name(self):
        """★ 2026-10-24 深夜改口径：同分并列不再弃权 —— 选中文名更具体的
        （用户实报：对手全耀圣时狱神耀圣与耀圣永远同分，三局全要手点）。"""
        a = dict(self._c('狱神耀圣', 7, 1.0, -3.2), name_cn='狱神耀圣')
        b = dict(self._c('Elfnote', 7, 1.0, -3.2), name_cn='耀圣')
        self.assertEqual(app_mod.auto_adoptable([a, b])['name'], '狱神耀圣')
        # 顺序反过来也一样
        self.assertEqual(app_mod.auto_adoptable([b, a])['name'], '狱神耀圣')
        # 小丑戏帮(4字) vs 堕天使(3字)
        c = dict(self._c('Clown Crew', 16, 0.84, -4.0), name_cn='小丑戏帮')
        d = dict(self._c('Darklord', 16, 0.84, -4.0), name_cn='堕天使')
        self.assertEqual(app_mod.auto_adoptable([c, d])['name'], 'Clown Crew')
        # 都没有中文名 —— 并列时维持第一名（不因英文名长而选边）
        e = self._c('Clown Crew', 16, 0.84, -4.0)
        f = self._c('Darklord', 16, 0.84, -4.0)
        self.assertEqual(app_mod.auto_adoptable([e, f])['name'], 'Clown Crew')
        # bayes 拉开（-3.2 vs -3.6）—— 并列组只有第一名，照旧自动
        g = dict(self._c('狱神耀圣', 7, 1.0, -3.2), name_cn='狱神耀圣')
        h = dict(self._c('Elfnote', 7, 1.0, -3.6), name_cn='耀圣')
        self.assertEqual(app_mod.auto_adoptable([g, h])['name'], '狱神耀圣')

    def test_empty_and_second_lower_hits(self):
        self.assertIsNone(app_mod.auto_adoptable([]))
        self.assertIsNone(app_mod.auto_adoptable(None))
        #: 第二名 hits 更少但 bayes 更高 —— 看合计：仍按第一名（hits 主排序）
        got = app_mod.auto_adoptable(
            [self._c('A', 8, 0.9, -5.0), self._c('B', 6, 0.7, -2.0)])
        self.assertIsNotNone(got)


class SuggestConfidenceGateTest(unittest.TestCase):
    """★ 2026-10-24 用户拍板：低置信建议不显示（老实「未知卡组」）。

    起因：对手是站外冷门自组（阿努比斯/王家）时 21 张只解释 8 张，
    第一名 conf=0.38 —— 显示出来就是误导（用户实测报「离谱建议」）。
    """

    def test_gate_filters_low_confidence(self):
        cands = [{'name': 'A', 'hits': 8, 'confidence': 0.38},
                 {'name': 'B', 'hits': 5, 'confidence': 0.49},
                 {'name': 'C', 'hits': 5, 'confidence': 0.5},
                 {'name': 'D', 'hits': 5, 'confidence': 1.0},
                 {'name': 'E', 'hits': 0, 'confidence': 0.9}]   # 0 命中也不许进
        got = app_mod.gate_suggestions(cands)
        self.assertEqual([c['name'] for c in got], ['C', 'D'])

    def test_gate_floor_override_and_empty(self):
        self.assertEqual(app_mod.gate_suggestions(None), [])
        self.assertEqual(app_mod.gate_suggestions([]), [])
        keep = app_mod.gate_suggestions(
            [{'name': 'X', 'hits': 2, 'confidence': 0.3}], floor=0.25)
        self.assertEqual([c['name'] for c in keep], ['X'])

    def test_real_cases_high_keeps_low_drops(self):
        """两条实测：耀圣镜像局（5/5 解释）保留；阿努比斯站外局（8/21）压掉。"""
        sys.path.insert(0, os.path.join(PROJ))
        from core import deck_guess
        idx, groups = deck_guess.load_index()
        strong = [c for c in deck_guess.suggest(
            {'opp_revealed_ids': [22565, 22159, 12950, 22523, 9455]},
            idx, groups) if c.get('hits')]
        weak = [c for c in deck_guess.suggest(
            {'opp_revealed_ids': [18311, 21353, 5530, 13293, 21044, 13631,
                                  6541, 17728, 5980, 21035, 21037, 21059,
                                  19049, 21040, 21215, 22586, 21036, 20257,
                                  5231, 8606, 20231]}, idx, groups)
            if c.get('hits')]
        self.assertTrue(app_mod.gate_suggestions(strong),
                        '耀圣镜像局的建议不该被压掉')
        self.assertEqual(app_mod.gate_suggestions(weak), [],
                         '站外冷门局的弱建议必须整体压掉')


class FloatingRelayoutTest(SettingsPatchedTest):
    """★ 2026-10-24：勾「隐藏某列」必须**实时**重排悬浮窗（新机器实测只能重启生效）。

    根因：`floating_relayout` 里写了 `self.deck_name_pool()` ——
    MasterDuelUiApp 没有这个方法，AttributeError 被 `except Exception: pass`
    吞掉，`win._relayout_columns()` 永远走不到。修复 = 模块级
    `ui_state.deck_name_pool(self.storage)`。这个测试锁「重排链真的走到底」。
    """

    def test_relayout_reaches_columns(self):
        from core import storage as storage_mod
        import tempfile
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        with tempfile.TemporaryDirectory() as td:
            a.storage = storage_mod.Storage(os.path.join(td, 't.db'))
            win = mock.MagicMock()
            a.win = win
            a.floating_relayout()
            #: 🔴 修复前：`self.deck_name_pool()` 抛 AttributeError 被吞，
            #:   下面两条都不会发生（实测 offscreen 全链复现）。
            self.assertIsNone(win._last_layout)
            win._relayout_columns.assert_called_once()
            win.setMinimumWidth.assert_called_once()


class AutoShowFloatingTest(SettingsPatchedTest):
    """游戏启动 → 自动弹悬浮窗（2026-10-05）。

    锁状态机：只在 **False→True 跳变**弹（首拍建基线不弹、持续运行不重复、
    手动收起后游戏没退不打扰）；设置关了就不弹。
    """

    class _ShowWin(object):
        def __init__(self):
            self.visible = False
            self.shown = 0

        def isVisible(self):
            return self.visible

        def show(self):
            self.visible = True
            self.shown += 1

        def raise_(self):
            pass

    def setUp(self):
        super().setUp()
        self.win = self._ShowWin()

    def _app(self, running):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a._demo = False
        a.win = self.win
        a._game_was_running = None
        a._game_running = lambda: running
        return a

    def test_first_tick_shows_when_game_already_running(self):
        """★ 2026-10-29 用户实报「打不开 start.bat」的回归锁。

        场景：**先开游戏、再双击 start.bat** —— 那时首拍 `_game_was_running`
        就是 True（不是 False），旧逻辑的 `prev is False` 永不成立 →
        UI 进程活着但窗口永不出现，用户以为软件没启动。

        现在首拍也应该弹一次。
        """
        a = self._app(running=True)
        a._auto_show_tick()
        self.assertEqual(self.win.shown, 1, '游戏已在跑时，启动后应弹一次')

    def test_first_tick_silent_when_game_not_running(self):
        """游戏没开 -> 不弹（等它开了再弹）。"""
        a = self._app(running=False)
        a._auto_show_tick()
        self.assertEqual(self.win.shown, 0)

    def test_pops_on_rising_edge(self):
        """游戏从关到开 -> 弹（原有语义不变）。"""
        a = self._app(running=False)
        a._auto_show_tick()                    # 首拍：游戏没开，不弹
        self.assertEqual(self.win.shown, 0)
        a._game_running = lambda: True
        a._auto_show_tick()                    # False→True：弹
        self.assertEqual(self.win.shown, 1)

    def test_user_hides_not_repopped(self):
        """用户手动收起、游戏没退 -> **不再**自动弹（原有语义不变）。

        ⚠️ 这条是「启动后首拍也弹」的边界：那个只弹**一次**
        （`_auto_shown_once`），用户收起后不会被它反复弹出来。
        """
        a = self._app(running=True)
        a._auto_show_tick()                    # 首拍弹一次
        self.assertEqual(self.win.shown, 1)
        self.win.visible = False               # 用户收起（游戏没退）
        a._auto_show_tick()
        self.assertEqual(self.win.shown, 1, '收起了就不该再弹')
        a._auto_show_tick()
        self.assertEqual(self.win.shown, 1)

    def test_setting_off_disables(self):
        app_mod.settings().setValue('ui/auto_show_floating', '0')
        a = self._app(running=True)
        a._game_was_running = False
        a._auto_show_tick()
        self.assertEqual(self.win.shown, 0)




if __name__ == '__main__':
    unittest.main()


class CollectorGuardTest(unittest.TestCase):
    """★ 2026-10-24 机主实机事故：采集器静默死 12 小时没人管。

    `_collector_guard_tick()` 补常驻守护：每 5 分钟查一次心跳，
    不新鲜就 `ensure_collector()`。这里用裸对象 + 打桩验证三条：
    死了会拉、节流期内不重复拉、活着不拉。
    """

    def _bare_app(self):
        from ui.app import MasterDuelUiApp
        app = MasterDuelUiApp.__new__(MasterDuelUiApp)
        app._demo = False
        app._last_collector_guard = 0.0
        return app

    def test_dead_collector_gets_started(self):
        """心跳死了（采集器不在）→ 拉起。"""
        from ui import app as app_mod
        from core import ui_state
        app = self._bare_app()
        started = []
        with mock.patch.object(app_mod.ui_state, 'collector_status',
                               lambda *a, **k: {'running': False}), \
             mock.patch('ui.collector_ctl.ensure_collector',
                        lambda *a, **k: started.append(1) or 99):
            app._collector_guard_tick()
        self.assertEqual(started, [1], '采集器死了必须拉起一次')

    def test_throttled_within_window(self):
        """5 分钟内第二次 → 不重复拉（节流）。"""
        from ui import app as app_mod
        app = self._bare_app()
        started = []
        with mock.patch.object(app_mod.ui_state, 'collector_status',
                               lambda *a, **k: {'running': False}), \
             mock.patch('ui.collector_ctl.ensure_collector',
                        lambda *a, **k: started.append(1)):
            app._collector_guard_tick()
            app._collector_guard_tick()      # 立刻再拍：应被节流挡住
        self.assertEqual(started, [1], '节流期内不能重复拉起')

    def test_running_collector_untouched(self):
        """心跳新鲜（在跑）→ 不拉。"""
        from ui import app as app_mod
        app = self._bare_app()
        started = []
        with mock.patch.object(app_mod.ui_state, 'collector_status',
                               lambda *a, **k: {'running': True}), \
             mock.patch('ui.collector_ctl.ensure_collector',
                        lambda *a, **k: started.append(1)):
            app._collector_guard_tick()
        self.assertEqual(started, [], '采集器活着不该再拉')

    def test_tick_calls_guard(self):
        """tick() 里真的接了守护（防将来被删掉都不报错）。"""
        from ui import app as app_mod
        self.assertIn('self._collector_guard_tick()',
                      open(os.path.join(PROJ, 'ui', 'app.py'),
                           encoding='utf-8').read(),
                      'tick() 没接采集器守护 —— 静默死亡事故会重演')
