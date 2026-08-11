#!/usr/bin/env python3
"""v10.4.9 パッチ: number_check.segment_summary を抽出データから直接再計算
既存output JSONを修正（レポート再生成不要）"""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_BASE = Path("./output_v10.2")
PROGRESS_FILE = "batch_225_progress.json"

# v10.4.9 ゴーストフィルタ
GARBAGE_NAMES = ["売上高", "営業利益", "営業収益", "経常収益",
                 "外部顧客に対する 経常収益", "外部顧客に対する経常収益"]
GARBAGE_REGEX = re.compile(r'^[\s−\-—–\d,\.]+$')


def patch_company(code, name):
    """1社のoutput JSONをパッチ。変更があればTrue"""
    company_dir = OUTPUT_BASE / f"{code}_{name}"

    # 抽出ログ読み込み
    ext_log = company_dir / "extraction_logs" / "2024" / "05_セグメント_extraction.json"
    if not ext_log.exists():
        return False, "no extraction log"

    with open(ext_log, 'r', encoding='utf-8') as f:
        ext_data = json.load(f)

    bseg = ext_data.get("extracted", {}).get("business_segments", [])

    # ゴーストフィルタ適用
    bseg = [s for s in bseg
            if not any(g in s.get("name", "") for g in GARBAGE_NAMES)
            and not GARBAGE_REGEX.match(s.get("name", "").replace(" ", ""))]

    if not bseg:
        return False, "no business segments"

    # 単位変換
    unit = bseg[0].get("unit", "百万円")
    unit_mult = 1e6 if "百万" in unit else (1e8 if "億" in unit else 1)

    seg_rev = sum((s.get("revenue") or 0) * unit_mult for s in bseg)
    seg_prof = sum((s.get("profit") or 0) * unit_mult for s in bseg
                   if s.get("profit") is not None)
    has_profit = any(s.get("profit") is not None for s in bseg)

    # output JSON読み込み
    jsons = sorted(company_dir.glob(f"porta102_{code}_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsons:
        return False, "no output json"

    json_path = jsons[0]
    with open(json_path, 'r', encoding='utf-8') as f:
        jdata = json.load(f)

    # XBRL値取得
    xbrl = jdata.get("xbrl_data", {})
    co_rev = xbrl.get("revenue") or xbrl.get("net_sales") or 0
    co_oi = xbrl.get("operating_income") or 0

    # segment_summary再計算
    seg_summary = {}
    if co_rev and seg_rev:
        diff_pct = abs(seg_rev - co_rev) / co_rev * 100 if co_rev else 0
        seg_summary["revenue"] = {
            "segment_total": seg_rev,
            "company_total": co_rev,
            "diff_pct": round(diff_pct, 1),
            "match": diff_pct < 20,
            "segments_used": len(bseg)
        }

    if co_oi and has_profit and seg_prof:
        diff_pct = abs(seg_prof - co_oi) / abs(co_oi) * 100 if co_oi else 0
        seg_summary["operating_income"] = {
            "segment_total": seg_prof,
            "company_total": co_oi,
            "diff_pct": round(diff_pct, 1),
            "match": diff_pct < 20,
            "segments_used": len(bseg)
        }

    # パッチ適用
    if "number_check" not in jdata:
        jdata["number_check"] = {}

    old_summary = jdata["number_check"].get("segment_summary", {})
    jdata["number_check"]["segment_summary"] = seg_summary

    # 保存
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(jdata, f, ensure_ascii=False, indent=2)

    # 変更チェック
    changed = False
    for metric in ["revenue", "operating_income"]:
        old_val = old_summary.get(metric, {})
        new_val = seg_summary.get(metric, {})
        if old_val.get("diff_pct", 999) != new_val.get("diff_pct", 999):
            changed = True

    return changed, seg_summary


def main():
    with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
        progress = json.load(f)

    completed = progress.get("completed", {})
    print(f"=== segment_summary パッチ ({len(completed)}社) ===\n")

    patched = 0
    skipped = 0
    improved = 0
    still_bad = 0

    for code, info in sorted(completed.items()):
        name = info["name"]
        changed, result = patch_company(code, name)

        if isinstance(result, str):
            skipped += 1
            continue

        patched += 1
        if changed:
            # 結果表示
            for metric, vals in result.items():
                diff = vals.get("diff_pct", 0)
                match = "✅" if vals["match"] else "⚠️"
                seg_b = vals["segment_total"] / 1e8
                co_b = vals["company_total"] / 1e8
                if not vals["match"]:
                    still_bad += 1
                    print(f"  {match} {code} {name}: {metric} seg={seg_b:,.0f}億 vs co={co_b:,.0f}億 ({diff:.0f}%)")
                else:
                    improved += 1

    print(f"\n=== 結果 ===")
    print(f"パッチ適用: {patched}社")
    print(f"スキップ: {skipped}社")
    print(f"改善: {improved}件")
    print(f"まだ乖離>20%: {still_bad}件")


if __name__ == "__main__":
    main()
