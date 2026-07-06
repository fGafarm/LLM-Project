# OPS.md — kinmyakucode 運用ルールブック (単一情報源)

> 最終更新: 2026-07-06 / 事故教訓の原典: `StockFlow/POSTMORTEM_2026-07-05.md` + `RECOVERY_2026-07-06.md`
> 迷ったらまず `python health_check.py`。Claude Code なら `/daily-ops`。スキル索引: `.claude/skills/README.md`

---

## 0. システム全体像 (1分で思い出す用)

```
EDINET API ──┐                                        ┌→ R2 (store同期)
TDnet HTML ──┤→ daily_update.py → pdf_xbrl(E:) ZIP原本 │
             │     ├ xbrl_batch_extractor (--output必須!) → financial_analysis_system/xbrl_store (正史)
             │     ├ tanshin_xbrl_extractor (overwrite=False)      │
             │     └ extract_setsubi → fixed_assets_store          ↓
             │  generate_all_companies + integrate_hidden_assets + metrics_summary
             │     → StockFlow/frontend/public/data/*.json
             └→ git push (kinmyakucode main) → Cloudflare Pages 自動デプロイ → kinmyakucode.com
```

- **正史store**: `financial_analysis_system/xbrl_store` (約5,950フォルダ)。ルート直下 `xbrl_store/` はレガシー。
- **配当性向は%値格納 (×100厳禁)**。株式保有比率は0-1格納。
- 社名ゆれでstoreフォルダ二重化あり → **必ずコード一致で探す**。

## 1. 日次ランナーは2系統ある (二重運用)

| | ローカルタスク `DailyStockFlowUpdate` | GitHub Actions `daily-update.yml` |
|---|---|---|
| 発火 | 毎朝 06:00 JST (Task Scheduler) | 毎朝 06:00 JST (cron) + 手動 |
| コード | **作業ツリーそのまま** (未コミット含む) | **origin/main** (pushしたものだけ) |
| ZIP原本 | E:\PDF\PDF+XBRL に永続保存 ✅ | ランナー内で消滅 ❌ (storeのJSONだけR2へ) |
| push | StockFlow の branch 全体を道連れpush ⚠️ | public/data のみ add (bot commit) |
| 死活確認 | health_check.py が State/LastResult を見る | gh CLI 必須 (`gh run list`) |

**推奨方針**: Actions を「正」、ローカルを「ZIP原本アーカイブ + 監査」とし時刻をずらす (例: Actions 06:00 / ローカル 07:30)。ローカルを止めたままにすると **ZIP原本が溜まらなくなる** ので、完全にActions単独へ移行する場合は workflow に「ZIPもR2へアップロード」を追加してから。

**⚠️ 鉄則 (feedback-local-daily-task-autopush)**: StockFlow で夜をまたぐ作業をする時は
`Disable-ScheduledTask -TaskName DailyStockFlowUpdate` → 作業後 `Enable-ScheduledTask`。
**Disableしたら再有効化をタスク登録するまで作業を終えない** (今回2日間止まった原因)。

## 2. 朝のルーチン (5分)

```powershell
python health_check.py          # 標準診断 (~20秒)
python health_check.py --audit  # 週1回はこちら (EDINET/TDnet突合まで実走)
```

| 結果 | アクション |
|---|---|
| ALL GREEN | 何もしない |
| TASK Disabled | 意図的か思い出す → 不要なら Enable-ScheduledTask |
| GIT ahead>0 (root) | 修正がCIに乗っていない → §4 のpush手順へ |
| StockFlow 未コミットN件 | 06:00までに commit+push するか、内容を確認して破棄 |
| AUDITS FAIL | `python yuho_audit.py --days 7` で named 一覧 → §5 リカバリへ |
| MOUNT CRIT | **即対応**: E:接続確認 → `mklink /J pdf_xbrl E:\PDF\PDF+XBRL` |

## 3. 監査・品質ゲート一覧 (何がどこを守るか)

| ツール | 突合対象 | 実行 | 失敗時 |
|---|---|---|---|
| `yuho_audit.py` | EDINET提出一覧 vs ZIP vs store (有報/半期) | daily_update Step 8b + CI末尾 + 手動 | named一覧 → 再抽出 |
| `tanshin_audit.py` | TDnet短信 vs store | daily_update Step 8 | 同上 |
| `financial_analysis_system/audit_yuho_coverage.py` | 全ZIP vs store 全量 (年単位) | 月1 or 復旧後 | 強制再抽出 |
| `validate_payout.py` | 配当性向の単位事故 (×100) | CI (push前ブロック) | pushされない |
| `scan_forbidden.py` | 投資推奨表現 (金商法) | CI (push前ブロック) | 同上 |
| `scan_report_quality.py` | LLM謝罪文・パーサ崩れ | CI (push前ブロック) | 同上 |
| `audit_meta.py` | canonical/メタ/i18n露出 (要build) | ビルド後 `npm run check:all` | 修正まで再申請禁止 |
| `detect_interim_contamination.py` | 半期→年度混入 | 抽出ロジック変更後 | S0 T4参照 |
| `health_check.py` | 上記全部の死活 + 環境 | 毎朝 | 本表に従う |

**新しい取込経路を作る時は、必ず同時に expected-vs-actual 監査を作る** (無い経路は静かに死ぬ)。

**--skip-existing の意味 (2026-07-06 改定)**: 「ファイルが存在するか」ではなく **docID比較**。
同一docID抽出済み→skip / 壊れJSON・tanshin由来→再抽出 / 新しいdocID (訂正版)→再抽出 /
古いZIPが新しいstoreを上書きしない (ダウングレード防止)。半期ZIPの判定対象は `{year}_Q2.json`。
また年度モードは `半期/` フォルダもスキャンし、Interim-onlyガードが `{year}_Q2.json` へ振り替える
(2026-07-03 の 5942/9872 取込漏れの根治)。週次は `weekly_audit.bat` (health_check --full +
yuho_audit 14日 + 全量カバレッジ) — 登録コマンドはファイル冒頭コメント参照。

## 4. push手順 (チェックリスト)

**mainリポ (LLM-Project) — CIの挙動が変わる:**
1. `git diff origin/main..main --stat` で差分確認
2. daily_update.py を触った場合: `python daily_update.py --date <平日>` を手動1回 → ログ確認
3. push → 翌朝 `gh run list` で green 確認 (gh未導入なら github.com/fGafarm/LLM-Project/actions)

**StockFlow (kinmyakucode) — 本番サイトに出る:**
1. `python health_check.py --gates` (payout/forbidden/report_quality)
2. データ大量変更時: 分布の端を目視 (ランキング1位・最下位、売上YoY±50%超の社数)
3. ファイル数確認: `Get-ChildItem StockFlow\frontend\public -Recurse -File | Measure-Object` → **15,000未満** (Cloudflare上限20k)
4. commit → push → 5分後に本番3ページ目視 (トップ / 変更した企業 / ランキング)
5. **「done」と書く前に実物を見る** (feedback-inspect-every-output)

## 5. リカバリSOP: 取込が死んでいた時 (POSTMORTEM §3)

順序厳守。**スキップ禁止**:
1. **マウント**: `health_check.py` → pdf_xbrl junction / E: を復旧
2. **ZIP有無**: `python yuho_audit.py --days 30` → NO_ZIP (DL漏れ) と MISSING (抽出漏れ) を分離
3. **DL漏れ**: `python daily_update.py --date YYYY-MM-DD` を該当日ごとに再実行
4. **抽出漏れ**: 壊れ残骸が疑われる年は **強制再抽出 (--skip-existing を外す)**:
   `python financial_analysis_system/xbrl_batch_extractor.py --scan-folder pdf_xbrl --years <年> --output financial_analysis_system/xbrl_store`
   ※ `--output` を忘れるとレガシーstoreに書く (†G事故の真因)
5. **全量検算**: `python financial_analysis_system/audit_yuho_coverage.py --years 2025,2026`
6. **再生成**: `python StockFlow/scripts/generate_all_companies.py` → integrate_hidden_assets → generate_metrics_summary
7. **ゲート**: `python health_check.py --gates` 全PASS
8. **分布の端を目視** → commit+push → 本番確認

Claude Code なら `/recover-pipeline` がこの手順を対話で実行する (push前に必ず確認を挟む)。

## 6. 秘密情報ルール

- 置き場所は2つだけ: `backend/.env` (EDINET_API_KEY, Fudousan_API_KEY) / `backend/keys/` (Google SA)
- `credentials.json` は全階層で gitignore 済み (2026-07-06)。**新しい鍵ファイルは必ず追加前に `git check-ignore <path>` で確認**
- ログ・レポート・スキル・プロンプトに鍵の値を書かない。CIは `${{ secrets.* }}` のみ

## 7. ファイル衛生ルール

- **単発調査スクリプト** (check_/analyze_/debug_/compare_): 今後は `financial_analysis_system/tools/oneoff/` に作る。役目が済んだら削除
- **恒久ツール**に昇格したら: docstringに「使い方 + 終了コード」を書き、本表 (§3) に登録
- 既存の root 直下75本の整理は保留中 (移動は import 破壊リスクがあるため一括では行わない)
- `nul` という名のファイルはリダイレクトミスの残骸: `Remove-Item '\\?\C:\...\nul' -Force` で削除
- 出力系 (`output_v*`, `company_data/`, `rag_db/`, `*.log`) は gitignore 済み — git status に出たら .gitignore に追記
- **ログ保持ポリシー**: `python rotate_logs.py --apply` (weekly_audit.bat に組込済み)。
  daily_update 60日 / recovery系 90日 を超えたら gzip で `logs/archive/` へ移動 (削除はしない)。
  scheduler.log は5MB超で退避。EDINETキャッシュは最小フィールド形式+90日剪定 (yuho_audit側)

## 8. Claude Code 自動化レシピ

**スキル (このリポの .claude/skills/):**
- `/daily-ops` — health_check 実行 + 赤/黄の診断と対処提案 (毎朝これ1つでOK)
- `/recover-pipeline` — §5 のリカバリSOPを対話実行 (push前確認つき)
- `/pre-push` — §4 のpushチェックリストを自動実行
- `/adsense-recheck` — T9再申請前の全DoD検証 (機械検証+本番目視+人間手順)

**ヘッドレス自動実行 (課金が発生するため有効化は手動判断):**
- `ops_auto_diag.ps1` — 2段構え自動診断 (全緑ならClaude起動なし=API費ゼロ、異常時のみ
  読み取り専用で診断を logs\ops_advice.md に書く)。登録コマンドはファイル冒頭コメント参照
- `weekly_audit.bat` — 週次ディープ監査 (日曜09:00推奨)。同上
- **push・タスク有効化・外部投稿を伴う操作は自動化しない** (人間の承認ゲートを残す)
- X投稿系は `safe_schedule.py` の3重ガード + パイロット1件 + キュー目視が憲法 (自動化上限はそこまで)

## 9. 既知の未解決事項 (2026-07-06 更新)

- [ ] **mainリポ push 承認** — 未pushコミット3件 + 本日の未コミット分 (--output修正 / yuho_audit /
  health_check / 抽出器の半期スキャン+docID-skip / CI監査+ZIPアーカイブ / OPS・スキル群)。
  **これをしないとCIは修正前コードのまま毎朝走る**
- [x] **全量復旧完了 (2026-07-06 深夜)**: 60日監査で OK 2,608 / MISSING 0 / NO_ZIP 0 / STALE 0 / 年度汚染 0。
  run3+run4 で tanshin→有報 1,096社・訂正再抽出 1,190・壊れ残骸再抽出 823・単体のみ採用 1,452・強制Q2 1,921。
  検疫 (可逆・`_dup_quarantine_20260706/`): 2027_Q2誤キー147社 / 誤ラベルZIP 444本 / 8887年度汚染 / 9552・6080誤キーQ2。
  **クラウド (R2) 系統には未反映** — push後、次のCI upload-changed --since が拾える時間内にローカルで
  `python r2_sync.py upload-all financial_analysis_system/xbrl_store` を実行するのが確実 (要ユーザー判断・数十分)
- [ ] NO_REVENUE 残10社 (タグ変種バックログ): 博報堂DY/スカイマーク/ミニストップ/セーラー広告/創薬系4社
  (売上ゼロが正当の可能性)/alpha 2社。raw_tags からタグ特定→FALLBACK_TAGS追加→migrate_revenue_tags の型
- [ ] 過去年度 (2020-2024) の掃除: 単体のみ全欠損の回収 (2025/26は済み)・誤キーQ2 (source日付≠キー年) の全量検出
- [ ] 誤ラベル `訂正有報` ZIP (7/4朝の旧コードDL分、実体は半期) が `pdf_xbrl/2026/有報/` に残存。
  新skipロジックで無害化済みだが、`2026/半期/` への移動が望ましい (5942/9872 の2件)
- [ ] レガシー root `xbrl_store/` の棚卸し — 7/5 に書かれた抽出物で正史に無いものが他にもある可能性
  → `yuho_audit.py --days 30` で欠落を洗い、あれば強制再抽出
- [ ] DailyStockFlowUpdate が Disabled のまま → §1 の方針決定
- [ ] StockFlow 未コミット660件 → **調査済み・SAFE_TO_PUSH** (T4修復の成果物: 458社の前年複製修復+40社FY2026追加。
  本番は現在index.jsonと詳細ページが不整合でpushが修復方向)。`/pre-push` 経由でpush推奨。
  フォローアップ: 前年複製が残る46社 (1301,1380,1711他) / 3187のquarterly 1行減 / 2003 payoutRatio 298.57%の検算
- [ ] gh CLI 未導入 → `winget install GitHub.cli` + `gh auth login`
- [ ] R2 の旧2026.json 617件削除 / Search Console 再クロール / AdSense T9 再申請 (→ `/adsense-recheck`)
- [ ] integrated_analyzer.py の742行未コミット → **調査済み・COMMIT_AS_IS 推奨** (2026-01の「XBRL vs PDF
  クロス検証」実験 = 検証層テーゼの原型。本番経路は一切importしておらず実害なし。
  **必ず pipeline_numeric/sheets_reader.py + numeric_comparator.py と3ファイルセットでcommit**、WIP明記)
- [x] 9872 の半期タグ薄 → 根治 (単体のみ提出者は NonConsolidated コンテキストへフォールバックする改修、
  rev=297.4億で正史store更新済み。5942等の連結企業は不変を隔離テストで確認)
- [ ] 一時スクリプト整理 → 全数分類完了: `financial_analysis_system/SCRIPTS_INVENTORY.md`
  (CORE16/TOOL16/ONEOFF43 + import hazards)。移動実行はユーザー承認後
- [ ] **過去年度の単体のみ全欠損の回収** (レビューで発見): store 58,511ファイル中コア全欠損7,401件、
  うち「他年度は取れているのに特定年度だけ全欠損」約499社 (例: 2130メンバーズ2025、1848富士PS 2021)。
  単体フォールバック実装済みの現コードで対象年度を再抽出すれば回収可能 (新規5942/9872方式)
- [ ] 建設業等の業界別revenueタグ欠落 (1950日本電設工業: 有報再抽出でもrevenue空)。
  FALLBACK_TAGS への NetSalesOfCompletedConstructionContracts 系追加を検討 (5/10-11の9タグ追加と同型)
