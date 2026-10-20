# -*- coding: utf-8 -*-
"""常驻观察者：**编辑界面**的卡组变了就重新识别一次，写进「当前卡组」状态
（`core.my_deck` 的 `data/my_deck.json`）。用户 2026-09-16 口径。

节拍（★ CPU 预算：全堆扫是秒级的，绝不能每拍做）
--------------------------------------------------
* **便宜路径**：记住上次找到的实例地址 —— 每拍只做「对象头校验 + 读两个 List」
  （`deck_edit.read_instance`）。在编辑界面里几乎零成本地发现「换牌了」。
* **重发现**：实例失效（离开编辑界面）后，**距上次全堆扫 ≥ `RESCAN_SEC`** 才再扫一次。
* ⚠️ **「读不到」不等于「实例没了」**（BUG-B3-14）：一次瞬时读失败（正在删牌/换牌
  时 `_size` 短暂不足、页刚好被回收）不能让实例地址作废 —— 否则 120s 内不再重扫，
  用户换完牌两分钟看不到变化。判据走 `deck_edit.instance_state()` 三态：
  只有**确实不是本类实例**才作废地址；读失败只跳过这一拍，下一拍照旧重试。
  （同款结构见 `il2cpp.read_singleton_slot` 的 2026-10-14 事故修法。）
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

    def read_at_state(self, addr):
        """便宜读 -> ``(deck, state)``；``state`` ∈ ``'ok' / 'error' / 'gone'``。

        * ``ok``    —— 读到卡表（deck 非 None）；
        * ``error`` —— **这一拍读不到**（瞬时失败）→ 调用方**保留**实例地址，下拍重试；
        * ``gone``  —— 对象头说明它确实不是本类实例（离开编辑界面 / 地址被复用）。

        ⚠️ 把 ``error`` 当成 ``gone`` 就是 BUG-B3-14 的 120 秒盲区。
        """
        state = deck_edit.instance_state(self.proc, addr, self.typeinfo)
        if state is None:
            return None, 'error'
        if not state:
            return None, 'gone'
        deck = deck_edit.read_instance(self.proc, self.mirror, addr)
        if deck is None:
            return None, 'error'        # 实例还在，只是这一拍读不出卡表
        return deck, 'ok'

    def read_at(self, addr):
        """便宜读的旧口（deck 或 None）。三态请用 :meth:`read_at_state`。"""
        return self.read_at_state(addr)[0]


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

    def _read_at(self, addr):
        """reader 的三态接口；只有旧式 ``read_at`` 时按「None = 没了」兼容。"""
        fn = getattr(self.reader, 'read_at_state', None)
        if fn is not None:
            return fn(addr)
        deck = self.reader.read_at(addr)
        return deck, ('ok' if deck is not None else 'gone')

    def poll(self, now=None) -> Optional[dict]:
        """推进一步。返回**新写入的状态**（有变化时），否则 None。"""
        now = time.time() if now is None else now
        deck, addr = None, None
        if self._inst:
            deck, state = self._read_at(self._inst)
            if state == 'error':
                # 这一拍读不到 ≠ 实例没了：保留地址，下一拍照旧走便宜路径
                return None
            if state != 'ok':
                self._inst = None         # 确实不是本类实例 -> 走重扫节流
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
