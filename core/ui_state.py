# -*- coding: utf-8 -*-
"""M7 数据层（S0）—— **纯 Python，不依赖任何 GUI 框架**。

设计原则（见 ``docs/M7计划.md`` §三）：界面测不动，所以**能单测的逻辑全放这里**。
UI 只负责把 :class:`UiSnapshot` 画出来。

★ 两条铁律：

1. **这里不许出现任何 GUI import** —— 框架（PySide6 / 别的）定了也不许加；
2. **任何函数都不许抛异常** —— 库被锁、字段缺失、表为空，一律退化成「无数据」。
   UI 不能因为读库失败就崩在游戏上面。

「待录入」判据（审查 P1-3 定稿，见 :func:`pending_match`）：

    触发 = **本应用启动之后入库** 的、**口径内**、**未标注** 的对局存在。
    锚点用 ``MAX(rowid)``（:meth:`core.storage.Storage.max_rowid`），
    「稍后」记 match_id，**历史积压只走主窗口待标注 tab、永不触发悬浮窗**。
"""
from __future__ import annotations

import calendar
import datetime
import json
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ⚠️ 唯一来源：口径常量与伪条目名单都**不在这里重写**（S6 审查 P2-2 的同源要求）。
from core import paths
from core.storage import (MODE_GROUP_ALL, MODE_SCOPE_DUEL,
                           MODE_SELECT_NONE, mode_group)
from core.ui_actions import NON_DECK_LABELS

#: 项目根 —— 读 `data/card_names.json` 这种**真源文件**时要拼绝对路径
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 悬浮窗两态（**没有第三态** —— 实时帧按 D2 本期不消费）
MODE_IDLE = 'idle'
MODE_PENDING = 'pending'

#: 快照里可被 :func:`diff` 单独比较的段
SECTIONS: Tuple[str, ...] = (
    'mode', 'today', 'stats', 'by_deck', 'heatmap', 'unmarked', 'pending',
    'recent', 'my_rank')

_EMPTY_DAY = {'n': 0, 'w': 0, 'l': 0, 'd': 0}


class Probe(object):
    """**便宜探针**的结果（审查 P2-3）—— 1~2 秒跑一次的那条 SQL。

    两个字段够判「值变没变」：``n_total`` / ``unmarked`` 变了才拉全量快照。
    ``max_rowid`` 顺带当「有没有新局入库」的锚点。

    ⚠️ ``session``（本轮起点 / None）**必须进 `key()`**：本轮超时（隔 ≥60 分钟）
    时前三个字段**一个都不变** —— 窗口一直开着就永远不会刷新、两块统计一直是旧数字
    （2026-09-16 晚实机踩到）。带上它，跨过阈值那一刻 key 变化 → `tick()` 触发重拉。

    ⚠️ ``backfill``（回填欠账，2026-09-18 八轮）同理必须进 `key()`：
    段位探测 / winbits 的**事后回填**是 UPDATE（不动 rowid/n_total），
    不带上它，回填完成后界面会一直挂着空段位（用户实测：投降局没显示段位）。
    """

    __slots__ = ('n_total', 'max_rowid', 'unmarked', 'session', 'backfill',
                 'my_deck_fp')

    def __init__(self, n_total: int = 0, max_rowid: int = 0,
                 unmarked: int = 0, session=None, backfill: int = 0,
                 my_deck_fp: str = '') -> None:
        self.n_total = int(n_total)
        self.max_rowid = int(max_rowid)
        self.unmarked = int(unmarked)
        self.session = session
        self.backfill = int(backfill)
        self.my_deck_fp = str(my_deck_fp or '')

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Probe):
            return NotImplemented
        return self.key() == other.key()

    def __repr__(self) -> str:
        return ('Probe(n_total=%d, max_rowid=%d, unmarked=%d, session=%r)'
                % (self.n_total, self.max_rowid, self.unmarked, self.session))

    def key(self) -> Tuple:
        """用于比较的元组（变了才拉全量）。"""
        return (self.n_total, self.max_rowid, self.unmarked, self.session,
                self.backfill, self.my_deck_fp)


class UiSnapshot(object):
    """一次全量读取的结果。字段缺什么都得是 None / 空，不能炸。

    `recent` 是**已经解析好的一行行 dict**（卡图路径、段位图标、升段判定都在里面），
    界面直接画就行 —— **UI 不该为了这些再去查库 / 查别名**。
    """

    __slots__ = ('mode', 'today', 'stats', 'by_deck', 'heatmap',
                 'unmarked', 'pending', 'probe', 'recent', 'my_rank')

    def __init__(self, mode: str = MODE_IDLE, today=None, stats=None,
                 by_deck=None, heatmap=None, unmarked=None, pending=None,
                 probe: Optional[Probe] = None, recent=None,
                 my_rank=None) -> None:
        self.mode = mode
        self.today = today
        self.stats = stats or {}
        self.by_deck = list(by_deck or [])
        self.heatmap = dict(heatmap or {})
        self.unmarked = list(unmarked or [])
        self.pending = pending
        self.probe = probe
        self.recent = list(recent or [])
        self.my_rank = my_rank

    @property
    def is_pending(self) -> bool:
        return self.mode == MODE_PENDING

    @property
    def unmarked_count(self) -> int:
        return len(self.unmarked)

    def section(self, name: str):
        return getattr(self, name, None)

    def to_dict(self) -> Dict[str, object]:
        return {s: self.section(s) for s in SECTIONS}


# --------------------------------------------------------------- 探针 / 锚点
def probe(storage) -> Optional[Probe]:
    """一条 SQL 拿到「有没有变」。失败返回 None（**不抛**）。"""
    try:
        n = storage.count()
        rowid = storage.max_rowid()
        unmarked = storage.get_stats().get('unmarked') or 0
    except Exception:
        return None
    # 本轮起点（超时 = None）—— 没有它，窗口开着跨过 60 分钟阈值也不会刷新。
    # 单查失败不算整条探针失败（老桩类没有这个方法）：降级成「不判轮次」。
    try:
        session = storage.session_start()
    except Exception:
        session = None
    try:
        backfill = int(storage.backfill_pending())
    except Exception:
        backfill = 0                        # 老 fake / 无此方法 → 视为没有欠账
    # ★ 「当前卡组」状态（2026-09-22）：采集器识别出新卡组只写 `my_deck.json`
    #   （**不动库**）—— 不进 key 的话，悬浮窗的卡组确认条会一直显示旧内容
    #   （用户实测：匹配界面换了卡组，提示条纹丝不动）。
    my_deck_fp = ''
    try:
        from core import my_deck
        my_deck_fp = my_deck.state_fingerprint()
    except Exception:
        my_deck_fp = ''                     # 状态文件读不到 = 没有变化可言
    return Probe(n_total=n or 0, max_rowid=rowid or 0, unmarked=unmarked,
                 session=session, backfill=backfill, my_deck_fp=my_deck_fp)


def boot_anchor(storage) -> int:
    """应用启动时记下的锚点（``MAX(rowid)``）。失败返回 0 = 全部都算「之后」。"""
    try:
        return int(storage.max_rowid() or 0)
    except Exception:
        return 0


# ----------------------------------------------------------- 待录入（P1-3）
def pending_match(storage, boot_rowid: int,
                  snoozed: Sequence[str] = ()) -> Optional[dict]:
    """该不该弹录入框？返回要弹的那一局（row dict），不该弹返回 None。

    四条判据（审查 P1-3 拍死，**别再改成"存在未标注的新局"** —— 那会让
    UI 一启动就弹，因为库里常驻着十几二十局历史积压）：

    1. **本应用启动之后入库** —— ``rowid > boot_rowid``（``rowid`` 只增不复用）；
    2. **口径内 + 未标注** —— 由 :meth:`Storage.unmarked_since` 保证
       （同一句 ``counted_clause()``，所以活动局天然不在里面）；
    3. **「稍后」过的不再弹** —— ``snoozed`` 里的 match_id 跳过；
    4. 弹**最新一局**（rowid 最大）。

    标注完成后由调用方把锚点重设到当前 `MAX(rowid)`（见 :func:`next_anchor`）。
    """
    try:
        rows = storage.unmarked_since(int(boot_rowid or 0))
    except Exception:
        return None
    skip = set(snoozed or ())
    for row in rows:                       # unmarked_since 已按 rowid 倒序
        if row.get('match_id') in skip:
            continue
        return row
    return None


def next_anchor(storage, fallback: int = 0) -> int:
    """标注完成 / 点「稍后」后重设锚点 —— 把已处理过的历史甩在身后。"""
    try:
        return int(storage.max_rowid() or 0)
    except Exception:
        return int(fallback or 0)


def deck_name_pool(storage) -> List[str]:
    """**所有可能显示的卡组名**（三语 + 别名 + 我自己的卡组）—— 算窗口宽度上限用。

    2026-09-15 用户定：左右拖宽最多到「最长的那个卡组名完整显示」为止，
    所以要先知道有哪些名字、最长的多长。这里只负责**收集**，
    量像素是界面层的事（`ui.floating.max_window_width`）。

    两种数据形状都要认：
    * `list_meta_decks()` → dict（`name` / `name_cn` / `name_display` / `name_ja` / `aliases`）
    * `list_opp_decks()` → **tuple** `(id, name, aliases, count)`

    **不抛异常** —— 拿不到就少收几个名字，界面会退回默认宽度上限。
    """
    out = set()
    keys = ('name', 'name_cn', 'name_display', 'name_ja')
    try:
        for d in storage.list_meta_decks() or []:
            if isinstance(d, dict):
                for key in keys:
                    if d.get(key):
                        out.add(str(d[key]))
                for a in (d.get('aliases') or []):
                    out.add(str(a))
            elif isinstance(d, (list, tuple)) and len(d) > 1 and d[1]:
                out.add(str(d[1]))
    except Exception:
        pass
    try:
        for d in storage.list_opp_decks() or []:
            if isinstance(d, (list, tuple)):
                if len(d) > 1 and d[1]:
                    out.add(str(d[1]))
                if len(d) > 2:
                    for a in (d[2] or []):
                        out.add(str(a))
            elif isinstance(d, dict):
                for key in keys:
                    if d.get(key):
                        out.add(str(d[key]))
    except Exception:
        pass
    return sorted(out)


# ------------------------------------------------- 最近几场（界面直接用这个）
def _recent_row(m, nxt, decks, disp_name=None) -> dict:
    """一条 match 记录 + 它的「下一局」→ 界面能直接画的一行。

    `nxt` **只**用来倒推这局打完后的段位 —— 库里只有开打前的 `before`
    （见 `core/ranks.py` 模块头）。
    """
    from core import modes as M
    from core import ranks as R

    started = str(m.get('started_at') or '')
    today = datetime.date.today().isoformat()
    r_before = (m.get('my_rank_before'), m.get('my_rate_before'))
    group = M.group(m.get('game_mode'))
    # ⚠️ 段位变化**只在天梯局算**（2026-09-15 用户口径：巅峰赛只显示
    # 巅峰图标，没有段位）—— `r_after` 取的是「下一局」开打前的段位，
    # 巅峰局的下一局多半是天梯，跨模式比出来只会是**错的升降段**；
    # 巅峰/DC/活动一律不算（rank_after / delta / after 三件套全空）。
    r_after = None
    # ★ 优先用**内存给的局后段位**（`my_rank_after`，2026-09-15 起有）：
    #   老口径只能靠「下一局的 before」倒推，而**最新一局没有下一局**
    #   → 升段那行不显示新段位（实机踩到：升到白金1 而界面还是旧段位）。
    if group == 'ranked' and m.get('my_rank_after') is not None:
        r_after = (m.get('my_rank_after'), m.get('my_rate_after'))
    elif nxt is not None and group == 'ranked' and M.group(nxt.get('game_mode')) == 'ranked':
        # ★ BUG-B1-08：倒推源那一局**也必须是天梯局**。原判据只查本局，
        #   于是「天梯局之后紧跟一局 DC/活动」时，把 DC 局补回来的假段位
        #   （`my_rank_before` 是别处填的，不是真天梯段位）当成局后段位，
        #   算出错的升降段。10 月数据：DC/活动 51 行，全在天梯连打之间。
        r_after = (nxt.get('my_rank_before'), nxt.get('my_rate_before'))
    badge_icon, badge_text = M.badge(m.get('game_mode'),
                                     r_before[0], r_before[1])
    # 小段位的**罗马数字文字**（'III' 这种）—— 只有天梯才贴；
    # 巅峰/DC/活动没有小段位，给空串（界面就不画）。
    badge_numeral = (R.numeral_text(r_before[1])
                     if group == 'ranked' else '')
    badge_numeral_after = (R.numeral_text(r_after[1])
                           if (group == 'ranked' and r_after) else '')
    # ★ 巅峰积分（2026-09-28）：只作为 `peak_rate` 字段随行携带（悬浮窗**不消费**
    #    —— 用户二口径「悬浮窗只显示图标」）；看板战绩页的段位列用它画
    #    「巅峰图标 + 积分」（ui/dashboard._rank_cell）。
    #    after 优先 / before 兜底，与天梯行「局后值优先」同语义；没有给 None。
    peak_rate = None
    if group == 'peak':
        peak = m.get('peak_rate_after')
        if peak is None:
            peak = m.get('peak_rate_before')
        if peak is not None:
            peak_rate = '%.2f' % float(peak)
    opp_deck = m.get('opp_deck_name') or ''
    if disp_name is not None:                  # 显示名按语言（对手卡组列）
        opp_deck = disp_name(opp_deck)
    return {
        'match_id': m.get('match_id'),
        'started_at': started,
        'day': started[5:10],                       # 'MM-DD'
        'time': started[11:16],                     # 'HH:MM'
        'is_today': started[:10] == today,
        'result': m.get('result'),
        'went_first': m.get('went_first'),   # True=先攻 / False=后攻 / None=未知
        'my_deck': m.get('my_deck_name'),
        'my_deck_img': decks.resolve(m.get('my_deck_name')),
        'opp_deck': opp_deck,
        'opp_deck_img': decks.resolve(opp_deck),
        'opp_known': bool(opp_deck),                # False = 没标注（界面画 ⚠）
        # ⚠️ M5 反推（`core.deck_guess.suggest`）的**唯一输入**：对手打出的卡 id
        # （JSON 字符串）。少了它，选择器面板里的「建议」永远是空的
        # —— 2026-09-15 做面板时发现的缺口，别删。
        'opp_revealed_ids': m.get('opp_revealed_ids'),
        'opp_revealed': m.get('opp_revealed'),
        # S3 选择器预填用（matches.opp_deck_id = **opp_decks** 表的 id）
        'opp_deck_id': m.get('opp_deck_id'),
        'opp_player': m.get('opp_player_name'),
        # 一个「等级标识位」：天梯给段位图标、巅峰给巅峰图标
        'badge_icon': badge_icon,
        'badge_text': badge_text,
        'rank_before': r_before,
        'rank_after': r_after,
        'rank_delta': R.compare(r_before, r_after) if r_after else None,
        'rank_label_before': R.rank_label(*r_before),
        'rank_label_after': R.rank_label(*r_after) if r_after else '',
        # 升/掉段时显示的**当前（局后）段位**图标（2026-09-15 起 A→B 对比已删）
        'rank_icon_after': (R.icon_path(r_after[0]) if r_after else None),
        # ★ 2026-09-18 五轮：升降段局要画「**动之前那一档**」（用户：「白金2升段局
        # 如果赢了就是 Platinum 2 ↑」）→ 界面需要**局前**的图标，这里补上。
        'rank_icon_before': (R.icon_path(r_before[0]) if r_before[0] else None),
        'badge_numeral': badge_numeral,
        'badge_numeral_after': badge_numeral_after,
        # 巅峰积分（'%d.%02d' 已格式化字符串；非巅峰行恒 None —— 悬浮窗不消费）
        'peak_rate': peak_rate,
        #: 段位「获取中」占位（同 `_page_row`；悬浮窗与看板同一份语义）
        'rank_pending': rank_pending(m.get('ended_at'), m.get('game_mode')),
    }


def build_recent(storage, limit: int = 3, decks=None,
                 account=None, disp_name=None) -> List[dict]:
    """最近 `limit` 场（**新的在前**），已解析成界面能直接画的行。

    ⚠️ **必须用 `get_recent_matches()`（`ORDER BY started_at DESC`）**，
    不要用 `list_matches()` —— 后者是 **ASC**，拿它取"最近"会拿到几个月前的老局
    （2026-09-14 实测踩过：想要 09-13 的局，拿到了 09-02 的）。

    多取一条（`limit + 1`）是为了让**最老那一行**也能算出「打完后的段位」——
    用它的**上一局**（DESC 排序里就是 `rows[i - 1]`）开打前的段位。

    **不许抛异常** —— 任何一步失败退回已拿到的部分 / 空列表。
    """
    from core.deck_images import DeckImages

    try:
        #: 多拉几倍再滤：单人模式（solo）不进悬浮窗，别让连打几局单人把
        #: 真正的最近对局挤出去（09-26 用户实报）。窗口外极端情况不追。
        rows = list(storage.get_recent_matches(limit=limit * 4 + 8,
                                               account=account) or [])
    except Exception:
        return []
    if not rows:
        return []
    # ⚠️ 还要看 `mode` 字段：**补录的日志行 `game_mode` 是 NULL**
    #    （日志线拿不到 game_mode），只按 `mode_group(None) -> None` 判会漏掉
    #    单人局 —— 2026-10-08 用户实报「悬浮窗还有 4 局单人模式」。
    #    与 `_in_duel_scope()` 对 game_mode=None 的兜底同语义。
    rows = [m for m in rows
            if mode_group(m.get('game_mode')) != 'solo'
            and m.get('mode') != 'solo']
    if decks is None:
        decks = DeckImages(storage)

    out: List[dict] = []
    for i in range(min(limit, len(rows))):
        # DESC：i-1 才是"时间上更晚"的那一局
        nxt = rows[i - 1] if i - 1 >= 0 else None
        try:
            out.append(_recent_row(rows[i], nxt, decks, disp_name=disp_name))
        except Exception:
            continue                                # 单行坏了不拖累整列
    return out


def current_rank(storage, scan: int = 200, account=None) -> Optional[dict]:
    """我**最后一次已知的段位** → `{'rank','rate','label','icon'}`；没有返回 None。

    ⚠️ 两个坑都在这儿处理掉：

    1. **段位字段只有 25/54 有值** —— 最新几局很可能是空的，
       所以不能只看最新那一局，得**往前扫到最近一条有段位的记录**（默认扫 200 条）。
    2. **它叫"最后一次已知"，不是"实时"** —— 游戏只在**开局**刷新这个值
       （`duelSettings`），所以严格说这是"最近一局开打时的段位"，
       可能比真值小一档。要精确的话得改采集侧（收局再读一次），已记为待办。

    **不许抛异常** —— 读失败返回 None。
    """
    from core import ranks as R

    try:
        rows = storage.get_recent_matches(limit=int(scan),
                                          account=account) or []
    except Exception:
        return None
    for m in rows:
        # ★ BUG-B1-08：**只看天梯局**。DC 杯 / 活动局的 `my_rank_*` 是别处
        #   补回来的值（不是天梯段位），拿它当「当前段位」会顶掉真的那条。
        #   10 月实测：非天梯行里 74 行带段位列（DC 51 行占多数）。
        if mode_group(m.get('game_mode')) != 'ranked':
            continue
        # ★ 优先用内存的局后段位（最新一局的「当前段位」才是真的当前）
        rank, rate = m.get('my_rank_after'), m.get('my_rate_after')
        if not R.rank_name(rank):
            rank, rate = m.get('my_rank_before'), m.get('my_rate_before')
        if R.rank_name(rank):
            return {'rank': rank, 'rate': rate,
                    'label': R.rank_label(rank, rate),
                    'icon': R.icon_path(rank),
                    'from_match': m.get('match_id')}
    return None


# ------------------------------------------------------------------- 全量
#: `stats_since()` 的哨兵 —— **本轮已结束**（隔太久没打）。
#: ⚠️ 不能拿 `None` 表示这件事：`None` 在 `get_stats(since=None)` 里是
#: 「**不限时间**」= 全库统计，语义正好相反（2026-09-16 晚踩到的坑）。
SESSION_EXPIRED = object()

#: 本轮结束时拿去查 stats 的「起点」—— 一个**不可能命中的未来时刻**。
#: 这样 `get_stats` 自然返回**零值结构**（计数 0 / 比率 None），而**不是空 dict**：
#: 界面要的是「**新一轮还没开打**」（胜负 `0-0`、先攻/后攻 `0`、比率 `-`），
#: 不是「没有数据」；而且形状由 `get_stats` 统一产 —— 以后加字段两边不会漂。
NO_SESSION_SINCE = '9999-12-31 23:59:59'


def stats_since(storage, scope: str = 'session'):
    """总览 / 先后攻统计的**时间起点**（用户 2026-09-16 定：**按「每一轮」算**）。

    * ``session``（默认）= 本轮：相邻两局间隔 ≥ 60 分钟断开，**且最后一局距今
      ≥ 60 分钟 = 本轮已结束**（见 :func:`core.storage.session_start_of`）；
      本轮结束时返回 :data:`SESSION_EXPIRED`（不是 None —— 那个意思是「不限」）；
    * ``today`` = 今天 00:00；``all``（及未知值）= 不限（None）。
    """
    if scope == 'session':
        try:
            start = storage.session_start()
        except Exception:              # 库坏了也别让界面挂掉
            # 读不到轮次 = **当本轮已结束**（零值），**不是**「不限时间」——
            # 后者会拿全库数字冒充「本轮」，正是要修的谎。
            return SESSION_EXPIRED
        return start if start else SESSION_EXPIRED
    if scope == 'today':
        return datetime.date.today().isoformat() + ' 00:00:00'
    return None


def snapshot(storage, boot_rowid: Optional[int] = None,
             snoozed: Sequence[str] = (),
             unmarked_limit: int = 100,
             recent_limit: int = 3,
             stats_scope: str = 'session',
             account=None) -> UiSnapshot:
    """拉一份全量快照。**失败不退 None，返回一份空快照**（mode=idle）。

    ``stats_scope``：统计的时间口径 —— ``session``（默认，本轮）/ ``today`` /
    ``all``（见 :func:`stats_since`）。
    """
    snap = UiSnapshot(mode=MODE_IDLE)
    # ⚠️ **逐块 try**（BUG-B1-11）：原来全段共用一个 try，任一查询炸掉
    #    （典型：库被采集器锁住一瞬）后面的块**整排变空** —— 用户看到的是
    #    「本局统计 0-0 / 最近对局空白」，而不是「只有一块读挂了」。
    #    现在一块失败只丢那一块（默认值），其余照常填。
    try:
        snap.probe = probe(storage)
    except Exception:
        snap.probe = None                            # 探针失败 -> 上层按「无数据」处理
    # ⚠️ 界面口径（用户 2026-09-16）：总览 / 先后攻统计要算**天梯 + 巅峰 + 活动**
    #（打活动局时两块全 0 就是这条引起的）；主口径与 CLI 不受影响。
    # ⚠️ 本轮结束（隔 ≥60 分钟没打）-> 拿**不可能命中的起点**去查 =>
    #    零值结构（计数 0 / 比率 None）—— 界面显示「**新一轮还没开打**」
    #    （胜负 `0-0`、先攻/后攻 `0`、比率 `-`），用户 2026-09-16 晚定。
    #    不能把「本轮结束」当成 `since=None` 丢给 get_stats —— 那是「不限时间」，
    #    会变成**全库统计**（数字反而更大，实机踩到）。
    try:
        since = stats_since(storage, stats_scope)
        snap.stats = storage.get_stats(
            since=(NO_SESSION_SINCE if since is SESSION_EXPIRED else since),
            mode_scope='duel', account=account) or {}
    except Exception:
        snap.stats = {}
    try:
        snap.today = storage.get_today_stats() or {}
    except Exception:
        snap.today = {}
    try:
        snap.by_deck = list(storage.get_stats_by_deck(account=account) or [])
    except Exception:
        snap.by_deck = []
    try:
        snap.heatmap = dict(storage.get_daily_stats(account=account) or {})
    except Exception:
        snap.heatmap = {}
    try:
        snap.unmarked = list(
            storage.get_unmarked_matches(limit=unmarked_limit) or [])
    except Exception:
        snap.unmarked = []
    try:
        _lng = _ui_lang()
        snap.recent = build_recent(storage, limit=recent_limit,
                                   account=account,
                                   disp_name=(_name_resolver(storage, _lng)
                                              if _lng != 'cn' else None))
    except Exception:
        snap.recent = []
    try:
        snap.my_rank = current_rank(storage, account=account)
    except Exception:
        snap.my_rank = None

    try:
        anchor = boot_anchor(storage) if boot_rowid is None else int(boot_rowid)
        row = pending_match(storage, anchor, snoozed)
    except Exception:
        row = None
    if row is not None:
        snap.pending = row
        snap.mode = MODE_PENDING
    return snap


def diff(prev: Optional[UiSnapshot], cur: Optional[UiSnapshot]
         ) -> List[str]:
    """哪几段变了 -> UI 只重绘这几段。``prev`` 为 None = 全变。"""
    if prev is None or cur is None:
        return list(SECTIONS)
    changed = []
    for name in SECTIONS:
        try:
            if prev.section(name) != cur.section(name):
                changed.append(name)
        except Exception:
            changed.append(name)
    return changed


# ----------------------------------------------------------------- 热力图
def months_of(daily: Optional[Dict[str, dict]]) -> List[str]:
    """``{'2026-09-02': {...}}`` -> ``['2026-09', ...]``（升序）。"""
    if not daily:
        return []
    return sorted({d[:7] for d in daily if isinstance(d, str) and len(d) >= 7})


def month_matrix(daily: Optional[Dict[str, dict]], month: str
                 ) -> List[List[Optional[dict]]]:
    """把某个月排成**周 × 星期**的格子阵（月度热力图直接照着画）。

    返回 ``[第1周, 第2周, …]``，每"周"是 7 个格子（**周一在前**），
    格子 = ``{'day': 2, 'n': 3, 'w': 2, 'l': 1, 'd': 0}``；
    月首之前的空位与月尾之后都是 ``None``（画成空格）。
    """
    out: List[List[Optional[dict]]] = []
    if not month or len(month) < 7:
        return out
    daily = daily or {}
    try:
        year, mon = int(month[:4]), int(month[5:7])
        days = calendar.monthrange(year, mon)[1]
        first_wd = calendar.monthrange(year, mon)[0]      # 0=周一
    except Exception:
        return out

    week: List[Optional[dict]] = [None] * first_wd
    for day in range(1, days + 1):
        key = '%s-%02d' % (month, day)
        cell = dict(daily.get(key) or _EMPTY_DAY)
        cell['day'] = day
        cell['date'] = key
        week.append(cell)
        if len(week) == 7:
            out.append(week)
            week = []
    if week:
        week += [None] * (7 - len(week))
        out.append(week)
    return out


# ===================================================== S6 统计窗（2026-09-17）
#: 四组的**固定顺序** —— 与 `Storage.get_group_stats()` 的返回顺序一致，别再写第二份。
MODE_GROUPS: Tuple[str, ...] = ('ranked', 'peak', 'dc', 'event')


def _mode_sel(mode_group):
    """模式筛选（单值 / 列表 / None）-> **集合**；``None`` / 空 = 不按模式过滤。

    ★ 与 SQL 端 `storage.mode_group_clause` 的语义对齐（2026-10-14 多选）：
    **空列表 = 「全部模式」**，Python 端等价于「不过滤」——因为
    :func:`_in_duel_calibre` 已经把单人/观战排除掉了，剩下的正是四组并集。
    非空列表/单值 -> 集合，Python 端按 ``g in sel`` 筛。

    ⚠️ 为什么要它：老代码写的是 ``g != mode_group``（单值比较）——多选后
    `mode_group` 是**列表**，"ranked" != ['ranked'] 恒真 → 一勾模式，对局记录
    与最高段位**全空**（2026-10-14 实测踩过）。
    """
    if not mode_group:
        return None
    if isinstance(mode_group, str):
        # ★ BUG-B1-10：`'all'` 在 SQL 端（`storage.mode_group_clause`）是
        #   「**不加模式条件**」= 不过滤 → Python 端也必须回 None。
        #   原来返回 `{'all'}`，`g in {'all'}` 恒假 → cross_matrix 有数据、
        #   match_groups 全空（同一筛选三处结果不一致）。
        return None if mode_group == MODE_GROUP_ALL else {mode_group}
    keys = set(mode_group)
    # 列表里带 'all' 与 SQL 端同一语义：整条筛选取消（不做模式过滤）
    if MODE_GROUP_ALL in keys:
        return None
    #: 「一个模式都没选」哨兵 -> 用哨兵本身当集合，`g in sel` 恒假（= 全筛掉）。
    if MODE_SELECT_NONE in keys:
        return {MODE_SELECT_NONE}
    return keys or None


def _mode_scope_note(mode_group) -> str:
    """模式筛选 -> 注脚用的口径串。``None`` = 主口径（天梯 + 巅峰）；
    空列表 = 「全部模式」（四组并集）；给了组 = 逐组名拼「 + 」。
    """
    from core.storage import MODE_GROUP_NAMES
    if mode_group is None:
        return CROSS_SCOPE_NOTE
    keys = [mode_group] if isinstance(mode_group, str) else list(mode_group)
    if MODE_SELECT_NONE in keys:
        return '未选择模式'               # 一个都没选（用户 2026-10-15 的「全部取消」态）
    keys = [k for k in keys if k in MODE_GROUP_NAMES]
    if not keys or set(keys) >= set(MODE_GROUPS):
        return '全部模式'                 # 空/全勾 = 四组并集 = 界面口径
    return ' + '.join(MODE_GROUP_NAMES[k] for k in MODE_GROUPS if k in keys)

#: **默认**（`mode_group=None`）交叉战绩的口径串 = 主口径。⚠️ 2026-09-19 起
#: `get_cross_stats()` **已收 `mode_group`**（旧裁定作废）——给了筛选就按筛选，
#: 注脚改由 :func:`_mode_scope_note` 现算。界面必须标出口径，否则用户会拿它
#: 跟「总览」（界面口径）对不上账。
CROSS_SCOPE_NOTE = '天梯 + 巅峰'


def interface_stats(storage, mode_group=None, since=None,
                    until=None, account=None) -> Dict[str, object]:
    """**界面口径**的单份统计（S6 顶部总览 / 筛选器联动用）。

    * ``mode_group=None`` = 「全部」= :func:`core.storage.duel_clause`
      （天梯 + 巅峰 + 各活动）—— ⚠️ **不是** ``counted_clause()``（只 3/19），
      两者在真库差 20 场（S6 计划 §3.3 那个坑）；
    * 给了分组 → ``mode_group=<组>``（与「分模式四组」构造同源）。

    ⚠️ **别用 ``mode_group='all'`` 表达「全部」**：那个只是「不加模式条件」，
    会把 ``game_mode IS NULL`` 的 20 场也算进来（真库实测 73 场）。
    """
    try:
        # ★ 2026-09-18 七轮：since/until 与 mode_group **可以同时给**（看板「战绩总览」
        #   的模式筛选要「本月 + 某模式」；`_stats_where` 本来就支持，是这里自己拆成两支）。
        #   `mode_group` 给了就按那一类单独统计（`_stats_where` 里它优先于 mode_scope）。
        return storage.get_stats(since=since, until=until, mode_group=mode_group,
                                 mode_scope=MODE_SCOPE_DUEL,
                                 account=account) or {}
    except Exception:
        return {}


def merged_daily(storage, mode_group=None, account=None) -> Dict[str, dict]:
    """按天聚合，**口径与「分模式四组」同源**（S6「按月」热力图的输入）。

    * 给了分组 → ``get_daily_stats(mode_group=…)``，一次查询；
    * 「全部」→ **逐组取 by-day 再合并**。

    为什么必须逐组：`get_daily_stats()` 的默认口径是**主口径**（真库 33 场），
    ``mode_group='all'`` 是 **73 场**，而四组合计才是 **53 场** —— 只有逐组合并
    与四组表**构造上同一套划分**，两块合计数才必然相等（否则同窗两个合计对不上）。
    """
    merged: Dict[str, dict] = {}
    try:
        for g in ((mode_group,) if mode_group else MODE_GROUPS):
            for day, v in (storage.get_daily_stats(mode_group=g,
                                                   account=account)
                           or {}).items():
                acc = merged.setdefault(day, {'n': 0, 'w': 0, 'l': 0, 'd': 0})
                for k in ('n', 'w', 'l', 'd'):
                    acc[k] += int(v.get(k) or 0)
    except Exception:
        return {}
    return merged


#: 逐组合并 by-deck 时要累加的**绝对值**键（比率一律最后重算，别加）
_DECK_ABS_KEYS = ('n_total', 'n_win', 'n_lose', 'n_draw',
                  'first_n', 'first_wins', 'second_n', 'second_wins')


def merged_deck_stats(storage, mode_group=None) -> List[Tuple[str, dict]]:
    """按我方卡组聚合，**口径与四组表同源**（S6「按我方卡组」块的输入）。

    ⚠️ `get_stats_by_deck()` **没有 `mode_scope` 参数** → 它的「全部」是主口径（33 场）。
    要拿到界面口径（53 场）只能**逐组取再按卡组名合并** —— 与 :func:`merged_daily`
    同一手法，同样**不碰 `core/storage.py`**。
    排序与 `get_stats_by_deck()` 一致：场次降序、同场次按首次出现时间升序。
    """
    accs: Dict[str, dict] = {}
    try:
        if mode_group:
            return list(storage.get_stats_by_deck(mode_group=mode_group) or [])
        for g in MODE_GROUPS:
            for name, s in (storage.get_stats_by_deck(mode_group=g) or []):
                acc = accs.setdefault(name, {})
                for k in _DECK_ABS_KEYS:
                    acc[k] = acc.get(k, 0) + int(s.get(k) or 0)
                for k in ('first_at', 'last_at'):
                    v = s.get(k)
                    if not v:
                        continue
                    cur = acc.get(k)
                    acc[k] = v if cur is None else (min(cur, v) if k == 'first_at'
                                                    else max(cur, v))
    except Exception:
        return []
    out: List[Tuple[str, dict]] = []
    for name, acc in accs.items():
        n, f_n, s_n = acc.get('n_total', 0), acc.get('first_n', 0), acc.get('second_n', 0)
        acc['win_rate'] = (acc.get('n_win', 0) / n) if n else None
        acc['first_rate'] = (acc.get('first_wins', 0) / f_n) if f_n else None
        acc['second_rate'] = (acc.get('second_wins', 0) / s_n) if s_n else None
        out.append((name, acc))
    out.sort(key=lambda t: (-t[1]['n_total'], t[1].get('first_at') or ''))
    return out


def stats_window_data(storage, mode_group=None, show_pseudo: bool = False
                      ) -> Dict[str, object]:
    """S6 统计窗的**一份全量数据**（窗口只画、不查库，与 :func:`snapshot` 同构）。

    * ``mode_group=None`` = 「全部」= 界面口径；给组名 = 只算那一组；
    * ``show_pseudo=False``（默认）= 交叉战绩滤掉 :data:`core.ui_actions.NON_DECK_LABELS`；
    * ⚠️ **「分模式四组」与「交叉战绩」两块不随筛选器变**：前者的标题就写着
      「全部历史」（它是跨模式对照表），后者恒为主口径（见 :data:`CROSS_SCOPE_NOTE`）；
    * 任何一块查失败都**不抛**（部分成功也算数 —— 与 `snapshot()` 同口径，
      界面宁可少一块，也不该因为一次查询异常整窗空白）。
    """
    data: Dict[str, object] = {
        'mode_group': mode_group,
        'overview': {},
        'groups': [],
        'daily': {},
        'months': [],
        'cross': [],
        'decks': [],
        'cross_scope': CROSS_SCOPE_NOTE,
    }
    # ⚠️ **逐块 try**（不是一处大 try）：某一块的查询炸了，别的块照样出数 ——
    #    整窗空白比少一块难受得多（与 `snapshot()` 的「部分成功也算数」同口径）。
    data['overview'] = interface_stats(storage, mode_group)      # 自己已 catch
    try:
        data['groups'] = list(storage.get_group_stats() or [])
    except Exception:
        pass
    data['daily'] = merged_daily(storage, mode_group)            # 自己已 catch
    data['months'] = months_of(data['daily'])
    data['decks'] = merged_deck_stats(storage, mode_group)       # 自己已 catch
    try:
        rows = list(storage.get_cross_stats() or [])
        if not show_pseudo:
            rows = [r for r in rows if r[1] not in NON_DECK_LABELS]
        data['cross'] = rows
    except Exception:
        pass
    return data


# ===================================================== S7 战绩页（D1）
#: 我方卡组读不出时的哨兵 —— **与 `core.storage._deck_expr()` 的字面量一致**
#: （那边是 SQL 里的 CASE 表达式，没有 Python 常量，所以这里也有一份；见 §7 的回归锁）
MY_DECK_UNKNOWN = '(未识别)'

#: 只算打完的局 —— 与 `core.storage.DONE_RESULTS` 同义（本模块不 import storage，保持鸭子类型）
_DONE_RESULTS = ('Win', 'Lose', 'Draw')


def _in_duel_calibre(m) -> bool:
    """行的**界面口径**判据 —— **镜像 `core.storage.duel_clause()`**。

    为什么要在 Python 里复刻：`get_recent_matches()` 不按口径过滤，而「对局记录」的
    每一行必须与组头（`merged_deck_stats`，界面口径）**对得上**，否则「组头 5-2、下面 7 行」。

    ⚠️ 复刻 SQL 有漂移风险 → `match_groups()` 配了「**行数合计 == 组头合计**」的回归锁兜着；
    真漂了测试会红，而不是界面悄悄错。口径本身（天梯+巅峰+DC+活动，含掉线兜底）不变。
    """
    if m.get('game_mode') is not None:
        # 单人模式（9）/ Duel Trial（15）是 PvE：口径与显示都排除（09-26 用户实测）
        return mode_group(m['game_mode']) != 'solo'
    return (m.get('mode') == 'pvp' and not m.get('ended_at'))    # 掉线兜底那一段


#: 段位探测的落盘窗口（`tools.duel_collector.RANK_WAIT_SEC` = 30s）+ 30s 余量。
#: UI 用它区分「段位获取中」与「这局本来就没有段位」（活动局等）。
RANK_PENDING_SEC = 60.0


def rank_pending(ended_at, game_mode) -> bool:
    """该局是否**还在段位探测窗口内**（收局后 120s 内的天梯/巅峰局）。

    为什么要有它（用户 2026-09-18 晚）：段位的真值要等游戏把结算写进
    ClientWork（探测窗口最长 ~90s），所以「最近对局」的行会先出来、
    段位晚一拍 —— 窗口内的空段位界面显示成「获取中」占位（`…`），
    窗口过了仍空才是真的没有（活动局快照按口径就是 null，永远不占位）。
    """
    if game_mode not in (3, 19):            # 只有天梯/巅峰有段位
        return False
    s = str(ended_at or '')
    if len(s) < 19:
        return False
    try:
        ended = datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False
    return (datetime.datetime.now() - ended).total_seconds() < RANK_PENDING_SEC


def split_datetime(started_at) -> Tuple[str, str]:
    """``'2026-09-16 23:27:00'`` -> ``('09-16', '23:27')``。

    ⚠️ **日期与时间分开是用户口径**（2026-09-18，Q16）：之后做导出时直接两列，
    不用再拆字符串。认不出的输入返回两个空串（**不猜**）。
    """
    s = str(started_at or '')
    if len(s) < 16:
        return ('', '')
    return (s[5:10], s[11:16])


def _recent_in_window(storage, limit: int = 500, since=None, until=None,
                      account=None) -> List[dict]:
    """时间窗内的最近 `limit` 局（DESC）—— **窗条件下推 SQL**（BUG-B1-09）。

    * 有 :meth:`Storage.get_matches_between` 就直接下推（首选；库超过窗口外
      的行数时也**不会截断**）；
    * 测试替身只实现了 `get_recent_matches` → 退回「取一批再在 Python 里筛」。
      回退路径的 `limit` 放大到 `max(limit, 5000)`：它只服务替身/老接口，
      放大一点换来「不会少算」，而真库永远走上面那条。
    * **不抛异常** —— 取数失败返回 `[]`（与各调用方原有的 try/except 同语义）。
    """
    fn = getattr(storage, 'get_matches_between', None)
    if callable(fn):
        try:
            return list(fn(since=since, until=until, limit=limit,
                           account=account) or [])
        except Exception:                                    # noqa: BLE001
            return []
    try:
        rows = list(storage.get_recent_matches(
            limit=max(int(limit), 5000), account=account) or [])
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    for m in rows:
        at = m.get('started_at') or ''
        if since and at < since:
            continue
        if until and at > until:
            continue
        out.append(m)
        if len(out) >= int(limit):
            break
    return out


def _page_row(m) -> dict:
    """一局 -> 对局记录的一行（字段就是界面那几列要用的，不多不少）。

    ★ **段位口径与悬浮窗「最近对局」同源**（用户 2026-09-18：**「对局记录应该和
    悬浮窗的最近对局的显示相同」**）—— 照抄 :func:`_recent_row` 那条判据：
    **段位只在天梯局算**，巅峰 / DC / 活动局一律**没有段位**。
    实测：活动局（`game_mode=21`）库里 14/21 行带着 `my_rank_after` —— 那是别处
    补回来的值，拿它当"这局的段位"显示就是错的（用户原话：
    「**为什么你的最近对局在活动局也显示了段位？**」）。模式另立一列去说。
    """
    from core import modes as M
    from core import ranks
    lang = _ui_lang()
    group = M.group(m.get('game_mode'))
    ranked = (group == 'ranked')
    rb = ((m.get('my_rank_before'), m.get('my_rate_before')) if ranked
          else (None, None))
    ra = ((m.get('my_rank_after'), m.get('my_rate_after')) if ranked
          else (None, None))
    date, time = split_datetime(m.get('started_at'))
    return {
        'match_id': m.get('match_id'),
        #: 完整时刻（`YYYY-MM-DD HH:MM:SS`）—— **只给导出排序用**：
        #: `date` 是 `MM-DD`，跨年时字符串比较会把 12 月排到 1 月后面
        #: （BUG-B1-12）。界面列仍用 date/time，别拿这个去显示。
        'started_at': m.get('started_at') or '',
        'date': date,
        'time': time,
        'result': m.get('result'),
        'went_first': m.get('went_first'),
        #: ★ 硬币（**赢得先后手选择权**）—— 与 `went_first` **不是一个概念**，
        #: 两个字段永久分开（`docs/交接说明.md` §3.1：本项目最大的认知陷阱）。
        #: ⚠️ **稀疏**：日志线只 3/5489，API 线 100%。读出来可能是 None。
        'won_initiative': m.get('won_initiative'),
        'opp_player': m.get('opp_player_name') or '',
        'total_turns': m.get('total_turns'),
        #: 模式列（用户 2026-09-18 新增）：分类 + 显示名（经 tr 跟语言走）
        'mode_group': group,
        'mode_label': _tr()(M.group_name(group)),
        'rank_before': rb if rb[0] else None,
        'rank_after': ra if ra[0] else None,
        'rank_delta': ranks.compare(rb, ra),
        'rank_label_before': ranks.rank_label(rb[0], rb[1]) if rb[0] else '',
        'rank_label_after': ranks.rank_label(ra[0], ra[1]) if ra[0] else '',
        'rank_icon_before': ranks.icon_path(rb[0]) if rb[0] else None,
        #: 段位格画的是这局**局后**的图标（没有局后就退回局前，见界面的取法）
        'rank_icon_after': ranks.icon_path(ra[0]) if ra[0] else None,
        #: 小段位罗马数字（与悬浮窗 `RankBadge` 同一套：升/掉段画局后那个）
        'badge_numeral': ranks.numeral_text(rb[1]) if rb[0] else '',
        'badge_numeral_after': ranks.numeral_text(ra[1]) if ra[0] else '',
        #: 段位「获取中」占位（收局后 120s 内的天梯局，探测还没落盘）
        'rank_pending': rank_pending(m.get('ended_at'), m.get('game_mode')),
        # ★ 巅峰积分（2026-09-28）：战绩页段位列的巅峰行画「巅峰图标 + 积分」
        # （after 优先 / before 兜底）；格式化好的字符串，非巅峰行恒 None
        'peak_rate': ('%.2f' % float(m.get('peak_rate_after')
                                     if m.get('peak_rate_after') is not None
                                     else m.get('peak_rate_before')))
                      if (group == 'peak'
                          and (m.get('peak_rate_after') is not None
                               or m.get('peak_rate_before') is not None))
                      else None,
        'opp_deck': m.get('opp_deck_name') or '',
        'opp_known': bool(m.get('opp_deck_name')),
        #: M5 反推的输入（点了对手卡组格要拿它算建议，见 app.on_pick_deck）
        'opp_revealed_ids': m.get('opp_revealed_ids'),
        'opp_deck_id': m.get('opp_deck_id'),
        # 手坑两列（2026-10-16）：None = 判不出/无逐卡数据（界面画「—」）；
        # [] = 确实没用过（界面留空）。**两种语义别混**（见 core/duel_record.py）。
        'my_traps': _trap_items(m.get('my_traps'), lang),
        'opp_traps': _trap_items(m.get('opp_traps'), lang),
    }


def match_groups(storage, limit: int = 500, since=None, until=None,
                 current_start=None, mode_group=None, account=None):
    """对局记录 -> ``[(我方卡组, 组头 stats, [行 dict, ...]), ...]``（D1）。

    * **组头**直接来自 :func:`merged_deck_stats()`（**界面口径**，53 场那套）；
    * **组内行**按 `my_deck_name` 分桶，并**镜像界面口径**过滤（见 :func:`_in_duel_calibre`）；
    * ★ ``mode_group``（2026-09-18 七轮，用户：「按模式筛选对局进行统计」）：
      只留该模式的局（None = 全部）。判据**同源** `core.modes.group()`，别在界面再写一份；
      **对局记录必须跟「战绩总览」的筛选走**，否则总览 33 场、下面 53 场对不上。
    * 行按 `started_at` **倒序**（近的在上）；组按场次降序（与组头顺序一致）；
    * 查不动（库里炸了）返回 `[]` —— 不抛。
    """
    from core import modes as M
    # ★ BUG-B1-09：since/until 下推 SQL（同上；回退路径见 `_recent_in_window`）
    rows = _recent_in_window(storage, limit=limit, since=since, until=until,
                             account=account)
    if not rows:
        return []
    sel = _mode_sel(mode_group)          # None = 不筛；集合 = 只留勾选的模式
    keep = []
    for m in rows:
        if m.get('result') not in _DONE_RESULTS or not _in_duel_calibre(m):
            continue
        if sel:
            g = M.group(m.get('game_mode'))
            #: ⚠️ `ranked` 的 SQL 口径**含掉线兜底局**（`game_mode IS NULL` + pvp 未结束，
            #: `mode_group_clause` 里拼了 `disconnect_clause()`）—— Python 端
            #: `modes.group(None)` 是 None，不补这条就"总览 33、记录 32"对不上。
            fallback = ('ranked' in sel and g is None
                        and m.get('mode') == 'pvp' and not m.get('ended_at'))
            if g not in sel and not fallback:
                continue                  # 模式筛选（用户 2026-09-18 七轮）
        at = m.get('started_at') or ''
        if since and at < since:
            continue                      # 早于窗口（本月）的不算
        if until and at > until:
            continue
        keep.append(m)

    # ★ 先按**轮**切开（用户 2026-09-18：按轮是**分组方式**，数据范围仍是本月）
    sessions = split_sessions(keep)
    sessions.reverse()                    # 最新一轮在前

    out = []
    for si, sess in enumerate(sessions):
        by_deck = {}
        for m in sess:
            name = (m.get('my_deck_name') or '').strip() or MY_DECK_UNKNOWN
            by_deck.setdefault(name, []).append(m)
        #: 同一轮里，哪个卡组最近打过哪个组排前面
        ordered = sorted(by_deck.items(),
                         key=lambda kv: max(x.get('started_at') or '' for x in kv[1]),
                         reverse=True)
        for name, items in ordered:
            items.sort(key=lambda m: m.get('started_at') or '', reverse=True)
            st = group_header(items)
            st['round_start'] = sess[0].get('started_at')
            st['is_current'] = bool(si == 0 and current_start
                                    and (sess[0].get('started_at') or '') == current_start)
            st['round_label'] = round_label(sess[0].get('started_at'),
                                            st['is_current'])
            out.append((name, st, [_page_row(m) for m in items]))
    return out


def split_sessions(rows):
    """把（时间升序的）行切成**一轮一轮** -> ``[[row, ...], ...]``（最后一轮在末尾）。

    判据与悬浮窗**同源**：看「上一局**结束** → 本局开局」的间隔 ≥ `SESSION_GAP_MIN`（60 分钟）。
    直接复用 `core.storage.end_time_of()` 的**三层回退**（ended_at > started+duration > started）
    与 `SESSION_GAP_MIN` —— **别在这里重写一套**（写两套必然漂）。

    ⚠️ 为什么要有它：用户 2026-09-18 的口径是
    「**对局记录显示本月数据**，但**按轮次分组**」——
    同一副卡组跨轮要分成两组（今天 10 把闪刀姬 + 下一轮 2 把 = 两组，**两组都显示**），
    所以既要「本月全量」又要「轮次边界」。
    """
    from core.storage import SESSION_GAP_MIN, end_time_of, parse_dt
    ordered = sorted(rows, key=lambda m: m.get('started_at') or '')
    out, cur, prev_end = [], [], None
    for m in ordered:
        sd = parse_dt(m.get('started_at'))
        if sd is None:
            continue                       # 坏数据跳过（坏一局不该让整页挂）
        if prev_end is not None and \
                (sd - prev_end).total_seconds() >= SESSION_GAP_MIN * 60:
            out.append(cur)
            cur = []
        cur.append(m)
        ed = parse_dt(end_time_of(m.get('started_at'), m.get('ended_at'),
                                 m.get('duration_sec')))
        prev_end = ed if ed is not None else sd
    if cur:
        out.append(cur)
    return out


def round_label(started_at, is_current: bool) -> str:
    """轮次标签：当前那轮 = 「本轮」；其它轮 = 「MM-DD HH:MM 起」。"""
    s = str(started_at or '')
    if is_current:
        return '本轮'
    return ('%s 起' % s[5:16]) if len(s) >= 16 else ''


def group_header(rows) -> dict:
    """组头统计 —— **直接从本轮的行算**（n / 胜负 / 先手后手各自的胜负）。"""
    n = len(rows)
    w = sum(1 for r in rows if r.get('result') == 'Win')
    l = sum(1 for r in rows if r.get('result') == 'Lose')
    d = sum(1 for r in rows if r.get('result') == 'Draw')
    f_n = sum(1 for r in rows if r.get('went_first') is True)
    f_w = sum(1 for r in rows if r.get('went_first') is True
              and r.get('result') == 'Win')
    s_n = sum(1 for r in rows if r.get('went_first') is False)
    s_w = sum(1 for r in rows if r.get('went_first') is False
              and r.get('result') == 'Win')
    return {
        'n_total': n, 'n_win': w, 'n_lose': l, 'n_draw': d,
        'win_rate': (w / n) if n else None,
        'first_n': f_n, 'first_wins': f_w,
        'first_rate': (f_w / f_n) if f_n else None,
        'second_n': s_n, 'second_wins': s_w,
        'second_rate': (s_w / s_n) if s_n else None,
    }


def month_window(day=None) -> Tuple[str, str, str]:
    """某天的**本月窗口** -> ``(月份 'YYYY-MM', 月初时刻, 月末时刻)``（含首含尾）。

    ⚠️ 用户 2026-09-18 口径：**「战绩总览」按月统计**（对局记录才按轮）。
    时刻用定宽文本（与 `started_at` 的字符串比较口径一致，见 `core.storage.day_range`）。
    """
    import datetime as _dt
    d = day or _dt.date.today()
    if isinstance(d, str):
        d = _dt.date.fromisoformat(d[:10])
    first = d.replace(day=1)
    nxt = (first + _dt.timedelta(days=32)).replace(day=1)
    last = nxt - _dt.timedelta(days=1)
    # ★ BUG-B1-07：上界必须带毫秒（与 `storage.day_range` 的 `.999` 同口径）。
    #   原来写 `23:59:59`，字符串比较下 `23:59:59.800` > `23:59:59` ——
    #   月末最后一局（带毫秒的日志行）被整月漏算。10 月真数据有 2 行带毫秒。
    return ('%04d-%02d' % (first.year, first.month),
            first.strftime('%Y-%m-%d 00:00:00'),
            last.strftime('%Y-%m-%d 23:59:59.999'))


def stats_page_data(storage, limit: int = 500, mode_group=None,
                    month=None, account=None) -> Dict[str, object]:
    """「战绩」页的**一份全量数据**（窗口只画、不查库；与 `stats_window_data` 同构）。

    D1 三块：① 月历热力图（`daily`/`months`）② 战绩总览五格（`overview` + `best_rank`）
    ③ 对局记录（`groups`）。

    ★ ``mode_group``（2026-09-18 七轮，用户：「按模式筛选对局进行统计」）：
    「战绩总览五格」与「对局记录」都按它筛（None = 全部）；**热力图不跟**
    （它是"全部历史"的日历视图，口径已拍板别再改）。

    ★ ``month``（2026-10-01，用户：「点热力图切换月份查看」）：``'YYYY-MM'`` =
    整页看那个月（总览 / 最高段位 / 对局记录三块都切过去）；``None`` = 本月。
    热力图两块（daily/months）仍画**全部历史**，月卡选中态由 UI 层拿这里的
    `month` 对出来。
    """
    data: Dict[str, object] = {
        'overview': {}, 'best_rank': None, 'daily': {}, 'months': [],
        'groups': [], 'row_total': 0, 'group_total': 0,
        'session_since': None, 'session_live': True, 'last_match_at': None,
        'month': None, 'month_since': None, 'mode_group': mode_group,
        'wins_to_promote': None,
        'account': account, 'accounts': [],
    }
    # ★ 本轮起点（用户 2026-09-18：「按轮次分」「不统计上一轮的对局」）：
    #   本轮结束时给**未来起点**（`NO_SESSION_SINCE`）→ `get_stats` 自然返回零值形态，
    #   与悬浮窗那两块的口径**一模一样**（别用 since=None，那是「不限时间」= 全库）。
    try:
        start = stats_since(storage, 'session')
    except Exception:
        start = SESSION_EXPIRED
    live = start is not SESSION_EXPIRED
    since = start if live else NO_SESSION_SINCE
    data['session_since'] = None if not live else start
    data['session_live'] = live
    # ★ 「战绩总览」= **本月**（用户 2026-09-18：「这个页面是按月统计的」）——
    #   只有**对局记录**按轮次；两者口径不同，标题里各自标出来。
    try:
        month, m_since, m_until = (
            month_window(str(month) + '-01') if month else month_window())
    except Exception:
        month, m_since, m_until = month_window()     # 月份串不合法 → 回本月
    data['month'] = month
    data['month_since'] = m_since
    data['overview'] = interface_stats(storage, mode_group=mode_group,
                                       since=m_since, until=m_until,
                                       account=account)  # 自己已 catch
    # 热力图**仍看全部历史**（它是日历视图，只留本轮就只剩一天有格子）；
    # 多账号（2026-10-03）：热力图跟账号筛 —— 切账号整页换人
    data['daily'] = merged_daily(storage, account=account)  # 自己已 catch
    data['months'] = months_of(data['daily'])
    try:
        # ★ BUG-B1-09：时间窗**下推到 SQL**（原来先取 `limit=500` 行再在
        #   Python 里按月筛 —— 库超过 500 行后月报会静默少算；真库 340 行
        #   当前不触发）。
        rows = _recent_in_window(storage, limit=limit, since=m_since,
                                 until=m_until, account=account)
        #: 全库最近一局的开始时间 —— 空态文案要告诉用户「上一轮停在什么时候」
        newest = _recent_in_window(storage, limit=1, account=account)
        if newest:
            data['last_match_at'] = newest[0].get('started_at')
        elif rows:
            data['last_match_at'] = rows[0].get('started_at')
        from core import modes as M
        from core import ranks
        #: 最高段位 = **本月**最高（与「战绩总览」同口径 —— 这块整体按月）。
        #: ★ 七轮修正：只算**天梯局**的段位 —— 活动局那些别处补回来的
        #: `my_rank_after` 是假段位，不该参与"最高"；筛选到非天梯模式时自然为 None
        #: （界面那格显示 `-`，不是错误）。
        sel = _mode_sel(mode_group)
        # ★ BUG-B1-06：**上下界都要卡**。原来只卡下界，看 9 月时会把这个月
        #   （10 月）的段位也算进「本月最高段位」。上界与 since 同源（`m_until`，
        #   带 `.999` 见 BUG-B1-07）。
        mine = [m for m in rows
                if m_since <= (m.get('started_at') or '') <= m_until
                and M.group(m.get('game_mode')) == 'ranked'
                and (sel is None or M.group(m.get('game_mode')) in sel)]
        pairs = [((m.get('my_rank_after'), m.get('my_rate_after'))) for m in mine]
        before = [((m.get('my_rank_before'), m.get('my_rate_before'))) for m in mine]
        data['best_rank'] = ranks.best(list(pairs) + list(before))
    except Exception:
        pass
    # ★ 「还剩 N 胜升段」（2026-09-18 晚，段位推断链的状态；-1 = 未知 -> None）
    try:
        from core import rank_state
        w = rank_state.wins_to_promote(rank_state.load())
        data['wins_to_promote'] = w if w is not None and w >= 0 else None
    except Exception:
        data['wins_to_promote'] = None
    # ★ 对局记录：**本月**数据 + **按轮次分组** + **模式筛选**（用户 2026-09-18 口径）
    data['groups'] = match_groups(storage, limit=limit, since=m_since,
                                  until=m_until, mode_group=mode_group,
                                  current_start=start if live else None,
                                  account=account)
    if _ui_lang() != 'cn':
        # 显示名按语言（组头=我方卡组、行=对手卡组列）；失败不抛，回退原值
        #（导出走 match_groups 本体，不经过这里 —— 导出保持库内原值）
        try:
            resolve = _name_resolver(storage, _ui_lang())
            new_groups = []
            for name, st, rows in data['groups']:
                new_rows = []
                for r in rows:
                    od = r.get('opp_deck')
                    if od:
                        r = dict(r, opp_deck=resolve(od) or od)
                    new_rows.append(r)
                new_groups.append((resolve(name) or name, st, new_rows))
            data['groups'] = new_groups
        except Exception:                                    # noqa: BLE001
            pass
    # 账号清单（切换器用；查不动给 []）
    try:
        data['accounts'] = storage.account_options() or []
    except Exception:
        data['accounts'] = []
    data['group_total'] = sum(int((s or {}).get('n_total') or 0)
                              for _n, s, _r in data['groups'])
    data['row_total'] = sum(len(r) for _n, _s, r in data['groups'])
    return data


# ===================================================== 对局分析页（D2）


def hand_trap_summary(storage, month=None, mode_group=None) -> Dict[str, object]:
    """手坑汇总（对局分析页「手坑汇总」视图用）—— **两张表**（用户 2026-10-16 口径）。

    行 = 我方卡组，列 = 12 张手坑，格子 = **我的胜率**（+ 小字场数）：

    * 表 A ``second_my``：「**我后手** · 我用手坑」—— 我后手时，前两回合（turn 0/1）
      **我**用了某坑的局 → 我的胜率；
    * 表 B ``first_opp``：「**我先手** · 敌用手坑」—— 我先手时，前两回合
      **对方**用了某坑的局 → 我的胜率。

    口径与旧版一致：**界面口径**（`match_groups` 那套），只算有逐卡数据的局；
    turn 范围 = `duel_record.HAND_TRAP_TURNS`（= `(0,1)`，先攻/后攻各自的第一回合）。
    """
    from core import hand_traps as _ht
    from core import duel_record as _dr
    since = until = None
    if month:
        try:
            since, until = month_bounds(str(month))
        except Exception:                                    # noqa: BLE001
            since = until = None
    traps = [{'key': k, 'name_cn': _ht.name_cn(k), 'name': _trap_display(k),
              'group': _ht.group_of(k)} for k in _ht.TRAPS]
    empty = {'traps': traps, 'second_my': [], 'first_opp': [],
             'n_games': 0, 'n_calibre': 0, 'month': month,
             'mode_group': mode_group}
    # ★ BUG-B1-09：按月筛的那一版原来取 5000 行再在 Python 里卡窗口
    rows = _recent_in_window(storage, limit=5000, since=since, until=until)
    if not rows:
        return empty

    from core import modes as M
    sel = _mode_sel(mode_group)

    def _keep(m):
        if m.get('result') not in _DONE_RESULTS or not _in_duel_calibre(m):
            return False
        if sel:
            g = M.group(m.get('game_mode'))
            fallback = ('ranked' in sel and g is None
                        and m.get('mode') == 'pvp' and not m.get('ended_at'))
            if g not in sel and not fallback:
                return False
        at = m.get('started_at') or ''
        if since and at < since:
            return False
        if until and at > until:
            return False
        return True

    calibre = [m for m in rows if _keep(m)]
    usable = [m for m in calibre
              if m.get('my_traps') is not None and m.get('opp_traps') is not None]
    valid = _dr.HAND_TRAP_TURNS
    keys = [t['key'] for t in traps]

    #: 表 A / 表 B 各按我方卡组分桶：deck -> {key: [n,w,l]}
    def _blank():
        return {k: [0, 0, 0] for k in keys}
    buckets = {'second_my': {}, 'first_opp': {}}

    for m in usable:
        wf = m.get('went_first')
        if wf is None:
            continue                       # 先/后判不出 -> 两张表都不收
        deck = (m.get('my_deck_name') or '').strip() or MY_DECK_UNKNOWN
        if wf is False:                    # 我后手 -> 表 A「我用手坑」
            side, table = m.get('my_traps') or [], 'second_my'
        else:                              # 我先手 -> 表 B「敌用手坑」
            side, table = m.get('opp_traps') or [], 'first_opp'
        b = buckets[table].setdefault(deck, {'all': 0, 'cells': _blank()})
        b['all'] += 1
        seen = set()
        for t in side:
            k = t.get('key')
            if k not in keys or t.get('turn') not in valid or k in seen:
                continue                   # 同一局同一张坑只记一次
            seen.add(k)
            cell = b['cells'][k]
            cell[0] += 1
            if m.get('result') == 'Win':
                cell[1] += 1
            elif m.get('result') == 'Lose':
                cell[2] += 1

    def _rows(table):
        out = []
        for deck, b in buckets[table].items():
            cells = {}
            for k in keys:
                n, w, l = b['cells'][k]
                cells[k] = {'n': n, 'w': w, 'l': l,
                            'rate': (100.0 * w / (w + l)) if (w + l) else None}
            out.append({'deck': deck, 'n_total': b['all'], 'cells': cells})
        out.sort(key=lambda r: (-int(r['n_total'] or 0), r['deck']))
        return out

    return {'traps': traps, 'second_my': _rows('second_my'),
            'first_opp': _rows('first_opp'),
            'n_games': len(usable), 'n_calibre': len(calibre),
            'month': month, 'mode_group': mode_group}


def _trap_display(key) -> str:
    """手坑 key -> 当前语言显示名（缺该语言顺延到中文名）。"""
    from core import hand_traps as _ht
    lang = _ui_lang()
    sid = _ht.standard_id(key)
    try:
        from core.card_names import display
        return display(sid, lang) or _ht.name_cn(key) or key
    except Exception:                                        # noqa: BLE001
        return _ht.name_cn(key) or key


def _acc() -> Dict[str, int]:
    """一个视角的计数器（总 + 先攻 + 后攻）。"""
    return {'n': 0, 'w': 0, 'l': 0, 'd': 0,
            'f_n': 0, 'f_w': 0, 'f_l': 0, 'f_d': 0,
            's_n': 0, 's_w': 0, 's_l': 0, 's_d': 0}


def _acc_add(a: Dict[str, int], result, went_first) -> None:
    a['n'] += 1
    if result == 'Win':
        a['w'] += 1
    elif result == 'Lose':
        a['l'] += 1
    else:
        a['d'] += 1
    if went_first is None:
        return
    pre = 'f_' if went_first else 's_'
    a[pre + 'n'] += 1
    if result == 'Win':
        a[pre + 'w'] += 1
    elif result == 'Lose':
        a[pre + 'l'] += 1
    else:
        a[pre + 'd'] += 1


def _acc_finish(a: Dict[str, int], total: int) -> Dict[str, object]:
    """计数器 -> 带胜率的出口形状（胜率 = 胜 / (胜+负)，平局不算）。"""
    def rate(w, l):
        n = w + l
        return (100.0 * w / n) if n else None
    return {
        'n': a['n'], 'w': a['w'], 'l': a['l'], 'd': a['d'],
        'pct_of_total': (100.0 * a['n'] / total) if total else None,
        'rate': rate(a['w'], a['l']),
        'first': {'n': a['f_n'], 'w': a['f_w'], 'l': a['f_l'], 'd': a['f_d'],
                  'rate': rate(a['f_w'], a['f_l'])},
        'second': {'n': a['s_n'], 'w': a['s_w'], 'l': a['s_l'], 'd': a['s_d'],
                   'rate': rate(a['s_w'], a['s_l'])},
    }


def month_bounds(ym: str) -> Tuple[str, str]:
    """'YYYY-MM' -> ``(月初时刻, 月末时刻)``（含首含尾；定宽文本比较口径）。

    与 :func:`month_window` 同口径，但接受**任意月份** —— D2「对局分析」的
    日期筛选按月（用户 2026-09-19 拍板），月份清单 = 战绩页热力图那批。
    """
    import datetime as _dt
    y, m = int(ym[:4]), int(ym[5:7])
    first = _dt.date(y, m, 1)
    nxt = (first + _dt.timedelta(days=32)).replace(day=1)
    last = nxt - _dt.timedelta(days=1)
    # ★ BUG-B1-07：上界带毫秒（同 `month_window` / `storage.day_range`）
    return (first.strftime('%Y-%m-%d 00:00:00'),
            last.strftime('%Y-%m-%d 23:59:59.999'))


def cross_matrix(storage, went_first=None, hide_pseudo: bool = True,
                 month=None, mode_group=None) -> Dict[str, object]:
    """「对局分析」页的**一份全量数据**（列 = 我方卡组、行 = 对手卡组的稀疏矩阵）。

    ★ 方向 v4（2026-09-19 用户拍板）：**我方卡组只有三四副、对手卡组几十个 ——
    少的做列**。每列的汇总（我方卡组的综合胜率）给在 ``col_avgs``；
    每行的总场数给在行 dict 的 ``n``（行首场数显示用）。

    * 口径 = `get_cross_stats()` 的**主口径**（天梯 + 巅峰，见
      :data:`CROSS_SCOPE_NOTE`）——与「战绩」页（界面口径）合计必然不等，
      页头警示就是防这个；
    * ★ **行列轴按「综合」页签计算并固定**（审查 P2-1）：两轴滤伪条目 +
      空行/空列剪枝只对 ``went_first=None`` 做一次；先攻/后攻复用同一行列轴、
      只换格内数据 —— 切页签不跳版、行列序不重排（用户：「空不要紧，格式对就行」）；
    * ``col_avgs`` = **场次加权** Σw/Σn，不做各格简单平均（旧项目
      `游戏王战绩统计` 的口径教训）；``rate = w/n``，**平局算非胜**
      （与 `get_stats` 的 win_rate 同口径，审查已确认）；
    * ``month``（'YYYY-MM'）与 ``mode_group``（ranked/peak/dc/event/all）= v5
      筛选器（用户 2026-09-19 拍板：日期按月、模式分四类、**不做段位筛选**）；
      行列轴在**当前筛选**的综合口径下计算并固定；
    * ``months`` = 战绩页热力图那批月份（倒序），给日期下拉当选项；
    * 任何失败不抛 —— 零值形态里 note 只剩口径文案（与 D1 同款）。
    """
    data: Dict[str, object] = {
        'note': '统计口径：%s' % _mode_scope_note(mode_group),
        'went_first': went_first, 'month': month, 'mode_group': mode_group,
        'cols': [], 'rows': [], 'col_avgs': [], 'months': [],
        'total_calibre': 0, 'total_in_matrix': 0,
        #: ★ 2026-10-16：本页第二个视图「手坑汇总」（四组 × 三视角 × 胜负交叉）。
        #: **在这里算**（而不是让 app 的 loader 补）—— 否则 demo 与真函数的键集合
        #: 会对不上（tests/test_ui_demo 的契约锁）。失败退零值形态。
        'hand_traps': {},
    }
    try:
        data['hand_traps'] = hand_trap_summary(storage, month=month,
                                               mode_group=mode_group)
    except Exception:                                        # noqa: BLE001
        data['hand_traps'] = {'groups': [], 'n_games': 0}
    try:
        data['months'] = list(reversed(months_of(merged_daily(storage))))
    except Exception:
        pass                                    # 月份下拉空着也不影响矩阵
    since = until = None
    if month:
        try:
            since, until = month_bounds(month)
        except Exception:
            since = until = None
    try:
        axis_rows = list(storage.get_cross_stats(since=since, until=until,
                                                 mode_group=mode_group) or [])
    except Exception:
        return data
    data['total_calibre'] = sum(int(r[2] or 0) for r in axis_rows)
    if went_first is None:
        cell_rows = axis_rows                      # 综合 = 一次查询就够
    else:
        try:
            cell_rows = list(storage.get_cross_stats(
                since=since, until=until, went_first=went_first,
                mode_group=mode_group) or [])
        except Exception:
            cell_rows = []

    # ---- 出口翻译（轴/格/列汇总共用一份 —— 2026-10-04 用户：名字跟着语言走）----
    _resolve = _name_resolver(storage, _ui_lang())
    _disp: Dict[str, str] = {}

    def _d(name: str) -> str:
        if _resolve is None:
            return name
        if name not in _disp:
            _disp[name] = _resolve(name) or name
        return _disp[name]

    # ---- 轴（当前筛选的综合口径，固定）：列=我方、行=对手；滤伪条目 + 空行列剪枝 ----
    #: ★🔴 2026-10-15 修复：分组键要用**显示名**，不是原始名。
    #:   两个不同 opp_deck_id 可能显示成同一个中文名（`Branded` 与 `烙印` 都显示
    #:   「烙印」）—— 老代码按**原始名**分桶、再按显示名出图，于是同一显示名在
    #:   `cell_map` 里**互相覆盖**（后一条盖前一条）→ 显示场数比真值少（「烙印」实测
    #:   显示 5 场、实际 9 场），而行序按真值排 → 看着不降序（用户 2026-10-15 报
    #:   「行排列让我看不懂」）。改为一律按 `_d(名)` 归并：同名的合一行。
    col_names, row_names = [], []                  # 列=我方卡组、行=对手卡组
    col_seen, row_seen = set(), set()
    col_n: Dict[str, int] = {}
    row_n: Dict[str, int] = {}
    for my, opp, n, _w, _l, _d_ in axis_rows:
        # ★ 09-28 用户口径：**「未知卡组」要显示**（巅峰局看不了对面 = 真实的一局）；
        #   仍隐藏的只剩我方「(未识别)」与对手「(未标注)」——那是没有信息的占位。
        if hide_pseudo and (my == MY_DECK_UNKNOWN or opp == '(未标注)'):
            continue
        myk, oppk = _d(my), _d(opp)                #: 归并键 = 显示名（同名合并）
        if myk not in col_seen:
            col_seen.add(myk)
            col_names.append(myk)
            col_n[myk] = 0
        if oppk not in row_seen:
            row_seen.add(oppk)
            row_names.append(oppk)
            row_n[oppk] = 0
        col_n[myk] += int(n or 0)
        row_n[oppk] += int(n or 0)
    col_names.sort(key=lambda x: (-col_n[x], x))   # 列序 = 列（我方）总场次降序
    row_names.sort(key=lambda x: (-row_n[x], x))   # 行序 = 行（对手）总场次降序
    data['cols'] = list(col_names)                 # 已是显示名，不用再翻
    data['total_in_matrix'] = sum(col_n.values())

    # ---- 格（当前页签口径）：按**归并键**累加（同名多行是相加，不是覆盖）----
    cell_map: Dict[Tuple[str, str], List[int]] = {}
    for my, opp, n, w, l, d in cell_rows:
        myk, oppk = _d(my), _d(opp)
        if myk in col_seen and oppk in row_seen:
            acc = cell_map.setdefault((myk, oppk), [0, 0, 0, 0])
            acc[0] += int(n or 0)
            acc[1] += int(w or 0)
            acc[2] += int(l or 0)
            acc[3] += int(d or 0)
    rows: List[Dict[str, object]] = []
    for oppk in row_names:
        cells = []
        n_sum = 0
        for myk in col_names:
            got = cell_map.get((myk, oppk))
            if got is None:
                continue
            n, w, l, d = got
            n_sum += n
            cells.append({'deck': myk, 'n': n, 'w': w, 'l': l, 'd': d,
                          'rate': (w / n * 100.0) if n else None})
        #: ``n`` = 这个对手在当前筛选+页签下的总场数（行首「场数」显示用）；
        #:   行序键 `row_n` 也是它 —— 两者同源，不再出现「排序与显示不一致」。
        rows.append({'deck': oppk, 'n': n_sum, 'cells': cells})
    data['rows'] = rows
    #: 列汇总（我方卡组的综合/先攻/后攻胜率）—— UI 放在列头下第一行
    data['col_avgs'] = [
        {'deck': myk,
         'n': sum(c[0] for (my_, _o), c in cell_map.items() if my_ == myk),
         'w': sum(c[1] for (my_, _o), c in cell_map.items() if my_ == myk)}
        for myk in col_names]
    for a in data['col_avgs']:
        a['rate'] = (a['w'] / a['n'] * 100.0) if a['n'] else None
    scope = _mode_scope_note(mode_group)
    #: 「主口径」这层标签只在**默认**（`None` = 天梯 + 巅峰那套）下出现 —— 勾了
    #: 某个模式再说「主口径」就是错的（那串数字是筛选后的）。
    label = ('主口径 %d 场' if mode_group is None else '%d 场')
    #: ⚠️ 「与战绩页合计不等」那句**只在默认口径下成立**（战绩页走界面口径）——
    #: 两页都勾了同一模式时两边本就该一致，别再挂这句话。
    tail = ('——与「战绩」页（界面口径）合计不等，不是 bug'
            if mode_group is None else '')
    if hide_pseudo and data['total_in_matrix'] != data['total_calibre']:
        data['note'] = ('统计口径：%s（%s · 矩阵内 %d 场，已隐藏未标注）%s'
                        % (scope, label % data['total_calibre'],
                           data['total_in_matrix'], tail))
    else:
        data['note'] = ('统计口径：%s（%s）%s'
                        % (scope, label % data['total_calibre'], tail))
    return data


# ===================================================== Tier 表页（D3 第一刀）

#: 分组展示顺序与说明（power 区间照 masterduelmeta 的 tierlist 口径）
TIER_SECTIONS = (
    ('Tier1', 'TIER 1', 'Power ≥ 12 · 赛事上位卡组'),
    ('Tier2', 'TIER 2', 'Power 7 ~ 12'),
    ('Tier3', 'TIER 3', 'Power 3 ~ 7'),
    ('Trending', 'TRENDING', 'Power 1 ~ 3'),
)


def tier_page_data(storage, lang=None) -> Dict[str, object]:
    """「Tier 表」页的**一份全量数据**（窗口只画、不查库）。

    * 数据源 = 本地 `meta_decks` 表（`tools/fetch_meta_decks.py` **每月手动**刷新，
      默认已滤 hidden）—— 本页**纯只读**，不联网；
    * 显示名 = **中文名优先**（`name_cn` → 覆盖表显示英文名 `name_display` → 站内名
      `name`），卡图由 app 侧 loader 注入 `img`（与 D2 同款纪律）；
    * `tier` 不在 :data:`TIER_SECTIONS` 里的脏数据**不显示**；
    * 任何失败不抛 —— sections 照常给（空分组），decks 为空。
    """
    lang = lang or _ui_lang()
    data: Dict[str, object] = {
        'sections': [{'tier': t, 'title': ti, 'desc': d, 'decks': []}
                     for t, ti, d in TIER_SECTIONS],
        'deck_total': 0,
    }
    try:
        # hidden 的 **engine 行要进**（Tier 表要列出全部混轴，2026-09-20 用户拍板）；
        # 其余 hidden（手工隐藏的条目）照旧不显示
        decks = [m for m in storage.list_meta_decks(include_hidden=True) or []
                 if not (m.get('hidden') and m.get('kind') != 'engine')]
    except Exception:
        return data
    by_tier = {s['tier']: s for s in data['sections']}
    for m in decks:
        # ★ 分组口径（AGENTS.md 铁律 7）：
        #   * **站内条目**只认站内 power —— 没有 power 的一律落 TRENDING
        #     （2026-09-20 用户拍板；tier 字段可能是旧榜的陈旧值，不作数）；
        #   * **自定义条目**（`source='custom'`，站内没有）没有 power 可依，
        #     分组以覆盖表的「分组」列为准（BUG-B1-12）—— 否则手填的
        #     `Tier2` / `Tier3` 全被压成 Trending，等于忽略用户分组。
        if m.get('source') == 'custom' and m.get('tier') in by_tier:
            t = m.get('tier')
        else:
            t = 'Trending' if m.get('power') is None else m.get('tier')
        if t not in by_tier:
            continue                          # 脏 tier（合法值见 storage.VALID_TIERS）
        by_tier[t]['decks'].append({
            'name': m.get('name') or '',
            'display': _deck_display_row(m, lang),
            'power': m.get('power'),
        })
    data['deck_total'] = sum(len(s['decks']) for s in data['sections'])
    return data


# ===================================================== 禁限表页（D3 第二刀）

#: 状态显示名（**唯一来源 = tools/fetch_banlist.STATUS_CN**，这里不另写一份映射）
def _status_label(value, lang='cn') -> str:
    from tools.fetch_banlist import status_label
    return status_label(value, lang)


def banlist_page_data(storage, limit: int = 300, lang=None) -> Dict[str, object]:
    """「禁限表」页的**一份全量数据**（窗口只画、不查库；纯只读，不联网）。

    两块（用户口径：中文只有三个词、不带数字 —— 禁止 / 限制 / 准限制）：

    * ``current``：**现在还在禁限表上**的卡（把变更史按时间累计出来的，
      见 `storage.banlist_current()`），按 禁止 → 限制 → 准限制 排序；
    * ``recent``：最近 N 条**变更**（生效时间倒序），带 from -> to 的箭头语义。

    卡名走 `core.card_names`（英文名反查中文；查不到原样给英文，**绝不吞信息**）。
    """
    data: Dict[str, object] = {
        'current': [], 'recent': [], 'counts': {},
        'current_total': 0, 'last_effective': None,
    }
    #: 卡名反查表：英文名（**归一后**）-> 中文。
    #: 归一 = 小写 + 去角括号 + 压连字符/空格 —— 站内写 `Maliss P White Rabbit`、
    #: ygocdb 写 `Maliss <P> White Rabbit`，精确匹配会漏 4 张（2026-09-21 实报）。
    lang = lang or _ui_lang()
    #: 目标语言 = 卡名表里的第几个字段（en 不用查 —— 库里存的就是英文名）
    tgt = {'cn': 0, 'jp': 2}.get(lang)
    rev, rev_sq = {}, {}
    if tgt is not None:
        try:
            from core import card_names as CN
            table = CN.load() if hasattr(CN, 'load') else None
            if table:
                for names in table.values():
                    if (names and names[1] and len(names) > tgt
                            and names[tgt]):
                        key = names[1].strip().lower()
                        norm = key.replace('<', '').replace('>', '')
                        rev.setdefault(norm, names[tgt] or names[1])
                        rev_sq.setdefault(
                            ' '.join(norm.replace('-', ' ').split()),
                            names[tgt] or names[1])
        except Exception:
            rev = rev_sq = {}

    def _loc(en):
        if tgt is None:
            return en or '?'
        key = (en or '').strip().lower().replace('<', '').replace('>', '')
        hit = rev.get(key)
        if hit:
            return hit
        squeezed = ' '.join(key.replace('-', ' ').split())
        return rev_sq.get(squeezed, en or '?')

    try:
        # ★ 当前状态 = 站内 `banStatus`（`banlist_current_status()`，权威）——
        #   别按变更史累计算这个：那个算法漏得出奇（2026-09-21 用户实报
        #   简易融合：变更史停在 2023「限制→禁止」，站内实际是限制）。
        #   `banlist_current_status` 表空时会回落到旧算法（老库没跑过新版抓取）。
        cur = list(storage.banlist_current_status() or [])
    except Exception:
        cur = []
    counts: Dict[str, int] = {}
    for name, status in cur:
        label = _status_label(status, lang)
        counts[label] = counts.get(label, 0) + 1
        #: ``art`` = 本地卡绘缓存路径（**只查缓存不联网**，见 core/card_art.py）——
        #: 没缓存时界面留占位（`widgets.thumb_label` 的空占位），由页面决定何时去下
        art = None
        try:
            from core import card_art
            art = card_art.art_path_by_en(name)
        except Exception:
            art = None
        data['current'].append({'name': _loc(name), 'name_en': name,
                                'status': label, 'art': art,
                                'status_key': status or 'Unlimited'})
    data['counts'] = counts
    data['current_total'] = len(cur)

    try:
        rows = list(storage.list_banlist_changes(limit=limit) or [])
    except Exception:
        rows = []
    if rows:
        data['last_effective'] = max(
            ((r.get('effective') or r.get('announced') or '')
             for r in rows), default='') or None
    for r in rows:
        art = None
        try:
            from core import card_art
            art = card_art.art_path_by_en(r.get('card_en'))
        except Exception:
            art = None
        data['recent'].append({
            'name': _loc(r.get('card_en')),
            'name_en': r.get('card_en'),
            'from': _status_label(r.get('from_status'), lang),
            'to': _status_label(r.get('to_status'), lang),
            'from_key': r.get('from_status') or 'Unlimited',
            'to_key': r.get('to_status') or 'Unlimited',
            'effective': r.get('effective') or r.get('announced'),
            'art': art,
            'url': r.get('url'),
        })
    return data


# ===================================================== 卡组构成页 + 卡组页（D3 剩余）


def _reference(reference=None) -> Dict[str, object]:
    """参照库整份（`data/mdm_deck_cards.json`）—— `decks` 段 + 顶层 `fetched_at`。

    ⚠️ 只走 :func:`core.deck_guess.load_reference()`（**CLI 与 M7 UI 唯一的参照库
    入口**，UI 不该 import `tools/`）；文件不在 → 空 dict（调用方给空态文案）。
    ``reference`` 参数是给 demo / 单测**注入**用的（默认 None = 读真文件）。
    """
    if reference is None:
        try:
            from core import deck_guess
            reference = deck_guess.load_reference()
        except Exception:                                    # noqa: BLE001
            reference = {}
    return reference if isinstance(reference, dict) else {}


def clear_reference_cache() -> None:
    """清掉参照库缓存（**UI 侧唯一入口**，转调 `core.deck_guess`）。

    给「设置 -> 数据工具 -> 参照库刷新」用：脚本跑完必须调一次，
    否则「卡组构成」页与选择器的 M5 建议仍按**旧快照**算。
    """
    try:
        from core import deck_guess
        deck_guess.clear_reference_cache()
    except Exception:                                    # noqa: BLE001
        pass


def _reference_decks(reference=None) -> Dict[str, dict]:
    """参照库的 `decks` 段 —— **99 副**（拿不到给空 dict）。"""
    decks = _reference(reference).get('decks')
    return decks if isinstance(decks, dict) else {}


def _reference_missing(reference=None) -> bool:
    """参照库整份缺失？（`reference` 参数注入时按注入的内容判，不读磁盘）

    ★ BUG-A6-02：`deck_composition()` 里 `has_ref=False` 有两种完全不同的原因 ——
    ① 参照库没建/被删（该提示用户去刷新）；② 参照库有，但**这副**卡组没样本
    （正常的空态）。混在一起用户只会看到「暂无该卡组构成」，无从判断该做什么。
    """
    if reference is not None:
        decks = (reference or {}).get('decks') if isinstance(reference, dict) else None
        return not (isinstance(decks, dict) and decks)
    try:
        from core import deck_guess
        return bool(deck_guess.reference_missing())
    except Exception:                                    # noqa: BLE001
        return True


def _ref_fetched_at(reference=None) -> Optional[str]:
    """参照库快照时间（顶层 `fetched_at`）；拿不到 ``None``。

    给「卡组构成」页页头交代**数据新旧**用（参照库是月度手动刷的快照）。
    """
    val = _reference(reference).get('fetched_at')
    return str(val) if val else None


#: 语言 → 卡组显示名字段优先级（与 ``ui.selector.NAME_KEYS`` 同口径；
#: core 不 import 控件层，这里自持一份 —— 改两处要一起改）
_DECK_NAME_KEYS = {
    'cn': ('name_cn', 'name_display', 'name'),
    'en': ('name_display', 'name', 'name_cn'),
    'jp': ('name_ja', 'name_display', 'name'),
}


def _tr():
    """`ui.i18n.tr` 惰性取（core 不在 import 期依赖 ui）。"""
    from ui.i18n import tr
    return tr


def _ui_lang() -> str:
    """界面显示语言（`ui/lang`）—— 经 `ui.i18n` 的进程内缓存（实时切换时会 reset）。"""
    try:
        from ui.i18n import lang as _ilang
        return _ilang()
    except Exception:                                        # noqa: BLE001
        return 'cn'


def _deck_display_row(row, lang) -> str:
    """一行 meta_decks 记录 → 该语言的显示名（字段优先级见 `_DECK_NAME_KEYS`）。"""
    row = row if isinstance(row, dict) else {}
    for k in (_DECK_NAME_KEYS.get(lang) or _DECK_NAME_KEYS['cn']):
        v = row.get(k)
        if v:
            return str(v)
    return str(row.get('name') or '')


def _name_resolver(storage, lang):
    """任意叫法（站内名/中文名/显示英文名/日文名/别名）→ 该语言的显示名。

    认不出（用户自命名等）**原样返回**。
    ⚠️ 2026-10-12 起 cn 不再恒等：对局分析的分组名可能是**站内名**（跟随卡组名
    管理），cn 也要映射回中文名（中文名本来就恒等映射回自己，无害）。
    """
    try:
        rows = storage.list_meta_decks(include_hidden=True) or []
    except Exception:                                        # noqa: BLE001
        rows = []
    mapping: Dict[str, str] = {}
    for r in rows:
        disp = _deck_display_row(r, lang)
        if not disp:
            continue
        for key in ('name', 'name_cn', 'name_display', 'name_ja'):
            v = r.get(key)
            if v:
                mapping.setdefault(str(v), disp)
        for a in (r.get('aliases') or []):
            if a:
                mapping.setdefault(str(a), disp)
    return lambda s: mapping.get(s or '', s or '')


def _display_map(storage, lang=None) -> Dict[str, str]:
    """站内名 → 显示名（**按语言**取字段，含隐藏条目）—— 拿不到就空表。"""
    lang = lang or _ui_lang()
    try:
        rows = storage.list_meta_decks(include_hidden=True) or []
    except Exception:                                        # noqa: BLE001
        return {}
    return {r.get('name'): _deck_display_row(r, lang) for r in rows}


def _art_path(cid) -> Optional[str]:
    """本地卡绘缓存路径（`core.card_art.art_path`，**纯本地不联网**）；缺就 None。"""
    try:
        from core import card_art
        return card_art.art_path(cid)
    except Exception:                                        # noqa: BLE001
        return None


def _cropped_path(cid) -> Optional[str]:
    """**纯卡绘**（去卡框）缓存路径（`core.card_art.cropped_path`，纯本地不联网）。

    手坑格用 —— 整张卡在 18px 格里看不清卡面（2026-10-16 用户实测反馈）。
    """
    try:
        from core import card_art
        return card_art.cropped_path(cid)
    except Exception:                                        # noqa: BLE001
        return None


def _trap_items(traps, lang) -> Optional[List[Dict[str, object]]]:
    """手坑结论列表 -> 显示项 ``[{'name','turn','img'}, ...]``。

    * ``traps is None``（判不出 / 无逐卡数据）-> ``None``（界面画「—」）；
    * ``[]``（确实没用过）-> ``[]``（界面留空）；
    * 名字按界面语言（缺该语言顺延，`card_names.display`）；
    * ``img`` = **纯卡绘**缓存（`core.card_art.cropped_path` —— 去卡框，
      2026-10-16 用户实测：整张卡在 18px 格里框+文案占满、看不清卡面），
      纯本地不联网；没有就 None —— 界面回落 `_default.webp`（悠悠）。
    """
    if traps is None:
        return None
    out = []
    for t in traps or []:
        if not isinstance(t, dict):
            continue
        cid = t.get('id')
        name = None
        try:
            from core.card_names import display
            name = display(cid, lang)
        except Exception:                                    # noqa: BLE001
            name = None
        out.append({
            'name': name or t.get('name_cn') or ('#%s' % cid),
            'turn': t.get('turn'),
            'id': cid,
            'img': _cropped_path(cid),
        })
    return out


#: 自组卡组 → **共用参照库哪个键**（2026-10-10，用户口径）。
#:
#: 「狱神耀圣」是用户自组的牌，站内没有这一条，但它的构成与 `Elfnote`
#: （耀圣）**是同一副** —— 所以直接读 Elfnote 的逐卡使用率，不必重复抓一份。
#: ⚠️ 这张表**只写用户明确说过的**（「共用」），**不推断**：两个卡组像 ≠ 用户认为
#: 它们是同一副；猜错会让使用率静默地对不上。
#: 「隐藏卡组不列出」（见 AGENTS 铁律 7）—— 这里的 `hidden` 是**卡组名**的隐藏标记，
#: 不是 `meta_decks.hidden`，两者无关。
#: ★ 2026-10-11 迁到 `core.deck_guess.SHARED_REFERENCE`（建议候选也要用它，
#: 那边不能反向 import 本模块）—— 这里保留原名字做兼容别名。
from core.deck_guess import SHARED_REFERENCE as REF_SHARED_WITH  # noqa: E402


def match_ref_key(name, decks, storage=None) -> Optional[str]:
    """把**任意叫法**落到参照库的键（站内英文名）—— 落不到返回 ``None``。

    顺序：参照库键精确 → 忽略大小写 → 走 `search_meta_decks`（中文名 / 别名 /
    日文名都能命中，见 `core.deck_match`）→ 参照库自己的 `source_name`。
    「看构成」按钮能不能点，就看这里返不返回 None。
    """
    name = (name or '').strip()
    if not name or not decks:
        return None
    #: ★ 先问「共用表」（2026-10-10）：`狱神耀圣` 读 `Elfnote` 的卡表。
    #: 必须在下面那些匹配**之前** —— 它在参照库里没有自己的键，
    #: 否则 `search_meta_decks` 会把它模糊匹配到别的条目上去。
    shared = REF_SHARED_WITH.get(name)
    if shared and shared in decks:
        return shared
    if name in decks:
        return name
    low = name.lower()
    for k in decks:
        if k.lower() == low:
            return k
    if storage is not None:
        try:
            hits = storage.search_meta_decks(name, limit=1,
                                             include_hidden=True) or []
        except Exception:                                    # noqa: BLE001
            hits = []
        for r in hits:
            for cand in (r.get('name'), r.get('name_display')):
                if cand in decks:
                    return cand
    for k, d in decks.items():
        if isinstance(d, dict) and (d.get('source_name') or '') == name:
            return k
    return None


def _ref_search_terms(storage) -> Dict[str, List[str]]:
    """站内名 → **搜索词**（覆盖表里的别名 + 日文名）—— 拿不到就给空表。

    「换卡组」下拉要**任何叫法**都能搜到（中文 / 别名 / 日文），而候选项的显示名
    只有一个 —— 所以把别名与日文名一并透出，让页面喂给 `QCompleter`。
    """
    out: Dict[str, List[str]] = {}
    try:
        rows = storage.list_meta_decks(include_hidden=True) or []
    except Exception:                                        # noqa: BLE001
        rows = []
    for r in rows:
        r = r if isinstance(r, dict) else {}
        words = [str(w) for w in (r.get('aliases') or []) if w]
        ja = r.get('name_ja')
        if ja:
            words.append(str(ja))
        out[r.get('name')] = words
    return out


def data_status(storage=None) -> Dict[str, object]:
    """设置区三功能页页头那行「**当前数据状态**」（D3 §3.1）。

    形状::

        {'card_table': {'count': 14615, 'generated_at': '2026-09-19T21:50:48',
                        'path': '…/card_names.json'},
         'banlist':    {'changes': 420, 'current': 130, 'last_effective': '2026-09-03'},
         'matches':    {'count': 35, 'last_ended': '2026-09-21 12:34'}}

    用途：**不跑脚本也能核对**（表是不是最新 / 有多少场没入库），
    以及跑完当「结果摘要」（重跑一次这个函数再渲染）。

    ⚠️ **失败不抛**（全项目纪律）：任一段拿不到就给该段的零值 /
    ``None``，其余段照常 —— 设置页不能因为卡表文件被删就整个白屏。
    """

    def _read_card_table() -> Dict[str, object]:
        """`data/card_names.json` 的顶层 `generated_at` + `counts.total`。"""
        # ★ BUG-B1-03：既有随包基线、又会被「卡表更新」改写 —— 用 resolve()
        #   （DATA_DIR 优先、缺失回落 ASSET_DIR），别写死 PROJ/data
        path = paths.resolve('card_names.json')
        out: Dict[str, object] = {'count': None, 'generated_at': None,
                                  'path': path}
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:                                    # noqa: BLE001
            return out
        if not isinstance(raw, dict):
            return out
        counts = raw.get('counts') if isinstance(raw.get('counts'), dict) else {}
        total = counts.get('total')
        try:
            out['count'] = int(total) if total is not None else None
        except (TypeError, ValueError):
            out['count'] = None
        stamp = raw.get('generated_at')
        out['generated_at'] = str(stamp) if stamp else None
        return out

    def _read_banlist() -> Dict[str, object]:
        """变更史条数 + max(生效) + **当前受限张数**（站内 banStatus 权威）。"""
        out: Dict[str, object] = {'changes': None, 'current': None,
                                  'last_effective': None}
        if storage is None:
            return out
        try:
            rows = storage.list_banlist_changes(limit=None) or []
            out['changes'] = len(rows)
            eff = []
            for r in rows:
                stamp = (r or {}).get('effective') or (r or {}).get('announced')
                if stamp:
                    eff.append(str(stamp))
            #: 库里按生效时间**倒序**返回 —— 第一个有值的就是最近的
            out['last_effective'] = eff[0][:10] if eff else None
        except Exception:                                    # noqa: BLE001
            pass
        try:
            out['current'] = len(storage.banlist_current_status() or [])
        except Exception:                                    # noqa: BLE001
            pass
        return out

    def _read_matches() -> Dict[str, object]:
        """对局总场数 + 最近一局的结束时刻（没有就退开工时刻）。"""
        out: Dict[str, object] = {'count': None, 'last_ended': None}
        if storage is None:
            return out
        try:
            out['count'] = int(storage.count())
        except Exception:                                    # noqa: BLE001
            pass
        try:
            rows = storage.list_matches(limit=None) or []
        except Exception:                                    # noqa: BLE001
            rows = []
        for r in reversed(rows):          # `list_matches` 是**升序** —— 从后往前找
            r = r if isinstance(r, dict) else {}
            stamp = (r.get('ended_at') or r.get('started_at'))
            if stamp:
                out['last_ended'] = str(stamp)[:16].replace('T', ' ')
                break
        return out

    return {'card_table': _read_card_table(),
            'banlist': _read_banlist(),
            'matches': _read_matches()}


# ===================================================== 采集器状态（D4b）

#: 心跳过期阈值（秒）—— 超过就认为「没在跑」。
#: 采集器默认 2s 一拍、**待机时也写**（游戏没开那段会 sleep 5s），
#: 给 60s 余量足够宽：正常跑的机器不会误报，被杀/关机的会自然过期。
HEARTBEAT_STALE_SEC = 60


def collector_status(now=None) -> Dict[str, object]:
    """采集器（`tools/duel_collector.py`）在不在跑 —— 看板顶部那行状态。

    ⚠️ **判据 = 心跳文件的新鲜度**，不是 pid 存活、也不是日志 mtime：

    * `duel_collector.pid` 只在**启动**时写、只在**优雅退出**时删 ——
      被 kill 的实例会留下 stale 文件（实测 09-21 那份留到次日），拿它判必然误报；
    * `duel_collector.log` 只在**有事件**时写 —— 待机（游戏没开）那段不写，
      拿它判会把「在跑但待机」读成「没在跑」。

    心跳文件由采集器每拍覆盖写（`data/archive/collector_heartbeat.json`）：
    ``{"at": "YYYY-MM-DD HH:MM:SS", "in_duel": bool, "game_pid": int|None}``。

    形状::

        {'running': True, 'at': '2026-09-22 10:00:00', 'age_sec': 3,
         'in_duel': False, 'game_pid': None}

    文件不在 / 读不出来 → ``running=False``、``at=None``（**不抛**，全项目纪律）。
    """

    def _fail() -> Dict[str, object]:
        return {'running': False, 'at': None, 'age_sec': None,
                'in_duel': False, 'game_pid': None}

    try:
        # ★ BUG-B1-03：心跳由采集器写，走 paths.data_path
        path = paths.data_path('archive', 'collector_heartbeat.json')
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:                                        # noqa: BLE001
        return _fail()
    if not isinstance(raw, dict):
        return _fail()

    stamp = str(raw.get('at') or '')
    age = None
    if stamp:
        try:
            when = datetime.datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')
            ref = now or datetime.datetime.now()
            age = int((ref - when).total_seconds())
        except (TypeError, ValueError):
            age = None
    return {
        'running': age is not None and age <= HEARTBEAT_STALE_SEC,
        'at': stamp or None,
        'age_sec': age,
        'in_duel': bool(raw.get('in_duel')),
        'game_pid': raw.get('game_pid'),
    }


# ===================================================== 导出（D4c）

#: 导出的列（**顺序即 CSV 列序**）。日期 / 时间**已分列**（Q16 口径）。
#: 每项 ``(键, 中文表头)``；键就是 `_page_row()` 给的字段名。
EXPORT_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ('date', '日期'),
    ('time', '时间'),
    ('result', '结果'),
    ('went_first', '先后手'),
    ('my_deck', '我方卡组'),
    ('opp_player', '对手'),
    ('opp_deck', '对手卡组'),
    ('opp_known', '对手卡组已标注'),
    ('mode_label', '模式'),
    ('total_turns', '回合数'),
    ('rank_label_before', '局前段位'),
    ('rank_label_after', '局后段位'),
    ('rank_delta', '段位变化'),
    #: ★ 2026-10-16 手坑两列：逗号分隔的卡名（按界面语言）；
    #: 判不出（无逐卡数据）留空 —— 与界面「—」同源（`_trap_names_cell`）。
    ('my_traps', '我方手坑'),
    ('opp_traps', '对方手坑'),
    ('match_id', '对局 ID'),
)

#: 结果 / 先后手的中文（导出给人看，别留 `Win` / `True`）
EXPORT_RESULT_CN = {'Win': '胜', 'Lose': '负', 'Draw': '平'}
#: ⚠️ 硬币列**已删**（2026-10-03 用户）：库里 `won_initiative` 仍保留
#: （悬浮窗先后攻还用它），只是不再导出 —— 需要时从库里现查。
EXPORT_FIRST_CN = {True: '先攻', False: '后攻'}
EXPORT_DELTA_CN = {'up': '升段', 'down': '掉段', 'same': '不变'}


def export_months(storage, limit: int = 5000) -> List[Tuple[str, str]]:
    """可导出的月份清单 —— ``[(月份, 显示名), ...]``，**最近的在前**。

    与战绩页热力图、对局分析页的月份筛选**同源**（都是库里实际有对局的月份），
    别在导出页另造一份（那样三处会漂）。
    """
    try:
        page = stats_page_data(storage, limit=limit)
        months = list(page.get('months') or [])
    except Exception:                                        # noqa: BLE001
        months = []
    out = [('', '全部月份')]
    # ⚠️ `stats_page_data()['months']`（= `months_of()`）是**升序**（旧→新），
    #    而本函数契约是「**最近的在前**」（BUG-B6-12 / B1-12）。这里显式倒序，
    #    别改成依赖上游顺序。
    for m in sorted((str(x) for x in months if x), reverse=True):
        out.append((m, m))
    return out


def _trap_names_cell(traps) -> str:
    """手坑结论 -> 导出格文本（逗号分隔的卡名）。

    * ``None``（判不出 / 无逐卡数据）-> 空串（与界面「—」同源，不留 `None` 进 CSV）；
    * ``[]``（确实没用过）-> 空串；
    * 有值 -> `灰流丽, 无限泡影`（**顺序 = 事件顺序**，同名多张会重复出现）。
    """
    if not traps:
        return ''
    return ', '.join(str(t.get('name') or '') for t in traps if t)


def export_rows(storage, mode_group=None, limit: int = 5000,
                month=None) -> List[Dict[str, object]]:
    """把对局记录导成「一行 = 一局」的扁平表（**纯函数**，可单测）。

    口径：直接复用 :func:`match_groups()` —— 所以导出**与战绩页看到的完全一致**
    （界面口径 + 同一个模式筛选）。⚠️ 别另写一份查询：两份口径迟早会漂。

    ``month``（``'YYYY-MM'``，None = 不限）用 :func:`month_bounds()` 划范围 ——
    与对局分析页的月份筛选**同一个函数**（用户 2026-09-22：「按月导出」）。

    返回的每行已经**翻译成给人看的值**（胜/负、先攻/后攻、升段/掉段），
    日期与时间**分列**（Q16）。字段名见 :data:`EXPORT_COLUMNS`。
    """
    from ui.i18n import tr
    out: List[Dict[str, object]] = []
    since = until = None
    if month:
        try:
            since, until = month_bounds(str(month))
        except Exception:                                    # noqa: BLE001
            since = until = None
    try:
        groups = match_groups(storage, limit=limit, mode_group=mode_group,
                              since=since, until=until) or []
    except Exception:                                        # noqa: BLE001
        return out
    #: 值跟界面语言走（2026-10-04 用户：导出表也要能读懂）；cn 恒等
    lang = _ui_lang()
    resolve = _name_resolver(storage, lang) if lang != 'cn' else None
    #: 排序键跟着行走（BUILD 时留一份，不塞进导出行 —— JSON 导出的
    #: `rows` 要和 `columns` 一一对应，多一个键就是脏数据）
    _sort_keys = []
    for deck_name, _stats, rows in groups:
        for r in rows or []:
            r = r if isinstance(r, dict) else {}
            od = r.get('opp_deck') or ''
            _sort_keys.append(r.get('started_at')
                              or ('%s %s' % (r.get('date') or '',
                                             r.get('time') or '')))
            out.append({
                'date': r.get('date') or '',
                'time': r.get('time') or '',
                'result': tr(EXPORT_RESULT_CN.get(r.get('result'),
                                                  r.get('result') or '')),

                'went_first': tr(EXPORT_FIRST_CN.get(r.get('went_first'), '')),
                'my_deck': ((resolve(deck_name) if resolve else deck_name)
                            or ''),
                'opp_player': r.get('opp_player') or '',
                'opp_deck': ((resolve(od) if resolve else od) or ''),
                'opp_known': tr('是') if r.get('opp_known') else tr('否'),
                'mode_label': r.get('mode_label') or '',
                'total_turns': r.get('total_turns'),
                'rank_label_before': r.get('rank_label_before') or '',
                'rank_label_after': r.get('rank_label_after') or '',
                'rank_delta': tr(EXPORT_DELTA_CN.get(r.get('rank_delta'), '')),
                'my_traps': _trap_names_cell(r.get('my_traps')),
                'opp_traps': _trap_names_cell(r.get('opp_traps')),
                'match_id': r.get('match_id') or '',
            })
    #: 按**完整时刻**倒序（近的在上，与战绩页一致）。
    #  ⚠️ 用 `started_at` 而不是 `date`+`time`：`date` 只有 `MM-DD`，
    #     跨年时字符串比较会把 12 月排到次年 1 月之后（BUG-B1-12）。
    #     行里没有 `started_at` 的（老调用方自己拼的行）退回 date+time。
    # ⚠️ 排序键**不能**从 `out` 里取 —— 导出行只有 EXPORT_COLUMNS 那些键，
    #    没有 `started_at`（上面 `_sort_keys` 就是为此留的）。
    order = sorted(range(len(out)), key=lambda i: _sort_keys[i], reverse=True)
    return [out[i] for i in order]


def export_default_name(fmt: str = 'csv', now=None) -> str:
    """默认文件名：``duel_export_YYYYMMDD_HHMM.csv``（纯函数，单测锁后缀）。"""
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d_%H%M')
    ext = 'json' if str(fmt).lower() == 'json' else 'csv'
    return 'duel_export_%s.%s' % (stamp, ext)


def write_export(path, rows, fmt=None) -> str:
    """把 :func:`export_rows()` 的结果落盘，返回**实际写入的路径**。

    * ``fmt`` 不给就按扩展名猜（``.json`` → JSON，其余 CSV）；
    * **CSV 用 `utf-8-sig` + CRLF** —— Excel 双击直接认（不留 BOM 会乱码、
      用 LF 在旧 Excel 里会挤成一行）；
    * **JSON 用 `ensure_ascii=False`** —— 中文照原样写（不转义）；
    * 任一步失败**抛给调用方**（导出是用户显式动作，静默失败最糟 ——
      用户以为导出了，其实没写）。
    """
    fmt = (fmt or '').lower() or (
        'json' if str(path).lower().endswith('.json') else 'csv')
    rows = list(rows or [])
    if fmt == 'json':
        payload = {
            'exported_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'count': len(rows),
            'columns': [{'key': k, 'label': lab} for k, lab in EXPORT_COLUMNS],
            'rows': rows,
        }
        with open(path, 'w', encoding='utf-8', newline='') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.write('\n')
        return path
    import csv as _csv
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = _csv.writer(f, lineterminator='\r\n')
        writer.writerow([lab for _k, lab in EXPORT_COLUMNS])
        for r in rows:
            writer.writerow([r.get(k, '') if r.get(k) is not None else ''
                             for k, _lab in EXPORT_COLUMNS])
    return path


def collector_status_line(status) -> str:
    """`collector_status()` → 一行给人看的字（**纯函数**，单测直接锁）。

    文案经 `ui.i18n`（cn 原样返回 —— 测试与默认观感零改动）。
    """
    from ui.i18n import tr
    d = status if isinstance(status, dict) else {}
    if not d.get('running'):
        return (tr('采集器：未运行')
                + (tr('（最后心跳 %s）') % d['at'] if d.get('at') else ''))
    age = d.get('age_sec')
    head = (tr('采集器：运行中（%ds 前心跳）') % age if age is not None
            else tr('采集器：运行中'))
    return head + (tr('，对局中') if d.get('in_duel') else tr('，待机中'))


def data_status_line(status) -> str:
    """`data_status()` → 一行给人看的中文（**纯函数**，单测直接锁）。

    形如：``卡表 14615 张（2026-09-19 更新）/ 禁限 130 张（最近 2026-09-03）/
    对局 35 场（最近 2026-09-21 12:34）``；缺项写「—」，绝不吞掉整行。
    """

    def _num(value):
        return '—' if value is None else str(value)

    d = status if isinstance(status, dict) else {}
    ct = d.get('card_table') if isinstance(d.get('card_table'), dict) else {}
    bl = d.get('banlist') if isinstance(d.get('banlist'), dict) else {}
    mt = d.get('matches') if isinstance(d.get('matches'), dict) else {}

    from ui.i18n import tr
    stamp = str(ct.get('generated_at') or '')[:10] or tr('未知')
    card = tr('卡表 %s 张（%s 更新）') % (_num(ct.get('count')), stamp)

    last = str(bl.get('last_effective') or '') or tr('无记录')
    ban = tr('禁限 %s 张（最近 %s）') % (_num(bl.get('current')), last)

    played = str(mt.get('last_ended') or '') or tr('还没打过')
    match = tr('对局 %s 场（最近 %s）') % (_num(mt.get('count')), played)
    return '%s / %s / %s' % (card, ban, match)


def _ref_options(storage, decks, lang=None) -> List[Dict[str, object]]:
    """「换卡组」下拉的候选（参照库全量，按 tier → 样本数排序）。

    * ``name`` = **站内英文名（data）** —— 选中后发给窗口的就是它；
    * ``display`` = 给人看的名字（覆盖表口径）；
    * ``extra`` = 别名 + 日文名（**只给搜索用**，不显示）。
    """
    lang = lang or _ui_lang()
    disp = _display_map(storage, lang)
    terms = _ref_search_terms(storage)
    order = {t: i for i, (t, _ti, _d) in enumerate(TIER_SECTIONS)}
    out: List[Dict[str, object]] = []
    for key, d in decks.items():
        d = d if isinstance(d, dict) else {}
        out.append({'name': key,
                    'display': (disp.get(key) or _deck_display_row(d, lang)
                                or key),
                    'tier': d.get('tier'),
                    'total': int(d.get('total') or 0),
                    'extra': terms.get(key) or []})
    out.sort(key=lambda o: (order.get(o.get('tier'), len(order)),
                            -int(o.get('total') or 0), str(o.get('name'))))
    return out


def deck_composition(storage, ref_name=None, per_min: int = 0,
                     reference=None, lang=None) -> Dict[str, object]:
    """某个卡组的**构成**（主卡 / 额外两组）—— 数据源 = 参照库（99 副）。

    形状::

        {'name': 'Sky Striker', 'display': '闪刀姬', 'tier': 'Tier3',
         'total': 21, 'avg_main': 40, 'has_ref': True,
         'main':  [{'id': 12950, 'name': '灰流丽', 'per': 95, 'at': 3, 'art': …}],
         'extra': [{'id': 67108897, 'name': '闪刀姬-飒天', …}],
         'unknown': 0,       # 卡表里查不到类型的张数（**只计数不显示**，不猜）
         'options': [{'name': …, 'display': …, 'tier': …, 'total': …,
                      'extra': [别名 / 日文名 …]}],
         'ref_fetched_at': '2026-09-20T12:00:00'}

    * 主卡 / 额外判据 = ygocdb `data.type` 位掩码（`core.card_names.is_extra`）；
    * 排序：`per` 降序 → `at` 降序 → 卡名（站内截图也是这个次序）；
    * **同名去重**（异画）：同名只留 `per` 更高的那条（与 `deck_guess.build_index`
      同口径 —— 打分器也是这么把异画并成一张卡的）；
    * ``ref_fetched_at`` = 参照库快照时间（页头交代数据新旧；拿不到 ``None``）；
    * ``options[].extra`` = 别名 + 日文名（**换卡组下拉的搜索词**，不显示）；
    * 参照库里没有这个卡组 → 两组空 + ``total=0``（界面给空态文案），**不抛**；
    * `art` 只查**本地缓存**（`core.card_art.art_path`，纯本地不联网），
      缺的由页面在后台下（见 `ui/deck_view_page.py`）。
    """
    from core import card_names as CN

    decks = _reference_decks(reference)
    key = match_ref_key(ref_name, decks, storage)
    ref = decks.get(key) if key else None
    if not isinstance(ref, dict):
        ref = {}
    lang = lang or _ui_lang()
    disp = _display_map(storage, lang)
    #: ⚠️ 显示名**优先用用户点的那个**（2026-10-10）：`狱神耀圣` 共用
    #: `Elfnote` 的卡表后 `key` 是 `Elfnote`，直接 `disp.get(key)` 会显示成
    #: 「耀圣」—— 用户点的是狱神耀圣，页头就该写狱神耀圣。
    #: `disp` 是 `meta_decks` 的中文名表，自组条目（source='custom'）也在里面。
    _own = (str(ref_name or '').strip()
            and (disp.get(str(ref_name).strip()) or
                 _deck_display_row({'name_cn': ref_name}, lang)))
    data: Dict[str, object] = {
        'name': key or (ref_name or ''),
        'display': (_own or (disp.get(key) if key else '')
                    or _deck_display_row(ref, lang) or ref_name or ''),
        'tier': ref.get('tier'),
        'total': int(ref.get('total') or 0),
        'avg_main': ref.get('avgMainSize'),
        'has_ref': bool(ref),
        'main': [], 'extra': [], 'unknown': 0,
        'options': _ref_options(storage, decks, lang),
        'ref_fetched_at': _ref_fetched_at(reference),
        #: ★ BUG-A6-02：参照库**整份缺失**（没跑过 / 被删 / 读不出）——
        #: 页面据此提示「去跑一次参照库刷新」，而不是只显示「暂无该卡组构成」
        #: （两者对用户是不同的事：一个是数据没建，一个是这个卡组没样本）。
        'ref_missing': _reference_missing(reference),
    }
    by_name: Dict[str, tuple] = {}
    unknown = 0
    for cid, info in (ref.get('cards') or {}).items():
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            continue
        per = info.get('per') if isinstance(info, dict) else None
        at = info.get('at') if isinstance(info, dict) else None
        try:
            per = int(per)
        except (TypeError, ValueError):
            continue
        if per < int(per_min or 0):
            continue
        try:
            at = int(at)
        except (TypeError, ValueError):
            at = None
        try:
            ctype = CN.card_type(cid_int)   # 卡表炸了 / 没这张卡 → None（计入 unknown）
        except Exception:                                    # noqa: BLE001
            ctype = None
        if ctype is None:
            unknown += 1          # 卡表里没有这张卡：只计数，不猜它属主卡还是额外
            continue
        name = CN.display(cid_int, lang) or ('#%d' % cid_int)
        prev = by_name.get(name)
        if prev is not None and prev[1]['per'] >= per:
            continue
        by_name[name] = (bool(ctype & CN.EXTRA_TYPE_MASK),
                         {'id': cid_int, 'name': name, 'per': per, 'at': at,
                          'art': _art_path(cid_int)})
    for is_extra_card, row in by_name.values():
        data['extra' if is_extra_card else 'main'].append(row)
    data['unknown'] = unknown
    for group in ('main', 'extra'):
        data[group].sort(key=lambda r: (-r['per'], -(r['at'] or 0), r['name']))
    return data


# ===================================================== 数据体检（用户 2026-09-23）
#: 「数据体检」检测的记录目录根（与采集器/`duel_record` 同一份磁盘事实）
# ★ BUG-B1-03：走 paths.data_path（安装版 Program Files 下 PROJ/data 不可写）
_AUDIT_RECORDS = paths.data_path('archive', 'duel_records')

#: 体检取历史行时的上限 —— **不是分页**，只是防无穷；500 会漏（BUG-B1-12）。
AUDIT_ROW_LIMIT = 100000


def audit_issues(storage=None, records_dir=None) -> Dict[str, object]:
    """**数据体检**（用户 2026-09-23 定）：把今天踩过的三类数据事故变成一键检测。

    事故背景：①结果缺失（EM 结算没抓到 → `result=NULL`，悬浮窗显示 —）；
    ②**整局漏记**（采集器开局了但 record 目录没建起来 / 建了没入库 ——
    用户打了 6 局只统计上 2 局就是这么发现的）；③**open 未收口**
    （采集器中途被杀，目录永远停在 open，不会入库）。

    :return: ``{'missing_result': [...], 'orphan_dirs': [...], 'open_dirs': [...],
            'n_issues': int}``，每项形如
            ``{'dir': 'record_…', 'started_at': …, 'opp_deck': …}``。
            **失败不抛**（全项目纪律）：哪段拿不到就给空列表。
    """
    if storage is None:
        from core.storage import Storage
        storage = Storage()
    root = records_dir or _AUDIT_RECORDS
    out: Dict[str, object] = {'missing_result': [], 'orphan_dirs': [],
                              'open_dirs': [], 'n_issues': 0,
                              #: ★ A3-结论-C：把「整局漏记」按**能不能配上对**分成两组。
                              #: * ``orphan_no_pair``：窗口内**根本没有 log/api 行**
                              #:   （观战 / 单人 / 判不出模式的历史局）—— 日志侧本来就没有
                              #:   对手 → 不是漏记，**不再算进 n_issues**；
                              #: * ``orphan_pair_exists``：窗口内**有** log/api 行却没并上
                              #:   —— 这才是真漏记（要人工看）。
                              'orphan_no_pair': [], 'orphan_pair_exists': []}
    try:
        # ⚠️ **不能截断**（BUG-B1-12）：原来 `limit=500`，超出的历史行既查不到
        #    「缺结果」，那些行的 `record_dir` 也不进 `in_db` —— 磁盘上的老目录
        #    会被误报成「漏记」。体检要的是全量，取一个大到用不完的上限。
        rows = list(storage.get_recent_matches(limit=AUDIT_ROW_LIMIT) or [])
    except Exception:                                        # noqa: BLE001
        rows = []

    # ① 结果缺失：已结束（有 ended_at）但 result 是空 —— 结果不会再来，
    #    越早发现越早补（EM 窗口只有几秒，事后必须手动）。
    for m in rows:
        # 单人局 result 恒空（PvE 不抓结算），别报「结果缺失」。
        # ⚠️ 补录的日志行 game_mode 是 NULL —— 退回看 `mode` 字段（2026-10-08）。
        if m.get('mode') == 'solo' or (
                m.get('game_mode') is not None
                and mode_group(m['game_mode']) == 'solo'):
            continue
        if m.get('result') is None and m.get('ended_at'):
            out['missing_result'].append({
                'dir': m.get('record_dir'),
                'started_at': m.get('started_at'),
                'opp_deck': m.get('opp_deck_name'),
            })

    # ②③ 扫 record 目录：入库了没有 / 还开着没有。
    #    ⚠️ 「孤儿目录」要分拣：真对局（有 ShowFirstPlayer，≈每局恰 1 条）
    #    才值得报；采集器的**残影/空壳目录**是正常现象，报了只会狼来了。
    try:
        names = sorted(os.listdir(root)) if os.path.isdir(root) else []
    except OSError:
        names = []
    in_db = {m.get('record_dir') for m in rows if m.get('record_dir')}
    for name in names:
        if not name.startswith('record_') or name in in_db:
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            with open(os.path.join(path, 'manifest.json'),
                      encoding='utf-8') as f:
                man = json.load(f)
        except Exception:                                # noqa: BLE001
            man = {}
        entries = (man.get('logbase') or {}).get('entries') or []
        real = any(e.get('label') == 'ShowFirstPlayer' for e in entries)
        state = man.get('state')
        if state == 'open':
            # ⚠️ **正在打的那局不算问题**（BUG-B1-12）：采集器开局就建 open 目录、
            #    打完才 live 终局化 —— 体检在「对局进行中」跑会把当前这局报成
            #    「未收口」。判据与 `import_duel_records --finalize-stale` 同源：
            #    最后一次落盘距今 < `STALE_OPEN_SEC`（6h）= 可能还在打，跳过。
            #    ⚠️ 拿不到时间戳的老 manifest 无从判断 -> 照旧报（宁可多报）。
            if _open_dir_still_running(man):
                continue
            out['open_dirs'].append({'dir': name, 'n_entries': len(entries)})
            continue                      # open 且没入库 = ③ 的一种，只报一次
        if real:
            item = {'dir': name, 'n_entries': len(entries), 'state': state}
            #: ★ A3-结论-C：13 个孤儿目录里绝大多数是**观战/单人局**
            #: （窗口内一行 log/api 都没有，`find_record_pair` 结构性地找不到东西）
            #: —— 旧代码一律报「漏记」，用户每次体检都看到一堆假问题（狼来了）。
            #: 按「窗口内有没有 log/api 行」分组：有 = 真漏记；没有 = 观战/单人。
            item['pair_hint'] = ('pair-exists-but-not-merged'
                                 if _has_pair_candidate(rows, man)
                                 else 'no-pair-object')
            if item['pair_hint'] == 'pair-exists-but-not-merged':
                out['orphan_pair_exists'].append(item)
                out['orphan_dirs'].append(item)
            else:
                out['orphan_no_pair'].append(item)
    out['n_issues'] = (len(out['missing_result']) + len(out['orphan_dirs'])
                       + len(out['open_dirs']))
    return out


def _has_pair_candidate(rows, man) -> bool:
    """这个孤儿目录的**时间窗内**有没有 log/api 行 —— 有 = 真漏记。

    ★ A3-结论-C：`find_record_pair` 只认 `origin IN ('log','api')`；观战/单人局
    日志侧根本不写 `Starting Duel`，所以那些目录永远配不上对 —— 那不是 bug。
    判据与 `Storage.find_record_pair` 同源（宽窗 = `RECORD_WIDE_LOOKBACK`），
    但这里只回答「有没有东西可并」，所以用**双向 ±30 分钟**（宁可宽一点，
    少报假漏记）：真对局的 log 行 `started_at` 完全可能落在目录 `first_seen`
    之后（采集器先抓到控制器、日志后写）。
    """
    try:
        import datetime as _dt
        from core.storage import Storage
        cap = (man or {}).get('capture') or {}
        stamp = cap.get('first_seen_at') or (man or {}).get('saved_at')
        base = _parse_dt(stamp)
        if base is None:
            # 判不出时间 -> 无从判断，按「有可能」处理（宁可多报，别掩盖真漏记）
            return True
        wide = Storage.RECORD_WIDE_LOOKBACK
        lo = (base - _dt.timedelta(seconds=wide)).strftime('%Y-%m-%d %H:%M:%S')
        hi = (base + _dt.timedelta(seconds=wide)).strftime('%Y-%m-%d %H:%M:%S.999')
        for m in rows or []:
            if m.get('origin') not in ('log', 'api'):
                continue
            started = str(m.get('started_at') or '')
            if lo <= started <= hi:
                return True
            ended = str(m.get('ended_at') or '')
            if ended and lo <= ended <= hi:
                return True
        return False
    except Exception:                                        # noqa: BLE001
        return True                       # 判不出 = 照报（别用异常掩盖漏记）


def _open_dir_still_running(man) -> bool:
    """manifest 像是**正在采集**还没收口（最后一次落盘距今 < 6 小时）。"""
    try:
        from core.memread.record_capture import STALE_OPEN_SEC
    except Exception:                                        # noqa: BLE001
        STALE_OPEN_SEC = 6 * 3600
    cap = (man or {}).get('capture') or {}
    stamp = cap.get('last_saved_at') or (man or {}).get('saved_at')
    dt = _parse_dt(stamp)
    if dt is None:
        return False                      # 判不出 = 当成 stale（照报，别掩盖问题）
    return (datetime.datetime.now() - dt).total_seconds() < STALE_OPEN_SEC


def _parse_dt(s):
    """定宽时刻文本 -> datetime；认不出给 None（本模块不 import storage，避免环）。"""
    try:
        return datetime.datetime.strptime(str(s)[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def audit_issues_line(issues) -> str:
    """`audit_issues()` → 一行给人看的中文（**纯函数**）。

    全空 = 「未发现问题」；有问题逐类计数，形如
    ``缺结果 2 局 / 漏记 1 局 / 未收口 1 个 —— 共 4 项``。
    """
    try:
        mr = len(issues.get('missing_result') or [])
        orphan = len(issues.get('orphan_dirs') or [])
        opens = len(issues.get('open_dirs') or [])
    except AttributeError:
        return '数据体检：读不懂的体检结果'
    try:
        #: ★ A3-结论-C：观战/单人目录（日志侧没有对手）**不计入问题**，但要交代一句
        skip = len(issues.get('orphan_no_pair') or [])
    except AttributeError:
        skip = 0
    tail = ('；另有 %d 个观战/单人局目录（日志侧无对手，不计入）' % skip) if skip else ''
    if not (mr or orphan or opens):
        return '体检通过：结果完整、没有漏记、没有未收口的目录' + tail
    parts = []
    if mr:
        parts.append('缺结果 %d 局' % mr)
    if orphan:
        parts.append('漏记 %d 局' % orphan)
    if opens:
        parts.append('未收口 %d 个' % opens)
    return ('发现问题：%s —— 共 %d 项（详情见下方日志）%s'
            % (' / '.join(parts), mr + orphan + opens, tail))
