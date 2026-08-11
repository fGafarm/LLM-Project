"""
SEC EDGAR 10-K Filing Downloader

10-K (Annual Report) の本文をダウンロードする。
日本の有価証券報告書PDFに相当。SEC filing は主にHTML形式。

1. submissions API で企業のfiling一覧を取得
2. 10-K filing の accession number を特定
3. filing index から primary document (HTML) をダウンロード
4. HTML → テキスト変換して保存

日本側の教訓を反映:
- FY判定: filing_year - 1 は非暦年決算で壊れる → reportDate/periodOfReport使用
- 429エラー: 無限再帰は危険 → ループ + max_retries
- submissions pagination: 古いfilingはfiles配列に分割される
- HTMLゴミ: XBRLデータがbody内に混入 → ix:タグ除去

Usage:
    python edgar_10k_downloader.py --ticker AAPL
    python edgar_10k_downloader.py --ticker AAPL --year 2024
    python edgar_10k_downloader.py --list sp500.txt --skip-existing
    python edgar_10k_downloader.py --list top50.txt --year 2024
"""

import json
import sys
import re
import time
import argparse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser

from config import (
    SEC_USER_AGENT, SEC_BASE_URL, SEC_RATE_LIMIT,
    COMPANY_TICKERS_FILE, FILING_DIR, TARGET_YEARS
)


class HTMLTextExtractor(HTMLParser):
    """
    HTML to text converter for 10-K filings.

    日本側の教訓: XBRLインラインタグ (ix:nonFraction等) が
    body内に埋め込まれていて、単純なHTMLパースだとゴミが混入する。
    → ix: タグ内のテキストは取り込むが、属性値は無視する。
    """

    SKIP_TAGS = {"script", "style", "head", "meta", "link", "title"}
    # XBRL inline tags - テキスト内容は保持するが、hidden属性なら除外
    IX_TAGS = {"ix:nonfraction", "ix:nonnumeric", "ix:fraction",
               "ix:header", "ix:hidden", "ix:resources",
               "ix:continuation", "ix:exclude", "ix:footnote"}

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip_depth = 0
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        attrs_dict = dict(attrs)

        if tag_lower in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        # ix:hidden の中身はXBRLメタデータ → スキップ
        if tag_lower in ("ix:hidden", "ix:header", "ix:resources", "ix:exclude"):
            self._hidden_depth += 1
            return

        if tag_lower in ("br", "p", "div", "tr", "li",
                         "h1", "h2", "h3", "h4", "h5", "h6"):
            self._text.append("\n")
        elif tag_lower == "td":
            self._text.append("\t")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return

        if tag_lower in ("ix:hidden", "ix:header", "ix:resources", "ix:exclude"):
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return

        if tag_lower in ("p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._text.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and self._hidden_depth == 0:
            self._text.append(data)

    def get_text(self) -> str:
        raw = "".join(self._text)
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def sec_request(url: str, max_retries: int = 3) -> bytes:
    """
    Make a rate-limited request to SEC EDGAR with proper User-Agent.

    日本側の教訓: 429で無限再帰するのは危険 → ループ + max_retries
    """
    for attempt in range(max_retries + 1):
        req = Request(url, headers={"User-Agent": SEC_USER_AGENT})
        try:
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            time.sleep(SEC_RATE_LIMIT)
            return data
        except HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"  Rate limited (429). Waiting {wait}s... (attempt {attempt+1})")
                time.sleep(wait)
                continue
            raise
        except URLError as e:
            if attempt < max_retries:
                print(f"  Network error: {e}. Retry {attempt+1}...")
                time.sleep(5)
                continue
            raise

    raise RuntimeError(f"Failed after {max_retries+1} attempts: {url}")


def get_submissions(cik: int) -> dict:
    """Get filing submissions for a CIK (with pagination support)."""
    cik_padded = f"{cik:010d}"
    url = f"{SEC_BASE_URL}/submissions/CIK{cik_padded}.json"
    data = sec_request(url)
    submissions = json.loads(data)

    # Handle pagination: older filings are in separate files
    # 日本側の教訓: データが分割されてることに気づかず古い年度が取れなかった
    recent = submissions.get("filings", {}).get("recent", {})
    extra_files = submissions.get("filings", {}).get("files", [])

    for extra in extra_files:
        filename = extra.get("name", "")
        if not filename:
            continue
        extra_url = f"{SEC_BASE_URL}/submissions/{filename}"
        try:
            extra_data = sec_request(extra_url)
            extra_json = json.loads(extra_data)
            # Merge arrays
            for key in recent:
                if key in extra_json and isinstance(recent[key], list):
                    recent[key].extend(extra_json[key])
        except Exception as e:
            print(f"  Warning: Could not load pagination file {filename}: {e}")

    return submissions


def find_10k_filings(submissions: dict, target_years: list[int] | None = None) -> list[dict]:
    """
    Extract 10-K filing info from submissions data.

    日本側の教訓:
    - FY判定を filing_year - 1 でやると非暦年決算で壊れる
    - Apple(9月決算), Microsoft(6月決算) 等は同年に提出
    - → reportDate の年を使うか、periodOfReport を見る
    - submissions API の reportDate フィールドが fiscal period end date
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    report_dates = recent.get("reportDate", [])  # fiscal period end date

    # Collect all 10-K / 10-K/A per FY, then prefer 10-K over 10-K/A
    by_fy: dict[int, list[dict]] = {}

    for i, form in enumerate(forms):
        if form not in ("10-K", "10-K/A"):
            continue

        filing_date = dates[i] if i < len(dates) else ""

        # FY判定: reportDate (= fiscal period end) を使う
        # これが最も正確 (日本側で学んだ教訓)
        report_date = report_dates[i] if i < len(report_dates) else ""
        if report_date:
            fiscal_year = int(report_date[:4])
        elif filing_date:
            # fallback: filing dateから推定 (不正確だが無いよりマシ)
            fiscal_year = int(filing_date[:4])
        else:
            continue

        if target_years and fiscal_year not in target_years:
            continue

        entry = {
            "accessionNumber": accessions[i] if i < len(accessions) else "",
            "filingDate": filing_date,
            "reportDate": report_date,
            "primaryDocument": primary_docs[i] if i < len(primary_docs) else "",
            "form": form,
            "fiscalYear": fiscal_year,
        }
        by_fy.setdefault(fiscal_year, []).append(entry)

    # Per FY: prefer 10-K over 10-K/A (amendment often lacks Items 1-9A)
    filings = []
    for fy, entries in sorted(by_fy.items()):
        original = [e for e in entries if e["form"] == "10-K"]
        filings.append(original[0] if original else entries[0])

    return filings


def download_10k_document(cik: int, filing: dict) -> str | None:
    """Download the primary 10-K document and convert to text."""
    acc = filing["accessionNumber"].replace("-", "")
    primary_doc = filing["primaryDocument"]

    if not primary_doc:
        return None

    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{primary_doc}"

    try:
        raw = sec_request(url)
    except Exception as e:
        print(f"  Download error: {e}")
        return None

    # Detect encoding from HTML meta tag or Content-Type
    # 日本側の教訓: UTF-8決め打ちだと文字化けする場合がある
    text_bytes = raw
    encoding = "utf-8"

    # Check for meta charset
    charset_match = re.search(rb'charset[="\s]+([A-Za-z0-9\-]+)', text_bytes[:2000])
    if charset_match:
        detected = charset_match.group(1).decode("ascii", errors="ignore").lower()
        if detected in ("windows-1252", "iso-8859-1", "latin1", "ascii"):
            encoding = detected

    if primary_doc.lower().endswith((".htm", ".html")):
        try:
            html = text_bytes.decode(encoding, errors="replace")
            extractor = HTMLTextExtractor()
            extractor.feed(html)
            return extractor.get_text()
        except Exception as e:
            print(f"  HTML parse error: {e}")
            return text_bytes.decode(encoding, errors="replace")
    else:
        return text_bytes.decode(encoding, errors="replace")


def load_ticker_map() -> dict:
    """Load company_tickers.json -> {ticker: {cik, title}} mapping."""
    with open(COMPANY_TICKERS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for entry in raw.values():
        ticker = entry.get("ticker", "")
        cik = entry.get("cik_str")
        title = entry.get("title", "")
        if ticker and cik:
            result[ticker.upper()] = {"cik": int(cik), "title": title}
    return result


def sanitize_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80]


def download_company(ticker: str, cik: int, company_name: str,
                     target_years: list[int] | None = None):
    """Download all 10-K filings for one company."""
    safe_name = sanitize_name(company_name)
    company_dir = FILING_DIR / f"{ticker}_{safe_name}"
    company_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Fetching submissions for {ticker} (CIK {cik})...")
    submissions = get_submissions(cik)

    filings = find_10k_filings(submissions, target_years)
    if not filings:
        print(f"  No 10-K filings found")
        return 0

    print(f"  Found {len(filings)} 10-K filings")
    downloaded = 0

    for filing in filings:
        fy = filing["fiscalYear"]
        outpath = company_dir / f"{fy}_10K.txt"

        if outpath.exists():
            print(f"    FY{fy}: already exists, skipping")
            downloaded += 1
            continue

        print(f"    FY{fy}: downloading (filed {filing['filingDate']}, period end {filing.get('reportDate','?')})...")
        text = download_10k_document(cik, filing)

        if text:
            with open(outpath, "w", encoding="utf-8") as f:
                f.write(text)

            meta = {
                "ticker": ticker,
                "cik": cik,
                "companyName": company_name,
                "fiscalYear": fy,
                "filingDate": filing["filingDate"],
                "reportDate": filing.get("reportDate", ""),
                "form": filing["form"],
                "accessionNumber": filing["accessionNumber"],
                "primaryDocument": filing["primaryDocument"],
                "textLength": len(text),
            }
            meta_path = company_dir / f"{fy}_10K_meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            downloaded += 1
            print(f"    FY{fy}: saved ({len(text):,} chars)")
        else:
            print(f"    FY{fy}: failed")

    return downloaded


def main():
    parser = argparse.ArgumentParser(description="Download 10-K filings from SEC EDGAR")
    parser.add_argument("--ticker", type=str, help="Single ticker to download")
    parser.add_argument("--list", type=str, dest="ticker_list",
                        help="File with tickers (one per line)")
    parser.add_argument("--year", type=int, help="Single year to target")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip companies that already have a directory")
    args = parser.parse_args()

    FILING_DIR.mkdir(parents=True, exist_ok=True)

    ticker_map = load_ticker_map()
    print(f"Loaded {len(ticker_map)} tickers")

    target_years = [args.year] if args.year else TARGET_YEARS

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.ticker_list:
        with open(args.ticker_list, "r") as f:
            tickers = [line.strip().upper() for line in f if line.strip()]
    else:
        tickers = list(ticker_map.keys())

    if args.limit > 0:
        tickers = tickers[:args.limit]

    total = len(tickers)
    print(f"Downloading 10-K for {total} companies, years: {target_years}")
    print(f"Output directory: {FILING_DIR}")

    total_downloaded = 0
    skipped = 0
    errors = 0

    for i, ticker in enumerate(tickers):
        # SEC uses hyphens not dots (BRK.B -> BRK-B, BF.B -> BF-B)
        lookup = ticker.replace(".", "-") if "." in ticker else ticker
        info = ticker_map.get(lookup)
        if not info:
            if errors < 5:
                print(f"[{i+1}/{total}] {ticker}: not found in ticker map")
            errors += 1
            continue

        if args.skip_existing:
            safe_name = sanitize_name(info["title"])
            company_dir = FILING_DIR / f"{lookup}_{safe_name}"
            if company_dir.exists() and any(company_dir.glob("*_10K.txt")):
                skipped += 1
                continue

        print(f"[{i+1}/{total}] {ticker} ({info['title']})")
        try:
            n = download_company(lookup, info["cik"], info["title"], target_years)
            total_downloaded += n
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print(f"\nDone: {total_downloaded} filings downloaded, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
