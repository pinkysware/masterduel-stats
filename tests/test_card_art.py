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


if __name__ == '__main__':
    unittest.main()
