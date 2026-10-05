# -*- coding: utf-8 -*-
"""storage.py M3 扩展的单测：三新列 / 老库迁移 / upsert 判负开关 /
apply_record_meta（winbit 优先覆盖 + 冲突记录）/ find_record_pair 三段窗口 / set_result。

全部用临时库（``Storage(tempdir)``），不碰真实 ``data/masterduel.db``。
"""

import os
import sqlite3
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import Storage, MATCH_FIELDS, ORIGIN_RECORD      # noqa: E402


def _row(match_id, started, ended=None, result=None, origin='log', **kw):
    row = {'match_id': match_id, 'started_at': started, 'ended_at': ended,
           'result': result, 'origin': origin, 'mode': 'pvp'}
    row.update(kw)
    return row


class TestSchema(unittest.TestCase):

    def test_new_columns_in_fields_and_schema(self):
        for col in ('record_dir', 'result_note', 'winbits'):
            self.assertIn(col, MATCH_FIELDS)

    def test_fresh_db_has_columns(self):
        tmp = tempfile.mkdtemp()
        try:
            st = Storage(os.path.join(tmp, 'a.db'))
            st.init_schema()
            with st._open() as conn:
                cols = {r['name'] for r in
                        conn.execute('PRAGMA table_info(matches)')}
            self.assertLessEqual({'record_dir', 'result_note', 'winbits'}, cols)
        finally:
            os.remove(os.path.join(tmp, 'a.db'))
            os.rmdir(tmp)

    def test_old_db_migrated(self):
        """老库（没有这三列）打开后自动 ALTER 补上。"""
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, 'old.db')
        try:
            conn = sqlite3.connect(db)
            # 老库 = 全列都在、只缺 M3 四列（executescript 的建索引需要老列齐全）
            from core.storage import MATCH_FIELDS as _MF
            old_cols = [f for f in _MF
                        if f not in ('result_src', 'record_dir',
                                     'result_note', 'winbits')]
            old_cols = [f for f in old_cols
                        if f not in ('match_id', 'started_at')]
            ddl = (', '.join(['match_id TEXT PRIMARY KEY',
                              'started_at TEXT NOT NULL'])
                   + ', ' + ', '.join(
                       f + (' INTEGER' if f == 'created_at' else ' TEXT')
                       for f in old_cols))
            conn.execute('CREATE TABLE matches (%s)' % ddl)
            conn.execute(
                "INSERT INTO matches (match_id, started_at, result,"
                " created_at) VALUES ('abc', '2026-09-01 10:00:00.000',"
                " 'Win', 1)")
            conn.commit()
            conn.close()
            st = Storage(db)
            st.init_schema()
            with st._open() as conn:
                cols = {r['name'] for r in
                        conn.execute('PRAGMA table_info(matches)')}
            self.assertLessEqual({'record_dir', 'result_note', 'winbits'}, cols)
            row = st.get_match('abc')
            self.assertEqual(row['result'], 'Win')
            self.assertIsNone(row['record_dir'])
        finally:
            os.remove(db)
            os.rmdir(tmp)


class TestUpsertJudgeSwitch(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'a.db'))
        self.st.init_schema()

    def tearDown(self):
        self.st.close() if hasattr(self.st, 'close') else None
        os.remove(os.path.join(self.tmp, 'a.db'))
        os.rmdir(self.tmp)

    def test_default_judges_lack_end(self):
        self.st.upsert_match(_row('m1', '2026-09-13 12:00:00.000'))
        self.assertEqual(self.st.get_match('m1')['result'], 'Lose')

    def test_record_rows_opt_out(self):
        """record 行胜负只认 winbit：判不出不许按"没看到结束"硬判负。"""
        self.st.upsert_match(_row('m2', '2026-09-13 12:00:00.000',
                                  origin=ORIGIN_RECORD), judge_lack_end=False)
        self.assertIsNone(self.st.get_match('m2')['result'])


class TestApplyRecordMeta(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'a.db'))
        self.st.init_schema()
        self.st.upsert_match(_row('m1', '2026-09-13 12:05:46.000',
                                  '2026-09-13 12:13:57.000', result='Win'))

    def tearDown(self):
        os.remove(os.path.join(self.tmp, 'a.db'))
        os.rmdir(self.tmp)

    def test_fill_empty(self):
        actions = self.st.apply_record_meta(
            'm1', record_dir='record_x', winbits=[1, 0], result='Win',
            result_src='winbit', result_note='对手的LP已削减至0')
        row = self.st.get_match('m1')
        self.assertEqual(actions['record_dir'], 'set')
        self.assertEqual(actions['winbits'], 'set')
        self.assertEqual(actions['result'], 'unchanged')   # 旧值一致
        self.assertEqual(row['winbits'], [1, 0])           # JSON 还原成 list
        self.assertEqual(row['result_note'], '对手的LP已削减至0')

    def test_idempotent_rerun(self):
        self.st.apply_record_meta('m1', record_dir='record_x', winbits=[1, 0])
        actions = self.st.apply_record_meta('m1', record_dir='record_x',
                                            winbits=[1, 0])
        self.assertEqual(actions['record_dir'], 'unchanged')
        self.assertEqual(actions['winbits'], 'unchanged')

    def test_winbit_overrides_conflicting_result(self):
        """老行 result 是文案推的（判反）—— winbit 优先、允许覆盖、冲突留痕。"""
        self.st.apply_record_meta('m1', winbits=[0, 1], result='Lose')
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Lose')
        self.assertEqual(row['result_src'], 'winbit')
        self.assertIn('result冲突:旧=Win', row['result_note'])

    def test_note_filled_only_when_null(self):
        self.st.apply_record_meta('m1', result_note='文案A')
        actions = self.st.apply_record_meta('m1', result_note='文案B')
        self.assertEqual(actions['result_note'], 'kept')
        self.assertEqual(self.st.get_match('m1')['result_note'], '文案A')

    def test_missing_match(self):
        self.assertEqual(self.st.apply_record_meta('nope', winbits=[1, 0]), {})


class TestFindRecordPair(unittest.TestCase):
    """三段窗口 —— 用 09-13 真实时刻做锚（docs/对局记录格式.md §5.4）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'a.db'))
        self.st.init_schema()
        st = self.st
        st.upsert_match(_row('x', '2026-09-13 12:05:46.000',
                             '2026-09-13 12:13:57.000'))
        st.upsert_match(_row('y', '2026-09-13 12:23:43.000',
                             '2026-09-13 12:29:41.000'))
        st.upsert_match(_row('z', '2026-09-13 12:33:21.000',
                             '2026-09-13 12:36:54.000'))

    def tearDown(self):
        os.remove(os.path.join(self.tmp, 'a.db'))
        os.rmdir(self.tmp)

    def test_tight_window(self):
        """watcher 对局中抓：first_seen 只比 started_at 晚几秒。"""
        row, stage = self.st.find_record_pair('2026-09-13 12:33:26')
        self.assertEqual(stage, 'tight')
        self.assertEqual(row['match_id'], 'z')

    def test_in_progress(self):
        """first_seen 落在对局区间内（watcher 中途启动）。"""
        row, stage = self.st.find_record_pair('2026-09-13 12:27:13')
        self.assertEqual(stage, 'in-progress')
        self.assertEqual(row['match_id'], 'y')

    def test_ended_residue(self):
        """one-shot 结算残影：first_seen 比结束晚 4.5 分钟。"""
        row, stage = self.st.find_record_pair('2026-09-13 12:18:29')
        self.assertEqual(stage, 'ended-residue')
        self.assertEqual(row['match_id'], 'x')

    def test_no_anchor_guard(self):
        """坏目录（无锚点）返回 no-anchor，不抛异常拖垮整批（实现审查 S-2）。"""
        row, stage = self.st.find_record_pair(None)
        self.assertIsNone(row)
        self.assertEqual(stage, 'no-anchor')

    def test_no_candidate(self):
        row, stage = self.st.find_record_pair('2026-09-13 13:00:00')
        self.assertIsNone(row)
        self.assertEqual(stage, 'no-candidate')

    def test_ambiguous_ended(self):
        # 单独建库（setUp 的 x/y/z 行会截胡残影窗口）
        db = os.path.join(self.tmp, 'b.db')
        st = Storage(db)
        st.init_schema()
        st.upsert_match(_row('p', '2026-09-13 12:00:00.000',
                             '2026-09-13 12:10:00.000'))
        st.upsert_match(_row('q', '2026-09-13 12:08:00.000',
                             '2026-09-13 12:10:30.000'))
        # ended 窗口 [12:03:00, 12:11:00] 同时含 p/q，间隔 30s < 60 -> 歧义
        row, stage = st.find_record_pair('2026-09-13 12:11:00')
        self.assertIsNone(row)
        self.assertEqual(stage, 'ambiguous-ended')
        os.remove(db)

    def test_record_rows_not_eligible(self):
        """并档目标只找 log/api 行 —— record 行不能互相当锚。"""
        self.st.upsert_match(_row('r1', '2026-09-13 12:50:00.000',
                                  origin=ORIGIN_RECORD), judge_lack_end=False)
        row, stage = self.st.find_record_pair('2026-09-13 12:50:10')
        self.assertIsNone(row)


class TestSetResult(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'a.db'))
        self.st.init_schema()
        self.st.upsert_match(_row('m1', '2026-09-13 12:00:00.000'))

    def tearDown(self):
        os.remove(os.path.join(self.tmp, 'a.db'))
        os.rmdir(self.tmp)

    def test_set_result(self):
        self.assertEqual(self.st.set_result('m1', 'Win'), 1)
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'manual')

    def test_rejects_invalid(self):
        with self.assertRaises(AssertionError):
            self.st.set_result('m1', '不知道')


if __name__ == '__main__':
    unittest.main()


class TestManualResultWins(unittest.TestCase):
    """★ 人工修复的胜负**不许**被自动写入覆盖（用户 2026-09-18 深夜口径）。

    实锤事故：手动补填的 result 被下一次 `tools/import_duel_records.py --apply`
    冲掉 —— 那条链路有**两条**写入路径（新行走 `upsert_match`、并档行走
    `apply_record_meta`），所以两条都得守。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = Storage(os.path.join(self.tmp, 'a.db'))
        self.st.init_schema()
        self.st.upsert_match(_row('m1', '2026-09-13 12:05:46.000',
                                  '2026-09-13 12:13:57.000'))
        # 手动修正（= 界面/CLI 那条路）
        self.st.repair_match_result('m1', result='Win', result_src='manual')

    def tearDown(self):
        os.remove(os.path.join(self.tmp, 'a.db'))
        os.rmdir(self.tmp)

    def test_upsert_keeps_manual_result(self):
        """重跑 import 的 upsert（新值相反）→ 人工值原样（连 src 一起）。"""
        self.st.upsert_match(_row('m1', '2026-09-13 12:05:46.000',
                                  '2026-09-13 12:13:57.000', result='Lose',
                                  origin=ORIGIN_RECORD))
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'manual')

    def test_upsert_null_keeps_manual_result(self):
        """record 行重跑（result 为 NULL）也不许把人工值抹成空。"""
        self.st.upsert_match(_row('m1', '2026-09-13 12:05:46.000',
                                  origin=ORIGIN_RECORD))
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'manual')

    def test_apply_record_meta_keeps_manual_result(self):
        """并档路径：winbit 与人工值相反 → 人工值留下，winbit 只当证据。"""
        actions = self.st.apply_record_meta('m1', record_dir='record_x',
                                            winbits=[0, 1], result='Lose',
                                            result_src='winbit')
        row = self.st.get_match('m1')
        self.assertEqual(actions['result'], 'kept:manual')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'manual')
        self.assertEqual(row['winbits'], [0, 1])          # 证据照收
        self.assertIn('result冲突:人工=Win 自动=Lose', row['result_note'])

    def test_conflict_note_is_idempotent(self):
        """重跑 import 不许把冲突备注越堆越长（每次追加一份 = 注释爆炸）。"""
        for _ in range(3):
            self.st.apply_record_meta('m1', winbits=[0, 1], result='Lose',
                                      result_src='winbit')
        note = self.st.get_match('m1')['result_note'] or ''
        self.assertEqual(note.count('result冲突:人工'), 1)

    def test_agreeing_auto_value_adds_no_note(self):
        actions = self.st.apply_record_meta('m1', winbits=[1, 0], result='Win',
                                            result_src='winbit')
        row = self.st.get_match('m1')
        self.assertEqual(actions['result'], 'kept:manual')
        self.assertIsNone(row['result_note'])

    def test_clearing_manual_lets_auto_fill_again(self):
        """清除修正（result + src 一起置 NULL）后，自动解析可以再填。"""
        self.st.repair_match_result('m1', result=None, result_src=None)
        self.st.apply_record_meta('m1', winbits=[0, 1], result='Lose',
                                  result_src='winbit')
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Lose')
        self.assertEqual(row['result_src'], 'winbit')

    def test_manual_stamp_without_value_is_not_locked(self):
        """★ 只有「人工 + 有值」才锁：`manual` 但 result 空的行必须能被自动填上。

        实测库里就有一行这样（record_20260918_194446：手补时只落了 src）。
        锁了它 = 那局**永远填不上**，比没修还坏。
        """
        self.st.repair_match_result('m1', result=None, result_src='manual')
        self.st.apply_record_meta('m1', winbits=[0, 1], result='Lose',
                                  result_src='winbit')
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Lose')
        self.assertEqual(row['result_src'], 'winbit')

    def test_manual_stamp_without_value_upsert_fills_too(self):
        self.st.repair_match_result('m1', result=None, result_src='manual')
        self.st.upsert_match(_row('m1', '2026-09-13 12:05:46.000',
                                  '2026-09-13 12:13:57.000', result='Lose'))
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Lose')

    def test_non_manual_row_still_overridden(self):
        """没有 manual 戳的行照旧 winbit 优先 —— 别把既有规则改坏。"""
        self.st.upsert_match(_row('m2', '2026-09-13 13:00:00.000',
                                  '2026-09-13 13:10:00.000', result='Win'))
        self.st.apply_record_meta('m2', winbits=[0, 1], result='Lose',
                                  result_src='winbit')
        row = self.st.get_match('m2')
        self.assertEqual(row['result'], 'Lose')
        self.assertEqual(row['result_src'], 'winbit')
