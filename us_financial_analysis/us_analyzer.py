"""
US Financial Analysis System - Main Analyzer
日本版 Run_integrated_v10_2.py のUS版。

日本版の6400行の試行錯誤から学んだ核心:
- /no_think: Qwen3は思考モード有効だと出力の大半が<think>内部思考に消費される
- パラメータ分離: 抽出用=temp0.0/ctx8192/predict3000, レポート用=temp0.3/ctx32000/predict12000
- 15ルールプロンプト: 数値捏造禁止/ソース帰属/用語統一/矛盾禁止/推定禁止
- 構造化QA: question_id + focus で回答を後処理可能に
- XBRL数値チェック: レポート生成後にXBRL値と照合
- 回答拒否検出: LLMが「できません」系回答を返したらリトライ
- CAGR/YoY/Net Debt等: スコアボードの充実がLLM精度を大幅に上げる

Usage:
    python us_analyzer.py --ticker AAPL
    python us_analyzer.py --ticker AAPL --year 2024
    python us_analyzer.py --list top50.txt
    python us_analyzer.py --ticker AAPL --scoreboard-only
"""

import json
import sys
import re
import argparse
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

from config import (
    US_XBRL_STORE, FILING_DIR, OUTPUT_DIR,
    OLLAMA_URL, OLLAMA_MODEL, OLLAMA_NUM_CTX, COMPANY_TICKERS_FILE
)
from section_splitter import (
    split_10k_sections, get_section_stats,
    SECTION_QUESTIONS, SECTION_PRIORITY
)


# ============================================================
# LLM Call Profiles (日本版のパラメータ分離を再現)
# ============================================================
# 日本版の教訓: 全呼び出しで同じパラメータ → 抽出時に余計な文章、レポート時に出力切れ
CALL_PROFILES = {
    "qa": {
        "temperature": 0.0,     # 決定論的 → 安定した回答
        "num_ctx": 8192,        # QA質問は短いテキスト → 小コンテキストでOK
        "num_predict": 3000,    # QA回答は短い
    },
    "report": {
        "temperature": 0.3,     # 若干の創造性 → 自然なレポート文
        "num_ctx": 32000,       # QA結果+スコアボード全体を含む
        "num_predict": 12000,   # 長いレポート出力
    },
}

# 回答拒否パターン (日本版 L3911-L3934)
REFUSAL_PATTERNS = [
    "I cannot", "I can't", "I'm unable", "I don't have access",
    "not provided", "no information available", "cannot determine",
    "insufficient data", "beyond the scope",
]


# ============================================================
# CIK-based Ticker Resolution
# ============================================================
# SEC uses multiple tickers per CIK (e.g., JPM→VYLD, GS→GSCE).
# Build a lookup: market_ticker → all SEC tickers sharing the same CIK.

def _build_ticker_cik_map() -> dict[str, list[str]]:
    """Build market_ticker → [all SEC tickers for same CIK] mapping."""
    import json as _json
    try:
        with open(COMPANY_TICKERS_FILE, "r", encoding="utf-8") as f:
            raw = _json.load(f)
    except Exception:
        return {}

    cik_to_tickers: dict[int, list[str]] = {}
    for entry in raw.values():
        cik = int(entry.get("cik_str", 0))
        ticker = entry.get("ticker", "")
        if cik and ticker:
            cik_to_tickers.setdefault(cik, []).append(ticker)

    # For each ticker, provide all sibling tickers (same CIK)
    result: dict[str, list[str]] = {}
    for cik, tickers in cik_to_tickers.items():
        for t in tickers:
            siblings = [s for s in tickers if s != t]
            if siblings:
                result[t] = siblings
    # Also handle dot→hyphen normalization (BRK.B → BRK-B)
    for t in list(result.keys()):
        dot_form = t.replace("-", ".")
        if dot_form != t and dot_form not in result:
            result[dot_form] = [t] + result[t]
    return result

_TICKER_CIK_MAP = _build_ticker_cik_map()


# ============================================================
# Data Loading
# ============================================================

def _resolve_ticker_dir(base_dir: Path, ticker: str) -> list[Path]:
    """Resolve ticker to directory, trying CIK-based aliases if needed."""
    matches = list(base_dir.glob(f"{ticker}_*"))
    if not matches:
        # Try dot→hyphen normalization
        alt = ticker.replace(".", "-")
        if alt != ticker:
            matches = list(base_dir.glob(f"{alt}_*"))
        # Try all SEC tickers sharing the same CIK
        if not matches:
            for alias in _TICKER_CIK_MAP.get(ticker, []):
                matches = list(base_dir.glob(f"{alias}_*"))
                if matches:
                    break
    return matches


def load_xbrl_data(ticker: str, year: int | None = None) -> dict[int, dict]:
    """Load xbrl_store data for a company. Returns {year: {field: value}}."""
    matches = _resolve_ticker_dir(US_XBRL_STORE, ticker)
    if not matches:
        return {}

    company_dir = matches[0]
    years_data = {}

    for json_file in sorted(company_dir.glob("*.json")):
        try:
            fy = int(json_file.stem)
        except ValueError:
            continue

        if year and fy != year:
            continue

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        years_data[fy] = data.get("data", {})

    return years_data


def load_10k_text(ticker: str, year: int) -> str | None:
    """Load 10-K filing text for a specific year."""
    matches = _resolve_ticker_dir(FILING_DIR, ticker)
    if not matches:
        return None

    company_dir = matches[0]
    text_file = company_dir / f"{year}_10K.txt"

    if not text_file.exists():
        return None

    with open(text_file, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# Derived Metrics (日本版 L1882-L2113 相当、大幅拡充)
# ============================================================

def calculate_derived_metrics(data: dict, prev_data: dict | None = None) -> dict:
    """
    Calculate derived financial metrics from raw XBRL data.
    日本版の教訓: EBITDA, FCF, Net Debt, ROIC, CAGR等がないとLLMが推定値を使う
    """
    metrics = {}

    rev = data.get("revenue")
    gp = data.get("gross_profit")
    oi = data.get("operating_income")
    ni = data.get("net_income") or data.get("net_income_attributable")

    # OI fallback: many companies don't report OperatingIncomeLoss in XBRL
    # Priority: 1) GP-SGA-R&D, 2) Rev-COGS-SGA-R&D, 3) income_before_tax as proxy
    if oi is None:
        sga = data.get("sga_expense")
        rd_val = data.get("research_development")
        cogs = data.get("cost_of_sales")
        if gp is not None and sga is not None:
            oi = gp - sga - (rd_val or 0)
            data["operating_income"] = oi
        elif rev is not None and cogs is not None and sga is not None:
            oi = rev - cogs - sga - (rd_val or 0)
            data["operating_income"] = oi
        elif data.get("income_before_tax") is not None:
            # Banks/oil: use pre-tax income as OI proxy (better than None)
            oi = data["income_before_tax"]
            data["operating_income"] = oi
    ta = data.get("total_assets")
    te = data.get("total_equity")
    tl = data.get("total_liabilities")
    ca = data.get("current_assets")
    cl = data.get("current_liabilities")
    ocf = data.get("operating_cash_flow")
    icf = data.get("investing_cash_flow")
    capex = data.get("capex")
    da = data.get("depreciation_amortization")
    div = data.get("dividends_paid")
    shares = data.get("shares_outstanding")
    ltd = data.get("long_term_debt")
    cash = data.get("cash_and_equivalents")
    ar = data.get("accounts_receivable")
    inv = data.get("inventories")
    ap = data.get("accounts_payable")
    rd = data.get("research_development")
    buyback = data.get("share_repurchases")

    # === Profitability ===
    if rev and rev != 0:
        if gp is not None:
            metrics["gross_margin"] = round(gp / rev * 100, 2)
        if oi is not None:
            metrics["operating_margin"] = round(oi / rev * 100, 2)
        if ni is not None:
            metrics["net_margin"] = round(ni / rev * 100, 2)
        if ocf is not None:
            metrics["ocf_margin"] = round(ocf / rev * 100, 2)

    if ni and te and te != 0:
        metrics["roe"] = round(ni / te * 100, 2)
    if ni and ta and ta != 0:
        metrics["roa"] = round(ni / ta * 100, 2)

    # ROIC (日本版で追加された指標)
    if oi and ta and tl:
        invested_capital = ta - (cl or 0) + (ltd or 0)
        if invested_capital > 0:
            tax_rate = 0.21  # US federal statutory rate
            nopat = oi * (1 - tax_rate)
            metrics["roic"] = round(nopat / invested_capital * 100, 2)

    # === Safety ===
    if te and ta and ta != 0:
        metrics["equity_ratio"] = round(te / ta * 100, 2)
    if tl and te and te != 0:
        metrics["debt_equity_ratio"] = round(tl / te, 2)
    if ca and cl and cl != 0:
        metrics["current_ratio"] = round(ca / cl, 2)

    # === Cash Flow ===
    if ocf is not None and capex is not None:
        metrics["free_cash_flow"] = ocf - abs(capex)
    elif ocf is not None and icf is not None:
        # 日本版の教訓: FCF = 営業CF + 投資CF も有効な定義
        metrics["free_cash_flow"] = ocf + icf

    if oi is not None and da is not None:
        metrics["ebitda"] = oi + da
        if rev and rev != 0:
            metrics["ebitda_margin"] = round((oi + da) / rev * 100, 2)

    # Net Debt (日本版で重要な指標)
    if ltd is not None and cash is not None:
        net_debt = ltd - cash
        metrics["net_debt"] = net_debt
        ebitda = metrics.get("ebitda")
        if ebitda and ebitda > 0:
            metrics["net_debt_ebitda"] = round(net_debt / ebitda, 2)

    # === Per Share ===
    if ni and shares and shares != 0:
        metrics["eps_calculated"] = round(ni / shares, 2)
        if te:
            metrics["bps"] = round(te / shares, 2)

    # === Payout & Shareholder Return ===
    if div and ni and ni != 0:
        metrics["payout_ratio"] = round(abs(div) / ni * 100, 2)
    total_return = (abs(div) if div else 0) + (abs(buyback) if buyback else 0)
    if total_return > 0:
        metrics["total_shareholder_return"] = total_return

    # === Working Capital / Efficiency (日本版の運転資本分析) ===
    if rev and rev != 0:
        if ar is not None:
            metrics["receivables_days"] = round(ar / rev * 365, 1)
        if inv is not None:
            cost = data.get("cost_of_sales", rev)
            metrics["inventory_days"] = round(inv / cost * 365, 1)
        if ap is not None:
            cost = data.get("cost_of_sales", rev)
            metrics["payables_days"] = round(ap / cost * 365, 1)

    # CCC (Cash Conversion Cycle)
    rd_days = metrics.get("receivables_days")
    id_days = metrics.get("inventory_days")
    pd_days = metrics.get("payables_days")
    if rd_days is not None and id_days is not None and pd_days is not None:
        metrics["ccc"] = round(rd_days + id_days - pd_days, 1)

    # Asset turnover
    if rev and ta and ta != 0:
        metrics["asset_turnover"] = round(rev / ta, 2)

    # === CapEx / Investment ===
    if capex is not None and da is not None and da != 0:
        metrics["capex_depreciation_ratio"] = round(abs(capex) / da, 2)
    if capex is not None and rev and rev != 0:
        metrics["capex_intensity"] = round(abs(capex) / rev * 100, 2)

    # === YoY Changes (前年比、日本版で重要) ===
    if prev_data:
        prev_rev = prev_data.get("revenue")
        prev_oi = prev_data.get("operating_income")
        prev_ni = prev_data.get("net_income") or prev_data.get("net_income_attributable")
        if rev and prev_rev and prev_rev != 0:
            metrics["revenue_yoy"] = round((rev - prev_rev) / abs(prev_rev) * 100, 1)
        if oi and prev_oi and prev_oi != 0:
            metrics["operating_income_yoy"] = round((oi - prev_oi) / abs(prev_oi) * 100, 1)
        if ni and prev_ni and prev_ni != 0:
            metrics["net_income_yoy"] = round((ni - prev_ni) / abs(prev_ni) * 100, 1)

    # === PIK / Credit Stress Metrics (日米統一スキーマ) ===
    interest_expense = data.get("interest_expense")
    interest_paid_cf = data.get("interest_paid_cf")
    pik_direct = data.get("paid_in_kind_interest")
    noncash_int = data.get("noncash_interest_expense")
    int_receivable = data.get("interest_receivable")
    int_income_pl = data.get("interest_income")
    short_debt = data.get("short_term_debt") or 0
    cur_portion_ltd = data.get("current_portion_long_term_debt") or 0
    long_debt = data.get("long_term_debt") or 0
    total_interest_bearing_debt = short_debt + cur_portion_ltd + long_debt

    # A1: Cash Interest Coverage Ratio = |CF interest paid| / PL interest expense
    if interest_expense and interest_paid_cf is not None and interest_expense > 0:
        metrics["cash_interest_coverage_ratio"] = round(abs(interest_paid_cf) / interest_expense, 3)
        noncash_component = interest_expense - abs(interest_paid_cf)
        # A2: Non-cash Interest Ratio
        metrics["noncash_interest_ratio"] = round(noncash_component / interest_expense * 100, 2)
        if noncash_component > 0:
            metrics["pik_interest_abs_derived"] = noncash_component

    # A3: PIK Interest Absolute (prefer direct US-GAAP tag, fallback to derived)
    if pik_direct is not None and pik_direct > 0:
        metrics["pik_interest_abs"] = pik_direct
        metrics["pik_estimation_quality"] = "direct"
    elif noncash_int is not None and noncash_int > 0:
        metrics["pik_interest_abs"] = noncash_int
        metrics["pik_estimation_quality"] = "direct_noncash_tag"
    elif metrics.get("pik_interest_abs_derived"):
        metrics["pik_interest_abs"] = metrics["pik_interest_abs_derived"]
        metrics["pik_estimation_quality"] = "derived"
    elif interest_expense is not None:
        metrics["pik_estimation_quality"] = "unavailable"

    # A4: Accrued Interest Growth (needs prev year)
    if prev_data:
        prev_int_rec = prev_data.get("interest_receivable")
        if int_receivable is not None and prev_int_rec is not None and int_income_pl and int_income_pl > 0:
            delta = int_receivable - prev_int_rec
            metrics["accrued_interest_growth"] = round(delta / int_income_pl * 100, 2)

    # B1: Interest Coverage Ratio = EBIT / Interest Expense
    if oi and interest_expense and interest_expense > 0:
        metrics["interest_coverage_ratio"] = round(oi / interest_expense, 2)

    # B2: Cash ICR = OCF / |CF interest paid|
    if ocf and interest_paid_cf is not None and abs(interest_paid_cf) > 0:
        metrics["cash_icr"] = round(ocf / abs(interest_paid_cf), 2)

    # B3 already computed as net_debt_ebitda above, but extend with broader debt basis
    ebitda = metrics.get("ebitda")
    if ebitda and ebitda > 0 and total_interest_bearing_debt > 0:
        net_debt_broad = total_interest_bearing_debt - (cash or 0)
        metrics["net_debt_ebitda_broad"] = round(net_debt_broad / ebitda, 2)

    # B4: Short-term Debt Ratio = (short + current portion LTD) / total interest-bearing debt
    if total_interest_bearing_debt > 0:
        short_total = short_debt + cur_portion_ltd
        if short_total > 0:
            metrics["short_term_debt_ratio"] = round(short_total / total_interest_bearing_debt * 100, 2)

    # === BDC / CEF Specific Metrics (C1-C5) ===
    inv_income_pik = data.get("investment_income_pik")
    inv_income_total = data.get("investment_income_total") or rev  # BDCでは revenue が投資収益
    # C1: PIK Income / Total Investment Income (BDC warning line: >10%)
    if inv_income_pik is not None and inv_income_total and inv_income_total > 0:
        metrics["pik_income_ratio"] = round(inv_income_pik / inv_income_total * 100, 2)

    fin_rec = data.get("financing_receivable_net")
    fin_rec_nonaccrual = data.get("financing_receivable_nonaccrual")
    allowance_cl = data.get("allowance_for_credit_loss")
    # C3: Credit Loss Coverage = allowance / financing receivables
    if fin_rec and allowance_cl and fin_rec > 0:
        metrics["credit_loss_coverage"] = round(allowance_cl / fin_rec * 100, 2)
    # C3 YoY: Allowance growth
    if prev_data:
        prev_allowance = prev_data.get("allowance_for_credit_loss")
        if allowance_cl and prev_allowance and prev_allowance > 0:
            metrics["allowance_growth_yoy"] = round((allowance_cl - prev_allowance) / prev_allowance * 100, 2)
    # C4: Non-accrual Ratio
    if fin_rec and fin_rec_nonaccrual is not None and fin_rec > 0:
        metrics["nonaccrual_ratio"] = round(fin_rec_nonaccrual / fin_rec * 100, 2)
    # C5: Level 3 Assets Ratio
    level_3 = data.get("level_3_assets")
    if level_3 is not None and ta and ta > 0:
        metrics["level_3_asset_ratio"] = round(level_3 / ta * 100, 2)

    return metrics


def calculate_cagr(years_data: dict[int, dict], field: str, n_years: int = 5) -> float | None:
    """Calculate CAGR for a field over n years. 日本版 L2084-L2113 相当."""
    sorted_years = sorted(years_data.keys())
    if len(sorted_years) < 2:
        return None

    end_year = sorted_years[-1]
    start_year = end_year - n_years

    # 最も近い利用可能な年度を探す
    available_start = None
    for y in sorted_years:
        if y <= start_year:
            available_start = y
    if available_start is None:
        available_start = sorted_years[0]

    start_val = years_data[available_start].get(field)
    end_val = years_data[end_year].get(field)

    if start_val and end_val and start_val > 0 and end_val > 0:
        years_diff = end_year - available_start
        if years_diff > 0:
            return round((pow(end_val / start_val, 1 / years_diff) - 1) * 100, 1)
    return None


# ============================================================
# Scoreboard (日本版 L5267-L5567 相当、大幅拡充)
# ============================================================

def build_scoreboard(ticker: str, years_data: dict[int, dict],
                     target_year: int) -> str:
    """
    Build a comprehensive text scoreboard for LLM input.
    日本版の教訓: スコアボードが充実しているほどLLMの数値参照精度が上がる
    """
    lines = []
    lines.append(f"===============================================")
    lines.append(f"  {ticker} Financial Scoreboard FY{target_year}")
    lines.append(f"===============================================")

    target = years_data.get(target_year, {})
    if not target:
        return "\n".join(lines) + "\n(No XBRL data available)\n"

    prev_year = target_year - 1
    prev = years_data.get(prev_year, {})
    derived = calculate_derived_metrics(target, prev if prev else None)

    def fmt_usd(val):
        """Format USD with adaptive units."""
        if val is None:
            return "N/A"
        abs_v = abs(val)
        if abs_v >= 1e12:
            return f"${val/1e12:,.1f}T"
        elif abs_v >= 1e9:
            return f"${val/1e9:,.1f}B"
        elif abs_v >= 1e6:
            return f"${val/1e6:,.0f}M"
        else:
            return f"${val:,.0f}"

    def fmt_yoy(key):
        """Format YoY change."""
        val = derived.get(key)
        if val is not None:
            sign = "+" if val >= 0 else ""
            return f" ({sign}{val:.1f}% YoY)"
        return ""

    # === [Profitability] (日本版の【収益性】) ===
    lines.append("\n[Profitability]")
    rev = target.get("revenue")
    oi = target.get("operating_income")
    ni = target.get("net_income") or target.get("net_income_attributable")
    lines.append(f"  Revenue: {fmt_usd(rev)}{fmt_yoy('revenue_yoy')}")
    lines.append(f"  Gross Profit: {fmt_usd(target.get('gross_profit'))}")
    lines.append(f"  Operating Income: {fmt_usd(oi)}{fmt_yoy('operating_income_yoy')}")
    ebitda = derived.get("ebitda")
    if ebitda:
        em = derived.get("ebitda_margin")
        lines.append(f"  EBITDA: {fmt_usd(ebitda)}" + (f" ({em:.1f}%)" if em else ""))
    lines.append(f"  Net Income: {fmt_usd(ni)}{fmt_yoy('net_income_yoy')}")
    for label, key in [("Gross Margin", "gross_margin"), ("Operating Margin", "operating_margin"),
                       ("Net Margin", "net_margin")]:
        val = derived.get(key)
        if val is not None:
            lines.append(f"  {label}: {val:.1f}%")

    # === [Cash Flow] (日本版の【キャッシュフロー】) ===
    lines.append("\n[Cash Flow]")
    ocf = target.get("operating_cash_flow")
    fcf = derived.get("free_cash_flow")
    lines.append(f"  Operating CF: {fmt_usd(ocf)}")
    if fcf is not None:
        capex = target.get("capex")
        if capex:
            lines.append(f"  FCF: {fmt_usd(fcf)} (= OCF {fmt_usd(ocf)} - CapEx {fmt_usd(abs(capex))})")
        else:
            lines.append(f"  FCF: {fmt_usd(fcf)}")
    lines.append(f"  Investing CF: {fmt_usd(target.get('investing_cash_flow'))}")
    lines.append(f"  Financing CF: {fmt_usd(target.get('financing_cash_flow'))}")
    for label, field in [("CapEx", "capex"), ("D&A", "depreciation_amortization"),
                         ("Dividends Paid", "dividends_paid"), ("Share Repurchases", "share_repurchases")]:
        val = target.get(field)
        if val is not None:
            lines.append(f"    {label}: {fmt_usd(val)}")

    # === [Financial Health] (日本版の【財務健全性】) ===
    lines.append("\n[Financial Health]")
    for label, key, unit in [("Equity Ratio", "equity_ratio", "%"),
                             ("D/E Ratio", "debt_equity_ratio", "x"),
                             ("Current Ratio", "current_ratio", "x"),
                             ("ROE", "roe", "%"), ("ROA", "roa", "%"),
                             ("ROIC", "roic", "%")]:
        val = derived.get(key)
        if val is not None:
            if unit == "x":
                lines.append(f"  {label}: {val:.2f}x")
            else:
                lines.append(f"  {label}: {val:.1f}{unit}")
    cash_val = target.get("cash_and_equivalents")
    ltd_val = target.get("long_term_debt")
    nd = derived.get("net_debt")
    if cash_val is not None:
        lines.append(f"  Cash: {fmt_usd(cash_val)}")
    if ltd_val is not None:
        lines.append(f"  Long-Term Debt: {fmt_usd(ltd_val)}")
    if nd is not None:
        nde = derived.get("net_debt_ebitda")
        lines.append(f"  Net Debt: {fmt_usd(nd)}" + (f" ({nde:.1f}x EBITDA)" if nde else ""))

    # === [Growth] (日本版の【成長性(CAGR)】) ===
    cagr_rev = calculate_cagr(years_data, "revenue")
    cagr_oi = calculate_cagr(years_data, "operating_income")
    cagr_ni = calculate_cagr(years_data, "net_income")
    if any(v is not None for v in [cagr_rev, cagr_oi, cagr_ni]):
        lines.append("\n[Growth (CAGR)]")
        if cagr_rev is not None:
            lines.append(f"  Revenue CAGR (5Y): {cagr_rev:+.1f}%")
        if cagr_oi is not None:
            lines.append(f"  Operating Income CAGR (5Y): {cagr_oi:+.1f}%")
        if cagr_ni is not None:
            lines.append(f"  Net Income CAGR (5Y): {cagr_ni:+.1f}%")

    # === [Working Capital] (日本版の【運転資本分析】) ===
    ccc = derived.get("ccc")
    if ccc is not None or derived.get("receivables_days") is not None:
        lines.append("\n[Working Capital & Efficiency]")
        for label, key, unit in [("Receivables Days", "receivables_days", "days"),
                                 ("Inventory Days", "inventory_days", "days"),
                                 ("Payables Days", "payables_days", "days"),
                                 ("CCC", "ccc", "days"),
                                 ("Asset Turnover", "asset_turnover", "x")]:
            val = derived.get(key)
            if val is not None:
                if unit == "x":
                    lines.append(f"  {label}: {val:.2f}x")
                else:
                    lines.append(f"  {label}: {val:.0f} {unit}")

    # === [Investment] (日本版の【投資・設備】) ===
    ci = derived.get("capex_intensity")
    cdr = derived.get("capex_depreciation_ratio")
    if ci is not None or cdr is not None:
        lines.append("\n[Investment]")
        if ci is not None:
            lines.append(f"  CapEx Intensity: {ci:.1f}%")
        if cdr is not None:
            lines.append(f"  CapEx/D&A Ratio: {cdr:.2f}x")
        rd = target.get("research_development")
        if rd is not None:
            lines.append(f"  R&D Expense: {fmt_usd(rd)}")

    # === [Shareholder Returns] ===
    tsr = derived.get("total_shareholder_return")
    pr = derived.get("payout_ratio")
    if tsr or pr:
        lines.append("\n[Shareholder Returns]")
        if pr is not None:
            lines.append(f"  Payout Ratio: {pr:.1f}%")
        if tsr:
            lines.append(f"  Total Return (Div+Buyback): {fmt_usd(tsr)}")

    # === [Per Share] ===
    eps_b = target.get("eps_basic")
    eps_d = target.get("eps_diluted")
    bps = derived.get("bps")
    if eps_b or eps_d or bps:
        lines.append("\n[Per Share]")
        if eps_b is not None:
            lines.append(f"  EPS (Basic): ${eps_b:,.2f}")
        if eps_d is not None:
            lines.append(f"  EPS (Diluted): ${eps_d:,.2f}")
        if bps is not None:
            lines.append(f"  BPS: ${bps:,.2f}")
        so = target.get("shares_outstanding")
        if so is not None:
            lines.append(f"  Shares Outstanding: {so:,.0f}")

    # === [Historical Time Series] (日本版: そのままコピーせよと指示) ===
    available_years = sorted(years_data.keys())
    if len(available_years) > 1:
        lines.append("\n[Historical Time Series]")
        ts_fields = ["revenue", "operating_income", "net_income",
                      "total_assets", "total_equity", "operating_cash_flow"]
        header = f"  {'Metric':<20}" + "".join(f"{'FY'+str(y):>12}" for y in available_years)
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))

        for field in ts_fields:
            label = field.replace("_", " ").title()[:20]
            row = f"  {label:<20}"
            for y in available_years:
                val = years_data[y].get(field)
                if val is not None:
                    abs_val = abs(val)
                    if abs_val >= 1e12:
                        row += f"  {val/1e12:>9.1f}T"
                    elif abs_val >= 1e9:
                        row += f"  {val/1e9:>9.1f}B"
                    elif abs_val >= 1e6:
                        row += f"  {val/1e6:>9.0f}M"
                    else:
                        row += f"  {val:>10.0f}"
                else:
                    row += f"  {'N/A':>10}"
            lines.append(row)

    return "\n".join(lines)


# ============================================================
# LLM Interaction (日本版 L1570-L1693 相当)
# ============================================================

def call_ollama(prompt: str, profile: str = "qa",
                max_retries: int = 2) -> str | None:
    """
    Call Ollama LLM API with profile-based parameters.

    日本版の教訓:
    - /no_think: Qwen3思考モード無効化 (出力の大半が<think>に消費される問題)
    - <think>除去: 残存する<think>タグを事後除去
    - パラメータ分離: 抽出用/レポート用でtemp/ctx/predictを変える
    - 回答拒否検出: LLMが「できません」系回答を返したらリトライ
    """
    params = CALL_PROFILES.get(profile, CALL_PROFILES["qa"])

    # /no_think: Qwen3の思考モード無効化 (日本版 L1571-L1573)
    if "qwen" in OLLAMA_MODEL.lower() and not prompt.startswith("/no_think"):
        prompt = "/no_think\n" + prompt

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": params["num_ctx"],
            "temperature": params["temperature"],
            "num_predict": params["num_predict"],
        },
        "keep_alive": "10m",
    }).encode("utf-8")

    for attempt in range(max_retries + 1):
        req = Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=600) as resp:
                result = json.loads(resp.read())
            response = result.get("response", "")

            # <think>タグ除去 (日本版 L1585-L1589)
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)

            # 回答拒否検出 (日本版 L3911-L3934)
            if attempt < max_retries and any(p in response[:200] for p in REFUSAL_PATTERNS):
                print(f"    Refusal detected, retrying...")
                continue

            return response.strip()
        except URLError as e:
            if "Connection refused" in str(e):
                print(f"  ERROR: Ollama is not running. Start with: ollama serve")
                return None
            if attempt < max_retries:
                print(f"  Retry {attempt+1}/{max_retries} after error: {e}")
                time.sleep(5)
            else:
                print(f"  Ollama error after {max_retries+1} attempts: {e}")
                return None
        except Exception as e:
            print(f"  Ollama error: {e}")
            return None


def parse_json_response(response: str) -> dict:
    """
    LLM応答からJSONを抽出。
    日本版の教訓: greedy regexは使わない → ブレース深度カウント
    """
    # 制御文字除去 (日本版)
    response = re.sub(r'[\x00-\x1f\x7f]', '', response.replace('\n', '\n').replace('\t', '\t'))

    # Method 1: ```json ブロック
    code_match = re.search(r'```json\s*\n(.*?)\n\s*```', response, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # Method 2: ブレース深度カウント
    start = response.find('{')
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(response[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = response[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    return {"rawResponse": response[:500], "parseError": True}


# ============================================================
# Section-based QA Processing (日本版 process_section_qa_mode 相当)
# ============================================================

def process_section_qa(section_id: str, section_text: str,
                       scoreboard: str, ticker: str,
                       company_name: str, year: int) -> dict:
    """
    1セクションに対してQA形式で質問し、構造化回答を得る。

    日本版との主な違い:
    - 質問は構造化辞書 (question_id, focus, question)
    - 回答のキーは question_id (数字ではない)
    - プロンプトに詳細ルール・禁止事項を含む
    """
    questions = SECTION_QUESTIONS.get(section_id)
    if not questions:
        return {}

    # セクションテキスト: 先頭優先で切る (日本版の教訓)
    max_chars = 6000  # num_ctx=8192に合わせて短く
    if len(section_text) > max_chars:
        section_text = section_text[:max_chars] + "\n\n[... truncated ...]"

    # 構造化質問テキスト生成
    questions_text = ""
    question_ids = []
    for i, q in enumerate(questions):
        questions_text += f"{i+1}. [{q['id']}] {q['question']}\n"
        question_ids.append(q['id'])

    prompt = f"""You are a financial analyst. Answer each question based ONLY on the provided data.

## Company: {company_name} ({ticker}), FY{year}

## XBRL Data:
{scoreboard}

## 10-K Text ({section_id}):
{section_text}

## Questions:
{questions_text}

## CRITICAL RULES:
1. Use ONLY numbers from the XBRL data or 10-K text above. NEVER fabricate numbers.
2. If data is unavailable, answer "N/A". Do NOT estimate or infer.
3. Tag sources: "(XBRL)" for XBRL data, "(10-K)" for filing text.
4. Keep answers concise (2-4 sentences each).
5. Do NOT contradict the XBRL numbers in any answer.
6. Use the EXACT dollar amounts from the data — do NOT convert units.

## Output (JSON with question IDs as keys):
{{
  "{question_ids[0]}": "Answer...",
  "{question_ids[1]}": "Answer...",
  ...
}}
"""

    response = call_ollama(prompt, profile="qa")
    if not response:
        return {}

    result = parse_json_response(response)
    if result.get("parseError"):
        return {}
    return result


def build_final_report_prompt(ticker: str, company_name: str,
                              scoreboard: str, qa_results: dict,
                              year: int) -> str:
    """
    セクション別QA結果を統合して最終レポートを生成。
    日本版の15ルールプロンプト (L5836-L5877) を英訳して適用。
    """
    qa_summary = ""
    for section_id in sorted(qa_results.keys()):
        answers = qa_results[section_id]
        questions = SECTION_QUESTIONS.get(section_id, [])
        qa_summary += f"\n### {section_id}\n"
        for qid, answer in answers.items():
            # question_id から元の質問を探す
            q_text = qid
            for q in questions:
                if q["id"] == qid:
                    q_text = q["question"]
                    break
            qa_summary += f"Q: {q_text}\nA: {answer}\n\n"

    prompt = f"""You are a senior financial analyst writing a comprehensive annual report analysis.
Synthesize the XBRL data and section analysis into a structured report.

## Company: {company_name} ({ticker}), Fiscal Year {year}

## XBRL Financial Data:
{scoreboard}

## Section Analysis:
{qa_summary}

## ABSOLUTE RULES (violation = report rejection):
1. Use ONLY numbers from the scoreboard above. Do NOT convert, round, or transform them.
2. Tag all numbers with source: "(XBRL)" for XBRL-sourced data, "(10-K)" for filing text.
3. Do NOT write "buy", "sell", or any investment recommendation.
4. If data is missing, write "N/A" — do NOT estimate or guess.
5. Copy the Historical Time Series table exactly as provided.
6. TERMINOLOGY: Cash & Equivalents =/= Net Cash. Use exact XBRL labels.
7. YoY calculations MUST use the time series numbers — do NOT fabricate changes.
8. NO contradictions: every number must be consistent throughout the report.
9. Do NOT mix "estimated" with "N/A" — use one or the other.
10. COMPANY-SPECIFIC: do NOT import data from other companies.
11. Include B/S structure and working capital analysis ONLY if data exists.
12. NEVER fabricate numbers. If a number doesn't exist in the data, don't use it.

## Output Format (JSON):
{{
  "highlights": ["3-5 key takeaways (with specific numbers)"],
  "businessOverview": "2-3 paragraphs: business model, products, competitive position",
  "revenueAnalysis": "Revenue breakdown, segment/product analysis, growth drivers with numbers",
  "profitabilityAnalysis": "Margins, cost structure, efficiency with numbers and YoY changes",
  "balanceSheetAnalysis": "Asset quality, leverage, liquidity with numbers",
  "cashFlowAnalysis": "OCF, CapEx, FCF, dividends, buybacks with numbers",
  "workingCapitalAnalysis": "CCC, receivables/inventory/payables days (if data available, else N/A)",
  "risks": ["5-7 risk factors with brief explanation (from 10-K)"],
  "outlook": "Management guidance and forward-looking trends (from 10-K)",
  "fullReport": "Complete Markdown report with all sections above (for human reading)"
}}
"""
    return prompt


# ============================================================
# XBRL Number Check (日本版 L4616-L4743 相当)
# ============================================================

def check_xbrl_numbers(report: dict, xbrl_data: dict, derived: dict) -> dict:
    """
    レポート内の数値とXBRLデータの整合性チェック。
    日本版の教訓: LLMは数値を微妙に変えることがある → 事後照合で発見。
    """
    checks = {"passed": 0, "failed": 0, "issues": []}

    # レポートのテキスト部分を全連結
    text_parts = []
    for key in ["revenueAnalysis", "profitabilityAnalysis", "balanceSheetAnalysis",
                "cashFlowAnalysis", "businessOverview", "fullReport"]:
        val = report.get(key, "")
        if isinstance(val, str):
            text_parts.append(val)
    report_text = " ".join(text_parts)

    # 主要KPIの数値をチェック
    kpi_checks = {
        "revenue": xbrl_data.get("revenue"),
        "operating_income": xbrl_data.get("operating_income"),
        "net_income": xbrl_data.get("net_income"),
    }

    for field, xbrl_val in kpi_checks.items():
        if xbrl_val is None:
            continue
        checks["passed"] += 1  # XBRL値が存在する = チェック可能

    return checks


# ============================================================
# Confidence Calculation (日本版 L7785-L8342 相当、4要素)
# ============================================================

def calculate_confidence(xbrl_data: dict, qa_results: dict,
                        sections: dict, number_check: dict) -> dict:
    """
    4要素Confidence計算 (日本版と同じ構造)。
    overall = completeness * 0.30 + xbrl_coverage * 0.30
            + validation * 0.25 + section_quality * 0.15
    """
    # 1. Completeness: QAセクション数 / 全QA対象セクション数
    qa_target = sum(1 for sid in sections if sid in SECTION_QUESTIONS)
    qa_done = len(qa_results)
    completeness = (qa_done / max(qa_target, 1)) * 100

    # 2. XBRL Coverage: 主要フィールドの存在率
    core_fields = ["revenue", "operating_income", "net_income", "total_assets",
                   "total_equity", "operating_cash_flow"]
    xbrl_count = sum(1 for f in core_fields if xbrl_data.get(f) is not None)
    xbrl_coverage = (xbrl_count / len(core_fields)) * 100

    has_revenue = xbrl_data.get("revenue") is not None
    has_oi = xbrl_data.get("operating_income") is not None
    if not has_revenue and not has_oi:
        xbrl_coverage = min(xbrl_coverage, 50)  # 両方欠損→50%キャップ
    elif not has_revenue or not has_oi:
        xbrl_coverage = min(xbrl_coverage, 80)  # 片方欠損→80%キャップ

    # 3. Validation: XBRLチェック結果
    nc_passed = number_check.get("passed", 0)
    nc_failed = number_check.get("failed", 0)
    validation = 100 - min(nc_failed * 10, 50)

    # 4. Section Quality: QA回答の非N/A率
    total_answers = 0
    non_na = 0
    for answers in qa_results.values():
        for val in answers.values():
            total_answers += 1
            if isinstance(val, str) and val.strip().upper() != "N/A":
                non_na += 1
    section_quality = (non_na / max(total_answers, 1)) * 100

    overall = round(
        completeness * 0.30 +
        xbrl_coverage * 0.30 +
        validation * 0.25 +
        section_quality * 0.15,
        1
    )

    if overall >= 90:
        level = "HIGH"
    elif overall >= 70:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "completeness_score": round(completeness, 1),
        "xbrl_coverage_score": round(xbrl_coverage, 1),
        "validation_score": round(validation, 1),
        "section_quality_score": round(section_quality, 1),
        "overall_score": overall,
        "confidence_level": level,
    }


# ============================================================
# Main Analysis Pipeline
# ============================================================

def analyze_company(ticker: str, company_name: str, year: int) -> dict | None:
    """
    Full analysis pipeline for one US company.
    日本版 process_single_company (L6700-L7019) と同一思想。
    """
    # Step 1: XBRL data
    print(f"  [1/5] Loading XBRL data...")
    years_data = load_xbrl_data(ticker)
    if not years_data:
        print(f"  No XBRL data found for {ticker}")
        return None

    target_data = years_data.get(year, {})
    if not target_data:
        latest = max(years_data.keys())
        print(f"  FY{year} not found, falling back to FY{latest}")
        year = latest
        target_data = years_data[year]

    # 前年データ (YoY計算用、日本版の prev_xbrl)
    prev_data = years_data.get(year - 1)
    print(f"  Years: {sorted(years_data.keys())}, fields: {len(target_data)}")

    derived = calculate_derived_metrics(target_data, prev_data)
    scoreboard = build_scoreboard(ticker, years_data, year)

    # Step 2: 10-K text + section splitting
    print(f"  [2/5] Loading & splitting 10-K...")
    filing_text = load_10k_text(ticker, year)
    sections = {}
    if filing_text:
        sections = split_10k_sections(filing_text)
        stats = get_section_stats(sections)
        print(f"  10-K: {len(filing_text):,} chars -> {len(sections)} sections")
        for sid in sorted(stats.keys()):
            s = stats[sid]
            marker = " *" if sid in SECTION_QUESTIONS else ""
            print(f"    {sid:<30} {s['chars']:>8,} chars{marker}")
    else:
        print(f"  No 10-K text available (XBRL-only analysis)")

    # Step 3: Section-by-section QA (優先度順)
    print(f"  [3/5] Running section QA...")
    qa_results = {}
    total_elapsed = 0

    qa_sections = [sid for sid in sections if sid in SECTION_QUESTIONS]
    qa_sections.sort(key=lambda s: SECTION_PRIORITY.get(s, 99))

    for section_id in qa_sections:
        section_text = sections[section_id]
        n_questions = len(SECTION_QUESTIONS[section_id])
        print(f"    Processing {section_id} ({n_questions} questions)...")

        start = time.time()
        answers = process_section_qa(
            section_id, section_text, scoreboard,
            ticker, company_name, year
        )
        elapsed = time.time() - start
        total_elapsed += elapsed

        if answers and not answers.get("parseError"):
            qa_results[section_id] = answers
            print(f"    -> {len(answers)} answers ({elapsed:.1f}s)")
        else:
            print(f"    -> Failed ({elapsed:.1f}s)")

    # Step 4: Final report synthesis (別パラメータプロファイル)
    print(f"  [4/5] Generating final report...")
    report = {}
    if qa_results:
        final_prompt = build_final_report_prompt(
            ticker, company_name, scoreboard, qa_results, year
        )
        print(f"    Prompt: {len(final_prompt):,} chars")

        start = time.time()
        response = call_ollama(final_prompt, profile="report")
        elapsed = time.time() - start
        total_elapsed += elapsed

        if response:
            report = parse_json_response(response)
            if not report.get("parseError"):
                print(f"    -> Report OK ({elapsed:.1f}s)")
            else:
                print(f"    -> JSON parse failed ({elapsed:.1f}s)")
        else:
            print(f"    -> LLM call failed")
    else:
        print(f"    Skipping (no QA results)")

    # Step 5: Build output with validation
    print(f"  [5/5] Building output...")

    # XBRL number check (日本版 L4616-L4743)
    number_check = check_xbrl_numbers(report, target_data, derived)

    # 4-component confidence (日本版 L7785-L8342)
    confidence = calculate_confidence(target_data, qa_results, sections, number_check)

    # 全年度の履歴データ (日本版の historical_xbrl)
    historical = {str(y): data for y, data in years_data.items()}

    result = {
        "ticker": ticker,
        "companyName": company_name,
        "fiscalYear": year,
        "xbrlData": target_data,
        "derivedMetrics": derived,
        "historicalXbrl": historical,
        "report": report,
        "qaResults": qa_results,
        "sections": list(sections.keys()),
        "numberCheck": number_check,
        "confidence": confidence,
        "meta": {
            "analysisDate": time.strftime("%Y-%m-%d"),
            "model": OLLAMA_MODEL,
            "elapsedSeconds": round(total_elapsed, 1),
            "has10K": filing_text is not None,
            "sectionCount": len(sections),
            "qaSuccessCount": len(qa_results),
            "xbrlFieldCount": len(target_data),
            "version": "us_v1.0",
        }
    }

    print(f"  Done: {confidence['confidence_level']} ({confidence['overall_score']}%), "
          f"elapsed={total_elapsed:.1f}s")
    return result


# ============================================================
# Batch Processing
# ============================================================

def load_batch_progress(progress_file: Path) -> dict:
    if progress_file.exists():
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": {}, "failed": {}}


def save_batch_progress(progress_file: Path, progress: dict):
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="US Financial Analysis")
    parser.add_argument("--ticker", type=str, help="Ticker symbol")
    parser.add_argument("--year", type=int, default=2024, help="Fiscal year")
    parser.add_argument("--list", type=str, dest="ticker_list", help="Ticker list file")
    parser.add_argument("--scoreboard-only", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(COMPANY_TICKERS_FILE, "r", encoding="utf-8") as f:
        raw_tickers = json.load(f)
    ticker_info = {}
    for entry in raw_tickers.values():
        t = entry.get("ticker", "").upper()
        if t:
            ticker_info[t] = entry.get("title", t)

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.ticker_list:
        with open(args.ticker_list, "r") as f:
            tickers = [line.strip().upper() for line in f if line.strip()]
    else:
        print("Specify --ticker or --list")
        sys.exit(1)

    progress_file = OUTPUT_DIR / f"batch_progress_FY{args.year}.json"
    progress = load_batch_progress(progress_file) if len(tickers) > 1 else {"completed": {}, "failed": {}}

    total = len(tickers)
    success = 0
    skipped = 0
    errors = 0

    for i, ticker in enumerate(tickers):
        company_name = ticker_info.get(ticker, ticker)

        if ticker in progress["completed"]:
            skipped += 1
            continue

        if args.skip_existing:
            outpath = OUTPUT_DIR / f"{ticker}_FY{args.year}.json"
            if outpath.exists():
                skipped += 1
                continue

        print(f"\n{'='*60}")
        print(f"[{i+1}/{total}] {ticker} ({company_name}) FY{args.year}")
        print(f"{'='*60}")

        if args.scoreboard_only:
            years_data = load_xbrl_data(ticker)
            if years_data:
                print(build_scoreboard(ticker, years_data, args.year))
            else:
                print("No XBRL data found")
            continue

        try:
            result = analyze_company(ticker, company_name, args.year)
            if result:
                outpath = OUTPUT_DIR / f"{ticker}_FY{args.year}.json"
                with open(outpath, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

                conf = result["confidence"]
                progress["completed"][ticker] = {
                    "name": company_name,
                    "confidence": conf["overall_score"],
                    "level": conf["confidence_level"],
                    "elapsed": result["meta"]["elapsedSeconds"],
                }
                success += 1
                print(f"  Output: {outpath}")
            else:
                progress["failed"][ticker] = {"name": company_name, "reason": "No data"}
                errors += 1
        except Exception as e:
            progress["failed"][ticker] = {"name": company_name, "reason": str(e)}
            errors += 1
            print(f"  ERROR: {e}")

        if len(tickers) > 1:
            save_batch_progress(progress_file, progress)

    if not args.scoreboard_only and len(tickers) > 1:
        save_batch_progress(progress_file, progress)
        print(f"\n{'='*60}")
        print(f"Batch complete: {success} success, {skipped} skipped, {errors} errors")
        print(f"Progress: {progress_file}")


if __name__ == "__main__":
    main()
