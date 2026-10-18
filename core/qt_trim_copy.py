# -*- coding: utf-8 -*-
r"""按白名单精简复制 PySide6（2026-10-18）。

## 为什么要抽出来

三个打包脚本（Setup exe / full zip / 补丁包）都要做同一件事：把本机
site-packages 里的 PySide6 按 :mod:`core.qt_trim` 的白名单复制进产物。
抄三份必然漂移（本项目已有先例：`build_full_zip3.py` 与
`build_installer.py` 的 `JUNK` 名单就已经不完全一致）。

## 用法

```python
from core.qt_trim_copy import copy_pyside6
copied = copy_pyside6(src_pyside, dst_pyside)   # -> 复制了多少个文件
```

## 三条纪律

1. **先删后拷**：目标目录里若有退役的大件（升级场景），先删掉再拷。
   ⚠️ 顺序反了会删掉刚拷进去的新文件。
2. **白名单只管 PySide6**：`shiboken6` / `pypinyin` 很小（3MB / 3.7MB），
   整个复制即可，别为省这点钱引入风险。
3. **数文件、算体积**：返回复制数量，调用方打印出来（对账用）。
"""
from __future__ import annotations

import os
import shutil

from core import qt_trim


def _rm(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def copy_pyside6(src_dir: str, dst_dir: str) -> int:
    """按白名单把 ``src_dir``（本机 PySide6）复制成 ``dst_dir``。

    返回**复制了多少个文件**（调用方可打印对账）。
    源目录不存在直接抛 ``SystemExit`` —— 打包时缺依赖必须响亮失败，不能静默跳过。
    """
    if not os.path.isdir(src_dir):
        raise SystemExit('找不到 PySide6 源目录: %s' % src_dir)

    # 1) 先删目标里的退役大件（升级/重打场景）
    for p in qt_trim.retired_paths(dst_dir):
        _rm(p)

    # 2) 按白名单拷
    n = 0
    for rel in qt_trim.manifest(src_dir):
        s = os.path.join(src_dir, rel.replace('/', os.sep))
        d = os.path.join(dst_dir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        n += 1
    return n


def pyside6_size(dst_dir: str) -> float:
    """目标 PySide6 目录的体积（MB）—— 给打包脚本打印对账用。"""
    total = 0
    for root, _dirs, files in os.walk(dst_dir):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576.0
