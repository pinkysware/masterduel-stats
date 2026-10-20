# -*- coding: utf-8 -*-
"""界面复用件（M7）—— 所有控件都从 `DESIGN.md` 的 token 取值，**不在调用处写数字**。

这一层是「代码 ↔ DESIGN.md」的唯一翻译点：
想改观感就改 `theme.py` / `DESIGN.md`，别在这里或页面里手写颜色与字号。

★ **两条硬规矩（都是踩出来的，别改回去）**：

    **① 字体（族 / 字号 / 字重）一律用 QFont 设，不要写进 QSS。**

    **② 同一个控件上，`setStyleSheet()` 必须在 `setFont()` 之前。**

    这两条合起来解释了一个很能骗人的症状：悬浮窗里的数字看着"像 `()`"。
    实测（`tmp/_order_probe.png`，2026-09-14）——同一个 `0 43.8%`：

    ===============================  ======
    ``setFont(22)`` → ``setStyleSheet``   字号被弹回默认（≈13px），`0` 的细弧线糊成 `()` ❌
    ``setStyleSheet`` → ``setFont(22)``  正常 ✅
    ===============================  ======

    也就是说：**不是字形坏了，是字号根本没生效。** 所以下面所有控件都写成
    「先设样式表（颜色/圆角/内边距），再设字体」；改颜色时（:func:`set_metric`）
    也**必须补一次 `setFont`**，否则改一次颜色掉一次字号。
"""
from __future__ import annotations

import os
import time
from typing import Optional, Sequence

from PySide6.QtCore import QEventLoop, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QLabel, QRadioButton,
                               QStyle, QStyleOptionButton, QVBoxLayout,
                               QWidget)

# ⚠️ **这一层里禁止「函数内 from PySide6... import」**（2026-09-16 段错误教训）：
#    函数内首次导入某个 PySide6 名字会走 shiboken 的惰性 feature-import，
#    而它一旦在**垃圾回收期间**被触发就崩（实测 `round_pixmap` 在列表重建时命中，
#    faulthandler 栈：Garbage-collecting -> feature_import -> round_pixmap）。
#    一律提到模块级，全局只导一次。

from ui import theme

#: 项目根（卡图路径可能是相对的，解析时要拼回来）
PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 卡图缓存：`(绝对路径, 尺寸, 透明度) -> QPixmap`。列表刷新时反复读盘很浪费。
_PIX_CACHE: dict = {}

#: role -> (字号, 字重)。**与 `DESIGN.md` §3 的排版层级表一一对应。**
ROLE_FONT = {
    'label': (theme.F_LABEL, QFont.Weight.Normal),
    'label_sm': (theme.F_LABEL_SM, QFont.Weight.Normal),
    'muted': (theme.F_BODY_SM, QFont.Weight.Normal),
    'body': (theme.F_BODY, QFont.Weight.Normal),
    'title': (theme.F_TITLE, QFont.Weight.Medium),
    'eyebrow': (theme.F_LABEL, QFont.Weight.Medium),      # 分组标题（正字距）
    'tab': (theme.F_TAB, QFont.Weight.Medium),
    'metric': (theme.F_STAT, QFont.Weight.Medium),   # 悬浮窗统计块的大数值
}


def round_pixmap(path, size: int = 26, radius: int = theme.R_XS,
                 opacity: float = 1.0):
    """卡图 → **居中裁剪成正方形 + 圆角**的 QPixmap；读不出来返回 None。

    * **居中裁剪**而不是拉伸 —— 卡绘本来就是方的，但万一不是，拉伸会变形；
    * **带缓存** —— 悬浮窗每次刷新都要重画这几张图，别每次都读盘；
    * `opacity < 1` 用来画「未标注」的占位（半透明的默认图）。
    """
    if not path:
        return None                      # 空路径先返回（别碰 Qt）
    full = path if os.path.isabs(path) else os.path.join(PROJ_ROOT, path)
    key = (os.path.normcase(full), int(size), float(opacity))
    if key in _PIX_CACHE:
        return _PIX_CACHE[key]

    src = QPixmap(full)
    if src.isNull():
        _PIX_CACHE[key] = None
        return None

    side = min(src.width(), src.height())
    src = src.copy((src.width() - side) // 2, (src.height() - side) // 2,
                   side, side)
    src = src.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    out = QPixmap(size, size)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(clip)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, src)
    p.end()
    _PIX_CACHE[key] = out
    return out


def forget_pixmap(path, size: Optional[int] = None) -> int:
    """把某个**路径**的卡图从缓存里踢掉（**换图后必须调**）。

    ⚠️ 为什么需要它（2026-10-08 用户实测：「改了卡图不刷新，必须重启 UI」）：
    换卡图时**文件名不变**（`data/deck_images_custom/<名字>.webp` 原地覆盖），
    而这里的缓存键是「路径 + 尺寸 + 透明度」—— 内容换了仍命中旧 pixmap，
    界面就一直显示旧图，直到进程重启（缓存清空）。

    ``size=None`` 清掉这个路径**所有尺寸**的缓存；返回踢掉了几个。
    """
    if not path:
        return 0
    full = path if os.path.isabs(path) else os.path.join(PROJ_ROOT, path)
    want = os.path.normcase(full)
    dead = [k for k in list(_PIX_CACHE)
            if k[0] == want and (size is None or k[1] == int(size))]
    for k in dead:
        _PIX_CACHE.pop(k, None)
    return len(dead)


def thumb_label(path, size: int = 26, radius: int = theme.R_XS,
                opacity: float = 1.0) -> QLabel:
    """卡图小标签（**图缺失就留一个同尺寸的空占位**，布局不会跳）。"""
    lab = QLabel()
    lab.setFixedSize(size, size)
    lab.setStyleSheet('background: %s; border-radius: %dpx;'
                      % (theme.SURFACE2, radius))
    pix = round_pixmap(path, size, radius, opacity)
    if pix is not None:
        lab.setPixmap(pix)
    return lab


def font(size: int, weight=QFont.Weight.Normal,
         families: Optional[Sequence[str]] = None, tabular: bool = False,
         lang: Optional[str] = None) -> QFont:
    """按 DESIGN.md 造字体：**族 + 像素字号 + 字重**，可选等宽数字。

    一律用像素字号（``setPixelSize``）—— 点字号会随系统 DPI 变，摆不准。

    ``lang`` 决定 CJK 回退链（见 :func:`ui.theme.families`）。默认走**中文链** ——
    界面文案现在全是中文；本地化那一期把界面文案的语言传进来即可，字体层不用再动。
    """
    f = QFont()
    f.setFamilies(list(families or theme.families(lang or theme.DEFAULT_LANG)))
    f.setPixelSize(int(size))
    f.setWeight(weight)
    if tabular:
        try:
            f.setFeature(QFont.Tag('tnum'), 1)
        except Exception:
            pass                                    # 老 Qt 没有就算了，不影响可用性
    return f


def apply_role(widget: QWidget, role: str = 'label') -> QWidget:
    """按 role 套字体（字号/字重来自 :data:`ROLE_FONT`）。"""
    size, weight = ROLE_FONT.get(role, ROLE_FONT['label'])
    widget.setFont(font(size, weight))
    return widget


def label(text: str, role: str = 'label', align=Qt.AlignVCenter | Qt.AlignLeft
          ) -> QLabel:
    """普通标签。颜色由 QSS 按 ``[role=...]`` 给，字体由本函数给。"""
    lab = QLabel(text)
    lab.setProperty('role', role)
    lab.setAlignment(align)
    return apply_role(lab, role)


def metric(text: str = '', size: int = theme.F_METRIC, color: str = theme.INK,
           align=Qt.AlignCenter) -> QLabel:
    """大数字（悬浮窗 2×2 格、统计条）：**QFont 设字号 + 等宽数字**。

    ⚠️ 顺序不能反：**先样式表，后字体**（见模块头 ②）。
    """
    lab = QLabel(text)
    lab.setAlignment(align)
    lab.setStyleSheet('color: %s; background: transparent;' % color)
    lab.setFont(font(size, QFont.Weight.Medium, tabular=True))
    return lab


def set_metric(label_widget: QLabel, text: str, color: str,
               size: int = theme.F_METRIC) -> None:
    """改大数字的内容与颜色。

    ⚠️ **改样式表会把字体弹回去，所以这里必须补一次 setFont** ——
    漏了就会出现"数值一刷新，字号掉回 13px"这种极难查的 bug。
    """
    label_widget.setText(text)
    label_widget.setStyleSheet('color: %s; background: transparent;' % color)
    label_widget.setFont(font(size, QFont.Weight.Medium, tabular=True))


def value_chip(text: str, fg: str = theme.INK, bg: str = theme.NEUTRAL_SOFT
               ) -> QLabel:
    """小胶囊（胜/败/待标注这类）。**语义色前景 + soft 底**，不是整块饱和色。"""
    lab = QLabel(text)
    lab.setAlignment(Qt.AlignCenter)
    lab.setFixedHeight(theme.BADGE_H)
    lab.setStyleSheet('color: %s; background: %s; border-radius: %dpx;'
                      ' padding: 1px 7px;'
                      % (fg, bg, theme.R_XS))
    lab.setFont(font(theme.F_LABEL, QFont.Weight.Medium))   # 样式表在前，字体在后
    return lab


def hairline(horizontal: bool = True) -> QWidget:
    """1px 发丝分隔线（**半透明白**，叠在任何表面上都协调）。"""
    w = QWidget()
    if horizontal:
        w.setFixedHeight(1)
    else:
        w.setFixedWidth(1)
    w.setStyleSheet('background: %s;' % theme.HAIRLINE)
    return w


def bar(color: str, width: int = theme.BAR_W) -> QWidget:
    """状态竖条 —— **全系统唯一允许的饱和实心块**（宽 3px）。"""
    w = QWidget()
    w.setFixedWidth(width)
    w.setStyleSheet('background: %s; border-radius: %dpx;'
                    % (color, max(width // 2, 1)))
    return w


def panel(object_name: str = 'panel', pad: int = theme.CARD_PAD,
          radius: int = theme.R_LG, bg: str = theme.SURFACE1) -> QWidget:
    """标准面板：``surface-1`` 底 + 1px 发丝 + 圆角 10。"""
    w = QWidget()
    w.setObjectName(object_name)
    w.setStyleSheet(
        '#%s { background: %s; border: 1px solid %s; border-radius: %dpx; }'
        % (object_name, bg, theme.HAIRLINE, radius))
    if pad:
        lay = QVBoxLayout(w)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(theme.S_SM)
    return w


def color_or(default: str, value: Optional[str]) -> str:
    return value or default


# --------------------------------------------------------------- 勾选类控件
def _indicator_rect(widget) -> QRect:
    """指示器（那个小方块/小圆圈）在控件里的矩形 —— 由样式算，别自己猜。

    ⚠️ 所需名字（`QStyle` / `QStyleOptionButton`）**在模块头导入** ——
    函数内 import PySide6 违反本模块自述红线（BUG-B7-10 / 2026-09-16 段错误）。
    """
    opt = QStyleOptionButton()
    widget.initStyleOption(opt)
    sub = (QStyle.SE_RadioButtonIndicator
           if isinstance(widget, QRadioButton) else QStyle.SE_CheckBoxIndicator)
    return widget.style().subElementRect(sub, opt, widget)


def _draw_mark(widget, radio: bool = False) -> None:
    """在 QSS 画好的指示器上**补一个勾**。

    ⚠️ 2026-09-15 用户实测：「单选项打不了勾、『不再提示』只是变颜色」——
    QSS 的 `::indicator:checked` 只能改**底色/边框**，想画勾得引图片资源。
    这里干脆自己画（和折三角、窗口按钮同一套思路：不赌平台样式、不引素材）。
    ⚠️ 单选项的指示器也**画勾**（用户 2026-09-15：「圆点太丑，换成打勾」）——
    互斥行为不变，只是视觉上和勾选框一致。
    """
    if not widget.isChecked():
        return
    r = _indicator_rect(widget)
    if r.isEmpty():
        return
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(theme.CANVAS), 2))     # 勾：一道折线（单选项/勾选框同一套）
    p.setBrush(Qt.NoBrush)
    x, y, w, h = (r.x(), r.y(), r.width(), r.height())
    p.drawPolyline([QPointF(x + w * 0.22, y + h * 0.55),
                    QPointF(x + w * 0.42, y + h * 0.76),
                    QPointF(x + w * 0.78, y + h * 0.26)])


class TickBox(QCheckBox):
    """复选框：勾是**自己画的**（见 :func:`_draw_mark`）。"""

    def paintEvent(self, ev):                                   # noqa: N802
        super().paintEvent(ev)
        _draw_mark(self, radio=False)


class DotRadio(QRadioButton):
    """单选项：指示器 = **方框 + 勾**（自己画，见 :func:`_draw_mark`）。

    ⚠️ 类名是历史遗留（原来画圆点，2026-09-15 用户嫌丑改成勾）。行为仍是**互斥的单选项**。
    """

    def paintEvent(self, ev):                                   # noqa: N802
        super().paintEvent(ev)
        _draw_mark(self, radio=True)


class BusyOverlay(QWidget):
    """「加载中」旋转遮罩（2026-10-05 用户：切语言原地重译有卡顿感）。

    半透明全窗覆盖 + 中央旋转弧线。⚠️ **重译是主线程阻塞的**，动画在阻塞
    期间会停 —— 所以别直接用：走 :func:`run_with_busy`（先 show + 
    processEvents 画一帧再干活；结束后遮罩至少停留 ~400ms 再收，
    用户能看见「加载过了」，不是闪一下）。遮罩盖住全窗，顺带吞掉
    重译期间的点击。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, ev):                                   # noqa: N802
        # ★ 六段弧、头亮尾暗（Windows 加载圈同款）—— 重译阻塞期间动画会停，
        #   单段弧停住像「转到一半」；分段环冻住也是一整圈完整的样子。
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 115))
        r = 15.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        ring = QRectF(cx - r, cy - r, r * 2, r * 2)
        for i in range(6):
            alpha = max(235 - i * 40, 45)
            p.setPen(QPen(QColor(255, 255, 255, alpha), 4,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, -self._angle * 16 - i * 60 * 16, 36 * 16)


def run_with_busy(widget, fn, min_ms: int = 400, pre_ms: int = 150):
    """在 `widget` 上显示旋转遮罩并**同步**执行 `fn()`，结束后收回遮罩。

    * **两段式**（2026-10-05 实测：只 processEvents 画一帧就阻塞，动画
      根本没转起来）：先让事件循环跑 `pre_ms` 毫秒把动画转起来，再进
      `fn()` 的阻塞段；
    * `fn()` 完成后遮罩至少停留 `min_ms` 毫秒（不足就 singleShot 补齐）；
    * 遮罩是普通子控件（无 WA_TransparentForMouseEvents）—— 停留期间
      吞掉点击，避免半路交互。
    """
    overlay = BusyOverlay(widget)
    overlay.setGeometry(widget.rect())
    overlay.show()
    overlay.raise_()
    if pre_ms > 0:
        loop = QEventLoop()
        QTimer.singleShot(pre_ms, loop.quit)
        loop.exec()
    t0 = time.time()
    try:
        return fn()
    finally:
        remain = int(min_ms - (time.time() - t0) * 1000)
        if remain > 0:
            QTimer.singleShot(remain, overlay.stop)
            QTimer.singleShot(remain, overlay.deleteLater)
        else:
            overlay.stop()
            overlay.deleteLater()
