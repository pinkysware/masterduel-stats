# -*- coding: utf-8 -*-
"""卡组覆盖表（`data/deck_name_cn.csv`）的读 / 写 —— 全项目**只留这一份实现**。

**7 列**（表头是权威；认不出表头才按老顺序兜底）：

    ============  ==========================================================
    站内英文名     masterduelmeta 上的原名（**连接键**）；
                  **站内没有的名字 = 自定义卡组**（我们自己建的）
    中文名         选择框显示用；留空 = 还没补
    日文名         ★ 2026-09-12 新加：日语显示 / 日语搜索用
    别名           同一卡组的**其它叫法**，搜索也能命中；多个用 ``/`` 或 ``、``
    分组           'Tier1' / 'Tier2' / 'Tier3' / 'Trending'（不带空格）
    隐藏           填 Y = 不进选择框（相当于手工删掉）
    备注           自由文本；含「待核」会被标出来
    ============  ==========================================================

历史：①前 7 列（没有「日文名」）用了很久；②曾经还有第 2 列「显示英文名」
（改名机制用）—— **2026-10-12 用户拍板删除**（全表只有 Yummy Engine 一行在用，
其值 Mitsurugi Yummy 已搬进该行别名；meta 行的 name_display 字段保留作兜底链，
读老文件仍认这列）。**所以列顺序变了也得能读** —— 只要表头还在，就按表头认列；
表头被删掉了才退回老顺序。Excel / WPS 存成 BOM 或 GBK 都认。
"""

import csv
import io
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 覆盖表的默认位置 —— **唯一来源**（`ui/app.py` 的 `CN_CSV`、看板的
#: 「管理对手卡组」对话框都取这里；`tools/fetch_meta_decks.py` 有自己的同名常量，
#: 指的是同一个文件）
CN_CSV = os.path.join(PROJ, 'data', 'deck_name_cn.csv')

#: 当前列顺序
COLUMNS = ('站内英文名', '中文名', '日文名',
           '别名', '分组', '隐藏', '备注')

#: 老版本列顺序（没有「日文名」）—— 没有表头时按这个读
LEGACY_COLUMNS = ('站内英文名', '显示英文名', '中文名',
                  '别名', '分组', '隐藏', '备注')

#: 表头里可能的其它写法 -> 标准列名
HEADER_ALIASES = {
    '英文名': '站内英文名', 'english': '站内英文名', 'name': '站内英文名',
    'display': '显示英文名', '显示名': '显示英文名', '英文显示名': '显示英文名',
    'cn': '中文名', '中文': '中文名',
    'jp': '日文名', 'ja': '日文名', '日文': '日文名',
    'alias': '别名', 'aliases': '别名',
    'tier': '分组', '组': '分组',
    'hidden': '隐藏', 'hide': '隐藏',
    'note': '备注', 'notes': '备注', '说明': '备注',
}

#: 认表头时看这几个词（第一格命中任意一个就当表头）
HEADER_HINTS = ('站内英文名', '英文名', 'english', 'name')


def read_text(path):
    """读文本，自动认 UTF-8(BOM) / GBK（Excel 在中文 Windows 上会存 GBK）。"""
    with open(path, 'rb') as f:
        raw = f.read()
    if not raw:
        return ''
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def is_header(row):
    """这一行像表头吗（只看第一格）。"""
    if not row:
        return False
    first = (row[0] or '').strip().lower()
    return any(h.lower() == first or h.lower() in first for h in HEADER_HINTS)


def normalize_header(header):
    """把表头里的写法归一成标准列名（认不出的原样留着）。"""
    out = []
    for cell in header:
        cell = (cell or '').strip()
        out.append(HEADER_ALIASES.get(cell, HEADER_ALIASES.get(cell.lower(), cell)))
    return out


def read(path):
    """读覆盖表 -> ``(header, rows)``；空行已剔除，单元格已 strip。

    没有表头时返回 ``LEGACY_COLUMNS`` 当 header（老文件靠这条继续能读）。
    """
    if not os.path.exists(path):
        return list(COLUMNS), []
    rows = list(csv.reader(io.StringIO(read_text(path))))
    rows = [r for r in rows if r and any((c or '').strip() for c in r)]
    if not rows:
        return list(COLUMNS), []
    if is_header(rows[0]):
        header, body = normalize_header(rows[0]), rows[1:]
    else:
        header, body = list(LEGACY_COLUMNS), rows
    return header, [[(c or '').strip() for c in r] for r in body]


def index_map(header):
    """列名 -> 下标。"""
    return {name: i for i, name in enumerate(header) if name}


def get(row, idx, name):
    """按列名取一格（列不在就返回空串）。"""
    i = idx.get(name)
    return row[i].strip() if i is not None and i < len(row) else ''


def set_cell(row, idx, name, value):
    """按列名写一格（列不在返回 False，不硬塞）。"""
    i = idx.get(name)
    if i is None:
        return False
    while len(row) <= i:
        row.append('')
    row[i] = value
    return True


def write(path, header, rows):
    """写回覆盖表：**BOM + LF**（跟仓库里现有文件一致，Excel 也认）。

    ★ 2026-10-18（BUG-B2A-03）：改成**原子写** —— 先落 `<path>.tmp` 再
    `os.replace`。原实现直接 `open(path, 'wb')`，会在写第一个字节前就把真源
    截断；此时崩溃 / 磁盘满 / 进程被杀 → 用户手填的覆盖表只剩 0 字节或半截
    CSV（`data/deck_name_cn.csv` 不可再生）。现在要么全成功、要么**原文件
    保持原样**。

    返回 `True` / `False`（失败时原文件不变），调用方据此提示用户。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(list(header))
    for r in rows:
        writer.writerow(list(r))
    from core import atomic
    return atomic.write_bytes(
        path, b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8'))
