# -*- coding: utf-8 -*-
"""M5 标注 CLI（`tools/mark_opponent.py`）的单测。

只测**能用代码验的部分**：目标解析、卡组名解析、绑定 / 撤销 / 新建条目、
`--list` / `--cross` 的输出不炸。逐局交互那一层（`input()`）留给用户本地跑。

全部用临时库（`Storage(tempdir)`），不碰真实的 `data/masterduel.db`。
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import mark_opponent as mo                                    # noqa: E402
from core import deck_guess as dg                             # noqa: E402
from core.storage import Storage, MODE_PVP                    # noqa: E402

#: 一份小号 meta 清单，覆盖「中文名 / 别名 / 前缀撞车」三种情况
META = [
    dict(name='Branded', kind='deck_type', name_cn='烙印', tier='Tier1', power=9.0),
    dict(name='Tearlaments', kind='deck_type', name_cn='泪冠哀歌', aliases=['珠泪'],
         tier='Tier1', power=8.0),
    dict(name='Vanquish Soul', kind='deck_type', name_cn='斗魂', tier='Tier3',
         power=1.0),
    dict(name='Vanquish Soul K9', kind='deck_type', name_cn='K9VS', tier='Tier1',
         power=7.0),
]


def mk(mid, started, result='Win', deck_name='闪刀姬', mode=MODE_PVP,
       game_mode=3, **kw):
    """和 test_storage.mk 同形状的最小对局记录。"""
    row = dict(
        match_id=mid, started_at=started,
        ended_at=(None if result == '(未结束)' else started),
        my_deck_id='d1', my_deck_name=deck_name, deck_source='log',
        seat=0, seat_evidence='-', won_initiative=None, went_first=True,
        first_player=0, result=result, total_turns=3, duration_sec=120,
        opp_deck_id=None, opp_deck_name=None, opp_deck_note=None,
        opp_revealed=['灰流丽'], opp_revealed_ids=[12950],
        mode=mode, game_mode=game_mode,
    )
    row.update(kw)
    return row


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='m5test_')
        self.db = os.path.join(self.d, 'masterduel.db')
        self.st = Storage(self.db)
        self.st.init_schema()
        self.st.replace_meta_decks(META)
        for r in (mk('aaaa1111', '2026-09-10 10:00:00.000', 'Win'),
                  mk('bbbb2222', '2026-09-10 11:00:00.000', 'Lose')):
            self.st.upsert_match(r)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_cli(self, *argv):
        """跑一次 main()，返回 (rc, 标准输出)。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mo.main(['--db', self.db] + list(argv))
        return rc, buf.getvalue()


class TestToken(Base):
    def test_match_by_token(self):
        """数字 = 列表序号（1 起）；非数字 = match_id 前缀唯一匹配。"""
        rows = self.st.get_unmarked_matches(10)
        self.assertEqual(rows[0]['match_id'], 'bbbb2222')     # 倒序，最新在前
        self.assertEqual(mo.match_by_token(rows, '1')[0]['match_id'], 'bbbb2222')
        self.assertEqual(mo.match_by_token(rows, '2')[0]['match_id'], 'aaaa1111')
        self.assertEqual(mo.match_by_token(rows, 'aaaa')[0]['match_id'], 'aaaa1111')
        for bad in ('', '9', 'zzz'):
            row, err = mo.match_by_token(rows, bad)
            self.assertIsNone(row, bad)
            self.assertTrue(err, bad)

    def test_match_by_token_rejects_ambiguous_prefix(self):
        rows = [{'match_id': 'abcd1111'}, {'match_id': 'abcd2222'}]
        row, err = mo.match_by_token(rows, 'abcd')
        self.assertIsNone(row)
        self.assertIn('撞了 2 局', err)


class TestResolveDeck(Base):
    def test_alias_and_cn_name_hit_same_row(self):
        """「珠泪」（别名）和「泪冠哀歌」（中文名）都落到同一条。"""
        by_alias, err1 = mo.resolve_deck(self.st, '珠泪')
        by_cn, err2 = mo.resolve_deck(self.st, '泪冠哀歌')
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        self.assertEqual(by_alias['name'], 'Tearlaments')
        self.assertEqual(by_cn['name'], 'Tearlaments')

    def test_ambiguous_and_missing(self):
        deck, err = mo.resolve_deck(self.st, 'vanquish')
        self.assertIsNone(deck, '前缀撞车时必须让人写清楚，不能瞎猜')
        self.assertIn('匹配到多条', err)
        deck, err = mo.resolve_deck(self.st, '不存在的卡组')
        self.assertIsNone(deck)
        self.assertIn('没有', err)
        self.assertEqual(mo.deck_display(self.st.search_meta_decks('烙印')[0]), '烙印')


class TestCli(Base):
    def test_set_then_cross(self):
        """--set 绑一局 -> 待标注少一条 -> --cross 里出现这个对手。"""
        rc, out = self.run_cli('--set', '1', '珠泪', '--note', '掉线判负')
        self.assertEqual(rc, 0, out)
        self.assertIn('泪冠哀歌', out)
        self.assertEqual(len(self.st.get_unmarked_matches(10)), 1)
        row = self.st.get_marked_matches(1)[0]
        self.assertEqual(row['opp_deck_name'], '泪冠哀歌')
        self.assertEqual(row['opp_deck_note'], '掉线判负')

        rc, out = self.run_cli('--cross')
        self.assertIn('泪冠哀歌', out)
        self.assertIn('我方 闪刀姬', out)

    def test_set_new_deck_needs_flag(self):
        """清单里没有的名字：不加 --new 就报错，加了才建条目。"""
        rc, out = self.run_cli('--set', '1', '自研卡组')
        self.assertEqual(rc, 1)
        self.assertIn('--new', out)
        self.assertEqual(self.st.list_opp_decks(), [])

        rc, out = self.run_cli('--set', '1', '自研卡组', '--new')
        self.assertEqual(rc, 0, out)
        got = self.st.list_opp_decks()
        self.assertEqual([(n, cnt) for _, n, _, cnt in got], [('自研卡组', 1)])

    def test_clear_undoes(self):
        """标错的能撤销：已标注队列里按序号 --clear。"""
        self.run_cli('--set', '1', '烙印')
        rc, out = self.run_cli('--list', '--marked')
        self.assertIn('烙印', out)
        rc, out = self.run_cli('--set', '1', '--clear')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.st.get_marked_matches(), [])
        self.assertEqual(self.st.list_opp_decks()[0][3], 0)   # 计数跟着回落

    def test_list_shows_revealed_cards(self):
        rc, out = self.run_cli('--list', '5')
        self.assertEqual(rc, 0)
        self.assertIn('待标注 2 局', out)
        self.assertIn('灰流丽', out)          # 对手打出的卡是唯一线索，必须打出来

    def test_unknown_card_id_marked(self):
        """M4 P2-2：卡表里查不到的 id 要显式标「未知」（不只给个 #id）。"""
        self.st.upsert_match(mk('cccc3333', '2026-09-10 12:00:00.000',
                                opp_revealed_ids=[999999, 12950],
                                opp_revealed=['', '灰流丽']))
        rc, out = self.run_cli('--list', '5')
        self.assertEqual(rc, 0, out)
        self.assertIn('#999999（未知）', out)
        self.assertIn('灰流丽', out)
        self.assertNotIn('#12950', out, '表里有的卡不该被打成未知')

    def test_lang_switches_card_names(self):
        """--lang 只换显示语言；翻不出来时退回库里那份中文快照。"""
        rc, out = self.run_cli('--list', '5', '--lang', 'jp')
        self.assertEqual(rc, 0)
        self.assertIn('灰流うらら', out)
        rc, out = self.run_cli('--list', '5')
        self.assertIn('灰流丽', out)

    def test_no_args_prints_help(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mo.main([])
        self.assertEqual(rc, 1)
        self.assertIn('--interactive', buf.getvalue())


#: 合成参照库：Branded 带 5 张签名卡（900001~900005）+ 灰流丽（12950，通用）
REF = {'version': 1, 'fetched_at': 'x', 'decks': {
    'Branded': dict(tier='Tier2', source_name='Branded', total=20,
                    cards={**{str(i): dict(per=100, at=3)
                              for i in range(900001, 900006)},
                           '12950': dict(per=100, at=3)}),
}}

BIG = [900001, 900002, 900003, 900004, 900005]


class TestUnknownBefore(Base):
    """★ `--unknown-before`：把过期批次标成「未知卡组」（用户 2026-09-17 定）。

    场景：游戏内对战记录只留最近一段 + 日志会轮转 → 更早的局**永远查不回来**。
    与其让它们永远挂在待标注队列里（「还剩多少没标」永远不等于 0），
    不如明确记成「未知卡组」。
    """

    def setUp(self):
        super().setUp()
        self.st.upsert_match(mk('dddd4444', '2026-09-01 10:00:00.000', 'Lose'))
        # 加一条「更新」的局，用来验证**只动指定的批次**
        self.st.upsert_match(mk('cccc3333', '2026-09-20 10:00:00.000', 'Win'))

    def test_dry_run_does_not_write(self):
        rc, out = self.run_cli('--unknown-before', '2026-09-06', '--dry-run')
        self.assertEqual(rc, 0, out)
        self.assertIn('[dry-run]', out)
        self.assertIn('2026-09-06', out)                   # 提到了日期
        self.assertEqual(len(self.st.get_unmarked_matches(10)), 4)  # 一条没动

    def test_marks_only_older_unmarked(self):
        """只标「早于该日期 + 未标注」的局；更新的局 / 已标注的都不碰。"""
        # ⚠️ 要真的「已标注」必须给 opp_deck_id —— 队列判据是 `opp_deck_id IS NULL`，
        #    只写 name 快照（mark_opp_deck(name=…)）那局**仍在队列里**（既有语义）。
        oid = self.st.upsert_opp_deck('Branded')
        self.st.mark_opp_deck('dddd4444', opp_deck_id=oid, name='烙印')
        rc, out = self.run_cli('--unknown-before', '2026-09-06')
        self.assertEqual(rc, 0, out)
        # 09-01 那条已标：不该被覆盖成「未知卡组」
        self.assertEqual(self.st.get_match('dddd4444')['opp_deck_name'], '烙印')
        # 09-10 / 09-20 那两条比日期新：仍在队列里
        left = {r['match_id'] for r in self.st.get_unmarked_matches(10)}
        self.assertEqual(left, {'aaaa1111', 'bbbb2222', 'cccc3333'})

    def test_unknown_and_generic_are_separate_entries(self):
        """★「未知卡组」与「混轴卡组」必须是**两个独立条目**（用户 2026-09-17 明确）。

        混轴只是统称（用户之后会用自定义卡组名替换成具体名字），
        未知卡组是「永远填不上」的终态 —— 合并了就再也分不出来了。
        """
        from core import ui_actions
        gid = self.st.upsert_opp_deck('混轴卡组')
        self.st.mark_opp_deck('aaaa1111', opp_deck_id=gid, name='混轴卡组')
        ok, _msg = ui_actions.mark_unknown(self.st, 'bbbb2222')
        self.assertTrue(ok)
        rows = {o[0]: o[1] for o in self.st.list_opp_decks()
                if o[1] in ('未知卡组', '混轴卡组')}
        self.assertEqual(len(rows), 2)                     # 两个独立条目（不同 id）
        self.assertEqual(set(rows.values()), {'未知卡组', '混轴卡组'})
        # 两局各挂各的，没有互相覆盖
        self.assertEqual(self.st.get_match('aaaa1111')['opp_deck_name'], '混轴卡组')
        self.assertEqual(self.st.get_match('bbbb2222')['opp_deck_name'], '未知卡组')

    def test_nothing_older_is_noop(self):
        rc, out = self.run_cli('--unknown-before', '2020-01-01')
        self.assertEqual(rc, 0, out)
        self.assertIn('不用动', out)


class TestInteractiveBranches(Base):

    def test_display_map_is_storage_level(self):
        """UI/CLI 共用的显示口径：站内名 → 中文名优先（实现在 Storage）。"""
        m = self.st.meta_deck_display_map()
        self.assertEqual(m['Branded'], '烙印')
        self.assertEqual(m['Tearlaments'], '泪冠哀歌')
        self.assertEqual(m['Vanquish Soul'], '斗魂')

    def test_guess_uses_same_core_path(self):
        """`load_guesser` / `reveal_ids` / `suggest_for` 只是 core 的薄壳。"""
        self.assertIs(mo.reveal_ids, mo.reveal_ids)          # 存在即可
        self.assertEqual(mo.reveal_ids({'opp_revealed_ids': '[1,2]'}), [1, 2])
        self.assertIsNone(mo.load_guesser('/不存在的路径.json')[0])
    """`--interactive` 的分支表（写死在反推计划 §四 P2-4）。"""

    def setUp(self):
        super().setUp()
        self.index, self.groups = dg.prepare(REF)

    def add_big_row(self):
        self.st.upsert_match(mk('cccc3333', '2026-09-10 12:00:00.000',
                                opp_revealed=[str(c) for c in BIG],
                                opp_revealed_ids=list(BIG)))

    def run_it(self, keys):
        buf = io.StringIO()
        with mock.patch('builtins.input', side_effect=list(keys)):
            with contextlib.redirect_stdout(buf):
                rc = mo.cmd_interactive(self.st, self.index, self.groups)
        return rc, buf.getvalue()

    def test_empty_enter_accepts_suggestion(self):
        self.add_big_row()
        rc, out = self.run_it(['', 'q'])
        self.assertEqual(rc, 0, out)
        row = self.st.get_match('cccc3333')
        self.assertEqual(row['opp_deck_name'], '烙印', '空回车 = 采纳建议')

    def test_empty_enter_skips_without_suggestion(self):
        """★ 小 n 负向：只露出 1 张通用卡 → confidence 1.0 但**不预填**，回车=跳过。"""
        rc, out = self.run_it(['', 'q'])
        self.assertEqual(rc, 0, out)
        self.assertTrue(all(r['opp_deck_id'] is None
                            for r in self.st.get_unmarked_matches(10)))
        self.assertNotIn('（回车 = 采纳', out)

    def test_number_picks_candidate(self):
        self.add_big_row()
        rc, out = self.run_it(['1', 'q'])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.st.get_match('cccc3333')['opp_deck_name'], '烙印')

    def test_bare_question_lists_all_candidates(self):
        self.add_big_row()
        rc, out = self.run_it(['?', 'q'])
        self.assertIn('命中 5/5', out)
        self.assertEqual(self.st.get_match('cccc3333')['opp_deck_id'], None,
                         '看候选不等于采纳')

    def test_question_word_still_searches(self):
        """`?词` 保持原语义（搜卡组），不能被裸 `?` 抢走。"""
        self.add_big_row()
        rc, out = self.run_it(['?烙印', 'q'])
        self.assertIn('Branded', out)
        self.assertEqual(self.st.get_match('cccc3333')['opp_deck_id'], None)

    def test_no_reference_keeps_old_hint(self):
        buf = io.StringIO()
        with mock.patch('builtins.input', side_effect=['q']):
            with contextlib.redirect_stdout(buf):
                mo.cmd_interactive(self.st, None, None)
        self.assertIn('回车 = 跳过', buf.getvalue())

class TestUnifiedIndexLimit(Base):
    """★ BUG-B9A-05 / B9A-06：`--list` / `--set` / `--clear` / `--guess` **共用一个 limit**。"""

    def setUp(self):
        super().setUp()
        for i in range(25):
            self.st.upsert_match(mk('e%07d' % i,
                                    '2026-09-%02d 10:00:00.000' % (i + 1)))

    def test_default_limit_is_one_constant(self):
        self.assertEqual(mo.DEFAULT_LIST_LIMIT, 20)

    def test_set_uses_list_limit(self):
        """`--list 3` 显示的第 1 条 = `--set 1` 绑的那一条（两边同一份列表）。"""
        rc, out = self.run_cli('--list', '5')
        newest = self.st.get_unmarked_matches(5)[0]['match_id']
        self.assertIn(newest[:8], out)
        rc, out = self.run_cli('--list', '5', '--set', '1', '烙印')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.st.get_match(newest)['opp_deck_name'], '烙印')

    def test_set_beyond_list_limit_is_rejected(self):
        """超出屏幕上看到的范围 → **必须报多余**，不能默默作用到看不见的行。

        旧行为：`--set 60` 会在 1000 条里找，看不见也照绑。
        现在：列表只有 `--list` 那么长，越界就报错。
        """
        rc, out = self.run_cli('--set', '60', '烙印')      # 默认 20 条
        self.assertEqual(rc, 1, out)
        self.assertIn('越界', out)

    def test_clear_uses_same_limit_as_marked_list(self):
        """`--list --marked` 显示的第 1 条 = `--clear 1` 撤的那一条。"""
        rows = self.st.get_unmarked_matches(5)
        for r in rows[:2]:
            self.run_cli('--set', '1', '烙印')
        marked = self.st.get_marked_matches(mo.DEFAULT_LIST_LIMIT)
        self.assertEqual(len(marked), 2)
        target = marked[0]['match_id']
        rc, out = self.run_cli('--set', '1', '--clear')
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.st.get_match(target)['opp_deck_id'])

    def test_guess_uses_list_limit_for_index(self):
        """`--guess N` 的序号也跟 `--list` 同一份列表。"""
        rows = self.st.get_unmarked_matches(mo.DEFAULT_LIST_LIMIT)
        with mock.patch.object(mo, 'match_any_by_token',
                               wraps=mo.match_any_by_token) as spy:
            mo.match_any_by_token(self.st, '1')
            args, kwargs = spy.call_args
            self.assertEqual(kwargs.get('limit', mo.DEFAULT_LIST_LIMIT),
                             mo.DEFAULT_LIST_LIMIT)




if __name__ == '__main__':
    unittest.main()
