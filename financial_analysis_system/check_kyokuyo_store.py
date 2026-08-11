#!/usr/bin/env python3
import json
from pathlib import Path

store_file = Path("xbrl_store/1301_株式会社　極洋/2022.json")
with open(store_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

xbrl = data.get('data', {})

print(f"極洋 (1301) xbrl_store:")
print(f"  roe_calc: {xbrl.get('roe_calc')}")
print(f"  roe_calc is not None: {xbrl.get('roe_calc') is not None}")
print(f"  Total keys: {len(xbrl)}")
print(f"\n  Calc fields:")
for k in sorted(xbrl.keys()):
    if '_calc' in k:
        print(f"    - {k}: {xbrl[k]}")
