#!/usr/bin/env python3
"""
Company Data Extractor - Split PDF → Structured JSON
有価証券報告書の分割PDFから企業情報を構造化抽出する

対象: 従業員, 大株主, 役員, 子会社, 沿革, 事業内容, リスク, 研究開発
LLM不使用 - pdfplumber + regex のみ
"""

import pdfplumber
import re
import json
import sys
import io
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PDF_BASE = Path("E:/PDF/本番用PDF分割")
OUTPUT_DIR = Path("financial_analysis_system/company_data") if not Path("company_data").exists() else Path("company_data")


def clean_number(s):
    """文字列から数値を抽出"""
    if not s:
        return None
    s = str(s).replace('\n', '').replace(' ', '').replace('　', '')
    s = s.replace('△', '-').replace('－', '-').replace('―', '-').replace('–', '-')
    m = re.search(r'(-?[\d,]+)', s)
    if m:
        return int(m.group(1).replace(',', ''))
    return None


def clean_float(s):
    """文字列から小数を抽出"""
    if not s:
        return None
    s = str(s).replace('\n', '').replace(' ', '').replace('　', '')
    m = re.search(r'(-?[\d,.]+)', s)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            return None
    return None


def safe_text(page):
    """ページからテキストを安全に抽出"""
    try:
        return page.extract_text() or ""
    except Exception:
        return ""


def safe_tables(page):
    """ページからテーブルを安全に抽出"""
    try:
        return page.extract_tables() or []
    except Exception:
        return []


# =============================================================================
# 1. 従業員情報
# =============================================================================
def extract_employees(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "01_会社概要" / "06_従業員の状況.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    result = {"parent": {}, "segments": []}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = safe_tables(page)
                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # 全行を走査してヘッダー行を探す（1行目とは限らない）
                    for row_idx, row in enumerate(table):
                        row_text = " ".join([str(c) for c in row if c])
                        if "平均年齢" in row_text and ("従業員" in row_text or "人" in row_text):
                            # 千円単位の検出
                            salary_unit = 1
                            if "千円" in row_text:
                                salary_unit = 1000
                            # テキストから千円単位を検出（テーブルに給与列がない場合用）
                            page_text = page.extract_text() or ""
                            if "千円" in page_text and "平均年間給与" in page_text:
                                salary_unit = 1000

                            # ヘッダー列名と位置のマッピング
                            col_map = {}
                            for ci, cell in enumerate(row):
                                cell_str = str(cell) if cell else ""
                                if "従業員数" in cell_str:
                                    col_map["emp"] = ci
                                elif "平均年齢" in cell_str:
                                    col_map["age"] = ci
                                elif "平均勤続" in cell_str:
                                    col_map["tenure"] = ci
                                elif "平均年間給与" in cell_str:
                                    col_map["salary"] = ci

                            # データ行はヘッダーの次
                            if row_idx + 1 < len(table):
                                data_row = table[row_idx + 1]
                                emp_idx = col_map.get("emp", 0)
                                emp_str = str(data_row[emp_idx]) if emp_idx < len(data_row) and data_row[emp_idx] else ""
                                emp_match = re.search(r'([\d,]+)', emp_str)
                                temp_match = re.search(r'[\[\(（]\s*([\d,]+)\s*[\]\)）]', emp_str)
                                # 臨時従業員が別列にある場合
                                if not temp_match and emp_idx + 1 < len(data_row):
                                    next_cell = str(data_row[emp_idx + 1]) if data_row[emp_idx + 1] else ""
                                    temp_match = re.search(r'[\[\(（]\s*([\d,]+)\s*[\]\)）]', next_cell)

                                age_idx = col_map.get("age", 1)
                                tenure_idx = col_map.get("tenure", 2)
                                salary_idx = col_map.get("salary", 3)
                                raw_salary = clean_number(data_row[salary_idx]) if salary_idx < len(data_row) else None

                                # テーブルに給与列がない場合、テキストからフォールバック取得
                                if raw_salary is None and "salary" not in col_map:
                                    # テキストから「従業員数 年齢 勤続 給与」パターンを探す
                                    sal_m = re.search(r'[\d,]+[〔\[（].+?[〕\]）]\s+[\d\.]+\s+[\d\.]+\s+([\d,]+)', page_text)
                                    if sal_m:
                                        raw_salary = clean_number(sal_m.group(1))

                                result["parent"] = {
                                    "employee_count": int(emp_match.group(1).replace(',', '')) if emp_match else None,
                                    "temp_employee_count": int(temp_match.group(1).replace(',', '')) if temp_match else None,
                                    "avg_age": clean_float(data_row[age_idx]) if age_idx < len(data_row) else None,
                                    "avg_tenure_years": clean_float(data_row[tenure_idx]) if tenure_idx < len(data_row) else None,
                                    "avg_annual_salary": raw_salary * salary_unit if raw_salary else None,
                                }
                            break

                    # セグメント別従業員数
                    header = " ".join([str(c) for c in table[0] if c])
                    if "セグメント" in header and "従業員" in header:
                        for row in table[1:]:
                            if not row or not row[0]:
                                continue
                            seg_name = str(row[0]).strip().replace('\n', '')
                            if "合計" in seg_name or seg_name == "計" or "全社" in seg_name:
                                continue
                            emp_str = str(row[1]) if len(row) > 1 and row[1] else ""
                            emp_match = re.search(r'([\d,]+)', emp_str)
                            temp_match = re.search(r'\[\s*([\d,]+)\s*\]', emp_str)
                            if emp_match:
                                result["segments"].append({
                                    "segment": seg_name,
                                    "employee_count": int(emp_match.group(1).replace(',', '')),
                                    "temp_count": int(temp_match.group(1).replace(',', '')) if temp_match else None,
                                })
    except Exception as e:
        result["error"] = str(e)

    return result


# =============================================================================
# 2. 大株主
# =============================================================================
def extract_shareholders(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "06_ガバナンス" / "02_株式等の状況.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    result = {"major_shareholders": [], "total_shares_issued": None, "dividend_per_share": None}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = safe_tables(page)
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = " ".join([str(c) for c in table[0] if c])

                    # 大株主テーブル: 氏名/名称 + 所有株式数 + 割合
                    # ヘッダーのスペース除去して判定（「氏 名」→「氏名」）
                    header_nospace = header.replace(' ', '').replace('　', '')
                    if ("氏名" in header_nospace or "名称" in header_nospace or "株主" in header_nospace) and ("割合" in header_nospace or "所有株式" in header_nospace):
                        for row in table[1:]:
                            if not row or not row[0]:
                                continue
                            raw_name = str(row[0]).strip()

                            # 改行で分割 → 複数株主が1セルに入っているケースを検出
                            name_lines = [l.strip() for l in raw_name.split('\n') if l.strip()]
                            name_lines = [l for l in name_lines if not ("計" in l and len(l) <= 3)]

                            # 各数値セルも改行で分割
                            col_lines = {}
                            for idx, cell in enumerate(row[1:], 1):
                                cell_str = str(cell).strip() if cell else ""
                                lines = [l.strip().replace(',', '') for l in cell_str.split('\n') if l.strip()]
                                col_lines[idx] = lines

                            # 複数株主が結合されているか判定
                            if len(name_lines) > 1:
                                # 数値列から株数・割合のペアを再構成
                                shares_list = []
                                ratio_list = []
                                for idx in sorted(col_lines.keys()):
                                    vals = col_lines[idx]
                                    # 整数列(株数) vs 小数列(割合)を判定
                                    has_dot = any('.' in v for v in vals if re.match(r'^[\d,.]+$', v))
                                    for v in vals:
                                        if not re.match(r'^[\d,.]+$', v):
                                            continue
                                        if has_dot:
                                            ratio_list.append(clean_float(v))
                                        else:
                                            shares_list.append(clean_number(v))

                                # 株数の行数を実エントリ数の基準にする
                                n_entries = max(len(shares_list), len(ratio_list), 1)

                                # 名前行が多い場合は折り返しをマージ
                                if len(name_lines) > n_entries:
                                    merged_names = []
                                    for nl in name_lines:
                                        # 名前が短く、前のエントリの続きっぽい場合はマージ
                                        if merged_names and len(merged_names) < n_entries + 1:
                                            # まだエントリ数に達していなければ独立名
                                            merged_names.append(nl)
                                        elif merged_names:
                                            # エントリ数超過 → 前の名前に結合
                                            merged_names[-1] += nl
                                        else:
                                            merged_names.append(nl)
                                    # エントリ数に合わせて再マージ
                                    while len(merged_names) > n_entries and len(merged_names) > 1:
                                        # 最短の名前を前の名前に結合
                                        min_idx = min(range(1, len(merged_names)),
                                                      key=lambda i: len(merged_names[i]))
                                        merged_names[min_idx - 1] += merged_names[min_idx]
                                        merged_names.pop(min_idx)
                                    name_lines = merged_names

                                for i, nm in enumerate(name_lines):
                                    if not nm:
                                        continue
                                    sh = shares_list[i] if i < len(shares_list) else None
                                    rt = ratio_list[i] if i < len(ratio_list) else None
                                    result["major_shareholders"].append({
                                        "name": nm,
                                        "shares_thousands": sh,
                                        "ratio_percent": rt,
                                    })
                            else:
                                # 通常の1株主1行ケース
                                name = raw_name.replace('\n', ' ')
                                if "計" in name and len(name) <= 3:
                                    continue

                                numerics = []
                                for idx, cell in enumerate(row[1:], 1):
                                    cell_str = str(cell).strip().replace('\n', '').replace(',', '') if cell else ""
                                    if re.match(r'^[\d,.]+$', cell_str):
                                        numerics.append((idx, cell_str))

                                shares = None
                                ratio = None
                                if len(numerics) >= 2:
                                    shares = clean_number(row[numerics[-2][0]])
                                    ratio = clean_float(row[numerics[-1][0]])
                                elif len(numerics) == 1:
                                    ratio = clean_float(row[numerics[0][0]])

                                if name:
                                    result["major_shareholders"].append({
                                        "name": name,
                                        "shares_thousands": shares,
                                        "ratio_percent": ratio,
                                    })

                    # 発行済株式総数
                    if "発行済" in header and "株式" in header:
                        for row in table[1:]:
                            for cell in row:
                                cell_str = str(cell).replace('\n', '').replace(',', '') if cell else ""
                                m = re.search(r'(\d{8,})', cell_str)
                                if m and result["total_shares_issued"] is None:
                                    result["total_shares_issued"] = int(m.group(1))

                    # 配当
                    if "配当" in header and "１株当たり" in header:
                        for row in table[1:]:
                            for cell in row:
                                cell_str = str(cell).strip() if cell else ""
                                m = re.match(r'^[\d.]+$', cell_str)
                                if m:
                                    val = float(m.group(0))
                                    if 0 < val < 100000:
                                        result["dividend_per_share"] = val
    except Exception as e:
        result["error"] = str(e)

    return result


# =============================================================================
# 3. 役員一覧
# =============================================================================
def _clean_officer_name(raw_name):
    """役員名をクリーンアップ"""
    if not raw_name:
        return ""
    name = str(raw_name).replace('\n', ' ').strip()
    # 英語表記の括弧部分を除去: (Christophe Weber) etc.
    name = re.sub(r'\([A-Za-z\s\.\-\']+\)', '', name)
    # 全角括弧の英語表記も除去
    name = re.sub(r'（[A-Za-z\s\.\-\']+）', '', name)
    # 「豊 田 章 男」→「豊田章男」（字間スペース除去）
    name = re.sub(r'\s+', '', name)
    # 末尾の「生」を除去（生年月日セルの混入対策）
    name = re.sub(r'生$', '', name)
    return name.strip()


def _parse_career(career_text):
    """略歴テキストから構造化された経歴リストを抽出"""
    if not career_text:
        return []
    text = str(career_text).replace('\n', ' ')
    entries = re.findall(
        r'(\d{4}年\s*\d{1,2}月)\s*(.+?)(?=\d{4}年\s*\d{1,2}月|$)',
        text
    )
    career = []
    for date, desc in entries:
        desc_clean = re.sub(r'\s+', ' ', desc.strip())
        if desc_clean and len(desc_clean) > 1:
            career.append({"date": re.sub(r'\s+', '', date), "description": desc_clean})
    return career[:20]


def _is_officer_header(row):
    """テーブルのヘッダー行かどうか判定"""
    if not row:
        return False
    row_text = " ".join([str(c) for c in row if c])
    return "氏名" in row_text and ("生年月日" in row_text or "役職" in row_text)


def _find_col_indices(header_row):
    """ヘッダー行から各列のインデックスを特定"""
    cols = {"position": None, "name": None, "birthdate": None,
            "career": [], "tenure": None, "shares": None}
    for i, cell in enumerate(header_row):
        c = str(cell).replace('\n', '').replace(' ', '') if cell else ""
        if "役職" in c:
            cols["position"] = i
        elif "氏名" in c:
            cols["name"] = i
        elif "生年月日" in c:
            cols["birthdate"] = i
        elif "略歴" in c:
            cols["career"].append(i)
        elif "任期" in c:
            cols["tenure"] = i
        elif "所有株式" in c or "株式数" in c:
            cols["shares"] = i
    # None列が略歴列の直後にある場合、略歴の続き（説明列）として追加
    if cols["career"]:
        for i, cell in enumerate(header_row):
            if cell is None and i > 0 and (i - 1) in cols["career"]:
                cols["career"].append(i)
    else:
        # 略歴列が見つからない場合、None列を略歴候補に
        for i, cell in enumerate(header_row):
            if cell is None and i > 0:
                cols["career"].append(i)
    return cols


def extract_officers(company_dir: Path, year: str) -> dict:
    gov_dir = company_dir / f"{year}_有報" / "06_ガバナンス"
    if not gov_dir.exists():
        return {"error": "dir_not_found"}
    # 役員の状況PDFを探す（03_ or 04_ のどちらでもマッチ）
    pdf_path = None
    for f in sorted(gov_dir.glob("*.pdf")):
        if "役員" in f.name:
            pdf_path = f
            break
    if not pdf_path:
        return {"error": "officer_pdf_not_found"}

    officers = []
    col_indices = None
    shares_unit_text = ""  # (千株) or (株) or (百株)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # ページテキストで役員テーブル外セクションを検出 → スキップ
                page_text = safe_text(page)
                # 「執行役員」の一覧表（取締役テーブル外）や報酬セクションに入ったら終了
                if any(kw in page_text for kw in ['役員の報酬', '報酬等の額', '監査の状況', '監査法人']):
                    # ただし同ページに役職名テーブルがある場合はまだ抽出する
                    tables = safe_tables(page)
                    has_officer_table = False
                    for t in tables:
                        if t and len(t) >= 2 and _is_officer_header(t[0]):
                            has_officer_table = True
                    if not has_officer_table:
                        break

                tables = safe_tables(page)
                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # ヘッダー行の検出
                    if _is_officer_header(table[0]):
                        col_indices = _find_col_indices(table[0])
                        # 株式単位の検出
                        hdr_text = " ".join([str(c) for c in table[0] if c])
                        if "千株" in hdr_text:
                            shares_unit_text = "千株"
                        elif "百株" in hdr_text:
                            shares_unit_text = "百株"
                        data_rows = table[1:]
                    elif col_indices:
                        # 前ページからの継続テーブル（ヘッダーなし）
                        data_rows = table
                    else:
                        continue

                    if not col_indices or col_indices["name"] is None:
                        continue

                    for row in data_rows:
                        if not row:
                            continue
                        # 氏名セル
                        ni = col_indices["name"]
                        if ni >= len(row) or not row[ni]:
                            continue

                        raw_name = str(row[ni]).strip()
                        name = _clean_officer_name(raw_name)

                        # 名前の妥当性チェック: 2-30文字、数字のみ/「計」等は除外
                        if not name or len(name) < 2 or len(name) > 30:
                            continue
                        if re.match(r'^[\d,.\s計合注（）()]+$', name):
                            continue
                        # 集計行をスキップ
                        if name in ['計', '合計'] or name.startswith('計') or '合計' in name:
                            continue

                        # 役職
                        position = ""
                        pi = col_indices["position"]
                        if pi is not None and pi < len(row) and row[pi]:
                            position = str(row[pi]).replace('\n', ' ').strip()

                        # 生年月日
                        birthdate = ""
                        bi = col_indices["birthdate"]
                        if bi is not None and bi < len(row) and row[bi]:
                            bd_raw = str(row[bi]).replace('\n', '').replace(' ', '').replace('生', '')
                            # YYYY年M月D日 を抽出
                            bd_match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', bd_raw)
                            if bd_match:
                                birthdate = bd_match.group(1)
                            else:
                                birthdate = bd_raw.strip()

                        # 生年月日がないなら役員行ではない可能性が高い
                        if not birthdate or not re.search(r'\d{4}年', birthdate):
                            continue

                        # 略歴
                        career_cols = [ci for ci in col_indices["career"] if ci < len(row) and row[ci]]
                        if len(career_cols) == 2:
                            # 7列テーブル: col3=日付列, col4=説明列 → インターリーブ
                            lines0 = str(row[career_cols[0]]).split('\n')
                            lines1 = str(row[career_cols[1]]).split('\n')
                            career_text = ""
                            for li in range(max(len(lines0), len(lines1))):
                                l0 = lines0[li].strip() if li < len(lines0) else ""
                                l1 = lines1[li].strip() if li < len(lines1) else ""
                                career_text += l0 + " " + l1 + "\n"
                        elif len(career_cols) == 1:
                            career_text = str(row[career_cols[0]])
                        else:
                            career_text = ""
                        career = _parse_career(career_text)

                        # 所有株式数
                        shares = None
                        si = col_indices["shares"]
                        if si is not None and si < len(row) and row[si]:
                            shares_raw = str(row[si]).replace('\n', '').replace(' ', '')
                            # 「普通株式\n1,234」のようなパターン対応
                            shares_raw = re.sub(r'普通株式', '', shares_raw)
                            shares = clean_number(shares_raw)

                        officers.append({
                            "position": position,
                            "name": name,
                            "birthdate": birthdate,
                            "career": career,
                            "shares": shares,
                        })

    except Exception as e:
        return {"officers": [], "error": str(e)}

    # 重複除去（同じ名前が複数回出る場合）
    seen = set()
    unique_officers = []
    for off in officers:
        if off["name"] not in seen:
            seen.add(off["name"])
            unique_officers.append(off)

    return {"officers": unique_officers, "total_count": len(unique_officers)}


# =============================================================================
# 4. 子会社・関係会社
# =============================================================================
def extract_subsidiaries(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "01_会社概要" / "05_関係会社の状況.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    subsidiaries = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            current_category = ""
            for page in pdf.pages:
                tables = safe_tables(page)
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = " ".join([str(c) for c in table[0] if c])

                    # 関係会社テーブル（スペース入り「名 称」にも対応）
                    header_nospace = header.replace(' ', '').replace('　', '')
                    if "名称" in header_nospace and ("事業" in header_nospace or "資本" in header_nospace):
                        for row in table[1:]:
                            if not row or not row[0]:
                                continue
                            name_str = str(row[0]).strip().replace('\n', ' ')

                            # カテゴリ行の検出
                            if "連結子会社" in name_str:
                                current_category = "連結子会社"
                                name_str = name_str.replace('（連結子会社）', '').replace('(連結子会社)', '').strip()
                            elif "持分法適用" in name_str:
                                current_category = "持分法適用関連会社"
                                name_str = name_str.replace('（持分法適用関連会社）', '').strip()

                            if not name_str or name_str in ['計', '合計']:
                                continue

                            # 注記マーク除去
                            name_clean = re.sub(r'\s*[＊*]+[０-９0-9＊*]*', '', name_str).strip()

                            location = str(row[1]).replace('\n', ' ').strip() if len(row) > 1 and row[1] else ""
                            capital = str(row[2]).replace('\n', ' ').strip() if len(row) > 2 and row[2] else ""
                            business = str(row[3]).replace('\n', ' ').strip() if len(row) > 3 and row[3] else ""

                            # 議決権比率
                            ratio = None
                            if len(row) > 4 and row[4]:
                                ratio_str = str(row[4]).replace('\n', '')
                                ratio = clean_float(ratio_str)

                            relation = str(row[5]).replace('\n', ' ').strip() if len(row) > 5 and row[5] else ""

                            if name_clean:
                                subsidiaries.append({
                                    "name": name_clean,
                                    "category": current_category,
                                    "location": location,
                                    "capital": capital,
                                    "business": business,
                                    "voting_ratio_percent": ratio,
                                    "relationship": relation,
                                })
    except Exception as e:
        return {"subsidiaries": [], "error": str(e)}

    return {"subsidiaries": subsidiaries, "total_count": len(subsidiaries)}


# =============================================================================
# 5. 沿革
# =============================================================================
def extract_history(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "01_会社概要" / "03_沿革.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    events = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            table_found = False
            full_text = ""

            for page in pdf.pages:
                full_text += safe_text(page) + "\n"
                tables = safe_tables(page)
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = " ".join([str(c) for c in table[0] if c])
                    if "年月" not in header and "概要" not in header:
                        continue
                    table_found = True

                    for row in table[1:]:
                        if not row or len(row) < 2 or not row[0] or not row[1]:
                            continue
                        dates_str = str(row[0])
                        descs_str = str(row[1])
                        dates = [d.strip() for d in dates_str.split('\n') if d.strip()]
                        descs = [d.strip() for d in descs_str.split('\n') if d.strip()]

                        if len(dates) == len(descs):
                            # 行数一致: 1:1でペアリング
                            current_date = ""
                            current_desc_parts = []
                            for d, desc in zip(dates, descs):
                                if re.match(r'\d{4}年', d) or re.match(r'\d{1,2}月', d):
                                    if current_date and current_desc_parts:
                                        events.append({
                                            "date": current_date,
                                            "event": ' '.join(current_desc_parts)
                                        })
                                    current_date = d
                                    current_desc_parts = [desc]
                                else:
                                    current_desc_parts.append(desc)
                            if current_date and current_desc_parts:
                                events.append({
                                    "date": current_date,
                                    "event": ' '.join(current_desc_parts)
                                })
                        else:
                            # 行数不一致: テーブル解析を諦めテキストフォールバックに任せる
                            table_found = False
                            events = []
                            break
                    if not table_found:
                        break

            # テーブルがない場合: テキストから抽出（大成建設パターン）
            if not table_found and full_text:
                # ヘッダー除去
                text = re.sub(r'EDINET提出書類.*?有価証券報告書\n?', '', full_text)
                text = re.sub(r'\d+/\d+\n?', '', text)
                # 年月パターンで分割
                entries = re.findall(
                    r'(\d{4}年\d{1,2}月)\s*(.+?)(?=\d{4}年\d{1,2}月|$)',
                    text, re.DOTALL
                )
                for date, desc in entries:
                    desc_clean = re.sub(r'\s+', ' ', desc.strip())
                    if desc_clean and len(desc_clean) > 2:
                        events.append({"date": date, "event": desc_clean})

    except Exception as e:
        return {"events": [], "error": str(e)}

    return {"events": events, "total_count": len(events)}


# =============================================================================
# 6. 事業内容（テキスト抽出）
# =============================================================================
def extract_business_description(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "01_会社概要" / "04_事業の内容.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += safe_text(page) + "\n"
            # ヘッダー/フッター除去
            text = re.sub(r'EDINET提出書類.*?有価証券報告書\n?', '', text)
            text = re.sub(r'\d+/\d+\n?', '', text)
            text = text.strip()
            return {"text": text, "char_count": len(text)}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# 7. リスク情報（テキスト抽出）
# =============================================================================
def extract_risks(company_dir: Path, year: str) -> dict:
    risk_dir = company_dir / f"{year}_有報" / "02_経営戦略_リスク"
    if not risk_dir.exists():
        return {"error": "dir_not_found"}

    # 「事業等のリスク」を含むPDFを探す
    risk_pdfs = []
    for pdf_file in sorted(risk_dir.glob("*.pdf")):
        if "リスク" in pdf_file.name and "事業" in pdf_file.name:
            risk_pdfs.append(pdf_file)
    if not risk_pdfs:
        # フォールバック: リスクを含む全PDF
        for pdf_file in sorted(risk_dir.glob("*.pdf")):
            if "リスク" in pdf_file.name:
                risk_pdfs.append(pdf_file)
    if not risk_pdfs:
        return {"error": "risk_pdf_not_found"}

    try:
        text = ""
        for pdf_path in risk_pdfs[:3]:  # 最大3ファイル
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text += safe_text(page) + "\n"
        text = re.sub(r'EDINET提出書類.*?有価証券報告書\n?', '', text)
        text = re.sub(r'\d+/\d+\n?', '', text)
        text = text.strip()

        # リスク項目をリストとして抽出
        risk_items = []
        patterns = [
            r'[（(](\d+)[）)]\s*(.+?)(?=[（(]\d+[）)]|$)',  # (1) xxx
            r'[①②③④⑤⑥⑦⑧⑨⑩]\s*(.+?)(?=[①②③④⑤⑥⑦⑧⑨⑩]|$)',
        ]
        for pat in patterns:
            items = re.findall(pat, text[:5000], re.DOTALL)
            if len(items) >= 3:
                for item in items:
                    title = item[-1].strip().split('\n')[0][:100] if isinstance(item, tuple) else item.strip().split('\n')[0][:100]
                    risk_items.append(title)
                break

        return {"text": text[:10000], "risk_items": risk_items, "char_count": len(text)}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# 8. 研究開発活動（テキスト抽出）
# =============================================================================
def extract_rd(company_dir: Path, year: str) -> dict:
    pdf_path = company_dir / f"{year}_有報" / "07_その他" / "02_研究開発活動.pdf"
    if not pdf_path.exists():
        return {"error": "file_not_found"}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += safe_text(page) + "\n"
            text = re.sub(r'EDINET提出書類.*?有価証券報告書\n?', '', text)
            text = re.sub(r'\d+/\d+\n?', '', text)
            text = text.strip()

            # 研究開発費の金額を抽出
            rd_amount = None
            m = re.search(r'研究開発費.*?([\d,]+)\s*(百万円|億円|千円|万円)', text[:3000])
            if m:
                amount = int(m.group(1).replace(',', ''))
                unit = m.group(2)
                if unit == '百万円':
                    rd_amount = amount * 1_000_000
                elif unit == '億円':
                    rd_amount = amount * 100_000_000
                elif unit == '千円':
                    rd_amount = amount * 1_000
                elif unit == '万円':
                    rd_amount = amount * 10_000
                else:
                    rd_amount = amount

            return {"text": text[:8000], "rd_expense": rd_amount, "char_count": len(text)}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# メイン処理
# =============================================================================
def extract_company(company_dir: Path, year: str = "2024") -> dict:
    """1社分の全データを抽出"""
    dir_name = company_dir.name
    code_match = re.match(r'(\d+)_(.+)', dir_name)
    code = code_match.group(1) if code_match else ""
    name = code_match.group(2) if code_match else dir_name

    year_dir = company_dir / f"{year}_有報"
    if not year_dir.exists():
        return {"company_code": code, "company_name": name, "error": f"{year}_有報 not found"}

    result = {
        "company_code": code,
        "company_name": name,
        "fiscal_year": year,
        "extracted_at": datetime.now().isoformat(),
        "employees": extract_employees(company_dir, year),
        "major_shareholders": extract_shareholders(company_dir, year),
        "officers": extract_officers(company_dir, year),
        "subsidiaries": extract_subsidiaries(company_dir, year),
        "history": extract_history(company_dir, year),
        "business_description": extract_business_description(company_dir, year),
        "risk_factors": extract_risks(company_dir, year),
        "rd_activities": extract_rd(company_dir, year),
    }
    return result


def run_batch(company_dirs: list, year: str = "2024", max_workers: int = 4):
    """バッチ処理（単年・逐次 - 後方互換用）"""
    output_dir = Path("company_data")
    output_dir.mkdir(exist_ok=True)

    results = []
    for i, company_dir in enumerate(company_dirs):
        code = company_dir.name.split('_')[0]
        print(f"[{i+1}/{len(company_dirs)}] {company_dir.name}...", end=" ", flush=True)

        try:
            data = extract_company(company_dir, year)
            out_path = output_dir / f"{code}_data.json"
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            emp = data.get("employees", {}).get("parent", {})
            sh = data.get("major_shareholders", {})
            off = data.get("officers", {})
            sub = data.get("subsidiaries", {})
            hist = data.get("history", {})

            emp_count = emp.get("employee_count", "?")
            salary = emp.get("avg_annual_salary", "?")
            sh_count = len(sh.get("major_shareholders", []))
            off_count = off.get("total_count", "?")
            sub_count = sub.get("total_count", "?")
            hist_count = hist.get("total_count", "?")

            print(f"OK emp:{emp_count} sal:{salary} sh:{sh_count} off:{off_count} sub:{sub_count} hist:{hist_count}")
            results.append(data)
        except Exception as e:
            print(f"ERR: {e}")

    return results


# =============================================================================
# 並列バッチ処理（多年対応）
# =============================================================================
def _process_one(args_tuple):
    """ワーカー関数: 1社1年を抽出してJSON保存"""
    company_dir, year, output_dir_str, skip_existing = args_tuple
    output_dir = Path(output_dir_str)
    code = company_dir.name.split('_')[0]
    out_path = output_dir / f"{code}_{year}_data.json"

    if skip_existing and out_path.exists():
        return {"code": code, "year": year, "status": "skipped"}

    try:
        data = extract_company(company_dir, year)

        # 年度ディレクトリが存在しない場合
        if "error" in data and "_有報 not found" in data.get("error", ""):
            return {"code": code, "year": year, "status": "no_year_dir"}

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        emp = data.get("employees", {}).get("parent", {})
        return {
            "code": code, "year": year, "status": "success",
            "emp": emp.get("employee_count"),
            "sal": emp.get("avg_annual_salary"),
        }
    except Exception as e:
        return {"code": code, "year": year, "status": "error", "error": str(e)}


def parse_years(year_arg, years_arg):
    """--year / --years から年リストを生成"""
    if years_arg:
        if '-' in years_arg and ',' not in years_arg:
            start, end = years_arg.split('-')
            return [str(y) for y in range(int(start), int(end) + 1)]
        else:
            return [y.strip() for y in years_arg.split(',')]
    elif year_arg:
        return [year_arg]
    else:
        return ["2024"]


def run_batch_parallel(company_dirs, years, max_workers=8, skip_existing=False,
                       progress_file=None):
    """多年並列バッチ処理"""
    output_dir = Path("company_data")
    output_dir.mkdir(exist_ok=True)

    # 進捗ロード
    progress = {"completed": {}, "failed": {}, "skipped": {}}
    if progress_file and Path(progress_file).exists():
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)

    # タスクリスト構築
    tasks = []
    for company_dir in company_dirs:
        code = company_dir.name.split('_')[0]
        for year in years:
            task_key = f"{code}_{year}"
            if task_key in progress.get("completed", {}):
                continue
            if task_key in progress.get("skipped", {}):
                continue
            tasks.append((company_dir, year, str(output_dir), skip_existing))

    total = len(tasks)
    prev_completed = len(progress.get("completed", {}))
    prev_skipped = len(progress.get("skipped", {}))
    print(f"=== Batch: {len(company_dirs)} companies x {len(years)} years ===")
    print(f"Tasks: {total} (prev completed: {prev_completed}, prev skipped: {prev_skipped})")
    print(f"Workers: {max_workers}")
    print()

    if total == 0:
        print("Nothing to do.")
        return progress

    done = 0
    success = 0
    errors = 0
    skipped = 0
    no_year = 0
    start_time = time.time()

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_one, task): task for task in tasks}

        for future in as_completed(futures):
            result = future.result()
            done += 1
            task_key = f"{result['code']}_{result['year']}"

            if result["status"] == "success":
                success += 1
                progress["completed"][task_key] = result
            elif result["status"] == "no_year_dir":
                no_year += 1
                progress["skipped"][task_key] = result
            elif result["status"] == "skipped":
                skipped += 1
                progress["skipped"][task_key] = result
            else:
                errors += 1
                progress["failed"][task_key] = result

            # 進捗表示（100件ごと、またはエラー時）
            if done % 100 == 0 or result["status"] == "error" or done == total:
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta_min = (total - done) / rate / 60 if rate > 0 else 0
                print(f"  [{done}/{total}] ok={success} skip={skipped} nodir={no_year} "
                      f"err={errors} | {rate:.1f}/s | ETA {eta_min:.0f}min")

            # 進捗保存（500件ごと）
            if progress_file and done % 500 == 0:
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, ensure_ascii=False)

    # 最終保存
    if progress_file:
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress, f, ensure_ascii=False)

    elapsed = time.time() - start_time
    print(f"\n=== Done: {done} tasks in {elapsed/60:.1f} min ===")
    print(f"  Success: {success}")
    print(f"  Skipped(existing): {skipped}")
    print(f"  Skipped(no year dir): {no_year}")
    print(f"  Errors: {errors}")

    if errors > 0:
        print(f"\nFailed tasks:")
        for k, v in list(progress["failed"].items())[:20]:
            print(f"  {k}: {v.get('error', '?')}")

    return progress


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Company Data Extractor - Multi-Year Batch")
    parser.add_argument("--year", default=None, help="Single year (legacy)")
    parser.add_argument("--years", default=None, help="Years: '2016-2025' or '2020,2021,2022'")
    parser.add_argument("--code", default=None, help="Single company code")
    parser.add_argument("--codes", nargs="*", help="Company codes to process")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already extracted files")
    parser.add_argument("--progress", default=None, help="Progress tracking file")
    args = parser.parse_args()

    years = parse_years(args.year, args.years)
    progress_file = args.progress or f"company_data_progress.json"

    # 単一コード指定
    if args.code:
        codes = [args.code]
    elif args.codes:
        codes = args.codes
    else:
        codes = None

    if codes:
        dirs = []
        for code in codes:
            matches = list(PDF_BASE.glob(f"{code}_*"))
            if matches:
                dirs.append(matches[0])
            else:
                print(f"WARN: {code}: directory not found")
        if len(years) == 1 and len(dirs) <= 10:
            # 少数指定は逐次で見やすく表示
            run_batch(dirs, years[0])
        else:
            run_batch_parallel(dirs, years, args.workers, args.skip_existing, progress_file)
    elif args.all:
        dirs = sorted([d for d in PDF_BASE.iterdir() if d.is_dir()])
        if args.limit:
            dirs = dirs[:args.limit]
        run_batch_parallel(dirs, years, args.workers, args.skip_existing, progress_file)
    else:
        # デフォルト: テスト10社
        test_codes = ["7203", "6758", "1332", "1801", "8306", "4502", "6501", "9984", "2802", "3382"]
        dirs = []
        for code in test_codes:
            matches = list(PDF_BASE.glob(f"{code}_*"))
            if matches:
                dirs.append(matches[0])
        print(f"=== Test: {len(dirs)} companies x {len(years)} years ===\n")
        if len(years) == 1:
            run_batch(dirs, years[0])
        else:
            run_batch_parallel(dirs, years, args.workers, args.skip_existing, progress_file)
