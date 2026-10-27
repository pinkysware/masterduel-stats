# -*- coding: utf-8 -*-
"""``ui.opp_deck_manager`` —— 「卡组名管理」的分组 / 建条目 / 保存行为。

锁两个**用户实报过的 bug**（2026-10-28）：
1. **保存后自定义卡组与站内混在一起**（分隔行被无条件隐藏）；
2. **新加的自定义卡组落进站内组**（永远显示在分隔线上方，标志丢失）。

⚠️ 这两个都靠 Qt 界面对象，所以用 offscreen；CSV 一律复制到临时目录，
**绝不碰真库**（`data/deck_name_cn.csv` 不可再生）。
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtWidgets import QApplication
    _HAS_QT = True
except Exception:                                                # noqa: BLE001
    _HAS_QT = False

_REAL_CSV = os.path.join(ROOT, 'data', 'deck_name_cn.csv')


class _Sig(object):
    def connect(self, *a):
        pass


class _FakeRunner(object):
    """假的 `--cn-only` 子进程（不真跑脚本）。"""

    def __init__(self, *a, **k):
        self.done = _Sig()

    def start(self):
        pass

    def stop(self):
        pass


def _make_manager(csv_path):
    from ui.opp_deck_manager import OppDeckManager
    m = OppDeckManager(csv_path=csv_path)
    m._runner_factory = lambda *a, **k: _FakeRunner()
    return m


def _name_of(m, item):
    from core import deck_csv
    return deck_csv.get(item.csv_row, m.idx, '站内英文名')


@unittest.skipUnless(_HAS_QT, '需要 PySide6')
class OppDeckManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = os.path.join(self.tmp, 'deck_name_cn.csv')
        if os.path.exists(_REAL_CSV):
            shutil.copy(_REAL_CSV, self.csv)
        else:                                    # 干净克隆：造一张最小表
            with open(self.csv, 'w', encoding='utf-8', newline='') as f:
                f.write('站内英文名,中文名,日文名,别名,分组,隐藏,备注\n'
                        'Branded,烙印,烙印,,Tier2,,\n'
                        '我的自组,我的自组,,,,,\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------ bug 1：分隔行
    def test_divider_stays_visible_after_save(self):
        """保存**不该**把「—— 自定义卡组 ——」分隔行藏起来。

        用户原话：「点保存，自定义卡组和非自定义卡组会混在一起，
        自定义卡组标志会消失」。根因是 `save()` 里无条件
        `custom_divider.setVisible(False)`。
        """
        m = _make_manager(self.csv)
        m.show()
        self.assertGreater(len(m.custom_csv_ui), 0, '测试数据里应有自定义条目')
        self.assertTrue(m.custom_divider.isVisible())
        m.save()
        self.assertTrue(m.custom_divider.isVisible(),
                        '保存后分隔行必须还在（否则两组视觉上混成一片）')

    def test_divider_hidden_only_when_no_custom_left(self):
        """自定义条目全部删光后，分隔行才该收起。"""
        m = _make_manager(self.csv)
        m.show()
        for item in list(m.custom_csv_ui):
            m.remove_row(item)
        m.save()
        self.assertEqual(len(m.custom_csv_ui), 0)
        self.assertFalse(m.custom_divider.isVisible())

    # ------------------------------------------------ bug 2：新条目落错组
    def test_new_custom_goes_to_custom_group(self):
        """新加的自定义卡组必须进 `custom_csv_ui`（分隔线**下方**）。

        原来 `add_custom` 直接 `rows_ui.append(item)`，落进站内组 ——
        永远显示在分隔线上方，保存后也找不回来。
        """
        m = _make_manager(self.csv)
        m.show()
        before = len(m.custom_csv_ui)
        self.assertTrue(m.add_custom('测试自组卡组'))
        self.assertEqual(len(m.custom_csv_ui), before + 1)
        self.assertIn('测试自组卡组',
                      [_name_of(m, r) for r in m.custom_csv_ui])

    def test_new_custom_survives_save_in_custom_group(self):
        """保存后它仍在自定义组（不会因为重排掉进站内组）。"""
        m = _make_manager(self.csv)
        m.show()
        m.add_custom('测试自组卡组')
        m.save()
        self.assertIn('测试自组卡组',
                      [_name_of(m, r) for r in m.custom_csv_ui])
        self.assertTrue(m.custom_divider.isVisible())

    def test_new_custom_written_to_csv(self):
        """保存要真的写进 CSV（用户点保存后重开也该在）。"""
        from core import deck_csv
        m = _make_manager(self.csv)
        m.show()
        m.add_custom('测试自组卡组')
        m.save()
        header, body = deck_csv.read(self.csv)
        idx = deck_csv.index_map(header)
        names = [deck_csv.get(r, idx, '站内英文名') for r in body]
        self.assertIn('测试自组卡组', names)

    def test_duplicate_name_rejected(self):
        """重名不重复加（站内名 / 中文名 / 别名任一撞上都算）。"""
        m = _make_manager(self.csv)
        m.show()
        self.assertFalse(m.add_custom('Branded'))

    def test_removing_custom_keeps_lists_in_sync(self):
        """删一行要同时从 `rows_ui` 与 `custom_csv_ui` 移除（否则崩）。"""
        m = _make_manager(self.csv)
        m.show()
        m.add_custom('测试自组卡组')
        item = [r for r in m.custom_csv_ui
                if _name_of(m, r) == '测试自组卡组'][0]
        m.remove_row(item)
        self.assertNotIn(item, m.rows_ui)
        self.assertNotIn(item, m.custom_csv_ui)


class ColumnWidthTest(unittest.TestCase):
    def test_alias_column_narrowed(self):
        """★ 2026-10-28 用户：缩减别名列宽（5 -> 4）。

        ⚠️ 改这个常量会同时改表头与所有行 —— 两处共用，别只改一处。
        """
        from ui.opp_deck_manager import COL_STRETCH
        self.assertEqual(COL_STRETCH, (2, 4, 3))


if __name__ == '__main__':
    unittest.main()
