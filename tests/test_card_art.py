# -*- coding: utf-8 -*-
"""`core.card_art`（卡绘缓存）单测 —— **一律不打网络**（fetch 打桩）。

锁四件事：① Konami 密码映射（游戏 id / 英文名两条路 + 角括号归一）
② 缓存命中直接返回 ③ 下载成功落盘 ④ 失败返回 None 不抛。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from core import card_art

_APP = None


def _app():
    """QApplication 全进程一份（Qt 图像操作要它； 尤其）。"""
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        _APP = QApplication.instance() or QApplication([])
    return _APP


class KonamiIdTest(unittest.TestCase):
    """卡 id / 英文名 -> Konami 密码（用真卡表，只读）。"""

    def test_by_card_id(self):
        # 灰流丽：游戏内 id 12950 -> Konami 14558127
        self.assertEqual(card_art.konami_id('12950'), '14558127')

    def test_by_en_name_exact(self):
        self.assertEqual(card_art.konami_by_en('Abyss Dweller'), '21044178')

    def test_by_en_name_angle_brackets(self):
        """★ 站内写 `Maliss P White Rabbit`、ygocdb 写 `Maliss <P> White Rabbit`
        —— 4 张 Maliss 卡就卡在这个角括号上（2026-09-21 实测）。"""
        self.assertEqual(card_art.konami_by_en('Maliss P White Rabbit'),
                         card_art.konami_by_en('Maliss <P> White Rabbit'))

    def test_unknown_returns_none(self):
        self.assertIsNone(card_art.konami_id('999999999'))
        self.assertIsNone(card_art.konami_by_en('Not A Real Card xyz'))


class YgocdbFallbackTest(unittest.TestCase):
    """★ BUG-B2A-09：完整卡表不在（干净 clone / lite 包）时走**精简卡表**。

    `card_art._load()` 靠 `card_names.ygocdb()` 建索引，而那份
    13MB 原始卡表是 **gitignore** 的 —— 回落链路坏了要等
    用户报「图全空」才会发现（本机有完整包，所以看不到）。
    """

    def setUp(self):
        #: 把完整卡表路径指向不存在的地方 -> `ygocdb()` 退回
        #: `data/card_index.json`（精简卡表，随发布仓库带）。
        from core import card_names
        self._cn = card_names
        self._old_path = card_names.YGOCDB_PATH
        self._old_index = card_names.CARD_INDEX_PATH
        self._old_loaded = card_art._LOADED
        self._old_konami = dict(card_art._KONAMI)
        self._old_en = dict(card_art._EN)
        self._old_en_sq = dict(card_art._EN_SQUEEZED)
        card_names.YGOCDB_PATH = os.path.join(self.tmp(), 'no-such-cards.json')
        card_names.clear_ygocdb_cache()
        self._reset_art_index()

    def tearDown(self):
        self._cn.YGOCDB_PATH = self._old_path
        self._cn.CARD_INDEX_PATH = self._old_index
        self._cn.clear_ygocdb_cache()
        card_art._LOADED = self._old_loaded
        card_art._KONAMI.clear()
        card_art._KONAMI.update(self._old_konami)
        card_art._EN.clear()
        card_art._EN.update(self._old_en)
        card_art._EN_SQUEEZED.clear()
        card_art._EN_SQUEEZED.update(self._old_en_sq)

    @staticmethod
    def _reset_art_index():
        """清掉 card_art 的**三张索引** + `_LOADED`（下次调用重建）。"""
        card_art._LOADED = False
        card_art._KONAMI.clear()
        card_art._EN.clear()
        card_art._EN_SQUEEZED.clear()

    def tmp(self):
        if not hasattr(self, '_tmp'):
            self._tmp = tempfile.mkdtemp(prefix='no-ygocdb-')
            self.addCleanup(shutil.rmtree, self._tmp, True)
        return self._tmp

    def test_falls_back_to_card_index(self):
        """完整包不在 -> `konami_id(12950)` 仍能拿到（走精简卡表）。"""
        self.assertEqual(card_art.konami_id('12950'), '14558127')

    def test_english_index_also_works_on_fallback(self):
        """英文名那张索引同样靠得住（禁限表走这条）。"""
        self.assertEqual(card_art.konami_by_en('Abyss Dweller'), '21044178')

    def test_no_index_at_all_degrades_quietly(self):
        """两份卡表都没了 -> 返回 None，**不抛**（界面正常开，卡图留空）。"""
        self._cn.CARD_INDEX_PATH = os.path.join(self.tmp(), 'no-index.json')
        self._cn.clear_ygocdb_cache()
        self._reset_art_index()
        self.assertIsNone(card_art.konami_id('12950'))
        self.assertIsNone(card_art.konami_by_en('Abyss Dweller'))


class CacheTest(unittest.TestCase):
    """缓存行为 —— 网络一律打桩。"""

    def setUp(self):
        _app()
        self.tmp = tempfile.mkdtemp(prefix='card-art-')
        self._old_dir = card_art.CACHE_DIR
        card_art.CACHE_DIR = self.tmp

    def tearDown(self):
        card_art.CACHE_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_art_path_none_when_not_cached(self):
        """没缓存 -> None（纯本地查，**不联网**）。"""
        self.assertIsNone(card_art.art_path('12950'))
        self.assertIsNone(card_art.art_path_by_en('Abyss Dweller'))

    def test_fetch_downloads_and_caches(self):
        """下载成功 -> 落盘 + 返回路径；第二次走缓存不再下载。"""
        png = self._tiny_png()
        with mock.patch.object(card_art, '_download', return_value=png) as dl:
            p = card_art.fetch_art(cid='12950')
            self.assertTrue(p and os.path.exists(p), '该落盘')
            self.assertEqual(dl.call_count, 1)
            p2 = card_art.fetch_art(cid='12950')       # 第二次
            self.assertEqual(p2, p)
            self.assertEqual(dl.call_count, 1, '命中缓存就不该再下')
        self.assertEqual(card_art.art_path('12950'), p)

    def test_fetch_failure_returns_none(self):
        """下载失败 -> None，**不抛**（界面留占位）。"""
        with mock.patch.object(card_art, '_download', return_value=None):
            self.assertIsNone(card_art.fetch_art(cid='12950'))

    def test_fetch_unknown_card_returns_none(self):
        with mock.patch.object(card_art, '_download') as dl:
            self.assertIsNone(card_art.fetch_art(cid='999999999'))
            dl.assert_not_called()

    @staticmethod
    def _tiny_png() -> bytes:
        """一张真 PNG（Qt 要能解码才存得下 webp）。

        ⚠️ **别用 `QBuffer(QByteArray())` 那种匿名临时对象** —— 临时 QByteArray
        会被 Python GC 掉，Qt 再去访问就挂住（2026-09-21 实测：单跑某个用例能过、
        连起来跑必卡死）。写成临时文件再读回，没有生命周期陷阱。
        """
        import tempfile
        from PySide6.QtGui import QImage
        img = QImage(8, 8, QImage.Format_RGB32)
        img.fill(0x336699)
        fd, tmp = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        try:
            if not img.save(tmp, 'PNG'):
                raise AssertionError('测试用的 PNG 存不出来')
            with open(tmp, 'rb') as fh:
                return fh.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass



class CroppedFallbackTest(unittest.TestCase):
    """★ 2026-10-24：纯卡绘**两级回落** —— 用户层没有、随包素材层有 → 读得到。

    背景：`fetch_cropped` 全项目**零调用**，新机器缓存目录必空；12 张手坑
    纯卡绘随包预置（assets 层），靠 `paths.resolve` 回落才读得到 ——
    不回落则手坑格 100% 悠悠兜底（2026-10-24 用户实机报的全默认图）。
    """

    def _resolve_factory(self, td):
        def fake(*parts):
            user = os.path.join(td, *parts)
            if os.path.exists(user):
                return user
            return os.path.join(PROJ, 'data', *parts)
        return fake

    def test_falls_back_to_package_assets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(card_art.paths, 'resolve',
                                   self._resolve_factory(td)):
                #: 灰流丽：游戏 id 12950 -> 14558127.webp（随包预置里有）
                p = card_art.cropped_path('12950')
            self.assertIsNotNone(p)
            self.assertEqual(os.path.basename(p), '14558127.webp')
            #: 来自随包层（不是空的用户层）
            self.assertFalse(os.path.dirname(p).startswith(td))
            self.assertTrue(os.path.exists(p))

    def test_data_layer_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, 'card_art_cropped')
            os.makedirs(dst)
            with open(os.path.join(dst, '14558127.webp'), 'wb') as f:
                f.write(b'x')
            with mock.patch.object(card_art.paths, 'resolve',
                                   self._resolve_factory(td)):
                p = card_art.cropped_path('12950')
            self.assertTrue(p.startswith(td))     # 用户层优先

if __name__ == '__main__':
    unittest.main()
