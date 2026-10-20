# Master Duel Stats

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

A **local-only** desktop tracker for *Yu-Gi-Oh! Master Duel*: a resident collector
archives every duel into a local SQLite database the moment you finish it, and a
topmost floating window plus a full dashboard visualize your win rates, opponent
decks, rank progress and the metagame — with UI in **English / 中文 / 日本語**.

| Floating window | Dashboard · Matches |
|---|---|
| ![floating](assets/floating.png) | ![dashboard](assets/dashboard.png) |

| Dashboard · Analysis matrix |
|---|
| ![analysis](assets/analysis.png) |

> This is an unofficial fan tool, not affiliated with or endorsed by KONAMI.
> All stats stay on your own computer — **nothing is collected or uploaded**.

## Features

- **Floating window** — always-on-top mini window with session overview,
  going-first/second stats and the latest duels. Click an opponent deck cell to
  tag it. Trilingual UI, switched in-place from the dashboard settings.
- **Dashboard** — monthly heatmap, overview, per-duel log, my-deck × opponent
  win-rate matrix, tier list, banlist, deck composition reference, gift box
  viewer, and CSV/JSON export filtered by month and mode.
- **Auto-collection** — a resident collector decodes and archives each duel
  automatically while you play. Zero interaction.
- **Opponent deck tagging & guessing** — manual tagging plus a card-table
  similarity based guesser.
- **Rank & RATE points** — after each duel, the real rank and RATE points are
  read from the game memory **read-only**.
- **Trilingual card / deck names** — a built-in table covers ~14,000 cards in
  English / Chinese / Japanese.
- **Deck art management** — in the deck-name manager you can set art for any deck:
  search a card name to pick its artwork, or pull it from the site tier list.
  Custom decks (not on the tier list) get art and search too.
- **Reference refresh from the UI** — dashboard → Settings → Data tools →
  "Refresh reference"; no command line needed.
- **Multi-select mode filter** — on both the Matches page and the Analysis page,
  tick the modes you care about (Ranked / Rating / DC Cup / Events); they are
  filtered independently, and the menu updates the figures as you tick.
- **Hand-trap detection**: automatically detects the 12 hand traps (alt-arts included)
  used by both sides in the first two turns — two card-art columns on the Matches page
  (up to 5 per cell, `+N` beyond), plus a "Hand Traps" view under Analysis showing
  going-first / going-second splits per deck.

**Upgrading a portable install:** grab `patch.zip` (under 1 MB), quit the app
and the collector completely (tray icon -> exit), then unzip `master-duel-stats`
from the archive over your existing folder. Your duel data lives in `data/` and is
never touched by the patch. Installed copies should just run the new `Setup` instead.

**Where your duel data lives —** the installer keeps it **next to the program** (`<install>\data`) so you can move the whole folder to another PC. If you install somewhere read-only like `Program Files`, it automatically falls back to `%LOCALAPPDATA%\MasterDuelStats`. Either way you can switch to any folder in **dashboard → Settings → Interface → Game Data** (restart the UI to apply).

## Install & Run

### Downloads ([Releases](../../releases/latest))

| Asset | Size | Requirements |
|---|---|---|
| `masterduel-stats-Setup-1.1.0.exe` | ~29 MB | **One-click installer (recommended)** — install and run. Game data lives next to the program by default; under `Program Files` it moves to `%LOCALAPPDATA%\MasterDuelStats` |
| `masterduel-stats-v1.1.0-win64-full.zip` | ~39 MB | **None** — Python & all deps bundled; unzip anywhere and run `MDStats.exe` |
| `masterduel-stats-v1.1.0-patch-win64.zip` | **~0.7 MB** | **Upgrade for existing portable installs** — program code only; unzip over the old folder. Your duel data is not touched |
| `masterduel-stats-v1.1.0-win64-lite.zip` | ~6 MB | Python 3.13 installed; `MDStats.exe` installs the dependencies on first run |
| Source code (zip / tar.gz) | auto | for developers — attached automatically by GitHub for each tag |

**The easy way:** install [Python 3.13](https://www.python.org/downloads/)
(check *Add to PATH*, or make sure the `py` launcher works), unzip the
download, and **double-click `MDStats.exe`**. It finds Python on its own,
installs the dependencies on first run (with visible progress), starts the
resident collector and the floating window, and — if anything goes wrong —
shows you a dialog and writes `data/launcher.log` instead of a console
window flashing and vanishing.

**To run the pieces yourself** (from the extracted folder):
```
python tools\duel_collector.py     # resident collector
python tools\duel_ui.py            # floating window + dashboard
```
`start.bat` does the same thing if you prefer a script.

**Stopping:** close the floating window (the X button) — that exits the whole
tool. The dashboard opens from the floating window, or from its tray icon
(right-click → exit). If you have several Pythons installed, put the full
path to the right `python.exe` into a `python_path.txt` file next to
`MDStats.exe` and it will prefer that one.

### First run

* The SQLite database is created automatically on first start
  (`data/masterduel.db`); all data stays in `data/`.
* **Deck-guessing / composition reference** needs the reference library fetched
  once (online, ~2-3 min):
  ```
  python tools\fetch_mdm_decks.py --all
  ```
  (Same thing from the UI: dashboard → Settings → Data tools → "Refresh reference")
  Refresh monthly.
* **Banlist / deck names / deck images**: one-click tools live in the dashboard
  settings page; refresh monthly.
* (Optional) To back-fill duels from the Untapped public API, put your account
  ids into `data/untapped_ids.json` (see the top of
  `tools/fetch_untapped_api.py`).

## Data & Privacy

* Stats come from **game log parsing + read-only memory capture** (two
  complementary sources; see DESIGN.md);
* Memory capture is **read-only**: no writes, no injection, no modification of
  any game file — `core/memread/redline.py` defines the forbidden list and
  `tests/test_memread_guard.py` enforces it with 25 unit tests;
* The tool never talks to the network except for a few explicitly clicked
  fetches (official card DB, public meta lists); your duel database, records
  and personal state never leave `data/`.

## Disclaimer

* Unofficial fan tool, not affiliated with KONAMI. Game assets (card art,
  icons) belong to KONAMI and their respective owners;
* Third-party assets & font licensing: see [NOTICE](NOTICE);
* Memory reads are for personal review only — **use at your own risk**;
* Code is licensed under [GPL-3.0](LICENSE); third-party assets are excluded
  (see NOTICE).

## Requirements

* Windows 10/11 (memory capture uses the Windows API)
* Python 3.13
* See [requirements.txt](requirements.txt) (PySide6-Essentials, pypinyin)
