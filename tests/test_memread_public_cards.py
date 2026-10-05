# -*- coding: utf-8 -*-
"""public_cards 单测 —— 判据函数 + 假内存上的整链读取。

不依赖真实游戏：``FakeMem``/``FakeProc`` 模拟进程内存，
把 ``Engine -> threadWork -> currentEngineData -> 三张表 -> 卡对象`` 整条链
搭出来。判据用四组差分样本的**真实观测值**做回归
（出处：项目记忆 2026-09-12 第二十三节，tmp/card_snaps/）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.memread.process import MemoryReadError  # noqa: E402
from core.memread.public_cards import (  # noqa: E402
    CardTableOffsets,
    PublicCard,
    PublicCardReader,
    RawCard,
    ZONE_FIELD,
    ZONE_GRAVE,
    is_public_zone,
    read_card_table,
    read_public_cards,
    zone_of,
)


# ------------------------------------------------------------------ 假内存

class FakeMem(object):
    """按块分配的假地址空间（块间递增、不重叠）。"""

    def __init__(self):
        self._blocks = []
        self._cur = 0x1000_0000

    def alloc(self, size):
        addr = self._cur
        self._cur += max(size, 8)
        self._blocks.append((addr, bytearray(size)))
        return addr

    def put(self, addr, off, data):
        for ba, buf in self._blocks:
            if ba == addr:
                buf[off:off + len(data)] = data
                return
        raise KeyError(hex(addr))

    def put_u8(self, addr, off, v):
        self.put(addr, off, bytes([v & 0xFF]))

    def put_i32(self, addr, off, v):
        self.put(addr, off, int(v).to_bytes(4, 'little', signed=True))

    def put_u64(self, addr, off, v):
        self.put(addr, off, int(v).to_bytes(8, 'little'))

    def put_str(self, s):
        """分配一个字符串（结尾补足 64 字节可读，配合 _read_str_at 的定长读）。"""
        addr = self.alloc(len(s) + 1 + 64)
        self.put(addr, 0, s.encode('ascii') + b'\x00')
        return addr

    def load(self, addr, size):
        if size <= 0:
            return b''
        want_end = addr + size
        cov_end = addr
        for ba, buf in sorted(self._blocks, key=lambda b: b[0]):
            bs = max(ba, addr)
            be = min(ba + len(buf), want_end)
            if bs >= be:
                continue
            if bs > cov_end:
                raise MemoryReadError('未映射 0x%X' % cov_end)
            if be > cov_end:
                cov_end = be
            if cov_end >= want_end:
                break
        if cov_end < want_end:
            raise MemoryReadError('未映射 0x%X' % cov_end)
        out = bytearray(size)
        for ba, buf in self._blocks:
            bs = max(ba, addr)
            be = min(ba + len(buf), want_end)
            if bs < be:
                out[bs - addr:be - addr] = buf[bs - ba:be - ba]
        return bytes(out)


class FakeProc(object):
    """只实现 public_cards 用得到的三个读方法。"""

    def __init__(self, mem):
        self.mem = mem

    def read(self, address, size):
        return self.mem.load(address, size)

    def try_read(self, address, size):
        try:
            return self.read(address, size)
        except Exception:
            return None

    def read_ptr(self, address):
        return int.from_bytes(self.read(address, 8), 'little')


# ------------------------------------------------------------------ 场景搭建

#: 测试场景里 8 张卡（attr/is_face 全部来自真实观测值，见各类注释）
SCENE = [
    # uid, card_id, owner, is_face, attr(P0 本), 说明
    (100, 22119, 0, 1, 0x0001),   # 我方表侧在场（斯特莉西亚）
    (44, 22116, 0, 0, 0x2008),    # 我方墓地（露西娜送墓）
    (79, 5114, 1, 0, 0x8801),     # 对方里侧盖卡（opp1）
    (54, 21480, 0, 0, 0x0801),    # 我方里侧盖放（闪刀亚式）
    (64, 22533, 0, 0, 0x0802),    # 手牌/牌组区
    (12, 21059, 1, 1, 0x8041),    # 对方表侧在场（0x41 分支）
    (7, None, 0, 0, 0x0810),      # 额外区 + stProp 未填
    (55, 12345, 1, 0, None),      # 只在 P1 字典有（对方墓地 0x8000|0x2008）
]


def build_card(mem, uid, card_id, owner, is_face, n_pos=0x0005_0002):
    """搭一张卡：卡对象 + stProp，返回卡对象地址。"""
    stprop = 0
    if card_id is not None:
        stprop = mem.alloc(0x28)
        mem.put_i32(stprop, 0x10, card_id)
        mem.put_u8(stprop, 0x18, owner)
    obj = mem.alloc(0x40)
    mem.put_i32(obj, 0x14, n_pos)          # 高 16 位 = 卡序号（原样保留）
    mem.put_i32(obj, 0x18, uid)
    mem.put_u64(obj, 0x20, stprop)
    mem.put_u8(obj, 0x28, is_face)
    mem.put_u8(obj, 0x29, 0)
    return obj


def build_dict(mem, mapping):
    """搭一本 Dictionary<int,int>（entry: hash/next/key/val 各 4 字节）。"""
    d = mem.alloc(0x28)
    n = len(mapping)
    entries = mem.alloc(0x20 + 0x10 * (n + 1))
    mem.put_u64(d, 0x18, entries)
    mem.put_i32(d, 0x20, n)
    for i, (k, v) in enumerate(mapping.items()):
        e = 0x20 + i * 0x10
        mem.put_i32(entries, e + 0x00, 0x1234_5678)   # hashCode 非 0 = 有效
        mem.put_i32(entries, e + 0x08, k)
        mem.put_i32(entries, e + 0x0C, v)
    return d


def build_typeinfo(mem, fields):
    """搭假 TypeInfo：+0x80 -> FieldInfo[]（步长 0x20，末尾全 0 结束）。"""
    ti = mem.alloc(0xA0)
    arr = mem.alloc(0x20 * (len(fields) + 1))
    for i, (name, off) in enumerate(fields):
        e = i * 0x20
        mem.put_u64(arr, e + 0x00, mem.put_str(name))
        mem.put_i32(arr, e + 0x18, off)
    mem.put_u64(ti, 0x80, arr)
    return ti


def build_scene(mem, extra_p1=None, drop_uids_p0=()):
    """搭 currentEngineData + 三张表，返回 (ced, offsets, ced_typeinfo)。"""
    card_addr = {}
    for uid, cid, owner, face, attr in SCENE:
        card_addr[uid] = build_card(mem, uid, cid, owner, face)

    uid_slot = {uid: i for i, (uid, *_r) in enumerate(SCENE)}
    uid_tbl = build_dict(mem, uid_slot)

    p0 = {uid: attr for uid, _c, _o, _f, attr in SCENE
          if attr is not None and uid not in drop_uids_p0}
    p1 = dict(extra_p1 or {})
    # P1 本常规条目带 0x8000 位（与实测一致）
    for uid, _c, _o, _f, attr in SCENE:
        if attr is not None and uid not in p1:
            p1[uid] = attr | 0x8000
    # uid 55 的 attr=None 表示 P0 本没有它，只落在 P1 本（对方墓地，带 0x8000 位）
    for uid, v in {55: 0xA008}.items():
        p1.setdefault(uid, v)
    ca = mem.alloc(0x30)
    mem.put_u64(ca, 0x18, 2)
    mem.put_u64(ca, 0x20, build_dict(mem, p0))
    mem.put_u64(ca, 0x28, build_dict(mem, p1))

    n = len(SCENE)
    ub = mem.alloc(0x20 + 8 * (n + 1))
    mem.put_u64(ub, 0x18, n)
    for uid, slot in uid_slot.items():
        mem.put_u64(ub, 0x20 + slot * 8, card_addr[uid])

    ced_ti = build_typeinfo(mem, [('uidTbl', 0x28), ('uidBases', 0x30),
                                  ('cardAttribute', 0x38)])
    ced = mem.alloc(0x40)
    mem.put_u64(ced, 0x00, ced_ti)
    mem.put_u64(ced, 0x28, uid_tbl)
    mem.put_u64(ced, 0x30, ub)
    mem.put_u64(ced, 0x38, ca)
    offsets = CardTableOffsets(uid_tbl=0x28, uid_bases=0x30, card_attribute=0x38)
    return ced, offsets, ced_ti


# ------------------------------------------------------------------ 判据

class TestIsPublicZone(unittest.TestCase):
    """四组差分样本的真实观测值回归（2026-09-12，项目记忆第二十三节）。"""

    OBS = [
        # (attr, is_face, 公开?, 出处)
        (0x0001, 1, True,  '我方表侧在场（斯特莉西亚，now2 差分）'),
        (0x8001, 1, True,  '同上在 P1 字典（0x8000 位不影响判据）'),
        (0x0041, 1, True,  '对方表侧在场（沼地泥龙/迪亚波罗，now3 差分）'),
        (0x8041, 1, True,  '同上在 P0 字典'),
        (0x0801, 0, False, '我方里侧盖放（闪刀亚式，now3 差分）'),
        (0x8801, 0, False, '我方里侧盖放（P1 字典）'),
        (0x0801, 0, False, '对方里侧盖卡（opp1，P1 字典）'),
        (0x8801, 0, False, '对方里侧盖卡（opp1，P0 字典）'),
        (0x2008, 0, True,  '墓地（露西娜送墓，now2 差分）'),
        (0xA008, 0, True,  '墓地（P1 字典 0x8000|0x2008）'),
        (0x0802, 0, False, '手牌/牌组/额外（0x802 系）'),
        (0x8802, 0, False, '同上在对方那本字典'),
        (0x0804, 0, False, '手牌/牌组/额外（0x804 系）'),
        (0x0810, 0, False, '手牌/牌组/额外（0x810 系）'),
        (0x8810, 0, False, '同上在对方那本字典'),
        (None,   0, False, 'attr 缺失（不在字典里）'),
    ]

    def test_observations(self):
        for attr, face, want, why in self.OBS:
            got = is_public_zone(attr, face)
            self.assertEqual(got, want,
                             'attr=%s face=%s（%s）应为%s'
                             % (hex(attr) if attr is not None else None,
                                face, why, '公开' if want else '不公开'))

    def test_field_needs_face_up(self):
        """在场 + isFace=0（里侧）一律不公开 —— 双方盖卡都验过。"""
        self.assertFalse(is_public_zone(0x0001, 0))
        self.assertFalse(is_public_zone(0x0041, 0))
        self.assertFalse(is_public_zone(0x0801, 0))

    def test_grave_ignores_face(self):
        """墓地恒公开（没有里侧概念，isFace 不参与）。"""
        self.assertTrue(is_public_zone(0x2008, 0))
        self.assertTrue(is_public_zone(0x2008, 1))

    def test_zone_of(self):
        self.assertEqual(zone_of(0x0001), ZONE_FIELD)
        self.assertEqual(zone_of(0x8041), ZONE_FIELD)
        self.assertEqual(zone_of(0x2008), ZONE_GRAVE)
        self.assertEqual(zone_of(0xA008), ZONE_GRAVE)
        self.assertIsNone(zone_of(0x0802))
        self.assertIsNone(zone_of(None))


# ------------------------------------------------------------------ 底层读表

class TestReadCardTable(unittest.TestCase):

    def test_scene(self):
        mem = FakeMem()
        ced, offsets, _ti = build_scene(mem)
        cards = read_card_table(FakeProc(mem), ced, offsets)
        by_uid = {c.uid: c for c in cards}
        self.assertEqual(len(cards), len(SCENE))

        c = by_uid[100]
        self.assertIsInstance(c, RawCard)
        self.assertEqual(c.card_id, 22119)
        self.assertEqual(c.owner, 0)
        self.assertEqual(c.is_face, 1)
        self.assertEqual(c.attr, 0x0001)
        self.assertEqual(c.n_pos, 0x0005_0002)   # 原样返回，mask 是调用方的事

        self.assertIsNone(by_uid[7].card_id)     # stProp 未填
        # attr 只在 P1 本里的卡：两本合并后能拿到
        self.assertEqual(by_uid[55].attr, 0x2008 | 0x8000)

    def test_merged_when_missing_from_p0(self):
        """P0 本缺条目时落到 P1 本（两本低位一致，合并无冲突）。"""
        mem = FakeMem()
        ced, offsets, _ti = build_scene(mem, drop_uids_p0=(100, 44))
        cards = {c.uid: c for c in read_card_table(FakeProc(mem), ced, offsets)}
        self.assertEqual(cards[100].attr, 0x8001)   # 从 P1 本拿（带 0x8000）
        self.assertEqual(cards[44].attr, 0xA008)

    def test_empty_uid_table(self):
        mem = FakeMem()
        ced, offsets, _ti = build_scene(mem)
        # 把 uidTbl 指到一本空字典
        empty = build_dict(mem, {})
        mem.put_u64(ced, offsets.uid_tbl, empty)
        self.assertEqual(read_card_table(FakeProc(mem), ced, offsets), [])

    def test_broken_card_skipped(self):
        """单张卡对象指针为 0 / 读失败：跳过，不影响其它卡。"""
        mem = FakeMem()
        ced, offsets, _ti = build_scene(mem)
        ub = _ptr_of(mem, ced + offsets.uid_bases)
        mem.put_u64(ub, 0x20 + 0 * 8, 0)   # 槽 0（uid 100）的卡指针清 0
        cards = read_card_table(FakeProc(mem), ced, offsets)
        self.assertEqual([c.uid for c in cards if c.uid == 100], [])
        self.assertEqual(len(cards), len(SCENE) - 1)


def _ptr_of(mem, addr):
    return int.from_bytes(mem.load(addr, 8), 'little')


class TestReadPublicCards(unittest.TestCase):

    def test_scene(self):
        mem = FakeMem()
        ced, offsets, _ti = build_scene(mem)
        out = read_public_cards(FakeProc(mem), ced, offsets)
        # 公开的只有：100（我方表侧在场）、44（我方墓地）、
        # 12（对方表侧在场）、55（对方墓地，attr 只在 P1 本）
        self.assertEqual([p.uid for p in out], [12, 44, 55, 100])
        by_uid = {p.uid: p for p in out}
        self.assertEqual(by_uid[100], PublicCard(100, 22119, 0, ZONE_FIELD))
        self.assertEqual(by_uid[44], PublicCard(44, 22116, 0, ZONE_GRAVE))
        self.assertEqual(by_uid[12], PublicCard(12, 21059, 1, ZONE_FIELD))
        self.assertEqual(by_uid[55], PublicCard(55, 12345, 1, ZONE_GRAVE))
        # 里侧 / 手牌 / stProp 未填的一张都不出现
        for hidden in (79, 54, 64, 7):
            self.assertNotIn(hidden, by_uid)


# ------------------------------------------------------------------ 上层 Reader

class TestPublicCardReader(unittest.TestCase):

    def build(self):
        mem = FakeMem()
        ced, offsets, ced_ti = build_scene(mem)
        tw_ti = build_typeinfo(mem, [('currentEngineData', 0x10)])
        tw = mem.alloc(0x20)
        mem.put_u64(tw, 0x00, tw_ti)
        mem.put_u64(tw, 0x10, ced)
        eng_ti = build_typeinfo(mem, [('threadWork', 0x128)])
        eng = mem.alloc(0x140)
        mem.put_u64(eng, 0x00, eng_ti)
        mem.put_u64(eng, 0x128, tw)
        return mem, eng_ti, eng, ced

    def test_full_chain(self):
        mem, eng_ti, eng, _ced = self.build()
        r = PublicCardReader(FakeProc(mem))
        out = r.read(eng_ti, eng)
        self.assertEqual([p.uid for p in out], [12, 44, 55, 100])
        # 再读一次（字段缓存路径）
        out2 = r.read(eng_ti, eng)
        self.assertEqual(out, out2)

    def test_no_engine(self):
        mem, eng_ti, eng, _ced = self.build()
        r = PublicCardReader(FakeProc(mem))
        self.assertEqual(r.read(eng_ti, 0), [])

    def test_no_threadwork(self):
        mem, eng_ti, eng, _ced = self.build()
        mem.put_u64(eng, 0x128, 0)
        r = PublicCardReader(FakeProc(mem))
        self.assertEqual(r.read(eng_ti, eng), [])

    def test_no_current_engine_data(self):
        mem, eng_ti, eng, _ced = self.build()
        # threadWork.currentEngineData 清 0：threadWork 对象 0x10 处
        tw = _ptr_of(mem, eng + 0x128)
        mem.put_u64(tw, 0x10, 0)
        r = PublicCardReader(FakeProc(mem))
        self.assertEqual(r.read(eng_ti, eng), [])

    def test_missing_field_raises(self):
        """字段表缺白名单字段 -> ValueError（偏移不猜，宁可失败）。"""
        mem, eng_ti, eng, _ced = self.build()
        # 给 ced 换一个缺 cardAttribute 的类
        ti2 = build_typeinfo(mem, [('uidTbl', 0x28), ('uidBases', 0x30)])
        tw = _ptr_of(mem, eng + 0x128)
        ced = _ptr_of(mem, tw + 0x10)
        mem.put_u64(ced, 0x00, ti2)
        r = PublicCardReader(FakeProc(mem))
        with self.assertRaises(ValueError):
            r.read(eng_ti, eng)

    def test_read_ced_directly(self):
        mem, _eng_ti, _eng, ced = self.build()
        r = PublicCardReader(FakeProc(mem))
        out = r.read_ced(ced)
        self.assertEqual([p.uid for p in out], [12, 44, 55, 100])
        self.assertEqual(r.read_ced(0), [])


if __name__ == '__main__':
    unittest.main()
