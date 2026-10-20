# -*- coding: utf-8 -*-
"""capture_end_message —— 结算画面：抓 `DuelEndMessage` 的文字（M1 胜负线）。

★ M6 起本工具**只保留 one-shot 抓样本**（研究用）；常驻采集由
`tools/duel_collector.py` 接管（`--watch` 移除）。读取逻辑
（`EndMessageReader` / `make_record` / 残渣过滤 / 文案签名）已搬进
`core/memread/end_message.py` —— **两处共用一份，别复制第二份**。

用户口径（2026-09-12）：「只用记录胜负就行」——只要 Win / Lose 一个比特。
★ 唯一判据 = **胜负状态位**（WinIcon 原生 +0x46，2026-09-13 拍板）；
文案是备注（result_note）。详见 `core/memread/end_message.py` 的 docstring。

红线
----
* 只读、只申请 0x0410 权限、不注入（`core/memread/` 保证，单测盯着）
* 读的是**结算画面显示给玩家看的文字**（公开信息）

用法
----
    # 打完一局、结算画面别退，然后：
    python tools/capture_end_message.py --result lose --label 我投降
    python tools/capture_end_message.py --list         # 已攒样本 + 文案表
    python tools/capture_end_message.py --dry-run      # 只打印，不落盘

输出：``data/archive/end_messages.jsonl``（append-only，一行一局，已 gitignore）

⚠️ 结算画面上 `DuelEndMessage` 实例只活几分钟，**回主菜单就没了** ——
看到 `DEFEAT` / `VICTORY` 大字时**先别点确认**。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime
from typing import List

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import paths

from core.memread.end_message import (                      # noqa: E402
    TEXT_FIELDS, EndMessageReader, default_game_dir, make_record)


#: 采样记录落盘位置（append-only）
OUT_PATH = paths.data_path('archive', 'end_messages.jsonl')

#: Untapped 日志目录（`Game ended: result=Win/Lose` 那行在这里）
LOG_DIR = os.path.join(os.environ.get('APPDATA')
                       or os.path.expanduser('~'),
                       'untapped-companion')

#: 日志里的结束行：`Game ended: result=Win deckId=48171157`
GAME_ENDED_RE = re.compile(r'Game ended:\s*result=(Win|Lose)\s+deckId=([^,\s]+)')


def _game_dir() -> str:
    """游戏目录（`MD_GAME_DIR` 环境变量可覆盖）。"""
    return default_game_dir()


# ---------------------------------------------------------------- 纯逻辑（好单测）


def analyze_samples(samples: List[dict]) -> dict:
    """把攒到的样本归纳成「文案 → 胜负」表，并判定 `m_text` 能不能当判据。

    判据成立的条件（两条都要满足）：

    1. **同一个文案不能对应两种胜负**；
    2. **胜负两侧用到的文案必须互不重叠** —— 且至少各有 1 个样本。

    ⚠️ 2026-09-13 起文案判据**退役**（状态位为唯一判据），本函数保留：
    `--list` 的报表 + 历史样本研究用。
    """
    rows = {}
    unlabeled = 0
    for rec in samples:
        text = (rec.get('texts') or {}).get('messageText') or ''
        result = (rec.get('result') or '').strip().lower() or None
        if result not in ('win', 'lose'):
            result = None
            unlabeled += 1
        key = text or '(空)'
        row = rows.setdefault(key, {'text': key, 'results': [], 'labels': [],
                                    'count': 0})
        row['count'] += 1
        if result:
            row['results'].append(result)
        label = (rec.get('label') or '').strip()
        if label and label not in row['labels']:
            row['labels'].append(label)

    out_rows = [rows[k] for k in sorted(rows)]
    for row in out_rows:
        row['results'] = sorted(set(row['results']))

    # --- 判定 ---
    win_texts, lose_texts = set(), set()
    conflicting = []
    for row in out_rows:
        if len(row['results']) > 1:
            conflicting.append(row['text'])
        if row['results'] == ['win']:
            win_texts.add(row['text'])
        elif row['results'] == ['lose']:
            lose_texts.add(row['text'])

    if unlabeled == len(samples) and samples:
        ok, verdict = None, ('样本都没带 --result，判定不了 —— '
                             '抓的时候带上 `--result win|lose`')
    elif conflicting:
        ok, verdict = False, ('❌ **文案不是判据**：%s 同时出现在胜负两侧'
                              % '、'.join(conflicting))
    elif not win_texts or not lose_texts:
        ok = None
        have, need = ('输', '赢') if lose_texts else ('赢', '输')
        verdict = ('⚠️ 还差样本：目前只有【%s】侧，需要再打一局【%s】的'
                   '（对方投降 / 正常打死对面 / 正常被打死 …）' % (have, need))
    else:
        ok, verdict = True, ('✅ **文案是判据**：赢 = %s / 输 = %s，两侧无重叠'
                             % ('、'.join(sorted(win_texts)),
                                '、'.join(sorted(lose_texts))))
    return {'rows': out_rows, 'verdict': verdict, 'ok': ok,
            'unlabeled': unlabeled, 'total': len(samples)}


def render_report(samples: List[dict]) -> str:
    """人类可读的样本 + 文案表（`--list` 的输出）。"""
    if not samples:
        return ('还没有样本。\n'
                '打完一局**别退结算画面**，然后跑：\n'
                '  python tools/capture_end_message.py --result win|lose '
                '--label 结局描述')
    rep = analyze_samples(samples)
    lines = ['已攒样本 %d 条 ——' % rep['total'], '']
    for i, rec in enumerate(samples, 1):
        t = rec.get('texts') or {}
        lines.append('  [%d] %s  %-12s %-14s messageText=%r  %s / %s'
                     % (i, rec.get('captured_at', '?'),
                        '<%s%s>' % (rec.get('result') or '未标',
                                     '*' if rec.get('result_src')
                                     == 'log' else ''),
                        rec.get('label') or '',
                        t.get('messageText', ''),
                        t.get('player0Name', ''), t.get('player1Name', '')))
    lines += ['', '文案表（m_text -> 结局）：', '']
    for row in rep['rows']:
        lines.append('  %-14r ×%-3d %s   %s'
                     % (row['text'], row['count'],
                        ('<%s>' % '/'.join(row['results'])) if row['results']
                        else '<未标胜负>',
                        '、'.join(row['labels'])))
    # 旧样本可能没有 mem_result 字段 —— 现算一份，别让一致率漏数它们
    def _mem(r):
        return r.get('mem_result') if r.get('mem_result') in ('win', 'lose') \
            else None
    both = [r for r in samples
            if r.get('result') in ('win', 'lose')
            and _mem(r) in ('win', 'lose')]
    if both:
        agree = sum(1 for r in both if r['result'] == _mem(r))
        lines += ['', '内存判据 vs 日志：一致 %d/%d%s'
                  % (agree, len(both),
                     '' if agree == len(both)
                     else '   ⚠️ 有不一致 —— 判据要复核')]
    if any(r.get('result_src') == 'log' for r in samples):
        lines += ['', '（标了 * 的胜负是**从日志推的**，不是人填的）']
    lines += ['', rep['verdict']]
    return '\n'.join(lines)


# ---------------------------------------------------------------- 落盘


def append_record(rec, path=OUT_PATH):
    """追加一条记录（**显式用 \\n**，别让 Windows 写成 CRLF）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'a', encoding='utf-8', newline='') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def load_records(path=OUT_PATH) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with io.open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# ------------------------------------------------- 从日志自动判定胜负（可选）

def latest_log_result(paths=None, max_bytes=262144):
    """从 Untapped 日志尾部读**最近一次对局结束**的胜负。

    ⚠️ `deckId=null` 的那行是**观战/回放** —— result 不代表「我」的胜负，
    **返回 None**，别拿来标样本。

    :return: ``'win'`` / ``'lose'`` / ``None``
    """
    if paths is None:
        paths = [os.path.join(LOG_DIR, 'log.old.log'),      # 先老后新
                 os.path.join(LOG_DIR, 'log.log')]          # 最后一个命中才是最近的
    got = None
    for path in paths:
        try:
            size = os.path.getsize(path)
            with io.open(path, 'rb') as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                blob = f.read().decode('utf-8', 'replace')
        except OSError:
            continue
        for m in GAME_ENDED_RE.finditer(blob):
            got = (m.group(1).lower(), m.group(2))
    if got is None:
        return None
    result, deck_id = got
    if deck_id.strip().lower() == 'null':   # 观战/回放（`deckId=null, ...`）
        return None
    return result


def resolve_result(cli_result=None, auto=True):
    """命令行给的优先；没给就从日志尾部推。

    :return: ``(result, source)``，`source` 是 ``'cli'`` / ``'log'`` / ``None``
    """
    if cli_result:
        return cli_result.lower(), 'cli'
    if not auto:
        return None, None
    got = latest_log_result()
    return (got, 'log') if got else (None, None)


# ---------------------------------------------------------------- CLI


def _print_texts(texts, extra):
    if not texts:
        print('❌ 没读到结算文字 —— 可能的情况：')
        print('   · 不在结算画面上（实例已销毁 / 已经点确认回主菜单了）')
        print('   · 游戏没开')
        return False
    print('结算画面文字（实例 %s / pid %s）：' % (extra.get('instance', '?'),
                                                extra.get('pid', '?')))
    for name in TEXT_FIELDS:
        print('   %-16s = %r' % (name, texts.get(name)))
    return True


def cmd_once(args):
    reader = EndMessageReader(game_dir=_game_dir())
    try:
        if not reader.connect():
            print('❌ 连不上游戏（没开 / 找不到 %s）' % _game_dir())
            return 2
        texts, extra = reader.read_texts()
        if not _print_texts(texts, extra):
            return 1
    finally:
        reader.close()
    result, src = resolve_result(args.result, auto=not args.no_auto)
    rec = make_record(texts, extra, result, args.label,
                      result_src=src)
    if args.dry_run:
        print('\n[dry-run] 不落盘。本来要写：')
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    append_record(rec, args.out)
    print('\n✅ 已记录到 %s' % args.out)
    if src == 'log':
        print('（胜负从日志尾部自动判定：%s）' % result)
    elif not result:
        print('⚠️ 没拿到胜负（命令行没给、日志里也推不出）—— '
              '文案表里它会是「未标胜负」，判定时用不上。')
    samples = load_records(args.out)
    print('\n' + render_report(samples))
    return 0


def cmd_list(args):
    print(render_report(load_records(args.out)))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        description='结算画面抓 DuelEndMessage 文字（one-shot 抓样本；'
                    '常驻采集已归 tools/duel_collector.py）')
    ap.add_argument('--result', choices=['win', 'lose'],
                    help='这局的胜负（你本人视角）—— 判定文案表的关键')
    ap.add_argument('--label', help='结局描述，如「我投降」「对方投降」「正常打死对面」')
    ap.add_argument('--list', action='store_true', help='看已攒样本 + 文案表 + 判据结论')
    ap.add_argument('--no-auto', action='store_true',
                    help='不给 --result 时别从日志自动判胜负')
    ap.add_argument('--dry-run', action='store_true', help='只打印，不落盘')
    ap.add_argument('--out', default=OUT_PATH, help='记录文件（默认 %s）' % OUT_PATH)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.list:
        return cmd_list(args)
    return cmd_once(args)


if __name__ == '__main__':
    sys.exit(main())
