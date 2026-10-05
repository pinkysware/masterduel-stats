# -*- coding: utf-8 -*-
"""**自己账号的段位** —— 只读内存（2026-09-15 第三轮：**锚点找到，通了**）。

## 一句话

段位 = ``ClientWork.s_data["User"]["profile"]`` 里的 ``rank`` / ``rate``
（大段 1..7、小段 1..5），**主界面就能读**，走确定性指针链，**不靠扫**。

## 这条链是怎么找到的（2026-09-15 深夜，游戏 pid 15996，主界面）

1. **「按值扫」被否掉（连带上一轮那个命中）**。上一轮在 `0x25E008F653C`
   读到 `[5, 2, 1]`（看着像 `[大段, 小段, 连胜]`）。本轮**同进程同地址复读**
   = `[5, 1600, 1]` —— `1600` 超出小段值域，`decode_record()` 自己就判它不是那条记录。
   另外：以该地址往回 0x400 找不到对象头、全堆 `u64 == 该地址` **命中 0 处**
   → 它也不在任何 IL2CPP 对象的字段里。→ **那个命中是巧合，按值域扫这条路彻底否掉。**

2. **改走 ClientWork 锚点**。段位是「服务端下发 + 界面展示」的数据 ——
   本项目早有一条走过的同款路（`client_work.py` 的卡组快照）。把 s_data 的
   62 个键全打出来，`User` 下面直接挂着 `profile`：

   ```
   s_data["User"]["profile"] = Dictionary<string, object>（16 项）
       name = '示例玩家'  rank = 5  rate = 2  level = 36  pcode = 123456789
       exp / need_exp / icon_id / icon_frame_id / avatar_id / wallpaper / …
   ```

3. **交叉校验（三条都对上）**：

   * `rank=5` → `core.ranks.RANK_NAMES[5]` = **Platinum**、`rate=2` → `II`
     = 用户界面上的 **白金2**（`docs/段位内存读取方案.md` §5.4 的真值）；
   * 真库的段位链：2026-09-06 18:35 那局 `5-3 → 5-2` 升段之后一直是 `5-2`，
     `profile` 读出来正是 **`5-2`**（= 最后一局的**局后值**）；
   * `name = '示例玩家'` = 用户本人游戏名（与日志 / `profileDataMyself` 同源）。

## 口径

* `rank`（大段）= `core.ranks` 的 1..7，**5 = Platinum**；
* `rate`（小段）= 1..5，**数字越小段位越高**（5 最低 / 1 最高）——
  与库里的 `my_rank_before` / `my_rate_before` **同一套编号**，可直接入库；
* 读到的是「**当前**段位」= 最后一局的**局后值**（将来 `my_rank_after` 的来源）；
  局前值沿用项目既有口径（= **上一局的局后值**）。

## ⚠️ 已知的坑（别再走一遍）

* **`DuelHistory` 里的段位是快照，不是逐局真值**：
  `DuelHistory[赛季][序号].player[i].rank/rate` 读得到，但实测 19 条历史里
  我方**全是 `5-2`**，而其中 2026-09-06 18:29 那局库里明确记着 `5-3`
  → 那是**抓取时的当前值**被填进了每条历史。**别拿它补 before/after。**
* `MyRateInfo` / `RankData` / `TierData`（§5.1-5.2 定位过的 DTO 类）**扫不到实例**
  （它们是服务端 DTO，没有对象头），**也不用再扫** —— 值从 ClientWork 拿就够。
* `read_profile()` 首次调用要做一次**全内存 TypeInfo 扫描**（秒级）
  → 常驻方**必须缓存 TypeInfo**（口径同 `client_work.snapshot_my_deck` 的 `cw_ti`）。
* 本模块**只读**（`SafeProcess` 权限位硬编码 0x0410，没有写入口）。

## 还没验的一件事（下一步）

**`profile` 什么时候被刷新** —— 是「结算那一刻」就更新，还是「回主界面 / 下次同步」才更新。
这决定采集器能不能在 `pvpFinished 0→1` 当场读到的就是局后值。
验法：`tools/capture_my_rank.py --watch` 挂着，打一局，看数值翻转的时刻。
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from core.memread.client_work import find_clientwork
from core.memread.il2cpp import list_fields, static_fields_addr
from core.memread.il2cpp_obj import (MIN_ADDR, boxed_int, class_name_of,
                                     dict_items, read_string)

#: 大段范围（`core.ranks` 编号：1 Rookie … 5 Platinum … 7 Master）
MIN_RANK, MAX_RANK = 1, 7
#: 小段范围（I~V；数字越小段位越高）
MIN_TIER, MAX_TIER = 1, 5

#: `User.profile` 在 s_data 里的路径
PROFILE_PATH = ('User', 'profile')
#: 要从 profile 里取的键（其余键是不相关的装扮字段）
#: ⚠️ `exp` / `need_exp` 是**每局都会变的哨兵** —— 用它们才能测出
#: 「ClientWork 在哪一刻被刷新」（`rate` 只在升/掉段时变，太稀疏）。
PROFILE_KEYS = ('name', 'rank', 'rate', 'level', 'pcode', 'exp', 'need_exp')

# ---------------------------------------------------------------------------
# 现役实现（走 ClientWork 锚点）
# ---------------------------------------------------------------------------


def decode_profile(values) -> Optional[Dict[str, object]]:
    """profile 的原始值（已解成 Python 标量）-> 段位 dict；不合法给 None。

    **纯函数**（不碰进程），所以能离线单测。
    判据 = `rank` 落在 1..7 **且** `rate` 落在 1..5（与 `core.ranks` 同一套编号）。
    任一项缺失/越界就返回 None —— **不猜**（宁可让调用方退回旧数据源）。
    """
    if not values:
        return None
    try:
        rank = int(values['rank'])
        rate = int(values['rate'])
    except (KeyError, TypeError, ValueError):
        return None
    if not (MIN_RANK <= rank <= MAX_RANK):
        return None
    if not (MIN_TIER <= rate <= MAX_TIER):
        return None
    out: Dict[str, object] = {'rank': rank, 'rate': rate}
    for key in ('name', 'level', 'pcode', 'exp', 'need_exp'):
        if values.get(key) is not None:
            out[key] = values[key]
    return out


def _scalar(proc, mirror, addr):
    """profile 的值 -> str / int；认不出给 None（**不猜**）。"""
    s = read_string(proc, addr, 80)
    if s is not None:
        return s
    cn = class_name_of(proc, addr)
    try:
        if cn == 'Int64':
            return proc.read_u64(addr + 0x10)
        if cn in ('Int32', 'UInt32'):
            return proc.read_u32(addr + 0x10)
    except Exception:
        pass
    return boxed_int(proc, addr)


def read_profile(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读 ``ClientWork.s_data["User"]["profile"]`` -> 原始值 dict；读不到给 None。

    :param cw_ti: 已缓存的 ClientWork TypeInfo（**常驻方务必传** ——
        不传就要做一次全内存扫描，秒级）
    """
    try:
        ti = cw_ti or find_clientwork(proc, mirror, tbl)
        if not ti:
            return None
        fl = {f.name: f.offset for f in list_fields(proc, ti, mirror=mirror)}
        if 's_data' not in fl:
            return None
        node = proc.read_ptr(static_fields_addr(proc, ti) + fl['s_data'])
        for key in PROFILE_PATH:
            if not node or node < MIN_ADDR:
                return None
            hit = [v for k, v in (dict_items(proc, mirror, node) or []) if k == key]
            if not hit:
                return None
            node = hit[0]
        if not node or node < MIN_ADDR:
            return None
        values: Dict[str, object] = {}
        for k, v in (dict_items(proc, mirror, node) or []):
            if k in PROFILE_KEYS:
                values[k] = _scalar(proc, mirror, v)
        return values or None
    except Exception:
        return None


def read_my_rank(proc, mirror, tbl, cw_ti=None) -> Optional[Dict[str, object]]:
    """读当前段位 -> ``{'rank': 5, 'rate': 2, 'name': …, 'level': …, 'pcode': …}``。

    读不到 / 值不合法给 None（调用方按「本局没有段位」处理，别猜）。
    ``rank`` / ``rate`` 与库里的 ``my_rank_before`` / ``my_rate_before`` 同口径。
    """
    return decode_profile(read_profile(proc, mirror, tbl, cw_ti))


# ---------------------------------------------------------------------------
# 考古区：上一轮「按值域扫那条记录」的遗留（**已否掉，别接线**）
#
# 2026-09-15 深夜复核：那条「记录」是巧合 —— 同地址复读成 `[5, 1600, 1]`，
# `decode_record()` 自己就否了。留着是为了让下一个人看到「这条路试过了、
# 为什么不行」，不是生产代码。**没有任何生产代码调用它们。**
# ---------------------------------------------------------------------------

#: 那条「记录」后面跟着多少字节的 0（当年观察到的形态）
TRAILING_ZEROS = 0x10


def decode_record(raw: bytes) -> Optional[Tuple[int, int, int]]:
    """**已弃用**：一段内存 -> ``(大段, 小段, 连胜)``；不像就给 None。

    当年（2026-09-15 第二轮）以为实机那条 `[5,2,1]` 是 `[大段,小段,连胜]`，
    但它扛不住复核（同地址复读成 `[5,1600,1]`）→ 这条判据**只是猜中了巧合**。
    纯函数，10 项离线单测仍在（`tests/test_memread_rank.py`），**仅作考古**。
    """
    if len(raw) < 12 + TRAILING_ZEROS:
        return None
    rank, tier, streak = struct.unpack_from('<3i', raw, 0)
    if not (MIN_RANK <= rank <= MAX_RANK):
        return None
    if not (MIN_TIER <= tier <= MAX_TIER):
        return None
    if not (0 <= streak <= 99):
        return None
    if any(raw[12:12 + TRAILING_ZEROS]):
        return None                          # 后面不是一片 0 → 不是这条记录
    return rank, tier, streak


def find_candidates(proc, regions=None) -> List[int]:
    """**已弃用、禁止接线**：按值域扫出所有「像那条记录」的地址（只读）。

    实测在真进程里撞出 **2 万个**候选（`[1,2,0]` + 尾巴一片 0 满地都是），
    取到的第一个是错的 —— **按值扫不能当定位器**。
    段位请用 :func:`read_my_rank`（走 ClientWork 锚点）。
    """
    out: List[int] = []
    if regions is None:
        regions = [r for r in proc.regions()
                   if getattr(r, 'protect', 0) & 0x04
                   and getattr(r, 'type', 0) == 0x20000]      # 私有可读堆
    for region in regions:
        raw = proc.try_read(region.base, region.size)
        if not raw:
            continue
        need = 12 + TRAILING_ZEROS
        for off in range(0, len(raw) - need, 4):               # int32 对齐扫
            if raw[off] not in (1, 2, 3, 4, 5, 6, 7):          # 大段必须 1..7
                continue
            if decode_record(raw[off:off + need]) is not None:
                out.append(region.base + off)
    # 簇合并：同一条记录会被「错位 4 字节」的窗口重复匹配，只留最早的起点
    merged: List[int] = []
    for addr in sorted(out):
        if merged and addr - merged[-1] < 12 + TRAILING_ZEROS:
            continue
        merged.append(addr)
    return merged
