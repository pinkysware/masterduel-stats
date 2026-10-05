#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mem_probe —— 只读内存探查的诊断工具。

它干的事，按顺序：

1. 找到 ``masterduel.exe`` 的 pid（没开游戏就直说）
2. 用只读句柄列出模块，报出 ``GameAssembly.dll`` 的基址
3. 在进程里搜 ``global-metadata.dat`` 的**内存镜像**
   （IL2CPP 启动时会把整个文件读进内存，字节与磁盘一致；
   已验证：类名字符串在 dll 里一处都没有，只存在于 metadata）
4. 把 metadata 里的类名翻译成**进程虚拟地址**
5. 用「类名字指针 + 命名空间指针相邻」这 16 字节特征，
   在 ``GameAssembly.dll`` 的映射范围内定位 ``Il2CppClass``（TypeInfo）

第 5 步是下一步所有工作的起点 —— 拿到 TypeInfo 才能往下找实例、找字段。

**不用开游戏也能练**：加 ``--self`` 会把本地 metadata 读进 Python 自己进程，
用同一套代码跑 1-4 步（第 5 步需要真实的 dll 映射，自演练会跳过）。

用法 ::

    python tools/mem_probe.py              # 扫游戏进程（需要游戏在跑）
    python tools/mem_probe.py --self       # 不需要游戏，拿自己进程演练
    python tools/mem_probe.py --pid 1234   # 手动指定
    python tools/mem_probe.py --names LogHandler,Engine

全程只读：只申请 PROCESS_VM_READ | PROCESS_QUERY_INFORMATION，
不写内存、不注入、不在目标进程里执行任何代码。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.memread.il2cpp import (  # noqa: E402
    TYPEINFO_NAME_OFFSET,
    MetadataHeader,
    MetadataMirror,
    default_metadata_path,
    find_all_metadata_mirrors,
    find_string_offset,
    find_typeinfo,
    read_string_table,
    verify_typeinfo,
)
from core.memread.process import (  # noqa: E402
    MEM_COMMIT,
    MemoryRegion,
    SafeProcess,
    find_module,
    find_pids_by_name,
)

GAME_EXE = 'masterduel.exe'
GAME_DIR = r'D:\steam\steamapps\common\Yu-Gi-Oh!  Master Duel'

#: 默认要定位的类名（metadata 里是**短名**，namespace 单独存）
#: 这些名字都已在 YgomGame.Duel 命名空间下核对过确实存在
DEFAULT_NAMES = ('LogHandler', 'DuelClient', 'Engine',
                 'LogShowAction', 'LogShowTurn', 'CardInfo')
DEFAULT_NS = 'YgomGame.Duel'
ASM_DLL = 'GameAssembly.dll'

HR = '-' * 68


def _hdr(title):
    print('\n' + HR)
    print(title)
    print(HR)


def probe_strings(proc, mirror, names):
    """第 4 步：类名 -> 进程地址（并回读校验）。"""
    _hdr('4. 类名 -> 进程地址')
    s0, s1 = mirror.string_table_span
    print('   字符串表 0x%012X - 0x%012X' % (s0, s1))
    hits = 0
    for name in names:
        addr = mirror.string_addr(name, _TABLE)
        if addr is None:
            print('     %-24s (字符串表里没有)' % name)
            continue
        got = proc.try_read(addr, len(name) + 1) or b''
        ok = got.rstrip(b'\x00') == name.encode('utf-8')
        print('     %-24s 0x%012X   回读%s'
              % (name, addr, 'OK' if ok else '**失败:%r**' % got[:24]))
        hits += 1
    print('   （%d/%d 个名字定位成功）' % (hits, len(names)))


def probe_typeinfo(proc, mirror, names, ns, asm):
    """第 5 步：在 dll 映射范围内搜 TypeInfo。"""
    _hdr('5. TypeInfo 定位（namespace=%s）' % ns)
    print('   搜索范围：整个进程（TypeInfo 在 IL2CPP 的堆上，不在 dll 映射里）')
    print('   假设 Il2CppClass.name 在基址 +0x%X（实机已验证）'
          % TYPEINFO_NAME_OFFSET)
    region = None
    found = 0
    for name in names:
        tis = find_typeinfo(proc, mirror, _TABLE, ns, name,
                            regions=region, max_hits=4)
        if not tis:
            print('     %-20s 未命中' % name)
            continue
        for ti in tis:
            ok = verify_typeinfo(proc, ti, mirror, _TABLE, ns, name)
            print('     %-20s TypeInfo=0x%012X   校验%s'
                  % (name, ti, 'OK' if ok else '**失败**'))
            found += 1
    print('   （%d 个类定位成功）' % found)
    if not found:
        print('\n   0 命中 = 这些类**还没被 CLR 初始化**（name 字段仍是 0）。')
        print('   IL2CPP 是 lazy 的 —— 进一次对局再试。')
        print('   （布局 0x%X 已实机验证过，先别怀疑它）' % TYPEINFO_NAME_OFFSET)


def probe_metadata(proc, header, table, regions=None, asm=None, ns=DEFAULT_NS):
    """第 3-5 步，共用逻辑。"""
    global _TABLE
    _TABLE = table

    _hdr('3. metadata 内存镜像')
    mirrors = find_all_metadata_mirrors(proc, header, regions=regions)
    if not mirrors:
        print('   没找到。可能原因：进程没加载 IL2CPP / 镜像被拆散 / 权限不足')
        return None
    print('   找到 %d 处（正常应为 1）：' % len(mirrors))
    for m in mirrors:
        print('     0x%012X  version=%d' % (m.base, m.header.version))

    m = mirrors[0]
    local_head = _local_head(4096)
    if local_head:
        remote = proc.try_read(m.base, len(local_head))
        same = (remote == local_head)
        print('   与磁盘前 %d 字节%s'
              % (len(local_head), '完全一致' if same else ' **不一致**'))
        if not same and remote:
            diff = sum(1 for a, b in zip(remote, local_head) if a != b)
            print('      差异字节数：%d / %d' % (diff, len(local_head)))

    probe_strings(proc, m, _NAMES)
    probe_typeinfo(proc, m, _NAMES, ns, asm)
    return m


def _local_head(n):
    if not os.path.exists(_META):
        return b''
    with open(_META, 'rb') as fh:
        return fh.read(n)


def probe_game(pid=None) -> int:
    _hdr('1. 目标进程')
    if pid is None:
        pids = find_pids_by_name(GAME_EXE)
        if not pids:
            print('   没找到 %s（游戏没开）' % GAME_EXE)
            print('   -> 先开游戏再跑本命令；或加 --self 拿自己进程演练')
            return 1
        pid = pids[0]
        print('   %s  pid=%d%s' % (GAME_EXE, pid,
                                   '  (共 %d 个)' % len(pids) if len(pids) > 1 else ''))
    else:
        print('   手动指定 pid=%d' % pid)

    try:
        proc = SafeProcess(pid)
    except Exception as exc:
        print('   打开失败：%s' % exc)
        return 1

    with proc:
        print('   句柄 access=0x%04X（只读）' % proc.access)

        _hdr('2. 模块')
        try:
            mods = proc.modules()
        except Exception as exc:
            print('   列模块失败：%s' % exc)
            return 1
        print('   共 %d 个模块' % len(mods))
        asm = None
        for key in (ASM_DLL, 'UnityPlayer.dll', GAME_EXE):
            m = find_module(mods, key)
            if m:
                print('   %-20s base=0x%012X size=0x%X' % (m[0], m[1], m[2]))
                if key == ASM_DLL:
                    asm = m
            else:
                print('   %-20s (未加载)' % key)

        if not os.path.exists(_META):
            print('\n   metadata 文件不存在：%s' % _META)
            return 1
        header = MetadataHeader.from_file(_META)
        table = read_string_table(_META, header)
        print('\n   本地 metadata: %r' % header)
        probe_metadata(proc, header, table, asm=asm, ns=_NS)
    return 0


def probe_self() -> int:
    """拿本地 metadata 在 Python 自己进程里演练（不需要游戏）。"""
    _hdr('1. 目标进程')
    if not os.path.exists(_META):
        print('   metadata 文件不存在：%s' % _META)
        return 1
    print('   pid=%d（Python 自己）' % os.getpid())

    header = MetadataHeader.from_file(_META)
    with open(_META, 'rb') as fh:
        blob = bytearray(fh.read())
    view = ctypes.c_char.from_buffer(blob)
    addr = ctypes.addressof(view)
    print('   把 %d 字节 metadata 读进本进程：0x%012X' % (len(blob), addr))

    _hdr('2. 模块')
    print('   （自演练模式跳过）')

    table = read_string_table(_META, header)
    print('\n   本地 metadata: %r' % header)

    regions = [MemoryRegion(addr & ~0xFFF, len(blob) + 0x2000, 0x04,
                            MEM_COMMIT, 0x20000)]
    with SafeProcess(os.getpid()) as proc:
        probe_metadata(proc, header, table, regions=regions, asm=None, ns=_NS)

    del view   # 必须持有到用完，否则地址失效
    del blob
    return 0


_TABLE = b''
_NAMES = DEFAULT_NAMES
_NS = DEFAULT_NS
_META = default_metadata_path(GAME_DIR)


def main(argv=None) -> int:
    global _NAMES, _NS, _META
    ap = argparse.ArgumentParser(description='只读内存探查诊断')
    ap.add_argument('--self', action='store_true',
                    help='不扫游戏，拿本地 metadata 在自己进程里演练')
    ap.add_argument('--pid', type=int, help='手动指定进程 pid')
    ap.add_argument('--names', default=','.join(DEFAULT_NAMES),
                    help='要定位的类名，逗号分隔')
    ap.add_argument('--namespace', default=DEFAULT_NS, help='命名空间')
    ap.add_argument('--game-dir', default=GAME_DIR, help='游戏安装目录')
    args = ap.parse_args(argv)

    _META = default_metadata_path(args.game_dir)
    _NS = args.namespace
    _NAMES = tuple(n.strip() for n in args.names.split(',') if n.strip())

    print(HR)
    print('mem_probe —— 只读内存探查（不写内存 / 不注入 / 不远程执行）')
    print(HR)

    if args.self:
        return probe_self()
    return probe_game(args.pid)


if __name__ == '__main__':
    sys.exit(main())
