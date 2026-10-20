# -*- coding: utf-8 -*-
"""IL2CPP 堆对象的通用下钻助手（Dictionary / List / 装箱基元 / System.String）。

client_work（卡组快照）与 duel_settings（对局设置）都要「顺指针走进
``Dictionary<string, object>`` / ``List<T>``」—— 这层通用逻辑抽在这里，
**别抄第二份**（双实现漂移本项目踩过）。全程只读、全程容错：
读不到给 None / []，不抛异常（调用方每 tick 都在跑，不能炸）。

布局依据（2026-09-12/13 实机校准，tmp/read_my_deck.py / tmp/dig_settings*.py 同源）：
* ``Dictionary<string, V>``：entry = {hashCode(4), next(4), key(8), value(8)}
  = 24 字节，数据区从 ``_entries`` 数组的 +0x20 起；
* ``List<T>``：``_items`` 数组（长度在 +0x18、元素从 +0x20 起、引用类型每项 8 字节）；
* ``System.String``：长度 i32 在 +0x10、UTF-16LE 数据在 +0x14
  （⚠️ loglabel/卡组名都是它，不是 C 字符串 —— 当年用 C 读法只拿到垃圾）；
* 装箱基元：对象头 +0x10 起放值，类名（``Int64``/``Int32``）在对象头指向的
  TypeInfo +0x10 的 char* 上。
"""
import struct

#: Dictionary entry 的元素尺寸与数据区偏移
DICT_ENTRY_STRIDE = 24
DICT_DATA_OFFSET = 0x20
#: List 元素上限（防把垃圾指针当数组读爆）
LIST_MAX = 4096
#: Dictionary 条目上限（同上）
DICT_MAX = 4096
#: 指针/地址的最小合法值（更小的当 0/垃圾处理）
MIN_ADDR = 0x10000


def read_string(proc, addr, maxlen=200, min_printable=0.7):
    """System.String（UTF-16LE，长度 i32 在 +0x10、数据在 +0x14）-> str 或 None。"""
    if not addr or addr < MIN_ADDR:
        return None
    try:
        n = struct.unpack('<i', proc.read(addr + 0x10, 4))[0]
        if n <= 0 or n > maxlen:
            return None
        s = proc.read(addr + 0x14, n * 2).decode('utf-16-le', 'replace')
    except Exception:
        return None
    if not s:
        return None
    printable = sum(1 for c in s if c.isprintable())
    if printable < len(s) * min_printable:
        return None
    return s


def class_name_of(proc, obj):
    """对象头指向的 TypeInfo 的类名（``Il2CppClass.name`` 是 char*，在 +0x10）。"""
    if not obj or obj < MIN_ADDR:
        return ''
    try:
        ti = proc.read_ptr(obj)
        return proc.read_cstring(proc.read_ptr(ti + 0x10), 128)
    except Exception:
        return ''


#: 装箱基元按**类名**决定的读取宽度（★ BUG-B3-09：原来 Int32 也按 8 字节读，
#: `+0x14` 是对齐填充、不保证为 0，会把邻字节算进值里）。
#: 范本是 `peak_rate._val`（:110-115）。
BOXED_WIDTHS = {
    'Int64': (8, '<q'),
    'UInt64': (8, '<Q'),
    'Int32': (4, '<i'),
    'UInt32': (4, '<I'),
}


def boxed_int(proc, obj):
    """装箱整数 -> 数值；其它类型返回 None（不硬猜）。宽度按类名取。"""
    if not obj or obj < MIN_ADDR:
        return None
    spec = BOXED_WIDTHS.get(class_name_of(proc, obj))
    if spec is None:
        return None
    size, fmt = spec
    try:
        raw = proc.read(obj + 0x10, size)
    except Exception:
        return None
    if not raw or len(raw) < size:
        return None
    return struct.unpack(fmt, raw)[0]


def _fields_of(proc, mirror, obj):
    """对象所属类的字段表 {名字: 偏移}（对象头 -> TypeInfo -> FieldInfo[]）。"""
    from core.memread.il2cpp import list_fields
    return {f.name: f.offset
            for f in list_fields(proc, proc.read_ptr(obj), mirror=mirror)}


def dict_items(proc, mirror, obj):
    """``Dictionary<string, V>`` -> [(key, value_addr)]；不是字典给 None。

    空字典 / count 越界给 []（区别于「根本不是字典」的 None）。
    """
    if not obj or obj < MIN_ADDR:
        return None
    try:
        fl = _fields_of(proc, mirror, obj)
        if '_entries' not in fl or '_count' not in fl:
            return None
        entries = proc.read_ptr(obj + fl['_entries'])
        count = struct.unpack('<i', proc.read(obj + fl['_count'], 4))[0]
    except Exception:
        return None
    if not entries or entries < MIN_ADDR or count <= 0 or count > DICT_MAX:
        return []
    out = []
    for i in range(count):
        base = entries + DICT_DATA_OFFSET + i * DICT_ENTRY_STRIDE
        try:
            raw = proc.read(base, DICT_ENTRY_STRIDE)
        except Exception:
            continue
        # ★ BUG-B3-08：短读时 unpack_from 会抛 struct.error 逃出本函数
        #   （契约是「读不到给 None / []，不抛」；所有调用方都得自己兜）。
        if not raw or len(raw) < DICT_ENTRY_STRIDE:
            continue
        try:
            key = read_string(proc, struct.unpack_from('<Q', raw, 8)[0], 80)
            val = struct.unpack_from('<Q', raw, 16)[0]
        except struct.error:
            continue
        out.append((key, val))
    return out


def list_items(proc, mirror, obj):
    """``List<T>``（引用类型元素）-> [元素地址]；不是列表给 None。"""
    if not obj or obj < MIN_ADDR:
        return None
    try:
        fl = _fields_of(proc, mirror, obj)
        if '_items' not in fl or '_size' not in fl:
            return None
        items = proc.read_ptr(obj + fl['_items'])
        size = struct.unpack('<i', proc.read(obj + fl['_size'], 4))[0]
    except Exception:
        return None
    if not items or items < MIN_ADDR or size <= 0 or size > LIST_MAX:
        return []
    out = []
    for i in range(size):
        try:
            out.append(proc.read_ptr(items + 0x20 + i * 8))
        except Exception:
            # ★ BUG-B3-10：读失败给 **None 占位**（不是 0）—— 0 与「这张卡 id
            #   真是 0」无法区分，而 None 一眼能看出是"没读到"。
            out.append(None)
    return out


def int_list(proc, mirror, obj):
    """``List<boxed Int64>`` -> [数值]（读不出的项**丢掉**，不再填 0）。

    ★ BUG-B3-10：原来读失败填 0，`client_work.flatten_ids` 原样透传，于是
    ``my_deck_snapshot.ids`` 里混进 0 —— `infer_seat` 拿它做交集时永远不匹配，
    而且无法区分「读失败」与「卡 id 是 0」。现在失败项直接跳过。
    """
    its = list_items(proc, mirror, obj) if obj and obj >= MIN_ADDR else None
    if not its:
        return []
    out = []
    for it in its:
        if not it or it < MIN_ADDR:
            continue
        v = boxed_int(proc, it)
        if v is not None:
            out.append(v)
    return out
