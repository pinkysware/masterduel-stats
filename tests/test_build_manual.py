# -*- coding: utf-8 -*-
"""`tools/build_manual.py` 的单测 —— 手册是**独立发布产物**（第 5 个）。

锁三件事：

1. **产物名必须是 ASCII**（`bug-guide`）—— GitHub Release / 百度网盘 URL
   会再编码一次，中文名在下游容易变乱码。
2. **渲染必须真的渲染**：表格 / 代码块 / 标题都转成标签，原文不能原样漏进去
   （漏了 = 用户打开看到一堆 `|` 和 ` ``` `）。
3. **手册正文不含本机绝对路径** —— 它是要公开发布的（发版时挂到 Release）。
"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tools import build_manual                                       # noqa: E402

MANUAL = os.path.join(PROJ, 'docs', 'Bug定位手册.md')

#: 本机才有的路径 —— 发布出去就是信息泄露
LOCAL_PATHS = ('C:/Users/Administrator', 'E:/LMStudio', 'D:/ProgramData',
               '.workbuddy/binaries', '.zcode/')


def _read(path):
    with io.open(path, encoding='utf-8', newline='') as f:
        return f.read()


class ManualSourceTest(unittest.TestCase):
    """手册源文件本身得站得住（它是公开发布的）。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(MANUAL):
            raise unittest.SkipTest('手册不存在：%s' % MANUAL)
        cls.md = _read(MANUAL)

    def test_has_no_local_absolute_paths(self):
        for bad in LOCAL_PATHS:
            self.assertNotIn(bad, self.md,
                             '手册泄露了本机路径：%s' % bad)

    def test_has_all_sections(self):
        """五段结构不能缺（缺了就不是「3 分钟定位」了）。"""
        for sec in ('## §0', '## §1', '## §2', '## §3', '## §4', '## §5'):
            self.assertIn(sec, self.md, '手册缺 %s' % sec)

    def test_symptom_table_is_populated(self):
        """§1 速查表必须真的有内容（至少 20 行症状）。"""
        body = self.md.split('## §1')[1].split('## §2')[0]
        rows = [l for l in body.split('\n') if l.strip().startswith('|')]
        self.assertGreater(len(rows), 20,
                           '§1 速查表条目太少（%d 行）' % len(rows))


class RenderTest(unittest.TestCase):
    """Markdown -> HTML 的最小渲染器（不引第三方库）。"""

    def test_table(self):
        html = build_manual.render_markdown(
            '| a | b |\n|---|---|\n| 1 | 2 |\n')
        self.assertIn('<table>', html)
        self.assertIn('<th>a</th>', html)
        self.assertIn('<td>1</td>', html)
        self.assertNotIn('|---', html)

    def test_fenced_code(self):
        html = build_manual.render_markdown('```bash\necho hi\n```\n')
        self.assertIn('<pre>', html)
        self.assertIn('echo hi', html)
        self.assertNotIn('```', html)

    def test_heading_levels(self):
        html = build_manual.render_markdown('# A\n## B\n### C\n')
        self.assertIn('<h1>A</h1>', html)
        self.assertIn('<h2>B</h2>', html)
        self.assertIn('<h3>C</h3>', html)

    def test_inline_code_and_bold(self):
        html = build_manual.render_markdown('用 `core/x.py` 和 **粗体**。')
        self.assertIn('<code>core/x.py</code>', html)
        self.assertIn('<strong>粗体</strong>', html)

    def test_bold_spanning_lines_still_renders(self):
        """★ 跨行粗体（段落内换行）—— 早先按行分段渲染会漏掉，留下裸 `**`。"""
        html = build_manual.render_markdown('开头 **这一段\n跨行了** 结尾。')
        self.assertIn('<strong>', html)
        self.assertNotIn('**', html)

    def test_html_is_escaped(self):
        """尖括号必须转义（否则用户数据里的 `<` 会变成标签）。"""
        html = build_manual.render_markdown('a < b 且 x & y')
        self.assertIn('&lt;', html)
        self.assertIn('&amp;', html)

    def test_code_content_is_escaped(self):
        html = build_manual.render_markdown('`<script>`')
        self.assertIn('&lt;script&gt;', html)

    def test_quote_block(self):
        html = build_manual.render_markdown('> 一行\n> 又一行')
        self.assertIn('<blockquote>', html)

    def test_list(self):
        html = build_manual.render_markdown('- a\n- b\n')
        self.assertIn('<ul>', html)
        self.assertIn('<li>a</li>', html)


class BuildOutputTest(unittest.TestCase):
    """产物名与内容（ASCII / 有外壳 / 中文不糊）。"""

    def setUp(self):
        if not os.path.isfile(MANUAL):
            raise unittest.SkipTest('手册不存在')
        self.dir = tempfile.mkdtemp(prefix='manual_')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_output_names_are_ascii(self):
        paths = build_manual.build('9.9.9', self.dir)
        names = [os.path.basename(p) for p in paths]
        self.assertEqual(set(names),
                         {'masterduel-stats-v9.9.9-bug-guide.html',
                          'masterduel-stats-v9.9.9-bug-guide.md'})
        for nm in names:
            self.assertTrue(nm.isascii(), '产物名含非 ASCII：%s' % nm)

    def test_html_is_a_standalone_document(self):
        paths = build_manual.build('9.9.9', self.dir)
        htm = [p for p in paths if p.endswith('.html')][0]
        text = _read(htm)
        self.assertTrue(text.startswith('<!DOCTYPE html>'))
        self.assertIn('<meta charset="utf-8">', text)
        self.assertIn('<style>', text)          # 样式内联，不联网也能看
        self.assertIn('</html>', text)
        #: 中文标题要保证是 UTF-8 存下来的（不是转义实体）
        self.assertIn('Bug 定位手册', text)

    def test_md_output_matches_source(self):
        paths = build_manual.build('9.9.9', self.dir)
        mdp = [p for p in paths if p.endswith('.md')][0]
        self.assertEqual(_read(mdp), _read(MANUAL))

    def test_rendered_full_manual_has_no_raw_markup(self):
        """拿真手册整篇渲染：不能有漏渲染的 `|` 表格 / ``` / **。"""
        text = build_manual.render_html(_read(MANUAL))
        self.assertNotIn('```', text)
        self.assertNotIn('|---', text)
        self.assertNotIn('**', text)
        self.assertGreater(text.count('<table>'), 10)
        self.assertGreater(text.count('<pre>'), 5)
        #: 章节标题都在
        for sec in ('§0', '§1', '§2', '§3', '§4', '§5'):
            self.assertIn(sec, text)


class ReleaseDocTest(unittest.TestCase):
    """发布流程文档要把手册算进产物（否则下次发版会漏传）。"""

    def setUp(self):
        path = os.path.join(PROJ, 'docs', '发布流程.md')
        if not os.path.isfile(path):
            raise unittest.SkipTest('发布流程文档不存在')
        self.doc = _read(path)

    def test_product_count_is_five(self):
        self.assertIn('5 个产物', self.doc)

    def test_upload_list_includes_manual(self):
        self.assertIn('bug-guide.html', self.doc)
        self.assertIn('bug-guide.md', self.doc)

    def test_build_step_mentions_build_manual(self):
        self.assertIn('tools/build_manual.py', self.doc)



class EmitIntoTest(unittest.TestCase):
    """★ 2026-10-24 手册进包（不单独发布）：emit_into 生成不带版本号的两件。"""

    def test_emits_unversioned_pair(self):
        import tempfile, os
        from tools import build_manual
        with tempfile.TemporaryDirectory() as td:
            out = build_manual.emit_into(td)
            names = sorted(os.path.basename(x) for x in out)
            self.assertEqual(names, ['bug-guide.html', 'bug-guide.md'])
            html = io.open(out[0], encoding='utf-8').read()
            self.assertIn('<html', html)
            md = io.open(out[1], encoding='utf-8').read()
            self.assertIn('Bug', md[:200] or md)


class TestParameterized(unittest.TestCase):
    """emit_into / render_html 参数化 —— user-guide 与 bug-guide 共用管线。"""

    def test_emit_into_custom_stem_and_title(self):
        d = tempfile.mkdtemp()
        try:
            out = build_manual.emit_into(
                d, version='9.9.9', src=MANUAL,
                stem='user-guide', title='测试标题 XY')
            names = sorted(os.path.basename(x) for x in out)
            self.assertEqual(names, ['user-guide.html', 'user-guide.md'])
            html = io.open(out[0], encoding='utf-8').read()
            self.assertIn('<title>测试标题 XY v9.9.9</title>', html)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_emit_into_default_is_bug_guide(self):
        """缺省行为不变（向后兼容：老调用方 emit_into(dir) 不带参）。"""
        d = tempfile.mkdtemp()
        try:
            out = build_manual.emit_into(d, version='9.9.9')
            names = sorted(os.path.basename(x) for x in out)
            self.assertEqual(names, ['bug-guide.html', 'bug-guide.md'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_render_html_default_title_unchanged(self):
        html = build_manual.render_html('# x', '1.0.0')
        self.assertIn('Bug 定位手册 v1.0.0', html)


if __name__ == '__main__':
    unittest.main()
