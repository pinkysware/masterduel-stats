# -*- coding: utf-8 -*-
"""锁住补丁的升级语义（用户 2026-10-24 定）。

1. 补丁是**全量代码快照**，所以「从 1.1.2 起的任意旧版本都能直接升到最新」
   —— 不是只能从上一版升。
2. 打包前**必须**跑升级自检，且失败要拦下（不能只 print）。
3. 比 1.1.2 更早的版本（1.1.0 / 1.1.1）不用管 —— 没公开发布过。
"""
import io
import os
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJ, 'build')
PATCH = os.path.join(BUILD, 'build_patch.py')
CHECKER = os.path.join(PROJ, 'tools', 'check_patch_upgrade.py')

_HAS_BUILD = os.path.isdir(BUILD)


def _read(path):
    with io.open(path, encoding='utf-8', newline='') as f:
        return f.read()


@unittest.skipUnless(_HAS_BUILD, '发布仓不带 build/')
class PatchUpgradeSemanticsTest(unittest.TestCase):
    """补丁 = 全量快照 → 任意旧版都要能直接升到最新。"""

    def setUp(self):
        if not os.path.isfile(CHECKER):
            raise unittest.SkipTest('自检脚本不存在')
        self.src = _read(CHECKER)

    def test_first_public_version_is_1_1_2(self):
        """最早要支持的版本是 1.1.2（第一个公开发布版）。"""
        self.assertIn('FIRST_PUBLIC = (1, 1, 2)', self.src,
                      '基线被改了 —— 改可以，但要同步 docs/发布流程.md 并让用户知情')

    def test_checks_every_version_not_just_previous(self):
        """★ 要验**每一个**基线版本，不是只验上一版。

        用户原话：「假设以后出了 1.1.8，补丁需要是 1.1.2~1.1.7 任意版本
        都能升级到 1.1.8」。
        """
        #: 有多版本枚举 + 逐个 check_one，而不是写死单个 BASE_TAG
        self.assertIn('def baseline_versions(', self.src,
                      '没有「枚举多个基线版本」的逻辑')
        self.assertIn('def check_one(', self.src,
                      '没有「逐个版本校验」的逻辑')
        #: 旧的单版本常量不该还在
        self.assertNotIn("BASE_TAG = 'v1.1.2'", self.src,
                         '还在用单版本 BASE_TAG —— 应该改成范围 1.1.2..上一版')

    def test_excludes_unreleased_versions(self):
        """1.1.0 / 1.1.1 没公开发布过，不该出现在验证范围里。"""
        self.assertIn('v >= FIRST_PUBLIC', self.src)

    def test_patch_build_runs_upgrade_check(self):
        """打包前必须自检；没过就拦下（不能只是打印一下）。"""
        src = _read(PATCH)
        self.assertIn('check_patch_upgrade.py', src,
                      'build_patch.py 没有调用升级自检')
        self.assertIn('_check_upgrade()', src, '自检没被真正调用')
        self.assertIn("raise SystemExit('升级自检没过", src,
                      '自检失败必须拦下打包，不能只提示')

    def test_upgrade_check_skip_is_opt_in(self):
        """跳过自检必须**显式**加参数，默认不能跳过。"""
        src = _read(PATCH)
        self.assertIn("'--skip-upgrade-check', action='store_true'", src)
