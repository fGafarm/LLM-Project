"""
国土数値情報 L01 地価公示データ + L02 都道府県地価調査データを市区町村別に集計し、
land_price_by_city.json を生成。

入力:
  - land_price_data/L01-24_GML/L01-24.geojson  (国の地価公示, 1月1日時点)
  - land_price_data/L02-23_GML/L02-23.geojson  (都道府県地価調査, 7月1日時点)
出力: land_price_data/land_price_by_city.json

集計ロジック:
- L01とL02の標点を同じバケット（市区町村×用途）に混ぜて集計
- L02はL02_037(用途名テキスト)で住宅/商業/工業を判定
- 平均値・中央値・min/max・p25/p75・サンプル数を算出
- 政令指定都市は「○○市××区」を1単位として扱う
- アウトプットキー: "{都道府県}{市区町村}" (空白なし)
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path
from collections import defaultdict
from typing import Optional

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
L01_GEOJSON_PATH = PROJECT_ROOT / "land_price_data" / "L01-24_GML" / "L01-24.geojson"
L02_GEOJSON_PATH = PROJECT_ROOT / "land_price_data" / "L02-23_GML" / "L02-23.geojson"
OUT_PATH = PROJECT_ROOT / "land_price_data" / "land_price_by_city.json"
POINTS_OUT_PATH = PROJECT_ROOT / "land_price_data" / "land_price_points.json"

PREF_RE = re.compile(r"^(北海道|東京都|大阪府|京都府|.+?県)")


def parse_address(addr: str) -> tuple[Optional[str], Optional[str]]:
    """住所文字列から (都道府県, 市区町村) を抽出。

    優先順位:
      1. 政令指定都市の行政区: '横浜市西区' '札幌市中央区'
      2. 東京特別区: '千代田区' '中央区'  ← 「日本橋兜町」等の町名より先に
      3. 通常の市: '豊田市' '北茨城市'
      4. 郡＋町: '木田郡三木町'
      5. 単独町: '茨城町'
      6. 郡＋村: '南津軽郡藤崎村'
      7. 単独村: '十島村'
    """
    if not addr:
        return None, None
    s = re.sub(r"[\u3000\s]", "", addr)
    m = PREF_RE.match(s)
    if not m:
        return None, None
    pref = m.group(1)
    rest = s[len(pref):]

    patterns = [
        r"(.+?市.+?区)",     # 政令指定都市の区
        r"(.+?区)",          # 東京特別区
        r"(.+?市)",          # 一般市
        r"(.+?郡.+?町)",     # 郡＋町
        r"(.+?町)",          # 単独町
        r"(.+?郡.+?村)",     # 郡＋村
        r"(.+?村)",          # 単独村
    ]
    for pat in patterns:
        m_city = re.match(pat, rest)
        if m_city:
            return pref, m_city.group(1)
    return pref, None


def categorize_use(use_code, use_text: str) -> str:
    """L01_010(用途区分コード) と L01_028(現況用途) から正規化。

    L01_010 コード: 1=住宅地, 2=商業地, 3=宅地見込地, 4=工業地, 5=準工業地, 6=市街化調整区域内, 7=林地, 11=採草放牧地
    住宅地でも現況が「店舗」「事務所」「銀行」なら商業扱い（実勢価格が近い）。
    """
    code = use_code if isinstance(use_code, int) else 0
    if code == 2:
        return "commercial"
    if code in (4, 5):
        return "industrial"
    if code == 1:
        # 住宅地区分の中で店舗・事務所・銀行は商業利用とみなす
        text = use_text or ""
        commercial_kw = ("店舗", "事務所", "銀行", "倉庫", "工場", "医院", "診療所")
        if any(kw in text for kw in commercial_kw) and "住宅" not in text:
            return "commercial"
        return "residential"
    return "other"


def categorize_use_l02(use_name: str, current_use: str) -> str:
    """L02_037(用途名) と L02_043(現況) から L01 と同じ住宅/商業/工業/その他に正規化。

    L02_037 の値: '市街地宅地'(住宅), '商業', '工業', '林地', '都市計画区域外', '工場', '林業'
    """
    name = use_name or ""
    text = current_use or ""
    if "商業" in name:
        return "commercial"
    if "工業" in name or "工場" in name:
        return "industrial"
    if "林" in name:
        return "other"
    # 市街地宅地 → 住宅 (現況に商業キーワードあれば商業)
    if "宅地" in name or "住宅" in name or name == "":
        commercial_kw = ("店舗", "事務所", "銀行", "倉庫", "工場", "医院", "診療所")
        if any(kw in text for kw in commercial_kw) and "住宅" not in text:
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
    # p25/p75 は最低2点あれば計算可能（quantiles は n>=2 を要求）
    if len(prices) >= 2:
        q = statistics.quantiles(prices, n=4, method="inclusive")
        p25, p75 = round(q[0], 0), round(q[2], 0)
        result["p25"] = p25
        result["p75"] = p75
        # 正規化IQR = (p75 - p25) / median: 中央値に対する幅の比率
        if result["median"] > 0:
            result["dispersion"] = round((p75 - p25) / result["median"], 3)
    return result


def _coord_from_geometry(geom: dict) -> tuple[Optional[float], Optional[float]]:
    """GeoJSON geometry から (lat, lon) を抽出。Pointなら直接、それ以外は重心。"""
    if not geom:
        return None, None
    coords = geom.get("coordinates")
    if not coords:
        return None, None
    if geom.get("type") == "Point":
        lon, lat = coords[0], coords[1]
        return float(lat), float(lon)
    return None, None


def main():
    # bucket: {(pref, city): {use_category: [prices]}}
    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"residential": [], "commercial": [], "industrial": [], "other": []}
    )
    # 個別標点: kNN用に lat/lon 付きで保持
    points: list[dict] = []

    # ---- L01 (国の地価公示) 読み込み ----
    print(f"Loading L01 from {L01_GEOJSON_PATH}...")
    with L01_GEOJSON_PATH.open(encoding="utf-8") as f:
        l01_data = json.load(f)
    l01_features = l01_data["features"]
    print(f"  L01 features: {len(l01_features):,}")

    l01_used = 0
    l01_addr_unparsed = 0
    for feat in l01_features:
        props = feat["properties"]
        price = props.get("L01_008")
        addr = props.get("L01_025") or ""
        use_code = props.get("L01_010")
        use_text = props.get("L01_028") or ""
        if not price or price <= 0:
            continue
        pref, city = parse_address(addr)
        if not pref or not city:
            l01_addr_unparsed += 1
            continue
        cat = categorize_use(use_code, use_text)
        buckets[(pref, city)][cat].append(float(price))
        lat, lon = _coord_from_geometry(feat.get("geometry"))
        if lat is not None and lon is not None:
            points.append({"lat": lat, "lon": lon, "price": float(price), "use": cat, "src": "L01"})
        l01_used += 1
    print(f"  L01 used: {l01_used:,}, addr_unparsed: {l01_addr_unparsed}")

    # ---- L02 (都道府県地価調査) 読み込み (重複点もそのままプール: 7月時点と1月時点で時期差あり) ----
    if L02_GEOJSON_PATH.exists():
        print(f"Loading L02 from {L02_GEOJSON_PATH}...")
        with L02_GEOJSON_PATH.open(encoding="utf-8") as f:
            l02_data = json.load(f)
        l02_features = l02_data["features"]
        print(f"  L02 features: {len(l02_features):,}")

        l02_used = 0
        l02_addr_unparsed = 0
        for feat in l02_features:
            props = feat["properties"]
            price = props.get("L02_006")
            addr = props.get("L02_022") or ""
            use_name = props.get("L02_037") or ""
            current_use = props.get("L02_043") or ""
            if not price or price <= 0:
                continue
            pref, city = parse_address(addr)
            if not pref or not city:
                l02_addr_unparsed += 1
                continue
            cat = categorize_use_l02(use_name, current_use)
            buckets[(pref, city)][cat].append(float(price))
            lat, lon = _coord_from_geometry(feat.get("geometry"))
            if lat is not None and lon is not None:
                points.append({"lat": lat, "lon": lon, "price": float(price), "use": cat, "src": "L02"})
            l02_used += 1
        print(f"  L02 used: {l02_used:,}, addr_unparsed: {l02_addr_unparsed}")
    else:
        print(f"  L02 not found at {L02_GEOJSON_PATH}, skipping")

    print(f"Total cities aggregated: {len(buckets):,}")
    print(f"Total points with coords: {len(points):,}")

    # 出力構築
    out = {
        "source": "国土数値情報 L01-2024 (令和6年地価公示) + L02-2023 (令和5年都道府県地価調査)",
        "fiscal_year": 2024,
        "unit": "円/㎡",
        "license": "CC-BY 4.0",
        "cities": {},
    }
    for (pref, city), cats in sorted(buckets.items()):
        all_prices = []
        per_use = {}
        for cat, prices in cats.items():
            agg = aggregate_prices(prices)
            if agg:
                per_use[cat] = agg
                all_prices.extend(prices)
        out["cities"][f"{pref}{city}"] = {
            "prefecture": pref,
            "city": city,
            "all": aggregate_prices(all_prices),
            "by_use": per_use,
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    file_size = OUT_PATH.stat().st_size
    print(f"\nWrote {OUT_PATH}")
    print(f"  Cities: {len(out['cities']):,}")
    print(f"  Size: {file_size/1024:.1f} KB")

    # 標点リスト書き出し (kNN lookup用)
    points_out = {
        "source": out["source"] + " (with coordinates)",
        "fiscal_year": 2024,
        "unit": "円/㎡",
        "count": len(points),
        "points": points,
    }
    with POINTS_OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(points_out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {POINTS_OUT_PATH}")
    print(f"  Points: {len(points):,}")
    print(f"  Size: {POINTS_OUT_PATH.stat().st_size/1024:.1f} KB")

    # サマリー
    print("\n--- Sample (Tokyo 23 wards) ---")
    for key in sorted(out["cities"]):
        if "東京都" not in key:
            continue
        c = out["cities"][key]
        if "区" not in c["city"] or "市" in c["city"]:
            continue
        all_agg = c["all"]
        com = c["by_use"].get("commercial", {}).get("mean", 0)
        res = c["by_use"].get("residential", {}).get("mean", 0)
        print(f"  {key:20} 全体平均={all_agg.get('mean', 0):>12,.0f}円/㎡  商業={com:>12,.0f}  住宅={res:>10,.0f}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
