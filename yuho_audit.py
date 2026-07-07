"""
Yuho Audit — EDINET 提出一覧 vs pdf_xbrl ZIP vs xbrl_store の3層突合監査.

POSTMORTEM_2026-07-05 §4 P0 の実装 (tanshin_audit.py の有報版)。
「EDINETにN件提出された → ZIPがN件ある → storeにN件反映された」を毎日突合し、
取込パイプラインのサイレント死 (junction消失・DL停止・抽出停止・壊れ残骸ブロック) を
named で即日検知する。

3層:
  L0 MOUNT     : pdf_xbrl junction / xbrl_store / APIキー の存在 (欠落なら即 exit 2)
  L1 EDINET→ZIP: EDINET documents.json の提出一覧 (有報120/訂正130/半期160/訂正170,
                 上場企業のみ) に対し ZIP 原本が pdf_xbrl/{fy}/ 配下に実在するか
  L2 ZIP→STORE : ZIP がある企業の xbrl_store 反映を判定
                 有報:   {fy}.json が存在 / source_file が当該docID / revenue 非空
                 半期:   {fy}_Q2.json 等が存在 (または後続の有報 {fy}.json で上書き済み)

判定バケツ:
  OK / OK_SUPERSEDED (半期→有報上書き済) / NO_ZIP (DL漏れ=最重度) / MISSING (ZIPあり抽出なし)
  NO_DIR (企業フォルダなし) / NO_REVENUE (抽出済みだが revenue 空 = 壊れ残骸)
  STALE (別docID由来のまま = 訂正有報未反映 or 短信データが有報をブロック)

使い方:
  python yuho_audit.py                     # 昨日1日分
  python yuho_audit.py --days 7            # 直近7日 (ローカル日次: daily_update Step 8b)
  python yuho_audit.py --days 2 --strict   # CI用 (差分あれば exit 1)
  python yuho_audit.py --date 2026-06-25   # 指定日
  python yuho_audit.py --days 30 --offline # APIを叩かずキャッシュのみで再判定

終了コード: 0=PASS / 1=差分あり(--strict時のみ) / 2=環境エラー (mount欠落・APIキー無し)
常に logs/yuho_audit_last.json に機械可読サマリを書く (health_check.py が読む)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parent)
PDF_ROOT = Path(os.environ.get("PDF_ROOT") or (PROJECT_ROOT / "pdf_xbrl"))
XBRL_STORE = Path(os.environ.get("XBRL_STORE") or (PROJECT_ROOT / "financial_analysis_system" / "xbrl_store"))
STOCKFLOW_ROOT = Path(os.environ.get("STOCKFLOW_ROOT") or (PROJECT_ROOT / "StockFlow"))
INDUSTRY_JSON = STOCKFLOW_ROOT / "scripts" / "industry_mapping.json"
ENV_FILE = PROJECT_ROOT / "backend" / ".env"
LOG_DIR = Path(os.environ.get("LOG_DIR") or (PROJECT_ROOT / "logs"))
CACHE_DIR = LOG_DIR / "edinet_cache"
SUMMARY_JSON = LOG_DIR / "yuho_audit_last.json"

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
DOC_TYPES_TARGET = {"120", "130", "160", "170"}  # 有報, 訂正有報, 半期報告書, 訂正半期
ANNUAL_TYPES = {"120", "130"}
INTERIM_TYPES = {"160", "170"}

# 構造的に抽出不能な提出者 (tanshin_audit の KNOWN_UNEXTRACTABLE と同思想、毎日警告するとノイズ)。
# 外国籍発行体でZIP内にXBRLインスタンスが無い/構造が異なる: 2026-07-06 実測
KNOWN_UNEXTRACTABLE: set[str] = {
    "4875",  # メディシノバ・インク (US法人)
    "9257",  # YCPホールディングス (ケイマン法人、XBRLファイルなしを実測)
}

# 売上ゼロが正当な提出者 (臨床段階の創薬等、有報XBRLに収益系タグ自体が存在しないことを
# 2026-07-07 に raw_tags 全数で実測確認)。revenue空でも NO_REVENUE にしない
ZERO_REVENUE_OK: set[str] = {
    "4598",  # Delta-Fly Pharma (179タグ中、収益系タグ皆無)
    "520A",  # ジェイファーマ (補助金収入は営業外のみ)
    "543A",  # ARCHION
}


def load_api_key() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("EDINET_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("EDINET_API_KEY", "")


# ===========================================================================
# L1: EDINET 提出一覧 (キャッシュ付き)
# ===========================================================================
def fetch_document_list(date_str: str, api_key: str, offline: bool) -> list[dict] | None:
    """指定日の EDINET documents.json results を返す (過去日はキャッシュ再利用).

    None = 取得不能 (API障害/オフラインでキャッシュ無し) → その日は UNVERIFIED 扱い。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{date_str}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # 壊れたキャッシュは再取得
    if offline:
        return None
    if not api_key:
        return None
    try:
        resp = requests.get(
            f"{EDINET_API_BASE}/documents.json",
            params={"date": date_str, "type": 2, "Subscription-Key": api_key},
            timeout=30,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:  # 200だがJSONでない (プロキシ/メンテページ等)
        return None
    # metadata.status を検証: 一時障害の空応答を「提出ゼロ」として恒久キャッシュしない
    if str(((payload or {}).get("metadata") or {}).get("status") or "") != "200":
        return None
    results = payload.get("results", [])
    # 当日分は提出が続いている可能性があるためキャッシュしない (翌日以降に確定キャッシュ)。
    # キャッシュは対象docTypeの最小フィールドのみ (肥大防止)
    if date_str < datetime.now().strftime("%Y-%m-%d"):
        slim = [{k: r.get(k) for k in ("docID", "secCode", "docTypeCode", "periodEnd", "filerName")}
                for r in results if r.get("docTypeCode") in DOC_TYPES_TARGET]
        cache_file.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    return results


def resolve_ticker(sec_code: str, industry_map: dict) -> str | None:
    """daily_update.fetch_documents と同一の上場判定 (数字4桁 + 英数字5桁ケア)."""
    if not sec_code or len(sec_code) < 4:
        return None
    ticker = sec_code[:4]
    if ticker in industry_map:
        return ticker
    ticker = sec_code[:5].rstrip("0") if len(sec_code) >= 5 else ticker
    return ticker if ticker in industry_map else None


def expected_docs_for(date_str: str, results: list[dict], industry_map: dict) -> list[dict]:
    docs = []
    for r in results:
        doc_type = r.get("docTypeCode", "")
        if doc_type not in DOC_TYPES_TARGET:
            continue
        ticker = resolve_ticker(r.get("secCode") or "", industry_map)
        if not ticker or ticker in KNOWN_UNEXTRACTABLE:
            continue
        period_end = r.get("periodEnd", "")
        if not period_end:
            continue
        docs.append({
            "doc_id": r["docID"],
            "ticker": ticker,
            "name": r.get("filerName", ""),
            "doc_type": doc_type,
            "period_end": period_end,
            "fiscal_year": int(period_end[:4]),
            "filed_date": date_str,
        })
    return docs


def find_zip(doc: dict) -> Path | None:
    """pdf_xbrl/{fy}/ 配下 (サブフォルダ名は問わない) から docID を含む ZIP を探す."""
    year_dir = PDF_ROOT / str(doc["fiscal_year"])
    if not year_dir.is_dir():
        return None
    docid = doc["doc_id"]
    for z in year_dir.glob(f"*/*{docid}*.zip"):
        return z
    for z in year_dir.glob(f"*{docid}*.zip"):  # 旧レイアウト (年直下) 互換
        return z
    return None


# ===========================================================================
# L2: xbrl_store 反映判定
# ===========================================================================
def build_store_index() -> dict[str, list[Path]]:
    """code -> [company_dir,...] (社名表記ゆれの二重フォルダも全部拾う)."""
    idx: dict[str, list[Path]] = {}
    if not XBRL_STORE.is_dir():
        return idx
    for d in XBRL_STORE.iterdir():
        if d.is_dir() and "_" in d.name:
            idx.setdefault(d.name.split("_")[0], []).append(d)
    return idx


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def judge_store(doc: dict, store_idx: dict[str, list[Path]]) -> tuple[str, str]:
    """(status, detail) を返す。status ∈ OK / OK_SUPERSEDED / MISSING / NO_DIR / NO_REVENUE / STALE / PARSE_ERROR."""
    dirs = store_idx.get(doc["ticker"])
    if not dirs:
        return "NO_DIR", f"{doc['ticker']} {doc['name'][:14]} storeに企業フォルダなし"

    if doc["doc_type"] in ANNUAL_TYPES:
        fy = doc["fiscal_year"]  # 有報: period_end の年 = 会計年度終了年
        # ランク順序: どこかのフォルダに期待docIDが入っていれば「取込は成功」(OK/NO_REVENUE) を優先。
        # 表記ゆれ二重フォルダの片方に残る tanshin 残骸が、正しい方の判定を覆い隠さないようにする
        # (実例: 8795 T&D — 新フォルダにEDINET抽出済みなのに旧フォルダのtanshinでSTALE誤判定)
        best: tuple[int, str, str] | None = None  # (rank, status, detail)
        for d in dirs:
            yf = d / f"{fy}.json"
            if not yf.exists():
                continue
            j = load_json(yf)
            if j is None:
                cand = (4, "PARSE_ERROR", f"{doc['ticker']} {fy}.json parse不能 ({d.name})")
            else:
                src = str(j.get("source_file") or "")
                rev = (j.get("data") or {}).get("revenue")
                if doc["doc_id"] in src:
                    if rev is not None or doc["ticker"] in ZERO_REVENUE_OK:
                        cand = (0, "OK", "")
                    else:
                        cand = (1, "NO_REVENUE", f"{doc['ticker']} {fy} {doc['doc_id']} revenue空 ({d.name})")
                elif re.search(r"半期", src):
                    # 年度ファイルに半期由来のソース = S0 T4事故クラスの汚染
                    cand = (2, "INTERIM_AS_ANNUAL", f"{doc['ticker']} {fy}.json が半期データ ({src[-30:]}) = 年度汚染")
                else:
                    origin = "tanshin残存" if j.get("source") == "tanshin" else f"別docID({src[-22:]})"
                    cand = (3, "STALE", f"{doc['ticker']} {fy} 期待={doc['doc_id']} 実際={origin} ({d.name})")
            best = min(best, cand) if best else cand
        if best is None:
            return "MISSING", f"{doc['ticker']} {doc['name'][:14]} {fy}.json なし (ZIPは有)"
        return best[1], best[2]

    # 半期: EDINETのperiodEndは会計年度末を指す (実測: 5942の半期 periodEnd=2026-11-30) ため
    # store側の中間ファイルは {fiscal_year}_Q2.json。
    # 汚染検知が最優先: 半期docIDが {fy}.json に入っていたら年度汚染 (S0 T4事故クラス) = hard fail
    fy = doc["fiscal_year"]
    docid = doc["doc_id"]
    best: tuple[int, str, str] | None = None
    for d in dirs:
        yf = d / f"{fy}.json"
        if yf.exists():
            jy = load_json(yf)
            ysrc = str((jy or {}).get("source_file") or "")
            if docid in ysrc or re.search(r"半期", ysrc):
                return "INTERIM_AS_ANNUAL", (
                    f"{doc['ticker']} {doc['name'][:14]} {fy}.json が半期データ ({ysrc[-30:]}) = 年度汚染")
        q2 = d / f"{fy}_Q2.json"
        if q2.exists():
            j = load_json(q2)
            if j is None:
                cand = (3, "PARSE_ERROR", f"{doc['ticker']} {fy}_Q2 parse不能 ({d.name})")
            else:
                src = str(j.get("source_file") or "")
                if docid in src:
                    cand = (0, "OK", "")
                else:
                    origin = "tanshin" if j.get("source") == "tanshin" else f"別docID({src[-22:]})"
                    cand = (1, "STALE", f"{doc['ticker']} {fy}_Q2 期待={docid} 実際={origin} ({d.name})")
            best = min(best, cand) if best else cand
        elif yf.exists() and (best is None or best[0] > 2):
            # 後続の有報 (別docID・半期ソースでない) で上書き済み
            best = min(best, (2, "OK_SUPERSEDED", "")) if best else (2, "OK_SUPERSEDED", "")
    if best is None:
        return "MISSING", f"{doc['ticker']} {doc['name'][:14]} 半期 {fy}_Q2.json なし (ZIPは有)"
    return best[1], best[2]


# ===========================================================================
# Main
# ===========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Yuho ingestion audit (EDINET vs pdf_xbrl vs xbrl_store)")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days", type=int, default=1, help="Audit N days back (default: 1)")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if NO_ZIP/MISSING/NO_DIR/NO_REVENUE/PARSE_ERROR > 0")
    parser.add_argument("--strict-stale", action="store_true", help="STALE も strict 失敗に含める")
    parser.add_argument("--offline", action="store_true", help="EDINET APIを叩かずキャッシュのみ使用")
    parser.add_argument("--max-list", type=int, default=30, help="各バケツの最大表示件数")
    parser.add_argument("--json", dest="json_out", default=None, help="結果を JSON 出力するパス")
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, args.days + 1)]
        dates.reverse()

    # ---- L0: 環境 ----
    # 注: 「年フォルダが無い」は CI (このrunでDLした分しか無い) や提出ゼロの連休では正常なので
    # 環境エラーにしない。ZIP欠落は L1 の NO_ZIP (expected>0 の時のみ) で自然に検出される
    problems = []
    if not PDF_ROOT.exists():
        problems.append(f"pdf_xbrl が存在しない: {PDF_ROOT} (junction切れ = POSTMORTEM根本原因の再発)")
    elif not any(PDF_ROOT.glob("20*")):
        print(f"[NOTE] pdf_xbrl 配下に年フォルダなし (CI初回/提出ゼロなら正常): {PDF_ROOT}")
    if not XBRL_STORE.is_dir():
        problems.append(f"xbrl_store が存在しない: {XBRL_STORE}")
    if not INDUSTRY_JSON.exists():
        problems.append(f"industry_mapping.json が存在しない: {INDUSTRY_JSON}")
    api_key = load_api_key()
    if not api_key and not args.offline:
        problems.append("EDINET_API_KEY が backend/.env にも環境変数にもない")
    if problems:
        print("=== Yuho Audit: L0 環境エラー ===")
        for p in problems:
            print(f"  [CRITICAL] {p}")
        _write_summary({"status": "ENV_ERROR", "problems": problems, "dates": dates})
        return 2

    with INDUSTRY_JSON.open(encoding="utf-8") as f:
        industry_map = json.load(f)

    print("=== Yuho Audit (EDINET → ZIP → store) ===")
    print(f"pdf_xbrl : {PDF_ROOT}")
    print(f"store    : {XBRL_STORE}")
    print(f"Dates    : {dates[0]} .. {dates[-1]} ({len(dates)}日)")
    print()

    # 90日より古いキャッシュを剪定 (肥大防止)
    try:
        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        for cf in CACHE_DIR.glob("20*.json"):
            if cf.stem < cutoff:
                cf.unlink()
    except OSError:
        pass

    store_idx = build_store_index()
    buckets: dict[str, list[str]] = {k: [] for k in
        ("NO_ZIP", "MISSING", "NO_DIR", "NO_REVENUE", "PARSE_ERROR", "STALE", "INTERIM_AS_ANNUAL", "OK", "OK_SUPERSEDED")}
    unverified_dates: list[str] = []
    per_date = []

    for date_str in dates:
        results = fetch_document_list(date_str, api_key, args.offline)
        if results is None:
            print(f"  [??]   {date_str}: EDINET一覧を取得できず (UNVERIFIED)")
            unverified_dates.append(date_str)
            per_date.append({"date": date_str, "expected": None})
            continue
        docs = expected_docs_for(date_str, results, industry_map)
        n_zip_ok = 0
        date_miss = []
        for doc in docs:
            z = find_zip(doc)
            if z is None:
                label = "有報" if doc["doc_type"] in ANNUAL_TYPES else "半期"
                buckets["NO_ZIP"].append(
                    f"{date_str} {doc['ticker']} {doc['name'][:14]} {label} {doc['doc_id']} ZIPなし")
                date_miss.append(doc["ticker"])
                continue
            n_zip_ok += 1
            status, detail = judge_store(doc, store_idx)
            buckets[status].append(detail or f"{doc['ticker']}")
            if status not in ("OK", "OK_SUPERSEDED"):
                date_miss.append(doc["ticker"])
        flag = "OK " if not date_miss else "MISS"
        print(f"  [{flag}] {date_str}: 提出={len(docs):3d} ZIP={n_zip_ok:3d} 問題={len(date_miss)}"
              + (f"  ({', '.join(sorted(set(date_miss))[:12])}{'...' if len(set(date_miss)) > 12 else ''})" if date_miss else ""))
        per_date.append({"date": date_str, "expected": len(docs), "zip_ok": n_zip_ok, "problems": sorted(set(date_miss))})
        if not args.offline:
            time.sleep(0.3)

    print()
    print("=== Summary (企業×文書ごと) ===")
    for k in ("OK", "OK_SUPERSEDED", "STALE", "NO_REVENUE", "PARSE_ERROR", "INTERIM_AS_ANNUAL", "MISSING", "NO_DIR", "NO_ZIP"):
        print(f"  {k:17s}: {len(buckets[k])}")
    for k in ("INTERIM_AS_ANNUAL", "NO_ZIP", "MISSING", "NO_DIR", "NO_REVENUE", "PARSE_ERROR", "STALE"):
        if buckets[k]:
            print(f"\n-- {k} (先頭{args.max_list}) --")
            for line in buckets[k][: args.max_list]:
                print("  " + line)
            if len(buckets[k]) > args.max_list:
                print(f"  ... 他 {len(buckets[k]) - args.max_list} 件")
    if unverified_dates:
        print(f"\n[WARN] EDINET一覧を取得できなかった日: {unverified_dates}")

    hard_fail = sum(len(buckets[k]) for k in ("NO_ZIP", "MISSING", "NO_DIR", "NO_REVENUE", "PARSE_ERROR", "INTERIM_AS_ANNUAL"))
    stale = len(buckets["STALE"])
    verdict = "FAIL" if hard_fail else ("PASS_PARTIAL" if unverified_dates else "PASS")
    print(f"\n{verdict}: 取込漏れ/欠損 {hard_fail}件 (うち年度汚染 {len(buckets['INTERIM_AS_ANNUAL'])}件), STALE {stale}件, 未検証日 {len(unverified_dates)}日")

    summary = {
        "status": verdict,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "dates": dates,
        "hard_fail": hard_fail,
        "stale": stale,
        "unverified_dates": unverified_dates,
        "counts": {k: len(v) for k, v in buckets.items()},
        "per_date": per_date,
        "details": {k: v[:200] for k, v in buckets.items() if k not in ("OK", "OK_SUPERSEDED") and v},
    }
    _write_summary(summary)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {args.json_out}")

    if args.strict and (hard_fail > 0 or (args.strict_stale and stale > 0)):
        return 1
    # 未検証日が1日でもあれば「その日は監査できていない」= strict では失敗させる
    # (特に昨日分の取得失敗をPASS扱いすると監査の意味がない)
    if args.strict and unverified_dates:
        print(f"AUDIT FAILED: {len(unverified_dates)}日分の EDINET 一覧を取得できず、監査が成立していない")
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
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
