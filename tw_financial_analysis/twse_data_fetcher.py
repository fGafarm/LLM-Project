"""
台湾証券取引所 (TWSE) OpenAPIからデータ取得
APIキー不要・完全無料

データソース:
  - 損益計算書: /opendata/t187ap06_L_ci (一般産業)
  - 貸借対照表: /opendata/t187ap07_L_ci (一般産業)
  - 金融業種P/L: /opendata/t187ap06_L_{basi,fh,ins,bd,mim} (銀行/金融持株/保険/証券/綜合)
  - 金融業種B/S: /opendata/t187ap07_L_{basi,fh,ins,bd,mim}
  - 月次売上: /opendata/t187ap05_P
  - PER/PBR/配当: /exchangeReport/BWIBBU_ALL

Usage:
  python twse_data_fetcher.py              # 全上場企業取得
  python twse_data_fetcher.py 2330         # TSMC のみ
  python twse_data_fetcher.py 2330,2317    # 複数社
"""
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

# TWSE uses self-signed certificate chain
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

BASE_URL = "https://openapi.twse.com.tw/v1"
OUTPUT_DIR = Path(__file__).resolve().parent / "tw_xbrl_store"

# TWSE API endpoints
ENDPOINTS = {
    "income_statement": "/opendata/t187ap06_L_ci",   # 損益計算書 (一般産業)
    "balance_sheet": "/opendata/t187ap07_L_ci",       # 貸借対照表 (一般産業)
    "monthly_revenue": "/opendata/t187ap05_P",        # 月次売上高
    "valuation": "/exchangeReport/BWIBBU_ALL",        # PER/PBR/配当利回り
}

# 損益計算書フィールドマッピング (中国語→英語キー)
PL_MAPPING = {
    "營業收入": "revenue",
    "營業成本": "cost_of_revenue",
    "營業毛利（毛損）淨額": "gross_profit",
    "營業費用": "operating_expenses",
    "營業利益（損失）": "operating_income",
    "營業外收入及支出": "non_operating_income",
    "稅前淨利（淨損）": "income_before_tax",
    "所得稅費用（利益）": "income_tax",
    "本期淨利（淨損）": "net_income",
    "淨利（淨損）歸屬於母公司業主": "net_income_parent",
    "基本每股盈餘（元）": "eps_basic",
    "本期綜合損益總額": "comprehensive_income",
}

# 貸借対照表フィールドマッピング
BS_MAPPING = {
    "流動資產": "current_assets",
    "非流動資產": "non_current_assets",
    "資產總額": "total_assets",
    "流動負債": "current_liabilities",
    "非流動負債": "non_current_liabilities",
    "負債總額": "total_liabilities",
    "股本": "share_capital",
    "資本公積": "capital_surplus",
    "保留盈餘": "retained_earnings",
    "庫藏股票": "treasury_stock",
    "歸屬於母公司業主之權益合計": "equity_parent",
    "非控制權益": "non_controlling_interests",
    "權益總額": "total_equity",
    "每股參考淨值": "book_value_per_share",
}

# ---------------------------------------------------------------------------
# 金融5業種 (basi=銀行, fh=金融持株, ins=保険, bd=証券, mim=綜合)
# キー体系は業種ごとに異なる (2026-07-11 全キー実測: scratchpad/twse_analysis.txt)
# ---------------------------------------------------------------------------
FIN_INDUSTRIES = ("basi", "fh", "ins", "bd", "mim")

# 千元→元スケールの除外キー (元単位のまま格納するもの)
NO_SCALE_KEYS = {"eps_basic", "book_value_per_share"}

# 業種別P/Lマッピング。fh はAPI側バグがあるため extract_fh_pl() で個別処理
FIN_PL_MAPPING = {
    "basi": {
        "利息淨收益": "net_interest_income",
        "利息以外淨損益": "non_interest_income",
        "營業費用": "operating_expenses",
        "繼續營業單位稅前淨利（淨損）": "income_before_tax",
        "所得稅費用（利益）": "income_tax",
        "本期淨利（淨損）": "net_income",
        "淨利（損）歸屬於母公司業主": "net_income_parent",
        "本期綜合損益總額（稅後）": "comprehensive_income",
        "基本每股盈餘（元）": "eps_basic",
    },
    "ins": {
        # IFRS17移行で「營業收入/營業成本/營業費用」のキー名は陳腐化 (費用は負値あり)。
        # revenueは 營業收入+營業成本 の近似導出 (下記 revenue_definition フラグ付与)
        "營業利益（損失）": "operating_income",
        "營業外收入及支出": "non_operating_income",
        "繼續營業單位稅前純益（純損）": "income_before_tax",
        "所得稅費用（利益）": "income_tax",
        "本期淨利（淨損）": "net_income",
        "淨利（淨損）歸屬於母公司業主": "net_income_parent",
        "本期綜合損益總額": "comprehensive_income",
        "基本每股盈餘（元）": "eps_basic",
    },
    "bd": {
        "收益": "revenue",
        "支出及費用": "operating_expenses",
        "營業利益": "operating_income",
        "營業外損益": "non_operating_income",
        "稅前淨利（淨損）": "income_before_tax",
        "所得稅費用（利益）": "income_tax",
        "本期淨利（淨損）": "net_income",
        "淨利（損）歸屬於母公司業主": "net_income_parent",
        "本期綜合損益總額": "comprehensive_income",
        "基本每股盈餘（元）": "eps_basic",
    },
    "mim": {
        "收入": "revenue",
        "支出": "operating_expenses",
        "繼續營業單位稅前淨利（淨損）": "income_before_tax",
        "所得稅費用（利益）": "income_tax",
        "本期淨利（淨損）": "net_income",
        "淨利（淨損）歸屬於母公司業主": "net_income_parent",
        "本期綜合損益總額": "comprehensive_income",
        "基本每股盈餘（元）": "eps_basic",
    },
}

# 業種別B/Sマッピング。金融3業種 (basi/fh/ins) は「總計」、bd/mim はciと同じ「總額」体系
_FIN_BS_TOTALS = {
    "資產總計": "total_assets",
    "負債總計": "total_liabilities",
    "權益總計": "total_equity",
    "股本": "share_capital",
    "資本公積": "capital_surplus",
    "保留盈餘": "retained_earnings",
    "庫藏股票": "treasury_stock",
    "每股參考淨值": "book_value_per_share",
}
FIN_BS_MAPPING = {
    "basi": {
        **_FIN_BS_TOTALS,
        "歸屬於母公司業主之權益合計": "equity_parent",
        "非控制權益": "non_controlling_interests",
    },
    "fh": {
        **_FIN_BS_TOTALS,
        "歸屬於母公司業主之權益": "equity_parent",  # fhのみ「合計」なし
        "非控制權益": "non_controlling_interests",
    },
    # ins: 個別負債はAPI側欠落で不整合のため、合計3点+株本系のみマップ
    "ins": dict(_FIN_BS_TOTALS),
    "bd": BS_MAPPING,   # ciと同一キー体系
    "mim": BS_MAPPING,  # ciと同一キー体系
}


def fetch_mops_html(endpoint: str, year: int, season: int, typek: str = "sii") -> str:
    """MOPS AJAX APIからHTMLテーブルを取得 (過去年度対応)
    year: 民国暦 (e.g. 113 = 2024)
    season: 1-4
    typek: sii=上場, otc=店頭
    """
    # 新版 mops.twse.com.tw は ajax_t163sb04/05 を遮断している。旧版ホスト mopsov が正
    # (2026-07-07 実測確認。旧版は予告なく廃止されうるため原本保全を必須とする)
    url = f"https://mopsov.twse.com.tw/mops/web/{endpoint}"
    params = f"step=1&firstin=1&TYPEK={typek}&year={year}&season={season:02d}"
    print(f"  MOPS: {endpoint} year={year} Q{season}...")
    req = urllib.request.Request(
        url, data=params.encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (tw_financial_analysis)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # 原本保全: mopsov は旧版扱いで予告なく廃止されうる。生HTMLを必ず残す
        # (POSTMORTEM教訓: 原本なき抽出は再現不能)
        raw_dir = Path(__file__).resolve().parent / "raw_mops"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / f"{endpoint}_{typek}_{year}Q{season}.html").write_text(html, encoding="utf-8")
        return html
    except Exception as e:
        print(f"  [ERROR] MOPS {endpoint}: {e}")
        return ""


def parse_mops_table(html: str) -> list[dict]:
    """MOPS HTMLテーブルをパース"""
    import re
    records = []
    # テーブル行を探す
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    headers = []
    for row in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if not cells:
            continue
        # ヘッダー行を検出
        if '公司代號' in cells or '公司 代號' in cells:
            headers = [c.replace('\n', '').replace(' ', '') for c in cells]
            continue
        if headers and len(cells) == len(headers):
            record = dict(zip(headers, cells))
            if record.get('公司代號', '').strip():
                records.append(record)
    return records


def fetch_api(endpoint: str) -> list:
    """TWSE OpenAPIからJSONデータ取得"""
    url = f"{BASE_URL}{endpoint}"
    print(f"  Fetching {url} ...")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (tw_financial_analysis)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  -> {len(data)} records")
            return data
    except Exception as e:
        print(f"  [ERROR] {e}")
        return []


def parse_number(val: str) -> float | None:
    """数値文字列をfloatに変換 (千元単位)"""
    if not val or val.strip() in ("", "-", "N/A", "－"):
        return None
    try:
        return float(val.replace(",", ""))
    except ValueError:
        return None


def roc_to_ad(roc_year: str) -> int:
    """民国暦→西暦変換 (114 → 2025)"""
    try:
        return int(roc_year) + 1911
    except ValueError:
        return 0


def process_income_statements(records: list) -> dict:
    """損益計算書データを会社コード別に整理"""
    companies = {}
    for rec in records:
        code = rec.get("公司代號", "").strip()
        if not code:
            continue
        year = roc_to_ad(rec.get("年度", ""))
        quarter = rec.get("季別", "")
        name = rec.get("公司名稱", "").strip()

        data = {}
        for zh_key, en_key in PL_MAPPING.items():
            val = parse_number(rec.get(zh_key, ""))
            if val is not None:
                # 千元→元に変換 (EPSは既に元単位)
                if en_key != "eps_basic":
                    val = val * 1000
                data[en_key] = val

        if code not in companies:
            companies[code] = {"name": name, "years": {}}
        companies[code]["years"][year] = {
            "quarter": quarter,
            "pl": data,
        }
    return companies


def process_balance_sheets(records: list) -> dict:
    """貸借対照表データを会社コード別に整理"""
    companies = {}
    for rec in records:
        code = rec.get("公司代號", "").strip()
        if not code:
            continue
        year = roc_to_ad(rec.get("年度", ""))
        name = rec.get("公司名稱", "").strip()

        data = {}
        for zh_key, en_key in BS_MAPPING.items():
            val = parse_number(rec.get(zh_key, ""))
            if val is not None:
                if en_key not in ("book_value_per_share",):
                    val = val * 1000
                data[en_key] = val

        if code not in companies:
            companies[code] = {"name": name, "years": {}}
        if year not in companies[code]["years"]:
            companies[code]["years"][year] = {}
        companies[code]["years"][year]["bs"] = data
    return companies


def approx_equal(a: float, b: float, rel: float = 0.002, abs_tol: float = 10.0) -> bool:
    """算術検証用の近似一致 (千元単位・端数許容)"""
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


def scale_thousands(data: dict) -> dict:
    """千元→元スケール (EPS・每股參考淨值・文字列フラグは除外)"""
    out = {}
    for k, v in data.items():
        if isinstance(v, (int, float)) and k not in NO_SCALE_KEYS:
            out[k] = v * 1000
        else:
            out[k] = v
    return out


def extract_fh_pl(rec: dict) -> dict | None:
    """fh (金融持株) P/L抽出 — API側の値ズレバグ対応 (2026-07-11実測)

    TWSE APIのfh P/Lは「所得稅費用」キーが欠落しており、値が1キー分ずれて格納:
      - val(利息以外淨收益)       = 実際はトップライン淨收益
      - val(營業費用)             = 実際は繼續營業單位稅前損益
      - val(繼續營業單位稅前損益) = 実際は所得稅費用
    (繼續營業單位本期淨利 以降は整合。全13社で算術検証済み)

    算術でズレ版/正常版 (TWSEが将来修正した場合) を自動判別し、
    どちらも成立しないレコードは None を返して取り込まない (検証層テーゼ)。
    値は千元単位のまま返す (スケールは呼び出し側)。
    """
    nii = parse_number(rec.get("利息淨收益", ""))
    other_net = parse_number(rec.get("其他收益及費損淨額", ""))
    non_ii = parse_number(rec.get("利息以外淨收益", ""))
    net_rev = parse_number(rec.get("淨收益", ""))
    opex = parse_number(rec.get("營業費用", ""))
    pretax = parse_number(rec.get("繼續營業單位稅前損益", ""))
    net_income = parse_number(rec.get("本期稅後淨利（淨損）", ""))

    # ズレの影響を受けない共通フィールド (繼續營業單位本期淨利 以降)
    common = {}
    for zh_key, en_key in (
        ("本期稅後淨利（淨損）", "net_income"),
        ("淨利（淨損）歸屬於母公司業主", "net_income_parent"),
        ("本期綜合損益總額", "comprehensive_income"),
        ("基本每股盈餘（元）", "eps_basic"),
    ):
        val = parse_number(rec.get(zh_key, ""))
        if val is not None:
            common[en_key] = val

    # ズレ版判定: NII+其他收益及費損淨額 ≈ val(利息以外淨收益) かつ
    #             val(營業費用)-val(繼續營業單位稅前損益) ≈ 本期稅後淨利
    shifted = (
        None not in (nii, other_net, non_ii, opex, pretax, net_income)
        and approx_equal(nii + other_net, non_ii)
        and approx_equal(opex - pretax, net_income)
    )
    if shifted:
        data = dict(common)
        data["revenue"] = non_ii                  # 実体: 淨收益 (トップライン)
        data["net_interest_income"] = nii
        data["non_interest_income"] = other_net   # 実体: 利息以外淨收益
        data["income_before_tax"] = opex          # 実体: 稅前損益
        data["income_tax"] = pretax               # 実体: 所得稅費用
        return data

    # 正常版判定: NII + val(利息以外淨收益) ≈ val(淨收益)
    normal = (
        None not in (nii, non_ii, net_rev)
        and approx_equal(nii + non_ii, net_rev)
    )
    if normal:
        data = dict(common)
        data["revenue"] = net_rev
        data["net_interest_income"] = nii
        data["non_interest_income"] = non_ii
        if opex is not None:
            data["operating_expenses"] = opex
        if pretax is not None:
            data["income_before_tax"] = pretax
        tax = parse_number(rec.get("所得稅費用（利益）", ""))
        if tax is None and pretax is not None:
            cont_ni = parse_number(rec.get("繼續營業單位本期淨利（淨損）", ""))
            if cont_ni is not None:
                tax = pretax - cont_ni
        if tax is not None:
            data["income_tax"] = tax
        return data

    return None


def fetch_financial_industries() -> tuple[dict, dict]:
    """金融5業種 (basi/fh/ins/bd/mim) のP/L・B/SをOpenAPIから取得

    戻り値 (pl_companies, bs_companies) は process_income_statements /
    process_balance_sheets と同形式。main() で一般業(ci)の結果にマージする。
    """
    pl_all: dict = {}
    bs_all: dict = {}

    for ind in FIN_INDUSTRIES:
        dropped: set = set()  # fh算術検証NGの (code, year)

        # --- P/L ---
        records = fetch_api(f"/opendata/t187ap06_L_{ind}")
        for rec in records:
            code = rec.get("公司代號", "").strip()
            if not code:
                continue
            year = roc_to_ad(rec.get("年度", ""))
            quarter = rec.get("季別", "")
            name = rec.get("公司名稱", "").strip()

            if ind == "fh":
                data = extract_fh_pl(rec)
                if data is None:
                    print(f"  [WARN] fh {code} {name} FY{year}: P/L算術検証不成立 "
                          f"(ズレ版/正常版とも) — 取込スキップ")
                    dropped.add((code, year))
                    continue
            else:
                data = {}
                for zh_key, en_key in FIN_PL_MAPPING[ind].items():
                    val = parse_number(rec.get(zh_key, ""))
                    if val is not None:
                        data[en_key] = val
                if ind == "basi":
                    # 銀行: revenue = 利息淨收益 + 利息以外淨損益 の導出
                    if "net_interest_income" in data and "non_interest_income" in data:
                        data["revenue"] = (data["net_interest_income"]
                                           + data["non_interest_income"])
                elif ind == "ins":
                    # 保険: IFRS17でキー名陳腐化。營業收入+營業成本 を近似revenueとする
                    rev = parse_number(rec.get("營業收入", ""))
                    cost = parse_number(rec.get("營業成本", ""))
                    if rev is not None:
                        data["revenue"] = rev + (cost if cost is not None else 0.0)
                        data["revenue_definition"] = "insurance_ifrs17_approx"

            data = scale_thousands(data)
            if code not in pl_all:
                pl_all[code] = {"name": name, "years": {}}
            pl_all[code]["years"][year] = {"quarter": quarter, "pl": data}

        # --- B/S ---
        records = fetch_api(f"/opendata/t187ap07_L_{ind}")
        for rec in records:
            code = rec.get("公司代號", "").strip()
            if not code:
                continue
            year = roc_to_ad(rec.get("年度", ""))
            name = rec.get("公司名稱", "").strip()
            if (code, year) in dropped:
                # P/L検証NGの年はB/Sも出さない (quarter情報はP/L側にしか無く、
                # B/S単独保存は quarter="4" 既定値で年度誤認するリスクがあるため)
                print(f"  [WARN] fh {code} {name} FY{year}: P/L検証NGに伴いB/Sも取込スキップ")
                continue

            data = {}
            for zh_key, en_key in FIN_BS_MAPPING[ind].items():
                val = parse_number(rec.get(zh_key, ""))
                if val is not None:
                    data[en_key] = val
            data = scale_thousands(data)

            if code not in bs_all:
                bs_all[code] = {"name": name, "years": {}}
            bs_all[code]["years"].setdefault(year, {})["bs"] = data

        time.sleep(1)  # rate limit
    return pl_all, bs_all


def process_valuation(records: list) -> dict:
    """PER/PBR/配当利回りデータ"""
    companies = {}
    for rec in records:
        code = rec.get("Code", rec.get("證券代號", "")).strip()
        if not code:
            continue
        name = rec.get("Name", rec.get("證券名稱", "")).strip()
        companies[code] = {
            "name": name,
            "per": parse_number(rec.get("PEratio", rec.get("本益比", ""))),
            "dividend_yield": parse_number(rec.get("DividendYield", rec.get("殖利率(%)", ""))),
            "pbr": parse_number(rec.get("PBratio", rec.get("股價淨值比", ""))),
        }
    return companies


def calculate_derived(data: dict) -> dict:
    """導出指標を計算"""
    derived = {}
    pl = data.get("pl", {})
    bs = data.get("bs", {})

    rev = pl.get("revenue")
    oi = pl.get("operating_income")
    ni = pl.get("net_income")
    gp = pl.get("gross_profit")
    ta = bs.get("total_assets")
    te = bs.get("total_equity")
    ca = bs.get("current_assets")
    cl = bs.get("current_liabilities")
    tl = bs.get("total_liabilities")

    if rev and rev != 0:
        if gp is not None:
            derived["gross_margin"] = round(gp / rev * 100, 2)
        if oi is not None:
            derived["operating_margin"] = round(oi / rev * 100, 2)
        if ni is not None:
            derived["net_margin"] = round(ni / rev * 100, 2)

    if te and te != 0 and ni is not None:
        derived["roe"] = round(ni / te * 100, 2)

    if ta and ta != 0 and ni is not None:
        derived["roa"] = round(ni / ta * 100, 2)

    if ta and ta != 0 and te is not None:
        derived["equity_ratio"] = round(te / ta * 100, 2)

    if cl and cl != 0 and ca is not None:
        derived["current_ratio"] = round(ca / cl, 2)

    if te and te != 0 and tl is not None:
        derived["debt_equity_ratio"] = round(tl / te, 2)

    return derived


def save_company(code: str, name: str, data: dict, valuation: dict | None = None):
    """1社分のデータをJSON保存"""
    # ディレクトリ作成
    safe_name = name
    for ch in '/\\:*?"<>|':
        safe_name = safe_name.replace(ch, "_")
    company_dir = OUTPUT_DIR / f"{code}_{safe_name}"
    company_dir.mkdir(parents=True, exist_ok=True)

    for year, year_data in data.items():
        pl = year_data.get("pl", {})
        bs = year_data.get("bs", {})
        merged = {**pl, **bs}
        derived = calculate_derived(year_data)

        output = {
            "company_code": code,
            "company_name": name,
            "fiscal_year": year,
            "quarter": year_data.get("quarter", "4"),
            "market": "TWSE",
            "currency": "TWD",
            "unit": "NTD",
            "data": merged,
            "derivedMetrics": derived,
        }

        if valuation:
            output["valuation"] = valuation

        out_path = company_dir / f"{year}.json"
        out_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return len(data)


def fetch_historical_mops(years_back: int = 5) -> tuple[dict, dict]:
    """MOPS AJAX APIから過去年度のP/L・B/Sを取得"""
    import re

    current_roc = 114  # 2025年 = 民国114年
    pl_all = {}
    bs_all = {}

    for offset in range(years_back):
        roc_year = current_roc - offset
        ad_year = roc_year + 1911
        print(f"\n--- FY{ad_year} (民国{roc_year}年) Q4 ---")

        # 損益計算書
        html = fetch_mops_html("ajax_t163sb04", roc_year, 4)
        if html:
            records = parse_mops_table(html)
            print(f"  P/L: {len(records)} records")
            for rec in records:
                code = rec.get("公司代號", "").strip()
                if not code:
                    continue
                name = rec.get("公司名稱", "").strip()
                data = {}
                # MOPS field mapping
                field_map = {
                    "營業收入": "revenue",
                    "營業成本": "cost_of_revenue",
                    "營業毛利（毛損）": "gross_profit",
                    "營業毛利(毛損)": "gross_profit",
                    "營業費用": "operating_expenses",
                    "營業利益（損失）": "operating_income",
                    "營業利益(損失)": "operating_income",
                    "稅前淨利（淨損）": "income_before_tax",
                    "稅前淨利(淨損)": "income_before_tax",
                    "本期淨利（淨損）": "net_income",
                    "本期淨利(淨損)": "net_income",
                    "基本每股盈餘（元）": "eps_basic",
                    "基本每股盈餘(元)": "eps_basic",
                }
                for zh, en in field_map.items():
                    val = parse_number(rec.get(zh, ""))
                    if val is not None:
                        if en != "eps_basic":
                            val = val * 1000
                        data[en] = val

                if code not in pl_all:
                    pl_all[code] = {"name": name, "years": {}}
                pl_all[code]["years"][ad_year] = {"quarter": "4", "pl": data}

        time.sleep(3)  # rate limit

        # 貸借対照表
        html = fetch_mops_html("ajax_t163sb05", roc_year, 4)
        if html:
            records = parse_mops_table(html)
            print(f"  B/S: {len(records)} records")
            for rec in records:
                code = rec.get("公司代號", "").strip()
                if not code:
                    continue
                name = rec.get("公司名稱", "").strip()
                data = {}
                field_map = {
                    "流動資產": "current_assets",
                    "非流動資產": "non_current_assets",
                    "資產總額": "total_assets",
                    "資產總計": "total_assets",
                    "流動負債": "current_liabilities",
                    "非流動負債": "non_current_liabilities",
                    "負債總額": "total_liabilities",
                    "負債總計": "total_liabilities",
                    "股本": "share_capital",
                    "保留盈餘": "retained_earnings",
                    "權益總額": "total_equity",
                    "權益總計": "total_equity",
                    "每股參考淨值": "book_value_per_share",
                }
                for zh, en in field_map.items():
                    val = parse_number(rec.get(zh, ""))
                    if val is not None:
                        if en not in ("book_value_per_share",):
                            val = val * 1000
                        data[en] = val

                if code not in bs_all:
                    bs_all[code] = {"name": name, "years": {}}
                if ad_year not in bs_all[code]["years"]:
                    bs_all[code]["years"][ad_year] = {}
                bs_all[code]["years"][ad_year]["bs"] = data

        time.sleep(3)

    return pl_all, bs_all


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "ALL"
    filter_codes = None
    if arg != "ALL":
        filter_codes = set(t.strip() for t in arg.split(","))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=== TWSE Data Fetcher ===")
    print()

    # 1. 最新データ (OpenAPI)
    print("[1/4] Latest Income Statements (OpenAPI)...")
    pl_raw = fetch_api(ENDPOINTS["income_statement"])
    pl_companies = process_income_statements(pl_raw)
    print(f"  {len(pl_companies)} companies")

    print("[2/4] Latest Balance Sheets (OpenAPI)...")
    bs_raw = fetch_api(ENDPOINTS["balance_sheet"])
    bs_companies = process_balance_sheets(bs_raw)
    print(f"  {len(bs_companies)} companies")

    # 1.5. 金融5業種 (basi/fh/ins/bd/mim) — 一般業(ci)に無い企業をOpenAPIから追加
    print("\n[2.5/4] Financial industries (basi/fh/ins/bd/mim, OpenAPI)...")
    fin_pl, fin_bs = fetch_financial_industries()
    print(f"  P/L: {len(fin_pl)} companies, B/S: {len(fin_bs)} companies")
    for code, fin_data in fin_pl.items():
        if code not in pl_companies:
            pl_companies[code] = fin_data
        else:
            for year, ydata in fin_data["years"].items():
                if year not in pl_companies[code]["years"]:
                    pl_companies[code]["years"][year] = ydata
    for code, fin_data in fin_bs.items():
        if code not in bs_companies:
            bs_companies[code] = fin_data
        else:
            for year, ydata in fin_data["years"].items():
                if year not in bs_companies[code]["years"]:
                    bs_companies[code]["years"][year] = ydata

    # 2. 過去データ (MOPS AJAX)
    print("\n[3/4] Historical data (MOPS, 5 years)...")
    hist_pl, hist_bs = fetch_historical_mops(5)
    print(f"  P/L: {len(hist_pl)} companies, B/S: {len(hist_bs)} companies")

    # 3. Valuation
    print("\n[4/4] Valuation (PER/PBR)...")
    val_raw = fetch_api(ENDPOINTS["valuation"])
    val_companies = process_valuation(val_raw)
    print(f"  {len(val_companies)} companies")

    # マージ: OpenAPI + MOPS
    print("\nMerging all data...")
    # OpenAPIデータをベースにMOPSデータをマージ
    for code, mops_data in hist_pl.items():
        if code not in pl_companies:
            pl_companies[code] = mops_data
        else:
            for year, ydata in mops_data["years"].items():
                if year not in pl_companies[code]["years"]:
                    pl_companies[code]["years"][year] = ydata

    for code, mops_data in hist_bs.items():
        if code not in bs_companies:
            bs_companies[code] = mops_data
        else:
            for year, ydata in mops_data["years"].items():
                if year not in bs_companies[code]["years"]:
                    bs_companies[code]["years"][year] = ydata

    all_codes = set(pl_companies.keys()) | set(bs_companies.keys())
    if filter_codes:
        all_codes = all_codes & filter_codes

    saved = 0
    for code in sorted(all_codes):
        pl_data = pl_companies.get(code, {"name": "", "years": {}})
        bs_data = bs_companies.get(code, {"name": "", "years": {}})
        name = pl_data.get("name") or bs_data.get("name", "")

        merged_years = {}
        all_years = set(pl_data.get("years", {}).keys()) | set(bs_data.get("years", {}).keys())
        for year in all_years:
            merged_years[year] = {
                "quarter": pl_data.get("years", {}).get(year, {}).get("quarter", "4"),
                "pl": pl_data.get("years", {}).get(year, {}).get("pl", {}),
                "bs": bs_data.get("years", {}).get(year, {}).get("bs", {}),
            }

        valuation = val_companies.get(code)
        save_company(code, name, merged_years, valuation)
        saved += 1

    print(f"\nDone: {saved} companies saved to {OUTPUT_DIR}")
    print(f"Total codes available: {len(all_codes)}")


if __name__ == "__main__":
    main()
