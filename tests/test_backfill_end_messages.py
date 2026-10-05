# -*- coding: utf-8 -*-
"""tools/backfill_end_messages.py 的单测（历史回补）。

配对窗口 / 唯一认领 / winbit 优先覆盖 / 只补 NULL / 幂等，全部临时库。
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import backfill_end_messages as bf                                # noqa: E402
from core.storage import Storage                                  # noqa: E402


def _entry(captured, winbits=None, text='文案', result=None):
    return {'captured_at': captured, 'winbits': winbits,
            'mem_result': result,
            'texts': {'messageText': text}}


def _row(match_id, started, ended, result=None):
    return {'match_id': match_id, 'started_at': started, 'ended_at': ended,
            'result': result, 'origin': 'log', 'mode': 'pvp'}


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, 't.db')
        self.st = Storage(self.db)
        self.st.init_schema()
        self.em = os.path.join(self.tmp, 'em.jsonl')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_rows(self, *rows):
        for r in rows:
            self.st.upsert_match(r)

    def write_em(self, entries):
        with open(self.em, 'w', encoding='utf-8', newline='') as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')

    def load(self):
        return bf.load_entries(self.em)

    def do_backfill(self, entries, rows, apply=False, window=60):
        return bf.backfill(self.st, entries, rows, apply=apply, window=window)

    def count(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0]


class TestPairing(Base):

    def test_window_and_claim(self):
        """capture 在 ended 附近 -> 认领；2.5 分钟外 -> 放弃。"""
        self.add_rows(_row('x', '2026-09-13 12:33:21.000',
                           '2026-09-13 12:36:54.000', result='Lose'))
        entries = [
            _entry('2026-09-13 12:36:45', winbits=[0, 1]),
            _entry('2026-09-13 12:29:41', winbits=[0, 1]),   # 窗外 2.5 分钟
        ]
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9))
        claims = {c['match_id'] for c in report['claims']}
        self.assertEqual(claims, {'x'})                        # 只认领窗内那条

    def test_one_row_two_entries(self):
        """同一局的两条结算文案 -> 第一条赢，第二条落空。"""
        self.add_rows(_row('x', '2026-09-13 21:30:00.000',
                           '2026-09-13 21:34:50.000'))
        entries = [_entry('2026-09-13 21:34:44', winbits=[1, 0], text='A'),
                   _entry('2026-09-13 21:35:10', winbits=[1, 0], text='B')]
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9))
        self.assertEqual(len(report['claims']), 1)
        self.assertEqual(report['claims'][0]['result_note'], 'A')
        self.assertEqual(report['ambiguous'],
                         [('2026-09-13 21:35:10', 'rows-already-claimed')])

    def test_equidistant_ambiguous(self):
        """两行等距 -> 歧义放弃（不猜）。"""
        self.add_rows(_row('p', '2026-09-13 12:00:00.000',
                           '2026-09-13 12:00:00.000'),
                      _row('q', '2026-09-13 12:02:00.000',
                           '2026-09-13 12:02:00.000'))
        entries = [_entry('2026-09-13 12:01:00', winbits=[1, 0])]
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9))
        self.assertEqual(report['claims'], [])
        self.assertEqual(report['ambiguous'],
                         [('2026-09-13 12:01:00', 'equidistant-rows')])

    def test_row_without_ended_not_eligible(self):
        """掉线局（ended_at NULL）不参配。"""
        self.add_rows(_row('x', '2026-09-13 12:00:00.000', None))
        entries = [_entry('2026-09-13 12:05:00', winbits=[1, 0])]
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9))
        self.assertEqual(report['claims'], [])


class TestApply(Base):

    def test_winbit_fill_and_overwrite(self):
        """winbit 填空 / 覆盖文案推的错值（冲突留痕）。"""
        self.add_rows(_row('ok', '2026-09-13 12:00:00.000',
                           '2026-09-13 12:05:00.000', result='Win'),
                      _row('bad', '2026-09-13 12:10:00.000',
                           '2026-09-13 12:15:00.000', result='Win'))
        entries = [_entry('2026-09-13 12:05:30', winbits=[1, 0],
                          text='对手的LP已削减至0'),
                   _entry('2026-09-13 12:15:30', winbits=[0, 1],
                          text='已投降')]
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9),
                         apply=True)
        ok = self.st.get_match('ok')
        self.assertEqual(ok['result'], 'Win')
        self.assertEqual(ok['result_src'], 'winbit')
        self.assertEqual(ok['winbits'], [1, 0])
        self.assertEqual(ok['result_note'], '对手的LP已削减至0')
        bad = self.st.get_match('bad')
        self.assertEqual(bad['result'], 'Lose')                # 覆盖判反的旧值
        self.assertIn('result冲突:旧=Win', bad['result_note'])
        self.assertEqual(report['applied'], 2)

    def test_no_winbits_only_note(self):
        """没带 winbits 的样本：只补文案备注，result 不碰。"""
        self.add_rows(_row('x', '2026-09-13 12:00:00.000',
                           '2026-09-13 12:05:00.000', result='Lose'))
        entries = [_entry('2026-09-13 12:05:30', winbits=None,
                          text='已投降')]
        self.do_backfill(entries, self.st.list_matches(limit=10 ** 9), apply=True)
        row = self.st.get_match('x')
        self.assertEqual(row['result'], 'Lose')                # 不动
        self.assertEqual(row['result_note'], '已投降')
        self.assertIsNone(row['winbits'])

    def test_idempotent_rerun(self):
        self.add_rows(_row('x', '2026-09-13 12:00:00.000',
                           '2026-09-13 12:05:00.000'))
        entries = [_entry('2026-09-13 12:05:30', winbits=[1, 0], text='T')]
        self.do_backfill(entries, self.st.list_matches(limit=10 ** 9), apply=True)
        report = self.do_backfill(entries, self.st.list_matches(limit=10 ** 9),
                         apply=True)
        actions = report['claims'][0]['actions']
        self.assertEqual(actions['winbits'], 'unchanged')
        self.assertEqual(actions['result'], 'unchanged')

    def test_dry_run_no_db_change(self):
        self.add_rows(_row('x', '2026-09-13 12:00:00.000',
                           '2026-09-13 12:05:00.000'))
        entries = [_entry('2026-09-13 12:05:30', winbits=[1, 0])]
        self.do_backfill(entries, self.st.list_matches(limit=10 ** 9), apply=False)
        row = self.st.get_match('x')
        self.assertIsNone(row['winbits'])


class TestCli(Base):

    def test_set_result(self):
        self.add_rows(_row('rec:x', '2026-09-13 12:00:00.000', None))
        rc = bf.main(['--set-result', 'rec:x', 'Win', '--db', self.db])
        self.assertEqual(rc, 0)
        row = self.st.get_match('rec:x')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'manual')


if __name__ == '__main__':
    unittest.main()
