# -*- coding: utf-8 -*-
"""幂等文档补丁 —— 把「MARK 不在正文里」这个坑从纪律变成机制。

**为什么有这个文件**：2026-09-13 / 09-14 两晚各犯一次同样的错 ——
手写幂等脚本时 MARK 写成了正文里**不存在**的片段：

* `MARK='M7 计划已写好'`，正文标题却是 `## M7 悬浮窗 UI 计划（…）`；
* `MARK='M7 视觉规格已定（参考图驱动）'`，正文却是 `…（参考图驱动，2026-09-14 凌晨）`。

判重**永不命中** → 本机命令跑两次 → 同一个块被追加两份，事后还要手工去重。
两次都是「MARK 不是被追加文本的子串」，所以这里把判据改成**从文本自身推导**：

    marker = 传入的 marker，或 text 的第一个非空行

并在写入前 `assert marker in text`（不成立直接抛错，而不是静默写两份），
写完再自检份数。

CLI::

    # 把 tmp/block.md 的内容幂等追加到 docs/当前进度.md
    python tools/doc_patch.py --file docs/当前进度.md --append tmp/block.md

    # 只是数一份某个标记出现几次
    python tools/doc_patch.py --file docs/当前进度.md --count "M7 视觉规格"

    # 已经写重复了？塌成一份
    python tools/doc_patch.py --file docs/当前进度.md --dedup "> 📐 **M7 视觉规格"
"""
from __future__ import annotations

import argparse
import io
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_text(path: str) -> str:
    with io.open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    """写回时必须 newline='' —— 否则 Windows 会把 \\n 变 \\r\\n（项目铁律）。"""
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def derive_marker(text: str, marker: str = None, exclude: str = None) -> str:
    """推导判重标记 —— **保证它一定是 text 的子串**（手写 MARK 最容易写错）。

    ``exclude`` 是给 ``replace_once`` 用的：``new`` 常写成 ``old + 补充``，
    这时若取第一行，取到的是**文件里已经有的旧内容** → 判重立刻命中 →
    永远返回 skip、什么都没替换。所以要排除 ``exclude`` 里已有的行，
    取**第一个新出现的行**。
    """
    if marker:
        if marker not in text:
            raise ValueError(
                'marker 不在待写入文本里（这就是重复追加的根因）：%r' % marker)
        return marker
    skip = exclude or ''
    for line in text.split('\n'):
        s = line.strip()
        if s and s not in skip:
            return s
    raise ValueError('文本里没有「新的」非空行，无法推导 marker')


def count(path: str, marker: str) -> int:
    """marker 在文件里出现的行数（按行数，不按子串次数）。"""
    if not os.path.exists(path):
        return 0
    return sum(1 for ln in read_text(path).split('\n') if marker in ln)


def append_once(path: str, text: str, marker: str = None) -> str:
    """幂等追加。返回 ``'appended'`` / ``'skip'``。

    marker 已存在则跳过；否则追加，并在写完后自检份数（应为 1）。
    """
    mk = derive_marker(text, marker)
    if count(path, mk) > 0:
        return 'skip'
    old = read_text(path) if os.path.exists(path) else ''
    if old and not old.endswith('\n'):
        old += '\n'
    write_text(path, old + text)
    n = count(path, mk)
    if n != 1:
        raise RuntimeError('追加后 %r 出现 %d 次（应为 1）—— 疑似 marker 撞车' % (mk, n))
    return 'appended'


def replace_once(path: str, old: str, new: str, marker: str = None) -> str:
    """幂等替换：**``new`` 整段已存在于文件里就不动**，否则替换第一处 ``old``。

    判重依据是 ``new`` 本身，**不是** marker —— 因为 marker 只要在文件**任何位置**
    出现过就会误判成"已写过"。实例（2026-09-14 连踩三次）：「获取新卡组名」那段
    先在 §二 目标里插入，同一串片段就成了 §4.5 那次替换的 marker，
    于是 §4.5 永远被判 skip、实际没写进去。

    ``marker`` 只用于「MARK 写错就抛错」的校验（见 :func:`derive_marker`），
    默认从 ``new`` 推导（``exclude=old`` 保证取到**新出现**的行）。

    注意 ``old`` 必须是文件里**当前存在**的片段；``new`` 以 ``old`` 开头（常见写法）
    不影响本判据 —— 判的是 ``new``。
    """
    derive_marker(new, marker, exclude=old)
    cur = read_text(path)
    if new in cur:
        return 'skip'
    if old not in cur:
        raise ValueError('old 锚点不在文件里，无法替换')
    write_text(path, cur.replace(old, new, 1))
    return 'replaced'
def dedup(path: str, marker: str) -> int:
    """把**连续重复**的同名块塌成一份（用于事后收拾已重复的写入）。

    块边界 = marker 行到下一个 marker 行之前；连续两份时删掉第二份。
    返回删除的块数。
    """
    lines = read_text(path).split('\n')
    removed = 0
    while True:
        idx = [i for i, ln in enumerate(lines) if marker in ln]
        if len(idx) < 2:
            break
        i1, i2 = idx[0], idx[1]
        span = i2 - i1
        del lines[i2:i2 + span]
        removed += 1
    if removed:
        write_text(path, '\n'.join(lines))
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='幂等文档补丁')
    ap.add_argument('--file', required=True, help='目标 md（相对项目根或绝对路径）')
    ap.add_argument('--append', help='要追加的文本文件')
    ap.add_argument('--marker', help='判重标记（默认取追加文本的第一个非空行）')
    ap.add_argument('--count', help='数这个标记出现几行')
    ap.add_argument('--dedup', help='把连续重复的块塌成一份')
    args = ap.parse_args(argv)

    path = args.file
    if not os.path.isabs(path):
        path = os.path.join(PROJ, path)

    if args.count:
        print('count=%d' % count(path, args.count))
        return 0
    if args.dedup:
        print('removed=%d' % dedup(path, args.dedup))
        return 0
    if args.append:
        text = read_text(args.append)
        print(append_once(path, text, args.marker))
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
