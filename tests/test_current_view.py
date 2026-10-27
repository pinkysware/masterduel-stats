# -*- coding: utf-8 -*-
"""``core.memread.current_view`` 单测 —— 视图栈读取（O1）。

用 FakeMem 假内存整链（不需要开游戏）：
搭一个 ``ContentViewControllerManager`` 实例，``+0x48`` 挂 ``List<ViewController>``，
每个元素的对象头指向带类名的假 TypeInfo，验证读出来的是正确的类名列表。

⚠️ **`viewStack` 偏移 +0x48 是实机校准值**（2026-10-28 主界面，见模块头）。
本测试锁的是**读取链**与**语义判定**，不是偏移本身 ——
偏移变了（游戏大版本更新）本测试仍会绿，要靠 ``tmp/probe_current_view.py`` 重校准。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fakemem_obj import (  # noqa: E402
    FakeMem,
    ObjProc,
    build_named_typeinfo,
    build_ref_list,
)
from core.memread.current_view import (  # noqa: E402
    DUEL_PREFIX_LONG,
    DUEL_PREFIX_SHORT,
    INSTANCE_SLOT,
    MANAGER_NAME,
    MANAGER_NS,
    RESULT_VIEWS,
    VIEW_STACK_OFFSET,
    CurrentView,
    find_manager_typeinfo,
    list_manager_fields,
    read_current_view,
    singleton_address,
)


def build_manager(mem, view_names):
    """搭一个管理器实例：Instance 静态槽 + viewStack 指到假 ViewController 列表。"""
    ti = build_named_typeinfo(mem, MANAGER_NAME,
                              [('Instance', 0x00), ('cv', 0xE0),
                               ('transitionTime', 0xD8)])
    sb = mem.alloc(0x20)
    mem.put_u64(ti, 0xB8, sb)                    # static_fields

    obj = mem.alloc(0x100)
    mem.put_u64(obj, 0x00, ti)                   # 对象头
    mem.put_u64(sb, INSTANCE_SLOT, obj)          # Instance 槽

    views = []
    for n in view_names:
        vti = build_named_typeinfo(mem, n, [])
        v = mem.alloc(0x40)
        mem.put_u64(v, 0x00, vti)
        views.append(v)
    lst = build_ref_list(mem, views)
    mem.put_u64(obj, VIEW_STACK_OFFSET, lst)
    return ti, obj, views


#: ★ 假内存里**必须传 None 当 mirror** —— ``list_fields(strict=True)`` 会拿
#: mirror 的 base/末尾去校验字段名指针落点，而假 TypeInfo 的字段名字符串
#: 不在任何「真镜像」范围内，传 FakeMirror 会让整张字段表被判空
#: （实测踩过：list_items 直接返回 None，类名列表变成空）。
FAKE_MIRROR = None


class ReadCurrentViewTest(unittest.TestCase):
    def setUp(self):
        self.mem = FakeMem()
        self.proc = ObjProc(self.mem)
        self.mirror = FAKE_MIRROR

    def test_home_view(self):
        ti, _obj, _ = build_manager(self.mem, ['HomeViewController'])
        cv = read_current_view(self.proc, self.mirror, cw_ti=ti)
        self.assertIsNotNone(cv)
        self.assertEqual(cv.names, ['HomeViewController'])
        self.assertEqual(cv.top, 'HomeViewController')
        self.assertFalse(cv.in_duel)
        self.assertFalse(cv.in_result)

    def test_stack_order_bottom_to_top(self):
        names = ['HomeViewController', 'DeckSelectViewController2',
                 'DeckEditViewController2']
        ti, _obj, _ = build_manager(self.mem, names)
        cv = read_current_view(self.proc, self.mirror, cw_ti=ti)
        self.assertEqual(cv.names, names)
        self.assertEqual(cv.top, 'DeckEditViewController2')

    def test_empty_stack_is_empty_not_none(self):
        """空栈 = 还没建界面（正常态），与「读不到」区分开。"""
        ti, obj, _ = build_manager(self.mem, [])
        self.mem.put_u64(obj, VIEW_STACK_OFFSET, 0)
        cv = read_current_view(self.proc, self.mirror, cw_ti=ti)
        self.assertIsNotNone(cv)
        self.assertEqual(cv.names, [])
        self.assertIsNone(cv.top)

    def test_no_instance_returns_none(self):
        """Instance 槽为 0（还没建单例）-> None（链路断，不炸）。"""
        ti = build_named_typeinfo(self.mem, MANAGER_NAME, [('Instance', 0x00)])
        sb = self.mem.alloc(0x20)
        self.mem.put_u64(ti, 0xB8, sb)           # 静态区在但 Instance = 0
        self.assertIsNone(read_current_view(self.proc, self.mirror, cw_ti=ti))

    def test_zero_typeinfo_returns_none(self):
        self.assertIsNone(read_current_view(self.proc, self.mirror, cw_ti=0))

    def test_singleton_address(self):
        ti, obj, _ = build_manager(self.mem, ['HomeViewController'])
        self.assertEqual(singleton_address(self.proc, ti), obj)

    def test_singleton_address_without_instance(self):
        ti = build_named_typeinfo(self.mem, MANAGER_NAME, [('Instance', 0x00)])
        sb = self.mem.alloc(0x20)
        self.mem.put_u64(ti, 0xB8, sb)
        self.assertEqual(singleton_address(self.proc, ti), 0)

    def test_list_manager_fields(self):
        ti, _obj, _ = build_manager(self.mem, [])
        fields = list_manager_fields(self.proc, self.mirror, ti)
        self.assertIn('Instance', fields)
        self.assertEqual(fields['Instance'], 0x00)

    def test_find_manager_typeinfo_with_empty_table(self):
        """table 为空 -> find_typeinfo 走不到，返回 0（不炸）。"""
        self.assertEqual(
            find_manager_typeinfo(self.proc, self.mirror, b''), 0)


class InDuelTest(unittest.TestCase):
    """``in_duel`` 的判据 —— 长名 / 短名两种形态都认。"""

    def test_short_name_duel_view(self):
        cv = CurrentView(names=['HomeViewController', 'DuelStartViewController'],
                         top='DuelStartViewController')
        self.assertTrue(cv.in_duel)

    def test_long_name_duel_view(self):
        cv = CurrentView(names=['YgomGame.Duel.ViewerViewController'],
                         top='YgomGame.Duel.ViewerViewController')
        self.assertTrue(cv.in_duel)

    def test_any_layer_counts(self):
        """栈中间有对局视图也算 —— 转场期栈顶可能是别的类。"""
        cv = CurrentView(names=['HomeViewController', 'DuelStartViewController',
                                'DuelResultViewController'],
                         top='DuelResultViewController')
        self.assertTrue(cv.in_duel)

    def test_home_only_is_not_in_duel(self):
        cv = CurrentView(names=['HomeViewController'], top='HomeViewController')
        self.assertFalse(cv.in_duel)

    def test_none_names_is_not_in_duel(self):
        cv = CurrentView(names=None, top=None)
        self.assertFalse(cv.in_duel)

    def test_empty_names_is_not_in_duel(self):
        cv = CurrentView(names=[], top=None)
        self.assertFalse(cv.in_duel)


class InResultTest(unittest.TestCase):
    def test_duel_result_view(self):
        cv = CurrentView(names=['HomeViewController', 'DuelResultViewController'],
                         top='DuelResultViewController')
        self.assertTrue(cv.in_result)

    def test_rate_result_view(self):
        cv = CurrentView(
            names=['ColosseumDuelResultViewController_Rate'],
            top='ColosseumDuelResultViewController_Rate')
        self.assertTrue(cv.in_result)

    def test_duel_start_is_not_result(self):
        cv = CurrentView(names=['DuelStartViewController'],
                         top='DuelStartViewController')
        self.assertFalse(cv.in_result)

    def test_no_top(self):
        self.assertFalse(CurrentView(names=[], top=None).in_result)


class ConstantsTest(unittest.TestCase):
    """常量锁 —— 改动这些等于改动实机校准结果。"""

    def test_manager_namespace(self):
        """★ 实机确认：YgomGame.Menu（不是空、不是 YgomSystem）。"""
        self.assertEqual(MANAGER_NS, 'YgomGame.Menu')
        self.assertEqual(MANAGER_NAME, 'ContentViewControllerManager')

    def test_view_stack_offset(self):
        """★ 实机校准 2026-10-28：父类 YgomSystem.UI.ViewControllerManager 的字段。"""
        self.assertEqual(VIEW_STACK_OFFSET, 0x48)

    def test_instance_slot(self):
        self.assertEqual(INSTANCE_SLOT, 0x00)

    def test_duel_prefixes(self):
        self.assertEqual(DUEL_PREFIX_LONG, 'YgomGame.Duel.')
        self.assertEqual(DUEL_PREFIX_SHORT, 'Duel')

    def test_result_views(self):
        self.assertIn('DuelResultViewController', RESULT_VIEWS)
        self.assertIn('ColosseumDuelResultViewController_Rate', RESULT_VIEWS)


class ToDictTest(unittest.TestCase):
    def test_shape(self):
        cv = CurrentView(names=['HomeViewController'], top='HomeViewController')
        d = cv.to_dict()
        self.assertEqual(d['names'], ['HomeViewController'])
        self.assertEqual(d['top'], 'HomeViewController')
        self.assertIs(d['in_duel'], False)
        self.assertIs(d['in_result'], False)


if __name__ == '__main__':
    unittest.main()
