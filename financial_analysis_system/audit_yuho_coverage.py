#!/usr/bin/env python3
"""有報の全数カバレッジ監査 (POSTMORTEM P0対策・tanshin_audit の有報版).

「ダウンロード済みの有報ZIP 全件」対「正史storeの年度ファイル」を企業コード×docIDで突合し、
取り込み漏れ・古いまま・中身欠損を named で全件報告する。

判定 (企業×年度ごと、同一企業に複数ZIPがある場合は最新提出のdocIDを正とする):
  OK           : {year}.json が存在し source_file が最新docIDと一致
  STALE        : {year}.json はあるが古いdocID由来 (訂正有報の取り込み漏れ等)
  MISSING      : ZIPはあるのに {year}.json が無い (半期由来で_Q2に振替済みのものは INTERIM_OK)
  NO_REVENUE   : {year}.json はあるが revenue が空 (フロントの年度行に出ない)
  NO_DIR       : 企業フォルダ自体が無い

使い方:
  python audit_yuho_coverage.py [--years 2025,2026] [--max-list 30] [--json report.json]
終了コード: MISSING/NO_DIR/STALE >0 で 1
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PDF_ROOT = PROJECT / "pdf_xbrl"
STORE = PROJECT / "financial_analysis_system" / "xbrl_store"

# ZIP名: {code}_{name}_{periodEnd}_{label}_{docID}.zip
RE_ZIP = re.compile(r"^([0-9A-Z]{4,5})_(.+)_(\d{8})_(有報|訂正有報|半期報告書|訂正半期報告書|その他)_(S[0-9A-Z]+)\.zip$")


def build_store_index() -> dict:
    """code -> [company_dir,...] (社名表記ゆれの二重フォルダも全部拾う)"""
    idx = defaultdict(list)
    for d in STORE.iterdir():
        if d.is_dir() and "_" in d.name:
            idx[d.name.split("_")[0]].append(d)
    return idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2025,2026")
    ap.add_argument("--max-list", type=int, default=30)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    years = [y.strip() for y in args.years.split(",")]

    store_idx = build_store_index()
    results = {"OK": [], "STALE": [], "MISSING": [], "NO_REVENUE": [], "NO_DIR": [], "INTERIM_OK": []}

    for year in years:
        zip_dir = PDF_ROOT / year / "有報"
        if not zip_dir.is_dir():
            print(f"WARN: {zip_dir} なし", file=sys.stderr)
            continue

        # 企業ごとに最新の有報/訂正有報 docID を確定 (提出日=zipのmtimeでなくdocID列挙順は不定のため、
        # 訂正有報 > 有報、同種なら docID 辞書順の最大を最新とみなす)
        latest: dict = {}
        for z in zip_dir.glob("*.zip"):
            m = RE_ZIP.match(z.name)
            if not m:
                continue
            code, _name, period, label, docid = m.groups()
            if label not in ("有報", "訂正有報"):
                continue  # 半期は対象外 (別監査)
            cur = latest.get(code)
            rank = (1 if label == "訂正有報" else 0, docid)
            if cur is None or rank > cur[0]:
                latest[code] = (rank, docid, z.name)

        print(f"[{year}] 有報ZIPを持つ企業: {len(latest)}社")

        for code, (_rank, docid, zname) in sorted(latest.items()):
            dirs = store_idx.get(code)
            if not dirs:
                results["NO_DIR"].append(f"{year} {code} ({zname})")
                continue
            # 二重フォルダはどちらかにあればOKとする (最良の状態を採用)
            best = None  # (status_rank, detail)
            for d in dirs:
                yf = d / f"{year}.json"
                q2 = d / f"{year}_Q2.json"
                if yf.exists():
                    try:
                        j = json.loads(yf.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        cand = (3, f"{year} {code} parse error")
                        best = min(best, cand) if best else cand
                        continue
                    src = str(j.get("source_file") or "")
                    rev = (j.get("data") or {}).get("revenue")
                    if docid in src:
                        cand = (0, "OK") if rev else (2, f"{year} {code} {docid} rev空 ({d.name})")
                        cand = (cand[0], cand[1] if cand[0] else "OK")
                    else:
                        cand = (1, f"{year} {code} 期待={docid} 実際={src[-30:]} ({d.name})")
                    best = min(best, cand) if best else cand
                elif q2.exists():
                    cand = (4, f"{year} {code}")
                    best = min(best, cand) if best else cand
            if best is None:
                results["MISSING"].append(f"{year} {code} ({zname})")
            elif best[0] == 0:
                results["OK"].append(f"{year} {code}")
            elif best[0] == 1:
                results["STALE"].append(best[1])
            elif best[0] in (2, 3):
                results["NO_REVENUE"].append(best[1])
            elif best[0] == 4:
                results["INTERIM_OK"].append(best[1])

    print("\n=== 有報カバレッジ監査 (全企業) ===")
    for k in ("OK", "INTERIM_OK", "STALE", "NO_REVENUE", "MISSING", "NO_DIR"):
        print(f"  {k:11s}: {len(results[k])}")
    for k in ("MISSING", "NO_DIR", "STALE", "NO_REVENUE"):
        if results[k]:
            print(f"\n-- {k} (先頭{args.max_list}) --")
            for line in results[k][: args.max_list]:
                print("  " + line)
            if len(results[k]) > args.max_list:
                print(f"  ... 他 {len(results[k]) - args.max_list} 件")

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")

    bad = len(results["MISSING"]) + len(results["NO_DIR"]) + len(results["STALE"])
    if bad:
        print(f"\nFAIL: 取込漏れ/不整合 {bad}件", file=sys.stderr)
        return 1
    print("\nPASS: ダウンロード済み有報は全企業storeに反映済み")
    return 0


if __name__ == "__main__":
    sys.exit(main())
