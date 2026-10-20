# -*- coding: utf-8 -*-
"""礼盒读取（core/memread/present_box.py）单测。

* 纯函数：limitDate 归一 / 日期截取 / 物品标签 / 聚合 / 缓存读写；
* FakeMem 假对象图整链：按 2026-09-19 实机校准的布局搭
  VC TypeInfo（dataList）→ List`1 → Data 元素，外加「klass 相同但不是真身」
  的诱饵对象，验证 pick_instance 的甄别判据；
* 不开真进程、不联网。
"""
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(HERE), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_memread_public_cards import FakeMem  # noqa: E402
from fakemem_obj import ObjProc, build_named_typeinfo, build_ref_list, \
    build_string  # noqa: E402

from core.memread import present_box as pb  # noqa: E402
from core.memread.il2cpp import find_string_offset, TYPEINFO_NAME_OFFSET  # noqa: E402


# ------------------------------------------------------------------ 纯函数

class TestPureFunctions(unittest.TestCase):
    def test_normalize_limit(self):
        # 实机：日期与时间之间是 U+00A0
        self.assertEqual(pb.normalize_limit('2026/10/6\xa019:01'), '2026/10/6 19:01')
        self.assertEqual(pb.normalize_limit(''), '')
        self.assertEqual(pb.normalize_limit(None), '')

    def test_limit_date_part(self):
        self.assertEqual(pb.limit_date_part('2026/10/6 19:01'), '2026/10/6')
        self.assertEqual(pb.limit_date_part('2026/10/6'), '2026/10/6')

    def test_item_label(self):
        self.assertEqual(pb.item_label(1, 1), '宝石')
        self.assertEqual(pb.item_label(2, 7), '物品(cat 2 id 7)')

    def test_summarize_groups_by_day_sorted_asc(self):
        rows = [
            {'limit_date': '2026/10/9 12:40', 'quantity': 100},
            {'limit_date': '2026/10/6 19:01', 'quantity': 60},
            {'limit_date': '2026/10/9 10:12', 'quantity': 25},
        ]
        out = pb.summarize(rows)
        self.assertEqual(out['total_n'], 3)
        self.assertEqual(out['total_qty'], 185)
        self.assertEqual([d['date'] for d in out['days']],
                         ['2026/10/6', '2026/10/9'])
        self.assertEqual(out['days'][1], {'date': '2026/10/9', 'n': 2,
                                          'qty': 125, 'at': '10:12'})
        self.assertEqual(out['days'][0]['at'], '19:01')

    def test_summarize_sorts_numerically_not_lexically(self):
        # 2026-09-19 自检出图抓到：字符串序会把 10/12 排到 10/6 前面
        rows = [{'limit_date': '2026/10/12 15:14', 'quantity': 160},
                {'limit_date': '2026/10/6 19:01', 'quantity': 60},
                {'limit_date': '2026/10/9 12:40', 'quantity': 540}]
        out = pb.summarize(rows)
        self.assertEqual([d['date'] for d in out['days']],
                         ['2026/10/6', '2026/10/9', '2026/10/12'])
        self.assertEqual(out['days'][0]['qty'], 60)   # 最早到期 = 10/6

    def test_summarize_empty_and_no_limit(self):
        self.assertEqual(pb.summarize([]),
                         {'total_n': 0, 'total_qty': 0, 'days': []})
        # 多条取最早到点时刻；无时刻的给 ''
        out = pb.summarize([{'limit_date': '2026/10/9 12:40', 'quantity': 100},
                            {'limit_date': '2026/10/9 08:05', 'quantity': 20}])
        self.assertEqual(out['days'][0]['at'], '08:05')
        out2 = pb.summarize([{'limit_date': '', 'quantity': 5}])
        self.assertEqual(out2['days'][0]['at'], '')
        out = pb.summarize([{'limit_date': '', 'quantity': 5}])
        self.assertEqual(out['days'][0]['date'], '（无期限）')

    def test_cache_roundtrip_and_broken(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'cache.json')
            self.assertIsNone(pb.load_cache(path))          # 没有
            rows = [{'pID': 1, 'quantity': 60, 'limit_date': '2026/10/6 19:01'}]
            pb.save_cache(path, rows, read_at=123.5)
            got = pb.load_cache(path)
            self.assertEqual(got['read_at'], 123.5)
            self.assertEqual(got['rows'], rows)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('{ 坏 json')
            self.assertIsNone(pb.load_cache(path))          # 坏了
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump({'rows': '不是列表', 'read_at': 1}, fh)
            self.assertIsNone(pb.load_cache(path))          # 形状不对


# ------------------------------------------------------------------ FakeMem 整链

class FakeMirror(object):
    """duck-type MetadataMirror：只实现 present_box 用到的三个入口。

    字符串表就是一块连续 NUL 结尾条目（base 即表起点，string_offset=0）。
    """

    def __init__(self, mem, names):
        self.base = mem.alloc(sum(len(n) + 1 for n in names) + 8)
        blob = b''.join(n.encode('ascii') + b'\x00' for n in names)
        mem.put(self.base, 0, blob)
        self._table = blob

    class _Hdr(object):                      # list_fields 的 strict 边界用
        string_offset = 0
        #: 放宽到「base 之后都算」—— 假对象图的字段名不在表里，真实现里在
        string_size = 0x1000000

    header = _Hdr()

    def string_addr(self, name, table):
        off = find_string_offset(table, name)
        return None if off is None else self.base + off

    @property
    def string_table_span(self):
        return self.base, self.base + len(self._table)


class ScanProc(ObjProc):
    """ObjProc + scan_all（逐块字节搜索，含非对齐命中，与真实现口径一致）。"""

    def scan_all(self, pattern, max_hits=None, regions=None):
        hits = []
        for ba, buf in self.mem._blocks:
            start = 0
            while True:
                i = bytes(buf).find(pattern, start)
                if i < 0:
                    break
                hits.append(ba + i)
                start = i + 1
                if max_hits is not None and len(hits) >= max_hits:
                    return hits
        return hits


class AlignedMem(FakeMem):
    """FakeMem 的 8 字节对齐版 —— 真实 IL2CPP 堆对象都是对齐的，
    pick_instance 的 ``hit % 8`` 过滤在非对齐假地址上会把真身也滤掉。"""

    def alloc(self, size):
        return super().alloc((size + 7) // 8 * 8)


class FakeChain(object):
    """搭一条礼盒对象图：VC TypeInfo + 2 条真条目 + 1 个诱饵实例。"""

    #: 2026-09-19 实机校准的 Data 字段表
    DATA_FIELDS = [('pID', 0x10), ('itemCategory', 0x14), ('itemID', 0x18),
                   ('quantity', 0x1c), ('message', 0x20), ('limitDate', 0x28)]

    def __init__(self, items):
        self.mem = AlignedMem()
        self.proc = ScanProc(self.mem)
        self.mirror = FakeMirror(self.mem, [pb.VC_CLASS, pb.VC_NS])
        self.vc_ti = build_named_typeinfo(
            self.mem, pb.VC_CLASS, [('dataList', 0x118)])
        # name/namespaze 必须指向**字符串表**里的条目（find_vc_typeinfo 的校验口径）
        name_a = self.mirror.string_addr(pb.VC_CLASS, self.mirror._table)
        ns_a = self.mirror.string_addr(pb.VC_NS, self.mirror._table)
        self.mem.put_u64(self.vc_ti, TYPEINFO_NAME_OFFSET, name_a)
        self.mem.put_u64(self.vc_ti, TYPEINFO_NAME_OFFSET + 8, ns_a)

        data_ti = build_named_typeinfo(self.mem, pb.DATA_CLASS, self.DATA_FIELDS)
        elems = []
        for pid, cat, iid, qty, msg, lim in items:
            o = self.mem.alloc(0x38)
            self.mem.put_u64(o, 0x00, data_ti)
            self.mem.put_i32(o, 0x10, pid)
            self.mem.put_i32(o, 0x14, cat)
            self.mem.put_i32(o, 0x18, iid)
            self.mem.put_i32(o, 0x1c, qty)
            self.mem.put_u64(o, 0x20, build_string(self.mem, msg))
            self.mem.put_u64(o, 0x28, build_string(self.mem, lim))
            elems.append(o)
        lst = build_ref_list(self.mem, elems)
        self.vc_obj = self.mem.alloc(0x130)
        self.mem.put_u64(self.vc_obj, 0x00, self.vc_ti)
        self.mem.put_u64(self.vc_obj, 0x118, lst)

        # 诱饵：klass==VC_TI 但 dataList 指向的不是 List —— pick_instance 必须跳过
        self.decoy = self.mem.alloc(0x130)
        self.mem.put_u64(self.decoy, 0x00, self.vc_ti)
        self.mem.put_u64(self.decoy, 0x118, build_string(self.mem, '不是列表'))

    def items(self):
        return self.proc, self.mirror, self.mirror._table


ITEMS = [
    (268357898, 1, 1, 60, '已经达到持有上限的物品', '2026/10/6\xa019:01'),
    (268724074, 1, 1, 150, '已经达到持有上限的物品', '2026/10/9\xa012:28'),
    (1, 2, 7, 3, '别的道具', '2026/10/7\xa009:47'),
]


class TestFakeChain(unittest.TestCase):
    def setUp(self):
        self.chain = FakeChain(ITEMS)

    def test_find_vc_typeinfo(self):
        proc, mirror, stbl = self.chain.items()
        self.assertEqual(pb.find_vc_typeinfo(proc, mirror, stbl),
                         self.chain.vc_ti)

    def test_find_vc_typeinfo_miss(self):
        proc, mirror, _ = self.chain.items()
        empty = FakeMirror(self.chain.mem, ['Nothing', 'Nowhere'])
        self.assertEqual(pb.find_vc_typeinfo(proc, empty, empty._table), 0)

    def test_pick_instance_skips_decoy(self):
        proc, mirror, stbl = self.chain.items()
        ti = pb.find_vc_typeinfo(proc, mirror, stbl)
        fields = pb._field_map(proc, mirror, ti)
        self.assertEqual(pb.pick_instance(proc, mirror, ti, fields),
                         self.chain.vc_obj)
        # 诱饵自己交出去必须被拒（dataList 不是 List`1）
        self.assertNotEqual(pb.pick_instance(proc, mirror, ti, fields),
                            self.chain.decoy)

    def test_read_items_full_chain(self):
        proc, mirror, stbl = self.chain.items()
        rows, ti, state = pb.read_items(proc, mirror, stbl)
        self.assertEqual(ti, self.chain.vc_ti)
        self.assertEqual(state, 'ok')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {'pID': 268357898, 'category': 1, 'item_id': 1,
                                   'quantity': 60,
                                   'limit_date': '2026/10/6 19:01',
                                   'message': '已经达到持有上限的物品'})
        self.assertEqual(pb.item_label(rows[2]['category'], rows[2]['item_id']),
                         '物品(cat 2 id 7)')
        # 会话内缓存 TypeInfo 地址：第二次读直接命中，结果一致
        rows2, ti2, state2 = pb.read_items(proc, mirror, stbl, known_vc_ti=ti)
        self.assertEqual(rows2, rows)
        self.assertEqual(ti2, ti)
        self.assertEqual(state2, 'ok')

    def test_read_items_bad_known_ti_falls_back(self):
        proc, mirror, stbl = self.chain.items()
        rows, ti, state = pb.read_items(proc, mirror, stbl, known_vc_ti=0xDEAD0000)
        self.assertEqual(len(rows), 3)
        self.assertEqual(ti, self.chain.vc_ti)
        self.assertEqual(state, 'ok')

    def test_read_items_no_instance(self):
        # 类在、实例不在 = 没进过礼盒页：state=no_instance（区别于「礼盒真空了」）
        chain = FakeChain(ITEMS)
        chain.mem.put_u64(chain.vc_obj, 0x00, 0)   # 抹掉实例的对象头
        rows, ti, state = pb.read_items(chain.proc, chain.mirror,
                                        chain.mirror._table)
        self.assertEqual(rows, [])
        self.assertEqual(ti, chain.vc_ti)
        self.assertEqual(state, 'no_instance')

    def test_read_items_no_class(self):
        # TypeInfo 都没有 = 游戏没加载到这个界面
        proc, mirror, stbl = self.chain.items()
        rows, ti, state = pb.read_items(proc, FakeMirror(self.chain.mem,
                                                         ['A', 'B']), b'')
        self.assertEqual((rows, ti, state), ([], 0, 'no_class'))

    def test_read_items_empty_box_is_ok(self):
        # 真身在、List 空 = 礼盒真空了 —— state=ok（别跟「没进过页」混淆）
        chain = FakeChain([])
        rows, ti, state = pb.read_items(chain.proc, chain.mirror,
                                        chain.mirror._table)
        self.assertEqual((rows, state), ([], 'ok'))
        self.assertEqual(ti, chain.vc_ti)

    def test_read_element_skips_broken(self):
        proc, mirror, stbl = self.chain.items()
        ti = pb.find_vc_typeinfo(proc, mirror, stbl)
        fields = pb._field_map(proc, mirror, ti)
        obj = self.chain.mem.alloc(0x30)      # 什么都不放 —— 读炸 → None
        self.assertIsNone(pb.read_element(proc, obj, fields))

class TestPathsAndGameDir(unittest.TestCase):
    """★ B3-04 / B3-05：缓存路径跟 `paths.DATA_DIR` 走、游戏目录认 `MD_GAME_DIR`。"""

    def test_cache_path_follows_data_dir(self):
        from core import paths
        self.assertTrue(os.path.isabs(pb.CACHE_PATH),
                        'CACHE_PATH 是相对路径 —— 安装版会写到只读的安装目录')
        self.assertEqual(os.path.normcase(os.path.dirname(pb.CACHE_PATH)),
                         os.path.normcase(paths.DATA_DIR))

    def test_game_dir_honours_env(self):
        old = os.environ.get('MD_GAME_DIR')
        os.environ['MD_GAME_DIR'] = 'X:/fake/Master Duel'
        try:
            self.assertEqual(pb._candidate_game_dirs(), ['X:/fake/Master Duel'])
        finally:
            if old is None:
                os.environ.pop('MD_GAME_DIR', None)
            else:
                os.environ['MD_GAME_DIR'] = old

    def test_missing_metadata_reason_mentions_env(self):
        """目录里没有 metadata -> 提示要指向 MD_GAME_DIR，而不是「等游戏加载完」。"""
        old = os.environ.get('MD_GAME_DIR')
        os.environ['MD_GAME_DIR'] = 'X:/definitely/not/here'
        try:
            msg = pb.game_metadata_missing_reason()
        finally:
            if old is None:
                os.environ.pop('MD_GAME_DIR', None)
            else:
                os.environ['MD_GAME_DIR'] = old
        self.assertIn('MD_GAME_DIR', msg)
        self.assertNotIn('加载完', msg)




if __name__ == '__main__':
    unittest.main()
