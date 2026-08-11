#!/usr/bin/env python3
"""v10.4.8 修正検証 - 3社再実行（Pattern1/及びコネクタ修正後）"""
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

# Pattern1全文検索 + 及びコネクタ修正で改善する3社
COMPANIES = [
    ("3382", "株式会社セブン＆アイ・ホールディングス"),       # Pattern1全文検索で正しいセグメント名取得
    ("9433", "ＫＤＤＩ株式会社"),                             # Pattern1全文検索で「パーソナル」「ビジネス」取得
    ("6902", "株式会社デンソー"),                              # 「及び」コネクタ修正で日本を含む4地域取得
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
    print(f"  v10.4.8 修正検証(2): {len(COMPANIES)}社 (Pattern1/及び修正)")
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
            import traceback
            traceback.print_exc()
            results.append((code, name, f"error: {e}", elapsed))

    total_elapsed = time.time() - total_start

    print(f"\n\n{'='*70}")
    print(f"  v10.4.8修正検証 完了 ({total_elapsed/60:.1f}分)")
    print(f"{'='*70}")
    success = sum(1 for _, _, s, _ in results if s == 'success')
    print(f"  成功: {success}/{len(results)}社\n")
    for code, name, status, elapsed in results:
        mark = "✅" if status == "success" else "❌"
        print(f"  {mark} {code} {name}: {status} ({elapsed:.1f}秒)")

if __name__ == "__main__":
    main()
