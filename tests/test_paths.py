# -*- coding: utf-8 -*-
"""`core/paths.py` + 安装版数据目录的单测。

盯三件事：
1. **便携版行为不变**（没设 `MD_DATA_DIR` 时，可写目录 == 随包 `data/`）——
   这是现有 2313 项测试与便携包能继续工作的前提；
2. 设了 `MD_DATA_DIR` 后，**所有可写入口**都跟着走（库 / 卡绘 / 状态文件）；
3. 首次启动的**目录准备 + 从旧便携目录迁移**（含嵌套的记录目录，
   且已经存在新库时**不许**覆盖）。
"""

import importlib
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core import paths                                          # noqa: E402


class PortableModeTest(unittest.TestCase):
    """没设环境变量 = 便携版（老行为）。"""

    def setUp(self):
        self._prev = os.environ.pop(paths.ENV_DATA_DIR, None)
        importlib.reload(paths)

    def tearDown(self):
        if self._prev is not None:
            os.environ[paths.ENV_DATA_DIR] = self._prev
        else:
            os.environ.pop(paths.ENV_DATA_DIR, None)
        importlib.reload(paths)

    def test_data_dir_is_asset_dir(self):
        self.assertTrue(paths.is_portable())
        self.assertEqual(os.path.normcase(paths.DATA_DIR),
                         os.path.normcase(paths.ASSET_DIR))

    def test_db_stays_in_program_dir(self):
        """便携版：可写路径仍在 `<程序>/data/`（老用户无感）。

        ⚠️ **不要 `importlib.reload(storage)`**（2026-10-18 踩过）：
        reload 会重新执行模块级代码 —— `_KEEP` 这个**哨兵对象**变成新实例，
        而别处（`repair_match_result` 的默认参数等）还持着旧引用，
        于是「不修改」的列被当成真值写下去 -> 13 个 storage 用例连环红。
        ⚠️ 也别在这里比 `storage.DEFAULT_DB`：那是**导入时**算的常量，
        本用例 reload 过 `paths`，两者天然不等。验 `paths` 层即可。
        """
        self.assertEqual(os.path.normcase(paths.data_path('masterduel.db')),
                         os.path.normcase(
                             os.path.join(PROJ, 'data', 'masterduel.db')))


class InstalledModeTest(unittest.TestCase):
    """设了 `MD_DATA_DIR` = 安装版（可写数据搬到别处）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='md_inst_')
        self._prev = os.environ.get(paths.ENV_DATA_DIR)
        os.environ[paths.ENV_DATA_DIR] = self.tmp
        importlib.reload(paths)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop(paths.ENV_DATA_DIR, None)
        else:
            os.environ[paths.ENV_DATA_DIR] = self._prev
        importlib.reload(paths)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_portable(self):
        self.assertFalse(paths.is_portable())
        self.assertEqual(paths.DATA_DIR, self.tmp)

    def test_all_writable_entries_move(self):
        """库 / 卡绘 / 状态文件都必须落在 DATA_DIR（程序目录只读也能跑）。

        ⚠️ 这几个常量是**模块导入时**由 `paths.data_path()` 算出来的，所以
        这里验证的是**同一个公式**（不靠 reload —— reload 会毁掉模块级哨兵，
        见 `test_db_stays_in_program_dir` 的注释）。真实安装版里进程启动时
        `MD_DATA_DIR` 已经设好，import 那一刻就会算对。
        """
        from core import storage, card_art, my_deck, rank_state
        for name, p in (('db', paths.data_path('masterduel.db')),
                        ('card_art', paths.data_path('card_art')),
                        ('card_art_cropped', paths.data_path('card_art_cropped')),
                        ('my_deck', paths.data_path('my_deck.json')),
                        ('rank_state', paths.data_path('rank_state.json'))):
            self.assertTrue(
                os.path.normcase(p).startswith(os.path.normcase(self.tmp)),
                '%s 没落到 DATA_DIR: %s' % (name, p))
        #: ⚠️ 不在这里比 `storage.DEFAULT_DB` —— 它是**导入时**算的常量，
        #: 而本用例 reload 过 `paths` 指向临时目录，两者天然不等。
        #: 「模块常量 = data_path(...)」这层关系由
        #: `test_module_constants_follow_paths`（不 reload）来守。

    def test_readonly_assets_stay_in_program_dir(self):
        """只读素材（卡表 / 图标）仍从程序目录读 —— 它们随包走。"""
        for name in ('card_names.json', 'rank_icons', 'mode_icons'):
            p = paths.asset_path(name)
            self.assertTrue(
                os.path.normcase(p).startswith(os.path.normcase(PROJ)),
                '%s 不该被搬走' % name)

    def test_resolve_prefers_data_dir_then_asset(self):
        """既有基线又会改写的文件：先看 DATA_DIR，没有回落随包。"""
        name = 'mdm_deck_cards.json'
        self.assertEqual(os.path.normcase(paths.resolve(name)),
                         os.path.normcase(paths.asset_path(name)))
        mine = paths.data_path(name)
        with open(mine, 'w', encoding='utf-8') as f:
            f.write('{}')
        self.assertEqual(os.path.normcase(paths.resolve(name)),
                         os.path.normcase(mine))

    def test_ensure_data_dirs(self):
        made = paths.ensure_data_dirs()
        self.assertTrue(made)
        for rel in ((), ('archive',), ('archive', 'duel_records')):
            self.assertTrue(os.path.isdir(paths.data_path(*rel)))
        # 幂等：再跑一次没有新建
        self.assertEqual(paths.ensure_data_dirs(), [])


class ModuleConstantsTest(unittest.TestCase):
    """「模块常量 = paths.data_path(...)」的一致性（**不 reload**，纯静态对照）。

    这条锁住的是**重构本身**：哪天有人把某个常量改回 `os.path.join(PROJ, 'data')`，
    安装版就会重新往 Program Files 里写。
    """

    def test_code_no_longer_hardcodes_proj_data(self):
        """可写数据不许再出现 `PROJ, 'data'` 直拼（都该走 paths.data_path）。"""
        import re
        offenders = []
        for rel in ('core/storage.py', 'core/card_art.py', 'core/my_deck.py',
                    'core/rank_state.py', 'ui/app.py', 'ui/first_run.py',
                    'ui/collector_ctl.py', 'tools/archive_matches.py'):
            with io.open(os.path.join(PROJ, rel), encoding='utf-8') as f:
                src = f.read()
            for m in re.finditer(r"os\.path\.join\(PROJ,\s*'data'",
                                 src):
                line = src[:m.start()].count(chr(10)) + 1
                offenders.append((rel, line))
        self.assertEqual(offenders, [],
                         '这些地方还在往程序目录写：%r' % (offenders,))


class MigrateTest(unittest.TestCase):
    """从旧便携目录迁数据（装成安装版的用户，老数据不能丢）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='md_mig_')
        self.legacy = os.path.join(self.root, 'appdata_legacy')
        self.newdata = os.path.join(self.root, 'localappdata')
        os.makedirs(os.path.join(self.legacy, 'archive', 'duel_records',
                                 'record_1'))
        with open(os.path.join(self.legacy, 'masterduel.db'), 'wb') as f:
            f.write(b'SQLITE')
        with open(os.path.join(self.legacy, 'archive', 'end_messages.jsonl'),
                  'w', encoding='utf-8') as f:
            f.write('{}\n')
        with open(os.path.join(self.legacy, 'archive', 'duel_records',
                               'record_1', 'manifest.json'), 'w',
                  encoding='utf-8') as f:
            f.write('{}')
        self._prev = os.environ.get(paths.ENV_DATA_DIR)
        os.environ[paths.ENV_DATA_DIR] = self.newdata
        importlib.reload(paths)
        import ui.first_run as fr
        importlib.reload(fr)
        self.fr = fr
        #: 把 ASSET_DIR 指到「假安装目录/data」
        self._asset = paths.ASSET_DIR
        paths.ASSET_DIR = self.legacy
        fr.paths = paths

    def tearDown(self):
        paths.ASSET_DIR = self._asset
        if self._prev is None:
            os.environ.pop(paths.ENV_DATA_DIR, None)
        else:
            os.environ[paths.ENV_DATA_DIR] = self._prev
        importlib.reload(paths)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_migrates_db_and_nested_records(self):
        lines = self.fr.prepare_data_dir()
        self.assertTrue(os.path.exists(
            os.path.join(self.newdata, 'masterduel.db')), lines)
        #: ★ 嵌套的记录目录也必须迁（曾经因「目标目录已被预建」而漏迁）
        self.assertTrue(os.path.exists(os.path.join(
            self.newdata, 'archive', 'end_messages.jsonl')), lines)
        self.assertTrue(os.path.exists(os.path.join(
            self.newdata, 'archive', 'duel_records', 'record_1',
            'manifest.json')), lines)

    def test_does_not_overwrite_existing_db(self):
        """新位置已经有库 = 本机已在用，不许拿旧数据盖掉。"""
        os.makedirs(self.newdata, exist_ok=True)
        with open(os.path.join(self.newdata, 'masterduel.db'), 'wb') as f:
            f.write(b'NEWER')
        self.fr.prepare_data_dir()
        with open(os.path.join(self.newdata, 'masterduel.db'), 'rb') as f:
            self.assertEqual(f.read(), b'NEWER')

    def test_legacy_dir_kept(self):
        """源目录**保留不删**（让用户自己确认后再清）。"""
        self.fr.prepare_data_dir()
        self.assertTrue(os.path.exists(
            os.path.join(self.legacy, 'masterduel.db')))


if __name__ == '__main__':
    unittest.main()

class PrefFileTest(unittest.TestCase):
    """设置页写的 `paths.ini`（数据目录偏好）—— 用户 2026-10-18 要能改位置。

    🔴 必须把配置文件指到**临时目录**：`paths.ini` 写在 PROJ 根，直接测会把
    偏好写进仓库（2026-10-18 我漏了这条，残留一份 paths.ini 让 8 个用例连锁红 ——
    `DATA_DIR` 被指到已删的临时目录）。
    ⚠️ 也不要 `importlib.reload(paths)`：那会把 `_config_path` 打回 PROJ 根，
    读到的又是用户真偏好。这里直接调 :func:`paths._default_data_dir`。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='md_pref_')
        self._cfg = paths._config_path
        self._prev = os.environ.get(paths.ENV_DATA_DIR)
        os.environ.pop(paths.ENV_DATA_DIR, None)
        paths._config_path = lambda: os.path.join(self.root, 'paths.ini')

    def tearDown(self):
        paths._config_path = self._cfg
        if self._prev is None:
            os.environ.pop(paths.ENV_DATA_DIR, None)
        else:
            os.environ[paths.ENV_DATA_DIR] = self._prev
        shutil.rmtree(self.root, ignore_errors=True)
        self.assertFalse(os.path.exists(os.path.join(PROJ, 'paths.ini')),
                         '测试把 paths.ini 写进仓库了！')

    def test_write_then_read(self):
        target = os.path.join(self.root, 'MyDuelData')
        self.assertTrue(paths.write_pref(target))
        self.assertEqual(paths.read_pref(), target)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'paths.ini')))

    def test_clear_removes_file(self):
        self.assertTrue(paths.write_pref(os.path.join(self.root, 'x')))
        self.assertTrue(paths.write_pref(''))
        self.assertEqual(paths.read_pref(), '')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'paths.ini')))

    def test_default_uses_pref_when_no_env(self):
        target = os.path.join(self.root, 'chosen')
        self.assertTrue(paths.write_pref(target))
        self.assertEqual(paths._default_data_dir(), target)

    def test_env_overrides_pref(self):
        """环境变量是「手动覆盖」，仍优先于 pref（留给高级用户/排错）。"""
        paths.write_pref(os.path.join(self.root, 'chosen'))
        os.environ[paths.ENV_DATA_DIR] = os.path.join(self.root, 'env')
        self.assertEqual(paths._default_data_dir(), target_ := os.path.join(
            self.root, 'chosen'))      # _default_data_dir 只认 pref
        self.assertEqual(paths._env_data_dir(), os.path.join(self.root, 'env'))

    def test_falls_back_to_program_dir_when_writable(self):
        """没有偏好 + 程序目录可写 -> 用程序目录（用户口径）。"""
        self.assertEqual(paths._default_data_dir(), paths.ASSET_DIR)

    def test_writable_is_side_effect_free(self):
        """★ 探测不许创建目录（Program Files 判只读时会留垃圾）。"""
        probe = os.path.join(self.root, 'deep', 'nested')
        self.assertTrue(paths._writable(probe))       # 父目录可建
        self.assertFalse(os.path.exists(probe))       # 但没被建出来

    def test_writable_false_for_missing_drive(self):
        self.assertFalse(paths._writable('Q:/definitely_not_here/data'))

