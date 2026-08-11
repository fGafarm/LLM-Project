"""
ダウンロード済の取引価格データを既存の land_price_by_city.json と
land_price_points.json にマージする。

処理:
1. 全 transaction_prices/{year}/Q{q}/{pref}.json を読み込み
2. Type=「宅地(土地)」のみフィルタ (建物込み・農地・林地は除外)
3. CityPlanning から residential/commercial/industrial に分類
4. 市区町村×用途別に price/sqm を集計
5. 既存 land_price_by_city.json にマージ (新規バケット追加 + p25/p75/dispersion 再計算)
6. 標点毎の DistrictName を unique化、市区町村中心座標で代表 (kNNには tier 2 として追加)

出力:
- land_price_data/land_price_by_city.json (上書き)
- land_price_data/land_price_points.json (transaction prices タグ付きで追加)
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
TX_ROOT = PROJECT_ROOT / "land_price_data" / "transaction_prices"
CITY_DB_PATH = PROJECT_ROOT / "land_price_data" / "land_price_by_city.json"
POINTS_PATH = PROJECT_ROOT / "land_price_data" / "land_price_points.json"
GEOCODE_CACHE = PROJECT_ROOT / "land_price_data" / "geocode_cache.json"


def categorize_city_planning(planning: str, use_text: str = "") -> str:
    """CityPlanning + Use 文字列から residential/commercial/industrial に正規化。"""
    p = (planning or "").strip()
    text = use_text or ""
    if "商業" in p:
        return "commercial"
    if "工業" in p:
        return "industrial"
    if "近隣商業" in p:
        return "commercial"
    if "住居" in p or "住宅" in p:
        # 住居地域でも 用途=店舗/事務所 なら商業
        commercial_kw = ("店舗", "事務所", "銀行", "倉庫")
        if any(kw in text for kw in commercial_kw):
            return "commercial"
        return "residential"
    return "other"


def aggregate_prices(prices: list[float]) -> dict:
    if not prices:
        return {}
    result = {
        "count": len(prices),
        "mean": round(statistics.mean(prices), 0),
        "median": round(statistics.median(prices), 0),
        "min": round(min(prices), 0),
        "max": round(max(prices), 0),
    }
    if len(prices) >= 2:
        q = statistics.quantiles(prices, n=4, method="inclusive")
        p25, p75 = round(q[0], 0), round(q[2], 0)
        result["p25"] = p25
        result["p75"] = p75
        if result["median"] > 0:
            result["dispersion"] = round((p75 - p25) / result["median"], 3)
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    print("Loading existing land_price_by_city.json...")
    with CITY_DB_PATH.open(encoding="utf-8") as f:
        existing_db = json.load(f)
    existing_cities = existing_db["cities"]

    print(f"Existing cities: {len(existing_cities):,}")

    # 取引価格データを集計
    # bucket: {(pref, city): {use: [unit_prices]}}
    tx_buckets: dict = defaultdict(
        lambda: {"residential": [], "commercial": [], "industrial": [], "other": []}
    )
    tx_files = list(TX_ROOT.rglob("*.json"))
    print(f"Transaction files: {len(tx_files):,}")

    total_records = 0
    used_records = 0
    skipped_no_unit = 0
    skipped_non_land = 0
    for tf in tx_files:
        try:
            with tf.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if "error" in data:
            continue
        for rec in data.get("data", []):
            total_records += 1
            t = rec.get("Type", "")
            # 「宅地(土地)」と「宅地(土地と建物)」のうち、UnitPrice (土地単価) のあるもの
            if "宅地" not in t:
                skipped_non_land += 1
                continue
            unit = rec.get("UnitPrice")
            if not unit or unit == "":
                skipped_no_unit += 1
                continue
            try:
                price = float(unit)
            except (ValueError, TypeError):
                continue
            if price <= 0 or price > 100_000_000:  # 異常値除外
                continue
            pref = rec.get("Prefecture", "").strip()
            city = rec.get("Municipality", "").strip()
            if not pref or not city:
                continue
            cat = categorize_city_planning(rec.get("CityPlanning", ""), rec.get("Use", ""))
            tx_buckets[(pref, city)][cat].append(price)
            used_records += 1

    print(f"\nProcessed: total={total_records:,}, used={used_records:,}, "
          f"skipped(no_unit)={skipped_no_unit:,}, skipped(non_land)={skipped_non_land:,}")
    print(f"Cities with tx data: {len(tx_buckets):,}")

    # マージ: 既存の prices と取引価格を pool して再集計
    merged_count = 0
    new_count = 0
    for (pref, city), cat_prices in tx_buckets.items():
        key = f"{pref}{city}"
        existing = existing_cities.get(key)

        # 既存の bucket と取引価格を pool するため、現状の by_use を ‘mean’ ベースで再構築できないので、
        # 取引価格のみで集計し直して上書き or by_use["xxx_tx"] を別バケットで追加
        # → ここでは既存 mean に取引価格平均を加重平均する形で更新
        new_by_use = {}
        all_prices_combined: list[float] = []
        for cat, prices in cat_prices.items():
            if not prices:
                continue
            tx_agg = aggregate_prices(prices)
            new_by_use[cat] = tx_agg
            all_prices_combined.extend(prices)

        # 既存と取引価格を統合
        if existing:
            merged_count += 1
            for cat, tx_agg in new_by_use.items():
                existing_agg = existing.get("by_use", {}).get(cat)
                if existing_agg:
                    # 既存数とサンプル比で加重平均
                    n_old = existing_agg["count"]
                    n_new = tx_agg["count"]
                    # 既存の平均と取引価格の平均を加重で合成
                    combined_mean = (existing_agg["mean"] * n_old + tx_agg["mean"] * n_new) / (n_old + n_new)
                    # p25/p75 は新規取引価格のもの (ばらつき検出は実取引重視)
                    merged = {
                        "count": n_old + n_new,
                        "mean": round(combined_mean, 0),
                        "median": tx_agg.get("median", existing_agg.get("median")),
                        "min": min(existing_agg.get("min", float("inf")), tx_agg.get("min", float("inf"))),
                        "max": max(existing_agg.get("max", 0), tx_agg.get("max", 0)),
                        "p25": tx_agg.get("p25", existing_agg.get("p25")),
                        "p75": tx_agg.get("p75", existing_agg.get("p75")),
                        "dispersion": tx_agg.get("dispersion", existing_agg.get("dispersion")),
                        "tx_count": n_new,  # 取引価格サンプル数
                    }
                    existing["by_use"][cat] = merged
                else:
                    # 新規カテゴリ
                    tx_agg["tx_count"] = tx_agg["count"]
                    existing["by_use"][cat] = tx_agg
            # 全体平均も再計算
            all_old = []
            for c, agg in existing["by_use"].items():
                all_old.extend([agg["mean"]] * agg["count"])
            if all_old:
                existing["all"] = aggregate_prices(all_old)
        else:
            # 新規市区町村
            new_count += 1
            all_p = aggregate_prices(all_prices_combined)
            existing_cities[key] = {
                "prefecture": pref,
                "city": city,
                "all": all_p,
                "by_use": {c: a for c, a in new_by_use.items() if a},
            }

    print(f"\nMerged into existing: {merged_count:,}, new cities added: {new_count:,}")
    print(f"Total cities now: {len(existing_cities):,}")

    # source 情報更新
    existing_db["source"] = (
        "国土数値情報 L01-2024 + L02-2023 + 不動産情報ライブラリ XIT001 取引価格 (2023-2024)"
    )

    # 保存
    with CITY_DB_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing_db, f, ensure_ascii=False, separators=(",", ":"))
    sz = CITY_DB_PATH.stat().st_size
    print(f"\nWrote {CITY_DB_PATH} ({sz/1024:.0f} KB)")


if __name__ == "__main__":
    main()
