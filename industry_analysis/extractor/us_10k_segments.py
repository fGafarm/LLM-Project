"""
US 10-K segment extractor (LLM-based)
--------------------------------------
10-K 本文から Segment Information セクションを特定し、Qwen3 に JSON 形式で
セグメント別 revenue / operating_income 等を抽出させる。

入力: E:/PDF/US Company/10-K/{ticker}_{name}/{year}_10K.txt
出力: industry_analysis/data/segment_store/us/{ticker}_{name}/{year}_segments.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

FILING_BASE = Path(r"E:/PDF/US Company/10-K")
US_XBRL_STORE = Path(__file__).parent.parent.parent / "us_financial_analysis" / "us_xbrl_store"
OUTPUT_BASE = Path(__file__).parent.parent / "data" / "segment_store" / "us"

OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:14b"

# セグメントセクションの切り出しヒューリスティック
SEG_SECTION_MARKERS = [
    r"Segment\s+(Information|Reporting)",
    r"Reportable\s+Segments?",
    r"Segment\s+and\s+Geographic\s+Information",
    r"Operating\s+Segments?",
    r"NOTE\s+\d+\s*[-\u2013\.\s]+Segment",
]

WINDOW_BEFORE = 800
WINDOW_AFTER = 25000  # 25K chars after the marker


def _score_window(text: str, pos: int, size: int = 25000) -> float:
    """pos 以降 size 文字窓の「セグメント表らしさ」をスコア。$+数値密度 + キーワード。"""
    window = text[pos:pos + size]
    if not window:
        return 0.0
    # 金額らしき "$" 出現回数 + 3桁カンマ区切り数値
    dollar_count = window.count("$")
    num_count = len(re.findall(r"\d{1,3}(?:,\d{3}){2,}", window))  # e.g. 1,234,567
    # セグメント名キーワード
    kw_count = len(
        re.findall(
            r"(segment|reportable|operating income|net revenue|external|geographic)",
            window,
            re.IGNORECASE,
        )
    )
    # table-like structure: short lines
    lines = window.split("\n")
    short_lines = sum(1 for l in lines if 0 < len(l.strip()) < 60)
    return dollar_count * 1.0 + num_count * 2.0 + kw_count * 0.3 + short_lines * 0.05


def find_segment_section(text: str) -> str | None:
    """
    10-K から segment 表が含まれるウィンドウを切り出す。
    全ての "Segment" 出現箇所をスコアリングして最も密度の高いウィンドウを選ぶ。
    """
    pattern = re.compile("|".join(SEG_SECTION_MARKERS), re.IGNORECASE)
    candidates: list[tuple[float, int]] = []
    for m in pattern.finditer(text):
        start = max(0, m.start() - WINDOW_BEFORE)
        score = _score_window(text, start, WINDOW_AFTER)
        candidates.append((score, start))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_score, best_start = candidates[0]
    if best_score < 5:  # too sparse
        return None
    end = min(len(text), best_start + WINDOW_AFTER + WINDOW_BEFORE)
    return text[best_start:end]


SYSTEM_INSTRUCTION = """You are an expert financial analyst. You extract segment-level financial data from SEC 10-K filings into structured JSON."""


EXTRACT_PROMPT_TEMPLATE = """/no_think

You are given an excerpt from a U.S. 10-K annual report that contains the company's reportable operating segments.

Extract the following for **each reportable operating segment** (do not include "Total", "Consolidated", "Corporate Eliminations", or "Reconciliation" as a segment — list those separately under `reconciling`):

- segment_name: the exact name as used in the filing
- revenue: external customer revenue for the most recent full fiscal year (USD, numeric)
- operating_income: segment operating income / profit / loss for the most recent full fiscal year (USD, numeric)
- segment_assets: total assets attributed to this segment (USD, if disclosed; else null)
- description: 1-2 sentence plain-English description of what this segment sells (derive from the filing; use null if unclear)

Units: if the filing shows values in "millions" or "thousands", CONVERT to actual dollars. Do NOT leave values in millions.
If a field is not disclosed, use null.
If the company reports only ONE segment, list it as the single segment.

# Company: {company_name} ({ticker})
# Fiscal year end (most recent): {fiscal_year}

# 10-K excerpt:
<<<
{excerpt}
>>>

# Output
Return a single JSON object, no other text. Schema:
{{
  "segments": [
    {{
      "segment_name": "...",
      "revenue": 12345678900,
      "operating_income": 2345678900,
      "segment_assets": null,
      "description": "..."
    }}
  ],
  "reconciling": {{
    "corporate_eliminations_revenue": null,
    "corporate_eliminations_operating_income": null,
    "note": "..."
  }},
  "reporting_note": "..."
}}
"""


def call_qwen(prompt: str, model: str = DEFAULT_MODEL, num_ctx: int = 32000, timeout: int = 600) -> str:
    resp = requests.post(
        f"{OLLAMA_ENDPOINT}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "system": SYSTEM_INSTRUCTION,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": num_ctx,
            },
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def parse_llm_response(text: str) -> dict:
    # strip <think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON object found. raw:\n{text[:500]}")
    json_text = text[start:end + 1]
    return json.loads(json_text)


def _load_us_xbrl_store(ticker: str, fiscal_year: int) -> dict | None:
    """us_xbrl_store/{TICKER}_{name}/{year}.json から全社値を読む"""
    for cand in US_XBRL_STORE.glob(f"{ticker}_*"):
        if not cand.is_dir():
            continue
        year_file = cand / f"{fiscal_year}.json"
        if year_file.exists():
            with open(year_file, encoding="utf-8") as f:
                return json.load(f)
    return None


def _find_10k_file(ticker: str, fiscal_year: int) -> tuple[Path | None, str | None]:
    for cand in FILING_BASE.glob(f"{ticker}_*"):
        if not cand.is_dir():
            continue
        f = cand / f"{fiscal_year}_10K.txt"
        if f.exists():
            return f, cand.name
    return None, None


def run_for_company(
    ticker: str,
    name: str,
    fiscal_year: int,
    model: str = DEFAULT_MODEL,
    force: bool = False,
) -> dict | None:
    out_dir = OUTPUT_BASE / f"{ticker}_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{fiscal_year}_segments.json"
    if out_path.exists() and not force:
        logger.info("SKIP (exists): %s", out_path)
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)

    txt_path, dir_name = _find_10k_file(ticker, fiscal_year)
    if txt_path is None:
        logger.warning("[NOT FOUND] %s %s FY%d", ticker, name, fiscal_year)
        return None

    with open(txt_path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    excerpt = find_segment_section(text)
    if excerpt is None:
        logger.warning("[NO SEG SECTION] %s", ticker)
        excerpt = text[-40000:]  # fallback: last ~40K chars (usually in Notes)

    logger.info(
        "[%s] 10-K size=%d chars, excerpt=%d chars", ticker, len(text), len(excerpt)
    )

    prompt = EXTRACT_PROMPT_TEMPLATE.format(
        company_name=name,
        ticker=ticker,
        fiscal_year=fiscal_year,
        excerpt=excerpt,
    )

    try:
        raw = call_qwen(prompt, model=model)
        parsed = parse_llm_response(raw)
    except Exception as e:
        logger.error("[LLM FAIL] %s: %s", ticker, e)
        return None

    # canonicalize
    segments_out = []
    for s in parsed.get("segments", []):
        segments_out.append({
            "segment_id": f"{ticker}_{re.sub(r'[^A-Za-z0-9]+', '_', str(s.get('segment_name', '')))}",
            "segment_member_qname": "",
            "segment_label_ja": None,
            "segment_label_en": s.get("segment_name"),
            "values": {
                k: v for k, v in {
                    "revenue_external": s.get("revenue"),
                    "revenue_total": s.get("revenue"),
                    "operating_profit": s.get("operating_income"),
                    "segment_assets": s.get("segment_assets"),
                }.items() if v is not None
            },
            "source_concepts": {"source": "10K_LLM_extraction"},
            "description": s.get("description"),
        })

    # 全社値を xbrl_store から比較用に読む
    totals = _load_us_xbrl_store(ticker, fiscal_year) or {}
    totals_data = totals.get("data", {})

    # 単一セグメント fallback
    single_segment_fallback = False
    if not segments_out:
        if totals_data.get("revenue"):
            segments_out.append({
                "segment_id": f"{ticker}_WHOLE_COMPANY",
                "segment_member_qname": "",
                "segment_label_ja": None,
                "segment_label_en": f"{name} (whole company / single segment)",
                "values": {
                    k: v for k, v in {
                        "revenue_external": totals_data.get("revenue"),
                        "revenue_total": totals_data.get("revenue"),
                        "operating_profit": totals_data.get("operating_income"),
                        "segment_assets": totals_data.get("total_assets"),
                    }.items() if v is not None
                },
                "source_concepts": {"source": "us_xbrl_store_aggregate_single_segment"},
                "description": None,
            })
            single_segment_fallback = True

    payload = {
        "source": "us_10k_llm",
        "ticker": ticker,
        "company_name": name,
        "fiscal_year": fiscal_year,
        "model": model,
        "single_segment_fallback": single_segment_fallback,
        "segment_count": len(segments_out),
        "segments": segments_out,
        "reconciling": parsed.get("reconciling"),
        "reporting_note": parsed.get("reporting_note"),
        "consolidated_totals_reference": {
            "revenue": totals_data.get("revenue"),
            "operating_income": totals_data.get("operating_income"),
            "total_assets": totals_data.get("total_assets"),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(
        "[OK] %s FY%d  %d segs", ticker, fiscal_year, len(segments_out)
    )
    return payload


def main():
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--companies-yaml", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ticker", type=str, help="single run")
    parser.add_argument("--name", type=str, default="test", help="single run name")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.ticker:
        run_for_company(args.ticker, args.name, args.year, model=args.model, force=args.force)
        return

    yaml_path = Path(args.companies_yaml) if args.companies_yaml else (
        Path(__file__).parent.parent / "semiconductor_companies.yaml"
    )
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    us = cfg.get("us", [])
    ok, fail = 0, 0
    for c in us:
        try:
            r = run_for_company(
                ticker=c["ticker"],
                name=c["name"],
                fiscal_year=args.year,
                model=args.model,
                force=args.force,
            )
            if r:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            logger.error("unexpected error on %s: %s", c.get("ticker"), e)
            fail += 1
    print(f"\nDone. OK={ok} FAIL={fail}")


if __name__ == "__main__":
    main()
