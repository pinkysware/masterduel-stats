# -*- coding: utf-8 -*-
"""`fetch_meta_decks.py` 的 **API 分支**单测（不联网，全部打桩）。

盯三件事：
  1. `tournamentPower` -> tier 的切档（Tier1 ≥12 / Tier2 ≥7 / Tier3 ≥3 / 其余 Trending）
  2. 使用率（`popRank`）合并到榜单条目；榜单没有的进 orphan（按口径丢弃）
  3. 接口的怪脾气：**`limit=1` 时返回的是单个对象、不是数组**
"""

import json
import tempfile
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



class TestMissingImageRows(unittest.TestCase):
    """`missing_image_rows` —— 「库里哪些站内条目缺图」（2026-10-08 问题 1）。

    判据：`img_file` 空 **或** 那个文件已不在；**自定义条目（source='custom'）跳过**
    （它们的图我们自己放，站内取不到）。
    """

    class _Stub(object):
        def __init__(self, rows):
            self._rows = rows

        def list_meta_decks(self, include_hidden=True):
            return list(self._rows)

    def _rows(self):
        return [
            {'name': 'Branded', 'kind': 'deck_type', 'source': 'masterduelmeta',
             'img_file': 'data/deck_images/Branded.webp'},           # 有图且在
            {'name': 'Bystial', 'kind': 'deck_type', 'source': 'masterduelmeta',
             'img_file': None},                                       # 空 -> 缺
            {'name': 'Phantom Knight Orcust', 'kind': 'deck_type',
             'source': 'masterduelmeta', 'img_file': 'data/deck_images/gone.webp'},
            {'name': '狱神耀圣', 'kind': 'deck_type', 'source': 'custom',
             'img_file': None},                                       # 自定义 -> 跳过
        ]

    def test_picks_empty_and_missing_files_only(self):
        got = fmd.missing_image_rows(self._Stub(self._rows()),
                                     exists=lambda p: 'Branded' in p)
        names = [r['name'] for r in got]
        self.assertEqual(names, ['Bystial', 'Phantom Knight Orcust'])

    def test_custom_entries_are_never_touched(self):
        """自定义条目（source='custom'）不进补图清单 —— 站内没有它们的图。"""
        got = fmd.missing_image_rows(self._Stub(self._rows()),
                                     exists=lambda p: 'Branded' in p)
        self.assertNotIn('狱神耀圣', [r['name'] for r in got])

    def test_all_present_returns_empty(self):
        rows = [{'name': 'Branded', 'kind': 'deck_type',
                 'source': 'masterduelmeta',
                 'img_file': 'data/deck_images/Branded.webp'}]
        self.assertEqual(fmd.missing_image_rows(self._Stub(rows),
                                                exists=lambda p: True), [])

    def test_row_shape_is_feedable_to_download(self):
        """产出的行要能直接喂 `download_images`（要 `name` / `kind`）。"""
        got = fmd.missing_image_rows(self._Stub(self._rows()),
                                     exists=lambda p: False)
        for r in got:
            self.assertIn('name', r)
            self.assertIn('kind', r)



class TestRepresentativeCard(unittest.TestCase):
    """`representative_card_id` —— 卡组 -> 代表卡（游戏内卡 id）。

    两步：`deck-types?name=X` 取 `card`(ObjectId) -> `cards?_id=` 换 `gameId`。
    **全打桩**，不联网。
    """

    def setUp(self):
        self.calls = []
        self._api = fmd.fetch_api
        self._fetch = fmd.fetch

        def fake_api(params, base=None):
            self.calls.append(params)
            if params.get('name') == 'Sky Striker':
                return [{'name': 'Sky Striker', 'card': 'OID1'}]
            return []

        def fake_fetch(url, retries=3, timeout=45):
            self.calls.append(url)
            if 'OID1' in url:
                return (json.dumps([{'name': 'Ace', 'gameId': '13668'}]).encode(),
                        'application/json')
            return (b'[]', 'application/json')

        fmd.fetch_api = fake_api
        fmd.fetch = fake_fetch

    def tearDown(self):
        fmd.fetch_api = self._api
        fmd.fetch = self._fetch

    def test_returns_game_id(self):
        self.assertEqual(fmd.representative_card_id('Sky Striker'), 13668)

    def test_unknown_deck_returns_none(self):
        self.assertIsNone(fmd.representative_card_id('No Such Deck'))

    def test_no_card_field_returns_none(self):
        fmd.fetch_api = lambda params, base=None: [{'name': 'X'}]
        self.assertIsNone(fmd.representative_card_id('X'))

    def test_fetch_failure_is_swallowed(self):
        def boom(params, base=None):
            raise RuntimeError('net down')
        fmd.fetch_api = boom
        self.assertIsNone(fmd.representative_card_id('Sky Striker'))


class TestFetchByRepresentative(unittest.TestCase):
    """`fetch_by_representative` —— 站内图取不到时的兜底（代表卡卡绘）。"""

    def setUp(self):
        from core import deck_images
        self.di = deck_images
        self._rep = fmd.representative_card_id
        self._art = deck_images.fetch_card_art
        self._save = deck_images.save_custom_image
        self._exists = os.path.exists
        self._remove = os.remove
        self.tmp = tempfile.mkdtemp(prefix='rep-fallback-')
        self.art = os.path.join(self.tmp, 'art.png')
        with open(self.art, 'wb') as f:
            f.write(b'x')

    def tearDown(self):
        fmd.representative_card_id = self._rep
        self.di.fetch_card_art = self._art
        self.di.save_custom_image = self._save
        os.path.exists = self._exists
        os.remove = self._remove

    def test_uses_representative_card_art(self):
        fmd.representative_card_id = lambda nm: 13668
        self.di.fetch_card_art = lambda cid: self.art
        self.di.save_custom_image = (
            lambda nm, src: 'data/deck_images_custom/%s.webp' % nm)
        os.path.exists = lambda p: p == self.art
        os.remove = lambda p: None
        rel, why = fmd.fetch_by_representative('狱火机')
        self.assertEqual(rel, 'data/deck_images_custom/狱火机.webp')
        self.assertIn('13668', why)

    def test_no_representative_returns_reason(self):
        fmd.representative_card_id = lambda nm: None
        rel, why = fmd.fetch_by_representative('X')
        self.assertIsNone(rel)
        self.assertIn('拿不到代表卡', why)

    def test_art_failure_returns_reason(self):
        fmd.representative_card_id = lambda nm: 13668
        self.di.fetch_card_art = lambda cid: None
        rel, why = fmd.fetch_by_representative('X')
        self.assertIsNone(rel)
        self.assertIn('取不到', why)


if __name__ == '__main__':
    unittest.main()
