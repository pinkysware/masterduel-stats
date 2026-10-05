# -*- coding: utf-8 -*-
"""duel_settings 单测（M6 刀 2）：FakeMem 整链 DuelClient -> initializer ->
duelSettings；断链各环节给 None 不炸。

方向：``NAMES_DIRECTION`` **已定案为 'by-seat'**（2026-09-13，2/2 实机样本），
见 ``test_direction_confirmed_by_seat``。定案前输出的是中性对（None），
当时的「P1-2：方向未定不写库」约束已由实机样本解除。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core.memread import duel_settings                              # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_boxed_int,         # noqa: E402
                         build_named_typeinfo, build_ref_list,
                         build_str_dict, build_string)

INIT_FIELD = '<engineInitializer>k__BackingField'
DS_FIELD = '<duelSettings>k__BackingField'


def build_scene(mem, names=None, game_mode=3, ts=1726000000,
                with_initializer=True, static_slot=0x00):
    """搭 DuelClient.instance -> EngineInitializerByServer -> duelSettings。

    ``names``: None = 不放 name 键；字符串 = 单名；list = List<string>。
    """
    dti = build_named_typeinfo(mem, 'DuelClient', [(INIT_FIELD, 0x10)])
    sb = mem.alloc(0x40)
    mem.put_u64(dti, 0xB8, sb)

    ds_kv = {}
    if names is not None:
        if isinstance(names, str):
            ds_kv['name'] = build_string(mem, names)
        else:
            ds_kv['name'] = build_ref_list(
                mem, [build_string(mem, n) for n in names])
    if game_mode is not None:
        ds_kv['GameMode'] = build_boxed_int(mem, game_mode)
    if ts is not None:
        ds_kv['duel_start_timestamp'] = build_boxed_int(mem, ts)
    ds_kv['rank'] = build_boxed_int(mem, 12345)     # 别的键照常解析
    ds = build_str_dict(mem, ds_kv)

    dc = mem.alloc(0x20)
    mem.put_u64(dc, 0x00, dti)
    if with_initializer:
        iti = build_named_typeinfo(mem, 'EngineInitializerByServer',
                                   [(DS_FIELD, 0x10)])
        init = mem.alloc(0x20)
        mem.put_u64(init, 0x00, iti)
        mem.put_u64(init, 0x10, ds)
        mem.put_u64(dc, 0x10, init)
    mem.put_u64(sb, static_slot, dc)
    return ObjProc(mem), dti


class TestReadDuelSettings(unittest.TestCase):

    def test_full_chain_neutral_pair(self):
        mem = FakeMem()
        proc, dti = build_scene(mem, names=['幻日のヨハネ', '示例玩家'])
        duel_settings.find_typeinfo = lambda *a, **k: [dti]
        got = duel_settings.read_duel_settings(proc, None, 'tbl')
        self.assertEqual(got, {'names': ['幻日のヨハネ', '示例玩家'],
                               'names_direction': 'by-seat',
                               'game_mode': 3,
                               'duel_start_ts': 1726000000})

    def test_direction_confirmed_by_seat(self):
        """方向已定案（2026-09-13，2/2 实机样本）：by-seat = names[i]
        是 player i 的名字；消费规则在 core.duel_record（opp=names[1-seat]）。"""
        self.assertEqual(duel_settings.NAMES_DIRECTION, 'by-seat')
        mem = FakeMem()
        proc, dti = build_scene(mem, names=['A', 'B'])
        duel_settings.find_typeinfo = lambda *a, **k: [dti]
        self.assertEqual(
            duel_settings.read_duel_settings(proc, None, 'tbl')[
                'names_direction'], 'by-seat')

    def test_single_name_string(self):
        mem = FakeMem()
        proc, dti = build_scene(mem, names='独名')
        duel_settings.find_typeinfo = lambda *a, **k: [dti]
        got = duel_settings.read_duel_settings(proc, None, 'tbl')
        self.assertEqual(got['names'], ['独名'])

    def test_not_in_duel_returns_none(self):
        """静态槽位空（主界面/不在对局）-> None。"""
        mem = FakeMem()
        proc, dti = build_scene(mem, names=['A'], static_slot=0x40)
        duel_settings.find_typeinfo = lambda *a, **k: [dti]
        self.assertIsNone(duel_settings.read_duel_settings(proc, None, 'tbl'))

    def test_missing_initializer_returns_none(self):
        mem = FakeMem()
        proc, dti = build_scene(mem, names=['A'], with_initializer=False)
        duel_settings.find_typeinfo = lambda *a, **k: [dti]
        self.assertIsNone(duel_settings.read_duel_settings(proc, None, 'tbl'))

    def test_cached_dc_ti_skips_lookup(self):
        """审查 P2-1：传 dc_ti 就不再 find_typeinfo。"""
        mem = FakeMem()
        proc, dti = build_scene(mem, names=['A', 'B'])

        def _boom(*a, **k):
            raise AssertionError('find_typeinfo 不该被调')

        duel_settings.find_typeinfo = _boom
        got = duel_settings.read_duel_settings(proc, None, 'tbl', dc_ti=dti)
        self.assertEqual(got['names'], ['A', 'B'])

    def test_no_typeinfo_returns_none(self):
        mem = FakeMem()
        proc, _dti = build_scene(mem, names=['A'])
        duel_settings.find_typeinfo = lambda *a, **k: []
        self.assertIsNone(duel_settings.read_duel_settings(proc, None, 'tbl'))


if __name__ == '__main__':
    unittest.main()
