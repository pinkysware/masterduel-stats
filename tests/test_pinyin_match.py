# -*- coding: utf-8 -*-
"""`core/pinyin_match` 单测 —— 卡组选择器的拼音/首字母搜索（中文独有）。

用户原话（2026-09-19）：闪刀姬输入 ``sdj`` 或 ``shandaoji`` 都能查到；
切换到其他语言关闭这个功能（语言闸在 ui/selector，本模块只管键与匹配）。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import pinyin_match as pm                       # noqa: E402


@unittest.skipUnless(pm.available(), 'pypinyin 没装（功能整体降级）')
class KeysTest(unittest.TestCase):
    """键 = (全拼, 首字母)，只取汉字、小写无分隔。"""

    def test_full_and_initials(self):
        self.assertEqual(pm.keys('闪刀姬'), ('shandaoji', 'sdj'))
        self.assertEqual(pm.keys('青眼白龙'), ('qingyanbailong', 'qybl'))
        self.assertEqual(pm.keys('圣徒白森'), ('shengtubaisen', 'stbs'))

    def test_mixed_text_keeps_only_chinese(self):
        """英文名 / 别名里的 ASCII 段不掺进键（五字段匹配本来就管它们）。"""
        self.assertEqual(pm.keys('K9vs'), ('', ''))
        self.assertEqual(pm.keys('VSK9 / 对击斗魂'), ('duijidouhun', 'djdh'))

    def test_empty_and_none(self):
        self.assertEqual(pm.keys(''), ('', ''))
        self.assertEqual(pm.keys(None), ('', ''))


@unittest.skipUnless(pm.available(), 'pypinyin 没装（功能整体降级）')
class MatchTest(unittest.TestCase):

    def test_sdj_and_full_pinyin_hit(self):
        """★ 用户原始需求：sdj / shandaoji 都能查到 闪刀姬。"""
        self.assertTrue(pm.match('sdj', '闪刀姬'))
        self.assertTrue(pm.match('shandaoji', '闪刀姬'))

    def test_case_insensitive(self):
        self.assertTrue(pm.match('SDJ', '闪刀姬'))
        self.assertTrue(pm.match('ShanDaoJi', '闪刀姬'))

    def test_substring_is_the_fuzzy_rule(self):
        """子串即命中（模糊口径）：andao ⊂ shandaoji。"""
        self.assertTrue(pm.match('andao', '闪刀姬'))

    def test_no_cross_deck_hit(self):
        self.assertFalse(pm.match('sdj', '青眼白龙'))
        self.assertFalse(pm.match('qybl', '闪刀姬'))

    def test_matches_any_of_the_texts(self):
        """多个候选字段任一命中即可（中文名/别名分开传）。"""
        self.assertTrue(pm.match('ly', 'Branded', '烙印星辰'))
        self.assertTrue(pm.match('stbs', None, '', '圣徒白森'))

    def test_ascii_only_names_never_hit(self):
        self.assertFalse(pm.match('k9', 'K9vs'))

    def test_pinyin_query_gate(self):
        """闸 3：查询必须**纯英文字母**才走拼音（中文/数字/符号走五字段直配）。"""
        self.assertTrue(pm.is_pinyin_query('sdj'))
        self.assertFalse(pm.is_pinyin_query('闪'))
        self.assertFalse(pm.is_pinyin_query('sdj1'))
        self.assertFalse(pm.is_pinyin_query(''))
        self.assertFalse(pm.match('闪', '闪刀姬'))         # 中文查询不是拼音查询


class DegradedTest(unittest.TestCase):
    """闸 2：pypinyin 缺席时功能整体关闭、绝不抛（UI 静默降级为五字段搜索）。"""

    def test_flag_off_means_no_match_no_crash(self):
        from unittest import mock
        #: ⚠️ keys() 有 lru_cache —— 关标志期间的空键会被缓存污染别的用例
        #:    （字母序本类先跑，实测真踩了），跑完必须清。
        self.addCleanup(pm.keys.cache_clear)
        pm.keys.cache_clear()      # 开测先清：别让前面用例缓存的真实键混进来
        with mock.patch.object(pm, '_HAS_PYPINYIN', False):
            self.assertFalse(pm.available())
            self.assertFalse(pm.is_pinyin_query('sdj') and pm.match('sdj', '闪刀姬'))
            self.assertEqual(pm.keys('闪刀姬'), ('', ''))
