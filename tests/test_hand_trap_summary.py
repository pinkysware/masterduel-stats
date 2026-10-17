# -*- coding: utf-8 -*-
"""手坑汇总（`ui_state.hand_trap_summary`）+ 导出两列 的单测。

★ 2026-10-16 第四轮设计：**两张表**（行 = 我方卡组，列 = 12 张手坑，格 = 我的胜率）：
  * `second_my` = 我后手时、前两回合（turn 0/1）**我**用了某坑的局；
  * `first_opp` = 我先手时、前两回合 **对方** 用了某坑的局。
⚠️ 只算有逐卡数据的局；`went_first` 判不出的局不进任何表。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import ui_state                                          # noqa: E402


class _LangCnMixin(object):
    def setUp(self):
        import tempfile
        from PySide6.QtCore import QSettings
        import ui.i18n as _i18n
        cls = type(self)
        if not hasattr(cls, '_INI'):
            cls._INI = tempfile.mkdtemp(prefix='ht-sum-')
        _i18n._SETTINGS_FACTORY = staticmethod(
            lambda: QSettings(os.path.join(cls._INI, 'ui.ini'),
                              QSettings.IniFormat))
        from ui.i18n import reset_lang
        reset_lang()
        super().setUp()


class _Store(object):
    def __init__(self, rows):
        self.rows = rows

    def get_recent_matches(self, limit=5000, account=None):
        return list(self.rows)[:limit]


def _m(mid, res, wf, my, opp, deck='闪刀姬', gm=3,
       started='2026-10-05 12:00:00'):
    return {'match_id': mid, 'started_at': started, 'mode': 'pvp',
            'game_mode': gm, 'result': res, 'went_first': wf,
            'my_deck_name': deck, 'my_traps': my, 'opp_traps': opp,
            'record_dir': mid}


def _t(key, cid, turn=0):
    from core import hand_traps as ht
    return {'key': key, 'id': cid, 'name_cn': ht.name_cn(key), 'turn': turn}


class HandTrapSummaryTest(_LangCnMixin, unittest.TestCase):

    def _summary(self, rows, **kw):
        return ui_state.hand_trap_summary(_Store(rows), **kw)

    def _find(self, rows, deck='闪刀姬'):
        return [r for r in rows if r['deck'] == deck][0]

    def test_traps_catalog(self):
        """12 张手坑都进表（列）。"""
        d = self._summary([])
        self.assertEqual(len(d['traps']), 12)
        self.assertEqual([t['key'] for t in d['traps']][0], 'maxx')

    def test_two_tables(self):
        """表 A = 我后手/我用；表 B = 我先手/敌用。"""
        ash = _t('ash', 12950)
        maxx = _t('maxx', 9455)
        rows = [
            _m('a', 'Win', False, [ash], [maxx]),    # 我后手 + 我用 -> 表A
            _m('b', 'Lose', True, [maxx], [ash]),    # 我先手 + 敌用 -> 表B
        ]
        d = self._summary(rows)
        self.assertEqual(self._find(d['second_my'], '闪刀姬')['n_total'], 1)
        self.assertEqual(self._find(d['first_opp'], '闪刀姬')['n_total'], 1)

    def test_table_a_counts_my_trap_only(self):
        """表 A 格 = 我后手时我用该坑的局（对面用不算进表 A 的格）。"""
        ash = _t('ash', 12950)
        maxx = _t('maxx', 9455)
        rows = [_m('a', 'Win', False, [ash], [maxx]),
                _m('b', 'Lose', False, [ash], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['n_total'], 2)
        self.assertEqual(r['cells']['ash'], {'n': 2, 'w': 1, 'l': 1, 'rate': 50.0})
        self.assertEqual(r['cells']['maxx']['n'], 0)      # 对面用的不算

    def test_table_b_counts_opp_trap_only(self):
        """表 B 格 = 我先手时对方用该坑的局。"""
        ash = _t('ash', 12950)
        rows = [_m('a', 'Win', True, [], [ash]),
                _m('b', 'Win', True, [], [ash]),
                _m('c', 'Lose', True, [], [ash])]
        r = self._find(self._summary(rows)['first_opp'], '闪刀姬')
        self.assertEqual(r['n_total'], 3)
        self.assertEqual(r['cells']['ash'], {'n': 3, 'w': 2, 'l': 1,
                                             'rate': 200.0 / 3})

    def test_turn_filter_drops_second_round(self):
        """★ 只算前两回合（turn 0/1）—— turn 2 的手坑不算（用户第 1 条口径）。"""
        ash = _t('ash', 12950, turn=2)
        rows = [_m('a', 'Win', False, [ash], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['n_total'], 1)          # 局仍计入 n_total
        self.assertEqual(r['cells']['ash']['n'], 0)   # 但 turn2 的坑不算

    def test_turn_01_counted(self):
        ash0 = _t('ash', 12950, turn=0)
        ash1 = _t('ash', 12950, turn=1)
        rows = [_m('a', 'Win', False, [ash0], []),
                _m('b', 'Lose', False, [ash1], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['cells']['ash']['n'], 2)

    def test_same_trap_twice_in_one_game_counts_once(self):
        """同一局同一张坑两次（turn0+turn1）-> 该格只记一次。"""
        ash0 = _t('ash', 12950, turn=0)
        ash1 = _t('ash', 12950, turn=1)
        rows = [_m('a', 'Win', False, [ash0, ash1], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['cells']['ash']['n'], 1)

    def test_unjudgeable_rows_excluded(self):
        """判不出（两列 None）的局不进表。"""
        rows = [_m('a', 'Win', False, None, None),
                _m('b', 'Win', False, [], [])]
        d = self._summary(rows)
        self.assertEqual(d['n_games'], 1)

    def test_went_first_none_excluded(self):
        """先/后判不出的局两张表都不收（但算进 n_games）。"""
        ash = _t('ash', 12950)
        rows = [_m('a', 'Win', None, [ash], [ash])]
        d = self._summary(rows)
        self.assertEqual(d['n_games'], 1)
        self.assertEqual(d['second_my'], [])
        self.assertEqual(d['first_opp'], [])

    def test_empty_list_counts_game(self):
        """`[]` = 确实没用过（局计入 n_total，格子全 0）。"""
        rows = [_m('a', 'Win', False, [], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['n_total'], 1)
        self.assertTrue(all(c['n'] == 0 for c in r['cells'].values()))

    def test_rows_sorted_by_games(self):
        rows = [_m('1', 'Win', False, [], [], deck='K9vs'),
                _m('2', 'Win', False, [], [], deck='闪刀姬'),
                _m('3', 'Win', False, [], [], deck='闪刀姬')]
        d = self._summary(rows)
        self.assertEqual([r['deck'] for r in d['second_my']], ['闪刀姬', 'K9vs'])

    def test_month_filter(self):
        ash = _t('ash', 12950)
        rows = [_m('a', 'Win', False, [ash], [], started='2026-10-05 12:00:00'),
                _m('b', 'Win', False, [ash], [], started='2026-09-05 12:00:00')]
        r = self._find(self._summary(rows, month='2026-10')['second_my'],
                       '闪刀姬')
        self.assertEqual(r['n_total'], 1)

    def test_draw_not_in_rate(self):
        ash = _t('ash', 12950)
        rows = [_m('a', 'Win', False, [ash], []),
                _m('b', 'Draw', False, [ash], [])]
        r = self._find(self._summary(rows)['second_my'], '闪刀姬')
        self.assertEqual(r['cells']['ash']['n'], 2)
        self.assertEqual(r['cells']['ash']['rate'], 100.0)   # 平局不进分母

    def test_unknown_deck_bucket(self):
        row = _m('a', 'Win', False, [], [])
        row['my_deck_name'] = None
        d = self._summary([row])
        self.assertEqual(d['second_my'][0]['deck'], ui_state.MY_DECK_UNKNOWN)


class TrapTableLayoutTest(unittest.TestCase):
    """手坑汇总表列宽（12 列 + 卡组列，表头 = 卡图）。"""

    def test_calc_widths_fits_or_scrolls(self):
        from ui.analysis_page import (HandTrapTable, TRAP_DECK_W, TRAP_TRAP_W,
                                      TRAP_GAP)
        from core import hand_traps as ht
        app = _qapp()
        traps = [{'key': k, 'name': ht.name_cn(k)} for k in ht.TRAPS]
        t = HandTrapTable(traps)
        wide = t._calc_widths(2000)
        gaps = len(traps) * TRAP_GAP          # 13 控件 -> 12 个间距
        self.assertEqual(sum(wide) + gaps, 2000)          # 宽了要铺满
        self.assertEqual(wide[0], TRAP_DECK_W)
        tight = t._calc_widths(100)                        # 窄了退回最小宽
        self.assertEqual(tight[0], TRAP_DECK_W)
        self.assertEqual(tight[1], TRAP_TRAP_W)

    def test_cell_texts(self):
        from ui.analysis_page import _cell_texts
        self.assertEqual(_cell_texts({'n': 3, 'rate': 66.7}), ('66.7%', '3 场'))
        self.assertEqual(_cell_texts({'n': 0, 'rate': None}), ('—', ''))


_APP = None


def _qapp():
    global _APP
    import os as _os
    _os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class ExportTrapsTest(_LangCnMixin, unittest.TestCase):
    def test_trap_names_cell(self):
        rows = [{'name': '灰流丽'}, {'name': '无限泡影'}]
        self.assertEqual(ui_state._trap_names_cell(rows), '灰流丽, 无限泡影')
        self.assertEqual(ui_state._trap_names_cell([]), '')
        self.assertEqual(ui_state._trap_names_cell(None), '')

    def test_export_columns_present(self):
        keys = [k for k, _lab in ui_state.EXPORT_COLUMNS]
        self.assertIn('my_traps', keys)
        self.assertIn('opp_traps', keys)

    def test_export_rows_include_traps(self):
        st = _Store([_m('1', 'Win', True,
                        [{'name': '灰流丽', 'turn': 0, 'id': 12950, 'img': None}],
                        [])])
        rows = ui_state.export_rows(st, month='2026-10')
        self.assertEqual(rows[0]['my_traps'], '灰流丽')
        self.assertEqual(rows[0]['opp_traps'], '')


if __name__ == '__main__':
    unittest.main()
