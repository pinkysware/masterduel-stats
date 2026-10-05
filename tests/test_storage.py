# -*- coding: utf-8 -*-
"""M2 storage 测试。

覆盖：
  1. schema 建表 + 建索引
  2. upsert 新增 / 幂等 / 保留人工标注
  3. opp_decks 同名去重
  4. 待标注队列（含排序）
  5. 统计（SQL 聚合 vs 纯 Python 复算，逐键对账）
  6. 卡组分布的并列顺序（必须等同 Counter.most_common）
  7. jsonl -> SQLite 迁移往返（用真实 20 局逐字段比对）
  8. 迁移后再 upsert，人工字段仍在
  9. 同一局因卡组解析变化算出另一个 match_id 时，不新增重复行
 10. `archive_matches.warn_unsynced` 的漂移提示不误报（判据与 upsert 一致）
 11. meta 卡组清单（M5 数据源）：整体刷新 / 幂等 / 清理掉榜 / 排序 / 过滤
 12. meta 中文名（name_cn）：重抓不冲掉人工值（COALESCE）/ 指定 kind 设置
 13. 卡组覆盖表 CSV 的读取容错（BOM / GBK / 空行 / 表头 / 多列 / 分组）
 14. 自定义条目（站内没有的卡组）：构建 + 不被重抓清理 + 可手动删除
 15. 自定义条目的图：优先取 data/deck_images_custom/（不可再生）
 16. 分组的兜底规则：站内有分组以站内为准，站内没有才用覆盖表
17. 别名 aliases + search_meta_decks（别名命中 / 排序 / 忽略大小写标点 /
     拼错容错 / 隐藏项 / 重抓保留）
18. M5 对手卡组标注：绑定 / 撤销 / 已标注队列 / 我×对手 交叉战绩
19. 卡组日文名 name_ja（覆盖表写入 / 重抓不冲掉 / 日语能搜到）

全部用临时目录，不碰真实的 data/masterduel.db。
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import contextlib
import io
from collections import Counter

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

from core.storage import (Storage, MODE_PVP, MODE_SOLO, MODE_LIVE,  # noqa: E402
                          MODE_UNKNOWN)
from core import storage as st_mod                                 # noqa: E402
import migrate_jsonl_to_sqlite as mig  # noqa: E402
import archive_matches as am  # noqa: E402
import fetch_untapped_api as api  # noqa: E402
import fetch_meta_decks as fmd  # noqa: E402

# 真实数据源：matches.jsonl，或 M2 之后改名成的 .v1.bak 备份
REAL_JSONL = mig.resolve_jsonl()
DONE = ('Win', 'Lose', 'Draw')


def mk(mid, started, result='Win', went_first=True, won_initiative=None,
       deck_id='48171157', deck_name='闪刀姬', mode=None, game_mode=None, **kw):
    """造一条形状与 jsonl 完全一致的对局记录。"""
    row = dict(
        match_id=mid, started_at=started,
        # 有结果 = 看到了结束标记；`(未结束)` = 没看到（掉线/中途退出）
        ended_at=(None if result == '(未结束)' else started),
        my_deck_id=deck_id, my_deck_name=deck_name, deck_source='log',
        seat=0, seat_evidence='deck-hit P0=9/9 P1=0/6',
        won_initiative=won_initiative, went_first=went_first, first_player=0,
        result=result, total_turns=5, duration_sec=300,
        opp_deck_id=None, opp_deck_name=None, opp_deck_note=None,
        opp_revealed=[], opp_revealed_ids=[],
    )
    if mode is not None:      # 真实 v1 jsonl 里没有 mode 这个键，None 就不写
        row['mode'] = mode
    if game_mode is not None:
        row['game_mode'] = game_mode
    row.update(kw)
    return row


def py_recompute(rows):
    """按 v1 `show_stats` 的口径用纯 Python 重算，供 SQL 聚合对账。"""
    done = [r for r in rows if r.get('result') in DONE]
    n = len(done)
    w = sum(1 for r in done if r['result'] == 'Win')
    lose = sum(1 for r in done if r['result'] == 'Lose')
    draw = sum(1 for r in done if r['result'] == 'Draw')
    first = [r for r in done if r.get('went_first') is True]
    second = [r for r in done if r.get('went_first') is False]
    fw = sum(1 for r in first if r['result'] == 'Win')
    sw = sum(1 for r in second if r['result'] == 'Win')
    ini = [r for r in done if r.get('won_initiative') is True]
    nini = [r for r in done if r.get('won_initiative') is False]
    n_ini = len(ini) + len(nini)
    n_dir = len(first) + len(second)
    return {
        'n_total': n,
        'n_win': w,
        'n_lose': lose,
        'n_draw': draw,
        'win_rate': (w / n) if n else None,
        'first_n': len(first),
        'first_wins': fw,
        'first_rate': (fw / len(first)) if first else None,
        'second_n': len(second),
        'second_wins': sw,
        'second_rate': (sw / len(second)) if second else None,
        'first_hand_pct': (len(first) / n_dir) if n_dir else None,
        'initiative_n': n_ini,
        'initiative_rate': (len(ini) / n_ini) if n_ini else None,
        'unmarked': sum(1 for r in done if r.get('opp_deck_id') is None),
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='m2test_')
        self.db = os.path.join(self.d, 'masterduel.db')
        self.st = Storage(self.db)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class TestSchema(Base):
    def test_init_schema_creates_tables(self):
        """5 张表 + 6 个索引；含 v0.4 新增列，且不含推迟的 log_file。"""
        self.st.init_schema()
        self.st.init_schema()          # 重复调用不该报错（IF NOT EXISTS）
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
            " AND name LIKE 'idx_%'")}
        cols = {r[1] for r in conn.execute('PRAGMA table_info(matches)')}
        meta_cols = {r[1] for r in conn.execute('PRAGMA table_info(meta_decks)')}
        conn.close()
        self.assertTrue({'my_decks', 'opp_decks', 'matches', 'meta_decks',
                         'banlist_changes'} <= tables)
        self.assertEqual(len(idx), 6, '应有 6 个索引，实际 %s' % sorted(idx))
        self.assertTrue(
            {'first_player', 'seat_evidence', 'deck_source', 'my_deck_name',
             'opp_revealed', 'opp_revealed_ids'} <= cols)
        self.assertNotIn('log_file', cols)
        self.assertNotIn('log_offset', cols)
        self.assertTrue(
            {'name', 'kind', 'name_cn', 'name_display', 'hidden', 'tier',
             'power', 'popularity', 'slug', 'img_url', 'img_file'} <= meta_cols,
            'meta_decks 列不全')

    def test_meta_decks_unique_name_kind(self):
        """(name, kind) 唯一 —— 同名但一个是 deck_type、一个是 engine 可以共存。"""
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='X', kind='deck_type', tier='Tier1', power=1.0),
            dict(name='X', kind='engine', tier='Tier1', power=2.0),
        ])
        self.assertEqual(self.st.count_meta_decks(), 2)


class TestMetaDecks(Base):
    """M5 数据源：meta 卡组清单（来自 masterduelmeta，按月整体刷新）。"""

    ROWS = [
        dict(name='Kewl Tune Engine', kind='engine', tier='Tier1', power=15.5,
             slug='Kewl%20Tune'),
        dict(name='Dracotail', kind='deck_type', tier='Tier1', power=12.0,
             popularity=7.25, slug='Dracotail'),
        dict(name='Kewl Tune', kind='deck_type', tier='Tier2', power=10.0,
             popularity=5.25, slug='Kewl%20Tune'),
        dict(name='HEROs', kind='deck_type', tier='Trending', power=1.0,
             popularity=2.36, slug='HEROs'),
    ]

    def test_replace_inserts(self):
        self.st.init_schema()
        ins, upd, pruned = self.st.replace_meta_decks(self.ROWS)
        self.assertEqual((ins, upd, pruned), (4, 0, 0))
        self.assertEqual(self.st.count_meta_decks(), 4)

    def test_replace_is_idempotent(self):
        """同一份数据刷两遍：第二遍全是更新，不新增、不重复。"""
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        ins, upd, pruned = self.st.replace_meta_decks(self.ROWS)
        self.assertEqual((ins, upd, pruned), (0, 4, 0))
        self.assertEqual(self.st.count_meta_decks(), 4)

    def test_fix_legacy_tier(self):
        """分组只归一律法值（如「其它」），站内给的合法值不碰。

        用户口径 2026-09-11：分组的合法值只有 Tier1/Tier2/Tier3/Trending，
        「其它」一律并入 Trending。
        """
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='A', kind='deck_type', tier='其它'),
            dict(name='B', kind='deck_type', tier='Tier2'),
            dict(name='C', kind='deck_type', tier=None),
        ])
        self.assertEqual(self.st.fix_legacy_tier('A', 'Trending'), 1)
        self.assertEqual(self.st.fix_legacy_tier('B', 'Trending'), 0)   # 合法值不动
        self.assertEqual(self.st.fix_legacy_tier('C', 'Trending'), 1)   # 空值补齐
        self.assertEqual(self.st.fix_legacy_tier('A', '其它'), 0)       # 非法目标值不改
        tiers = {r['name']: r['tier']
                 for r in self.st.list_meta_decks(include_hidden=True)}
        self.assertEqual(tiers, {'A': 'Trending', 'B': 'Tier2', 'C': 'Trending'})

    def test_norm_tier_rules(self):
        """覆盖表的分组写法归一（一律不带空格；其它/Tier4 -> Trending）。"""
        self.assertEqual(fmd._norm_tier('Tier 2'), 'Tier2')
        self.assertEqual(fmd._norm_tier('tier  3'), 'Tier3')
        self.assertEqual(fmd._norm_tier('Tier4'), 'Trending')
        self.assertEqual(fmd._norm_tier('其它'), 'Trending')
        self.assertEqual(fmd._norm_tier(''), None)

    def test_override_can_replace_image(self):
        """可以直接覆盖某条目的图（站内条目手动换图走这条路）。

        覆盖目录 `data/deck_images_custom/` 优先于站内下载的图 ——
        用户只想换图、不想换条目时用。
        """
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='Branded', kind='deck_type', tier='Tier2',
                 img_file='data/deck_images/Branded.webp'),
        ])
        self.st.set_meta_deck_override(
            'Branded', img_file='data/deck_images_custom/Branded.webp')
        got = self.st.get_meta_deck('Branded')
        self.assertEqual(got['img_file'], 'data/deck_images_custom/Branded.webp')

    def test_engine_rows_are_alias_not_entry(self):
        """engine 分类取消：`Xxx Engine` 只当别名，不再单独建条目。

        实现分两半：① 抓取时丢弃 kind='engine' 的行（除非覆盖表给了「显示英文名」，
        例如 Yummy Engine -> Mitsurugi Yummy）；② 别名写在覆盖表里。
        这里验证 ② —— 「Kewl Tune Engine」能落到 Kewl Tune。
        """
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='Kewl Tune', kind='deck_type', tier='Tier2',
                 aliases=['杀手级调整曲', 'Kewl Tune Engine']),
        ])
        names = [r['name'] for r in self.st.search_meta_decks('Kewl Tune Engine')]
        self.assertEqual(names, ['Kewl Tune'])

    def test_set_deck_image_finds_deck(self):
        """换图工具的卡组名解析：英文名 / 中文名 / 找不到的报错。"""
        import set_deck_image as sdi
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='Branded', kind='deck_type', tier='Tier2', name_cn='烙印'),
        ])
        self.assertEqual(sdi.find_deck(self.st, 'Branded')[0]['name'], 'Branded')
        self.assertEqual(sdi.find_deck(self.st, '烙印')[0]['name'], 'Branded')
        row, err = sdi.find_deck(self.st, '不存在的卡组XYZ')
        self.assertIsNone(row)
        self.assertIn('找不到', err)

    def test_replace_prunes_and_keeps_id(self):
        """掉出榜单的会被清掉；仍在榜的 meta_deck_id 不变。"""
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        before = self.st.get_meta_deck('Dracotail')['meta_deck_id']
        ins, upd, pruned = self.st.replace_meta_decks(self.ROWS[1:2])
        self.assertEqual((ins, upd, pruned), (0, 1, 3))
        self.assertEqual(self.st.count_meta_decks(), 1)
        self.assertEqual(self.st.get_meta_deck('Dracotail')['meta_deck_id'], before)

    def test_replace_prune_false_keeps_stale(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        ins, upd, pruned = self.st.replace_meta_decks(self.ROWS[1:2], prune=False)
        self.assertEqual(pruned, 0)
        self.assertEqual(self.st.count_meta_decks(), 4)

    def test_list_orders_by_tier_then_power(self):
        """排序：Tier1 -> Tier2 -> Tier3 -> Trending；组内按 power 降序。"""
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        got = [r['name'] for r in self.st.list_meta_decks()]
        self.assertEqual(got, ['Kewl Tune Engine', 'Dracotail', 'Kewl Tune', 'HEROs'])

    def test_list_filters_kind_and_tier(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.assertEqual(
            [r['name'] for r in self.st.list_meta_decks(kinds=['engine'])],
            ['Kewl Tune Engine'])
        self.assertEqual(
            [r['name'] for r in self.st.list_meta_decks(tiers=['Tier1', 'Tier2'])],
            ['Kewl Tune Engine', 'Dracotail', 'Kewl Tune'])

    def test_get_meta_deck_prefers_deck_type(self):
        """同名时 kind 省略应优先返回 deck_type。"""
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='X', kind='engine', tier='Tier1', power=2.0),
            dict(name='X', kind='deck_type', tier='Tier2', power=1.0),
        ])
        self.assertEqual(self.st.get_meta_deck('X')['kind'], 'deck_type')
        self.assertEqual(self.st.get_meta_deck('X', kind='engine')['power'], 2.0)

    def test_replace_keeps_manual_cn(self):
        """★ 重抓时没带中文名的条目，**不能冲掉人工填的 name_cn**。

        这是「每月重抓」能不能安全跑的关键：抓取脚本对没查到的卡组
        传 None，靠 SQL 层的 COALESCE 保住用户手改的值。
        """
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.st.set_meta_deck_cn('Dracotail', '星辰')
        # 再抓一次，这次不带任何 name_cn（模拟 JSON 里没这个词条）
        self.st.replace_meta_decks([dict(r) for r in self.ROWS])
        self.assertEqual(self.st.get_meta_deck('Dracotail')['name_cn'], '星辰')

    def test_replace_updates_cn_when_provided(self):
        """带了中文名就以新的为准（JSON 是中文名的真源）。"""
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.st.set_meta_deck_cn('Dracotail', '旧名')
        rows = [dict(r) for r in self.ROWS]
        rows[1]['name_cn'] = '星辰'
        self.st.replace_meta_decks(rows)
        self.assertEqual(self.st.get_meta_deck('Dracotail')['name_cn'], '星辰')

    def test_set_meta_deck_cn_by_kind(self):
        self.st.init_schema()
        self.st.replace_meta_decks([
            dict(name='X', kind='engine', tier='Tier1', power=2.0),
            dict(name='X', kind='deck_type', tier='Tier2', power=1.0),
        ])
        self.assertEqual(self.st.set_meta_deck_cn('X', '甲', kind='engine'), 1)
        self.assertEqual(self.st.get_meta_deck('X', kind='engine')['name_cn'], '甲')
        self.assertIsNone(self.st.get_meta_deck('X', kind='deck_type')['name_cn'])

    def test_set_meta_deck_cn_missing_returns_zero(self):
        self.st.init_schema()
        self.assertEqual(self.st.set_meta_deck_cn('不存在', '甲'), 0)

    def test_replace_keeps_custom_rows(self):
        """★ 重抓时，自定义条目（source='custom'）**不能被清理掉**。

        站内抓取永远不会有它们，靠 prune 的排除条件保住。
        """
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.st.replace_meta_decks([dict(
            name='Branded Elfnote', kind='deck_type', tier='Tier2',
            name_cn='烙印耀圣', source='custom')])
        # 再抓一次站内数据（这次抓取里没有自定义条目）
        ins, upd, pruned = self.st.replace_meta_decks(self.ROWS)
        self.assertEqual(pruned, 0, '自定义条目不该被清理')
        self.assertIsNotNone(self.st.get_meta_deck('Branded Elfnote'),
                             '自定义条目被误清')
        # 反过来：站内条目不在抓取结果里时，照样要清
        ins, upd, pruned = self.st.replace_meta_decks(
            [dict(name='只有这一条', kind='deck_type', tier='Tier1')])
        self.assertEqual(pruned, 4, '站内条目该被清理')

    def test_delete_meta_deck(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.assertEqual(self.st.delete_meta_deck('Dracotail'), 1)
        self.assertIsNone(self.st.get_meta_deck('Dracotail'))
        self.assertEqual(self.st.delete_meta_deck('Dracotail'), 0)

    def test_hidden_excluded_from_picker(self):
        """隐藏的条目不进选择框（list_meta_decks 默认过滤）。"""
        self.st.init_schema()
        rows = [dict(r) for r in self.ROWS]
        rows[0]['hidden'] = 1
        self.st.replace_meta_decks(rows)
        self.assertEqual(len(self.st.list_meta_decks()), 3)
        self.assertEqual(len(self.st.list_meta_decks(include_hidden=True)), 4)
        self.assertEqual(self.st.count_meta_decks(), 4)
        self.assertEqual(self.st.count_meta_decks(include_hidden=False), 3)

    def test_search_by_alias(self):
        """★ 别名要能命中同一条 —— 这就是「不死均 / Zombies 都指向南瓜王」。"""
        self.st.init_schema()
        rows = [dict(r) for r in self.ROWS]
        rows[2]['aliases'] = ['Zombies', '不死均']
        self.st.replace_meta_decks(rows)
        for q in ('Zombies', '不死均', 'zombies'):
            got = self.st.search_meta_decks(q)
            self.assertTrue(got, '%s 应有结果' % q)
            self.assertEqual(got[0]['name'], 'Kewl Tune', '%s 应命中带别名那条' % q)

    def test_search_ranking(self):
        """完全相等 > 前缀 > 子串。"""
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        got = [r['name'] for r in self.st.search_meta_decks('kewl tune')]
        self.assertEqual(got[0], 'Kewl Tune', '完全相等排最前')
        self.assertIn('Kewl Tune Engine', got, '前缀/子串也要出来')

    def test_search_ignores_case_and_punct(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        for q in ('DRACOTAIL', 'dracotail', 'Draco-tail', 'dracotail.'):
            self.assertTrue(self.st.search_meta_decks(q), '%s 应有结果' % q)

    def test_search_fuzzy_typo(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        got = self.st.search_meta_decks('dracotil')      # 漏了个 a
        self.assertTrue(got)
        self.assertEqual(got[0]['name'], 'Dracotail')

    def test_search_excludes_hidden_by_default(self):
        self.st.init_schema()
        rows = [dict(r) for r in self.ROWS]
        rows[1]['hidden'] = 1
        self.st.replace_meta_decks(rows)
        self.assertEqual(self.st.search_meta_decks('Dracotail'), [])
        self.assertTrue(self.st.search_meta_decks('Dracotail', include_hidden=True))

    def test_search_no_match_returns_empty(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.assertEqual(self.st.search_meta_decks('完全不存在的东西'), [])

    def test_aliases_survive_refetch(self):
        """重抓没带 aliases 时，不能把库里的别名冲掉（COALESCE）。"""
        self.st.init_schema()
        rows = [dict(r) for r in self.ROWS]
        rows[2]['aliases'] = ['Zombies']
        self.st.replace_meta_decks(rows)
        self.st.replace_meta_decks([dict(r) for r in self.ROWS])   # 不带 aliases
        self.assertEqual(self.st.get_meta_deck('Kewl Tune')['aliases'], ['Zombies'])

    def test_set_override_writes_aliases(self):
        self.st.init_schema()
        self.st.replace_meta_decks(self.ROWS)
        self.st.set_meta_deck_override('Dracotail', aliases=['星辰', 'Star'])
        self.assertEqual(self.st.get_meta_deck('Dracotail')['aliases'],
                         ['星辰', 'Star'])
        self.st.set_meta_deck_override('Dracotail', aliases=[])
        self.assertEqual(self.st.get_meta_deck('Dracotail')['aliases'], [])

    def test_get_meta_deck_missing_returns_none(self):
        self.st.init_schema()
        self.assertIsNone(self.st.get_meta_deck('不存在'))


class TestUpsert(Base):
    def test_upsert_match_new(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.assertEqual(self.st.count(), 1)
        got = self.st.get_match('m1')
        self.assertEqual(got['result'], 'Win')
        self.assertIs(got['went_first'], True)      # 读出来是 bool，不是 1
        self.assertEqual(got['opp_revealed'], [])   # JSON 列还原成 list

    def test_upsert_match_idempotent(self):
        self.st.init_schema()
        for _ in range(2):
            self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.assertEqual(self.st.count(), 1)
        self.assertEqual(self.st.get_match('m1')['match_id'], 'm1')

    def test_upsert_match_preserves_manual(self):
        """重解析（人工字段为 NULL）不能抹掉已标注的对手卡组。"""
        self.st.init_schema()
        oid = self.st.upsert_opp_deck('烙印')
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.st.mark_opp_deck('m1', opp_deck_id=oid, name='烙印', note='烙印融合')
        # 同一局再 upsert 一次，人工字段全是 NULL
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        got = self.st.get_match('m1')
        self.assertEqual(got['opp_deck_id'], oid)
        self.assertEqual(got['opp_deck_name'], '烙印')
        self.assertEqual(got['opp_deck_note'], '烙印融合')
        # 但日志派生字段仍要按新值覆盖
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000', result='Lose'))
        self.assertEqual(self.st.get_match('m1')['result'], 'Lose')

    def test_rescrape_never_wipes_my_deck_name(self):
        """🔴 2026-10-01 事故回归：重解析不能把已有的我方卡组名冲掉。

        卡组兜底的新鲜度闸门是**时间相关**的（当天入库有 current-deck 兜底、
        隔天重解析拒绝 -> NULL），全量覆盖曾把整列名字冲回 NULL。
        """
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        # 同一局重解析：快照过期 -> 名字变 NULL -> 旧值必须保住
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000',
                                deck_id=None, deck_name=None))
        self.assertEqual(self.st.get_match('m1')['my_deck_name'], '闪刀姬')

    def test_rescrape_keeps_manual_and_pseudo_manual_names(self):
        """人工/修复来源的名字（src 非自动）连**不同的新值**也不许覆盖。

        （09-28 把 48 行 K9vs 统一成 K9VS，一次「刷新数据」就 reverted ——
        名字没有更真的自动源，改值只走行级修正。）
        """
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.st.set_my_deck_for_record('m1', '闪刀姬', src='manual')
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000',
                                deck_name='K9vs'))
        got = self.st.get_match('m1')
        self.assertEqual(got['my_deck_name'], '闪刀姬')
        self.assertEqual(got['my_deck_name_src'], 'manual')
        # 直接 UPDATE 的统一名（src=NULL）同样受保护
        with self.st._open() as conn:
            conn.execute("UPDATE matches SET my_deck_name = 'K9VS',"
                         " my_deck_name_src = NULL WHERE match_id = 'm1'")
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000',
                                deck_name='K9vs'))
        self.assertEqual(self.st.get_match('m1')['my_deck_name'], 'K9VS')
        # 但「已知自动来源」的旧值允许被新解析刷新
        self.st.set_my_deck_for_record('m1', '旧名', src='snapshot-name')
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000',
                                deck_name='新名'))
        self.assertEqual(self.st.get_match('m1')['my_deck_name'], '新名')

    def test_rescrape_keeps_api_backfilled_result(self):
        """🔴 2026-10-01 事故回归：API 补录的胜负（src=untapped-api-…）
        曾被重解析的 NULL 冲回 —（保护圈原来只认 `manual` 一种 src）。"""
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-29 10:59:00.000',
                                result='(未结束)'))
        self.st.set_result('m1', 'Win', src='untapped-api-20260929')
        # 重解析这局仍然没有结果（EM 没抓到）-> 不许冲掉补录值
        self.st.upsert_match(mk('m1', '2026-09-29 10:59:00.000',
                                result='(未结束)'))
        got = self.st.get_match('m1')
        self.assertEqual(got['result'], 'Win')
        self.assertEqual(got['result_src'], 'untapped-api-20260929')
        # NULL-src 的旧值维持老契约：日志派生字段，重解析按新值覆盖
        with self.st._open() as conn:
            conn.execute("UPDATE matches SET result = 'Lose',"
                         " result_src = NULL WHERE match_id = 'm1'")
        self.st.upsert_match(mk('m1', '2026-09-29 10:59:00.000',
                                result='Win'))
        self.assertEqual(self.st.get_match('m1')['result'], 'Win')

    def test_set_result_if_null_by_record(self):
        """结算码提前直写：**只补空** —— 已有值（含人工值）一律不动。"""
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        with self.st._open() as conn:
            conn.execute("UPDATE matches SET record_dir = 'd1',"
                         " result = NULL, result_src = NULL"
                         " WHERE match_id = 'm1'")
        self.assertEqual(
            self.st.set_result_if_null_by_record('d1', 'Win', 'duel_code'), 1)
        self.assertEqual(self.st.get_match('m1')['result'], 'Win')
        self.assertEqual(self.st.get_match('m1')['result_src'], 'duel_code')
        # 已有值 -> 不覆盖
        self.assertEqual(
            self.st.set_result_if_null_by_record('d1', 'Lose', 'duel_code'), 0)
        self.assertEqual(self.st.get_match('m1')['result'], 'Win')
        # 人工值更不许动
        self.st.set_result('m1', 'Lose', src='manual')
        self.assertEqual(
            self.st.set_result_if_null_by_record('d1', 'Win', 'duel_code'), 0)
        self.assertEqual(self.st.get_match('m1')['result'], 'Lose')

    def test_account_stamp_and_filter(self):
        """多账号（2026-10-03）：盖章只补空 + account 筛选贯穿统计。"""
        self.st.init_schema()
        # game_mode=3（天梯）：get_stats 默认主口径只算 3/19
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000', game_mode=3))
        self.st.upsert_match(mk('m2', '2026-09-11 10:00:00.000', game_mode=3))
        with self.st._open() as conn:
            conn.execute("UPDATE matches SET record_dir = 'd1'"
                         " WHERE match_id = 'm1'")
        # 盖章
        self.assertEqual(self.st.set_account_for_record('d1', '甲', 111), 1)
        # 幂等（只补空）：再盖/盖别人都不动
        self.assertEqual(self.st.set_account_for_record('d1', '乙', 222), 0)
        got = self.st.get_match('m1')
        self.assertEqual(got['account_name'], '甲')
        self.assertEqual(got['account_pcode'], 111)
        # 重解析 upsert（新值 NULL）不许冲掉章
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.assertEqual(self.st.get_match('m1')['account_name'], '甲')
        # account 筛选：统计与最近对局都只认该账号
        self.st.set_result('m1', 'Win', src='manual')
        self.assertEqual(self.st.get_stats(account='甲')['n_total'], 1)
        self.assertEqual(self.st.get_stats(account='乙')['n_total'], 0)
        self.assertEqual(len(self.st.get_recent_matches(limit=10)), 2)
        self.assertEqual([r['match_id'] for r in
                          self.st.get_recent_matches(limit=10, account='甲')],
                         ['m1'])
        # 账号清单（未分账号的历史局不列）
        opts = self.st.account_options()
        self.assertEqual([o[0] for o in opts], ['甲'])

    def test_account_options_shape(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        with self.st._open() as conn:
            conn.execute("UPDATE matches SET record_dir = 'd1',"
                         " account_name = '甲', account_pcode = 111"
                         " WHERE match_id = 'm1'")
        opts = self.st.account_options()
        self.assertEqual(len(opts), 1)
        name, n, last = opts[0]
        self.assertEqual(name, '甲')
        self.assertEqual(n, 1)
        self.assertEqual(last, '2026-09-10 10:00:00.000')

    def test_upsert_opp_deck_dedup(self):
        self.st.init_schema()
        a = self.st.upsert_opp_deck('烙印')
        b = self.st.upsert_opp_deck('烙印')
        c = self.st.upsert_opp_deck('闪刀姬', aliases=['闪刀'])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        with self.st._open() as conn:
            n = conn.execute('SELECT COUNT(*) AS n FROM opp_decks').fetchone()['n']
        self.assertEqual(n, 2)

    def test_upsert_match_skips_same_started_at_other_id(self):
        """同一局因卡组解析变化算出另一个 match_id -> 不新增重复行。

        真实案例：2026-09-07 09:47:17.650 那局，日志里 `Selected deck changed`
        被轮转掉后 deckId 从 48171157 变成 null，match_id 随之改变。
        """
        self.st.init_schema()
        ts = '2026-09-07 09:47:17.650'
        first = mk('id-a', ts, deck_id='48171157', deck_name='闪刀姬')
        self.assertEqual(self.st.upsert_match(first), 'written')
        # 同一局、同一开始时间，但卡组解析不出来 -> 另一个 match_id
        second = mk('id-b', ts, '(未结束)', deck_id=None, deck_name=None)
        self.assertEqual(self.st.upsert_match(second), 'skipped')
        self.assertEqual(self.st.count(), 1)
        got = self.st.get_match('id-a')
        self.assertEqual(got['my_deck_id'], '48171157')     # 原记录未被改动
        self.assertIsNone(self.st.get_match('id-b'))
        # 批量接口也汇报跳过数
        again = self.st.upsert_many([first, second])
        self.assertEqual(again, {'written': 1, 'skipped': 1, 'skipped-live': 0})

    def test_upsert_match_skips_live(self):
        """观战/回放不是对局 -> 压根不入库。"""
        self.st.init_schema()
        self.assertEqual(
            self.st.upsert_match(mk('live1', '2026-09-07 09:47:17.650',
                                    '(未结束)', mode=MODE_LIVE)),
            'skipped-live')
        self.assertEqual(self.st.count(), 0)

    def test_upsert_match_abandon_becomes_lose(self):
        """天梯局没等到结束标记（中途退出/掉线）-> 直接判负，ended_at 留空。"""
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 23:13:18.837',
                                '(未结束)', mode=MODE_PVP))
        got = self.st.get_match('m1')
        self.assertEqual(got['result'], 'Lose')
        self.assertIsNone(got['ended_at'])   # 保留「没看到结束」的标记
        # 单人局不判负
        self.st.upsert_match(mk('m2', '2026-09-09 12:40:54.045',
                                '(未结束)', mode=MODE_SOLO))
        self.assertEqual(self.st.get_match('m2')['result'], '(未结束)')
        # 正常结束的天梯局不动
        self.st.upsert_match(mk('m3', '2026-09-10 21:25:08.464', 'Lose',
                                mode=MODE_PVP))
        self.assertEqual(self.st.get_match('m3')['result'], 'Lose')

    def test_delete_match(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000'))
        self.assertTrue(self.st.delete_match('m1'))
        self.assertFalse(self.st.delete_match('m1'))
        self.assertEqual(self.st.count(), 0)


class TestModeFilter(Base):
    def test_stats_excludes_solo(self):
        """单人 Solo 不进统计、不进待标注队列。"""
        self.st.init_schema()
        day = '2026-09-09'
        self.st.upsert_match(mk('p1', day + ' 12:11:14.778', 'Win',
                                mode=MODE_PVP, game_mode=3))
        for i, t in enumerate(('12:40:54.045', '12:42:04.945', '12:42:47.086')):
            self.st.upsert_match(mk('s%d' % i, day + ' ' + t, 'Win', mode=MODE_SOLO))
        st = self.st.get_stats()
        self.assertEqual(st['n_total'], 1)          # 只算 PvP
        self.assertEqual(st['n_win'], 1)
        self.assertEqual(self.st.get_deck_distribution(), [('闪刀姬', 1)])
        self.assertEqual([r['match_id'] for r in self.st.get_unmarked_matches()],
                         ['p1'])
        self.assertEqual(self.st.get_today_stats(day)['n_total'], 1)

    def test_unknown_mode_not_counted(self):
        """口径收紧后：判不出模式的旧记录**不计入统计**（用户只要天梯+巅峰）。"""
        self.st.init_schema()
        self.st.upsert_match(mk('u1', '2026-09-06 18:29:17.374', 'Win',
                                mode=MODE_UNKNOWN))
        self.assertEqual(self.st.get_stats()['n_total'], 0)

    def test_counted_game_modes(self):
        """只算 game_mode 3(排名决斗) / 19(巅峰决斗)，活动赛(11/20)不算。"""
        self.st.init_schema()
        day = '2026-09-06 '
        self.st.upsert_match(mk('r1', day + '18:00:00.000', 'Win',
                                mode=MODE_PVP, game_mode=3))
        self.st.upsert_match(mk('r2', day + '18:10:00.000', 'Win',
                                mode=MODE_PVP, game_mode=19))
        self.st.upsert_match(mk('e1', day + '18:20:00.000', 'Win',
                                mode=MODE_PVP, game_mode=11))
        self.st.upsert_match(mk('e2', day + '18:30:00.000', 'Win',
                                mode=MODE_PVP, game_mode=20))
        st = self.st.get_stats()
        self.assertEqual(st['n_total'], 2)
        self.assertEqual(st['n_win'], 2)
        self.assertEqual(self.st.get_deck_distribution(), [('闪刀姬', 2)])

    def test_disconnect_counted_without_api(self):
        """掉线局 API 里没有 → 靠 mode='pvp' + ended_at IS NULL 兜住。"""
        self.st.init_schema()
        self.st.upsert_match(mk('d1', '2026-09-10 23:13:18.837', '(未结束)',
                                mode=MODE_PVP))
        self.assertEqual(self.st.get_stats()['n_total'], 1)
        # 但同模式、有结束标记的（活动赛）不算
        self.st.upsert_match(mk('e1', '2026-09-09 12:11:14.778', 'Win',
                                mode=MODE_PVP))
        self.assertEqual(self.st.get_stats()['n_total'], 1)


class TestDuelScope(Base):
    """界面口径（用户 2026-09-16）：总览/先后攻统计要算**天梯+巅峰+活动**。

    ⚠️ 主口径（默认）**一个字没变**：只 3+19。
    """

    def setUp(self):
        super().setUp()
        self.st.init_schema()

    def test_event_counted_only_in_duel_scope(self):
        self.st.upsert_match(mk('r1', '2026-09-16 10:00:00.000', game_mode=3))
        self.st.upsert_match(mk('d1', '2026-09-16 10:10:00.000', game_mode=21))
        self.st.upsert_match(mk('d2', '2026-09-16 10:20:00.000', game_mode=21,
                                result='Lose'))
        self.assertEqual(self.st.get_stats()['n_total'], 1)                    # 主口径
        self.assertEqual(
            self.st.get_stats(mode_scope='duel')['n_total'], 3)                # 界面口径

    def test_solo_live_and_unknown_still_excluded(self):
        """观战/单人、以及模式判不出的历史行**都不算**（两种口径都不算）。"""
        self.st.upsert_match(mk('s1', '2026-09-16 11:00:00.000', mode='solo'))
        self.st.upsert_match(mk('l1', '2026-09-16 11:10:00.000', mode='live'))
        self.st.upsert_match(mk('u1', '2026-09-16 11:20:00.000'))
        self.assertEqual(self.st.get_stats(mode_scope='duel')['n_total'], 0)

    def test_pve_game_mode_9_excluded_trial_15_is_event(self):
        """单人模式（9）两种口径都不算；Duel Trial（15）仍算活动（用户 09-26 拍板）。"""
        self.st.upsert_match(mk('s9', '2026-09-26 13:27:00.000', game_mode=9))
        self.st.upsert_match(mk('s15', '2026-09-21 23:29:00.000', game_mode=15,
                                result='Win'))
        self.assertEqual(self.st.get_stats()['n_total'], 0)
        self.assertEqual(self.st.get_stats(mode_scope='duel')['n_total'], 1)
        self.assertEqual(self.st.get_stats(mode_group='event')['n_total'], 1)
        from core import storage as st_mod
        self.assertEqual(st_mod.mode_group(9), 'solo')
        self.assertEqual(st_mod.mode_group(15), 'event')

    def test_mode_group_still_wins(self):
        """显式给了 mode_group 就按它算（不受 mode_scope 影响）。"""
        self.st.upsert_match(mk('d1', '2026-09-16 10:10:00.000', game_mode=21))
        self.assertEqual(self.st.get_stats(mode_group='event')['n_total'], 1)
        self.assertEqual(self.st.get_stats(mode_group='ranked')['n_total'], 0)


class TestModeGroupStats(Base):
    """按模式分类**单独统计**（用户 2026-09-13 拍板：杯赛单独统计、不入主口径）。

    设计见 `docs/分模式统计计划.md`。盯三件事：
      ① SQL 从 `MODE_GROUP_BY_GAME_MODE` **生成**（不许第二份口径）；
      ② **默认行为不变**（不传 = 主口径 3 + 19）；
      ③ 活动组的边界 = 「其余**且非 NULL**」。
    """

    def _seed(self):
        self.st.init_schema()
        d = '2026-09-06 '
        self.st.upsert_match(mk('g-r', d + '18:00:00.000', 'Win',
                                mode=MODE_PVP, game_mode=3))
        self.st.upsert_match(mk('g-p', d + '18:10:00.000', 'Lose',
                                mode=MODE_PVP, game_mode=19))
        self.st.upsert_match(mk('g-dc', d + '18:20:00.000', 'Win',
                                mode=MODE_PVP, game_mode=12))
        self.st.upsert_match(mk('g-ev', d + '18:30:00.000', 'Lose',
                                mode=MODE_PVP, game_mode=21))
        self.st.upsert_match(mk('g-none', d + '18:40:00.000', 'Win',
                                mode=MODE_UNKNOWN))

    def test_clause_generated_from_mapping(self):
        """★ 条件从映射表生成 —— 手写第二份 (3,19,12) 是 DC 杯那轮审查点过的坑。"""
        self.assertEqual(
            st_mod.mode_group_clause('ranked'),
            "(game_mode IN (3) OR (game_mode IS NULL"
            " AND mode = 'pvp' AND ended_at IS NULL))",
            '天梯组含掉线兜底，否则分组和 ≠ 主口径')
        self.assertEqual(st_mod.mode_group_clause('peak'), 'game_mode IN (19)')
        self.assertEqual(st_mod.mode_group_clause('dc'), 'game_mode IN (12)')
        self.assertEqual(
            st_mod.mode_group_clause('event'),
            'game_mode NOT IN (3,9,12,19) AND game_mode IS NOT NULL')
        self.assertIsNone(st_mod.mode_group_clause('all'))

    def test_clause_follows_mapping_table(self):
        """往映射表里加一个 mode，clause 自动跟上（单一口径源的实证）。"""
        old = st_mod.MODE_GROUP_BY_GAME_MODE
        patched = dict(old)
        patched[99] = st_mod.MODE_GROUP_DC      # `**{99: ...}` 不行：关键字必须 str
        st_mod.MODE_GROUP_BY_GAME_MODE = patched
        try:
            self.assertEqual(st_mod.mode_group_clause('dc'),
                             'game_mode IN (12,99)')
            self.assertEqual(
                st_mod.mode_group_clause('event'),
                'game_mode NOT IN (3,9,12,19,99) AND game_mode IS NOT NULL')
        finally:
            st_mod.MODE_GROUP_BY_GAME_MODE = old

    def test_unknown_group_raises(self):
        """不认识的分类要炸 —— 绝不静默返回全量（否则会看到错数字还不知道）。"""
        for bad in ('xxx', '', 'RANKED', None, 'dc '):
            with self.assertRaises(ValueError, msg=bad):
                st_mod.mode_group_clause(bad)

    def test_each_group_counts_only_itself(self):
        self._seed()
        self.assertEqual(self.st.get_stats(mode_group='ranked')['n_total'], 1)
        self.assertEqual(self.st.get_stats(mode_group='peak')['n_total'], 1)
        dc = self.st.get_stats(mode_group='dc')
        self.assertEqual((dc['n_total'], dc['n_win'], dc['n_lose']), (1, 1, 0))
        ev = self.st.get_stats(mode_group='event')
        self.assertEqual((ev['n_total'], ev['n_win'], ev['n_lose']), (1, 0, 1))
        self.assertEqual(self.st.get_stats(mode_group='all')['n_total'], 5)

    def test_event_excludes_null_mode_row(self):
        """活动组 = 「其余**且非 NULL**」：判不出模式的旧行不算活动。"""
        self.st.init_schema()
        self.st.upsert_match(mk('n1', '2026-09-06 18:00:00.000', 'Win',
                                mode=MODE_UNKNOWN))
        self.st.upsert_match(mk('e1', '2026-09-06 18:10:00.000', 'Win',
                                mode=MODE_PVP, game_mode=21))
        self.assertEqual(self.st.get_stats(mode_group='event')['n_total'], 1)
        self.assertEqual(self.st.get_stats(mode_group='all')['n_total'], 2)

    def test_ranked_includes_disconnect_fallback(self):
        """掉线天梯局（模式判不出）算进**天梯组** —— 否则分组和 ≠ 主口径。"""
        self.st.init_schema()
        self.st.upsert_match(mk('r1', '2026-09-06 18:00:00.000', 'Win',
                                mode=MODE_PVP, game_mode=3))
        self.st.upsert_match(mk('dc1', '2026-09-06 18:10:00.000', 'Win',
                                mode=MODE_PVP, game_mode=12))
        self.st.upsert_match(mk('d1', '2026-09-06 18:20:00.000', '(未结束)',
                                mode=MODE_PVP))          # 掉线兜底那局
        self.assertEqual(self.st.get_stats(mode_group='ranked')['n_total'], 2)
        grouped = sum(s['n_total'] for k, _n, s in self.st.get_group_stats()
                      if k in ('ranked', 'peak'))
        self.assertEqual(grouped, self.st.get_stats()['n_total'],
                         '主口径 == 天梯组 + 巅峰组')

    def test_default_unchanged_is_main_caliber(self):

        """★★ 不回归：不传 mode_group == 主口径（3 + 19），一个字都不变。"""
        self._seed()
        default = self.st.get_stats()
        self.assertEqual(default['n_total'], 2)          # 只 3 + 19
        self.assertEqual(default, self.st.get_stats(mode_group=None))
        manual = [r for r in self.st.list_matches(limit=None)
                  if r['game_mode'] in (3, 19)]
        self.assertEqual(default['n_total'], len(manual))
        self.assertEqual(default['n_win'],
                         len([r for r in manual if r['result'] == 'Win']))

    def test_get_group_stats_order_and_names(self):
        self._seed()
        rows = self.st.get_group_stats()
        self.assertEqual([k for k, _n, _s in rows],
                         ['ranked', 'peak', 'dc', 'event'])
        self.assertEqual([n for _k, n, _s in rows],
                         ['排名决斗', '巅峰决斗', 'DC杯', '活动决斗'])
        self.assertEqual([s['n_total'] for _k, _n, s in rows], [1, 1, 1, 1])
        only = self.st.get_group_stats(groups=['dc', 'event'])
        self.assertEqual([k for k, _n, _s in only], ['dc', 'event'])

    def test_show_group_stats_output(self):
        """CLI 那张表的纯打印（不碰真库 / 真日志）。"""
        rows = [('ranked', '排名决斗',
                 {'n_total': 10, 'n_win': 6, 'n_lose': 4, 'n_draw': 0}),
                ('dc', 'DC杯',
                 {'n_total': 0, 'n_win': 0, 'n_lose': 0, 'n_draw': 0})]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            am.show_group_stats(rows)
        out = buf.getvalue()
        self.assertIn('按模式分类', out)
        self.assertIn('ranked', out)
        self.assertIn('60.0%', out)
        self.assertIn('DC杯', out)
        self.assertIn('属正常', out, '空杯赛组要说明是预期的，别让人以为坏了')
        self.assertIn('主口径 = 天梯组 + 巅峰组 = %d 场'
                      % (6 + 4), out, '要把「主口径 = 天梯 + 巅峰」这层关系打印出来')


def _ts(text):
    """本地时间字符串 -> unix 秒（API 的 match_start 用它）。"""
    import datetime
    return int(datetime.datetime.strptime(text, '%Y-%m-%d %H:%M:%S').timestamp())


def api_game(mid, match_start, mode=3, win=True, first=True, coin=True,
             turns=2, deck='K9vs'):
    """造一条 Untapped API 的对局记录。"""
    return {
        'match_id': mid, 'match_start': match_start, 'game_mode': mode,
        'friendly_team_id': 1, 'opponent_team_id': 0,
        'winning_team_id': 1 if win else 0,
        'initiative_player_id': 1 if coin else 0,      # 硬币/选择权
        'friendly_deck_id': '1287097', 'friendly_deck_name': deck,
        'opponent_player_name': '测试对手',
        'duels': [{'turn_count': turns, 'first_player_id': 1 if first else 0,
                   'duel_duration_seconds': 300}],
    }


class TestApiImport(Base):
    """从 Untapped API 补录对局。"""

    def test_api_to_row_maps_correct_fields(self):
        """字段对应关系：friendly_team_id->seat，first_player_id->went_first，
        initiative_player_id->won_initiative（**不是**先攻）。"""
        self.st.init_schema()
        row = api.api_to_row(api_game('m-1', _ts('2026-09-02 11:30:18'),
                                      turns=1))
        self.assertEqual(row['seat'], 1)
        self.assertTrue(row['went_first'])
        self.assertTrue(row['won_initiative'])
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['total_turns'], 1)
        self.assertEqual(row['game_mode'], 3)
        self.assertEqual(row['origin'], 'api')
        self.assertTrue(row['match_id'].startswith('api:'))
        # 硬币/先攻可以不一致：拿硬币却选了后攻
        row2 = api.api_to_row(api_game('m-2', _ts('2026-09-02 11:31:00'),
                                       first=True, coin=False))
        self.assertFalse(row2['won_initiative'])
        self.assertTrue(row2['went_first'])

    def test_import_keeps_back_to_back_games(self):
        """同一天连着打的两局（实测最短隔 43 秒）必须都补进来。

        踩过的坑：判重窗口开到 180 秒时，16 局只补进 12 局。
        """
        self.st.init_schema()
        g1 = api_game('m-1', _ts('2026-09-02 11:56:00'))
        g2 = api_game('m-2', _ts('2026-09-02 11:56:43'))
        n_new, _, _ = api.import_games(self.st, [g1, g2], log=lambda *a: None)
        self.assertEqual(n_new, 2)
        self.assertEqual(self.st.count(), 2)

    def test_import_skips_when_log_row_exists(self):
        """日志已经采到的局不再补录（日志行比 API 早约 10 秒）。"""
        self.st.init_schema()
        self.st.upsert_match(mk('log-1', '2026-09-10 21:25:08.464', 'Lose',
                                mode=MODE_PVP, game_mode=3))
        n_new, n_have, _ = api.import_games(
            self.st, [api_game('m-1', _ts('2026-09-10 21:25:18'), win=False)],
            log=lambda *a: None)
        self.assertEqual((n_new, n_have), (0, 1))
        self.assertEqual(self.st.count(), 1)

    def test_import_idempotent(self):
        self.st.init_schema()
        g = api_game('m-1', _ts('2026-09-02 11:30:18'))
        api.import_games(self.st, [g], log=lambda *a: None)
        n_new, n_have, _ = api.import_games(self.st, [g], log=lambda *a: None)
        self.assertEqual((n_new, n_have), (0, 1))
        self.assertEqual(self.st.count(), 1)

    def test_api_meta_enriches_log_rows(self):
        """API 独有的字段（对手名/段位/finish_type）也要补到**日志行**上。"""
        self.st.init_schema()
        self.st.upsert_match(mk('log-1', '2026-09-10 21:25:08.464', 'Lose',
                                mode=MODE_PVP))
        g = api_game('m-1', _ts('2026-09-10 21:25:18'), win=False)
        g['opponent_player_name'] = 'popopo'
        g['friendly_rank_before'] = 5
        g['friendly_rate_before'] = 3
        g['opponent_rank_before'] = 5
        g['opponent_rate_before'] = 3
        g['duels'][0]['finish_type'] = 4
        hit, miss = self.st.sync_api_meta([g])
        self.assertEqual(hit, 1)
        got = self.st.get_match('log-1')
        self.assertEqual(got['opp_player_name'], 'popopo')
        self.assertEqual(got['my_rank_before'], 5)
        self.assertEqual(got['finish_type'], 4)
        # 日志独有的字段不能被抹掉
        self.assertEqual(got['origin'], 'log')

    def test_api_meta_not_wiped_by_log_resync(self):
        """日志重新解析（这些字段为 NULL）不能把 API 回填的值冲掉。"""
        self.st.init_schema()
        g = api_game('m-1', _ts('2026-09-02 11:30:18'))
        api.import_games(self.st, [g], log=lambda *a: None)
        rid = api.api_to_row(g)['match_id']
        self.assertEqual(self.st.get_match(rid)['opp_player_name'], '测试对手')
        # 再 upsert 一次同样的 API 行（模拟重跑补录）
        api.import_games(self.st, [g], log=lambda *a: None)
        self.assertEqual(self.st.get_match(rid)['opp_player_name'], '测试对手')


class TestModeDetection(Base):
    """模式判定：看 `Starting Duel` 之前最后一次进入 DuelClient 的来源界面。

    这里用**真实日志行的格式**（前面带 `[时间戳] [info] [模块]`）——
    曾因为用 `re.match` 而不是 `search`，导致一条都匹配不上。
    """

    def _mode_for(self, entry_view):
        lines = []
        if entry_view:
            lines.append('[2026-09-10 23:13:18.836] [info]  '
                         '[Ygom.ygomCurrentViewWatcher] %s -> '
                         'YgomGame.Duel.DuelClient\r\n' % entry_view)
        lines.append('[2026-09-10 23:13:18.837] [info]  [Ygom.handleScryEvent] '
                     'Starting Duel with deckId=48171157, isDeckKnown=true\r\n')
        segs = am.split_matches(lines)
        self.assertEqual(len(segs), 1)
        return am.parse_seg(segs[0], lines)['mode']

    def test_live(self):
        self.assertEqual(
            self._mode_for('YgomGame.DuelLive.DuelLiveViewController'),
            MODE_LIVE)

    def test_profile_replay_is_live(self):
        """自己资料里的回放也要判成 live —— 2026-09-12 前漏判，会被归档成 unknown 行。"""
        self.assertEqual(
            self._mode_for('YgomGame.Menu.ProfileReplayViewController'),
            MODE_LIVE)

    def test_colosseum_history_replay_is_live(self):
        """从活动「对战记录」点进去看回放 —— 同样不是对局。"""
        self.assertEqual(
            self._mode_for('YgomGame.Colosseum.ColosseumHistoryViewController'),
            MODE_LIVE)

    def test_solo(self):
        self.assertEqual(
            self._mode_for('YgomGame.Solo.SoloStartProductionViewController'),
            MODE_SOLO)

    def test_pvp(self):
        self.assertEqual(
            self._mode_for('YgomGame.Colosseum.DuelStartViewController'),
            MODE_PVP)

    def test_unknown_without_entry(self):
        self.assertEqual(self._mode_for(None), MODE_UNKNOWN)


class TestM7Aggregates(Base):
    """M7 · S0 新增的两个聚合 + 两个 rowid 助手（审查 P1-1 / P1-3）。

    纪律：**口径只能有一份** —— 新增聚合必须与 `get_stats()` 对得上，
    所以这几个测试都拿 `get_stats()` 的结果做交叉验证。
    """

    def _seed(self):
        self.st.init_schema()
        rows = [
            # 同一天两局，两副不同的我方卡组
            mk('m1', '2026-09-10 10:00:00.000', 'Win', True, True, 'd1', '甲',
               mode=MODE_PVP, game_mode=3),
            mk('m2', '2026-09-10 11:00:00.000', 'Lose', False, None, 'd2', '乙',
               mode=MODE_PVP, game_mode=3),
            # 另一天
            mk('m3', '2026-09-11 10:00:00.000', 'Win', True, None, 'd1', '甲',
               mode=MODE_PVP, game_mode=3),
            # 活动局（21）—— 不在主口径里，两个聚合都该排除
            mk('m4', '2026-09-11 12:00:00.000', 'Win', True, None, 'd1', '甲',
               mode=MODE_PVP, game_mode=21),
            # 未结束 —— 不进分母
            mk('m5', '2026-09-11 13:00:00.000', '(未结束)', True, None, 'd1', '甲',
               mode=MODE_SOLO),
        ]
        for r in rows:
            self.st.upsert_match(r)

    # ---------------------------------------------------------- 按天
    def test_get_daily_stats_matches_get_stats(self):
        self._seed()
        daily = self.st.get_daily_stats()
        self.assertEqual(sorted(daily), ['2026-09-10', '2026-09-11'])
        self.assertEqual(daily['2026-09-10'], {'n': 2, 'w': 1, 'l': 1, 'd': 0})
        self.assertEqual(daily['2026-09-11'], {'n': 1, 'w': 1, 'l': 0, 'd': 0})
        # 交叉验证：逐日相加 == 总体（同一口径）
        tot = self.st.get_stats()
        self.assertEqual(sum(d['n'] for d in daily.values()), tot['n_total'])
        self.assertEqual(sum(d['w'] for d in daily.values()), tot['n_win'])
        self.assertEqual(sum(d['l'] for d in daily.values()), tot['n_lose'])

    def test_get_daily_stats_respects_window(self):
        self._seed()
        daily = self.st.get_daily_stats(since='2026-09-11 00:00:00')
        self.assertEqual(sorted(daily), ['2026-09-11'])

    def test_get_daily_stats_mode_group(self):
        """活动局（21）**只在 `event` 分类里**，主口径看不到它。"""
        self._seed()
        main = self.st.get_daily_stats()                    # 主口径
        self.assertEqual(main,
                         {'2026-09-10': {'n': 2, 'w': 1, 'l': 1, 'd': 0},
                          '2026-09-11': {'n': 1, 'w': 1, 'l': 0, 'd': 0}})
        ev = self.st.get_daily_stats(mode_group='event')    # 只算活动
        self.assertEqual(ev, {'2026-09-11': {'n': 1, 'w': 1, 'l': 0, 'd': 0}})
        ranked = self.st.get_daily_stats(mode_group='ranked')
        self.assertEqual(ranked, main)                      # 数据全在天梯

    def test_get_daily_stats_empty_db(self):
        self.st.init_schema()
        self.assertEqual(self.st.get_daily_stats(), {})

    # ------------------------------------------------------ 按我方卡组
    def test_get_stats_by_deck_shape_and_caliber(self):
        self._seed()
        got = self.st.get_stats_by_deck()
        self.assertEqual([n for n, _ in got], ['甲', '乙'])       # 场次降序
        jia = dict(got)['甲']
        self.assertEqual(jia['n_total'], 2)
        self.assertEqual(jia['n_win'], 2)
        self.assertEqual(jia['win_rate'], 1.0)
        self.assertEqual(jia['first_n'], 2)
        self.assertEqual(jia['first_rate'], 1.0)
        self.assertEqual(jia['second_n'], 0)
        self.assertIsNone(jia['second_rate'])                     # 0 场 -> None，不是 0
        self.assertEqual(jia['first_at'], '2026-09-10 10:00:00.000')
        self.assertEqual(jia['last_at'], '2026-09-11 10:00:00.000')
        yi = dict(got)['乙']
        self.assertEqual((yi['n_total'], yi['n_win'], yi['win_rate']), (1, 0, 0.0))

    def test_get_stats_by_deck_same_caliber_as_get_stats(self):
        """**口径不二写**：按卡组分组的加总 == 总体统计。"""
        self._seed()
        by_deck = self.st.get_stats_by_deck()
        tot = self.st.get_stats()
        self.assertEqual(sum(s['n_total'] for _, s in by_deck), tot['n_total'])
        self.assertEqual(sum(s['n_win'] for _, s in by_deck), tot['n_win'])
        self.assertEqual(sum(s['first_n'] for _, s in by_deck), tot['first_n'])
        self.assertEqual(sum(s['second_n'] for _, s in by_deck), tot['second_n'])

    def test_get_stats_by_deck_unidentified_bucket(self):
        """我方卡组为空 -> '(未识别)'（与 get_deck_distribution 同一表达式）。"""
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-10 10:00:00.000', 'Win',
                                deck_id=None, deck_name=None,
                                mode=MODE_PVP, game_mode=3))
        got = self.st.get_stats_by_deck()
        self.assertEqual([n for n, _ in got], ['(未识别)'])

    def test_get_stats_by_deck_empty_db(self):
        self.st.init_schema()
        self.assertEqual(self.st.get_stats_by_deck(), [])

    # ------------------------------------------------- rowid 锚点（P1-3）
    def test_max_rowid_and_unmarked_since(self):
        self._seed()
        anchor = self.st.max_rowid()
        self.assertEqual(len(self.st.unmarked_since(anchor)), 0)   # 没有更新的

        self.st.upsert_match(mk('m9', '2026-09-12 10:00:00.000', 'Win',
                                mode=MODE_PVP, game_mode=3))
        got = self.st.unmarked_since(anchor)
        self.assertEqual([r['match_id'] for r in got], ['m9'])
        self.assertIn('rowid', got[0])
        self.assertGreater(got[0]['rowid'], anchor)

    def test_unmarked_since_same_caliber_as_unmarked(self):
        """与 `get_unmarked_matches` 完全同口径：活动局 / 未结束都不在里面。"""
        self._seed()
        since = self.st.unmarked_since(0)
        plain = self.st.get_unmarked_matches(limit=100)
        self.assertEqual([r['match_id'] for r in since],
                         [r['match_id'] for r in plain])
        self.assertNotIn('m4', [r['match_id'] for r in since])     # 活动局
        self.assertNotIn('m5', [r['match_id'] for r in since])     # 未结束

    def test_unmarked_since_excludes_marked(self):
        self._seed()
        oid = self.st.upsert_opp_deck('烙印')
        self.st.mark_opp_deck('m3', opp_deck_id=oid, name='烙印')
        got = self.st.unmarked_since(0)
        self.assertNotIn('m3', [r['match_id'] for r in got])

    def test_max_rowid_empty_db(self):
        self.st.init_schema()
        self.assertEqual(self.st.max_rowid(), 0)


class TestQuery(Base):
    def test_get_unmarked_matches(self):
        self.st.init_schema()
        for i, mid in enumerate(('m1', 'm2', 'm3')):
            self.st.upsert_match(mk(mid, '2026-09-10 1%d:00:00.000' % i,
                                    mode=MODE_PVP, game_mode=3))
        oid = self.st.upsert_opp_deck('烙印')
        self.st.mark_opp_deck('m2', opp_deck_id=oid, name='烙印')
        got = self.st.get_unmarked_matches()
        self.assertEqual([r['match_id'] for r in got], ['m3', 'm1'])
        one = self.st.get_unmarked_matches(limit=1)
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]['match_id'], 'm3')

    def test_get_today_stats_basic(self):
        """4 局合成数据：SQL 聚合 == 纯 Python 复算；(未结束) 不进分母。"""
        self.st.init_schema()
        day = '2026-09-10'
        rows = [
            mk('m1', day + ' 10:00:00.000', 'Win', True, True, 'd1', '甲',
               mode=MODE_PVP, game_mode=3),
            mk('m2', day + ' 11:00:00.000', 'Win', False, False, 'd1', '甲',
               mode=MODE_PVP, game_mode=3),
            mk('m3', day + ' 12:00:00.000', 'Lose', False, None, 'd2', '乙',
               mode=MODE_PVP, game_mode=3),
            mk('m4', day + ' 13:00:00.000', '(未结束)', True, None, 'd1', '甲',
               mode=MODE_SOLO),
        ]
        for r in rows:
            self.st.upsert_match(r)
        st = self.st.get_today_stats(day)
        self.assertEqual(st['date'], day)
        self.assertEqual(st['n_total'], 3)          # m4 未结束，不算
        self.assertEqual(st['n_win'], 2)
        self.assertEqual(st['n_lose'], 1)
        self.assertEqual(st['first_n'], 1)
        self.assertEqual(st['second_n'], 2)
        self.assertAlmostEqual(st['win_rate'], 2 / 3)
        self.assertAlmostEqual(st['first_hand_pct'], 1 / 3)
        self.assertEqual(st['initiative_n'], 2)
        self.assertAlmostEqual(st['initiative_rate'], 0.5)
        self.assertEqual(st['unmarked'], 3)
        for k, v in py_recompute(rows).items():
            self.assertAlmostEqual(st[k], v, msg='字段 %s 不一致' % k)
        self.assertEqual(self.st.get_today_stats('2026-01-01')['n_total'], 0)

    def test_get_deck_distribution_tie_order(self):
        """并列场次的顺序按"首次出现时间"——否则 --stats 输出会变。"""
        self.st.init_schema()
        rows = [
            mk('m1', '2026-09-10 10:00:00.000', deck_id='d1', deck_name='甲',
               mode=MODE_PVP, game_mode=3),
            mk('m2', '2026-09-10 10:05:00.000', deck_id='d1', deck_name='甲',
               mode=MODE_PVP, game_mode=3),
            mk('m3', '2026-09-10 11:00:00.000', deck_id='d3', deck_name='丙',
               mode=MODE_PVP, game_mode=3),
            mk('m4', '2026-09-10 11:30:00.000', deck_id='d2', deck_name='乙',
               mode=MODE_PVP, game_mode=3),
            mk('m5', '2026-09-10 12:00:00.000', '(未结束)',
               deck_id=None, deck_name=None, mode=MODE_SOLO),
        ]
        for r in rows:
            self.st.upsert_match(r)
        self.assertEqual(self.st.get_deck_distribution(),
                         [('甲', 2), ('丙', 1), ('乙', 1)])
        done = [r for r in rows if r['result'] in DONE]
        ref = Counter(r.get('my_deck_name') or '(未识别)' for r in done)
        self.assertEqual(self.st.get_deck_distribution(), ref.most_common())


class TestMetaNameJa(Base):
    """卡组日文名（`meta_decks.name_ja`）：能写、重抓不冲掉、日语能搜到。"""

    def _seed(self):
        self.st.init_schema()
        self.st.replace_meta_decks([dict(
            name='Branded', kind='deck_type', name_cn='烙印', name_ja='烙印',
            aliases=[], tier='Tier2')])

    def test_override_and_search(self):
        self._seed()
        self.assertEqual(self.st.get_meta_deck('Branded')['name_ja'], '烙印')
        self.st.set_meta_deck_override('Branded', name_ja='ブランド')
        self.assertEqual(self.st.get_meta_deck('Branded')['name_ja'], 'ブランド')
        self.assertEqual([h['name'] for h in self.st.search_meta_decks('ブランド')],
                         ['Branded'], '日文名也要能搜到')

    def test_rescrape_keeps_manual_ja(self):
        """重抓不传 name_ja（= None）不该冲掉手改值 —— 跟 name_cn 一样的 COALESCE 规矩。"""
        self._seed()
        self.st.replace_meta_decks([dict(name='Branded', kind='deck_type',
                                         tier='Tier1')])
        self.assertEqual(self.st.get_meta_deck('Branded')['name_ja'], '烙印')
        self.assertEqual(self.st.get_meta_deck('Branded')['tier'], 'Tier1')

    def test_rescrape_can_clear_ja(self):
        """脚本要清空就传空串（非 None）。"""
        self._seed()
        self.st.replace_meta_decks([dict(name='Branded', kind='deck_type',
                                         name_ja='')])
        self.assertEqual(self.st.get_meta_deck('Branded')['name_ja'], '')


class TestRepairMatchResult(Base):
    """事故修复口（2026-09-16 EM 误配）：显式覆盖 + 置 NULL。"""

    def test_overwrite_and_clear(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m1', '2026-09-16 11:53:28.000',
                                result='Lose'))
        self.st.apply_record_meta('m1', winbits=[0, 1],
                                 result_note='LP已削减至0')
        n = self.st.repair_match_result(
            'm1', result='Win', result_src='duel_code',
            result_note='对手已投降', winbits=None)
        self.assertEqual(n, 1)
        row = self.st.get_match('m1')
        self.assertEqual(row['result'], 'Win')
        self.assertEqual(row['result_src'], 'duel_code')
        self.assertEqual(row['result_note'], '对手已投降')
        self.assertIsNone(row['winbits'])

    def test_untouched_columns_kept(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m2', '2026-09-16 11:53:28.000',
                                result='Lose'))
        self.st.repair_match_result('m2', winbits=None)
        row = self.st.get_match('m2')
        self.assertEqual(row['result'], 'Lose')      # 没传 = 不动
        self.assertIsNone(row['winbits'])

    def test_bad_result_rejected(self):
        self.st.init_schema()
        self.st.upsert_match(mk('m3', '2026-09-16 11:53:28.000'))
        with self.assertRaises(AssertionError):
            self.st.repair_match_result('m3', result='nope')


class TestOppDecks(Base):
    """M5：对手卡组标注（绑定 / 撤销 / 队列 / 交叉战绩）。"""

    def _seed(self):
        """3 场天梯（2 场排名 + 1 场巅峰）+ 1 场活动赛（不该进统计）。"""
        self.st.init_schema()
        rows = [
            mk('m1', '2026-09-10 10:00:00.000', 'Win', deck_name='闪刀姬',
               mode=MODE_PVP, game_mode=3),
            mk('m2', '2026-09-10 11:00:00.000', 'Lose', deck_name='闪刀姬',
               mode=MODE_PVP, game_mode=3),
            mk('m3', '2026-09-10 12:00:00.000', 'Lose', deck_name='K9vs',
               mode=MODE_PVP, game_mode=19),
            mk('m4', '2026-09-10 13:00:00.000', 'Win', deck_name='闪刀姬',
               mode=MODE_PVP, game_mode=99),
        ]
        for r in rows:
            self.st.upsert_match(r)

    def test_bind_and_clear_roundtrip(self):
        """标注 -> 已标注队列 -> 撤销，三态都要对。"""
        self._seed()
        self.assertEqual(len(self.st.get_unmarked_matches(100)), 3)
        oid = self.st.upsert_opp_deck('烙印', aliases=['Branded'])
        self.st.mark_opp_deck('m1', opp_deck_id=oid, name='烙印',
                              note='先手两回合被展开')
        got = self.st.get_match('m1')
        self.assertEqual(got['opp_deck_id'], oid)
        self.assertEqual(got['opp_deck_name'], '烙印')
        self.assertEqual(got['opp_deck_note'], '先手两回合被展开')
        self.assertNotIn('m1', [r['match_id'] for r in self.st.get_unmarked_matches(100)])
        self.assertEqual([r['match_id'] for r in self.st.get_marked_matches()], ['m1'])

        self.assertTrue(self.st.clear_opp_deck('m1'))
        got = self.st.get_match('m1')
        self.assertIsNone(got['opp_deck_id'])
        self.assertIsNone(got['opp_deck_name'])
        self.assertIsNone(got['opp_deck_note'])
        self.assertEqual(self.st.get_marked_matches(), [])
        self.assertFalse(self.st.clear_opp_deck('不存在的 id'))

    def test_list_opp_decks_counts_live(self):
        """「打过几局」现算，且只算统计口径内的；没遇到过的排最后。"""
        self._seed()
        a = self.st.upsert_opp_deck('烙印', aliases=['Branded'])
        b = self.st.upsert_opp_deck('自研卡组')
        self.st.mark_opp_deck('m1', opp_deck_id=a, name='烙印')
        self.st.mark_opp_deck('m2', opp_deck_id=a, name='烙印')
        self.st.mark_opp_deck('m4', opp_deck_id=b, name='自研卡组')  # 活动赛，不计
        self.assertEqual(self.st.list_opp_decks(),
                         [(a, '烙印', ['Branded'], 2), (b, '自研卡组', None, 0)])
        self.assertEqual(self.st.list_opp_decks(limit=1),
                         [(a, '烙印', ['Branded'], 2)])

    def test_get_cross_stats_groups_and_filters(self):
        """交叉战绩：未标注单列一格；活动赛不进；掉线局判负且**计入**。"""
        self._seed()
        a = self.st.upsert_opp_deck('烙印')
        b = self.st.upsert_opp_deck('泪冠哀歌')
        self.st.mark_opp_deck('m1', opp_deck_id=a, name='烙印')
        self.st.mark_opp_deck('m3', opp_deck_id=b, name='泪冠哀歌')
        # 掉线/中途退出：mode=pvp 且没有结束标记 -> upsert_match 直接判负，
        # ended_at 留 NULL。这一局**要进统计**（用户口径），所以算 2 场未标注。
        self.st.upsert_match(mk('m5', '2026-09-10 14:00:00.000', '(未结束)',
                                deck_name='闪刀姬', mode=MODE_PVP))
        self.assertEqual(self.st.get_match('m5')['result'], 'Lose')
        self.assertIsNone(self.st.get_match('m5')['ended_at'])
        # 闪刀姬 未标注 = m2（真输）+ m5（掉线判负），共 2 场
        self.assertEqual(self.st.get_cross_stats(), [
            ('闪刀姬', '(未标注)', 2, 0, 2, 0),
            ('K9vs', '泪冠哀歌', 1, 0, 1, 0),
            ('闪刀姬', '烙印', 1, 1, 0, 0),
        ])
        # since 按开始时间截断：m1(10:00)/m2(11:00) 排除，剩 m3 + m5
        self.assertEqual(self.st.get_cross_stats(since='2026-09-10 11:30:00.000'), [
            ('K9vs', '泪冠哀歌', 1, 0, 1, 0),
            ('闪刀姬', '(未标注)', 1, 0, 1, 0),
        ])


class TestCrossStatsWentFirst(Base):
    """D2：`get_cross_stats` 的 ``went_first`` 三页签（None=综合 / True=先攻 / False=后攻）。

    ⚠️ `went_first` 为 NULL 的老局**只进综合**、两个页签都不进（D2 计划 §3.2）。
    """

    def _seed(self):
        """3 场天梯（先攻胜/先攻负/后攻胜）+ 1 场 went_first=NULL 的老局，对手同一卡组。"""
        self.st.init_schema()
        rows = [
            mk('w1', '2026-09-10 10:00:00.000', 'Win', went_first=True,
               deck_name='闪刀姬', mode=MODE_PVP, game_mode=3),
            mk('w2', '2026-09-10 11:00:00.000', 'Lose', went_first=True,
               deck_name='闪刀姬', mode=MODE_PVP, game_mode=3),
            mk('w3', '2026-09-10 12:00:00.000', 'Win', went_first=False,
               deck_name='闪刀姬', mode=MODE_PVP, game_mode=3),
            mk('w4', '2026-09-10 13:00:00.000', 'Win', went_first=None,
               deck_name='闪刀姬', mode=MODE_PVP, game_mode=3),
        ]
        for r in rows:
            self.st.upsert_match(r)
        oid = self.st.upsert_opp_deck('烙印')
        for mid in ('w1', 'w2', 'w3', 'w4'):
            self.st.mark_opp_deck(mid, opp_deck_id=oid, name='烙印')

    def test_none_keeps_everything(self):
        """综合（None）= 不过滤，NULL 老局也在。"""
        self._seed()
        self.assertEqual(self.st.get_cross_stats(),
                         [('闪刀姬', '烙印', 4, 3, 1, 0)])

    def test_first_and_second_split(self):
        """先攻只算 went_first=1、后攻只算 went_first=0。"""
        self._seed()
        self.assertEqual(self.st.get_cross_stats(went_first=True),
                         [('闪刀姬', '烙印', 2, 1, 1, 0)])
        self.assertEqual(self.st.get_cross_stats(went_first=False),
                         [('闪刀姬', '烙印', 1, 1, 0, 0)])

    def test_mode_group_filter(self):
        """v5 模式筛选：ranked/dc/all 分别只算各自口径（用户拍板推翻旧裁定）。"""
        self._seed()
        # 加一场 DC 杯（game_mode=12），同对手同我方卡组
        self.st.upsert_match(mk('w5', '2026-09-10 14:00:00.000', 'Lose',
                                deck_name='闪刀姬', mode=MODE_PVP,
                                game_mode=12))
        oid = self.st.upsert_opp_deck('烙印')
        self.st.mark_opp_deck('w5', opp_deck_id=oid, name='烙印')
        self.assertEqual(self.st.get_cross_stats(mode_group='ranked')[0][2], 4)
        self.assertEqual(self.st.get_cross_stats(mode_group='dc')[0][2], 1)
        self.assertEqual(self.st.get_cross_stats(mode_group='all')[0][2], 5)
        self.assertEqual(self.st.get_cross_stats()[0][2], 4)   # 默认 = 主口径不变

    def test_null_went_first_only_in_overall(self):
        """NULL（2 先 + 1 后 + 1 NULL = 4）只进综合：页签合计 3 ≠ 综合 4。"""
        self._seed()
        self.assertEqual(self.st.get_cross_stats(went_first=True)[0][2], 2)
        self.assertEqual(self.st.get_cross_stats(went_first=False)[0][2], 1)
        self.assertEqual(self.st.get_cross_stats()[0][2], 4)


class TestMigrate(Base):
    def _write_jsonl(self, rows):
        p = os.path.join(self.d, 'src.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        return p

    def test_migrate_jsonl_roundtrip(self):
        """真实 20 局：迁移后逐字段全等，统计口径与 v1 一致。"""
        if not os.path.exists(REAL_JSONL):
            self.skipTest('找不到 %s' % REAL_JSONL)
        rows = mig.load_jsonl(REAL_JSONL)
        info = mig.migrate(REAL_JSONL, self.st, decks_path=None,
                           log=lambda *a: None)
        self.assertEqual(info['n_rows'], len(rows))
        self.assertEqual(self.st.count(), len(rows))
        for r in rows:
            got = self.st.get_match(r['match_id'])
            self.assertIsNotNone(got, '库里缺 %s' % r['match_id'])
            for k, v in r.items():
                self.assertEqual(got.get(k), v, '%s.%s' % (r['match_id'], k))
        n_diff, checked, _ = mig.verify(REAL_JSONL, self.st, log=lambda *a: None)
        self.assertEqual(n_diff, 0)
        self.assertEqual(checked, len(rows) * len(rows[0]))
        # 幂等：再迁移一次，条数不变
        mig.migrate(REAL_JSONL, self.st, decks_path=None, log=lambda *a: None)
        self.assertEqual(self.st.count(), len(rows))
        # 统计与纯 Python 复算一致（对齐 --stats）
        # 先按真实流程回填 game_mode（API 里这 20 局全是排名决斗）
        for r in rows:
            self.st.set_game_mode(r['match_id'], 3)
        st = self.st.get_stats()
        for k, v in py_recompute(rows).items():
            self.assertAlmostEqual(st[k], v, msg='字段 %s 不一致' % k)
        self.assertEqual(st['n_total'], 16)
        self.assertEqual(st['n_win'], 12)
        self.assertEqual(st['n_lose'], 4)
        self.assertEqual(st['unmarked'], 16)

    def test_upsert_match_preserves_jsonl_manual(self):
        """jsonl 带人工标注 -> 迁移 -> 再 upsert（人工字段 NULL），标注仍在。"""
        rows = [
            mk('m1', '2026-09-10 10:00:00.000', opp_deck_id=1,
               opp_deck_name='烙印'),
            mk('m2', '2026-09-10 11:00:00.000'),
        ]
        src = self._write_jsonl(rows)
        mig.migrate(src, self.st, decks_path=None, log=lambda *a: None)
        self.assertEqual(self.st.get_match('m1')['opp_deck_name'], '烙印')
        mig.migrate(src, self.st, decks_path=None, log=lambda *a: None)
        got = self.st.get_match('m1')
        self.assertEqual(got['opp_deck_id'], 1)
        self.assertEqual(got['opp_deck_name'], '烙印')
        self.assertEqual(self.st.count(), 2)
        n_diff, _, _ = mig.verify(src, self.st, log=lambda *a: None)
        self.assertEqual(n_diff, 0)


class TestWarnUnsynced(Base):
    """`archive_matches.warn_unsynced` 的判据必须和 upsert 的身份口径一致。

    踩过的坑：upsert 用 started_at 兜住 match_id 漂移，但提示逻辑只看 match_id，
    结果 --stats 会**永远**报「1 局尚未入库」。
    """

    def _run(self, rows):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            am.warn_unsynced(self.st, rows)
        return buf.getvalue()

    def test_no_false_alarm_on_match_id_drift(self):
        self.st.init_schema()
        ts = '2026-09-07 09:47:17.650'
        self.st.upsert_match(mk('id-a', ts, deck_id='48171157', deck_name='闪刀姬'))
        drifted = mk('id-b', ts, '(未结束)', deck_id=None, deck_name=None)
        self.assertEqual(self._run([drifted]), '')      # 漂移不算"未入库"

    def test_alarms_on_genuinely_new_match(self):
        self.st.init_schema()
        self.st.upsert_match(mk('id-a', '2026-09-07 09:47:17.650'))
        fresh = mk('id-c', '2026-09-10 10:00:00.000')
        self.assertIn('尚未入库', self._run([fresh]))

    def test_no_alarm_for_live_rows(self):
        """观战/回放本来就不入库，不能被报成"未同步"。"""
        self.st.init_schema()
        self.st.upsert_match(mk('id-a', '2026-09-10 10:00:00.000'))
        live = mk('live1', '2026-09-07 09:47:17.650', '(未结束)', mode=MODE_LIVE)
        self.assertEqual(self._run([live]), '')


if __name__ == '__main__':
    unittest.main()


class TestDeckNameCn(unittest.TestCase):
    """卡组覆盖表（CSV）的读取 —— 这是**给人手改的入口**，容错必须好。

    6 列：站内英文名 / 显示英文名 / 中文名 / 分组 / 隐藏 / 备注。
    Excel / WPS 存 CSV 可能带 BOM、也可能存成 GBK，两种都要能读。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='cntest_')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, text, enc='utf-8-sig'):
        p = os.path.join(self.d, 'cn.csv')
        with open(p, 'wb') as f:
            f.write(text.encode(enc))
        return p

    BODY = ('站内英文名,显示英文名,中文名,别名,分组,隐藏,备注\n'
            'Dracotail,,星辰,,Tier 1,,\n'
            'Yummy Engine,Mitsurugi Yummy,巳剑黯蜜,,Tier 3,,混轴改名 待核\n'
            'Elfnote Engine,,,,,Y,engine 分类取消\n'
            'Fur Hire,,空牙团,,Trending,,\n'
            'Branded Elfnote,,烙印耀圣,,Tier 2,,自定义\n'
            'Pumpking Zombies,,南瓜王,Zombies / 不死均,Trending,,\n')

    def test_reads_utf8_bom(self):
        m, todo = fmd.load_overrides(self._write(self.BODY, 'utf-8-sig'))
        self.assertEqual(m['Dracotail']['cn'], '星辰')
        self.assertEqual(m['Dracotail']['tier'], 'Tier1')
        self.assertEqual(m['Yummy Engine']['display'], 'Mitsurugi Yummy')
        self.assertEqual(m['Yummy Engine']['cn'], '巳剑黯蜜')
        self.assertEqual(todo, ['Yummy Engine'], '备注含「待核」才进名单')
        self.assertEqual(m['Pumpking Zombies']['aliases'], ['Zombies', '不死均'])

    def test_reads_gbk(self):
        """Excel 在中文 Windows 上常把 CSV 存成 GBK。"""
        m, _ = fmd.load_overrides(self._write(self.BODY, 'gb18030'))
        self.assertEqual(m['Dracotail']['cn'], '星辰')
        self.assertEqual(m['Yummy Engine']['display'], 'Mitsurugi Yummy')

    def test_reads_new_jp_column(self):
        """新版 8 列（多了「日文名」）—— 列在中文名后面。"""
        m, _ = fmd.load_overrides(self._write(
            '站内英文名,显示英文名,中文名,日文名,别名,分组,隐藏,备注\n'
            'Branded,,烙印,烙印,BrandedDracotail,Tier2,,\n'))
        self.assertEqual(m['Branded']['jp'], '烙印')
        self.assertEqual(m['Branded']['aliases'], ['BrandedDracotail'])
        self.assertEqual(m['Branded']['tier'], 'Tier2')
        self.assertEqual(m['Branded']['cn'], '烙印')

    def test_old_7_column_header_still_reads(self):
        """★ 老文件（7 列、没有「日文名」）靠**表头认列**，别名/分组/隐藏不会串位。"""
        m, _ = fmd.load_overrides(self._write(
            '站内英文名,显示英文名,中文名,别名,分组,隐藏,备注\n'
            'Zombies,,不死均,Zombies / 不死均,Trending,Y,备注\n'))
        self.assertEqual(m['Zombies']['aliases'], ['Zombies', '不死均'])
        self.assertEqual(m['Zombies']['tier'], 'Trending')
        self.assertTrue(m['Zombies']['hidden'])
        self.assertEqual(m['Zombies']['jp'], '')

    def test_no_header_falls_back_to_legacy_order(self):
        """表头被删掉了才退回老顺序（7 列）。"""
        m, _ = fmd.load_overrides(self._write(
            'Zombies,,不死均,Zombies / 不死均,Trending,Y,备注\n'))
        self.assertEqual(m['Zombies']['cn'], '不死均')
        self.assertEqual(m['Zombies']['aliases'], ['Zombies', '不死均'])
        self.assertTrue(m['Zombies']['hidden'])

    def test_parses_hidden_flag(self):
        m, _ = fmd.load_overrides(self._write(self.BODY))
        self.assertTrue(m['Elfnote Engine']['hidden'])
        self.assertFalse(m['Dracotail']['hidden'])

    def test_blank_cn_stays_blank(self):
        """在表里但中文名留空 = 明确留空（用于「删掉一个填错的名字」）。"""
        m, _ = fmd.load_overrides(self._write(self.BODY))
        self.assertEqual(m['Elfnote Engine']['cn'], '')

    def test_norm_tier(self):
        self.assertEqual(fmd._norm_tier('Tier 2'), 'Tier2')
        self.assertEqual(fmd._norm_tier('Tier2'), 'Tier2')
        self.assertEqual(fmd._norm_tier('Trending'), 'Trending')
        self.assertEqual(fmd._norm_tier('Tier 4'), 'Trending', 'Tier4 等同于 Trending')
        self.assertIsNone(fmd._norm_tier(''))

    def test_skips_header_and_blank_lines(self):
        m, todo = fmd.load_overrides(
            self._write('站内英文名,显示英文名,中文名,分组,隐藏,备注\n\n'
                        'Dracotail,,星辰,Tier 1,,\n\n'))
        self.assertEqual(list(m), ['Dracotail'])
        self.assertEqual(todo, [])

    def test_tolerates_extra_and_missing_columns(self):
        m, _ = fmd.load_overrides(self._write('Dracotail,,星辰\n'))
        self.assertEqual(m['Dracotail']['cn'], '星辰')
        self.assertEqual(m['Dracotail']['display'], '')
        self.assertFalse(m['Dracotail']['hidden'])
        m2, _ = fmd.load_overrides(
            self._write('Dracotail,,星辰,,Tier 1,,备注,多出来的一列\n'))
        self.assertEqual(m2['Dracotail']['cn'], '星辰')

    def test_missing_file_returns_empty(self):
        m, todo = fmd.load_overrides(os.path.join(self.d, '不存在.csv'))
        self.assertEqual(m, {})
        self.assertEqual(todo, [])

    def test_apply_overrides_leaves_unlisted_untouched(self):
        """不在表里的条目不贴任何键 —— 库里保留旧值（COALESCE 语义）。"""
        rows = [{'name': 'Dracotail'}, {'name': '从未见过的卡组'}]
        unknown = fmd.apply_overrides(rows, {'Dracotail': dict(
            cn='星辰', display='', tier='Tier1', hidden=False, note='')})
        self.assertEqual(rows[0]['name_cn'], '星辰')
        self.assertNotIn('name_cn', rows[1])
        self.assertEqual(unknown, ['从未见过的卡组'])

    def test_apply_overrides_fills_missing_tier_only(self):
        """分组分工：站内有分组 → 以站内为准；站内没有 → 用覆盖表兜底。"""
        ovr = {'A': dict(cn='甲', display='', tier='其它', hidden=False, note=''),
               'B': dict(cn='乙', display='', tier='其它', hidden=False, note='')}
        rows = [{'name': 'A', 'tier': 'Tier2'},      # 站内有分组
                {'name': 'B', 'tier': None}]         # 站内没分组（如仅在使用率榜）
        fmd.apply_overrides(rows, ovr)
        self.assertEqual(rows[0]['tier'], 'Tier2', '站内分组不该被覆盖表改掉')
        self.assertEqual(rows[1]['tier'], '其它', '站内没分组时才兜底')

    def test_custom_rows_built_for_names_not_on_site(self):
        """★ 覆盖表里、站内抓取里没有的名字 = 自定义卡组。"""
        ovr = {
            'Dracotail': dict(cn='星辰', display='', tier='Tier1',
                              hidden=False, note=''),
            'Branded Elfnote': dict(cn='烙印耀圣', display='', tier='Tier2',
                                    hidden=False, note='自定义'),
            '某个隐藏的自定义': dict(cn='X', display='', tier=None,
                                     hidden=True, note=''),
        }
        got = fmd.custom_rows(ovr, {'Dracotail'})      # 站内只有 Dracotail
        self.assertEqual([r['name'] for r in got], ['Branded Elfnote'])
        self.assertEqual(got[0]['name_cn'], '烙印耀圣')
        self.assertEqual(got[0]['tier'], 'Tier2')
        self.assertEqual(got[0]['source'], 'custom')
        self.assertEqual(got[0]['kind'], 'deck_type')

    def test_local_image_prefers_custom_dir(self):
        """自定义目录优先 —— 那是不可再生的图，得盖过站内下载下来的同名图。

        ⚠️ 实现的**真身已搬到 `core/deck_images`**（口径一份），所以常量要 patch 那边；
        但下面仍然调 `fmd.local_image_for` —— 顺带守住「tools 侧委托」这条链没断。
        """
        from core import deck_images as di
        cus = tempfile.mkdtemp(prefix='cusimg_')
        std = tempfile.mkdtemp(prefix='stdimg_')
        self.addCleanup(shutil.rmtree, cus, ignore_errors=True)
        self.addCleanup(shutil.rmtree, std, ignore_errors=True)

        # tools 侧的常量也一起换掉，免得别的路径读到真实目录
        di.CUSTOM_IMG_DIR, old_cus = cus, di.CUSTOM_IMG_DIR
        di.IMG_DIR, old_std = std, di.IMG_DIR
        old_def = di.DEFAULT_IMAGE
        di.DEFAULT_IMAGE = os.path.join(cus, '_default.webp')
        self.addCleanup(setattr, di, 'IMG_CUSTOM_DIR', old_cus)
        self.addCleanup(setattr, di, 'IMG_DIR', old_std)
        self.addCleanup(setattr, di, 'DEFAULT_IMAGE', old_def)

        with open(os.path.join(std, 'X.webp'), 'wb') as f:
            f.write(b'RIFF____WEBP')
        self.assertTrue(fmd.local_image_for('X').endswith('X.webp'))
        with open(os.path.join(cus, 'X.webp'), 'wb') as f:
            f.write(b'RIFF____WEBP')
        self.assertIn('cusimg_', fmd.local_image_for('X'), '应优先自定义目录')
        # 查不到图 → 回退默认卡图；连默认图都没有才返回 None
        self.assertIsNone(fmd.local_image_for('查不到'))
        with open(os.path.join(cus, '_default.webp'), 'wb') as f:
            f.write(b'RIFF____WEBP')
        self.assertIn('_default.webp', fmd.local_image_for('查不到'))

    def test_display_name_priority(self):
        self.assertEqual(fmd.display_name(
            {'name': 'X', 'name_cn': '甲', 'name_display': 'Y'}), '甲')
        self.assertEqual(fmd.display_name(
            {'name': 'X', 'name_cn': '', 'name_display': 'Y'}), 'Y')
        self.assertEqual(fmd.display_name(
            {'name': 'X', 'name_cn': None, 'name_display': None}), 'X')


class TestBackfillPendingRankTerm(unittest.TestCase):
    """`backfill_pending` 第三项：天梯/巅峰行「还没有段位」也算欠账。

    实测（2026-09-19）：只补段位的回填（那行 result 还是 NULL）不改变任何
    探针键 -> 界面永远不刷，用户看到的一直是补之前的样子。
    """

    def _mk(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        st = Storage(os.path.join(tmp, 'a.db'))
        st.init_schema()
        return st

    def test_ranked_row_without_rank_counts(self):
        st = self._mk()
        st.upsert_match({'match_id': 'm1', 'started_at': '2026-09-19 00:10:22',
                         'origin': 'log', 'mode': 'pvp', 'game_mode': 3,
                         'result': None})
        base = st.backfill_pending()
        # 只补段位（result 依旧 NULL）：欠账必须变化，界面才有得刷
        with st._open() as conn:
            conn.execute("UPDATE matches SET my_rank_after=5, my_rate_after=1"
                         " WHERE match_id='m1'")
        self.assertNotEqual(st.backfill_pending(), base)

    def test_event_rows_do_not_count(self):
        """活动局（game_mode 21）永远没有段位 —— 不许把它算成常驻欠账抖动。"""
        st = self._mk()
        st.upsert_match({'match_id': 'e1', 'started_at': '2026-09-19 00:10:22',
                         'origin': 'log', 'mode': 'pvp', 'game_mode': 21,
                         'result': 'Win'})
        with st._open() as conn:
            conn.execute("UPDATE matches SET my_rank_after=5, my_rate_after=1"
                         " WHERE match_id='e1'")
        before = st.backfill_pending()
        with st._open() as conn:
            conn.execute("UPDATE matches SET my_rank_after=NULL WHERE match_id='e1'")
        self.assertEqual(st.backfill_pending(), before,
                         '活动局的段位不该进欠账')
