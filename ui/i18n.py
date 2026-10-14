# -*- coding: utf-8 -*-
"""界面骨架三语表（2026-10-03 用户：本地化落地，骨架层）。

## 用法

界面里的**骨架文案**（导航 / 页面标题 / 区块标题 / 按钮 / 表头 / 空态提示）
用 `tr('战绩')` 包一层：

* ``ui/lang = cn``（默认）→ **原样返回** —— 现有测试与默认观感零改动；
* ``en`` / ``ja`` → 查 :data:`_T`，**没收录的键优雅回落中文**
  （宁可漏翻，不可报错/留白）；
* 切语言按既有口径**重开窗口生效**（选择器那条老规矩，设置页文案有提示）。

## 键的约定

**中文原文就是键** —— 新代码里写 `tr('对局记录')` 即可，不用另造 key；
字典缺一条只是那一处不翻译，不会崩。

## 范围（骨架层，用户拍板）

导航 / 页面与区块标题 / 按钮 / 表头 / 设置项 / 空态提示。**不在范围**：
对局数据文字（卡名 / 卡组名 / 对手名 —— 本来就跟 `ui/lang` 走）与
深层 tooltip（后续按用户反馈补）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings

_LANG = None

#: QSettings 工厂（测试可注入临时 INI —— 与 ``FloatingWindow._settings`` 同款手法；
#: 不注入 = 真实 ``QSettings('masterduel', 'M7')``）
_SETTINGS_FACTORY = None


def _settings() -> QSettings:
    if _SETTINGS_FACTORY is not None:
        return _SETTINGS_FACTORY()
    return QSettings('masterduel', 'M7')

#: 语义常量（收藏用，别用魔法串）
CN, EN, JA = 'cn', 'en', 'jp'   # ⚠️ JA 跟全项目的 'jp'（theme.LANGS / 设置值），别写 'ja'

# 键 = 中文原文；值 = (英文, 日文)
_T = {
    # ---- 左栏导航 / 分组 -------------------------------------------------
    '战绩': ('Matches', '対戦'),
    '对局分析': ('Analysis', '対戦分析'),
    '卡组': ('Decks', 'デッキ'),
    '悬浮窗': ('Overlay', 'オーバーレイ'),
    '导出': ('Export', 'エクスポート'),
    '查询': ('Lookup', '検索'),
    '禁限表': ('Banlist', 'リミットレギュレーション'),   # ja=游戏内术语（审查 §五.6）
    '查卡': ('Card Search', 'カード検索'),
    '礼盒': ('Gift Box', 'プレゼントボックス'),
    '设置': ('Settings', '設定'),

    # ---- 战绩页 ----------------------------------------------------------
    '按月': ('By Month', '月別'),
    '全部历史': ('All Time', '全期間'),
    '点月卡看该月战绩': ('Click a month card to view that month',
                    '月カードをクリックするとその月を表示'),
    '块越大 = 那天打得越多': ('Bigger block = more games that day',
                          'ブロックが大きいほどその日は多く対戦'),
    '越绿 = 那天胜率越高': ('Greener = higher win rate that day',
                          '緑ほど勝率が高い'),
    '场数 少': ('Fewer games', '少ない'),
    '多': ('More games', '多い'),
    '胜率 低': ('Lower win rate', '低い'),
    '高': ('Higher win rate', '高い'),
    '战绩总览': ('Overview', 'オーバービュー'),
    '本月': ('This month', '今月'),
    '本月还没打过': ('No duels this month yet', '今月はまだ対戦なし'),
    '最高段位': ('Best Rank', '最高ランク'),
    '对局数': ('Duels', '対戦数'),
    '胜率': ('Win rate', '勝率'),
    '先攻胜率': ('Going-First Win Rate', '先攻勝率'),
    '后攻胜率': ('Going-Second Win Rate', '後攻勝率'),
    '对局记录': ('Duel Log', '対戦記録'),
    '按轮次分组（同一副卡组跨轮不合并）': ('Grouped by session (same deck '
                                    'across sessions is not merged)',
                                    'セッション別にグループ化'),
    '点对手卡组格即可标注': ('Click an opponent deck cell to tag it',
                        '相手デッキのセルをクリックでタグ付け'),
    '本月还没有对局': ('No duels this month', '今月の対戦はまだありません'),
    '库里最后一局：': ('Last match in DB: ', 'DBの最終対戦: '),
    '全部模式': ('All Game Modes', '全モード'),   # Untapped 分类口径
    #: 多选：一个模式都没勾（用户 2026-10-15「全部再点一次 = 全取消」）
    '未选择模式': ('No mode selected', 'モード未選択'),
    #: 多选筛选：勾了 2~3 项时的按钮文字（`'a 等 2 项'`）
    '%s 等 %d 项': ('%s +%d more', '%s 他 %d 件'),
    '排名决斗': ('Ranked Duel', 'ランクデュエル'),
    '巅峰决斗': ('Rating Duel', 'レーティングデュエル'),
    'DC杯': ('Duelist Cup', 'デュエリストカップ'),
    '活动决斗': ('Events', 'イベント'),
    '胜负': ('Result', '勝敗'),
    '先/后': ('1st/2nd', '先/後'),
    '对手': ('Opponent', '相手'),
    '回合': ('Turns', 'ターン'),
    '段位': ('Rank', 'ランク'),
    '对手卡组': ('Opponent Deck', '相手デッキ'),
    '日期': ('Date', '日付'),
    '时间': ('Time', '時刻'),
    '模式': ('Mode', 'モード'),
    '先手': ('First', '先攻'),
    '后手': ('Second', '後攻'),
    '先后攻': ('First/Second', '先攻/後攻'),
    '未知卡组': ('Unknown Deck', '不明デッキ'),

    # ---- 对局分析页 ------------------------------------------------------
    '综合胜率': ('Overall Win Rate', '全体勝率'),
    '我方卡组': ('My Deck', '自デッキ'),

    # ---- 卡组页 / 卡组构成 ----------------------------------------------
    '卡组名管理': ('Deck Names', 'デッキ名管理'),
    'Tier 表': ('Tier List', 'ティア表'),
    '卡组构成': ('Deck Composition', 'デッキ構築'),
    '获取新卡组名': ('Fetch New Deck Names', '新デッキ名を取得'),
    '抓取榜单': ('Fetch Tier List', 'ランキングを取得'),
    '回 Tier 表': ('← Back to Tier List', '← ティア表へ'),
    '暂无该卡组构成': ('No composition data for this deck yet',
                    'このデッキの構築データはまだありません'),
    '参照库快照': ('Reference snapshot', '参照スナップショット'),
    '份样本': ('samples', 'サンプル'),
    '平均主卡': ('Avg. main deck', '平均メイン'),
    '张': ('cards', '枚'),

    # ---- 禁限表页 --------------------------------------------------------
    '抓取禁限表': ('Fetch Banlist', '制限リストを取得'),
    '禁止': ('Forbidden', '禁止'),
    '限制': ('Limited', '制限'),
    '准限制': ('Semi-Limited', '準制限'),
    '最近变更': ('Recent Changes', '変更履歴'),
    '解除限制': ('Unlimited', '制限解除'),
    '运行中…': ('Running…', '実行中…'),
    '完成': ('Done', '完了'),
    '失败': ('Lose', '失敗'),
    '抓取失败': ('Failed', '失敗'),

    # ---- 悬浮窗开关页 ----------------------------------------------------
    '显示悬浮窗': ('Show Overlay', 'オーバーレイを表示'),
    '隐藏悬浮窗': ('Hide Overlay', 'オーバーレイを非表示'),
    '游戏里打完一局自动弹结果的那个小窗。被关掉（×）后在这里点一下就能唤回来。':
        ('The small result window from the game. If closed (×), click here '
         'to bring it back.',
         'ゲーム内の結果小ウィンドウ。×で閉じた場合はここで再表示'),

    # ---- 查卡页 ----------------------------------------------------------
    '官网查卡（Konami 数据库）': ('Official DB (Konami)', '公式DB（Konami）'),
    '百鸽查卡（ygocdb）': ('ygocdb', 'ygocdb'),
    '点链接用默认浏览器打开；上方搜索框是本地三语候选'
    '（离线），点候选拿卡 id 跳 ygocdb。':
        ('Links open in your browser. The search box above gives local '
         'trilingual candidates (offline).',
         'リンクはブラウザで開きます。上の検索はローカル三言語候補'),

    # ---- 导出页 ----------------------------------------------------------
    '导出 CSV': ('Export CSV', 'CSVを出力'),
    '导出 JSON': ('Export JSON', 'JSONを出力'),
    '月份': ('Month', '月'),
    '点「导出」写全部列。': ('"Export" writes all columns.', '出力で全列を書き出します'),
    '共 %d 局（预览前 %d 行）': ('%d duels total (previewing first %d)',
                             '計 %d 対戦（プレビュー %d）'),
    '…还有 %d 局（导出时才全部写出）': ('…and %d more (written on export)',
                                  '…残り %d 対戦（出力時に全件）'),
    '场': ('duels', '対戦'),
    '（暂无该卡组构成）': ('(no composition data)', '（構築データなし）'),
    '共 %d 张受限': ('%d limited cards total', '制限カード 計 %d 枚'),
    '全部': ('All', '全て'),
    '全部月份': ('All months', '全ての月'),
    # 卡组名管理表头（2026-10-03 接线 opp_deck_manager）
    '站内英文名': ('Site Name (EN)', 'サイト内英名'),
    '中文名': ('Chinese Name', '中国語名'),
    '别名': ('Aliases', '別名'),
    '显示英文名': ('Display Name (EN)', '表示英名'),
    '隐藏': ('Hidden', '隠す'),
    '删': ('Del', '削除'),

    # ---- 设置页 ----------------------------------------------------------
    '语言': ('Language', '言語'),
    '界面': ('Interface', 'インターフェース'),
    '数据': ('Data', 'データ'),
    '自动化': ('Automation', '自動化'),
    '启动': ('Startup', '起動'),

    # ---- 退出 / 菜单（悬浮窗 + 托盘）------------------------------------
    '设置…': ('Settings…', '設定…'),
    '看板…': ('Dashboard…', 'ダッシュボード…'),
    '退出': ('Quit', '終了'),

    # ---- 卡图设置（2026-10-08 问题 1：自定义卡组没卡图）----
    '设置卡图': ('Set Deck Art', 'デッキ画像を設定'),
    '卡组：%s': ('Deck: %s', 'デッキ：%s'),
    '选择本地图片…': ('Choose local image…', 'ローカル画像を選択…'),
    '选择图片': ('Choose Image', '画像を選択'),
    '从站内卡组取图…': ('Fetch from site deck…', 'サイトのデッキから取得…'),
    '取图': ('Fetch', '取得'),
    '还原为默认图': ('Reset to default', '既定に戻す'),
    '请填站内英文名': ('Enter the site (EN) deck name',
                    'サイト内の英名を入力してください'),
    '站内英文名（如 Elfnote / Power Patron）':
        ('Site deck name, e.g. Elfnote / Power Patron',
         'サイト内デッキ名（例：Elfnote / Power Patron）'),
    '按站内卡组名取那张 200×200 的卡图（适合自组卡组 ≈ 某站内卡组）':
        ('Grab that deck\'s 200×200 art from the site (good when your custom deck ≈ a site deck)',
         'サイトの 200×200 デッキ画像を取得（自組デッキがサイトのデッキに近い時）'),
    '点一下给这个卡组设置卡图（本地图片 / 从站内取图 / 还原）':
        ('Click to set deck art (local image / fetch from site / reset)',
         'クリックでデッキ画像を設定（ローカル / サイト / 既定に戻す）'),
    '设置卡图失败（图读不出来？）': ('Failed to set deck art (unreadable image?)',
                                'デッキ画像の設定に失敗（画像が読めません？）'),
    '设置卡图失败：%s': ('Failed to set deck art: %s',
                      'デッキ画像の設定に失敗：%s'),
    '站内没有「%s」这张图（自组混轴名会 404；试试最接近的站内卡组名）':
        ('The site has no art for "%s" (custom/engine names 404; try the nearest site deck)',
         'サイトに「%s」の画像がありません（混成名は404。近いサイト名を）'),
    '已设置「%s」的卡图：%s': ('Deck art set for "%s": %s',
                           '「%s」のデッキ画像を設定：%s'),
    '已还原「%s」的卡图%s': ('Deck art reset for "%s"%s',
                         '「%s」のデッキ画像を既定に戻しました%s'),
    # ---- 管理页分组 / 自定义行转正（2026-10-12）----
    '%d / %d 条（其中 %d 条待保存进覆盖表）':
        ('%d / %d entries (%d pending save into the cover table)',
         '%d / %d 件（うち %d 件は保存待ち）'),
    '—— 自定义卡组 ——': ('—— Custom decks ——',
                       '—— カスタムデッキ ——'),
    '日文名': ('Japanese name', '日本語名'),
    '日文名（留空 = 未填）': ('Japanese name (blank = unset)',
                         '日本語名（空欄 = 未入力）'),
    '已移除「%s」（还没进覆盖表，不用保存）':
        ('Removed "%s" (not in the cover table; no save needed)',
         '「%s」を消しました（上書き表に無し。保存不要）'),
    '我加的自定义卡组（还没进覆盖表 —— 可直接编辑，点「保存」并进去）':
        ('My custom decks (not in the cover table yet — edit here and press Save)',
         '自分で追加したデッキ（上書き表に未登録。編集して「保存」で追加）'),
    '没有新卡组 —— 顺手补缺失的卡图…':
        ('No new decks — backfilling missing deck art…',
         '新しいデッキは無し — 無い画像を取得中…'),
    # ---- 自定义卡组只读行（2026-10-08 问题 1）----
    '%d / %d 条（其中 %d 条不在覆盖表）':
        ('%d / %d entries (%d not in the cover table)',
         '%d / %d 件（うち %d 件は上書き表に無し）'),
    '我加的自定义卡组（不在覆盖表里；只有卡图能改）':
        ('My custom decks (not in the cover table; only art is editable)',
         '自分で追加したデッキ（上書き表に無し。画像のみ編集可）'),
    '我加的自定义卡组（不在覆盖表里；名字来自标注）':
        ('My custom deck (not in the cover table; name comes from labels)',
         '自分で追加したデッキ（上書き表に無し。名前はラベル由来）'),
    '点一下给这个卡组设置卡图': ("Click to set this deck's art",
                            'クリックでこのデッキの画像を設定'),
    '这个卡组不在覆盖表里（只有卡图能改）':
        ('This deck is not in the cover table (only art is editable)',
         'このデッキは上書き表にありません（画像のみ編集可）'),
    # ---- Q17 补缺图（2026-10-08）----
    '已贴进库，正在补缺失的卡图…':
        ('Pushed to DB; fetching missing deck art…',
         'DBに反映済み。不足するデッキ画像を取得中…'),
    # ---- 设置卡图：卡名搜索（2026-10-08）----
    '用某张卡的卡图（推荐）：': ('Use a card\'s art (recommended):',
                                       'カードの画像を使う（推奨）：'),
    '搜卡名，如 灰流丽 / 闪刀姬－篝': ('Search a card name',
                                    'カード名を検索'),
    '用这张卡的卡图': ('Use this card\'s art', 'このカードの画像を使う'),
    '先搜一张卡，再从列表里点一张': ('Search a card, then pick one from the list',
                                     'カードを検索して一つ選んでください'),
    '取不到那张卡的卡绘（卡表里没有 / 网络不通）': ('Could not fetch that card\'s art (not in card table / offline)',
                                       'そのカードの画像を取得できません'),
    '选一个卡组，取它那张 200×200 的卡图（可搜中文名 / 别名 / 日文名）':
        ('Pick a deck and grab its 200x200 art (searchable by CN/alias/JP)',
         'デッキを選んで 200x200 画像を取得（中・別名・日本語で検索可）'),
    '请选一个卡组': ('Please pick a deck', 'デッキを選んでください'),
    # ---- 设置卡图第二版（2026-10-08）----
    '获取卡图': ("Fetch Art", "画像を取得"),
    '取选中那张卡的卡图（只看，不落盘）': ("Fetch the selected card's art (preview only)", "選択したカードの画像を取得（プレビューのみ）"),
    '把上面这张图设为该卡组的卡图': ("Set the image above as this deck's art", "上の画像をこのデッキの画像に設定"),
    '正在获取卡图…': ("Fetching art…", "画像を取得中…"),
    '先在下面选一张卡（点一行，再点它右边的「确认」）': ("Pick a card below (click a row, then its 确认 button)", "下のカードを選んでください（行をクリック→右の「確認」）"),
    '卡图已就绪 —— 点「保存」生效': ("Art ready — click 保存 to apply", "画像の準備完了 —「保存」で適用"),
    '已选「%s」—— 点「获取卡图」看效果，「保存」生效': ("Selected \"%s\" — click 获取卡图 to preview, 保存 to apply", "「%s」を選択 —「获取卡图」で確認、「保存」で適用"),
    '确认': ("OK", "決定"),
    # ---- 设置卡图：状态行不回显路径（2026-10-08）----
    '已设置「%s」的卡图': ("Deck art set for \"%s\"",
                             '「%s」のデッキ画像を設定しました'),
    # ---- 本地化第二批（2026-10-04 实时切换 + 全页面接线）----
    " %d平": (" %d draw", " %d引き分け"),
    " · 最近生效 %s": (" · last effective %s", " ・最近の発効 %s"),
    "%d / %d 条": ("%d / %d entries", "%d / %d 件"),
    "%d 张卡没查到类型，未显示（跑一次 tools/build_card_names.py --update 试试）": ("%d cards with unknown type are hidden (try running tools/build_card_names.py --update)", "タイプ不明のカード %d 枚は未表示（tools/build_card_names.py --update の実行を試してください）"),
    "%d 条": ("%d items", "%d 件"),
    "%d场 %d-%d": ("%d duels %d-%d", "%d戦 %d-%d"),
    "%s · %d 场": ("%s · %d duels", "%s · %d 対戦"),
    "%s · %d 场（%d 胜 %d 负）": ("%s · %d duels (%d wins %d losses)", "%s · %d 対戦（%d 勝 %d 敗）"),
    "%s 回合": ("%s turns", "%s ターン"),
    "%s（%d 局）": ("%s (%d duels)", "%s（%d 対戦）"),
    "%s（升段）": ("%s (ranked up)", "%s（昇格）"),
    "%s（掉段）": ("%s (ranked down)", "%s（降格）"),
    "%s：%s": ("%s: %s", "%s：%s"),
    "%s：运行中…": ("%s: running…", "%s：実行中…"),
    "CSV：Excel 直接双击打开（utf-8 BOM + CRLF）": ("CSV: opens directly in Excel (utf-8 BOM + CRLF)", "CSV：Excel でダブルクリックでそのまま開けます（utf-8 BOM + CRLF）"),
    "JSON：带列名表与导出时间，中文不转义": ("JSON: with column headers and export time; Chinese kept unescaped", "JSON：列名と出力時刻付き、中国語はエスケープしません"),
    "MD 战绩": ("MD Stats", "MD 戦績"),
    "MD 环境站：Tier 榜 / 卡组构成 / 使用率（本项目参照库的数据源）": ("MD meta site: tier list / deck lists / usage (source of this project's reference decks)", "MD 環境サイト：ティア表 / デッキ構築 / 使用率（本プロジェクト参照データのソース）"),
    "Master Duel 战绩": ("Master Duel Stats", "Master Duel 戦績"),
    "Master Duel 战绩悬浮窗": ("Master Duel Overlay", "Master Duel オーバーレイ"),
    "Master Duel 战绩统计": ("Master Duel Statistics", "Master Duel 戦績統計"),
    "Power ≥ 12 · 赛事上位卡组": ("Power ≥ 12 · Top tournament decks", "Power ≥ 12 · 大会上位デッキ"),
    "「%s」已经在表里了（没重复加）": ("\"%s\" is already in the table (not added again)", "「%s」は既に表にあります（重複追加していません）"),
    "一个都没勾": ("Nothing checked", "何もチェックされていません"),
    "上游卡表比对与更新（三语卡名）": ("Compare with and update the upstream card table (trilingual names)", "上流カードDBの比較と更新（三言語カード名）"),
    "参照库刷新": ("Refresh Reference Decks", "参照データを更新"),
    "刷新（几分钟）": ("Refresh (a few min)", "更新（数分）"),
    "卡组构成的参照库（每张卡的使用率，每月一次）": ("Reference decks for compositions (per-card usage, monthly)", "構築の参照データ（カード別使用率・毎月）"),
    "下载更新": ("Download Updates", "更新をダウンロード"),
    "下面三项：禁限表 / 查卡 / 礼盒": ("Three items below: Banlist / Card Search / Gift Box", "下の3項目：リミットレギュレーション / カード検索 / プレゼントボックス"),
    "不再提示 · 之后直接按此执行": ("Don't Ask Again · Apply This Choice Directly", "今後表示しない ・ 以降はこの選択で実行"),
    "中文名（可留空）": ("Chinese name (optional)", "中国語名（空欄可）"),
    "中文圈最全的卡查，三语名/卡图/查卡组构筑": ("The fullest card search for CN players — trilingual names / art / deck lists", "中国語圏最高のカード検索。三言語名 / カード画像 / デッキ構築"),
    "从 masterduelmeta 抓禁限变更史（每月一次）": ("Fetch banlist history from masterduelmeta (once a month)", "masterduelmeta から制限変更履歴を取得（月1回）"),
    "从覆盖表里删掉「%s」": ("Remove \"%s\" from the override table", "上書き表から「%s」を削除"),
    "保存": ("Save", "保存"),
    "修正失败": ("Failed to Fix Result", "結果の修正に失敗しました"),
    "先": ("1st", "先"),
    "先后手": ("First/Second", "先攻/後攻"),
    "先手率": ("Going-First Rate", "先攻率"),
    "先攻": ("Going First", "先攻"),
    "先攻（硬币正面）": ("Going first (coin heads)", "先攻（コインの表）"),
    "关闭": ("Close", "閉じる"),
    "关闭悬浮窗": ("Close Floating Window", "フローティングウィンドウを閉じる"),
    "写盘失败：%s": ("Failed to write: %s", "書き込み失敗: %s"),
    "删除这一行（保存后才真正从覆盖表移除）": ("Remove this row (only written to the table on Save)", "この行を削除（「保存」で実際に上書き表から消えます）"),
    "别名（多个用 / 分隔）": ("Aliases (separate multiple with /)", "別名（複数は / で区切る）"),
    "刷新": ("Refresh", "更新"),
    "加入覆盖表": ("Add to Override Table", "対応表に追加"),
    "勾上 = 隐藏这个条目（选择器 / 标注里见不到，但库里的记录还在）": ("Check to hide this entry (gone from the picker / tagging; DB records remain)", "チェックでこの項目を非表示（セレクター/タグ付けに出なくなります。DBの記録は残ります）"),
    "升段": ("Promoted", "昇格"),
    "卡名 / 卡组名的显示语言（匹配一律走卡 id）": ("Display language for card / deck names (matching always uses card ids)", "カード名 / デッキ名の表示言語（照合は常にカード id）"),
    "卡图（新卡组默认用悠悠；想换就放一张进 data/deck_images_custom/）": ("Card art (new decks default to Kuriboh; drop a replacement into data/deck_images_custom/)", "カード画像（新デッキは既定でクリボー。差し替えは data/deck_images_custom/ に画像を置く）"),
    "卡组名 / 中文名 / 别名 / 日文名（留空 = 全部）": ("Deck name / Chinese name / alias / Japanese name (empty = all)", "デッキ名 / 中国語名 / 別名 / 日本語名（空欄 = 全て）"),
    "卡组清单": ("Deck List", "デッキ一覧"),
    "卡表 %s 张（%s 更新）": ("Card table: %s cards (updated %s)", "カード表 %s 枚（%s 更新）"),
    "卡表更新": ("Card DB Update", "カードDB更新"),
    "取消": ("Cancel", "キャンセル"),
    "只看常带 ≥ 0%": ("Min. share ≥ 0%", "採用率 ≥ 0% のみ表示"),
    "只看常带 ≥ 10%": ("Min. share ≥ 10%", "採用率 ≥ 10% のみ表示"),
    "只看常带 ≥ 20%": ("Min. share ≥ 20%", "採用率 ≥ 20% のみ表示"),
    "只看常带 ≥ 50%": ("Min. share ≥ 50%", "採用率 ≥ 50% のみ表示"),
    "右侧即控件：改完即存、点「运行」直接跑（输出在下方日志区）": ("Controls on the right: changes save instantly; Run starts the tool (output in the log area below)", "右側が操作部：変更は即保存、「実行」で直接実行（出力は下のログ欄）"),
    "合计 %d 场（%d 胜）": ("%d duels total (%d wins)", "計 %d 対戦（%d 勝）"),
    "合计 %d 条": ("Total %d items", "合計 %d 件"),
    "后": ("2nd", "後"),
    "后攻": ("Going Second", "後攻"),
    "后攻（硬币背面）": ("Going second (coin tails)", "後攻（コインの裏）"),
    "填好点右边「确定」或按回车": ("Type a name, then click OK or press Enter", "入力後、右の「決定」またはEnterで確定"),
    "失败（退出码 %d）": ("Failed (exit code %d)", "失敗（終了コード %d）"),
    "完成（%s）": ("Done (%s)", "完了（%s）"),
    "官方卡片数据库，效果文/ rulings 的最终口径": ("Official card database — the final word on card text / rulings", "公式カードデータベース。効果文 / ruling の最終窓口"),
    "对局 %s 场（最近 %s）": ("Matches: %s (last %s)", "対戦 %s（最新 %s）"),
    "对手：%s": ("Opponent: %s", "相手：%s"),
    "对手：%s\n我方：%s\n\n胜率：%s\n对局：%d\n战绩：%d胜 %d负%s": ("Opponent: %s\nMy deck: %s\n\nWin rate: %s\nDuels: %d\nRecord: %dW %dL%s", "相手：%s\n自デッキ：%s\n\n勝率：%s\n対戦：%d\n戦績：%d勝 %d敗%s"),
    "导出对局记录": ("Export Match Records", "対戦記録をエクスポート"),
    "导出的是**对局记录**（一行一局）。日期与时间已分列；": ("Exports the **duel records** (one row per duel). Date and time are in separate columns; ", "出力するのは**対戦記録**（1 行 = 1 対戦）です。日付と時刻は分列済み；"),
    "小计": ("Subtotal", "小計"),
    "已保存。": ("Saved.", "保存しました。"),
    "已保存。重启界面后全部生效（设置 → 重启界面）。": ("Saved. Takes effect after restarting the UI (Settings → Restart UI).", "保存しました。インターフェース再起動後に有効になります（設定 → 再起動）。"),
    "已保存到 CSV，但贴进库失败（退出码 %d）—— 可手动跑 %s %s": ("Saved to CSV, but pushing into the DB failed (exit code %d) - run manually: %s %s", "CSVには保存しましたが、DB反映に失敗しました（終了コード %d）- 手動実行: %s %s"),
    "已保存并贴进库（选择器 / 看板即时生效）": ("Saved and pushed into the DB (picker / dashboard update immediately)", "保存してDBに反映しました（セレクター/ダッシュボードに即時反映）"),
    "已关闭开机自启": ("Autostart disabled", "自動起動を無効にしました"),
    "已写入 %s，正在贴进库…": ("Written to %s, pushing into the DB…", "%s に書き込みました。DBに反映中…"),
    "已写入覆盖表 %d 条，正在贴进库…": ("Wrote %d entries to the override table, importing into the database…", "対応表に %d 件書き込み、データベースへ反映中…"),
    "已删除「%s」—— 点「保存」写回覆盖表": ("Removed \"%s\" - click Save to write the override table", "「%s」を削除しました - 「保存」で上書き表に反映します"),
    "已加「%s」—— 点「保存」写进覆盖表并贴进库": ("Added \"%s\" - click Save to write the override table and push into the DB", "「%s」を追加しました - 「保存」で上書き表に書き込み、DBに反映します"),
    "已取消（%d 个新卡组没收录）": ("Cancelled (%d new decks not added)", "キャンセルしました（新デッキ %d 件は未収録）"),
    "已开启开机自启": ("Autostart enabled", "自動起動を有効にしました"),
    "已经在跑了": ("Already Running", "すでに起動しています"),
    "平局": ("Draw", "引き分け"),
    "建议": ("Suggested", "候補"),
    "建议：%s —— 点一下即标注": ("Suggested: %s — click to tag", "候補：%s — クリックでタグ付け"),
    "开始体检": ("Run Check", "検査を開始"),
    "开机后自动启动悬浮窗": ("Show the overlay automatically at startup", "起動時にオーバーレイを自動表示"),
    "开机自启": ("Launch at Startup", "自動起動"),
    "当前已标注：%s —— 选别的会覆盖，点外面则不改": ("Current tag: %s — picking another overwrites it; clicking outside changes nothing", "現在のタグ：%s — 別を選ぶと上書き、外側クリックなら変更なし"),
    "当前筛选 / 页签下没有对局": ("No duels under the current filter / tab", "現在のフィルター/タブでの対戦なし"),
    "当前筛选下没有对局 —— 换个月份 / 模式试试": ("No duels with the current filters — try another month / mode", "現在のフィルターでは対戦がありません —— 月 / モードを変えてみてください"),
    "当窗口关闭时": ("When Window Closes", "ウィンドウを閉じるとき"),
    "总胜率": ("Win Rate", "総勝率"),
    "悬浮窗已经在运行（看屏幕右上角）。": ("The floating window is already running (see the top-right corner of your screen).", "フローティングウィンドウはすでに実行中です（画面の右上をご確認ください）。"),
    "成功": ("Success", "成功"),
    "我方卡组未知 —— 打开「卡组编辑」界面即可自动获取": ("My deck unknown — open the Deck Editor to auto-detect", "自デッキ不明 — デッキ編集画面を開くと自動取得します"),
    "我方卡组：": ("My Deck:", "自デッキ："),
    "把改动写回覆盖表 CSV，并跑一次 fetch_meta_decks --cn-only 贴进库": ("Write changes back to the override CSV and run fetch_meta_decks --cn-only to push into the DB", "変更を上書き表CSVに書き戻し、fetch_meta_decks --cn-only を実行してDBに反映します"),
    "把记录目录里的对局重新入库（幂等）": ("Re-import duels from the record folders (idempotent)", "記録フォルダの対戦を再取込（冪等）"),
    "抓一次 masterduelmeta 榜单（联网，1~3 分钟），再把「不在覆盖表里」的卡组列出来让你勾选填中文名。": ("Fetch the masterduelmeta tier list (online, 1-3 min), then list decks not yet in the override table for you to check and name in Chinese.", "masterduelmetaのランキングを取得し（通信、1〜3分）、上書き表にないデッキを一覧表示して、チェックと中国語名の入力ができます。"),
    "抓取": ("Fetch", "取得"),
    "抓取失败（退出码 %d）": ("Fetch failed (exit code %d)", "取得に失敗しました（終了コード %d）"),
    "抓取完成，正在比对覆盖表…": ("Fetch finished, comparing with the override table…", "取得完了、対応表と照合中…"),
    "抓禁限表": ("Fetch Banlist", "制限リスト取得"),
    "按占比": ("By share", "採用率順"),
    "按卡名": ("By card name", "カード名順"),
    "按张数": ("By copies", "枚数順"),
    "掉段": ("Demoted", "降格"),
    "搜索": ("Search", "検索"),
    "搜索卡组：中 / 英 / 日 / 别名": ("Search decks: CN / EN / JP / alias", "デッキ検索：中 / 英 / 日 / 別名"),
    "搜索卡组：中 / 英 / 日 / 别名 / 拼音": ("Search decks: CN / EN / JP / alias / pinyin", "デッキ検索：中 / 英 / 日 / 別名 / ピンイン"),
    "收起明细": ("Hide Details", "詳細を非表示"),
    "改了 UI 代码后一键重启界面进程": ("Restart the UI process in one click after code changes", "UI コード変更後にワンクリックで再起動"),
    "改的是**覆盖表**（%s）；保存后自动跑一次 fetch_meta_decks.py --cn-only 把表贴进库。「站内英文名」是连接键、只读。": ("Edits the **override table** (%s); saving runs fetch_meta_decks.py --cn-only once to push it into the DB. \"Site name\" is the read-only join key.", "**上書き表**（%s）を編集します。保存すると fetch_meta_decks.py --cn-only が自動実行され、DBに反映されます。「サイト内英名」は読み取り専用の結合キーです。"),
    "数据体检": ("Data Check", "データ検査"),
    "数据工具": ("Data Tools", "データツール"),
    "数据截至 %s": ("Data as of %s", "データの取得時刻：%s"),
    "数据源：masterduelmeta（每月手动跑 fetch_meta_decks 刷新）": ("Source: masterduelmeta (refreshed monthly via fetch_meta_decks)", "データソース：masterduelmeta（毎月 fetch_meta_decks を手動実行して更新）"),
    "新卡组": ("New Decks", "新デッキ"),
    "无记录": ("no record", "記録なし"),
    "显示明细": ("Show Details", "詳細を表示"),
    "显示英文名（留空 = 用站内名）": ("Display name (empty = use site name)", "表示英名（空欄 = サイト内名を使用）"),
    "显示语言": ("Display Language", "表示言語"),
    "暂无数据": ("No data yet", "データがありません"),
    "最大化（再点一次还原）": ("Maximize (click again to restore)", "最大化（もう一度クリックで元に戻す）"),
    "最小化（隐藏到任务栏托盘）": ("Minimize (hide to tray)", "最小化（トレイに隠す）"),
    "最早到期": ("Earliest Expiry", "最も早い期限"),
    "有 B 类未知卡 id（要人工看一眼，不算失败）": ("Unknown card ids (category B) — worth a manual look, not a failure", "B類の未知カード id あり（要確認。失敗ではない）"),
    "有脚本在跑 —— 等它结束再点（输出在下方日志区）": ("A script is still running — wait for it to finish (output is in the log area below)", "スクリプト実行中 — 終了してからクリックしてください（出力は下のログ欄）"),
    "未知": ("Unknown", "不明"),
    "未领取的礼盒物品 · 按领取期限分组（最早到期在最上）": ("Unclaimed gift box items · grouped by claim deadline (earliest on top)", "未受取のプレゼント物品 ・受取期限ごとにグループ化（最も早い期限が最上段）"),
    "条数": ("Items", "件数"),
    "标注失败": ("Failed to Mark", "マークに失敗しました"),
    "检查库 / 卡表 / 记录目录的一致性": ("Check consistency of the DB / card table / record folders", "DB / カードDB / 記録フォルダの整合性をチェック"),
    "检查更新": ("Check Updates", "更新を確認"),
    "正在抓取榜单…（联网，1~3 分钟）": ("Fetching tier list… (online, 1-3 minutes)", "ティアリストを取得中…（オンライン、1〜3分）"),
    "正在重启界面…": ("Restarting UI…", "UI を再起動中…"),
    "段位获取中…（结算写入通常需要 1~2 分钟）": ("Rank pending… (settlement usually takes 1–2 minutes)", "ランク取得中…（確定まで通常 1〜2 分）"),
    "没有匹配「%s」的卡组 —— 点外面关闭，标注不变": ("No decks match \"%s\" — click outside to close, tag unchanged", "「%s」に一致するデッキなし — 外側クリックで閉じる（タグは変更なし）"),
    "没有新卡组 —— 覆盖表已是最新": ("No new decks — the override table is up to date", "新しいデッキはありません — 対応表は最新です"),
    "添加": ("Add", "追加"),
    "清除结果": ("Clear Result", "結果をクリア"),
    "点「读取礼盒」从游戏内存读一次（约 10-15 秒；游戏需进过礼盒页面）": ("Click \"Read Gift Box\" to read once from game memory (about 10-15 s; the game must have opened the gift box page)", "「プレゼントボックスを読む」を押すとゲームメモリから一度読み取ります（約 10-15 秒；ゲーム内でプレゼントボックス画面を開いたことがある必要があります）"),
    "点「运行」后，脚本输出显示在这里。": ("Script output appears here after you click Run.", "「実行」をクリックするとスクリプトの出力がここに表示されます。"),
    "点一下改标": ("Click to change the tag", "クリックでタグを変更"),
    "点一下标注对手卡组": ("Click to tag the opponent deck", "クリックで相手デッキをタグ付け"),
    "点击修正这一局的胜负（人工修正优先，不会被自动解析覆盖）": ("Click to fix the result (manual fixes take priority over auto-parse)", "クリックで勝敗を修正（手動修正は自動解析より優先）"),
    "点击关闭后": ("When Close Is Clicked", "閉じるをクリックした時"),
    "点击选择对手卡组": ("Click to pick the opponent deck", "クリックで相手デッキを選択"),
    "点卡图手动选，或进游戏卡组编辑界面自动获取": ("Click the art to pick manually, or open the in-game Deck Editor to auto-detect", "カード画像をクリックして手動選択、またはゲーム内のデッキ編集画面で自動取得"),
    "点开 %s 的战绩": ("Click to view matches of %s", "%s の戦績を表示"),
    "点悬浮窗的 × 是隐藏到托盘还是退出": ("Whether the overlay × hides to the tray or quits", "オーバーレイの × でトレイに隠すか終了するか"),
    "界面吃 demo 战绩（不读不写真库）": ("UI uses demo records (reads/writes no real database)", "UI はデモ戦績を使用（実 DB は読み書きしません）"),
    "留空 = 用站内名": ("Empty = use site name", "空欄 = サイト内名を使用"),
    "看 %s 的卡组构成（主卡 / 额外）": ("View deck composition of %s (main / extra)", "%s のデッキ構築を見る（メイン/エクストラ）"),
    "确定": ("OK", "決定"),
    "确认": ("Confirm", "確認"),
    "礼盒是空的（或者已经全领了）": ("The gift box is empty (or everything has been claimed)", "プレゼントボックスは空です（またはすべて受け取り済みです）"),
    "禁限 %s 张（最近 %s）": ("Banlist: %s cards (latest %s)", "制限 %s 枚（最新 %s）"),
    "站内英文名（连接键，只读 —— 要改名字请新建条目）": ("Site name (join key, read-only - add a new entry to rename)", "サイト内英名（結合キー、読み取り専用 - 名前を変えるには新規エントリで）"),
    "结果": ("W/L", "勝敗"),
    '胜': ('W', 'W'),
    '不变': ('Unchanged', '変化なし'),
    '是': ('Yes', 'はい'),
    '否': ('No', 'いいえ'),
    '负': ('L', 'L'),
    '平': ('D', 'D'),
    "联网失败（上游取不到，稍后再试）": ("Network failed (upstream unreachable, try again later)", "通信に失敗しました（上流に接続できません。後でお試しください）"),
    "胜利": ("Win", "勝利"),
    "胜率 %s": ("Win rate %s", "勝率 %s"),
    "自定义卡组名…": ("Custom deck name…", "カスタムデッキ名…"),
    "自定义卡组名…（打一个名字，点「添加」加进覆盖表）": ("Custom deck name… (type a name, click Add to put it in the override table)", "カスタムデッキ名…（名前を入力して「追加」で上書き表に追加）"),
    "行为": ("Behavior", "動作"),
    "设置失败 —— 已回滚": ("Setting failed — reverted", "設定に失敗しました — 元に戻しました"),
    "设置失败 —— 没能写入「启动」目录": ("Failed — could not write to the Startup folder", "失敗 — スタートアップフォルダに書き込めませんでした"),
    "语言只影响卡名/卡组名的显示；匹配一律走卡 id。\n调试模式立即生效（不用重启）；demo 的统计数字是设计稿\n那套（65.6% / 21-11 / 77.8%），一眼可辨；demo 下点对手\n卡组格只提示、不标注。": ("Language affects only the display of card/deck names; matching always uses card ids.\nDebug mode takes effect immediately (no restart); demo stats are the mock design numbers (65.6% / 21-11 / 77.8%), easy to tell apart; in demo mode tapping an opponent deck cell only hints, never marks.", "言語はカード名/デッキ名の表示にのみ影響し、マッチングは常にカード ID で行います。\nデバッグモードは即時反映（再起動不要）。デモの統計値は設計稿のもの（65.6% / 21-11 / 77.8%）で一目で判別できます。デモモードで対戦相手デッキのセルをクリックしてもヒントを表示するだけで、マークはしません。"),
    "读取中……（全进程扫一次，约 10-15 秒）": ("Reading… (one full-process scan, about 10-15 s)", "読み取り中……（全プロセスを 1 回スキャン、約 10-15 秒）"),
    "读取出错：%s": ("Read error: %s", "読み取りエラー：%s"),
    "读取失败": ("Read failed", "読み取りに失敗しました"),
    "读取礼盒": ("Read Gift Box", "プレゼントボックスを読む"),
    "调试模式": ("Debug Mode", "デバッグモード"),
    "还没打过": ("no duels yet", "まだ対戦なし"),
    "还没有 Tier 数据 —— 跑一次 tools/fetch_meta_decks.py（每月手动）就有": ("No tier data yet — run tools/fetch_meta_decks.py once (monthly, manual)", "ティアデータがまだありません —— tools/fetch_meta_decks.py を一度実行してください（毎月手動）"),
    "还没有可统计的对局 —— 打完天梯/巅峰对局并在「战绩」页标注对手卡组后，这里会出对阵矩阵": ("No duels to analyze yet — play Ranked/Rating duels and tag opponent decks on the Matches page, and the matrix will appear here", "集計できる対戦がまだありません —— ランク/レーティング対戦をして「対戦」ページで相手デッキをタグ付けすると、ここにマトリクスが表示されます"),
    "还没有对局记录": ("No duel records yet", "対戦記録はまだありません"),
    "还没有数据 —— 点上面的「读取礼盒」，从游戏内存读一次": ("No data yet — click \"Read Gift Box\" above to read once from game memory", "データがまだありません —— 上の「プレゼントボックスを読む」でゲームメモリから一度読み取ってください"),
    "还没有禁限表数据 —— 跑一次 tools/fetch_banlist.py（每月手动）就有": ("No banlist data yet — run tools/fetch_banlist.py once (monthly, manual)", "リミットレギュレーションのデータがまだありません —— tools/fetch_banlist.py を一度実行してください（毎月手動）"),
    "这些卡组不在覆盖表里 —— 勾上并填中文名": ("These decks are not in the override table — check them and fill in the Chinese names", "これらのデッキは対応表にありません — チェックして中国語名を入力してください"),
    "退出主程序": ("Exit App", "アプリを終了"),
    "采集器：未运行": ("Collector: not running", "コレクター：未実行"),
    "采集器：运行中": ("Collector: running", "コレクター：実行中"),
    "采集器：运行中（%ds 前心跳）": ("Collector: running (heartbeat %ds ago)", "コレクター：実行中（%ds前にハートビート）"),
    "重启界面": ("Restart UI", "UI を再起動"),
    "重新读取当前卡组（自动识别没跟上时用）": ("Re-read the current deck (fallback when auto-detect lags)", "現在のデッキを再読み込み（自動認識が追いつかないとき）"),
    "隐藏到任务栏托盘": ("Hide to System Tray", "タスクトレイに格納"),
    "领取期限": ("Claim Deadline", "受取期限"),
    "（数据已更新 —— 相关页面重开看板后刷新）": ("(Data updated — reopen the dashboard to refresh related pages)", "（データを更新しました — 関連ページはダッシュボードを開き直すと反映されます）"),
    "（最后心跳 %s）": (" (last heartbeat %s)", "（最終ハートビート %s）"),
    "（本月该分组没有卡组）": ("(no decks in this tier this month)", "（今月このティアのデッキはありません）"),
    "（独立模式：这里会写入「启动」目录）": ("(Standalone: this would write to the Startup folder)", "（単体実行：ここでは「スタートアップ」フォルダに書き込みます）"),
    "（独立模式：这里会联网抓榜单）": ("(Standalone mode: this would fetch the tier list online)", "（単体モード：ここでティアリストをオンライン取得します）"),
    "（独立模式：这里会重启界面）": ("(Standalone mode: this would restart the UI)", "（単体モード：ここで UI を再起動します）"),
    "（这一栏是空的）": ("(this section is empty)", "（この欄は空です）"),
    "，对局中": (", in a duel", "、対戦中"),
    "，待机中": (", idle", "、待機中"),

    # ---- 二轮补充（2026-10-04 用户实测：悬浮窗标题 / 表头短词）----
    '总览': ('Overview', 'オーバービュー'),
    '先后攻统计': ('First/Second Stats', '先攻/後攻'),
    '最近对局': ('Recent Duels', '最近の対戦'),
    '游戏启动时弹悬浮窗': ('Pop Up When Game Starts', 'ゲーム起動時にオーバーレイを表示'),
    '检测到游戏启动时，自动弹出悬浮窗': ('Automatically show the overlay when the game starts',
                              'ゲームの起動を検知したらオーバーレイを自動表示'),
}


def lang() -> str:
    """当前语言（``ui/lang``，默认 ``cn``）。进程内缓存 —— 切语言重开窗口生效。"""
    global _LANG
    if _LANG is None:
        try:
            _LANG = str(_settings().value('ui/lang', 'cn')) or 'cn'
        except Exception:
            _LANG = CN
    return _LANG


def reset_lang() -> None:
    """测试 / 设置页改语言后调用：下次 `tr()` 重新读设置。"""
    global _LANG
    _LANG = None


# 开发期可观测性（审查 §4.1）：设 MD_I18N_STRICT=1 时，未命中的键返回
# 「⟦原文⟧」并记进 MISSES —— 「还剩多少没译」跑一次就有数，不用靠眼睛找。
STRICT = os.environ.get('MD_I18N_STRICT') == '1'
MISSES = []


def tr(s: str) -> str:
    """骨架文案 → 当前语言。cn 原样返回；没收录的键优雅回落中文。

    规则（审查 §4.2/§五 定案）：
    * 键是**字节级**匹配 —— 改标点/空格 = 删键（翻译会静默失效），
      由 tests/test_ui_i18n.py 的静态护栏兜住；
    * **多义串不进表**（如「失败」= 抓取失败 vs 对局结果）—— 那类用整句键；
    * 英文大小写：标题/按钮 Title Case，量词/单位 lower case；
    * 复数不做（`1 duels` 这类不完美复数是工具类 UI 的常见取舍，用户接受）。
    """
    if not s:
        return s
    cur = lang()
    if cur == CN:
        return s
    pair = _T.get(s)
    if pair is None:
        if STRICT and s not in MISSES:
            MISSES.append(s)
        return '⟦%s⟧' % s
    if cur == EN:
        return pair[0]
    if cur == JA:
        return pair[1]
    return s                       # 未知语言一律回落中文
