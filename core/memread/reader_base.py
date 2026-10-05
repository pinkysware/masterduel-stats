# -*- coding: utf-8 -*-
"""只读内存读数的公共底座 —— 「按字段名白名单查偏移」的那套规矩。

**为什么要有这个基类**：本项目读游戏内存有两条不可退让的红线设计：

1. **偏移一律按字段名现查，绝不硬编码、绝不猜**
   —— 游戏一更新偏移就变，硬编码会读飞；而且读飞之后可能正好落在禁区字段上。
   查不到就抛异常，宁可不读。
2. **白名单之外的字段一概不碰**
   —— 每个 Reader 只声明自己需要的字段名，从根上碰不到
   ``core/memread/redline.py`` 里那些禁区字段。

`public_cards`（公开卡）和 `duel_state`（回合/LP）都用这一套，所以提到这里。
"""
from __future__ import annotations

from typing import Dict, Optional

from .il2cpp import FieldEntry, list_fields


class TypeInfoReader(object):
    """按字段名读 IL2CPP 对象的基类。子类声明自己的白名单即可。

    子类通常还要提供一个「主入口」方法（如 ``read``），
    负责顺着指针链把对象找出来，再交给这里的方法逐个取值。
    """

    def __init__(self, proc, mirror=None) -> None:
        self.proc = proc
        self.mirror = mirror
        self._fields_cache: Dict[int, Dict[str, FieldEntry]] = {}

    # ------------------------------------------------------------ 字段表

    def _fields_of(self, typeinfo: int) -> Dict[str, FieldEntry]:
        """某个类的「字段名 -> FieldEntry」。按 TypeInfo 缓存。"""
        if typeinfo not in self._fields_cache:
            self._fields_cache[typeinfo] = {
                f.name: f for f in
                list_fields(self.proc, typeinfo, mirror=self.mirror)
            }
        return self._fields_cache[typeinfo]

    def _offset(self, fields: Dict[str, FieldEntry], name: str,
                whitelist: tuple) -> int:
        """取字段偏移。名字不在白名单、或字段表里查不到，都抛 ``ValueError``。

        属性（property）在 IL2CPP 里会生成 ``<名字>k__BackingField``
        这样的后备字段，这里一并认。
        """
        if name not in whitelist:
            raise ValueError('字段 %r 不在白名单 %r 里' % (name, whitelist))
        f = fields.get(name) or fields.get('<%s>k__BackingField' % name)
        if f is None:
            raise ValueError(
                '类的字段表里找不到 %r —— 偏移不猜，宁可不读（字段表：%s）'
                % (name, ', '.join(sorted(fields)) or '(空)'))
        return f.offset

    # ------------------------------------------------------------ 取值

    def _typeinfo_at(self, obj: int) -> int:
        """对象的类（对象头 +0x00 就是 klass 指针）；读不到给 0。"""
        try:
            return self.proc.read_ptr(obj) or 0
        except Exception:
            return 0

    def _read_prim(self, obj: int, name: str, whitelist: tuple, fmt: str,
                   size: int, typeinfo: Optional[int] = None):
        """读一个定长标量字段；读不到给 ``None``。

        读失败给 None 而不是抛 —— 采样器每秒都在读，对局切换的一瞬间
        读不到是常态，不该让整轮采样炸掉。
        """
        import struct as _struct
        if not obj:
            return None
        if typeinfo is None:
            typeinfo = self._typeinfo_at(obj)
            if not typeinfo:
                return None
        try:
            off = self._offset(self._fields_of(typeinfo), name, whitelist)
        except ValueError:
            raise
        try:
            raw = self.proc.read(obj + off, size)
        except Exception:
            return None
        if not raw or len(raw) < size:
            return None
        return _struct.unpack_from(fmt, raw, 0)[0]

    def _i32(self, obj: int, name: str, whitelist: tuple,
             typeinfo: Optional[int] = None) -> Optional[int]:
        return self._read_prim(obj, name, whitelist, '<i', 4, typeinfo)

    def _u8(self, obj: int, name: str, whitelist: tuple,
            typeinfo: Optional[int] = None) -> Optional[int]:
        return self._read_prim(obj, name, whitelist, '<B', 1, typeinfo)

    def _ptr_by_name(self, obj: int, name: str, whitelist: tuple,
                     typeinfo: Optional[int] = None) -> int:
        """读 ``obj`` 的一个指针字段；读不到给 0（调用方按"链路断了"处理）。

        ``typeinfo`` 没给就从对象头认类。
        """
        if not obj:
            return 0
        if typeinfo is None:
            typeinfo = self._typeinfo_at(obj)
            if not typeinfo:
                return 0
        try:
            return self.proc.read_ptr(obj + self._offset(
                self._fields_of(typeinfo), name, whitelist))
        except ValueError:
            raise
        except Exception:
            return 0
