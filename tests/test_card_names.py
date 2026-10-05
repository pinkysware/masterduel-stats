# -*- coding: utf-8 -*-
"""卡名三语表（`core/card_names.py` + `tools/build_card_names.py`）的单测。

重点盯两件容易错的事：
  1. **连接键是 `cid`（游戏内卡 id），不是 `id`（Konami 密码）** —— 比错了就全空
  2. 缺哪种语言就顺延（请求的 → 中文 → 英文 → 日文），全空返回 None

真实文件 `data/card_names.json` 存在时再顺带验一遍规模（不存在就跳过）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core import card_names as cn                     # noqa: E402
import build_card_names as bcn                        # noqa: E402

REAL_JSON = os.path.join(PROJ, 'data', 'card_names.json')


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='cardnames_')
        cn.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        cn.clear_cache()

    def _write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return p

    def _fixture(self):
        """ygocdb 用 dict（跟真实文件一样）；游戏卡表用 list。"""
        ygo = {
            # 正常：cid = 游戏内卡 id（20500），id = Konami 密码
            'a': {'cid': 20500, 'id': 42141493, 'en_name': 'Mulcharmy Fuwalos',
                  'jp_name': 'マルチャミー・フワロス', 'md_name': 'ygocdb 的中文'},
            # 游戏卡表里没有这个 id，但 ygocdb 有 -> 也要收进来
            'b': {'cid': 99999, 'id': 11111111, 'en_name': 'Only In Ygocdb',
                  'jp_name': 'ユゴクdb', 'cn_name': '只在 ygocdb'},
            # 中文名只能从 sc_name 拿；日文只能从 jp_ruby 拿
            'c': {'cid': 88888, 'id': 22222222, 'en_name': 'Fallback Guy',
                  'sc_name': '兜底中文', 'jp_ruby': 'フォールバック'},
            # ★ 陷阚：id（Konami 密码）故意等于游戏卡表里的另一个卡 id
            'd': {'cid': 77777, 'id': 20500, 'en_name': 'Trap Card',
                  'jp_name': 'トラップ'},
        }
        cards = [{'id': 20500, 'name': '多多迷宝・软绵雀精'},
                 {'id': 77777, 'name': '游戏里的 77777 卡'}]
        return (bcn.read_ygocdb(self._write('ygo.json', ygo)),
                bcn.read_card_data(self._write('cards.json', cards)))

    def _build_file(self):
        ygo, cards = self._fixture()
        names, stats = bcn.build(ygo, cards)
        path = os.path.join(self.d, 'card_names.json')
        bcn.dump(names, path)
        return path, names, stats


class TestBuild(Base):
    def test_joins_by_cid_not_konami_id(self):
        """★ 用 cid 连 —— 拿 id（Konami 密码）连会全错。"""
        _, names, _ = self._build_file()
        self.assertIn(20500, names)
        self.assertEqual(names[20500][0], '多多迷宝・软绵雀精')
        self.assertEqual(names[20500][1], 'Mulcharmy Fuwalos')
        self.assertEqual(names[20500][2], 'マルチャミー・フワロス')
        # 'd' 那条的 id（密码）= 20500，但 cid=77777 —— 绝不该串到 20500 上
        self.assertNotIn(77777, [20500])
        self.assertEqual(names[77777][1], 'Trap Card')

    def test_cn_prefers_game_resource(self):
        _, names, _ = self._build_file()
        self.assertEqual(names[20500][0], '多多迷宝・软绵雀精',
                         '中文名以游戏卡表为准，不用 ygocdb 的 md_name')

    def test_cn_and_jp_fallbacks(self):
        _, names, _ = self._build_file()
        self.assertEqual(names[88888], ['兜底中文', 'Fallback Guy', 'フォールバック'])
        self.assertEqual(names[99999][0], '只在 ygocdb')

    def test_stats(self):
        _, _, stats = self._build_file()
        self.assertEqual(stats['游戏卡表'], 2)
        self.assertEqual(stats['产出条数'], 4)
        self.assertEqual(stats['游戏卡表命中'], 2)
        self.assertEqual(stats['有日文'], 4)


class TestLookup(Base):
    def test_display_and_fallback_chain(self):
        path, _, _ = self._build_file()
        table = cn.load(path)
        self.assertEqual(cn.display(20500, 'jp', table), 'マルチャミー・フワロス')
        self.assertEqual(cn.display(20500, 'en', table), 'Mulcharmy Fuwalos')
        self.assertEqual(cn.display(20500, 'cn', table), '多多迷宝・软绵雀精')
        self.assertIsNone(cn.display(123456, 'jp', table), '查不到就是 None，别编')

    def test_missing_language_falls_back(self):
        """英文缺失时给中文，而不是给空。"""
        path = os.path.join(self.d, 'n.json')
        bcn.dump({1: ['中文名', '', '日本語']}, path)
        table = cn.load(path)
        self.assertEqual(cn.display(1, 'en', table), '中文名')
        self.assertEqual(cn.display_all(1, table), ('中文名', '', '日本語'))

    def test_load_missing_file_is_empty(self):
        self.assertEqual(cn.load(os.path.join(self.d, 'nope.json')), {})
        self.assertIsNone(cn.display(1, 'cn', {}))

    def test_tolerates_plain_dict_shape(self):
        """老形状（id -> 中文名 字符串）也要能读。"""
        path = os.path.join(self.d, 'old.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'20500': '多多迷宝'}, f, ensure_ascii=False)
        table = cn.load(path)
        self.assertEqual(cn.display(20500, 'cn', table), '多多迷宝')
        self.assertEqual(cn.display(20500, 'jp', table), '多多迷宝',
                         '只有中文时，日文请求顺延到中文（别显示成空）')


class TestIsKnown(Base):
    """M4：`is_known` = unknown 判据（卡表过期的信号）。"""

    def test_known_true_unknown_false(self):
        path, _, _ = self._build_file()
        table = cn.load(path)
        self.assertTrue(cn.is_known(20500, table))
        self.assertTrue(cn.is_known(99999, table), '只在 ygocdb 的也算已知')
        self.assertFalse(cn.is_known(123456, table), '表里没有就是 False')

    def test_int_and_str_both_work(self):
        """int（decode 出来的）和 str（JSON 的键）都要认。"""
        path, _, _ = self._build_file()
        table = cn.load(path)
        self.assertTrue(cn.is_known(20500, table))
        self.assertTrue(cn.is_known('20500', table))
        self.assertFalse(cn.is_known('123456', table))
        for bad in ('abc', None, '', [], {}):
            self.assertFalse(cn.is_known(bad, table), bad)

    def test_empty_and_missing_table(self):
        """表空 / 表文件不在 —— 一律 False（宁可提示，不静默）。"""
        self.assertFalse(cn.is_known(20500, {}))
        self.assertFalse(cn.is_known(
            20500, cn.load(os.path.join(self.d, 'nope.json'))))


@unittest.skipUnless(os.path.exists(REAL_JSON), 'data/card_names.json 还没生成')
class TestRealFile(unittest.TestCase):
    def test_real_coverage(self):
        table = cn.load(REAL_JSON)
        self.assertGreater(len(table), 10000, '真实表不该只有这么点')
        jp = sum(1 for v in table.values() if v[2])
        self.assertGreater(jp / len(table), 0.9, '日文覆盖率应 >90%')
        self.assertEqual(cn.display(20500, 'jp', table), 'マルチャミー・フワロス')
        self.assertEqual(cn.display(12950, 'jp', table), '灰流うらら')
        self.assertTrue(cn.is_known(20500, table))
        self.assertFalse(cn.is_known(99999999, table), '8 位假 id 只可能是密码，不是卡 id')


class YgocdbTypeTest(unittest.TestCase):
    """卡类型访问器（`card_type` / `is_extra`）—— 主卡 / 额外的位掩码判据。

    用**临时卡表**（`path` 注入）锁语义；本地有 ygocdb 快照时再抽两张真卡
    （与 `docs/M7卡组页实现计划.md` §2.2 的核准表同源）。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='cardtypes_')
        self.path = self._write({
            '12950': {'id': '14558127', 'en_name': 'Ash Blossom & Joyous Spring',
                      'data': {'type': 4129}},                      # 主卡
            '21600': {'id': '67092055', 'en_name': 'Sky Striker Extra',
                      'data': {'type': 0x4000000 | 0x21}},          # 连接（额外）
            '15994': {'id': '23427604', 'en_name': 'Branded Dragon',
                      'data': {'type': 0x40 | 0x21}},               # 融合（额外）
            '999999999': {'id': '1', 'en_name': 'Mystery', 'data': {}},
        })
        cn.clear_ygocdb_cache()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        cn.clear_ygocdb_cache()

    def _write(self, doc):
        p = os.path.join(self.d, 'cards.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False)
        return p

    def test_main_deck_card(self):
        self.assertEqual(cn.card_type('12950', self.path), 4129)
        self.assertFalse(cn.is_extra('12950', self.path))

    def test_extra_masks(self):
        """融合 0x40 / 连接 0x4000000 都算额外（判据集中在 EXTRA_TYPE_MASK）。"""
        self.assertTrue(cn.is_extra('21600', self.path))
        self.assertTrue(cn.is_extra('15994', self.path))

    def test_missing_type_is_unknown_not_main(self):
        """`data.type` 缺 = 查不到（None）—— is_extra 兜成 False，但两者要分得开。"""
        self.assertIsNone(cn.card_type('999999999', self.path))
        self.assertFalse(cn.is_extra('999999999', self.path))

    def test_unknown_card_and_bad_input(self):
        self.assertIsNone(cn.card_type('123', self.path))
        self.assertIsNone(cn.card_type(None, self.path))
        self.assertFalse(cn.is_extra('oops', self.path))

    def test_missing_file_gives_empty_doc(self):
        self.assertEqual(cn.ygocdb(os.path.join(self.d, 'nope.json')), {})
        self.assertIsNone(cn.card_type('12950', os.path.join(self.d, 'nope.json')))

    @unittest.skipUnless(os.path.exists(cn.YGOCDB_PATH), '本地没有 ygocdb 快照')
    def test_real_table_spot_check(self):
        """真表抽查：灰流丽主卡、试作闪刀姬-天津额外（参照库实测归类）。"""
        self.assertFalse(cn.is_extra('12950'))
        self.assertTrue(cn.is_extra('21600'))


if __name__ == '__main__':
    unittest.main()
