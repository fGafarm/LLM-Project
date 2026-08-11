#!/usr/bin/env python3
"""v10.4.9 セグメント修正検証 - ゴミ行フィルタ + 合計直接計算"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from Run_integrated_v10_2 import process_single_company, LocalRAGDB
from pathlib import Path
import json

COMPANIES = [
    ("1332", "株式会社ニッスイ"),       # 「売上高」ゴミ行 + 地域セグメント二重カウント
    ("1721", "コムシスホールディングス株式会社"),  # 「− − 1,941」ゴミ行
]

def main():
    year = "2024"
    doc_type = "有報"
    output_base = Path("./output_v10.2")
    rag_db = LocalRAGDB("./rag_db")

    for code, name in COMPANIES:
        print(f"\n{'='*70}")
        print(f"  {code} {name}")
        print(f"{'='*70}")
        start = time.time()
        result = process_single_company(
            company_code=code, company_name=name,
            year=year, doc_type=doc_type,
            industry="all", output_base=output_base, rag_db=rag_db
        )
        elapsed = time.time() - start
        print(f"  -> {result.get('status')} | {elapsed:.1f}秒")

        # 抽出ログ確認
        ext_log = output_base / f"{code}_{name}" / "extraction_logs" / year / "05_セグメント_extraction.json"
        if ext_log.exists():
            with open(ext_log, 'r', encoding='utf-8') as f:
                data = json.load(f)
            bseg = data.get("extracted", {}).get("business_segments", [])
            gseg = data.get("extracted", {}).get("geographic_segments", [])
            print(f"  business_segments ({len(bseg)}):")
            for s in bseg:
                print(f"    - {s['name']}: rev={s.get('revenue')}, prof={s.get('profit')}")
            print(f"  geographic_segments ({len(gseg)}):")
            for s in gseg:
                print(f"    - {s['name']}: rev={s.get('revenue')}")

        # 数値チェック確認
        jsons = sorted(
            (output_base / f"{code}_{name}").glob(f"porta102_{code}_*.json"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if jsons:
            with open(jsons[0], 'r', encoding='utf-8') as f:
                jdata = json.load(f)
            nc = jdata.get("number_check", {})
            seg_sum = nc.get("segment_summary", {})
            print(f"  number_check.segment_summary:")
            for k, v in seg_sum.items():
                match = "✅" if v.get("match") else "⚠️"
                print(f"    {match} {k}: seg={v.get('segment_total',0)/1e8:,.1f}億 vs co={v.get('company_total',0)/1e8:,.1f}億 ({v.get('diff_pct',0):.1f}%)")

if __name__ == "__main__":
    main()
