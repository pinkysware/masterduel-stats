# -*- coding: utf-8 -*-
"""生成 `data/card_names.json` —— 卡名三语表（中文 / 英文 / 日文）。

数据来源（**合成时不联网**）：

* `ygocdb.com.cards/cards.json`（项目根目录的快照，13.7MB，**不入库**）：
  `cid` / `cn_name` / `sc_name` / `md_name` / `en_name` / `jp_name` / `jp_ruby` /
  `cnocg_n` / `nwbbs_n`
  ⚠️ **连接键是 `cid`（= 游戏内卡 id，如 20500），不是 `id`（Konami 密码）**
  上游地址：`https://ygocdb.com/api/v0/cards.zip`（zip 里就是这份 cards.json）；
  另有 `…/cards.zip.md5` 公布**包内 cards.json** 的 MD5 —— 官方口径是「先比 MD5、
  只有变了才重新下载」，`--check-update` / `--update` 就照这个来。
* `data/card_data.json`：**游戏客户端资源**（14298 张，只有中文名）。它**没有
  生成脚本**（历来人工提取，见 `docs/交接说明.md`）—— 「ygocdb 也没有」的那类
  新卡只能靠它，所以 `--check-update` 把这类判成 **B 类**，别让人白跑 build。

用法::

    python tools/build_card_names.py               # 生成 data/card_names.json
    python tools/build_card_names.py --stats       # 只打印覆盖率，不写文件
    python tools/build_card_names.py --check       # 看现有文件覆盖多少、缺哪些
    python tools/build_card_names.py --check-update  # 联网比对上游 + 给 unknown 分类
    python tools/build_card_names.py --update      # 下载上游 -> 刷新快照 -> 重跑 build
    python tools/build_card_names.py --check-update --ids 22747,3002   # 指定 id 分类

`--check-update` 的 unknown 清单从哪来：归档里每份 `duel.json` 的
`summary.unknown_card_ids`（对局里解出来、卡表却查不到的卡 id，见 M4）。
分类之后**按类给处置**：A 类跑 `--update` 就好；B 类重跑一百次也不会变。
"""

import argparse
import hashlib
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import atomic
from core import paths

YGOCDB = os.path.join(PROJ, 'ygocdb.com.cards', 'cards.json')
CARD_DATA = os.path.join(PROJ, 'data', 'card_data.json')
OUT = os.path.join(PROJ, 'data', 'card_names.json')

#: 中文名的兜底顺序（`card_data.json` 用完之后，从 ygocdb 的这几列挑）
CN_FIELDS = ('md_name', 'sc_name', 'cn_name', 'cnocg_n', 'nwbbs_n')

#: 上游（百鸽 ygocdb）—— API 文档：https://ygocdb.com/api
#: `cards.zip` 里只有一个 `cards.json`；`cards.zip.md5` 公布的是**包内 cards.json**
#: 的 MD5（不是 zip 自己的；2026-09-13 实测两者不同）。
BASE = 'https://ygocdb.com/api/v0'
URL_ZIP = BASE + '/cards.zip'
URL_MD5 = BASE + '/cards.zip.md5'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

#: 归档对局记录（`--check-update` 从这里汇总 unknown 卡 id）
RECORDS_DIR = paths.data_path('archive', 'duel_records')


def read_ygocdb(path=YGOCDB):
    """ygocdb 卡表 -> {游戏内卡 id: 那条记录}。查不到返回 {}。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = {}
    for rec in (raw.values() if isinstance(raw, dict) else raw):
        cid = rec.get('cid')
        if cid is None:
            continue
        try:
            out[int(cid)] = rec
        except (TypeError, ValueError):
            continue
    return out


def read_card_data(path=CARD_DATA):
    """游戏卡表 -> {卡 id: 中文名}。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        rows = json.load(f)
    return {int(r['id']): r.get('name') or '' for r in rows if r.get('id') is not None}


def build(ygocdb=None, card_data=None):
    """合成 ``(names, stats)``；names = ``{卡id: [中文, 英文, 日文]}``。"""
    ygocdb = read_ygocdb() if ygocdb is None else ygocdb
    card_data = read_card_data() if card_data is None else card_data
    names = {}
    covered = 0
    for cid in sorted(set(ygocdb) | set(card_data)):
        rec = ygocdb.get(cid) or {}
        cn = (card_data.get(cid) or '').strip()
        if not cn:
            for field in CN_FIELDS:
                cn = (rec.get(field) or '').strip()
                if cn:
                    break
        en = (rec.get('en_name') or '').strip()
        jp = (rec.get('jp_name') or rec.get('jp_ruby') or '').strip()
        if cn or en or jp:
            names[cid] = [cn, en, jp]
            if cid in card_data:
                covered += 1
    stats = {
        '游戏卡表': len(card_data),
        'ygocdb 卡表': len(ygocdb),
        '两边都有': len(set(ygocdb) & set(card_data)),
        '产出条数': len(names),
        '游戏卡表命中': covered,
        '有中文': sum(1 for v in names.values() if v[0]),
        '有英文': sum(1 for v in names.values() if v[1]),
        '有日文': sum(1 for v in names.values() if v[2]),
    }
    return names, stats


def _now_iso():
    """本地时间 ISO 8601（秒精度）—— 写进 `generated_at`，方便对齐上游版本。"""
    return datetime.now().isoformat(timespec='seconds')


def dump(names, path=OUT, generated_at=None):
    """写卡名表。`generated_at` = 生成时刻（ISO），用来对齐上游版本 / 分辨新旧。"""
    payload = {'v': 1, 'langs': list(('cn', 'en', 'jp')),
               'generated_at': generated_at or _now_iso(),
               'counts': {'total': len(names),
                          'cn': sum(1 for v in names.values() if v[0]),
                          'en': sum(1 for v in names.values() if v[1]),
                          'jp': sum(1 for v in names.values() if v[2])},
               'names': {str(k): v for k, v in sorted(names.items())}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    #: ★ BUG-B9A-11：**原子写**。这份 14610 张卡的产出一旦写坏，
    #: `core.card_names.load()` 的 json.load 抛异常 → 全站卡名变 `#id`。
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if not atomic.write_text(path, text):
        raise SystemExit('写卡名表失败：%s（原文件未改动）' % path)
    return path


def show_stats(stats, src_missing=()):
    for k, v in stats.items():
        print('  %-10s %s' % (k, v))
    total = stats.get('产出条数') or 1
    if stats.get('产出条数'):
        for lang, key in (('中文', '有中文'), ('英文', '有英文'), ('日文', '有日文')):
            print('  %s覆盖：%d / %d（%.1f%%）'
                  % (lang, stats[key], total, 100.0 * stats[key] / total))
        if stats.get('游戏卡表'):
            print('  游戏卡表（%d 张）里命中：%d（%.1f%%）'
                  % (stats['游戏卡表'], stats['游戏卡表命中'],
                     100.0 * stats['游戏卡表命中'] / stats['游戏卡表']))
    for name in src_missing:
        print('  ⚠️ 缺少数据文件：%s' % name)


# ---------------------------------------------------------------- 上游检测 / 一键更新（M4）

def md5_bytes(data):
    return hashlib.md5(data).hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


#: 退避睡的觉（单测打桩成 no-op，别真睡）
_SLEEP = time.sleep


def fetch_bytes(url, retries=3, timeout=60, fetcher=None):
    """GET 取原始字节。失败重试 ``retries`` 次（2/4 秒退避），最终抛 ``RuntimeError``。

    ``fetcher`` 只给单测注入（默认走 urllib）；**注入的也走同一套重试/降级**，
    这样「网络不行时一个字都不写」才测得出来。范式照 ``tools/fetch_banlist.py``。
    """
    last = None
    for i in range(retries):
        try:
            if fetcher is not None:
                return fetcher(url)
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                          # noqa: BLE001
            last = e
            if i + 1 < retries:
                _SLEEP(2 * (i + 1))
    raise RuntimeError('抓取失败 %s：%s' % (url, last))


def fetch_upstream_md5(fetcher=None):
    """上游公布的**包内 cards.json** 的 MD5（很小的请求，先探它再决定下不下载）。"""
    raw = fetch_bytes(URL_MD5, timeout=30, fetcher=fetcher)
    return raw.decode('utf-8', 'replace').strip().strip('"').strip().lower()


def fetch_upstream_cards(fetcher=None):
    """下载 `cards.zip` 并解出 cards.json -> ``(原始字节, md5)``。"""
    blob = fetch_bytes(URL_ZIP, timeout=180, fetcher=fetcher)
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            data = z.read('cards.json')
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise RuntimeError('上游包结构变了（期望 zip 里有个 cards.json）：%r' % e)
    return data, md5_bytes(data)


def read_upstream_cids(raw):
    """上游卡表 -> ``{游戏内卡 id}``。**键是 `cid`，不是 `id`（Konami 密码）**。"""
    cids = set()
    for rec in (raw.values() if isinstance(raw, dict) else raw) or []:
        if not isinstance(rec, dict):
            continue
        cid = rec.get('cid')
        if cid is None:
            continue
        try:
            cids.add(int(cid))
        except (TypeError, ValueError):
            continue
    return cids


def as_ints(value):
    """``'22747, 3002'`` / ``[22747]`` -> ``[22747, 3002]``（排序去重，不吃垃圾）。"""
    if isinstance(value, (str, bytes)):
        parts = str(value).replace('，', ',').split(',')
    else:
        parts = list(value or [])
    out = set()
    for p in parts:
        try:
            out.add(int(str(p).strip()))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def classify_unknown(unknown_ids, upstream_cids):
    """unknown 清单按「上游 ygocdb 有没有」分类 -> ``{'A': [...], 'B': [...]}``。

    * **A 类**（上游已收录）：跑 `--update` 刷新快照 + 重跑 build 即可解决；
    * **B 类**（上游也没有）：缺的是「只有游戏卡表才有」的那一宇宙
      （活动 / 推广卡、MD 专属异画 id，如 `22747`）—— **重跑 build 一百次也不会变**，
      得重出 `data/card_data.json`（人工）。

    ⚠️ 别把两类混成一句「重跑 build」：对 B 类那是白忙（M4 审查 P2-1）。
    """
    up = set(upstream_cids)
    ids = as_ints(unknown_ids)
    return {'A': [i for i in ids if i in up], 'B': [i for i in ids if i not in up]}


def collect_unknown_from_records(records_dir=RECORDS_DIR):
    """归档 `duel.json` 的 `summary.unknown_card_ids` 汇总（排序去重）。"""
    ids = set()
    if not os.path.isdir(records_dir):
        return []
    for name in sorted(os.listdir(records_dir)):
        path = os.path.join(records_dir, name, 'duel.json')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                duel = json.load(f)
        except (OSError, ValueError):
            continue
        ids.update(as_ints((duel.get('summary') or {}).get('unknown_card_ids')))
    return sorted(ids)


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _fmt_ids(ids, limit=8):
    shown = '、'.join(str(i) for i in ids[:limit])
    return shown + (' …等共 %d 个' % len(ids) if len(ids) > limit else '')


def check_update(ids=(), records_dir=RECORDS_DIR, ygocdb_path=YGOCDB, fetcher=None):
    """联网比对上游 + 给 unknown 清单分类。**只读，不写任何文件。**

    先比 MD5（官方建议）：一致就用本地快照分类、**不必下载 13.7MB**；
    不一致才下载 zip，并**校验下载到的 md5 == 上游公布的 md5**（防半截包）。
    """
    unknown = as_ints(list(collect_unknown_from_records(records_dir)) + as_ints(ids))
    local_md5 = md5_file(ygocdb_path) if os.path.exists(ygocdb_path) else None
    up_md5 = fetch_upstream_md5(fetcher)            # 失败 -> 异常，调用方不写文件
    fresh = bool(local_md5) and local_md5.lower() == up_md5
    if fresh:
        up_cids = read_upstream_cids(_load_json(ygocdb_path))
    else:
        data, got = fetch_upstream_cards(fetcher)
        if got.lower() != up_md5:
            raise RuntimeError('下载到的 cards.json md5 与上游公布不一致（%s != %s）'
                               % (got, up_md5))
        up_cids = read_upstream_cids(json.loads(data.decode('utf-8')))
    cls = classify_unknown(unknown, up_cids)
    return {'local_md5': local_md5, 'upstream_md5': up_md5, 'fresh': fresh,
            'upstream_cards': len(up_cids), 'unknown': unknown,
            'A': cls['A'], 'B': cls['B']}


def update(ygocdb_path=YGOCDB, out=OUT, fetcher=None):
    """下载上游 -> 刷新本地快照 -> 重跑 build（一键）。

    * 上游 md5 与本地一致就**跳过下载**（官方建议），但仍重跑 build 保证一致；
    * 写快照先落 `.tmp` 再 `os.replace`（不留半份快照）；
    * **根目录的 `ygocdb.com.cards.zip` 备份不动** —— 它只是历史打包，
      build 读的是解包后的 `ygocdb.com.cards/cards.json`（docs/M4计划.md §七.4）；
    * 网络 / 结构问题会抛异常，**此时一个字都还没写**。
    """
    local_md5 = md5_file(ygocdb_path) if os.path.exists(ygocdb_path) else None
    up_md5 = fetch_upstream_md5(fetcher)
    refreshed = False
    if not local_md5 or local_md5.lower() != up_md5:
        data, got = fetch_upstream_cards(fetcher)
        if got.lower() != up_md5:
            raise RuntimeError('下载到的 cards.json md5 与上游公布不一致（%s != %s）'
                               % (got, up_md5))
        tmp = ygocdb_path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, ygocdb_path)
        refreshed = True
    names, stats = build(read_ygocdb(ygocdb_path), read_card_data())
    dump(names, out)
    return {'refreshed': refreshed, 'local_md5_before': local_md5,
            'upstream_md5': up_md5, 'stats': stats}


def cmd_check_update(args):
    """`--check-update`：只读比对 + 分类，按类给处置文案（M4 审查 P2-1）。"""
    try:
        rep = check_update(as_ints(args.ids), records_dir=args.records_dir,
                           ygocdb_path=args.ygocdb, fetcher=args.fetcher)
    except RuntimeError as e:
        print('⚠️ 联网比对失败：%s' % e)
        print('   （没有写任何文件；网络恢复后再跑，或先跑本地 build）')
        return 2
    print('[上游] cards.json md5：%s' % rep['upstream_md5'])
    print('       本地快照 md5：%s%s'
          % (rep['local_md5'] or '（文件不在）',
             '（一致 → 快照就是最新的，无需下载）' if rep['fresh']
             else '（不一致 → 跑 --update 就能刷新快照）'))
    print('[上游卡数] %d 张' % rep['upstream_cards'])
    if not rep['unknown']:
        print('[未知卡 id] 0 个 —— 卡表覆盖良好（归档 duel.json 里没有查不到的 id）')
        return 0
    print('[未知卡 id] %d 个（归档 duel.json 汇总 + --ids）：%s'
          % (len(rep['unknown']), _fmt_ids(rep['unknown'])))
    if rep['A']:
        print('  A 类 %d 个（上游已收录 → 跑 `--update` 即可解决）：%s'
              % (len(rep['A']), _fmt_ids(rep['A'])))
    if rep['B']:
        print('  B 类 %d 个（**上游也没有** → 重跑 build 无用，需重出游戏卡表 '
              'data/card_data.json：人工，游戏更新后重新提取，见 docs/交接说明.md，'
              '之后重跑 build）：%s' % (len(rep['B']), _fmt_ids(rep['B'])))
    return 1 if rep['B'] else 0


def cmd_update(args):
    """`--update`：一键刷新快照 + 重跑 build。"""
    try:
        rep = update(ygocdb_path=args.ygocdb, out=args.out, fetcher=args.fetcher)
    except (RuntimeError, OSError) as e:
        print('⚠️ 更新失败（没有改动任何文件）：%s' % e)
        return 2
    if rep['refreshed']:
        print('[快照] 已刷新 %s（%.1f MB）：%s -> %s'
              % (os.path.relpath(args.ygocdb, PROJ),
                 os.path.getsize(args.ygocdb) / 1024.0 / 1024.0,
                 rep['local_md5_before'] or '（新建）', rep['upstream_md5']))
    else:
        print('[快照] 本地已是最新（md5 与上游一致），跳过下载：%s' % rep['upstream_md5'])
    print('[合成] %s' % os.path.relpath(args.out, PROJ))
    show_stats(rep['stats'])
    print('[写入] %s（%.2f MB）'
          % (os.path.relpath(args.out, PROJ),
             os.path.getsize(args.out) / 1024.0 / 1024.0))
    print('提示：根目录 ygocdb.com.cards.zip 是历史打包，按惯例不动'
          '（build 读的是解包后的 ygocdb.com.cards/cards.json）。')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成卡名三语表 data/card_names.json')
    ap.add_argument('--stats', action='store_true', help='只打印，不写文件')
    ap.add_argument('--check', action='store_true', help='看现有文件的情况')
    ap.add_argument('--check-update', action='store_true', dest='check_update',
                    help='联网比对上游 ygocdb（MD5）+ 把 unknown 卡 id 分成 A/B 两类'
                         '（只读，不写文件）')
    ap.add_argument('--update', action='store_true',
                    help='下载上游 cards.zip -> 刷新 ygocdb 快照 -> 重跑 build（一键）')
    ap.add_argument('--ids', default='',
                    help='--check-update 额外分类这些卡 id（逗号分隔；'
                         '默认还会汇总归档 duel.json 里的 unknown）')
    ap.add_argument('--records-dir', default=RECORDS_DIR,
                    help='--check-update 读哪个归档目录（默认 data/archive/duel_records）')
    ap.add_argument('--ygocdb', default=YGOCDB,
                    help='ygocdb 快照路径（默认 ygocdb.com.cards/cards.json）')
    ap.add_argument('--out', default=OUT, help='输出路径（默认 data/card_names.json）')
    args = ap.parse_args(argv)
    args.fetcher = None             # 单测注入点（默认走 urllib）

    if args.check_update:
        return cmd_check_update(args)
    if args.update:
        return cmd_update(args)

    if args.check:
        from core.card_names import LANGS, load
        table = load(args.out)
        print('现有文件：%s' % os.path.relpath(args.out, PROJ))
        print('  %d 张卡；中文 %d / 英文 %d / 日文 %d'
              % (len(table), sum(1 for v in table.values() if v[0]),
                 sum(1 for v in table.values() if v[1]),
                 sum(1 for v in table.values() if v[2])))
        missing = [k for k, v in table.items() if not all(v)]
        print('  三语不全的：%d 张%s' % (len(missing), ('，例如 ' + str(missing[:5]))
                                       if missing else ''))
        return 0

    missing_src = []
    if not os.path.exists(YGOCDB):
        missing_src.append(os.path.relpath(YGOCDB, PROJ))
    if not os.path.exists(CARD_DATA):
        missing_src.append(os.path.relpath(CARD_DATA, PROJ))
    names, stats = build()
    print('[合成] %s' % os.path.relpath(OUT, PROJ))
    show_stats(stats, missing_src)
    if args.stats:
        print('[--stats] 未写文件')
        return 0 if names else 1
    dump(names, args.out)
    print('[写入] %s（%.2f MB）'
          % (os.path.relpath(args.out, PROJ),
             os.path.getsize(args.out) / 1024.0 / 1024.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
