#!/usr/bin/env python3
"""年度順バッチ実行 - 各年度完了後にGPU休憩を挟む"""
import subprocess
import sys
import time
import json
from pathlib import Path

PYTHON = sys.executable
SCRIPT = Path(__file__).parent / "run_nikkei225_batch.py"
REST_SECONDS = 3600  # GPU休憩時間（1時間）

YEARS = ["2022", "2021"]  # 処理する年度

def check_progress(year):
    pf = Path(f"batch_progress_{year}.json")
    if not pf.exists():
        return 0, 0
    with open(pf, "r", encoding="utf-8") as f:
        d = json.load(f)
    return len(d.get("completed", {})), len(d.get("failed", {}))

def main():
    for i, year in enumerate(YEARS):
        completed, failed = check_progress(year)
        remaining = 225 - completed
        print(f"\n{'='*60}")
        print(f"  {year}年バッチ開始 ({completed}/225完了済, 残り{remaining}社)")
        print(f"{'='*60}\n")

        if remaining <= 0:
            print(f"  → {year}年は全社完了済み。スキップ。")
            continue

        result = subprocess.run(
            [PYTHON, str(SCRIPT), "--year", year],
            cwd=str(Path(__file__).parent)
        )

        completed, failed = check_progress(year)
        print(f"\n  {year}年完了: {completed}/225社, 失敗{failed}社")

        # 次の年度がある場合、GPU休憩
        if i < len(YEARS) - 1:
            next_year = YEARS[i + 1]
            next_completed, _ = check_progress(next_year)
            if 225 - next_completed > 0:
                print(f"\n  GPU休憩: {REST_SECONDS // 60}分間...")
                time.sleep(REST_SECONDS)
                print("  休憩完了。次の年度へ。")

    print("\n全年度の処理が完了しました。")

if __name__ == "__main__":
    main()
