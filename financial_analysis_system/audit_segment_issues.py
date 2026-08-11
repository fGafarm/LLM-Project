#!/usr/bin/env python3
"""v10.4.9 セグメント問題監査 - バッチ完了企業の抽出ログをスキャン"""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_BASE = Path("./output_v10.2")
PROGRESS_FILE = "batch_225_progress.json"

# v10.4.9で追加したフィルタ（これらが business_segments に混入していたら問題）
GARBAGE_NAMES = ["売上高", "営業利益", "営業収益", "経常収益"]
GARBAGE_REGEX = re.compile(r'^[\s−\-—–\d,\.]+$')

# v10.4.9で追加したbusiness_keywords（これがないと誤分類される）
BUSINESS_KEYWORDS_NEW = ["グループ", "ホールディングス", "株式会社"]
GEOGRAPHIC_KEYWORDS = [
    "日本", "北米", "欧州", "アジア", "中国", "米国", "アメリカ",
    "中華", "韓国", "台湾", "東南アジア", "オセアニア", "中南米",
    "シンガポール", "オランダ", "マレーシア", "ロシア", "スイス",
    "イタリア", "スペイン", "インド", "タイ", "ベトナム", "フィリピン",
    "インドネシア", "ブラジル", "メキシコ", "カナダ", "英国",
    "イギリス", "フランス", "ドイツ", "豪州", "オーストラリア",
]

def audit_company(code, name):
    """1社分の抽出ログを監査。問題リストを返す"""
    issues = []
    ext_log = OUTPUT_BASE / f"{code}_{name}" / "extraction_logs" / "2024" / "05_セグメント_extraction.json"
    if not ext_log.exists():
        return issues

    with open(ext_log, 'r', encoding='utf-8') as f:
        data = json.load(f)

    extracted = data.get("extracted", {})
    bseg = extracted.get("business_segments", [])
    gseg = extracted.get("geographic_segments", [])
    all_seg = extracted.get("segments", [])

    # Issue 1: ゴースト名がbusiness_segmentsに混入
    for s in bseg:
        sname = s.get("name", "")
        if any(g in sname for g in GARBAGE_NAMES):
            issues.append(f"GHOST: business_segに「{sname}」混入 (rev={s.get('revenue')})")
        if GARBAGE_REGEX.match(sname.replace(" ", "")):
            issues.append(f"GARBAGE: business_segに「{sname}」混入")

    # Issue 2: geographic keywordを含むのにbusiness_segmentsにいる（逆方向は新キーワードで解決）
    # → これは既存の挙動なのでスキップ

    # Issue 3: business_keywordsがないためgeographicに誤分類されたセグメント
    for s in gseg:
        sname = s.get("name", "")
        # 新キーワードがあればbusinessになるべきだった
        has_new_biz_kw = any(kw in sname for kw in BUSINESS_KEYWORDS_NEW)
        has_geo_kw = any(kw in sname for kw in GEOGRAPHIC_KEYWORDS)
        if has_new_biz_kw and has_geo_kw:
            issues.append(f"MISCLASS: 「{sname}」がgeographicだがグループ/HD/株式会社を含む")

    # Issue 4: business_segmentsが空 or 1個しかない（全てgeographic扱い）
    if len(bseg) == 0 and len(all_seg) > 0:
        seg_names = [s.get("name", "") for s in all_seg]
        issues.append(f"NO_BIZ: business_segments空 (全{len(all_seg)}seg: {seg_names[:5]})")
    elif len(bseg) == 1 and bseg[0].get("name") == "その他" and len(all_seg) > 2:
        seg_names = [s.get("name", "") for s in all_seg]
        issues.append(f"ONLY_OTHER: businessが「その他」のみ (全{len(all_seg)}seg: {seg_names[:5]})")

    # Issue 5: number_check segment_summary の乖離チェック
    company_dir = OUTPUT_BASE / f"{code}_{name}"
    jsons = sorted(company_dir.glob(f"porta102_{code}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if jsons:
        try:
            with open(jsons[0], 'r', encoding='utf-8') as jf:
                jdata = json.load(jf)
            nc = jdata.get("number_check", {})
            seg_sum = nc.get("segment_summary", {})
            for metric, vals in seg_sum.items():
                diff_pct = abs(vals.get("diff_pct", 0))
                if diff_pct > 30:
                    seg_t = vals.get("segment_total", 0) / 1e8
                    co_t = vals.get("company_total", 0) / 1e8
                    issues.append(f"DEVIATION: {metric} seg={seg_t:,.0f}億 vs co={co_t:,.0f}億 ({diff_pct:.0f}%)")
        except Exception:
            pass

    return issues


def main():
    with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
        progress = json.load(f)

    completed = progress.get("completed", {})
    print(f"=== v10.4.9 セグメント監査 ({len(completed)}社) ===\n")

    problem_companies = []
    for code, info in sorted(completed.items()):
        name = info["name"]
        issues = audit_company(code, name)
        if issues:
            problem_companies.append((code, name, issues))
            print(f"❌ {code} {name}")
            for issue in issues:
                print(f"   {issue}")

    clean_count = len(completed) - len(problem_companies)
    print(f"\n=== 結果 ===")
    print(f"問題あり: {len(problem_companies)}社")
    print(f"問題なし: {clean_count}社")
    print(f"合計: {len(completed)}社")

    if problem_companies:
        print(f"\n=== 再実行対象 ===")
        for code, name, issues in problem_companies:
            issue_types = set(i.split(":")[0] for i in issues)
            print(f"  {code} {name} [{', '.join(issue_types)}]")


if __name__ == "__main__":
    main()
