"""
companyfacts → us_xbrl_store 変換スクリプト

SEC EDGAR companyfacts (1ファイル/CIK, 全年度混在) を
日本側 xbrl_store と同じ構造 (1ファイル/年度) に変換する。

出力: us_xbrl_store/{ticker}_{company_name}/{year}.json
構造: {"company_code": ticker, "fiscal_year": year, "data": {field: value, ...}}

Usage:
    python companyfacts_to_xbrl_store.py                 # 全社変換
    python companyfacts_to_xbrl_store.py --ticker AAPL   # 1社のみ
    python companyfacts_to_xbrl_store.py --sp500          # S&P500のみ
"""

import json
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime

from config import (
    COMPANYFACTS_DIR, COMPANY_TICKERS_FILE, US_XBRL_STORE, TARGET_YEARS
)
from us_tag_mapping import ALL_TAGS


def load_ticker_map() -> dict:
    """company_tickers.json → {cik_int: {ticker, title}} mapping"""
    with open(COMPANY_TICKERS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for entry in raw.values():
        cik = entry.get("cik_str")
        if cik:
            result[int(cik)] = {
                "ticker": entry.get("ticker", ""),
                "title": entry.get("title", ""),
            }
    return result


def sanitize_name(name: str) -> str:
    """Company name → safe directory name"""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80]


def extract_annual_value(entries: list, fiscal_year: int) -> float | None:
    """
    Given a list of XBRL fact entries for a tag, extract the annual (FY) value
    for the specified fiscal year.

    Handles duplicates by preferring:
    1. fp == "FY" (annual filing)
    2. Most recent 'filed' date (amendment supersedes original)
    3. frame containing "CY{year}" for instant-type facts
    """
    candidates = []

    for e in entries:
        fy = e.get("fy")
        fp = e.get("fp", "")

        # Match fiscal year
        if fy != fiscal_year:
            continue

        # Only annual filings
        if fp != "FY":
            continue

        val = e.get("val")
        if val is None:
            continue

        # For duration facts, check the period covers ~12 months
        start = e.get("start", "")
        end = e.get("end", "")
        filed = e.get("filed", "")

        # Calculate period length if available
        months = 12
        if start and end:
            try:
                d_start = datetime.strptime(start, "%Y-%m-%d")
                d_end = datetime.strptime(end, "%Y-%m-%d")
                months = round((d_end - d_start).days / 30)
            except ValueError:
                pass

        candidates.append({
            "val": val,
            "filed": filed,
            "months": months,
            "end": end,
        })

    if not candidates:
        return None

    # Prefer full-year (10-14 months) entries
    annual = [c for c in candidates if 10 <= c["months"] <= 14]
    if annual:
        candidates = annual

    # Pick the entry whose period end date is latest (= current FY's own data,
    # not prior-year comparison figures that share the same fy tag).
    # Break ties by most recently filed (handles amendments/restatements).
    candidates.sort(key=lambda c: (c["end"], c["filed"]), reverse=True)
    return candidates[0]["val"]


def convert_company(cik: int, ticker: str, company_name: str) -> dict:
    """Convert one company's companyfacts to per-year xbrl_store format."""
    cik_padded = f"CIK{cik:010d}"
    filepath = COMPANYFACTS_DIR / f"{cik_padded}.json"

    if not filepath.exists():
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    facts = data.get("facts", {})
    usgaap = facts.get("us-gaap", {})

    # Also check IFRS (some US-listed companies use IFRS)
    ifrs = facts.get("ifrs-full", {})

    years_data = {}

    for year in TARGET_YEARS:
        extracted = {}

        for internal_field, tag_list in ALL_TAGS.items():
            for tag_name in tag_list:
                # Try US-GAAP first, then IFRS
                tag_data = usgaap.get(tag_name) or ifrs.get(tag_name)
                if not tag_data:
                    continue

                units = tag_data.get("units", {})
                # Try USD first, then shares, then USD/shares
                for unit_key in ["USD", "shares", "USD/shares", "pure"]:
                    if unit_key in units:
                        val = extract_annual_value(units[unit_key], year)
                        if val is not None:
                            extracted[internal_field] = val
                            break
                if internal_field in extracted:
                    break

        # 過少計上修正 (2026-07-11): RevenueFromContractWithCustomer* は ASC606 契約収益のみで、
        # 保険料・リース等の非契約収益 (RevenueNotFromContractWithCustomer) を含まない。
        # 非契約収益が併存する会社に限り総収益タグ Revenues を優先する。
        # 実測: バークシャー FY2024 = 249.7B(RFCWC) + 121.7B(NotFromContract) = 371.4B(Revenues) 完全一致
        if "revenue" in extracted and "RevenueNotFromContractWithCustomer" in usgaap:
            rev_tag = usgaap.get("Revenues") or {}
            total = extract_annual_value(rev_tag.get("units", {}).get("USD", []), year)
            if total and total > extracted["revenue"]:
                extracted["revenue"] = total

        if extracted:
            years_data[year] = extracted

    return years_data


def save_company(ticker: str, company_name: str, years_data: dict):
    """Save converted data to us_xbrl_store/{ticker}_{name}/{year}.json"""
    safe_name = sanitize_name(company_name)
    company_dir = US_XBRL_STORE / f"{ticker}_{safe_name}"
    company_dir.mkdir(parents=True, exist_ok=True)

    for year, data in years_data.items():
        output = {
            "company_code": ticker,
            "fiscal_year": year,
            "data": data,
        }
        outpath = company_dir / f"{year}.json"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description="Convert companyfacts to xbrl_store format")
    parser.add_argument("--ticker", type=str, help="Convert single ticker (e.g., AAPL)")
    parser.add_argument("--sp500", action="store_true", help="S&P 500 only (requires sp500 list)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies")
    args = parser.parse_args()

    print("Loading ticker map...")
    ticker_map = load_ticker_map()
    print(f"  {len(ticker_map)} tickers loaded")

    US_XBRL_STORE.mkdir(parents=True, exist_ok=True)

    if args.ticker:
        # Single company mode
        matches = {cik: info for cik, info in ticker_map.items()
                    if info["ticker"] == args.ticker.upper()}
        if not matches:
            print(f"Ticker {args.ticker} not found")
            sys.exit(1)
        targets = matches
    elif args.sp500:
        # S&P 500 only
        sp500_file = Path(__file__).parent / "sp500.txt"
        if not sp500_file.exists():
            print("sp500.txt not found. Run sp500_tickers.py first.")
            sys.exit(1)
        with open(sp500_file, "r") as f:
            sp500_tickers = {line.strip().upper() for line in f if line.strip()}
        targets = {cik: info for cik, info in ticker_map.items()
                    if info["ticker"] in sp500_tickers}
        print(f"  S&P 500 filter: {len(targets)} matches")
    else:
        targets = ticker_map

    if args.limit and args.limit > 0:
        targets = dict(list(targets.items())[:args.limit])

    total = len(targets)
    print(f"Converting {total} companies...")

    success = 0
    empty = 0
    errors = 0

    for i, (cik, info) in enumerate(targets.items()):
        ticker = info["ticker"]
        title = info["title"]

        try:
            years_data = convert_company(cik, ticker, title)
            if years_data:
                save_company(ticker, title, years_data)
                success += 1
            else:
                empty += 1
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"  ERROR {ticker}: {e}")

        if (i + 1) % 500 == 0 or (i + 1) == total:
            print(f"  {i + 1}/{total} done (success={success}, empty={empty}, errors={errors})")

    print(f"\nDone: {success} converted, {empty} empty, {errors} errors")


if __name__ == "__main__":
    main()
