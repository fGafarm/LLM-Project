#!/usr/bin/env python3
"""大株主結合バグの修正: 影響1009社を再抽出"""
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
FRONTEND_DATA = Path(__file__).parent.parent / "StockFlow" / "frontend" / "public" / "data"
COMPANY_DATA = Path(__file__).parent / "company_data"

def find_bug_tickers():
    """フロントエンドJSONからバグ企業を検出"""
    bug_tickers = []
    for f in glob.glob(str(FRONTEND_DATA / "*.json")):
        if "index.json" in f:
            continue
        ticker = os.path.basename(f).replace('.json', '')
        with open(f, 'r', encoding='utf-8') as fp:
            d = json.load(fp)
        ci = d.get('companyInfo', {})
        sh = ci.get('shareholders', {})
        ms = sh.get('majorShareholders', [])
        for entry in ms:
            s = entry.get('sharesThousands')
            if s and isinstance(s, (int, float)) and s > 1e15:
                bug_tickers.append(ticker)
                break
    return bug_tickers

def find_company_dir(ticker):
    dirs = list(PDF_BASE.glob(f"{ticker}_*"))
    return dirs[0] if dirs else None

def find_latest_year(company_dir):
    for year in ['2025', '2024', '2023', '2022', '2021', '2020']:
        if (company_dir / f"{year}_有報" / "06_ガバナンス" / "02_株式等の状況.pdf").exists():
            return year
    return None

def main():
    print("大株主結合バグ修正スクリプト")
    print("=" * 60)

    print("バグ企業を検出中...")
    bug_tickers = find_bug_tickers()
    print(f"バグ企業: {len(bug_tickers)}社")

    fixed = 0
    still_broken = 0
    no_pdf = 0
    errors = 0

    for i, ticker in enumerate(bug_tickers):
        company_dir = find_company_dir(ticker)
        if not company_dir:
            no_pdf += 1
            continue

        year = find_latest_year(company_dir)
        if not year:
            no_pdf += 1
            continue

        try:
            result = extract_shareholders(company_dir, year)
            holders = result.get('major_shareholders', [])

            # まだバグが残っているか確認
            is_fixed = True
            for h in holders:
                s = h.get('shares_thousands')
                if s and isinstance(s, (int, float)) and s > 1e15:
                    is_fixed = False
                    break

            if is_fixed and holders:
                fixed += 1
                # company_dataを更新
                for cd_year in ['2025', '2024', '2023', '2022', '2021', '2020']:
                    cd_path = COMPANY_DATA / f"{ticker}_{cd_year}_data.json"
                    if cd_path.exists():
                        with open(cd_path, 'r', encoding='utf-8') as f:
                            cd = json.load(f)
                        cd['major_shareholders'] = result
                        with open(cd_path, 'w', encoding='utf-8') as f:
                            json.dump(cd, f, ensure_ascii=False, separators=(',', ':'))
                        break

                # フロントエンドJSONも更新
                fe_path = FRONTEND_DATA / f"{ticker}.json"
                if fe_path.exists():
                    with open(fe_path, 'r', encoding='utf-8') as f:
                        fe = json.load(f)
                    ci = fe.get('companyInfo', {})
                    ci['shareholders'] = {
                        'majorShareholders': [
                            {'name': h['name'],
                             'sharesThousands': h['shares_thousands'],
                             'ratioPercent': h['ratio_percent']}
                            for h in holders
                        ],
                        'totalSharesIssued': result.get('total_shares_issued'),
                        'dividendPerShare': result.get('dividend_per_share'),
                    }
                    fe['companyInfo'] = ci
                    with open(fe_path, 'w', encoding='utf-8') as f:
                        json.dump(fe, f, ensure_ascii=False, separators=(',', ':'))
            else:
                still_broken += 1

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  Error {ticker}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(bug_tickers)} processed (fixed={fixed}, broken={still_broken}, no_pdf={no_pdf})")

    print(f"\n結果:")
    print(f"  修正: {fixed}社")
    print(f"  未修正: {still_broken}社")
    print(f"  PDF無し: {no_pdf}社")
    print(f"  エラー: {errors}社")

if __name__ == "__main__":
    main()
