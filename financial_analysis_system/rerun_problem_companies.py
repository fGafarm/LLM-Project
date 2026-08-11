#!/usr/bin/env python3
"""v10.4.9 問題企業再実行バッチ - audit_segment_issuesで検出した企業のみ"""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from Run_integrated_v10_2 import process_single_company, LocalRAGDB
from pathlib import Path

OUTPUT_BASE = Path("./output_v10.2")
PROGRESS_FILE = "rerun_progress.json"

# audit_segment_issues.py で検出された問題企業48社
PROBLEM_COMPANIES = [
    ("1605", "株式会社ＩＮＰＥＸ"),
    ("2002", "株式会社日清製粉グループ本社"),
    ("2432", "株式会社ディー・エヌ・エー"),
    ("2501", "サッポロホールディングス株式会社"),
    ("2801", "キッコーマン株式会社"),
    ("2871", "株式会社ニチレイ"),
    ("2914", "日本たばこ産業株式会社"),
    ("3436", "株式会社ＳＵＭＣＯ"),
    ("3659", "株式会社ネクソン"),
    ("4151", "協和キリン株式会社"),
    ("4183", "三井化学株式会社"),
    ("4324", "株式会社電通グループ"),
    ("4452", "花王株式会社"),
    ("4502", "武田薬品工業株式会社"),
    ("4503", "アステラス製薬株式会社"),
    ("4519", "中外製薬株式会社"),
    ("4704", "トレンドマイクロ株式会社"),
    ("4901", "富士フイルムホールディングス株式会社"),
    ("4911", "株式会社資生堂"),
    ("5108", "株式会社ブリヂストン"),
    ("5406", "株式会社　神戸製鋼所"),
    ("5631", "株式会社日本製鋼所"),
    ("5711", "三菱マテリアル株式会社"),
    ("5714", "ＤＯＷＡホールディングス株式会社"),
    ("6103", "オークマ株式会社"),
    ("6146", "株式会社ディスコ"),
    ("6178", "日本郵政株式会社"),
    ("6326", "株式会社　クボタ"),
    ("6503", "三菱電機株式会社"),
    ("6702", "富士通株式会社"),
    ("6753", "シャープ株式会社"),
    ("6762", "ＴＤＫ株式会社"),
    ("6770", "アルプスアルパイン株式会社"),
    ("6861", "株式会社キーエンス"),
    ("6902", "株式会社デンソー"),
    ("6971", "京セラ株式会社"),
    ("6976", "太陽誘電株式会社"),
    ("6981", "株式会社村田製作所"),
    ("7012", "川崎重工業株式会社"),
    ("7205", "日野自動車株式会社"),
    ("7267", "本田技研工業株式会社"),
    ("7735", "株式会社ＳＣＲＥＥＮホールディングス"),
    ("7832", "株式会社バンダイナムコホールディングス"),
    ("7912", "大日本印刷株式会社"),
    ("7974", "任天堂株式会社"),
    ("8001", "伊藤忠商事株式会社"),
    ("8002", "丸紅株式会社"),
    ("8031", "三井物産株式会社"),
]


def load_progress():
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"total": len(PROBLEM_COMPANIES), "completed": {}, "failed": {}}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def main():
    year = "2024"
    doc_type = "有報"
    rag_db = LocalRAGDB("./rag_db")
    progress = load_progress()

    # スキップ済み
    done_codes = set(progress["completed"].keys()) | set(progress["failed"].keys())
    remaining = [(c, n) for c, n in PROBLEM_COMPANIES if c not in done_codes]

    print(f"=== v10.4.9 問題企業再実行 ===")
    print(f"対象: {len(PROBLEM_COMPANIES)}社, 完了: {len(done_codes)}社, 残り: {len(remaining)}社\n")

    for i, (code, name) in enumerate(remaining, 1):
        print(f"\n[{len(done_codes)+i}/{len(PROBLEM_COMPANIES)}] {code} {name}")
        start = time.time()

        try:
            result = process_single_company(
                company_code=code, company_name=name,
                year=year, doc_type=doc_type,
                industry="all", output_base=OUTPUT_BASE, rag_db=rag_db
            )
            elapsed = time.time() - start
            status = result.get('status', 'unknown')

            # confidence取得
            confidence = 0
            company_dir = OUTPUT_BASE / f"{code}_{name}"
            if company_dir.exists():
                jsons = sorted(company_dir.glob(f"porta102_{code}_*.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                if jsons:
                    try:
                        with open(jsons[0], 'r', encoding='utf-8') as jf:
                            jdata = json.load(jf)
                        cs = jdata.get('phase1_validation', {}).get('confidence_score', {})
                        confidence = cs.get('overall_score', 0)
                    except Exception:
                        pass

            print(f"  -> {status} | conf: {confidence:.1f}% | {elapsed:.1f}秒")

            progress["completed"][code] = {
                "name": name, "status": status,
                "elapsed": round(elapsed, 1),
                "confidence": round(confidence, 2)
            }

        except Exception as e:
            elapsed = time.time() - start
            print(f"  -> FAILED: {e} ({elapsed:.1f}秒)")
            progress["failed"][code] = {
                "name": name, "error": str(e),
                "elapsed": round(elapsed, 1)
            }

        save_progress(progress)

    # サマリ
    print(f"\n=== 完了 ===")
    total_done = len(progress["completed"])
    total_fail = len(progress["failed"])
    confs = [v["confidence"] for v in progress["completed"].values() if v.get("confidence")]
    if confs:
        avg = sum(confs) / len(confs)
        high = sum(1 for c in confs if c >= 90)
        print(f"成功: {total_done}/{len(PROBLEM_COMPANIES)}社")
        print(f"失敗: {total_fail}社")
        print(f"平均confidence: {avg:.1f}%")
        print(f"HIGH(>=90%): {high}社")


if __name__ == "__main__":
    main()
