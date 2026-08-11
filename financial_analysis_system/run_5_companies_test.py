#!/usr/bin/env python3
"""
v10.3 - 5社テスト実行スクリプト
レポート全体（特に文章部分）の品質確認用
"""

import subprocess
import sys
import time
from datetime import datetime

# テスト対象5社
TEST_COMPANIES = [
    ("1332", "株式会社ニッスイ"),
    ("1414", "ショーボンドホールディングス株式会社"),
    ("1417", "株式会社ミライト・ワン"),
    ("1377", "株式会社サカタのタネ"),
    ("1379", "ホクト株式会社"),
]

def main():
    print("=" * 60)
    print("v10.3 - 5社テスト実行")
    print(f"開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = []
    total_start = time.time()

    for i, (code, name) in enumerate(TEST_COMPANIES, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/5] {code}_{name}")
        print("=" * 60)

        start = time.time()

        cmd = [
            sys.executable,
            "Run_integrated_v10_2.py",
            "--company", code,
            "--year", "2024",
            "--type", "有報",
            "--model", "gemma2:9b",
            "--final-model", "qwen3:14b",
            "--mode", "hybrid"
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=False,
                text=True,
                timeout=600  # 10分タイムアウト
            )
            elapsed = time.time() - start
            status = "成功" if result.returncode == 0 else f"失敗(code={result.returncode})"
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            status = "タイムアウト"
        except Exception as e:
            elapsed = time.time() - start
            status = f"エラー: {e}"

        results.append({
            "code": code,
            "name": name,
            "status": status,
            "time": elapsed
        })

        print(f"\n→ {status} ({elapsed:.1f}秒)")

    total_elapsed = time.time() - total_start

    print("\n" + "=" * 60)
    print("テスト結果サマリー")
    print("=" * 60)

    success_count = sum(1 for r in results if r["status"] == "成功")

    for r in results:
        status_mark = "✓" if r["status"] == "成功" else "✗"
        print(f"  {status_mark} {r['code']}_{r['name']}: {r['status']} ({r['time']:.1f}秒)")

    print(f"\n結果: {success_count}/5 成功")
    print(f"総時間: {total_elapsed/60:.1f}分")
    print(f"終了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
