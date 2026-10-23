# -*- coding: utf-8 -*-
"""cup_dp 单测：decode 纯函数 + FakeMem 整链（结算对 / 常驻值 / 阶段）+ 断链给 None。

锚点与实测结论见 `core/memread/cup_dp.py` 模块头（2026-10-23 实机打通：
`DuelResult.resultInfo.Cup.dp.{before,after,win}` + 常驻 `DuelMenu.Cup.{stage,dp,dlv}`）。
假内存基建同 test_memread_peak_rate（fakemem_obj）。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core.memread import cup_dp                                       # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_boxed_int,           # noqa: E402
                         build_named_typeinfo, build_str_dict)


def build_scene(mem, before=0, after=1000, win=1, stage=2,
                dp=1000, dlv=20, max_dlv=20, with_result=True,
                with_cup=True):
    """搭 s_data：`DuelResult.resultInfo.Cup.dp` 结算对 + `DuelMenu.Cup` 常驻。

    ⚠️ 实测形状（2026-10-23 12:44 那局）：
      结算节点 = {before: 0, after: 1000, win: 1}  ← 全是装箱 Int64
      常驻节点 = {stage: 2, dp: 1000, dlv: 20, max_dlv: 20, …}
    """
    cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x10)])
    sdata_kv = {}
    if with_result:
        dp_kv = {}
        if before is not None:
            dp_kv['before'] = build_boxed_int(mem, before)
        if after is not None:
            dp_kv['after'] = build_boxed_int(mem, after)
        if win is not None:
            dp_kv['win'] = build_boxed_int(mem, win)
        result_info = {'Cup': build_str_dict(
            mem, {'dp': build_str_dict(mem, dp_kv)})}
        # ⚠️ event_id 挂在 **DuelResult 层**（实测 13 / mode 12）
        sdata_kv['DuelResult'] = build_str_dict(
            mem, {'mode': build_boxed_int(mem, 12),
                  'event_id': build_boxed_int(mem, 13),
                  'resultInfo': build_str_dict(mem, result_info)})
    if with_cup:
        cup_kv = {}
        if stage is not None:
            cup_kv['stage'] = build_boxed_int(mem, stage)
        if dp is not None:
            cup_kv['dp'] = build_boxed_int(mem, dp)
        if dlv is not None:
            cup_kv['dlv'] = build_boxed_int(mem, dlv)
        if max_dlv is not None:
            cup_kv['max_dlv'] = build_boxed_int(mem, max_dlv)
        sdata_kv['DuelMenu'] = build_str_dict(
            mem, {'Cup': build_str_dict(mem, cup_kv)})
    sdata = build_str_dict(mem, sdata_kv)
    sb = mem.alloc(0x40)
    mem.put_u64(cw_ti, 0xB8, sb)
    mem.put_u64(sb, 0x10, sdata)
    return ObjProc(mem), cw_ti


class TestDecodePure(unittest.TestCase):
    """纯函数：真值样本 / 越界 / 垃圾输入 / **0 是合法的**。"""

    def test_dp_from_int(self):
        self.assertEqual(cup_dp.decode_dp(1000), 1000)

    def test_dp_zero_is_valid(self):
        """★ DP 0 合法（2 阶段刚开）—— 与巅峰积分的 `0 < rate` 不同。"""
        self.assertEqual(cup_dp.decode_dp(0), 0)

    def test_dp_from_string(self):
        self.assertEqual(cup_dp.decode_dp('1000'), 1000)

    def test_dp_garbage(self):
        self.assertIsNone(cup_dp.decode_dp(None))
        self.assertIsNone(cup_dp.decode_dp('abc'))
        self.assertIsNone(cup_dp.decode_dp(True))     # bool 别当 1

    def test_dp_bounds(self):
        self.assertIsNone(cup_dp.decode_dp(-1))       # 负数不可能
        self.assertIsNone(cup_dp.decode_dp(10 ** 7))  # 超上界

    def test_pair(self):
        got = cup_dp.decode_dp_pair({'before': 0, 'after': 1000, 'win': 1})
        self.assertEqual(got, {'before': 0, 'after': 1000, 'win': 1})

    def test_pair_without_win(self):
        got = cup_dp.decode_dp_pair({'before': 0, 'after': 1000})
        self.assertEqual(got, {'before': 0, 'after': 1000})
        self.assertNotIn('win', got)

    def test_pair_bad_win_dropped(self):
        """win 解不出只是不带这个键，不影响 before/after。"""
        got = cup_dp.decode_dp_pair({'before': 0, 'after': 1000, 'win': 7})
        self.assertEqual(got, {'before': 0, 'after': 1000})

    def test_pair_missing_side(self):
        self.assertIsNone(cup_dp.decode_dp_pair({'before': 0}))
        self.assertIsNone(cup_dp.decode_dp_pair({'after': 1000}))
        self.assertIsNone(cup_dp.decode_dp_pair(None))

    def test_stage(self):
        self.assertEqual(cup_dp.decode_stage(1), 1)
        self.assertEqual(cup_dp.decode_stage(2), 2)
        self.assertEqual(cup_dp.decode_stage('2'), 2)
        self.assertIsNone(cup_dp.decode_stage(0))
        self.assertIsNone(cup_dp.decode_stage(3))
        self.assertIsNone(cup_dp.decode_stage(None))
        self.assertIsNone(cup_dp.decode_stage(True))


class TestReadCupResult(unittest.TestCase):
    """FakeMem 整链：DuelResult -> resultInfo -> Cup -> dp。"""

    def test_full_chain(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem)
        got = cup_dp.read_cup_result(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'before': 0, 'after': 1000, 'win': 1})

    def test_no_duel_result(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_result=False)
        self.assertIsNone(cup_dp.read_cup_result(proc, None, 'tbl',
                                                 cw_ti=cw_ti))

    def test_missing_after(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, after=None)
        self.assertIsNone(cup_dp.read_cup_result(proc, None, 'tbl',
                                                 cw_ti=cw_ti))

    def test_no_typeinfo_returns_none(self):
        mem = FakeMem()
        proc, _cw_ti = build_scene(mem)
        orig = cup_dp.find_clientwork

        def boom(*a, **k):
            raise RuntimeError('定位失败')
        cup_dp.find_clientwork = boom
        try:
            self.assertIsNone(cup_dp.read_cup_result(proc, None, 'tbl'))
        finally:
            cup_dp.find_clientwork = orig


class TestReadCupStage(unittest.TestCase):
    """常驻阶段：`DuelMenu.Cup.stage`。"""

    def test_stage_2(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=2)
        self.assertEqual(cup_dp.read_cup_stage(proc, None, 'tbl', cw_ti=cw_ti), 2)

    def test_stage_1(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=1)
        self.assertEqual(cup_dp.read_cup_stage(proc, None, 'tbl', cw_ti=cw_ti), 1)

    def test_no_cup(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_cup=False)
        self.assertIsNone(cup_dp.read_cup_stage(proc, None, 'tbl', cw_ti=cw_ti))

    def test_bad_stage(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=9)
        self.assertIsNone(cup_dp.read_cup_stage(proc, None, 'tbl', cw_ti=cw_ti))


class TestReadCupScore(unittest.TestCase):
    """常驻值：stage / dp / dlv / max_dlv 一起给。"""

    def test_full(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=2, dp=1000, dlv=20, max_dlv=20)
        got = cup_dp.read_cup_score(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'stage': 2, 'dp': 1000, 'dlv': 20, 'max_dlv': 20})

    def test_only_stage(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=1, dp=None, dlv=None, max_dlv=None)
        got = cup_dp.read_cup_score(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'stage': 1})

    def test_no_cup(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_cup=False)
        self.assertIsNone(cup_dp.read_cup_score(proc, None, 'tbl', cw_ti=cw_ti))

    def test_empty_dict_gives_none(self):
        """什么都没有时给 None（不是空 dict）—— 调用方好判。"""
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, stage=None, dp=None, dlv=None,
                                  max_dlv=None)
        self.assertIsNone(cup_dp.read_cup_score(proc, None, 'tbl', cw_ti=cw_ti))


if __name__ == '__main__':
    unittest.main()
