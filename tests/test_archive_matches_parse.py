# -*- coding: utf-8 -*-
"""parse_seg 回扫区污染回归（2026-09-13 实测缺陷）。

缺陷：`parse_seg` 为抓硬币行从 start_line-60 起扫，60 行可能扫进**上一局的收尾**——
那里的 ACT/HANDOPEN/SPSUMMON 被当成下一局 opp_revealed 的前缀卡
（实测 09-13 12:05 局前缀混入 12:01 的 20759/21377；33 对相邻局 3 对中招），
上一局的 'Turn N' 行还会抬高 total_turns、抢走 first_player。
修复：回扫区（start_line 之前的行）只认 COIN。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import archive_matches as am  # noqa: E402


def L(ts, msg):
    return '[2026-09-13 %s] [info]  [Ygom.handleScryEvent] %s' % (ts, msg)


def _seg(lines, start_idx):
    return {'start_line': start_idx, 'end_line': len(lines) - 1,
            'start_time': '2026-09-13 12:05:46.000',
            'end_time': '2026-09-13 12:07:00.000',
            'deck_id_raw': '48171157'}


def _base_lines():
    """真实日志形态：上一局收尾 → Game ended → 本局硬币 → Starting Duel → 本局主体。"""
    return [
        L('12:01:30.000', 'Turn 5 for player 1'),                       # 上一局尾部 TURN
        L('12:01:47.000', 'HANDOPEN: cardId=20759 source=P1-Hand target=P1-Grave'),
        L('12:01:49.000', 'SPSUMMON: cardId=21377 source=P1-Monster0 target=null'),
        L('12:01:52.000', 'Game ended: result=Lose deckId=48171157'),   # 上一局 END
        L('12:05:40.000', 'Player is deciding whether to go first'),    # 本局硬币（回扫目标）
        L('12:05:46.000', 'Starting Duel with deckId=48171157'),        # ← start_line=5
        L('12:05:50.000', 'Turn 1 for player 0'),
        L('12:06:00.000', 'SPSUMMON: cardId=99999 source=P1-Hand target=P1-Monster0'),
        L('12:06:10.000', 'DRAW: cardId=11111 source=P0-Deck target=P0-Hand'),
        L('12:07:00.000', 'Game ended: result=Win deckId=48171157'),
    ]


class TestBackscanContamination(unittest.TestCase):
    """上一局收尾的 ACT/TURN/END 不得混进下一局。"""

    def setUp(self):
        self.lines = _base_lines()
        self.seg = _seg(self.lines, 5)
        self.saved = am.DECKS
        am.DECKS = {'48171157': {'name': '闪刀姬', 'main': {'11111': 3}, 'extra': {}}}

    def tearDown(self):
        am.DECKS = self.saved

    def test_prev_tail_acts_not_in_opp_revealed(self):
        """★ 核心回归：上一局尾部的 20759/21377 不得出现在本局 opp_revealed。"""
        row = am.parse_seg(self.seg, self.lines)
        self.assertEqual(row['opp_revealed_ids'], [99999])
        self.assertEqual(row['opp_revealed'], ['[未知:99999]'])

    def test_prev_turn_not_inflating_max_turn(self):
        """上一局的 'Turn 5' 不得抬高本局 total_turns。"""
        row = am.parse_seg(self.seg, self.lines)
        self.assertEqual(row['total_turns'], 1)

    def test_coin_still_collected_from_backscan(self):
        """回扫区只认 COIN——硬币行（在 start_line 之前）必须仍被采到。"""
        row = am.parse_seg(self.seg, self.lines)
        self.assertTrue(row['won_initiative'])          # coin='Player'

    def test_went_first_from_post_start_turn(self):
        """went_first 用主体里的 Turn 1 归属（seat=0 → player 0 先攻 → True）。"""
        row = am.parse_seg(self.seg, self.lines)
        self.assertIs(row['went_first'], True)
        self.assertEqual(row['seat'], 0)

    def test_prev_turn1_does_not_steal_first_player(self):
        """回扫区里的上一局 'Turn 1' 不得抢走 first_player（修复前会）。"""
        lines = _base_lines()
        lines.insert(2, L('12:01:35.000', 'Turn 1 for player 1'))
        seg = _seg(lines, 6)                    # 插入一行后 start_line 顺延
        row = am.parse_seg(seg, lines)
        self.assertIs(row['went_first'], True)  # 本局主体 Turn 1 = player 0
        self.assertEqual(row['first_player'], 0)

    def test_post_start_events_untouched(self):
        """主体内的事件照常收集（防修复过度）。"""
        row = am.parse_seg(self.seg, self.lines)
        # 11111 是我方（P0）——不在 opp_revealed；99999（对手）必须在
        self.assertEqual(row['opp_revealed_ids'], [99999])


if __name__ == '__main__':
    unittest.main()

class TestCoinBackscanPicksLatest(unittest.TestCase):
    """★ BUG-B8-13：回扫区里的硬币行要取**离本局最近**的那条。

    实测事故（2026-10-17 22:22:07 那局）：60 行回扫窗口里同时躺着
    22:21:43 上一局的 `Opponent` 和 22:22:12 本局的 `Player`，
    旧代码取第一条 → 硬币记反。
    """

    def setUp(self):
        self.saved = am.DECKS
        am.DECKS = {'48171157': {'name': '闪刀姬', 'main': {'11111': 3}, 'extra': {}}}

    def tearDown(self):
        am.DECKS = self.saved

    def _lines(self, prev_coin_ts=None, own_coin_ts='22:22:12.000',
               prev_gap_lines=30):
        lines = []
        if prev_coin_ts:
            lines.append(L(prev_coin_ts, 'Opponent is deciding whether to go first'))
            # 中间填一些上一局的收尾行（真实日志里还有几十行 debug）
            lines += [L('22:22:00.000', 'DRAW: cardId=1 source=P0-Deck target=P0-Hand')
                      for _ in range(max(0, prev_gap_lines))]
        if own_coin_ts:
            lines.append(L(own_coin_ts, 'Player is deciding whether to go first'))
        lines.append(L('22:22:17.000', 'Starting Duel with deckId=48171157'))
        lines.append(L('22:22:25.000', 'Turn 1 for player 1'))
        lines.append(L('22:23:00.000', 'Game ended: result=Win deckId=48171157'))
        return lines

    def _seg(self, lines):
        start = next(i for i, l in enumerate(lines)
                     if 'Starting Duel with deckId=' in l)
        return {'start_line': start, 'end_line': len(lines) - 1,
                'start_time': '2026-09-13 22:22:17.000',
                'end_time': '2026-09-13 22:23:00.000',
                'deck_id_raw': '48171157'}

    def test_previous_game_coin_does_not_win(self):
        """本局 `Player` 必须压过窗口里更早的上一局 `Opponent`。"""
        lines = self._lines('22:21:43.000')
        row = am.parse_seg(self._seg(lines), lines)
        self.assertIs(row['won_initiative'], True,
                      '取到了上一局的硬币（BUG-B8-13 回归）')
        self.assertEqual(row['won_initiative_src'], 'backscan-latest')

    def test_stale_coin_beyond_age_is_rejected(self):
        """只剩一条很远的硬币行（> COIN_MAX_AGE_SEC）= 上一局残影，宁缺。"""
        lines = self._lines('22:21:43.000', own_coin_ts=None)
        lines.insert(0, L('22:21:43.000', 'Opponent is deciding whether to go first'))
        row = am.parse_seg(self._seg(lines), lines)
        self.assertIsNone(row['won_initiative'])
        self.assertTrue(str(row['won_initiative_src']).startswith('stale('),
                        row['won_initiative_src'])

    def test_coin_in_main_body_still_counts(self):
        """主体区（Starting Duel 之后）出现硬币行时照旧采（重排/回放口径）。"""
        lines = self._lines(None, own_coin_ts=None)
        idx = next(i for i, l in enumerate(lines) if 'Starting Duel' in l)
        lines.insert(idx + 1, L('22:22:18.000',
                                'Opponent is deciding whether to go first'))
        row = am.parse_seg(self._seg(lines), lines)
        self.assertIs(row['won_initiative'], False)
        self.assertEqual(row['won_initiative_src'], 'main')

