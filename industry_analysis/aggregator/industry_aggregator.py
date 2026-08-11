"""
Industry aggregator
-------------------
segment_store × mappings を結合してカテゴリ別集計を出す。
USD/JPY 換算で通貨統合 (env: USDJPY, default 150)。

出力:
  data/aggregates/semiconductor_2024.json         : カテゴリ×企業×集計
  data/aggregates/semiconductor_2024_report.md    : 読みやすいマークダウン
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent.parent
CATEGORY_YAML = BASE / "category_tree" / "semiconductor.yaml"
SEGMENT_STORE = BASE / "data" / "segment_store"
MAPPING_JSON = BASE / "data" / "mappings" / "semiconductor_2024.json"
OUT_JSON = BASE / "data" / "aggregates" / "semiconductor_2024.json"
OUT_MD = BASE / "data" / "aggregates" / "semiconductor_2024_report.md"

USDJPY = float(os.environ.get("USDJPY", "150"))


def flatten_tree(nodes: list[dict], parent_id: str | None = None) -> list[dict]:
    out = []
    for n in nodes:
        out.append({
            "id": n["id"],
            "parent_id": parent_id,
            "level": n.get("level", 1),
            "name_ja": n.get("name_ja"),
            "name_en": n.get("name_en"),
        })
        out.extend(flatten_tree(n.get("children", []) or [], parent_id=n["id"]))
    return out


def load_category_map() -> dict[str, dict]:
    with open(CATEGORY_YAML, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    flat = flatten_tree(raw)
    return {c["id"]: c for c in flat}


def get_ancestors(cat_id: str, cat_map: dict[str, dict]) -> list[str]:
    """cat_id からルートまでの ancestor list(自分含む)"""
    chain = []
    cur = cat_id
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = cat_map.get(cur, {}).get("parent_id")
    return chain


def _load_all_segments() -> dict[tuple[str, str], dict]:
    """(region, segment_id) → segment row"""
    result = {}
    for region in ["jp", "us"]:
        region_dir = SEGMENT_STORE / region
        if not region_dir.exists():
            continue
        for comp_dir in sorted(region_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            for jf in comp_dir.glob("2024_segments.json"):
                with open(jf, encoding="utf-8") as f:
                    data = json.load(f)
                company_code = (
                    data.get("company_code")
                    or data.get("ticker")
                    or comp_dir.name.split("_")[0]
                )
                company_name = data.get("company_name", comp_dir.name)
                for seg in data.get("segments", []):
                    key = (region.upper(), seg.get("segment_id", ""))
                    result[key] = {
                        "region": region.upper(),
                        "company_code": company_code,
                        "company_name": company_name,
                        "segment_id": seg.get("segment_id"),
                        "segment_name": (
                            seg.get("segment_label_ja")
                            or seg.get("segment_label_en")
                            or seg.get("segment_id")
                        ),
                        "values": seg.get("values", {}),
                        "single_segment": data.get("single_segment_fallback", False),
                    }
    return result


def to_jpy(region: str, v: float | int | None) -> float | None:
    if v is None:
        return None
    if region == "US":
        return float(v) * USDJPY  # USD → JPY
    return float(v)


def aggregate():
    cat_map = load_category_map()
    segments = _load_all_segments()
    with open(MAPPING_JSON, encoding="utf-8") as f:
        mappings = json.load(f)["mappings"]

    # カテゴリ ancestor も含めて売上/利益を配布
    cat_agg: dict[str, dict] = defaultdict(
        lambda: {
            "revenue_jpy": 0.0,
            "operating_profit_jpy": 0.0,
            "companies": {},  # code -> {name, revenue, op, segments:[]}
        }
    )
    company_cat_agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"revenue_jpy": 0.0, "operating_profit_jpy": 0.0, "segments": []}
    )

    skipped = 0
    used = 0
    for m in mappings:
        seg_key = (m["region"], m["raw_segment_id"])
        seg = segments.get(seg_key)
        if not seg:
            skipped += 1
            continue
        values = seg["values"]
        rev = values.get("revenue_external") or values.get("revenue_total")
        op = values.get("operating_profit")
        rev_jpy = to_jpy(m["region"], rev) or 0.0
        op_jpy = to_jpy(m["region"], op) or 0.0
        used += 1

        # category + all ancestors
        cat_chain = get_ancestors(m["primary_category_id"], cat_map)
        for cat_id in cat_chain:
            cat_agg[cat_id]["revenue_jpy"] += rev_jpy
            cat_agg[cat_id]["operating_profit_jpy"] += op_jpy
            comp_key = seg["company_code"]
            if comp_key not in cat_agg[cat_id]["companies"]:
                cat_agg[cat_id]["companies"][comp_key] = {
                    "code": seg["company_code"],
                    "name": seg["company_name"],
                    "region": seg["region"],
                    "revenue_jpy": 0.0,
                    "operating_profit_jpy": 0.0,
                    "segments": [],
                }
            cat_agg[cat_id]["companies"][comp_key]["revenue_jpy"] += rev_jpy
            cat_agg[cat_id]["companies"][comp_key]["operating_profit_jpy"] += op_jpy
            cat_agg[cat_id]["companies"][comp_key]["segments"].append({
                "segment_id": seg["segment_id"],
                "segment_name": seg["segment_name"],
                "revenue_jpy": rev_jpy,
                "operating_profit_jpy": op_jpy,
                "confidence": m["confidence"],
                "method": m["method"],
            })

        # 企業 × (primary category) 集計
        ck = (seg["company_code"], m["primary_category_id"])
        company_cat_agg[ck]["revenue_jpy"] += rev_jpy
        company_cat_agg[ck]["operating_profit_jpy"] += op_jpy
        company_cat_agg[ck]["segments"].append({
            "segment_id": seg["segment_id"],
            "segment_name": seg["segment_name"],
            "revenue_jpy": rev_jpy,
            "operating_profit_jpy": op_jpy,
        })

    # シェアとHHIを計算
    def compute_shares(cat_id: str) -> dict:
        cat = cat_agg[cat_id]
        total = cat["revenue_jpy"] or 1.0  # avoid div0
        companies = sorted(
            cat["companies"].values(),
            key=lambda c: c["revenue_jpy"],
            reverse=True,
        )
        ranks = []
        squared_shares = 0.0
        for c in companies:
            share = c["revenue_jpy"] / total if total > 0 else 0.0
            squared_shares += share ** 2
            ranks.append({
                **c,
                "share": share,
                "op_margin": (
                    c["operating_profit_jpy"] / c["revenue_jpy"]
                    if c["revenue_jpy"] > 0
                    else None
                ),
            })
        return {
            "revenue_jpy": cat["revenue_jpy"],
            "operating_profit_jpy": cat["operating_profit_jpy"],
            "op_margin": (
                cat["operating_profit_jpy"] / cat["revenue_jpy"]
                if cat["revenue_jpy"] > 0 else None
            ),
            "company_count": len(companies),
            "companies": ranks,
            "hhi": squared_shares * 10000,
            "top1_share": ranks[0]["share"] if ranks else None,
            "top3_share": sum(r["share"] for r in ranks[:3]) if ranks else 0,
            "top5_share": sum(r["share"] for r in ranks[:5]) if ranks else 0,
        }

    categories_out = {}
    for cat_id, agg in cat_agg.items():
        categories_out[cat_id] = {
            "id": cat_id,
            "name_ja": cat_map.get(cat_id, {}).get("name_ja"),
            "name_en": cat_map.get(cat_id, {}).get("name_en"),
            "level": cat_map.get(cat_id, {}).get("level"),
            **compute_shares(cat_id),
        }

    # 企業 × カテゴリマトリックス
    company_matrix = defaultdict(dict)
    for (code, cat_id), agg in company_cat_agg.items():
        company_matrix[code][cat_id] = {
            "revenue_jpy": agg["revenue_jpy"],
            "operating_profit_jpy": agg["operating_profit_jpy"],
            "segments": agg["segments"],
        }

    out = {
        "meta": {
            "fiscal_year": 2024,
            "usdjpy": USDJPY,
            "categories_covered": len(categories_out),
            "segments_mapped": used,
            "segments_skipped": skipped,
        },
        "categories": categories_out,
        "company_matrix": company_matrix,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    logger.info("wrote %s", OUT_JSON)

    write_markdown_report(out, cat_map)
    return out


def _fmt_jpy(v: float | None) -> str:
    if v is None or v == 0:
        return "-"
    if abs(v) >= 1e12:
        return f"{v/1e12:.2f}兆円"
    if abs(v) >= 1e8:
        return f"{v/1e8:,.0f}億円"
    return f"{v:,.0f}円"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v*100:.1f}%"


def write_markdown_report(out: dict, cat_map: dict[str, dict]) -> None:
    cats = out["categories"]
    # レポート対象: level >= 2 かつ _other 以外を優先、売上降順
    visible = [
        c for c in cats.values()
        if c.get("level", 0) >= 2 and not c["id"].startswith("_other")
    ]
    visible.sort(key=lambda c: c["revenue_jpy"], reverse=True)

    lines = []
    lines.append("# 半導体業界 セグメント別分析 (FY2024, JP+US)\n")
    lines.append(f"- 為替: 1 USD = {out['meta']['usdjpy']} JPY")
    lines.append(f"- セグメント数: {out['meta']['segments_mapped']}")
    lines.append("")

    lines.append("## 主要カテゴリ サマリー\n")
    lines.append("| カテゴリ | 売上(JPY換算) | 営業利益 | 利益率 | 企業数 | Top1シェア | HHI |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for c in visible[:30]:
        lines.append(
            f"| {c['name_ja']} ({c['id']}) | {_fmt_jpy(c['revenue_jpy'])} | {_fmt_jpy(c['operating_profit_jpy'])} | {_fmt_pct(c['op_margin'])} | {c['company_count']} | {_fmt_pct(c['top1_share'])} | {c['hhi']:.0f} |"
        )
    lines.append("")

    # 詳細: leaf カテゴリのランキング
    lines.append("## カテゴリ別企業ランキング\n")
    for c in visible[:20]:
        if not c["companies"]:
            continue
        lines.append(f"### {c['name_ja']} (`{c['id']}`) — 売上 {_fmt_jpy(c['revenue_jpy'])} (企業 {c['company_count']}社)\n")
        lines.append("| 順位 | 企業 | 地域 | 売上 | 営業利益 | 利益率 | シェア |")
        lines.append("|---:|---|---|---:|---:|---:|---:|")
        for i, comp in enumerate(c["companies"][:10], 1):
            lines.append(
                f"| {i} | {comp['name']} ({comp['code']}) | {comp['region']} | {_fmt_jpy(comp['revenue_jpy'])} | {_fmt_jpy(comp['operating_profit_jpy'])} | {_fmt_pct(comp['op_margin'])} | {_fmt_pct(comp['share'])} |"
            )
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote %s", OUT_MD)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    aggregate()


if __name__ == "__main__":
    main()
