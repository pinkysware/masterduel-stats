# -*- coding: utf-8 -*-
"""duel_collector —— M6 常驻采集器（三 watcher 合一，打完一局自动归档+入库）。

计划：``docs/M6计划.md`` §2（v2.1 已终审放行）。单循环 tick 默认 2s::

    启动：双开检测 -> 启动自愈（open 目录终局化，P1-1/P3-2）-> attach
    每拍：Engine 快路径采样（零扫描，毫秒级）
          -> DuelSessionTracker 生命周期（idle -> in_duel -> 收局）
          -> duel_start：钉 first_seen（**不建目录**，见下）+ 快照我方卡组
             + duelSettings 中性对 + game_mode
          -> 控制器有界退避查找（找到才建/复用目录并写首版 manifest，P2-2；
             首见 size 异常大 = 旧局残影，继续退避，P3-3）
          -> 对局中：每拍摸 logbase.size（指针直读），变大且距上次 >=5s
             才全量落盘（state=open，同目录覆盖）
          -> pvpFinished：最后一笔全量抓 -> state=final -> DuelEndMessage
             （秒级，名字 2 分钟后被清）-> import --record 增量入库
             （失败只记日志，绝不阻塞采集）
          -> instance-gone / 掉线（连续 None >= DISCONNECT_TIMEOUT）：
             最后一次 open 落盘标 final（end_reason 记进 manifest）

⚠️ 「建目录」推迟到控制器找到时：复用三条件（§1.3）要拿新局的 instance 和
logbase.size 才能判 —— duel_start 就 mkdir 会留下没有 manifest 的空目录。

扫描预算制（§2.2）：attach（每进程一次）/ 新局找控制器（有界退避，上限
~60s）/ 收局兜底补扫 —— 对局中**零扫描**（单测断言盯着）。

红线：全程只读内存；不碰 ``redline.py`` 禁区字段。

用法::

    python tools/duel_collector.py                    # 常驻（Ctrl-C 收口）
    python tools/duel_collector.py --minutes 120      # 跑两小时
    python tools/duel_collector.py --emit-state       # 给 M7 的实时帧（默认关）
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from core import duel_record
from core.app_service import DuelSessionTracker, EV_DUEL_END, EV_DUEL_START
from core.memread import record_capture as rc
from core.memread.end_message import (EndMessageReader, arbitrate_result,
                                      default_game_dir, is_junk_texts,
                                      make_record, result_from_duel_code,
                                      texts_key)
from core.memread.il2cpp import (MetadataHeader, default_metadata_path,
                                 find_all_metadata_mirrors, find_singleton,
                                 find_typeinfo, list_fields,
                                 read_string_table)
from core.memread.process import SafeProcess, find_pids_by_name
from core.storage import DECK_SNAPSHOT_TRUSTED_MODES
from core import my_deck                       # 「当前卡组」状态文件（data/my_deck.json）
from core.memread.my_deck_watch import identify_by_cards   # 整副牌 -> 原型名

#: 掉线收口：``in_duel=None`` 连续持续 >= 此秒数视为本局终结（计划 §2.1 步 6③，
#: 终审 P2 补定义；须显著大于正常对局中的瞬时读失败间隔）
DISCONNECT_TIMEOUT = 120.0
#: 对局中两次全量落盘的最小间隔（size 变化才可能触发，这只是节流）
WRITE_GAP_SEC = 5.0
#: 控制器有界退避上限（duel_start 起算；超时本局只收 mode 不抓 record）
HUNT_LIMIT_SEC = 60.0
#: 残影判据②的 size 下限（新局开局只有 1~5 条；结合「刚收局」上下文用）
REMNANT_MIN_SIZE = 30
#: 残影判据②的时间窗：本进程刚收完一局的秒数（实现审查 P1-1：残影改上下文判据）
REMNANT_WINDOW_SEC = 90.0
#: hunt 超时后的降频重试间隔（09-27：彻底放弃会整局静默丢失 ——
#   12:04 局控制器 60s 内没找到，之后整局无 record）。
HUNT_RETRY_GAP = 30.0
#: 段位探测（§5.7 两拍法）：收局后最多等多少秒「exp/rank/rate 变化」
#: = 本局结算已写进 ClientWork。
#: ★ 2026-09-18 晚（用户：「90秒太长了」）：90 -> **30**。
#: 实测依据：21:00 局收局后 1 分钟内 profile 的 exp 就刷了（30850 -> 30950）。
#: ⚠️ 代价记录在案：结算写得慢的局（掉段晚生效）会在 30s 时按局前值落盘，
#: 晚到的掉段记不到 —— 用短等待换段位尽快显示，是用户口径的取舍。
RANK_WAIT_SEC = 30.0
#: 段位**基线**（`read_my_rank`）读不到时的挂起重试窗口（秒）—— 任务书 Bug 1：
#: 收局那一拍正好卡在场景切换（对手投降尤甚），profile 会读空；实测 2026-09-18 的
#: 21:00 / 22:27 / 22:28 三局因此**整局没有段位**，而回主界面后同一份数据又读得到。
#: 不放弃 = 窗口内每拍重试一次（`read_my_rank` 是便宜路径，不扫描，2s 一拍无压力）。
RANK_BASE_WAIT_SEC = 60.0
#: 巅峰积分（2026-09-28）：收局后等「本局结算对」出现的窗口（秒）。
#: watch 实测（tmp/_probe_peak_rate.py）：结算对**结算画面才出现**（比 pvpFinished
#: 晚一拍），且存活到下一局开局 —— 窗口比段位的 30s 宽是安全的（不抢时间），
#: 180s 覆盖「用户在结算画面磨蹭 + 慢慢点回菜单」的场景。
PEAK_WAIT_SEC = 180.0
#: 收局 EM 的**结算窗口**（秒）：结算画面是**逐步就绪**的（文案 -> 胜负位），
#: ⚠️ 2026-09-20 60 -> 90：投降局的结算画面实测 40s+ 才出现，60s 窗口在整读
#:    还没做完时就被新局切掉 → 那两局 result 丢失（用户实报）。
#: 收局那一拍常常还没好 —— 2026-09-15 实机两例：22:59/23:03 两局**连文案都没抓到**
#: （一行 EM 没留下），23:14 那局文案有了但 `winbits=None`（`result` 空着）。
#: 窗口内重试，抓到完整一条就落盘；**只 append 一行**（配对规则：一条 EM 一行）。
EM_SETTLE_WAIT_SEC = 90.0

#: 非对局时段「大厅装备卡组」的**节流识别间隔**（2026-09-18 九轮，需求①）：
#: 用户每隔一段时间打开游戏，在主界面/匹配界面就能自动纠正「当前卡组」，
#: 不依赖他记得进一次编辑界面。`snapshot_my_deck` 是便宜路径（不扫描），60s 一次无压力。
LOBBY_DECK_CHECK_SEC = 60.0
#: 第一局「打出的卡」匹配的最少卡数 —— 太少匹配不可信（对手投降局可能一张不亮）。
CONFIRM_MIN_CARDS = 5
#: 窗口内「**整读**」的退避间隔秒（整读要扫全进程 —— 扫描预算硬约束：
#: 一次收局最多 `1 + len()` = 5 次整读）。两次整读之间走「按实例地址补读胜负位」，
#: 那条不扫描、每拍都能来。
EM_FULL_RETRY_DELAYS = (2.0, 6.0, 15.0, 30.0)
#: 有文案后**每拍**按实例地址补读胜负位（2026-10-01 起，不再被整读插队）；
#: 连补这么多次仍 None ≈ 图标对象没实例化（2026-09-23 实测），提前落盘。
#: ⚠️ 阈值从 2 提到 4：旧节奏里补读被 10s 整读隔开（2 次 ≈ 10-20s），
#: 现在每拍一次（2s/次），4 次 ≈ 8s，耐心大体持平、结果快一个量级。
EM_WB_DRY_TRIES = 4
#: 快路径读 Engine 的 blob 大小（与 capture_duel_mode 同口径）
ENGINE_BLOB = 0x140
#: pid 文件（双开检测 + 启动自愈的 game pid 变化判据）
PID_PATH = os.path.join(PROJ, 'data', 'archive', 'duel_collector.pid')
#: 采集器进程的镜像名 —— ⚠️ Windows 会**复用 pid**，双开检测必须校验它。
#: 2026-10-08 事故：只看 OpenProcess 成不成功，被 svchost 骗过 -> 整场零记录。
COLLECTOR_EXES = ('python.exe', 'pythonw.exe')
#: 默认记录根目录
DEFAULT_RECORDS = os.path.join(PROJ, 'data', 'archive', 'duel_records')
#: 默认 end_messages.jsonl
DEFAULT_EM_PATH = os.path.join(PROJ, 'data', 'archive', 'end_messages.jsonl')
#: --emit-state 的单文件输出（M7 实时帧；覆盖写、不 append）
EMIT_STATE_PATH = os.path.join(PROJ, 'data', 'archive', 'duel_state.json')
#: ★ 心跳文件（每拍覆盖写）—— 看板据此显示「采集器在不在跑」。
#: ⚠️ **别用 pid 文件判**：它只在启动时写、只在优雅退出时删，被 kill 的实例会留下
#:    stale 文件（实测 09-21 那份留到了次日）。心跳是「时间戳新鲜度」，自杀/被杀
#:    都会自然变旧，判据稳。见 `core/ui_state.collector_status()`。
HEARTBEAT_PATH = os.path.join(PROJ, 'data', 'archive',
                              'collector_heartbeat.json')

ENGINE_NS, ENGINE_NAME = 'YgomGame.Duel', 'Engine'
ENGINE_FIELD_MODE = 'gameMode'
ENGINE_FIELD_FINISHED = 'pvpFinished'
#: Engine 单例槽位（capture_duel_mode 实测）
ENGINE_SLOT = 0x08
#: DuelClient 单例槽位（duel_settings 链路实测）
DUELCLIENT_SLOT = 0x00


def now_str():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def dir_name_for(ts_str):
    """'YYYY-MM-DD HH:MM:SS' -> record_YYYYMMDD_HHMMSS（目录名 = 首见时刻）。"""
    return 'record_' + ts_str.replace('-', '').replace(' ', '_').replace(':', '')


# ---------------------------------------------------------------- 编排核心

class Hooks(object):
    """内存/磁盘副作用全部经这层（纯编排可打桩单测）。默认全是「没接」."""

    def proc(self):
        return None

    def controller_ti(self):
        return 0

    def scan_controller(self):
        """全内存扫描找控制器（预算制第②类）。返回 (obj, fields) 或 (None, None)。"""
        return None, None

    def read_logbase_size(self, obj, fields):
        return None

    def read_instance(self, obj):
        return None

    def read_state(self, obj, fields):
        return None

    def scan_records(self):
        """record 目录全量扫描 -> (scans, broken)（duel_record.scan_record 形状）。"""
        return [], []

    def load_reuse_manifest(self, name):
        return None

    def snapshot_deck(self):
        return None

    def snapshot_peak_deck(self):
        """巅峰 own-deck（DeckEdit 编辑实例；Runtime 才有真实现）。"""
        return None

    def read_duel_settings(self):
        return None

    def read_my_rank(self):
        """当前段位（**自己账号**的公开资料，只读内存）；没接给 None。

        口径与 `my_rank_before/my_rate_before` 一致：
        `{'rank': 1..7, 'rate': 1..5, 'name':…, 'exp':…}`。见 `core/memread/rank.py`。
        """
        return None

    def read_end_message(self):
        """(texts, extra) 或 (None, None)。"""
        return None, None

    def read_winbits(self, instance):
        """补读某个 DuelEndMessage 实例的胜负位；没接给 None。"""
        return None

    def read_duel_result(self):
        """ClientWork 的本局结算（`DuelResult`，**只在结算画面期间有**）；没接给 None。

        胜负位是抢时间的（Unity 对象只活几秒），这个是普通字典 —— 记下来当**证据**，
        攒够样本再考虑当判据。见 `core/memread/duel_result.py`。
        """
        return None

    def read_duel_players(self):
        """ClientWork 的本局双方（含**局前段位** `Duel.rank`）；没接给 None。"""
        return None

    def read_peak_result(self):
        """ClientWork 的**巅峰积分结算对**（before/after，结算画面后出现）。

        没接给 None。见 `core/memread/peak_rate.py`；只对巅峰局消费
        （编排层门控，天梯局不读）。
        """
        return None

    def backfill_peak(self, dir_name):
        """把刚落盘的巅峰积分补进库（`tools/backfill_peak_rate.apply_one`）。

        没接给 None —— 补写失败不阻塞采集（口径同 `backfill_rank`）。
        """
        return None

    def snapshot_peak_deck(self):
        """巅峰局 own-deck：扫**编辑中的卡组**实例（09-28 用户配合实测）。

        现场实锤：巅峰选的卡组**不落 ClientWork 容器**（DTDeck 全空），
        而是走 DeckEdit 编辑实例（deck_name='闪刀姬'、55 张，识别=Sky Striker）。
        便宜路径：MyDeckWatcher 同款（实例缓存 + 重扫节流）。
        """
        if self.sp is None or self.mirror is None or self.tbl is None:
            return None
        try:
            if self._peak_watch is None:
                from core.memread.my_deck_watch import (DeckEditReader,
                                                        MyDeckWatcher)
                self._peak_watch = MyDeckWatcher(
                    DeckEditReader(self.sp, self.mirror, self.tbl))
            return self._peak_watch.poll()
        except Exception:
            return None

    def poll_my_deck(self, now=None):
        """编辑界面卡组观察者（`core.memread.my_deck_watch`）；没接给 None。

        返回**新写入的「当前卡组」状态**（有变化时），否则 None。
        """
        return None

    def append_em(self, rec):
        pass

    def import_record(self, dir_name):
        return None

    def backfill_rank(self, dir_name):
        """把刚落盘的段位探测补进库（`tools/backfill_my_rank.apply_one`）。

        没接给 None —— 采集器编排不依赖它，补写失败也**不阻塞**。
        """
        return None

    def backfill_duel_code(self, dir_name, code, code_name):
        """结算码兜底**提前补结果**（2026-10-01 深夜，无文案局不等满窗口）。

        没接给 None —— 补写失败不阻塞采集（EM 窗口收口时还会照常走 import）。
        """
        return None

    def backfill_account(self, dir_name, name, pcode):
        """收局**盖账号章**（2026-10-03 多账号，storage 只补空）。

        没接给 None —— 补写失败不阻塞采集（账号章不是关键数据，丢了
        该局归「未分账号」，事后 API 对账/手动可补）。
        """
        return None

    def repair_my_deck(self, dir_name, name):
        """卡组确认后的**本局修正**（2026-09-18 九轮审查 P1-1）。

        没接给 None —— 确认链路改不动库就**不动全局状态文件**（P2-2：
        revealed 反推有误识风险，不能让它跨局污染「当前卡组」）。
        """
        return None

    def finalize(self, dir_path, reason):
        return None

    def read_duel_state(self):
        return None

    def write_duel_state_file(self, frame):
        pass


class _OpenRecord(object):
    """一局的采集状态（first_seen 钉在 duel_start，目录晚于控制器诞生）。"""

    def __init__(self, first_seen_at, game_mode=None, deck=None, settings=None):
        self.first_seen_at = first_seen_at
        self.game_mode = game_mode
        self.deck = deck
        self.settings = settings
        self.dir_path = None          # 控制器找到后才定（复用或新建）
        self.first_logbase_size = None
        self.last_size = None
        self.last_write_ts = 0.0
        self.finalized = False


class DuelCollector(object):
    """纯编排：生命周期判据（tracker）+ record 落盘 + EM + 入库。

    **内存读取全部经 hooks** —— 单测打桩即可覆盖全部路径，不碰真进程。
    """

    def __init__(self, records_dir=DEFAULT_RECORDS, note='',
                 disconnect_timeout=DISCONNECT_TIMEOUT,
                 write_gap=WRITE_GAP_SEC, rank_wait=RANK_WAIT_SEC,
                 rank_base_wait=RANK_BASE_WAIT_SEC, peak_wait=PEAK_WAIT_SEC,
                 em_settle_wait=EM_SETTLE_WAIT_SEC,
                 hunt_limit=HUNT_LIMIT_SEC,
                 remnant_min=REMNANT_MIN_SIZE,
                 remnant_window=REMNANT_WINDOW_SEC, emit_state=False,
                 hooks=None, log=None, importer=None):
        self.records_dir = records_dir
        self.note = note
        self.disconnect_timeout = disconnect_timeout
        self.write_gap = write_gap
        self.rank_wait = rank_wait
        self.rank_base_wait = rank_base_wait
        self.peak_wait = peak_wait
        self.em_settle_wait = em_settle_wait
        self.hunt_limit = hunt_limit
        self.remnant_min = remnant_min
        self.emit_state = emit_state
        self.hooks = hooks or Hooks()
        self.log = log or (lambda msg: None)
        self.importer = importer          # None = hooks.import_record
        self.tracker = DuelSessionTracker()
        self.cache = rc.ControllerCache()
        self.open_rec = None
        self._none_since = None
        self._hunt_started = None
        self._last_scan_ts = None
        self._em_seen = set()
        self._em_pending = None          # 文案已就绪、等胜负位的那条 EM
        self._duel_before = None         # 本局开局时的双方信息（含局前段位）
        self._peak_base = None           # 开局那拍的巅峰结算对（上一局的，当指纹）
        self._peak_probe = None          # 巅峰积分挂起等待（结算对出现为止）
        self._last_finish_ts = None      # 本进程最近一次收局（残影判据②用）
        self._last_seen_size = None      # 上一拍扫描观测的 size（残影判据①用）
        self._pending_reuse = None       # 首见时已判定的复用目录（P1-1 复用优先）
        self._rank_probe = None          # 收局段位探测（§5.7 两拍法）
        self._rank_base_pending = None   # 基线读不到时的挂起重试（Bug 1）
        self._lobby_check_at = None      # 大厅装备卡组的上次节流识别（九轮①）
        self._lobby_pending_fp = None    # 大厅识别两拍防抖的候选指纹（09-28）
        self._lobby_pending_deck = None  # 候选内容（name, ids）
        self.lobby_check_sec = LOBBY_DECK_CHECK_SEC
        self._deck_snapshot_pending = False   # 对局内快照重试（九轮②）
        self._deck_pending_at = None          # 重试起点（P2-3 的 300s 截止用）
        self._deck_confirm = None             # 第一局确认（九轮③）：收局后窗口内
        self.identify_my_deck = identify_by_cards   # 可注入（单测打桩）
        self.remnant_window = remnant_window

    # ---- EM 去重与重启播种（终审 P3-1） ------------------------------
    def seed_em(self, records):
        """已有样本的文案先塞进去 —— 重启别把上局遗留的结算文字再记一遍。"""
        for old in records or []:
            self._em_seen.add(texts_key(old.get('texts')))

    def capture_end_message(self, now=None, record_dir=None):
        """收局抓 EM（**结算画面逐步就绪 -> 开窗口重试**）。返回记录或 None。

        :param record_dir: 这一局的记录目录 —— EM 若**延后**落盘，落盘后会用它
            补跑一次 import（否则晚到的 winbits 赶不上收局那一刻的 import）。

        实测两种「缺」（2026-09-15 深夜，游戏 pid 15996）：

        * ① **文案还没就绪** —— 22:59 / 23:03 两局，`messageText` 读不到就直接放弃了，
          而 `_close_current` 对已 finalize 的局不补抓 → **一行 EM 都没留下**；
        * ② **文案有、胜负位没有** —— 23:14 那局 `winbits=None`（WinIcon 的
          GameObject 还没赋值）→ 那几局 `result` 空着，悬浮窗「最近对局」显示 `—`。

        所以开一个 `EM_SETTLE_WAIT_SEC` 的窗口重试，**两级**（扫描预算见下）：

        * 没文案时做**整读**（`_candidates()` 要扫全进程 → 按
          `EM_FULL_RETRY_DELAYS` 退避，一次收局最多 5 次）；
        * 两次整读之间按**实例地址补读胜负位**（不扫描，每拍都行）。

        抓到「文案 + 胜负位」立刻落盘；窗口到了就用手上最好的那条落盘。
        **只 append 一行**（配对规则：一条 EM 只配一行）。
        """
        if now is None:
            now = time.time()
        if (self._em_pending is not None and record_dir
                and self._em_pending.get('dir') != record_dir):
            # 🔴 2026-10-02 实锤（record_20261002_000913，30s 秒投局）：
            # 上一局的 EM 窗口没关（09-20 起「值得等就让它跑完」），本局收口
            # 时槽位被占 → 本局的 EM **整局丢掉**；更糟的是旧窗口晚到的整读
            # 会把**本局的结算画面**读回来、盖着旧局的 record_id 落盘
            # （00:10:32 那条：新局文案「对手已投降」配旧局结算码）。
            # ★ 本局优先：旧窗口当场收口（有结算码的凭码落盘，第三刀兜底；
            #   有文案的照常落），槽位让给本局。
            old = self._em_pending
            self._em_pending = None
            old_dir = old.get('dir')
            rec, why = self._flush_em(old, '新局收口（旧窗口让位）')
            if rec is not None:
                for line in self._reimport_after_em(old_dir, why):
                    self.log(line)
        if self._em_pending is None:
            self._em_pending = {'deadline': now + self.em_settle_wait,
                                'rec': None, 'sig': None, 'instance': None,
                                'dir': record_dir,
                                'tries': 0,
                # 首读不占重试名额：整读 = 1 次 + 每段退避各 1 次 = 5 次；
                # 差一错误会让 30s 那次永远轮不到（实际窗口缩到 ~25s）
                'full_left': len(EM_FULL_RETRY_DELAYS) + 1,
                                'next_full_at': now,
                                'wb_tries': 0}
        rec, _why = self._try_em(now)
        return rec

    def _try_em(self, now):
        """推进一步 -> ``(记录或 None, 说明)``；还没到点给 ``(None, 'wait')``。"""
        p = self._em_pending
        p['tries'] += 1
        if p.get('code') is None and p['tries'] <= 5:
            # ClientWork 的结算码（证据）：前几次尝试里读 —— 它在结算画面期间才在
            dr = None
            try:
                dr = self.hooks.read_duel_result()
            except Exception:
                dr = None
            if dr and dr.get('result') is not None:
                p['code'] = dr.get('result')
                p['code_name'] = dr.get('result_name')
        #: ★ 2026-10-01 重排（cheap-first）：整读一次最多烧 10s 扫描预算，
        #: 旧代码找到文案后**还继续整读**（白扫 10s×3），胜负位的便宜补读被
        #: 插队 —— 实测 22:51 局文案 22:53:44 就到手、结果 22:54:25 才落盘。
        #: 现在：有文案+有实例 = 每拍补读（毫秒级）；整读只在还没文案时做。
        have_texts = p['rec'] is not None and bool(p['instance'])
        if have_texts:
            self._cheap_winbits(p)           # 不扫描：只补胜负位
        elif p['full_left'] > 0 and now >= p['next_full_at']:
            p['full_left'] -= 1
            idx = min(len(EM_FULL_RETRY_DELAYS) - 1,
                      len(EM_FULL_RETRY_DELAYS) - p['full_left'])
            p['next_full_at'] = now + EM_FULL_RETRY_DELAYS[idx]
            self._full_read(p)
        #: ★ 2026-10-01 第五刀：结算码在手、整读连续两次扑空（~20s）→
        #: **提前把结果直写库里**（storage 侧只补空）。无文案局的结果
        #: 原来要等整读名额烧完（实测 23:37 局 83s）—— 用户看着就是
        #: 「没有结果」。EM 窗口继续跑：晚到的文案/胜负位照常收口升级。
        if (p.get('code') is not None and p['rec'] is None
                and not p.get('code_backfilled')
                and p['full_left'] <= len(EM_FULL_RETRY_DELAYS) - 1):
            p['code_backfilled'] = True
            try:
                msg = self.hooks.backfill_duel_code(
                    os.path.basename(p.get('dir') or ''),
                    p['code'], p.get('code_name'))
            except Exception:
                msg = None
            if msg:
                self.log(msg)
        if p['rec'] is not None and p['rec'].get('winbits') is not None:
            return self._flush_em(p, '胜负位就绪')
        # ★ 还能更好吗？（2026-09-20 修）——旧判据把「整读名额用完」和
        #   「没有实例可补」混为一谈：`full_left` 归零就立刻 flush，把**还没到点**
        #   的那次整读（30s）连同 deadline 一起扔掉。投降局结算画面来得晚
        #   （回大厅快、紧接着开新局），两局 result 就是这么丢的。
        #   精确判据（两条任一成立就继续等，**deadline 是唯一出口**）：
        #     * 还没抓到文案 且 整读还有名额 —— 等下一次整读；
        #     * 已有文案 且 有实例地址 —— 等便宜补读（不扫描，零成本）。
        #   两条都不成立（如：没文案又没名额 / 有文案但读不到实例）时，
        #   再等也读不出新东西 —— 当场落盘，别白占 90s 窗口。
        #: ★ 有文案 + 有实例 + 连补 EM_WB_DRY_TRIES 次仍无胜负位 = 图标对象
        #:   大概率**没实例化**（2026-09-23 实测：一局观测器全程 wb=None ——
        #:   文字齐、实例地址有值，但图标 GameObject 从未出现）→ 当场落盘。
        #:   真晚赋值的局第一两次补读就拿到了（2026-09-15 23:14 那类）。
        improvable = ((p['rec'] is None and p['full_left'] > 0) or
                      (p['rec'] is not None and bool(p['instance'])))
        if (p['rec'] is not None and bool(p['instance'])
                and p.get('wb_tries', 0) >= EM_WB_DRY_TRIES):
            return self._flush_em(p, '补读 %d 次仍无胜负位（不等了）'
                                  % p['wb_tries'])
        if improvable and now < p['deadline']:
            return None, 'wait'
        return self._flush_em(p, '超时' if p['rec'] is None else '无胜负位可补')

    def _full_read(self, p):
        """整读一次结算画面（含扫描）；更新挂起里「最好的一条」。"""
        texts, extra = self.hooks.read_end_message() or (None, None)
        if not texts or not texts.get('messageText') or is_junk_texts(texts):
            return p['rec']
        rec = make_record(texts, extra)
        if p['rec'] is None or rec.get('winbits') is not None:
            p['rec'], p['sig'] = rec, texts_key(texts)
            p['instance'] = (extra or {}).get('instance')
        return p['rec']

    def _cheap_winbits(self, p):
        """按实例地址补读胜负位（不扫描）—— 图标 GameObject 是晚一点才赋值的。

        ⚠️ 每次补读都计数（``wb_tries``）：连补 2 次仍 None ≈ 图标对象没实例化
        （2026-09-23 实测），`_try_em` 据此提前落盘，不再干等满窗口。
        """
        p['wb_tries'] = p.get('wb_tries', 0) + 1
        try:
            wb = self.hooks.read_winbits(p['instance'])
        except Exception:
            wb = None
        if not wb:
            return p['rec']
        again = make_record(p['rec']['texts'], {'winbits': list(wb),
                                                'instance': p['instance']})
        again['captured_at'] = p['rec']['captured_at']      # 保留原抓取时刻
        p['rec'] = again
        return again

    def _flush_em(self, p, why):
        """结束挂起（append 或丢弃）-> ``(记录或 None, why)``。

        去重签名**只在这里**记 —— 挂起期间不算「见过」，否则自己的重试会被挡掉。
        """
        self._em_pending = None
        rec, tries = p['rec'], p['tries']
        if rec is None:
            # ★ 2026-10-01：没文案但**结算码在手** = 结果判据已经在 ——
            # 第一局 5 次整读全落空时（10-01 22:40 局），code=1/2 被直接扔掉，
            # 结果凭空丢一局。合成一条无文案记录，胜负走 duel_code 兜底判据
            # （import 侧同一条路：winbits 恒 None 的掉线局）。
            code = p.get('code')
            if code is None:
                self.log('end_message 抓不到（%d 次尝试 / %.0fs 窗口内没有结算文案）'
                         % (tries, self.em_settle_wait))
                return None, 'nothing'
            rec = make_record({}, {})
            why = '无文案，凭结算码'
        if p['sig'] is None:
            # 无文案条目：签名带目录+结算码 —— 别跟真文案的签名/别的局互撞
            p['sig'] = '$code$%s:%s' % (p.get('dir'), p.get('code'))
        if p['sig'] in self._em_seen:
            return None, 'duplicate'          # 同文案已记过：不写第二行
        rid = os.path.basename(p.get('dir') or '') or None
        if rid:
            # 这一局的目录名 —— 配对时**精确认领**用。没有它，单目录增量
            # import（--record）会在自己那条 EM 还没落盘时，认领到相邻局的
            # 残影（2026-09-16 实机事故：骰子拉力连打，11:53 那局被上一局的
            # EM 判成 Lose，实际是胜）。
            rec['record_id'] = rid
        if p.get('code') is not None:
            # ClientWork 结算码：winbits 读不到时的**兜底判据**（2026-09-16
            # 拍板，掉线局 winbits 恒 None）；`2 = 负` 是同构推断，只认 1/2。
            rec['duel_code'] = p.get('code')
            rec['duel_code_name'] = p.get('code_name')
            rec['duel_code_src'] = 'clientwork'
        #: 🔴 2026-10-03 起 mem_result 走**三判据投票仲裁**（旧口径 winbit 优先
        #: 会被投降局残留的图标骗——记录 20261003_193726 用户自己投降却记成胜）。
        got, src_, note = arbitrate_result(
            rec.get('winbits'), p.get('code'),
            (rec.get('texts') or {}).get('messageText'))
        if got:
            rec['mem_result'] = got
            rec['mem_result_src'] = src_
        if note:
            base = rec.get('result_note')
            rec['result_note'] = ('%s；%s' % (base, note)) if base else note
        self._em_seen.add(p['sig'])
        self.hooks.append_em(rec)
        self.log('end_message 落盘（%s / %d 次尝试）：winbits=%s mem_result=%s '
                 '文案=%r duel_code=%s'
                 % (why, tries, rec.get('winbits'), rec.get('mem_result'),
                    (rec.get('texts') or {}).get('messageText'), p.get('code')))
        return rec, why

    def _poll_em_pending(self, now):
        """每拍推进 EM 抓取；这一步真的落盘时返回日志行（并补跑一次 import）。"""
        if self._em_pending is None:
            return []
        dir_path = self._em_pending.get('dir')
        rec, why = self._try_em(now)
        if rec is None:
            return []
        lines = ['end_message（%s）：winbits=%s mem_result=%s'
                 % (why, rec.get('winbits'), rec.get('mem_result'))]
        lines += self._reimport_after_em(dir_path, why)
        return lines

    def _reimport_after_em(self, dir_path, why):
        """EM 延后落盘 -> 补跑一次 import，让 winbits/result 真正进库。

        ⚠️ 收局那一刻的 import 用的是**当时**的 end_messages.jsonl；
        EM 晚到就赶不上（用户会看到 `result` 空、悬浮窗显示 `—`）。
        import 本身是 upsert/并档（`tools/import_duel_records.py`），重跑无副作用。
        """
        if not dir_path:
            return []
        fn = self.importer or self.hooks.import_record
        try:
            rep = fn(os.path.basename(dir_path))
            return ['入库（EM %s 后补跑）：%s' % (why, _summarize_import(rep))]
        except Exception as e:
            self.log('EM 后补跑 import 失败（事后手动 import 可补）：%r' % e)
            return []

    # ---- 主入口 ------------------------------------------------------
    def observe(self, ts, in_duel, game_mode=None, finished=None, now=None):
        """喂一拍（``in_duel=None`` = 这拍没读成）。返回日志行列表。"""
        now = time.time() if now is None else now
        lines = []
        #: ⚠️ 两条是 `elif`：挂起重试那一拍**读到基线就只开机、不判变化** ——
        #: 同拍再推一步会让「基线」和「局后」读的是同一个瞬间（两拍法的意义没了）。
        if self._rank_base_pending is not None:
            lines += self._poll_rank_base(now)       # 段位基线：读不到时挂起重试
        elif self._rank_probe is not None:
            lines += self._poll_rank_probe(now)      # 段位探测：每拍推进一步
        if self._peak_probe is not None:
            lines += self._poll_peak_probe(now)      # 巅峰积分：等本局结算对出现
        if self._em_pending is not None:
            lines += self._poll_em_pending(now)      # EM 胜负位补读：每拍推进
        # 编辑界面的卡组（用户 2026-09-16 口径）：读一次就一直沿用，
        # **换牌了**（卡表指纹变）才重新识别。便宜路径优先见 my_deck_watch。
        try:
            got = self.hooks.poll_my_deck(now)
        except Exception:
            got = None
        if got:
            lines.append('当前卡组：%s（编辑界面读到 %s 张，已记录）'
                         % (got.get('name'), got.get('card_count')))
        # ---- 九轮①：非对局时段，节流读「大厅装备卡组」纠正「当前卡组」
        if self.open_rec is None:
            lines += self._poll_lobby_deck(now)
        # ---- 九轮②：对局内快照重试（duel_start 那拍常为 null：容器未就绪）
        if self._deck_snapshot_pending and self.open_rec is not None:
            lines += self._retry_deck_snapshot(now)
        if self.open_rec is not None:
            lines += [x for x in self._retry_duel_settings() if x]
        # ---- 九轮③：第一局确认（收局后窗口内）
        lines += self._poll_deck_confirm(now)
        if in_duel is None:
            # 读失败不算收局（tracker 铁律，保留）；但**连续**超时要收口（终审 P2）
            if self.open_rec is not None:
                if self._none_since is None:
                    self._none_since = now
                elif now - self._none_since >= self.disconnect_timeout:
                    lines += self._close_current(
                        ts, 'disconnect-or-unknown', now)
            return lines
        self._none_since = None
        # 终审 Q3 前提：game_mode 取「**最后一次非空观测**」——duel_start 那一拍有
        # 可能读到瞬时 None，冻在那里会让「3/19 才可信」的快照判定误判（并且
        # 与 tracker 侧 `game_modes[-1]` 的语义也不一致）。
        if self.open_rec is not None and game_mode is not None:
            self.open_rec.game_mode = game_mode

        if self.emit_state:
            frame = self.hooks.read_duel_state()
            if frame is not None:
                self.hooks.write_duel_state_file(frame)

        events = self.tracker.observe(ts, in_duel, game_mode=game_mode,
                                      finished=finished)
        for ev in events:
            if ev.kind == EV_DUEL_START:
                lines += self._start_record(ts, ev, game_mode, now)
                lines.append('对局开始 %s（mode=%s）' % (ts, game_mode))
            elif ev.kind == EV_DUEL_END:
                reason = ev.data.get('end_reason')
                if reason == 'pvpFinished':
                    lines += self._finish_record(ts, now)
                else:
                    lines += self._close_current(ts, reason, now)
        if self.open_rec is not None and in_duel:
            lines += [x for x in self._track_record(now) if x]
        return lines

    # ---- 各阶段 ------------------------------------------------------
    def _start_record(self, ts, ev, game_mode, now=None):
        """duel_start：强制作废缓存（地址复用防线）+ 钉 first_seen + 快照。

        ★ 顺手收口**上一局遗留的 EM 挂起**（2026-09-18 实锤，见 `_close_stale_em`）。
        """
        lines = self._close_stale_em(now)
        self.cache.invalidate()
        self._hunt_started = time.time() if now is None else now
        self._last_scan_ts = None
        self._hunt_overdue_logged = False
        self._last_seen_size = None
        self._pending_reuse = None
        if game_mode == 19:
            # ★ 09-28：巅峰 own-deck = **编辑实例**（DTDeck 容器空、Deck 是天梯
            #   那份）—— 用户配合实测：巅峰界面里编辑实例=他巅峰选的牌。
            deck = self.hooks.snapshot_peak_deck()
        else:
            deck = self.hooks.snapshot_deck()
        game_mode_known = game_mode is not None
        if deck is not None and game_mode_known \
                and game_mode not in DECK_SNAPSHOT_TRUSTED_MODES:
            deck = None                   # 非可信集（口径同下：常规快照是错值）
        self._deck_snapshot_pending = (deck is None)
        self._deck_pending_at = None if deck is not None else now
        # 局前段位：ClientWork `Duel.rank`（下标与 name/pcode 对齐，my_index 已判好）
        # ⚠️ 只在**开局那一拍**读 —— 那时它必然是「局前」值
        try:
            self._duel_before = self.hooks.read_duel_players()
        except Exception:
            self._duel_before = None
        # 巅峰积分链的基线（2026-09-28）：开局那拍 DuelResult 里还是**上一局**
        # 的结算对，读一次当指纹。收局后新结算对一出现，用
        # 「新对 before == 基线 after」判「这是本局的对」—— 防止把上一局
        # 残留对错记到本局（快退重开等场景）。基线读不到（开局就没了）时
        # 收局后出现的**第一对**就收：对局期间 DuelResult 是被清掉的，
        # 收局后冒出来的只能是本局的（watch 实测 12:57:33 消失 / 13:01:17 出现）。
        self._peak_base = None
        try:
            self._peak_base = self.hooks.read_peak_result()
        except Exception:
            pass
        # 实机发现（验收局 2，2026-09-13）：活动局（骰子拉力等，mode 不在 3/19）
        # 的 maindeck_id 仍指向大厅常规选中的卡组 —— 常规快照是**错值**而非
        # null（本局实际卡组是用户自组的活动卡组，不在 Deck/DeckList 容器）。
        # 口径：快照只在 3/19（天梯/巅峰，用户必选常规卡组）可信；其余局存
        # null（不进统计，seat 判 NULL 无碍）—— 待审查确认后写进契约。
        # ⚠️ 终审细化（Q3）：**只有「已知模式且不在可信集」才丢**。duel_start 那一拍
        # 有可能读到瞬时 None（`engine.gameMode` 还没就位），原先会连一局真天梯的
        # 快照一起误丢；改成「模式未知时**先留着**」—— 层 0 保留原样证据，
        # 采信与否交给出口（duel_record.to_row 的兜底闸门）。
        if deck is not None and game_mode is not None \
                and game_mode not in DECK_SNAPSHOT_TRUSTED_MODES:
            self.log('game_mode=%s 非可信集：常规快照不可信（读到大厅的 %s），'
                     '按口径存 null' % (game_mode, deck.get('deck_name')))
            deck = None
        if deck is None:
            self.log('my_deck_snapshot 读不到（租借/活动局？）—— 契约口径存 null')
        settings = self.hooks.read_duel_settings()
        # 我方座位（`read_duel_players` 返回里的 my_index，来自 Duel.MyID /
        # pcode↔profile 比对）—— 活动局没有可信卡组快照，`infer_seat` 判不出
        # 座位，「先/后」与对手名就都空着；这里顺手带上当第二判据。
        # **读到才带**（只认 0/1），读不到维持现状 —— 绝不写猜测值。
        my_index = (self._duel_before or {}).get('my_index')
        if settings and my_index in (0, 1):
            settings = dict(settings)
            settings['my_index'] = int(my_index)
        self.open_rec = _OpenRecord(
            first_seen_at=ts, game_mode=game_mode, deck=deck,
            settings=settings)
        return lines

    def on_game_gone(self):
        """游戏进程消失：作废所有跨进程挂起（09-26 事故刀②）。

        旧进程的结算画面 / 控制器永远没了 —— ``_em_pending`` 留着只会在
        新进程里空烧扫描预算（09-26 实锤：用户重启游戏后旧局 EM 挂起存活，
        每拍全量扫描分钟级，主循环被拖死 8 分钟，下一局 duel_start 晚 4 分钟、
        前 4 分钟数据没采）。段位挂起同理：重启后 profile 直接是终值，
        旧的两拍法探测没有意义。由主循环在「游戏关了」分支调用，幂等。
        """
        had = []
        if self._em_pending is not None:
            self._em_pending = None
            had.append('EM')
        if self._rank_base_pending is not None:
            self._rank_base_pending = None
            had.append('段位基线')
        if self._rank_probe is not None:
            self._rank_probe = None
            had.append('段位探测')
        if self._deck_snapshot_pending:
            self._deck_snapshot_pending = False
            self._deck_pending_at = None
            had.append('卡组快照重试')
        if not had:
            return []
        return ['游戏退出：作废旧局挂起（%s）' % ' / '.join(had)]

    def _close_stale_em(self, now=None):
        """新局开始时收口上一局的 EM 挂起 —— **唯一那个槽位**要腾给本局。

        ⚠️ ``now`` 用主循环注入的拍钟（默认才用墙钟）—— 单测的时间轴是
        虚拟的，混用会让「窗口还剩多少」永远判成过期。

        2026-09-18 实锤（record_20260918_222756，21 秒的秒投局）：收局那一刻上一局
        的 EM 窗口还没关，`capture_end_message` 见槽被占就**整局不抓** →
        那局一行 EM 都没有（winbits / 结算码一起丢，`result` 只能人工补）。
        新局开始 = 上一局的结算画面**早就没了** → 当场收口（该补的 import 照跑），
        不再干等到 60s 超时。
        """
        p = self._em_pending
        if p is None:
            return []
        # ★ 别急着切（2026-09-20 修）：投降局回大厅只要几秒、用户紧接着开下一局，
        #   无条件收口会把上一局**还没做完**的整读窗口一并扔掉（实报两局结果丢失）。
        #   判据 = 整读还有名额 + 窗口没到点 -> 让它在旁边继续跑完
        #   （EM 读的是结算画面实例，与正在进行的新局互不干扰）。
        if now is None:
            now = time.time()
        _left = now < p.get('deadline', 0)
        _worth = ((p.get('rec') is None and p.get('full_left', 0) > 0)
                  or (p.get('rec') is not None and bool(p.get('instance'))))
        if _worth and _left:
            return ['上一局的 EM 窗口继续等（整读还剩 %d 次 / 余 %ds）'
                    % (p.get('full_left', 0),
                       max(0, int(p.get('deadline', 0) - now)))]
        dir_path = p.get('dir')
        rec, why = self._flush_em(p, '新局开始（旧窗口收口）')
        if rec is None:
            return []
        lines = ['上一局的 EM 挂起在新局开始时收口（%s）：winbits=%s'
                 % (why, rec.get('winbits'))]
        lines += self._reimport_after_em(dir_path, why)
        return lines

    def _track_record(self, now):
        lines = []
        rec = self.open_rec
        ti = self.hooks.controller_ti()
        if not self.cache.valid(self.hooks.proc(), ti):
            # 有界退避：duel_start 起 hunt_limit 秒内每拍试一次；
            # ★ 09-27 实锤（12:04 局整局静默丢失）：超时后不再彻底放弃 ——
            #   控制器可能晚就绪（结算清理 / 加载慢）。改为**降频重试**
            #   （每 HUNT_RETRY_GAP 秒一次），找到就照常建目录续采。
            if self._hunt_started is None:
                return lines
            overdue = now - self._hunt_started > self.hunt_limit
            gap = HUNT_RETRY_GAP if overdue else 1.0
            if self._last_scan_ts is not None and \
                    now - self._last_scan_ts < gap:
                return lines
            self._last_scan_ts = now
            if overdue and not getattr(self, '_hunt_overdue_logged', False):
                self._hunt_overdue_logged = True
                self.log('控制器 %ds 未找到 —— 降为每 %ds 重试（找到即续采）'
                         % (int(self.hunt_limit), int(HUNT_RETRY_GAP)))
            obj, fl = self.hooks.scan_controller()
            if not obj:
                return lines
            size = self.hooks.read_logbase_size(obj, fl)
            if not size:
                return lines
            prev_size = self._last_seen_size
            self._last_seen_size = size
            # 复用优先（审查 P1-1）：同 instance 且 size 单调不减的 open 目录
            # 命中即复用 —— 残影判据不参与（宁可多并、不可静默覆盖）。
            # 这是「中途重启接管半局」的唯一通道，必须在残影之前。
            reuse = self._probe_reuse(obj, size)
            if reuse is None:
                why = self._is_remnant(size, now, prev_size)
                if why:
                    self.log('控制器 size=%d 判为旧局残影（%s），继续退避'
                             % (size, why))
                    return lines
            self._last_seen_size = None
            self.cache.store(obj, fl)
            self._pending_reuse = reuse
        obj, fl = self.cache.obj, self.cache.fields
        if not obj:
            return lines
        size = self.hooks.read_logbase_size(obj, fl)
        if not size:
            return lines
        if rec.dir_path is None:
            lines += self._open_dir(size, now)
        if rec.dir_path is None:
            return lines
        if (rec.last_size is None or size > rec.last_size) and \
                now - rec.last_write_ts >= self.write_gap:
            if self._write(now, 'open', size):
                rec.last_size = size
                lines.append('logbase=%d 落盘（state=open）' % size)
        return lines

    def _probe_reuse(self, obj, size):
        """三条件复用探测（契约 §1.3；hooks.scan_records 是文件系统扫描，便宜）。"""
        scans, _broken = self.hooks.scan_records()
        return rc.find_reusable_dir(scans, self.hooks.read_instance(obj),
                                    datetime.now(), size)

    def _is_remnant(self, size, now, prev_size=None):
        """残影**上下文**判据（审查 P1-1：取代「size>30 即残影」的绝对阈值）。

        ① size 相对上一拍扫描观测**回退**（同一地址内容换成了更短的旧局）；
        ② 本进程刚收完一局（窗口内）且 size 明显不是新局开局
           （旧控制器会挂到结算画面结束；新局开局只有 1~5 条，不会误伤）
           —— ⚠️ **且 size 没在涨**：涨 = 活局（2026-09-18 实锤，用户漏局事故：
           新局开局 22s 时上一局刚收，真新局的 43→46→52 被当成残影连续拒绝，
           60s hunt 预算烧光，整局没建目录）。
        是残影返回原因字符串，不是给 None。
        """
        if prev_size is not None and size < prev_size:
            return 'size 回退 %d<%d' % (size, prev_size)
        if self._last_finish_ts is not None and \
                now - self._last_finish_ts <= self.remnant_window and \
                size > self.remnant_min and \
                (prev_size is None or size <= prev_size):
            return '刚收局（%.0fs 前）且 size=%d 偏大' % (
                now - self._last_finish_ts, size)
        return None

    def _open_dir(self, size, now):
        """控制器首见：三条件复用 open 目录，否则按 first_seen 新建。"""
        lines = []
        rec = self.open_rec
        reuse, self._pending_reuse = self._pending_reuse, None
        if reuse is None:
            reuse = self._probe_reuse(self.cache.obj, size)
        if reuse:
            man = self.hooks.load_reuse_manifest(reuse) or {}
            cap = man.get('capture') or {}
            rec.dir_path = os.path.join(self.records_dir, reuse)
            rec.first_seen_at = cap.get('first_seen_at') or rec.first_seen_at
            rec.first_logbase_size = cap.get('first_logbase_size') or size
            lines.append('复用 open 目录 %s（三条件命中，不劈局）' % reuse)
        else:
            rec.dir_path = os.path.join(self.records_dir,
                                        dir_name_for(rec.first_seen_at))
            rec.first_logbase_size = size
            lines.append('新建目录 %s' % os.path.basename(rec.dir_path))
        if self._write(now, 'open', size):
            rec.last_size = size
            lines.append('首版 manifest（state=open, size=%d）' % size)
        return lines

    def _finish_record(self, ts, now):
        """pvpFinished：最后一笔全量抓 -> final -> EM -> import（顺序固定）。"""
        lines = []
        rec = self.open_rec
        if rec is None or rec.finalized:
            return lines
        rec.finalized = True
        self._last_finish_ts = now
        # ① 收局兜底补扫（缓存失效时结算画面再扫一次，审查 §四.1(b)）；
        # 补扫结果比已落盘还小 = 旧局残影，不覆盖（审查 P3-1，与 P1-1 判据同源）
        ti = self.hooks.controller_ti()
        if not self.cache.valid(self.hooks.proc(), ti):
            obj, fl = self.hooks.scan_controller()
            if obj:
                size = self.hooks.read_logbase_size(obj, fl)
                if size and rec.last_size is not None and size < rec.last_size:
                    self.log('收局补扫 size=%d < 已落盘 %d，判残影不覆盖'
                             % (size, rec.last_size))
                else:
                    self.cache.store(obj, fl)
        # ② 最后一笔全量抓 -> state=final
        if rec.dir_path is not None:
            state = None
            if self.cache.obj:
                state = self.hooks.read_state(self.cache.obj,
                                              self.cache.fields)
            if state:
                self._write(now, 'final', state['logbase']['size'],
                            state_data=state)
                lines.append('最终落盘（state=final, size=%d）'
                             % state['logbase']['size'])
            else:
                self.hooks.finalize(rec.dir_path, 'finish-no-data')
                lines.append('读不到控制器了，按现状标 final')
        # ③ DuelEndMessage（秒级抓：结算后约 2 分钟名字字段被清）
        em = self.capture_end_message(now, rec.dir_path)
        if em:
            lines.append('end_message：winbits=%s mem_result=%s'
                         % (em.get('winbits'), em.get('mem_result')))
        # ④ import 增量入库（失败降级，绝不阻塞采集）
        if rec.dir_path is not None:
            fn = self.importer or self.hooks.import_record
            try:
                rep = fn(os.path.basename(rec.dir_path))
                lines.append('入库：%s' % _summarize_import(rep))
            except Exception as e:
                self.log('import 失败（不阻塞采集，事后手动 import 可补）：%r' % e)
        # ⑤ 段位（内存，只读）：当场读一次 + 后续拍等 exp 变化（§5.7 两拍法）
        lines += self._start_rank_probe(now, rec)
        # ⑥ 巅峰积分（2026-09-28）：只挂巅峰局，等结算对出现（便宜路径重试）
        lines += self._start_peak_probe(now, rec)
        self._start_deck_confirm(now, rec)     # 九轮③：第一局确认窗口
        self.open_rec = None
        return lines

    def _close_current(self, ts, reason, now):
        """instance-gone / 掉线超时：最后一次 open 落盘标 final（note 记原因）。"""
        rec = self.open_rec
        self.open_rec = None
        self._hunt_started = None
        self._last_finish_ts = now
        self.cache.invalidate()
        if rec is None:
            return []            # 审查 P3-7：没开过局就别打误导日志
        if rec.dir_path is None or rec.finalized:
            return []
        lines = []
        if not rec.finalized:
            # 审查 P3-2：instance-gone 时结算画面可能还在，顺手补抓一次 EM
            # （winbits 本来就是 NULL 口径，抓到能给掉线局补 result_note）
            try:
                if self.capture_end_message(now, rec.dir_path):
                    lines.append('end_message 已补抓（%s）' % reason)
            except Exception:
                pass
            self.hooks.finalize(rec.dir_path, reason)
        self._start_deck_confirm(now, rec)     # 九轮③：收口也给确认窗口
        #: ★ **收口的局也要入库**（2026-09-18 实锤，Bug 3）：22:48 / 22:53 两局
        #: （用户重启采集器时正在打的那两局）走的就是这条收口 —— 以前只标 final
        #: 不 import，记录**永远躺在磁盘上**（库里没有这两行，最后靠手工补）。
        #: 这条路也是 Ctrl-C 优雅退出（reason='flushed'）与掉线超时的收口。
        #: import 幂等（层 0 指纹 + 并档），重跑无副作用；失败只记日志不阻塞采集。
        fn = self.importer or self.hooks.import_record
        try:
            rep = fn(os.path.basename(rec.dir_path))
            lines.append('入库（收口后）：%s' % _summarize_import(rep))
        except Exception as e:
            self.log('import 失败（不阻塞采集，事后手动 import 可补）：%r' % e)
        # ★ 收口的局**没有 pvpFinished 探测**（2026-10-05 实测：段位只有推断值，
        #   state 一脱节就错）—— 收口时用户多半在主界面，profile 读得到：
        #   读一次真值，修本局行 + 校准 rank_state（与 _finish_record 路径同守卫）。
        try:
            cur = self.hooks.read_my_rank()
            cur_rank = (cur or {}).get('rank')
            if cur_rank is not None:
                from core import rank_state
                from core.storage import Storage
                Storage().repair_rank_for_record(
                    os.path.basename(rec.dir_path), cur_rank, cur.get('rate'))
                st = rank_state.load()
                if (st.get('rank'), st.get('rate')) != (cur_rank,
                                                        (cur or {}).get('rate')):
                    rank_state.init_from(cur_rank, cur.get('rate'))
                    lines.append('段位复核（收口）：探测真值 %s 与推断不符，已修正'
                                 % _rank_label(cur))
        except Exception as e:
            self.log('段位复核失败（不阻塞）：%r' % e)
        lines.append('对局收口（%s）：%s 已标 final'
                     % (reason, os.path.basename(rec.dir_path)))
        return lines

    def _write(self, now, state, size, state_data=None):
        rec = self.open_rec
        if rec is None or rec.dir_path is None:
            return False
        state_data = state_data or self.hooks.read_state(
            self.cache.obj, self.cache.fields)
        if not state_data:
            return False
        rc.write_record(
            rec.dir_path, state_data,
            instance_hex=self.hooks.read_instance(self.cache.obj),
            state=state, first_seen_at=rec.first_seen_at,
            first_logbase_size=rec.first_logbase_size or size,
            note=self.note, my_deck_snapshot=rec.deck,
            game_mode=rec.game_mode, duel_settings=rec.settings)
        rec.last_write_ts = now
        return True

    # ---- 我方卡组三道防线（2026-09-18 九轮）---------------------------
    def _poll_lobby_deck(self, now):
        """①非对局时段每 `LOBBY_DECK_CHECK_SEC` 读一次「大厅装备卡组」。

        指纹与「当前卡组」相同就什么都不做（反复回大厅不会抖）；不同（或状态文件
        还没有名字）就重识别并更新 —— **开游戏 1 分钟内自动纠正**，不依赖用户
        记得进编辑界面（当天实测的跟丢场景）。
        """
        if self._lobby_check_at is not None \
                and now - self._lobby_check_at < self.lobby_check_sec:
            return []
        self._lobby_check_at = now
        deck = self.hooks.snapshot_deck()
        ids = (deck or {}).get('ids') or []
        if len(ids) < CONFIRM_MIN_CARDS:
            return []                    # 容器没就绪 / 租借局：下个节流点再试
        # ★ 09-28 实锤：回大厅瞬间 ClientWork 卡表可能是**过渡值**（旧装备 ->
        #   新装备的中间态），识别跟着抖（Branded/Sky Striker/Tearlaments 来回
        #   切）→ 确认条「确认了又弹」。改两拍防抖：同指纹连续两次节流点一致
        #   才落盘；期间指纹变了就作废候选重计。
        fp = my_deck.fingerprint(ids)
        if self._lobby_pending_fp is not None and self._lobby_pending_fp != fp:
            self._lobby_pending_fp = None
            self._lobby_pending_deck = None
            return ['卡组识别抖动（候选作废，下一节流点重试）']
        if self._lobby_pending_fp is None:
            name = self.identify_my_deck(ids)
            if not name:
                return []                # 认不出不挂候选（别拿噪声盖对的）
            self._lobby_pending_fp = fp
            self._lobby_pending_deck = (name, ids)
            return []                    # 下一节流点同指纹才确认
        self._lobby_pending_fp = None
        name, ids = self._lobby_pending_deck
        self._lobby_pending_deck = None
        cur = my_deck.load()
        if cur.get('name') and cur.get('ids_fingerprint') == fp:
            return []                    # 认出同一套牌：什么都不做（防抖）
        name = self.identify_my_deck(ids)
        if not name:
            return []                    # 认不出不覆盖（别拿噪声盖对的）
        my_deck.save(name, ids=ids, src='lobby')
        return ['当前卡组（大厅识别）：%s（%d 张，原记 %s）'
                % (name, len(ids), cur.get('name') or '无')]

    def _retry_duel_settings(self):
        """②b duel_settings 兜底重试（09-27 海皇局实锤）。

        duel_start 那拍 `ClientWork` 容器未就绪时 `read_duel_settings` 读不到 →
        `duel_settings` 整局 null → `my_index` 判据失效 → **seat / went_first /
        对手名全空**（用户被迫手补）。settings 是便宜读（不扫描），未就绪就
        每拍重试，读到为止 —— 晚到照样随 `_write` 进 manifest，
        收局 import 的座位判据②就能用（**不受快照的 300s 上限约束**）。
        """
        rec = self.open_rec
        # ⚠️ 不要求 dir_path：duel_start 那拍还没建目录，settings 该先回填到
        # rec（建目录后的 _write 自然带上）—— 09-27 海皇局教训。
        if rec is None or rec.settings is not None:
            return []
        try:
            s = self.hooks.read_duel_settings()
        except Exception:
            return []
        if not s:
            return []
        rec.settings = s
        return ['duel_settings 补读成功（my_index=%s）' % s.get('my_index')]

    def _retry_deck_snapshot(self, now):
        """②对局内快照重试：duel_start 那拍 `ClientWork.Deck` 常未就绪（快照 null），
        对局中每拍重读一次，读到就带回 `rec.deck`（收局 manifest/import 都用它）。
        活动局（非可信集）仍按口径丢弃 —— 判据与 `_start_record` 同一份。"""
        rec = self.open_rec
        if rec is None or rec.dir_path is None:
            return []
        #: ★ 审查 P2-3：可信模式但容器整局未就绪时，不能每拍无限重试 ——
        #: 开局 300s 后放弃（真局里 30s 内早就绪；300s 是十倍余量）。
        if self._deck_pending_at is not None \
                and now - self._deck_pending_at > 300.0:
            self._deck_snapshot_pending = False
            line = '对局内快照重试超 300s 放弃（容器整局未就绪）'
            self.log(line)
            return [line]      # ← 之前漏了把日志行带进返回值，observe 拍上就不显示
        if rec.game_mode == 19:
            deck = self.hooks.snapshot_peak_deck()
        else:
            deck = self.hooks.snapshot_deck()
        if deck is None:
            return []
        self._deck_snapshot_pending = False
        game_mode = rec.game_mode
        if game_mode is not None and game_mode not in DECK_SNAPSHOT_TRUSTED_MODES:
            return ['对局内快照重试读到 %s，但 mode=%s 非可信集，按口径丢弃'
                    % (deck.get('deck_name'), game_mode)]
        rec.deck = deck
        return ['我方卡组快照（重试）：%s（%d 张）'
                % (deck.get('deck_name'), len(deck.get('ids') or []))]

    def _start_deck_confirm(self, now, rec):
        """收局时挂起确认窗口（`duel.json` 解码有延迟，窗口内每拍尝试）。"""
        if rec.dir_path is None:
            return
        #: ⚠️ 存**目录名**（basename）—— `matches.record_dir` 与 confirm 里的
        #: 读盘/修库都按目录名走（`rec.dir_path` 是绝对路径）。
        #: ⚠️ 这里**不再**顺手推段位：`duel.json` 要等收局 import 之后才有
        #: `meta.result`，那时推才是对的 —— 由 `_poll_deck_confirm` 在窗口内推
        #: （原来这行还把 (now, rec) 传给了 `(dir_name, result)` 那个签名，
        #:  参数全错；且守卫写坏导致整条链空转，见 `_infer_rank_state`）。
        self._deck_confirm = {'dir': os.path.basename(rec.dir_path),
                              'deadline': now + 120.0}

    def _poll_deck_confirm(self, now):
        """③第一局确认（收局后窗口内，`duel.json` 解码完成才认）：

        * **段位推断**（2026-09-18 晚，用户需求）：胜负收局即知 → `rank_state`
          推断新段位 → **只补空**写进本局行（段位同帧出现）；
        * **卡组确认**：`revealed.p0`（≥5 张）反查卡组库，与「当前卡组」不符 →
          修本局行；对手秒投（一张不亮）不确认。
        窗口 120s 超时放弃。段位真值复核在 `_poll_rank_probe` 落盘处。
        """
        p = self._deck_confirm
        if p is None:
            return []
        try:
            with open(os.path.join(self.records_dir, p['dir'], 'duel.json'),
                      encoding='utf-8') as f:
                data = json.load(f) or {}
            meta = data.get('meta') or {}
            summ = data.get('summary') or {}
            ids = (summ.get('revealed') or {}).get('p0') or []
        except Exception:
            meta, summ, ids, data = {}, {}, [], None
        if not data or data.get('state') != 'final' or not meta:
            if now >= p['deadline']:
                self._deck_confirm = None
            return []
        lines = []
        if not p.get('inferred') and meta.get('result'):
            #: ⚠️ 结果**到了**才算推过（不能无条件置位）：instance-gone 收口的局，
            #: result 要等 EM 挂起收口才进 duel.json —— 实测 2026-09-19 01:11 局
            #: （圣徒白森）：结果比窗口开还晚 20s+，无条件的 one-shot 会在
            #: 「duel.json 可读但结果还没到」的第一拍把唯一一次推断烧掉 → 该局没段位。
            p['inferred'] = True
            lines += self._infer_rank_state(p['dir'], meta.get('result'),
                                            meta.get('game_mode'))
        #: ★ 审查 P2-2 + 2026-09-18 晚实锤：卡组确认（revealed 反推）已撤 ——
        #: revealed.p0/p1 的键是座位号（seat=1 的局 p0 装的是对手 27 张卡），
        #: 反推我方卡组会把对手卡组当自己的（实测：K9vs 局被误纠成 HEROs）。
        #: 我方卡组的识别交给 ① 大厅节流识别（主界面真值）与编辑界面观察者。
        #: ⚠️ 窗口**等结果到了（或到点）才关**：instance-gone 局的结果由 EM 挂起
        #:    收口补进档案（晚 20s+），第一拍就关窗口 = 推断没机会跑第二遍。
        if p.get('inferred') or now >= p['deadline']:
            self._deck_confirm = None
        return lines

    def _infer_rank_state(self, dir_name, result, game_mode=None):
        """★ 段位推断：胜负实时 → `rank_state` 推断新段位 → 只补空写本局行。

        ⚠️ **只推天梯局**（`core.modes.group(mode) == 'ranked'`，口径同源）：
        巅峰 / DC 杯 / 活动局打完**不改天梯段位**，拿它们的结果去 `apply_result`
        会把 rank_state 的连胜计数与段位一起带歪。

        ⚠️ 2026-09-18 深夜修正：守卫原来写的是 `st.get('streak')`，可 rank_state
        v2 的键是 `win_streak` / `lose_streak` —— 于是**每局都在这儿早退**，
        「推断值同帧上屏」从十二轮起一直空转（段位整局 NULL 少了一层兜底）。
        """
        if not result:
            return []
        from core import modes as M
        from core import rank_state
        from core.storage import Storage
        if M.group(game_mode) != 'ranked':
            return []
        st = rank_state.load()
        if st.get('rank') is None or st.get('rate') is None \
                or st.get('win_streak') is None:
            # 状态未校准（连胜计数未知）→ **不编**：写个假段位比空着更坏
            return []
        before = (st.get('rank'), st.get('rate'))
        st, promoted = rank_state.apply_result(st, result)
        after = (st.get('rank'), st.get('rate'))
        n = Storage().infer_rank_for_record(dir_name, before, after)
        if not n:
            return []
        return ['段位（推断）：%s -> %s%s，探测稍后复核'
                % (_rank_label({'rank': before[0], 'rate': before[1]}),
                   _rank_label({'rank': after[0], 'rate': after[1]}),
                   '（升段！）' if promoted else '')]

    # ---- 段位探测（两拍法，见 docs/段位内存读取方案.md §5.7）------------
    def _start_rank_probe(self, now, rec):
        """收局读段位：① 当场读一次当基线；② 后续拍等 `exp` 变化再落盘。

        为什么两拍：`pvpFinished` 翻 1 与「ClientWork 被写」谁先谁后没有秒级证据
        （§5.7），只读那一拍有读到**局前值**的风险。`exp` 每局都变，是唯一灵敏的
        哨兵 —— 本轮实测那局赢了没升段，`rate` 一直 `5-2`、只有 `exp` 动了。

        ⚠️ 基线**读不到不放弃**（任务书 Bug 1，2026-09-18）：`pvpFinished` 那一拍
        正好卡在场景切换（对手投降尤甚），`read_my_rank` 会读空 —— 实测 21:00 /
        22:27 / 22:28 三局因此 `my_rank_*` 整局 NULL，而回主界面后同一份数据又
        读得到。改成**挂起 `rank_base_wait` 秒、每拍重试**（便宜路径，不扫描）。
        """
        if rec.dir_path is None:
            return []                    # 没建目录（早退局）没处落盘
        base = self.hooks.read_my_rank()
        if not base:
            self._rank_base_pending = {
                'dir': rec.dir_path, 'at': now,
                'deadline': now + self.rank_base_wait, 'tries': 1}
            self.log('段位读不到（profile 里没有 rank/rate）—— 挂起 %.0fs 每拍重试'
                     % self.rank_base_wait)
            return []
        #: 本局基线到手 —— 上一局那个还没等到基线的挂起作废（它的窗口归零）
        self._rank_base_pending = None
        return self._begin_rank_probe(base, rec.dir_path, now)

    def _begin_rank_probe(self, base, dir_path, now, waited=0.0):
        """基线到手 → 开两拍窗口（`rank_wait` 内等 exp/rank/rate 任一变化）。"""
        try:
            from core import rank_state
            if rank_state.load().get('rank') is None:
                rank_state.init_from(base.get('rank'), base.get('rate'))
        except Exception:
            pass
        self._rank_probe = {
            'dir': dir_path, 'base': base, 'base_at': now,
            'before': (self._duel_before or {}).get('my_rank'),
            'before_opp': (self._duel_before or {}).get('opp_rank'),
            'deadline': now + self.rank_wait, 'reads': 1}
        # ★ 多账号（2026-10-03）：收局盖章 —— profile 的 name/pcode 本来
        #   就在这份 base 里（以前只取 rank/rate）。storage 只补空，幂等。
        try:
            msg = self.hooks.backfill_account(
                os.path.basename(dir_path),
                base.get('name'), base.get('pcode'))
        except Exception:
            msg = None
        if msg:
            self.log(msg)
        self.log('段位基线 %s（exp=%s）%s，等 exp/rank/rate 变化再落盘'
                 % (_rank_label(base), base.get('exp'),
                    '（挂起 %.0fs 后读到）' % waited if waited else ''))
        return []

    # ---- 巅峰积分（Rate Duel RATE，2026-09-28）------------------------
    def _start_peak_probe(self, now, rec):
        """收局读巅峰积分：`DuelResult.resultInfo.RateDuel` 的 before/after 对。

        ⚠️ **只挂巅峰局**（`M.group(game_mode) == 'peak'`，计划审查 B2-1）：
        段位探测对所有模式无条件启动是历史包袱，积分不背 —— 天梯局读这个
        纯属浪费，还可能把天梯局的 event_id 写进 peak_season。
        结算对要等结算画面出现（watch 实测比 pvpFinished 晚一拍），
        挂起 `peak_wait` 秒每拍重试（便宜路径，不扫描）。
        """
        if rec.dir_path is None:
            return []                    # 没建目录（早退局）没处落盘
        from core import modes as M
        if M.group(rec.game_mode) != 'peak':
            return []
        cur = self._read_peak_checked()
        if cur:
            return self._settle_peak(cur, rec.dir_path, now, waited=0.0)
        self._peak_probe = {'dir': rec.dir_path, 'at': now,
                            'deadline': now + self.peak_wait, 'tries': 1}
        self.log('巅峰积分：结算对还没出现，挂起 %.0fs 每拍重试' % self.peak_wait)
        return []

    def _read_peak_checked(self):
        """读结算对 + **链条校验**：新对 `before` 必须等于基线（上一局）的
        `after`（上一局局后 = 本局局前，与段位同口径）。基线没有时，
        收局后出现的第一对就收（对局期间 DuelResult 已被清掉）。"""
        cur = self.hooks.read_peak_result()
        if not cur:
            return None
        base = self._peak_base
        if base and cur.get('before') != base.get('after'):
            return None
        return cur

    def _poll_peak_probe(self, now):
        """推进巅峰积分等待；落盘那一刻返回日志行（其余帧返回空）。"""
        p = self._peak_probe
        cur = self._read_peak_checked()
        if cur:
            self._peak_probe = None
            return self._settle_peak(cur, p['dir'], now, waited=now - p['at'])
        p['tries'] += 1
        if now < p['deadline']:
            return []
        self._peak_probe = None
        return ['巅峰积分：放弃（%.0fs 内没等到本局结算对；快退/掉线局多半没有）'
                % self.peak_wait]

    def _settle_peak(self, cur, dir_path, now, waited=0.0):
        """落盘 `peak_rate.json` + 顺手补进库（只补空；失败不阻塞）。"""
        data = {'before': cur.get('before'), 'after': cur.get('after'),
                'season': cur.get('season'), 'base': self._peak_base,
                'at': now, 'waited_sec': round(waited, 1)}
        try:
            with open(os.path.join(dir_path, 'peak_rate.json'), 'w',
                      encoding='utf-8', newline='') as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        except Exception as e:
            self.log('巅峰积分落盘失败：%r' % e)
            return []
        self._peak_base = None
        lines = ['巅峰积分：%.2f -> %.2f（S%s，等了 %.1fs）'
                 % (cur['before'], cur['after'], cur.get('season'), waited)]
        dir_name = os.path.basename(dir_path)
        try:
            got = self.hooks.backfill_peak(dir_name)
        except Exception as e:
            self.log('巅峰积分补写失败（不阻塞，可手跑 backfill_peak_rate --apply）：%r' % e)
            got = None
        if got:
            lines.append(got)
        return lines

    def _poll_rank_base(self, now):
        """挂起窗口内每拍重试读基线；读到就照常走两拍法，超时放弃（日志可见）。"""
        p = self._rank_base_pending
        base = self.hooks.read_my_rank()
        if base:
            self._rank_base_pending = None
            return self._begin_rank_probe(base, p['dir'], now,
                                          waited=now - p['at'])
        p['tries'] += 1
        if now < p['deadline']:
            return []
        self._rank_base_pending = None
        self.log('段位探测放弃：%.0fs 内 %d 次都没读到 profile（本局不记段位）'
                 % (self.rank_base_wait, p['tries']))
        return ['段位：放弃（%.0fs 内读不到 profile，本局不记段位）'
                % self.rank_base_wait]

    def _poll_rank_probe(self, now):
        """推进段位探测；落盘那一刻返回日志行（其余帧返回空）。"""
        p = self._rank_probe
        cur = self.hooks.read_my_rank()
        if cur:
            p['reads'] += 1
        #: ★ 2026-09-18 八轮（用户口供：自己投降的输局不加 exp）——
        #: 哨兵从单一 `exp` 扩成 exp/rank/rate **任一变化**：输局不加 exp，
        #: 但掉段时 rank/rate 会变，同样说明「结算已写进 ClientWork」。
        settled = bool(cur) and (cur.get('exp') != p['base'].get('exp')
                                 or cur.get('rank') != p['base'].get('rank')
                                 or cur.get('rate') != p['base'].get('rate')
                                 # ★ 升段/掉段局结算写得快：基线那拍可能**已经是局后值**
                                 #   （实测 2026-09-19 00:21 升段局：基线就是 6-5）——
                                 #   两拍完全等值会被判成 timeout -> 只补空 -> 推断的错值
                                 #   留在行上、界面不显示升段箭头。与「局前段位」（duel_start
                                 #   读的 ClientWork.Duel.rank）比：不同 = 结算已落，也算 settled。
                                 or (p.get('before') is not None
                                    and (cur.get('rank'), cur.get('rate'))
                                    != (p['before'].get('rank'),
                                        p['before'].get('rate'))))
        if not (settled or now >= p['deadline']):
            return []
        self._rank_probe = None
        data = {'before': p.get('before'), 'before_opp': p.get('before_opp'),
                'base': p['base'], 'after': cur,
                'base_at': p['base_at'], 'settled_at': now if settled else None,
                'waited_sec': round(now - p['base_at'], 1),
                'resolved_by': ('clientwork-change' if settled else 'timeout'),
                'reads': p['reads']}
        try:
            with open(os.path.join(p['dir'], 'my_rank_after.json'), 'w',
                      encoding='utf-8', newline='') as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        except Exception as e:
            self.log('段位落盘失败：%r' % e)
            return []
        lines = ['段位：%s -> %s（%s，等了 %.1fs）'
                 % (_rank_label(p['base']), _rank_label(cur),
                    data['resolved_by'], data['waited_sec'])]
        # ★ 落盘后**顺手补进库**（2026-09-17）：以前要用户手跑
        #   `tools/backfill_my_rank.py --apply`。补写失败只是记一行日志 ——
        #   绝不阻塞采集（库里行还没入库时 apply_one 本来就会静默返回）。
        dir_name = os.path.basename(p['dir'])
        try:
            got = self.hooks.backfill_rank(dir_name)
        except Exception as e:
            self.log('段位补写失败（不阻塞，可手跑 backfill_my_rank --apply）：%r' % e)
            got = None
        if got:
            lines.append(got)
        # ★ 真值复核（2026-09-18 晚 + 深夜补强）：探测真值是**权威**，本局行以它为准。
        #   为什么要覆盖而不是「只补空」：本局行此刻装的可能是**推断值**
        #   （`_infer_rank_state` 收局后同帧写的），推断规则里那条跨大段晋级还
        #   未经实测确认 —— 真值到了就必须压过它。
        #   ⚠️ 2026-09-22 二次修正（用户实测：两局段位显示 IV/V，实为 II）：
        #   原守卫「只有 settled 才覆盖」**过头了** —— timeout 局的 `cur` 是
        #   落盘那一拍的 **profile 快照（游戏段位页真值）**，即使没观察到变化，
        #   它也比「凭连胜计数推出来的推断值」权威（rank_state 一旦脱节，
        #   推断值步步错，且 timeout 局永不复核 = 错值永久留在行上，实测就是这两局）。
        #   新守卫：`cur` 读得到就覆盖，**但覆盖后以真值校准 rank_state**
        #   （推断链脱节正是从 state 与 profile 不一致开始的）。
        try:
            from core import rank_state
            cur_rank = (cur or {}).get('rank')
            if cur_rank is not None:
                from core.storage import Storage
                Storage().repair_rank_for_record(
                    dir_name, cur_rank, cur.get('rate'))
                st = rank_state.load()
                if (st.get('rank'), st.get('rate')) != (cur_rank, (cur or {}).get('rate')):
                    rank_state.init_from(cur_rank, cur.get('rate'))
                    lines.append('段位复核：探测真值 %s 与推断不符，已修正'
                                 % _rank_label(cur))
        except Exception as e:
            self.log('段位复核失败（不阻塞）：%r' % e)
        return lines


def _rank_label(rec):
    """段位 dict -> `'5-2'`；读不到给 `'—'`（别编）。"""
    if not rec or rec.get('rank') is None:
        return '—'
    return '%s-%s' % (rec.get('rank'), rec.get('rate'))


def _summarize_import(rep):
    if not rep:
        return '无报告'
    parts = []
    if rep.get('merged'):
        parts.append('并档 %d' % rep['merged'])
    if rep.get('new_rows'):
        parts.append('新建 %d' % rep['new_rows'])
    if rep.get('skipped_open'):
        parts.append('open 跳过 %d' % rep['skipped_open'])
    unk = rep.get('unknown_card_ids') or []
    if unk:      # P1-2：常驻路径（--log / 控制台）必须看得见，别只活在手动 import 里
        parts.append('⚠️ 卡表里查不到 %d 个卡 id（%s）→ 卡表可能过期，'
                     '跑 tools/build_card_names.py --check-update'
                     % (len(unk), '、'.join(str(i) for i in unk[:5])))
    return ' '.join(parts) or '无写入'


# ---------------------------------------------------------------- 实机接线

class TypeinfoCache(object):
    """(ns, name) -> TypeInfo 记忆化（审查 P2-1：TypeInfo 查找 = 全内存扫描
    ~6.4s，每类只允许一次；attach 已解析的直接注入，不进这里）。"""

    def __init__(self, lookup):
        self._lookup = lookup
        self._cache = {}
        self.misses = 0

    def get(self, ns, name):
        key = (ns, name)
        if key not in self._cache:
            self.misses += 1
            self._cache[key] = self._lookup(ns, name)
        return self._cache[key]


class Runtime(Hooks):
    """真进程接线：SafeProcess + metadata + Engine 采样 + 控制器缓存。"""

    def __init__(self, game_dir, log):
        self.game_dir = game_dir
        self.log = log
        self.ticache = None          # attach 成功时重建（跨进程地址不可复用）
        self._cw_ti = None
        self._emit_fail = 0
        self.sp = None
        self.pid = None
        self.mirror = None
        self.tbl = None
        self.engine_ti = 0
        self.engine_fields = {}
        self.mode_off = None
        self.finished_off = None
        self.dc_ti = 0
        self.ctrl_ti = 0
        self.ctrl_fields = None
        self.em_reader = EndMessageReader(game_dir=game_dir)
        self._deck_watch = None      # 编辑界面卡组观察者（懒建；attach 时重建）
        self._peak_watch = None      # 巅峰 own-deck 观察者（懒建；同上）

    # -- 生命周期 --
    def attach(self):
        """连游戏进程 + 解析 metadata + 定位 Engine/DuelClient/控制器 TypeInfo。"""
        self.detach()
        pids = find_pids_by_name('masterduel.exe')
        if not pids:
            return False
        mp = default_metadata_path(self.game_dir)
        proc = None
        try:
            header = MetadataHeader.from_file(mp)
            proc = SafeProcess(pids[0])
            mirrors = find_all_metadata_mirrors(proc, header)
            if not mirrors:
                raise RuntimeError('没找到 metadata 镜像')
            self.mirror = mirrors[0]
            self.tbl = read_string_table(mp, header)
            tis = find_typeinfo(proc, self.mirror, self.tbl,
                                ENGINE_NS, ENGINE_NAME)
            if not tis:
                raise RuntimeError('没定位到 Engine')
            self.engine_ti = tis[0]
            efl = {f.name: f.offset
                   for f in list_fields(proc, self.engine_ti,
                                        mirror=self.mirror)}
            if ENGINE_FIELD_MODE not in efl:
                raise RuntimeError('字段表里没有 %s' % ENGINE_FIELD_MODE)
            self.engine_fields = efl
            self.mode_off = efl[ENGINE_FIELD_MODE]
            self.finished_off = efl.get(ENGINE_FIELD_FINISHED)
            dctis = find_typeinfo(proc, self.mirror, self.tbl,
                                  'YgomGame.Duel', 'DuelClient')
            self.dc_ti = dctis[0] if dctis else 0
            ctis = find_typeinfo(proc, self.mirror, self.tbl,
                                 'YgomGame.Duel', 'DuelLogController')
            self.ctrl_ti = ctis[0] if ctis else 0
        except Exception:
            if proc is not None:
                try:
                    proc.close()
                except Exception:
                    pass
            return False
        self.sp, self.pid = proc, pids[0]
        # 新进程新地址：TypeInfo 记忆化必须整体作废（审查 P2-1）
        self.ticache = TypeinfoCache(self._find_ti)
        self._cw_ti = None
        self._emit_fail = 0
        return True

    def _find_ti(self, ns, name):
        tis = find_typeinfo(self.sp, self.mirror, self.tbl, ns, name)
        return tis[0] if tis else 0

    def detach(self):
        self._deck_watch = None      # 实例地址/TypeInfo 跨进程不可复用
        self._peak_watch = None
        self.em_reader.forget()      # adopt 的句柄跟着 sp 一起作废（10-01）
        if self.sp is not None:
            try:
                self.sp.close()
            except Exception:
                pass
        self.sp = self.pid = None
        self.mirror = self.tbl = None
        self.engine_ti = self.dc_ti = self.ctrl_ti = 0

    # -- Hooks 实现 --
    def proc(self):
        """Hooks 接口：返回 SafeProcess 句柄。

        ⚠️ 命名教训（实机局 1 整局丢数据）：曾经用 ``self.proc`` 属性存
        SafeProcess，把本方法遮蔽成「不可调用的实例」，每拍都抛 TypeError。
        句柄一律叫 ``self.sp``，``tests/test_duel_collector`` 有源码扫描盯着。
        """
        return self.sp

    def controller_ti(self):
        return self.ctrl_ti

    def scan_controller(self):
        if not self.sp or not self.ctrl_ti:
            return None, None
        obj, fl = rc.find_controller(self.sp, self.mirror, self.tbl,
                                     self.ctrl_ti)
        return (obj, fl) if obj else (None, None)

    def read_logbase_size(self, obj, fields):
        try:
            size, _items = rc.list_meta(
                self.sp, self.mirror,
                self.sp.read_ptr(obj + fields['m_LogBaseItemList']))
            return size or None
        except Exception:
            return None

    def read_instance(self, obj):
        return hex(obj) if obj else None

    def read_state(self, obj, fields):
        try:
            return rc.read_state(self.sp, self.mirror, obj, fl=fields)
        except Exception:
            return None

    def scan_records(self):
        root = getattr(self, 'records_dir', None) or DEFAULT_RECORDS
        scans, broken = [], []
        if not os.path.isdir(root):
            return scans, broken
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return scans, broken
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isdir(path) or not name.startswith('record_'):
                continue
            try:
                scans.append(duel_record.scan_record(path))
            except Exception as e:
                broken.append((name, repr(e)))
        return scans, broken

    def load_reuse_manifest(self, name):
        try:
            return duel_record.load_manifest(
                os.path.join(self.records_dir, name))
        except Exception:
            return None

    def snapshot_deck(self):
        from core.memread.client_work import snapshot_my_deck
        try:
            # P2-1：ClientWork 的 TypeInfo 只找一次（记忆化），之后走缓存
            if self._cw_ti is None:
                self._cw_ti = self.ticache.get('YgomSystem.Utility',
                                               'ClientWork') or None
            if not self._cw_ti:
                return None
            return snapshot_my_deck(self.sp, self.mirror, self.tbl,
                                    cw_ti=self._cw_ti)
        except Exception:
            return None

    def read_duel_settings(self):
        from core.memread.duel_settings import read_duel_settings
        try:
            # P2-1：DuelClient 在 attach 已解析，直接注入（不再扫描）
            return read_duel_settings(self.sp, self.mirror, self.tbl,
                                      dc_ti=self.dc_ti)
        except Exception:
            return None

    def read_my_rank(self):
        from core.memread.rank import read_my_rank
        try:
            # P2-1 同款：ClientWork 的 TypeInfo 只找一次（首次是全内存扫描）
            if self._cw_ti is None:
                self._cw_ti = self.ticache.get('YgomSystem.Utility',
                                               'ClientWork') or None
            if not self._cw_ti:
                return None
            return read_my_rank(self.sp, self.mirror, self.tbl,
                                cw_ti=self._cw_ti)
        except Exception:
            return None

    def _ensure_em_reader(self) -> bool:
        """EM 读取器**复用主连接**的进程/镜像/字符串表（🔴 2026-10-01 事故刀①）。

        旧的 `em_reader.connect()` 自己全进程扫 metadata 镜像且**无预算**
        （实测分钟级）—— 10-01 22:40 局的 90s 收局窗口整个耗在它上面，
        结算画面早就没了，5 次整读全落空。attach 时镜像/表早就绪，
        `adopt()` 只做一次类型定位（毫秒级）。
        """
        if self.sp is None or self.mirror is None or self.tbl is None:
            return False
        r = self.em_reader
        if r.ready() and r.pid == self.sp.pid:
            return True
        ok = r.adopt(self.sp, self.mirror, self.tbl)
        if not ok and time.time() - getattr(self, '_em_adopt_fail_at', 0) > 60:
            self._em_adopt_fail_at = time.time()
            self.log('EM 读取器定位 DuelEndMessage 失败（60s 内不再重复记）')
        return ok

    def read_end_message(self):
        if not self._ensure_em_reader():
            return None, None
        try:
            return self.em_reader.read_texts()
        except Exception:
            return None, None

    def read_duel_result(self):
        from core.memread.duel_result import read_duel_result
        try:
            if self._cw_ti is None:
                self._cw_ti = self.ticache.get('YgomSystem.Utility',
                                               'ClientWork') or None
            if not self._cw_ti:
                return None
            return read_duel_result(self.sp, self.mirror, self.tbl,
                                    cw_ti=self._cw_ti)
        except Exception:
            return None

    def poll_my_deck(self, now=None):
        """编辑界面卡组观察者：便宜路径（缓存实例）优先，全堆扫按 `RESCAN_SEC` 节流。

        ⚠️ 这是除「手动工具/一次性动作」之外**唯一**会读编辑场景的地方；别在别处再加全堆扫。
        """
        if self.sp is None or self.mirror is None or self.tbl is None:
            return None
        try:
            if self._deck_watch is None:
                from core.memread.my_deck_watch import (DeckEditReader,
                                                        MyDeckWatcher)
                self._deck_watch = MyDeckWatcher(
                    DeckEditReader(self.sp, self.mirror, self.tbl))
            return self._deck_watch.poll(now)
        except Exception:
            return None

    def read_duel_players(self):
        from core.memread.duel_result import read_duel_players
        try:
            if self._cw_ti is None:
                self._cw_ti = self.ticache.get('YgomSystem.Utility',
                                               'ClientWork') or None
            if not self._cw_ti:
                return None
            return read_duel_players(self.sp, self.mirror, self.tbl,
                                     cw_ti=self._cw_ti)
        except Exception:
            return None

    def read_peak_result(self):
        from core.memread.peak_rate import read_peak_result
        try:
            # P2-1 同款：ClientWork 的 TypeInfo 只找一次（缓存已热，零扫描）
            if self._cw_ti is None:
                self._cw_ti = self.ticache.get('YgomSystem.Utility',
                                               'ClientWork') or None
            if not self._cw_ti:
                return None
            return read_peak_result(self.sp, self.mirror, self.tbl,
                                    cw_ti=self._cw_ti)
        except Exception:
            return None

    def backfill_peak(self, dir_name):
        """巅峰积分落盘后补进库（与 `tools/backfill_peak_rate.py` 共用 apply_one）。"""
        import backfill_peak_rate as bpr       # 同目录 tools/
        from core.storage import Storage
        return bpr.apply_one(Storage(), self.records_dir, dir_name)

    def read_winbits(self, instance):
        """补读胜负位（同一个 EM 实例，隔几秒再来一次）。"""
        if not self._ensure_em_reader():
            return None
        try:
            return self.em_reader.read_winbits(instance)
        except Exception:
            return None

    def append_em(self, rec):
        path = DEFAULT_EM_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8', newline='') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    def import_record(self, dir_name):
        import import_duel_records as idr      # 同目录 tools/
        from core.storage import Storage
        return idr.process(records_dir=self.records_dir, storage=Storage(),
                           apply=True, only=dir_name)

    def backfill_rank(self, dir_name):
        """段位探测落盘后补进库（与 `tools/backfill_my_rank.py` 共用 apply_one）。"""
        import backfill_my_rank as bmr         # 同目录 tools/
        from core.storage import Storage
        return bmr.apply_one(Storage(), self.records_dir, dir_name)

    def backfill_account(self, dir_name, name, pcode, storage=None):
        """收局盖账号章（🔴 2026-10-03 多账号）。

        账号 = 收局段位探测**本来就在读**的 profile（``s_data["User"]
        ["profile"]`` 的 ``name`` / ``pcode``）—— 以前只取 rank/rate，现在
        顺手把账号带上（storage 侧只补空，反复盖幂等）。失败不阻塞。
        """
        from core.storage import Storage
        if not name and not pcode:
            return None
        n = (storage or Storage()).set_account_for_record(
            os.path.basename(dir_name), name, pcode)
        if n:
            return '账号章：%s（pcode=%s）' % (name, pcode)
        return None

    def backfill_duel_code(self, dir_name, code, code_name, storage=None):
        """结算码兜底**提前补结果**（🔴 2026-10-01 深夜第五刀）。

        无文案局的结果原来要等整读名额烧完（实测 83s）才凭结算码落库；
        现在结算码一确认就直写（**只补空**，storage 侧闸门）—— EM 窗口晚到
        的 winbit 仍可升级、行级修正不受影响。失败不阻塞（收口时照常 import）。
        """
        from core.storage import Storage
        got = result_from_duel_code(code)
        if not got:
            return None
        n = (storage or Storage()).set_result_if_null_by_record(
            os.path.basename(dir_name), got, 'duel_code')
        if n:
            return '结果（结算码提前入库）：%s（%s）' % (got, code_name)
        return None

    def repair_my_deck(self, dir_name, name):
        """卡组确认后的本局修正（审查 P1-1）：按 record_dir 直接 UPDATE。

        ⚠️ `rec.dir_path` 是绝对路径，库里 `matches.record_dir` 存的是**目录名**
        （实测 `record_20260918_194446`）—— 传库前取 basename。"""
        from core.storage import Storage
        n = Storage().repair_my_deck_by_record(os.path.basename(dir_name), name)
        if n:
            return '卡组确认入库：%s -> %s（本局行）' % (dir_name, name)
        return None

    def finalize(self, dir_path, reason):
        try:
            return rc.finalize_open(dir_path, reason=reason)
        except Exception:
            return None

    def read_duel_state(self):
        from core.memread.duel_state import DuelStateReader
        try:
            eng = self._engine_obj()
            if not eng:
                return None                      # 不在对局：静默，不算失败
            dc = find_singleton(self.sp, self.dc_ti,
                                slot=DUELCLIENT_SLOT) if self.dc_ti else 0
            if not dc:
                # 引擎实例在、DuelClient 不在：更像槽位/偏移错了（审查 P3-3）
                self._emit_fail += 1
                if self._emit_fail == 30:
                    self.log('⚠️ --emit-state 连续 30 拍读不到 DuelClient '
                             '（DUELCLIENT_SLOT=%s 对不对？）' % DUELCLIENT_SLOT)
                return None
            frame = DuelStateReader(self.sp, self.mirror).read(
                self.engine_ti, self._engine_obj(), self.dc_ti, dc)
            self._emit_fail = 0
            return frame.to_dict() if frame is not None else None
        except Exception:
            self._emit_fail += 1
            if self._emit_fail == 30:
                self.log('⚠️ --emit-state 连续 30 拍读帧失败（见前序日志）')
            return None

    def write_duel_state_file(self, frame):
        os.makedirs(os.path.dirname(EMIT_STATE_PATH), exist_ok=True)
        data = {'at': now_str(), 'state': frame}
        with open(EMIT_STATE_PATH, 'w', encoding='utf-8',
                  newline='') as f:
            json.dump(data, f, ensure_ascii=False)

    def _engine_obj(self):
        return find_singleton(self.sp, self.engine_ti, slot=ENGINE_SLOT)


def sample_engine(rt):
    """快路径采样：Engine 单例 -> (in_duel, game_mode, finished)。

    ``in_duel=None`` = 这拍没读成（不算收局）；False = 实例不在。
    """
    if rt.sp is None:
        return None, None, None
    try:
        obj = find_singleton(rt.sp, rt.engine_ti, slot=ENGINE_SLOT)
        if not obj:
            return False, None, None
        blob = rt.sp.read(obj, ENGINE_BLOB)
        mode = struct.unpack_from('<i', blob, rt.mode_off)[0]
        finished = (struct.unpack_from('<i', blob, rt.finished_off)[0]
                    if rt.finished_off is not None else None)
        return True, mode, finished
    except Exception:
        return None, None, None


# ---------------------------------------------------------------- 启动自愈

def load_pid_file():
    try:
        with open(PID_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_heartbeat(in_duel=False, game_pid=None):
    """每拍覆盖写心跳（看板据此判「采集器在不在跑」）。**失败不抛** —— 采样器

    每秒都在调，写不了就跳过（磁盘满 / 权限），绝不能因此打断采集。
    """
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_PATH), exist_ok=True)
        with open(HEARTBEAT_PATH, 'w', encoding='utf-8', newline='') as f:
            json.dump({'at': now_str(), 'in_duel': bool(in_duel),
                       'game_pid': game_pid, 'collector_pid': os.getpid()},
                      f, ensure_ascii=False)
    except OSError:
        pass


def write_pid_file(game_pid):
    os.makedirs(os.path.dirname(PID_PATH), exist_ok=True)
    with open(PID_PATH, 'w', encoding='utf-8', newline='') as f:
        json.dump({'collector_pid': os.getpid(),
                   'collector_born': proc_born(os.getpid()),
                   'game_pid': game_pid,
                   'at': now_str()}, f, ensure_ascii=False)


def clear_pid_file():
    try:
        os.remove(PID_PATH)
    except OSError:
        pass


def proc_image_name(pid):
    """取 pid 的**可执行文件名**（小写）；不存在 / 无权限时返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def proc_born(pid):
    """取 pid 的**进程创建时间**（Windows FILETIME 整数）；拿不到返回 None。

    ``(pid, 创建时间)`` 才是进程的**唯一身份** —— pid 会被系统复用，
    这个组合不会。
    """
    try:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                   ctypes.byref(k), ctypes.byref(u)):
                return (c.dwHighDateTime << 32) | c.dwLowDateTime
            return None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def pid_alive(pid, born=None):
    """该 pid 是否指向**活着的采集器**（而不是被复用给别的进程）。

    ⚠️ 不能只问 OpenProcess 成不成功 —— Windows 会**复用 pid**：
    2026-10-08 事故，10-06 那次采集器的 pid=15848 在 10-08 10:26 被系统
    分给了 svchost，于是 10:54 启动的新采集器误判「已有实例在跑」直接
    return 2，**当天整场零记录**（两局全丢）。
    所以必须校验 ①镜像名是 python/pythonw；②有创建时间记录时它也要一致。
    """
    if not pid:
        return False
    if proc_image_name(pid) not in COLLECTOR_EXES:
        return False
    if born:
        cur = proc_born(pid)
        if cur is None or int(cur) != int(born):
            return False
    return True


def import_just_closed(records_dir, names, log, importer=None):
    """把**刚被收口**的那几局补进库（逐目录 import；失败只记日志，不阻塞启动）。

    为什么需要（2026-09-18 实锤，Bug 3 的另一半）：被 kill / 被「停止」干掉的采集器
    会在磁盘上留下 open 目录，下次启动把它们标成 final 就**再也没人 import** ——
    记录永远躺在磁盘上（22:48 / 22:53 两局就是这么丢的）。
    只补**这一次刚收口的**，不碰历史目录（历史数据仍走手工
    `tools/import_duel_records.py --apply` —— 那条路涉及并档口径，别自动化）。
    """
    done = []
    for name in names or ():
        try:
            if importer is not None:
                importer(name)
            else:
                import import_duel_records as idr      # 同目录 tools/
                from core.storage import Storage
                st = Storage()
                st.init_schema()
                idr.process(records_dir=records_dir, storage=st, apply=True,
                            only=name)
            done.append(name)
        except Exception as e:
            log('  补入库失败（不阻塞启动；可手跑 '
                'tools/import_duel_records.py --record %s --apply）：%r'
                % (name, e))
    if done:
        log('已把刚收口的 %d 局补进库：%s' % (len(done), '、'.join(done)))
    return done


def self_heal(records_dir, game_pids, prev, log, importer=None):
    """open 目录终局化（P1-1）+ 终审 P3-2 口径：
    pid 变化 / 游戏未运行 -> 全部 open 即 stale；否则按时间（6h）。

    ★ 刚收口的这几局**顺手补 import**（见 :func:`import_just_closed`）。"""
    scans, broken = [], []
    if os.path.isdir(records_dir):
        for name in sorted(os.listdir(records_dir)):
            path = os.path.join(records_dir, name)
            if not os.path.isdir(path) or not name.startswith('record_'):
                continue
            try:
                scans.append(duel_record.scan_record(path))
            except Exception as e:
                broken.append((name, repr(e)))
    now = datetime.now()
    if not game_pids:
        stale = rc.find_stale_open(scans, now, all_open=True)
        reason_all = True
    elif prev and prev.get('game_pid') and \
            int(prev['game_pid']) not in [int(p) for p in game_pids]:
        stale = rc.find_stale_open(scans, now, all_open=True)
        reason_all = True
    else:
        stale = rc.find_stale_open(scans, now)
        reason_all = False
    if stale:
        log('启动自愈：%d 个 open 目录过期（%s）-> 标 final'
            % (len(stale), 'pid 变化/游戏未运行' if reason_all else '超时'))
    for s in scans:
        if s['name'] in stale:
            rc.finalize_open(s['dir'], reason='stale-open')
            log('  已标 final：%s' % s['name'])
    import_just_closed(records_dir, stale, log, importer=importer)
    return scans


# ---------------------------------------------------------------- CLI

def make_logger(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(msg):
        line = '%s  %s' % (now_str(), msg)
        try:
            with open(path, 'a', encoding='utf-8', newline='') as f:
                f.write(line + '\n')
        except OSError:
            pass
        print(line, flush=True)
    return log


def main(argv=None):
    ap = argparse.ArgumentParser(description='M6 常驻采集器（三 watcher 合一）')
    ap.add_argument('--interval', type=float, default=2.0,
                    help='快路径采样间隔秒（默认 2）')
    ap.add_argument('--write-gap', type=float, default=WRITE_GAP_SEC,
                    help='对局中两次全量落盘最小间隔秒（默认 5）')
    ap.add_argument('--minutes', type=float, default=0,
                    help='跑多久（0 = 常驻直到 Ctrl-C）')
    ap.add_argument('--note', default='', help='写进 manifest 的备注')
    ap.add_argument('--records-dir', default=DEFAULT_RECORDS)
    ap.add_argument('--log', default=os.path.join(
        PROJ, 'data', 'archive', 'duel_collector.log'),
        help='落盘日志（默认 data/archive/duel_collector.log）')
    ap.add_argument('--emit-state', action='store_true',
                    help='给 M7 的实时帧：每拍覆盖写 %s（默认关）'
                         % EMIT_STATE_PATH)
    args = ap.parse_args(argv)

    log = make_logger(args.log)
    log('=== duel_collector 启动（pid=%d, interval=%ss, emit_state=%s） ==='
        % (os.getpid(), args.interval, args.emit_state))

    # 双开检测（P3-9）：已有活实例就提示退出，不上锁
    # ⚠️ pid 文件是**陈旧判据**（只在启动写、优雅退出才删），被 kill 的实例
    #    会留下它；且 pid 会被系统复用（2026-10-08 svchost 事故）。
    #    所以这里必须用 pid_alive 校验「镜像名 + 创建时间」，别退回裸 OpenProcess。
    prev = load_pid_file()
    if prev and prev.get('collector_pid') != os.getpid():
        if pid_alive(prev.get('collector_pid'), prev.get('collector_born')):
            log('⚠️ 已有采集器在跑（pid=%s），本实例退出（不上锁，防双写）'
                % prev['collector_pid'])
            return 2
        log('（pid 文件里的 %s 已不是采集器：%s —— 按陈旧文件处理，继续启动）'
            % (prev.get('collector_pid'),
               proc_image_name(prev.get('collector_pid')) or '进程已不存在'))

    game_pids = find_pids_by_name('masterduel.exe')
    self_heal(args.records_dir, game_pids, prev, log)
    write_pid_file(game_pids[0] if game_pids else None)
    write_heartbeat(in_duel=False, game_pid=game_pids[0] if game_pids else None)

    rt = Runtime(default_game_dir(), log)
    rt.records_dir = args.records_dir
    hooks = rt
    collector = DuelCollector(records_dir=args.records_dir, note=args.note,
                              write_gap=args.write_gap,
                              emit_state=args.emit_state, hooks=hooks,
                              log=log)
    # EM 重启播种（P3-1）：已有样本的文案不再记
    try:
        if os.path.exists(DEFAULT_EM_PATH):
            with open(DEFAULT_EM_PATH, 'r', encoding='utf-8') as f:
                collector.seed_em(json.loads(line) for line in f
                                  if line.strip())
    except Exception as e:
        log('EM 播种失败（继续）：%r' % e)

    connected_pid = None
    deadline = (time.time() + args.minutes * 60) if args.minutes else None
    tries = 0
    try:
        while deadline is None or time.time() < deadline:
            pids = find_pids_by_name('masterduel.exe')
            if not pids:
                if connected_pid:
                    log('游戏关了，采集器待机（Ctrl-C 退出）')
                    connected_pid = None
                    rt.detach()
                    write_pid_file(None)
                    # 旧进程的结算画面永远没了：挂起必须作废，别带进下一局
                    for line in collector.on_game_gone():
                        log(line)
                # ⚠️ **待机期间也要写心跳** —— 不写的话游戏没开时心跳会过期，
                #    看板就会显示「采集器没在跑」（其实在跑，只是待机）。
                write_heartbeat(in_duel=False, game_pid=None)
                time.sleep(max(args.interval, 5.0))
                continue
            if rt.sp is None or connected_pid != pids[0]:
                # pid 变了重连（metadata 每进程一次，~10s）
                if rt.attach():
                    connected_pid = rt.pid
                    tries = 0
                    log('已连上 pid=%s' % rt.pid)
                else:
                    tries += 1
                    if tries == 1 or tries % 60 == 0:
                        log('…等待游戏就绪（第 %d 次）' % tries)
                    time.sleep(args.interval)
                    continue
            ts = now_str()
            in_duel, mode, finished = sample_engine(rt)
            write_heartbeat(in_duel=bool(in_duel), game_pid=connected_pid)
            try:
                for line in collector.observe(ts, in_duel, game_mode=mode,
                                              finished=finished):
                    log(line)
            except Exception as e:
                log('采集异常（不中断循环）：%r' % e)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log('Ctrl-C —— 优雅退出')
    finally:
        # 优雅退出：tracker.flush + 当前局收口 + **所有** open 目录标 final
        # （终审裁决 2 的零成本闸门：只有被 kill 才会留 open）
        for line in collector._close_current(now_str(), 'flushed',
                                             time.time()):
            log(line)
        scans, _br = hooks.scan_records()
        for s in scans:
            if s.get('state') == 'open':
                rc.finalize_open(s['dir'], reason='flushed')
                log('  已标 final（flushed）：%s' % s['name'])
        log('=== duel_collector 退出 ===')
        clear_pid_file()
    return 0


if __name__ == '__main__':
    sys.exit(main())
