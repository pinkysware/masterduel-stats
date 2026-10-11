# -*- coding: utf-8 -*-
"""S7 看板（Dashboard）主窗口 —— **D0：壳**。

计划：`docs/M7看板计划.md`（v5）。D0 只做**骨架**，不接任何数据：

* 左栏导航（**战绩 / 对局分析 / 卡组 / 禁限表**）+ 左栏最下的**设置区**
  （「获取新卡组名」不在设置区 —— Q17 拍板挪进「卡组」页）；
* 设置区**只靠字号分层**（「设置」大、设置项小，不缩进不箭头）；
* **点导航或任一设置项 → 右侧主区换页**（操作一律在主区，不弹窗、不滑抽屉）；
* 主区未排到的页是**正式空态页**（标题 + 说明，无任何临时机制）；
* 可**拖拽缩放**（系统原生，8px 热区），**最小 1200×620 逻辑**（用户 2026-09-17 口径：可拖拽、这是最小尺寸）；
* 尺寸/位置/当前页存 QSettings（`dash/*`，与悬浮窗 `float/*` 分开）。

⚠️ 三条 Qt 纪律（`DESIGN.md` §11）：QSS 不写 `font-*` / 样式表要在 `setFont` 之前 /
   文字控件高度必须 ≥ 字体高（矮一点就垂直裁字）。
⚠️ `Qt` 名字**一律模块级 import** —— 函数内首次导入某个 PySide6 名字会走 shiboken
   惰性 feature-import，撞上垃圾回收会**段错误**（2026-09-16 实机踩过）。
⚠️ **别自己往 UI 加内容**（教训：自作主张加的卡组名列被要求撤掉）—— 本文件只放计划里写好的东西。
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import (QPoint, QPointF, QRectF, QSettings, QSize, Qt,
                            Signal)
from PySide6.QtGui import (QActionGroup, QColor, QFont, QFontMetrics,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from core import ui_state          # D1：月历格子阵 + 口径纯函数（窗口只画不查库）
from ui.present_page import PresentPage   # 礼盒页（2026-09-19 用户定，见 docs/礼盒看板设计.md）
from ui.analysis_page import AnalysisPage  # 对局分析矩阵（D2，见 docs/M7对局分析矩阵计划.md）
from ui.banlist_page import BanlistPage    # 禁限表（D3 第二刀，本地 banlist_changes 只读视图）
from ui.decks_page import DecksPage        # 卡组页（D3 §2：卡组名管理 / Tier 表 / 卡组构成 三页签）
from ui.export_page import ExportPage      # 导出页（D4c：左栏独立一项，按月 + 模式）
from ui.i18n import tr                     # 界面骨架三语（2026-10-03 本地化）
from ui.script_page import SCRIPT_COMMANDS, exit_note    # 工具命令表 + 退出码翻译
from ui.script_runner import ScriptRunner               # 概览页兜底 factory
from core.storage import MODE_GROUP_NAMES   # 模式筛选的四个名字（唯一来源）
from ui import theme, widgets
from ui.floating import (COIN_BACK, COIN_FRONT, COIN_SIZE,
                         RESIZE_MARGIN, WindowButton, cursor_for)

#: 最小尺寸（逻辑像素）—— 用户口径：**可拖拽，这是最小**（1800×930 物理 ÷ DPR 1.5）
MIN_W, MIN_H = 1200, 620
#: 标题条高度（拖拽区 + 最小化 / 关闭）
TITLE_H = 34
#: 左栏宽度（导航 + 设置区都在这一列）—— 字号/栏宽唯一来源是 `theme.RAIL_*`
RAIL_W = theme.RAIL_W

#: 主导航：(显示名, 页 key) —— 顺序即侧栏顺序（计划 §2.2）
#: ⚠️ 2026-09-18 调整：「悬浮窗」占原来「禁限表」的位置（卡组下方）；
#:    「禁限表」挪到下面的「查询」分组里。
NAV_PAGES: Tuple[Tuple[str, str], ...] = (
    ('战绩', 'stats'),
    ('对局分析', 'analysis'),
    ('卡组', 'decks'),
    ('导出', 'export'),
    ('悬浮窗', 'overlay'),
)

#: 「查询」分组：(显示名, 页 key) —— **左栏里挂在「查询」标题下面**（用户 2026-09-18 口径）
QUERY_PAGES: Tuple[Tuple[str, str], ...] = (
    ('禁限表', 'banlist'),
    ('查卡', 'cards'),
    ('礼盒', 'present'),
)
#: ⚠️ 「Tier 表」**不在「查询」里了**（用户 2026-09-21：「把查询下的 Tier 表做到卡组的
#: 卡组构成页签左边，移除查询下的 tier 表」）—— 它是「卡组」页的页签 2。

#: 「查询」分组标题自己的 key（点了显示分组概览）
QUERY_KEY = 'query'

#: 设置项：(显示名, 页 key)。⚠️ **2026-09-18 起不再列在左栏** ——
#: 左栏只有最下一个「设置」，点进去在主区看这份索引；再点某一项才进对应页。
SETTINGS_PAGES: Tuple[Tuple[str, str], ...] = (
    ('数据体检', 's_audit'),
    ('抓禁限表', 's_banlist'),
    ('卡表更新', 's_cardtable'),
    ('参照库刷新', 's_refdecks'),
    ('语言', 's_lang'),
    ('游戏启动时弹悬浮窗', 's_autoshow'),
    ('当窗口关闭时', 's_close'),
    ('开机自启', 's_autostart'),
    ('重启界面', 's_restart'),
)

#: 点左栏「设置」本身 = 进这个**设置概览页**（悬浮窗右键「设置…」也进这里，Q18）
SETTINGS_KEY = 'settings'

#: 概览页的分组（三组九项；键并集 = `SETTINGS_PAGES`、无重复 —— 测试锁着）。
#: ⚠️ 3 个工具（数据体检/抓禁限表/卡表更新）在概览页行内「运行」
#: （09-27 起无独立工具页），这里只负责分组展示。
#: ⚠️ 「刷新数据」（s_refresh）2026-10-05 用户拍板移除 —— 常驻采集器自动做；
#:    工具本体 tools/import_duel_records.py 保留（--apply 仍可手动跑）。
SETTING_GROUPS = (
    ('数据工具', ('s_audit', 's_banlist', 's_cardtable', 's_refdecks')),
    ('界面', ('s_lang',)),
    ('行为', ('s_autoshow', 's_close', 's_autostart', 's_restart')),
)

#: 每个设置项在概览页的一行说明（短句；长说明在子页里）
SETTING_NOTES = {
    's_audit': '检查库 / 卡表 / 记录目录的一致性',
    's_banlist': '从 masterduelmeta 抓禁限变更史（每月一次）',
    's_cardtable': '上游卡表比对与更新（三语卡名）',
    's_refdecks': '卡组构成的参照库（每张卡的使用率，每月一次）',
    's_lang': '卡名 / 卡组名的显示语言（匹配一律走卡 id）',
    's_autoshow': '检测到游戏启动时，自动弹出悬浮窗',
    's_close': '点悬浮窗的 × 是隐藏到托盘还是退出',
    's_autostart': '开机后自动启动悬浮窗',
    's_restart': '改了 UI 代码后一键重启界面进程',
}


def _autostart_state() -> bool:
    """开机自启当前状态（查「启动」目录；读不了 = 关）。"""
    try:
        from core import ui_actions
        return bool(ui_actions.is_autostart())
    except Exception:
        return False


#: 数据工具四项（概览页行内「运行」按钮直接跑，09-27 起）；
#: 命令表唯一来源 = `ui/script_page.SCRIPT_COMMANDS`，别在这儿另抄一份
SCRIPT_PAGE_KEYS = tuple(SCRIPT_COMMANDS)

#: 每页的说明文案（未排到内容的页显示在正式空态页上）
PAGE_HINTS = {
    'stats': ('月历热力图 · 战绩总览 · 对局记录',
              'D1 实现：热力图 → 总览五格 → 按我方卡组分组的逐局行（8 列）'),
    'banlist': ('禁限表（当前禁限状态 + 变更史）',
                'D3 第二刀：现在的禁限表（禁止/限制/准限制三分组）+ 最近变更；'
                '数据 = fetch_banlist.py 每月手动刷新'),
    'analysis': ('我方卡组 × 对手卡组 矩阵',
                 'D2：行 = 我方、列 = 对手（照图）+ 均胜率列（场次加权）；'
                 '三页签（综合/先攻/后攻），行列轴按综合固定；'
                 '伪条目默认隐藏；只做图不跳转'),
    'export': ('导出对局记录（CSV / JSON）',
               'D4c（用户 2026-09-22：左栏独立一项）：**按月 + 按模式**筛选后导出；'
               '日期/时间已分列，「硬币」与「先后手」是**两个**字段'
               '（硬币 = 赢得选择权，不是先攻；旧记录多半没有 → 空着就是没记录）'),
    'decks': ('卡组名管理 · Tier 表 · 卡组构成',
              'D3 §2（用户 2026-09-21）：**三个页签** —— 「卡组名管理」= 改覆盖表'
              '（中文名/别名/显示英文名/隐藏/自定义卡组名）+ [获取新卡组名]（Q17）；'
              '「Tier 表」= masterduelmeta 榜单本地视图（**从「查询」搬来的**，'
              '点芯片 → 同页切「卡组构成」）；「卡组构成」= 主卡/额外卡图网格'
              '（数据 = 月度参照库 99 副）。'
              '⚠️ 我方卡组聚合列表**已删**（战绩页已按我方卡组分组显示同一批数字）'),
    'overlay': ('悬浮窗',
                '以后放：**悬浮窗自己的功能开关**（参考 Untapped 那份清单 —— 启用悬浮窗 / '
                '后台运行才显示 / 卡片名称用游戏语言 / 显示召唤记录 / 显示墓地·除外预览 / '
                '对手卡牌记录（list·grid）/ 已发动效果图标 / 持续效果图标 / 每日牌组统计 / '
                '决斗计时 / 决斗结束汇总 / 牌组自动导入 / 始终置顶…）。'
                '⚠️ 用户口径：**这一项之后再做**'),
    'cards': ('查卡（ygocdb）',
              'D3 实现：输入卡名 → **本地卡名表（14610 张三语）即时给候选**（离线、不爬站）→ '
              '点候选用**默认浏览器打开 ygocdb 页**看效果文/卡图。'
              '（用户让我定「网页查还是 API 查」：**走网页外链**，不在程序里抓站）'),
    'present': ('礼盒未领取物品',
                '已实现（2026-09-19）：读游戏内存里的未领取礼盒条目，'
                '按领取期限分组 —— 点「读取礼盒」才读一次（M7 不读内存的唯一破例口子）'),
    QUERY_KEY: ('查询', '下面三项：禁限表 / 查卡 / 礼盒'),
    SETTINGS_KEY: ('设置',
                   '右侧即控件：改完即存、点「运行」直接跑（输出在下方日志区）'),
}



def settings() -> QSettings:
    """与悬浮窗同一份 QSettings（`masterduel/M7`），键分开：`dash/*`。"""
    return QSettings('masterduel', 'M7')


def _lab(text: str, role: str = 'body', size: Optional[int] = None,
         weight=QFont.Weight.Normal,
         align=Qt.AlignVCenter | Qt.AlignLeft) -> QLabel:
    """定高文字标签 —— **高度跟着字体算**（矮一点就垂直裁字）。"""
    lab = widgets.label(text, role, align)
    if size:
        lab.setFont(widgets.font(size, weight))
    lab.setFixedHeight(lab.fontMetrics().height() + 2)
    return lab


#: 哪些 key 有图标（用户 2026-09-18 点名的 6 个；**分组下的子项不给**，
#: 正好用「有没有图标」再加一层层级差）
ICON_KEYS = ('stats', 'analysis', 'decks', 'overlay', 'query', 'settings')


def draw_nav_icon(painter, key: str, size: int, color: str) -> None:
    """画左栏图标（**自绘**，零素材依赖）。

    为什么自绘：项目里所有小字形（窗口按钮 `-`/`□`/`×`、折叠三角、勾选框的勾）
    都是 `paintEvent` 画的 —— **字体缺那个几何字符就会出豆腐块**（踩过）。
    图标同理：引 PNG/SVG 素材又要管尺寸/换色/DPI，自绘几行就够，
    而且颜色能跟着「选中/未选中」走。

    每个图标都画在 ``size × size`` 的方框里（留 2px 内边距）。
    """
    p = painter
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    u = size / 18.0                     # 以 18px 为基准等比缩放

    def s(v):
        return v * u

    p.save()
    if key == 'stats':                  # 战绩：柱状图（三根柱子）
        for i, h in enumerate((5, 9, 13)):
            x = s(3.2) + i * s(4.6)
            p.drawLine(QPointF(x, s(15)), QPointF(x, s(15 - h)))
    elif key == 'analysis':             # 对局分析：矩阵（田字格）
        p.drawRoundedRect(QRectF(s(3), s(3), s(12), s(12)), s(1.5), s(1.5))
        p.drawLine(QPointF(s(9), s(3)), QPointF(s(9), s(15)))
        p.drawLine(QPointF(s(3), s(9)), QPointF(s(15), s(9)))
    elif key == 'decks':                # 卡组：两张叠起来的卡
        p.drawRoundedRect(QRectF(s(5.5), s(3), s(9), s(12)), s(1.5), s(1.5))
        p.drawLine(QPointF(s(3), s(5.5)), QPointF(s(3), s(14.5)))
        p.drawLine(QPointF(s(3), s(14.5)), QPointF(s(5.5), s(15)))
    elif key == 'overlay':              # 悬浮窗：小窗 + 标题条
        p.drawRoundedRect(QRectF(s(2.5), s(4), s(13), s(10.5)), s(1.5), s(1.5))
        p.drawLine(QPointF(s(2.5), s(7.5)), QPointF(s(15.5), s(7.5)))
    elif key == 'query':                # 查询：放大镜（圆 + 柄）
        p.drawEllipse(QPointF(s(7.6), s(7.6)), s(4.4), s(4.4))
        p.drawLine(QPointF(s(11), s(11)), QPointF(s(15.3), s(15.3)))
    elif key == 'settings':             # 设置：滑杆（两条轨 + 两个滑块）
        p.drawLine(QPointF(s(3), s(6.5)), QPointF(s(15), s(6.5)))
        p.drawLine(QPointF(s(3), s(11.5)), QPointF(s(15), s(11.5)))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s(6.5), s(6.5)), s(1.9), s(1.9))
        p.drawEllipse(QPointF(s(11.5), s(11.5)), s(1.9), s(1.9))
        p.setBrush(Qt.NoBrush)
    p.restore()


class NavIcon(QWidget):
    """左栏图标控件 —— 只负责把 `draw_nav_icon` 贴到那一行上。

    ⚠️ `WA_TransparentForMouseEvents`：点图标要**算点整行**（否则点图标没反应）。
    """

    def __init__(self, key: str, size: Optional[int] = None,
                 color: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.key = key
        self._size = int(size or theme.RAIL_ICON)
        self._color = color or theme.INK_MUTED
        self.setFixedSize(self._size, self._size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, ev):                                   # noqa: N802
        draw_nav_icon(QPainter(self), self.key, self._size, self._color)


class RailItem(QWidget):
    """左栏的一项（导航项或设置项）。选中态 = 浅底 + 左缘 3px 强调竖条。"""

    clicked = Signal(str)

    def __init__(self, text: str, key: str, big: bool = False,
                 child: bool = False, parent=None):
        super().__init__(parent)
        self.key = key
        self._big = bool(big)
        self._child = bool(child)
        self._selected = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        #: ⚠️ 字号就是**层级本身**（Q13 口径），值来自 theme（用户 2026-09-18 放大过一档）
        self.setFixedHeight(theme.RAIL_H_GROUP if big else theme.RAIL_H_CHILD
                            if child else theme.RAIL_H_ITEM)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_XS, 0, theme.S_SM, 0)
        lay.setSpacing(theme.S_XS)
        #: ⚠️ 选中竖条在**左缘**（DESIGN.md：饱和色只出现在「状态徽章 / 热力图格 /
        #:    左侧 3px 竖条」）—— 放右边了，`_apply()` 里补样式。
        self._dot = QWidget()
        self._dot.setFixedWidth(3)
        lay.addWidget(self._dot)
        #: ⚠️ 字号就是**层级本身**（用户 Q13 口径）：分组标题 > 主导航项 > 子项
        if big:
            size, weight = theme.RAIL_F_GROUP, QFont.Weight.Medium
        elif child:
            size, weight = theme.RAIL_F_CHILD, QFont.Weight.Normal
        else:
            size, weight = theme.RAIL_F_ITEM, QFont.Weight.Normal
        self._size, self._weight = size, weight
        #: 图标在**文字左边**（用户 2026-09-18 口径）；只有 ICON_KEYS 那 6 项有
        self.icon = None
        if key in ICON_KEYS:
            self.icon = NavIcon(key, color=theme.INK_MUTED)
            lay.addWidget(self.icon)
        self.label = _lab(text, 'body' if big else 'label', size=size, weight=weight)
        lay.addWidget(self.label)
        lay.addStretch(1)
        #: 大字号那项（「设置」）不缩进，子项跟着它对齐（层级靠字号，不靠缩进）
        lay.setContentsMargins(theme.S_SM, 0, theme.S_SM, 0)
        self._apply()

    def set_selected(self, on: bool) -> None:
        if bool(on) != self._selected:
            self._selected = bool(on)
            self._apply()

    def is_selected(self) -> bool:
        return self._selected

    def _apply(self) -> None:
        bg = theme.SURFACE2 if self._selected else 'transparent'
        self.setStyleSheet('RailItem { background: %s; border-radius: %dpx; }'
                           % (bg, theme.R_XS))
        self._dot.setStyleSheet(
            'background: %s; border-radius: 1px;'
            % (theme.ACCENT if self._selected else 'transparent'))
        color = theme.INK if self._selected else theme.INK_MUTED
        if self.icon is not None:
            self.icon.set_color(color)
        self.label.setStyleSheet('color: %s; background: transparent;' % color)
        self.label.setFont(widgets.font(
            self._size,
            QFont.Weight.Medium if (self._big or self._selected) else self._weight))

    def mousePressEvent(self, ev):                          # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
            ev.accept()

    def mouseReleaseEvent(self, ev):                        # noqa: N802
        ev.accept()


class Rail(QWidget):
    """左栏：导航项 + 设置区（「设置」大字 + 设置项小字）。

    ★ 顶部品牌字「MD 战绩」= **账号切换器**（2026-10-03 用户：把这几个字
    换成当前账号名、点击切换）—— 未选账号时仍显示「MD 战绩」。
    """

    page_requested = Signal(str)
    account_picked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(RAIL_W)
        self.setStyleSheet('Rail { background: %s; }' % theme.SURFACE1)
        self.items = {}                      # key -> RailItem

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, theme.S_SM, theme.S_SM, theme.S_SM)
        lay.setSpacing(2)

        #: ★ 账号切换器（2026-10-03 用户二轮）：整行可点 —— 文案居左、
        #:   **倒三角贴右缘**（右对齐，提示这是个下拉）。
        self.brand = QWidget()
        self.brand.setObjectName('railBrand')
        self.brand.setAttribute(Qt.WA_StyledBackground, True)
        self.brand.setAttribute(Qt.WA_Hover, True)
        self.brand.setCursor(Qt.PointingHandCursor)
        self.brand.setStyleSheet(
            'QWidget#railBrand { background: transparent; border-radius: %dpx; }'
            'QWidget#railBrand:hover { background: rgba(255,255,255,0.05); }'
            % theme.R_XS)
        bl = QHBoxLayout(self.brand)
        bl.setContentsMargins(2, 0, 6, 0)
        self.brand_text = _lab(tr('MD 战绩'), 'title', size=theme.RAIL_F_BRAND,
                               weight=QFont.Weight.Medium)
        self.brand_text.setStyleSheet('color: %s; background: transparent;'
                                      % theme.INK)
        bl.addWidget(self.brand_text)
        bl.addStretch(1)
        self.brand_arrow = _lab('▾', 'label', size=theme.PAGE_F_SMALL)
        self.brand_arrow.setStyleSheet(
            'color: %s; background: transparent;' % theme.INK_MUTED)
        bl.addWidget(self.brand_arrow)
        for _lbl in (self.brand_text, self.brand_arrow):
            _lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.brand.mousePressEvent = lambda _ev: self.account_picked.emit()
        lay.addWidget(self.brand)
        lay.addWidget(widgets.hairline())
        lay.addSpacing(theme.S_XS)

        for name, key in NAV_PAGES:
            lay.addWidget(self._add(tr(name), key))

        #: 底部三块（从上到下）：「查询」分组 → 它的两项 → 最下的「设置」
        #: ⚠️ 2026-09-18 用户口径：**设置放最下**，且**设置项不列在左栏**
        lay.addStretch(1)
        lay.addWidget(widgets.hairline())
        lay.addSpacing(theme.S_XS)
        lay.addWidget(self._add(tr('查询'), QUERY_KEY, big=True))
        for name, key in QUERY_PAGES:
            lay.addWidget(self._add(tr(name), key, child=True))
        lay.addSpacing(theme.S_XS)
        lay.addWidget(widgets.hairline())
        lay.addWidget(self._add(tr('设置'), SETTINGS_KEY, big=True))

    def _add(self, name: str, key: str, big: bool = False,
             child: bool = False) -> RailItem:
        item = RailItem(name, key, big=big, child=child)
        item.clicked.connect(self.page_requested.emit)
        self.items[key] = item
        return item

    def _brand_text(self) -> str:
        return self.brand_text.text()

    def set_account_label(self, text: str) -> None:
        """品牌位文案 = 当前账号名（未选 = 「MD 战绩」）；倒三角贴右缘。"""
        self.brand_text.setText(text or tr('MD 战绩'))

    def set_current(self, key: str) -> None:
        for k, item in self.items.items():
            item.set_selected(k == key)

    def keys(self):
        return list(self.items)

    def retranslate(self) -> None:
        """切语言原地重译左栏标签（2026-10-05 用户：窗口不关直接切换）。"""
        names = {k: n for n, k in NAV_PAGES}
        names.update({k: n for n, k in QUERY_PAGES})
        names[QUERY_KEY] = '查询'
        names[SETTINGS_KEY] = '设置'
        for key, item in self.items.items():
            if key in names:
                item.label.setText(tr(names[key]))


class PlaceholderPage(QWidget):
    """空态页（**正式**）：标题 + 说明卡。

    服务还没排到内容的页（「悬浮窗开关」等 —— 用户口径：之后再做）。
    ⚠️ 曾经挂在它身上的「打开现有设置对话框（过渡用）」按钮连同老
    `SettingsDialog` 已于 09-26 设置页优化时**整体下掉**——本类不再有任何
    临时机制；`tests/test_ui_dashboard.py` 的「无过渡按钮」全页锁盯着。
    """

    def __init__(self, title: str, subtitle: str, hint: str = '',
                 parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)
        lay.addWidget(_lab(title, 'title', size=theme.F_STAT,
                           weight=QFont.Weight.Medium))
        if subtitle:
            lay.addWidget(_lab(subtitle, 'muted', size=theme.F_METRIC_SM))
        card = widgets.panel('dashPlaceholder', pad=theme.S_LG,
                             radius=theme.R_MD, bg=theme.SURFACE2)
        cl = card.layout()
        cl.setSpacing(theme.S_SM)
        if hint:
            note = _lab(hint, 'muted', size=theme.F_BODY)
            note.setWordWrap(True)
            cl.addWidget(note)
        lay.addWidget(card)
        lay.addStretch(1)


class SettingRow(QWidget):
    """设置概览页的一行：左（名称 + 说明）+ 右（**内联控件**）。

    用户 09-27 口径：所有设置项（含数据工具的「运行」按钮）都在概览页一级完成；
    数据工具的输出落在页底的共用日志区。行是自绘 `QWidget`。
    """

    def __init__(self, name, key, note='', control=None, parent=None):
        super().__init__(parent)
        self._key = key
        self.control = control
        self.setObjectName('settingRow')
        v = QVBoxLayout(self)
        v.setContentsMargins(theme.S_MD, theme.S_SM, theme.S_MD, theme.S_SM)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(theme.S_SM)
        top.addWidget(_lab(name, 'body', size=theme.F_BODY))
        top.addStretch(1)
        if control is not None:
            top.addWidget(control)
        v.addLayout(top)
        if note:
            n = _lab(note, 'muted', size=theme.F_BODY_SM)
            n.setWordWrap(True)
            v.addWidget(n)
        self.setStyleSheet(
            'QWidget#settingRow { background: %s; border-radius: %dpx; }'
            % (theme.SURFACE2, theme.R_MD))


class SettingsIndexPage(QWidget):
    """设置页（**一级直达**）：三组八项，右侧即控件，行内直接改、即保存。

    * 选择类（语言 / 当窗口关闭时）= 行内筛选框；开关（开机自启）= 打钩框；
      动作（重启界面 / 数据工具的运行）= 按钮。
    * 数据工具的**输出落在页底共用日志区**（一次跑一个，跑时按钮全禁用）；
      退出码按 `script_page.exit_note` 翻译（如卡表检查的 1 = 有未知卡）。
    * 用户 09-27：不再显示「卡表 N 张 / 禁限 N 张…」状态文本 —— 要看详情
      跑一次「数据体检」，或去各工具页（已由概览页取代）。
    """

    def __init__(self, subtitle, hint='', runner_factory=None,
                 on_restart=None, on_set_autostart=None,
                 on_lang_changed=None, parent=None):
        super().__init__(parent)
        self._runner_factory = runner_factory or (
            lambda script, args: ScriptRunner(script, args))
        self._on_restart = on_restart
        self._on_set_autostart = on_set_autostart
        #: 语言改了（2026-10-04 用户口径：实时切换）—— app 侧重建两个窗口；
        #: None（独立/测试）= 保持旧行为只提示重启
        self._on_lang_changed = on_lang_changed
        self._runner = None
        self._tool_btns = []          # 运行中的互斥禁用
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)
        lay.addWidget(_lab(tr('设置'), 'title', size=theme.F_STAT,
                           weight=QFont.Weight.Medium))
        if subtitle and subtitle != '设置':
            lay.addWidget(_lab(subtitle, 'muted', size=theme.F_METRIC_SM))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.S_SM)
        self._rows = {}
        by_key = {k: n for n, k in SETTINGS_PAGES}
        for gname, keys in SETTING_GROUPS:
            gname = tr(gname)
            col.addWidget(_lab(gname, 'title', size=theme.PAGE_F_TITLE,
                               weight=QFont.Weight.Medium))
            for key in keys:
                row = SettingRow(tr(by_key.get(key, key)), key,
                                 tr(SETTING_NOTES.get(key, '')),
                                 control=self._make_control(key))
                self._rows[key] = row
                col.addWidget(row)
        col.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        #: 页底共用日志区（数据工具的输出；平时给一行提示）
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(widgets.font(theme.F_BODY_SM))
        self._log.setFixedHeight(112)
        self._log.setPlaceholderText(tr('点「运行」后，脚本输出显示在这里。'))
        lay.addWidget(self._log)
        self._status = _lab('', 'muted', size=theme.F_BODY_SM)
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        if hint:
            lay.addWidget(_lab(hint, 'muted', size=theme.F_METRIC_SM))

    # ---- 行内控件（09-27：一级直达） --------------------------------
    def _make_control(self, key):
        """行右侧控件。数据工具 = 「运行」按钮（多入口的卡表给两个）。"""
        if key == 's_lang':
            return self._make_combo(
                [(k, theme.LANG_NAMES[k]) for k in theme.LANGS], 'ui/lang')
        if key == 's_close':
            return self._make_combo(
                [(k, tr(l)) for k, l in theme.CLOSE_ACTIONS],
                'ui/close_action')
        if key == 's_autostart':
            return self._make_autostart_checkbox()
        if key == 's_autoshow':
            box = QCheckBox()
            box.setChecked(str(settings().value('ui/auto_show_floating', '1'))
                           not in ('0', 'false'))
            box.setFont(widgets.font(theme.RAIL_F_ITEM))
            box.clicked.connect(
                lambda on: settings().setValue('ui/auto_show_floating',
                                               '1' if on else '0'))
            return box
        if key == 's_restart':
            btn = QPushButton(tr('重启界面'))
            btn.setFont(widgets.font(theme.RAIL_F_ITEM))
            btn.setFixedHeight(btn.fontMetrics().height() + 12)
            btn.clicked.connect(self._do_restart)
            return btn
        cfg = SCRIPT_COMMANDS.get(key)
        if cfg:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(theme.S_XS)
            for label, args in cfg.get('runs') or []:
                btn = QPushButton(tr(label))
                btn.setFont(widgets.font(theme.RAIL_F_ITEM))
                btn.setFixedHeight(btn.fontMetrics().height() + 12)
                btn.clicked.connect(
                    lambda _c=False, k=key, a=args: self._run_tool(k, a))
                h.addWidget(btn)
                self._tool_btns.append(btn)
            return box
        return None

    def _make_combo(self, options, qkey):
        combo = QComboBox()
        cur = str(settings().value(qkey) or '')
        keys = [k for k, _l in options]
        if cur not in keys:
            cur = keys[0]
        for k, label in options:
            combo.addItem(label, k)
        combo.setCurrentIndex(keys.index(cur))         # ★ 先设初值
        combo.setFont(widgets.font(theme.RAIL_F_ITEM))
        combo.setFixedHeight(combo.fontMetrics().height() + 12)
        combo.setMinimumWidth(
            max(combo.fontMetrics().horizontalAdvance(l)
                for _k, l in options) + 56)
        combo.activated.connect(                       # ★ 只在用户操作时发
            lambda idx, q=qkey: self._save(q, combo.itemData(idx)))
        return combo

    def _make_autostart_checkbox(self):
        on = _autostart_state()
        box = QCheckBox()
        box.setChecked(on)                             # ★ 先设初值
        box.setFont(widgets.font(theme.RAIL_F_ITEM))
        box.clicked.connect(self._toggle_autostart)    # ★ 后接信号（clicked 语义）
        return box

    # ---- 行为 -------------------------------------------------------
    def _save(self, qkey, value):
        settings().setValue(qkey, value)
        if qkey == 'ui/lang':
            # 语言缓存在进程内，必须 reset 才会重新读设置（审查 P0-2）；
            # 文案是构造期烘进控件的 —— app 侧收到回调就重建两个窗口 = 实时切换
            # （2026-10-04 用户口径，推翻「重启生效」）。
            from ui.i18n import reset_lang
            reset_lang()
            if callable(self._on_lang_changed):
                self._on_lang_changed()
            else:
                self._status.setText(tr('已保存。重启界面后全部生效'
                                        '（设置 → 重启界面）。'))
        else:
            self._status.setText(tr('已保存。'))

    def _toggle_autostart(self, on):
        box = self.sender()
        ok = True
        if callable(self._on_set_autostart):
            try:
                ok = bool(self._on_set_autostart(bool(on)))
            except Exception:
                ok = False
        else:
            self._status.setText(tr('（独立模式：这里会写入「启动」目录）'))
            return
        if not ok:
            box.setChecked(not on)                     # 回滚显示
            self._status.setText(tr('设置失败 —— 已回滚'))
            return
        self._status.setText(tr('已保存。'))

    def _do_restart(self):
        if not callable(self._on_restart):
            self._status.setText(tr('（独立模式：这里会重启界面）'))
            return
        self._status.setText(tr('正在重启界面…'))
        self._on_restart()

    # ---- 数据工具（页底日志区） --------------------------------------
    def _run_tool(self, key, args):
        if self._runner is not None and self._runner.running():
            self._status.setText(tr('有脚本在跑 —— 等它结束再点（输出在下方日志区）'))
            return
        cfg = SCRIPT_COMMANDS.get(key) or {}
        script = cfg.get('script')
        if not script:
            return
        for b in self._tool_btns:
            b.setEnabled(False)                        # 互斥：跑时全禁
        self._log.appendPlainText('--- %s ---' % tr(cfg.get('title') or key))
        runner = self._runner_factory(script, list(args or []))
        runner.line.connect(self._log.appendPlainText)
        runner.done.connect(lambda code, k=key: self._on_tool_done(k, code))
        self._runner = runner
        self._status.setText(tr('%s：运行中…') % tr(cfg.get('title') or key))
        runner.start()

    def _on_tool_done(self, key, code):
        self._runner = None
        for b in self._tool_btns:
            b.setEnabled(True)
        cfg = SCRIPT_COMMANDS.get(key) or {}
        self._status.setText(tr('%s：%s') % (tr(cfg.get('title') or key),
                                             tr(exit_note(cfg.get('script'), code))))
        if code == 0:
            #: ★ 参照库刷新完必须**清进程内缓存**（2026-10-10 修的真 bug）：
            #:   `deck_guess.load_reference()` 把 JSON 永久缓存，不清的话
            #:   「卡组构成」页与选择器的 M5 建议**永远按旧快照算** ——
            #:   这个按钮就等于白加。
            if key == 's_refdecks':
                from core import ui_state
                ui_state.clear_reference_cache()
            self.reload_hint()                         # 跑完了 → 提醒数据已更新

    def reload_hint(self):
        self._status.setText(self._status.text() +
                             tr('（数据已更新 —— 相关页面重开看板后刷新）'))


def _fixed(text: str, width: int, size: int, color: str = None) -> QLabel:
    """定宽的一格文字（列对齐靠它；改列宽只改这一处调用）。

    ⚠️ **水平居中**（用户 2026-09-18 第四轮：「表头和列应该居中对齐」）——
    表头也用同一套对齐，两者才是"一根中轴"。要用左对齐就显式传 `align`。
    """
    lab = _lab(text, 'body', size=size, align=Qt.AlignCenter)
    lab.setFixedWidth(int(width))
    if color:
        lab.setStyleSheet('color: %s; background: transparent;' % color)
    return lab


def _block(title: str, note: str = '', extra=None):
    """区块外壳：标题 + 可选说明 + 可选附加控件 + 内容列。返回 `(box, 内容布局)`。

    ``extra``：塞进标题行右端的控件（如「战绩总览」的模式筛选按钮），
    在 `addStretch` **之前** —— 紧跟说明文字，不被拉伸。
    """
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.S_SM)
    head = QWidget()
    hl = QHBoxLayout(head)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(theme.S_SM)
    hl.addWidget(_lab(title, 'title', size=theme.PAGE_F_TITLE,
                      weight=QFont.Weight.Medium))
    if note:
        hl.addWidget(_lab(note, 'label', size=theme.PAGE_F_NOTE))
    if extra is not None:
        hl.addWidget(extra)
    hl.addStretch(1)
    lay.addWidget(head)
    return box, lay


#: 「对局记录」的列宽表 —— **表头与数据行都读它**（唯一来源，见 `MatchRow` / `MatchHeader`）
#: ⚠️ 列宽要**按真实视口算**。窗口最小 1200，主区 = `1200 − 左栏 − 边框 2 − 滚动条 10`。
#: * 用户 2026-09-18 拍板：**右边不够就缩左栏** → `theme.RAIL_W = 180` → 视口 **1008**；
#: * 行内可用宽的**真实账**：视口 1008 − 页面内边距 32 − 组框内边距 24 = 952，
#:   再减行内边距(4×2) 与列间距(9×`MATCH_GAP`=72) → **列宽预算 872**，当前合计 860（余 12）。
#: ★ 2026-09-18 **第二轮**（用户：「硬币列没显示，列全错位，日志列太宽 —— 把列宽均衡一下」）：
#:   ① 每列元素宽度**必须等于这里的列宽** —— 旧代码给徽章和日志按钮写 `- 8`
#:      （想"紧凑一点"），于是**每行比表头少 16px，整行往左错位**，最后几列被挤出视口
#:      （那就是"硬币列没显示"的真正原因：它是唯一还能被压缩的控件，被压到 0）。
#:   ② 新增**模式列**。
#: ★ 2026-09-18 **第三轮**（用户：「模式列移到日志右边」「段位改回 Platinum 1」）：
#:   ③ 列顺序改为 …… 对手卡组 · 日志 · **模式**（模式挪到最后一列）；
#:   ④ 段位格**改回完整段位名**（`[图]Platinum 1`，升降段带箭头）→ 列宽 84 → **108**
#:      （图标 18 + 间距 4 + `Platinum 1`@14px 实测 67 + 箭头 16 = 105 ≤ 108）；
#:   ⑤ 省出来的宽度给「对手」与「对手卡组」（都回到 168）。
#: ★ 2026-09-18 **第五轮**（用户：「把对战列的表头改成对手，下面的对战xxx去掉对战两字」+
#:   「把对手卡组移到段位右边，这一列单独向左对齐」+「卡组做成和日志类似的按钮效果」）：
#:   ⑥ 表头 `对战` → **`对手`**；行里删掉「对战」二字（只留对手名，居中）；
#:   ⑦ **对手卡组列挪到段位右边**（…… 段位 · 对手卡组 · 日期 ……），
#:      并且**这一列是唯一左对齐的**（其余列居中）；
#:   ⑧ 对手卡组格做成**按钮**（`SURFACE2` 底 + 发丝边框 + 悬停变亮，高 26 = 日志按钮同高）。
#: 改列宽**只改这张表**；改完必须看 1200 宽下的渲染图（`tools/render_ui_demo.py`，自检用）。
#: 总览五格的**宽度权重**（`QHBoxLayout` 的 stretch）。
#: ★ 为什么不等宽：四格要塞「32px 主值 + 14px 括号」= **实测 155px**；
#: 「最高段位」要塞「40px 图标 + `Platinum 1`（28px）」= **实测 186px**。
#: 平均分（每格 185.6、格内可用 161.6）会让最高段位那格差 20px 放不下 ——
#: 所以按**内容需求**给权重：四格 179（格内可用 155）、最高段位 212（格内可用 188），
#: 合计 `4×179 + 212 = 928` ✓（正是五格加四个 12px 间距后的可画宽）。
#: ⚠️ 这两个数是**量出来的**，量法见 `tmp/measure_text.py`；改字号就要重量一遍。
#: 兜底：`_metric_line(avail=…)` 与 `_best_rank_card()` 都会按实测宽度**自动降档**，
#: 换机器 / 换字体 / 换 DPI 也不会撑破格子。
OVERVIEW_W = 179
OVERVIEW_W_RANK = 212
#: 格内可用宽 = 格宽 − 卡片内边距（`theme.S_MD` × 2）
OVERVIEW_PAD = theme.S_MD * 2

#: 热力图**每行放几张月卡**。预算：可画宽 976（见上），一张月卡 =
#: `7 × 26 + 6 × 3 = 200`，4 张 + 3 个 12px 列距 = **836 ≤ 976** ✓（5 张要 1048，放不下）。
HEAT_PER_ROW = 4

MATCH_GAP = 8                      # 逐局行的列间距（比 S_MD 紧，给列宽让路）
#: ★ 2026-09-20 用户：「去除日志列，不添加这个功能了」→ 10 列改 9 列；
#:   「钻石5升段显示不全」→ 段位列 108 → 132（图标 + 全名 + 升降箭头）。
STATS_COLS = (
    ('胜负', 56), ('先/后', 92), ('对手', 168), ('回合', 66), ('段位', 132),
    ('对手卡组', 168), ('日期', 44), ('时间', 44), ('模式', 112),
)


def _metric_line(main: str, suffix: str = '', color: str = theme.INK,
                 avail: Optional[int] = None) -> QWidget:
    """一格里的「**一行数据**」：`53(30-23)` / `72.0%(18-7)` / `56.6%`。

    ★ 用户 2026-09-18：「2 行变 1 行数据」「**字体加粗居中**」→
    主值 `PAGE_F_BIG` **加粗**、括号 `PAGE_F_SUB` 次要灰**贴底对齐**，整体水平居中。
    （`suffix` 为空就只画主值 —— 胜率那格就是这样。）

    ``avail`` = 这一格的**可用宽度**（px）。给了它就按 `QFontMetrics` **实测降档**
    主值字号，直到「主值 + 括号」塞得进去 —— 这样字号可以贴着上限给
    （用户第二轮：「字体太小，**填满这个框**」），换机器 / 换字体也不会撑破格子。
    """
    size = theme.PAGE_F_BIG
    if avail and suffix:
        sub_w = QFontMetrics(widgets.font(theme.PAGE_F_SUB, QFont.Weight.Bold,
                                          tabular=True)).horizontalAdvance(suffix)
        while size > theme.PAGE_F_OVER_TITLE:
            got = QFontMetrics(widgets.font(size, QFont.Weight.Bold,
                                            tabular=True)).horizontalAdvance(main)
            if got + sub_w <= int(avail):
                break
            size -= 1
    host = QWidget()
    hl = QHBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(0)
    hl.addStretch(1)
    big = widgets.metric(main, size=size, color=color)
    big.setFont(widgets.font(size, QFont.Weight.Bold, tabular=True))
    big.setFixedHeight(big.fontMetrics().height() + 2)
    hl.addWidget(big)
    if suffix:
        #: ⚠️ 这里**不能**用 `_fixed()`：它先 setFont 再 setStyleSheet
        #: （本机 Qt 实测没丢字号，但 DESIGN.md §11 那条纪律是"样式表在前"）
        sub = QLabel(suffix)
        sub.setProperty('role', 'label')
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet('color: %s; background: transparent;' % theme.INK_MUTED)
        sub.setFont(widgets.font(theme.PAGE_F_SUB, QFont.Weight.Bold, tabular=True))
        sub.setFixedHeight(sub.fontMetrics().height() + 2)
        hl.addWidget(sub, 0, Qt.AlignBottom)
    hl.addStretch(1)
    host.setFixedHeight(big.height())
    return host


def _elide(text: str, width: int, size: int) -> str:
    """超宽就截断加省略号（**名字列不能撑破列**）。

    `QLabel` 不会自己省略 —— 得用 `QFontMetrics.elidedText` 预先算；
    调用方同时把**全文塞进 tooltip**，信息不丢。
    """
    fm = QFontMetrics(widgets.font(size))
    return fm.elidedText(str(text or ''), Qt.ElideRight, int(width))


def _spacer(width: int) -> QWidget:
    w = QWidget()
    w.setFixedWidth(int(width))
    return w


class ResultBadge(QLabel):
    """胜负徽章：**语义色前景 + soft 底**（与悬浮窗 `ResultChip` 同一套 token）。

    用户 2026-09-18：要「和悬浮窗一样的有背景色」；名字仍用**完整中文**
    （`胜利` / `失败`）—— 悬浮窗那格窄，这里是表格，宽一点更好读。
    """

    #: ★ 2026-09-18 晚：这一格**可点** —— 秒投/掉线局采集侧拿不到胜负
    #: （任务书 Bug 2），点它发 `clicked` → `MatchRow.result_pick` → app 弹菜单。
    clicked = Signal()

    def __init__(self, result, parent=None):
        text = {'Win': tr('胜利'), 'Lose': tr('失败'),
                'Draw': tr('平局')}.get(result or '', '—')
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        #: ⚠️ 宽度**等于列宽**（不是 `- 8`）—— 少给 8px 整行就往左错 8px（见 STATS_COLS 注释）
        self.setFixedSize(STATS_COLS[0][1], theme.PAGE_ROW_H - 6)
        self.setFont(widgets.font(theme.PAGE_F_BODY, QFont.Weight.Medium))
        self.setStyleSheet('color: %s; background: %s; border-radius: %dpx;'
                           % (theme.result_fg(result),
                              theme.result_soft(result), theme.R_XS))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr('点击修正这一局的胜负（人工修正优先，不会被自动解析覆盖）'))

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        if ev.button() == Qt.LeftButton and self.rect().contains(
                ev.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(ev)


class CoinLabel(QLabel):
    """先后攻那格：**硬币图 + 文字**（先攻 = 正面金底 / 后攻 = 背面银边）。

    硬币素材与悬浮窗同一份（`data/coin_icons/`，解包 duelcointoss，进版本库）；
    图读不到就只显示文字（**不吞信息**）。
    """

    def __init__(self, went_first, parent=None):
        text = (tr('先手') if went_first
                else (tr('后手') if went_first is not None else '—'))
        super().__init__('', parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.S_XS)
        #: ★ 左对齐（2026-10-04 用户：居中时硬币随文字长短漂，不成列）
        if went_first is not None:
            path = COIN_FRONT if went_first else COIN_BACK
            pix = widgets.round_pixmap(path, COIN_SIZE, radius=COIN_SIZE // 2)
            if not pix.isNull():
                icon = QLabel()
                icon.setPixmap(pix)
                icon.setFixedSize(COIN_SIZE, COIN_SIZE)
                icon.setToolTip(tr('先攻') if went_first else tr('后攻'))
                lay.addWidget(icon)
        lab = _lab(text, 'body', size=theme.PAGE_F_BODY)
        lay.addWidget(lab)
        lay.addStretch(1)                   # 首尾各一条 → 内容居中
        #: ⚠️ **必须定宽**：不然它是整行里唯一能压缩的控件，列宽一紧张就被压成 0，
        #: 硬币和文字一起消失（用户 2026-09-18：「硬币列没显示」的半个原因）。
        self.setFixedWidth(STATS_COLS[1][1])


class MatchHeader(QWidget):
    """逐局行的**表头**（用户 2026-09-18：「你的对局统计没有表头」）。

    列宽与 `MatchRow` 共用 :data:`STATS_COLS` —— 表头与数据永远对齐。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
        lay.setSpacing(MATCH_GAP)
        for name, width in STATS_COLS:
            lab = _lab(tr(name), 'label', size=theme.PAGE_F_SMALL)
            lab.setFixedWidth(int(width))
            #: ★ 用户 2026-09-18 第四轮：「表头和列应该居中对齐」—— 表头与列内容都居中
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet('color: %s; background: transparent;'
                              % theme.INK_SUBTLE)
            lay.addWidget(lab)
        lay.addStretch(1)
        self.setFixedHeight(theme.PAGE_HEAD_H)


class MatchRow(QWidget):
    """逐局行：**9 列**（胜负 · 先/后 · 对战<名> · N 回合 · 段位 · 模式 · 日期 · 时间 · 对手卡组；日志列 2026-09-20 移除）。"""

    #: 点了胜负徽章 → (row dict, 被点的徽章)（app 弹修正菜单，任务书 Bug 2）
    result_pick = Signal(dict, object)

    def __init__(self, row: dict, on_pick, parent=None):
        super().__init__(parent)
        self.row = dict(row or {})
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_XS, 2, theme.S_XS, 2)
        lay.setSpacing(MATCH_GAP)

        self.result_badge = ResultBadge(self.row.get('result'))
        self.result_badge.clicked.connect(
            lambda: self.result_pick.emit(self.row, self.result_badge))
        lay.addWidget(self.result_badge)
        lay.addWidget(CoinLabel(self.row.get('went_first')))

        #: ★ 2026-09-18 五轮：**去掉「对战」二字**，只留对手名（居中）
        #: （用户：「下面的对战xxx去掉对战两字只保留对手名字，对手名字居中」）
        name = self.row.get('opp_player') or tr('未知')
        versus = QWidget()
        vl = QHBoxLayout(versus)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addStretch(1)
        #: 名字按列宽省略（QFontMetrics），全文进 tooltip —— 长名字不撑破列
        lab = _lab(_elide(name, STATS_COLS[2][1] - 8, theme.PAGE_F_BODY), 'body',
                   size=theme.PAGE_F_BODY)
        lab.setToolTip(name)
        vl.addWidget(lab)
        vl.addStretch(1)
        versus.setFixedWidth(STATS_COLS[2][1])
        lay.addWidget(versus)

        turns = self.row.get('total_turns')
        lay.addWidget(_fixed((tr('%s 回合') % turns) if turns else '—',
                             STATS_COLS[3][1], theme.PAGE_F_BODY))
        lay.addWidget(self._rank_cell())

        #: ★ 五轮：「对手卡组」列挪到**段位右边**（这一列单独左对齐，见 `OppDeckChip`）
        chip = OppDeckChip(self.row)
        chip.clicked.connect(lambda: on_pick(self.row, chip))
        lay.addWidget(chip)

        lay.addWidget(_fixed(self.row.get('date') or '—', STATS_COLS[6][1],
                             theme.PAGE_F_SMALL, theme.INK_MUTED))
        lay.addWidget(_fixed(self.row.get('time') or '—', STATS_COLS[7][1],
                             theme.PAGE_F_SMALL, theme.INK_MUTED))

        lay.addWidget(_fixed(self.row.get('mode_label') or '—', STATS_COLS[8][1],
                             theme.PAGE_F_BODY, theme.INK_MUTED))
        lay.addStretch(1)

    def _rank_cell(self) -> QWidget:
        """我方段位：**图标 + 完整段位名（± 升降箭头）**：`[图]Platinum 1`。

        ★ 用户 2026-09-18 第三轮：「**段位改回 Platinum 1**」——
        上一轮照悬浮窗画成了 `[图]II ↑`（罗马数字），这次改回**全名**，列宽 84 → 108。
        ⚠️ 名字超宽用 `_elide`（换机器 / 换字体时不许撑破列）。

        ⚠️ **非天梯局这一格是空的**（判据在 `core.ui_state._page_row`，界面不自己判模式）：
        用户原话「**为什么你的最近对局在活动局也显示了段位？**」——
        活动局那个 `my_rank_after` 是别的模式补回来的值，不能当真段位画。
        """
        host = QWidget()
        hl = QHBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.S_XS)
        delta = self.row.get('rank_delta')
        moving = delta in ('up', 'down')
        #: ★ 用户 2026-09-18 第四轮：**升降段局画"动之前那一档"**。
        #: `Platinum 2 ↑` = 「在 Platinum 2 升了一段」；而 `Platinum 1 ↑` 读起来是
        #: 「Platinum 1 又升一段」—— 1 已经是最高的那一小段，语义不成立（用户原话）。
        #: 平滑局（`same` / 无 delta）照旧画**当前（局后）**段位。
        if moving:
            icon = (self.row.get('rank_icon_before')
                    or self.row.get('rank_icon_after'))
            label = (self.row.get('rank_label_before')
                     or self.row.get('rank_label_after') or '')
        else:
            icon = (self.row.get('rank_icon_after')
                    or self.row.get('rank_icon_before'))
            label = (self.row.get('rank_label_after')
                     or self.row.get('rank_label_before') or '')
        # ★ 巅峰积分（2026-09-28 用户二口径）：战绩页段位列的巅峰行 =
        #   **巅峰图标 + 积分数字**（悬浮窗只显图标 —— 那半边已回退）。
        #   积分覆盖 label（天梯判据给巅峰行的 label 本来就是空），
        #   升降段那套 arrow 逻辑不碰（巅峰行永远 not moving）。
        peak = self.row.get('peak_rate')
        if peak:
            from core import modes as _M
            icon = _M.icon_path('peak')
            label = peak
        #: 有箭头时名字要让出「间距 + 箭头」的宽，否则会被挤出列外
        room = STATS_COLS[4][1] - theme.RANK_ICON_ROW - theme.S_XS
        if moving:
            room -= theme.S_XS + 12
        hl.addStretch(1)                    # ★ 居中（用户：「表头和列应该居中对齐」）
        if icon:
            hl.addWidget(widgets.thumb_label(icon, theme.RANK_ICON_ROW))
        if label:
            lab = _lab(_elide(label, room, theme.PAGE_F_SMALL), 'body',
                       size=theme.PAGE_F_SMALL)
            lab.setToolTip(label)
            hl.addWidget(lab)
        if moving:
            hl.addWidget(_lab('↑' if delta == 'up' else '↓', 'muted',
                              size=theme.PAGE_F_SMALL))
        if not icon and not label and self.row.get('rank_pending'):
            #: ★ 用户 2026-09-18 晚：「能不能做到结果和段位和其他信息一起显示」——
            #: 段位真值要等游戏写结算（~90s），做不到同帧，但把「还没到」变成可见的
            #: 获取中占位（`…`），而不是看起来像丢了。窗口（120s）过了仍空 = 真没有。
            wait = _lab('…', 'muted', size=theme.PAGE_F_SMALL)
            wait.setToolTip(tr('段位获取中…（结算写入通常需要 1~2 分钟）'))
            hl.addWidget(wait)
        hl.addStretch(1)
        host.setFixedWidth(STATS_COLS[4][1])
        if label:
            tip = label
            if delta == 'up':
                tip = tr('%s（升段）') % label
            elif delta == 'down':
                tip = tr('%s（掉段）') % label
            if peak:
                tip = '%s %s' % (tr('巅峰决斗'), peak)
            host.setToolTip(tip)
        elif self.row.get('rank_pending'):
            host.setToolTip(tr('段位获取中…（结算写入通常需要 1~2 分钟）'))
        return host


class HeatCell(QWidget):
    """热力图的一格：**底色方块 + 居中的填充块**（块大小 = 场数、颜色 = 胜率）。

    ★ 用户 2026-09-18（参考 Untapped）：「打的场数多颜色填充的就大，反之就小。
    胜率越高颜色越绿，胜率越低颜色越红」—— **两根轴各管一件事**：
    块的大小说"打了多少"、颜色说"赢了多少"。

    ⚠️ 为什么自绘而不是叠两个 QLabel：填充块要按比例**横竖同缩并居中**
    （26×20 的格里画 11~26px 的块），QLabel 排不出这个效果。
    ⚠️ 0 场的格子**只画底色、一点语义色都不染** —— 染了会被读成"那天赢过"
    （同 `theme.heat_color` 那条注释的教训）。
    """

    def __init__(self, cell=None, n_max: int = 1, parent=None):
        super().__init__(parent)
        self.setFixedSize(theme.HEAT_W, theme.HEAT_H)
        self._fill = None
        if cell:
            n = int(cell.get('n') or 0)
            self._fill = theme.heat_cell(n, cell.get('w'), cell.get('l'), n_max)
            #: 图例那几个格子**没有 date**（不是真日期）→ 不挂 tooltip
            if n and cell.get('date'):
                self.setToolTip(tr('%s · %d 场（%d 胜 %d 负）')
                                % (cell.get('date'), n,
                                   int(cell.get('w') or 0),
                                   int(cell.get('l') or 0)))

    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        w, h = float(self.width()), float(self.height())
        p.setBrush(QColor(theme.SURFACE2))
        p.drawRoundedRect(QRectF(0, 0, w, h), 3, 3)
        if self._fill is not None:
            color, ratio = self._fill
            fw, fh = w * ratio, h * ratio
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF((w - fw) / 2.0, (h - fh) / 2.0, fw, fh),
                              2, 2)


class MonthHeatCard(QWidget):
    """一张月卡：月份 + **周 × 星期**格子阵（复用 `ui_state.month_matrix()`，界面不重算日历）。

    ★ 可点（2026-10-01 用户：「点热力图切换月份查看」）—— 点它发 `clicked(月份)`，
    总览五格 + 对局记录跟着切到那个月；选中的月卡挂焦点环
    （`theme.HAIRLINE_FOCUS`，与「tab 选中 / 焦点」同一色，ACCENT 只许那三处用）。
    """

    clicked = Signal(str)

    def __init__(self, month: str, grid, n_max: int = 1, selected=False, parent=None):
        super().__init__(parent)
        self._month = month
        self._selected = bool(selected)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)      # QSS 的 `:hover` 需要它
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr('点开 %s 的战绩') % month)
        #: ⚠️ 间距/内边距按 Untapped 收窄（2026-10-03 用户：月份间距太大）——
        #: 卡间 4px（grid 侧也收了）、卡内 2px，卡片几乎贴着。
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(theme.S_XS)
        lay.addWidget(_lab(month, 'label', size=theme.PAGE_F_SMALL))
        body = QGridLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.HEAT_GAP)
        self.cells = []
        for r, week in enumerate(grid or []):
            for i, cell in enumerate(week or []):
                item = HeatCell(cell, n_max)
                body.addWidget(item, r, i)
                self.cells.append(item)
        lay.addLayout(body)
        lay.addStretch(1)
        self._apply_style()

    def _apply_style(self) -> None:
        """底色/边框三态：普通透明 → 悬停发丝线 → 选中焦点环。"""
        if self._selected:
            self.setStyleSheet(
                'MonthHeatCard { background: %s; border: 1px solid %s;'
                ' border-radius: %dpx; }'
                % (theme.SURFACE1, theme.HAIRLINE_FOCUS, theme.R_MD))
        else:
            self.setStyleSheet(
                'MonthHeatCard { background: transparent;'
                ' border: 1px solid transparent; border-radius: %dpx; }'
                'MonthHeatCard:hover { border: 1px solid %s; }'
                % (theme.R_MD, theme.HAIRLINE_STRONG))

    def set_selected(self, selected: bool) -> None:
        """选中态原地切换（快路径用，整卡不重建）。"""
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def mousePressEvent(self, ev) -> None:                  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self._month)
        super().mousePressEvent(ev)


def _fingerprint(data) -> str:
    """页面数据的指纹 —— reload 的「没变就不重建」闸门用（2026-10-11）。

    🔴 用户实测：「卡组选择器点确认后会卡住几秒」。根因 = 点确认 ->
    `app.refresh()` -> `reload()` 把 8 个页**全量重建**（战绩页 53 行 ×
    8 列控件 + 禁限表 130 张整卡图 + Tier 103 条芯片），全挤在同一帧。
    数据层实测每页 <150ms，**重建才是大头**。数据没变 = 页保持原样。
    """
    import hashlib
    import json
    try:
        blob = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.md5(blob.encode('utf-8')).hexdigest()
    except Exception:                                # noqa: BLE001
        # 不可序列化就退回 id() —— 等于「每次都当变了」（与旧行为一致，别更糟）
        return 'id:%s' % id(data)


class OppDeckChip(QWidget):
    """逐局行那格：对手卡组 —— **按钮样**（可点 → 标注）。缺 = 琥珀 `!` + 「未知卡组」。

    ★ 用户 2026-09-18 第五轮：「**卡组需要做成和日志类似的按钮效果，可以更直观的知道
    可以点击**」+「**这一列单独向左对齐**」——
    所以这里给了「`SURFACE2` 底 + 发丝边框 + 圆角」的按钮外观（悬停时边框变亮），
    高度与「日志」按钮一致（`PAGE_ROW_H − 6`）；**整行只有这一列是靠左的**。
    """

    clicked = Signal()

    #: 格内卡图边长 —— 按钮高 26，减去边框 2 → 24，图取 22 才留得住呼吸
    THUMB_IN_BUTTON = 22

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)      # QSS 的 `:hover` 需要它
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            'OppDeckChip { background: %s; border: 1px solid %s;'
            ' border-radius: %dpx; }'
            'OppDeckChip:hover { border: 1px solid %s; }'
            % (theme.SURFACE2, theme.HAIRLINE, theme.R_MD,
               theme.HAIRLINE_STRONG))
        # 🔴 2026-10-11 用户口径：「应该和悬浮窗是相同显示」—— 未标注但有
        # M5 反推建议的行，显示**建议的卡组名/图**（同悬浮窗），保留琥珀 `!`
        # 未确认态；真标注了才用标注值。
        name = row.get('opp_deck') or row.get('suggest_name') or ''
        img = row.get('opp_deck_img') or row.get('suggest_img')
        known = bool(row.get('opp_deck'))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
        lay.setSpacing(theme.S_XS)
        thumb = self.THUMB_IN_BUTTON
        lay.addWidget(widgets.thumb_label(img if img else '', thumb,
                                          radius=theme.R_XS))
        show = name or tr('未知卡组')
        room = (STATS_COLS[5][1] - thumb - theme.S_XS * 3
                - (0 if known else 14))          # 未标注还要给 `!` 留位
        lab = _lab(_elide(show, room, theme.PAGE_F_BODY), 'body',
                   size=theme.PAGE_F_BODY)
        lab.setStyleSheet('color: %s; background: transparent;'
                          % (theme.INK if known else theme.WARN))
        lab.setToolTip(show)
        lay.addWidget(lab)
        if not known:
            bang = _lab('!', 'label', size=theme.PAGE_F_BODY)
            bang.setStyleSheet('color: %s; background: transparent;' % theme.WARN)
            lay.addWidget(bang)
        lay.addStretch(1)                         # ★ **左对齐**（用户口径）
        self.setFixedWidth(STATS_COLS[5][1])
        self.setFixedHeight(theme.PAGE_ROW_H - 6)
        self.setToolTip(tr('点一下标注对手卡组') if not known else tr('点一下改标'))

    def mouseReleaseEvent(self, ev):                     # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            ev.accept()


class DeckGroupBox(QWidget):
    """一轮里的一副卡组：**组头**（卡组图 + 名 + 对局数 + 先后攻 + 胜率 + 先手 + 后手）+ 表头 + 逐局行。"""

    def __init__(self, name: str, st: dict, rows, on_pick, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet('DeckGroupBox { background: %s; border: 1px solid %s;'
                           ' border-radius: %dpx; }'
                           % (theme.SURFACE1, theme.HAIRLINE, theme.R_MD))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_MD, theme.S_MD, theme.S_MD, theme.S_MD)
        lay.setSpacing(theme.S_XS)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.S_MD)
        hl.addWidget(widgets.thumb_label(st.get('img') or '', theme.THUMB))
        hl.addWidget(_lab(name, 'title', size=theme.PAGE_F_TITLE,
                          weight=QFont.Weight.Medium))
        #: 轮次标签不再渲染（2026-09-20 用户拍板；数据层 round_label 保留）
        n = int(st.get('n_total') or 0)
        w, l = int(st.get('n_win') or 0), int(st.get('n_lose') or 0)
        f_n, s_n = int(st.get('first_n') or 0), int(st.get('second_n') or 0)
        items = [
            (tr('对局数'), theme.fmt_record(w, l), theme.INK),
            (tr('先后攻'), '%d-%d' % (f_n, s_n), theme.INK_MUTED),
            (tr('胜率'), theme.fmt_pct(st.get('win_rate')),
             theme.win_rate_color(st.get('win_rate'))),
            (tr('先手'), theme.fmt_pct(st.get('first_rate')),
             theme.win_rate_color(st.get('first_rate'))),
            (tr('后手'), theme.fmt_pct(st.get('second_rate')),
             theme.win_rate_color(st.get('second_rate'))),
        ]
        for label, value, color in items:
            hl.addWidget(_lab(label, 'label', size=theme.PAGE_F_SMALL))
            lab = _lab(value, 'body', size=theme.PAGE_F_BODY)
            lab.setStyleSheet('color: %s; background: transparent;' % color)
            hl.addWidget(lab)
        hl.addStretch(1)
        lay.addWidget(head)
        lay.addWidget(widgets.hairline())
        lay.addWidget(MatchHeader())
        lay.addWidget(widgets.hairline())

        for r in rows:
            lay.addWidget(MatchRow(r, on_pick))


class StatsPage(QWidget):
    """「战绩」页（**D1**）：① 月历热力图 ② 战绩总览五格 ③ 对局记录（**本轮**，按我方卡组分组）。

    ★ **2026-09-18 用户口径（本轮修订）**：

    * **对局记录只算本轮**（「不统计上一轮的对局」）—— 一轮里用同一副卡组打的局放一组；
      换卡组的局另起一组（例：今天 10 盘里 4 盘闪刀姬 + 6 盘 K9vs → **两组**）；
    * 组与行都**按时间倒序**：最上面 = 最近打的那局，最下面 = 最早那局；
    * 逐局行带**表头**；胜负 = **带 soft 底色的徽章**（同悬浮窗）；先后攻 = **硬币图 + 文字**；
    * 整页字号**统一放大**（`theme.PAGE_F_*`）。

    数据由 `core.ui_state.stats_page_data()` 备好（**窗口只画、不查库**）。
    """

    pick_deck = Signal(dict, object)          # (行 dict, 被点的控件)
    result_pick = Signal(dict, object)        # (行 dict, 被点的徽章) —— 手动修正胜负
    #: 模式筛选变了（用户 2026-09-18 七轮：参考 Untapped 的模式下拉）——
    #: ``None`` = 全部；其余是 `core.storage` 的 MODE_GROUPS key
    mode_selected = Signal(object)
    #: 热力图月卡被点（2026-10-01）—— 携带 ``'YYYY-MM'``，整页切到那个月
    month_selected = Signal(str)

    #: 下拉清单（**顺序即菜单顺序**）；名字唯一来源 = `storage.MODE_GROUP_NAMES`
    #: ⚠️ 单人模式不进筛选（2026-10-03 用户）—— 单人局本来就不进口径
    MODE_FILTERS = ((None, '全部模式'),) + tuple(
        (k, v) for k, v in MODE_GROUP_NAMES.items() if k != 'solo')

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('rowsScroll')       # 无边框（复用 theme.qss 规则）
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.host.setAttribute(Qt.WA_StyledBackground, True)
        self.host.setStyleSheet('background: %s;' % theme.CANVAS)
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(theme.S_LG, theme.S_MD, theme.S_LG, theme.S_LG)
        self.col.setSpacing(theme.S_XL)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

    # ------------------------------------------------------------ 数据
    def set_data(self, data: dict) -> None:
        data = dict(data or {})
        old = getattr(self, '_data', None)
        if self._can_month_fast_path(old, data):
            self._data = data
            self._refresh_month_view()
            return
        self._data = data
        self._rebuild()

    def _can_month_fast_path(self, old, data) -> bool:
        """切月/换模式筛选的**快路径**判据（2026-10-03 卡顿修复刀②）。

        热力图是「全部历史」的日历视图（daily/months 不随切月/模式变）——
        输入没变就**整块不重建**：月卡高亮原地挪、只重画「总览+记录」两块。
        热力图输入变了（新的一天有局/新月份）才走全量重建。
        """
        if not getattr(self, '_heat_cards', None) or not getattr(self, 'blocks', None):
            return False
        if old is None:
            return False
        if (data.get('daily') != old.get('daily')
                or data.get('months') != old.get('months')):
            return False
        return data != old            # 完全没变化：按老规矩整页重画（幂等）

    def _refresh_month_view(self) -> None:
        """快路径的重画：高亮原地挪 + 摘掉总览/记录两块重挂（热力图不动）。"""
        d = self._data
        for month, card in self._heat_cards.items():
            card.set_selected(month == d.get('month'))
        while self.col.count() > 1:            # 第 0 个 = 热力图块，保留
            item = self.col.takeAt(1)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.blocks['overview'] = self._block_overview(
            d.get('overview') or {}, d.get('best_rank'))
        self.blocks['records'] = self._block_records(
            list(d.get('groups') or []), bool(d.get('session_live')))
        for w in (self.blocks['overview'], self.blocks['records']):
            self.col.addWidget(w)
        self.col.addStretch(1)
        for w in self.host.findChildren(QWidget):
            w.setMouseTracking(True)

    def _clear(self):
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        d = self._data
        if getattr(self, '_mode_btn', None) is not None:
            self._mode_btn.setText('%s ▾' % self._mode_label(d.get('mode_group')))
        self._heat_cards = {}
        self.blocks = {
            'heat': self._block_heat(d.get('daily') or {}, list(d.get('months') or []),
                                     d.get('month')),
            'overview': self._block_overview(d.get('overview') or {}, d.get('best_rank')),
            'records': self._block_records(list(d.get('groups') or []),
                                           bool(d.get('session_live'))),
        }
        for w in self.blocks.values():
            self.col.addWidget(w)
        self.col.addStretch(1)
        for w in self.host.findChildren(QWidget):
            w.setMouseTracking(True)

    # ------------------------------------------------- 模式筛选（七轮）
    def _mode_label(self, key) -> str:
        """模式 key → 显示名（``None`` = 全部模式）。"""
        for k, name in self.MODE_FILTERS:
            if k == key:
                return tr(name)
        return tr('全部模式')

    def _mode_filter_button(self) -> QPushButton:
        """「全部模式 ▾」按钮 —— Untapped 同款：文字 + 下拉箭头，点开单选菜单。"""
        btn = QPushButton('%s ▾' % self._mode_label(self._data.get('mode_group')))
        btn.setObjectName('modeFilter')
        btn.setFont(widgets.font(theme.PAGE_F_SMALL))
        btn.setFixedHeight(btn.fontMetrics().height() + 10)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._show_mode_menu(btn))
        self._mode_btn = btn
        return btn

    def _show_mode_menu(self, btn) -> None:
        """单选菜单（`QActionGroup` 互斥 + checkable，Untapped 的圆点单选同语义）。"""
        menu = QMenu(btn)
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = self._data.get('mode_group')
        for key, name in self.MODE_FILTERS:
            act = menu.addAction(tr(name))
            act.setCheckable(True)
            act.setChecked(key == current)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.mode_selected.emit(k))
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    # ------------------------------------------------------------ ① 热力图
    def _heat_legend(self) -> QWidget:
        """图例：**两根轴各画一遍**（块大小 = 场数 / 颜色 = 胜率）。

        ⚠️ 别退回"一根少→多的绿色色阶"：那是 S6 那版的画法，看的人分不清
        "绿是打得多还是赢得多"。现在的两件事分开表达，图例就得分开讲。
        """
        host = QWidget()
        hl = QHBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.S_XS)
        hl.addWidget(_lab(tr('场数 少'), 'label', size=theme.PAGE_F_SMALL))
        for n in (1, 4, 12):
            hl.addWidget(HeatCell({'n': n, 'w': n, 'l': 0}, 12))
        hl.addWidget(_lab(tr('多'), 'label', size=theme.PAGE_F_SMALL))
        hl.addSpacing(theme.S_LG)
        hl.addWidget(_lab(tr('胜率 低'), 'label', size=theme.PAGE_F_SMALL))
        for w, l in ((0, 4), (2, 2), (4, 0)):
            hl.addWidget(HeatCell({'n': 4, 'w': w, 'l': l}, 4))
        hl.addWidget(_lab(tr('高'), 'label', size=theme.PAGE_F_SMALL))
        hl.addStretch(1)
        return host

    def _block_heat(self, daily: dict, months, selected=None) -> QWidget:
        box, lay = _block(
            tr('按月'),
            '%s · %s · %s · %s' % (tr('全部历史'), tr('点月卡看该月战绩'),
                                   tr('块越大 = 那天打得越多'),
                                   tr('越绿 = 那天胜率越高')))
        if not months:
            lay.addWidget(_lab(tr('暂无数据'), 'muted', size=theme.PAGE_F_BODY))
            return box
        lay.addWidget(self._heat_legend())
        #: ⚠️ 归一化基准取**全库单日最大场次**（不是当月）—— 换月看时格子大小才可比
        n_max = max(1, theme.heat_max(daily))
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        #: ⚠️ 2026-10-03 用户（对齐 Untapped）：月份间距 S_MD -> S_XS
        grid.setHorizontalSpacing(theme.S_XS)
        grid.setVerticalSpacing(theme.S_XS)
        for i, month in enumerate(months):
            card = MonthHeatCard(month, ui_state.month_matrix(daily, month), n_max,
                                 selected=(month == selected))
            card.clicked.connect(self.month_selected.emit)
            self._heat_cards[month] = card
            grid.addWidget(card,
                           i // HEAT_PER_ROW, i % HEAT_PER_ROW,
                           Qt.AlignLeft | Qt.AlignTop)
        #: ⚠️ 必须 **AlignLeft 打包整块**（2026-10-03 用户实拍：月份间距还是
        #:    巨大）—— 只给格子 AlignLeft 不够，grid_host 本体会被拉到整行宽，
        #:    多余宽度全变成列间空气。打包后列距才是真正的 S_XS。
        lay.addWidget(grid_host, 0, Qt.AlignLeft)
        return box

    # ------------------------------------------------------------ ② 总览五格
    def _block_overview(self, st: dict, best_rank) -> QWidget:
        """五格：**对局数 / 胜率 / 先攻胜率 / 后攻胜率 / 最高段位**（全部本月口径）。

        ★ 用户 2026-09-18 的五条，逐条落在这里：

        * 文字**加粗 + 水平居中**（连标题一起）；
        * **一格一行数据**：对局数 `53(30-23)`、先攻/后攻 `72.0%(18-7)`
          —— 括号里**恒为胜-负**（与对局数同构）；
        * **胜率那格只留百分比**（原来下面还挂一行场数，用户：「下方场数不显示」）；
        * 卡名：先手 / 后手 → **先攻胜率 / 后攻胜率**；
        * 「最高段位」原来字太小 → **图标 + 段位名放大加粗居中**，撑满整格。
        """
        n_all = int(st.get('n_total') or 0)
        n_win, n_lose = int(st.get('n_win') or 0), int(st.get('n_lose') or 0)
        f_n, f_w = int(st.get('first_n') or 0), int(st.get('first_wins') or 0)
        s_n, s_w = int(st.get('second_n') or 0), int(st.get('second_wins') or 0)
        #: ⚠️ 这块是**按月**（用户 2026-09-18：「这个页面是按月统计的」）——
        #: 只有下面「对局记录」才按轮分组，两块口径不同、各标各的。
        month = self._data.get('month') or ''
        # ★ 审查 P0-4：分支只认 **key**（mode_group），不认显示串 ——
        #    显示串已被 tr，en/ja 下「!= '全部模式'」恒真。
        mode_note = ('' if self._data.get('mode_group') is None
                     else ' · %s' % self._mode_label(self._data.get('mode_group')))
        box, lay = _block(
            tr('战绩总览'),
            ('%s · %s %d %s' % (month, tr('本月'), n_all, tr('场')))
            if n_all
            else ('%s · %s' % (month, tr('本月还没打过')) if month
                  else tr('本月还没打过')),
            extra=self._mode_filter_button())
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(theme.S_MD)

        avail = OVERVIEW_W - OVERVIEW_PAD       # 四格的格内可用宽

        def cell(title, main, suffix, color):
            card = widgets.panel('dashOverviewCard', pad=theme.S_MD,
                                 radius=theme.R_MD, bg=theme.SURFACE1)
            cl = card.layout()
            cl.setSpacing(theme.S_XS)
            cl.addStretch(1)                       # 上下各一条弹性 → 内容垂直居中
            head = _lab(title, 'label', size=theme.PAGE_F_OVER_TITLE,
                        weight=QFont.Weight.Bold)
            head.setAlignment(Qt.AlignCenter)
            cl.addWidget(head)
            cl.addWidget(_metric_line(main, suffix, color, avail=avail))
            cl.addStretch(1)
            return card

        #: ⚠️ stretch 用**内容需求**的比例（不是 1:1:1:1:1），见 `OVERVIEW_W` 注释
        rl.addWidget(cell(tr('对局数'), '%d' % n_all, '(%d-%d)' % (n_win, n_lose),
                          theme.INK), OVERVIEW_W)
        rl.addWidget(cell(tr('胜率'), theme.fmt_pct(st.get('win_rate')), '',
                          theme.win_rate_color(st.get('win_rate'))), OVERVIEW_W)
        rl.addWidget(cell(tr('先攻胜率'), theme.fmt_pct(st.get('first_rate')),
                          '(%d-%d)' % (f_w, f_n - f_w),
                          theme.win_rate_color(st.get('first_rate'))), OVERVIEW_W)
        rl.addWidget(cell(tr('后攻胜率'), theme.fmt_pct(st.get('second_rate')),
                          '(%d-%d)' % (s_w, s_n - s_w),
                          theme.win_rate_color(st.get('second_rate'))), OVERVIEW_W)
        rl.addWidget(self._best_rank_card(best_rank), OVERVIEW_W_RANK)
        lay.addWidget(row)
        return box

    def _best_rank_card(self, best_rank) -> QWidget:
        """「最高段位」那格：**大图标 + 大号加粗段位名**，居中撑满整格。

        ★ 用户 2026-09-18：「最高段位的白金1字体太小，**加粗居中填充满整个框**」——
        图标从 15 放到 `theme.PAGE_RANK_ICON`(40)、文字用 `PAGE_F_BIG` 加粗；
        原来那行「（历史）」小字随口径统一（本月最高）删掉。
        """
        from core import ranks
        card = widgets.panel('dashBestRankCard', pad=theme.S_MD,
                             radius=theme.R_MD, bg=theme.SURFACE1)
        cl = card.layout()
        cl.setSpacing(theme.S_XS)
        icon = ranks.icon_path((best_rank or (None,))[0]) if best_rank else None
        text = ranks.rank_label(*(best_rank or (None, None))) if best_rank else '-'
        cl.addStretch(1)
        head = _lab(tr('最高段位'), 'label', size=theme.PAGE_F_OVER_TITLE,
                    weight=QFont.Weight.Bold)
        head.setAlignment(Qt.AlignCenter)
        cl.addWidget(head)
        #: 名字字号按实测宽度自适应（图标 + 间距先扣掉）—— 见 `_metric_line` 那条注释
        size = theme.PAGE_F_OVER_RANK
        room = (OVERVIEW_W_RANK - OVERVIEW_PAD
                - (theme.PAGE_RANK_ICON + 6 if icon else 0))
        while size > theme.PAGE_F_OVER_TITLE:
            if QFontMetrics(widgets.font(size, QFont.Weight.Bold)
                            ).horizontalAdvance(text or '-') <= room:
                break
            size -= 1
        line = QWidget()
        hl = QHBoxLayout(line)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        hl.addStretch(1)
        if icon:
            hl.addWidget(widgets.thumb_label(icon, theme.PAGE_RANK_ICON))
        name = _lab(text or '-', 'title', size=size, weight=QFont.Weight.Bold)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet('color: %s; background: transparent;' % theme.INK)
        hl.addWidget(name)
        hl.addStretch(1)
        cl.addWidget(line)
        cl.addStretch(1)
        return card

    # ------------------------------------------------------------ ③ 对局记录
    def _block_records(self, groups, session_live: bool) -> QWidget:
        #: ⚠️ 数据范围 = **本月**；「按轮」说的是**分组**（同一副卡组跨轮不合并）
        # ★ 审查 P0-4：分支只认 **key**（mode_group），不认显示串 ——
        #    显示串已被 tr，en/ja 下「!= '全部模式'」恒真。
        mode_note = ('' if self._data.get('mode_group') is None
                     else ' · %s' % self._mode_label(self._data.get('mode_group')))
        box, lay = _block(
            tr('对局记录'),
            '%s · %s%s · %s'
            % (self._data.get('month') or '',
               tr('按轮次分组（同一副卡组跨轮不合并）'), mode_note,
               tr('点对手卡组格即可标注')))
        if not groups:
            last = self._data.get('last_match_at') or ''
            lay.addWidget(_lab(tr('本月还没有对局'), 'muted',
                               size=theme.PAGE_F_BODY))
            if last:
                lay.addWidget(_lab(tr('库里最后一局：') + last[:16], 'label',
                                   size=theme.PAGE_F_SMALL))
            return box
        for name, st, rows in groups:
            box_g = DeckGroupBox(name, st, rows, self.pick_deck.emit)
            for r in box_g.findChildren(MatchRow):
                        r.result_pick.connect(self.result_pick.emit)
            lay.addWidget(box_g)
        return box


class FloatingTogglePage(QWidget):
    """「悬浮窗」页（2026-10-03 用户：加一个**显示/隐藏悬浮窗**的按钮）。

    页面只发号施令（`toggle_requested`）+ 接状态（`set_floating_visible`）；
    真正的 show/hide 在 app 侧（窗口不归看板管）。
    """

    toggle_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)
        lay.addWidget(_lab(tr('悬浮窗'), 'title', size=theme.F_STAT,
                           weight=QFont.Weight.Medium))
        note = _lab(tr('游戏里打完一局自动弹结果的那个小窗。被关掉（×）后'
                       '在这里点一下就能唤回来。'), 'muted')
        note.setWordWrap(True)
        lay.addWidget(note)
        self.btn = QPushButton(tr('显示悬浮窗'))
        self.btn.setObjectName('modeFilter')
        self.btn.setFont(widgets.font(theme.PAGE_F_BODY))
        self.btn.setFixedHeight(self.btn.fontMetrics().height() + 14)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.toggle_requested.emit)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.btn)
        rl.addStretch(1)
        lay.addWidget(row)
        lay.addStretch(1)

    def set_floating_visible(self, visible: bool) -> None:
        self.btn.setText(tr('隐藏悬浮窗') if visible else tr('显示悬浮窗'))


class CardsLinksPage(QWidget):
    """「查卡」页的查卡网站清单（2026-10-03 用户：列出三个站、点开浏览器）。

    与上方「查卡输入框」互补 —— 那个是本地三语候选 + ygocdb 外链；
    这里是常用查卡站直达。
    """

    LINKS = (
        ('官网查卡（Konami 数据库）', 'https://www.db.yugioh-card.com/',
         '官方卡片数据库，效果文/ rulings 的最终口径'),
        ('百鸽查卡（ygocdb）', 'https://ygocdb.com/',
         '中文圈最全的卡查，三语名/卡图/查卡组构筑'),
        ('masterduelmeta', 'https://www.masterduelmeta.com/',
         'MD 环境站：Tier 榜 / 卡组构成 / 使用率（本项目参照库的数据源）'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.S_XL, theme.S_XL, theme.S_XL, theme.S_XL)
        lay.setSpacing(theme.S_MD)
        lay.addWidget(_lab(tr('查卡'), 'title', size=theme.F_STAT,
                           weight=QFont.Weight.Medium))
        note = _lab(tr('点链接用默认浏览器打开；上方搜索框是本地三语候选'
                       '（离线），点候选拿卡 id 跳 ygocdb。'), 'muted')
        note.setWordWrap(True)
        lay.addWidget(note)
        for title, url, desc in self.LINKS:
            link = QLabel('<a href="%s">%s</a>' % (url, tr(title)))
            link.setTextFormat(Qt.RichText)
            link.setOpenExternalLinks(True)
            link.setFont(widgets.font(theme.PAGE_F_TITLE))
            # ⚠️ 默认链接色在暗底上对比度太低（2026-10-03 用户）——
            #    用主题亮色 + 下划线
            link.setStyleSheet(
                'QLabel { color: %s; }'
                'QLabel a { color: %s; text-decoration: underline; }'
                % (theme.INK, theme.LINK if hasattr(theme, 'LINK')
                   else theme.ACCENT))
            lay.addWidget(link)
            d = _lab('%s · %s' % (url, tr(desc)), 'muted', size=theme.PAGE_F_SMALL)
            d.setWordWrap(True)
            lay.addWidget(d)
        lay.addStretch(1)

class DashboardWindow(QWidget):
    """看板主窗口。

    * `loader`（app 注入）= 取「战绩」页数据（`core.ui_state.stats_page_data()` 的产物）；
      **窗口只画、不查库**（与悬浮窗同一条纪律），demo 模式换成 demo 数据源。
    *  /  / ：设置子页的动作回调（app 侧接）。
    * `on_pick_deck`：逐局行的对手卡组格被点（标注链路，app 接）。
    """

    #: (行 dict, 被点的控件) —— 与悬浮窗的 `pick_deck` 同语义
    pick_deck = Signal(dict, object)
    #: 点了胜负徽章（手动修正入口，任务书 Bug 2）—— 与悬浮窗 `result_pick` 同语义
    result_pick = Signal(dict, object)

    def __init__(self, on_pick_deck=None, loader=None,
                 on_pick_result=None, analysis_loader=None, tier_loader=None,
                 banlist_loader=None, deckview_loader=None,
                 on_fetch_decks=None, script_runner_factory=None,
                 opp_deck_images=None, storage=None,
                 collector_loader=None, export_saver=None,
                 export_loader=None,
                 on_restart=None, on_set_autostart=None,
                 on_toggle_floating=None, on_lang_changed=None,
                 parent=None):
        super().__init__(parent)
        self._loader = loader
        self._analysis_loader = analysis_loader
        self._tier_loader = tier_loader
        self._banlist_loader = banlist_loader
        self._deckview_loader = deckview_loader
        self._on_fetch_decks = on_fetch_decks              # Q17（app 侧接）
        #: 「卡组名管理」页签的卡组图解析（`(名字) -> 路径`；app 侧给 `DeckImages.resolve`）
        self._opp_deck_images = opp_deck_images
        #: 「卡组名管理」换图要写库 —— storage 由 app 侧给（单测可省）
        self._storage = storage
        #: 设置区三功能页页头的「当前数据状态」（app 侧给 `ui_state.data_status` 的包装）
        #: 标题栏的「采集器状态」（app 侧给 `ui_state.collector_status` 的包装，D4b）
        self._collector_loader = collector_loader
        #: 导出落盘（app 侧接：弹文件框 + `ui_state.write_export`，D4c）
        #: 签名 ``(fmt, month, mode_group) -> None``
        self._export_saver = export_saver
        #: 导出页预览取数（app 侧给，收 ``(month, mode_group)``，D4c）
        self._export_loader = export_loader
        self._script_runner_factory = script_runner_factory   # 单测注入（不真起进程）
        #: 设置子页的动作回调（app 侧接；页面只发号施令 —— 审查 P2-3，别 import ui.app）
        self._on_restart = on_restart
        self._on_set_autostart = on_set_autostart
        #: 语言切换回调（实时切换，2026-10-04）：app 侧重建窗口
        self._on_lang_changed = on_lang_changed
        #: 悬浮窗显示/隐藏回调（2026-10-03）：app 侧切换后返回新状态 bool
        self._on_toggle_floating = on_toggle_floating
        self._on_pick_deck = on_pick_deck
        self._on_pick_result = on_pick_result
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(tr('Master Duel 战绩'))
        #: ★ 任务栏图标 = 托盘同款 MD 图（2026-10-03 用户）
        from ui.floating import md_app_icon
        self.setWindowIcon(md_app_icon())
        self.setMinimumSize(MIN_W, MIN_H)
        #: 模式筛选（None = 全部）—— 记在 QSettings（`dash/mode_filter`）
        self._mode_group = settings().value('dash/mode_filter') or None
        #: 战绩页看的月（``None`` = 本月）—— **不落 QSettings**：用户 2026-10-01
        #: 拍板「重开看板回本月」，只有热力图点月卡才临时切
        self._stats_month = None
        #: 多账号（2026-10-03）：战绩页看谁的（``None`` = 全部）—— 记 QSettings
        #: ``dash/account``（切账号是持久偏好，跟切月不同）。清单来自战绩数据
        #: 的 ``accounts``（storage.account_options）。
        self._account = settings().value('dash/account') or None
        self._accounts = []
        #: 分析页页签（None=综合 / True=先攻 / False=后攻）—— 记在 QSettings
        _raw = str(settings().value('dash/analysis_tab') or '')
        self._went_first = ((True if _raw == '1' else False) if _raw else None)
        #: 分析页筛选器（v5）：日期按月 / 模式四类（不做段位筛选，用户拍板）
        self._analysis_month = settings().value('dash/analysis_month') or None
        self._analysis_mode = settings().value('dash/analysis_mode') or None
        self._drag_from = None
        self._resizing = False
        self._cursor_shape = None
        self._current = ''
        #: 「卡组」页当前页签（`names` / `tier` / `composition`）—— **不再记忆**：
        #: 每次打开都落第一页「卡组名管理」（用户 2026-10-12；QSettings 里的
        #: `dash/decks_tab` 旧值作废）
        self._decks_tab = ''
        #: 「卡组构成」页签当前看的是哪个卡组（站内英文名）—— 记在 QSettings
        #: （`dash/deckview`），Tier 芯片 / 卡组页都会改它
        #: ⚠️ 这个键**不是**页签键（它存的是卡组名）—— 两者都别互相覆盖
        self._deck_view_name = str(settings().value('dash/deckview') or '')

        self._build()
        self._restore_geometry()
        page = str(settings().value('dash/page') or NAV_PAGES[0][1])
        if page == 'deckview':
            # 旧版把「卡组构成」当独立隐藏页存过 `dash/page` —— 现在它是
            # 「卡组」页的一个页签，迁移到那儿（旧值作废，但别落成空页）
            page = 'decks'
        self.open_page(page)
        self.reload()          # ★ 建完就先取一次数（否则「战绩」页一直是空壳）

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)      # 1px 给描边留位置
        outer.setSpacing(0)
        outer.addWidget(self._make_title())

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.rail = Rail()
        self.rail.page_requested.connect(self.open_page)
        #: ★ 多账号（2026-10-03）：品牌位点击 = 账号下拉
        self.rail.account_picked.connect(self._show_account_menu)
        row.addWidget(self.rail)

        self.stack = QStackedWidget()
        self.stack.setObjectName('dashStack')
        self._build_pages()
        row.addWidget(self.stack, 1)
        outer.addLayout(row, 1)

    def _build_pages(self) -> None:
        """构建全部页控件 —— `_build` 首建 + 切语言原地重译共用（2026-10-05）。"""
        self.pages = {}
        self._page_fps = {}      # 各页数据指纹（reload 闸门，见 _fingerprint）
        for name, key in NAV_PAGES:
            sub, hint = PAGE_HINTS[key]
            if key == 'stats':                       # ★ D1：战绩页是真内容，不是占位
                page = StatsPage()
                page.pick_deck.connect(self._forward_pick)
                page.result_pick.connect(self._forward_result_pick)
                page.mode_selected.connect(self.set_mode_group)
                page.month_selected.connect(self.set_stats_month)
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'analysis':                # ★ D2：对局分析矩阵（2026-09-19）
                page = AnalysisPage()
                page.scope_selected.connect(self.set_went_first)
                page.month_selected.connect(self.set_analysis_month)
                page.mode_selected.connect(self.set_analysis_mode)
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'export':                  # ★ D4c：导出（左栏独立一项）
                page = ExportPage()
                page.filter_changed.connect(self._on_export_filter)
                page.export_requested.connect(self._on_export_requested)
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'decks':                   # ★ D3 §2：三个页签
                page = DecksPage(
                    runner_factory=self._script_runner_factory,
                    image_resolver=self._opp_deck_images,
                    storage=self._storage)
                page.deck_selected.connect(self.open_deck_view)
                #: ⚠️ 「← 回 Tier 表」**不再跨页** —— Tier 表就是左边那个页签，
                #: 卡组页自己切（`DecksPage._on_back`）；这里**别**再 open_page('tier')
                #: （那个 key 已经不存在了，会回落成第一页 = 跳到「战绩」）
                page.saved.connect(self._reload_deckview)   # 覆盖表改了 → 重取
                if callable(self._on_fetch_decks):
                    page.fetch_requested.connect(self._on_fetch_decks)
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'overlay':             # ★ 悬浮窗开关（2026-10-03 用户）
                page = FloatingTogglePage()
                page.toggle_requested.connect(self._toggle_floating)
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            self._add_page(key, tr(name), tr(sub), tr(hint))
        sub, hint = PAGE_HINTS[QUERY_KEY]
        self._add_page(QUERY_KEY, tr(sub), tr(hint), tr(hint))
        for name, key in QUERY_PAGES:
            sub, hint = PAGE_HINTS[key]
            if key == 'present':                 # ★ 礼盒页是真内容（2026-09-19）
                page = PresentPage()
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'banlist':                 # ★ 禁限表是真内容（D3 第二刀）
                page = BanlistPage(runner_factory=self._script_runner_factory)
                page.refreshed.connect(self._reload_banlist)  # 抓完重取
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            if key == 'cards':               # ★ 查卡网站清单（2026-10-03）
                page = CardsLinksPage()
                self.pages[key] = page
                self.stack.addWidget(page)
                continue
            self._add_page(key, tr(sub), tr(hint), tr(hint))
        #: 「设置」= **索引页**（设置项不在左栏了，得在这儿能点到）
        sub, hint = PAGE_HINTS[SETTINGS_KEY]
        page = SettingsIndexPage(
            '', tr(hint), runner_factory=self._script_runner_factory,
            on_restart=self._on_restart,
            on_set_autostart=self._on_set_autostart,
            on_lang_changed=self._on_lang_changed)
        self.pages[SETTINGS_KEY] = page
        self.stack.addWidget(page)
        # 09-27：数据工具的独立工具页由概览页的「运行」按钮 + 页底日志区
        # 取代 —— 不再有独立工具页。

    def _add_page(self, key: str, title: str, subtitle: str, hint: str) -> None:
        page = PlaceholderPage(title, subtitle, hint)
        self.pages[key] = page
        self.stack.addWidget(page)

    def _toggle_floating(self) -> None:
        """「悬浮窗」页的开关按钮 -> app 侧执行，回传新状态刷新按钮文案。"""
        visible = None
        if callable(self._on_toggle_floating):
            visible = self._on_toggle_floating()
        if visible is not None:
            self.set_floating_visible(bool(visible))

    def set_floating_visible(self, visible: bool) -> None:
        self._floating_visible = bool(visible)   # 记下（切语言重建页后回填）
        page = self.pages.get('overlay')
        if page is not None:
            page.set_floating_visible(bool(visible))

    def retranslate(self) -> None:
        """切语言**原地重译**（2026-10-05 用户：窗口不关、同一界面直接变）。

        页控件全部销毁重建（tr 是构造期烘进控件的），窗口几何不动，
        筛选/当前页等窗口对象上的状态保留；重建后重取数据、回到原页。
        重建阻塞主线程 —— 套 `widgets.run_with_busy`：旋转 loading 遮罩
        先画一帧再干活，结束后遮罩至少停留 400ms（2026-10-05 用户：像
        Windows 加载那样有个「转一下」的过程感）。
        """
        cur = self._current

        def _work():
            for w in list(self.pages.values()):
                self.stack.removeWidget(w)
                # ★ 先 hide 再脱离：hide 立即停止绘制（修重影），且隐藏状态下
                #   setParent(None) 不会闪现顶层窗口框（2026-10-05 实测弹框修因）
                w.hide()
                w.setParent(None)
                w.deleteLater()
            self.pages.clear()
            self._build_pages()
            QApplication.processEvents()   # 段间喘气 —— 弧线在段间继续跳
            self.rail.retranslate()
            self._title_text.setText(tr('Master Duel 战绩统计'))
            self.setWindowTitle(tr('Master Duel 战绩'))
            if getattr(self, '_floating_visible', None) is not None:
                self.set_floating_visible(self._floating_visible)
            self.reload()
            QApplication.processEvents()
            self.open_page(cur)

        widgets.run_with_busy(self, _work)


    def _make_title(self) -> QWidget:
        bar = QWidget()
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(TITLE_H)
        bar.setStyleSheet(
            'background: %s; border-top-left-radius: %dpx;'
            ' border-top-right-radius: %dpx;'
            % (theme.CANVAS, theme.FLOAT_RADIUS, theme.FLOAT_RADIUS))
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(theme.S_MD, 0, theme.S_XS, 0)
        lay.setSpacing(theme.S_SM)
        self._title_text = _lab(tr('Master Duel 战绩统计'), 'title',
                                size=theme.F_TITLE, weight=QFont.Weight.Medium)
        lay.addWidget(self._title_text)
        lay.addStretch(1)
        #: ★ D4b：采集器状态（标题栏右侧、窗口按钮左边）—— 不跑也能一眼看到
        #:   采集器在不在跑（判据见 `ui_state.collector_status` 的 docstring）
        self.collector = _lab('', 'body', size=theme.PAGE_F_SMALL)
        self.collector.setStyleSheet('color: %s; background: transparent;'
                                     % theme.INK_MUTED)
        lay.addWidget(self.collector)
        lay.addSpacing(theme.S_MD)
        #: ⚠️ 标题栏那个「导出 ▾」下拉**已撤**（用户 2026-09-22：
        #:    「导出需要在左边栏单独做一项，按月和模式筛选导出」）→ 见左栏「导出」页
        btn_min = WindowButton('min')
        btn_min.clicked.connect(self.hide)      # 只收起窗口，不退出程序
        lay.addWidget(btn_min)
        btn_close = WindowButton('close')
        btn_close.clicked.connect(self.hide)    # 同上：看板关闭 ≠ 退出程序
        lay.addWidget(btn_close)

        bar.mousePressEvent = self._title_press              # type: ignore
        bar.mouseMoveEvent = self._title_move                # type: ignore
        bar.mouseReleaseEvent = self._title_release          # type: ignore
        self._title_bar = bar
        return bar

    # ------------------------------------------------------------ 换页
    def current_page(self) -> str:
        return self._current

    def open_page(self, key: str) -> None:
        """切到某页（导航 / 设置项 / 程序调用都走这里）。"""
        if key not in self.pages:
            key = NAV_PAGES[0][1]
        self._current = key
        self.stack.setCurrentWidget(self.pages[key])
        self.rail.set_current(key)
        settings().setValue('dash/page', key)
        # 进页刷新（审查 P1-2）：只对实现了 refresh 的页生效（如设置概览页），
        # 其余页保持原样 —— 别顺手给所有页加基类。
        getattr(self.pages[key], 'refresh', lambda: None)()

    def _forward_pick(self, row, widget) -> None:
        self.pick_deck.emit(row, widget)
        if callable(self._on_pick_deck):
            self._on_pick_deck(row, widget)

    def _forward_result_pick(self, row, widget) -> None:
        self.result_pick.emit(row, widget)
        if callable(self._on_pick_result):
            self._on_pick_result(row, widget)

    # ------------------------------------------------------------ 数据（D1）
    def set_loader(self, loader) -> None:
        self._loader = loader

    def set_analysis_loader(self, loader) -> None:
        """「对局分析」页的数据源热切换（demo 开关切换走这里，D2）。"""
        self._analysis_loader = loader

    def set_tier_loader(self, loader) -> None:
        """「Tier 表」页的数据源热切换（D3）。"""
        self._tier_loader = loader

    def set_banlist_loader(self, loader) -> None:
        """「禁限表」页的数据源热切换（D3 第二刀）。"""
        self._banlist_loader = loader

    def set_deckview_loader(self, loader) -> None:
        """「卡组构成」页签的数据源热切换 —— 收站内英文名。"""
        self._deckview_loader = loader

    # ------------------------------------------------------------ 卡组构成（D3 §2：页签）
    def open_deck_view(self, ref_name: str) -> None:
        """看某个卡组的构成：记住卡组 + 切到「卡组」页的**构成页签** + 取数。

        入口：Tier 表芯片点击 / 「卡组」页里的调用。**对用户的行为没变**
        （D3 §2 只是把页面位置从隐藏页搬进页签）。
        """
        name = str(ref_name or '').strip()
        if not name:
            return
        self._deck_view_name = name
        settings().setValue('dash/deckview', name)
        self.open_page('decks')
        self.set_decks_tab('composition')      # 切页签（是否取数见下）
        self._reload_deckview()

    def set_decks_tab(self, tab: str) -> None:
        """切「卡组」页的页签（并记进 `dash/decks_tab`）。

        ⚠️ 切到构成页签时**不自动取数** —— 取数照旧只由
        `set_deckview_loader` / `open_deck_view` 那条路驱动（单测也吃这条契约）。
        """
        page = self.pages.get('decks')
        if page is None:
            return
        key = str(tab or '')
        if key not in ('names', 'tier', 'composition'):
            key = 'names'
        page.set_tab(key)
        self._decks_tab = key

    def current_decks_tab(self) -> str:
        """「卡组」页当前页签（`names` / `composition`）。"""
        page = self.pages.get('decks')
        return page.current_tab() if page is not None else self._decks_tab

    def current_deck_view(self) -> str:
        """「卡组构成」页签当前看的卡组（站内英文名）。"""
        return self._deck_view_name

    def _reload_deckview(self) -> None:
        """「卡组构成」页签重取（收卡组名）—— 数据打在卡组页上。"""
        page = self.pages.get('decks')
        if page is None:
            return
        data = {}
        if callable(self._deckview_loader):
            try:
                data = self._deckview_loader(self._deck_view_name) or {}
            except Exception:
                data = {}                    # 数据层炸了也别把窗口带崩
        fp = _fingerprint(data)
        if self._page_fps.get('deckview') == fp:
            return                       # 数据没变 -> 页保持原样（卡顿修复 2026-10-11）
        self._page_fps['deckview'] = fp
        page.set_data(data)

    def _reload_decks(self) -> None:
        """「卡组」页只读部分（现在只有构成页签要数）。"""
        self._reload_deckview()

    def _show_account_menu(self) -> None:
        """账号下拉（挂左栏「MD 战绩」品牌位；单选：账号名 + 局数）。

        ⚠️ 2026-10-03 用户口径：**没有「全部账号」项** —— 只列账号名字。
        """
        btn = self.rail.brand
        menu = QMenu(btn)
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = self._account
        for name, n, _last in self._accounts:
            act = menu.addAction(tr('%s（%d 局）') % (name, n))
            act.setCheckable(True)
            act.setChecked(name == current)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=name: self.set_account(k))
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    def _update_account_button(self, accounts) -> None:
        """按最新数据的账号清单刷新**左栏品牌位**（= 账号切换器）。

        文案 = 当前账号名（2026-10-03 用户：替换「MD 战绩」几个字、
        只显示账号名字）；未选/选中项失效 = 显示「MD 战绩」。
        """
        self._accounts = list(accounts or [])
        names = [a[0] for a in self._accounts]
        if self._account and self._account not in names:
            self._account = None                   # 选中项没了（清库等）→ 未选
            settings().setValue('dash/account', '')
        self.rail.set_account_label(self._account)

    def set_account(self, name) -> None:
        """账号切换器变了（``None`` = 全部）→ 记住 + 只刷战绩页。"""
        name = name or None
        if name == self._account:
            return
        self._account = name
        settings().setValue('dash/account', name or '')
        self._reload_stats()

    def _reload_stats(self) -> None:
        """只取数重画「战绩」页（**窗口自己不查库**）。

        loader 收模式筛选（`self._mode_group`；``None`` = 全部，2026-09-18
        七轮起驱动「战绩总览」）+ 月份（`self._stats_month`；``None`` = 本月，
        2026-10-01 起热力图点月卡切月用）。
        """
        page = self.pages.get('stats')
        if page is None:
            return
        data = {}
        if callable(self._loader):
            try:
                data = self._loader(mode_group=self._mode_group,
                                    month=self._stats_month,
                                    account=self._account) or {}
            except TypeError:
                # 旧签名 ``loader(mode_group[, month])``（单测 lambda 等）也认；
                # 降级路径同样要兜（loader 自己炸了别把窗口带崩）
                try:
                    data = self._loader(mode_group=self._mode_group,
                                        month=self._stats_month) or {}
                except TypeError:
                    try:
                        data = self._loader(self._mode_group) or {}
                    except Exception:
                        data = {}
                except Exception:
                    data = {}
            except Exception:
                data = {}                    # 数据层炸了也别把窗口带崩
        # ⚠️ 账号按钮**每次都要更新**（切换账号后数据可能不变但品牌位要跟着换
        #    —— 单测 test_set_account_reloads_with_account 抓过），放在闸门之前。
        self._update_account_button(data.get('accounts') or [])
        fp = _fingerprint(data)
        if self._page_fps.get('stats') == fp:
            return                       # 数据没变 -> 页保持原样（卡顿修复 2026-10-11）
        self._page_fps['stats'] = fp
        page.set_data(data)

    def reload(self) -> None:
        """向数据层要新数据并重画「战绩」+「对局分析」等页（**窗口自己不查库**）。

        「对局分析」loader 收页签（`self._went_first`，D2）。
        """
        self._reload_stats()
        self.refresh_collector_status()
        self._reload_export()
        self._reload_analysis()
        self._reload_tier()
        self._reload_banlist()
        self._reload_decks()
        self._reload_deckview()

    def _reload_banlist(self) -> None:
        """「禁限表」页重取（纯只读页）。"""
        page = self.pages.get('banlist')
        if page is None:
            return
        data = {}
        if callable(self._banlist_loader):
            try:
                data = self._banlist_loader() or {}
            except Exception:
                data = {}
        page.set_data(data)

    # ------------------------------------------------------------ 导出（D4c）
    def _on_export_filter(self, month, mode_group) -> None:
        """导出页的月份 / 模式变了 → 重取预览（窗口只转发，导出的数由 app 侧给）。"""
        self._reload_export()

    def _on_export_requested(self, fmt, month, mode_group) -> None:
        """导出页点了「导出 CSV/JSON」→ 交给 app 侧弹框写盘（**窗口不写文件**）。"""
        if callable(self._export_saver):
            self._export_saver(str(fmt), month, mode_group)

    def _reload_export(self) -> None:
        """重取导出页的预览（`_export_loader` 收 ``(month, mode_group)``）。"""
        page = self.pages.get('export')
        if page is None:
            return
        data = {}
        if callable(self._export_loader):
            try:
                data = self._export_loader(page.month(), page.mode_group()) or {}
            except Exception:
                data = {}                    # 数据层炸了也别把窗口带崩
        fp = _fingerprint(data)
        if self._page_fps.get('export') == fp:
            return                       # 数据没变 -> 页保持原样（卡顿修复 2026-10-11）
        self._page_fps['export'] = fp
        page.set_data(data)

    def set_export_loader(self, loader) -> None:
        """导出页预览取数的热切换（demo 开关用）。"""
        self._export_loader = loader
        self._reload_export()

    def refresh_collector_status(self) -> str:
        """刷新标题栏的采集器状态（`reload()` 与热切换都会调）。

        返回渲染出来的那一行 —— 单测直接锁它。**失败不抛**（数据层已兜底，
        这里再包一层是防 app 侧注入的 loader 炸）。
        """
        try:
            status = (self._collector_loader() if callable(self._collector_loader)
                      else {})
        except Exception:                                    # noqa: BLE001
            status = {}
        line = ui_state.collector_status_line(status)
        self.collector.setText(line)
        return line

    def set_collector_loader(self, loader) -> None:
        """采集器状态取数的热切换（demo 开关用）。"""
        self._collector_loader = loader
        self.refresh_collector_status()

    def _reload_tier(self) -> None:
        """「Tier 表」**页签**重取（它在「卡组」页里，用户 2026-09-21 搬过来的）。"""
        page = self.pages.get('decks')
        if page is None or not hasattr(page, 'set_tier_data'):
            return
        data = {}
        if callable(self._tier_loader):
            try:
                data = self._tier_loader() or {}
            except Exception:
                data = {}                    # 数据层炸了也别把窗口带崩
        fp = _fingerprint(data)
        if self._page_fps.get('tier') == fp:
            return                       # 数据没变 -> 页保持原样（卡顿修复 2026-10-11）
        self._page_fps['tier'] = fp
        page.set_tier_data(data)

    def _reload_analysis(self) -> None:
        """「对局分析」页单页重取（页签/筛选切换时不用连带「战绩」页）。"""
        page = self.pages.get('analysis')
        if page is None:
            return
        data = {}
        if callable(self._analysis_loader):
            try:
                data = self._analysis_loader(self._went_first,
                                             self._analysis_month,
                                             self._analysis_mode) or {}
            except Exception:
                data = {}                    # 数据层炸了也别把窗口带崩
        fp = _fingerprint(data)
        if self._page_fps.get('analysis') == fp:
            return                       # 数据没变 -> 页保持原样（卡顿修复 2026-10-11）
        self._page_fps['analysis'] = fp
        page.set_data(data)

    def set_went_first(self, value) -> None:
        """分析页页签变了（综合/先攻/后攻）→ 记住 + 重取（照 `set_mode_group` 模式）。"""
        value = None if value is None else bool(value)
        if value == self._went_first:
            return
        self._went_first = value
        settings().setValue('dash/analysis_tab',
                            '' if value is None else ('1' if value else '0'))
        self._reload_analysis()

    def set_analysis_month(self, ym) -> None:
        """分析页日期筛选变了（'YYYY-MM' / None=全部，v5）→ 记住 + 重取。"""
        ym = ym or None
        if ym == self._analysis_month:
            return
        self._analysis_month = ym
        settings().setValue('dash/analysis_month', ym or '')
        self._reload_analysis()

    def set_analysis_mode(self, key) -> None:
        """分析页模式筛选变了（MODE_GROUPS key / None=全部，v5）→ 记住 + 重取。"""
        key = key or None
        if key == self._analysis_mode:
            return
        self._analysis_mode = key
        settings().setValue('dash/analysis_mode', key or '')
        self._reload_analysis()

    def set_mode_group(self, key) -> None:
        """模式筛选变了（StatsPage 的下拉）→ 记住 + 重取数据。"""
        key = key or None
        if key == self._mode_group:
            return
        self._mode_group = key
        settings().setValue('dash/mode_filter', key or '')
        self.reload()

    def set_stats_month(self, ym) -> None:
        """热力图月卡被点（``'YYYY-MM'``）→ 战绩页切到那个月 + 重取。

        ⚠️ 不落 QSettings —— 重开看板总是回本月（用户 2026-10-01 拍板）。
        """
        ym = ym or None
        if ym == self._stats_month:
            return
        self._stats_month = ym
        #: ★ 2026-10-03 切月卡顿修复：只刷「战绩」页 —— 全量 `reload()` 会把
        #: 导出/分析/Tier/禁限/卡组七个 loader 全部重跑一遍，点一次月卡卡一下。
        self._reload_stats()

    # ------------------------------------------------------------ 缩放/拖动
    def edges_at(self, pos):
        m = RESIZE_MARGIN
        edges = None
        if pos.x() <= m:
            edges = Qt.LeftEdge
        elif pos.x() >= self.width() - m:
            edges = Qt.RightEdge
        if pos.y() <= m:
            edges = (edges | Qt.TopEdge) if edges else Qt.TopEdge
        elif pos.y() >= self.height() - m:
            edges = (edges | Qt.BottomEdge) if edges else Qt.BottomEdge
        return edges

    def mousePressEvent(self, ev):                          # noqa: N802
        if ev.button() != Qt.LeftButton:
            return
        edges = self.edges_at(ev.position().toPoint())
        if edges and self.begin_resize(edges):
            ev.accept()
            return
        self._drag_from = (ev.globalPosition().toPoint()
                           - self.frameGeometry().topLeft())
        ev.accept()

    def mouseMoveEvent(self, ev):                           # noqa: N802
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()
            return
        shape = cursor_for(self.edges_at(ev.position().toPoint()))
        if shape != self._cursor_shape:
            self._cursor_shape = shape
            self.setCursor(shape)

    def mouseReleaseEvent(self, ev):                        # noqa: N802
        self._drag_from = None
        self.save_geometry()

    def leaveEvent(self, ev):                               # noqa: N802
        if self._cursor_shape is not None:
            self._cursor_shape = None
            self.unsetCursor()

    def begin_resize(self, edges) -> bool:
        """交给**系统原生**缩放（自己算位移会闪，`ui/floating.py` 有实测注释）。"""
        if not edges:
            return False
        self._resizing = True
        win = self.windowHandle()
        if win is not None:
            try:
                win.startSystemResize(edges)
                return True
            except Exception:
                pass
        self._resizing = False
        return False

    def _title_press(self, ev):
        if ev.button() == Qt.LeftButton:
            win = self.windowHandle()
            if win is None or not win.startSystemMove():
                self._drag_from = (ev.globalPosition().toPoint()
                                   - self.frameGeometry().topLeft())

    def _title_move(self, ev):
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)

    def _title_release(self, _ev):
        self._drag_from = None

    def keyPressEvent(self, ev):                            # noqa: N802
        if ev.key() == Qt.Key_Escape:
            self.hide()
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------ 几何
    def save_geometry(self) -> None:
        s = settings()
        s.setValue('dash/size', self.size())
        s.setValue('dash/pos', self.pos())

    def _restore_geometry(self) -> None:
        s = settings()
        size = s.value('dash/size')
        if isinstance(size, QSize) and size.width() >= MIN_W and size.height() >= MIN_H:
            self.resize(size)
        else:
            self.resize(MIN_W, MIN_H)
        pos = s.value('dash/pos')
        if isinstance(pos, QPoint) and pos.x() >= -50 and pos.y() >= -50:
            self.move(pos)
        else:
            screen = self.screen().availableGeometry() if self.screen() else None
            if screen is not None:
                self.move(screen.left() + max(0, (screen.width() - self.width()) // 2),
                          screen.top() + max(0, (screen.height() - self.height()) // 2))

    def closeEvent(self, ev):                               # noqa: N802
        self.save_geometry()
        super().closeEvent(ev)

    def paintEvent(self, ev):                               # noqa: N802
        """整窗圆角 + 1px 发丝描边（半透明无边框窗靠 QSS 圆角不够）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(theme.FLOAT_ALPHA)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.FLOAT_RADIUS, theme.FLOAT_RADIUS)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)
