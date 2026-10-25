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

只读内存（``SafeProcess`` 0x410，无写入口）；UI 点按钮才调（有 1~2 秒
attach 成本，不进任何轮询热路径）。
"""
from __future__ import annotations

from typing import Optional, Tuple

from core.memread.process import SafeProcess, find_pids_by_name
from core import my_deck
from core.memread import client_work as cw
from core.memread.present_box import find_game_metadata, game_metadata_missing_reason

#: 错误文案（给用户看的中文，与 present_box_live 同风格）
ERR_NO_GAME = '没有检测到游戏进程 —— 请先启动 Master Duel'
ERR_NO_META = '没读到游戏数据 —— 请进一次游戏主界面再刷新'
ERR_NOTHING = '没读到卡组 —— 请进一次游戏主界面（或卡组编辑界面）再刷新'


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
    """
    pids = find_pids_by_name('masterduel.exe')
    if not pids:
        return None, ERR_NO_GAME
    with SafeProcess(pids[0]) as proc:
        found = find_game_metadata(proc)
        if not found:
            return None, game_metadata_missing_reason()
        _, mirror, stbl = found
        ti = cw.find_clientwork(proc, mirror, stbl)
        if not ti:
            return None, ERR_NO_META
        snap = read_current_deck_any_mode(proc, mirror, stbl, cw_ti=ti)
    if not snap or not snap.get('ids'):
        return None, ERR_NOTHING
    name = snap.get('deck_name')
    if not name:
        from core.memread.my_deck_watch import identify_by_cards
        name = identify_by_cards(snap['ids'])
    if not name:
        return None, ERR_NOTHING
    rec = my_deck.save(name, ids=snap['ids'], src='m5-guess')
    rec['container'] = snap.get('container') or ''
    return rec, ''
