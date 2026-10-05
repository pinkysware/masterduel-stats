# -*- coding: utf-8 -*-
"""tools/doc_patch.py 的单测 —— 守住「幂等追加/替换」这条纪律。

核心用例：**marker 写错必须抛错，而不是静默写两份**（2026-09-13/14 两晚各踩一次）。
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tools import doc_patch                                          # noqa: E402

BLOCK = ('> 📐 **M7 视觉规格已定（参考图驱动，2026-09-14 凌晨）**\n'
         '> 学习分工：图1 → 卡组选择器\n')


class DocPatchTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='docpatch_')
        self.path = os.path.join(self.dir, 'doc.md')
        self._write('# 标题\n\n正文。\n')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text):
        with io.open(self.path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)

    def _read(self):
        with io.open(self.path, 'r', encoding='utf-8', newline='') as f:
            return f.read()


class TestAppendOnce(DocPatchTestBase):

    def test_append_then_skip(self):
        """跑两次只写一份 —— 这是本模块存在的理由。"""
        self.assertEqual(doc_patch.append_once(self.path, BLOCK), 'appended')
        self.assertEqual(doc_patch.append_once(self.path, BLOCK), 'skip')
        self.assertEqual(self._read().count('M7 视觉规格已定'), 1)

    def test_derived_marker_can_never_miss(self):
        """省略 marker 时从文本自身推导 —— 所以判重不可能失效。"""
        doc_patch.append_once(self.path, BLOCK)
        doc_patch.append_once(self.path, BLOCK)
        self.assertEqual(doc_patch.count(self.path, '视觉规格'), 1)

    def test_wrong_marker_raises_instead_of_duplicating(self):
        """★ 回归：marker 不在正文里必须抛错（这正是两次事故的根因）。"""
        with self.assertRaises(ValueError):
            doc_patch.append_once(self.path, BLOCK,
                                  marker='M7 视觉规格已定（参考图驱动）')
        self.assertEqual(self._read().count('视觉规格'), 0)

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            doc_patch.append_once(self.path, '\n\n')

    def test_append_to_missing_file_creates_it(self):
        p = os.path.join(self.dir, 'new.md')
        doc_patch.append_once(p, BLOCK)
        self.assertEqual(doc_patch.count(p, '视觉规格'), 1)

    def test_existing_file_without_trailing_newline(self):
        self._write('# 标题')                       # 无尾换行
        doc_patch.append_once(self.path, BLOCK)
        self.assertTrue(self._read().startswith('# 标题\n> 📐'))

    def test_no_crlf_written(self):
        """项目铁律：写文件必须 newline='' —— 不能出现 \\r\\n。"""
        doc_patch.append_once(self.path, BLOCK)
        with open(self.path, 'rb') as f:
            self.assertNotIn(b'\r\n', f.read())


class TestReplaceOnce(DocPatchTestBase):

    def test_replace_then_skip(self):
        old = '正文。\n'
        new = '正文（改过）。\n'
        self.assertEqual(doc_patch.replace_once(self.path, old, new), 'replaced')
        self.assertEqual(doc_patch.replace_once(self.path, old, new), 'skip')
        self.assertEqual(self._read().count('正文（改过）'), 1)

    def test_new_starting_with_old_still_skips(self):
        """new 以 old 开头（常见写法）时，判重靠 marker 而不是 old。"""
        old = '正文。\n'
        new = old + '补充一句。\n'
        doc_patch.replace_once(self.path, old, new)
        doc_patch.replace_once(self.path, old, new)
        self.assertEqual(self._read().count('补充一句'), 1)

    def test_missing_anchor_raises(self):
        with self.assertRaises(ValueError):
            doc_patch.replace_once(self.path, '不存在的锚点\n', 'x\n')

    def test_marker_elsewhere_must_not_block_replace(self):
        """★ 回归：marker 在文件**别处**已存在时，仍必须真的替换。

        2026-09-14 连踩三次 ——「获取新卡组名」先在 §二 插入，同一片段成了
        §4.5 那次替换的 marker，导致 §4.5 被误判 skip、实际没写进去。
        """
        # 真实场景：§二 目标里**已经**插过「**「获取新卡组名」按钮**（S3.5）」，
        # 于是同一串片段成了 §4.5 那次替换的 marker —— 旧判据（看 marker）会误 skip。
        marker = '**「获取新卡组名」按钮**（S3.5）'
        self._write('# 标题\n\nA 段：' + marker + '\n\nB 段：正文。\n')
        new = ('B 段：正文（改）。\n* ★ ' + marker + '：子进程跑脚本\n')
        # 前置条件：marker 在文件里、new 不在
        self.assertIn(marker, self._read())
        self.assertNotIn(new, self._read())

        self.assertEqual(doc_patch.replace_once(self.path, 'B 段：正文。\n', new,
                                                marker=marker), 'replaced')
        self.assertIn(new, self._read())

        # 再跑一次要 skip（判据是 new 本身）
        self.assertEqual(doc_patch.replace_once(self.path, 'B 段：正文。\n', new,
                                                marker=marker), 'skip')


class TestDedup(DocPatchTestBase):

    def test_collapse_two_copies(self):
        self._write('# 标题\n' + BLOCK + BLOCK + '尾巴。\n')
        self.assertEqual(doc_patch.dedup(self.path, '> 📐 **M7 视觉规格'), 1)
        self.assertEqual(self._read().count('视觉规格'), 1)
        self.assertIn('尾巴。', self._read())

    def test_dedup_is_idempotent(self):
        self._write('# 标题\n' + BLOCK + BLOCK + '尾巴。\n')
        doc_patch.dedup(self.path, '> 📐 **M7 视觉规格')
        self.assertEqual(doc_patch.dedup(self.path, '> 📐 **M7 视觉规格'), 0)

    def test_collapse_three_copies(self):
        self._write(BLOCK * 3)
        self.assertEqual(doc_patch.dedup(self.path, '> 📐 **M7 视觉规格'), 2)
        self.assertEqual(self._read().count('视觉规格'), 1)


class TestCli(DocPatchTestBase):

    def test_cli_append_count_dedup(self):
        src = os.path.join(self.dir, 'block.md')
        self._write(BLOCK)                                     # 借 _write 造块文件
        shutil.copy2(self.path, src)
        self._write('\n')                                      # 目标清空
        self.assertEqual(doc_patch.main(['--file', self.path,
                                         '--append', src]), 0)
        self.assertEqual(doc_patch.count(self.path, '视觉规格'), 1)

        # --count
        self.assertEqual(doc_patch.main(['--file', self.path,
                                         '--count', '视觉规格']), 0)

        # --dedup（先人为造重复）
        self._write(BLOCK * 2)
        self.assertEqual(doc_patch.main(['--file', self.path,
                                         '--dedup', '视觉规格']), 0)
        self.assertEqual(doc_patch.count(self.path, '视觉规格'), 1)

    def test_cli_no_action_returns_2(self):
        self.assertEqual(doc_patch.main(['--file', self.path]), 2)


if __name__ == '__main__':
    unittest.main()
