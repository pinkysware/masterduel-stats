# -*- coding: utf-8 -*-
"""core/modes.py 单测（M7）。

盯两件事：
1. **分类/名称口径必须转发 `core.storage`**，不许在这儿另写一份；
2. 没图标时 `icon_path()` 返回 **None**、`label()` 返回 **空串**，都不许编。
"""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import modes                                                # noqa: E402
from core import storage as S                                         # noqa: E402


class GroupTest(unittest.TestCase):

    def test_known_modes(self):
        self.assertEqual(modes.group(3), 'ranked')
        self.assertEqual(modes.group(19), 'peak')
        self.assertEqual(modes.group(12), 'dc')

    def test_other_modes_fall_into_event(self):
        self.assertEqual(modes.group(21), 'event')
        self.assertEqual(modes.group(7), 'event')       # 回放

    def test_none_or_garbage(self):
        self.assertIsNone(modes.group(None))
        # 脏值不该炸，落到 event 也行
        self.assertIn(modes.group('abc'), ('event', None))

    def test_delegates_to_storage_not_a_second_copy(self):
        """★ 分类必须走 storage.mode_group —— 别在这儿再写一份映射。"""
        for m in list(S.MODE_GROUP_BY_GAME_MODE) + [21, None]:
            self.assertEqual(modes.group(m), S.mode_group(m), 'game_mode=%r' % m)

    def test_names_come_from_storage(self):
        self.assertEqual(modes.group_name('peak'), S.MODE_GROUP_NAMES['peak'])
        self.assertEqual(modes.label(19), '巅峰决斗')
        self.assertEqual(modes.label(3), '排名决斗')

    def test_empty_string_when_unknown(self):
        """★ 认不出就空着，不要编。"""
        self.assertEqual(modes.label(None), '')
        self.assertEqual(modes.group_name(None), '')
        self.assertEqual(modes.group_name('nope'), '')


class IconTest(unittest.TestCase):

    def test_peak_icon_exists_and_is_not_empty(self):
        p = modes.icon_path('peak')
        self.assertIsNotNone(p, '巅峰决斗的图标应该已落地')
        full = os.path.join(PROJ, p)
        self.assertTrue(os.path.exists(full))
        self.assertGreater(os.path.getsize(full), 5000)

    def test_peak_icon_reachable_via_game_mode(self):
        """`game_mode=19` → 图标（这才是界面会用的入口）。"""
        self.assertEqual(modes.icon_for(19), modes.icon_path('peak'))

    def test_missing_icon_returns_none_not_a_fallback(self):
        """★ 没图标就 None —— **不许回落别的图标**（显示错模式比不显示更糟）。"""
        self.assertIsNone(modes.icon_path('ranked'))
        self.assertIsNone(modes.icon_for(3))
        self.assertIsNone(modes.icon_path(None))
        self.assertIsNone(modes.icon_path('nope'))

    def test_peak_and_dc_need_an_icon(self):
        """★ 需要模式图标的分类 = **巅峰 + DC 杯**。

        * 天梯用段位图标（不用模式图标）；
        * 用户 2026-09-14 说过「dc杯和活动不需要图标」，
          **DC 那一半已被 2026-10-23 推翻**（用户要求给 DC 杯加图标）；
        * 活动杯赛**仍然不做** —— 每月换 logo，等真到那个活动再定。
        """
        self.assertEqual(modes.WANTED_ICONS, ('peak', 'dc'))

    def test_missing_icons_is_empty_in_a_healthy_repo(self):
        """需要的都拿到了 —— 所以正常就是空列表（这个函数现在当"素材丢没丢"自检）。"""
        self.assertEqual(modes.missing_icons(), [])

    def test_ranked_and_event_are_not_reported_as_missing(self):
        """天梯（用段位图标）与活动杯赛（每月换）不算"缺素材"。"""
        miss = modes.missing_icons()
        for k in ('ranked', 'event'):
            self.assertNotIn(k, miss, '%s 不需要图标，不该被算成缺素材' % k)


class BadgeTest(unittest.TestCase):
    """`badge()` = 界面那一格的「等级标识」：天梯给段位、巅峰给巅峰。"""

    def test_ranked_uses_rank_icon_and_label(self):
        """★ 天梯那一行显示的是**该局的段位图标**，不是模式图标。"""
        icon, text = modes.badge(3, rank=5, rate=2)
        self.assertEqual(icon, 'data/rank_icons/Platinum.webp')
        self.assertEqual(text, 'Platinum 2')

    def test_ranked_without_rank_data_is_empty_not_guessed(self):
        icon, text = modes.badge(3, rank=None, rate=None)
        self.assertIsNone(icon)
        self.assertEqual(text, '')

    def test_peak_uses_its_own_icon(self):
        icon, text = modes.badge(19)
        self.assertEqual(icon, 'data/mode_icons/peak.webp')
        self.assertEqual(text, '巅峰决斗')

    def test_event_has_name_even_without_icon(self):
        """图标可以缺，**名字不能缺**（缺名字界面就成空白了）。"""
        icon, text = modes.badge(21)
        self.assertEqual(text, '活动决斗')
        self.assertIsNone(icon)

    def test_dc_uses_its_own_icon(self):
        """DC 杯（2026-10-23 起）有自己的图标：`data/mode_icons/dc.webp`。"""
        icon, text = modes.badge(12)
        self.assertEqual(icon, 'data/mode_icons/dc.webp')
        self.assertEqual(text, 'DC杯')

    def test_badge_never_raises(self):
        for gm in (None, 'x', 0, 999):
            modes.badge(gm)

    def test_icon_dir_is_under_data(self):
        self.assertTrue(modes.ICON_DIR.replace('\\', '/').endswith('data/mode_icons'))

    def test_wanted_icons_are_present_on_disk(self):
        for k in modes.WANTED_ICONS:
            self.assertIsNotNone(modes.icon_path(k), k)


if __name__ == '__main__':
    unittest.main()
