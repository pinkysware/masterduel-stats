# -*- coding: utf-8 -*-
"""把 `docs/Bug定位手册.md` 打成**独立发布产物**（与 full/lite/patch/Setup 并列）。

## 为什么要有这个

用户要求（2026-10-24）：**手册和软件本体一起发布**，但**不并入包内**，
而是作为**独立文件**跟 full / lite / patch / Setup 一起挂到 GitHub Release
（以及百度网盘）。

所以这里产出一个自足的 HTML（单文件、无外部依赖）+ 一份原始 Markdown：

```
masterduel-stats-v1.1.2-bug-guide.html   ← 双击即看，样式内联，不联网
masterduel-stats-v1.1.2-bug-guide.md     ← 原文（给 agent / 想要纯文本的人）
```

## 为什么是 HTML 而不是 PDF

* **零依赖**：不引 pandoc / weasyprint / LaTeX（本机都没装，装了也是几百 MB 的坑）
* **中文不会糊**：浏览器自己挑系统字体，不会出现「缺字变方块」
* **可搜索可复制**：用户要复制命令时 PDF 反而难用
* 生成器只用标准库，`pip` 里什么都不用加

## 用法

```bash
python tools/build_manual.py                    # 版本读 core/version.py
python tools/build_manual.py --version 1.1.2    # 显式指定
python tools/build_manual.py --out-dir <dir>    # 换输出目录（默认 playground/）
```

产物落在 `--out-dir`（默认 `E:/LMStudio/Claude/playground/`，与四个包同目录）。
"""
from __future__ import annotations

import argparse
import html
import io
import os
import re
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

MANUAL_REL = 'docs/Bug定位手册.md'
DEFAULT_OUT = os.path.dirname(PROJ)

#: 产物名后缀。**用 ASCII**（`bug-guide`）—— 四个包的命名全是 ASCII，
#: 而 GitHub Release 上传、百度网盘 `file_upload_by_url` 都会把中文名再编码一次，
#: 实测混进中文容易在下游出现乱码名（不如一开始就别用）。
SUFFIX = 'bug-guide'


def read_version() -> str:
    from core.version import __version__
    return __version__


# --------------------------------------------------------------- Markdown
#: 最小 Markdown 渲染：**只支持手册实际用到的语法**（标题 / 表格 / 代码块 /
#: 列表 / 引用 / 行内 code+bold+link）。不引 markdown 库 —— 多一个依赖不值。
_RE_H = re.compile(r'^(#{1,6})\s+(.*)$')
_RE_FENCE = re.compile(r'^```(\w*)\s*$')
_RE_TABLE_SEP = re.compile(r'^\|[\s:|-]+\|$')
_RE_UL = re.compile(r'^(\s*)[-*]\s+(.*)$')
_RE_OL = re.compile(r'^(\s*)(\d+)\.\s+(.*)$')
_RE_QUOTE = re.compile(r'^>\s?(.*)$')
_RE_HR = re.compile(r'^\s*(---+|\*\*\*+)\s*$')


def _inline(text: str, br: bool = False) -> str:
    """行内：先转义，再处理 `code` / **bold** / [text](url)。

    ``br=True`` 时把换行变成 ``<br>``（段落 / 引用块用）。**必须在 code 占位之后
    再换** —— 否则代码块里的换行会被插进 ``<br>``（手册里没有跨行 code，但别留隐患）。
    """
    out = html.escape(text, quote=False)
    #: ⚠️ 顺序有讲究：code 先占位（否则 bold 会跑进 code 里）
    spans = []

    def stash(m):
        spans.append('<code>%s</code>' % m.group(1))
        return '\x00%d\x00' % (len(spans) - 1)

    out = re.sub(r'`([^`]+)`', stash, out)
    out = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', out)
    out = re.sub(r'\x00(\d+)\x00', lambda m: spans[int(m.group(1))], out)
    if br:
        out = out.replace('\n', '<br>')
    return out


def _split_row(line: str) -> list:
    """`| a | b |` -> ['a', 'b']（不处理转义竖线 —— 手册里没有）。"""
    body = line.strip()
    if body.startswith('|'):
        body = body[1:]
    if body.endswith('|'):
        body = body[:-1]
    return [c.strip() for c in body.split('|')]


def render_markdown(md: str) -> str:
    """Markdown -> HTML body 片段（不含 <html> 外壳）。"""
    lines = md.split('\n')
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # 代码块
        m = _RE_FENCE.match(line)
        if m:
            lang = m.group(1)
            i += 1
            buf = []
            while i < n and not _RE_FENCE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1                                    # 跳过收尾 ```
            cls = ' class="lang-%s"' % lang if lang else ''
            out.append('<pre><code%s>%s</code></pre>'
                       % (cls, html.escape('\n'.join(buf))))
            continue

        # 表格：本行以 | 开头且下一行是分隔行
        if (line.lstrip().startswith('|') and i + 1 < n
                and _RE_TABLE_SEP.match(lines[i + 1].strip())):
            head = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].lstrip().startswith('|'):
                rows.append(_split_row(lines[i]))
                i += 1
            out.append('<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
                       % (''.join('<th>%s</th>' % _inline(c) for c in head),
                          ''.join('<tr>%s</tr>' % ''.join(
                              '<td>%s</td>' % _inline(c) for c in r)
                              for r in rows)))
            continue

        # 标题
        m = _RE_H.match(line)
        if m:
            lvl = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (lvl, _inline(m.group(2)), lvl))
            i += 1
            continue

        if _RE_HR.match(line):
            out.append('<hr>')
            i += 1
            continue

        # 引用块（连续多行合并）
        if _RE_QUOTE.match(line):
            buf = []
            while i < n and _RE_QUOTE.match(lines[i]):
                buf.append(_RE_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append('<blockquote>%s</blockquote>'
                       % _inline('\n'.join(buf), br=True))
            continue

        # 列表（连续同类型合并）
        if _RE_UL.match(line) or _RE_OL.match(line):
            ordered = bool(_RE_OL.match(line))
            pattern = _RE_OL if ordered else _RE_UL
            items = []
            while i < n:
                mm = pattern.match(lines[i])
                if not mm:
                    #: 缩进的续行算上一项（手册里有用到）
                    if items and lines[i].startswith('  ') and lines[i].strip():
                        items[-1] += ' ' + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(mm.group(mm.lastindex))
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>%s</%s>' % (tag, ''.join(
                '<li>%s</li>' % _inline(x) for x in items), tag))
            continue

        if not line.strip():
            i += 1
            continue

        # 段落
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not (
                _RE_H.match(lines[i]) or _RE_FENCE.match(lines[i])
                or _RE_QUOTE.match(lines[i]) or _RE_UL.match(lines[i])
                or _RE_OL.match(lines[i]) or _RE_HR.match(lines[i])
                or lines[i].lstrip().startswith('|')):
            buf.append(lines[i])
            i += 1
        out.append('<p>%s</p>' % _inline('\n'.join(buf), br=True))
    return '\n'.join(out)


_CSS = """\
:root { color-scheme: light dark; }
body { margin: 0 auto; padding: 28px 32px 80px; max-width: 1080px;
       font: 15px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei",
             "Hiragino Sans", "Noto Sans CJK SC", sans-serif;
       color: #1c1c1e; background: #fff; }
h1 { font-size: 30px; margin: 8px 0 20px; padding-bottom: 12px;
     border-bottom: 3px solid #d94f2b; }
h2 { font-size: 23px; margin: 40px 0 14px; padding-left: 11px;
     border-left: 4px solid #d94f2b; }
h3 { font-size: 18px; margin: 28px 0 10px; color: #333; }
h4 { font-size: 16px; margin: 22px 0 8px; }
p { margin: 10px 0; }
a { color: #0b5cad; text-decoration: none; }
a:hover { text-decoration: underline; }
code { font: 13px/1.5 "Cascadia Mono", Consolas, "Courier New", monospace;
       background: #f2f3f5; padding: 2px 5px; border-radius: 4px;
       word-break: break-word; }
pre { background: #f8f9fb; border: 1px solid #e3e6ea; border-radius: 7px;
      padding: 13px 15px; overflow-x: auto; margin: 14px 0; }
pre code { background: none; padding: 0; font-size: 13px; line-height: 1.6;
           white-space: pre; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
th, td { border: 1px solid #dfe2e6; padding: 8px 10px; text-align: left;
         vertical-align: top; }
th { background: #f4f6f8; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafbfc; }
blockquote { margin: 14px 0; padding: 10px 16px; background: #fff8f0;
             border-left: 4px solid #e8a33d; color: #4a4030; }
blockquote p { margin: 0; }
hr { border: 0; border-top: 1px solid #e3e6ea; margin: 30px 0; }
ul, ol { margin: 10px 0; padding-left: 26px; }
li { margin: 5px 0; }
strong { font-weight: 600; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e6e6; background: #1b1d20; }
  code { background: #2a2d31; }
  pre { background: #232629; border-color: #34383d; }
  th { background: #26292d; }
  tbody tr:nth-child(even) { background: #202326; }
  th, td { border-color: #34383d; }
  blockquote { background: #2b2721; color: #e0d5c2; }
  a { color: #6cb6ff; }
  h3 { color: #cfd3d8; }
}
@media print { body { max-width: none; font-size: 12px; }
  h2 { page-break-after: avoid; } pre, table { page-break-inside: avoid; } }
"""


def render_html(md: str, version: str = '') -> str:
    body = render_markdown(md)
    title = 'Master Duel Stats — Bug 定位手册'
    if version:
        title += ' v%s' % version
    return (
        '<!DOCTYPE html>\n'
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>%s</title>\n'
        '<style>\n%s</style>\n'
        '</head>\n<body>\n%s\n</body>\n</html>\n'
        % (html.escape(title), _CSS, body))


# -------------------------------------------------------------------- main
def build(version: str = None, out_dir: str = None) -> list:
    """生成产物，返回写出的文件路径列表。**不抛网络/环境异常**。"""
    ver = version or read_version()
    out = out_dir or DEFAULT_OUT
    src = os.path.join(PROJ, MANUAL_REL)
    with io.open(src, encoding='utf-8', newline='') as f:
        md = f.read()

    os.makedirs(out, exist_ok=True)
    stem = 'masterduel-stats-v%s-%s' % (ver, SUFFIX)
    html_path = os.path.join(out, stem + '.html')
    md_path = os.path.join(out, stem + '.md')

    with io.open(html_path, 'w', encoding='utf-8', newline='') as f:
        f.write(render_html(md, ver))
    with io.open(md_path, 'w', encoding='utf-8', newline='') as f:
        f.write(md)
    return [html_path, md_path]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='把 Bug 定位手册打成独立发布产物（HTML + MD）')
    ap.add_argument('--version', default=None,
                    help='版本号（默认读 core/version.py 的 __version__）')
    ap.add_argument('--out-dir', default=None,
                    help='输出目录（默认 = 仓库上一级，与四个包同目录）')
    args = ap.parse_args(argv)
    paths = build(args.version, args.out_dir)
    for p in paths:
        print('  %s  (%.1f KB)' % (p, os.path.getsize(p) / 1024.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
