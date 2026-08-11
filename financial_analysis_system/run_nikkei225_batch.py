#!/usr/bin/env python3
"""日経225全社バッチ実行 - v10.5"""
import sys
import os
import time
import json
import argparse
sys.stdout.reconfigure(encoding='utf-8')

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from Run_integrated_v10_2 import (
    process_single_company, LocalRAGDB,
    infer_industry_from_name
)
from pathlib import Path

def load_companies(year="2025"):
    """nikkei225_codes.txt + xbrl_storeからコード→企業名を解決。該当年度のデータがある企業のみ"""
    store = Path("./xbrl_store")
    codes_file = Path("./nikkei225_codes.txt")

    with open(codes_file) as f:
        codes = [line.strip() for line in f if line.strip()]

    code_to_name = {}
    for d in store.iterdir():
        if d.is_dir() and '_' in d.name:
            parts = d.name.split('_', 1)
            # 該当年度のxbrl_storeデータがあるか確認
            year_json = d / f"{year}.json"
            if year_json.exists():
                code_to_name[parts[0]] = parts[1]

    companies = []
    for code in codes:
        name = code_to_name.get(code)
        if name:
            companies.append((code, name))
        else:
            print(f"  ⚠️ {code}: xbrl_store/{year}.json なし → スキップ")

    return companies

def load_progress(progress_file):
    """前回の進捗をロード（再開用）"""
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"completed": {}, "failed": {}}

def save_progress(progress, progress_file):
    """進捗を保存"""
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def main():
    parser = argparse.ArgumentParser(description="日経225全社バッチ実行")
    parser.add_argument("--year", default="2025", help="対象年度 (default: 2025)")
    parser.add_argument("--output", default="./Report本番用", help="出力先フォルダ (default: ./Report本番用)")
    parser.add_argument("--progress", default=None, help="進捗ファイル (default: batch_progress_{year}.json)")
    args = parser.parse_args()

    year = args.year
    doc_type = "有報"
    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)
    progress_file = args.progress or f"batch_progress_{year}.json"
    rag_db = LocalRAGDB("./rag_db")

    companies = load_companies(year)
    progress = load_progress(progress_file)

    # 既に完了した企業をスキップ
    remaining = [(c, n) for c, n in companies if c not in progress["completed"]]

    total_start = time.time()

    print(f"\n{'='*70}")
    print(f"  日経225 全社バッチ (v10.5)")
    print(f"  年度: {year} / 種別: {doc_type}")
    print(f"  出力先: {output_base.resolve()}")
    print(f"  総数: {len(companies)}社 / 完了済: {len(progress['completed'])}社 / 残り: {len(remaining)}社")
    print(f"{'='*70}\n")

    if not remaining:
        print("  全社処理済みです。再実行する場合は batch_225_progress.json を削除してください。")
        return

    batch_results = []
    already_done = len(progress["completed"])

    for i, (code, name) in enumerate(remaining, 1):
        overall_idx = already_done + i
        industry = infer_industry_from_name(name)
        print(f"\n{'='*70}")
        print(f"  [{overall_idx}/{len(companies)}] {code} {name} (industry={industry})")
        print(f"{'='*70}")

        start = time.time()
        try:
            result = process_single_company(
                company_code=code,
                company_name=name,
                year=year,
                doc_type=doc_type,
                industry="all",
                output_base=output_base,
                rag_db=rag_db
            )
            elapsed = time.time() - start
            status = result.get('status', 'unknown')
            xbrl_items = result.get('xbrl_items', 0)
            sections = result.get('sections', 0)

            # ★ v10.5: result dictから直接confidence取得（primary）
            confidence = result.get('confidence', 0)
            # fallback: output JSONファイルから取得
            if not confidence:
                output_files = result.get('output_files', {})
                json_path = output_files.get('json') if isinstance(output_files, dict) else None
                if not json_path and isinstance(output_files, list) and output_files:
                    json_path = output_files[0]  # レガシー list 形式互換
                if json_path and os.path.exists(json_path):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as jf:
                            jdata = json.load(jf)
                        cs = jdata.get('phase1_validation', {}).get('confidence_score', {})
                        confidence = cs.get('overall_score', 0)
                    except Exception:
                        pass
            # fallback 2: output_v10.2ディレクトリから最新JSONを検索
            if not confidence:
                company_dir = output_base / f"{code}_{name}"
                if company_dir.exists():
                    jsons = sorted(company_dir.glob(f"porta102_{code}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if jsons:
                        try:
                            with open(jsons[0], 'r', encoding='utf-8') as jf:
                                jdata = json.load(jf)
                            cs = jdata.get('phase1_validation', {}).get('confidence_score', {})
                            confidence = cs.get('overall_score', 0)
                        except Exception:
                            pass

            print(f"  -> {status} | XBRL: {xbrl_items}項目 | sections: {sections} | conf: {confidence:.1f}% | {elapsed:.1f}秒")

            progress["completed"][code] = {
                "name": name,
                "status": status,
                "elapsed": round(elapsed, 1),
                "xbrl_items": xbrl_items,
                "sections": sections,
                "confidence": confidence,
            }
            batch_results.append((code, name, status, elapsed, confidence))

        except Exception as e:
            elapsed = time.time() - start
            print(f"  -> ERROR: {e} | {elapsed:.1f}秒")
            import traceback
            traceback.print_exc()
            progress["failed"][code] = {
                "name": name,
                "error": str(e),
                "elapsed": round(elapsed, 1),
            }
            batch_results.append((code, name, f"error: {e}", elapsed, 0))

        # 毎社完了後に進捗保存（中断しても再開可能）
        save_progress(progress, progress_file)

    total_elapsed = time.time() - total_start

    # サマリー出力
    all_completed = progress["completed"]
    all_failed = progress["failed"]

    print(f"\n\n{'='*70}")
    print(f"  日経225 バッチ完了サマリー")
    print(f"  今回: {len(batch_results)}社 ({total_elapsed/60:.1f}分)")
    print(f"  累計: 成功{len(all_completed)}社 / 失敗{len(all_failed)}社")
    print(f"{'='*70}")

    # 信頼度分布
    confidences = [v["confidence"] for v in all_completed.values() if v.get("confidence")]
    if confidences:
        high = sum(1 for c in confidences if c >= 90)
        mid = sum(1 for c in confidences if 80 <= c < 90)
        low = sum(1 for c in confidences if c < 80)
        avg = sum(confidences) / len(confidences)
        print(f"\n  信頼度分布:")
        print(f"    HIGH (≥90%): {high}社")
        print(f"    MID (80-89%): {mid}社")
        print(f"    LOW (<80%): {low}社")
        print(f"    平均: {avg:.1f}%")

    # 失敗企業
    if all_failed:
        print(f"\n  ❌ 失敗企業:")
        for code, info in all_failed.items():
            print(f"    {code} {info['name']}: {info['error']}")

    # 今回の結果
    print(f"\n  今回の処理結果:")
    success = sum(1 for _, _, s, _, _ in batch_results if s == 'success')
    print(f"    成功: {success}/{len(batch_results)}社\n")
    for code, name, status, elapsed, conf in batch_results:
        mark = "✅" if status == "success" else "❌"
        conf_str = f" ({conf:.1f}%)" if conf else ""
        print(f"    {mark} {code} {name}: {status}{conf_str} ({elapsed:.1f}秒)")

if __name__ == "__main__":
    main()
