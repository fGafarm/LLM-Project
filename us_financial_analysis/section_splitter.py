"""
10-K Section Splitter

SEC 10-K filing テキストを Item 境界で分割する。
日本側の Yuho_splitter_v4 (01_会社概要〜07_その他) に相当。

10-K Item構造:
  Part I:
    Item 1.   Business                    → 01_business (≈ 01_会社概要)
    Item 1A.  Risk Factors                → 02_risk_factors (≈ 02_経営戦略_リスク)
    Item 1B.  Unresolved Staff Comments
    Item 1C.  Cybersecurity
    Item 2.   Properties
    Item 3.   Legal Proceedings
    Item 4.   Mine Safety Disclosures
  Part II:
    Item 5.   Market for Common Equity
    Item 6.   [Reserved]
    Item 7.   MD&A                        → 03_mda (≈ 03_MDA) ★最重要
    Item 7A.  Market Risk Disclosures
    Item 8.   Financial Statements        → 04_financial_statements (≈ 04_財務三表)
    Item 9.   Accountant Disagreements
    Item 9A.  Controls and Procedures
  Part III:
    Item 10-14. Governance/Compensation   → 05_governance (≈ 06_ガバナンス)
  Part IV:
    Item 15.  Exhibits
    Item 16.  Summary

Usage:
    from section_splitter import split_10k_sections
    sections = split_10k_sections(text)
    # sections["03_mda"] → MD&A テキスト
"""

import re


# Item番号 → 内部セクションID のマッピング
# 日本側: 01_会社概要, 02_経営戦略_リスク, 03_MDA, 04_財務三表, 05_セグメント, 06_ガバナンス
ITEM_SECTION_MAP = {
    "1":   "01_business",
    "1A":  "02_risk_factors",
    "1B":  "02b_unresolved_comments",
    "1C":  "02c_cybersecurity",
    "2":   "02d_properties",
    "3":   "02e_legal",
    "4":   "02f_mine_safety",
    "5":   "05_market_equity",
    "6":   "05b_reserved",
    "7":   "03_mda",            # ★最重要: 日本の03_MDAに相当
    "7A":  "03a_market_risk",
    "8":   "04_financial_statements",
    "9":   "09_accountant",
    "9A":  "09a_controls",
    "9B":  "09b_other",
    "9C":  "09c_foreign",
    "10":  "06_governance",
    "11":  "06a_compensation",
    "12":  "06b_ownership",
    "13":  "06c_relationships",
    "14":  "06d_accountant_fees",
    "15":  "07_exhibits",
    "16":  "07a_summary",
}

# セクション処理優先度 (低い=高優先度)
# 日本側のSECTION_PRIORITYと同じ思想
SECTION_PRIORITY = {
    "03_mda":                 1,   # 最重要: 業績分析
    "02_risk_factors":        2,   # リスク要因
    "01_business":            3,   # 事業概要
    "03a_market_risk":        4,   # 市場リスク
    "04_financial_statements": 5,  # 財務三表（XBRL代用可）
    "06_governance":          6,
}

# XBRL メタデータゴミ除去パターン
# HTMLパーサーが取りきれなかったXBRLタグデータ
XBRL_GARBAGE_PATTERNS = [
    # CIK-date エントリの羅列 (例: 0000320193us-gaap:CommonStockMember2024-09-29...)
    re.compile(r'\d{10}[a-z\-:]+\d{4}-\d{2}-\d{2}'),
    # namespace URI の羅列 (http://fasb.org/...)
    re.compile(r'http://[a-z]+\.org/[a-z\-/]+#[A-Za-z]+'),
    # iso4217:USD, xbrli:shares 等
    re.compile(r'(?:iso4217|xbrli|dei|srt):[A-Za-z]+'),
]

# Item ヘッダーの正規表現
# 日本側で「実物見てからパターン作る」で学んだ教訓:
# 1つのパターンじゃ全社カバーできない → 複数パターン + fallback
ITEM_HEADER_PATTERNS = [
    # 日本側の教訓: 1パターンじゃ全社カバーできない
    # AAPL, AMZN, META, PG, JPM, NVDA等の実物確認から作成
    # 共通: 先頭の空白を許容 (^\s*), タイトルに . や ' を許容

    # Pattern A: "Item 7.    Management's Discussion..." (AAPL/MSFT形式, 多スペース)
    re.compile(
        r'^\s*(?:ITEM|Item)\s+(\d+[A-Z]?)\.?\s{2,}(.+?)$',
        re.MULTILINE
    ),
    # Pattern B: "Item 7. Management's Discussion..." (スペース1個, AMZN/NVDA形式)
    re.compile(
        r'^\s*(?:ITEM|Item)\s+(\d+[A-Z]?)\.\s([A-Z][A-Za-z\s,\.\'\-\(\)\[\]&/;:]+)$',
        re.MULTILINE
    ),
    # Pattern C: "Item 7.Management's Discussion..." (スペース0個, META形式)
    re.compile(
        r'^\s*(?:ITEM|Item)\s+(\d+[A-Z]?)\.([A-Z][A-Za-z\s,\.\'\-\(\)\[\]&/;:]+)$',
        re.MULTILINE
    ),
    # Pattern D: "ITEM 7. MANAGEMENT'S DISCUSSION..." (全大文字形式)
    re.compile(
        r'^\s*(?:ITEM)\s+(\d+[A-Z]?)\.?\s+([A-Z][A-Z\s,\.\'\-\(\)\[\]&/;:]+)$',
        re.MULTILINE
    ),
    # Pattern E: "Item 7 — Management's Discussion..." (em-dash形式)
    re.compile(
        r'^\s*(?:ITEM|Item)\s+(\d+[A-Z]?)\s*[—–\-]\s*(.+?)$',
        re.MULTILINE
    ),
    # Pattern F: "Item 7: Management's Discussion..." (コロン形式, MSI等)
    re.compile(
        r'^\s*(?:ITEM|Item)\s+(\d+[A-Z]?):\s*(.+?)$',
        re.MULTILINE
    ),
]


def clean_xbrl_garbage(text: str) -> str:
    """
    テキスト先頭のXBRLメタデータゴミを除去する。

    日本側の教訓: HTMLパーサーが完璧にフィルタできない場合がある。
    AAPL 10-Kの1行目に数万文字のXBRL生データが混入していた。
    """
    lines = text.split('\n')
    clean_lines = []
    in_garbage_zone = True

    for line in lines:
        stripped = line.strip()

        if in_garbage_zone:
            # XBRLゴミ行の特徴: 非常に長い(1000文字+)かつXBRLパターンにマッチ
            if len(stripped) > 500 and any(p.search(stripped) for p in XBRL_GARBAGE_PATTERNS):
                continue
            # "false" or "true" で始まるXBRL boolean行
            if stripped.startswith(('false', 'true')) and len(stripped) > 100:
                continue
            # 空行は保持
            if not stripped:
                clean_lines.append(line)
                continue
            # 意味のあるテキストが出たらゴミゾーン終了
            in_garbage_zone = False

        clean_lines.append(line)

    return '\n'.join(clean_lines)


def find_toc_end(text: str) -> int:
    """
    目次(Table of Contents)の終了位置を検出する。

    10-Kの構造:
    1. 表紙 (Cover page)
    2. 目次 (Table of Contents) ← Itemが羅列されるがこれはスキップ
    3. 本文 ← ここからのItemが本物

    日本側の教訓: 目次のItem参照と本文のItem参照を区別しないと
    セクション分割が壊れる。
    """
    # 目次マーカーを探す
    toc_markers = [
        "TABLE OF CONTENTS",
        "Table of Contents",
        "INDEX",
    ]

    toc_start = -1
    text_lower = text.lower()
    for marker in toc_markers:
        pos = text_lower.find(marker.lower())
        if pos >= 0:
            toc_start = pos
            break

    if toc_start < 0:
        # 目次マーカーがない場合、最初のItem本文を直接探す
        return 0

    # 目次の後、"Part I" or 最初の実Item本文を探す
    # 目次のItemは短い行（タイトルのみ）、本文のItemは長いテキストが続く
    part_pattern = re.compile(r'^\s*PART\s+I\s*$', re.MULTILINE | re.IGNORECASE)
    match = part_pattern.search(text, toc_start + 100)
    if match:
        # PART I の2回目の出現を探す（1回目は目次内）
        second = part_pattern.search(text, match.end() + 10)
        if second:
            return second.start()
        return match.start()

    # fallback: 目次から500行後を本文開始とみなす
    lines = text[:toc_start + 5000].count('\n')
    return toc_start + 5000


def _find_item_boundaries(text: str, start_pos: int) -> list[dict]:
    """
    テキスト内のItem境界を検出する。

    Returns: [{item_num: "7", title: "MD&A...", pos: 12345}, ...]
    """
    boundaries = []
    seen_items = set()

    for pattern in ITEM_HEADER_PATTERNS:
        for match in pattern.finditer(text, start_pos):
            item_num = match.group(1).upper()
            title = match.group(2).strip() if match.lastindex >= 2 else ""
            pos = match.start()

            # 同じItemが複数回出る場合、最初のものを採用
            # (目次のは start_pos でスキップ済み)
            if item_num not in seen_items:
                seen_items.add(item_num)
                boundaries.append({
                    "item_num": item_num,
                    "title": title,
                    "pos": pos,
                })

    # 位置順にソート
    boundaries.sort(key=lambda x: x["pos"])

    return boundaries


def _keyword_fallback_split(text: str) -> dict[str, str]:
    """
    Item ヘッダーが見つからない場合のキーワードベースフォールバック。

    MCD等、Itemヘッダーを使わない独自構造の10-Kに対応。
    セクション見出し（行頭の大文字ヘッダー）を検出して分割。
    """
    sections = {}

    # 行頭のセクション見出しパターン
    # TOCのページ番号参照（"Management's Discussion... 7\n"）と本文見出しを区別
    KEYWORD_SECTIONS = [
        (r"Management'?s\s+Discussion\s+and\s+Analysis", "03_mda"),
        (r"Risk\s+Factors\b", "02_risk_factors"),
        (r"(?:Description\s+of\s+Business|Business\s+Overview|About\s+\w+)", "01_business"),
        (r"Financial\s+Statements\s+and\s+Supplementary", "04_financial_statements"),
    ]

    found = []
    for kw_pattern, section_id in KEYWORD_SECTIONS:
        pattern = re.compile(kw_pattern, re.IGNORECASE)
        best = None
        for match in pattern.finditer(text):
            # TOC行を除外: マッチ後の同一行にページ番号のみ → TOC参照
            line_end = text.find('\n', match.end(), match.end() + 200)
            if line_end < 0:
                line_end = len(text)
            after_match = text[match.end():line_end].strip()
            # "Page 3" or just "3" or "7" at line end → TOC reference
            if re.match(r'^[\s\.\-]*(?:Page\s*)?\d{1,3}\s*$', after_match):
                continue

            # 本文見出し候補
            if best is None:
                best = match
            # 大文字見出しを優先
            elif match.group(0).strip() == match.group(0).strip().upper():
                best = match
                break

        if best:
            line_start = text.rfind('\n', max(0, best.start() - 5), best.start())
            pos = line_start + 1 if line_start >= 0 else best.start()
            found.append({"section_id": section_id, "pos": pos})

    if not found:
        return {"00_full_text": text}

    found.sort(key=lambda x: x["pos"])

    for i, entry in enumerate(found):
        start = entry["pos"]
        end = found[i + 1]["pos"] if i + 1 < len(found) else len(text)
        section_text = text[start:end].strip()
        if len(section_text) > 200:
            sections[entry["section_id"]] = section_text

    return sections


def split_10k_sections(text: str) -> dict[str, str]:
    """
    10-Kテキストを Item セクションに分割する。

    Returns:
        dict: {section_id: section_text}
        例: {"01_business": "...", "03_mda": "...", ...}

    日本側の Yuho_splitter_v4 に相当。
    日本側の教訓を踏まえた設計:
    1. 目次と本文のItem参照を区別する
    2. 複数の正規表現パターンで幅広くカバー
    3. ゴミデータ（XBRL生データ等）を除去
    4. セクションが見つからなくても fallback を用意
    """
    # Step 0: \xa0 (non-breaking space) → 通常スペースに正規化
    text = text.replace('\xa0', ' ')

    # Step 1: XBRLゴミ除去
    text = clean_xbrl_garbage(text)

    # Step 2: 目次終了位置の検出
    toc_end = find_toc_end(text)

    # Safety: toc_end がファイルの30%を超えたらリセット
    max_toc = len(text) * 3 // 10
    if toc_end > max_toc:
        toc_end = 0

    # Step 3: Item境界の検出
    boundaries = _find_item_boundaries(text, toc_end)

    # TOC内のItem参照を除外: 連続Itemが1500文字以内に密集→TOCとみなす
    if boundaries and len(boundaries) >= 5:
        span = boundaries[-1]["pos"] - boundaries[0]["pos"]
        if span < 1500:
            # 全Itemが1500文字圏内 = これはTOC → 本文Itemなし
            boundaries = []

    if not boundaries:
        # Item分割できなかった場合: キーワードフォールバック → 全テキスト
        fallback = _keyword_fallback_split(text)
        if len(fallback) > 1 or "00_full_text" not in fallback:
            return fallback
        return {"00_full_text": text}

    # Step 4: セクション分割
    sections = {}
    for i, boundary in enumerate(boundaries):
        item_num = boundary["item_num"]
        section_id = ITEM_SECTION_MAP.get(item_num, f"item_{item_num}")

        start = boundary["pos"]
        end = boundaries[i + 1]["pos"] if i + 1 < len(boundaries) else len(text)

        section_text = text[start:end].strip()

        # 空セクションはスキップ
        if len(section_text) < 50:
            continue

        sections[section_id] = section_text

    # Step 5: 表紙セクション（Item 1の前）
    if boundaries:
        cover_text = text[toc_end:boundaries[0]["pos"]].strip()
        if len(cover_text) > 100:
            sections["00_cover"] = cover_text

    return sections


def get_section_stats(sections: dict[str, str]) -> dict:
    """セクション分割の統計情報を返す。"""
    stats = {}
    for section_id, text in sections.items():
        stats[section_id] = {
            "chars": len(text),
            "lines": text.count('\n') + 1,
        }
    return stats


# セクション別QA質問セット（構造化形式）
# 日本版: 各質問に question_id + focus を持ち、回答ルール・禁止事項・フォーマット例を含む
# 日本側の教訓: 汎用プロンプト1発だとLLMが嘘つく → セクション別に具体的質問を投げる
SECTION_QUESTIONS = {
    "03_mda": [
        {
            "id": "revenue_drivers",
            "focus": "revenue",
            "question": "What was the total revenue for the fiscal year? How did it change year-over-year (amount and percentage)? What were the main drivers of revenue growth or decline? Break down by segment or product line if available.",
        },
        {
            "id": "segment_revenue",
            "focus": "segments",
            "question": "What were the revenue figures for each business segment or product category? List each segment with its revenue amount.",
        },
        {
            "id": "gross_margin",
            "focus": "profitability",
            "question": "What was the gross profit and gross margin? How did cost of sales/cost of revenue change and why?",
        },
        {
            "id": "operating_profit",
            "focus": "profitability",
            "question": "What was the operating income and operating margin? What factors affected operating profitability?",
        },
        {
            "id": "cost_structure",
            "focus": "costs",
            "question": "How did SG&A expenses and R&D expenses change year-over-year? What drove those changes?",
        },
        {
            "id": "net_income",
            "focus": "profitability",
            "question": "What was net income and how did it compare to the prior year? What were the main items below operating income (interest, tax, other)?",
        },
        {
            "id": "cash_flow",
            "focus": "cashflow",
            "question": "What was operating cash flow? How did it compare to net income? What were the major working capital changes?",
        },
        {
            "id": "capex_investment",
            "focus": "investment",
            "question": "What were the major investing activities? How much was CapEx? Any significant acquisitions or divestitures?",
        },
        {
            "id": "shareholder_return",
            "focus": "shareholder",
            "question": "How much was returned to shareholders via dividends and share repurchases during the fiscal year?",
        },
        {
            "id": "debt_level",
            "focus": "balance_sheet",
            "question": "What was the total debt level and how did it change? What is the maturity profile?",
        },
        {
            "id": "liquidity",
            "focus": "balance_sheet",
            "question": "What is the company's liquidity position? Cash and equivalents, credit facilities, and any constraints?",
        },
        {
            "id": "guidance",
            "focus": "outlook",
            "question": "What forward-looking statements or guidance did management provide for the next fiscal year?",
        },
        {
            "id": "key_trends",
            "focus": "outlook",
            "question": "What are the key trends or factors management expects to impact future results?",
        },
    ],
    "02_risk_factors": [
        {
            "id": "top_risks",
            "focus": "risks",
            "question": "What are the top 5 most significant risk factors? Summarize each in 1-2 sentences.",
        },
        {
            "id": "new_risks",
            "focus": "risks",
            "question": "Are there any NEW risk factors added this year? What changed in the risk landscape?",
        },
        {
            "id": "quantified_risks",
            "focus": "risks",
            "question": "What specific risks are quantified with dollar amounts or probabilities?",
        },
        {
            "id": "regulatory_risks",
            "focus": "risks",
            "question": "What regulatory, legal, or geopolitical risks are highlighted?",
        },
        {
            "id": "competitive_risks",
            "focus": "risks",
            "question": "What risks relate to competitive position, technology disruption, or market conditions?",
        },
    ],
    "01_business": [
        {
            "id": "business_model",
            "focus": "overview",
            "question": "What is the company's primary business and revenue model? How does it make money?",
        },
        {
            "id": "products_segments",
            "focus": "overview",
            "question": "What are the main products, services, or business segments? List them with brief descriptions.",
        },
        {
            "id": "customers",
            "focus": "market",
            "question": "Who are the key customers? Is there significant customer concentration?",
        },
        {
            "id": "competition",
            "focus": "market",
            "question": "What is the competitive landscape? Who are the main competitors mentioned?",
        },
        {
            "id": "employees",
            "focus": "workforce",
            "question": "How many employees does the company have? Any significant workforce changes?",
        },
        {
            "id": "competitive_advantage",
            "focus": "strategy",
            "question": "What are the company's key competitive advantages, intellectual property, or barriers to entry?",
        },
    ],
    "04_financial_statements": [
        {
            "id": "accounting_policies",
            "focus": "notes",
            "question": "What are the key accounting policies or significant estimates? Any changes in accounting methods?",
        },
        {
            "id": "segment_notes",
            "focus": "segments",
            "question": "What segment information is disclosed? Revenue and operating income by segment?",
        },
        {
            "id": "acquisitions",
            "focus": "transactions",
            "question": "What are the details of any significant acquisitions, divestitures, or restructuring?",
        },
        {
            "id": "geographic_revenue",
            "focus": "geographic",
            "question": "What is the breakdown of revenue or assets by geographic region?",
        },
        {
            "id": "debt_details",
            "focus": "debt",
            "question": "What are the details of long-term debt (amounts, maturity schedule, interest rates)?",
        },
        {
            "id": "contingencies",
            "focus": "legal",
            "question": "Are there any significant contingent liabilities, legal proceedings, or off-balance sheet items?",
        },
    ],
}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Usage: python section_splitter.py <10k_text_file>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    sections = split_10k_sections(text)
    stats = get_section_stats(sections)

    print(f"Input: {filepath} ({len(text):,} chars)")
    print(f"Sections found: {len(sections)}")
    print()
    for section_id in sorted(stats.keys()):
        s = stats[section_id]
        has_qa = "★" if section_id in SECTION_QUESTIONS else ""
        print(f"  {section_id:<30} {s['chars']:>8,} chars  {s['lines']:>5} lines  {has_qa}")
