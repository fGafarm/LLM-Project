# WORK_SUMMARY 2026-07-06 → 07-13 — 業務改善・全量復旧・グローバル化・記事量産の総括

> 詳細の一次資料: `OPS.md` (運用) / `RECOVERY_2026-07-06.md` (復旧) / `EXPANSION_BLUEPRINT.md` (4カ国) /
> `financial_analysis_system/SCRIPTS_INVENTORY.md` (棚卸し) / `StockFlow/drafts/articles_20260711/00_INDEX.md` (記事)

## 1. 何が変わったか (Before → After)

| 領域 | 7/6朝 | 7/13 |
|---|---|---|
| パイプライン監視 | 目視のみ (サイレント死が構造的) | **3カ国監査常設** (yuho_audit / us_yuho_audit / tw_audit) + health_check + report_reconcile |
| JP store 品質 | 欠落170+STALE1,273 (復旧未完に無自覚) | **欠落0 / STALE0 / 汚染0** (60日監査) |
| 営業利益 | **95%の企業で経常利益が混入** (全年度) | 根治 — 短信vs有報一致率 7.27%→**99.33%** |
| 棚卸資産 | 不動産・建設で系統的過小 (大和ハウス345億) | 根治 — 17,470ファイル修復 (同2.57兆)・独立実装と100%一致 |
| 純利益ほか | IFRS移行年に参考J-GAAP値が混入 (東京海上9,804億等) | 根治 — 208社再抽出 (同5,313億=IFRS正値) |
| 短信取込 | 訂正を偽Q1化・四半期/中間を全量スキップ | 根治 — form接頭辞+context判定・検疫194+正置213 |
| 掲載 | 4,892社 | **5,093社** (+201=英数字コードIPO組) |
| 海外 | US 547社(旧情報)・TW 764社1年分 | US 5,976社store+監査 (BRK売上371.4Bに修正) / TW 1,079社×5年+金融5業種 (TWSE APIバグ自動判別) |
| 記事 | 実測記事3本 (T8) | **+95本の下書き** (全て機械集計+二重検算) + 公開優先度索引 S22/A48/B25 |
| X運用 | 手動+事故リスク | x_preflight機械ゲート+/x-daily正式フロー+キュー掃除 |

## 2. 時系列ダイジェスト

- **7/6**: 棚卸し→観測性ゼロが最大ボトルネックと特定。health_check/yuho_audit/OPS.md/スキル3種を実装。
  初回監査で取込漏れ検出→根因2つ実証 (半期フォルダ不可視+--output欠落でレガシーstore行き)。
  敵対的レビュー (50 agents) の確定22件を全修正 (強制Q2化・docID選択/skip・スパースガード・単体フォールバック等)。
  全量復旧 run1-4: tanshin→有報1,096社・訂正反映1,190・壊れ残骸823・単体のみ1,452・半期Q2 1,921。
  検疫 (可逆): 2027_Q2誤キー147社・誤ラベルZIP444本ほか
- **7/7**: push承認→main 8コミット+**本番デプロイ3,602ファイル** (TDK 2.50兆/西武HD 5,133億の有報行を実測確認)。
  R2認証欠如→巻き戻り防止でCI cron一時停止。EXPANSION_BLUEPRINT (米韓台EU、一次情報調査)
- **7/11**: スキルカタログ/ログ整理 (43→17MB)。「R2以外全部」: TW金融5業種 (**TWSE公式APIの値ズレバグを
  算術自動判別で吸収**)・業種バックログ763件解消・us_yuho_audit (BRK過少$121.7B修正・BDC回収)・
  T9レディネス実測 (audit_meta違反0 / **out残枠964→R2移行が最優先と判明**)・revenueタグ13種。
  X体制整備 (x_preflight・/x-daily・33件キュー掃除)
- **7/12**: 記事量産14弾×検算付きで**95本+総索引**。記事54の突合が**P0発見** (営業利益=経常/IFRS二重表) →
  即修復 (migrate 63,423+再抽出208社)。P1 (訂正短信Q1誤読・qced/sced全スキップ) も根治
- **7/13**: P1×2 (在庫過小17,470修復・IFRS経常120社None化)。**4系統のデータバグすべて根治**

## 3. 作った恒久資産

- **監査/診断**: yuho_audit.py / us_yuho_audit.py / tw_audit.py / health_check.py / report_reconcile.py /
  x_preflight.py / audit_yuho_coverage.py (常設化)
- **修復ツール**: migrate_operating_income.py / migrate_inventories_ordinary.py / migrate_revenue_tags (増強)
- **運用**: OPS.md / rotate_logs.py / weekly_audit.bat / ops_auto_diag.ps1 / RECOVERY_2026-07-06.md /
  SCRIPTS_INVENTORY.md / EXPANSION_BLUEPRINT.md
- **スキル7種**: /daily-ops /recover-pipeline /pre-push /adsense-recheck /x-daily /article-draft (+tanshin-tweet既存) + カタログ
- **コンテンツ**: 記事下書き95本+00_INDEX.md (証拠JSON・集計スクリプト同梱)
- push済みコミット: 主要12+ (†G修正〜P1×2まで)

## 4. 未完 (ユーザー判断・手動待ち)

1. **`Enable-ScheduledTask -TaskName DailyStockFlowUpdate`** + `python daily_update.py --days 8` (7/5以降の空白回収)
2. **R2認証→ `python r2_sync.py upload-changed financial_analysis_system/xbrl_store fixed_assets_store --since 1209600`** → 連絡後cron復活
3. **R2移行プロジェクトのGO** (public/data退避 — out残枠964の解消、TW/US/記事拡張の物理前提)
4. 保険業の売上定義 (OPS §9 案a=経常収益統一を推奨) / T9再申請 (Search Console→待機→申請、`/adsense-recheck`) /
   gh CLI (winget失敗×2、手動導入) / LLMレポート再生成232行 (監視付きセッションで) / X投稿の運転開始 (`/x-daily`)

## 5. 最大の学び

**「AIが読み、機械が検算し、落ちたものは出さない」は、外向けの差別化である前に自衛装置だった。**
記事95本の副産物として自社データのP0×2+P1×2を発見・根治できたのは、全数値に機械集計+独立二重検算を
強制した規律の直接の成果。今後の新機能はこの規律 (expected-vs-actual監査の同時実装、OPS §3) を憲法とする。
