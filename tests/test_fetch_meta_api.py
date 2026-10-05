# -*- coding: utf-8 -*-
"""`fetch_meta_decks.py` 的 **API 分支**单测（不联网，全部打桩）。

盯三件事：
  1. `tournamentPower` -> tier 的切档（Tier1 ≥12 / Tier2 ≥7 / Tier3 ≥3 / 其余 Trending）
  2. 使用率（`popRank`）合并到榜单条目；榜单没有的进 orphan（按口径丢弃）
  3. 接口的怪脾气：**`limit=1` 时返回的是单个对象、不是数组**
"""

import json
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import fetch_meta_decks as fmd                          # noqa: E402


class TestTierOf(unittest.TestCase):
    def test_cuts(self):
        self.assertEqual(fmd.tier_of(12), 'Tier1')
        self.assertEqual(fmd.tier_of(15.5), 'Tier1')
        self.assertEqual(fmd.tier_of(11.5), 'Tier2')
        self.assertEqual(fmd.tier_of(7), 'Tier2')
        self.assertEqual(fmd.tier_of(6.5), 'Tier3')
        self.assertEqual(fmd.tier_of(3), 'Tier3')
        self.assertEqual(fmd.tier_of(2.5), 'Trending')
        self.assertEqual(fmd.tier_of(1), 'Trending')

    def test_none(self):
        self.assertIsNone(fmd.tier_of(None), '没分的（榜单外）不该硬塞一个 tier')


class _StubBase(unittest.TestCase):
    def setUp(self):
        self._api, self._fetch = fmd.fetch_api, fmd.fetch

    def tearDown(self):
        fmd.fetch_api, fmd.fetch = self._api, self._fetch

    def stub_api(self, tier_rows, pop_rows):
        def fake(params, base=None):
            return tier_rows if 'tournamentPower[$gte]' in params else pop_rows
        fmd.fetch_api = fake


class TestFetchFromApi(_StubBase):
    TIER = [dict(name='Dracotail', tournamentPower=12, popRank=7.22),
            dict(name='Kewl Tune', tournamentPower=10, popRank=5.0),
            dict(name='Blue-Eyes', tournamentPower=3, popRank=0)]
    POP = [dict(name='Dracotail', popRank=7.22, tournamentPower=12),
           dict(name='Kewl Tune', popRank=5.0, tournamentPower=10),
           dict(name='Blue-Eyes', popRank=1.48, tournamentPower=3),
           dict(name='Ghostrick', popRank=0.9, tournamentPower=None)]

    def test_rows_shape_and_tier(self):
        self.stub_api(self.TIER, self.POP)
        rows, orphan = fmd.fetch_from_api()
        self.assertEqual([r['name'] for r in rows],
                         ['Dracotail', 'Kewl Tune', 'Blue-Eyes'])
        self.assertEqual([r['tier'] for r in rows], ['Tier1', 'Tier2', 'Tier3'])
        self.assertEqual([r['power'] for r in rows], [12, 10, 3])
        self.assertTrue(all(r['kind'] == 'deck_type' for r in rows),
                        'API 里没有 engine 行 —— 一律 deck_type')
        self.assertTrue(all(r['slug'] for r in rows))

    def test_popularity_merged(self):
        self.stub_api(self.TIER, self.POP)
        rows, _ = fmd.fetch_from_api()
        self.assertEqual([r['popularity'] for r in rows], [7.22, 5.0, 1.48],
                         '使用率榜里的 popRank 要合并进榜单条目')

    def test_popularity_from_tier_row_itself(self):
        """榜单行自己就带 popRank —— 使用率榜里没有的也要留着那个值。"""
        self.stub_api([dict(name='Sky Striker', tournamentPower=5.5, popRank=5.93)],
                      [])
        rows, _ = fmd.fetch_from_api()
        self.assertEqual(rows[0]['popularity'], 5.93)

    def test_zero_poprank_means_no_data(self):
        self.stub_api([dict(name='Magical Musket', tournamentPower=1, popRank=0)],
                      [])
        self.assertIsNone(fmd.fetch_from_api()[0][0]['popularity'],
                          'popRank=0 是「没有数据」，别写成 0%')

    def test_orphan_only_in_popularity(self):
        """只在使用率榜出现、榜单没有的 -> orphan（调用方按口径丢弃）。"""
        self.stub_api(self.TIER, self.POP)
        _, orphan = fmd.fetch_from_api()
        self.assertEqual([o['name'] for o in orphan], ['Ghostrick'])
        self.assertEqual(orphan[0]['popularity'], 0.9)
        self.assertIsNone(orphan[0]['tier'])

    def test_skips_rows_without_name(self):
        self.stub_api([dict(tournamentPower=5), dict(name='', tournamentPower=5),
                       dict(name='Orcust', tournamentPower=2)],
                      [dict(name=None, popRank=1)])
        rows, orphan = fmd.fetch_from_api()
        self.assertEqual([r['name'] for r in rows], ['Orcust'])
        self.assertEqual(orphan, [])


class TestFetchApiResponseShape(_StubBase):
    def _stub_fetch(self, payload):
        body = json.dumps(payload).encode('utf-8')
        fmd.fetch = lambda url, retries=3, timeout=45: (body, 'application/json')

    def test_single_object_when_limit1(self):
        """★ `limit=1` 时接口返回单个对象 —— 不能当成「没有结果」。"""
        self._stub_fetch({'name': 'Dracotail', 'tournamentPower': 12})
        got = fmd.fetch_api({'tournamentPower[$gte]': 1, 'limit': '1'})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['name'], 'Dracotail')

    def test_list_passthrough(self):
        self._stub_fetch([{'name': 'A'}, {'name': 'B'}])
        self.assertEqual(len(fmd.fetch_api({})), 2)

    def test_empty_list(self):
        self._stub_fetch([])
        self.assertEqual(fmd.fetch_api({}), [])


if __name__ == '__main__':
    unittest.main()
