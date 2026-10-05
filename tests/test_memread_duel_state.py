# -*- coding: utf-8 -*-
"""duel_state 单测 —— 假内存上把「Engine -> threadWork -> ced -> duelInfo」
和「DuelClient -> duelHUD -> LP 计数器」两条链搭出来。

数值取自 2026-09-12 的实机校准样本：用户报「第三回合对方战斗阶段」，
读到 ``nTurnNum=2`` / ``nCurrentPhase=3``；那局 LP 4000 → 0 落在
``phase=3`` + ``nCurrentDmgStep=3``（伤害计算）。这里把它固化成回归样本。

假内存工具从 public_cards 的测试里复用（同一个 ``FakeMem`` 约定）。
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from test_memread_public_cards import FakeMem, FakeProc, build_typeinfo  # noqa: E402

from core.memread.duel_state import (  # noqa: E402
    DuelState,
    DuelStateReader,
    PHASE_NAMES,
)


def build_duel_info(mem, turn=2, phase=3, holder=1, movable=3, step=3, dmg=3):
    """duelInfo：注意字段偏移与实机一致（0x10/0x14/0x18/0x1C/0x28/0x2C）。"""
    ti = build_typeinfo(mem, [
        ('nTurnNum', 0x10), ('nCurrentPhase', 0x14), ('bWhichTurnNow', 0x18),
        ('nMovablePhase', 0x1C), ('nCurrentStep', 0x28),
        ('nCurrentDmgStep', 0x2C),
    ])
    obj = mem.alloc(0x40)
    mem.put_u64(obj, 0x00, ti)
    mem.put_i32(obj, 0x10, turn)
    mem.put_i32(obj, 0x14, phase)
    mem.put_i32(obj, 0x18, holder)
    mem.put_i32(obj, 0x1C, movable)
    mem.put_i32(obj, 0x28, step)
    mem.put_i32(obj, 0x2C, dmg)
    return obj, ti


def build_lp_counter(mem, current, before):
    ti = build_typeinfo(mem, [('m_LPCurrent', 0x60), ('m_LPBeforeTrans', 0x58)])
    obj = mem.alloc(0x70)
    mem.put_u64(obj, 0x00, ti)
    mem.put_i32(obj, 0x60, current)
    mem.put_i32(obj, 0x58, before)
    return obj


def build_scene(mem):
    """整条链搭出来，返回一个 dict。字段名全按实机（属性用 k__BackingField）。"""
    di, _di_ti = build_duel_info(mem)

    ced_ti = build_typeinfo(mem, [('duelInfo', 0x10)])
    ced = mem.alloc(0x20)
    mem.put_u64(ced, 0x00, ced_ti)
    mem.put_u64(ced, 0x10, di)

    tw_ti = build_typeinfo(mem, [('currentEngineData', 0x10)])
    tw = mem.alloc(0x20)
    mem.put_u64(tw, 0x00, tw_ti)
    mem.put_u64(tw, 0x10, ced)

    eng_ti = build_typeinfo(mem, [('threadWork', 0x128)])
    eng = mem.alloc(0x140)
    mem.put_u64(eng, 0x00, eng_ti)
    mem.put_u64(eng, 0x128, tw)

    near = build_lp_counter(mem, current=4000, before=8000)
    far = build_lp_counter(mem, current=0, before=4000)

    hud_ti = build_typeinfo(mem, [
        ('<nearLPCounter>k__BackingField', 0x1F8),
        ('<farLPCounter>k__BackingField', 0x200),
    ])
    hud = mem.alloc(0x210)
    mem.put_u64(hud, 0x00, hud_ti)
    mem.put_u64(hud, 0x1F8, near)
    mem.put_u64(hud, 0x200, far)

    dc_ti = build_typeinfo(mem, [('<duelHUD>k__BackingField', 0xC8)])
    dc = mem.alloc(0xE0)
    mem.put_u64(dc, 0x00, dc_ti)
    mem.put_u64(dc, 0xC8, hud)

    return {
        'eng_ti': eng_ti, 'eng': eng, 'tw': tw, 'ced': ced, 'di': di,
        'lp_ti': hud_ti, 'hud': hud, 'near': near, 'far': far,
        'dc_ti': dc_ti, 'dc': dc,
    }


class DuelStateCase(unittest.TestCase):

    def setUp(self):
        self.mem = FakeMem()
        self.proc = FakeProc(self.mem)
        self.s = build_scene(self.mem)
        self.reader = DuelStateReader(self.proc)

    def read(self, **kw):
        return self.reader.read(self.s['eng_ti'], kw.get('eng', self.s['eng']),
                                self.s['dc_ti'], kw.get('dc', self.s['dc']))


class TestTurn(DuelStateCase):

    def test_reads_turn_phase_holder(self):
        st = self.read()
        self.assertEqual(st.turn, 2)
        self.assertEqual(st.phase, 3)
        self.assertEqual(st.turn_holder, 1)
        self.assertEqual(st.step, 3)
        self.assertEqual(st.dmg_step, 3)
        self.assertEqual(st.movable_phase, 3)

    def test_turn_number_is_one_based(self):
        """nTurnNum 是 0 基的 —— 实机里用户报「第三回合」时它读 2。"""
        self.assertEqual(self.read().turn_number, 3)

    def test_phase_name(self):
        self.assertEqual(self.read().phase_name, 'battle')

    def test_read_turn_only_returns_dict(self):
        d = self.reader.read_turn(self.s['eng_ti'], self.s['eng'])
        self.assertEqual(d['turn'], 2)
        self.assertEqual(d['phase'], 3)


class TestLP(DuelStateCase):

    def test_reads_both_sides(self):
        st = self.read()
        self.assertEqual(st.near_lp, 4000)
        self.assertEqual(st.far_lp, 0)

    def test_reads_before_trans(self):
        """m_LPBeforeTrans 给「这次掉血前是多少」，伤害统计要用。"""
        st = self.read()
        self.assertEqual(st.near_lp_before, 8000)
        self.assertEqual(st.far_lp_before, 4000)

    def test_lp_sides_are_positional_not_ownership(self):
        """near/far 只是屏幕方位 —— 读出来的字段名里不许出现 "my"/"opp"。"""
        st = self.read()
        for field in DuelState._fields:
            self.assertNotIn('my_', field)
            self.assertNotIn('opp_', field)
        self.assertTrue(hasattr(st, 'near_lp') and hasattr(st, 'far_lp'))


class TestDegenerate(DuelStateCase):
    """不在对局 / 链路断掉时，必须给 None 而不是抛异常（采样器每秒都在读）。"""

    def test_no_engine_gives_no_turn_info(self):
        st = self.reader.read(self.s['eng_ti'], 0, self.s['dc_ti'], self.s['dc'])
        self.assertIsNone(st.turn)
        self.assertIsNone(st.phase)
        self.assertIsNone(st.turn_holder)
        self.assertIsNone(st.turn_number)

    def test_nothing_at_all(self):
        """两个实例都没了（回到主界面）—— 整帧全空。"""
        st = self.reader.read(self.s['eng_ti'], 0, self.s['dc_ti'], 0)
        for f in DuelState._fields:
            self.assertIsNone(getattr(st, f), f)

    def test_lp_still_read_when_engine_absent(self):
        """实例没了但 DuelClient 还在（收局那一瞬）—— LP 照样读得到。"""
        st = self.reader.read(self.s['eng_ti'], 0, self.s['dc_ti'], self.s['dc'])
        self.assertEqual(st.near_lp, 4000)

    def test_no_duelclient_gives_turn_but_no_lp(self):
        st = self.reader.read(self.s['eng_ti'], self.s['eng'], None, 0)
        self.assertEqual(st.turn, 2)
        self.assertIsNone(st.near_lp)

    def test_broken_chain_at_threadwork(self):
        # ⚠️ 必须用**新建的 reader**：_fields_cache 是按地址缓存的，
        # 而 FakeMem 每次都从同一地址起分配，两个 FakeMem 之间会串缓存。
        # （真实进程里 TypeInfo 地址不会复用，不存在这个问题。）
        mem = FakeMem()
        r = DuelStateReader(FakeProc(mem))
        ti = build_typeinfo(mem, [('threadWork', 0x128)])
        eng = mem.alloc(0x140)
        mem.put_u64(eng, 0x00, ti)          # +0x128 是 0 -> 链路断
        self.assertIsNone(r.read(ti, eng).turn)
        self.assertEqual(r.read_turn(ti, eng), {})

    def test_hud_absent_gives_no_lp(self):
        mem = FakeMem()
        r = DuelStateReader(FakeProc(mem))
        dc_ti = build_typeinfo(mem, [('<duelHUD>k__BackingField', 0xC8)])
        dc = mem.alloc(0xE0)
        mem.put_u64(dc, 0x00, dc_ti)        # +0xC8 是 0
        self.assertEqual(r.read_lp(dc_ti, dc), {})

    def test_turn_number_none_safe(self):
        st = DuelState()
        self.assertIsNone(st.turn_number)
        self.assertIsNone(st.phase_name)


class TestWhitelist(unittest.TestCase):
    """红线设计：偏移只按字段名查、白名单外的名字一概拒绝。"""

    def setUp(self):
        self.mem = FakeMem()
        self.reader = DuelStateReader(FakeProc(self.mem))

    def test_field_outside_whitelist_raises(self):
        ti = build_typeinfo(self.mem, [('somethingElse', 0x10)])
        fields = self.reader._fields_of(ti)
        with self.assertRaises(ValueError):
            self.reader._offset(fields, 'somethingElse',
                                DuelStateReader.DUELINFO_FIELDS)

    def test_missing_field_raises(self):
        """字段表里没有这个名字 —— 宁可不读，也不猜偏移。"""
        ti = build_typeinfo(self.mem, [('nTurnNum', 0x10)])
        fields = self.reader._fields_of(ti)
        with self.assertRaises(ValueError):
            self.reader._offset(fields, 'nCurrentPhase',
                                DuelStateReader.DUELINFO_FIELDS)

    def test_whitelists_do_not_contain_forbidden_names(self):
        """白名单里不该出现禁区字段名（本模块连它的名字都不该有）。"""
        import core.memread.redline as redline
        blob = ' '.join(
            ' '.join(getattr(DuelStateReader, n))
            for n in dir(DuelStateReader) if n.endswith('_FIELDS'))
        for bad in redline.FORBIDDEN_STRUCT_FIELDS:
            self.assertNotIn(bad, blob)


class TestPhaseTable(unittest.TestCase):

    def test_known_phases(self):
        self.assertEqual(PHASE_NAMES[0], 'draw')
        self.assertEqual(PHASE_NAMES[2], 'main1')
        self.assertEqual(PHASE_NAMES[3], 'battle')
        self.assertEqual(PHASE_NAMES[5], 'end')

    def test_unknown_phase_gives_none(self):
        self.assertIsNone(DuelState(phase=99).phase_name)

    def test_to_dict(self):
        d = DuelState(turn=2, phase=3, near_lp=4000).to_dict()
        self.assertEqual(d['turn_number'], 3)
        self.assertEqual(d['phase_name'], 'battle')
        self.assertEqual(d['near_lp'], 4000)
        self.assertIn('far_lp', d)


if __name__ == '__main__':
    unittest.main()
