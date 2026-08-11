#!/usr/bin/env python3
"""
XBRL財務三表の原本構造を抽出するスクリプト

各企業のXBRL ZIPファイルから:
1. _pre.xml (Presentation Linkbase) → 階層構造と表示順序
2. _lab.xml (Label Linkbase) → 企業固有拡張タグの日本語ラベル
3. iXBRL HTML → タグ→日本語ラベルマッピング（標準タクソノミ補完）
4. raw_tags.json → 値

を読み取り、{year}_statements.json として保存する。

Usage:
    python extract_all_statements.py --company 9984  # 1社テスト
    python extract_all_statements.py --all            # 全社処理
    python extract_all_statements.py --all --skip-existing  # 差分処理
"""

import argparse
import glob
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from lxml import etree

# ============================================================
# Constants
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XBRL_STORE = os.path.join(BASE_DIR, "xbrl_store")
LABEL_DICT_PATH = os.path.join(BASE_DIR, "standard_taxonomy_labels.json")

# ZIP source directories
ZIP_BASES = [r"E:\PDF\PDF+XBRL"]

# Map _pre.xml role URIs to statement types
ROLE_MAPPING = {
    # IFRS consolidated
    "rol_ConsolidatedStatementOfFinancialPositionIFRS": ("bs", True),
    "rol_ConsolidatedStatementOfProfitOrLossIFRS": ("pl", True),
    "rol_ConsolidatedStatementOfIncomeIFRS": ("pl", True),
    "rol_ConsolidatedStatementOfCashFlowsIFRS": ("cf", True),
    # J-GAAP consolidated
    "rol_ConsolidatedBalanceSheet": ("bs", True),
    "rol_ConsolidatedStatementOfIncome": ("pl", True),
    "rol_ConsolidatedStatementOfCashFlows-indirect": ("cf", True),
    "rol_ConsolidatedStatementOfCashFlows-direct": ("cf", True),
    "rol_ConsolidatedStatementOfCashFlows": ("cf", True),
    # Non-consolidated (fallback if no consolidated)
    "rol_BalanceSheet": ("bs", False),
    "rol_StatementOfIncome": ("pl", False),
    "rol_StatementOfCashFlows-indirect": ("cf", False),
    "rol_StatementOfCashFlows-direct": ("cf", False),
    "rol_StatementOfCashFlows": ("cf", False),
}

# XBRL namespaces
NS = {
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
}

# ============================================================
# Abstract tag Japanese labels (hardcoded for IFRS & J-GAAP)
# These tags never appear in iXBRL HTML, so the label dictionary
# doesn't contain them. Hardcode the common ones.
# ============================================================

ABSTRACT_LABELS = {
    # --- IFRS B/S ---
    "AssetsIFRSAbstract": "資産の部",
    "CurrentAssetsIFRSAbstract": "流動資産",
    "NonCurrentAssetsIFRSAbstract": "非流動資産",
    "LiabilitiesAndEquityIFRSAbstract": "負債及び資本の部",
    "LiabilitiesIFRSAbstract": "負債",
    "CurrentLiabilitiesIFRSAbstract": "流動負債",
    "NonCurrentLiabilitiesIFRSAbstract": "非流動負債",
    "EquityIFRSAbstract": "資本",
    "EquityAttributableToOwnersOfParentIFRSAbstract": "親会社の所有者に帰属する持分",
    # --- IFRS P/L ---
    "StatementOfProfitOrLossIFRSAbstract": "損益計算書",
    "ProfitLossAttributableIFRSAbstract": "純利益の帰属",
    "EarningsPerShareIFRSAbstract": "1株当たり利益",
    "InvestmentGainLossIFRSAbstract": "投資損益",
    "GainLossOnInvestmentsIFRSAbstract": "投資損益",
    # --- IFRS CF ---
    "CashFlowsFromOperatingActivitiesIFRSAbstract": "営業活動によるキャッシュ・フロー",
    "CashFlowsFromInvestingActivitiesIFRSAbstract": "投資活動によるキャッシュ・フロー",
    "CashFlowsFromFinancingActivitiesIFRSAbstract": "財務活動によるキャッシュ・フロー",
    "StatementOfCashFlowsIFRSAbstract": "キャッシュ・フロー計算書",
    # --- J-GAAP B/S ---
    "AssetsAbstract": "資産の部",
    "CurrentAssetsAbstract": "流動資産",
    "NoncurrentAssetsAbstract": "固定資産",
    "PropertyPlantAndEquipmentAbstract": "有形固定資産",
    "IntangibleAssetsAbstract": "無形固定資産",
    "InvestmentsAndOtherAssetsAbstract": "投資その他の資産",
    "DeferredAssetsAbstract": "繰延資産",
    "LiabilitiesAbstract": "負債の部",
    "CurrentLiabilitiesAbstract": "流動負債",
    "NoncurrentLiabilitiesAbstract": "固定負債",
    "NetAssetsAbstract": "純資産の部",
    "ShareholdersEquityAbstract": "株主資本",
    "ValuationAndTranslationAdjustmentsAbstract": "評価・換算差額等",
    "SubscriptionRightsToSharesAbstract": "新株予約権",
    "MinorityInterestsAbstract": "非支配株主持分",
    # --- J-GAAP P/L ---
    "StatementOfIncomeAbstract": "損益計算書",
    "OperatingRevenuesAbstract": "営業収益",
    "OperatingExpensesAbstract": "営業費用",
    "NonOperatingIncomeAbstract": "営業外収益",
    "NonOperatingExpensesAbstract": "営業外費用",
    "ExtraordinaryIncomeAbstract": "特別利益",
    "ExtraordinaryLossAbstract": "特別損失",
    # --- J-GAAP CF ---
    "CashFlowsFromOperatingActivitiesAbstract": "営業活動によるキャッシュ・フロー",
    "CashFlowsFromInvestingActivitiesAbstract": "投資活動によるキャッシュ・フロー",
    "CashFlowsFromFinancingActivitiesAbstract": "財務活動によるキャッシュ・フロー",
    # --- Banking J-GAAP ---
    "OrdinaryIncomeBNKAbstract": "経常収益",
    "OrdinaryExpensesBNKAbstract": "経常費用",
    "InterestIncomeBNKAbstract": "資金運用収益",
    "InterestExpensesBNKAbstract": "資金調達費用",
    # --- Insurance ---
    "OrdinaryIncomeINSAbstract": "経常収益",
    "OrdinaryExpensesINSAbstract": "経常費用",
}


# ============================================================
# Label Dictionary
# ============================================================

def load_label_dictionary():
    """Load the standard taxonomy label dictionary."""
    if not os.path.exists(LABEL_DICT_PATH):
        print(f"WARNING: Label dictionary not found at {LABEL_DICT_PATH}")
        print("Run with --build-labels first to build it.")
        return {}
    with open(LABEL_DICT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("standard", {})


def build_label_dictionary_from_zips(year="2024"):
    """Build label dictionary by parsing iXBRL HTML from all ZIPs."""
    print(f"Building label dictionary from {year} ZIPs...")

    zip_dir = None
    for base in ZIP_BASES:
        candidate = os.path.join(base, year, "有報")
        if os.path.isdir(candidate):
            zip_dir = candidate
            break

    if not zip_dir:
        print(f"ERROR: ZIP directory not found for year {year}")
        return {}

    all_zips = glob.glob(os.path.join(zip_dir, "*.zip"))
    print(f"Found {len(all_zips)} ZIPs in {zip_dir}")

    all_labels = {}
    processed = 0
    errors = 0

    for zip_path in all_zips:
        try:
            labels = _extract_labels_from_ixbrl(zip_path)
            for tag, label in labels.items():
                # Prefer real labels over &#160; placeholders
                if tag not in all_labels or all_labels[tag] in ("&#160;", "&nbsp;", "\xa0"):
                    all_labels[tag] = label
            processed += 1
            if processed % 500 == 0:
                print(f"  Processed {processed}/{len(all_zips)}, labels: {len(all_labels)}")
        except Exception as e:
            errors += 1

    # Filter to standard tags only
    standard = {}
    for tag, label in all_labels.items():
        if any(ns in tag for ns in ["jpigp_cor:", "jppfs_cor:", "jpcrp_cor:", "jpdei_cor:"]):
            standard[tag] = label

    print(f"Done. Processed: {processed}, Errors: {errors}")
    print(f"Total labels: {len(all_labels)}, Standard: {len(standard)}")

    output = {
        "standard": standard,
        "meta": {
            "source": f"iXBRL HTML parsing from {year} 有報",
            "companies_processed": processed,
            "total_labels": len(all_labels),
            "standard_labels": len(standard),
        },
    }
    with open(LABEL_DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(standard)} standard labels to {LABEL_DICT_PATH}")
    return standard


def _extract_labels_from_ixbrl(zip_path):
    """Extract tag->label mappings from iXBRL HTML files in a ZIP."""
    labels = {}
    z = zipfile.ZipFile(zip_path)
    ixbrl_files = [n for n in z.namelist() if "_ixbrl.htm" in n and "AuditDoc" not in n]

    for ixf in ixbrl_files:
        content = z.read(ixf).decode("utf-8", errors="replace")
        if len(re.findall(r"<ix:nonFraction", content)) < 10:
            continue

        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.DOTALL)
        for tr in trs:
            nf_matches = re.findall(
                r'<ix:nonFraction[^>]*name="([^"]+)"[^>]*>([^<]*)</ix:nonFraction>', tr
            )
            if not nf_matches:
                continue

            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
            texts = []
            for td in tds:
                clean = re.sub(r"<[^>]+>", "", td).strip()
                clean = re.sub(r"&nbsp;", " ", clean).strip()
                if clean:
                    texts.append(clean)

            for tag_name, value in nf_matches:
                label = ""
                for t in texts:
                    if (not re.match(r"^[\d,\.\-\s△※　（）\(\)]+$", t)
                            and len(t) > 1
                            and t not in ("&#160;", "&nbsp;", "\xa0")):
                        label = t
                        break
                if label and tag_name not in labels:
                    labels[tag_name] = label

    z.close()
    return labels


# ============================================================
# Helper: Extract local tag name from fragment
# ============================================================

def _extract_local_name(fragment):
    """
    Extract local tag name from an XBRL locator fragment.

    Standard tags: "jpigp_cor_CashAndCashEquivalentsIFRS" -> ("jpigp_cor", "CashAndCashEquivalentsIFRS")
    Extension tags: "jpcrp030000-asr_E02778-000_DepositsForBankingBusinessCLIFRS" -> ("ext", "DepositsForBankingBusinessCLIFRS")
    """
    if "_cor_" in fragment:
        parts = fragment.split("_cor_", 1)
        ns = parts[0] + "_cor"
        tag = parts[1]
        return ns, tag

    if "_rt_" in fragment:
        return None, None  # Role type reference, skip

    # Company extension tag
    # Pattern: jpcrp030000-asr_E02778-000_TagName
    m = re.match(r'.*?_E\d{5}-\d{3}_(.+)', fragment)
    if m:
        return "ext", m.group(1)

    # Fallback: take the last segment after underscore
    if "_" in fragment:
        return "ext", fragment.rsplit("_", 1)[-1]

    return "ext", fragment


# ============================================================
# Label resolution from _lab.xml (company extensions)
# ============================================================

def extract_extension_labels(lab_content):
    """
    Parse _lab.xml to get extension tag Japanese labels.
    Returns: {tag_local_name: japanese_label}
    """
    labels = {}
    try:
        root = etree.fromstring(lab_content)
    except Exception:
        return labels

    # Collect label texts (ja only)
    label_texts = {}  # xlink:label -> text
    for elem in root.iter():
        local = etree.QName(elem.tag).localname
        if local == "label":
            lang = elem.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            if lang != "ja":
                continue
            role = elem.get("{http://www.w3.org/1999/xlink}role", "")
            label_id = elem.get("{http://www.w3.org/1999/xlink}label", "")
            text = (elem.text or "").strip()
            if label_id and text:
                # Prefer standard label role
                if label_id not in label_texts or "role/label" in role:
                    label_texts[label_id] = text

    # Collect locators: loc_label -> concept_name (fragment after #)
    locs = {}
    for elem in root.iter():
        local = etree.QName(elem.tag).localname
        if local == "loc":
            loc_label = elem.get("{http://www.w3.org/1999/xlink}label", "")
            href = elem.get("{http://www.w3.org/1999/xlink}href", "")
            if "#" in href:
                concept = href.split("#")[-1]
                locs[loc_label] = concept

    # Collect label arcs: concept -> Japanese label
    for elem in root.iter():
        local = etree.QName(elem.tag).localname
        if local == "labelArc":
            from_loc = elem.get("{http://www.w3.org/1999/xlink}from", "")
            to_label = elem.get("{http://www.w3.org/1999/xlink}to", "")
            concept = locs.get(from_loc, "")
            text = label_texts.get(to_label, "")
            if concept and text:
                # Extract local tag name from concept
                _, tag_name = _extract_local_name(concept)
                if tag_name:
                    # Clean up context suffixes like "、流動負債" or "、非流動負債"
                    # Keep only the main label part
                    clean_text = text
                    if "、" in clean_text and len(clean_text.split("、")) == 2:
                        main_part = clean_text.split("、")[0]
                        suffix = clean_text.split("、")[1]
                        # Only strip if suffix looks like a balance sheet section
                        section_suffixes = ["流動資産", "非流動資産", "流動負債", "非流動負債",
                                            "固定負債", "固定資産", "純資産"]
                        if suffix in section_suffixes:
                            clean_text = main_part
                    labels[tag_name] = clean_text

    return labels


# ============================================================
# Presentation Linkbase Parsing (_pre.xml)
# ============================================================

def parse_presentation_linkbase(pre_content, raw_tags, label_dict, ext_labels, ixbrl_labels):
    """
    Parse _pre.xml to build ordered financial statement structures.

    Key insight: _pre.xml uses multiple locators (different xlink:labels) for
    the same concept. We must unify by concept tag name, not locator label.

    Returns: (statements_dict, accounting_standard)
    """
    try:
        root = etree.fromstring(pre_content)
    except Exception as e:
        print(f"  ERROR parsing _pre.xml: {e}")
        return {}, "JGAAP"

    # Detect accounting standard
    accounting_standard = "JGAAP"
    pre_text = pre_content.decode("utf-8", errors="replace") if isinstance(pre_content, bytes) else pre_content
    if "jpigp" in pre_text or "IFRS" in pre_text:
        accounting_standard = "IFRS"

    results = {}

    for plink in root.findall(f".//{{{NS['link']}}}presentationLink"):
        role = plink.get(f"{{{NS['xlink']}}}role", "")

        # Determine statement type
        stmt_type = None
        is_consolidated = None
        for role_key, (stype, consolidated) in ROLE_MAPPING.items():
            if role_key in role:
                stmt_type = stype
                is_consolidated = consolidated
                break

        if not stmt_type:
            continue

        # Prefer consolidated over non-consolidated
        if stmt_type in results and not is_consolidated:
            continue

        # -------------------------------------------------------
        # Parse locators: xlink:label -> (ns, tag_local, fragment)
        # Also build loc_label -> tag_local mapping
        # -------------------------------------------------------
        loc_to_info = {}  # xlink:label -> (ns, tag_local, fragment)
        tag_info = {}     # tag_local -> (ns, tag_local, fragment) [first seen]

        for loc in plink.findall(f"{{{NS['link']}}}loc"):
            label = loc.get(f"{{{NS['xlink']}}}label", "")
            href = loc.get(f"{{{NS['xlink']}}}href", "")
            if "#" in href:
                fragment = href.split("#")[-1]
                ns, tag = _extract_local_name(fragment)
                if ns is None:
                    continue
                loc_to_info[label] = (ns, tag, fragment)
                if tag not in tag_info:
                    tag_info[tag] = (ns, tag, fragment)

        # -------------------------------------------------------
        # Parse arcs: build parent-child tree using TAG NAMES
        # This unifies multiple locators for the same concept
        # -------------------------------------------------------
        tag_children = defaultdict(list)  # parent_tag -> [(child_tag, order, is_total)]
        seen_edges = set()  # avoid duplicate edges

        for arc in plink.findall(f"{{{NS['link']}}}presentationArc"):
            from_label = arc.get(f"{{{NS['xlink']}}}from", "")
            to_label = arc.get(f"{{{NS['xlink']}}}to", "")
            order = float(arc.get("order", "1"))
            preferred_label = arc.get("preferredLabel", "")
            is_total = "totalLabel" in preferred_label

            from_info = loc_to_info.get(from_label)
            to_info = loc_to_info.get(to_label)
            if not from_info or not to_info:
                continue

            from_tag = from_info[1]
            to_tag = to_info[1]

            edge_key = (from_tag, to_tag)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            tag_children[from_tag].append((to_tag, order, is_total))

        # Sort children by order
        for parent in tag_children:
            tag_children[parent].sort(key=lambda x: x[1])

        # -------------------------------------------------------
        # Find root nodes using tag names
        # -------------------------------------------------------
        all_child_tags = set()
        for ch_list in tag_children.values():
            for ch_tag, _, _ in ch_list:
                all_child_tags.add(ch_tag)
        root_tags = [t for t in tag_children.keys() if t not in all_child_tags]

        # Find the best root (deepest tree, prefer Table/Heading)
        best_root = None
        best_depth = 0
        for rt in root_tags:
            d = _tree_depth_by_tag(rt, tag_children, 0)
            if d > best_depth:
                best_depth = d
                best_root = rt

        if not best_root and root_tags:
            best_root = root_tags[0]

        if not best_root:
            continue

        # -------------------------------------------------------
        # Label and value resolution helpers
        # -------------------------------------------------------
        def resolve_label(tag):
            """Resolve Japanese label for a tag."""
            # 1. Hardcoded abstract labels
            if tag in ABSTRACT_LABELS:
                return ABSTRACT_LABELS[tag]

            # 2. Extension labels from _lab.xml (keyed by tag local name)
            if tag in ext_labels:
                return ext_labels[tag]

            # Bad label values to skip
            _BAD = {"&#160;", "&nbsp;", "\xa0", ""}

            # 3. iXBRL labels from this company's HTML (namespace:tag format)
            for ns_prefix in ["jpigp_cor:", "jppfs_cor:", "jpcrp_cor:", "jpdei_cor:"]:
                full_key = ns_prefix + tag
                if full_key in ixbrl_labels:
                    lbl = ixbrl_labels[full_key]
                    if lbl and lbl.strip() and lbl.strip() not in _BAD:
                        return lbl

            # 4. Standard label dictionary
            for ns_prefix in ["jpigp_cor:", "jppfs_cor:", "jpcrp_cor:", "jpdei_cor:"]:
                full_key = ns_prefix + tag
                if full_key in label_dict:
                    lbl = label_dict[full_key]
                    if lbl and lbl.strip() and lbl.strip() not in _BAD:
                        return lbl

            # 5. For Abstract tags, try the base name (without "Abstract")
            if tag.endswith("Abstract"):
                base = tag[:-len("Abstract")]
                if base in ext_labels:
                    return ext_labels[base]
                for ns_prefix in ["jpigp_cor:", "jppfs_cor:", "jpcrp_cor:"]:
                    full_key = ns_prefix + base
                    if full_key in ixbrl_labels:
                        lbl = ixbrl_labels[full_key]
                        if lbl and lbl.strip() and lbl.strip() not in _BAD:
                            return lbl.replace("合計", "").strip() or lbl
                for ns_prefix in ["jpigp_cor:", "jppfs_cor:", "jpcrp_cor:"]:
                    full_key = ns_prefix + base
                    if full_key in label_dict:
                        lbl = label_dict[full_key]
                        if lbl and lbl.strip() and lbl.strip() not in _BAD:
                            return lbl.replace("合計", "").strip() or lbl

            # 6. Fallback: CamelCase -> readable
            clean = tag
            for sfx in ["IFRS", "Abstract", "SummaryOfBusinessResults",
                         "CAIFRS", "NCAIFRS", "CLIFRS", "NCLIFRS", "SSIFRS"]:
                clean = clean.replace(sfx, "")
            clean = re.sub(r"([a-z])([A-Z])", r"\1 \2", clean)
            return clean

        def resolve_value(tag):
            """Look up value from raw_tags by local tag name."""
            if tag in raw_tags:
                v = raw_tags[tag].get("value")
                if isinstance(v, (int, float)):
                    return v
            # Partial match for extension-prefixed tags only (require _ separator)
            for key, data in raw_tags.items():
                if key.endswith("_" + tag) or tag.endswith("_" + key):
                    v = data.get("value")
                    if isinstance(v, (int, float)):
                        return v
            return None

        def is_structural(tag):
            """Check if a tag is structural (skip but recurse children)."""
            return (tag.endswith("Table") or tag.endswith("Heading") or
                    tag.endswith("LineItems") or tag.endswith("Axis") or
                    tag.endswith("Member") or tag.endswith("Domain"))

        # -------------------------------------------------------
        # DFS using tag names
        # -------------------------------------------------------
        items = []
        visited = set()

        def dfs(tag, depth, is_total_flag=False):
            # Skip structural elements, recurse at same depth
            if is_structural(tag):
                for child_tag, _, child_is_total in tag_children.get(tag, []):
                    dfs(child_tag, depth, child_is_total)
                return

            is_abstract = tag.endswith("Abstract")

            # Deduplicate: skip if already visited (except abstracts may
            # legitimately appear in different contexts, but we only show once)
            if tag in visited:
                return
            visited.add(tag)

            value = None if is_abstract else resolve_value(tag)
            label = resolve_label(tag)

            items.append({
                "label": label,
                "value": value,
                "depth": depth,
                "total": is_total_flag,
                "abstract": is_abstract,
            })

            # Recurse into children at depth+1
            for child_tag, _, child_is_total in tag_children.get(tag, []):
                dfs(child_tag, depth + 1, child_is_total)

        # Start DFS from best root
        dfs(best_root, 0)

        # Filter empty branches and normalize depth
        if items:
            items = filter_empty_branches(items)
            items = _normalize_depth(items)
            results[stmt_type] = items

    return results, accounting_standard


def _tree_depth(node, children_map, current_depth):
    """Calculate the maximum depth of a subtree (locator-label based)."""
    if node not in children_map:
        return current_depth
    max_d = current_depth
    for child, _, _ in children_map[node]:
        d = _tree_depth(child, children_map, current_depth + 1)
        if d > max_d:
            max_d = d
    return max_d


def _tree_depth_by_tag(tag, tag_children_map, current_depth):
    """Calculate the maximum depth of a subtree (tag-name based)."""
    if tag not in tag_children_map:
        return current_depth
    max_d = current_depth
    for child_tag, _, _ in tag_children_map[tag]:
        d = _tree_depth_by_tag(child_tag, tag_children_map, current_depth + 1)
        if d > max_d:
            max_d = d
    return max_d


def _normalize_depth(items):
    """
    Normalize depth values so the minimum depth is 0.
    This handles cases where the DFS starts inside structural elements
    that add extra depth.
    """
    if not items:
        return items
    min_depth = min(item["depth"] for item in items)
    if min_depth > 0:
        for item in items:
            item["depth"] -= min_depth
    return items


def filter_empty_branches(items):
    """Remove abstract items whose subtree has no values, and remove null-value non-total leaves."""
    if not items:
        return []

    filtered = []
    i = 0
    while i < len(items):
        item = items[i]
        if item["abstract"]:
            # Check if any descendant has a value
            has_value = False
            j = i + 1
            while j < len(items) and items[j]["depth"] > item["depth"]:
                if items[j]["value"] is not None:
                    has_value = True
                    break
                j += 1
            if has_value:
                filtered.append(item)
        elif item["value"] is not None or item["total"]:
            filtered.append(item)
        i += 1

    return filtered


# ============================================================
# ZIP file finding
# ============================================================

def find_zip_for_company(code, year):
    """Find the ZIP file for a specific company and year."""
    for base in ZIP_BASES:
        zip_dir = os.path.join(base, str(year), "有報")
        if not os.path.isdir(zip_dir):
            continue
        pattern = os.path.join(zip_dir, f"{code}_*.zip")
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def find_all_zips_for_year(year):
    """Find all ZIP files for a specific year."""
    for base in ZIP_BASES:
        zip_dir = os.path.join(base, str(year), "有報")
        if os.path.isdir(zip_dir):
            return glob.glob(os.path.join(zip_dir, "*.zip"))
    return []


# ============================================================
# Main extraction
# ============================================================

def extract_statements_from_zip(zip_path, label_dict, raw_tags=None):
    """
    Extract structured financial statements from a single ZIP.

    Returns: (statements_dict, accounting_standard) or (None, None) on failure
    """
    try:
        z = zipfile.ZipFile(zip_path)
    except Exception as e:
        print(f"  ERROR opening ZIP: {e}")
        return None, None

    # Find _pre.xml and _lab.xml
    pre_files = [n for n in z.namelist() if "_pre.xml" in n and "AuditDoc" not in n]
    lab_files = [n for n in z.namelist() if "_lab.xml" in n and "lab-en" not in n and "AuditDoc" not in n]

    if not pre_files:
        z.close()
        return None, None

    # Parse _lab.xml for extension labels (keyed by tag local name)
    ext_labels = {}
    if lab_files:
        try:
            lab_content = z.read(lab_files[0])
            ext_labels = extract_extension_labels(lab_content)
        except Exception:
            pass

    # Also extract labels from iXBRL HTML for this company
    ixbrl_labels = _extract_labels_from_ixbrl_zip(z)

    # If no raw_tags provided, try to load from the XBRL instance doc
    if raw_tags is None:
        raw_tags = _extract_raw_tags_from_zip(z)

    # Parse _pre.xml
    try:
        pre_content = z.read(pre_files[0])
        statements, accounting_standard = parse_presentation_linkbase(
            pre_content, raw_tags, label_dict, ext_labels, ixbrl_labels
        )
    except Exception as e:
        print(f"  ERROR parsing presentation: {e}")
        z.close()
        return None, None

    z.close()

    return statements, accounting_standard


def _extract_labels_from_ixbrl_zip(z):
    """Extract tag->label from iXBRL HTML inside an already-opened ZIP."""
    labels = {}
    ixbrl_files = [n for n in z.namelist() if "_ixbrl.htm" in n and "AuditDoc" not in n]

    for ixf in ixbrl_files:
        try:
            content = z.read(ixf).decode("utf-8", errors="replace")
            if len(re.findall(r"<ix:nonFraction", content)) < 5:
                continue

            trs = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.DOTALL)
            for tr in trs:
                nf_matches = re.findall(
                    r'<ix:nonFraction[^>]*name="([^"]+)"[^>]*>([^<]*)</ix:nonFraction>', tr
                )
                if not nf_matches:
                    continue

                tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
                texts = []
                for td in tds:
                    clean = re.sub(r"<[^>]+>", "", td).strip()
                    clean = re.sub(r"&nbsp;", " ", clean).strip()
                    if clean:
                        texts.append(clean)

                for tag_name, value in nf_matches:
                    label = ""
                    for t in texts:
                        if not re.match(r"^[\d,\.\-\s△※　（）\(\)]+$", t) and len(t) > 1:
                            label = t
                            break
                    if label and tag_name not in labels:
                        labels[tag_name] = label
        except Exception:
            pass

    return labels


def _extract_raw_tags_from_zip(z):
    """Minimal extraction of XBRL instance values for matching."""
    raw_tags = {}
    xbrl_files = [n for n in z.namelist() if n.endswith(".xbrl") and "AuditDoc" not in n]

    if not xbrl_files:
        return raw_tags

    try:
        content = z.read(xbrl_files[0])
        root = etree.fromstring(content)

        for elem in root.iter():
            ctx = elem.get("contextRef")
            if ctx is None:
                continue

            # Only current year data
            if "Prior" in ctx or "previous" in ctx.lower():
                continue

            tag = etree.QName(elem.tag).localname
            text = (elem.text or "").strip().replace(",", "")

            try:
                value = float(text)
                if tag not in raw_tags:
                    raw_tags[tag] = {"value": value, "context": ctx}
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    return raw_tags


def process_company(code, years, label_dict, skip_existing=False):
    """Process a single company for all specified years."""
    # Find company folder in xbrl_store
    store_dirs = glob.glob(os.path.join(XBRL_STORE, f"{code}_*"))
    if not store_dirs:
        return 0

    store_dir = store_dirs[0]
    processed = 0

    for year in years:
        output_path = os.path.join(store_dir, f"{year}_statements.json")
        if skip_existing and os.path.exists(output_path):
            continue

        # Load raw_tags if available
        raw_tags_path = os.path.join(store_dir, f"{year}_raw_tags.json")
        raw_tags = {}
        if os.path.exists(raw_tags_path):
            with open(raw_tags_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_tags = data.get("tags", {})

        # Find ZIP
        zip_path = find_zip_for_company(code, year)
        if not zip_path:
            continue

        # Extract
        statements, acct_std = extract_statements_from_zip(zip_path, label_dict, raw_tags)
        if not statements:
            continue

        # Count items
        total_items = sum(len(v) for v in statements.values())
        valued_items = sum(
            1 for v in statements.values() for item in v if item.get("value") is not None
        )

        # Save
        output = {
            "company_code": code,
            "fiscal_year": str(year),
            "accounting_standard": acct_std,
            "statements": statements,
            "meta": {
                "total_items": total_items,
                "valued_items": valued_items,
                "source_zip": os.path.basename(zip_path),
            },
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        processed += 1

    return processed


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Extract XBRL financial statement structures")
    parser.add_argument("--company", type=str, help="Process single company by code")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--years", type=str, default="2024",
                        help="Comma-separated years (default: 2024)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip companies that already have _statements.json")
    parser.add_argument("--build-labels", action="store_true",
                        help="Build label dictionary from iXBRL HTML")
    parser.add_argument("--label-year", type=str, default="2024",
                        help="Year to use for building labels (default: 2024)")
    args = parser.parse_args()

    years = [int(y.strip()) for y in args.years.split(",")]

    if args.build_labels:
        build_label_dictionary_from_zips(args.label_year)
        return

    # Load label dictionary
    label_dict = load_label_dictionary()
    if not label_dict:
        print("No label dictionary found. Building from ZIPs...")
        label_dict = build_label_dictionary_from_zips(args.label_year)

    print(f"Loaded {len(label_dict)} standard labels")

    if args.company:
        count = process_company(args.company, years, label_dict, args.skip_existing)
        print(f"Processed {count} year(s) for company {args.company}")

    elif args.all:
        # Get all company codes from xbrl_store
        company_dirs = glob.glob(os.path.join(XBRL_STORE, "*"))
        codes = []
        for d in company_dirs:
            name = os.path.basename(d)
            if "_" in name:
                code = name.split("_")[0]
                if code.isdigit():
                    codes.append(code)

        codes.sort()
        print(f"Processing {len(codes)} companies for years {years}...")

        total = 0
        for i, code in enumerate(codes):
            count = process_company(code, years, label_dict, args.skip_existing)
            total += count
            if (i + 1) % 100 == 0:
                print(f"  Progress: {i+1}/{len(codes)}, generated: {total}")

        print(f"\nDone! Generated {total} statement files for {len(codes)} companies")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
