"""
A29 用途地域 (zoning) シェイプファイルからポリゴン全件をロードし、
STRtree で緯度経度→用途地域コード のルックアップを提供する。

ルックアップ結果を「商業/住宅/工業/その他」に正規化し、
calculate_hidden_assets.py の estimate_use_category() を補強する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
A29_ROOT = PROJECT_ROOT / "land_price_data" / "A29-11"

# 用途地域コード(A29_004) → 商業/住宅/工業 正規化
# 1: 第一種低層住居専用
# 2: 第二種低層住居専用
# 3: 第一種中高層住居専用
# 4: 第二種中高層住居専用
# 5: 第一種住居
# 6: 第二種住居
# 7: 準住居
# 8: 田園住居 (2018年新設、A29-11時点では存在しない可能性)
# 9: 近隣商業
# 10: 商業
# 11: 準工業
# 12: 工業
# 13: 工業専用
A29_USE_MAP = {
    1: "residential", 2: "residential", 3: "residential", 4: "residential",
    5: "residential", 6: "residential", 7: "residential", 8: "residential",
    9: "commercial", 10: "commercial",
    11: "industrial", 12: "industrial", 13: "industrial",
}


class A29ZoningIndex:
    def __init__(self, root: Path = A29_ROOT):
        try:
            import shapefile
            from shapely.geometry import Polygon, Point
            from shapely.strtree import STRtree
        except ImportError as e:
            raise ImportError(f"A29ZoningIndex requires pyshp + shapely: {e}")
        self._Polygon = Polygon
        self._Point = Point
        self._STRtree = STRtree

        self.polygons: list = []
        self.codes: list[int] = []
        self.use_cats: list[str] = []
        self.prefs: list[str] = []
        self.cities: list[str] = []
        self._load_all(root, shapefile)

        # Build STRtree
        if self.polygons:
            self.tree = self._STRtree(self.polygons)
        else:
            self.tree = None

    def _load_all(self, root: Path, shapefile_module) -> None:
        for pref_dir in sorted(root.iterdir()):
            if not pref_dir.is_dir():
                continue
            shp_files = list(pref_dir.rglob("*.shp"))
            for shp in shp_files:
                try:
                    self._load_one(shp, shapefile_module)
                except Exception as e:
                    print(f"  WARNING: failed to load {shp}: {e}")

    def _load_one(self, shp_path: Path, shapefile_module) -> None:
        sf = shapefile_module.Reader(str(shp_path), encoding="cp932")
        fields = [f[0] for f in sf.fields[1:]]
        idx_004 = fields.index("A29_004")
        idx_002 = fields.index("A29_002") if "A29_002" in fields else None
        idx_003 = fields.index("A29_003") if "A29_003" in fields else None

        for shape_rec in sf.shapeRecords():
            shape = shape_rec.shape
            rec = shape_rec.record
            if shape.shapeTypeName not in ("POLYGON", "POLYGONM", "POLYGONZ"):
                continue
            try:
                use_code = int(rec[idx_004])
            except (ValueError, TypeError):
                continue
            use_cat = A29_USE_MAP.get(use_code)
            if not use_cat:
                continue

            # parts 区切りで multi-polygon を処理
            parts = list(shape.parts) + [len(shape.points)]
            for i in range(len(parts) - 1):
                ring = shape.points[parts[i]:parts[i + 1]]
                if len(ring) < 3:
                    continue
                try:
                    poly = self._Polygon(ring)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_empty:
                        continue
                except Exception:
                    continue
                self.polygons.append(poly)
                self.codes.append(use_code)
                self.use_cats.append(use_cat)
                self.prefs.append(rec[idx_002] if idx_002 is not None else "")
                self.cities.append(rec[idx_003] if idx_003 is not None else "")

    def lookup(self, lat: float, lon: float) -> Optional[dict]:
        """指定の緯度経度を含む用途地域ポリゴンを検索。

        Returns: {"use_category": "commercial"/..., "code": 9, "city": "千代田区"} or None
        """
        if self.tree is None:
            return None
        pt = self._Point(lon, lat)
        # STRtree.query() returns indices in shapely 2.x
        cand_indices = self.tree.query(pt)
        for i in cand_indices:
            poly = self.polygons[int(i)]
            if poly.contains(pt):
                return {
                    "use_category": self.use_cats[int(i)],
                    "code": self.codes[int(i)],
                    "city": self.cities[int(i)],
                    "pref": self.prefs[int(i)],
                    "method": "a29",
                }
        return None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("Loading A29 zoning index...")
    idx = A29ZoningIndex()
    print(f"Loaded {len(idx.polygons):,} polygons")
    # Test lookup at well-known points
    samples = [
        ("Tokyo Sta",  35.6809, 139.7673),
        ("Shibuya Sta", 35.6580, 139.7016),
        ("Marunouchi", 35.6817, 139.7647),
        ("Toyota City", 35.0828, 137.1564),
        ("Yokohama",   35.4659, 139.6224),
    ]
    for name, lat, lon in samples:
        r = idx.lookup(lat, lon)
        if r:
            print(f"  {name:15} ({lat},{lon}) -> {r['use_category']:12} code={r['code']:>3}  {r['city']}")
        else:
            print(f"  {name:15} ({lat},{lon}) -> not found")
