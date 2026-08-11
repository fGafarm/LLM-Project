"""
xbrl_store の年次/四半期 .json で revenue=None のものを raw_tags から再抽出。
追加した IFRS/業界別タグ (OperatingRevenue1, InsuranceRevenue, etc.) を反映。

実行: python financial_analysis_system/migrate_revenue_tags.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
XBRL_STORE = ROOT / "financial_analysis_system" / "xbrl_store"

# extractorのFALLBACK_TAGSから revenue 欄をimport
sys.path.insert(0, str(ROOT / "financial_analysis_system"))
from xbrl_batch_extractor import FALLBACK_TAGS  # noqa: E402

REVENUE_TAGS = [t for t, _ in FALLBACK_TAGS["revenue"]]
REVENUE_LOCAL = [t.split(":")[-1] for t in REVENUE_TAGS]


def extract_revenue_from_raw_tags(raw_tags: dict) -> float | None:
    """raw_tags.tags 辞書から revenue を抽出 (priority順)"""
    for local_name in REVENUE_LOCAL:
        td = raw_tags.get(local_name)
        if not td:
            continue
        if isinstance(td, dict):
            v = td.get("value")
        else:
            v = td
        try:
            v = float(v)
            if v > 0:
                return v
        except (ValueError, TypeError):
            continue
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fixed = 0
    no_match = 0
    no_change = 0
    skipped = 0

    json_files = list(XBRL_STORE.glob("*/[0-9][0-9][0-9][0-9]*.json"))
    # raw_tagsとstatementsはスキップ
    json_files = [f for f in json_files if "_raw_tags" not in f.name and "_statements" not in f.name]
    print(f"Total candidate files: {len(json_files):,}")

    for jf in json_files:
        try:
            with jf.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            skipped += 1
            continue

        d = data.get("data") or {}
        if d.get("revenue") is not None:
            no_change += 1
            continue

        # 同じ年/四半期の raw_tags.json を探す
        rt_path = jf.with_name(jf.stem + "_raw_tags.json")
        if not rt_path.exists():
            skipped += 1
            continue
        try:
            with rt_path.open(encoding="utf-8") as f:
                rt_data = json.load(f)
        except Exception:
            skipped += 1
            continue

        tags = rt_data.get("tags") or {}
        new_revenue = extract_revenue_from_raw_tags(tags)
        if new_revenue is None:
            no_match += 1
            continue

        d["revenue"] = new_revenue
        data["data"] = d
        with jf.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        fixed += 1
        if fixed % 200 == 0:
            print(f"  fixed {fixed}: {jf.parent.name} {jf.stem}")

    print(f"\nDone. fixed={fixed}, no_match={no_match}, no_change={no_change}, skipped={skipped}")


if __name__ == "__main__":
    main()
