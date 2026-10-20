# -*- coding: utf-8 -*-
"""天梯段位状态推断链 v2（2026-09-18 用户口述规则，逐条确认过）。

规则（用户原话 + 追问确认）
--------------------------
* Rookie 只有 Tier 1-2；Bronze~Master 各 Tier 1-5；**Tier 越高数字越小**。
* 升一小段所需连胜：**黄金及以下 3、白金/钻石 4、大师 5**（**与掉段线无关**）。
* 低段（黄金及以下）：**输掉不减「还剩几胜」、也不会掉段**。
* **白金/钻石/大师：连输 3 把掉一小段**（用户 2026-09-20 实测修正：钻石 4 连输 2 把后
  第 3 把提示「输了就掉段」，实际掉段 → 连输 **3** 把；大师同）。
  ★ 2026-10-19 更正：**白金也是 3**。此处原写「白金仍是连输 5 把（只改钻石大师）」
  是 09-20 那轮的旧口径，已作废 —— 用户原话：「白金段是在还有 4 胜到下一段的
  状态时连输 3 把掉段。」
* **跨大段不会掉**（大师五不管输几把都不会掉到钻石一）——最低小段（Tier 5）
  触发掉段线时保持原位。
* 升段/掉段后「还剩几胜」重新从**满额**算（白金/钻石 4、大师 5）。

推断 vs 探测
------------
胜负收局即知 → `apply_result` 立即推断（段位同帧显示）；
探测（30s 窗口）读到的 rank/rate 是**真值** → 复核：与推断不符以真值覆盖。

状态文件：``data/rank_state.json``（本机数据，不入库）。
"""
from __future__ import annotations

import io
import json
import os
import time
from core import paths

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.environ.get('MD_RANK_STATE') or paths.data_path('rank_state.json')

RANK_MASTER = 7
RANK_PLATINUM = 5
RATE_MIN, RATE_MAX = 1, 5


def streak_needed(rank) -> int:
    """升一小段需要的连胜数：低段 3、白金/钻石 4、大师 5。"""
    r = rank if isinstance(rank, int) else 0
    if r >= RANK_MASTER:
        return 5
    if r >= RANK_PLATINUM:
        return 4
    return 3


#: 段位编号：钻石 = 6（`core.ranks.RANK_NAMES`）
RANK_DIAMOND = 6

#: **掉段线（白金 / 钻石 / 大师统一）= 连输 3 把**。
#: 2026-09-20 用户实测：「钻石4连输2把，第三把提示我输了就掉段」；大师同理。
#: ★ 2026-10-19 用户更正：**白金也是 3**（旧值 5 已作废）—— 原话「白金段是在
#:   还有 4 胜到下一段的状态时连输 3 把掉段」。
#: （曾另有一个 `DEMOTE_LINE_PLATINUM = 3`，与本案同值；2026-10-19 合并成一个。）
DEMOTE_LINE_HIGH = 3


def demote_line(rank, rate=None):
    """连输多少把会掉一小段：**白金/钻石/大师都是 3**；低段（黄金及以下）不掉 → None。

    ⚠️ 与 `streak_needed`（升段所需连胜）**不是同一个数**：白金/钻石升段要 4 连胜、
    掉段只要 3 连败（用户 2026-09-20 实测确认，别再"对齐"成同一个值）。

    ★ 2026-10-19 更正：白金掉段线**也是 3**（原来是 5，口径作废）。
      用户原话：「白金段是在还有 4 胜到下一段的状态时连输 3 把掉段。」

    ★ 09-28 用户确认（v3.1）：**大师段会掉段，但大师 I（rate 1，段内顶）与
    大师 V（rate 5，最低小段，跨大段保护）不掉** —— 大师 II/III/IV 连输 3 掉一段。
    """
    r = rank if isinstance(rank, int) else 0
    if r >= RANK_MASTER:
        if rate is not None and (int(rate) <= RATE_MIN or int(rate) >= RATE_MAX):
            return None
        return DEMOTE_LINE_HIGH
    if r >= RANK_DIAMOND:
        return DEMOTE_LINE_HIGH
    if r >= RANK_PLATINUM:
        return DEMOTE_LINE_HIGH
    return None


def load(path=None):
    """读状态；文件不在/坏了 -> ``{}``。"""
    p = path or STATE_PATH
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                            # noqa: BLE001
        return {}


def _audit_save(new_state):
    # 默认状态文件的写入审计：rank/rate/streak 变化才记（带调用栈）。
    # 09-27 实锤：rank_state 被 7-3 无日志改成 7-5，推断链整条跟着错。
    old_st = load()
    keys = ('rank', 'rate', 'win_streak', 'lose_streak')
    old_v = {k: old_st.get(k) for k in keys}
    new_v = {k: new_state.get(k) for k in keys}
    if old_v == new_v:
        return
    import traceback
    # ★ BUG-B2A-05（2026-10-18）：改走 `paths.data_path('archive', ...)`。
    #   原来硬拼 `PROJ/data/archive/` —— 安装版（Program Files）那里不可写，
    #   `open` 抛 OSError 被调用处的 `except: pass` 吞掉，审计日志一条都不写；
    #   而它正是 09-27 / 10-05 两次「段位被无日志改掉」事故的取证手段。
    log = paths.data_path('archive', 'rank_state_audit.log')
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with io.open(log, 'a', encoding='utf-8', newline='') as f:
        f.write('%s  %s -> %s  (src=%s)\n' % (stamp, old_v, new_v,
                                               new_state.get('src')))
        f.write('  stack: %s\n' % ' | '.join(
            x.strip() for x in traceback.format_stack(limit=7)[:-1]))


def save(state, path=None):
    if path is None:                     # 写审计：09-27 段位漂移事故，再发作就有据可查
        try:
            _audit_save(state)
        except Exception:
            pass
    """写状态（覆盖）。"""
    p = path or STATE_PATH
    state = dict(state or {})
    state['at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except Exception:                                            # noqa: BLEINGER
        pass
    # ★ 写入追踪（2026-10-05：state 被无日志改写 6-3→6-5，写手不明）——
    #   每次落盘追加一行「时间 | 谁 | 值」，抓到写手后再撤。
    try:
        import traceback
        who = ' <- '.join('%s:%s %s' % (t.filename.replace('\\', '/').split('/')[-1],
                                        t.lineno, t.name)
                          for t in reversed(traceback.extract_stack()[-5:-1]))
        with open(p.rsplit('.', 1)[0] + '_writes.log', 'a',
                  encoding='utf-8', newline='') as f:
            f.write('%s | %s | %s-%s streak w%s/l%s\n'
                    % (state.get('at'), who, state.get('rank'),
                       state.get('rate'), state.get('win_streak'),
                       state.get('lose_streak')))
    except Exception:                                            # noqa: BLEINGER
        pass
    return state


def init_from(rank, rate, path=None):
    """用探测读到的真值初始化/校准（连胜/连败计数清零重新起算）。"""
    return save({'rank': rank, 'rate': rate,
                 'win_streak': 0, 'lose_streak': 0, 'src': 'probe'}, path)


def next_state(state, result):
    """推一局（**纯函数，不落盘**）→ ``(新状态 dict, 是否升段)``。

    给「不想立刻 save」的调用方用（如界面的手填链：先写库成功再落状态文件）。

    规则（用户 2026-09-18 口述 v2）：
    * 赢：连胜 +1（连败清零）；攒满 → 升一小段（跨大段允许升）；
    * 输：**先扣升段进度**（win_streak-1，不为负）；进度扣光（还有满额 N 胜）后
  连输 **3** 把才掉一段（前 2 把是保护局；**不在最低小段**——跨大段保护）。
  ★ 09-27 用户口供确认 v3（旧「连败计数≥3 掉段」只在进度 0 时与新规则重合）
      → 掉一小段（低段不掉）；
    * rank/rate 未知时不编。
    """
    st = dict(state or {})
    rank, rate = st.get('rank'), st.get('rate')
    if rank is None or rate is None:
        return st, False
    win_streak = int(st.get('win_streak') or 0)
    lose_streak = int(st.get('lose_streak') or 0)
    promoted = False
    if result == 'Win':
        win_streak += 1
        lose_streak = 0
        if win_streak >= streak_needed(rank):
            promoted = True
            win_streak = 0
            if rate - 1 >= RATE_MIN:
                rate = rate - 1
            elif rank + 1 <= RANK_MASTER:
                rank, rate = rank + 1, RATE_MAX
            # Master 1（rank 7 rate 1）已是大段顶：只清计数，往上没有段位可升
            # （游戏里 Master 之上用 DP 积分排位，超出本工具的 rank/rate 模型）
    elif result in ('Lose', 'Draw'):
        # ★ v3 进度制（09-27 用户口供确认）：输局先扣进度（win_streak-1）；
        #   进度扣光后，连输 3 把才掉段 —— lose_streak 从「连败计数」改为
        #   「进度 0 期间的保护输局计数」（赢局即清零）。
        if win_streak > 0:
            win_streak -= 1
            lose_streak = 0
        else:
            lose_streak += 1
            line = demote_line(rank, rate)
            # ★ BUG-B2A-02（2026-10-18）：原来硬编码 `>= 3`，而 `demote_line()`
            #   白金返回 **5**、钻石/大师返回 3、黄金以下返回 None。
            #   结果白金连输 3 把就掉段（用户口径是 5 把），
            #   且因为钻石/大师恰好也是 3，这个错从未被发现。
            if line is not None and lose_streak >= line:
                lose_streak = 0
                if rate + 1 <= RATE_MAX:   # 跨大段保护：最低小段（Tier 5）不掉
                    rate = rate + 1
    st.update({'rank': rank, 'rate': rate,
               'win_streak': win_streak, 'lose_streak': lose_streak})
    return st, promoted


def apply_result(state, result, path=None):
    """一局结束（result 实时已知）→ 推断**并落盘**。返回 (新状态, 是否升段)。"""
    st, promoted = next_state(state, result)
    return save(st, path), promoted


def wins_to_promote(state) -> int:
    """还差几胜升一小段（连胜数未知给 -1，界面别硬编）。"""
    st = state or {}
    rank, n = st.get('rank'), st.get('win_streak')
    if rank is None or n is None:
        return -1
    return max(0, streak_needed(rank) - int(n))
