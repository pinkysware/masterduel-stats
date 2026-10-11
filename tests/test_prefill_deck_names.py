# -*- coding: utf-8 -*-
"""`prefill_deck_names.py` 的纯函数单测（不联网，卡表用假数据）。

盯三件事：
  1. `cn_prefixes` —— 分隔符切割 / 脏尾巴剥除 / ASCII 收尾（K9）保留
  2. `field_guess` —— 覆盖率闸门（MIN_CARDS / MIN_HITS / LOOSE_COV）
     与「先清洗再去重」（同一张卡不重复计票）
  3. `prefill_one` —— 中文名取 md 优先、其余字段成别名；推不出留空；
     混轴拆段拼接（有一段不达线整个放弃）
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import prefill_deck_names as pfn                        # noqa: E402


class TestCnPrefixes(unittest.TestCase):
    def test_separator_cut(self):
        #: 「终刻 XIII 超覆－格拉法雷奥」的头段在分隔符前
        got = pfn.cn_prefixes('终刻超覆－格拉法雷奥')
        self.assertIn('终刻超覆', got)
        self.assertIn('终刻', got)

    def test_dirty_tail_stripped(self):
        got = pfn.cn_prefixes('K9－17号 魔弹')
        self.assertTrue(any(p == 'K9' for p in got),
                        'ASCII 收尾的字段（K9）要保留')
        self.assertFalse(any(p.endswith('17') for p in got))

    def test_space_segment(self):
        got = pfn.cn_prefixes('凭依之风 薇茵')
        self.assertIn('凭依之风', got)


def _cards(*triples):
    """(en, md, sc) -> 假卡表（其余字段空）。"""
    return [(en.lower(), {'md_name': md, 'sc_name': sc, 'cn_name': '',
                          'nwbbs_n': '', 'cnocg_n': ''})
            for en, md, sc in triples]


class TestFieldGuess(unittest.TestCase):
    def test_coverage_gate(self):
        cards = _cards(('DoomZ A', '终刻霸龙', ''), ('DoomZ B', '终刻十三', ''),
                       ('DoomZ C', '终刻覆写', ''), ('Other', '别的卡', ''))
        val, cov, hit, total = pfn.field_guess('doomz', cards, 'md_name')
        self.assertEqual((val, hit, total), ('终刻', 3, 3))
        self.assertGreaterEqual(cov, 0.6)

    def test_too_few_cards_is_none(self):
        cards = _cards(('DoomZ A', '终刻霸龙', ''), ('DoomZ B', '终刻十三', ''))
        val, _cov, hit, total = pfn.field_guess('doomz', cards, 'md_name')
        self.assertIsNone(val, '命中卡少于 3 张不下结论')
        #: 与日文版 guess 同口径：不够线时原样报命中数（2, 2）
        self.assertEqual((hit, total), (2, 2))

    def test_dedup_votes(self):
        #: 同一张卡贡献多个候选（终刻/终刻霸龙）—— 每张卡一个字段只算一票，
        #: 不去重的话覆盖率会 >1
        cards = _cards(('Foo A', '终刻霸龙', ''), ('Foo B', '终刻', ''),
                       ('Foo C', '终刻', ''), ('Foo D', '终刻', ''))
        val, cov, hit, total = pfn.field_guess('foo', cards, 'md_name')
        self.assertEqual((val, hit, total), ('终刻', 4, 4))
        self.assertLessEqual(cov, 1.0)

    def test_empty_field_not_counted(self):
        #: md 缺译的卡不进分母（不冤枉这个字段）
        cards = _cards(('Bar A', '', '光道'), ('Bar B', '', '光道'),
                       ('Bar C', '', '光道'))
        val, _cov, hit, total = pfn.field_guess('bar', cards, 'md_name')
        self.assertIsNone(val)
        self.assertEqual(total, 0)
        val, cov, hit, total = pfn.field_guess('bar', cards, 'sc_name')
        self.assertEqual((val, hit, total), ('光道', 3, 3))


class TestPrefillOne(unittest.TestCase):
    def test_md_first_and_aliases(self):
        cards = [
            ('exosister a'.lower(), {'md_name': '驱魔姐妹伊莉丝', 'sc_name': '驱魔姐妹索利娜',
                                     'cn_name': '救祓少女一号', 'nwbbs_n': '救祓少女二号',
                                     'cnocg_n': '驱魔修女甲'}),
            ('exosister b'.lower(), {'md_name': '驱魔姐妹两号', 'sc_name': '驱魔姐妹三号',
                                     'cn_name': '救祓少女三号', 'nwbbs_n': '救祓少女四号',
                                     'cnocg_n': '驱魔修女乙'}),
            ('exosister c'.lower(), {'md_name': '驱魔姐妹四号', 'sc_name': '驱魔姐妹五号',
                                     'cn_name': '救祓少女五号', 'nwbbs_n': '救祓少女六号',
                                     'cnocg_n': '驱魔修女丙'}),
        ]
        cn, aliases, mixed, _detail = pfn.prefill_one('Exosister', cards)
        self.assertEqual(cn, '驱魔姐妹', '中文名取 md（游戏译名）')
        self.assertFalse(mixed)
        self.assertIn('救祓少女', aliases)
        self.assertIn('驱魔修女', aliases)

    def test_no_match_returns_empty(self):
        cards = _cards(('Stun Card A', '', ''), ('Stun Card B', '', ''),
                       ('Stun Card C', '', ''))
        cn, aliases, mixed, detail = pfn.prefill_one('Stun', cards)
        self.assertEqual((cn, aliases, mixed, detail), ('', [], False, []))

    def test_mixed_needs_all_segments(self):
        #: 两段都达线 -> 拼接
        cards = _cards(('Magistus A', '伟魔甲', ''), ('Magistus B', '伟魔乙', ''),
                       ('Magistus C', '伟魔丙', ''),
                       ('Charmer A', '凭依子', ''), ('Charmer B', '凭依丑', ''),
                       ('Charmer C', '凭依寅', ''))
        cn, _aliases, mixed, _detail = pfn.prefill_one('Magistus Charmer', cards)
        self.assertTrue(mixed)
        self.assertEqual(cn, '伟魔凭依')

    def test_mixed_refuses_when_segment_fails(self):
        #: 第二段推不出字段 -> 整个放弃（宁缺毋滥）
        cards = _cards(('Magistus A', '伟魔甲', ''), ('Magistus B', '伟魔乙', ''),
                       ('Magistus C', '伟魔丙', ''),
                       ('Random A', '乱起', ''), ('Random B', '名号', ''),
                       ('Random C', '不齐', ''))
        cn, _aliases, mixed, _detail = pfn.prefill_one('Magistus Random', cards)
        self.assertEqual((cn, mixed), ('', False))


if __name__ == '__main__':
    unittest.main()
