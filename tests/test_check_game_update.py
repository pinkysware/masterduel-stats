# -*- coding: utf-8 -*-
"""`tools/check_game_update.py` 的单测。

**为什么值得锁**：这个工具只在「游戏更新那几天」被跑一次 —— 平时没人看它。
而它一旦悄悄失效（依赖清单推不出来 / diff 永远返回空），更新当天就会给出
「✅ 没有发现差异」的**假绿**，白白浪费一次排查窗口。

所以这里锁的是它的**判据**，不是它的输出格式：

* 依赖清单必须**真从代码里推出来**（新加的 Reader 不用回来改这个文件）；
* 每一种「更新会弄坏的东西」都必须被抓到（metadata 版本 / Unity 版本 /
  类名消失 / 字段名消失 / 文件变了）；
* 「什么都没变」必须真的返回空 —— 否则工具会天天喊狼来了。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import check_game_update as C  # noqa: E402


class TestDependencyCollection(unittest.TestCase):
    """依赖清单：从代码推，不抄第二份。"""

    def setUp(self):
        self.classes, self.fields, self.notes = C.collect_dependencies()

    def test_finds_the_core_classes(self):
        got = {'%s.%s' % (ns, n) if ns else n for ns, n in self.classes}
        for want in ('YgomGame.Duel.Engine', 'YgomGame.Duel.DuelClient',
                     'YgomGame.Duel.DuelLogController',
                     'YgomSystem.Utility.ClientWork'):
            self.assertIn(want, got, '依赖清单里少了 %s（AST 推导失效了？）' % want)

    def test_finds_class_from_constant_not_call(self):
        """`DeckEditViewController2` 只以 `CLASS_NAME` 常量出现，也要抓到。"""
        names = {n for _ns, n in self.classes}
        self.assertIn('DeckEditViewController2', names)
        self.assertIn('PresentBoxViewController', names)

    def test_finds_fields_from_reader_whitelists(self):
        """字段白名单是「游戏里改个名就断链」的入口，一个都不能漏。"""
        for want in ('threadWork', 'currentEngineData', 'nTurnNum',
                     'm_LogBaseItemList', 'player0Name', 'm_LPCurrent',
                     'pvpFinished', 'gameMode'):
            self.assertIn(want, self.fields, '字段清单里少了 %r' % want)

    def test_notes_prove_it_read_the_sources(self):
        self.assertTrue(self.notes, '没有任何「从哪推出来」的记录 = 可能在硬编码')

    def test_matches_module_constants(self):
        """推出来的字段必须覆盖各 Reader 自己声明的白名单（防两处漂移）。"""
        from core.memread import duel_log, duel_settings, duel_state

        expected = set()
        for cls in (duel_state.DuelStateReader,):
            for attr in ('ENGINE_FIELDS', 'THREADWORK_FIELDS', 'CED_FIELDS',
                         'DUELINFO_FIELDS', 'DUELCLIENT_FIELDS',
                         'DUELHUD_FIELDS', 'LP_FIELDS'):
                expected.update(getattr(cls, attr))
        expected.update(duel_log.CONTROLLER_FIELDS)
        expected.update(duel_log.LIST_FIELDS)
        expected.update(duel_settings.DUELCLIENT_FIELDS)
        expected.update(duel_settings.INITIALIZER_FIELDS)
        missing = sorted(expected - self.fields)
        self.assertEqual(missing, [], '依赖清单与 Reader 白名单不一致：%s' % missing)


class TestOfflineDiff(unittest.TestCase):
    """diff() 是「更新后第一眼」的全部 —— 每种损坏都要抓到。"""

    def setUp(self):
        self.base = {
            'unity_version': '6000.0.61f1',
            'metadata': {'exists': True, 'valid': True, 'version': 31,
                         'string_size': 2954908, 'size': 17711668},
            'file_sizes': {'GameAssembly.dll': 66766848,
                           'masterduel_Data/data.unity3d': 127006540},
            'names': {'missing_classes': [], 'missing_namespaces': [],
                      'missing_fields': []},
        }

    def _diffs(self, mutate):
        after = copy.deepcopy(self.base)
        mutate(after)
        return C.diff(self.base, after)

    def test_identical_gives_no_noise(self):
        self.assertEqual(self._diffs(lambda a: None), [],
                         '没变化时必须返回空，否则工具天天喊狼来了')

    def test_metadata_version_bump_is_bad(self):
        rows = self._diffs(lambda a: a['metadata'].update(version=32))
        bad = [t for s, t in rows if s == 'BAD']
        self.assertTrue(any('v31 -> v32' in t for t in bad), rows)
        self.assertTrue(any('重新校准' in t for t in bad), rows)

    def test_metadata_becoming_invalid_is_bad(self):
        rows = self._diffs(lambda a: a['metadata'].update(valid=False))
        self.assertTrue(any(s == 'BAD' and '头部' in t for s, t in rows), rows)

    def test_unity_bump_is_warn(self):
        rows = self._diffs(lambda a: a.update(unity_version='6000.2.10f1'))
        self.assertTrue(any(s == 'WARN' and 'Unity' in t for s, t in rows), rows)

    def test_missing_class_is_bad(self):
        rows = self._diffs(
            lambda a: a['names']['missing_classes'].append('DuelLogController'))
        bad = [t for s, t in rows if s == 'BAD']
        self.assertTrue(any('DuelLogController' in t for t in bad), rows)

    def test_missing_field_is_bad(self):
        rows = self._diffs(
            lambda a: a['names']['missing_fields'].append('m_Host'))
        bad = [t for s, t in rows if s == 'BAD']
        self.assertTrue(any('m_Host' in t for t in bad), rows)

    def test_name_coming_back_is_only_info(self):
        """基线里缺、现在有了 = 不是故障（别报成 BAD 吓人）。"""
        self.base['names']['missing_classes'] = ['OldName']
        rows = self._diffs(
            lambda a: a['names'].update(missing_classes=[]))
        self.assertTrue(any(s == 'INFO' for s, _t in rows), rows)
        self.assertFalse(any(s == 'BAD' for s, _t in rows), rows)

    def test_file_size_change_is_reported(self):
        rows = self._diffs(
            lambda a: a['file_sizes'].update({'GameAssembly.dll': 1}))
        self.assertTrue(any('GameAssembly.dll' in t for _s, t in rows), rows)


class TestNameLookup(unittest.TestCase):
    """`check_names()` 的查法必须与 `find_string_offset` 同口径。"""

    def test_finds_names_between_nuls(self):
        table = b'\x00Engine\x00DuelClient\x00threadWork\x00'
        got = C.check_names(table, {('YgomGame.Duel', 'Engine')},
                            {'threadWork'})
        self.assertEqual(got['missing_classes'], [])
        self.assertEqual(got['missing_fields'], [])

    def test_finds_name_at_table_start(self):
        """表首条目前面没有 NUL —— 这也算存在（`find_string_offset` 同款处理）。"""
        table = b'Engine\x00DuelClient\x00'
        res = C.check_names(table, {('YgomGame.Duel', 'Engine')}, set())
        self.assertEqual(res['missing_classes'], [])

    def test_reports_missing(self):
        table = b'\x00Engine\x00'
        res = C.check_names(table, {('YgomGame.Duel', 'Gone')},
                            {'alsoGone'})
        self.assertEqual(res['missing_classes'], ['Gone'])
        self.assertEqual(res['missing_fields'], ['alsoGone'])

    def test_namespace_checked_too(self):
        table = b'\x00Engine\x00'
        res = C.check_names(table, {('No.Such.Ns', 'Engine')}, set())
        self.assertEqual(res['missing_namespaces'], ['No.Such.Ns'])


class TestUnityVersionParsing(unittest.TestCase):
    """UnityFS 包头：[0] 格式版本（5.x.x）**不是**引擎版本，别读错。"""

    def _parse(self, head):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'masterduel_Data'))
            with open(os.path.join(d, 'masterduel_Data', 'data.unity3d'),
                      'wb') as fh:
                fh.write(head)
            return C.read_unity_version(d)

    def test_skips_format_version(self):
        head = b'UnityFS\x00\x00\x00\x00\x085.x.x\x006000.0.61f1\x00\x00'
        self.assertEqual(self._parse(head), '6000.0.61f1')

    def test_handles_old_style_version(self):
        head = b'UnityFS\x00\x00\x00\x00\x085.x.x\x002022.3.12f1\x00\x00'
        self.assertEqual(self._parse(head), '2022.3.12f1')

    def test_missing_file_gives_none(self):
        self.assertIsNone(C.read_unity_version(os.path.join(ROOT, 'no_such')))


if __name__ == '__main__':
    unittest.main()
