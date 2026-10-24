# -*- coding: utf-8 -*-
"""client_work 单测（M6 刀 2）：FakeMem 对象图整链 —— s_data -> Deck/DeckList
-> 快照；租借局（无逐卡表）读不到返回 None（契约口径：存 null 不阻塞）。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core.memread import client_work                                 # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_boxed_int,          # noqa: E402
                         build_named_typeinfo, build_ref_list,
                         build_str_dict, build_string)


def build_scene(mem, maindeck='123', deck_list_ids=None, name='闪刀姬',
                deck_has_list=True, extra_containers=None):
    """搭 ClientWork.s_data -> Deck/DeckList 的对象图，返回 (proc, cw_ti)。

    ``extra_containers``: {'TDeck': {'maindeck_id': '999'}, 'TDeckList': {}}
    形状的补充容器（测试多容器选择用）。
    """
    cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x08)])
    sb = mem.alloc(0x40)
    mem.put_u64(cw_ti, 0xB8, sb)

    deck_inner = {'maindeck_id': build_string(mem, str(maindeck))}
    if deck_has_list:
        deck_inner['list'] = build_str_dict(
            mem, {str(maindeck): build_str_dict(
                mem, {'name': build_string(mem, name)})})
    deck = build_str_dict(mem, deck_inner)

    dl = build_str_dict(mem, {})
    if deck_list_ids is not None:
        parts = {}
        for part in ('m', 'e'):
            if deck_list_ids.get(part):
                parts[part] = build_str_dict(mem, {
                    'ids': build_ref_list(
                        mem, [build_boxed_int(mem, v)
                              for v in deck_list_ids[part]])})
        dl = build_str_dict(mem, {str(maindeck): build_str_dict(mem, parts)})

    kv = {'Deck': deck, 'DeckList': dl}
    for k, v in (extra_containers or {}).items():
        if k.endswith('List'):
            kv[k] = build_str_dict(mem, v)
        else:
            inner = {}
            for k2, v2 in v.items():
                inner[k2] = build_string(mem, str(v2))
            if 'list' in v:
                inner['list'] = build_str_dict(mem, {})
            kv[k] = build_str_dict(mem, inner)
    sdata = build_str_dict(mem, kv)
    mem.put_u64(sb, 0x08, sdata)
    return ObjProc(mem), cw_ti


def _patch_typeinfo(cw_ti):
    client_work.find_typeinfo = lambda *a, **k: [cw_ti]


class TestRatedeck(unittest.TestCase):
    """read_ratedeck —— 巅峰槽位 `Deck.ratedeck_id`（2026-10-25 实机定性）。"""

    def _build(self, mem, ratedeck='48171157', name='闪刀姬', deck_list=None):
        cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x08)])
        sb = mem.alloc(0x40)
        mem.put_u64(cw_ti, 0xB8, sb)
        deck_inner = {'maindeck_id': build_string(mem, '48171157')}
        if ratedeck is not None:
            deck_inner['ratedeck_id'] = build_string(mem, str(ratedeck))
        if name is not None and ratedeck is not None:
            deck_inner['list'] = build_str_dict(mem, {
                str(ratedeck): build_str_dict(
                    mem, {'name': build_string(mem, name)})})
        kv = {'Deck': build_str_dict(mem, deck_inner)}
        if deck_list:
            parts = {}
            for part, ids in deck_list.items():
                if ids:
                    parts[part] = build_str_dict(mem, {
                        'ids': build_ref_list(
                            mem, [build_boxed_int(mem, v) for v in ids])})
            kv['DeckList'] = build_str_dict(
                mem, {str(ratedeck): build_str_dict(mem, parts)})
        sdata = build_str_dict(mem, kv)
        mem.put_u64(sb, 0x08, sdata)
        return ObjProc(mem), cw_ti

    def test_full_chain(self):
        mem = FakeMem()
        proc, cw_ti = self._build(
            mem, deck_list={'m': [20500, 8933], 'e': [14990]})
        d = client_work.read_ratedeck(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(d['deck_id'], 48171157)
        self.assertEqual(d['deck_name'], '闪刀姬')
        self.assertEqual(d['ids'], [20500, 8933, 14990])
        self.assertEqual(d['container'], 'Ratedeck')

    def test_ratedeck_missing_is_none(self):
        """从没选过巅峰（ratedeck_id 缺）-> None，不回落天梯那份。"""
        mem = FakeMem()
        proc, cw_ti = self._build(mem, ratedeck=None)
        self.assertIsNone(
            client_work.read_ratedeck(proc, None, 'tbl', cw_ti=cw_ti))

    def test_no_card_list_is_none(self):
        """逐卡表没加载（DeckList 无该条）-> None（契约：宁空不编）。"""
        mem = FakeMem()
        proc, cw_ti = self._build(mem, deck_list={})
        self.assertIsNone(
            client_work.read_ratedeck(proc, None, 'tbl', cw_ti=cw_ti))

    def test_name_missing_still_returns_cards(self):
        """名字条目缺 -> deck_name=None 但卡表照回（名字不拦识别）。"""
        mem = FakeMem()
        proc, cw_ti = self._build(mem, name=None,
                                  deck_list={'m': [20500]})
        d = client_work.read_ratedeck(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertIsNone(d['deck_name'])
        self.assertEqual(d['ids'], [20500])


class TestSnapshot(unittest.TestCase):

    def test_full_chain(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(
            mem, maindeck=123,
            deck_list_ids={'m': [20500, 20500, 21433], 'e': [14990]})
        _patch_typeinfo(cw_ti)
        snap = client_work.snapshot_my_deck(proc, None, 'tbl')
        self.assertEqual(snap['deck_id'], 123)
        self.assertEqual(snap['deck_name'], '闪刀姬')
        self.assertEqual(snap['ids'], [20500, 20500, 21433, 14990])

    def test_maindeck_prefers_container_with_own_list(self):
        """maindeck_id 落在「自家清单里有它」的容器最可信（tmp 实测口径）。"""
        mem = FakeMem()
        proc, cw_ti = build_scene(
            mem, maindeck=123, deck_list_ids={'m': [20500]},
            extra_containers={'TDeck': {'maindeck_id': '999'},
                              'TDeckList': {}})
        _patch_typeinfo(cw_ti)
        snap = client_work.snapshot_my_deck(proc, None, 'tbl')
        self.assertEqual(snap['deck_id'], 123)      # 不是 TDeck 的 999
        self.assertEqual(snap['ids'], [20500])

    def test_rental_no_deck_list_returns_none(self):
        """租借局：DeckList 没加载（逐卡表空）-> None（契约：存 null 不阻塞）。"""
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, maindeck=123, deck_list_ids=None)
        _patch_typeinfo(cw_ti)
        self.assertIsNone(client_work.snapshot_my_deck(proc, None, 'tbl'))

    def test_no_maindeck_returns_none(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, maindeck='', deck_has_list=False)
        _patch_typeinfo(cw_ti)
        self.assertIsNone(client_work.snapshot_my_deck(proc, None, 'tbl'))

    def test_cached_typeinfo_skips_lookup(self):
        """审查 P2-1：传 cw_ti 就不再 find_typeinfo（对方桩一调即炸）。"""
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, maindeck=123,
                                  deck_list_ids={'m': [20500]})

        def _boom(*a, **k):
            raise AssertionError('find_typeinfo 不该被调')

        client_work.find_typeinfo = _boom
        snap = client_work.snapshot_my_deck(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(snap['deck_id'], 123)

    def test_garbage_memory_returns_none(self):
        """假内存是空的：一路异常 -> None，绝不炸（常驻每 tick 都在跑）。"""
        mem = FakeMem()
        cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x08)])
        _patch_typeinfo(cw_ti)
        self.assertIsNone(
            client_work.snapshot_my_deck(ObjProc(mem), None, 'tbl'))


class TestPureHelpers(unittest.TestCase):

    def test_flatten_ids_order(self):
        parts = {'m': {'ids': [1, 2]}, 'e': {'ids': [3]},
                 's': {'ids': []}, 't': {'ids': [4]}}
        self.assertEqual(client_work.flatten_ids(parts), [1, 2, 3, 4])

    def test_flatten_none_safe(self):
        self.assertEqual(client_work.flatten_ids(None), [])
        self.assertEqual(client_work.flatten_ids({'m': None}), [])

    def test_choose_selected_prefers_own_list(self):
        containers = {'Deck': {'list': {'123': {'name': 'x'}},
                               'maindeck_id': '123'},
                      'TDeck': {'list': {}, 'maindeck_id': '999'}}
        self.assertEqual(client_work.choose_selected(containers),
                         ('123', 'Deck'))
        self.assertEqual(client_work.choose_selected({}), (None, None))

    def test_choose_selected_fallback(self):
        """都对不上自家清单时，取第一个有 maindeck_id 的（别直接放弃）。"""
        containers = {'Deck': {'list': {}, 'maindeck_id': '777'}}
        self.assertEqual(client_work.choose_selected(containers),
                         ('777', 'Deck'))


if __name__ == '__main__':
    unittest.main()
