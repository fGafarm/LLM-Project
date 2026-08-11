#!/usr/bin/env python3
"""
xbrl_storeに全B/S・CF詳細項目を追加するパッチスクリプト。
raw_tagsから直接マッピングして、xbrl_store JSONを更新する。
"""

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

STORE_DIR = Path(r'c:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store')

# ==========================================
# 新規追加フィールドのタグマッピング
# key = xbrl_store field name
# value = list of (tag_name, priority) - lower priority = preferred
# ==========================================

NEW_TAGS = {
    # ===== B/S 有形固定資産の内訳 =====
    'machinery_equipment_net': [
        'MachineryEquipmentAndVehiclesNet',
        'MachineryAndEquipmentNet',
    ],
    'vessels_net': [
        'VesselsNet',
    ],
    'lease_assets_ppe_net': [
        'LeaseAssetsNetPPE',
        'LeaseAssetsNet',
    ],
    'construction_in_progress': [
        'ConstructionInProgress',
        'ConstructionInProgressPPE',
    ],
    'other_ppe_net': [
        'OtherNetPPE',
        'OtherPropertyPlantAndEquipmentNet',
    ],

    # ===== B/S 無形固定資産の内訳 =====
    'software': [
        'Software',
        'SoftwareIA',
    ],
    'other_intangible_assets': [
        'OtherIA',
        'OtherIntangibleAssets',
    ],

    # ===== B/S 投資その他の資産の内訳 =====
    'investment_securities': [
        'InvestmentSecurities',
        'InvestmentSecuritiesIOA',
    ],
    'investments_and_other_assets': [
        'InvestmentsAndOtherAssets',
    ],
    'subsidiary_stocks': [
        'StocksOfSubsidiariesAndAffiliates',
        'InvestmentsAccountedForUsingEquityMethod',
    ],
    'long_term_loans_receivable': [
        'LongTermLoansReceivable',
        'LongTermLoansReceivableIOA',
    ],
    'retirement_benefit_assets': [
        'NetDefinedBenefitAsset',
        'RetirementBenefitAsset',
    ],
    'other_investments_other_assets': [
        'OtherIOA',
        'OtherInvestmentsAndOtherAssets',
    ],
    'allowance_doubtful_accounts_investments': [
        'AllowanceForDoubtfulAccountsIOAByGroup',
        'AllowanceForDoubtfulAccountsIOA',
    ],

    # ===== B/S 流動資産追加 =====
    'allowance_doubtful_accounts_ca': [
        'AllowanceForDoubtfulAccountsCA',
        'AllowanceForDoubtfulAccountsCurrentAssets',
    ],

    # ===== B/S 流動負債の内訳 =====
    'lease_obligations_current': [
        'LeaseObligationsCL',
        'LeaseObligationsCurrentLiabilities',
    ],
    'provision_bonuses': [
        'ProvisionForBonuses',
        'ProvisionForBonusesCL',
    ],
    'provision_directors_bonuses': [
        'ProvisionForDirectorsBonuses',
        'ProvisionForDirectorsBonusesCL',
    ],
    'accounts_payable_other': [
        'AccountsPayableOther',
        'AccountsPayableOtherCL',
    ],
    'advances_received': [
        'AdvancesReceived',
        'AdvancesReceivedCL',
        'ContractLiabilitiesCL',
    ],
    'provision_loss_litigation_cl': [
        'ProvisionForLossOnLitigationCL',
    ],
    'commercial_paper': [
        'CommercialPapersLiabilities',
        'CommercialPapersCL',
    ],
    'other_cl': [
        'OtherCL',
        'OtherCurrentLiabilitiesOther',
    ],

    # ===== B/S 固定負債の内訳 =====
    'lease_obligations_non_current': [
        'LeaseObligationsNCL',
        'LeaseObligationsNoncurrentLiabilities',
    ],
    'bonds_payable': [
        'BondsPayable',
        'BondsPayableNCL',
    ],
    'provision_share_based_ncl': [
        'ProvisionForShareBasedRemunerationForDirectorsAndOtherOfficersNCL',
    ],
    'other_ncl': [
        'OtherNCL',
        'OtherNoncurrentLiabilitiesOther',
    ],

    # ===== C/F 営業活動の詳細 =====
    'depreciation_amortization_cf': [
        'DepreciationAndAmortizationOpeCF',
        'DepreciationAndAmortizationSGA',
    ],
    'impairment_loss_cf': [
        'ImpairmentLossOpeCF',
        'ImpairmentLossOnNonCurrentAssetsOpeCF',
    ],
    'equity_method_earnings_cf': [
        'EquityInEarningsLossesOfAffiliatesOpeCF',
        'ShareOfProfitLossOfInvestmentsAccountedForUsingEquityMethodOpeCF',
    ],
    'decrease_increase_receivables_cf': [
        'DecreaseIncreaseInNotesAndAccountsReceivableTradeOpeCF',
        'DecreaseIncreaseInTradeReceivablesOpeCF',
    ],
    'decrease_increase_inventories_cf': [
        'DecreaseIncreaseInInventoriesOpeCF',
    ],
    'increase_decrease_payables_cf': [
        'IncreaseDecreaseInNotesAndAccountsPayableTradeOpeCF',
        'IncreaseDecreaseInTradePayablesOpeCF',
    ],
    'subtotal_operating_cf': [
        'SubtotalOpeCF',
    ],

    # ===== C/F 投資活動の詳細 =====
    'proceeds_sales_ppe_cf': [
        'ProceedsFromSalesOfPropertyPlantAndEquipmentInvCF',
        'ProceedsFromSalesOfPropertyPlantAndEquipmentAndIntangibleAssetsInvCF',
    ],
    'proceeds_sales_investments_cf': [
        'ProceedsFromSalesOfInvestmentSecuritiesInvCF',
    ],
    'purchase_subsidiary_shares_cf': [
        'PurchaseOfStocksOfSubsidiariesAndAffiliatesInvCF',
        'PurchaseOfInvestmentsInSubsidiariesResultingInChangeInScopeOfConsolidationInvCF',
    ],

    # ===== C/F 財務活動の詳細 =====
    'dividends_paid_nci_cf': [
        'DividendsPaidToNonControllingInterestsFinCF',
    ],
    'repayment_lease_obligations_cf': [
        'RepaymentsOfLeaseObligationsFinCF',
        'PaymentsOfLeaseObligationsFinCF',
    ],
    'proceeds_long_term_loans_cf': [
        'ProceedsFromLongTermLoansPayableFinCF',
    ],
    'repayment_long_term_loans_cf': [
        'RepaymentOfLongTermLoansPayableFinCF',
    ],
    'net_change_short_term_loans_cf': [
        'NetIncreaseDecreaseInShortTermLoansPayableFinCF',
    ],
    'proceeds_bonds_cf': [
        'ProceedsFromIssuanceOfBondsFinCF',
    ],
    'redemption_bonds_cf': [
        'RedemptionOfBondsFinCF',
    ],
    'effect_exchange_rate_cf': [
        'EffectOfExchangeRateChangeOnCashAndCashEquivalents',
        'EffectOfExchangeRateChangesOnCashAndCashEquivalentsIFRS',
    ],

    # ===== P/L 追加 =====
    'non_controlling_profit': [
        'ProfitLossAttributableToNonControllingInterests',
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

    for field, tag_candidates in NEW_TAGS.items():
        # 既にデータがある場合はスキップ（既存データを保護）
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
    print(f"全B/S・CFパッチ: {len(raw_files)} ファイルを処理")

    updated = 0
    errors = 0

    for rf in raw_files:
        year = rf.stem.split('_')[0]
        store_file = rf.parent / f'{year}.json'
        if not store_file.exists():
            continue

        try:
            if patch_store_file(store_file, rf):
                updated += 1
        except Exception as e:
            print(f"  ERROR {rf}: {e}")
            errors += 1

    print(f"\nDone! Updated: {updated}, Errors: {errors}, Total: {len(raw_files)}")


if __name__ == '__main__':
    main()
