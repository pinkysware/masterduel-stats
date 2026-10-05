# -*- coding: utf-8 -*-
"""SafeProcess —— 只读地打开进程、按地址读内存。

设计原则：**这个类的存在意义就是「不可能写」。**

* 权限位在类里硬编码为 ``PROCESS_VM_READ | PROCESS_QUERY_INFORMATION``，
  ``__init__`` **不接受 access 参数** —— 调用方想申请写权限也没入口
* 只提供 ``read()`` / ``regions()`` / ``scan()`` 系列；
  **没有** ``write()`` / ``alloc()`` / ``inject()``
* 任何对游戏文件的打开都走 ``open_game_file()``，只允许 ``'rb'``

这些约束由 ``tests/test_memread_guard.py`` 守着，改动会被测试拦下。

关于内存区域遍历
----------------
``regions()`` 用 ``VirtualQueryEx`` —— 它是**查询**类 API（只读页属性），
与那类会**改动**目标进程地址空间的 Virtual*Ex API 不是一回事，
所以不在红线清单里（清单见 redline.py）。没有它就没法做进程内搜索。
"""
from __future__ import annotations

import ctypes
import io
import os
import time
from ctypes import wintypes
from typing import Iterator, List, NamedTuple, Optional, Tuple

from . import redline

# ------------------------------------------------------------------ 权限位

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

#: 唯一允许的访问掩码。改这个值会让单测变红 —— 那是故意的。
READ_ONLY_ACCESS = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION

# 禁用位定义在 redline.py（集中管理，才能让静态扫描的豁免名单只有一个文件）
FORBIDDEN_ACCESS_BITS = redline.FORBIDDEN_ACCESS_BITS

_LIST_MODULES_ALL = 0x03

# ------------------------------------------------------------------ 内存属性

MEM_COMMIT = 0x1000
MEM_FREE = 0x10000
MEM_RESERVE = 0x2000
#: 私有内存（VirtualAlloc 的堆，含 IL2CPP GC 托管堆）——
#: C# 实例只可能在这里；扫实例指针时用它滤掉 dll/文件映射大半地址空间
MEM_PRIVATE = 0x20000

PAGE_NOACCESS = 0x01
#: 任何不可读的页都跳过，读它会让 ReadProcessMemory 失败
_BAD_PROTECT = {PAGE_NOACCESS, 0x00}

#: 扫描时单块大小（1 MiB）。太大了浪费内存，太小了 syscall 太密。
DEFAULT_CHUNK = 1 << 20


class ReadOnlyViolation(Exception):
    """试图突破只读约束（写代码、申请写权限、写游戏文件）。"""


class MemoryReadError(Exception):
    """读取失败（进程退出、地址未映射、权限不足等）。"""


def _kernel32():
    try:
        return ctypes.windll.kernel32  # type: ignore[attr-defined]
    except AttributeError:
        return None


def _psapi():
    try:
        return ctypes.windll.psapi  # type: ignore[attr-defined]
    except AttributeError:
        return None


def assert_read_only(access: int) -> None:
    """断言访问掩码不含任何写/执行类权限位。

    :raises ReadOnlyViolation: 掩码不等于 READ_ONLY_ACCESS 或含禁用位
    """
    if access != READ_ONLY_ACCESS:
        raise ReadOnlyViolation(
            '访问掩码必须是 READ_ONLY_ACCESS(0x%04X)，收到 0x%04X'
            % (READ_ONLY_ACCESS, access)
        )
    for name, bit in FORBIDDEN_ACCESS_BITS.items():
        if access & bit:
            raise ReadOnlyViolation('访问掩码含有被禁止的权限位：%s' % name)


class MemoryRegion(NamedTuple):
    """一段进程虚拟内存。只读描述，不含任何句柄。"""

    base: int
    size: int
    protect: int
    state: int
    type: int

    @property
    def end(self) -> int:
        return self.base + self.size

    def __str__(self) -> str:
        return '0x%X-0x%X (%d KiB, prot=%#x)' % (
            self.base, self.end, self.size >> 10, self.protect)


class SafeProcess(object):
    """一个**只能读**的进程句柄。

    ::

        with SafeProcess(pid) as p:
            head = p.read(base, 64)
            for addr in p.scan(b'AF\\x1b\\xb1\\xfa'):
                ...

    关闭后句柄失效；支持 ``with``。
    """

    def __init__(self, pid: int) -> None:
        """打开进程。**只申请读权限**，没有别的选项。"""
        k32 = _kernel32()
        if k32 is None:
            raise MemoryReadError('当前平台不是 Windows，无法读取进程内存')

        assert_read_only(READ_ONLY_ACCESS)

        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(READ_ONLY_ACCESS, False, int(pid))
        if not handle:
            raise MemoryReadError(
                'OpenProcess 失败 pid=%s（进程不存在或权限不足）：%s'
                % (pid, ctypes.WinError(ctypes.get_last_error()))
            )
        self._k32 = k32
        self._pid = int(pid)
        self._handle = handle
        self._closed = False

    # ---------------------------------------------------------- 属性

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def access(self) -> int:
        """实际使用的访问掩码（恒为 READ_ONLY_ACCESS）。"""
        return READ_ONLY_ACCESS

    # ---------------------------------------------------------- 读取

    def read(self, address: int, size: int) -> bytes:
        """从 ``address`` 读 ``size`` 字节。

        :raises MemoryReadError: 读取失败（地址未映射 / 进程已退出）
        """
        if self._closed:
            raise MemoryReadError('句柄已关闭')
        if size <= 0:
            return b''
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        self._k32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        ]
        self._k32.ReadProcessMemory.restype = wintypes.BOOL
        ok = self._k32.ReadProcessMemory(
            self._handle, wintypes.LPCVOID(address), buf, size, ctypes.byref(got)
        )
        if not ok:
            raise MemoryReadError(
                'ReadProcessMemory 失败 addr=0x%X size=%d：%s'
                % (address, size, ctypes.WinError(ctypes.get_last_error()))
            )
        return buf.raw[:got.value]

    def try_read(self, address: int, size: int) -> Optional[bytes]:
        """读失败时返回 ``None`` 而不是抛异常（扫内存时用，页可能中途消失）。"""
        try:
            return self.read(address, size)
        except (MemoryReadError, OSError):
            return None

    def read_u32(self, address: int) -> int:
        """读一个小端 uint32。"""
        return int.from_bytes(self.read(address, 4), 'little', signed=False)

    def read_u64(self, address: int) -> int:
        """读一个小端 uint64（64 位下即原生指针宽度）。"""
        return int.from_bytes(self.read(address, 8), 'little', signed=False)

    def read_ptr(self, address: int) -> int:
        """读一个指针（按当前解释器位数）。"""
        width = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
        return int.from_bytes(self.read(address, width), 'little', signed=False)

    def read_cstring(self, address: int, limit: int = 4096) -> str:
        """读到 NUL 为止，按 UTF-8 解码（用于 IL2CPP 字符串表）。"""
        chunk = self.read(address, min(limit, 4096))
        end = chunk.find(b'\x00')
        if end >= 0:
            chunk = chunk[:end]
        return chunk.decode('utf-8', errors='replace')

    # ---------------------------------------------------------- 区域遍历

    def iter_regions(self, max_address: Optional[int] = None) -> Iterator[MemoryRegion]:
        """遍历进程已提交且可读的内存区域（``VirtualQueryEx``，只读）。

        只产出 ``State == MEM_COMMIT`` 且页保护不是 ``PAGE_NOACCESS`` 的区域，
        因为其它区域读不了，读也只会失败。

        ``VirtualQueryEx`` 是查询页属性的 API，**不修改**进程地址空间，
        因此不在红线清单里（清单里禁的是会**改动**目标进程的那几个）。
        """
        if self._closed:
            raise MemoryReadError('句柄已关闭')
        k32 = self._k32
        vq = k32.VirtualQueryEx
        vq.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID,
            ctypes.POINTER(_MEMORY_BASIC_INFORMATION), ctypes.c_size_t,
        ]
        vq.restype = ctypes.c_size_t

        if max_address is None:
            max_address = _user_space_limit(k32)

        mbi = _MEMORY_BASIC_INFORMATION()
        addr = 0
        while addr < max_address:
            got = vq(self._handle, wintypes.LPCVOID(addr), ctypes.byref(mbi),
                     ctypes.sizeof(mbi))
            if not got:
                # 查询本身失败（极少见）：跳过一格继续，不要整段放弃
                addr += DEFAULT_CHUNK
                continue
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize or 0)
            if size <= 0:
                break  # 防御死循环
            if (mbi.State == MEM_COMMIT and mbi.Protect not in _BAD_PROTECT
                    and size > 0):
                yield MemoryRegion(base, size, int(mbi.Protect),
                                   int(mbi.State), int(mbi.Type))
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt
        return

    def regions(self, max_address: Optional[int] = None) -> List[MemoryRegion]:
        """:meth:`iter_regions` 的列表版。"""
        return list(self.iter_regions(max_address))

    def private_regions(self, max_address: Optional[int] = None
                        ) -> List[MemoryRegion]:
        """只取 ``MEM_PRIVATE`` 的已提交区域（GC 托管堆都在这里）。

        全进程可读地址空间里 dll 映射（MEM_IMAGE）与文件映射（MEM_MAPPED）
        往往占大半 —— 2026-09-26 实测单次全量扫描要 1-4 分钟（游戏内存越大
        越慢），而扫「C# 实例指针」这类目标只可能命中私有堆。过滤后通常能
        砍掉大部分耗时。只读查询，不碰红线。
        """
        return [r for r in self.iter_regions(max_address)
                if r.type == MEM_PRIVATE]

    # ---------------------------------------------------------- 搜索

    def scan(self, pattern: bytes, max_hits: Optional[int] = None,
             chunk: int = DEFAULT_CHUNK,
             regions: Optional[List[MemoryRegion]] = None,
             deadline: Optional[float] = None) -> Iterator[int]:
        """在整个可读地址空间里搜字节序列，yield 命中的起始地址。

        跨块边界做了重叠处理，不会漏掉骑在两块之间的命中。

        :param pattern: 要找的字节串（越独特越好，4 字节以下会有一堆噪声）
        :param max_hits: 命中多少个就停（找唯一镜像时填 1 会快很多）
        """
        if not pattern:
            return
        if regions is None:
            regions = self.regions()
        overlap = len(pattern) - 1
        found = 0
        for reg in regions:
            pos = reg.base
            end = reg.end
            while pos < end:
                #: 墙钟预算（09-26 事故刀③的底层支持）：超时立即停扫。
                #: 一次收局窗口的开销必须有上限，不能跟着目标进程内存涨。
                if deadline is not None and time.monotonic() > deadline:
                    return
                want = min(chunk, end - pos)
                buf = self.try_read(pos, want + overlap)
                if not buf:
                    buf = self.try_read(pos, want)
                if not buf:
                    # 这一块读不了（页可能已被释放）：跳过去，别放弃整个区域
                    pos += chunk
                    continue
                start = 0
                while True:
                    i = buf.find(pattern, start)
                    if i < 0 or i >= want:
                        break
                    yield pos + i
                    found += 1
                    if max_hits is not None and found >= max_hits:
                        return
                    start = i + 1
                pos += want
        return

    def scan_all(self, pattern: bytes, max_hits: Optional[int] = None,
                 **kw) -> List[int]:
        """:meth:`scan` 的列表版。"""
        return list(self.scan(pattern, max_hits, **kw))

    # ---------------------------------------------------------- 模块列表

    def modules(self) -> List[Tuple[str, int, int]]:
        """列出已加载模块 ``[(名称, 基址, 大小)]``。只读操作。

        用来拿 ``GameAssembly.dll`` 的基址 —— 后续算 RVA 的起点。
        """
        if self._closed:
            raise MemoryReadError('句柄已关闭')
        psapi = _psapi()
        if psapi is None:
            raise MemoryReadError('psapi 不可用')
        buf = (wintypes.HMODULE * 4096)()
        needed = wintypes.DWORD(0)
        psapi.EnumProcessModulesEx.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
        ]
        psapi.EnumProcessModulesEx.restype = wintypes.BOOL
        if not psapi.EnumProcessModulesEx(
            self._handle, ctypes.cast(buf, ctypes.c_void_p), ctypes.sizeof(buf),
            ctypes.byref(needed), _LIST_MODULES_ALL,
        ):
            raise MemoryReadError(
                'EnumProcessModulesEx 失败：%s'
                % ctypes.WinError(ctypes.get_last_error())
            )
        count = min(needed.value // ctypes.sizeof(wintypes.HMODULE), 4096)
        out: List[Tuple[str, int, int]] = []
        namebuf = ctypes.create_unicode_buffer(260)
        for i in range(count):
            hmod = buf[i]
            base = int(hmod) if hmod else 0
            if not base:
                continue
            psapi.GetModuleBaseNameW.argtypes = [
                wintypes.HANDLE, wintypes.HMODULE,
                ctypes.POINTER(ctypes.c_wchar), wintypes.DWORD,
            ]
            psapi.GetModuleBaseNameW.restype = wintypes.DWORD
            psapi.GetModuleBaseNameW(self._handle, hmod, namebuf, 260)
            name = namebuf.value
            info = _MODULEINFO()
            psapi.GetModuleInformation.argtypes = [
                wintypes.HANDLE, wintypes.HMODULE,
                ctypes.POINTER(_MODULEINFO), wintypes.DWORD,
            ]
            psapi.GetModuleInformation.restype = wintypes.BOOL
            size = 0
            if psapi.GetModuleInformation(
                self._handle, hmod, ctypes.byref(info), ctypes.sizeof(info)
            ):
                size = int(info.SizeOfImage)
            out.append((name, base, size))
        return out

    # ---------------------------------------------------------- 生命周期

    def close(self) -> None:
        if self._closed:
            return
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32.CloseHandle.restype = wintypes.BOOL
        self._k32.CloseHandle(self._handle)
        self._closed = True

    def __enter__(self) -> 'SafeProcess':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return '<SafeProcess pid=%d access=0x%04X%s>' % (
            self._pid, READ_ONLY_ACCESS, ' closed' if self._closed else ''
        )


class _MODULEINFO(ctypes.Structure):
    _fields_ = [
        ('lpBaseOfDll', ctypes.c_void_p),
        ('SizeOfImage', wintypes.DWORD),
        ('EntryPoint', ctypes.c_void_p),
    ]


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('PartitionId', wintypes.WORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD),
    ]


class _SYSTEM_INFO(ctypes.Structure):
    _fields_ = [
        ('wProcessorArchitecture', wintypes.WORD),
        ('wReserved', wintypes.WORD),
        ('dwPageSize', wintypes.DWORD),
        ('lpMinimumApplicationAddress', ctypes.c_void_p),
        ('lpMaximumApplicationAddress', ctypes.c_void_p),
        ('dwActiveProcessorMask', ctypes.POINTER(wintypes.DWORD)),
        ('dwNumberOfProcessors', wintypes.DWORD),
        ('dwProcessorType', wintypes.DWORD),
        ('dwAllocationGranularity', wintypes.DWORD),
        ('wProcessorLevel', wintypes.WORD),
        ('wProcessorRevision', wintypes.WORD),
    ]


def _user_space_limit(k32) -> int:
    """拿用户态地址上限；拿不到就用保守值。"""
    try:
        si = _SYSTEM_INFO()
        k32.GetSystemInfo.argtypes = [ctypes.POINTER(_SYSTEM_INFO)]
        k32.GetSystemInfo(ctypes.byref(si))
        limit = int(si.lpMaximumApplicationAddress or 0)
        if limit > 0:
            return limit
    except Exception:
        pass
    return 0x7FFFFFFFFFFF


def open_game_file(path: str, mode: str = 'rb'):
    """以**只读**方式打开游戏安装目录下的文件。

    这是「不修改 GameAssembly.dll」的落地点：模式不是 ``'rb'`` 直接抛异常，
    于是任何想写游戏文件的代码在写出来的那一刻就会失败。

    :raises ValueError: 模式不是 ``'rb'``
    """
    redline.check_game_file_mode(path, mode)
    return io.open(path, 'rb')


def find_module(modules: List[Tuple[str, int, int]], name: str) -> Optional[Tuple[str, int, int]]:
    """在 :meth:`SafeProcess.modules` 的结果里按名字（忽略大小写）找模块。"""
    target = name.lower()
    for m in modules:
        if m[0].lower() == target:
            return m
    return None


def iter_pids(max_pids: int = 8192) -> Iterator[int]:
    """枚举当前系统里的所有 pid（``EnumProcesses``，只读）。"""
    psapi = _psapi()
    if psapi is None:
        raise MemoryReadError('psapi 不可用')
    arr = (wintypes.DWORD * max_pids)()
    needed = wintypes.DWORD(0)
    psapi.EnumProcesses.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    psapi.EnumProcesses.restype = wintypes.BOOL
    if not psapi.EnumProcesses(ctypes.cast(arr, ctypes.c_void_p),
                               ctypes.sizeof(arr), ctypes.byref(needed)):
        raise MemoryReadError('EnumProcesses 失败：%s'
                              % ctypes.WinError(ctypes.get_last_error()))
    count = min(int(needed.value) // ctypes.sizeof(wintypes.DWORD), max_pids)
    for i in range(count):
        pid = int(arr[i])
        if pid:
            yield pid


def find_pids_by_name(name: str, main_only: bool = True) -> List[int]:
    """按**进程主模块名**找 pid，如 ``find_pids_by_name('masterduel.exe')``。

    没有 pid 会拿到一堆「拒绝访问」的进程（系统进程等），这里直接跳过 ——
    我们只要能读的那个。

    :return: 匹配的 pid 列表（通常 0 或 1 个）
    """
    target = name.lower()
    out: List[int] = []
    for pid in iter_pids():
        try:
            proc = SafeProcess(pid)
        except (MemoryReadError, OSError):
            continue                      # 打不开就算了，不是我们的目标
        try:
            mods = proc.modules()
        except (MemoryReadError, OSError):
            continue
        finally:
            proc.close()
        if not mods:
            continue
        if main_only:
            if mods[0][0].lower() == target:
                out.append(pid)
        else:
            if any(m[0].lower() == target for m in mods):
                out.append(pid)
    return out
