# -*- coding: utf-8 -*-
"""`ui/i18n.py` 护栏测试（2026-10-03，按外部审查 §4.3 落地）。

* a：`ui/*.py` 里所有 `tr('X')` 字面量的 X 必须在 `_T` 里
  （防改文案后翻译静默失效）；
* b：`_T` 每条键必须在 ui/+core/ 源码里被引用（抓「收了没接」的死键；
  宽松匹配 = 取键前 10 个字符 + 全文含注释 —— 够抓真死键，不误报）；
* c：en/ja 冒烟 —— 真构造页面断言关键控件文字 = 译文；
* d：常量一致性 —— `i18n.JA` 必须是全项目认的 `'jp'`（theme.LANGS）。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ui import i18n  # noqa: E402
from ui import theme  # noqa: E402

_APP = None          #: QApplication 单例的模块级强引用（别让 GC 收走）

UI_DIR = os.path.join(PROJ, 'ui')
CORE_DIR = os.path.join(PROJ, 'core')


def _read(path):
    with io.open(path, encoding='utf-8') as f:      # 关句柄（GD 噪声会干扰全量
        return f.read()                             # 跑时的 GC 时机）


def _ui_sources():
    return {fn: _read(os.path.join(UI_DIR, fn))
            for fn in os.listdir(UI_DIR) if fn.endswith('.py')}


def _tr_literal_keys():
    """AST 抠出 ui/*.py 里所有 `tr('X')` 的字面量 X。"""
    keys = set()
    for src in _ui_sources().values():
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'tr' and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


class GuardAllTrKeysExist(unittest.TestCase):
    """a：tr('X') 的 X 必须收进 _T —— 改了文案忘了补翻译会在这里红。"""

    def test_every_tr_literal_is_in_table(self):
        missing = sorted(k for k in _tr_literal_keys() if k not in i18n._T)
        self.assertEqual(missing, [], '这些 tr() 键没进 _T：%s' % missing)


class GuardEveryKeyIsUsed(unittest.TestCase):
    """b：_T 每条键必须在 ui/+core/ 源码里出现（抓「收了没接」的死键）。

    匹配是**宽松子串**（键前 10 字符），因为源码里有相邻字面量拼接的键。
    """

    def test_every_key_is_referenced(self):
        corpus = ''.join(_ui_sources().values())
        for fn in os.listdir(CORE_DIR):
            if fn.endswith('.py'):
                corpus += _read(os.path.join(CORE_DIR, fn))
        dead = []
        for k in i18n._T:
            head = k[:10]
            # 源码里换行/制表符写的是转义序列（\n），键值里是真字符 —— 两种形态都试
            cands = (head,
                     head.replace('\n', '\\n'),
                     head.replace('\t', '\\t'))
            if not any(c in corpus for c in cands):
                dead.append(k)
        self.assertEqual(dead, [], '这些 _T 键没有任何源码引用（死键）：%s' % dead)


class ConstantsConsistencyTest(unittest.TestCase):
    """d：i18n 的语言常量必须与全项目一致（P0-3 的回归锁）。"""

    def test_ja_is_jp_per_project_convention(self):
        self.assertEqual(i18n.JA, 'jp')
        self.assertIn(i18n.JA, theme.LANGS)
        self.assertIn(i18n.CN, theme.LANGS)

    def test_explicit_three_way_fallback(self):
        i18n._LANG = 'xx'                       # 未知语言 → 回落中文
        self.assertEqual(i18n.tr('战绩'), '战绩')
        i18n._LANG = 'en'
        self.assertEqual(i18n.tr('战绩'), 'Matches')
        i18n._LANG = 'jp'
        self.assertEqual(i18n.tr('战绩'), '対戦')
        i18n._LANG = 'cn'


class SmokeEnJpTest(unittest.TestCase):
    """c：en/ja 冒烟 —— **零控件**（纯字符串 + 裸对象逻辑断言）。

    ⚠️ 2026-10-03 段错误教训：这里原来真建页面（ExportPage/Rail/DashboardWindow），
    多出来的控件量把**全量套件**推过累积阈值 —— 后面某个模块创建 FloatingWindow
    时 Qt 直接 access violation（老提交 1933 测试不崩、加上本模块 1948 就崩）。
    冒烟要证明的只有「翻出来了 + key 驱动逻辑没坏」，控制器级别的断言足够。
    """

    def setUp(self):
        i18n._LANG = 'en'

    def tearDown(self):
        i18n._LANG = 'cn'

    def test_nav_strings_translated(self):
        from ui import dashboard
        names = [i18n.tr(n) for n, _k in dashboard.NAV_PAGES]
        self.assertIn('Matches', names)
        self.assertIn('Decks', names)
        self.assertNotIn('战绩', names)

    def test_export_month_filter_is_key_driven(self):
        """P0-1 回归锁（无控件版）：'month' → 月份项；'mode' → 模式项（不含单人）。"""
        from ui import export_page
        page = export_page.ExportPage.__new__(export_page.ExportPage)
        page._data = {'months': [('2026-09', '2026-09')]}
        self.assertEqual([v for v, _n in page._menu_items('month')],
                         ['2026-09'])
        keys = [k for k, _n in page._menu_items('mode')]
        self.assertIsNone(keys[0])
        self.assertNotIn('solo', keys)
        self.assertIn('ranked', keys)


if __name__ == '__main__':
    unittest.main()

class FallbackAndDuplicateKeysTest(unittest.TestCase):
    """★ BUG-B7-05 / B7-06（2026-10-20）。

    B7-05：未收录键要**回落中文**（模块 docstring 的原话），`⟦⟧` 只在
    `MD_I18N_STRICT=1`（开发期查漏）时出现。
    B7-06：`先手` / `后手` 曾有**重复键**把短文案覆盖成长文案
    （`First` -> `Going 1st`，75px vs 战绩页 56px 列宽 → 裁字）。
    """

    def setUp(self):
        self._lang = i18n._LANG
        self._strict = i18n.STRICT

    def tearDown(self):
        i18n._LANG = self._lang
        i18n.STRICT = self._strict

    def test_untranslated_key_falls_back_to_chinese(self):
        i18n.STRICT = False
        i18n._LANG = 'en'
        self.assertEqual(i18n.tr('这条一定没收录zzz'), '这条一定没收录zzz')
        i18n._LANG = 'ja'
        self.assertEqual(i18n.tr('这条一定没收录zzz'), '这条一定没收录zzz')

    def test_strict_mode_marks_missing_keys(self):
        i18n.STRICT = True
        i18n._LANG = 'en'
        self.assertEqual(i18n.tr('另一条没收录zzz'), '⟦另一条没收录zzz⟧')

    def test_missing_key_recorded_even_without_strict(self):
        """查漏名单照样记（否则开发期根本不知道漏了多少）。"""
        i18n.STRICT = False
        i18n._LANG = 'en'
        key = '第三条没收录zzz'
        if key in i18n.MISSES:
            i18n.MISSES.remove(key)
        i18n.tr(key)
        self.assertIn(key, i18n.MISSES)

    def test_first_second_are_short(self):
        """`先手`/`后手` 必须是短文案（56px 列宽）。"""
        self.assertEqual(i18n._T['先手'][0], 'First')
        self.assertEqual(i18n._T['后手'][0], 'Second')

    def test_no_duplicate_keys(self):
        """`_T` 里不许有重复键 —— 重复会静默覆盖（B7-06 就是这么来的）。

        直接扫源码文本（dict 字面量在 import 后已经折叠，看不到重复）。
        """
        import re
        path = os.path.join(PROJ, 'ui', 'i18n.py')
        seen, dups = set(), []
        with open(path, encoding='utf-8') as f:
            for line in f:
                m = re.match(r"^    '([^']+)':", line)
                if not m:
                    continue
                k = m.group(1)
                if k in seen:
                    dups.append(k)
                seen.add(k)
        self.assertEqual(dups, [], '重复键（后者静默覆盖前者）：%r' % dups)

