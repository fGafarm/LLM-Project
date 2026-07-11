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
    # P/L (連結) — J-GAAP
    "tse-ed-t:NetSales": "revenue",
    "tse-ed-t:OperatingIncome": "operating_income",
    "tse-ed-t:OrdinaryIncome": "ordinary_income",
    "tse-ed-t:NetIncome": "net_income",
    "tse-ed-t:ProfitAttributableToOwnersOfParent": "net_income",
    "tse-ed-t:ComprehensiveIncome": "comprehensive_income",
    # P/L (連結) — IFRS
    "tse-ed-t:SalesIFRS": "revenue",
    "tse-ed-t:Revenue": "revenue",
    "tse-ed-t:RevenueIFRS": "revenue",
    "tse-ed-t:OperatingIncomeIFRS": "operating_income",
    "tse-ed-t:ProfitBeforeTaxIFRS": "ordinary_income",
    "tse-ed-t:ProfitIFRS": "net_income_total",
    "tse-ed-t:ProfitAttributableToOwnersOfParentIFRS": "net_income",
    "tse-ed-t:ComprehensiveIncomeIFRS": "comprehensive_income",
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
    "tse-ed-t:NetIncomeToShareholdersEquityRatioIFRS": "roe",
    "tse-ed-t:OperatingIncomeToNetSalesRatio": "operating_margin",
    "tse-ed-t:OperatingIncomeToSalesRatioIFRS": "operating_margin",
    "tse-ed-t:OrdinaryIncomeToTotalAssetsRatio": "roa",
    "tse-ed-t:ProfitBeforeTaxToTotalAssetsRatioIFRS": "roa",
    # Forecast (来期予想)
    "tse-ed-t:ForecastNetSales": "forecast_revenue",
    "tse-ed-t:ForecastOperatingIncome": "forecast_operating_income",
    "tse-ed-t:ForecastOrdinaryIncome": "forecast_ordinary_income",
    "tse-ed-t:ForecastNetIncome": "forecast_net_income",
    "tse-ed-t:ForecastNetIncomePerShare": "forecast_eps",
    # YoY rates (already %) - J-GAAP
    "tse-ed-t:ChangeInNetSales": "revenue_growth_rate",
    "tse-ed-t:ChangeInOperatingIncome": "operating_income_growth_rate",
    "tse-ed-t:ChangeInOrdinaryIncome": "ordinary_income_growth_rate",
    "tse-ed-t:ChangeInNetIncome": "net_income_growth_rate",
    # YoY rates - IFRS
    "tse-ed-t:ChangeInSalesIFRS": "revenue_growth_rate",
    "tse-ed-t:ChangeInOperatingIncomeIFRS": "operating_income_growth_rate",
    "tse-ed-t:ChangeInProfitBeforeTaxIFRS": "ordinary_income_growth_rate",
}


@dataclass
class TanshinExtractResult:
    ticker: str
    fiscal_year: int  # 会計年度の終了年 (e.g. 2026年3月期 → 2026)
    period_end: str  # YYYY-MM-DD (通期=年度末 / 四半期・中間=当該期間末)
    quarter: Optional[int]  # None=annual, 1/2/3=Q1/Q2(中間)/Q3
    announcement_date: str  # YYYY-MM-DD
    accounting_type: str  # "JP" or "IFRS"
    data: dict
    submission_no: Optional[int] = None  # 提出連番 (01=初回, 02=1回目の訂正, ...)
    source_file: Optional[str] = None  # 元 ZIP ファイル名 (監査用)


# ------------------------------------------------------------------
# TDnet ファイル名の form code:
#   tse-{p}{c}ed{gaap}{kind}-{code}-{period_end}-{seq}-{filing_date}-...
#     p: a=通期決算短信, s=中間(半期)決算短信, q=四半期決算短信 (Q1/Q3, 旧制度のQ2含む)
#     c: c=連結, n=非連結
#     gaap: jp / if / us
#     kind: fr=Attachment, sm/sy=Summary 等
#
#   ⚠ {seq} (period_end と発表日の間の2桁) は「提出連番」であり四半期コードではない。
#     訂正短信で 02, 03... と増える。ここを四半期と誤読すると通期の訂正短信が
#     偽の {year}_Q1.json として store を汚染する (2026-07 P1事故の根因)。
#     四半期番号はファイル名からは判別できないため Summary 内の xbrli:context
#     (CurrentAccumulatedQn) から解決し、判別不能なら取り込まない (誤値より欠損)。
# ------------------------------------------------------------------
_FORM_PERIOD_MAP = {"a": "annual", "s": "semi", "q": "quarterly"}


def _parse_filename(name: str) -> Optional[tuple]:
    """Parse TDnet Attachment iXBRL filename like:
    tse-acedjpfr-76020-2026-03-31-01-2026-05-13-... (通期 J-GAAP, 初回)
    tse-acediffr-45020-2026-03-31-02-2026-06-05-... (通期 IFRS, 訂正=連番02)
    tse-qnedjpfr-130A0-2026-03-31-01-2026-05-11-... (四半期, period_end=四半期末)
    tse-scedjpfr-14380-2026-03-31-01-2026-05-15-... (中間)

    Returns: (ticker, period_end YYYY-MM-DD, form_period, submission_no,
              announcement YYYY-MM-DD)
    form_period: "annual" | "semi" | "quarterly"
    """
    m = re.search(
        r"tse-([asq])([cn])ed(?:jp|if|us)[a-z]{2}-([0-9A-Z]{4,5})-(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{4}-\d{2}-\d{2})",
        name,
    )
    if not m:
        return None
    form_period = _FORM_PERIOD_MAP[m.group(1)]
    ticker_raw = m.group(3)
    ticker = ticker_raw[:4]  # strip check digit (alpha-numeric tickers like 130A0 → 130A)
    period_end = m.group(4)
    submission_no = int(m.group(5))
    announcement = m.group(6)
    return ticker, period_end, form_period, submission_no, announcement


def _parse_summary_only_filename(name: str) -> Optional[tuple]:
    """Parse Summary-only filename like:
    tse-acedjpsm-59380-20260518359380-ixbrl.htm
    tse-acedifsm-59380-20260518359380-ixbrl.htm
    tse-qnedjpsm-130A0-202605113130A0-ixbrl.htm (alpha-numeric ticker)
    tse-scedjpsy-13830-20260615313830-ixbrl.htm (中間は kind=sy の場合あり)

    Returns: (ticker, form_period, announcement YYYY-MM-DD)
    """
    m = re.search(
        r"tse-([asq])([cn])ed(?:jp|if|us)s[a-z]-([0-9A-Z]{4,5})-(\d{8})[0-9A-Z]+-ixbrl",
        name,
    )
    if not m:
        return None
    form_period = _FORM_PERIOD_MAP[m.group(1)]
    ticker = m.group(3)[:4]
    dt = m.group(4)
    announcement = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"
    return ticker, form_period, announcement


def _quarter_from_duration(start: str, end: str) -> Optional[int]:
    """累計期間の日数から四半期を推定 (Q1≈3ヶ月, Q2≈6ヶ月, Q3≈9ヶ月)."""
    try:
        days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    except ValueError:
        return None
    if 80 <= days <= 100:
        return 1
    if 170 <= days <= 195:
        return 2
    if 260 <= days <= 290:
        return 3
    return None


def _extract_context_periods(content: str) -> dict:
    """Summary 内の <xbrli:context> から期間情報を抽出.

    Returns dict:
      year_end:    CurrentYearDuration の endDate。
                   通期短信では当期末 / 四半期・中間短信では会計年度末 (通期予想 context)。
      acc_end:     CurrentAccumulatedQn / CurrentYTDDuration の endDate (四半期・中間の期間末)。
      acc_quarter: 上記 context id から判定した 1/2/3。id に Qn が無い (YTD のみ) 場合は
                   期間の日数から推定。判別不能なら None。
    """
    context_pattern = re.compile(
        r'<xbrli:context\s+id="([^"]+)">(.*?)</xbrli:context>',
        re.DOTALL,
    )
    start_pattern = re.compile(r"<xbrli:startDate>(\d{4}-\d{2}-\d{2})</xbrli:startDate>")
    end_pattern = re.compile(r"<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>")

    year_end = None
    acc_end = None
    acc_start = None
    acc_quarter = None
    for m in context_pattern.finditer(content):
        cid, body = m.group(1), m.group(2)
        em = end_pattern.search(body)
        if not em:
            continue
        end_date = em.group(1)
        if cid.startswith("Prior"):
            continue
        if "CurrentAccumulatedQ1" in cid:
            acc_end, acc_quarter = end_date, 1
        elif "CurrentAccumulatedQ2" in cid:
            acc_end, acc_quarter = end_date, 2
        elif "CurrentAccumulatedQ3" in cid:
            acc_end, acc_quarter = end_date, 3
        elif "CurrentYTDDuration" in cid and acc_quarter is None:
            sm_ = start_pattern.search(body)
            acc_end = end_date
            acc_start = sm_.group(1) if sm_ else None
        elif "CurrentYearDuration" in cid and year_end is None:
            year_end = end_date

    if acc_quarter is None and acc_end and acc_start:
        acc_quarter = _quarter_from_duration(acc_start, acc_end)

    return {"year_end": year_end, "acc_end": acc_end, "acc_quarter": acc_quarter}


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

    # Parse filename → period info. Strategy:
    # 1. Try Attachment iXBRLs (have period_end-seq-announcement in filename)
    # 2. Fallback: parse Summary filename (ticker + form + announcement only)
    # いずれの場合も、四半期番号・会計年度は Summary 内の <xbrli:context> から
    # 解決する (ファイル名の2桁は提出連番であり四半期コードではない)。
    attach_paths = [n for n in zf.namelist() if "/Attachment/" in n and n.endswith("-ixbrl.htm")]
    # 訂正 ZIP は連番のみ増えて日付が当初発表日のまま残る (例: -02-2026-05-11-)。
    # 複数世代の Attachment が同居する可能性に備え、(発表日, 連番) 最大を採用。
    parses = [p for p in (_parse_filename(Path(n).name) for n in attach_paths) if p]
    parsed = max(parses, key=lambda t: (t[4], t[3])) if parses else None

    submission_no: Optional[int] = None
    period_end: Optional[str] = None
    if parsed:
        ticker, period_end, form_period, submission_no, announcement = parsed
    else:
        # Summary-only: derive ticker+form+announcement from Summary filename.
        sum_parse = _parse_summary_only_filename(Path(summary_path).name)
        if not sum_parse:
            return None
        ticker, form_period, announcement = sum_parse

    ctx_periods = _extract_context_periods(content)

    if form_period == "annual":
        quarter = None
        if period_end is None:
            period_end = ctx_periods.get("year_end")
        if not period_end:
            return None
        fiscal_year = int(period_end[:4])
    else:
        # 中間・四半期: 会計年度末は CurrentYearDuration (通期予想 context) の
        # endDate から取得。四半期番号は中間なら常に 2、四半期短信は
        # CurrentAccumulatedQn context から。どちらかが判別できなければ
        # 取り込まない (誤値より欠損)。
        fy_end = ctx_periods.get("year_end")
        if form_period == "semi":
            quarter = 2
        else:
            quarter = ctx_periods.get("acc_quarter")
        if quarter is None or not fy_end:
            return None
        fiscal_year = int(fy_end[:4])
        if period_end is None:
            period_end = ctx_periods.get("acc_end")
        if not period_end:
            return None

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

    # Detect if Consolidated contexts exist (avoid "NonConsolidatedMember" substring trap)
    has_consolidated = bool(re.search(r"(?<!Non)ConsolidatedMember", content))

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
        if "NonConsolidatedMember" in ctx and has_consolidated:
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
        submission_no=submission_no,
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
            # 訂正短信ガード: 既存 tanshin がより新しい発表・連番なら巻き戻さない。
            # 注意: 訂正 ZIP の Attachment 日付は当初発表日のまま連番だけ増えるため
            # (例: -02-2026-05-11-)、発表日単独でなく (発表日, 連番) で比較する。
            existing_ann = existing.get("announcement_date") or ""
            existing_seq = existing.get("submission_no") or 0
            new_seq = result.submission_no or 0
            if (existing_ann, existing_seq) > (result.announcement_date, new_seq):
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
        "submission_no": result.submission_no,
        "source_file": result.source_file,
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
    result.source_file = zip_path.name
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
