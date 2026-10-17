# -*- coding: utf-8 -*-
"""demo 战绩（:mod:`core.ui_demo`）单测 —— 它是**做 UI 的数据契约**，烂了所有 UI
迭代都会被带偏，所以锁死：

1. 行 dict 字段与 :func:`core.ui_state._recent_row` 完全一致（键一个不少）；
2. 覆盖面：升/掉/跨段、巅峰、未标注、缺图、先后未知、长名字 —— 都得在；
3. 统计数字 = 设计稿那套（拿 `initiative_rate` 当先手率的界面会露馅）；
4. 界面吃得下（有 Qt 时整份快照过一遍 ``FloatingWindow``，不许抛）。
"""
import json
import io
import os
import re
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')     # 必须在 Qt import 之前

from core import ui_actions, ui_demo, ui_state                        # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QLabel
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    import tempfile as _tempfile
    _I18N_DIR = _tempfile.mkdtemp(prefix='m7-demoui18n-')
    from PySide6.QtCore import QSettings as _QS
    import ui.i18n as _i18n
    _i18n._SETTINGS_FACTORY = staticmethod(
        lambda: _QS(os.path.join(_I18N_DIR, 'ui.ini'), _QS.IniFormat))

    from ui import theme
    from ui import floating
    from ui.floating import FloatingWindow, MatchRow

_APP = None

#: 行 dict 必含的键（= ``_recent_row`` 的输出面，UI 契约）
_REQUIRED = {
    'match_id', 'started_at', 'day', 'time', 'is_today', 'result',
    'went_first', 'my_deck', 'my_deck_img', 'opp_deck', 'opp_deck_img',
    'opp_known', 'opp_player', 'badge_icon', 'badge_text', 'rank_before',
    'rank_after', 'rank_delta', 'rank_label_before', 'rank_label_after',
    'rank_icon_after', 'badge_numeral', 'badge_numeral_after',
    # M5 反推的输入（选择器面板的「建议」靠它）
    'opp_revealed_ids', 'opp_revealed',
}


class DemoDataTest(unittest.TestCase):

    def setUp(self):
        self.rows = ui_demo.demo_recent()

    def test_rows_have_every_key_the_ui_reads(self):
        """★ 行 dict 键一个不少 —— 漏一个（如 badge_numeral）界面就画缺。"""
        for r in self.rows:
            self.assertTrue(_REQUIRED <= set(r), _REQUIRED - set(r))

    def test_nine_documented_states_exist(self):
        """★ 覆盖面：模块头表格承诺的形态都得真在。"""
        self.assertEqual(len(self.rows), 9)
        deltas = {r['rank_delta'] for r in self.rows}
        self.assertIn('up', deltas)
        self.assertIn('down', deltas)
        self.assertIn('same', deltas)
        self.assertIn(None, deltas)
        # 跨段：after 图标与 before 不同
        self.assertTrue(any(r['rank_icon_after'] and r['rank_icon_after'] != r['badge_icon']
                            for r in self.rows))
        # ★ 升/掉段行**必须带 after 图标**（2026-09-15 起画的是当前段位图标；
        #   demo0001 漏过一次 -> 渲染图升段行只剩 `II ↑` 没图）
        for r in self.rows:
            if r['rank_delta'] in ('up', 'down'):
                self.assertTrue(r['rank_icon_after'],
                                '%s 升/掉段缺 rank_icon_after' % r['match_id'])
        # 巅峰局 / 未标注 / 缺图 / 先后未知 / 平局 / 长名字
        self.assertTrue(any(r['badge_icon'] == ui_demo.PEAK_ICON for r in self.rows))
        self.assertTrue(any(not r['opp_known'] for r in self.rows))
        self.assertTrue(any(r['opp_known'] and not r['opp_deck_img'] for r in self.rows))
        self.assertTrue(any(r['went_first'] is None for r in self.rows))
        self.assertTrue(any(r['result'] == 'Draw' for r in self.rows))
        self.assertTrue(any(len(r['opp_player'] or '') > 10 for r in self.rows))
        # 没段位数据的行也得有（真库最近三局的形态）
        self.assertTrue(any(r['badge_icon'] is None for r in self.rows))

    def test_dates_are_relative_so_demo_never_rots(self):
        """日期用 today() 现算 —— 写死的话过两天 is_today 全变 False。"""
        import datetime
        today = datetime.date.today().isoformat()
        self.assertTrue(all(r['is_today'] == (r['started_at'][:10] == today)
                            for r in self.rows))
        self.assertTrue(any(r['is_today'] for r in self.rows))
        self.assertTrue(any(not r['is_today'] for r in self.rows))

    def test_only_real_image_files_referenced(self):
        """引用的图必须真实存在（custom 目录进版本库，不会跑）——
        引用不存在的图，渲染出来就是一块块空白还不好查。"""
        for r in self.rows:
            for p in (r['my_deck_img'], r['opp_deck_img'], r['badge_icon'],
                      r['rank_icon_after']):
                if p:
                    self.assertTrue(os.path.exists(os.path.join(PROJ, p)), p)

    def test_stats_match_the_design_sheet(self):
        """统计 = 设计稿那套（65.6% / 77.8% / 41.7% / 60% / 21-11 / 18-12）。"""
        st = ui_demo.DEMO_STATS
        self.assertEqual(theme_pct(st['win_rate']), '65.6%')
        self.assertEqual(theme_pct(st['first_rate']), '77.8%')
        self.assertEqual(theme_pct(st['second_rate']), '41.7%')
        self.assertEqual(theme_pct(st['first_hand_pct']), '60.0%')
        self.assertEqual((st['n_win'], st['n_lose']), (21, 11))
        self.assertEqual((st['first_n'], st['second_n']), (18, 12))
        # ★ 故意带着的「错误答案」：先手率若拿 initiative_rate 会显示 53.6%
        self.assertEqual(theme_pct(st['initiative_rate']), '53.6%')
        self.assertNotEqual(st['initiative_rate'], st['first_hand_pct'])

    def test_snapshot_builds_without_raising(self):
        snap = ui_demo.demo_snapshot()
        self.assertIsInstance(snap, ui_state.UiSnapshot)
        self.assertFalse(snap.is_pending)
        self.assertEqual(len(snap.recent), 9)
        self.assertEqual(snap.unmarked_count, 18)
        self.assertEqual(snap.my_rank['label'], 'Platinum 2')

    def test_demo_module_is_gui_free(self):
        """★ 纪律同 core/ui_state：不许 import GUI（无头单测要能直接用）。"""
        import io
        with io.open(ui_demo.__file__, encoding='utf-8') as f:
            src = f.read()
        imports = [ln for ln in src.split('\n')
                   if ln.strip().startswith(('import ', 'from '))]
        self.assertFalse(any('PySide6' in ln or 'QtWidgets' in ln
                             for ln in imports), imports)


def theme_pct(rate):
    """跟 theme.fmt_pct 同口径，但本模块不许 import theme（它在这组用例外）。"""
    return '%.1f%%' % (rate * 100)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DemoRendersTest(unittest.TestCase):
    """整份 demo 过一遍悬浮窗 —— 界面侧的「吃得下」检验。"""

    def setUp(self):
        global _APP
        if _APP is None:
            _APP = QApplication.instance() or QApplication([])
            _APP.setStyleSheet(theme.qss())

    def test_every_row_constructs(self):
        """9 行逐行建 MatchRow（悬浮窗只显示 3 行，剩下 6 行也得能建）。"""
        for row in ui_demo.demo_recent():
            w = MatchRow(dict(row))
            w.deleteLater()

    def test_window_renders_demo_snapshot(self):
        win = FloatingWindow()
        win.set_snapshot(ui_demo.demo_snapshot())
        rows = win.row_widgets()
        # 2026-09-15 起 MAX_ROWS = 10（行区可滚动），demo 的 9 行全画出来
        self.assertEqual(len(rows), 9)
        # 第 3 行是未标注 + **M5 有建议** —— 2026-09-16 用户定：那一格直接显示
        # 建议的卡组（图用建议卡组的图、白字名字），点一下即标注；
        # 它仍算未标注 → 琥珀 `!` 角标保留。
        self.assertEqual(rows[2].opp_deck_label.text(), '小丑戏帮')
        self.assertTrue(rows[2].opp_cell.suggested)
        self.assertIn('建议', rows[2].opp_cell.toolTip())
        self.assertTrue(rows[2].opp_thumb.is_warning_badged())
        # 第 1 行升段 —— 方向箭头 ↑ 在（2026-09-15 起升段 = 当前段位 + ↑）
        self.assertTrue(any(lab.text() == floating.ARROW_UP
                            for lab in rows[0].badge.findChildren(QLabel)))
        # 没有「待补」计数（09-15 已删，防回潮）
        texts = [w.text() for w in win.findChildren(QLabel)]
        self.assertFalse(any('待补' in t for t in texts))
        win.deleteLater()




@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DemoSandboxTest(unittest.TestCase):
    """demo 沙盒：DemoSandboxStorage 三写口拦截 + demo_recent 套标注。

    规矩（2026-09-15）：动过 _MARKS / _SANDBOX_OPP 全局态必须
    ``demo_reset_marks()`` 收尾，否则泄漏到别的测试。
    """

    def setUp(self):
        self.addCleanup(ui_demo.demo_reset_marks)      # 全局态防泄漏

    def _real(self):
        import tempfile
        from core.storage import Storage
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = Storage(os.path.join(tmp.name, 'sandbox.db'))
        st.init_schema()                       # 空库先建表（项目惯例）
        return st

    def test_upsert_negative_id_reuse(self):
        """upsert -> 负数 id，同名复用（不重复登记）。"""
        s = ui_demo.DemoSandboxStorage(self._real())
        a = s.upsert_opp_deck('Maliss', aliases=['Maliss'])
        b = s.upsert_opp_deck('Maliss', aliases=['码丽丝'])
        self.assertLess(a, 0)                          # 负数 id
        self.assertEqual(b, a)                         # 同名复用
        c = s.upsert_opp_deck('Branded')
        self.assertEqual(c, a - 1)                     # 新名递减

    def test_mark_and_demo_recent_applies(self):
        """标注进内存 -> demo_recent 套回行上（opp_known / opp_deck）。"""
        s = ui_demo.DemoSandboxStorage(self._real())
        oid = s.upsert_opp_deck('Maliss')
        s.mark_opp_deck('demo0003', opp_deck_id=oid, name='码丽丝')
        rows = {r['match_id']: r for r in ui_demo.demo_recent()}
        self.assertTrue(rows['demo0003']['opp_known'])
        self.assertEqual(rows['demo0003']['opp_deck'], '码丽丝')
        # 没标过的行不动
        self.assertEqual(rows['demo0001']['opp_deck'], '烙印')

    def test_mark_updates_the_image_too(self):
        """★ 用户 2026-09-15 实测：「换卡组只换名字、图片不换」。

        根因：沙盒只记了名字，`opp_deck_img` 还是旧值（未标注行的默认图）。
        现在 `mark_opp_deck` 会把**解析好的图片路径**一起记下来。

        ⚠️ 这里用的是**空库**（沙盒测试的惯例），所以解析结果就是默认图 ——
        断言只锁「**图片按新名字重新解析过**」（= 与 `DeckImages.resolve()` 一致），
        不锁具体是哪张图（真库下才是 Branded.webp）。
        """
        from core.deck_images import DeckImages
        real = self._real()
        s = ui_demo.DemoSandboxStorage(real)
        oid = s.upsert_opp_deck('Branded')
        s.mark_opp_deck('demo0003', opp_deck_id=oid, name='Branded')
        row = next(r for r in ui_demo.demo_recent()
                   if r['match_id'] == 'demo0003')
        self.assertTrue(row.get('opp_deck_img'), '标完之后图片路径不能是空的')
        self.assertEqual(row['opp_deck_img'], DeckImages(real).resolve('Branded'))

    def test_reset_marks_restores_pristine(self):
        """reset 后 demo_recent 回到 pristine 基底。"""
        s = ui_demo.DemoSandboxStorage(self._real())
        s.mark_opp_deck('demo0001', opp_deck_id=-1000, name='码丽丝')
        ui_demo.demo_reset_marks()
        rows = {r['match_id']: r for r in ui_demo.demo_recent()}
        self.assertEqual(rows['demo0001']['opp_deck'], '烙印')
        self.assertEqual(ui_demo.demo_sandbox_opp_decks(), [])

    def test_list_opp_decks_merges_sandbox(self):
        """list_opp_decks = 真库表 + 沙盒负数条目（排最后）。"""
        real = self._real()
        n_real = len(real.list_opp_decks())
        s = ui_demo.DemoSandboxStorage(real)
        oid = s.upsert_opp_deck('Maliss')
        self.assertLess(oid, 0)
        merged = s.list_opp_decks()
        self.assertEqual(len(merged), n_real + 1)
        self.assertEqual(merged[-1][0], oid)           # 沙盒条目在末尾
        # 真库自己没被写
        self.assertEqual(len(real.list_opp_decks()), n_real)

    def test_passthrough_reads_real(self):
        """非拦截方法全部透传真库（只读参考数据）。"""
        real = self._real()
        s = ui_demo.DemoSandboxStorage(real)
        # 透传读参考数据：调用结果与真库一致（bound method 每次 getattr
        # 都是新对象，不能 assertIs —— 2026-09-15 踩过）
        self.assertEqual(s.list_meta_decks(), real.list_meta_decks())
        self.assertEqual(s.search_meta_decks('Maliss'),
                         real.search_meta_decks('Maliss'))
        self.assertTrue(callable(s.upsert_opp_deck))


class DemoGuessSampleTest(unittest.TestCase):
    """★ demo 必须有一行**带露出卡**，否则 M5 候选功能没法验收。

    实测真库的待标注队列里带 `opp_revealed_ids` 的局 = **0**（曾有过 2/18，已被标掉），
    所以「选择器里的建议」这条只有 demo 能验 —— 这也是项目口径「做 UI 一律吃 demo」。
    """

    def test_some_row_has_revealed_cards(self):
        rows = ui_demo.demo_recent()
        self.assertTrue(any(r.get('opp_revealed_ids') for r in rows),
                        'demo 里没有带露出卡的样本')

    def test_that_row_yields_suggestions(self):
        """样本要能真的算出候选（卡组必须是参照库里有的）。"""
        from core import deck_guess as dg
        index, groups = dg.load_index()
        if index is None:
            self.skipTest('没有参照库 data/mdm_deck_cards.json')
        row = next(r for r in ui_demo.demo_recent() if r.get('opp_revealed_ids'))
        cands = dg.suggest(row, index, groups)
        self.assertTrue(cands, 'demo 样本算不出候选 —— 换成参照库里真有的卡组')


class DefaultDeckImageTest(unittest.TestCase):
    """★ 缺图兜底 = **悠悠**（Mokey Mokey）卡绘（用户 2026-09-15 定，D7）。

    锁三件事：① 图在且是 200×200；② 取图脚本在（**可复现**，不是手工塞的图）；
    ③ 脚本要用的 Konami 密码确实能从我们自己的卡表查出来（`cid` ↔ `id`）。
    """

    def test_default_image_exists_and_is_200(self):
        from PySide6.QtGui import QImage
        path = os.path.join(PROJ, 'data', 'deck_images_custom', '_default.webp')
        self.assertTrue(os.path.isfile(path), '缺默认卡图：%s' % path)
        img = QImage(path)
        self.assertFalse(img.isNull(), '默认卡图读不出来（webp 支持？）')
        self.assertEqual((img.width(), img.height()), (200, 200))

    def test_fetch_script_exists(self):
        """取图脚本必须在 —— 换机器/重建时要能一键重现，不是随手塞的文件。"""
        path = os.path.join(PROJ, 'tools', 'fetch_deck_default.py')
        self.assertTrue(os.path.isfile(path), '缺 tools/fetch_deck_default.py')

    def test_mokey_konami_id_comes_from_our_card_table(self):
        """`cid`=6018（游戏内）↔ `id`=27288416（Konami 密码）—— 卡绘 URL 要用后者。"""
        path = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')
        if not os.path.isfile(path):
            self.skipTest('没有 ygocdb 卡表快照')
        with open(path, encoding='utf-8') as fh:
            cards = json.load(fh)
        card = cards.get('6018')
        self.assertIsNotNone(card, '卡表里没有 cid=6018')
        self.assertEqual(card.get('cn_name'), '悠悠')
        self.assertEqual(int(card.get('id')), 27288416)



class DemoStatsPageModeFilterTest(unittest.TestCase):
    """★ demo 的模式筛选与真库**同一条路**（筛行 → 丢空组 → 重算组头）。"""

    def test_mode_filter(self):
        self.assertEqual(ui_demo.demo_stats_page()['overview']['n_total'], 53)
        d_r = ui_demo.demo_stats_page('ranked')
        self.assertEqual(d_r['overview']['n_total'], 33)
        self.assertEqual(d_r['row_total'], 5)
        self.assertEqual([n for n, _s, _r in d_r['groups']], ['闪刀姬', '烙印'])
        self.assertEqual(d_r['mode_group'], 'ranked')
        #: 非天梯模式没有真段位 → 最高段位空
        self.assertIsNone(ui_demo.demo_stats_page('event')['best_rank'])


class DemoStatsWindowTest(unittest.TestCase):
    """S6 统计窗的 demo 数据 —— **形状与真库那条路逐键对齐**，否则 demo 跑通不算数。"""

    KEYS = ('mode_group', 'overview', 'groups', 'daily', 'months', 'cross',
            'decks', 'cross_scope')

    def test_keys_match_data_layer(self):
        """★ demo 的键集合必须与**真库那条路**完全一致（少一个键界面就少一块）。"""
        class _EmptyStorage(object):
            """最小桩：每个方法都返回空 —— 只用来问「键有哪些」。"""
            def get_stats(self, **kw):
                return {}

            def get_group_stats(self, **kw):
                return []

            def get_daily_stats(self, **kw):
                return {}

            def get_stats_by_deck(self, **kw):
                return []

            def get_cross_stats(self, **kw):
                return []

        d = ui_demo.demo_stats_window()
        real = ui_state.stats_window_data(_EmptyStorage())
        self.assertEqual(set(d), set(real),
                         'demo 与真库的键集合不一致：%r vs %r'
                         % (sorted(d), sorted(real)))
        for k in self.KEYS:
            self.assertIn(k, d)

    def test_totals_agree_across_blocks(self):
        """★ 三块合计必须相等（四组 / 按月 / 按卡组）—— 这正是真库里最容易漂的地方。"""
        d = ui_demo.demo_stats_window()
        by_group = sum(s['n_total'] for _k, _n, s in d['groups'])
        by_day = sum(v['n'] for v in d['daily'].values())
        by_deck = sum(s['n_total'] for _n, s in d['decks'])
        self.assertEqual(by_group, by_day)
        self.assertEqual(by_group, by_deck)
        self.assertEqual(d['overview']['n_total'], by_group)

    def test_filter_selects_one_group(self):
        d = ui_demo.demo_stats_window('ranked')
        self.assertEqual(d['overview']['n_total'], 33)
        self.assertEqual(sum(s['n_total'] for _n, s in d['decks']), 33)
        self.assertEqual(sum(v['n'] for v in d['daily'].values()), 33)

    def test_pseudo_filtering_matches_real_rules(self):
        hidden = ui_demo.demo_stats_window()
        shown = ui_demo.demo_stats_window(None, show_pseudo=True)
        self.assertEqual(len(shown['cross']) - len(hidden['cross']), 2)
        self.assertTrue(all(r[1] not in ui_actions.NON_DECK_LABELS
                            for r in hidden['cross']))
        self.assertTrue(any(r[1] in ui_actions.NON_DECK_LABELS
                            for r in shown['cross']))

    def test_months_from_daily(self):
        d = ui_demo.demo_stats_window()
        self.assertEqual(d['months'], sorted({k[:7] for k in d['daily']}))


class DemoAnalysisPageTest(unittest.TestCase):
    """D2 `demo_analysis_page` —— demo **直接吃真函数** `ui_state.cross_matrix`
    （与 D1「组头用真函数重算」同款防漂移），锁别退回手拼 dict。"""

    KEYS = ('note', 'went_first', 'month', 'mode_group', 'cols', 'rows',
            'col_avgs', 'months', 'total_calibre', 'total_in_matrix',
            'hand_traps')        # 2026-10-16：第二个视图「手坑汇总」

    def test_keys_match_data_layer(self):
        """★ demo 的键集合必须与真函数一致（审查 §5 契约锁）。"""
        demo = ui_demo.demo_analysis_page()
        real = ui_state.cross_matrix(_DemoEmptyCross())
        self.assertEqual(set(demo), set(real),
                         'demo 与真函数的键集合不一致：%r vs %r'
                         % (sorted(demo), sorted(real)))
        self.assertEqual(sorted(demo), sorted(self.KEYS))

    def test_tabs_share_fixed_axis(self):
        """页签轴固定（P2-1）：先攻/后攻的行列序与综合一致。"""
        base = ui_demo.demo_analysis_page()
        self.assertIs(base['went_first'], None)
        for wf in (True, False):
            d = ui_demo.demo_analysis_page(wf)
            self.assertIs(d['went_first'], wf)
            self.assertEqual(d['cols'], base['cols'])
            self.assertEqual([r['deck'] for r in d['rows']],
                             [r['deck'] for r in base['rows']])

    def test_orientation_v4(self):
        """★ v4（用户 2026-09-19）：列 = 我方卡组（少的做列）、行 = 对手卡组；
        col_avgs = 各列我方卡组的综合胜率。"""
        d = ui_demo.demo_analysis_page()
        # 09-28 起未知卡组行计入列场数 → K9vs（含 vs 未知 16 场）排到第一
        self.assertEqual(d['cols'], ['K9vs', '闪刀姬', '耀圣'])
        self.assertEqual([a['deck'] for a in d['col_avgs']], d['cols'])
        # 09-28 起「未知卡组」行显示（16 场，排最前）
        self.assertEqual([r['deck'] for r in d['rows']],
                         ['未知卡组', '烙印', '珠泪', '天杯龙', '闪刀姬',
                          '小丑戏帮', '码丽丝'])
        brand = d['rows'][1]
        # 列序变了（K9vs 含未知行场数排第一）→ 格序跟着列轴走
        self.assertEqual([(c['deck'], c['n']) for c in brand['cells']],
                         [('K9vs', 2), ('闪刀姬', 3), ('耀圣', 1)])
        self.assertEqual(brand['n'], 6)           # 行场数（烙印 3+2+1，v4b）

    def test_pseudo_hidden_and_mirror_present(self):
        """09-28 口径：未知卡组**显示**（巅峰局看不了对面）；其余伪条目仍隐藏。"""
        self.assertEqual(len(ui_demo.DEMO_CROSS), 8)      # S6 数据原样
        d = ui_demo.demo_analysis_page()
        self.assertIn('未知卡组', [r['deck'] for r in d['rows']])
        self.assertNotIn('(未标注)', [r['deck'] for r in d['rows']])
        self.assertNotIn('青眼白龙', d['cols'])   # 只跟伪条目打的列被剪掉
        mirror = [c for r in d['rows'] if r['deck'] == '闪刀姬'
                  for c in r['cells'] if c['deck'] == '闪刀姬']
        self.assertEqual(len(mirror), 1)                  # 镜像 = 普通格
        self.assertIn('主口径', d['note'])

    def test_imgs_and_months_attached(self):
        """v5：demo 给行/列挂卡图路径 + 月份清单（真库由 app loader 注入 img）。"""
        d = ui_demo.demo_analysis_page()
        with_img = [a for a in d['col_avgs'] if a.get('img')]
        self.assertTrue(with_img, '列头卡图该有值')
        self.assertEqual(d['months'], ['2026-09', '2026-08'])

    def test_empty_cells_under_tabs(self):
        """页签下没数据的行/列照实为空（n=0、rate=None），不造假格子。"""
        first = ui_demo.demo_analysis_page(True)
        yao = [a for a in first['col_avgs'] if a['deck'] == '耀圣'][0]
        self.assertEqual({k: yao.get(k) for k in ('deck', 'n', 'w', 'rate')},
                         {'deck': '耀圣', 'n': 0, 'w': 0, 'rate': None})
        maliss = [r for r in first['rows'] if r['deck'] == '码丽丝'][0]
        cell = maliss['cells'][0]
        self.assertEqual(cell['n'], 0)
        self.assertIsNone(cell['rate'])


class _DemoEmptyCross(object):
    """最小桩：只用来问「真函数的键有哪些」。"""

    def get_cross_stats(self, since=None, until=None, went_first=None):
        return []


class _LangCnMixin(object):
    """把 `ui/lang` 锁回 cn（跑完还原）—— ui_state 显示名/状态字按界面语言出，
    用户机器上可能已是 en，不锁就随机器状态翻车（与 test_ui_state 同款）。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from PySide6.QtCore import QSettings
        import ui.i18n as _i18n
        cls._factory_prev = _i18n._SETTINGS_FACTORY
        cls._ini = tempfile.mkdtemp(prefix='m7-langcn-')
        _i18n._SETTINGS_FACTORY = staticmethod(
            lambda: QSettings(os.path.join(cls._ini, 'ui.ini'),
                              QSettings.IniFormat))
        from ui.i18n import reset_lang
        reset_lang()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        import ui.i18n as _i18n
        _i18n._SETTINGS_FACTORY = cls._factory_prev
        from ui.i18n import reset_lang
        reset_lang()
        super().tearDownClass()


class DemoTierPageTest(_LangCnMixin, unittest.TestCase):
    """D3 `demo_tier_page` —— demo 直接吃真函数 `ui_state.tier_page_data`。"""

    KEYS = ('sections', 'deck_total')

    def test_keys_match_data_layer(self):
        demo = ui_demo.demo_tier_page()
        real = ui_state.tier_page_data(_DemoEmptyCross())
        self.assertEqual(set(demo), set(real))
        self.assertEqual(sorted(demo), sorted(self.KEYS))

    def test_sections_and_imgs(self):
        """四节 + demo 卡图挂上；显示名中文优先。"""
        d = ui_demo.demo_tier_page()
        self.assertEqual([s['tier'] for s in d['sections']],
                         ['Tier1', 'Tier2', 'Tier3', 'Trending'])
        t1 = d['sections'][0]['decks'][0]
        self.assertEqual(t1['display'], '烙印星辰')
        self.assertTrue(t1.get('img'), 'demo 芯片该有卡图路径')


class DemoBanlistPageTest(_LangCnMixin, unittest.TestCase):
    """D3 `demo_banlist_page` —— demo 直接吃真函数 `ui_state.banlist_page_data`。"""

    KEYS = ('current', 'recent', 'counts', 'current_total', 'last_effective')

    def test_keys_match_data_layer(self):
        demo = ui_demo.demo_banlist_page()
        real = ui_state.banlist_page_data(_DemoEmptyCross())
        self.assertEqual(set(demo), set(real))
        self.assertEqual(sorted(demo), sorted(self.KEYS))

    def test_status_labels_and_counts(self):
        """三分组齐全；状态中文不带数字。"""
        d = ui_demo.demo_banlist_page()
        self.assertEqual(d['counts'], {'禁止': 2, '限制': 2, '准限制': 1})
        self.assertEqual(d['current_total'], 5)
        for x in d['current']:
            self.assertIn(x['status'], ('禁止', '限制', '准限制'))



class TestNoRealPlayerNames(unittest.TestCase):
    """★ 发布阻塞项回归锁（2026-10-10）：**demo / 测试里不许出现真实对手名**。

    背景：`core/ui_demo.py` 是**随源码发布**的文件（v1.0.0 起就在公开仓库），
    而它的 demo 对手名一度是用户的真实对局对手（逐名能在
    `matches.opp_player_name` 里对上）。v1.0.0 三轮审查只抓到**截图**里的名字
    （已打码），源码这批从没清过 —— 用户 2026-10-10 拍板清掉并统一叫 `playerN`。

    本锁的判据**不依赖真库**（CI/干净克隆上没有 `masterduel.db`）——
    改为「**黑名单**」：把这次清掉的那批真名钉死，出现即红。
    """

    #: 强特征名 —— 直接子串匹配即可（这些串不会出现在正常代码里）
    STRONG = (
        'Taolanamz2', 'なごみ', 'Thragg', '卡手少年', 'あいとー',
        'Kader', '28K白少', 'Sione', 'もっちゃん', 'Zabuza', 'ピークマスター',
        '幻想乡的毛玉', '幻日のヨハネ',
    )
    #: 弱特征名 —— 是常见词的一部分，必须整词匹配（`Revi` ⊂ `Revision`）
    WEAK = ('Revi',)

    @classmethod
    def _scan_text(cls, text):
        """文本里命中的黑名单名（强 = 子串；弱 = 整词）。

        ⚠️ 2026-10-10 修订：原判据只匹配「被单引号完整包裹」的名字
        （`"'%s'" % n`），**漏掉了不带引号的引用** —— `test_fonts.py` 里
        `'Sione0123…'` / `'もっちゃんカタカナ…'` 就是这么漏过去的。
        """
        hits = []
        for n in cls.STRONG:
            if n in text:
                hits.append(n)
        for n in cls.WEAK:
            if re.search(r'(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])' % re.escape(n), text):
                hits.append(n)
        return hits

    def _sources(self):
        """要扫的源码文件 —— **跳过本文件自己**（黑名单就在这里面，否则自指必红）。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        me = os.path.normcase(os.path.abspath(__file__))
        for sub in ('core', 'ui', 'tools', 'tests'):
            base = os.path.join(root, sub)
            for dp, _dn, fns in os.walk(base):
                if '__pycache__' in dp:
                    continue
                for fn in fns:
                    if fn.endswith('.py'):
                        p = os.path.join(dp, fn)
                        if os.path.normcase(os.path.abspath(p)) != me:
                            yield p

    def test_no_blacklisted_real_names(self):
        bad = []
        for path in self._sources():
            with io.open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            for n in self._scan_text(text):
                bad.append('%s: %s' % (os.path.basename(path), n))
        self.assertEqual(bad, [], '这些真实对手名又出现了：%s' % bad)

    def test_scan_catches_unquoted_and_ignores_substring(self):
        """负向验证（2026-10-10 补）：**不带引号**的引用也要抓到；常见词不许误伤。

        本次就是栽在「只匹配引号包裹」上 —— `'Sione0123…'` 后跟 `0`，原判据不匹配。
        """
        # 不带引号也要抓（本次的漏网点）
        self.assertIn('Sione', self._scan_text("UI_OTHER = 'Sione0123456789'"))
        self.assertIn('もっちゃん', self._scan_text("JP_SAMPLE = 'もっちゃんカタカナ'"))
        # 引号包裹的当然要抓
        self.assertIn('Zabuza', self._scan_text("opp = 'Zabuza'"))
        # 弱特征名不许被常见词误伤（`Revi` ⊂ `Revision`）
        self.assertEqual(self._scan_text('wProcessorRevision = 1'), [])
        self.assertEqual(self._scan_text('Revision 版本'), [])
        # 干净文本 → 无命中
        self.assertEqual(self._scan_text("opp = 'player1'"), [])

if __name__ == '__main__':
    unittest.main()


class DemoResultRepairTest(unittest.TestCase):
    """demo 沙盒里的胜负修正：改得动、看得见、**真库一个字节不动**。"""

    def setUp(self):
        self.addCleanup(ui_demo.demo_reset_marks)

    def _real(self):
        import tempfile
        from core.storage import Storage
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = Storage(os.path.join(tmp.name, 'sandbox.db'))
        st.init_schema()
        return st

    def test_repair_does_not_touch_the_real_db(self):
        real = self._real()
        s = ui_demo.DemoSandboxStorage(real)
        s.repair_match_result('demo0001', 'Lose')
        #: 真库一行都没有（写口被拦住了）
        self.assertEqual(real.count(), 0)

    def test_demo_recent_reflects_the_change(self):
        s = ui_demo.DemoSandboxStorage(self._real())
        s.repair_match_result('demo0001', 'Lose')
        rows = {r['match_id']: r for r in ui_demo.demo_recent()}
        self.assertEqual(rows['demo0001']['result'], 'Lose')

    def test_reset_restores_pristine_results(self):
        s = ui_demo.DemoSandboxStorage(self._real())
        s.repair_match_result('demo0001', 'Lose')
        ui_demo.demo_reset_marks()
        rows = {r['match_id']: r for r in ui_demo.demo_recent()}
        self.assertEqual(rows['demo0001']['result'], 'Win')

    def test_keep_sentinel_leaves_result_alone(self):
        """没带 result 的调用（如将来别的列）不该改胜负。"""
        s = ui_demo.DemoSandboxStorage(self._real())
        self.assertEqual(s.repair_match_result('demo0001'), 1)
        rows = {r['match_id']: r for r in ui_demo.demo_recent()}
        self.assertEqual(rows['demo0001']['result'], 'Win')


class DemoDeckPagesTest(_LangCnMixin, unittest.TestCase):
    """D3 `demo_deck_composition` —— demo 契约。

    ★ demo **不依赖** gitignore 的参照库/真库（`reference=` 注入、桩 storage），
    干净克隆上也得画得出来 —— 这是它和「直接读真文件」那条路的差别。
    """

    COMP_KEYS = {'name', 'display', 'tier', 'total', 'avg_main', 'has_ref',
                 'main', 'extra', 'unknown', 'options', 'ref_fetched_at'}

    def test_composition_keys_and_content(self):
        d = ui_demo.demo_deck_composition()
        self.assertEqual(set(d), self.COMP_KEYS)
        self.assertTrue(d['has_ref'])
        self.assertTrue(d['main'] and d['extra'], '主卡/额外两栏都得有货')
        self.assertEqual(d['unknown'], 1, 'demo 故意放一张查不到的卡验页头小字')
        self.assertTrue(d['options'])
        # ★ 页头要交代参照库快照日期（月度快照，2026-09-21 §1.4 加的键）
        self.assertEqual(d['ref_fetched_at'], ui_demo.DEMO_REF_FETCHED_AT)

    def test_composition_accepts_any_name(self):
        """换卡组（下拉/Tier 芯片传站内英文名）在 demo 里也走同一条路。"""
        self.assertEqual(ui_demo.demo_deck_composition('Branded')['name'],
                         'Branded')
        self.assertEqual(ui_demo.demo_deck_composition('K9vs')['name'],
                         'Vanquish Soul K9')
        self.assertFalse(ui_demo.demo_deck_composition('不存在的卡组')['has_ref'])

