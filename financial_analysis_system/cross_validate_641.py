"""
641社の「賃貸等不動産関係」注記抽出値 vs 「主要な設備の状況」推定値 の
クロス検証。業種別の gap を分析し、systematic bias を検出する。

注意: 両者は完全には同じ概念ではない:
- 賃貸等不動産: 賃貸用 (オフィスビル賃貸, 商業施設賃貸 等)
- 主要な設備: 自社使用 (工場, 本社, 物流センター 等)

ただし:
- 不動産業/REIT/銀行/商社: 賃貸等が大部分 → 賃貸抽出 ≈ 真の含み益
- 製造業: 自社使用が大部分 → 主要な設備推定 ≈ 真の含み益、賃貸抽出は「副次保有」
- 小売/外食/サービス: ハイブリッド (店舗の一部は賃貸提供)

業種別に「どちらが信頼できるか」を可視化。
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
DATA_DIR = PROJECT_ROOT / "StockFlow" / "frontend" / "public" / "data"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    matched = []
    for fp in DATA_DIR.glob("*.json"):
        if fp.name in ("index.json", "metrics_summary.json"):
            continue
        try:
            with fp.open(encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        ha = d.get("hiddenAssets")
        if not ha:
            continue
        rre = ha.get("rentalRealEstate")
        if not rre or rre.get("hiddenGainMil") is None:
            continue
        est_set = (ha.get("summary") or {}).get("totalHiddenGainMil") or 0
        rre_gain = rre["hiddenGainMil"]
        rre_book = rre.get("bookValueMil") or 0
        rre_fair = rre.get("fairValueMil") or 0
        industry = ha.get("industry") or "unknown"
        matched.append({
            "code": d.get("ticker"),
            "name": d.get("companyName") or "",
            "industry": industry,
            "set_estimate": est_set,
            "rre_gain": rre_gain,
            "rre_book": rre_book,
            "rre_fair": rre_fair,
            "set_book": (ha.get("summary") or {}).get("totalLandBookValueMil") or 0,
        })

    print(f"Matched companies (with both 設備推定 + 賃貸抽出): {len(matched):,}")
    print()

    # 業種別 gap 分析
    by_industry: dict[str, list[dict]] = defaultdict(list)
    for r in matched:
        by_industry[r["industry"]].append(r)

    print(f"=== 業種別: 設備推定 vs 賃貸抽出 (gap = (set - rre) / rre × 100) ===")
    print(f"{'業種':22} {'n':>5} {'設備推定中央':>12} {'賃貸抽出中央':>12} {'gap中央':>10} {'設備>賃貸の社%':>14}")
    print("-" * 90)
    industry_summary = []
    for ind, rows in sorted(by_industry.items(), key=lambda x: -len(x[1])):
        if len(rows) < 5:
            continue
        set_vals = [r["set_estimate"] for r in rows]
        rre_vals = [r["rre_gain"] for r in rows]
        gaps = []
        set_higher = 0
        for r in rows:
            if r["rre_gain"] != 0:
                gap_pct = (r["set_estimate"] - r["rre_gain"]) / abs(r["rre_gain"]) * 100
                gaps.append(gap_pct)
            if r["set_estimate"] > r["rre_gain"]:
                set_higher += 1
        med_set = statistics.median(set_vals)
        med_rre = statistics.median(rre_vals)
        med_gap = statistics.median(gaps) if gaps else 0
        pct_set_higher = set_higher / len(rows) * 100
        industry_summary.append({
            "industry": ind, "n": len(rows),
            "med_set": med_set, "med_rre": med_rre,
            "med_gap": med_gap, "pct_set_higher": pct_set_higher,
        })
        print(
            f"  {ind[:22]:22} {len(rows):>5} "
            f"{med_set:>12,.0f} {med_rre:>12,.0f} "
            f"{med_gap:>+9.1f}% {pct_set_higher:>13.0f}%"
        )

    print()
    print("=== 解釈ガイド ===")
    print("  gap > 0%: 設備推定が賃貸抽出より大きい → 自社使用主体 (製造業・運輸 等)")
    print("  gap < 0%: 賃貸抽出が大きい → 賃貸不動産が主体 (不動産業・REIT 等)")
    print("  gap ≈ 0%: 両者がほぼ同規模 → ハイブリッド型")
    print()

    # 既知ベンチマーク銘柄の検証 (3桁業種コード基準ではない)
    print("=== 主要銘柄の数字 ===")
    targets = ["8801", "8802", "8830", "3289", "9020", "9022", "8306", "8316", "9432", "9433", "9434", "8267", "3382", "9041", "9042", "8001", "8031", "8058"]
    for code in targets:
        row = next((r for r in matched if r["code"] == code), None)
        if row:
            print(f"  {code} {row['name'][:20]:20} ({row['industry']:10}) "
                  f"設備={row['set_estimate']:>10,}  賃貸={row['rre_gain']:>10,}  比率={row['set_estimate']/max(row['rre_gain'],1):.2f}")
        else:
            print(f"  {code} - 賃貸注記なし or 抽出失敗")

    # 全体統計
    print()
    print(f"=== 全体 ({len(matched)}社) ===")
    all_set = sum(r["set_estimate"] for r in matched) / 1_000_000
    all_rre = sum(r["rre_gain"] for r in matched) / 1_000_000
    print(f"  設備推定 合計: {all_set:>10,.1f} 兆円")
    print(f"  賃貸抽出 合計: {all_rre:>10,.1f} 兆円")
    print(f"  両者の合計 (重複あり可能性): {all_set + all_rre:>10,.1f} 兆円")


if __name__ == "__main__":
    main()
