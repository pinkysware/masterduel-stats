# -*- coding: utf-8 -*-
"""取「悠悠」的**纯卡绘**当缺图兜底 → `data/deck_images_custom/_default.webp`（手动跑，幂等）。

用法::

    python tools/fetch_deck_default.py            # 下载 + 缩到 200×200 + 存 webp
    python tools/fetch_deck_default.py --check    # 只看现状，不联网
    python tools/fetch_deck_default.py --card 6018  # 换一张卡（默认 6018 = 悠悠）

## 口径（用户 2026-09-15 定）

缺图时统一用**悠悠（Mokey Mokey）**的卡绘 —— 包括「未标注的对手卡组」那一格
和「已标注但没图」的卡组（`ui` 里的 `_default.webp` 就是这一张）。

## 三个坑（AGENTS.md 铁律 7 写过，实测复现过）

1. **URL 要用 Konami 密码，不是游戏内 id**：
   游戏内 id 是 `ygocdb` 卡表的 **`cid`**（悠悠 = 6018），
   Konami 密码是同一份表里的 **`id`**（悠悠 = 27288416）。
   拿 `6018.jpg!art` 去请求 **404**（实测）。
2. **必须带 `!art`**（256×256 纯卡绘）；去掉它拿到的是**整张卡**（带框带效果文本），
   和站内其它卡图风格不搭。
3. 落地尺寸统一 **200×200**（与 `data/deck_images_custom/` 里其它图一致）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

#: 卡绘 CDN（**纯卡绘**、无框无效果文）—— 按 **Konami 密码**寻址。
#:
#: ★ BUG-B9A-19（2026-10-19）：原来是
#:   `https://cdn.233.momobako.com/ygopro/pics/%s.jpg!art`，该 CDN **已 404**（与
#:   BUG-B2A-01 同源，本机实测连老卡都拿不到）。现走 ygoprodeck 的
#:   `cards_cropped`（与 `core/card_art.CROPPED_URL` 同一族：去框纯卡绘）。
#:   ⚠️ 别换成 `cards/<konami>.jpg` —— 那是**整张卡**（带框带文本），
#:   与站内其它卡组图风格不搭（AGENTS.md 铁律 7）。
ART_URL = 'https://images.ygoprodeck.com/images/cards_cropped/%s.jpg'
#: 我们自己的卡表快照（`cid` = 游戏内 id，`id` = Konami 密码）
CARDS_JSON = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')
OUT = os.path.join(PROJ, 'data', 'deck_images_custom', '_default.webp')
SIDE = 200


def konami_id(cid: str) -> str:
    """游戏内 id → Konami 密码（从 `ygocdb` 卡表读，**不硬编码**）。"""
    with open(CARDS_JSON, encoding='utf-8') as fh:
        cards = json.load(fh)
    card = cards.get(str(cid))
    if not card:
        raise SystemExit('卡表里没有 cid=%s' % cid)
    return str(card['id'])


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'masterduel-tools'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        #: 404 给专门文案 —— 否则下面 `loadFromData` 只会报
        #: 「下载到的不是图片」，看不出是地址失效（BUG-B9A-19）。
        if exc.code == 404:
            raise SystemExit('卡绘不存在（HTTP 404）：%s\n'
                             '这说明 CDN 地址又失效了 —— '
                             '先核对 ART_URL。' % url)
        raise SystemExit('下载失败（HTTP %s）：%s' % (exc.code, url))


def save_art(raw: bytes, out: str, side: int = SIDE) -> None:
    """缩到 `side`×`side` 存 webp（用 Qt，不引 Pillow）。

    ★ 落盘走 `core.atomic`（先写临时文件再 `os.replace`）—— `_default.webp` 是
    进版本库的**缺图兜底**，写一半崩了就会让所有没图的卡组都变成一块白板。
    """
    import tempfile
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from core import atomic

    img = QImage()
    if not img.loadFromData(raw):
        raise SystemExit('下载到的不是图片（前 40 字节：%r）' % raw[:40])
    img = img.scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix='.webp', prefix='_default-')
    os.close(fd)
    try:
        if not img.save(tmp, 'WEBP'):
            raise SystemExit('存 webp 失败：%s' % out)
        with open(tmp, 'rb') as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not atomic.write_bytes(out, data):
        raise SystemExit('写入失败：%s（原文件未改动）' % out)


def show(out: str) -> None:
    if not os.path.isfile(out):
        print('  %s：**不存在**' % out)
        return
    from PySide6.QtGui import QImage
    img = QImage(out)
    print('  %s：%d×%d，%.1f KB'
          % (out, img.width(), img.height(), os.path.getsize(out) / 1024))


def main() -> int:
    ap = argparse.ArgumentParser(description='取悠悠卡绘当缺图兜底')
    ap.add_argument('--card', default='6018', help='游戏内卡 id（默认 6018 = 悠悠）')
    ap.add_argument('--check', action='store_true', help='只看现状，不联网')
    args = ap.parse_args()

    km = konami_id(args.card)
    url = ART_URL % km
    print('卡 id=%s → Konami 密码 %s' % (args.card, km))
    print('卡绘 URL：%s' % url)
    if args.check:
        show(OUT)
        return 0

    raw = fetch(url)
    print('  下载 %d 字节' % len(raw))
    save_art(raw, OUT)
    show(OUT)
    print('完成（幂等，可重跑）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
