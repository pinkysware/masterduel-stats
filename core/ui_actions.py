# -*- coding: utf-8 -*-
"""M7 的写操作（S0）—— **纯 Python，不依赖任何 GUI 框架**。

UI 只负责发号施令，所有落库都经过这里，方便单测打桩。
纪律同 :mod:`core.ui_state`：**不许 GUI import、读失败不许抛**（写操作可以返回
``False``，但不许把异常丢给界面）。

开机自启（审查 P2-2 定死）：**pywin32 没装**，为一个 .lnk 引入它不值 ——
改用 ``subprocess`` 调一句 ``WScript.Shell`` COM 建快捷方式，**零新依赖**。
放在「启动」目录而不是计划任务：根路径下的计划任务属主是
BUILTIN\\Administrators，标准令牌没有写权限（本机踩过）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable, Optional, Sequence, Tuple

from core import modes
from core.storage import MANUAL_RESULT_SRC

#: 快捷方式文件名（放在系统「启动」目录里）
SHORTCUT_NAME = 'MasterDuelStats.lnk'

#: 🔴 2026-10-24 用户实报「没勾自启也自启」：安装器的 "Start with Windows"
#:   选项写的是 **HKCU Run 注册表键**（installer.nsi 的 SecAutostart，
#:   且默认勾选），而软件内开关一直只管「启动目录的 .lnk」—— 两条通道
#:   互不知道，于是 Run 键在而开关显示「关」。统一口径：
#:   * ``is_autostart`` = .lnk 在 **或** Run 键在（任一即视为「开」）；
#:   * ``set_autostart(False)`` = 两条都清；
#:   * ``set_autostart(True)`` = 只建 .lnk（软件内标准通道），**顺手清
#:     Run 键** —— 两条都在会开机双启动。
RUN_KEY_SUB = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_KEY_NAME = 'MasterDuelStats'


def _run_key_exists() -> bool:
    """HKCU Run 键里的安装器自启动项还在不在（读不了注册表 = False）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_SUB) as k:
            winreg.QueryValueEx(k, RUN_KEY_NAME)
            return True
    except OSError:
        return False
    except ImportError:                                  # pragma: no cover
        return False


def clear_run_key() -> bool:
    """清掉安装器写的 Run 自启动键（幂等；读不了注册表返回 False）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_SUB,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_KEY_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
    except ImportError:                                  # pragma: no cover
        return False

_DEFAULT_ARGS_TMPL = (
    "$s = (New-Object -COM WScript.Shell).CreateShortcut('%s');"
    "$s.TargetPath = '%s';$s.Arguments = '%s';"
    "$s.WorkingDirectory = '%s';$s.Save()"
)


def startup_dir() -> str:
    """当前用户的「启动」目录（``shell:startup``）。

    依次回退 ``APPDATA`` -> ``USERPROFILE\\AppData\\Roaming`` -> ``~``
    （``%APPDATA%`` 在 Git Bash 下是空的，项目里踩过）。
    """
    appdata = os.environ.get('APPDATA') or ''
    if not appdata:
        prof = os.environ.get('USERPROFILE') or ''
        if prof:
            appdata = os.path.join(prof, 'AppData', 'Roaming')
    if not appdata:
        appdata = os.path.expanduser('~')
    return os.path.join(
        appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')


def shortcut_path(name: str = SHORTCUT_NAME) -> str:
    return os.path.join(startup_dir(), name)


def is_autostart(name: str = SHORTCUT_NAME) -> bool:
    """「开没开」—— .lnk 在 **或** 安装器的 Run 键在（任一即开，2026-10-24 统一）。"""
    try:
        if os.path.exists(shortcut_path(name)):
            return True
    except Exception:
        pass
    return _run_key_exists()


def _default_runner(args: Sequence[str]) -> int:
    """真正去跑子进程。单测里会被替换掉（**测试不许真的建快捷方式**）。"""
    try:
        return subprocess.call(
            list(args), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return 1


def set_autostart(on: bool, target: str, arguments: str = '',
                  workdir: str = '', name: str = SHORTCUT_NAME,
                  runner: Optional[Callable[[Sequence[str]], int]] = None
                  ) -> bool:
    """开 / 关开机自启。**关 = 直接删 .lnk**（不用再调子进程）。

    :param target: 要启动的程序（如 pythonw.exe 的绝对路径）
    :param arguments: 命令行参数（如 ``tools\\duel_ui.py``）
    :param runner: 注入用，默认走 :func:`subprocess.call`
    :return: 操作后 ``is_autostart()`` 是否为期望值
    """
    path = shortcut_path(name)
    if not on:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            return False
        #: ★ 关也要清安装器的 Run 键 —— 只删 .lnk 的话，装时勾过
        #:   "Start with Windows" 的用户开关显示「关」却照样自启
        #:   （2026-10-24 贴吧用户实报）。
        clear_run_key()
        return not is_autostart(name)
    #: ★ 开 = 只用 .lnk 通道，顺手清 Run 键 —— 两条并存会开机双启动。
    clear_run_key()

    if not target:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    # PowerShell 一句建快捷方式（零新依赖；pywin32 没装）
    ps = _DEFAULT_ARGS_TMPL % (
        path.replace("'", "''"),
        target.replace("'", "''"),
        arguments.replace("'", "''"),
        (workdir or os.path.dirname(target) or '').replace("'", "''"),
    )
    run = runner or _default_runner
    try:
        rc = run(['powershell', '-NoProfile', '-NonInteractive',
                  '-Command', ps])
    except Exception:
        return False
    if rc != 0:
        return False
    return is_autostart(name)


# --------------------------------------------------------------- 标注
#: 「未知卡组」——**一个专门的条目**（用户 2026-09-15 定，UI 选择器最后那一条）。
#: 语义 = 「这局对手是什么我知道，但**现在查不到了**」（日志轮转、游戏内记录过期），
#: **不是**「我懒得标」。点了它这局就**离开待标注队列**、行上显示「未知卡组」。
#: ⚠️ 与 `混轴卡组` **是两个独立条目**（用户 2026-09-17 明确）：
#: 混轴只是统称，用户之后会用**自定义卡组名**替换成具体名字；
#: 未知卡组则是「永远填不上」的终态。
#: ⚠️ **唯一定义处**（2026-09-17 起）：`ui/selector.py` 直接从这里 import，
#: 不再各写一份 —— 回归锁 `tests/test_ui_selector.py::test_unknown_name_matches_ui`。
UNKNOWN_DECK_NAME = '未知卡组'

#: 交叉战绩里**不是真对手**的占位条目 —— S6 统计窗默认不显示它们（用户 2026-09-17 定）。
#: ⚠️ **读侧过滤用**，与上面的写操作无关（标注流程不会写这些常量）。
#: ⚠️ `'(未标注)'` 是 `core.storage.OPP_DECK_EXPR` 那条 SQL 里的**同值字面量**
#:    （那边没有 Python 常量）→ 回归锁会断言它确实出现在 `OPP_DECK_EXPR` 里，
#:    否则「默认隐藏」会**静默失效**（不报错、只是不再隐藏）。
#: ⚠️ 「混轴卡组」**不在**这里：用户 2026-09-17 口径 = 该条目已弃用，
#:    存量 2 局**照常显示**，等他以后用自定义卡组名替换。
NON_DECK_LABELS = (UNKNOWN_DECK_NAME, '(未标注)')


def mark_unknown(storage, match_id: str, note: Optional[str] = None
                 ) -> Tuple[bool, str]:
    """把一局标成「未知卡组」—— 与 UI 点选择器最后一条**完全同一条链路**。

    用于**游戏内记录已过期、永远查不回来**的局（用户 2026-09-17 口径）：
    与其永远挂在待标注队列里当噪声，不如明确记成「查不到了」。
    """
    if not match_id:
        return False, '没有指定对局'
    try:
        oid = storage.upsert_opp_deck(UNKNOWN_DECK_NAME)
        storage.mark_opp_deck(match_id, opp_deck_id=oid,
                              name=UNKNOWN_DECK_NAME, note=note)
    except Exception as exc:                       # 不许把异常丢给界面
        return False, '写入失败：%s' % exc
    return True, '已标注为「%s」' % UNKNOWN_DECK_NAME


def mark(storage, match_id: str, name: Optional[str] = None,
         opp_deck_id: Optional[int] = None, note: Optional[str] = None,
         new: bool = False) -> Tuple[bool, str]:
    """给一局绑上对手卡组。返回 ``(是否成功, 说明)``。

    * ``name`` 走 :meth:`Storage.mark_opp_deck`（内部按名字找/建条目）；
    * ``opp_deck_id`` 已知 id 时直接传，省一次查表；
    * ``new=True`` = 清单里没有的名字，**建进自己的 opp_decks 表**
      （先 :meth:`Storage.upsert_opp_deck` 再绑）。

    ⚠️ 终审结论（2026-09-14）：**没有「标记未知」这个动作** —— 全 None 参数等于
    ``COALESCE`` 什么都不改，而队列判据是 ``opp_deck_id IS NULL``，
    点了等于没点（和「取消」同效果，用户会当 bug）。首期**不做**这个按钮，
    「稍后 / 跳过」就够了：那一局留在待标注队列里。
    """
    if not match_id:
        return False, '没有指定对局'
    if not name and opp_deck_id is None:
        return False, '既没给卡组名也没给 id（「标记未知」已按终审砍掉）'
    try:
        if new and name:
            storage.upsert_opp_deck(name)
        storage.mark_opp_deck(match_id, opp_deck_id=opp_deck_id,
                              name=name, note=note)
    except Exception as exc:                       # 不许把异常丢给界面
        return False, '写入失败：%s' % exc
    return True, '已标注'


def mark_meta_deck(storage, match_id: str, deck: Optional[dict],
                   disp: Optional[str] = None,
                   note: Optional[str] = None
                   ) -> Tuple[bool, str]:
    """把 meta_decks 的一条记录（dict）绑到一局 —— S3 卡组选择器用。

    口径与 M5 CLI ``tools/mark_opponent.py`` 的 ``bind()`` 一致：
    ``matches.opp_deck_id`` 外键指向 **opp_decks** 表（不是 meta_decks），
    所以先 :meth:`Storage.upsert_opp_deck`(站内名, aliases) 换 id 再绑，
    ``name`` 存**显示名**（``中文名 > 显示英文名 > 站内名``）。

    「标记未知」没有：``deck`` 为空返回失败（终审 2026-09-14）。

    ``disp`` = **界面上显示的那个名字**（多语言时由调用方按当前显示语言算好传进来，
    见 `ui.selector.deck_display`）。不传就退回「中文优先」的老口径。

    ``note`` = 标注留痕（如 auto-adopt 的 ``auto:conf=0.95``），落
    ``matches.opp_deck_note``（storage 层 COALESCE 补空，不盖已有备注）。
    """
    if not match_id:
        return False, '没有指定对局'
    if not deck or not deck.get('name'):
        return False, '没有选择卡组'
    disp = disp or (deck.get('name_cn') or deck.get('name_display')
                    or deck['name'])
    try:
        oid = storage.upsert_opp_deck(deck['name'],
                                      aliases=deck.get('aliases'))
        storage.mark_opp_deck(match_id, opp_deck_id=oid, name=disp, note=note)
    except Exception as exc:                       # 不许把异常丢给界面
        return False, '写入失败：%s' % exc
    return True, '已标注：%s' % disp


def unmark(storage, match_id: str) -> Tuple[bool, str]:
    """撤销一局的标注（标错了要能改回来）。"""
    if not match_id:
        return False, '没有指定对局'
    try:
        changed = storage.clear_opp_deck(match_id)
    except Exception as exc:
        return False, '撤销失败：%s' % exc
    return (True, '已撤销') if changed else (False, '这一局本来就没标注')


# ------------------------------------------------------------- 胜负修正
#: 手动修正的候选（顺序 = 界面菜单顺序）：值 -> 中文
RESULT_CHOICES = (('Win', '胜利'), ('Lose', '失败'), ('Draw', '平局'))
#: 值 -> 中文（菜单 / 提示条共用；界面别再写第二份）
RESULT_LABELS = dict(RESULT_CHOICES)


def repair_result(storage, match_id: str, result: Optional[str]
                  ) -> Tuple[bool, str]:
    """手动修正一局的胜负 —— **人工优先于自动解析**（用户 2026-09-18 定）。

    为什么需要它（`docs/Bug修复任务书-20260918.md` Bug 2）：对手秒投的局，
    采集侧**没有任何胜负证据** —— winbits / ClientWork 结算码 / 结算文案全空
    （实测 record_20260918_194446、record_20260918_222756），`result` 只能人工补。

    写 ``result`` + ``result_src='manual'``；带这个戳的行**不会被**
    `tools/import_duel_records.py --apply` 冲掉（判据在
    :data:`core.storage.MANUAL_RESULT_SRC`，两条写入路径都守着）。

    :param result: ``'Win'`` / ``'Lose'`` / ``'Draw'``；``None`` = **清除**结论
        （撤销修正，那一行回到「没有结果」，后续自动解析可以再填）。
    """
    if not match_id:
        return False, '没有指定对局'
    if result is not None and result not in RESULT_LABELS:
        return False, '结果只认 胜利 / 失败 / 平局'
    try:
        n = storage.repair_match_result(
            match_id, result=result,
            result_src=(MANUAL_RESULT_SRC if result else None))
    except Exception as exc:                       # 不许把异常丢给界面
        return False, '写入失败：%s' % exc
    if not n:
        return False, '没找到这一局'
    if result is not None:
        #: ★ 手填的局多半是采集侧缺证据的局（掉线/崩溃/秒投）——段位往往也空着。
        #:   按推断链补上（只补空）+ 推进连胜计数，下一局的推断才接得上。
        #:   best-effort：任何一步失败都静默（别让段位问题挡住结果修正）。
        _infer_rank_after_manual(storage, match_id, result)
    if result is None:
        return True, '已清除这一局的结果'
    return True, '已记为「%s」' % RESULT_LABELS[result]


def _infer_rank_after_manual(storage, match_id: str, result: str) -> None:
    """手填结果后按推断链补段位（**best-effort**，任何失败静默）。

    * 只在该行**还没有局后段位**时动：段位已在 = 采集链/探测处理过这局，
      再推就是双计（连胜会虚高）。
    * 推断链状态**先写库成功再落盘**（`next_state` 纯函数 + 显式 save）——
      行写不进去时状态不许前进。
    """
    try:
        from core import rank_state
        st = rank_state.load()
        if st.get('rank') is None or st.get('rate') is None \
                or st.get('win_streak') is None:
            return                              # 状态未校准：不编
        row = storage.get_match(match_id)
        if row is None or row.get('my_rank_after') is not None:
            return
        #: ★ 2026-10-22：**只推天梯局**（口径同采集器 `_infer_rank_state`）——
        #:   巅峰/DC/活动局打完**不改天梯段位**，拿它们的结果去推会把
        #:   rank_state 的连胜/连败计数带歪。
        #:   实锤：10-19~10-21 用户手填的几局（非天梯）把 `lose_streak`
        #:   一路推到 107 —— 后面每一局的推断都建立在虚高的连败数上。
        #:   判不出模式（`game_mode` 为 NULL）→ **不推**（不编）。
        if modes.group(row.get('game_mode')) != 'ranked':
            return
        before = (st.get('rank'), st.get('rate'))
        new_st, _promoted = rank_state.next_state(st, result)
        infer = getattr(storage, 'infer_rank_for_match', None)
        if not callable(infer):
            return
        if infer(match_id, before,
                 (new_st.get('rank'), new_st.get('rate'))):
            rank_state.save(new_st)
    except Exception:                            # noqa: BLE001 —— 纪律同上
        pass


def new_deck(storage, name: str, aliases: Optional[str] = None
             ) -> Tuple[bool, Optional[int]]:
    """建一个自己的对手卡组条目 -> ``(ok, opp_deck_id)``。"""
    if not name or not name.strip():
        return False, None
    try:
        return True, storage.upsert_opp_deck(name.strip(), aliases=aliases)
    except Exception:
        return False, None
