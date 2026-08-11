#!/usr/bin/env python3
"""
セグメント情報の問題パターン分析スクリプト
50社以上のデータを分析し、問題パターンを体系化する
"""

import os
import re
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict

@dataclass
class SegmentData:
    """セグメントデータ"""
    name: str
    revenue: Optional[float] = None  # 百万円単位
    revenue_str: str = ""
    yoy: Optional[float] = None  # %
    yoy_str: str = ""
    profit: Optional[float] = None
    profit_str: str = ""
    source_page: str = ""

@dataclass
class CompanySegmentAnalysis:
    """企業別セグメント分析結果"""
    ticker: str
    company_name: str
    fiscal_year: int
    file_path: str
    total_revenue: Optional[float] = None  # 全社売上高（億円）
    total_revenue_yoy: Optional[float] = None
    segments: List[SegmentData] = field(default_factory=list)

    # 問題検出
    issue_type: str = "OK"  # OK, ALL_NA, ALL_SAME, PARTIAL_NA, YOY_ALL_SAME, COPY_FROM_TOTAL, OTHER
    issue_details: str = ""

def parse_revenue_value(value_str: str) -> Optional[float]:
    """売上高文字列をパースして百万円単位に変換"""
    if not value_str or value_str.strip().upper() in ['N/A', 'データなし', '(不明)', '不明', '-', '']:
        return None

    # 数値部分を抽出
    value_str = value_str.replace(',', '').replace('、', '')

    # 億円単位
    match = re.search(r'([\d.]+)\s*億円', value_str)
    if match:
        return float(match.group(1)) * 100  # 億円 -> 百万円

    # 百万円単位
    match = re.search(r'([\d.]+)\s*百万円', value_str)
    if match:
        return float(match.group(1))

    # 単純な数値（百万円と仮定）
    match = re.search(r'([\d.]+)', value_str)
    if match:
        return float(match.group(1))

    return None

def parse_yoy_value(yoy_str: str) -> Optional[float]:
    """前年比文字列をパース"""
    if not yoy_str or yoy_str.strip().upper() in ['N/A', 'データなし', '-', '']:
        return None

    # パターン: +5.1%, -3.2%, (+5.1%), （+5.1%）
    match = re.search(r'([+\-]?[\d.]+)\s*%', yoy_str)
    if match:
        return float(match.group(1))

    return None

def extract_segments_from_markdown(content: str) -> Tuple[List[SegmentData], Optional[float], Optional[float]]:
    """Markdownからセグメント情報を抽出"""
    segments = []
    total_revenue = None
    total_revenue_yoy = None

    # 全社売上高の抽出
    total_match = re.search(r'売上高:\s*([\d,\.]+)億円\s*\(([+\-]?[\d.]+)%\)', content)
    if total_match:
        total_revenue = float(total_match.group(1).replace(',', ''))
        total_revenue_yoy = float(total_match.group(2))

    # セグメント別ハイライトセクションを抽出
    segment_section_match = re.search(
        r'## セグメント別ハイライト(.*?)(?=## |$)',
        content,
        re.DOTALL
    )

    if not segment_section_match:
        return segments, total_revenue, total_revenue_yoy

    segment_section = segment_section_match.group(1)

    # パターン1: **セグメント名**:\n- 売上高: XXX
    pattern1 = r'\*\*([^*]+(?:セグメント|事業|部門)?)\*\*:\s*\n-\s*売上高:\s*([^\n]+)'

    # パターン2: ### セグメント名:\n- 売上高: XXX
    pattern2 = r'###\s*([^:\n]+):\s*\n-\s*売上高:\s*([^\n]+)'

    # パターン3: **セグメント名**: ... 売上高: XXX
    pattern3 = r'\*\*([^*]+)\*\*:\s*[^\n]*売上高:\s*([\d,\.]+(?:億円|百万円)[^\n]*)'

    for pattern in [pattern1, pattern2, pattern3]:
        for match in re.finditer(pattern, segment_section, re.MULTILINE):
            seg_name = match.group(1).strip().rstrip(':')
            revenue_str = match.group(2).strip()

            # 前年比を探す
            yoy_match = re.search(r'\(([+\-]?[\d.]+)%\)', revenue_str)
            yoy_str = yoy_match.group(0) if yoy_match else ""

            segment = SegmentData(
                name=seg_name,
                revenue=parse_revenue_value(revenue_str),
                revenue_str=revenue_str,
                yoy=parse_yoy_value(yoy_str),
                yoy_str=yoy_str
            )

            # 重複チェック
            if not any(s.name == segment.name for s in segments):
                segments.append(segment)

    # より柔軟なパターンでセグメントを探す
    if not segments:
        # 「セグメント」「事業」を含む行を探す
        lines = segment_section.split('\n')
        current_segment = None

        for i, line in enumerate(lines):
            # セグメント名の行を検出
            seg_match = re.match(r'^\*\*(.+?(?:セグメント|事業|部門|その他).*?)\*\*', line)
            if seg_match:
                current_segment = seg_match.group(1).strip().rstrip(':')

            # 売上高の行を検出
            if current_segment and '売上高' in line:
                revenue_match = re.search(r'売上高[:\s]*([^\n（(]+)', line)
                if revenue_match:
                    revenue_str = revenue_match.group(1).strip()
                    yoy_match = re.search(r'\(([+\-]?[\d.]+)%\)', line)

                    segment = SegmentData(
                        name=current_segment,
                        revenue=parse_revenue_value(revenue_str),
                        revenue_str=revenue_str,
                        yoy=parse_yoy_value(yoy_match.group(0) if yoy_match else ""),
                        yoy_str=yoy_match.group(0) if yoy_match else ""
                    )

                    if not any(s.name == segment.name for s in segments):
                        segments.append(segment)
                    current_segment = None

    return segments, total_revenue, total_revenue_yoy

def analyze_segment_issues(analysis: CompanySegmentAnalysis) -> CompanySegmentAnalysis:
    """セグメント問題を分析"""
    segments = analysis.segments

    if not segments:
        analysis.issue_type = "NO_SEGMENT"
        analysis.issue_details = "セグメント情報が見つからない"
        return analysis

    # 売上高の値を収集
    revenues = [s.revenue for s in segments if s.revenue is not None]
    yoys = [s.yoy for s in segments if s.yoy is not None]
    na_count = sum(1 for s in segments if s.revenue is None)

    # すべてN/A
    if na_count == len(segments):
        analysis.issue_type = "ALL_NA"
        analysis.issue_details = f"全{len(segments)}セグメントの売上高がN/A"
        return analysis

    # 一部N/A
    if na_count > 0:
        analysis.issue_type = "PARTIAL_NA"
        analysis.issue_details = f"{na_count}/{len(segments)}セグメントがN/A"

    # すべて同じ売上高
    if len(revenues) >= 2 and len(set(revenues)) == 1:
        single_value = revenues[0]

        # 全社売上と比較
        if analysis.total_revenue and abs(single_value - analysis.total_revenue * 100) / (analysis.total_revenue * 100) < 0.01:
            analysis.issue_type = "COPY_FROM_TOTAL"
            analysis.issue_details = f"全{len(revenues)}セグメントが全社売上高（{analysis.total_revenue}億円）と同一"
        else:
            analysis.issue_type = "ALL_SAME"
            analysis.issue_details = f"全{len(revenues)}セグメントが同一売上高（{single_value}百万円）"
        return analysis

    # すべて同じYoY
    if len(yoys) >= 2 and len(set(yoys)) == 1:
        if analysis.issue_type == "OK":
            analysis.issue_type = "YOY_ALL_SAME"
        else:
            analysis.issue_type += "+YOY_ALL_SAME"
        analysis.issue_details += f"; 全セグメントが同一YoY（{yoys[0]}%）"
        return analysis

    # 問題なし
    if analysis.issue_type == "OK" or analysis.issue_type == "":
        analysis.issue_type = "OK"
        analysis.issue_details = f"正常: {len(segments)}セグメント、{len(revenues)}個の売上高取得"

    return analysis

def process_markdown_file(file_path: str) -> Optional[CompanySegmentAnalysis]:
    """Markdownファイルを処理"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None

    # ファイル名からメタ情報を抽出
    filename = os.path.basename(file_path)
    match = re.match(r'porta10_(\d+)_(\d+)_', filename)
    if not match:
        return None

    ticker = match.group(1)
    fiscal_year = int(match.group(2))

    # 会社名を抽出
    company_match = re.search(r'^# (.+?) 業績レポート', content, re.MULTILINE)
    company_name = company_match.group(1) if company_match else ""

    # セグメント情報を抽出
    segments, total_revenue, total_revenue_yoy = extract_segments_from_markdown(content)

    analysis = CompanySegmentAnalysis(
        ticker=ticker,
        company_name=company_name,
        fiscal_year=fiscal_year,
        file_path=file_path,
        total_revenue=total_revenue,
        total_revenue_yoy=total_revenue_yoy,
        segments=segments
    )

    # 問題分析
    analysis = analyze_segment_issues(analysis)

    return analysis

def analyze_llm_variance(company_files: Dict[str, List[str]]) -> Dict:
    """同一企業・年度で複数ファイルがある場合のLLM揺れを分析"""
    variance_report = {}

    for key, files in company_files.items():
        if len(files) < 2:
            continue

        analyses = []
        for f in files:
            analysis = process_markdown_file(f)
            if analysis:
                analyses.append(analysis)

        if len(analyses) < 2:
            continue

        # セグメント数の揺れ
        segment_counts = [len(a.segments) for a in analyses]

        # 問題タイプの揺れ
        issue_types = [a.issue_type for a in analyses]

        # 売上高の揺れ（セグメントごと）
        revenue_variance = []
        for i, seg in enumerate(analyses[0].segments):
            if i < len(analyses[0].segments):
                values = []
                for a in analyses:
                    if i < len(a.segments) and a.segments[i].revenue is not None:
                        values.append(a.segments[i].revenue)
                if len(values) >= 2:
                    revenue_variance.append({
                        "segment": seg.name,
                        "values": values,
                        "variance": max(values) - min(values) if values else 0
                    })

        variance_report[key] = {
            "num_files": len(files),
            "segment_counts": segment_counts,
            "issue_types": issue_types,
            "issue_type_variance": len(set(issue_types)) > 1,
            "revenue_variance": revenue_variance
        }

    return variance_report

def main():
    """メイン処理"""
    output_dir = Path("c:/Users/shun nabeno/Desktop/Local LLM Project/financial_analysis_system/output_v10")

    all_analyses = []
    company_files = defaultdict(list)  # {ticker_year: [file_paths]}

    print("=" * 80)
    print("セグメント情報 問題パターン分析")
    print("=" * 80)

    # 全ファイルを処理
    for company_dir in sorted(output_dir.iterdir()):
        if not company_dir.is_dir():
            continue

        for md_file in company_dir.glob("porta10_*.md"):
            # ファイル名からキーを生成
            match = re.match(r'porta10_(\d+)_(\d+)_', md_file.name)
            if match:
                key = f"{match.group(1)}_{match.group(2)}"
                company_files[key].append(str(md_file))

            analysis = process_markdown_file(str(md_file))
            if analysis:
                all_analyses.append(analysis)

    print(f"\n総分析ファイル数: {len(all_analyses)}")

    # 問題タイプ別に集計
    issue_counts = defaultdict(list)
    for a in all_analyses:
        issue_counts[a.issue_type].append(a)

    print("\n" + "=" * 80)
    print("問題タイプ別集計")
    print("=" * 80)

    for issue_type, analyses in sorted(issue_counts.items(), key=lambda x: -len(x[1])):
        print(f"\n【{issue_type}】: {len(analyses)}件")

        # 代表例を表示
        for a in analyses[:5]:
            seg_info = ", ".join([f"{s.name}:{s.revenue_str[:20] if s.revenue_str else 'N/A'}"
                                  for s in a.segments[:3]])
            print(f"  - {a.ticker} {a.company_name} ({a.fiscal_year})")
            print(f"    全社売上: {a.total_revenue}億円, セグメント: {seg_info}...")
            print(f"    詳細: {a.issue_details}")

    # LLM揺れ分析
    print("\n" + "=" * 80)
    print("LLM出力の揺れ分析（同一企業・年度で複数ファイルがあるケース）")
    print("=" * 80)

    variance_report = analyze_llm_variance(company_files)

    variance_with_issues = {k: v for k, v in variance_report.items() if v["issue_type_variance"]}
    print(f"\n問題タイプが揺れているケース: {len(variance_with_issues)}件")

    for key, report in list(variance_with_issues.items())[:10]:
        print(f"\n  {key}:")
        print(f"    ファイル数: {report['num_files']}")
        print(f"    問題タイプ: {report['issue_types']}")
        print(f"    セグメント数: {report['segment_counts']}")

    # 詳細レポートをJSONで保存
    report_data = {
        "summary": {
            "total_files": len(all_analyses),
            "issue_counts": {k: len(v) for k, v in issue_counts.items()},
            "variance_cases": len(variance_with_issues)
        },
        "issue_examples": {},
        "variance_report": variance_report
    }

    for issue_type, analyses in issue_counts.items():
        report_data["issue_examples"][issue_type] = [
            {
                "ticker": a.ticker,
                "company_name": a.company_name,
                "fiscal_year": a.fiscal_year,
                "total_revenue": a.total_revenue,
                "segments": [asdict(s) for s in a.segments],
                "issue_details": a.issue_details
            }
            for a in analyses[:10]
        ]

    report_path = output_dir.parent / "segment_analysis_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"\n詳細レポート保存先: {report_path}")

    # 論文用サマリー
    print("\n" + "=" * 80)
    print("論文用サマリー")
    print("=" * 80)

    total = len(all_analyses)
    ok_count = len(issue_counts.get("OK", []))
    problem_count = total - ok_count

    print(f"""
【分析結果サマリー】
- 分析対象: {total}ファイル（{len(set(a.ticker for a in all_analyses))}社）
- 正常抽出: {ok_count}件 ({ok_count/total*100:.1f}%)
- 問題検出: {problem_count}件 ({problem_count/total*100:.1f}%)

【問題パターン内訳】
""")

    for issue_type in ["ALL_NA", "ALL_SAME", "COPY_FROM_TOTAL", "PARTIAL_NA", "YOY_ALL_SAME", "NO_SEGMENT"]:
        count = len(issue_counts.get(issue_type, []))
        if count > 0:
            print(f"  - {issue_type}: {count}件 ({count/total*100:.1f}%)")

    print(f"""
【LLM出力揺れ】
- 同一入力で異なる出力: {len(variance_with_issues)}ケース
""")

if __name__ == "__main__":
    main()
