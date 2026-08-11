"""
国交省「不動産情報ライブラリ」の取引価格情報 (XIT001) を全国分ダウンロード。

取引価格 = 実際に売買された不動産の匿名化価格情報 (アンケートベース)
2005年Q3以降蓄積、四半期ごと、約30万件/年。

API: https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001
認証: Ocp-Apim-Subscription-Key ヘッダ (.env の Fudousan_API_KEY)

出力: land_price_data/transaction_prices/{year}/{quarter}/{pref_code}.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
ENV_PATH = PROJECT_ROOT / "backend" / ".env"
OUTPUT_ROOT = PROJECT_ROOT / "land_price_data" / "transaction_prices"
ENDPOINT = "https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001"

# 取得対象期間 (year, quarter)
YEARS = (2023, 2024)
QUARTERS = (1, 2, 3, 4)

# 47都道府県コード
PREFECTURES = [f"{i:02d}" for i in range(1, 48)]

# レート制限 (TOSに具体記載なし、1秒1リクエストで安全マージン)
RATE_LIMIT_SEC = 0.5


def load_api_key() -> str:
    if not ENV_PATH.exists():
        raise FileNotFoundError(f".env not found at {ENV_PATH}")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().lower() == "fudousan_api_key":
            return v.strip().strip('"\'')
    raise RuntimeError("Fudousan_API_KEY not found in .env")


def fetch_one(api_key: str, year: int, quarter: int, area: str) -> Optional[dict]:
    """1都道府県×1四半期分の取引データ取得。"""
    params = {
        "year": str(year),
        "quarter": str(quarter),
        "area": area,
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Ocp-Apim-Subscription-Key": api_key,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            # レスポンスは gzip-encoded JSON の可能性あり
            try:
                return json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                import gzip
                return json.loads(gzip.decompress(raw).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")[:500]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    api_key = load_api_key()
    print(f"API key loaded (length={len(api_key)})")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    total_requests = len(YEARS) * len(QUARTERS) * len(PREFECTURES)
    print(f"Plan: {len(YEARS)}年 × {len(QUARTERS)}四半期 × {len(PREFECTURES)}都道府県 = {total_requests} requests")
    print(f"Estimated time: {total_requests * RATE_LIMIT_SEC / 60:.1f} minutes")
    print()

    success = 0
    failures = 0
    total_records = 0
    i = 0
    for year in YEARS:
        for quarter in QUARTERS:
            for pref in PREFECTURES:
                i += 1
                out_dir = OUTPUT_ROOT / str(year) / f"Q{quarter}"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"{pref}.json"
                if out_file.exists() and out_file.stat().st_size > 100:
                    # キャッシュあり、スキップ
                    with out_file.open(encoding="utf-8") as f:
                        cached = json.load(f)
                    if not cached.get("error"):
                        success += 1
                        total_records += len(cached.get("data", []))
                        continue
                result = fetch_one(api_key, year, quarter, pref)
                if result is None or result.get("error"):
                    failures += 1
                    if i % 10 == 0 or result and result.get("error"):
                        print(f"  [{i}/{total_requests}] FAIL pref={pref} y={year} q={quarter}: {result}")
                else:
                    success += 1
                    n = len(result.get("data", []))
                    total_records += n
                with out_file.open("w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
                if i % 50 == 0:
                    print(f"  [{i}/{total_requests}] success={success} failed={failures} records={total_records:,}")
                time.sleep(RATE_LIMIT_SEC)

    print(f"\nDone. success={success}, failed={failures}, total_records={total_records:,}")


if __name__ == "__main__":
    main()
