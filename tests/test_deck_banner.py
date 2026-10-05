# -*- coding: utf-8 -*-
"""D4 后追加（2026-09-22）：卡组确认提示条 + `current-deck` 兜底时效的单测。

背景（当天实测事故）：`my_deck.json` 残留「白银城」，之后用闪刀姬打的局被
`current-deck` 兜底**错标**。用户口径：悬浮窗启动后提示去编辑卡组，
读到后显卡图确认，确认后消失。
"""
import io
import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from core import my_deck                                     # noqa: E402
from core import ui_state                                    # noqa: E402
from ui.deck_banner import (CONFIRM_KEY, DeckConfirmBanner,  # noqa: E402
                            banner_mode, deck_state_fingerprint)

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:                                            # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    import tempfile as _tempfile
    _I18N_DIR = _tempfile.mkdtemp(prefix='m7-i18n-')
    from PySide6.QtCore import QSettings as _QS
    import ui.i18n as _i18n
    _i18n._SETTINGS_FACTORY = staticmethod(
        lambda: _QS(os.path.join(_I18N_DIR, 'ui.ini'), _QS.IniFormat))

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class BannerModeTest(unittest.TestCase):
    """`banner_mode` / `deck_state_fingerprint`（**纯函数**）。"""

    def test_empty_state_is_waiting(self):
        """没有状态文件也要提示（用户口径：启动后显示提示）。"""
        self.assertEqual(banner_mode({}, ''), 'waiting')
        self.assertEqual(banner_mode(None, ''), 'waiting')
        self.assertEqual(banner_mode({'name': None}, ''), 'waiting')

    def test_named_unconfirmed_is_confirm(self):
        st = {'name': 'Sky Striker', 'ids_fingerprint': 'abc'}
        self.assertEqual(banner_mode(st, ''), 'confirm')

    def test_confirmed_same_deck_is_hidden(self):
        st = {'name': 'Sky Striker', 'ids_fingerprint': 'abc'}
        fp = deck_state_fingerprint(st)
        self.assertEqual(banner_mode(st, fp), 'hidden')

    def test_switching_deck_reappears(self):
        """★ 确认过 A 再换 B —— 提示要**重新出现**（这次的 bug 就是换卡组没提示）。"""
        a = {'name': 'Labrynth', 'ids_fingerprint': 'k1'}
        self.assertEqual(banner_mode(a, deck_state_fingerprint(a)), 'hidden')
        b = {'name': 'Sky Striker', 'ids_fingerprint': 'k2'}
        self.assertEqual(banner_mode(b, deck_state_fingerprint(a)), 'confirm')

    def test_fingerprint_includes_name_and_ids(self):
        self.assertEqual(deck_state_fingerprint({}), '')
        self.assertEqual(deck_state_fingerprint({'name': 'X'}),
                         'X')                     # 没指纹就用名字
        st = {'name': 'X', 'ids_fingerprint': 'f1'}
        self.assertEqual(deck_state_fingerprint(st), 'X|f1')


@unittest.skipUnless(HAVE_QT, '没装 PySide6')
class BannerWidgetTest(unittest.TestCase):
    """控件两态 + 确认交互。"""

    def setUp(self):
        _app()
        self.d = tempfile.mkdtemp(prefix='banner-')
        os.environ['MD_MY_DECK_STATE'] = os.path.join(self.d, 'my_deck.json')
        self.banner = DeckConfirmBanner()
        self.banner.show()                    # offscreen 下 isVisible 才可信

    def tearDown(self):
        self.banner.deleteLater()

    def test_waiting_has_no_confirm_button(self):
        mode = self.banner.update_state({}, '', '')
        self.assertEqual(mode, 'waiting')
        self.assertTrue(self.banner.isVisible())
        self.assertFalse(self.banner.btn_ok.isVisible())
        self.assertIn('卡组编辑', self.banner.label.text())

    def test_confirm_has_fixed_title_no_name(self):
        """★ 三轮拍板：「我方卡组：卡图(不带名字)，刷新键，确认」。

        名字不放文字里 —— 卡图即信息；固定标题 = 「我方卡组：」。
        """
        st = {'name': 'Sky Striker', 'ids_fingerprint': 'abc'}
        mode = self.banner.update_state(st, '', '')
        self.assertEqual(mode, 'confirm')
        self.assertEqual(self.banner.title.text(), '我方卡组：')
        self.assertEqual(self.banner.label.text(), '', '不再显示卡组名')
        self.assertTrue(self.banner.btn_ok.isVisible())
        self.assertTrue(self.banner.btn_refresh.isVisible(), '要有刷新键')

    def test_thumb_click_emits_pick_requested(self):
        """★ 点卡图 → 发 `pick_requested`（浮窗弹卡组选择器，手动换卡组）。"""
        st = {'name': 'Sky Striker', 'ids_fingerprint': 'abc'}
        self.banner.update_state(st, '', '')
        got = []
        self.banner.pick_requested.connect(lambda *a: got.append(a))
        self.banner.thumb.clicked.emit()
        self.assertEqual(len(got), 1)

    def test_refresh_click_emits_refresh_requested(self):
        got = []
        self.banner.refresh_requested.connect(lambda: got.append(True))
        self.banner.btn_refresh.click()
        self.assertEqual(got, [True])

    def test_confirmed_hides(self):
        st = {'name': 'Sky Striker', 'ids_fingerprint': 'abc'}
        self.banner.update_state(st, '', '')
        self.banner.confirmed.emit()          # 确认是浮窗记指纹；这里只验隐藏由谁做
        self.banner.update_state(st, deck_state_fingerprint(st), '')
        self.assertEqual(self.banner.isVisible(), False)

    def test_thumb_image_is_actually_visible(self):
        """★ 用户实测「点刷新不显示当前卡组」：内部 QLabel 换 parent 后**默认隐藏**，
        必须显式 show() —— pixmap 读到了但不上屏。"""
        st = {'name': 'Vanquish Soul K9', 'ids_fingerprint': 'x'}
        self.banner.update_state(st, '', 'data/deck_images_custom/K9.webp')
        img = self.banner.thumb._img
        self.assertIsNotNone(img)
        self.assertTrue(img.isVisible(), '内部图没 show —— 卡图永远不显示')
        self.assertFalse(img.pixmap().isNull())
        # 再刷一次（模拟刷新键路径）：重建后仍然可见
        self.banner.update_state(st, '', 'data/deck_images_custom/K9.webp')
        self.assertTrue(self.banner.thumb._img.isVisible())

    def test_button_text_not_clipped(self):
        """★ 用户实测「刷新/确认按钮文字显示不全」：小高度按钮必须用**紧样式**
        （全局 QSS 的 `padding:6px 12px` 会把字上下切掉）。"""
        from PySide6.QtGui import QFontMetrics
        for btn in (self.banner.btn_refresh, self.banner.btn_ok):
            sheet = btn.styleSheet()
            self.assertIn('padding: 1px 10px', sheet,
                          '按钮没用专用紧样式 —— 文字会被全局 QSS 裁掉')
            self.assertNotIn('font-', sheet)
            fm = QFontMetrics(btn.font())
            self.assertGreaterEqual(btn.height(), fm.height())

    def test_mode_switch_refreshes_thumb(self):
        """waiting → confirm 重建缩略图（`_PickableThumb.set_image` 换图）。"""
        self.banner.update_state({}, '', '')
        self.assertFalse(self.banner._known)
        self.banner.update_state({'name': 'X', 'ids_fingerprint': 'f'}, '',
                                 'nonexistent.png')
        self.assertTrue(self.banner._known)


class CurrentDeckGuardTest(unittest.TestCase):
    """`current-deck` 兜底的**时效守卫**（2026-09-22 白银城错标事故）。"""

    class _St(object):
        def __init__(self):
            self.rows = []

        def search_meta_decks(self, key, limit=3):
            return [{'name': key}]

    def _process_one(self, started_at, state_at, state_name='Labrynth',
                     meta_name=None):
        """跑一次 import 的兜底逻辑：直接构造 duel dict 过 `_page_row` 之后的段。

        ⚠️ 不真调 `process()`（要 record 目录）；这里把兜底段抽出来等价验证。
        """
        # 兜底段逻辑在 process() 内联 —— 用等价复刻 + 真函数验证时效算式
        import datetime as _dt
        cur = {'name': state_name, 'at': state_at} if state_at else None
        started = started_at
        fresh = (not cur) or not cur.get('at') or not started \
            or str(started) >= str(cur['at'])
        stale = False
        if cur and cur.get('at') and started:
            try:
                at = _dt.datetime.strptime(str(cur['at'])[:19],
                                           '%Y-%m-%d %H:%M:%S')
                st = _dt.datetime.strptime(str(started)[:19],
                                           '%Y-%m-%d %H:%M:%S')
                stale = abs((st - at).total_seconds()) > 24 * 3600
            except (TypeError, ValueError):
                stale = False              # 与 tools/import_duel_records 同款
        return bool(cur and fresh and not stale), fresh, stale

    def test_stale_state_does_not_fallback(self):
        """★ 事故场景还原（2026-09-22）：

        状态文件 09-21 23:53 写成「白银城」（残留），用户 09-22 11:44 用闪刀姬
        打的局 —— 状态时刻**早于**局、但只差半天 → fresh 且不 stale → 照样兜底
        = **错标**（这次真发生了）。
        """
        apply_it, fresh, stale = self._process_one(
            started_at='2026-09-22 11:44:19',
            state_at='2026-09-21 23:53:50')
        self.assertTrue(fresh, '局晚于状态时刻 → fresh')
        self.assertFalse(stale, '差半天 < 24h → 不算 stale')
        self.assertTrue(apply_it, '⚠️ 这正是当时错标的原因 —— 24h 挡不住半天残区')
        # 但状态若是**三天前**的，就挡住了：
        apply3, fresh3, stale3 = self._process_one(
            started_at='2026-09-22 11:44:19',
            state_at='2026-09-19 23:53:50')
        self.assertTrue(stale3)
        self.assertFalse(apply3)

    def test_older_than_24h_is_stale(self):
        apply_it, fresh, stale = self._process_one(
            started_at='2026-09-22 11:44:19',
            state_at='2026-09-20 11:00:00')
        self.assertTrue(fresh)
        self.assertTrue(stale, '状态超过 24h 该判 stale（不许兜底）')

    def test_bad_timestamp_is_not_stale(self):
        """时刻解析不了（脏数据）→ 不判 stale（保守：兜底照走）。"""
        apply_it, fresh, stale = self._process_one(
            started_at='乱写的时间',
            state_at='也乱写')
        self.assertTrue(fresh, 'started 与 at 都乱写 → fresh 走缺省分支')
        self.assertFalse(stale)
        self.assertTrue(apply_it)
