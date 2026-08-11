#!/usr/bin/env python3
"""
Validation Issue Analysis Script
検証エラーのパターン分析スクリプト
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).parent))
from xbrl_validator import FinancialStatementValidator, CompanyCategory


def load_validation_report():
    """検証レポートを読み込み"""
    report_file = Path("xbrl_store/nikkei225_validation_report.json")
    with open(report_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def analyze_company(code: str) -> dict:
    """単一企業の詳細分析"""
    # Find company folder
    store = Path("xbrl_store")
    company_folder = None
    for d in store.iterdir():
        if d.name.startswith(code + "_"):
            company_folder = d
            break

    if not company_folder:
        return {"error": "Company folder not found"}

    json_file = company_folder / "2022.json"
    raw_file = company_folder / "2022_raw_tags.json"

    if not json_file.exists():
        return {"error": "JSON file not found"}

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    result = {
        "code": code,
        "name": data.get("company_name", ""),
        "category": data.get("validation", {}).get("category", "UNKNOWN"),
        "score": data.get("validation", {}).get("overall_score", 0),
        "issues": []
    }

    xbrl = data.get("data", {})
    validation = data.get("validation", {})

    # Analyze BS issues
    bs_checks = validation.get("bs_validations", [])
    for check in bs_checks:
        if not check.get("passed", True):
            result["issues"].append({
                "type": "BS",
                "check": check.get("check_name", ""),
                "diff_pct": check.get("difference_pct", 0),
                "message": check.get("message", "")
            })

    # Analyze PL issues
    pl_checks = validation.get("pl_validations", [])
    for check in pl_checks:
        if not check.get("passed", True):
            result["issues"].append({
                "type": "PL",
                "check": check.get("check_name", ""),
                "diff_pct": check.get("difference_pct", 0),
                "message": check.get("message", "")
            })

    # Analyze CF issues
    cf_checks = validation.get("cf_validations", [])
    for check in cf_checks:
        if not check.get("passed", True):
            result["issues"].append({
                "type": "CF",
                "check": check.get("check_name", ""),
                "diff_pct": check.get("difference_pct", 0),
                "message": check.get("message", "")
            })

    # Check missing fields
    missing = validation.get("missing_fields", [])
    if missing:
        result["missing_fields"] = missing

    # Additional analysis: Check raw data availability
    if raw_file.exists():
        with open(raw_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        result["raw_tags_count"] = raw.get("tags_count", 0)

    return result


def categorize_issues(analyses: list) -> dict:
    """問題をカテゴリ別に分類"""
    categories = defaultdict(list)
    issue_patterns = defaultdict(list)

    for analysis in analyses:
        if "error" in analysis:
            categories["error"].append(analysis)
            continue

        cat = analysis.get("category", "UNKNOWN")
        categories[cat].append(analysis)

        for issue in analysis.get("issues", []):
            pattern_key = f"{issue['type']}:{issue['check']}"
            issue_patterns[pattern_key].append({
                "code": analysis["code"],
                "name": analysis["name"],
                "diff_pct": issue["diff_pct"],
                "category": cat
            })

    return {
        "by_category": dict(categories),
        "by_issue_pattern": dict(issue_patterns)
    }


def main():
    print("=" * 70)
    print("Validation Issue Analysis")
    print("=" * 70)

    # Load report
    report = load_validation_report()
    non_perfect = report.get("non_perfect_companies", [])

    print(f"\nTotal non-perfect companies: {len(non_perfect)}")

    # Analyze each company
    analyses = []
    for company in non_perfect:
        code = company["company_code"]
        analysis = analyze_company(code)
        analyses.append(analysis)

    # Categorize issues
    categorized = categorize_issues(analyses)

    # Print results by category
    print("\n" + "=" * 70)
    print("Issues by Company Category")
    print("=" * 70)

    for cat, companies in sorted(categorized["by_category"].items()):
        print(f"\n### {cat} ({len(companies)} companies) ###")

        # Group by score range
        score_groups = defaultdict(list)
        for c in companies:
            score_bucket = int(c.get("score", 0) // 10) * 10
            score_groups[score_bucket].append(c)

        for score, group in sorted(score_groups.items()):
            print(f"\n  Score {score}-{score+9}%:")
            for c in group[:5]:  # Show top 5
                print(f"    [{c['code']}] {c['name'][:20]}: {c.get('score', 0)}%")
                for issue in c.get("issues", [])[:2]:
                    print(f"      - {issue['type']}: {issue['check'][:40]} ({issue['diff_pct']}%)")

    # Print common issue patterns
    print("\n" + "=" * 70)
    print("Common Issue Patterns")
    print("=" * 70)

    sorted_patterns = sorted(
        categorized["by_issue_pattern"].items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    for pattern, companies in sorted_patterns[:15]:
        print(f"\n### {pattern} ({len(companies)} companies) ###")
        # Show sample companies
        for c in companies[:5]:
            print(f"  [{c['code']}] {c['name'][:20]} ({c['category']}): diff={c['diff_pct']}%")

    # Save detailed analysis
    output_file = Path("xbrl_store/validation_issue_analysis.json")
    analysis_result = {
        "summary": {
            "total_non_perfect": len(non_perfect),
            "by_category_count": {k: len(v) for k, v in categorized["by_category"].items()},
            "issue_patterns_count": {k: len(v) for k, v in categorized["by_issue_pattern"].items()}
        },
        "analyses": analyses,
        "categorized": categorized
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(analysis_result, f, ensure_ascii=False, indent=2)

    print(f"\n\nDetailed analysis saved to: {output_file}")

    # Return key statistics for further processing
    return analysis_result


if __name__ == "__main__":
    main()
