#!/usr/bin/env python3
"""
四半期報告書バッチ処理スクリプト

使い方:
  # 単一年度（フォルダ年度）
  python run_quarterly_batch.py --year 2023

  # 全年度（2020-2024）
  python run_quarterly_batch.py --all

  # 途中再開
  python run_quarterly_batch.py --year 2023 --resume

  # テスト（3社のみ）
  python run_quarterly_batch.py --year 2023 --test
"""

import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime

# xbrl_batch_extractor を同ディレクトリからインポート
sys.path.insert(0, str(Path(__file__).parent))
from xbrl_batch_extractor import (
    Config, scan_all_quarterly_zips, batch_process_quarterly,
    load_company_list_from_sheets, logger
)


def load_progress(progress_file: Path) -> dict:
    if progress_file.exists():
        with open(progress_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"completed": {}, "failed": {}}


def save_progress(progress_file: Path, progress: dict):
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def run_batch(year: str, resume: bool = False, test: bool = False,
              skip_existing: bool = True):
    """指定年度の四半期バッチ実行"""

    progress_file = Path(f"batch_progress_quarterly_{year}.json")
    progress = load_progress(progress_file) if resume else {"completed": {}, "failed": {}}
    output_base = Config.OUTPUT_BASE

    # 四半期ZIPをスキャン
    logger.info(f"\n四半期バッチ: {year}年度")
    company_zips = scan_all_quarterly_zips(Config.XBRL_BASE, [year])
    all_companies = sorted(company_zips.keys())

    if test:
        # テスト: 最初の3社のみ
        all_companies = all_companies[:3]
        logger.info(f"テストモード: {len(all_companies)}社のみ処理")

    # 既に完了した企業をスキップ
    if resume:
        completed_codes = set(progress.get("completed", {}).keys())
        remaining = [c for c in all_companies if c not in completed_codes]
        logger.info(f"再開モード: {len(completed_codes)}社完了済み → 残り{len(remaining)}社")
    else:
        remaining = all_companies

    logger.info(f"対象: {len(remaining)}社 (全{len(all_companies)}社中)")

    company_names = load_company_list_from_sheets()
    start_time = time.time()

    for i, company_code in enumerate(remaining):
        try:
            from xbrl_batch_extractor import process_company_quarterly

            result = process_company_quarterly(
                company_code, [year], company_names, output_base,
                skip_existing=skip_existing
            )

            if result["quarters_processed"]:
                progress["completed"][company_code] = {
                    "quarters": result["quarters_processed"],
                    "timestamp": datetime.now().isoformat()
                }
            elif result["errors"]:
                progress["failed"][company_code] = {
                    "errors": result["errors"],
                    "timestamp": datetime.now().isoformat()
                }

            # 50社ごとにプログレス保存
            if (i + 1) % 50 == 0:
                save_progress(progress_file, progress)
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed * 60
                logger.info(f"\n📈 進捗: {i+1}/{len(remaining)} ({rate:.0f}社/分)")

        except Exception as e:
            progress["failed"][company_code] = {
                "errors": [str(e)],
                "timestamp": datetime.now().isoformat()
            }
            logger.error(f"❌ {company_code}: {e}")

    # 最終保存
    save_progress(progress_file, progress)

    elapsed = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"四半期バッチ完了: {year}年度")
    logger.info(f"  完了: {len(progress['completed'])}社")
    logger.info(f"  失敗: {len(progress['failed'])}社")
    logger.info(f"  所要時間: {elapsed/60:.1f}分")
    logger.info(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description='四半期報告書バッチ処理')
    parser.add_argument('--year', '-y', help='処理する年度（フォルダ年度）')
    parser.add_argument('--all', action='store_true', help='全年度 (2020-2024)')
    parser.add_argument('--resume', action='store_true', help='中断再開')
    parser.add_argument('--test', action='store_true', help='テスト（3社のみ）')
    parser.add_argument('--no-skip-existing', action='store_true',
                        help='既存ファイルを上書き（デフォルトはスキップ）')

    args = parser.parse_args()

    if args.all:
        years = ['2020', '2021', '2022', '2023', '2024']
        for year in years:
            run_batch(year, resume=args.resume, test=args.test,
                      skip_existing=not args.no_skip_existing)
    elif args.year:
        run_batch(args.year, resume=args.resume, test=args.test,
                  skip_existing=not args.no_skip_existing)
    else:
        parser.error("--year または --all を指定してください")


if __name__ == "__main__":
    main()
