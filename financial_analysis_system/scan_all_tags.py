#!/usr/bin/env python3
"""
全raw_tagsスキャン: 未マッピング高頻度タグを洗い出し、
B/S項目ごとにグルーピングして「その他」を減らすための包括的調査。
"""
import json
import os
import sys
import re
from pathlib import Path
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8')

STORE_DIR = Path(r'c:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store')

# === 現在マッピング済みのタグ（patch_comprehensive_tags.py + xbrl_batch_extractor.py から） ===
ALREADY_MAPPED_FIELDS = {
    # P/L
    'revenue', 'cost_of_sales', 'gross_profit', 'sga_expense', 'selling_general_admin',
    'operating_income', 'ordinary_income', 'ebitda', 'income_before_tax',
    'income_taxes', 'net_income', 'comprehensive_income', 'non_controlling_profit',
    'extraordinary_income', 'extraordinary_loss',
    # B/S Assets
    'total_assets', 'current_assets', 'cash_and_deposits',
    'trade_receivables', 'notes_receivable', 'accounts_receivable',
    'inventories', 'merchandise', 'work_in_progress', 'raw_materials', 'supplies',
    'other_current_assets', 'financial_assets_current', 'allowance_doubtful_accounts_ca',
    'fixed_assets', 'property_plant_equipment', 'buildings', 'land',
    'machinery_equipment_net', 'tools_furniture_net', 'vessels_net', 'vehicles_net',
    'lease_assets_ppe_net', 'construction_in_progress', 'other_ppe_net',
    'intangible_assets', 'goodwill', 'software', 'other_intangible_assets',
    'investments_and_other_assets', 'investments', 'investment_securities',
    'subsidiary_stocks', 'long_term_loans_receivable', 'retirement_benefit_assets',
    'other_investments_other_assets', 'allowance_doubtful_accounts_investments',
    'right_of_use_assets', 'deferred_tax_assets', 'financial_assets_non_current',
    'other_non_current_assets',
    # B/S Liabilities
    'total_liabilities', 'current_liabilities', 'trade_payables', 'notes_payable',
    'short_term_loans', 'current_portion_long_term', 'commercial_paper',
    'lease_obligations_current', 'accounts_payable_other', 'advances_received',
    'income_taxes_payable', 'accrued_expenses', 'provision_bonuses',
    'other_current_liabilities', 'financial_liabilities_current',
    'fixed_liabilities', 'bonds_payable', 'long_term_loans',
    'lease_obligations_non_current', 'warranty_liability',
    'retirement_benefit_liability', 'deferred_tax_liabilities',
    'other_non_current_liabilities', 'financial_liabilities_non_current',
    'interest_bearing_debt',
    # B/S Equity
    'net_assets', 'shareholders_equity', 'capital_stock', 'capital_surplus',
    'retained_earnings', 'treasury_stock', 'accumulated_other_comprehensive',
    'valuation_adjustments', 'non_controlling_interests', 'total_equity',
    # CF
    'operating_cf', 'investing_cf', 'financing_cf', 'free_cash_flow',
    'cash_end_of_period', 'dividends_paid', 'depreciation_cf',
    'depreciation_amortization_cf', 'impairment_loss_cf',
    'equity_method_earnings_cf', 'decrease_increase_receivables_cf',
    'decrease_increase_inventories_cf', 'increase_decrease_payables_cf',
    'subtotal_operating_cf', 'interest_paid_cf', 'interest_received_cf',
    'dividends_received_cf', 'income_taxes_paid_cf',
    'purchase_ppe', 'purchase_intangibles', 'purchase_investments',
    'proceeds_sales_ppe_cf', 'proceeds_sales_investments_cf',
    'purchase_subsidiary_shares_cf',
    'proceeds_borrowings', 'repayments_borrowings',
    'proceeds_long_term_loans_cf', 'repayment_long_term_loans_cf',
    'net_change_short_term_loans_cf', 'proceeds_bonds_cf', 'redemption_bonds_cf',
    'dividends_paid_nci_cf', 'repayment_lease_obligations_cf',
    'effect_exchange_rate_cf',
    # Other
    'shares_issued', 'treasury_shares', 'employee_count',
    'financial_institutions_pct', 'foreign_corporations_pct', 'individuals_pct',
    'domestic_corporations_pct', 'treasury_pct',
}

# B/S関連タグを判別するキーワード
BS_ASSET_KEYWORDS = [
    'Asset', 'Cash', 'Deposit', 'Receivable', 'Inventory', 'Securities',
    'Property', 'Plant', 'Equipment', 'Building', 'Land', 'Machinery',
    'Goodwill', 'Software', 'Intangible', 'Investment', 'Loan', 'Deferred',
    'Prepaid', 'RealEstate', 'Construction', 'Lease', 'RightOfUse',
    'Merchandise', 'FinishedGoods', 'WorkInProcess', 'RawMaterial', 'Supply',
    'Vehicle', 'Tool', 'Furniture', 'Vessel', 'Allowance',
]
BS_LIABILITY_KEYWORDS = [
    'Liabilit', 'Payable', 'Borrowing', 'Loan', 'Bond', 'Debt',
    'Provision', 'Reserve', 'Accrued', 'Advance', 'Deposit', 'Tax',
    'Retirement', 'Pension', 'Warrant', 'Lease', 'Commercial',
    'Income.*Tax', 'Deferred.*Tax',
]
BS_EQUITY_KEYWORDS = [
    'Equity', 'Capital', 'Surplus', 'Retained', 'Treasury', 'Comprehensive',
    'NetAsset', 'Shareholder', 'NonControlling', 'Valuation',
]

def classify_tag(tag_name):
    """タグ名からB/Sカテゴリを推定"""
    # 明らかにB/S以外のものを除外
    if any(kw in tag_name for kw in ['TextBlock', 'Axis', 'Member', 'Domain', 'LineItems', 'Table', 'Abstract', 'Heading']):
        return 'meta'
    if any(kw in tag_name for kw in ['DEI', 'FilingDate', 'FiscalYear', 'Report', 'Audit', 'AccountingStandard']):
        return 'dei'
    if 'CashFlow' in tag_name or tag_name.endswith('CF') or 'FinCF' in tag_name or 'InvCF' in tag_name or 'OpeCF' in tag_name:
        return 'cf'
    if any(kw in tag_name for kw in ['Revenue', 'Sales', 'Cost', 'Profit', 'Loss', 'Income', 'Expense', 'Margin', 'EBITDA']):
        if 'Tax' not in tag_name and 'Payable' not in tag_name and 'Receivable' not in tag_name:
            return 'pl'

    # B/S分類
    tag_lower = tag_name.lower()
    for kw in BS_EQUITY_KEYWORDS:
        if kw.lower() in tag_lower:
            return 'bs_equity'
    for kw in BS_LIABILITY_KEYWORDS:
        if kw.lower() in tag_lower:
            return 'bs_liability'
    for kw in BS_ASSET_KEYWORDS:
        if kw.lower() in tag_lower:
            return 'bs_asset'

    return 'other'


def main():
    raw_files = sorted(STORE_DIR.glob('*/*_raw_tags.json'))
    print(f"スキャン対象: {len(raw_files)} raw_tags files")

    # タグ名 → 出現企業数
    tag_company_count = Counter()
    # タグ名 → 値がnumericな企業数
    tag_numeric_count = Counter()
    # タグ名 → サンプル値（最大5つ）
    tag_samples = defaultdict(list)
    # 企業ごとに1回だけカウント
    company_tags = defaultdict(set)

    for i, rf in enumerate(raw_files):
        if (i + 1) % 5000 == 0:
            print(f"  進捗: {i+1}/{len(raw_files)}")

        company_dir = rf.parent.name
        try:
            with open(rf, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            tags = raw.get('tags', {})
        except:
            continue

        for tag_name, td in tags.items():
            if tag_name not in company_tags[company_dir]:
                company_tags[company_dir].add(tag_name)
                tag_company_count[tag_name] += 1

            val = td.get('value') if isinstance(td, dict) else None
            if isinstance(val, (int, float)) and val != 0:
                tag_numeric_count[tag_name] += 1
                if len(tag_samples[tag_name]) < 3:
                    tag_samples[tag_name].append((company_dir, val))

    total_companies = len(company_tags)
    print(f"\n総企業数: {total_companies}")
    print(f"総ユニークタグ数: {len(tag_company_count)}")

    # マッピング済みタグを特定するため、patch_comprehensive_tags.pyのタグ名を読み込む
    mapped_xbrl_tags = set()
    patch_file = STORE_DIR.parent / 'patch_comprehensive_tags.py'
    if patch_file.exists():
        with open(patch_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # シングルクォートで囲まれたタグ名を抽出
        mapped_xbrl_tags = set(re.findall(r"'([A-Z][A-Za-z]+)'", content))

    shareholder_patch = STORE_DIR.parent / 'patch_shareholder_data.py'
    if shareholder_patch.exists():
        with open(shareholder_patch, 'r', encoding='utf-8') as f:
            content = f.read()
        mapped_xbrl_tags |= set(re.findall(r"'([A-Z][A-Za-z]+)'", content))

    batch_extractor = STORE_DIR.parent / 'xbrl_batch_extractor.py'
    if batch_extractor.exists():
        with open(batch_extractor, 'r', encoding='utf-8') as f:
            content = f.read()
        mapped_xbrl_tags |= set(re.findall(r"'([A-Z][A-Za-z]+)'", content))

    print(f"既知マッピングタグ数: {len(mapped_xbrl_tags)}")

    # 未マッピングかつnumeric値を持つ高頻度タグを抽出
    unmapped = []
    for tag_name, count in tag_company_count.most_common():
        if tag_name in mapped_xbrl_tags:
            continue
        numeric = tag_numeric_count.get(tag_name, 0)
        if numeric < 50:  # 50社未満は無視
            continue
        category = classify_tag(tag_name)
        if category in ('meta', 'dei'):
            continue
        unmapped.append({
            'tag': tag_name,
            'companies': count,
            'numeric': numeric,
            'pct': round(100 * count / total_companies, 1),
            'category': category,
            'samples': tag_samples.get(tag_name, []),
        })

    # カテゴリごとにソート表示
    print("\n" + "=" * 120)
    print("未マッピング高頻度タグ（50社以上、numeric値あり）")
    print("=" * 120)

    categories = ['bs_asset', 'bs_liability', 'bs_equity', 'pl', 'cf', 'other']
    cat_labels = {
        'bs_asset': '【B/S 資産】',
        'bs_liability': '【B/S 負債】',
        'bs_equity': '【B/S 純資産】',
        'pl': '【P/L】',
        'cf': '【C/F】',
        'other': '【その他/未分類】',
    }

    results = {}
    for cat in categories:
        items = [x for x in unmapped if x['category'] == cat]
        items.sort(key=lambda x: -x['numeric'])
        results[cat] = items
        if not items:
            continue
        print(f"\n{cat_labels[cat]} ({len(items)} tags)")
        print("-" * 120)
        for item in items[:80]:  # 上位80個
            samples_str = ', '.join(f"{c}={v}" for c, v in item['samples'][:2])
            print(f"  {item['tag']:<70s} 企業:{item['companies']:>5d} ({item['pct']:>5.1f}%)  numeric:{item['numeric']:>5d}  例: {samples_str}")

    # JSON出力
    output = {cat: results[cat] for cat in categories}
    out_file = STORE_DIR.parent / 'unmapped_tags_report.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n詳細レポート: {out_file}")


if __name__ == '__main__':
    main()
