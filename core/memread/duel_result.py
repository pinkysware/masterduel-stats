# -*- coding: utf-8 -*-
"""**本局对阵/结算信息**（ClientWork，只读）—— 段位与胜负的「不抢时间」来源。

为什么需要它（2026-09-15 深夜实机踩坑）
--------------------------------------
胜负原来的主判据是 `DuelEndMessage` 的 WinIcon 状态位（Unity 对象的两个字节），
但那个**只在结算动画那几秒可读**：实测同一实例 8 秒后图标原生指针就被清成 0
（见 `end_message.py` 模块头）。采集器是 2 秒轮询、且对局中每拍还要做全量落盘，
于是**经常整窗错过** → `winbits=None` → 那几局 `result` 空着（悬浮窗显示 `—`）。

ClientWork 里这两块是**普通字典**，没有对象生命周期问题：

```
s_data["DuelResult"] = {mode, event_id, replayButton:{did,…},
                        resultInfo:{result, expChange:{before,after},
                                    scoreInfo:{score, rewards}, …}}   # 结算画面期间
s_data["Duel"]       = {…, MyID, GameMode, name:[2], pcode:[2], rank:[2], …}   # 对局期间
                        # 结算后它会缩成 {result, pcode:[2], name:[2]}
```

* `DuelResult.resultInfo.result` —— 本局结算码，**与游戏对战记录的 `res` 同口径**
  （`1` = 胜 / `2` = 负；`DuelHistory[i].res` 已与真库逐条对上 3/3）。
  ✅ `2 = 负` 已实机验证（2026-09-16 11:52 骰子拉力败局）：同拍 winbits=(0,1)
  判负 + 文案「LP已削减至0」+ `result=2` 三方一致。`decode_result_code()`
  只认 1/2、其余一律 None（不猜）。
* `Duel.rank` —— **本局双方的段位**（`[{'rank','rate'}, {'rank','rate'}]`，
  下标与 `Duel.name`/`pcode` 对齐；实测 `name[1]`/`pcode[1]` = 我）。
  → 这就是**局前段位**（库里 `my_rank_before` / `opp_rank_before` 的来源，
  以前只有 Untapped API 填过），且**与 `User.profile`（局后值）配对**即可拿到升/掉段。

⚠️ 本模块**只读**（`SafeProcess` 权限位硬编码 0x0410，没有写入口）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core.memread.client_work import find_clientwork
from core.memread.il2cpp import list_fields, static_fields_addr
from core.memread.il2cpp_obj import (MIN_ADDR, boxed_int, class_name_of,
                                     dict_items, list_items, read_string)

#: 结算码 -> 胜负（口径同 `DuelHistory[i].res`：1 胜 / 2 负）
RESULT_WIN, RESULT_LOSE = 1, 2


def decode_result_code(code) -> Optional[str]:
    """结算码 -> ``'Win'`` / ``'Lose'``；认不出给 None（**不猜**）。"""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return None
    if n == RESULT_WIN:
        return 'Win'
    if n == RESULT_LOSE:
        return 'Lose'
    return None


def decode_rank_pair(values) -> Optional[Dict[str, int]]:
    """``{'rank': 5, 'rate': 2}`` -> ``{'rank': 5, 'rate': 2}``；不合法给 None。

    **纯函数**（离线可测）。大段 1..7 / 小段 1..5，与 `core.ranks` 同口径。
    """
    if not values:
        return None
    try:
        rank = int(values['rank'])
        rate = int(values['rate'])
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= rank <= 7) or not (1 <= rate <= 5):
        return None
    return {'rank': rank, 'rate': rate}


def first_rank_pair(items) -> Optional[Dict[str, int]]:
    """``[{'rank':…,'rate':…}, …]`` -> 第一个合法的那一对；没有给 None。"""
    for v in items or []:
        got = decode_rank_pair(v)
        if got:
            return got
    return None


# ---------------------------------------------------------------------------
# 读（需要进程）
# ---------------------------------------------------------------------------


def _clientwork_ti(proc, mirror, tbl, cw_ti):
    return cw_ti or find_clientwork(proc, mirror, tbl)


def _sdata(proc, mirror, ti):
    fl = {f.name: f.offset for f in list_fields(proc, ti, mirror=mirror)}
    if 's_data' not in fl:
        return 0
    return proc.read_ptr(static_fields_addr(proc, ti) + fl['s_data'])


def _scalar(proc, addr):
    """字典取值 -> str / int / bool；认不出给 None。"""
    s = read_string(proc, addr, 80)
    if s is not None:
        return s
    cn = class_name_of(proc, addr)
    try:
        if cn == 'Int64':
            return proc.read_u64(addr + 0x10)
        if cn in ('Int32', 'UInt32'):
            return proc.read_u32(addr + 0x10)
        if cn == 'Boolean':
            # ★ BUG-B3-09：装箱 Boolean 只占 **1 字节**（+0x11 起是填充），
            #   按 4 字节读会把邻字节算进来。范本见 peak_rate._val。
            return bool(proc.read(addr + 0x10, 1)[0])
    except Exception:
        pass
    return boxed_int(proc, addr)


def _sub_dict(proc, mirror, obj, key):
    for k, v in (dict_items(proc, mirror, obj) or []):
        if k == key:
            return v
    return 0


def read_duel_result(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """本局结算（**只在结算画面期间存在**）。

    :return: ``{'result': 1|2, 'result_name': 'Win'|'Lose'|None,
                'mode':…, 'event_id':…, 'did':…,
                'exp_before':…, 'exp_after':…}``；读不到给 None
    """
    try:
        ti = _clientwork_ti(proc, mirror, tbl, cw_ti)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        node = _sub_dict(proc, mirror, sdata, 'DuelResult')
        if not node or node < MIN_ADDR:
            return None
        info = _sub_dict(proc, mirror, node, 'resultInfo')
        out: Dict[str, object] = {'mode': _scalar(proc, _sub_dict(
            proc, mirror, node, 'mode'))}
        for key in ('event_id', 'result'):
            if info and info >= MIN_ADDR:
                out[key] = _scalar(proc, _sub_dict(proc, mirror, info, key))
        if info and info >= MIN_ADDR:
            exp = _sub_dict(proc, mirror, info, 'expChange')
            if exp and exp >= MIN_ADDR:
                for side in ('before', 'after'):
                    sub = _sub_dict(proc, mirror, exp, side)
                    if sub and sub >= MIN_ADDR:
                        out['exp_%s' % side] = _scalar(
                            proc, _sub_dict(proc, mirror, sub, 'exp'))
        rb = _sub_dict(proc, mirror, node, 'replayButton')
        if rb and rb >= MIN_ADDR:
            out['did'] = _scalar(proc, _sub_dict(proc, mirror, rb, 'did'))
        out['result_name'] = decode_result_code(out.get('result'))
        return out
    except Exception:
        return None


def _rank_list(proc, mirror, obj) -> List[Dict[str, int]]:
    """``List<Dictionary>`` -> ``[{'rank','rate'} …]``（读不出的项跳过）。"""
    out: List[Dict[str, int]] = []
    for item in (list_items(proc, mirror, obj) or []):
        if not item or item < MIN_ADDR:
            continue
        pairs = dict(dict_items(proc, mirror, item) or [])
        got = decode_rank_pair({k: _scalar(proc, v) for k, v in pairs.items()})
        if got:
            out.append(got)
    return out


def read_duel_players(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """本局双方（`ClientWork.Duel`）—— **局前段位**在这里。

    :return: ``{'myid':…, 'my_index':…, 'rank': [{rank,rate} …],
                'name': […], 'pcode': […], 'game_mode':…}``；读不到给 None

    ``my_index`` = 我方在下标里的位置（`MyID` 实测 0/1 就是下标；对不上时
    退回用 `pcode` 与 `User.profile.pcode` 比对，比不出来给 None）。
    """
    try:
        ti = _clientwork_ti(proc, mirror, tbl, cw_ti)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        node = _sub_dict(proc, mirror, sdata, 'Duel')
        if not node or node < MIN_ADDR:
            return None
        items = dict(dict_items(proc, mirror, node) or [])
        out: Dict[str, object] = {}
        for key in ('MyID', 'GameMode'):
            if key in items:
                out['game_mode' if key == 'GameMode' else 'myid'] = _scalar(
                    proc, items[key])
        for key in ('name', 'pcode'):
            if key in items:
                vals = []
                for v in (list_items(proc, mirror, items[key]) or []):
                    vals.append(_scalar(proc, v) if v else None)
                out[key] = vals
        if 'rank' in items:
            out['rank'] = _rank_list(proc, mirror, items['rank'])
        # 我方下标：先信 MyID（实测就是下标），再拿 pcode 与 profile 对
        idx = None
        try:
            myid = int(out.get('myid'))
            if 0 <= myid < len(out.get('rank') or []):
                idx = myid
        except (TypeError, ValueError):
            idx = None
        if idx is None:
            prof_pcode = _profile_pcode(proc, mirror, sdata)
            pcodes = out.get('pcode') or []
            if prof_pcode is not None:
                for i, pc in enumerate(pcodes):
                    if pc == prof_pcode:
                        idx = i
                        break
        out['my_index'] = idx
        ranks = out.get('rank') or []
        if idx is not None and idx < len(ranks):
            out['my_rank'] = ranks[idx]
            if len(ranks) == 2:
                out['opp_rank'] = ranks[1 - idx]
        return out
    except Exception:
        return None


def _profile_pcode(proc, mirror, sdata):
    try:
        user = _sub_dict(proc, mirror, sdata, 'User')
        prof = _sub_dict(proc, mirror, user, 'profile')
        for k, v in (dict_items(proc, mirror, prof) or []):
            if k == 'pcode':
                return _scalar(proc, v)
    except Exception:
        pass
    return None
