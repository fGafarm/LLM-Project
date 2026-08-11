"""
半導体 PoC のエンドツーエンド実行スクリプト
順序: JP抽出 → US抽出 → マッピング → 集計
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
PY = r"C:/Users/shun nabeno/AppData/Local/Python/bin/python.exe"


def run(name: str, cmd: list[str]):
    print(f"\n=== {name} ===")
    print(" ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"FAIL: {name} rc={rc}")
        sys.exit(rc)


def main():
    run("JP extract", [PY, str(BASE / "extractor" / "jp_xbrl_segments.py"), "--year", "2024"])
    run("US extract", [PY, str(BASE / "extractor" / "us_10k_segments.py"), "--year", "2024"])
    run("Category mapping", [PY, str(BASE / "mapper" / "category_mapper.py")])
    run("Aggregation", [PY, str(BASE / "aggregator" / "industry_aggregator.py")])
    print("\n=== DONE ===")
    print(f"Report: {BASE / 'data' / 'aggregates' / 'semiconductor_2024_report.md'}")


if __name__ == "__main__":
    main()
