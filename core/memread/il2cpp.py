# -*- coding: utf-8 -*-
"""IL2CPP 静态/半静态探查 —— 在**不注入、不调用游戏函数**的前提下定位内部结构。

当前能做的
-----------
``find_metadata_mirror()``
    IL2CPP 启动时会把 ``global-metadata.dat`` **整个读进进程内存**
    （``il2cpp::vm::MetadataLoader::LoadMetadataFile``）。
    于是进程里躺着一份和磁盘文件字节一致的镜像 —— 找到了它，
    「metadata 里的某个字符串」就有了**进程虚拟地址**，
    之后所有「搜谁指向这个地址」的定位手法都以它为起点。

    这一步**不需要**知道 ``Il2CppClass`` / ``Il2CppImage`` 的结构布局
    （那些才是真正的深坑，字段偏移不在 metadata 里）。

明确不做的事
-------------
* 不写目标进程内存、不注入、不在目标进程里开线程（清单见 redline.py）
* 不调用 ``GameAssembly.dll`` 导出的 ``il2cpp_*`` 函数 —— 那需要远程执行
* 不读游戏未公开的信息（手牌 / 盖牌 / 未展示的卡）
"""
from __future__ import annotations

import os
import struct
from typing import Dict, List, NamedTuple, Optional, Tuple

from .process import SafeProcess

#: ``global-metadata.dat`` 开头的 sanity 值 0xFAB11BAF（小端存 AF 1B B1 FA）
METADATA_MAGIC = 0xFAB11BAF

#: v31 header 前段字段的字节偏移（与磁盘文件一致，已用本机文件核对）
_HDR = {
    'magic': 0x00,
    'version': 0x04,
    'stringLiteralOffset': 0x08,
    'stringLiteralSize': 0x0C,
    'stringLiteralDataOffset': 0x10,
    'stringLiteralDataSize': 0x14,
    'stringOffset': 0x18,   # 24
    'stringSize': 0x1C,     # 28
    'eventsOffset': 0x20,
    'eventsSize': 0x24,
    'propertiesOffset': 0x28,
    'propertiesSize': 0x2C,
    'methodsOffset': 0x30,
    'methodsSize': 0x34,
}

#: 搜索用的特征长度。16 字节 = magic+version+前两组偏移，足够独特
SIGNATURE_LEN = 16


class MetadataHeader(object):
    """``global-metadata.dat`` 的头部（离线解析，来自磁盘只读副本）。"""

    __slots__ = ('_raw', '_fields')

    def __init__(self, raw: bytes) -> None:
        if len(raw) < SIGNATURE_LEN:
            raise ValueError('metadata 头部太短：%d 字节' % len(raw))
        self._raw = bytes(raw)
        f: Dict[str, int] = {}
        for name, off in _HDR.items():
            if off + 4 <= len(raw):
                # magic 是 0xFAB11BAF，超过 int32 正数上限，得按无符号读
                fmt = '<I' if name == 'magic' else '<i'
                f[name] = struct.unpack_from(fmt, raw, off)[0]
        self._fields = f

    # ---------------------------------------------------------------- 工厂

    @classmethod
    def from_file(cls, path: str) -> 'MetadataHeader':
        """只读打开 metadata 文件并解析头部。"""
        # 走 process.open_game_file 才能确保只读
        from .process import open_game_file
        with open_game_file(path, 'rb') as fh:
            return cls(fh.read(4096))

    # ---------------------------------------------------------------- 字段

    def __getitem__(self, name: str) -> int:
        return self._fields[name]

    def get(self, name: str, default: int = -1) -> int:
        return self._fields.get(name, default)

    @property
    def version(self) -> int:
        return self.get('version')

    @property
    def string_offset(self) -> int:
        """字符串表在文件里的起始偏移。"""
        return self.get('stringOffset')

    @property
    def string_size(self) -> int:
        return self.get('stringSize')

    @property
    def is_valid(self) -> bool:
        return (self.get('magic') == METADATA_MAGIC
                and self.version > 0
                and self.string_offset > 0
                and self.string_size > 0)

    @property
    def signature(self) -> bytes:
        """用于进程内搜索的头部特征字节。"""
        return self._raw[:SIGNATURE_LEN]

    def __repr__(self) -> str:
        return ('<MetadataHeader version=%s string=(%d,%d) valid=%s>'
                % (self.version, self.string_offset, self.string_size, self.is_valid))


class MetadataMirror(object):
    """进程里那份 metadata 镜像 —— 把「文件偏移」翻译成「进程地址」。"""

    def __init__(self, base: int, header: MetadataHeader) -> None:
        self.base = int(base)
        self.header = header

    def addr_of_offset(self, file_offset: int) -> int:
        return self.base + file_offset

    def string_addr(self, name: str, string_table: bytes) -> Optional[int]:
        """在字符串表里查 ``name``，返回它在进程里的地址。

        :param string_table: 磁盘上读出的字符串表原文（``[string_offset, +size)``）
        :return: 进程地址；找不到返回 ``None``
        """
        off = find_string_offset(string_table, name)
        if off is None:
            return None
        return self.base + self.header.string_offset + off

    @property
    def string_table_span(self) -> Tuple[int, int]:
        """字符串表在进程里的 ``(起始地址, 结束地址)``。"""
        s = self.base + self.header.string_offset
        return s, s + self.header.string_size

    def __repr__(self) -> str:
        return '<MetadataMirror base=0x%X %r>' % (self.base, self.header)


def find_string_offset(table: bytes, name: str, exact: bool = True) -> Optional[int]:
    """在 metadata 字符串表里找一个**完整**条目。

    metadata 的字符串表是一串 NUL 结尾的条目，所以查 ``\\0name\\0``
    才能避免匹配到 ``LogHandlerExtra`` 这种前缀相同的名字。

    :return: 相对字符串表**开头**的偏移（不是文件偏移）
    """
    if not table or not name:
        return None
    pat = name.encode('utf-8')
    if exact:
        pat = b'\x00' + pat + b'\x00'
    i = table.find(pat)
    if i < 0:
        if exact:
            # 表首条目前面没有 NUL
            if table.startswith(pat[1:]):
                return 0
        return None
    return (i + 1) if exact else i


def read_string_table(path: str, header: MetadataHeader) -> bytes:
    """只读取出字符串表原文。"""
    from .process import open_game_file
    with open_game_file(path, 'rb') as fh:
        fh.seek(header.string_offset)
        return fh.read(header.string_size)


def verify_candidate(proc: SafeProcess, addr: int, header: MetadataHeader) -> bool:
    """验证 ``addr`` 处确实是那份 metadata 镜像（而不是撞巧的 16 字节）。"""
    sig = header.signature
    blob = proc.try_read(addr, 64)
    if not blob or len(blob) < 64:
        return False
    if blob[:len(sig)] != sig:
        return False
    # 再核两个不该变的字段
    try:
        if struct.unpack_from('<i', blob, _HDR['stringOffset'])[0] != header.string_offset:
            return False
        if struct.unpack_from('<i', blob, _HDR['stringSize'])[0] != header.string_size:
            return False
    except struct.error:
        return False
    return True


def find_metadata_mirror(proc: SafeProcess, header: MetadataHeader,
                         max_candidates: int = 8,
                         regions=None) -> Optional[MetadataMirror]:
    """在游戏进程里找 ``global-metadata.dat`` 的内存镜像。

    :param proc: 已打开的只读进程句柄
    :param header: 磁盘文件解析出的头部（用来生成特征 + 验证）
    :return: :class:`MetadataMirror` 或 ``None``（进程里没找到）
    """
    if not header.is_valid:
        raise ValueError('metadata 头部不合法：%r' % header)
    sig = header.signature
    for addr in proc.scan(sig, max_hits=max_candidates, regions=regions):
        if verify_candidate(proc, addr, header):
            return MetadataMirror(addr, header)
    return None


def find_all_metadata_mirrors(proc: SafeProcess, header: MetadataHeader,
                              max_candidates: int = 32,
                              regions=None) -> List[MetadataMirror]:
    """列出全部通过验证的镜像（正常应只有 1 个，多个说明需要人工判断）。"""
    out: List[MetadataMirror] = []
    for addr in proc.scan(header.signature,
                          max_hits=max_candidates, regions=regions):
        if verify_candidate(proc, addr, header):
            out.append(MetadataMirror(addr, header))
    return out


#: 默认 metadata 路径（Steam 安装目录下的相对位置）
METADATA_RELPATH = os.path.join('masterduel_Data', 'il2cpp_data', 'Metadata',
                                'global-metadata.dat')


def default_metadata_path(game_dir: str) -> str:
    """拼出 metadata 的默认绝对路径。"""
    return os.path.join(game_dir, METADATA_RELPATH)


#: Il2CppClass 里 name 字段的偏移。**已实机验证**（2026-09-12，游戏主界面）：
#: 命中点减去 0x10 就是 TypeInfo 基址，此时布局为
#:     image@0x00 / gc_desc@0x08 / name@0x10 / namespaze@0x18
#: 与 v31 的 Il2CppClass 定义一致。
#:
#: ⚠️ **TypeInfo 在 IL2CPP 的堆上，不在 GameAssembly.dll 的映射里**
#: （实测堆地址 0x1BDD... vs dll 基址 0x7FFE...）。
#: 所以搜索**不能**限定在 dll 范围内 —— 限定住必然 0 命中，必须全进程扫。
#:
#: 未命中时的正确解读：这些类还没被 CLR 初始化（name 字段仍是 0），
#: 不是布局错了。IL2CPP 是 lazy 的，进一次对局再试。
TYPEINFO_NAME_OFFSET = 0x10
#: namespaze 紧跟 name
TYPEINFO_NS_OFFSET = 0x18


def typeinfo_pattern(mirror: 'MetadataMirror', table: bytes,
                     namespace: str, name: str) -> Optional[bytes]:
    """拼出「name 指针 + namespaze 指针」这 16 字节特征。

    :return: 特征字节；任一名字查不到就返回 ``None``
    """
    a = mirror.string_addr(name, table)
    b = mirror.string_addr(namespace, table)
    if a is None or b is None:
        return None
    return struct.pack('<QQ', a, b)


def find_typeinfo(proc: 'SafeProcess', mirror: 'MetadataMirror', table: bytes,
                  namespace: str, name: str, regions=None,
                  max_hits: int = 8, verify: bool = True) -> List[int]:
    """定位 ``Il2CppClass``（TypeInfo）的进程地址。

    原理：运行时 ``Il2CppClass.name`` / ``.namespaze`` 指向 **metadata 镜像**里的
    字符串（已验证：这些名字在 GameAssembly.dll 里根本不存在，只在 metadata 里）。
    所以先算出两个字符串的进程地址，再搜谁**相邻存着这两个指针**。

    ⚠️ 注意参数顺序是 **namespace 在前、name 在后**（跟 C# 的
    ``Namespace.Class`` 反过来，跟 IL2CPP 结构体里的顺序一致）。
    写反了不会报错 —— 只是拿错的两个字符串去拼特征，搜回一堆垃圾。
    所以默认开了 ``verify``，把回读校验不过的候选直接滤掉。

    :param verify: True（默认）时每个候选都要通过 :func:`verify_typeinfo`
    :return: TypeInfo 基址列表（通常 0 或 1 个）
    """
    pat = typeinfo_pattern(mirror, table, namespace, name)
    if pat is None:
        return []
    out: List[int] = []
    # 噪声会消耗扫描配额，所以扫描时放宽，校验后才算数
    scan_cap = None if max_hits is None else max(max_hits, 1) * 8 + 8
    for hit in proc.scan(pat, max_hits=scan_cap, regions=regions):
        ti = hit - TYPEINFO_NAME_OFFSET
        if verify and not verify_typeinfo(proc, ti, mirror, table,
                                          namespace, name):
            continue
        out.append(ti)
        if max_hits is not None and len(out) >= max_hits:
            break
    return out


def verify_typeinfo(proc: 'SafeProcess', typeinfo: int, mirror: 'MetadataMirror',
                    table: bytes, namespace: str, name: str) -> bool:
    """回读校验：这个地址上真的是我们要的那个类吗？

    不看偏移量对不对，而是把 name / namespaze 两个指针读回来，
    再顺着指针把字符串读出来比对 —— 全对上才算数。
    """
    try:
        p_name = proc.read_ptr(typeinfo + TYPEINFO_NAME_OFFSET)
        p_ns = proc.read_ptr(typeinfo + TYPEINFO_NS_OFFSET)
    except Exception:
        return False
    got_name = _read_str_at(proc, p_name, len(name))
    got_ns = _read_str_at(proc, p_ns, len(namespace))
    return got_name == name and got_ns == namespace


def _read_str_at(proc: 'SafeProcess', addr: int, n: int) -> Optional[str]:
    if not addr:
        return None
    try:
        raw = proc.read(addr, n)
    except Exception:
        return None
    return raw.split(b'\x00', 1)[0].decode('utf-8', 'replace')

# ------------------------------------------------------------------
# v31 Il2CppClass 布局 —— **实机 dump 校准过**（2026-09-12，游戏主界面，
# 拿 YgomGame.Duel.Engine 的 TypeInfo 逐字段看过）
#
# 校准点是 +0x68 的 typeDefinition：它指向 metadata 镜像内部，
# 在几十 GB 的地址空间里，落进那 17.7MB 区间的指针基本只有它一个。
# 它一对上，前后字段就都坐实了。
# ------------------------------------------------------------------
TYPEINFO_IMAGE_OFFSET = 0x00
TYPEINFO_GCDESC_OFFSET = 0x08
TYPEINFO_NS_OFFSET = 0x18
TYPEINFO_TYPEDEF_OFFSET = 0x68      # 指向 metadata 镜像，最强的校准点
# ⚠️ fields / properties 是**连续的结构数组**，不是指针数组（踩过这个坑：
#    按步长 8 扫只会读到 0 个字段）。methods / nestedTypes 才是指针数组。
TYPEINFO_FIELDS_OFFSET = 0x80       # FieldInfo[]，步长 0x20
TYPEINFO_EVENTS_OFFSET = 0x88
TYPEINFO_PROPERTIES_OFFSET = 0x90   # PropertyInfo[]，步长 0x28，name@+0x08
TYPEINFO_METHODS_OFFSET = 0x98      # const MethodInfo** 指针数组，步长 8
TYPEINFO_NESTEDTYPES_OFFSET = 0xA0  # Il2CppClass** 指针数组，步长 8
#: 下面这几个是**布局推出来的**，不是直接 dump 校准的 —— 但可信度很高：
#:   * 上面 0x68/0x80/0x88/0x90/0x98/0xA0 六个位置全部实机校准过，
#:     与 IL2CPP v31 的官方布局逐项吻合，往下推的偏移就站得住；
#:   * 实测 +0xA8 在「没实现接口」的类上指向 GameAssembly.dll 里的共享空表
#:     （多个类同一个值），在「有接口」的类上指向堆上新建的数组 ——
#:     正是 implementedInterfaces 该有的样子。
TYPEINFO_IMPLEMENTED_INTERFACES_OFFSET = 0xA8  # Il2CppClass**，步长 8
TYPEINFO_INTERFACE_OFFSETS_OFFSET = 0xB0       # Il2CppRuntimeInterfaceOffsetPair[]
#: ★ 静态数据区基址。2026-09-12 实机验证：YgomGame.Duel.Engine 的静态块
#:   里 +0x08 正是 s_instance 的槽位（FieldInfo.offset 也是 0x8），
#:   主界面下值为 0 —— 因为 Engine 是对局引擎，主界面不建实例。
TYPEINFO_STATIC_FIELDS_OFFSET = 0xB8
TYPEINFO_RGCTX_OFFSET = 0xC0                   # const Il2CppRGCTXData*

#: 对象头大小：+0x00 是 klass 指针，+0x08 是 monitor。实例数据从 +0x10 起。
#: 所以「FieldInfo.offset < 0x10」基本可以断定是静态字段
#: （实例字段不可能落在对象头里）。
#: ⚠️ 编译期 const 的 offset 一律填 0 —— IL2CPP 直接内联了，不占静态块，
#:    所以「静态块里 offset=0 的位置」读到的值没有意义。
INSTANCE_DATA_START = 0x10

FIELDINFO_STRIDE = 0x20             # name(8)+type(8)+parent(8)+offset(4)+token(4)
PROPERTYINFO_STRIDE = 0x28
PROPERTYINFO_NAME_OFFSET = 0x08

#: FieldInfo 的布局（v31）。**字段偏移就在这个结构里** ——
#: 所以「拿字段偏移必须去解析 GameAssembly.dll 的 registration 结构」
#: 这个判断是**错的**，进程外只读就能拿到。
FIELDINFO_NAME_OFFSET = 0x00        # const char*，指向 metadata 镜像
FIELDINFO_TYPE_OFFSET = 0x08
FIELDINFO_PARENT_OFFSET = 0x10
FIELDINFO_OFFSET_OFFSET = 0x18      # int32 —— 字段在实例里的字节偏移
FIELDINFO_TOKEN_OFFSET = 0x1C


class FieldEntry(NamedTuple):
    """一个字段：名字 + 在对象里的字节偏移 + metadata token。

    ``offset`` 对静态字段是相对 ``static_fields`` 区的偏移，
    对实例字段是相对对象头的偏移 —— 光看 offset 分不出来，
    得结合 C# 源码或实际读值判断。
    """

    name: str
    offset: int
    token: int
    address: int


def list_fields(proc: 'SafeProcess', typeinfo: int, mirror=None,
                limit: int = 500, strict: bool = True) -> List[FieldEntry]:
    """列出一个类的所有字段（名字 + 偏移 + token）。

    ``Il2CppClass.fields`` 指向**连续的 FieldInfo 结构数组**（步长 0x20），
    数组没有显式长度 —— 一直读到「名字不是一个合法的 metadata 字符串」为止。
    给了 ``mirror`` 就顺带校验名字落在 metadata 镜像内，能挡住跑飞。

    :param typeinfo: TypeInfo 基址（:func:`find_typeinfo` 的返回值）
    :param mirror: :class:`MetadataMirror`，用来校验字段名的合法性
    :param strict: True 时名字必须落在 metadata 镜像内才算数
    :return: 字段列表；读不出任何字段时是空列表
    """
    out: List[FieldEntry] = []
    try:
        arr = proc.read_ptr(typeinfo + TYPEINFO_FIELDS_OFFSET)
    except Exception:
        return out
    if not arr:
        return out
    lo = hi = 0
    if mirror is not None:
        # 字段名的落点**不一定在字符串表里**（那只是 metadata 的一段），
        # 所以校验边界放宽到「整份 metadata 镜像」：从 base 到字符串表末尾。
        # ★ BUG-B3-16：这里原来先 `lo, hi = mirror.string_table_span` 再整段
        #   覆盖成上面这两个值 —— 第一行赋值是死代码，读起来像有分支。
        lo = mirror.base
        hi = mirror.base + (mirror.header.string_offset
                            + mirror.header.string_size)

    for i in range(limit):
        base = arr + i * FIELDINFO_STRIDE
        try:
            name_ptr = proc.read_ptr(base + FIELDINFO_NAME_OFFSET)
            off = struct.unpack('<i', proc.read(base + FIELDINFO_OFFSET_OFFSET, 4))[0]
            tok = struct.unpack('<I', proc.read(base + FIELDINFO_TOKEN_OFFSET, 4))[0]
        except Exception:
            break
        if not name_ptr:
            break
        if strict and lo:
            if not (lo <= name_ptr < hi):
                break
        name = _read_str_at(proc, name_ptr, 64)
        if name is None:
            break
        out.append(FieldEntry(name, off, tok, base))
    return out


def static_fields_addr(proc: 'SafeProcess', typeinfo: int) -> int:
    """一个类静态数据区的基址。

    :return: 基址；没有静态数据或读失败时是 0
    """
    try:
        return proc.read_ptr(typeinfo + TYPEINFO_STATIC_FIELDS_OFFSET) or 0
    except Exception:
        return 0


def read_static_value(proc: 'SafeProcess', typeinfo: int, offset: int,
                      size: int = 8) -> Optional[bytes]:
    """读静态区里 ``offset`` 处的 ``size`` 字节。

    :return: 字节串；类没有静态区或读失败时是 None
    """
    base = static_fields_addr(proc, typeinfo)
    if not base:
        return None
    try:
        return proc.read(base + int(offset), int(size))
    except Exception:
        return None


def read_singleton_slot(proc: 'SafeProcess', typeinfo: int,
                        slot: int = 0x08):
    """读单例槽位 -> ``(对象地址, 读成功没)``。**区分「读不到」与「没有实例」**。

    ★ 2026-10-14 实机事故催生：`find_singleton` 把「内存读取失败」（静态区
    基址失效 / ReadProcessMemory 报错）和「槽位就是 0」（实例真的还没建）
    **都返回 0**。采集器据此判「不在对局」，于是**定位失效时静默漏局**
    （2026-10-14 19:15 起漏了 1 局以上，日志里毫无痕迹）。

    返回三态：

    * ``(0, False)`` —— **读不到**（读取失败）→ 调用方该考虑重新 attach；
    * ``(0, True)`` —— 读到了，槽位就是 0（如大厅里 `Duel.Engine` 还没建）；
    * ``(addr, True)`` —— 对象地址（已过对象头校验）。
    """
    try:
        raw = read_static_value(proc, typeinfo, slot, 8)
    except Exception:                                        # noqa: BLE001
        return 0, False
    if not raw or len(raw) < 8:
        return 0, False
    obj = struct.unpack_from('<Q', raw, 0)[0]
    if not obj:
        return 0, True                       # 读到了，就是没有实例
    try:
        head = proc.read_ptr(obj)
    except Exception:                                        # noqa: BLE001
        return 0, False                      # 指针读不动 = 读不到
    return (obj, True) if head == typeinfo else (0, True)


def find_singleton(proc: 'SafeProcess', typeinfo: int,
                   slot: int = 0x08) -> int:
    """拿一个类的单例对象（读静态区里那个指向本类对象的槽位）。

    很多管理器类（``Engine``、``DuelClient``...）都长成
    ``static Xxx s_instance;`` 的样子。它的槽位偏移可以从
    :func:`list_fields` 查到（名字通常叫 ``s_instance`` / ``instance``），
    静态区基址由 ``Il2CppClass.static_fields`` 给出。

    读出来会**校验对象头**：``*(obj + 0)`` 必须等于 ``typeinfo``，
    防止读到一个野指针就当宝贝用。

    ⚠️ **返回 0 有两种含义**（「读不到」与「没有实例」）—— 需要区分时用
    :func:`read_singleton_slot`（2026-10-14 起）。

    :param slot: 单例字段在静态区里的字节偏移（默认 0x08，实测最常见）
    :return: 对象地址；还没建实例 / 读不到 / 校验不过时是 0
    """
    return read_singleton_slot(proc, typeinfo, slot)[0]
