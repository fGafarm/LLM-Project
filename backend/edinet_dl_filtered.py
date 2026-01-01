#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EDINET書類ダウンローダー（改善版）

改善点:
- 日付範囲指定（--from / --to）
- 書類タイプフィルタ（--doc-types）
- リトライ機能（指数バックオフ）
- 並列ダウンロード（--parallel）
- 進捗バー表示（tqdm）
- ログファイル出力
- 詳細サマリーレポート
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

import gspread
from google.oauth2.service_account import Credentials

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# =============================================================================
# 定数
# =============================================================================

BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"

# 主要な書類タイプコード
DOC_TYPE_CODES = {
    "120": "有価証券報告書",
    "130": "訂正有価証券報告書",
    "140": "四半期報告書",
    "150": "訂正四半期報告書",
    "160": "半期報告書",
    "170": "訂正半期報告書",
}

DEFAULT_DOC_TYPES = ["120", "140"]  # デフォルトは有報と四半期


# =============================================================================
# データクラス
# =============================================================================

@dataclass(frozen=True)
class DocItem:
    """EDINET書類情報"""
    doc_id: str
    filer_name: str
    edinet_code: str
    submit_date_time: str
    doc_description: str
    doc_type_code: str
    xbrl_flag: str
    period_start: str = ""
    period_end: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DownloadResult:
    """ダウンロード結果"""
    success: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[Tuple[str, str]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# =============================================================================
# ユーティリティ
# =============================================================================

def setup_logging(log_dir: Path, year: int) -> logging.Logger:
    """ロガー設定"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"edinet_dl_{year}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger("edinet_dl")
    logger.setLevel(logging.DEBUG)

    # ファイルハンドラ
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    # コンソールハンドラ（ERRORのみ）
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def normalize_jp(s: str) -> str:
    """企業名マッチ用に正規化"""
    if not s:
        return ""
    s = s.strip()
    s = s.replace(" ", "").replace("　", "")
    s = s.replace("株式会社", "").replace("（株）", "").replace("(株)", "")
    s = s.replace("ホールディングス", "HD")
    s = re.sub(r"[･・,，．.（）()\[\]【】「」『』\-_—–／/]", "", s)
    return s.lower()


def sanitize_name(name: str) -> str:
    """ファイル名に使えない文字を置換"""
    bad = r'\/:*?"<>|'
    for ch in bad:
        name = name.replace(ch, "_")
    name = name.strip()
    return name[:120] if name else "UNKNOWN"


def default_output_dir() -> Path:
    """デフォルト保存先"""
    home = Path.home()
    desktop = home / "Desktop"
    return (desktop / "EDINET_DL") if desktop.exists() else (home / "EDINET_DL")


def parse_date(s: str) -> date:
    """日付文字列をパース（YYYY-MM-DD or YYYYMMDD）"""
    s = s.strip().replace("/", "-")
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    else:
        return datetime.strptime(s, "%Y%m%d").date()


def date_range(start: date, end: date) -> Iterable[date]:
    """日付範囲のイテレータ"""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def create_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """リトライ付きセッション作成"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# =============================================================================
# Google Sheets
# =============================================================================

def load_company_names_from_sheet(
    sa_json_path: Path,
    spreadsheet_name: str,
    tab_name: str,
) -> List[str]:
    """Google Sheetsから企業名リストを読み込む"""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file(str(sa_json_path), scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open(spreadsheet_name)
    ws = sh.worksheet(tab_name)

    records = ws.get_all_records()
    if not records:
        return []

    out: List[str] = []
    for r in records:
        name = (r.get("company_name") or "").strip()
        if name:
            out.append(name)

    # company_name列が取れない場合の救済
    if not out:
        values = ws.get_all_values()
        if not values:
            return []
        header = values[0]
        idx = -1
        for i, h in enumerate(header):
            if str(h).strip().lower() == "company_name":
                idx = i
                break
        if idx >= 0:
            for row in values[1:]:
                if idx < len(row) and row[idx].strip():
                    out.append(row[idx].strip())

    return out


# =============================================================================
# EDINET API
# =============================================================================

def fetch_daily_list(
    session: requests.Session,
    d: date,
    api_key: str,
    timeout: int = 60,
) -> List[DocItem]:
    """日付の書類一覧を取得"""
    url = f"{BASE_URL}/documents.json"
    params = {
        "date": d.isoformat(),
        "type": "2",
        "Subscription-Key": api_key,
    }

    r = session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    js = r.json()

    results = js.get("results") or []
    items: List[DocItem] = []

    for row in results:
        doc_id = (row.get("docID") or "").strip()
        if not doc_id:
            continue

        items.append(
            DocItem(
                doc_id=doc_id,
                filer_name=(row.get("filerName") or "").strip(),
                edinet_code=(row.get("edinetCode") or "").strip(),
                submit_date_time=(row.get("submitDateTime") or "").strip(),
                doc_description=(row.get("docDescription") or "").strip(),
                doc_type_code=str(row.get("docTypeCode") or "").strip(),
                xbrl_flag=str(row.get("xbrlFlag") or "").strip(),
                period_start=(row.get("periodStart") or "").strip(),
                period_end=(row.get("periodEnd") or "").strip(),
            )
        )

    return items


def download_doc_zip(
    session: requests.Session,
    doc_id: str,
    api_key: str,
    out_path: Path,
    doc_type: int = 1,
    timeout: int = 120,
) -> None:
    """書類ZIPをダウンロード"""
    url = f"{BASE_URL}/documents/{doc_id}"
    params = {"type": str(doc_type), "Subscription-Key": api_key}

    r = session.get(url, params=params, timeout=timeout, stream=True)
    r.raise_for_status()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


# =============================================================================
# フィルタリング
# =============================================================================

def filter_docs(
    docs: List[DocItem],
    company_norms: Set[str],
    doc_types: Set[str],
) -> List[DocItem]:
    """書類をフィルタリング"""
    out: List[DocItem] = []

    for x in docs:
        # XBRLありのみ
        if x.xbrl_flag != "1":
            continue

        # 書類タイプフィルタ
        if doc_types and x.doc_type_code not in doc_types:
            continue

        # 企業名マッチ
        fn = normalize_jp(x.filer_name)
        hit = any(cn and (cn in fn) for cn in company_norms)
        if hit:
            out.append(x)

    return out


# =============================================================================
# ダウンロード処理
# =============================================================================

def download_single(
    session: requests.Session,
    item: DocItem,
    api_key: str,
    out_dir: Path,
    year: int,
    logger: logging.Logger,
) -> Tuple[str, bool, str]:
    """単一ファイルダウンロード（並列用）"""
    company_dirname = sanitize_name(f"{item.edinet_code}_{item.filer_name}".strip("_"))
    subdir = out_dir / str(year) / company_dirname
    filename = sanitize_name(f"{item.doc_id}_{item.doc_type_code}.zip")
    out_path = subdir / filename

    # 既存スキップ
    if out_path.exists() and out_path.stat().st_size > 0:
        return item.doc_id, True, "skipped"

    try:
        download_doc_zip(session, item.doc_id, api_key, out_path)
        logger.info(f"Downloaded: {item.doc_id} -> {out_path}")
        return item.doc_id, True, "success"
    except Exception as e:
        logger.error(f"Failed: {item.doc_id} - {e}")
        return item.doc_id, False, str(e)


def process_downloads(
    targets: List[DocItem],
    session: requests.Session,
    api_key: str,
    out_dir: Path,
    year: int,
    logger: logging.Logger,
    parallel: int = 1,
    dry_run: bool = False,
) -> DownloadResult:
    """ダウンロード処理（直列/並列対応）"""
    result = DownloadResult()

    if dry_run:
        for item in targets:
            print(f"[DRY] {item.submit_date_time[:10]} {item.filer_name} {item.doc_id} {item.doc_description}")
        return result

    # 進捗バー
    if HAS_TQDM:
        pbar = tqdm(total=len(targets), desc="Downloading", unit="file")
    else:
        pbar = None

    def update_result(doc_id: str, success: bool, status: str):
        if status == "skipped":
            result.skipped += 1
        elif success:
            result.success += 1
        else:
            result.failed += 1
            result.errors.append((doc_id, status))

        if pbar:
            pbar.update(1)

    if parallel > 1:
        # 並列ダウンロード
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(
                    download_single, session, item, api_key, out_dir, year, logger
                ): item
                for item in targets
            }
            for future in as_completed(futures):
                doc_id, success, status = future.result()
                update_result(doc_id, success, status)
    else:
        # 直列ダウンロード
        for item in targets:
            doc_id, success, status = download_single(
                session, item, api_key, out_dir, year, logger
            )
            update_result(doc_id, success, status)
            time.sleep(0.1)  # API負荷軽減

    if pbar:
        pbar.close()

    return result


# =============================================================================
# パス解決
# =============================================================================

def resolve_path(base_dir: Path, maybe_rel: str) -> Path:
    """相対パス/絶対パスを解決"""
    p = Path(maybe_rel).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base_dir / p).resolve()


# =============================================================================
# メイン
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="EDINET書類ダウンローダー（改善版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 2024年の有報・四半期報告書を10件だけテスト
  python edinet_dl_filtered.py --year 2024 --mode test --limit 10

  # 2024年3月のみ全件ダウンロード
  python edinet_dl_filtered.py --year 2024 --from 2024-03-01 --to 2024-03-31 --mode all

  # 有報(120)のみ対象
  python edinet_dl_filtered.py --year 2024 --doc-types 120

  # 並列4スレッドでダウンロード
  python edinet_dl_filtered.py --year 2024 --mode all --parallel 4
        """,
    )

    # 基本オプション
    ap.add_argument("--env", type=str, default="backend/.env", help=".envのパス")
    ap.add_argument("--year", type=int, required=True, help="対象年（例: 2024）")

    # 日付範囲
    ap.add_argument("--from", dest="date_from", type=str, help="開始日（YYYY-MM-DD）")
    ap.add_argument("--to", dest="date_to", type=str, help="終了日（YYYY-MM-DD）")

    # フィルタ
    ap.add_argument(
        "--doc-types",
        type=str,
        default="",
        help=f"書類タイプ（カンマ区切り）例: 120,140\n{DOC_TYPE_CODES}",
    )

    # モード
    ap.add_argument("--mode", choices=["test", "all"], default="test", help="test/all")
    ap.add_argument("--limit", type=int, default=10, help="testモードのDL上限")
    ap.add_argument("--max-docs", type=int, default=0, help="全体DL上限（0=無制限）")

    # 出力
    ap.add_argument("--out", type=str, default="", help="保存先ディレクトリ")

    # パフォーマンス
    ap.add_argument("--parallel", type=int, default=1, help="並列DL数（1=直列）")
    ap.add_argument("--sleep", type=float, default=0.15, help="API呼び出し間隔（秒）")

    # その他
    ap.add_argument("--dry-run", action="store_true", help="DLせず対象だけ表示")
    ap.add_argument("--no-progress", action="store_true", help="進捗バー非表示")

    args = ap.parse_args()

    # .env読み込み
    env_path = Path(args.env).expanduser().resolve()
    if not env_path.exists():
        print(f"ERROR: .envが見つからない: {env_path}", file=sys.stderr)
        return 2

    load_dotenv(dotenv_path=env_path)
    base_dir = env_path.parent

    # 環境変数取得
    edinet_key = (os.getenv("EDINET_API_KEY") or "").strip()
    if not edinet_key:
        print("ERROR: EDINET_API_KEY が .env に無い", file=sys.stderr)
        return 2

    spreadsheet_name = (os.getenv("SPREADSHEET_NAME") or "").strip()
    tab_name = (os.getenv("COMPANY_TAB_NAME") or "company_name").strip()
    sa_json = (os.getenv("GOOGLE_SA_JSON") or "").strip()

    if not spreadsheet_name:
        print("ERROR: SPREADSHEET_NAME が .env に無い", file=sys.stderr)
        return 2
    if not sa_json:
        print("ERROR: GOOGLE_SA_JSON が .env に無い", file=sys.stderr)
        return 2

    sa_json_path = resolve_path(base_dir, sa_json)
    if not sa_json_path.exists():
        print(f"ERROR: サービスアカウントJSONが見つからない: {sa_json_path}", file=sys.stderr)
        return 2

    # 保存先決定
    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        env_out = (os.getenv("OUTPUT_DIR") or "").strip()
        if env_out:
            out_dir = resolve_path(base_dir, env_out)
        else:
            out_dir = default_output_dir().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # ロガー設定
    logger = setup_logging(out_dir / "logs", args.year)
    logger.info(f"Started: year={args.year} mode={args.mode}")

    # 日付範囲決定
    if args.date_from:
        start_date = parse_date(args.date_from)
    else:
        start_date = date(args.year, 1, 1)

    if args.date_to:
        end_date = parse_date(args.date_to)
    else:
        end_date = date(args.year, 12, 31)

    # 書類タイプフィルタ
    if args.doc_types:
        doc_types = set(x.strip() for x in args.doc_types.split(",") if x.strip())
    else:
        doc_types = set(DEFAULT_DOC_TYPES)

    # Sheetsから企業名取得
    print("[INFO] Loading company names from Google Sheets...")
    company_names = load_company_names_from_sheet(sa_json_path, spreadsheet_name, tab_name)
    if not company_names:
        print(f"ERROR: タブ '{tab_name}' から company_name が取れなかった", file=sys.stderr)
        return 2

    company_norms = {normalize_jp(x) for x in company_names if x.strip()}
    company_norms = {x for x in company_norms if x}

    # 情報表示
    print(f"\n{'='*60}")
    print(f"EDINET Downloader - Configuration")
    print(f"{'='*60}")
    print(f"Year:        {args.year}")
    print(f"Date Range:  {start_date} ~ {end_date}")
    print(f"Doc Types:   {', '.join(sorted(doc_types))} ({', '.join(DOC_TYPE_CODES.get(t, t) for t in sorted(doc_types))})")
    print(f"Mode:        {args.mode} (limit={args.limit if args.mode == 'test' else 'N/A'})")
    print(f"Parallel:    {args.parallel}")
    print(f"Companies:   {len(company_norms)}")
    print(f"Output:      {out_dir}")
    print(f"{'='*60}\n")

    # セッション作成
    session = create_session(retries=3, backoff=0.5)

    # 全日付の書類一覧を取得
    print("[INFO] Fetching document list from EDINET...")
    all_targets: List[DocItem] = []
    dates = list(date_range(start_date, end_date))

    if HAS_TQDM and not args.no_progress:
        date_iter = tqdm(dates, desc="Scanning dates", unit="day")
    else:
        date_iter = dates

    for d in date_iter:
        try:
            docs = fetch_daily_list(session, d, edinet_key)
            filtered = filter_docs(docs, company_norms, doc_types)
            all_targets.extend(filtered)
            time.sleep(args.sleep)
        except Exception as e:
            logger.error(f"Failed to fetch {d}: {e}")

    # 新しい順にソート
    all_targets.sort(key=lambda x: x.submit_date_time, reverse=True)

    # 重複除去（doc_idベース）
    seen = set()
    unique_targets = []
    for t in all_targets:
        if t.doc_id not in seen:
            seen.add(t.doc_id)
            unique_targets.append(t)
    all_targets = unique_targets

    print(f"\n[INFO] Found {len(all_targets)} documents matching criteria")

    # 上限適用
    if args.mode == "test":
        all_targets = all_targets[: args.limit]
    elif args.max_docs > 0:
        all_targets = all_targets[: args.max_docs]

    if not all_targets:
        print("[INFO] No documents to download")
        return 0

    print(f"[INFO] Downloading {len(all_targets)} documents...")

    # ダウンロード実行
    result = process_downloads(
        all_targets,
        session,
        edinet_key,
        out_dir,
        args.year,
        logger,
        parallel=args.parallel,
        dry_run=args.dry_run,
    )

    # サマリー表示
    print(f"\n{'='*60}")
    print(f"DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"Success:  {result.success}")
    print(f"Skipped:  {result.skipped} (already exists)")
    print(f"Failed:   {result.failed}")
    print(f"{'='*60}")
    print(f"Saved to: {out_dir}")

    # エラーログ保存
    if result.errors:
        err_path = out_dir / f"errors_{args.year}.json"
        with open(err_path, "w", encoding="utf-8") as f:
            json.dump(result.errors, f, ensure_ascii=False, indent=2)
        print(f"[WARN] Errors saved: {err_path}")

    # ダウンロード済みファイル一覧保存
    manifest_path = out_dir / f"manifest_{args.year}.json"
    manifest = [t.to_dict() for t in all_targets]
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Manifest saved: {manifest_path}")

    logger.info(f"Completed: success={result.success} skipped={result.skipped} failed={result.failed}")

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())