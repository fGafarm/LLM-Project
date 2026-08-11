"""v10.5 修正検証テスト — 5社実行"""
import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from Run_integrated_v10_2 import process_single_company, cleanup_company_context

# テスト対象: セグメント問題が確認された企業 + 銀行
TEST_COMPANIES = [
    ("2282", "日本ハム株式会社", "2024", "有報"),        # セグメント全社値コピー問題
    ("2802", "味の素株式会社", "2024", "有報"),          # セグメント二重カウント
    ("6758", "ソニーグループ株式会社", "2024", "有報"),   # IFRS複雑セグメント
    ("7203", "トヨタ自動車株式会社", "2024", "有報"),     # 大型IFRS
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ", "2024", "有報"),  # 銀行業
]

output_base = Path(__file__).parent / "output_v10.5_test"
output_base.mkdir(exist_ok=True)

# RAG DB (dummy if not needed)
try:
    from Run_integrated_v10_2 import LocalRAGDB
    rag_db = LocalRAGDB(str(Path(__file__).parent / "rag_db"))
except Exception:
    rag_db = None

results = []
for code, name, year, doc_type in TEST_COMPANIES:
    print(f"\n{'='*60}")
    print(f"▶ {code} {name}")
    print(f"{'='*60}")
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
        confidence = result.get('confidence', 0)
        conf_level = result.get('confidence_level', 'N/A')
        status = result.get('status', 'unknown')

        # レポートからセグメント部分を抽出して確認
        md_path = result.get('output_files', {}).get('md') if isinstance(result.get('output_files'), dict) else None
        seg_check = "N/A"
        if md_path and Path(md_path).exists():
            with open(md_path, 'r', encoding='utf-8') as f:
                report = f.read()
            # セグメント別ハイライトを抽出
            import re
            seg_match = re.search(r'## セグメント別ハイライト\n(.*?)(?=\n## |\n---)', report, re.DOTALL)
            if seg_match:
                seg_text = seg_match.group(1)
                # 売上高の値を全部取得
                rev_matches = re.findall(r'売上高:\s*([\d,\.]+)億円', seg_text)
                if rev_matches:
                    rev_values = [v for v in rev_matches]
                    unique_count = len(set(rev_values))
                    if unique_count == 1 and len(rev_values) > 1:
                        seg_check = f"❌ 全{len(rev_values)}セグメント同一値: {rev_values[0]}億"
                    else:
                        seg_check = f"✅ {len(rev_values)}セグメント, {unique_count}種類の売上高"
                else:
                    seg_check = "⚠️ 売上高データなし"
            else:
                seg_check = "⚠️ セグメントセクションなし"

            # 品質警告の有無
            has_hallucination_warning = "品質警告" in report
        else:
            has_hallucination_warning = False

        print(f"\n{'─'*40}")
        print(f"  Status: {status}")
        print(f"  Confidence: {confidence:.1f}% ({conf_level})")
        print(f"  Segment: {seg_check}")
        print(f"  Hallucination Warning: {'あり' if has_hallucination_warning else 'なし'}")
        print(f"  Time: {elapsed:.1f}秒")
        print(f"  output_files type: {type(result.get('output_files')).__name__}")

        results.append({
            "code": code,
            "name": name,
            "status": status,
            "confidence": confidence,
            "confidence_level": conf_level,
            "segment_check": seg_check,
            "hallucination_warning": has_hallucination_warning,
            "elapsed": round(elapsed, 1),
        })

    except Exception as e:
        elapsed = time.time() - start
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        results.append({
            "code": code,
            "name": name,
            "status": "error",
            "error": str(e),
            "elapsed": round(elapsed, 1),
        })

    cleanup_company_context()

# サマリー
print(f"\n\n{'='*60}")
print(f"📊 v10.5 テスト結果サマリー")
print(f"{'='*60}")
for r in results:
    status_icon = "✅" if r['status'] == 'success' else "❌"
    conf = r.get('confidence', 0)
    seg = r.get('segment_check', 'N/A')
    hall = "⚠️Hall" if r.get('hallucination_warning') else ""
    print(f"  {status_icon} {r['code']} {r['name'][:10]} | conf={conf:.1f}% | {seg} {hall} | {r['elapsed']}秒")

# JSON保存
summary_path = output_base / "test_summary.json"
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n結果保存: {summary_path}")
