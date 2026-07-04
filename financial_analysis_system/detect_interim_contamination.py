#!/usr/bin/env python3
"""S0 T4: 半期報告書由来のデータが年度ファイル {year}.json として混入した企業を検出・修復する.

原因: 2026-05 の back-fill で、半期報告書 (XBRLコンテキストが InterimDuration) の ZIP が
年度ドキュメントとして抽出され {year}.json に書かれた (例: 1377 サカタのタネ 2026.json、
source_file は「訂正有報」名だが中身は中間期6ヶ月)。年度売上が前年の約50%になり、
前年比 -40〜-60% の減収クラスタが frontend に量産された。

検出ロジック: {year}_raw_tags.json のコンテキスト分布を見る。
  CurrentYear* コンテキストが 0 かつ Interim* コンテキスト > 0 → 中間期ドキュメント確定
修復 (--apply): {year}.json → {year}_Q2.json へ改名 (既存の半期取込の命名慣行に合わせる。
  frontend の load_quarterly_xbrl が *_Q[123].json を quarterlyReports として拾う)。
  既に {year}_Q2.json が存在する場合は {year}.json を削除のみ。
  raw_tags も同様に改名 ({year}_Q2_raw_tags.json が無ければ)。

使い方:
  python detect_interim_contamination.py            # 検出のみ (レポート出力)
  python detect_interim_contamination.py --apply    # 改名/削除を実行
  出力: interim_contamination_report.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "xbrl_store"
YEARS = (2025, 2026, 2027)

RE_CTX = re.compile(r'"context":\s*"([A-Za-z0-9_]+)"')


def classify_raw(raw_path: Path) -> tuple[int, int]:
    """(current_year_count, interim_count) をraw_tagsテキストから数える"""
    txt = raw_path.read_text(encoding="utf-8", errors="replace")
    cur = 0
    interim = 0
    for m in RE_CTX.finditer(txt):
        ctx = m.group(1)
        if ctx.startswith("CurrentYear"):
            cur += 1
        elif ctx.startswith("Interim"):
            interim += 1
    return cur, interim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="検出した年度ファイルを _Q2 へ改名する")
    ap.add_argument("--store", default=None)
    args = ap.parse_args()

    store = Path(args.store) if args.store else STORE
    if not store.is_dir():
        print(f"ERROR: store not found: {store}", file=sys.stderr)
        return 2

    contaminated = []
    no_raw = []
    n_checked = 0

    for comp_dir in sorted(store.iterdir()):
        if not comp_dir.is_dir():
            continue
        for year in YEARS:
            ann = comp_dir / f"{year}.json"
            if not ann.exists():
                continue
            raw = comp_dir / f"{year}_raw_tags.json"
            n_checked += 1
            if not raw.exists():
                # raw が無い場合は source_file 名でのみ判定 (半期 を含めば疑い)
                try:
                    meta = json.loads(ann.read_text(encoding="utf-8"))
                    if "半期" in str(meta.get("source_file", "")):
                        no_raw.append({"dir": comp_dir.name, "year": year, "source_file": meta.get("source_file")})
                except (json.JSONDecodeError, OSError):
                    pass
                continue
            cur, interim = classify_raw(raw)
            if cur == 0 and interim > 0:
                try:
                    meta = json.loads(ann.read_text(encoding="utf-8"))
                    src = meta.get("source_file")
                    rev = (meta.get("data") or {}).get("revenue")
                except (json.JSONDecodeError, OSError):
                    src, rev = None, None
                contaminated.append({
                    "dir": comp_dir.name,
                    "code": comp_dir.name.split("_")[0],
                    "year": year,
                    "interim_tags": interim,
                    "currentyear_tags": cur,
                    "revenue": rev,
                    "source_file": src,
                })

    print(f"checked {n_checked} annual files (years {YEARS})")
    print(f"contaminated (Interim-only): {len(contaminated)}")
    print(f"no-raw suspects (半期 in source_file): {len(no_raw)}")

    report = {"contaminated": contaminated, "no_raw_suspects": no_raw}
    out = Path(__file__).resolve().parent / "interim_contamination_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out}")

    for c in contaminated[:15]:
        print(f"  {c['dir']} {c['year']}: rev={c['revenue']} src={str(c['source_file'])[:60]}")

    if args.apply and contaminated:
        renamed = 0
        deleted = 0
        for c in contaminated:
            comp_dir = store / c["dir"]
            year = c["year"]
            ann = comp_dir / f"{year}.json"
            q2 = comp_dir / f"{year}_Q2.json"
            raw = comp_dir / f"{year}_raw_tags.json"
            q2_raw = comp_dir / f"{year}_Q2_raw_tags.json"
            if q2.exists():
                ann.unlink()
                deleted += 1
            else:
                ann.rename(q2)
                renamed += 1
            if raw.exists() and not q2_raw.exists():
                raw.rename(q2_raw)
        print(f"APPLY: renamed {renamed} -> _Q2, deleted {deleted} (Q2既存)")
    elif contaminated:
        print("(dry-run: --apply で改名を実行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
