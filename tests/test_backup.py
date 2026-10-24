# -*- coding: utf-8 -*-
"""`core.backup`（全量备份 / 两步恢复）单测 —— 全程临时目录，不碰真数据。

背景（2026-10-24 用户需求）：卸载保留数据后重装「保留的数据不完全」——
程序目录里按素材管理的用户可改件（覆盖表 / 自定义卡组图 / 自定义参照库）
不在卸载保护范围，换目录重装数据位置还会变。本模块一个 zip 带走全部。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import backup, paths                                    # noqa: E402


def _mk(path: str, content: bytes = b'x') -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)
    return path


class _FakeDirs(unittest.TestCase):
    """把 DATA_DIR / ASSET_DIR / 配置文件指到临时目录。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        td = self._td.name
        self.data_dir = os.path.join(td, 'data')
        self.asset_dir = os.path.join(td, 'app')
        for d in (self.data_dir, self.asset_dir):
            os.makedirs(d, exist_ok=True)
        patcher = [
            mock.patch.object(paths, 'DATA_DIR', self.data_dir),
            mock.patch.object(paths, 'ASSET_DIR', self.asset_dir),
            mock.patch.object(paths, '_config_path',
                              lambda: os.path.join(td, 'paths.ini')),
        ]
        for p in patcher:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)


class BackupItemsTest(_FakeDirs):
    """清单口径：DATA_DIR 全部 + ASSET_DIR 三样用户可改件 + paths.ini。"""

    def test_collects_all_three_groups(self):
        _mk(os.path.join(self.data_dir, 'masterduel.db'), b'db')
        _mk(os.path.join(self.data_dir, 'archive', 'duel_records', 'a.json'))
        _mk(os.path.join(self.asset_dir, 'deck_name_cn.csv'), b'csv')
        _mk(os.path.join(self.asset_dir, 'deck_images_custom', 'a.webp'))
        _mk(os.path.join(self.asset_dir, 'mdm_deck_cards_custom.json'), b'{}')
        _mk(os.path.join(self._td.name, 'paths.ini'), b'pref')
        items = dict((rel, src) for src, rel in backup.backup_items())
        self.assertIn('data/masterduel.db', items)
        self.assertIn('data/archive/duel_records/a.json', items)
        self.assertIn('appdata/deck_name_cn.csv', items)
        self.assertIn('appdata/deck_images_custom/a.webp', items)
        self.assertIn('appdata/mdm_deck_cards_custom.json', items)
        self.assertIn('paths.ini', items)

    def test_excludes_staging_pending_pid(self):
        _mk(os.path.join(self.data_dir, 'masterduel.db'))
        _mk(os.path.join(self.data_dir, backup.STAGING_NAME, 'x'))
        _mk(os.path.join(self.data_dir, backup.PENDING_NAME), b'{}')
        _mk(os.path.join(self.data_dir, 'archive', 'duel_collector.pid'), b'1')
        rels = [rel for _src, rel in backup.backup_items()]
        self.assertEqual([r for r in rels if 'staging' in r
                          or 'pending' in r or '.pid' in r], [])


class MakeBackupTest(_FakeDirs):
    """打包：zip 内容 / 戳 / 字节数。"""

    def test_zip_contents_and_stamp(self):
        _mk(os.path.join(self.data_dir, 'masterduel.db'), b'database!')
        _mk(os.path.join(self.asset_dir, 'deck_name_cn.csv'), b'cn,csv')
        dest = os.path.join(self._td.name, 'bk.zip')
        r = backup.make_backup(dest, note='unit')
        self.assertEqual(r['files'], 2)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertIn('backup.json', names)
            self.assertIn('data/masterduel.db', names)
            self.assertIn('appdata/deck_name_cn.csv', names)
            stamp = json.loads(z.read('backup.json').decode('utf-8'))
        self.assertEqual(stamp['note'], 'unit')
        self.assertEqual(stamp['format'], 1)
        self.assertIn('created_at', stamp)


class RestoreTwoStepTest(_FakeDirs):
    """两步恢复：stage 只写暂存不动现有 → apply 合并覆盖、不删多余。"""

    def _make_world(self):
        _mk(os.path.join(self.data_dir, 'masterduel.db'), b'OLD')
        _mk(os.path.join(self.data_dir, 'my.json'), b'OLD')
        _mk(os.path.join(self.data_dir, 'archive', 'a.log'), b'OLD')
        _mk(os.path.join(self.asset_dir, 'deck_name_cn.csv'), b'OLD')
        dest = os.path.join(self._td.name, 'bk.zip')
        backup.make_backup(dest)
        return dest

    def test_stage_touches_nothing_then_apply_restores(self):
        dest = self._make_world()
        # 备份后把现实改坏（模拟卸载重装丢数据 / 改了东西）
        _mk(os.path.join(self.data_dir, 'masterduel.db'), b'NEW-BAD')
        os.remove(os.path.join(self.data_dir, 'my.json'))
        # --- 第一步：stage（只写暂存区 + pending）---
        pend = backup.stage_restore(dest)
        self.assertEqual(pend['format'], 1)
        self.assertTrue(backup.pending_exists())
        self.assertTrue(os.path.isdir(backup.staging_path()))
        with open(os.path.join(self.data_dir, 'masterduel.db'), 'rb') as f:
            self.assertEqual(f.read(), b'NEW-BAD')      # 现有数据没被碰
        # --- 第二步：apply（下次启动）---
        r = backup.apply_pending()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['data_files'], 3)
        with open(os.path.join(self.data_dir, 'masterduel.db'), 'rb') as f:
            self.assertEqual(f.read(), b'OLD')          # 被备份盖回
        self.assertTrue(os.path.isfile(os.path.join(self.data_dir, 'my.json')))
        with open(os.path.join(self.asset_dir, 'deck_name_cn.csv'), 'rb') as f:
            self.assertEqual(f.read(), b'OLD')
        # 收尾干净
        self.assertFalse(backup.pending_exists())
        self.assertFalse(os.path.isdir(backup.staging_path()))
        # 没有 pending 时 apply 是零开销 None
        self.assertIsNone(backup.apply_pending())

    def test_apply_keeps_files_not_in_backup(self):
        """合并口径：备份里没有的**新增文件**保留不动。"""
        dest = self._make_world()
        extra = _mk(os.path.join(self.data_dir, 'new-after-backup.json'))
        backup.stage_restore(dest)
        r = backup.apply_pending()
        self.assertTrue(r['ok'])
        self.assertTrue(os.path.isfile(extra))

    def test_apply_restores_paths_ini(self):
        io.open(os.path.join(self._td.name, 'paths.ini'), 'w',
                encoding='utf-8', newline='').write('D:\\somewhere\n')
        dest = self._make_world()
        os.remove(os.path.join(self._td.name, 'paths.ini'))
        backup.stage_restore(dest)
        backup.apply_pending()
        with open(paths._config_path(), encoding='utf-8') as f:
            self.assertEqual(f.read().strip(), 'D:\\somewhere')

    def test_apply_failure_keeps_pending(self):
        """应用失败（OSError）→ pending 保留，下次启动再试。"""
        dest = self._make_world()
        backup.stage_restore(dest)
        real_copy = backup._merge_tree

        def boom(src, dst):
            raise OSError('locked')
            # pragma: no cover
        with mock.patch.object(backup, '_merge_tree', boom):
            r = backup.apply_pending()
        self.assertFalse(r['ok'])
        self.assertIn('locked', r['error'])
        self.assertTrue(backup.pending_exists())        # 留着下次再试



class ExportPageBackupUiTest(unittest.TestCase):
    """导出页有「全量备份」区（裸构造，offscreen）。"""

    def test_page_has_backup_buttons(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from ui.export_page import ExportPage
        page = ExportPage()
        self.assertTrue(page.btn_backup.text())
        self.assertTrue(page.btn_restore.text())
        self.assertTrue(callable(page._do_backup))
        self.assertTrue(callable(page._do_restore))
        #: 🔴 2026-10-24 踩过：诊断包按钮创建了却没 addWidget（孤儿控件，
        #:   永不显示）—— 存在性断言拦不住，必须查**布局归属**。
        for b in (page.btn_backup, page.btn_restore, page.btn_diag):
            self.assertIsNotNone(b.parentWidget(), '按钮没进布局: %r' % b)
            self.assertTrue(b.isVisibleTo(page))


if __name__ == '__main__':
    unittest.main()
