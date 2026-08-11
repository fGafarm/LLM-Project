"""
Segment → Category mapper
--------------------------
3段階:
  1) Rule-based: 正規化したセグメント名/説明文を alias にマッチ
  2) LLM (Qwen3): 未マッチ or 低信頼度のものを分類
  3) Fallback: どうしても決められない場合は _other.unspecified

入力: industry_analysis/data/segment_store/{jp,us}/**/2024_segments.json
出力: industry_analysis/data/mappings/semiconductor_2024.json
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent.parent
CATEGORY_YAML = BASE / "category_tree" / "semiconductor.yaml"
SEGMENT_STORE = BASE / "data" / "segment_store"
MAPPING_OUT = BASE / "data" / "mappings" / "semiconductor_2024.json"

OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:14b"


@dataclass
class CategoryNode:
    id: str
    level: int
    name_ja: str
    name_en: str | None = None
    aliases: list[str] = field(default_factory=list)
    children: list["CategoryNode"] = field(default_factory=list)


def _parse_tree(nodes: list[dict], level: int = 1) -> list[CategoryNode]:
    result = []
    for n in nodes:
        cn = CategoryNode(
            id=n["id"],
            level=n.get("level", level),
            name_ja=n.get("name_ja", ""),
            name_en=n.get("name_en"),
            aliases=n.get("aliases", []) or [],
            children=_parse_tree(n.get("children", []) or [], level=level + 1),
        )
        result.append(cn)
    return result


def flatten_tree(nodes: list[CategoryNode]) -> list[CategoryNode]:
    out = []
    for n in nodes:
        out.append(n)
        out.extend(flatten_tree(n.children))
    return out


def load_category_tree() -> tuple[list[CategoryNode], list[CategoryNode]]:
    with open(CATEGORY_YAML, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    roots = _parse_tree(raw)
    flat = flatten_tree(roots)
    return roots, flat


def _normalize(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).lower().strip()
    for ch in ["事業", "部門", "セグメント", "・", " ", "　", "-", "&", ","]:
        t = t.replace(ch, "")
    # 全角中黒/ハイフンなど追加
    t = re.sub(r"[ \t\-_/]+", "", t)
    return t


def build_alias_index(flat: list[CategoryNode]) -> dict[str, CategoryNode]:
    idx: dict[str, CategoryNode] = {}
    for c in flat:
        if c.level < 2:  # 最上位カテゴリは alias マッチ対象外
            continue
        aliases = [c.name_ja, c.name_en] + list(c.aliases)
        for a in aliases:
            if not a:
                continue
            n = _normalize(a)
            if not n:
                continue
            # 既存 alias がより詳細なら上書きしない
            if n in idx:
                if idx[n].level >= c.level:
                    continue
            idx[n] = c
    return idx


# ---------------- Rule-based ----------------
@dataclass
class MappingResult:
    raw_segment_id: str
    company_code: str
    region: str
    segment_name: str
    segment_description: str | None
    primary_category_id: str
    primary_category_label: str
    confidence: float
    method: str  # "rule" / "llm" / "fallback"
    rationale: str = ""
    llm_model: str | None = None


def rule_based_map(
    segment_name: str,
    description: str | None,
    alias_index: dict[str, CategoryNode],
) -> MappingResult | None:
    norm_name = _normalize(segment_name)
    if norm_name in alias_index:
        cat = alias_index[norm_name]
        return MappingResult(
            raw_segment_id="",
            company_code="",
            region="",
            segment_name=segment_name,
            segment_description=description,
            primary_category_id=cat.id,
            primary_category_label=cat.name_ja,
            confidence=0.95,
            method="rule",
            rationale=f"alias完全一致: '{segment_name}' → {cat.name_ja}",
        )

    # partial: 長めのキーワードが segment 名 or 説明文に含まれるか
    text_full = f"{segment_name} {description or ''}"
    text_norm = _normalize(text_full)
    candidates: list[tuple[CategoryNode, int, str]] = []  # cat, match_len, alias
    for alias, cat in alias_index.items():
        if len(alias) < 3:
            continue
        if alias in text_norm:
            candidates.append((cat, len(alias), alias))
    if candidates:
        # 最長 alias かつ 最深階層 を優先
        candidates.sort(key=lambda x: (x[1], x[0].level), reverse=True)
        cat, match_len, alias = candidates[0]
        conf = 0.8 if match_len >= 5 else 0.65
        return MappingResult(
            raw_segment_id="",
            company_code="",
            region="",
            segment_name=segment_name,
            segment_description=description,
            primary_category_id=cat.id,
            primary_category_label=cat.name_ja,
            confidence=conf,
            method="rule",
            rationale=f"partial match: '{alias}' in segment → {cat.name_ja}",
        )
    return None


# ---------------- LLM-based ----------------
def _render_tree_for_prompt(nodes: list[CategoryNode], max_level: int = 4) -> str:
    lines = []

    def recurse(n: CategoryNode, depth: int):
        if n.level > max_level:
            return
        indent = "  " * (n.level - 1)
        alias_str = (
            f"  [aliases: {', '.join(n.aliases[:5])}]" if n.aliases else ""
        )
        lines.append(f"{indent}- {n.id}  | {n.name_ja} / {n.name_en or ''}{alias_str}")
        for c in n.children:
            recurse(c, depth + 1)

    for n in nodes:
        recurse(n, 0)
    return "\n".join(lines)


LLM_PROMPT_TMPL = """/no_think

You classify a company business segment into a standard semiconductor-industry category.

# Company
{company_name} ({region}, {value_chain_hint})

# Segment to classify
- Name: {segment_name}
- Description: {description}
- Approx revenue: {revenue_str}

# Category tree (choose one leaf, or an intermediate node if no leaf fits)
{tree}

# Rules
- Pick the single best `category_id` that describes the PRODUCTS this segment sells.
- Prefer the deepest (most specific) category that still fits.
- If the segment is NOT a semiconductor-related business, use `_other.non_semi`.
- If the segment is eliminations / corporate / unallocated, use `_other.unspecified`.
- Return confidence 0.0-1.0: 1.0 = perfect match, 0.5 = guessing.

# Output (JSON only, no other text)
{{"category_id": "...", "confidence": 0.9, "rationale": "..."}}
"""


def call_llm_classify(
    segment_name: str,
    description: str | None,
    revenue: float | None,
    company_name: str,
    region: str,
    value_chain_hint: str,
    tree_rendered: str,
    model: str = DEFAULT_MODEL,
    timeout: int = 180,
) -> dict:
    rev_str = "n/a"
    if revenue:
        if region == "JP":
            rev_str = f"{int(revenue/1e8):,} 億円"
        else:
            rev_str = f"${int(revenue/1e6):,}M"
    prompt = LLM_PROMPT_TMPL.format(
        company_name=company_name,
        region=region,
        value_chain_hint=value_chain_hint,
        segment_name=segment_name,
        description=description or "(none)",
        revenue_str=rev_str,
        tree=tree_rendered,
    )
    resp = requests.post(
        f"{OLLAMA_ENDPOINT}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 16000},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    raw = resp.json()["response"]
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON: {raw[:200]}")
    return json.loads(raw[start:end + 1])


# ---------------- Driver ----------------
def _load_all_segments() -> list[dict]:
    """全セグメント JSON を読み込み、各 segment をフラット化"""
    rows = []
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
                company_name = data.get("company_name", comp_dir.name)
                company_code = data.get("company_code") or data.get("ticker") or ""
                for seg in data.get("segments", []):
                    rows.append({
                        "region": region.upper(),
                        "company_code": company_code,
                        "company_name": company_name,
                        "segment_id": seg.get("segment_id", ""),
                        "segment_name": (
                            seg.get("segment_label_ja")
                            or seg.get("segment_label_en")
                            or seg.get("segment_id", "?")
                        ),
                        "segment_name_en": seg.get("segment_label_en"),
                        "description": seg.get("description"),
                        "values": seg.get("values", {}),
                        "single_segment": data.get("single_segment_fallback", False),
                    })
    return rows


def _load_company_hints() -> dict[str, dict]:
    """semiconductor_companies.yaml から value_chain などヒントを読む"""
    with open(BASE / "semiconductor_companies.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    hints: dict[str, dict] = {}
    for c in cfg.get("jp", []):
        hints[str(c["code"])] = {
            "value_chain": c.get("value_chain"),
            "primary_focus": c.get("primary_focus"),
            "note": c.get("note"),
        }
    for c in cfg.get("us", []):
        hints[c["ticker"]] = {
            "value_chain": c.get("value_chain"),
            "primary_focus": c.get("primary_focus"),
            "note": c.get("note"),
        }
    return hints


def run(model: str = DEFAULT_MODEL, llm_threshold: float = 0.75) -> list[MappingResult]:
    roots, flat = load_category_tree()
    alias_index = build_alias_index(flat)
    id_to_cat = {c.id: c for c in flat}
    tree_rendered = _render_tree_for_prompt(roots)
    rows = _load_all_segments()
    hints = _load_company_hints()

    results: list[MappingResult] = []
    for row in rows:
        # まずルール
        r = rule_based_map(row["segment_name"], row["description"], alias_index)
        if r and r.confidence >= llm_threshold:
            r.raw_segment_id = row["segment_id"]
            r.company_code = row["company_code"]
            r.region = row["region"]
            results.append(r)
            continue
        # LLM fallback
        hint = hints.get(row["company_code"], {})
        vchain = hint.get("value_chain", "unknown")
        try:
            llm_out = call_llm_classify(
                segment_name=row["segment_name"],
                description=row["description"],
                revenue=row["values"].get("revenue_total") or row["values"].get("revenue_external"),
                company_name=row["company_name"],
                region=row["region"],
                value_chain_hint=vchain,
                tree_rendered=tree_rendered,
                model=model,
            )
            cat_id = llm_out.get("category_id", "_other.unspecified")
            if cat_id not in id_to_cat:
                logger.warning(
                    "LLM returned unknown category_id=%s for %s/%s", cat_id, row["company_name"], row["segment_name"]
                )
                cat_id = "_other.unspecified"
            cat = id_to_cat[cat_id]
            results.append(MappingResult(
                raw_segment_id=row["segment_id"],
                company_code=row["company_code"],
                region=row["region"],
                segment_name=row["segment_name"],
                segment_description=row["description"],
                primary_category_id=cat.id,
                primary_category_label=cat.name_ja,
                confidence=float(llm_out.get("confidence", 0.6)),
                method="llm",
                rationale=llm_out.get("rationale", ""),
                llm_model=model,
            ))
            logger.info(
                "[LLM] %s/%s → %s (%.2f)",
                row["company_name"], row["segment_name"], cat.id, float(llm_out.get("confidence", 0.6))
            )
        except Exception as e:
            logger.error(
                "LLM fail for %s/%s: %s — falling back", row["company_name"], row["segment_name"], e
            )
            results.append(MappingResult(
                raw_segment_id=row["segment_id"],
                company_code=row["company_code"],
                region=row["region"],
                segment_name=row["segment_name"],
                segment_description=row["description"],
                primary_category_id="_other.unspecified",
                primary_category_label="未分類",
                confidence=0.0,
                method="fallback",
                rationale=f"LLM fail: {e}",
            ))

    # Apply manual overrides
    override_path = BASE / "data" / "mappings" / "manual_overrides.json"
    if override_path.exists():
        with open(override_path, encoding="utf-8") as f:
            over = json.load(f).get("overrides", [])
        applied = 0
        for r in results:
            for o in over:
                if str(r.company_code) == str(o["company_code"]) and o["segment_name_match"] in (r.segment_name or ""):
                    r.primary_category_id = o["new_category_id"]
                    r.primary_category_label = o["new_category_label"]
                    r.method = "manual_override"
                    r.confidence = 1.0
                    r.rationale = o.get("rationale", r.rationale)
                    applied += 1
                    break
        logger.info("Applied %d manual overrides", applied)

    # save
    MAPPING_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(MAPPING_OUT, "w", encoding="utf-8") as f:
        json.dump(
            {
                "count": len(results),
                "mappings": [asdict(r) for r in results],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Saved %d mappings to %s", len(results), MAPPING_OUT)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="rule confidence below which LLM is invoked")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(model=args.model, llm_threshold=args.threshold)


if __name__ == "__main__":
    main()
