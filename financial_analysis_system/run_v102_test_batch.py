#!/usr/bin/env python3
"""
v10.2テスト用バッチスクリプト
問題企業8社を順次処理
"""

import subprocess
import sys
import time

# テスト対象企業リスト
TEST_COMPANIES = [
    # COPY_FROM_TOTAL (未テスト3社)
    {"code": "4689", "year": "2025", "name": "LINEヤフー", "issue": "COPY_FROM_TOTAL"},
    {"code": "5711", "year": "2025", "name": "三菱マテリアル", "issue": "COPY_FROM_TOTAL"},
    {"code": "6674", "year": "2025", "name": "GSユアサ", "issue": "COPY_FROM_TOTAL"},
    # ALL_NA (5社)
    {"code": "3092", "year": "2025", "name": "ZOZO", "issue": "ALL_NA"},
    {"code": "5019", "year": "2025", "name": "出光興産", "issue": "ALL_NA"},
    {"code": "6501", "year": "2023", "name": "日立製作所", "issue": "ALL_NA"},
    {"code": "6532", "year": "2025", "name": "ベイカレント", "issue": "ALL_NA"},
    {"code": "7012", "year": "2025", "name": "川崎重工業", "issue": "ALL_NA"},
]

def run_company(company):
    """1社を処理"""
    print(f"\n{'='*60}")
    print(f"処理開始: {company['code']} {company['name']} ({company['year']})")
    print(f"問題タイプ: {company['issue']}")
    print(f"{'='*60}")

    cmd = [
        sys.executable,
        "Run_integrated_v10_2.py",
        "--company", company["code"],
        "--year", company["year"],
        "--industry", "all"
    ]

    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            cwd="c:/Users/shun nabeno/Desktop/Local LLM Project/financial_analysis_system",
            capture_output=False,  # 出力をリアルタイムで表示
            text=True,
            timeout=600  # 10分タイムアウト
        )
        elapsed = time.time() - start_time
        print(f"\n完了: {company['name']} ({elapsed:.1f}秒)")
        return {"company": company, "success": result.returncode == 0, "time": elapsed}
    except subprocess.TimeoutExpired:
        print(f"\nタイムアウト: {company['name']}")
        return {"company": company, "success": False, "time": 600, "error": "timeout"}
    except Exception as e:
        print(f"\nエラー: {company['name']} - {e}")
        return {"company": company, "success": False, "time": 0, "error": str(e)}

def main():
    print("=" * 60)
    print("v10.2 テストバッチ - 問題企業8社処理")
    print("=" * 60)

    results = []
    total_start = time.time()

    for i, company in enumerate(TEST_COMPANIES):
        print(f"\n[{i+1}/{len(TEST_COMPANIES)}]", end="")
        result = run_company(company)
        results.append(result)

    # サマリー
    print("\n" + "=" * 60)
    print("処理結果サマリー")
    print("=" * 60)

    success_count = sum(1 for r in results if r["success"])
    print(f"成功: {success_count}/{len(results)}")

    for r in results:
        status = "OK" if r["success"] else "NG"
        print(f"  [{status}] {r['company']['code']} {r['company']['name']} ({r['time']:.1f}秒)")

    print(f"\n総処理時間: {time.time() - total_start:.1f}秒")

if __name__ == "__main__":
    main()
