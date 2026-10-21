# -*- coding: utf-8 -*-
"""从**卡组截图**生成自定义参照库（`data/mdm_deck_cards_custom.json`）。

## 它解决什么

参照库 `mdm_deck_cards.json` 的键是**站内英文名**，游戏里自组的卡组名
（如「狱神耀圣」）不在其中 —— 反推候选里永远不会出现它。
本工具识别该卡组名的截图，生成它**自己的** `cards` 结构，之后反推就能判到它。

## 输入 / 输出

    data/deck_images_custom/<卡组名>/*.jpg      ← 用户放截图（目录名 = 卡组名）
            ↓ 本工具（离线，YOLO 切块 + pHash）
    data/mdm_deck_cards_custom.json
        {"<卡组名>": {"cards": {"<卡id>": {per, at, aboveThresh}},
                      "total": N, "samples": N, "provisional": bool,
                      "built_at": "..."}}

**为什么单独一个文件**：`mdm_deck_cards.json` 是月度快照，`fetch_mdm_decks.py --all`
会整表覆盖；自定义条目写进去会被冲掉（AGENTS 铁律 7「自定义条目不被月度清理」）。

## ★ 核心：只取「卡组区」

游戏内卡组编辑界面的截图里，**右侧那一大块是「卡片列表」（收藏箱）**，
不是这副牌 —— 整图识别会把收藏的凡骨全算进去（实测 F231_1 多了 30 张垃圾）。
卡组区与收集箱之间有一道**≥6% 图宽**的空白，实测稳定落在 67%~69%（5/5 张图）：
取框中心 x 的最大间隙当切点，左侧 = 主牌组 40 + 额外牌组 15。

判不出间隙就**整图返回**（宁可多检，不误删）。

## 闸门（`per` 有没有意义全靠它）

| 判据 | 值 | 依据 |
|---|---|---|
| 识别张数 | 40–60 | 真实卡组 = 40 主 + 15 额外（±5 容差，允许识别漏几张） |
| 重复率 `len/len(set)` | ≥1.4 | 参照库 111 副真实卡表实测：中位 **1.52**、最低 1.23。<br>⚠️ 交接文档写的 **2.0 是错的** —— 那会让 109/111 副判成噪声 |

不达标 → **整张图丢弃**，绝不拿噪声建库。

## 用法

    python tools/build_custom_ref.py --list                 # 看哪些卡组名有截图、几个样本
    python tools/build_custom_ref.py --deck 狱神耀圣         # 建/重建一个
    python tools/build_custom_ref.py --all                  # 全建
    python tools/build_custom_ref.py --deck 狱神耀圣 --dry-run   # 只打印，不落盘
    python tools/build_custom_ref.py --forget 狱神耀圣       # 从自定义库删掉

⚠️ **识别链需要 numpy / onnxruntime / Pillow**（主项目 `requirements.txt` 里**没有**）。
缺了就明确报错并给出装法，**不要**静默降级 —— 静默降级会让用户以为建好了。
"""
from __future__ import annotations

import argparse
import datetime
import glob
import io
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import paths                                        # noqa: E402

#: 自定义参照库落点 —— **必须走 paths**（安装版 `PROJ/data` 只读，见 core/paths.py）
CUSTOM_REF = paths.data_path('mdm_deck_cards_custom.json')

#: 截图目录（目录名 = 卡组名）
SHOT_DIR = paths.data_path('deck_images_custom')

#: 识别链所在（GetDeck 的模型与哈希库；不在本仓）
RECOG_DIR = os.environ.get('MD_RECOG_DIR') or \
    r'E:\LMStudio\Claude\playground\tieba_scrape'

#: 闸门（见模块头）
MIN_IDS, MAX_IDS = 40, 62
#:
#: **重复率下限 = 1.25**（2026-10-21 用**全量真实数据**标定，不是拍的）：
#: 10 月大师一帖 188 层 / 545 张图跑下来，识别出 ≥30 张唯一卡的 52 张图里，
#: 重复率 min 1.13 / **P10 1.28** / 中位 1.40 / P90 1.58 / max 1.65。
#:
#: | 阈值 | 通过率 |
#: |---|---|
#: | 1.20 | 98% |
#: | **1.25** | **96%** |
#: | 1.30 | 83% |
#: | 1.40 | 54%（原来的值，**砍掉近一半真实卡组**） |
#:
#: ⚠️ 交接文档写的 **2.0** 会让 52/52 全灭 —— 那个数字对应的是「满编三连」的
#: 极端构筑，不是常态。1.25 之下基本只剩「几乎无重复」的噪声（反例：10 个 id
#: 去重后 9 个，比值 1.11）。
MIN_DUP_RATIO = 1.25

#: 哈希匹配距离上限。
#:
#: ⚠️ 48 是 `recognize_deck.py` 的原有默认值，但**对原图偏紧**：实测 5 张真实
#: 卡组截图（2046x1151 原图），48 只识别出 36~47 张、每张图都差 3~7 张才够 40；
#: 放到 **56** 后每张 39~49 张，且**准确率一点没降**（与 Elfnote 卡表重合
#: 77%~100% 不变，中位距离实测 29~43）。
#: 再往上（64/72）只是多检同一批卡的错位框，唯一卡数不再涨（56→57）。
MAX_DIST = 56

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


# ---------------------------------------------------------------- 依赖
def _require_recog():
    """导入识别链；缺依赖时抛**能看懂**的错（不静默降级）。"""
    missing = []
    for mod in ('numpy', 'onnxruntime', 'PIL'):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise RuntimeError(
            '识别链缺依赖：%s\n'
            '  pip install numpy onnxruntime pillow\n'
            '（这些**不在** requirements.txt 里 —— 主程序不需要它们，'
            '只有建自定义参照库才用）' % ', '.join(missing))
    if not os.path.isdir(RECOG_DIR):
        raise RuntimeError('找不到识别链目录：%s\n'
                           '设环境变量 MD_RECOG_DIR 指向 tieba_scrape' % RECOG_DIR)
    if RECOG_DIR not in sys.path:
        sys.path.insert(0, RECOG_DIR)
    from recognize_deck import Recognizer, gradient_hash, crop_artwork   # noqa: F401
    return Recognizer, gradient_hash, crop_artwork


# ---------------------------------------------------------------- 卡组区切分
def deck_region(boxes, width, min_gap_frac=0.06, min_left_frac=0.30):
    """把 YOLO 框按「卡组区 / 卡片列表面板」切开。

    返回 ``(left_boxes, cut_x, gap_px)``；判不出间隙时 ``(boxes, None, 0)``
    （= 整图都是卡组区，宁可多检不误删）。

    判据：框中心 x 的最大间隙，要求
      * ``gap >= width * min_gap_frac``（实测 7.1%~7.4%，留 6% 余量）
      * 间隙左端 ``>= width * min_left_frac``（不能把左边全砍掉）
      * 两侧都有实质内容（``>=10`` / ``>=5`` 个框）

    ⚠️ **不猜固定比例**：实测切点在 67%~69% 有波动（图宽不同），
    按间隙算比按比例稳。
    """
    if len(boxes) < 15:
        return boxes, None, 0
    xs = sorted((b[0] + b[2]) / 2 for b in boxes)
    best = None
    for i in range(len(xs) - 1):
        gap = xs[i + 1] - xs[i]
        left_n, right_n = i + 1, len(xs) - (i + 1)
        if (gap >= width * min_gap_frac and xs[i] >= width * min_left_frac
                and left_n >= 10 and right_n >= 5):
            if best is None or gap > best[0]:
                best = (gap, xs[i], xs[i + 1])
    if best is None:
        return boxes, None, 0
    gap, lx, rx = best
    cut = (lx + rx) / 2
    return [b for b in boxes if (b[0] + b[2]) / 2 < cut], cut, gap


# ---------------------------------------------------------------- 单图识别
def recognise_one(path, rec, gradient_hash, crop_artwork):
    """一张截图 → ``(卡 id 列表, 诊断 dict)``。

    诊断字段：``w/h/n_frames/cut_x/gap_px/n_ids/n_uniq/dup_ratio``。
    """
    from PIL import Image
    im = Image.open(path).convert('RGB')
    boxes = rec.detect(im)
    left, cut, gap = deck_region(boxes, im.size[0])
    ids = []
    for b in left:
        card = im.crop((round(max(0, b[0])), round(max(0, b[1])),
                        round(min(im.size[0], b[2])), round(min(im.size[1], b[3]))))
        if card.width < 8 or card.height < 8:
            continue
        m = rec.db.match(gradient_hash(crop_artwork(card)), topk=1)
        if m and m[0][2] <= MAX_DIST:
            ids.append(int(m[0][0]))
    uniq = len(set(ids))
    return ids, {
        'file': os.path.basename(path),
        'w': im.size[0], 'h': im.size[1],
        'n_frames': len(boxes), 'n_left': len(left),
        'cut_x': round(cut) if cut else None,
        'gap_px': round(gap) if gap else None,
        'n_ids': len(ids), 'n_uniq': uniq,
        'dup_ratio': round(len(ids) / uniq, 2) if uniq else 0.0,
    }


def gate_ok(diag):
    """这张截图够不够格进库？返回 ``(ok, reason)``。"""
    n, ratio = diag['n_ids'], diag['dup_ratio']
    if n < MIN_IDS:
        return False, '识别 %d 张 < %d' % (n, MIN_IDS)
    if n > MAX_IDS:
        return False, '识别 %d 张 > %d（可能是收藏界面/拼图）' % (n, MAX_IDS)
    if ratio < MIN_DUP_RATIO:
        return False, '重复率 %.2f < %.2f' % (ratio, MIN_DUP_RATIO)
    return True, ''


# ---------------------------------------------------------------- 聚合
def aggregate(sample_ids, name=None):
    """多张截图的卡 id 列表 → 自定义库的条目 dict。

    * ``per`` = 有百分之几的**样本**带这张卡（与站内库同口径）；
    * ``at`` = 出现率的众数（最多出现几次），没有就 ``None``；
    * ``aboveThresh`` = ``per >= 60``（与站内库同一个阈值）；
    * **少于 5 副样本**：``per`` 二值化成 100/0，``provisional=True``
      （1~4 副算不出有意义的百分比，但二值化对打分仍然有用 ——
      per=100 的卡表在 bayes 上等价于「这些卡我一定带」）；
    * **≥5 副**：``per`` 才计算真实百分比，``provisional=False``。
    """
    samples = [list(s) for s in sample_ids if s]
    n = len(samples)
    if not n:
        return None
    presence, copies = {}, {}
    for s in samples:
        cnt = {}
        for cid in s:
            cnt[cid] = cnt.get(cid, 0) + 1
        for cid, k in cnt.items():
            presence[cid] = presence.get(cid, 0) + 1
            copies.setdefault(cid, []).append(k)
    provisional = n < 5
    cards = {}
    for cid, seen in presence.items():
        if provisional:
            per = 100                       # 出现过的都是「一定有」
        else:
            per = int(round(100.0 * seen / n))
        if per <= 0:
            continue
        ks = sorted(copies.get(cid) or [])
        at = ks[len(ks) // 2] if ks else None
        cards[str(cid)] = {'per': per, 'at': at, 'aboveThresh': per >= 60}
    out = {'cards': cards, 'total': n, 'samples': n,
           'provisional': provisional,
           'built_at': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}
    if name:
        out['name_cn'] = name
    return out


# ---------------------------------------------------------------- 存取
def load_custom(path=None):
    """读自定义库 -> ``{卡组名: 条目}``（**decks 段**）；没有/坏了返回 ``{}``。

    WARNING: 顶层结构与 `fetch_mdm_decks.py` **同构**
    （`version / source / fetched_at / skipped / decks`），所以这里取
    ``['decks']``，不是整个 JSON。也兼容早期「顶层直接是名字表」的旧文件。
    """
    path = path or CUSTOM_REF
    try:
        with io.open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    decks = data.get('decks')
    return decks if isinstance(decks, dict) else data


def save_custom(decks, path=None):
    """写自定义库（原子替换；**顶层结构与站内参照库同构**）。

    ``decks`` = ``{卡组名: 条目}`` —— 本函数负责包上
    `version / source / fetched_at / skipped` 四个顶层键。
    """
    path = path or CUSTOM_REF
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    payload = {
        'version': 2,
        'source': 'deck-screenshot / YOLO+pHash',
        'fetched_at': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'skipped': [],
        'decks': decks,
    }
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write(chr(10))
    os.replace(tmp, path)
    return path

# ---------------------------------------------------------------- 目录扫描
def list_deck_dirs():
    """``{卡组名: [图片路径...]}`` —— ``deck_images_custom/<名字>/`` 下的子目录。

    ⚠️ 只认**子目录**：`deck_images_custom/` 平铺的 `<名字>.webp` 是**卡图**，
    不是截图，别混进来。
    """
    out = {}
    if not os.path.isdir(SHOT_DIR):
        return out
    for entry in sorted(os.listdir(SHOT_DIR)):
        p = os.path.join(SHOT_DIR, entry)
        if not os.path.isdir(p):
            continue
        imgs = []
        for ext in IMAGE_EXTS:
            imgs.extend(glob.glob(os.path.join(p, '*' + ext)))
            imgs.extend(glob.glob(os.path.join(p, '*' + ext.upper())))
        imgs = sorted(set(imgs))
        if imgs:
            out[entry] = imgs
    return out


# ---------------------------------------------------------------- 主流程
def build(name, dry_run=False, verbose=True):
    """建一个卡组名的自定义参照库条目。返回 ``(条目 dict | None, 报告 list)``。"""
    Recognizer, gradient_hash, crop_artwork = _require_recog()
    dirs = list_deck_dirs()
    shots = dirs.get(name) or []
    report = []
    if not shots:
        return None, ['%s：没有截图（放 %s）' % (name, os.path.join(SHOT_DIR, name))]

    rec = Recognizer()
    report.append('%s：%d 张截图，哈希库 %d 张' % (name, len(shots), len(rec.db)))
    kept = []
    for p in shots:
        try:
            ids, diag = recognise_one(p, rec, gradient_hash, crop_artwork)
        except Exception as exc:                             # noqa: BLE001
            report.append('  %-16s 识别失败：%s: %s'
                          % (os.path.basename(p), type(exc).__name__, exc))
            continue
        ok, why = gate_ok(diag)
        report.append('  %-16s 卡组区 %-3d/%d 张 唯一 %-3d 重复 %.2f  切点 %s   %s'
                      % (os.path.basename(p), diag['n_left'], diag['n_frames'],
                         diag['n_ids'], diag['dup_ratio'],
                         ('%d%%' % (100 * diag['cut_x'] // diag['w']))
                         if diag['cut_x'] else '整图',
                         '✓' if ok else ('✗ ' + why)))
        if ok:
            kept.append(ids)

    if not kept:
        report.append('  → **没有一张过闸门**，不建库（宁可没有，也不能拿噪声建）')
        return None, report

    deck = aggregate(kept, name=name)
    report.append('  → 用 %d/%d 张 → %d 张卡，样本 %d 副，%s'
                  % (len(kept), len(shots), len(deck['cards']), deck['samples'],
                     'provisional（<5 副，per 二值化）' if deck['provisional']
                     else 'per = 真实百分比'))
    if not dry_run:
        data = load_custom()
        data[name] = deck
        save_custom(data)
        report.append('  → 已写入 %s' % CUSTOM_REF)
    return deck, report


def main(argv=None):
    ap = argparse.ArgumentParser(description='卡组截图 → 自定义参照库')
    ap.add_argument('--list', action='store_true', help='看哪些卡组名有截图')
    ap.add_argument('--deck', help='只建这一个卡组名')
    ap.add_argument('--all', action='store_true', help='全建')
    ap.add_argument('--forget', help='从自定义库删掉这个卡组名')
    ap.add_argument('--dry-run', action='store_true', help='只打印，不落盘')
    args = ap.parse_args(argv)

    if args.forget:
        data = load_custom()
        if args.forget in data:
            del data[args.forget]
            if args.dry_run:
                print('[dry-run] 会删掉 %s' % args.forget)
            else:
                save_custom(data)
                print('已删掉 %s（剩下 %d 条）' % (args.forget, len(data)))
        else:
            print('自定义库里没有 %s' % args.forget)
        return 0

    if args.list:
        dirs = list_deck_dirs()
        have = load_custom()
        if not dirs:
            print('没有截图目录。放成：%s/<卡组名>/*.jpg' % SHOT_DIR)
            return 0
        print('截图目录 %s：' % SHOT_DIR)
        for name, imgs in dirs.items():
            d = have.get(name) or {}
            mark = ('库里已有 %d 张卡 / %d 副' % (len(d.get('cards') or {}),
                                               d.get('samples') or 0)) if d else '库内暂无'
            print('  %-20s %2d 张截图   %s' % (name, len(imgs), mark))
        return 0

    targets = []
    if args.deck:
        targets = [args.deck]
    elif args.all:
        targets = list(list_deck_dirs())
    if not targets:
        print('要 --deck <名字> / --all / --list（见 --help）')
        return 2

    rc = 0
    for name in targets:
        print('\n=== %s ===' % name)
        try:
            deck, report = build(name, dry_run=args.dry_run)
        except RuntimeError as exc:
            print(str(exc))
            return 3
        for line in report:
            print(line)
        if deck is None:
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
