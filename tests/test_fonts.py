# -*- coding: utf-8 -*-
"""自带字体 + 字体链的**覆盖**单测（2026-09-15 换掉得意黑之后重写）。

三条口径（设计文档 `docs/M7悬浮窗可缩放设计.md` §4.5）：

1. **没有任何一族能同时伺候中日英** —— 汉字共用码位、中日写法不同，所以按语言切
   `theme.families(lang)`；拉丁/数字用自带的 Inter（有真 `tnum`）。
2. **覆盖必须用 fontTools 读 cmap 验**，不能用 `QtGui.QRawFont.supportsCharacter()`
   （对 CFF 字体误报，实测喂出过「常用汉字只覆盖 54%」这种错结论）。
3. 链尾必须留兜底：另一种 CJK + 符号字体。

⚠️ 覆盖用例按**真实字符集**（`data/card_names.json` 三语卡名）算，不写死「一个都不能缺」
—— 系统字体因机器而异，按缺失率断更有意义。
"""
import json
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')     # 必须在 Qt import 之前

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:                                          # pragma: no cover
    HAVE_QT = False

try:
    from fontTools.ttLib import TTFont
    HAVE_FT = True
except Exception:                                          # pragma: no cover
    HAVE_FT = False

if HAVE_QT:
    from ui import fonts, theme, widgets

#: 界面上会出现的中文（区块标题、表头、统计标签、按钮、设置）
UI_CN = ('先后攻统计总览最近对局卡组胜负对手名段位时间总场次胜率今日'
         '先攻后攻先手率战绩段位决斗巅峰天梯杯赛活动待补点击标注还没有记录'
         '设置显示语言开机自启隐藏退出调试模式获取新卡组名搜索取消确定')
#: Inter 该覆盖的：拉丁 / 数字 / 半角标点 / 箭头（对手名、数值、单位）
#: ⚠️ **全角标点 `（）．` 不在这里** —— 那是 CJK 族的活，Inter 本来就没有
UI_OTHER = 'Sione0123456789%+-()→↑↓—·'
#: 罗马数字（升段徽章用文字画，见 ui/floating.RankBadge）
UI_ROMAN = 'IVX'
#: 日文：假名 + 只在日文里出现的汉字（得意黑当年就是这些缺）
JP_SAMPLE = 'もっちゃんカタカナ・ドラゴン閃刀姫両亜仏価'

#: 系统字体 → 文件（**只用于覆盖率测量**；机器上没有就跳过该族）
FONT_FILES = {
    'Microsoft YaHei': r'C:\Windows\Fonts\msyh.ttc',
    'Noto Sans SC': r'C:\Windows\Fonts\NotoSansSC-VF.ttf',
    'Noto Sans JP': r'C:\Windows\Fonts\NotoSansJP-VF.ttf',
    'Meiryo': r'C:\Windows\Fonts\meiryo.ttc',
    'Yu Gothic UI': r'C:\Windows\Fonts\YuGothR.ttc',
}


def _cmap_of(path):
    """读一份字体文件的 cmap。

    ⚠️ `.ttc` 是**集合**（msyh.ttc 里有 4 款），不指定 `fontNumber` 会抛
    `TTLibFileIsCollectionError` —— 我们要第一款就够了。
    """
    try:
        f = TTFont(path, lazy=True, fontNumber=0)
    except Exception:
        f = TTFont(path, lazy=True)
    out = set()
    for table in f['cmap'].tables:
        out |= set(table.cmap.keys())
    f.close()
    return out


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class ChainTest(unittest.TestCase):
    """字体链的形状（纯配置，不需要字体文件）。"""

    def test_inter_is_the_first_family_for_latin(self):
        """拉丁/数字优先 Inter（自带、有 tnum）；缺字才往 CJK 掉。"""
        self.assertEqual(theme.families('cn')[0], 'Inter')
        self.assertIn('Segoe UI', theme.families('en'))

    def test_japanese_chain_prefers_a_japanese_family(self):
        """日文链里日文族必须排在中文族**前面**，否则日文字形永远轮不上。"""
        chain = theme.families('jp')
        self.assertLess(chain.index('Noto Sans JP'),
                        chain.index('Microsoft YaHei'))

    def test_every_chain_keeps_another_cjk_as_fallback(self):
        """英文界面里照样显示日文卡组名 —— 链尾必须有另一种 CJK。"""
        self.assertIn('Microsoft YaHei', theme.families('jp'))
        self.assertIn('Noto Sans JP', theme.families('cn'))

    def test_every_chain_ends_with_symbol_fallback(self):
        for lang in theme.LANGS:
            self.assertIn('Segoe UI Symbol', theme.families(lang))

    def test_chains_have_no_duplicate_family(self):
        for lang in theme.LANGS:
            chain = theme.families(lang)
            self.assertEqual(len(chain), len(set(chain)), chain)

    def test_unknown_language_falls_back_to_default(self):
        self.assertEqual(theme.families('de'), theme.families(theme.DEFAULT_LANG))

    def test_smiley_sans_is_gone(self):
        """D1：得意黑换掉了 —— 文件、常量、链里都不该再有它。"""
        self.assertFalse(hasattr(theme, 'FONT_FAMILY'),
                         'FONT_FAMILY 是得意黑时代的常量，已删')
        self.assertFalse(
            os.path.exists(os.path.join(fonts.FONT_DIR,
                                        'SmileySans-Oblique.otf')))
        for lang in theme.LANGS:
            self.assertNotIn('Smiley Sans', theme.families(lang))


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class LoadTest(unittest.TestCase):

    def test_font_file_is_bundled(self):
        self.assertTrue(fonts.has_app_font(),
                        'data/fonts/ 下没有字体文件 —— 换机器就掉字体')

    def test_load_returns_family_names(self):
        QApplication.instance() or QApplication([])
        fams = fonts.load_app_fonts()
        self.assertTrue(fams, fonts.load_app_fonts())
        self.assertIn('Inter', fams)

    def test_never_raises_without_the_dir(self):
        self.assertIsInstance(fonts.load_app_fonts(), list)

    def test_qt_actually_resolves_to_inter(self):
        from PySide6.QtGui import QFontInfo
        QApplication.instance() or QApplication([])
        fonts.load_app_fonts()
        # ⚠️ 无头环境的字体引擎是桩，解析不出 family —— 那种情况下跳过
        resolved = QFontInfo(widgets.font(13)).family()
        if not resolved:
            self.skipTest('本环境字体引擎是桩（family 解析为空），跳过')
        self.assertEqual(resolved, 'Inter')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class PrepareAppTest(unittest.TestCase):
    """契约锁：**所有**建窗口的入口都必须走 `prepare_app()`。

    2026-09-15 实测踩过：`tools/render_ui_demo.py` 自己建 QApplication 却没注册字体，
    出的图全是微软雅黑版，而真机是得意黑版（同一份 demo：260×337 vs 260×325）——
    按图迭代 UI 等于瞎子摸象。
    """

    def test_prepare_app_registers_fonts_and_qss(self):
        qapp = QApplication.instance() or QApplication([])
        fams = fonts.prepare_app(qapp)
        self.assertIsInstance(fams, list)
        self.assertTrue(qapp.styleSheet(), 'prepare_app 必须把全局 QSS 上上去')

    def test_prepare_app_can_create_the_app_itself(self):
        self.assertIsInstance(fonts.prepare_app(), list)

    def test_render_script_goes_through_prepare_app(self):
        path = os.path.join(PROJ, 'tools', 'render_ui_demo.py')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('prepare_app', src,
                      '出图脚本必须走 ui.fonts.prepare_app()，否则图和真机不一致')


@unittest.skipUnless(HAVE_QT and HAVE_FT, '需要 PySide6 + fontTools')
class InterCoverageTest(unittest.TestCase):
    """Inter 只负责拉丁/数字/符号，中日文交给 CJK 族 —— 别指望它。"""

    @classmethod
    def setUpClass(cls):
        cls.cmaps = {}
        for fn in ('Inter-Regular.ttf', 'Inter-Medium.ttf'):
            p = os.path.join(fonts.FONT_DIR, fn)
            if os.path.isfile(p):
                cls.cmaps[fn] = _cmap_of(p)
        assert cls.cmaps, 'data/fonts/ 里没有 Inter，任务 2 没做完'

    def test_has_tabular_figures(self):
        """★ 等宽数字是选 Inter 的理由 —— 没有 tnum 就白换。"""
        for fn in self.cmaps:
            f = TTFont(os.path.join(fonts.FONT_DIR, fn), lazy=True)
            tags = [r.FeatureTag
                    for r in f['GSUB'].table.FeatureList.FeatureRecord]
            f.close()
            self.assertIn('tnum', tags, '%s 没有 tnum' % fn)

    def test_covers_ui_latin_and_symbols(self):
        for fn, cmap in self.cmaps.items():
            miss = [c for c in UI_OTHER + UI_ROMAN if ord(c) not in cmap]
            self.assertEqual(miss, [], '%s 缺：%s' % (fn, ''.join(miss)))

    def test_one_family_two_weights(self):
        """Regular 与 Medium 必须是**同一族**（web 构建原本叫 `Inter Medium`）。"""
        for fn in self.cmaps:
            f = TTFont(os.path.join(fonts.FONT_DIR, fn), lazy=True)
            self.assertEqual(f['name'].getDebugName(1), 'Inter', fn)
            f.close()


@unittest.skipUnless(HAVE_QT and HAVE_FT, '需要 PySide6 + fontTools')
class RealCharsetCoverageTest(unittest.TestCase):
    """按**真实字符集**（三语卡名）验链的整体覆盖 —— 这是权威口径。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(PROJ, 'data', 'card_names.json')
        if not os.path.isfile(path):
            raise unittest.SkipTest('没有 data/card_names.json')
        with open(path, encoding='utf-8') as fh:
            names = json.load(fh)['names']
        cls.buckets = {'cn': set(), 'jp': set()}
        for v in names.values():
            for i, key in enumerate(('cn', 'jp')):
                if len(v) > i and v[i]:
                    cls.buckets[key].update(ord(c) for c in v[i])

    def _chain_coverage(self, lang):
        covered = set()
        for fam in theme.families(lang):
            p = FONT_FILES.get(fam)
            if p and os.path.isfile(p):
                covered |= _cmap_of(p)
        return covered

    def test_cn_chain_covers_chinese_card_names(self):
        covered = self._chain_coverage('cn')
        if not covered:
            self.skipTest('这台机器上没有可测的 CJK 字体文件')
        chars = self.buckets['cn']
        rate = len(chars & covered) / len(chars)
        self.assertGreater(rate, 0.99, '中文卡名只覆盖 %.2f%%' % (rate * 100))

    def test_jp_chain_covers_japanese_card_names(self):
        covered = self._chain_coverage('jp')
        if not covered:
            self.skipTest('这台机器上没有可测的 CJK 字体文件')
        chars = self.buckets['jp']
        rate = len(chars & covered) / len(chars)
        self.assertGreater(rate, 0.99, '日文卡名只覆盖 %.2f%%' % (rate * 100))

    def test_kana_and_jp_only_kanji_are_covered(self):
        covered = self._chain_coverage('jp')
        miss = [c for c in JP_SAMPLE if ord(c) not in covered]
        self.assertEqual(miss, [], '日文缺：%s' % ''.join(miss))


if __name__ == '__main__':
    unittest.main()
