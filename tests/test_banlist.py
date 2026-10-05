# -*- coding: utf-8 -*-
"""禁限卡变更史（`tools/fetch_banlist.py` + `Storage` 的三方法）单测。

盯三件事：
  1. `to_status` 为空 = **解除限制**，累计时要把这张卡从当前表里去掉
  2. 同一张卡先禁、后解、再禁 —— 累计结果要跟着变
  3. 整表覆盖是幂等的（重跑不会翻倍、掉出榜单的会清掉）
"""

import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import fetch_banlist as fb                             # noqa: E402
from core.storage import Storage                       # noqa: E402

RAW = [
    {'_id': 'a1', 'announced': '2026-01-01T00:00:00.000Z',
     'date': '2026-01-10T00:00:00.000Z',
     'linkedArticle': {'url': '/news/x/'},
     'changes': [{'card': {'_id': 'c1', 'name': 'Ash Blossom & Joyous Spring'},
                  'to': 'Limited 2'},
                 {'card': {'_id': 'c2', 'name': 'Called by the Grave'},
                  'from': 'Limited 1', 'to': 'Forbidden'}]},
    {'_id': 'a2', 'announced': '2026-02-01T00:00:00.000Z',
     'date': '2026-02-10T00:00:00.000Z',
     'changes': [{'card': {'_id': 'c1', 'name': 'Ash Blossom & Joyous Spring'},
                  'from': 'Limited 2', 'to': None},          # 解除限制
                 {'card': {'_id': 'c3', 'name': 'Branded Fusion'},
                  'to': 'Limited 1'}]},
    {'_id': 'a3', 'announced': '2026-03-01T00:00:00.000Z', 'changes': []},
]


class TestParse(unittest.TestCase):
    def test_rows(self):
        rows = fb.parse(RAW)
        self.assertEqual(len(rows), 4, '没变化的公告不产出任何行')
        first = rows[0]
        self.assertEqual(first['announce_id'], 'a1')
        self.assertEqual(first['card_en'], 'Ash Blossom & Joyous Spring')
        self.assertEqual(first['to_status'], 'Limited 2')
        self.assertIsNone(first['from_status'])
        self.assertEqual(first['effective'], '2026-01-10T00:00:00.000Z')
        self.assertEqual(first['url'], '/news/x/')

    def test_skips_card_without_id(self):
        rows = fb.parse([{'_id': 'z', 'changes': [{'card': {'name': 'X'},
                                                   'to': 'Forbidden'}]}])
        self.assertEqual(rows, [])

    def test_empty_input(self):
        self.assertEqual(fb.parse([]), [])
        self.assertEqual(fb.parse(None), [])


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='banlist_')
        self.st = Storage(os.path.join(self.d, 'masterduel.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class TestStorage(Base):
    def test_replace_is_idempotent(self):
        rows = fb.parse(RAW)
        self.assertEqual(self.st.replace_banlist_changes(rows), (4, 0))
        self.assertEqual(self.st.replace_banlist_changes(rows), (4, 0),
                         '重跑不该翻倍')
        self.assertEqual(len(self.st.list_banlist_changes()), 4)

    def test_replace_prunes_missing(self):
        self.st.replace_banlist_changes(fb.parse(RAW))
        only_first = [r for r in fb.parse(RAW) if r['announce_id'] == 'a1']
        ins, stale = self.st.replace_banlist_changes(only_first)
        self.assertEqual((ins, stale), (2, 2), '第二次公告的两行该被清掉')
        self.assertEqual({r['announce_id'] for r in self.st.list_banlist_changes()},
                         {'a1'})

    def test_list_order_and_limit(self):
        self.st.replace_banlist_changes(fb.parse(RAW))
        got = self.st.list_banlist_changes()
        self.assertEqual([r['announced'][:7] for r in got],
                         ['2026-02', '2026-02', '2026-01', '2026-01'],
                         '按生效时间倒序')
        self.assertEqual(len(self.st.list_banlist_changes(limit=1)), 1)
        self.assertEqual(len(self.st.list_banlist_changes(since='2026-02-01')), 2)
        self.assertEqual(len(self.st.list_banlist_changes(since='2027-01-01')), 0)

    def test_current_folds_unlimited(self):
        """先限制、后解除的卡不该出现在当前表里。"""
        self.st.replace_banlist_changes(fb.parse(RAW))
        now = dict((name, status) for name, status, _ in self.st.banlist_current())
        self.assertEqual(now, {'Called by the Grave': 'Forbidden',
                               'Branded Fusion': 'Limited 1'})
        self.assertNotIn('Ash Blossom & Joyous Spring', now, '解除限制的要消失')

    def test_current_keeps_order_forbidden_first(self):
        self.st.replace_banlist_changes(fb.parse(RAW))
        self.assertEqual(self.st.banlist_current()[0][1], 'Forbidden')

    def test_current_empty(self):
        self.assertEqual(self.st.banlist_current(), [])


class TestStatusLabel(unittest.TestCase):
    """状态的说法（用户 2026-09-12 口径）：

    **中文只有三个词 —— 禁止 / 限制 / 准限制，不带数字**；
    `Limited 2` 是准限制（放 2 张），**不是禁止**。
    """

    def test_cn_terms(self):
        self.assertEqual(fb.status_label('Forbidden', 'cn'), '禁止')
        self.assertEqual(fb.status_label('Limited 1', 'cn'), '限制')
        self.assertEqual(fb.status_label('Limited 2', 'cn'), '准限制')
        self.assertEqual(fb.status_label(None, 'cn'), '解除限制')

    def test_no_digits_in_cn(self):
        """中文里不许出现数字（不要写成「限制1」「准限制2」）。"""
        for value in ('Forbidden', 'Limited 1', 'Limited 2', None):
            label = fb.status_label(value, 'cn')
            self.assertFalse(any(ch.isdigit() for ch in label),
                             '%s 的中文说法不该带数字：%s' % (value, label))

    def test_limited2_is_not_forbidden(self):
        self.assertNotEqual(fb.status_label('Limited 2'),
                            fb.status_label('Forbidden'))

    def test_en_passthrough(self):
        self.assertEqual(fb.status_label('Limited 2', 'en'), 'Limited 2')
        self.assertEqual(fb.status_label(None, 'en'), '解除限制')

    def test_unknown_value_shows_raw(self):
        self.assertEqual(fb.status_label('Semi-Limited', 'cn'), 'Semi-Limited')


class TestNameLookup(unittest.TestCase):
    def setUp(self):
        self.rev = fb.build_reverse({1: ['墓穴指名者', 'Called by the Grave',
                                         '墓穴の指名者']})

    def test_langs(self):
        self.assertEqual(fb.show_name('Called by the Grave', 'cn', self.rev),
                         '墓穴指名者')
        self.assertEqual(fb.show_name('called by the grave', 'jp', self.rev),
                         '墓穴の指名者', '大小写不敏感')
        self.assertEqual(fb.show_name('Called by the Grave', 'en', self.rev),
                         'Called by the Grave')

    def test_unknown_falls_back_to_english(self):
        self.assertEqual(fb.show_name('Some New Card', 'jp', self.rev),
                         'Some New Card', '查不到就显示英文，别显示空')
        self.assertEqual(fb.show_name(None, 'cn', self.rev), '?')


if __name__ == '__main__':
    unittest.main()
