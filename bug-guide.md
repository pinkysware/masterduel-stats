# Bug 定位手册

> **这是修 bug 的第一入口。** 别再通读 `AGENTS.md`（792 行）和历史 `当前进度.md`
> —— 那两份是规则归档与开发期记录，历史部分已移到 `docs/历史归档/`。
>
> 目标：**从「用户说了一句话」到「定位到文件:行号」用 3 分钟内**。
> 全文约 400 行，一次读完。行号是 2026-10-23 实测的，代码动了顺手改这里。
>
> **本手册随软件一起发布**（发版时作为独立产物，见 `docs/发布流程.md`），
> 所以正文里没有本机绝对路径；只有开发仓才有的文档标〔开发仓〕。

---

## §0 三分钟开工

### 0.1 解释器

```bash
# 开发机：python_path.txt 写着本机解释器；发布包解压目录：用自带的 python
PY=$(tr -d '\r\n' < python_path.txt 2>/dev/null)
PY=${PY:-python}
cd <项目根目录>
```

⚠️ **没有 pytest**（`requirements-dev.txt` 声明了，但本机解释器没装）。
**一律用 unittest**，别浪费时间排查 `No module named pytest`。
⚠️ `tests/` 没有 `__init__.py`，`unittest discover -t .` 会报 `not importable` —— **别带 `-t`**。

### 0.2 三条基线命令（动手前必跑）

```bash
# ① 单模块（改哪跑哪，秒级）—— 日常主力
$PY -m unittest tests.test_ui_state -v

# ② 全量（2711 项，约 3.5~4 分钟）—— 只在收工 / 动过 core 时跑
$PY -m unittest discover -s tests -p 'test_*.py' 2>&1 | grep -E "^Ran |^OK|^FAILED"

# ③ 数据体检（30 秒）
$PY tools/audit_issues.py
```

**2026-10-23 实测基线**：`Ran 2711 / FAILED (failures=1, skipped=2)`。

⚠️ 那 1 条红是**真 bug**：`tests/test_ui_i18n.py::GuardAllTrKeysExist` ——
`ui/dashboard.py:1460` 用了 `tr('DC杯（1 阶段：LV1~LV20，没有 DP）')`，
但 `ui/i18n.py` 的 `_T` 没这个键。修 bug 时顺手补上。

### 0.3 六条铁律（违反必返工）

| # | 规则 |
|---|---|
| 1 | **中文**交流 |
| 2 | **改完不打包、不推 GitHub**。push / tag / Release **必须用户逐次明确允许**；说「commit」≠ 说「push」 |
| 3 | **含中文的文件禁用 Edit 工具**（会把汉字第三字节变成 `0x3F`）。用 Write / Python 写；Python 写要带 `newline=''` |
| 4 | 别读 `Network\Cookies` / `Session Storage`；**战绩数据绝不来自抓取**（合规基线） |
| 5 | **别截图给用户看**（用户 2026-09-18 明令）—— 出图只作自检，汇报说「去哪儿看」 |
| 6 | **别问能自己查到的事** —— 先翻本文 / 代码 / 库；翻不到再问，且要问得具体 |

`core/memread/redline.py` 的红线由 `tests/test_memread_guard.py` 静态扫描强制：
不写内存、不注入、不碰千年眼、不碰「双方卡组全集」那个字段 ——
**业务代码里连注释出现禁词都算违规**。

---

## §1 症状 → 文件速查表

> 用户报现象 → 找对应行 → 「第一刀」去看 → 不对再走「第二刀」。
> 每条都给了判定命令，别靠猜。

### 1.1 采集：打完一局，数据没进来

| 症状 | 第一刀 | 第二刀 | 判定 |
|---|---|---|---|
| 打完一局**完全不显示** | `data/archive/duel_collector.log` 有没有 `入库：` | `tools/duel_collector.py:1233 _finish_record` → `:1257 _finish_record_steps` | `tail -20 data/archive/duel_collector.log` |
| 采集器**根本没在跑** | `data/archive/collector_heartbeat.json` 的 `at` 是否 60 秒内 | `core/ui_state.py:2198 collector_status()` | **别看 pid 文件**（stale 会骗人） |
| 采集器**起不来 / 双开** | 日志 `已有采集器在跑（pid=…）` | `tools/duel_collector.py:2885` 附近 | 日志说「已不是采集器」= 陈旧文件，正常继续 |
| 采集器**连不上游戏** | `attach()` 的 `last_attach_error`（日志 `——` 后那句） | `tools/duel_collector.py:2146 attach()` 六步链 | 见 §1.5 对照表 |
| 库里**少统计一局** | `$PY tools/audit_issues.py` → 漏记段 | `core/ui_state.py:2611 audit_issues()` | `orphan_pair_exists` 才是真漏记；`orphan_no_pair` = 观战/单人，正常 |
| 结果缺失（`result` 空） | `audit_issues` → 缺结果段 | `core/memread/end_message.py` + `tools/duel_collector.py:510 capture_end_message` | 结算窗口只几秒，事后**只能手补**：`core/ui_actions.py:230 repair_result()` |
| 一局拆成两行 / 两局并一行 | `data/archive/duel_records/*/manifest.json` 的 `instance` | `core/memread/record_capture.py:341 find_reusable_dir()` | 同 `instance` 才是同一局 |
| 目录 `state=open` 永不入库 | `$PY tools/import_duel_records.py --list-stale` | `record_capture.py:382 find_stale_open()` / `:403 finalize_open()` | 修：`--finalize-stale` |

**采集主链路**：找进程 → `attach()`(:2146) → `_track_record()`(:1073) →
`_open_dir()`(:1209) → 对局中每 5 秒 `_write()`(:1459) → `_finish_record()`(:1233) →
`import_record()`(:2470) → `Storage.upsert_match()`（`core/storage.py:953`）。

### 1.2 数据对不对（口径 / 段位 / 卡组 / 卡名）

| 症状 | 第一刀 | 第二刀 | 判定 |
|---|---|---|---|
| **段位假升段 / 掉段判错** | `core/rank_state.py:70 demote_line()` + `:186 next_state()` | `data/archive/rank_state_audit.log`（真机写入取证，带调用栈） | 掉段：白金/钻石/大师**统一连输 3**；升段：低段 3、白金钻石 4、大师 5 |
| 段位**整局空白**显示「…」 | `core/ui_state.py:812 rank_pending()`（60 秒窗口） | 采集器日志有没有 `段位入库：record_xxx` | 窗口内 = 获取中（正常）；过窗还空 = 真没探到 |
| 段位**差一小段** | `core/storage.py:1643 repair_rank_before_for_record()` | `$PY tools/backfill_my_rank.py --list` | 探测真值盖推断值的唯一入口 |
| **我方卡组标错**（上一副牌） | 库里 `my_deck_name_src` 的值 | `tools/import_duel_records.py:206 MY_DECK_MAX_AGE_SEC` | `current-deck` 兜底只对天梯 + 24h 内；`snapshot-name` 才权威 |
| **对手卡组反推不准** | `core/deck_guess.py:568 suggest()` | `$PY tools/mark_opponent.py --guess --backtest` | 看回测四分组是哪类错 |
| 卡名显示 `#12345` | `core/card_names.py:92 display()` | `data/card_names.json` 有没有这个 id | 表过期 → `tools/build_card_names.py --check-update` |
| 卡名语言不对 | `core/card_names.display(cid, lang)` + `ui/i18n.py` | QSettings `ui/lang` | 切语言要**重开窗口**才全生效 |
| 统计场次对不上 | `core/storage.py:721 counted_clause()` | `core/storage.py:192 duel_clause()` | **主口径**（天梯 3 + 巅峰 19）vs **界面口径**（+DC 12 +活动）。两份都有意，**别统一** |
| 「本轮」不清零 | `core/storage.py:798 session_start_of()`（60 分钟，比**结束**时间） | `core/ui_state.py:463 snapshot()` | 隔 60 分钟再打一局应归零 |

### 1.3 卡图 / 图标

| 症状 | 第一刀 | 第二刀 | 判定 |
|---|---|---|---|
| 卡组图是**默认悠悠** | `core/deck_images.py:341 resolve()` 五环链 | `data/deck_images_custom/<名字>.webp` 在不在 | 第 0 环 = 覆盖目录按名字直查，**优先于**别名索引 |
| 卡组图**清不掉** | `storage.set_meta_deck_override()` | 约定 **`None`=不动 / 空串=清空** | 传 `None` 会让假图永远清不掉（2026-10-10 踩过） |
| 卡全图（禁限表/手坑）不出来 | `core/card_art.py:151 art_path()` / `:48 ART_URL` | `data/card_art/` 缓存 | ⚠️ 这里用**整卡** `cards/<konamiID>.jpg` 是**对的** |
| 段位/DC/巅峰图标不显示 | `core/modes.py:100 badge()` + `WANTED_ICONS` | `data/mode_icons/{peak,dc}.webp`、`data/rank_icons/` | 文件不在时**别回落别的图标**（显示错模式更糟） |
| 图标被裁成方块 / 有灰底 | `ui/widgets.py:150 round_pixmap_fit()` | `thumb_label(..., background=False, fit=True)` | DC 是 2:1 宽扁图，`round_pixmap` 会砍掉左右 |

### 1.4 界面（看板 / 悬浮窗 / 选择器）

| 症状 | 第一刀 | 第二刀 | 判定 |
|---|---|---|---|
| 文案**被裁 / 显示不全** | 全局 QSS 的 `padding:6px 10px` | 该控件有没有紧样式 | 20px 高框套全局 QSS 只剩 6px。**搜索框也要单独套** |
| 列宽**漂移 / 数字省略成 `1515…`** | `ui/theme.py` 的 `PAGE_*` / `RAIL_*` | `ui/dashboard.py:1038 stats_col_widths()` | ⚠️ **无字体环境**下所有文本量成 98px，**别据此改列宽** |
| 看板**不刷新** | `ui/app.py:604 tick()` → `ui_state.probe()` | `core/ui_state.py:85 Probe.key()` | **新状态必须进 `key()`**，否则永不触发（踩过三次） |
| 悬浮窗**宽度自己变 / 藏列不缩** | `ui/floating.py:478 invalidate_width_cache()` | `:451 window_width()` / `:341 column_widths()` | 宽度缓存键不含新设置项就是这症状 |
| 悬浮窗**卡组确认条不动** | `ui/deck_banner.py:174 update_state()` | `core/my_deck.py:70 state_fingerprint()` | 指纹必须进 `Probe.key()` |
| 悬浮窗图标**跨行没对齐** | `RankBadge` / `badge_group_width()` | 用户 2026-10-23 定：**一律贴左**，别做居中 | 居中会让天梯 x=127、DC x=151 |
| 选择器**搜不到卡组** | `core/deck_match.py:84 best_score()` | 覆盖表 `data/deck_name_cn.csv` 的「别名」列 | 中文/日文/别名/拼音（`core/pinyin_match.py`，仅 lang=cn） |
| 输入框**打字没过滤** | 该页 completer 模型 | `ui/selector.py` / `ui/opp_deck_manager.py` | 独立 completer 模型，别和主列表共用 |
| 切卡组**卡图不下** | `_art_busy` 没复位 | `ui/deck_view_page.py:326 set_data()` + `:566` | 代次令牌 + `finally` 复位，两件都要 |
| **Tier 表 / 卡组页空白** | `core/ui_state.py:1635 tier_page_data()` | `meta_decks` 表 `fetched_at` | 过期 → 设置区「刷新 Tier 榜单」 |
| **卡组构成**「参照库里没有这副牌」 | `core/ui_state.py:2504 deck_composition()` | `data/mdm_deck_cards.json` 的 `fetched_at` | 刷新：设置区「参照库刷新」 |
| 导出**少列 / 顺序错** | `core/ui_state.py:2255 EXPORT_COLUMNS` | `core/ui_state.py:2319 export_rows()` | 段位两列 = 「局前/局后段位/积分」：天梯填段位、巅峰填 RATE、DC 2 阶段填 `DP 1000` |
| **手坑**两列全是「—」 | `core/duel_record.py:370 summarize_hand_traps()` | 库里 `my_traps` / `opp_traps` 是否 `NULL` | `NULL` = 判不出；`[]` = 确实没用过。**两种语义别混** |
| 设置里某项**点了没反应** | `ui/script_page.py:48 SCRIPT_COMMANDS`（唯一命令表） | `ui/dashboard.py` 的 `SETTINGS_PAGES` 页 key 对不对得上 | 对不上 = 落到 PlaceholderPage |
| **打不开软件**（启动器报错退出） | 安装目录**带空格**吗（默认 `C:\Program Files\...` 就是） | `build/launch_installed.bat` 有没有 `for /f ... in (` 命令替换 | 见 §1.6 |

### 1.5 内存 / 采集器异常（要开游戏才能验）

| 症状 | 第一刀 | 第二刀 | 判定 |
|---|---|---|---|
| 一直「等待游戏就绪」 | `last_attach_error`（日志 `——` 后那句） | `tools/duel_collector.py:2146 attach()` | 见下方对照表 |
| **游戏大版本更新后全挂** | `$PY tools/check_game_update.py --offline`（不用开游戏，秒级） | 再 `--live`（开游戏，深检 TypeInfo / 字段表） | 〔开发仓〕`docs/游戏更新应对计划.md` |
| 段位/巅峰/DC 积分读不到 | `core/memread/peak_rate.py` / `core/memread/cup_dp.py` | `duel_records/<dir>/{my_rank_after,cup_dp}.json` | DC 的 DP 只认 `stage=2`；**DP 是整数、RATE 是 2 位小数** |
| 手坑 / 先后手**判反** | `core/duel_record.py:816 infer_seat()` | `core/memread/duel_log.py:397 revealed_by()` | 首回合双方 LP 相等才判得出先手；LP 上限必须 20000 |
| **游戏找不到 / metadata 读不到**（attach 报 `没找到 metadata 镜像` 等） | `core/memread/end_message.py default_game_dir()` 探测链 | 向导「手动选择」写的 `game_dir.ini`（程序根） | 探测顺序：环境变量 → game_dir.ini → **进程反查**（开着游戏必中）→ Steam 注册表+libraryfolders.vdf → 旧 D 盘常量兜底 |

**`attach()` 失败对照**：

| 报错文本 | 根因 | 下一步 |
|---|---|---|
| `没找到 masterduel.exe 进程` | 游戏没开 | 正常等待 |
| `metadata 头部不合法` | **游戏版本变了** | `check_game_update.py --offline` |
| `没找到 metadata 镜像` | 还在加载 / 版本改了格式 | 等加载；仍失败查版本 |
| `没定位到 …Engine 的 TypeInfo` | 类改名 / `il2cpp.py` 布局常量坏 | `check_game_update.py --live` |
| `Engine 字段表里没有 gameMode` | 字段改名 | 同上 |

⚠️ `core/memread/il2cpp.py` 的偏移常量（`+0xB8` 静态字段区、`+0x10` name 等）
是 v31 校准值 —— Unity 大版本升级时最脆弱，**坏了不报错、只是拿不到数据**。

### 1.6 启动器 / 安装版打不开（**P0，发布阻断**）

**症状**：双击没反应，或黑窗口一闪而过，报
`'C:\Program' 不是内部或外部命令` + `[ERROR] could not determine the duel data folder.`

**根因**（2026-10-24 用户实机首报，已复现）：默认安装目录是
`C:\Program Files\Master Duel Stats`（**带空格**），于是 `%PY%` 也带空格。
而 cmd 的 `for /f ... in (`命令`)` 会**二次解析**命令行，
带引号的 exe 路径在第一个空格处被截断 → python 根本没被执行 → `DATA_DIR` 空 → 报错退出。

**影响面**：默认目录安装的用户 **100% 打不开**（不是偶发）。

| 判据 | 说明 |
|---|---|
| 装到 `C:\Program Files\...` | 中招 |
| 装到 `D:\mdstats`（无空格） | 正常 —— 所以开发机测不出来 |
| 便携版 `start.bat` | 不受影响（它不用命令替换取 DATA_DIR） |

**修法**：**不要把命令塞进 `for /f` 的反引号** —— 先重定向到临时文件，再读文件
（文件读取不做二次解析）：

```bat
REM ❌ 带空格路径会截断
for /f "usebackq delims=" %%D in (`"%PY%" -c "..."`) do set "DATA_DIR=%%D"

REM ✅ 先重定向到文件
"%PY%" -c "..." > "%MD_TMP%" 2> "%MD_TMPERR%"
for /f "usebackq delims=" %%D in ("%MD_TMP%") do set "DATA_DIR=%%D"
```

回归锁：`tests/test_build.py::test_launcher_survives_path_with_spaces`
（把反引号写法改回去，这条立刻变红 —— **已负向验证**）。

⚠️ 改这类 bat 时两条：① **必须纯 ASCII**（cmd 按 GBK 跑，中文会碾坏，
有 `test_launcher_is_ascii` 盯着）；② 报错分支要**把 python 的 stderr 打出来**
（原来被 `2>nul` 吞了，只能靠截图猜）。

**用户等不到新包时的临时绕过**（程序本体没坏，只是启动脚本坏了）：

```bat
cd "C:\Program Files\Master Duel Stats"
python\pythonw.exe tools\duel_ui.py
```

⚠️ 这条**不启动采集器** —— 想连采集一起跑，另开一个窗口再执行
`python\pythonw.exe tools\duel_collector.py --minutes 0`。

---

## §2 数据链路图

```
【两条采集线，互补 —— 别以为有一条就够】

  ① 日志线（Untapped）                  ② 内存线（自研只读）
     %APPDATA%/untapped-companion/log.log     masterduel.exe 进程
     ├ 结束时刻 / 时长 / 掉线判负             ├ 对手打出的卡 opp_revealed  ★日志没有
     ├ 对手打出的卡 opp_revealed ★           ├ 开局时刻 / 我方卡组快照
     └ 对手名 / 段位 / game_mode ✗            └ 段位真值 / 巅峰 RATE / DC DP
              │                                       │
     core/log_tailer.py:65                    core/memread/*.py
     tools/archive_matches.py                 tools/duel_collector.py
              └──────────────┬────────────────────────┘
                             ▼
                  core/storage.py  ← 唯一真源 data/masterduel.db
                  ├ upsert_match()      :953   （KEEP_IF_NEW_NULL / MANUAL_FIELDS 决定谁不被覆盖）
                  ├ counted_clause()    :721   （主口径：天梯 3 + 巅峰 19）
                  ├ duel_clause()       :192   （界面口径：+ DC 12 + 活动）
                  └ session_start_of()  :798   （本轮：60 分钟阈值，比结束时间）
                             ▼
                  core/ui_state.py  ← 数据层（UI 不写 SQL）
                  ├ probe()  :132      ├ snapshot()          :463
                  ├ _page_row() :911  ★一局→一行的唯一出口（两处界面共用）
                  ├ stats_page_data() :1166   ├ cross_matrix()      :1465
                  ├ tier_page_data()  :1635   ├ banlist_page_data() :1730
                  ├ deck_composition():2504   └ export_rows()       :2319
                             ▼
                  ui/app.py:604 tick() → refresh() :656 → win.set_snapshot()
                  ├ ui/floating.py   悬浮窗
                  └ ui/dashboard.py  看板
```

**怎么用这张图二分**：

1. 现象在**界面** → 先查**库**里那行对不对（§3.2）。
   - 库对 → 问题在 `ui_state` 之后（`_page_row` 字段 / 界面画法）。
   - 库错 → 问题在 `storage` / 采集侧。
2. 库错 → 看 **record 目录**在不在（`data/archive/duel_records/record_*`）。
   - 目录在、库没有 → `import_duel_records`（跑 `--record <dir>` 空跑看报告）。
   - 目录都没有 → 采集器。
3. 界面显示的是 `_page_row()` 给的**格式化值**（`peak_rate` 已是 `'1507.49'`）——
   格式错先看那里，别改界面。

---

## §3 现场取证命令

### 3.1 采集器现场

```bash
tail -30 data/archive/duel_collector.log                          # 最近动作
grep -E "入库|失败|attach|DC杯积分|巅峰积分|段位" data/archive/duel_collector.log | tail -20
cat data/archive/collector_heartbeat.json                         # ★ 判活看 at 新鲜度
$PY tools/import_duel_records.py --list-stale
$PY tools/import_duel_records.py --finalize-stale
```

### 3.2 查库（**只读**，别手改真库）

```bash
$PY -c "
import sqlite3
c = sqlite3.connect('file:data/masterduel.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
for r in c.execute('''select started_at,result,my_deck_name,opp_deck_name,
                             game_mode,origin,result_src,record_dir
                      from matches order by started_at desc limit 10'''):
    print(dict(r))
"

# 缺结果的局
$PY -c "import sqlite3;c=sqlite3.connect('file:data/masterduel.db?mode=ro',uri=True);print(c.execute(\"select started_at,record_dir from matches where result is null and ended_at is not null\").fetchall())"

# 段位真值排查（假升段用）
$PY -c "import sqlite3;c=sqlite3.connect('file:data/masterduel.db?mode=ro',uri=True);print(c.execute(\"select started_at,my_rank_before,my_rate_before,my_rank_after,my_rate_after from matches where game_mode=3 order by started_at desc limit 20\").fetchall())"
```

### 3.3 UI 复现（**不碰真库**）

```bash
$PY tools/duel_ui.py --demo         # ★ 做 UI 一律先吃 demo（9 行覆盖全形态）
$PY tools/duel_ui.py --db tmp/x.db  # 造假数据试界面
$PY tools/render_ui_demo.py         # 出预览图自检（⚠️ 别贴给用户）
```

### 3.4 单跑测试（改哪儿跑哪儿）

```bash
$PY -m unittest tests.test_ui_state -v            # 数据层（158 项，0.4 秒）
$PY -m unittest tests.test_ui_dashboard -v        # 看板（277）
$PY -m unittest tests.test_ui_floating -v         # 悬浮窗（146）
$PY -m unittest tests.test_duel_collector -v      # 采集器（152）
$PY -m unittest tests.test_storage -v             # 存储（131）
$PY -m unittest tests.test_deck_guess -v          # 反推（65）
$PY -m unittest tests.test_import_duel_records -v # 入库（38）
$PY -m unittest tests.test_ui_i18n -v             # 三语键守卫（11）★ 当前这条红
```

### 3.5 数据侧 / 其他

```bash
$PY tools/audit_issues.py              # 三类事故：缺结果 / 漏记 / 未收口
$PY tools/audit_issues.py --json
$PY tools/archive_matches.py --stats   # 主口径（只读）
$PY tools/archive_matches.py --stats --group-stats
```

| 日志 | 位置 | 看什么 |
|---|---|---|
| 采集器 | `data/archive/duel_collector.log` | 入库成功 / 失败 / attach |
| 段位写入 | `data/archive/rank_state_audit.log` | **谁写的段位**（带调用栈） |
| 段位状态 | `data/archive/rank_state_writes.log` | 每次变更前后值 |

---

## §4 踩坑禁区（动手前先读）

### 4.1 改代码

| # | 坑 | 后果 |
|---|---|---|
| 1 | **含中文的文件用 Edit 工具改** | 汉字第三字节变 `0x3F`（`?`）。用 Write / Python；Python 写带 `newline=''`，否则整个文件显示被改动 |
| 2 | **加列只改一处** | 必须**四处联动**：`SCHEMA_SQL` / `ADDED_COLUMNS` / `MATCH_FIELDS` / `KEEP_IF_NEW_NULL`。漏 `MATCH_FIELDS` = 采集器写了也静默丢；老库还要跑 `Storage().init_schema()` 才长列 |
| 3 | **`Probe.key()` 不带新状态** | 界面永不刷新。已踩三次：`session`（跨 60 分钟）、`backfill`（回填后段位不显示）、`my_deck_fp`（换卡组提示条不动） |
| 4 | **在无字体环境判列宽** | `families()==[]` 时所有文本量成 98px，`1515.62` 被误判放不下。真实 Inter 下约 50px，74px 格子够用 |
| 5 | **`find_image_file()` 兜底默认图** | 会把 `_default.webp` 写进库且**永远救不回来**。兜底只能放界面侧（`DeckImages.resolve` 第 3 环） |
| 6 | **`set_meta_deck_override` 传 `None` 当清空** | 约定 **`None`=不动 / 空串=清空**。传错假图永远清不掉 |
| 7 | **`rank_state.STATE_PATH` 测试里没隔离** | 跑一次全量就**写真机** `data/rank_state.json`（假升段事故根因之一）。判据用导入时的 `DEFAULT_STATE_PATH` 常量，不是可变的 `STATE_PATH` |
| 8 | **想「统一」`counted_clause()` / `duel_clause()`** | 两份口径都是用户拍板的，别合并 |
| 9 | **硬编码 `PROJ/data`** | 安装版在 Program Files 不可写。一律 `core/paths.py` 的 `data_path()`（可写）/ `resolve()`（只读素材优先） |
| 10 | **手写 `.tmp + os.replace`** | 统一用 `core/atomic.py`（唯一实现，**刻意不 makedirs**） |
| 11 | **`tr('新文案')` 忘了进 `_T`** | `tests/test_ui_i18n` 会红（**当前就有一条**）。中文原文就是键 |
| 12 | **改完顺手 push / 打包** | 铁律 2。说「提交」只代表 commit |

### 4.2 界面

| # | 坑 | 后果 |
|---|---|---|
| 13 | 全局 QSS `padding:6px 10px` 套到小框 | 20px 高输入框只剩 6px 内容区，字被上下切。**每个新输入框都要套紧样式** |
| 14 | 表头用 `addSpacing` 对齐 | 不算项间距，差 4px。要用**真 spacer 控件**；表头标签还要 `QSizePolicy.Ignored` |
| 15 | 在 `paintEvent` 里 `import PySide6` | 2026-09-16 段错误同款。一律模块顶部 import |
| 16 | show 之前用 `height()` | 排队期恒为 0。用 `sizeHint()`，`showEvent` 里补一次 |

### 4.3 数据与合规

| # | 坑 | 后果 |
|---|---|---|
| 17 | 以为「API 全都有，不用日志」 | 错。对手打出的卡日志有 19/35 局、API **0/1680**；时长/结束时刻/掉线也只在日志 |
| 18 | 读 `Network\Cookies` / `Session Storage` | 绝对禁止 |
| 19 | 碰「双方卡组全集」那个内存字段 | 名字在 `core/memread/redline.py` 的 `FORBIDDEN_STRUCT_FIELDS`，**注释里出现也算违规** |
| 20 | 拿 9 月数据当 bug 证据 | 2026-09 是开发期脏数据（含 untapped 导入）。**优先用 10 月数据复现** |

---

## §5 收尾

1. **先写能失败的测试**（P0/P1 必配）。临时测试放 **`pending_tests/`**（在 `tests/` 之外，
   放 `tests/` 里会与「全绿」验收打架）；修好后再决定搬不搬进 `tests/`。
2. 改完跑**相关模块**单测。只改 UI 时**别跑全量**（用户 2026-10-17 明确要求）。
3. 动了行为**就更新本文**：新坑写进 §1 或 §4。
4. **更新 `docs/当前进度.md`**（40 行接力棒，只写「这一刀切了哪、下一步」）。
5. **不打包、不 push**。汇报写清：改了什么、去哪儿验证、还差什么。

---

## 附录 A 仍然有效的项目口径

> 开发期沉淀的决策，修 bug 时必须遵守。**细节**（具体某卡组叫什么、日文名表、
> 各 CDN 的失效记录…）在〔开发仓〕`docs/历史归档/`，按需去查，别通读。

### A.1 统计口径**分两份，别混**

| 口径 | 包含 | 用在哪 | 函数 |
|---|---|---|---|
| **主口径** | 排名决斗(3) + 巅峰决斗(19) | CLI `--stats`、库内按月/按天聚合、`--group-stats` | `counted_clause()` |
| **界面口径** | 天梯 + 巅峰 + **DC 杯(12)** + 活动 | 悬浮窗「总览」「先后攻统计」 | `duel_clause()`，`mode_scope='duel'` |

杯赛/活动「单独统计」**不进主口径**（用户 2026-09-13 拍板）。分类一律走 `mode_group()`。

**时间口径 = 本轮**：上一局**结束** → 本局开局 ≥60 分钟 → 本轮结束，两块回归**零值形态**
（胜负 `0-0`、先攻/后攻 `0`、比率 `-`），不是「没有数据」。
⚠️ 比**结束**不是开局 —— 超长对局会把自身时长白算进间隔。
⚠️ 「本轮结束」不能用 `since=None`（那是全库统计），也不能把 stats 置空（那是没有数据）。

### A.2 硬币 ≠ 先后手（本项目最大的认知陷阱）

* `won_initiative` = **赢得选择权**（硬币/掷币），库里**稀疏**（日志线只 3/5489）
* `went_first` = **实际先攻**，100% 覆盖

两个字段永久分开。导出页「硬币」列与「先后手」列也是两回事。

### A.3 两个数据源互补（见 §2 图）

日志独有：结束时刻、时长、**对手打出的卡**、掉线判负。
API 独有：对手名、双方段位、`game_mode`、完整硬币记录。

### A.4 卡组名以**覆盖表**为准，不跟站内叫

`data/deck_name_cn.csv`（Excel 直接改，跑 `--cn-only` 进库）。表头认列，7 列：
站内英文名 / 中文名 / 日文名 / 别名 / 分组 / 隐藏 / 备注。

| 场景 | 做法 |
|---|---|
| 站内叫 `Xxx Engine`（混轴） | **不用这个分类**。`kind='engine'` 的行保留但 `hidden=1`（Tier 表要列全混轴）；覆盖表填了「显示英文名」的才改造成独立条目 |
| 同卡组不同叫法（`Zombies`=不死均、`Odion`=利希德） | **不新建条目**，写进保留方的「别名」列，多余那行标 `隐藏=Y` |
| 站内没有的卡组 | 直接往表里加一行 → 工具建 **自定义条目**（`source='custom'`），图放 `data/deck_images_custom/`，**月度重抓不会清理** |
| 游戏内自组名想看到逐卡使用率 | 把该名字写进某个站内条目的「别名」列 → `match_ref_key()` 就能落到那条 |

**卡组图 = 该卡组「代表卡」的卡绘**（站方单独指定的，不是 100% 的第一张）。
必须**纯卡绘**（`cards_cropped`，无框无文本）；禁限表/卡组构成要的是**整卡**（`cards/`）—— 两族并存，别混。
换图五条路：看板「卡组名管理」点卡图（主路）/ `tools/set_deck_image.py` / 站内取图 / 手放文件 / `--missing-images` 补缺。
卡图一律归一化成 **200×200 webp**（`core.deck_images._normalize_to_webp`，core 一份实现 CLI 与 UI 共用）。

**多语言**：语言**只影响显示，不影响匹配**（匹配走卡 id）。卡名 `data/card_names.json`
（14610 张，**5 个顶层键，卡表在 `d["names"]` 里**）；缺哪种语言就顺延（请求的→中→英→日）。
⚠️ 唯一真源 join 键是 ygocdb 的 `cid`（= 游戏内卡 id），**不是** `id`（Konami 密码）。

### A.5 数据真源

| 项 | 值 |
|---|---|
| **唯一真源** | `data/masterduel.db`（SQLite），读写一律经 `core/storage.py` |
| meta 卡组清单 | `meta_decks` 表（外部快照，按月覆盖；与用户自维护的 `opp_decks` **分开**） |
| 禁限卡变更史 | `banlist_changes` + `banlist_current`（后者是站内 `banStatus` 权威值） |
| 已退役 | `data/archive/matches.jsonl.v1.bak`（**代码不再读**） |

> ⚠️ **加列的规矩**（2026-09-16 事故）：`ADDED_COLUMNS` 只是改 schema 定义，
> 老库要跑 `Storage().init_schema()` 才长列。采集器那条入库路径自带；在那之前加列 =
> 每局入库 `no such column` **静默失败**，表象是「打完一局悬浮窗不显示」。
> **排查先看 `duel_collector.log` 有没有 `import 失败`。**

### A.6 常用命令（按月 / 手动跑）

```bash
$PY tools/fetch_meta_decks.py          # meta 卡组清单（默认走 API；--cn-only 只写库不联网）
$PY tools/fetch_banlist.py             # 禁限表变更史
$PY tools/fetch_mdm_decks.py --all     # 逐卡使用率参照库（102 条，2-3 分钟）
$PY tools/build_card_names.py --check-update   # 卡表上游比对（退出码 1 = 有未知卡要人工看）
$PY tools/duel_collector.py            # 常驻采集器（打之前跑上）
$PY tools/audit_issues.py              # 数据体检
```

**masterduelmeta 有公开 JSON API**（`/api/v1/deck-types`、`/api/v1/cards`、`banlist-changes`）——
`fetch_meta_decks.py` / `fetch_banlist.py` 已走它，别重构回 HTML 解析。
`robots.txt` 只禁 `/cdn-cgi/`，与本项目无关；低频使用（每月一次）、本地缓存。

### A.8 一键可用三件套（2026-10-24）

装完打开即能用的三根支柱，出问题时按这里查：

| 件 | 落点 | 说明 |
|---|---|---|
| **游戏目录自动探测** | `core/memread/end_message.py:default_game_dir()` + `core/memread/process.py:exe_path()` | 优先级：`MD_GAME_DIR` 环境变量 → `game_dir.ini`（向导手动选）→ 进程反查（`masterduel.exe` 的全路径取目录）→ Steam 注册表 + `libraryfolders.vdf` 各库 → 旧 D 盘常量。结果有缓存；采集器 `attach()` 每次重连都重取 |
| **离线基线** | `data/meta_decks_seed.json`（`fetch_meta_decks.py` 抓完顺手原子写） | 首跑 `ui/first_run.py:seed_from_package()` **离线**灌进空库（`replace_meta_decks(prune=False)`，不走 `--cn-only` —— 空库上它会把条目全建成 `source='custom'`）。联网抓取降级为后台更新 |
| **首启引导窗** | `ui/setup_wizard.py` + `core/ui_state.py:env_check()` | 首启（或换数据目录）自动弹：数据目录/游戏/采集器/卡组清单四行状态，可就地修（选目录、启采集器）。重开入口 = 设置页顶部「环境自检…」。标志 `setup/done` 与数据目录绑定（同 `first_run._dir_mark`） |

### A.7 已取消 / 不要碰

| 项 | 状态 |
|---|---|
| 硬币探针（`ChoiceFirstPlayer`） | ❌ **已回退**（2026-09-26 用户拍板，判据方向无法验证）。库列 `won_initiative` 保留 |
| D5 动作流日志 UI | ❌ 不做（读取层 `core/memread/duel_log.py` 保留） |
| S6 旧统计窗 | ❌ 已删，数据层 `stats_window_data()` 保留 |
| BUG-004（ruff 30+ 处） | ⏭️ 明确不做，逐一核过不影响行为 |
| 活动杯赛图标 | ⏳ 等真到那个活动再定（DC 图标已做） |
| `tools/fetch_mode_icons.py` | ⚠️ 遗留 CDN 下载器，跑 `--force` 会覆盖解包版图标，**别再运行** |

---

## 附录 B 文件地图（只列修 bug 会碰的）

```
core/storage.py              唯一真源入口（schema / upsert / 统计口径 / 会话）
core/ui_state.py             数据层（probe / snapshot / 各页数据 / _page_row）
core/duel_record.py          record 目录解码（座位 / 手坑 / 胜负位）
core/rank_state.py           段位状态机（升/掉段线）
core/my_deck.py              「当前卡组」状态文件
core/deck_guess.py           对手卡组反推（参照库打分）
core/deck_images.py          卡组图解析五环链
core/card_art.py             单卡卡绘缓存（整卡 / 纯卡绘两族）
core/card_names.py           卡名三语 + card_type 位掩码
core/paths.py                可写/只读目录分流（ASSET_DIR / DATA_DIR）
core/atomic.py               原子写唯一实现
core/modes.py                game_mode → 分类 / 名称 / 图标
core/hand_traps.py           手坑结论层
core/app_service.py          对局状态机（内存侧）
core/memread/                只读内存（il2cpp / record_capture / duel_log / end_message）
                             ⚠️ redline.py 是唯一豁免文件，禁词别写进业务代码

tools/duel_collector.py      ★ 常驻采集器（三 watcher 合一）
tools/import_duel_records.py record 入库（幂等）
tools/archive_matches.py     日志采集 / 统计 CLI
tools/audit_issues.py        数据体检（三类事故）
tools/mark_opponent.py       对手卡组标注 + 反推回测
tools/fetch_*.py             各类联网抓取（按月手动跑）
tools/check_game_update.py   ★ 游戏版本更新自检（--offline 不用开游戏）
build/                       打包脚本（setup / full / patch / lite / sync_to_pub）
                             ⚠️ 目录名是 build/ 不是 packaging/（会遮蔽 PyPI 包）

ui/app.py                    装配 + 探针循环 + 设置
ui/dashboard.py              ★ 看板（各页面类）
ui/floating.py               ★ 悬浮窗
ui/deck_view_page.py         卡组构成页
ui/opp_deck_manager.py       卡组名管理（覆盖表 CSV 编辑）
ui/selector.py               卡组选择器面板
ui/i18n.py                   三语键表 `_T`
ui/theme.py                  所有尺寸 / 颜色常量

data/masterduel.db           唯一真源（只读排查，别手改）
data/archive/duel_records/   每局一个 record_YYYYMMDD_HHMMSS/ 目录
data/archive/duel_collector.log
data/archive/collector_heartbeat.json
data/deck_name_cn.csv        卡组覆盖表（Excel 改，跑 --cn-only 生效）
data/card_names.json         卡名三语表
data/mdm_deck_cards.json     逐卡使用率参照库（月度快照）
```

### 协作方式

**两个 agent 交替接力（不是并行）** —— 一个跑到限额，用户切到另一个。
所以不存在写冲突，真正的风险是**冷启动**：切过来的 agent 没有对话上下文，
只能靠文件重建状态。规矩两条：① 开工读本文 §0；② **做完任何一件小事就更新
`docs/当前进度.md`**（跑到限额是硬中断，等不到从容写总结；**没更新接力棒 = 这次工作不算完成**）。

| 文件 | 性质 |
|---|---|
| `docs/Bug定位手册.md`（本文） | 修 bug 的方法论，稳定，**随软件单独发布** |
| `AGENTS.md` | 项目规则（铁律 / 口径），稳定 |
| `docs/当前进度.md` | 接力棒，**每次变动**，≤50 行 |
| `docs/历史归档/` | 开发期全貌 + 历史接力日志，**只作考古** |

单测一律用临时目录建库（`Storage(tempdir)`），已天然隔离；
临时实验先 `cp data/masterduel.db tmp/xxx.db`，**别动真库**。
