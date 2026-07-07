"""
Report Reconcile — 表示層 (frontend reports行のKPI) vs 正史store の売上検算監査.

背景 (OPS.md §9): 汚染期間 (2026-05-08〜07-06) に生成されたAIレポート/年度行は、
当時の誤ったstore (例: KDDIの誤年度データ) から数値を焼き込んでいる可能性がある。
storeは2026-07-06に全量復旧済みのため、表示層との乖離 = 再生成が必要な行。

方式: public/data/{ticker}.json の reports[].kpis[0] (売上高、"59,179.5億円"/"2.50兆円"形式)
をパースし、store {year}.json の data.revenue と突合。乖離 >2% を named で報告。

使い方:
  python report_reconcile.py                 # 2024年度以降 (デフォルト)
  python report_reconcile.py --years 2025,2026 --tolerance 0.02 --json out.json
終了コード: 0=乖離なし / 1=乖離あり
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DATA = PROJECT_ROOT / "StockFlow" / "frontend" / "public" / "data"
XBRL_STORE = PROJECT_ROOT / "financial_analysis_system" / "xbrl_store"
LOG_DIR = PROJECT_ROOT / "logs"

NUM = re.compile(r"([\d,]+(?:\.\d+)?)(兆|億|百万)?円")


def parse_kpi_yen(s: str) -> float | None:
    """'59,179.5億円' / '2.50兆円' / '156百万円' → 円。パース不能は None。"""
    m = NUM.search(s or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    mul = {"兆": 1e12, "億": 1e8, "百万": 1e6, None: 1.0}[unit]
    return v * mul


def store_revenue(code: str, year: str) -> float | None:
    for d in XBRL_STORE.glob(f"{code}_*"):
        f = d / f"{year}.json"
        if f.exists():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rev = (j.get("data") or {}).get("revenue")
            if rev is not None:
                return float(rev)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="表示層KPI vs store revenue の検算")
    ap.add_argument("--years", default="2024,2025,2026")
    ap.add_argument("--tolerance", type=float, default=0.02, help="許容乖離率 (デフォルト2%%)")
    ap.add_argument("--max-list", type=int, default=40)
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    years = {y.strip() for y in args.years.split(",")}

    findings = []
    checked = 0
    parse_fail = 0
    no_store = 0
    for f in sorted(FRONTEND_DATA.glob("[0-9]*.json")):
        code = f.stem
        if not re.fullmatch(r"[0-9][0-9A-Z]{3}", code):
            continue
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for r in j.get("reports") or []:
            year = str(r.get("year"))
            if year not in years:
                continue
            kpis = r.get("kpis") or []
            if not kpis or "売上" not in str(kpis[0].get("label", "")):
                continue
            disp = parse_kpi_yen(str(kpis[0].get("value", "")))
            if disp is None:
                parse_fail += 1
                continue
            sv = store_revenue(code, year)
            if sv is None or sv == 0:
                no_store += 1
                continue
            checked += 1
            ratio = abs(disp - sv) / sv
            if ratio > args.tolerance:
                findings.append({
                    "code": code, "year": year, "type": r.get("type"),
                    "display": disp, "store": sv, "ratio": round(ratio, 4),
                    "generatedAt": str(r.get("generatedAt") or "")[:10],
                })

    findings.sort(key=lambda x: -x["ratio"])
    print("=== Report Reconcile (表示層 vs store) ===")
    print(f"検算 {checked}行 / 乖離>{args.tolerance:.0%} {len(findings)}件 / パース不能 {parse_fail} / store側なし {no_store}")
    for x in findings[: args.max_list]:
        print(f"  {x['code']} FY{x['year']} 表示={x['display']/1e8:,.0f}億 store={x['store']/1e8:,.0f}億 "
              f"乖離{x['ratio']:.1%} (type={x['type']}, gen={x['generatedAt']})")
    if len(findings) > args.max_list:
        print(f"  ... 他 {len(findings) - args.max_list} 件")

    out = {"ran_at": datetime.now().isoformat(timespec="seconds"), "checked": checked,
           "tolerance": args.tolerance, "findings": findings}
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / "report_reconcile_last.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
