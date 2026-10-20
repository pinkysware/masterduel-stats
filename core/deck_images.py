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
import shutil
from typing import Dict, Iterable, Optional, Sequence
from core import paths

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 站内下载的图（可再生）
#: ★ BUG-B2A-08（2026-10-18）：两个目录都改走 `paths.data_path` ——
#:   安装版（Program Files）的 `<PROJ>/data` 不可写：用户换图存不进去、
#:   自己加的图也不在包里 → 「换图功能静默失效」。
#:   `data/deck_images_custom/` 是**进版本库的用户资产**，更要能写。
IMG_DIR = paths.data_path('deck_images')
#: 自定义条目 + 手动替换的图（**不可再生**）
CUSTOM_IMG_DIR = paths.data_path('deck_images_custom')
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


#: 转存自定义卡图时的落地边长（与 `data/deck_images_custom/` 里其它图一致）
TARGET_SIZE = 200


def _normalize_to_webp(src_path: str, out_path: str,
                       target_size: int = TARGET_SIZE) -> Optional[str]:
    """把图**居中裁方 + 缩到 `target_size` + 存 webp**；做不到返回 ``None``。

    两条腿（**口径只留这一份** —— CLI 与 UI 都走它，别再各写一套）：

    1. **Pillow**（装了最好，质量最高）；
    2. **Qt**（`PySide6.QtGui.QImage`，项目本来就依赖 —— 实测**不需要 QApplication**
       也能读/写图）—— 这台机器没装 Pillow，靠它才能统一成 200×200 webp。

    都不行 → ``None``，调用方原样拷贝兜底。

    ⚠️ 为什么必须归一化：卡绘原图是 624² 级别，原样存会把**进版本库**的覆盖目录
    撑大好几倍（用户专门优化过 lite 发布包体积）。
    """
    try:
        from PIL import Image
        im = Image.open(src_path).convert('RGBA')
        side = min(im.size)
        left, top = (im.width - side) // 2, (im.height - side) // 2
        im = im.crop((left, top, left + side, top + side)).resize(
            (int(target_size), int(target_size)), Image.LANCZOS)
        im.save(out_path, 'WEBP', quality=92, method=6)
        return out_path
    except ImportError:
        pass
    except Exception:                                    # noqa: BLE001
        pass
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        img = QImage(src_path)
        if img.isNull():
            return None
        side = min(img.width(), img.height())
        img = img.copy((img.width() - side) // 2, (img.height() - side) // 2,
                       side, side).scaled(int(target_size), int(target_size),
                                          Qt.IgnoreAspectRatio,
                                          Qt.SmoothTransformation)
        return out_path if img.save(out_path, 'WEBP') else None
    except Exception:                                    # noqa: BLE001
        return None


def save_custom_image(name: str, src_path: str, target_size: int = TARGET_SIZE,
                      dest_dir: Optional[str] = None) -> Optional[str]:
    """把一张图存成 ``data/deck_images_custom/<名字>.<ext>``（覆盖目录）。

    给「给某卡组换图」用（CLI `tools/set_deck_image.py` 与看板卡组名管理共用）——
    覆盖目录**优先于**站内图，且站内月度重抓不会盖回去（AGENTS.md 铁律 7）。

    * 装了 Pillow → 居中裁剪成正方形 + 缩到 ``target_size`` + 存 **webp**；
      没装 → **原样拷贝**（保留扩展名；界面会自己缩放，但尺寸不统一）。
    * 会**清掉同名别的扩展名**旧文件 —— 不清的话它更早被 `_find_in` 命中。
    * 返回**相对项目根**的路径（统一正斜杠）；失败返回 ``None``。
    * ``dest_dir`` 默认 = 覆盖目录（``data/deck_images_custom/``）；
      **单测传临时目录**，别往真图目录里写。
    """
    name = (name or '').strip()
    if not name or not src_path or not os.path.exists(src_path):
        return None
    dest = dest_dir or CUSTOM_IMG_DIR
    os.makedirs(dest, exist_ok=True)
    base = os.path.join(dest, safe_filename(name))

    dst = _normalize_to_webp(src_path, base + '.webp', target_size)
    if dst is None:
        #: Pillow / Qt 都没有 -> 原样拷贝（尺寸不统一，但别把它变成失败）
        ext = os.path.splitext(src_path)[1].lower() or '.webp'
        dst = base + ext
        shutil.copyfile(src_path, dst)

    for ext in IMAGE_EXTS:                         # 清掉同名别的扩展名旧图
        other = base + ext
        if (os.path.normcase(other) != os.path.normcase(dst)
                and os.path.exists(other)):
            try:
                os.remove(other)
            except OSError:
                pass
    return rel_or_abs(dst)


#: **纯卡绘** CDN（去卡框、去效果文）—— 按 **Konami 密码**寻址（不是游戏内卡 id）。
#: ⚠️ 与 `core/card_art.py` 的区别：那边是**整张卡**（带框带文案，禁限表页要用），
#: 这边是 `cards_cropped`（纯卡绘，给卡组图用）。别混。
CARD_ART_CROPPED_URL = 'https://images.ygoprodeck.com/images/cards_cropped/%s.jpg'


def fetch_card_art(card_id, timeout: int = 20) -> Optional[str]:
    """某张卡的**纯卡绘** -> 临时文件路径（失败返回 ``None``，不抛）。

    用户 2026-10-08（研究结论落地）：**卡组图 == 代表卡的卡绘**（实测站内
    `deck-type` 图与该卡组代表卡的相关性 0.90~0.96）。所以「给卡组配图」
    可以退化成「挑一张代表卡」——敲中文卡名即可，不用记英文站内名。

    ``card_id`` = **游戏内卡 id**（ygocdb 的 ``cid``）；Konami 密码从这里换算
    （`core.card_art.konami_id`，与项目别处同一口径）。
    """
    import tempfile
    import urllib.request
    try:
        from core.card_art import konami_id
        konami = konami_id(card_id)
    except Exception:                                  # noqa: BLE001
        konami = None
    if not konami:
        return None
    url = CARD_ART_CROPPED_URL % konami
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'masterduel'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except Exception:                                  # noqa: BLE001
        return None
    if not body:
        return None
    fd, tmp = tempfile.mkstemp(suffix='.jpg', prefix='mdm-cardart-')
    with os.fdopen(fd, 'wb') as f:
        f.write(body)
    return tmp


#: 站内卡组图的 CDN（2026-10-08 实测：返回 200×200 webp，与 `data/deck_images/`
#: 同源同规格；只覆盖**榜上卡组**，自组混轴名本身会 404）
SITE_IMG_URL = ('https://imgserv.duellinksmeta.com/v2/mdm/deck-type/%s'
                '?portrait=true&width=200')


def fetch_site_deck_image(site_name: str, timeout: int = 20) -> Optional[str]:
    """从站内取某卡组的卡图 → **临时文件路径**（失败返回 ``None``，不抛）。

    给「从站内卡组取图」用（CLI `--from-site` 与看板换图共用同一口径）。
    ⚠️ 名字是**站内英文名**（`Elfnote` / `Power Patron` …），不是中文名；
    站内榜上没有的（自组混轴）会 404 → 返回 None，调用方给提示。
    """
    import tempfile
    import urllib.parse
    import urllib.request
    name = (site_name or '').strip()
    if not name:
        return None
    url = SITE_IMG_URL % urllib.parse.quote(name, safe='')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'masterduel'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
    except Exception:                                  # noqa: BLE001
        return None
    if not body:
        return None
    ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
           'image/gif': '.gif'}.get(ctype.lower(), '.webp')
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix='mdm-site-')
    with os.fdopen(fd, 'wb') as f:
        f.write(body)
    return tmp


def remove_custom_image(name: str) -> list:
    """删掉覆盖目录里这个名字**所有扩展名**的图；返回被删文件名列表。"""
    removed = []
    base = os.path.join(CUSTOM_IMG_DIR, safe_filename(name or ''))
    for ext in IMAGE_EXTS:
        p = base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append(os.path.basename(p))
            except OSError:
                pass
    return removed


def find_image_file(name: Optional[str]) -> Optional[str]:
    """**文件名直查**（工具链用）—— 自定义目录优先，其次站内。**找不到返回 None**。

    不碰数据库、不需要 storage：`tools/fetch_meta_decks.py` 建清单时还没有
    meta_decks 表可查，只能按文件名约定找。

    ⚠️ 与 `DeckImages.resolve` 的区别：那个是**名字解析链**
    （要 storage 查别名索引），面向界面；这个是**文件名约定**，面向工具链。
    两者共用本函数，别再各写一份。

    🔴 **绝不返回 `DEFAULT_IMAGE`**（2026-10-10 修的真事故）：以前兜底会把默认图
    当成「找到的图」返回，调用方（`fetch_meta_decks.custom_rows`）分不清
    「找到真图」和「拿到兜底」，于是把 `deck_images_custom/_default.webp`
    **当成真图路径写进了 `meta_decks.img_file`** —— 实测污染 14 条（`Yummy` /
    `Fur Hire` / `Orcust` / `Radiant Typhoon` / `Rescue-ACE` / `Vanquish Soul` …）。
    库被污染后，**用户后来把真图放进 `deck_images/` 也救不回来**：别名索引命中
    的正是这个 `_default.webp` 路径（`resolve` 第 1 环 `os.path.exists` 通过），
    于是永远显示默认图 —— 这正是发布包首次打开大量卡组默认化的直接原因。
    要默认图请调 :meth:`DeckImages.resolve` 或显式用 `DEFAULT_IMAGE`。
    """
    return _find_in(CUSTOM_IMG_DIR, name or '') or _find_in(IMG_DIR, name or '')


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

        # 0) ★ **覆盖目录里按这个名字直接放的图优先**（2026-10-09）
        #: 用户 2026-10-09：给「狱神耀圣」配了图，但把该名字挂成 `Elfnote` 的**别名**
        #: 之后，别名索引（第 1 环）会把它解析到站内 `Elfnote.webp`，把用户自己放的
        #: 图盖掉。用户**按名字放的那张**是更明确的人工意图，必须最优先。
        #: ⚠️ 只查**覆盖目录**（`deck_images_custom/`），站内目录照旧走下面几环。
        own = _find_in(CUSTOM_IMG_DIR, name)
        if own:
            return own

        # 1) 别名索引精确命中
        hit = self._ensure_index().get(name)
        if hit and os.path.exists(os.path.join(PROJ, hit)):
            return os.path.join(PROJ, hit)

        # 2) 模糊匹配
        if self._storage is not None:
            hit = self._fuzzy(name)
            if hit and os.path.exists(os.path.join(PROJ, hit)):
                return os.path.join(PROJ, hit)

        # 3) 文件名直查（自定义条目 / 手动替换的图）
        #    ⚠️ `find_image_file` **不再兜底默认图**（见其文档）—— 默认图在这里显式加，
        #    保证界面仍然「永远有图」，而工具链拿得到诚实的 None。
        return (find_image_file(name)
                or (DEFAULT_IMAGE if os.path.exists(DEFAULT_IMAGE) else None))

    # ------------------------------------------------- 给 UI 的小便利
    def is_placeholder(self, name: Optional[str]) -> bool:
        """这个名字是不是只能落到默认图（UI 可以据此标注「图缺」）。"""
        p = self.resolve_abs(name)
        return (not p) or os.path.normcase(p) == os.path.normcase(DEFAULT_IMAGE)
