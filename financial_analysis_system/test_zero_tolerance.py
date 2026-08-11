#!/usr/bin/env python3
"""
0%許容検証テスト

XBRLから取得した財務三表が0%誤差であることを検証し、
差異がある場合はraw_tagsから正しいタグを探して学習する
"""

import json
import logging
from pathlib import Path
from xbrl_validator import (
    FinancialStatementValidator,
    TagLearningManager,
    validate_and_learn,
    load_raw_tags,
    revalidate_with_learning,
    XBRL_STORE_DIR,
    LEARNED_TAGS_FILE
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# テスト対象企業
TEST_COMPANIES = [
    ("7203", "トヨタ自動車株式会社"),
    ("6758", "ソニーグループ株式会社"),
    ("6501", "株式会社日立製作所"),
    ("2914", "日本たばこ産業株式会社"),
    ("9984", "ソフトバンクグループ株式会社"),
    ("9983", "株式会社ファーストリテイリング"),
    ("7974", "任天堂株式会社"),
    ("4063", "信越化学工業株式会社"),
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),
    ("1301", "株式会社　極洋"),
]

TEST_YEAR = "2022"


def test_single_company(company_code: str, company_name: str, year: str):
    """単一企業の検証テスト"""
    logger.info(f"\n{'='*60}")
    logger.info(f"検証: {company_code} {company_name} ({year}年)")
    logger.info(f"{'='*60}")

    # データファイルを探す
    company_dir = None
    for d in XBRL_STORE_DIR.iterdir():
        if d.is_dir() and d.name.startswith(f"{company_code}_"):
            company_dir = d
            break

    if not company_dir:
        logger.warning(f"  ❌ 企業ディレクトリが見つかりません")
        return None

    data_file = company_dir / f"{year}.json"
    raw_tags_file = company_dir / f"{year}_raw_tags.json"

    if not data_file.exists():
        logger.warning(f"  ❌ データファイルが見つかりません: {data_file}")
        return None

    if not raw_tags_file.exists():
        logger.warning(f"  ❌ raw_tagsファイルが見つかりません: {raw_tags_file}")
        return None

    # データ読み込み
    with open(data_file, 'r', encoding='utf-8') as f:
        stored_data = json.load(f)

    with open(raw_tags_file, 'r', encoding='utf-8') as f:
        raw_tags_data = json.load(f)

    xbrl_data = stored_data.get('data', {})
    raw_tags = raw_tags_data.get('tags', {})

    logger.info(f"  📊 データ項目数: {len(xbrl_data)}")
    logger.info(f"  📋 raw_tags数: {len(raw_tags)}")

    # 検証と学習を実行
    validation_report, new_tags = validate_and_learn(
        xbrl_data, raw_tags,
        company_code, company_name, year
    )

    # 結果表示
    score = validation_report.get('overall_score', 0)
    errors = validation_report.get('errors', [])
    warnings = validation_report.get('warnings', [])
    auto_fixes = validation_report.get('auto_fixes', {})

    logger.info(f"\n  📈 検証スコア: {score}%")

    if auto_fixes:
        logger.info(f"  🔧 自動修正: {sum(len(v) if isinstance(v, dict) else 0 for v in auto_fixes.values())}項目")
        for category, fixes in auto_fixes.items():
            if isinstance(fixes, dict):
                for field, fix_info in fixes.items():
                    logger.info(f"    - {field}: {fix_info.get('tag', 'N/A')}")

    if errors:
        logger.warning(f"  ❌ エラー: {len(errors)}")
        for err in errors[:5]:
            logger.warning(f"    - {err}")

    if warnings:
        logger.info(f"  ⚠️ 警告: {len(warnings)}")

    # 結果を保存
    stored_data['data'] = xbrl_data
    stored_data['validation'] = validation_report

    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(stored_data, f, ensure_ascii=False, indent=2)

    return {
        'company_code': company_code,
        'company_name': company_name,
        'year': year,
        'score': score,
        'errors': len(errors),
        'warnings': len(warnings),
        'auto_fixes': sum(len(v) if isinstance(v, dict) else 0 for v in auto_fixes.values()),
        'new_tags': len(new_tags) if new_tags else 0
    }


def main():
    """全社テスト実行"""
    logger.info("="*60)
    logger.info("0%許容検証テスト開始")
    logger.info("="*60)
    logger.info(f"対象企業数: {len(TEST_COMPANIES)}")
    logger.info(f"対象年度: {TEST_YEAR}")
    logger.info(f"学習ファイル: {LEARNED_TAGS_FILE}")

    results = []

    for company_code, company_name in TEST_COMPANIES:
        result = test_single_company(company_code, company_name, TEST_YEAR)
        if result:
            results.append(result)

    # サマリー表示
    logger.info("\n" + "="*60)
    logger.info("検証結果サマリー")
    logger.info("="*60)

    total_score = 0
    total_errors = 0
    total_fixes = 0

    for r in results:
        status = "✅" if r['score'] >= 100 else "⚠️" if r['score'] >= 80 else "❌"
        logger.info(f"{status} {r['company_code']} {r['company_name'][:10]}... "
                   f"スコア:{r['score']}% エラー:{r['errors']} 修正:{r['auto_fixes']}")
        total_score += r['score']
        total_errors += r['errors']
        total_fixes += r['auto_fixes']

    if results:
        avg_score = total_score / len(results)
        logger.info(f"\n平均スコア: {avg_score:.1f}%")
        logger.info(f"総エラー数: {total_errors}")
        logger.info(f"総自動修正数: {total_fixes}")

    # 学習済みタグの確認
    learner = TagLearningManager()
    learned_count = len(learner.learned_tags)
    logger.info(f"\n学習済みタグ数: {learned_count}")

    if learned_count > 0:
        logger.info("学習済みタグ（直近10件）:")
        for i, (key, tag) in enumerate(list(learner.learned_tags.items())[:10]):
            logger.info(f"  {i+1}. {tag.suggested_field} ← {tag.tag_name} ({tag.company_count}社)")


if __name__ == "__main__":
    main()
