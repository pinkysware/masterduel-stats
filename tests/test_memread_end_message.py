# -*- coding: utf-8 -*-
"""`core/memread/end_message.py` 的单测 —— 结算文案判胜负（纯逻辑，不碰进程）。

这里盯的是**判错方向**：判据一旦把「对手投降」认成「我输」，
整条胜负线就是错的，而它又是 M1 要替换日志的那一块。
"""
from __future__ import annotations

import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core.memread.end_message import (                       # noqa: E402
    LP_DEPLETION_RE, SELF_TEXT_RESULT, SURRENDER_LOSE, SURRENDER_WIN,
    WINBIT_OFFSET, EndMessageReader, derive_result,
    derive_result_from_winbits, is_known, result_from_duel_code,
    lp_loser, read_winbits, winbits_from_native)


class TestResultFromDuelCode(unittest.TestCase):
    """结算码兜底判据（2026-09-16 拍板）：只认 1 胜 / 2 负，其余不猜。"""

    def test_one_is_win_two_is_lose(self):
        self.assertEqual(result_from_duel_code(1), 'Win')
        self.assertEqual(result_from_duel_code(2), 'Lose')
        self.assertEqual(result_from_duel_code('1'), 'Win')

    def test_unknown_returns_none(self):
        for bad in (None, 0, 3, 'x', [1]):
            self.assertIsNone(result_from_duel_code(bad), repr(bad))


class TestSurrender(unittest.TestCase):
    def test_rival_surrendered_i_win(self):
        self.assertEqual(derive_result('对手已投降'), 'win')

    def test_i_surrendered_i_lose(self):
        self.assertEqual(derive_result('已投降'), 'lose')

    def test_substring_trap(self):
        """★ `'对手已投降'` 里含子串 `'已投降'` —— 不许被判成我输。"""
        self.assertNotEqual(derive_result('对手已投降'), 'lose')
        self.assertNotEqual(derive_result('已投降'), 'win')

    def test_side_swapped(self):
        """万一哪局视角方在 player1，投降类也要跟着翻。"""
        self.assertEqual(derive_result('对手已投降', my_side=1), 'lose')
        self.assertEqual(derive_result('已投降', my_side=1), 'win')

    def test_whitespace_tolerated(self):
        self.assertEqual(derive_result('  对手已投降 \n'), 'win')
        self.assertEqual(derive_result(' 已投降'), 'lose')


class TestSelfTextResult(unittest.TestCase):
    """★ 视角方自指文案（省略主语 = 我方）—— 中文实证 + 2026-10-05 日文样本。

    这些文案**不含玩家名**，`lp_loser` 对不上名字 → 只能靠精确匹配表。
    表里加错方向 = 整局记反，所以每条都必须有样本出处。
    """

    def test_chinese_self(self):
        self.assertEqual(derive_result('LP已削减至0', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('对手的LP已削减至0', 'A', 'B'), 'win')
        self.assertEqual(derive_result('由于超出时间在对局中落败', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('由于网络中断，决斗已经结束', 'A', 'B'),
                         'win')

    def test_japanese_samples(self):
        """2026-10-05 用户实机样本（日文客户端 7 张截图）。"""
        self.assertEqual(derive_result('相手が降参しました', 'A', 'B'), 'win')
        self.assertEqual(derive_result('降参しました', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('LPが0になりました', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('相手のLPを0にしました', 'A', 'B'), 'win')
        self.assertEqual(derive_result('タイムアウトで敗北しました', 'A', 'B'),
                         'lose')
        self.assertEqual(
            derive_result('通信切断によりデュエルが終了しました', 'A', 'B'), 'win')

    def test_side_swapped(self):
        self.assertEqual(
            derive_result('相手が降参しました', 'A', 'B', my_side=1), 'lose')
        self.assertEqual(
            derive_result('LPが0になりました', 'A', 'B', my_side=1), 'win')

    def test_not_collected_still_none(self):
        """**没样本的日文文案不许猜**（我方投降 / 对手 LP 归零仍缺样本）。"""
        for text in ('相手のLPが0になりました', '敗北しました'):
            self.assertIsNone(derive_result(text, 'A', 'B'), msg=text)

    def test_is_known_covers_self_text(self):
        for text in ('LP已削减至0', '对手的LP已削减至0', '相手が降参しました',
                     'LPが0になりました', 'タイムアウトで敗北しました',
                     '通信切断によりデュエルが終了しました',
                     '降参しました', '相手のLPを0にしました',
                     "Opponent's LP reduced to 0!", 'LP reduced to 0',
                     'Opponent Surrendered', 'You Surrendered',
                     'Time limit win', 'Time limit loss'):
            self.assertTrue(is_known(text), msg=text)

    def test_english_samples(self):
        """2026-10-05 用户实机样本（英文客户端 6 张截图）。"""
        self.assertEqual(derive_result('You Surrendered', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('Time limit loss', 'A', 'B'), 'lose')
        self.assertEqual(derive_result("Opponent's LP reduced to 0!", 'A', 'B'),
                         'win')
        self.assertEqual(derive_result('LP reduced to 0', 'A', 'B'), 'lose')
        self.assertEqual(derive_result('Opponent Surrendered', 'A', 'B'), 'win')
        self.assertEqual(derive_result('Time limit win', 'A', 'B'), 'win')

    def test_english_variants_normalized(self):
        """撇号（直/弯）与结尾感叹号由 `_norm_text` 归一 —— 写法不同也同判。"""
        for text in ("Opponent's LP reduced to 0!",
                     'Opponent\u2019s LP reduced to 0!',
                     "Opponent's LP reduced to 0",
                     'Opponent\u2019s LP reduced to 0'):
            self.assertEqual(derive_result(text, 'A', 'B'), 'win', msg=text)
        self.assertEqual(derive_result('LP reduced to 0!', 'A', 'B'), 'lose')

    def test_english_side_swapped(self):
        self.assertEqual(
            derive_result('Opponent Surrendered', 'A', 'B', my_side=1), 'lose')
        self.assertEqual(
            derive_result('LP reduced to 0', 'A', 'B', my_side=1), 'win')

    def test_table_has_no_duplicate_value(self):
        """同一文案不许映射两种胜负（表内自检）。"""
        for table in (SELF_TEXT_RESULT,):
            vals = set(table.values())
            self.assertTrue(vals <= {'win', 'lose'}, repr(vals))


class TestLpDepletion(unittest.TestCase):
    """`{胜者名}将{败者名}的LP已削减至0` —— 句首是胜者。"""

    def test_i_won(self):
        self.assertEqual(
            derive_result('示例玩家将对手甲的LP已削减至0',
                          player0_name='示例玩家', player1_name='对手甲'), 'win')

    def test_i_lost(self):
        self.assertEqual(
            derive_result('对手甲将示例玩家的LP已削减至0',
                          player0_name='示例玩家', player1_name='对手甲'), 'lose')

    def test_replay_sample(self):
        """真实回放样本（观战名字被隐藏成 DUELIST A/B）。"""
        self.assertEqual(
            derive_result('DUELIST A将DUELIST B的LP已削减至0',
                          player0_name='DUELIST A',
                          player1_name='DUELIST B'), 'win')

    def test_winner_matches_nobody(self):
        """胜者名既不是我方也不是对手 -> 不判（别硬猜）。"""
        self.assertIsNone(
            derive_result('路人甲将路人乙的LP已削减至0',
                          player0_name='示例玩家', player1_name='对手甲'))

    def test_names_missing(self):
        self.assertIsNone(derive_result('A将B的LP已削减至0'))
        self.assertIsNone(derive_result('A将B的LP已削减至0', player0_name='A'))

    def test_side_swapped(self):
        self.assertEqual(
            derive_result('A将B的LP已削减至0', player0_name='B',
                          player1_name='A', my_side=1), 'win')

    def test_anchored_regex(self):
        """正则锚定在**结尾** —— 带后缀的串不算这个文案。"""
        self.assertIsNone(LP_DEPLETION_RE.match('A将B的LP已削减至0，然后呢'))
        # ⚠️ 带**前缀**的串会被正则吃掉（前缀成了胜者名的一部分）——
        #    真正拦住它的是「胜者名对不上任何人」，不是锚点。
        self.assertEqual(LP_DEPLETION_RE.match('前缀A将B的LP已削减至0')
                         .group('winner'), '前缀A')
        # ⚠️ 现在判 'win' 而不是 None：邻接规则认出「贴 LP 的是 B」= B 输，
        #    胜者名带前缀垃圾也无所谓 —— 败方认定比胜者认定更稳。
        self.assertEqual(derive_result('前缀A将B的LP已削减至0',
                                       player0_name='A', player1_name='B'),
                         'win')

    def test_surrender_text_is_not_lp(self):
        self.assertIsNone(LP_DEPLETION_RE.match(SURRENDER_LOSE))
        self.assertIsNone(LP_DEPLETION_RE.match(SURRENDER_WIN))


class TestUnknown(unittest.TestCase):
    def test_unknown_text(self):
        for text in ('胜利', 'DEFEAT', '时间耗尽', 'A已投降', '对手认输',
                     '平局', '双方的LP已削减至0'):
            self.assertIsNone(derive_result(text, 'A', 'B'), msg=text)

    def test_empty(self):
        for text in (None, '', '   '):
            self.assertIsNone(derive_result(text, 'A', 'B'), msg=repr(text))


class TestIsKnown(unittest.TestCase):
    """「文案认不认识」—— 只问格式，不看名字。"""

    def test_known(self):
        self.assertTrue(is_known('已投降'))
        self.assertTrue(is_known('对手已投降'))
        self.assertTrue(is_known('A将B的LP已削减至0'))

    def test_unknown(self):
        for text in (None, '', '时间耗尽', '胜利'):
            self.assertFalse(is_known(text), msg=repr(text))

    def test_lp_needs_no_names(self):
        """LP 那类**格式对就算认识**（与 derive_result 的差别就在这里）。"""
        self.assertIsNone(derive_result('A将B的LP已削减至0'))
        self.assertTrue(is_known('A将B的LP已削减至0'))



class TestLpLanguageIndependent(unittest.TestCase):
    """★ 贴着 LP 字样的名字是**败方** —— 与语言/语序无关。

    中文已实证（`A将B的LP已削减至0`）；英/日的格式是**同构推断**，
    等撞到样本确认 —— 但规则本身不依赖语序，被动句也不会判反。
    """

    def test_chinese_active(self):
        self.assertEqual(derive_result('A将B的LP已削减至0', 'A', 'B'), 'win')
        self.assertEqual(derive_result('B将A的LP已削减至0', 'A', 'B'), 'lose')

    def test_english_passive_loser_first(self):
        """英语被动句败者名在**句首** —— 按「贴 LP 者 = 败方」照样判对。"""
        text = "B's LP was reduced to 0 by A"
        self.assertEqual(derive_result(text, 'A', 'B'), 'win')
        self.assertEqual(derive_result(text, 'B', 'A'), 'lose')

    def test_english_active(self):
        self.assertEqual(derive_result("A reduced B's LP to 0", 'A', 'B'),
                         'win')

    def test_japanese(self):
        self.assertEqual(derive_result('AがBのLPを0にした', 'A', 'B'), 'win')

    def test_long_name_substring(self):
        """长名优先匹配，免得 Alice 撞进 Alice2 里。"""
        text = 'Alice2将Alice的LP已削减至0'
        self.assertEqual(lp_loser(text, ('Alice2', 'Alice')), 'Alice')
        self.assertEqual(derive_result(text, 'Alice2', 'Alice'), 'win')

    def test_surrender_checked_before_names(self):
        """名字规则不许抢在**精确匹配**前面把投降文案判掉。"""
        self.assertEqual(derive_result('对手已投降', '对手', '已投降'), 'win')



# --------------------------------------------------------------- 胜负状态位

#: 真实样本（2026-09-13 骰子拉力，data/archive/result_states/ 里的原生 +0x40..0x4F）。
#: 亮 = +0x46/+0x47 两字节为 1,1（败方的 icon 全 0）。
NATIVE_WINDOW = 0x40
WIN_RAW = '00000000000001010000000000000000'      # 胜方 icon
LOSE_RAW = '00000000000000000000000000000000'     # 败方 icon


class _FakeProc(object):
    """只实现 read 的假进程 —— 地址->字节 的平铺字典。"""

    def __init__(self, blob):
        self.blob = blob                 # {addr: byte}

    def read(self, addr, n):
        return bytes(self.blob.get(addr + i, 0) for i in range(n))

    def read_ptr(self, addr):
        return int.from_bytes(self.read(addr, 8), 'little')


def _put_qword(blob, addr, value):
    """往平铺字典里按小端写一个 8 字节指针。"""
    for i, b in enumerate(value.to_bytes(8, 'little')):
        blob[addr + i] = b


def _fake_with_winbits(p0_bit, p1_bit):
    """搭一个 instance -> icon GO -> native 的最小内存布局。"""
    inst, go0, go1, nat0, nat1 = (0x10000000, 0x20000000,
                                  0x20010000, 0x30000000, 0x30010000)
    blob = {}
    _put_qword(blob, inst + 0xB0, go0)   # player0WinIcon 字段偏移
    _put_qword(blob, inst + 0xB8, go1)
    for go, nat in ((go0, nat0), (go1, nat1)):
        _put_qword(blob, go + 0x10, nat)  # GO 包装 -> 原生对象
    blob[nat0 + WINBIT_OFFSET] = p0_bit
    blob[nat1 + WINBIT_OFFSET] = p1_bit
    return _FakeProc(blob), inst


def _native_from_window(window_hex):
    """真实样本是 +0x40..0x4F 的窗口 —— 拼回「从对象头开始的字节」。"""
    buf = bytearray(WINBIT_OFFSET + 2)
    buf[NATIVE_WINDOW: NATIVE_WINDOW + 0x10] = bytes.fromhex(window_hex)
    return bytes(buf)


class TestWinbitsFromNative(unittest.TestCase):
    def test_real_win_sample(self):
        self.assertEqual(winbits_from_native(_native_from_window(WIN_RAW)),
                         (1, 1))

    def test_real_lose_sample(self):
        self.assertEqual(winbits_from_native(_native_from_window(LOSE_RAW)),
                         (0, 0))

    def test_garbage_bytes_rejected(self):
        """半死实例读出的残值（如 128）必须拒 —— 库里出现过 `winbits=[128, 1]`。

        那两局是图标对象正在被回收时读到的，0/1 之外的值写进库就是脏数据；
        拒掉之后 `read_texts()` 的「胜负位可读」加分也不会把半死实例当成活的。
        """
        raw = bytearray(WINBIT_OFFSET + 2)
        raw[WINBIT_OFFSET] = 128
        raw[WINBIT_OFFSET + 1] = 1
        self.assertIsNone(winbits_from_native(bytes(raw)))
        raw[WINBIT_OFFSET] = 1
        raw[WINBIT_OFFSET + 1] = 255
        self.assertIsNone(winbits_from_native(bytes(raw)))

    def test_too_short(self):
        self.assertIsNone(
            winbits_from_native(bytes.fromhex(WIN_RAW)))   # 只有 16 字节
        self.assertIsNone(winbits_from_native(None))
        self.assertIsNone(winbits_from_native(b''))


class TestDeriveFromWinbits(unittest.TestCase):
    """三结局实测（2026-09-13 骰子拉力）：位翻=胜负翻，座位恒定。"""

    def test_win_sample(self):
        """第 2 局：我 1,1 / 对方 0,0 -> 我赢。"""
        self.assertEqual(derive_result_from_winbits(1, 0), 'win')

    def test_lose_lp0_sample(self):
        """第 1 局：我 0,0 / 对方 1,1 -> 我输。"""
        self.assertEqual(derive_result_from_winbits(0, 1), 'lose')

    def test_surrender_sample(self):
        """第 3 局（我投降）：我 0,0 / 对方 1,1 -> 我输 —— 与 LP0 败局同构。"""
        self.assertEqual(derive_result_from_winbits(0, 1), 'lose')

    def test_ambiguous_returns_none(self):
        """两 bit 同值 = 信息不足（画面没画完之类），别硬猜。"""
        self.assertIsNone(derive_result_from_winbits(0, 0))
        self.assertIsNone(derive_result_from_winbits(1, 1))

    def test_garbage_returns_none(self):
        self.assertIsNone(derive_result_from_winbits(2, 1))
        self.assertIsNone(derive_result_from_winbits(0, 255))

    def test_side_swapped(self):
        self.assertEqual(derive_result_from_winbits(0, 1, my_side=1), 'win')
        self.assertEqual(derive_result_from_winbits(1, 0, my_side=1), 'lose')


class TestReadWinbits(unittest.TestCase):
    def test_reads_both_sides(self):
        proc, inst = _fake_with_winbits(1, 0)
        self.assertEqual(read_winbits(proc, inst, 0xB0, 0xB8), (1, 0))

    def test_invalid_instance(self):
        proc, _inst = _fake_with_winbits(1, 0)
        self.assertIsNone(read_winbits(proc, 0xDEAD0000, 0xB0, 0xB8))

    def test_null_icon_pointer(self):
        """不在结算画面时 icon 指针会被清零 —— 返回 None 不抛。"""
        proc, inst = _fake_with_winbits(1, 0)
        _put_qword(proc.blob, inst + 0xB0, 0)
        self.assertIsNone(read_winbits(proc, inst, 0xB0, 0xB8))


class TestReaderReadWinbits(unittest.TestCase):
    """`EndMessageReader.read_winbits(instance)` —— 隔几秒补读胜负位。

    实机背景（2026-09-15 深夜）：收局那一拍文案已就绪、WinIcon 的 GameObject
    还没赋值 → 采集器记下 `winbits=None`（那两局 `result` 空着）。
    修法 = 拿**同一个实例**隔几秒重试，所以这个口要能按地址重读。
    """

    def _reader(self, proc, fields=None):
        r = EndMessageReader(game_dir=None)          # 只建对象，不 connect
        r._proc = proc
        if fields is None:
            from types import SimpleNamespace
            fields = {'player0WinIcon': SimpleNamespace(offset=0xB0),
                      'player1WinIcon': SimpleNamespace(offset=0xB8)}
        r._fields = fields
        return r

    def test_reads_by_hex_string_or_int(self):
        proc, inst = _fake_with_winbits(1, 0)
        r = self._reader(proc)
        self.assertEqual(r.read_winbits('0x%X' % inst), (1, 0))
        self.assertEqual(r.read_winbits(inst), (1, 0))

    def test_missing_field_returns_none(self):
        proc, inst = _fake_with_winbits(1, 0)
        r = self._reader(proc, fields={})
        self.assertIsNone(r.read_winbits(inst))

    def test_no_process_returns_none(self):
        r = self._reader(None)
        self.assertIsNone(r.read_winbits(0x1000))
        self.assertIsNone(r.read_winbits(''))


if __name__ == '__main__':

    unittest.main()


class _FakeProcForReader(object):
    """adopt/区域优先用的假进程：记录 scan 收到的区域顺序。"""

    def __init__(self, regions, hits=()):
        self.pid = 4321
        self._regions = regions
        self._hits = list(hits)
        self.scan_regions_seen = []

    def private_regions(self):
        return list(self._regions)

    def scan(self, pattern, max_hits=None, regions=None, deadline=None):
        self.scan_regions_seen.append(list(regions or []))
        for h in self._hits:
            yield h

    def read_ptr(self, addr):
        # 验证规则：*hit == ti、*(hit+8) < 0x10000
        if addr % 16 == 0:
            return 0x42
        return 0


class TestAdopt(unittest.TestCase):
    """🔴 2026-10-01 事故刀①：Runtime 复用主连接，别再自带 connect()
    （全进程扫 metadata 镜像无预算，分钟级 —— 首局结算窗口整个耗在它上面）。
    """

    def _reader(self, proc):
        from core.memread import end_message as EM
        r = EM.EndMessageReader(game_dir='X:/nope')
        orig_ft, orig_lf = EM.find_typeinfo, EM.list_fields
        EM.find_typeinfo = lambda *a, **k: [0x42]
        EM.list_fields = lambda *a, **k: []
        try:
            ok = r.adopt(proc, mirror=object(), tbl=object())
        finally:
            EM.find_typeinfo, EM.list_fields = orig_ft, orig_lf
        return r, ok

    def test_adopt_reuses_host_process(self):
        proc = _FakeProcForReader([])
        # find_typeinfo 打桩返回 ti，但字段表为空 -> 'messageText' 缺失 -> 失败
        r, ok = self._reader(proc)
        self.assertFalse(ok)
        self.assertFalse(r.ready())

    def test_adopt_ok_and_close_keeps_host_process(self):
        from core.memread import end_message as EM
        proc = _FakeProcForReader([])
        r = EM.EndMessageReader(game_dir='X:/nope')
        orig_ft, orig_lf = EM.find_typeinfo, EM.list_fields

        class _F(object):
            name = 'messageText'
            offset = 8
        EM.find_typeinfo = lambda *a, **k: [0x42]
        EM.list_fields = lambda *a, **k: [_F()]
        try:
            ok = r.adopt(proc, mirror=object(), tbl=object())
        finally:
            EM.find_typeinfo, EM.list_fields = orig_ft, orig_lf
        self.assertTrue(ok)
        self.assertTrue(r.ready())
        self.assertEqual(r.pid, 4321)
        self.assertFalse(r._owns_proc)
        closed = []
        proc.close = lambda: closed.append(1)
        r.close()
        self.assertEqual(closed, [], 'adopt 的进程归宿主管，close 不许关它')
        self.assertFalse(r.ready())


class TestRegionPriority(unittest.TestCase):
    """🔴 2026-10-01 事故刀②：10s 扫描预算能不能扫到目标区域全看内存布局
    （同一局 5 次整读全落空、下一局首读就命中）。上次命中的区域/实例地址
    必须优先。
    """

    @staticmethod
    def _fields():
        class _F(object):
            offset = 8
        return {'messageText': _F()}

    def _regions(self):
        from core.memread.process import MemoryRegion
        mk = lambda base: MemoryRegion(base=base, size=0x1000, protect=0,
                                       state=0, type=0)
        return [mk(0x1000), mk(0x2000), mk(0x3000)]

    def test_last_instance_prepended_without_scan(self):
        from core.memread.end_message import EndMessageReader
        regions = self._regions()
        proc = _FakeProcForReader(regions)
        r = EndMessageReader(game_dir='X:/nope')
        r._proc, r._pid, r._ti = proc, 4321, 0x42
        r._fields = self._fields()
        r._last_instance = 0x9990
        hits = list(r._candidates())
        self.assertEqual(hits[0], 0x9990, '上次命中的地址免扫描直接前置')

    def test_last_region_scanned_first(self):
        from core.memread.end_message import EndMessageReader
        regions = self._regions()
        proc = _FakeProcForReader(regions)
        r = EndMessageReader(game_dir='X:/nope')
        r._proc, r._pid, r._ti = proc, 4321, 0x42
        r._fields = self._fields()
        r._last_region = regions[2]              # 上次命中在 0x3000
        list(r._candidates())
        self.assertEqual(proc.scan_regions_seen[-1][0].base, 0x3000,
                         '上次命中的区域该排在最前')
        self.assertEqual([x.base for x in proc.scan_regions_seen[-1]],
                         [0x3000, 0x1000, 0x2000])

    def test_no_cache_keeps_original_order(self):
        from core.memread.end_message import EndMessageReader
        regions = self._regions()
        proc = _FakeProcForReader(regions)
        r = EndMessageReader(game_dir='X:/nope')
        r._proc, r._pid, r._ti = proc, 4321, 0x42
        r._fields = self._fields()
        list(r._candidates())
        self.assertEqual([x.base for x in proc.scan_regions_seen[-1]],
                         [0x1000, 0x2000, 0x3000])


class ArbitrateResultTest(unittest.TestCase):
    """🔴 2026-10-03 事故回归：投降局 winbit 是上一局残留，压过了正确的
    结算码+文案。仲裁 = 多数票；平票维持 winbit > duel_code > text。"""

    def test_real_incident_winbit_is_overruled(self):
        """今天那局实锤：文案「已投降」+ 码 2 都说输，winbit [1,0] 说赢。"""
        from core.memread.end_message import arbitrate_result as A
        got, src, note = A([1, 0], 2, '已投降')
        self.assertEqual((got, src), ('lose', 'duel_code'))
        self.assertIn('冲突', note)

    def test_unanimous_uses_winbit(self):
        from core.memread.end_message import arbitrate_result as A
        got, src, note = A([0, 1], 2, '已投降')
        self.assertEqual((got, src), ('lose', 'winbit'))
        self.assertIsNone(note)

    def test_tie_keeps_old_priority(self):
        """平票（文案 vs 码）维持旧优先级 —— 10-02 那局的库值（旧抢占 bug
        污染的文案）不被翻案。"""
        from core.memread.end_message import arbitrate_result as A
        got, src, _note = A(None, 2, '对手已投降')
        self.assertEqual((got, src), ('lose', 'duel_code'))

    def test_single_vote_and_invalid_bits(self):
        from core.memread.end_message import arbitrate_result as A
        self.assertEqual(A([0, 1], None, None)[0], 'lose')
        self.assertEqual(A([0, 0], 1, None)[0], 'win')     # 无效位 -> 用码
        self.assertEqual(A(None, None, '对手已投降')[0], 'win')
        self.assertEqual(A(None, None, None), (None, None, None))
