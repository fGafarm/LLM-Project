"""
Tanshin Audit — TDnet 決算短信 vs xbrl_store の日次差分監査.

毎日 daily_update の最後に走らせて、
  「TDnet 上に存在する決算短信 (XBRL付き)」 - 「xbrl_store 上の同 announcement_date エントリ」
の差分を検出する。差分ありなら exit code 1 を返し、daily_update / GitHub Actions を失敗扱いに。

潜在バグ (例: 2026-05-25 の page1 早期 break バグ) が将来また紛れ込んでも即発見できるよう、
監査は **コードで強制** することが目的。

使い方:
  python tanshin_audit.py                    # 昨日分
  python tanshin_audit.py --date 2026-05-25  # 指定日
  python tanshin_audit.py --days 7           # 直近7日
  python tanshin_audit.py --days 7 --strict  # 差分があれば exit 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parent)
XBRL_STORE = Path(os.environ.get("XBRL_STORE") or (PROJECT_ROOT / "financial_analysis_system" / "xbrl_store"))
STOCKFLOW_ROOT = Path(os.environ.get("STOCKFLOW_ROOT") or (PROJECT_ROOT / "StockFlow"))
INDUSTRY_JSON = STOCKFLOW_ROOT / "scripts" / "industry_mapping.json"

TDNET_BASE = "https://www.release.tdnet.info/inbs"
TANSHINN_KEYWORDS = ("決算短信", "四半期決算短信")

# 抽出失敗が既知の構造的限界 (Summary無し / Attachment-only ZIPで tse-(an)edjpfr taxonomy使用) で、
# 毎日警告するとノイズになる ticker。将来 jpfr-t-cte/jpcrp_cor タグマッパーを実装すれば回収可能。
# 確認方法: E:/PDF/PDF+XBRL/短信/{year}/{ticker}_*/*FY*.zip を unzip して
#   - XBRLData/Summary/ が無い
#   - XBRLData/Attachment/0101010-acbs01-tse-acedjpfr-... 等が存在
# なら該当する。
KNOWN_UNEXTRACTABLE: set[str] = {
    "5233",  # 太平洋セメント (Attachment-only, tse-acedjpfr taxonomy)
    "6572",  # オープングループ (同上, 2月期決算)
    "9104",  # 商船三井 (同上)
    "6360",  # 東京自働機械製作所 (Attachment-only, tse-anedjpfr taxonomy)
    "7995",  # バルカー (同上)
    "1948",  # 弘電社 (同上)
    "7031",  # インバウンドテック (同上)
}


def fetch_tdnet_page(date_compact: str, page: int):
    url = f"{TDNET_BASE}/I_list_{page:03d}_{date_compact}.html"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def get_tdnet_expected(date_str: str, industry_map: dict) -> set[str]:
    """TDnet の指定日 disclosures から、XBRL付き決算短信を出した ticker 集合を返す.

    重要: download_tanshinn と同様に **全ページスキャン** する (page1 で 0件でも継続)。
    旧バグ (if not found_any: break) は使わない。
    """
    date_compact = date_str.replace("-", "")
    expected: set[str] = set()
    for page in range(1, 30):  # 安全上限 30 ページ
        html = fetch_tdnet_page(date_compact, page)
        if not html or len(html) < 2000:
            break
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        for row in rows:
            if not any(kw in row for kw in TANSHINN_KEYWORDS):
                continue
            zips = re.findall(r'href="([^"]+\.zip)"', row)
            if not zips:
                continue  # ZIP 無しは構造的に抽出不可なので audit 対象外
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 4:
                continue
            cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            code = cleaned[1].strip()[:4]
            if code not in industry_map:
                continue
            if code in KNOWN_UNEXTRACTABLE:
                continue
            expected.add(code)
        time.sleep(0.3)
    return expected


def get_xbrl_actual(date_str: str, window_days: int = 14) -> dict[str, list[Path]]:
    """xbrl_store 上で対象 ticker がデータを持つか判定の {ticker: [paths]} を返す.

    判定基準 (どちらかを満たせば covered):
      (a) tanshin 由来エントリで announcement_date が TDnet 掲載日±window_days日内
      (b) 同一 fiscal_year (target_date.year) の 通期/四半期 entry が存在 (EDINET含む)

    Why (b): EDINET 既存データがある場合 tanshin は overwrite しない設計のため、
    tanshin 抽出が無くても EDINET データで Web 上は機能する。audit の目的は
    「catastrophic な抜け (page miss 等で大量取りこぼし)」検知なので、データ源を問わず
    「該当年度のデータがあるか」で covered と判定する。
    """
    from datetime import date
    target = date.fromisoformat(date_str)
    target_year = target.year
    actual: dict[str, list[Path]] = {}
    if not XBRL_STORE.exists():
        return actual

    for company_dir in XBRL_STORE.iterdir():
        if not company_dir.is_dir():
            continue
        m = re.match(r"^([0-9A-Z]{4,5})_", company_dir.name)
        if not m:
            continue
        ticker = m.group(1)[:4]

        for jf in company_dir.glob("20*.json"):
            if "_raw_tags" in jf.name or "_statements" in jf.name:
                continue
            # Quick filename match: target_year がファイル名に含まれるか
            if str(target_year) not in jf.name:
                continue
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # 判定 (a): tanshin 由来で日付窓内
            if d.get("source") == "tanshin":
                ann = d.get("announcement_date")
                if ann:
                    try:
                        ann_date = date.fromisoformat(ann)
                        if abs((ann_date - target).days) <= window_days:
                            actual.setdefault(ticker, []).append(jf)
                            continue
                    except (ValueError, TypeError):
                        pass
            # 判定 (b): EDINET含む任意のエントリで該当年度
            fy = d.get("fiscal_year")
            if fy and abs(int(fy) - target_year) <= 1:  # 直近年度±1
                actual.setdefault(ticker, []).append(jf)

    return actual


def audit_date(date_str: str, industry_map: dict) -> dict:
    """1日分の audit. 結果 dict を返す."""
    expected = get_tdnet_expected(date_str, industry_map)
    actual = get_xbrl_actual(date_str)

    missing = expected - set(actual.keys())
    extra = set(actual.keys()) - expected  # 予想外の追加 (まあ問題ない)

    return {
        "date": date_str,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "pass": len(missing) == 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Tanshin XBRL extraction audit (TDnet vs xbrl_store)")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday JST)")
    parser.add_argument("--days", type=int, default=1, help="Audit N days back (default: 1)")
    parser.add_argument("--strict", action="store_true", help="Exit code 1 if any missing")
    parser.add_argument("--verbose", action="store_true", help="Print extra details per date")
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, args.days + 1)]
        dates.reverse()

    if not INDUSTRY_JSON.exists():
        print(f"ERROR: industry_mapping.json not found: {INDUSTRY_JSON}")
        sys.exit(2)

    with INDUSTRY_JSON.open(encoding="utf-8") as f:
        industry_map = json.load(f)

    print(f"=== Tanshin Audit ===")
    print(f"xbrl_store: {XBRL_STORE}")
    print(f"Dates: {dates}")
    print()

    total_missing = 0
    failed_dates = []
    for date_str in dates:
        result = audit_date(date_str, industry_map)
        status = "OK" if result["pass"] else "MISS"
        miss_count = len(result["missing"])
        print(f"  [{status}] {date_str}: expected={result['expected_count']:3d} actual={result['actual_count']:3d} missing={miss_count}")
        if miss_count > 0:
            print(f"         missing tickers: {', '.join(result['missing'])}")
            total_missing += miss_count
            failed_dates.append(date_str)
        if args.verbose and result["extra"]:
            print(f"         (extra in store, not on TDnet today: {', '.join(result['extra'][:10])}{'...' if len(result['extra']) > 10 else ''})")
        time.sleep(0.5)

    print()
    print(f"=== Summary ===")
    print(f"Days audited: {len(dates)}")
    print(f"Days with missing: {len(failed_dates)} {failed_dates if failed_dates else ''}")
    print(f"Total missing: {total_missing}")

    if args.strict and total_missing > 0:
        print(f"\nAUDIT FAILED (strict mode): {total_missing} ticker(s) missing")
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
