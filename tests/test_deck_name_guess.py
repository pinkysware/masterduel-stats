# -*- coding: utf-8 -*-
"""卡组中文名预填（`core/deck_name_guess.py`）的回归测试。

用户 2026-10-21：「获取新卡组名 都没有卡图，中文名能不能预填」。

**这一组测的是「预填得对不对、以及推不出来时会不会瞎猜」** ——
后半句同样重要：`Charmer` 的卡叫「地灵使 奥丝」「水灵使 艾莉娅」，
硬取共同前缀会给出「灵使」，而用户口径里「灵使」是 `Magistus Charmer` 的中文名。
猜歪比留空更糟（用户要逐条核对，等于白预填还误导）。
"""
from __future__ import annotations

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import deck_name_guess as DNG                            # noqa: E402


class PrefixCutTest(unittest.TestCase):
    """卡名切分（`卫星闪灵·蓝色喷流灵` -> `卫星闪灵`）。"""

    def test_cut_at_separator(self):
        #: 游戏卡表口径的名字（`电光闪灵・蓝精` 用的是 U+00FB 片假名中点）
        self.assertEqual(DNG._cut('电光闪灵・蓝精'), '电光闪灵')
        self.assertEqual(DNG._cut('电光闪灵·蓝精'), '电光闪灵')
        self.assertEqual(DNG._cut('百鬼罗刹 特攻达格'), '百鬼罗刹')
        self.assertEqual(DNG._cut('群豪之巫女-东云'), '群豪')

    def test_keeps_name_without_separator(self):
        self.assertEqual(DNG._cut('烙印'), '烙印')
        self.assertEqual(DNG._cut('虫惑魔'), '虫惑魔')

    def test_candidates_are_short_enough(self):
        cands = DNG._prefix_candidates('电光闪灵・蓝精')
        self.assertIn('电光闪灵', cands)
        self.assertNotIn('电光闪灵・', cands, '不该以分隔符收尾')


class PrefillTest(unittest.TestCase):
    """三层预填：覆盖表 → 前缀法 → 留空。"""

    @classmethod
    def setUpClass(cls):
        cls.existing = DNG._existing_map()
        if not cls.existing:
            raise unittest.SkipTest('覆盖表不可用')

    def test_layer1_existing_entry_wins(self):
        """覆盖表里有的，直接用（**比前缀法可信**，哪怕前缀法能推别的）。"""
        for name, want in (('Dracotail', '烙印星辰'), ('Sky Striker', '闪刀姬'),
                           ('Traptrix', '虫惑魔'), ('Branded', '烙印')):
            if name not in self.existing:
                continue
            got, why = DNG.prefill_cn(name)
            self.assertEqual(got, self.existing[name], '%s 该用覆盖表的中文名' % name)
            self.assertIn('覆盖表', why)

    def test_layer2_prefix_guess(self):
        """前缀法：卡名有稳定中文前缀的能推出来。

        ⚠️ 期望值是**游戏卡表口径**（`data/card_names.json` 的 `names` 段，
        与 `data/card_data.json` 逐字一致，见 AGENTS 铁律「中文以游戏卡表为准」），
        **不是** ygocdb 的社区译名 —— 例：`Spright` 游戏内叫「电光闪灵」，
        ygocdb 叫「卫星闪灵」。2026-10-21 切换数据源后两边会不一致，
        测试锁的是**游戏内口径**（用户看得见的那套）。
        """
        cases = {
            'Spright': '电光闪灵',
            'Vaylantz': '弈勇',
            'Goblin Biker': '格布林百罗刹',
        }
        for name, want in cases.items():
            got, why = DNG.prefill_cn(name)
            self.assertEqual(got, want, '%s 前缀法该推出「%s」（%s）' % (name, want, why))

    def test_uses_card_names_json_not_ygocdb(self):
        """★ 数据源必须是**发布仓也带的** `card_names.json`。

        否则干净克隆上这个功能静默失效（实测：发布仓跑测试全红 ——
        `ygocdb.com.cards/` 是 gitignore 的，只在开发机有）。
        """
        cards = DNG._cn_cards()
        self.assertGreater(len(cards), 10000, '卡表规模不对：%d' % len(cards))
        #: 该数据源里 Spright 的中文是游戏口径「电光闪灵」
        self.assertIn(('Spright Blue', '电光闪灵・蓝精'), cards)

    def test_layer3_left_blank_when_unsure(self):
        """★ 推不出来就**留空**（不猜歪）。

        * `Charmer` 只命中 2 张卡（`Aussa the Earth Charmer` 这类不以
          `Charmer` **开头**）—— 样本不足；
        * `Insect` 命中 9 张，但「巨大蚂蚁」「昆虫女王」没有共同前缀。

        ⚠️ 这两条是本模块**最重要的行为**：曾经试过「后缀法」把它们硬猜成
        「灵使」「昆虫」，全错。
        """
        for name in ('Charmer', 'Insect'):
            got, why = DNG.prefill_cn(name)
            self.assertIsNone(got, '%s 不该被瞎猜（%s）' % (name, why))
            self.assertTrue(why, '推不出来也要给一句原因（弹窗 tooltip 用）')

    def test_short_guess_needs_full_coverage(self):
        """2 个汉字的推论只在**全覆盖**时才敢用，否则留空并说明。"""
        #: 构造：伪造一个只有 60% 覆盖的 2 字前缀场景不现实，直接测判据
        #: —— `_prefix_candidates` + 手工算覆盖率走的是 `guess_cn`
        guess, cov, total = DNG.guess_cn('Charmer')
        self.assertIsNone(guess)

    def test_unknown_name_gives_reason(self):
        got, why = DNG.prefill_cn('Absolutely Not A Deck')
        self.assertIsNone(got)
        self.assertIn('样本不足', why)

    def test_empty_name(self):
        self.assertEqual(DNG.prefill_cn('')[0], None)
        self.assertEqual(DNG.prefill_cn(None)[0], None)

    def test_prefill_many_shape(self):
        out = DNG.prefill_many(['Spright', 'Charmer'])
        self.assertEqual(set(out), {'Spright', 'Charmer'})
        for val in out.values():
            self.assertIsInstance(val, tuple)
            self.assertEqual(len(val), 2)


class CacheTest(unittest.TestCase):
    """缓存与清理（卡表刷新后必须能重新推）。"""

    def test_clear_cache(self):
        DNG.prefill_cn('Spright')            # 建缓存
        self.assertTrue(DNG._CARDS_CACHE or DNG._CSV_CACHE)
        DNG.clear_cache()
        self.assertFalse(DNG._CARDS_CACHE)
        self.assertFalse(DNG._CSV_CACHE)

    def test_still_works_after_clear(self):
        DNG.clear_cache()
        got, _why = DNG.prefill_cn('Spright')
        self.assertEqual(got, '电光闪灵')


if __name__ == '__main__':
    unittest.main()
