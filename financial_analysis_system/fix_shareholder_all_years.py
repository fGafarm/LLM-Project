#!/usr/bin/env python3
"""大株主結合バグ修正 - 全年度のcompany_data"""
import sys
import json
import glob
import os
import re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from extract_company_data import extract_shareholders
from pathlib import Path

PDF_BASE = Path("E:/PDF/本番用PDF分割")
COMPANY_DATA = Path(__file__).parent / "company_data"

def find_company_dir(ticker):
    dirs = list(PDF_BASE.glob(f"{ticker}_*"))
    return dirs[0] if dirs else None

def main():
    print("全年度の大株主結合バグ修正")
    print("=" * 60)

    # バグファイルを検出
    bug_files = []
    for f in glob.glob(str(COMPANY_DATA / "*_data.json")):
        with open(f, 'r', encoding='utf-8') as fp:
            d = json.load(fp)
        sh = d.get('major_shareholders', {})
        holders = sh.get('major_shareholders', sh.get('holders', []))
        for h in holders:
            s = h.get('shares_thousands')
            if s and isinstance(s, (int, float)) and s > 1e15:
                bug_files.append(f)
                break

    print(f"バグファイル: {len(bug_files)}件")

    fixed = 0
    still_broken = 0
    no_pdf = 0
    errors = 0

    for i, filepath in enumerate(bug_files):
        base = os.path.basename(filepath)
        parts = base.replace('_data.json', '').rsplit('_', 1)
        if len(parts) != 2:
            continue
        ticker, year = parts

        company_dir = find_company_dir(ticker)
        if not company_dir:
            no_pdf += 1
            continue

        # そのyearのPDFがあるか確認
        pdf_path = company_dir / f"{year}_有報" / "06_ガバナンス" / "02_株式等の状況.pdf"
        if not pdf_path.exists():
            no_pdf += 1
            continue

        try:
            result = extract_shareholders(company_dir, year)
            holders = result.get('major_shareholders', [])

            is_fixed = True
            for h in holders:
                s = h.get('shares_thousands')
                if s and isinstance(s, (int, float)) and s > 1e15:
                    is_fixed = False
                    break

            if is_fixed and holders:
                fixed += 1
                with open(filepath, 'r', encoding='utf-8') as f:
                    cd = json.load(f)
                cd['major_shareholders'] = result
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(cd, f, ensure_ascii=False, separators=(',', ':'))
            else:
                still_broken += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error {ticker}/{year}: {e}")

        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(bug_files)} (fixed={fixed}, broken={still_broken}, no_pdf={no_pdf})")

    print(f"\n結果:")
    print(f"  修正: {fixed}件")
    print(f"  未修正: {still_broken}件")
    print(f"  PDF無し: {no_pdf}件")
    print(f"  エラー: {errors}件")

if __name__ == "__main__":
    main()
