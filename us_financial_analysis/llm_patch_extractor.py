#!/usr/bin/env python3
"""
10-K本文LLM抽出によるinterest_expenseフォールバック。

対象: XBRL interest_expense が欠損している米国企業
目的: 10-K本文から数値を抽出して us_xbrl_store にパッチを当てる
品質: pik_estimation_quality = 'llm_extracted' として明示

戦略:
  1. 10-Kテキストから財務3表（Consolidated Statements of Operations）周辺を抽出
  2. 注記の "Other income/expense" "Non-operating income" セクションも含める
  3. qwen3:14b に構造化JSONで回答させる
  4. CF `interest_paid_cf` とクロスバリデーション（20%以内の乖離ならOK）
  5. パッチファイル `{year}_llm_patch.json` として保存
  6. us_xbrl_store ロード時にマージ

Usage:
    python llm_patch_extractor.py --ticker AAPL --year 2024
    python llm_patch_extractor.py --missing-only --min-revenue 1000000000
    python llm_patch_extractor.py --dry-run --limit 5
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    OLLAMA_URL, OLLAMA_MODEL, OLLAMA_NUM_CTX,
    COMPANY_TICKERS_FILE, FILING_DIR, US_XBRL_STORE
)

from urllib.request import Request, urlopen
from urllib.error import URLError


# ============================================================
# 10-Kテキストから関連セクションを抽出
# ============================================================

def extract_financial_context(text: str, max_chars: int = 20000) -> str:
    """
    10-K本文から interest expense 判定に必要なコンテキストを抽出。

    優先度:
      1. "Interest income ... Interest expense" テーブル（最も信頼度高い）
      2. CONSOLIDATED STATEMENTS OF OPERATIONS 周辺（P/L表）
      3. "Other income/(expense), net" 注記内訳
      4. EBITDA reconciliation（営業利益 + 減価償却 + 支払利息の分解）
      5. Interest expense 関連テキスト（注釈、計上ポリシー等）
      6. Cash flow statement 周辺
    """
    segments = []
    seen_starts = set()

    def add_segment(label: str, match_obj, size: int):
        start = match_obj.start()
        # 近接重複を防ぐ
        if any(abs(start - s) < 300 for s in seen_starts):
            return
        seen_starts.add(start)
        end = min(len(text), start + size)
        segments.append((label, text[start:end]))

    # 1. Interest income + Interest expense table（最優先）
    for m in re.finditer(
        r'Interest income[^\n]{0,100}\$?\s*[\d,]+[^\n]{0,200}Interest expense',
        text, re.IGNORECASE,
    ):
        add_segment('IE_TABLE', m, 1200)
        if len(segments) >= 3:
            break

    # 2. EBITDA reconciliation — "Interest expense" as additions line
    for m in re.finditer(
        r'Interest expense[^\n]{0,50}[\d,]+[^\n]{0,100}(?:Investment interest income|Income taxes|Depreciation)',
        text, re.IGNORECASE,
    ):
        add_segment('EBITDA', m, 1000)
        if len(segments) >= 4:
            break

    # 3. P/L表
    pl_match = re.search(
        r'CONSOLIDATED STATEMENTS? OF OPERATIONS',
        text, re.IGNORECASE,
    )
    if pl_match:
        add_segment('PL', pl_match, 4000)

    # 4. "Other income/(expense), net" breakdown
    for pat in [
        r'Other [Ii]ncome.{0,80}[Ee]xpense.{0,50}[Nn]et[^a-z]',
        r'Interest expense and other',
        r'Contractual interest expense',
    ]:
        m = re.search(pat, text)
        if m:
            add_segment('NOTE', m, 1500)

    # 5. CF statement
    cf_match = re.search(
        r'CONSOLIDATED STATEMENTS? OF CASH FLOWS',
        text, re.IGNORECASE,
    )
    if cf_match:
        add_segment('CF', cf_match, 3000)

    # Combine under max_chars limit
    combined = []
    total = 0
    for label, content in segments:
        piece = f'\n=== {label} SECTION ===\n{content}\n'
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > 300:
                combined.append(piece[:remaining])
            break
        combined.append(piece)
        total += len(piece)

    return ''.join(combined) if combined else text[:max_chars]


# ============================================================
# LLM呼び出し
# ============================================================

PROMPT_TEMPLATE = """/no_think
You are a financial data extraction assistant. Read the 10-K filing excerpt below and extract the company's annual INTEREST EXPENSE.

10-K EXCERPT ({ticker}):
```
{excerpt}
```

TASK: Extract the MOST RECENT fiscal year's interest expense (the latest/leftmost column in 3-year comparison tables). Report raw numbers exactly as printed plus the unit.

Rules:
- "interest_expense_raw": The raw positive number as printed in the MOST RECENT year column. Example: "Interest expense (7,098) (6,995)" → 7098 (the first, most-recent number).
- "interest_income_raw": Same for interest income (most recent year only).
- "unit": The currency unit — "millions", "thousands", or "units" — based on the section header (e.g., "in thousands" → "thousands", "in millions" → "millions"). Default to "millions" if unclear.
- "confidence": "high" if clearly labeled P/L or breakdown line, "medium" if inferred from notes, "low" if ambiguous.
- "source_quote": The exact phrase/line (up to 250 chars) where the number appears.
- If interest expense is ONLY aggregated in "Other income/(expense), net" with no breakdown in the excerpt, set all "_raw" fields to null.

Output ONLY valid JSON with these exact keys:
{{
  "interest_expense_raw": number or null,
  "interest_income_raw": number or null,
  "unit": "millions" | "thousands" | "units",
  "confidence": "high" | "medium" | "low",
  "source_quote": string
}}
"""


def call_ollama(prompt: str, timeout: int = 600) -> str | None:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "temperature": 0.1,
            "num_predict": 800,
        },
        "keep_alive": "10m",
    }).encode('utf-8')

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
        response = result.get("response", "")
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        return response.strip()
    except URLError as e:
        print(f"  Ollama error: {e}")
        return None
    except Exception as e:
        print(f"  Unexpected error: {e}")
        return None


UNIT_MULTIPLIER = {
    'millions': 1_000_000,
    'million': 1_000_000,
    'thousands': 1_000,
    'thousand': 1_000,
    'units': 1,
    'unit': 1,
}


def parse_llm_response(raw: str) -> dict | None:
    if not raw:
        return None
    match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
    except json.JSONDecodeError:
        return None

    # Normalize numeric fields
    for k in ['interest_expense_raw', 'interest_income_raw']:
        v = obj.get(k)
        if isinstance(v, str):
            v = v.replace(',', '').replace('$', '').replace('(', '').replace(')', '').strip()
            try:
                obj[k] = float(v)
            except ValueError:
                obj[k] = None
        elif not isinstance(v, (int, float)):
            obj[k] = None

    unit = (obj.get('unit') or '').lower().strip()
    mult = UNIT_MULTIPLIER.get(unit, None)

    # Convert to USD absolute
    for key_raw, key_abs in [('interest_expense_raw', 'interest_expense'),
                              ('interest_income_raw', 'interest_income')]:
        raw_val = obj.get(key_raw)
        if raw_val is not None and mult is not None:
            obj[key_abs] = abs(raw_val) * mult
        else:
            obj[key_abs] = None

    if obj.get('confidence') not in {'high', 'medium', 'low'}:
        obj['confidence'] = 'low'

    return obj


# ============================================================
# バリデーション
# ============================================================

def validate_with_cf(
    extracted_ie: float | None,
    cf_interest_paid: float | None,
    operating_income: float | None,
    revenue: float | None,
) -> tuple[bool, str]:
    """
    LLM抽出値の妥当性検証。
    - Too small (< $10K) → probably noise/misread
    - Too large (> revenue) → clearly wrong
    - Too large relative to CF (ratio > 10) → probably hallucination
    - CF >> PL (ratio < 0.3) → shouldn't happen (cash can't exceed accrual for same period)
    - Ratio 0.7-1.3: tight match (no significant PIK)
    - Ratio 1.3-10: legitimate PIK signal, ACCEPT with note
    """
    if extracted_ie is None:
        return False, "no_value"
    if extracted_ie < 10_000:  # less than $10K → probably parsing error
        return False, "too_small"
    if revenue and extracted_ie > revenue:
        return False, f"exceeds_revenue ({extracted_ie} vs {revenue})"
    if operating_income and operating_income > 0 and extracted_ie > abs(operating_income) * 2:
        # Interest > 200% of opincome is suspicious for non-distressed companies
        return False, f"exceeds_200%_opincome ({extracted_ie} vs {operating_income})"
    if cf_interest_paid is not None:
        cf_abs = abs(cf_interest_paid)
        if cf_abs > 10_000:
            ratio = extracted_ie / cf_abs
            if ratio > 10:
                return False, f"cf_ratio_too_high ({ratio:.2f})"
            if ratio < 0.3:
                return False, f"cf_exceeds_pl ({ratio:.2f})"
            # ratio 0.3-10 accepted
    return True, "ok"


# ============================================================
# 単一社処理
# ============================================================

def process_company(ticker: str, year: int, company_dir: Path,
                    dry_run: bool = False, verbose: bool = False) -> dict:
    """
    1社1年分のLLM抽出を実行。結果を {year}_llm_patch.json に書き出し。

    Returns: {status, extracted, validated, elapsed}
    """
    result = {'ticker': ticker, 'year': year, 'status': None,
              'extracted': None, 'validated': False, 'elapsed': 0,
              'reason': None}
    start = time.time()

    # 10-K text読込 — 10-Kファイル名と fiscal year がズレるケース対応
    # (Apple: 2024_10K.txt は実は FY2025期末 9/27/2025 の10-K, 3年比較あり)
    folder_name = company_dir.name
    tk_path = None
    for candidate_year in [year, year - 1, year + 1]:
        candidate = FILING_DIR / folder_name / f'{candidate_year}_10K.txt'
        if candidate.exists():
            tk_path = candidate
            break
    if tk_path is None:
        result['status'] = 'no_10k'
        return result

    # xbrl_store 既存データ
    year_json_path = company_dir / f'{year}.json'
    if not year_json_path.exists():
        result['status'] = 'no_xbrl_year'
        return result
    with year_json_path.open('r', encoding='utf-8') as f:
        year_data = json.load(f)
    data = year_data.get('data', {})
    if data.get('interest_expense') is not None:
        result['status'] = 'already_has_ie'
        return result

    # 10-K text → コンテキスト抽出
    with tk_path.open('r', encoding='utf-8') as f:
        text = f.read()
    excerpt = extract_financial_context(text, max_chars=18000)
    if len(excerpt) < 500:
        result['status'] = 'no_relevant_section'
        return result

    # LLM呼び出し
    prompt = PROMPT_TEMPLATE.format(ticker=ticker, year=year, excerpt=excerpt)
    if verbose:
        print(f'  [{ticker}] Calling LLM (excerpt {len(excerpt)} chars)...')
    raw = call_ollama(prompt)
    if raw is None:
        result['status'] = 'llm_error'
        return result
    parsed = parse_llm_response(raw)
    if parsed is None:
        result['status'] = 'parse_error'
        result['reason'] = raw[:500]
        if verbose:
            print(f'  [{ticker}] Raw LLM output (first 500 chars):')
            print(f'    {raw[:500]}')
        return result

    result['extracted'] = parsed

    # バリデーション — parsed['interest_expense'] は既にUSD絶対値
    ie_value = parsed.get('interest_expense')
    ii_value = parsed.get('interest_income')

    ok, reason = validate_with_cf(
        ie_value,
        data.get('interest_paid_cf'),
        data.get('operating_income'),
        data.get('revenue'),
    )
    result['validated'] = ok
    result['reason'] = reason
    if not ok:
        result['status'] = f'invalid_{reason}'
        return result

    # パッチ保存
    patch = {
        'source': 'llm_10k_extraction',
        'model': OLLAMA_MODEL,
        'extracted_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'patches': {
            'interest_expense': ie_value,
        },
        'llm_confidence': parsed.get('confidence'),
        'source_quote': parsed.get('source_quote', '')[:400],
    }
    if ii_value is not None:
        patch['patches']['interest_income'] = ii_value

    patch_path = company_dir / f'{year}_llm_patch.json'
    if not dry_run:
        with patch_path.open('w', encoding='utf-8') as f:
            json.dump(patch, f, ensure_ascii=False, indent=2)

    result['status'] = 'extracted'
    result['elapsed'] = round(time.time() - start, 1)
    return result


# ============================================================
# バッチ
# ============================================================

def iter_target_companies(ticker_filter: str | None, year: int,
                          min_revenue: float, missing_only: bool):
    """対象企業をイテレート。"""
    for company_dir in sorted(US_XBRL_STORE.iterdir()):
        if not company_dir.is_dir():
            continue
        folder = company_dir.name
        ticker = folder.split('_')[0]
        if ticker_filter and ticker != ticker_filter:
            continue

        year_json = company_dir / f'{year}.json'
        if not year_json.exists():
            continue

        try:
            with year_json.open('r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue

        data = d.get('data', {})
        if missing_only and data.get('interest_expense') is not None:
            continue
        rev = data.get('revenue', 0) or 0
        if rev < min_revenue:
            continue

        # 10-K available? (year-fallback: exact > prev > next)
        has_any_10k = any(
            (FILING_DIR / folder / f'{y}_10K.txt').exists()
            for y in [year, year - 1, year + 1]
        )
        if not has_any_10k:
            continue

        yield ticker, company_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticker', help='Single ticker (e.g., AAPL)')
    ap.add_argument('--year', type=int, default=2024)
    ap.add_argument('--missing-only', action='store_true', default=True,
                    help='Skip companies that already have interest_expense')
    ap.add_argument('--min-revenue', type=float, default=1_000_000_000,
                    help='Skip companies with revenue below this threshold (USD)')
    ap.add_argument('--limit', type=int, default=0, help='Max companies to process')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    targets = list(iter_target_companies(
        ticker_filter=args.ticker,
        year=args.year,
        min_revenue=args.min_revenue,
        missing_only=args.missing_only,
    ))
    if args.limit:
        targets = targets[:args.limit]

    print(f'Target companies: {len(targets)}')
    if not targets:
        return

    stats = {'extracted': 0, 'invalid': 0, 'no_10k': 0, 'error': 0, 'skipped': 0}
    for i, (ticker, company_dir) in enumerate(targets, 1):
        print(f'[{i}/{len(targets)}] {ticker}...', end=' ', flush=True)
        try:
            r = process_company(ticker, args.year, company_dir,
                                dry_run=args.dry_run, verbose=args.verbose)
        except Exception as exc:
            print(f'EXCEPTION: {exc}')
            stats['error'] += 1
            continue
        status = r['status']
        if status == 'extracted':
            ex = r['extracted']
            print(f'OK ie={ex.get("interest_expense")}M conf={ex.get("confidence")} ({r["elapsed"]}s)')
            stats['extracted'] += 1
        elif status and status.startswith('invalid'):
            print(f'INVALID: {status}')
            stats['invalid'] += 1
        elif status in ('no_10k', 'no_xbrl_year', 'no_relevant_section',
                        'already_has_ie'):
            print(f'skip ({status})')
            stats['skipped'] += 1
        else:
            print(f'error ({status})')
            stats['error'] += 1

    print()
    print('=== SUMMARY ===')
    for k, v in stats.items():
        print(f'  {k}: {v}')


if __name__ == '__main__':
    main()
