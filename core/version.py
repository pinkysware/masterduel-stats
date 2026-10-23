# -*- coding: utf-8 -*-
r"""版本号唯一真源（2026-10-18 新增，为「补丁包 + 精简包」的打包流程准备）。

## 为什么要有这个文件

版本号原先只存在于**三处、全靠人抄**：发布仓 README 的下载表、git tag、
GitHub Release。更糟的是 `build/build_installer.py` 的 ``default_version()``
用正则从 README 里 grep 版本号 —— 意味着「README 里写错一个数字」= 「打出来的包
名字错、控制面板里版本错」。

现在版本号只有一个真源：本文件的 :data:`__version__`。
四个打包脚本（Setup / full / patch / lite）都读它；README 下载表与 tag 由发版
流程照抄（见 `docs/当前进度.md` 的发版清单）。

⚠️ **本文件有两份**：主仓一份（开发用）、发布仓一份（**打进包的那份**）。
打包脚本读**发布仓**那份 —— 打进包的版本必须与产物名一致。
两边不一致时打包脚本会直接报错退出（不会默默打出 version 与 README 不符的包）。

## 比较规则

语义化三段 ``X.Y.Z``，**逐段比整数**。不能按字符串比 ——
``'1.0.10' < '1.0.9'`` 在字符串比较下为真（``'1' < '9'``），整数比较才正确。
"""
from __future__ import annotations

#: 当前版本。**发版时只改这一行**（改完记得同步到发布仓，否则打包脚本会拦下）。
__version__ = '1.1.3'

#: 语义化三段的默认值（解不出来时用 0 兜底，绝不抛）
_ZERO = (0, 0, 0)


def parse(v) -> tuple:
    """``'1.0.10'`` -> ``(1, 0, 10)``；解不出来 -> ``(0, 0, 0)``（**不抛**）。

    容忍的输入：``None`` / 空串 / 带 ``v`` 前缀（GitHub tag 就是 ``v1.0.8``）/
    只有两段（``1.0``）/ 带 garbage 后缀（``1.0.8-beta``）。
    """
    if not v:
        return _ZERO
    text = str(v).strip()
    if text[:1] in ('v', 'V'):
        text = text[1:]
    head = text.split('-', 1)[0].split('+', 1)[0]
    parts = head.split('.')
    out = []
    for i in range(3):
        chunk = parts[i].strip() if i < len(parts) else ''
        #: 只取前导数字（'10abc' -> 10）；一个数字都没有就记 0
        digits = ''
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def compare(a, b) -> int:
    """比两个版本号：``-1``（a 旧）/ ``0``（同版本）/ ``1``（a 新）。"""
    ta, tb = parse(a), parse(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def is_newer(latest) -> bool:
    """``latest`` 比**当前版本**新吗（给「检查更新」用；现在没接更新功能）。"""
    return compare(latest, __version__) > 0
