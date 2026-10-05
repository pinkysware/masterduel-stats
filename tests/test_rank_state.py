# -*- coding: utf-8 -*-
"""`core.rank_state`（段位推断链 v2）单测 —— 规则逐条 = 用户 2026-09-18 口述。

1. 低段（黄金及以下）：3 胜升一小段、输不减不掉。
2. 白金：4 胜升一小段、**连输 5** 掉一小段（2026-09-18 口述，本轮未改）。
3. 钻石：4 胜升一小段、**连输 3** 掉一小段（2026-09-20 用户实测）。
4. 大师：5 胜升一小段、**连输 3** 掉一小段（同轮实测）。
4. 跨大段不掉：最低小段（Tier 5）触线保持原位。
5. 升/掉段后计数从满额重新起算。
"""
import io
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import rank_state

#: ★ 单测**必须**把状态文件指到 tmp（2026-09-18 深夜实锤）：
#: `apply_result(state, result)` 的 `path=None` 会写 `rank_state.STATE_PATH` ——
#: 默认就是**真机数据** `data/rank_state.json`。实测跑一次本文件就把用户的
#: 段位状态从「白金1、连败 0」改成「白金2、连败 1」，下一局的推断会跟着错。
_TMP_DIR = None
_TMP_STATE = None
_ORIG_PATH = None


def setUpModule():
    global _TMP_DIR, _TMP_STATE, _ORIG_PATH
    import tempfile
    _TMP_DIR = tempfile.mkdtemp(prefix='rank-state-')
    _TMP_STATE = os.path.join(_TMP_DIR, 'rank_state.json')
    _ORIG_PATH = rank_state.STATE_PATH
    rank_state.STATE_PATH = _TMP_STATE


def tearDownModule():
    rank_state.STATE_PATH = _ORIG_PATH
    if _TMP_DIR:
        import shutil
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


class IsolationTest(unittest.TestCase):
    """回归锁：这套单测不许碰真机 `data/rank_state.json`。"""

    def test_state_path_points_into_tmp(self):
        self.assertEqual(rank_state.STATE_PATH, _TMP_STATE)
        self.assertNotEqual(rank_state.STATE_PATH, _ORIG_PATH)
        self.assertNotIn(os.path.join('data', 'rank_state.json'),
                         rank_state.STATE_PATH)

    def test_apply_result_writes_only_tmp(self):
        real = os.path.join(PROJ, 'data', 'rank_state.json')
        before = io.open(real, encoding='utf-8').read() if os.path.exists(real) else None
        rank_state.apply_result({'rank': 6, 'rate': 3, 'win_streak': 0,
                                 'lose_streak': 0}, 'Win')
        after = io.open(real, encoding='utf-8').read() if os.path.exists(real) else None
        self.assertEqual(before, after, '单测写到了真机状态文件！')
        self.assertTrue(os.path.exists(_TMP_STATE))


class LowRankTest(unittest.TestCase):
    """黄金及以下（rank<=4）：3 胜升一小段，输不减、不掉。"""

    def test_three_wins_promote(self):
        st = {'rank': 4, 'rate': 2, 'win_streak': 0, 'lose_streak': 0}
        st, p = rank_state.apply_result(st, 'Win')
        st, p = rank_state.apply_result(st, 'Win')
        self.assertFalse(p)
        st, p = rank_state.apply_result(st, 'Win')
        self.assertTrue(p)
        self.assertEqual((st['rank'], st['rate'], st['win_streak']), (4, 1, 0))

    def test_lose_keeps_wins_to_promote(self):
        st = {'rank': 4, 'rate': 2, 'win_streak': 1, 'lose_streak': 0}
        st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual(st['win_streak'], 0)
        self.assertEqual(rank_state.wins_to_promote(st), 3)
        self.assertEqual(st['rate'], 2)          # 不会掉


class PlatinumTest(unittest.TestCase):
    """白金：4 胜一小段、**连输 5** 掉一小段（钻石另见 DiamondTest）。"""

    def test_four_wins_promote(self):
        st = {'rank': 5, 'rate': 1, 'win_streak': 3, 'lose_streak': 0}
        st, p = rank_state.apply_result(st, 'Win')
        self.assertTrue(p)
        self.assertEqual((st['rank'], st['rate']), (6, 5))   # 晋级钻石

    def test_lose_five_drops_one_tier(self):
        st = {'rank': 5, 'rate': 2, 'win_streak': 0, 'lose_streak': 0}
        for _ in range(5):
            st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual(st['rate'], 3)          # 白金 2 -> 白金 3

    def test_win_breaks_the_lose_chain(self):
        """v3（09-27）：保护局内（连输 2 把未到 3）赢一把 → 清保护重新计。"""
        st = {'rank': 5, 'rate': 2, 'win_streak': 0, 'lose_streak': 0}
        for _ in range(2):                       # 保护局 ×2，不掉
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual(st['rate'], 2)
        st, _ = rank_state.apply_result(st, 'Win')   # 赢一把：进度 +1、清保护
        self.assertEqual(st['lose_streak'], 0)
        self.assertEqual(st['win_streak'], 1)
        for _ in range(3):                       # 扣进度 1 把 + 保护 2 把：不掉
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual(st['rate'], 2)
        st, _ = rank_state.apply_result(st, 'Lose')  # 进度 0 后连输第 3 把 → 掉
        self.assertEqual(st['rate'], 3)

    def test_lose_first_deducts_progress(self):
        """★ v3 增量：**有进度时输局扣进度**，不累积连败 —— 连输不掉。

        白金 2、还有 3 胜（ws=1）连输 4 把：1→0（扣进度）、保护×2、
        第 4 把（进度 0 后连输第 3 把）才掉。
        """
        st = {'rank': 5, 'rate': 2, 'win_streak': 1, 'lose_streak': 0}
        st, _ = rank_state.apply_result(st, 'Lose')       # 进度 1 -> 0
        self.assertEqual(st['win_streak'], 0)
        self.assertEqual(st['rate'], 2)
        st, _ = rank_state.apply_result(st, 'Lose')       # 保护 1
        self.assertEqual(st['rate'], 2)
        st, _ = rank_state.apply_result(st, 'Lose')       # 保护 2
        self.assertEqual(st['rate'], 2)
        st, _ = rank_state.apply_result(st, 'Lose')       # 保护后第 3 把 → 掉
        self.assertEqual(st['rate'], 3)


class DiamondTest(unittest.TestCase):
    """钻石：4 胜一小段、**连输 3** 掉一小段（2026-09-20 用户实测）。"""

    def test_three_loses_drop_one_tier(self):
        """★ 用户原话：「我在钻石4连输2把，第三把提示我输了就掉段」。

        前 2 把绝不掉 —— 逐把断言。
        """
        st = {'rank': 6, 'rate': 4, 'win_streak': 0, 'lose_streak': 0}
        for i in range(2):
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual(st['rate'], 4, '第 %d 把不该掉段' % (i + 1))
        st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual(st['rate'], 5)          # Diamond 4 -> Diamond 5

    def test_diamond_five_never_drops_out(self):
        """钻石五（最低小段）触线也不掉出钻石（跨大段保护，与大师五同款）。"""
        st = {'rank': 6, 'rate': 5, 'win_streak': 0, 'lose_streak': 0}
        for _ in range(6):
            st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual((st['rank'], st['rate']), (6, 5))

    def test_promote_needs_four_wins(self):
        """升段仍是 4 连胜 —— 与掉段线（3）**不是同一个数**，别"对齐"。"""
        st = {'rank': 6, 'rate': 3, 'win_streak': 3, 'lose_streak': 0}
        st, p = rank_state.apply_result(st, 'Win')
        self.assertTrue(p)
        self.assertEqual(st['rate'], 2)          # Diamond 3 -> Diamond 2


class MasterTest(unittest.TestCase):
    """大师：5 胜一小段、**连输 3** 掉一小段；大师五不掉出大师。"""

    def test_three_loses_drop_one_tier(self):
        """★ 逐把断言（2026-09-20）：前 2 把**不掉**，第 3 把才掉。

        ⚠️ 旧用例循环 4 次只查终值 —— 在「3 把掉段」下第 4 把会再掉一次，
        终值恰好也是 +1，**假通过**（改口径时实测发现）。别退回只查终值。
        """
        st = {'rank': 7, 'rate': 3, 'win_streak': 0, 'lose_streak': 0}
        for i in range(2):
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual(st['rate'], 3, '第 %d 把不该掉段' % (i + 1))
        st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual(st['rate'], 4)          # Master 3 -> Master 4（第 3 把）
        self.assertEqual(st['lose_streak'], 0)   # 掉段后重新起算

    def test_master_five_never_drops_out(self):
        """大师五（最低小段）触线也不掉出大师（跨大段保护）。"""
        st = {'rank': 7, 'rate': 5, 'win_streak': 0, 'lose_streak': 0}
        for _ in range(6):
            st, _ = rank_state.apply_result(st, 'Lose')
        self.assertEqual(st['rank'], 7)
        self.assertEqual(st['rate'], 5)

    def test_master_one_never_drops(self):
        """★ 09-28 用户确认：**大师 I（段内顶）不掉段** —— 连输再多 rate 也不动。"""
        st = {'rank': 7, 'rate': 1, 'win_streak': 0, 'lose_streak': 0}
        for i in range(6):
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual((st['rank'], st['rate']), (7, 1),
                             '大师 I 第 %d 把也不该掉段' % (i + 1))

    def test_master_two_three_four_still_drop(self):
        """★ 大师 II/III/IV 连输 3 **会掉一段**（09-28 用户确认大师段会掉）。"""
        for rate in (2, 3, 4):
            st = {'rank': 7, 'rate': rate, 'win_streak': 0, 'lose_streak': 0}
            for i in range(2):
                st, _ = rank_state.apply_result(st, 'Lose')
                self.assertEqual(st['rate'], rate, '第 %d 把不该掉' % (i + 1))
            st, _ = rank_state.apply_result(st, 'Lose')
            self.assertEqual(st['rate'], rate + 1, '大师 %d 第 3 把掉一段' % rate)

    def test_five_wins_promote(self):
        st = {'rank': 7, 'rate': 1, 'win_streak': 4, 'lose_streak': 0}
        st, p = rank_state.apply_result(st, 'Win')
        self.assertTrue(p)
        self.assertEqual(st['rate'], 1)          # Master 1 再升：仍是 Master（v1 简化）


class WinsToPromoteTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(rank_state.wins_to_promote(
            {'rank': 5, 'rate': 1, 'win_streak': 1}), 3)
        self.assertEqual(rank_state.wins_to_promote(
            {'rank': 7, 'rate': 2, 'win_streak': 1}), 4)

    def test_unknown_streak(self):
        self.assertEqual(rank_state.wins_to_promote(
            {'rank': 5, 'rate': 1, 'win_streak': None}), -1)


if __name__ == '__main__':
    unittest.main()


class NextStatePureTest(unittest.TestCase):
    """`next_state` 是纯函数：推一局**不许落盘**（界面的手填链靠它「先写库再落状态」）。"""

    def test_next_state_does_not_save(self):
        from core import rank_state
        path = rank_state.STATE_PATH
        st, promoted = rank_state.next_state(
            {'rank': 6, 'rate': 5, 'win_streak': 3, 'lose_streak': 0}, 'Win')
        self.assertTrue(promoted)                          # 第 4 胜 → 升一小段
        self.assertEqual((st['rank'], st['rate']), (6, 4))  # 钻石5 → 钻石4（大段内升）
        self.assertEqual(path, rank_state.STATE_PATH)
