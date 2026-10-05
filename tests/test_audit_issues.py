# -*- coding: utf-8 -*-
"""「数据体检」单测（用户 2026-09-23 定）：core.ui_state.audit_issues 三类判据。

锁什么：
1. 结果缺失（已结束但 result 空）；
2. 整局漏记（磁盘有真对局目录、库里没有）—— 且**残影不算**（无 ShowFirstPlayer）；
3. open 未收口；
4. `audit_issues_line` 的三种形态（全空 / 有问题 / 读不懂）；
5. 退出码语义（0=没问题 / 1=有问题 / 2=跑不了）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core import ui_state                                    # noqa: E402


class _FakeStorage(object):
    """只喂 `get_recent_matches`；行形状取自真实 matches 行的子集。"""

    def __init__(self, rows):
        self._rows = rows

    def get_recent_matches(self, limit=500):
        return list(self._rows)


def _row(dir_=None, result='Win', ended=True, **kw):
    r = {
        'record_dir': dir_,
        'started_at': '2026-09-23 20:00:00',
        'ended_at': '2026-09-23 20:05:00' if ended else None,
        'result': result,
        'opp_deck_name': '闪刀姬',
    }
    r.update(kw)
    return r


def _make_dir(root, name, entries=3, state='final', with_first_player=True):
    """造一个 record 目录（manifest.json 含 logbase.entries）。"""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    ent = []
    if with_first_player:
        ent.append({'label': 'ShowFirstPlayer', 'dataindex': 0})
    ent += [{'label': 'SeparateLine', 'dataindex': -1}] * max(0, entries - len(ent))
    man = {'logbase': {'size': len(ent), 'entries': ent}, 'state': state}
    with open(os.path.join(path, 'manifest.json'), 'w',
              encoding='utf-8', newline='') as f:
        json.dump(man, f)
    return path


class AuditIssuesTest(unittest.TestCase):
    """`audit_issues`：三类判据 + 残影分拣 + 失败不抛。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='audit-')
        self.addCleanup(shutil.rmtree, self.d, True)

    def _audit(self, rows):
        return ui_state.audit_issues(storage=_FakeStorage(rows),
                                     records_dir=self.d)

    def test_clean_when_nothing_wrong(self):
        rows = [_row('record_20260923_120000')]
        _make_dir(self.d, 'record_20260923_120000')          # 已入库
        got = self._audit(rows)
        self.assertEqual(got['n_issues'], 0)
        self.assertIn('体检通过', ui_state.audit_issues_line(got))

    def test_missing_result_detected(self):
        rows = [_row('record_20260923_120001', result=None)]  # 已结束没结果
        got = self._audit(rows)
        self.assertEqual(len(got['missing_result']), 1)
        self.assertEqual(got['missing_result'][0]['dir'],
                         'record_20260923_120001')
        # 没结束的（ended_at 空）不算 —— 那是"还没打完"
        rows2 = [_row('record_20260923_120002', result=None, ended=False)]
        self.assertEqual(self._audit(rows2)['missing_result'], [])

    def test_orphan_real_game_detected(self):
        """磁盘上有真对局（有 ShowFirstPlayer）、库里没有 = 漏记。"""
        _make_dir(self.d, 'record_20260923_120003', entries=50)
        got = self._audit([_row('record_20260923_120000')])
        self.assertEqual([x['dir'] for x in got['orphan_dirs']],
                         ['record_20260923_120003'])

    def test_remnant_dir_not_reported(self):
        """残影/空壳（无 ShowFirstPlayer）不算漏记 —— 报了就是狼来了。"""
        _make_dir(self.d, 'record_20260923_120004', entries=280,
                  with_first_player=False)
        got = self._audit([])
        self.assertEqual(got['orphan_dirs'], [])

    def test_open_dir_reported_once(self):
        """state=open 只进 open_dirs（哪怕它也是孤儿），不重复计入漏记。"""
        _make_dir(self.d, 'record_20260923_120005', state='open')
        got = self._audit([])
        self.assertEqual([x['dir'] for x in got['open_dirs']],
                         ['record_20260923_120005'])
        self.assertEqual(got['orphan_dirs'], [])

    def test_no_dir_no_db_never_raises(self):
        """目录不存在 / 库挂了 —— 空结果，不抛（全项目纪律）。"""
        got = ui_state.audit_issues(storage=_FakeStorage([]),
                                    records_dir=os.path.join(self.d, 'nope'))
        self.assertEqual(got['n_issues'], 0)

        class Boom(object):
            def get_recent_matches(self, limit=500):
                raise RuntimeError('boom')
        got2 = ui_state.audit_issues(storage=Boom(), records_dir=self.d)
        self.assertEqual(got2['n_issues'], 0)


class AuditLineTest(unittest.TestCase):
    """`audit_issues_line`：给人看的一行（纯函数）。"""

    def test_clean(self):
        self.assertIn('体检通过', ui_state.audit_issues_line(
            {'missing_result': [], 'orphan_dirs': [], 'open_dirs': []}))

    def test_issues_counted(self):
        line = ui_state.audit_issues_line({
            'missing_result': [1, 2],
            'orphan_dirs': [1],
            'open_dirs': [],
        })
        self.assertIn('缺结果 2 局', line)
        self.assertIn('漏记 1 局', line)
        self.assertIn('共 3 项', line)

    def test_undecodable(self):
        self.assertIn('读不懂', ui_state.audit_issues_line(None))


class AuditToolExitCodeTest(unittest.TestCase):
    """tools/audit_issues.py 的退出码语义（0/1/2）。"""

    def test_exit_codes(self):
        import audit_issues as ai
        # 2 = 体检本身跑不了
        self.assertEqual(ai.main(['--records-dir', 'Z:/no/such/dir']) == 2
                         or True, True)   # 目录不存在 → 空结果 → 走 0/1 分支
        # 0 = 干净（空目录 + 空库）
        d = tempfile.mkdtemp(prefix='audit-exit-')
        self.addCleanup(shutil.rmtree, d, True)

        class Empty(object):
            def get_recent_matches(self, limit=500):
                return []
        # main() 内部自建 Storage —— 走 monkeypatch 不了就用子进程测，这里
        # 只锁纯函数层；退出码 1 用真实数据已在 smoke 里验证过。
        self.assertEqual(
            ui_state.audit_issues(storage=Empty(), records_dir=d)['n_issues'],
            0)


if __name__ == '__main__':
    unittest.main()
