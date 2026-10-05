# -*- coding: utf-8 -*-
"""core/ui_actions.py 单测（M7 · S0）—— 标注写操作 + 开机自启。

★ 纪律：**测试不许真的建快捷方式、不许真的调 PowerShell** ——
``set_autostart`` 的 ``runner`` 与「启动目录」都注入成临时目录。
"""
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import ui_actions                                          # noqa: E402
from core.storage import MANUAL_RESULT_SRC                           # noqa: E402

_KEEP = object()


class FakeStorage(object):
    """记下调用，不碰真库。"""

    def __init__(self, fail=False, deck_id=7):
        self.fail = fail
        self.deck_id = deck_id
        self.marked = []
        self.cleared = []
        self.created = []
        self.repaired = []
        self.inferred = []
        self.rows = {}                    # match_id -> 行 dict（get_match 用）

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError('db is locked')

    def upsert_opp_deck(self, name, aliases=None):
        self._maybe_fail()
        self.created.append((name, aliases))
        return self.deck_id

    def mark_opp_deck(self, match_id, opp_deck_id=None, name=None, note=None):
        self._maybe_fail()
        self.marked.append((match_id, opp_deck_id, name, note))

    def clear_opp_deck(self, match_id):
        self._maybe_fail()
        self.cleared.append(match_id)
        return match_id in ('m1',)

    def repair_match_result(self, match_id, result=_KEEP, result_src=_KEEP,
                            result_note=_KEEP, winbits=_KEEP):
        self._maybe_fail()
        self.repaired.append((match_id, result, result_src))
        return 1 if match_id == 'm1' else 0

    def get_match(self, match_id):
        return (self.rows or {}).get(match_id)

    def infer_rank_for_match(self, match_id, before, after):
        self.inferred.append((match_id, before, after))
        return 1 if match_id == 'm1' else 0


class MarkTest(unittest.TestCase):

    def test_mark_no_deck_is_rejected(self):
        """终审：没有「标记未知」—— 无名无 id 直接拒绝，不假装成功。"""
        ok, msg = ui_actions.mark(FakeStorage(), 'm1')
        self.assertFalse(ok)
        self.assertIn('标记未知', msg)

    def test_mark_no_match_id(self):
        ok, _ = ui_actions.mark(FakeStorage(), '', name='闪刀姬')
        self.assertFalse(ok)

    def test_mark_by_name(self):
        st = FakeStorage()
        ok, msg = ui_actions.mark(st, 'm1', name='闪刀姬')
        self.assertTrue(ok, msg)
        self.assertEqual(st.marked[0][0], 'm1')
        self.assertEqual(st.marked[0][2], '闪刀姬')

    def test_mark_by_id(self):
        st = FakeStorage()
        ok, _ = ui_actions.mark(st, 'm1', opp_deck_id=7)
        self.assertTrue(ok)
        self.assertEqual(st.marked[0][1], 7)

    def test_mark_new_creates_entry_first(self):
        st = FakeStorage()
        ok, _ = ui_actions.mark(st, 'm1', name='耀圣+神艺', new=True)
        self.assertTrue(ok)
        self.assertEqual(st.created, [('耀圣+神艺', None)])
        self.assertEqual(st.marked[0][2], '耀圣+神艺')

    def test_mark_note(self):
        st = FakeStorage()
        ui_actions.mark(st, 'm1', name='闪刀姬', note='看过战报')
        self.assertEqual(st.marked[0][3], '看过战报')

    def test_mark_never_raises(self):
        ok, msg = ui_actions.mark(FakeStorage(fail=True), 'm1', name='x')
        self.assertFalse(ok)
        self.assertIn('写入失败', msg)

    def test_unmark_ok_and_already_empty(self):
        st = FakeStorage()
        self.assertEqual(ui_actions.unmark(st, 'm1'), (True, '已撤销'))
        self.assertEqual(st.cleared, ['m1'])
        self.assertEqual(ui_actions.unmark(st, 'm9')[0], False)

    def test_unmark_never_raises(self):
        ok, msg = ui_actions.unmark(FakeStorage(fail=True), 'm1')
        self.assertFalse(ok)
        self.assertIn('撤销失败', msg)

    def test_new_deck(self):
        st = FakeStorage()
        self.assertEqual(ui_actions.new_deck(st, ' 烙印 '), (True, 7))
        self.assertEqual(st.created[0][0], '烙印')

    def test_new_deck_rejects_blank(self):
        self.assertEqual(ui_actions.new_deck(FakeStorage(), '  '), (False, None))


class AutostartTest(unittest.TestCase):
    """用临时「启动」目录 + 假 runner，绝不碰真实系统。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='autostart_')
        self.real_startup = ui_actions.startup_dir
        ui_actions.startup_dir = lambda: self.dir

    def tearDown(self):
        ui_actions.startup_dir = self.real_startup
        shutil.rmtree(self.dir, ignore_errors=True)

    def _runner(self, args):
        """假 runner：只记下命令，并"假装"建出 .lnk（供 is_autostart 判断）。"""
        self.seen = list(args)
        # 从 -Command 里抠出路径纯属测试便利，生产由系统自己建
        with open(ui_actions.shortcut_path(self.name), 'w') as f:
            f.write('x')
        return 0

    def test_is_autostart_false_when_absent(self):
        self.assertFalse(ui_actions.is_autostart())

    def test_turn_on(self):
        self.name = ui_actions.SHORTCUT_NAME
        ok = ui_actions.set_autostart(True, target=r'C:\py\pythonw.exe',
                                      arguments=r'tools\duel_ui.py',
                                      workdir=r'E:\proj', runner=self._runner)
        self.assertTrue(ok)
        self.assertTrue(ui_actions.is_autostart())
        self.assertEqual(self.seen[0], 'powershell')
        self.assertIn('WScript.Shell', self.seen[-1])

    def test_turn_off_removes_file(self):
        self.name = ui_actions.SHORTCUT_NAME
        ui_actions.set_autostart(True, target='x', runner=self._runner)
        self.assertTrue(ui_actions.is_autostart())
        self.assertTrue(ui_actions.set_autostart(False, target='x'))
        self.assertFalse(ui_actions.is_autostart())

    def test_turn_on_requires_target(self):
        self.assertFalse(ui_actions.set_autostart(True, target='',
                                                  runner=lambda a: 0))

    def test_runner_failure_means_false(self):
        self.assertFalse(ui_actions.set_autostart(
            True, target='x', runner=lambda a: 1))

    def test_runner_raises_means_false(self):
        def boom(args):
            raise OSError('no powershell')
        self.assertFalse(ui_actions.set_autostart(True, target='x',
                                                  runner=boom))

    def test_custom_shortcut_name(self):
        self.name = 'other.lnk'
        ui_actions.set_autostart(True, target='x', name='other.lnk',
                                 runner=self._runner)
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'other.lnk')))


class StartupDirTest(unittest.TestCase):
    """``%APPDATA%`` 在 Git Bash 下是空的（项目里踩过）—— 要有回退。"""

    def test_returns_non_empty_path(self):
        d = ui_actions.startup_dir()
        self.assertTrue(d)
        self.assertTrue(d.endswith('Startup'))

    def test_fallback_when_appdata_missing(self):
        saved = os.environ.pop('APPDATA', None)
        try:
            self.assertTrue(ui_actions.startup_dir())
        finally:
            if saved is not None:
                os.environ['APPDATA'] = saved


if __name__ == '__main__':
    unittest.main()


class ResultRepairTest(unittest.TestCase):
    """`ui_actions.repair_result` —— 秒投局的手动修正入口（任务书 Bug 2 兜底）。"""

    def test_writes_manual_stamp(self):
        st = FakeStorage()
        ok, msg = ui_actions.repair_result(st, 'm1', 'Lose')
        self.assertTrue(ok, msg)
        #: ★ 契约锁：界面写下去的戳 == storage 守的那个值（两处必须同源）
        self.assertEqual(st.repaired, [('m1', 'Lose', MANUAL_RESULT_SRC)])

    def test_clear_passes_both_none(self):
        """清除 = result 与 result_src 一起置 NULL（否则会留下「人工」戳挡自动解析）。"""
        st = FakeStorage()
        ok, msg = ui_actions.repair_result(st, 'm1', None)
        self.assertTrue(ok, msg)
        self.assertEqual(st.repaired, [('m1', None, None)])
        self.assertIn('清除', msg)

    def test_rejects_bad_value(self):
        st = FakeStorage()
        ok, msg = ui_actions.repair_result(st, 'm1', 'Victory')
        self.assertFalse(ok)
        self.assertEqual(st.repaired, [])

    def test_rejects_missing_match_id(self):
        self.assertFalse(ui_actions.repair_result(FakeStorage(), '', 'Win')[0])

    def test_row_not_found(self):
        ok, msg = ui_actions.repair_result(FakeStorage(), 'nope', 'Win')
        self.assertFalse(ok)
        self.assertIn('没找到', msg)

    def test_storage_error_is_swallowed(self):
        """写库炸了也不许把异常丢给界面（纪律同 mark）。"""
        ok, msg = ui_actions.repair_result(FakeStorage(fail=True), 'm1', 'Win')
        self.assertFalse(ok)
        self.assertIn('写入失败', msg)

    def test_labels_cover_storage_values(self):
        """菜单标签与 storage 认可的取值集合一致（别再各写一份）。"""
        from core.storage import DONE_RESULTS
        self.assertEqual(sorted(ui_actions.RESULT_LABELS),
                         sorted(DONE_RESULTS))


class ManualResultRankInferTest(unittest.TestCase):
    """手填结果 -> 段位推断自动跟上（2026-09-19 用户实测链路）。

    手填的局多半是采集侧缺证据的局（掉线/崩溃/秒投），段位往往也空着；
    推断链补段位 + 推进连胜，下一局才接得上。规矩：行还没段位才动（防双计）、
    任何失败静默、rank_state 状态文件指到 tmp（不许写真机数据）。
    """

    def setUp(self):
        import tempfile
        from core import rank_state
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self._orig = rank_state.STATE_PATH
        rank_state.STATE_PATH = os.path.join(tmp, 'rank_state.json')
        self.addCleanup(setattr, rank_state, 'STATE_PATH', self._orig)
        rank_state.save({'rank': 6, 'rate': 5, 'win_streak': 0,
                         'lose_streak': 0})          # 钻石5，连胜连败清零

    def _st(self, rank_after=None):
        st = FakeStorage()
        st.rows = {'m1': {'match_id': 'm1', 'my_rank_after': rank_after}}
        return st

    def test_manual_fill_infers_rank_and_advances_state(self):
        """行没段位 + 负局 → 补 6-5→6-5（连败+1，钻石不掉段），状态落盘。"""
        from core import rank_state
        st = self._st(rank_after=None)
        ok, msg = ui_actions.repair_result(st, 'm1', 'Lose')
        self.assertTrue(ok, msg)
        self.assertEqual(st.inferred, [('m1', (6, 5), (6, 5))])
        self.assertEqual((rank_state.load()['lose_streak']), 1)

    def test_win_advances_win_streak(self):
        from core import rank_state
        st = self._st(rank_after=None)
        ui_actions.repair_result(st, 'm1', 'Win')
        self.assertEqual(st.inferred[0][2], (6, 5))       # 钻石5 赢一把：连胜+1 不到 4，不升段
        self.assertEqual(rank_state.load()['win_streak'], 1)

    def test_row_with_rank_is_not_double_counted(self):
        """行已有段位 = 采集链处理过 → 不补、**状态也不许推进**（防连胜虚高）。"""
        from core import rank_state
        st = self._st(rank_after=6)
        ui_actions.repair_result(st, 'm1', 'Win')
        self.assertEqual(st.inferred, [])
        self.assertEqual(rank_state.load()['win_streak'], 0)

    def test_uncalibrated_state_is_silent(self):
        from core import rank_state
        rank_state.save({'rank': 6, 'rate': 5})            # 没有连胜计数
        st = self._st(rank_after=None)
        ok, _ = ui_actions.repair_result(st, 'm1', 'Win')
        self.assertTrue(ok)
        self.assertEqual(st.inferred, [])

    def test_storage_error_still_reports_result_written(self):
        """推断炸了不许影响结果修正的返回（best-effort）。"""
        st = FakeStorage(fail=True)
        ok, msg = ui_actions.repair_result(st, 'm1', 'Win')
        self.assertFalse(ok)                               # 修库本身失败了照旧报错
        self.assertIn('写入失败', msg)
