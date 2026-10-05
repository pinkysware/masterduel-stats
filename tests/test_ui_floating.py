# -*- coding: utf-8 -*-
"""ui/theme.py + ui/floating.py 单测（M7 —— 悬浮窗已改版成「最近 3 场」，2026-09-14）。

界面代码本来测不动，但「快照里的行怎么落到控件上、什么颜色、升段画不画箭头」
是纯映射逻辑，值得锁住。用 ``QT_QPA_PLATFORM=offscreen`` 跑，无显示器也能过。

⚠️ 没装 PySide6 时整个模块跳过（数据层不该因为界面依赖变红）。
"""
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')     # 必须在 Qt import 之前
# ★ 2026-09-22：`my_deck.json` 也要隔离 —— 且必须在 `core.my_deck` 被 import 之前
#   （`STATE_PATH` 在模块导入时定格）。卡组确认提示条读它；读到用户真实状态
#   （残留的旧卡组名）会让「3 行不出滚动条」等高度断言全红（实测）。
os.environ.setdefault(
    'MD_MY_DECK_STATE',
    os.path.join(tempfile.gettempdir(), 'm7-ui-mydeck-test.json'))

try:
    from PySide6.QtCore import QPoint, QSize, Qt
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    from core import ui_state
    from ui import floating, theme, widgets
    from ui.floating import MAX_ROWS, FloatingWindow, RankBadge, ResultChip

_APP = None
_SETTINGS_DIR = None


def _isolate_settings():
    """把 `FloatingWindow` 的 QSettings 指到**临时 INI**，别碰用户的真实配置。

    ⚠️ 2026-09-15 踩到：用户在真实 app 里把窗口拖成 299×425（`float/size` 落盘）之后，
    「窗口宽 == `window_width()`」那一堆用例全红了 —— 测试读到了**真实用户状态**。
    位置（`float/pos`）同理，也会让窗口跑到屏幕外。
    """
    global _SETTINGS_DIR
    if _SETTINGS_DIR is not None:
        return
    import tempfile
    from PySide6.QtCore import QSettings
    _SETTINGS_DIR = tempfile.mkdtemp(prefix='m7-ui-settings-')
    path = os.path.join(_SETTINGS_DIR, 'ui.ini')
    floating.FloatingWindow._settings = staticmethod(
        lambda: QSettings(path, QSettings.IniFormat))
    # ★ i18n 也走这份临时 INI（否则读用户真机 ui/lang，测试随机器状态翻车）
    import ui.i18n as _i18n
    _i18n._SETTINGS_FACTORY = staticmethod(
        lambda: QSettings(path, QSettings.IniFormat))

    # ★ 2026-09-22：`my_deck.json` 同样要隔离 —— 卡组确认提示条读它，
    #   读到用户真实状态（残留的旧卡组名）会让「不出滚动条」等高度断言全红。
    os.environ['MD_MY_DECK_STATE'] = os.path.join(
        _SETTINGS_DIR, 'my_deck.json')


def _app():
    """QApplication 是进程级单例 —— 整个测试模块共用一份。"""
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyleSheet(theme.qss())
    _isolate_settings()
    return _APP


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ThemeTest(unittest.TestCase):

    def test_win_rate_colors(self):
        self.assertEqual(theme.win_rate_color(0.6), theme.WIN)      # >50% 绿
        self.assertEqual(theme.win_rate_color(0.5), theme.WARN)     # =50% 黄
        self.assertEqual(theme.win_rate_color(0.4), theme.LOSE)     # <50% 红
        self.assertEqual(theme.win_rate_color(None), theme.INK_SUBTLE)

    def test_progress_color_is_not_win_rate_color(self):
        """★ 标注进度 ≠ 胜负：43.8% 的进度不该显示成「红 = 打得差」。"""
        self.assertNotEqual(theme.progress_color(0.438),
                            theme.win_rate_color(0.438))
        self.assertEqual(theme.progress_color(0.9), theme.WIN)
        self.assertEqual(theme.progress_color(None), theme.INK_SUBTLE)

    def test_rank_delta_colors(self):
        """升段绿、掉段红、持平/未知灰。"""
        self.assertEqual(theme.rank_delta_fg('up'), theme.WIN)
        self.assertEqual(theme.rank_delta_fg('down'), theme.LOSE)
        self.assertEqual(theme.rank_delta_fg('same'), theme.INK_MUTED)
        self.assertEqual(theme.rank_delta_fg(None), theme.INK_MUTED)

    def test_formatters(self):
        self.assertEqual(theme.fmt_pct(0.65625), '65.6%')
        self.assertEqual(theme.fmt_pct(None), '-')      # 不是 0%
        self.assertEqual(theme.fmt_record(32, 11), '32 - 11')

    def test_result_colors(self):
        self.assertEqual(theme.result_fg('Win'), theme.WIN)
        self.assertEqual(theme.result_fg('Lose'), theme.LOSE)
        self.assertEqual(theme.result_soft('Win'), theme.WIN_SOFT)
        self.assertEqual(theme.result_label('Win'), '胜')

    def test_qss_has_colors_but_no_fonts(self):
        """★ `qss()` 里**不许**出现 `font-*` —— 字体一律走 QFont（DESIGN.md §11①）。"""
        q = theme.qss()
        self.assertIn(theme.CANVAS, q)
        self.assertIn(theme.HAIRLINE, q)
        self.assertNotIn('font-size', q)
        self.assertNotIn('font-family', q)

    def test_theme_is_gui_free(self):
        """theme.py 是纯常量模块 —— 不许 import Qt（这样它可被无头单测直接 import）。"""
        import io
        with io.open(theme.__file__, encoding='utf-8') as f:
            src = f.read()
        import_lines = [ln for ln in src.split('\n')
                        if ln.strip().startswith(('import ', 'from '))]
        self.assertFalse(any('PySide6' in ln for ln in import_lines), import_lines)


def _press(widget):
    """左键按下事件（直接喂 mousePressEvent，不走窗口系统）。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    return QMouseEvent(QEvent.MouseButtonPress, QPointF(4, 4),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


def _release(widget):
    """左键抬起事件（与 _press 同位置 → 位移 0 = 点击）。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    return QMouseEvent(QEvent.MouseButtonRelease, QPointF(4, 4),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


def _move(widget, dx, dy):
    """左键移动事件（dx/dy 相对位移，起点同 _press）。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    return QMouseEvent(QEvent.MouseMove, QPointF(4 + dx, 4 + dy),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


def _row(**kw):
    """一行快照数据（默认是「已标注的胜局」）。"""
    base = {
        'match_id': 'm1', 'started_at': '2026-09-13 19:53',
        'day': '09-13', 'time': '19:53', 'is_today': False, 'result': 'Win',
        'went_first': 1,
        'my_deck': '闪刀姬',
        'my_deck_img': 'data/deck_images_custom/Sky_Striker.webp',
        'opp_deck': '烙印', 'opp_known': True,
        'opp_deck_img': 'data/deck_images_custom/Branded.webp',
        'opp_player': '幻想乡的毛玉',
        'badge_icon': 'data/rank_icons/Platinum.webp', 'badge_text': 'Platinum 2',
        'rank_before': (5, 2), 'rank_after': None, 'rank_delta': None,
        'rank_label_before': 'Platinum 2', 'rank_label_after': '',
        'rank_icon_after': None,
        'badge_numeral': 'II',
        'badge_numeral_after': '',
    }
    base.update(kw)
    return base


def _snap(rows=(), unmarked=0):
    s = ui_state.UiSnapshot()
    s.recent = list(rows)
    s.unmarked = [{'match_id': 'x%d' % i} for i in range(unmarked)]
    return s


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class FloatingWindowTest(unittest.TestCase):

    def setUp(self):
        _app()
        self.win = FloatingWindow()

    def tearDown(self):
        self.win.deleteLater()

    # ------------------------------------------------------------ 基本
    def test_no_more_tabs_and_no_2x2_grid(self):
        """★ 改版：**「今日」「待标注」两个 tab 和 2×2 数字格都删掉了**。"""
        for gone in ('tab_today', 'tab_unmarked', 'cells', 'banner'):
            self.assertFalse(hasattr(self.win, gone), '还留着 %s' % gone)

    def test_height_stable_across_refreshes(self):
        """★ 实机回归锁（2026-09-15）：探针每 2 秒重建全部行，行区**已可见**
        时新行不会被同步 show → 隐藏 item 被当空、rows_host.sizeHint() 读 0
        → 窗口塌到 156、内容挤压叠绘（用户桌面实拍）。
        修复 = `_render_rows` 里 addWidget 后立即 show()。
        ⚠️ 本测试必须先把窗口 show() —— 行区不可见时 bug 复现不了（空锁）。
        """
        self.win.show()
        QApplication.processEvents()
        self.win.set_snapshot(_snap(rows=[_row()]))
        h1 = self.win.height()
        self.assertTrue(self.win.rows_host.isVisible())
        for w in self.win.row_widgets():
            self.assertTrue(w.isVisible(), '行控件必须真正可见（否则 sizeHint 读 0）')
        self.win.set_snapshot(_snap(rows=[_row(), _row(), _row()]))
        h3 = self.win.height()
        # ⚠️ 2026-09-15 起「露 3 行」是常量：1 行与 3 行同高（1 行时下面留白）。
        #    这条锁要的是**刷新后高度不塌**，所以比的是自洽而不是行数差。
        self.assertEqual(h3, h1, '刷新前后高度必须一致（塌了就是老 bug 回归）')
        self.win.set_snapshot(_snap(rows=[_row()]))
        self.assertEqual(self.win.height(), h1, '刷回一行，高度必须一致')
        # 折叠「先后攻统计」后再刷新 —— 折叠态也要稳（不复原、不塌）
        self.win.stats.set_split_expanded(False)
        self.win.set_snapshot(_snap(rows=[_row(), _row()]))
        folded = self.win.height()
        self.assertLess(folded, h3, '折叠统计块后窗口应变矮')
        self.win.set_snapshot(_snap(rows=[_row(), _row()]))
        self.assertEqual(self.win.height(), folded, '折叠态刷新后高度必须不变')
        self.win.hide()

    def test_promotion_badge_fits_its_slot(self):
        """★ 升段徽章必须**放得进固定槽位** —— 放不下就会顶歪表头。

        2026-09-15 起升降段形态 = `[图]II ↑`（当前段位 + 箭头），
        比旧的「A → B」对比窄一截，槽位跟着实测走。
        """
        rows = [_row(rank_delta='up', badge_numeral='III',
                     badge_numeral_after='II', badge_text='Platinum 3',
                     rank_label_before='Platinum 3', rank_label_after='Platinum 2')]
        self.win.set_snapshot(_snap(rows=rows))
        badge = self._first_row().badge
        slot = floating.badge_slot()
        # 槽位跟着列宽走（列等比拉伸，默认宽下就比 badge_slot() 略宽）
        widths = floating.column_widths(
            floating.available_for_cols(self.win.width(), 0))
        self.assertEqual(badge.width(), widths[3])
        need = badge.sizeHint().width()
        self.assertLessEqual(need, slot,
                             '升段徽章要 %dpx，槽位只有 %dpx' % (need, slot))
        self.assertLessEqual(floating.badge_group_width(), slot,
                             '内容组不能比最小槽位还宽')

    def test_badge_slot_is_measured_not_hardcoded(self):
        """★ 槽位必须**按当前字体实测** —— 写死会在一台机器裁字、另一台留一大块空白。

        实测两个环境差 2 倍：某个无头环境的字体引擎是「每字符 1em」的桩
        （`I` 一个字占 11px、`III` 33px）；真机 Microsoft YaHei 下只有 3px / 9px。
        """
        slot = floating.badge_slot()
        # 两次调用必须一致（有缓存），且比一个图标宽得合理
        self.assertEqual(slot, floating.badge_slot())
        self.assertGreater(slot, floating.BADGE_ICON * 2)
        self.assertLess(slot, 260, '槽位再宽就说明字体解析炸了')

    def test_two_section_titles(self):
        """★ 用户 2026-09-15 定：**统计 + 最近对局 合成一个面板**，学炉石那个参考图。"""
        self.assertIn('最近对局', self.win.recent_title.title_label.text())
        self.assertNotIn('最近 3 场', self.win.recent_title.title_label.text(),
                         '「最近 3 场」已改名为「最近对局」')

    def test_backlog_count_is_gone(self):
        """★ 用户 2026-09-15：「待补 18 那个去掉」—— 标题条不再挂计数。

        未标注行本身有琥珀提示（`!` 角标 + 琥珀「点击标注」），计数是冗余。
        """
        self.win.set_snapshot(_snap(rows=[_row()], unmarked=18))
        texts = [w.text() for w in self.win.findChildren(QLabel)]
        self.assertFalse(any('待补' in t for t in texts), texts)
        self.assertFalse(hasattr(self.win.recent_title, 'right_label'))

    # ------------------------------------------------- 统计：两块的结构
    def test_stats_has_two_blocks_each_with_a_title(self):
        """★ 用户 2026-09-15 晚定：统计**上下两块** ——「总览」在上、「先后攻统计」在下。"""
        st = self.win.stats
        self.assertEqual(st.overview_title.text(), '总览')
        self.assertEqual(st.split_title.text(), '先后攻统计')
        lay = st.layout()
        self.assertLess(lay.indexOf(st.overview_title),
                        lay.indexOf(st.split_title),
                        '总览必须在先后攻统计上面')

    def _stats_text(self):
        return ({k: v.text() for k, v in self.win.stats.value_labels.items()},
                {k: v.text() for k, v in self.win.stats.sub_labels.items()})

    def test_stats_has_four_columns(self):
        """★ 统计四栏拆成上下两块各两栏：总览 = 总胜率/先手率；先后攻 = 先攻/后攻。"""
        st = self.win.stats
        self.assertEqual([k for k, _c in st.OVERVIEW_COLUMNS],
                         ['win_rate', 'first_hand'])
        self.assertEqual([k for k, _c in st.SPLIT_COLUMNS],
                         ['first', 'second'])
        self.assertEqual(set(st.value_labels),
                         {'first', 'second', 'win_rate', 'first_hand'})

    def test_first_two_columns_show_count_and_rate(self):
        """前两栏：**场数**（大数字）+ 下面一行胜率。"""
        snap = _snap()
        snap.stats = {'first_n': 18, 'first_rate': 14 / 18.0,
                      'second_n': 12, 'second_rate': 5 / 12.0}
        self.win.set_snapshot(snap)
        vals, subs = self._stats_text()
        self.assertEqual(vals['first'], '18')
        self.assertEqual(vals['second'], '12')
        self.assertIn('77.8%', subs['first'])
        self.assertIn('41.7%', subs['second'])

    def test_left_two_rate_uses_semantic_color(self):
        snap = _snap()
        snap.stats = {'first_n': 18, 'first_rate': 0.778,
                      'second_n': 12, 'second_rate': 0.417}
        self.win.set_snapshot(snap)
        subs = self.win.stats.sub_labels
        self.assertIn(theme.WIN, subs['first'].styleSheet())
        self.assertIn(theme.LOSE, subs['second'].styleSheet())

    def test_last_two_columns_show_rate_and_record(self):
        """★ 后两栏按用户给的设计：**大百分比 + 下面一行战绩**。

        总胜率下面 = 胜-负；先手率下面 = 先攻-后攻的**局数**。
        """
        snap = _snap()
        snap.stats = {'n_total': 32, 'win_rate': 0.65625, 'n_win': 21,
                      'n_lose': 11, 'first_n': 18, 'second_n': 12,
                      'first_hand_pct': 0.6}
        self.win.set_snapshot(snap)
        vals, subs = self._stats_text()
        self.assertEqual(vals['win_rate'], '65.6%')
        self.assertEqual(subs['win_rate'], '21-11')
        self.assertEqual(vals['first_hand'], '60.0%')
        self.assertEqual(subs['first_hand'], '18-12')

    def test_first_hand_rate_uses_first_hand_pct_not_initiative(self):
        """★ 回归：**先手率必须用 `first_hand_pct`，不是 `initiative_rate`**。

        `initiative_rate` 是**赢硬币率** —— 用户口径「硬币 ≠ 先后手」，
        拿错字段界面上看不出错，但数字是错的。
        """
        snap = _snap()
        snap.stats = {'first_n': 18, 'second_n': 12,
                      'first_hand_pct': 0.60,        # 先攻 18 /（18+12）
                      'initiative_n': 28, 'initiative_rate': 0.5357}  # 赢硬币
        self.win.set_snapshot(snap)
        got = self.win.stats.value_labels['first_hand'].text()
        self.assertEqual(got, '60.0%')
        self.assertNotIn('53.6', got)

    def test_stats_show_dash_when_no_data(self):
        """★ 没数据就写 `-`，**不许编 0**（编了会让人以为真打过 0 场）。"""
        self.win.set_snapshot(_snap())
        for lab in self.win.stats.value_labels.values():
            self.assertEqual(lab.text(), '-')
        # 先攻/后攻 下面那行永远带「胜率 」前缀（没数据就是「胜率 -」）
        self.assertEqual(self.win.stats.sub_labels['first'].text(), '胜率 -')
        self.assertEqual(self.win.stats.sub_labels['second'].text(), '胜率 -')
        self.assertEqual(self.win.stats.sub_labels['win_rate'].text(), '-')
        self.assertEqual(self.win.stats.sub_labels['first_hand'].text(), '-')

    def test_right_block_win_rate_uses_semantic_color(self):
        for wr, want in ((0.7, theme.WIN), (0.4, theme.LOSE)):
            snap = _snap()
            snap.stats = {'n_total': 10, 'win_rate': wr}
            self.win.set_snapshot(snap)
            self.assertIn(want,
                          self.win.stats.value_labels['win_rate'].styleSheet())

    # ------------------------------------------------------------ 表头
    def test_header_labels_are_centered_on_their_columns(self):
        """★ 用户 2026-09-15：「卡组胜负对手对手名段位时间那一行没有居中」。

        表头**一律在自己列里居中** —— 之前是「数据怎么对齐表头就怎么对齐」，
        但数据里有卡图/徽章那种视觉重心偏左的东西，表头跟着左对齐看着就散了。
        """
        hdr = self.win.findChild(floating.MatchHeader)
        labs = [w for w in hdr.findChildren(QLabel) if w.text()]
        self.assertTrue(labs)
        for lab in labs:
            self.assertTrue(lab.alignment() & Qt.AlignHCenter,
                            '表头「%s」没有居中' % lab.text())

    def test_row_data_is_centered_too(self):
        """表头与「先/后」「胜负胶囊」都是列内居中。

        ⚠️ **段位那栏的居中方式特殊**（2026-09-15 两次返工后定稿）：
        内容组成**固定宽度**再整体居中 —— 这样列被拉宽时图标仍跨行对齐
        （原来内容直接居中，图标 x 实测 106/107/112/116，用户：「段位图标没有对齐」）。
        """
        self.win.set_snapshot(_snap(rows=[_row()]))
        row = self._first_row()
        self.assertTrue(row.order_label.alignment() & Qt.AlignHCenter)
        # 徽章外层：[弹性][固定宽度的内容组][弹性]
        outer = row.badge.layout()
        self.assertIsNotNone(outer.itemAt(0).spacerItem(),
                             '徽章外层左边要有弹性空隙（内容组居中）')
        self.assertEqual(row.badge._group.width(),
                         floating.badge_group_width(),
                         '内容组宽度必须固定，否则图标跨行不齐')

    def test_header_columns_match_row_columns(self):
        """★ 表头列宽必须跟行一致 —— 否则「段位」会偏到别处。

        段位那栏留了固定槽位（升段行 `[图]III → [图]II` 比普通行宽），
        所以行里的徽章也得是固定宽。
        """
        from ui import floating
        rx = self.win.findChild(floating.MatchHeader)
        hdr = [lab.text() for lab in rx.findChildren(QLabel) if lab.text()]
        self.assertEqual(hdr, ['卡组', '结果', '先/后', '段位', '对手卡组'])
        self.win.set_snapshot(_snap(rows=[_row()]))
        widths = floating.column_widths(
            floating.available_for_cols(self.win.width(), 0))
        self.assertEqual(self._first_row().badge.width(), widths[3],
                         '徽章槽宽必须等于表头「段位」那列的宽')

    def test_renders_at_most_max_rows(self):
        """最多画 `MAX_ROWS` 行（2026-09-15 起 = 10；多出来的行由数据层不取）。"""
        rows = [_row(match_id='m%d' % i) for i in range(MAX_ROWS + 3)]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertEqual(len(self.win.row_widgets()), MAX_ROWS)
        rows = [_row(match_id='m%d' % i) for i in range(4)]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertEqual(len(self.win.row_widgets()), 4, '不足上限就画实际行数')

    def test_empty_state(self):
        self.win.set_snapshot(_snap())
        self.assertEqual(len(self.win.row_widgets()), 0)
        texts = [w.text() for w in self.win.findChildren(QLabel)]
        self.assertTrue(any('还没有' in t for t in texts), texts)

    def test_never_raises_on_empty_snapshot(self):
        self.win.set_snapshot(ui_state.UiSnapshot())

    # ---------------------------------------------------------- 行内容
    def _first_row(self):
        return self.win.row_widgets()[0]

    def test_row_columns(self):
        self.win.set_snapshot(_snap(rows=[_row()]))
        row = self._first_row()
        self.assertEqual(row.findChild(ResultChip).text(), '胜')

    def test_name_column_is_gone_player_name_in_deck_tooltip(self):
        """★ 用户 2026-09-15 晚：「去掉对手名统计」—— 列删了，名字挪进对手卡组格
        的 tooltip（信息不丢，悬停可见）。"""
        self.win.set_snapshot(_snap(rows=[_row(opp_player='幻想乡的毛玉')]))
        row = self._first_row()
        self.assertFalse(hasattr(row, 'name_label'), '对手名列应已删除')
        self.assertIn('幻想乡的毛玉', row.opp_deck_label.toolTip())
        # 没名字就不挂多余的一段
        self.win.set_snapshot(_snap(rows=[_row(match_id='m2', opp_player=None)]))
        self.assertNotIn('对手：', self._first_row().opp_deck_label.toolTip())

    def test_time_column_is_gone(self):
        """★ 用户 2026-09-15：「把时间列去掉」—— 行里没时间标签、表头没「时间」。

        数据层 `row['time']` 字段还在（core/ui_state 生成），UI 不消费；
        别为找回时刻把列加回来，要看去库里看。
        """
        self.win.set_snapshot(_snap(rows=[_row(time='19:53')]))
        row = self._first_row()
        self.assertFalse(hasattr(row, 'time_label'), '时间列应已删除')
        texts = [lab.text() for lab in row.findChildren(QLabel)]
        self.assertNotIn('19:53', texts)
        hdr = self.win.findChild(floating.MatchHeader)
        self.assertNotIn('时间',
                         [lab.text() for lab in hdr.findChildren(QLabel)])

    def test_unmarked_row_invites_and_warns(self):
        """未标注那行：对手卡组格里琥珀「未知卡组」+ `!` 角标 —— **唯一标注入口**。

        2026-09-15 用户定：文案「点击标注」→「未知卡组」，角标保留。
        """
        self.win.set_snapshot(_snap(rows=[_row(opp_known=False, opp_deck=None,
                                                opp_player=None)]))
        row = self._first_row()
        self.assertEqual(row.opp_deck_label.text(), '未知卡组')
        self.assertIn(theme.WARN, row.opp_deck_label.styleSheet())
        self.assertTrue(row.opp_thumb.is_warning_badged(), '`!` 角标要保留')

    def test_unmarked_row_has_a_light_amber_wash(self):
        """未标注行给 5% 琥珀底 —— **不铺饱和色**（DESIGN.md 反模式①）。"""
        self.win.set_snapshot(_snap(rows=[_row(opp_known=False)]))
        self.assertIn('0.05', self._first_row().styleSheet())
        self.win.set_snapshot(_snap(rows=[_row()]))
        self.assertNotIn('0.05', self._first_row().styleSheet())

    def test_suggest_cell_shows_the_suggested_deck(self):
        """★ 用户 2026-09-16：未标注 + M5 有建议 → 那格**直接显示建议的卡组**。

        图用建议卡组的图（**不半透明** —— 半透明是「兜底图」的语义）、名字白字、
        提示写明「点一下即标注」；它仍算未标注（`!` 角标保留）。
        """
        self.win.set_snapshot(_snap(rows=[_row(
            opp_known=False, opp_deck=None, opp_deck_img=None,
            suggest_name='小丑戏帮',
            suggest_img='data/deck_images_custom/Clown_Crew.webp')]))
        cell = self._first_row().opp_cell
        self.assertTrue(cell.suggested)
        self.assertEqual(cell.label.text(), '小丑戏帮')
        self.assertNotIn('未知卡组', cell.label.text())
        self.assertIn('建议', cell.toolTip())
        self.assertIn('点一下即标注', cell.toolTip())
        self.assertTrue(cell.thumb.is_warning_badged())         # 仍未标注：`!` 保留

    def test_suggest_click_marks_without_panel(self):
        """点建议态那格 = **直接标注**（emit `accept_suggest`，**不开面板**）。"""
        self.win.set_snapshot(_snap(rows=[_row(
            opp_known=False, opp_deck=None, opp_deck_img=None,
            suggest_name='小丑戏帮',
            suggest_img='data/deck_images_custom/Clown_Crew.webp')]))
        got, picks = [], []
        self.win.accept_suggest.connect(got.append)
        self.win.pick_deck.connect(lambda r, a: picks.append(r))
        self._first_row().opp_cell.clicked.emit()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['suggest_name'], '小丑戏帮')
        self.assertEqual(picks, [], '建议态不该再开面板')

    def test_without_suggestion_still_shows_unknown(self):
        """没有建议时行为**完全不变**：仍是「未知卡组」+ `!`，点了开面板。"""
        self.win.set_snapshot(_snap(rows=[_row(opp_known=False, opp_deck=None)]))
        cell = self._first_row().opp_cell
        self.assertFalse(cell.suggested)
        self.assertEqual(cell.label.text(), '未知卡组')
        self.assertTrue(cell.thumb.is_warning_badged())
        picks = []
        self.win.pick_deck.connect(lambda r, a: picks.append(r))
        cell.clicked.emit()
        self.assertEqual(len(picks), 1)

    # ------------------------------------------------------ 胜负格可点（Bug 2）
    def test_result_chip_click_emits_result_pick(self):
        """点胜负胶囊 → 发 `result_pick(row, 胶囊)`（app 弹手动修正菜单）。"""
        self.win.set_snapshot(_snap(rows=[_row(result=None)]))
        got = []
        self.win.result_pick.connect(lambda r, a: got.append((r, a)))
        chip = self._first_row().result_chip
        chip.mousePressEvent(_press(chip))
        chip.mouseReleaseEvent(_release(chip))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0]['match_id'], 'm1')
        self.assertIs(got[0][1], chip)
        self.assertEqual(chip.text(), theme.result_label(None))    # 空结果那格就是 —

    def test_my_deck_cell_click_requests_banner(self):
        """★ 09-28 用户需求：点行首我方卡组图 → 发弹确认条请求（自动弹太慢）。"""
        row = _row()
        got = []
        r = floating.MatchRow(row)
        r.my_deck_clicked.connect(lambda: got.append(1))   # 09-28 起复用此信号
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5),
                         QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                         Qt.NoModifier)
        r.my_deck_cell.mousePressEvent(ev)
        self.assertEqual(got, [1])

    def test_result_chip_drag_does_not_emit(self):
        """拖窗时按下落在这一格上也不许当成点击（同 OppDeckCell 的门槛）。"""
        self.win.set_snapshot(_snap(rows=[_row(result=None)]))
        got = []
        self.win.result_pick.connect(lambda r, a: got.append(r))
        chip = self._first_row().result_chip
        chip.mousePressEvent(_press(chip))
        chip.mouseMoveEvent(_move(chip, 40, 0))
        chip.mouseReleaseEvent(_release(chip))
        self.assertEqual(got, [])

    def test_result_chip_tooltip_says_it_is_clickable(self):
        self.win.set_snapshot(_snap(rows=[_row(result=None)]))
        chip = self._first_row().result_chip
        self.assertIn('修正', chip.toolTip())

    def test_opponent_thumb_click_emits(self):
        self.win.set_snapshot(_snap(rows=[_row(opp_known=False)]))
        got = []
        self.win.pick_deck.connect(lambda r, a: got.append((r, a)))
        self._first_row().opp_cell.clicked.emit()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0]['match_id'], 'm1')
        self.assertIs(got[0][1], self._first_row(),
                      '要带出被点的行控件（面板靠它的全局坐标对齐）')

    # ------------------------------------------------------ 等级标识格
    def _badge(self):
        badge = self._first_row().findChild(RankBadge)
        self.assertIsNotNone(badge)
        return badge

    def _badge_pics(self):
        """徽章里**有 pixmap 的 QLabel 个数**（= 图标 + 罗马数字）。"""
        return sum(1 for lab in self._badge().findChildren(QLabel)
                   if lab.pixmap() and not lab.pixmap().isNull())

    def _badge_arrow(self):
        return any(lab.text() in (floating.ARROW_UP, floating.ARROW_DOWN)
                   for lab in self._badge().findChildren(QLabel))

    def _badge_texts(self):
        return [lab.text() for lab in self._badge().findChildren(QLabel)
                if lab.text()]

    def test_badge_shows_roman_numeral_not_the_full_label(self):
        """★ 那一格是**段位图标 + 罗马数字**，不是 `Platinum 2` 全称（全称在 tooltip）。

        数字用**文字**画（用户 2026-09-15 定）—— 游戏那几张 `tier1..5` 蒙版
        分辨率太低，图像放大会糊；字体画任何缩放都锐利。
        """
        self.win.set_snapshot(_snap(rows=[_row()]))
        texts = self._badge_texts()
        self.assertIn('II', texts)
        self.assertNotIn('Platinum 2', texts)
        self.assertEqual(self._badge_pics(), 1)      # 只有图标是图，数字是文字

    def test_roman_numerals_cover_1_to_5(self):
        for want in ('I', 'II', 'III', 'IV', 'V'):
            self.win.set_snapshot(_snap(rows=[_row(badge_numeral=want)]))
            self.assertIn(want, self._badge_texts())

    def test_plain_row_has_no_arrow(self):
        """普通行：图标 + 数字，**不画箭头**。"""
        self.win.set_snapshot(_snap(rows=[_row()]))
        self.assertFalse(self._badge_arrow())

    def test_row_without_numeral_has_only_the_icon(self):
        """巅峰局不分小段 → 只有图标，没有数字。"""
        self.win.set_snapshot(_snap(rows=[_row(
            badge_icon='data/mode_icons/peak.webp', badge_text='巅峰决斗',
            badge_numeral='')]))
        self.assertEqual(self._badge_pics(), 1)
        self.assertEqual(self._badge_texts(), [])
        self.assertFalse(self._badge_arrow())

    def test_promotion_shows_previous_rank_with_up_arrow(self):
        """★ 升段（2026-09-18 五轮改）：**动之前那一档 + ↑**。

        用户：「白金2升段局如果赢了就是 `Platinum 2 ↑`，输了就是 `Platinum 2`」——
        `Platinum 1 ↑` 读作「Platinum 1 又升一段」，而 1 已是最高的那一小段，语义不成立。
        `Platinum III -> II` 那行显示 `[图]III ↑`；「旧 → 新」对比仍然不画。
        """
        rows = [_row(badge_text='Platinum 3', rank_before=(5, 3),
                     rank_after=(5, 2),
                     rank_label_before='Platinum 3', rank_label_after='Platinum 2',
                     rank_delta='up',
                     rank_icon_before='data/rank_icons/Platinum.webp',
                     rank_icon_after='data/rank_icons/Platinum.webp',
                     badge_numeral='III', badge_numeral_after='II')]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertEqual(self._badge_pics(), 1, '只画一个图标（局前的）')
        texts = self._badge_texts()
        self.assertIn('III', texts)                # 动之前那一档
        self.assertIn(floating.ARROW_UP, texts)
        self.assertNotIn('II', texts)              # 局后那一档不再显示
        self.assertNotIn('→', texts)               # 旧对比箭头已删

    def test_cross_rank_promotion_shows_previous_icon(self):
        """**跨大段位**升段（2026-09-18 五轮改）：画**动之前**那一档（`[Gold图]I ↑`）。"""
        rows = [_row(badge_icon='data/rank_icons/Gold.webp', badge_numeral='I',
                     rank_before=(4, 1), rank_after=(5, 5),
                     rank_label_before='Gold 1', rank_label_after='Platinum 5',
                     rank_delta='up',
                     rank_icon_before='data/rank_icons/Gold.webp',
                     rank_icon_after='data/rank_icons/Platinum.webp',
                     badge_numeral_after='V')]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertEqual(self._badge_pics(), 1, '只画一个图标（局前的 Gold）')
        texts = self._badge_texts()
        self.assertIn('I', texts)
        self.assertIn(floating.ARROW_UP, texts)
        self.assertNotIn('V', texts)
        need = self._first_row().badge.sizeHint().width()
        self.assertLessEqual(need, floating.badge_slot(),
                             '升段徽章要 %dpx，槽位 %dpx' % (need, floating.badge_slot()))

    def test_demotion_shows_previous_rank_with_down_arrow(self):
        """掉段（2026-09-18 五轮改）：**动之前那一档 + ↓**（与升段同一条规则）。"""
        rows = [_row(rank_before=(5, 2), rank_after=(5, 4),
                     rank_label_before='Platinum 2', rank_label_after='Platinum 4',
                     rank_delta='down', badge_numeral='II',
                     rank_icon_before='data/rank_icons/Platinum.webp',
                     badge_numeral_after='III')]
        self.win.set_snapshot(_snap(rows=rows))
        texts = self._badge_texts()
        self.assertIn('II', texts)                 # 动之前那一档
        self.assertIn(floating.ARROW_DOWN, texts)
        self.assertNotIn('III', texts)             # 局后那一档不再显示

    def test_pending_rank_shows_dots(self):
        """★ 探测窗口内的空段位显示 `…`（获取中），不是看起来像丢了。"""
        rows = [_row(badge_icon=None, badge_text='', badge_numeral='',
                     rank_pending=True)]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertIn('…', self._badge_texts())

    def test_same_does_not_draw_arrow(self):
        """`same` 不画箭头（只有升/掉才画）。"""
        rows = [_row(rank_after=(5, 2), rank_label_after='Platinum 2',
                     rank_delta='same')]
        self.win.set_snapshot(_snap(rows=rows))
        self.assertFalse(self._badge_arrow())

    def test_numerals_and_arrow_share_one_color(self):
        """★ 用户 2026-09-15 定：**箭头颜色要和数字一致**（都是 muted，同时间那一列）。

        原先数字和箭头跟着升段染绿，III 那种粗笔画染饱和绿很扎眼。
        """
        rows = [_row(rank_delta='up', badge_numeral='III',
                     badge_numeral_after='II')]
        self.win.set_snapshot(_snap(rows=rows))
        rel = [lab for lab in self._badge().findChildren(QLabel) if lab.text()]
        self.assertTrue(rel, '徽章里应该有数字/箭头文字')
        roles = set(lab.property('role') for lab in rel)
        self.assertEqual(roles, set(['muted']),
                         '数字和箭头必须同角色（同色）：%r' % roles)
        styles = [lab.styleSheet() for lab in rel]
        self.assertTrue(all(theme.WIN not in s and theme.LOSE not in s
                            for s in styles),
                        '徽章里不该再出现涨跌色：%r' % styles)

    def test_promotion_keeps_the_text_in_a_tooltip(self):
        """文字没丢 —— tooltip = **动之前那一档 +（升段/掉段）**（想核对就悬停）。"""
        rows = [_row(badge_text='Platinum 3', rank_label_before='Platinum 3',
                     rank_label_after='Platinum 2', rank_delta='up')]
        self.win.set_snapshot(_snap(rows=rows))
        tip = self._badge().toolTip()
        self.assertIn('Platinum 3', tip)           # 动之前那一档
        self.assertIn('升段', tip)

    def test_empty_badge_renders_nothing_not_placeholder(self):
        """没段位数据（25/54 没值）→ 那一格**空着**，不编一个出来。"""
        self.win.set_snapshot(_snap(rows=[_row(badge_icon=None, badge_text='',
                                               badge_numeral=None)]))
        self.assertEqual(self._badge_pics(), 0)
        self.assertFalse(self._badge_arrow())

    # ------------------------------------------------------- 不可裁字
    def test_no_label_is_vertically_clipped(self):
        """★ 回归：**任何文字控件都不能比它的字还矮**。

        踩过的坑：高度不够时标签被压扁，数字只剩中间一条，看着像 `()`。
        """
        self.win.set_snapshot(_snap(rows=[_row(), _row(match_id='m2')]))
        self.win.show()
        QApplication.processEvents()
        bad = []
        for lab in self.win.findChildren(QLabel):
            need = lab.fontMetrics().height()
            if lab.text() and lab.height() < need:
                bad.append((lab.text()[:12], lab.height(), need))
        self.assertEqual(bad, [], '这些标签被压扁了（会垂直裁字）：%r' % (bad,))
        self.win.hide()

    def test_stats_height_tracks_content_not_hardcoded(self):
        """★ 统计改上下两块后高度翻倍 —— 必须**按内容推导**（写死会垂直裁字）。"""
        self.assertGreater(self.win.stats.height(), theme.STATS_H,
                           '上下两块应比旧的单块（%d）高' % theme.STATS_H)
        for lab in self.win.stats.findChildren(QLabel):
            if lab.text():
                self.assertGreaterEqual(lab.height(),
                                        lab.fontMetrics().height())

    def test_height_fits_content(self):
        """高度是**内容推导**的，但不低于设计下限。

        ⚠️ 2026-09-15 口径变了：行区高度**恒等于 `MIN_ROWS` 行**（默认露 3 行）——
        所以「1 行」和「3 行」的窗口一样高（1 行时下面留白），
        行数再多也不长高（多出来的靠滚动/拖高）。旧行为是「按实际行数长高」。
        """
        self.win.set_snapshot(_snap(rows=[_row()]))
        h1 = self.win.height()
        self.win.set_snapshot(_snap(rows=[_row(match_id='a'),
                                          _row(match_id='b'),
                                          _row(match_id='c')]))
        h3 = self.win.height()
        self.assertEqual(h3, h1, '行数不该改窗口高（露 3 行是常量）')
        self.assertGreaterEqual(h1, theme.FLOAT_H)
        self.assertGreaterEqual(h1, theme.MATCH_ROW_H * floating.MIN_ROWS)

    # ------------------------------------------------------------ 折叠
    def test_recent_section_collapses_and_expands(self):
        """★ 用户 2026-09-15：「最近对局和先后手在左边增加一个折叠按钮」。

        点「最近对局」标题条 = 折叠：表头/行区藏起来、窗口变矮；再点恢复原高。
        """
        self.win.set_snapshot(_snap(rows=[_row()]))
        h_open = self.win.height()
        self.assertTrue(self.win.header.isVisibleTo(self.win))
        self.win.recent_title.mousePressEvent(_press(self.win.recent_title))
        self.win.recent_title.mouseReleaseEvent(_release(self.win.recent_title))
        self.assertFalse(self.win._recent_expanded)
        self.assertFalse(self.win.header.isVisibleTo(self.win))
        self.assertFalse(self.win.rows_host.isVisibleTo(self.win))
        self.assertLess(self.win.height(), h_open)
        # 再点 = 展开，回到原高
        self.win.recent_title.mousePressEvent(_press(self.win.recent_title))
        self.win.recent_title.mouseReleaseEvent(_release(self.win.recent_title))
        self.assertTrue(self.win.header.isVisibleTo(self.win))
        self.assertEqual(self.win.height(), h_open)

    def test_split_section_collapses_and_expands(self):
        """「先后攻统计」标题条折叠：两栏藏起来、块变矮、窗口跟着变矮。"""
        self.win.set_snapshot(_snap())
        st = self.win.stats
        h_open = st.minimumHeight()
        win_open = self.win.height()
        st.split_title.mousePressEvent(_press(st.split_title))
        st.split_title.mouseReleaseEvent(_release(st.split_title))
        self.assertFalse(st.is_split_expanded())
        self.assertFalse(st.split_body.isVisibleTo(st))
        self.assertFalse(st.split_hair.isVisibleTo(st))
        self.assertLess(st.minimumHeight(), h_open)
        self.assertEqual(st.minimumHeight(), st.height(),
                         '窗口布局要跟上（信号链 _fit_height 已触发重排）')
        self.assertLess(self.win.height(), win_open)
        st.split_title.mousePressEvent(_press(st.split_title))
        st.split_title.mouseReleaseEvent(_release(st.split_title))
        self.assertTrue(st.is_split_expanded())
        self.assertEqual(st.minimumHeight(), h_open)
        self.assertEqual(self.win.height(), win_open)

    def test_section_title_text_helper(self):
        """SectionTitle.text() 对齐 QLabel 的叫法（老代码少写一层 .title_label）。"""
        self.assertEqual(self.win.recent_title.text(), '最近对局')
        self.assertEqual(self.win.stats.split_title.text(), '先后攻统计')

    def test_size_and_flags(self):
        from PySide6.QtCore import Qt
        self.assertEqual(self.win.width(), floating.window_width())
        self.assertTrue(self.win.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(self.win.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_height_is_right_even_before_any_snapshot(self):
        """★ 窗口一建出来就该是内容高度 —— 不然在拿到第一份快照前被 show 会裁内容。"""
        from ui.floating import FloatingWindow
        w = FloatingWindow()
        self.assertGreater(w.height(), theme.FLOAT_H + 40,
                           '不能停在下限 112（要按内容算）')
        w.deleteLater()

    def test_panel_layout_size(self):
        """面板比原来那条标题行高；宽度 = **各列实测之和**（跟着字体走）。"""
        self.win.set_snapshot(_snap(rows=[_row()]))
        self.assertGreater(self.win.height(), theme.FLOAT_H)
        self.assertEqual(self.win.width(), floating.window_width())

    def test_window_width_tracks_the_measured_columns(self):
        """★ 窗口宽**下限** = 各列最小值 + chrome（实测，不写死）——
        否则换台机器就裁字/留空。列宽的唯一来源是 `column_widths()`。"""
        self.assertEqual(
            floating.window_width(),
            max(theme.FLOAT_W,
                floating.chrome_width() + (theme.S_SM - 2) * 4
                + floating.min_columns_width() + theme.SCROLL_W))
        # 徽章槽位受字体桩影响（offscreen 下是真机的 ~2 倍），扣掉再卡上限
        # —— 这才是「窄」的硬约束，任何环境下都成立。
        self.assertLess(floating.window_width() - floating.badge_slot(), 340,
                        '去掉徽章槽后其余各列之和不该超过 340')

    def test_order_column_maps_went_first(self):
        """★ 「先/后」列（2026-09-15 起改**硬币图标**）：正/反面；未知 -> —。

        正面 = 先攻、背面 = 后攻（用户 2026-09-15：用青眼亚白龙硬币正反面
        替换文字 先/后）；素材读不到时回退文字（见 fallback 测试）。
        """
        self.win.set_snapshot(_snap(rows=[
            _row(match_id='m1', went_first=1),
            _row(match_id='m2', went_first=0),
            _row(match_id='m3', went_first=None)]))
        rows = self.win.row_widgets()
        self.assertTrue(rows[0].order_label.pixmap()
                        and not rows[0].order_label.pixmap().isNull(),
                        '先攻应显示硬币正面图标')
        self.assertIn('先攻', rows[0].order_label.toolTip())
        self.assertTrue(rows[1].order_label.pixmap()
                        and not rows[1].order_label.pixmap().isNull(),
                        '后攻应显示硬币背面图标')
        self.assertIn('后攻', rows[1].order_label.toolTip())
        self.assertEqual(rows[2].order_label.text(), '—')

    def test_order_column_coin_assets_are_in_repo(self):
        """★ 硬币素材必须在仓库里（data/coin_icons/），且能解码成 QPixmap。"""
        from PySide6.QtGui import QPixmap
        for rel in (floating.COIN_FRONT, floating.COIN_BACK):
            path = os.path.join(PROJ, rel.replace('/', os.sep))
            self.assertTrue(os.path.isfile(path), rel)
            self.assertFalse(QPixmap(path).isNull(), rel)

    def test_order_column_falls_back_to_text_without_assets(self):
        """⚠️ 素材缺失时回退文字 先/后 —— 别让这一格空着。"""
        from ui import widgets
        orig = widgets.round_pixmap
        widgets.round_pixmap = lambda *a, **k: None
        try:
            self.win.set_snapshot(_snap(rows=[
                _row(match_id='m1', went_first=1),
                _row(match_id='m2', went_first=0)]))
        finally:
            widgets.round_pixmap = orig
        rows = self.win.row_widgets()
        self.assertEqual(rows[0].order_label.text(), '先')
        self.assertEqual(rows[1].order_label.text(), '后')

    def test_opponent_deck_name_is_shown_in_last_column(self):
        """对手卡组挪到**最后一列**：图 + 卡组名（定长省略 + 悬停看全名）。"""
        self.win.set_snapshot(_snap(
            rows=[_row(opp_deck='幻想乡的毛玉卡组名字特别长')]))
        row = self._first_row()
        self.assertTrue(row.opp_deck_label.text().endswith('…'), )
        # tooltip = 卡组全名（被截才挂）+ 对手名一行
        self.assertIn('幻想乡的毛玉卡组名字特别长',
                      row.opp_deck_label.toolTip())
        self.assertIn('对手：幻想乡的毛玉', row.opp_deck_label.toolTip())
        # 短名字不截；tooltip 只剩对手名那行
        self.win.set_snapshot(_snap(rows=[_row(opp_deck='烙印')]))
        row = self._first_row()
        self.assertEqual(row.opp_deck_label.text(), '烙印')
        self.assertEqual(row.opp_deck_label.toolTip(), '对手：幻想乡的毛玉')

    def test_opponent_deck_name_is_centered(self):
        """★ 用户 2026-09-15：「位置需要调整居中」—— 名字在定宽列内居中。"""
        self.win.set_snapshot(_snap(rows=[_row(opp_deck='烙印')]))
        self.assertTrue(self._first_row().opp_deck_label.alignment()
                        & Qt.AlignHCenter)




class TrayIconTest(unittest.TestCase):
    """系统托盘：隐藏后的唯一恢复入口（2026-09-15，用户被锁死过一次）。

    「隐藏」只 hide 窗口 —— Qt.Tool 不进任务栏，进程还活着，
    没有托盘就打不开。托盘在 FloatingWindow 内部自管理。
    ⚠️ 独立类，不继承 FloatingWindowTest（继承会把父类测试全跑一遍）。
    """

    def setUp(self):
        _app()
        self.win = FloatingWindow()

    def tearDown(self):
        self.win.deleteLater()

    def test_toggle_visible_roundtrip(self):
        """_toggle_visible 往返：显示 -> 隐藏 -> 显示。"""
        w = self.win
        w.show()
        self.assertFalse(w.isHidden())
        w._toggle_visible()
        self.assertTrue(w.isHidden())
        w._toggle_visible()
        self.assertFalse(w.isHidden())

    def test_tray_has_dashboard_entry_emitting_signal(self):
        """看板入口：托盘菜单里要有「看板…」，点了**只发信号**（开窗由 app 做）。

        ⚠️ 2026-09-18 从「统计…」改名过来的（S6 统计窗不做，改做看板）。
        """
        w = self.win
        if w._tray is None:
            self.skipTest('无系统托盘环境（CI / 无头）')
        acts = [a for a in w._tray.contextMenu().actions()
                if a.text() == '看板…']
        self.assertEqual(len(acts), 1, '托盘菜单应恰好有一个「看板…」')
        got = []
        w.dashboard_requested.connect(lambda: got.append(1))
        acts[0].trigger()
        self.assertEqual(got, [1])

    def test_dashboard_requested_signal_exists(self):
        """信号本身要被 `FloatingWindow` 声明（app 用 `connect` 接它）。"""
        self.assertTrue(hasattr(self.win, 'dashboard_requested'))

    def test_tray_optional_and_sync_text(self):
        """无托盘环境（CI/无头）不炸；有托盘则文案随可见性同步。"""
        w = self.win
        # _make_tray 在构造时已跑：要么 None（无系统托盘）要么 QSystemTrayIcon
        if w._tray is None:
            self.assertIsNone(w._sync_tray_text())      # 不炸即可
            return
        from PySide6.QtWidgets import QSystemTrayIcon
        self.assertIsInstance(w._tray, QSystemTrayIcon)
        w.show()
        self.assertEqual(w._act_toggle.text(), '隐藏悬浮窗')
        w.hide()
        self.assertEqual(w._act_toggle.text(), '显示悬浮窗')
        w._on_tray_activated(QSystemTrayIcon.Trigger)   # 左键 = 切回显示
        self.assertFalse(w.isHidden())
        self.assertEqual(w._act_toggle.text(), '隐藏悬浮窗')

    def test_tray_icon_draws(self):
        """自绘图标非空（64x64、有像素）。"""
        pm = self.win._tray_icon().pixmap(64, 64)
        self.assertEqual(pm.width(), 64)
        self.assertFalse(pm.isNull())


def _demo_snap():
    """demo 快照（9 行覆盖全形态）—— 界面断言一律用它，别拿真库。"""
    from core.ui_demo import demo_snapshot
    return demo_snapshot()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class MyDeckCellTest(unittest.TestCase):
    """行首「我方卡组」格：图 + 名字列；未知时名字列写**行内提示**。

    ★ 用户 2026-09-16：「你的提示应该和其他列一起，句子太长」-> 提示改成行内，
    文案定稿为 10 个字的 ``打开卡组编辑获取卡组``（独立提示条已删）。
    """

    def test_unknown_shows_bang_and_tooltip(self):
        """未知 -> 琥珀 `!` + tooltip（**不在这列放文字**：用户 2026-09-16）。"""
        cell = floating.MyDeckCell(None, known=False)
        self.assertFalse(cell.known)
        self.assertTrue(cell.thumb.is_warning_badged())
        self.assertIsNone(cell.label)
        self.assertEqual(cell.width(), floating.COL_MY)
        self.assertIn('卡组编辑', cell.toolTip())
        cell.deleteLater()

    def test_known_shows_image_only(self):
        """已知 -> **只有卡图**（用户 2026-09-16：不显示我方卡组名）。"""
        cell = floating.MyDeckCell(None, known=True)
        self.assertTrue(cell.known)
        self.assertFalse(cell.thumb.is_warning_badged())
        self.assertIsNone(cell.label)                  # 没有文字那一段
        self.assertEqual(cell.toolTip(), '')
        # ⚠️ 宽度**必须等于列宽**（缩了会让整行错位 —— 2026-09-16 实机踩过）
        self.assertEqual(cell.width(), floating.COL_MY)
        cell.deleteLater()


class ColumnWidthTest(unittest.TestCase):
    """列宽只有一个来源（`column_widths`）；拖宽时**所有列按比例一起拉伸**。

    ⚠️ 口径改过一次（2026-09-15）：第一版「只有对手卡组名吃弹性」被用户否掉，
    原话「左右拉伸不能只拉伸卡组名，所有列一起拉伸」。
    """

    def test_all_columns_fixed(self):
        """★ 口径（2026-10-05 用户定稿）：五列全部钉死基准宽 —— 不追求长名
        全显示，最小列宽贴着对手卡组字的右边；拉宽窗口不再分配给列。"""
        base = floating.min_columns_width() + 200
        a = floating.column_widths(base)
        b = floating.column_widths(base + 120)
        self.assertEqual(a, b, '列宽不该随窗口宽变')
        self.assertEqual(b, floating.column_widths(0), '任意宽度都是同一份基准')

    def test_every_header_caption_fits_its_column(self):
        """★ 表头文字必须放得下 —— 「结果」曾经要 22px 只给 20px，被裁掉边（实测）。"""
        caps = ('卡组', '结果', '先/后', '段位', '对手卡组')
        widths = floating.column_widths(floating.min_columns_width() + 200)
        for cap, w in zip(caps, widths):
            lab = widgets.label(cap, 'label')
            need = lab.fontMetrics().horizontalAdvance(cap)
            self.assertLessEqual(need, w, '表头「%s」需要 %dpx，只给了 %dpx'
                                 % (cap, need, w))

    def test_never_below_the_floor_width(self):
        widths = floating.column_widths(0)
        self.assertEqual(
            widths[4],
            floating.COL_OPP_THUMB + floating.OPP_GAP + floating.COL_OPP_NAME)

    def test_widths_are_monotonic(self):
        prev = 0
        for avail in range(0, 600, 20):
            cur = floating.column_widths(avail)[4]
            self.assertGreaterEqual(cur, prev)
            prev = cur

    def test_min_width_reserves_the_scrollbar(self):
        """★ 下限要把滚动条宽度算进去 —— 否则最窄时最右列被切 10px（量出来的）。"""
        self.assertGreaterEqual(
            floating.window_width(),
            floating.chrome_width() + (theme.S_SM - 2) * 4
            + floating.min_columns_width() + theme.SCROLL_W)
        # 滚动条可见时，列之和必须放得下（不能溢出）
        avail = floating.available_for_cols(floating.window_width(),
                                            theme.SCROLL_W)
        widths = floating.column_widths(avail)
        self.assertLessEqual(sum(widths) + (theme.S_SM - 2) * 4, avail)

    def test_my_deck_cell_width_equals_column(self):
        """★ 回归锁（2026-09-16 实机全错位）：行首格宽**必须等于列宽**。

        踩过的坑：已知行只画图就把格宽缩成 26，而表头那列是按提示文字量的
        （~142）→ 行里后续控件整体左移 100+px，表头与数据全对不上。
        """
        win = floating.FloatingWindow()
        snap = _demo_snap()
        snap.recent[0]['my_deck'] = None         # 一行未知、一行已知都覆盖
        snap.recent[0]['my_deck_img'] = None
        win.set_snapshot(snap)
        win.show()
        _app().processEvents()
        widths = floating.column_widths(
            floating.available_for_cols(win.width(), win._scrollbar_w()),
            opp_name_w=getattr(win, '_opp_name_w', None))
        for row in win._rows:
            self.assertEqual(row.my_deck_cell.width(), widths[0],
                             '行首格宽 != 列宽 -> 整行会错位')
        # 再按实际几何核一遍：行里各控件的 x 必须与表头逐列相同
        hdr = win.header.layout()
        hx = [hdr.itemAt(i).widget().x() for i in range(4)]
        for row in win._rows:
            lay = row.layout()
            rx = [lay.itemAt(i).widget().x() for i in range(4)]
            self.assertEqual(rx, hx, '表头与行各列 x 必须逐列相同')
        win.close()

    def test_header_and_rows_use_the_same_widths(self):
        """★ 表头与行的列宽必须逐列相等（两边各算一次就会错开）。"""
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        widths = floating.column_widths(
            floating.available_for_cols(win.width(), 0),
            opp_name_w=getattr(win, '_opp_name_w', None))
        head = win.findChild(floating.MatchHeader)
        for i, lab in enumerate(head.findChildren(QLabel)):
            self.assertEqual(lab.width(), widths[i], '表头第 %d 列宽不对' % i)
        for row in win.row_widgets():
            self.assertEqual(row.opp_cell.width(), widths[4])
        win.hide()

    def test_wide_window_unfolds_the_deck_name(self):
        """拖宽 → 名字列变宽 → 省略变少（这一格是弹性的意义所在）。"""
        from core.ui_demo import demo_recent
        row_data = demo_recent()[0]
        cell = floating.OppDeckCell(row_data.get('opp_deck_img'),
                                    bool(row_data.get('opp_deck')),
                                    row_data.get('opp_deck') or '', '')
        cell.apply_width(floating.COL_OPP)
        narrow = cell.label.width()
        cell.apply_width(floating.COL_OPP + 120)
        self.assertEqual(cell.label.width(), narrow + 120)
        self.assertEqual(cell.width(), floating.COL_OPP + 120)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ResizeTest(unittest.TestCase):
    """缩放热区 / 高度三态（设计 §4.1、§5）。"""

    def _win(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        return win

    def test_edges_are_detected_on_four_sides(self):
        win = self._win()
        w, h = win.width(), win.height()
        self.assertEqual(win.edges_at(QPoint(2, h // 2)), Qt.LeftEdge)
        self.assertEqual(win.edges_at(QPoint(w - 2, h // 2)), Qt.RightEdge)
        self.assertEqual(win.edges_at(QPoint(w // 2, 2)), Qt.TopEdge)
        self.assertEqual(win.edges_at(QPoint(w // 2, h - 2)), Qt.BottomEdge)
        win.hide()

    def test_corners_give_two_edges_at_once(self):
        win = self._win()
        e = win.edges_at(QPoint(2, 2))
        self.assertTrue(e & Qt.LeftEdge and e & Qt.TopEdge)
        win.hide()

    def test_center_is_not_a_resize_zone(self):
        """窗中间按下去仍然是**拖动窗口**，不能变成缩放。"""
        win = self._win()
        self.assertFalse(win.edges_at(QPoint(win.width() // 2,
                                             win.height() // 2)))
        win.hide()

    def test_cursor_follows_the_edge(self):
        self.assertEqual(floating.cursor_for(Qt.LeftEdge), Qt.SizeHorCursor)
        self.assertEqual(floating.cursor_for(Qt.BottomEdge), Qt.SizeVerCursor)
        self.assertEqual(floating.cursor_for(Qt.LeftEdge | Qt.TopEdge),
                         Qt.SizeFDiagCursor)
        self.assertEqual(floating.cursor_for(Qt.RightEdge | Qt.BottomEdge),
                         Qt.SizeFDiagCursor)
        self.assertEqual(floating.cursor_for(Qt.RightEdge | Qt.TopEdge),
                         Qt.SizeBDiagCursor)
        self.assertEqual(floating.cursor_for(Qt.Edges()), Qt.ArrowCursor)

    def test_begin_resize_goes_through_the_injected_starter(self):
        """测试里**不真调系统** —— 用钩子收 `Edges`。"""
        win = self._win()
        seen = []
        win._resize_starter = lambda edges: seen.append(edges) or True
        self.assertTrue(win.begin_resize(Qt.RightEdge))
        self.assertEqual(seen, [Qt.RightEdge])
        win.hide()

    def test_user_height_survives_a_refresh(self):
        """★ 拖过的尺寸不能被下一次刷新弹回去（加缩放最大的坑）。"""
        win = self._win()
        win.begin_resize(Qt.BottomEdge)
        win._user_size = QSize(win.width(), win.height() + 68)
        win.resize(win._user_size)
        tall = win.height()
        win.set_snapshot(_demo_snap())            # 刷新一次
        self.assertEqual(win.height(), tall)
        win.hide()

    def test_title_triangle_click_toggles(self):
        """★ 09-27 事故锁：**鼠标**点左缘三角必须真的触发折叠。

        程序性 set_recent_expanded 一直正常，坏的是事件路径——
        三角内的按下曾被 ignore 冒泡给窗口，窗口 accept 拖动/缩放并抓走鼠标，
        release 回不到标题条 → 折叠永远不触发（用户：按了没反应）。
        """
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        win = self._win()
        title = win.recent_title
        fired = []
        title.toggled.connect(lambda v: fired.append(v))
        before = win.height()
        pos = QPoint(2, title.height() // 2)     # 左缘三角内
        QTest.mousePress(title, Qt.LeftButton, Qt.NoModifier, pos)
        QTest.mouseRelease(title, Qt.LeftButton, Qt.NoModifier, pos)
        self.assertEqual(fired, [False], '三角点击必须触发折叠信号')
        self.assertLess(win.height(), before)
        QTest.mousePress(title, Qt.LeftButton, Qt.NoModifier, pos)
        QTest.mouseRelease(title, Qt.LeftButton, Qt.NoModifier, pos)
        self.assertEqual(fired, [False, True])   # 再点展开
        # 三角之外（标题条其余部分）点击**不**触发折叠（纯拖窗区）
        fired.clear()
        outside = QPoint(title.width() - 10, title.height() // 2)
        QTest.mousePress(title, Qt.LeftButton, Qt.NoModifier, outside)
        QTest.mouseRelease(title, Qt.LeftButton, Qt.NoModifier, outside)
        self.assertEqual(fired, [])
        win.hide()

    def test_folding_shrinks_and_unfolding_restores(self):
        """折叠 → 缩到内容高；展开 → 恢复用户拖的高度。"""
        win = self._win()
        win.begin_resize(Qt.BottomEdge)
        win._user_size = QSize(win.width(), win.height() + 68)
        win.resize(win._user_size)
        tall = win.height()
        win.set_recent_expanded(False)
        self.assertLess(win.height(), tall)
        win.set_recent_expanded(True)
        self.assertEqual(win.height(), tall)
        win.hide()

    def test_untouched_window_still_auto_fits(self):
        """没拖过 = 完全保持今天的行为（内容变，高度跟着变）。"""
        win = self._win()
        before = win.height()
        win.set_recent_expanded(False)
        self.assertLess(win.height(), before)
        win.hide()

    def test_min_size_follows_the_content(self):
        win = self._win()
        self.assertEqual(win.minimumWidth(), floating.window_width())
        self.assertGreaterEqual(win.minimumHeight(),
                                theme.MATCH_ROW_H * floating.MIN_ROWS)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ScrollTest(unittest.TestCase):
    """行区滚动（D2/D3）：装得下不出条，装不下才出；上限 10 行。"""

    def _win(self, n_rows=9):
        app = _app()
        snap = _demo_snap()
        snap.recent = snap.recent[:n_rows]
        win = floating.FloatingWindow()
        win.set_snapshot(snap)
        win.show()
        app.processEvents()
        return app, win

    def test_max_rows_is_ten(self):
        self.assertEqual(floating.MAX_ROWS, 10)

    def test_default_height_scrolls_when_more_than_three_rows(self):
        app, win = self._win(9)
        self.assertTrue(win.scroll.verticalScrollBar().isVisible(),
                        '9 局 > 默认露 3 行，应该出滚动条')
        win.hide()

    def test_no_scrollbar_when_it_fits(self):
        app, win = self._win(3)
        self.assertFalse(win.scroll.verticalScrollBar().isVisible(),
                         '只有 3 局，不该出滚动条')
        win.hide()

    def test_rows_host_is_inside_the_scroll_area(self):
        app, win = self._win(3)
        self.assertIs(win.scroll.widget(), win.rows_host)
        win.hide()

    def test_dragging_down_shows_more_rows(self):
        """★ D3：往下拖 = 同时看到更多行（拖动上限 10 行）。"""
        app, win = self._win(9)
        before = win.scroll.viewport().height()
        win.begin_resize(Qt.BottomEdge)
        tall = win.height() + theme.MATCH_ROW_H * 7
        win._user_size = QSize(win.width(), tall)
        win.resize(win.width(), tall)
        app.processEvents()
        self.assertGreater(win.scroll.viewport().height(), before)
        self.assertFalse(win.scroll.verticalScrollBar().isVisible(),
                         '拖到装得下 9 行时滚动条应当消失')
        win.hide()

    def test_renders_at_most_ten_rows(self):
        app, win = self._win(9)
        self.assertEqual(len(win.row_widgets()), 9)
        win.hide()

    def test_folding_hides_the_scroll_area(self):
        app, win = self._win(9)
        win.set_recent_expanded(False)
        self.assertFalse(win.scroll.isVisible())
        win.set_recent_expanded(True)
        self.assertTrue(win.scroll.isVisible())
        win.hide()

    def test_empty_state_keeps_the_three_row_height(self):
        app, win = self._win(0)
        self.assertGreaterEqual(win.height(),
                                theme.MATCH_ROW_H * floating.MIN_ROWS)
        win.hide()


def _mouse(kind, local, glob):
    """造一个带**全局坐标**的鼠标事件（窗口拖动/缩放都靠全局位移判断）。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    ev_type = (QEvent.MouseButtonPress if kind == 'press'
               else QEvent.MouseButtonRelease)
    return QMouseEvent(ev_type, QPointF(*local), QPointF(*glob),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class MouseTrackingTest(unittest.TestCase):
    """★ 2026-09-15 用户实测：「光标没变」—— 根因是不按键时收不到 move 事件。

    Qt 默认 `mouseTracking=False`：**没有按住键就不发 move 事件**，
    所以「移到边缘换缩放光标」永远不触发。窗口和**所有子控件**都要开。
    """

    def _win(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        return app, win

    def test_window_tracks_the_mouse(self):
        app, win = self._win()
        self.assertTrue(win.hasMouseTracking())
        win.hide()

    def test_every_child_tracks_the_mouse(self):
        app, win = self._win()
        kids = win.findChildren(QWidget)
        self.assertTrue(kids, '窗口里不该没有子控件')
        off = [k.objectName() or k.__class__.__name__
               for k in kids if not k.hasMouseTracking()]
        self.assertEqual(off, [], '这些子控件没开 mouseTracking，事件到不了窗口')
        win.hide()

    def test_cursor_updates_on_edge_move(self):
        """移到边缘 → 缩放光标；回到窗中间 → 箭头。"""
        app, win = self._win()
        win._update_cursor(QPoint(2, win.height() // 2))
        self.assertEqual(win.cursor().shape(), Qt.SizeHorCursor)
        win._update_cursor(QPoint(win.width() // 2, win.height() - 2))
        self.assertEqual(win.cursor().shape(), Qt.SizeVerCursor)
        win._update_cursor(QPoint(win.width() // 2, win.height() // 2))
        self.assertEqual(win.cursor().shape(), Qt.ArrowCursor)
        win.hide()

    def test_cursor_is_only_set_when_it_changes(self):
        """★ 光标**只在形状变了才设** —— 每帧设一遍在 Windows 上会把光标闪花。"""
        app, win = self._win()
        calls = []
        original = win.setCursor
        win.setCursor = lambda shape: (calls.append(shape), original(shape))[1]
        for _ in range(5):                       # 同一个位置连来 5 次 move
            win._update_cursor(QPoint(win.width() // 2, win.height() // 2))
        self.assertEqual(len(calls), 1, '相同光标不该重复 setCursor：%s' % calls)
        win._update_cursor(QPoint(2, win.height() // 2))
        self.assertEqual(len(calls), 2)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class MoveTest(unittest.TestCase):
    """★ 拖窗走**系统原生移动**（2026-09-15 用户：「快速拖动 UI 会闪烁」）。

    原来是自己算位移每帧 `move()` 一个半透明无边框窗 —— 每次 SetWindowPos 都要
    重新合成，快了就闪。缩放早就走系统（不闪），移动也得交出去。
    """

    def _win(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        return app, win

    def test_press_in_the_middle_starts_a_system_move(self):
        app, win = self._win()
        called = []
        win._move_starter = lambda: called.append(1) or True
        middle = (win.width() // 2, win.height() // 2)
        win.mousePressEvent(_mouse('press', middle, (500, 300)))
        self.assertEqual(called, [1])
        self.assertIsNone(win._drag_from, '走系统移动时不该再自己算位移')
        win.hide()

    def test_falls_back_to_manual_move_when_unsupported(self):
        """系统不支持（无头 / 老平台）时回退到手动 move，功能不能没有。"""
        app, win = self._win()
        win._move_starter = lambda: False
        middle = (win.width() // 2, win.height() // 2)
        win.mousePressEvent(_mouse('press', middle, (500, 300)))
        self.assertIsNotNone(win._drag_from, '回退路径必须还能拖')
        win.hide()

    def test_edges_still_resize_not_move(self):
        """边缘按下仍然是**缩放**，不会被移动抢走。"""
        app, win = self._win()
        moved = []
        win._move_starter = lambda: moved.append(1) or True
        win.mousePressEvent(_mouse('press', (2, win.height() // 2), (500, 300)))
        self.assertEqual(moved, [])
        self.assertTrue(win._resizing)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class CornerRadiusTest(unittest.TestCase):
    """★ 四个角的圆角必须一致（2026-09-15 用户实测：「上面两个角和下面不一样」）。

    根因：「总览」标题条的 **SURFACE1 方角方块**盖住了窗口圆角（它紧贴窗口上缘），
    于是上面两个角是方的、下面两个是 10px 圆角。修法是给标题条带上圆角。
    判据 = 从角点沿对角线走，**开头连续全透明**的像素个数；四角必须相同
    （方角只有 0~1 个，10px 圆角有 3 个）。用「全透明」而不是「不透明」
    是因为窗口底是 94% 不透明的，最深处也到不了 255。
    """

    def _profile(self, win):
        img = win.grab().toImage()
        w, h = img.width(), img.height()

        def leading_clear(x0, y0, sx, sy):
            for i in range(20):
                if img.pixelColor(x0 + sx * i, y0 + sy * i).alpha() > 0:
                    return i
            return 999

        return [leading_clear(0, 0, 1, 1), leading_clear(w - 1, 0, -1, 1),
                leading_clear(0, h - 1, 1, -1),
                leading_clear(w - 1, h - 1, -1, -1)]

    def test_all_four_corners_are_rounded_the_same(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        prof = self._profile(win)
        self.assertEqual(len(set(prof)), 1,
                         '四角圆角不一致（左上/右上/左下/右下 = %s）' % prof)
        self.assertGreaterEqual(prof[0], 2, '圆角太小，等于方角')
        win.hide()

    def test_corners_stay_round_when_folded(self):
        """折叠「最近对局」后，下面的角由标题条补圆角，也要一致。"""
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        win.set_recent_expanded(False)
        app.processEvents()
        prof = self._profile(win)
        self.assertEqual(len(set(prof)), 1, '折叠后四角不一致：%s' % prof)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SectionTitleClickTest(unittest.TestCase):
    """★ 折叠改回**只点左缘三角按钮**（2026-09-15 用户最终口径）。

    演进：① 原来 press 直接翻转 + `accept()` → 一按就折、窗还拖不动；
    ② 改成「整条可点 + 抬起判位移」→ 用户否掉，要求「还是改回点折叠按钮」；
    ③ 现在：**只有左缘 `TOGGLE_W` 那一条是按钮**，标题条其余部分纯拖窗口。
    """

    def _title(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        return app, win, win.recent_title

    def test_click_toggles(self):
        app, win, title = self._title()
        seen = []
        title.toggled.connect(seen.append)
        title.mousePressEvent(_mouse('press', (20, 10), (500, 300)))
        title.mouseReleaseEvent(_mouse('release', (20, 10), (500, 300)))
        self.assertEqual(seen, [False], '点三角应当折叠')
        win.hide()

    def test_click_on_the_rest_of_the_bar_does_not_toggle(self):
        """★ 标题条**其余部分**点一下不折叠（那里是拖窗区）。"""
        app, win, title = self._title()
        seen = []
        title.toggled.connect(seen.append)
        far = title.width() - 10
        title.mousePressEvent(_mouse('press', (far, 10), (500, 300)))
        title.mouseReleaseEvent(_mouse('release', (far, 10), (500, 300)))
        self.assertEqual(seen, [], '标题条其余部分是拖窗区，不该折叠')
        win.hide()

    def test_drag_from_the_toggle_does_not_toggle(self):
        """从三角上开始拖（位移超阈值）也不算点击。"""
        app, win, title = self._title()
        seen = []
        title.toggled.connect(seen.append)
        title.mousePressEvent(_mouse('press', (10, 10), (500, 300)))
        title.mouseReleaseEvent(_mouse('release', (10, 10), (620, 390)))
        self.assertEqual(seen, [], '拖窗口不该折叠')
        win.hide()

    def test_press_does_not_swallow_the_window_drag(self):
        """按标题条 = 可以拖窗口（事件不能被标题条吃成「已处理」）。

        Qt 只在子控件**忽略**按下事件时才把它冒泡给父窗口 —— 旧实现
        `ev.accept()` 掉了，所以从标题条根本拖不动窗。
        """
        app, win, title = self._title()
        # x=40 在三角命中区（TOGGLE_W=32）之外 —— 拖窗区必须继续 ignore 冒泡
        ev = _mouse('press', (40, 10), (500, 300))
        title.mousePressEvent(ev)
        self.assertFalse(ev.isAccepted(),
                         '拖窗区不许 accept 按下事件（否则窗口拖不动）')
        # 三角区内（x<=32）则相反：必须 accept 自己消费（09-27 折叠事故锁）
        ev2 = _mouse('press', (10, 10), (500, 300))
        title.mousePressEvent(ev2)
        self.assertTrue(ev2.isAccepted(),
                        '三角内按下要 accept —— ignore 会让窗口抢走折叠点击')
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class BadgeAlignTest(unittest.TestCase):
    """★ 段位图标要**跨行对齐**（2026-09-15 用户：「段位图标没有对齐」）。

    实测原来是 106/107/112/116 —— 因为内容整体居中，带箭头的行比不带的宽。
    现在图标贴槽左缘，起点必须是同一个数。
    """

    def test_icons_start_at_the_same_x_in_every_row(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        xs = []
        for row in win.row_widgets():
            for lab in row.badge.findChildren(QLabel):
                pm = lab.pixmap()
                if pm is not None and not pm.isNull():
                    xs.append(lab.mapTo(win, QPoint(0, 0)).x())
                    break
        self.assertGreaterEqual(len(xs), 3, 'demo 里应当有多行带段位图标')
        self.assertEqual(len(set(xs)), 1, '图标起点必须一致，实测：%s' % xs)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SizeLimitTest(unittest.TestCase):
    """★ 2026-09-15 用户定的两条尺寸上限。"""

    def _win(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        win.begin_resize(Qt.BottomEdge)
        return app, win

    def test_height_is_capped_at_max_rows(self):
        """上下最多拉到「10 局全显示」—— 再高也没内容，只留白。"""
        app, win = self._win()
        win.resize(win.width(), win._max_height() + 200)
        app.processEvents()
        self.assertLessEqual(win.height(), win._max_height())
        self.assertGreaterEqual(win.height(), win._content_height(),
                                '不能比下限还矮')
        win.hide()

    def test_width_is_capped_when_injected(self):
        """左右最多拉到「最长的卡组名放得下」（上限由 app 注入）。"""
        app, win = self._win()
        cap = win.width() + 120
        win.set_max_width(cap)
        win.resize(cap + 300, win.height())
        app.processEvents()
        self.assertLessEqual(win.width(), cap)
        self.assertEqual(win.maximumWidth(), cap)
        win.hide()

    def test_rows_keep_the_default_height(self):
        """每行行高固定 `MATCH_ROW_H` —— 拉高窗口不该把行撑胖。"""
        app, win = self._win()
        win.resize(win.width(), win._max_height())
        app.processEvents()
        for row in win.row_widgets():
            self.assertEqual(row.height(), theme.MATCH_ROW_H)
        win.hide()

    def test_resize_relayouts_are_coalesced(self):
        """★ 拉边时连续 resize 必须**限流**（不能每个 resize 重排一次）。

        2026-09-15 两次实测（诊断计数）：先是每次 resize 同步重排 → 87 次/秒 +
        30~116 次重绘/秒（闪烁）；改成 0ms 定时器仍无效（系统把每个 resize 放在
        各自的事件循环轮次里，0ms 定时器每次都立刻执行）→ 现在用**限流**（40ms）。
        """
        import time
        app, win = self._win()
        calls = []
        original = win._relayout_columns
        win._relayout_columns = lambda: (calls.append(1), original())[1]
        for i in range(20):
            win.resize(win.width() + i, win.height())
        self.assertEqual(calls, [], '手势期间不该同步重排（要限流）')
        deadline = time.time() + 0.5
        while time.time() < deadline and not calls:
            app.processEvents()
            time.sleep(0.01)
        self.assertLessEqual(len(calls), 2,
                             '20 次 resize 应限流成 1~2 次重排，实际 %d' % len(calls))
        # 松手时必须补一次精确重排（否则列宽停在限流那一刻）
        before = len(calls)
        win._end_resize()
        self.assertEqual(len(calls), before + 1, '手势结束要补一次精确重排')
        win.hide()

    def test_name_column_is_fixed_regardless_of_names(self):
        """★ 口径（2026-10-05 用户）：**不追求长名全显示** —— 卡组名列恒为
        `COL_OPP_NAME` 定宽省略（悬停看全名），名字池再长也不撑宽列。"""
        names = ['短名', 'A Much Longer Deck Name Here']
        cap = floating.max_window_width(names)
        widths = floating.column_widths(
            floating.available_for_cols(cap, theme.SCROLL_W))
        self.assertEqual(widths[4],
                         floating.COL_OPP_THUMB + floating.OPP_GAP
                         + floating.COL_OPP_NAME)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class WindowButtonsTest(unittest.TestCase):
    """★ 用户 2026-09-15：总览条右上加 `-`（最小化到托盘）/ `□`（最大化-还原）/ `×`（关闭）。

    ⚠️ 字形是**自绘**的（`WindowButton.paintEvent`）—— 字体缺 □ 这类几何字符会出豆腐块，
    这条路（折三角）已经踩过一次。
    """

    def _win(self):
        app = _app()
        win = floating.FloatingWindow()
        win.set_snapshot(_demo_snap())
        win.show()
        app.processEvents()
        return app, win

    def test_three_buttons_live_on_the_overview_bar(self):
        app, win = self._win()
        btns = win.stats.overview_title.win_buttons
        self.assertEqual(sorted(btns), ['close', 'max', 'min'])
        for kind, btn in btns.items():
            self.assertTrue(btn.isVisibleTo(win), '%s 按钮没显示' % kind)
        win.hide()

    def test_title_stays_centered_with_buttons(self):
        """按钮占位不能把「总览」挤偏（左边补了等宽空白）。"""
        app, win = self._win()
        title = win.stats.overview_title
        label = title.title_label
        tl = label.mapTo(title, QPoint(0, 0))
        center = tl.x() + label.width() / 2.0
        self.assertAlmostEqual(center, title.width() / 2.0, delta=1.0)
        win.hide()

    def test_min_hides_to_tray(self):
        app, win = self._win()
        win.stats.overview_title.win_buttons['min'].clicked.emit()
        app.processEvents()
        self.assertFalse(win.isVisible(), '「-」应当隐藏（托盘是唯一恢复入口）')

    def test_max_toggles_and_restores(self):
        app, win = self._win()
        win.set_max_width(win.width() + 120)
        default = (win.width(), win.height())
        win.stats.overview_title.win_buttons['max'].clicked.emit()
        app.processEvents()
        self.assertTrue(win.is_maximized())
        self.assertEqual(win.width(), win.maximumWidth())
        self.assertEqual(win.height(), win._max_height())
        win.stats.overview_title.win_buttons['max'].clicked.emit()
        app.processEvents()
        self.assertFalse(win.is_maximized())
        self.assertEqual((win.width(), win.height()), default, '再点要还原')
        win.hide()

    def test_close_only_emits(self):
        """「×」只发信号 —— 怎么关（托盘 / 退出）由 app 按设置决定，窗口自己不读设置。"""
        app, win = self._win()
        got = []
        win.close_requested.connect(lambda: got.append(1))
        win.stats.overview_title.win_buttons['close'].clicked.emit()
        self.assertEqual(got, [1])
        win.hide()

    def test_button_click_does_not_drag_the_window(self):
        """按按钮不许同时开始拖窗（按钮 accept 掉按下）。"""
        app, win = self._win()
        btn = win.stats.overview_title.win_buttons['close']
        ev = _mouse('press', (5, 5), (500, 300))
        btn.mousePressEvent(ev)
        self.assertTrue(ev.isAccepted())
        self.assertIsNone(win._drag_from)
        win.hide()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class TickWidgetsTest(unittest.TestCase):
    """★ 用户 2026-09-15：「单选项打不了勾、『不再提示』只是变颜色」。

    根因：QSS 的 `::indicator:checked` 只能改**底色/边框**，画勾得引图片资源。
    现在 `widgets.TickBox` / `DotRadio` **自己画**勾 / 圆点 —— 这里按**像素**锁住它：
    勾选后，指示器矩形里必须出现「勾的颜色」（`theme.CANVAS`）。
    """

    def _mark_pixels(self, widget) -> int:
        """指示器矩形里有多少个像素是「勾 / 点」的颜色。"""
        from ui import widgets as W
        img = widget.grab().toImage()
        r = W._indicator_rect(widget)
        want = theme.CANVAS.lower()
        return sum(1 for y in range(r.top(), r.bottom() + 1)
                   for x in range(r.left(), r.right() + 1)
                   if img.pixelColor(x, y).name() == want)

    def test_checkbox_draws_a_tick(self):
        app = _app()
        box = widgets.TickBox('不再提示')
        box.show()
        app.processEvents()
        self.assertEqual(self._mark_pixels(box), 0, '没勾时不该有勾')
        box.setChecked(True)
        app.processEvents()
        self.assertGreater(self._mark_pixels(box), 3, '勾选后要真的画出一个勾')
        box.deleteLater()

    def test_radio_draws_a_tick_not_a_dot(self):
        """★ 用户 2026-09-15：「圆点太丑，换成打勾」—— 单选项的指示器也画勾。"""
        app = _app()
        rb = widgets.DotRadio('退出主程序')
        rb.show()
        app.processEvents()
        self.assertEqual(self._mark_pixels(rb), 0, '没选时不该有勾')
        rb.setChecked(True)
        app.processEvents()
        self.assertGreater(self._mark_pixels(rb), 3, '选中后要真的画出一个勾')
        rb.deleteLater()

    def test_radio_indicator_is_a_square_like_the_checkbox(self):
        """方框（不是圆）—— 用户嫌圆点丑。"""
        app = _app()
        rb = widgets.DotRadio('退出主程序')
        box = widgets.TickBox('不再提示')
        for w in (rb, box):
            w.show()
        app.processEvents()
        self.assertEqual(widgets._indicator_rect(rb).size(),
                         widgets._indicator_rect(box).size(),
                         '单选项与勾选框的指示器尺寸要一致')
        rb.deleteLater()
        box.deleteLater()

    def test_close_dialog_uses_them_and_is_compact(self):
        """关闭对话框要用上自绘控件，并且**要小**（用户：「把关闭悬浮窗的 ui 做小」）。"""
        from ui import app as app_mod
        dlg = app_mod.CloseDialog()
        self.assertIsInstance(dlg.rb_tray, widgets.DotRadio)
        self.assertIsInstance(dlg.rb_quit, widgets.DotRadio)
        self.assertIsInstance(dlg.cb_remember, widgets.TickBox)
        dlg.show()
        _app().processEvents()
        self.assertLessEqual(dlg.width(), 340, '对话框太宽')
        self.assertLessEqual(dlg.height(), 170, '对话框太高：%d' % dlg.height())
        dlg.deleteLater()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class RetranslateInPlaceTest(unittest.TestCase):
    """悬浮窗切语言**原地重译**：窗口不关、快照还在、文案换语言。"""

    def setUp(self):
        _app()
        self.win = floating.FloatingWindow()

    def tearDown(self):
        self.win.deleteLater()

    def test_retranslate_in_place(self):
        from ui.i18n import reset_lang
        self.win.set_snapshot(_demo_snap())
        n_rows = len(self.win._rows)
        w0 = self.win.width()
        s = floating.FloatingWindow._settings()
        s.setValue('ui/lang', 'en')
        reset_lang()
        try:
            self.win.retranslate()
        finally:
            s.setValue('ui/lang', 'cn')
            reset_lang()
        self.assertEqual(self.win.recent_title.text(), 'Recent Duels')
        self.assertEqual(self.win.windowTitle(), 'Master Duel Stats')
        self.assertEqual(len(self.win._rows), n_rows, '快照行不能丢')
        self.assertEqual(self.win.width(), w0, '窗口几何不能变')


if __name__ == '__main__':
    unittest.main()
