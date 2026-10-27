# -*- coding: utf-8 -*-
"""`core.deck_status` + `core.matching_monitor` 单测。

两块都是**纯函数/纯状态机**（不碰 IO、不碰 Qt、不碰内存），
所以这里不需要 FakeMem —— 直接喂 dict / bool 就行。

对标的实现是 untapped 的 `YgomDeckStatus`（`Core.js` / `YgomOverlay.js`）：
* 三态判定：`known` / `unknown_live` / `unknown`
  （untapped 是 `known` / `unknown_reselect` / `unknown_edit` / `unknown`）
* 边沿检测：`state === 'known' && prev !== 'known' && prev !== null`
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deck_status import (  # noqa: E402
    MODES_NEED_EDIT,
    _HINT_CHANGE,
    _HINT_EDIT,
    _HINT_PICK_NAME,
    _HINT_RESELECT,
    STATE_KNOWN,
    STATE_UNKNOWN,
    STATE_UNKNOWN_LIVE,
    TRUSTED_SOURCES,
    deck_state,
    hint_for,
    should_announce,
)
from core.matching_monitor import (  # noqa: E402
    EV_ENTER,
    EV_LEAVE,
    MatchingMonitor,
)


class DeckStateTest(unittest.TestCase):
    def test_named_with_trusted_source_is_known(self):
        for src in ('m5-guess', 'manual', 'log-deck', 'current-deck'):
            with self.subTest(src=src):
                self.assertEqual(
                    deck_state({'name': 'Branded', 'src': src}), STATE_KNOWN)

    def test_named_without_source_is_known(self):
        """没有 src 但有名字 -> 也算 known（老状态文件没有 src 字段）。"""
        self.assertEqual(deck_state({'name': '闪刀姬'}), STATE_KNOWN)

    def test_game_name_echo_is_unknown_live(self):
        """★ 用户 2026-10-28：**卡组名是玩家随手起的**，有名字 ≠ 认出来了。

        `game_name` 与 `name` 相同 = 卡表没归到参照库，只是把游戏内名字
        抬了上来（「狱神神艺」「烙印40」）。这种必须算未识别，提示用户手动选。
        """
        self.assertEqual(
            deck_state({'name': '狱神神艺', 'src': 'm5-guess',
                        'ids_fingerprint': 'x', 'game_name': '狱神神艺'}),
            STATE_UNKNOWN_LIVE)

    def test_card_id_derived_name_is_known(self):
        """名字来自卡表反推（与游戏内名不同）-> known，哪怕名字长得怪。"""
        self.assertEqual(
            deck_state({'name': 'Branded', 'src': 'm5-guess',
                        'ids_fingerprint': 'x', 'game_name': '烙印40'}),
            STATE_KNOWN)

    def test_named_with_untrusted_source_is_not_known(self):
        """来源不可信（例如未来新增的调试来源）-> 不当作 known。"""
        self.assertNotEqual(
            deck_state({'name': '某卡组', 'src': 'debug-guess'}), STATE_KNOWN)

    def test_blank_name_with_ids_is_unknown_live(self):
        """读到了卡表但没名字 —— 内存通路是通的，只是名字没归到参照库。"""
        self.assertEqual(
            deck_state({'ids_fingerprint': 'abc123', 'src': 'm5-guess'}),
            STATE_UNKNOWN_LIVE)

    def test_empty_is_unknown(self):
        self.assertEqual(deck_state({}), STATE_UNKNOWN)

    def test_none_is_unknown(self):
        self.assertEqual(deck_state(None), STATE_UNKNOWN)

    def test_whitespace_name_is_unknown(self):
        """只有空格的名字等同于空（别把空白当卡组名显示出去）。"""
        self.assertEqual(deck_state({'name': '   '}), STATE_UNKNOWN)

    def test_blank_name_with_ids_but_untrusted_src(self):
        """名字空 + 有指纹 -> unknown_live（与 src 无关，指纹就是证据）。"""
        self.assertEqual(
            deck_state({'name': '', 'ids_fingerprint': 'x'}), STATE_UNKNOWN_LIVE)


class HintForTest(unittest.TestCase):
    def test_known_needs_no_hint(self):
        self.assertEqual(hint_for(STATE_KNOWN), '')

    def test_unknown_live_asks_to_pick_name(self):
        """读到卡表只差名字 —— 让用户点卡图选，不是去编辑卡组。"""
        self.assertEqual(hint_for(STATE_UNKNOWN_LIVE), _HINT_PICK_NAME)

    def test_dc_needs_edit(self):
        """DC 杯卡表只在编辑界面可读（2026-10-25 实机确认）。"""
        self.assertEqual(hint_for(STATE_UNKNOWN, 'dc'), _HINT_EDIT)

    def test_event_needs_reselect(self):
        """活动/租借局 -> 重新选（untapped 的 unknown_reselect 同口径）。"""
        self.assertEqual(hint_for(STATE_UNKNOWN, 'event'), _HINT_RESELECT)

    def test_ranked_needs_change_decks(self):
        self.assertEqual(hint_for(STATE_UNKNOWN, 'ranked'), _HINT_CHANGE)

    def test_unknown_mode_falls_back_to_change(self):
        self.assertEqual(hint_for(STATE_UNKNOWN, None), _HINT_CHANGE)
        self.assertEqual(hint_for(STATE_UNKNOWN, ''), _HINT_CHANGE)

    def test_peak_uses_change(self):
        self.assertEqual(hint_for(STATE_UNKNOWN, 'peak'), _HINT_CHANGE)


class HintIsUserReadableTest(unittest.TestCase):
    """hint 返回的**必须是可以直接给用户看的中文句子**，不是内部 key。

    ⚠️ 本项目约定「中文原文就是 i18n 键」（见 `ui/i18n.py` 模块头），
    所以 hint 用中文句子当键 —— 中文模式下 `tr()` 原样返回就是文案本身。
    若写成 `deck_hint_edit` 这类英文 key，中文用户会看到内部标识符。
    """

    def test_all_hints_are_chinese(self):
        hints = [_HINT_PICK_NAME, _HINT_RESELECT, _HINT_EDIT, _HINT_CHANGE]
        for h in hints:
            with self.subTest(hint=h):
                self.assertTrue(
                    any('一' <= ch <= '鿿' for ch in h),
                    '提示里没有汉字：%r' % h)
                self.assertFalse(h.startswith('deck_hint_'),
                                 'hint 是内部 key 不是文案：%r' % h)

    def test_hints_in_i18n_table(self):
        """每个 hint 都必须在 i18n 表里（EN/JA 才有翻译）。"""
        from ui.i18n import _T
        for h in (_HINT_PICK_NAME, _HINT_RESELECT, _HINT_EDIT, _HINT_CHANGE):
            with self.subTest(hint=h):
                self.assertIn(h, _T)


class ShouldAnnounceTest(unittest.TestCase):
    """对齐 untapped：只有「从非 known 变成 known」才弹一次性提示。"""

    def test_none_prev_does_not_announce(self):
        """首拍不弹 —— 避免一进匹配界面就闪一下。"""
        self.assertFalse(should_announce(None, STATE_KNOWN))

    def test_unknown_to_known_announces(self):
        self.assertTrue(should_announce(STATE_UNKNOWN, STATE_KNOWN))

    def test_unknown_live_to_known_announces(self):
        self.assertTrue(should_announce(STATE_UNKNOWN_LIVE, STATE_KNOWN))

    def test_known_to_known_does_not(self):
        self.assertFalse(should_announce(STATE_KNOWN, STATE_KNOWN))

    def test_known_to_unknown_does_not(self):
        self.assertFalse(should_announce(STATE_KNOWN, STATE_UNKNOWN))

    def test_unknown_to_unknown_does_not(self):
        self.assertFalse(should_announce(STATE_UNKNOWN, STATE_UNKNOWN))


class MatchingMonitorTest(unittest.TestCase):
    def test_enter_edge(self):
        m = MatchingMonitor()
        self.assertEqual(m.feed(False), [])
        self.assertEqual(m.feed(True), [EV_ENTER])

    def test_no_repeat_while_matching(self):
        """持续在匹配界面不重复触发（否则每 2 秒弹一次）。"""
        m = MatchingMonitor()
        m.feed(False)
        m.feed(True)
        self.assertEqual(m.feed(True), [])
        self.assertEqual(m.feed(True), [])

    def test_leave_edge(self):
        m = MatchingMonitor()
        m.feed(False)
        m.feed(True)
        self.assertEqual(m.feed(False), [EV_LEAVE])

    def test_none_does_not_change_state(self):
        """读不到视图栈（None）不能当成「离开匹配」把提示收掉。"""
        m = MatchingMonitor()
        m.feed(False)
        m.feed(True)
        self.assertEqual(m.feed(None), [])
        self.assertTrue(m.in_matching)

    def test_first_true_announces_by_default(self):
        """首次就是 True -> 默认算一次进入（用户可能先开软件再进匹配）。"""
        m = MatchingMonitor()
        self.assertEqual(m.feed(True), [EV_ENTER])

    def test_first_true_quiet_when_not_known(self):
        """`known=False` 对齐 untapped 的 `prevState === null` 不弹。"""
        m = MatchingMonitor(known=False)
        self.assertEqual(m.feed(True), [])
        self.assertEqual(m.feed(False), [EV_LEAVE])

    def test_first_false_is_silent(self):
        m = MatchingMonitor()
        self.assertEqual(m.feed(False), [])

    def test_reset_clears_state(self):
        m = MatchingMonitor()
        m.feed(False)
        m.feed(True)
        m.reset()
        self.assertIsNone(m.in_matching)
        # reset 后首次 True 又算一次进入
        self.assertEqual(m.feed(True), [EV_ENTER])

    def test_full_cycle(self):
        m = MatchingMonitor()
        seq = [False, False, True, True, False, False, True]
        got = [m.feed(x) for x in seq]
        self.assertEqual(got, [[], [], [EV_ENTER], [], [EV_LEAVE], [],
                               [EV_ENTER]])


class ConstantsTest(unittest.TestCase):
    def test_states_distinct(self):
        self.assertEqual(len({STATE_KNOWN, STATE_UNKNOWN_LIVE,
                              STATE_UNKNOWN}), 3)

    def test_trusted_sources_cover_known_producers(self):
        """这些来源在项目里**确实会写进 my_deck.json**（见 core/my_deck.py
        与 tools/import_duel_records.py 的调用点）。少一个会让横幅误报未识别。"""
        for src in ('m5-guess', 'manual', 'log-deck'):
            self.assertIn(src, TRUSTED_SOURCES)

    def test_modes_need_edit(self):
        """DC 杯与活动局才需要「去编辑卡组」（2026-10-25 实机口径）。"""
        self.assertEqual(MODES_NEED_EDIT, frozenset({'dc', 'event'}))
        self.assertNotIn('ranked', MODES_NEED_EDIT)
        self.assertNotIn('peak', MODES_NEED_EDIT)


if __name__ == '__main__':
    unittest.main()
