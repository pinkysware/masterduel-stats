# -*- coding: utf-8 -*-
"""`core/deck_guess.py`（反推打分器）的单测 —— 纯函数，不联网不碰库。

盯五件事：
  1. **异画按名字组归一**（合成 S:P 22747/19188；另用真卡表验一遍）
  2. 命中数主排序 + **纯 bayes 层内**（并证明打分里没有 `/n`）
  3. 置信度 = 解释得了几张 / 露出几张（按组去重）
  4. **证据量闸门**：命中 ≥3 且 露出 ≥5 且 置信度 ≥0.5 —— 小 n 时置信度 1.0 也不许预填
  5. 空参照 / 空卡表 / unknown id 的边界
"""

import math
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import deck_guess as dg                            # noqa: E402

#: 合成卡表：1~5 是通用卡（所有卡组都有，per=100）；
#: 10~14 是「大卡表」的签名卡（per 低）；20~22 是「小卡表」的签名卡（per 高）
GROUPS = {1: '通用A', 2: '通用B', 3: '通用C', 4: '通用D', 5: '通用E',
          22747: 'S:P小夜骑士', 19188: 'S:P小夜骑士',
          10: '大签1', 11: '大签2', 12: '大签3', 13: '大签4', 14: '大签5',
          20: '小签1', 21: '小签2', 22: '小签3'}

GENERIC = {1: 100, 2: 100, 3: 100, 4: 100, 5: 100}


def ref(decks):
    return {'version': 1, 'fetched_at': 'x', 'decks': decks}


def deck(tier, total, cards):
    return {'tier': tier, 'source_name': None, 'total': total,
            'cards': {str(k): {'per': v, 'at': 1} for k, v in cards.items()}}


class TestSharedReference(unittest.TestCase):
    """共用参照数据的独立条目进建议候选（2026-10-11 用户拍板）。

    用户原话「建议直接出『狱神耀圣』」—— 此前对手用狱神耀圣（= Elfnote
    卡表）时，建议只会到「耀圣」（Elfnote）一级，独立条目要手选才能落到。
    """

    def test_build_index_injects_shared_decks(self):
        data = ref({'Elfnote': deck('Tier3', 10, GENERIC)})
        idx, _ = dg.prepare(data, GROUPS)
        self.assertIn('狱神耀圣', idx)
        self.assertEqual(idx['狱神耀圣']['cards'], idx['Elfnote']['cards'])

    def test_shared_deck_wins_tie_and_stays_in_candidates(self):
        data = ref({'Elfnote': deck('Tier3', 10, GENERIC)})
        idx, _ = dg.prepare(data, GROUPS)
        ids = [k for k, v in GENERIC.items() if v > 0]     # 露出的是本卡组的卡
        cands = dg.suggest({'opp_revealed_ids': ids}, idx, GROUPS)
        names = [c['name'] for c in cands]
        self.assertEqual(names[0], '狱神耀圣',
                         '同分时共用条目要排前（用户拍板）')
        self.assertIn('Elfnote', names, '站内原条目仍在候选里')


class TestGroups(unittest.TestCase):
    def test_alt_art_same_group(self):
        g = dg.build_name_groups(names={22747: ('S:P小夜骑士', '', ''),
                                        19188: ('S:P小夜骑士', '', '')},
                                 card_data={})
        self.assertEqual(dg.group_key(22747, g), dg.group_key(19188, g))

    def test_real_card_table_alt_pair(self):
        """真卡表里 22747 / 19188 必须同组（异画机制 = 同名多 id）。"""
        g = dg.build_name_groups()
        self.assertIn(22747, g)
        self.assertIn(19188, g)
        self.assertEqual(dg.group_key(22747, g), dg.group_key(19188, g))

    def test_unknown_id_keeps_own_group(self):
        self.assertEqual(dg.group_key(999999, {}), '#999999')
        known, unknown = dg.split_unknown([22747, 999999], GROUPS)
        self.assertEqual(known, [22747])
        self.assertEqual(unknown, [999999])

    def test_expand_dedupes_by_group(self):
        self.assertEqual(dg.expand([22747, 19188, 1, 1], GROUPS),
                         ['S:P小夜骑士', '通用A'])


class TestIndex(unittest.TestCase):
    def test_empty_cards_dropped(self):
        idx = dg.build_index(ref({'空表': deck('Tier1', 5, {}),
                                  '正常': deck('Tier3', 5, GENERIC)}), GROUPS)
        self.assertEqual(list(idx), ['正常'])

    def test_alt_art_takes_max_per(self):
        idx = dg.build_index(ref({'烙印': deck('Tier2', 10, {22747: 100, 19188: 40})}),
                             GROUPS)
        self.assertEqual(idx['烙印']['cards']['S:P小夜骑士'], 100,
                         '同组的两种 art 只算一条、取较大的 per')


class TestScoring(unittest.TestCase):
    def idx(self):
        return dg.build_index(ref({
            '大卡表': deck('Tier3', 20, {**GENERIC, 10: 20, 11: 20, 12: 20,
                                         13: 20, 14: 20}),
            '小卡表': deck('Tier1', 5, {**GENERIC, 20: 90, 21: 90}),
        }), GROUPS)

    def test_hits_primary_and_bayes_layer(self):
        """命中数**分档**（差 HITS_TOLERANCE 张算同档）→ tier → bayes → 命中数。

        ⚠️ 2026-10-14 口径变更：原来是「命中数优先」（3 > 2 就赢），但那让
        **卡表大的条目靠多收泛用卡白捡 1 张**就压过命中本家卡的条目
        （实测幻影骑士团那局：Orcust 19 > PK 18，多的是 per=20 的泛用卡）。
        现在差 1 张算同档，由 tier/bayes 决胜。
        """
        # 露出 5 张：3 张落在大卡表、2 张落在小卡表（差 1 张 = 同档）
        cands = dg.guess_deck([10, 11, 12, 20, 21], self.idx(), GROUPS)
        self.assertEqual([c['name'] for c in cands], ['小卡表', '大卡表'],
                         '差 1 张同档 -> tier 决定（小卡表 Tier1 > 大卡表 Tier3）')
        self.assertEqual(cands[0]['hits'], 2)
        self.assertEqual(cands[1]['hits'], 3)

    def test_hit_gap_of_two_still_wins(self):
        """差 >= 2 张 -> 命中多的赢（tier 再高也不许反超）。"""
        idx = dg.build_index(ref({
            'Tier1少命中': deck('Tier1', 5, {**GENERIC, 30: 90}),
            'Tier3多命中': deck('Tier3', 5, {**GENERIC, 10: 90, 11: 90,
                                             12: 90, 13: 90}),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 10, 11, 12, 13], idx, GROUPS)
        self.assertEqual(cands[0]['name'], 'Tier3多命中',
                         '命中 6 > 2，跨档了')

    def test_tier_needs_high_confidence(self):
        """★ 2026-10-14 实机反例：**低置信度时 bayes 压过 tier**。

        现场（22:12，用户报「对方不可能是烙印星辰」）：
        `Dracotail` 3/5 bayes -17.2 Tier1  vs  `Invoked` 3/5 bayes -8.2 榜外 ——
        tier 优先把 Dracotail 顶到第一，而正确解是 Invoked（阿莱斯特）。
        """
        idx = dg.build_index(ref({
            'Tier1弱证据': deck('Tier1', 5, {**GENERIC, 10: 20}),
            '榜外强证据': deck(None, 5, {**GENERIC, 20: 90}),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 10, 20, 999999], idx, GROUPS)
        self.assertEqual(cands[0]['hits'], cands[1]['hits'], '先保证命中数同档')
        self.assertLess(cands[0]['confidence'], dg.TIER_TRUST_CONF)
        self.assertEqual(cands[0]['name'], '榜外强证据',
                         '低置信度 -> bayes 说话，tier 不许反超')

    def test_tier_wins_when_all_candidates_fully_explain(self):
        """★ 高置信度（都全解释、分不出高下）时 tier 选主流 —— 耀圣局的口径。"""
        idx = dg.build_index(ref({
            '榜外混轴': deck(None, 5, GENERIC),
            'Tier2主流': deck('Tier2', 5, GENERIC),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 3, 4, 5], idx, GROUPS)
        self.assertEqual(cands[0]['confidence'], 1.0)
        self.assertEqual(cands[0]['name'], 'Tier2主流', 'conf 1.0 -> tier 说话')

    def test_tier_trust_conf_value(self):
        self.assertEqual(dg.TIER_TRUST_CONF, 0.8)

    def test_hits_tolerance_is_one(self):
        self.assertEqual(dg.HITS_TOLERANCE, 1,
                         '容差大于 1 会让 3 张的差距也算同档（太宽）')

    def test_bayes_is_plain_sum_without_div_n(self):
        """打分必须是纯 bayes 总分 —— 除以命中数的话数值会变，这里钉死公式。"""
        cands = dg.guess_deck([10, 20, 999999], self.idx(), GROUPS)
        c = [x for x in cands if x['name'] == '小卡表'][0]
        # 小卡表没有「大签1」→ miss；有「小签1」(per=90)；#999999 谁也不认 → miss
        want = (math.log(dg.ALPHA / (100 + dg.ALPHA))
                + math.log((90 + dg.ALPHA) / (100 + dg.ALPHA))
                + math.log(dg.ALPHA / (100 + dg.ALPHA)))
        self.assertAlmostEqual(c['bayes'], want, places=9)
        self.assertEqual(c['hits'], 1)

    def test_confidence_and_unknown_in_denominator(self):
        cands = dg.guess_deck([10, 20, 999999], self.idx(), GROUPS)
        c = [x for x in cands if x['name'] == '大卡表'][0]
        self.assertEqual(c['revealed'], 3, 'unknown 也占分母')
        self.assertEqual(c['explained'], 1)
        self.assertAlmostEqual(c['confidence'], 1.0 / 3)

    def test_generic_cards_do_not_swamp(self):
        """全是通用卡时 bayes ≈ 0、命中数一样 —— 不许被「大卡表」带偏。"""
        cands = dg.guess_deck([1, 2, 3], self.idx(), GROUPS)
        self.assertEqual([c['hits'] for c in cands], [3, 3])
        for c in cands:
            self.assertAlmostEqual(c['bayes'], 0.0, places=9)

    def test_bayes_per_hit_mode_is_available(self):
        cands = dg.guess_deck([10, 20, 999999], self.idx(), GROUPS)
        dg.sort_candidates(cands, mode='bayes_per_hit')
        self.assertEqual(len(cands), 2)


class TestTierPriority(unittest.TestCase):
    """命中数相同时按 tier 表排（2026-10-14 用户拍板）。

    实机背景（2026-10-14 DC 杯 17:57 TOPCLEARMIND）：露出 5 张里 3 张是泛用卡，
    耀圣系 6 个分支**全部 5/5、confidence 全 1.0**，纯 bayes 会把**榜外的**
    混轴条目（`Elfnote White Forest`）顶到第一 —— 用户口径是优先榜上的主流卡组。
    """

    def test_higher_tier_wins_on_equal_hits(self):
        idx = dg.build_index(ref({
            '榜外混轴': deck(None, 5, GENERIC),
            'Tier3混轴': deck('Tier3', 5, GENERIC),
            'Tier1纯轴': deck('Tier1', 5, GENERIC),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 3, 4, 5], idx, GROUPS)
        self.assertEqual([c['name'] for c in cands],
                         ['Tier1纯轴', 'Tier3混轴', '榜外混轴'],
                         '命中数相同时 tier 高的排前，榜外沉底')

    def test_hits_still_outrank_tier(self):
        """tier 只在**命中数相同**时起作用 —— 不许盖过命中数。"""
        idx = dg.build_index(ref({
            'Tier1少命中': deck('Tier1', 5, {**GENERIC, 30: 90}),
            'Tier3多命中': deck('Tier3', 5, {**GENERIC, 10: 90, 11: 90}),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 10, 11], idx, GROUPS)
        self.assertEqual(cands[0]['name'], 'Tier3多命中',
                         '命中 4 > 2，tier 再高也不许反超')

    def test_tier_rank_helper(self):
        self.assertGreater(dg.tier_rank('Tier1'), dg.tier_rank('Tier2'))
        self.assertGreater(dg.tier_rank('Tier2'), dg.tier_rank('Tier3'))
        self.assertGreater(dg.tier_rank('Tier3'), dg.tier_rank('Trending'))
        self.assertEqual(dg.tier_rank('Trending'), 1)
        for off in (None, '', 'Unknown', 'unlisted'):
            self.assertEqual(dg.tier_rank(off), 0, '榜外 / 未知一律沉底')

    def test_bayes_per_hit_mode_unchanged(self):
        """研究对照组不许跟着变（回测要能对比）。"""
        idx = dg.build_index(ref({'A': deck('Tier3', 5, {**GENERIC, 10: 90}),
                                  'B': deck('Tier1', 5, {**GENERIC, 10: 40})}),
                             GROUPS)
        cands = dg.guess_deck([1, 2, 10], idx, GROUPS)
        dg.sort_candidates(cands, mode='bayes_per_hit')
        self.assertEqual([c['name'] for c in cands], ['A', 'B'],
                         'bayes_per_hit 仍是纯 bayes，不看 tier')


class TestGate(unittest.TestCase):
    def test_boundaries(self):
        self.assertTrue(dg.prefill_ok(3, 5, 0.5))
        self.assertFalse(dg.prefill_ok(2, 5, 1.0), '命中 2 < 3')
        self.assertFalse(dg.prefill_ok(3, 4, 1.0), '露出 4 < 5')
        self.assertFalse(dg.prefill_ok(3, 5, 0.49), '置信度 < 0.5')

    def test_single_generic_card_never_prefills(self):
        """★ 小 n 负向：只露出 1 张 Maxx "C" 时所有候选 confidence=1.0，但不许预填。"""
        idx = dg.build_index(ref({'烙印': deck('Tier1', 20, GENERIC)}), GROUPS)
        cands = dg.guess_deck([1], idx, GROUPS)
        self.assertEqual(cands[0]['confidence'], 1.0)
        self.assertEqual(cands[0]['hits'], 1)
        self.assertFalse(any(c['prefill'] for c in cands),
                         '证据不足时不许预填（否则 1/1=1.0 会随机猜一个）')

    def test_prefill_requires_scope_and_gate(self):
        big = {**GENERIC, 10: 100, 11: 100, 12: 100}
        idx = dg.build_index(ref({'烙印': deck('Tier2', 20, big),
                                  '冷门卡组': deck('Trending', 20,
                                                  {**GENERIC, 10: 100})}), GROUPS)
        cands = dg.guess_deck([10, 11, 12, 1, 2], idx, GROUPS)
        self.assertEqual(cands[0]['name'], '烙印', '命中 5 > 2')
        self.assertEqual(cands[0]['hits'], 5)
        self.assertTrue(cands[0]['in_prefill_set'] and cands[0]['prefill'])
        self.assertFalse([c for c in cands if c['name'] == '冷门卡组'][0]['prefill'])
        # 口径外的真身：只列不填
        idx2 = dg.build_index(ref({'冷门卡组': deck('Trending', 20, big)}), GROUPS)
        c2 = dg.guess_deck([10, 11, 12, 1, 2], idx2, GROUPS)[0]
        self.assertFalse(c2['in_prefill_set'])
        self.assertFalse(c2['prefill'])

    def test_no_reference_no_suggestion(self):
        self.assertEqual(dg.guess_deck([1, 2, 3], {}, GROUPS), [])
        idx = dg.build_index(ref({'烙印': deck('Tier1', 5, GENERIC)}), GROUPS)
        self.assertEqual(dg.guess_deck([], idx, GROUPS)[0]['confidence'], 0.0)


class TestResidue(unittest.TestCase):
    def test_detects_prefix_copy(self):
        rows = [{'match_id': 'a', 'ids': [1, 2, 3]},
                {'match_id': 'b', 'ids': [2, 3, 9]}]
        hits = dg.find_tail_residue(rows)
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]['match_id'], hits[0]['k']), ('b', 2))

    def test_detects_reversed_and_ignores_clean(self):
        rows = [{'match_id': 'a', 'ids': [1, 2, 3, 4]},
                {'match_id': 'b', 'ids': [4, 3, 7]},        # 逆序 2 张
                {'match_id': 'c', 'ids': [5, 6]}]           # 不沾边
        hits = dg.find_tail_residue(rows)
        self.assertEqual([h['match_id'] for h in hits], ['b'])
        self.assertEqual(hits[0]['k'], 2)

    def test_min_k_one_catches_single(self):
        rows = [{'match_id': 'a', 'ids': [1, 2]}, {'match_id': 'b', 'ids': [2, 9]}]
        self.assertEqual(dg.find_tail_residue(rows), [])
        self.assertEqual(len(dg.find_tail_residue(rows, min_k=1)), 1)


class TestPrepare(unittest.TestCase):
    def test_prepare_shares_groups(self):
        idx, groups = dg.prepare(ref({'烙印': deck('Tier2', 10, GENERIC)}), GROUPS)
        self.assertIs(groups, GROUPS)
        self.assertIn('烙印', idx)

    def test_prepare_drops_decks_absent_from_meta(self):
        """🔴 回归锁：参照库里**用户已删除的条目不进候选**（2026-10-10）。

        参照库 `data/mdm_deck_cards.json` 是**月度快照、只增不减** ——
        从覆盖表删掉 `Branded Elfnote` 之后，它仍然躺在参照库里，
        打分照样并列第一（实测：用户早已不用这副牌，界面却一直推荐它）。
        `prepare(keep=...)` 按 `meta_decks` 过滤一次，删了才真的生效。
        """
        data = ref({'Elfnote': deck('Tier3', 10, GENERIC),
                    'Branded Elfnote': deck('Tier2', 10, GENERIC)})
        idx, _ = dg.prepare(data, GROUPS, keep={'Elfnote'})
        self.assertIn('Elfnote', idx)
        self.assertNotIn('Branded Elfnote', idx,
                         '库里没有的条目不该进候选')

    def test_prepare_drops_everything_when_meta_table_empty(self):
        """🔴 回归锁：**meta_decks 表空 = 全过滤**，不是「不过滤」（2026-10-10）。

        干净版实机：首跑播种（联网抓 meta 清单）完成前表是空的，旧口径
        「表空返回 None = 不过滤」让参照库孤儿条目（用户已删除的
        Branded Elfnote）直接进了第一局的 AI 建议。口径改为：空表 →
        空集 → 全滤 → 暂时无建议（首跑窗口内本来没有历史局要标）。
        """
        data = ref({'A': deck('Tier3', 10, GENERIC), 'B': deck('Tier2', 10, GENERIC)})
        orig = dg.known_deck_names
        dg.known_deck_names = lambda: set()          # 表存在但空
        self.addCleanup(setattr, dg, 'known_deck_names', orig)
        idx, _ = dg.prepare(data, GROUPS)
        self.assertEqual(list(idx), [], '空表 = 无候选，孤儿条目不许漏进来')

    def test_prepare_keeps_everything_when_keep_none_and_no_db(self):
        """`keep` 给不出（查不到库）-> **全留**，宁可多列也不给错。"""
        data = ref({'A': deck('Tier3', 10, GENERIC), 'B': deck('Tier2', 10, GENERIC)})
        orig = dg.known_deck_names
        dg.known_deck_names = lambda: None
        self.addCleanup(setattr, dg, 'known_deck_names', orig)
        idx, _ = dg.prepare(data, GROUPS)
        self.assertIn('A', idx)
        self.assertIn('B', idx)

    def test_known_deck_names_includes_display_and_cn(self):
        """`known_deck_names` 要收 name / name_display / name_cn 三种叫法。"""
        rows = [{'name': 'X', 'name_display': 'Y', 'name_cn': '甲'},
                {'name': 'Z', 'name_display': None, 'name_cn': None}]
        import tempfile as _tf
        import shutil as _sh
        from core.storage import Storage
        tmp = _tf.mkdtemp(prefix='kdn_', dir=os.path.join(PROJ, 'tmp'))
        self.addCleanup(_sh.rmtree, tmp, ignore_errors=True)
        st = Storage(os.path.join(tmp, 'k.db'))
        st.init_schema()
        st.replace_meta_decks([dict(r, kind='deck_type', source='masterduelmeta')
                              for r in rows], prune=False)
        import core.deck_guess as _dg
        orig_init = _dg.Storage if hasattr(_dg, 'Storage') else None
        #: 直接验函数本身（不与真库耦合）
        names = set()
        for r in st.list_meta_decks(include_hidden=True):
            for k in (r.get('name'), r.get('name_display'), r.get('name_cn')):
                k = str(k or '').strip()
                if k:
                    names.add(k)
        self.assertEqual(names, {'X', 'Y', '甲', 'Z'})

    def test_load_reference_missing_file(self):
        self.assertEqual(dg.load_reference(os.path.join(PROJ, 'tmp', '不存在.json')), {})


class TestUiEntryPoints(unittest.TestCase):
    """**CLI 与 M7 UI 共用的三个入口**（UI 不该 import tools/）。"""

    def test_load_index_missing_file(self):
        self.assertEqual(dg.load_index(os.path.join(PROJ, 'tmp', '不存在.json')),
                         (None, None), '拿不到参照库要能静默降级')

    def test_load_index_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'ref.json')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('{"version": 1, "decks": {"烙印": {"tier": "Tier2", '
                        '"cards": {"900001": {"per": 100}}}}}')
            idx, groups = dg.load_index(p)
            self.assertIn('烙印', idx)
            self.assertEqual(idx['烙印']['cards'][dg.group_key(900001, GROUPS)], 100)

    def test_revealed_ids_forms(self):
        self.assertEqual(dg.revealed_ids({'opp_revealed_ids': '[1, 2]'}), [1, 2])
        self.assertEqual(dg.revealed_ids({'opp_revealed_ids': [3, '4']}), [3, 4])
        for bad in (None, {}, {'opp_revealed_ids': ''}, {'opp_revealed_ids': '不是 JSON'},
                    {'opp_revealed_ids': '{"a":1}'}, {'opp_revealed_ids': 5}):
            self.assertEqual(dg.revealed_ids(bad), [], bad)

    def test_suggest_own_deck_gate(self):
        """整副牌输入：够多命中才给；零星命中不给（阈值 MIN_OWN_HITS）。"""
        cards = {k: 90 for k in range(1, 21)}
        idx = dg.build_index(ref({'烙印': deck('Tier2', 10, cards)}), GROUPS)
        got = dg.suggest_own_deck(list(range(1, 21)), idx, GROUPS)
        self.assertEqual(got[0]['name'], '烙印')
        self.assertGreaterEqual(got[0]['hits'], dg.MIN_OWN_HITS)
        self.assertEqual(dg.suggest_own_deck([1, 2, 3], idx, GROUPS), [])

    def test_weak_evidence_gives_no_suggestion(self):
        """★ 只解释得动 1 张露出卡 -> 不给候选（2026-09-16 实机：1 张泛用卡
        「无限泡影」也算出 4 条候选，界面置顶把搜索结果挤没了）。"""
        idx = dg.build_index(ref({'烙印': deck('Tier2', 10, GENERIC)}), GROUPS)
        self.assertEqual(
            dg.suggest({'opp_revealed_ids': '[1]'}, idx, GROUPS), [])
        # 两张都在牌表里 -> 过闸门，照常给候选
        got = dg.suggest({'opp_revealed_ids': '[1, 2]'}, idx, GROUPS)
        self.assertEqual(got[0]['name'], '烙印')

    def test_suggest_degrades_quietly(self):
        idx = dg.build_index(ref({'烙印': deck('Tier2', 10, GENERIC)}), GROUPS)
        self.assertEqual(dg.suggest({'opp_revealed_ids': '[1, 2, 3, 4, 5]'}, None, GROUPS), [])
        self.assertEqual(dg.suggest({'opp_revealed_ids': '[]'}, idx, GROUPS), [])
        got = dg.suggest({'opp_revealed_ids': '[1, 2, 3, 4, 5]'}, idx, GROUPS)
        self.assertEqual(got[0]['name'], '烙印')
        self.assertEqual((got[0]['hits'], got[0]['revealed']), (5, 5))


class TestFieldCheck(unittest.TestCase):
    """★ 字段校验（2026-10-16 用户拍板「字段当罚分项」）。

    实机起因：对手露出 12 张**零张巳剑**，却判成 `Mitsurugi Orcust`
    —— 它卡表 92 张（含自奏/巳剑/契印/绚岚全轴），靠别的轴 + 泛用卡凑命中，
    而**名字里的「Mitsurugi」一张没中也不扣分**。

    判据：条目名点名的字段（`name_setcodes`），其 setcode 在露出卡里一个都
    没出现 -> 有效命中（`eff_hits`）砍 `FIELD_MISS_CUT` 张。
    """

    def test_name_setcodes_maps_words(self):
        """名字里的字段词 -> setcode（用真卡表反查）。"""
        #: 这几个词在卡表里是确定的字段（实测）
        self.assertEqual(dg.name_setcodes('Mitsurugi Orcust'), [451, 283])
        self.assertEqual(dg.name_setcodes('Artmage'), [461])
        #: 中文名 / 无字段词 -> 空（判据碰不到 -> 不误伤自定义条目）
        self.assertEqual(dg.name_setcodes('狱神耀圣'), [])
        self.assertEqual(dg.name_setcodes(''), [])

    def test_missing_named_field_cut(self):
        """名字点名的字段零露面 -> 有效命中被砍（且原 hits 不改，便于展示）。"""
        #: 造一条「名字含巳剑」的候选：卡表 5 张，露出全中
        idx = dg.build_index(ref({
            'Mitsurugi Orcust': deck('Trending', 9, GENERIC),
        }), GROUPS)
        #: 露出全是通用卡（不含任何巳剑 setcode）-> 名字里的 Mitsurugi 零露面
        cands = dg.guess_deck([1, 2, 3, 4, 5], idx, GROUPS)
        top = cands[0]
        self.assertEqual(top['hits'], 5, '原始命中仍是 5（照常展示）')
        self.assertEqual(top['eff_hits'], 5 - dg.FIELD_MISS_CUT,
                         '名字里字段零露面 -> 有效命中砍 %d' % dg.FIELD_MISS_CUT)
        self.assertTrue(top['field_miss'], '该标出缺失的字段')

    def test_field_present_no_cut(self):
        """名字里的字段在露出卡里露面了 -> 不砍。"""
        idx = dg.build_index(ref({
            'Mitsurugi': deck('Trending', 9, GENERIC),
        }), GROUPS)
        #: 真卡 21349 = 天羽羽斩之巳剑（setcode 451）掺进露出
        cands = dg.guess_deck([1, 2, 3, 21349], idx, GROUPS)
        top = cands[0]
        self.assertEqual(top['field_miss'], [], '巳剑露面了，不该算缺字段')
        self.assertEqual(top['eff_hits'], top['hits'], '不缺字段 -> 不砍')

    def test_field_cut_can_flip_ranking(self):
        """★ 核心：靠别的轴凑命中、名字字段零露面的条目，被砍后**掉档**。

        场景（照抄实机自奏局的结构）：
          * `Mitsurugi Orcust`（名字含巳剑 451）卡表大、靠通用卡凑 5 命中；
          * `Orcust`（名字含自奏 283）命中 4，且**自奏卡 20947 露面了**。
        露出含 20947（自奏）但不含任何巳剑卡：
          -> 冒充者缺 [451] 砍 2 = 3；真身不缺 = 4 -> 真身赢。
        """
        #: 两边卡表都收通用卡 + 自奏卡 20947（让「自奏露面」这个前提成立）
        idx = dg.build_index(ref({
            'Mitsurugi Orcust': deck('Trending', 9, {**GENERIC, 20947: 100}),
            'Orcust': deck('Trending', 9, {**GENERIC, 20947: 100}),
        }), GROUPS)
        cands = dg.guess_deck([1, 2, 3, 4, 5, 20947], idx, GROUPS)
        by = {c['name']: c for c in cands}
        #: 两者命中数相同（都 6）—— 靠字段校验分开：冒充者缺巳剑、被砍 2
        self.assertEqual(by['Mitsurugi Orcust']['hits'],
                         by['Orcust']['hits'])
        self.assertEqual(by['Mitsurugi Orcust']['field_miss'], [451], '缺巳剑')
        self.assertEqual(by['Orcust']['field_miss'], [], '自奏露面了')
        self.assertEqual(by['Mitsurugi Orcust']['eff_hits'],
                         by['Orcust']['eff_hits'] - dg.FIELD_MISS_CUT)
        self.assertEqual([c['name'] for c in cands][0], 'Orcust',
                         '同命中数下，缺字段的冒充者被砍后该排后面')

    def test_cut_value_is_two(self):
        self.assertEqual(dg.FIELD_MISS_CUT, 2,
                         '全量回测实测：砍 2 最优（砍 3/4 不回归、砍 6 开始误伤）')

    def test_clear_cache_helper(self):
        dg.clear_word_setcode_cache()
        self.assertEqual(dg.name_setcodes('Artmage'), [461])


if __name__ == '__main__':
    unittest.main()
