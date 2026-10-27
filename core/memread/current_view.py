# -*- coding: utf-8 -*-
"""当前界面（viewStack）只读 —— 「现在在哪个界面」的权威来源。

为什么需要它
------------
判断「在不在对局、在哪个阶段」现在靠**解析 Untapped 写的日志**（
``tools/archive_matches.py`` 的 ``RE_ENTRY`` 正则 + ``classify_mode``）。
问题：① 依赖 untapped 装在同机；② 只能拿到「进入对局那一次」的跳转快照，
不是实时状态；③ 正则脆弱，untapped 换格式就挂。

游戏自己有一个**视图栈**：``YgomSystem.UI.ViewControllerManager.viewStack``，
栈顶就是当前界面，栈里每个元素是 ViewController 子类（``HomeViewController``
/ ``DuelStartViewController`` / ``DuelResultViewController`` …）。
这就是 untapped 的 ``ScryYgomCurrentView`` 用的同一份数据。

链路（**2026-10-28 实机打通**，游戏主界面）
------------------------------------------
::

    YgomGame.Menu.ContentViewControllerManager.Instance   (静态单例，类字段 +0x0)
      -> +0x48  viewStack        List<ViewController>    ★ 继承自父类
      -> 每项 = ViewController 对象，其对象头 -> TypeInfo -> name

⚠️ **`viewStack` 不在 `ContentViewControllerManager` 自己的字段表里**
（``list_fields`` 只读本类字段，不跟继承）。它是父类
**``YgomSystem.UI.ViewControllerManager``** 的字段，偏移 **+0x48**。
定位过程（可复现）：在 metadata 字符串表里找 ``viewStack`` →
算出进程内地址 → 全进程扫谁引用这个指针 → 只 1 处命中（FieldInfo.name）→
读该 FieldInfo 的 offset（+0x48）→ 反推 TypeInfo 得到父类名。

实测（2026-10-28 主界面）::

    ContentViewControllerManager.Instance = 0x19834E67BB0
    viewStack(+0x48)                      = 0x19834EAFE70
    长度 = 1   [0] HomeViewController

⚠️ 类名**不带命名空间**（``HomeViewController`` 而不是
``YgomGame.Menu.HomeViewController``）—— 与 untapped 的判据完全一致
（它也是拿 ``className`` 直接比 / ``startsWith``）。

红线
----
* 全程只读（``SafeProcess`` 权限位硬编码 0x0410）
* 只读 ``Instance`` / ``viewStack`` 两个位置，不整块读实例
* 视图栈为空 / 类名读不出 -> 返回 ``None``，**不猜**
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

from .il2cpp import find_singleton, find_typeinfo, list_fields, read_singleton_slot
from .il2cpp_obj import MIN_ADDR, class_name_of, list_items

#: 管理器类（**实机确认的命名空间** —— 不是空、不是 YgomSystem）
MANAGER_NS = 'YgomGame.Menu'
MANAGER_NAME = 'ContentViewControllerManager'

#: ``viewStack`` 在实例里的字节偏移 —— **实机校准 2026-10-28**（主界面）。
#: 它是父类 ``YgomSystem.UI.ViewControllerManager`` 的字段，
#: 所以**不会被 ``list_fields(子类)`` 返回**，只能按偏移读。
#: ⚠️ 游戏大版本更新后若读不到，先跑 ``tmp/probe_current_view.py`` 重新校准。
VIEW_STACK_OFFSET = 0x48

#: 单例槽位：``Instance`` 是**类字段**（static），实测偏移 +0x00。
INSTANCE_SLOT = 0x00

#: 「在对局内」的类名前缀（untapped 同口径：``startsWith("YgomGame.Duel.")``）。
#: ⚠️ 但实测栈里存的是**短类名**（不带命名空间），所以这里两种都认。
DUEL_PREFIX_LONG = 'YgomGame.Duel.'
DUEL_PREFIX_SHORT = 'Duel'

#: 结算界面类名（短名 —— 实测栈里是短名）
RESULT_VIEWS = frozenset({
    'DuelResultViewController',
    'ColosseumDuelResultViewController_Rate',
})

#: **匹配中**的界面类名（2026-10-28 加）。
#: 实测来源：`docs/日志内容清单.md` 记录的真实界面跳转
#: ``ColosseumInfoViewController -> PvpMenuMatchingViewController``（开始匹配）
#: ``PvpMenuMatchingViewController -> DuelStartViewController``（匹配成功）。
#: ⚠️ 这是**短名**；也认长名（``YgomGame.PvpMenu.PvpMenuMatchingViewController``）。
MATCHING_VIEWS = frozenset({
    'PvpMenuMatchingViewController',
    'PvpMatchingViewController',
})


class CurrentView(NamedTuple):
    """一帧界面状态。读不到的一律 ``None``（不拿空列表冒充）。"""

    names: Optional[List[str]] = None    # 视图栈类名（栈底 -> 栈顶）
    top: Optional[str] = None            # 栈顶类名（= 当前界面）

    def to_dict(self):
        return {'names': self.names, 'top': self.top,
                'in_duel': self.in_duel, 'in_result': self.in_result,
                'in_matching': self.in_matching}

    @property
    def in_duel(self) -> bool:
        """栈里**任意一层**是对局视图就算在对局内。

        为什么不是只看栈顶：对局中会叠对话框（``DialogViewControllerManager``
        是另一个栈），且转场期栈顶可能短暂是别的类。untapped 也是遍历整个栈
        置 ``isInDuel``。
        """
        if not self.names:
            return False
        for n in self.names:
            if n.startswith(DUEL_PREFIX_LONG):
                return True
            # 短名形态：DuelStartViewController / DuelLiveViewController …
            if n.startswith(DUEL_PREFIX_SHORT) and n.endswith('ViewController'):
                return True
        return False

    @property
    def in_result(self) -> bool:
        """栈顶是结算界面。"""
        return bool(self.top) and self.top in RESULT_VIEWS

    @property
    def in_matching(self) -> bool:
        """栈里任意一层是**匹配界面**（在等对手）。

        用于「进匹配界面时提示我方卡组识别状态」—— 这是唯一该弹提示的时机
        （untapped 同款口径：只在匹配界面显示 Badge，对局中/大厅都不显示）。
        """
        if not self.names:
            return False
        for n in self.names:
            short = n.rsplit('.', 1)[-1]
            if short in MATCHING_VIEWS:
                return True
        return False


def read_current_view(proc, mirror, cw_ti=None,
                      table: bytes = b'') -> Optional[CurrentView]:
    """读当前界面。全链断掉给 ``None``（**正常态**，不炸）。

    :param cw_ti: 已缓存的 ``ContentViewControllerManager`` TypeInfo
                  （常驻方必须传；否则每次都要全内存扫类名，很慢）
    :param table: metadata 字符串表（``read_string_table``）——
                  只在 ``cw_ti`` 没给时需要
    """
    try:
        if cw_ti:
            ti = cw_ti
        else:
            tis = find_typeinfo(proc, mirror, table, MANAGER_NS, MANAGER_NAME)
            ti = tis[0] if tis else 0
        if not ti:
            return None
        obj = find_singleton(proc, ti, slot=INSTANCE_SLOT)
        if not obj:
            return None
        stack = proc.read_ptr(obj + VIEW_STACK_OFFSET)
        if not stack or stack < MIN_ADDR:
            return CurrentView(names=[], top=None)
        items = list_items(proc, mirror, stack) or []
        names = [class_name_of(proc, v) for v in items if v]
        names = [n for n in names if n]
        return CurrentView(names=names, top=(names[-1] if names else None))
    except Exception:
        return None


def find_manager_typeinfo(proc, mirror, table: bytes) -> int:
    """定位 ``ContentViewControllerManager`` 的 TypeInfo；找不到给 0。

    常驻方应当调一次并缓存（TypeInfo 查找是全内存扫描）。
    """
    try:
        tis = find_typeinfo(proc, mirror, table, MANAGER_NS, MANAGER_NAME)
        return tis[0] if tis else 0
    except Exception:
        return 0


def list_manager_fields(proc, mirror, ti: int) -> dict:
    """诊断用：管理器**本类**的字段表（不含继承字段）。"""
    try:
        return {f.name: f.offset for f in list_fields(proc, ti, mirror=mirror)}
    except Exception:
        return {}


def singleton_address(proc, ti: int) -> int:
    """``Instance`` 单例对象地址；没有实例给 0。"""
    try:
        return read_singleton_slot(proc, ti, INSTANCE_SLOT)[0]
    except Exception:
        return 0
