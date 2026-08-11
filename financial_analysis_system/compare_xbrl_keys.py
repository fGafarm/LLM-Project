#!/usr/bin/env python3
"""Compare XBRL keys between xbrl_store and report JSON"""
import json
from pathlib import Path

# Load from xbrl_store
store_file = Path("xbrl_store/7203_トヨタ自動車株式会社/2022.json")
with open(store_file, 'r', encoding='utf-8') as f:
    store_data = json.load(f)
store_xbrl = store_data.get('data', {})

# Load from report JSON
report_file = Path("output_v10/7203_トヨタ自動車株式会社/porta10_7203_2022_有報_20260126_112311.json")
with open(report_file, 'r', encoding='utf-8') as f:
    report_data = json.load(f)
report_xbrl = report_data.get('xbrl', {})

print(f"XBRL Store keys: {len(store_xbrl)}")
print(f"Report JSON keys: {len(report_xbrl)}")
print()

# Find keys in store but not in report
missing_in_report = set(store_xbrl.keys()) - set(report_xbrl.keys())
print(f"Keys in xbrl_store but MISSING in report ({len(missing_in_report)}):")
for key in sorted(missing_in_report):
    print(f"  - {key}: {store_xbrl[key]}")
print()

# Find keys in report but not in store
extra_in_report = set(report_xbrl.keys()) - set(store_xbrl.keys())
print(f"Keys in report but NOT in xbrl_store ({len(extra_in_report)}):")
for key in sorted(extra_in_report):
    print(f"  - {key}: {report_xbrl[key]}")
print()

# Find keys with different values
different_values = []
for key in set(store_xbrl.keys()) & set(report_xbrl.keys()):
    if store_xbrl[key] != report_xbrl[key]:
        # Allow for small floating point differences
        try:
            if isinstance(store_xbrl[key], (int, float)) and isinstance(report_xbrl[key], (int, float)):
                if abs(store_xbrl[key] - report_xbrl[key]) < 0.01:
                    continue
        except:
            pass
        different_values.append(key)

print(f"Keys with DIFFERENT values ({len(different_values)}):")
for key in sorted(different_values)[:10]:  # Show first 10
    print(f"  - {key}: store={store_xbrl[key]}, report={report_xbrl[key]}")
