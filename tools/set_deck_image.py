# -*- coding: utf-8 -*-
"""换掉某个卡组的卡图。

图统一放 ``data/deck_images_custom/``（**覆盖目录**）—— 它优先于站内下载的
``data/deck_images/``，而且站内月度重抓**不会**把它盖回去（见 AGENTS.md 铁律 7）。

用法::

    python tools/set_deck_image.py --list                    # 看所有卡组 + 当前用哪张图
    python tools/set_deck_image.py 烙印 D:\\图\\xx.png         # 换成这张（卡组名支持模糊匹配）
    python tools/set_deck_image.py 烙印 --reset               # 撤掉自换的图，回到站内图

图片格式：装了 Pillow 就自动转成 **200x200 webp**（和其他卡图统一）；
没装就原样拷贝（尺寸不变，界面会自己缩放）。
"""

import argparse
import os
import shutil
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import fetch_meta_decks as fmd          # noqa: E402
from core.storage import Storage        # noqa: E402

TARGET_SIZE = 200


def find_deck(st, name):
    """模糊找卡组，返回唯一的 Storage 行；找不到/多个就返回 (None, 说明)。"""
    hits = st.search_meta_decks(name, limit=5)
    exact = [h for h in hits if h['name'] == name or h.get('name_cn') == name]
    if len(exact) == 1:
        return exact[0], None
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, '找不到卡组：%s' % name
    return None, '匹配到多个，写清楚点：%s' % '、'.join(
        '%s(%s)' % (h['name'], h.get('name_cn') or '-') for h in hits)


def convert(src, dst_base):
    """把 src 存成覆盖目录里的 <dst_base>.*，返回产出文件路径。

    有 Pillow 就统一成 200x200 webp；没有就原样拷贝（保留原扩展名）。
    """
    try:
        from PIL import Image
    except ImportError:
        ext = os.path.splitext(src)[1].lower() or '.webp'
        dst = dst_base + ext
        shutil.copyfile(src, dst)
        return dst, '原样拷贝（没装 Pillow，尺寸未统一；用内置 Python 跑可自动转 200x200 webp）'
    im = Image.open(src).convert('RGBA')
    src_w, src_h = im.size
    side = min(im.size)
    l, t = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((l, t, l + side, t + side)).resize(
        (TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    dst = dst_base + '.webp'
    im.save(dst, 'WEBP', quality=92, method=6)
    return dst, '%dx%d -> %dx%d webp' % (src_w, src_h, TARGET_SIZE, TARGET_SIZE)


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


def cmd_set(st, deck_name, image_path):
    if not os.path.exists(image_path):
        print('[错误] 图片不存在：%s' % image_path)
        return 2
    row, err = find_deck(st, deck_name)
    if err:
        print('[错误] %s' % err)
        return 2
    base = os.path.join(fmd.IMG_CUSTOM_DIR, fmd.safe_filename(row['name']))
    os.makedirs(fmd.IMG_CUSTOM_DIR, exist_ok=True)
    dst, note = convert(image_path, base)
    rel = fmd._rel_or_abs(dst)
    # 换图会留下别的扩展名的旧文件 —— 那个会先被 local_image_for 命中，得清掉
    for ext in ('.webp', '.png', '.jpg', '.jpeg'):
        other = base + ext
        if os.path.normcase(other) != os.path.normcase(dst) and os.path.exists(other):
            os.remove(other)
            print('   清掉同名旧图 %s' % os.path.basename(other))
    st.set_meta_deck_override(row['name'], img_file=rel)
    print('[换图] %s（%s）-> %s' % (row['name'], row.get('name_cn') or '-', rel))
    print('       %s' % note)
    return 0


def cmd_reset(st, deck_name):
    row, err = find_deck(st, deck_name)
    if err:
        print('[错误] %s' % err)
        return 2
    base = os.path.join(fmd.IMG_CUSTOM_DIR, fmd.safe_filename(row['name']))
    removed = []
    for ext in ('.webp', '.png', '.jpg', '.jpeg'):
        p = base + ext
        if os.path.exists(p):
            os.remove(p)
            removed.append(os.path.basename(p))
    back = fmd.local_image_for(row['name'])       # 覆盖图没了，自动回落到站内图/默认图
    st.set_meta_deck_override(row['name'], img_file=back)
    print('[还原] %s -> %s%s' % (row['name'], back,
                               ('（已删 %s）' % '、'.join(removed)) if removed else ''))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='换掉/还原某个卡组的卡图')
    ap.add_argument('deck', nargs='?', help='卡组名（中英文都行，支持模糊匹配）')
    ap.add_argument('image', nargs='?', help='新图片路径')
    ap.add_argument('--list', action='store_true', help='列出所有卡组当前用的图')
    ap.add_argument('--reset', action='store_true', help='还原成站内图')
    args = ap.parse_args(argv)

    st = Storage()
    st.init_schema()
    if args.list or not args.deck:
        cmd_list(st)
        return 0
    if args.reset:
        return cmd_reset(st, args.deck)
    if not args.image:
        print('[错误] 要给图片路径，或用 --reset')
        return 2
    return cmd_set(st, args.deck, args.image)


if __name__ == '__main__':
    sys.exit(main())
