# -*- coding: utf-8 -*-
"""卡绘缓存 —— 单卡插图（`!art` 纯卡绘）下载 + 本地缓存。

⚠️ **URL 用 Konami 密码，不是游戏内 id**（实测 12950 直接取 404、换 14558127 才对）。
密码从 `ygocdb.com.cards/cards.json` 的 `id` 字段读（与 `tools/fetch_deck_default.py`
同一套口径，那边是「卡组默认图」的单张取法）。

缓存目录 ``data/card_art/<Konami密码>.webp`` —— **可再生**（随时能重下），
按 `data/deck_images/` 的先例**进 .gitignore**，别进版本库。

三条纪律：
1. **失败不抛** —— 单张取不到返回 None，界面留空占位（布局不跳）；
2. **礼貌抓取** —— 串行 + 间隔，别并发猛拉别人家的 CDN（月度/按需触发，不做定时）；
3. **网络只在 `fetch_art()` 里** —— `art_path()` 纯本地查缓存，界面先问它。
"""
from __future__ import annotations

import os
import time
from typing import Dict, Optional

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: **整张卡** CDN（带卡框与文案，2026-09-21 用户：「完整的卡图，包括卡框」——
#: 带 `!art` 后缀是纯卡绘 256²，那版被推翻）
ART_URL = 'https://cdn.233.momobako.com/ygopro/pics/%s.jpg'
#: 缓存目录（可再生 → gitignore）
CACHE_DIR = os.path.join(PROJ, 'data', 'card_art')
#: 存图**高**（完整卡是 59:86 长方形，宽按比例；KeepAspectRatio 不裁切）
SIDE = 200
#: 两次下载之间的最小间隔（秒）—— 礼貌限流
MIN_GAP = 0.05

#: 游戏内 id -> Konami 密码（进程内缓存，13MB JSON 只读一次 —— 走
#: :func:`core.card_names.ygocdb`，与卡类型访问器共用同一份解析结果）
_KONAMI: Dict[str, str] = {}
#: 英文名（归一后小写）-> Konami 密码（禁限表按英文名查，见 `konami_by_en`）
_EN: Dict[str, str] = {}
#: 再压一层（去连字符 / 合并空格）—— 站内与 ygocdb 的连字符习惯不同
_EN_SQUEEZED: Dict[str, str] = {}
_LOADED = False
_last_fetch = [0.0]


def _load():
    """读一次 ygocdb 卡表，建两张索引（游戏 id / 英文名 → Konami 密码）。

    ⚠️ 解析走 :func:`core.card_names.ygocdb`（**同一个 13MB JSON 全进程只解析一次**，
    主卡/额外卡类型判据也在用它）—— 别在这里再 `json.load` 一遍。
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from core.card_names import ygocdb
        cards = ygocdb()
    except Exception:                                    # noqa: BLE001
        return
    if not isinstance(cards, dict):
        return
    for cid, c in cards.items():
        if not isinstance(c, dict):
            continue
        konami = c.get('id')
        if not konami:
            continue
        _KONAMI[str(cid)] = str(konami)
        en = (c.get('en_name') or '').strip().lower()
        if en:
            key = en.replace('<', '').replace('>', '')
            _EN.setdefault(key, str(konami))
            _EN_SQUEEZED.setdefault(' '.join(key.replace('-', ' ').split()),
                                    str(konami))


def konami_id(cid) -> Optional[str]:
    """游戏内卡 id -> Konami 密码（找不到返回 None）。"""
    _load()
    return _KONAMI.get(str(cid))


def _norm_en(name: str) -> str:
    """英文卡名归一 —— 只去角括号：站内写 `Maliss P White Rabbit`，
    ygocdb 写 `Maliss <P> White Rabbit`（实测 4 张 Maliss 卡就卡在这里）。"""
    return (name or '').strip().lower().replace('<', '').replace('>', '')


def konami_by_en(name: str) -> Optional[str]:
    """**英文卡名** -> Konami 密码（禁限表那批数据只有英文名）。

    两级匹配：归一后精确 → 归一后去连字符/多余空格再试；查不到返回 None
    （界面留占位，绝不吞信息）。
    """
    _load()
    key = _norm_en(name)
    hit = _EN.get(key)
    if hit:
        return hit
    squeezed = ' '.join(key.replace('-', ' ').split())
    return _EN_SQUEEZED.get(squeezed)


def art_path(cid) -> Optional[str]:
    """本地缓存路径；**没有缓存返回 None**（纯本地，不联网）。"""
    konami = konami_id(cid)
    if not konami:
        return None
    p = os.path.join(CACHE_DIR, '%s.webp' % konami)
    return p if os.path.exists(p) else None


def art_path_by_en(name: str) -> Optional[str]:
    """英文卡名版的 `art_path`（禁限表用）。"""
    konami = konami_by_en(name)
    if not konami:
        return None
    p = os.path.join(CACHE_DIR, '%s.webp' % konami)
    return p if os.path.exists(p) else None


def _download(konami: str) -> Optional[bytes]:
    """下一次图（带 UA 与最小间隔）；失败返回 None。"""
    import urllib.request
    gap = MIN_GAP - (time.time() - _last_fetch[0])
    if gap > 0:
        time.sleep(gap)
    try:
        req = urllib.request.Request(ART_URL % konami,
                                     headers={'User-Agent': 'masterduel-tools'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        _last_fetch[0] = time.time()
        return raw
    except Exception:                                    # noqa: BLE001
        _last_fetch[0] = time.time()
        return None


def _save(raw: bytes, out: str, side: int = SIDE) -> bool:
    """缩放存 webp（用 Qt，不引 Pillow —— 同 `tools/fetch_deck_default.py`）。

    ⚠️ **KeepAspectRatio**：完整卡是长方形，IgnoreAspectRatio 会把卡面压扁。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        img = QImage()
        if not img.loadFromData(raw):
            return False
        img = img.scaled(10000, side, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        return bool(img.save(out, 'WEBP'))
    except Exception:                                    # noqa: BLE001
        return False


def fetch_art(cid=None, name_en=None) -> Optional[str]:
    """确保某张卡的卡绘在本地（**要联网**）→ 返回路径；失败返回 None。

    传 ``cid``（游戏内 id）或 ``name_en``（英文名，禁限表用）二选一。
    """
    if cid is not None:
        konami = konami_id(cid)
    else:
        konami = konami_by_en(name_en)
    if not konami:
        return None
    out = os.path.join(CACHE_DIR, '%s.webp' % konami)
    if os.path.exists(out):
        return out
    raw = _download(konami)
    if not raw or not _save(raw, out):
        return None
    return out
