"""
US Yuho Audit — SEC submissions API vs us_xbrl_store の expected-vs-actual 監査.

日本の yuho_audit.py の米国版 (EXPANSION_BLUEPRINT §2)。
「SECに10-KがN件提出されている → storeに{FY}.jsonがある → revenueが入っている」を突合し、
US側パイプラインのサイレント欠落を named で検知する。

データ源 (認証不要・無料):
  - ticker→CIK: https://www.sec.gov/files/company_tickers.json
  - 提出一覧:   https://data.sec.gov/submissions/CIK{10桁}.json (宣言UA必須, 10req/s上限)

使い方:
  python us_yuho_audit.py                    # frontend掲載のUS企業 (デフォルト)
  python us_yuho_audit.py --tickers AAPL,MSFT
  python us_yuho_audit.py --store-all        # us_xbrl_store 全社 (約6,000社, 数十分)
  python us_yuho_audit.py --years 2023,2024,2025 --strict

判定: OK / MISSING ({fy}.jsonなし) / NO_REVENUE / NOT_IN_SEC (ticker→CIK解決不能)
制限 (MVP): storeにaccession未保存のため訂正(10-K/A)のSTALE検知は未対応。20-F(外国民間発行体)は対象外。
終了コード: 0=PASS / 1=欠落あり(--strict時) / 2=環境エラー
常に logs/us_yuho_audit_last.json にサマリを書く。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parent)
US_STORE = PROJECT_ROOT / "us_financial_analysis" / "us_xbrl_store"
FRONTEND_DATA = PROJECT_ROOT / "StockFlow" / "frontend" / "public" / "data"
LOG_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = LOG_DIR / "us_submissions_cache"
SUMMARY_JSON = LOG_DIR / "us_yuho_audit_last.json"

# SEC fair access: 宣言UA必須 (無いと403)。config.py と同じ流儀
sys.path.insert(0, str(PROJECT_ROOT / "us_financial_analysis"))
try:
    from config import SEC_USER_AGENT  # noqa: E402
except ImportError:
    SEC_USER_AGENT = "StockFlow Research (contact via kinmyakucode.com)"

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SLEEP = 0.13  # ~7.7req/s (上限10req/sに安全マージン)


def load_cik_map() -> dict[str, int]:
    """ticker -> CIK。company_tickers.json は小さいので日次キャッシュ。"""
    cache = LOG_DIR / "us_cik_map.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86400:
        return {k: int(v) for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()  # {"0": {"cik_str":..., "ticker":..., "title":...}, ...}
    m = {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(m), encoding="utf-8")
    return m


def fetch_submissions(cik: int) -> dict | None:
    """submissions API (当日キャッシュつき)。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cf = CACHE_DIR / f"CIK{cik:010d}.json"
    if cf.exists() and (time.time() - cf.stat().st_mtime) < 86400:
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    try:
        resp = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                            headers=HEADERS, timeout=30)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        j = resp.json()
    except ValueError:
        return None
    # キャッシュは監査に必要な最小限 (recentのform/periodOfReport/accession) に絞る
    recent = (j.get("filings") or {}).get("recent") or {}
    slim = {"filings": {"recent": {k: recent.get(k, []) for k in
                                   ("form", "reportDate", "accessionNumber", "filingDate")}}}
    cf.write_text(json.dumps(slim), encoding="utf-8")
    time.sleep(SLEEP)
    return slim


def expected_fys(subs: dict, target_years: set[int]) -> dict[int, str]:
    """10-K/10-K/A の periodOfReport から {FY年: accession} を返す (同FYは最新filed優先)。"""
    recent = (subs.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    dates = recent.get("reportDate", [])
    accs = recent.get("accessionNumber", [])
    out: dict[int, str] = {}
    for i, form in enumerate(forms):
        if form not in ("10-K", "10-K/A"):
            continue
        rd = dates[i] if i < len(dates) else ""
        if not rd:
            continue
        fy = int(rd[:4])
        if fy in target_years and fy not in out:  # recentは新しい順 → 最初に出たものが最新
            out[fy] = accs[i] if i < len(accs) else ""
    return out


def frontend_us_tickers() -> list[str]:
    """frontend index.json から US ticker (英字・TW-以外) を抽出。"""
    idx = json.loads((FRONTEND_DATA / "index.json").read_text(encoding="utf-8"))
    companies = idx.get("companies", idx if isinstance(idx, list) else [])
    out = []
    for c in companies:
        t = str(c.get("ticker") or "")
        if t and not t.startswith("TW-") and t.isascii() and any(ch.isalpha() for ch in t) and not t[0].isdigit():
            out.append(t.upper())
    return sorted(set(out))


# 複数クラス上場のticker衝突 (company_tickers.json のCIK後勝ち上書きでBクラスが消える)。
# storeフォルダは勝った方のticker名 → 監査側で別名解決する
TICKER_ALIAS = {"BRK-B": "BRK-A"}


def store_dir_for(ticker: str) -> Path | None:
    for t in (ticker, TICKER_ALIAS.get(ticker, "")):
        if not t:
            continue
        hits = list(US_STORE.glob(f"{t}_*"))
        if hits:
            return hits[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="US 10-K ingestion audit (SEC submissions vs us_xbrl_store)")
    ap.add_argument("--tickers", help="カンマ区切り (省略時: frontend掲載のUS企業)")
    ap.add_argument("--store-all", action="store_true", help="us_xbrl_store 全社を対象")
    ap.add_argument("--years", default="2023,2024,2025")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--max-list", type=int, default=30)
    args = ap.parse_args()

    target_years = {int(y) for y in args.years.split(",")}

    if not US_STORE.is_dir():
        print(f"[CRITICAL] us_xbrl_store が存在しない: {US_STORE}")
        _write_summary({"status": "ENV_ERROR", "problems": ["us_xbrl_store missing"]})
        return 2

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    elif args.store_all:
        tickers = sorted({d.name.split("_")[0] for d in US_STORE.iterdir() if d.is_dir()})
    else:
        tickers = frontend_us_tickers()

    print("=== US Yuho Audit (SEC submissions → us_xbrl_store) ===")
    print(f"対象: {len(tickers)} tickers / years {sorted(target_years)}")

    try:
        cik_map = load_cik_map()
    except Exception as e:
        print(f"[CRITICAL] CIKマップ取得失敗: {e}")
        _write_summary({"status": "ENV_ERROR", "problems": [f"cik_map: {e}"]})
        return 2

    buckets: dict[str, list[str]] = {k: [] for k in ("OK", "MISSING", "NO_REVENUE", "NOT_IN_SEC", "UNVERIFIED")}
    for n, t in enumerate(tickers, 1):
        if n % 50 == 0:
            print(f"  ... {n}/{len(tickers)}")
        cik = cik_map.get(t)
        if not cik:
            buckets["NOT_IN_SEC"].append(t)  # 上場廃止・ticker変更・ETF等
            continue
        subs = fetch_submissions(cik)
        if subs is None:
            buckets["UNVERIFIED"].append(t)
            continue
        exp = expected_fys(subs, target_years)
        d = store_dir_for(t)
        for fy in sorted(exp):
            # 1月末等のH1決算企業は SEC reportDate年 (fy) と発行体FYラベル (store側キー) が
            # 1年ズレる (例: Home Depot FY2024 = periodOfReport 2025-02)。{fy-1}.json も許容
            f = (d / f"{fy}.json") if d else None
            f_shift = (d / f"{fy - 1}.json") if d else None
            if d and f.exists():
                target, label = f, f"{t} FY{fy}"
            elif d and f_shift.exists():
                target, label = f_shift, f"{t} FY{fy} (storeは{fy - 1}ラベル)"
            else:
                buckets["MISSING"].append(f"{t} FY{fy} ({exp[fy]})")
                continue
            try:
                j = json.loads(target.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                buckets["MISSING"].append(f"{t} FY{fy} (parse不能)")
                continue
            rev = (j.get("data") or {}).get("revenue")
            buckets["OK" if rev is not None else "NO_REVENUE"].append(label)

    print()
    print("=== Summary (ticker×FY) ===")
    for k in ("OK", "NO_REVENUE", "MISSING", "NOT_IN_SEC", "UNVERIFIED"):
        print(f"  {k:11s}: {len(buckets[k])}")
    for k in ("MISSING", "NO_REVENUE", "NOT_IN_SEC", "UNVERIFIED"):
        if buckets[k]:
            print(f"\n-- {k} (先頭{args.max_list}) --")
            for line in buckets[k][: args.max_list]:
                print("  " + line)
            if len(buckets[k]) > args.max_list:
                print(f"  ... 他 {len(buckets[k]) - args.max_list} 件")

    hard = len(buckets["MISSING"]) + len(buckets["NO_REVENUE"])
    verdict = "FAIL" if hard else ("PASS_PARTIAL" if buckets["UNVERIFIED"] else "PASS")
    print(f"\n{verdict}: 欠落 {hard}件 (MISSING {len(buckets['MISSING'])} / NO_REVENUE {len(buckets['NO_REVENUE'])}), "
          f"SEC未解決 {len(buckets['NOT_IN_SEC'])}件, 未検証 {len(buckets['UNVERIFIED'])}件")

    _write_summary({
        "status": verdict, "ran_at": datetime.now().isoformat(timespec="seconds"),
        "tickers": len(tickers), "years": sorted(target_years),
        "counts": {k: len(v) for k, v in buckets.items()},
        "details": {k: v[:200] for k, v in buckets.items() if k != "OK" and v},
    })
    if args.strict and (hard or (buckets["UNVERIFIED"] and len(buckets["UNVERIFIED"]) == len(tickers))):
        return 1
    return 0


def _write_summary(summary: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
