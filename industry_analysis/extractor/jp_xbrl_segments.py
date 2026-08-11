"""
JP XBRL segment extractor
-------------------------
EDINET 有報 XBRL ZIP から OperatingSegmentsAxis を軸にしたセグメント別
売上/営業利益/資産/従業員/設備投資/減価償却 を抽出する。

入力: E:/PDF/PDF+XBRL/{year}/有報/ 配下の ZIP
出力: industry_analysis/data/segment_store/jp/{code}_{name}/{year}_segments.json
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

XBRL_BASE = Path(r"E:/PDF/PDF+XBRL")
OUTPUT_BASE = Path(__file__).parent.parent / "data" / "segment_store" / "jp"
XBRL_STORE_BASE = Path(__file__).parent.parent.parent / "financial_analysis_system" / "xbrl_store"

# ------------------------------------------------------------
# Concept → canonical field mapping
# 優先度(順): 先にマッチしたものを採用
# ------------------------------------------------------------
CONCEPT_PRIORITY: list[tuple[str, list[str]]] = [
    ("revenue_external", [
        "RevenuesFromExternalCustomers",
        "RevenueFromExternalCustomersIFRS",
        "RevenueFromExternalCustomerIFRS",
    ]),
    ("revenue_total", [
        "NetSales",
        "Revenue",
        "RevenueIFRS",
    ]),
    ("revenue_internal", [
        "TransactionsWithOtherSegments",
        "TransactionsWithOtherOperatingSegmentsIFRS",
    ]),
    ("operating_profit", [
        "OperatingIncome",
        "OperatingProfitLoss",
        "OperatingProfitLossIFRS",
        "SegmentProfitLoss",
        "SegmentIncomeLossIFRS",
    ]),
    ("segment_assets", [
        "Assets",
        "AssetsIFRS",
        "SegmentAssets",
    ]),
    ("employees", [
        "NumberOfEmployees",
    ]),
    ("capex", [
        "IncreaseInPropertyPlantAndEquipmentAndIntangibleAssets",
        "CapitalExpendituresOverviewOfCapitalExpendituresEtc",
        "CapitalExpenditures",
    ]),
    ("depreciation", [
        "DepreciationSegmentInformation",
        "Depreciation",
        "DepreciationAndAmortizationOperatingExpensesIFRS",
    ]),
    ("rd_expense", [
        "ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities",
        "ResearchAndDevelopmentExpenses",
    ]),
]

# concept name -> canonical field (flattened for O(1) lookup)
CONCEPT_TO_CANONICAL: dict[str, str] = {}
for canonical, concepts in CONCEPT_PRIORITY:
    for c in concepts:
        CONCEPT_TO_CANONICAL[c] = canonical

# P/L 系か B/S 系か: 優先 period を決めるため
INSTANT_FIELDS = {"segment_assets", "employees"}


@dataclass
class SegmentFact:
    segment_id: str             # 例: E00776_FunctionalMaterialsReportableSegmentsMember
    segment_member_qname: str   # 例: jpcrp030000-asr_E00776-000:FunctionalMaterialsReportableSegmentsMember
    segment_label_ja: str | None = None
    segment_label_en: str | None = None
    values: dict[str, float | int] = field(default_factory=dict)
    source_concepts: dict[str, str] = field(default_factory=dict)


def _find_xbrl_zip(edinet_code: str, fiscal_year: int) -> Path | None:
    """指定年の有報ZIPを探す。fiscal_year は決算期(例: 2024-03期 → 2024)"""
    # fiscal_year 年フォルダ配下の有報を最優先、翌年も探す(3月決算企業は6月頃開示)
    for yr in (fiscal_year, fiscal_year + 1):
        dir_path = XBRL_BASE / str(yr) / "有報"
        if not dir_path.exists():
            continue
        for f in dir_path.glob(f"*_{edinet_code}_*.zip"):
            return f
        # edinet_code で見つからなければ、ファイル名パターンが "{code}_{name}_{yyyymmdd}_有報_S10xxxxx.zip" なので
        # 代替: ファイル名の日付で絞り込む + edinet は metadata から
    # fallback: scan by year
    dir_path = XBRL_BASE / str(fiscal_year) / "有報"
    if dir_path.exists():
        for f in dir_path.glob("*.zip"):
            if f"_{edinet_code}_" in f.stem or f.stem.endswith(edinet_code):
                return f
    return None


def _find_xbrl_zip_by_code(securities_code: str, fiscal_year: int) -> Path | None:
    """証券コードでも検索"""
    for yr in (fiscal_year, fiscal_year + 1):
        dir_path = XBRL_BASE / str(yr) / "有報"
        if not dir_path.exists():
            continue
        matches = list(dir_path.glob(f"{securities_code}_*.zip"))
        if matches:
            # 最新(decimals か日付でソート): filename にファイル日付が含まれるので最大を
            return max(matches, key=lambda p: p.name)
    return None


def _parse_period_type(ctx_id: str) -> str:
    """context id から period type を分類"""
    if "CurrentYearDuration" in ctx_id:
        return "current_duration"
    if "CurrentYearInstant" in ctx_id:
        return "current_instant"
    if "Prior1YearDuration" in ctx_id:
        return "prior1_duration"
    if "Prior1YearInstant" in ctx_id:
        return "prior1_instant"
    if "Prior2YearDuration" in ctx_id:
        return "prior2_duration"
    return "other"


def _extract_labels(zf: zipfile.ZipFile) -> dict[str, str]:
    """ラベルリンクベースから localname -> 日本語ラベル の辞書を作る"""
    result: dict[str, str] = {}
    lab_files = [n for n in zf.namelist()
                 if n.endswith(".xml") and "lab" in n.lower() and "lab-en" not in n and "PublicDoc" in n]
    if not lab_files:
        return result
    for lab_name in lab_files:
        content = zf.read(lab_name).decode("utf-8", errors="replace")
        # label_{localname} → <link:label ... xlink:label="label_X"...xml:lang="ja">text</link:label>
        # 上 2行が別行のこともあるので多行対応
        for m in re.finditer(
            r'<link:label[^>]*xlink:label="label_([A-Za-z0-9_]+?)(?:_\d+)?"[^>]*xml:lang="ja"[^>]*>([^<]+)</link:label>',
            content,
            re.DOTALL,
        ):
            localname, text = m.group(1), m.group(2).strip()
            # "_2" 付きのラベル(説明) は avoid すでに拾っているなら上書きしない
            if localname not in result:
                result[localname] = text
    return result


def extract_segments(xbrl_zip_path: Path) -> dict:
    """
    1企業1年分のXBRL ZIPから segment 情報を抽出して辞書を返す
    """
    with zipfile.ZipFile(xbrl_zip_path) as zf:
        xbrl_names = [n for n in zf.namelist() if n.endswith(".xbrl") and "PublicDoc" in n]
        if not xbrl_names:
            raise ValueError(f"No XBRL in PublicDoc: {xbrl_zip_path}")
        content = zf.read(xbrl_names[0]).decode("utf-8", errors="replace")
        labels_ja = _extract_labels(zf)

    # 1) context id → (segment_member, period_type)
    # NonConsolidated(単体)コンテキストはスキップし、連結値のみ対象。
    ctx_to_seg: dict[str, tuple[str, str]] = {}
    fiscal_period_end = None
    for m in re.finditer(
        r'<xbrli:context[^>]*id="([^"]+)"[^>]*>(.*?)</xbrli:context>',
        content,
        re.DOTALL,
    ):
        ctx_id, ctx_body = m.group(1), m.group(2)
        if "OperatingSegmentsAxis" not in ctx_body:
            continue
        if "NonConsolidatedMember" in ctx_body:
            continue
        mm = re.search(
            r'dimension="jpcrp_cor:OperatingSegmentsAxis"[^>]*>([^<]+)<',
            ctx_body,
        )
        if not mm:
            continue
        seg_member = mm.group(1).strip()
        ptype = _parse_period_type(ctx_id)
        ctx_to_seg[ctx_id] = (seg_member, ptype)
        if ptype == "current_duration" and fiscal_period_end is None:
            em = re.search(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", ctx_body)
            if em:
                fiscal_period_end = em.group(1).strip()
        elif ptype == "current_instant" and fiscal_period_end is None:
            im = re.search(r"<xbrli:instant>([^<]+)</xbrli:instant>", ctx_body)
            if im:
                fiscal_period_end = im.group(1).strip()

    # 2) segment_member -> concept -> period_type -> value
    fact_re = re.compile(
        r'<([a-zA-Z_]+):([A-Za-z0-9_]+)\s+contextRef="([^"]+)"([^>]*)>([^<]+)</\1:\2>'
    )
    seg_facts: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for fm in fact_re.finditer(content):
        _, localname, ctxref, _attrs, value = fm.groups()
        if ctxref not in ctx_to_seg:
            continue
        seg_member, ptype = ctx_to_seg[ctxref]
        try:
            val = float(value)
        except ValueError:
            continue
        seg_facts[seg_member][localname][ptype] = val

    # 3) segment ごとに canonical field に畳む
    segments: list[SegmentFact] = []
    for seg_member, concept_map in seg_facts.items():
        # segment_id: E{code}_MemberName or ReportableSegmentsMember
        local_segname = seg_member.split(":")[-1] if ":" in seg_member else seg_member
        prefix = seg_member.split(":")[0] if ":" in seg_member else ""
        edinet_match = re.search(r"E(\d{5})-?\d*", prefix)
        segment_id = (
            f"E{edinet_match.group(1)}_{local_segname}"
            if edinet_match
            else f"std_{local_segname}"
        )
        sf = SegmentFact(
            segment_id=segment_id,
            segment_member_qname=seg_member,
            segment_label_ja=labels_ja.get(local_segname),
        )
        # canonical に変換
        for canonical, candidates in CONCEPT_PRIORITY:
            for c in candidates:
                if c in concept_map:
                    periods = concept_map[c]
                    # instant 系は current_instant、それ以外は current_duration 優先
                    if canonical in INSTANT_FIELDS:
                        v = periods.get("current_instant") or periods.get("current_duration")
                    else:
                        v = periods.get("current_duration") or periods.get("current_instant")
                    if v is not None:
                        # employees は整数化
                        sf.values[canonical] = int(v) if canonical == "employees" else v
                        sf.source_concepts[canonical] = c
                        break  # 次の canonical へ
        segments.append(sf)

    # 4) 合成: revenue_total が無く external + internal があれば合成
    for sf in segments:
        if "revenue_total" not in sf.values:
            ext = sf.values.get("revenue_external")
            itn = sf.values.get("revenue_internal")
            if ext is not None:
                sf.values["revenue_total"] = ext + (itn or 0)
                sf.source_concepts["revenue_total"] = "synthesized_from_external_plus_internal"

    # 5) ReportableSegmentsMember / ReconcilingItemsMember / 他を分離
    reportable_total = None
    reconciling = None
    actual_segments = []
    for sf in segments:
        if "ReconcilingItemsMember" in sf.segment_member_qname:
            reconciling = sf
        elif sf.segment_member_qname.endswith(":ReportableSegmentsMember") or sf.segment_member_qname.endswith(":TotalOfReportableSegmentsAndOthersMember"):
            reportable_total = sf
        elif "CorporateShared" in sf.segment_member_qname or "OperatingSegmentsNotIncludedInReportableSegments" in sf.segment_member_qname:
            # その他/全社共通 → non-actual としておく
            sf.segment_label_ja = sf.segment_label_ja or "その他・全社"
            actual_segments.append(sf)
        else:
            actual_segments.append(sf)

    return {
        "fiscal_period_end": fiscal_period_end,
        "xbrl_zip": str(xbrl_zip_path),
        "segment_count": len(actual_segments),
        "segments": [asdict(s) for s in actual_segments],
        "reportable_total": asdict(reportable_total) if reportable_total else None,
        "reconciling": asdict(reconciling) if reconciling else None,
    }


def _load_from_xbrl_store(code: str, name: str, fiscal_year: int) -> dict | None:
    """
    xbrl_store/{code}_{name}/{year}.json から全社集計値を読む
    単一セグメント企業の fallback 用
    """
    # xbrl_store 配下のフォルダ名は {code}_{正式社名} 形式で、yaml の name と異なる可能性がある
    for cand_dir in XBRL_STORE_BASE.glob(f"{code}_*"):
        if not cand_dir.is_dir():
            continue
        year_file = cand_dir / f"{fiscal_year}.json"
        if year_file.exists():
            with open(year_file, encoding="utf-8") as f:
                return json.load(f)
    return None


def run_for_company(
    code: str,
    name: str,
    edinet: str | None,
    fiscal_year: int,
    force: bool = False,
) -> dict | None:
    """1社1年の抽出を実行。既に出力済みなら skip。"""
    out_dir = OUTPUT_BASE / f"{code}_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{fiscal_year}_segments.json"
    if out_path.exists() and not force:
        logger.info("SKIP (exists): %s", out_path)
        with open(out_path, encoding="utf-8") as f:
            return json.load(f)

    zip_path: Path | None = None
    if edinet:
        zip_path = _find_xbrl_zip(edinet, fiscal_year)
    if zip_path is None:
        zip_path = _find_xbrl_zip_by_code(code, fiscal_year)
    if zip_path is None:
        logger.warning("[NOT FOUND] %s %s FY%d", code, name, fiscal_year)
        return None

    try:
        result = extract_segments(zip_path)
    except Exception as e:
        logger.error("[FAIL] %s %s: %s", code, name, e)
        return None

    # 単一セグメント企業: segments が空 → 全社値で疑似セグメントを作る
    single_segment_fallback = False
    if result["segment_count"] == 0:
        store = _load_from_xbrl_store(code, name, fiscal_year)
        if store and isinstance(store.get("data"), dict):
            data = store["data"]
            pseudo = {
                "segment_id": f"{code}_WHOLE_COMPANY",
                "segment_member_qname": "",
                "segment_label_ja": f"{name}(全社 / 単一セグメント)",
                "segment_label_en": None,
                "values": {},
                "source_concepts": {"note": "xbrl_store aggregate (single segment)"},
            }
            # 連結ベースの数値を優先
            mapping = {
                "revenue_external": ["revenue", "net_sales", "sales"],
                "revenue_total": ["revenue", "net_sales", "sales"],
                "operating_profit": ["operating_income", "operating_profit"],
                "segment_assets": ["total_assets"],
                "employees": ["employees_consolidated", "employees", "number_of_employees"],
                "capex": ["capex", "capital_expenditure"],
                "depreciation": ["depreciation"],
                "rd_expense": ["research_development", "rd_expense"],
            }
            for canonical, keys in mapping.items():
                for k in keys:
                    v = data.get(k)
                    if v is not None and v != 0:
                        pseudo["values"][canonical] = v
                        break
            if pseudo["values"].get("revenue_total") or pseudo["values"].get("operating_profit"):
                result["segments"] = [pseudo]
                result["segment_count"] = 1
                single_segment_fallback = True

    payload = {
        "source": "jp_xbrl",
        "company_code": code,
        "company_name": name,
        "edinet_code": edinet,
        "fiscal_year": fiscal_year,
        "single_segment_fallback": single_segment_fallback,
        **result,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(
        "[OK] %s %s FY%d  %d segs",
        code,
        name,
        fiscal_year,
        result["segment_count"],
    )
    return payload


def main():
    import argparse
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--companies-yaml", type=str, default=None,
                        help="YAML with list of JP companies. default: ../semiconductor_companies.yaml")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--code", type=str, help="single company run")
    parser.add_argument("--edinet", type=str, help="edinet code for single-company run")
    parser.add_argument("--name", type=str, default="test", help="name for single-company run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.code:
        run_for_company(args.code, args.name, args.edinet, args.year, force=args.force)
        return

    yaml_path = Path(args.companies_yaml) if args.companies_yaml else (
        Path(__file__).parent.parent / "semiconductor_companies.yaml"
    )
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    jp = cfg.get("jp", [])
    ok = 0
    fail = 0
    for c in jp:
        result = run_for_company(
            code=str(c["code"]),
            name=c["name"],
            edinet=c.get("edinet"),
            fiscal_year=args.year,
            force=args.force,
        )
        if result:
            ok += 1
        else:
            fail += 1
    print(f"\nDone. OK={ok} FAIL={fail}")


if __name__ == "__main__":
    main()
