#!/usr/bin/env python3
"""
Debug script to trace dividend_per_share through the data pipeline
"""
import json
from pathlib import Path

def load_historical_xbrl_from_store_debug(company_code: str):
    """Simplified version of load_historical_xbrl_from_store"""
    store_path = Path("xbrl_store")
    company_dirs = list(store_path.glob(f"{company_code}_*"))

    if not company_dirs:
        print(f"NO XBRL STORE FOUND for {company_code}")
        return {}

    company_dir = company_dirs[0]
    years_data = {}

    json_file = company_dir / "2022.json"
    if not json_file.exists():
        print(f"NO 2022.json FOUND")
        return {}

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        year = int(data.get("fiscal_year", "2022"))
        xbrl_data = data.get("data", {})

        print(f"\n1. Loaded from xbrl_store/{company_dir.name}/2022.json:")
        print(f"   dividend_per_share: {xbrl_data.get('dividend_per_share')}")
        print(f"   dividends_paid: {xbrl_data.get('dividends_paid')}")
        print(f"   shares_issued: {xbrl_data.get('shares_issued')}")
        print(f"   Total keys: {len(xbrl_data)}")

        years_data[year] = xbrl_data

    return years_data

def test_dividend_flow(company_code: str):
    print(f"=" * 70)
    print(f"Testing dividend_per_share data flow for {company_code}")
    print(f"=" * 70)

    # Step 1: Load from xbrl_store (simulating load_historical_xbrl_from_store)
    historical_xbrl = load_historical_xbrl_from_store_debug(company_code)

    current_year_int = 2022
    if current_year_int not in historical_xbrl:
        print(f"\nERROR: Year {current_year_int} not in historical_xbrl")
        return

    # Step 2: Simulate xbrl = stored_xbrl (line 3503)
    stored_xbrl = historical_xbrl[current_year_int]
    if stored_xbrl.get('roe_calc') is not None:
        xbrl = stored_xbrl
        print(f"\n2. After xbrl = stored_xbrl (line 3503):")
        print(f"   dividend_per_share: {xbrl.get('dividend_per_share')}")
        print(f"   Total keys: {len(xbrl)}")
        print(f"   roe_calc exists: {xbrl.get('roe_calc') is not None}")

    # Step 3: Check if it would be in the final result_data (line 3767)
    result_data = {
        'company_code': company_code,
        'xbrl': xbrl,
    }

    print(f"\n3. In result_data (line 3767):")
    print(f"   dividend_per_share: {result_data['xbrl'].get('dividend_per_share')}")

    # Step 4: Serialize to JSON and load back
    temp_file = Path("temp_debug.json")
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)

    with open(temp_file, 'r', encoding='utf-8') as f:
        loaded_data = json.load(f)

    print(f"\n4. After JSON serialization round-trip:")
    print(f"   dividend_per_share: {loaded_data['xbrl'].get('dividend_per_share')}")

    temp_file.unlink()

    # Step 5: Check actual report JSON
    output_base = Path("output_v10")
    company_dirs = list(output_base.glob(f"{company_code}_*"))
    if company_dirs:
        json_files = sorted(company_dirs[0].glob("porta10_*_2022_*.json"), reverse=True)
        if json_files:
            with open(json_files[0], 'r', encoding='utf-8') as f:
                report_data = json.load(f)

            print(f"\n5. Actual report JSON ({json_files[0].name}):")
            print(f"   dividend_per_share: {report_data.get('xbrl', {}).get('dividend_per_share')}")
            print(f"   Total XBRL keys: {len(report_data.get('xbrl', {}))}")


if __name__ == "__main__":
    test_dividend_flow("7203")  # Toyota
