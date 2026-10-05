# -*- coding: utf-8 -*-
"""**巅峰决斗积分**（Rate Duel RATE，界面 `RATE: 1507.49`）—— 只读内存。

## 一句话（2026-09-28 实机打通）

积分以**字符串**存在 `ClientWork.s_data` 里，两个落点：

```
s_data["DuelResult"]["resultInfo"]["RateDuel"]["rate"]
    = {old_rate: '1507.49', new_rate: '1515.62'}   ← 本局 before/after 一次拿全
s_data["RateDuel"]["<赛季号>"]["info"]
    = {rate: '1507.49', rate_distribution: '50',    ← 常驻值（回菜单才刷新）
       con_win: 1, attend_status: True}
```

* 结算对**结算画面就出现**、存活到**下一局开局**（`tmp/_probe_peak_rate.py`
  watch 实测：13:01:17 出现，常驻 `info.rate` 那时还是旧值）→ 采集器收局后
  从容读，不用赛跑；`old_rate` 就是局前值，**不需要**「下一局倒推」备选。
* 赛季号 = `DuelResult.event_id`（= 界面 `Season 57`）。

## ⚠️ 别再走的路（当年实测的否定结论）

* `DuelMenu.RateDuel` 只有赛季档期、没积分 —— 旧文档排除的是它；
  **顶层 `RateDuel` 是另一棵子树**（当年探针没翻到这一层，误判成「这里没有」）；
* `MyRateInfo`（⚠️ **空 namespace**，按 `YgomGame` 找不到；字段 `myRate` /
  `myDistribution` 是 **STRING**、`attendStatus` 是 BOOLEAN，FieldInfo→Il2CppType
  链实测）是服务端 DTO，扫不到实例 —— 值从 ClientWork 拿就够；
* `DuelHistory.*.player[n].rate` 是**天梯小段**（1..5），不是巅峰积分；
* 按值扫不能当定位器（`docs/段位内存读取方案.md` §5.6）。

## 口径

* `before` / `after` = 局前 / 局后积分，入库 `peak_rate_before` / `peak_rate_after`
  （REAL，**写库前 round 2 位**）；`peak_season` = 赛季号（积分跨赛季不可比）；
* 值域校验 `0 < rate < 30000`（初定，界值真值 1507.49 远在中间）；
* 载荷里**只有我方一份**积分，没有对手数据 —— 不存在「顺手读了对手」的问题；
* 本模块**只读**（`SafeProcess` 权限位硬编码 0x0410，没有写入口）；
* 常驻调用方**必须缓存 ClientWork 的 TypeInfo**（首次全内存扫描是秒级的），
  口径同 `rank.py` / `client_work.py`。
"""
from __future__ import annotations

import struct
from typing import Dict, Optional

from core.memread.client_work import find_clientwork
from core.memread.duel_result import _sdata, _sub_dict
from core.memread.il2cpp_obj import MIN_ADDR, class_name_of, dict_items, read_string

#: 合理积分值域（decode 用；探针真值 1507.49 / 1515.62 远在中间）
MIN_RATE, MAX_RATE = 0.0, 30000.0
MIN_SEASON, MAX_SEASON = 1, 999


def decode_peak_value(v) -> Optional[float]:
    """积分原始值（字符串/数字）-> float（**round 2 位**）；非法/越界给 None。

    **纯函数**（离线可测）。游戏给的是 `'1507.49'` 这样的字符串 ——
    字符串转 float 没有二进制长尾问题，round 只是防一手上游改数值类型。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not (MIN_RATE < f < MAX_RATE):
        return None
    return round(f, 2)


def decode_peak_pair(values) -> Optional[Dict[str, float]]:
    """``{'old_rate':…, 'new_rate':…}`` -> ``{'before':…, 'after':…}``。

    任一缺失/非法给 None —— **不猜**（宁可让调用方当「本局没读到」）。
    """
    if not values:
        return None
    before = decode_peak_value(values.get('old_rate'))
    after = decode_peak_value(values.get('new_rate'))
    if before is None or after is None:
        return None
    return {'before': before, 'after': after}


def decode_season(v) -> Optional[int]:
    """赛季号（如 57）-> int；非法给 None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if not (MIN_SEASON <= n <= MAX_SEASON):
        return None
    return n


def _val(proc, addr):
    """字典取值 -> str / int / bool；认不出给 None（**不猜**）。

    ⚠️ 字符串优先、其余按类名读 —— 与 `duel_result._scalar` 同思路，但
    不用它（那边走 `read_u32/read_u64`，假内存测试桩没有这几个方法）。
    """
    s = read_string(proc, addr, 80)
    if s is not None:
        return s
    cn = class_name_of(proc, addr)
    try:
        if cn == 'Int64':
            return struct.unpack('<q', proc.read(addr + 0x10, 8))[0]
        if cn in ('Int32', 'UInt32'):
            return struct.unpack('<i', proc.read(addr + 0x10, 4))[0]
        if cn == 'Boolean':
            return bool(proc.read(addr + 0x10, 1)[0])
    except Exception:
        pass
    return None


def read_peak_result(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读**本局结算**的积分对 -> ``{'before','after','season'}``；读不到给 None。

    链路：``s_data → DuelResult → resultInfo → RateDuel → rate``；
    赛季取 ``DuelResult.event_id``。只在结算画面之后有（存活到下一局开局）。
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
        rd = _sub_dict(proc, mirror, info, 'RateDuel') if info and info >= MIN_ADDR else 0
        rate = _sub_dict(proc, mirror, rd, 'rate') if rd and rd >= MIN_ADDR else 0
        if not rate or rate < MIN_ADDR:
            return None
        items = dict(dict_items(proc, mirror, rate) or [])
        pair = decode_peak_pair({k: _val(proc, v) for k, v in items.items()})
        if not pair:
            return None
        season = decode_season(_val(proc, _sub_dict(proc, mirror, node, 'event_id')))
        if season is not None:
            pair['season'] = season
        return pair
    except Exception:
        return None


def read_peak_standing(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读**常驻**巅峰信息 -> ``{'season','rate','distribution','con_win'}``。

    链路：``s_data → RateDuel → <数字最大的赛季键> → info``。
    ⚠️ 回菜单才刷新 —— 结算画面上它是**旧值**，逐局 before/after 请用
    :func:`read_peak_result`。读不到给 None。
    """
    try:
        ti = cw_ti or find_clientwork(proc, mirror, tbl)
        if not ti:
            return None
        sdata = _sdata(proc, mirror, ti)
        node = _sub_dict(proc, mirror, sdata, 'RateDuel')
        if not node or node < MIN_ADDR:
            return None
        best_key, best_node = None, 0
        for k, v in (dict_items(proc, mirror, node) or []):
            if not k or not k.isdigit():
                continue
            sn = int(k)
            if best_key is None or sn > best_key:
                best_key, best_node = sn, v
        if not best_node or best_node < MIN_ADDR:
            return None
        info = _sub_dict(proc, mirror, best_node, 'info')
        if not info or info < MIN_ADDR:
            return None
        items = dict(dict_items(proc, mirror, info) or [])
        rate = decode_peak_value(_val(proc, items.get('rate')))
        if rate is None:
            return None
        out: Dict[str, object] = {'season': best_key, 'rate': rate}
        dist = _val(proc, items.get('rate_distribution'))
        try:
            out['distribution'] = int(dist)
        except (TypeError, ValueError):
            pass
        cw = _val(proc, items.get('con_win'))
        try:
            out['con_win'] = int(cw)
        except (TypeError, ValueError):
            pass
        return out
    except Exception:
        return None
