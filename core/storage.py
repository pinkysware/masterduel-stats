# -*- coding: utf-8 -*-
"""M2 存储层 —— SQLite 封装（本项目对局数据的唯一真源）。

★ 唯一真源声明（M2 决策，见 docs/M2计划.md §1.2）
=================================================
从 M2 起，**`data/masterduel.db` 是本项目对局数据的唯一真源**：

    data/archive/matches.jsonl          v1 时代的存档格式，已退出代码路径
    data/archive/matches.jsonl.v1.bak   上述文件的备份，仅供人工查阅

M3 解析引擎 / M5 对手卡组管理 / M6 应用服务 / M7 UI 都必须经由本模块读写，
不要再直接操作 jsonl。

设计要点
========
* **match_id 用 TEXT**（sha1 前 16 位）：跨运行稳定，重跑迁移或重复解析同一局
  只会 upsert 同一行，不会产生新行。
* **upsert 语义**：日志派生字段全量覆盖；人工字段
  （``opp_deck_id`` / ``opp_deck_name`` / ``opp_deck_note``）**仅在新值为 NULL 时
  保留旧值** —— 即"重解析不丢人工标注"。与改写前的
  ``archive_matches.merge_archive`` 行为一致。
* **布尔字段**：``won_initiative`` / ``went_first`` 库里存 0/1/NULL，读出来还原成
  True/False/None，保持与 jsonl 时代相同的数据形状。
* **JSON 字段**：``opp_revealed`` / ``opp_revealed_ids`` / ``opp_decks.aliases``
  在库里是 TEXT，读写自动 ``json.dumps`` / ``json.loads``（``ensure_ascii=False``，
  中文卡名不转义）。
* **外键**：开启 ``PRAGMA foreign_keys``。upsert 一条对局时，若它引用的
  ``my_decks`` / ``opp_decks`` 行还不存在，会自动补一行占位（``source='log'``），
  这样"先写对局还是先写卡组"就不会成为坑。

⚠️ 两个字段别混算（沿用 v1 语义）
=================================
    won_initiative : 赢得硬币、获得先后手**选择权**（日志稀疏，常为 NULL）
    went_first     : 实际**先攻**（由 `Turn 1 for player N` 判定，覆盖 100%）

硬币 ≠ 先后手，详见 docs/交接说明.md §3.1。
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from core import paths

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 数据库（**可写**）—— 安装版落在 `%LOCALAPPDATA%\MasterDuelStats`（见 core/paths）
DEFAULT_DB = paths.data_path('masterduel.db')

#: 计入胜率分母的结果（`(未结束)` 不算，与 v1 的 show_stats 一致）
DONE_RESULTS = ('Win', 'Lose', 'Draw')

#: repair_match_result 的「这一列不动」哨兵（None 是有意义的输入 = 置 NULL）
_KEEP = object()

#: `result_src` 的**人工**取值 —— 带这个戳的行，`result` 不许被任何自动写入覆盖。
#: 2026-09-18 深夜实测事故：手动补填的胜负，被下一次
#: `tools/import_duel_records.py --apply` 冲回自动解析值（那条链路走
#: :meth:`Storage.apply_record_meta` / :meth:`Storage.upsert_match` 两条路）。
#: 口径（用户 2026-09-18 定）：**人工修复的数据优先于自动解析**。
MANUAL_RESULT_SRC = 'manual'

#: 重解析 upsert 时视为「自动来源」的 src 集合。旧行值非空、且来源**非空且
#: 不在**集合里（manual / manual-fix-* / untapped-api-* …人之一侧）时保留旧值。
#: ⚠️ NULL-src 旧值**不在**保护圈 —— result 是日志派生字段，老契约就是
#: 「重解析按新值覆盖」（winbit 真值优先，见 _build_insert_sql 的注释）。
#: 🔴 2026-10-01 事故：设置里「刷新数据」（`import_duel_records.py --apply`
#: 重跑全量 record 目录）把 API 补录的 `result`（src=untapped-api-…）与用户
#: 行级修正的 `my_deck_name` 全冲回 NULL —— 卡组兜底的新鲜度闸门是**时间相关**
#: 的（当天入库有 current-deck 兜底名、隔天重解析就拒绝 -> NULL），
#: 全量覆盖正好把旧值抹掉。名字/胜负一旦有值，重解析只在「旧值也是自动来源
#: 且新值非空」时刷新；改值一律走行级修正 / `repair_match_result`。
AUTO_RESULT_SRCS = ('winbit', 'duel_code', 'text')
#: ⚠️ 'text'（结算文案判据，2026-10-03 投票仲裁引入）也算自动来源 ——
#:   否则文案判的结果会被当成人工值锁死，后续 winbit 覆盖不进来。
AUTO_MY_DECK_NAME_SRCS = ('snapshot-name', 'm5-guess', 'current-deck')

#: 对局模式。**日志里的 `Starting Duel` 不只代表天梯对局**，判据见
#: `archive_matches.split_matches`（看进入 DuelClient 之前的那次界面跳转）。
MODE_PVP = 'pvp'          # 真对局（天梯 / 活动赛）
MODE_SOLO = 'solo'        # 单人模式 Solo（打 NPC，不是天梯）
MODE_LIVE = 'live'        # 观战 / 回放（Duel Live）—— **根本不是对局**
MODE_UNKNOWN = 'unknown'  # 判定不出来（日志已轮转）

#: 计入统计的模式。`solo` 是打 NPC，`live` 不是对局，都不该进天梯胜率。
COUNTED_MODES = (MODE_PVP, MODE_UNKNOWN)

#: 这一行是从哪来的：`'log'` = 日志解析（默认），`'api'` = 从 Untapped API 补录。
ORIGIN_LOG = 'log'
ORIGIN_API = 'api'
#: M3：自有对局记录（内存采集）—— match_id = 'rec:' + record 目录名。
#: ⚠️ 只在与既有 log/api 行配不上时才新建行；能配上就并入旧行（防战绩翻倍）。
ORIGIN_RECORD = 'record'

#: Untapped API 的 `game_mode` 枚举（从网页 JS 里抠出来的完整定义）。
GAME_MODE_NAMES = {
    0: 'NORMAL', 1: 'FREE', 2: 'SINGLE', 3: 'RANK（排名决斗）',
    4: 'TOURNAMENT', 5: 'TOURNAMENT_SINGLE', 6: 'AUDIENCE', 7: 'REPLAY',
    8: 'RANK_SINGLE', 9: 'SOLO_SINGLE', 10: 'ROOM', 11: 'EXHIBITION',
    12: 'DUELIST_CUP', 13: 'RANK_EVENT', 14: 'TEAM_MATCH', 15: 'DUEL_TRIAL',
    16: 'WCS（世界大赛）', 17: 'VERSUS', 18: 'WCSFINAL', 19: 'RATE（巅峰决斗）',
    # ⚠️ 21 = 'NULL' 是 Untapped 枚举里的**占位名**，不是"没有模式"：
    #    2026-09-12 实测**骰子拉力（活动赛）读到就是 21**（真实对局，mode=pvp，
    #    内存采样全程 21）。活动赛别指望枚举里有名字 —— 判据是「不等于 3/19」。
    #: ★ Untapped 中文界面的分类名（2026-09-13 截图核对）：排名决斗=3 / 巅峰决斗=19 /
    #:    「所有赛事」下含 世界大赛=16、决斗赛=12（DUELIST_CUP）以及若干主题赛事 /
    #:    「其他」如 21 骰子拉力。**「决斗赛」与「世界大赛」同样只是「赛事」的一种**
    #:    —— 主口径只认 3 + 19，其余一律「单独统计」（用户 2026-09-13 拍板）。
    20: 'RDC', 21: 'NULL',
}

#: ★ 用户口径（2026-09-11）：**只统计排名决斗 + 巅峰决斗**。
#: 其它模式（活动赛等）每个月都会变，不纳入统计。
COUNTED_GAME_MODES = (3, 19)      # RANK / RATE

#: `my_deck_snapshot`（我方卡组快照）**可信**的模式 —— 只有这些模式里玩家「必选常规卡组」。
#: ⚠️ 与上面那个「进不进统计」的集合**不是同一个口径**、当前只是同值：
#: ⚠️ **12（DC 杯 / DUELIST_CUP）不要加** —— 2026-09-13 用外部数据（另一个项目
#:   1785 场 Untapped 原始 API）验完了：DC 杯 193 场的 `friendly_deck_id` **全部**是
#:   `CUP_Deck`（活动专用卡组位，跟 `WCS_Deck` / `EXH_*` / `RDC_Deck` 同类），
#:   与我们 ClientWork 里的独立容器 `CUPDeckList` 吻合 —— **DC 杯同样不走常规
#:   `Deck` 容器**，常规快照在那里一样是「大厅卡组」这类错值。
#:   详见 docs/活动局快照防线方案.md §十二。
#: 消费方：`core/duel_record.to_row()`（出口兜底）+ `tools/duel_collector._start_record()`
#: （采集侧主防线）。一处定义、两处引。
#: ★ 09-28 终版：天梯/巅峰 own-deck **来源不同但都可信** —— 现场实测（用户在
#: 巅峰界面打开卡组编辑时采样）：天梯 = ClientWork `Deck`（常规快照）；
#: 巅峰 = **DeckEdit 编辑实例**（`DTDeck` 容器是空的；用户巅峰选的牌在编辑实例里，
#: deck_name='闪刀姬'、识别=Sky Striker）。采集器按 game_mode 分流
#: （19 → snapshot_peak_deck），口径上两者都可信（曾一度收紧到 (3,) 又回）。
DECK_SNAPSHOT_TRUSTED_MODES = (3, 19)


#: ★ 用户口径（2026-09-12）：模式**只分四类** ——
#: 天梯（3 RANK）/ 巅峰（19 RATE）/ **DC 杯（12 DUELIST_CUP）** 各自单独一类，
#: **其它一切统统算「活动决斗」**。
#:
#: ⚠️ 为什么不去枚举活动：**每个月的活动不一样，`game_mode` 的值也会变**
#: （实测骰子拉力 = 21，而 Untapped 枚举里 21 只是个占位名 `NULL`；
#: 三天的 Duel Trial = 15 等）。所以判据是「**不在前三者里就是活动**」，
#: 而不是"认识某个活动号"。
MODE_GROUP_RANKED = 'ranked'
MODE_GROUP_PEAK = 'peak'
MODE_GROUP_DC = 'dc'
MODE_GROUP_EVENT = 'event'
#: **单人（PvE）**：哪边口径都不算、悬浮窗也不显示
#: （2026-09-26 用户实测 game_mode=9 = 单人模式；**15 = Duel Trial 决斗试炼
#: 仍算活动**——用户 09-26 拍板，别再挪进 solo）。
MODE_GROUP_SOLO = 'solo'

#: 分类 -> 中文名（给 UI / CLI 显示用）
MODE_GROUP_NAMES = {
    MODE_GROUP_RANKED: '排名决斗',
    MODE_GROUP_PEAK: '巅峰决斗',
    MODE_GROUP_DC: 'DC杯',
    MODE_GROUP_EVENT: '活动决斗',
    MODE_GROUP_SOLO: '单人模式',
}

#: 有**专属分类**的 `game_mode`；其余（含每月新活动）一律落 `event`。
MODE_GROUP_BY_GAME_MODE = {
    3: MODE_GROUP_RANKED,
    19: MODE_GROUP_PEAK,
    12: MODE_GROUP_DC,
    9: MODE_GROUP_SOLO,
}


#: 「全部模式」伪分类 —— 只给 CLI / 查询用，**不是** `mode_group()` 的返回值
MODE_GROUP_ALL = 'all'

#: 「全部模式」（UI 口径）的**四组并集** —— `mode_group_clause([])` 展开成它，
#: 结果 == `duel_clause()`（天梯 + 巅峰 + DC + 各活动）。2026-10-14 加。
MODE_SELECT_ALL = (MODE_GROUP_RANKED, MODE_GROUP_PEAK,
                   MODE_GROUP_DC, MODE_GROUP_EVENT)

#: 「一个模式都没选」哨兵（用户 2026-10-15：「全部高亮再点全部应该全部取消」）——
#: 多选菜单里把「全部」也取消后就是这个态：**显示 0 场**。
#: 与「空列表 = 全部模式」区分开 —— 空列表是「不筛模式」，哨兵是「筛掉全部」。
MODE_SELECT_NONE = '__none__'
MODE_SCOPE_COUNTED = 'counted'      # 主口径：只天梯 + 巅峰
MODE_SCOPE_DUEL = 'duel'            # 界面口径：天梯 + 巅峰 + 各活动/杯赛


def duel_clause():
    """**界面口径**（用户 2026-09-16）：「天梯 + 巅峰 + 各活动/杯赛」都要算。

    为什么另立一份：悬浮窗那两块（总览 / 先后攻统计）是「**我这轮打得怎么样**」，
    不该因为某局是活动就看不见 —— 用户实测打骰子拉力时两块全是 0。

    ⚠️ **主口径（`counted_clause()`：只 3+19）一个字没动** —— CLI 的 `--stats`、
    库内按月/按天聚合仍走主口径。两者并存，各有单测盯着。

    组成 = `counted_clause()`（含掉线兜底）∪ DC 杯(12) ∪ 其余活动
    （`event` 分类 = 已分类之外的**非 NULL** game_mode）；**观战/单人**与
    **模式判不出的历史行**仍然不算（mode_group(None) -> None 的语义）。
    """
    return '(%s OR %s OR %s)' % (counted_clause(),
                                 mode_group_clause(MODE_GROUP_DC),
                                 mode_group_clause(MODE_GROUP_EVENT))


def mode_group_clause(group):
    """分类 -> SQL 模式条件。**从 `MODE_GROUP_BY_GAME_MODE` 生成，别再写第二份口径。**

    * `'ranked'` / `'peak'` / `'dc'` -> ``game_mode IN (...)``
    * `'event'` -> ``game_mode NOT IN (<已分类的>) AND game_mode IS NOT NULL``
      （=「其余一切」，含每月新活动；**判不出模式的 NULL 不算** —— 与
      `mode_group(None) -> None` 的语义一致）
    * `'all'` -> ``None``（**字面**「不加模式条件」= 不过滤，含判不出模式的残留行）
    * ★ **列表 / 元组 / 集合**（2026-10-14 用户要求「像 Excel 那样打勾多选」）
      -> 各项的**并集**（``OR``）。任一单项是 `'all'` -> ``None``（不过滤）。
      **空列表 = 「全部模式」= :data:`MODE_SELECT_ALL` 四组并集**（= 界面口径
      `duel_clause()`：天梯 + 巅峰 + DC + 各活动）—— 这不是「不过滤」，判不出模式的
      残留行仍不算。⚠️ 2026-10-14 用户报「全部模式显示没有可统计的对局」的根因就是
      空列表以前走 `None`（= 主口径只 3/19），他打的 DC 杯一条都看不到。
    * 其它值 -> 抛 `ValueError`：**绝不静默返回全量**（那会让人看到错数字还不知道）
    """
    if isinstance(group, (list, tuple, set, frozenset)):
        if MODE_SELECT_NONE in group:
            return '0'                   # 「一个模式都没选」= 恒假（筛掉全部）
        parts = []
        for g in group:
            clause = mode_group_clause(g)
            if clause is None:
                return None              # 含 'all' -> 不加条件
            parts.append(clause)
        if not parts:
            #: 空列表 = 「全部模式」= 四组并集（= 界面口径）—— 见上面 docstring。
            parts = [mode_group_clause(g) for g in MODE_SELECT_ALL]
        return '(%s)' % ' OR '.join(parts)
    if group == MODE_SELECT_NONE:
        return '0'                       # 单值哨兵也认（恒假）
    if group == MODE_GROUP_ALL:
        return None
    known = sorted(MODE_GROUP_BY_GAME_MODE)
    if group == MODE_GROUP_EVENT:
        return ('game_mode NOT IN (%s) AND game_mode IS NOT NULL'
                % ','.join(str(m) for m in known))
    ids = sorted(m for m in known if MODE_GROUP_BY_GAME_MODE[m] == group)
    if not ids:
        raise ValueError('未知的模式分类：%r（可用：%s、%s）'
                         % (group, '/'.join(MODE_GROUP_NAMES), MODE_GROUP_ALL))
    clause = 'game_mode IN (%s)' % ','.join(str(m) for m in ids)
    if group == MODE_GROUP_RANKED:
        # 天梯组还要含「掉线 / 中途退出」的兜底局（`counted_clause()` 的第二段，
        # 同一判据走 `disconnect_clause()`）—— 否则这组的和 ≠ 主口径。
        clause = '(%s OR (%s))' % (clause, disconnect_clause())
    return clause


def mode_group(game_mode):
    """`game_mode` -> 四个分类之一；`None`（判不出）返回 `None`。

    这是**显示/归类**用的粗分类，跟"算不算**主口径**"（`counted_clause`）是两件事：
    **主口径永远只含天梯 + 巅峰**；DC 杯与活动杯赛按用户 2026-09-13 拍板
    「**单独统计**」—— 见 :func:`mode_group_clause` / :meth:`Storage.get_group_stats` /
    `tools/archive_matches.py --group-stats`。
    """
    if game_mode is None:
        return None
    return MODE_GROUP_BY_GAME_MODE.get(int(game_mode), MODE_GROUP_EVENT)

#: `matches` 表的全部列（顺序即建表顺序）
MATCH_FIELDS = (
    'match_id', 'started_at', 'ended_at',
    'my_deck_id', 'my_deck_name', 'my_deck_name_src', 'deck_source', 'mode',
    'game_mode',
    'origin',
    'seat', 'seat_evidence',
    'won_initiative', 'went_first', 'first_player',
    'result', 'result_src', 'total_turns', 'duration_sec',
    'opp_deck_id', 'opp_deck_name', 'opp_deck_note',
    'opp_player_name',
    'my_rank_before', 'my_rate_before', 'my_rank_after', 'my_rate_after',
    'opp_rank_before', 'opp_rate_before', 'finish_type',
    # 巅峰决斗积分（2026-09-28）：Rate Duel RATE（如 1507.49）与赛季号。
    # ⚠️ 加列必须四处联动（SCHEMA_SQL / ADDED_COLUMNS / MATCH_FIELDS /
    #    KEEP_IF_NEW_NULL），漏 MATCH_FIELDS = 采集器写了也静默丢。
    'peak_rate_before', 'peak_rate_after', 'peak_season',
    'opp_revealed', 'opp_revealed_ids',
    # 手坑两列（2026-10-16）：JSON 数组字符串，每项
    # [{'turn':0,'id':12950,'key':'ash','name_cn':'灰流丽'}, ...]
    # ⚠️ None = 判不出（seat 判不出 / 无 ShowTurn）-> 界面显示「—」；
    #    [] = 确实没用过。两种语义**别混**（见 core/duel_record.py）。
    'my_traps', 'opp_traps',
    'record_dir', 'result_note', 'winbits',
    'account_name', 'account_pcode',
    'created_at',
)

#: upsert 时**新值为 NULL 就保留旧值**的列。两类：
#:   ① 人工标注 —— 重解析不能抹掉用户填的
#:   ② API 独有字段 —— 日志解析拿不到，别把已回填的值覆盖成 NULL
KEEP_IF_NEW_NULL = (
    'opp_deck_id', 'opp_deck_name', 'opp_deck_note',
    'game_mode', 'opp_player_name', 'finish_type',
    'my_rank_before', 'my_rate_before', 'my_rank_after', 'my_rate_after',
    'opp_rank_before', 'opp_rate_before',
    # 巅峰积分（2026-09-28）：收局后由采集器补写 —— 重解析 upsert 的新值
    # 是 NULL 时必须保留，否则把已回填的积分冲掉（与段位列同一批坑）
    'peak_rate_before', 'peak_rate_after', 'peak_season',
    # M3：record 回填列 —— 新值为 NULL 保留旧值（result 的覆盖走
    # apply_record_meta 的 winbit 优先规则，不走 upsert 的全量覆盖）
    # ⚠️ `result_src` **不在**这里：它跟 `result` 同生共死（见 _build_insert_sql
    #    里那对 CASE 分支），拆开写会出现「值人工、来源自动」的错配。
    'record_dir', 'result_note', 'winbits',
    # 手坑（2026-10-16）：重跑 import 时无 ShowTurn 的局算出 NULL，
    # 必须保留已回填的值（与 record_dir/winbits 同一批坑）
    'my_traps', 'opp_traps',
)

#: 只在**首次插入**时落值，之后永不改动
KEEP_ALWAYS = ('origin', 'created_at')

#: `meta_decks.tier`（分组）的合法值 —— 用户 2026-09-11 定：**一律不带空格**，
#: 且历史遗留的「其它」一律并入 Trending。
VALID_TIERS = ('Tier1', 'Tier2', 'Tier3', 'Trending')

#: M2 之后陆续加的列 —— `init_schema` 会给老库按需 ALTER TABLE
ADDED_COLUMNS = (
    ('mode', 'TEXT'),
    # 我方卡组名的**来源**（2026-09-16）：snapshot-name / m5-guess / current-deck。
    # 为什么值得一列：m5-guess 是**猜的**，以后复核/统计要能区分。
    ('my_deck_name_src', 'TEXT'),
    ('game_mode', 'INTEGER'),
    ('origin', 'TEXT'),
    ('opp_player_name', 'TEXT'),
    ('my_rank_before', 'INTEGER'), ('my_rate_before', 'INTEGER'),
    ('my_rank_after', 'INTEGER'), ('my_rate_after', 'INTEGER'),
    ('opp_rank_before', 'INTEGER'), ('opp_rate_before', 'INTEGER'),
    ('finish_type', 'INTEGER'),
    # 巅峰决斗积分（2026-09-28）：REAL —— 游戏给字符串 '1507.49'，转 float 后
    # round 2 位入库；season 整数（积分跨赛季不可比，2026-09-28 审查 C2）
    ('peak_rate_before', 'REAL'), ('peak_rate_after', 'REAL'),
    ('peak_season', 'INTEGER'),
    # 多账号（2026-10-03）：收局从内存 profile 盖章（name + pcode 唯一键）
    ('account_name', 'TEXT'),
    ('account_pcode', 'INTEGER'),
    # M3：自有对局记录（record）四列
    ('result_src', 'TEXT'),
    ('record_dir', 'TEXT'),
    ('result_note', 'TEXT'),
    ('winbits', 'TEXT'),
    # 手坑两列（2026-10-16）：JSON 数组字符串（结论层，见 core/hand_traps.py）
    ('my_traps', 'TEXT'), ('opp_traps', 'TEXT'),
)

#: 库里按 0/1/NULL 存、读出来要还原成 bool/None 的列
BOOL_FIELDS = ('won_initiative', 'went_first')

#: 库里按 JSON 文本存、读出来要还原成 list 的列
#: 对局表里按 JSON 文本存的列（upsert_match 会遍历它们做 dump）
JSON_FIELDS = ('opp_revealed', 'opp_revealed_ids', 'winbits',
               'my_traps', 'opp_traps')

#: meta_decks 里按 JSON 文本存的列（**不能混进 JSON_FIELDS**，
#: 否则 upsert_match 会去找对局表里不存在的 aliases 而 KeyError）
META_JSON_FIELDS = ('aliases',)

#: upsert 时**只在旧值为 NULL 才被新值填上**的人工字段（重解析不丢标注）
MANUAL_FIELDS = ('opp_deck_id', 'opp_deck_name', 'opp_deck_note')

#: meta 卡组来源：站内抓取 / 我们自己手工建的
SOURCE_META = 'masterduelmeta'
SOURCE_CUSTOM = 'custom'

#: meta_decks 后来加的列（老库需要 ALTER 补上）
META_ADDED_COLUMNS = (('name_cn', 'TEXT'), ('name_display', 'TEXT'),
                      ('name_ja', 'TEXT'),
                      ('hidden', 'INTEGER DEFAULT 0'), ('aliases', 'TEXT'))

SCHEMA_SQL = """
-- 1. 我的卡组（来自 decks.json + 兜底占位）
CREATE TABLE IF NOT EXISTS my_decks (
    deck_id     TEXT PRIMARY KEY,           -- '48171157' / 'DR_Deck_1'
    name        TEXT NOT NULL,
    source      TEXT,                       -- 'decks_json' | 'manual' | 'log'
    updated_at  INTEGER
);

-- 2. 对手卡组（用户自维护）
CREATE TABLE IF NOT EXISTS opp_decks (
    opp_deck_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    aliases     TEXT,                       -- JSON 数组
    use_count   INTEGER DEFAULT 0,
    created_at  INTEGER
);

-- 2b. Meta 卡组清单（**外部参考**，来自 masterduelmeta，按月整体刷新）
--
-- ⚠️ 为什么不并进 opp_decks：
--    opp_decks 承载**用户自己的**别名(aliases)与使用计数(use_count)，是长期累积的；
--    meta_decks 是随时可整体覆盖的外部快照。两者生命周期不同，混一张表会互相覆盖。
--
-- kind 的语义（用户口径）：
--    'deck_type' = 单一主题卡组，如 Dracotail
--    'engine'    = **混合卡组**，如 Kewl Tune + Elfnote 混编；
--                  哪边卡多就归为哪边的 engine（Kewl Tune 多 -> Kewl Tune Engine）
CREATE TABLE IF NOT EXISTS meta_decks (
    meta_deck_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,             -- 'Dracotail' / 'Kewl Tune Engine'
    kind         TEXT NOT NULL,             -- 'deck_type' | 'engine'
    name_cn      TEXT,                      -- 中文名（选择框显示）；NULL = 还没补
    name_ja      TEXT,                      -- 日文名（日语显示 / 日语搜索）；NULL = 还没补
    name_display TEXT,                      -- 显示用英文名（覆盖站内名）；NULL = 用 name
    hidden       INTEGER DEFAULT 0,         -- 1 = 不进选择框（等于手工删掉）
    aliases      TEXT,                      -- JSON 数组：同一卡组的其它叫法，搜索用
    tier         TEXT,                      -- 'Tier1'|'Tier2'|'Tier3'|'Trending'，可空
    power        REAL,                      -- 强度分（强度榜）
    popularity   REAL,                      -- 使用率百分数，如 7.25（使用率榜）
    slug         TEXT,                      -- 站内链接片段
    img_url      TEXT,                      -- 远程图 URL（备份用）
    img_file     TEXT,                      -- 本地图相对路径
    source       TEXT DEFAULT 'masterduelmeta',
    fetched_at   INTEGER,
    UNIQUE(name, kind)
);

CREATE INDEX IF NOT EXISTS idx_meta_tier ON meta_decks(tier, power DESC);

-- 3. 对局主表
CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY,       -- sha1 前 16 位
    started_at      TEXT NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS.fff'
    ended_at        TEXT,
    my_deck_id      TEXT,                   -- 可空
    my_deck_name    TEXT,                   -- 冗余快照（UI 高频读）
    deck_source     TEXT,                   -- 'log' | 'selected-fallback' | 'snapshot' | 'snapshot-untrusted' | 'unknown'
    mode            TEXT,                   -- 'pvp' | 'solo' | 'live' | 'unknown'
    game_mode       INTEGER,                -- Untapped API 的原始模式值（3=RANK,19=RATE）
    origin          TEXT,                   -- 'log'=日志解析 | 'api'=从 API 补录
    seat            INTEGER,                -- 0/1
    seat_evidence   TEXT,                   -- 调试用
    won_initiative  INTEGER,                -- 0/1/NULL（稀疏，别用于统计）
    went_first      INTEGER,                -- 0/1/NULL（实际先攻，100% 覆盖）
    first_player    INTEGER,                -- Turn 1 判出的原始座位号
    result          TEXT,                   -- 'Win' | 'Lose' | 'Draw' | '(未结束)'
    result_src      TEXT,                   -- M3：结果来源 'winbit' | 'manual' | 'log'
    total_turns     INTEGER,
    duration_sec    INTEGER,
    opp_deck_id     INTEGER,                -- NULL = 未知
    opp_deck_name   TEXT,                   -- 冗余快照
    opp_deck_note   TEXT,                   -- 备注
    opp_player_name TEXT,                   -- 对手玩家名（★ 只有 API 有）
    my_rank_before  INTEGER,                -- 段位（大段：5=Platinum 6=Diamond 7=Master）
    my_rate_before  INTEGER,                -- 段位内小段位
    my_rank_after   INTEGER,
    my_rate_after   INTEGER,
    opp_rank_before INTEGER,
    opp_rate_before INTEGER,
    peak_rate_before REAL,                 -- 巅峰决斗积分（Rate Duel RATE，局前）
    peak_rate_after  REAL,                 -- 巅峰积分（局后）
    peak_season      INTEGER,              -- 巅峰赛季号（跨赛季不可比）
    finish_type     INTEGER,                -- API 的结束方式编码
    opp_revealed    TEXT,                   -- JSON 数组（卡名）
    opp_revealed_ids TEXT,                  -- JSON 数组（卡 ID）
    record_dir      TEXT,                   -- M3：自有记录目录名（origin='record' 或已并档的 log 行）
    result_note     TEXT,                   -- M3：收局文案备注（只记录不判断）
    winbits         TEXT,                   -- M3：JSON [我方位, 对方位]（胜负判据）
    my_traps        TEXT,                   -- 手坑（我）：JSON 数组，NULL=判不出
    opp_traps       TEXT,                   -- 手坑（对手）：同上
    account_name    TEXT,                   -- 多账号（2026-10-03）：我方游戏名（收局盖戳）
    account_pcode   INTEGER,                -- 我方玩家代码（profile.pcode，唯一账号键）
    created_at      INTEGER,

    FOREIGN KEY (my_deck_id)  REFERENCES my_decks(deck_id),
    FOREIGN KEY (opp_deck_id) REFERENCES opp_decks(opp_deck_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_time   ON matches(started_at);
CREATE INDEX IF NOT EXISTS idx_matches_opp    ON matches(opp_deck_id);
CREATE INDEX IF NOT EXISTS idx_matches_mydeck ON matches(my_deck_id);
CREATE INDEX IF NOT EXISTS idx_matches_result ON matches(result);

-- 4. 禁限卡变更史（来自 masterduelmeta 公开 API，**手动按月刷新**）
--
-- ⚠️ 卡 id 是**站内的 `_id`**（不是游戏内卡 id）；中文名/日文名靠 `card_en`
--    反查 `data/card_names.json`（见 tools/fetch_banlist.py）。
--    `to_status` 为 NULL = 解除限制（变回无限制）。
CREATE TABLE IF NOT EXISTS banlist_changes (
    announce_id TEXT NOT NULL,              -- 站内公告 _id
    card_mdm_id TEXT NOT NULL,              -- 站内卡 _id
    card_en     TEXT,                       -- 英文卡名（API 直接给）
    from_status TEXT,                       -- NULL = 之前没被限制
    to_status   TEXT,                       -- NULL = 解除限制
    effective   TEXT,                       -- 生效时间（ISO）
    announced   TEXT,                       -- 公告时间（ISO）
    url         TEXT,                       -- 相关新闻
    fetched_at  INTEGER,
    PRIMARY KEY (announce_id, card_mdm_id)
);

CREATE INDEX IF NOT EXISTS idx_banlist_eff ON banlist_changes(effective);

-- 当前禁限状态（**站内 cards API 的 `banStatus`**，权威）
-- ⚠️ 别再按变更史累计算这个 —— 那算法漏得出奇（2026-09-21 实报：简易融合
--     变更史停在 2023「限制→禁止」，站内实际是限制；另有 77 张老禁卡
--     在变更史里从未出现过）。
CREATE TABLE IF NOT EXISTS banlist_current (
    card_en   TEXT PRIMARY KEY,             -- 英文卡名（站内 cards.name，唯一键）
    status    TEXT NOT NULL,                -- 'Forbidden' | 'Limited 1' | 'Limited 2'
    fetched_at INTEGER
);
"""

def _build_insert_sql():
    """按 MATCH_FIELDS 生成 upsert 语句 —— 免得手写列名跟 schema 漂移。

    更新规则见 KEEP_IF_NEW_NULL / KEEP_ALWAYS 的注释。

    ``result`` / ``result_src`` 两列**成对**处理：旧行的 ``result_src`` 不是
    :data:`AUTO_RESULT_SRCS`（人工 / API 补录…）且旧值非空时两列都保持旧值
    （人工修复不许被自动解析冲掉，2026-09-18 用户口径；2026-10-01 起保护圈
    从『= manual』扩成『不在自动来源集合』—— API 补录的 src 同样不许冲）。

    ``my_deck_name`` / `my_deck_name_src` / `my_deck_id` / `deck_source`
    四列**成组**处理（2026-10-01 事故，见 :data:`AUTO_MY_DECK_NAME_SRCS`）：
    旧名字非空且（新名字为空或旧来源是人工侧）→ 整组保持旧值。
    ★ 2026-10-18：**NULL-src 不再算人工侧**（snapshot 路径本来就不写 src，
    把它当人工值保护会把采集器的第一版输出钉死，归一/修正永远进不来）——
    详见 `_deck_keep` 处的注释。
    """
    _auto_result = ', '.join("'%s'" % s for s in AUTO_RESULT_SRCS)
    _auto_deck = ', '.join("'%s'" % s for s in AUTO_MY_DECK_NAME_SRCS)
    #: result 组的保护判据（`result` / `result_src` 两列共用，保证成组移动）：
    #: 旧值是**人工结论**（src 非空且不在自动集合）时才顶住。
    #: ★ 2026-10-18：加 `matches.result IS NOT NULL` —— 旧值为空时没有什么
    #: 可保护的（那 1 行 `src='manual'` 但 result=NULL 的脏行就是被这条卡住，
    #: 永远填不上；与 `apply_record_meta` 的 `manual` 判据同口径）。
    #: ⚠️ 新值为空的保护**不在这条判据里**，而在两个 CASE 的
    #: `COALESCE(excluded.…, matches.…)` —— 「保留旧值」对 result 也要生效
    #: （原来只有 result_src 有，result 是裸 `excluded.result`，见下方注释）。
    _result_keep = ("matches.result IS NOT NULL"
                    " AND matches.result_src IS NOT NULL"
                    " AND matches.result_src NOT IN (%s)" % _auto_result)
    #: my_deck 组的保护判据（四列共用同一条件，保证成组移动）：
    #: 旧名字非空 且（新名字为空 或 旧来源不是已知自动来源）。
    #: ⚠️ 与 result 刻意**不对称**：胜负有「winbit 真值优先」的自动纠正流
    #: （apply_record_meta），NULL-src 旧值要允许被覆盖；名字没有更真的
    #: 自动源（同一 duel.json 重解码结果不变），库里有值就该顶住——
    #: 09-28 把 48 行 K9vs 统一成 K9VS 的手改就是这么被冲回去的。
    #: ★ 2026-10-18：NULL-src **不再享受保护**（与 `AUTO_RESULT_SRCS` 口径对齐）。
    #: 实机起因：神艺局库里 14 行 `my_deck_name='狱神神艺'`（自组名、src=NULL），
    #: 加了「自组名→显示名」归一后重解析**改不动**这些行 —— 手坑汇总里
    #: 「狱神神艺」和「神艺」分成两桶。NULL-src 的语义是「没有人工介入」
    #: （snapshot 路径本来就不写 src），把它当人工值保护等于把采集器的
    #: 第一版输出钉死，归一 / 修正永远进不来。
    #: ⚠️ 只放开 NULL-src；`manual` / `manual-fix-*` 等人工来源照旧顶住。
    _deck_keep = ("matches.my_deck_name IS NOT NULL"
                  " AND (excluded.my_deck_name IS NULL"
                  "      OR (matches.my_deck_name_src IS NOT NULL"
                  "          AND matches.my_deck_name_src NOT IN (%s)))" % _auto_deck)
    sets = []
    for f in MATCH_FIELDS:
        if f == 'match_id':
            continue
        if f == 'result':
            #: ★ 2026-10-18 修「重解析把 result 抹成 NULL」：
            #: 旧行 result='Win'/src='duel_code' 遇上「新解析判不出结果」
            #: （end_messages 没认领到该局 → result=NULL/src=NULL）时，
            #: 原判据只看「旧 src 是否人工」，else 分支直接吃 NULL ——
            #: 而 result_src 那列的 else 是 `COALESCE(excluded, matches)`，
            #: **新 src 为空时保留旧 src**。两列判据不对称，于是库里留下
            #: 自相矛盾的脏状态：`result_src='duel_code'` 但 `result=NULL`
            #: （实机 25 行，全量重导时一次性爆发）。
            #: 修法 = 两列判据对齐 + **新值为空一律保留旧值**：
            #:   * 旧值是人工结论（src 非自动）→ 顶住，新值再真也不覆盖；
            #:   * 旧值是自动结论、新值判不出 → 保留旧值（不许抹）；
            #:   * 旧值是自动结论、新值有结论 → 按新值刷新（winbit 真值优先）。
            #: ⚠️ 「保留旧值」必须 result / result_src **成组**，否则又是不对称。
            sets.append("result = CASE WHEN %s"
                        " THEN matches.result"
                        " ELSE COALESCE(excluded.result, matches.result) END"
                        % _result_keep)
        elif f == 'result_src':
            sets.append("result_src = CASE WHEN %s"
                        " THEN matches.result_src"
                        " ELSE COALESCE(excluded.result_src, matches.result_src)"
                        " END" % _result_keep)
        elif f == 'my_deck_name':
            sets.append('my_deck_name = CASE WHEN %s'
                        ' THEN matches.my_deck_name'
                        ' ELSE excluded.my_deck_name END' % _deck_keep)
        elif f == 'my_deck_name_src':
            sets.append('my_deck_name_src = CASE WHEN %s'
                        ' THEN matches.my_deck_name_src'
                        ' ELSE excluded.my_deck_name_src END' % _deck_keep)
        elif f == 'my_deck_id':
            sets.append('my_deck_id = CASE WHEN %s'
                        ' THEN matches.my_deck_id'
                        ' ELSE excluded.my_deck_id END' % _deck_keep)
        elif f == 'deck_source':
            sets.append('deck_source = CASE WHEN %s'
                        ' THEN matches.deck_source'
                        ' ELSE excluded.deck_source END' % _deck_keep)
        elif f == 'account_name':
            # 多账号章（2026-10-03）：**一旦有值就不许任何来源改写** ——
            # 采集器盖的是「当时登录的号」，API 盖的是 untapped 号，
            # 两边打架时先到的赢（用户口径：API 补的局都是示例玩家的号）。
            sets.append('account_name = COALESCE(matches.account_name,'
                        ' excluded.account_name)')
        elif f == 'account_pcode':
            sets.append('account_pcode = COALESCE(matches.account_pcode,'
                        ' excluded.account_pcode)')
        elif f in KEEP_IF_NEW_NULL:
            sets.append('%s = COALESCE(excluded.%s, matches.%s)' % (f, f, f))
        elif f in KEEP_ALWAYS:
            sets.append('%s = COALESCE(matches.%s, excluded.%s)' % (f, f, f))
        else:
            sets.append('%s = excluded.%s' % (f, f))
    return ('INSERT INTO matches (%s) VALUES (%s)'
            ' ON CONFLICT(match_id) DO UPDATE SET %s'
            % (', '.join(MATCH_FIELDS),
               ', '.join(':' + f for f in MATCH_FIELDS),
               ', '.join(sets)))


INSERT_MATCH_SQL = _build_insert_sql()


# ------------------------------------------------------------------ 类型转换
def _bool_to_int(v):
    """True/False/None -> 1/0/NULL。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    return int(v)


def _int_to_bool(v):
    """1/0/NULL -> True/False/None。"""
    if v is None:
        return None
    return bool(v)


def _json_dump(v):
    """list/dict -> JSON 文本；字符串原样透传；None -> NULL。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _json_load(v):
    """JSON 文本 -> list/dict；解不开就返回 None。"""
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _ts_of(row, col='started_at'):
    """行内时间文本 -> unix 秒（空值 0.0）；配对窗口排序用。"""
    v = row.get(col) if hasattr(row, 'get') or isinstance(row, dict) else row[col]
    if not v:
        return 0.0
    return datetime.strptime(v[:19], '%Y-%m-%d %H:%M:%S').timestamp()


def _row_to_dict(row):
    """sqlite3.Row -> 与 jsonl 时代形状一致的 dict。"""
    d = {k: row[k] for k in row.keys()}
    for k in BOOL_FIELDS:
        if k in d:
            d[k] = _int_to_bool(d[k])
    for k in JSON_FIELDS + META_JSON_FIELDS:
        if k in d:
            d[k] = _json_load(d[k])
    if 'mode' in d and d['mode'] is None:
        d['mode'] = MODE_UNKNOWN
    return d


def disconnect_clause():
    """「掉线 / 中途退出的天梯局」兜底判据 —— **全项目只有这一份定义**。

    这种局 API 里没有（客户端没走完上报流程）→ `game_mode` 判不出，只能靠
    `mode='pvp'` + `ended_at IS NULL` 认。`counted_clause()`（主口径）与
    `mode_group_clause('ranked')`（天梯组）**共用它** —— 后者必须含它，
    否则「天梯 + 巅峰」的和 ≠ 主口径。
    """
    return "game_mode IS NULL AND mode = '%s' AND ended_at IS NULL" % MODE_PVP


def counted_clause():
    """只算「排名决斗 + 巅峰决斗」—— **用户口径（2026-09-11）**。

    其它模式（活动赛等）每个月都会变，不纳入统计。

    **★ 杯赛怎么算（用户 2026-09-13 拍板）**：DC 杯（12）/ 同款杯赛（WCS 16）
    与各活动「**单独统计**」—— 即**不入主口径**（主口径永远只 3 + 19），
    按分组各看各的：`get_group_stats()` / `tools/archive_matches.py --group-stats`
    （2026-09-13 已落地；分类见 :func:`mode_group`）。
    库里 12/16 的局尚未出现，出现即入 dc/event 组。

    判据分两层：

    1. **有 API 的 `game_mode`** → 必须属于 `COUNTED_GAME_MODES`（3=RANK / 19=RATE）
    2. **没有 `game_mode`**（这场 API 里查不到）→ 只有
       `mode='pvp'` **且 `ended_at IS NULL`** 才算 —— 那是**掉线/中途退出**的
       天梯局（客户端没上报，所以 API 里没有；用户要求判负并计入）。
       活动赛/单人/观战/判不出的，一律不计入。
    """
    return ('(game_mode IN (%s) OR (%s))'
            % (','.join(str(m) for m in COUNTED_GAME_MODES),
               disconnect_clause()))


def _deck_expr(col):
    """卡组名的归一化表达式，空串也算"未识别"（对齐 v1 的 `or` 语法）。"""
    return ("CASE WHEN %s IS NULL OR %s = '' THEN '(未识别)' ELSE %s END"
            % (col, col, col))


#: 对手卡组的归一化表达式 —— 没标注的局要跟「标注成空」区分开，
#: 单列一个 '(未标注)'，`get_cross_stats()` 才能一眼看出还差多少没标。
OPP_DECK_EXPR = "COALESCE(NULLIF(TRIM(opp_deck_name), ''), '(未标注)')"


#: 「一轮（会话）」的断开阈值：**相邻两局间隔 ≥ 这个分钟数**就算下一轮。
#: 用户 2026-09-16 拍板：总览 / 先后攻统计**按「每一轮」算**，不是按日历日。
SESSION_GAP_MIN = 60


def parse_dt(s):
    """定宽时刻文本（可带毫秒）-> ``datetime``；认不出给 ``None``。"""
    try:
        return datetime.strptime(str(s)[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def end_time_of(started_at, ended_at=None, duration_sec=None):
    """一局的**结束时刻**文本 -> 认不出给 ``None``。

    ★ 切轮判据要的是「**上一局结束 → 本局开局**」的间隔，不是两个开局之间。
    为什么（用户 2026-09-17 提问引出）：一局**超长对局**会让「按开局算」的间隔
    凭空多出这局的时长 —— 上一局 21:00 开局、打到 22:50，本局 22:55 开局，
    按开局算差 115 分钟 → **误判成「歇了一轮」**；按结束算只差 5 分钟 = 连着打。

    三层回退（越靠前越准）：
    1. ``ended_at`` —— 日志的 ``Game ended``；
    2. ``started_at + duration_sec`` —— 有时长记录（API / 日志）；
    3. ``started_at`` —— 两样都没有的老数据：**退回老口径**（等于不按时长算），
       不猜、也不因此丢掉这一局。
    """
    sd = parse_dt(started_at)
    if sd is None:
        return None
    ed = parse_dt(ended_at)
    if ed is not None and ed >= sd:
        return ed.strftime('%Y-%m-%d %H:%M:%S')
    try:
        dur = int(duration_sec)
    except (TypeError, ValueError):
        dur = None
    if dur is not None and dur > 0:
        return (sd + timedelta(seconds=dur)).strftime('%Y-%m-%d %H:%M:%S')
    return sd.strftime('%Y-%m-%d %H:%M:%S')


def session_start_of(matches, gap_minutes: int = SESSION_GAP_MIN, now=None):
    """一串（**按时间升序**的）局 -> **最后一轮**的起点。

    **纯函数**（单测直接喂字符串列表）—— 时间切分这种判据最容易写错，必须
    能不建库就测。

    ``matches`` 每项：``started_at`` 字符串（老式，等价于「结束 = 开始」），
    或 ``(started_at, ended_at)`` 二元组 —— 推荐后者。

    **切轮看「上一局结束 → 本局开局」**（见 :func:`end_time_of`）：
    只看两个开局之间的间隔会被**超长对局**骗（那局的时长会白算进间隔里）。

    为什么用「间隔切轮」而不是监听游戏进程：悬浮窗的口径是**只读库、不碰内存**
    （项目红线）。而且**随时打开窗口都能算出来** —— 不需要常驻任务，
    也不需要凌晨的定时器。

    ⚠️ **`now` 是「本轮还活着吗」的判据**（2026-09-16 晚补）——
    只看「相邻两局之间」是不够的：下午 16:16 打完、晚上 23:34 打开窗口，
    中间 7 小时的空档**没有任何一对相邻局能体现出来**，于是仍返回下午那轮的起点，
    统计一直显示旧数字（实机事故）。给了 `now` 且
    `now - 最后一局结束 >= gap` → 返回 **None** = **本轮已结束**。

    `now` 不给（默认）= **不判尾部**，只做「切轮」这一件事 —— 两种关注点分开，
    老调用方与老单测行为不变。`now` 可以是 ``datetime`` 或同一格式的字符串。
    """
    pairs = []
    for m in matches or ():
        if isinstance(m, (tuple, list)):
            s = m[0] if len(m) > 0 else None
            e = m[1] if len(m) > 1 else None
        else:
            s, e = m, None
        sd = parse_dt(s)
        if sd is None:
            continue                    # 坏数据跳过（坏一局不该让统计全挂）
        ed = parse_dt(e)
        if ed is None or ed < sd:
            ed = sd                     # 没有/坏掉的结束时刻 -> 退回开局
        pairs.append((sd, ed))
    if not pairs:
        return None
    cut = pairs[0][0]                   # 没有断开 = 整段就是一轮
    for (_ps, pe), (cs, _ce) in zip(pairs, pairs[1:]):
        if (cs - pe).total_seconds() >= gap_minutes * 60:
            cut = cs
    if now is not None:
        now_dt = parse_dt(now) if not isinstance(now, datetime) else now
        if now_dt is not None:
            idle = (now_dt - pairs[-1][1]).total_seconds()
            if idle >= gap_minutes * 60:
                return None             # 最后一局结束已超时 -> 本轮结束
    return cut.strftime('%Y-%m-%d %H:%M:%S')


def day_range(day):
    """'2026-09-10' -> ('2026-09-10 00:00:00', '2026-09-10 23:59:59.999')。

    started_at 是定宽文本，直接用字符串比较即可，不会跨天串味。
    """
    return day + ' 00:00:00', day + ' 23:59:59.999'


# ------------------------------------------------------------------ Storage
class Storage:
    """SQLite 存储层。所有对局数据的读写入口。

    用法::

        st = Storage()                 # 默认 data/masterduel.db
        st.init_schema()
        st.upsert_match(row)
        st.get_today_stats()
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB

    # -------------------------------------------------------- 连接 / 建表
    @contextmanager
    def _open(self):
        """一次事务。正常退出即提交，异常回滚。"""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self):
        """建表 + 建索引 + 轻量补列。全部 IF NOT EXISTS，已有库不会被覆盖。"""
        with self._open() as conn:
            conn.executescript(SCHEMA_SQL)
            # 轻量迁移：老库补后来加的列（全部可空，不影响已有行）
            cols = {r['name'] for r in conn.execute('PRAGMA table_info(matches)')}
            for col, typ in ADDED_COLUMNS:
                if col not in cols:
                    conn.execute('ALTER TABLE matches ADD COLUMN %s %s' % (col, typ))
            mcols = {r['name'] for r in conn.execute('PRAGMA table_info(meta_decks)')}
            for col, typ in META_ADDED_COLUMNS:
                if col not in mcols:
                    conn.execute('ALTER TABLE meta_decks ADD COLUMN %s %s' % (col, typ))
            # mode 为空一律视作 unknown（老记录日志已轮转，判不出来）
            conn.execute("UPDATE matches SET mode = ? WHERE mode IS NULL",
                         (MODE_UNKNOWN,))
            conn.execute('UPDATE matches SET origin = ? WHERE origin IS NULL',
                         (ORIGIN_LOG,))

    # -------------------------------------------------------- 写入
    @staticmethod
    def _ensure_my_deck(conn, deck_id, name):
        """外键占位：对局引用的我的卡组不存在时补一行（不覆盖已有名字）。"""
        if not deck_id:
            return
        cur = conn.execute('SELECT 1 FROM my_decks WHERE deck_id = ?', (deck_id,))
        if cur.fetchone() is None:
            conn.execute(
                'INSERT OR IGNORE INTO my_decks (deck_id, name, source, updated_at)'
                ' VALUES (?, ?, ?, ?)',
                (deck_id, name or deck_id, 'log', int(time.time())))

    @staticmethod
    def _ensure_opp_deck(conn, opp_deck_id, name):
        """外键占位：对局引用的对手卡组不存在时补一行。"""
        if opp_deck_id is None:
            return
        cur = conn.execute('SELECT 1 FROM opp_decks WHERE opp_deck_id = ?',
                           (opp_deck_id,))
        if cur.fetchone() is None:
            conn.execute(
                'INSERT OR IGNORE INTO opp_decks (opp_deck_id, name, aliases,'
                ' use_count, created_at) VALUES (?, ?, ?, 0, ?)',
                (opp_deck_id, name or str(opp_deck_id), None, int(time.time())))

    def upsert_match(self, row, judge_lack_end=True):
        """插入或更新一条对局（按 match_id 去重）。

        日志派生字段全量覆盖；``opp_deck_id`` / ``opp_deck_name`` /
        ``opp_deck_note`` 仅在新值为 NULL 时保留旧值 —— 重解析不会抹掉人工标注。

        ⚠️ **重复行防护**：``match_id = sha1(开始时间|卡组|座位)``，而卡组和座位
        都是**从日志上下文推导**出来的。日志轮转只保留 1 份，一旦某局的
        ``Selected deck changed`` 兜底行被轮转掉，同一局重解析就会算出**另一个
        match_id**（实测 2026-09-07 09:47:17.650 那局：库内 deckId=48171157，
        重解析 deckId=null），于是同一局在库里变成两行。

        所以这里先用 ``started_at`` 查一次：库里已有同一开始时间的记录就直接跳过，
        保留先写入的那一行（它来自上下文更完整的解析）。实测 12 条
        ``Starting Duel`` 的开始时间毫秒级互不相同，所以这个判重是安全的。

        **模式与判负**（M2.1 加入，用户 2026-09-11 口径）：
        * ``mode == 'live'``（观战/回放）**根本不入库** —— 它不是对局，返回
          ``'skipped-live'``。调用方可用 ``delete_match`` 清掉历史误录。
        * ``mode == 'pvp'`` 且**没有结束标记**（result 是 ``(未结束)``）→
          **直接判负**，result 改成 ``'Lose'``。此时 ``ended_at`` 保持 NULL，
          因为它就是"客户端没记到结束"的标记（中途退出/掉线都走这条）。

        返回 ``'written'`` / ``'skipped'`` / ``'skipped-live'``。
        """
        params = {k: row.get(k) for k in MATCH_FIELDS}
        if params.get('origin') is None:
            params['origin'] = ORIGIN_LOG
        if params.get('mode') is None:
            params['mode'] = MODE_UNKNOWN
        if params['mode'] == MODE_LIVE:
            return 'skipped-live'
        # 单人模式（PvE，game_mode=9）**不入库** —— 用户 2026-10-08 口径：
        # 「我只需要记录 PVP 的数据」。拦在同一个地方（入库口），调用方拿
        # 'skipped-solo'。⚠️ 只拦**入库**；采集器的 record 目录（层 0 真源）照旧抓。
        if params['mode'] == MODE_SOLO:
            return 'skipped-solo'
        # 中途退出 / 掉线 → 判负（ended_at 留空，标记"没看到结束"）。
        # ⚠️ record 来源可以关掉这条（judge_lack_end=False）：胜负只认 winbit，
        # 判不出留 NULL 等手动补填，不许按"没看到结束"硬判负。
        if (judge_lack_end and params['mode'] == MODE_PVP
                and params.get('result') not in DONE_RESULTS):
            params['result'] = 'Lose'
        params['created_at'] = row.get('created_at') or int(time.time())
        for k in BOOL_FIELDS:
            params[k] = _bool_to_int(params[k])
        for k in JSON_FIELDS:
            params[k] = _json_dump(params[k])
        with self._open() as conn:
            if params['started_at']:
                prev = conn.execute(
                    'SELECT match_id FROM matches WHERE started_at = ?',
                    (params['started_at'],)).fetchone()
                if prev is not None and prev['match_id'] != params['match_id']:
                    return 'skipped'
            self._ensure_my_deck(conn, params['my_deck_id'], params['my_deck_name'])
            self._ensure_opp_deck(conn, params['opp_deck_id'], params['opp_deck_name'])
            conn.execute(INSERT_MATCH_SQL, params)
        return 'written'

    def upsert_many(self, rows):
        """批量 upsert，返回 ``{'written': n, 'skipped': n, 'skipped-live': n}``。

        逐条调用 ``upsert_match``（每条各自一个事务）：本项目单次最多几百局，
        开销可以忽略，换来的是单条失败不会把整批回滚掉。
        """
        out = {'written': 0, 'skipped': 0, 'skipped-live': 0,
               'skipped-solo': 0}
        for r in rows:
            out[self.upsert_match(r)] += 1
        return out

    @staticmethod
    def _row_ts(row):
        """把 started_at 文本转成 unix 秒（用于和 API 的 match_start 配对）。"""
        import datetime as _dt
        return _dt.datetime.strptime(row['started_at'][:19],
                                     '%Y-%m-%d %H:%M:%S').timestamp()

    def set_game_mode(self, match_id, game_mode):
        """写入 Untapped API 的原始模式值（3=排名决斗 / 19=巅峰决斗 …）。"""
        with self._open() as conn:
            cur = conn.execute('UPDATE matches SET game_mode = ? WHERE match_id = ?',
                               (game_mode, match_id))
            return cur.rowcount

    def find_by_record_dir(self, record_dir):
        """按**记录目录名**找对局行（`matches.record_dir` 是精确 join 键）。

        :return: 行 dict；没有这一行给 None
        """
        with self._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE record_dir = ?',
                               (record_dir,)).fetchone()
        return _row_to_dict(row) if row is not None else None

    def set_result_if_null_by_record(self, record_dir, result, src):
        """结算码兜底**提前补结果**（2026-10-01 深夜）：**只补空、不覆盖**。

        🔴 背景：无文案局（整读全落空）的结果原来要等 90s EM 窗口收口才落库，
        用户看着就是「没有结果」。结算码（ClientWork DuelResult）在窗口开头
        就读到了 —— 提前直写，EM 窗口晚到的 winbit 仍按「winbit 优先」覆盖
        （非人工值可被真值升级），行级修正（manual）不受影响。
        按 `record_dir` 定位（目录名，非路径）。返回更新的行数。
        """
        assert result in DONE_RESULTS, 'result 只认 %s' % (DONE_RESULTS,)
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE matches SET result = ?, result_src = ?"
                " WHERE record_dir = ? AND result IS NULL",
                (result, src, record_dir))
            return cur.rowcount

    def backfill_my_rank_before(self, match_id, rank, rate):
        """把**内存读到的局前段位**并进既有行（**只补空，不覆盖**）。

        来源 = `core/memread/duel_result.py` 的 `Duel.rank`（**开局那一拍**读，
        那时它必然是局前值）。口径与 `backfill_my_rank_after` 完全一致。
        """
        return self._backfill_rank_pair(match_id, rank, rate,
                                       'my_rank_before', 'my_rate_before')

    def backfill_my_rank_after(self, match_id, rank, rate):
        """把**内存读到的局后段位**并进既有行（**只补空，不覆盖**）。

        这是「段位不再依赖 Untapped API」的入库落点：值来自
        `core/memread/rank.py`（采集器收局按 §5.7 两拍法读出的
        `<记录目录>/my_rank_after.json`）。`rank`/`rate` 与
        `my_rank_before`/`my_rate_before` **同一套编号**（1..7 / 1..5）。

        返回动作 dict（`'set'` / `'kept:<旧值>'` / `'unchanged'`）；
        match_id 不存在返回 {}。
        """
        return self._backfill_rank_pair(match_id, rank, rate,
                                       'my_rank_after', 'my_rate_after')

    def _backfill_rank_pair(self, match_id, rank, rate, col_rank, col_rate):
        """段位对的公共实现：**只补空、不覆盖**（两个口共用，别再抄一份）。"""
        if rank is None or rate is None:
            return {}
        with self._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
            if row is None:
                return {}
            row = _row_to_dict(row)
            actions = {}
            sets, args = [], []
            for col, value in ((col_rank, rank), (col_rate, rate)):
                cur = row.get(col)
                if cur is None:
                    sets.append('%s = ?' % col)
                    args.append(value)
                    actions[col] = 'set'
                elif cur != value:
                    actions[col] = 'kept:%s' % cur
                else:
                    actions[col] = 'unchanged'
            if sets:
                conn.execute('UPDATE matches SET %s WHERE match_id = ?'
                             % ', '.join(sets), args + [match_id])
            return actions

    def backfill_peak_rate(self, match_id, before, after, season):
        """巅峰积分三列 —— **只补空、不覆盖**（2026-09-28 审查 B3）。

        积分没有段位那套 `rank_state` 推断链，所以**不需要**「真值覆盖推断」
        的守卫；又因为积分是单值（成对语义的 `_backfill_rank_pair` 复用不了），
        这里按「列清单、None 跳过」的单列口径写。全 None / 行不存在返回 {}；
        返回动作 dict（'set' / 'kept:<旧值>' / 'unchanged'，按列）。
        """
        pairs = (('peak_rate_before', before), ('peak_rate_after', after),
                 ('peak_season', season))
        if all(v is None for _, v in pairs):
            return {}
        with self._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
            if row is None:
                return {}
            row = _row_to_dict(row)
            actions, sets, args = {}, [], []
            for col, value in pairs:
                if value is None:
                    continue
                cur = row.get(col)
                if cur is None:
                    sets.append('%s = ?' % col)
                    args.append(value)
                    actions[col] = 'set'
                elif cur != value:
                    actions[col] = 'kept:%s' % cur
                else:
                    actions[col] = 'unchanged'
            if sets:
                conn.execute('UPDATE matches SET %s WHERE match_id = ?'
                             % ', '.join(sets), args + [match_id])
            return actions

    def set_api_meta(self, match_id, g):
        """把 **API 独有**的字段写到某一行（日志解析拿不到这些）。

        game_mode / 对手名 / 双方段位 / finish_type。全用 COALESCE 写，
        已有值不会被空值覆盖。
        """
        d0 = (g.get('duels') or [{}])[0]
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET'
                '  game_mode       = COALESCE(?, game_mode),'
                '  opp_player_name = COALESCE(?, opp_player_name),'
                '  my_rank_before  = COALESCE(?, my_rank_before),'
                '  my_rate_before  = COALESCE(?, my_rate_before),'
                '  my_rank_after   = COALESCE(?, my_rank_after),'
                '  my_rate_after   = COALESCE(?, my_rate_after),'
                '  opp_rank_before = COALESCE(?, opp_rank_before),'
                '  opp_rate_before = COALESCE(?, opp_rate_before),'
                '  finish_type     = COALESCE(?, finish_type)'
                ' WHERE match_id = ?',
                (g.get('game_mode'), g.get('opponent_player_name'),
                 g.get('friendly_rank_before'), g.get('friendly_rate_before'),
                 g.get('friendly_rank_after'), g.get('friendly_rate_after'),
                 g.get('opponent_rank_before'), g.get('opponent_rate_before'),
                 d0.get('finish_type'), match_id))
            return cur.rowcount

    #: 跨来源判重的窗口。日志的 `Starting Duel` 比 API 的 `match_start`
    #: **早 0~10 秒**（实测），所以只往**前**找一小段。
    #: ⚠️ 窗口不能放宽：实测同一天连着打的两局最短只隔 **43 秒**，
    #: 用 ±180 秒会把两局误判成同一局（补录时真的踩过）。
    API_LOOKBACK = 30       # 日志行最多比 API 早 30 秒（实测最大 9.5 秒）
    API_LOOKAHEAD = 5       # 允许一点点时钟偏差（实测 +0.4 秒）

    def find_match_near(self, started_at, lookback=None, lookahead=None,
                        origin=None):
        """给一个 API 的 `match_start`，找对应的**已有对局行**；找不到返回 None。

        取窗口内**最晚**的一条（日志行总比 API 早，最晚的离它最近）。
        `origin` 可限定只找某一来源（补录时只跟日志行比对）。
        """
        lb = self.API_LOOKBACK if lookback is None else lookback
        la = self.API_LOOKAHEAD if lookahead is None else lookahead
        t = datetime.strptime(started_at[:19], '%Y-%m-%d %H:%M:%S').timestamp()
        lo = datetime.fromtimestamp(t - lb).strftime('%Y-%m-%d %H:%M:%S')
        hi = datetime.fromtimestamp(t + la).strftime('%Y-%m-%d %H:%M:%S.999')
        sql = ('SELECT * FROM matches WHERE started_at BETWEEN ? AND ?')
        args = [lo, hi]
        if origin:
            sql += ' AND origin = ?'
            args.append(origin)
        sql += ' ORDER BY started_at DESC LIMIT 1'
        with self._open() as conn:
            row = conn.execute(sql, args).fetchone()
        return _row_to_dict(row) if row else None

    def sync_api_meta(self, games):
        """用 API 回填所有能对上的行 —— **日志行也会被 enrich**。

        返回 (命中数, 库里仍缺 game_mode 的行数)。对不上的（比如掉线那局）
        保持 NULL —— 它不在 Untapped 记录里。
        """
        hit = 0
        for g in games:
            ts = g.get('match_start')
            if not ts:
                continue
            key = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S.000')
            row = self.find_match_near(key)
            if row is None:
                continue
            self.set_api_meta(row['match_id'], g)
            hit += 1
        with self._open() as conn:
            miss = conn.execute('SELECT COUNT(*) AS n FROM matches'
                                ' WHERE game_mode IS NULL').fetchone()['n']
        return hit, miss

    # ------------------------------------------------ M3：record 并档/回填

    #: record 并档窗口（docs/对局记录格式.md §5.4）。
    #: record 的 first_seen 通常**晚于**日志 started_at（watcher 20 秒轮询、
    #: one-shot 残影可能晚几分钟），不能直接沿用 API 的 -30/+5 单向窗。
    RECORD_LOOKBACK = 30        # 紧窗：started_at 最早比 first_seen 早 30 秒
    RECORD_LOOKAHEAD = 5        # 紧窗：允许一点时钟偏差
    RECORD_WIDE_LOOKBACK = 1800 # 宽窗：legacy/one-shot 往前找 30 分钟
    RECORD_WIDE_GAP = 60        # 候选前两名间隔 < 60 秒 = 歧义放弃
    RECORD_END_WINDOW = 600     # 第三段：结算残影（ended_at 在 first_seen 前 10 分钟内）

    def find_record_pair(self, first_seen):
        """给 record 的 first_seen 时刻，找同局的**既有 log/api 行**。

        三段窗口（命中即返回，都空才返回 None）：
        1. 紧窗 ``started_at ∈ [first_seen-30, first_seen+5]`` —— 对局中就在抓；
        2. 进行中 ``started_at ∈ [first_seen-30min, first_seen-30s]`` 且
           ``ended_at IS NULL 或 ≥ first_seen``，取 started_at 最新一条
           —— first_seen 落在对局中间（watcher 中途启动）；
        3. 结算残影 ``ended_at ∈ [first_seen-10min, first_seen]``，取 ended 最新
           一条 —— one-shot 在结算画面补抓，控制器还活着。
        唯一性：宽窗/残影段要求候选唯一（前两名间隔 < :data:`RECORD_WIDE_GAP`
        → 歧义放弃）；**紧窗不查**（35 秒窗、实测两局最短隔 43 秒，取最新一条即可）。

        返回 ``(row, stage)`` 或 ``(None, reason)``。
        """
        if not first_seen:
            # 坏目录（目录名不合规 + manifest 缺 saved_at）没有锚点：
            # 返回 None 让调用方走新建行分支，不许抛异常拖垮整批导入
            return None, 'no-anchor'
        t = datetime.strptime(first_seen[:19], '%Y-%m-%d %H:%M:%S').timestamp()

        def _q(lo_ts, hi_ts, extra='', args=(), order='started_at DESC'):
            lo = datetime.fromtimestamp(lo_ts).strftime('%Y-%m-%d %H:%M:%S')
            hi = datetime.fromtimestamp(hi_ts).strftime('%Y-%m-%d %H:%M:%S.999')
            sql = ('SELECT * FROM matches WHERE started_at BETWEEN ? AND ?'
                   " AND origin IN ('log', 'api')" + extra + ' ORDER BY ' + order)
            with self._open() as conn:
                return [_row_to_dict(r) for r in
                        conn.execute(sql, [lo, hi] + list(args)).fetchall()]

        # 1. 紧窗
        rows = _q(t - self.RECORD_LOOKBACK, t + self.RECORD_LOOKAHEAD)
        if rows:
            return rows[0], 'tight'
        # 2. 进行中（first_seen 落在对局区间内）
        rows = _q(t - self.RECORD_WIDE_LOOKBACK, t - self.RECORD_LOOKBACK,
                  extra=' AND (ended_at IS NULL OR ended_at >= ?)',
                  args=(first_seen + '.999',))
        rows = [r for r in rows
                if not r.get('ended_at') or _ts_of(r, 'ended_at') >= t]
        if rows:
            if len(rows) >= 2 and (_ts_of(rows[0]) - _ts_of(rows[1])
                                   < self.RECORD_WIDE_GAP):
                return None, 'ambiguous-in-progress'
            return rows[0], 'in-progress'
        # 3. 结算残影（对局已结束、控制器还活着）
        #    ⚠️ 窗口锚在 ended_at（不是 started_at）—— one-shot 是对局结束
        #    几分钟后才抓的，started_at 早在 10 分钟窗口之外了。
        lo_end = datetime.fromtimestamp(
            t - self.RECORD_END_WINDOW).strftime('%Y-%m-%d %H:%M:%S')
        rows = _q(t - self.RECORD_WIDE_LOOKBACK, t - self.RECORD_LOOKBACK,
                  extra=(' AND ended_at IS NOT NULL AND ended_at >= ?'
                         ' AND ended_at <= ?'),
                  args=(lo_end, first_seen + '.999'), order='ended_at DESC')
        if rows:
            if len(rows) >= 2 and (_ts_of(rows[0], 'ended_at')
                                   - _ts_of(rows[1], 'ended_at')
                                   < self.RECORD_WIDE_GAP):
                return None, 'ambiguous-ended'
            return rows[0], 'ended-residue'
        return None, 'no-candidate'

    def apply_record_meta(self, match_id, record_dir=None, winbits=None,
                          result=None, result_src=None, result_note=None,
                          my_traps=None, opp_traps=None):
        """把 record 侧带来的元数据并进**既有行**（origin=log/api）。

        * ``record_dir``：旧值为空才填（不覆盖）；
        * ``winbits``：旧值为空才填（重跑幂等）；
        * ``result``：**winbit 优先、允许覆盖**（用户口径：winbit 是唯一判据，
          老行里文案推的 result 若相反不能一直留错值）；与旧值冲突时把旧值
          记进 ``result_note``，``result_src`` 跟着换成 winbit；
          ⚠️ **例外（2026-09-18 用户口径）**：旧行 ``result_src='manual'`` 时
          ``result`` / ``result_src`` **一个字节都不动**，只在自动值与人工值
          不同时往 ``result_note`` 追加一行冲突备注 —— 人工修复优先；
        * ``result_note``（收局文案）：**只记录不判断** —— 旧值为空才填。
        * ``my_traps`` / ``opp_traps``（手坑，2026-10-16）：**旧值为空才填**
          （重跑幂等；`[]` = 确实没用过，也算有值，不许被 NULL 冲掉）。

        返回动作 dict（'record_dir'/'winbits'/'result'/'result_note' -> 动作描述）；
        match_id 不存在返回 {}。
        """
        with self._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
            if row is None:
                return {}
            row = _row_to_dict(row)
            actions = {}
            sets, args = [], []

            def _set(col, value):
                sets.append('%s = ?' % col)
                args.append(value)

            if record_dir:
                cur = row.get('record_dir')
                if cur is None:
                    _set('record_dir', record_dir)
                    actions['record_dir'] = 'set'
                elif cur != record_dir:
                    actions['record_dir'] = 'kept:%s' % cur
                else:
                    actions['record_dir'] = 'unchanged'
            if winbits:
                cur = _json_load(row.get('winbits'))
                if cur is None:
                    _set('winbits', _json_dump(winbits))
                    actions['winbits'] = 'set'
                elif cur != list(winbits):
                    actions['winbits'] = 'kept'
                else:
                    actions['winbits'] = 'unchanged'

            for col, val in (('my_traps', my_traps),
                             ('opp_traps', opp_traps)):
                if val is None:
                    continue
                if row.get(col) is None:
                    _set(col, _json_dump(val))
                    actions[col] = 'set'
                else:
                    actions[col] = 'kept'

            conflict_note = None
            #: 判据 = **人工 + 有值**：`result_src='manual'` 但 `result` 是空的行
            #: （实测库里就有一行，10-44 那局）不许被锁死 —— 空值没有「人工结论」
            #: 可保护，锁了它反而**永远填不上**。
            manual = (row.get('result_src') == MANUAL_RESULT_SRC
                      and row.get('result') is not None)
            if result and manual:
                old = row.get('result')
                actions['result'] = 'kept:manual'
                if old != result:
                    # 只在**值不同**时留痕（自动解析可能整批重跑，备注必须幂等）
                    conflict_note = ('result冲突:人工=%s 自动=%s(%s)'
                                     % (old, result, result_src or 'winbit'))
            elif result:
                old = row.get('result')
                if old is None:
                    _set('result', result)
                    _set('result_src', result_src or 'winbit')
                    actions['result'] = 'filled'
                elif old not in DONE_RESULTS:      # '(未结束)' 等
                    _set('result', result)
                    _set('result_src', result_src or 'winbit')
                    actions['result'] = 'replaced:%s' % old
                elif old != result:
                    conflict_note = ('result冲突:旧=%s(src=%s)'
                                     % (old, row.get('result_src')))
                    _set('result', result)
                    _set('result_src', result_src or 'winbit')
                    actions['result'] = 'conflict-noted:%s->%s' % (old, result)
                else:
                    actions['result'] = 'unchanged'
                    # 值一致也把来源戳成 winbit（记录「winbit 已确认过这局」）
                    if row.get('result_src') != (result_src or 'winbit'):
                        _set('result_src', result_src or 'winbit')

            # result_note：文案只记录不判断 —— 旧值为空才填；冲突备注要追加
            final_note = row.get('result_note')
            if result_note and final_note is None:
                final_note = result_note
                actions['result_note'] = 'set'
            elif result_note and result_note != final_note:
                actions['result_note'] = 'kept'
            if conflict_note and conflict_note not in (final_note or ''):
                final_note = (final_note + '；' + conflict_note
                              if final_note else conflict_note)
                actions['result_note'] = 'conflict-noted'
            if final_note is not None and final_note != row.get('result_note'):
                _set('result_note', final_note)

            if not sets:
                return actions
            args.append(match_id)
            conn.execute('UPDATE matches SET %s WHERE match_id = ?'
                         % ', '.join(sets), args)
            return actions

    def set_hand_traps_by_record(self, record_dir, my_traps, opp_traps):
        """按 `record_dir` 写手坑两列（回填工具用）。

        * 两列都传 ``None`` = 这局判不出（seat 缺失 / 无 ShowTurn）→ **不动**，返回 0；
        * 传 ``[]`` = 确实没用过（**有值**，照写）；
        * 值按 JSON 文本存（同 upsert 的 `_json_dump`）。

        返回更新的行数。
        """
        if my_traps is None and opp_traps is None:
            return 0
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET my_traps = ?, opp_traps = ?'
                ' WHERE record_dir = ?',
                (_json_dump(my_traps), _json_dump(opp_traps), record_dir))
            return cur.rowcount

    def set_my_deck_for_record(self, match_id, name, src='manual'):
        """行级修正某局显示的**我方卡组**（09-28 用户需求：点行内卡图弹选择器改）。

        只改冗余快照列（`my_deck_name`/`my_deck_name_src`）—— 该局的
        `deck_source`/`my_deck_id` 不动（那是采集证据）。返回更新的行数。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET my_deck_name = ?, my_deck_name_src = ?'
                ' WHERE match_id = ?', (name, src, match_id))
            return cur.rowcount

    def set_result(self, match_id, result, src='manual'):
        """手动补填胜负（--set-result）。返回更新的行数。"""
        assert result in DONE_RESULTS, 'result 只认 %s' % (DONE_RESULTS,)
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET result = ?, result_src = ?'
                ' WHERE match_id = ?', (result, src, match_id))
            return cur.rowcount

    def set_account_for_record(self, record_dir, name=None, pcode=None):
        """收局**盖账号章**（2026-10-03 多账号）：**只补空、不覆盖**。

        采集器收局读内存 profile（``s_data["User"]["profile"]`` 的
        ``name`` / ``pcode``）—— 同一条链早就在读段位，顺手把账号带上。
        每局反复盖是幂等的（WHERE account_name IS NULL）；旧局/采集前
        的局保持 NULL = 「未分账号」。返回更新的行数。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET account_name = ?, account_pcode = ?'
                ' WHERE record_dir = ? AND account_name IS NULL',
                (name, pcode, record_dir))
            return cur.rowcount

    def account_options(self):
        """账号清单（切换器用）-> ``[(账号名, 局数, 最近一局时间), ...]``。

        按 ``account_name`` 聚合（NULL = 「未分账号」的历史局，**不列**）；
        局数不限口径（切换器只是个目录，数字给总局数）。查询失败返回 []。
        """
        try:
            with self._open() as conn:
                rows = conn.execute(
                    'SELECT account_name AS name, COUNT(*) AS n,'
                    ' MAX(started_at) AS last_at'
                    ' FROM matches WHERE account_name IS NOT NULL'
                    ' GROUP BY account_name ORDER BY last_at DESC'
                ).fetchall()
            return [(r['name'], int(r['n']), r['last_at']) for r in rows]
        except Exception:
            return []

    def backfill_pending(self) -> int:
        """事后回填还没跟上的局数（**探针用**，2026-09-18 八轮）。

        段位探测要等结算写进 ClientWork（最长 ~92s）才落盘+回填、winbits 也是
        收局后补读 —— 这些 UPDATE 不动 rowid/n_total/unmarked，探针
        （`ui_state.probe` 的 count/max_rowid/unmarked/session）感知不到，
        界面会一直挂着空段位/无结果，要手动重开窗口才对。
        把「还欠多少回填」本身做成探针键的一部分：**回填完成 = 一次可见变化**。
        """
        with self._open() as conn:
            row = conn.execute(
                'SELECT (SELECT COUNT(*) FROM matches'
                ' WHERE result IS NOT NULL AND my_rank_after IS NULL'
                ' AND (game_mode IS NULL OR game_mode IN (3, 19)))'
                ' + (SELECT COUNT(*) FROM matches WHERE result IS NULL)'
                # ★ 第三项（2026-09-19 实测）：**只补段位**的回填（那行 result 还是
                #   NULL、行数也不变）会让前两项纹丝不动 -> 界面永远不刷，
                #   用户看到的一直是补段位之前的样子。天梯/巅峰行「还没有段位」的
                #   条数也纳入：回填掉一个就可见。
                ' + (SELECT COUNT(*) FROM matches'
                ' WHERE my_rank_after IS NULL'
                ' AND game_mode IN (3, 19))'
            ).fetchone()
        return int(row[0] or 0)

    def infer_rank_for_record(self, record_dir, before, after):
        """段位**推断值**写本局行（只补空；2026-09-18 晚：胜负实时推断）。

        推断链：胜负收局即知 -> 段位真值要等结算写进档案（探测 30s）——
        推断值让本局行的段位**同帧出现**；探测落盘后用真值复核
        （`repair_rank_for_record`，不一致以真值覆盖）。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET'
                ' my_rank_before = COALESCE(my_rank_before, ?),'
                ' my_rate_before = COALESCE(my_rate_before, ?),'
                ' my_rank_after = COALESCE(my_rank_after, ?),'
                ' my_rate_after = COALESCE(my_rate_after, ?)'
                ' WHERE record_dir = ?',
                (before and before[0], before and before[1],
                 after and after[0], after and after[1], record_dir))
            return cur.rowcount

    def infer_rank_for_match(self, match_id, before, after):
        """段位**推断值**按 match_id 写行（只补空；给界面的手填链用）。

        为什么不用 :meth:`infer_rank_for_record`：手填的局可能**没有 record 目录**
        （2026-09-19 的重建行 `manual:…`），只能按 match_id 找。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET'
                ' my_rank_before = COALESCE(my_rank_before, ?),'
                ' my_rate_before = COALESCE(my_rate_before, ?),'
                ' my_rank_after = COALESCE(my_rank_after, ?),'
                ' my_rate_after = COALESCE(my_rate_after, ?)'
                ' WHERE match_id = ?',
                (before and before[0], before and before[1],
                 after and after[0], after and after[1], match_id))
            return cur.rowcount

    def repair_rank_for_record(self, record_dir, rank, rate):
        """段位**真值复核**（探测落盘后）：覆盖本局行的局后段位（推断可能失准）。"""
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET my_rank_after = ?, my_rate_after = ?'
                ' WHERE record_dir = ?', (rank, rate, record_dir))
            return cur.rowcount

    def repair_my_deck_by_record(self, record_dir, name, src='revealed'):
        """卡组确认后的**本局修正**（2026-09-18 九轮审查 P1-1）。

        `matches.record_dir` 存的就是记录目录名（如 ``record_20260918_194446``）。
        为什么不用「补跑 import」：import 的兜底只填 NULL（且新鲜度闸门会挡住
        重跑），改不到已入库的行 —— 审查实测确认这条链路是断的。
        只改 `my_deck_name` / `my_deck_name_src` 两列；返回受影响行数。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET my_deck_name = ?, my_deck_name_src = ?'
                ' WHERE record_dir = ?', (name, src, record_dir))
            return cur.rowcount

    def repair_match_result(self, match_id, result=_KEEP, result_src=_KEEP,
                            result_note=_KEEP, winbits=_KEEP):
        """事故修复：**显式覆盖**结算相关列（含置 NULL），不做 COALESCE 保护。

        ⚠️ 只给「已确认的脏数据」用 —— 正常写入一律走 :meth:`upsert_match` /
        :meth:`apply_record_meta`（那两个的语义是「只补空、不覆盖」，救不了错值）。
        首次用途（2026-09-16 EM 误配事故）：骰子拉力连打时某局认领到上一局的
        end_message，``winbits`` 被串成上一局的 ``[0, 1]``（该局胜负位实际从未
        可读）、``result_note`` 也是上一局文案。改完必须人工核对。

        不传的列保持原值；显式传 ``None`` = 置 NULL。返回更新的行数。
        """
        if result is not _KEEP:
            assert result is None or result in DONE_RESULTS, \
                'result 只认 %s' % (DONE_RESULTS,)
        cols = (('result', result), ('result_src', result_src),
                ('result_note', result_note), ('winbits', winbits))
        sets, args = [], []
        for col, val in cols:
            if val is _KEEP:
                continue
            if col == 'winbits' and val is not None:
                val = _json_dump(val)
            sets.append('%s = ?' % col)
            args.append(val)
        if not sets:
            return 0
        args.append(match_id)
        with self._open() as conn:
            cur = conn.execute('UPDATE matches SET %s WHERE match_id = ?'
                               % ', '.join(sets), args)
            return cur.rowcount

    def delete_match(self, match_id=None, started_at=None):
        """删掉对局，返回删除条数。

        传 `match_id` 或 `started_at` 任一即可；**两者都给时命中任一个都删** ——
        清理误录的观战记录时，同一局的 match_id 可能已因日志轮转漂移过
        （见 `upsert_match` 的判重规则），只按 id 删会漏。
        """
        clauses, args = [], []
        if match_id:
            clauses.append('match_id = ?')
            args.append(match_id)
        if started_at:
            clauses.append('started_at = ?')
            args.append(started_at)
        if not clauses:
            return 0
        with self._open() as conn:
            cur = conn.execute(
                'DELETE FROM matches WHERE ' + ' OR '.join(clauses), args)
            return cur.rowcount

    def upsert_my_deck(self, deck_id, name, source='manual'):
        """登记/更新我的卡组。name 总是以最新调用为准。"""
        with self._open() as conn:
            conn.execute(
                'INSERT INTO my_decks (deck_id, name, source, updated_at)'
                ' VALUES (?, ?, ?, ?)'
                ' ON CONFLICT(deck_id) DO UPDATE SET'
                '   name = excluded.name,'
                '   source = excluded.source,'
                '   updated_at = excluded.updated_at',
                (deck_id, name, source, int(time.time())))

    def upsert_opp_deck(self, name, aliases=None):
        """登记对手卡组，返回 opp_deck_id。同名（UNIQUE）不新建行。"""
        with self._open() as conn:
            cur = conn.execute('SELECT opp_deck_id FROM opp_decks WHERE name = ?',
                               (name,))
            got = cur.fetchone()
            if got is not None:
                if aliases is not None:
                    conn.execute('UPDATE opp_decks SET aliases = ?'
                                 ' WHERE opp_deck_id = ?',
                                 (_json_dump(aliases), got['opp_deck_id']))
                return got['opp_deck_id']
            cur = conn.execute(
                'INSERT INTO opp_decks (name, aliases, use_count, created_at)'
                ' VALUES (?, ?, 0, ?)',
                (name, _json_dump(aliases), int(time.time())))
            return cur.lastrowid

    def mark_opp_deck(self, match_id, opp_deck_id=None, name=None, note=None):
        """人工标注一局对手卡组（M5 会用到；M2 先提供最小能力）。"""
        with self._open() as conn:
            conn.execute(
                'UPDATE matches SET'
                '  opp_deck_id   = COALESCE(?, opp_deck_id),'
                '  opp_deck_name = COALESCE(?, opp_deck_name),'
                '  opp_deck_note = COALESCE(?, opp_deck_note)'
                ' WHERE match_id = ?',
                (opp_deck_id, name, note, match_id))

    def clear_opp_deck(self, match_id):
        """撤销一局的标注（标错了要能改回去）。

        ``mark_opp_deck`` 走的是 COALESCE，传 None = 「这项不改」，
        所以「清空」只能靠这个显式方法。返回是否真的改到了行。
        """
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE matches SET opp_deck_id = NULL, opp_deck_name = NULL,'
                ' opp_deck_note = NULL WHERE match_id = ?', (match_id,))
            return cur.rowcount > 0

    def list_opp_decks(self, limit=None):
        """我自己的对手卡组表 + 「打过几局」（从已标注的对局现算）。

        返回 ``[(opp_deck_id, name, aliases, n)]``，按 n 降序、name 升序。

        计数**现算**，不读 ``opp_decks.use_count`` —— 撤销标注、重解析都不会
        让它漂；``use_count`` 列是历史遗留，谁也别依赖。
        """
        sql = (
            "SELECT o.opp_deck_id AS opp_deck_id, o.name AS name,"
            " o.aliases AS aliases, COUNT(m.match_id) AS n"
            " FROM opp_decks o"
            " LEFT JOIN matches m ON m.opp_deck_id = o.opp_deck_id"
            " AND m.result IN (%s) AND %s"
            " GROUP BY o.opp_deck_id, o.name, o.aliases"
            " ORDER BY n DESC, o.name ASC"
            % (','.join("'%s'" % r for r in DONE_RESULTS), counted_clause()))
        args = []
        if limit is not None:
            sql += ' LIMIT ?'
            args.append(int(limit))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [(r['opp_deck_id'], r['name'], _json_load(r['aliases']), r['n'])
                for r in rows]

    # -------------------------------------------------------- 读取
    def get_match(self, match_id):
        """按 match_id 取一条，取不到返回 None。"""
        with self._open() as conn:
            row = conn.execute('SELECT * FROM matches WHERE match_id = ?',
                               (match_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_matches(self, since=None, limit=100):
        """按开始时间**升序**列出对局（与 v1 的 merged 顺序一致）。

        ``limit=None`` 表示不限条数。
        """
        sql = 'SELECT * FROM matches'
        args = []
        if since:
            sql += ' WHERE started_at >= ?'
            args.append(since)
        sql += ' ORDER BY started_at ASC'
        if limit is not None:
            sql += ' LIMIT ?'
            args.append(int(limit))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_unmarked_matches(self, limit=50):
        """待标注队列：``opp_deck_id IS NULL``，按时间**倒序**（最新的在最前）。

        判据就是 :func:`counted_clause()`：**有 `game_mode` 的必须是 3/19**，
        没有 `game_mode` 的只有**掉线局**（`mode='pvp' AND ended_at IS NULL`）才算。
        所以**队列 ≠ 全库**：单人 / 观战回放 / API 查不到又没掉线的局都不在里面。
        要指任意一局（含回放）用：``tools/mark_opponent.py --guess <match_id 前缀|last>``。
        """
        with self._open() as conn:
            rows = conn.execute(
                'SELECT * FROM matches WHERE opp_deck_id IS NULL'
                ' AND ' + counted_clause() +
                ' ORDER BY started_at DESC LIMIT ?', (int(limit),)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_recent_matches(self, limit=10, account=None):
        """**最近** `limit` 场，按时间**倒序**（最新在最前），**不限模式**。

        ⚠️ 三个易混点的区别，别用错：

        ======================  ==========  ==========================  ==========
        方法                     排序       模式范围                     标注过滤
        ======================  ==========  ==========================  ==========
        `list_matches`          **ASC**     全部                        无
        `get_unmarked_matches`  DESC        **口径内**（天梯+巅峰+掉线）  未标注
        `get_recent_matches`    DESC        全部（**含活动/回放**）        无
        ======================  ==========  ==========================  ==========

        「最近几场」是**显示**需求（刚打完一局就该看见它，哪怕是活动局），
        **不套统计口径** —— 所以**别拿这个方法的结果去算胜率**，
        算统计一律走 :func:`counted_clause`。
        """
        sql = 'SELECT * FROM matches'
        args = []
        if account:
            sql += ' WHERE account_name = ?'
            args.append(account)
        sql += ' ORDER BY started_at DESC LIMIT ?'
        args.append(int(limit))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_marked_matches(self, limit=20):
        """已标注队列：``opp_deck_id IS NOT NULL``，按时间**倒序**。

        和 `get_unmarked_matches` 对称 —— 撤销标注（标错了）时用得上，
        因为标完的局就不在待标注队列里了，序号对不上。
        """
        with self._open() as conn:
            rows = conn.execute(
                'SELECT * FROM matches WHERE opp_deck_id IS NOT NULL'
                ' AND ' + counted_clause() +
                ' ORDER BY started_at DESC LIMIT ?', (int(limit),)).fetchall()
        return [_row_to_dict(r) for r in rows]
    # ---------------- Meta 卡组清单（M5 的数据源） ----------------

    #: tier 的排序权重（Tier1 最强，Trending 最后；未知排末尾）
    _TIER_ORDER = ("CASE tier WHEN 'Tier1' THEN 1 WHEN 'Tier2' THEN 2 "
                   "WHEN 'Tier3' THEN 3 WHEN 'Trending' THEN 4 ELSE 9 END")

    def replace_meta_decks(self, rows, prune=True):
        """用一份新抓的 meta 清单覆盖旧数据（按月刷新）。

        - 按 ``(name, kind)`` upsert，**保持 meta_deck_id 稳定**
        - ``prune=True`` 删掉这次没抓到的（已掉出榜单的）
        - 整体一个事务

        返回 ``(inserted, updated, pruned)``。
        """
        now = int(time.time())
        ins = upd = 0
        with self._open() as conn:
            seen = set()
            for r in rows:
                name, kind = r['name'], r.get('kind') or 'deck_type'
                key = (name, kind)
                seen.add(key)
                cur = conn.execute(
                    'SELECT meta_deck_id FROM meta_decks WHERE name = ? AND kind = ?',
                    key)
                got = cur.fetchone()
                vals = (r.get('name_cn'), r.get('name_display'), r.get('name_ja'),
                        _json_dump(r.get('aliases')), r.get('hidden'),
                        r.get('tier'), r.get('power'), r.get('popularity'),
                        r.get('slug'), r.get('img_url'), r.get('img_file'),
                        r.get('source') or 'masterduelmeta', now)
                if got is None:
                    conn.execute(
                        'INSERT INTO meta_decks (name, kind, name_cn, name_display,'
                        ' name_ja, aliases, hidden, tier, power, popularity, slug,'
                        ' img_url, img_file, source, fetched_at)'
                        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (name, kind) + vals)
                    ins += 1
                else:
                    # ⚠️ 人工字段一律 COALESCE：脚本传 None = 「这项没意见，保留旧值」。
                    #    这样每月重抓不会冲掉手改的中文名 / 改名 / 隐藏标记。
                    #    脚本要「清空」某项时传空串或 0（非 NULL），COALESCE 就会生效。
                    conn.execute(
                        'UPDATE meta_decks SET name_cn = COALESCE(?, name_cn),'
                        ' name_display = COALESCE(?, name_display),'
                        ' name_ja = COALESCE(?, name_ja),'
                        ' aliases = COALESCE(?, aliases),'
                        ' hidden = COALESCE(?, hidden),'
                        ' tier = ?, power = ?, popularity = ?, slug = ?, img_url = ?,'
                        ' img_file = COALESCE(?, img_file), source = ?, fetched_at = ?'
                        ' WHERE meta_deck_id = ?',
                        vals + (got['meta_deck_id'],))
                    upd += 1
            pruned = 0
            if prune:
                old = conn.execute(
                    'SELECT meta_deck_id, name, kind, source FROM meta_decks').fetchall()
                # ⚠️ source='custom' 是**我们自己建的卡组**（站内没有），
                #    不该因为「这次抓取里没有」就被清掉
                stale = [o['meta_deck_id'] for o in old
                         if (o['name'], o['kind']) not in seen
                         and (o['source'] or '') != SOURCE_CUSTOM]
                for mid in stale:
                    conn.execute('DELETE FROM meta_decks WHERE meta_deck_id = ?', (mid,))
                pruned = len(stale)
        return ins, upd, pruned

    def list_meta_decks(self, tiers=None, kinds=None, include_hidden=False):
        """按 tier -> 强度降序返回 meta 清单（选择框用）。

        ``tiers`` / ``kinds`` 传 None 表示不过滤；传列表则只取其中的值。
        ``include_hidden=False``（默认）不返回被手工隐藏的条目 ——
        选择框用默认值，管理/核对时才传 True。
        """
        sql = 'SELECT * FROM meta_decks'
        where, args = [], []
        if not include_hidden:
            where.append('COALESCE(hidden, 0) = 0')
        if tiers:
            where.append('tier IN (%s)' % ','.join('?' * len(tiers)))
            args.extend(tiers)
        if kinds:
            where.append('kind IN (%s)' % ','.join('?' * len(kinds)))
            args.extend(kinds)
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY %s, power DESC, name' % self._TIER_ORDER
        with self._open() as conn:
            return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]

    def delete_meta_deck(self, name, kind=None):
        """删掉一条 meta 卡组（只该用于自定义条目；站内条目请用 hidden）。"""
        with self._open() as conn:
            if kind:
                cur = conn.execute(
                    'DELETE FROM meta_decks WHERE name = ? AND kind = ?', (name, kind))
            else:
                cur = conn.execute('DELETE FROM meta_decks WHERE name = ?', (name,))
            return cur.rowcount

    def set_meta_deck_override(self, name, name_cn=None, name_display=None,
                               name_ja=None, aliases=None, hidden=None,
                               kind=None, img_file=None):
        """人工覆盖某卡组的显示字段。**只改传进来的项**（None = 不动这一项）。

        想「清空」某一项就传空串 / 0（非 None）。
        返回受影响行数。
        """
        sets, args = [], []
        if name_cn is not None:
            sets.append('name_cn = ?')
            args.append(name_cn)
        if name_display is not None:
            sets.append('name_display = ?')
            args.append(name_display)
        if name_ja is not None:
            sets.append('name_ja = ?')
            args.append(name_ja)
        if aliases is not None:
            sets.append('aliases = ?')
            args.append(_json_dump(list(aliases)))
        if hidden is not None:
            sets.append('hidden = ?')
            args.append(1 if hidden else 0)
        if img_file is not None:
            sets.append('img_file = ?')
            args.append(img_file)
        if not sets:
            return 0
        sql = 'UPDATE meta_decks SET ' + ', '.join(sets) + ' WHERE name = ?'
        args.append(name)
        if kind:
            sql += ' AND kind = ?'
            args.append(kind)
        with self._open() as conn:
            return conn.execute(sql, args).rowcount

    def fix_legacy_tier(self, name, tier):
        """把**历史遗留**的分组值改成 `tier`。

        只动那些不在 `VALID_TIERS` 里的值（例如旧数据里的「其它」「Tier 2」）——
        规则是「站内有分组的以站内为准，覆盖表说了不算」，所以合法值一律不碰。
        分组值本身非法时返回 0。返回受影响行数。
        """
        if tier not in VALID_TIERS:
            return 0
        marks = ','.join('?' * len(VALID_TIERS))
        with self._open() as conn:
            cur = conn.execute(
                'UPDATE meta_decks SET tier = ?'
                ' WHERE name = ? AND (tier IS NULL OR tier = ""'
                '                     OR tier NOT IN (%s))' % marks,
                (tier, name) + VALID_TIERS)
            return cur.rowcount

    def set_meta_deck_cn(self, name, name_cn, kind=None):
        """人工设置某卡组的中文名。返回受影响行数。"""
        with self._open() as conn:
            if kind:
                cur = conn.execute(
                    'UPDATE meta_decks SET name_cn = ? WHERE name = ? AND kind = ?',
                    (name_cn, name, kind))
            else:
                cur = conn.execute(
                    'UPDATE meta_decks SET name_cn = ? WHERE name = ?',
                    (name_cn, name))
            return cur.rowcount

    def meta_deck_display_map(self, include_hidden=True):
        """站内名 → **显示名**（``中文名 > 显示英文名 > 站内名``）。

        参照库/候选都是按**站内名**索引的（`Ryzeal Mitsurugi`），但给人看要用覆盖表里的
        中文名（`雷火剑`）—— CLI 与 M7 UI 共用这一个口径（UI 不该自己拼一套）。
        """
        return {r['name']: (r.get('name_cn') or r.get('name_display') or r['name'])
                for r in self.list_meta_decks(include_hidden=include_hidden)}

    def search_meta_decks(self, query, limit=20, include_hidden=False):
        """模糊搜索卡组 —— M5 选择框用。

        匹配 ``name`` / ``name_display`` / ``name_cn`` / ``name_ja`` / ``aliases``
        五个字段（见 :mod:`core.deck_match`），**任何一种叫法、任何语言都能命中同一条**
        （中文、英文、日文都可以直接敲）。

        排序：匹配等级（越小越靠前）优先，同级保持
        「tier -> 强度」的原有次序（list 有序 + 稳定排序）。
        """
        from core.deck_match import best_score
        hits = []
        for r in self.list_meta_decks(include_hidden=include_hidden):
            sc = best_score(query, [r.get('name'), r.get('name_display'),
                                    r.get('name_cn'), r.get('name_ja')]
                            + list(r.get('aliases') or []))
            if sc is not None:
                hits.append((sc, r))
        hits.sort(key=lambda x: x[0])
        return [r for _, r in hits][:limit]

    def get_meta_deck(self, name, kind=None):
        """按名字取一条 meta 卡组（kind 省略时优先 deck_type）。"""
        with self._open() as conn:
            if kind:
                row = conn.execute(
                    'SELECT * FROM meta_decks WHERE name = ? AND kind = ?',
                    (name, kind)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM meta_decks WHERE name = ?"
                    " ORDER BY (kind = 'deck_type') DESC LIMIT 1", (name,)).fetchone()
        return _row_to_dict(row) if row else None

    def count_meta_decks(self, include_hidden=True):
        """meta 清单条数。默认含隐藏项（对账用）；选择框场景传 False。"""
        sql = 'SELECT COUNT(*) AS n FROM meta_decks'
        if not include_hidden:
            sql += ' WHERE COALESCE(hidden, 0) = 0'
        with self._open() as conn:
            return conn.execute(sql).fetchone()['n']

    def count(self):
        """对局总条数（含未结束）。"""
        with self._open() as conn:
            return conn.execute('SELECT COUNT(*) AS n FROM matches').fetchone()['n']

    # ---------------- 禁限卡变更史（masterduelmeta 公开 API） ----------------

    def replace_banlist_current(self, rows):
        """整表覆盖「当前禁限状态」（站内 `banStatus`，按月手动跑）。

        ``rows`` 每条：``card_en`` / ``status``。返回 ``(写入, 清掉)``。
        """
        now = int(time.time())
        seen = set()
        with self._open() as conn:
            for r in rows:
                name = (r.get('card_en') or '').strip()
                status = (r.get('status') or '').strip()
                if not name or not status:
                    continue
                seen.add(name)
                conn.execute(
                    'INSERT INTO banlist_current (card_en, status, fetched_at)'
                    ' VALUES (?, ?, ?)'
                    ' ON CONFLICT(card_en) DO UPDATE SET status = ?,'
                    ' fetched_at = ?',
                    (name, status, now, status, now))
            stale = 0
            if seen:
                q = ','.join('?' * len(seen))
                cur = conn.execute(
                    'DELETE FROM banlist_current WHERE card_en NOT IN (%s)' % q,
                    sorted(seen))
                stale = cur.rowcount
        return len(seen), stale

    def banlist_current_status(self):
        """现在的禁限状态（**站内 `banStatus` 原样**）。

        返回 ``[(卡英文名, 状态)]``，按 禁止 -> 限制 -> 准限制 排序。
        表空时**回落到变更史累计**（老库没跑过新版抓取时仍能用，别给空表）。
        """
        with self._open() as conn:
            rows = conn.execute(
                'SELECT card_en, status FROM banlist_current').fetchall()
        if not rows:
            return [(n, s) for n, s, _e in self.banlist_current()]
        order = {'Forbidden': 0, 'Limited 1': 1, 'Limited 2': 2}
        out = [(r['card_en'], r['status']) for r in rows
               if r['card_en'] and r['status']]
        out.sort(key=lambda x: (order.get(x[1], 9), x[0]))
        return out

    def replace_banlist_changes(self, rows):
        """整表覆盖禁限变更史（按月手动跑）。返回 ``(写入, 清掉)``。

        ``rows`` 每条：``announce_id / card_mdm_id / card_en / from_status /
        to_status / effective / announced / url``。
        """
        now = int(time.time())
        seen = set()
        with self._open() as conn:
            for r in rows:
                key = (r['announce_id'], r['card_mdm_id'])
                seen.add(key)
                conn.execute(
                    'INSERT INTO banlist_changes (announce_id, card_mdm_id, card_en,'
                    ' from_status, to_status, effective, announced, url, fetched_at)'
                    ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
                    ' ON CONFLICT(announce_id, card_mdm_id) DO UPDATE SET'
                    '   card_en = excluded.card_en,'
                    '   from_status = excluded.from_status,'
                    '   to_status = excluded.to_status,'
                    '   effective = excluded.effective,'
                    '   announced = excluded.announced,'
                    '   url = excluded.url,'
                    '   fetched_at = excluded.fetched_at',
                    (r['announce_id'], r['card_mdm_id'], r.get('card_en'),
                     r.get('from_status'), r.get('to_status'), r.get('effective'),
                     r.get('announced'), r.get('url'), now))
            old = conn.execute('SELECT announce_id, card_mdm_id'
                               ' FROM banlist_changes').fetchall()
            stale = [(o['announce_id'], o['card_mdm_id']) for o in old
                     if (o['announce_id'], o['card_mdm_id']) not in seen]
            for key in stale:
                conn.execute('DELETE FROM banlist_changes'
                             ' WHERE announce_id = ? AND card_mdm_id = ?', key)
        return len(rows), len(stale)

    def list_banlist_changes(self, limit=None, since=None):
        """按**生效时间倒序**列出变更（最近的在最前）。"""
        sql = ('SELECT * FROM banlist_changes')
        args = []
        if since:
            sql += ' WHERE COALESCE(effective, announced) >= ?'
            args.append(since)
        sql += ' ORDER BY COALESCE(effective, announced) DESC, card_en ASC'
        if limit is not None:
            sql += ' LIMIT ?'
            args.append(int(limit))
        with self._open() as conn:
            return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]

    def banlist_current(self):
        """把变更史按时间**累计**出「现在的禁限状态」。

        返回 ``[(卡英文名, 状态, 生效时间)]``；**只含仍然受限的卡**
        （`to_status` 是 NULL 的变更 = 解除限制，那类卡会从表里消失）。
        """
        with self._open() as conn:
            rows = conn.execute(
                'SELECT * FROM banlist_changes'
                ' ORDER BY COALESCE(effective, announced) ASC, announced ASC'
            ).fetchall()
        state = {}
        for r in rows:
            key = r['card_en'] or r['card_mdm_id']
            if r['to_status']:
                state[key] = (r['to_status'], r['effective'] or r['announced'])
            else:
                state.pop(key, None)          # 解除限制
        order = {'Forbidden': 0, 'Limited 1': 1, 'Limited 2': 2, 'Limited 3': 3}
        out = [(name, st, eff) for name, (st, eff) in state.items()]
        out.sort(key=lambda x: (order.get(x[1], 9), x[0]))
        return out

    def get_cross_stats(self, since=None, until=None, went_first=None,
                        mode_group=None):
        """我的卡组 × 对手卡组 的交叉战绩 —— **本项目的立项理由**。

        只算统计口径内的已结束对局（`game_mode` 3/19 那套规则）。
        返回 ``[(我的卡组, 对手卡组, 场次, 胜, 负, 平)]``，按场次降序。
        还没标注的对手会归到 ``'(未标注)'``（调用方可以过滤掉）。

        :param went_first: ``None`` = 不过滤（含 ``went_first`` 为 NULL 的老局）；
            ``True`` 只算实际先攻的局、``False`` 只算后攻（D2 矩阵三页签；
            ``NULL`` 的局两个页签都不进，只进综合）。
        :param mode_group: ``None`` = 主口径不变；给了分类（ranked/peak/dc/event/all）
            就按那一类单独统计（D2 看板「对局分析」的模式筛选，用户 2026-09-19 拍板
            —— 推翻「本函数不收 mode_group」的旧裁定，见 docs/M7看板计划.md §2.4 v5）。
        """
        clauses = ['result IN (%s)' % ','.join("'%s'" % r for r in DONE_RESULTS)]
        #: mode_group 给了就**替代**主口径（镜像 `_stats_where` 的逻辑）——
        #: 不然 counted_clause(只含 3/19) AND dc(12) 恒为空（单测抓过）
        mode = (mode_group_clause(mode_group)
                if mode_group is not None else counted_clause())
        if mode:
            clauses.append(mode)
        args = []
        if went_first is not None:
            clauses.append('went_first = ?')
            args.append(1 if went_first else 0)
        if since:
            clauses.append('started_at >= ?')
            args.append(since)
        if until:
            clauses.append('started_at <= ?')
            args.append(until)
        #: ★ 对手分组 = **卡组身份**（opp_decks 当前名，LEFT JOIN）——
        #:   管理页改名后矩阵自动跟随（2026-10-12 用户：对局分析要跟卡组名管理
        #:   关联）。没绑定的局回退**名字快照**（老数据/悬空 id 都不至于丢行）。
        opp_expr = ("COALESCE(NULLIF(TRIM(od.name), ''), %s)" % OPP_DECK_EXPR)
        my_expr = _deck_expr('my_deck_name')
        sql = (
            "SELECT %s AS my_deck, %s AS opp_deck,"
            "  COUNT(*) AS n,"
            "  COALESCE(SUM(result = 'Win'), 0)  AS w,"
            "  COALESCE(SUM(result = 'Lose'), 0) AS l,"
            "  COALESCE(SUM(result = 'Draw'), 0) AS d"
            " FROM matches"
            " LEFT JOIN opp_decks od ON od.opp_deck_id = matches.opp_deck_id"
            " WHERE %s"
            " GROUP BY %s, %s"
            " ORDER BY n DESC, my_deck ASC, opp_deck ASC"
            % (my_expr, opp_expr, ' AND '.join(clauses), my_expr, opp_expr))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        #: 分组名（站内名 / 老快照）→ **当前显示名**（中文名优先）—— 覆盖表改名
        #: 在这里生效，矩阵与 `mark_opponent --cross` 都吃这份（2026-10-12）
        disp = self.meta_deck_display_map()
        return [(r['my_deck'],
                 disp.get(r['opp_deck'], r['opp_deck']),
                 r['n'], r['w'], r['l'], r['d']) for r in rows]

    def get_deck_distribution(self, since=None, until=None):
        """卡组分布 -> [(卡组名, 场次)]，只统计已结束的对局。

        排序与 v1 的 ``Counter.most_common()`` 一致：场次降序，同场次按
        **首次出现时间**升序（否则并列项的顺序会与改写前不同）。

        传 ``since`` / ``until`` 可只看某段时间（今日视图要用）。
        """
        clauses = ['result IN (%s)' % ','.join("'%s'" % r for r in DONE_RESULTS),
                   counted_clause()]
        args = []
        if since:
            clauses.append('started_at >= ?')
            args.append(since)
        if until:
            clauses.append('started_at <= ?')
            args.append(until)
        sql = ('SELECT %s AS name, COUNT(*) AS n, MIN(started_at) AS first_at'
               ' FROM matches WHERE %s'
               ' GROUP BY name ORDER BY n DESC, first_at ASC'
               % (_deck_expr('my_deck_name'), ' AND '.join(clauses)))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [(r['name'], r['n']) for r in rows]

    # -------------------------------------------------------- 统计
    def _stats_where(self, since=None, until=None, mode_group=None,
                     mode_scope=MODE_SCOPE_COUNTED, account=None):
        """统计的 WHERE 片段（已结束 + 模式口径 + 可选时间窗）。

        * **`mode_group=None`（默认）= 主口径**（`counted_clause()`：只算天梯 3 + 巅峰 19）；
          给了分类就按那一类**单独统计**（`ranked`/`peak`/`dc`/`event`/`all`）——
          用户 2026-09-13 拍板「DC 杯与活动单独统计」，但**默认行为一个字不变**。
        * **`mode_scope='duel'`**（2026-09-16 用户定）= **界面口径**：天梯 + 巅峰 + 各活动
          （见 :func:`duel_clause`）。`mode_group` 给了的话以 `mode_group` 为准。
        """
        if mode_group is None:
            mode = (duel_clause() if mode_scope == MODE_SCOPE_DUEL
                    else counted_clause())
        else:
            mode = mode_group_clause(mode_group)
        clauses = ['result IN (%s)'
                   % ','.join("'%s'" % r for r in DONE_RESULTS)]
        if mode:
            clauses.append(mode)
        sql = ' AND '.join(clauses)
        args = []
        if since:
            sql += ' AND started_at >= ?'
            args.append(since)
        if until:
            sql += ' AND started_at <= ?'
            args.append(until)
        #: 多账号（2026-10-03）：``None`` = 不筛（含「未分账号」的历史局）
        if account:
            sql += ' AND account_name = ?'
            args.append(account)
        return sql, args

    def get_group_stats(self, since=None, until=None, groups=None):
        """**每个模式分类各自一份战绩** -> ``[(分类, 中文名, stats), ...]``。

        用户 2026-09-13 拍板：主口径只认天梯 + 巅峰，**DC 杯与各活动各自单独统计**
        （它们不进主口径）。顺序固定 ``ranked -> peak -> dc -> event``，
        可用 ``groups`` 指定子集或顺序。
        """
        order = list(groups) if groups else [
            MODE_GROUP_RANKED, MODE_GROUP_PEAK, MODE_GROUP_DC, MODE_GROUP_EVENT]
        return [(g, MODE_GROUP_NAMES[g],
                 self.get_stats(since, until, mode_group=g)) for g in order]

    # -------------------------------------------- M7 新增聚合（S0）
    def max_rowid(self):
        """当前 ``MAX(rowid)`` —— M7「待录入」判据的锚点（见 ``core/ui_state.py``）。

        ``rowid`` 是 SQLite 的隐式自增行号，**只增不复用**（除非 VACUUM），
        所以「启动时记下的 max_rowid」之后新增的行 = 本应用启动之后入库的。
        """
        with self._open() as conn:
            row = conn.execute(
                'SELECT COALESCE(MAX(rowid), 0) AS m FROM matches').fetchone()
        return int(row['m'])

    def unmarked_since(self, rowid, limit=50):
        """待标注**且** ``rowid > rowid`` 的对局，新的在前（带 ``rowid`` 键）。

        判据与 :meth:`get_unmarked_matches` **完全一致**（同一句 ``counted_clause()``，
        所以活动局天然不在里面），只多了 rowid 下界 —— M7 用它把「本应用启动之后
        入库的未标注局」挑出来，历史积压不会触发悬浮窗。
        """
        with self._open() as conn:
            rows = conn.execute(
                'SELECT rowid, * FROM matches WHERE opp_deck_id IS NULL'
                ' AND ' + counted_clause() +
                ' AND rowid > ? ORDER BY rowid DESC LIMIT ?',
                (int(rowid), int(limit))).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_daily_stats(self, since=None, until=None, mode_group=None,
                        account=None):
        """**按天聚合** -> ``{'YYYY-MM-DD': {'n':…,'w':…,'l':…,'d':…}}``（月度热力图用）。

        口径与 :meth:`get_stats` 完全一致（同一个 :meth:`_stats_where`，
        默认 = 主口径），**不另写一份**。日期取 ``started_at`` 的前 10 个字符
        （定宽文本，字符串比较即可，见 :func:`day_range`）。
        """
        where, args = self._stats_where(since, until, mode_group,
                                        account=account)
        sql = ('SELECT substr(started_at, 1, 10) AS day,'
               ' COUNT(*) AS n,'
               " COALESCE(SUM(result = 'Win'), 0) AS w,"
               " COALESCE(SUM(result = 'Lose'), 0) AS l,"
               " COALESCE(SUM(result = 'Draw'), 0) AS d"
               ' FROM matches WHERE %s GROUP BY day ORDER BY day' % where)
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()
        return {r['day']: {'n': r['n'], 'w': r['w'], 'l': r['l'], 'd': r['d']}
                for r in rows}

    def get_stats_by_deck(self, since=None, until=None, mode_group=None,
                          account=None):
        """**按我方卡组聚合** -> ``[(卡组名, stats), ...]``（主窗口的分组头指标）。

        审查 P1-1：现成的 ``get_deck_distribution()`` 只返回 ``[(名, 场数)]``，
        **没有胜负 / 先后手**，撑不起分组头那一行 ``6-2 │ 74% │ 先 62% │ 后 53%``。

        * 口径复用 :meth:`_stats_where`（默认主口径），卡组名归一化复用
          :func:`_deck_expr` —— **与 ``get_deck_distribution`` 同一表达式，口径不二写**；
        * 排序同 ``get_deck_distribution``：场次降序，同场次按首次出现时间升序；
        * ``stats`` 的键与 :meth:`get_stats` 对齐（``n_total`` / ``n_win`` /
          ``win_rate`` / ``first_rate`` / ``second_rate`` …），另给 ``first_at`` /
          ``last_at`` 供「1 天前」这类显示。
        """
        where, args = self._stats_where(since, until, mode_group,
                                        account=account)
        agg = ('COUNT(*) AS n,'
               " COALESCE(SUM(result = 'Win'), 0) AS w,"
               " COALESCE(SUM(result = 'Lose'), 0) AS l,"
               " COALESCE(SUM(result = 'Draw'), 0) AS d,"
               " COALESCE(SUM(went_first = 1), 0) AS f_n,"
               " COALESCE(SUM(went_first = 1 AND result = 'Win'), 0) AS f_w,"
               " COALESCE(SUM(went_first = 0), 0) AS s_n,"
               " COALESCE(SUM(went_first = 0 AND result = 'Win'), 0) AS s_w,"
               ' MIN(started_at) AS first_at, MAX(started_at) AS last_at')
        sql = ('SELECT %s AS name, %s FROM matches WHERE %s'
               ' GROUP BY name ORDER BY n DESC, first_at ASC'
               % (_deck_expr('my_deck_name'), agg, where))
        with self._open() as conn:
            rows = conn.execute(sql, args).fetchall()

        out = []
        for r in rows:
            n, f_n, s_n = r['n'], r['f_n'], r['s_n']
            out.append((r['name'], {
                'n_total': n, 'n_win': r['w'], 'n_lose': r['l'], 'n_draw': r['d'],
                'win_rate': (r['w'] / n) if n else None,
                'first_n': f_n, 'first_wins': r['f_w'],
                'first_rate': (r['f_w'] / f_n) if f_n else None,
                'second_n': s_n, 'second_wins': r['s_w'],
                'second_rate': (r['s_w'] / s_n) if s_n else None,
                'first_at': r['first_at'], 'last_at': r['last_at'],
            }))
        return out

    def session_start(self, gap_minutes: int = SESSION_GAP_MIN, now=None):
        """「本轮」的起点 —— 总览 / 先后攻统计的 `since`（见 `session_start_of`）。

        :return: 本轮第一局的 ``started_at``；
                 **库里没有对局 / 本轮已超时（见下）都返回 None**

        ``now`` 默认取**本机当前时刻** —— **最后一局结束**距今 ≥ `gap_minutes`
        就返回 None = **本轮结束**（统计清空）。传 `now` 可注入（单测用）。

        ⚠️ 查的是 ``(started_at, ended_at, duration_sec)`` 三元组 —— 切轮比的是
        「上一局**结束** → 本局**开局**」（见 :func:`end_time_of`）；
        只比两个开局会被**超长对局**骗（用户 2026-09-17 提问引出）。
        """
        try:
            with self._open() as conn:
                rows = [(r[0], r[1], r[2]) for r in conn.execute(
                    "SELECT started_at, ended_at, duration_sec FROM matches"
                    " WHERE started_at IS NOT NULL AND started_at <> ''"
                    ' ORDER BY started_at')]
        except Exception:
            return None
        pairs = [(s, end_time_of(s, e, d)) for s, e, d in rows]
        return session_start_of(pairs, gap_minutes,
                                now=datetime.now() if now is None else now)

    def get_stats(self, since=None, until=None, mode_group=None,
                  mode_scope=MODE_SCOPE_COUNTED, account=None):
        """胜率 / 先后手 / 硬币 / 待标注，全部用 SQL 聚合。

        只统计 ``result IN ('Win','Lose','Draw')`` 的对局 —— ``(未结束)`` 不进
        胜率分母（与 v1 的 show_stats 一致）。

        返回结构与 ``get_today_stats`` 相同（``date`` 为 None）。

        ``mode_group`` 不给 = **主口径**（3 + 19）；给了就按该分类单独统计（见
        :meth:`_stats_where`）—— 主口径默认行为不变。
        """
        where, args = self._stats_where(since, until, mode_group,
                                        mode_scope=mode_scope, account=account)
        with self._open() as conn:
            def one(extra='', extra_args=()):
                sql = ('SELECT COUNT(*) AS n,'
                       " COALESCE(SUM(result = 'Win'), 0)  AS w,"
                       " COALESCE(SUM(result = 'Lose'), 0) AS l,"
                       " COALESCE(SUM(result = 'Draw'), 0) AS d"
                       ' FROM matches WHERE ' + where + extra)
                return conn.execute(sql, args + list(extra_args)).fetchone()

            tot = one()
            first = one(' AND went_first = 1')
            second = one(' AND went_first = 0')
            ini = one(' AND won_initiative IS NOT NULL')
            ini_won = one(' AND won_initiative = 1')
            unmarked = one(' AND opp_deck_id IS NULL')

        n = tot['n']
        f_n, f_w = first['n'], first['w']
        s_n, s_w = second['n'], second['w']
        return {
            'date': None,
            'n_total': n,
            'n_win': tot['w'],
            'n_lose': tot['l'],
            'n_draw': tot['d'],
            'win_rate': (tot['w'] / n) if n else None,
            'first_n': f_n,
            'first_wins': f_w,
            'first_rate': (f_w / f_n) if f_n else None,
            'second_n': s_n,
            'second_wins': s_w,
            'second_rate': (s_w / s_n) if s_n else None,
            'first_hand_pct': (f_n / (f_n + s_n)) if (f_n + s_n) else None,
            'initiative_n': ini['n'],
            # 赢到硬币的**场数**（v1 的 show_stats 打印的是这个，不是 ini_n）
            'initiative_won': ini_won['n'],
            'initiative_rate': (ini_won['n'] / ini['n']) if ini['n'] else None,
            'unmarked': unmarked['n'],
        }

    def get_today_stats(self, today=None):
        """今日战绩。``today`` 默认取本机当天（YYYY-MM-DD）。"""
        day = today or date.today().isoformat()
        since, until = day_range(day)
        st = self.get_stats(since=since, until=until)
        st['date'] = day
        return st

    def vacuum(self):
        """整理数据库文件（写操作后偶尔跑一次）。"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('VACUUM')
        finally:
            conn.close()
