# -*- coding: utf-8 -*-
"""YDK 卡组文件解析 —— 把「一副牌」变成我们自己的卡 id 列表。

为什么需要它（用户 2026-10-28）
------------------------------
要判断「当前我方卡组是不是用户指定的那副」，需要一个**精确**的卡表来源。
两条路：

1. **卡组截图 → OCR/哈希识别**：`tieba_scrape/getdeck/` 那套（YOLO + pHash）。
   ❌ 硬障碍：依赖 `onnxruntime`，它**不在项目主环境**（只在单独的
   `.venv-recog`），普通用户装的软件包里没有 → 点按钮直接失败。
   ❌ 而且实测单张截图只能认出 39~49 张（真卡组 55 张），必然漏识。

2. **YDK 文件**（★ 本模块）：get-deck.com 等工具都能导出。
   ✅ 纯文本、字段精确、零漏识；✅ 不依赖任何第三方库；
   ✅ 用户在游戏里也能导出（游戏内的卡组 → 复制到剪贴板）。

YDK 格式（文本，``#`` / ``!`` 开头是指令，其余是 **Konami 密码**，每行一个）
------------------------------------------------------------------

::

    #created by ...
    #main
    89631139
    89631139
    14558127
    ...
    #extra
    40044918
    !side
    23434538

* ``#main`` / ``#extra`` / ``!side`` 分段
* 每个数字是 **Konami 密码**（8 位），不是游戏内 id
* 同一张卡放 3 份就是 3 行

⚠️ **必须做 Konami 密码 → 游戏内 id 的换算**（两者不是一回事）
------------------------------------------------------------------
``ygocdb.com.cards/cards.json`` 里同一条记录同时有 ``id``（Konami 密码）和
``cid``（游戏内 id）—— 实测 14299 条映射、**零冲突**：

    89631139 -> cid=4007   青眼白龙
    14558127 -> cid=12950  灰流丽      ← 与日志/内存里的 id 一致
    23434538 -> cid=9455   增殖的G

所以换算只有一次性建表 + O(1) 查表。

不做什么
--------
* **不做容错匹配**（那是 ``core.deck_fingerprint`` 的事）——本模块只负责
  「文件 → 精确卡 id 列表」。
* **不读写数据库**、不联网、不碰游戏内存。
"""
from __future__ import annotations

import os
from typing import Dict, List, NamedTuple, Optional, Tuple

#: YDK 的分段标记 -> 我们的段名
SECTION_MAIN = 'main'
SECTION_EXTRA = 'extra'
SECTION_SIDE = 'side'

#: 认得出的段头（小写）。``!side`` 用感叹号是 YDK 的历史遗留写法。
_SECTION_MARKERS = {
    '#main': SECTION_MAIN,
    '#extra': SECTION_EXTRA,
    '!side': SECTION_SIDE,
    '#side': SECTION_SIDE,
}


class YdkDeck(NamedTuple):
    """解析结果。**保留重复**（3 张同名卡 = 3 个 id）。"""

    main: List[int] = []          # 主卡组（Konami 密码，原样）
    extra: List[int] = []
    side: List[int] = []
    unknown_lines: List[str] = []  # 认不出的行（诊断用，不参与匹配）

    @property
    def all_ids(self) -> List[int]:
        """主 + 额外（**不含 side** —— side 不进对局卡组）。"""
        return list(self.main) + list(self.extra)

    @property
    def unique_count(self) -> int:
        return len(set(self.all_ids))


def parse_ydk(text: str) -> YdkDeck:
    """YDK 文本 -> :class:`YdkDeck`（**纯函数**，不读盘）。

    容错：
    * 空行 / 注释（``#`` ``!`` 开头的非段头行）跳过；
    * 非数字行进 ``unknown_lines``（不炸、不参与匹配）；
    * 没有段头时（有的导出工具省略）**全部当主卡组**。
    """
    main: List[int] = []
    extra: List[int] = []
    side: List[int] = []
    unknown: List[str] = []
    buckets = {SECTION_MAIN: main, SECTION_EXTRA: extra, SECTION_SIDE: side}
    cur = SECTION_MAIN          # 没段头时默认主卡组

    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low in _SECTION_MARKERS:
            cur = _SECTION_MARKERS[low]
            continue
        # 其它 # / ! 开头 = 注释（#created by ... / !something）
        if line[0] in '#!':
            continue
        if not line.isdigit():
            unknown.append(line)
            continue
        try:
            buckets[cur].append(int(line))
        except ValueError:                                   # noqa: BLE001
            unknown.append(line)
    return YdkDeck(main=main, extra=extra, side=side, unknown_lines=unknown)


def read_ydk(path: str) -> Tuple[Optional[YdkDeck], str]:
    """读一个 YDK 文件 -> ``(deck|None, error)``。error 是给用户看的中文。"""
    try:
        if not os.path.exists(path):
            return None, '文件不存在：%s' % path
        with open(path, 'rb') as f:
            raw = f.read()
    except Exception as e:                                   # noqa: BLE001
        return None, '读不了这个文件：%s' % (e,)
    # YDK 一般是 UTF-8 或纯 ASCII；带 BOM 的也认
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except Exception:                                    # noqa: BLE001
            continue
    else:
        return None, '文件编码认不出（试过 utf-8 / gbk）'
    deck = parse_ydk(text)
    if not deck.all_ids:
        return None, ('这个文件里没有卡（是不是空的？）')
    return deck, ''


# ---------------------------------------------------------------- 密码换算
#: Konami 密码(str) -> 游戏内 id(str)。模块级缓存（卡表 13MB，只解析一次）。
_CODE_TO_CID: Dict[str, str] = {}


def _ensure_map() -> Dict[str, str]:
    if _CODE_TO_CID:
        return _CODE_TO_CID
    try:
        from core.card_names import ygocdb
        cards = ygocdb()
    except Exception:                                        # noqa: BLE001
        return _CODE_TO_CID
    if not isinstance(cards, dict):
        return _CODE_TO_CID
    for cid, c in cards.items():
        if not isinstance(c, dict):
            continue
        code = c.get('id')          # Konami 密码
        if not code:
            continue
        # setdefault：万一将来出现重复，**先到者赢**（实测当前 0 冲突）
        _CODE_TO_CID.setdefault(str(code), str(cid))
    return _CODE_TO_CID


def clear_code_map_cache() -> None:
    """清缓存（单测 / 卡表更新后用）。"""
    _CODE_TO_CID.clear()


def code_to_cid(code) -> Optional[str]:
    """Konami 密码 -> 游戏内 id（``'89631139'`` -> ``'4007'``）；查不到 None。"""
    return _ensure_map().get(str(code))


def to_game_ids(codes, *, keep_unknown: bool = False) -> List[str]:
    """``[Konami 密码]`` -> ``[游戏内 id]``（**保留重复**= 卡的份数）。

    :param keep_unknown: True 时把查不到的密码**原样**留在结果里（诊断用）；
                         False（默认）时丢弃 —— 卡片 id 空间不同，绝不能混。
    """
    out: List[str] = []
    m = _ensure_map()
    for code in codes:
        cid = m.get(str(code))
        if cid is not None:
            out.append(cid)
        elif keep_unknown:
            out.append(str(code))
    return out


def unknown_codes(codes) -> List[str]:
    """哪些密码在 ygocdb 里查不到（给用户看「这几张我认不出」）。"""
    m = _ensure_map()
    return sorted({str(c) for c in codes if str(c) not in m})
