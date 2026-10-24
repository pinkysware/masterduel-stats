# -*- coding: utf-8 -*-
"""悬浮窗 —— **统计 + 最近对局，一个面板**（M7 改版，2026-09-15 用户定）。

```
┌──────────────────────────────────────┐
│                总览                   │  全宽标题条（2026-09-15 晚改上下）
├──────────────────┬───────────────────┤
│      总胜率       │       先手率       │  两栏等宽，栏间竖发丝线
│      65.6%       │      60.0%        │
│      21-11       │      18-12        │
├──────────────────┴───────────────────┤
│ ▸            先后攻统计                │  左缘 = 折叠三角（整条可点）
├──────────────────┬───────────────────┤
│       先攻        │       后攻         │
│        18        │        12         │
│    胜率 77.8%     │    胜率 41.7%      │
├──────────────────────────────────────┤
│ ▾             最近对局                 │
├──────────────────────────────────────┤
│ 卡组   结果  先/后   段位   对手卡组     │  表头（列宽与行对齐）
├──────────────────────────────────────┤
│ [图]   胜   [币]   [图]II   [图]名     │
└──────────────────────────────────────┘
```

要点：

* **统计上下两块**（2026-09-15 晚用户定：原先是并排，改**竖排、总览在上**）：
  上「总览」= **总胜率 / 先手率**；下「先后攻统计」= 先攻/后攻各自的**场数 + 胜率**。
  ⚠️ **先手率** = `first_hand_pct`（先攻局数 ÷ **已知先后手的**局数），
  **不是** `initiative_rate`（赢硬币率）—— 用户口径「硬币 ≠ 先后手」。
* **区块标题条**：居中 + `SURFACE1` 底（学参考图的分段方式，不靠发丝线硬分）
* **表头对齐**：`MatchHeader` 与 `MatchRow` 共用同一套固定列宽（`COL_*`）；
  段位那栏留**固定槽位** `COL_BADGE`，否则升段行被撑宽、表头就对不上
* **等级标识** = 段位图标 + 罗马数字；升/掉段 `[图]III → [图]II`；
  **数字与箭头同色**（`muted`，同时间那一列）；段位全称在 tooltip
* **列序**（2026-09-15 按 ChatGPT 建议稿重排）：我方卡组 | 结果 | 先/后 |
  段位 | **对手卡组（图 + 名，最后一列）**。
  「先/后」= `went_first`（实际先攻，不是赢硬币），未知显示 `—`
* **硬币图标**（2026-09-15 用户要求）：「先/后」格不再用文字，改画
  **青眼亚白龙硬币正/反面**（`data/coin_icons/`，游戏内解包素材 64²）：
  正面 = 先攻、背面 = 后攻，悬停 tooltip 写明；未知仍显示 `—`。
  ⚠️ 素材缺失时**回退文字**先/后 —— 图读不出来宁可退回文字，别空着
* **对手卡组名居中**（2026-09-15 用户要求「位置需要调整居中」）：名字在
  定宽 66px 列内**居中** —— 此前左对齐，短名（如「烙印」）偏离列中心
  20-40px，和居中的表头对不上；卡图仍钉在格子左缘不变
* **时间列已删**（2026-09-15 用户要求）—— 数据层 `row['time']` 字段仍在
  （core/ui_state 生成），UI 不消费而已；时刻要看去库里看，**别把列加回来**
* **折叠按钮**（2026-09-15 用户要求）：「先后攻统计」「最近对局」两条标题条
  **左缘画折叠三角 + 整条可点**，点击折叠/展开本块（窗口高度跟着重算）。
  三角是 paintEvent **自绘**的 —— 不依赖字体有没有 U+25BC 这类几何字符
  （旧字体得意黑就没有；换了字体也继续自绘，免得再被字体覆盖牵着走）
* **「对手名」列已删**（2026-09-15 晚用户要求，换窗口收窄）——
  名字没丢：挪到**对手卡组格的 tooltip**（悬停可见）
* **对手卡组没标注** → 半透明默认图 + 琥珀 `!` 角标 + 琥珀「未知卡组」
  （2026-09-15 用户定：文案从「点击标注」改成「未知卡组」，角标保留）
* **对手卡组格 = 整格一个按键**（S3，2026-09-15 拍板）：图 + 名字整格可点
  （**已标注行也可点**，选择器预填当前卡组，选新的覆盖）；按下→抬起位移 ≤
  `CLICK_TOLERANCE` 才算点击 —— 修掉「按住卡组格拖窗口误弹选择器」。
  删掉「待标注」tab 后，这是**唯一的标注入口**
* ⚠️ 标题条右侧的「待补 N」**已删**（2026-09-15 用户要求去掉）——
  未标注行本身有琥珀提示，计数是冗余，别加回来
* 时间用 `started_at`（`ended_at` 只有 34/54）
* ❌ 不显示实时回合 / LP（D2：本期不消费实时帧）

数据全部来自 :class:`core.ui_state.UiSnapshot`（**已解析好**，界面不查库、不查别名）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QPointF, QPoint, QRectF, QSettings, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea,
                               QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

from core import paths
from core.ui_state import UiSnapshot
from ui import theme, widgets
from ui.deck_banner import CONFIRM_KEY, DeckConfirmBanner  # 卡组确认提示条（2026-09-22）

TITLE_RECENT = '最近对局'
#: 一次最多画几行（数据层也只取这么多，见 `ui/app.py` 的 `recent_limit`）。
#: 默认**露** `MIN_ROWS` 行，多出来的靠滚动 / 拖高看。
MAX_ROWS = 10
#: 「最近对局」**默认露出几行**（2026-09-15 用户定）：想同时看更多就拖高 / 滚轮。
MIN_ROWS = 3

#: 列宽 —— **表头和行必须用同一套**，否则对不齐（改一处就得一起改）。
#: 列序 2026-09-15 按 ChatGPT 建议稿重排（对手卡组挪到最后一列、新增先/后）；
#: 「对手名」列当晚删除（用户要求收窄窗口，名字挪进对手卡组格的 tooltip）
COL_MY = theme.THUMB                    # 我方卡图（**只放图**，别再撑宽 —— 用户 2026-09-16）
COL_RESULT = 20                         # 胜负小胶囊
COL_ORDER = 28                          # 先/后
COL_OPP_THUMB = theme.THUMB             # 对手卡图
COL_OPP_NAME = 64                       # 对手卡组名（定长省略 + 悬停看全名）。
                                        # 2026-10-05 用户口径：**不追求长名全显示**，
                                        # 列宽贴着字走、窗口最小宽贴着这列的右边
OPP_GAP = 4                             # 卡图与卡组名之间的缝
COL_OPP = COL_OPP_THUMB + OPP_GAP + COL_OPP_NAME   # 对手卡组整格（最后一列）
#: 按下→抬起位移 ≤ 此像素才算点击（S3：防「按住卡组格拖窗口误弹选择器」）
CLICK_TOLERANCE = 8
#: 缩放热区宽度（与 CLICK_TOLERANCE 同量级；窗够小，做太宽会挡住拖动）
RESIZE_MARGIN = 8
#: 拉边时「重排列宽」的限流间隔（ms）。40ms ≈ 25fps —— 肉眼跟得上，
#: 但把实测 55~73 次/秒的重排压到 ~25 次/秒（重排会连带整窗重绘）。
RELAYOUT_MS = 40
#: Qt 的「不限制尺寸」= 宏 `QWIDGETSIZE_MAX`（PySide6 **没导出**这个宏，只能写值）。
SIZE_MAX = 16777215

#: 标题条上的三个窗口按钮：`-` 最小化（隐藏到托盘）/ `□` 最大化（拉到尺寸上限，再点还原）/ `×` 关闭
WIN_BUTTON_KINDS = ('min', 'max', 'close')
from ui.i18n import tr

#: 先/后列的**硬币图标**（2026-09-15 用户要求）：正面 = 先攻、背面 = 后攻。
#: 素材 = 游戏内解包的 `duelcointoss` 图标 64²（坐标见 docs/素材解包说明.md），
#: 复制进仓库 `data/coin_icons/`；读不到时行内回退成文字 先/后
#:
#: ★ BUG-B5-10：原来是**裸相对路径**（按进程 CWD 解析）—— 从别的目录启动
#:   （资源管理器里双击 .py、`python <abs>/tools/duel_ui.py`）就读不到，
#:   而读不到时**静默退回文字**，没人会发现。改走 `paths.asset_path`
#:   （随包只读素材，不跟 DATA_DIR）。
COIN_FRONT = paths.asset_path('coin_icons', 'coin_front.png')
COIN_BACK = paths.asset_path('coin_icons', 'coin_back.png')
COIN_SIZE = 18                          # 圆形硬币显示边长（槽位 COL_ORDER=28）

#: 徽章内部尺寸（槽位宽由下面 `badge_slot()` 按字体实测出来）
BADGE_ICON = 20
BADGE_NUM_SIZE = 11
BADGE_SPACING = 2
#: 升/掉段的**方向箭头**（2026-09-15 用户定：升降段 = 当前段位 + 箭头，
#: 不再画「旧段 → 新段」对比 —— 升段局很少，对比形态白占宽度）
ARROW_UP = '↑'
ARROW_DOWN = '↓'


def badge_slot() -> int:
    """段位徽章的**槽位宽** —— 按当前环境的字体**实测**（结果缓存）。

    取最宽的形态：升降段 `[图]III ↑`（一个图标 + 数字 + 方向箭头）+ 6px 余量
    （2026-09-15 用户定：升/掉段不再画「旧段 → 新段」对比，普通行更窄）。

    ⚠️ **绝对不要写死**。实测同一个界面在两台环境里差 2 倍：
    无头环境 Qt 解析不到字体、回退到**全角**字体，`I` 一个字占 11px（`III`=33px）；
    真机 Microsoft YaHei 下 `I` 只有 3px（`III`=9px）。
    写死的结果不是**裁字**就是**留一大块空白** —— 两种都踩过。
    """
    if _SLOT_CACHE.get('badge'):
        return _SLOT_CACHE['badge']
    fm = cached_fm(widgets.font(BADGE_NUM_SIZE, QFont.Weight.Medium))
    widest = max(fm.horizontalAdvance(s) for s in ('I', 'II', 'III', 'IV', 'V'))
    arrow = max(fm.horizontalAdvance(s) for s in (ARROW_UP, ARROW_DOWN))
    slot = BADGE_ICON + BADGE_SPACING * 2 + widest + arrow + 6
    _SLOT_CACHE['badge'] = slot
    return slot


def badge_group_width() -> int:
    """段位内容组（图标 + 罗马数字 + 箭头）的**固定**宽度。

    ★ 为什么要固定：列会变宽（等比拉伸），但图标必须**跨行对齐**、
    表头「段位」又要**保持居中**。让内容组宽度恒定、再把它整体居中，两个要求同时满足
    （2026-09-15 用户先提「段位图标没有对齐」，拉到最宽后又提「全都没有对齐」）。
    """
    if _SLOT_CACHE.get('bgroup'):
        return _SLOT_CACHE['bgroup']
    fm = cached_fm(widgets.font(BADGE_NUM_SIZE, QFont.Weight.Medium))
    widest = max(fm.horizontalAdvance(s) for s in ('I', 'II', 'III', 'IV', 'V'))
    arrow = max(fm.horizontalAdvance(s) for s in (ARROW_UP, ARROW_DOWN))
    w = BADGE_ICON + BADGE_SPACING * 2 + widest + arrow
    _SLOT_CACHE['bgroup'] = w
    return w


def chrome_width() -> int:
    """窗口总宽里**非列**的部分：1px×2 描边 + 行左右内边距。

    ⚠️ **列间距不在里面** —— `column_widths()` 自己会从可用宽里扣。
    2026-09-15 踩过：这里把 `(S_SM-2)*4` 也算进来，等于**扣了两次**，
    于是表头/行整体比可用宽窄 24px、右边多一块死白，看上去「全都没对齐」。
    算窗口下限时要把列间距单独加上（见 `window_width`）。
    """
    return 2 + theme.S_SM * 2


def min_columns_width(hidden=None) -> int:
    """各列都取最小值时的列宽之和（= 窗口下限时的那一份）。

    ⚠️ **隐藏的列不计入**（用户 2026-10-17）—— 否则窗口下限还按五列算，
    藏掉三列后右边留一圈空白（用户实测）。
    """
    hide = tuple(hidden or float_hidden_cols())
    while len(hide) < 3:
        hide = hide + (False,)
    h_deck, h_order, h_rank = hide[0], hide[1], hide[2]
    w = opp_col_width() + result_col_width()
    if not h_deck:
        w += deck_col_width()
    if not h_order:
        w += order_col_width()
    if not h_rank:
        w += badge_slot()
    return w


#: ★ `QFontMetrics` **实例**缓存（2026-10-10）。
#: 每次新建 `QFontMetrics` 都要走一遍字体解析/字形表，实测单次
#: `horizontalAdvance` 要 **29ms**（首次加载字体时更贵）。而 `min_columns_width()`
#: 一次要问 6 个列宽、`window_width()` 又调它 4 遍 = **12 次**，
#: 实测 `FloatingWindow.__init__` 里光这一项就吃掉 **0.35s**（启动最慢的一段）。
#: 字体定了就不变（`_refresh_fonts` 只在换语言时改），所以按字体缓存实例即可。
_FM_CACHE = {}


def cached_fm(font):
    """`QFontMetrics` **按字体缓存** —— 别在热路径里反复新建。

    实测（2026-10-10）：新建一次要 29ms，而启动时 `min_columns_width()` 之类
    会问十几次 —— `FloatingWindow.__init__` 光字体度量就吃掉 0.35s，
    是启动最慢的一段。字体换了自然算出不同的 key，不用手动失效。
    """
    key = (font.family(), font.pointSizeF(), font.weight(), font.styleHint())
    fm = _FM_CACHE.get(key)
    if fm is None:
        fm = QFontMetrics(font)
        _FM_CACHE[key] = fm
    return fm


def label_fm():
    """表头用的 `QFontMetrics`（缓存版）。"""
    return cached_fm(widgets.font(theme.F_LABEL, QFont.Weight.Normal))


def body_fm():
    """正文字号下的 `QFontMetrics`（缓存版）。"""
    return cached_fm(widgets.font(theme.F_BODY))


def _text_col_w(text: str, base: int) -> int:
    """表头列宽 = `max(基准, 译文表头宽 + 6)` —— 三语表头都不裁字。

    2026-10-04 用户实测：最小列宽下 en 的 Decks / 1st/2nd 被裁。量一次缓存
    （字体定了就不变；`_SLOT_CACHE` 与 `badge_slot` 同款）。
    """
    key = ('txt', text, base)
    if _SLOT_CACHE.get(key):
        return _SLOT_CACHE[key]
    w = max(base, label_fm().horizontalAdvance(text) + 2)
    _SLOT_CACHE[key] = w
    return w


def result_col_width() -> int:
    """「结果」列的宽 —— **按表头文字实测**，不写死。

    ⚠️ 原 `COL_RESULT = 20` 是照胜负胶囊定的，而表头「结果」两个字要 **22px**
    → 标题被裁（2026-09-15 用户实测：「结果两字显示不全」）。
    取 `max(胶囊宽, 表头文字宽 + 2)`；只量一次（字体定了就不变）。
    """
    return _text_col_w(tr('结果'), COL_RESULT)


def deck_col_width() -> int:
    """「卡组」列宽下限（译文表头放得下；2026-10-04 同款口径）。"""
    return _text_col_w(tr('卡组'), COL_MY)


def order_col_width() -> int:
    """「先/后」列宽下限（en 的 1st/2nd 比 28px 宽 —— 2026-10-04 实测被裁）。"""
    return _text_col_w(tr('先/后'), COL_ORDER)


def opp_name_width(rows) -> int:
    """对手卡组名列**想要**的宽 = 当前可见名字里最宽的那个（贴着字右边）。

    2026-10-05 用户：cn/ja 名字比英文短，定宽 64 右侧太空 —— 改按内容实测。
    ★ 2026-10-14 用户改口径（**推翻** 10-05 的「上限 `COL_OPP_NAME`」）：
      **不再封顶 64px** —— 拉宽窗口时这一列要跟着变宽、把长卡组名显示全。
      放不下的兜底由 `column_widths()` 按**窗口可用宽**做（真放不下才省略 +
      tooltip 全名）。空表 = 基准 `COL_OPP_NAME`（与旧行为一致）。
    """
    fm = body_fm()
    need = 0
    for r in (rows or []):
        # ★ BUG-B5-14：未标注但有 M5 建议的行，那一格显示的是 `suggest_name`
        #   （`OppDeckCell` 的 deck_text = suggest）—— 只按 `opp_deck` 算宽，
        #   建议名比列宽长就被 elide 成 `…`（2026-10-05 用户口径「贴着字右边」）。
        deck = (r or {}).get('opp_deck') or (r or {}).get('suggest_name') or ''
        if deck:
            need = max(need, fm.horizontalAdvance(str(deck)))
    if not need:
        return COL_OPP_NAME
    return max(40, need + 8)


def opp_col_width() -> int:
    """对手卡组整列宽 = max(基准 `COL_OPP`, 卡图+缝+「未知卡组」实测宽)。

    「未知卡组」是**固定 UI 文案**（琥珀未标注态），省略成「未知…」既难看
    又丢语义（2026-10-05 审查）—— 名字列收窄后给它保底。
    """
    fm = body_fm()
    need = fm.horizontalAdvance(tr('未知卡组')) + 8
    return max(COL_OPP, COL_OPP_THUMB + OPP_GAP + need)


#: 悬浮窗「最近对局」隐藏哪些列（用户 2026-10-17 要求可隐藏卡组/先后/段位）。
#: 顺序与 `column_widths` 返回的**前三列**对应：(卡组, 先/后, 段位)。
#: 默认**全显示**（不藏）—— 值 False = 隐藏。
FLOAT_HIDE_KEYS = ('ui/float_hide_deck', 'ui/float_hide_order',
                   'ui/float_hide_rank')
#: **藏了列**时窗口宽的绝对下限（用户 2026-10-17）—— 低于它就装不下
#: 「结果胶囊 + 对手卡组」这条最小链。只当**兜底**，正常下限由可见列算出。
FLOAT_W_MIN = 150


def float_settings() -> QSettings:
    """设置读写的**唯一入口**（`FloatingWindow._settings` 同款）。

    ⚠️ 走 `FloatingWindow._settings` 而不是自己 `QSettings('masterduel','M7')`：
    测试正是通过替换 `FloatingWindow._settings` 来**隔离用户真实设置**的
    （见 `tests/test_ui_floating._isolate_settings`）。这里另起一个实例 =
    绕过隔离 -> 测试读到用户真机状态（踩过一次，`test_ui_floating` 整片红）。
    """
    return FloatingWindow._settings()


def float_hidden_cols() -> tuple:
    """悬浮窗要**隐藏**的列 -> ``(卡组, 先/后, 段位)``（True = 隐藏）。"""
    st = float_settings()
    out = []
    for k in FLOAT_HIDE_KEYS:
        v = st.value(k)
        out.append(False if v is None else str(v) not in ('0', 'false', 'False'))
    return tuple(out)


def column_widths(avail_w: int, opp_name_w: Optional[int] = None,
                  hidden=None) -> tuple:
    """`(卡组, 结果, 先/后, 段位, 对手卡组)` —— **表头与行都调这一个**。

    ⚠️ **口径演变**：2026-09-15「所有列一起拉伸」→ 2026-10-04「窄列钉死、
    弹性给段位+对手卡组」→ 2026-10-05「五列全部钉死基准宽」→
    ★ **2026-10-14 定稿（用户改回来）**：**前四列钉死，多余宽度全给「对手卡组」
    列** —— 拉宽窗口这一列就跟着变宽（**吃满可用宽**，不以名字宽度为上限），
    名字长过窗口的部分才省略 + tooltip。

    传 `opp_name_w`（= `opp_name_width(rows)`，见 `_relayout_columns`）时按新口径；
    **不传**（CLI / 无行数据）→ 返回各列基准宽，与旧版逐字一致（单测锁着）。
    """
    hide = tuple(hidden or ())
    while len(hide) < 3:
        hide = hide + (False,)
    #: 隐藏的三列是「卡组(0) / 先/后(2) / 段位(3)」—— 见 `FLOAT_HIDE_KEYS`
    h_deck, h_order, h_rank = hide[0], hide[1], hide[2]
    deck_w = 0 if h_deck else deck_col_width()
    order_w = 0 if h_order else order_col_width()
    rank_w = 0 if h_rank else badge_slot()
    fixed = (deck_w, result_col_width(), order_w, rank_w)
    if not opp_name_w:
        return fixed + (opp_col_width(),)
    #: 间距也只算**可见**列之间的
    n_show = 5 - sum(1 for x in (h_deck, h_order, h_rank) if x)
    gaps = (theme.S_SM - 2) * max(n_show - 1, 0)
    #: ★ 2026-10-17 用户纠正：「**隐藏列后不要增加对手卡组的宽度**」——
    #: 之前这里把「省下的宽度」全部喂给最后一列，于是藏掉三列后对手卡组
    #: 那一格撑得又宽又空（用户实测）。现在对手卡组列**只拿它自己要的那点**
    #: （名字实测宽，见 `opp_name_width`），**省下的宽度由窗口自己缩掉**
    #: （见 `window_width` / `min_columns_width` 都按可见列算）。
    #: 窗口拉宽时它仍会跟着长（名字要多少给多少 + 下限 `COL_OPP`）。
    #: ⚠️ 2026-10-17 用户：**隐藏列不许让对手卡组列变宽**（实测藏三列后那格
    #: 撑得又宽又空）。做法：把窗口宽的**下限余量**也从可用宽里扣掉 ——
    #: 窗口按可见列缩窄后如果还剩下「低于 `window_width()` 的那点富余」，
    #: 那是窗口下限的填充，不该摊给任何一列。
    #: 拉宽窗口时它照旧跟着长（2026-10-14 口径：名字要多少给多少）。
    left = int(avail_w) - sum(fixed) - gaps
    return fixed + (max(COL_OPP, left),)


def pos_on_screen(win, pos) -> bool:
    """保存的窗口位置还**在屏幕上**吗（换显示器 / 改分辨率后要能拉回来）。

    ★ BUG-B5-15：原来是 `pos.x() >= -50 and pos.y() >= -50` ——
    **只挡左上负坐标**。换了显示器、或把副屏拔掉之后，窗口可以停在
    「屏幕外右侧 / 下方」（x/y 都是很大的正数），用户会以为程序没启动。
    现在按「窗口矩形与任一屏幕至少重叠 60×60」判。

    :param win: 用来取自身尺寸的 widget（`self`）
    :param pos: QSettings 里读回来的 `QPoint`
    """
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QGuiApplication
    if not isinstance(pos, QPoint):
        return False
    w, h = win.width(), win.height()
    if w <= 0 or h <= 0:
        w, h = max(60, win.minimumWidth()), max(60, win.minimumHeight())
    rect = QRect(pos, QPoint(pos.x() + w, pos.y() + h))
    for screen in QGuiApplication.screens():
        if rect.intersects(screen.availableGeometry()):
            return True
    return False


def max_window_width(names) -> int:
    """窗口宽的**上限**：最长的卡组名能**完整显示**时的宽度。

    2026-09-15 用户定：左右最多拉到这里（三语卡组名都要算进去），再宽就是纯留白。
    名字池由 `core.ui_state.deck_name_pool()` 提供；空表就退回默认下限。

    ★ 2026-10-14：口径改成「多余宽度全给对手卡组列」（见 `column_widths`）后，
      上限 = **前四列基准 + 列间距 + 该列名字实际需要的宽** —— 拉到这里名字就
      全显示，再宽是留白。（旧的「等比放大反解」随 10-05 口径一起作废。）
    """
    fm = body_fm()
    need = max([fm.horizontalAdvance(str(n)) for n in names if n]
               or [COL_OPP_NAME])
    #: ⚠️ 隐藏的列不计入（用户 2026-10-17：藏列后窗口整体也要变窄）
    hide = float_hidden_cols()
    fixed = [result_col_width()]
    if not hide[0]:
        fixed.append(deck_col_width())
    if not hide[1]:
        fixed.append(order_col_width())
    if not hide[2]:
        fixed.append(badge_slot())
    gaps = (theme.S_SM - 2) * len(fixed)      # 可见列之间的间距
    avail = sum(fixed) + gaps + max(COL_OPP_THUMB + OPP_GAP + need + 8, COL_OPP)
    return max(window_width(), chrome_width() + theme.SCROLL_W + avail)


def available_for_cols(win_w: int, scrollbar_w: int = 0,
                       hidden=None) -> int:
    """窗口宽 → 列的可用宽。滚动条**可见时**要把它的宽度扣掉（设计 §4.3）。

    ⚠️ **下界不能加 `scrollbar_w`**（2026-10-18 修的真 bug）：下面那句已经
    `- scrollbar_w` 扣过一次，floor 里再加一遍 = 多留一条滚动条的宽度 ->
    窗口窄到下限时「对手卡组列」会多吃到 10px（实测藏列后 106 vs 104 的 2px
    偏差就是这么来的）。
    """
    hide = tuple(hidden) if hidden is not None else float_hidden_cols()
    n_show = 5 - sum(1 for x in hide[:3] if x)
    floor = (min_columns_width(hide)
             + (theme.S_SM - 2) * max(n_show - 1, 0))
    return max(floor, int(win_w) - chrome_width() - int(scrollbar_w))


def window_width(hidden=None) -> int:
    """窗口宽的**下限**：一行各列最小值 + chrome + **滚动条**，不低于 `theme.FLOAT_W`。

    ⚠️ **按当前字体实测**，别写死 —— 字体解析在不同环境差别极大
    （无头环境是「每字符 1em」的桩，同一份界面能算出两倍宽）。
    ⚠️ 滚动条**必须**算进来：行数 > `MIN_ROWS` 时它几乎总是可见，
    不算的话列会溢出 `SCROLL_W` 像素、最右边那格（对手卡组）被切掉
    （2026-09-15 量出来的：下限 262 时实际需要 272）。
    """
    hide = tuple(hidden) if hidden is not None else float_hidden_cols()
    #: ⚠️ 缓存键要**区分隐藏集合**：只用 `'win'` 一个槽的话，切了开关还命中旧值
    #: （实测 317 vs 156 —— 藏了列窗口却不变窄）。
    ckey = 'win:%s' % (hide,)
    if _SLOT_CACHE.get(ckey):
        return _SLOT_CACHE[ckey]
    n_show = 5 - sum(1 for x in hide[:3] if x)
    #: ⚠️ `theme.FLOAT_W` 只是**全显示**时的下限（用户 2026-10-17 藏列实测：
    #: 它把窗口钉在 262，省下的宽度就全灌进对手卡组列 -> 那格又宽又空）。
    #: **藏了列时下限跟着可见列走**（`FLOAT_W_MIN` 保底，别缩到装不下）。
    floor = theme.FLOAT_W if n_show == 5 else FLOAT_W_MIN
    w = max(floor,
            chrome_width() + (theme.S_SM - 2) * max(n_show - 1, 0)
            + min_columns_width(hide) + theme.SCROLL_W)
    _SLOT_CACHE[ckey] = w
    return w


def invalidate_width_cache() -> None:
    """列宽/窗口宽的缓存作废（**设置改了必调**）。

    `window_width()` 的结果缓存在 `_SLOT_CACHE['win']` —— 隐藏几列之后不清缓存，
    窗口下限还按五列算（右边留一圈空白，用户 2026-10-17 实测）。
    """
    #: `_SLOT_CACHE` 里既有字符串键也有元组键（字体缓存）—— 只清 win 系列。
    for k in [k for k in list(_SLOT_CACHE)
              if isinstance(k, str) and (k == 'win' or k.startswith('win:'))]:
        _SLOT_CACHE.pop(k, None)


def cursor_for(edges) -> Qt.CursorShape:
    """命中的边 → 缩放光标形状（纯函数，方便单测）。

    ⚠️ 四角要**两个方向一起判**：`Qt.LeftEdge | Qt.TopEdge` 这类组合值
    与单边不相等，所以顺序是「先角、后边、最后箭头」。
    """
    if edges == (Qt.LeftEdge | Qt.TopEdge):
        return Qt.SizeFDiagCursor
    if edges == (Qt.RightEdge | Qt.BottomEdge):
        return Qt.SizeFDiagCursor
    if edges == (Qt.RightEdge | Qt.TopEdge):
        return Qt.SizeBDiagCursor
    if edges == (Qt.LeftEdge | Qt.BottomEdge):
        return Qt.SizeBDiagCursor
    if edges in (Qt.LeftEdge, Qt.RightEdge):
        return Qt.SizeHorCursor
    if edges in (Qt.TopEdge, Qt.BottomEdge):
        return Qt.SizeVerCursor
    return Qt.ArrowCursor


def _centered_host(widget: QWidget) -> QWidget:
    """把**定尺寸**的小控件包进一个容器：容器跟着列宽走，内容居中。

    为什么需要：列宽是等比放大的，但卡图最大只能 26px（行高定死 34），
    胜负胶囊也不该被拉成一条长条 —— 所以「列变宽」由容器吸收，
    控件本身保持原尺寸居中（2026-09-15：不这么做，行里的列会和表头错开 25px）。
    """
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    lay.addWidget(widget)
    lay.addStretch(1)
    return host


#: `badge_slot()` / `window_width()` 的缓存（字体定了就不变）
_SLOT_CACHE: dict = {}


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- 区块
class WindowButton(QWidget):
    """标题条右上的小按钮（**自绘字形**，不依赖字体有没有 □ / ×）。

    2026-09-15 用户要求：总览条上加 `-`（最小化到托盘）、`□`（最大化/还原）、`×`（关闭）。
    ⚠️ 字形一律 `paintEvent` 画 —— 「字体没有那个几何字符就出豆腐块」这坑踩过
    （折三角就是这么处理的），换成 Inter 也一样不赌。
    """

    clicked = Signal()
    SIZE = 18

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self._press = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip({'min': tr('最小化（隐藏到任务栏托盘）'),
                         'max': tr('最大化（再点一次还原）'),
                         'close': tr('关闭')}.get(kind, ''))

    def paintEvent(self, ev):                                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.SIZE
        if self._press or self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE3 if self._press
                              else theme.SURFACE2))
            p.drawRoundedRect(0, 0, r, r, theme.R_XS, theme.R_XS)
        p.setPen(QPen(QColor(theme.INK_MUTED), 1.2))
        c, d = r / 2.0, 3.5
        if self.kind == 'min':                      # 一条横线
            p.drawLine(QPointF(c - d, c + 2), QPointF(c + d, c + 2))
        elif self.kind == 'max':                    # 一个方框
            p.drawRect(QRectF(c - d, c - d + 1, d * 2, d * 2))
        else:                                       # 一个叉
            p.drawLine(QPointF(c - d, c - d), QPointF(c + d, c + d))
            p.drawLine(QPointF(c + d, c - d), QPointF(c - d, c + d))

    # ---- 点击：按下 → 抬起才算（和 OppDeckCell 同一套「别和拖窗抢」）----
    def enterEvent(self, ev):                                   # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, ev):                                   # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._press = True
            self.update()
            ev.accept()                         # 吃掉：别让窗口开始拖动
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        if ev.button() == Qt.LeftButton and self._press:
            self._press = False
            self.update()
            self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class BlockTitle(QLabel):
    """半宽区块的标题条（参考图里「投掷硬币 | 今日」那种）：居中 + `SURFACE1` 底。

    ⚠️ 自己设了样式表就**不再继承**父级规则，所以底色和字色要一起写全。
    ⚠️ 顶上那条（「总览」）必须带**上圆角**，否则它的方角会盖住窗口的圆角 ——
    2026-09-15 用户实测：「左上角/右上角的圆角和下面两个不一样」。实测确认：
    上角对角线上第 2 个像素就已经不透明（= 方角 + SURFACE1 不透明底），
    下角是标准的 10px 圆角。
    """

    def __init__(self, text: str, parent=None, top_rounded: bool = False):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(widgets.font(theme.F_TITLE, QFont.Weight.Medium))
        self.setFixedHeight(theme.SECTION_H)
        rule = 'background: %s; color: %s;' % (theme.SURFACE1, theme.INK)
        if top_rounded:
            # 顶层布局留了 1px 描边，圆角比窗口小 1px 才贴合
            r = max(theme.FLOAT_RADIUS - 1, 0)
            rule += ('border-top-left-radius: %dpx;'
                     'border-top-right-radius: %dpx;' % (r, r))
        self.setStyleSheet(rule)


class SectionTitle(QWidget):
    """整宽区块标题条：**居中**文字 + `SURFACE1` 底；可选**折叠**。

    * `collapsible=True`：左缘画一个折叠三角（**paintEvent 自绘** ——
      不依赖字体有没有 U+25BC 这类几何字符，见模块头），
      **只有这个三角是按钮**（左缘 :data:`TOGGLE_W` 宽的一条），点击翻转并发
      `toggled(expanded)`（True = 展开）。
    * ⚠️ **整条可点已经被撤掉**（2026-09-15 用户：「还是改回点折叠按钮进行折叠」）——
      整条可点时想拖窗口就会误折叠。现在标题条其余部分 = **纯拖窗口区**。
    * 三角/状态只管自己；内容的显隐由监听方做（
      :meth:`StatsBlock.set_split_expanded` / :meth:`FloatingWindow.set_recent_expanded`）。
    * ⚠️ 右侧曾经挂过「待补 N」—— 2026-09-15 用户要求**去掉**：
      未标注行本身就有琥珀提示，这个计数是冗余。别再加回来。
    """

    toggled = Signal(bool)                  # True = 展开
    #: 折叠按钮（左缘三角）的命中宽度 —— 只这一条是按钮，其余整条拖动窗口
    TOGGLE_W = 32                    # 09-27 用户实报「很难点到」：命中加宽

    def __init__(self, text: str, parent=None, top_rounded: bool = False,
                 collapsible: bool = False, buttons: bool = False):
        super().__init__(parent)
        self._collapsible = collapsible
        self._expanded = True
        self._press_global = None        # 点击/拖动的判据（见 mousePressEvent）
        self.setFixedHeight(theme.SECTION_H)
        self.set_corner_rounding(top=top_rounded)
        # 普通 QWidget 默认不画样式表里的背景，要显式开这个属性
        self.setAttribute(Qt.WA_StyledBackground, True)
        if collapsible:
            self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, 0, theme.S_SM, 0)

        # 两侧**等宽弹性空隙**让标题真的居中（用 stretch，没有控件就没有背景问题）
        lay.addStretch(1)
        self.title_label = widgets.label(text, 'title', Qt.AlignCenter)
        self.title_label.setFixedHeight(
            self.title_label.fontMetrics().height() + 2)
        lay.addWidget(self.title_label)
        lay.addStretch(1)

        #: 右上的窗口按钮（`-` / `□` / `×`）—— 只有「总览」那条要（用户 2026-09-15 定）
        self.win_buttons: dict = {}
        if buttons:
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(theme.S_XS // 2)
            for kind in WIN_BUTTON_KINDS:
                btn = WindowButton(kind)
                self.win_buttons[kind] = btn
                bl.addWidget(btn)
            # ⚠️ 左边补一块**等宽**的空白：否则标题会被右边的按钮挤偏（不再居中）
            pad = QWidget()
            pad.setFixedWidth(box.sizeHint().width())
            lay.insertWidget(0, pad)
            lay.addWidget(box)

    def text(self) -> str:
        """标题文字（对齐 QLabel.text() 的叫法，调用方少写一层 `.title_label`）。"""
        return self.title_label.text()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """程序性设状态：只翻三角，**不发信号**（别让监听方被自己绕进去）。"""
        if not self._collapsible or expanded == self._expanded:
            return
        self._expanded = expanded
        self.update()

    def set_corner_rounding(self, top: bool = False,
                            bottom: bool = False) -> None:
        """标题条四角的圆角 —— 顶/底**顶着窗口圆角**时别戳出方角
        （「总览」常态顶上；「最近对局」折叠后顶到窗底）。"""
        rule = 'background: %s;' % theme.SURFACE1
        if top:
            rule += ('border-top-left-radius: %dpx;'
                     'border-top-right-radius: %dpx;'
                     % (theme.FLOAT_RADIUS, theme.FLOAT_RADIUS))
        if bottom:
            rule += ('border-bottom-left-radius: %dpx;'
                     'border-bottom-right-radius: %dpx;'
                     % (theme.FLOAT_RADIUS, theme.FLOAT_RADIUS))
        self.setStyleSheet(rule)

    def mousePressEvent(self, ev):                              # noqa: N802
        """按下**只记位置**（且只在三角按钮里记），不翻转、也**不 accept**。

        ⚠️ 2026-09-15 两次踩坑后的定稿：
        1. 原实现在 press 里直接翻转 + `accept()` → 一按就折、窗还拖不动；
        2. 改成「整条可点 + 抬起判位移」后，用户明确要求**只留三角按钮**。
        现在：只有按在三角里才可能是点击，标题条其余部分纯拖窗口。
        """
        if self._collapsible and ev.button() == Qt.LeftButton:
            pt = ev.position().toPoint()
            if self._in_toggle(pt):
                # ★ 09-27 实锤：三角内的按下必须 **accept**（自己消费）——
                #   以前 ignore 冒泡给窗口，窗口无条件 accept 去做拖动/缩放并
                #   抓走鼠标，release 再也回不到标题条 → 折叠按钮「按了没反应」。
                #   （三角区让位折叠；拖窗/缩放走标题条其余部分和别的边缘。）
                self._press_global = ev.globalPosition()
                ev.accept()
                return
            # 非三角：显式 ignore 冒泡给窗口（拖动/缩放），否则标题条拖不动窗。
            self._press_global = None
            ev.ignore()
            return
        super().mousePressEvent(ev)

    def _in_toggle(self, pos) -> bool:
        """点在不在折叠按钮（左缘三角）里。"""
        return bool(self._collapsible) and pos.x() <= self.TOGGLE_W

    def mouseMoveEvent(self, ev):                               # noqa: N802
        """只有悬在三角上才显示手型；其余部分**不设光标**（继承窗口的，别盖住边缘缩放光标）。"""
        if self._collapsible and not (ev.buttons() & Qt.LeftButton):
            if self._in_toggle(ev.position().toPoint()):
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):                                   # noqa: N802
        self.unsetCursor()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        pos = getattr(self, '_press_global', None)
        self._press_global = None
        if (self._collapsible and ev.button() == Qt.LeftButton
                and pos is not None):
            # ⚠️ 比的是**全局**坐标：窗口跟着鼠标一起动，本地坐标几乎不变，
            #    用本地坐标判会把「拖了 300px」看成「没动」（和卡组格同一个坑）。
            moved = (ev.globalPosition() - pos).manhattanLength()
            if (moved <= CLICK_TOLERANCE
                    and self._in_toggle(ev.position().toPoint())):
                self._expanded = not self._expanded
                self.update()
                self.toggled.emit(self._expanded)
            # 抬起也 **ignore**：窗口要用这一下清 `_drag_from` 并存位置，
            # 被标题条吃掉的话，下次移动会「没有按下也在拖窗」。
            ev.ignore()
            return
        super().mouseReleaseEvent(ev)

    def paintEvent(self, ev):                                   # noqa: N802
        super().paintEvent(ev)                    # 先让样式表背景照常画
        if not self._collapsible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.INK_MUTED))
        cx, cy = theme.S_SM + 2.0, self.height() / 2.0
        r = 3.0
        if self._expanded:                    # ▼ 展开（尖朝下）
            pts = [QPointF(cx - r, cy - r * 0.6),
                   QPointF(cx + r, cy - r * 0.6),
                   QPointF(cx, cy + r * 0.8)]
        else:                                 # ▶ 折叠（尖朝右）
            pts = [QPointF(cx - r * 0.6, cy - r),
                   QPointF(cx + r * 0.8, cy),
                   QPointF(cx - r * 0.6, cy + r)]
        p.drawPolygon(QPolygonF(pts))


class StatsBlock(QWidget):
    """统计 —— **上下两块**（2026-09-15 晚用户定：原是并排，改竖排、**总览在上**）。

    ======  ========  ====================
    块      栏        内容
    ======  ========  ====================
    总览    总胜率    65.6%（下面 21-11 = 胜-负）
            先手率    60.0%（下面 18-12 = 先攻-后攻局数）
    先后攻  先攻      18（下面 胜率 77.8%）
    统计    后攻      12（下面 胜率 41.7%）
    ======  ========  ====================

    每块一条**全宽标题条**，块内两栏等宽、栏间竖发丝线。
    「总览」不可折叠（:class:`BlockTitle`）；「先后攻统计」**可折叠**
    （2026-09-15 用户要求，:class:`SectionTitle` 折叠条 +
    :meth:`set_split_expanded`），折叠状态经 :data:`split_toggled` 广播。
    ⚠️ 先手率用 `first_hand_pct`（= 先攻局数 ÷ **已知先后手的**局数），
    **不是** `initiative_rate`（赢硬币率）—— 用户口径「硬币 ≠ 先后手」。
    """

    OVERVIEW_TITLE = '总览'
    SPLIT_TITLE = '先后攻统计'

    #: 「先后攻统计」标题条被点击折叠/展开（True = 展开）—— 窗口监听它重算总高
    split_toggled = Signal(bool)
    #: 行内我方卡组图被点 → 弹卡组确认条（09-28：自动弹靠轮询太慢）
    my_deck_banner_requested = Signal()

    #: 每栏：`(键, 小标签)` —— 上块 = 总览，下块 = 先后攻统计
    OVERVIEW_COLUMNS = (('win_rate', '总胜率'), ('first_hand', '先手率'))
    SPLIT_COLUMNS = (('first', '先攻'), ('second', '后攻'))

    def __init__(self, snap: Optional[UiSnapshot] = None, parent=None,
                 window_buttons: bool = False):
        super().__init__(parent)
        self.value_labels = {}
        self.sub_labels = {}
        #: 窗口按钮挂在「总览」条右上（用户 2026-09-15 定）；False = 不加（测试/复用）
        self._window_buttons = bool(window_buttons)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # ---- 上块：总览
        # 「总览」顶到窗口上缘 → 要带上圆角，否则方角盖住窗口圆角（两个上角会变方）
        # 2026-09-15：换成 `SectionTitle`（能挂右上那三个窗口按钮；文字仍居中）
        self.overview_title = SectionTitle(tr(self.OVERVIEW_TITLE), top_rounded=True,
                                           buttons=self._window_buttons)
        col.addWidget(self.overview_title)
        col.addWidget(widgets.hairline())
        col.addWidget(self._columns_row(self.OVERVIEW_COLUMNS))
        col.addWidget(widgets.hairline())

        # ---- 下块：先后攻统计（标题条可折叠 —— 2026-09-15 用户要求）
        self._split_expanded = True
        self.split_title = SectionTitle(tr(self.SPLIT_TITLE), collapsible=True)
        self.split_title.toggled.connect(self.set_split_expanded)
        col.addWidget(self.split_title)
        self.split_hair = widgets.hairline()
        col.addWidget(self.split_hair)
        self.split_body = self._columns_row(self.SPLIT_COLUMNS)
        col.addWidget(self.split_body)

        # 高度 = 内容真实需求 —— 别写死：写死过 112 那次的教训是高度不够
        # 会**垂直裁字**；折叠是运行时变高度，每次都要重算
        self._refit()
        if snap is not None:
            self.update_from(snap)

    def _columns_row(self, columns) -> QWidget:
        """一行**等宽栏**，栏间竖发丝线（旧四栏版拆成上下两行）。"""
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(theme.S_XS, theme.S_XS, theme.S_XS, theme.S_XS)
        bl.setSpacing(0)
        for i, (key, cap) in enumerate(columns):
            if i:
                bl.addWidget(widgets.hairline(horizontal=False))
            cell = QWidget()
            bx = QVBoxLayout(cell)
            bx.setContentsMargins(theme.S_XS, 0, theme.S_XS, 0)
            bx.setSpacing(1)

            lab = widgets.label(tr(cap), 'label', Qt.AlignCenter)
            lab.setFixedHeight(lab.fontMetrics().height() + 2)
            bx.addWidget(lab)

            val = widgets.label('-', 'metric', Qt.AlignCenter)
            val.setFixedHeight(val.fontMetrics().height() + 2)
            bx.addWidget(val)

            sub = widgets.label('-', 'label_sm', Qt.AlignCenter)
            sub.setFixedHeight(sub.fontMetrics().height() + 2)
            bx.addWidget(sub)

            self.value_labels[key] = val
            self.sub_labels[key] = sub
            bl.addWidget(cell, 1)
        return body

    def _refit(self) -> None:
        """高度 = 内容真实需求。⚠️ 折叠后 `hidden` 的控件布局自动跳过
        （按 `isHidden()` 判定，与窗口显没显示无关），sizeHint 即新需求。"""
        lay = self.layout()
        lay.activate()
        self.setFixedHeight(lay.sizeHint().height())

    def set_split_expanded(self, expanded: bool) -> None:
        """折叠/展开「先后攻统计」块（标题条三角由 SectionTitle 维护）。

        高度变化经 :data:`split_toggled` 广播 —— 窗口要跟着重算总高，
        不然底下留一块空白（或裁掉最近对局）。
        """
        if expanded == self._split_expanded:
            return
        self._split_expanded = expanded
        self.split_title.set_expanded(expanded)
        self.split_hair.setVisible(expanded)
        self.split_body.setVisible(expanded)
        self._refit()
        self.split_toggled.emit(expanded)

    def is_split_expanded(self) -> bool:
        return self._split_expanded

    def update_from(self, snap: UiSnapshot) -> None:
        """按快照刷新。**没数据就写 `-`，不许编 0**（编了会让人以为真打过 0 场）。"""
        st = snap.stats or {}
        i = _int_or_none

        for key in ('first', 'second'):
            n = i(st.get(key + '_n'))
            rate = st.get(key + '_rate')
            self.value_labels[key].setText(str(n) if n is not None else '-')
            sub = self.sub_labels[key]
            sub.setText(tr('胜率 %s') % theme.fmt_pct(rate))
            sub.setStyleSheet('color: %s;' % theme.win_rate_color(rate))

        wr = st.get('win_rate')
        w, l = i(st.get('n_win')), i(st.get('n_lose'))
        val = self.value_labels['win_rate']
        val.setText(theme.fmt_pct(wr))
        val.setStyleSheet('color: %s;' % theme.win_rate_color(wr))
        self.sub_labels['win_rate'].setText(
            '%d-%d' % (w, l) if (w is not None and l is not None) else '-')

        # 先手率：`first_hand_pct` = 先攻局数 ÷（先攻 + 后攻）—— 不是赢硬币率
        fn, sn = i(st.get('first_n')), i(st.get('second_n'))
        self.value_labels['first_hand'].setText(
            theme.fmt_pct(st.get('first_hand_pct')))
        self.sub_labels['first_hand'].setText(
            '%d-%d' % (fn, sn) if (fn is not None and sn is not None) else '-')

        for key in ('win_rate', 'first_hand'):
            self.sub_labels[key].setStyleSheet(
                'color: %s;' % theme.INK_MUTED)


class MatchHeader(QWidget):
    """表头：`卡组 结果 先/后 段位 对手卡组` —— 列宽与 :class:`MatchRow` 完全一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.HEADER_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, 0, theme.S_SM, 0)
        lay.setSpacing(theme.S_SM - 2)

        # ⚠️ 表头一律**居中在自己的列上**（用户 2026-09-15：「那一行没有居中」）。
        #    之前是「数据怎么对齐、表头就怎么对齐」，但数据里有卡图/徽章那种
        #    视觉重心偏左的东西，表头跟着左对齐看着就散了。
        for cap, width in ((tr('卡组'), deck_col_width()),
                           (tr('结果'), result_col_width()),
                           (tr('先/后'), order_col_width()),
                           (tr('段位'), badge_slot()),
                           (tr('对手卡组'), opp_col_width())):
            align = Qt.AlignCenter
            lab = widgets.label(cap, 'label', align | Qt.AlignVCenter)
            lab.setFixedWidth(width)
            lab.setFixedHeight(lab.fontMetrics().height() + 2)
            lay.addWidget(lab)
        lay.addStretch(0)      # 多余宽度留在右侧（列宽由 column_widths() 说了算）

    def apply_widths(self, widths, right_pad: int = 0) -> None:
        """按窗口宽排一次列宽。

        ★ 表头与行**必须**用同一份 `widths`（有单测锁着）。
        `right_pad` = 滚动条宽度：表头在滚动区**外面**，右边留出等宽的缝，
        列心才能和行里对齐（设计 §4.3）。
        """
        lay = self.layout()
        lay.setContentsMargins(theme.S_SM, 0,
                               theme.S_SM + int(right_pad), 0)
        for i, w in enumerate(widths):
            item = lay.itemAt(i)
            if item is not None and item.widget() is not None:
                wdg = item.widget()
                #: ★ 宽为 0 = 该列被设置隐藏 -> 连控件一起藏（否则留一个列间距的空缝）
                wdg.setVisible(int(w) > 0)
                wdg.setFixedWidth(int(w))


# ----------------------------------------------------------------- 徽章
class RankBadge(QWidget):
    """等级标识：**`[段位图标][罗马数字 I~V]`**（用户 2026-09-14/15 定）。

    * 天梯：段位图标 + 小段位罗马数字
    * 巅峰决斗：只有巅峰图标（它不分小段）
    * 升/掉段那局：**当前（局后）段位 + 方向箭头** —— `[图]II ↑` / `[图]III ↓`
      （2026-09-15 用户定：不再画「旧段 → 新段」对比，太占宽）
    * 认不出段位 → 那一格**空着**（不编）

    ⚠️ **数字用文字画，不要用图**（2026-09-15 定）：游戏那几张 `tier1..5` 蒙版
    分辨率太低（我们只要 ~11px，放大就糊），自己画 SVG 也没必要 —— 罗马数字
    就 5 个字符，字体画最清晰。曾经落过 `data/rank_icons/num_1..5.webp`，已删。
    ⚠️ **数字和箭头同色**（`muted`，= 时间那一列的颜色），升/掉方向不靠颜色表达。
    """

    ICON = BADGE_ICON
    RADIUS = 3
    NUM_SIZE = BADGE_NUM_SIZE

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setFixedWidth(badge_slot())        # 固定槽位 -> 表头能跟行对齐
        # 外层：[弹性][固定宽度的内容组][弹性] —— 组居中，图标就跨行对齐
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        self._group = QWidget()
        #: 组宽**恒为满宽**（`badge_group_width()`）—— 这是「图标跨行对齐」的基准，
        #: 收缩会破坏它（`tests/test_ui_floating.BadgeAlignTest` 锁着）。
        #: 只有图标时（DC / 巅峰）怎么居中见下面 `_side` 的调用点。
        self._group.setFixedWidth(badge_group_width())
        outer.addWidget(self._group)
        outer.addStretch(1)

        lay = QHBoxLayout(self._group)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(BADGE_SPACING)

        icon = row.get('badge_icon')
        delta = row.get('rank_delta')

        # ★ 2026-09-18 五轮（用户口径）：升/掉段画「**动之前那一档**」+ 方向箭头 ——
        #    「白金2升段局：赢了 = `Platinum 2 ↑`，输了 = `Platinum 2`」。
        #    `Platinum 1 ↑` 读作「Platinum 1 又升一段」，而 1 已是最高的那一小段，
        #    语义不成立（看板那边同一天已改成同一条规则）。
        #    2026-09-15 的「只画局后段位」口径随之作废；「旧段 → 新段」对比仍然不画。
        if delta in ('up', 'down'):
            self._side(lay, row.get('rank_icon_before') or row.get('rank_icon_after'),
                       row.get('badge_numeral') or row.get('badge_numeral_after'))
            lay.addStretch(1)             # 数字短时把箭头**顶到组内右缘**（位置恒定）
            lay.addWidget(self._text(ARROW_UP if delta == 'up' else ARROW_DOWN,
                                     self.NUM_SIZE))
        else:
            #: 🔴 **所有行图标一律贴组左缘**（2026-09-15 用户「段位图标没有对齐」
            #:   定下的口径）。2026-10-23 曾试过「只有图标时在组内居中，与表头
            #:   段位对齐」—— 用户实测反馈「**和天梯图标比又有偏右的感觉**」：
            #:   DC/巅峰 图标到列中央，天梯图标贴左，跨行一看就不齐。
            #:   → **撤销居中**，回到图标左缘一致。表头「段位」本来就是整列居中，
            #:   正对「图标+数字」这个组的中心，两者不矛盾。
            self._side(lay, icon, row.get('badge_numeral'))
            if not icon and not row.get('badge_numeral')                     and row.get('rank_pending'):
                #: ★ 段位「获取中」占位（同看板 `_rank_cell`，2026-09-18 晚）
                lay.addWidget(self._text('…'))
            lay.addStretch(1)
        # ⚠️ **左对齐**（不是居中）：居中会按内容宽算起点，于是「带箭头的行」和
        #    「不带的行」图标 x 差到 10px（实测 106/107/112/116）——
        #    用户原话「段位图标没有对齐」。现在图标一律贴槽左缘。

        text = row.get('badge_text') or ''
        if delta in ('up', 'down') and row.get('rank_label_before'):
            text = '%s（%s）' % (row['rank_label_before'],
                                 tr('升段') if delta == 'up' else tr('掉段'))
        if text:
            self.setToolTip(text)          # 想看文字全称就悬停

    def set_slot_width(self, width: int) -> None:
        """段位槽宽（默认按字体实测，见 :func:`badge_slot`）。"""
        if int(width) == self.width():
            return                      # 拖动时每帧都调，没变就别动布局
        self.setFixedWidth(int(width))

    @classmethod
    def _text(cls, s, size=None):
        """罗马数字 / 箭头 —— 统一用 `muted`（和时间同色）。

        🔴 **必须钉死宽度**（2026-10-10 实机事故）：`QLabel` 的默认 size policy
        是 `Preferred` —— 它会**抢占**布局里的剩余空间而不是收缩。徽章组只有
        39px，但不约束宽度时那个 `II` 标签实测被撑到**整行宽（640px）**，
        文字被甩到整行正中间（用户截图：段位列空格里罗马数字飘在行中央）。
        末尾那个 `addStretch` 拦不住 —— stretch 只分配**剩余**空间，
        而剩余空间已经被标签吃光了。
        """
        lab = widgets.label(s, 'muted', Qt.AlignVCenter | Qt.AlignLeft)
        if size:
            lab.setFont(widgets.font(size, QFont.Weight.Medium))
        fm = lab.fontMetrics()
        lab.setFixedHeight(fm.height() + 2)              # 不许比字矮
        #: 宽度按**实测字宽**给死（+2 留边）；空串给 0，不占位
        lab.setFixedWidth((fm.horizontalAdvance(s) + 2) if s else 0)
        lab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return lab

    @classmethod
    def _side(cls, lay, icon, numeral) -> None:
        if icon:
            lab = QLabel()
            lab.setFixedSize(cls.ICON, cls.ICON)
            #: ★ 2026-10-23：改 **不裁切**（`round_pixmap_fit`）—— 段位图标本来是
            #:   方的，裁不裁一样；DC 图标是 2:1 宽扁图，居中裁方会把左右切掉，
            #:   用户实测「图标太小了」。按比例缩放能完整放进 20px 的槽。
            pix = widgets.round_pixmap_fit(icon, cls.ICON)
            if pix is not None:
                lab.setPixmap(pix)
            lay.addWidget(lab)
        if numeral:
            lay.addWidget(cls._text(numeral, cls.NUM_SIZE))


class DeckThumb(QWidget):
    """卡图 + 可选的「未标注」琥珀角标。**不拦鼠标** —— 点击在外层 OppDeckCell。"""

    clicked = Signal()

    def __init__(self, path, known: bool, size: int = theme.THUMB, parent=None,
                 dim: bool = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        # ⚠️ **缺图一律回落到默认图**（2026-09-15 用户：「未知卡组图片没有显示」）。
        # 真库那条路 `DeckImages.resolve(None)` 本来就给默认图，但 demo 数据里
        # `opp_deck_img` 可能是 None → 走到了「空占位」分支。两处口径统一到这里。
        if not path:
            from core.deck_images import DEFAULT_IMAGE
            path = DEFAULT_IMAGE
        # 未标注：半透明默认图 —— 一眼看出「这不是真卡组」。
        # ⚠️ 「建议态」传 `dim=False`（用户 2026-09-16）：那一格画的是**真卡组的图**，
        #    半透明会被当成兜底图。（`!` 角标仍按 `known` 走 —— 它确实还没标注。）
        if dim is None:
            dim = not known
        self._img = widgets.thumb_label(path, size,
                                       opacity=0.5 if dim else 1.0)
        self._img.setParent(self)
        self._img.move(0, 0)
        # 事件全交给外层 OppDeckCell（图不拦鼠标，整格才是按键）
        self._img.setAttribute(Qt.WA_TransparentForMouseEvents)
        if not known:
            d = 11
            dot = QLabel('!', self)
            dot.setFixedSize(d, d)
            dot.setAlignment(Qt.AlignCenter)
            dot.setStyleSheet('background: %s; color: %s; border-radius: %dpx;'
                              % (theme.WARN, theme.CANVAS, d // 2))
            dot.setFont(widgets.font(8, QFont.Weight.Bold))
            dot.move(size - d, 0)              # 贴着内边，别出血（超出会被裁掉）
            dot.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._badge = dot                  # 「未标注」的 `!`（`is_warning_badged()` 要看它）

    def is_warning_badged(self) -> bool:
        """有没有那枚琥珀 `!` 角标（未标注行才有）。

        2026-09-15 用户明确要求「感叹号保留」——所以它得有回归锁（见 test_ui_floating）。
        """
        return getattr(self, '_badge', None) is not None

class MyDeckCell(QFrame):
    """行首「我方卡组」格 —— **只放卡图**（用户 2026-09-16：「做成和之前一样」）。

    * 已知 -> 卡图；
    * **未知** -> 半透明默认图 + **琥珀 `!`**（与对手格同一套角标口径），
      tooltip 指路「打开卡组编辑界面即可自动获取」。

    ⚠️ 宽度**固定 = `COL_MY`（一个卡图宽）** —— 别再往这一列塞文字把它撑宽
    （2026-09-16 试过行内提示：用户当场否掉「做那么宽干嘛」）。
    ⚠️ 它必须与列宽一致：缩了/涨了都会让整行后续列错位（同日踩过）。
    """

    clicked = Signal()

    def __init__(self, path, known: bool, width: int = COL_MY, parent=None):
        super().__init__(parent)
        self.known = bool(known)
        self.setFixedSize(COL_MY, theme.MATCH_ROW_H - theme.S_XS * 2)
        self.setCursor(Qt.PointingHandCursor)   # 09-28：点卡图弹确认条
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        self.thumb = DeckThumb(path, self.known, theme.THUMB)
        lay.addWidget(self.thumb)

        self.label = None                    # 这一列没有文字（留个 None 兼容旧引用）
        if not self.known:
            tip = tr('我方卡组未知 —— 打开「卡组编辑」界面即可自动获取')
            self.setToolTip(tip)
            self.thumb.setToolTip(tip)

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def apply_width(self, total_w: int) -> None:
        """列宽 -> 自己的宽度（**恒等于列宽**，但本列就是 `COL_MY` 这么宽）。"""
        self.setFixedWidth(int(total_w) if total_w else COL_MY)


class OppDeckCell(QFrame):
    """对手卡组**整格按键**（图 + 名字）—— S3 拍板（2026-09-15）。

    * 三态视觉沿用旧格：已标注 = 白字名字（`SURFACE1` 胶囊底）；
    未标注 = 半透明图 + 琥珀「未知卡组」（`WARN_SOFT` 底，2026-09-15 起）；
    * **已标注行也可点** —— 选择器预填当前卡组，选新的覆盖；
    * 按下 → 抬起位移 ≤ :data:`CLICK_TOLERANCE` 才算点击，且事件在本格
      accept 掉（不传给 FloatingWindow 拖窗）——
      修掉「按住卡组格拖窗口，顺手把选择器弹出来」（交接 §6.2 两坑）。
    """

    clicked = Signal()

    def __init__(self, path, known: bool, name: str, player: str,
                 suggest: str = None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(opp_col_width(),
                          theme.MATCH_ROW_H - theme.S_XS * 2)
        self._press = None
        self.setStyleSheet(
            'OppDeckCell { background: %s; border: 1px solid transparent;'
            ' border-radius: %dpx; }'
            'OppDeckCell:hover { border: 1px solid %s; }'
            % (theme.WARN_SOFT if not known else theme.SURFACE1,
               theme.R_SM, theme.HAIRLINE_STRONG))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(2)
        self.suggested = bool(suggest) and not known
        self.thumb = DeckThumb(path, known, COL_OPP_THUMB,
                               dim=False if self.suggested else None)
        self.thumb.clicked.connect(self.clicked)      # 兼容旧信号（正常不走）
        lay.addWidget(self.thumb)

        # ---- 名字：定宽列内**居中** + 按像素省略，悬停看全名
        #（「对手名」列删了（2026-09-15 晚）—— 名字挂在这格的 tooltip 里）
        self.label = widgets.label('', 'body',
                                   Qt.AlignCenter | Qt.AlignVCenter)
        if known:
            deck_text, base_tip = (name or '—'), ''
        elif self.suggested:
            # ★ 用户 2026-09-16 定：未标注 + M5 反推有候选 → 这一格**直接显示
            #   建议的卡组**（真正的图 + 建议名），点一下即标注（不开面板）。
            #   仍算未标注：底色保持琥珀 soft、`!` 角标保留；只是文字用白字
            #   （琥珀字跟「未知卡组」会混淆）。
            deck_text = suggest
            base_tip = tr('建议：%s —— 点一下即确认；置信高且命中多时会自动'
                          '入库') % suggest
            self.label.setStyleSheet('color: %s;' % theme.INK)
        else:
            # D6（2026-09-15 用户定）：未标注那格写「未知卡组」（原「点击标注」），
            # 图仍是半透明兜底图、琥珀 `!` 角标保留。
            deck_text, base_tip = tr('未知卡组'), tr('点击选择对手卡组')
            self.label.setStyleSheet('color: %s;' % theme.WARN)
        self.label.setFixedHeight(self.label.fontMetrics().height() + 2)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.label)
        # 原文与上下文留着 —— 拖宽时要**重新省略**，否则拉宽了名字还是 `…`
        self._deck_text = deck_text
        self._base_tip = base_tip
        self._player = player
        self.apply_width(opp_col_width())

    def apply_width(self, total_w: int) -> None:
        """整格宽度 → 名字列宽（定长省略、悬停看全名）。

        ⚠️ 这一格是**弹性**的（拖宽只改它），所以每次变宽都要重新 elide；
        名字列宽上限不受限，下限 24px（再窄就只剩 `…` 了，没意义）。
        """
        floor = (QFontMetrics(self.label.font())
                 .horizontalAdvance(tr('未知卡组')) + 4)
        name_w = max(floor, 24, int(total_w) - COL_OPP_THUMB - OPP_GAP)
        if name_w == self.label.width():
            return                      # 名字列宽没变 → 连 elide 都不用重算
        self.setFixedWidth(COL_OPP_THUMB + OPP_GAP + name_w)
        self.label.setFixedWidth(name_w)
        shown = QFontMetrics(self.label.font()).elidedText(
            self._deck_text, Qt.ElideRight, name_w)
        self.label.setText(shown)
        tip = self._base_tip or (self._deck_text
                                 if shown != self._deck_text else '')
        if self._player:
            tip = (tip + '\n' if tip else '') + tr('对手：%s') % self._player
        self.label.setToolTip(tip)
        self.setToolTip(tip)

    # ---- 按下/抬起配对：位移超阈值 = 想拖窗，不是点击 ----
    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._press = ev.position()
            ev.accept()                                # 别传给窗口拖动
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):                               # noqa: N802
        if self._press is not None and (ev.buttons() & Qt.LeftButton):
            if ((ev.position() - self._press).manhattanLength()
                    > CLICK_TOLERANCE):
                self._press = None                    # 出阈值：作废这次按下
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        if ev.button() == Qt.LeftButton and self._press is not None:
            moved = (ev.position() - self._press).manhattanLength()
            self._press = None
            if moved <= CLICK_TOLERANCE:
                self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class ResultChip(QLabel):
    """胜/负/平 —— **语义色前景 + soft 底**的小胶囊（DESIGN.md §2）。

    ★ 2026-09-18 晚：这一格**可点**。对手秒投/掉线局采集侧拿不到任何胜负证据
    （任务书 Bug 2），`—` 那几局只能人工补 —— 点它发 `clicked`，`MatchRow`
    转成 `result_pick`，由 app 弹「胜利 / 失败 / 平局 / 清除结果」。
    """

    clicked = Signal()

    def __init__(self, result: Optional[str], parent=None):
        super().__init__(tr(theme.result_label(result)), parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(COL_RESULT, 18)
        self.setFont(widgets.font(theme.F_LABEL, QFont.Weight.Medium))
        self.setStyleSheet('color: %s; background: %s; border-radius: %dpx;'
                           % (theme.result_fg(result),
                              theme.result_soft(result), theme.R_XS))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr('点击修正这一局的胜负（人工修正优先，不会被自动解析覆盖）'))
        self._press = None

    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._press = ev.position()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):                               # noqa: N802
        #: 同 `OppDeckCell`：拖窗时按下会落在这一格上 —— 出门槛就作废这次点击
        if self._press is not None and (ev.buttons() & Qt.LeftButton):
            if (ev.position() - self._press).manhattanLength() > CLICK_TOLERANCE:
                self._press = None
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        if ev.button() == Qt.LeftButton and self._press is not None:
            moved = (ev.position() - self._press).manhattanLength()
            self._press = None
            if moved <= CLICK_TOLERANCE:
                self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class MatchRow(QWidget):
    """一行对局。`row` 是 :func:`core.ui_state.build_recent` 出来的 dict。"""

    #: 点了对手卡组格（已标注行也 emit）—— 带 (row dict, 本行控件)，
    #: 选择器面板要靠**控件的全局坐标**贴到这一行旁边（D1）
    pick_deck = Signal(dict, object)
    #: 未标注 + 有建议 → **点一下直接标注**（用户 2026-09-16 定，不开面板）
    accept_suggest = Signal(dict)
    #: 点了「我方卡组」格（未知时）→ 窗口把提示条亮出来（指路去卡组编辑界面）
    my_deck_clicked = Signal(dict)          # (row dict) —— 行级修正目标
    #: 点了胜负胶囊 → (row dict, 被点的控件)（app 弹修正菜单，任务书 Bug 2）
    result_pick = Signal(dict, object)

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.row = row
        self._widths = None             # 上一次应用的列宽（相同就跳过重排）
        self.setFixedHeight(theme.MATCH_ROW_H)
        known = bool(row.get('opp_known'))
        # 未标注那行给一层**极轻**的琥珀底（5%）—— 不铺饱和色（DESIGN.md 反模式①）
        self.setStyleSheet('background: %s;'
                           % ('rgba(210,153,34,0.05)' if not known
                              else 'transparent'))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.S_SM, theme.S_XS, theme.S_SM, theme.S_XS)
        lay.setSpacing(theme.S_SM - 2)

        # ⚠️ 卡图/胜负胶囊都**包一层容器**：列变宽时它们保持原尺寸居中。
        #    2026-09-15 踩过 —— 卡图写死 26px 没跟着列宽走，拉到最宽时
        #    表头每列 51px、行里还是 26px，于是整行相对表头左移 25px（用户：「全都没有对齐」）。
        # 我方卡组格（用户 2026-09-16）：图 + 名字列；**未知时名字列直接写行内提示**
        # （「打开卡组编辑获取卡组」）—— 不再有独立提示条，也**不开选择器**。
        # ★ BUG-B5-13：「已知」要按**卡组名**判，不能按有没有图 ——
        #   卡组已知但图缺失（自定义图还没放 / 首跑图没抓完）时，
        #   旧代码 `bool(my_deck_img)` 会把这一格画成「未知」：
        #   半透明兜底图 + 琥珀 `!` + tooltip「打开卡组编辑界面」，
        #   用户明明已经识别到卡组却被提示去编辑界面。
        #   （对手那格用的是数据层的 `opp_known`，同口径。）
        _my_deck = row.get('my_deck') or ''
        self.my_deck_cell = MyDeckCell(
            row.get('my_deck_img'), bool(_my_deck))
        self.thumb = self.my_deck_cell.thumb        # 兼容旧引用
        # 09-28：点行内我方卡组格 → 弹选择器修正该局（app 侧接）
        self.my_deck_cell.clicked.connect(
            lambda: self.my_deck_clicked.emit(self.row))
        lay.addWidget(self.my_deck_cell)
        self.result_chip = ResultChip(row.get('result'))
        self.result_chip.clicked.connect(self._on_result_clicked)
        self.chip_host = _centered_host(self.result_chip)
        lay.addWidget(self.chip_host)

        # ---- 先/后（went_first = 实际先攻；不是赢硬币）------
        # 2026-09-15 用户要求：文字 先/后 换成**青眼亚白龙硬币正/反面**
        #（正面 = 先攻、背面 = 后攻）；未知仍显示 `—`。
        # ⚠️ round_pixmap 读不到素材返回 None -> 回退文字，别让这一格空掉
        went = row.get('went_first')
        if went is None:
            self.order_label = widgets.label('—', 'muted', Qt.AlignCenter)
            self.order_label.setFixedHeight(
                self.order_label.fontMetrics().height() + 2)
            tip = ''
        else:
            pix = widgets.round_pixmap(COIN_FRONT if went else COIN_BACK,
                                       COIN_SIZE, radius=COIN_SIZE // 2)
            if pix is not None:
                self.order_label = QLabel()
                self.order_label.setAlignment(Qt.AlignCenter)
                self.order_label.setPixmap(pix)
                self.order_label.setFixedHeight(COIN_SIZE)
                tip = tr('先攻（硬币正面）') if went else tr('后攻（硬币背面）')
            else:
                self.order_label = widgets.label(tr('先') if went else tr('后'),
                                                 'body', Qt.AlignCenter)
                self.order_label.setFixedHeight(
                    self.order_label.fontMetrics().height() + 2)
                tip = tr('先攻') if went else tr('后攻')
        self.order_label.setFixedWidth(COL_ORDER)
        self.order_label.setToolTip(tip)
        lay.addWidget(self.order_label)

        self.badge = RankBadge(row)
        lay.addWidget(self.badge)

        # ---- 对手卡组（最后一列）：**整格一个按键**（图 + 名字，S3 拍板）
        # * 已标注行也可点 —— 选择器预填当前卡组，选新的覆盖；
        # * 点击 = 按下→抬起位移 ≤ CLICK_TOLERANCE（防拖窗误弹选择器）
        # ★ 建议态（用户 2026-09-16）：未标注且 M5 反推有候选 → 那一格画
        #   建议卡组的图 + 建议名，点一下即标注（`_on_opp_cell_clicked` 分派）
        suggest = None if known else (row.get('suggest_name') or None)
        img = row.get('opp_deck_img')
        if suggest:
            img = row.get('suggest_img') or img
        self.opp_cell = OppDeckCell(img, known, row.get('opp_deck') or '',
                                    row.get('opp_player'), suggest=suggest)
        self.opp_cell.clicked.connect(self._on_opp_cell_clicked)
        # 兼容旧引用（测试/外部直接摸 thumb、label）
        self.opp_thumb = self.opp_cell.thumb
        self.opp_deck_label = self.opp_cell.label
        lay.addWidget(self.opp_cell)
        lay.addStretch(0)      # 多余宽度留在右侧（列宽由 column_widths() 说了算）

    def _on_result_clicked(self) -> None:
        """点胜负胶囊 → 发 `result_pick`（app 弹修正菜单；任务书 Bug 2 的兜底）。"""
        self.result_pick.emit(self.row, self.result_chip)

    def _on_opp_cell_clicked(self) -> None:
        """未标注 + 有建议 → **点一下直接标注**；其余（含已标注）开面板。"""
        if getattr(self.opp_cell, 'suggested', False):
            self.accept_suggest.emit(self.row)
        else:
            self.pick_deck.emit(self.row, self)

    def apply_widths(self, widths) -> None:
        """按新的列宽重排本行。**必须**和表头用同一份 `widths`。

        ⚠️ 宽为 0 的列 = 设置里被**隐藏**（用户 2026-10-17 可藏卡组/先后/段位）——
        连控件一起 `hide()`，否则会留一个列间距的空缝。
        """
        if tuple(widths) == self._widths:
            return                       # 没变就别动（拖动时每帧都在重排）
        my_w, res_w, order_w, badge_w, opp_w = widths
        self._widths = tuple(widths)
        self.my_deck_cell.setVisible(int(my_w) > 0)
        self.my_deck_cell.apply_width(int(my_w))
        self.chip_host.setFixedWidth(int(res_w))
        self.order_label.setVisible(int(order_w) > 0)
        self.order_label.setFixedWidth(int(order_w))
        self.badge.setVisible(int(badge_w) > 0)
        self.badge.set_slot_width(int(badge_w))
        self.opp_cell.apply_width(int(opp_w))


# ----------------------------------------------------------------- 主窗
class FloatingWindow(QWidget):
    """常显小窗：**统计 + 最近对局**。"""

    settings_requested = Signal()
    #: 点了「我方卡组」提示条的卡图 → app 侧弹卡组选择器（手动换我方卡组）
    my_deck_pick = Signal(object)
    hide_requested = Signal()
    quit_requested = Signal()
    #: 点了标题条右上的「×」—— **怎么关由 `ui/app.py` 按设置决定**（托盘 / 退出主程序）：
    #: 窗口自己不读设置、也不弹对话框（它只负责把意图发出去）
    close_requested = Signal()
    pick_deck = Signal(dict, object)          # (row dict, 被点的行控件)
    accept_suggest = Signal(dict)             # 点了「建议态」那格：带 row dict
    #: 点了胜负格：带 (row dict, 被点的胶囊)（app 弹修正菜单）
    #: 点了行内我方卡组格 → (row dict) —— app 弹选择器**修正该局**（09-28）
    row_my_deck_pick = Signal(dict)
    result_pick = Signal(dict, object)
    #: 托盘菜单「看板…」—— 与「设置…」同款：**窗口只发意图**，
    #: 开窗/建窗由 `ui/app.py` 做（用户 2026-09-17 拍板入口 = 托盘；
    #: 2026-09-18 起入口从「统计…」换成「看板…」—— S6 统计窗不做，改做看板）
    dashboard_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # ---- 尺寸三态（2026-09-15 用户定）：没拖过 = 内容自适应；拖过 = 用户说了算
        self._user_size: Optional[QSize] = None   # None = 用户从没拖过尺寸
        self._max_width = 0                        # 0 = 不限制；app 用名字池算好注入
        self._pre_max_size: Optional[QSize] = None  # 「□」最大化前记下的尺寸（再点还原）
        self._resizing = False                     # 正在走系统原生缩放
        self._resize_starter = None                # 单测注入钩子（见 begin_resize）
        self._moving = False                       # 正在走系统原生移动
        self._move_starter = None                  # 单测注入钩子（见 begin_move）
        #: 「手势收尾」定时器：系统缩放期间会连发 resizeEvent，停 500ms 就算拖完了。
        #: ⚠️ 为什么不用「比对尺寸」那套判断用户拖没拖：折叠/展开时**布局最小值**会
        #:    自己把窗口改大改小，那也会发 resizeEvent，按尺寸判会把它记成用户尺寸
        #:    （2026-09-15 实测：展开后高度回不来，卡在内容高）。**只认手势期间**才准。
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(500)
        self._resize_timer.timeout.connect(self._end_resize)
        #: 位置落盘的防抖（拖动中不写注册表 —— 系统移动期间收不到 mouseRelease）
        self._pos_timer = QTimer(self)
        self._pos_timer.setSingleShot(True)
        self._pos_timer.setInterval(400)
        self._pos_timer.timeout.connect(self.save_pos)
        #: **重排合并器**：拖动/缩放时 resize 与滚动条 range 事件是连发的，
        #: 每次都同步重排 = 几十次/秒的重排风暴（实测 87 次/秒、30~116 次重绘/秒）。
        #: 改成「标脏 + 本轮事件循环末尾做一次」，一帧最多重排一次。
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(RELAYOUT_MS)
        self._relayout_timer.timeout.connect(self._relayout_columns)
        #: 上一次设过的光标 —— **只在真的变了才 setCursor**
        #: （每帧都设一遍光标，Windows 上会把箭头也闪出来）
        self._cursor_shape = None
        # ★ BUG-B5-15：原来这里有个 `_save_timer`（400ms 单次 + timeout→save_size），
        #   但**全项目没有任何地方 start() 它**（grep 确认）—— 尺寸实际由
        #   `_end_resize()`（松手时）落盘，`resizeEvent` 里的限流重排用的是
        #   `_resize_timer`。死定时器留着容易让人以为「拖动中会自动存」，
        #   已删。要恢复节流落盘就把 `_end_resize` 改成起这个定时器。
        self.setMinimumSize(window_width(), theme.FLOAT_H)
        self._resize_to(window_width(), theme.FLOAT_H)
        self.setWindowTitle(tr('Master Duel 战绩'))
        self._drag_from: Optional[QPoint] = None
        self._snap: Optional[UiSnapshot] = None
        self._rows = []
        self._recent_expanded = True          # 「最近对局」折叠状态
        self._build()
        # ⚠️ 建完就把**空态**铺上再算高度 —— 不然 `rows_host` 是空的，
        #    窗口会停在 `FLOAT_H` 下限（112），在拿到第一份快照前被 show 就裁内容。
        self._render_rows([])
        self._apply_height()
        self._refresh_fonts()
        self._restore_pos()
        self._restore_size()
        self.refresh_deck_banner()        # ★ 启动即检查卡组确认态（用户 2026-09-22 口径）
        self._tray = self._make_tray()    # 托盘：隐藏后唯一恢复入口（Qt.Tool
                                          # 不进任务栏，没托盘点了隐藏就被锁死）

    # ---------------------------------------------------------- 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)          # 1px 留给自绘描边
        root.setSpacing(0)

        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        root.addWidget(inner)
        self._inner = inner
        self._box = box
        self._build_inner()

    def _build_inner(self) -> None:
        """窗口内部全部内容 —— `_build` 首建 + 切语言原地重译共用（2026-10-05）。"""
        box = self._box

        # ---- 区块 1：统计（两块各有自己的标题条，整体顶到窗口圆角）
        self.stats = StatsBlock(window_buttons=True)
        # 标题条右上三个窗口按钮（2026-09-15 用户要求：- / □ / ×）
        for kind, slot in (('min', self.minimize_ui),
                           ('max', self.toggle_maximize),
                           ('close', self.close_requested.emit)):
            self.stats.overview_title.win_buttons[kind].clicked.connect(slot)
        self.stats.split_toggled.connect(self._apply_height)  # 折叠 -> 窗口跟着变矮
        box.addWidget(self.stats)
        box.addWidget(widgets.hairline())

        # ---- 卡组确认提示条（2026-09-22 用户口径）：启动后提示去「卡组编辑」
        #      读卡组；读到后显示卡图等确认；确认后消失（状态变了会再出现）。
        self.deck_banner = DeckConfirmBanner()
        self.deck_banner.confirmed.connect(self._confirm_deck_banner)
        self.deck_banner.pick_requested.connect(self._pick_my_deck)
        self.deck_banner.refresh_requested.connect(self.refresh_deck_banner)
        self.deck_banner.visibility_changed.connect(self._on_banner_visibility)
        box.addWidget(self.deck_banner)
        self._banner_hair = widgets.hairline()
        box.addWidget(self._banner_hair)
        self._banner_hair.hide()          # 构造时提示条默认收起

        # ---- 区块 2：最近对局（标题条可折叠 —— 2026-09-15 用户要求）
        self.recent_title = SectionTitle(tr(TITLE_RECENT), collapsible=True)
        self.recent_title.toggled.connect(self.set_recent_expanded)
        box.addWidget(self.recent_title)
        self.header = MatchHeader()
        box.addWidget(self.header)
        self._recent_hair = widgets.hairline()
        box.addWidget(self._recent_hair)

        self.rows_host = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_host)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(0)
        #: 🔴 **必须顶对齐**（2026-10-10 实机事故）：`QVBoxLayout` 默认会把
        #:   多余空间**均分到每个项目后面**。行区高度恒为 3 行（`MIN_ROWS`），
        #:   而只有 1 局时那唯一的行会被下移一行（实测 y=35、上面空 35px）——
        #:   用户截图：单局却显示在**第二行**，上面一片空白。
        #:   顶部对齐后单局就贴在第一行，往后依次往下排。
        self.rows_lay.setAlignment(Qt.AlignTop)
        # ---- 行区 = **滚动区**（2026-09-15 用户定）：默认露 3 行，往下拖最多同看 10 行，
        #      装不下就滚。⚠️ 表头在滚动区**外面** —— 滚动时它不动（D7），
        #      代价是两条列宽要扣掉滚动条宽度才对得齐（见 `_scrollbar_w`）。
        self.scroll = QScrollArea()
        # 行区不要边框/底色（`theme.qss` 给选择器的 QScrollArea 备了 1px 边框，
        # 那 2px 会让「正好 3 行」也装不下 → 无谓的滚动条）。样式仍写在 theme.qss 里。
        self.scroll.setObjectName('rowsScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setWidget(self.rows_host)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            self._on_scroll_range)
        box.addWidget(self.scroll)

        self._refresh_fonts()
        # ★ 不按住键也要收到 move 事件（否则边缘缩放光标永远不变，见 _enable_mouse_tracking）
        self._enable_mouse_tracking()

    def _refresh_fonts(self) -> None:
        """统一刷一遍字体。

        ⚠️ 兜底但必须有：**祖先控件一旦设样式表，子孙的 `setFont` 会被弹回默认字号**
        （症状 = 数字突然变小、字形发糊，2026-09-14 查了很久）。
        """
        for lab in self.findChildren(QLabel):
            role = lab.property('role')
            if role in ('title', 'label', 'label_sm', 'body', 'muted', 'metric'):
                widgets.apply_role(lab, str(role))

    def _content_height(self) -> int:
        """内容真实需求 —— 也就是高度的**下限**，不手写。

        ⚠️ 别改回写死：高度不够会**垂直裁字**（22px 的 `0` 压扁后只剩中间一条）。
        ⚠️ **显式算，别读顶层 `sizeHint()`**：顶层布局的 sizeHint 是缓存的，
        `invalidate()` / `activate()` / `updateGeometry()` 三种都压不住。
        ⚠️ 行区上限（`MAX_ROWS`）2026-09-15 起是**滚动**解决，所以这里按
        `MIN_ROWS` 行算：一有 10 局就自己长高的话，「拖拽」就被架空了。
        """
        # ⚠️ stats 用 minimumHeight() 不用 height()：折叠时 StatsBlock 先改了
        #    fixed 高度，height() 要等父布局重排才跟上（读到的是旧值）。
        if self._recent_expanded:
            # ⚠️ 行区高度**恒等于 MIN_ROWS 行**（2026-09-15 用户定：默认露 3 行）：
            #    行数少时就留白，行数多时不长高（多出来的靠滚动 / 拖高看）。
            # ⚠️ **别漏掉行间发丝线**：每两行之间 1px，3 行就是 2px。
            #    漏了这 2px，「正好 3 行」也会刚好装不下 → 无谓地冒出滚动条（实测踩过）。
            list_h = theme.MATCH_ROW_H * MIN_ROWS + (MIN_ROWS - 1)
            tail = self.header.height() + 1 + list_h
        else:
            tail = 0
        # ⚠️ 用 `sizeHint()` 别用 `height()`：`show()` 是**排队**的，刚显示完那刻
        #    `height()` 还是 0（布局没跑），按它算高度 = 提示条把行区挤掉 35px
        #    （实测「3 行不出滚动条」红就是这个）。`sizeHint()` 同步可算。
        banner = self.deck_banner.sizeHint().height()             if self.deck_banner.isVisible() else 0
        hair = 1 if self._banner_hair.isVisible() else 0
        return (2                                     # 上下各 1px 描边
                + self.stats.minimumHeight() + 1
                + banner + hair
                + self.recent_title.height() + tail)

    def _all_expanded(self) -> bool:
        """两个可折叠区块是不是都展开着（决定用户高度算不算数）。"""
        return bool(self._recent_expanded) and self.stats.is_split_expanded()

    def _max_height(self) -> int:
        """高度**上限** = 表头 + `MAX_ROWS` 行全部显示（再拉也没内容，只会留白）。

        2026-09-15 用户定：「上下拉伸也要限制，10 局全能显示」。
        行高固定用 `theme.MATCH_ROW_H`（同一句里还要求「保持每行默认行高不变」），
        所以行数上限也就是高度上限。
        """
        if not self._recent_expanded:
            return self._content_height()
        return (2                                     # 上下各 1px 描边
                + self.stats.minimumHeight() + 1
                + self.recent_title.height()
                + self.header.height() + 1
                + theme.MATCH_ROW_H * MAX_ROWS + (MAX_ROWS - 1))

    def set_max_width(self, px: int) -> None:
        """注入宽度**上限**（= 最长的卡组名放得下时的宽度）。

        由 `ui/app.py` 用 `core.ui_state.deck_name_pool()` + `max_window_width()` 算好。
        传 0 = 不限制（测试 / 没有名字池时）。
        """
        self._max_width = max(int(px or 0), window_width()) if px else 0
        if self._max_width:
            self.setMaximumWidth(self._max_width)
        self._clamp_size()

    def _clamp_size(self) -> None:
        """把当前尺寸夹进「下限、上限」之间（上限见 `_max_height` / `_max_width`）。"""
        cap_w = self._max_width or SIZE_MAX
        cap_h = self._max_height()
        w, h = self.width(), self.height()
        if w > cap_w or h > cap_h:
            self.resize(min(w, cap_w), min(h, cap_h))
            if self._user_size is not None:
                self._user_size = self.size()

    def _enter_user_size_mode(self) -> None:
        """第一次真正拖尺寸时调用：解除固定尺寸，转成「用户说了算」。"""
        self.setMinimumSize(window_width(), self._content_height())
        self.setMaximumSize(self._max_width or SIZE_MAX, self._max_height())

    def _apply_height(self) -> None:
        """高度三态（2026-09-15 用户定，设计文档 §4.1）。

        * **没拖过** → 完全保持改版之前的行为：`setFixedHeight(内容高)`
        * **拖过 + 两个区块都展开** → 保持用户高度，内容只当**下限**
        * **任一区块折叠** → 缩到内容高（否则底下留一大块空洞）；
          再全展开时用回 `_user_size`
        """
        content = self._content_height()
        if self._user_size is None or not self._all_expanded():
            self._resize_to(self.width(), content, fixed=True)
            return
        self.setMinimumHeight(content)
        self.setMaximumHeight(self._max_height())    # 最多拉到「10 行全显示」
        self._resize_to(self.width(),
                        max(content, self._user_size.height()))

    def _resize_to(self, w: int, h: int, fixed: bool = False) -> None:
        """**我们自己**改尺寸。

        ⚠️ 顺手把 `_resizing` 清掉：程序一接管尺寸，就不再是「用户手势」了 ——
        否则折叠时那次 resize 会被当成用户拖的，把用户高度覆盖成内容高。
        """
        self._resizing = False
        if fixed:
            self.setFixedHeight(int(h))
        else:
            self.resize(int(w), int(h))

    # ---------------------------------------------------------- 自绘
    def paintEvent(self, ev):                                   # noqa: N802
        """整窗圆角 + 1px 发丝描边（透明底窗口靠 QSS 圆角不够）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(theme.FLOAT_ALPHA)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                            theme.FLOAT_RADIUS, theme.FLOAT_RADIUS)
        p.fillPath(path, QColor(theme.CANVAS))
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))            # ≈ hairline-strong
        p.drawPath(path)

    # ---------------------------------------------------------- 拖动
    def mousePressEvent(self, ev):                              # noqa: N802
        if ev.button() == Qt.LeftButton:
            edges = self.edges_at(ev.position().toPoint())
            if edges:
                self.begin_resize(edges)
                ev.accept()
                return
            if self.begin_move():
                ev.accept()
                return
            # 兜底：拿不到系统移动（无头 / 老平台）才自己算位移
            self._drag_from = (ev.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())
            ev.accept()

    def mouseMoveEvent(self, ev):                               # noqa: N802
        if self._drag_from is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_from)
            ev.accept()
            return
        if not (ev.buttons() & Qt.LeftButton):
            self._update_cursor(ev.position().toPoint())

    def leaveEvent(self, ev):                                   # noqa: N802
        """移出窗口就把缩放光标清掉（别把「横向缩放」带到桌面上）。"""
        if self._cursor_shape is not None:
            self._cursor_shape = None
            self.unsetCursor()

    def _update_cursor(self, pos) -> None:
        """按鼠标位置换光标（抽成方法，方便单测直接问）。

        ⚠️ **只在形状真的变了才 `setCursor`** —— 鼠标移动是每帧都来的，
        每帧设一遍光标在 Windows 上会把光标一起闪花。
        """
        shape = cursor_for(self.edges_at(pos))
        if shape != self._cursor_shape:
            self._cursor_shape = shape
            self.setCursor(shape)

    def _enable_mouse_tracking(self) -> None:
        """让**窗口和所有子控件**都收 move 事件 —— 否则边缘光标根本不会变。

        ⚠️ Qt 默认 `mouseTracking=False`：**不按住键就不发 move 事件**，
        所以「移到边缘换缩放光标」永远不触发（2026-09-15 用户实测：「光标没有变」）。
        子控件（label / 行 / 滚动区 / 滚动条）也一样要开 —— 鼠标在它们上面时
        事件得先到它们手里、再冒泡上来。
        """
        self.setMouseTracking(True)
        for w in self.findChildren(QWidget):
            w.setMouseTracking(True)

    def mouseReleaseEvent(self, ev):                            # noqa: N802
        if self._resizing:
            self._end_resize()
            return
        if self._drag_from is not None:
            self._drag_from = None
            self.save_pos()

    # ---------------------------------------------------------- 缩放（系统原生）
    def edges_at(self, pos):
        """窗口坐标 → 命中的边（空 = 窗中间，走**拖动移动**而不是缩放）。"""
        m = RESIZE_MARGIN
        e = Qt.Edges()
        if pos.x() <= m:
            e |= Qt.LeftEdge
        if pos.x() >= self.width() - m:
            e |= Qt.RightEdge
        if pos.y() <= m:
            e |= Qt.TopEdge
        if pos.y() >= self.height() - m:
            e |= Qt.BottomEdge
        return e

    def begin_resize(self, edges) -> bool:
        """进入缩放态并交给**系统**处理。

        ⚠️ 走 `QWindow.startSystemResize()`（Qt 6 有）而不是自己算像素 ——
        拖动才跟手，跨 DPI / 多屏也不会缩歪（设计 D5）。
        `_resize_starter` 是单测注入用的钩子，生产路径是 :meth:`_system_resize`。
        """
        self._enter_user_size_mode()
        self._resizing = True
        starter = self._resize_starter or self._system_resize
        try:
            ok = bool(starter(edges))
        except Exception:
            ok = False
        # ★ BUG-B5-07：失败路径**必须复位** `_resizing`（`begin_move` 有，这里漏了）。
        #   而 `mousePressEvent` 是「先 begin_resize 再无条件 `ev.accept(); return`」——
        #   即边缘点击失败时事件已被吃掉，`_resizing` 又卡在 True，
        #   那个角落就成了**死区**（点什么都没反应）。
        #   且 `resizeEvent` 会把布局引起的 resize 记成「用户尺寸」。
        if not ok:
            self._resizing = False
        return ok

    def _system_resize(self, edges) -> bool:
        handle = self.windowHandle()
        if handle is None or not hasattr(handle, 'startSystemResize'):
            return False
        return bool(handle.startSystemResize(edges))

    def begin_move(self) -> bool:
        """开始拖窗 —— 走**系统原生** `QWindow.startSystemMove()`。

        ⚠️ 2026-09-15 用户实测：「快速拖动 UI 会闪烁」。根因是原来自己算位移
        每帧 `move()` 一个**半透明无边框**窗 —— 每次 `SetWindowPos` 都要重新合成，
        快了就闪。缩放早就是走系统的（`startSystemResize`），所以不闪；
        移动也得交给系统，跟手而且不闪。
        返回 False = 系统不支持，调用方回退到手动 `move()`（无头环境/老平台）。
        """
        self._moving = True
        starter = self._move_starter or self._system_move
        try:
            ok = bool(starter())
        except Exception:
            ok = False
        if not ok:
            self._moving = False
        return ok

    def _system_move(self) -> bool:
        handle = self.windowHandle()
        if handle is None or not hasattr(handle, 'startSystemMove'):
            return False
        return bool(handle.startSystemMove())

    def _end_resize(self) -> None:
        """一次缩放手势收尾：清标记 + **补一次精确重排** + 落盘尺寸。

        ⚠️ 手势期间重排是限流的（见 `_schedule_relayout`），所以松手时
        必须按最终尺寸再对齐一次，否则表头与行会停在限流那一刻的列宽上。
        """
        self._resizing = False
        self._relayout_columns()
        self.save_size()

    def contextMenuEvent(self, ev):                             # noqa: N802
        """右键菜单 = **托盘右键同款**（2026-10-03 用户口径）：
        显示/隐藏悬浮窗 + 设置… + 看板… + 退出。

        ★ BUG-B5-06：`remember=False` —— 右键是**临时菜单**，
        不许覆盖托盘那份 `_act_*` 引用（否则托盘文案不再同步）。
        """
        menu = QMenu(self)
        self._fill_menu(menu, remember=False)
        menu.exec(ev.globalPos())

    def _fill_menu(self, menu, remember: bool = True):
        """托盘 / 悬浮窗右键**共用**的菜单项（同一条实现，别写两份）。

        ★ BUG-B5-06：`remember` 参数 —— 只有**建托盘**时才记 `_act_*` 引用。
        原来每次右键都新建菜单并覆盖 `_act_settings/_act_dash/_act_quit`，
        于是 `_sync_tray_text()`（showEvent/hideEvent 都调）从此改的是
        **右键临时菜单**的动作文案 —— 托盘菜单的「显示/隐藏悬浮窗」
        不再跟着变，一直停在建托盘那一刻的文案。
        另外每次右键 `QMenu(self)` 有父对象、不会被回收，点 N 次攒 N 个菜单。
        """
        act_toggle = QAction(tr('显示悬浮窗') if self.isHidden()
                             else tr('隐藏悬浮窗'), menu)
        act_toggle.triggered.connect(self._toggle_visible)
        menu.addAction(act_toggle)
        act_settings = QAction(tr('设置…'), menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(act_settings)
        act_dash = QAction(tr('看板…'), menu)
        act_dash.triggered.connect(self.dashboard_requested.emit)
        menu.addAction(act_dash)
        menu.addSeparator()
        act_quit = QAction(tr('退出'), menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)
        if remember:
            # ★ 记下引用 —— 切语言原地重译时刷文案（2026-10-05）
            self._act_settings = act_settings
            self._act_dash = act_dash
            self._act_quit = act_quit
        return act_toggle

    # --------------------------------------------------- 托盘（常驻入口）
    def _make_tray(self):
        """系统托盘图标（2026-09-15，用户点「隐藏」后被锁死过一次）。

        「隐藏」只 hide 窗口 —— Qt.Tool 不进任务栏、进程还活着继续采数据，
        没有托盘就**没有任何恢复入口**。左键单击 = 显示/隐藏切换；
        右键菜单 = 显示/隐藏 + 设置 + 退出（与悬浮窗右键同源）。
        无系统托盘（CI / 无头）返回 None，一切照旧。
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self._tray_icon(), self)
        tray.setToolTip(tr('Master Duel 战绩悬浮窗'))
        menu = QMenu()
        self._act_toggle = self._fill_menu(menu)   # ★ 与悬浮窗右键同一条实现
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    @staticmethod
    def _tray_icon():
        """托盘图标 = 应用图标（看板任务栏同款，见 :func:`md_app_icon`）。"""
        return md_app_icon()

    def _toggle_visible(self):
        if self.isHidden():
            self.show()
        else:
            self.hide()

    # ----------------------------------------------------- 标题条三个按钮
    def minimize_ui(self) -> None:
        """「-」：**隐藏到任务栏托盘**。

        窗口是 `Qt.Tool`（不进任务栏），所以「最小化」= `hide()` ——
        进程继续采数据，托盘图标是唯一恢复入口（2026-09-15 用户点过「隐藏」被锁死过一次，
        托盘就是那次的产物）。
        """
        self.hide()

    def toggle_maximize(self) -> None:
        """「□」：拉到**尺寸上限**（最宽 = 最长卡组名、最高 = 10 行）；再点还原。"""
        if self._pre_max_size is not None:
            size = self._pre_max_size
            self._pre_max_size = None
            self._enter_user_size_mode()
            self._user_size = QSize(size)
            self._resize_to(size.width(), size.height())
            return
        self._pre_max_size = self.size()
        self._enter_user_size_mode()
        self._user_size = QSize(self._max_width or self.width(),
                                self._max_height())
        self._resize_to(self._user_size.width(), self._user_size.height())

    def is_maximized(self) -> bool:
        """当前是不是「最大化」态（测试/按钮态用）。"""
        return self._pre_max_size is not None

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:         # 左键单击
            self._toggle_visible()
        elif reason == QSystemTrayIcon.DoubleClick:   # 双击 = 打开看板
            self.dashboard_requested.emit()

    def retranslate(self) -> None:
        """切语言**原地重译**（2026-10-05 用户：窗口不关、同一界面直接变）。

        内部控件整体销毁重建（tr 是构造期烘进控件的），窗口几何/折叠态/
        当前快照保留；托盘菜单与窗口标题文案一并刷。
        重建阻塞主线程 —— 套 `widgets.run_with_busy`：旋转 loading 遮罩
        先画一帧再干活，结束后至少停留 400ms（2026-10-05 用户）。
        """

        def _work():
            split = self.stats.is_split_expanded()
            while self._box.count():
                item = self._box.takeAt(0)
                w = item.widget()
                if w is not None:
                    # ★ 先 hide 再脱离（同 dashboard：修重影 + 不闪顶层框）
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            self._rows = []
            self._build_inner()
            self._refresh_fonts()
            self._enable_mouse_tracking()
            QApplication.processEvents()   # 段间喘气 —— 弧线在段间继续跳
            # ---- 状态回填：折叠态 / 数据 / 确认条 / 高度
            self.set_recent_expanded(self._recent_expanded)
            if not split:
                self.stats.set_split_expanded(False)
            if self._snap is not None:
                self.set_snapshot(self._snap)
            else:
                self._render_rows([])
            self.refresh_deck_banner()
            self._apply_height()
            # ---- 窗口标题 / 托盘文案
            self.setWindowTitle(tr('Master Duel 战绩'))
            if getattr(self, '_act_toggle', None) is not None:
                self._sync_tray_text()
                self._act_settings.setText(tr('设置…'))
                self._act_dash.setText(tr('看板…'))
                self._act_quit.setText(tr('退出'))
                self._tray.setToolTip(tr('Master Duel 战绩悬浮窗'))

        widgets.run_with_busy(self, _work)

    def _sync_tray_text(self):
        act = getattr(self, '_act_toggle', None)
        if act is not None:
            act.setText(tr('显示悬浮窗') if self.isHidden() else tr('隐藏悬浮窗'))

    # ----------------------------------------------------- 卡组确认条
    def _pick_my_deck(self, anchor=None) -> None:
        """点了提示条的卡图 → **弹卡组选择器**（app 侧接；浮窗只发信号）。"""
        self.my_deck_pick.emit(anchor)

    def _confirm_deck_banner(self) -> None:
        """点了「确认」：把当前卡组状态记成已确认（QSettings），提示条收起。"""
        from core import my_deck
        from ui.deck_banner import deck_state_fingerprint
        fp = deck_state_fingerprint(my_deck.load())
        if fp:
            self._settings().setValue(CONFIRM_KEY, fp)
        self.deck_banner.hide()
        self._banner_hair.hide()
        self._apply_height()

    def _on_banner_visibility(self, visible: bool) -> None:
        """提示条显隐 → 发丝线跟着显隐 + 高度重算（提示条有自己的高度）。"""
        self._banner_hair.setVisible(visible)
        self._apply_height()

    def refresh_deck_banner(self, img_resolver=None) -> str:
        """按 `my_deck.json` 当前状态刷提示条（**窗口每轮启动 + 每次快照**都调）。

        返回模式（hidden/waiting/confirm）—— 单测直接锁。
        """
        from core import my_deck
        state = my_deck.load()
        img = ''
        if state.get('name') and callable(img_resolver):
            try:
                img = img_resolver(state['name']) or ''
            except Exception:                                # noqa: BLE001
                img = ''
        mode = self.deck_banner.update_state(
            state, str(self._settings().value(CONFIRM_KEY) or ''), img)
        # 显隐联动由 visibility_changed 信号驱动（_on_banner_visibility 里重算高度）
        # ⚠️ 提示条内部会**重建控件**（换图）—— 新控件要补 mouseTracking，
        #    否则鼠标事件到不了窗口（边缘光标/拖动失灵，2026-09-15 踩过同款）。
        self._enable_mouse_tracking()
        return mode

    def showEvent(self, ev):                                    # noqa: N802
        super().showEvent(ev)
        self._sync_tray_text()
        # ★ 2026-09-22：卡组确认条在**构造期**（窗口还没 show）就 set_visible 过，
        #   那时 `isVisible()` 因父窗未显而恒 False → `_content_height` 算出的是
        #   「没有提示条」的高度，show 之后就少了 35px（行区被挤 → 凭空出滚动条）。
        #   show 这一刻可见性才真实 —— 重算一次。
        self._apply_height()

    def hideEvent(self, ev):                                    # noqa: N802
        super().hideEvent(ev)
        self._sync_tray_text()

    # ----------------------------------------------------- 位置持久化
    @staticmethod
    def _settings() -> QSettings:
        return QSettings('masterduel', 'M7')

    def save_pos(self) -> None:
        self._settings().setValue('float/pos', self.pos())

    def save_size(self) -> None:
        """记下用户拖出来的尺寸（重启保持，2026-09-15 用户定）。"""
        self._settings().setValue('float/size', self.size())

    def moveEvent(self, ev):                                    # noqa: N802
        """位置变了 → 防抖落盘。

        ⚠️ 走系统原生移动（`begin_move`）时我们**收不到 mouseRelease**，
        所以不能只在 `mouseReleaseEvent` 里存位置，否则拖完直接退出会丢位置。
        """
        super().moveEvent(ev)
        timer = getattr(self, '_pos_timer', None)      # __init__ 早期可能还没建
        if timer is not None:
            timer.start()

    def _restore_size(self) -> None:
        """恢复上次拖出来的尺寸。

        ⚠️ **小于下限就夹到下限**（旧版本存的、或换了字体之后下限变大的，
        都可能比现在小）。异常值直接丢弃、回落「没拖过」的默认态。
        """
        size = self._settings().value('float/size')
        if not isinstance(size, QSize):
            return
        w = max(int(size.width()), window_width())
        h = max(int(size.height()), self._content_height())
        if w <= 0 or h <= 0:
            return
        self._user_size = QSize(w, h)
        self._enter_user_size_mode()
        self._resize_to(w, h)

    def resizeEvent(self, ev):                                  # noqa: N802
        """尺寸变了 → 记用户尺寸 + 重排列宽（拖宽只有这里能生效）。

        ⚠️ `_build()` 之前也可能收到 resize，那时 `header` 还没建好，要 guard。
        """
        super().resizeEvent(ev)
        if self._resizing:
            # **只在手势期间**记用户尺寸（布局最小值引起的 resize 不算，见 __init__ 注释）
            self._user_size = self.size()
            self._resize_timer.start()
            self._clamp_size()          # 拖过头就拉回上限（宽度上限见 set_max_width）
        if getattr(self, 'header', None) is not None:
            self._schedule_relayout()

    def _restore_pos(self) -> None:
        pos = self._settings().value('float/pos')
        # ★ BUG-B5-15：改判「还在屏幕上」（原来只挡左上负坐标）
        if pos_on_screen(self, pos):
            self.move(pos)
        else:                                        # 首次：右上角内侧
            screen = self.screen().availableGeometry()
            self.move(screen.right() - self.width() - 24, screen.top() + 24)

    # ---------------------------------------------------------- 更新
    def set_snapshot(self, snap: UiSnapshot) -> None:
        self._snap = snap
        self.stats.update_from(snap)
        self._render_rows(snap.recent or [])
        self.refresh_deck_banner()          # 卡组确认条（状态变了会重新出现）
        self._apply_height()
        self._refresh_fonts()

    def set_recent_expanded(self, expanded: bool) -> None:
        """折叠/展开「最近对局」（标题条三角由 SectionTitle 维护）。

        折叠后标题条变成**窗底**，要补下圆角（不然 SURFACE1 方角戳出圆角窗口）。
        """
        if expanded == self._recent_expanded:
            return
        self._recent_expanded = expanded
        self.recent_title.set_expanded(expanded)
        self.header.setVisible(expanded)
        self._recent_hair.setVisible(expanded)
        self.scroll.setVisible(expanded)      # 行区住在滚动区里
        self.recent_title.set_corner_rounding(bottom=not expanded)
        self._apply_height()

    def _clear_rows(self) -> None:
        """清空行区 —— **必须清整个布局**，不能只清 `self._rows`。

        ⚠️ 踩过：分隔线（`hairline()`）不在 `self._rows` 里，只清行的话它们会**越堆越多**
        （3 行时布局里攒到 6 个控件）。
        """
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._rows = []

    def _render_rows(self, recent) -> None:
        # ⚠️ 每个 addWidget 之后必须**立即 show()**：新建的行控件挂上之前是
        #    无父的顶层窗口（isHidden()=true），addWidget 挂到**已可见**的
        #    rows_host 时 Qt 不会同步 show（要等事件循环补）—— 在这之前
        #    QWidgetItem::isEmpty() 把这些隐藏 item 当空、整个行区
        #    sizeHint() 读 0 → 窗口塌到 156、内容挤压叠绘
        #    （2026-09-15 用户桌面实拍，复现 tmp/_repro_3_after_refresh.png；
        #    第一份快照恰好赶上 win.show() 整链 showChildren 所以没暴露）。
        self._clear_rows()
        # ★ 对手卡组列宽 = 当前可见名字实测（贴着字右边，2026-10-05 用户）
        self._opp_name_w = opp_name_width(recent)
        # ⚠️ 行是**新建**的（列宽都还是构造默认值）→ 必须让 `_relayout_columns()`
        #    真跑一遍，别被「列宽没变就跳过」的缓存骗过去（2026-09-15 踩过：
        #    新建的行停在默认列宽，表头与行又错开）。
        self._last_layout = None
        if not recent:
            empty = widgets.label(tr('还没有对局记录'), 'muted', Qt.AlignCenter)
            # 空态占 **MIN_ROWS** 行高（别按 MAX_ROWS —— 上限一改 10，空窗就被撑爆）
            empty.setFixedHeight(theme.MATCH_ROW_H * MIN_ROWS)
            self.rows_lay.addWidget(empty)
            empty.show()
            self._rows.append(empty)
            return
        for i, row in enumerate(recent[:MAX_ROWS]):
            if i:
                hair = widgets.hairline()
                self.rows_lay.addWidget(hair)
                hair.show()
            w = MatchRow(row)
            w.pick_deck.connect(self.pick_deck.emit)
            w.accept_suggest.connect(self.accept_suggest.emit)
            w.result_pick.connect(self.result_pick.emit)
            w.my_deck_clicked.connect(self.row_my_deck_pick.emit)
            self.rows_lay.addWidget(w)
            w.show()
            self._rows.append(w)
        self._relayout_columns()
        # 新建的行也要开 mouseTracking（否则鼠标落在行上时事件到不了窗口）
        self._enable_mouse_tracking()

    def _relayout_columns(self) -> None:
        """窗口宽变了（或滚动条出现/消失）→ 表头与所有行一起改列宽。

        ★ 表头与行**必须**用同一份 widths —— 两边各算一次必然错开（有单测锁着）。
        """
        if getattr(self, 'header', None) is None:      # _build() 之前
            return
        sb = self._scrollbar_w()
        widths = column_widths(available_for_cols(self.width(), sb),
                               opp_name_w=getattr(self, '_opp_name_w', None),
                               hidden=float_hidden_cols())
        if (widths, sb) == getattr(self, '_last_layout', None):
            return                       # 列宽没变就别重排（拖动/滚动时每帧都会调）
        self._last_layout = (widths, sb)
        self.header.apply_widths(widths, right_pad=sb)
        for row in self.row_widgets():
            row.apply_widths(widths)

    def _scrollbar_w(self) -> int:
        """滚动条**实际**占的宽（不可见就是 0）。

        表头在滚动区外面，右边要留同样宽的缝，列心才和行里对得上（设计 §4.3）。
        """
        area = getattr(self, 'scroll', None)
        if area is None:
            return 0
        bar = area.verticalScrollBar()
        if bar is None or not bar.isVisible():
            return 0
        return int(bar.sizeHint().width())

    def _on_scroll_range(self, _lo: int, _hi: int) -> None:
        """滚动条出现/消失 → 表头与行一起重排列宽（否则两边差一个滚动条宽）。

        🔴 2026-10-18 修的时序 bug：`rangeChanged` 发出时滚动条**还没 show()**，
        这一刻 `_scrollbar_w()` 仍返回 0 -> 按「没有滚动条」排完就再没人管，
        表头/行一直比内容宽 14px（实测 `_last_layout` 卡在 `sb=0`）。
        所以这里**立刻排一次**（保证布局不为空），再**等一轮事件循环**后补一次
        精确的（那时滚动条已可见，能读到真实宽度）。
        """
        self._schedule_relayout()
        QTimer.singleShot(0, self._relayout_after_scrollbar)

    def _relayout_after_scrollbar(self) -> None:
        """滚动条显隐**稳定后**再对齐一次（见 `_on_scroll_range` 的时序说明）。"""
        if getattr(self, 'header', None) is None:
            return
        t = getattr(self, '_relayout_timer', None)
        if t is not None and t.isActive():
            return                      # 手势中：让限流那条路自己收尾
        sb_now = self._scrollbar_w()
        last = getattr(self, '_last_layout', None)
        if last is not None and last[1] == sb_now:
            return                      # 已经按这个滚动条宽排过了
        self._last_layout = None        # 滚动条宽变了 -> 强制重排
        self._relayout_columns()

    def _schedule_relayout(self) -> None:
        """重排**限流**：手势期间最多每 `RELAYOUT_MS` 一次，手势结束再补一次精确的。

        ⚠️ 2026-09-15 两次实测：
        1. 一开始是每次 resize 就**同步重排** → 87 次/秒 + 30~116 次重绘/秒（闪烁）；
        2. 改成「0ms 单次定时器合并」**没用** —— 系统把每个 resize 事件放在
           **各自的事件循环轮次**里送达，0ms 定时器每次都立刻执行，等于没合并
           （实测仍是 55~73 次/秒）。
        现在：定时器**已在跑就不重置**（经典限流），所以窗口内的多次 resize 只会
        触发一次重排；抬手时由 `_end_resize` 补一次精确对齐。
        """
        timer = getattr(self, '_relayout_timer', None)
        if timer is None:
            return
        if not self._resizing:
            self._relayout_columns()        # 非手势（快照/滚动条变化）：立刻做
            return
        if not timer.isActive():            # ★ 已在跑就不重置 = 限流
            timer.start()

    def row_widgets(self):
        """当前的行控件（跳过分隔线）—— 单测用。"""
        return [w for w in self._rows if isinstance(w, MatchRow)]


def md_app_icon():
    """应用图标（托盘 / 看板任务栏同款）：深色圆角底 + 红色 MD 字样。

    自绘 64x64，零素材依赖、不碰版权图。2026-10-03 起看板窗口也用它
    （用户：任务栏图标和托盘一致）。
    """
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor('#2b2f3a'))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor('#e6403c'))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(26)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, 'MD')
    p.end()
    return QIcon(pm)
