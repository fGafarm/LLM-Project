#!/usr/bin/env python3
"""batch_225_progress.jsonのconfidence=0を出力JSONから修復"""
import json
from pathlib import Path

PROGRESS_FILE = "batch_225_progress.json"
OUTPUT_BASE = Path("./output_v10.2")

def main():
    with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
        progress = json.load(f)

    fixed = 0
    for code, info in progress["completed"].items():
        if info.get("confidence", 0) > 0:
            continue  # already has confidence

        name = info["name"]
        company_dir = OUTPUT_BASE / f"{code}_{name}"
        if not company_dir.exists():
            continue

        jsons = sorted(
            company_dir.glob(f"porta102_{code}_*.json"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not jsons:
            continue

        try:
            with open(jsons[0], 'r', encoding='utf-8') as jf:
                jdata = json.load(jf)
            cs = jdata.get('phase1_validation', {}).get('confidence_score', {})
            score = cs.get('overall_score', 0)
            if score:
                info["confidence"] = round(score, 2)
                info["confidence_level"] = cs.get('confidence_level', '')
                fixed += 1
                print(f"  {code} {name}: {score:.1f}% ({cs.get('confidence_level', '')})")
        except Exception as e:
            print(f"  {code} {name}: ERROR - {e}")

    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

    total = len(progress["completed"])
    print(f"\n修復: {fixed}/{total}社")

    # 信頼度分布
    confidences = [v["confidence"] for v in progress["completed"].values() if v.get("confidence")]
    if confidences:
        high = sum(1 for c in confidences if c >= 90)
        mid = sum(1 for c in confidences if 80 <= c < 90)
        low = sum(1 for c in confidences if c < 80)
        avg = sum(confidences) / len(confidences)
        print(f"\n信頼度分布:")
        print(f"  HIGH (>=90%): {high}社")
        print(f"  MID (80-89%): {mid}社")
        print(f"  LOW (<80%):   {low}社")
        print(f"  平均: {avg:.1f}%")

if __name__ == "__main__":
    main()
