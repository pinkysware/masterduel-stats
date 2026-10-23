# -*- coding: utf-8 -*-
"""**DC 杯 DP 积分**（Duelist Cup DUEL POINT）—— 只读内存。

## 一句话（2026-10-23 实机打通，2 阶段）

DP 在两个落点，**结算对才是正路**：

```
s_data["DuelResult"]["resultInfo"]["Cup"]["dp"]
    = {before: 0, after: 1000, win: 1}      ← 本局 before/after + 胜负，一次拿全
s_data["DuelMenu"]["Cup"]
    = {stage: 2, dp: 1000, dlv: 20, max_dlv: 20, …}   ← 常驻值（回菜单才刷新）
```

⚠️ **两个落点跟巅峰赛正好相反**（`peak_rate.py`）：那边是常驻值回菜单刷新、
结算对也在结算画面出现；这边实测 **12:44:05 结算对已 after=1000，
`DuelMenu.Cup.dp` 到 12:44:41 才变 1000 —— 常驻值晚了 36 秒**。
所以采集器**必须读结算对**，读常驻值会慢半分钟以上。

⚠️ **`dp` / `dlv` 离开杯赛界面就会被清成 null**（2026-10-23 12:45:07 实测：
回大厅后 `{stage: 2, dp: None, dlv: None, max_dlv: 20}`）。**`stage` 不会清** ——
所以「现在多少分」只能趁在杯赛界面时读；而阶段可以在任何时刻读。

## 那个 `win` 字段是白捡的

`Cup.dp.win`（1 = 胜）与 `resultInfo.result`（1 胜 / 2 负）互相印证 ——
它是**结算节点内部的一致性证据**，可以拿来判「这个结算节点是不是本局的」。
但**不要拿它当胜负判据**：判负是 2、判胜是 1，而全局胜负走 `duel_result`
那套已经验过的口径（`DuelResult.resultInfo.result` + winbits）。

## 为什么不在顶层找

`s_data` 顶层 **57 个键里没有任何 Cup / DP 字样** —— 词命中只有
`CUPDeck` / `CUPDeckList`，那是**卡组容器**（DC 杯的卡组不走 `Deck`，
见 `storage.DECK_SNAPSHOT_TRUSTED_MODES` 那段注释）。积分必须进
`DuelMenu` 再取 `Cup`。当年找巅峰积分也踩过「以为这层没有」的坑。

## 1 阶段 / 2 阶段

* `stage = 1` → 打的是 **DLv**（`dlv` / `max_dlv` / `reward_dlv`），**没有 DP**；
* `stage = 2` → 打的是 **DP**（`dp`），与巅峰积分类似。

**只有 stage=2 的局才该落 `cup_dp_*`** —— 1 阶段的 dlv 是段位不是积分，
混进同一列会让统计把两套数当同一种东西比。`decode_stage()` 只认 1/2。

## 口径

* DP 是**整数**（装箱 Int64），不是字符串 —— 与巅峰积分（`'1507.49'`）不同；
* 值域 `0 <= dp <= 999999`（初定；实测真值 0 / 1000）。**下界含 0** ——
  DP 0 是合法的（2 阶段刚开、还没赢过），不能像巅峰积分那样用 `0 < rate`；
* 本模块**只读**（`SafeProcess` 权限位硬编码 0x0410，没有写入口）；
* 常驻调用方**必须缓存 ClientWork 的 TypeInfo**（首次全内存扫描是秒级的），
  口径同 `rank.py` / `client_work.py` / `peak_rate.py`。
"""
from __future__ import annotations

import struct
from typing import Dict, Optional

from core.memread.client_work import find_clientwork
from core.memread.duel_result import _sdata, _sub_dict
from core.memread.il2cpp_obj import MIN_ADDR, class_name_of, dict_items, read_string

#: DP 值域。**下界含 0**（2 阶段刚开时就是 0）—— 与巅峰积分的 `0 < rate` 不同
MIN_DP, MAX_DP = 0, 999999
#: 合法阶段号
VALID_STAGES = (1, 2)


def decode_dp(v) -> Optional[int]:
    """DP 原始值（装箱整数 / 字符串）-> int；非法/越界给 None。

    **纯函数**（离线可测）。游戏给的是装箱 Int64，但这里也容忍字符串 ——
    上游改类型时不至于整条链路静默失效。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if not (MIN_DP <= n <= MAX_DP):
        return None
    return n


def decode_dp_pair(values) -> Optional[Dict[str, object]]:
    """``{'before':…, 'after':…, 'win':…}`` -> 校验过的 dict。

    `before` / `after` 任一缺失/非法给 None（**不猜**）。
    `win` 可选（只认 0/1），解不出就不带这个键。
    """
    if not values:
        return None
    before = decode_dp(values.get('before'))
    after = decode_dp(values.get('after'))
    if before is None or after is None:
        return None
    out: Dict[str, object] = {'before': before, 'after': after}
    win = values.get('win')
    if win is not None and not isinstance(win, bool):
        try:
            w = int(win)
        except (TypeError, ValueError):
            w = None
        if w in (0, 1):
            out['win'] = w
    return out


def decode_stage(v) -> Optional[int]:
    """阶段号 -> 1 / 2；其它给 None（**不猜**）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n in VALID_STAGES else None


def _val(proc, addr):
    """字典取值 -> str / int / bool；认不出给 None（**不猜**）。

    ⚠️ 与 `peak_rate._val` 同思路，但**多认 Double** —— DP 目前是 Int64，
    加 Double 只是防上游改类型时静默失效。
    """
    if not addr or addr < MIN_ADDR:
        return None
    s = read_string(proc, addr, 80)
    if s is not None:
        return s
    cn = class_name_of(proc, addr)
    try:
        if cn == 'Int64':
            return struct.unpack('<q', proc.read(addr + 0x10, 8))[0]
        if cn in ('Int32', 'UInt32'):
            return struct.unpack('<i', proc.read(addr + 0x10, 4))[0]
        if cn == 'Double':
            return struct.unpack('<d', proc.read(addr + 0x10, 8))[0]
        if cn == 'Boolean':
            return bool(proc.read(addr + 0x10, 1)[0])
    except Exception:
        pass
    return None


def read_cup_result(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读**本局结算**的 DP 对 -> ``{'before','after','win'?}``；读不到给 None。

    链路：``s_data → DuelResult → resultInfo → Cup → dp``。
    只在结算画面之后有（实测窗口 ≥37 秒，存活到回菜单）。

    ⚠️ **不校验 stage** —— 结算节点里没有 stage 字段，阶段由
    :func:`read_cup_stage` 另读（调用方负责只在 stage=2 时落库）。
    :param cw_ti: 已缓存的 ClientWork TypeInfo（**常驻方务必传**）。
    """
    try:
        ti = cw_ti or find_clientwork(proc, mirror, tbl)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        node = _sub_dict(proc, mirror, sdata, 'DuelResult')
        if not node or node < MIN_ADDR:
            return None
        info = _sub_dict(proc, mirror, node, 'resultInfo')
        if not info or info < MIN_ADDR:
            return None
        cup = _sub_dict(proc, mirror, info, 'Cup')
        if not cup or cup < MIN_ADDR:
            return None
        dp = _sub_dict(proc, mirror, cup, 'dp')
        if not dp or dp < MIN_ADDR:
            return None
        items = dict(dict_items(proc, mirror, dp) or [])
        return decode_dp_pair({k: _val(proc, v) for k, v in items.items()})
    except Exception:
        return None


def read_cup_stage(proc, mirror, tbl, cw_ti=None) -> Optional[int]:
    """读当前 **DC 杯阶段**（``DuelMenu.Cup.stage``）-> 1 / 2；读不到给 None。

    常驻值（主界面/匹配界面就有），与 :func:`read_cup_score` 同源。
    """
    try:
        ti = cw_ti or find_clientwork(proc, mirror, tbl)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        dm = _sub_dict(proc, mirror, sdata, 'DuelMenu')
        if not dm or dm < MIN_ADDR:
            return None
        cup = _sub_dict(proc, mirror, dm, 'Cup')
        if not cup or cup < MIN_ADDR:
            return None
        return decode_stage(_val(proc, _sub_dict(proc, mirror, cup, 'stage')))
    except Exception:
        return None


def read_cup_score(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读**常驻** DC 杯状态 -> ``{'stage','dp','dlv','max_dlv'}``；读不到给 None。

    ⚠️ **回菜单才刷新**（实测比结算对晚 36 秒）。逐局 before/after 请用
    :func:`read_cup_result`；这个只适合「现在多少分」这种展示用途。
    """
    try:
        ti = cw_ti or find_clientwork(proc, mirror, tbl)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        dm = _sub_dict(proc, mirror, sdata, 'DuelMenu')
        if not dm or dm < MIN_ADDR:
            return None
        cup = _sub_dict(proc, mirror, dm, 'Cup')
        if not cup or cup < MIN_ADDR:
            return None
        items = dict(dict_items(proc, mirror, cup) or [])
        out: Dict[str, object] = {}
        stage = decode_stage(_val(proc, items.get('stage')))
        if stage is not None:
            out['stage'] = stage
        for key in ('dp', 'dlv', 'max_dlv'):
            if key in items:
                n = decode_dp(_val(proc, items[key]))
                if n is not None:
                    out[key] = n
        return out or None
    except Exception:
        return None
