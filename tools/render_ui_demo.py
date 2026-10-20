# -*- coding: utf-8 -*-
"""渲染 demo 战绩的悬浮窗 PNG（做 UI 的**标准预览出口**，2026-09-15 建）。

数据一律来自 :mod:`core.ui_demo` —— 别再在 tmp/ 里散落临时合成数据
（2026-09-15 踩过：脚本忘了给 ``badge_numeral``，渲染图「段位显示不全」
排查半天其实是数据问题）。

    $PY tools/render_ui_demo.py
      -> tmp/_demo_float.png          默认态（露 3 行 + 滚动条）
      -> tmp/_demo_float_10.png       拉高：一次看到 9 行（滚动条消失）
      -> tmp/_demo_float_wide.png     拉宽：对手卡组名少省略
      -> tmp/_demo_float_folded.png   「最近对局」折叠
      -> tmp/_demo_empty.png          空态
      -> tmp/_demo_panel.png          悬浮窗 + 贴右侧的**选择器面板**（M5 候选置顶）

⚠️ 渲染**一律 1 倍比例**（用户：45% 才是正常显示比例），别用 offscreen
（字体是「每字符 1em」的桩，图会走形，见交接包 §7.1）。
⚠️ **与 DPR 无关**：`grab()` 出来的位图是「逻辑尺寸 × DPR」物理像素
（本机 DPR=1.5 时是 393×491 / 逻辑 262×327），所以下面**按 `widget.size()`
归一一次** —— 任何 DPR 都能得到 1 倍图（2026-09-15 像素级核对发现）。
换机器时不要以为「图变了就是失真」—— 正常情况下逻辑尺寸相同则产物一致。
"""
from __future__ import annotations

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from PySide6.QtCore import QSize, Qt                             # noqa: E402
from PySide6.QtGui import QImage, QPainter                       # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from core.ui_demo import demo_snapshot                           # noqa: E402
from core.ui_state import UiSnapshot                             # noqa: E402
from ui import fonts, theme                                      # noqa: E402
from ui import floating                                          # noqa: E402
from ui.floating import FloatingWindow                           # noqa: E402


def _grab(widget):
    """抓一个控件的 **1 倍**位图（本机 DPR=1.5，不缩回来图就是 1.5 倍）。"""
    pix = widget.grab()
    if pix.size() != widget.size():
        pix = pix.scaled(widget.size(), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    pix.setDevicePixelRatio(1.0)
    return pix


def shot(qapp, snap, name: str, *, rows=None, extra_w: int = 0,
         extra_h: int = 0, folded: bool = False) -> None:
    """出一张预览图。

    `extra_*` 模拟「用户拖过」的尺寸。**不要在 show() 之后调 begin_resize()** ——
    那会真进系统缩放循环，脚本会卡住；这里只是直接设 `_user_size` + resize。
    """
    if rows is not None:
        snap.recent = snap.recent[:rows]
    win = FloatingWindow()
    # ⚠️ 出图必须是**默认态**，不能继承 `QSettings float/size`（用户上次拖的尺寸）——
    #    2026-09-15 实测踩到：用户把窗口拖成 299×425 之后，所有渲染图都变成那个尺寸，
    #    看着像把布局改坏了，白排查一轮。要用尺寸就显式传 `extra_*`。
    win._user_size = None
    win.setFixedWidth(floating.window_width())
    win.set_snapshot(snap)
    if folded:
        win.set_recent_expanded(False)
    if extra_w or extra_h:
        win._enter_user_size_mode()
        size = QSize(win.width() + extra_w, win.height() + extra_h)
        win._user_size = size
        win.resize(size)
    win.show()
    qapp.processEvents()
    pix = _grab(win)
    out = os.path.join(PROJ, 'tmp', name)
    pix.save(out, 'PNG')
    print('  %-24s %d x %d（1 倍）-> %s'
          % (name, pix.width(), pix.height(), out))
    win.hide()
    win.deleteLater()


def shot_panel(qapp) -> None:
    """出「悬浮窗 + 贴右侧的选择器面板」的合成图（1 倍）。

    * 面板里的 M5 候选来自 **demo 的未标注那行**（`core/ui_demo` 里给它塞了露出卡）；
    * ⚠️ **不联网**：参照库拿不到就退化成「没有建议」的普通面板；
    * ⚠️ 只**只读**真库拿 meta 清单 / 卡图；真库不在就跳过这张图。
    """
    try:
        from core import deck_guess as dg
        from core.storage import Storage
        from core.ui_demo import DemoSandboxStorage
        from ui.selector import DeckSelectorPanel

        storage = DemoSandboxStorage(
            Storage(os.path.join(PROJ, 'data', 'masterduel.db')))
    except Exception as exc:                          # pragma: no cover
        print('  %-24s 跳过（%s）' % ('_demo_panel.png', exc))
        return

    win = FloatingWindow()
    win._user_size = None
    win.setFixedWidth(floating.window_width())
    win.set_snapshot(demo_snapshot())
    win.move(40, 40)                                  # 挪到左边，右边才放得下面板
    win.show()
    qapp.processEvents()

    index, groups = None, None
    try:
        index, groups = dg.load_index()
    except Exception:
        pass
    anchor = win.row_widgets()[2]                     # 未标注那行（带露出卡）
    cands = ([c for c in dg.suggest(anchor.row, index, groups) if c.get('hits')]
             if index is not None else [])
    panel = DeckSelectorPanel(storage, row=anchor.row, lang='cn',
                              suggestions=cands, parent=win)
    panel.show_near(anchor)
    qapp.processEvents()
    # ⚠️ 面板内容变了（2026-09-16 多了「自定义卡组名」那一行）——出图前显式按 sizeHint
    #    重算一次，否则 `_grab` 会按旧高度裁掉最后一行（预览图看着像布局坏了）。
    panel.adjustSize()
    panel.resize(panel.sizeHint())
    qapp.processEvents()

    win_pix, panel_pix = _grab(win), _grab(panel)
    w = win_pix.width() + panel_pix.width() + 36
    h = max(win_pix.height(), panel_pix.height()) + 24
    canvas = QImage(w, h, QImage.Format_ARGB32)
    canvas.fill(0xFF202225)                           # 中性灰底，方便看圆角/边界
    p = QPainter(canvas)
    p.drawPixmap(12, 12, win_pix)
    p.drawPixmap(24 + win_pix.width(), 12, panel_pix)
    p.end()
    out = os.path.join(PROJ, 'tmp', '_demo_panel.png')
    canvas.save(out)
    print('  %-24s %d x %d（1 倍）-> %s' % ('_demo_panel.png', w, h, out))
    panel.close()
    win.hide()
    win.deleteLater()


def shot_dashboard(qapp) -> None:
    """S7 看板 -> ``tmp/_demo_dashboard.png``（战绩页，demo 数据）+ ``_demo_dashboard_records.png``
    （滚动到「对局记录」）。

    ⚠️ 与悬浮窗出图同规矩：走 `fonts.prepare_app()`、**不吃 QSettings 尺寸**（显式 resize）。
    ⚠️ 滚动截图不能直接 `setValue` —— 内容重建后滚动范围是**懒更新**的，
    不先 `adjustSize()` 会被夹到 0（S6 那轮踩过，两张图 md5 一样）。
    """
    from core.ui_demo import demo_stats_page
    from ui.dashboard import DashboardWindow

    def _collector():
        #: D4b：自检时给一份**样例**状态（真库那条链在 app 侧）
        return {'running': True, 'at': '2026-09-22 09:59:57', 'age_sec': 3,
                'in_duel': False, 'game_pid': None}

    def _export(month=None, mode=None):
        #: D4c：导出页预览（自检用样例）
        return {'months': [('', '全部月份'), ('2026-09', '2026-09')],
                'rows': [{'date': '09-16', 'time': '23:45', 'result': '胜',
                          'coin': '赢', 'went_first': '先攻', 'my_deck': '闪刀姬',
                          'opp_deck': '烙印', 'mode_label': '排名决斗'},
                         {'date': '09-16', 'time': '23:27', 'result': '负',
                          'coin': '', 'went_first': '后攻', 'my_deck': '闪刀姬',
                          'opp_deck': '烙印星辰', 'mode_label': '排名决斗'}],
                'total': 53}

    from core.ui_demo import demo_analysis_page
    win = DashboardWindow(loader=demo_stats_page,
                          collector_loader=_collector,
                          export_loader=_export,
                          analysis_loader=demo_analysis_page)
    win.resize(1200, 620)
    win.open_page('stats')
    win.show()
    qapp.processEvents()
    out = os.path.join(PROJ, 'tmp', '_demo_dashboard.png')
    _grab(win).save(out)
    print('  %-24s %d x %d（1 倍）-> %s'
          % ('_demo_dashboard.png', win.width(), win.height(), out))

    #: 再出一张**整页高图**（1200×1060）—— 一屏看完三块，省得对着两张滚动图拼
    win.resize(1200, 1060)
    qapp.processEvents()
    out3 = os.path.join(PROJ, 'tmp', '_demo_dashboard_full.png')
    _grab(win).save(out3)
    print('  %-24s %d x %d（1 倍）-> %s'
          % ('_demo_dashboard_full.png', win.width(), win.height(), out3))

    # ---- 设置概览页（09-26 设置页优化）：出一张自检图（铁律 9：只自检，别贴给用户）
    win.resize(1200, 620)
    win.open_page('settings')
    qapp.processEvents()
    out4 = os.path.join(PROJ, 'tmp', '_demo_dashboard_settings.png')
    _grab(win).save(out4)
    print('  %-24s %d x %d（1 倍）-> %s'
          % ('_demo_dashboard_settings.png', win.width(), win.height(), out4))
    for key in ('s_lang', 's_close'):
        win.open_page(key)
        qapp.processEvents()
        out5 = os.path.join(PROJ, 'tmp', '_demo_dashboard_%s.png' % key)
        _grab(win).save(out5)
        print('  %-24s %d x %d（1 倍）-> %s'
              % ('_demo_dashboard_%s.png' % key, win.width(), win.height(), out5))
    win.open_page('stats')

    win.resize(1200, 620)
    qapp.processEvents()
    page = win.pages['stats']
    page.host.adjustSize()
    qapp.processEvents()
    bar = page.scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    qapp.processEvents()
    out2 = os.path.join(PROJ, 'tmp', '_demo_dashboard_records.png')
    _grab(win).save(out2)
    print('  %-24s %d x %d（1 倍）-> %s  [滚动到底：%d/%d]'
          % ('_demo_dashboard_records.png', win.width(), win.height(), out2,
             bar.value(), bar.maximum()))
    #: 对局分析页（2026-10-16 加）：矩阵视图 + 手坑汇总视图（各一张自检图）
    win.open_page('analysis')
    qapp.processEvents()
    out6 = os.path.join(PROJ, 'tmp', '_demo_analysis.png')
    _grab(win).save(out6)
    print('  %-24s %d x %d（1 倍）-> %s'
          % ('_demo_analysis.png', win.width(), win.height(), out6))
    ap = win.pages.get('analysis')
    if ap is not None:
        ap._pick_view('traps')
        qapp.processEvents()
        out7 = os.path.join(PROJ, 'tmp', '_demo_analysis_traps.png')
        _grab(win).save(out7)
        print('  %-24s %d x %d（1 倍）-> %s'
              % ('_demo_analysis_traps.png', win.width(), win.height(), out7))
        ap._pick_view('matrix')

    #: D4c：导出页（左栏独立一项）
    win.open_page('export')
    qapp.processEvents()
    out2 = os.path.join(PROJ, 'tmp', '_demo_export.png')
    _grab(win).save(out2)
    print('  %-24s %d x %d（1 倍）-> %s'
          % ('_demo_export.png', win.width(), win.height(), out2))

    win.hide()
    win.deleteLater()


def main() -> int:
    qapp = QApplication([])
    # ★ 必须走 prepare_app()：否则出图**不注册自带字体**，图和真机不是一套字体
    #   （2026-09-15 踩过：图是雅黑、真机是得意黑，连窗口高度都不同）
    fonts.prepare_app(qapp)
    shot(qapp, demo_snapshot(), '_demo_float.png', rows=9)
    shot(qapp, demo_snapshot(), '_demo_float_10.png', rows=9,
         extra_h=6 * theme.MATCH_ROW_H)
    shot(qapp, demo_snapshot(), '_demo_float_wide.png', rows=4, extra_w=140)
    shot(qapp, demo_snapshot(), '_demo_float_folded.png', rows=9, folded=True)
    shot(qapp, UiSnapshot(), '_demo_empty.png')
    shot_panel(qapp)
    shot_dashboard(qapp)
    #: ⚠️ `shot_stats()`（S6 统计窗）已随 `ui/stats_window.py` 一起删掉（D4 / Q2）
    return 0


if __name__ == '__main__':
    sys.exit(main())
