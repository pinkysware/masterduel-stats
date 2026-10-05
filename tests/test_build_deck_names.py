# -*- coding: utf-8 -*-
"""卡组日文名推定（`tools/build_deck_names.py`）的单测。

「老办法」= 按站内英文名找卡 → 在这些卡的 `jp_name` 里找字段名。四种典型：

* 前缀型：`キラーチューン・ミクス` → **キラーチューン**
* 结构切割型：`ドラゴンメイドのお召し替え` → **ドラゴンメイド**（切在「の」）
* 后缀型：`アトラの蟲惑魔` → **蟲惑魔**（Traptrix 的字段名在后面）
* 混轴型：`Branded Elfnote` 一张都匹配不到 → **留空**，别瞎猜
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import build_deck_names as bdn                        # noqa: E402
from core import deck_csv                             # noqa: E402

CARDS = [
    ('Kewl Tune Mix', 'キラーチューン・ミクス'),
    ('Kewl Tune Clip', 'キラーチューン・クリップ'),
    ('Kewl Tune RS', 'キラーチューン・レッドシール'),
    ('Dragonmaid Changeover', 'ドラゴンメイドのお召し替え'),
    ('Dragonmaid Tidying', 'ドラゴンメイドのお片付け'),
    ('Dragonmaid Sheou', 'ドラゴンメイド・シュトラール'),
    ('Traptrix Atrax', 'アトラの蟲惑魔'),
    ('Traptrix Myrmeleo', 'トリオンの蟲惑魔'),
    ('Traptrix Nepenthes', 'カズーラの蟲惑魔'),
    ('Enneacraft Alpha', '糾罪巧－A\'－愤怒'),
    ('Enneacraft Beta', '糾罪巧－B\'－恐怖'),
    ('Enneacraft Gamma', '糾罪巧－Γ\'－绝望'),
    ('Branded Fusion', '烙印融合'),
    ('Branded Opening', '烙印開幕'),
    ('Branded Retribution', '烙印断罪'),
    ('Clown Crew Flair', '道化の一座 フレア'),
    ('Clown Crew Diabolo', '道化の一座 ディアボロ'),
    ('Clown Crew Biancaviso', '道化の一座 ホワイトフェイス'),
    ('Clown Crew Rehearsal', '道化の一座『下稽古』'),
]


class TestGuess(unittest.TestCase):
    def test_prefix(self):
        jp, cov, hit, total = bdn.guess('Kewl Tune', CARDS)
        self.assertEqual(jp, 'キラーチューン')
        self.assertEqual((hit, total), (3, 3))
        self.assertEqual(cov, 1.0)

    def test_cut_at_no(self):
        """「の」是结构切割点：ドラゴンメイドのお… → ドラゴンメイド。"""
        jp, cov, _, total = bdn.guess('Dragonmaid', CARDS)
        self.assertEqual(jp, 'ドラゴンメイド')
        self.assertEqual(total, 3)
        self.assertGreater(cov, 0.99)

    def test_suffix_after_no(self):
        """Traptrix 的字段名在「の」后面。"""
        self.assertEqual(bdn.guess('Traptrix', CARDS)[0], '蟲惑魔')

    def test_does_not_take_card_suffix(self):
        """不能把卡名后缀（A'／B'）当字段名 —— 正解是「糾罪巧」。"""
        jp, _, _, _ = bdn.guess('Enneacraft', CARDS)
        self.assertEqual(jp, '糾罪巧')

    def test_space_segment_wins(self):
        """有空格时，空格前那一段才是字段名：道化の一座（别再切在「の」）。"""
        self.assertEqual(bdn.guess('Clown Crew', CARDS)[0], '道化の一座')

    def test_mixed_deck_stays_blank(self):
        """混轴卡组（Branded Elfnote）一张都匹配不到 -> 留空给人填。"""
        jp, cov, hit, total = bdn.guess('Branded Elfnote', CARDS)
        self.assertIsNone(jp)
        self.assertEqual((hit, total), (0, 0))

    def test_too_few_cards_is_not_guessed(self):
        self.assertIsNone(bdn.guess('Branded Bond', CARDS)[0])

    def test_prefixes_rejects_ascii_tail(self):
        self.assertNotIn("糾罪巧－A", bdn.prefixes("糾罪巧－A'－愤怒"))
        self.assertIn('糾罪巧', bdn.prefixes("糾罪巧－A'－愤怒"))


class TestGuessMixed(unittest.TestCase):
    """混轴拆词拼接（用户 2026-09-16 定：推不出缩写就叫法，按字段名填）。"""

    # 混轴测试卡表：Branded（烙印）+ Elfnote（耀聖の～）+ Kewl Tune（キラーチューン）
    MIX = [
        ('Branded Fusion', '烙印融合'),
        ('Branded Opening', '烙印開幕'),
        ('Branded Retribution', '烙印断罪'),
        ('Elfnote Lucina', '耀聖の花詩ルキナ'),
        ('Elfnote Tinia', '耀聖の波詩ディーナ'),
        ('Elfnote Fortuna', '耀聖の月詩フォルトナ'),
        ('Kewl Tune Mix', 'キラーチューン・ミクス'),
        ('Kewl Tune Clip', 'キラーチューン・クリップ'),
        ('Kewl Tune RS', 'キラーチューン・レッドシール'),
    ]

    def test_two_field_mixed(self):
        """两字段拼接：各段推字段名，按英文名顺序相连。"""
        jp, cov, hit, total = bdn.guess_mixed('Branded Kewl Tune', self.MIX)
        self.assertEqual(jp, '烙印キラーチューン')
        self.assertGreaterEqual(cov, bdn.MIN_COV)

    def test_no_cut_for_suffix_field(self):
        """「の」后缀型字段（耀聖の～ -> 耀聖）也能推出来。"""
        jp, _, _, _ = bdn.guess_mixed('Branded Elfnote', self.MIX)
        self.assertEqual(jp, '烙印耀聖')

    def test_known_map_wins(self):
        """覆盖表已有值的段优先（K9=K９ 这种卡表推不出的）；两段都 known 也行。"""
        known = {'Branded': '烙印', 'Kewl Tune': 'キラーチューン'}
        jp, cov, _, _ = bdn.guess_mixed('Branded Kewl Tune', self.MIX, known)
        self.assertEqual(jp, '烙印キラーチューン')
        self.assertEqual(cov, 1.0)

    def test_segment_below_cov_fails_whole(self):
        """一段推不出（<MIN_CARDS 或覆盖率不够）= 整条放弃，宁缺毋滥。"""
        known = {'Branded': '烙印'}
        jp, _, _, _ = bdn.guess_mixed('Branded Dragon', self.MIX, known)
        self.assertIsNone(jp)          # Dragon 段在 MIX 里只有 Dragonmaid? 没有 -> 不成立

    def test_single_field_not_split(self):
        """单字段（整名能直接 guess 出来）不归混轴管 —— guess_mixed 拆不出两段。"""
        jp, _, _, _ = bdn.guess_mixed('Kewl Tune', self.MIX)
        self.assertIsNone(jp)          # 一个词 -> 不是混轴

    def test_dirty_tail_stripped(self):
        """★数字/编号尾巴不是字段的一部分：K９－１７号 -> K９；ヤミー★X -> ヤミー。"""
        self.assertEqual(bdn._clean_field('K９－１７号'), 'K９')
        self.assertEqual(bdn._clean_field('ヤミー★スナッチー'), 'ヤミー')
        self.assertEqual(bdn._clean_field('絢嵐たる'), '絢嵐')

    def test_no_double_count_after_clean(self):
        """★清洗后去重：原始候选「巳剣之尊/巳剣」洗完同串，一张卡只算一次
        （不然覆盖率 >1，实机踩过 17/11）。"""
        cards = [('Mitsurugi A', '巳剣之尊'), ('Mitsurugi B', '巳剣の刃'),
                 ('Mitsurugi C', '巳剣の舞')]
        jp, cov, hit, total = bdn.field_jp('Mitsurugi', cards)
        self.assertEqual(jp, '巳剣')
        self.assertLessEqual(cov, 1.0)


class TestEnsureColumn(unittest.TestCase):
    def test_inserts_jp_column_after_cn(self):
        header = ['站内英文名', '显示英文名', '中文名', '别名', '分组', '隐藏', '备注']
        rows = [['Dracotail', '', '烙印星辰', '星辰', 'Tier1', '', ''],
                ['Branded', '', '烙印', '', 'Tier2', '', '']]
        header, rows, added = bdn.ensure_column(header, rows)
        self.assertTrue(added)
        self.assertEqual(header[3], '日文名')
        self.assertEqual(rows[0], ['Dracotail', '', '烙印星辰', '', '星辰',
                                   'Tier1', '', ''])

    def test_idempotent(self):
        header = list(deck_csv.COLUMNS)
        rows = [['A', '', '中', '日', '', 'Tier1', '', '']]
        header2, rows2, added = bdn.ensure_column(header, rows)
        self.assertFalse(added)
        self.assertEqual(header2, header)
        self.assertEqual(rows2, rows)


if __name__ == '__main__':
    unittest.main()
