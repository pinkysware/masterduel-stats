# -*- coding: utf-8 -*-
"""卡组名 → 卡图路径（M7 · **纯逻辑，无 GUI 依赖**）。

为什么需要它（2026-09-14 实测踩到的坑）：

    库里存的是**中文名**（`闪刀姬`），而图是按**站内英文名**命名的文件
    （`data/deck_images_custom/Sky_Striker.webp`）。
    直接拿中文名当文件名去找 —— **永远落到默认图**：

        local_image_for('闪刀姬')    -> _default.webp   ❌
        local_image_for('烙印星辰')  -> _default.webp   ❌

正确路径是经 `Storage.list_meta_decks()` 的**别名索引**（它每条都带 `img_file`，
且有 `name` / `name_cn` / `name_display` / `name_ja` / `aliases` 五个可匹配的键）。
实测库里出现过的卡组名 **11/15 命中**，差的几个（`K9vs`）再由
`Storage.search_meta_decks()` 的模糊匹配兜住。

解析链（**顺序别改**）：

1. 别名索引精确命中 → `img_file`
2. `search_meta_decks()` 模糊搜索取第一条 → `img_file`
3. 文件名直查（自定义条目 / 手动替换的图，**不可再生，进版本库**）
4. 全落空 → `_default.webp`

`tools/fetch_meta_decks.py` 的同名函数现在**委托到这里**，保证只有一份口径。
"""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, Optional, Sequence

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 站内下载的图（可再生）
IMG_DIR = os.path.join(PROJ, 'data', 'deck_images')
#: 自定义条目 + 手动替换的图（**不可再生**）
CUSTOM_IMG_DIR = os.path.join(PROJ, 'data', 'deck_images_custom')
#: 缺图时的兜底
DEFAULT_IMAGE = os.path.join(CUSTOM_IMG_DIR, '_default.webp')

IMAGE_EXTS: Sequence[str] = ('.webp', '.png', '.jpg', '.jpeg')

#: 文件名里不允许的字符（与 `tools/fetch_meta_decks` 保持一致）
RE_BAD_FN = re.compile(r'[\\/:*?"<>|\s]+')


def safe_filename(name: str) -> str:
    """卡组名 -> 安全的文件名（不含扩展名）。空格也算非法 → 下划线。"""
    s = RE_BAD_FN.sub('_', name or '').strip('._')
    return s or 'unnamed'


def rel_or_abs(path: Optional[str]) -> Optional[str]:
    """尽量给相对项目根的路径（统一正斜杠）；跨盘符才给绝对路径。"""
    if not path:
        return None
    try:
        return os.path.relpath(path, PROJ).replace('\\', '/')
    except ValueError:
        return path.replace('\\', '/')


def _find_in(directory: str, name: str) -> Optional[str]:
    """在某目录里按文件名找图（多扩展名依次试），返回绝对路径或 None。"""
    if not name:
        return None
    base = safe_filename(name)
    for ext in IMAGE_EXTS:
        p = os.path.join(directory, base + ext)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def find_image_file(name: Optional[str]) -> Optional[str]:
    """**文件名直查**（工具链用）—— 自定义目录优先，其次站内，最后默认图。

    不碰数据库、不需要 storage：`tools/fetch_meta_decks.py` 建清单时还没有
    meta_decks 表可查，只能按文件名约定找。找不到（连默认图也没有）返回 None。

    ⚠️ 与 `DeckImages.resolve` 的区别：那个是**名字解析链**
    （要 storage 查别名索引），面向界面；这个是**文件名约定**，面向工具链。
    两者共用本函数，别再各写一份。
    """
    p = _find_in(CUSTOM_IMG_DIR, name or '') or _find_in(IMG_DIR, name or '')
    if p is None and os.path.exists(DEFAULT_IMAGE):
        p = DEFAULT_IMAGE
    return p


def build_index(meta_decks: Iterable[dict]) -> Dict[str, str]:
    """``list_meta_decks()`` -> ``{任意叫法: img_file}``。

    收录 `name` / `name_cn` / `name_display` / `name_ja` / `aliases` 五个来源，
    **先到先得**（同名条目互相不覆盖，谁先被登记就用谁）。
    """
    idx: Dict[str, str] = {}
    for d in meta_decks or ():
        img = (d or {}).get('img_file')
        if not img:
            continue
        keys = [d.get('name'), d.get('name_cn'), d.get('name_display'),
                d.get('name_ja')]
        keys += list(d.get('aliases') or [])
        for k in keys:
            k = str(k or '').strip()
            if k:
                idx.setdefault(k, img)
    return idx


class DeckImages(object):
    """卡图解析器。索引**懒建一次**，之后只查字典（101 条，毫秒级）。"""

    __slots__ = ('_storage', '_index', '_searched')

    def __init__(self, storage=None, meta_decks: Optional[Iterable[dict]] = None):
        self._storage = storage
        self._index: Optional[Dict[str, str]] = (
            build_index(meta_decks) if meta_decks is not None else None)
        self._searched: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------ 索引
    def _ensure_index(self) -> Dict[str, str]:
        if self._index is None:
            try:
                meta = self._storage.list_meta_decks()
            except Exception:
                meta = []
            self._index = build_index(meta)
        return self._index

    def _fuzzy(self, name: str) -> Optional[str]:
        """模糊匹配兜底（`K9vs` -> `Vanquish Soul K9`）。结果缓存，失败也缓存。"""
        if name in self._searched:
            return self._searched[name]
        found = None
        try:
            hits = self._storage.search_meta_decks(name, limit=1)
            if hits:
                found = hits[0].get('img_file')
        except Exception:
            found = None
        self._searched[name] = found
        return found

    # ------------------------------------------------------------ 解析
    def resolve(self, name: Optional[str]) -> str:
        """卡组名 -> 卡图路径（**相对项目根**，永远有返回值 —— 至少是默认图）。

        输入可以是中文名 / 站内英文名 / 日文名 / 别名，任意一种。
        """
        abs_path = self.resolve_abs(name)
        return rel_or_abs(abs_path) or rel_or_abs(DEFAULT_IMAGE) or ''

    def resolve_abs(self, name: Optional[str]) -> Optional[str]:
        name = (name or '').strip()
        if not name:
            return DEFAULT_IMAGE if os.path.exists(DEFAULT_IMAGE) else None

        # 1) 别名索引精确命中
        hit = self._ensure_index().get(name)
        if hit and os.path.exists(os.path.join(PROJ, hit)):
            return os.path.join(PROJ, hit)

        # 2) 模糊匹配
        if self._storage is not None:
            hit = self._fuzzy(name)
            if hit and os.path.exists(os.path.join(PROJ, hit)):
                return os.path.join(PROJ, hit)

        # 3) 文件名直查（自定义条目 / 手动替换的图）+ 默认图兜底
        return find_image_file(name)

    # ------------------------------------------------- 给 UI 的小便利
    def is_placeholder(self, name: Optional[str]) -> bool:
        """这个名字是不是只能落到默认图（UI 可以据此标注「图缺」）。"""
        p = self.resolve_abs(name)
        return (not p) or os.path.normcase(p) == os.path.normcase(DEFAULT_IMAGE)
