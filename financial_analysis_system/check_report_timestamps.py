#!/usr/bin/env python3
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

print("Report Timestamps vs XBRL Store")
print("=" * 70)

for code, name in COMPANIES:
    # Find report
    company_dirs = list(OUTPUT_BASE.glob(f"{code}_*"))
    if not company_dirs:
        print(f"{code}: NO REPORT")
        continue

    md_files = sorted(company_dirs[0].glob("porta10_*_2022_*.md"), reverse=True)
    if not md_files:
        print(f"{code}: NO MD FILE")
        continue

    report_path = md_files[0]
    report_time = os.path.getmtime(report_path)

    # Find xbrl_store
    xbrl_dirs = list(XBRL_STORE.glob(f"{code}_*"))
    if not xbrl_dirs:
        print(f"{code}: NO XBRL STORE")
        continue

    xbrl_file = xbrl_dirs[0] / "2022.json"
    if not xbrl_file.exists():
        print(f"{code}: NO 2022.json IN XBRL STORE")
        continue

    xbrl_time = os.path.getmtime(xbrl_file)

    report_dt = datetime.fromtimestamp(report_time)
    xbrl_dt = datetime.fromtimestamp(xbrl_time)

    older = "OLDER" if report_time < xbrl_time else "NEWER"

    print(f"{code}: Report {report_dt.strftime('%H:%M:%S')}, " +
          f"XBRL {xbrl_dt.strftime('%H:%M:%S')} - Report is {older}")
