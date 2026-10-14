# -*- coding: utf-8 -*-
"""多选模式筛选的单测（2026-10-14 用户要求「Excel 式打勾」）。

盯三件事：
  1. `mode_group_clause` 收到**列表**要取并集；`'all'` / 空列表 = 不筛选；未知值照旧抛错；
  2. `ModeFilterButton` 勾选语义：**空 = 全部**、「全部」是快捷全选、取消一项要自动掉；
  3. QSettings 的读写（`parse_setting` / `dump_setting`）能往返，全勾归一成空。

⚠️ 背景坑：以前「全部」传的是 `None`，而 `get_cross_stats(None)` 走**主口径**
（只天梯+巅峰）—— 用户打了 35 局 DC 杯、对局分析却一条都没有。现在「空列表」
= 不加模式条件 = 全部。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import (MODE_GROUP_NAMES, MODE_SELECT_NONE,     # noqa: E402
                          mode_group_clause)
from ui.i18n import tr                                          # noqa: E402
from ui.mode_filter import (dump_setting, label_for,            # noqa: E402
                            parse_setting)


class TestModeGroupClause(unittest.TestCase):

    def test_list_is_union(self):
        c = mode_group_clause(['ranked', 'dc'])
        self.assertIn(' OR ', c)
        self.assertIn('12', c, 'DC 杯')
        self.assertIn('3', c, '天梯')

    def test_all_literal_means_no_filter(self):
        """★ `'all'` = **字面不加模式条件**（含判不出模式的残留行），不是主口径。"""
        self.assertIsNone(mode_group_clause('all'))
        self.assertIsNone(mode_group_clause(['all', 'dc']))

    def test_empty_list_means_all_modes_union(self):
        """★ **空列表 = 「全部模式」= 四组并集**（天梯 + 巅峰 + DC + 各活动），
        不是 None（那是主口径只 3/19 —— 用户 35 局 DC 杯看不到的根因）。

        并与用户原话对得上：「勾选排名决斗和 DC 杯」= 两组的 OR。
        """
        empty = mode_group_clause([])
        union = mode_group_clause(['ranked', 'peak', 'dc', 'event'])
        #: ⚠️ 比**字符串**而不是语义 —— `ranked OR peak` 与 `gm IN (3,19)` 逻辑等价
        #:   但串不同，所以只跟「四组并集」这个同构物比。
        self.assertEqual(empty, union, '空列表该等于四组并集')
        self.assertIn('12', empty, 'DC 杯要在里面')
        self.assertIn('3', empty, '天梯要在里面')
    def test_pick_two_modes_unions_their_matches(self):
        """★ 用户原话场景：「勾选排名决斗和 DC 杯」→ 两组的局都要在。

        ⚠️ 与界面口径 `duel_clause()` **逻辑等价但串形不同**（它是
        `IN (3) OR IN (19)`、duel 是 `IN (3,19)`）—— 比串会假红，所以这里
        **拿真库跑行数**：构建「天梯 + 巅峰 + DC + 活动」各一局，分别验证
        单组/并集/空列表（全部）三层语义。
        """
        import os
        import tempfile
        from core.storage import Storage
        d = tempfile.mkdtemp(prefix='mf-union-')
        st = Storage(os.path.join(d, 't.db'))
        st.init_schema()
        for i, gm in enumerate((3, 19, 12, 21)):      # 天梯/巅峰/DC/活动 各一局
            st.upsert_match({'match_id': 'm%d' % i,
                             'started_at': '2026-09-1%d 10:00:00.000' % i,
                             'result': 'Win', 'game_mode': gm, 'mode': 'pvp'})
        rows = st.get_cross_stats(mode_group=[])      # 空列表 = 全部模式
        self.assertEqual(sum(r[2] for r in rows), 4, '全部模式该含四组各一局')
        two = st.get_cross_stats(mode_group=['ranked', 'dc'])
        self.assertEqual(sum(r[2] for r in two), 2, '勾天梯 + DC 该取到两局')

    def test_none_sentinel_is_not_all_modes(self):
        """★ 哨兵 `MODE_SELECT_NONE`（未选择）= **恒假**，不是「全部」。

        与「空列表 = 全部模式」区分开 —— 那是用户 2026-10-15 的「全部再点一次
        = 全取消」态：菜单四个全不勾，结果该是 0 场。
        """
        self.assertEqual(mode_group_clause([MODE_SELECT_NONE]), '0')
        self.assertEqual(mode_group_clause(MODE_SELECT_NONE), '0')
        #: 真库跑一遍：哨兵 -> 0 场；空列表 -> 有场
        import os
        import tempfile
        from core.storage import Storage
        d = tempfile.mkdtemp(prefix='mf-none-')
        st = Storage(os.path.join(d, 't.db'))
        st.init_schema()
        st.upsert_match({'match_id': 'm0',
                         'started_at': '2026-09-10 10:00:00.000',
                         'result': 'Win', 'game_mode': 3, 'mode': 'pvp'})
        self.assertEqual(sum(r[2] for r in
                             st.get_cross_stats(mode_group=[MODE_SELECT_NONE])), 0)
        self.assertEqual(sum(r[2] for r in
                             st.get_cross_stats(mode_group=[])), 1)

    def test_single_value_still_works(self):
        self.assertIn('12', mode_group_clause('dc'))

    def test_unknown_still_raises(self):
        """绝不静默返回全量（那会让人看到错数字还不知道）。"""
        with self.assertRaises(ValueError):
            mode_group_clause('nope')
        with self.assertRaises(ValueError):
            mode_group_clause(['dc', 'nope'])


class TestSettingRoundTrip(unittest.TestCase):

    def test_round_trip(self):
        self.assertEqual(parse_setting('ranked,dc'), ['ranked', 'dc'])
        self.assertEqual(dump_setting(['ranked', 'dc']), 'ranked,dc')
        #: 全勾 / 空 都归一成空串（= 全部）
        self.assertEqual(dump_setting(['ranked', 'peak', 'dc', 'event']), '')
        self.assertEqual(parse_setting(''), [])
        self.assertEqual(parse_setting(None), [])
        self.assertEqual(parse_setting('ranked,nope'), ['ranked'])     # 坏值丢掉
        #: 哨兵原样往返（「未选择模式」要能存能读）
        self.assertEqual(dump_setting([MODE_SELECT_NONE]), MODE_SELECT_NONE)
        self.assertEqual(parse_setting(MODE_SELECT_NONE), [MODE_SELECT_NONE])


class TestLabel(unittest.TestCase):

    def test_empty_is_all(self):
        self.assertIn('全部', label_for([]))
        self.assertIn('全部', label_for(None))

    def test_string_value_also_accepted(self):
        """向后兼容：老调用还在传 'dc' 这种字符串（别迭代成 'd'/'c'）。"""
        self.assertNotIn('c', label_for('dc').replace('DC', ''))
        self.assertTrue(label_for('dc'))


try:
    import os as _os
    _os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QCheckBox, QWidgetAction

    from ui.mode_filter import FILTER_KEYS, ModeFilterButton
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class TestModeFilterButton(unittest.TestCase):
    """多选控件语义（用户 2026-10-14：「excel 那种可以打勾筛选」）。

    ⚠️ 关键回归：**点一项不该关菜单** —— 老实现用 `menu.addAction().setCheckable`
    + `exec()`，Qt 点任一 action 立刻关闭（用户报「打勾点完下拉框直接关闭了」）。
    现在菜单是 `QWidgetAction` 装 `QCheckBox`，勾选只改本地状态、关菜单时才发信号。
    """

    def setUp(self):
        _app()

    def test_empty_is_all_and_full_tick_normalizes(self):
        b = ModeFilterButton()
        self.assertEqual(b.selected(), [])
        self.assertIn('全部', b.text())
        b.set_selected(FILTER_KEYS)                  # 全勾 = 空 = 全部
        self.assertEqual(b.selected(), [])
        b.set_selected('dc')                          # 旧单值字符串也认
        self.assertEqual(b.selected(), ['dc'])

    def test_menu_uses_checkbox_widget_not_plain_actions(self):
        """菜单项是 `QCheckBox`（`QWidgetAction`）—— 这是「点完不关闭」的实现前提。"""
        b = ModeFilterButton()
        menu, host = b._build_menu()          # ⚠️ 不 exec（模态会挂测试）
        self.assertTrue(any(isinstance(a, QWidgetAction) for a in menu.actions()),
                        '菜单该用 QWidgetAction 装复选框')
        cbs = [c.text() for c in host.findChildren(QCheckBox)]
        self.assertIn('全部', cbs)
        for key in FILTER_KEYS:
            self.assertIn(tr(MODE_GROUP_NAMES[key]), cbs)

    def test_all_mode_highlights_every_sub_item(self):
        """★ 用户 2026-10-15：「全部应该是全部高亮，不是只有全部这一行高亮」。

        「全部模式」（空列表）时**四个子项也一起打勾** —— 否则看着像没生效。
        """
        b = ModeFilterButton()                # 初始 = 全部模式
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        self.assertTrue(cbs['全部'].isChecked(), '「全部」该是勾着的')
        for key in FILTER_KEYS:
            self.assertTrue(cbs[tr(MODE_GROUP_NAMES[key])].isChecked(),
                            '%s 也该一起高亮' % MODE_GROUP_NAMES[key])

    def test_uncheck_one_from_all_drops_all_box(self):
        """从「全部」里取消一项：该项掉、**「全部」自动掉**、其余仍打勾。"""
        b = ModeFilterButton()
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        cbs[tr(MODE_GROUP_NAMES['dc'])].setChecked(False)
        self.assertFalse(cbs['全部'].isChecked(), '取消一项后「全部」该掉')
        self.assertFalse(cbs[tr(MODE_GROUP_NAMES['dc'])].isChecked())
        for key in ('ranked', 'peak', 'event'):
            self.assertTrue(cbs[tr(MODE_GROUP_NAMES[key])].isChecked())
        self.assertEqual(sorted(b.selected()), ['event', 'peak', 'ranked'])

    def test_checking_items_emits_immediately(self):
        """★ 用户 2026-10-15：「选完后必须点一下才会刷新」→ 改成**实时刷新**：
        每勾一下就发 `changed`（带勾选列表），不用关菜单。"""
        b = ModeFilterButton()
        got = []
        b.changed.connect(got.append)
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        #: 从「全部」态取消两项（真实操作：勾选期间 checkbox 直接点，不关菜单）
        cbs[tr(MODE_GROUP_NAMES['dc'])].setChecked(False)
        self.assertEqual(sorted(got[-1]), ['event', 'peak', 'ranked'],
                         '勾一下立刻发信号')
        cbs[tr(MODE_GROUP_NAMES['event'])].setChecked(False)
        self.assertEqual(sorted(got[-1]), ['peak', 'ranked'], '每勾一下都发')
        self.assertEqual(len(got), 2, '两次操作 = 两次信号')

    def test_all_checkbox_resets_selection(self):
        b = ModeFilterButton()
        b.set_selected(['dc'])
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        cbs[tr(MODE_GROUP_NAMES['ranked'])].setChecked(True)
        self.assertEqual(sorted(b.selected()), ['dc', 'ranked'])
        cbs['全部'].setChecked(True)          # 「全部」= 全选（空列表）
        self.assertEqual(b.selected(), [])
        #: 且四个子项都回到勾选态
        for key in FILTER_KEYS:
            self.assertTrue(cbs[tr(MODE_GROUP_NAMES[key])].isChecked())

    def test_unchecking_all_clears_everything(self):
        """★ 用户 2026-10-15：「全部高亮再点全部应该全部取消」。

        「全部」= 主开关：勾 = 四模式全勾；**再点（取消） = 四模式全取消**
        → 数据值 `[MODE_SELECT_NONE]`（未选择模式），与「全部模式」区分开。
        """
        b = ModeFilterButton()
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        cbs['全部'].setChecked(False)         # 取消「全部」
        self.assertEqual(b.selected(), [MODE_SELECT_NONE], '该是「未选择模式」哨兵')
        self.assertFalse(cbs['全部'].isChecked())
        for key in FILTER_KEYS:
            self.assertFalse(cbs[tr(MODE_GROUP_NAMES[key])].isChecked(),
                             '四个模式该全取消')
        #: 再点「全部」-> 四个又全勾
        cbs['全部'].setChecked(True)
        self.assertEqual(b.selected(), [], '回到全部模式')

    def test_set_selected_does_not_clobber_live_ticks(self):
        """★ 实时刷新回环：勾选后页面立即重取，数据层回传**同一个值** ——
        `set_data` → `set_selected` 不能把勾选重置（否则勾一下就被打回）。
        """
        b = ModeFilterButton()
        _menu, host = b._build_menu()
        cbs = {c.text(): c for c in host.findChildren(QCheckBox)}
        cbs[tr(MODE_GROUP_NAMES['dc'])].setChecked(False)     # 用户取消 DC
        sent = b.selected()                                   # = ['ranked','peak','event']
        #: 实时刷新的回环：窗口把刚发出去的值原样喂回来 → 必须 no-op
        b.set_selected(list(sent))
        self.assertEqual(sorted(b.selected()), ['event', 'peak', 'ranked'],
                         '回环回传同一个值不该重置勾选')
        self.assertFalse(cbs[tr(MODE_GROUP_NAMES['dc'])].isChecked(), '菜单态也不该变')
        #: 数据层真变了 -> 才跟着动（比如切页/切语言）
        b.set_selected('dc')
        self.assertEqual(b.selected(), ['dc'])


if __name__ == '__main__':
    unittest.main()
