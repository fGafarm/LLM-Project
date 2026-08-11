#!/usr/bin/env python3
"""
Nikkei 225 Full Extraction and Validation Script
日経225全社のXBRL抽出・検証スクリプト
"""

import json
import sys
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import re

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Import local modules
sys.path.insert(0, str(Path(__file__).parent))
from xbrl_batch_extractor import (
    extract_xbrl_from_zip, save_xbrl_json,
    Config, scan_all_zips
)
from xbrl_validator import FinancialStatementValidator, TagLearningManager


def load_nikkei225_codes():
    """日経225銘柄コードを読み込み"""
    codes_file = Path(__file__).parent / "nikkei225_codes.txt"
    with open(codes_file, 'r') as f:
        return [line.strip() for line in f if line.strip()]


def find_annual_report_zip(company_code: str, year: str, base_path: Path) -> Path:
    """
    指定企業・年度の有価証券報告書ZIPを検索
    有報を優先、なければ訂正有報
    """
    # 有報フォルダを検索
    yuho_folder = base_path / year / "有報"
    if yuho_folder.exists():
        # 企業コードで始まるZIPを検索
        for zip_path in yuho_folder.glob(f"{company_code}_*.zip"):
            # 有報（訂正でない）を優先
            if "訂正" not in zip_path.name:
                return zip_path

        # 訂正有報でもOK
        for zip_path in yuho_folder.glob(f"{company_code}_*.zip"):
            return zip_path

    return None


def extract_company(company_code: str, year: str, output_base: Path,
                   validator: FinancialStatementValidator,
                   learner: TagLearningManager) -> dict:
    """単一企業の抽出・検証"""
    result = {
        "company_code": company_code,
        "year": year,
        "success": False,
        "score": 0,
        "errors": [],
        "warnings": []
    }

    # ZIPファイルを検索
    zip_path = find_annual_report_zip(company_code, year, Config.XBRL_BASE)

    if not zip_path:
        result["errors"].append("ZIP not found")
        return result

    # 企業名を取得（ファイル名から）
    parts = zip_path.stem.split('_')
    company_name = parts[1] if len(parts) >= 2 else "Unknown"

    try:
        # XBRL抽出
        xbrl_data = extract_xbrl_from_zip(zip_path)

        if not xbrl_data:
            result["errors"].append("XBRL extraction failed")
            return result

        # 保存（検証付き）
        year_file, validation_report, new_tags = save_xbrl_json(
            company_code, company_name, year,
            xbrl_data, zip_path.name, output_base,
            validator, learner
        )

        result["success"] = True
        result["company_name"] = company_name
        result["file"] = str(year_file)

        if validation_report:
            result["score"] = validation_report.get('overall_score', 0)
            result["errors"] = validation_report.get('errors', [])
            result["warnings"] = validation_report.get('warnings', [])

    except Exception as e:
        result["errors"].append(str(e))

    return result


def main():
    """メイン処理"""
    print("=" * 70)
    print("Nikkei 225 Full Extraction and Validation")
    print("=" * 70)

    # 設定
    year = "2022"  # 2022年度を対象
    output_base = Path("xbrl_store")
    output_base.mkdir(exist_ok=True)

    # 日経225銘柄コードを読み込み
    nikkei225_codes = load_nikkei225_codes()
    print(f"\nTarget: {len(nikkei225_codes)} Nikkei 225 companies")
    print(f"Year: {year}")

    # バリデーターと学習マネージャーを初期化
    validator = FinancialStatementValidator(tolerance=0.0)
    learner = TagLearningManager()

    # 結果を格納
    results = []
    success_count = 0
    perfect_count = 0

    # 進捗表示用
    total = len(nikkei225_codes)

    print(f"\nStarting extraction...")
    print("-" * 70)

    for i, code in enumerate(nikkei225_codes, 1):
        # 進捗表示
        if i % 10 == 0 or i == total:
            print(f"Progress: {i}/{total} ({i*100//total}%)")

        result = extract_company(code, year, output_base, validator, learner)
        results.append(result)

        if result["success"]:
            success_count += 1
            if result["score"] == 100:
                perfect_count += 1

            # 非100%企業をログ
            if result["score"] < 100:
                logger.info(f"  [{code}] {result.get('company_name', 'Unknown')}: {result['score']}%")
        else:
            logger.warning(f"  [{code}] Failed: {result['errors']}")

    print("-" * 70)

    # サマリー
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total companies: {total}")
    print(f"Successful extraction: {success_count}")
    print(f"Failed extraction: {total - success_count}")
    print(f"Perfect validation (100%): {perfect_count}")

    # 検証スコア別の分布
    score_dist = defaultdict(list)
    for r in results:
        if r["success"]:
            score_bucket = int(r["score"] // 10) * 10
            score_dist[score_bucket].append(r)

    print("\nScore Distribution:")
    for bucket in sorted(score_dist.keys(), reverse=True):
        count = len(score_dist[bucket])
        print(f"  {bucket}-{bucket+9}%: {count} companies")

    # 平均スコア
    scores = [r["score"] for r in results if r["success"]]
    if scores:
        avg_score = sum(scores) / len(scores)
        print(f"\nAverage Score: {avg_score:.1f}%")

    # 非100%企業の詳細を保存
    non_perfect = [r for r in results if r["success"] and r["score"] < 100]
    if non_perfect:
        report_file = output_base / "nikkei225_validation_report.json"
        report = {
            "generated_at": datetime.now().isoformat(),
            "year": year,
            "total_companies": total,
            "successful": success_count,
            "perfect_100": perfect_count,
            "average_score": avg_score if scores else 0,
            "non_perfect_companies": non_perfect
        }
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nReport saved to: {report_file}")

    # 失敗企業リスト
    failed = [r for r in results if not r["success"]]
    if failed:
        print(f"\nFailed companies ({len(failed)}):")
        for r in failed[:20]:  # 最初の20件
            print(f"  {r['company_code']}: {r['errors']}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")

    print("\n" + "=" * 70)
    print("Extraction completed")
    print("=" * 70)

    return results


if __name__ == "__main__":
    main()
