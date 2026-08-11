"""
A29 用途地域 (zoning) GeoJSON データを 47 都道府県分ダウンロード + 展開する。

URL pattern: https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-11/A29-11_{NN}_GML.zip
{NN} = 01..47 (都道府県コード)

出力: land_price_data/A29-11/{NN}/A29-11_{NN}.geojson
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
A29_ROOT = PROJECT_ROOT / "land_price_data" / "A29-11"
URL_PATTERN = "https://nlftp.mlit.go.jp/ksj/gml/data/A29/A29-11/A29-11_{:02d}_GML.zip"


def download_one(pref_code: int) -> Path:
    url = URL_PATTERN.format(pref_code)
    out_dir = A29_ROOT / f"{pref_code:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"A29-11_{pref_code:02d}_GML.zip"
    if zip_path.exists() and zip_path.stat().st_size > 100_000:
        return zip_path
    print(f"  Downloading {url}...")
    urllib.request.urlretrieve(url, zip_path)
    return zip_path


def extract(zip_path: Path) -> None:
    out_dir = zip_path.parent
    # Skip if already extracted
    if any(out_dir.glob("*.geojson")):
        return
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(out_dir)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    A29_ROOT.mkdir(parents=True, exist_ok=True)
    total_size = 0
    for code in range(1, 48):
        try:
            zp = download_one(code)
            extract(zp)
            total_size += zp.stat().st_size
            print(f"  [{code:02d}] {zp.stat().st_size/1024:.0f} KB")
        except Exception as e:
            print(f"  [{code:02d}] ERR: {e}")
        time.sleep(0.5)  # 礼儀的なレート制限
    print(f"\nTotal downloaded: {total_size/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
