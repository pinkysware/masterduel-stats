# -*- coding: utf-8 -*-
"""`tools/backfill_my_rank.py`（段位补录）单测 —— 临时库 + 临时记录目录，不碰真库。

盯住四件事：
* 值取 `after`（局后）；`after` 缺了退回 `base`；两个都缺给 None（**不猜**）
* **只补空、不覆盖**（库里已有 API 写的值就保留）
* 库内没有对应对局时不写、只报告（import 迟到的行下次还能补）
* `--list` / `--dry-run` **真的不写库**
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core.storage import Storage        # noqa: E402
import backfill_my_rank as bm           # noqa: E402

T0 = '2026-09-13 12:00:00'


def _row(match_id='rec:record_x', record_dir='record_x', rank_after=None,
         rate_after=None):
    return {'match_id': match_id, 'started_at': T0, 'mode': 'pvp',
            'origin': 'record', 'record_dir': record_dir, 'result': 'Win',
            'my_rank_after': rank_after, 'my_rate_after': rate_after}


class Base(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='rankbf_')
        self.db = os.path.join(self.d, 'masterduel.db')
        self.recs = os.path.join(self.d, 'duel_records')
        os.makedirs(self.recs)
        self.st = Storage(self.db)
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def probe(self, name='record_x', base=(5, 2, 28900), after=(5, 2, 29000),
              resolved='exp-change', raw=None, before=None):
        d = os.path.join(self.recs, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, bm.PROBE_NAME), 'w',
                  encoding='utf-8', newline='') as f:
            if raw is not None:
                f.write(raw)
                return
            data = {'resolved_by': resolved}
            if before:
                data['before'] = {'rank': before[0], 'rate': before[1]}
            if base:
                data['base'] = {'rank': base[0], 'rate': base[1], 'exp': base[2]}
            if after:
                data['after'] = {'rank': after[0], 'rate': after[1], 'exp': after[2]}
            f.write(json.dumps(data))

    def db_row(self, record_dir='record_x'):
        with self.st._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE record_dir = ?',
                               (record_dir,)).fetchone()
        return dict(row) if row is not None else None

    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = bm.main(['--db', self.db, '--records-dir', self.recs]
                           + list(argv))
        return code, buf.getvalue()


class TestResolvedRank(unittest.TestCase):
    """取哪个值：after 优先、缺了退 base、都缺给 None。"""

    def test_prefers_after(self):
        got = bm.resolved_rank({'base': {'rank': 5, 'rate': 2, 'exp': 1},
                                'after': {'rank': 5, 'rate': 1, 'exp': 2}})
        self.assertEqual(got[:2], (5, 1))

    def test_falls_back_to_base(self):
        got = bm.resolved_rank({'base': {'rank': 5, 'rate': 2, 'exp': 1},
                                'after': None})
        self.assertEqual(got[:2], (5, 2))

    def test_none_when_no_value(self):
        for bad in ({}, None, {'base': {}, 'after': {}},
                    {'base': {'rank': None, 'rate': 2}, 'after': None}):
            self.assertIsNone(bm.resolved_rank(bad), bad)


class TestRun(Base):
    """核心逻辑（打桩库 + 临时目录）。"""

    def test_ready_when_db_row_exists(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        stat = bm.run(self.recs, self.st)
        self.assertEqual(stat['total'], 1)
        self.assertEqual(stat['ready'], 1)
        self.assertIsNone(self.db_row()['my_rank_after'])      # 没 apply 不写

    def test_apply_fills_only_null(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        stat = bm.run(self.recs, self.st, apply=True)
        self.assertEqual(stat['ready'], 1)
        row = self.db_row()
        self.assertEqual(row['my_rank_after'], 5)
        self.assertEqual(row['my_rate_after'], 1)

    def test_second_run_is_kept_not_overwritten(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        bm.run(self.recs, self.st, apply=True)
        stat = bm.run(self.recs, self.st, apply=True)          # 再跑一次
        self.assertEqual(stat['ready'], 0)
        self.assertEqual(stat['skipped_has_value'], 1)
        self.assertIn('不覆盖', stat['detail'][0][3])

    def test_api_value_is_never_overwritten(self):
        """库里已有 API 写的段位时，内存值只能建议、不能覆盖。"""
        self.st.upsert_match(_row(rank_after=6, rate_after=3))
        self.probe(after=(5, 1, 29000))
        stat = bm.run(self.recs, self.st, apply=True)
        self.assertEqual(stat['skipped_has_value'], 1)
        row = self.db_row()
        self.assertEqual((row['my_rank_after'], row['my_rate_after']), (6, 3))

    def test_unmatched_when_no_db_row(self):
        self.probe(after=(5, 1, 29000))
        stat = bm.run(self.recs, self.st, apply=True)
        self.assertEqual(stat['unmatched'], 1)
        self.assertEqual(stat['ready'], 0)

    def test_unreadable_json_is_counted(self):
        self.probe(raw='{not json')
        stat = bm.run(self.recs, self.st, apply=True)
        self.assertEqual(stat['unreadable'], 1)

    def test_uses_base_when_after_missing(self):
        """`after` 缺失（探测超时且没读到）= 局内没变，用 base 也是对的。"""
        self.st.upsert_match(_row())
        self.probe(base=(5, 2, 28900), after=None, resolved='timeout')
        bm.run(self.recs, self.st, apply=True)
        row = self.db_row()
        self.assertEqual((row['my_rank_after'], row['my_rate_after']), (5, 2))

    def test_missing_records_dir_is_empty(self):
        stat = bm.run(os.path.join(self.d, 'nope'), self.st)
        self.assertEqual(stat['total'], 0)


class TestBeforeValue(Base):
    """局前段位（`Duel.rank`，内存）也能补 —— UI 段位徽章消费的就是它。"""

    def test_resolved_before(self):
        self.assertEqual(bm.resolved_before({'before': {'rank': 5, 'rate': 2}}),
                         (5, 2))
        for bad in ({}, None, {'before': None}, {'before': {'rank': 5}}):
            self.assertIsNone(bm.resolved_before(bad), bad)

    def test_apply_fills_before_and_after(self):
        self.st.upsert_match(_row())
        self.probe(before=(5, 2), after=(5, 1, 29500))
        stat = bm.run(self.recs, self.st, apply=True)
        self.assertEqual(stat['ready'], 1)
        row = self.db_row()
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (5, 2))
        self.assertEqual((row['my_rank_after'], row['my_rate_after']), (5, 1))

    def test_before_is_kept_when_present(self):
        self.st.upsert_match(_row(rank_after=None, rate_after=None))
        with self.st._open() as conn:
            conn.execute('UPDATE matches SET my_rank_before = 6,'
                         ' my_rate_before = 3 WHERE record_dir = ?', ('record_x',))
        self.probe(before=(5, 2), after=(5, 1, 29500))
        bm.run(self.recs, self.st, apply=True)
        row = self.db_row()
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (6, 3))

    def test_old_artifact_without_before_still_fills_after(self):
        """老产物（没有 before 键）照常只补 after。"""
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29500))
        bm.run(self.recs, self.st, apply=True)
        row = self.db_row()
        self.assertIsNone(row['my_rank_before'])
        self.assertEqual(row['my_rank_after'], 5)


class TestApplyOne(Base):
    """★ 采集器收局自动补写用的单目录口（2026-09-17）。

    以前落盘后要用户手跑 `--apply`；现在 `duel_collector` 落盘那一刻调这里。
    判据必须与 CLI 共用同一套解析助手，所以这里逐条对齐 CLI 的语义。
    """

    def test_fills_after_and_before(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000), before=(5, 2))
        msg = bm.apply_one(self.st, self.recs, 'record_x')
        self.assertIn('段位入库', msg)
        row = self.db_row()
        self.assertEqual((row['my_rank_after'], row['my_rate_after']), (5, 1))
        self.assertEqual((row['my_rank_before'], row['my_rate_before']), (5, 2))

    def test_missing_row_is_silent_none(self):
        """库内还没有对应对局（import 没跑）-> 返回 None 静默，不抛。"""
        self.probe(after=(5, 1, 29000))
        self.assertIsNone(bm.apply_one(self.st, self.recs, 'record_x'))

    def test_existing_values_are_kept(self):
        """已有值不覆盖（第二次跑 / API 已填）-> None。"""
        self.st.upsert_match(_row(rank_after=4, rate_after=4))
        self.probe(after=(5, 1, 29000))
        self.assertIsNone(bm.apply_one(self.st, self.recs, 'record_x'))
        self.assertEqual(self.db_row()['my_rank_after'], 4)

    def test_missing_probe_file_is_none(self):
        self.st.upsert_match(_row())
        self.assertIsNone(bm.apply_one(self.st, self.recs, 'nope'))

    def test_broken_probe_file_is_none(self):
        self.st.upsert_match(_row())
        self.probe(raw='{ not json')
        self.assertIsNone(bm.apply_one(self.st, self.recs, 'record_x'))

    def test_unreadable_rank_is_none(self):
        """探测里没有 rank/rate（两者都缺）-> 不猜、返回 None。"""
        self.st.upsert_match(_row())
        self.probe(base=None, after=None)
        self.assertIsNone(bm.apply_one(self.st, self.recs, 'record_x'))

    def test_only_before_is_filled_when_after_present(self):
        """after 已有值、before 还空 -> 只补 before（不因为 after 有值就整条跳过）。"""
        self.st.upsert_match(_row(rank_after=5, rate_after=1))
        self.probe(after=(5, 1, 29000), before=(5, 2))
        msg = bm.apply_one(self.st, self.recs, 'record_x')
        self.assertIn('局前', msg)
        self.assertEqual(self.db_row()['my_rank_before'], 5)


class TestCli(Base):
    """CLI 行为：--list / --dry-run 不许写库。"""

    def test_list_does_not_write(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        code, out = self.run_main('--list')
        self.assertEqual(code, 0)
        self.assertIn('可补 1', out)
        self.assertIsNone(self.db_row()['my_rank_after'])

    def test_apply_dry_run_does_not_write(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        code, out = self.run_main('--apply', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('dry-run', out)
        self.assertIsNone(self.db_row()['my_rank_after'])

    def test_apply_writes(self):
        self.st.upsert_match(_row())
        self.probe(after=(5, 1, 29000))
        code, out = self.run_main('--apply')
        self.assertEqual(code, 0)
        self.assertEqual(self.db_row()['my_rank_after'], 5)

    def test_no_probe_files_message(self):
        code, out = self.run_main('--list')
        self.assertEqual(code, 0)
        self.assertIn('没有段位探测文件', out)


class TestStorageHook(unittest.TestCase):
    """新加的两个 storage 口。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='rankbf2_')
        self.st = Storage(os.path.join(self.d, 'x.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_find_by_record_dir(self):
        self.st.upsert_match(_row())
        self.assertEqual(self.st.find_by_record_dir('record_x')['match_id'],
                         'rec:record_x')
        self.assertIsNone(self.st.find_by_record_dir('record_nope'))

    def test_backfill_returns_empty_for_missing_match(self):
        self.assertEqual(self.st.backfill_my_rank_after('nope', 5, 2), {})

    def test_backfill_reports_kept(self):
        self.st.upsert_match(_row(rank_after=6, rate_after=4))
        acts = self.st.backfill_my_rank_after('rec:record_x', 5, 2)
        self.assertEqual(acts['my_rank_after'], 'kept:6')
        self.assertEqual(acts['my_rate_after'], 'kept:4')

    def test_backfill_before_only_fills_null(self):
        self.st.upsert_match(_row())
        acts = self.st.backfill_my_rank_before('rec:record_x', 5, 2)
        self.assertEqual(acts, {'my_rank_before': 'set', 'my_rate_before': 'set'})
        acts = self.st.backfill_my_rank_before('rec:record_x', 6, 4)
        self.assertEqual(acts['my_rank_before'], 'kept:5')

    def test_backfill_none_is_noop(self):
        self.st.upsert_match(_row())
        self.assertEqual(self.st.backfill_my_rank_after('rec:record_x', None, None), {})
        self.assertIsNone(self.db_row_none())

    def db_row_none(self):
        with self.st._open() as conn:
            row = conn.execute('SELECT my_rank_after FROM matches').fetchone()
        return row['my_rank_after']


if __name__ == '__main__':
    unittest.main()
