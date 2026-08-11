"""
全上場企業の有価証券報告書から「主要な設備の状況」を一括抽出。

入力: E:/PDF/PDF+XBRL/{year}/有報/{code}_{name}_{date}_有報_{docID}.zip
出力: fixed_assets_store/{code}_{name}/{year}_facilities.json
ログ: fixed_assets_store/_extract_log_{year}.json (業種別カバー率)
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from extract_setsubi import extract_facilities  # type: ignore

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
INDUSTRY_JSON = PROJECT_ROOT / "StockFlow" / "scripts" / "industry_mapping.json"
PDF_ROOT = Path(r"E:/PDF/PDF+XBRL")
OUT_ROOT = PROJECT_ROOT / "fixed_assets_store"

ZIP_NAME_RE = re.compile(r"^([A-Z0-9]{4,5})_(.+?)_(?:\d{8}|unknown)_(?:有報|訂正有報)_([A-Z0-9]+)\.zip$")


def load_industry_map() -> dict[str, dict]:
    with INDUSTRY_JSON.open(encoding="utf-8") as f:
        return json.load(f)


def parse_zip_filename(name: str) -> tuple[str, str] | None:
    m = ZIP_NAME_RE.match(name)
    if not m:
        return None
    code = m.group(1)
    company_name = m.group(2)
    return code, company_name


def safe_dirname(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s)


def run_year(year: int, industry_map: dict[str, dict], limit: int | None = None) -> dict:
    year_dir = PDF_ROOT / str(year) / "有報"
    if not year_dir.exists():
        print(f"[SKIP] {year_dir} not found")
        return {}

    zips = sorted(year_dir.glob("*.zip"))
    print(f"[{year}] Found {len(zips)} ZIPs")
    if limit:
        zips = zips[:limit]
        print(f"[{year}] Limited to first {limit}")

    out_year_root = OUT_ROOT
    out_year_root.mkdir(parents=True, exist_ok=True)

    stats = {
        "year": year,
        "total": len(zips),
        "extracted": 0,
        "no_records": 0,
        "errors": 0,
        "by_industry": defaultdict(lambda: {"total": 0, "extracted": 0, "records": 0, "with_area": 0}),
        "elapsed_sec": 0.0,
    }
    failures: list[dict] = []

    t0 = time.time()
    for i, zp in enumerate(zips):
        parsed = parse_zip_filename(zp.name)
        if not parsed:
            stats["errors"] += 1
            continue
        code, company_name = parsed
        info = industry_map.get(code, {})
        industry = info.get("sector33", "")
        stats["by_industry"][industry]["total"] += 1

        try:
            records = extract_facilities(zp, industry)
        except Exception as e:
            stats["errors"] += 1
            failures.append({"code": code, "name": company_name, "error": f"{type(e).__name__}: {e}"})
            if (i + 1) % 200 == 0:
                print(f"  [{i+1}/{len(zips)}] {code} ERROR: {e}")
            continue

        if not records:
            stats["no_records"] += 1
            if (i + 1) % 500 == 0:
                print(f"  [{i+1}/{len(zips)}] {code} {industry} - no records")
            continue

        # 保存
        out_dir = out_year_root / safe_dirname(f"{code}_{company_name}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{year}_facilities.json"

        jp_records = [r for r in records if r.country == "JP"]
        with_area = sum(1 for r in jp_records if r.land_area_sqm)
        with_value = sum(1 for r in jp_records if r.land_book_value_mil)
        total_book = sum(r.land_book_value_mil or 0 for r in jp_records)
        total_area = sum(r.land_area_sqm or 0 for r in jp_records)

        payload = {
            "company_code": code,
            "company_name": company_name,
            "fiscal_year": year,
            "industry_sector33": industry,
            "summary": {
                "total_facilities": len(records),
                "jp_facilities": len(jp_records),
                "facilities_with_area": with_area,
                "facilities_with_book_value": with_value,
                "total_land_book_value_mil_yen": round(total_book, 2),
                "total_land_area_sqm": round(total_area, 0),
            },
            "facilities": [asdict(r) for r in records],
        }
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

        stats["extracted"] += 1
        ind_stats = stats["by_industry"][industry]
        ind_stats["extracted"] += 1
        ind_stats["records"] += len(jp_records)
        ind_stats["with_area"] += with_area

        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(zips) - i - 1) / rate
            print(f"  [{i+1}/{len(zips)}] extracted={stats['extracted']} no_rec={stats['no_records']} err={stats['errors']} | {rate:.1f}/s ETA {eta/60:.1f}min")

    stats["elapsed_sec"] = round(time.time() - t0, 1)
    stats["by_industry"] = dict(stats["by_industry"])

    log_file = OUT_ROOT / f"_extract_log_{year}.json"
    with log_file.open("w", encoding="utf-8") as f:
        json.dump({"stats": stats, "failures_sample": failures[:50]}, f, ensure_ascii=False, indent=2)

    return stats


def print_summary(stats: dict):
    print(f"\n========== Year {stats['year']} Summary ==========")
    print(f"  Total ZIPs:    {stats['total']:,}")
    print(f"  Extracted:     {stats['extracted']:,} ({100*stats['extracted']/max(1,stats['total']):.1f}%)")
    print(f"  No records:    {stats['no_records']:,}")
    print(f"  Errors:        {stats['errors']:,}")
    print(f"  Elapsed:       {stats['elapsed_sec']:.0f}s")
    print(f"\n--- By industry (extraction rate) ---")
    rows = sorted(stats["by_industry"].items(), key=lambda kv: -kv[1]["total"])
    for ind, s in rows:
        if s["total"] == 0:
            continue
        rate = 100 * s["extracted"] / s["total"]
        avg_area_pct = (100 * s["with_area"] / max(1, s["records"]))
        print(f"  {ind[:14]:14} total={s['total']:>4} extract={s['extracted']:>4} ({rate:>5.1f}%) records={s['records']:>5} with_area={s['with_area']:>5} ({avg_area_pct:>4.1f}%)")


def main():
    industry_map = load_industry_map()
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    stats = run_year(year, industry_map, limit)
    print_summary(stats)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
