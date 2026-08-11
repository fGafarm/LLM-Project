#!/usr/bin/env python3
"""
セグメント情報の問題パターン分析スクリプト v2
- 実験企業（複数ファイルがある企業）を除外
- 各企業・年度で最新の1ファイルのみを対象
- 50社以上を分析
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
    issue_type: str = "OK"
    issue_details: str = ""

def parse_revenue_value(value_str: str) -> Optional[float]:
    """売上高文字列をパースして百万円単位に変換"""
    if not value_str or value_str.strip().upper() in ['N/A', 'データなし', '(不明)', '不明', '-', '']:
        return None

    value_str = value_str.replace(',', '').replace('、', '')

    # 億円単位
    match = re.search(r'([\d.]+)\s*億円', value_str)
    if match:
        return float(match.group(1)) * 100

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
    pattern2 = r'###\s*([^:\n]+):\s*\n-\s*売上高:\s*([^\n]+)'
    pattern3 = r'\*\*([^*]+)\*\*:\s*[^\n]*売上高:\s*([\d,\.]+(?:億円|百万円)[^\n]*)'

    for pattern in [pattern1, pattern2, pattern3]:
        for match in re.finditer(pattern, segment_section, re.MULTILINE):
            seg_name = match.group(1).strip().rstrip(':')
            revenue_str = match.group(2).strip()

            yoy_match = re.search(r'\(([+\-]?[\d.]+)%\)', revenue_str)
            yoy_str = yoy_match.group(0) if yoy_match else ""

            segment = SegmentData(
                name=seg_name,
                revenue=parse_revenue_value(revenue_str),
                revenue_str=revenue_str,
                yoy=parse_yoy_value(yoy_str),
                yoy_str=yoy_str
            )

            if not any(s.name == segment.name for s in segments):
                segments.append(segment)

    # より柔軟なパターン
    if not segments:
        lines = segment_section.split('\n')
        current_segment = None

        for i, line in enumerate(lines):
            seg_match = re.match(r'^\*\*(.+?(?:セグメント|事業|部門|その他).*?)\*\*', line)
            if seg_match:
                current_segment = seg_match.group(1).strip().rstrip(':')

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

    revenues = [s.revenue for s in segments if s.revenue is not None]
    yoys = [s.yoy for s in segments if s.yoy is not None]
    na_count = sum(1 for s in segments if s.revenue is None)

    if na_count == len(segments):
        analysis.issue_type = "ALL_NA"
        analysis.issue_details = f"全{len(segments)}セグメントの売上高がN/A"
        return analysis

    if na_count > 0:
        analysis.issue_type = "PARTIAL_NA"
        analysis.issue_details = f"{na_count}/{len(segments)}セグメントがN/A"

    if len(revenues) >= 2 and len(set(revenues)) == 1:
        single_value = revenues[0]

        if analysis.total_revenue and abs(single_value - analysis.total_revenue * 100) / (analysis.total_revenue * 100) < 0.01:
            analysis.issue_type = "COPY_FROM_TOTAL"
            analysis.issue_details = f"全{len(revenues)}セグメントが全社売上高（{analysis.total_revenue}億円）と同一"
        else:
            analysis.issue_type = "ALL_SAME"
            analysis.issue_details = f"全{len(revenues)}セグメントが同一売上高（{single_value}百万円）"
        return analysis

    if len(yoys) >= 2 and len(set(yoys)) == 1:
        if analysis.issue_type == "OK":
            analysis.issue_type = "YOY_ALL_SAME"
        else:
            analysis.issue_type += "+YOY_ALL_SAME"
        analysis.issue_details += f"; 全セグメントが同一YoY（{yoys[0]}%）"
        return analysis

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

    filename = os.path.basename(file_path)
    match = re.match(r'porta10_(\d+)_(\d+)_', filename)
    if not match:
        return None

    ticker = match.group(1)
    fiscal_year = int(match.group(2))

    company_match = re.search(r'^# (.+?) 業績レポート', content, re.MULTILINE)
    company_name = company_match.group(1) if company_match else ""

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

    analysis = analyze_segment_issues(analysis)

    return analysis

def main():
    """メイン処理"""
    output_dir = Path("c:/Users/shun nabeno/Desktop/Local LLM Project/financial_analysis_system/output_v10")

    # 実験で使った企業（複数ファイルがある企業）を特定
    company_file_counts = defaultdict(list)  # {ticker_year: [file_paths]}

    for company_dir in sorted(output_dir.iterdir()):
        if not company_dir.is_dir():
            continue

        for md_file in company_dir.glob("porta10_*.md"):
            match = re.match(r'porta10_(\d+)_(\d+)_', md_file.name)
            if match:
                key = f"{match.group(1)}_{match.group(2)}"
                company_file_counts[key].append(str(md_file))

    # 実験企業を除外（2ファイル以上ある企業・年度）
    experiment_keys = {key for key, files in company_file_counts.items() if len(files) >= 2}
    print(f"実験企業（除外対象）: {len(experiment_keys)}件")

    # 実験企業の例を表示
    print("除外企業の例:")
    for key in list(experiment_keys)[:10]:
        ticker = key.split('_')[0]
        files_count = len(company_file_counts[key])
        print(f"  - {key}: {files_count}ファイル")

    # 1ファイルのみの企業・年度を対象にする
    single_file_analyses = []

    for key, files in company_file_counts.items():
        if key in experiment_keys:
            continue

        # 最新のファイルを選択
        latest_file = max(files, key=lambda x: os.path.getmtime(x))
        analysis = process_markdown_file(latest_file)
        if analysis:
            single_file_analyses.append(analysis)

    print(f"\n分析対象ファイル数: {len(single_file_analyses)}")

    # 問題タイプ別に集計
    issue_counts = defaultdict(list)
    for a in single_file_analyses:
        issue_counts[a.issue_type].append(a)

    print("\n" + "=" * 80)
    print("問題タイプ別集計（実験企業除外後）")
    print("=" * 80)

    for issue_type, analyses in sorted(issue_counts.items(), key=lambda x: -len(x[1])):
        print(f"\n【{issue_type}】: {len(analyses)}件")

        for a in analyses[:5]:
            seg_info = ", ".join([f"{s.name}:{s.revenue_str[:30] if s.revenue_str else 'N/A'}"
                                  for s in a.segments[:3]])
            print(f"  - {a.ticker} {a.company_name} ({a.fiscal_year})")
            print(f"    全社売上: {a.total_revenue}億円, セグメント: {seg_info}...")
            print(f"    詳細: {a.issue_details}")

    # 詳細レポートをJSONで保存
    report_data = {
        "summary": {
            "total_files": len(single_file_analyses),
            "excluded_experiment_keys": len(experiment_keys),
            "issue_counts": {k: len(v) for k, v in issue_counts.items()},
        },
        "issue_details": {},
    }

    for issue_type, analyses in issue_counts.items():
        report_data["issue_details"][issue_type] = [
            {
                "ticker": a.ticker,
                "company_name": a.company_name,
                "fiscal_year": a.fiscal_year,
                "total_revenue": a.total_revenue,
                "total_revenue_yoy": a.total_revenue_yoy,
                "segments": [asdict(s) for s in a.segments],
                "issue_details": a.issue_details,
                "file_path": a.file_path
            }
            for a in analyses
        ]

    report_path = output_dir.parent / "segment_analysis_report_v2.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"\n詳細レポート保存先: {report_path}")

    # 論文用サマリー
    print("\n" + "=" * 80)
    print("論文用サマリー（実験企業除外後）")
    print("=" * 80)

    total = len(single_file_analyses)
    ok_count = len(issue_counts.get("OK", []))
    problem_count = total - ok_count

    print(f"""
【分析結果サマリー】
- 分析対象: {total}ファイル（{len(set(a.ticker for a in single_file_analyses))}社）
- 実験企業除外: {len(experiment_keys)}件（複数ファイルがある企業・年度）
- 正常抽出: {ok_count}件 ({ok_count/total*100:.1f}%)
- 問題検出: {problem_count}件 ({problem_count/total*100:.1f}%)

【問題パターン内訳】
""")

    for issue_type in ["ALL_NA", "ALL_SAME", "COPY_FROM_TOTAL", "PARTIAL_NA", "YOY_ALL_SAME", "NO_SEGMENT"]:
        count = len(issue_counts.get(issue_type, []))
        if count > 0:
            print(f"  - {issue_type}: {count}件 ({count/total*100:.1f}%)")

    # 問題企業の詳細リスト
    print("\n" + "=" * 80)
    print("問題企業リスト（詳細）")
    print("=" * 80)

    for issue_type in ["COPY_FROM_TOTAL", "ALL_SAME", "ALL_NA", "YOY_ALL_SAME"]:
        analyses = issue_counts.get(issue_type, [])
        if analyses:
            print(f"\n【{issue_type}】")
            for a in analyses:
                print(f"\n  [*] {a.ticker} {a.company_name} ({a.fiscal_year})")
                print(f"     全社売上: {a.total_revenue}億円（前年比: {a.total_revenue_yoy}%）")
                print(f"     セグメント数: {len(a.segments)}")
                for s in a.segments[:5]:
                    rev_display = f"{s.revenue_str}" if s.revenue_str else "N/A"
                    print(f"       - {s.name}: {rev_display}")
                print(f"     問題: {a.issue_details}")

if __name__ == "__main__":
    main()
