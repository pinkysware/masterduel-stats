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
import time
from typing import Optional

from PySide6.QtCore import QSettings

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: QSettings 里记「已经跑过一次」的键 —— 兜底用（库非空时本来就不该再跑）
FLAG_KEY = 'firstrun/meta_decks_fetched'

#: 自检日志落盘处（与采集器同一个目录，见 tools/duel_collector.py）
LOG_PATH = os.path.join(PROJ, 'data', 'archive', 'first_run.log')


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


def ensure_meta_decks(app) -> Optional[object]:
    """库空就在后台抓一次清单 + 卡组图；返回 runner（不需要时返回 None）。

    * ``app`` = :class:`ui.app.MasterDuelUiApp`（要它的 ``storage`` 与 ``refresh``）。
    * **demo 模式直接跳过** —— 调试吃假数据，不联网、不写真库。
    * 已在抓 / 已抓过 → 返回 None，不重复起进程。
    """
    if getattr(app, '_demo', False):
        return None
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

    runner = ScriptRunner('tools/fetch_meta_decks.py', [], parent=app)
    runner.done.connect(on_done)
    app._firstrun_proc = runner
    runner.start()
    return runner