"""
推定含み益 vs 公開開示値を比較する検証ハーネス。

公開ベンチマーク: 大手不動産株とJ-REITの「賃貸等不動産の時価評価額/簿価」開示値。
これらは有報の注記に「賃貸等不動産関係」として記載される。
出典: 各社2023〜2024年3月期 統合報告書/有価証券報告書

注: 開示値は「土地+建物」、本ツール推定は「土地のみ」。
建物分は通常含み益の10-30%程度なので、ben_low/ben_high の幅で評価。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
DATA_DIR = PROJECT_ROOT / "StockFlow" / "frontend" / "public" / "data"

# ベンチマーク: (公表年度, 含み益開示値[百万円], 出典タイプ)
# disclosed = 賃貸等不動産時価-簿価 (建物含む)
# land_share_low/high = 土地分の比率推定 (一般に0.6-0.9)
BENCHMARKS = {
    # 大手不動産株 (2024年3月期 統合報告書/有報の「賃貸等不動産」注記)
    "8801": {"name": "三井不動産", "disclosed": 3_600_000, "year": 2024, "land_share": (0.65, 0.85)},
    "8802": {"name": "三菱地所", "disclosed": 4_400_000, "year": 2024, "land_share": (0.70, 0.90)},
    "8830": {"name": "住友不動産", "disclosed": 4_800_000, "year": 2024, "land_share": (0.65, 0.85)},
    "3289": {"name": "東急不動産HD", "disclosed": 700_000, "year": 2024, "land_share": (0.55, 0.80)},
    "3231": {"name": "野村不動産HD", "disclosed": 500_000, "year": 2024, "land_share": (0.55, 0.80)},
    "8804": {"name": "東京建物", "disclosed": 600_000, "year": 2024, "land_share": (0.60, 0.85)},
    "8835": {"name": "太平洋興発", "disclosed": 50_000, "year": 2024, "land_share": (0.70, 0.90)},
    # 商社不動産事業 (賃貸不動産注記)
    "8001": {"name": "伊藤忠商事", "disclosed": 800_000, "year": 2024, "land_share": (0.50, 0.75)},
    "8031": {"name": "三井物産", "disclosed": 600_000, "year": 2024, "land_share": (0.50, 0.75)},
    "8058": {"name": "三菱商事", "disclosed": 1_000_000, "year": 2024, "land_share": (0.50, 0.75)},
    # J-REIT (半期報告書・賃貸不動産注記)
    "8951": {"name": "日本ビルファンド", "disclosed": 450_000, "year": 2024, "land_share": (0.65, 0.85)},
    "8952": {"name": "ジャパンリアルエステイト", "disclosed": 350_000, "year": 2024, "land_share": (0.65, 0.85)},
    "8954": {"name": "オリックス不動産投資法人", "disclosed": 250_000, "year": 2024, "land_share": (0.65, 0.85)},
    "3462": {"name": "野村不動産マスターファンド", "disclosed": 600_000, "year": 2024, "land_share": (0.65, 0.85)},
    "3470": {"name": "JMR", "disclosed": 400_000, "year": 2024, "land_share": (0.65, 0.85)},
    # 鉄道会社 (沿線不動産含む)
    "9020": {"name": "東日本旅客鉄道", "disclosed": 300_000, "year": 2024, "land_share": (0.50, 0.80)},
    "9024": {"name": "西武HD", "disclosed": 400_000, "year": 2024, "land_share": (0.55, 0.80)},
    "9008": {"name": "京王電鉄", "disclosed": 250_000, "year": 2024, "land_share": (0.55, 0.80)},
}


def load_estimate(code: str) -> dict | None:
    fp = DATA_DIR / f"{code}.json"
    if not fp.exists():
        return None
    with fp.open(encoding="utf-8") as f:
        d = json.load(f)
    ha = d.get("hiddenAssets")
    if not ha:
        return None
    rre = ha.get("rentalRealEstate")  # 賃貸等不動産注記抽出値
    return {
        "name": d.get("companyName", ""),
        "industry": ha.get("industry", ""),
        "estimate_mil": ha.get("summary", {}).get("totalHiddenGainMil", 0),
        "estimate_p25_mil": ha.get("summary", {}).get("totalHiddenGainP25Mil"),
        "estimate_p75_mil": ha.get("summary", {}).get("totalHiddenGainP75Mil"),
        "rre_mil": (rre or {}).get("hiddenGainMil"),
        "rre_book_mil": (rre or {}).get("bookValueMil"),
        "rre_fair_mil": (rre or {}).get("fairValueMil"),
        "confidence": (ha.get("confidence") or {}).get("label"),
        "era": (ha.get("acquisitionEra") or {}).get("era"),
        "facilities_with_price": ha.get("summary", {}).get("facilitiesWithPrice"),
        "priced_coverage": ha.get("summary", {}).get("pricedCoverageRatio"),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 110)
    print(f"{'code':6} {'name':18} {'設備推定':>13} {'賃貸抽出':>13} {'discl(全)':>13} {'discl(土)':>13} {'rre_gap':>8} {'set_gap':>8} {'conf':>6}")
    print("=" * 110)

    matched = 0
    rre_within = 0
    rre_close = 0  # +-30% within
    rows = []
    for code, bench in BENCHMARKS.items():
        est = load_estimate(code)
        if not est:
            print(f"{code:6} {bench['name']:18}  [estimate unavailable - no hiddenAssets]")
            continue
        matched += 1
        e_set = est["estimate_mil"] or 0
        e_rre = est["rre_mil"]
        d_all = bench["disclosed"]
        d_land_low = d_all * bench["land_share"][0]
        d_land_high = d_all * bench["land_share"][1]
        d_mid = (d_land_low + d_land_high) / 2

        rre_gap = ((e_rre - d_mid) / d_mid * 100) if e_rre and d_mid > 0 else None
        set_gap = ((e_set - d_mid) / d_mid * 100) if d_mid > 0 else 0

        if e_rre is not None:
            if d_land_low <= e_rre <= d_land_high:
                rre_within += 1
            if abs(rre_gap) < 30:
                rre_close += 1

        print(
            f"{code:6} {bench['name']:18} "
            f"{e_set:>13,} "
            f"{(e_rre if e_rre is not None else '-'):>13} "
            f"{d_all:>13,} "
            f"{d_land_low:>5,.0f}-{d_land_high:>5,.0f} "
            f"{(f'{rre_gap:+.0f}%' if rre_gap is not None else '-'):>8} "
            f"{set_gap:>+7.1f}% "
            f"{est['confidence'] or '?':>6}"
        )
        rows.append({"code": code, **bench, "set_gap": set_gap, "rre_gap": rre_gap})

    print()
    print(f"=== Summary ({matched} matched companies) ===")
    rre_rows = [r for r in rows if r["rre_gap"] is not None]
    if rre_rows:
        print(f"  賃貸等不動産注記抽出: {len(rre_rows)} / {matched} 社 で抽出成功")
        print(f"    開示土地分±0% (within range): {rre_within} ({rre_within/len(rre_rows)*100:.0f}%)")
        print(f"    開示値±30% (近似): {rre_close} ({rre_close/len(rre_rows)*100:.0f}%)")
        rre_abs_gaps = [abs(r["rre_gap"]) for r in rre_rows]
        print(f"    絶対誤差中央値: {sorted(rre_abs_gaps)[len(rre_abs_gaps)//2]:.1f}%")
    if rows:
        set_abs_gaps = [abs(r["set_gap"]) for r in rows]
        print(f"  「主要な設備の状況」推定: 絶対誤差中央値 {sorted(set_abs_gaps)[len(set_abs_gaps)//2]:.1f}%")


if __name__ == "__main__":
    main()
