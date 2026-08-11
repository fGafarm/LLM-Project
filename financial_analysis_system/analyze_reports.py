#!/usr/bin/env python3
"""
10社レポートの品質分析スクリプト
共通問題点を抽出して改善策を提案
"""

import re
from pathlib import Path
from collections import defaultdict

# 分析対象企業
COMPANIES = [
    ("7203", "トヨタ自動車株式会社"),
    ("6758", "ソニーグループ株式会社"),
    ("1301", "株式会社　極洋"),
    ("9984", "ソフトバンクグループ株式会社"),
    ("7974", "任天堂株式会社"),
    ("9983", "株式会社ファーストリテイリング"),
    ("4063", "信越化学工業株式会社"),
    ("6501", "株式会社日立製作所"),
    ("2914", "日本たばこ産業株式会社"),
    ("8306", "株式会社三菱ＵＦＪフィナンシャル・グループ"),
]

OUTPUT_BASE = Path("output_v10")

def find_latest_report(company_code: str) -> Path:
    """最新のレポートファイルを見つける"""
    company_dirs = list(OUTPUT_BASE.glob(f"{company_code}_*"))
    if not company_dirs:
        return None

    company_dir = company_dirs[0]
    md_files = sorted(company_dir.glob("porta10_*_2022_*.md"), reverse=True)

    # 最新のもの（タイムスタンプが最も新しい）を返す
    return md_files[0] if md_files else None

def extract_scoreboard(content: str) -> dict:
    """「数字で見る2022年」セクションから主要指標を抽出"""
    scoreboard_match = re.search(r'## 数字で見る2022年(.+?)##', content, re.DOTALL)
    if not scoreboard_match:
        return {}

    scoreboard = scoreboard_match.group(1)

    indicators = {}
    patterns = {
        'operating_income': r'営業利益[:：]\s*([^\(\n]+)',
        'dividend_per_share': r'1株配当[:：]\s*([^\(\n]+)',
        'interest_bearing_debt': r'有利子負債.*[:：]\s*([^\(\n]+)',
        'ebitda': r'EBITDA[:：]\s*([^\(\n]+)',
        'net_debt': r'Net Debt.*[:：]\s*([^\(\n]+)',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, scoreboard)
        if match:
            value = match.group(1).strip()
            indicators[key] = value

    return indicators

def extract_segment_yoy(content: str) -> list:
    """セグメント別ハイライトからYoY%を抽出"""
    segment_match = re.search(r'## セグメント別ハイライト(.+?)##', content, re.DOTALL)
    if not segment_match:
        return []

    segment_section = segment_match.group(1)

    # 売上高のYoY%を抽出（例: 売上高: 120,796百万円（+1.8%））
    yoy_pattern = r'売上高[：:][^\(]*\(([+-]?\d+\.?\d*)%\)'
    yoy_matches = re.findall(yoy_pattern, segment_section)

    return [float(y) for y in yoy_matches]

def extract_revenue_drivers(content: str) -> str:
    """売上高要因分析セクションを抽出"""
    driver_match = re.search(r'### 売上高.*?の要因(.+?)###', content, re.DOTALL)
    if not driver_match:
        return ""

    return driver_match.group(1).strip()[:500]  # 最初の500文字

def analyze_report(report_path: Path) -> dict:
    """個別レポートを分析"""
    if not report_path or not report_path.exists():
        return None

    content = report_path.read_text(encoding='utf-8')

    scoreboard = extract_scoreboard(content)
    segment_yoy = extract_segment_yoy(content)
    revenue_drivers = extract_revenue_drivers(content)

    # 問題点の検出
    issues = []

    # 1. N/A問題
    if scoreboard.get('operating_income') == 'N/A':
        issues.append('営業利益がN/A')
    if scoreboard.get('dividend_per_share') == 'N/A':
        issues.append('1株配当がN/A')
    if scoreboard.get('interest_bearing_debt') == 'N/A':
        issues.append('有利子負債がN/A')
    if scoreboard.get('ebitda') == 'N/A':
        issues.append('EBITDAがN/A')

    # 2. セグメントYoY重複問題
    if len(segment_yoy) >= 3:
        if len(set(segment_yoy)) == 1:
            issues.append(f'全セグメントYoYが同一 ({segment_yoy[0]}%)')
        elif len([y for y in segment_yoy if abs(y - segment_yoy[0]) < 0.1]) >= len(segment_yoy) * 0.7:
            issues.append(f'セグメントYoYの70%以上が類似 ({segment_yoy[0]}%付近)')

    # 3. 抽象的な要因分析
    if revenue_drivers:
        abstract_patterns = ['不明', '新製品の販売拡大', '既存製品の需要増加', '顧客や産業の課題解決']
        if any(pattern in revenue_drivers for pattern in abstract_patterns):
            issues.append('売上高要因が抽象的')

    return {
        'path': str(report_path),
        'scoreboard': scoreboard,
        'segment_yoy': segment_yoy,
        'issues': issues,
        'revenue_drivers_preview': revenue_drivers[:200] if revenue_drivers else ""
    }

def main():
    print("=" * 70)
    print("10社レポート品質分析")
    print("=" * 70)
    print()

    all_issues = defaultdict(list)
    results = []

    for code, name in COMPANIES:
        report_path = find_latest_report(code)
        if not report_path:
            print(f"NG: {code} {name}: レポートが見つかりません")
            continue

        result = analyze_report(report_path)
        if not result:
            print(f"NG: {code} {name}: 分析失敗")
            continue

        results.append((code, name, result))

        print(f"\n[{code}] {name}")
        print(f"   ファイル: {report_path.name}")

        if result['issues']:
            print(f"   問題点: {len(result['issues'])}件")
            for issue in result['issues']:
                print(f"      - {issue}")
                all_issues[issue].append(code)
        else:
            print(f"   OK: 問題なし")

        # セグメントYoY表示
        if result['segment_yoy']:
            print(f"   セグメントYoY: {result['segment_yoy']}")

    # 共通問題の集計
    print("\n" + "=" * 70)
    print("共通問題点の集計")
    print("=" * 70)

    for issue, companies in sorted(all_issues.items(), key=lambda x: -len(x[1])):
        print(f"\n[問題] {issue}")
        print(f"   影響企業数: {len(companies)}/10社")
        print(f"   企業: {', '.join(companies)}")

    # 改善提案
    print("\n" + "=" * 70)
    print("改善提案")
    print("=" * 70)

    if '営業利益がN/A' in all_issues:
        print(f"\n1. 営業利益N/A問題 ({len(all_issues['営業利益がN/A'])}社)")
        print("   => xbrl_batch_extractor.pyでGP-SGA計算ロジック追加済み")
        print("   => XBRL再抽出が必要")

    if '1株配当がN/A' in all_issues:
        print(f"\n2. 1株配当N/A問題 ({len(all_issues['1株配当がN/A'])}社)")
        print("   => xbrl_batch_extractor.pyで配当計算ロジック追加済み")
        print("   => XBRL再抽出が必要")

    segment_issues = [k for k in all_issues.keys() if 'セグメントYoY' in k]
    if segment_issues:
        total = sum(len(all_issues[k]) for k in segment_issues)
        print(f"\n3. セグメントYoY問題 ({total}社)")
        print("   => segment_revenue/segment_profitプロンプト改善済み")
        print("   => レポート再生成が必要")

    if '売上高要因が抽象的' in all_issues:
        print(f"\n4. 売上高要因分析の抽象性 ({len(all_issues['売上高要因が抽象的'])}社)")
        print("   => revenue_drivers/profit_driversプロンプト改善済み")
        print("   => レポート再生成が必要")

    print()

if __name__ == "__main__":
    main()
