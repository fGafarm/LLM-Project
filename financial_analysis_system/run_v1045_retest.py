#!/usr/bin/env python3
"""v10.4.5 修正検証 - 地域別報告企業4社のみ再実行"""
import sys
import os
import time
sys.stdout.reconfigure(encoding='utf-8')

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from Run_integrated_v10_2 import (
    process_single_company, Company, LocalRAGDB,
    infer_industry_from_name, Config
)
from pathlib import Path

# バッチ後修正の影響を受ける4社
COMPANIES = [
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),  # 「その他」地域キャッチオール
    ("6902", "株式会社デンソー"),                             # 「その他」地域キャッチオール
    ("8035", "東京エレクトロン株式会社"),                     # 「その他」地域キャッチオール
    ("8316", "株式会社三井住友フィナンシャルグループ"),        # 「米州」キーワード追加
]

def main():
    year = "2024"
    doc_type = "有報"
    output_base = Path("./output_v10.2")
    output_base.mkdir(parents=True, exist_ok=True)
    rag_db = LocalRAGDB("./rag_db")

    total_start = time.time()
    results = []

    print(f"\n{'='*70}")
    print(f"  v10.4.5 修正検証: {len(COMPANIES)}社 (地域別報告企業)")
    print(f"  年度: {year} / 種別: {doc_type}")
    print(f"{'='*70}\n")

    for i, (code, name) in enumerate(COMPANIES, 1):
        industry = infer_industry_from_name(name)
        print(f"\n{'='*70}")
        print(f"  [{i}/{len(COMPANIES)}] {code} {name} (industry={industry})")
        print(f"{'='*70}")

        start = time.time()
        try:
            result = process_single_company(
                company_code=code,
                company_name=name,
                year=year,
                doc_type=doc_type,
                industry="all",
                output_base=output_base,
                rag_db=rag_db
            )
            elapsed = time.time() - start
            status = result.get('status', 'unknown')
            xbrl_items = result.get('xbrl_items', 0)
            sections = result.get('sections', 0)
            print(f"  -> {status} | XBRL: {xbrl_items}項目 | sections: {sections} | {elapsed:.1f}秒")
            results.append((code, name, status, elapsed))
        except Exception as e:
            elapsed = time.time() - start
            print(f"  -> ERROR: {e} | {elapsed:.1f}秒")
            results.append((code, name, f"error: {e}", elapsed))

    total_elapsed = time.time() - total_start

    print(f"\n\n{'='*70}")
    print(f"  v10.4.5修正検証 完了 ({total_elapsed/60:.1f}分)")
    print(f"{'='*70}")
    success = sum(1 for _, _, s, _ in results if s == 'success')
    print(f"  成功: {success}/{len(results)}社\n")
    for code, name, status, elapsed in results:
        mark = "✅" if status == "success" else "❌"
        print(f"  {mark} {code} {name}: {status} ({elapsed:.1f}秒)")

if __name__ == "__main__":
    main()
