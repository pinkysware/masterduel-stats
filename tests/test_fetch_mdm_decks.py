# -*- coding: utf-8 -*-
"""`fetch_mdm_decks.py`（反推参照库）的单测 —— **全部打桩，不联网**。

盯四件事：
  1. cards 接口翻页拼接（`skip` 翻页 + 短页收尾）
  2. deckBreakdown 的 `_id` → `gameId` 转换（认不出/没 per 的卡要丢掉但不炸）
  3. 容错降级：站内查不到 / 没 breakdown / 抓取抛异常 → **跳过并记录**，不进参照库
  4. 查名规则走「显示英文名」优先（`Yummy Engine` → `Mitsurugi Yummy`）
"""

import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import fetch_mdm_decks as fmd                            # noqa: E402
from core.storage import Storage                         # noqa: E402

META = [
    dict(name='Dracotail', kind='deck_type', name_cn='烙印星辰', tier='Tier1',
         name_display=None, power=12.0),
    dict(name='Yummy Engine', kind='deck_type', name_cn='巳剑黯蜜', tier='Tier3',
         name_display='Mitsurugi Yummy', power=4.0),
    dict(name='Branded Elfnote', kind='deck_type', name_cn='烙印耀圣', tier='Tier2',
         name_display=None, power=8.0),
]

CARDS_PAGES = [
    [dict(_id='idA', gameId='20500'), dict(_id='idB', gameId=12345),
     dict(_id='idC', gameId=None)],
    [dict(_id='idD', gameId='999')],
]


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='mdmdecks_')
        self.db = os.path.join(self.d, 'masterduel.db')
        self.st = Storage(self.db)
        self.st.init_schema()
        self.st.replace_meta_decks(META)
        self._api = fmd.fetch_api
        self.calls = []

    def tearDown(self):
        fmd.fetch_api = self._api
        shutil.rmtree(self.d, ignore_errors=True)

    def stub(self, decks):
        """``decks``: {查名: deckBreakdown 或 None}；cards 页固定两页。"""
        pages = list(CARDS_PAGES)

        def fake(params, base=None):
            self.calls.append((base, dict(params)))
            if base == fmd.CARDS_URL:
                return pages.pop(0) if pages else []
            bd = decks.get(params.get('name'))
            return [dict(name=params.get('name'), deckBreakdown=bd)] if bd else []
        fmd.fetch_api = fake


class TestCardsMap(Base):
    def test_paging_and_skip(self):
        self.stub({})
        m = fmd.fetch_cards_map(page=3, delay=0, verbose=False)
        self.assertEqual(m, {'idA': 20500, 'idB': 12345, 'idD': 999},
                         'gameId 为空/缺失的卡要丢，翻页靠 skip')
        skips = [c[1]['skip'] for c in self.calls if c[0] == fmd.CARDS_URL]
        self.assertEqual(skips, [0, 3], '第二页 skip 必须等于上一页实际条数')

    def test_short_page_stops(self):
        self.stub({})
        fmd.fetch_cards_map(page=5, delay=0, verbose=False)
        self.assertEqual(len([c for c in self.calls if c[0] == fmd.CARDS_URL]), 1)


class TestFetchDeck(Base):
    def test_id_to_gameid_and_shapes(self):
        self.stub({'Dracotail': dict(total=25, avgMainSize=40, cards=[
            dict(card='idA', per=100, at=3, aboveThresh=True),
            dict(card='idB', per=20.0, at=1, aboveThresh=False),
            dict(card='idZZZ', per=50, at=1),        # 映射里没有 -> 丢
            dict(card='idD', per=None),              # 没有 per -> 丢
        ])})
        cards = fmd.fetch_cards_map(page=3, delay=0, verbose=False)
        got, err = fmd.fetch_deck('Dracotail', cards)
        self.assertIsNone(err)
        self.assertEqual(got['total'], 25)
        self.assertEqual(got['avgMainSize'], 40)
        self.assertEqual(got['cards'], {
            '20500': {'per': 100, 'at': 3, 'aboveThresh': True},
            '12345': {'per': 20, 'at': 1, 'aboveThresh': False},
        })
        self.assertEqual(got['unmapped_cards'], 2)

    def test_missing_and_empty(self):
        self.stub({'Dracotail': None, 'Yummy Engine': dict(total=5, cards=[])})
        cards = fmd.fetch_cards_map(page=3, delay=0, verbose=False)
        self.assertEqual(fmd.fetch_deck('Dracotail', cards)[1], 'site-not-found')
        self.assertEqual(fmd.fetch_deck('Yummy Engine', cards)[1], 'no-breakdown')
        self.assertEqual(fmd.fetch_deck('X', {})[1], 'site-not-found')


class TestBuildReference(Base):
    def test_query_name_prefers_display(self):
        row = dict(name='Yummy Engine', name_display='Mitsurugi Yummy')
        self.assertEqual(fmd.query_name(row), 'Mitsurugi Yummy')
        row = dict(name='Dracotail', name_display='')
        self.assertEqual(fmd.query_name(row), 'Dracotail')
        self.assertEqual(fmd.query_name(dict(name='X')), 'X')

    def test_skips_are_recorded_not_fatal(self):
        """站内没这项的自定义条目（Branded Elfnote）→ skip，其余照常入参照库。"""
        self.stub({'Dracotail': dict(total=25, avgMainSize=40, cards=[
                      dict(card='idA', per=100, at=3, aboveThresh=True)]),
                   'Mitsurugi Yummy': dict(total=5, avgMainSize=41, cards=[
                      dict(card='idB', per=100, at=3, aboveThresh=True)])})
        p = fmd.build_reference(self.st, delay=0, verbose=False)
        self.assertEqual(sorted(p['decks']), ['Dracotail', 'Yummy Engine'])
        self.assertEqual(p['decks']['Yummy Engine']['source_name'], 'Mitsurugi Yummy')
        self.assertEqual(p['decks']['Dracotail']['tier'], 'Tier1')
        self.assertEqual(p['skipped'], [dict(name='Branded Elfnote',
                                             query='Branded Elfnote',
                                             reason='site-not-found')])
        self.assertEqual(p['version'], fmd.VERSION)
        self.assertTrue(p['fetched_at'])

    def test_fetch_error_degrades_to_skip(self):
        """抓取抛异常不能让整轮挂掉 —— 记成 fetch-error 继续跑下一条。"""
        def boom(params, base=None):
            if base == fmd.CARDS_URL:
                return [dict(_id='idA', gameId='20500')]
            if params.get('name') == 'Dracotail':
                raise RuntimeError('SSL: UNEXPECTED_EOF')
            return [dict(name=params.get('name'),
                         deckBreakdown=dict(total=5, cards=[
                             dict(card='idA', per=100, at=3, aboveThresh=True)]))]
        fmd.fetch_api = boom
        p = fmd.build_reference(self.st, delay=0, verbose=False)
        self.assertEqual(sorted(p['decks']), ['Branded Elfnote', 'Yummy Engine'])
        self.assertEqual(p['skipped'][0]['name'], 'Dracotail')
        self.assertIn('fetch-error', p['skipped'][0]['reason'])

    def test_empty_cards_not_in_reference(self):
        """空卡表不许进参照库（打分侧会除零 / 变幽灵候选）。"""
        self.stub({'Dracotail': dict(total=25, cards=[]),
                   'Mitsurugi Yummy': dict(total=5, cards=[
                       dict(card='idA', per=100, at=3, aboveThresh=True)]),
                   'Branded Elfnote': dict(total=9, cards=[])})
        p = fmd.build_reference(self.st, delay=0, verbose=False)
        self.assertEqual(sorted(p['decks']), ['Yummy Engine'])
        reasons = {s['name']: s['reason'] for s in p['skipped']}
        self.assertEqual(reasons['Dracotail'], 'no-breakdown')
        self.assertEqual(reasons['Branded Elfnote'], 'no-breakdown')


class TestEngineQueryNames(unittest.TestCase):
    """混轴行 → 站上 engine 的候选查名（**纯函数**）。"""

    def test_strips_the_engine_suffix(self):
        """★ 我们叫 `Elfnote Engine`，站上 engine 叫 `Elfnote`。"""
        got = fmd.engine_query_names(dict(name='Elfnote Engine'))
        self.assertEqual(got[0], 'Elfnote')
        self.assertIn('Elfnote Engine', got, '原样也算候选（有的站上就带后缀）')

    def test_includes_aliases(self):
        """覆盖表别名里的叫法也要试（`Branded Elfnote` 的别名 = `Elfnote Engine`）。"""
        got = fmd.engine_query_names(dict(name='Branded Elfnote',
                                         aliases=['Elfnote Engine']))
        self.assertIn('Elfnote', got)

    def test_dedupes_case_insensitively(self):
        got = fmd.engine_query_names(dict(name='Elfnote Engine',
                                          aliases=['ELFNOTE ENGINE']))
        self.assertEqual([g.lower() for g in got].count('elfnote engine'), 1)

    def test_display_name_considered(self):
        got = fmd.engine_query_names(dict(name='Yummy Engine',
                                          name_display='Mitsurugi Yummy',
                                          aliases=[]))
        self.assertIn('Mitsurugi Yummy', got)


class TestCombineEngineDecks(unittest.TestCase):
    """合并算法 = 站上「Core Engine Cards」的 `NN%avg`（**纯函数**）。"""

    def test_averages_only_over_decks_that_have_the_card(self):
        """★ 口径：**在出现的卡组上取平均**。

        分母若用「全部相关卡组」，只在 1 副里 100% 的卡会被压成 25% ——
        与站上对不上（实测）。这里锁死：100 和 40 → (100+40)/2 = 70，不是 50。
        """
        cards = fmd.combine_engine_decks([
            {'a': {'per': 100, 'at': 3}},
            {'a': {'per': 40, 'at': 1}, 'b': {'per': 80, 'at': 2}},
        ])
        self.assertEqual(cards['a']['per'], 70)
        self.assertEqual(cards['b']['per'], 80, '只在一副里出现就用它自己的值')

    def test_at_is_the_max(self):
        cards = fmd.combine_engine_decks([{'a': {'per': 50, 'at': 1}},
                                          {'a': {'per': 50, 'at': 3}}])
        self.assertEqual(cards['a']['at'], 3)

    def test_accepts_bare_numbers(self):
        """也认 ``{id: per}`` 这种简化形状（别把调用方锁死）。"""
        cards = fmd.combine_engine_decks([{'a': 100}, {'a': 50}])
        self.assertEqual(cards['a']['per'], 75)

    def test_empty_is_empty(self):
        self.assertEqual(fmd.combine_engine_decks([]), {})
        self.assertEqual(fmd.combine_engine_decks([{}, {}]), {})

    def test_rounds_to_int(self):
        cards = fmd.combine_engine_decks([{'a': 100}, {'a': 95}])
        self.assertIsInstance(cards['a']['per'], int)
        self.assertEqual(cards['a']['per'], 98)          # 97.5 -> 偶数舍入成 98


class TestFetchEngine(Base):
    """`fetch_engine`：查 engine → 拉相关卡组 → 合并（全打桩，不联网）。"""

    def _stub(self, engines, decks):
        pages = list(CARDS_PAGES)

        def fake(params, base=None):
            if base == fmd.CARDS_URL:
                return pages.pop(0) if pages else []
            if base == fmd.ENGINES_URL:
                #: ⚠️ 用 `in` 判存在，别用真值 —— 「有这条但相关卡组为空」
                #: 与「压根没这条」是**两种**结果（engine-no-decks / not-found）
                if params.get('name') not in engines:
                    return []
                hit = engines[params.get('name')]
                return [dict(name=params.get('name'),
                             relatedDeckTypes=[{'name': n} for n in hit],
                             requiredCards=[])]
            bd = decks.get(params.get('name'))
            return [dict(name=params.get('name'), deckBreakdown=bd)] if bd else []
        fmd.fetch_api = fake

    def test_merges_related_decks(self):
        self._stub({'Yummy': ['Yummy', 'Mitsurugi Yummy']},
                   {'Yummy': {'total': 16, 'avgMainSize': 40,
                              'cards': [{'card': 'idA', 'per': 100, 'at': 3}]},
                    'Mitsurugi Yummy': {'total': 5, 'avgMainSize': 40,
                                        'cards': [{'card': 'idA', 'per': 60, 'at': 2},
                                                  {'card': 'idB', 'per': 90, 'at': 1}]}})
        cm = fmd.fetch_cards_map(verbose=False)
        got, err = fmd.fetch_engine(['Yummy'], cm, delay=0)
        self.assertIsNone(err)
        self.assertEqual(len(got['cards']), 2)
        self.assertEqual(got['cards']['20500']['per'], 80)     # idA -> 20500
        self.assertEqual(got['engine_of'], ['Yummy', 'Mitsurugi Yummy'])
        self.assertEqual(got['total'], 21)                     # 16 + 5

    def test_engine_not_found(self):
        self._stub({'Nope': ['X']}, {})
        cm = fmd.fetch_cards_map(verbose=False)
        got, err = fmd.fetch_engine(['Yummy'], cm, delay=0)
        self.assertIsNone(got)
        self.assertEqual(err, 'engine-not-found')

    def test_engine_without_decks(self):
        self._stub({'Yummy': []}, {})
        cm = fmd.fetch_cards_map(verbose=False)
        got, err = fmd.fetch_engine(['Yummy'], cm, delay=0)
        self.assertIsNone(got)
        self.assertEqual(err, 'engine-no-decks')

    def test_child_fetch_failure_still_returns_other_decks(self):
        """相关卡组里有一副拉不到，**其余照常合并**（不许整体失败）。"""
        self._stub({'Yummy': ['Yummy', 'Broken']},
                   {'Yummy': {'total': 16, 'avgMainSize': 40,
                              'cards': [{'card': 'idA', 'per': 100, 'at': 3}]}})
        cm = fmd.fetch_cards_map(verbose=False)
        got, err = fmd.fetch_engine(['Yummy'], cm, delay=0)
        self.assertIsNone(err)
        self.assertEqual(got['cards']['20500']['per'], 100)
        self.assertEqual(got['engine_of'], ['Yummy', 'Broken'])


class TestBuildReferenceIncludesEngines(Base):
    """★ 混轴行（`hidden=1, kind='engine'`）**必须进参照库**。"""

    def test_hidden_engine_rows_are_fetched(self):
        self.st.replace_meta_decks(META + [
            dict(name='Elfnote Engine', kind='engine', hidden=1,
                 name_cn='耀圣混轴', tier='Tier2', power=5.0),
            dict(name='Hidden Manual', kind='deck_type', hidden=1,
                 name_cn='手藏的', tier=None),
        ])
        pages = list(CARDS_PAGES)

        def fake(params, base=None):
            if base == fmd.CARDS_URL:
                return pages.pop(0) if pages else []
            if base == fmd.ENGINES_URL:
                return ([dict(name='Elfnote', relatedDeckTypes=[{'name': 'Elfnote'}],
                              requiredCards=[])]
                        if params.get('name') == 'Elfnote' else [])
            if params.get('name') == 'Elfnote':
                return [dict(name='Elfnote', deckBreakdown={
                    'total': 8, 'avgMainSize': 40,
                    'cards': [{'card': 'idA', 'per': 100}]})]
            return []
        fmd.fetch_api = fake
        payload = fmd.build_reference(self.st, delay=0, verbose=False)
        names = payload['decks']
        self.assertIn('Elfnote Engine', names, '混轴行要进参照库')
        self.assertNotIn('Hidden Manual', names, '手工隐藏的照旧不进')
        self.assertEqual(names['Elfnote Engine']['engine_of'], ['Elfnote'])


class TestWrite(Base):
    def test_roundtrip_and_idempotent(self):
        path = os.path.join(self.d, 'ref.json')
        p = {'version': fmd.VERSION, 'fetched_at': 'x', 'decks': {'Dracotail': {
            'tier': 'Tier1', 'source_name': 'Dracotail', 'total': 25,
            'avgMainSize': 40, 'cards': {'20500': {'per': 100, 'at': 3}}}},
            'skipped': []}
        fmd.write_reference(p, path)
        with open(path, 'rb') as f:
            first = f.read()
        fmd.write_reference(p, path)
        with open(path, 'rb') as f:
            second = f.read()
        self.assertEqual(first, second, '同输入必须同输出（幂等）')
        self.assertNotIn(b'\r\n', first, '别把 Windows 换行写进去')


class TestCli(Base):
    def test_needs_target(self):
        self.assertEqual(fmd.main([]), 1)

    def test_unknown_deck_name(self):
        self.assertEqual(fmd.main(['--db', self.db, '--deck', '不存在']), 1)


if __name__ == '__main__':
    unittest.main()
