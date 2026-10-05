# -*- coding: utf-8 -*-
"""反推卡组的**回测**与 `--guess` 入口的单测（反推计划 v2 · 刀 3/4）。

全是临时库 + 合成参照库，**不联网、不碰 data/masterduel.db**。
盯四件事：
  1. 真值归一：`opp_decks.name`（中文名 / 别名）→ `meta_decks` 站内名
  2. 四分组计数（候选内 / 口径外 / 清单外 / 剔除）+ top1 命中
  3. 剔除清单逐局打印原因（混轴 + 污染局）
  4. 露出卡体检（尾→首残留）在回测里自动跑
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import mark_opponent as mo                                 # noqa: E402
from core import deck_guess as dg                          # noqa: E402
from core.storage import Storage, MODE_PVP                 # noqa: E402

META = [
    dict(name='Branded', kind='deck_type', name_cn='烙印', tier='Tier2', power=8.0),
    dict(name='Maliss', kind='deck_type', name_cn='码丽丝', tier='Trending', power=2.0),
    dict(name='Dracotail', kind='deck_type', name_cn='烙印星辰', tier='Tier1', power=12.0),
]


def cards(start, n=5, per=100):
    return {str(start + i): dict(per=per, at=3) for i in range(n)}


REF = {'version': 1, 'fetched_at': '2026-09-14T00:00:00', 'decks': {
    'Branded': dict(tier='Tier2', source_name='Branded', total=20,
                    cards=cards(900001)),
    'Maliss': dict(tier='Trending', source_name='Maliss', total=9,
                   cards=cards(900011)),
    'Thunders': dict(tier='Tier1', source_name='Thunders', total=5,
                     cards=cards(900031)),
}}


def mk(mid, started, deck_name, ids, deck_id=1):
    return dict(
        match_id=mid, started_at=started, ended_at=started,
        my_deck_id='d1', my_deck_name='闪刀姬', deck_source='log',
        seat=0, seat_evidence='-', won_initiative=None, went_first=True,
        first_player=0, result='Win', total_turns=3, duration_sec=120,
        opp_deck_id=deck_id, opp_deck_name=deck_name, opp_deck_note=None,
        opp_revealed=['卡%s' % i for i in ids], opp_revealed_ids=list(ids),
        mode=MODE_PVP, game_mode=3,
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='bt_')
        self.db = os.path.join(self.d, 'masterduel.db')
        self.st = Storage(self.db)
        self.st.init_schema()
        self.st.replace_meta_decks(META)
        # m1 候选内(Branded 5/5) / m2 口径外(Maliss 5/5) /
        # m3 清单外(灵兽，参照库里没这条) / m4+m5 剔除（混轴 + 污染局，且是残留对）
        self.st.upsert_match(mk('m1', '2026-09-06 18:00:00.000', '烙印',
                                range(900001, 900006)))
        self.st.upsert_match(mk('m2', '2026-09-06 18:10:00.000', '码丽丝',
                                range(900011, 900016)))
        self.st.upsert_match(mk('m3', '2026-09-06 18:20:00.000', '灵兽',
                                range(900031, 900036)))
        self.st.upsert_match(mk('m4', '2026-09-06 18:30:00.000', '混轴卡组',
                                [900051, 900052]))
        self.st.upsert_match(mk('dd7650f9d39b5c39', '2026-09-06 18:40:00.000',
                                '烙印星辰', [900052, 900051, 900053]))
        self.index, self.groups = dg.prepare(REF)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def backtest(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mo.cmd_backtest(self.st, self.index, self.groups)
        return rc, buf.getvalue()


class TestBacktest(Base):
    def test_groups_and_hits(self):
        rc, out = self.backtest()
        self.assertEqual(rc, 0, out)
        self.assertIn('回测：5 局', out)
        self.assertIn('候选内：1 局，top1 命中 1（100.0%）', out)
        self.assertIn('口径外：1 局，top1 命中 1（100.0%）', out)
        self.assertIn('清单外：1 局，top1 命中 0（0.0%）', out)
        self.assertIn('剔除：2 局（不进分母）', out)
        # 真值归一：中文名「烙印」→ Branded；**显示名优先**（烙印（Branded））
        self.assertIn('真身 烙印（Branded）', out)
        self.assertIn('真身 码丽丝（Maliss）', out)
        self.assertIn('真身 ？', out, '清单外的真值必须是「？」，不许瞎猜')

    def test_exclusion_reasons_printed(self):
        rc, out = self.backtest()
        self.assertIn('无单一真身（混轴）', out)
        self.assertIn('露出卡被上一局污染', out)

    def test_residue_scan_runs(self):
        rc, out = self.backtest()
        self.assertIn('露出卡体检', out)
        self.assertIn('→', out)

    def test_prefill_gate_reported(self):
        """口径外的真身永远不预填 —— 回测要把「被闸门拦」的条数报出来。"""
        rc, out = self.backtest()
        self.assertIn('被闸门拦（不预填）', out)

    def test_no_reference_is_a_clear_error(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mo.cmd_backtest(self.st, None, None)
        self.assertEqual(rc, 1)
        self.assertIn('fetch_mdm_decks', buf.getvalue())


class TestGuessCli(Base):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mo.main(['--db', self.db,
                          '--reference', os.path.join(self.d, 'ref.json')] + list(argv))
        return rc, buf.getvalue()

    def write_ref(self):
        import json
        with open(os.path.join(self.d, 'ref.json'), 'w', encoding='utf-8') as f:
            json.dump(REF, f, ensure_ascii=False)

    def test_guess_by_match_id_prefix(self):
        self.write_ref()
        rc, out = self.run_cli('--guess', 'm1')
        self.assertEqual(rc, 0, out)
        self.assertIn('烙印（Branded）', out, '候选表要显示中文名（覆盖表口径）')
        self.assertIn('命中 5/5', out)
        self.assertIn('建议', out)

    def test_guess_last_is_newest_with_cards(self):
        """`--guess last` = 库里最新一局有露出卡的（回放不进队列，只能这么指）。"""
        self.write_ref()
        self.st.upsert_match(mk('m9', '2026-09-06 19:00:00.000', '未标注局',
                                range(900001, 900006)))
        rc, out = self.run_cli('--guess', 'last')
        self.assertEqual(rc, 0, out)
        self.assertIn('09-06 19:00', out)
        self.assertIn('命中 5/5', out)
        # 中文别名也认
        rc, out = self.run_cli('--guess', '最新')
        self.assertIn('09-06 19:00', out)

    def test_guess_without_target_errors(self):
        rc, out = self.run_cli('--guess')
        self.assertEqual(rc, 1)
        self.assertIn('需要目标', out)

    def test_missing_reference_degrades(self):
        rc, out = self.run_cli('--guess', 'm1')       # 没写 ref.json
        self.assertEqual(rc, 1)
        self.assertIn('fetch_mdm_decks', out)


if __name__ == '__main__':
    unittest.main()
