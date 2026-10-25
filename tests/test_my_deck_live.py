# -*- coding: utf-8 -*-
"""my_deck_live 单测：三槽位回落顺序 + 优先级（FakeMem 整链）。"""
import io
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tests.test_client_work import (FakeMem, ObjProc, build_named_typeinfo,
                                    build_str_dict, build_string,
                                    build_ref_list, build_boxed_int)
from core.memread import my_deck_live
from core.memread import client_work as cw
from core import my_deck
import tempfile
import shutil


def _build(mem, *, ladder=None, peak=None, cup=None):
    """搭 s_data：天梯 Deck/DeckList + 巅峰 ratedeck/DeckList + CUPDeckList。

    ladder/peak/cup = (deck_name, main_ids, extra_ids)；None = 容器不给。
    巅峰走 Deck.ratedeck_id（与天梯同容器不同字段），卡表共用 DeckList[id]。
    """
    cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x08)])
    sb = mem.alloc(0x40)
    mem.put_u64(cw_ti, 0xB8, sb)

    kv = {}
    dl_entries = {}
    maindeck = None
    ratedeck = None
    if ladder is not None:
        maindeck = '100'
        name, m, e = ladder
        kv['Deck'] = build_str_dict(mem, {
            'maindeck_id': build_string(mem, maindeck),
            'list': build_str_dict(mem, {maindeck: build_str_dict(
                mem, {'name': build_string(mem, name)})})})
        dl_entries[maindeck] = build_str_dict(mem, {
            'm': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])}),
            'e': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})})
    if peak is not None:
        ratedeck = '200'
        name, m, e = peak
        deck_inner = {'ratedeck_id': build_string(mem, ratedeck)}
        if 'Deck' in kv:
            pass    # 同一 Deck 容器加字段
        else:
            kv['Deck'] = build_str_dict(mem, {})
        for k2, v2 in (dict_items := None) or []:
            pass
        # 重新构造带两个字段的 Deck（简单起见：peak 在 ladder 之后重搭）
        name_l = None
        if ladder is not None:
            name_l = ladder[0]
        inner = {}
        if maindeck:
            inner['maindeck_id'] = build_string(mem, maindeck)
        inner['ratedeck_id'] = build_string(mem, ratedeck)
        lst = {}
        if name_l:
            lst[maindeck] = build_str_dict(mem, {'name': build_string(mem, name_l)})
        lst[ratedeck] = build_str_dict(mem, {'name': build_string(mem, name)})
        inner['list'] = build_str_dict(mem, lst)
        kv['Deck'] = build_str_dict(mem, inner)
        dl_entries[ratedeck] = build_str_dict(mem, {
            'm': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])}),
            'e': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})})
    if kv.get('Deck') or dl_entries:
        kv['DeckList'] = build_str_dict(mem, dl_entries)
    if cup is not None:
        name, m, e = cup
        parts = {}
        if m:
            parts['m'] = build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])})
        if e:
            parts['e'] = build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})
        kv['CUPDeckList'] = build_str_dict(mem, parts)
    sdata = build_str_dict(mem, kv)
    mem.put_u64(sb, 0x08, sdata)
    return ObjProc(mem), cw_ti


class TestAnyModeFallback(unittest.TestCase):

    def test_cup_wins_when_present(self):
        """DC 杯卡表有值 = 用户在 DC 杯语境 -> 最具体者胜出。"""
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]),
                             cup=('reg', [20500], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertEqual(snap['container'], 'CUPDeck')
        self.assertEqual(snap['ids'], [20500, 14990])

    def test_peak_when_no_cup(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]),
                             peak=('狱神耀圣', [20500], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        # 顺序 = cupdeck -> ratedeck -> 常规；ratedeck 先于常规命中
        self.assertEqual(snap['container'], 'Ratedeck')
        self.assertEqual(snap['deck_name'], '狱神耀圣')

    def test_ladder_fallback(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertEqual(snap['deck_name'], '闪刀姬')
        self.assertIsNone(snap.get('container'))    # 常规快照无 container（天梯在可信集）


class TestReadMyDeckLive(unittest.TestCase):
    """一步式入口的写回行为（真进程路径打桩不了 —— 只测容器全空时的错误）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.old = my_deck._state_path() if hasattr(my_deck, '_state_path') else None

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_empty_containers_report_nothing(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem)                       # 三个容器全不给
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertIsNone(snap)
        self.assertEqual(my_deck_live.ERR_NOTHING,
                         '没读到卡组 —— 请进一次游戏主界面（或卡组编辑界面）再刷新')


if __name__ == '__main__':
    unittest.main()
