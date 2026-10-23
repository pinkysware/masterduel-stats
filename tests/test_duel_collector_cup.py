# -*- coding: utf-8 -*-
"""duel_collector DC 杯 DP 编排单测（2026-10-23）：

* 门控 —— 只挂 DC 杯（`group(game_mode)=='dc'`），天梯/巅峰局不读；
* **阶段门控** —— 只挂 2 阶段（1 阶段打的是 DLv，不是积分）；
* 收局 -> 结算对没出现 -> 挂起重试 -> 出现即落盘 `cup_dp.json` + 补库；
* **链条校验** —— 新对 `before` 必须 = 开局基线的 `after`（防上一局残留）；
* 超时放弃不阻塞；基线缺位时收局后第一对就收；
* 连打两局各自落盘（队列，不是单槽位）。
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


class CupHooks(Hooks):
    """只打桩 DC 杯相关钩子；其余走基类默认（None/空）。"""

    def __init__(self):
        self.cup_result = None
        self.cup_seq = []             # 每次 read_cup_result 弹一个（然后回落）
        self.cup_stage = 2
        self.cup_backfills = []
        self.cup_backfill_result = 'DC杯积分入库：x'

    def read_cup_result(self):
        if self.cup_seq:
            return self.cup_seq.pop(0)
        return self.cup_result

    def read_cup_stage(self):
        return self.cup_stage

    def backfill_cup(self, dir_name):
        self.cup_backfills.append(dir_name)
        return self.cup_backfill_result


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.records = os.path.join(self.tmp, 'duel_records')
        os.makedirs(self.records)
        self.st = CupHooks()
        self.lines = []
        self.c = DuelCollector(records_dir=self.records, hooks=self.st,
                               log=self.lines.append)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rec(self, game_mode=12, name='20261023_122900'):
        rec = _OpenRecord(first_seen_at='2026-10-23 12:29:00',
                          game_mode=game_mode)
        rec.dir_path = os.path.join(self.records, name)
        os.makedirs(rec.dir_path, exist_ok=True)
        return rec

    def _cup_json(self, rec):
        path = os.path.join(rec.dir_path, 'cup_dp.json')
        if not os.path.isfile(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def _set_stage(self, stage):
        """模拟「开局那拍读到的阶段」。"""
        self.c._cup_stages = [stage] if stage is not None else []


class TestGating(Base):

    def test_ranked_game_not_probed(self):
        """天梯局不读 DC 杯 DP。"""
        self._set_stage(2)
        rec = self._rec(game_mode=3)
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])

    def test_peak_game_not_probed(self):
        """巅峰局不读 DC 杯 DP（那是 peak_rate 的地盘）。"""
        self._set_stage(2)
        rec = self._rec(game_mode=19)
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])

    def test_stage1_not_probed(self):
        """★ 1 阶段打的是 DLv 不是 DP —— 必须拦住（混着记会让统计失真）。"""
        self._set_stage(1)
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000}
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])
        self.assertIsNone(self._cup_json(rec))
        self.assertTrue(any('不是 2 阶段' in ln for ln in self.lines))

    def test_no_stage_not_probed(self):
        """阶段读不到也不记（不猜）—— 兜底重读也拿不到时。"""
        self._set_stage(None)
        self.st.cup_stage = None          # 兜底重读也拿不到
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000}
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])
        self.assertIsNone(self._cup_json(rec))

    def test_stage_fallback_reread(self):
        """★ 开局那拍没读到阶段（采集器中途 attach）-> 收局时兜底重读一次。

        `stage` 是常驻值，离开杯赛界面也不会清（`dp`/`dlv` 会清）——
        所以收局这一刻重读是可靠的。
        """
        self._set_stage(None)             # 开局那拍没读到
        self.st.cup_stage = 2             # 收局兜底重读拿到 2
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000, 'win': 1}
        self.c._start_cup_probe(T0, rec)
        self.assertEqual(self._cup_json(rec)['after'], 1000)
        self.assertEqual(self.c._cup_stages, [2])   # 顺手记住

    def test_stage_fallback_reads_1_still_skips(self):
        """兜底读到 1 阶段 -> 照样跳过（不能因为「兜底成功」就放行）。"""
        self._set_stage(None)
        self.st.cup_stage = 1
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000}
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])
        self.assertIsNone(self._cup_json(rec))

    def test_no_dir_path(self):
        """早退局（没建目录）不挂。"""
        self._set_stage(2)
        rec = _OpenRecord(first_seen_at='2026-10-23 12:29:00', game_mode=12)
        rec.dir_path = None
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])


class TestSettle(Base):

    def test_immediate_settle(self):
        """收局那一刻结算对已经在 -> 直接落盘。"""
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000, 'win': 1}
        lines = self.c._start_cup_probe(T0, rec)
        data = self._cup_json(rec)
        self.assertIsNotNone(data)
        self.assertEqual(data['before'], 0)
        self.assertEqual(data['after'], 1000)
        self.assertEqual(data['win'], 1)
        self.assertEqual(data['stage'], 2)
        self.assertEqual(self.st.cup_backfills, [os.path.basename(rec.dir_path)])
        self.assertTrue(any('0 -> 1000' in ln for ln in lines))

    def test_pending_then_settle(self):
        """结算对晚到 -> 挂起 -> 出现了才落盘。"""
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = None
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])
        self.assertIsNone(self._cup_json(rec))
        self.assertEqual(len(self.c._cup_probes), 1)
        # 结算对出现
        self.st.cup_result = {'before': 0, 'after': 1000, 'win': 1}
        lines = self.c._poll_cup_probes(T0 + 5)
        self.assertEqual(self.c._cup_probes, [])
        self.assertEqual(self._cup_json(rec)['after'], 1000)
        self.assertTrue(lines)

    def test_timeout_gives_up(self):
        """窗口内一直没等到 -> 放弃并留日志（不阻塞采集）。"""
        self._set_stage(2)
        rec = self._rec()
        self.c._start_cup_probe(T0, rec)
        lines = self.c._poll_cup_probes(T0 + self.c.cup_wait + 1)
        self.assertEqual(self.c._cup_probes, [])
        self.assertIsNone(self._cup_json(rec))
        self.assertTrue(any('放弃' in ln for ln in lines))

    def test_backfill_failure_does_not_raise(self):
        """补库炸了不影响落盘（口径同巅峰积分）。"""
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000}

        def boom(_):
            raise RuntimeError('库打不开')
        self.st.backfill_cup = boom
        self.c._start_cup_probe(T0, rec)
        self.assertEqual(self._cup_json(rec)['after'], 1000)

    def test_disk_write_failure_returns_empty(self):
        """落盘失败 -> 返回空、不抛。"""
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = {'before': 0, 'after': 1000}
        os.rmdir(rec.dir_path)             # 目录没了，写不进去
        self.assertEqual(self.c._start_cup_probe(T0, rec), [])


class TestChainCheck(Base):
    """链条校验：新对 before 必须接得上基线的 after。"""

    def test_rejects_stale_pair(self):
        """上一局的残留对（before 接不上）不收。"""
        self._set_stage(2)
        rec = self._rec()
        self.c._cup_base = {'before': 0, 'after': 1000}   # 上一局局后 1000
        self.st.cup_result = {'before': 500, 'after': 1500}   # 接不上 -> 残留
        self.assertEqual(self.c._read_cup_checked(), None)

    def test_accepts_chained_pair(self):
        self._set_stage(2)
        self.c._cup_base = {'before': 0, 'after': 1000}
        self.st.cup_result = {'before': 1000, 'after': 1500}
        self.assertEqual(self.c._read_cup_checked(),
                         {'before': 1000, 'after': 1500})

    def test_first_pair_when_no_base(self):
        """基线没有时，收局后出现的第一对就收。"""
        self.c._cup_base = None
        self.st.cup_result = {'before': 1000, 'after': 1500}
        self.assertEqual(self.c._read_cup_checked(),
                         {'before': 1000, 'after': 1500})


class TestQueue(Base):
    """连打两局：两个挂起各自落盘（不是单槽位）。"""

    def test_two_probes_both_settle(self):
        self._set_stage(2)
        r1, r2 = self._rec(name='a'), self._rec(name='b')
        self.st.cup_result = None
        self.c._start_cup_probe(T0, r1)
        self.c._start_cup_probe(T0 + 1, r2)
        self.assertEqual(len(self.c._cup_probes), 2)
        self.c._cup_base = None                      # 没有基线 -> 第一对就收
        self.st.cup_result = {'before': 0, 'after': 1000}
        self.c._poll_cup_probes(T0 + 5)
        self.assertEqual(self.c._cup_probes, [])
        self.assertIsNotNone(self._cup_json(r1))
        self.assertIsNotNone(self._cup_json(r2))

    def test_reentry_same_dir_replaces(self):
        """同一目录重复挂起 -> 替换（重入幂等）。"""
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = None
        self.c._start_cup_probe(T0, rec)
        self.c._start_cup_probe(T0 + 1, rec)
        self.assertEqual(len(self.c._cup_probes), 1)


class TestResetOnGameExit(Base):
    """游戏退出：DP 挂起与基线必须一起作废（BUG-B8-07 同一批坑）。"""

    def test_exit_clears_probes(self):
        self._set_stage(2)
        rec = self._rec()
        self.st.cup_result = None
        self.c._start_cup_probe(T0, rec)
        self.c._cup_base = {'before': 0, 'after': 1000}
        lines = self.c.on_game_gone()
        self.assertEqual(self.c._cup_probes, [])
        self.assertIsNone(self.c._cup_base)
        self.assertTrue(any('DC杯积分' in ln for ln in lines))


if __name__ == '__main__':
    unittest.main()
