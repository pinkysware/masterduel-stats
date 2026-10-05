# -*- coding: utf-8 -*-
"""逐卡动作流（``core/memread/duel_log``）单测 —— 全部离线。

真值表来自 ``tests/fixtures/duellog_snap/``：

* ``m_DataList_ShowAction.bin`` —— 实机抓的载荷数组原始字节（2048 B = 64 条）
* ``log_window.txt``            —— 同一局的游戏日志，两类行：

  * ``[ScryYgomDuelLog] ShowAction: {...}``  —— 日志**就是**解码真值表
    （Untapped 把内存里的同一份结构打成 JSON）
  * ``[YgomDuelState.handleAction] DRAW: cardId=... source=P0-Hand ...``
    —— Untapped 的语义化解读，用来校准位段含义

⚠️ 128 条日志 vs 118 条内存，差的 10 条是两组 ``MoveMulti: 5 entries`` ——
   Untapped 把 MoveMulti 的子条目也打成 ShowAction 行，但游戏里它们**不在**
   ``m_DataList_ShowAction``。这条也是本次校准的结论之一，有单测盯着。
"""
import json
import os
import re
import struct
import unittest

from core.memread.duel_log import (
    ACTION_KIND_NAMES, CARD_NAME_SIZE, ENTRY_SIZE, MULTI_HEADER_SIZE,
    OFF_DATAC, OFF_DATAR, PHASE_NAMES, PHASE_SIZE, TURN_SIZE, ZONE_DECK,
    ZONE_EXTRA, ZONE_FIELD_MAX, ZONE_HAND, ActionEvent, CardNameEntry,
    ChainEntry, DuelLogReader, LogDataCenter, LogDataSide, LogEntry,
    TurnRecord, action_kind_name, is_field_zone, iter_action_events,
    parse_card_names, parse_chains,
    parse_center, parse_entries, parse_entry, parse_multi_headers,
    parse_phases, parse_side, parse_turns, revealed_by, zone_name)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'fixtures', 'duellog_snap')
BIN = os.path.join(FIX, 'm_DataList_ShowAction.bin')
LOG = os.path.join(FIX, 'log_window.txt')

LINE = re.compile(
    r'^\[(\d{4}-\d\d-\d\d [\d:.]+)\]\s+\[(\w+)\s*\]\s+\[([^\]]+)\]\s+(.*)$')
MULTI = re.compile(r'MoveMulti: (\d+) entries')


def _rows():
    out = []
    with open(LOG, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = LINE.match(line.rstrip('\n'))
            if m:
                out.append(m.groups())
    return out


def log_show_actions():
    """日志里的 ShowAction 顺序表，带 ``_multi`` 标记（是否属于 MoveMulti）。"""
    acts, pending = [], 0
    for _ts, _lvl, tag, msg in _rows():
        if tag != 'ScryYgomDuelLog':
            continue
        if msg.startswith('MoveMulti'):
            mm = MULTI.search(msg)
            pending = int(mm.group(1)) if mm else 0
            continue
        if msg.startswith('ShowAction:'):
            o = json.loads(msg.split(': ', 1)[1])
            o['_multi'] = pending > 0
            if pending > 0:
                pending -= 1
            acts.append(o)
    return acts


def pure_show_actions():
    """不属于 MoveMulti 的 ShowAction —— 与 ``m_DataList_ShowAction`` 一一对应。"""
    return [a for a in log_show_actions() if not a['_multi']]


def action_pairs():
    """(ShowAction, handleAction) 配对 —— 位段语义的真值表。

    ⚠️ **必须按 FIFO 队列配**，不能拿「ShowAction 后面最近一条 handleAction」：
    连续 N 条 ShowAction 一起打印、随后才连续打印 N 条 handleAction 是常态
    （开局那 10 条抽卡就是 10 + 10），就近配会整段错位。实测分布：一条
    ShowAction 后面跟 **0 / 1 / 2** 条 handleAction 的情况都有。

    ⚠️ MoveMulti 的子条目（开局的抽卡）字段含义跟普通动作不一样
    （``source=P1-Deck`` 但 ``databyte0=13``），所以返回里带 ``_multi`` 标记，
    位段校准的用例自己决定要不要过滤。

    ⚠️ 最后按**卡 id 自洽**过滤：handleAction 报的 cardId 必须落在配到的
    ShowAction 的 dataint 位段里，否则就是漂掉的，丢掉。丢掉的不影响结论 ——
    剩下的干净样本依然三位数。
    """
    out, queue, multi = [], [], 0
    for _ts, _lvl, tag, msg in _rows():
        if tag != 'ScryYgomDuelLog' and tag != 'YgomDuelState.handleAction':
            continue
        if msg.startswith('MoveMulti'):
            mm = MULTI.search(msg)
            multi = int(mm.group(1)) if mm else 0
            continue
        if msg.startswith('ShowAction:'):
            o = json.loads(msg.split(': ', 1)[1])
            o['_multi'] = multi > 0
            if multi > 0:
                multi -= 1
            queue.append(o)
            continue
        # handleAction：只让「带 cardId= 的动作行」消耗队列，
        # "Turn N for player M" 那种不是动作，别把队列吃掉。
        if 'cardId=' not in msg or not queue:
            continue
        o = queue.pop(0)
        cid = int(re.search(r'cardId=(\d+)', msg).group(1))
        d = o['datal']
        s = parse_side(struct.pack('<iBBBB', d['dataint'], d['boolbits'],
                                   d['databyte0'], d['databyte1'],
                                   d['datatype']))
        if cid in s.card_ids() or (cid == 0 and s.card_id == 0):
            out.append((o, msg))
    return out


def log_turns():
    """日志里的 ``Turn N for player M`` —— ShowTurn 的真值表。"""
    out = []
    for _ts, _lvl, tag, msg in _rows():
        if tag != 'YgomDuelState.handleAction':
            continue
        m = re.search(r'Turn (\d+) for player (\d+)', msg)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def fixture_bin(name):
    with open(os.path.join(FIX, 'm_DataList_%s.bin' % name), 'rb') as f:
        return f.read()


def side_dict(d):
    return dict(dataint=d['dataint'] & 0xFFFFFFFF, boolbits=d['boolbits'],
                databyte0=d['databyte0'], databyte1=d['databyte1'],
                datatype=d['datatype'])


def center_dict(c):
    return dict(dataint0=c['dataint0'], dataint1=c['dataint1'],
                boolbits=c['boolbits'], databyte0=c['databyte0'],
                databyte1=c['databyte1'], datatype=c['datatype'])


def _bin():
    with open(BIN, 'rb') as f:
        return f.read()


# ---------------------------------------------------------------- 数据自检

class TestFixture(unittest.TestCase):
    """fixture 本身没坏 —— 后面的对账才可信。"""

    def test_files_exist(self):
        self.assertTrue(os.path.exists(BIN))
        self.assertTrue(os.path.exists(LOG))

    def test_log_counts(self):
        acts = log_show_actions()
        self.assertEqual(len(acts), 128)
        self.assertEqual(len(pure_show_actions()), 118)
        # 差的 10 条 = 两组 MoveMulti（每组 5 条）
        self.assertEqual(len(acts) - len(pure_show_actions()), 10)

    def test_bin_is_multiple_of_entry(self):
        raw = _bin()
        self.assertEqual(len(raw) % ENTRY_SIZE, 0)
        # 抓快照的探针按 16 字节/元素读，只覆盖到 64 条（count 实际 118）
        self.assertEqual(len(raw) // ENTRY_SIZE, 64)


# ---------------------------------------------------------------- 元素布局

class TestEntryLayout(unittest.TestCase):
    """32 字节布局 —— 与日志 JSON 逐字段对账。"""

    def test_datal_matches_log(self):
        raw, pure = _bin(), pure_show_actions()
        n = len(raw) // ENTRY_SIZE
        for k in range(n):
            s = parse_side(raw, k * ENTRY_SIZE)
            self.assertEqual(s._asdict(), side_dict(pure[k]['datal']),
                             '第 %d 条 datal 对不上' % k)

    def test_datar_matches_log(self):
        raw, pure = _bin(), pure_show_actions()
        n = len(raw) // ENTRY_SIZE
        for k in range(n):
            s = parse_side(raw, k * ENTRY_SIZE + OFF_DATAR)
            self.assertEqual(s._asdict(), side_dict(pure[k]['datar']),
                             '第 %d 条 datar 对不上' % k)

    def test_datac_matches_log(self):
        raw, pure = _bin(), pure_show_actions()
        n = len(raw) // ENTRY_SIZE
        for k in range(n):
            c = parse_center(raw, k * ENTRY_SIZE + OFF_DATAC)
            self.assertEqual(c._asdict(), center_dict(pure[k]['datac']),
                             '第 %d 条 datac 对不上' % k)

    def test_parse_entries_equals_manual(self):
        raw = _bin()
        es = parse_entries(raw)
        self.assertEqual(len(es), len(raw) // ENTRY_SIZE)
        self.assertEqual(es[0], parse_entry(raw, 0))
        self.assertEqual(es[7], parse_entry(raw, 7 * ENTRY_SIZE))

    def test_entry_size_is_32_not_8_or_16(self):
        """上一棒记的 8 字节是错的 —— 用错步长对不上，这个测试就是钉子。"""
        raw, pure = _bin(), pure_show_actions()
        pack = struct.Struct('<iBBBB')
        want = [pack.pack(a['datal']['dataint'], a['datal']['boolbits'],
                          a['datal']['databyte0'], a['datal']['databyte1'],
                          a['datal']['datatype'])
                for a in pure[:len(raw) // ENTRY_SIZE]]
        for stride in (8, 16, 24):
            got = [raw[i * stride:i * stride + 8] for i in range(len(want))]
            self.assertNotEqual(got, want,
                                '步长 %d 竟然也对得上 —— 布局要重新校准' % stride)

    def test_parse_requires_full_entry(self):
        with self.assertRaises(ValueError):
            parse_entry(b'\x00' * (ENTRY_SIZE - 1))
        with self.assertRaises(ValueError):
            parse_side(b'\x00' * 7)
        with self.assertRaises(ValueError):
            parse_center(b'\x00' * 11)

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            parse_entries(b'\x00' * ENTRY_SIZE, -1)

    def test_count_limits(self):
        raw = _bin()
        self.assertEqual(len(parse_entries(raw, 3)), 3)
        self.assertEqual(parse_entries(raw, 0), [])


# ---------------------------------------------------------------- 位段语义

class TestSideBitfields(unittest.TestCase):
    """卡 id / 归属 / 区域 —— 拿 handleAction 当真值表。"""

    def test_card_id_is_low_16_bits(self):
        """handleAction 报的 cardId == dataint 的**低** 16 位（不是高 16 位）。

        注意不能只测「cardId 在位段里」—— 高 16 位也在这个集合里，
        那等于没测。真正的判据是**低位**。
        """
        checked = low = 0
        for o, msg in action_pairs():
            if o.get('_multi'):
                continue
            m = re.search(r'cardId=(\d+)', msg)
            if not m or int(m.group(1)) == 0:
                continue
            d = o['datal']
            s = parse_side(struct.pack('<iBBBB', d['dataint'], d['boolbits'],
                                       d['databyte0'], d['databyte1'],
                                       d['datatype']))
            checked += 1
            low += (s.card_id == int(m.group(1)))
        self.assertGreater(checked, 80)
        self.assertEqual(low, checked,
                         '%d/%d 条 handleAction 的 cardId 不在 dataint 的低 16 位上'
                         % (low, checked))

    def test_databyte1_is_player(self):
        """databyte1 == 0 -> P0，== 1 -> P1（112/112）。"""
        checked = 0
        for o, msg in action_pairs():
            m = re.search(r'\b(P[01])[- ]', msg)
            if not m:
                continue
            want = 0 if m.group(1) == 'P0' else 1
            self.assertEqual(o['datal']['databyte1'], want,
                             '归属对不上：%s（databyte1=%d）'
                             % (msg, o['datal']['databyte1']))
            checked += 1
        self.assertGreaterEqual(checked, 100)

    def test_zone_codes(self):
        """13=手牌 / 14=额外 / 15=卡组 —— 与 handleAction 的 source 对账。"""
        seen = {ZONE_HAND: 0, ZONE_EXTRA: 0, ZONE_DECK: 0}
        for o, msg in action_pairs():
            # ⚠️ 抽卡是系统性例外：**所有** DRAW（含中盘单张抽）databyte0 都是 13，
            # 尽管 handleAction 报的 source 是 Deck。见 test_draw_zone_is_hand。
            if msg.startswith('DRAW'):
                continue
            m = re.search(r'source=P[01]-(\w+)', msg)
            if not m:
                continue
            z = o['datal']['databyte0']
            word = m.group(1)
            if word.startswith('Hand'):
                self.assertEqual(z, ZONE_HAND, msg)
                seen[ZONE_HAND] += 1
            elif word.startswith('Extra'):
                self.assertEqual(z, ZONE_EXTRA, msg)
                seen[ZONE_EXTRA] += 1
            elif word.startswith('Deck'):
                self.assertEqual(z, ZONE_DECK, msg)
                seen[ZONE_DECK] += 1
        for k, v in seen.items():
            self.assertGreater(v, 0, '区域 %d 一条样本都没有' % k)

    def test_draw_zone_is_hand(self):
        """抽卡一律记 13（手牌），不管 handleAction 报的 source 是 Deck。

        这条是**反例归档**，不是期望：databyte0 对抽卡来说记的是「卡到了哪儿」，
        对其它动作记的是「卡从哪儿来」。别把它当成噪声删掉。
        """
        draws = [(o, m) for o, m in action_pairs() if m.startswith('DRAW')]
        self.assertGreaterEqual(len(draws), 10)
        for o, m in draws:
            self.assertEqual(o['datal']['databyte0'], ZONE_HAND, m)
            self.assertTrue(m.endswith('-Hand'), m)

    def test_zone_helpers(self):
        self.assertEqual(zone_name(ZONE_HAND), 'Hand')
        self.assertEqual(zone_name(ZONE_EXTRA), 'Extra')
        self.assertEqual(zone_name(ZONE_DECK), 'Deck')
        self.assertEqual(zone_name(7), 'Zone7')
        self.assertTrue(is_field_zone(0))
        self.assertTrue(is_field_zone(ZONE_FIELD_MAX))
        self.assertFalse(is_field_zone(ZONE_HAND))
        self.assertFalse(is_field_zone(-1))


# ---------------------------------------------------------------- 语义属性

class TestSideHelpers(unittest.TestCase):

    def test_card_ids_dedup_and_drop_zero(self):
        s = LogDataSide(dataint=18730, boolbits=5, databyte0=13,
                        databyte1=1, datatype=1)
        self.assertEqual(s.card_id, 18730)
        self.assertEqual(s.alt_id, 0)
        self.assertEqual(s.card_ids(), (18730,))
        self.assertEqual(s.player, 1)
        self.assertEqual(s.zone, ZONE_HAND)

    def test_alt_id_is_same_card_not_second(self):
        """高 16 位是同一张卡的另一套 id —— 不是第二张卡。

        实机验证（2026-09-13 骰子拉力）：1257527515 = (19188<<16)|22747，
        两个 id 在卡名表里都是 S:P小夜骑士；上一局唯一的 hi!=lo 样本
        (18732, 23494) 也都是「对击斗魂 龙帝瓦里乌斯」。
        这里直接用卡名表做判据，比注释可靠。
        """
        from core.card_names import display
        for raw in (1227643846, 1257527515):
            s = LogDataSide(dataint=raw, boolbits=5, databyte0=13,
                            databyte1=1, datatype=1)
            lo, hi = s.card_id, s.alt_id
            self.assertNotEqual(hi, 0)
            self.assertNotEqual(lo, hi)
            self.assertEqual(display(lo, 'cn'), display(hi, 'cn'),
                             '%d 和 %d 应该是同一张卡' % (lo, hi))

    def test_card_ids_never_two(self):
        """card_ids() 只出低 16 位 —— 算上高位会多算卡（实测就多出一张）。"""
        s = LogDataSide(dataint=1257527515, boolbits=5, databyte0=13,
                        databyte1=1, datatype=1)
        self.assertEqual(s.card_id, 22747)
        self.assertEqual(s.alt_id, 19188)
        self.assertEqual(s.card_ids(), (22747,))

    def test_same_id_both_halves(self):
        # 0x53B953B9：高低 16 位相同（这张卡在两套 id 里编号一样）
        s = LogDataSide(dataint=0x53B953B9, boolbits=5, databyte0=13,
                        databyte1=1, datatype=1)
        self.assertEqual(s.card_id, 21433)
        self.assertEqual(s.card_ids(), (21433,))

    def test_is_empty(self):
        self.assertTrue(LogDataSide(0, 0, 0, 0, 0).is_empty)
        self.assertFalse(LogDataSide(0, 1, 13, 0, 1).is_empty)

    def test_entry_card_ids_merges_sides(self):
        e = LogEntry(
            datal=LogDataSide(18730, 5, 13, 1, 1),
            datar=LogDataSide(21433, 7, 4, 1, 1),
            datac=LogDataCenter(0, 0, 33, 0, 11, 3),
            padding=0)
        self.assertEqual(e.card_ids(), (18730, 21433))


# ---------------------------------------------------------------- 动作流

class TestActionStream(unittest.TestCase):

    def setUp(self):
        self.entries = parse_entries(_bin())

    def test_events_are_flat(self):
        evs = list(iter_action_events(self.entries))
        self.assertTrue(evs)
        for ev in evs:
            self.assertIsInstance(ev, ActionEvent)
            self.assertIn(ev.side, ('l', 'r'))
            self.assertGreater(ev.card_id, 0)
            self.assertIn(ev.player, (0, 1))

    def test_every_event_has_a_card(self):
        # 全 0 的那一侧不该产出事件
        for ev in iter_action_events(self.entries):
            self.assertNotEqual(ev.card_id, 0)

    def test_revealed_dedup_keeps_order(self):
        for p in (0, 1):
            ids = revealed_by(self.entries, p)
            self.assertEqual(len(ids), len(set(ids)))
            first_seen = {}
            for ev in iter_action_events(self.entries):
                if ev.player == p:
                    first_seen.setdefault(ev.card_id, ev.index)
            order = [first_seen[c] for c in ids]
            self.assertEqual(order, sorted(order))

    def test_revealed_limit(self):
        ids = revealed_by(self.entries, 0)
        if len(ids) > 3:
            self.assertEqual(revealed_by(self.entries, 0, 3), ids[:3])

    def test_revealed_by_unknown_player_is_empty(self):
        self.assertEqual(revealed_by(self.entries, 9), [])

    def test_two_players_disjoint_enough(self):
        """两边露出的卡不该完全一样（本局一方公开得多得多）。"""
        a = set(revealed_by(self.entries, 0))
        b = set(revealed_by(self.entries, 1))
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)

    def test_empty_entries(self):
        self.assertEqual(list(iter_action_events([])), [])
        self.assertEqual(revealed_by([], 0), [])


# ---------------------------------------------------------------- 实机链路（假内存）

class FakeMem(object):
    """按块分配的假地址空间（跟 test_memread_public_cards 同一套思路）。"""

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

    def put_u64(self, addr, off, v):
        self.put(addr, off, int(v).to_bytes(8, 'little'))

    def put_i32(self, addr, off, v):
        self.put(addr, off, int(v).to_bytes(4, 'little', signed=True))

    def put_str(self, s):
        addr = self.alloc(len(s) + 65)
        self.put(addr, 0, s.encode('ascii') + b'\x00')
        return addr

    def load(self, addr, size):
        if size <= 0:
            return b''
        end = addr + size
        out = bytearray(size)
        for ba, buf in self._blocks:
            bs, be = max(ba, addr), min(ba + len(buf), end)
            if bs < be:
                out[bs - addr:be - addr] = buf[bs - ba:be - ba]
        return bytes(out)


class FakeProc(object):
    def __init__(self, mem, scan_hits=()):
        self.mem = mem
        self._scan_hits = list(scan_hits)

    def read(self, address, size):
        return self.mem.load(address, size)

    def try_read(self, address, size):
        try:
            return self.read(address, size)
        except Exception:
            return None

    def read_ptr(self, address):
        return int.from_bytes(self.read(address, 8), 'little')

    def read_cstring(self, address, size=96):
        raw = self.read(address, size)
        return raw.split(b'\x00', 1)[0].decode('ascii', 'replace')

    def scan(self, pattern, **_kw):
        return list(self._scan_hits)


def build_typeinfo(mem, fields):
    """假 TypeInfo：+0x80 -> FieldInfo[]（步长 0x20，末尾全 0 结束）；
    +0x10 -> name 字符串指针。"""
    ti = mem.alloc(0xC0)
    arr = mem.alloc(0x20 * (len(fields) + 1))
    for i, (name, off) in enumerate(fields):
        e = i * 0x20
        mem.put_u64(arr, e + 0x00, mem.put_str(name))
        mem.put_i32(arr, e + 0x18, off)
    mem.put_u64(ti, 0x80, arr)
    mem.put_u64(ti, 0x10, mem.put_str('DuelLogController'))
    return ti


def build_list(mem, raw, entry=ENTRY_SIZE):
    """搭一个 ``List<T>``（值类型数组），返回 List 对象地址。"""
    items = mem.alloc(0x20 + len(raw))
    mem.put(items, 0x20, raw)
    lst = mem.alloc(0x40)
    mem.put_u64(lst, 0x00, build_typeinfo(mem, [('_items', 0x08),
                                                 ('_size', 0x10)]))
    mem.put_u64(lst, 0x08, items)
    mem.put_i32(lst, 0x10, len(raw) // entry)
    return lst


class TestDuelLogReader(unittest.TestCase):

    def _scene(self):
        mem = FakeMem()
        raw = _bin()
        ctrl_ti = build_typeinfo(mem, [('m_LogBaseItemList', 0x18),
                                       ('m_DataList_ShowAction', 0x20),
                                       ('m_Host', 0x28)])
        host_ti = build_typeinfo(mem, [])
        mem.put_u64(host_ti, 0x10, mem.put_str('DuelClient'))
        host = mem.alloc(0x40)
        mem.put_u64(host, 0x00, host_ti)
        ctrl = mem.alloc(0x60)
        mem.put_u64(ctrl, 0x00, ctrl_ti)
        mem.put_u64(ctrl, 0x20, build_list(mem, raw))
        mem.put_u64(ctrl, 0x28, host)
        return mem, ctrl_ti, ctrl

    def test_locate_and_read(self):
        mem, ctrl_ti, ctrl = self._scene()
        proc = FakeProc(mem, scan_hits=[ctrl])
        r = DuelLogReader(proc)
        self.assertTrue(r.locate(ctrl_ti))
        self.assertEqual(r.controller, ctrl)
        self.assertEqual(r.read_action_bytes(), _bin())
        self.assertEqual(len(r.read_actions()), len(_bin()) // ENTRY_SIZE)
        # 读数跟离线解析完全一致
        self.assertEqual(r.read_actions(), parse_entries(_bin()))
        self.assertEqual(r.read_actions(5), parse_entries(_bin())[:5])

    def test_revealed_live_matches_offline(self):
        """实机读数 == 离线解析（假内存跑通整条链）。"""
        mem, ctrl_ti, ctrl = self._scene()
        r = DuelLogReader(FakeProc(mem, scan_hits=[ctrl]))
        self.assertTrue(r.locate(ctrl_ti))
        off = parse_entries(_bin())
        self.assertEqual(r.revealed(0), revealed_by(off, 0))
        self.assertEqual(r.revealed(1), revealed_by(off, 1))
        self.assertEqual(len(list(r.iter_action_events())),
                         len(list(iter_action_events(off))))

    def test_locate_rejects_wrong_host(self):
        mem, ctrl_ti, ctrl = self._scene()
        # m_Host 指到一个别的类的实例上
        other_ti = build_typeinfo(mem, [])
        mem.put_u64(other_ti, 0x10, mem.put_str('SomethingElse'))
        other = mem.alloc(0x40)
        mem.put_u64(other, 0x00, other_ti)
        mem.put_u64(ctrl, 0x28, other)
        r = DuelLogReader(FakeProc(mem, scan_hits=[ctrl]))
        self.assertFalse(r.locate(ctrl_ti))
        self.assertEqual(r.read_action_bytes(), b'')
        self.assertEqual(r.read_actions(), [])

    def test_missing_payload_field_raises(self):
        """载荷字段名查不到 -> 抛，绝不猜偏移。"""
        mem = FakeMem()
        ctrl_ti = build_typeinfo(mem, [('m_Host', 0x28)])   # 没有载荷字段
        r = DuelLogReader(FakeProc(mem, scan_hits=[]))
        with self.assertRaises(ValueError):
            r.locate(ctrl_ti)

    def test_whitelist_blocks_unknown_field(self):
        mem, ctrl_ti, ctrl = self._scene()
        r = DuelLogReader(FakeProc(mem, scan_hits=[ctrl]))
        self.assertTrue(r.locate(ctrl_ti))
        with self.assertRaises(ValueError):
            r._offset(r._fields_of(ctrl_ti), 'm_LogBaseItemList',
                      ('m_DataList_ShowAction',))

    def test_read_payload_bytes_custom_size(self):
        """各数组元素尺寸不同 —— 尺寸必须能由调用方指定。"""
        mem, ctrl_ti, ctrl = self._scene()
        r = DuelLogReader(FakeProc(mem, scan_hits=[ctrl]))
        self.assertTrue(r.locate(ctrl_ti))
        self.assertEqual(r.read_payload_bytes('ShowAction'), _bin())
        self.assertEqual(r.read_payload_bytes('ShowAction', 32, 3), _bin()[:96])
        # 换个没挂的数组名 -> 按字段名查不到，抛（绝不猜偏移、绝不返回空）
        with self.assertRaises(ValueError):
            r.read_payload_bytes('ShowTurn')

    def test_not_in_duel_gives_empty(self):
        mem, ctrl_ti, _c = self._scene()
        r = DuelLogReader(FakeProc(mem, scan_hits=[]))
        self.assertFalse(r.locate(ctrl_ti))
        self.assertEqual(r.read_action_bytes(), b'')
        self.assertEqual(list(r.iter_action_events()), [])


# ---------------------------------------------------------------- 其它载荷数组

class TestOtherPayloadArrays(unittest.TestCase):
    """ShowPhase / ShowTurn / MoveMultiHeader —— 元素尺寸各不相同。"""

    def test_show_turn_matches_log(self):
        """ShowTurn 的玩家序列必须与日志的 Turn N for player M 逐条对上。"""
        want = log_turns()
        self.assertEqual(len(want), 5)
        recs = parse_turns(fixture_bin('ShowTurn'), 5)
        self.assertEqual(len(recs), 5)
        for (n, p), r in zip(want, recs):
            self.assertEqual(r.player, p, '第 %d 回合归属对不上' % n)
            self.assertEqual(r.turn_number, n)
            self.assertEqual(r.turn, n - 1)      # 内存里是 0 基

    def test_show_turn_shape(self):
        recs = parse_turns(fixture_bin('ShowTurn'), 5)
        self.assertEqual([r.player for r in recs], [1, 0, 1, 0, 1])
        self.assertEqual([r.turn for r in recs], [0, 1, 2, 3, 4])
        self.assertEqual(TURN_SIZE, 24)
        # 最后一条是唯一掉过血的：P1 的回合、LP 2900（该局判负）
        self.assertEqual(recs[-1].lp_a, 2900)
        self.assertEqual(recs[-1].lp_b, 8000)
        self.assertTrue(all(r.lp_b == 8000 for r in recs))

    def test_show_phase_is_int32(self):
        ps = parse_phases(fixture_bin('ShowPhase'), 23)
        self.assertEqual(len(ps), 23)
        self.assertTrue(all(p in PHASE_NAMES for p in ps),
                        '有阶段码不在枚举里：%s' % ps)
        # 第 1 回合：抽牌 -> 准备 -> 主要1 -> 结束（没进战斗）
        self.assertEqual(ps[:4], [0, 1, 2, 5])
        # 第 2 回合：抽牌 -> 准备 -> 主要1 -> 战斗 -> 主要2 -> 结束
        self.assertEqual(ps[4:10], [0, 1, 2, 3, 4, 5])
        self.assertEqual(PHASE_SIZE, 4)

    def test_multi_header_is_32_bytes(self):
        """元素跟 ShowAction 一样是 32 字节 —— 不是 8。"""
        self.assertEqual(MULTI_HEADER_SIZE, ENTRY_SIZE)
        hs = parse_multi_headers(fixture_bin('MoveMultiHeader'), 2)
        self.assertEqual(len(hs), 2)
        acts = log_show_actions()
        # 第 1 条 = 第一组 MoveMulti 的头（日志第 1 条），第 2 条 = 第二组的头（日志第 6 条）
        self.assertEqual(hs[0].datal._asdict(), side_dict(acts[0]['datal']))
        self.assertEqual(hs[1].datal._asdict(), side_dict(acts[5]['datal']))

    def test_multi_header_needs_count(self):
        """不传 count 会按容量整除，多读出数组外面的垃圾 —— 这是已知行为。"""
        b = fixture_bin('MoveMultiHeader')
        self.assertEqual(len(parse_multi_headers(b)), len(b) // ENTRY_SIZE)
        self.assertEqual(len(parse_multi_headers(b, 2)), 2)

    def test_turn_record_helpers(self):
        r = TurnRecord(player=0, turn=3, marker=1, lp_a=100, lp_b=200,
                       reserved=0)
        self.assertEqual(r.turn_number, 4)
        self.assertEqual(r.player, 0)


class TestLogBaseItemList(unittest.TestCase):
    """m_LogBaseItemList 的 32 字节全量重抓对账（2026-09-13 DUEL LIVE 回放实机）。

    fixture ``logbase.json`` / ``logbase_items.bin`` 来自
    ``tmp/regrab_logbase.py --dump``：元素 = 32 字节内联 struct
    （+0 loglabel 字符串指针 / +8 dataindex int32）。
    ⚠️ label 字符串与载荷数组名**不同名**：
    ShowFirstPlayer->ChoiceFirstPlayer、CardMoveMultiHeader->MoveMultiHeader、
    CardMoveMulti->MoveMulti；SeparateLine 没有载荷，dataindex 恒 -1。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, 'logbase.json'), encoding='utf-8') as f:
            cls.man = json.load(f)
        cls.entries = cls.man['entries']

    def test_size_matches_bin(self):
        with open(os.path.join(FIX, 'logbase_items.bin'), 'rb') as f:
            raw = f.read()
        self.assertEqual(len(raw), self.man['size'] * 32)
        self.assertEqual(len(self.entries), self.man['size'])

    def test_labels_are_known(self):
        known = {'ShowFirstPlayer', 'SeparateLine', 'CardMoveMultiHeader',
                 'CardMoveMulti', 'ShowTurn', 'ShowPhase', 'ShowCardName',
                 'ShowAction', 'ShowChain', 'ShowTag', 'ShowText'}
        unknown = {e['label'] for e in self.entries} - known
        self.assertEqual(unknown, set())

    def test_dataindex_sequential_per_label(self):
        """每种 label 的 dataindex 从 0 连续递增；SeparateLine 恒 -1。"""
        nexts = {}
        for e in self.entries:
            lb, di = e['label'], e['dataindex']
            if lb == 'SeparateLine':
                self.assertEqual(di, -1)
            else:
                self.assertEqual(di, nexts.get(lb, 0),
                                 '%s 的 dataindex 断档' % lb)
                nexts[lb] = di + 1

    def test_separateline_interleaves(self):
        """SeparateLine 是分节符：至少出现在开头与每个 ShowTurn 前（观测规律）。"""
        n = len(self.entries)
        self.assertGreater(self.man['size'], 300)
        self.assertEqual(self.entries[0]['label'], 'ShowFirstPlayer')
        sep_before_turn = all(
            self.entries[i - 1]['label'] == 'SeparateLine'
            for i, e in enumerate(self.entries)
            if e['label'] == 'ShowTurn' and i > 0)
        self.assertTrue(sep_before_turn)

    def test_record_structure_counts(self):
        """一整局：首条 ShowFirstPlayer，之后各类型计数自洽。"""
        from collections import Counter
        c = Counter(e['label'] for e in self.entries)
        self.assertEqual(c['ShowFirstPlayer'], 1)
        self.assertEqual(c['CardMoveMultiHeader'], c['CardMoveMulti'])
        payload = sum(n for k, n in c.items() if k != 'SeparateLine')
        self.assertEqual(payload + c['SeparateLine'], self.man['size'])


class TestShowCardName(unittest.TestCase):
    """ShowCardName = 6 字节 [u16 卡id][归属][?][?][pad]（2026-09-13 离线解开）。

    fixture 是 09-12 那局的载荷原始字节（2048 B = 按容量×16 超量读的），
    有效条数 108。真值：与 ShowAction 的 databyte1 交叉 27/27 无反例。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, 'm_DataList_ShowCardName.bin'),
                  'rb') as f:
            cls.blob = f.read()
        cls.entries = parse_card_names(cls.blob, 108)

    def test_stride_constant(self):
        self.assertEqual(CARD_NAME_SIZE, 6)

    def test_all_ids_are_plausible(self):
        """108/108 的卡 id 都该是正经 id（>0 且非 0xFFFF）。"""
        self.assertEqual(len(self.entries), 108)
        for e in self.entries:
            self.assertTrue(0 < e.card_id <= 0xFFFF, e)

    def test_side_is_0_or_1(self):
        for e in self.entries:
            self.assertIn(e.side, (0, 1))

    def test_flags_are_0_or_1_and_pad_zero(self):
        for i, e in enumerate(self.entries):
            self.assertIn(e.flag1, (0, 1), i)
            self.assertIn(e.flag2, (0, 1), i)
            # 第 5 字节恒 0（108 条实测）
            off = i * CARD_NAME_SIZE
            self.assertEqual(self.blob[off + 5], 0, i)

    def test_first_entries_match_known_truth(self):
        """头两条 = 对手先手的 VS 卡（归属 1）：神圣苏/狂恋博士。"""
        self.assertEqual(self.entries[0].card_id, 21433)   # 对击斗魂 神圣苏
        self.assertEqual(self.entries[0].side, 1)
        self.assertEqual(self.entries[1].card_id, 18730)   # 对击斗魂 狂恋博士
        self.assertEqual(self.entries[1].side, 1)

    def test_side_cross_check_with_showaction(self):
        """「归属」与 ShowAction 的 databyte1 交叉：本局 27/27 无反例。"""
        sides = {}
        for a in log_show_actions():
            d = a['datal']
            sides.setdefault(d['dataint'] & 0xFFFF, set()).add(
                d['databyte1'])
        checked = 0
        for e in self.entries:
            s = sides.get(e.card_id)
            if not s or len(s) != 1:
                continue          # 双方共用的卡没有真值，跳过
            self.assertEqual(e.side, s.pop(), '卡 %d 归属不符' % e.card_id)
            checked += 1
        self.assertGreaterEqual(checked, 20)

    def test_short_blob_raises(self):
        with self.assertRaises(ValueError):
            parse_card_names(self.blob[:100], 108)


class TestDatacActionKind(unittest.TestCase):
    """datac.databyte1 = 动作类型枚举（2026-09-14 钉死，113 组配对无一例外）。

    内存侧的判据：bin 里每条 entry 的 datac 必须与日志 JSON 的 ``datac``
    **逐条相等**（与 datal/datar 的 64/64 同一套判据）——日志就是解码真值表。
    """

    def test_events_match_log_enum(self):
        """事件流的 action_kind == 日志 JSON 的 datac.databyte1（按序对齐）。"""
        entries = parse_entries(fixture_bin('ShowAction'), 64)
        pure = pure_show_actions()[:64]
        self.assertEqual(len(entries), len(pure))
        for k, (e, o) in enumerate(zip(entries, pure)):
            self.assertEqual(e.datac.databyte1, o['datac']['databyte1'],
                             '第 %d 条动作枚举不一致' % k)
        evs = {(ev.index, ev.side): ev.action_kind
               for ev in iter_action_events(entries)}
        for k, o in enumerate(pure):
            if (k, 'l') not in evs:
                continue          # datal/datar 全空的条目（如 Turn）没有事件
            self.assertEqual(evs[(k, 'l')], o['datac']['databyte1'],
                             '第 %d 条事件的 action_kind 不一致' % k)

    def test_action_enum_mapping(self):
        """关键枚举值 -> Untapped handleAction 名（真值表抽查）。"""
        expect = {1: 'DRAW', 2: 'SUMMON', 3: 'SPSUMMON', 11: 'HAPPEN',
                  13: 'SETH', 17: 'CONFIRM', 21: 'BREAK', 26: 'SEARCH'}
        for kind, name in expect.items():
            self.assertEqual(ACTION_KIND_NAMES[kind], name)
            self.assertEqual(action_kind_name(kind), name)

    def test_action_kind_unknown_no_guess(self):
        self.assertEqual(action_kind_name(999), 'KIND_999')

    def test_action_enum_s1_names(self):
        """S-1 补名（M3 结项划转 M6）：RELEASE/MOVE/VANISH/ATKTODEFCHANGE/PUT。"""
        expect = {15: 'RELEASE', 28: 'MOVE', 29: 'VANISH',
                  37: 'ATKTODEFCHANGE', 39: 'PUT'}
        for kind, name in expect.items():
            self.assertEqual(ACTION_KIND_NAMES[kind], name)
            self.assertEqual(action_kind_name(kind), name)

    def test_action_enum_table_complete(self):
        """补名后表 29 值：名字不重复、已观测枚举不再有裸数字占位。"""
        names = list(ACTION_KIND_NAMES.values())
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ACTION_KIND_NAMES), 29)

    def test_iter_events_carry_action_kind(self):
        entries = parse_entries(fixture_bin('ShowAction'), 64)
        evs = list(iter_action_events(entries))
        self.assertTrue(evs)
        for ev in evs:
            self.assertIn(ev.action_kind, ACTION_KIND_NAMES)
            self.assertTrue(ev.center_boolbits & 1)


class TestShowChain(unittest.TestCase):
    """ShowChain = 12 字节 [连锁序号<<16|卡id][归属][已处理?][pad]。

    真值：09-12 那局 30/30 合法卡 id，首块连锁登记 1→4、处理逆序 4→1
    （后发先至），归属 30/30 与 ShowAction 已知卡一致。
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIX, 'm_DataList_ShowChain.bin'), 'rb') as f:
            cls.blob = f.read()
        cls.entries = parse_chains(cls.blob, 30)

    def test_count_and_ids(self):
        self.assertEqual(len(self.entries), 30)
        for e in self.entries:
            self.assertTrue(0 < e.card_id <= 0xFFFF, e)

    def test_first_block_story(self):
        """首块：登记 1→2→3→4，随后逆序 4→3→2→1（后发先至）。"""
        first8 = self.entries[:8]
        self.assertEqual([e.chain_index for e in first8], [1, 2, 3, 4,
                                                           4, 3, 2, 1])
        self.assertEqual(first8[0].card_id, 21433)   # 对击斗魂 神圣苏
        self.assertEqual(first8[3].card_id, 17365)   # 拉比丽斯的吹火炉
        self.assertEqual(first8[4].card_id, 17365)
        self.assertEqual(first8[7].card_id, 21433)

    def test_sides_match_known_cards(self):
        """归属抽查：对手的 VS 卡 side=1、我方拉比丽斯/杀手旋律 side=0。"""
        ours = {17365, 22533, 17363, 17367, 18222, 17360, 18507}
        theirs = {21433, 18730, 18727, 21477, 23494, 14740, 22116, 20500,
                  12950}
        for e in self.entries:
            if e.card_id in ours:
                self.assertEqual(e.side, 0, e)
            elif e.card_id in theirs:
                self.assertEqual(e.side, 1, e)

    def test_flags_binary(self):
        for e in self.entries:
            self.assertIn(e.side, (0, 1))
            self.assertIn(e.flag2, (0, 1))

    def test_short_blob_raises(self):
        with self.assertRaises(ValueError):
            parse_chains(self.blob[:11], 30)


if __name__ == '__main__':
    unittest.main()
