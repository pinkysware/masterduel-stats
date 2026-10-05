# -*- coding: utf-8 -*-
"""memread —— 自研「只读进程内存」数据源（M1 的第二条腿）。

背景
----
当前对局数据全部来自 Untapped Companion 写的 ``log.log``，那是**三手数据**
（游戏内存 -> untapped-scry.node -> log.log -> 我们），而且 1MB 轮转只留一份。
本模块的目标是直接、只读地读取 ``masterduel.exe`` 的进程内存，拿回一手数据。

红线的由来
----------
用户 2026-09-12 明确要求：

    保证工具不会修改 GameAssembly.dll，也不会向 Master Duel 进程注入第三方 DLL。

这不是一句承诺书，而是**由代码和单测强制执行**的约束
（见 ``core/memread/redline.py`` 与 ``tests/test_memread_guard.py``）：

1. 只申请 ``PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`` —— **不申请任何写权限位**
2. 绝不调用任何「写内存 / 远程执行 / 注入」类 API（完整清单见 ``redline.py``）
3. 绝不向 masterduel 进程注入任何 DLL（不写内存就没有注入的载体）
4. 绝不修改游戏安装目录下的任何文件（含 ``GameAssembly.dll``）
5. 只读游戏**已公开**的信息 —— **不读对手手牌与盖牌**（读了就是作弊，会毁掉项目）

第 5 条的落地（step 2）：不调用游戏函数（``EngineApiUtil.IsCardKnown`` 等，
调用 = 远程执行），而是读引擎自己维护的区域/朝向标记，再按
``public_cards.is_public_zone`` 的判据只留公开区（场上表侧 + 墓地）。
``DuelClientMillenniumEye``（千年眼）强制返回 1 = 作弊开关，**绝不能碰**。

模块结构
--------
====================  ===================================================
``redline.py``        红线定义 + 源码静态扫描（被单测调用，唯一豁免文件）
``process.py``        ``SafeProcess``：只读打开进程、按地址读内存 / 遍历区域 / 搜字节
``il2cpp.py``         IL2CPP 结构解析：metadata 内存镜像、类名 -> TypeInfo、
                      字段表（名字 + 偏移）、静态字段区 -> 单例实例
``reader_base.py``    读数公共底座：偏移按「字段名白名单」查，查不到就抛
``public_cards.py``   step 2：读「当前对双方公开的卡」（场上表侧 ∪ 墓地），
                      只输出公开卡 id，隐藏区不输出
``duel_state.py``     step 3：读公开对局状态（回合 / 阶段 / 双方 LP），M6 的实时输入
====================  ===================================================

用法::

    from core.memread import SafeProcess, find_module

    with SafeProcess(pid) as p:
        mods = p.modules()
        ga = find_module(mods, 'GameAssembly.dll')
        head = p.read(ga[1], 64)

    # 读游戏文件（保证只读打开，任何写模式都会被拒绝）
    from core.memread import open_game_file
    with open_game_file(r"D:\\...\\global-metadata.dat") as f:
        head = f.read(64)

状态：**已实机打通**（2026-09-12）。在真实 ``masterduel.exe`` 上验证过整条链路：
找 metadata 镜像 -> 类名寻址 -> TypeInfo（回读校验通过）-> ``list_fields``
拿到字段名与偏移 -> ``static_fields`` 取到对局实例（``Engine.s_instance`` /
``DuelClient.instance``），并按偏移读出了 ``gameMode`` 等值。

⚠️ 另外：引擎里那个「本局卡牌全集」字段（实例偏移 ``+0x98``）**已列入禁区** ——
回放里它含对手未公开的卡，实时里则是块被网络层复用的临时内存。
按 ``redline.py`` 的规矩，**业务代码里连它的字段名都不许出现**
（名字只写在 ``core/memread/redline.py`` 的 ``FORBIDDEN_STRUCT_FIELDS`` 里）。
``tests/test_memread_guard.py`` 守着本模块的「只读 + 不注入 + 不读禁区字段」约束。
"""
from __future__ import annotations

from .process import (
    SafeProcess,
    MemoryReadError,
    ReadOnlyViolation,
    READ_ONLY_ACCESS,
    PROCESS_VM_READ,
    PROCESS_QUERY_INFORMATION,
    FORBIDDEN_ACCESS_BITS,
    open_game_file,
    find_module,
    assert_read_only,
)
from .reader_base import TypeInfoReader
from .duel_state import (
    DuelState,
    DuelStateReader,
    PHASE_NAMES,
)
from .public_cards import (
    PublicCard,
    CardTableOffsets,
    PublicCardReader,
    RawCard,
    is_public_zone,
    zone_of,
    read_card_table,
    read_public_cards,
)

__all__ = [
    'SafeProcess',
    'MemoryReadError',
    'ReadOnlyViolation',
    'READ_ONLY_ACCESS',
    'PROCESS_VM_READ',
    'PROCESS_QUERY_INFORMATION',
    'FORBIDDEN_ACCESS_BITS',
    'open_game_file',
    'find_module',
    'assert_read_only',
    # 读数底座
    'TypeInfoReader',
    # step 3：公开对局状态
    'DuelState',
    'DuelStateReader',
    'PHASE_NAMES',
    # step 2：公开卡
    'PublicCard',
    'CardTableOffsets',
    'PublicCardReader',
    'RawCard',
    'is_public_zone',
    'zone_of',
    'read_card_table',
    'read_public_cards',
]
