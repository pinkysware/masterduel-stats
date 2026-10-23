# -*- coding: utf-8 -*-
"""★ 2026-10-22：段位**假升段**事故的三道回归锁。

## 事故还原（用户 2026-10-22 实报）

「今天第一把打 LaoJoker 那局又判定成升段局了，明明打 Sora 10-17 23:57
那把已经判定过了。这把应该是大师4 对局。」

库里那行是 `my_rank_before=7-5, my_rank_after=7-4` → 界面画「7-5 → 7-4 ↑ 升段」。
但探测读到的**真值**是 `before=7-4, after=7-4` —— 这局是**大师4 原地不动**。

## 三个独立缺陷（缺一不可）

| # | 缺陷 | 后果 |
|---|---|---|
| **A** | `tests/test_ui_app.py` 没隔离 `rank_state.STATE_PATH` | 跑一次全量单测就把**真机**的连胜/连败计数改掉（实测 `lose_streak` 涨到 107） |
| **B** | 局前段位「只补空」→ 推断值先占位，真值永远进不来 | 库里留着推断的错值，界面据此画错升降段 |
| **C** | 手填链没有模式守卫 → 非天梯局也推天梯连胜计数 | 连败计数虚高，后续每局推断都歪 |

## 本文件盯住三件事

1. **A**：单测不许写真机状态文件（`rank_state.STATE_PATH` 必须落在 tmp）。
2. **B**：探测真值能盖掉**推断值**，但**盖不掉**权威值（API/人工）。
3. **C**：手填链只推**天梯局**；巅峰/DC/活动局不许动天梯连胜计数。
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import rank_state                     # noqa: E402
from core import ui_actions                     # noqa: E402
from core.storage import Storage                # noqa: E402


def _read_bytes(path):
    """读文件（存在才读）—— 用 with 关掉，免得 ResourceWarning 刷屏。"""
    if not os.path.exists(path):
        return None
    with io.open(path, 'rb') as f:
        return f.read()


def _read_text(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


# ==================================================================== A

class TestNoRealStatePollution(unittest.TestCase):
    """🔴 回归锁 A：单测**不许碰真机** `data/rank_state.json`。

    实锤（2026-10-22）：`tests/test_ui_app.py` 的「点结果格」用例会走
    `ui_actions.repair_result` → `_infer_rank_after_manual` →
    `rank_state.save()`，而 `save()` 默认写**真机**状态文件
    （`STATE_PATH` 是模块级常量，不跟 `DATA_DIR` 走）。
    结果：每跑一次全量单测，真机的 `lose_streak` 就 +1
    （10-19~10-21 从 80 涨到 107），连胜/连败计数从此全是错的。
    """

    def test_real_state_file_is_not_touched(self):
        """跑一遍「点结果格 → 手填结果」全链，真机状态文件必须**一个字节没变**。"""
        real = os.path.join(PROJ, 'data', 'rank_state.json')
        before = _read_bytes(real)

        tmp = tempfile.mkdtemp(prefix='rank-guard-')
        self.addCleanup(shutil.rmtree, tmp, True)
        orig = rank_state.STATE_PATH
        rank_state.STATE_PATH = os.path.join(tmp, 'rank_state.json')
        self.addCleanup(setattr, rank_state, 'STATE_PATH', orig)
        rank_state.save({'rank': 7, 'rate': 4, 'win_streak': 1,
                         'lose_streak': 0, 'src': 'probe'})

        class _St(object):
            def repair_match_result(self, match_id, result=None, result_src=None,
                                    result_note=None, winbits=None):
                return 1

            def get_match(self, match_id):
                return {'match_id': match_id, 'game_mode': 3,
                        'my_rank_after': None}

            def infer_rank_for_match(self, match_id, before, after):
                return 1

        ui_actions.repair_result(_St(), 'm1', 'Win')

        after = _read_bytes(real)
        self.assertEqual(before, after,
                         '单测改了真机 data/rank_state.json —— 段位状态被污染！')

    def test_audit_log_goes_to_tmp_not_real(self):
        """🔴 单测的审计日志也不许进真机那份。

        实锤（2026-10-22）：`_audit_log_path` 的判据原来拿**可变的**
        `STATE_PATH` 比对 —— 单测把它改到 tmp 后，「写的就是 STATE_PATH」
        恒成立 → 审计照样写进真机的 `data/archive/rank_state_audit.log`。
        一次全量单测 = 几千行噪音，把那份**取证日志**淹掉
        （09-27 / 10-05 两次「段位被无日志改掉」事故就靠它取证）。

        判据必须比对**导入时的默认路径**（`DEFAULT_STATE_PATH` 常量）。
        """
        tmp = tempfile.mkdtemp(prefix='rank-audit-')
        self.addCleanup(shutil.rmtree, tmp, True)
        orig = rank_state.STATE_PATH
        rank_state.STATE_PATH = os.path.join(tmp, 'rank_state.json')
        self.addCleanup(setattr, rank_state, 'STATE_PATH', orig)

        real_audit = os.path.join(PROJ, 'data', 'archive',
                                   'rank_state_audit.log')
        before = _read_bytes(real_audit)

        rank_state.save({'rank': 6, 'rate': 5, 'win_streak': 0,
                         'lose_streak': 0})
        rank_state.save({'rank': 6, 'rate': 4, 'win_streak': 0,
                         'lose_streak': 0})

        after = _read_bytes(real_audit)
        self.assertEqual(before, after,
                         '单测的审计日志写进真机取证日志了 —— 取证材料被噪音淹没')
        #: 审计应该落在 tmp 那边
        tmp_audit = os.path.join(tmp, 'rank_state_audit.log')
        self.assertTrue(os.path.isfile(tmp_audit),
                        '审计日志没落在状态文件旁边')

    def test_ui_app_test_module_isolates_state_path(self):
        """🔴 `tests/test_ui_app.py` 自己必须把 `STATE_PATH` 指到 tmp。

        静态检查（不跑那批 Qt 用例）：模块源码里必须有
        `rank_state.STATE_PATH = ...` 的隔离动作。
        """
        src = _read_text(os.path.join(PROJ, 'tests', 'test_ui_app.py'))
        self.assertIn('rank_state.STATE_PATH', src,
                      'test_ui_app.py 没隔离 rank_state.STATE_PATH —— '
                      '跑一次就改真机状态')
        self.assertIn('STATE_PATH', src)


# ==================================================================== B

class TestTruthOverridesInferred(unittest.TestCase):
    """🔴 回归锁 B：探测真值要能盖掉**推断值**。

    事故：2026-10-22 11:06 LaoJoker 局 ——
    库里 `my_rank_before=7-5`（推断值），探测真值 `7-4`。
    「只补空」的规则让真值永远补不进去 → 界面画成「7-5 → 7-4 ↑ 升段」，
    而这局其实是大师4 原地不动。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='rank-truth-')
        self.addCleanup(shutil.rmtree, self.d, True)
        self.st = Storage(os.path.join(self.d, 'x.db'))
        self.st.init_schema()
        self.st.upsert_match({
            'match_id': 'rec:record_x', 'started_at': '2026-10-22 11:06:42',
            'origin': 'record', 'mode': 'pvp', 'game_mode': 3,
            'record_dir': 'record_x', 'result': 'Lose',
            'my_rank_before': 7, 'my_rate_before': 5,   # ← 推断值（错的）
            'my_rank_after': 7, 'my_rate_after': 4,
        })

    def _row(self):
        with self.st._open() as conn:
            row = conn.execute('SELECT * FROM matches').fetchone()
        return dict(row)

    def test_truth_overrides_inferred_before(self):
        """真值 7-4 盖掉推断值 7-5 → 界面不再画「7-5 → 7-4 升段」。"""
        n = self.st.repair_rank_before_for_record('record_x', 7, 4)
        self.assertEqual(n, 1)
        row = self._row()
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (7, 4))

    def test_same_value_is_noop(self):
        """真值 == 现值 → 什么都不做（幂等）。"""
        self.assertEqual(self.st.repair_rank_before_for_record('record_x', 7, 5), 0)
        self.assertEqual(self.st.repair_rank_before_for_record('record_x', 7, 4), 1)
        # 再来一次（真值已就位）→ 不动
        self.assertEqual(self.st.repair_rank_before_for_record('record_x', 7, 4), 0)

    def test_api_value_is_not_overwritten(self):
        """🔴 API 写的值是权威的 —— 真值**不许**盖掉它。

        判据：局后段位与局前段位**不一致** → 这一行不是推断链写的
        （推断链同局写 before/after 用同一份推断结果）→ 不碰。
        """
        self.st.upsert_match({
            'match_id': 'rec:record_api', 'started_at': '2026-10-02 00:09:13',
            'origin': 'api', 'mode': 'pvp', 'game_mode': 3,
            'record_dir': 'record_api', 'result': 'Win',
            'my_rank_before': 6, 'my_rate_before': 4,   # API 值
            'my_rank_after': 7, 'my_rate_after': 1,     # 与 before 不一致
        })
        n = self.st.repair_rank_before_for_record('record_api', 7, 2)
        self.assertEqual(n, 0, 'API 写的局前段位被覆盖了！')
        with self.st._open() as conn:
            row = conn.execute("SELECT * FROM matches WHERE match_id = ?",
                               ('rec:record_api',)).fetchone()
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (6, 4))

    def test_illegal_value_is_not_overwritten(self):
        """非法值（越界）判不出来源 → 不碰。"""
        self.st.upsert_match({
            'match_id': 'rec:record_bad', 'started_at': '2026-09-21 23:36:31',
            'origin': 'record', 'mode': 'pvp', 'game_mode': 15,
            'record_dir': 'record_bad', 'result': 'Win',
            'my_rank_before': 0, 'my_rate_before': 0,
            'my_rank_after': 6, 'my_rate_after': 2,
        })
        self.assertEqual(self.st.repair_rank_before_for_record('record_bad', 7, 4), 0)
        with self.st._open() as conn:
            row = conn.execute("SELECT * FROM matches WHERE match_id = ?",
                               ('rec:record_bad',)).fetchone()
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (0, 0))

    def test_missing_row_is_zero(self):
        self.assertEqual(self.st.repair_rank_before_for_record('nope', 7, 4), 0)

    def test_none_is_noop(self):
        self.assertEqual(self.st.repair_rank_before_for_record('record_x', None, 4), 0)
        self.assertEqual(self.st.repair_rank_before_for_record('record_x', 7, None), 0)


# ==================================================================== C

class TestManualFillOnlyRanked(unittest.TestCase):
    """🔴 回归锁 C：手填链**只推天梯局**。

    事故：`_infer_rank_after_manual` 没有模式守卫 —— 巅峰/DC/活动局
    打完**不改天梯段位**（口径同采集器 `_infer_rank_state`），
    但手填链照样拿它们的结果去推天梯连胜/连败。
    实锤：10-19~10-21 用户手填的几局（非天梯）把 `lose_streak`
    一路推到 107 —— 后面每局的推断都建立在虚高的连败数上。
    """

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix='rank-manual-')
        self.addCleanup(shutil.rmtree, tmp, True)
        self.orig = rank_state.STATE_PATH
        rank_state.STATE_PATH = os.path.join(tmp, 'rank_state.json')
        self.addCleanup(setattr, rank_state, 'STATE_PATH', self.orig)
        rank_state.save({'rank': 7, 'rate': 5, 'win_streak': 0,
                         'lose_streak': 0})

    def _st(self, game_mode):
        class _St(object):
            def __init__(self):
                self.inferred = []

            def repair_match_result(self, match_id, result=None, result_src=None,
                                    result_note=None, winbits=None):
                return 1

            def get_match(self, match_id):
                return {'match_id': match_id, 'game_mode': game_mode,
                        'my_rank_after': None}

            def infer_rank_for_match(self, match_id, before, after):
                self.inferred.append((match_id, before, after))
                return 1
        return _St()

    def test_ranked_advances_streak(self):
        """天梯局照旧推（这是这条链存在的意义）。"""
        st = self._st(3)
        ui_actions.repair_result(st, 'm1', 'Win')
        self.assertEqual(len(st.inferred), 1)
        self.assertEqual(rank_state.load()['win_streak'], 1)

    def test_dc_does_not_advance_streak(self):
        """🔴 DC 杯（game_mode=12）打完不动天梯连胜计数。"""
        st = self._st(12)
        ui_actions.repair_result(st, 'm1', 'Win')
        self.assertEqual(st.inferred, [], 'DC 局不该推天梯段位')
        self.assertEqual(rank_state.load()['win_streak'], 0)

    def test_peak_does_not_advance_streak(self):
        """🔴 巅峰（game_mode=19）同样不动。"""
        st = self._st(19)
        ui_actions.repair_result(st, 'm1', 'Lose')
        self.assertEqual(st.inferred, [])
        self.assertEqual(rank_state.load()['lose_streak'], 0)

    def test_event_does_not_advance_streak(self):
        """🔴 活动局（判不出专属分类的那些）同样不动。"""
        st = self._st(21)
        ui_actions.repair_result(st, 'm1', 'Lose')
        self.assertEqual(st.inferred, [])
        self.assertEqual(rank_state.load()['lose_streak'], 0)

    def test_unknown_mode_does_not_advance_streak(self):
        """判不出模式（NULL）→ **不推**（不编）。"""
        st = self._st(None)
        ui_actions.repair_result(st, 'm1', 'Win')
        self.assertEqual(st.inferred, [])
        self.assertEqual(rank_state.load()['win_streak'], 0)


if __name__ == '__main__':
    unittest.main()
