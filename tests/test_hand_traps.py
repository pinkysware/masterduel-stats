# -*- coding: utf-8 -*-
"""手坑清单（`core/hand_traps.py`）的单测。

重点盯两件容易错的事：
  1. **异画 id 不能漏**：4 张有异画（Ash/Belle/Veiler/朔夜时雨），硬编码的 id
     集合必须**等于** `data/card_names.json` 里该中文名的**同名 id 组** ——
     哪天出新异画（第 17 个 id），测试会**变红报警**（旧写法只验手抄那批，会静默漏掉）；
  2. **id 归属**：每张手坑的中文名必须与 `card_names.json` 里那些 id 的名字一致。

⚠️ 异画 id 在 `ygocdb` 卡表里查不到（实测 3891/3892/3416/21224 全字段 None），
所以**只能用 `card_names.json` 验**，不能用 `card_names.card_type` 之类。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import card_names as cn                     # noqa: E402
from core import hand_traps as ht                     # noqa: E402

REAL_JSON = os.path.join(PROJ, 'data', 'card_names.json')


class StructureTest(unittest.TestCase):
    """纯结构断言（不依赖真实卡表）。"""

    def test_twelve_traps(self):
        self.assertEqual(len(ht.TRAPS), 12)

    def test_sixteen_ids(self):
        self.assertEqual(len(ht.ALL_TRAP_IDS), 16)
        self.assertEqual(len(ht.TRAP_BY_ID), 16)

    def test_fourteen_groups(self):
        self.assertEqual(set(ht.TRAP_GROUPS), {'draw', 'lock', 'negate', 'remove'})
        self.assertEqual(len(ht.TRAP_GROUPS['draw']), 3)
        self.assertEqual(len(ht.TRAP_GROUPS['lock']), 1)
        self.assertEqual(len(ht.TRAP_GROUPS['negate']), 5)   # 含朔夜时雨
        self.assertEqual(len(ht.TRAP_GROUPS['remove']), 3)

    def test_mourner_is_negate(self):
        """审查 P2-5：v1 的 negate 组漏了 15022（朔夜时雨）—— 本轮专门补的卡。"""
        self.assertIn('mourner', ht.TRAP_GROUPS['negate'])
        self.assertEqual(ht.group_of('mourner'), 'negate')

    def test_key_of_alt_is_same_key(self):
        """异画 id 与标准 id 反查同一个 key。"""
        self.assertEqual(ht.key_of(12950), 'ash')
        self.assertEqual(ht.key_of(3891), 'ash')        # 灰流丽异画
        self.assertEqual(ht.key_of(8933), 'veiler')
        self.assertEqual(ht.key_of(21224), 'veiler')    # 效果遮蒙者异画
        self.assertEqual(ht.key_of(13587), 'belle')
        self.assertEqual(ht.key_of(3892), 'belle')      # 屋敷童子异画
        self.assertEqual(ht.key_of(15022), 'mourner')
        self.assertEqual(ht.key_of(3416), 'mourner')    # 朔夜时雨异画

    def test_key_of_non_trap(self):
        self.assertIsNone(ht.key_of(12345))
        self.assertIsNone(ht.key_of(None))
        self.assertIsNone(ht.key_of('abc'))

    def test_standard_id(self):
        self.assertEqual(ht.standard_id('ash'), 12950)
        self.assertEqual(ht.standard_id('nibiru'), 14741)
        self.assertIsNone(ht.standard_id('nope'))

    def test_helpers(self):
        self.assertEqual(ht.name_cn('maxx'), '增殖的Z')
        self.assertEqual(ht.group_of('maxx'), 'draw')
        self.assertEqual(ht.groups(), ('draw', 'lock', 'negate', 'remove'))
        self.assertEqual(ht.name_cn('nope'), None)


@unittest.skipUnless(os.path.exists(REAL_JSON), 'real card_names.json absent')
class RealTableTest(unittest.TestCase):
    """接真实卡表：硬编码 id 集合 == 该中文名的同名 id 组（自动发现新异画）。"""

    @classmethod
    def setUpClass(cls):
        table = cn.load(REAL_JSON)
        #: 中文名 -> 全部同名的 id 集合
        by_cn = {}
        for cid, names in table.items():
            cn_name = (names[0] or '').strip()
            if cn_name:
                by_cn.setdefault(cn_name, set()).add(cid)
        cls.by_cn = by_cn

    def test_hardcoded_ids_match_same_name_group(self):
        """★ 升级版防呆（审查 P3-3）：id 集合必须 == `card_names.json` 的同名组。

        这样哪天出新异画（第 17 个 id），测试**变红**而不是静默漏掉。
        """
        for key, d in ht.TRAPS.items():
            want = self.by_cn.get(d['name_cn'])
            self.assertIsNotNone(
                want, '手坑 %s 的中文名 %r 在 card_names.json 里查不到'
                     % (key, d['name_cn']))
            self.assertEqual(
                set(d['ids']), want,
                '手坑 %s（%s）的硬编码 id %s 与卡表同名组 %s 不一致 —— '
                '可能出了新异画，请核对后更新 core/hand_traps.py'
                % (key, d['name_cn'], sorted(d['ids']), sorted(want)))

    def test_names_resolve_in_requested_langs(self):
        """每张手坑的标准 id 在卡表里中/英/日至少能查到名（展示要有字）。"""
        for key, d in ht.TRAPS.items():
            sid = d['ids'][0]
            for lang in cn.LANGS:
                self.assertTrue(cn.display(sid, lang),
                                '%s(%s) 缺 %s 名' % (key, sid, lang))


if __name__ == '__main__':
    unittest.main()
