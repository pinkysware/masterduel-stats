# -*- coding: utf-8 -*-
"""duel_collector 巅峰积分编排单测（2026-09-28）：

* 门控 —— 只挂巅峰局（`group(game_mode)=='peak'`），天梯局不读（审查 B2-1）；
* 收局 -> 结算对没出现 -> 挂起重试 -> 出现即落盘 `peak_rate.json` + 补库；
* **链条校验** —— 新对 `before` 必须 = 开局基线的 `after`（防上一局残留）；
* 超时放弃不阻塞；基线缺位时收局后第一对就收。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from tools.duel_collector import DuelCollector, Hooks, _OpenRecord   # noqa: E402

T0 = 1000000.0


class PeakHooks(Hooks):
    """只打桩巅峰相关两个钩子；其余走基类默认（None/空）。"""

    def __init__(self):
        self.peak_result = None
        self.peak_seq = []            # 每次 read_peak_result 弹一个（然后回落）
        self.peak_backfills = []
        self.peak_backfill_result = '巅峰积分入库：x'

    def read_peak_result(self):
        if self.peak_seq:
            return self.peak_seq.pop(0)
        return self.peak_result

    def backfill_peak(self, dir_name):
        self.peak_backfills.append(dir_name)
        return self.peak_backfill_result


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        os.makedirs(self.records)
        self.st = PeakHooks()
        self.lines = []
        self.c = DuelCollector(records_dir=self.records, hooks=self.st,
                               log=self.lines.append)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rec(self, game_mode=19, name='20260928_120000'):
        rec = _OpenRecord(first_seen_at='2026-09-28 12:00:00',
                          game_mode=game_mode)
        rec.dir_path = os.path.join(self.records, name)
        os.makedirs(rec.dir_path, exist_ok=True)
        return rec

    def _peak_json(self, rec):
        path = os.path.join(rec.dir_path, 'peak_rate.json')
        if not os.path.isfile(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)


class TestGating(Base):

    def test_ranked_game_not_probed(self):
        """天梯局不读巅峰积分（B2-1：段位探测全模式跑是历史包袱，积分不背）。"""
        rec = self._rec(game_mode=3)
        lines = self.c._start_peak_probe(T0, rec)
        self.assertEqual(lines, [])
        self.assertIsNone(self.c._peak_probe)
        self.assertEqual(self.st.peak_backfills, [])

    def test_unknown_mode_not_probed(self):
        rec = self._rec(game_mode=None)
        self.c._start_peak_probe(T0, rec)
        self.assertIsNone(self.c._peak_probe)

    def test_no_dir_not_probed(self):
        rec = _OpenRecord(first_seen_at='2026-09-28 12:00:00', game_mode=19)
        rec.dir_path = None
        lines = self.c._start_peak_probe(T0, rec)
        self.assertEqual(lines, [])
        self.assertIsNone(self.c._peak_probe)


class TestSettle(Base):

    def test_immediate_settle(self):
        """收局那拍结算对已出现 -> 当场落盘 + 补库。"""
        rec = self._rec()
        self.c._peak_base = None
        self.st.peak_result = {'before': 1507.49, 'after': 1515.62,
                               'season': 57}
        lines = self.c._start_peak_probe(T0, rec)
        self.assertTrue(any('1507.49 -> 1515.62' in ln for ln in lines))
        data = self._peak_json(rec)
        self.assertEqual(data['before'], 1507.49)
        self.assertEqual(data['after'], 1515.62)
        self.assertEqual(data['season'], 57)
        self.assertEqual(self.st.peak_backfills, [os.path.basename(rec.dir_path)])
        self.assertIsNone(self.c._peak_probe)

    def test_pending_then_chain_checked(self):
        """结算对没出现 -> 挂起；上一局残留对（链条对不上）**不收**；
        本局对（before == 基线 after）一到就落盘。"""
        rec = self._rec()
        self.c._peak_base = {'before': 1500.0, 'after': 1507.49, 'season': 57}
        self.st.peak_result = None
        self.c._start_peak_probe(T0, rec)
        self.assertIsNotNone(self.c._peak_probe)
        # 第 1 拍：还没出现
        self.assertEqual(self.c._poll_peak_probe(T0 + 2), [])
        # 第 2 拍：出现了，但是**上一局的残留**（old=1490 ≠ 基线 after=1507.49）
        self.st.peak_result = {'before': 1490.0, 'after': 1496.6, 'season': 57}
        self.assertEqual(self.c._poll_peak_probe(T0 + 4), [])
        self.assertIsNotNone(self.c._peak_probe)
        self.assertIsNone(self._peak_json(rec))
        # 第 3 拍：本局的结算对到了
        self.st.peak_result = {'before': 1507.49, 'after': 1515.62,
                               'season': 57}
        lines = self.c._poll_peak_probe(T0 + 6)
        self.assertTrue(any('1515.62' in ln for ln in lines))
        self.assertEqual(self._peak_json(rec)['after'], 1515.62)
        self.assertIsNone(self.c._peak_probe)

    def test_no_base_accepts_first_pair(self):
        """开局基线没读到（DuelResult 已被清）-> 收局后第一对就收。"""
        rec = self._rec()
        self.c._peak_base = None
        self.st.peak_result = {'before': 1507.49, 'after': 1515.62,
                               'season': 57}
        self.c._start_peak_probe(T0, rec)
        self.assertIsNone(self.c._peak_probe)       # 当场就收了
        self.assertEqual(self._peak_json(rec)['after'], 1515.62)

    def test_timeout_gives_up(self):
        """窗口内没等到 -> 放弃（返回日志行、清挂起、不落盘）。"""
        rec = self._rec()
        self.c._peak_base = None
        self.st.peak_result = None
        self.c._start_peak_probe(T0, rec)
        self.assertIsNotNone(self.c._peak_probe)
        lines = self.c._poll_peak_probe(T0 + self.c.peak_wait + 1)
        self.assertTrue(any('放弃' in ln for ln in lines))
        self.assertIsNone(self.c._peak_probe)
        self.assertIsNone(self._peak_json(rec))

    def test_backfill_error_not_blocking(self):
        """补库抛异常 -> 只记日志，落盘照常完成。"""
        rec = self._rec()
        self.st.peak_backfill_result = None
        self.st.peak_result = {'before': 1507.49, 'after': 1515.62,
                               'season': 57}

        def boom(dir_name):
            raise RuntimeError('db locked')

        self.st.backfill_peak = boom
        lines = self.c._start_peak_probe(T0, rec)
        self.assertEqual(self._peak_json(rec)['after'], 1515.62)
        self.assertTrue(any('补写失败' in ln for ln in self.c_log(lines)))

    def c_log(self, lines):
        return lines + self.lines


if __name__ == '__main__':
    unittest.main()
