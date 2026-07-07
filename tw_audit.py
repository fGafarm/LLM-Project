"""
TW Audit — TWSE上場マスタ vs tw_xbrl_store の expected-vs-actual 監査 + 機械検算.

日本の yuho_audit / 米国の us_yuho_audit と同型 (EXPANSION_BLUEPRINT §1)。
台湾特有の最重要ゲート: OpenAPIの「最新四半期」が年度ファイルとして混入する事故
(quarter≠4 の {year}.json = JPの半期→年度混入 S0 T4 と同型) を INTERIM_AS_ANNUAL で検知。

判定:
  OK / MISSING_LATEST (マスタに居るのに直近FYのstoreなし) / INTERIM_AS_ANNUAL (quarter≠4)
  BS_BROKEN (資産≠負債+資本 >1%) / NO_REVENUE

使い方:
  python tw_audit.py                 # 直近確定FY (デフォルト: 前年) を監査
  python tw_audit.py --year 2025 --strict
終了コード: 0=PASS / 1=欠落あり(--strict) / 2=環境エラー
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TW_STORE = PROJECT_ROOT / "tw_financial_analysis" / "tw_xbrl_store"
LOG_DIR = PROJECT_ROOT / "logs"
SUMMARY_JSON = LOG_DIR / "tw_audit_last.json"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE  # TWSE自己署名証明書対応 (twse_data_fetcher と同じ)

MASTER_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"  # 上場企業マスタ


def fetch_master() -> list[dict] | None:
    req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "Mozilla/5.0 (tw_audit)",
                                                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] マスタ取得失敗: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="TW store audit (TWSE master vs tw_xbrl_store)")
    ap.add_argument("--year", type=int, default=datetime.now().year - 1,
                    help="監査対象FY (デフォルト: 前年=直近確定年度)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--max-list", type=int, default=25)
    args = ap.parse_args()
    fy = str(args.year)

    if not TW_STORE.is_dir():
        print(f"[CRITICAL] tw_xbrl_store が存在しない: {TW_STORE}")
        _write({"status": "ENV_ERROR"})
        return 2

    master = fetch_master()
    master_codes = {str(r.get("公司代號") or r.get("Code") or "").strip() for r in (master or [])}
    master_codes.discard("")

    store_dirs = {d.name.split("_")[0]: d for d in TW_STORE.iterdir() if d.is_dir()}
    buckets: dict[str, list[str]] = {k: [] for k in
        ("OK", "MISSING_LATEST", "INTERIM_AS_ANNUAL", "BS_BROKEN", "NO_REVENUE")}

    # L1: マスタ vs store (直近確定FYの存在)
    if master_codes:
        for code in sorted(master_codes):
            d = store_dirs.get(code)
            if not d or not (d / f"{fy}.json").exists():
                buckets["MISSING_LATEST"].append(code)

    # L2: store全ファイルの機械検算 (quarterゲート + B/S等式 + revenue)
    for code, d in sorted(store_dirs.items()):
        for f in sorted(d.glob("20*.json")):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            data = j.get("data") or {}
            q = str(j.get("quarter", "4"))
            if q != "4":
                # 台湾版「中間→年度混入」: OpenAPI最新四半期が年度ファイルに入った状態。
                # 年度行として公開してはならない (JPのS0 T4と同型)
                buckets["INTERIM_AS_ANNUAL"].append(f"{code} {f.stem} (quarter={q})")
                continue
            ta, tl, te = data.get("total_assets"), data.get("total_liabilities"), data.get("total_equity")
            if ta and tl and te and abs(ta - (tl + te)) / ta > 0.01:
                buckets["BS_BROKEN"].append(f"{code} {f.stem} 資産{ta:,.0f}≠負債+資本{tl + te:,.0f}")
            elif f.stem == fy and data.get("revenue") is None:
                buckets["NO_REVENUE"].append(f"{code} {fy}")
            elif f.stem == fy:
                buckets["OK"].append(code)

    print("=== TW Audit (TWSEマスタ → tw_xbrl_store) ===")
    print(f"対象FY: {fy} / マスタ {len(master_codes)}社 / store {len(store_dirs)}社")
    print()
    for k in ("OK", "NO_REVENUE", "BS_BROKEN", "INTERIM_AS_ANNUAL", "MISSING_LATEST"):
        print(f"  {k:18s}: {len(buckets[k])}")
    for k in ("INTERIM_AS_ANNUAL", "BS_BROKEN", "NO_REVENUE", "MISSING_LATEST"):
        if buckets[k]:
            print(f"\n-- {k} (先頭{args.max_list}) --")
            for line in buckets[k][: args.max_list]:
                print("  " + str(line))
            if len(buckets[k]) > args.max_list:
                print(f"  ... 他 {len(buckets[k]) - args.max_list} 件")

    # INTERIM_AS_ANNUAL は「storeに存在すること」自体は許容 (最新Q速報として使い道あり) だが、
    # 公開ゲート必須の警告として扱う。hard failは公開系の破れ (BS_BROKEN/NO_REVENUE) + 大量欠落
    hard = len(buckets["BS_BROKEN"]) + len(buckets["NO_REVENUE"])
    missing_ratio = len(buckets["MISSING_LATEST"]) / max(len(master_codes), 1)
    verdict = "FAIL" if (hard or missing_ratio > 0.10) else "PASS"
    print(f"\n{verdict}: 検算破れ {hard}件 / マスタ比欠落 {len(buckets['MISSING_LATEST'])}件 "
          f"({missing_ratio:.0%}) / quarter≠4の年度ファイル {len(buckets['INTERIM_AS_ANNUAL'])}件 (公開ゲート必須)")

    _write({"status": verdict, "ran_at": datetime.now().isoformat(timespec="seconds"),
            "fy": fy, "counts": {k: len(v) for k, v in buckets.items()},
            "details": {k: v[:200] for k, v in buckets.items() if k != "OK" and v}})
    if args.strict and verdict == "FAIL":
        return 1
    return 0


def _write(summary: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
