# -*- coding: utf-8 -*-
"""换掉某个卡组的卡图。

图统一放 ``data/deck_images_custom/``（**覆盖目录**）—— 它优先于站内下载的
``data/deck_images/``，而且站内月度重抓**不会**把它盖回去（见 AGENTS.md 铁律 7）。

用法::

    python tools/set_deck_image.py --list                    # 看所有卡组 + 当前用哪张图
    python tools/set_deck_image.py 烙印 D:\\图\\xx.png         # 换成这张（卡组名支持模糊匹配）
    python tools/set_deck_image.py 狱神耀圣 D:\\图\\xx.png     # ★ 游戏内自组卡组名也能换
    python tools/set_deck_image.py 狱神耀圣 --from-site "Power Patron"
                                                             # ★ 直接取站内某卡组的图
    python tools/set_deck_image.py 烙印 --reset               # 撤掉自换的图，回到站内图

图片格式：**一律归一化成 200x200 webp**（居中裁剪 + 缩放，和其他卡图统一）——
归一化在 `core.deck_images._normalize_to_webp`，**Pillow 优先、没有就走 Qt**
（Qt 是项目本来就有的依赖，不需要 QApplication）。只有 Pillow 与 Qt 都不可用时
才原样拷贝（那时文案会明说「尺寸未统一」）。★ BUG-B9A-04：原先文案按
「有没有装 Pillow」猜，本机没装 Pillow 就永远说「原样拷贝」，与实际行为不符。

★ 2026-10-08（用户报「自定义卡组没有卡图」）：
  * `find_deck` 找不到 `meta_decks` 条目时**不再直接报错** —— 游戏内自组卡组名
    （如「狱神耀圣」）照样能换图（按名字直查，覆盖目录里的 ``<名字>.webp``）；
  * 新增 ``--from-site "<站内卡组名>"``：**从站内取图**给这个卡组用。
    适合「自组卡组 = 某站内卡组的小改」（实测：狱神耀圣 = 耀圣轴 → 站内 Elfnote /
    Power Patron 有同名卡绘），省得自己找图、裁图。
"""

import argparse
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import fetch_meta_decks as fmd                     # noqa: E402
from core import deck_images as dimg               # noqa: E402
from core.storage import Storage                   # noqa: E402

TARGET_SIZE = 200


def find_deck(st, name):
    """模糊找卡组，返回唯一的 Storage 行；找不到/多个就返回 (None, 说明)。

    ⚠️ 找不到时**不再直接报错** —— 游戏内自组卡组名（如「狱神耀圣」）不在
    `meta_decks` 表里，但照样可以按名字换图（见 `cmd_set` 的回落分支）。
    """
    hits = st.search_meta_decks(name, limit=5)
    exact = [h for h in hits if h['name'] == name or h.get('name_cn') == name]
    if len(exact) == 1:
        return exact[0], None
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, None            # 交给调用方走「按名字直查」（自组卡组名）
    return None, '匹配到多个，写清楚点：%s' % '、'.join(
        '%s(%s)' % (h['name'], h.get('name_cn') or '-') for h in hits)


def convert(src, dst_base):
    """把 ``src`` 存成覆盖目录里的 ``<dst_base>.*``，返回 ``(产出路径, 说明)``。

    ⚠️ 实现已搬到 :func:`core.deck_images.save_custom_image`（**口径只有一份**）——
    UI 也要用同一个，而 `ui/` 不许 import `tools/`。

    ★ BUG-B9A-04：说明文字**量产物**，不再按「有没有装 Pillow」猜。归一化其实
    由 `save_custom_image` 的 Pillow -> Qt 两条腿完成；本机没 Pillow 也照样
    产出 200x200 webp（Qt 那条腿），旧文案却永远说「原样拷贝」。
    """
    base_name = os.path.splitext(os.path.basename(dst_base))[0]
    src_size = _src_size(src)
    rel = dimg.save_custom_image(base_name, src, TARGET_SIZE)
    if rel is None:
        return None, '写入失败'
    dst = os.path.join(PROJ, rel.replace('/', os.sep))
    out_size = _src_size(dst)
    if rel.lower().endswith('.webp'):
        note = '%dx%d -> %dx%d webp（居中裁剪 + 缩放）' % (src_size + out_size)
    else:
        note = ('原样拷贝（%dx%d -> %dx%d）：Pillow 与 Qt 都不可用，'
                '尺寸未统一，界面会自己缩放' % (src_size + out_size))
    return dst, note


def _src_size(path):
    """读图片尺寸 —— Pillow 优先，没有就走 Qt（本机就没装 Pillow）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:                              # noqa: BLE001
        pass
    try:
        from PySide6.QtGui import QImage
        img = QImage(path)
        if not img.isNull():
            return (img.width(), img.height())
    except Exception:                              # noqa: BLE001
        pass
    return (0, 0)


def cmd_list(st):
    rows = st.list_meta_decks(include_hidden=True)
    print('%-24s %-10s %-46s %s' % ('卡组', '中文名', '当前图', '来源'))
    print('-' * 108)
    for r in rows:
        p = r.get('img_file') or ''
        src = ('自定义/自换' if 'deck_images_custom' in p
               else '站内' if 'deck_images/' in p else '默认' if p else '无')
        print('%-24s %-10s %-46s %s%s' % (
            r['name'][:24], (r.get('name_cn') or '-')[:10], p or '-', src,
            '（隐藏）' if r['hidden'] else ''))
    print()
    print('共 %d 条；覆盖目录：%s' % (len(rows), os.path.relpath(fmd.IMG_CUSTOM_DIR, PROJ)))
    print('（「自定义卡组名」如 狱神耀圣 不在 meta 表里，也支持换图 —— 直接拿名字当参数）')


def _named_base(deck_name):
    """按**名字**给覆盖图算路径基名（自组卡组 / 游戏内卡组名走这条）。

    与 `DeckImages.resolve` 第 3 环「文件名直查」同一口径：
    `data/deck_images_custom/<名字去非法字符>.webp` 每次读磁盘、立即生效。
    """
    base = os.path.join(fmd.IMG_CUSTOM_DIR, fmd.safe_filename(deck_name))
    os.makedirs(fmd.IMG_CUSTOM_DIR, exist_ok=True)
    return base


def fetch_site_image(site_name):
    """从站内取某卡组的图（``imgserv.duellinksmeta.com``）→ 临时文件路径。

    2026-10-08 实测：``deck-type/<站内英文名>`` 返回 200×200 webp（与
    `data/deck_images/` 同源同规格）；只覆盖**榜上卡组**，自组混轴名本身会 404。
    失败抛异常（调用方翻成提示）。
    """
    import tempfile
    url = fmd.image_url_for({'name': site_name, 'kind': 'deck_type'})
    body, ctype = fmd.fetch(url)
    ext = fmd.EXT_BY_CTYPE.get((ctype or '').split(';')[0].strip().lower(),
                               '.webp')
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix='mdm-site-')
    with os.fdopen(fd, 'wb') as f:
        f.write(body)
    return tmp, len(body)


def cmd_set(st, deck_name, image_path):
    if not os.path.exists(image_path):
        print('[错误] 图片不存在：%s' % image_path)
        return 2
    row, err = find_deck(st, deck_name)
    if err:
        print('[错误] %s' % err)
        return 2
    if row is None:
        # 不在 meta 表里（游戏内自组卡组名）—— 按**名字直查**，图放进覆盖目录即可，
        # 不写库（库里没有这条，`DeckImages.resolve` 的文件名直查会命中它）。
        base = _named_base(deck_name)
        dst, note = convert(image_path, base)
        print('[换图] %s（自组卡组名，不在 meta 表；按名字直查）-> %s'
              % (deck_name, fmd._rel_or_abs(dst)))
        print('       %s' % note)
        return 0
    base = os.path.join(fmd.IMG_CUSTOM_DIR, fmd.safe_filename(row['name']))
    os.makedirs(fmd.IMG_CUSTOM_DIR, exist_ok=True)
    dst, note = convert(image_path, base)
    rel = fmd._rel_or_abs(dst)
    st.set_meta_deck_override(row['name'], img_file=rel)
    print('[换图] %s（%s）-> %s' % (row['name'], row.get('name_cn') or '-', rel))
    print('       %s' % note)
    return 0


def cmd_reset(st, deck_name):
    row, err = find_deck(st, deck_name)
    if err:
        print('[错误] %s' % err)
        return 2
    if row is None:
        base = _named_base(deck_name)
        removed = []
        for ext in ('.webp', '.png', '.jpg', '.jpeg'):
            p = base + ext
            if os.path.exists(p):
                os.remove(p)
                removed.append(os.path.basename(p))
        print('[还原] %s（自组卡组名）%s -> 默认图'
              % (deck_name, ('（已删 %s）' % '、'.join(removed)) if removed else '（本来就没图）'))
        return 0
    base = os.path.join(fmd.IMG_CUSTOM_DIR, fmd.safe_filename(row['name']))
    removed = []
    for ext in ('.webp', '.png', '.jpg', '.jpeg'):
        p = base + ext
        if os.path.exists(p):
            os.remove(p)
            removed.append(os.path.basename(p))
    back = fmd.local_image_for(row['name'])       # 覆盖图没了，回落到站内图
    #: ⚠️ 回落成**空串**而不是 None（2026-10-10 修的真事故）：`set_meta_deck_override`
    #: 约定 None = 「不动这一项」，空串 = 「清空」。库里若存着历史污染的
    #: `_default.webp` 假图，不清掉就永远显示默认图；清空后 `DeckImages.resolve`
    #: 会重新走真图链。`local_image_for` 现在找不到真图就诚实返回 None。
    st.set_meta_deck_override(row['name'], img_file=(back or ''))
    print('[还原] %s -> %s%s' % (row['name'], back or '（空，界面回落到默认图）',
                               ('（已删 %s）' % '、'.join(removed)) if removed else ''))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='换掉/还原某个卡组的卡图')
    ap.add_argument('deck', nargs='?',
                    help='卡组名（中英文都行，支持模糊匹配）；'
                         '游戏内自组卡组名也能用（按名字直查，不写库）')
    ap.add_argument('image', nargs='?', help='新图片路径')
    ap.add_argument('--list', action='store_true', help='列出所有卡组当前用的图')
    ap.add_argument('--reset', action='store_true', help='还原成站内图')
    ap.add_argument('--from-site', metavar='站内卡组名',
                    help='不用本地图，直接取站内这个卡组的卡图'
                         '（如 --from-site "Power Patron"）')
    args = ap.parse_args(argv)

    st = Storage()
    st.init_schema()
    if args.list or not args.deck:
        cmd_list(st)
        return 0
    if args.reset:
        return cmd_reset(st, args.deck)

    image = args.image
    tmp = None
    if args.from_site:
        try:
            tmp, n = fetch_site_image(args.from_site)
        except Exception as exc:                   # noqa: BLE001
            print('[错误] 从站内取图失败（%s）：%s —— 站内图只覆盖榜上卡组，'
                  '自组混轴名本身会 404' % (args.from_site, exc))
            return 2
        print('[取图] 站内「%s」-> %d 字节' % (args.from_site, n))
        image = tmp
    if not image:
        print('[错误] 要给图片路径、或用 --reset / --from-site')
        return 2
    try:
        return cmd_set(st, args.deck, image)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


if __name__ == '__main__':
    sys.exit(main())
