"""50社バッチ品質検証スクリプト"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent / "company_data"

# ゴミ名前パターン
GARBAGE_NAME_PATTERNS = [
    r'\d{4}年', r'入社', r'取締役', r'監査', r'執行', r'代表',
    r'就任', r'退任', r'現在', r'昭和', r'平成', r'令和',
    r'生$', r'^\d+$', r'月', r'社長', r'会長', r'部長',
]

def check_company(filepath):
    """1社分のJSONを検証"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    code = data.get("company_code", "?")
    name = data.get("company_name", "?")
    issues = []
    warnings = []

    # === 従業員 ===
    emp = data.get("employees", {})
    parent = emp.get("parent", {}) if isinstance(emp, dict) else {}
    emp_count = parent.get("employee_count") if parent else None
    salary = parent.get("avg_annual_salary") if parent else None
    if emp_count is None:
        issues.append("従業員数=None")
    elif not isinstance(emp_count, (int, float)):
        issues.append(f"従業員数=不正値({emp_count})")
    if salary is None:
        warnings.append("平均年収=None")
    elif isinstance(salary, (int, float)):
        if salary < 1_000_000 or salary > 50_000_000:
            warnings.append(f"平均年収=範囲外({salary:,.0f})")

    # === 大株主 ===
    sh_data = data.get("major_shareholders", {})
    if isinstance(sh_data, dict):
        shareholders = sh_data.get("major_shareholders", [])
    elif isinstance(sh_data, list):
        shareholders = sh_data
    else:
        shareholders = []
    if not shareholders or len(shareholders) == 0:
        issues.append("大株主=0件")
    else:
        for i, sh in enumerate(shareholders):
            if isinstance(sh, dict):
                ratio = sh.get("ratio_percent")
                if ratio is not None and (ratio < 0 or ratio > 100):
                    warnings.append(f"大株主[{i}]比率={ratio}%")

    # === 役員 ===
    officers_data = data.get("officers", {})
    officers = officers_data.get("officers", [])
    total = officers_data.get("total_count", 0)
    if total == 0 or len(officers) == 0:
        issues.append("役員=0人")
    else:
        garbage_names = []
        for off in officers:
            oname = off.get("name", "")
            if not oname:
                garbage_names.append("(空)")
                continue
            for pat in GARBAGE_NAME_PATTERNS:
                if re.search(pat, oname):
                    garbage_names.append(oname)
                    break
            # 名前が長すぎる (30文字超)
            if len(oname) > 30:
                garbage_names.append(f"{oname[:20]}...(len={len(oname)})")
        if garbage_names:
            issues.append(f"役員名ゴミ混入: {garbage_names[:3]}")

        # 略歴チェック
        date_only_careers = 0
        empty_careers = 0
        for off in officers:
            career = off.get("career", [])
            if isinstance(career, dict):
                # dict形式の場合 (description list)
                descs = career.get("description", [])
                if not descs:
                    empty_careers += 1
            elif isinstance(career, list):
                # list形式の場合 [{date, description}, ...]
                if not career:
                    empty_careers += 1
                else:
                    all_date_only = all(
                        not entry.get("description", "").strip()
                        for entry in career if isinstance(entry, dict)
                    )
                    if all_date_only:
                        date_only_careers += 1
            else:
                empty_careers += 1
        if date_only_careers > 0:
            issues.append(f"略歴が日付のみ: {date_only_careers}人")
        if empty_careers > len(officers) * 0.5:
            warnings.append(f"略歴なし: {empty_careers}/{len(officers)}人")

    # === 子会社 ===
    sub_data = data.get("subsidiaries", {})
    if isinstance(sub_data, dict):
        subsidiaries = sub_data.get("subsidiaries", [])
    elif isinstance(sub_data, list):
        subsidiaries = sub_data
    else:
        subsidiaries = []

    # === 沿革 ===
    history = data.get("history", {})
    events = history.get("events", [])
    if not events or len(events) == 0:
        issues.append("沿革=0件")
    else:
        empty_events = sum(1 for e in events if not e.get("event", "").strip())
        if empty_events > 0:
            issues.append(f"沿革イベント空: {empty_events}/{len(events)}件")

    # === 事業内容 ===
    biz = data.get("business_description", {})
    biz_text = biz.get("text", "") if isinstance(biz, dict) else ""
    biz_len = biz.get("char_count", len(biz_text)) if isinstance(biz, dict) else 0
    if biz_len < 10:
        issues.append(f"事業内容=短い({biz_len}文字)")

    # === リスク ===
    risks = data.get("risk_factors", {})
    risk_text = risks.get("text", "") if isinstance(risks, dict) else ""
    risk_len = risks.get("char_count", len(risk_text)) if isinstance(risks, dict) else 0
    if risk_len < 10:
        issues.append(f"リスク=短い({risk_len}文字)")

    # === R&D ===
    rd = data.get("rd_activities", {})
    rd_text = rd.get("text", "") if isinstance(rd, dict) else ""
    rd_len = rd.get("char_count", len(rd_text)) if isinstance(rd, dict) else 0
    if rd_len < 10:
        warnings.append(f"R&D=短い({rd_len}文字)")

    # 判定
    if issues:
        status = "FAIL" if any("=0" in i or "ゴミ混入" in i or "日付のみ" in i for i in issues) else "WARN"
    else:
        status = "OK"
    if not issues and warnings:
        status = "WARN"

    return {
        "code": code, "name": name, "status": status,
        "issues": issues, "warnings": warnings,
        "stats": {
            "employees": emp_count,
            "salary": salary,
            "shareholders": len(shareholders),
            "officers": len(officers),
            "subsidiaries": len(subsidiaries),
            "history_events": len(events),
            "biz_len": biz_len,
            "risk_len": risk_len,
            "rd_len": rd_len,
        }
    }


def main():
    files = sorted(DATA_DIR.glob("*_data.json"))
    print(f"=== 品質検証: {len(files)}社 ===\n")

    results = {"OK": [], "WARN": [], "FAIL": []}
    all_results = []

    for f in files:
        r = check_company(f)
        results[r["status"]].append(r)
        all_results.append(r)

        mark = {"OK": "OK", "WARN": "WARN", "FAIL": "FAIL"}[r["status"]]
        line = f'[{mark}] {r["code"]} {r["name"]}'
        if r["issues"]:
            line += f'  [{"; ".join(r["issues"])}]'
        if r["warnings"]:
            line += f'  (warn: {"; ".join(r["warnings"])})'
        print(line)

    # サマリー
    print(f"\n{'='*60}")
    print(f"結果サマリー:")
    print(f"  OK:   {len(results['OK'])}社")
    print(f"  WARN: {len(results['WARN'])}社")
    print(f"  FAIL: {len(results['FAIL'])}社")
    print(f"  合計: {len(all_results)}社")
    print(f"  OK率: {len(results['OK'])/len(all_results)*100:.1f}%")

    # 問題パターン集約
    if results["FAIL"] or results["WARN"]:
        print(f"\n{'='*60}")
        print("問題パターン:")
        issue_counts = {}
        for r in results["FAIL"] + results["WARN"]:
            for issue in r["issues"] + r["warnings"]:
                # パターンを正規化
                key = re.sub(r'\(.*?\)', '', issue).strip()
                key = re.sub(r'\[.*?\]', '', key).strip()
                key = re.sub(r': \d+/\d+.*', '', key).strip()
                key = re.sub(r'=\d+文字', '=短い', key).strip()
                if key not in issue_counts:
                    issue_counts[key] = []
                issue_counts[key].append(f'{r["code"]}')

        for pattern, codes in sorted(issue_counts.items(), key=lambda x: -len(x[1])):
            print(f"  {pattern}: {len(codes)}社 -> {', '.join(codes[:10])}")

    # FAIL企業の詳細
    if results["FAIL"]:
        print(f"\n{'='*60}")
        print("FAIL企業詳細:")
        for r in results["FAIL"]:
            print(f"\n  {r['code']} {r['name']}:")
            for issue in r["issues"]:
                print(f"    FAIL: {issue}")
            for w in r["warnings"]:
                print(f"    WARN: {w}")
            print(f"    stats: {r['stats']}")

    # OK企業の統計
    if results["OK"]:
        print(f"\n{'='*60}")
        print("OK企業のstats:")
        for r in results["OK"]:
            s = r["stats"]
            print(f"  {r['code']} {r['name']}: emp={s['employees']} sal={s['salary']:,.0f} sh={s['shareholders']} off={s['officers']} sub={s['subsidiaries']} hist={s['history_events']} biz={s['biz_len']} risk={s['risk_len']} rd={s['rd_len']}")


if __name__ == "__main__":
    main()
