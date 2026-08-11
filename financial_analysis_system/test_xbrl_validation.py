#!/usr/bin/env python3
"""
XBRL検証・学習機能のテストスクリプト

既存のxbrl_storeのデータを使って検証機能をテストします。
"""

import json
import sys
import io
from pathlib import Path

# Windows文字コード対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# プロジェクトパスを追加
sys.path.insert(0, str(Path(__file__).parent))

from xbrl_validator import (
    FinancialStatementValidator,
    TagLearningManager,
    validate_and_learn
)

def test_single_company(json_path: Path, raw_tags_path: Path = None):
    """単一企業のデータを検証"""

    print(f"\n{'='*60}")
    print(f"テスト: {json_path.name}")
    print(f"{'='*60}")

    # データ読み込み
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    xbrl_data = data.get('data', {})
    company_code = data.get('company_code', '')
    company_name = data.get('company_name', '')
    fiscal_year = data.get('fiscal_year', '')

    print(f"\n企業: {company_code} {company_name}")
    print(f"年度: {fiscal_year}")
    print(f"データ項目数: {len(xbrl_data)}")

    # raw_tags読み込み（あれば）
    raw_tags = {}
    if raw_tags_path and raw_tags_path.exists():
        with open(raw_tags_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            raw_tags = raw_data.get('tags', {})
        print(f"生タグ数: {len(raw_tags)}")

    # 検証実行
    validator = FinancialStatementValidator()
    learner = TagLearningManager()

    validation_report, new_tags = validate_and_learn(
        xbrl_data, raw_tags, company_code, company_name, fiscal_year,
        validator, learner
    )

    # 結果表示
    print(f"\n--- 検証結果 ---")
    print(f"総合スコア: {validation_report['overall_score']}%")

    # BS検証
    print(f"\n[BS検証] {len(validation_report['bs_validations'])}項目")
    for v in validation_report['bs_validations']:
        status = "✅" if v['passed'] else "❌"
        print(f"  {status} {v['check_name']}")
        if v['difference_pct'] is not None:
            print(f"      差異: {v['difference_pct']:.2f}%")

    # PL検証
    print(f"\n[PL検証] {len(validation_report['pl_validations'])}項目")
    for v in validation_report['pl_validations']:
        status = "✅" if v['passed'] else "❌"
        print(f"  {status} {v['check_name']}")
        if v['difference_pct'] is not None:
            print(f"      差異: {v['difference_pct']:.2f}%")

    # CF検証
    print(f"\n[CF検証] {len(validation_report['cf_validations'])}項目")
    for v in validation_report['cf_validations']:
        status = "✅" if v['passed'] else "❌"
        print(f"  {status} {v['check_name']}")
        if v['difference_pct'] is not None:
            print(f"      差異: {v['difference_pct']:.2f}%")

    # 警告・エラー
    if validation_report['warnings']:
        print(f"\n⚠️ 警告: {len(validation_report['warnings'])}件")
        for w in validation_report['warnings'][:5]:
            print(f"  - {w}")

    if validation_report['errors']:
        print(f"\n❌ エラー: {len(validation_report['errors'])}件")
        for e in validation_report['errors'][:5]:
            print(f"  - {e}")

    if validation_report['missing_fields']:
        print(f"\n📋 不足フィールド: {len(validation_report['missing_fields'])}件")
        print(f"  {', '.join(validation_report['missing_fields'][:10])}")

    # 新タグ
    if new_tags:
        print(f"\n🆕 新タグ発見: {len(new_tags)}個")
        for tag in new_tags[:10]:
            print(f"  - {tag}")

    return validation_report


def test_all_companies(xbrl_store: Path):
    """全企業のデータを検証"""

    print(f"\n{'='*60}")
    print("全企業テスト開始")
    print(f"{'='*60}")

    results = []

    for company_dir in xbrl_store.iterdir():
        if not company_dir.is_dir():
            continue

        # 2022年のデータを検証
        json_file = company_dir / "2022.json"
        raw_tags_file = company_dir / "2022_raw_tags.json"

        if json_file.exists():
            try:
                report = test_single_company(json_file, raw_tags_file)
                results.append({
                    "company": company_dir.name,
                    "score": report['overall_score'],
                    "warnings": len(report['warnings']),
                    "errors": len(report['errors'])
                })
            except Exception as e:
                print(f"❌ エラー: {company_dir.name}: {e}")

    # サマリー
    print(f"\n{'='*60}")
    print("テストサマリー")
    print(f"{'='*60}")

    if results:
        avg_score = sum(r['score'] for r in results) / len(results)
        print(f"テスト企業数: {len(results)}")
        print(f"平均スコア: {avg_score:.1f}%")

        # スコア分布
        high = len([r for r in results if r['score'] >= 80])
        mid = len([r for r in results if 50 <= r['score'] < 80])
        low = len([r for r in results if r['score'] < 50])
        print(f"\nスコア分布:")
        print(f"  高 (≥80%): {high}社")
        print(f"  中 (50-79%): {mid}社")
        print(f"  低 (<50%): {low}社")

        # 詳細結果
        print(f"\n詳細:")
        for r in sorted(results, key=lambda x: x['score'], reverse=True):
            print(f"  {r['company'][:30]:<35} スコア:{r['score']:5.1f}% 警告:{r['warnings']:2} エラー:{r['errors']:2}")

    return results


def main():
    """メイン"""

    xbrl_store = Path("./xbrl_store")

    if not xbrl_store.exists():
        print("xbrl_storeディレクトリが見つかりません")
        return

    # 引数でテスト対象を指定
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--company', help='特定企業のディレクトリ名')
    parser.add_argument('--all', action='store_true', help='全企業テスト')
    args = parser.parse_args()

    if args.company:
        # 特定企業のテスト
        company_dir = xbrl_store / args.company
        if company_dir.exists():
            json_file = company_dir / "2022.json"
            raw_tags_file = company_dir / "2022_raw_tags.json"
            if json_file.exists():
                test_single_company(json_file, raw_tags_file)
            else:
                print(f"2022.jsonが見つかりません: {company_dir}")
        else:
            print(f"企業ディレクトリが見つかりません: {args.company}")

    elif args.all:
        # 全企業テスト
        test_all_companies(xbrl_store)

    else:
        # デフォルト: 最初の企業でテスト
        for company_dir in sorted(xbrl_store.iterdir()):
            if company_dir.is_dir():
                json_file = company_dir / "2022.json"
                raw_tags_file = company_dir / "2022_raw_tags.json"
                if json_file.exists():
                    test_single_company(json_file, raw_tags_file)
                    break


if __name__ == '__main__':
    main()
