# SCRIPTS_INVENTORY.md — スクリプト全数棚卸し台帳 (2026-07-06)

financial_analysis_system/ 直下 75本 + リポジトリroot 9本 の全数分類 (全ファイルのdocstring読解 + 全リポのimport/subprocess/bat/yml/md参照Grepによる)。
整理ルールは `OPS.md` §7。**CORE は移動・改名厳禁**。ONEOFF は `tools/oneoff/` へ移動候補 (import hazards 確認済み)。

## CORE — 参照されている。動かすな (16本)

| ファイル | 参照元 |
|---|---|
| `daily_update.py` (root) | daily_update.bat / タスク / CI L56 / OPS.md |
| `tanshin_audit.py` (root) | daily_update Step 8 |
| `yuho_audit.py` (root) | daily_update Step 8b + CI + OPS.md |
| `health_check.py` (root) | OPS.md + /daily-ops skill |
| `r2_sync.py` (root) | CI (download / upload-changed) |
| `xbrl_batch_extractor.py` | daily_update subprocess + run_quarterly_batch import |
| `xbrl_validator.py` | xbrl_batch_extractor L97 import (CORE連鎖) |
| `extract_setsubi.py` | daily_update import + batch_extract_facilities + _tmp_setsubi/約20本 |
| `calculate_hidden_assets.py` | daily_update import |
| `a29_zoning.py` | calculate_hidden_assets L879 import (CORE連鎖) |
| `tanshin_xbrl_extractor.py` | daily_update import |
| `detect_interim_contamination.py` | OPS.md 品質ゲート表 (S0 T4) |
| `audit_yuho_coverage.py` | OPS.md + /recover-pipeline skill |
| `config.py` | パッケージ共通 |
| `main.py` | パッケージエントリ (integrated_analyzer 経由で生きている) |
| `Run_integrated_v10_2.py` | メインエントリ。run_v10xx系/rerun/run_nikkei225 が import |

## TOOL — 恒久ツール。残す (16本)

migrate_pik_fields / migrate_revenue_tags / batch_extract_facilities / extract_rental_real_estate /
build_land_price_db / download_a29_zoning / download_transaction_prices / merge_transaction_prices /
geocode_facilities / verify_hidden_assets / cross_validate_641 / extract_company_data /
run_quarterly_batch / extract_all_statements / Ticker_get / (root) generate_tweets_v3

## ONEOFF — 使い捨て。`tools/oneoff/` へ移動候補 (43本)

**日付/企業固定の調査**: check_2020_tags, check_2020_complete, check_sony_2020, extract_all_2020_tags,
analyze_reports, check_dividend, check_report_timestamps, debug_dividend_flow, compare_xbrl_keys,
check_kyokuyo_store, compare_all_companies, (root) check_structure, test_sort, verify_aapl_sector

**適用済みパッチ/修正**: fix_validation_errors, fix_batch_confidence, patch_bs_tags, patch_full_bs_cf,
patch_shareholder_data, patch_comprehensive_tags, patch_segment_summary, fix_shareholder_bug,
fix_shareholder_all_years

**旧バージョンのバッチ/テスト** (Run_integrated_v10_2 に依存するものはセットで移動):
run_v102_test_batch, run_5_companies_test, run_v1044_batch, run_v1045_retest, run_v1046_batch,
run_v1047_batch, run_v1048_retest, test_v1049_segment_fix, audit_segment_issues, rerun_problem_companies,
run_v105_test, test_v105_fix, run_nikkei225_batch(完了済), run_sequential_years, wait_and_continue,
regenerate_10_companies, test_xbrl_validation, test_zero_tolerance, extract_nikkei225,
analyze_validation_issues, analyze_segment_issues, analyze_segment_issues_v2, verify_50_quality,
scan_all_tags, Run.py, run_integrated, Run_integrated_v9, Run_integrated_v10, Run_integrated_v10_1,
(root) generate_tweets, generate_tweets_v2

## 移動時の注意 (import hazards)

- `xbrl_validator` / `extract_setsubi` / `calculate_hidden_assets` / `a29_zoning` /
  `tanshin_xbrl_extractor` / `xbrl_batch_extractor` / `extract_company_data` — **移動厳禁** (CORE/import連鎖)
- ONEOFF の run_v10xx系は `Run_integrated_v10_2.py` (残留) を import — 移動後は実行不可になるが使い捨てなので許容
- `regenerate_10_companies` は `Run_integrated_v10_1` を subprocess 参照 — 両方ONEOFFなのでセット移動
- `scan_all_tags` は patch_comprehensive_tags / patch_shareholder_data を相対パス参照 — セット移動
- **一括移動はユーザー承認後に。移動→ `python -m py_compile` 全通し→ daily_update 手動1回で無事故確認**
