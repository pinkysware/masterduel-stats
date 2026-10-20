# -*- coding: utf-8 -*-
"""红线定义与静态扫描 —— 「只读内存」这条约束的可执行形式。

为什么要单独一个模块
--------------------
「不修改 GameAssembly.dll、不注入 DLL」如果只写在文档里，迟早会被后来的改动
悄悄破坏。这里把红线变成**常量 + 扫描函数**，由 ``tests/test_memread_guard.py``
每次跑单测时强制执行：谁在业务代码里写了 ``WriteProcessMemory``，测试立刻红。

豁免文件
--------
``redline.py`` 自身和 ``tests/test_memread_guard.py`` 必须出现这些字符串
（否则没法定义和断言），所以扫描时豁免这两个文件名。
**除此之外的任何文件命中即违规。**
"""
from __future__ import annotations

import io
import os
from typing import List, Tuple

# ---------------------------------------------------------------- 1. 禁止的 API

#: 能写内存 / 远程执行 / 注入的 Win32 与 NT API。命中任何一个都是违规。
FORBIDDEN_APIS: Tuple[str, ...] = (
    # 写内存
    'WriteProcessMemory',
    'NtWriteVirtualMemory',
    'ZwWriteVirtualMemory',
    # 在目标进程里分配 / 改属性
    'VirtualAllocEx',
    'VirtualFreeEx',
    'VirtualProtectEx',
    'NtAllocateVirtualMemory',
    'NtProtectVirtualMemory',
    # 远程执行（注入的载体）
    'CreateRemoteThread',
    'CreateRemoteThreadEx',
    'NtCreateThreadEx',
    'RtlCreateUserThread',
    'QueueUserAPC',
    # ★ BUG-B3-12：能**改目标进程执行流**的另外几个入口 —— 它们和
    #   CreateRemoteThread 是同一类风险（劫持一条已有线程去跑我们的代码），
    #   只是不需要新起线程。SetThreadContext 尤其直接（改 RIP 就跳过去了）。
    'SetThreadContext',
    'NtQueueApcThread',
    'SuspendThread',
    'DebugActiveProcess',
    # 全局钩子（等价于把 DLL 塞进别的进程）
    'SetWindowsHookEx',
    'SetWinEventHook',
)

# ------------------------------------------------------- 2. 禁止的权限位（常量名）

#: 源码里出现这些名字，说明有人想申请写权限。
FORBIDDEN_ACCESS_NAMES: Tuple[str, ...] = (
    'PROCESS_VM_WRITE',
    'PROCESS_VM_OPERATION',
    'PROCESS_CREATE_THREAD',
    'PROCESS_SET_INFORMATION',
    'PROCESS_SET_QUOTA',
    'PROCESS_SUSPEND_RESUME',
    'PROCESS_TERMINATE',
    'PROCESS_ALL_ACCESS',
)

#: 禁止出现的访问掩码位（供运行时断言使用）。
#: 所有需要用到它的模块都从这里导入 —— 这样全项目只有本文件出现这些常量名，
#: 静态扫描的豁免名单才能压到最小（只豁免本文件 + 测试文件）。
FORBIDDEN_ACCESS_BITS = {
    'PROCESS_TERMINATE': 0x0001,
    'PROCESS_CREATE_THREAD': 0x0002,
    'PROCESS_VM_OPERATION': 0x0008,
    'PROCESS_VM_WRITE': 0x0020,
    'PROCESS_SET_QUOTA': 0x0100,
    'PROCESS_SET_INFORMATION': 0x0200,
    'PROCESS_SUSPEND_RESUME': 0x0800,
}

#: 终止位：**唯一的合法用途** = 结束我们自己拉起的后台进程
#: （``ui/collector_ctl`` 收采集器）。红线管的是「不许碰**游戏**进程」，
#: 收自家子进程不在此列 —— 但常量名不许散落业务代码，要用从这里拿。
#: ⚠️ 这不是游戏内存的写权限：对游戏进程连 OpenProcess 都不走这条路。
TERMINATE_ACCESS_BIT = FORBIDDEN_ACCESS_BITS['PROCESS_TERMINATE']

# ------------------------------------------------------------- 3. 游戏文件保护

#: 游戏安装目录里**绝对不允许被写**的文件。只读也必须走 open_game_file()。
PROTECTED_GAME_FILES: Tuple[str, ...] = (
    'GameAssembly.dll',
    'global-metadata.dat',
    'masterduel.exe',
    'UnityPlayer.dll',
)

#: 允许对游戏文件使用的打开模式 —— 只有二进制只读。
ALLOWED_GAME_FILE_MODES = ('rb',)

# ------------------------------------------------- 3.5 禁止读取的引擎字段

#: 引擎里**语义上越界**的结构字段名 —— 命中即违规。
#:
#: 背景（2026-09-12 实机验证，证据见 `docs/当前进度.md` 与项目记忆）：
#:
#: * ``YgomGame.Duel.Engine`` 里那个「本局卡牌全集」字段（偏移 +0x98，uint16 数组、
#:   按卡 id 升序，容量 +0xA0 = 512 字节）
#: * **回放里**：实测它装着**双方卡组**（一次 69 张 = 我方 37 ∪ 对手 34，精确相等；
#:   另一次 39 张里 27 张从未公开）；**实时对局里**它只是块被网络层复用的临时内存
#:   （120 次采样、与双方卡表零重合）
#: * 所以它**不是**「已经公开的卡」：回放/赛后读它 = 拿到超出公开范围的信息
#: * 而唯一合规的用法（"只取我方卡表、补日志 deckId=null"）**已被证伪** ——
#:   天梯局的 deckId 日志本来就有，null 只出现在观战/单人
#:
#: → 结论：**这个字段整体按禁区处理**，不接进任何工具。名字写在这里，
#: 业务代码里连注释都不许出现（想讨论请改本文件）。
FORBIDDEN_STRUCT_FIELDS: Tuple[str, ...] = (
    'cardExistBuffer',
)

# ------------------------------------------------------------------ 4. 扫描逻辑

#: 扫描时豁免的文件名（定义/断言红线所必需）
EXEMPT_FILES = frozenset({'redline.py', 'test_memread_guard.py'})

#: 默认扫描的目录
#: ⚠️ M7 起多了 `ui/` —— 界面层**本来不碰内存**，但扫描面收紧是零成本防御：
#: 它和 core/ 在同一个进程里跑，谁也不能保证以后不被塞进不该有的东西。
#: ★ BUG-B3-12：再加 `build/` —— 打包/安装脚本也在这个仓库里、也参与发布产物，
#: 而且它们是「以后可能顺手加一句诊断」的地方（仓库里没有 `packaging/`，
#: 那个名字是审查报告里的旧称）。
#: ⚠️ **`tmp/` 有意不扫**：它是 gitignore 的临时目录（588 个文件、扫描 0.6s），
#: 里面是随手写的探针脚本、不进发布产物；把它纳入会让 guard 单测每次多跑
#: 一个数量级，且探针里出现禁区字段名反而是**研究过程**而非产品违规。
#: 真要查探针，手动跑 `scan_violations(PROJECT_ROOT, dirs=('tmp',))`。
SCAN_DIRS: Tuple[str, ...] = ('core', 'tools', 'ui', 'build')


def _iter_py_files(root: str, dirs=SCAN_DIRS):
    for d in dirs:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _sub, files in os.walk(base):
            for fn in files:
                if fn.endswith('.py') and fn not in EXEMPT_FILES:
                    yield os.path.join(dirpath, fn)


def scan_violations(project_root: str, dirs=SCAN_DIRS) -> List[Tuple[str, int, str]]:
    """扫描业务代码，返回 [(文件路径, 行号, 命中项)]。空列表 = 干净。

    注意：注释里的命中也算违规 —— 这是故意的。
    想讨论某个 API，请写进 ``redline.py``，别在业务代码里留字样。
    """
    hits: List[Tuple[str, int, str]] = []
    needles = (tuple(FORBIDDEN_APIS) + tuple(FORBIDDEN_ACCESS_NAMES)
               + tuple(FORBIDDEN_STRUCT_FIELDS))
    for path in _iter_py_files(project_root, dirs):
        try:
            with io.open(path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for needle in needles:
                if needle in line:
                    hits.append((path, i, needle))
    return hits


def check_game_file_mode(path: str, mode: str) -> None:
    """打开游戏文件前的守门员：只允许二进制只读。

    :raises ValueError: 模式不是 'rb'，或目标是受保护文件却用了别的模式
    """
    if mode not in ALLOWED_GAME_FILE_MODES:
        #: ★ BUG-B3-12：原来这里后面还有一段
        #: ``if '+' in mode or any(c in mode for c in 'wxa')`` —— 恒不可达
        #: （白名单只剩 'rb'，走到这说明 mode 已经不在白名单里、上面就抛了）。
        #: 已删除；保留这条注释是为了说明「为什么只有一个判断」。
        raise ValueError(
            '游戏文件只允许以 %s 打开，收到 mode=%r：%s'
            % (' / '.join(ALLOWED_GAME_FILE_MODES), mode, path)
        )


def is_protected_game_file(path: str) -> bool:
    """路径是否指向受保护的游戏文件。"""
    name = os.path.basename(path)
    return name.lower() in {f.lower() for f in PROTECTED_GAME_FILES}
