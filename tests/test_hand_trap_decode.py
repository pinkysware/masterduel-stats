# -*- coding: utf-8 -*-
"""手坑识别的解码层单测（`core/duel_record.summarize_hand_traps`）。

三个真数据用例（计划 §七 步 2 要求）：泡影盖后开、异画、脏回合号。
外加簇规则（一次发动）的差分验证 —— 用**手核过的样本**当基准。
"""

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import duel_record as dr                     # noqa: E402
from core import hand_traps as ht                      # noqa: E402

FIX = os.path.join(PROJ, 'tests', 'fixtures', 'duel_records')
REC_FULL = os.path.join(FIX, 'record_20260913_123714')
ARCH = os.path.join(PROJ, 'data', 'archive', 'duel_records')
#: 真实归档里的样本（跑测试的机器上可能没有 —— 缺就 skip）
REC_120910 = os.path.join(ARCH, 'record_20260916_120910')
REC_232750 = os.path.join(ARCH, 'record_20260915_232750')
REC_DIRTY = os.path.join(ARCH, 'record_20261010_203819')


def _act(card_id, kind, zone, player, seq, side='l'):
    """造一条 ShowAction 事件（含 cards 列表）。"""
    return {'seq': seq, 'label': 'ShowAction', 'kind_name': kind,
            'zone_name': zone, 'player': player,
            'cards': [{'card_id': card_id, 'player': player, 'side': side}]}


def _turn(turn, player, seq):
    return {'seq': seq, 'label': 'ShowTurn', 'turn': turn, 'player': player}


class KindZoneTest(unittest.TestCase):
    """判据：发动族 × 允许区域；排除抽/盖/检索/回收/送墓。"""

    def test_happen_hand_counts(self):
        s = dr.summarize_hand_traps([_act(9455, 'HAPPEN', 'Hand', 0, 1)])
        self.assertEqual(s['n'], 1)
        self.assertEqual(s['activations'][0]['key'], 'maxx')

    def test_unknown_ab_and_effectimpl_count(self):
        ev = [_act(9455, 'UNKNOWN_A', 'Hand', 0, 1),
              _act(9279, 'UNKNOWN_B', 'Hand', 0, 2),
              _act(11708, 'EFFECTIMPL', 'Hand', 0, 3)]
        self.assertEqual(dr.summarize_hand_traps(ev)['n'], 3)

    def test_draw_and_set_and_search_excluded(self):
        """用户 Q2：只算从手牌打出 —— 抽到/盖放/检索/回收/送墓都不算。"""
        for kind in ('DRAW', 'SETV', 'SETH', 'SEARCH', 'RETURN', 'SENDTOGRAVE'):
            s = dr.summarize_hand_traps([_act(9455, kind, 'Hand', 0, 1)])
            self.assertEqual(s['n'], 0, '%s 不该算使用' % kind)

    def test_zone_hand_ok(self):
        self.assertEqual(
            dr.summarize_hand_traps([_act(9455, 'HAPPEN', 'Hand', 0, 1)])['n'], 1)

    def test_zone_field_spell_trap_ok(self):
        """泡影盖后开：zone 是魔陷区 7~12 —— 必须放行（审查 P1-1）。"""
        for z in ('Zone7', 'Zone8', 'Zone9', 'Zone10', 'Zone11', 'Zone12'):
            s = dr.summarize_hand_traps([_act(13631, 'HAPPEN', z, 0, 1)])
            self.assertEqual(s['n'], 1, '%s 漏判了（泡影盖后开）' % z)

    def test_zone_other_excluded(self):
        """场上怪兽区 / 卡组 / 额外 / 墓地(Zone18) 不算。"""
        for z in ('Zone0', 'Zone4', 'Zone6', 'Zone13', 'Zone14', 'Zone15', 'Zone18'):
            s = dr.summarize_hand_traps([_act(13631, 'HAPPEN', z, 0, 1)])
            self.assertEqual(s['n'], 0, '%s 不该算' % z)

    def test_non_trap_card_ignored(self):
        self.assertEqual(
            dr.summarize_hand_traps([_act(12345, 'HAPPEN', 'Hand', 0, 1)])['n'], 0)


class AltArtTest(unittest.TestCase):
    """异画 id 归一到标准 key；展示用标准 id。"""

    def test_alt_ids_normalized(self):
        cases = ((3891, 'ash', 12950), (21224, 'veiler', 8933),
                 (3892, 'belle', 13587), (3416, 'mourner', 15022))
        for alt, key, std in cases:
            s = dr.summarize_hand_traps([_act(alt, 'HAPPEN', 'Hand', 0, 1)])
            self.assertEqual(s['activations'][0]['key'], key)
            self.assertEqual(s['activations'][0]['id'], std)

    def test_standard_and_alt_same_key(self):
        """同侧同时出现标准 + 异画：同一次 -> 一张（间隔 0 合并）。"""
        ev = [_act(12950, 'HAPPEN', 'Hand', 0, 1),
              _act(3891, 'UNKNOWN_B', 'Hand', 0, 2)]
        s = dr.summarize_hand_traps(ev)
        self.assertEqual(s['n'], 1, '标准+异画同一次应算一张')


class ClusterTest(unittest.TestCase):
    """一次发动的切分（seq 间隔规则，用差分验证定的平台区）。"""

    def test_happen_then_unknown_is_one(self):
        """seq23 HAPPEN 与 seq26 UNKNOWN_B 是同一次的两条记录 -> 1 次。"""
        ev = [_act(9455, 'HAPPEN', 'Hand', 1, 23),
              _act(9455, 'UNKNOWN_B', 'Hand', 1, 26)]
        self.assertEqual(dr.summarize_hand_traps(ev)['n'], 1)

    def test_two_activations_split_by_gap(self):
        """seq153 HAPPEN / seq171 HAPPEN（差 18 > 平台区）= 两次发动。"""
        ev = [_act(13631, 'HAPPEN', 'Hand', 0, 153),
              _act(13631, 'HAPPEN', 'Hand', 0, 171)]
        self.assertEqual(dr.summarize_hand_traps(ev)['n'], 2)

    def test_gap_platform_invariant(self):
        """差分验证：§3.3b 说 seq 间隔在 [3,8] 是平台区（阈值不敏感）。

        这里锁住「HAPPEN(23) + UNKNOWN(26) 差 3」与「差 8」都算一次 ——
        哪天规则改坏（比如把 <-改成 0<），这个测试先红。
        """
        for delta in (3, 4, 6, 8):
            ev = [_act(9455, 'HAPPEN', 'Hand', 1, 23),
                  _act(9455, 'UNKNOWN_B', 'Hand', 1, 23 + delta)]
            self.assertEqual(dr.summarize_hand_traps(ev)['n'], 1,
                             'gap=%d 应算同一次' % delta)
        # 差 2 也是平台区之外偏碎的一侧，但本实现取 [3,8]；锁住 9 起分开
        ev = [_act(9455, 'HAPPEN', 'Hand', 1, 23),
              _act(9455, 'UNKNOWN_B', 'Hand', 1, 34)]
        self.assertEqual(dr.summarize_hand_traps(ev)['n'], 2)

    def test_two_traps_interleaved(self):
        """同名两张、中间夹别的手坑 -> 两次（不同卡不影响同卡间隔）。"""
        ev = [_act(9455, 'HAPPEN', 'Hand', 0, 100),
              _act(12950, 'HAPPEN', 'Hand', 0, 105),
              _act(9455, 'HAPPEN', 'Hand', 0, 110)]
        s = dr.summarize_hand_traps(ev)
        self.assertEqual(s['n'], 3)

    def test_different_players_not_merged(self):
        """同一张卡两侧各发一次 = 2 次（key 含 player）。"""
        ev = [_act(9455, 'HAPPEN', 'Hand', 0, 100),
              _act(9455, 'HAPPEN', 'Hand', 1, 102)]
        self.assertEqual(dr.summarize_hand_traps(ev)['n'], 2)

    def test_lr_duplicate_same_event_is_one(self):
        """同一事件里 l/r 双侧重复记录 -> 只算一次（别算成两张）。"""
        e = _act(13631, 'HAPPEN', 'Hand', 0, 153)
        e['cards'] = [{'card_id': 13631, 'player': 0, 'side': 'l'},
                      {'card_id': 13631, 'player': 0, 'side': 'r'}]
        self.assertEqual(dr.summarize_hand_traps([e])['n'], 1)


class TurnTest(unittest.TestCase):
    """回合归属：取最近一条前置 ShowTurn；脏回合号忽略。"""

    def test_turn_attached(self):
        ev = [_turn(0, 1, 1), _act(9455, 'HAPPEN', 'Hand', 1, 5),
              _turn(1, 0, 10), _act(12950, 'HAPPEN', 'Hand', 0, 15)]
        s = dr.summarize_hand_traps(ev)
        self.assertEqual([a['turn'] for a in s['activations']], [0, 1])

    def test_no_show_turn_has_turns_false(self):
        s = dr.summarize_hand_traps([_act(9455, 'HAPPEN', 'Hand', 0, 5)])
        self.assertFalse(s['has_turns'])
        self.assertIsNone(s['activations'][0]['turn'])

    def test_dirty_turn_ignored(self):
        """record_20261010_203819 有 turn=597 脏值 —— 不许污染其后事件。"""
        ev = [_turn(0, 1, 1), _turn(597, 0, 5),
              _act(9455, 'HAPPEN', 'Hand', 1, 8)]
        s = dr.summarize_hand_traps(ev)
        self.assertEqual(s['dirty_turn'], 1)
        self.assertEqual(s['activations'][0]['turn'], 0)   # 沿用上一条合法回合

    def test_dirty_before_any_valid(self):
        ev = [_turn(597, 0, 1), _act(9455, 'HAPPEN', 'Hand', 0, 5)]
        s = dr.summarize_hand_traps(ev)
        self.assertIsNone(s['activations'][0]['turn'])
        self.assertFalse(s['has_turns'])


class SidesTest(unittest.TestCase):
    """seat 映射：player(P0/P1) -> 我/敌；seat 判不出两列都 None。"""

    def _summary(self):
        return dr.summarize_hand_traps([
            _turn(0, 1, 1),
            _act(9455, 'HAPPEN', 'Hand', 0, 5),
            _act(12950, 'HAPPEN', 'Hand', 1, 6)])

    def test_seat_zero(self):
        mine, opp = dr.hand_trap_sides(self._summary(), seat=0)
        self.assertEqual([x['key'] for x in mine], ['maxx'])
        self.assertEqual([x['key'] for x in opp], ['ash'])

    def test_seat_one(self):
        mine, opp = dr.hand_trap_sides(self._summary(), seat=1)
        self.assertEqual([x['key'] for x in mine], ['ash'])
        self.assertEqual([x['key'] for x in opp], ['maxx'])

    def test_seat_none_both_null(self):
        mine, opp = dr.hand_trap_sides(self._summary(), seat=None)
        self.assertIsNone(mine)
        self.assertIsNone(opp)

    def test_no_turns_both_null(self):
        s = dr.summarize_hand_traps([_act(9455, 'HAPPEN', 'Hand', 0, 5)])
        mine, opp = dr.hand_trap_sides(s, seat=0)
        self.assertIsNone(mine)
        self.assertIsNone(opp)

    def test_empty_use_is_empty_list_not_none(self):
        """确实没用过 = 空列表（与「判不出」的 None 不同）。

        玩家 0 打了 Maxx、玩家 1 没打 —— seat=1 时「我方」是空列表、不是 None。
        """
        s = dr.summarize_hand_traps([_turn(0, 1, 1),
                                     _act(9455, 'HAPPEN', 'Hand', 0, 5)])
        mine, opp = dr.hand_trap_sides(s, seat=1)
        self.assertEqual(mine, [])          # 玩家 1 = 我方，没用过
        self.assertEqual([x['key'] for x in opp], ['maxx'])

    def test_turn_filter_only_first_two(self):
        """★ 只留**前两回合 = turn 0/1**（用户 2026-10-16 明确：只统计前 2 回合）。

        turn 2（双方各自第二回合）/更高一律不收 —— 用户实测发现我先前按
        §3.4 的分类误收了 0..3，被纠正。
        """
        for bad_turn in (2, 3, 4):
            s = dr.summarize_hand_traps([_turn(bad_turn, 0, 1),
                                         _act(9455, 'HAPPEN', 'Hand', 0, 5)])
            mine, _o = dr.hand_trap_sides(s, seat=0)
            self.assertEqual(mine, [], 'turn=%d 不该收' % bad_turn)

    def test_turn_01_kept(self):
        """turn 0 与 turn 1 都收（本局第 1、2 个回合）。"""
        ev = [_turn(0, 1, 1), _act(9455, 'HAPPEN', 'Hand', 1, 3),
              _turn(1, 0, 10), _act(12950, 'HAPPEN', 'Hand', 0, 12)]
        s = dr.summarize_hand_traps(ev)
        mine, opp = dr.hand_trap_sides(s, seat=0)
        self.assertEqual([x['key'] for x in mine], ['ash'])   # turn1 = 玩家0
        self.assertEqual([x['key'] for x in opp], ['maxx'])   # turn0 = 玩家1

    def test_item_shape(self):
        mine, _ = dr.hand_trap_sides(self._summary(), seat=0)
        self.assertEqual(mine[0],
                         {'turn': 0, 'id': 9455, 'key': 'maxx', 'name_cn': '增殖的Z'})


@unittest.skipUnless(os.path.isdir(REC_FULL), 'fixture absent')
class FixtureTest(unittest.TestCase):
    """真 fixture：泡影盖后开（Zone7）+ 完整事件流。"""

    def test_full_record(self):
        rec = dr.decode_record(dr.scan_record(REC_FULL))
        activations = rec['summary']['hand_traps']['activations']
        imprm = [a for a in activations if a['key'] == 'imperm']
        #: seq40 HAPPEN/Zone7 是「盖后开」那条（只认 Hand 会漏它）
        self.assertTrue(any(a['seq'] == 40 and a['zone'] == 'Zone7'
                            for a in imprm), '泡影盖后开（Zone7）漏判')

    def test_hand_traps_key_present_in_summary(self):
        rec = dr.decode_record(dr.scan_record(REC_FULL))
        self.assertIn('hand_traps', rec['summary'])


@unittest.skipUnless(os.path.isdir(REC_120910), 'real archive absent')
class RealArchiveTest(unittest.TestCase):
    """真实归档样本（手核过的真值）—— 跑测试的机器上有才跑。"""

    def test_two_imperm_activations(self):
        rec = dr.decode_record(dr.scan_record(REC_120910))
        acts = rec['summary']['hand_traps']['activations']
        p0t1 = [a for a in acts if a['player'] == 0 and a['turn'] == 1
                and a['key'] == 'imperm']
        self.assertEqual(len(p0t1), 2, '泡影两次发动被并成一次（规则退化了）')

    def test_two_veiler_activations(self):
        rec = dr.decode_record(dr.scan_record(REC_120910))
        acts = rec['summary']['hand_traps']['activations']
        p1t1 = [a for a in acts if a['player'] == 1 and a['turn'] == 1
                and a['key'] == 'veiler']
        self.assertEqual(len(p1t1), 2, '遮蒙两次发动被并成一次')

    def test_maxx_single(self):
        rec = dr.decode_record(dr.scan_record(REC_232750))
        acts = rec['summary']['hand_traps']['activations']
        p1t0 = [a for a in acts if a['player'] == 1 and a['turn'] == 0
                and a['key'] == 'maxx']
        self.assertEqual(len(p1t0), 1)

    @unittest.skipUnless(os.path.isdir(REC_DIRTY), 'dirty record absent')
    def test_dirty_turn_detected(self):
        rec = dr.decode_record(dr.scan_record(REC_DIRTY))
        self.assertGreaterEqual(rec['summary']['hand_traps']['dirty_turn'], 1)


if __name__ == '__main__':
    unittest.main()
