"""
Daily EDINET + TDnet → Extract → Hidden Assets → Frontend → Deploy パイプライン

毎日:
  1. EDINET API: 新着有報/半期報告書を取得 → 設備抽出 → 含み益計算 → フロント統合
  2. TDnet: 新着決算短信 PDF をダウンロード
  3. metrics_summary 再生成 → git push → Cloudflare 自動デプロイ

使い方:
  python daily_update.py                    # 昨日分 (デフォルト)
  python daily_update.py --date 2026-04-17  # 指定日
  python daily_update.py --days 7           # 直近7日分
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import urllib.request
import requests

# ===========================================================================
# Paths (override with env vars in CI environments)
# ===========================================================================
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parent)
PDF_ROOT = Path(os.environ.get("PDF_ROOT") or (PROJECT_ROOT / "pdf_xbrl"))
FIXED_ASSETS_ROOT = Path(os.environ.get("FIXED_ASSETS_ROOT") or (PROJECT_ROOT / "fixed_assets_store"))
STOCKFLOW_ROOT = Path(os.environ.get("STOCKFLOW_ROOT") or (PROJECT_ROOT / "StockFlow"))
FRONTEND_DATA = STOCKFLOW_ROOT / "frontend" / "public" / "data"
INDUSTRY_JSON = STOCKFLOW_ROOT / "scripts" / "industry_mapping.json"
LAND_PRICE_DB = Path(os.environ.get("LAND_PRICE_DB") or (PROJECT_ROOT / "land_price_data" / "land_price_by_city.json"))
ENV_FILE = PROJECT_ROOT / "backend" / ".env"
LOG_DIR = Path(os.environ.get("LOG_DIR") or (PROJECT_ROOT / "logs"))
PYTHON = os.environ.get("PYTHON_EXE") or sys.executable

# xbrl_store canonical location.
# Existing canonical is financial_analysis_system/xbrl_store (3886 folders, used by generate_all_companies).
# Allow override via XBRL_STORE env var (for cloud setup where R2 syncs to PROJECT_ROOT/xbrl_store).
XBRL_STORE = Path(
    os.environ.get("XBRL_STORE")
    or (PROJECT_ROOT / "financial_analysis_system" / "xbrl_store")
)

# ===========================================================================
# sys.path for imports
# ===========================================================================
sys.path.insert(0, str(PROJECT_ROOT / "financial_analysis_system"))
sys.path.insert(0, str(STOCKFLOW_ROOT / "scripts"))
sys.path.insert(0, str(STOCKFLOW_ROOT / "frontend" / "scripts"))

# ===========================================================================
# EDINET API
# ===========================================================================
EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
DOC_TYPES_TARGET = {"120", "130", "160", "170"}  # 有報, 訂正有報, 半期報告書, 訂正半期


def load_api_key() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("EDINET_API_KEY="):
                return line.split("=", 1)[1].strip()
    key = os.environ.get("EDINET_API_KEY", "")
    if not key:
        raise RuntimeError("EDINET_API_KEY not found in .env or environment")
    return key


def load_industry_map() -> dict:
    with INDUSTRY_JSON.open(encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# Step 1: Fetch EDINET document list
# ===========================================================================
@dataclass
class EdinetDoc:
    doc_id: str
    sec_code: str  # 5-digit (last digit is check digit, first 4 = ticker)
    ticker: str
    filer_name: str
    doc_type_code: str
    period_end: str  # YYYY-MM-DD
    fiscal_year: int


def fetch_documents(date_str: str, api_key: str, industry_map: dict, log: logging.Logger) -> list[EdinetDoc]:
    url = f"{EDINET_API_BASE}/documents.json"
    params = {"date": date_str, "type": 2, "Subscription-Key": api_key}

    log.info(f"Querying EDINET for {date_str}...")
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        log.error(f"EDINET API returned {resp.status_code}: {resp.text[:200]}")
        return []

    data = resp.json()
    results = data.get("results", [])
    log.info(f"  {len(results)} total documents on {date_str}")

    docs = []
    for r in results:
        doc_type = r.get("docTypeCode", "")
        if doc_type not in DOC_TYPES_TARGET:
            continue
        sec_code = r.get("secCode") or ""
        if not sec_code or len(sec_code) < 4:
            continue
        ticker = sec_code[:4]
        if ticker not in industry_map:
            # Alpha-numeric tickers (e.g., 130A)
            ticker = sec_code[:5].rstrip("0") if len(sec_code) >= 5 else ticker
            if ticker not in industry_map:
                continue
        period_end = r.get("periodEnd", "")
        if not period_end:
            continue
        fiscal_year = int(period_end[:4])

        docs.append(EdinetDoc(
            doc_id=r["docID"],
            sec_code=sec_code,
            ticker=ticker,
            filer_name=r.get("filerName", ""),
            doc_type_code=doc_type,
            period_end=period_end,
            fiscal_year=fiscal_year,
        ))

    log.info(f"  {len(docs)} 有報/訂正有報 from listed companies")
    return docs


# ===========================================================================
# Step 2: Download ZIPs
# ===========================================================================
def safe_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s)


def download_reports(docs: list[EdinetDoc], api_key: str, log: logging.Logger) -> list[dict]:
    downloaded = []
    for doc in docs:
        year_dir = PDF_ROOT / str(doc.fiscal_year) / "有報"
        year_dir.mkdir(parents=True, exist_ok=True)

        doc_type_label = "有報" if doc.doc_type_code == "120" else "訂正有報"
        period_compact = doc.period_end.replace("-", "")
        fname = f"{doc.ticker}_{safe_filename(doc.filer_name)}_{period_compact}_{doc_type_label}_{doc.doc_id}.zip"
        zip_path = year_dir / fname

        if zip_path.exists():
            log.info(f"  [SKIP] {fname} (already exists)")
            # Still add to downloaded list for processing
            downloaded.append({
                "zip_path": zip_path, "ticker": doc.ticker,
                "name": doc.filer_name, "fiscal_year": doc.fiscal_year,
                "doc_id": doc.doc_id, "skipped": True,
            })
            continue

        url = f"{EDINET_API_BASE}/documents/{doc.doc_id}"
        params = {"type": 1, "Subscription-Key": api_key}  # type=1: XBRL ZIP
        log.info(f"  Downloading {fname}...")
        try:
            resp = requests.get(url, params=params, timeout=120)
            if resp.status_code != 200:
                log.error(f"    Failed: HTTP {resp.status_code}")
                continue
            if len(resp.content) < 1000:
                log.warning(f"    Tiny file ({len(resp.content)} bytes), skipping")
                continue
            zip_path.write_bytes(resp.content)
            log.info(f"    Saved ({len(resp.content):,} bytes)")
            downloaded.append({
                "zip_path": zip_path, "ticker": doc.ticker,
                "name": doc.filer_name, "fiscal_year": doc.fiscal_year,
                "doc_id": doc.doc_id, "skipped": False,
            })
        except Exception as e:
            log.error(f"    Download error: {e}")
        time.sleep(0.5)

    return downloaded


# ===========================================================================
# Step 2b: Extract XBRL financial data from downloaded ZIPs
# ===========================================================================
def extract_xbrl_for(downloaded: list[dict], log: logging.Logger) -> set[str]:
    """新規DLされたZIPがある年度に対し xbrl_batch_extractor を --skip-existing で実行。

    Returns: 影響を受けた fiscal_year のセット
    """
    if not downloaded:
        return set()

    new_zips = [d for d in downloaded if not d.get("skipped")]
    if not new_zips:
        log.info("  No new ZIPs to extract (all skipped)")
        return set()

    years_affected: set[int] = {d["fiscal_year"] for d in new_zips}
    log.info(f"  Extracting XBRL for fiscal years: {sorted(years_affected)}")

    script = PROJECT_ROOT / "financial_analysis_system" / "xbrl_batch_extractor.py"
    for year in sorted(years_affected):
        cmd = [
            PYTHON, str(script),
            "--scan-folder", str(PDF_ROOT),
            "--years", str(year),
            "--skip-existing",
        ]
        log.info(f"  Running xbrl_batch_extractor --years {year} --skip-existing")
        try:
            # Ensure subprocess writes to canonical xbrl_store (not cwd-relative ./xbrl_store)
            env = {**os.environ, "XBRL_STORE": str(XBRL_STORE)}
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1800,
                encoding="utf-8", errors="replace", env=env,
            )
            tail = (result.stdout or "").splitlines()[-15:]
            for line in tail:
                log.info(f"    {line}")
            if result.returncode != 0:
                log.error(f"  xbrl extract failed: {result.stderr[:500]}")
        except subprocess.TimeoutExpired:
            log.error(f"  xbrl extract timeout for year {year}")
        except Exception as e:
            log.error(f"  xbrl extract error: {e}")

    return {str(y) for y in years_affected}


# ===========================================================================
# Step 5b: Run generate_all_companies + integrate_hidden_assets (full sweep)
# ===========================================================================
def regenerate_frontend_full(log: logging.Logger):
    """xbrl_store + Report → frontend JSON 全件再生成 + 含み資産統合."""
    gen_script = STOCKFLOW_ROOT / "scripts" / "generate_all_companies.py"
    log.info("  Running generate_all_companies.py (full sweep)...")
    try:
        result = subprocess.run(
            [PYTHON, str(gen_script)],
            cwd=str(STOCKFLOW_ROOT),
            capture_output=True, text=True, timeout=1800,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            log.error(f"  generate_all_companies failed: {result.stderr[:500]}")
        else:
            for line in (result.stdout or "").splitlines()[-6:]:
                log.info(f"    {line}")
    except Exception as e:
        log.error(f"  generate_all_companies error: {e}")

    integrate_script = STOCKFLOW_ROOT / "scripts" / "integrate_hidden_assets.py"
    log.info("  Running integrate_hidden_assets.py (full sweep)...")
    try:
        result = subprocess.run(
            [PYTHON, str(integrate_script)],
            cwd=str(STOCKFLOW_ROOT),
            capture_output=True, text=True, timeout=900,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            log.error(f"  integrate_hidden_assets failed: {result.stderr[:500]}")
        else:
            for line in (result.stdout or "").splitlines()[-3:]:
                log.info(f"    {line}")
    except Exception as e:
        log.error(f"  integrate_hidden_assets error: {e}")


# ===========================================================================
# Step 3: Extract facilities
# ===========================================================================
def extract_facilities_for(downloaded: list[dict], industry_map: dict, log: logging.Logger) -> list[dict]:
    from extract_setsubi import extract_facilities
    from dataclasses import asdict

    extracted = []
    for d in downloaded:
        ticker = d["ticker"]
        info = industry_map.get(ticker, {})
        industry = info.get("sector33", "")
        company_name = info.get("name", d["name"])

        try:
            recs = extract_facilities(str(d["zip_path"]), industry)
        except Exception as e:
            log.error(f"  [ERR] {ticker} extract: {e}")
            continue

        if not recs:
            log.info(f"  {ticker} {company_name}: 0 records (no land)")
            continue

        dir_name = safe_filename(f"{ticker}_{company_name}")
        out_dir = FIXED_ASSETS_ROOT / dir_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{d['fiscal_year']}_facilities.json"

        jp = [r for r in recs if r.country == "JP"]
        with out_file.open("w", encoding="utf-8") as f:
            json.dump({
                "company_code": ticker,
                "company_name": company_name,
                "fiscal_year": d["fiscal_year"],
                "industry_sector33": industry,
                "total_records": len(recs),
                "jp_records": len(jp),
                "facilities": [asdict(r) for r in recs],
            }, f, ensure_ascii=False, separators=(",", ":"))

        log.info(f"  {ticker} {company_name}: {len(recs)} records ({len(jp)} JP)")
        extracted.append({
            "ticker": ticker, "name": company_name,
            "fiscal_year": d["fiscal_year"],
            "facilities_path": out_file,
            "jp_count": len(jp),
        })

    return extracted


# ===========================================================================
# Step 4: Calculate hidden assets
# ===========================================================================
def calculate_hidden_assets_for(extracted: list[dict], log: logging.Logger) -> list[str]:
    from calculate_hidden_assets import LandPriceDB, calculate_for_company

    db = LandPriceDB(LAND_PRICE_DB)
    log.info(f"  Land price DB: {len(db.cities):,} cities")

    affected_tickers = []
    for e in extracted:
        fp = e["facilities_path"]
        try:
            result = calculate_for_company(fp, db)
        except Exception as ex:
            log.error(f"  [ERR] {e['ticker']} calculate: {ex}")
            continue

        out_file = fp.parent / f"{e['fiscal_year']}_hidden_assets.json"
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

        gain = result.get("summary", {}).get("total_hidden_gain_mil_yen", 0)
        log.info(f"  {e['ticker']}: gain={gain:,.0f} mil")
        affected_tickers.append(e["ticker"])

    return affected_tickers


# ===========================================================================
# Step 5: Integrate into frontend JSONs
# ===========================================================================
def integrate_frontend(affected_tickers: list[str], log: logging.Logger):
    from integrate_hidden_assets import build_hidden_assets_payload

    updated = 0
    for ticker in affected_tickers:
        # Find hidden_assets file
        candidates = list(FIXED_ASSETS_ROOT.glob(f"{ticker}_*/2024_hidden_assets.json"))
        if not candidates:
            candidates = list(FIXED_ASSETS_ROOT.glob(f"{ticker}_*/2025_hidden_assets.json"))
        if not candidates:
            log.warning(f"  {ticker}: no hidden_assets file")
            continue

        asset_path = candidates[0]
        front_path = FRONTEND_DATA / f"{ticker}.json"
        if not front_path.exists():
            log.warning(f"  {ticker}: no frontend JSON")
            continue

        try:
            with asset_path.open(encoding="utf-8") as f:
                asset_data = json.load(f)
            with front_path.open(encoding="utf-8") as f:
                front = json.load(f)

            front["hiddenAssets"] = build_hidden_assets_payload(asset_data)

            with front_path.open("w", encoding="utf-8") as f:
                json.dump(front, f, ensure_ascii=False, separators=(",", ":"))

            updated += 1
        except Exception as e:
            log.error(f"  {ticker}: integrate error: {e}")

    log.info(f"  Frontend updated: {updated}/{len(affected_tickers)}")


# ===========================================================================
# Step 6: Regenerate metrics_summary
# ===========================================================================
def regenerate_metrics(log: logging.Logger):
    script = STOCKFLOW_ROOT / "frontend" / "scripts" / "generate_metrics_summary.py"
    log.info("  Running generate_metrics_summary.py...")
    result = subprocess.run(
        [PYTHON, str(script)],
        cwd=str(STOCKFLOW_ROOT / "frontend"),
        capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        log.error(f"  metrics_summary failed: {result.stderr[:500]}")
    else:
        for line in result.stdout.splitlines()[-3:]:
            log.info(f"    {line}")


# ===========================================================================
# Step 7: Git commit + push
# ===========================================================================
def git_commit_push(affected_tickers: list[str], date_str: str, log: logging.Logger) -> bool:
    cwd = str(STOCKFLOW_ROOT)

    # Stage all frontend/public/data/ changes (xbrl re-extract may have touched many)
    subprocess.run(["git", "add", str(FRONTEND_DATA)], cwd=cwd, capture_output=True)

    # Check if there are changes
    status = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=cwd, capture_output=True, text=True)
    if not status.stdout.strip():
        log.info("  No changes to commit")
        return False

    n_files = len([l for l in status.stdout.strip().splitlines() if l.strip()]) - 1  # last line is summary
    msg = f"daily: update {n_files} files ({date_str})"
    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error(f"  git commit failed: {result.stderr[:300]}")
        return False
    log.info(f"  Committed: {msg} ({n_files} files)")

    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        log.error(f"  git push failed: {result.stderr[:300]}")
        return False
    log.info("  Pushed to origin/main")
    return True


# ===========================================================================
# Step T: TDnet 短信ダウンロード
# ===========================================================================
TDNET_BASE = "https://www.release.tdnet.info/inbs"
TANSHINN_ROOT = PDF_ROOT / "短信"
TANSHINN_KEYWORDS = ["決算短信", "四半期決算短信"]


def fetch_tdnet_page(date_compact: str, page: int = 1) -> Optional[str]:
    url = f"{TDNET_BASE}/I_list_{page:03d}_{date_compact}.html"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def download_tanshinn(date_str: str, industry_map: dict, log: logging.Logger) -> tuple[int, list[dict]]:
    """Returns (total_downloaded_pdfs, zip_downloads).
    zip_downloads: [{ticker, name, zip_path, quarter, date_str}, ...]
    """
    date_compact = date_str.replace("-", "")
    year = date_str[:4]
    year_dir = TANSHINN_ROOT / year
    year_dir.mkdir(parents=True, exist_ok=True)

    page = 1
    total_downloaded = 0
    zip_downloads = []
    while True:
        html = fetch_tdnet_page(date_compact, page)
        if not html or len(html) < 2000:
            break

        rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)
        found_any = False
        for row in rows:
            if not any(kw in row for kw in TANSHINN_KEYWORDS):
                continue
            found_any = True

            pdfs = re.findall(r'href="([^"]+\.pdf)"', row)
            zips = re.findall(r'href="([^"]+\.zip)"', row)
            if not pdfs:
                continue
            pdf_name = pdfs[0]
            zip_name = zips[0] if zips else None

            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 4:
                continue
            cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            code_raw = cleaned[1].strip()
            ticker = code_raw[:4] if len(code_raw) >= 4 else code_raw
            if ticker not in industry_map:
                continue

            company_name = industry_map[ticker].get("name", cleaned[2])
            title = cleaned[3] if len(cleaned) > 3 else ""

            if "第1四半期" in title or "第１四半期" in title:
                quarter = "1Q"
            elif "第2四半期" in title or "第２四半期" in title or "中間" in title:
                quarter = "2Q"
            elif "第3四半期" in title or "第３四半期" in title:
                quarter = "3Q"
            else:
                quarter = "FY"

            safe_name = safe_filename(f"{ticker}_{company_name}")
            company_dir = year_dir / safe_name
            company_dir.mkdir(parents=True, exist_ok=True)

            pdf_path = company_dir / f"{date_compact}_{quarter}_{pdf_name}"
            zip_path = company_dir / f"{date_compact}_{quarter}_{zip_name}" if zip_name else None

            # Download PDF
            if not pdf_path.exists():
                try:
                    req = urllib.request.Request(
                        f"{TDNET_BASE}/{pdf_name}",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    )
                    resp = urllib.request.urlopen(req, timeout=30)
                    pdf_path.write_bytes(resp.read())
                    log.info(f"  [短信PDF] {ticker} {company_name[:15]} {quarter} ({pdf_path.stat().st_size:,} bytes)")
                    total_downloaded += 1
                    time.sleep(0.2)
                except Exception as e:
                    log.error(f"  [短信PDF] {ticker} error: {e}")

            # Download XBRL ZIP (Summary contains structured financials)
            if zip_name and zip_path and not zip_path.exists():
                try:
                    req = urllib.request.Request(
                        f"{TDNET_BASE}/{zip_name}",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    )
                    resp = urllib.request.urlopen(req, timeout=30)
                    zip_path.write_bytes(resp.read())
                    log.info(f"  [短信XBRL] {ticker} {quarter} ({zip_path.stat().st_size:,} bytes)")
                    time.sleep(0.2)
                except Exception as e:
                    log.error(f"  [短信XBRL] {ticker} error: {e}")
                    zip_path = None

            if zip_path and zip_path.exists():
                zip_downloads.append({
                    "ticker": ticker, "name": company_name,
                    "zip_path": zip_path, "quarter": quarter, "date_str": date_str,
                })

        # NOTE: pre-2026-05-25 までは「page1 に 決算短信 があり、なくなったら終了」だったが
        # TDnet は時間降順表示で、5/25のように朝の決算短信が page2-4 に押し下がる場合あり。
        # 404 / empty で break するのは fetch_tdnet_page で既に処理済。ここでは継続する。
        page += 1
        time.sleep(0.5)

    return total_downloaded, zip_downloads


# ===========================================================================
# Step T2: Extract Tanshin Summary XBRL → xbrl_store
# ===========================================================================
def extract_tanshin_zips(zip_downloads: list[dict], log: logging.Logger) -> int:
    """Process downloaded 短信 ZIPs through tanshin_xbrl_extractor.

    Writes to xbrl_store/{ticker}_{name}/{year}[_Q{n}].json — but only if
    no EDINET file exists (overwrite=False protects EDINET data).
    """
    if not zip_downloads:
        return 0

    try:
        from tanshin_xbrl_extractor import process_zip_file
    except ImportError as e:
        log.error(f"  tanshin_xbrl_extractor import failed: {e}")
        return 0

    written = 0
    for d in zip_downloads:
        try:
            out = process_zip_file(d["zip_path"], XBRL_STORE, d["name"], overwrite=False)
            if out:
                log.info(f"  [短信→XBRL] {d['ticker']} {d['quarter']} → {out.name}")
                written += 1
        except Exception as e:
            log.error(f"  [短信→XBRL] {d['ticker']} {d['quarter']} error: {e}")
    return written


# ===========================================================================
# Main
# ===========================================================================
def setup_logging(date_str: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("daily_update")
    log.setLevel(logging.DEBUG)
    # Remove old handlers to avoid duplicate output on repeated calls
    log.handlers.clear()

    fh = logging.FileHandler(
        LOG_DIR / f"daily_update_{date_str.replace('-', '')}.log",
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)

    return log


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Daily EDINET → Deploy pipeline")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD). Default: yesterday JST")
    parser.add_argument("--days", type=int, default=1, help="Process N days back (default: 1)")
    args = parser.parse_args()

    # Determine target dates
    if args.date:
        dates = [args.date]
    else:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, args.days + 1)]
        dates.reverse()  # oldest first

    # Pre-checks
    if not PDF_ROOT.exists():
        print(f"ERROR: {PDF_ROOT} not found. Is E: drive mounted?")
        sys.exit(1)

    api_key = load_api_key()
    industry_map = load_industry_map()

    all_affected = []
    for date_str in dates:
        log = setup_logging(date_str)
        t0 = time.time()
        log.info(f"{'='*60}")
        log.info(f"Daily update pipeline for {date_str}")
        log.info(f"{'='*60}")

        # Step 1: Fetch
        log.info("Step 1: Fetch EDINET documents")
        docs = fetch_documents(date_str, api_key, industry_map, log)
        if not docs:
            log.info("No target documents. Done.")
            continue

        # Step 2: Download
        log.info(f"Step 2: Download {len(docs)} reports")
        downloaded = download_reports(docs, api_key, log)
        new_count = sum(1 for d in downloaded if not d.get("skipped"))
        log.info(f"  Downloaded: {new_count} new, {len(downloaded) - new_count} skipped")

        # Step 2b: Extract XBRL financial data (新規 ZIP のみ年度単位で再抽出)
        if new_count > 0:
            log.info("Step 2b: Extract XBRL financial data")
            years_extracted = extract_xbrl_for(downloaded, log)
            if years_extracted:
                all_affected.append("__xbrl_changed__")  # marker for full regen
                log.info(f"  XBRL years extracted: {sorted(years_extracted)}")

        # Step 3: Extract
        log.info("Step 3: Extract facilities")
        extracted = extract_facilities_for(downloaded, industry_map, log)
        log.info(f"  Extracted: {len(extracted)} companies")

        # Step 4-5: Calculate hidden assets + integrate (if any extracted)
        affected = []
        if extracted:
            log.info("Step 4: Calculate hidden assets")
            affected = calculate_hidden_assets_for(extracted, log)
            log.info(f"  Calculated: {len(affected)} companies")
            all_affected.extend(affected)

            log.info("Step 5: Integrate frontend JSONs")
            integrate_frontend(affected, log)

        # Step T: TDnet 短信ダウンロード (PDF + XBRL ZIP)
        log.info("Step T: Download TDnet 短信 (PDF + Summary XBRL)")
        n_tanshinn, zip_downloads = download_tanshinn(date_str, industry_map, log)
        log.info(f"  短信PDF: {n_tanshinn} / XBRL ZIPs: {len(zip_downloads)}")

        # Step T2: Tanshin Summary XBRL → xbrl_store (EDINETに先んじてWebに反映)
        n_tanshinn_extracted = 0
        if zip_downloads:
            log.info("Step T2: Extract Tanshin Summary XBRL")
            n_tanshinn_extracted = extract_tanshin_zips(zip_downloads, log)
            if n_tanshinn_extracted > 0:
                all_affected.append("__tanshin_changed__")
            log.info(f"  Tanshin extracted: {n_tanshinn_extracted}")

        elapsed = time.time() - t0
        log.info(f"Date {date_str} done in {elapsed:.1f}s: {len(docs)} EDINET docs, {new_count} downloaded, {len(extracted)} extracted, {len(affected)} calculated, {n_tanshinn} 短信PDF, {n_tanshinn_extracted} 短信XBRL→store")

    if not all_affected:
        print("No companies updated across all dates.")
        return

    # Step 5b: Run full frontend regeneration (xbrl_store→JSON + hidden assets)
    log = setup_logging(dates[-1])
    log.info("Step 5b: Regenerate frontend (generate_all_companies + integrate_hidden_assets)")
    regenerate_frontend_full(log)

    # Step 6: Regenerate metrics (once for all dates)
    log.info("Step 6: Regenerate metrics_summary")
    regenerate_metrics(log)

    # Step 7: Git commit + push
    log.info("Step 7: Git commit + push")
    unique_tickers = list(dict.fromkeys(all_affected))
    pushed = git_commit_push(unique_tickers, dates[-1], log)

    log.info(f"{'='*60}")
    log.info(f"Pipeline complete: {len(unique_tickers)} companies updated, push={'OK' if pushed else 'skipped'}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
