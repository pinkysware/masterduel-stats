# -*- coding: utf-8 -*-
"""M7 **UI 开发用 demo 战绩**（2026-09-15 建）—— 做 UI 一律先拿这份数据画。

为什么要有它（用户 2026-09-15 定）：真库最近三局**没有段位数据**（段位字段
25/54 覆盖）、绝大多数局**未标注**，拿真库截图迭代 UI 会被数据形态带偏；
而且每次造合成数据都散落在 tmp/ 渲染脚本里，重复又容易漏字段（2026-09-15
就踩过：渲染图「段位显示不全」，其实是脚本忘了给 `badge_numeral`）。

→ **以后做 UI（悬浮窗改版 / 详情页 / 统计窗）直接 import 这份**：

    from core.ui_demo import demo_snapshot, demo_recent

    $PY tools/duel_ui.py --demo        # 挂着看（悬浮窗吃 demo 数据，不碰真库）
    $PY tools/render_ui_demo.py        # 出 PNG（tmp/_demo_float.png / _demo_empty.png）

**9 行覆盖的形态**（`demo_recent()` 新 -> 旧）：

====  ========  ====  ====  ==========================================
行    时间      结果  先后  覆盖点
====  ========  ====  ====  ==========================================
1     今天      胜    先    已标注 · **同段升段** `[白金图]II ↑`
2     今天      负    后    已标注 · **跨段掉段** `[金图]I ↓`
3     今天      负    —    **未标注**（琥珀提示 + `!` 角标）· 有对手名
4     今天      胜    先    **巅峰局**（悬浮窗只显图标；看板战绩页段位列 = 图标 + 积分 1515.62）
5     今天      胜    后    **没段位数据**（段位格空着 —— 真库最近三局的形态）
6     昨天      平    先    **平局**（灰胶囊）· same（不画箭头）
7     昨天      胜    先    **跨段升段** `[白金图]V ↑`
8     昨天      负    后    已标注 · **缺卡组图**（回退 `_default.webp`）
9     昨天      胜    —    先后未知 · **长对手名 + 长卡组名**（测省略）
====  ========  ====  ====  ==========================================

统计数字 = 设计稿那套（32 场 21-11 / 先攻 18 胜率 77.8% / 后攻 12 胜率 41.7% /
先手率 60%），并故意带上 `initiative_rate`（赢硬币率）—— 先手率**不许用它**
（用户口径「硬币 ≠ 先后手」），带着是为了让拿错字段的界面立刻露馅。

纪律（同 :mod:`core.ui_state`）：

1. **本模块不许 import 任何 GUI 框架** —— 无头单测要能直接用；
2. **不许抛异常** —— 它是给界面吃的，坏了自己烂掉别炸 UI；
3. 日期用 ``date.today()`` 现算（写死会烂：过两天 `is_today` 全变 False）。
"""
from __future__ import annotations

import datetime
from typing import List, Optional

from core import ui_state as _ui_state
from core.ui_state import (CROSS_SCOPE_NOTE, MODE_IDLE, Probe, UiSnapshot)

# ------------------------------------------------------------------ 图片路径
# 只引用仓库里真实存在的文件（data/deck_images_custom/ 进版本库，不会跑）
_IMG = 'data/deck_images_custom/%s.webp'
_RANK = 'data/rank_icons/%s.webp'
PEAK_ICON = 'data/mode_icons/peak.webp'


# 大段编号 -> 英文名（**demo 专用副本**；真源是 core.ranks.RANK_NAMES）
_RANK_NAME = {2: 'Silver', 3: 'Gold', 4: 'Gold',
              5: 'Platinum', 6: 'Diamond', 7: 'Master'}


def _label(rank) -> str:
    """`(5, 2)` -> `'Platinum 2'`（demo 里给 rank_label_after 用；对齐 ranks.rank_label）。"""
    if not rank or not rank[0]:
        return ''
    name = _RANK_NAME.get(rank[0], 'Rookie')
    return '%s %d' % (name, rank[1]) if rank[1] else name


#: 覆盖目录（`data/deck_images_custom/`）—— **进版本库**，demo 引用的图只能指这里
_IMG_CUSTOM = 'data/deck_images_custom/%s.webp'


def _row(match_id, hour, day_offset, result, went_first,
         my_deck, my_img, opp_deck, opp_img, opp_player,
         rank_before, rank_after=None, delta=None,
         numeral='', numeral_after='', icon_after=None,
         badge_icon=None, badge_text='', revealed_ids=None,
         suggest=None, peak_rate=None) -> dict:
    """按 :func:`core.ui_state._recent_row` 的行格式拼一行 demo（字段一个不少）。"""
    d = datetime.date.today() - datetime.timedelta(days=day_offset)
    # 分钟给个稳定又各不相同的值（纯装饰，界面只展示 HH:MM）
    started = '%s %02d:%02d' % (d.isoformat(), hour, (hour * 17 + 23) % 60)
    if badge_icon is None and rank_before and rank_before[0]:
        badge_icon = _RANK % _RANK_NAME.get(rank_before[0], 'Rookie')
    return {
        'match_id': match_id,
        'started_at': started,
        'day': started[5:10],
        'time': started[11:16],
        'is_today': day_offset == 0,
        'result': result,
        'went_first': went_first,          # 1=先攻 / 0=后攻 / None=未知
        'my_deck': my_deck,
        'my_deck_img': _IMG % my_img,
        'opp_deck': opp_deck,
        'opp_deck_img': (_IMG % opp_img) if opp_img else None,
        'opp_known': bool(opp_deck),
        # 露出卡（M5 反推的输入）—— 只有 demo0003 传值，见那一行的注释
        'opp_revealed_ids': revealed_ids,
        'opp_revealed': None,
        # M5 反推建议（真库里由 `ui.app._attach_suggestions` 挂上；demo 直接写死）——
        # 「未标注 + 有建议」那格的形态靠它演示（用户 2026-09-16）
        'suggest_deck': (suggest or {}).get('deck'),
        'suggest_name': (suggest or {}).get('name'),
        'suggest_img': (suggest or {}).get('img'),
        'opp_player': opp_player,
        'badge_icon': badge_icon,
        'badge_text': badge_text,
        # 巅峰积分（悬浮窗不消费；看板战绩页段位列用，见 _recent_row 同名字段）
        'peak_rate': peak_rate,
        'rank_before': rank_before,
        'rank_after': rank_after,
        'rank_delta': delta,
        'rank_label_before': badge_text,
        # ⚠️ 与 _recent_row 同语义：升/掉段给 **after 段位全称**（tooltip =「全称（升段/掉段）」）
        'rank_label_after': (badge_text if delta == 'same' else
                             (_label(rank_after)
                              if delta in ('up', 'down') else '')),
        'rank_icon_after': icon_after,
        'badge_numeral': numeral,
        'badge_numeral_after': numeral_after,
    }


# ------------------------------------------------------------------ 沙盒
#: demo 模式的**内存标注**（match_id -> {'opp_deck': 名, 'opp_deck_id': id}）。
#: 只活在进程里：重启 demo 自动还原 pristine，真库永远不碰（2026-09-15）。
_MARKS: dict = {}
#: 沙盒里 upsert 出来的对手卡组（name -> {'opp_deck_id', 'aliases'}），
#: id 用**负数**跟真库的 opp_decks 自增 id 隔开。
_SANDBOX_OPP: dict = {}
_SANDBOX_NEXT_ID = -1000
#: demo 模式的**内存胜负修正**（match_id -> 'Win'/'Lose'/'Draw'/None）——
#: demo 里点结果格改得动、看得见，真库一个字节不动（2026-09-18 晚）。
_RESULTS: dict = {}

#: 沙盒 ``repair_match_result`` 的「这一列不动」哨兵（与 `core.storage._KEEP` 同义）
_KEEP_RESULT = object()


def demo_repair_result(match_id, result):
    """沙盒改胜负（`DemoSandboxStorage.repair_match_result` 落到这里）。"""
    _RESULTS[str(match_id)] = result
    return 1


def _apply_demo_results(rows):
    """把沙盒改过的胜负套回行上（`demo_recent` / `demo_stats_page` 共用）。"""
    for r in rows or []:
        mid = str(r.get('match_id'))
        if mid in _RESULTS:
            r['result'] = _RESULTS[mid]
    return rows


def demo_mark(match_id, name, opp_deck_id, img=None):
    """沙盒标注一局 —— :meth:`DemoSandboxStorage.mark_opp_deck` 落到这里。

    ⚠️ **`img` 也要记**：2026-09-15 用户实测「用选择器换卡组，只有名字换了、图片没换」
    —— 因为沙盒只存了名字，`demo_recent()` 套回去时 `opp_deck_img` 还是旧值（默认图）。
    """
    _MARKS[str(match_id)] = {'opp_deck': name or '',
                             'opp_deck_id': opp_deck_id,
                             'opp_deck_img': img}


def demo_reset_marks():
    """清空沙盒标注**与沙盒胜负**（重启也会自然还原，这是手动重置口）。"""
    global _SANDBOX_NEXT_ID
    _MARKS.clear()
    _SANDBOX_OPP.clear()
    _RESULTS.clear()
    _SANDBOX_NEXT_ID = -1000


def demo_sandbox_opp_decks():
    """沙盒 upsert 过的对手卡组，追加进 ``list_opp_decks`` 的结果。"""
    return [(v['opp_deck_id'], name, v['aliases'], 0)
            for name, v in _SANDBOX_OPP.items()]


def _demo_recent_base() -> List[dict]:
    """9 行 demo 基底（新 -> 旧），覆盖面见模块头表格（未套沙盒标注）。"""
    return [
        # 1 今天 · 胜 · 先攻 · 同段升段 Platinum III -> II
        _row('demo0001', 21, 0, 'Win', 1,
             '闪刀姬', 'Sky_Striker', '烙印', 'Branded', 'Sione',
             (5, 3), (5, 2), 'up', 'III', 'II',
             icon_after=_RANK % 'Platinum',        # 2026-09-15 补：升降段画的是当前段位图标
             badge_text='Platinum 3'),
        # 2 今天 · 负 · 后攻 · 跨大段掉段 Platinum I -> Gold I
        _row('demo0002', 20, 0, 'Lose', 0,
             '闪刀姬', 'Sky_Striker', '天盃龙', 'Tenpai_Dragon', 'もっちゃん',
             (5, 1), (4, 1), 'down', 'I', 'I',
             icon_after=_RANK % 'Gold', badge_text='Platinum 1'),
        # 3 今天 · 负 · 先后未知 · 未标注（有对手名）+ **带露出卡** —— 选择器面板的
        #   「M5 建议」只有这条能演示（真库待标注队列里带露出卡的局 = 0）。
        #   卡取自参照库 `data/mdm_deck_cards.json` 的 **Clown Crew / 小丑戏帮**（Tier3，
        #   5 张都 100% 使用率）→ `deck_guess.suggest()` 实测 top1 = 小丑戏帮（命中 5/5）。
        _row('demo0003', 20, 0, 'Lose', None,
             '闪刀姬', 'Sky_Striker', None, None, 'NightmareEX',
             (5, 2), None, None, 'II', '',
             badge_text='Platinum 2',
             revealed_ids='[22568, 22582, 9455, 11384, 12950]',
             # 未标注 + 有建议 → 那格显示「小丑戏帮」（图进版本库的 custom 目录）
             suggest={'name': '小丑戏帮', 'img': _IMG_CUSTOM % 'Clown_Crew',
                      'deck': {'name': 'Clown Crew', 'name_cn': '小丑戏帮',
                               'name_display': None, 'name_ja': None,
                               'aliases': [], 'tier': 'Tier2'}}),
        # 4 今天 · 胜 · 先攻 · 巅峰局（2026-09-28 二口径：悬浮窗只显巅峰图标；
        #    peak_rate 字段只给**看板战绩页**的段位列画「图标 + 积分」）
        _row('demo0004', 19, 0, 'Win', 1,
             '烙印', 'Branded', '龙链巳剑', 'Ryzeal_Mitsurugi', 'ピークマスター',
             None, None, None, '', '',
             badge_icon=PEAK_ICON, badge_text='巅峰决斗',
             peak_rate='1515.62'),
        # 5 今天 · 胜 · 后攻 · 没段位数据（真库最近三局的形态）
        _row('demo0005', 19, 0, 'Win', 0,
             '闪刀姬', 'Sky_Striker', '尤贝尔', 'Yubel', None,
             (None, None), None, None, '', ''),
        # 6 昨天 · 平 · 先攻 · same（不画箭头）
        _row('demo0006', 22, 1, 'Draw', 1,
             '天盃龙', 'Tenpai_Dragon', '粛声', 'Voiceless_Voice',
             'Ash Blossom Enjoyer',
             (5, 2), (5, 2), 'same', 'II', 'II',
             badge_text='Platinum 2'),
        # 7 昨天 · 胜 · 先攻 · 跨大段升段 Gold I -> Platinum V
        _row('demo0007', 21, 1, 'Win', 1,
             '闪刀姬', 'Sky_Striker', '码丽丝', 'Maliss', 'Zabuza',
             (4, 1), (5, 5), 'up', 'I', 'V',
             icon_after=_RANK % 'Platinum', badge_text='Gold 1'),
        # 8 昨天 · 负 · 后攻 · 缺卡组图（回退默认图）
        _row('demo0008', 21, 1, 'Lose', 0,
             '白森林', 'White_Forest', '拉比林斯', None, 'がんばる人',
             (4, 3), None, None, 'III', '',
             badge_text='Gold 3'),
        # 9 昨天 · 胜 · 先后未知 · 长名字 + 长卡组名（测省略 / tooltip）
        _row('demo0009', 20, 1, 'Win', None,
             '半龙女仆', 'Dragonmaid', '白森林阿兹玛纳', 'White_Forest_Azamina',
             '这是一个非常长的对手名字啊啊啊',
             (5, 4), None, None, 'IV', '',
             badge_text='Platinum 4'),
    ]


def demo_recent() -> List[dict]:
    """9 行 demo（新 -> 旧）+ **套上沙盒标注**（demo 模式里标过的对手卡组）。

    demo 模式里用选择器标注 → :func:`demo_mark` 记进内存 → 这里套回去，
    行上立即显示标注结果；重启进程自动还原 pristine。
    """
    rows = _apply_demo_results(_demo_recent_base())
    for r in rows:
        m = _MARKS.get(str(r.get('match_id')))
        if m:
            r['opp_known'] = True
            r['opp_deck'] = m['opp_deck']
            r['opp_deck_id'] = m['opp_deck_id']
            if m.get('opp_deck_img'):          # ★ 图也要跟着换（见 demo_mark 注释）
                r['opp_deck_img'] = m['opp_deck_img']
    return rows


#: 统计数字 = 设计稿那套。**故意带 `initiative_rate`**（赢硬币率）：
#: 先手率用错字段（没走 `first_hand_pct`）的界面，拿到它会显示 53.6% 而不是 60.0%
DEMO_STATS = {
    'n_total': 32, 'n_win': 21, 'n_lose': 11, 'n_draw': 0,
    'win_rate': 21.0 / 32.0,                       # 65.6%
    'first_n': 18, 'first_w': 14, 'first_rate': 14.0 / 18.0,    # 77.8%
    'second_n': 12, 'second_w': 5, 'second_rate': 5.0 / 12.0,   # 41.7%
    'first_hand_pct': 18.0 / 30.0,                 # 60.0%（先攻 18 / 已知先后 30）
    'initiative_n': 28, 'initiative_rate': 15.0 / 28.0,         # 53.6%（★不该被显示）
    'unmarked': 18,
}

#: 今天 5 场 3 胜 2 负（和 demo_recent 的「今天」那 5 行对得上）
DEMO_TODAY = {'n': 5, 'w': 3, 'l': 2, 'd': 0}


class DemoSandboxStorage(object):
    """demo 模式的沙盒 Storage：**参考数据只读真库，标注写入内存**。

    选择器要能完整走通（搜 meta -> 选 -> 标注 -> 行上立即显示），
    但绝不能把假 match_id / 沙盒 opp_deck / 假胜负写进真库 —— 所以拦三个写口
    （``upsert_opp_deck`` / ``mark_opp_deck`` / ``repair_match_result``）
    和 ``list_opp_decks``（要并上沙盒新登记的），其余全部透传（只读）。
    """

    def __init__(self, real):
        self._real = real
        self._imgs = None                  # 懒建：DeckImages 要读 meta 清单/别名索引

    def _images(self):
        if self._imgs is None:
            from core.deck_images import DeckImages
            self._imgs = DeckImages(self._real)
        return self._imgs

    # ---- 写口：全部进内存沙盒 --------------------------------------
    def upsert_opp_deck(self, name, aliases=None):
        """登记对手卡组 -> 沙盒负数 id（同名复用）。"""
        global _SANDBOX_NEXT_ID
        got = _SANDBOX_OPP.get(name)
        if got is not None:
            return got['opp_deck_id']
        oid = _SANDBOX_NEXT_ID
        _SANDBOX_NEXT_ID -= 1
        _SANDBOX_OPP[name] = {'opp_deck_id': oid, 'aliases': aliases}
        return oid

    def mark_opp_deck(self, match_id, opp_deck_id=None, name=None,
                      note=None):
        """标注 -> 内存；真库的 matches 表一个字节不动。

        ⚠️ 连**图片路径**一起记下来 —— 否则换完卡组行上还是旧图（用户 2026-09-15 实测）。
        """
        img = None
        if name:
            try:
                img = self._images().resolve(name)
            except Exception:
                img = None
        demo_mark(match_id, name, opp_deck_id, img=img)
        return True

    def repair_match_result(self, match_id, result=_KEEP_RESULT,
                            result_src=_KEEP_RESULT,
                            result_note=_KEEP_RESULT,
                            winbits=_KEEP_RESULT):
        """手动改胜负 -> 只进内存沙盒（真库不碰）；界面重画就看得见。"""
        if result is _KEEP_RESULT:
            return 1      # 没带 result 的调用不改胜负：当作无事发生
        demo_repair_result(match_id, result)
        return 1

    def list_opp_decks(self, limit=None):
        """真库对手卡组表 + 沙盒里新登记的（负数 id 排最后）。"""
        rows = list(self._real.list_opp_decks(limit=limit))
        rows.extend(demo_sandbox_opp_decks())
        return rows

    # ---- 其余全部透传（读参考数据：meta 清单 / 卡组图 / ……）---------
    def __getattr__(self, item):
        return getattr(self._real, item)


# ------------------------------------------------------- S6 统计窗的 demo 数据
# 形状与 `core.ui_state.stats_window_data()` **逐键对齐**（少一个键界面就少一块）。
# ⚠️ 数字与真库同构（不是随便编的）：ranked 33 / event 20 / 合计 **53 场 30-23**，
#    先手 25 场 18 胜、后手 21 场 7 胜 —— 与 2026-09-17 真库实测逐位一致，
#    这样「demo 能画出来」就等于「真库的形状界面吃得下」。
def _st(n, w, l, f_n=0, f_w=0, s_n=0, s_w=0) -> dict:
    """造一份与 `Storage.get_stats()` **同键**的 stats（比率按同一个公式算）。"""
    return {
        'date': None,
        'n_total': n, 'n_win': w, 'n_lose': l, 'n_draw': 0,
        'win_rate': (w / n) if n else None,
        'first_n': f_n, 'first_wins': f_w,
        'first_rate': (f_w / f_n) if f_n else None,
        'second_n': s_n, 'second_wins': s_w,
        'second_rate': (s_w / s_n) if s_n else None,
        'first_hand_pct': (f_n / (f_n + s_n)) if (f_n + s_n) else None,
        'initiative_n': 0, 'initiative_won': 0, 'initiative_rate': None,
        'unmarked': 0,
    }


#: S6 四组（顺序与 `get_group_stats()` 一致）
DEMO_GROUPS = (
    ('ranked', '排名决斗', _st(33, 22, 11, 19, 15, 12, 5)),
    ('peak', '巅峰决斗', _st(0, 0, 0)),
    ('dc', 'DC杯', _st(0, 0, 0)),
    ('event', '活动决斗', _st(20, 8, 12, 6, 3, 9, 2)),
)

#: 逐组 by-day（**合并后 = 53 场**；两组日子特意不重叠，好让热力图有多种深浅）
DEMO_DAILY_BY_GROUP = {
    'ranked': {'2026-09-02': {'n': 5, 'w': 3, 'l': 2, 'd': 0},
               '2026-09-05': {'n': 6, 'w': 4, 'l': 2, 'd': 0},
               '2026-09-06': {'n': 4, 'w': 3, 'l': 1, 'd': 0},
               '2026-09-10': {'n': 7, 'w': 4, 'l': 3, 'd': 0},
               '2026-09-12': {'n': 5, 'w': 4, 'l': 1, 'd': 0},
               '2026-09-15': {'n': 4, 'w': 3, 'l': 1, 'd': 0},
               '2026-09-16': {'n': 2, 'w': 1, 'l': 1, 'd': 0}},
    'event': {'2026-09-11': {'n': 8, 'w': 4, 'l': 4, 'd': 0},
              '2026-09-13': {'n': 7, 'w': 2, 'l': 5, 'd': 0},
              '2026-09-17': {'n': 5, 'w': 2, 'l': 3, 'd': 0}},
}

#: 逐组按我方卡组（**合并后 = 53 场**，与上面两组同源）
DEMO_DECKS_BY_GROUP = {
    'ranked': [('K9vs', _st(16, 12, 4, 10, 8, 6, 4)),
               ('闪刀姬', _st(15, 8, 7, 7, 5, 6, 1)),
               ('耀圣杀手旋律', _st(1, 1, 0, 1, 1, 0, 0)),
               ('利希德', _st(1, 1, 0, 1, 1, 0, 0))],
    'event': [('青眼白龙', _st(12, 4, 8, 3, 1, 5, 1)),
              ('(未识别)', _st(8, 4, 4, 3, 2, 4, 1))],
}

#: 交叉战绩（**恒为主口径**，不随筛选器变）—— 前两条是伪条目，用来验「默认隐藏」开关
DEMO_CROSS = (
    ('K9vs', '未知卡组', 16, 12, 4, 0),
    ('青眼白龙', '(未标注)', 3, 1, 2, 0),
    ('闪刀姬', '混轴卡组', 2, 2, 0, 0),
    ('闪刀姬', '烙印', 2, 0, 2, 0),
    ('闪刀姬', '烙印星辰', 2, 1, 1, 0),
    ('闪刀姬', '珠泪', 2, 1, 1, 0),
    ('闪刀姬', '小丑戏帮', 1, 1, 0, 0),
    ('闪刀姬', '码丽丝', 1, 0, 1, 0),
)


def _sum_stats(parts) -> dict:
    """把几份 stats 按绝对值相加、比率重算（**与 `ui_state.merged_deck_stats` 同口径**）。"""
    keys = ('n_total', 'n_win', 'n_lose', 'n_draw', 'first_n', 'first_wins',
            'second_n', 'second_wins')
    acc = dict((k, sum(int(p.get(k) or 0) for p in parts)) for k in keys)
    return _st(acc['n_total'], acc['n_win'], acc['n_lose'],
               acc['first_n'], acc['first_wins'], acc['second_n'], acc['second_wins'])


# ---------------------------------------------------- D1「战绩」页的 demo 数据
#: 形状与 `core.ui_state.stats_page_data()` **逐键对齐**（少一个键界面就少一块）
def _prow(date, time, result, went_first, opp_player, turns,
          rank_before=None, rank_after=None, opp_deck='',
          opp_rb='', opp_ra='', opp_img=None, mode='ranked') -> dict:
    """拼一行「对局记录」demo 行（字段 = 界面那 10 列要用的）。

    ★ `mode` 与真库那条路**同一口径**：**不是天梯就不给段位**
    （用户 2026-09-18：「为什么你的最近对局在活动局也显示了段位？」）。
    下面几组 demo 行**故意**在活动/巅峰/DC 局里也传 `rank_*` 参数 ——
    看的就是"界面会不会照样画出来"（画了就是 bug，单测会红）。
    """
    from core import ranks
    from core.storage import MODE_GROUP_NAMES
    if mode != 'ranked':
        rank_before = rank_after = None
    rank_before = tuple(rank_before) if rank_before else (None, None)
    rank_after = tuple(rank_after) if rank_after else (None, None)
    delta = ranks.compare(rank_before, rank_after)
    return {
        'match_id': 'demo_%s_%s' % (date, time.replace(':', '')),
        'date': date, 'time': time,
        'result': result, 'went_first': went_first,
        'opp_player': opp_player, 'total_turns': turns,
        'mode_group': mode,
        'mode_label': MODE_GROUP_NAMES.get(mode, ''),
        #: ⚠️ 与真库 `_page_row` **同形态**：没有段位就给 `None`，
        #: 不给 `(None, None)`（两源形态不一致 = "demo 绿、真机不对"）
        'rank_before': rank_before if rank_before[0] else None,
        'rank_after': rank_after if rank_after[0] else None,
        'rank_delta': delta,
        'rank_label_before': ranks.rank_label(*(rank_before or (None, None))),
        'rank_label_after': ranks.rank_label(*(rank_after or (None, None))),
        'rank_icon_before': ranks.icon_path((rank_before or (None,))[0]),
        'rank_icon_after': ranks.icon_path((rank_after or (None,))[0]),
        'badge_numeral': ranks.numeral_text((rank_before or (None, None))[1],
                                            (rank_before or (None, 0))[0])
        if (rank_before or (None,))[0] else '',
        'badge_numeral_after': ranks.numeral_text((rank_after or (None, None))[1],
                                                  (rank_after or (None, 0))[0])
        if (rank_after or (None,))[0] else '',
        'opp_deck': opp_deck, 'opp_known': bool(opp_deck),
        'opp_revealed_ids': None, 'opp_deck_id': None,
        'opp_deck_img': opp_img,
    }


#: 各模式的「战绩总览」数字 —— 与 `DEMO_GROUPS` 的四组**同源**（53 = 33 + 20）：
#: ranked 33(22-11) 先19-15 后12-5；event 20(8-12) 先6-3 后9-2。
_DEMO_OVERVIEW_BY_MODE = {
    None: _st(53, 30, 23, 25, 18, 21, 7),
    'ranked': _st(33, 22, 11, 19, 15, 12, 5),
    'peak': _st(0, 0, 0),
    'dc': _st(0, 0, 0),
    'event': _st(20, 8, 12, 6, 3, 9, 2),
}


def demo_stats_page(mode_group=None, month=None, account=None) -> dict:
    """D1「战绩」页 demo：**三块都给**（热力图 / 总览五格 / 对局记录分组）。

    数字与真库同构（53 场 30-23、最高段位 Platinum 1），组头合计 == 行数合计。

    ★ 签名与真库 `stats_page_data` 对齐（2026-10-01 起收 ``month``、
    2026-10-03 起收 ``account``）—— demo 数据固定在 9 月/不分账号，
    参数**忽略**（月卡/账号切换只影响真库页；``accounts`` 形态给两个
    账号喂切换器）。

    ★ ``mode_group``（2026-09-18 七轮）：与真库 `stats_page_data` **同一条路** ——
    组内行按模式筛、空组丢弃、组头用 `group_header` 重算；overview 换成该模式的数字；
    「最高段位」只在全部/排名决斗下有值（别的模式没有天梯局）。
    """
    def img(name):
        #: demo 只引**进版本库**的 `data/deck_images_custom/`（别指 gitignore 的 deck_images/）
        return (_IMG_CUSTOM % name) if name else None

    # ── 本轮（当前那轮）：闪刀姬 4 把 + K9vs 3 把 ──────────────────────────────
    g1 = ('闪刀姬', _st(4, 2, 2, 2, 1, 2, 1), [
        _prow('09-16', '23:45', 'Win', True, 'Taolanamz2', 2,
              (5, 3), (5, 2), '烙印', opp_img=img('Branded')),
        _prow('09-16', '23:27', 'Lose', False, 'なごみ', 4,
              (5, 2), (5, 2), '烙印星辰', opp_img=img('Branded_Elfnote')),
        _prow('09-16', '23:03', 'Win', True, 'set', 1,
              (5, 2), (5, 2), '', opp_img=None),
        _prow('09-16', '22:44', 'Lose', False, 'Thragg', 5,
              None, None, '码丽丝', opp_img=img('Maliss')),
    ])
    #: ★ 这一组是**活动决斗** —— 行里照样塞了段位参数，界面**必须一个字都不画**
    #: （用户 2026-09-18 点名的现象：「活动局也显示了段位」）
    g2 = ('K9vs', _st(3, 2, 1, 2, 2, 1, 0), [
        _prow('09-16', '23:20', 'Win', True, '卡手少年', 2,
              (5, 4), (5, 3), '未知卡组', opp_img=None, mode='event'),
        _prow('09-16', '23:05', 'Win', True, 'Revi', 9,
              (5, 5), (5, 4), '未知卡组', opp_img=None, mode='event'),
        _prow('09-16', '22:50', 'Lose', False, 'あいとー', 5,
              None, None, '', opp_img=None, mode='event'),
    ])
    # ── 上一轮（09-14 20:00 起）：**同一副闪刀姬再出现一次** ────────────────────
    #: ★ 这一轮覆盖**巅峰决斗 / DC杯**（模式列要能一眼看出三种以上）
    g3 = ('闪刀姬', _st(2, 1, 1, 1, 1, 1, 0), [
        _prow('09-14', '21:00', 'Win', True, 'Kader', 1,
              (5, 2), (5, 1), '闪刀姬', opp_img=img('Sky_Striker'),
              mode='peak'),
        _prow('09-14', '20:40', 'Lose', False, '28K白少', 2,
              (5, 2), (5, 2), '纠罪巧', opp_img=None, mode='dc'),
    ])
    g4 = ('烙印', _st(1, 1, 0, 1, 1, 0, 0), [
        _prow('09-14', '20:10', 'Win', True, 'Sione', 3,
              (5, 3), (5, 2), '烙印', opp_img=img('Branded')),
    ])
    #: ★ 沙盒改过的胜负先套回去 —— 必须在下面 `group_header` **之前**，
    #: 否则组头还按老胜负算（demo 里「改了结果、数字没动」看着像没生效）
    for _g in (g1, g2, g3, g4):
        _apply_demo_results(_g[2])
    #: 轮次标签（真库那条路是 `match_groups` 用 `split_sessions` 算的；demo 直接写死）
    for _g, _label, _cur in ((g1, '本轮', True), (g2, '本轮', True),
                             (g3, '09-14 20:00 起', False),
                             (g4, '09-14 20:00 起', False)):
        _g[1]['round_label'] = _label
        _g[1]['is_current'] = _cur
        _g[1]['round_start'] = '2026-09-16 22:44:00' if _cur else '2026-09-14 20:00:00'
    #: ⚠️ 组头**按行重算**（与真库那条路同一个函数）—— 否则 demo 会出现
    #:    「组头 8-7、下面只有 4 行」这种自相矛盾的样子（2026-09-18 修）
    groups = [(n, dict(_ui_state.group_header(_rows), img=_s.get('img'),
                       round_label=_s.get('round_label'),
                       is_current=_s.get('is_current'),
                       round_start=_s.get('round_start')), _rows)
              for n, _s, _rows in (g1, g2, g3, g4)]
    if mode_group:
        #: ★ 与真库 `match_groups(mode_group=…)` 同一条路：筛行 → 丢空组 → 重算组头
        groups = [(n, st, [r for r in rows if r.get('mode_group') == mode_group])
                  for n, st, rows in groups]
        groups = [(n, dict(st, **_ui_state.group_header(rows)), rows)
                  for n, st, rows in groups if rows]
    #: 组顺序 = 轮次倒序（本轮在前）→ 同轮内按「该组最近一局」倒序
    groups.sort(key=lambda t: (t[1].get('is_current', False),
                               (t[2][0]['date'], t[2][0]['time']) if t[2] else ('', '')),
                reverse=True)
    #: ★ 热力图喂**逐组合并后的** daily（与真库 `merged_daily` 同源）——
    #: 只喂 ranked 的话格子数太少，看不出"块大小随场数变"的效果
    daily = {}
    for _key in ('ranked', 'event'):
        for _day, _v in DEMO_DAILY_BY_GROUP.get(_key, {}).items():
            _acc = daily.setdefault(_day, {'n': 0, 'w': 0, 'l': 0, 'd': 0})
            for _k in ('n', 'w', 'l', 'd'):
                _acc[_k] += _v[_k]
    return {
        'mode_group': mode_group,
        'wins_to_promote': 3,
        'overview': _DEMO_OVERVIEW_BY_MODE.get(mode_group, _DEMO_OVERVIEW_BY_MODE[None]),
        'best_rank': (5, 1) if mode_group in (None, 'ranked') else None,
        'daily': daily,
        'months': sorted({d[:7] for d in daily}),
        'groups': groups,
        'session_live': True,
        'session_since': '2026-09-16 22:44:00',
        'month': '2026-09', 'month_since': '2026-09-01 00:00:00',
        'account': None,
        'accounts': [('示例玩家', 53, '2026-09-16 23:45:00'),
                     ('示例玩家2', 7, '2025-11-02 21:00:00')],
        'group_total': sum(s['n_total'] for _n, s, _r in groups),
        'row_total': sum(len(r) for _n, _s, r in groups),
    }


def demo_stats_window(mode_group=None, show_pseudo: bool = False) -> dict:
    """S6 统计窗的 demo 数据（形状与真库那条路**完全同构**）。

    ``mode_group=None`` = 「全部」（界面口径）；给组名 = 只算那一组。
    伪条目的过滤走 `core.ui_actions.NON_DECK_LABELS` —— **与真库同一份名单**，
    免得 demo 藏了、真库没藏（那才是最难查的一类不一致）。
    """
    from core.ui_actions import NON_DECK_LABELS

    # ⚠️ 分模式四组**恒为四组**（它是跨模式对照表，标题就写着「全部历史」）——
    #    真库那条路 `stats_window_data()` 也是这么给的。这里跟着筛过一次，
    #    会导致「demo 能过、真机不对」（2026-09-17 被单测抓出来）。
    groups = list(DEMO_GROUPS)
    active = [g for g in groups if mode_group is None or g[0] == mode_group]

    daily = {}
    for key, _name, _s in active:
        for day, v in DEMO_DAILY_BY_GROUP.get(key, {}).items():
            acc = daily.setdefault(day, {'n': 0, 'w': 0, 'l': 0, 'd': 0})
            for k in ('n', 'w', 'l', 'd'):
                acc[k] += v[k]

    deck_acc = {}
    for key, _name, _s in active:
        for name, st in DEMO_DECKS_BY_GROUP.get(key, []):
            deck_acc.setdefault(name, []).append(st)
    decks = [(name, _sum_stats(parts)) for name, parts in deck_acc.items()]
    decks.sort(key=lambda t: -t[1]['n_total'])

    cross = [r for r in DEMO_CROSS
             if show_pseudo or r[1] not in NON_DECK_LABELS]

    overview = _sum_stats([s for _k, _n, s in active])
    return {
        'mode_group': mode_group,
        'overview': overview,
        'groups': groups,
        'daily': daily,
        'months': sorted({d[:7] for d in daily}),
        'cross': cross,
        'decks': decks,
        'cross_scope': CROSS_SCOPE_NOTE,
    }


def demo_snapshot(unmarked: int = 18) -> Optional[UiSnapshot]:
    """整份 demo 快照 —— 界面直接 ``set_snapshot(demo_snapshot())``。

    ``by_deck`` / ``heatmap`` 留空：它们的形状还没被任何 UI 消费过，
    编一个出来反而会把后来人带偏（要用时先看 ``Storage.get_stats_by_deck``
    的真实形状再加）。**失败返回空快照，不抛**。
    """
    try:
        snap = UiSnapshot(mode=MODE_IDLE)
        snap.today = dict(DEMO_TODAY)
        snap.stats = dict(DEMO_STATS)
        snap.unmarked = [{'match_id': 'demo_unmarked_%02d' % i}
                         for i in range(int(unmarked))]
        snap.probe = Probe(n_total=32, max_rowid=54, unmarked=int(unmarked))
        snap.recent = demo_recent()
        snap.my_rank = {'rank': 5, 'rate': 2, 'label': 'Platinum 2',
                        'icon': _RANK % 'Platinum', 'from_match': 'demo0001'}
        return snap
    except Exception:
        return UiSnapshot(mode=MODE_IDLE)


# ------------------------------------------------------------------ 礼盒 demo
# 看板「礼盒」页的假数据（2026-09-19 用户定：这个功能进看板）。
# 形状 = core/memread/present_box.read_items 的条目（字段名照抄真链路）；
# 数字编的，覆盖三个形态：多日分组 / 单日多条 / 非宝石物品（cat≠1）。
DEMO_PRESENT_ROWS = [
    {'pID': 101, 'category': 1, 'item_id': 1, 'quantity': 60,
     'limit_date': '2026/10/6 19:01', 'message': '已经达到持有上限的物品'},
    {'pID': 102, 'category': 1, 'item_id': 1, 'quantity': 540,
     'limit_date': '2026/10/9 12:40', 'message': '已经达到持有上限的物品'},
    {'pID': 103, 'category': 1, 'item_id': 1, 'quantity': 40,
     'limit_date': '2026/10/9 12:44', 'message': '已经达到持有上限的物品'},
    {'pID': 104, 'category': 1, 'item_id': 1, 'quantity': 160,
     'limit_date': '2026/10/12 15:14', 'message': '已经达到持有上限的物品'},
    {'pID': 105, 'category': 1, 'item_id': 1, 'quantity': 25,
     'limit_date': '2026/10/12 15:25', 'message': '已经达到持有上限的物品'},
    {'pID': 106, 'category': 1, 'item_id': 1, 'quantity': 240,
     'limit_date': '2026/10/15 16:12', 'message': '已经达到持有上限的物品'},
    {'pID': 107, 'category': 2, 'item_id': 7, 'quantity': 1,
     'limit_date': '2026/10/15 16:12', 'message': ''},
    {'pID': 108, 'category': 1, 'item_id': 1, 'quantity': 120,
     'limit_date': '2026/10/18 22:09', 'message': '已经达到持有上限的物品'},
]


# ---------------------------------------------------- D2「对局分析」页的 demo 数据
#: 形状 = 交叉战绩 + **先攻/后攻拆分**（n = 先攻局 + 后攻局，胜同拆）。
#: ⚠️ **别动 `DEMO_CROSS`** —— S6 的 `demo_stats_window()` 还在消费它（审查 P3-2）。
#: 前两条是伪条目（验默认隐藏）；第 5 条是**镜像局**（真实战绩，用户拍板不用 50% 基线）；
#: 第 8/10 条只有后攻、第 9 条只有先攻 —— 用来验「页签下空格照实显示、行列轴不重排」。
DEMO_ANALYSIS_ROWS = (
    ('K9vs', '未知卡组', 16, 12, 4, 0, 8, 7, 8, 5),
    ('青眼白龙', '(未标注)', 3, 1, 2, 0, 2, 1, 1, 0),
    ('闪刀姬', '烙印', 3, 0, 3, 0, 2, 0, 1, 0),
    ('闪刀姬', '珠泪', 3, 1, 2, 0, 1, 1, 2, 0),
    ('闪刀姬', '闪刀姬', 2, 1, 1, 0, 1, 1, 1, 0),
    ('K9vs', '烙印', 2, 1, 1, 0, 1, 0, 1, 1),
    ('K9vs', '天杯龙', 2, 2, 0, 0, 1, 1, 1, 1),
    ('闪刀姬', '码丽丝', 1, 1, 0, 0, 0, 0, 1, 1),
    ('K9vs', '小丑戏帮', 1, 0, 1, 0, 1, 0, 0, 0),
    ('耀圣', '烙印', 1, 1, 0, 0, 0, 0, 1, 1),
)


class _DemoAnalysisStorage(object):
    """`demo_analysis_page` 专用桩：`get_cross_stats` 按 ``went_first`` 切片。

    ★ demo **直接吃真函数** `core.ui_state.cross_matrix`（与 D1「组头用真函数重算」
    同一个防漂移思路）—— demo 与真库唯一的差别只剩数据本身。
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def get_cross_stats(self, since=None, until=None, went_first=None,
                        mode_group=None):
        if went_first is None:
            return [(my, opp, n, w, l, d)
                    for my, opp, n, w, l, d, _fn, _fw, _sn, _sw in self._rows]
        out = []
        for my, opp, _n, _w, _l, _d, fn, fw, sn, sw in self._rows:
            if went_first:
                out.append((my, opp, fn, fw, fn - fw, 0))
            else:
                out.append((my, opp, sn, sw, sn - sw, 0))
        return out


#: demo 卡图映射（**只引**进版本库的 `data/deck_images_custom/`，别指 gitignore 目录）
DEMO_ANALYSIS_IMGS = {
    '闪刀姬': 'Sky_Striker', '烙印': 'Branded', '烙印星辰': 'Branded_Elfnote',
    'K9vs': 'K9', '码丽丝': 'Maliss', '耀圣': 'Elfnote',
}


def _analysis_img(name):
    path = DEMO_ANALYSIS_IMGS.get(name)
    return (_IMG_CUSTOM % path) if path else None


def demo_analysis_page(went_first=None, month=None, mode_group=None) -> dict:
    """D2「对局分析」页 demo：形状与 `core.ui_state.cross_matrix()` 逐键对齐。

    ``went_first``/``month``/``mode_group`` 与真库**同签名**（审查 P2-2：窗口传
    什么 demo 收什么，不加映射层）；demo 数据不随月份/模式筛选变（就一份），
    但会挂上卡图路径（``img``）与月份清单（``months``）。
    """
    from core.ui_state import cross_matrix
    data = cross_matrix(_DemoAnalysisStorage(DEMO_ANALYSIS_ROWS),
                        went_first=went_first)
    for a in data.get('col_avgs') or []:
        a['img'] = _analysis_img(a.get('deck'))
    for r in data.get('rows') or []:
        r['img'] = _analysis_img(r.get('deck'))
    data['months'] = ['2026-09', '2026-08']
    return data


# ---------------------------------------------------- D3「Tier 表」页的 demo 数据
#: 形状与 `storage.list_meta_decks()` 返回一致（dict 行）；数值照 2026-09 真榜缩编
DEMO_TIER_DECKS = (
    {'name': 'Dracotail', 'name_cn': '烙印星辰', 'name_display': None,
     'tier': 'Tier1', 'power': 16.5},
    {'name': 'Kewl Tune', 'name_cn': '酷调', 'name_display': None,
     'tier': 'Tier2', 'power': 9.0},
    {'name': 'Branded', 'name_cn': '烙印', 'name_display': None,
     'tier': 'Tier2', 'power': 8.5},
    {'name': 'Clown Crew', 'name_cn': '小丑戏帮', 'name_display': None,
     'tier': 'Tier2', 'power': 7.5},
    {'name': 'Sky Striker', 'name_cn': '闪刀姬', 'name_display': None,
     'tier': 'Tier3', 'power': 6.0},
    {'name': 'Branded Elfnote', 'name_cn': '烙印耀圣', 'name_display': None,
     'tier': 'Tier3', 'power': 5.5},
    {'name': 'Blue-Eyes', 'name_cn': '青眼', 'name_display': None,
     'tier': 'Tier3', 'power': 4.0},
    {'name': 'Vanquish Soul K9', 'name_cn': 'K9斗魂', 'name_display': None,
     'tier': 'Tier3', 'power': 3.0},
    {'name': 'Maliss', 'name_cn': '码丽丝', 'name_display': None,
     'tier': 'Trending', 'power': 1.0},
    {'name': 'Mitsurugi Yummy', 'name_cn': '巳剑黯蜜', 'name_display': None,
     'tier': 'Trending', 'power': 1.0},
)


class _DemoMetaStorage(object):
    """`demo_tier_page` 专用桩：只实现 `list_meta_decks`。"""

    def list_meta_decks(self, tiers=None, kinds=None, include_hidden=False):
        return list(DEMO_TIER_DECKS)


def demo_tier_page() -> dict:
    """D3「Tier 表」页 demo：**直接吃真函数** `ui_state.tier_page_data`
    （D2 同款防漂移）；卡图复用 `_analysis_img` 的中文名映射。"""
    from core.ui_state import tier_page_data
    data = tier_page_data(_DemoMetaStorage())
    for s in data.get('sections') or []:
        for d in s.get('decks') or []:
            d['img'] = _analysis_img(d.get('display'))
    return data


# ---------------------------------------------------- D3「禁限表」页的 demo 数据
#: 形状与 `storage.banlist_current()` / `list_banlist_changes()` 一致（缩编）
DEMO_BANLIST_CURRENT = (
    ('Snake-Eye Ash', 'Forbidden', '2026-09-03T07:30:00.000Z'),
    ('Mathmech Circular', 'Forbidden', '2026-09-03T07:30:00.000Z'),
    ('Rescue-ACE Air Lifter', 'Limited 1', '2026-09-03T07:30:00.000Z'),
    ('Tearlaments Kitkallos', 'Limited 1', '2026-06-01T07:30:00.000Z'),
    ('Kashtira Fenrir', 'Limited 2', '2026-09-03T07:30:00.000Z'),
)
DEMO_BANLIST_CHANGES = (
    {'card_en': 'Gravekeeper’s Inscription', 'from_status': 'Limited 1',
     'to_status': 'Forbidden', 'effective': '2026-09-03T07:30:00.000Z',
     'announced': None, 'url': None},
    {'card_en': 'Snake-Eye Ash', 'from_status': 'Limited 1',
     'to_status': 'Forbidden', 'effective': '2026-09-03T07:30:00.000Z',
     'announced': None, 'url': None},
    {'card_en': 'Mathmech Circular', 'from_status': None,
     'to_status': 'Limited 2', 'effective': '2026-06-01T07:30:00.000Z',
     'announced': None, 'url': None},
)


class _DemoBanlistStorage(object):
    """`demo_banlist_page` 专用桩：只实现禁限表那几个方法。

    ⚠️ `banlist_current_status` = **新数据源**（站内 banStatus，2026-09-21）——
    数据层优先走它；`banlist_current`（旧算法）只在回落时用。
    """

    def banlist_current_status(self):
        return [(n, s) for n, s, _e in DEMO_BANLIST_CURRENT]

    def banlist_current(self):
        return list(DEMO_BANLIST_CURRENT)

    def list_banlist_changes(self, limit=None, since=None):
        return list(DEMO_BANLIST_CHANGES)


def demo_banlist_page() -> dict:
    """D3「禁限表」页 demo：**直接吃真函数** `ui_state.banlist_page_data`
    （D2/D3 同款防漂移）。卡名反查走真表（`core.card_names`）。

    ⚠️ demo 用**英文名**（真库那批卡的英文名在卡表里也没有中文 —— 是站内名），
    所以这里不假装有中文；真库会反查出中文（如「墓穴指名者」）。
    """
    from core.ui_state import banlist_page_data
    return banlist_page_data(_DemoBanlistStorage())


# ---------------------------------------------- D3「卡组构成」页的 demo 数据
#: 参照库 demo（形状 = `data/mdm_deck_cards.json` 的 `decks` 段）—— 卡 id 全用**真卡 id**：
#: 主卡 / 额外判据走真卡表、卡绘缓存命中时也能看见真图，demo 不假装。
#: `999999999` 是**故意放进去的查不到的卡**（验 `unknown` 那条页脚文案）。
#: demo 参照库的「快照时间」（页头会显示，**固定值**免得测试漂）
DEMO_REF_FETCHED_AT = '2026-09-20T12:00:00'

DEMO_REF_DECKS = (
    ('Sky Striker', {'tier': 'Tier3', 'name_cn': '闪刀姬', 'total': 21,
                     'avgMainSize': 40, 'source_name': 'Sky Striker',
                     'cards': {'12950': {'per': 100, 'at': 3},     # 灰流丽
                               '13670': {'per': 100, 'at': 3},     # 闪刀姬－零
                               '13671': {'per': 100, 'at': 3},     # 闪刀起动－交闪
                               '17034': {'per': 100, 'at': 3},     # 闪刀启动－连闪
                               '21829': {'per': 100, 'at': 2},     # 绚岚之见神
                               '9455': {'per': 100, 'at': 1},      # 增殖的Z
                               '22089': {'per': 90, 'at': 3},      # 落胤与圣女
                               '5328': {'per': 90, 'at': 1},       # 增援
                               '13675': {'per': 86, 'at': 3},      # 闪刀机－黑寡妇锚
                               '11708': {'per': 62, 'at': 1},      # 幽鬼兔
                               '13669': {'per': 100, 'at': 1},     # 闪刀姬－雫（额外）
                               '13763': {'per': 100, 'at': 1},     # 闪刀姬－飒（额外）
                               '14947': {'per': 100, 'at': 1},     # 闪刀姬－泽克（额外）
                               '19011': {'per': 100, 'at': 1},     # 闪刀姬－卡梅莉娅（额外）
                               '21600': {'per': 100, 'at': 1},     # 试作闪刀姬－天津（额外）
                               '18448': {'per': 90, 'at': 1},      # 闪刀姬－阿塞蕾娅（额外）
                               '999999999': {'per': 5, 'at': 1}}}),
    ('Vanquish Soul K9', {'tier': 'Tier3', 'name_cn': 'K9VS', 'total': 8,
                          'avgMainSize': 40, 'source_name': 'Vanquish Soul K9',
                          'cards': {'12950': {'per': 100, 'at': 3},
                                    '14740': {'per': 100, 'at': 1},
                                    '18727': {'per': 100, 'at': 2},
                                    '18730': {'per': 100, 'at': 3},
                                    '10658': {'per': 88, 'at': 1},
                                    '10659': {'per': 88, 'at': 1},
                                    '17041': {'per': 75, 'at': 1}}}),
    ('Dracotail', {'tier': 'Tier1', 'name_cn': '烙印星辰', 'total': 25,
                   'avgMainSize': 40, 'source_name': 'Dracotail',
                   'cards': {'20500': {'per': 100, 'at': 3},     # 多多迷宝（主）
                             '21355': {'per': 100, 'at': 3},     # 星宿炮手 法魅那（主）
                             '21361': {'per': 100, 'at': 2},     # 计都星宿（主）
                             '22127': {'per': 100, 'at': 2},     # 白之落胤（主）
                             '15245': {'per': 100, 'at': 1},     # 阿尔白斯之落胤（主）
                             '17066': {'per': 100, 'at': 1},     # 烙印融合（主）
                             '21354': {'per': 100, 'at': 1},     # 星宿枪手 卢奇亚斯（主）
                             '21362': {'per': 100, 'at': 1},     # 罗睺星宿（主）
                             '21364': {'per': 100, 'at': 1},     # 星宿之吼炎（主）
                             '21789': {'per': 100, 'at': 1},     # 星宿响手 普莉库露（主）
                             '17069': {'per': 100, 'at': 1},     # 冰剑龙（额外）
                             '21360': {'per': 100, 'at': 1},     # 星宿法宫（额外）
                             '21448': {'per': 100, 'at': 1},     # 圣秘龙骑士（额外）
                             '22090': {'per': 100, 'at': 1}}}),  # 吞喰圣痕之龙（额外）
    ('Branded', {'tier': 'Tier2', 'name_cn': '烙印', 'total': 22,
                 'avgMainSize': 40, 'source_name': 'Branded',
                 'cards': {'15245': {'per': 100, 'at': 2},
                           '17066': {'per': 100, 'at': 3},
                           '16541': {'per': 100, 'at': 3},
                           '15272': {'per': 95, 'at': 2},     # 灰烬龙（额外）
                           '15994': {'per': 95, 'at': 2},     # 烙印龙 阿尔比昂（额外）
                           '17070': {'per': 90, 'at': 1},
                           '17069': {'per': 77, 'at': 1},
                           '18179': {'per': 82, 'at': 1}}}),
)

#: demo 的「我方卡组名 → 站内英文名」对应（= 真库覆盖表「别名」列那套）
DEMO_REF_ALIASES = {'闪刀姬': 'Sky Striker', 'K9vs': 'Vanquish Soul K9',
                    '烙印': 'Branded'}


class _DemoRefStorage(object):
    """`demo_deck_composition` 专用桩：显示名 + 模糊搜索（走**真** `core.deck_match`）。"""

    def meta_deck_display_map(self, include_hidden=True):
        return {name: d.get('name_cn') or name for name, d in DEMO_REF_DECKS}

    def list_meta_decks(self, tiers=None, kinds=None, include_hidden=False):
        """候选搜索词的来源（`_ref_search_terms` 要的：别名 + 日文名）。"""
        return [{'name': name, 'name_cn': d.get('name_cn'),
                 'name_ja': d.get('name_ja'),
                 'aliases': [k for k, v in DEMO_REF_ALIASES.items() if v == name]}
                for name, d in DEMO_REF_DECKS]

    def search_meta_decks(self, query, limit=20, include_hidden=False):
        from core.deck_match import best_score
        hits = []
        for name, d in DEMO_REF_DECKS:
            labels = [name, d.get('name_cn'), d.get('source_name')]
            labels += [k for k, v in DEMO_REF_ALIASES.items() if v == name]
            sc = best_score(query, labels)
            if sc is not None:
                hits.append((sc, {'name': name, 'name_display': None,
                                  'name_cn': d.get('name_cn')}))
        hits.sort(key=lambda x: x[0])
        return [r for _s, r in hits][:limit]


def demo_deck_composition(ref_name=None, per_min: int = 0) -> dict:
    """D3「卡组构成」页 demo：**直接吃真函数** `ui_state.deck_composition`
    （D2/D3 同款防漂移）。

    参照库用本模块的 demo 副本（`reference=` 注入）—— **不依赖** gitignore 的
    `data/mdm_deck_cards.json`（测试机 / 干净克隆上也得画得出来）。
    """
    from core.ui_state import deck_composition
    if not ref_name:
        ref_name = DEMO_REF_DECKS[0][0]
    return deck_composition(_DemoRefStorage(), ref_name, per_min=per_min,
                            reference={'decks': dict(DEMO_REF_DECKS),
                                       'fetched_at': DEMO_REF_FETCHED_AT})
