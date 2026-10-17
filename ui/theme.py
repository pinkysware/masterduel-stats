# -*- coding: utf-8 -*-
"""M7 样式 token —— **唯一来源**，规格见仓库根的 `DESIGN.md`。

纪律（照抄 DESIGN.md）：
* 层级靠**表面阶梯 + 发丝边框**，全系统**零投影 / 零渐变**；
* 状态用「**语义色前景 + soft 底**」的小面积表达，**不整行铺色**；
* 强调色只出现在 **tab 选中下划线 / 焦点环**，不做装饰；
* 圆角阶梯到 **10px 为止**；间距走 **4px 基数**（4/8/12/16/24/32）。

本模块**故意不 import Qt**（纯常量 + 纯函数）—— 这样它可被单测直接 import，
也不会把界面依赖带进数据层。
"""
from __future__ import annotations

from typing import Optional, Tuple

# ---------------------------------------------------- 表面阶梯（层级靠它）
CANVAS = '#0A0B0D'        # 主窗口底 / 悬浮窗底（近黑中性偏冷，**不是蓝**）
SURFACE1 = '#101114'      # 卡片 / 面板 / 选择器行底
SURFACE2 = '#15171A'      # 悬停行 / 次级面板 / 输入框
SURFACE3 = '#1A1D21'      # 选中行 / 菜单 / tooltip

# ---------------------------------------------------- 边框（一律发丝半透明白）
HAIRLINE = 'rgba(255,255,255,0.08)'
HAIRLINE_STRONG = 'rgba(255,255,255,0.14)'
HAIRLINE_FOCUS = 'rgba(110,121,232,0.55)'

# ---------------------------------------------------- 滚动条
#: ⚠️ 滑块**不能**用 HAIRLINE_STRONG（0.14）—— 2026-09-15 用户实测：「拖动条太淡」。
#: 0.14 在 262 宽的悬浮窗里只有 4px 宽、看着像一条划痕。这里单独给一档更亮的灰。
SCROLL_HANDLE = 'rgba(255,255,255,0.30)'
SCROLL_HANDLE_HOVER = 'rgba(255,255,255,0.42)'
SCROLL_W = 10             # 滑块轨道宽（含 2px 内边距 -> 可见 6px）

# ---------------------------------------------------- 文字
INK = '#F2F4F6'           # 主文字 / 数字
INK_MUTED = '#A8B0BA'     # 次要文字 / 说明
#: 标签 / 单位 / 时间戳。⚠️ 2026-09-15 从 `#6E7681` 提亮到 `#8B949E` ——
#: 旧值在 SURFACE1 上只有 **4.11:1**，低于小字 AA（4.5），表头那种 11px 灰字
#: 在暗底上读起来发虚（用户当时的原话是「可读性差」）。现值 **6.14:1**。
INK_SUBTLE = '#8B949E'

# ---------------------------------------------------- 语义色（soft 底 + 饱和前景）
WIN = '#3FB950'
LOSE = '#F85149'
WARN = '#D29922'
NEUTRAL = '#8B949E'

WIN_SOFT = 'rgba(63,185,80,0.14)'
LOSE_SOFT = 'rgba(248,81,73,0.14)'
WARN_SOFT = 'rgba(210,153,34,0.14)'
NEUTRAL_SOFT = 'rgba(255,255,255,0.06)'

#: ⚠️ 本工具沿用参考图口径：**胜 = 绿、负 = 红**（不是股票那套红涨绿跌）。
RESULT_FG = {'Win': WIN, 'Lose': LOSE, 'Draw': NEUTRAL}
RESULT_SOFT = {'Win': WIN_SOFT, 'Lose': LOSE_SOFT, 'Draw': NEUTRAL_SOFT}
RESULT_LABEL = {'Win': '胜', 'Lose': '负', 'Draw': '平'}

# ---------------------------------------------------- 强调色（克制到近乎看不见）
ACCENT = '#6E79E8'        # 只允许：tab 选中下划线 / 焦点环 / 链接

# ---------------------------------------------------- 圆角（阶梯到 10 为止）
R_XS, R_SM, R_MD, R_LG = 4, 6, 8, 10

# ---------------------------------------------------- 间距（4px 基数）
S_XS, S_SM, S_MD, S_LG, S_XL, S_XXL = 4, 8, 12, 16, 24, 32

# ---------------------------------------------------- 字形层级
#: 支持的语言（与 `ui/lang` 设置、`core.card_names.LANGS` 同一套值）
LANGS: Tuple[str, ...] = ('cn', 'en', 'jp')
DEFAULT_LANG = 'cn'
#: 语言显示名（key = `LANGS`；09-26 从 `ui/app.py` 挪来 —— 单一来源，别建第二份）
LANG_NAMES = {'cn': '中文', 'en': 'English', 'jp': '日本語'}
#: 「当窗口关闭时」的两个选项（用户 2026-09-15 定；09-26 从 `ui/app.py` 挪来）
CLOSE_ACTIONS = (('tray', '隐藏到任务栏托盘'), ('quit', '退出主程序'))
DEFAULT_CLOSE_ACTION = 'tray'

#: 拉丁 / 数字打底 —— **随仓库带**（`data/fonts/Inter-*.ttf`，SIL OFL 1.1）。
#: 选它的理由：有**真等宽数字**（`tnum`），而界面里全是 `65.6%` / `21-11`，
#: 数字等宽才不挤、不留锯齿。
FONT_LATIN: Tuple[str, ...] = ('Inter', 'Segoe UI')

#: 中日文**按当前语言切族** —— 没有任何一族能同时伺候中日两语：
#: 中文字体只覆盖 73% 的日文卡名字符，日文字体只覆盖 74% 的中文卡名字符
#: （实测见 `docs/M7悬浮窗可缩放设计.md` §3.3；得意黑当时更糟，日文只有 69%）。
#: ⚠️ 这里走**系统优先**（Windows 自带 YaHei / Meiryo / Yu Gothic）；要跨机器
#:    款式一致就把思源（Noto Sans SC / JP）加进来 —— 全套约 26MB，按需再上。
FONT_CJK = {
    'cn': ('Microsoft YaHei', 'Noto Sans SC'),
    'jp': ('Noto Sans JP', 'Meiryo', 'Yu Gothic UI'),
    'en': ('Inter', 'Segoe UI'),
}

#: 兜底：`∀` `Ϝ` `神` 这类怪字符（我们三语卡名里确实有）谁也没覆盖全
FONT_TAIL: Tuple[str, ...] = ('Segoe UI Symbol', 'Segoe UI Emoji')


def families(lang: str = DEFAULT_LANG) -> Tuple[str, ...]:
    """UI 字体的**唯一**族来源：拉丁打底 + 当前语言的 CJK + 另一种 CJK + 符号兜底。

    ⚠️ **Qt 没有 CSS 的 `lang` + `locl` 自动匹配**（`QFont.setFeature` 不接受
    script/language 参数），所以只能**按族切**；`QFont.setFamilies()` 是
    **逐字符回退链** —— 顺序写错的话，日文字形永远轮不上。
    ⚠️ 链尾**必须**留另一种 CJK：英文界面里照样会显示日文卡组名
    （`エルフェンノーツ`）、日文界面里照样会显示中文卡名。
    """
    cur = FONT_CJK.get(lang) or FONT_CJK[DEFAULT_LANG]
    others: Tuple[str, ...] = tuple(
        f for key in LANGS if key != lang for f in FONT_CJK[key])
    out: list = []
    for fam in FONT_LATIN + tuple(cur) + others + FONT_TAIL:
        if fam not in out:                  # 去重：链里出现重名一定是配置错了
            out.append(fam)
    return tuple(out)

F_METRIC = 18       # 悬浮窗大数字（**尺寸由格子高度反推**，见 FLOAT_H 注释）
F_METRIC_SM = 16    # 逐局行的回合数
F_TITLE = 15
F_BODY = 13
F_BODY_SM = 12
F_STAT = 19         # 悬浮窗统计块的大数值（总场次 / 胜率 / 今日）
F_LABEL = 11        # 标签（悬浮窗里用 F_LABEL_SM）
F_LABEL_SM = 10     # 悬浮窗那种紧凑格里的标签
F_TAB = 12

# ---------------------------------------------------- 尺寸
#: 悬浮窗尺寸。**高度是下限，不是真值** —— 真值由 `FloatingWindow._build()`
#: 末尾按内容算（`setFixedHeight(max(FLOAT_H, sizeHint()))`）。
#: ⚠️ 别回去手写高度：手调过 112→124→126→128 每次差 1–2px，
#:    而高度不够会**垂直裁字** —— 22px 的 `0` 被压扁后只剩中间一条，看着像 `()`（踩过）。
#: ⚠️ 宽度 2026-09-15 三次收窄：420 -> 360 -> 292 -> 260 -> **262**
#: （段位列改「当前段位 + ↑/↓」后徽章槽窄了 ~30px；用户：「继续把UI做窄」）。
#: 最后一次 260 -> 262 是**换字体（Inter + 系统 CJK）后重新实测**的结果 ——
#: 字体变了，徽章槽/文本宽度就变，这个数是量出来的，**不是拍的**（见 ui/floating.window_width）。
#: 这里是**设计下限**；实际宽度由 `ui.floating.window_width()` 按**当前字体实测**算出来
#: （各列定宽之和）—— 因为字体解析在不同环境差别极大，写死会裁字或留空。
FLOAT_W, FLOAT_H = 262, 112
#: 悬浮窗分层结构（区块标题条 / 三栏统计 / 表头）—— 高度由内容推导，见 `_fit_height()`
SECTION_H = 22      # 区块标题条（居中标题 + SURFACE1 底）
STATS_H = 90        # 统计块：两块标题条（22）+ 发丝（1）+ 内容（约 66）
HEADER_H = 20       # 表头行
FLOAT_RADIUS = R_LG
FLOAT_ALPHA = 0.94              # 不能太透，字会糊
TAB_H = 28
TAB_UNDERLINE = 2

# ---------------------------------------------------- S7 看板左栏
#: ⚠️ 2026-09-18 用户实测反馈：「侧栏字体都太小」→ 整体**放大一档**并**加宽栏宽**。
#: 之前用 F_BODY_SM(12)/F_TITLE(15)，在 1200×620 的窗口里显得又小又空。
#: 这里**不再复用** F_* 那套（它们是给悬浮窗那种紧凑格调的），单独给一档。
#: ⚠️ 2026-09-18 用户**第二次**反馈字号不够 → 再提一档（15/17/14 → 17/19/15）。
RAIL_W = 150            # 栏宽（⚠️ 2026-09-18 用户：右边像素不够 → **缩栏**腾地方；
                        # 字号不动 —— 用户要的是栏窄，不是字小）
                        # ★ 2026-10-16：180 -> 150（手坑两列要 128px）。实测导航
                        # 文字不裁的下限 ≈ 121px（「对局分析」@17px = 68 + 内边距），
                        # 150 仍余 29px —— 见 docs/手坑识别计划.md §5.4.1。
RAIL_F_BRAND = 20       # 顶部「MD 战绩」
RAIL_F_GROUP = 19       # 分组标题（查询 / 设置）
RAIL_F_ITEM = 17        # 主导航项 + 设置项按钮
RAIL_F_CHILD = 15       # 分组下的子项（禁限表 / 查卡）
RAIL_H_GROUP = 42       # 三种行高（文字控件高度一律 ≥ 字体高，见 §11③）
RAIL_H_ITEM = 38
RAIL_H_CHILD = 32
#: 左栏图标边长（**自绘**，不引素材 —— 同 `WindowButton` 的思路）
RAIL_ICON = 18

# ---------------------------------------------------- S7「战绩」页
#: ⚠️ 2026-09-18 用户实测：「战绩页所有的字体都太小」→ 整页**统一提一档**，
#: 且**单独一组 token**（别再蹭悬浮窗那套紧凑字号：那是给 26px 行高的格子调的）。
PAGE_F_TITLE = 20       # 区块标题（按月 / 战绩总览 / 对局记录）
PAGE_F_NOTE = 14        # 标题右边的说明
PAGE_F_BODY = 15        # 正文（表格里的字）
PAGE_F_BIG = 32         # 总览那五个大数字（⚠️ 2026-09-18 二轮：用户「字体太小、太空，
                        # 填满这个框」→ 26 → 32；`_metric_line(avail=…)` 会按实测宽度
                        # 自动降档兜底，所以这个数可以贴着上限给）
PAGE_F_OVER_TITLE = 16  # 总览五格的标题（14 → 16，同轮放大）
PAGE_F_SMALL = 14       # 次要列 / 组头标签 / 表头
PAGE_ROW_H = 32         # 逐局行高
PAGE_HEAD_H = 28        # 表头行高

MAIN_W, MAIN_H = 980, 720
ROW_H = 34                      # 选择器行
#: ⚠️ 2026-09-15 用户要求精简：删掉次名行后行高 46 → 34（图也从 40 缩到 `THUMB`=26，
#:    不然 40px 的图塞不进 34px 的行）。想保留大图就把这两个值一起调回去。
SEL_W = 440                     # 选择器面板宽
#: 面板宽的**下限**（搜索框 + 取消 + 确定 放得下）—— 实际宽度由
#: `DeckSelectorPanel._fit_width()` 按内容算（用户：「搜索框太宽，和卡组名贴着就行」）
SEL_W_MIN = 300
BADGE_H = 18
BAR_W = 3                       # 状态竖条

DECK_ICON = 40
DECK_ICON_SM = 24
THUMB = 26              # 最近几场里的卡图边长（正方形裁剪）
RANK_ICON = 15          # 段位/模式徽章里的图标边长
PAGE_RANK_ICON = 40     # 看板总览「最高段位」那格的大图标（用户 2026-09-18：
                        # 「字体太小、填充满整个框」—— 图标与字号一起放大）
RANK_ICON_ROW = 18      # 逐局行段位格里的图标边长（悬浮窗那格是 20 —— 行更高，取 18）
PAGE_F_SUB = 14         # 总览那几格里「括号里的胜负」（同一行的副信息；随主值一起重排：
                        # 主值 32px(实测 106) + 括号 14px(实测 49) = 155 = 格内可用宽）
PAGE_F_OVER_RANK = 28   # 总览「最高段位」那格的段位名（26 → 28）
MATCH_ROW_H = 34        # 最近几场一行的高度（26 图 + 上下各 4）
HEAT_CELL = 11
CARD_PAD = S_LG                 # 卡片内边距 16（不要 32+）


# ------------------------------------------------------------- 纯函数
def win_rate_color(rate: Optional[float]) -> str:
    """胜率按值着色：>50% 绿 / =50% 黄 / <50% 红 / 无数据 次要灰。"""
    if rate is None:
        return INK_SUBTLE
    if rate > 0.5:
        return WIN
    if rate < 0.5:
        return LOSE
    return WARN


def progress_color(rate: Optional[float]) -> str:
    """**进度色**（标注进度这类完成度），**不要**用 :func:`win_rate_color` 顶替。

    低标注率不是"输" —— 用胜负色会让 43% 的进度显示成红色，读起来像"打得差"。
    """
    if rate is None:
        return INK_SUBTLE
    if rate >= 0.8:
        return WIN
    if rate >= 0.5:
        return WARN
    return NEUTRAL


#: 升段 / 掉段时那段文字的色（`core.ranks.compare()` 的返回值当键）
RANK_DELTA_FG = {'up': WIN, 'down': LOSE, 'same': INK_MUTED}


def rank_delta_fg(delta: Optional[str]) -> str:
    """`'up'` → 绿、`'down'` → 红、其余（含 None）→ 次要灰。

    ⚠️ 只在 **up / down** 时才配箭头显示；`same` / `None` 不画对比。
    """
    return RANK_DELTA_FG.get(delta or '', INK_MUTED)


def result_fg(result: Optional[str]) -> str:
    return RESULT_FG.get(result or '', INK_SUBTLE)


def result_soft(result: Optional[str]) -> str:
    return RESULT_SOFT.get(result or '', NEUTRAL_SOFT)


def result_label(result: Optional[str]) -> str:
    return RESULT_LABEL.get(result or '', '—')


def fmt_pct(rate: Optional[float], digits: int = 1) -> str:
    """``0.65625`` -> ``'65.6%'``；``None`` -> ``'-'``（不显示 0%，那是假的）。"""
    if rate is None:
        return '-'
    return '%.*f%%' % (digits, rate * 100)


def fmt_record(wins: int, loses: int) -> str:
    """``(32, 11)`` -> ``'32 - 11'``（参考图 2 的写法）。"""
    return '%d - %d' % (int(wins), int(loses))


def qss() -> str:
    """全局 Qt 样式表 —— **本模块唯一产出 Qt 字符串的地方**。

    ★ **只有颜色 / 边框 / 圆角 / 内边距**，**绝不写 font-family / font-size / font-weight**。

    实测（`tmp/_metric_ab.png`，2026-09-14）：同样的数字用 QSS 定字号会渲染成怪字形
    （`0` 像 `()`、`3` 像 `ȝ`），改用 ``QFont.setPixelSize`` 就正常 ——
    因为 QSS 解析出的字体族和 ``QFont.setFamilies()`` 不是同一个。
    所以字体一律走 `ui.widgets.font()`，两处设字号必然打架。
    """
    return (
        'QWidget { background: transparent; color: %(ink)s; }\n'
        'QMainWindow, QDialog { background: %(canvas)s; }\n'
        'QLabel { background: transparent; }\n'
        'QLabel[role="label"], QLabel[role="label_sm"] { color: %(subtle)s; }\n'
        'QLabel[role="muted"] { color: %(muted)s; }\n'
        'QLabel[role="title"], QLabel[role="body"],'
        ' QLabel[role="metric"] { color: %(ink)s; }\n'
        'QLabel[role="eyebrow"] { color: %(subtle)s; letter-spacing: 0.6px; }\n'

        'QScrollArea, QListView, QListWidget { background: %(s1)s;'
        ' border: 1px solid %(hair)s; border-radius: %(rlg)dpx; }\n'
        # 悬浮窗的**行区**不要边框/底色：它是无边框圆角窗的一部分，
        # 那 1px 边框会让「正好 N 行」也装不下（白白冒出滚动条，实测踩过）。
        'QScrollArea#rowsScroll { background: transparent; border: none;'
        ' border-radius: 0px; }\n'
        'QLineEdit { background: %(s2)s; border: 1px solid %(hair2)s;'
        ' border-radius: %(rmd)dpx; padding: 6px 10px; color: %(ink)s; }\n'
        'QLineEdit:focus { border: 1px solid %(focus)s; }\n'
        'QComboBox { background: %(s2)s; border: 1px solid %(hair2)s;'
        ' border-radius: %(rmd)dpx; padding: 5px 10px; color: %(ink)s; }\n'
        'QComboBox QAbstractItemView { background: %(s3)s;'
        ' border: 1px solid %(hair2)s; color: %(ink)s;'
        ' selection-background-color: %(s2)s; }\n'

        'QPushButton { background: %(s2)s; border: 1px solid %(hair)s;'
        ' border-radius: %(rmd)dpx; padding: 6px 12px; color: %(ink)s; }\n'
        'QPushButton:hover { border: 1px solid %(hair2)s; }\n'
        # 分段单选（设置子页）：选中 = 亮底 + accent 描边，一眼可辨
        'QPushButton:checked { background: %(s3)s; border: 1px solid %(accent)s; }\n'
        'QPushButton:disabled { color: %(subtle)s; }\n'

        'QCheckBox { color: %(ink)s; }\n'
        'QCheckBox::indicator { width: 14px; height: 14px;'
        ' border: 1px solid %(hair2)s; border-radius: %(rxs)dpx;'
        ' background: %(s2)s; }\n'
        'QCheckBox::indicator:checked { background: %(accent)s;'
        ' border: 1px solid %(accent)s; }\n'
        # ⚠️ 勾/圆点**不是** QSS 画的（QSS 只能改底色边框，画勾要引图片资源）——
        # 由 `ui.widgets.TickBox` / `DotRadio` 自绘，见那里的注释。
        'QRadioButton { color: %(ink)s; }\n'
        # ⚠️ 单选项的指示器**也是方框**（用户 2026-09-15：「圆点太丑，换成打勾」）——
        # 勾由 `ui.widgets` 自绘，这里只画空盒子（和勾选框同一套）。
        'QRadioButton::indicator { width: 14px; height: 14px;'
        ' border: 1px solid %(hair2)s; border-radius: %(rxs)dpx;'
        ' background: %(s2)s; }\n'
        'QRadioButton::indicator:checked { background: %(accent)s;'
        ' border: 1px solid %(accent)s; }\n'

        'QToolTip { background: %(s3)s; color: %(ink)s;'
        ' border: 1px solid %(hair2)s; border-radius: %(rsm)dpx;'
        ' padding: 5px 8px; }\n'
        'QMenu { background: %(s3)s; border: 1px solid %(hair2)s;'
        ' border-radius: %(rmd)dpx; padding: %(sxs)dpx; }\n'
        'QMenu::item { padding: 6px 18px; border-radius: %(rsm)dpx;'
        ' color: %(ink)s; }\n'
        'QMenu::item:selected { background: %(s2)s; }\n'
        'QMenu::separator { height: 1px; background: %(hair)s;'
        ' margin: %(sxs)dpx %(ssm)dpx; }\n'
        'QScrollBar:vertical { background: transparent; width: %(sbw)dpx;'
        ' margin: 2px; }\n'
        'QScrollBar::handle:vertical { background: %(sb)s;'
        ' border-radius: 4px; min-height: 24px; }\n'
        'QScrollBar::handle:vertical:hover { background: %(sbh)s; }\n'
        'QScrollBar::add-line, QScrollBar::sub-line { height: 0; }\n'
        'QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }\n'
        % {'canvas': CANVAS, 's1': SURFACE1, 's2': SURFACE2, 's3': SURFACE3,
           'hair': HAIRLINE, 'hair2': HAIRLINE_STRONG, 'focus': HAIRLINE_FOCUS,
           'sb': SCROLL_HANDLE, 'sbh': SCROLL_HANDLE_HOVER, 'sbw': SCROLL_W,
           'ink': INK, 'muted': INK_MUTED, 'subtle': INK_SUBTLE,
           'accent': ACCENT,
           'rxs': R_XS, 'rsm': R_SM, 'rmd': R_MD, 'rlg': R_LG,
           'sxs': S_XS, 'ssm': S_SM}
    )


def font_candidates() -> Tuple[str, ...]:
    """兼容旧调用 = :func:`families` 的默认链。新代码请直接用 `families(lang)`。"""
    return families()


# ---------------------------------------------------- 热力图（S6 统计窗「按月」）
#: 格子边长（周 × 星期阵）——`HEAT_CELL` 见上面「尺寸」段
HEAT_GAP = 3

#: 强度阶梯：0 场用**中性底**，1..4+ 场按 `WIN` 的透明度递增。
#: ⚠️ 0 场**绝不染语义色** —— 染了会被读成「那天赢过」，这是热力图最容易骗人的地方
#: （同 :func:`progress_color` 那条注释的教训：颜色是有语义的，别拿来当亮度用）。
HEAT_LEVELS = (
    'rgba(255,255,255,0.06)',      # 0 场（= NEUTRAL_SOFT 那档）
    'rgba(63,185,80,0.30)',
    'rgba(63,185,80,0.55)',
    'rgba(63,185,80,0.78)',
    'rgba(63,185,80,1.00)',
)


def heat_color(n) -> str:
    """当日场次 → 格子色（≥ 4 场封顶）。纯函数，单测直接调。"""
    try:
        k = int(n or 0)
    except (TypeError, ValueError):
        k = 0
    k = max(k, 0)
    return HEAT_LEVELS[min(k, len(HEAT_LEVELS) - 1)]


# ---------------------------------------------------- 热力图（D1 · 看板版）
#: 格子 = **横向矩形**（照 Untapped 的比例，宽 > 高）—— 比 S6 那个 11px 方块大得多，
#: 不再"一个色点代表一天"。
#: 尺寸预算（窗口最小 1200、左栏 180、边框 2、滚动条 10 → 视口 1008，
#: 再减页面左右内边距 32 = **可画宽 976**）：
#:   一张月卡 = 7 × 26 + 6 × 3 = 200；每行 4 张 = 4×200 + 3×12 = 836 ≤ 976 ✓
#:   （5 张要 1048，放不下 —— **改格子宽就得重算这里**）
HEAT_W, HEAT_H = 26, 20

#: 1 场时的填充比例 —— 再小就看不见了，再大则跟 4 场区分不开。
HEAT_MIN_FILL = 0.42

#: 胜率 → 块色的三个锚点（与 `LOSE` / `WARN` / `WIN` 同值，别另配色）
_HEAT_LOW = (0xF8, 0x51, 0x49)
_HEAT_MID = (0xD2, 0x99, 0x22)
_HEAT_HIGH = (0x3F, 0xB9, 0x50)


def heat_fill_ratio(n, n_max) -> float:
    """当日场数 → **填充块的比例**（0.42..1.0）；0 场返回 ``0.0``（不画块）。

    用户 2026-09-18 口径（照 Untapped）：
    **块的大小 = 场数**、**颜色 = 胜率** —— 两件事分成两根轴表达。
    （S6 那版把两件事都压在"同一根绿色深浅轴"上，所以看不懂，别再回去。）

    曲线用 `sqrt`：场数分布长尾（真库 1..18），线性会把 1~5 场全挤在底部。
    """
    try:
        k, m = int(n or 0), int(n_max or 0)
    except (TypeError, ValueError):
        return 0.0
    if k <= 0:
        return 0.0
    if m <= 1:
        return 1.0
    t = float(min(k, m) - 1) / float(m - 1)
    return HEAT_MIN_FILL + (1.0 - HEAT_MIN_FILL) * (t ** 0.5)


def heat_rate_color(rate) -> str:
    """当日胜率 → 块色（0% 红 → 50% 黄 → 100% 绿，线性插值）；无胜负 = 中性灰。"""
    if rate is None:
        return NEUTRAL
    r = min(max(float(rate), 0.0), 1.0)
    a, b, t = ((_HEAT_LOW, _HEAT_MID, r * 2.0) if r <= 0.5
               else (_HEAT_MID, _HEAT_HIGH, (r - 0.5) * 2.0))
    return '#%02X%02X%02X' % tuple(int(round(a[i] + (b[i] - a[i]) * t))
                                   for i in range(3))


def heat_cell(n, wins, loses, n_max):
    """一天 → ``(块色, 填充比例)``；0 场返回 ``None``（格子只留底色，不染语义色）。

    胜率只在**有胜负的那几局**上算（平局既不入分子也不入分母 —— 全平局 = 中性灰）。
    """
    ratio = heat_fill_ratio(n, n_max)
    if ratio <= 0:
        return None
    w, l = int(wins or 0), int(loses or 0)
    rate = (float(w) / (w + l)) if (w + l) else None
    return (heat_rate_color(rate), ratio)


def heat_max(daily) -> int:
    """一份 by-day 数据里的最大场次（给界面显示图例用；空数据 = 0）。"""
    best = 0
    for v in (daily or {}).values():
        try:
            best = max(best, int(v.get('n') or 0))
        except Exception:
            continue
    return best
