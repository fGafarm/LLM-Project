#!/usr/bin/env python3
"""Compare xbrl_store vs report JSON for all 10 companies"""
import json
import os
from datetime import datetime
from pathlib import Path

COMPANIES = [
    ("7203", "トヨタ自動車株式会社"),
    ("6758", "ソニーグループ株式会社"),
    ("1301", "株式会社　極洋"),
    ("9984", "ソフトバンクグループ株式会社"),
    ("7974", "任天堂株式会社"),
    ("9983", "株式会社ファーストリテイリング"),
    ("4063", "信越化学工業株式会社"),
    ("6501", "株式会社日立製作所"),
    ("2914", "日本たばこ産業株式会社"),
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),
]

OUTPUT_BASE = Path("output_v10")
XBRL_STORE = Path("xbrl_store")

print("Comparing xbrl_store vs LATEST Report JSON for all 10 companies")
print("=" * 90)
print(f"{'Code':<6} {'Store':<6} {'Report':<7} {'Missing':<9} {'Dividend Store':<15} {'Dividend Report':<16} {'Report Time'}")
print("-" * 90)

for code, name in COMPANIES:
    # Load xbrl_store
    xbrl_dirs = list(XBRL_STORE.glob(f"{code}_*"))
    if not xbrl_dirs:
        print(f"{code:<6} NO XBRL STORE")
        continue

    xbrl_file = xbrl_dirs[0] / "2022.json"
    if not xbrl_file.exists():
        print(f"{code:<6} NO 2022.json")
        continue

    with open(xbrl_file, 'r', encoding='utf-8') as f:
        store_data = json.load(f)
    store_xbrl = store_data.get('data', {})
    store_keys = len(store_xbrl)
    store_dps = store_xbrl.get('dividend_per_share')

    # Load report JSON (LATEST by modification time)
    company_dirs = list(OUTPUT_BASE.glob(f"{code}_*"))
    if not company_dirs:
        print(f"{code:<6} {store_keys:<6} NO REPORT")
        continue

    json_files = list(company_dirs[0].glob("porta10_*_2022_*.json"))
    if not json_files:
        print(f"{code:<6} {store_keys:<6} NO JSON")
        continue

    # Get the latest JSON by modification time
    latest_json = max(json_files, key=os.path.getmtime)
    report_time = datetime.fromtimestamp(os.path.getmtime(latest_json))

    with open(latest_json, 'r', encoding='utf-8') as f:
        report_data = json.load(f)
    report_xbrl = report_data.get('xbrl', {})
    report_keys = len(report_xbrl)
    report_dps = report_xbrl.get('dividend_per_share')

    missing_keys = store_keys - report_keys
    store_dps_str = f"{store_dps:.2f}" if store_dps else "None"
    report_dps_str = f"{report_dps:.2f}" if report_dps else "None"
    report_time_str = report_time.strftime("%m-%d %H:%M")

    print(f"{code:<6} {store_keys:<6} {report_keys:<7} {missing_keys:<9} {store_dps_str:<15} {report_dps_str:<16} {report_time_str}")
