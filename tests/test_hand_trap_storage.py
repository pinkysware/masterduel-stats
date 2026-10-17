# -*- coding: utf-8 -*-
"""手坑两列 + 回填工具的单测。

盯三件事：
  1. **加列四处联动**（SCHEMA_SQL / ADDED_COLUMNS / MATCH_FIELDS / KEEP_IF_NEW_NULL）
     —— 漏一处就是「采集器写了也静默丢」或「重跑把已回填的值冲成 NULL」；
  2. `KEEP_IF_NEW_NULL` 语义：重跑 import 时新值 NULL 不许冲掉旧值，
     但 `[]`（确实没用过）是**有值**，也不许被冲掉；
  3. 回填工具读改写 duel.json（旧格式没键 -> 补上；再跑幂等）。
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

from core import duel_record as dr                     # noqa: E402
from core import storage as st_mod                     # noqa: E402
from core.storage import Storage                       # noqa: E402
import backfill_hand_traps as bht                      # noqa: E402


class ColumnLinkageTest(unittest.TestCase):
    """加列四处联动（审查 P2-2）—— 纯静态断言，不建库。"""

    def test_in_match_fields(self):
        self.assertIn('my_traps', st_mod.MATCH_FIELDS)
        self.assertIn('opp_traps', st_mod.MATCH_FIELDS)

    def test_in_added_columns(self):
        cols = [c for c, _t in st_mod.ADDED_COLUMNS]
        self.assertIn('my_traps', cols)
        self.assertIn('opp_traps', cols)

    def test_in_keep_if_new_null(self):
        self.assertIn('my_traps', st_mod.KEEP_IF_NEW_NULL)
        self.assertIn('opp_traps', st_mod.KEEP_IF_NEW_NULL)

    def test_in_schema_sql(self):
        self.assertIn('my_traps', st_mod.SCHEMA_SQL)
        self.assertIn('opp_traps', st_mod.SCHEMA_SQL)

    def test_in_json_fields(self):
        self.assertIn('my_traps', st_mod.JSON_FIELDS)
        self.assertIn('opp_traps', st_mod.JSON_FIELDS)

    def test_upsert_keeps_on_null(self):
        """生成的 SQL 必须是 COALESCE（新值 NULL 保留旧值）。"""
        sql = st_mod.INSERT_MATCH_SQL
        for c in ('my_traps', 'opp_traps'):
            self.assertIn('%s = COALESCE(excluded.%s, matches.%s)' % (c, c, c),
                          sql)

    def test_migration_adds_columns(self):
        """老库跑 init_schema 后两列都在。"""
        d = tempfile.mkdtemp(prefix='ht_mig_')
        try:
            st = Storage(os.path.join(d, 'x.db'))
            st.init_schema()
            conn = st._open()
            with conn as c:
                cols = [r[1] for r in c.execute('PRAGMA table_info(matches)')]
            self.assertIn('my_traps', cols)
            self.assertIn('opp_traps', cols)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class UpsertSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='ht_up_')
        self.st = Storage(os.path.join(self.d, 'x.db'))
        self.st.init_schema()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _row(self, **kw):
        r = {'match_id': 'm1', 'started_at': '2026-10-16 12:00:00',
             'origin': 'record', 'mode': 'pvp'}
        r.update(kw)
        return r

    def test_json_roundtrip(self):
        val = [{'turn': 0, 'id': 9455, 'key': 'maxx', 'name_cn': '增殖的Z'}]
        self.st.upsert_match(self._row(my_traps=val))
        back = self.st.get_match('m1')
        self.assertEqual(back.get('my_traps'), val)

    def test_empty_list_is_a_value(self):
        """`[]` = 确实没用过 —— 必须存下来、且不被后来的 NULL 冲掉。"""
        self.st.upsert_match(self._row(my_traps=[], opp_traps=[]))
        self.assertEqual(self.st.get_match('m1').get('my_traps'), [])
        # 重跑一个 None 的行
        self.st.upsert_match(self._row(my_traps=None))
        self.assertEqual(self.st.get_match('m1').get('my_traps'), [])

    def test_null_new_keeps_old(self):
        val = [{'turn': 1, 'id': 12950, 'key': 'ash', 'name_cn': '灰流丽'}]
        self.st.upsert_match(self._row(my_traps=val))
        # 重跑（新值 NULL，如无 record_dir / 无 ShowTurn 的局）不许冲掉
        self.st.upsert_match(self._row(my_traps=None, opp_traps=None))
        self.assertEqual(self.st.get_match('m1').get('my_traps'), val)

    def test_set_by_record(self):
        self.st.upsert_match(self._row(record_dir='record_x'))
        n = self.st.set_hand_traps_by_record(
            'record_x', [{'turn': 0, 'id': 9455, 'key': 'maxx',
                          'name_cn': '增殖的Z'}], [])
        self.assertEqual(n, 1)
        back = self.st.get_match('m1')
        self.assertEqual(back.get('opp_traps'), [])
        self.assertEqual(back.get('my_traps')[0]['key'], 'maxx')

    def test_set_by_record_none_none_noop(self):
        """两列都 None（判不出）-> 不动库。"""
        self.st.upsert_match(self._row(record_dir='record_x'))
        n = self.st.set_hand_traps_by_record('record_x', None, None)
        self.assertEqual(n, 0)
        self.assertIsNone(self.st.get_match('m1').get('my_traps'))

    def test_apply_record_meta_merge(self):
        """并档路径：旧行为空才填；已有值保留。"""
        self.st.upsert_match(self._row())
        val = [{'turn': 0, 'id': 9455, 'key': 'maxx', 'name_cn': '增殖的Z'}]
        actions = self.st.apply_record_meta('m1', my_traps=val, opp_traps=[])
        self.assertEqual(actions.get('my_traps'), 'set')
        self.assertEqual(self.st.get_match('m1').get('my_traps'), val)
        # 再来一次（旧值已有）-> 保留
        val2 = [{'turn': 2, 'id': 9279, 'key': 'droll', 'name_cn': '抽牌小丑与封锁鸟'}]
        actions2 = self.st.apply_record_meta('m1', my_traps=val2)
        self.assertEqual(actions2.get('my_traps'), 'kept')
        self.assertEqual(self.st.get_match('m1').get('my_traps'), val)


class BackfillToolTest(unittest.TestCase):
    """回填工具：读改写 duel.json + 幂等。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='ht_bf_')
        self.recs = os.path.join(self.d, 'duel_records')
        self.fix = os.path.join(PROJ, 'tests', 'fixtures', 'duel_records',
                                'record_20260913_123714')
        self.rec = os.path.join(self.recs, 'record_20260913_123714')
        os.makedirs(self.recs)
        shutil.copytree(self.fix, self.rec)
        self.db = os.path.join(self.d, 'x.db')
        # 先按新解码器生成一份 duel.json，再模拟「存量旧文件」（删掉新键）
        st = Storage(self.db)
        st.init_schema()
        duel = dr.decode_record(dr.scan_record(self.rec))
        dr.write_duel_json(self.rec, duel, meta_updates={})
        self._strip()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _strip(self):
        p = os.path.join(self.rec, dr.DUEL_JSON_NAME)
        o = self._read_json(p)
        o['summary'].pop('hand_traps', None)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            json.dump(o, f, ensure_ascii=False, indent=1)

    def _load(self):
        return dr.read_duel_json(self.rec)

    def _read_json(self, path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def test_no_json_status(self):
        empty = os.path.join(self.recs, 'record_empty')
        os.makedirs(empty)
        status, mine, opp = bht.backfill_one(empty, apply=False)
        self.assertEqual(status, 'no-json')

    def test_dry_run_does_not_write(self):
        status, _m, _o = bht.backfill_one(self.rec, apply=False)
        self.assertEqual(status, 'planned')
        self.assertNotIn('hand_traps', self._load()['summary'])

    def test_apply_writes_key(self):
        status, mine, opp = bht.backfill_one(self.rec, apply=True)
        self.assertEqual(status, 'written')
        self.assertIn('hand_traps', self._load()['summary'])
        # 该 fixture seat=None -> 两列 None
        self.assertIsNone(mine)
        self.assertIsNone(opp)

    def test_idempotent(self):
        bht.backfill_one(self.rec, apply=True)
        status, _m, _o = bht.backfill_one(self.rec, apply=True)
        self.assertEqual(status, 'up-to-date')

    def test_run_writes_db_row(self):
        st = Storage(self.db)
        st.upsert_match({'match_id': 'rec:record_20260913_123714',
                         'started_at': '2026-09-13 12:37:14',
                         'record_dir': 'record_20260913_123714',
                         'origin': 'record', 'mode': 'pvp'})
        # 该 fixture seat=None -> 两列判不出 -> 不写库
        report = bht.run(records_dir=self.recs, storage=st, apply=True)
        self.assertEqual(report['written_json'], 1)
        self.assertEqual(report['rows_updated'], 0)

    def test_run_with_seat_writes_db(self):
        """给 duel.json 塞一个 seat，验证真写库。"""
        p = os.path.join(self.rec, dr.DUEL_JSON_NAME)
        o = self._read_json(p)
        o['meta']['seat'] = 0
        with open(p, 'w', encoding='utf-8', newline='') as f:
            json.dump(o, f, ensure_ascii=False, indent=1)
        st = Storage(self.db)
        st.upsert_match({'match_id': 'rec:record_20260913_123714',
                         'started_at': '2026-09-13 12:37:14',
                         'record_dir': 'record_20260913_123714',
                         'origin': 'record', 'mode': 'pvp'})
        report = bht.run(records_dir=self.recs, storage=st, apply=True)
        self.assertEqual(report['rows_updated'], 1)
        back = st.get_match('rec:record_20260913_123714')
        self.assertIsNotNone(back.get('my_traps'))


if __name__ == '__main__':
    unittest.main()

class ToRowTest(unittest.TestCase):
    """`duel_record.to_row()` 携带手坑两列（入库路径的出口）。"""

    def _duel(self, seat, has_turns=True):
        #: has_turns=False = **整局没有 ShowTurn**（秒投/掉线局）-> 两列 NULL
        events = []
        if has_turns:
            events.append({'seq': 1, 'label': 'ShowTurn', 'turn': 0, 'player': 1})
        events.append({'seq': 5, 'label': 'ShowAction',
                       'kind_name': 'HAPPEN', 'zone_name': 'Hand',
                       'player': 0,
                       'cards': [{'card_id': 9455, 'player': 0, 'side': 'l'}]})
        return {
            'record_id': 'record_x',
            'meta': {'seat': seat, 'started_at': '2026-10-16 12:00:00'},
            'summary': {'first_player': 1,
                        'hand_traps': dr.summarize_hand_traps(events)},
        }

    def test_row_carries_traps(self):
        row = dr.to_row(self._duel(seat=1))
        self.assertIn('my_traps', row)
        self.assertIn('opp_traps', row)
        # seat=1 -> player0 是对手
        self.assertEqual([x['key'] for x in row['opp_traps']], ['maxx'])
        self.assertEqual(row['my_traps'], [])

    def test_row_null_when_seat_missing(self):
        row = dr.to_row(self._duel(seat=None))
        self.assertIsNone(row['my_traps'])
        self.assertIsNone(row['opp_traps'])

    def test_row_null_when_no_turns(self):
        row = dr.to_row(self._duel(seat=0, has_turns=False))
        self.assertIsNone(row['my_traps'])
        self.assertIsNone(row['opp_traps'])

    def test_row_upsert_and_readback(self):
        d = tempfile.mkdtemp(prefix='ht_torow_')
        try:
            st = Storage(os.path.join(d, 'x.db'))
            st.init_schema()
            row = dr.to_row(self._duel(seat=0))
            row['origin'] = 'record'
            row['mode'] = 'pvp'
            st.upsert_match(row)
            back = st.get_match(row['match_id'])
            self.assertEqual([x['key'] for x in back['my_traps']], ['maxx'])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

class CroppedArtTest(unittest.TestCase):
    """手坑格用**纯卡绘**（去卡框）—— 用户 2026-10-16 实测反馈。"""

    def test_cropped_functions_exist(self):
        from core import card_art
        self.assertTrue(hasattr(card_art, 'cropped_path'))
        self.assertTrue(hasattr(card_art, 'fetch_cropped'))
        #: 与整张卡那一族**分开**（别互相覆盖）
        self.assertNotEqual(card_art.CROPPED_DIR, card_art.CACHE_DIR)
        self.assertIn('cards_cropped', card_art.CROPPED_URL)

    def test_trap_items_use_cropped(self):
        """`ui_state._trap_items` 的 img 走 cropped_path（不是 art_path）。"""
        import inspect
        from core import ui_state
        src = inspect.getsource(ui_state._trap_items)
        self.assertIn('_cropped_path', src)

    def test_cropped_path_none_when_uncached(self):
        from core import card_art
        #: 一个必然没缓存的假 id -> None（纯本地、不联网）
        self.assertIsNone(card_art.cropped_path(99999999))


class FixedLabelElideTest(unittest.TestCase):
    """`_fixed` 的文字超宽要 elide（否则 QLabel 的 minHint 撑大整行 -> 横向滚动条）。"""

    def test_fixed_elides_and_tooltips(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui import dashboard
        #: 用**足够长**的文本，任何字体下都必然超 52px（别依赖具体字宽）
        long_text = '这是一个特别特别长的对手名字用来测试省略'
        lab = dashboard._fixed(long_text, 52, 14)
        self.assertEqual(lab.width(), 52)
        self.assertNotEqual(lab.text(), long_text)   # 被 elide 了
        self.assertEqual(lab.toolTip(), long_text)   # 全文进 tooltip


if __name__ == '__main__':
    unittest.main()
