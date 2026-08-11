"""
有報の「賃貸等不動産関係」注記から、簿価・時価・含み益を抽出する。

XBRL タグ: NotesRealEstateForLeaseEtcConsolidatedFinancialStatementsTextBlock
不動産業/商社/銀行/REIT等が「主要な設備の状況」では拾えない賃貸不動産の含み益を開示。

抽出対象:
- 連結貸借対照表計上額 (簿価): 連結会計年度末残高
- 連結会計年度末の時価
- 含み益 = 時価 - 簿価

出力: fixed_assets_store/{code}_{name}/2024_rental_real_estate.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
XBRL_STORE = PROJECT_ROOT / "financial_analysis_system" / "xbrl_store"
OUTPUT_ROOT = PROJECT_ROOT / "fixed_assets_store"

TARGET_TAGS = (
    # JGAAP「賃貸等不動産関係」注記
    "NotesRealEstateForLeaseEtcConsolidatedFinancialStatementsTextBlock",
    # IFRS「投資不動産」注記 (商社・通信・グローバル企業)
    "NotesInvestmentPropertyConsolidatedFinancialStatementsIFRSTextBlock",
)
# 後方互換 (deprecated)
TARGET_TAG = TARGET_TAGS[0]


def strip_html(html: str) -> str:
    """HTMLタグを除去して空白正規化したテキストに。"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("　", " ")
    return text.strip()


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(s.replace(",", "").replace("△", "-").replace("▲", "-"))
    except (ValueError, AttributeError):
        return None


def detect_unit_factor(text: str) -> tuple[float, str]:
    """テキストから単位を検出し、百万円換算係数を返す。
    Returns: (factor, label) e.g. (1.0, "百万円") or (0.001, "千円")
    """
    if "（単位：千円）" in text or "(単位：千円)" in text:
        return 0.001, "千円"
    if "（単位：百万円）" in text or "(単位：百万円)" in text or "（単位 百万円）" in text:
        return 1.0, "百万円"
    if "（単位：億円）" in text or "(単位：億円)" in text:
        return 100.0, "億円"
    # デフォルト: 百万円
    return 1.0, "百万円"


def _looks_like_year(n: int) -> bool:
    """数値が年に見えるか (1900-2100)"""
    return 1900 <= n <= 2100


def _looks_like_money_amount(nums: list[int]) -> bool:
    """数値リストが「金額の表」のように見えるか。
    最初の値が >= 10000 (=「億円」相当) かつ年/月/日のパターンでない。
    """
    if not nums or len(nums) < 1:
        return False
    if _looks_like_year(nums[0]):
        return False
    # 1未満の小さな値ばかりの場合 (「2023 4 1 2024」みたいな日付系) を除外
    if all(n < 100 for n in nums[1:3]) if len(nums) >= 3 else False:
        return False
    return nums[0] >= 1000


def _numbers_after(text: str, label: str, max_count: int = 4) -> list[int]:
    """labelの直後にある数値を最大max_count個取得。

    複数occurrenceがある場合、「金額の表」に見える occurrence を選択。
    年月日や説明文の中の occurrence は skip する。
    """
    fallback = []
    start = 0
    while True:
        idx = text.find(label, start)
        if idx == -1:
            break
        snippet = text[idx + len(label):idx + len(label) + 200]
        nums_str = re.findall(r"[△▲]?[\d,]+", snippet)
        candidate = []
        for s in nums_str[:max_count]:
            n = _parse_int(s)
            if n is not None:
                candidate.append(n)
        if _looks_like_money_amount(candidate):
            return candidate
        # この occurrence は説明文の可能性、次を探す
        start = idx + len(label)
        if candidate:
            fallback = candidate
    return fallback


def extract_amount_pairs(text: str) -> Optional[dict]:
    """テキストから (book_value, fair_value) ペアを抽出。

    主要パターン:
    1. 期末残高 [前期] [当期] ... 期末時価 [前期] [当期]   (三菱地所型・2列)
    2. 期末残高 [当期]  期末時価 [当期]                   (1列)
    3. 「末残高 XXX 末の時価 YYY」 (三井不動産型・テーブルレス)

    最新値 (当期) を返す。整合性チェック: 期首+増減≈期末。
    単位（千円/百万円/億円）を検出して百万円換算。
    """
    unit_factor, unit_label = detect_unit_factor(text)
    # Pattern 1/2: 「期末残高」or「帳簿価額」 + 「期末時価」or「公正価値」
    # JGAAP: 期末残高 + 期末時価
    # IFRS:  帳簿価額 + 公正価値 (NTT/商社)
    end_balance_nums = _numbers_after(text, "期末残高", 4)
    if not end_balance_nums:
        end_balance_nums = _numbers_after(text, "帳簿価額", 4)
    end_value_nums = _numbers_after(text, "期末時価", 4)
    if not end_value_nums:
        end_value_nums = _numbers_after(text, "公正価値", 4)
    if len(end_balance_nums) >= 1 and len(end_value_nums) >= 1:
        # 当期残高 = 末尾の値 (1列なら単独、2列なら2番目)
        # 表は通常 [前期, 当期] か [当期] 単独
        if len(end_balance_nums) >= 2 and len(end_value_nums) >= 2:
            book = end_balance_nums[1]
            fair = end_value_nums[1]
        else:
            book = end_balance_nums[0]
            fair = end_value_nums[0]
        # 整合性: book < fair * 5 程度 (異常な比率排除)
        if book > 0 and fair > 0 and book < fair * 5 and fair < book * 20:
            book_mil = book * unit_factor
            fair_mil = fair * unit_factor
            return {
                "book_value_mil": round(book_mil),
                "fair_value_mil": round(fair_mil),
                "hidden_gain_mil": round(fair_mil - book_mil),
                "method": "labeled_end_balance",
                "unit": unit_label,
            }

    # Pattern 3: 三井不動産型 - 「連結会計年度末残高」「連結会計年度末の時価」見出しの後の表
    # 期首残高 増減額 期末残高 末の時価 がこの順で並ぶ (1セクションあたり4数値)
    pattern_full = re.compile(
        r"連結会計年度末残高\s*連結会計年度末の時価\s+"
        r"([\d,]+)\s+([△▲]?[\d,]+)\s+([\d,]+)\s+([\d,]+)"
    )
    matches = list(pattern_full.finditer(text))
    if matches:
        # 最後のマッチが最新年度
        m = matches[-1]
        opening = _parse_int(m.group(1))
        change = _parse_int(m.group(2))
        ending = _parse_int(m.group(3))
        fair = _parse_int(m.group(4))
        if all(v is not None for v in (opening, change, ending, fair)):
            # 整合性: 期首+増減≈期末
            if abs((opening + change) - ending) / max(ending, 1) < 0.10:
                ending_mil = ending * unit_factor
                fair_mil = fair * unit_factor
                return {
                    "book_value_mil": round(ending_mil),
                    "fair_value_mil": round(fair_mil),
                    "hidden_gain_mil": round(fair_mil - ending_mil),
                    "method": "mitsui_pattern",
                    "unit": unit_label,
                }

    # Pattern 4: シンプル「末残高 X 末の時価 Y」
    m = re.search(r"末残高\s+([\d,]+)\s*[^\d]{0,30}末の時価\s*[^\d]{0,30}([\d,]+)", text)
    if m:
        book = _parse_int(m.group(1))
        fair = _parse_int(m.group(2))
        if book and fair and book > 0 and fair > 0:
            book_mil = book * unit_factor
            fair_mil = fair * unit_factor
            return {
                "book_value_mil": round(book_mil),
                "fair_value_mil": round(fair_mil),
                "hidden_gain_mil": round(fair_mil - book_mil),
                "method": "simple_pattern",
                "unit": unit_label,
            }

    return None


def process_one(raw_tags_path: Path) -> Optional[dict]:
    """1社1年分の raw_tags.json を読み、賃貸等不動産または投資不動産(IFRS)情報を抽出。"""
    with raw_tags_path.open(encoding="utf-8") as f:
        d = json.load(f)
    tags = d.get("tags") or {}
    # 試行: JGAAPタグ → IFRSタグ
    for tag_name in TARGET_TAGS:
        tag_data = tags.get(tag_name)
        if not tag_data:
            continue
        html = tag_data.get("value", "")
        if not html:
            continue
        text = strip_html(html)
        extracted = extract_amount_pairs(text)
        if extracted:
            extracted["source_tag"] = tag_name
            return {
                "company_code": d.get("company_code"),
                "company_name": d.get("company_name"),
                "fiscal_year": d.get("fiscal_year"),
                "rental_real_estate": extracted,
                "source_excerpt": text[:500],
            }
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    targets = list(XBRL_STORE.glob("*/2024_raw_tags.json"))
    print(f"Processing {len(targets):,} companies...")

    success = 0
    no_tag = 0
    parse_failed = 0
    sample_results = []

    for i, fp in enumerate(targets):
        try:
            result = process_one(fp)
        except Exception as e:
            parse_failed += 1
            continue
        if result is None:
            # Distinguish: tag missing vs parse failed
            with fp.open(encoding="utf-8") as f:
                d = json.load(f)
            tags = d.get("tags") or {}
            if any(t in tags for t in TARGET_TAGS):
                parse_failed += 1
            else:
                no_tag += 1
            continue
        success += 1
        # Save to fixed_assets_store
        code = result["company_code"]
        name = result["company_name"]
        out_dir = OUTPUT_ROOT / f"{code}_{name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "2024_rental_real_estate.json"
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        if len(sample_results) < 20:
            sample_results.append(result)
        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(targets)}] success={success} no_tag={no_tag} parse_failed={parse_failed}")

    print(f"\nDone: success={success}, no_tag={no_tag}, parse_failed={parse_failed}")
    print()
    print("=== Sample results ===")
    for r in sorted(sample_results, key=lambda x: -(x["rental_real_estate"]["hidden_gain_mil"]))[:15]:
        rre = r["rental_real_estate"]
        print(
            f"  {r['company_code']:6} {r['company_name'][:24]:24}  "
            f"book={rre['book_value_mil']:>12,}  "
            f"fair={rre['fair_value_mil']:>12,}  "
            f"gain={rre['hidden_gain_mil']:>12,}"
        )


if __name__ == "__main__":
    main()
