"""
fixed_assets_store の全社設備住所を Nominatim でジオコーディングし、
緯度経度キャッシュを `geocode_cache.json` に保存する。

レート制限: 1 req/sec (Nominatim TOS), User-Agent 必須。
中断・再開可能。既にキャッシュにある住所はスキップ。

実行: python financial_analysis_system/geocode_facilities.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(r"C:/Users/shun nabeno/Desktop/Local LLM Project")
ASSETS_ROOT = PROJECT_ROOT / "fixed_assets_store"
CACHE_PATH = PROJECT_ROOT / "land_price_data" / "geocode_cache.json"
USER_AGENT = "kinmyakucode/1.0 (https://kinmyakucode.com; data@kinmyakucode.com)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# 1 req/sec (Nominatim TOS) + 安全マージン
RATE_LIMIT_SEC = 1.1

# 都道府県名(全47)を文字列先頭にチェックする (.+?県 は貪欲なので使わない)
PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県",
    "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
PREF_PREFIX_RE = re.compile(r"^[\s　]*(" + "|".join(PREFECTURES) + r")")
# 数字始まり / 「ヶ所」「店舗」「事業所」のみ等のジャンク
JUNK_PATTERNS = (
    re.compile(r"^[0-9,]+\s*(ヶ所|店舗|事業所|事業)"),
    re.compile(r"^\(.*?\)$"),  # "(国名)" だけのもの
)


def extract_address_from_parens(addr: str) -> Optional[str]:
    """住所が「ATAMI BAY ... （静岡県熱海市）」形式の場合、括弧内住所を抽出。"""
    s = addr.strip()
    # 末尾の「（都道府県+市区町村）」を抽出
    m = re.search(r"[（(]([^（()）]*?(?:" + "|".join(PREFECTURES) + r")[^（()）]*?)[）)]", s)
    if m:
        candidate = m.group(1).strip()
        if PREF_PREFIX_RE.match(candidate):
            return candidate
    return None


def is_real_address(addr: str) -> bool:
    """住所先頭が都道府県名で、ジャンクパターンに合致しないか。
    括弧書き内に都道府県があれば、それも対象とする (ATAMI BAY例)。"""
    if not addr:
        return False
    s = addr.strip()
    for jp in JUNK_PATTERNS:
        if jp.search(s):
            return False
    if PREF_PREFIX_RE.match(s):
        return True
    # 括弧書き内に都道府県+市区町村があれば、それを抽出して使う
    if extract_address_from_parens(s):
        return True
    return False


def collect_unique_addresses() -> list[str]:
    addrs: set[str] = set()
    skipped = 0
    for fp in ASSETS_ROOT.glob("*/2024_facilities.json"):
        with fp.open(encoding="utf-8") as f:
            d = json.load(f)
        for fac in d.get("facilities", []):
            if fac.get("country") != "JP":
                continue
            addr = (fac.get("address_raw") or "").strip()
            if not addr:
                continue
            if not is_real_address(addr):
                skipped += 1
                continue
            addrs.add(addr)
    print(f"  Filtered out {skipped:,} junk address occurrences")
    # 真の住所のみ、都道府県順にソート
    return sorted(addrs)


def load_cache() -> dict:
    if CACHE_PATH.exists():
        with CACHE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(CACHE_PATH)


def clean_address_for_query(addr: str) -> str:
    """注記/括弧書き/「ほか」等を除去して clean な住所文字列にする。
    住所が括弧内にある場合は、括弧内住所を最優先で抽出。"""
    s = addr.strip()
    # 括弧内に都道府県+市区町村がある場合はそれを取り出す (ATAMI BAY型)
    inner = extract_address_from_parens(s)
    if inner:
        return inner
    # 「ほか」「等」「他」サフィックス
    s = re.sub(r"(ほか.*$|外.*$|他.*$|等$)", "", s)
    # 「（注）」「(注)」等の注記
    s = re.sub(r"[（(]注[）)0-9０-９、, ]*[）)]", "", s)
    # 末尾の括弧 (補足説明) を除去
    s = re.sub(r"[（(][^（()）]*[）)]\s*$", "", s)
    # 全角スペース→半角
    s = s.replace("　", " ").strip()
    return s


def geocode_one(addr: str) -> Optional[dict]:
    query = clean_address_for_query(addr)
    if not query:
        return None
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "countrycodes": "jp",
        "addressdetails": "0",
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if not data:
        return None
    top = data[0]
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name", ""),
        "type": top.get("type", ""),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    addrs = collect_unique_addresses()
    print(f"Unique JP addresses: {len(addrs):,}")
    cache = load_cache()
    print(f"Already cached: {len(cache):,}")
    todo = [a for a in addrs if a not in cache]
    print(f"To geocode: {len(todo):,}")
    if not todo:
        print("Nothing to do. Exiting.")
        return

    save_every = 50
    last_save = time.time()
    success = 0
    failures = 0
    for i, addr in enumerate(todo, start=1):
        result = geocode_one(addr)
        cache[addr] = result if result else {"not_found": True}
        if result and not result.get("error"):
            success += 1
        else:
            failures += 1
        if i % save_every == 0 or time.time() - last_save > 60:
            save_cache(cache)
            last_save = time.time()
            print(f"  [{i}/{len(todo)}] success={success} failed={failures} cached_total={len(cache)}")
        time.sleep(RATE_LIMIT_SEC)
    save_cache(cache)
    print(f"\nDone. success={success}, failed/notfound={failures}, cache_size={len(cache)}")


if __name__ == "__main__":
    main()
