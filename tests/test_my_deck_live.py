# -*- coding: utf-8 -*-
"""my_deck_live 单测：三槽位回落顺序 + 优先级（FakeMem 整链）。"""
import io
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tests.test_client_work import (FakeMem, ObjProc, build_named_typeinfo,
                                    build_str_dict, build_string,
                                    build_ref_list, build_boxed_int)
from core.memread import my_deck_live
from core.memread import client_work as cw
from core import my_deck
import tempfile
import shutil


def _build(mem, *, ladder=None, peak=None, cup=None):
    """搭 s_data：天梯 Deck/DeckList + 巅峰 ratedeck/DeckList + CUPDeckList。

    ladder/peak/cup = (deck_name, main_ids, extra_ids)；None = 容器不给。
    巅峰走 Deck.ratedeck_id（与天梯同容器不同字段），卡表共用 DeckList[id]。
    """
    cw_ti = build_named_typeinfo(mem, 'ClientWork', [('s_data', 0x08)])
    sb = mem.alloc(0x40)
    mem.put_u64(cw_ti, 0xB8, sb)

    kv = {}
    dl_entries = {}
    maindeck = None
    ratedeck = None
    if ladder is not None:
        maindeck = '100'
        name, m, e = ladder
        kv['Deck'] = build_str_dict(mem, {
            'maindeck_id': build_string(mem, maindeck),
            'list': build_str_dict(mem, {maindeck: build_str_dict(
                mem, {'name': build_string(mem, name)})})})
        dl_entries[maindeck] = build_str_dict(mem, {
            'm': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])}),
            'e': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})})
    if peak is not None:
        ratedeck = '200'
        name, m, e = peak
        deck_inner = {'ratedeck_id': build_string(mem, ratedeck)}
        if 'Deck' in kv:
            pass    # 同一 Deck 容器加字段
        else:
            kv['Deck'] = build_str_dict(mem, {})
        for k2, v2 in (dict_items := None) or []:
            pass
        # 重新构造带两个字段的 Deck（简单起见：peak 在 ladder 之后重搭）
        name_l = None
        if ladder is not None:
            name_l = ladder[0]
        inner = {}
        if maindeck:
            inner['maindeck_id'] = build_string(mem, maindeck)
        inner['ratedeck_id'] = build_string(mem, ratedeck)
        lst = {}
        if name_l:
            lst[maindeck] = build_str_dict(mem, {'name': build_string(mem, name_l)})
        lst[ratedeck] = build_str_dict(mem, {'name': build_string(mem, name)})
        inner['list'] = build_str_dict(mem, lst)
        kv['Deck'] = build_str_dict(mem, inner)
        dl_entries[ratedeck] = build_str_dict(mem, {
            'm': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])}),
            'e': build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})})
    if kv.get('Deck') or dl_entries:
        kv['DeckList'] = build_str_dict(mem, dl_entries)
    if cup is not None:
        name, m, e = cup
        parts = {}
        if m:
            parts['m'] = build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in m])})
        if e:
            parts['e'] = build_str_dict(mem, {'ids': build_ref_list(
                mem, [build_boxed_int(mem, v) for v in e])})
        kv['CUPDeckList'] = build_str_dict(mem, parts)
    sdata = build_str_dict(mem, kv)
    mem.put_u64(sb, 0x08, sdata)
    return ObjProc(mem), cw_ti


class TestAnyModeFallback(unittest.TestCase):

    def test_cup_wins_when_present(self):
        """DC 杯卡表有值 = 用户在 DC 杯语境 -> 最具体者胜出。"""
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]),
                             cup=('reg', [20500], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertEqual(snap['container'], 'CUPDeck')
        self.assertEqual(snap['ids'], [20500, 14990])

    def test_peak_when_no_cup(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]),
                             peak=('狱神耀圣', [20500], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        # 顺序 = cupdeck -> ratedeck -> 常规；ratedeck 先于常规命中
        self.assertEqual(snap['container'], 'Ratedeck')
        self.assertEqual(snap['deck_name'], '狱神耀圣')

    def test_ladder_fallback(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem, ladder=('闪刀姬', [9279], [14990]))
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertEqual(snap['deck_name'], '闪刀姬')
        self.assertIsNone(snap.get('container'))    # 常规快照无 container（天梯在可信集）


class TestReadMyDeckLive(unittest.TestCase):
    """一步式入口的写回行为（真进程路径打桩不了 —— 只测容器全空时的错误）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.old = my_deck._state_path() if hasattr(my_deck, '_state_path') else None

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_empty_containers_report_nothing(self):
        mem = FakeMem()
        proc, cw_ti = _build(mem)                       # 三个容器全不给
        snap = my_deck_live.read_current_deck_any_mode(proc, None, 'tbl',
                                                       cw_ti=cw_ti)
        self.assertIsNone(snap)
        self.assertEqual(my_deck_live.ERR_NOTHING,
                         '没读到卡组 —— 请进一次游戏主界面（或卡组编辑界面）再刷新')


if __name__ == '__main__':
    unittest.main()


class TestLocateCache(unittest.TestCase):
    """定位缓存 —— 用户 2026-10-28 实报「点刷新卡 10 秒」的修复锁。

    实测成本（真机）：find_game_metadata 3.75s + find_clientwork 6.11s，
    真读卡组只要 0.01s。所以缓存的是**路**（mirror/stbl/TypeInfo），不是值。

    这里用打桩替换两个贵的函数，验证「第二次不再真扫」。
    """

    def setUp(self):
        my_deck_live.clear_cache()
        self.scans = {'metadata': 0, 'clientwork': 0}
        self._orig = (my_deck_live.find_game_metadata,
                      my_deck_live.cw.find_clientwork)

    def tearDown(self):
        (my_deck_live.find_game_metadata,
         my_deck_live.cw.find_clientwork) = self._orig
        my_deck_live.clear_cache()

    def _stub(self, metadata_result, cw_ti=0x1234):
        def fake_meta(proc):
            self.scans['metadata'] += 1
            return metadata_result

        def fake_cw(proc, mirror, stbl):
            self.scans['clientwork'] += 1
            return cw_ti
        my_deck_live.find_game_metadata = fake_meta
        my_deck_live.cw.find_clientwork = fake_cw

    def test_second_call_reuses_cache(self):
        """第二次调用不该再扫 metadata / 类名。"""
        class M(object):
            base = 0x10000000

        self._stub((None, M(), b'tbl'))
        proc = _AliveProc()
        my_deck_live._locate(proc, pid=1234)
        first = dict(self.scans)
        self.assertEqual(first, {'metadata': 1, 'clientwork': 1})
        my_deck_live._locate(proc, pid=1234)
        self.assertEqual(self.scans, first, '第二次不该重扫')

    def test_pid_change_forces_rescan(self):
        """游戏重启（pid 变）-> 必须重扫（地址空间全变了）。"""
        class M(object):
            base = 0x10000000

        self._stub((None, M(), b'tbl'))
        proc = _AliveProc()
        my_deck_live._locate(proc, pid=1234)
        n = self.scans['metadata']
        my_deck_live._locate(proc, pid=9999)
        self.assertEqual(self.scans['metadata'], n + 1, 'pid 变了必须重扫')

    def test_dead_typeinfo_rescans_clientwork_only(self):
        """TypeInfo 失效但镜像还在 -> 只重扫类名（省掉 3.75s 的镜像扫描）。"""
        class M(object):
            base = 0x10000000

        self._stub((None, M(), b'tbl'))
        proc = _AliveProc()
        my_deck_live._locate(proc, pid=1234)
        before = dict(self.scans)
        proc.typeinfo_alive = False          # 模拟 TypeInfo 失效
        my_deck_live._locate(proc, pid=1234)
        self.assertEqual(self.scans['metadata'], before['metadata'],
                         '镜像还在就不该重扫 metadata')
        self.assertEqual(self.scans['clientwork'], before['clientwork'] + 1,
                         'TypeInfo 失效要重扫类名')

    def test_metadata_failure_not_cached(self):
        """定位失败不缓存 —— 下次要能重试（游戏可能刚加载完）。"""
        self._stub(None)                    # 找不到镜像
        proc = _AliveProc()
        my_deck_live._locate(proc, pid=1234)
        my_deck_live._locate(proc, pid=1234)
        self.assertEqual(self.scans['metadata'], 2, '失败不该缓存')

    def test_clear_cache_forces_rescan(self):
        class M(object):
            base = 0x10000000

        self._stub((None, M(), b'tbl'))
        proc = _AliveProc()
        my_deck_live._locate(proc, pid=1234)
        n = self.scans['metadata']
        my_deck_live.clear_cache()
        my_deck_live._locate(proc, pid=1234)
        self.assertEqual(self.scans['metadata'], n + 1)


class _AliveProc(object):
    """假进程：``read``/``read_ptr`` 永远返回非空（模拟「镜像/TypeInfo 还活着」）。"""

    def __init__(self, typeinfo_alive=True):
        self.typeinfo_alive = typeinfo_alive

    def read(self, addr, size):
        return b'\x01' * size

    def read_ptr(self, addr):
        return 0x10000000 if self.typeinfo_alive else 0


class TestCliEntry(unittest.TestCase):
    """CLI 入口（UI 用 QProcess 异步调它）—— 输出必须是一行可解析 JSON。"""

    def test_main_writes_json_line(self):
        import json
        orig = my_deck_live.read_my_deck_live
        my_deck_live.read_my_deck_live = lambda: (
            {'name': '烙印', 'container': 'Deck'}, '')
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = my_deck_live.main([])
        finally:
            sys.stdout = old
            my_deck_live.read_my_deck_live = orig
        self.assertEqual(code, 0)
        rec = json.loads(buf.getvalue().strip())
        self.assertTrue(rec['ok'])
        self.assertEqual(rec['name'], '烙印')
        self.assertEqual(rec['container'], 'Deck')

    def test_main_reports_error(self):
        import json
        orig = my_deck_live.read_my_deck_live
        my_deck_live.read_my_deck_live = lambda: (None, '没读到卡组')
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = my_deck_live.main([])
        finally:
            sys.stdout = old
            my_deck_live.read_my_deck_live = orig
        self.assertEqual(code, 1)
        rec = json.loads(buf.getvalue().strip())
        self.assertFalse(rec['ok'])
        self.assertEqual(rec['error'], '没读到卡组')
