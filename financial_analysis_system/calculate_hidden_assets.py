"""
fixed_assets_store の各社 facilities.json と land_price_by_city.json を突合し、
土地の簿価 vs 推定時価の差額（含み益）を算出する。

各設備の住所→市区町村→坪単価 を引いて
   推定時価 = land_area_sqm × price_per_sqm (用途別: 商業/住宅/工業)
   含み益   = 推定時価 - 簿価
を計算。会社別に集計。

出力: fixed_assets_store/{code}_{name}/{year}_hidden_assets.json
ログ: fixed_assets_store/_hidden_assets_log_{year}.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or r"C:/Users/shun nabeno/Desktop/Local LLM Project")
LAND_PRICE_DB = PROJECT_ROOT / "land_price_data" / "land_price_by_city.json"
LAND_PRICE_POINTS = PROJECT_ROOT / "land_price_data" / "land_price_points.json"
GEOCODE_CACHE = PROJECT_ROOT / "land_price_data" / "geocode_cache.json"
ASSETS_ROOT = PROJECT_ROOT / "fixed_assets_store"

PREF_RE = re.compile(r"^(北海道|東京都|大阪府|京都府|.+?県)")

# kNN lookup parameters
KNN_RADIUS_KM = 3.0           # 半径3km以内の標点を検索
KNN_MIN_POINTS = 3            # この件数未満なら市区町村フォールバック
KNN_MAX_POINTS = 10           # 上位10件で重み付き平均
KNN_DISTANCE_FLOOR_KM = 0.05  # 距離0除算防止 (50m)


# ----- 住所解析 -------------------------------------------------------------


def parse_address(addr: str) -> tuple[Optional[str], Optional[str]]:
    """設備所在地から (都道府県, 市区町村) を抽出。

    対応する入力例:
      '愛知県豊田市'           → ('愛知県', '豊田市')
      '東京都港区'             → ('東京都', '港区')
      '東京都 多摩市ほか'      → ('東京都', '多摩市')   # 「ほか」を除去
      '神奈川県横浜市西区'     → ('神奈川県', '横浜市西区')
      '京都市南区'             → ('京都府', '京都市南区')  # 都道府県省略を補完
      '名古屋市中村区'         → ('愛知県', '名古屋市中村区')
    """
    if not addr:
        return None, None
    s = re.sub(r"[\u3000\s]", "", addr)
    # 「ほか」「ほか1社」「等」のサフィックス除去
    s = re.sub(r"(ほか.*$|外.*$|他.*$|等$)", "", s)

    # 政令指定都市は都道府県を補完
    s = _expand_government_city(s)

    m = PREF_RE.match(s)
    if not m:
        return None, None
    pref = m.group(1)
    rest = s[len(pref):]

    patterns = [
        r"(.+?市.+?区)",
        r"(.+?区)",
        r"(.+?市)",
        r"(.+?郡.+?町)",
        r"(.+?町)",
        r"(.+?郡.+?村)",
        r"(.+?村)",
    ]
    for pat in patterns:
        m_city = re.match(pat, rest)
        if m_city:
            return pref, m_city.group(1)
    return pref, None


# 政令指定都市名 → 所属都道府県（住所の先頭が市名で始まる場合の補完用）
GOVERNMENT_CITIES = {
    "札幌市": "北海道", "仙台市": "宮城県", "さいたま市": "埼玉県",
    "千葉市": "千葉県", "横浜市": "神奈川県", "川崎市": "神奈川県",
    "相模原市": "神奈川県", "新潟市": "新潟県", "静岡市": "静岡県",
    "浜松市": "静岡県", "名古屋市": "愛知県", "京都市": "京都府",
    "大阪市": "大阪府", "堺市": "大阪府", "神戸市": "兵庫県",
    "岡山市": "岡山県", "広島市": "広島県", "北九州市": "福岡県",
    "福岡市": "福岡県", "熊本市": "熊本県",
}


def _expand_government_city(s: str) -> str:
    """先頭が政令指定都市名なら都道府県名を補う。"""
    for city, pref in GOVERNMENT_CITIES.items():
        if s.startswith(city):
            return pref + s
    return s


# ----- 用途推定 -------------------------------------------------------------


# 業種別のデフォルト用途（土地の主要用途）
INDUSTRY_DEFAULT_USE = {
    # 製造業（工業地ベース）
    "輸送用機器": "industrial", "電気機器": "industrial", "機械": "industrial",
    "化学": "industrial", "鉄鋼": "industrial", "非鉄金属": "industrial",
    "ガラス・土石製品": "industrial", "繊維製品": "industrial", "ゴム製品": "industrial",
    "パルプ・紙": "industrial", "食料品": "industrial", "石油・石炭製品": "industrial",
    "金属製品": "industrial", "精密機器": "industrial", "医薬品": "industrial",
    "その他製品": "industrial",
    # 一次・建設・運輸・公益（工業地ベース）
    "鉱業": "industrial", "建設業": "industrial", "水産・農林業": "industrial",
    "陸運業": "industrial", "海運業": "industrial", "空運業": "industrial",
    "倉庫・運輸関連業": "industrial", "電気・ガス業": "industrial",
    # サービス・商業・金融（商業地ベース）
    "不動産業": "commercial", "銀行業": "commercial", "保険業": "commercial",
    "証券、商品先物取引業": "commercial", "その他金融業": "commercial",
    "卸売業": "commercial", "小売業": "commercial", "サービス業": "commercial",
    "情報・通信業": "commercial",
}


def estimate_use_category(facility: dict, industry: str = "") -> str:
    """設備の facility_type / segment / 業種から商業/住宅/工業を推定。

    優先順位:
      1. facility_type/office_name に「店舗・支店・ビル・ホテル・百貨店」等があれば商業
      2. facility_type/office_name に「工場・プラント・研究所」等があれば工業
      3. 業種デフォルトを適用
      4. 不明は商業
    """
    text = " ".join([
        facility.get("facility_type") or "",
        facility.get("segment") or "",
        facility.get("office_name") or "",
    ])

    # 強い商業シグナル
    if any(k in text for k in ("店舗", "支店", "営業所", "ホテル", "百貨店", "ショッピング", "ビル")):
        return "commercial"
    # 強い工業シグナル
    if any(k in text for k in ("工場", "プラント", "製造", "生産", "研究所", "テクニカル", "車両", "保線", "倉庫", "物流")):
        return "industrial"

    # 業種デフォルト
    return INDUSTRY_DEFAULT_USE.get(industry, "commercial")


# ----- 坪単価ルックアップ ---------------------------------------------------


class LandPriceDB:
    def __init__(self, db_path: Path):
        with db_path.open(encoding="utf-8") as f:
            self.data = json.load(f)
        self.cities = self.data["cities"]

    def lookup(self, pref: str, city: str, use_category: str) -> Optional[dict]:
        """指定の市区町村+用途の坪単価を取得。

        Returns: {"price_per_sqm": ..., "source": ..., "fallback_used": bool}
        """
        if not pref or not city:
            return None
        key = f"{pref}{city}"
        entry = self.cities.get(key)
        if not entry:
            # 都道府県平均にフォールバック
            return self._prefecture_fallback(pref, use_category)

        # 用途別優先 → 全体平均にフォールバック
        by_use = entry.get("by_use", {})
        target = by_use.get(use_category)
        fallback = False
        if not target or target.get("count", 0) < 2:
            target = entry.get("all")
            fallback = True
        if not target:
            return None
        return {
            "price_per_sqm": target["mean"],
            "median_per_sqm": target["median"],
            "p25_per_sqm": target.get("p25"),
            "p75_per_sqm": target.get("p75"),
            "dispersion": target.get("dispersion"),
            "sample_count": target["count"],
            "city_key": key,
            "use_category": use_category,
            "fallback_to_all": fallback,
        }

    def has_city(self, pref: str, city: str) -> bool:
        return f"{pref}{city}" in self.cities

    def _prefecture_fallback(self, pref: str, use_category: str) -> Optional[dict]:
        prices = []
        for key, entry in self.cities.items():
            if entry["prefecture"] != pref:
                continue
            target = entry.get("by_use", {}).get(use_category) or entry.get("all")
            if target:
                prices.append(target["mean"])
        if not prices:
            return None
        import statistics as _st
        avg = sum(prices) / len(prices)
        median_val = _st.median(prices)
        result = {
            "price_per_sqm": round(avg, 0),
            "median_per_sqm": round(median_val, 0),
            "sample_count": len(prices),
            "city_key": f"{pref}*",
            "use_category": use_category,
            "fallback_to_all": True,
            "prefecture_average": True,
        }
        # 県内の市区町村 mean 分布から p25/p75/dispersion を算出
        if len(prices) >= 2:
            q = _st.quantiles(prices, n=4, method="inclusive")
            p25, p75 = round(q[0], 0), round(q[2], 0)
            result["p25_per_sqm"] = p25
            result["p75_per_sqm"] = p75
            if median_val > 0:
                result["dispersion"] = round((p75 - p25) / median_val, 3)
        return result


# ----- kNN 標点索引 ---------------------------------------------------------


import math


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の距離(km)。Haversine 公式。"""
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class LandPriceKNNIndex:
    """L01+L02 標点を緯度経度グリッドでインデックス化し、近傍検索する。

    グリッド: 0.05度 x 0.05度 (約 5.5km x 4.5km in 日本緯度帯)
    検索時は中心セル ± 1 (3x3) のセルを走査して半径フィルタ。
    """

    GRID_DEG = 0.05

    def __init__(self, points_path: Path):
        with points_path.open(encoding="utf-8") as f:
            self.data = json.load(f)
        self.points = self.data.get("points", [])
        self.grid: dict[tuple[int, int], list[dict]] = {}
        for p in self.points:
            cell = self._cell(p["lat"], p["lon"])
            self.grid.setdefault(cell, []).append(p)

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return (int(lat / self.GRID_DEG), int(lon / self.GRID_DEG))

    def lookup_knn(
        self,
        lat: float,
        lon: float,
        use_category: str,
        radius_km: float = KNN_RADIUS_KM,
        min_points: int = KNN_MIN_POINTS,
        max_points: int = KNN_MAX_POINTS,
    ) -> Optional[dict]:
        """半径 radius_km 以内の同用途標点を距離逆数で重み付き平均。

        Returns: {"price_per_sqm": ..., "p25_per_sqm": ..., "p75_per_sqm": ..., "dispersion": ...,
                  "knn_count": ..., "knn_avg_distance_km": ..., "knn_radius_km": ...}
        """
        # グリッドの何セル分を走査するか (radius / GRID_DEG_in_KM)
        # 1度 ≈ 111km、0.05度 ≈ 5.5km、しかし安全側に余裕を取る
        cell_radius = max(1, int(radius_km / 5.0) + 1)
        center = self._cell(lat, lon)
        candidates: list[tuple[float, dict]] = []
        for di in range(-cell_radius, cell_radius + 1):
            for dj in range(-cell_radius, cell_radius + 1):
                cell_pts = self.grid.get((center[0] + di, center[1] + dj))
                if not cell_pts:
                    continue
                for p in cell_pts:
                    if p["use"] != use_category:
                        continue
                    d = _haversine_km(lat, lon, p["lat"], p["lon"])
                    if d <= radius_km:
                        candidates.append((d, p))
        # 用途一致が足りなければ用途緩和 (other 以外)
        if len(candidates) < min_points:
            for di in range(-cell_radius, cell_radius + 1):
                for dj in range(-cell_radius, cell_radius + 1):
                    cell_pts = self.grid.get((center[0] + di, center[1] + dj))
                    if not cell_pts:
                        continue
                    for p in cell_pts:
                        if p["use"] == use_category or p["use"] == "other":
                            continue
                        d = _haversine_km(lat, lon, p["lat"], p["lon"])
                        if d <= radius_km:
                            candidates.append((d, p))
        if len(candidates) < min_points:
            return None

        # 近い順にソートして上位 max_points
        candidates.sort(key=lambda x: x[0])
        used = candidates[:max_points]
        # 距離逆数加重平均
        total_w = 0.0
        wsum = 0.0
        prices = []
        for d, p in used:
            w = 1.0 / max(d, KNN_DISTANCE_FLOOR_KM)
            total_w += w
            wsum += w * p["price"]
            prices.append(p["price"])
        weighted_mean = wsum / total_w
        # p25/p75/median は加重なし(単純な順位統計)
        prices.sort()
        n = len(prices)
        median = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
        # quantiles via linear interpolation
        def q(qtl: float) -> float:
            idx = qtl * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            return prices[lo] + (prices[hi] - prices[lo]) * (idx - lo)
        p25 = q(0.25)
        p75 = q(0.75)
        dispersion = (p75 - p25) / median if median > 0 else None
        avg_d = sum(d for d, _ in used) / len(used)
        return {
            "price_per_sqm": round(weighted_mean, 0),
            "median_per_sqm": round(median, 0),
            "p25_per_sqm": round(p25, 0),
            "p75_per_sqm": round(p75, 0),
            "dispersion": round(dispersion, 3) if dispersion is not None else None,
            "sample_count": len(used),
            "knn_count": len(used),
            "knn_avg_distance_km": round(avg_d, 2),
            "knn_radius_km": radius_km,
            "use_category": use_category,
            "method": "knn",
        }


def load_geocode_cache(path: Path = GEOCODE_CACHE) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ----- 含み益計算 -----------------------------------------------------------


def is_facility_outlier(facility: dict, price_per_sqm: Optional[float] = None) -> tuple[bool, str]:
    """異常値検知。

    除外対象:
    - 住所あいまい: 「他」「ほか」「各地」→ 単一地点として時価計算不可
    - office_name に「(注)」マーク → 有報の注記付き行（多くは全社集計値）
    - 面積 10km² 以上 → 鉱山地/誤抽出/合算行
    - 都市商業地単価 × 面積過大: 単価 > 300万円/㎡ で 面積 > 100,000㎡ → 物理不可能
      （港区・千代田区・中央区で10万㎡超の単一物件は現実に存在しない）
    - 面積 > 500,000㎡ で「本社」「本店」名義 → 全国合算の可能性高
    - 簿価/㎡ < 500円 → 山林/鉱業権付随地
    - 簿価/㎡ > 1億円/㎡ → 注記数値の誤読
    - 簿価=0 or null かつ面積>100㎡ → 借地の可能性高（簿価未計上）
    """
    addr = facility.get("address_raw") or ""
    name = facility.get("office_name") or ""

    ambiguous_markers = ("他", "ほか", "各地", "国内各地", "全国")
    for mk in ambiguous_markers:
        if mk in addr:
            return True, f"address_ambiguous:{mk}"

    # (注) or （注) 付きのレコードは全社合算の可能性
    if "(注)" in name or "（注）" in name or "（注)" in name or "(注）" in name:
        return True, "annotation_marked"

    area = facility.get("land_area_sqm") or 0
    book_raw = facility.get("land_book_value_mil")
    book = book_raw or 0

    if area >= 10_000_000:
        return True, "area_too_large"

    # 簿価=0 or null かつ面積>100㎡ → 借地推定 (時価評価対象外)
    if (book_raw is None or book_raw == 0) and area > 100:
        return True, "leased_land_no_book_value"

    # 都市中心部×大面積の物理的不可能チェック
    if price_per_sqm and price_per_sqm > 3_000_000 and area > 100_000:
        return True, "urban_area_implausible"

    # 「本社」「本店」名義 + 大面積 → 全社合算の可能性大
    # 東京23区など大都市の本社で20,000㎡超は通常ありえない (高層化される)
    # 地方の本社 (トヨタ本社=愛知県豊田市) は広い土地もあり得るので、
    # 都市中心部の単価 (price > ¥500K/㎡) かつ 面積>30,000㎡ で flag
    head_office_markers = ("本社", "本店")
    if any(mk in name for mk in head_office_markers):
        if area > 500_000:
            return True, "head_office_aggregate"
        if area > 30_000 and price_per_sqm and price_per_sqm > 500_000:
            return True, "head_office_aggregate_urban"

    if area > 0 and book > 0:
        per_sqm = book * 1_000_000 / area
        if per_sqm < 500:
            return True, "book_per_sqm_too_low"
        if per_sqm > 100_000_000:
            return True, "book_per_sqm_too_high"
        # 簿価/㎡ が当該市区町村の地価公示単価の10倍超 → 面積or簿価の誤読
        # 歴史的取得原価は現在地価より安いのが普通で、高くても2-3倍程度
        if price_per_sqm and per_sqm > price_per_sqm * 10:
            return True, "book_per_sqm_vs_land_price_mismatch"

    if area > 0 and area < 50 and book > 100:
        return True, "area_too_small"
    return False, ""


# ばらつき(dispersion = (p75-p25)/median) の分類しきい値
DISPERSION_LOW_MAX = 0.25   # < 0.25 → low (点推定で十分)
DISPERSION_MID_MAX = 0.50   # 0.25–0.50 → mid
                            # >= 0.50 → high (レンジ表示の価値あり)


def classify_dispersion(d: Optional[float]) -> str:
    """dispersion 値を low/mid/high に分類。None は unknown。"""
    if d is None:
        return "unknown"
    if d < DISPERSION_LOW_MAX:
        return "low"
    if d < DISPERSION_MID_MAX:
        return "mid"
    return "high"


# 特殊用地 (線路敷/送電/変電/発電所/基地局等) を検出するキーワード
SPECIAL_LAND_KEYWORDS = (
    "線路", "軌道", "保線", "車両基地", "留置線", "操車場", "車両センター", "車両所",
    "駅", "停車場", "駅務", "信号場", "電車基地", "電車区", "機関区", "客車区",  # 駅敷地
    "変電", "発電", "送電", "送配電", "電気所", "電気設備", "配電", "開閉所",
    "基地局", "交換局", "局舎", "無線局", "アンテナ",
    "パイプライン", "ガス導管", "貯蔵", "備蓄", "タンク",
    "ダム", "取水", "浄水", "配水", "排水処理",
    "港湾", "岸壁", "埠頭",
)

# 業種×特殊用地 の時価キャップ: 簿価×この倍率 を上限とする
# (市場流通性が低いため地価公示平均では過大評価になる)
INDUSTRY_SPECIAL_CAP = {
    "電気・ガス業": 3.0,      # 発電所/変電所/送電: 簿価×3 上限
    "陸運業": 4.0,           # 線路敷/車両基地: 鉄道会社向け
    "情報・通信業": 5.0,      # 基地局/交換局
    "倉庫・運輸関連業": 5.0,   # 港湾用地
    "建設業": 6.0,            # 鉄道工事/通信工事/電気工事会社の特殊用地
}

# 店舗系 facility を検出 (小売・サービス業のリース店舗を簿価で持ってるケース対策)
RETAIL_STORE_KEYWORDS = (
    "店舗", "店", "ストア", "売場", "支店", "ショップ", "ホール",
    "百貨店", "ショッピング", "営業所", "サロン",
)
# 業種×店舗 の時価キャップ: 倍率上限
RETAIL_STORE_CAP = {
    "小売業": 20.0,
    "サービス業": 20.0,
    "卸売業": 20.0,
}


def is_retail_store(facility: dict) -> bool:
    text = " ".join([
        facility.get("facility_type") or "",
        facility.get("segment") or "",
        facility.get("office_name") or "",
    ])
    return any(kw in text for kw in RETAIL_STORE_KEYWORDS)


# 不動産業/商社の都心保有不動産にプレミアム補正
# 都心商業地のkNN推定は超プレミアム立地 (大手町/丸の内/銀座/虎ノ門/六本木) を捕捉しきれない
# 区レベルで「都心ビジネス区」を判定 (建物・土地ごと再評価しない取得原価主義の簿価対策)
PREMIUM_DISTRICTS = (
    "千代田区", "中央区", "港区", "渋谷区", "新宿区",
    "大阪市北区", "大阪市中央区", "大阪市西区",
    "名古屋市中区",
    "横浜市西区", "横浜市中区",
    "福岡市中央区", "福岡市博多区",
)

# 不動産業・商社・銀行・証券など「都心優良不動産を保有する業種」
PREMIUM_LOCATION_INDUSTRIES = (
    "不動産業", "卸売業", "銀行業", "保険業", "証券、商品先物取引業",
)


def is_premium_location(facility: dict) -> bool:
    """住所がプレミアム立地 (都心ビジネス区) に該当するか。"""
    addr = (facility.get("address_raw") or "").replace("　", "").replace(" ", "")
    return any(d in addr for d in PREMIUM_DISTRICTS)


def apply_premium_floor(
    market_value_mil: float,
    book_value_mil: Optional[float],
    facility: dict,
    industry: str,
) -> tuple[float, bool]:
    """簿価>時価かつ プレミアム立地+対象業種 の場合、含み損を回避するため
    時価 = max(時価, 簿価×1.2) とする (簿価以上の時価を保証, 20%プレミアム加算)。

    取得原価ベースで簿価が積み上がっている不動産業では、地価公示の市区町村平均で
    再評価すると都心の超プレミアム物件 (丸の内・大手町等) は過小評価になる。
    """
    if book_value_mil is None or book_value_mil <= 0:
        return market_value_mil, False
    if market_value_mil >= book_value_mil:
        return market_value_mil, False
    if industry not in PREMIUM_LOCATION_INDUSTRIES:
        return market_value_mil, False
    if not is_premium_location(facility):
        return market_value_mil, False
    return book_value_mil * 1.2, True


def apply_retail_cap(
    market_value_mil: float,
    book_value_mil: Optional[float],
    facility: dict,
    industry: str,
) -> tuple[float, bool, Optional[float]]:
    """小売・サービス業の店舗用地に倍率キャップ。
    Returns: (capped_market_value, was_capped, cap_multiple_used)
    """
    if book_value_mil is None or book_value_mil <= 0:
        return market_value_mil, False, None
    if not is_retail_store(facility):
        return market_value_mil, False, None
    cap_mult = RETAIL_STORE_CAP.get(industry)
    if cap_mult is None:
        return market_value_mil, False, None
    cap_value = book_value_mil * cap_mult
    if market_value_mil > cap_value:
        return cap_value, True, cap_mult
    return market_value_mil, False, cap_mult


def is_special_land(facility: dict) -> bool:
    """facility_type / segment / office_name に特殊用地キーワードが含まれるか。"""
    text = " ".join([
        facility.get("facility_type") or "",
        facility.get("segment") or "",
        facility.get("office_name") or "",
    ])
    return any(kw in text for kw in SPECIAL_LAND_KEYWORDS)


def apply_industry_cap(
    market_value_mil: float,
    book_value_mil: Optional[float],
    facility: dict,
    industry: str,
) -> tuple[float, bool, Optional[float]]:
    """業種別の時価キャップを適用。
    Returns: (capped_market_value, was_capped, cap_multiple_used)
    """
    if book_value_mil is None or book_value_mil <= 0:
        return market_value_mil, False, None
    if not is_special_land(facility):
        return market_value_mil, False, None
    cap_mult = INDUSTRY_SPECIAL_CAP.get(industry)
    if cap_mult is None:
        return market_value_mil, False, None
    cap_value = book_value_mil * cap_mult
    if market_value_mil > cap_value:
        return cap_value, True, cap_mult
    return market_value_mil, False, cap_mult


def calculate_for_company(
    facilities_file: Path,
    db: LandPriceDB,
    knn_index: Optional[LandPriceKNNIndex] = None,
    geocode_cache: Optional[dict] = None,
    a29_index=None,
) -> dict:
    with facilities_file.open(encoding="utf-8") as f:
        data = json.load(f)

    industry = data.get("industry_sector33", "")
    enriched_facilities = []
    total_book_all = 0.0        # 全facility簿価合計（参考値）
    priced_book = 0.0            # 時価評価できた facility の簿価のみ
    priced_market = 0.0          # 対応する時価合計
    priced_market_p25 = 0.0      # p25単価ベース時価合計
    priced_market_p75 = 0.0      # p75単価ベース時価合計
    # 高ばらつき地点のみの集計（「レンジ表示価値あり」部分）
    high_disp_book = 0.0
    high_disp_market = 0.0
    high_disp_market_p25 = 0.0
    high_disp_market_p75 = 0.0
    disp_counts = {"low": 0, "mid": 0, "high": 0, "unknown": 0}
    excluded_book = 0.0
    excluded_count = 0
    facilities_priced = 0

    for f in data["facilities"]:
        if f.get("country") != "JP":
            continue
        addr = f.get("address_raw") or ""
        area = f.get("land_area_sqm")
        book = f.get("land_book_value_mil")

        pref, city = parse_address(addr)
        use_cat = estimate_use_category(f, industry)
        use_cat_source = "keyword"  # keyword | a29 | knn_geocode
        # geocoding 結果取得 (kNN と A29 で共用)
        geo = None
        if geocode_cache is not None:
            geo = geocode_cache.get(addr) or geocode_cache.get(addr.strip())
            if not (geo and isinstance(geo, dict) and geo.get("lat") is not None and not geo.get("error")):
                geo = None
        # A29 用途地域GIS で use_category を上書き (文字列マッチより精緻)
        a29_result = None
        if a29_index is not None and geo is not None:
            try:
                a29_result = a29_index.lookup(float(geo["lat"]), float(geo["lon"]))
                if a29_result:
                    use_cat = a29_result["use_category"]
                    use_cat_source = "a29"
            except Exception:
                pass
        # 1) kNN: geocodingがあれば近傍標点で重み付き平均
        knn_price = None
        if knn_index is not None and geo is not None:
            knn_price = knn_index.lookup_knn(
                float(geo["lat"]), float(geo["lon"]), use_cat
            )
        # 2) 市区町村平均にフォールバック (kNNが取れなかった場合)
        if knn_price is None:
            tentative_price = db.lookup(pref, city, use_cat)
        else:
            tentative_price = knn_price
        tentative_ppm = tentative_price["price_per_sqm"] if tentative_price else None
        outlier, reason = is_facility_outlier(f, tentative_ppm)
        price_info = tentative_price if not outlier else None

        if book and not outlier:
            total_book_all += book

        market_value_mil = None
        market_value_p25_mil = None
        market_value_p75_mil = None
        hidden_gain_mil = None
        hidden_gain_p25_mil = None
        hidden_gain_p75_mil = None
        disp_level = "unknown"
        special_land_flag = is_special_land(f)
        capped = False
        cap_mult_used = None
        premium_floored = False
        if price_info and area:
            market_value_yen = area * price_info["price_per_sqm"]
            market_value_mil = market_value_yen / 1_000_000
            p25_ppm = price_info.get("p25_per_sqm")
            p75_ppm = price_info.get("p75_per_sqm")
            if p25_ppm is not None:
                market_value_p25_mil = area * p25_ppm / 1_000_000
            if p75_ppm is not None:
                market_value_p75_mil = area * p75_ppm / 1_000_000
            # 業種別キャップ適用 (電力/鉄道/通信の特殊用地)
            market_value_mil, capped, cap_mult_used = apply_industry_cap(
                market_value_mil, book, f, industry
            )
            # 小売・サービスの店舗用地キャップ
            if not capped:
                market_value_mil, retail_capped, retail_mult = apply_retail_cap(
                    market_value_mil, book, f, industry
                )
                if retail_capped:
                    capped = True
                    cap_mult_used = retail_mult
            # 不動産業・商社の都心プレミアム立地: 簿価以上の時価を保証
            market_value_mil, premium_floored = apply_premium_floor(
                market_value_mil, book, f, industry
            )
            # P25/P75 も同じ floor を適用 (P25 が簿価×1.2 を下回らないように)
            if premium_floored and book and book > 0:
                floor_value = book * 1.2
                if market_value_p25_mil is not None and market_value_p25_mil < floor_value:
                    market_value_p25_mil = floor_value
                if market_value_p75_mil is not None and market_value_p75_mil < floor_value:
                    market_value_p75_mil = floor_value
            if capped and market_value_p25_mil is not None:
                market_value_p25_mil = min(market_value_p25_mil, book * cap_mult_used)
            if capped and market_value_p75_mil is not None:
                market_value_p75_mil = min(market_value_p75_mil, book * cap_mult_used)
            if book is not None:
                hidden_gain_mil = market_value_mil - book
                if market_value_p25_mil is not None:
                    hidden_gain_p25_mil = market_value_p25_mil - book
                if market_value_p75_mil is not None:
                    hidden_gain_p75_mil = market_value_p75_mil - book
            facilities_priced += 1
            priced_market += market_value_mil
            if market_value_p25_mil is not None:
                priced_market_p25 += market_value_p25_mil
            else:
                priced_market_p25 += market_value_mil  # p25が無ければ点推定で代用
            if market_value_p75_mil is not None:
                priced_market_p75 += market_value_p75_mil
            else:
                priced_market_p75 += market_value_mil
            if book:
                priced_book += book

            disp_level = classify_dispersion(price_info.get("dispersion"))
            disp_counts[disp_level] = disp_counts.get(disp_level, 0) + 1
            if disp_level == "high":
                if book:
                    high_disp_book += book
                high_disp_market += market_value_mil
                if market_value_p25_mil is not None:
                    high_disp_market_p25 += market_value_p25_mil
                if market_value_p75_mil is not None:
                    high_disp_market_p75 += market_value_p75_mil

        if outlier and book:
            excluded_book += book
            excluded_count += 1

        enriched_facilities.append({
            **f,
            "parsed_pref": pref,
            "parsed_city": city,
            "use_category": use_cat,
            "use_category_source": use_cat_source,
            "a29_zone_code": (a29_result or {}).get("code"),
            "geocoded_lat": (geo or {}).get("lat") if geo else None,
            "geocoded_lon": (geo or {}).get("lon") if geo else None,
            "lookup": price_info,
            "estimated_market_value_mil": round(market_value_mil, 1) if market_value_mil else None,
            "estimated_market_value_p25_mil": round(market_value_p25_mil, 1) if market_value_p25_mil is not None else None,
            "estimated_market_value_p75_mil": round(market_value_p75_mil, 1) if market_value_p75_mil is not None else None,
            "hidden_gain_mil": round(hidden_gain_mil, 1) if hidden_gain_mil is not None else None,
            "hidden_gain_p25_mil": round(hidden_gain_p25_mil, 1) if hidden_gain_p25_mil is not None else None,
            "hidden_gain_p75_mil": round(hidden_gain_p75_mil, 1) if hidden_gain_p75_mil is not None else None,
            "dispersion_level": disp_level,
            "special_land": special_land_flag,
            "industry_cap_applied": capped,
            "industry_cap_multiple": cap_mult_used if capped else None,
            "premium_location_floored": premium_floored,
            "outlier": outlier,
            "outlier_reason": reason if outlier else None,
        })

    # === Phase 8a: 同一企業内 facility 倍率外れ値検出 ===
    # 各社内で「他facilityの中央値倍率の10倍超」のものを intra_company_outlier として除外
    # ただし時価評価できた facility が3件以上ある場合のみ実行
    intra_outlier_count = 0
    intra_outlier_book = 0.0
    intra_outlier_market = 0.0
    multiples = []
    for ef in enriched_facilities:
        b = ef.get("land_book_value_mil")
        m = ef.get("estimated_market_value_mil")
        if b and b > 0 and m and not ef.get("outlier"):
            multiples.append((m / b, ef))
    if len(multiples) >= 3:
        sorted_mults = sorted(m for m, _ in multiples)
        median_mult = sorted_mults[len(sorted_mults) // 2]
        # 中央値の10倍超を外れ値扱い (ただし最低絶対値 30倍 を超える場合)
        threshold = max(median_mult * 10.0, 30.0)
        for mult, ef in multiples:
            if mult > threshold:
                ef["intra_company_outlier"] = True
                ef["intra_company_outlier_reason"] = f"mult={mult:.1f}x > median×10 ({median_mult:.1f}×10)"
                intra_outlier_count += 1
                intra_outlier_book += ef.get("land_book_value_mil") or 0
                intra_outlier_market += ef.get("estimated_market_value_mil") or 0
                # サマリ集計から除外するため値をクリア
                # priced_book/priced_market から差し引く
                priced_book -= ef.get("land_book_value_mil") or 0
                priced_market -= ef.get("estimated_market_value_mil") or 0
                if ef.get("estimated_market_value_p25_mil") is not None:
                    priced_market_p25 -= ef["estimated_market_value_p25_mil"]
                else:
                    priced_market_p25 -= ef.get("estimated_market_value_mil") or 0
                if ef.get("estimated_market_value_p75_mil") is not None:
                    priced_market_p75 -= ef["estimated_market_value_p75_mil"]
                else:
                    priced_market_p75 -= ef.get("estimated_market_value_mil") or 0
                facilities_priced -= 1
                # ef から含み益関連フィールドを None化
                ef["estimated_market_value_mil"] = None
                ef["estimated_market_value_p25_mil"] = None
                ef["estimated_market_value_p75_mil"] = None
                ef["hidden_gain_mil"] = None
                ef["hidden_gain_p25_mil"] = None
                ef["hidden_gain_p75_mil"] = None

    total_hidden = priced_market - priced_book if priced_book else 0
    total_hidden_p25 = priced_market_p25 - priced_book if priced_book else 0
    total_hidden_p75 = priced_market_p75 - priced_book if priced_book else 0
    high_disp_hidden_p25 = high_disp_market_p25 - high_disp_book if high_disp_book else 0
    high_disp_hidden_p75 = high_disp_market_p75 - high_disp_book if high_disp_book else 0
    high_disp_hidden_mean = high_disp_market - high_disp_book if high_disp_book else 0
    priced_coverage = (priced_book / total_book_all) if total_book_all else 0
    return {
        "company_code": data["company_code"],
        "company_name": data["company_name"],
        "fiscal_year": data["fiscal_year"],
        "industry_sector33": data.get("industry_sector33", ""),
        "summary": {
            "facilities_total": len(data["facilities"]),
            "facilities_with_price": facilities_priced,
            "facilities_excluded_outlier": excluded_count,
            "excluded_book_value_mil_yen": round(excluded_book, 0),
            # 全facility簿価（参考値、外れ値除外済）
            "total_land_book_value_all_mil_yen": round(total_book_all, 0),
            # 時価評価できた分のみ（含み益計算の基礎）
            "total_land_book_value_mil_yen": round(priced_book, 0),
            "total_land_estimated_market_value_mil_yen": round(priced_market, 0),
            "total_land_estimated_market_value_p25_mil_yen": round(priced_market_p25, 0),
            "total_land_estimated_market_value_p75_mil_yen": round(priced_market_p75, 0),
            "total_hidden_gain_mil_yen": round(total_hidden, 0),
            "total_hidden_gain_p25_mil_yen": round(total_hidden_p25, 0),
            "total_hidden_gain_p75_mil_yen": round(total_hidden_p75, 0),
            "hidden_gain_ratio": round(total_hidden / priced_book, 2) if priced_book else None,
            "market_to_book_multiple": round(priced_market / priced_book, 2) if priced_book else None,
            # 時価評価カバー率 (時価評価済簿価 / 全簿価)
            "priced_coverage_ratio": round(priced_coverage, 3),
            # ばらつき分布
            "dispersion_counts": disp_counts,
            # 高ばらつき地点のみの部分（レンジ表示価値ありの部分集合）
            "high_dispersion_book_value_mil_yen": round(high_disp_book, 0),
            "high_dispersion_hidden_gain_mil_yen": round(high_disp_hidden_mean, 0),
            "high_dispersion_hidden_gain_p25_mil_yen": round(high_disp_hidden_p25, 0),
            "high_dispersion_hidden_gain_p75_mil_yen": round(high_disp_hidden_p75, 0),
            # Phase 8a: 同一企業内倍率外れ値 (除外済)
            "intra_company_outlier_count": intra_outlier_count,
            "intra_company_outlier_book_mil_yen": round(intra_outlier_book, 0),
            "intra_company_outlier_market_mil_yen": round(intra_outlier_market, 0),
        },
        "facilities": enriched_facilities,
    }


def main():
    db = LandPriceDB(LAND_PRICE_DB)
    print(f"Loaded {len(db.cities):,} cities from land price DB")

    knn_index = None
    if LAND_PRICE_POINTS.exists():
        knn_index = LandPriceKNNIndex(LAND_PRICE_POINTS)
        print(f"Loaded {len(knn_index.points):,} points for kNN ({len(knn_index.grid):,} grid cells)")
    else:
        print(f"WARNING: {LAND_PRICE_POINTS} not found, kNN disabled")

    geocode_cache = load_geocode_cache()
    print(f"Geocode cache entries: {len(geocode_cache):,}")

    a29_index = None
    try:
        from a29_zoning import A29ZoningIndex
        print("Loading A29 用途地域 index...")
        a29_index = A29ZoningIndex()
        print(f"  A29 polygons: {len(a29_index.polygons):,}")
    except Exception as e:
        print(f"  A29 load skipped: {e}")

    files = list(ASSETS_ROOT.glob("*/2024_facilities.json"))
    print(f"Processing {len(files):,} companies...")

    failures = []
    summaries = []
    knn_used_total = 0
    city_used_total = 0

    for i, fp in enumerate(files):
        try:
            result = calculate_for_company(fp, db, knn_index, geocode_cache, a29_index)
            for fac in result.get("facilities", []):
                lookup = fac.get("lookup") or {}
                if lookup.get("method") == "knn":
                    knn_used_total += 1
                elif lookup:
                    city_used_total += 1
        except Exception as e:
            failures.append({"file": str(fp), "error": f"{type(e).__name__}: {e}"})
            continue
        out_file = fp.parent / "2024_hidden_assets.json"
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        summaries.append({
            "code": result["company_code"],
            "name": result["company_name"],
            "industry": result["industry_sector33"],
            **result["summary"],
        })
        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(files)}] processed")

    print(f"\nDone. {len(summaries)} success, {len(failures)} failures")
    print(f"Lookup method: kNN={knn_used_total:,}  city_avg={city_used_total:,}")

    # 全社サマリー
    log_file = ASSETS_ROOT / "_hidden_assets_log_2024.json"
    with log_file.open("w", encoding="utf-8") as f:
        json.dump({
            "total_companies": len(summaries),
            "summaries": summaries,
            "failures": failures[:50],
        }, f, ensure_ascii=False, indent=2)

    # Top 20 by 含み益
    sorted_by_gain = sorted(summaries, key=lambda s: -(s.get("total_hidden_gain_mil_yen") or 0))
    print("\n--- Top 20 by 含み益 ---")
    print(f"{'Code':6}{'Name':30}{'Industry':12} {'簿価(百万)':>14}{'時価(百万)':>14}{'含み益(百万)':>16}{'倍率':>8}")
    for s in sorted_by_gain[:20]:
        book = s.get("total_land_book_value_mil_yen", 0)
        market = s.get("total_land_estimated_market_value_mil_yen", 0)
        gain = s.get("total_hidden_gain_mil_yen", 0)
        mult = s.get("market_to_book_multiple", 0)
        print(f"{s['code']:6}{s['name'][:28]:28} {(s.get('industry') or '')[:10]:10} {book:>14,.0f}{market:>14,.0f}{gain:>16,.0f}{(mult or 0):>7.1f}x")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
