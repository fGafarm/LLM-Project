#!/usr/bin/env python3
import json
from pathlib import Path

json_path = Path("output_v10/7203_トヨタ自動車株式会社/porta10_7203_2022_有報_20260126_112311.json")

with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

xbrl = data.get('xbrl', {})

print(f"Total XBRL fields: {len(xbrl)}")
print(f"Fields with 'dividend': {[k for k in xbrl.keys() if 'dividend' in k.lower()]}")
print(f"\ndividend_per_share: {xbrl.get('dividend_per_share', 'NOT FOUND')}")
print(f"dividend_per_share_calc: {xbrl.get('dividend_per_share_calc', 'NOT FOUND')}")
print(f"dividends_paid: {xbrl.get('dividends_paid', 'NOT FOUND')}")
print(f"total_dividend: {xbrl.get('total_dividend', 'NOT FOUND')}")
