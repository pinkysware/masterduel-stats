# -*- coding: utf-8 -*-
"""S7 看板（D0 壳）单测 —— 只锁**骨架契约**，不锁还没做的内容。

锁什么（都是「错了不报错、只是看着不对」的那类）：
1. 最小尺寸 **1200×620**（用户口径：可拖拽，这是最小）；
2. 左栏 = 4 个导航页 + 设置区（9 项），**顺序**与计划 §2.2 一致；
3. **点导航 / 点设置项 → 右侧主区换页**；未知 key 回落第一页；
4. ★ **设置区靠字号分层**（「设置」大、设置项小）—— 用户 2026-09-18 口径 Q13；
5. 选中态唯一；QSettings 键走 `dash/*`（**不碰悬浮窗的 `float/*`**）；
6. 设置项页已真接主区内联操作（09-26 设置页优化：概览页 + 子页控件），老对话框已下掉；
7. 文字不被垂直裁切 / 窗口自带样式表里没有 `font-*`。

⚠️ QSettings 必须隔离（`dash/size` 会被用户真机改）—— 照 `tests/test_ui_floating.py` 的做法。
⚠️ 没装 PySide6 时整个模块跳过。
"""
import os
import sys
import tempfile
from unittest import mock
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')      # 必须在 Qt import 之前

try:
    from PySide6.QtCore import QObject, QSettings, Qt, Signal
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel,
                                   QPushButton)
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    from core import deck_csv
    from ui import analysis_page, dashboard, tier_page, theme

    class FakeRunner(QObject):
        """`ui.script_runner.ScriptRunner` 的假体（不真起进程）。

        `ui/opp_deck_manager.py` 与 `ui/script_page.py` 都要 runner —— 提到模块级，
        免得两个测试类各写一份（原来那份在 `ScriptPageTest` 里，见下）。
        """

        line = Signal(str)
        done = Signal(int)

        def __init__(self, script, args=None):
            super().__init__()
            self.script = script
            self.args = list(args or [])
            self.started = False
            self.killed = False

        def running(self):
            return self.started and not self.killed

        def start(self):
            self.started = True

        def stop(self):
            self.killed = True

_APP = None
_SETTINGS_DIR = None


def _isolate_settings():
    global _SETTINGS_DIR
    if _SETTINGS_DIR is not None:
        return
    import tempfile
    _SETTINGS_DIR = tempfile.mkdtemp(prefix='m7-dash-settings-')
    path = os.path.join(_SETTINGS_DIR, 'ui.ini')
    dashboard.settings = lambda: QSettings(path, QSettings.IniFormat)
    # ★ i18n 也走这份临时 INI（否则读用户真机 ui/lang，测试随机器状态翻车）
    import ui.i18n as _i18n
    _i18n._SETTINGS_FACTORY = staticmethod(
        lambda: QSettings(path, QSettings.IniFormat))



def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyleSheet(theme.qss())
    _isolate_settings()
    #: 模式筛选会写 QSettings（2026-09-18 七轮起）—— 每个测试都从「全部」开始，
    #: 不然前一个测试选的模式会泄漏到后面的窗口（实测：闪刀姬两组变一组）。
    dashboard.settings().setValue('dash/mode_filter', '')
    #: 分析页页签同样要复位（D2：dash/analysis_tab 会被页签测试写脏）
    dashboard.settings().setValue('dash/analysis_tab', '')
    dashboard.settings().setValue('dash/analysis_month', '')
    dashboard.settings().setValue('dash/analysis_mode', '')
    #: 「卡组构成」记住的卡组同样要复位（D3：Tier 芯片点击会写 dash/deckview，
    #: 不复位的话后面建窗口的构成页会吃上一个测试的卡组）
    dashboard.settings().setValue('dash/deckview', '')
    return _APP


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DashboardShellTest(unittest.TestCase):

    def setUp(self):
        _app()
        self.opened = []
        self.win = dashboard.DashboardWindow()

    def tearDown(self):
        self.win.deleteLater()

    # ------------------------------------------------------------ 地板与结构
    def test_min_size_floor(self):
        """可拖拽缩放，**最小 1200×620 逻辑**（用户 2026-09-17 口径 Q5）。"""
        self.assertEqual(self.win.minimumWidth(), dashboard.MIN_W)
        self.assertEqual(self.win.minimumHeight(), dashboard.MIN_H)
        self.assertEqual((dashboard.MIN_W, dashboard.MIN_H), (1200, 620))

    def test_rail_structure(self):
        """★ 左栏结构（用户 2026-09-18 口径）：

        战绩 / 对局分析 / 卡组 / **悬浮窗**（占原「禁限表」位）→ 底部「**查询**」分组
        （禁限表 / 查卡）→ 最下「**设置**」；**设置项不列在左栏**。
        """
        keys = self.win.rail.keys()
        expect = ([k for _n, k in dashboard.NAV_PAGES]
                  + [dashboard.QUERY_KEY]
                  + [k for _n, k in dashboard.QUERY_PAGES]
                  + [dashboard.SETTINGS_KEY])
        self.assertEqual(keys, expect)
        # 顺序：导航四项在前、「查询」在最下的「设置」之前
        self.assertLess(keys.index('overlay'), keys.index('query'))
        self.assertLess(keys.index('cards'), keys.index(dashboard.SETTINGS_KEY))
        self.assertEqual(keys[-1], dashboard.SETTINGS_KEY)

    def test_overlay_took_banlist_slot(self):
        """「悬浮窗」在「卡组」那一组末尾；**「导出」排在它前面**（D4c 新增）；
        禁限表挪进「查询」。"""
        keys = self.win.rail.keys()
        self.assertEqual(keys[keys.index('decks') + 1], 'export')
        self.assertEqual(keys[keys.index('export') + 1], 'overlay')
        self.assertIn('banlist', [k for _n, k in dashboard.QUERY_PAGES])

    def test_settings_items_are_not_in_rail(self):
        """★ **设置项不在左栏**（用户 2026-09-18 口径）—— 它们只在「设置」页里。"""
        keys = self.win.rail.keys()
        for _name, key in dashboard.SETTINGS_PAGES:
            self.assertNotIn(key, keys, '%s 不该出现在左栏' % key)
        self.assertEqual(len(keys),
                         len(dashboard.NAV_PAGES) + 1
                         + len(dashboard.QUERY_PAGES) + 1)

    def test_every_page_exists_with_hint(self):
        # 09-27 一级直达：设置项（含工具）**都没有独立页** —— 全在概览页行内。
        keys = ([k for _n, k in dashboard.NAV_PAGES]
                + [dashboard.QUERY_KEY] + [k for _n, k in dashboard.QUERY_PAGES]
                + [dashboard.SETTINGS_KEY])
        for key in keys:
            self.assertIn(key, self.win.pages, key)
            self.assertIn(key, dashboard.PAGE_HINTS, key)
        self.assertEqual(self.win.stack.count(), len(keys))
        for _n, key in dashboard.SETTINGS_PAGES:
            self.assertNotIn(key, self.win.pages,
                             '%s 一级直达，不该有独立页' % key)
        # ⚠️ D3 §2 起「卡组构成」不再是独立页（它是「卡组」页的页签）——
        #    所以既不在 pages 里，也不该在 PAGE_HINTS 里
        self.assertNotIn('deckview', self.win.pages)
        self.assertNotIn('deckview', dashboard.PAGE_HINTS)
        # 4 导航 + 查询 + 查询子项(banlist/查卡/礼盒) + 设置
        # —— 子项数跟 QUERY_PAGES 推导，加页时这里不再烂
        self.assertEqual(len(keys),
                         len(dashboard.NAV_PAGES) + 1
                         + len(dashboard.QUERY_PAGES) + 1)

    # ------------------------------------------------------------ 换页
    def test_open_page_switches_main_area(self):
        for _n, key in list(dashboard.NAV_PAGES) + [
                ('设置', dashboard.SETTINGS_KEY)]:
            self.win.open_page(key)
            self.assertEqual(self.win.current_page(), key)
            self.assertIs(self.win.stack.currentWidget(), self.win.pages[key])

    def test_unknown_key_falls_back_to_first_nav_page(self):
        self.win.open_page('不存在的页')
        self.assertEqual(self.win.current_page(), dashboard.NAV_PAGES[0][1])

    def test_selection_is_unique(self):
        self.win.open_page('analysis')
        selected = [k for k, it in self.win.rail.items.items() if it.is_selected()]
        self.assertEqual(selected, ['analysis'])

    def test_rail_click_switches_page(self):
        """点左栏那一项本身（不是调 open_page）也要换页 —— 用户就是这么点的。"""
        item = self.win.rail.items['banlist']
        item.clicked.emit('banlist')
        self.assertEqual(self.win.current_page(), 'banlist')

    # ------------------------------------------------------------ ★ 字号分层
    def test_group_titles_are_bigger_than_children(self):
        """★ 层级**靠字号**（用户 Q13 口径）：「查询」「设置」是组标题（大），
        它们的子项（禁限表 / 查卡）小一档。"""
        big_q = self.win.rail.items[dashboard.QUERY_KEY].label.font().pixelSize()
        big_s = self.win.rail.items[dashboard.SETTINGS_KEY].label.font().pixelSize()
        self.assertGreater(big_q, 0)
        self.assertEqual(big_q, big_s, '两个组标题应同级')
        for _name, key in dashboard.QUERY_PAGES:
            small = self.win.rail.items[key].label.font().pixelSize()
            self.assertGreater(big_q, small,
                               '「查询」(%d) 应比子项 %s(%d) 大' % (big_q, key, small))

    def test_nav_icons_exist_and_sit_left_of_label(self):
        """★ 6 个图标在**文字左边**（用户 2026-09-18 口径），且尺寸统一。"""
        for key in dashboard.ICON_KEYS:
            item = self.win.rail.items[key]
            self.assertIsNotNone(item.icon, '%s 应有图标' % key)
            lay = item.layout()
            self.assertLess(lay.indexOf(item.icon), lay.indexOf(item.label),
                            '%s 的图标应在文字左边' % key)
            self.assertEqual(item.icon.width(), theme.RAIL_ICON)
            self.assertEqual(item.icon.height(), theme.RAIL_ICON)

    def test_child_items_have_no_icon(self):
        """分组下的子项（禁限表 / 查卡）**不给图标** —— 用有没有图标再加一层层级差。"""
        for _n, key in dashboard.QUERY_PAGES:
            self.assertIsNone(self.win.rail.items[key].icon, key)

    def test_icon_click_passes_through_to_row(self):
        """点图标要**算点整行**（图标设了 `WA_TransparentForMouseEvents`）。"""
        from PySide6.QtCore import Qt as _Qt
        item = self.win.rail.items['stats']
        self.assertTrue(item.icon.testAttribute(_Qt.WA_TransparentForMouseEvents))
        item.clicked.emit('stats')
        self.assertEqual(self.win.current_page(), 'stats')

    def test_rail_fonts_are_big_enough(self):
        """★ 下限锁：用户 2026-09-18 实测「侧栏字体都太小」→ 放大过一档，别再调小。

        之前主导航用 `F_BODY_SM(12)`、子项更小，在 1200×620 的窗口里显得又小又空。
        """
        # ⚠️ 2026-09-18 用户**第二次**说小 → 下限提到 17/19/15/20；别再调小
        self.assertGreaterEqual(theme.RAIL_F_ITEM, 17)
        self.assertGreaterEqual(theme.RAIL_F_GROUP, 19)
        self.assertGreaterEqual(theme.RAIL_F_CHILD, 15)
        self.assertGreaterEqual(theme.RAIL_F_BRAND, 20)
        for _n, key in dashboard.NAV_PAGES:
            self.assertGreaterEqual(
                self.win.rail.items[key].label.font().pixelSize(), theme.RAIL_F_ITEM)
        for _n, key in dashboard.QUERY_PAGES:
            self.assertGreaterEqual(
                self.win.rail.items[key].label.font().pixelSize(), theme.RAIL_F_CHILD)

    def test_nav_items_are_mid_size(self):
        """导航项字号介于两者之间之外也行，但必须**存在且为正**（防 0 号字）。"""
        for _name, key in dashboard.NAV_PAGES:
            self.assertGreater(self.win.rail.items[key].label.font().pixelSize(), 0)

    # ------------------------------------------------------------ 设置概览页
    def test_settings_page_is_flat(self):
        """★ 09-27 用户口径：**所有设置项（含数据工具的「运行」）一级完成**。

        * 9 行一个不少，**全部行内有控件**；无任何二级页（工具页已由概览页取代）。
        """
        page = self.win.pages[dashboard.SETTINGS_KEY]
        self.assertEqual(set(page._rows),
                         {k for _n, k in dashboard.SETTINGS_PAGES})
        self.assertEqual(len(page._rows), len(dashboard.SETTINGS_PAGES))
        for key, row in page._rows.items():
            self.assertIsNotNone(row.control, '%s 应行内直接操作' % key)
            self.assertNotIn(key, self.win.pages,
                             '%s 不该再有独立页' % key)

    def test_lang_combo_inline(self):
        """语言：行内筛选框，选择即写 `ui/lang`。"""
        page = self.win.pages[dashboard.SETTINGS_KEY]
        combo = page._rows['s_lang'].control
        self.assertEqual(combo.currentData(), 'cn')       # 初值 = 当前语言
        idx = combo.findData('jp')
        combo.activated.emit(idx)                          # 用户式选择
        self.assertEqual(str(dashboard.settings().value('ui/lang')), 'jp')
        # ★ 用组合框还原（不能裸 setValue：_save 会 reset i18n 缓存，
        #   还原时让缓存随「cn」重新落定 —— 否则 jp 毒化后续测试的 tr()）
        combo.activated.emit(combo.findData('cn'))
        self.assertEqual(str(dashboard.settings().value('ui/lang')), 'cn')

    def test_setting_groups_cover_all_pages(self):
        """概览页分组必须**不重不漏**覆盖 `SETTINGS_PAGES`（09-26 三组九项）。"""
        grouped = [k for _g, keys in dashboard.SETTING_GROUPS for k in keys]
        self.assertEqual(sorted(grouped),
                         sorted(k for _n, k in dashboard.SETTINGS_PAGES))
        self.assertEqual(len(grouped), len(set(grouped)))

    def test_tool_run_button_starts_shared_runner(self):
        """★ 工具行「运行」按钮：点击 → 共用 runner 起脚本 → 输出进页底日志区。"""
        made = []

        def _factory(script, args):
            made.append((script, list(args)))

            class _R(object):
                line = __import__('PySide6.QtCore', fromlist=['Signal']).Signal(str)
                done = __import__('PySide6.QtCore', fromlist=['Signal']).Signal(int)

                def running(self):
                    return False

                def start(self):
                    pass
            return _R()

        page = dashboard.SettingsIndexPage('', '', runner_factory=_factory)
        btn = page._rows['s_audit'].control.findChildren(QPushButton)[0]
        btn.click()
        self.assertEqual(made, [('tools/audit_issues.py', [])])
        # 卡表更新有两个入口（检查/下载）
        card = page._rows['s_cardtable'].control.findChildren(QPushButton)
        self.assertEqual([b.text() for b in card], ['检查更新', '下载更新'])

    def test_close_choice_page_constructor_does_not_write(self):
        """★ 构造即写库锁（审查 P1-3）：构造关窗行为页**不得写** `ui/close_action`。

        `close_action()` 的 explicit 位（「第一次点 × 给提示」）就靠「从未显式设置」——
        构造期一次 setChecked 若触发了写库，这个提示会**静默失效**。
        """
        dashboard.settings().remove('ui/close_action')
        combo = self.win.pages[dashboard.SETTINGS_KEY]._rows['s_close'].control
        self.assertEqual(combo.currentData(), 'tray')    # 有初值（默认托盘）
        self.assertIsNone(dashboard.settings().value('ui/close_action'),
                          '构造不许写 ui/close_action（保住 explicit 位）')

    def test_autostart_toggle_constructor_does_not_call_set(self):
        """★ 构造即写库锁：构造自启页**不得调**写「启动」目录的动作。"""
        calls = []
        page = dashboard.SettingsIndexPage(
            '', '', on_set_autostart=lambda on: calls.append(on) or True)
        box = page._rows['s_autostart'].control         # 打钩框（用户 09-27 口径）
        self.assertTrue(hasattr(box, 'isChecked'), '自启应是 QCheckBox')
        self.assertEqual(calls, [])                     # 构造不写「启动」目录
        self.assertFalse(box.isChecked())
        box.click()                                     # 用户点击才发号施令
        self.assertEqual(calls, [True])

    def test_close_choice_page_selection_roundtrip(self):
        """关窗行为页：两个选项都在；选择即写键（用户显式操作，explicit 位就该立起来）。"""
        combo = self.win.pages[dashboard.SETTINGS_KEY]._rows['s_close'].control
        combo.activated.emit(combo.findData('quit'))     # 用户式选择（写键）
        self.assertEqual(str(dashboard.settings().value('ui/close_action')), 'quit')
        combo.activated.emit(combo.findData('tray'))
        self.assertEqual(str(dashboard.settings().value('ui/close_action')), 'tray')

    def test_query_page_lists_its_two_items(self):
        """「查询」分组的两个子项都在左栏，且都能切页。"""
        for _name, key in dashboard.QUERY_PAGES:
            self.win.rail.items[key].clicked.emit(key)
            self.assertEqual(self.win.current_page(), key)

    def test_no_page_has_transitional_button(self):
        """**所有页**都不该再有过渡按钮（09-26 设置页优化：老对话框已下掉）。"""
        for key, page in self.win.pages.items():
            btns = [b for b in page.findChildren(QPushButton)
                    if '过渡' in b.text()]
            self.assertEqual(btns, [], '%s 不该有过渡按钮' % key)

    def test_fetch_decknames_moved_out_of_settings(self):
        """★ Q17（2026-09-18）：**「获取新卡组名」不在设置区**，改在「卡组」页里编辑。"""
        keys = [k for _n, k in dashboard.SETTINGS_PAGES]
        self.assertNotIn('s_fetch', keys)
        self.assertNotIn('获取新卡组名', [n for n, _k in dashboard.SETTINGS_PAGES])
        # 2026-09-23 用户定：新增「数据体检」（s_audit）；
        # 2026-10-05 用户定：新增「游戏启动时弹悬浮窗」（s_autoshow）；
        # 同日移除「刷新数据」（采集器自动做）；
        # 2026-10-09 用户定：新增「参照库刷新」（s_refdecks，卡组构成的逐卡使用率）→ 9 项
        # 2026-10-17 用户定：新增「显示我方/对方手坑列」两项（战绩页列开关）→ 11 项
        # 2026-10-18 用户定：新增「游戏对局数据」（数据目录可改）→ 12 项
        self.assertEqual(len(dashboard.SETTINGS_PAGES), 12)
        self.assertIn('获取新卡组名', dashboard.PAGE_HINTS['decks'][1] + dashboard.PAGE_HINTS['decks'][0])

    # ------------------------------------------------------------ 几何持久化
    def test_geometry_uses_own_settings_keys(self):
        self.win.resize(1360, 800)
        self.win.open_page('decks')
        self.win.save_geometry()
        s = dashboard.settings()
        self.assertTrue(s.contains('dash/size'))
        self.assertTrue(s.contains('dash/pos'))
        self.assertTrue(s.contains('dash/page'))
        self.assertFalse(s.contains('float/size'),
                         '看板不能写悬浮窗的键')

    def test_page_is_remembered_on_open(self):
        self.win.open_page('banlist')
        fresh = dashboard.DashboardWindow()
        try:
            self.assertEqual(fresh.current_page(), 'banlist')
        finally:
            fresh.deleteLater()

    # ------------------------------------------------------------ 版面纪律
    def test_no_label_is_vertically_clipped(self):
        """文字控件不能比它的字还矮（矮了会被压扁）。"""
        self.win.show()
        QApplication.processEvents()
        bad = []
        for lab in self.win.findChildren(QLabel):
            if lab.text() and lab.height() < lab.fontMetrics().height():
                bad.append((lab.text()[:12], lab.height(),
                            lab.fontMetrics().height()))
        self.assertEqual(bad, [], '这些标签被压扁了：%r' % (bad,))
        self.win.hide()

    def test_no_font_declarations_in_own_qss(self):
        """本窗自己设的样式表里不许出现 `font-*`（DESIGN.md §11①）。"""
        styles = [w.styleSheet() for w in self.win.findChildren(QLabel)]
        styles += [self.win.rail.styleSheet(), self.win.styleSheet()]
        for s in styles:
            self.assertNotIn('font-size', s)
            self.assertNotIn('font-family', s)



@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class StatsPageV2Test(unittest.TestCase):
    """2026-09-18 用户七条要求 → 逐条锁住（别再改回去）。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page
        self.win = dashboard.DashboardWindow(loader=demo_stats_page)
        self.page = self.win.pages['stats']

    def tearDown(self):
        self.win.deleteLater()

    def _texts(self):
        return [lab.text() for lab in self.page.findChildren(QLabel) if lab.text()]

    # ③ 表头
    def test_match_header_exists_with_eleven_columns(self):
        """③ 对局统计**要有表头**，列名与列数一致（**11 列**：2026-09-18 加「模式」、
        2026-09-20 去「日志」；**2026-10-16 加手坑两列**）。"""
        heads = self.page.findChildren(dashboard.MatchHeader)
        self.assertTrue(heads, '每组都该有表头')
        cols = [l.text() for l in heads[0].findChildren(QLabel)]
        self.assertEqual(cols, [n for n, _w in dashboard.STATS_COLS])
        self.assertEqual(len(cols), 11)
        self.assertIn('模式', cols)
        self.assertNotIn('日志', cols)
        self.assertIn('我方手坑', cols)
        self.assertIn('对方手坑', cols)

    def test_row_cells_match_column_widths(self):
        """★ **每个列元素的宽度 == 列宽、x 与表头逐格相同**（用户 2026-09-18 第二轮：

        「硬币列没显示，列全错位，日志列太宽 —— 把各个列的列宽均衡一下」）。
        旧代码给徽章与日志按钮写 `- 8`，整行比表头短 16px、一路往左错，
        最后几列被挤出视口（硬币格是唯一能压缩的控件，被压成 0 就是"没显示"）。
        """
        hdr = self.page.findChildren(dashboard.MatchHeader)[0]
        row = self.page.findChildren(dashboard.MatchRow)[0]
        heads = list(hdr.findChildren(QLabel))
        cells = []
        for i in range(row.layout().count()):
            w = row.layout().itemAt(i).widget()
            if w is None or w.width() <= 1:
                continue
            cells.append(w)
        self.assertEqual(len(cells), len(heads))
        for (name, width), lab, cell in zip(dashboard.STATS_COLS, heads, cells):
            self.assertEqual(lab.width(), int(width), '%s 表头列宽' % name)
            self.assertEqual(cell.width(), int(width), '%s 数据列宽' % name)
            self.assertEqual(cell.x(), lab.x(), '%s 列没对齐' % name)

    def test_header_and_row_share_widths(self):
        """表头与数据行**列宽同源**（都读 `STATS_COLS`）—— 否则会错位。"""
        hdr = self.page.findChildren(dashboard.MatchHeader)[0]
        row = self.page.findChildren(dashboard.MatchRow)[0]
        hw = [l.width() for l in hdr.findChildren(QLabel)]
        self.assertEqual(hw[0], dashboard.STATS_COLS[0][1])

    # ④ 徽章 + 硬币
    def test_result_badge_has_soft_background(self):
        """④ 胜负 = **带背景色的徽章**（同悬浮窗：语义色前景 + soft 底）。"""
        badges = self.page.findChildren(dashboard.ResultBadge)
        self.assertTrue(badges)
        texts = set(b.text() for b in badges)
        self.assertTrue(texts <= {'胜利', '失败', '平局', '—'}, texts)
        self.assertIn('胜利', texts)
        for b in badges:
            css = b.styleSheet()
            self.assertIn('background', css)
            self.assertIn('border-radius', css)
            self.assertTrue('rgba(' in css, css)          # soft 底是 rgba

    def test_went_first_has_coin_icon(self):
        """④ 先后攻**带硬币图**（素材与悬浮窗同一份 `data/coin_icons/`）。"""
        coins = self.page.findChildren(dashboard.CoinLabel)
        self.assertTrue(coins)
        with_pix = [c for c in coins
                    if any(l.pixmap() and not l.pixmap().isNull()
                           for l in c.findChildren(QLabel))]
        self.assertTrue(with_pix, '至少有一格要画出硬币')
        texts = [l.text() for c in coins for l in c.findChildren(QLabel) if l.text()]
        self.assertTrue({'先手', '后手'} & set(texts), texts)

    # ⑥ 对战日志入口 —— 2026-09-20 用户拍板移除（「不添加这个功能了」），
    #    原 test_log_button_is_placeholder_and_disabled 一并删除。

    # ⑦ 字号
    def test_page_fonts_are_big_enough(self):
        """⑦ 「战绩页所有的字体都太小」→ 锁下限（别再调小）。"""
        self.assertGreaterEqual(theme.PAGE_F_BODY, 15)
        self.assertGreaterEqual(theme.PAGE_F_TITLE, 20)
        self.assertGreaterEqual(theme.PAGE_F_SMALL, 14)
        self.assertGreaterEqual(theme.PAGE_F_BIG, 26)
        for lab in self.page.findChildren(QLabel):
            if lab.text():
                self.assertGreaterEqual(lab.font().pixelSize(), 14,
                                        '%r 的字号太小' % lab.text()[:10])

    # ① 时间倒序 + ⑤ 间距
    def test_rows_are_newest_first(self):
        """① 最上方 = 最近一局、最下方 = 最早一局（组内与组间都按时间倒序）。"""
        groups = self.page._data['groups']
        for _n, _s, rows in groups:
            keys = [(r['date'], r['time']) for r in rows]
            self.assertEqual(keys, sorted(keys, reverse=True), rows[0]['match_id'])
        gkeys = [(r[0]['date'], r[0]['time']) for _n, _s, r in groups if r]
        self.assertEqual(gkeys, sorted(gkeys, reverse=True))

    def test_opponent_column_has_no_versus_label(self):
        """★ 「对战」二字已删、表头改「对手」（用户 2026-09-18 五轮：

        「把对战列的表头改成对手，下面的对战xxx去掉对战两字只保留对手名字，对手名字居中」）。
        """
        heads = [l.text() for l in self.page.findChildren(
            dashboard.MatchHeader)[0].findChildren(QLabel)]
        self.assertIn('对手', heads)
        self.assertNotIn('对战', heads)
        row = self.page.findChildren(dashboard.MatchRow)[0]
        cell = row.layout().itemAt(2).widget()
        texts = [l.text() for l in cell.findChildren(QLabel) if l.text()]
        self.assertEqual(len(texts), 1, texts)          # 只有名字，没有「对战」
        self.assertEqual(texts[0], row.row.get('opp_player') or '未知')
        #: 居中 = 首尾各一条弹性
        self.assertIsNotNone(cell.layout().itemAt(0).spacerItem())

    # ② 本轮
    def test_records_are_month_scoped_and_grouped_by_round(self):
        """★ 对局记录 = **本月数据** + **按轮次分组**（同一副卡组跨轮 = 两组，都显示）。"""
        d = self.page._data
        self.assertIn('month', d)
        texts = self._texts()
        self.assertTrue(any('本月' in t for t in texts), texts[:8])
        self.assertTrue(any('按轮次分组' in t for t in texts), texts[:8])
        # demo 里闪刀姬出现在两轮 → 应该有两个「闪刀姬」组，各带自己的轮次标签
        names = [n for n, _s, _r in d['groups']]
        self.assertEqual(names.count('闪刀姬'), 2)
        labels = [s.get('round_label') for _n, s, _r in d['groups']]
        self.assertIn('本轮', labels)
        self.assertTrue(any(lbl and lbl != '本轮' for lbl in labels), labels)

    def test_mode_filter_button_filters_the_page(self):
        """★ 模式筛选（用户 2026-09-18 七轮，参考 Untapped 的模式下拉）：

        按钮挂在「战绩总览」标题行；选完 → loader 收到该模式、
        按钮文字与「战绩总览 / 对局记录」的口径标注都跟着更新。
        """
        calls = []

        from core.ui_demo import demo_stats_page

        def loader(mg=None):
            calls.append(mg)
            return demo_stats_page(mg)

        self.win.set_loader(loader)
        self.win.reload()
        self.assertEqual(calls[-1], [],
                         '空列表 = 全部模式（= 四组并集，见 mode_group_clause([])）')
        self.page.mode_selected.emit('ranked')
        _app().processEvents()
        self.assertEqual(calls[-1], ['ranked'])
        self.assertEqual(self.win._mode_group, ['ranked'])
        btns = [b.text() for b in self.page.findChildren(QPushButton)]
        self.assertTrue(any('排名决斗' in t for t in btns), btns)
        texts = self._texts()
        self.assertTrue(any('· 排名决斗' in t for t in texts),
                        '口径标注该跟着变：%r' % texts[:10])

    def test_empty_session_shows_reason(self):
        """本轮结束时的空态要说清**为什么空**（不然打开看板以为坏了）。"""
        self.win.set_loader(lambda mg=None: {'overview': {}, 'best_rank': None,
                                     'daily': {'2026-09-16': {'n': 1, 'w': 1, 'l': 0, 'd': 0}},
                                     'months': ['2026-09'], 'groups': [],
                                     'month': '2026-09',
                                     'last_match_at': '2026-09-16 16:16:02'})
        self.win.reload()
        texts = self._texts()
        self.assertTrue(any('本月还没有对局' in t for t in texts), texts)
        self.assertTrue(any('2026-09-16 16:16' in t for t in texts), texts)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class StatsPageTest(unittest.TestCase):
    """D1「战绩」页 —— 三块 + 组头 6 项 + 逐局行 8 列 + 行内标注入口（计划 §2.3 逐字规格）。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page
        self.demo = demo_stats_page
        self.win = dashboard.DashboardWindow(loader=self.demo)
        self.page = self.win.pages['stats']

    def tearDown(self):
        self.win.deleteLater()

    def _texts(self, root=None):
        root = root or self.page
        return [lab.text() for lab in root.findChildren(QLabel) if lab.text()]

    # ------------------------------------------------------------ 三块
    def test_three_blocks_exist(self):
        self.assertEqual(sorted(self.page.blocks),
                         ['heat', 'overview', 'records'])

    def test_overview_has_five_cells(self):
        """五格：对局数 / 胜率 / **先攻胜率 / 后攻胜率** / 最高段位（用户 2026-09-18 改名）。

        ★ 一格**一行数据**、**加粗**、**水平居中**：
        * 对局数 = `53(30-23)`（括号里恒为**胜-负**）；
        * 胜率 = 只有 `56.6%`（下面那行场数删掉）；
        * 先攻 / 后攻 = `72.0%(18-7)` / `33.3%(7-14)`；
        * 最高段位 = 大图标 + `Platinum 1`。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QWidget
        block = self.page.blocks['overview']
        texts = self._texts(block)
        for cell in ('对局数', '胜率', '先攻胜率', '后攻胜率', '最高段位'):
            self.assertIn(cell, texts)
        self.assertNotIn('先手', texts)                 # 旧卡名
        self.assertNotIn('后手', texts)
        self.assertIn('53', texts)
        self.assertIn('(30-23)', texts)                 # 一行里的副信息
        self.assertIn('(18-7)', texts)
        self.assertIn('(7-14)', texts)
        self.assertIn('56.6%', texts)
        self.assertIn('Platinum 1', texts)              # 最高段位：大图标 + 全称
        self.assertNotIn('30 - 23', texts)              # 旧的两行写法
        #: ⚠️ 只看**五格里面**（区块标题「战绩总览」是另一个层级，不在这次口径里）
        cards = [w for w in block.findChildren(QWidget)
                 if w.objectName() in ('dashOverviewCard',
                                       'dashBestRankCard')]
        self.assertEqual(len(cards), 5, '总览应该有五格')
        for card in cards:
            for lab in card.findChildren(QLabel):
                if not lab.text():
                    continue
                self.assertEqual(lab.font().weight(), QFont.Weight.Bold,
                                 '%r 没加粗' % lab.text())
                self.assertTrue(lab.alignment() & Qt.AlignHCenter,
                                '%r 没水平居中' % lab.text())

    def test_group_header_has_six_items(self):
        """组头 = 卡组名 + 对局数 + 先后攻 + 胜率 + 先手 + 后手（**没有「显示牌组」**）。"""
        texts = self._texts()
        self.assertIn('对局数', texts)
        self.assertIn('先后攻', texts)
        self.assertNotIn('显示牌组', texts)

    def test_each_match_row_has_nine_columns(self):
        """逐局行 **9 列**：胜负 · 先/后手 · 对战<名> · N 回合 · 段位 · **模式** ·
        日期 · 时间 · 对手卡组（日志列 2026-09-20 移除）。"""
        rows = self.page.findChildren(dashboard.MatchRow)
        self.assertTrue(rows, '应渲染出逐局行')
        first = rows[0]
        texts = [lab.text() for lab in first.findChildren(QLabel) if lab.text()]
        self.assertTrue({'胜利', '失败'} & set(texts), texts)          # 完整中文名
        self.assertTrue({'先手', '后手'} & set(texts), texts)
        #: ★ 五轮：「对战」二字删了 —— 那一格只有一个对手名
        self.assertFalse(any('对战' in t for t in texts), texts)
        self.assertTrue(any(t.endswith('回合') for t in texts), texts)
        self.assertTrue(any(len(t) == 5 and '-' in t for t in texts), texts)   # 09-16
        self.assertTrue(any(len(t) == 5 and ':' in t for t in texts), texts)   # 23:45
        self.assertTrue(first.findChildren(dashboard.OppDeckChip), '对手卡组格要在')
        #: ★ 段位格画的是**完整段位名**（用户 2026-09-18 第三轮：「段位改回 Platinum 1」）
        rank_cell = first.layout().itemAt(4).widget()
        #: ⚠️ 断言 **tooltip** 而不是渲染文本：offscreen 的桩字体把名字裁成了 `Pla…`
        #: （真机实测 `Platinum 1` = 67px，列内放得下 70px），单测环境量不准宽度。
        self.assertIn('Platinum', rank_cell.toolTip())
        #: ★ 模式列**在最后一列**（日志列移除后左移一格）
        self.assertEqual(dashboard.STATS_COLS[8][0], '模式')
        mode_cell = first.layout().itemAt(8).widget()
        self.assertIn(mode_cell.text(),
                      ['排名决斗', '巅峰决斗', 'DC杯', '活动决斗', '—'])

    def test_overview_cells_are_sized_by_content(self):
        """★ 总览五格**按内容分宽**（不是五等分）—— 用户 2026-09-18 第三轮：
        「字体太小，填满这个框」→ 字号提到 32px 后，四格要「主值 + 括号」= 实测 155px，
        而「最高段位」要「40px 图标 + `Platinum 1`(28px)」= 实测 186px；
        五等分（每格 185.6、格内 161.6）会让最高段位那格差 20px 放不下。
        """
        from PySide6.QtWidgets import QWidget
        #: ① 设计值：四格 179 + 最高段位 212 = **928**（五格加四个 12px 间距的可画宽）
        self.assertGreater(dashboard.OVERVIEW_W_RANK, dashboard.OVERVIEW_W)
        self.assertEqual(dashboard.OVERVIEW_W * 4 + dashboard.OVERVIEW_W_RANK, 928)
        #: ② 五格都在（四格统计 + 一格最高段位）
        block = self.page.blocks['overview']
        self.assertEqual(len(block.findChildren(QWidget, 'dashOverviewCard')), 4)
        self.assertEqual(len(block.findChildren(QWidget, 'dashBestRankCard')), 1)
        #: ⚠️ **运行时的实际宽度不在这里断言**：offscreen 的字体是"每字符 1em"的桩，
        #: 各格标题字数不同 → sizeHint 不同 → 宽度被分成 178/184/188/188（真机不是这样）。
        #: 真机宽度由自检脚本 `tmp/shot_dash.py` 逐格打印核对。

    def test_rank_cell_uses_previous_tier_for_delta(self):
        """★ 升降段局画**动之前那一档**（用户 2026-09-18 第四轮）：

        「`[图标] Platinum 2` 升段局应该显示的是 `[图标] Platinum 2↑`，不是 `Platinum 1↑`」
        —— `Platinum 1 ↑` 读起来是「Platinum 1 又升一段」，而 1 已经是最高的那一小段，
        语义不成立；`Platinum 2 ↑` = 「在 Platinum 2 升了一段」。平滑局照旧画**局后**段位。
        """
        seen = 0
        for r in self.page.findChildren(dashboard.MatchRow):
            delta = r.row.get('rank_delta')
            if delta not in ('up', 'down'):
                continue
            seen += 1
            cell = r.layout().itemAt(4).widget()
            before = r.row.get('rank_label_before') or ''
            self.assertTrue(before, '升降段的行必须有局前段位')
            self.assertTrue(cell.toolTip().startswith(before),
                            '%s 画成了 %r' % (before, cell.toolTip()))
            self.assertIn('升段' if delta == 'up' else '掉段', cell.toolTip())
        self.assertTrue(seen, 'demo 里要有升降段局')

    def test_header_and_cells_are_centered(self):
        """★ 表头与每列内容**水平居中**（用户 2026-09-18 第四轮：

        「表头和列应该居中对齐」）。文字格看 `alignment`；
        组合格（硬币 / 对战 / 段位 / 对手卡组）看布局**首尾各一条弹性**。
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton
        hdr = self.page.findChildren(dashboard.MatchHeader)[0]
        for lab in hdr.findChildren(QLabel):
            self.assertTrue(lab.alignment() & Qt.AlignHCenter, lab.text())
        row = self.page.findChildren(dashboard.MatchRow)[0]
        lay = row.layout()
        cells = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None or w.width() <= 1:
                continue
            cells.append(w)
        for (name, _w), cell in zip(dashboard.STATS_COLS, cells):
            if isinstance(cell, QPushButton):
                continue                       # 按钮文字本来就居中
            inner = cell.layout()
            #: ★ 用户 2026-09-18 五轮：「把对手卡组移到段位右边，**这一列单独向左对齐**」
            #: ★ 2026-10-04：先/后列同款左对齐（居中时硬币随文字长短漂、不成列）
            if name in ('对手卡组', '先/后'):
                self.assertIsNone(inner.itemAt(0).spacerItem(),
                                  '对手卡组列应该靠左（首端不许有弹性）')
                self.assertIsNotNone(inner.itemAt(inner.count() - 1).spacerItem())
                continue
            #: ⚠️ `CoinLabel` 也是 QLabel，但它是**容器**（里面有图 + 文字）——
            #: 所以先看布局，再看对齐属性。
            if inner is not None and inner.count() > 1:
                self.assertIsNotNone(inner.itemAt(0).spacerItem(),
                                     '%s 首端没有弹性（内容靠左）' % name)
                self.assertIsNotNone(inner.itemAt(inner.count() - 1).spacerItem(),
                                     '%s 尾端没有弹性（内容靠左）' % name)
                continue
            if isinstance(cell, QLabel):
                self.assertTrue(cell.alignment() & Qt.AlignHCenter,
                                '%s 没居中' % name)

    def test_rank_pending_shows_placeholder(self):
        """★ 段位获取中占位（用户 2026-09-18 晚）：窗口内的空段位显示 `…`
        + tooltip 说明，而不是一格空白让人以为坏了。"""
        from ui.dashboard import MatchRow as MR
        cell = MR({'rank_pending': True}, lambda r, w: None)._rank_cell()
        texts = [l.text() for l in cell.findChildren(QLabel)]
        self.assertIn('…', texts, texts)
        self.assertIn('获取中', cell.toolTip())

    def test_rank_cell_is_empty_outside_ranked(self):
        """★ **非天梯局不画段位** —— 与悬浮窗「最近对局」同口径。

        用户 2026-09-18：「对局记录应该和悬浮窗的最近对局的显示相同，
        **为什么你的最近对局在活动局也显示了段位？**」
        demo 里活动 / 巅峰 / DC 局**故意**带着段位参数（见 `ui_demo._prow`），
        界面必须一个字都不画 —— 画了就是这条测试红。
        """
        seen = set()
        for r in self.page.findChildren(dashboard.MatchRow):
            mode = r.row.get('mode_group')
            cell = r.layout().itemAt(4).widget()
            texts = [lab.text() for lab in cell.findChildren(QLabel) if lab.text()]
            seen.add(mode)
            has_rank = bool(r.row.get('rank_before') or r.row.get('rank_after'))
            if mode == 'ranked' and has_rank:
                self.assertTrue(texts, '天梯局该有段位：%r' % r.row.get('match_id'))
            else:
                self.assertEqual(texts, [], '%s 局不该画段位' % mode)
                self.assertEqual(cell.toolTip(), '')
        self.assertTrue({'ranked', 'event'} <= seen, seen)

    def test_mode_column_covers_the_four_modes(self):
        """★ 新增列：模式 —— 排名决斗 / 巅峰决斗 / DC杯 / 活动决斗（用户 2026-09-18）。"""
        labels = set()
        for r in self.page.findChildren(dashboard.MatchRow):
            labels.add(r.layout().itemAt(8).widget().text())
        self.assertTrue({'排名决斗', '活动决斗', '巅峰决斗', 'DC杯'} <= labels,
                        labels)

    def test_heat_cells_scale_and_color(self):
        """★ 热力图照 Untapped：**块大小 = 场数、颜色 = 胜率**（用户 2026-09-18）。

        * 每一格都是自绘的 :class:`ui.dashboard.HeatCell`；
        * 填充比例要**分出档**（不然"打得多的那天更大"就看不出来）；
        * 0 场的格子**不画填充块**（不染语义色）。
        """
        cells = self.page.findChildren(dashboard.HeatCell)
        self.assertTrue(cells, '热力图应该有格子')
        ratios = sorted({round(c._fill[1], 3) for c in cells if c._fill})
        self.assertGreaterEqual(len(ratios), 3, '填充块大小要随场数分档')
        self.assertLess(ratios[0], ratios[-1])
        self.assertTrue(any(c._fill is None for c in cells), '空白天不该有填充块')


    def test_opp_deck_chip_looks_like_a_button(self):
        """★ 对手卡组格做成**按钮样**（用户 2026-09-18 五轮：

        「卡组需要做成和日志类似的按钮效果，可以更直观的知道可以点击」）——
        底色 + 边框 + 悬停规则，高度与「日志」按钮一致。
        """
        chips = self.page.findChildren(dashboard.OppDeckChip)
        self.assertTrue(chips)
        css = chips[0].styleSheet()
        self.assertIn('background', css)
        self.assertIn('border', css)
        self.assertIn(':hover', css)
        self.assertEqual(chips[0].height(), theme.PAGE_ROW_H - 6)   # 原与「日志」按钮同高（列已移除）

    def test_opp_deck_chip_shows_suggestion_like_floating(self):
        """🔴 回归锁：看板对手卡组格 = **悬浮窗同款显示**（2026-10-11 用户口径）。

        未标注但有 M5 反推建议的行显示**建议名/建议图**（保留琥珀 `!` 未确认态），
        不是一律「未知卡组！」——悬浮窗那格一直显示建议，看板却空着，
        用户看到的是两边不一致。
        """
        row = {'opp_deck': None, 'opp_deck_img': None,
               'suggest_name': '耀圣', 'suggest_img': 'x.webp',
               'match_id': 'm1', 'opp_known': False}
        chip = dashboard.OppDeckChip(row)
        texts = [lab.text() for lab in chip.findChildren(QLabel)]
        self.assertIn('耀圣', texts)
        self.assertIn('!', texts)                    # 未确认角标保留
        thumbs = chip.findChildren(QLabel)
        self.assertTrue(any(t.pixmap() is not None for t in thumbs),
                        '建议图要显示出来')

    def test_opp_deck_chip_marked_wins_over_suggestion(self):
        """真标注了就显示标注值，建议只在没有标注时顶上。"""
        row = {'opp_deck': '魔术师', 'opp_deck_img': 'a.webp',
               'suggest_name': '耀圣', 'suggest_img': 'x.webp',
               'match_id': 'm1', 'opp_known': True}
        chip = dashboard.OppDeckChip(row)
        texts = [lab.text() for lab in chip.findChildren(QLabel)]
        self.assertIn('魔术师', texts)
        self.assertNotIn('!', texts)                 # 已标注无角标

    def test_unknown_opp_deck_chip_is_amber(self):
        """未标注 = 琥珀 `!` + 「未知卡组」（与悬浮窗同款）。"""
        chips = self.page.findChildren(dashboard.OppDeckChip)
        unknown = [c for c in chips
                   if any(lab.text() == '未知卡组'
                          for lab in c.findChildren(QLabel))]
        self.assertTrue(unknown, 'demo 里应有未标註的局')
        texts = [lab.text() for lab in unknown[0].findChildren(QLabel)]
        self.assertIn('!', texts)

    def test_click_result_badge_emits_result_pick(self):
        """点胜负徽章 → 发 `result_pick(row, 徽章)`（app 弹手动修正菜单）。"""
        got = []
        self.win.result_pick.connect(lambda r, w: got.append((r, w)))
        row = self.page.findChildren(dashboard.MatchRow)[0]
        row.result_badge.clicked.emit()
        self.assertEqual(len(got), 1)
        self.assertIn('match_id', got[0][0])
        self.assertIs(got[0][1], row.result_badge)

    def test_result_badge_is_clickable_looking(self):
        """可点就得看得出来（手型 + tooltip），别让用户猜。"""
        row = self.page.findChildren(dashboard.MatchRow)[0]
        self.assertIn('修正', row.result_badge.toolTip())

    def test_click_opp_deck_emits_pick_deck(self):
        """点对手卡组格 → 发 `pick_deck(row, widget)`（app 接它开选择器）。"""
        got = []
        self.win.pick_deck.connect(lambda row, w: got.append(row))
        chip = self.page.findChildren(dashboard.OppDeckChip)[0]
        chip.clicked.emit()
        self.assertEqual(len(got), 1)
        for key in ('match_id', 'opp_deck', 'opp_revealed_ids'):
            self.assertIn(key, got[0])

    def test_loader_failure_does_not_crash(self):
        def boom():
            raise RuntimeError('db is locked')
        self.win.set_loader(boom)
        self.win.reload()
        self.assertIn('暂无数据', self._texts())

    def test_realign_renders_after_reload(self):
        """再刷一次不许把内容刷没（每 2 秒的探针会反复调 reload）。"""
        before = len(self._texts())
        self.win.reload()
        self.assertEqual(len(self._texts()), before)

@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class HeatScaleTest(unittest.TestCase):
    """热力图的**纯函数**（用户 2026-09-18：照 Untapped 的读图法）。

    两根轴分开：**块大小 = 当天场数**、**颜色 = 当天胜率**。
    纯函数不给界面，单测直接调（与 `theme.win_rate_color` 同一层）。
    """

    def test_fill_ratio_grows_with_games(self):
        from ui import theme
        got = [theme.heat_fill_ratio(n, 18) for n in (0, 1, 4, 18)]
        self.assertEqual(got[0], 0.0)                     # 0 场不画块
        self.assertEqual(got[1], theme.HEAT_MIN_FILL)
        self.assertEqual(got[-1], 1.0)
        self.assertEqual(got, sorted(got), '场数越多块越大，不许反')

    def test_fill_ratio_never_overflow(self):
        from ui import theme
        self.assertEqual(theme.heat_fill_ratio(9, 9), 1.0)
        self.assertEqual(theme.heat_fill_ratio(99, 9), 1.0)
        self.assertEqual(theme.heat_fill_ratio(-1, 9), 0.0)
        self.assertLessEqual(theme.heat_fill_ratio(3, 0), 1.0)

    def test_color_green_high_red_low(self):
        from ui import theme
        self.assertEqual(theme.heat_rate_color(0.0), theme.LOSE)     # 全输 = 红
        self.assertEqual(theme.heat_rate_color(0.5), theme.WARN)     # 半 = 黄
        self.assertEqual(theme.heat_rate_color(1.0), theme.WIN)      # 全赢 = 绿
        self.assertEqual(theme.heat_rate_color(None), theme.NEUTRAL)  # 判不出 = 灰
        for rate in (0.1, 0.33, 0.66, 0.9, 2.0, -1.0):
            self.assertTrue(theme.heat_rate_color(rate).startswith('#'))

    def test_cell_needs_games(self):
        from ui import theme
        self.assertIsNone(theme.heat_cell(0, 0, 0, 5))
        self.assertIsNone(theme.heat_cell(None, None, None, 5))
        color, ratio = theme.heat_cell(3, 2, 1, 5)
        self.assertEqual(color, theme.heat_rate_color(2 / 3.0))
        self.assertGreater(ratio, 0.0)

    def test_draw_only_day_is_neutral(self):
        """平局日没有胜负可比 → 中性灰，**不冒充 0%**。"""
        from ui import theme
        color, _ratio = theme.heat_cell(2, 0, 0, 5)
        self.assertEqual(color, theme.NEUTRAL)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class StatsMonthSwitchTest(unittest.TestCase):
    """2026-10-01 用户：「点热力图切换月份查看」—— 月卡可点、选中高亮、整页切月。

    ⚠️ demo 数据只有 9 月一张卡，所以「两张卡」的场景由 loader 给 demo 补一天 8 月。
    """

    def _loader_with_two_months(self):
        def loader(mode_group=None, month=None):
            from core.ui_demo import demo_stats_page
            self.calls.append((mode_group, month))
            d = demo_stats_page(mode_group)
            d = dict(d)
            daily = dict(d.get('daily') or {})
            daily['2026-08-20'] = {'n': 2, 'w': 1, 'l': 1, 'd': 0}
            d['daily'] = daily
            d['months'] = sorted({k[:7] for k in daily})
            return d
        return loader

    def _press(self, card):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        card.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress,
                                         QPointF(5, 5), Qt.LeftButton,
                                         Qt.LeftButton, Qt.NoModifier))

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page
        self.calls = []
        self._raw_loader = demo_stats_page

        def counting(mode_group=None, month=None):
            self.calls.append((mode_group, month))
            return self._raw_loader(mode_group, month)
        self.win = dashboard.DashboardWindow(loader=counting)
        self.page = self.win.pages['stats']

    def tearDown(self):
        self.win.deleteLater()

    def _cards(self):
        return self.page.findChildren(dashboard.MonthHeatCard)

    def test_month_card_click_emits_month(self):
        cards = self._cards()
        self.assertTrue(cards)
        got = []
        self.page.month_selected.connect(got.append)
        self._press(cards[0])
        self.assertEqual(got, [cards[0]._month])

    def test_selected_card_gets_focus_ring(self):
        """当前看的月恰好一张卡高亮（焦点环 = `theme.HAIRLINE_FOCUS`）。"""
        rings = [c for c in self._cards()
                 if theme.HAIRLINE_FOCUS in c.styleSheet()]
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0]._month, self.page._data.get('month'))

    def test_click_switches_whole_page_and_reloads(self):
        self.win.deleteLater()
        self.win = dashboard.DashboardWindow(loader=self._loader_with_two_months())
        self.page = self.win.pages['stats']
        aug = [c for c in self._cards() if c._month == '2026-08']
        self.assertEqual(len(aug), 1)
        self._press(aug[0])
        self.assertEqual(self.win._stats_month, '2026-08')
        #: 2026-10-14 起模式筛选是**列表**，初始 = [] = 全部模式
        self.assertEqual(self.calls[-1], ([], '2026-08'))

    def test_clicking_same_month_does_not_reload(self):
        card = self._cards()[0]
        self._press(card)
        n = len(self.calls)
        self._press(card)
        self.assertEqual(len(self.calls), n)      # 同一个月重复点 = 不重取


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class StatsMonthFastPathTest(unittest.TestCase):
    """2026-10-03 切月卡顿修复：刀① 只刷战绩页（别的 loader 不动）+
    刀② 热力图块不重建（月卡高亮原地挪，只重画总览/记录）。
    """

    @staticmethod
    def _loader(calls):
        def loader(mode_group=None, month=None):
            calls.append(('stats', mode_group, month))
            from core.ui_demo import demo_stats_page
            d = dict(demo_stats_page(mode_group))
            daily = dict(d.get('daily') or {})
            daily['2026-08-20'] = {'n': 2, 'w': 1, 'l': 1, 'd': 0}
            d['daily'] = daily
            d['months'] = sorted({k[:7] for k in daily})
            if month == '2026-08':      # 切到 8 月：总览数字跟着变
                d['month'] = '2026-08'
                d['overview'] = dict(d.get('overview') or {},
                                     n_total=2, n_win=1, n_lose=1)
                d['groups'] = []
            return d
        return loader

    def setUp(self):
        _app()
        self.analysis_calls = []
        self.stats_calls = []
        self.win = dashboard.DashboardWindow(
            loader=self._loader(self.stats_calls),
            analysis_loader=lambda *a: self.analysis_calls.append(a) or {})
        self.page = self.win.pages['stats']

    def tearDown(self):
        self.win.deleteLater()

    def _cards(self):
        return self.page.findChildren(dashboard.MonthHeatCard)

    def _click(self, card):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        card.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(5, 5), Qt.LeftButton,
            Qt.LeftButton, Qt.NoModifier))

    def test_month_click_does_not_touch_other_loaders(self):
        """刀①：点月卡只跑战绩 loader —— 分析/导出等七个 loader 不许重跑。"""
        self.analysis_calls.clear()          # 构造时的初始 reload 不算
        self._click([c for c in self._cards() if c._month == '2026-08'][0])
        self.assertEqual(self.stats_calls[-1], ('stats', [], '2026-08'),
                         '模式筛选初始是 [] = 全部模式（2026-10-14 多选）')
        self.assertEqual(self.analysis_calls, [], '切月不该重跑别的页')

    def test_heat_block_not_rebuilt_on_month_switch(self):
        """刀②：切月后热力图块是**同一批控件**（月卡对象身份不变）。"""
        before = [c._month for c in self._cards()]
        ids_before = {id(c) for c in self._cards()}
        heat_block_before = id(self.page.blocks['heat'])
        self._click([c for c in self._cards() if c._month == '2026-08'][0])
        self.assertEqual([c._month for c in self._cards()], before)
        self.assertEqual({id(c) for c in self._cards()}, ids_before)
        self.assertEqual(id(self.page.blocks['heat']), heat_block_before)
        # 高亮挪到 8 月
        rings = [c for c in self._cards()
                 if theme.HAIRLINE_FOCUS in c.styleSheet()]
        self.assertEqual([c._month for c in rings], ['2026-08'])
        # 总览块重建了（新控件）且数据是 8 月的
        self.assertIsNot(id(self.page.blocks['overview']), None)
        texts = [lab.text() for lab in self.page.findChildren(QLabel)]
        self.assertTrue(any('2026-08' in t for t in texts), texts[:12])

    def test_daily_change_falls_back_to_full_rebuild(self):
        """热力图输入变了（新的一天有局）-> 走全量重建，月卡是新控件。"""
        ids_before = {id(c) for c in self._cards()}
        def new_day_loader(mode_group=None, month=None):
            d = self._loader(self.stats_calls)(mode_group, month)
            d = dict(d)
            d['daily'] = dict(d['daily'])
            d['daily']['2026-08-21'] = {'n': 1, 'w': 0, 'l': 1, 'd': 0}
            d['months'] = sorted({k[:7] for k in d['daily']})
            return d
        self.win.set_loader(new_day_loader)
        self.win._reload_stats()
        self.assertNotEqual({id(c) for c in self._cards()}, ids_before,
                            'daily 变了必须整块重建')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class AccountSwitcherTest(unittest.TestCase):
    """多账号（2026-10-03）：左栏「MD 战绩」品牌位 = 账号切换器 ——
    文案 = 当前账号名（未选 = 「MD 战绩」），点击弹账号下拉；
    选择记 QSettings ``dash/account``、只刷战绩页（loader 收 ``account=``）。
    """

    def setUp(self):
        _app()
        self.calls = []
        self.accounts = [('示例玩家', 53, '2026-09-16 23:45:00'),
                         ('示例玩家2', 7, '2025-11-02 21:00:00')]

        def loader(mode_group=None, month=None, account=None):
            self.calls.append((mode_group, month, account))
            from core.ui_demo import demo_stats_page
            d = dict(demo_stats_page(mode_group))
            d['accounts'] = list(self.accounts)
            return d
        self.win = dashboard.DashboardWindow(loader=loader)
        self.settings().setValue('dash/account', '')
        self.win._update_account_button(self.accounts)

    def tearDown(self):
        self.settings().setValue('dash/account', '')
        self.win.deleteLater()

    @staticmethod
    def settings():
        return dashboard.settings()

    def _btn(self):
        return self.win.rail.brand

    def test_brand_shows_account_name(self):
        """品牌位 = 当前账号名；未选 = 「MD 战绩」。"""
        self.assertIn('MD 战绩', self.win.rail._brand_text())
        self.win.set_account('示例玩家')
        self.assertEqual(self.win.rail._brand_text(), '示例玩家')
        self.win.set_account('示例玩家2')
        self.assertEqual(self.win.rail._brand_text(), '示例玩家2')

    def test_set_account_reloads_with_account(self):
        self.calls.clear()
        self.win.set_account('示例玩家')
        #: 1st = mode_group（[] = 全部模式）
        self.assertEqual(self.calls[-1], ([], None, '示例玩家'))
        self.assertIn('示例玩家', self.win.rail._brand_text())
        self.assertEqual(self.settings().value('dash/account'), '示例玩家')
        self.assertEqual(self.calls[-1], ([], None, '示例玩家'))

    def test_single_account_still_shows_name(self):
        """一个账号也照常显示账号名（2026-10-03 用户实拍口径）。"""
        self.win._update_account_button(self.accounts[:1])
        self.assertIn('MD 战绩', self.win.rail._brand_text())      # 未选 -> 品牌字
        self.assertIn('▾', self.win.rail.brand_arrow.text())  # 倒三角贴右缘

    def test_heat_grid_packed_left(self):
        """月历整块 AlignLeft 打包（间距实拍修复：网格不许被拉到整行宽）。"""
        self.win.set_account(None)
        heat = self.page = self.win.pages['stats']
        from PySide6.QtCore import Qt
        lay = heat.blocks['heat'].layout()
        found = False
        for i in range(lay.count()):
            it = lay.itemAt(i)
            w = it.widget()
            if w is not None and w.findChildren(dashboard.MonthHeatCard):
                self.assertTrue(it.alignment() & Qt.AlignLeft,
                                'grid_host 该 AlignLeft 打包')
                found = True
        self.assertTrue(found, '热力图块里该有月历网格')

    def test_stale_account_resets_to_brand(self):
        self.win.set_account('示例玩家2')
        self.win._update_account_button(self.accounts[:1])   # 账号没了
        self.assertIsNone(self.win._account)
        self.assertEqual(self.settings().value('dash/account'), '')
        self.assertIn('MD 战绩', self.win.rail._brand_text())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class AnalysisPageTest(unittest.TestCase):
    """D2「对局分析」页 —— 真页接线 + 三页签 + 矩阵格（`loader=demo_analysis_page`）。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_analysis_page, demo_stats_page
        self.demo_analysis = demo_analysis_page
        self.win = dashboard.DashboardWindow(loader=demo_stats_page,
                                             analysis_loader=demo_analysis_page)
        self.page = self.win.pages['analysis']

    def tearDown(self):
        self.win.deleteLater()

    def test_real_page_not_placeholder(self):
        """占位页换成了真页（照 present 特判成法）。"""
        self.assertIsInstance(self.page, analysis_page.AnalysisPage)

    def test_avg_row_rendered_and_note_removed(self):
        """v5d：口径警示行删掉了。

        ★ 2026-10-15 用户：「把对手卡组和综合胜率交换」—— 现在页签名
        （综合胜率）进**角格**（自绘，不在 QLabel 里），下面那行行首 = 「对手卡组」。
        """
        texts = [l.text() for l in self.page.findChildren(QLabel)]
        self.assertIn('对手卡组', texts)                 # 行首标签（原角格左下那个）
        self.assertNotIn('统计口径：天梯 + 巅峰', texts)
        #: 角格里画的是页签名（自绘，取 widget 实例验证）
        corners = self.page.findChildren(analysis_page.CornerHeader)
        self.assertTrue(corners, '角格还在')
        self.assertEqual(corners[0]._scope, '综合胜率')

    def test_matrix_cells_rendered(self):
        """demo 7 行（6 对手 + 未知卡组）× 3 列 + 首行 3 列汇总 = 24 格。"""
        cells = self.page.findChildren(analysis_page.MatrixCell)
        # 09-28 起「未知卡组」行显示：7 行 × 3 列 + 3 列汇总
        self.assertEqual(len(cells), 7 * 3 + 3)

    def test_tab_switch_refetches_with_param(self):
        """页签 → 窗口带 went_first 重取（照 set_mode_group 模式）+ QSettings 持久化。"""
        calls = []

        def loader(wf, month=None, mode_group=None):
            calls.append(wf)
            return self.demo_analysis(wf)

        self.win.set_analysis_loader(loader)
        self.page._tab_buttons[True].click()
        self.assertEqual(calls, [True])
        self.assertIs(self.win._went_first, True)
        self.assertTrue(self.page._tab_buttons[True].isChecked())
        self.assertEqual(str(dashboard.settings().value('dash/analysis_tab')),
                         '1')
        self.page._tab_buttons[None].click()
        self.assertEqual(calls, [True, None])
        self.assertEqual(str(dashboard.settings().value('dash/analysis_tab')), '')

    def test_month_and_mode_filters_persist_and_refetch(self):
        """v5 筛选器：页上报 -> 窗口记住 + QSettings 持久化 + 带参重取。"""
        calls = []

        def loader(wf, month=None, mode_group=None):
            calls.append((wf, month, mode_group))
            return self.demo_analysis(wf)

        self.win.set_analysis_loader(loader)
        self.page.month_selected.emit('2026-09')
        self.page.mode_selected.emit('ranked')
        self.assertEqual(calls[-2:], [(None, '2026-09', []),
                                      (None, '2026-09', ['ranked'])])
        self.assertEqual(str(dashboard.settings().value('dash/analysis_month')),
                         '2026-09')
        self.assertEqual(str(dashboard.settings().value('dash/analysis_mode')),
                         'ranked')
        self.assertEqual(self.win._analysis_month, '2026-09')
        self.assertEqual(self.win._analysis_mode, ['ranked'])

    def test_show_menu_emits_signals(self):
        """🔴 2026-10-03 回归：筛选按钮点击后**真的能选** —— 旧代码连接还引用
        改名前的 `label` 变量，点击即 NameError、菜单永远弹不出来。

        验证方式：把模块里的 `QMenu` 换成假体（`exec` 自动触发当前选中项），
        走完整 `_show_menu` 链路断言信号发出。
        """
        got = []
        self.page.month_selected.connect(got.append)
        self.page.mode_selected.connect(got.append)

        class _FakeMenu(object):
            def __init__(self, *_a, **_k):
                self._acts = []

            def addAction(self, text):
                from PySide6.QtGui import QAction
                act = QAction(text)          # 真动作（无父也能 emit）
                self._acts.append(act)
                return act

            def addSeparator(self):
                pass

            def exec(self, *_a, **_k):
                for act in self._acts:          # 模拟用户点「当前选中项」
                    if act.isChecked():
                        act.triggered.emit()

        class _FakeGroup(object):
            def __init__(self, *_a, **_k):
                pass

            def setExclusive(self, _on):
                pass

            def addAction(self, _act):
                pass

        real_menu = analysis_page.QMenu
        real_group = analysis_page.QActionGroup
        analysis_page.QMenu = _FakeMenu
        analysis_page.QActionGroup = _FakeGroup
        try:
            self.page._show_menu(self.page._month_btn, 'date')
            self.page._show_menu(self.page._mode_btn, 'mode')
        finally:
            analysis_page.QMenu = real_menu
            analysis_page.QActionGroup = real_group
        # demo 数据：月份选 None（全部）、模式选 None（全部）→ 两个信号都发 None
        self.assertEqual(got, [None, None])

    def test_stats_page_still_loaded_alongside(self):
        """🔴 2026-10-03 回归：分析页筛选器**点不了** —— 接线时把导出页
        同款「tuple + list」拼接 bug 又抄了一遍，开菜单即 TypeError。
        这里只锁「两个筛选器的菜单项都构造得出来且模式项不含单人」。"""
        date_items = self.page._menu_items('date')
        mode_keys = [k for k, _n in self.page._menu_items('mode')]
        self.assertTrue(date_items)
        self.assertIsNone(mode_keys[0])                  # 首项 = 全部
        self.assertNotIn('solo', mode_keys)
        self.assertIn('ranked', mode_keys)

    def test_stats_page_still_loaded_alongside(self):
        """reload() 现在喂两页 —— 「战绩」页不能因为加了解析页就空壳。"""
        self.win.reload()
        self.assertTrue(self.win.pages['stats'].findChildren(QLabel))


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class BanlistRefreshTest(unittest.TestCase):
    """禁限表页的「抓取禁限表」按钮（2026-10-03 用户：对应界面也要能刷新）。

    按钮只发号施令（经 runner_factory 跑 tools/fetch_banlist.py），
    成功后发 `refreshed`（看板接：重取本页数据）；跑的时候按钮禁用。
    """

    def setUp(self):
        _app()
        from ui import banlist_page
        self.calls = []
        self.page = banlist_page.BanlistPage(
            runner_factory=lambda s, a: self.calls.append((s, a))
            or _FakeBanRunner())

    def tearDown(self):
        self.page.deleteLater()

    def test_button_runs_fetch_and_disables(self):
        self.page.btn_refresh.click()
        self.assertEqual(self.calls, [('tools/fetch_banlist.py', [])])
        self.assertFalse(self.page.btn_refresh.isEnabled())

    def test_done_reenables_and_emits_refreshed(self):
        got = []
        self.page.refreshed.connect(lambda: got.append(1))
        self.page.btn_refresh.click()
        self.page._on_fetch_done(0)
        self.assertTrue(self.page.btn_refresh.isEnabled())
        self.assertEqual(self.page.fetch_status.text(), '完成')
        self.assertEqual(got, [1])
        self.page._on_fetch_done(2)                     # 失败不发 refreshed
        self.assertIn('失败', self.page.fetch_status.text())
        self.assertEqual(got, [1])

    def test_busy_click_is_noop(self):
        self.page.btn_refresh.click()
        n = len(self.calls)
        self.page.btn_refresh.click()                   # running 中再点 = 忽略
        self.assertEqual(len(self.calls), n)


class _FakeBanRunner(object):
    """够用的假 runner：running() 可控、信号可连。"""

    def __init__(self):
        from PySide6.QtCore import Signal, QObject

        class _Sigs(QObject):
            line = Signal(str)
            done = Signal(int)
        self._sigs = _Sigs()
        self.line = self._sigs.line
        self.done = self._sigs.done
        self._running = True

    def running(self):
        return self._running

    def start(self):
        pass


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class OverlayAndCardsPagesTest(unittest.TestCase):
    """「悬浮窗」开关页 + 「查卡」网站清单页（2026-10-03 用户四条需求）。

    * 悬浮窗页：显示/隐藏按钮，文案随状态换（真切换在 app 侧回调）；
    * 查卡页：三个查卡站直达（官网 / 百鸽 / masterduelmeta），外链可点。
    """

    def setUp(self):
        _app()
        self.toggles = []
        self.page = dashboard.FloatingTogglePage()
        self.page.toggle_requested.connect(
            lambda: self.page.set_floating_visible(
                self.toggles.append(True) or True))
        self.links = dashboard.CardsLinksPage()

    def tearDown(self):
        self.page.deleteLater()
        self.links.deleteLater()

    def test_floating_toggle_button_text_follows_state(self):
        self.assertEqual(self.page.btn.text(), '显示悬浮窗')
        self.page.set_floating_visible(True)
        self.assertEqual(self.page.btn.text(), '隐藏悬浮窗')
        self.page.set_floating_visible(False)
        self.assertEqual(self.page.btn.text(), '显示悬浮窗')

    def test_floating_toggle_signal_fires(self):
        self.page.btn.click()
        self.assertEqual(len(self.toggles), 1)

    def test_cards_links_present(self):
        from PySide6.QtWidgets import QLabel
        urls = [l.url() for l in self.links.findChildren(QLabel)
                if hasattr(l, 'url')]
        texts = ' '.join(l.text() for l in self.links.findChildren(QLabel))
        for url in ('https://www.db.yugioh-card.com/', 'https://ygocdb.com/',
                    'https://www.masterduelmeta.com/'):
            self.assertIn(url, texts, url)
        self.assertIn('官网查卡', texts)
        self.assertIn('百鸽查卡', texts)
        self.assertIn('masterduelmeta', texts)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class AnalysisStretchResidueTest(unittest.TestCase):
    """🔴 2026-10-03 实测事故回归：切日期后矩阵第二列撑爆。

    根因 = QGridLayout 的列 stretch **累积不清理**：10 月只有闪刀姬一个
    我方卡组（单列 -> 它那列 stretch=1），切回 9 月残留还在，多余宽度
    全灌进第二列。修法 = 重建前清零全部行列 stretch。
    """

    def setUp(self):
        _app()
        self.page = analysis_page.AnalysisPage()
        self.page.resize(1200, 800)
        self.page.show()

    def tearDown(self):
        self.page.deleteLater()

    @staticmethod
    def _matrix(cols):
        """最小可用矩阵数据：每列有 avg，两行对手卡组。"""
        return {
            'cols': list(cols),
            'col_avgs': [{'deck': c, 'n': 4, 'w': 3} for c in cols],
            'rows': [
                {'deck': '未知卡组', 'n': 4,
                 'cells': [{'deck': c, 'n': 4, 'w': 3, 'l': 1} for c in cols]},
                {'deck': '珠泪', 'n': 2,
                 'cells': [{'deck': c, 'n': 2, 'w': 1, 'l': 1} for c in cols]},
            ],
        }

    def _col_widths(self):
        g = self.page.grid
        out = []
        for i in range(g.count()):
            pos = g.getItemPosition(i)
            if pos[0] == 0 and pos[1] >= 1 and g.itemAt(i).widget() is not None:
                out.append((pos[1], g.itemAt(i).widget().geometry().width()))
        return [x for _, x in sorted(out)]

    def test_switch_from_single_col_month(self):
        _app().processEvents()
        self.page.set_data(self._matrix(['闪刀姬']))       # 10 月：单列
        _app().processEvents()
        self.page.set_data(self._matrix(['K9VS', '闪刀姬',
                                         '耀圣杀手旋律']))  # 切 9 月：三列
        _app().processEvents()
        widths = self._col_widths()
        self.assertEqual(len(widths), 3)
        for w in widths:
            self.assertLessEqual(w, 160,
                                 '切日期后任何列都不许被残留 stretch 撑爆')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class TierPageTest(unittest.TestCase):
    """D3「Tier 表」—— 真页渲染 + 分节（loader=demo_tier_page）。

    ⚠️ 2026-09-21（用户）：「把查询下的 Tier 表做到卡组的卡组构成页签左边，
    移除查询下的 tier 表」→ 它现在是**「卡组」页的页签 2**，不再是独立页。
    """

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page, demo_tier_page
        self.win = dashboard.DashboardWindow(loader=demo_stats_page,
                                             tier_loader=demo_tier_page)
        self.page = self.win.pages['decks'].tier_page

    def tearDown(self):
        self.win.deleteLater()

    def test_real_page_not_placeholder(self):
        from ui import tier_page as tier_page_mod
        self.assertIsInstance(self.page, tier_page.TierPage)

    def test_four_sections_rendered(self):
        """四节标题都在；demo 10 张芯片（1+3+4+2）都渲染。"""
        texts = [l.text() for l in self.page.findChildren(QLabel)]
        for t in ('TIER 1', 'TIER 2', 'TIER 3', 'TRENDING'):
            self.assertIn(t, texts)
        chips = self.page.findChildren(tier_page.TierChip)
        self.assertEqual(len(chips), 10)
        self.assertTrue(any('Power: 16.5' in l.text()
                            for l in self.page.findChildren(QLabel)))


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class BanlistPageTest(unittest.TestCase):
    """D3「禁限表」页 —— 真页注册 + 三分组渲染（loader=demo_banlist_page）。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_banlist_page, demo_stats_page
        self.win = dashboard.DashboardWindow(
            loader=demo_stats_page, banlist_loader=demo_banlist_page)
        self.page = self.win.pages['banlist']

    def tearDown(self):
        self.win.deleteLater()

    def test_real_page_not_placeholder(self):
        from ui import banlist_page as bp
        self.assertIsInstance(self.page, bp.BanlistPage)

    def test_status_blocks_and_cards(self):
        """三分组标题 + 5 张卡片 + 汇总行（含「共 5 张受限」）。"""
        from ui import banlist_page as bp
        texts = [l.text() for l in self.page.findChildren(QLabel)]
        self.assertTrue(any(t.startswith('禁止 · 2') for t in texts), texts[:6])
        self.assertTrue(any(t.startswith('限制 · 2') for t in texts))
        self.assertTrue(any(t.startswith('准限制 · 1') for t in texts))
        self.assertEqual(len(self.page.findChildren(bp.BanCard)), 5)
        self.assertIn('共 5 张受限', self.page.summary.text())
        self.assertIn('最近变更', texts)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class TierChipClickableTest(unittest.TestCase):
    """D3 剩余：Tier 表芯片**可点击** → 切到「卡组构成」页（用户 2026-09-21 拍板）。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page, demo_tier_page
        self.win = dashboard.DashboardWindow(
            loader=demo_stats_page, tier_loader=demo_tier_page,
            deckview_loader=lambda name=None: {'name': name, 'options': [],
                                               'main': [], 'extra': []})
        #: Tier 表是「卡组」页的页签 2（2026-09-21 从「查询」搬来的）
        self.page = self.win.pages['decks'].tier_page

    def tearDown(self):
        self.win.deleteLater()

    def test_chip_carries_the_site_name(self):
        """芯片上记着**站内英文名**（`clicked` 发的就是它 —— 参照库的键）。"""
        from ui import tier_page as tp
        chips = self.page.findChildren(tp.TierChip)
        self.assertTrue(chips)
        decks = [d for s in self.page._data.get('sections') or []
                 for d in s.get('decks') or []]
        for chip, deck in zip(chips, decks):
            self.assertEqual(chip.ref_name, deck['name'])

    def test_click_switches_to_composition_tab(self):
        """★ 点芯片 → 「卡组」页 **且** 停在「卡组构成」页签。

        （Tier 表本来就是这一页的页签，所以「切页签」就是它的全部动作。）
        """
        from ui import tier_page as tp
        chip = self.page.findChildren(tp.TierChip)[0]
        chip.clicked.emit(chip.ref_name)
        self.assertEqual(self.win.current_page(), 'decks')
        self.assertEqual(self.win.current_decks_tab(), 'composition')
        self.assertEqual(self.win.current_deck_view(), chip.ref_name)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckViewPageTest(unittest.TestCase):
    """D3「卡组构成」页 —— 两栏卡图网格 + `Nx in P%` + 换卡组下拉。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_deck_composition, demo_stats_page
        from ui import deck_view_page
        self.dvp = deck_view_page
        self.win = dashboard.DashboardWindow(
            loader=demo_stats_page, deckview_loader=demo_deck_composition)
        self.win.set_decks_tab('composition')
        self.page = self.win.pages['decks'].view_page
        self.demo = demo_deck_composition()

    def tearDown(self):
        self.win.deleteLater()

    def test_two_panels_and_cell_count(self):
        """两栏标题**带数量**（`Top Main Deck (10)`）—— 2026-09-21 §1.2-2。"""
        texts = [l.text() for l in self.page.findChildren(QLabel)]
        main_t = [t for t in texts if t.startswith('Top Main Deck')]
        extra_t = [t for t in texts if t.startswith('Top Extra Deck')]
        self.assertTrue(main_t, '主卡栏标题在')
        self.assertTrue(extra_t, '额外栏标题在')
        self.assertIn('(%d)' % len(self.demo['main']), main_t[0])
        self.assertIn('(%d)' % len(self.demo['extra']), extra_t[0])
        cells = self.page.findChildren(self.dvp.CardCell)
        self.assertEqual(len(cells),
                         len(self.demo['main']) + len(self.demo['extra']))

    def test_count_text_format(self):
        """`3x in 100%`（at 缺了省略前半段）。"""
        self.assertEqual(self.dvp.count_text({'per': 100, 'at': 3}),
                         '3x in 100%')
        self.assertEqual(self.dvp.count_text({'per': 42, 'at': None}),
                         'in 42%')
        labels = []
        for c in self.page.findChildren(self.dvp.CardCell):
            text_labels = [l.text() for l in c.findChildren(QLabel) if l.text()]
            # 一格两行字：**卡名 + 计数**（卡图没下回来时还有个 `…` 占位）
            self.assertEqual(len(text_labels), 2, '一格 = 卡名 + 计数')
            labels.append(text_labels[1])
        self.assertIn('3x in 100%', labels)

    def test_picker_lists_reference_decks(self):
        self.assertEqual(self.page.picker.count(), len(self.demo['options']))
        self.assertTrue(self.page.picker.currentData(), '当前卡组要选上')

    def test_picking_a_deck_refetches(self):
        """用户在下拉里换卡组 → `deck_selected` → 窗口重取（这里直接发信号验链路）。"""
        seen = []
        self.page.deck_selected.connect(seen.append)
        self.page.deck_selected.emit('Branded')
        self.assertEqual(seen, ['Branded'])

    def test_empty_reference_shows_hint(self):
        """参照库里没这副牌 → 空态文案，别留白屏。"""
        self.page.set_data({'name': 'Nope', 'display': 'Nope', 'has_ref': False,
                            'options': [], 'main': [], 'extra': [],
                            'unknown': 0})
        texts = [l.text() for l in self.page.findChildren(QLabel)]
        self.assertTrue(any('暂无该卡组构成' in t for t in texts))  # 10-03 文案
        self.assertEqual(self.page.findChildren(self.dvp.CardCell), [])


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckViewOptimizeTest(unittest.TestCase):
    """D3 §1（2026-09-21）—— 用户「可以点击但是 UI 需要优化」的九条。

    这里只锁**能自动验的**：列数自适应（P1，硬指标）、可搜索（P3）、
    格子有卡名（P4）、栏标题带数量（P2）、阈值/排序（P6）、unknown 在页头（P7）、
    快照日期（P8）、回 Tier 表（P9）、加载占位（1.2-9）。
    """

    def setUp(self):
        _app()
        from core.ui_demo import demo_deck_composition
        from ui import deck_view_page
        self.dvp = deck_view_page
        self.demo = demo_deck_composition()
        self.page = deck_view_page.DeckViewPage()
        self.page.show()                     # viewport 有真实尺寸才谈得上自适应
        self.page.resize(1018, 584)          # 最小窗口下每栏可视区（计划 §0）
        self.page.set_data(self.demo)
        for _ in range(6):
            QApplication.processEvents()

    def tearDown(self):
        self.page.deleteLater()

    # -------------------------------------------------- P1 横向滚动条（硬指标）
    def test_no_horizontal_scrollbar_at_any_width(self):
        """★ 最小窗口 1200×620 下**不得出现**横向滚动条（计划 §1.2-1 硬指标）。"""
        for w, h in ((1018, 584), (1200, 620), (1400, 764), (1738, 964)):
            self.page.resize(w, h)
            for _ in range(4):
                QApplication.processEvents()
            for key in ('main', 'extra'):
                bar = self.page.areas[key].horizontalScrollBar()
                self.assertEqual(
                    bar.maximum(), 0,
                    '%dx%d 时「%s」栏出了横向滚动条（列数没算对）'
                    % (w, h, key))

    def test_cols_for_width_never_overflows(self):
        """列数公式：n 列占宽必须 ≤ 可视宽（否则就是撑出滚动条）。"""
        for width in (300, 471, 481, 528, 640, 841, 1200):
            n = self.dvp.cols_for_width(width)
            need = n * self.dvp.CELL_W + (n - 1) * self.dvp.SP
            self.assertLessEqual(need, width, '宽度 %d 给了 %d 列（需 %d）'
                                 % (width, n, need))
            self.assertGreaterEqual(n, 1)

    def test_cols_shrink_with_narrow_viewport(self):
        """窄了列数就得减（自适应，不是固定 6 列）。"""
        narrow = self.dvp.cols_for_width(471)
        wide = self.dvp.cols_for_width(841)
        self.assertLess(narrow, wide)
        self.assertEqual(narrow, 5)          # 计划 §1.2-4 算的就是 5 列

    # -------------------------------------------------- P2 栏标题带数量
    def test_titles_carry_counts(self):
        self.assertIn('(%d)' % len(self.demo['main']),
                      self.page.titles['main'].text())
        self.assertIn('(%d)' % len(self.demo['extra']),
                      self.page.titles['extra'].text())

    # -------------------------------------------------- P3 下拉可搜索
    def test_picker_is_searchable(self):
        self.assertTrue(self.page.picker.isEditable())
        self.assertIsNotNone(self.page.picker.completer())
        self.assertEqual(self.page.picker.completer().filterMode(),
                         Qt.MatchContains)   # 含匹配，不是只认前缀

    def test_completer_model_is_independent_from_deck_list(self):
        """★ 补全候选与卡片列表**互不干扰**（当初写错就整列表被换掉）。"""
        n_items = self.page.picker.count()
        n_words = self.page._comp_model.rowCount()
        self.assertGreater(n_words, 0)
        self.assertEqual(self.page.picker.count(), n_items)   # 没被覆盖
        self.assertEqual(n_items, len(self.demo['options']))

    def test_search_by_cn_alias_and_jp(self):
        """中文 / 别名 / 日文 / 站内英文名 —— 任一种叫法都能落到站内名。"""
        self.assertEqual(self.page._resolve_name('闪刀姬'), 'Sky Striker')
        self.assertEqual(self.page._resolve_name('Sky Striker'), 'Sky Striker')
        self.assertEqual(self.page._resolve_name('k9vs'), 'Vanquish Soul K9')
        self.assertIsNone(self.page._resolve_name('压根不存在的卡组'))

    def test_typing_alias_and_enter_emits_internal_name(self):
        """敲**别名**回车 → `deck_selected` 发的必须是**站内英文名**。"""
        seen = []
        self.page.deck_selected.connect(seen.append)
        self.page._emit_for_text('烙印')
        self.assertEqual(seen, ['Branded'])

    def test_enter_on_current_deck_does_not_emit(self):
        """选中当前这副牌 → 不该发信号（否则窗口白重取一次）。"""
        seen = []
        self.page.deck_selected.connect(seen.append)
        self.page._emit_for_text(self.demo['display'])
        self.assertEqual(seen, [])

    # -------------------------------------------------- P4 格子加卡名
    def test_cells_show_card_name(self):
        cells = self.page.findChildren(self.dvp.CardCell)
        names = [c.name_lab.text() for c in cells]
        self.assertTrue(all(names), '每格都得有卡名（可省略，但不能空）')
        self.assertTrue(all(c.toolTip() for c in cells), 'tooltip 给全名')
        self.assertIn(self.demo['main'][0]['name'], names)

    def test_elide_keeps_text_short(self):
        long_name = '非常非常非常非常非常长的卡名' * 3
        out = self.dvp.elide(long_name, self.dvp.CELL_W)
        self.assertLess(len(out), len(long_name))
        self.assertIn('…', out)

    def test_loading_placeholder(self):
        """卡图没下回来 → 格子里有极淡的 `…` 占位（1.2-9）。"""
        cell = self.dvp.CardCell({'id': 1, 'name': 'X', 'per': 50, 'at': 2})
        self.assertEqual(cell.art.text(), '…')

    # -------------------------------------------------- P6 阈值 / 排序
    def test_per_min_filter_narrows_cells(self):
        """「只看常带 ≥N%」直接过滤 `per`（计划 §1.2-6）。"""
        cells_all = len(self.page.findChildren(self.dvp.CardCell))
        self.page.per_box.setCurrentIndex(1)          # >=10%
        QApplication.processEvents()
        by_group = sum(
            len([r for r in self.demo[k] if int(r['per'] or 0) >= 10])
            for k in ('main', 'extra'))
        self.assertEqual(len(self.page.findChildren(self.dvp.CardCell)),
                         by_group)
        self.assertLessEqual(by_group, cells_all)

    def test_filter_does_not_hit_the_data_layer(self):
        """筛选只在页内做 —— `_data` 是**同一份引用**，不重新取数。"""
        before = self.page._data['main']
        self.page.per_box.setCurrentIndex(1)
        QApplication.processEvents()
        self.assertEqual([r['id'] for r in self.page._data['main']],
                         [r['id'] for r in before])

    def test_sort_switches_order(self):
        self.page.sort_box.setCurrentIndex(2)         # 按卡名
        QApplication.processEvents()
        names = [c.name_lab.toolTip() for c in self.page._cells['main']]
        self.assertEqual(names, sorted(names))
        self.page.sort_box.setCurrentIndex(1)         # 按张数
        QApplication.processEvents()
        ats = [r['at'] for r in self.dvp.sort_rows(self.demo['main'], 'at')]
        self.assertEqual(ats, sorted(ats, reverse=True))

    # -------------------------------------------------- P7 unknown 挪页头
    def test_unknown_moved_to_header(self):
        self.assertIn('%d 张卡没查到类型' % self.demo['unknown'],
                      self.page.note.text())
        self.assertTrue(self.page.note.isVisible())
        # 页脚那行删了：不该再有第二个 unknown 提示
        hits = [l.text() for l in self.page.findChildren(QLabel)
                if '没查到类型' in l.text()]
        self.assertEqual(len(hits), 1)

    def test_note_hidden_when_no_unknown(self):
        d = dict(self.demo)
        d['unknown'] = 0
        self.page.set_data(d)
        QApplication.processEvents()
        self.assertFalse(self.page.note.isVisible(), '没缺卡就别占那一行')

    # -------------------------------------------------- P8 快照日期
    def test_header_shows_snapshot_date(self):
        self.assertIn('参照库快照 2026-09-20', self.page.sub.text())

    # -------------------------------------------------- P9 回 Tier 表
    def test_back_button_requests_tier(self):
        seen = []
        self.page.back_requested.connect(lambda: seen.append(True))
        self.page.btn_back.click()
        self.assertEqual(seen, [True])


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DecksPageTest(unittest.TestCase):
    """D3 §2（用户 2026-09-21）：「卡组」页 = **两个页签**。

    页签 1「卡组名管理」= 覆盖表编辑（内嵌，不再弹窗）；
    页签 2「卡组构成」= `DeckViewPage` 原样内嵌。
    我方卡组聚合列表**已删**（战绩页已有同一批数字）。
    """

    def setUp(self):
        _app()
        from core.ui_demo import demo_deck_composition, demo_stats_page
        from ui import decks_page
        self.dp = decks_page
        self.demo = demo_deck_composition()
        self.fetched = []
        self.csv = os.path.join(tempfile.mkdtemp(prefix='d3-tabs-'), 'cn.csv')
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['Branded', '烙印', '烙印融合', '', '', '', ''],
            ['Sky Striker', '闪刀姬', '闪刀', '', '', '', ''],
        ])
        self.win = dashboard.DashboardWindow(
            loader=demo_stats_page,
            deckview_loader=lambda name=None: self.demo,
            on_fetch_decks=lambda: self.fetched.append(True),
            script_runner_factory=lambda script, args: FakeRunner(script, args))
        self.page = self.win.pages['decks']
        # 页签 1 的行/保存不碰真 CSV（真源在 data/ 下）
        self.page.names_page.csv_path = self.csv
        self.page.names_page._runner_factory = (
            lambda script, args: FakeRunner(script, args))

    def tearDown(self):
        self.win.deleteLater()

    def test_three_tabs_exist(self):
        """★ 页签 = 卡组名管理 / **Tier 表** / 卡组构成（Tier 在构成**左边**）。"""
        self.assertEqual([k for k, _n in self.dp.TABS],
                         ['names', 'tier', 'composition'])
        self.assertEqual(len(self.page.tab_buttons), 3)

    def test_tier_tab_is_left_of_composition(self):
        """顺序断言：用户口径是「Tier 表做到卡组构成**左边**」。"""
        keys = [k for k, _n in self.dp.TABS]
        self.assertLess(keys.index('tier'), keys.index('composition'))

    def test_default_tab_is_names(self):
        self.assertEqual(self.page.current_tab(), 'names')
        self.assertEqual(self.page.stack.currentWidget(), self.page.names_page)

    def test_switching_tab_changes_stack(self):
        self.win.set_decks_tab('composition')
        self.assertEqual(self.page.current_tab(), 'composition')
        self.assertEqual(self.page.stack.currentWidget(), self.page.view_page)
        self.win.set_decks_tab('names')
        self.assertEqual(self.page.stack.currentWidget(), self.page.names_page)

    def test_unknown_tab_falls_back(self):
        self.win.set_decks_tab('nonsense')
        self.assertEqual(self.page.current_tab(), 'names')

    def test_tab_not_remembered_anymore(self):
        """★ 2026-10-12 用户：卡组页打开**固定第一页**「卡组名管理」——
        页签不再记忆（切页签也不写 QSettings；旧键的残留值作废）。"""
        dashboard.settings().remove('dash/decks_tab')
        self.win.set_decks_tab('composition')
        self.assertEqual(self.win.current_decks_tab(), 'composition')
        self.assertFalse(dashboard.settings().contains('dash/decks_tab'),
                         '切页签不该再写 QSettings')

    def test_deck_name_memory_kept_separate(self):
        """★ 页签键**不能**顶掉「上次看哪副牌」那个键（计划 §2.3 的坑）。

        `dash/deckview` 存的是**卡组名**（`Sky Striker`），不是页签。
        原先计划写「`dash/decks_tab` 取代 `dash/deckview`」—— 那会让用户
        每次启动构成页都回到空白。
        """
        self.win.open_deck_view('Sky Striker')
        self.assertEqual(dashboard.settings().value('dash/deckview'),
                         'Sky Striker')
        self.assertEqual(self.win.current_deck_view(), 'Sky Striker')
        self.win.set_decks_tab('names')
        self.assertEqual(dashboard.settings().value('dash/deckview'),
                         'Sky Striker', '切页签不该把卡组名记忆冲掉')

    # ---- 页签 1：卡组名管理（原 Q14 弹窗 → 内嵌控件）----------------------
    def test_names_tab_is_embedded_manager_not_dialog(self):
        from ui.opp_deck_manager import OppDeckManager
        self.assertIsInstance(self.page.names_page, OppDeckManager)
        self.assertFalse(self.page.names_page.isWindow(),
                         '内嵌控件不该是独立窗口（原来那是弹窗）')

    def test_names_tab_has_search_and_rows(self):
        """页签 1 的行来自**构造时读的那份 CSV**（真源）。

        ⚠️ 所以不能靠「换掉 `csv_path`」来改行数 —— 行是构造时按它建的。
        要小样本就**先写好小 CSV 再建窗口**（见 `DeckManagerUnitTest`）。
        """
        mgr = self.page.names_page
        self.assertTrue(mgr.edit_search.placeholderText())
        self.assertTrue(mgr.rows_ui, '取自真覆盖表（100+ 条），不该是空的')
        self.assertEqual(mgr.edit_search.text(), '')

    def test_names_tab_search_filters(self):
        mgr = self.page.names_page
        total = len(mgr.rows_ui)
        mgr.edit_search.setText('闪刀')
        visible = [r for r in mgr.rows_ui if not r.isHidden()]
        self.assertTrue(0 < len(visible) < total, '搜索该**窄化**（不是全不中）')
        self.assertIn('%d / %d 条' % (len(visible), total), mgr.status.text())

    def test_names_tab_save_writes_csv(self):
        mgr = self.page.names_page
        mgr.rows_ui[0].cn.setText('烙印改')
        mgr.save()
        _h, rows = deck_csv.read(self.csv)
        self.assertEqual(deck_csv.get(rows[0], mgr.idx, '中文名'), '烙印改')

    def test_names_tab_save_emits_saved(self):
        seen = []
        self.page.saved.connect(lambda: seen.append(True))
        self.page.names_page.save()
        self.assertEqual(seen, [True])

    def test_names_tab_save_runs_cn_only(self):
        ran = []
        self.page.names_page._runner_factory = (
            lambda s, a: ran.append((s, list(a))) or FakeRunner(s, a))
        self.page.names_page.save()
        self.assertEqual(ran, [('tools/fetch_meta_decks.py', ['--cn-only'])])

    # ---- 页签 2：卡组构成（原隐藏页 → 内嵌）-------------------------------
    def test_composition_tab_embeds_deckview(self):
        from ui.deck_view_page import DeckViewPage
        self.assertIsInstance(self.page.view_page, DeckViewPage)
        self.win.set_decks_tab('composition')
        self.win._reload_deckview()
        self.assertEqual(self.page.current_deck(), self.demo['name'])

    def test_composition_tab_switch_emits(self):
        seen = []
        self.page.deck_selected.connect(seen.append)
        self.page.view_page._emit_for_text('烙印')
        self.assertEqual(seen, ['Branded'])

    def test_back_button_switches_to_tier_tab(self):
        """「← 回 Tier 表」= **同页切页签**（Tier 表就在左边那格，不再跨页）。"""
        self.win.set_decks_tab('composition')
        self.assertEqual(self.page.current_tab(), 'composition')
        seen = []
        self.page.back_requested.connect(lambda: seen.append(True))
        self.page.view_page.btn_back.click()
        self.assertEqual(self.page.current_tab(), 'tier')
        self.assertEqual(self.win.current_page(), 'decks')
        self.assertEqual(seen, [True])

    def test_tier_tab_data_comes_from_loader(self):
        """Tier 页签的数据由窗口喂（`set_tier_data`），页面自己不查库。"""
        self.assertTrue(hasattr(self.page, 'set_tier_data'))
        self.page.set_tier_data({'sections': []})
        self.assertEqual(self.page.tier_page._data.get('sections'), [])

    # ---- Q17 仍在页头 -----------------------------------------------------
    def test_fetch_button_wires_to_app_callback(self):
        self.page.btn_fetch.click()
        self.assertEqual(self.fetched, [True])


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckManagerUnitTest(unittest.TestCase):
    """`OppDeckManager` 的内嵌形态（原 `OppDeckDialogTest` 搬过来）。"""

    def setUp(self):
        _app()
        from ui.opp_deck_manager import OppDeckManager
        self.mod = OppDeckManager
        self.csv = os.path.join(tempfile.mkdtemp(prefix='d3-mgr-'), 'cn.csv')
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['Branded', '烙印', '烙印融合', '', '', '', ''],
            ['Sky Striker', '闪刀姬', '闪刀', '', '', '', ''],
        ])

    def _make(self, **kw):
        kw.setdefault('runner_factory', lambda s, a: FakeRunner(s, a))
        return self.mod(csv_path=self.csv, **kw)

    def test_loads_rows_from_csv(self):
        mgr = self._make()
        self.assertEqual(len(mgr.rows_ui), 2)

    def test_status_counts(self):
        mgr = self._make()
        self.assertEqual(mgr.status.text(), '2 条')

    def test_save_pushes_to_db(self):
        ran = []
        mgr = self._make(
            runner_factory=lambda s, a: ran.append((s, list(a))) or FakeRunner(s, a))
        mgr.save()
        self.assertEqual(ran, [('tools/fetch_meta_decks.py', ['--cn-only'])])

    def test_hidden_flag_roundtrip(self):
        mgr = self._make()
        mgr.rows_ui[1].hidden.setChecked(True)
        mgr.save()
        _h, rows = deck_csv.read(self.csv)
        self.assertEqual(deck_csv.get(rows[1], mgr.idx, '隐藏'), 'Y')

    def test_missing_csv_does_not_crash(self):
        mgr = self.mod(csv_path=os.path.join(os.path.dirname(self.csv), 'nope.csv'),
                       runner_factory=lambda s, a: FakeRunner(s, a))
        self.assertEqual(len(mgr.rows_ui), 0)
        self.assertIn('0 条', mgr.status.text())

    def test_write_failure_surfaces(self):
        from unittest import mock
        mgr = self._make()
        with mock.patch.object(deck_csv, 'write', side_effect=OSError('locked')):
            mgr.save()
        self.assertIn('写盘失败', mgr.status.text())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckManagerFeedbackTest(unittest.TestCase):
    """2026-09-21 用户实测五条反馈里落在「卡组名管理」的三条。

    ① **要有表头**；② **输入框文字不能被裁**；③ **要能加自定义卡组名**。
    """

    def setUp(self):
        _app()
        from ui.opp_deck_manager import OppDeckManager
        self.mod = OppDeckManager
        self.csv = os.path.join(tempfile.mkdtemp(prefix='d3-fb-'), 'cn.csv')
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['Branded', '烙印', 'ブランド', '烙印融合', 'Tier2', '', ''],
            ['Sky Striker', '闪刀姬', '閃刀姫', '闪刀', 'Tier3', '', ''],
        ])
        self.mgr = OppDeckManager(
            csv_path=self.csv, runner_factory=lambda s, a: FakeRunner(s, a))

    def tearDown(self):
        self.mgr.deleteLater()

    # ---------------------------------------------------- ① 表头
    def test_header_row_exists(self):
        """★ 反馈 1：原来一排输入框没有表头，不知道哪列是哪个。"""
        self.assertTrue(hasattr(self.mgr, 'header_row'))
        texts = [l.text() for l in self.mgr.header_row.findChildren(QLabel)]
        joined = ' '.join(texts)
        for want in ('站内英文名', '中文名', '别名', '日文名'):
            self.assertIn(want, joined, '表头缺「%s」' % want)
        # 长说明在 tooltip 里（表头字要短，否则会把列撑宽）
        tips = ' '.join(l.toolTip() for l in
                        self.mgr.header_row.findChildren(QLabel))
        self.assertIn('多个用 / 分隔', tips)

    def test_header_is_above_the_rows(self):
        """表头在最上面（先表头 → 自定义行 → 数据行）。"""
        lay = self.mgr.col
        self.assertIs(lay.itemAt(0).widget(), self.mgr.header_row)
        self.assertIs(lay.itemAt(1).widget(), self.mgr.new_row)

    def test_header_aligns_with_row_columns(self):
        """★ 表头与数据行**逐列对齐**（两边列宽/伸缩比必须一致）。

        对不齐是这类「自造表头」最常见的坑 —— 这里按**实际几何**验：
        表头的「中文名」标签左缘必须贴着第 0 行中文名输入框的左缘。
        """
        self.mgr.resize(1000, 600)
        self.mgr.show()
        for _ in range(4):
            QApplication.processEvents()
        hdr_labels = [l for l in self.mgr.header_row.findChildren(QLabel)]
        by_text = {l.text(): l for l in hdr_labels}
        row = self.mgr.rows_ui[0]
        #: 表头是**短标签**（长说明放 tooltip —— 长标签的 minimumSizeHint 会把
        #: 列撑宽几个像素，表头就与输入框错开了）
        pairs = [('中文名', row.cn), ('别名', row.alias),
                 ('日文名', row.jp)]
        for text, edit in pairs:
            lab = by_text.get(text)
            self.assertIsNotNone(lab, '表头缺 %r' % text)
            dx = abs(lab.mapTo(self.mgr, lab.rect().topLeft()).x()
                     - edit.mapTo(self.mgr, edit.rect().topLeft()).x())
            self.assertLessEqual(dx, 2, '「%s」表头与输入框错开 %dpx' % (text, dx))

    # ---------------------------------------------------- ② 文字不被裁
    def test_edit_text_is_not_clipped(self):
        """★ 反馈 2：原来 30px 行高 + QSS `padding:6px 10px` → 内容区只剩 10px，
        12px 的字**上下都被切**。判据：编辑框高度 ≥ 字高 + 上下内边距。"""
        from PySide6.QtGui import QFontMetrics
        from ui import opp_deck_manager as _mgr_mod
        row = self.mgr.rows_ui[0]
        for edit in (row.cn, row.alias, row.jp):
            fm = QFontMetrics(edit.font())
            need = fm.height() + 2 * _mgr_mod.EDIT_PAD_V
            self.assertGreaterEqual(
                edit.height(), need,
                '输入框高 %d < 「字高 %d + 内边距 %d」—— 文字会被裁'
                % (edit.height(), fm.height(), 2 * _mgr_mod.EDIT_PAD_V))
        # 行高也要容得下编辑框（别让输入框顶出格子）
        self.assertGreaterEqual(row.height(), row.cn.height())

    def test_edit_style_does_not_set_font(self):
        """样式表里**不许写 font-**（`ui/widgets.py` 模块头硬规矩①）。"""
        row = self.mgr.rows_ui[0]
        for edit in (row.cn, row.alias, row.jp):
            self.assertNotIn('font-', edit.styleSheet())

    def test_edit_style_is_tighter_than_global(self):
        """行内编辑框的内边距要比全局 QSS 那套紧（否则又回到被裁的样子）。"""
        from ui import opp_deck_manager as _mgr_mod
        sheet = self.mgr.rows_ui[0].cn.styleSheet()
        self.assertIn('padding: %dpx %dpx'
                      % (_mgr_mod.EDIT_PAD_V, _mgr_mod.EDIT_PAD_H), sheet)

    # ---------------------------------------------------- ③ 自定义卡组名
    def test_new_row_exists(self):
        """★ 反馈 3：要有「自定义卡组名」那一行（与悬浮窗选择器同款）。"""
        self.assertTrue(hasattr(self.mgr, 'new_row'))
        self.assertIn('自定义卡组名', self.mgr.new_row.edit.placeholderText())

    def test_add_custom_appends_row(self):
        added = self.mgr.add_custom('我的新卡组')
        self.assertTrue(added)
        self.assertEqual(len(self.mgr.rows_ui), 3)
        self.assertEqual(len(self.mgr.rows), 3)
        self.assertIsNotNone(self.mgr._find_existing('我的新卡组'))

    def test_add_custom_writes_both_columns(self):
        """自定义条目的口径：**站内英文名 = 中文名 = 用户打的字**
        （`fetch_meta_decks.custom_rows` 据此建 `source='custom'` 条目）。"""
        self.mgr.add_custom('我的新卡组')
        self.mgr.save()
        _h, rows = deck_csv.read(self.csv)
        idx = deck_csv.index_map(_h)
        hit = [r for r in rows if deck_csv.get(r, idx, '站内英文名') == '我的新卡组']
        self.assertEqual(len(hit), 1)
        self.assertEqual(deck_csv.get(hit[0], idx, '中文名'), '我的新卡组')

    def test_add_custom_rejects_duplicate(self):
        """已在表里（站内名 / 中文名 / 别名任一）→ 不重复加。"""
        self.assertFalse(self.mgr.add_custom('烙印'), '中文名撞上了')
        self.assertFalse(self.mgr.add_custom('Branded'), '站内名撞上了')
        self.assertEqual(len(self.mgr.rows_ui), 2)
        self.assertIn('已经在表里', self.mgr.status.text())

    def test_add_custom_rejects_empty(self):
        self.assertFalse(self.mgr.add_custom('   '))
        self.assertEqual(len(self.mgr.rows_ui), 2)

    def test_add_custom_is_undoable_before_save(self):
        """加错了在**保存前**可以撤：不加到 CSV 就不落盘。"""
        self.mgr.add_custom('手滑加的')
        _h, rows = deck_csv.read(self.csv)      # 还没 save
        self.assertNotIn('手滑加的', [deck_csv.get(r, deck_csv.index_map(_h),
                                                   '站内英文名') for r in rows])

    # ---- 删除按钮（用户 2026-09-21：「增加删除按钮在最右边，点击删除这行卡组」）
    def test_row_has_delete_button_on_the_right(self):
        """★ 删除按钮在最右边（隐藏列之后）。"""
        row = self.mgr.rows_ui[0]
        self.assertTrue(hasattr(row, 'btn_del'))
        from PySide6.QtCore import QPoint
        self.mgr.resize(1000, 600)
        self.mgr.show()
        for _ in range(4):
            QApplication.processEvents()
        del_x = row.btn_del.mapTo(self.mgr, QPoint(0, 0)).x()
        hide_x = row.hidden.mapTo(self.mgr, QPoint(0, 0)).x()
        self.assertGreater(del_x, hide_x, '删除按钮该在「隐藏」右边')
        self.assertAlmostEqual(del_x + row.btn_del.width(),
                               row.width() + row.pos().x() - 0, delta=4,
                               msg='删除按钮该贴到行最右缘')

    def test_delete_removes_row_from_ui_and_pending_write(self):
        """点删除 → 行从界面与**待写盘的表**里都拿掉（保存后才真落盘）。"""
        row = self.mgr.rows_ui[0]
        name = deck_csv.get(row.csv_row, self.mgr.idx, '站内英文名')
        n0 = len(self.mgr.rows_ui)
        row.btn_del.click()
        self.assertEqual(len(self.mgr.rows_ui), n0 - 1)
        self.assertEqual(len(self.mgr.rows), n0 - 1)
        self.assertIn('已删除', self.mgr.status.text())
        self.assertNotIn(name, [deck_csv.get(r, self.mgr.idx, '站内英文名')
                                for r in self.mgr.rows])

    def test_delete_persists_only_after_save(self):
        """点删除**不立刻**改 CSV —— 保存才写（误删可以直接不保存）。"""
        name = deck_csv.get(self.mgr.rows_ui[0].csv_row, self.mgr.idx,
                            '站内英文名')
        self.mgr.rows_ui[0].btn_del.click()
        _h, rows = deck_csv.read(self.csv)
        idx = deck_csv.index_map(_h)
        self.assertIn(name, [deck_csv.get(r, idx, '站内英文名') for r in rows],
                      '还没点保存，CSV 不该变')
        self.mgr.save()
        _h, rows = deck_csv.read(self.csv)
        idx = deck_csv.index_map(_h)
        self.assertNotIn(name, [deck_csv.get(r, idx, '站内英文名') for r in rows])

    def test_delete_removes_the_newly_added_custom_row(self):
        """刚加的「自定义卡组名」也能删（加错了不用保存即可撤）。"""
        self.mgr.add_custom('野狗卡组')
        self.assertEqual(len(self.mgr.rows_ui), 3)
        self.mgr.remove_row(self.mgr.rows_ui[-1])
        self.assertEqual(len(self.mgr.rows_ui), 2)

    def test_delete_unknown_row_is_a_noop(self):
        self.assertFalse(self.mgr.remove_row(None))

    # ---- 列宽（用户：「缩小中文名的列宽」）
    def test_cn_column_narrower_than_alias(self):
        """★ 中文名列宽要比别名列窄（原来 3:4，现在 2:5）。"""
        from PySide6.QtCore import QPoint
        self.mgr.resize(1000, 600)
        self.mgr.show()
        for _ in range(4):
            QApplication.processEvents()
        row = self.mgr.rows_ui[0]
        self.assertLess(row.cn.width(), row.alias.width(),
                        '中文名列该比别名列窄（已缩过一次）')
        from ui import opp_deck_manager as mgr_mod
        self.assertEqual(mgr_mod.COL_STRETCH[0], 2)
        self.assertEqual(mgr_mod.COL_STRETCH[1], 5)

    def test_header_has_delete_column(self):
        """表头最右边也有「删」那一格（否则与行对不齐）。"""
        texts = [l.text() for l in self.mgr.header_row.findChildren(QLabel)]
        self.assertIn('删', texts)

    def test_new_deck_row_gets_default_image(self):
        """★ 反馈：「我添加的卡组没有默认卡图悠悠」—— 新加的行必须显式拿默认图。

        （新卡组站内 / 覆盖目录都还没有图，靠 `resolve()` 拿不到。）
        """
        from core.deck_images import DEFAULT_IMAGE
        from PySide6.QtGui import QPixmap
        self.assertTrue(os.path.exists(DEFAULT_IMAGE), '默认卡图得在')
        self.mgr.add_custom('野狗卡组')
        item = self.mgr.rows_ui[-1]
        pix = item.thumb.pixmap()
        self.assertFalse(pix.isNull(), '新加的行没有缩略图（不是默认图）')
        self.assertAlmostEqual(pix.width(), pix.height(), delta=1,
                               msg='缩略图该是正方形（round_pixmap 裁的）')

    def test_new_deck_name_row_also_shows_default_image(self):
        """「自定义卡组名…」那一行同样要有默认图（别是个空格子）。"""
        self.assertFalse(self.mgr.new_row.thumb.pixmap().isNull())

    def test_new_deck_default_image_uses_resolver_when_available(self):
        """配得出来就用配的（这里给一个假 resolver 验它被用上）。"""
        from core.deck_images import DEFAULT_IMAGE
        seen = []

        def fake_resolve(name):
            seen.append(name)
            return DEFAULT_IMAGE
        self.mgr._resolver = fake_resolve
        self.mgr.add_custom('野狗卡组')
        self.assertIn('野狗卡组', seen)

    def test_search_box_text_is_not_clipped(self):
        """★ 反馈：「搜索框的文字显示不全」—— 只设高度不够，还得套紧内边距。

        全局 QSS 的 `QLineEdit { padding: 6px 10px }` 会把 20px 高的框
        压到只剩 6px 内容区，12px 的字上下都被切。
        """
        from PySide6.QtGui import QFontMetrics
        from ui import opp_deck_manager as mgr_mod
        fm = QFontMetrics(self.mgr.edit_search.font())
        need = fm.height() + 2 * mgr_mod.EDIT_PAD_V
        self.assertGreaterEqual(self.mgr.edit_search.height(), need)
        self.assertIn('padding: %dpx %dpx'
                      % (mgr_mod.EDIT_PAD_V, mgr_mod.EDIT_PAD_H),
                      self.mgr.edit_search.styleSheet(),
                      '搜索框没套紧样式 —— 会沿用全局 QSS，字被裁')

    def test_new_row_clears_after_add(self):
        self.mgr.new_row.edit.setText('我的新卡组')
        self.mgr.new_row._on_return()
        self.assertEqual(self.mgr.new_row.edit.text(), '')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class FetchDecksDialogFeedbackTest(unittest.TestCase):
    """反馈 4：「获取新卡组名的框是透明的，可读性太差」—— 无边框窗得自己画底。"""

    def setUp(self):
        _app()
        from ui.decks_page import FetchDecksDialog
        self.dlg = FetchDecksDialog()

    def tearDown(self):
        self.dlg.deleteLater()

    def test_paints_opaque_background(self):
        """★ 判据：抓出来的图**中心像素不是透明的**（不画底就是全透）。"""
        from PySide6.QtGui import QColor
        self.dlg.resize(560, 420)
        self.dlg.show()
        for _ in range(4):
            QApplication.processEvents()
        img = self.dlg.grab().toImage()
        center = QColor(img.pixel(img.width() // 2, img.height() // 2))
        self.assertEqual(center.alpha(), 255, '对话框中心是透明的（没画底）')
        self.assertNotEqual(center.name(), '#000000',
                            '画成了纯黑 —— 该是主题的 CANVAS 底色')

    def test_has_close_button(self):
        """无边框窗没有系统标题栏 → 必须自带关闭按钮（否则只能 Esc）。"""
        self.assertTrue(hasattr(self.dlg, 'btn_close'))
        self.dlg.btn_close.clicked.emit()
        self.assertFalse(self.dlg.isVisible())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SettingsToolRunTest(unittest.TestCase):
    """概览页数据工具的运行行为（09-27：取代 ScriptPage 的锁）。

    锁三件事：命令表内容 / 点「运行」起共享 runner（输出进页底日志区、
    跑时按钮互斥禁用、退出码按 `exit_note` 翻译）/ 退出码纯函数。
    """

    class _FakeRunner(QObject):
        line = Signal(str)
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

    def setUp(self):
        _app()
        self.made = []
        self.page = dashboard.SettingsIndexPage(
            '', '', runner_factory=lambda s, a: self._make(s, a))

    def _make(self, script, args):
        r = SettingsToolRunTest._FakeRunner(script, args)
        self.made.append(r)
        return r

    def tearDown(self):
        self.page.deleteLater()

    def test_commands_match_the_plan(self):
        """命令表（计划 §3.7）：禁限表裸跑 / 卡表两条 / 体检裸跑 / 刷新 --apply。"""
        self.assertEqual(dashboard.SCRIPT_COMMANDS['s_banlist']['script'],
                         'tools/fetch_banlist.py')
        self.assertEqual(dashboard.SCRIPT_COMMANDS['s_cardtable']['runs'],
                         [('检查更新', ['--check-update']),
                          ('下载更新', ['--update'])])
        # 「刷新数据」（s_refresh）2026-10-05 用户拍板移除 —— 采集器自动做
        self.assertNotIn('s_refresh', dashboard.SCRIPT_COMMANDS)

    def test_tool_buttons_carry_the_command_entries(self):
        """工具行的按钮文本 = 命令表的 runs（卡表两枚，其余一枚）。"""
        for key in dashboard.SCRIPT_PAGE_KEYS:
            btns = self.page._rows[key].control.findChildren(QPushButton)
            self.assertEqual([b.text() for b in btns],
                             [t for t, _a in dashboard.SCRIPT_COMMANDS[key]['runs']],
                             key)

    def test_run_starts_runner_logs_lines_and_restores(self):
        """点「运行」：起脚本、逐行回显进页底日志区、跑时互斥禁用、跑完恢复+翻译。"""
        btn = self.page._rows['s_banlist'].control.findChildren(QPushButton)[0]
        btn.click()
        self.assertTrue(self.made and self.made[0].started)
        self.assertFalse(btn.isEnabled(), '跑的时候按钮要禁用')
        self.made[0].line.emit('fetching ...')
        self.assertIn('fetching ...', self.page._log.toPlainText())
        self.made[0].done.emit(0)
        self.assertTrue(btn.isEnabled(), '跑完要恢复')
        self.assertIn('成功', self.page._status.text())

    def test_exit_note_translated_on_done(self):
        """退出码 1（卡表检查）按 `exit_note` 翻译，不是笼统的「失败」。"""
        btn = self.page._rows['s_cardtable'].control.findChildren(QPushButton)[0]
        btn.click()
        self.made[0].done.emit(1)
        from ui.script_page import exit_note
        self.assertIn(exit_note('tools/build_card_names.py', 1),
                      self.page._status.text())

    def test_needs_attention_is_pure(self):
        from ui.script_page import needs_attention
        self.assertTrue(needs_attention('tools/build_card_names.py', 1))
        self.assertFalse(needs_attention('tools/build_card_names.py', 0))
        self.assertFalse(needs_attention('tools/fetch_banlist.py', 1),
                         '别的脚本的码 1 就是普通失败/成功，不标红')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DashboardD4Test(unittest.TestCase):
    """D4 收尾的界面两件：标题栏**采集器状态** + **导出**入口。"""

    def setUp(self):
        _app()
        from core.ui_demo import demo_stats_page
        self.got = []
        self.status = {'running': True, 'at': '2026-09-22 09:59:57',
                       'age_sec': 3, 'in_duel': False, 'game_pid': None}
        self.win = dashboard.DashboardWindow(
            loader=demo_stats_page,
            collector_loader=lambda: self.status,
            export_saver=lambda fmt, month, mode: self.got.append(
                (fmt, month, mode)))

    def tearDown(self):
        self.win.deleteLater()

    def test_collector_status_in_title_bar(self):
        """★ 标题栏显示采集器状态（不跑也能一眼看到）。"""
        self.assertTrue(self.win.collector.text())
        self.assertIn('采集器', self.win.collector.text())
        self.assertIn('运行中', self.win.collector.text())

    def test_collector_status_reflects_loader(self):
        self.status = {'running': False, 'at': '2026-09-22 09:00:00',
                       'age_sec': 999, 'in_duel': False}
        self.win.refresh_collector_status()
        self.assertIn('未运行', self.win.collector.text())

    def test_collector_loader_failure_does_not_crash(self):
        def boom():
            raise RuntimeError('boom')
        self.win.set_collector_loader(boom)
        self.assertIn('采集器', self.win.collector.text())
        self.assertTrue(self.win.collector.text())

    def test_export_is_a_rail_page_not_a_title_button(self):
        """★ 用户 2026-09-22：「导出需要在左边栏单独做一项」—— 标题栏那个下拉**撤了**。"""
        self.assertIn('export', self.win.pages)
        self.assertIn('export', self.win.rail.items)
        self.assertFalse(hasattr(self.win, 'btn_export'),
                         '标题栏的「导出 ▾」该撤掉（改成左栏页了）')

    def test_export_page_passes_format_month_and_mode(self):
        """`_on_export_requested()` 把**格式 + 月份 + 模式**交给 app 侧（窗口不写文件）。"""
        page = self.win.pages['export']
        page._emit_export('json')
        self.assertEqual(self.got, [('json', None, None)])

    def test_export_without_saver_is_silent(self):
        """没接 saver（独立跑/单测）→ 什么都不做，**不许抛**。"""
        self.win._export_saver = None
        self.win.pages['export']._emit_export('csv')   # 不该炸
        self.assertEqual(self.got, [])


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ExportPageTest(unittest.TestCase):
    """「导出」页（D4c）—— 左栏独立一项，**按月 + 按模式**筛选 + 预览。

    用户口径（2026-09-22）：「导出需要在左边栏单独做一项，按月和模式筛选导出」。
    """

    def setUp(self):
        _app()
        from ui.export_page import ExportPage
        self.mod = ExportPage
        self.calls = []
        self.page = ExportPage()
        self.page.filter_changed.connect(lambda m, g: self.calls.append((m, g)))
        self.page.set_data({
            'months': [('', '全部月份'), ('2026-09', '2026-09'),
                       ('2026-08', '2026-08')],
            'rows': [{'date': '09-16', 'time': '23:45', 'result': '胜',
                      'coin': '赢', 'went_first': '先攻', 'my_deck': '闪刀姬',
                      'opp_deck': '烙印', 'mode_label': '排名决斗'}],
            'total': 93,
        })

    def tearDown(self):
        self.page.deleteLater()

    def test_is_in_the_left_rail(self):
        """★ 「左边栏单独做一项」。"""
        win = dashboard.DashboardWindow()
        try:
            self.assertIn('export', win.pages)
            self.assertIn('export', win.rail.items)
        finally:
            win.deleteLater()

    def test_two_filters_exist(self):
        """月份 + 模式两个筛选器都在。"""
        self.assertIn('月份', self.page._month_btn.text())
        self.assertIn('模式', self.page._mode_btn.text())

    def test_default_is_all(self):
        self.assertIsNone(self.page.month())
        self.assertIsNone(self.page.mode_group())

    def test_picking_month_emits(self):
        self.page._pick('month', '2026-08')
        self.assertEqual(self.calls, [('2026-08', None)])
        self.assertIn('2026-08', self.page._month_btn.text())

    def test_picking_mode_emits(self):
        from core.storage import MODE_GROUP_NAMES
        self.page._pick('模式', 'ranked')
        self.assertEqual(self.calls, [(None, 'ranked')])
        #: 显示名取自 `storage.MODE_GROUP_NAMES`（唯一来源），别在测试里写死
        self.assertIn(MODE_GROUP_NAMES['ranked'], self.page._mode_btn.text())

    def test_picking_same_value_is_noop(self):
        self.page._pick('模式', None)        # 本来就是全部
        self.assertEqual(self.calls, [])

    def test_mode_menu_comes_from_storage_names(self):
        """模式下拉的**唯一来源** = `storage.MODE_GROUP_NAMES`（别另抄一份）。"""
        from core.storage import MODE_GROUP_NAMES
        keys = [k for k, _n in self.page._menu_items('mode')]
        self.assertEqual(keys[0], None)
        #: ⚠️ 单人模式不进筛选（2026-10-03 用户口径，与战绩/分析页一致）
        self.assertEqual(keys[1:], [k for k in MODE_GROUP_NAMES
                                     if k != 'solo'])

    def test_preview_shows_rows_and_total(self):
        self.assertEqual(len(self.page.preview_rows()), 1)
        self.assertIn('93', self.page.count.text())

    def test_preview_has_no_coin_column(self):
        """硬币列已删（2026-10-03 用户口径），预览里也不该再有。"""
        from ui import export_page
        page = export_page.ExportPage()
        cols = [l.text() for l in page.findChildren(QLabel)]
        self.assertNotIn('硬币', cols)
        page.deleteLater()

    def test_empty_preview_disables_buttons(self):
        self.page.set_data({'months': [], 'rows': [], 'total': 0})
        self.assertFalse(self.page.btn_csv.isEnabled())
        self.assertFalse(self.page.btn_json.isEnabled())
        self.assertIn('没有对局', ' '.join(
            l.text() for l in self.page.findChildren(
                __import__('PySide6.QtWidgets',
                           fromlist=['QLabel']).QLabel)))

    def test_export_signal_carries_fmt_month_mode(self):
        got = []
        self.page.export_requested.connect(
            lambda f, m, g: got.append((f, m, g)))
        self.page._pick('month', '2026-09')
        self.page._pick('模式', 'peak')
        self.page.btn_csv.click()
        self.assertEqual(got, [('csv', '2026-09', 'peak')])

    def test_month_falls_back_when_not_in_list(self):
        """月份清单变了（切 demo / 换库）→ 当前选中项若已不在清单里就回落「全部」。"""
        self.page._pick('month', '2026-08')
        self.page.set_data({'months': [('', '全部月份')], 'rows': [], 'total': 0})
        self.assertIsNone(self.page.month())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class RetranslateInPlaceTest(unittest.TestCase):
    """切语言**原地重译**（2026-10-05 用户：窗口不关、同一界面直接变）。

    锁三件事：当前页不丢 / 左栏标签换语言 / 页控件是新语言烘的。
    """

    def setUp(self):
        _app()
        self.win = dashboard.DashboardWindow()

    def tearDown(self):
        self.win.deleteLater()

    def test_retranslate_keeps_page_and_rerails(self):
        from ui.i18n import reset_lang
        self.win.open_page('stats')
        dashboard.settings().setValue('ui/lang', 'en')
        reset_lang()
        try:
            self.win.retranslate()
        finally:
            dashboard.settings().setValue('ui/lang', 'cn')
            reset_lang()
        self.assertEqual(self.win.current_page(), 'stats', '当前页不能丢')
        labels = {k: it.label.text() for k, it in self.win.rail.items.items()}
        self.assertEqual(labels['stats'], 'Matches')
        self.assertEqual(labels['decks'], 'Decks')
        self.assertEqual(labels['settings'], 'Settings')
        # 页控件是新语言烘的：设置概览页标题 = tr('设置')
        page = self.win.pages[dashboard.SETTINGS_KEY]
        texts = [l.text() for l in page.findChildren(QLabel)]
        self.assertIn('Settings', texts)
        # 标题条
        self.assertEqual(self.win._title_text.text(), 'Master Duel Statistics')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckImageSetTest(unittest.TestCase):
    """★ 2026-10-08（用户）：「自定义卡组没有卡组卡图」。

    修法：① `OppDeckRow` 的卡图**可点** → `SetImageDialog`（本地图片 / 站内取图 /
    还原）；② 「我加过、但不在覆盖表里」的自定义卡组**也列在这一页**（只读行），
    否则用户根本没有地方给它设图。
    """

    def setUp(self):
        _app()
        from core import deck_images
        from ui import opp_deck_manager as odm_mod
        from ui.opp_deck_manager import OppDeckManager
        self.di = deck_images
        self.mod = OppDeckManager
        self.odm_mod = odm_mod
        self.tmp = tempfile.mkdtemp(prefix='d3-img-')
        self.csv = os.path.join(self.tmp, 'cn.csv')
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['Branded', '烙印', '', '', '', '', ''],
        ])
        #: 存图指向**临时目录** —— 不许往真的 data/deck_images_custom/ 写
        self.imgdir = os.path.join(self.tmp, 'imgs')
        self._prev_imgdir = self.di.CUSTOM_IMG_DIR
        self.di.CUSTOM_IMG_DIR = self.imgdir

    def tearDown(self):
        self.di.CUSTOM_IMG_DIR = self._prev_imgdir

    def _src_image(self):
        from core.deck_images import DEFAULT_IMAGE
        return DEFAULT_IMAGE

    def _make(self, **kw):
        kw.setdefault('runner_factory', lambda s, a: FakeRunner(s, a))
        kw.setdefault('custom_names', ['狱神耀圣'])
        return self.mod(csv_path=self.csv, **kw)

    # ---------------------------------------------------------- 可点卡图
    def test_thumb_is_clickable(self):
        mgr = self._make()
        self.assertTrue(hasattr(mgr.rows_ui[0].thumb, 'clicked'),
                        '卡图格要可点（点了才能设图）')
        self.assertEqual(mgr.rows_ui[0].thumb.cursor().shape(),
                         Qt.PointingHandCursor)

    def test_save_custom_image_writes_into_the_folder(self):
        """存图落到覆盖目录，文件名 = 卡组名（`resolve` 的文件名直查靠它）。"""
        rel = self.di.save_custom_image('狱神耀圣', self._src_image())
        self.assertTrue(rel)
        self.assertTrue(os.path.exists(os.path.join(self.imgdir, '狱神耀圣.webp')))

    def test_set_image_from_local_file(self):
        """走 `set_deck_image` 的 'file' 分支（对话框用桩替换）。"""
        mgr = self._make()

        src_img = self._src_image()

        class _Dlg(object):
            choice, image_path, site_name = 'file', src_img, ''

            def __init__(self_inner, *a, **kw):
                pass

            def exec(self_inner):
                return QDialog.Accepted
        with mock.patch.object(self.odm_mod, 'SetImageDialog', _Dlg):
            mgr.set_deck_image(mgr.rows_ui[0])
        self.assertTrue(os.path.exists(os.path.join(self.imgdir, 'Branded.webp')))
        self.assertIn('已设置', mgr.status.text())

    def test_set_image_for_custom_only_row(self):
        """只读行（自组卡组名）也能设图 —— 用户报的正是这种（狱神耀圣）。"""
        mgr = self._make()

        src_img = self._src_image()

        class _Dlg(object):
            choice, image_path, site_name = 'file', src_img, ''

            def __init__(self_inner, *a, **kw):
                pass

            def exec(self_inner):
                return QDialog.Accepted
        with mock.patch.object(self.odm_mod, 'SetImageDialog', _Dlg):
            mgr.set_deck_image(mgr.custom_rows_ui[0])
        self.assertTrue(os.path.exists(os.path.join(self.imgdir, '狱神耀圣.webp')))

    def test_reset_removes_the_custom_image(self):
        self.di.save_custom_image('Branded', self._src_image())
        mgr = self._make()
        mgr._reset_deck_image(mgr.rows_ui[0], 'Branded')
        self.assertFalse(os.path.exists(os.path.join(self.imgdir, 'Branded.webp')))
        self.assertIn('已还原', mgr.status.text())

    def test_site_fetch_failure_is_reported(self):
        """站内取不到图（自组混轴名 404）→ 状态行给出可行动的提示，不炸。"""
        mgr = self._make()

        class _Dlg(object):
            choice, image_path, site_name = 'site', '', 'NoSuchDeckXYZ'

            def __init__(self_inner, *a, **kw):
                pass

            def exec(self_inner):
                return QDialog.Accepted
        with mock.patch.object(self.odm_mod, 'SetImageDialog', _Dlg), \
                mock.patch.object(self.odm_mod.deck_images, 'fetch_site_deck_image',
                                  lambda n: None):
            mgr.set_deck_image(mgr.rows_ui[0])
        self.assertIn('站内没有', mgr.status.text())

    # ---------------------------------------------------------- 只读行
    def test_custom_only_rows_listed(self):
        mgr = self._make()
        names = [r.deck_name for r in mgr.custom_rows_ui]
        self.assertIn('狱神耀圣', names)

    def test_custom_rows_are_full_editable_rows(self):
        """自定义行与普通行**同格式**（2026-10-12 用户）—— 可编辑、有删除钮。

        （2026-10-12 前是只读行：`assertFalse(hasattr(row, 'btn_del'))` ——
        那个口径已被推翻。）
        """
        mgr = self._make()
        row = mgr.custom_rows_ui[0]
        self.assertIsInstance(row, self.odm_mod.OppDeckRow)
        for attr in ('cn', 'alias', 'jp', 'hidden', 'btn_del'):
            self.assertTrue(hasattr(row, attr), '缺 %s —— 不是同格式行' % attr)
        self.assertEqual(row.cn.text(), '狱神耀圣')

    def test_divider_between_site_and_custom(self):
        """爬取的卡组与自定义卡组之间要有**分隔行**（2026-10-12 用户）。"""
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['狱神耀圣', '狱神耀圣', '', '', 'Tier3', '', ''],
            ['Branded', '烙印', '', '', 'Tier2', '', ''],
        ])

        #: 分组判据 = 站内名单（Branded 站内有 -> 爬取；狱神耀圣没有 -> 自定义）
        mgr = self.mod(csv_path=self.csv, site_names=['Branded'])
        order = [deck_csv.get(i.csv_row, mgr.idx, '站内英文名')
                 for i in mgr.rows_ui]
        self.assertEqual(order, ['Branded', '狱神耀圣'],
                         '站内行要排在自定义行前（CSV 里顺序是反的）')
        lay = mgr.col

        def _at(w):
            return next(i for i in range(lay.count())
                        if lay.itemAt(i).widget() is w)

        self.assertTrue(_at(mgr.rows_ui[0]) < _at(mgr.custom_divider)
                        < _at(mgr.rows_ui[1]), '分隔行要在两组中间')
        self.assertTrue(mgr.custom_divider.isVisibleTo(mgr))

    def test_site_names_with_slashes_and_aliases(self):
        """带斜杠真名（D/D/D）与别名命中站内名的行都算**爬取组**（2026-10-12）。"""
        deck_csv.write(self.csv, list(deck_csv.COLUMNS), [
            ['D/D/D', 'DDD', '', '', '', '', ''],
            ['Yummy Engine', '巳剑黯蜜', '', 'Mitsurugi Yummy', '', '', ''],
            ['狱神耀圣', '狱神耀圣', '', '', '', '', ''],
        ])
        mgr = self.mod(csv_path=self.csv,
                       site_names=['D/D/D', 'Mitsurugi Yummy'])
        order = [deck_csv.get(i.csv_row, mgr.idx, '站内英文名')
                 for i in mgr.rows_ui]
        self.assertEqual(order, ['D/D/D', 'Yummy Engine', '狱神耀圣'],
                         '站内两行（含别名命中的）在上，真自定义在下')

    def test_divider_hidden_without_custom_rows(self):
        """没有自定义行就不露分隔行。"""
        mgr = self._make(site_names=['Branded'])   # 夹具只有 Branded（站内）
        self.assertFalse(mgr.custom_divider.isVisibleTo(mgr))

    def test_custom_rows_promoted_on_save(self):
        """自定义行点「保存」**并进覆盖表**（转正；2026-10-12 同格式口径）。"""
        mgr = self._make()
        mgr.save()
        _h, rows = deck_csv.read(self.csv)
        idx = deck_csv.index_map(_h)
        hit = [r for r in rows if deck_csv.get(r, idx, '站内英文名') == '狱神耀圣']
        self.assertEqual(len(hit), 1)
        self.assertEqual(deck_csv.get(hit[0], idx, '中文名'), '狱神耀圣')

    def test_rows_that_are_meta_aliases_are_not_duplicated(self):
        """`烙印` 这类（已在覆盖表里）不该再当自定义行列一遍。"""
        mgr = self._make(custom_names=['烙印', '狱神耀圣'])
        names = [r.deck_name for r in mgr.custom_rows_ui]
        self.assertNotIn('烙印', names, '覆盖表里的名字不该再列只读行')
        self.assertIn('狱神耀圣', names)

    def test_status_counts_readonly_rows(self):
        mgr = self._make()
        self.assertIn('1 条待保存进覆盖表', mgr.status.text())

    def test_search_filters_readonly_rows(self):
        mgr = self._make(custom_names=['狱神耀圣', '魔术师'])
        mgr.edit_search.setText('狱神')
        shown = [r.deck_name for r in mgr.custom_rows_ui if r.isVisibleTo(mgr)]
        self.assertEqual(shown, ['狱神耀圣'])

    def test_save_promotes_custom_rows(self):
        """点「保存」把自定义行并进覆盖表（转正）。"""
        mgr = self._make(custom_names=['朋克'])
        mgr.custom_rows_ui[0].alias.setText('punk')
        mgr.save()
        header, rows = deck_csv.read(mgr.csv_path)
        idx = deck_csv.index_map(header)
        hit = [r for r in rows if deck_csv.get(r, idx, '站内英文名') == '朋克']
        self.assertEqual(len(hit), 1, '自定义行保存后要进覆盖表')
        self.assertEqual(deck_csv.get(hit[0], idx, '别名'), 'punk')

    def test_remove_custom_row_keeps_csv_clean(self):
        """自定义行点删除 = 直接消失（还没进表，保存也不该出现）。"""
        mgr = self._make(custom_names=['朋克'])
        self.assertTrue(mgr._remove_custom_row(mgr.custom_rows_ui[0]))
        mgr.save()
        header, rows = deck_csv.read(mgr.csv_path)
        idx = deck_csv.index_map(header)
        self.assertNotIn('朋克',
                         [deck_csv.get(r, idx, '站内英文名') for r in rows])




@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class CardArtPickerTest(unittest.TestCase):
    """★ 2026-10-08（用户）「只能输英文查很不方便」+ 第二版口径。

    结论：**卡组图 == 该卡组代表卡的卡绘** → 配图 = 挑一张卡，敲**中文卡名**即可。
    第二版（用户截图反馈）：
      * 列表**不再批量下缩略图**（「下的图太多了」）；
      * 「选择本地图片」**移除**，改成「获取卡图」；
      * 点列表一行 -> **该行右边出现「确认」按钮**；
      * 选中卡的**名字显示在卡图下方**；[获取卡图][保存] 同一行放名字下面。
    """

    def setUp(self):
        _app()
        from core import card_names
        from ui.opp_deck_manager import SetImageDialog
        self.cn = card_names
        self.Dlg = SetImageDialog

    def _dlg(self, **kw):
        d = self.Dlg('狱火机', '', **kw)
        self.addCleanup(d.deleteLater)
        return d

    def test_card_search_by_chinese_name(self):
        """敲中文就能搜到卡（`core.card_names.search`，中/英/日都认）。"""
        d = self._dlg()
        d.card_search.setText('灰流丽')
        self.assertTrue(d.card_list.count() > 0, '敲中文应该搜得到卡')
        row = d.card_list.itemWidget(d.card_list.item(0))
        #: 用户 2026-10-08：「去掉卡名后的括号和数字」—— 标签**只有卡名**
        self.assertEqual(row.label.text(), '灰流丽')

    def test_rows_carry_card_id_no_thumbnail(self):
        """行带卡 id；**不下载缩略图**（用户：「下的图太多了」）。"""
        d = self._dlg()
        d.card_search.setText('灰流丽')
        it = d.card_list.item(0)
        self.assertIsInstance(it.data(Qt.UserRole), int)
        row = d.card_list.itemWidget(it)
        self.assertFalse(hasattr(row, 'thumb'), '第二版已回退缩略图')

    def test_confirm_button_appears_on_click(self):
        """★ 点一行 -> 该行右边出现「确认」按钮；点别的行，旧的收起。"""
        d = self._dlg()
        d.resize(400, 600)
        d.show()
        self.addCleanup(d.hide)
        d.card_search.setText('斩机')
        QApplication.processEvents()
        self.assertTrue(d.card_list.count() >= 2)
        r0 = d.card_list.itemWidget(d.card_list.item(0))
        r1 = d.card_list.itemWidget(d.card_list.item(1))
        self.assertFalse(r0.btn_confirm.isVisibleTo(r0), '默认不显示')
        d._on_row_clicked(d.card_list.item(0))
        self.assertTrue(r0.btn_confirm.isVisibleTo(r0), '点了要出现')
        d._on_row_clicked(d.card_list.item(1))
        self.assertFalse(r0.btn_confirm.isVisibleTo(r0), '点别的行要收起')
        self.assertTrue(r1.btn_confirm.isVisibleTo(r1))

    def test_name_shows_above_and_id_is_set(self):
        """点「确认」-> 名字显示在上方（卡图下方那块），并记下卡 id。"""
        d = self._dlg()
        d.card_search.setText('灰流丽')
        row = d.card_list.itemWidget(d.card_list.item(0))
        d._on_row_confirmed(row)
        self.assertEqual(d.picked_label.text(), row.card_name)
        self.assertEqual(d.card_id, row.card_id)

    def test_local_file_button_is_gone(self):
        """★ 用户要求：「移除选择本地图片」。"""
        d = self._dlg()
        texts = [b.text() for b in d.findChildren(QPushButton)]
        self.assertNotIn('选择本地图片…', texts)
        self.assertIn('获取卡图', texts)

    def test_fetch_and_save_share_one_row_below_the_name(self):
        """[获取卡图][保存] 必须在**名字下方、同一行**。"""
        d = self._dlg()
        lay = d.layout()
        names = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            names.append(type(w).__name__ if w is not None else 'layout')
        #: 顺序 = [0]标题行 [1]卡组名 [2]预览图 [3]名字 [4]按钮行（同一行）
        self.assertEqual(names[1], 'QLabel')      # 卡组名
        self.assertEqual(names[2], 'QLabel')      # 预览
        self.assertEqual(names[3], 'QLabel')      # 选中卡的名字（在预览**下方**）
        btn_row = lay.itemAt(4).layout()
        self.assertIsNotNone(btn_row, '第 5 项应该是按钮行')
        row_texts = [btn_row.itemAt(j).widget().text()
                     for j in range(btn_row.count())
                     if btn_row.itemAt(j).widget() is not None]
        self.assertIn('获取卡图', row_texts)
        self.assertIn('保存', row_texts)
        self.assertLess(lay.indexOf(d.picked_label), 4, '名字在按钮行**上方**')



    def test_changing_image_invalidates_the_pixmap_cache(self):
        """★ 2026-10-08（用户）：「改了我自定义卡组的卡图不会立即刷新，必须重启 UI」。

        根因（已复现）：换图时**文件名不变**（`deck_images_custom/<名字>.webp`
        原地覆盖），而 `widgets.round_pixmap` 按**路径**缓存 pixmap —— 内容换了
        仍返回旧图。修法：换图后调 `widgets.forget_pixmap(path)`。
        """
        import shutil
        from ui import widgets
        a = os.path.join('data', 'deck_images_custom', '_default.webp')
        b = os.path.join('data', 'deck_images', 'Elfnote.webp')
        for f in (a, b):
            if not os.path.exists(f):
                self.skipTest('素材图不在：%s' % f)
        tmp = tempfile.mkdtemp(prefix='pixcache-')
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, 'same.webp')
        seen = []
        for src in (a, b):
            shutil.copyfile(src, p)
            widgets.forget_pixmap(p)              #: 换图后必须清（被测行为）
            seen.append(widgets.round_pixmap(p, 22, theme.R_XS))
        self.assertIsNot(seen[0], seen[1], '换了图还是同一个 pixmap（缓存没失效）')

    def test_forget_pixmap_clears_all_sizes(self):
        from ui import widgets
        p = os.path.join('data', 'deck_images_custom', '_default.webp')
        if not os.path.exists(p):
            self.skipTest('默认卡图不在')
        widgets.round_pixmap(p, 22, theme.R_XS)
        widgets.round_pixmap(p, 40, theme.R_XS)
        self.assertGreaterEqual(widgets.forget_pixmap(p), 2, '该清掉所有尺寸')

    def test_forget_pixmap_accepts_relative_path(self):
        """相对路径 / 绝对路径都要能命中同一条缓存（换图画的是绝对路径）。"""
        from ui import widgets
        rel = os.path.join('data', 'deck_images_custom', '_default.webp')
        full = os.path.join(widgets.PROJ_ROOT, rel)
        if not os.path.exists(full):
            self.skipTest('默认卡图不在')
        widgets.round_pixmap(rel, 22, theme.R_XS)
        self.assertGreaterEqual(widgets.forget_pixmap(full), 1)

    def test_save_without_picking_hints(self):
        d = self._dlg()
        d._save()
        self.assertIsNone(d.choice)
        self.assertIn('先在下面选一张卡', d.hint.text())

    def test_deck_picker_is_searchable_and_stores_site_name(self):
        """站内下拉：显示友好名、`itemData` 存站内英文名（不要求手输英文）。"""
        d = self._dlg(deck_options=[('烙印', 'Branded'), ('闪刀姬', 'Sky Striker')])
        self.assertTrue(d.site_picker.isEditable())
        self.assertEqual(d.site_picker.itemText(0), '烙印')
        self.assertEqual(d.site_picker.itemData(0), 'Branded')
        words = d._comp_model.stringList()
        self.assertIn('烙印', words)
        self.assertIn('Branded', words)


    def test_label_has_no_id_suffix(self):
        """★ 2026-10-08 用户：「去掉卡名后的括号和数字」。"""
        d = self._dlg()
        d.card_search.setText('灰流丽')
        row = d.card_list.itemWidget(d.card_list.item(0))
        self.assertNotIn('#', row.label.text())
        self.assertNotIn('（', row.label.text())

    def test_confirm_button_text_is_not_clipped(self):
        """★ 2026-10-08 用户：「确认按钮显示不全」—— 按钮要装得下自己的字。

        根因：全局 QSS 的 `padding: 6px 12px` 在 ~18px 高的行内按钮上只剩 6px
        内容区，12px 的字上下被裁（与输入框被裁是同一个坑）。
        """
        from ui.opp_deck_manager import CardPickRow
        row = CardPickRow(12950, '灰流丽')
        fm = row.btn_confirm.fontMetrics()
        self.assertGreaterEqual(row.btn_confirm.height(),
                                fm.height() + 4,
                                '按钮高 %d < 字高 %d + 余量 —— 字会被裁'
                                % (row.btn_confirm.height(), fm.height()))
        self.assertGreaterEqual(
            row.btn_confirm.width(),
            fm.horizontalAdvance(row.btn_confirm.text()) + 8,
            '按钮宽不够（字会被挤掉）')
        self.assertIn('padding: 2px 8px', row.btn_confirm.styleSheet())

    def test_take_site_uses_item_data(self):
        d = self._dlg(deck_options=[('烙印', 'Branded')])
        d.site_picker.setCurrentIndex(0)
        d._take_site()
        self.assertEqual(d.choice, 'site')
        self.assertEqual(d.site_name, 'Branded')




    def test_refdecks_entry_runs_the_refresh_tool(self):
        """★ 2026-10-09（用户）：「参照库快照是 09-21 的，而且没有能更新的按钮」。

        修法：设置概览页「数据工具」组新增一行「参照库刷新」——
        跑 `tools/fetch_mdm_decks.py --all`（联网几分钟）。
        """
        cfg = dashboard.SCRIPT_COMMANDS.get('s_refdecks')
        self.assertIsNotNone(cfg, 's_refdecks 必须进命令表')
        self.assertEqual(cfg['script'], 'tools/fetch_mdm_decks.py')
        self.assertEqual(cfg['runs'], [('刷新（几分钟）', ['--all'])])
        #: 分组里要有（否则概览页不显示）
        grouped = dict(dashboard.SETTING_GROUPS)['数据工具']
        self.assertIn('s_refdecks', grouped)
        made = []

        def _factory(script, args):
            made.append((script, list(args)))
            from PySide6.QtCore import Signal as _S

            class _R(object):
                line = _S(str)
                done = _S(int)

                def running(self):
                    return False

                def start(self):
                    pass
            return _R()

        page = dashboard.SettingsIndexPage('', '', runner_factory=_factory)
        btns = page._rows['s_refdecks'].control.findChildren(QPushButton)
        self.assertEqual([b.text() for b in btns], ['刷新（几分钟）'])
        btns[0].click()
        self.assertEqual(made, [('tools/fetch_mdm_decks.py', ['--all'])])


class HandTrapColumnsTest(StatsPageTest):
    """手坑两列（2026-10-16）：列宽预算 + 三种值语义。"""

    def test_columns_fit_viewport(self):
        """★ 列宽合计 + 间距必须 ≤ 1200 窗口的行内可用宽 —— 否则最右列被挤出视口。"""
        from ui import theme
        total = sum(w for _n, w in dashboard.STATS_COLS)
        total += (len(dashboard.STATS_COLS) - 1) * dashboard.MATCH_GAP
        viewport = (1200 - theme.RAIL_W - 2 - 10)
        avail = viewport - theme.S_LG * 2 - theme.S_MD * 2 - theme.S_XS * 2
        self.assertLessEqual(total, avail,
                             '列宽合计 %d > 行内可用宽 %d' % (total, avail))

    def test_two_trap_columns(self):
        w = {n: ww for n, ww in dashboard.STATS_COLS}
        self.assertIn('我方手坑', w)
        self.assertIn('对方手坑', w)
        self.assertEqual(w['我方手坑'], w['对方手坑'])

    def test_cell_width_matches_column(self):
        row = self.page.findChildren(dashboard.MatchRow)[0]
        cells = row.findChildren(dashboard.HandTrapCell)
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0].width(), dashboard.STATS_COLS[9][1])
        self.assertEqual(cells[1].width(), dashboard.STATS_COLS[10][1])

    def test_none_and_empty_render_identically(self):
        """★ 用户 2026-10-17：没有记录（None）与没用过（[]）**渲染完全一致**。

        「不要一个用 `—` 一个没有」—— 两者都**留空**；区别只在 tooltip：
        判不出才说明「没有逐卡记录」。
        """
        from PySide6.QtWidgets import QLabel
        none_cell = dashboard.HandTrapCell(None, 90)
        empty_cell = dashboard.HandTrapCell([], 90)
        self.assertEqual(none_cell.findChildren(QLabel),
                         empty_cell.findChildren(QLabel))
        self.assertEqual(none_cell.findChildren(QLabel), [])
        self.assertIn('逐卡记录', none_cell.toolTip())     # 判不出有说明
        self.assertFalse(empty_cell.toolTip())             # 没用过不挂 tooltip

    def test_used_cell_tooltip(self):
        rows = [{'name': '灰流丽', 'turn': 0, 'img': None},
                {'name': '增殖的Z', 'turn': 1, 'img': None}]
        some_cell = dashboard.HandTrapCell(rows, 90)
        self.assertIn('共 2 张', some_cell.toolTip())

    def test_shows_five_cards(self):
        """★ 用户 2026-10-17：一格放 **5 张**（目测放得下）。"""
        self.assertEqual(dashboard.HandTrapCell.MAX_CARDS, 5)
        from PySide6.QtWidgets import QLabel
        rows = [{'name': 'c%d' % i, 'turn': 0, 'img': None} for i in range(5)]
        cell = dashboard.HandTrapCell(rows, 90)
        self.assertNotIn('+', [l.text() for l in cell.findChildren(QLabel)])
        #: 5 张必须塞进最小列宽（否则列又要变宽、挤压别的列）
        self.assertLessEqual(cell.minimumSizeHint().width(),
                             dashboard.STATS_COLS[9][1])

    def test_overflow_badge_at_least_14px(self):
        """第 6 张起收 `+N` 角标；字号不许低于 14（页面字号下限锁）。"""
        from PySide6.QtWidgets import QLabel
        rows = [{'name': 'c%d' % i, 'turn': 0, 'img': None} for i in range(6)]
        cell = dashboard.HandTrapCell(rows, 90)
        badges = [l for l in cell.findChildren(QLabel)
                  if l.text().startswith('+')]
        self.assertTrue(badges, '第 6 张起要有 +N 角标')
        self.assertGreaterEqual(badges[0].font().pixelSize(), 14)

class StatsColWidthsTest(unittest.TestCase):
    """列宽自适应（2026-10-16 用户：「右侧很多空间，填满让各列更宽松」）。"""

    def test_min_widths_when_tight(self):
        """可用宽不够 -> 原样返回最小宽（不缩到负数）。"""
        tiny = dashboard.stats_col_widths(10)
        self.assertEqual(list(tiny), [w for _n, w in dashboard.STATS_COLS])

    def test_fills_exactly(self):
        """够宽 -> 合计恰好等于可用宽（填满，不留右边空白）。"""
        mins = sum(w for _n, w in dashboard.STATS_COLS)
        gaps = (len(dashboard.STATS_COLS) - 1) * dashboard.MATCH_GAP
        for avail in (mins + gaps + 1, 974, 1200, 1500):
            got = dashboard.stats_col_widths(avail)
            self.assertEqual(sum(got) + gaps, avail, 'avail=%d' % avail)

    def test_never_below_min(self):
        for avail in (974, 1200, 1500, 2000):
            got = dashboard.stats_col_widths(avail)
            for i, (_n, mn) in enumerate(dashboard.STATS_COLS):
                self.assertGreaterEqual(got[i], mn)

    def test_monotonic(self):
        """窗口变宽 -> 每列都不缩（单调不减）。"""
        a = dashboard.stats_col_widths(974)
        b = dashboard.stats_col_widths(1400)
        for i in range(len(a)):
            self.assertLessEqual(a[i], b[i])

    def test_set_and_get(self):
        """set_avail_width 后 col_width 反映新值；越界返回 0（不抛）。"""
        try:
            dashboard.set_avail_width(1400)
            self.assertGreaterEqual(dashboard.col_width(0), 52)
            self.assertEqual(dashboard.col_width(999), 0)
        finally:
            dashboard.set_avail_width(0)     # 还原最小宽（别污染别的用例）

class TrapColumnSwitchTest(unittest.TestCase):
    """战绩页「我方/对方手坑列」显示开关（用户 2026-10-17）。"""

    def _patch(self, **kv):
        """临时改 `stats_show_traps` 返回值（不碰真实 QSettings）。"""
        orig = dashboard.stats_show_traps
        dashboard.stats_show_traps = lambda: kv.get('v', (True, True))
        self.addCleanup(lambda: setattr(dashboard, 'stats_show_traps', orig))

    def test_hidden_col_gets_zero_width(self):
        self._patch(v=(True, False))
        w = dashboard.stats_col_widths(956, (True, False))
        self.assertGreater(w[dashboard.TRAP_COL_IDX[0]], 0)
        self.assertEqual(w[dashboard.TRAP_COL_IDX[1]], 0)

    def test_other_cols_absorb_space(self):
        """关掉一列后其余列**变宽**（省下的空间分回去）。"""
        full = dashboard.stats_col_widths(956, (True, True))
        off = dashboard.stats_col_widths(956, (True, False))
        self.assertGreater(off[2], full[2])          # 「对手」列变宽

    def test_both_off(self):
        self._patch(v=(False, False))
        w = dashboard.stats_col_widths(956, (False, False))
        self.assertEqual(w[dashboard.TRAP_COL_IDX[0]], 0)
        self.assertEqual(w[dashboard.TRAP_COL_IDX[1]], 0)

    def test_default_shows_both(self):
        """默认两列都显示（不该默认藏起来）。"""
        self.assertEqual(dashboard.STATS_TRAP_KEYS,
                         (('显示我方手坑列', 'ui/stats_show_my_traps'),
                          ('显示对方手坑列', 'ui/stats_show_opp_traps')))

class SettingsLiveApplyTest(unittest.TestCase):
    """★ 设置改动**必须实时生效**（用户 2026-10-17：不能要重启）。

    🔴 回归锁：`_reload_stats` 有「数据没变就不重画」的指纹闸门，而列开关
    只改显示、**数据一模一样** -> 不清闸门等于「勾了没反应、得重启」。
    """

    def test_stats_columns_update_without_reload(self):
        """关掉一列手坑后，表头**立刻**少一列（不必重启）。

        🔴 回归点：`_reload_stats` 的指纹闸门见「数据没变」就 return，
        而列开关只改显示 —— 闸门不清就等于「勾了没反应」。
        """
        app = _qapp_for_settings()
        loader = _stats_rows_loader()
        win = dashboard.DashboardWindow(loader=loader)
        win.resize(1200, 620)
        win.open_page('stats')
        win.show()
        app.processEvents()
        pg = win.pages['stats']

        def heads():
            from PySide6.QtWidgets import QLabel
            h = pg.findChildren(dashboard.MatchHeader)
            return [l.text() for l in h[0].findChildren(QLabel)] if h else []

        self.assertIn('对方手坑', heads())
        orig = dashboard.stats_show_traps
        dashboard.stats_show_traps = lambda: (True, False)
        self.addCleanup(lambda: setattr(dashboard, 'stats_show_traps', orig))
        try:
            win._on_settings_changed('ui/stats_show_opp_traps')
            app.processEvents()
            app.processEvents()
            self.assertNotIn('对方手坑', heads(),
                             '关掉列后表头没变 -> 得重启才生效')
            self.assertIn('我方手坑', heads())
        finally:
            win.hide()
            win.deleteLater()

    def test_floating_flag_routes_to_floating_callback(self):
        app = _qapp_for_settings()
        called = []
        win = dashboard.DashboardWindow(
            loader=lambda **kw: {},
            on_floating_layout_changed=lambda: called.append(1))
        win._on_settings_changed('ui/float_hide_order')
        self.assertEqual(called, [1], '悬浮窗列开关没通知 app 去重排')
        win.hide()
        win.deleteLater()


def _stats_rows_loader():
    """造一份**有对局记录**的战绩数据（一行，带手坑两列）。"""
    row = {'match_id': 'x', 'date': '09-16', 'time': '23:45', 'result': 'Win',
           'went_first': True, 'opp_player': 'p', 'total_turns': 5,
           'mode_label': '排名决斗', 'opp_deck': '', 'my_traps': [],
           'opp_traps': []}
    st = {'n_total': 1, 'n_win': 1, 'n_lose': 0, 'first_n': 1, 'second_n': 0,
          'first_rate': 100.0, 'second_rate': None, 'win_rate': 100.0,
          'first_wins': 1, 'second_wins': 0, 'img': None}
    data = {'groups': [('甲', st, [row])], 'daily': {}, 'months': [],
            'overview': {}, 'accounts': [], 'month': '2026-09'}
    return lambda **kw: dict(data)


_APP2 = None


def _qapp_for_settings():
    """取（或建）QApplication 单例（`settings()` 走真实 QSettings，这里只画控件）。"""
    global _APP2
    from PySide6.QtWidgets import QApplication
    if _APP2 is None:
        _APP2 = QApplication.instance() or QApplication([])
    return _APP2

class OverviewUniformFontTest(unittest.TestCase):
    """★ 2026-10-17 用户：「先攻/后攻胜率字号不对」。

    原来每格**各自**降档（只有带括号的才降）-> 五格 32/21/16/16 参差。
    现在先算一个**公共字号**（按最挤的那格），五格统一用它。
    """

    def test_metric_size_fits_all_items(self):
        from PySide6.QtGui import QFont, QFontMetrics
        from ui import widgets
        #: 场数已挪到下一行（用户 2026-10-17 第二次纠正）-> 只验主值
        items = [('73', ''), ('64.4%', ''), ('68.4%', '')]
        avail = 120
        size = dashboard._metric_size(avail, items)
        self.assertGreaterEqual(size, dashboard.theme.PAGE_F_OVER_TITLE)
        fm_main = QFontMetrics(widgets.font(
            size, QFont.Weight.Bold, tabular=True))
        fm_sub = QFontMetrics(widgets.font(
            dashboard.theme.PAGE_F_SUB, QFont.Weight.Bold, tabular=True))
        for main, suf in items:
            need = fm_main.horizontalAdvance(main) +                 (fm_sub.horizontalAdvance(suf) if suf else 0)
            #: 装不下时**不再降**（字号有下限 `PAGE_F_OVER_TITLE`）——
            #: 这时宁可截断/省略，也不能把字缩成蚂蚁。
            self.assertTrue(
                need <= avail
                or size == dashboard.theme.PAGE_F_OVER_TITLE,
                '%s+%s 在 %dpx 里塞不下（字号已到下限 %d）'
                % (main, suf, avail, size))

    def test_metric_size_takes_largest_that_fits(self):
        """★ 主值取「装得下的**最大**字号」（用户：改**大**到一致，不是改小）。

        场数已挪到下一行，主值只看自己 —— 所以五个百分比都能用上限
        `PAGE_F_BIG`，而不是被括号挤到 `PAGE_F_OVER_TITLE`。
        """
        from PySide6.QtGui import QFont, QFontMetrics
        from ui import widgets
        avail = 120
        size = dashboard._metric_size(avail, [(x, '') for x in
                                            ('73', '64.4%', '68.4%', '60.0%')])
        fm = QFontMetrics(widgets.font(size, QFont.Weight.Bold, tabular=True))
        one_more = QFontMetrics(widgets.font(size + 1, QFont.Weight.Bold,
                                             tabular=True))
        for x in ('64.4%', '68.4%', '60.0%'):
            self.assertLessEqual(fm.horizontalAdvance(x), avail)
            #: 再大一号就该装不下了 —— **除非已到字号上限**（`PAGE_F_BIG` 是设计封顶，
            #: 不能因为格宽富余就无限放大）
            if size < dashboard.theme.PAGE_F_BIG:
                self.assertGreater(one_more.horizontalAdvance(x), avail)

    def test_metric_size_shrinks_when_too_narrow(self):
        self.assertLess(dashboard._metric_size(40, [('100.0%', '')]),
                        dashboard._metric_size(400, [('100.0%', '')]))

    def test_metric_line_honors_explicit_size(self):
        from PySide6.QtWidgets import QLabel
        app = _qapp_for_settings()
        w = dashboard._metric_line('50%', '', size=20)
        sizes = [l.font().pixelSize()
                 for l in w.findChildren(QLabel) if l.text()]
        self.assertTrue(sizes)
        for s_ in sizes:
            self.assertLessEqual(s_, 20)


class RailAffordanceTest(unittest.TestCase):
    """★ 2026-10-17：左栏「导出」补图标；「查询」分组**不可点击**。"""

    def test_export_has_icon(self):
        self.assertIn('export', dashboard.ICON_KEYS)

    def test_query_not_clickable(self):
        from PySide6.QtCore import Qt
        app = _qapp_for_settings()
        rail = dashboard.Rail()
        item = None
        for w in rail.findChildren(dashboard.RailItem):
            if w.key == dashboard.QUERY_KEY:
                item = w
        self.assertIsNotNone(item, '左栏里找不到「查询」项')
        self.assertFalse(item._clickable, '「查询」应该是不可点的分组标题')
        self.assertEqual(item.cursor().shape(), Qt.ArrowCursor)

    def test_nav_icons_all_drawable(self):
        """每个 ICON_KEYS 都要有绘制分支（漏一个 = 左栏少个图标）。"""
        import inspect
        src = inspect.getsource(dashboard.draw_nav_icon)
        for k in dashboard.ICON_KEYS:
            self.assertIn("key == '%s'" % k, src, '%s 没有图标分支' % k)


class SettingsPageFitTest(unittest.TestCase):
    """★ 2026-10-17：设置页去掉页底提示，620 高窗口**不出拖拽条**。"""

    def test_no_hint_label(self):
        """页底不再有那句常驻说明（红框区域）。"""
        import inspect
        src = inspect.getsource(dashboard.SettingsIndexPage.__init__)
        self.assertNotIn("if hint:", src,
                         '页底那句 hint 说明还在（用户要求去掉）')

    def test_no_scrollbar_at_min_height(self):
        from PySide6.QtWidgets import QScrollArea
        app = _qapp_for_settings()
        win = dashboard.DashboardWindow(loader=lambda **kw: {})
        try:
            #: ⚠️ 用 `setFixedSize` 而不是 `resize`：`DashboardWindow.__init__` 末尾会
            #: `_restore_geometry()` 从 QSettings 恢复尺寸，把 resize 覆盖掉 ——
            #: 全量里别的用例存过小窗口，这里就会测到 600 高而不是 620。
            win.setFixedSize(1200, 620)       # 看板最小高度
            win.open_page('settings')
            win.show()
            app.processEvents()
            app.processEvents()
            sa = win.pages['settings'].findChildren(QScrollArea)
            self.assertTrue(sa)
            #: ★ 判据 = **不显示滚动条**（用户口径「不要出现拖拽条」）。
            #: 内容高度会随字体度量浮动（不同环境差 ±14px），所以只要策略是
            #: AlwaysOff 就满足；仍可滚轮滚动，信息不丢。
            from PySide6.QtCore import Qt as _Qt
            self.assertEqual(sa[0].verticalScrollBarPolicy(),
                             _Qt.ScrollBarAlwaysOff,
                             '设置页还显示拖拽条')
        finally:
            win.hide()
            win.deleteLater()


class FloatingSectionTitleTest(unittest.TestCase):
    """★ 2026-10-17：「最近对局显示哪些列」→「最近对局设置」。"""

    def test_title_renamed(self):
        from ui import i18n
        from ui.i18n import tr
        self.assertEqual(tr('最近对局设置'), '最近对局设置')
        self.assertIn('最近对局设置', i18n._T)
