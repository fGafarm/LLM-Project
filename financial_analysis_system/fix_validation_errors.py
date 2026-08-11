#!/usr/bin/env python3
"""
検証エラー修正スクリプト

100%に達していない企業のデータを分析し、
raw_tagsから正しい値を見つけて修正する
"""

import json
from pathlib import Path

XBRL_STORE = Path(__file__).parent / "xbrl_store"


def load_company_data(company_code: str) -> tuple:
    """企業データとraw_tagsを読み込む"""
    for company_dir in XBRL_STORE.iterdir():
        if company_dir.is_dir() and company_dir.name.startswith(f"{company_code}_"):
            data_file = company_dir / "2022.json"
            raw_file = company_dir / "2022_raw_tags.json"

            if data_file.exists() and raw_file.exists():
                with open(data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                with open(raw_file, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                return data, raw, data_file, company_dir.name
    return None, None, None, None


def analyze_validation_errors(data: dict, raw_tags: dict) -> list:
    """検証エラーを分析して修正候補を返す"""
    fixes = []
    xbrl_data = data.get('data', {})
    tags = raw_tags.get('tags', {})

    # BS検証: 資産合計 = 負債 + 純資産
    total_assets = xbrl_data.get('total_assets', 0)
    total_liabilities = xbrl_data.get('total_liabilities', 0)
    total_equity = xbrl_data.get('total_equity', 0)

    bs_calc = total_liabilities + total_equity
    bs_diff = abs(total_assets - bs_calc)
    bs_diff_pct = bs_diff / total_assets * 100 if total_assets else 0

    if bs_diff_pct > 0.01:  # 0.01%以上の差異
        print(f"  BS差異: {bs_diff_pct:.4f}%")
        print(f"    資産: {total_assets:,.0f}")
        print(f"    負債+純資産: {bs_calc:,.0f} ({total_liabilities:,.0f} + {total_equity:,.0f})")

        # EquityIFRSを確認
        if 'EquityIFRS' in tags:
            equity_ifrs = tags['EquityIFRS']['value']
            new_calc = total_liabilities + equity_ifrs
            new_diff_pct = abs(total_assets - new_calc) / total_assets * 100
            if new_diff_pct < bs_diff_pct:
                fixes.append({
                    'field': 'total_equity',
                    'old': total_equity,
                    'new': equity_ifrs,
                    'tag': 'EquityIFRS',
                    'reason': f'BS差異 {bs_diff_pct:.4f}% → {new_diff_pct:.4f}%'
                })

        # LiabilitiesIFRSを確認
        if 'LiabilitiesIFRS' in tags:
            liab_ifrs = tags['LiabilitiesIFRS']['value']
            new_calc = liab_ifrs + total_equity
            new_diff_pct = abs(total_assets - new_calc) / total_assets * 100
            if new_diff_pct < bs_diff_pct:
                fixes.append({
                    'field': 'total_liabilities',
                    'old': total_liabilities,
                    'new': liab_ifrs,
                    'tag': 'LiabilitiesIFRS',
                    'reason': f'BS差異 {bs_diff_pct:.4f}% → {new_diff_pct:.4f}%'
                })

    # PL検証: 売上高 - 売上原価 = 売上総利益
    revenue = xbrl_data.get('revenue', 0)
    cost_of_sales = xbrl_data.get('cost_of_sales', 0)
    gross_profit = xbrl_data.get('gross_profit', 0)

    pl_calc = revenue - cost_of_sales
    pl_diff = abs(gross_profit - pl_calc)
    pl_diff_pct = pl_diff / gross_profit * 100 if gross_profit else 0

    if pl_diff_pct > 0.01:
        print(f"  PL差異: {pl_diff_pct:.4f}%")
        print(f"    売上総利益: {gross_profit:,.0f}")
        print(f"    売上高-原価: {pl_calc:,.0f} ({revenue:,.0f} - {cost_of_sales:,.0f})")

        # RevenueIFRSを確認
        for tag_name in ['RevenueIFRS', 'RevenueFromExternalCustomersIFRS']:
            if tag_name in tags:
                rev_ifrs = tags[tag_name]['value']
                new_calc = rev_ifrs - cost_of_sales
                new_diff_pct = abs(gross_profit - new_calc) / gross_profit * 100
                if new_diff_pct < pl_diff_pct:
                    fixes.append({
                        'field': 'revenue',
                        'old': revenue,
                        'new': rev_ifrs,
                        'tag': tag_name,
                        'reason': f'PL差異 {pl_diff_pct:.4f}% → {new_diff_pct:.4f}%'
                    })
                    break

        # GrossProfitIFRSを確認
        if 'GrossProfitIFRS' in tags:
            gp_ifrs = tags['GrossProfitIFRS']['value']
            new_calc = revenue - cost_of_sales
            if abs(gp_ifrs - new_calc) < pl_diff:
                fixes.append({
                    'field': 'gross_profit',
                    'old': gross_profit,
                    'new': gp_ifrs,
                    'tag': 'GrossProfitIFRS',
                    'reason': 'GrossProfitIFRS値に修正'
                })

    # 流動資産 + 固定資産 = 資産合計
    current_assets = xbrl_data.get('current_assets', 0)
    non_current_assets = xbrl_data.get('non_current_assets', 0)
    assets_calc = current_assets + non_current_assets
    assets_diff_pct = abs(total_assets - assets_calc) / total_assets * 100 if total_assets else 0

    if assets_diff_pct > 0.01:
        print(f"  資産内訳差異: {assets_diff_pct:.4f}%")
        print(f"    資産合計: {total_assets:,.0f}")
        print(f"    流動+固定: {assets_calc:,.0f}")

        # CurrentAssetsIFRSを確認
        if 'CurrentAssetsIFRS' in tags:
            ca_ifrs = tags['CurrentAssetsIFRS']['value']
            new_calc = ca_ifrs + non_current_assets
            new_diff_pct = abs(total_assets - new_calc) / total_assets * 100
            if new_diff_pct < assets_diff_pct:
                fixes.append({
                    'field': 'current_assets',
                    'old': current_assets,
                    'new': ca_ifrs,
                    'tag': 'CurrentAssetsIFRS',
                    'reason': f'資産内訳差異 {assets_diff_pct:.4f}% → {new_diff_pct:.4f}%'
                })

        # NonCurrentAssetsIFRSを確認
        if 'NonCurrentAssetsIFRS' in tags:
            nca_ifrs = tags['NonCurrentAssetsIFRS']['value']
            new_calc = current_assets + nca_ifrs
            new_diff_pct = abs(total_assets - new_calc) / total_assets * 100
            if new_diff_pct < assets_diff_pct:
                fixes.append({
                    'field': 'non_current_assets',
                    'old': non_current_assets,
                    'new': nca_ifrs,
                    'tag': 'NonCurrentAssetsIFRS',
                    'reason': f'資産内訳差異 {assets_diff_pct:.4f}% → {new_diff_pct:.4f}%'
                })

    # 流動負債 + 固定負債 = 負債合計
    current_liabilities = xbrl_data.get('current_liabilities', 0)
    non_current_liabilities = xbrl_data.get('non_current_liabilities', 0)
    liab_calc = current_liabilities + non_current_liabilities
    liab_diff_pct = abs(total_liabilities - liab_calc) / total_liabilities * 100 if total_liabilities else 0

    if liab_diff_pct > 0.01:
        print(f"  Liab breakdown diff: {liab_diff_pct:.4f}%")

        # TotalCurrentLiabilitiesIFRSを確認
        for tag_name in ['TotalCurrentLiabilitiesIFRS', 'CurrentLiabilitiesIFRS']:
            if tag_name in tags:
                cl_ifrs = tags[tag_name]['value']
                new_calc = cl_ifrs + non_current_liabilities
                new_diff_pct = abs(total_liabilities - new_calc) / total_liabilities * 100
                if new_diff_pct < liab_diff_pct:
                    fixes.append({
                        'field': 'current_liabilities',
                        'old': current_liabilities,
                        'new': cl_ifrs,
                        'tag': tag_name,
                        'reason': f'Liab breakdown diff {liab_diff_pct:.4f}% -> {new_diff_pct:.4f}%'
                    })
                    break

    # PL: 税引前利益 - 法人税等 = 純利益
    income_before_tax = xbrl_data.get('income_before_tax', 0)
    income_taxes = xbrl_data.get('income_taxes', 0)
    net_income = xbrl_data.get('net_income', 0)

    if income_before_tax and income_taxes and net_income:
        ni_calc = income_before_tax - income_taxes
        ni_diff = abs(net_income - ni_calc)
        ni_diff_pct = ni_diff / abs(net_income) * 100 if net_income else 0

        if ni_diff_pct > 0.5:  # 0.5%以上の差異
            print(f"  Net income diff: {ni_diff_pct:.2f}%")
            print(f"    Net income: {net_income:,.0f}")
            print(f"    Pre-tax - Tax: {ni_calc:,.0f} ({income_before_tax:,.0f} - {income_taxes:,.0f})")

            # ProfitLossAttributableToOwnersOfParentIFRSを確認
            for tag_name in ['ProfitLossAttributableToOwnersOfParentIFRS',
                            'ProfitLossAttributableToOwnersOfParent',
                            'ProfitLossIFRS']:
                if tag_name in tags:
                    ni_ifrs = tags[tag_name]['value']
                    new_diff_pct = abs(ni_ifrs - ni_calc) / abs(ni_ifrs) * 100 if ni_ifrs else 0
                    print(f"    Found {tag_name}: {ni_ifrs:,.0f} (diff: {new_diff_pct:.2f}%)")

                    # 差異が改善されるか、より適切な値か判断
                    if new_diff_pct < ni_diff_pct or abs(ni_ifrs - ni_calc) < ni_diff:
                        fixes.append({
                            'field': 'net_income',
                            'old': net_income,
                            'new': ni_ifrs,
                            'tag': tag_name,
                            'reason': f'Net income diff {ni_diff_pct:.2f}% -> {new_diff_pct:.2f}%'
                        })
                        break

            # IncomeTaxExpenseIFRSを確認（法人税等の代替）
            for tag_name in ['IncomeTaxExpenseIFRS', 'IncomeTaxes']:
                if tag_name in tags:
                    tax_ifrs = tags[tag_name]['value']
                    new_calc = income_before_tax - tax_ifrs
                    new_diff_pct = abs(net_income - new_calc) / abs(net_income) * 100 if net_income else 0
                    if new_diff_pct < ni_diff_pct:
                        fixes.append({
                            'field': 'income_taxes',
                            'old': income_taxes,
                            'new': tax_ifrs,
                            'tag': tag_name,
                            'reason': f'Income tax fix: diff {ni_diff_pct:.2f}% -> {new_diff_pct:.2f}%'
                        })
                        break

    return fixes


def apply_fixes(data: dict, fixes: list, data_file: Path):
    """修正を適用して保存"""
    xbrl_data = data.get('data', {})

    for fix in fixes:
        field = fix['field']
        old_val = fix['old']
        new_val = fix['new']
        tag = fix['tag']
        reason = fix['reason']

        print(f"    [+] {field}: {old_val:,.0f} -> {new_val:,.0f} ({tag})")
        print(f"        Reason: {reason}")

        xbrl_data[field] = new_val

    # 保存
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"    [SAVE] {data_file.name}")


def validate_after_fix(data: dict) -> dict:
    """修正後の検証"""
    xbrl_data = data.get('data', {})
    results = {}

    # BS検証
    total_assets = xbrl_data.get('total_assets', 0)
    total_liabilities = xbrl_data.get('total_liabilities', 0)
    total_equity = xbrl_data.get('total_equity', 0)
    bs_calc = total_liabilities + total_equity
    bs_diff_pct = abs(total_assets - bs_calc) / total_assets * 100 if total_assets else 0
    results['bs_balance'] = bs_diff_pct

    # PL検証 - 売上総利益
    revenue = xbrl_data.get('revenue', 0)
    cost_of_sales = xbrl_data.get('cost_of_sales', 0)
    gross_profit = xbrl_data.get('gross_profit', 0)
    pl_calc = revenue - cost_of_sales
    pl_diff_pct = abs(gross_profit - pl_calc) / gross_profit * 100 if gross_profit else 0
    results['pl_gross'] = pl_diff_pct

    # PL検証 - 純利益
    income_before_tax = xbrl_data.get('income_before_tax', 0)
    income_taxes = xbrl_data.get('income_taxes', 0)
    net_income = xbrl_data.get('net_income', 0)
    if income_before_tax and net_income:
        ni_calc = income_before_tax - income_taxes
        ni_diff_pct = abs(net_income - ni_calc) / abs(net_income) * 100 if net_income else 0
        results['pl_net_income'] = ni_diff_pct

    # 資産内訳
    current_assets = xbrl_data.get('current_assets', 0)
    non_current_assets = xbrl_data.get('non_current_assets', 0)
    assets_calc = current_assets + non_current_assets
    assets_diff_pct = abs(total_assets - assets_calc) / total_assets * 100 if total_assets else 0
    results['assets_breakdown'] = assets_diff_pct

    # 負債内訳
    current_liabilities = xbrl_data.get('current_liabilities', 0)
    non_current_liabilities = xbrl_data.get('non_current_liabilities', 0)
    liab_calc = current_liabilities + non_current_liabilities
    liab_diff_pct = abs(total_liabilities - liab_calc) / total_liabilities * 100 if total_liabilities else 0
    results['liab_breakdown'] = liab_diff_pct

    return results


def main():
    # 100%未満の企業
    problem_companies = [
        "2801",  # キッコーマン: 83.3%
        "3382",  # セブン&アイ: 70.0%
        "8801",  # 三井不動産: 75.0%
    ]

    print("="*60)
    print("Validation Error Fix")
    print("="*60)

    for company_code in problem_companies:
        print(f"\n{'='*50}")
        data, raw_tags, data_file, company_name = load_company_data(company_code)

        if not data:
            print(f"[X] {company_code}: Data not found")
            continue

        print(f"[*] {company_code} {company_name}")

        # 現在の検証結果を表示
        validation = data.get('validation', {})
        current_score = validation.get('overall_score', 0)
        print(f"  Current score: {current_score}%")

        # エラーを分析
        fixes = analyze_validation_errors(data, raw_tags)

        if not fixes:
            print("  [OK] No fixes needed")
            continue

        print(f"\n  [FIX] Found {len(fixes)} fixes")

        # 修正を適用
        apply_fixes(data, fixes, data_file)

        # 修正後の検証
        print("\n  [RESULT] After fix:")
        results = validate_after_fix(data)
        for check, diff in results.items():
            status = "[OK]" if diff < 0.01 else "[WARN]" if diff < 1.0 else "[ERR]"
            print(f"    {status} {check}: {diff:.4f}%")

    print("\n" + "="*60)
    print("Fix completed")
    print("="*60)


if __name__ == "__main__":
    main()
