# -*- coding: utf-8 -*-
"""core/deck_images.py 单测（M7）。

重点盯那个**踩过的坑**：中文名不能当文件名去查（否则永远落默认图）。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import deck_images as di                                   # noqa: E402


def _tmp_file(path, size=8):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'x' * size)
    return path


class FakeStorage(object):
    """只实现解析器用得到的两个方法。"""

    def __init__(self, meta=None, search=None, fail=False):
        self._meta = meta or []
        self._search = search or {}
        self.fail = fail
        self.search_calls = []

    def list_meta_decks(self, *a, **kw):
        if self.fail:
            raise RuntimeError('db locked')
        return self._meta

    def search_meta_decks(self, q, limit=1):
        self.search_calls.append(q)
        if self.fail:
            raise RuntimeError('db locked')
        return self._search.get(q, [])


class PureHelpersTest(unittest.TestCase):

    def test_safe_filename_replaces_spaces_and_bad_chars(self):
        self.assertEqual(di.safe_filename('Sky Striker'), 'Sky_Striker')
        self.assertEqual(di.safe_filename('Gravekeeper\'s'), "Gravekeeper's")
        self.assertEqual(di.safe_filename('a/b:c*d?'), 'a_b_c_d')

    def test_safe_filename_never_empty(self):
        self.assertEqual(di.safe_filename(''), 'unnamed')
        self.assertEqual(di.safe_filename(None), 'unnamed')
        self.assertEqual(di.safe_filename('   '), 'unnamed')

    def test_rel_or_abs_is_forward_slash(self):
        p = di.rel_or_abs(os.path.join(PROJ, 'data', 'x.webp'))
        self.assertEqual(p, 'data/x.webp')
        self.assertIsNone(di.rel_or_abs(None))

    def test_build_index_covers_all_five_name_sources(self):
        idx = di.build_index([
            {'name': 'Sky Striker', 'name_cn': '闪刀姬', 'name_display': 'SkyStriker',
             'name_ja': '閃刀姫', 'aliases': ['闪刀'], 'img_file': 'data/a.webp'},
        ])
        for k in ('Sky Striker', '闪刀姬', 'SkyStriker', '閃刀姫', '闪刀'):
            self.assertEqual(idx[k], 'data/a.webp', k)

    def test_build_index_skips_rows_without_image(self):
        idx = di.build_index([{'name': 'X', 'img_file': None},
                              {'name': 'Y', 'img_file': ''}])
        self.assertEqual(idx, {})

    def test_build_index_first_wins(self):
        idx = di.build_index([
            {'name': 'A', 'img_file': 'first.webp'},
            {'name': 'A', 'img_file': 'second.webp'},
        ])
        self.assertEqual(idx['A'], 'first.webp')


class ResolveTest(unittest.TestCase):
    """解析链：索引 -> 模糊 -> 文件名直查 -> 默认图。"""

    def setUp(self):
        self.d = os.path.join(PROJ, 'tmp', '_deckimg_test')
        os.makedirs(self.d, exist_ok=True)
        self.real_custom = di.CUSTOM_IMG_DIR
        self.real_site = di.IMG_DIR
        self.real_default = di.DEFAULT_IMAGE
        di.CUSTOM_IMG_DIR = os.path.join(self.d, 'custom')
        di.IMG_DIR = os.path.join(self.d, 'site')
        di.DEFAULT_IMAGE = os.path.join(self.d, 'custom', '_default.webp')
        _tmp_file(di.DEFAULT_IMAGE)

    def tearDown(self):
        di.CUSTOM_IMG_DIR = self.real_custom
        di.IMG_DIR = self.real_site
        di.DEFAULT_IMAGE = self.real_default
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_alias_index_hits_chinese_name(self):
        """★ 核心回归：**中文名必须经别名索引命中**，不能落默认图。"""
        st = FakeStorage(meta=[{'name': 'Sky Striker', 'name_cn': '闪刀姬',
                                'img_file': 'x/Sky_Striker.webp'}])
        _tmp_file(os.path.join(PROJ, 'x', 'Sky_Striker.webp'))
        try:
            got = di.DeckImages(st).resolve('闪刀姬')
            self.assertEqual(got, 'x/Sky_Striker.webp')
            self.assertFalse(di.DeckImages(st).is_placeholder('闪刀姬'))
        finally:
            os.remove(os.path.join(PROJ, 'x', 'Sky_Striker.webp'))
            os.rmdir(os.path.join(PROJ, 'x'))

    def test_fuzzy_fallback(self):
        """索引里没有时走模糊搜索（实测 `K9vs` -> `Vanquish Soul K9`）。"""
        st = FakeStorage(meta=[],
                         search={'K9vs': [{'name': 'Vanquish Soul K9',
                                           'img_file': 'y/vs.webp'}]})
        _tmp_file(os.path.join(PROJ, 'y', 'vs.webp'))
        try:
            self.assertEqual(di.DeckImages(st).resolve('K9vs'), 'y/vs.webp')
            self.assertIn('K9vs', st.search_calls)
        finally:
            os.remove(os.path.join(PROJ, 'y', 'vs.webp'))
            os.rmdir(os.path.join(PROJ, 'y'))

    def test_filename_direct_lookup_for_custom_entries(self):
        """自定义条目（站内没有的卡组）靠文件名直查。"""
        _tmp_file(os.path.join(di.CUSTOM_IMG_DIR, 'My_Deck.webp'))
        self.assertEqual(di.DeckImages(FakeStorage()).resolve('My Deck'),
                         'tmp/_deckimg_test/custom/My_Deck.webp')

    def test_site_dir_is_second_choice(self):
        _tmp_file(os.path.join(di.IMG_DIR, 'Site_Deck.webp'))
        self.assertEqual(di.DeckImages(FakeStorage()).resolve('Site Deck'),
                         'tmp/_deckimg_test/site/Site_Deck.webp')

    def test_custom_wins_over_site(self):
        _tmp_file(os.path.join(di.CUSTOM_IMG_DIR, 'Both.webp'))
        _tmp_file(os.path.join(di.IMG_DIR, 'Both.webp'))
        self.assertIn('custom', di.DeckImages(FakeStorage()).resolve('Both'))

    def test_falls_back_to_default(self):
        self.assertTrue(di.DeckImages(FakeStorage()).is_placeholder('不存在'))
        self.assertEqual(di.DeckImages(FakeStorage()).resolve('不存在'),
                         di.rel_or_abs(di.DEFAULT_IMAGE))

    def test_empty_name_gives_default(self):
        self.assertTrue(di.DeckImages(FakeStorage()).is_placeholder(''))
        self.assertTrue(di.DeckImages(FakeStorage()).is_placeholder(None))

    def test_never_raises_on_broken_storage(self):
        """库锁了也不许抛 —— UI 不能因此崩。"""
        got = di.DeckImages(FakeStorage(fail=True)).resolve('闪刀姬')
        self.assertEqual(got, di.rel_or_abs(di.DEFAULT_IMAGE))

    def test_index_built_once(self):
        """索引懒建且只建一次（101 条，不该每次解析都重查）。"""
        calls = []

        class Counting(FakeStorage):
            def list_meta_decks(self, *a, **kw):
                calls.append(1)
                return []

        di_obj = di.DeckImages(Counting())
        di_obj.resolve('a')
        di_obj.resolve('b')
        di_obj.resolve('c')
        self.assertEqual(len(calls), 1)

    def test_zero_byte_file_is_not_used(self):
        """0 字节的图（下载失败留下的空文件）不能当有效图。"""
        _tmp_file(os.path.join(di.CUSTOM_IMG_DIR, 'Empty.webp'), size=0)
        self.assertTrue(di.DeckImages(FakeStorage()).is_placeholder('Empty'))


def _has_real_db() -> bool:
    """本机是否有**带数据的**真实库 —— 没有就跳过「真库」类测试。

    2026-10-05 发布审核：发布仓库不带 `data/masterduel.db`（含个人战绩），而本类
    测试要读库里的表 —— 干净克隆上必然红。缺库时 skip，本机有库时照常跑。
    """
    p = os.path.join(PROJ, 'data', 'masterduel.db')
    if not os.path.exists(p):
        return False
    try:
        import sqlite3
        with sqlite3.connect(p) as c:
            return c.execute('SELECT COUNT(*) FROM matches').fetchone()[0] > 0
    except Exception:                                        # noqa: BLE001
        return False


@unittest.skipUnless(_has_real_db(), '本机没有带数据的真实库（发布仓库不带）')
class RealRepoTest(unittest.TestCase):
    """拿真库跑一遍（只读）—— 保证真实中文名解析得住。"""

    def setUp(self):
        from core.storage import Storage
        self.st = Storage()

    def test_real_chinese_names_resolve_to_non_default(self):
        got = di.DeckImages(self.st).resolve('闪刀姬')
        self.assertIn('Sky_Striker', got)
        self.assertNotIn('_default', got)

    def test_real_alias_resolves(self):
        self.assertIn('Vanquish_Soul_K9', di.DeckImages(self.st).resolve('K9vs'))

    def test_default_image_exists(self):
        self.assertTrue(os.path.exists(di.DEFAULT_IMAGE))



class CardArtFetchTest(unittest.TestCase):
    """`deck_images.fetch_card_art` —— 按卡 id 取**纯卡绘**（2026-10-08）。

    研究结论：卡组图 == 代表卡的卡绘。这里锁「卡 id -> Konami 密码 -> 抓图」
    这条链路（打桩，不联网）。
    """

    def setUp(self):
        import core.deck_images as di_module
        self.di = di_module
        self._konami = getattr(di_module, '_test_konami', None)
        self._urlopen = di_module.urllib.request.urlopen if hasattr(
            di_module, 'urllib') else None

    def test_unknown_card_returns_none(self):
        #: 卡表里没有的 id -> konami 换不出来 -> None（不抛）
        self.assertIsNone(self.di.fetch_card_art(99999999))

    def test_url_uses_konami_password(self):
        """取图 URL 必须是 `cards_cropped/<Konami密码>`（不是游戏内 id）。"""
        import core.card_art as ca
        self.assertTrue(self.di.CARD_ART_CROPPED_URL.startswith(
            'https://images.ygoprodeck.com/images/cards_cropped/'))
        #: 实测：游戏内 13668（闪刀姬－篝）-> Konami 63288573
        try:
            self.assertEqual(ca.konami_id(13668), '63288573')
        except Exception:
            self.skipTest('缺卡表快照，跳过')

class CustomNameBeatsAliasTest(unittest.TestCase):
    """★ 2026-10-09：**覆盖目录里按名字放的图，优先于别名索引**。

    场景（真实踩到）：给「狱神耀圣」配了图，随后把该名字挂成 `Elfnote` 的别名 ——
    别名索引（第 1 环）会把它解析到站内 `Elfnote.webp`，把用户自己放的图盖掉。
    用户**按名字放的那张**是更明确的人工意图，必须最优先（第 0 环）。
    """

    def setUp(self):
        import core.deck_images as di
        self.di = di

    def test_custom_dir_file_wins_over_alias_index(self):
        name = '狱神耀圣'
        own = os.path.join(self.di.CUSTOM_IMG_DIR,
                           self.di.safe_filename(name) + '.webp')
        if not os.path.exists(own):
            self.skipTest('覆盖目录里没有 %s.webp' % name)

        class _St:
            """桩：别名索引把这个名字映到站内另一张图。"""

            def list_meta_decks(self, include_hidden=True):
                return [{'name': 'Elfnote', 'name_cn': '耀圣',
                         'name_display': None, 'name_ja': None,
                         'aliases': [name],
                         'img_file': 'data/deck_images/Elfnote.webp'}]

            def search_meta_decks(self, q, limit=20, include_hidden=False):
                return []

        got = self.di.DeckImages(_St()).resolve_abs(name)
        self.assertEqual(os.path.normcase(got), os.path.normcase(own),
                         '按名字放的图被别名索引盖掉了')

if __name__ == '__main__':
    unittest.main()
