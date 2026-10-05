# -*- coding: utf-8 -*-
"""peak_rate 单测：decode 纯函数 + FakeMem 整链（结算对 / 常驻值）+ 断链给 None。

锚点与实测结论见 `core/memread/peak_rate.py` 模块头（2026-09-28 实机打通：
`DuelResult.resultInfo.RateDuel.rate.{old_rate,new_rate}` 字符串对 + 常驻
`RateDuel.<赛季>.info`）。假内存基建同 test_duel_settings（fakemem_obj）。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tests'))

from core.memread import peak_rate                                   # noqa: E402
from fakemem_obj import (FakeMem, ObjProc, build_boxed_int,          # noqa: E402
                         build_named_typeinfo, build_str_dict,
                         build_string)


def build_scene(mem, old='1507.49', new='1515.62', season=57,
                standing=None, with_result=True):
    """搭 s_data：DuelResult.resultInfo.RateDuel.rate 结算对 + RateDuel 常驻值。

    ``standing``: {'57': {'rate': '1507.49', 'rate_distribution': '50',
                          'con_win': 1}} 形状；None = 不放常驻子树。
    """
    cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x10)])
    sdata_kv = {}
    if with_result:
        rd = {}
        if old is not None:
            rd['old_rate'] = build_string(mem, old)
        if new is not None:
            rd['new_rate'] = build_string(mem, new)
        result_info = {'RateDuel': build_str_dict(mem, {'rate':
                       build_str_dict(mem, rd)})}
        # ⚠️ event_id 挂在 **DuelResult 层**（2026-09-28 watch 实测：
        #    DuelResult 键 = event_id,mode,replayButton,resultInfo）
        node_kv = {'mode': build_boxed_int(mem, 19),
                   'resultInfo': build_str_dict(mem, result_info)}
        if season is not None:
            node_kv['event_id'] = build_boxed_int(mem, season)
        sdata_kv['DuelResult'] = build_str_dict(mem, node_kv)
    if standing:
        seasons = {}
        for sn, info in standing.items():
            kv = {'rate': build_string(mem, info['rate']),
                  'rate_distribution': build_string(
                      mem, str(info.get('rate_distribution', '50')))}
            if info.get('con_win') is not None:
                kv['con_win'] = build_boxed_int(mem, info['con_win'])
            seasons[sn] = build_str_dict(mem, {'info': build_str_dict(mem, kv)})
        sdata_kv['RateDuel'] = build_str_dict(mem, seasons)
    sdata = build_str_dict(mem, sdata_kv)
    sb = mem.alloc(0x40)                     # 静态区；s_data 槽位 = 声明的字段偏移 0x10
    mem.put_u64(cw_ti, 0xB8, sb)
    mem.put_u64(sb, 0x10, sdata)
    return ObjProc(mem), cw_ti


class TestDecodePure(unittest.TestCase):
    """纯函数：真值样本 / 越界 / 垃圾输入 / 边界。"""

    def test_value_from_string(self):
        self.assertEqual(peak_rate.decode_peak_value('1507.49'), 1507.49)

    def test_value_rounds_float32_tail(self):
        # 就算上游改数值类型，round 2 位也把长尾切掉
        self.assertEqual(peak_rate.decode_peak_value(1507.489990234375),
                         1507.49)

    def test_value_garbage(self):
        self.assertIsNone(peak_rate.decode_peak_value('abc'))
        self.assertIsNone(peak_rate.decode_peak_value(None))
        self.assertIsNone(peak_rate.decode_peak_value(True))    # bool 别当 1.0

    def test_value_bounds(self):
        self.assertIsNone(peak_rate.decode_peak_value(0))
        self.assertIsNone(peak_rate.decode_peak_value(-1))
        self.assertIsNone(peak_rate.decode_peak_value(30000))   # 上界不含

    def test_pair(self):
        got = peak_rate.decode_peak_pair({'old_rate': '1507.49',
                                          'new_rate': '1515.62'})
        self.assertEqual(got, {'before': 1507.49, 'after': 1515.62})

    def test_pair_missing_side(self):
        self.assertIsNone(peak_rate.decode_peak_pair({'old_rate': '1507.49'}))
        self.assertIsNone(peak_rate.decode_peak_pair(None))

    def test_season(self):
        self.assertEqual(peak_rate.decode_season(57), 57)
        self.assertEqual(peak_rate.decode_season('57'), 57)
        self.assertIsNone(peak_rate.decode_season(0))
        self.assertIsNone(peak_rate.decode_season(True))


class TestReadPeakResult(unittest.TestCase):
    """FakeMem 整链：DuelResult -> resultInfo -> RateDuel -> rate。"""

    def test_full_chain(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem)
        got = peak_rate.read_peak_result(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'before': 1507.49, 'after': 1515.62,
                               'season': 57})

    def test_no_duel_result(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_result=False)
        self.assertIsNone(peak_rate.read_peak_result(proc, None, 'tbl',
                                                     cw_ti=cw_ti))

    def test_missing_new_rate(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, new=None)
        self.assertIsNone(peak_rate.read_peak_result(proc, None, 'tbl',
                                                     cw_ti=cw_ti))

    def test_bad_values(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, old='垃圾', new='1515.62')
        self.assertIsNone(peak_rate.read_peak_result(proc, None, 'tbl',
                                                     cw_ti=cw_ti))

    def test_season_omitted_if_invalid(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, season=None)
        got = peak_rate.read_peak_result(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'before': 1507.49, 'after': 1515.62})

    def test_no_typeinfo_returns_none(self):
        mem = FakeMem()
        proc, _cw_ti = build_scene(mem)
        orig = peak_rate.find_clientwork
        peak_rate.find_clientwork = lambda *a, **k: []
        try:
            self.assertIsNone(peak_rate.read_peak_result(proc, None, 'tbl'))
        finally:
            peak_rate.find_clientwork = orig


class TestReadStanding(unittest.TestCase):
    """FakeMem 整链：RateDuel -> 数字最大的赛季 -> info。"""

    def test_full_chain(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_result=False, standing={
            '57': {'rate': '1507.49', 'rate_distribution': '50',
                   'con_win': 1}})
        got = peak_rate.read_peak_standing(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got, {'season': 57, 'rate': 1507.49,
                               'distribution': 50, 'con_win': 1})

    def test_picks_max_season(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_result=False, standing={
            '56': {'rate': '1400.00'},
            '57': {'rate': '1507.49'}})
        got = peak_rate.read_peak_standing(proc, None, 'tbl', cw_ti=cw_ti)
        self.assertEqual(got['season'], 57)
        self.assertEqual(got['rate'], 1507.49)

    def test_no_standing(self):
        mem = FakeMem()
        proc, cw_ti = build_scene(mem, with_result=False)
        self.assertIsNone(peak_rate.read_peak_standing(proc, None, 'tbl',
                                                       cw_ti=cw_ti))


if __name__ == '__main__':
    unittest.main()
