# -*- coding: utf-8 -*-
"""把随仓库带的字体注册进 Qt —— **必须在建任何控件之前调**。

## 为什么字体要进仓库

`QFontDatabase.addApplicationFont()` 只认**文件路径**，系统没装就没戏。
所以 `data/fonts/` 下随仓库带一份 **Inter**（拉丁/数字），启动时注册 ——
换机器 / 换系统都不用重装字体。中日文走**系统** CJK 族（见 `theme.families()`）。

## 仓库里这份字体是什么（2026-09-15 换掉得意黑之后）

`data/fonts/Inter-Regular.ttf` / `Inter-Medium.ttf` —— **Inter**，SIL OFL 1.1
（`data/fonts/OFL-Inter.txt`），**允许随软件分发**（不得单独售卖字体本身）。

* **为什么换掉得意黑**：它是展示型字体（单字重 400 + `italicAngle = -8°` 斜体 +
  窄字面），而且**日文覆盖只有 68.96%** —— 日文卡名有一半字掉回退字体，
  同一行里两种字体混排。实测见 `docs/M7悬浮窗可缩放设计.md` §3.3。
* **为什么是 Inter**：界面里全是 `65.6%` / `21-11` 这种数字，Inter 有**真等宽数字**
  （`tnum`，我们的 `widgets.font(tabular=True)` 正好用得上），且是正体、常规字面宽。
* **来源与加工**（可复现，别改成别处下）：
  1. npm 包 `inter-ui@4.1.1`（`https://registry.npmmirror.com/inter-ui/-/inter-ui-4.1.1.tgz`）
     里的 `package/web/Inter-Regular.woff2` / `Inter-Medium.woff2`；
  2. Qt **不认 woff2**（实测 `addApplicationFont` 返回 -1）→ 用 fontTools 转成 TTF
     （`TTFont(src).save(dst)`，依赖 `brotli`）；
  3. web 构建把 Medium 单独命名成 `Inter Medium`，会让 Qt 认成**两个族** →
     把 name 表改回同一族（1/4/6/16=Inter，2/17=Medium，`OS/2.usWeightClass=500`）。
* 只带**拉丁 / 数字 / 符号**：中日文交给 `theme.FONT_CJK` 的系统族（见
  `theme.families()`）；Inter 里没有汉字，也不该有。
* 体积：两个静态 TTF 各约 318KB / 323KB。

> ⚠️ **判断字体覆盖别用 `QtGui.QRawFont.supportsCharacter()`** ——
> 对这份 CFF 字体它误报成「常用汉字只覆盖 54%」，差点被带偏。
> 要权威结论就用 `fontTools.ttLib.TTFont(...)['cmap']` 直接读码表。
"""
from __future__ import annotations

import os
from typing import List

from core import paths
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 自带字体目录
FONT_DIR = paths.data_path('fonts')

_EXTS = ('.ttf', '.otf')


def load_app_fonts(verbose: bool = False) -> List[str]:
    """注册 `data/fonts/` 下所有 `.ttf/.otf`，返回注册进去的 **family 名**。

    **不抛异常** —— 字体缺了/坏了，界面该用系统的字体继续跑，不能崩。
    """
    from PySide6.QtGui import QFontDatabase

    got: List[str] = []
    if not os.path.isdir(FONT_DIR):
        return got
    for fn in sorted(os.listdir(FONT_DIR)):
        if os.path.splitext(fn)[1].lower() not in _EXTS:
            continue
        path = os.path.join(FONT_DIR, fn)
        try:
            fid = QFontDatabase.addApplicationFont(path)
            if fid is None or fid < 0:
                if verbose:
                    print('  [字体] 注册失败：%s' % fn)
                continue
            fams = QFontDatabase.applicationFontFamilies(fid)
            got.extend(fams)
            if verbose:
                print('  [字体] %s -> %s' % (fn, fams))
        except Exception as exc:                       # pragma: no cover
            if verbose:
                print('  [字体] %s 出错：%s' % (fn, exc))
    return got


def has_app_font() -> bool:
    """自带的字体文件在不在（离线/精简部署时可能没有）。"""
    if not os.path.isdir(FONT_DIR):
        return False
    return any(os.path.splitext(f)[1].lower() in _EXTS
               for f in os.listdir(FONT_DIR))


def prepare_app(qapp=None, style_sheet=None) -> List[str]:
    """建任何控件之前的**唯一**初始化入口：注册自带字体 + 上全局 QSS。

    为什么要有这一层：`ui/app.py` 与**所有**出图 / 截图脚本必须走同一条路径，
    否则「按图迭代 UI」就是瞎子摸象 —— 2026-09-15 实测踩过：
    `tools/render_ui_demo.py` 漏了注册字体，出的图是**微软雅黑**版，
    真机是**得意黑**版，同一份 demo 连窗口高度都不同（260×337 vs 260×325）。

    返回注册到的 family 名（可能为空 —— 离线 / 精简部署时没有字体文件，
    界面该继续跑，**不抛异常**）。调用方别拿返回值做分支判断。
    """
    from PySide6.QtWidgets import QApplication
    from ui import theme

    app = qapp or QApplication.instance() or QApplication([])
    fams = load_app_fonts()
    app.setStyleSheet(theme.qss() if style_sheet is None else style_sheet)
    _warm_up_fonts()
    return fams


def _warm_up_fonts() -> None:
    """**预热字体**（2026-10-10，用户反馈「打开很慢」时定位到的）。

    Qt 第一次对某个字体族做字形度量时，要现场解析字体文件并建字形表 ——
    实测首个 `horizontalAdvance()` 要 **0.26~0.36 秒**，之后同字体的所有字号
    都是 0ms。而启动早期必然有人量字（列宽全靠 `QFontMetrics`），所以这笔钱
    只是**迟早**要付；挪到这里付，至少不和「建窗口」抢在同一处，
    也让 `_warm_up_fonts` 之后的时间线干净（后续步骤都已是热的）。

    量一个**覆盖三语 + 数字**的串即可 —— 建的是字体的字形表，不是逐字缓存。
    失败无所谓（只是没预热），所以**不抛**。
    """
    try:
        from PySide6.QtGui import QFontMetrics
        from ui import theme, widgets
        for size in (theme.F_LABEL, theme.F_BODY, theme.F_TITLE):
            fm = QFontMetrics(widgets.font(size))
            for s in ('卡组结果先后段位对手', 'DeckResultRankOpp', '总览统计'):
                fm.horizontalAdvance(s)
    except Exception:                                    # noqa: BLE001
        pass
