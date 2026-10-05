# -*- coding: utf-8 -*-
"""`tools/capture_end_message.py` 的纯逻辑单测（不碰游戏进程）。

盯的是「文案表能不能当判据」这条判定 —— 攒样本攒到最后就靠它出结论，
判错了方向就错了；以及「从日志自动判胜负」那条（观战/回放必须不判）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tools.capture_end_message import (                      # noqa: E402
    append_record, analyze_samples, latest_log_result, load_records,
    make_record, render_report, resolve_result)


def rec(text, result=None, label=None, result_src=None):
    return make_record({'messageText': text}, {'instance': '0x1', 'pid': 1},
                       result, label, result_src=result_src)


class TestAnalyzeSamples(unittest.TestCase):
    def test_empty(self):
        rep = analyze_samples([])
        self.assertEqual(rep['total'], 0)
        self.assertIsNone(rep['ok'])

    def test_single_sample_one_side(self):
        """只有一侧样本 -> 给不出结论，但要点出缺哪侧。"""
        rep = analyze_samples([rec('已投降', 'lose', '我投降')])
        self.assertIsNone(rep['ok'])
        self.assertIn('赢', rep['verdict'])

    def test_two_texts_two_results_is_criterion(self):
        """不同结局显示不同文案 -> 判据成立。"""
        rep = analyze_samples([rec('已投降', 'lose', '我投降'),
                               rec('对方已投降', 'win', '对方投降')])
        self.assertTrue(rep['ok'])
        self.assertIn('是判据', rep['verdict'])

    def test_same_text_both_results_not_criterion(self):
        """★ 关键反例：同一个文案同时出现在胜负两侧 -> 不是判据。"""
        rep = analyze_samples([rec('已投降', 'lose', '我投降'),
                               rec('已投降', 'win', '对方投降')])
        self.assertFalse(rep['ok'])
        self.assertIn('不是判据', rep['verdict'])
        self.assertIn('已投降', rep['verdict'])

    def test_multiple_texts_same_result_ok(self):
        """同一侧有多种文案不影响判据。"""
        rep = analyze_samples([rec('已投降', 'lose', '我投降'),
                               rec('你输了', 'lose', '正常被打死'),
                               rec('对方已投降', 'win', '对方投降')])
        self.assertTrue(rep['ok'])

    def test_unlabeled_samples_reported(self):
        rep = analyze_samples([rec('已投降', 'lose'), rec('???')])
        self.assertEqual(rep['unlabeled'], 1)
        self.assertEqual(rep['total'], 2)

    def test_all_unlabeled_cannot_judge(self):
        rep = analyze_samples([rec('已投降'), rec('对方已投降')])
        self.assertIsNone(rep['ok'])
        self.assertIn('--result', rep['verdict'])

    def test_empty_text_grouped(self):
        rep = analyze_samples([rec('', 'lose')])
        self.assertEqual(rep['rows'][0]['text'], '(空)')

    def test_rows_count_and_labels(self):
        rep = analyze_samples([rec('已投降', 'lose', '我投降'),
                               rec('已投降', 'lose', '点了投降')])
        self.assertEqual(rep['rows'][0]['count'], 2)
        self.assertEqual(rep['rows'][0]['labels'], ['我投降', '点了投降'])

    def test_missing_texts_key_is_safe(self):
        rep = analyze_samples([{'captured_at': 'x', 'result': 'lose'}])
        self.assertEqual(rep['total'], 1)


class TestRenderReport(unittest.TestCase):
    def test_empty_hint(self):
        out = render_report([])
        self.assertIn('还没有样本', out)
        self.assertIn('--result', out)

    def test_contains_rows_and_verdict(self):
        out = render_report([rec('已投降', 'lose', '我投降'),
                             rec('对方已投降', 'win', '对方投降')])
        self.assertIn('文案表', out)
        self.assertIn('是判据', out)
        self.assertIn('我投降', out)


LOG_LINE = ('[2026-09-12 20:50:41.700] [info]  [Ygom.handleScryEvent] '
            'Game ended: result=Lose deckId=48171157, isDeckKnown=true\n')


class TestLogResult(unittest.TestCase):
    """`latest_log_result` —— 从日志尾部自动判胜负（观战/回放必须不判）。"""

    @staticmethod
    def _write(tmpdir, name, body):
        path = os.path.join(tmpdir, name)
        with io.open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(body)
        return path

    def test_real_line_shape(self):
        """真实日志行长这样（带毫秒时间戳、末尾还有 `, isDeckKnown=true`）。"""
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, 'log.log', LOG_LINE)
            self.assertEqual(latest_log_result([path]), 'lose')

    def test_last_match_wins(self):
        body = ('Game ended: result=Win deckId=1\n'
                'Game ended: result=Lose deckId=2\n')
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, 'log.log', body)
            self.assertEqual(latest_log_result([path]), 'lose')

    def test_newer_file_wins(self):
        """两个文件都扫时，后扫（更新）的那个说了算。"""
        with tempfile.TemporaryDirectory() as d:
            old = self._write(d, 'log.old.log',
                              'Game ended: result=Win deckId=1\n')
            new = self._write(d, 'log.log',
                              'Game ended: result=Lose deckId=2\n')
            self.assertEqual(latest_log_result([old, new]), 'lose')

    def test_null_deck_is_not_a_result(self):
        """★ 观战/回放（`deckId=null`）的 result 不代表「我」→ 必须 None。"""
        body = ('[x] [Ygom.handleScryEvent] Game ended: '
                'result=Win deckId=null, isDeckKnown=false\n')
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, 'log.log', body)
            self.assertIsNone(latest_log_result([path]))

    def test_no_match_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(latest_log_result([os.path.join(d, 'nope.log')]))
            path = self._write(d, 'log.log', 'nothing to see here\n')
            self.assertIsNone(latest_log_result([path]))


class TestResolveResult(unittest.TestCase):
    """`resolve_result` —— 命令行优先，其次日志；`auto=False` 时不许猜。"""

    def test_cli_wins(self):
        self.assertEqual(resolve_result('WIN'), ('win', 'cli'))

    def test_auto_off_never_guesses(self):
        self.assertEqual(resolve_result(None, auto=False), (None, None))

    def test_falls_back_to_log(self):
        import tools.capture_end_message as M
        keep = M.latest_log_result
        M.latest_log_result = lambda *a, **k: 'lose'
        try:
            self.assertEqual(resolve_result(None), ('lose', 'log'))
        finally:
            M.latest_log_result = keep

    def test_log_empty_gives_nothing(self):
        import tools.capture_end_message as M
        keep = M.latest_log_result
        M.latest_log_result = lambda *a, **k: None
        try:
            self.assertEqual(resolve_result(None), (None, None))
        finally:
            M.latest_log_result = keep


class TestResultSrcPlumbing(unittest.TestCase):
    def test_record_keeps_source(self):
        self.assertEqual(rec('x', 'lose', '对方投降', result_src='log')
                         ['result_src'], 'log')

    def test_log_derived_marked_in_report(self):
        recs = [rec('已投降', 'lose', None, result_src='log'),
                rec('对方已投降', 'win', None, result_src='cli')]
        out = render_report(recs)
        self.assertIn('从日志推的', out)
        self.assertIn('<lose*>', out)
        self.assertIn('<win>', out)

    def test_no_footnote_without_log_source(self):
        self.assertNotIn('从日志推的', render_report([rec('已投降', 'lose')]))


class TestStorage(unittest.TestCase):
    def test_append_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'end.jsonl')
            append_record(rec('已投降', 'lose', '我投降'), path)
            append_record(rec('对方已投降', 'win', '对方投降'), path)
            got = load_records(path)
            self.assertEqual(len(got), 2)
            self.assertEqual(got[0]['texts']['messageText'], '已投降')
            self.assertEqual(got[1]['result'], 'win')

    def test_written_with_lf_not_crlf(self):
        """⚠️ 写 JSONL 必须显式 newline=''，否则 Windows 会写成 CRLF。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'end.jsonl')
            append_record(rec('已投降', 'lose'), path)
            with io.open(path, 'rb') as f:
                blob = f.read()
            self.assertNotIn(b'\r\n', blob)
            self.assertTrue(blob.endswith(b'\n'))
            json.loads(blob.decode('utf-8').strip())

    def test_load_missing_file(self):
        self.assertEqual(load_records(os.path.join('Z:', 'nope.jsonl')
                                      if os.name == 'nt' else '/nope.jsonl'), [])

    def test_bad_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'end.jsonl')
            with io.open(path, 'w', encoding='utf-8', newline='') as f:
                f.write('{"a": 1}\n')
                f.write('not json\n')
            self.assertEqual(len(load_records(path)), 1)


if __name__ == '__main__':
    unittest.main()
