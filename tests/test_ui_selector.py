# -*- coding: utf-8 -*-
"""ui/selector.py（S3 卡组选择器）+ core.ui_actions.mark_meta_deck + app 接线单测。

用 ``QT_QPA_PLATFORM=offscreen`` 跑，无显示器也能过。
Storage 一律 duck-typing 桩（选择器只用 3 个读方法 + 标注链路 2 个写方法），
**不碰真库**。

⚠️ 没装 PySide6 时整个模块跳过（数据层不该因为界面依赖变红）。
"""
import os
import sys
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')     # 必须在 Qt import 之前

try:
    from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QPointingDevice
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel,
                                   QAbstractButton)
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    from core import ui_actions
    from ui import app as app_mod, floating, selector, theme
    from ui.selector import DeckRow, deck_display

_APP = None
_SETTINGS_DIR = None


def _isolate_settings():
    """把 `FloatingWindow` 的 QSettings 指到**临时 INI**（不碰用户真实配置）。

    理由同 `tests/test_ui_floating.py`：用户拖过窗口后 `float/size` / `float/pos`
    会真实落盘，测试读到就会红/跑偏。
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
    # ★ app 侧 `settings()` 同样指过去（_mark 的显示名按 ui/lang 取）
    app_mod.settings = staticmethod(
        lambda: QSettings(path, QSettings.IniFormat))



def _app():
    """QApplication 是进程级单例 —— 整个测试模块共用一份。"""
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        _APP.setStyleSheet(theme.qss())
    _isolate_settings()
    return _APP


# ------------------------------------------------------------- 桩与样例
def _deck(name, cn=None, ja=None, tier=None, aliases=None):
    return {'name': name, 'name_cn': cn, 'name_ja': ja, 'tier': tier,
            'name_display': None, 'aliases': aliases or []}


DECKS = [
    _deck('Ryzeal', cn='雷火', tier='Tier1'),
    _deck('Branded', cn='烙印', ja='烙印', tier='Tier2'),
    _deck('Tenpai Dragon', cn='天盃龙'),
    _deck('Mitsurugi', cn='御剑', ja='御剣', aliases=['Swordsoul']),
]


class _FakeStorage(object):
    """duck-typing Storage：选择器 + 标注链路用到的方法。"""

    def __init__(self, decks, opp_decks=()):
        self._decks = list(decks)
        self._opp = [tuple(x) for x in opp_decks]   # (id, name, aliases, n)
        self.marked = []                            # mark_opp_deck 调用记录

    # ---- 选择器读 ----
    def list_meta_decks(self, include_hidden=False):
        return list(self._decks)

    def search_meta_decks(self, query, limit=20, include_hidden=False):
        q = (query or '').lower()
        hits = [d for d in self._decks
                if q in (d.get('name') or '').lower()
                or q in (d.get('name_cn') or '')
                or q in (d.get('name_ja') or '')]
        return hits[:limit]

    def list_opp_decks(self):
        return list(self._opp)

    # ---- 标注链路写 ----
    def upsert_opp_deck(self, name, aliases=None):
        for oid, n, _a, _c in self._opp:
            if n == name:
                return oid
        oid = 100 + len(self._opp)
        self._opp.append((oid, name, aliases, 0))
        return oid

    def mark_opp_deck(self, match_id, opp_deck_id=None, name=None, note=None):
        self.marked.append((match_id, opp_deck_id, name, note))


def _row(**kw):
    """一行快照数据（默认是「已标注的胜局」——与 test_ui_floating._row 同构）。"""
    base = {
        'match_id': 'm1', 'started_at': '2026-09-13 19:53',
        'day': '09-13', 'time': '19:53', 'is_today': False, 'result': 'Win',
        'went_first': 1,
        'my_deck': '闪刀姬',
        'my_deck_img': 'data/deck_images_custom/Sky_Striker.webp',
        'opp_deck': '烙印', 'opp_known': True,
        'opp_deck_img': 'data/deck_images_custom/Branded.webp',
        'opp_player': '幻想乡的毛玉',
        'opp_deck_id': 7,
        'badge_icon': None, 'badge_text': '',
        'rank_before': (5, 2), 'rank_after': None, 'rank_delta': None,
        'rank_label_before': '', 'rank_label_after': '',
        'rank_icon_after': None, 'badge_numeral': '', 'badge_numeral_after': '',
    }
    base.update(kw)
    return base


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class CustomNameRowTest(unittest.TestCase):
    """★ 用户 2026-09-16 定稿：那条 = **输入框 + 右侧「确定」**。

    ⚠️ 中途试过「显示态 + 编辑按钮」两态，真机上「完成」按钮与输入框的 `FocusOut`
    时序打架、修不干净 —— 用户拍板**回退成这一版**（输入框常显 + 右边「确定」），
    所以这里**别再出现** begin_edit / 完成 / Esc 撤回 那套用例。

    口径：
    * 位置固定在「未知卡组」上面（默认态与搜索态都要）；
    * 边打边配图：命中别名/模糊/文件名就用它的图，配不出回落**悠悠**；
    * **输入框有字 = 选中这一条** —— 右侧「确定」/ 回车 / 面板「确定」三条路都能确认。
    """

    def setUp(self):
        _app()
        self.st = _FakeStorage(DECKS)

    def _panel(self):
        return selector.DeckSelectorPanel(self.st, row=_row(opp_known=False),
                                          lang='cn')

    # ------------------------------------------------------ 位置 / 配图
    def test_row_sits_above_unknown_deck(self):
        p = self._panel()
        row = p.custom_row()
        self.assertIsNotNone(row)
        unknown = p.deck_rows()[-1]
        self.assertEqual(unknown.deck.get('name'), selector.UNKNOWN_DECK_NAME,
                         '最后一条仍应是「未知卡组」')
        self.assertLess(p.rows_lay.indexOf(row),
                        p.rows_lay.indexOf(unknown),
                        '自定义行必须在「未知卡组」上面')

    def test_row_also_present_in_search_state(self):
        p = self._panel()
        p.search.setText('雷')
        unknown = p.deck_rows()[-1]
        self.assertEqual(unknown.deck.get('name'), selector.UNKNOWN_DECK_NAME)
        self.assertLess(p.rows_lay.indexOf(p.custom_row()),
                        p.rows_lay.indexOf(unknown))

    def test_image_resolves_by_name_with_youyou_fallback(self):
        """按名字配图：命中就用它的图；配不出回落悠悠（两者必须不同）。

        ⚠️ 索引只登记**有 `img_file` 的** meta 行（`build_index`）—— 所以桩里必须
        给 `img_file`，否则这条测的是「全都回落默认图」。
        """
        self.st = _FakeStorage([
            dict(d, img_file='data/deck_images_custom/Branded.webp') for d in DECKS])
        p = self._panel()
        hit = p._resolve_img('烙印')                  # 别名索引命中
        miss = p._resolve_img('zzz这个卡组不存在zzz')
        self.assertTrue(hit)
        self.assertTrue(miss)
        self.assertIn('_default', miss, '配不出应回落悠悠默认图')
        self.assertNotEqual(hit, miss)

    def test_thumb_follows_typed_name(self):
        """边打边换图 —— 用一个打桩解析器盯「有没有拿输入去解析」。"""
        calls = []

        def resolve(name):
            calls.append(name)
            return 'data/deck_images_custom/Branded.webp' if name == '烙印' else ''

        row = selector.CustomNameRow(resolve, text='')
        row.edit.setText('烙印')
        self.assertEqual(calls[-1], '烙印')
        self.assertIsNotNone(row.thumb.pixmap())

    # ------------------------------------------------------ 确认路径
    def test_confirm_button_commits(self):
        """★★ 用户口径：**输入名字后点右边的「确定」就确认**。"""
        p = self._panel()
        got = []
        p.confirmed.connect(got.append)
        p.custom_row().edit.setText('炫岚闪刀姬')
        self.assertEqual(p.custom_row().btn_confirm.text(), '确定')
        p.custom_row().btn_confirm.click()
        self.assertEqual(len(got), 1, '点右边的确定就该确认')
        self.assertEqual(got[0]['name'], '炫岚闪刀姬')
        self.assertTrue(got[0].get('custom'), '要标成自定义条目（便于排查来源）')
        self.assertEqual(p.custom_row().text(), '', '确认后清空输入框')

    def test_enter_also_commits(self):
        """回车是同一条路（保留）。"""
        p = self._panel()
        got = []
        p.confirmed.connect(got.append)
        p.custom_row().edit.setText('我的自制卡组')
        p.custom_row().edit.returnPressed.emit()
        self.assertEqual([g['name'] for g in got], ['我的自制卡组'])

    def test_blank_confirm_does_nothing(self):
        """空名字点「确定」= 什么都不发生（别写进一个无名条目）。"""
        p = self._panel()
        got = []
        p.confirmed.connect(got.append)
        p.custom_row().edit.setText('   ')
        p.custom_row().btn_confirm.click()
        self.assertEqual(got, [])

    def test_typing_enables_panel_ok_and_confirms(self):
        """输入框有字 = 选中了这一条 —— 所以**面板顶上那个「确定」**也能确认。

        ⚠️ 面板的「确定」只看 `chosen`，而这一行不是 `DeckRow`、不参与选择 ——
        2026-09-16 用户实测「打完字按确定没反应」，故有这条锁。
        """
        p = self._panel()
        got = []
        p.confirmed.connect(got.append)
        p.custom_row().edit.setText('炫岚闪刀姬')
        self.assertTrue(p.btn_ok.isEnabled(), '有字就该能按面板的「确定」')
        self.assertEqual(p.chosen.get('name'), '炫岚闪刀姬')
        p.btn_ok.click()
        self.assertEqual([g['name'] for g in got], ['炫岚闪刀姬'])

    def test_clearing_input_drops_the_custom_choice(self):
        """清空输入 = 撤掉这条选择（别「看着没选却写了库」）。"""
        p = self._panel()
        p.custom_row().edit.setText('炫岚闪刀姬')
        self.assertIsNotNone(p.chosen)
        p.custom_row().edit.clear()
        self.assertIsNone(p.chosen)
        self.assertFalse(p.btn_ok.isEnabled())

    def test_typing_overrides_prefilled_meta(self):
        """已标注行打开时预填了原卡组 —— 一旦开始打字，选择就该变成自定义那条。"""
        self.st = _FakeStorage(DECKS, opp_decks=[(7, 'Branded', None, 3)])
        p = selector.DeckSelectorPanel(
            self.st, row=_row(opp_known=True, opp_deck='烙印', opp_deck_id=7),
            lang='cn')
        self.assertEqual(p.chosen.get('name'), 'Branded', '开局预填原卡组')
        p.custom_row().edit.setText('我的烙印')
        self.assertEqual(p.chosen.get('name'), '我的烙印')

    def test_click_empty_custom_row_selects_nothing(self):
        """还没起名字时点那条是空操作（不能写进一个无名条目）。"""
        p = self._panel()
        p.custom_row().clicked.emit()
        self.assertIsNone(p.chosen)

    # ------------------------------------------------------ 版式 / 可观测
    def test_confirm_button_column_matches_suggest_marker(self):
        """★ 用户要求：右边的按钮要占「建议」**同一列**（都在右缘）。"""
        p = selector.DeckSelectorPanel(
            self.st, row=_row(opp_known=False), lang='cn',
            suggestions=[{'name': 'Ryzeal', 'name_cn': '雷火', 'hits': 5}])
        p.adjustSize()
        sug = p.deck_rows()[0]
        marks = [lab for lab in sug.findChildren(QLabel) if lab.text() == '建议']
        self.assertTrue(marks, '第一条应该是「建议」那行')
        mark = marks[0]
        btn = p.custom_row().btn_confirm
        right_mark = mark.mapTo(p, mark.rect().topRight()).x()
        right_btn = btn.mapTo(p, btn.rect().topRight()).x()
        self.assertLessEqual(abs(right_mark - right_btn), 4,
                             '「确定」与「建议」的右缘要对齐（同一列）')

    def test_action_log_actually_writes(self):
        """动作日志必须真的写进去 —— 它 `except: pass`，静默失效过（缺 import os）。

        这条「点了没反应」只有真机复现，日志是唯一现场证据，不能再让它悄悄坏。
        """
        import io as _io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, 'data', 'archive', 'ui_selector.log')
        if os.path.exists(path):
            os.remove(path)
        selector._log('selftest-42')
        self.assertTrue(os.path.exists(path), '日志文件没生成')
        with _io.open(path, encoding='utf-8') as f:
            self.assertIn('selftest-42', f.read())


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class UnknownNameSingleSourceTest(unittest.TestCase):
    """★ 常量唯一来源（S6 审查 §3.5）—— 以前 `ui/selector` 和 `core/ui_actions`
    各定义一份，注释里还写着「有单测盯着」但那个单测**根本不存在**。现在真补上。"""

    def test_unknown_name_matches_ui(self):
        from core import ui_actions
        self.assertEqual(selector.UNKNOWN_DECK_NAME, ui_actions.UNKNOWN_DECK_NAME)
        self.assertEqual(selector.UNKNOWN_DECK_NAME, '未知卡组')

    def test_selector_does_not_redefine_it(self):
        """必须是 import 来的 —— 文件里不许再出现一次赋值。"""
        import io
        with io.open(selector.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn("UNKNOWN_DECK_NAME = '", src)


class DeckDisplayTest(unittest.TestCase):


    def test_display_priority(self):
        """默认（中文）显示名 = 中文名 > 显示英文名 > 站内名。"""
        self.assertEqual(deck_display(DECKS[0]), '雷火')
        self.assertEqual(deck_display({'name': 'Only'}), 'Only')
        self.assertEqual(deck_display({'name': 'X', 'name_display': 'Xd'}), 'Xd')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckDisplayLangTest(unittest.TestCase):
    """★ 只显示设置里选的语言（用户 2026-09-15：选中文就只显示中文卡名）。"""

    DECK = {'name': 'Branded', 'name_cn': '烙印', 'name_display': '烙印星辰',
            'name_ja': '烙印', 'aliases': ['BrandedDracotail']}

    def test_cn_prefers_chinese(self):
        self.assertEqual(selector.deck_display(self.DECK, 'cn'), '烙印')

    def test_jp_uses_japanese_name(self):
        d = dict(self.DECK, name_ja='エルフェンノーツ')
        self.assertEqual(selector.deck_display(d, 'jp'), 'エルフェンノーツ')

    def test_en_uses_site_display_name(self):
        self.assertEqual(selector.deck_display(self.DECK, 'en'), '烙印星辰')

    def test_falls_back_when_language_missing(self):
        d = {'name': 'Branded', 'name_cn': None, 'name_display': None,
             'name_ja': None}
        self.assertEqual(selector.deck_display(d, 'jp'), 'Branded')

    def test_unknown_lang_falls_back_to_cn(self):
        self.assertEqual(selector.deck_display(self.DECK, 'de'),
                         selector.deck_display(self.DECK, 'cn'))

    def test_aliases_are_not_displayed(self):
        """别名只用于**搜索**，不显示（D4 删掉了次名行）。"""
        self.assertNotIn('BrandedDracotail',
                         selector.deck_display(self.DECK, 'cn'))


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class DeckRowLeanTest(unittest.TestCase):
    """行只留「图 + 一个名字」：没有 tier 胶囊、没有次名行、行高 34（D4）。"""

    def setUp(self):
        # ⚠️ 造控件前必须有 QApplication —— 否则 Qt 直接崩（退出码 0xC0000409，踩过）
        _app()

    def test_row_shows_one_label_and_no_tier(self):
        row = selector.DeckRow({'name': 'Branded', 'name_cn': '烙印',
                                'name_display': '烙印星辰', 'name_ja': '烙印',
                                'tier': 'Tier1', 'aliases': []}, '', lang='cn')
        texts = [lab.text() for lab in row.findChildren(QLabel) if lab.text()]
        self.assertEqual(texts, ['烙印'], '行里只该有一行名字：%s' % texts)
        self.assertEqual(row.height(), theme.ROW_H)

    def test_row_height_is_34(self):
        self.assertEqual(theme.ROW_H, 34)

    def test_row_respects_lang(self):
        row = selector.DeckRow({'name': 'Branded', 'name_cn': '烙印',
                                'name_ja': 'ブランデッド', 'aliases': []},
                               '', lang='jp')
        texts = [lab.text() for lab in row.findChildren(QLabel) if lab.text()]
        self.assertEqual(texts, ['ブランデッド'])


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class PinyinSearchTest(unittest.TestCase):
    """★ 拼音/首字母搜索（**中文独有**，2026-09-19 用户需求）：

    闪刀姬敲 ``sdj`` / ``shandaoji`` 都能查到；**切到其他语言关闭**
    （搜不到，占位符也不提示拼音）。
    """

    def _deck(self):
        return _deck('Sky Striker', cn='闪刀姬', aliases=['闪刀'])

    def _panel(self, **kw):
        return selector.DeckSelectorPanel(
            _FakeStorage(kw.pop('decks', DECKS)), row=kw.pop('row', {}),
            lang=kw.pop('lang', 'cn'), suggestions=kw.pop('suggestions', None))

    def _search(self, query, lang='cn'):
        p = self._panel(decks=[self._deck(), _deck('Branded', cn='烙印')],
                        lang=lang)
        p.search.setText(query)                 # textChanged -> _refill
        names = [d.get('name') for d in (r.deck for r in p.deck_rows())
                 if d is not None]
        p.deleteLater()
        return names

    def test_initials_hit(self):
        self.assertIn('Sky Striker', self._search('sdj'))

    def test_full_pinyin_hits(self):
        self.assertIn('Sky Striker', self._search('shandaoji'))

    def test_partial_pinyin_hits(self):
        self.assertIn('Sky Striker', self._search('dao'))

    def test_alias_pinyin_hits(self):
        """别名也参与拼音：`耀圣`（yaosheng）的 ys 只在别名里 —— 证明别名字段被搜了。"""
        p = self._panel(decks=[_deck('Branded', cn='烙印', aliases=['耀圣'])])
        p.search.setText('ys')
        names = [d.get('name') for d in (r.deck for r in p.deck_rows())
                 if d is not None]
        p.deleteLater()
        self.assertIn('Branded', names)

    def test_off_when_language_is_not_cn(self):
        """★ 用户拍板：切到其他语言关闭这个功能。"""
        self.assertNotIn('Sky Striker', self._search('sdj', lang='en'))
        self.assertNotIn('Sky Striker', self._search('sdj', lang='jp'))

    def test_ly_finds_branded_above_fuzzy_direct_hits(self):
        """★ 用户实测（2026-09-19）：`ly` 搜不到烙印。

        `ly` 直配就能撞上一堆英文名（Purrely…），拼音命中若追加在后面会被
        显示上限（4 行 + 未知卡组）挤没 —— 拼音命中必须排前面。
        """
        p = self._panel(decks=[_deck('Purrely', cn='皮尔莉'),
                               _deck('Branded', cn='烙印')])
        p.search.setText('ly')
        names = [d.get('name') for d in (r.deck for r in p.deck_rows())
                 if d is not None]
        p.deleteLater()
        self.assertEqual(names[0], 'Branded', names)
        self.assertIn('Purrely', names)          # 直配没丢，只是靠后

    def test_placeholder_mentions_pinyin_only_in_cn(self):
        p_cn = self._panel(lang='cn')
        self.assertIn('拼音', p_cn.search.placeholderText())
        p_en = self._panel(lang='en')
        self.assertNotIn('拼音', p_en.search.placeholderText())
        p_cn.deleteLater()
        p_en.deleteLater()

    def test_chinese_query_still_works(self):
        """拼音是**加**进来的能力，中文直配一个字都不能少。"""
        self.assertIn('Sky Striker', self._search('闪刀'))


class PanelShapeTest(unittest.TestCase):
    """★ 面板形态（2026-09-15 用户定的 D1/D3/D4/D5/D8）。

    附着弹层（**不是** QDialog）、搜索框与取消/确定同一行、≤5 行、
    没有上下文头、没有页脚清单说明、M5 候选置顶（**搜索态不补回**，2026-09-16 改）。
    """

    def setUp(self):
        _app()

    def _panel(self, **kw):
        return selector.DeckSelectorPanel(
            _FakeStorage(kw.pop('decks', DECKS)), row=kw.pop('row', {}),
            lang=kw.pop('lang', 'cn'), suggestions=kw.pop('suggestions', None))

    def test_is_popup_not_dialog(self):
        p = self._panel()
        self.assertFalse(isinstance(p, QDialog), '不能再是 QDialog（会阻塞事件循环）')
        self.assertTrue(p.windowFlags() & Qt.Popup, '应当是 Qt.Popup 弹层')
        p.deleteLater()

    def test_search_and_buttons_share_one_row(self):
        p = self._panel()
        lay = p.search_row.layout()
        in_row = [lay.itemAt(i).widget() for i in range(lay.count())]
        self.assertIn(p.search, in_row)
        self.assertIn(p.btn_cancel, in_row)
        self.assertIn(p.btn_ok, in_row)
        self.assertLessEqual(lay.count(), 5, '一行里不该塞太多东西')
        p.deleteLater()

    def test_no_context_header_and_no_footer_note(self):
        p = self._panel()
        joined = ' '.join(lab.text() for lab in p.findChildren(QLabel))
        self.assertNotIn('对手：', joined, '上下文头（对手名）该删掉了')
        self.assertNotIn('masterduelmeta', joined, '页脚清单说明该删掉了')
        self.assertNotIn('这一局还没标注', joined, '未标注时不占一行提示')
        p.deleteLater()

    def test_at_most_five_rows(self):
        many = [_deck('D%d' % i) for i in range(20)]
        p = self._panel(decks=many)
        self.assertEqual(selector.MAX_ROWS, 5)
        self.assertLessEqual(len(p.deck_rows()), selector.MAX_ROWS)
        p.deleteLater()

    def test_default_rows_are_suggest_common_unknown(self):
        """★ 用户 2026-09-15 定的列表：**AI 建议(1) → 常选(3) → 未知卡组(1)**。"""
        decks = [_deck('A'), _deck('B'), _deck('C'), _deck('D'), _deck('E')]
        p = self._panel(decks=decks, suggestions=[dict(decks[0])])   # A = AI 建议 top1
        names = [r.deck['name'] for r in p.deck_rows()]
        self.assertEqual(len(names), 5)
        self.assertEqual(names[0], 'A', '第一条要是 AI 建议')
        self.assertEqual(names[-1], selector.UNKNOWN_DECK_NAME,
                         '最后一条要是「未知卡组」')
        self.assertEqual(len(set(names)), len(names), '不该有重复行')
        self.assertTrue(p.deck_rows()[0].is_suggestion())
        self.assertFalse(p.deck_rows()[-1].is_suggestion())
        p.deleteLater()

    def test_unknown_row_marks_the_match(self):
        """选「未知卡组」→ `chosen` 是那条伪 meta 行（走同一条 mark 链路）。"""
        p = self._panel()
        p._pick(p.deck_rows()[-1])
        self.assertEqual(p.chosen['name'], selector.UNKNOWN_DECK_NAME)
        self.assertTrue(p.btn_ok.isEnabled())
        p.deleteLater()

    def test_common_decks_fall_back_to_tier_top3(self):
        """今天还没标过 → 中间三条用 **Tier 榜前三**（meta 清单顺序）。"""
        p = self._panel(decks=[_deck('A'), _deck('B'), _deck('C'), _deck('D')])
        names = [r.deck['name'] for r in p.deck_rows()]
        self.assertEqual(names[:3], ['A', 'B', 'C'])
        p.deleteLater()

    def test_panel_width_follows_content(self):
        """★ 用户：「搜索框太宽，和卡组名贴着就行」—— 面板宽跟着内容走。

        ⚠️ 环境注记（2026-09-18 定位）：无头环境的字体引擎是**桩**，而
        `tests/test_fonts.py` 会把自带字体**注册进进程**（`addApplicationFont`
        撤不掉）—— 之后无头环境里 Inter 没有汉字字形，长中文名反而被量得**更窄**，
        短/长一起撞到宽度下限 300。那不是界面错，是这台机器量不出汉字宽。
        退化时**跳过**（同 `tests/test_fonts.py::test_qt_actually_resolves_to_inter`
        的先例：量不出字体的环境不硬断言）；单跑本文件、真机上照常断言。
        """
        short = self._panel(decks=[_deck('A', cn='甲')])
        long_ = self._panel(decks=[_deck('B', cn='一个非常非常非常非常非常非常长的卡组名字')])
        if long_.width() <= theme.SEL_W_MIN:
            self.skipTest('本环境量不出汉字宽度（长名字也撞下限 300），跳过')
        self.assertLess(short.width(), long_.width())
        self.assertGreaterEqual(short.width(), theme.SEL_W_MIN)
        self.assertLessEqual(long_.width(), theme.SEL_W)
        short.deleteLater()
        long_.deleteLater()

    def test_search_box_takes_focus_on_open(self):
        """★ 弹出就能直接打字 —— `Qt.Popup` 不会自动把焦点给 QLineEdit（实测踩过）。"""
        p = self._panel()
        p.show()
        _app().processEvents()
        self.assertTrue(p.search.hasFocus(), '焦点该在搜索框上')
        p.close()

    def test_search_shows_matches_not_stray_suggestions(self):
        """★ 2026-09-16（用户报「搜小丑戏帮搜不到」）：搜索态**不补回**候选。

        旧口径（2026-09-15「有搜索词也置顶」）会把 4 个格子塞满候选、把真正的
        匹配挤出可视区；现在：候选**本身匹配**才置顶（信息不丢），不匹配的不出现。
        """
        sug = [{'name': 'Branded', 'name_cn': '烙印', 'name_display': '烙印星辰',
                'name_ja': '烙印', 'tier': 'Tier2', 'aliases': []}]
        p = self._panel(suggestions=sug)
        self.assertEqual(p.deck_rows()[0].deck['name'], 'Branded')   # 空词：候选置顶
        p.search.setText('雷')
        names = [r.deck['name'] for r in p.deck_rows()]
        self.assertIn('Ryzeal', names)          # 「雷火」匹配上了
        self.assertNotIn('Branded', names)      # 不匹配的候选不再挤进来
        p.deleteLater()

    def test_matching_suggestion_still_pinned_with_query(self):
        """候选本身匹配搜索词 -> 仍置顶并带「建议」标记（旧口径的有效部分保留）。"""
        sug = [{'name': 'Ryzeal', 'name_cn': '雷火', 'name_display': None,
                'name_ja': None, 'tier': 'Tier1', 'aliases': []}]
        p = self._panel(suggestions=sug)
        p.search.setText('雷')
        self.assertEqual(p.deck_rows()[0].deck['name'], 'Ryzeal')
        self.assertTrue(p.deck_rows()[0].is_suggestion())
        self.assertIn('建议',
                      [w.text() for w in p.deck_rows()[0].findChildren(QLabel)])
        p.deleteLater()

    def test_hint_only_when_already_marked(self):
        """已标注行打开时才提示「当前已标注」（省垂直空间）。"""
        p = selector.DeckSelectorPanel(
            _FakeStorage(DECKS, opp_decks=[(7, 'Branded', None, 2)]),
            row={'opp_deck_id': 7}, lang='cn')
        self.assertTrue(p.hint.isVisibleTo(p), '已标注要有提示行')
        self.assertIn('当前已标注', p.hint.text())
        p.deleteLater()

    def test_confirm_emits_chosen_and_close_emits_nothing(self):
        """确认 → `confirmed(chosen)`；直接关（取消）→ 不写库。"""
        p = self._panel()
        got = []
        p.confirmed.connect(got.append)
        p._pick(p.deck_rows()[0])
        p._on_ok()
        self.assertEqual(got, [p.deck_rows()[0].deck])
        p2 = self._panel()
        seen = []
        p2.confirmed.connect(seen.append)
        p2._on_cancel()
        self.assertEqual(seen, [], '取消不许写库')
        p.deleteLater()
        p2.deleteLater()


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class PanelPositionTest(unittest.TestCase):
    """★ 面板贴**被点那一格**的右侧、与它顶对齐；右边放不下翻左侧；窗口动了跟着走。

    ⚠️ 2026-09-18 五轮改口径：基准从「窗口右缘」改成**锚点控件自己的右缘** ——
    用户：「点击卡组名后的卡组选择器直接出现在**点击卡组的右侧**，而不是整个 UI 外」。
    （悬浮窗那条路锚点是占满窗口宽的行，所以看起来仍是"贴窗口右侧"。）
    """

    def setUp(self):
        _app()
        self.win = floating.FloatingWindow()
        self.win.set_snapshot(_snap_one(_row()))
        self.win.show()
        _app().processEvents()
        self.row = self.win.row_widgets()[0]
        self.panel = selector.DeckSelectorPanel(
            _FakeStorage(DECKS), row=self.row.row, lang='cn',
            parent=self.win)

    def tearDown(self):
        self.panel.deleteLater()
        self.win.hide()
        self.win.deleteLater()

    def test_sits_right_of_the_anchor_aligned_with_it(self):
        # 悬浮窗默认在屏幕右上角 —— 那里右边没地方放 440 宽的面板（会翻到左侧）。
        # 这条用例要验的是「右侧优先」，所以先把窗挪到左边留出空间。
        screen = self.win.screen().availableGeometry()
        self.win.move(screen.left() + 20, screen.top() + 80)
        _app().processEvents()
        self.panel.show_near(self.row)
        _app().processEvents()
        tl = self.row.mapToGlobal(self.row.rect().topLeft())
        #: 基准 = **锚点右缘**（不是窗口右缘；行比窗口少 1px 边框）
        self.assertEqual(self.panel.x(), tl.x() + self.row.width()
                         + selector.PANEL_GAP)
        self.assertEqual(self.panel.y(), tl.y())

    def test_anchors_to_a_narrow_cell_not_the_window(self):
        """★ 锚点是**窄控件**时贴着它的右缘弹出 —— 看板里点「对手卡组」格就是这种。

        用户 2026-09-18：「点击卡组名后的卡组选择器直接出现在**点击卡组的右侧**，
        **而不是整个 UI 外**」。旧实现按窗口右缘算，看板（1200 宽）会把面板甩到窗口外。
        """
        from PySide6.QtWidgets import QWidget
        screen = self.win.screen().availableGeometry()
        self.win.move(screen.left() + 20, screen.top() + 80)
        _app().processEvents()
        cell = QWidget(self.win)
        cell.setGeometry(10, 40, 120, 24)
        cell.show()
        _app().processEvents()
        self.panel.show_near(cell)
        _app().processEvents()
        tl = cell.mapToGlobal(cell.rect().topLeft())
        self.assertEqual(self.panel.x(), tl.x() + cell.width()
                         + selector.PANEL_GAP)
        self.assertLess(self.panel.x(),
                        self.win.x() + self.win.width(),
                        '面板不该甩到窗口外')
        cell.deleteLater()

    def test_flips_to_the_left_when_no_room(self):
        screen = self.win.screen().availableGeometry()
        self.win.move(screen.right() - self.win.width() - 2, self.win.y())
        _app().processEvents()
        self.panel.show_near(self.row)
        _app().processEvents()
        self.assertLessEqual(self.panel.x() + self.panel.width(),
                             screen.right() + 1, '面板出屏了，应该翻到左侧')
        self.assertLess(self.panel.x(), self.win.x(), '应当在窗口左侧')

    def test_follows_the_window_move(self):
        self.panel.show_near(self.row)
        _app().processEvents()
        before = self.panel.pos()
        self.win.move(self.win.x() - 60, self.win.y() + 30)
        _app().processEvents()
        self.assertNotEqual(self.panel.pos(), before, '窗口移动时面板要跟着挪')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class SelectorTest(unittest.TestCase):

    def setUp(self):
        _app()
        self.st = _FakeStorage(DECKS, opp_decks=[(7, 'Branded', None, 2)])

    def _dlg(self, row=None, **kw):
        """面板（旧的 `DeckSelectorDialog` 已被它替代，2026-09-15）。"""
        p = selector.DeckSelectorPanel(self.st, row if row is not None else {},
                                       **kw)
        self.addCleanup(p.deleteLater)
        return p

    # ------------------------------------------------------------ 列表
    def test_full_list_without_query(self):
        """空搜索 = 全量清单（tier -> 强度序，桩里按原序）。"""
        dlg = self._dlg()
        self.assertEqual(len(dlg.deck_rows()), len(DECKS))

    def test_search_filters(self):
        dlg = self._dlg()
        dlg.search.setText('雷火')
        # 搜索结果 + 末尾固定一条「未知卡组」（用户 2026-09-15 定）
        self.assertEqual([r.deck['name'] for r in dlg.deck_rows()],
                         ['Ryzeal', selector.UNKNOWN_DECK_NAME])
        dlg.search.setText('swordsoul')             # 别名也能搜（桩不实现别名，
        # 但至少不能崩）；清空恢复全量
        dlg.search.setText('')
        self.assertEqual(len(dlg.deck_rows()), len(DECKS))

    def test_empty_search_hint(self):
        """搜不到 = 空态提示 + **只剩末尾那条「未知卡组」**。"""
        dlg = self._dlg()
        dlg.search.setText('不存在的卡组xyz')
        self.assertEqual([r.deck['name'] for r in dlg.deck_rows()],
                         [selector.UNKNOWN_DECK_NAME])
        self.assertIn('不存在的卡组xyz', dlg.empty_text())

    # ------------------------------------------------------------ 上下文（已删）
    def test_context_header_is_gone(self):
        """★ 用户 2026-09-15：搜索框上面那部分（对局上下文）删掉。"""
        p = self._dlg(_row())
        texts = ' '.join(lab.text() for lab in p.findChildren(QLabel))
        self.assertNotIn('对手：', texts)
        self.assertNotIn('19:53', texts)

    # ------------------------------------------------------------ 预填
    def test_prefill_by_opp_deck_id(self):
        """row 带已标注的 opp_deck_id → 预填对应 meta 条目 + 提示覆盖。"""
        dlg = self._dlg(_row())
        self.assertIsNotNone(dlg.chosen)
        self.assertEqual(dlg.chosen['name'], 'Branded')
        self.assertIn('当前已标注', dlg.hint.text())
        self.assertIn('烙印', dlg.hint.text())
        self.assertTrue(dlg.btn_ok.isEnabled())

    def test_no_prefill_when_unmarked(self):
        dlg = self._dlg(_row(opp_deck_id=None, opp_known=False,
                             opp_deck=None))
        self.assertIsNone(dlg.chosen)
        self.assertFalse(dlg.btn_ok.isEnabled())
        self.assertFalse(dlg.hint.isVisibleTo(dlg), '未标注不该占一行提示')

    def test_prefill_unknown_id_stays_empty(self):
        """opp_deck_id 在 opp_decks 里查不到 → 不预填（不硬猜）。"""
        dlg = self._dlg(_row(opp_deck_id=999))
        self.assertIsNone(dlg.chosen)

    # ------------------------------------------------------------ 选择
    def test_pick_sets_chosen_and_enables_ok(self):
        dlg = self._dlg(_row(opp_deck_id=None, opp_known=False,
                             opp_deck=None))
        self.assertFalse(dlg.btn_ok.isEnabled())
        rows = dlg.deck_rows()
        rows[2].clicked.emit(rows[2])               # Tenpai Dragon
        self.assertEqual(dlg.chosen['name'], 'Tenpai Dragon')
        self.assertTrue(dlg.btn_ok.isEnabled())

    def test_double_click_confirms(self):
        dlg = self._dlg(_row(opp_deck_id=None, opp_known=False,
                             opp_deck=None))
        got = []
        dlg.confirmed.connect(got.append)
        row = dlg.deck_rows()[1]
        row.double_clicked.emit(row)
        self.assertEqual(got, [dlg.chosen], '双击 = 确认（发 confirmed 信号）')
        self.assertEqual(dlg.chosen['name'], 'Branded')

    # ------------------------------------------------------------ 砍掉的按钮
    def test_no_unknown_button(self):
        """★ 没有「标记未知」**按钮**。

        ⚠️ 口径更新（2026-09-15 用户定）：列表**末尾多一条「未知卡组」行**——
        点它 = 用一个专门条目记下来（离开待标注队列）。但**按钮**仍然没有。
        """
        dlg = self._dlg(_row(opp_deck_id=None, opp_known=False,
                             opp_deck=None))
        texts = []
        for b in dlg.findChildren(QAbstractButton):
            texts.append(b.text())
        for lab in dlg.findChildren(QLabel):
            texts.append(lab.text())
        texts.append(dlg.windowTitle())
        for t in texts:
            self.assertNotIn('标记未知', t)
        # 「未知卡组」只以**列表最后一行**的形式存在
        self.assertEqual(dlg.deck_rows()[-1].deck['name'],
                         selector.UNKNOWN_DECK_NAME)

    def test_row_widget_is_deckrow(self):
        dlg = self._dlg()
        for r in dlg.deck_rows():
            self.assertIsInstance(r, DeckRow)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class MarkMetaDeckTest(unittest.TestCase):
    """core.ui_actions.mark_meta_deck —— 不碰 Qt。"""

    def setUp(self):
        self.st = _FakeStorage(DECKS)

    def test_binds_via_upsert_then_mark(self):
        """链路 = upsert_opp_deck(站内名) 换 opp_decks id → mark 存显示名。"""
        ok, msg = ui_actions.mark_meta_deck(self.st, 'm1', DECKS[0])
        self.assertTrue(ok, msg)
        self.assertEqual(self.st.marked, [('m1', 100, '雷火', None)])

    def test_existing_opp_deck_reuses_id(self):
        self.st._opp = [(7, 'Branded', None, 2)]
        ok, _ = ui_actions.mark_meta_deck(self.st, 'm1', DECKS[1])
        self.assertTrue(ok)
        self.assertEqual(self.st.marked, [('m1', 7, '烙印', None)])

    def test_rejects_empty(self):
        ok, _ = ui_actions.mark_meta_deck(self.st, 'm1', None)
        self.assertFalse(ok)
        ok, _ = ui_actions.mark_meta_deck(self.st, '', DECKS[0])
        self.assertFalse(ok)
        self.assertEqual(self.st.marked, [])

    def test_storage_error_is_caught(self):
        def boom(*a, **kw):
            raise RuntimeError('disk full')
        self.st.upsert_opp_deck = boom
        ok, msg = ui_actions.mark_meta_deck(self.st, 'm1', DECKS[0])
        self.assertFalse(ok)
        self.assertIn('写入失败', msg)


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class OnPickDeckTest(unittest.TestCase):
    """app.MasterDuelUiApp.on_pick_deck —— 对话框打桩，不真弹。"""

    def _appobj(self, storage):
        a = app_mod.MasterDuelUiApp.__new__(app_mod.MasterDuelUiApp)
        a.storage = storage
        a._demo = False                    # on_pick_deck 的调试模式守卫要看这个
        a.win = None                       # 对话框已打桩，parent 不会被用
        return a

    def test_confirm_binds_and_refreshes(self):
        """面板确认（`confirmed` 信号）→ 写库 + 刷新。"""
        st = _FakeStorage(DECKS)
        a = self._appobj(st)
        refreshed = []
        a.refresh = lambda: refreshed.append(1)
        panel = _FakePanel(deck=DECKS[1])
        with mock.patch.object(app_mod, 'DeckSelectorPanel',
                               lambda *args, **kw: panel):
            a.on_pick_deck(_row(), anchor=None)
            panel.confirmed.emit(panel.deck)       # 用户点了「确定」
        self.assertEqual(st.marked, [('m1', 100, '烙印', None)])
        self.assertEqual(refreshed, [1])

    def test_cancel_writes_nothing(self):
        """点面板外面 / 取消 → 什么都不写（「标记未知」已砍）。"""
        st = _FakeStorage(DECKS)
        a = self._appobj(st)
        refreshed = []
        a.refresh = lambda: refreshed.append(1)
        panel = _FakePanel(deck=DECKS[1])
        with mock.patch.object(app_mod, 'DeckSelectorPanel',
                               lambda *args, **kw: panel):
            a.on_pick_deck(_row(), anchor=None)
            panel.cancelled.emit()                 # 用户关掉了
        self.assertEqual(st.marked, [])
        self.assertEqual(refreshed, [])


class _FakePanel(QObject):
    """DeckSelectorPanel 替身 —— **不上屏**，靠信号驱动（旧对话框是 exec 打桩）。"""

    confirmed = Signal(object)
    cancelled = Signal()

    def __init__(self, deck=None, **kw):
        super().__init__(kw.get('parent'))
        self.deck = deck
        self.row = kw.get('row')
        self.lang = kw.get('lang')
        self.suggestions = kw.get('suggestions')
        self.shown_near = None

    def show_near(self, anchor):
        self.shown_near = anchor

    def show(self):
        pass


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class OppCellClickTest(unittest.TestCase):
    """floating.OppDeckCell —— 整格按键 + 点击位移阈值（S3 必修的拖窗误弹）。"""

    def setUp(self):
        _app()
        self.st = _FakeStorage(DECKS)
        self.win = floating.FloatingWindow()

    def tearDown(self):
        self.win.deleteLater()

    def _cell(self, known=True):
        self.win.set_snapshot(_snap_one(_row(opp_known=known)))
        return self._row_widget().opp_cell

    def _row_widget(self):
        rows = self.win.row_widgets()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_click_requires_small_move(self):
        """原地按下抬起 = 点击；按住拖远再抬起 = 拖窗，不弹选择器。"""
        cell = self._cell(known=False)
        got = []
        cell.clicked.connect(lambda: got.append(1))
        cell.mousePressEvent(_press())
        cell.mouseReleaseEvent(_release())
        self.assertEqual(got, [1])
        # 拖远：出阈值作废这次按下，抬起不 emit
        cell.mousePressEvent(_press())
        cell.mouseMoveEvent(_move(60, 0))
        cell.mouseReleaseEvent(_release())
        self.assertEqual(got, [1])

    def test_press_on_cell_never_reaches_window_drag(self):
        """按在卡组格上不许把窗口拖起来；空白处仍可拖窗。

        ⚠️ 「空白处」要挑**窗中间**：四条边 8px 内现在是**缩放热区**
        （`floating.RESIZE_MARGIN`），按在那里是缩放而不是拖窗。
        """
        cell = self._cell()
        cell.mousePressEvent(_press(cell))
        self.assertIsNone(self.win._drag_from,
                          '按在对手卡组格上不该开始拖窗')
        middle = QPointF(self.win.width() / 2.0, self.win.height() / 2.0)
        moved = []
        self.win._move_starter = lambda: moved.append(1) or True
        self.win.mousePressEvent(_press(self.win, middle))
        self.assertEqual(moved, [1], '行空白处仍应可以拖窗（走系统原生移动）')

    def test_compat_refs_point_to_cell_children(self):
        """旧引用 opp_thumb / opp_deck_label 必须还挂在行上（测试/外部在用）。"""
        self._cell()
        row = self._row_widget()
        self.assertIs(row.opp_thumb, row.opp_cell.thumb)
        self.assertIs(row.opp_deck_label, row.opp_cell.label)

    def test_unknown_cell_keeps_warn_look(self):
        """未标注：琥珀字「未知卡组」（2026-09-15 用户改的）+ 整格手型 + WARN_SOFT 底。"""
        cell = self._cell(known=False)
        self.assertEqual(cell.label.text(), '未知卡组')
        self.assertIn(theme.WARN, cell.label.styleSheet())
        self.assertIn(theme.WARN_SOFT, cell.styleSheet())
        self.assertEqual(cell.cursor().shape(), Qt.PointingHandCursor)
        self.assertTrue(cell.thumb.is_warning_badged(), '琥珀 `!` 角标要保留')

    def test_known_cell_click_emits_pick_deck(self):
        """已标注行整格可点：cell.clicked → MatchRow.pick_deck(row, 本行控件)。"""
        self._cell()
        got = []
        row = self._row_widget()
        self.win.pick_deck.connect(lambda r, a: got.append((r, a)))
        row.opp_cell.clicked.emit()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0]['match_id'], 'm1')
        self.assertIs(got[0][1], row, '要带出被点的行控件（面板靠它定位）')


def _snap_one(row):
    s = app_mod.ui_state.UiSnapshot()
    s.recent = [row]
    s.unmarked = []
    return s


def _press(widget=None, pos=None):
    """按下事件。`pos` 默认 (4,4) —— ⚠️ 那个点现在落在**缩放热区**里
    （`RESIZE_MARGIN=8`），要测「拖窗」得显式给个窗中间的点。"""
    from PySide6.QtCore import QEvent
    return QMouseEvent(QEvent.MouseButtonPress, pos or QPointF(4, 4),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


def _release(widget=None, pos=None):
    from PySide6.QtCore import QEvent
    return QMouseEvent(QEvent.MouseButtonRelease, pos or QPointF(4, 4),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


def _move(dx, dy):
    from PySide6.QtCore import QEvent
    return QMouseEvent(QEvent.MouseMove, QPointF(4 + dx, 4 + dy),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                       QPointingDevice.primaryPointingDevice())


if __name__ == '__main__':
    unittest.main()
