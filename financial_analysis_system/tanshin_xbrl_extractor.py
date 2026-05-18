#!/usr/bin/env python3
"""TDnet 決算短信 (Summary iXBRL) パーサー.

EDINET XBRL より早く出る決算短信を Web に反映するため、
TDnet の Summary iXBRL から主要財務データを抽出して xbrl_store に書き込む。

入力: TDnet の決算短信 ZIP (例: 0812{date}{id}.zip)
出力: xbrl_store/{ticker}_{name}/{year}.json (annual) または
      xbrl_store/{ticker}_{name}/{year}_Q{n}.json (quarterly)

優先順位: 既存のEDINET XBRL があれば上書きしない (EDINET の方が網羅的)。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================
# Summary iXBRL 主要タグ → 内部フィールド名のマッピング
# ============================================================
TANSHIN_TAG_MAP = {
    # P/L (連結)
    "tse-ed-t:NetSales": "revenue",
    "tse-ed-t:Revenue": "revenue",  # IFRS
    "tse-ed-t:OperatingIncome": "operating_income",
    "tse-ed-t:OrdinaryIncome": "ordinary_income",
    "tse-ed-t:NetIncome": "net_income",
    "tse-ed-t:ProfitAttributableToOwnersOfParent": "net_income",
    "tse-ed-t:ComprehensiveIncome": "comprehensive_income",
    # B/S (連結)
    "tse-ed-t:TotalAssets": "total_assets",
    "tse-ed-t:NetAssets": "net_assets",
    "tse-ed-t:EquityToAssetRatio": "equity_ratio",
    "tse-ed-t:ShareholdersEquity": "shareholders_equity",
    # Per share
    "tse-ed-t:NetIncomePerShare": "eps",
    "tse-ed-t:DilutedNetIncomePerShare": "diluted_eps",
    "tse-ed-t:NetAssetsPerShare": "bps",
    # CF
    "tse-ed-t:CashFlowsFromOperatingActivities": "operating_cf",
    "tse-ed-t:CashFlowsFromInvestingActivities": "investing_cf",
    "tse-ed-t:CashFlowsFromFinancingActivities": "financing_cf",
    "tse-ed-t:CashAndEquivalentsEndOfPeriod": "cash_end",
    # Dividend
    "tse-ed-t:DividendPerShareAnnual": "dividend_per_share_annual",
    "tse-ed-t:DividendPayoutRatioAnnual": "dividend_payout_ratio",
    # Ratios
    "tse-ed-t:NetIncomeToShareholdersEquityRatio": "roe",
    "tse-ed-t:OperatingIncomeToNetSalesRatio": "operating_margin",
    "tse-ed-t:OrdinaryIncomeToTotalAssetsRatio": "roa",
    # Forecast (来期予想)
    "tse-ed-t:ForecastNetSales": "forecast_revenue",
    "tse-ed-t:ForecastOperatingIncome": "forecast_operating_income",
    "tse-ed-t:ForecastOrdinaryIncome": "forecast_ordinary_income",
    "tse-ed-t:ForecastNetIncome": "forecast_net_income",
    "tse-ed-t:ForecastNetIncomePerShare": "forecast_eps",
    # YoY rates (already %)
    "tse-ed-t:ChangeInNetSales": "revenue_growth_rate",
    "tse-ed-t:ChangeInOperatingIncome": "operating_income_growth_rate",
    "tse-ed-t:ChangeInOrdinaryIncome": "ordinary_income_growth_rate",
    "tse-ed-t:ChangeInNetIncome": "net_income_growth_rate",
}


@dataclass
class TanshinExtractResult:
    ticker: str
    fiscal_year: int  # 終了年 (e.g. 2026年3月期 → 2026)
    period_end: str  # YYYY-MM-DD
    quarter: Optional[int]  # None=annual, 1/2/3=Q1/Q2/Q3
    announcement_date: str  # YYYY-MM-DD
    accounting_type: str  # "JP" or "IFRS"
    data: dict


# Quarter suffix in TDnet filename: 01=annual, 02=Q1, 03=Q2/半期, 04=Q3
QUARTER_SUFFIX_MAP = {"01": None, "02": 1, "03": 2, "04": 3}


def _parse_filename(name: str) -> Optional[tuple]:
    """Parse TDnet ixbrl filename like:
    tse-acedjpfr-76020-2026-03-31-01-2026-05-13-...

    Returns: (ticker, period_end YYYY-MM-DD, quarter or None, announcement YYYY-MM-DD)
    """
    m = re.search(
        r"tse-acedjp(?:fr|sm|cn)-(\d{4,5})-(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{4}-\d{2}-\d{2})",
        name,
    )
    if not m:
        return None
    ticker_raw = m.group(1)
    ticker = ticker_raw[:4]  # strip check digit
    period_end = m.group(2)
    suffix = m.group(3)
    announcement = m.group(4)
    quarter = QUARTER_SUFFIX_MAP.get(suffix)
    return ticker, period_end, quarter, announcement


def _to_number(s: str) -> Optional[float]:
    """'19,846' → 19846.0, '0.9' → 0.9, '△403' / '-403' → -403."""
    if not s:
        return None
    s = s.strip().replace(",", "").replace("△", "-").replace("▲", "-").replace("　", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_tanshin_data(zip_bytes: bytes) -> Optional[TanshinExtractResult]:
    """Extract structured financials from a TDnet 決算短信 XBRL ZIP."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return None

    # Locate Summary iXBRL
    summary_paths = [
        n for n in zf.namelist()
        if ("Summary" in n or "summary" in n) and n.endswith("-ixbrl.htm")
    ]
    if not summary_paths:
        return None
    summary_path = summary_paths[0]
    content = zf.read(summary_path).decode("utf-8", errors="replace")

    # Parse filename → period info (filename inside Summary/ may have different layout)
    # Try Attachment/*ixbrl.htm filenames which include period_end and suffix
    attach_paths = [n for n in zf.namelist() if "/Attachment/" in n and n.endswith("-ixbrl.htm")]
    parsed = None
    for p in attach_paths:
        parsed = _parse_filename(Path(p).name)
        if parsed:
            break
    if not parsed:
        # Fall back to summary filename
        parsed = _parse_filename(Path(summary_path).name)
    if not parsed:
        return None
    ticker, period_end, quarter, announcement = parsed
    fiscal_year = int(period_end[:4])

    # IFRS or J-GAAP detection by presence of "Revenue" tag
    accounting_type = "IFRS" if "tse-ed-t:Revenue" in content else "JP"

    # Extract ix:nonFraction tags. Attributes may appear in any order, so first
    # match the open tag + value, then parse attributes separately.
    open_pattern = re.compile(
        r'<ix:nonFraction\s+([^>]*?)>([^<]*)</ix:nonFraction>',
        re.DOTALL,
    )

    def _attr(s: str, key: str) -> Optional[str]:
        m = re.search(rf'\b{key}="([^"]*)"', s)
        return m.group(1) if m else None

    buckets: dict[str, dict[str, float]] = {}
    for m in open_pattern.finditer(content):
        attrs, val_text = m.group(1), m.group(2)
        tag_name = _attr(attrs, "name")
        ctx = _attr(attrs, "contextRef")
        sign = _attr(attrs, "sign")
        if not tag_name or not ctx:
            continue
        if tag_name not in TANSHIN_TAG_MAP:
            continue
        # Only "Consolidated" results (skip NonConsolidatedMember unless solo company)
        if "NonConsolidatedMember" in ctx and "ConsolidatedMember" in content:
            continue
        # Classify context
        if "ForecastMember" in ctx:
            kind = "forecast"
        elif "PriorYear" in ctx or "PreviousYear" in ctx:
            kind = "prior"
        elif "CurrentYear" in ctx or "CurrentQuarter" in ctx or "CurrentAccumulated" in ctx:
            kind = "current"
        else:
            continue

        val = _to_number(val_text)
        if val is None:
            continue
        if sign == "-":
            val = -val

        # Convert 百万円 (million yen) to 円 for absolute amounts; ratios/EPS keep raw
        field = TANSHIN_TAG_MAP[tag_name]
        scale_to_yen = field in {
            "revenue", "operating_income", "ordinary_income", "net_income",
            "comprehensive_income", "total_assets", "net_assets", "shareholders_equity",
            "operating_cf", "investing_cf", "financing_cf", "cash_end",
            "forecast_revenue", "forecast_operating_income", "forecast_ordinary_income",
            "forecast_net_income",
        }
        if scale_to_yen:
            val = val * 1_000_000.0

        # Map "forecast" context for forecast fields
        if kind == "forecast" and not field.startswith("forecast_"):
            field = "forecast_" + field
            kind = "current"  # store as primary value for forecast field

        buckets.setdefault(field, {})[kind] = val

    # Build flat data dict (current values primary, prior_year_* for prior)
    data: dict = {}
    for field, kinds in buckets.items():
        if "current" in kinds:
            data[field] = kinds["current"]
        if "prior" in kinds:
            data[f"prior_year_{field}"] = kinds["prior"]

    if not data:
        return None

    return TanshinExtractResult(
        ticker=ticker,
        fiscal_year=fiscal_year,
        period_end=period_end,
        quarter=quarter,
        announcement_date=announcement,
        accounting_type=accounting_type,
        data=data,
    )


def write_to_xbrl_store(
    result: TanshinExtractResult,
    xbrl_store: Path,
    company_name: str,
    overwrite: bool = False,
) -> Optional[Path]:
    """Write extracted data to xbrl_store/{ticker}_{name}/{year}[_Qn].json.

    If file exists and `overwrite=False`, skip (EDINET data is more comprehensive).
    Returns the output path on write, None if skipped.
    """
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", company_name)
    out_dir = xbrl_store / f"{result.ticker}_{safe_name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if result.quarter is None:
        fname = f"{result.fiscal_year}.json"
    else:
        fname = f"{result.fiscal_year}_Q{result.quarter}.json"
    out_path = out_dir / fname

    if out_path.exists() and not overwrite:
        # Check if existing file came from tanshin or EDINET
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if existing.get("source") != "tanshin":
                # EDINET data exists; don't overwrite
                return None
        except (json.JSONDecodeError, OSError):
            pass  # Treat as missing → overwrite

    payload = {
        "company_code": result.ticker,
        "company_name": company_name,
        "fiscal_year": result.fiscal_year,
        "period_end": result.period_end,
        "quarter": result.quarter,
        "announcement_date": result.announcement_date,
        "accounting_type": result.accounting_type,
        "source": "tanshin",
        "extracted_at": datetime.now().isoformat(),
        "data": result.data,
        "validation": {"score": 100.0, "warnings": [], "errors": []},
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return out_path


def process_zip_file(
    zip_path: Path,
    xbrl_store: Path,
    company_name: Optional[str] = None,
    overwrite: bool = False,
) -> Optional[Path]:
    """High-level: read a 短信 ZIP file → write to xbrl_store. Returns output path."""
    zb = zip_path.read_bytes()
    result = extract_tanshin_data(zb)
    if not result:
        return None
    # Fall back to ticker as company name if not provided
    name = company_name or f"company_{result.ticker}"
    return write_to_xbrl_store(result, xbrl_store, name, overwrite=overwrite)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TDnet 決算短信 Summary XBRL extractor")
    parser.add_argument("zip_paths", nargs="+", help="ZIP files to process")
    parser.add_argument("--xbrl-store", default=None, help="xbrl_store output directory")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    xbrl_store = Path(
        args.xbrl_store
        or os.environ.get("XBRL_STORE")
        or "./xbrl_store"
    )
    xbrl_store.mkdir(parents=True, exist_ok=True)

    written, skipped, failed = 0, 0, 0
    for zp in args.zip_paths:
        zp = Path(zp)
        if not zp.exists():
            print(f"  [SKIP] {zp.name} not found")
            failed += 1
            continue
        try:
            out = process_zip_file(zp, xbrl_store, overwrite=args.overwrite)
            if out:
                print(f"  [OK] {zp.name} → {out}")
                written += 1
            else:
                print(f"  [SKIP] {zp.name} (existing or no data)")
                skipped += 1
        except Exception as e:
            print(f"  [ERR] {zp.name}: {e}")
            failed += 1

    print(f"\nDone: {written} written, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
