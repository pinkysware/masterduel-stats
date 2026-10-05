# Master Duel Stats（Master Duel 战绩统计）

[English](README.md) | **简体中文** | [日本語](README.ja.md)

一个**纯本地**的《游戏王 Master Duel》桌面战绩统计工具：常驻采集器在你打完
每一局的瞬间自动归档进本地 SQLite 数据库；一个置顶悬浮窗加一块完整看板，
把胜负、对手卡组、段位起伏和环境的分析直观地摆在你面前。界面支持
**英文 / 中文 / 日文**（看板设置里实时切换）。

| 悬浮窗 | 看板 · 战绩页 |
|---|---|
| ![floating](assets/floating.png) | ![dashboard](assets/dashboard.png) |

| 看板 · 对局分析矩阵 |
|---|
| ![analysis](assets/analysis.png) |

> 本工具为非官方粉丝作品，与 KONAMI、Untapped 均无关联。
> 所有战绩数据只保存在你自己的电脑上，**不收集、不上传任何数据**。

## 功能

- **悬浮窗**：置顶小窗实时显示本轮总览 / 先后攻统计 / 最近对局；点对手
  卡组格直接标注；三语界面在设置里原地切换。
- **看板**：月历热力图、战绩总览、逐局记录、我方 × 对手胜率矩阵、
  Tier 表、禁限表、卡组构成参照、礼盒查询、按月按模式导出 CSV / JSON。
- **自动采集**：常驻采集器在你打游戏的同时自动解码并归档每一局，零操作。
- **对手卡组标注与反推**：手动标注之外，内置基于卡表相似度的反推建议。
- **段位与巅峰积分**：对局结束后从游戏内存**只读**读取真实段位与 RATE 积分。
- **三语卡名 / 卡组名**：内置约 1.4 万张卡的中 / 英 / 日卡名表。

## 安装与运行

### 下载（[Releases](../../releases/latest)）

| 附件 | 体积 | 需要什么 |
|---|---|---|
| `masterduel-stats-v1.0.0-win64-full.zip` | ~193 MB | **什么都不用装** —— 自带 Python 与全部依赖，解压后双击 `start.bat` |
| `masterduel-stats-v1.0.0-win64-lite.zip` | ~4 MB | 需先装 Python 3.13；首次运行 `start.bat` 会自动安装依赖 |
| Source code (zip / tar.gz) | 自动 | 源码包，GitHub 对每个 tag 自动附带 |

1. 安装 [Python 3.13](https://www.python.org/downloads/)（勾选 *Add to PATH*，
   或确保 `py` 启动器可用）；
2. 克隆本仓库，在仓库根目录执行：
   ```
   pip install -r requirements.txt
   ```
3. 双击 `start.bat` —— 自动拉起常驻采集器（无窗口）和悬浮窗，
   看板随即打开。也可以分别手动运行：
   ```
   python tools\duel_collector.py     # 常驻采集器
   python tools\duel_ui.py            # 悬浮窗 + 看板
   ```
   `stop.bat` 停止两者。若装了多个 Python，把正确 `python.exe` 的完整路径
   写进仓库根目录的 `python_path.txt`。

### 首次运行

* 数据库**首次启动自动建表**（`data/masterduel.db`），所有数据都在 `data/`；
* **反推卡组 / 参照库**需联网抓一次参照数据（约 2~3 分钟，建议每月刷新）：
  ```
  python tools\fetch_mdm_decks.py --all
  ```
* **禁限表 / 卡组名称 / 卡组图**：看板设置页有一键工具，按月刷新即可；
* （可选）用 Untapped 公开 API 补录历史对局：把你的账号标识写进
  `data/untapped_ids.json`（格式见 `tools/fetch_untapped_api.py` 顶部说明）。

## 数据与隐私

* 战绩数据来源 = **游戏日志解析 + 只读内存采集**，两条腿互补（见 DESIGN.md）；
* 内存采集**只读**：不写入、不注入、不修改任何游戏文件 ——
  `core/memread/redline.py` 定义禁区清单，`tests/test_memread_guard.py`
  用 25 项单测强制执行；
* 除少数显式点击的联网操作（官网查卡、公开榜单抓取）外，工具不联网；
  战绩库、对局记录、个人状态全部留在本机 `data/`。

## 声明

* 非官方粉丝工具，与 KONAMI 无关；游戏内素材（卡绘、图标）版权归
  KONAMI 及各自权利方；
* 第三方素材与字体许可见 [NOTICE](NOTICE)；
* 段位 / 积分的内存读取仅供个人复盘，**使用风险自负**；
* 代码以 [GPL-3.0](LICENSE) 授权，第三方素材不适用该授权（见 NOTICE）。

## 环境要求

* Windows 10/11（内存采集依赖 Windows API）
* Python 3.13
* 依赖见 [requirements.txt](requirements.txt)（PySide6-Essentials、pypinyin）
