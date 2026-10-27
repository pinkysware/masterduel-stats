# -*- coding: utf-8 -*-
"""悬浮窗「刷新」按钮的一步式内存读取（2026-10-25，仿 present_box_live）。

之前的行为只是**重读 `my_deck.json` 状态文件**（`refresh_deck_banner`）——
文件里是什么就显示什么，用户怎么按都刷不出新识别（实报「刷不出我的天梯
卡组」）。本模块真读一次内存，按**具体度**依次试三个槽位：

1. ``CUPDeckList`` —— DC 杯（仅 DC 杯界面/对局期间有值）；
2. ``Deck.ratedeck_id`` —— 巅峰（常驻）；
3. ``Deck`` 常规容器 —— 天梯（大厅常驻）。

第一个有值的胜出；名字用容器自带（Deck.list 的真名），DC 杯没有名字时走
原型识别（``identify_by_cards``）。读到即写回 ``my_deck.json``
（``src='m5-guess'``，自动识别语义），悬浮窗随后照常渲染。

⚠️ **性能：定位成本远大于读取成本**（2026-10-28 实测，用户报「点刷新卡 10 秒」）
--------------------------------------------------------------------------
::

    find_pids          0.74s
    find_game_metadata 3.75s   <- 全进程扫 17.7MB metadata 镜像
    find_clientwork    6.11s   <- **全内存扫类名**
    真正读卡组         0.01s   <- 占 0.1%

所以本模块**缓存「路」而不是「值」**：
``(pid, mirror_base, cw_ti)`` 记在模块级 ``_CACHE`` 里，同一游戏进程只扫一次。
缓存失效条件（任一命中就重扫）：
* ``pid`` 变了（游戏重启）；
* ``mirror_base`` 读不到了 / 变了（游戏重新加载 metadata）；
* ``cw_ti`` 的 ``Il2CppClass.name`` 回读不到（TypeInfo 失效，游戏大更新后常见）。

实测收益：第二次起 **10.6s -> 0.02s**。

只读内存（``SafeProcess`` 0x410，无写入口）；UI 点按钮才调。
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from core import my_deck
from core.memread import client_work as cw
from core.memread.present_box import (find_game_metadata,
                                      game_metadata_missing_reason)
from core.memread.process import SafeProcess, find_pids_by_name

#: 错误文案（给用户看的中文，与 present_box_live 同风格）
ERR_NO_GAME = '没有检测到游戏进程 —— 请先启动 Master Duel'
ERR_NO_META = '没读到游戏数据 —— 请进一次游戏主界面再刷新'
ERR_NOTHING = '没读到卡组 —— 请进一次游戏主界面（或卡组编辑界面）再刷新'

#: 缓存多久算过期（秒）。0 = 只靠有效性校验（pid/mirror/TypeInfo 回读）。
#: 给个上限是为了「缓存看着有效、其实游戏换了套内存」这种极端情况兜底。
CACHE_TTL_SEC = 300.0

#: 模块级缓存：``{'pid','mirror_base','mirror','stbl','cw_ti','at'}``
_CACHE = {}


def clear_cache() -> None:
    """清缓存（单测 / 强制重连时用）。"""
    _CACHE.clear()


def _metadata_alive(proc, mirror_base: int) -> bool:
    """镜像还在不在（读它头 4 字节 —— 读得到就算在）。"""
    if not mirror_base:
        return False
    try:
        return bool(proc.read(mirror_base, 4))
    except Exception:                                            # noqa: BLE001
        return False


def _typeinfo_alive(proc, ti: int) -> bool:
    """TypeInfo 还有效吗（``Il2CppClass.name`` 的 char* 能读出非空串）。"""
    if not ti:
        return False
    try:
        name_ptr = proc.read_ptr(ti + 0x10)          # TYPEINFO_NAME_OFFSET
        if not name_ptr:
            return False
        raw = proc.read(name_ptr, 4)
        return bool(raw) and raw[0] != 0
    except Exception:                                            # noqa: BLE001
        return False


def _locate(proc, pid: int):
    """返回 ``(mirror, stbl, cw_ti)``；定位失败给 ``(None, None, 0)``。

    先看缓存，失效才真扫（3~10 秒）。
    """
    now = time.time()
    cached = _CACHE
    fresh = (cached.get('pid') == pid
             and now - cached.get('at', 0) < CACHE_TTL_SEC
             and cached.get('mirror') is not None
             and cached.get('stbl') is not None)
    if fresh and _metadata_alive(proc, cached.get('mirror_base')):
        if _typeinfo_alive(proc, cached.get('cw_ti')):
            return cached['mirror'], cached['stbl'], cached['cw_ti']
        # 镜像还在、TypeInfo 失效 -> 只重扫类名（省掉镜像那 3.75s）
        try:
            ti = cw.find_clientwork(proc, cached['mirror'], cached['stbl'])
        except Exception:                                        # noqa: BLE001
            ti = 0
        if ti:
            cached['cw_ti'] = ti
            cached['at'] = now
            return cached['mirror'], cached['stbl'], ti

    # 全量重扫
    found = find_game_metadata(proc)
    if not found:
        return None, None, 0
    _header, mirror, stbl = found
    try:
        ti = cw.find_clientwork(proc, mirror, stbl)
    except Exception:                                            # noqa: BLE001
        ti = 0
    if not ti:
        return mirror, stbl, 0
    _CACHE.update({'pid': pid, 'mirror': mirror, 'stbl': stbl,
                   'mirror_base': getattr(mirror, 'base', 0),
                   'cw_ti': ti, 'at': now})
    return mirror, stbl, ti


#: 卡表反推的最低命中数（与 `deck_guess.MIN_OWN_HITS` 同口径 —— 那边是
#: `suggest_own_deck` 的闸门；这里单独写一份是因为要拿**置信度**，而
#: `identify_by_cards` 只回 top1 名字）。
def identify_own_deck(ids):
    """整副牌 -> ``{'name','hits','confidence'}``；认不出给空 dict（**不猜**）。

    与 `core.memread.my_deck_watch.identify_by_cards` 同一条打分路径
    （`deck_guess.suggest_own_deck`），区别只是**把置信度一起带出来** ——
    UI 要据此判断「这名字靠不靠谱」。

    ⚠️ 为什么我方卡组也走反推：玩家给卡组起的名字是任意的（「烙印40」
       「狱神神艺」），跟参照库条目对不上；**卡表才是稳定身份**。
    """
    #: ★ 第一判据：**卡组指纹**（用户导入的 YDK，2026-10-28）。
    #: 用户亲手给的卡表比参照库的统计推断更权威，所以排在最前。
    #: 容错判定见 `core.deck_fingerprint.compare`（换 15% 以内的卡仍算同一副）。
    try:
        from core import deck_fingerprint as _fp
        name, m = _fp.match_any(ids)
        if name:
            return {'name': name, 'hits': m.shared,
                    'confidence': m.jaccard, 'source': 'fingerprint'}
    except Exception:                                            # noqa: BLE001
        pass
    try:
        from core import deck_guess as dg
        idx, groups = dg.load_index()
        if not idx or not ids:
            return {}
        cands = dg.suggest_own_deck(ids, idx, groups)
        if not cands:
            return {}
        top = cands[0]
        return {'name': top.get('name') or '',
                'hits': int(top.get('hits') or 0),
                'confidence': top.get('confidence'),
                'source': 'reference'}
    except Exception:                                            # noqa: BLE001
        return {}


def read_current_deck_any_mode(proc, mirror, stbl, cw_ti=None):
    """按具体度依次试三个槽位 -> 第一个有值的快照 dict；全空 ``None``。

    快照形状各异（deck_name / container 有无），原样带出由调用方处理。
    """
    for reader in (cw.read_cupdeck, cw.read_ratedeck, cw.snapshot_my_deck):
        try:
            snap = reader(proc, mirror, stbl, cw_ti=cw_ti)
        except Exception:                                        # noqa: BLE001
            snap = None
        if snap and snap.get('ids'):
            return snap
    return None


def read_my_deck_live() -> Tuple[Optional[dict], str]:
    """读一次内存并写回 ``my_deck.json`` -> ``(state|None, error)``。

    state 是 ``my_deck.save()`` 的返回形状（含 ``container`` 标记来源槽位）；
    error 是给用户看的中文（空串 = 成功）。

    ⚠️ 首次调用约 10 秒（定位）、之后约 0.02 秒（走缓存）。
       调用方**不要放在 UI 主线程**里同步调 —— 见 `ui/floating.py` 的
       `refresh_deck_banner_from_memory`（现在走后台线程）。
    """
    pids = find_pids_by_name('masterduel.exe')
    if not pids:
        return None, ERR_NO_GAME
    pid = pids[0]
    with SafeProcess(pid) as proc:
        mirror, stbl, ti = _locate(proc, pid)
        if mirror is None:
            return None, game_metadata_missing_reason()
        if not ti:
            return None, ERR_NO_META
        snap = read_current_deck_any_mode(proc, mirror, stbl, cw_ti=ti)
    if not snap or not snap.get('ids'):
        return None, ERR_NOTHING
    #: ★ **卡表优先，游戏内名字只作参考**（用户 2026-10-28 定）。
    #:
    #: 为什么不能直接拿内存里的 ``deck_name`` 当软件卡组名：
    #: 那是**玩家自己随手起的**（实测「烙印40」「狱神神艺」「XXX 2」），
    #: 跟参照库/覆盖表里的条目名对不上 —— 卡图解析不到、统计按名字分桶也会散。
    #: **卡是不会变的**，所以身份一律认卡表；游戏内名字只用来：
    #:   ① 卡表认不出时的兜底显示；② 留档给人看（`game_name` 字段）。
    game_name = (snap.get('deck_name') or '').strip()
    ident = identify_own_deck(snap['ids'])
    name = ident.get('name') or ''
    if not name:
        # 卡表认不出（站外自组 / 参照库没这条）-> 才退回游戏内名字。
        # 此时状态是「读到了卡表但没归到参照库」，UI 会提示点卡图手动选。
        name = game_name
    if not name:
        return None, ERR_NOTHING
    rec = my_deck.save(name, ids=snap['ids'], src='m5-guess')
    rec['container'] = snap.get('container') or ''
    #: 游戏内原始名字（总是留着 —— 手动归名时用户能对上号）
    if game_name:
        rec['game_name'] = game_name
    if ident.get('confidence') is not None:
        rec['confidence'] = round(float(ident['confidence']), 3)
    #: 名字从哪来：'fingerprint'（用户导入的 YDK）/ 'reference'（参照库反推）/
    #: 空（退回游戏内名）。UI 可据此显示判定依据。
    if ident.get('source'):
        rec['ident_source'] = ident['source']
    return rec, ''


# ---------------------------------------------------------------- CLI（异步）
def main(argv=None) -> int:
    """命令行入口 —— **给 UI 用 QProcess 异步调**（别在主线程同步跑 10 秒）。

    输出一行 JSON 到 stdout，UI 侧解析：

        {"ok": true,  "name": "...", "container": "...", "error": ""}
        {"ok": false, "name": "",    "container": "",    "error": "..."}

    退出码：0 = 成功，1 = 失败（失败时 error 有中文原因）。
    """
    import json as _json
    import sys as _sys
    try:
        rec, err = read_my_deck_live()
    except Exception as e:                                       # noqa: BLE001
        rec, err = None, '读取异常：%s' % (e,)
    out = {'ok': bool(rec), 'name': (rec or {}).get('name') or '',
           'container': (rec or {}).get('container') or '',
           'error': err or ''}
    _sys.stdout.write(_json.dumps(out, ensure_ascii=False) + '\n')
    _sys.stdout.flush()
    return 0 if rec else 1


if __name__ == '__main__':
    raise SystemExit(main())
