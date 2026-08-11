#!/usr/bin/env python3
"""
XBRL自己学習パッチ: raw_tagsから株主構成データをxbrl_storeに追加。
サーベイで100%の企業に存在が確認された株主構成タグをマッピング。
"""

import json
import os
import sys
import glob
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

STORE_DIR = Path(r'c:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store')

# ==========================================
# 株主構成タグマッピング
# ==========================================
SHAREHOLDER_TAGS = {
    # 株主構成比率（%）
    'financial_institutions_pct': [
        'PercentageOfShareholdingsFinancialInstitutions',
        'PercentageOfTotalShareholdingFinancialInstitutions',
    ],
    'foreign_corporations_pct': [
        'PercentageOfShareholdingsForeignCorporationsEtc',
        'PercentageOfShareholdingsForeignNationalsEtc',
        'PercentageOfTotalShareholdingForeignCorporationsEtc',
    ],
    'individuals_pct': [
        'PercentageOfShareholdingsIndividualsAndOthers',
        'PercentageOfTotalShareholdingIndividualsAndOthers',
    ],
    'domestic_corporations_pct': [
        'PercentageOfShareholdingsOtherCorporations',
        'PercentageOfTotalShareholdingOtherCorporations',
    ],
    'treasury_pct': [
        'PercentageOfShareholdingsTreasuryStock',
        'PercentageOfTotalShareholdingTreasuryStock',
    ],
    'financial_services_pct': [
        'PercentageOfShareholdingsFinancialInstrumentsBusinessOperators',
        'PercentageOfShareholdingsFinancialServiceProviders',
    ],

    # 株式数
    'shares_held_financial_institutions': [
        'NumberOfSharesHeldFinancialInstitutions',
    ],
    'shares_held_foreign': [
        'NumberOfSharesHeldForeignCorporationsEtc',
        'NumberOfSharesHeldForeignNationalsEtc',
    ],
    'shares_held_individuals': [
        'NumberOfSharesHeldIndividualsAndOthers',
    ],
    'shares_held_domestic_corps': [
        'NumberOfSharesHeldOtherCorporations',
    ],
    'shares_held_treasury': [
        'NumberOfSharesHeldTreasuryStock',
    ],

    # 株主数
    'num_shareholders_total': [
        'NumberOfShareholdersTotal',
    ],
    'num_shareholders_financial': [
        'NumberOfShareholdersFinancialInstitutions',
    ],
    'num_shareholders_foreign': [
        'NumberOfShareholdersForeignCorporationsEtc',
        'NumberOfShareholdersForeignNationalsEtc',
    ],
    'num_shareholders_individuals': [
        'NumberOfShareholdersIndividualsAndOthers',
    ],

    # 発行済株式数・議決権
    'shares_issued': [
        'TotalNumberOfIssuedShares',
        'TotalNumberOfIssuedSharesEndOfFiscalYearIncludingTreasuryStockDEI',
    ],
    'treasury_shares': [
        'TotalNumberOfTreasurySharesEndOfFiscalYearDEI',
        'NumberOfSharesHeldTreasuryStock',
    ],
    'shares_with_voting_rights': [
        'NumberOfSharesIssuedSharesVotingRights',
    ],

    # 従業員数
    'employee_count': [
        'NumberOfEmployees',
        'NumberOfEmployeesDEI',
    ],

    # 監査報酬
    'audit_fee_company': [
        'AuditFeesReportingCompany',
    ],
    'audit_fee_total': [
        'AuditFeesTotal',
    ],
}


def patch_store_file(store_file: Path, raw_tags_file: Path) -> bool:
    """1年分のデータを修正"""
    with open(store_file, 'r', encoding='utf-8') as f:
        store = json.load(f)
    data = store.get('data', {})

    with open(raw_tags_file, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    tags = raw.get('tags', {})

    changed = False

    for field, tag_candidates in SHAREHOLDER_TAGS.items():
        # 既にデータがある場合はスキップ
        if field in data and data[field] is not None:
            continue

        for tag_name in tag_candidates:
            if tag_name in tags:
                td = tags[tag_name]
                val = td.get('value') if isinstance(td, dict) else None
                if isinstance(val, (int, float)):
                    data[field] = val
                    changed = True
                    break

    if changed:
        store['data'] = data
        with open(store_file, 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    return changed


def main():
    raw_files = sorted(STORE_DIR.glob('*/*_raw_tags.json'))
    print(f"株主構成パッチ: {len(raw_files)} ファイルを処理")

    updated = 0
    errors = 0

    for i, rf in enumerate(raw_files):
        if (i + 1) % 5000 == 0:
            print(f"  進捗: {i+1}/{len(raw_files)} (更新:{updated})")

        year = rf.stem.split('_')[0]
        store_file = rf.parent / f'{year}.json'
        if not store_file.exists():
            continue

        try:
            if patch_store_file(store_file, rf):
                updated += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ERROR {rf}: {e}")

    print(f"\nDone! Updated: {updated}, Errors: {errors}, Total: {len(raw_files)}")


if __name__ == '__main__':
    main()
