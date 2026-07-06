"""
Rotate Logs — logs/ ディレクトリのローテーション (OPS.md §7).

ポリシー (削除はしない。すべて gzip 圧縮して logs/archive/ へ移動 = 可逆):
  - daily_update_YYYYMMDD.log : 60日より古いもの → archive/
  - recovery_*.log / weekly_audit_*.log : 90日より古いもの → archive/
  - scheduler.log : 5MB 超で archive/scheduler_YYYYMMDD.log.gz へ退避して新規開始
  - edinet_cache/*.json : 対象docType・最小フィールドへスリム化 (yuho_audit の新形式に統一)
    ※ 90日超の削除は yuho_audit.py 側が毎回実施

使い方:
  python rotate_logs.py            # dry-run (何が動くか表示のみ)
  python rotate_logs.py --apply    # 実行
毎週の実行は weekly_audit.bat に組込済み。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
ARCHIVE = LOG_DIR / "archive"
CACHE_DIR = LOG_DIR / "edinet_cache"
DOC_TYPES_TARGET = {"120", "130", "160", "170"}
SLIM_KEYS = ("docID", "secCode", "docTypeCode", "periodEnd", "filerName")


def gzip_move(src: Path, apply: bool) -> str:
    dst = ARCHIVE / (src.name + ".gz")
    if apply:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as f_in, gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        src.unlink()
    return f"{src.name} -> archive/{dst.name}"


def file_date(name: str) -> datetime | None:
    m = re.search(r"(\d{8})", name)
    try:
        return datetime.strptime(m.group(1), "%Y%m%d") if m else None
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="logs/ rotation (gzip to logs/archive, no deletion)")
    ap.add_argument("--apply", action="store_true", help="実行 (省略時は dry-run)")
    args = ap.parse_args()
    now = datetime.now()
    actions: list[str] = []

    # 1) daily_update: 60日超
    for f in sorted(LOG_DIR.glob("daily_update_*.log")):
        d = file_date(f.name)
        if d and (now - d).days > 60:
            actions.append(gzip_move(f, args.apply))

    # 2) recovery / weekly_audit: 90日超
    for pattern in ("recovery_*.log", "weekly_audit_*.log"):
        for f in sorted(LOG_DIR.glob(pattern)):
            d = file_date(f.name)
            if d and (now - d).days > 90:
                actions.append(gzip_move(f, args.apply))

    # 3) scheduler.log: 5MB超で退避
    sched = LOG_DIR / "scheduler.log"
    if sched.exists() and sched.stat().st_size > 5 * 1024 * 1024:
        stamped = LOG_DIR / f"scheduler_{now:%Y%m%d}.log"
        if args.apply:
            sched.rename(stamped)
            actions.append(gzip_move(stamped, True))
        else:
            actions.append(f"scheduler.log ({sched.stat().st_size/1e6:.1f}MB) -> archive/scheduler_{now:%Y%m%d}.log.gz")

    # 4) edinet_cache のスリム化 (旧形式=全提出リスト生データ を最小形式へ)
    slimmed = 0
    saved_bytes = 0
    for cf in sorted(CACHE_DIR.glob("20*.json")) if CACHE_DIR.is_dir() else []:
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        # 既にスリム形式 (キーが最小セットのみ) ならスキップ
        if data and set(data[0].keys()) <= set(SLIM_KEYS):
            continue
        slim = [{k: r.get(k) for k in SLIM_KEYS}
                for r in data if r.get("docTypeCode") in DOC_TYPES_TARGET]
        old_size = cf.stat().st_size
        if args.apply:
            cf.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
            saved_bytes += old_size - cf.stat().st_size
        else:
            saved_bytes += old_size - len(json.dumps(slim, ensure_ascii=False).encode("utf-8"))
        slimmed += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== rotate_logs ({mode}) ===")
    for a in actions:
        print("  " + a)
    print(f"アーカイブ対象: {len(actions)}件 / キャッシュスリム化: {slimmed}件 (約{saved_bytes/1e6:.1f}MB削減)")
    if not args.apply and (actions or slimmed):
        print("実行するには: python rotate_logs.py --apply")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
