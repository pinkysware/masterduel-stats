# -*- coding: utf-8 -*-
"""常驻观察者：**编辑界面**的卡组变了就重新识别一次，写进「当前卡组」状态
（`core.my_deck` 的 `data/my_deck.json`）。用户 2026-09-16 口径。

节拍（★ CPU 预算：全堆扫是秒级的，绝不能每拍做）
--------------------------------------------------
* **便宜路径**：记住上次找到的实例地址 —— 每拍只做「对象头校验 + 读两个 List」
  （`deck_edit.read_instance`）。在编辑界面里几乎零成本地发现「换牌了」。
* **重发现**：实例失效（离开编辑界面）后，**距上次全堆扫 ≥ `RESCAN_SEC`** 才再扫一次。
* 认出同一套牌（**卡表指纹相同**）就什么都不做 —— 反复进出编辑界面不会来回抖。
* 认不出（过不了闸门）**不覆盖**旧值（别拿噪声盖掉对的名字）。

⚠️ 本模块只读内存（`SafeProcess` 0x410），只写本机状态文件。
"""

import time
from typing import Optional

from core import my_deck
from core.memread import deck_edit

#: 重发现的最小间隔（秒）—— 全堆扫太贵，别每拍做
RESCAN_SEC = 120.0


class DeckEditReader(object):
    """真读内存的口子（把「全扫」与「按地址便宜读」分开，观察者只管节拍）。"""

    def __init__(self, proc, mirror, table):
        self.proc, self.mirror, self.table = proc, mirror, table
        self.typeinfo = None

    def scan(self):
        """全堆扫 -> ``(deck, 实例地址)``。"""
        deck, addr = deck_edit.scan_edited_deck(self.proc, self.mirror, self.table)
        if deck:
            self.typeinfo = deck.get('typeinfo')
        return deck, addr

    def read_at(self, addr):
        """便宜读（对象头校验 + 读两个 List）-> deck 或 None。"""
        if not deck_edit.is_instance(self.proc, addr, self.typeinfo):
            return None
        return deck_edit.read_instance(self.proc, self.mirror, addr)


def identify_by_cards(ids):
    """整副牌 -> 原型名（meta 清单里的 key，如 ``Blue-Eyes``）；认不出给 None。"""
    try:
        from core import deck_guess as dg
        idx, groups = dg.load_index()
        if not idx:
            return None
        cands = dg.suggest_own_deck(ids, idx, groups)
        return cands[0]['name'] if cands else None
    except Exception:                                            # noqa: BLE001
        return None


class MyDeckWatcher(object):
    """观察者本体（编排：便宜路径优先 + 重扫节流；识别函数可注入，单测打桩）。"""

    def __init__(self, reader, state_path=None, identify=None, log=None,
                 rescan_sec=RESCAN_SEC):
        self.reader = reader
        self.state_path = state_path
        self.identify = identify or identify_by_cards
        self.log = log
        self.rescan_sec = float(rescan_sec)
        self._inst = None                 # 上次找到的实例地址（便宜路径用）
        self._next_scan_at = 0.0

    def poll(self, now=None) -> Optional[dict]:
        """推进一步。返回**新写入的状态**（有变化时），否则 None。"""
        now = time.time() if now is None else now
        deck, addr = None, None
        if self._inst:
            deck = self.reader.read_at(self._inst)
            if deck is None:              # 实例没了（离开编辑界面）-> 走重扫节流
                self._inst = None
                self._next_scan_at = now + self.rescan_sec
                return None
            addr = self._inst
        else:
            if now < self._next_scan_at:
                return None
            deck, addr = self.reader.scan()
            self._next_scan_at = now + self.rescan_sec
            if not deck:
                return None
            self._inst = addr
        return self._consider(deck)

    def _consider(self, deck) -> Optional[dict]:
        ids = list(deck.get('ids') or [])
        fp = my_deck.fingerprint(ids)
        cur = my_deck.load(self.state_path)
        if cur.get('ids_fingerprint') == fp:
            return None                   # 同一套牌（顺序/份数变化不算）-> 不重复识别
        name = self.identify(ids)
        if not name:
            return None                   # 认不出别覆盖旧值
        return my_deck.save(name, ids=ids, src='m5-guess', path=self.state_path)
