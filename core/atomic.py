# -*- coding: utf-8 -*-
"""原子写文件 —— **全项目唯一的落盘方式**（2026-10-18）。

## 为什么需要

审查（`docs/BUG审查/00-总览.md` §6）查出全项目 39 处写文件，**只有 2 处**用了
`os.replace`；其余都是 `open(path, 'w'/'wb')` 直接截断真源。写一半崩溃/磁盘满/
进程被杀 → 真源只剩 0 字节或半截内容。

最危险的是 `data/deck_name_cn.csv`（用户逐条查证填的 125 行覆盖表，不可再生）
与 `data/rank_state.json`（段位推断链的状态，写坏会显示错段位）。

## 用法

```python
from core import atomic

# 文本（自动 UTF-8，不含 BOM —— 调用方要 BOM 就自己加）
atomic.write_text(path, text, encoding='utf-8', newline='')

# 字节
atomic.write_bytes(path, data)
```

失败时**原文件保持原样**（要么全成功、要么不变），并返回 False。
成功返回 True。
"""

from __future__ import annotations

import os

#: 临时文件后缀（与 `core/log_tailer.py:142` 的既有写法一致，便于人工识别残留）
TMP_SUFFIX = '.tmp'


def write_bytes(path: str, data: bytes) -> bool:
    """原子写字节。成功 True；失败 False（**原文件不变**）。

    ⚠️ **不自动建父目录** —— 与本函数要替换的那些 `open(path,'w')` 保持同一
    语义：目录不存在就是失败，调用方该自己保证目录已建好（`paths.ensure_data_dirs()`
    或 `os.makedirs`）。自动建目录会把「路径配错了」这类错误悄悄变成「写成功了」。
    """
    tmp = str(path) + TMP_SUFFIX
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        # 清掉可能残留的临时文件，别让它污染目录
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def write_text(path: str, text: str, encoding: str = 'utf-8',
               newline: str | None = None) -> bool:
    """原子写文本。参数语义同 `open()`。

    :param newline: 传 `''` 可禁止 Windows 把 `\\n` 翻译成 `\\r\\n`
        （本项目对行尾敏感：铁律 3 要求仓库文件不带 CRLF）。
    """
    try:
        b = text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return write_bytes(path, b)


def replace(path: str, data: bytes) -> bool:
    """:func:`write_bytes` 的别名（语义更直白：整体替换一个文件）。"""
    return write_bytes(path, data)
