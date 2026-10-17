# -*- coding: utf-8 -*-
"""**首次启动**自动抓卡组清单 + 卡组图（2026-10-10）。

## 为什么需要它

发布包里 `data/masterduel.db` 被**故意排除**（运行期数据，不该带进公开包），
`data/deck_images/`（38 张站内卡组图）也曾被排除。于是新用户第一次打开时：

* `meta_decks` 表是**空的**（`init_schema()` 只建表结构，不填数据）
  → 卡组清单、卡组选择器、Tier 表**全是空的**；
* `deck_images/` 也不在 → 站内卡组即使有条目也**只能落到默认图**。

原先的口径是「`deck_images/` 可再生，首跑自动补」—— 但代码里**根本没有首跑
补图这一步**：该目录只由 `tools/fetch_meta_decks.py` 联网抓取时写入。
于是发布包首次打开必然缺图，而用户点「卡表更新」也修不了（那个按钮刷的是
**卡名表** `data/card_names.json`，与卡组图无关 —— 实测踩过，用户 2026-10-10）。

现在把这条链补上：**启动时若库是空的，就联网抓一次清单 + 下图**。

## 设计取舍

* **一次性**：靠 `meta_decks` 是否有行来判断，跑过一次就永远不再跑
  （也可用 `QSettings` 记标志兜底 —— 见 :func:`ensure_meta_decks`）。
* **静默失败**：抓不到（离线 / 墙 / 站挂了）**不打扰用户**，只记一行日志。
  没有卡图不等于不能用 —— 界面全程有 `_default.webp` 兜底。
* **后台跑**：用 :class:`ui.script_runner.ScriptRunner`，**不阻塞界面**，
  用户可以照常用；抓完再刷新卡图缓存。
* **绝不在 demo 模式跑**：调试模式吃假数据，不该联网、也不该写真库。

## 用法

    from ui.first_run import ensure_meta_decks
    ensure_meta_decks(self)      # 在 MasterDuelUiApp.__init__ 末尾调一次
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Optional

from PySide6.QtCore import QSettings
from core import paths

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: QSettings 里记「已经跑过一次」的键 —— 兜底用（库非空时本来就不该再跑）
FLAG_KEY = 'firstrun/meta_decks_fetched'

#: 自检日志落盘处（与采集器同一个目录，见 tools/duel_collector.py）
LOG_PATH = paths.data_path('archive', 'first_run.log')


def _log(msg: str) -> None:
    """首跑自检日志 —— 失败也要留痕（用户报问题时第一眼要看的就是这里）。"""
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:                                    # noqa: BLE001
        pass


def meta_decks_empty(st) -> bool:
    """`meta_decks` 是否**没有**站内条目（= 需要首跑播种）。

    ⚠️ 判据用「一条 `meta_decks` 都没有」，不是「有没有自定义条目」——
    只建了自定义条目、站内清单仍空，那也是**没播种过**（正常情况下两者一起来）。
    """
    try:
        return not (st.list_meta_decks(include_hidden=True) or [])
    except Exception:                                    # noqa: BLE001
        return False


#: 旧版便携包把数据放在**程序目录**（`<安装目录>/data`）。安装版换了
#: `MD_DATA_DIR` 之后，如果用户之前用过便携版，那些数据不应悄悄消失 ——
#: 首次启动搬到新位置（**只搬可写数据**，不搬随包素材）。
MIGRATE_FILES = ('masterduel.db', 'masterduel.db-shm', 'masterduel.db-wal',
                 'my_deck.json', 'rank_state.json', 'rank_state_writes.log',
                 'present_box_cache.json', 'site_deck_names.json',
                 'mdm_deck_cards.json', 'untapped_api_raw.json',
                 'untapped_ids.json')
MIGRATE_DIRS = ('archive', 'card_art', 'card_art_cropped',
                'deck_images_custom', 'fonts', 'thumb_cache')


def prepare_data_dir() -> list:
    """首次启动的**数据目录准备**：建目录 + （装成安装版时）迁移旧便携数据。

    返回一行行日志（调用方决定写哪）。**绝不抛** —— 建目录失败也要让界面起来，
    真正写库失败时用户会在采集器日志里看到更具体的原因。
    """
    out = []
    #: ⚠️ **先迁移、后建目录**（2026-10-18 实测）：`ensure_data_dirs()` 会先建出
    #: `archive/duel_records`（空），之后「目标目录空不空」的判据就失效 ->
    #: 对局记录一条都迁不过来。顺序反一下就没这个问题。
    legacy = paths.asset_path()       # = <安装目录>/data
    can_migrate = (not paths.is_portable()
                   and os.path.normcase(legacy) != os.path.normcase(paths.DATA_DIR)
                   and os.path.isdir(legacy)
                   #: 已经有库 = 本机已在用新位置，别再迁（否则覆盖现有数据）
                   and not os.path.exists(paths.data_path('masterduel.db')))
    if can_migrate:
        out.extend(_migrate_from(legacy))

    try:
        made = paths.ensure_data_dirs()
        for m in made:
            out.append('[paths] 新建目录 %s' % m)
    except Exception as e:                               # noqa: BLE001
        out.append('[paths] 建目录失败: %r' % (e,))
    return out


def _migrate_from(legacy: str) -> list:
    """把旧便携目录里的**可写数据**搬进 `DATA_DIR`（源保留，幂等）。"""
    out = []

    moved = []
    for name in MIGRATE_FILES:
        src = os.path.join(legacy, name)
        dst = paths.data_path(name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                moved.append(name)
            except OSError as e:
                out.append('[migrate] %s 复制失败: %r' % (name, e))
    for name in MIGRATE_DIRS:
        src = os.path.join(legacy, name)
        dst = paths.data_path(name)
        if not os.path.isdir(src):
            continue
        #: ⚠️ **不能用 `not os.path.exists(dst)` 判**：`ensure_data_dirs()` 会先建
        #: 出 `archive/duel_records`（空目录），于是这条永远为假 -> 对局记录
        #: 一条都迁不过来（2026-10-18 实测踩过）。
        #: 正确判据 = **目标里还没有内容**才搬（`dirs_exist_ok` 会合并，不会覆盖）。
        if os.path.isdir(dst) and os.listdir(dst):
            continue                      # 目标非空 = 已有更新的数据，别动
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            moved.append(name + '/')
        except OSError as e:
            out.append('[migrate] %s 复制失败: %r' % (name, e))
    if moved:
        out.append('[migrate] 从旧便携目录迁入 %d 项：%s'
                   % (len(moved), ', '.join(moved)))
        out.append('[migrate] 源目录保留未删（%s），确认无误后可手动删除'
                   % legacy)
    return out


def ensure_meta_decks(app) -> Optional[object]:
    """库空就在后台抓一次清单 + 卡组图；返回 runner（不需要时返回 None）。

    * ``app`` = :class:`ui.app.MasterDuelUiApp`（要它的 ``storage`` 与 ``refresh``）。
    * **demo 模式直接跳过** —— 调试吃假数据，不联网、不写真库。
    * 已在抓 / 已抓过 → 返回 None，不重复起进程。
    """
    if getattr(app, '_demo', False):
        return None
    #: ★ 2026-10-18：先准备数据目录（建目录 / 从旧便携目录迁数据）——
    #: 安装版装在 Program Files 时，可写数据在 `%LOCALAPPDATA%`，全新机器上
    #: 那目录还不存在，必须先建出来（否则第一次写库就失败）。
    for line in prepare_data_dir():
        _log(line)
    if getattr(app, '_firstrun_proc', None) is not None:
        return None

    st = getattr(app, 'storage', None)
    if st is None:
        return None

    s = QSettings()
    if s.value(FLAG_KEY):
        return None

    try:
        if not meta_decks_empty(st):
            # 库非空 = 已经有清单（用户自己跑过、或跑过首跑）→ 只记标志，不再联网
            s.setValue(FLAG_KEY, 1)
            return None
    except Exception:                                    # noqa: BLE001
        return None

    from ui.script_runner import ScriptRunner

    _log('[first_run] meta_decks 为空 → 后台抓卡组清单 + 卡组图')

    def on_done(code: int) -> None:
        app._firstrun_proc = None
        # 🔴 **必须丢掉 `DeckImages` 实例**：它的别名索引是**懒建一次**的缓存
        # （`core/deck_images.py` 的 `_ensure_index`），抓图前建的实例永远看不到
        # 新落库的 `img_file` —— 不丢就是「抓成功了但界面还是默认图」。
        app._imgs = None
        if code == 0:
            s.setValue(FLAG_KEY, 1)
            _log('[first_run] 卡组清单 + 卡组图抓取完成')
        else:
            # 失败**不写标志** → 下次启动会再试一次（离线用户联网后自然恢复）
            _log('[first_run] 抓取失败（退出码 %d），下次启动会重试；'
                 '期间卡图用默认图兜底' % code)
        try:
            app.refresh()
        except Exception:                                # noqa: BLE001
            pass

    #: 🔴 **不要传 `parent=app`**（2026-10-10 实机事故）：`MasterDuelUiApp` 是
    #: **普通 Python 类、不是 QObject**，而 `ScriptRunner`（QObject 子类）会把
    #: `parent` 交给 `QObject.__init__` —— 那要求 parent 必须是 QObject，
    #: 于是抛 `TypeError: QObject.__init__() called with wrong argument types`。
    #: ⚠️ 这个异常被 `ui/app.py` 启动处的 `try/except` 吞掉，**首跑播种从未真正
    #: 启动**：`first_run.log` 只留下一行「开始」、没有「完成」，
    #: `meta_decks` 永远 0 行 → 卡组选择器搜不到任何卡组。
    #: 生命周期不靠 parent —— `app._firstrun_proc` 持有强引用，够了。
    runner = ScriptRunner('tools/fetch_meta_decks.py', [])
    runner.done.connect(on_done)
    app._firstrun_proc = runner
    runner.start()

    #: 🔴 **第二段：缺图兜底** —— 榜单抓图只覆盖**当期榜单上**的卡组
    #: （实测 39 张）；榜外、以及站内图 404 的条目首跑后没有图（除非用户
    #: 自己放了覆盖图——`deck_images_custom/` 优先且下载会跳过）。
    #: 种子完成后跑 `--missing-images`：站内图 404 时自动改用**代表卡卡绘**
    #: 补齐，新用户的条目才不落悠悠兜底。
    def _then_missing():
        # ⚠️ 补图那只**不占 `_firstrun_proc`** —— 那个属性的语义是
        # 「清单播种」的 runner，`on_done` 抓完就把它清掉（单测锁着
        # 「抓完清引用」）。补图独立持有，防 GC 即可。
        miss = ScriptRunner('tools/fetch_meta_decks.py', ['--missing-images'])
        miss.done.connect(on_done)
        app._missing_proc = miss
        miss.start()

    runner.done.connect(lambda *_: _then_missing())
    return runner