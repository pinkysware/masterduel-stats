# -*- coding: utf-8 -*-
"""``core.ydk`` + ``core.deck_fingerprint`` 单测。

两块的定位（用户 2026-10-28 需求）：
* ``ydk``：YDK 文本 -> 卡 id 列表（Konami 密码 -> 游戏内 id 换算）
* ``deck_fingerprint``：判断「当前我方卡组是不是用户指定的那副」，**要有容错**
  （用户会替换或增减卡片）

⚠️ **容错是硬要求**（用户明说）：
* 换 15% 以内的卡仍算同一副
* 加/减几张仍算同一副
* 但两副只共享泛用卡的不同卡组必须判成**不同**
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ydk as Y  # noqa: E402
from core.deck_fingerprint import (  # noqa: E402
    CORE_COPIES,
    CORE_MIN,
    JACCARD_MIN,
    MIN_CURRENT_CARDS,
    compare,
    core_ids,
    diff_summary,
)


def build_deck():
    """55 张基准卡组：10 张主轴满编(x3) + 15 张泛用 + 10 张额外。"""
    ids = [str(1000 + i) for i in range(10) for _ in range(3)]
    ids += [str(2000 + i) for i in range(15)]
    ids += [str(3000 + i) for i in range(10)]
    return ids


class ParseYdkTest(unittest.TestCase):
    def test_basic_sections(self):
        text = '#main\n111\n222\n#extra\n333\n!side\n444\n'
        d = Y.parse_ydk(text)
        self.assertEqual(d.main, [111, 222])
        self.assertEqual(d.extra, [333])
        self.assertEqual(d.side, [444])

    def test_duplicates_preserved(self):
        """3 张同名卡 = 3 行 = 3 个 id（份数必须保留）。"""
        d = Y.parse_ydk('#main\n111\n111\n111\n')
        self.assertEqual(d.main, [111, 111, 111])
        self.assertEqual(d.unique_count, 1)
        self.assertEqual(len(d.all_ids), 3)

    def test_comments_skipped(self):
        text = '#created by get-deck.com\n#main\n111\n!something\n222\n'
        d = Y.parse_ydk(text)
        self.assertEqual(d.main, [111, 222])

    def test_no_section_header_defaults_to_main(self):
        """有的导出工具不写 #main —— 全部当主卡组。"""
        d = Y.parse_ydk('111\n222\n')
        self.assertEqual(d.main, [111, 222])

    def test_non_numeric_goes_to_unknown(self):
        d = Y.parse_ydk('#main\n111\nabc\n222\n')
        self.assertEqual(d.main, [111, 222])
        self.assertEqual(d.unknown_lines, ['abc'])

    def test_empty_text(self):
        d = Y.parse_ydk('')
        self.assertEqual(d.all_ids, [])
        self.assertEqual(d.unique_count, 0)

    def test_side_not_in_all_ids(self):
        """side 不进对局卡组 —— 不能算进指纹。"""
        d = Y.parse_ydk('#main\n111\n#extra\n222\n!side\n333\n')
        self.assertEqual(d.all_ids, [111, 222])

    def test_section_case_insensitive(self):
        d = Y.parse_ydk('#MAIN\n111\n#Extra\n222\n')
        self.assertEqual(d.main, [111])
        self.assertEqual(d.extra, [222])

    def test_crlf(self):
        d = Y.parse_ydk('#main\r\n111\r\n222\r\n')
        self.assertEqual(d.main, [111, 222])


class ReadYdkFileTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'd.ydk')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_ok(self):
        open(self.path, 'w', encoding='utf-8').write('#main\n89631139\n')
        deck, err = Y.read_ydk(self.path)
        self.assertIsNotNone(deck)
        self.assertEqual(deck.main, [89631139])
        self.assertEqual(err, '')

    def test_missing_file(self):
        deck, err = Y.read_ydk(os.path.join(self.tmp, 'nope.ydk'))
        self.assertIsNone(deck)
        self.assertIn('不存在', err)

    def test_empty_file(self):
        open(self.path, 'w', encoding='utf-8').write('#main\n')
        deck, err = Y.read_ydk(self.path)
        self.assertIsNone(deck)
        self.assertIn('没有卡', err)


class CodeToCidTest(unittest.TestCase):
    """Konami 密码 -> 游戏内 id（两套编号完全不同，必须换算）。"""

    def test_known_cards(self):
        # 实测锚点（与日志/内存里的 id 一致）
        self.assertEqual(Y.code_to_cid('89631139'), '4007')     # 青眼白龙
        self.assertEqual(Y.code_to_cid('14558127'), '12950')    # 灰流丽
        self.assertEqual(Y.code_to_cid('23434538'), '9455')     # 增殖的G

    def test_unknown_code(self):
        self.assertIsNone(Y.code_to_cid('99999999'))

    def test_to_game_ids_keeps_duplicates(self):
        got = Y.to_game_ids([89631139, 89631139, 14558127])
        self.assertEqual(got, ['4007', '4007', '12950'])

    def test_to_game_ids_drops_unknown_by_default(self):
        got = Y.to_game_ids([89631139, 99999999])
        self.assertEqual(got, ['4007'])

    def test_to_game_ids_keep_unknown(self):
        got = Y.to_game_ids([89631139, 99999999], keep_unknown=True)
        self.assertEqual(got, ['4007', '99999999'])

    def test_unknown_codes(self):
        self.assertEqual(Y.unknown_codes([89631139, 99999999]), ['99999999'])


class CompareTest(unittest.TestCase):
    """★ 容错语义（用户 2026-10-28：会替换或增减卡片）。"""

    def setUp(self):
        self.base = build_deck()

    def test_identical(self):
        m = compare(self.base, self.base)
        self.assertTrue(m.matched)
        self.assertAlmostEqual(m.jaccard, 1.0)
        self.assertEqual(m.only_current, 0)
        self.assertEqual(m.only_fingerprint, 0)

    def test_swap_3_cards_still_matches(self):
        """换 3 张（5%）—— 必须认出来。"""
        cur = self.base[:-3] + ['2999', '2998', '2997']
        m = compare(cur, self.base)
        self.assertTrue(m.matched, m.reason)
        self.assertEqual(m.only_current, 3)
        self.assertEqual(m.only_fingerprint, 3)

    def test_swap_8_cards_still_matches(self):
        """换 8 张（约 15%）—— 必须认出来（这是容错的核心用例）。

        ⚠️ 初版用 Jaccard（并集做分母）时这里是 0.63 会被误拒，
        改成覆盖度（较小集合做分母）后是 0.77。别改回去。
        """
        cur = self.base[:39] + ['29%02d' % i for i in range(8)] + self.base[47:]
        m = compare(cur, self.base)
        self.assertTrue(m.matched, m.reason)
        self.assertGreater(m.jaccard, 0.72)

    def test_add_cards_still_matches(self):
        """用户往卡组里加卡 —— 仍算同一副。"""
        cur = self.base + ['2990', '2991', '2992', '2993', '2994']
        m = compare(cur, self.base)
        self.assertTrue(m.matched, m.reason)
        self.assertEqual(m.only_current, 5)
        self.assertEqual(m.only_fingerprint, 0)

    def test_remove_cards_still_matches(self):
        """用户从卡组里减卡 —— 仍算同一副。"""
        cur = self.base[:-5]
        m = compare(cur, self.base)
        self.assertTrue(m.matched, m.reason)
        self.assertEqual(m.only_fingerprint, 5)

    def test_copies_change_does_not_matter(self):
        """份数变化（3 张改 2 张）不该影响判定 —— 是同一副。"""
        cur = list(self.base)
        # 把一张满编卡减成 1 份
        cur.remove('1000')
        cur.remove('1000')
        m = compare(cur, self.base)
        self.assertTrue(m.matched, m.reason)

    def test_different_deck_rejected(self):
        """只共享泛用卡的不同卡组 —— 必须判成不同。"""
        other = ([str(5000 + i) for i in range(10) for _ in range(3)]
                 + [str(2000 + i) for i in range(15)]
                 + [str(6000 + i) for i in range(10)])
        m = compare(other, self.base)
        self.assertFalse(m.matched)
        self.assertLess(m.jaccard, JACCARD_MIN)
        # 主轴全不中 -> 关键卡保底也会拦
        self.assertLess(m.core_ratio, CORE_MIN)

    def test_too_few_current_cards(self):
        """当前卡组读得太少（内存没读全）-> 不判，避免误判。"""
        m = compare(self.base[:MIN_CURRENT_CARDS - 1], self.base)
        self.assertFalse(m.matched)
        self.assertIn('不足', m.reason)

    def test_empty_fingerprint(self):
        m = compare(self.base, [])
        self.assertFalse(m.matched)
        self.assertIn('YDK 里没有卡', m.reason)

    def test_zero_ids_ignored(self):
        """id 0（读失败的占位）不能参与匹配。"""
        m = compare(self.base + ['0', '0'], self.base)
        self.assertTrue(m.matched)
        self.assertEqual(m.only_current, 0)


class CoreIdsTest(unittest.TestCase):
    def test_core_is_full_copies(self):
        ids = ['1', '1', '1', '2', '2', '3']
        self.assertEqual(core_ids(ids), ['1'])

    def test_no_core_when_no_full_copies(self):
        self.assertEqual(core_ids(['1', '1', '2', '2']), [])

    def test_custom_copies_threshold(self):
        ids = ['1', '1', '2', '2', '3']
        self.assertEqual(core_ids(ids, copies=2), ['1', '2'])


class DiffSummaryTest(unittest.TestCase):
    def test_added_removed(self):
        d = diff_summary(['1', '2', '3'], ['2', '3', '4'])
        self.assertEqual(d['added'], ['1'])
        self.assertEqual(d['removed'], ['4'])


class ConstantsTest(unittest.TestCase):
    def test_thresholds_sane(self):
        self.assertGreater(JACCARD_MIN, 0.5)
        self.assertLess(JACCARD_MIN, 1.0)
        self.assertEqual(CORE_COPIES, 3)
        self.assertGreater(CORE_MIN, 0.0)

    def test_min_current_cards(self):
        self.assertGreaterEqual(MIN_CURRENT_CARDS, 20)


if __name__ == '__main__':
    unittest.main()


class FingerprintStorageTest(unittest.TestCase):
    """指纹落盘/读取/批量匹配（`data/deck_fingerprints/<名字>.json`）。"""

    def setUp(self):
        #: ⚠️ 用 `MD_FINGERPRINT_DIR` 而不是 `MD_DATA_DIR` ——
        #: `paths.DATA_DIR` 是 **import 期定格的常量**，改 `MD_DATA_DIR`
        #: 对已导入的模块无效，测试会写进真 `data/deck_fingerprints/`
        #: 并互相污染（实测踩过：A.json / 我的卡组.json 落进了真库）。
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get('MD_FINGERPRINT_DIR')
        os.environ['MD_FINGERPRINT_DIR'] = self.tmp

    def tearDown(self):
        import shutil
        if self._old is None:
            os.environ.pop('MD_FINGERPRINT_DIR', None)
        else:
            os.environ['MD_FINGERPRINT_DIR'] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load(self):
        from core import deck_fingerprint as fp
        ids = build_deck()
        path = fp.save_fingerprint('我的卡组', ids, source='ydk')
        self.assertTrue(os.path.exists(path))
        got = fp.load_fingerprints()
        self.assertIn('我的卡组', got)
        self.assertEqual(len(got['我的卡组']['ids']), len(ids))

    def test_same_name_overwrites(self):
        """同名重导 = 更新指纹（期望行为）。"""
        from core import deck_fingerprint as fp
        fp.save_fingerprint('A', build_deck())
        fp.save_fingerprint('A', build_deck()[:30])
        got = fp.load_fingerprints()
        self.assertEqual(len(got['A']['ids']), 30)

    def test_unsafe_filename_characters(self):
        """卡组名里的 Windows 非法字符要换掉，否则写盘失败。"""
        from core import deck_fingerprint as fp
        path = fp.save_fingerprint('a/b\c:d*e?f"g<h>i|j', build_deck())
        self.assertTrue(os.path.exists(path))
        self.assertNotIn('..', os.path.basename(path))

    def test_load_missing_dir_is_empty(self):
        from core import deck_fingerprint as fp
        self.assertEqual(fp.load_fingerprints(), {})

    def test_match_any_picks_best(self):
        """多个指纹都命中时，取覆盖率最高的那个。"""
        from core import deck_fingerprint as fp
        base = build_deck()
        fp.save_fingerprint('接近的', base)
        fp.save_fingerprint('较远的', base[:40] + ['8%02d' % i for i in range(15)])
        name, m = fp.match_any(base)
        self.assertEqual(name, '接近的')

    def test_match_any_none_when_no_fingerprints(self):
        from core import deck_fingerprint as fp
        self.assertEqual(fp.match_any(build_deck()), (None, None))

    def test_match_any_rejects_other_deck(self):
        from core import deck_fingerprint as fp
        fp.save_fingerprint('A', build_deck())
        other = [str(5000 + i) for i in range(10) for _ in range(3)]
        other += [str(2000 + i) for i in range(15)]
        other += [str(6000 + i) for i in range(10)]
        self.assertEqual(fp.match_any(other), (None, None))
