#!/usr/bin/env python3
"""2022年バッチ完了を監視 → 1時間休憩 → 2021年バッチ開始"""
import subprocess
import sys
import time
import json
from pathlib import Path
sys.stdout.reconfigure(line_buffering=True)

PYTHON = sys.executable
SCRIPT = Path(__file__).parent / "run_nikkei225_batch.py"
REST_SECONDS = 3600

def check_progress(year):
    pf = Path(f"batch_progress_{year}.json")
    if not pf.exists():
        return 0, 0
    with open(pf, "r", encoding="utf-8") as f:
        d = json.load(f)
    return len(d.get("completed", {})), len(d.get("failed", {}))

def wait_for_2022():
    """2022年バッチの完了を60秒間隔で監視"""
    print("2022年バッチの完了を監視中...")
    while True:
        completed, failed = check_progress("2022")
        remaining = 225 - completed
        if remaining <= 0:
            print(f"  2022年完了: {completed}/225社 (失敗{failed}社)")
            return
        print(f"  2022年: {completed}/225社完了, 残り{remaining}社...")
        time.sleep(60)

def main():
    # 2022年バッチ完了を待機
    wait_for_2022()

    # GPU休憩
    print(f"\nGPU休憩: {REST_SECONDS // 60}分間...")
    time.sleep(REST_SECONDS)
    print("休憩完了。2021年バッチを開始します。\n")

    # 2021年バッチ実行
    completed, _ = check_progress("2021")
    remaining = 225 - completed
    print(f"2021年バッチ開始 ({completed}/225完了済, 残り{remaining}社)")
    subprocess.run(
        [PYTHON, str(SCRIPT), "--year", "2021"],
        cwd=str(Path(__file__).parent)
    )

    completed, failed = check_progress("2021")
    print(f"\n2021年完了: {completed}/225社, 失敗{failed}社")
    print("全処理完了。")

if __name__ == "__main__":
    main()
