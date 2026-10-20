# -*- coding: utf-8 -*-
r"""安装版 / 便携版共用的**路径解析**（2026-10-18 新增，为一键安装包做准备）。

## 为什么要这一层

原先把「程序目录」当成一切：代码、卡表、数据库、卡绘缓存、对局记录全在同一个
`PROJ` 下。便携包（解压即用）没问题，但**装到 `Program Files` 会直接失效** ——
普通用户对那里没有写权限：

* 采集器写 `data/masterduel.db` → `sqlite3.OperationalError: unable to open database file`
* 卡绘缓存、对局记录目录同理

所以拆成两个概念：

| 概念 | 谁 | 可写 | 安装版位置 |
|---|---|---|---|
| :data:`ASSET_DIR` | 程序自带的**只读素材**（卡表 / 图标 / 字体 / 字体回退图） | ❌ 只读 | 安装目录（Program Files 也行） |
| :data:`DATA_DIR` | 所有**会被写入**的东西（数据库 / 记录 / 缓存 / 状态） | ✅ | `%LOCALAPPDATA%\\MasterDuelStats` |

## 怎么切换（安装版）

安装包（NSIS）在启动器里设环境变量 :data:`ENV_DATA_DIR`：

```bat
set MD_DATA_DIR=%LOCALAPPDATA%\MasterDuelStats
```

不给这个变量时（便携版 / 开发 / 单测）:data:`DATA_DIR` **就是** `PROJ/data`
—— 与旧行为**逐字节一致**，所以现有测试和便携包都无感。

## 只读素材也可以是「用户覆盖」

`mdm_deck_cards.json`（参照库）与 `site_deck_names.json` 既有随包附带的基线、
又会被「刷新」按钮改写。它们归 **DATA_DIR 优先、缺失时回落到 ASSET_DIR** ——
见 :func:`resolve`。
"""
from __future__ import annotations

import os

#: 程序根目录（代码 + 随包素材）。**只读假设**：安装版可能在 Program Files。
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 环境变量：安装版用它把可写数据指到 `%LOCALAPPDATA%`。
ENV_DATA_DIR = 'MD_DATA_DIR'

#: 随包素材根（只读）：`<PROJ>/data`
ASSET_DIR = os.path.join(PROJ, 'data')

#: 用户在**设置页**里指定的数据目录 —— 一个极简文本文件（**一行路径**）。
#: 为什么不用 QSettings：`paths` 要在 Qt 起来之前就解析（启动脚本、采集器、
#: CLI 工具都要），而 QSettings 依赖 Qt/平台后端。纯文本谁都能读。
#: 放在 **PROJ 根**（不是 data/）：它是「数据放哪」的元信息，不能跟着数据搬。
CONFIG_NAME = 'paths.ini'


def _config_path() -> str:
    return os.path.join(PROJ, CONFIG_NAME)


def read_pref() -> str:
    """读「设置页指定的数据目录」；没有/读不出返回空串。"""
    try:
        with open(_config_path(), 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line
    except OSError:
        pass
    return ''


def write_pref(path: str) -> bool:
    """写「数据目录偏好」（空串 = 清除，回到自动策略）。返回是否写成功。"""
    try:
        if not path:
            if os.path.exists(_config_path()):
                os.remove(_config_path())
            return True
        with open(_config_path(), 'w', encoding='utf-8', newline='') as f:
            f.write('# Master Duel Stats data location (one path per line).\n')
            f.write('# Delete this file to go back to automatic.\n')
            f.write(path.rstrip('\\/') + '\n')
        return True
    except OSError:
        return False


def _remove_probe(path: str) -> None:
    """删探针文件 —— 先判存在再删（别人已经删掉就不算失败）。

    ★ BUG-B2A-17 的另一半：原来直接 `os.remove()`，文件被并发进程删掉时
    抛 `OSError`，被外层 `except` 吃掉后返回 **False**（可写目录被判不可写）。
    """
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _writable(d: str) -> bool:
    """目录可写吗 —— **纯探测，不留副作用**（不建目录、不留文件）。

    ⚠️ 早先的实现「不存在就 `makedirs` 再返回 True」有副作用：判断一个
    **本该只读**的位置（如 Program Files）时会把目录建出来，随后又因写不进去
    才失败 —— 既留垃圾又绕远路（2026-10-18 实测踩到）。
    现在只做两件事：
      1. 目录存在 -> 真写一个探针文件再删掉；
      2. 目录不存在 -> 看**最近的已存在祖先**能不能写（能不能建出来由它决定）。
    """
    d = os.path.abspath(d)
    #: ★ BUG-B2A-17：探针文件名**带 pid** —— 采集器 + UI + CLI 同时启动时
    #:   各自探测同一目录，固定名 `.md_write_probe` 会互相盲删：
    #:   A 建好、B 删掉、A 再删就抛 OSError（被下面吞掉）→ **可写目录被判成
    #:   不可写**，数据被赶到 %LOCALAPPDATA%。带 pid 后各删各的。
    probe_name = '.md_write_probe_%d' % os.getpid()
    try:
        if os.path.isdir(d):
            probe = os.path.join(d, probe_name)
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('x')
            _remove_probe(probe)
            return True
        # 往上找第一个已存在的祖先
        parent = os.path.dirname(d)
        while parent and not os.path.isdir(parent):
            nxt = os.path.dirname(parent)
            if nxt == parent:
                return False
            parent = nxt
        if not os.path.isdir(parent):
            return False
        probe = os.path.join(parent, probe_name)
        try:
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('x')
            _remove_probe(probe)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _default_data_dir() -> str:
    """**没设环境变量**时的数据根（用户 2026-10-18 口径）：

    1. 设置页指定过 -> 用它；
    2. 否则**程序目录可写**就用 `<PROJ>/data`（便携包 / 装在普通磁盘目录 ——
       用户希望数据跟程序在一起，整个文件夹拷走就能换机器）；
    3. 不可写（装在 `Program Files`）-> `%LOCALAPPDATA%` 下的 `MasterDuelStats`。

    这样「装到 Program Files」不会坏，而「装到普通目录」又能保持便携。
    """
    pref = read_pref()
    if pref:
        return pref
    if _writable(ASSET_DIR):
        return ASSET_DIR
    local = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if local:
        return os.path.join(local, 'MasterDuelStats')
    return ASSET_DIR          # 兜底：实在判不出就按老的来（至少不会崩）


def _env_data_dir() -> str:
    return (os.environ.get(ENV_DATA_DIR) or '').strip()


#: 可写数据根。**模块导入时定一次**（与旧代码 `PROJ` 的用法一致）——
#: 测试要换目录请设环境变量后 `importlib.reload`，或直接用 :func:`data_path`。
DATA_DIR = _env_data_dir() or _default_data_dir()


def data_path(*parts: str) -> str:
    """可写数据路径：``data_path('archive', 'duel_records')``。"""
    return os.path.join(DATA_DIR, *parts)


def asset_path(*parts: str) -> str:
    """随包只读素材路径：``asset_path('rank_icons')``。"""
    return os.path.join(ASSET_DIR, *parts)


def resolve(*parts: str) -> str:
    """**先找可写目录、再回落随包素材**。

    给「既有基线、又会被改写」的文件用（参照库 / 站内卡组名单）：
    用户改过的那份在 DATA_DIR 里，就用它；没有就用随包基线（只读也能读）。
    """
    p = data_path(*parts)
    if os.path.exists(p):
        return p
    return asset_path(*parts)


def ensure_data_dirs() -> list:
    """首次启动建好可写目录（安装版全新机器上 DATA_DIR 还不存在）。

    返回**新建**的目录列表（已有就不列）。只建必需的那几个，
    其余（如 `card_art`）在首次下载时按需创建。
    """
    made = []
    for rel in ((), ('archive',), ('archive', 'duel_records')):
        p = data_path(*rel) if rel else DATA_DIR
        if not os.path.isdir(p):
            try:
                os.makedirs(p, exist_ok=True)
                made.append(p)
            except OSError:
                pass
    return made


def is_portable() -> bool:
    """是否便携模式（可写目录 == 随包素材目录）。"""
    return os.path.normcase(DATA_DIR) == os.path.normcase(ASSET_DIR)


def describe() -> str:
    """一行诊断（日志 / 首跑写盘用）。"""
    return ('mode=%s\n  ASSET_DIR=%s\n  DATA_DIR=%s'
            % ('portable' if is_portable() else 'installed',
               ASSET_DIR, DATA_DIR))
