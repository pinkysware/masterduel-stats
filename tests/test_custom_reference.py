# -*- coding: utf-8 -*-
"""自定义参照库（C 阶段一）的回归测试。

**这些测试锁的是「自定义卡组能不能被反推判到」这条链**，
不是识别算法本身（识别要 onnxruntime，不在主仓依赖里）。

四个锁点，全部对应实测踩过的坑：

1. :func:`test_custom_ranks_first` —— 排序档位。自定义条目没有 tier，
   旧代码 `tier_rank(None)=0`（榜外）会让它**永远沉底**（实测排第 4，
   尽管它的 bayes 比第一名还好）。
2. :func:`test_custom_survives_keep_filter` —— `prepare()` 的 keep 过滤。
   `keep` 只收 `meta_decks` 的叫法，只在 `opp_decks` 的自定义名会被整条滤掉
   （实测 `prepare()` 后剩 `[]`）。
3. :func:`test_custom_prefills` —— `PRE_FILL_TIERS` 是 Tier1-3 的老口径，
   自定义条目没有 tier 会「排第一但不预填」，界面不标「建议」。
4. :func:`test_match_ref_key_prefers_own_table` —— `SHARED_REFERENCE`
   抢在精确键前面，自定义卡表建好了也看不见（实测传「狱神耀圣」返回 `Elfnote`）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import deck_guess as dg                                   # noqa: E402

#: 卡 id → 名字组（不走真卡表，测试要快且确定）
GROUPS = {12950: '灰流丽', 20500: '软绵雀精', 22116: '耀圣之花诗',
          22533: '调和之天救龙', 22119: '狱神精', 4531: '山的精灵'}

#: 自定义卡组带的卡（per 全 100 = 单样本建库 / 二值化口径）
OWN_CARDS = (12950, 20500, 22116, 22533, 22119)


def _cards(ids, per=100):
    return {str(cid): {'per': per, 'at': 1, 'aboveThresh': per >= 60}
            for cid in ids}


def _custom(ids=OWN_CARDS, samples=3, name=None):
    #: `custom=True` 是 `_merge_custom()` 在真实链路里打的标记（测试直接建 dict，
    #: 得自己带上，否则测的不是生产路径）。
    d = {'cards': _cards(ids), 'total': samples, 'samples': samples,
         'custom': True, 'tier': None, 'provisional': samples < 5}
    if name:
        d['name_cn'] = name
    return d


def _ref(decks):
    return {'version': 2, 'decks': decks}


class CustomMergeTest(unittest.TestCase):
    """`load_reference` 合并自定义库（只在默认路径上合并）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_ref = dg.DEFAULT_REFERENCE
        self._old_custom = dg.DEFAULT_CUSTOM_REFERENCE
        dg.clear_reference_cache()

    def tearDown(self):
        dg.DEFAULT_REFERENCE = self._old_ref
        dg.DEFAULT_CUSTOM_REFERENCE = self._old_custom
        dg.clear_reference_cache()

    def _write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return p

    def test_custom_merged_into_default_reference(self):
        dg.DEFAULT_REFERENCE = self._write('ref.json', _ref({
            'Elfnote': {'tier': 'Tier2', 'cards': _cards(OWN_CARDS[:3])}}))
        dg.DEFAULT_CUSTOM_REFERENCE = self._write(
            'custom.json', {'狱神耀圣': _custom(name='狱神耀圣')})
        decks = dg.load_reference()['decks']
        self.assertIn('狱神耀圣', decks)
        self.assertIn('Elfnote', decks)
        # 合并进来的条目带 custom 标记，tier 强制清空
        self.assertTrue(decks['狱神耀圣']['custom'])
        self.assertIsNone(decks['狱神耀圣']['tier'])

    def test_explicit_path_does_not_merge(self):
        """显式传 path **不合并** —— 回测 / 测试要确定性，别被磁盘干扰。"""
        p = self._write('ref.json', _ref({'Elfnote': {'tier': 'Tier2',
                                                      'cards': _cards(OWN_CARDS)}}))
        dg.DEFAULT_CUSTOM_REFERENCE = self._write(
            'custom.json', {'狱神耀圣': _custom()})
        self.assertNotIn('狱神耀圣', dg.load_reference(p)['decks'])

    def test_custom_wins_on_name_clash(self):
        dg.DEFAULT_REFERENCE = self._write('ref.json', _ref({
            'Elfnote': {'tier': 'Tier1', 'cards': _cards([12950])}}))
        dg.DEFAULT_CUSTOM_REFERENCE = self._write(
            'custom.json', {'Elfnote': _custom(ids=[20500])})
        card = dg.load_reference()['decks']['Elfnote']
        self.assertIn('20500', card['cards'])       # 自定义的卡表生效
        self.assertTrue(card['custom'])

    def test_missing_custom_file_is_harmless(self):
        dg.DEFAULT_REFERENCE = self._write('ref.json', _ref({
            'Elfnote': {'tier': 'Tier2', 'cards': _cards([12950])}}))
        dg.DEFAULT_CUSTOM_REFERENCE = os.path.join(self.tmp, '没有这个文件.json')
        self.assertEqual(list(dg.load_reference()['decks']), ['Elfnote'])

    def test_broken_custom_file_is_harmless(self):
        dg.DEFAULT_REFERENCE = self._write('ref.json', _ref({
            'Elfnote': {'tier': 'Tier2', 'cards': _cards([12950])}}))
        p = os.path.join(self.tmp, 'broken.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ 这不是 JSON')
        dg.DEFAULT_CUSTOM_REFERENCE = p
        self.assertEqual(list(dg.load_reference()['decks']), ['Elfnote'])


class CustomRankingTest(unittest.TestCase):
    """★ 核心：自定义条目必须排得到第一、且能预填。"""

    def setUp(self):
        self.ref = _ref({
            'Elfnote': {'tier': 'Tier2', 'total': 33, 'cards': _cards(OWN_CARDS)},
            '狱神耀圣': _custom(samples=3, name='狱神耀圣'),
        })
        self.index, self.groups = dg.prepare(self.ref, groups=GROUPS,
                                             keep=set())

    def test_custom_survives_keep_filter(self):
        """`keep` 是空的（= 全过滤）也要留下自定义条目。"""
        self.assertIn('狱神耀圣', self.index)
        self.assertNotIn('Elfnote', self.index)     # 站内条目照旧被滤掉

    def test_custom_ranks_first(self):
        """自定义卡表完全命中时排第一（旧代码：tier=None -> 榜外 -> 沉底）。"""
        cands = dg.guess_deck(list(OWN_CARDS), self.index, self.groups)
        self.assertTrue(cands, '应该有候选')
        self.assertEqual(cands[0]['name'], '狱神耀圣')
        self.assertTrue(cands[0]['custom'])

    def test_custom_ranks_first_even_with_partial_reveal(self):
        """只露出 5 张里的 4 张也要压过同分的站内条目。"""
        cands = dg.guess_deck(list(OWN_CARDS)[:4], self.index, self.groups)
        self.assertEqual(cands[0]['name'], '狱神耀圣')

    def test_custom_prefills(self):
        """排第一还要能**预填** —— 否则界面不标「建议」、CLI 回车不采纳。"""
        cands = dg.guess_deck(list(OWN_CARDS), self.index, self.groups)
        self.assertTrue(cands[0]['in_prefill_set'])
        self.assertTrue(cands[0]['prefill'])

    def test_custom_does_not_steal_other_decks(self):
        """露出**别的**卡组的卡时，自定义条目不该靠档位抢到第一。

        场景要带上一个真正能解释这些卡的站内条目 —— 否则索引里只剩自定义
        条目，它当然是唯一候选（那不是「抢」）。
        """
        foreign_ids = [4531, 40001, 40002, 40003]      # 自定义卡表里一张都没有
        ref = _ref({
            '别的卡组': {'tier': 'Trending', 'total': 20,
                       'cards': _cards(foreign_ids)},
            '狱神耀圣': _custom(samples=3, name='狱神耀圣'),
        })
        #: keep 要让「别的卡组」活下来（它是普通站内条目，会被 keep 过滤）
        index, groups = dg.prepare(ref, groups=GROUPS, keep={'别的卡组'})
        cands = dg.guess_deck(foreign_ids, index, groups)
        self.assertEqual(cands[0]['name'], '别的卡组')

    def test_custom_does_not_get_tier_boost(self):
        """★ 自定义条目**不靠 tier 抬档** —— 这是全量回测推翻过的做法。

        历史（2026-10-21 当天两次翻车，别再试）：
          * 档位「高于全部 tier」：回测 273 局，候选内 110 → **80**
            （39 局被自定义条目抢走 top1，几乎全是命中数打平或更低的局）；
          * 「与 Tier2 同档」：仍只有 114/195。
        根因：自定义库是**整个卡组的并集**，卡表天然更大、覆盖面更宽，
        命中数虚高一点点。实测「耀圣」（94 卡并集）vs 站内
        `Elfnote Kewl Tune`（51 卡）在真身是「耀圣杀手旋律」的 19 局上：
        耀圣命中 14/15、bayes −17.21；KewlTune 15/15、bayes −0.78。
        tier 一抬就把这 19 局全判错。
        """
        self.assertEqual(dg._sort_tier_rank({'custom': True}), 0)
        self.assertEqual(dg._sort_tier_rank({'custom': True, 'tier': 'Tier1'}),
                         dg.TIER_RANK['Tier1'])

    def test_sort_tier_rank_helper(self):
        """档位就是真实 tier；自定义条目的 tier 是 None → 榜外（rank 0）。"""
        self.assertEqual(dg._sort_tier_rank({'custom': True, 'eff_hits': 8}), 0)
        self.assertEqual(dg._sort_tier_rank({'tier': 'Tier1'}), dg.TIER_RANK['Tier1'])
        self.assertEqual(dg._sort_tier_rank({}), 0)

    def test_low_hit_custom_does_not_win_ranking(self):
        """0 命中的自定义条目**不能**靠档位赢下 top1（实测 11/30 组踩过）。"""
        foreign = [4531, 40001, 40002, 40003]
        ref = _ref({
            '别的卡组': {'tier': 'Trending', 'total': 20,
                       'cards': _cards(foreign)},
            '狱神耀圣': _custom(samples=3, name='狱神耀圣'),
        })
        index, groups = dg.prepare(ref, groups=GROUPS, keep={'别的卡组'})
        cands = dg.guess_deck(foreign, index, groups)
        top = cands[0]
        self.assertEqual(top['name'], '别的卡组')
        custom = next(c for c in cands if c['name'] == '狱神耀圣')
        self.assertEqual(custom['hits'], 0)


class CustomEngineTest(unittest.TestCase):
    """自定义条目**不被 engine 归并吞掉**。"""

    def test_custom_not_folded_into_engine_target(self):
        ref = _ref({
            '狱神耀圣': dict(_custom(name='狱神耀圣'), source_name='Elfnote'),
            'Elfnote Engine': {'tier': 'Tier1', 'cards': _cards([12950]),
                               'source_name': 'Elfnote'},
        })
        orig = dg.engine_kinds
        dg.engine_kinds = lambda: {'Elfnote Engine'}
        try:
            decks = dg._fold_engines(ref['decks'])
        finally:
            dg.engine_kinds = orig
        self.assertIn('狱神耀圣', decks)
        self.assertTrue(decks['狱神耀圣'].get('custom'))


class MatchRefKeyTest(unittest.TestCase):
    """`match_ref_key` 优先用自己的卡表，`SHARED_REFERENCE` 只兜底。"""

    def setUp(self):
        from core.ui_state import match_ref_key
        self.fn = match_ref_key

    def test_own_table_wins_over_shared(self):
        decks = {'Elfnote': {'cards': {}},
                 '狱神耀圣': {'cards': {'12950': {'per': 100}}}}
        # 狱神耀圣 是 SHARED_REFERENCE 里映射到 Elfnote 的名字
        self.assertEqual(self.fn('狱神耀圣', decks), '狱神耀圣')

    def test_shared_still_used_when_no_own_table(self):
        decks = {'Elfnote': {'cards': {}}}
        self.assertEqual(self.fn('狱神耀圣', decks), 'Elfnote')


if __name__ == '__main__':
    unittest.main()
