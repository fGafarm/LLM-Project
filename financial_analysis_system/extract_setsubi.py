"""
有価証券報告書「主要な設備の状況」抽出モジュール

EDINET ZIP内の `0103010_honbun_*.htm` から提出会社・国内子会社・在外子会社の
保有設備テーブル（事業所名・所在地・土地簿価・土地面積等）を抽出する。

業種ごとにフォーマットが異なるため、業種別パーサで処理する:
- 不動産業:    parse_real_estate    (物件単位、竣工年付き)
- 銀行業:      parse_bank           (店舗単位、㎡)
- 陸運業:      parse_railway        (土地/建物別列、㎡)
- その他:      parse_manufacturer   (汎用、千㎡ or ㎡)
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Callable

from bs4 import BeautifulSoup
from bs4.element import Tag


# ----- 数値・テキスト処理ユーティリティ ------------------------------------

NUMBER_RE = re.compile(r"[-－―\s]?[\d,]+(?:\.\d+)?")
PAREN_AREA_RE = re.compile(r"[（(]\s*([\d,\.]+)\s*[）)](?:\s*[（(]\s*※\s*([\d,\.]+)\s*[）)])?")
ADDRESS_PAREN_RE = re.compile(r"[（(]([^（()）]+)[）)]\s*$")
JP_LOCATION_HINTS = ("都", "道", "府", "県", "市", "区", "町", "村")


def _to_num(s: Optional[str]) -> Optional[float]:
    """文字列から最初の数値を抽出。'-' '－' '―' '※n' (注記) は None。"""
    if s is None:
        return None
    cleaned = s.replace("\xa0", " ").strip()
    if not cleaned or cleaned in ("－", "―", "-", "−"):
        return None
    # 「※１」「※2」「※３」などの注記マークは数値ではない
    if "※" in cleaned:
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _norm(s: str) -> str:
    return s.replace("\xa0", " ").replace("\u3000", " ").strip()


def _split_office_address(text: str) -> tuple[str, str]:
    """事業所名と所在地を分離。

    例:
      '本社 （愛知県豊田市）'           → ('本社', '愛知県豊田市')
      '本社 (東京都八王子市)他'         → ('本社', '東京都八王子市他')
      '宮城仙台第二Base （宮城県加美郡） 他３機材Base' → ('宮城仙台第二Base', '宮城県加美郡')
      '本社工場 山口県 下関市'          → ('本社工場', '山口県下関市')  # 長府製作所型
    """
    text = _norm(text)
    # 末尾の「他」「ほか」などを取り除いた位置で検索
    stripped = re.sub(r"(他.*|ほか.*)$", "", text).strip()
    m = ADDRESS_PAREN_RE.search(stripped)
    if m:
        addr = m.group(1).strip()
        # 括弧内が「25店舗」「3箇所」のような数量表記なら住所ではない
        if re.match(r"^\d+[店舗箇所ケ所か所件拠点]", addr):
            addr = ""
            office = stripped
        else:
            office = ADDRESS_PAREN_RE.sub("", stripped).strip()
            if "他" in text[len(stripped):] or "ほか" in text[len(stripped):]:
                addr = addr + "他"
        if addr:
            return office, addr
    m = ADDRESS_PAREN_RE.search(text)
    if m:
        addr = m.group(1).strip()
        if re.match(r"^\d+[店舗箇所ケ所か所件拠点]", addr):
            pass  # skip, fall through to PREF_NAMES
        else:
            office = ADDRESS_PAREN_RE.sub("", text).strip()
            return office, addr

    # 括弧がない場合: 都道府県名を検出して office と address を分離
    # '本社工場 山口県 下関市' → office='本社工場', address='山口県下関市'
    PREF_NAMES = (
        "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
        "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
        "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
        "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
        "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
        "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
        "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
    )
    for pref in PREF_NAMES:
        idx = text.find(pref)
        if idx > 0:
            office = text[:idx].strip()
            addr = text[idx:].replace(" ", "").replace("\u3000", "")
            return office, addr
        elif idx == 0:
            # 先頭が都道府県名: 全体をaddressとして扱い、officeは空
            addr = text.replace(" ", "").replace("\u3000", "")
            return "", addr
    return text, ""


FOREIGN_MARKERS = (
    "中国", "韓国", "台湾", "香港", "シンガポール", "タイ", "ベトナム",
    "マレーシア", "インドネシア", "フィリピン", "インド",
    "アメリカ", "米国", "U.S.A", "USA", "カナダ", "メキシコ", "ブラジル",
    "イギリス", "英国", "フランス", "ドイツ", "イタリア", "スペイン",
    "オランダ", "ベルギー", "スイス", "ポーランド", "チェコ", "ハンガリー",
    "ロシア", "トルコ", "オーストラリア", "ニュージーランド",
    "南アフリカ", "UAE", "エジプト",
)


def _is_japanese_address(addr: str) -> bool:
    if not addr:
        return False
    # 先に外国マーカーをチェック（「中国南京市」→ 市があってもJPではない）
    for fm in FOREIGN_MARKERS:
        if fm in addr:
            return False
    return any(h in addr for h in JP_LOCATION_HINTS)


def _parse_paren_area(text: str) -> tuple[Optional[float], Optional[float]]:
    """`( 2,733) (※ 32)` → (2733, 32)。"""
    if not text:
        return (None, None)
    m = PAREN_AREA_RE.search(text.replace("\xa0", " "))
    if not m:
        return (None, None)
    area = _to_num(m.group(1))
    leased = _to_num(m.group(2)) if m.group(2) else None
    return (area, leased)


def _row_cells(tr: Tag) -> list[str]:
    return [_norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]


def _expand_rowspan(table: Tag) -> list[list[str]]:
    """rowspan/colspan を展開してテーブルをフルサイズの2次元配列にする。

    TBS/日テレの有報は row[0] に rowspan=2 の結合セルを多用するため、
    BeautifulSoupの単純走査では row[0] が空に見える。このメソッドでは
    結合セルの値を下の行にコピーする（列見出しの再現のため）。

    戻り値: rows[i][j] = そのセル位置のテキスト値
    """
    grid, _ = _expand_rowspan_tracked(table)
    return grid


def _expand_rowspan_tracked(table: Tag) -> tuple[list[list[str]], list[list[bool]]]:
    """rowspan/colspan を展開 + origin 情報を返す。

    Returns:
        grid[i][j]    = そのセル位置のテキスト値
        origins[i][j] = そのセルが row i の HTML で定義されたか (True)
                        それとも上の行から rowspan で継承されたか (False)

    origin 情報は supplement 行（TBS の面積注記など、同一論理行の続き）
    を data 行と区別するために使う。
    """
    raw_rows = table.find_all("tr")
    if not raw_rows:
        return [], []

    # 1パス目: 各行の生セル総colspanから max_cols を概算
    est_max_cols = 0
    for r in raw_rows:
        total = 0
        for c in r.find_all(["td", "th"]):
            total += int(c.get("colspan", 1) or 1)
        if total > est_max_cols:
            est_max_cols = total

    n_rows = len(raw_rows)
    # 少し余裕を持たせる (ネストやバグ対応)
    max_cols = max(est_max_cols, 1) + 4
    grid: list[list[Optional[str]]] = [[None] * max_cols for _ in range(n_rows)]
    origins: list[list[bool]] = [[False] * max_cols for _ in range(n_rows)]

    for ri, r in enumerate(raw_rows):
        col = 0
        for c in r.find_all(["td", "th"]):
            # 既に埋まっている場所（上から rowspan でコピー済み）はスキップ
            while col < max_cols and grid[ri][col] is not None:
                col += 1
            if col >= max_cols:
                break
            text = _norm(c.get_text(" ", strip=True))
            cspan = int(c.get("colspan", 1) or 1)
            rspan = int(c.get("rowspan", 1) or 1)
            for dr in range(rspan):
                for dc in range(cspan):
                    rr = ri + dr
                    cc = col + dc
                    if rr < n_rows and cc < max_cols:
                        grid[rr][cc] = text
                        if dr == 0:
                            origins[rr][cc] = True
            col += cspan

    # 末尾の使用されていない列を削除
    actual_cols = 0
    for row in grid:
        for j in range(len(row) - 1, -1, -1):
            if row[j] is not None:
                actual_cols = max(actual_cols, j + 1)
                break
    grid_text = [[("" if grid[i][j] is None else grid[i][j]) for j in range(actual_cols)] for i in range(n_rows)]
    origins_trim = [[origins[i][j] for j in range(actual_cols)] for i in range(n_rows)]
    return grid_text, origins_trim


# ----- 共通スキーマ ---------------------------------------------------------


@dataclass
class FacilityRecord:
    parser: str                       # "manufacturer" | "real_estate" | "bank" | "railway"
    table_type: str                   # "parent" | "domestic_sub" | "foreign_sub" | "property"
    office_name: str
    address_raw: str
    country: str                      # "JP" | "FOREIGN"
    segment: str = ""
    facility_type: str = ""
    land_book_value_mil: Optional[float] = None
    land_area_sqm: Optional[float] = None       # 必ず ㎡ に正規化して格納
    leased_area_sqm: Optional[float] = None
    building_book_value_mil: Optional[float] = None
    building_floor_area_sqm: Optional[float] = None
    machinery_book_value_mil: Optional[float] = None
    total_book_value_mil: Optional[float] = None
    employees: Optional[int] = None
    acquired_year: Optional[int] = None         # 不動産業の竣工年


# ----- 設備セクション取り出し -----------------------------------------------


def _load_setsubi_html(zip_path: str | Path) -> Optional[bytes]:
    """設備の状況セクションのHTMLバイト列を取り出す。

    有報の構成には2パターンあり、どちらにも対応する:
    - 分割型 (多数派): `0103010_honbun_*.htm` にセクション単位で収録
    - 一括型 (清水建設・熊谷組 等): `0101010_honbun_*.htm` に全部入り
      → この場合、HTMLから「第3 設備の状況」部分だけを抜き出す必要がある

    いずれの場合も、返すバイト列は BeautifulSoup がパースできる HTML で、
    _find_main_setsubi_section() が見つけられる h3 を含んでいればよい。
    """
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # まず 0103010 分割型を試す
        for n in names:
            if "PublicDoc" in n and "0103010_honbun" in n and n.endswith(".htm"):
                with z.open(n) as f:
                    return f.read()
        # 次に 0102010 / 0101010 一括型: 「主要な設備の状況」があるか確認
        for prefix in ("0102010", "0101010"):
            for n in names:
                if "PublicDoc" in n and f"{prefix}_honbun" in n and n.endswith(".htm"):
                    with z.open(n) as f:
                        data = f.read()
                    if "主要な設備の状況" in data.decode("utf-8", errors="ignore"):
                        return data
        # 最後に全 honbun ファイルを走査
        for n in names:
            if "PublicDoc" in n and "honbun" in n and n.endswith(".htm"):
                with z.open(n) as f:
                    data = f.read()
                if "主要な設備の状況" in data.decode("utf-8", errors="ignore"):
                    return data
    return None


def _find_main_setsubi_section(soup: BeautifulSoup) -> Optional[Tag]:
    """「主要な設備の状況」の見出しタグを返す。

    h3 が一般的だが、h4 (みずほリース型) や h2 の場合もある。
    見出しタグが存在しない場合は p/div/span 等のテキストノードも探す。
    全角スペースやNBSPの表記ゆれにも対応。
    """
    needle = "主要な設備の状況"
    # h3 → h4 → h2 → h5 の優先順で探す
    for tag_name in ("h3", "h4", "h2", "h5"):
        for h in soup.find_all(tag_name):
            text = h.get_text().replace("\xa0", " ").replace("\u3000", " ")
            if needle in text:
                return h
    # 見出しタグがない場合: p/div/span/b/strong 等からテキスト検索
    for tag_name in ("p", "div", "span", "b", "strong"):
        for el in soup.find_all(tag_name):
            text = el.get_text().replace("\xa0", " ").replace("\u3000", " ")
            if needle in text and len(text) < 50:
                return el
    return None


def _collect_h4_tables(setsubi_h3: Tag) -> list[tuple[str, Tag]]:
    """設備セクション内の (h4テキスト, table) ペアを収集。h4 が無ければ ('', table)。"""
    out: list[tuple[str, Tag]] = []
    current_h4 = ""
    for el in setsubi_h3.find_all_next(["h3", "h4", "table"]):
        if el is setsubi_h3:
            continue
        if el.name == "h3":
            break
        if el.name == "h4":
            current_h4 = _norm(el.get_text())
            continue
        if el.name == "table":
            out.append((current_h4, el))
    return out


def _classify_h4(h4_text: str) -> str:
    if "提出会社" in h4_text:
        return "parent"
    if "国内子会社" in h4_text:
        return "domestic_sub"
    if "在外子会社" in h4_text or "海外子会社" in h4_text:
        return "foreign_sub"
    return "parent"  # h4 無しは提出会社扱い


# ----- パーサ: 製造業（汎用） -----------------------------------------------


def _is_setsubi_table(table: Tag) -> bool:
    """主要な設備テーブルか?（投資計画・除却計画は除外）。

    TBS/日テレのように rowspan で raw の最初数行が崩れている場合にも
    対応するため、header_text は最初5行まで見る。
    """
    rows = table.find_all("tr")
    if len(rows) < 2:
        return False
    header_text = " ".join(
        _norm(c.get_text(" ", strip=True))
        for r in rows[:8]
        for c in r.find_all(["td", "th"])
    )
    # 「土地」必須。「所在地」は不要（DM三井製糖型はセグメント名で代用）
    if "土地" not in header_text:
        return False
    # 帳簿価額 or 百万円 or 千円 がないと設備テーブルではない
    if not any(k in header_text for k in ("帳簿価額", "百万円", "千円", "百万")):
        return False
    nogos = ["投資予定", "予定金額", "資金調達", "着手", "完了予定", "売却時期",
             "営業キロ", "線名", "区間", "電動客車"]
    if any(k in header_text for k in nogos):
        return False
    return True


def _detect_unit_is_kilo_sqm(table: Tag) -> bool:
    """ヘッダーから面積単位を検出。「千㎡」あれば True、それ以外は ㎡ 想定。"""
    text = " ".join(_norm(c.get_text(" ", strip=True)) for c in table.find_all(["td", "th"]))
    # 千㎡ / 千m / 千ｍ / 千m2 / 千ｍ2 / 千ｍ²
    return any(kw in text for kw in ("千㎡", "千m", "千ｍ", "千m²", "千ｍ²", "千平方"))


def _detect_book_value_scale(table: Tag) -> float:
    """帳簿価額の単位から百万円換算スケールを検出。

    帳簿価額(千円)  → 0.001 (千円を百万円に変換)
    帳簿価額(百万円) → 1.0
    帳簿価額(億円)  → 100.0
    デフォルト      → 1.0 (百万円想定)

    ヘッダーが深い行（grid[2] 等のサブヘッダー）に単位が書いてある場合も対応するため
    最初5行まで見る。
    """
    rows = table.find_all("tr")
    if not rows:
        return 1.0
    header_text = " ".join(
        _norm(c.get_text(" ", strip=True))
        for r in rows[:8]
        for c in r.find_all(["td", "th"])
    )
    if "千円" in header_text and "百万" not in header_text:
        return 0.001
    if "億円" in header_text:
        return 100.0
    return 1.0


def _build_column_map(table: Tag) -> dict[str, int]:
    """ヘッダー2行を統合し、{論理列名: データ行のセルインデックス} を返す。

    「土地」「建物」「機械」「合計」「事業所名」「所在地」「セグメント」「設備内容」「従業員」を識別。
    データ行の列インデックスはヘッダー1行目セル数で計算。
    """
    rows = table.find_all("tr")
    if not rows:
        return {}
    h1 = _row_cells(rows[0])
    col_map: dict[str, int] = {}
    for i, txt in enumerate(h1):
        t = txt
        if "事業所" in t or ("会社名" in t and "所在地" in t):
            col_map.setdefault("office", i)
        elif "セグメント" in t or "事業内容" in t:
            col_map.setdefault("segment", i)
        elif "設備の内容" in t or "設備内容" in t:
            col_map.setdefault("facility", i)
        elif t == "土地" or "土地" in t and "面積" not in t:
            col_map.setdefault("land", i)
        elif "建物" in t and "機械" not in t:
            col_map.setdefault("building", i)
        elif "機械" in t:
            col_map.setdefault("machinery", i)
        elif t == "合計" or t.startswith("合計"):
            col_map.setdefault("total", i)
        elif "従業" in t:
            col_map.setdefault("employees", i)
        elif "所在地" in t and "office" not in col_map:
            col_map.setdefault("address", i)
    return col_map


def _looks_like_subheader(cells: list[str]) -> bool:
    """サブヘッダー行の判定: 「土地/建物/機械/合計/面積」等の列名が並ぶ行。"""
    if not cells:
        return False
    keywords = (
        "土地", "建物", "機械", "合計", "面積", "帳簿", "金額",
        "工具", "リース", "ソフト", "借地", "使用権", "車両", "構築物", "動産",
    )
    hits = sum(1 for c in cells if any(k in c for k in keywords))
    has_digit = any(re.search(r"\d{2,}", c) for c in cells)
    if has_digit:
        return False
    # 短いサブヘッダー（2〜3セル、例: ['面積(㎡)','金額']）は1ヒットでも許容
    if len(cells) <= 3 and hits >= 1:
        return True
    return hits >= 2


def _is_numeric_data_cell(text: str) -> bool:
    """セル内容が「数値データ」として解釈できるか。

    data_row 検出に使用。日付（年月日）や注記（※、現在、以下）は除外。
    """
    if not text:
        return False
    s = text.strip()
    # 日付パターン除外: "2024年3月31日現在" "2024年" "(2024年...)" etc.
    if re.search(r"\d{4}年|年\d+月|月\d+日|現在|以下|以上|注記|（注）|\(注\)", s):
        return False
    # 括弧内にも「年月日」が含まれるケース（カラム名等）
    if "年" in s or "月" in s or "日" in s or "％" in s:
        return False
    # 「(1)」「(２)」「（１）」のような注釈/セクション番号は数値データではない
    # 括弧の直後に数値が続く "(81) 442" のような値は除外しない
    if re.match(r"^[\(（]\s*[\d１２３４５６７８９０]+\s*[\)）]\s*$", s):
        return False
    # 「(1) 提出会社」のようなセクション見出し (括弧内1桁の後に非数値文字)
    if re.match(r"^[\(（]\s*\d\s*[\)）]\s*[^\d\s,\.]", s):
        return False

    s_clean = s.replace(",", "").replace("，", "")

    # 括弧/角括弧で囲まれた数値 (面積注記など): "(35222)" "[6,771]" "(2,733) (※ 32)"
    if re.match(r"^[\(\[（]\s*[\d,\.]+", s):
        return True
    # 純粋な数値 (3桁以上)
    if re.match(r"^-?\d+(?:\.\d+)?$", s_clean) and len(s_clean.lstrip("-").split(".")[0]) >= 3:
        return True
    # 数値+括弧 (トヨタ型 "14,891"、任天堂型 "22,160 (67)")
    if re.match(r"^-?[\d,]+(?:\.\d+)?\s*[\(（]", s):
        return True
    # ダッシュ/0 + 括弧付き数値: "― (35,870)" "- (1,234)"
    if re.match(r"^[\-－―−0]\s*[\(（]\s*[\d,\.]+", s):
        return True
    # 括弧内に3桁以上の数値を含むセル: "968 〔206〕" etc.
    if re.search(r"\d{3,}", s_clean):
        return True
    return False


def _row_has_data(row: list[str]) -> bool:
    """行がデータ行か（数値セルを少なくとも1つ持つ）。"""
    for c in row:
        if _is_numeric_data_cell(c):
            return True
    return False


def _parse_setsubi_table_generic(table: Tag, table_type: str, parser_name: str) -> list[FacilityRecord]:
    """汎用ヘッダー駆動パーサ（全フォーマット統一版・grid-based）。

    rowspan/colspan を展開してフルグリッドを作り、列位置ベースでデータを抽出する。
    対応する変種:
    - Format A (トヨタ提出会社): 1セル面積補足行 (col 0 に括弧付き面積)
    - Format B (任天堂等): 土地セルに "value (area)" 埋め込み
    - Format C (セブン&アイ): 3行ヘッダー (大列|中列|細列)
    - Format D (エア・ウォーター等): 土地列が 面積|金額 の2セルに分割
    - Format E (TBS/日テレ等): row[0] が rowspan 結合で raw では空に見える
    - Format F (銀行・金融型): 土地セルに "value (area)" 埋め込み + 全体 rowspan
    """
    return _parse_setsubi_table_unified(table, table_type, parser_name)


def _parse_setsubi_table_unified(table: Tag, table_type: str, parser_name: str) -> list[FacilityRecord]:
    """Grid-based unified parser. Handles all known rowspan/colspan patterns."""
    grid, origins = _expand_rowspan_tracked(table)
    if len(grid) < 3:
        return []
    n_cols = len(grid[0])
    if n_cols < 3:
        return []

    is_kilo = _detect_unit_is_kilo_sqm(table)
    area_scale = 1000.0 if is_kilo else 1.0
    book_scale = _detect_book_value_scale(table)

    # ---- 1. Find header_end (first row with numeric data) ----
    header_end = 0
    for ri in range(min(6, len(grid))):
        if _row_has_data(grid[ri]):
            break
        header_end = ri + 1
    if header_end == 0 or header_end >= len(grid):
        return []

    # ---- 2. Merge header rows -> h1 for column detection ----
    # Strategy: search ALL header rows for keywords; pick the shallowest match.
    header_keywords_all = ("事業所", "会社名", "帳簿価額", "所在地", "土地", "建物",
                           "機械", "合計", "従業", "面積", "セグメント", "設備",
                           "金額", "その他", "構築物")

    # 「事業所数」「事業所面積」等、意味が異なる派生語は office 列として使わない
    EXCLUDED_OFFICE_SUFFIXES = ("数", "面積", "面積㎡", "件数")

    def find_col_in_headers(*needles) -> Optional[int]:
        for ri in range(header_end):
            for j, c in enumerate(grid[ri]):
                if j >= n_cols:
                    break
                # 空白除去版でマッチング ('設備の 内容' → '設備の内容')
                c_stripped = re.sub(r'\s+', '', c)
                for nd in needles:
                    nd_stripped = re.sub(r'\s+', '', nd)
                    if nd_stripped in c_stripped:
                        # '事業所' が '事業所数' にマッチするのを防ぐ
                        if nd_stripped in ("事業所", "会社名", "名称", "所在地"):
                            idx = c_stripped.find(nd_stripped)
                            after = c_stripped[idx + len(nd_stripped):]
                            if any(after.startswith(sfx) for sfx in EXCLUDED_OFFICE_SUFFIXES):
                                continue
                        return j
        return None

    # 事業所名 (office) 列の検出
    # 優先順位: 「事業所名」> 「会社名」> 「所在地」(ヤマダHD型: 所在地のみで地区分け)
    # 事業所/店舗/営業所/支店 等も office 候補 (保険・銀行の 店名/支店名 フォーマット対応)
    jigyo_col = find_col_in_headers("事業所名", "事業所", "店名", "店舗名", "支店名", "営業所名")
    kaisha_col = find_col_in_headers("会社名")
    # 会社名 はあるが 事業所名/店名 が無い場合、'名称'/'設備名' 列を office として使う (9048/8725 型)
    # ただし 'セグメントの名称' のような列は除外 (セグメント分類であって office ではない)
    if jigyo_col is None and kaisha_col is not None:
        def find_meisho_or_setsubi() -> Optional[int]:
            for ri in range(header_end):
                for j, c in enumerate(grid[ri]):
                    if j >= n_cols or j == kaisha_col:
                        continue
                    c_stripped = re.sub(r'\s+', '', c)
                    if 'セグメント' in c_stripped:
                        continue
                    if '名称' in c_stripped or '設備名' in c_stripped:
                        return j
            return None
        sub_col = find_meisho_or_setsubi()
        if sub_col is not None:
            jigyo_col = sub_col
    # 会社名 も無い場合、'地区' (証券業 8207 型) を office として使う
    if jigyo_col is None and kaisha_col is None:
        chiku_col = find_col_in_headers("地区")
        if chiku_col is not None:
            jigyo_col = chiku_col
    if jigyo_col is not None:
        # colspan 等で同じ '事業所' ラベルが複数列になる場合 (あさひ 3333 型、ポーラ 4926 型)、
        # データ行の内容を見て真の office 列を選ぶ:
        #   - col 0 が常に空 → col 1 が office (あさひ)
        #   - col 0 が重複 (セグメント/部門名) かつ col 1 が一意 → col 1 が office (ポーラ)
        if jigyo_col + 1 < n_cols and header_end < len(grid):
            same_label_next = False
            for ri in range(header_end):
                cur = re.sub(r'\s+', '', grid[ri][jigyo_col]) if jigyo_col < len(grid[ri]) else ''
                nxt = re.sub(r'\s+', '', grid[ri][jigyo_col + 1]) if jigyo_col + 1 < len(grid[ri]) else ''
                if cur and cur == nxt and '事業所' in cur:
                    same_label_next = True
                    break
            if same_label_next:
                v0_list = []
                v1_list = []
                for ri in range(header_end, min(header_end + 10, len(grid))):
                    v0 = grid[ri][jigyo_col].strip() if jigyo_col < len(grid[ri]) else ''
                    v1 = grid[ri][jigyo_col + 1].strip() if jigyo_col + 1 < len(grid[ri]) else ''
                    v0_list.append(v0)
                    v1_list.append(v1)
                v0_empty = sum(1 for v in v0_list if not v)
                v1_empty = sum(1 for v in v1_list if not v)
                v0_non_empty = [v for v in v0_list if v]
                v1_non_empty = [v for v in v1_list if v]
                v0_unique = len(set(v0_non_empty))
                v1_unique = len(set(v1_non_empty))
                # col 0 がほぼ空 → col 1 (あさひ)
                if v0_empty > v1_empty and v0_empty >= len(v0_list) * 0.5:
                    jigyo_col = jigyo_col + 1
                # col 0 が重複多 (dept label) で col 1 が一意 (ポーラ)
                elif len(v0_non_empty) >= 3 and v1_unique > v0_unique and v0_unique <= max(1, len(v0_non_empty) // 2):
                    jigyo_col = jigyo_col + 1
        office_col = jigyo_col
        company_col = kaisha_col  # ㈱フジテレビジョン 等を入れる
    elif kaisha_col is not None:
        office_col = kaisha_col
        company_col = None
    else:
        # 事業所名も会社名も無い: 「所在地」を office として使う (ヤマダHD型)
        shozaichi_col = find_col_in_headers("所在地")
        if shozaichi_col is not None:
            office_col = shozaichi_col
            company_col = None
        else:
            # 「事業所」「セグメント」「名称」等、最初の列をofficeとして使う (DM三井製糖型)
            office_col = 0
            company_col = None

    # 所在地が独立した列になっているかを検出
    office_label = ""
    for ri in range(header_end):
        if office_col < len(grid[ri]) and grid[ri][office_col]:
            office_label = grid[ri][office_col]
            break
    address_col: Optional[int] = None
    if "所在地" not in office_label:
        for ri in range(header_end):
            for j, c in enumerate(grid[ri]):
                if j == office_col or j == company_col:
                    continue
                if "所在地" in c:
                    address_col = j
                    break
            if address_col is not None:
                break

    segment_col = find_col_in_headers("セグメント", "事業内容", "事業別")
    facility_col = find_col_in_headers("設備の内容", "設備内容")

    # 土地列検出: 「土地」を含むが「土地面積」単独ではない列
    # ヤマダHD型は土地列と「土地面積」独立列を持つため、土地面積列を land_col に誤検出しないように
    land_col = None
    for ri in range(header_end):
        for j, c in enumerate(grid[ri]):
            if "土地" not in c:
                continue
            # "土地面積 （㎡）" だけのセルは面積列 → 除外
            if c.replace(" ", "").replace("\u3000", "").startswith("土地面積"):
                continue
            land_col = j
            break
        if land_col is not None:
            break
    if land_col is None:
        return []

    building_col = find_col_in_headers("建物", "構築物")
    machinery_col = find_col_in_headers("機械")
    total_col = find_col_in_headers("合計")
    employees_col = find_col_in_headers("従業")

    # 「土地面積」独立列 (ヤマダHD型)
    standalone_area_col: Optional[int] = None
    for ri in range(header_end):
        for j, c in enumerate(grid[ri]):
            if j == land_col:
                continue
            if "土地面積" in c or "土地 面積" in c:
                standalone_area_col = j
                break
        if standalone_area_col is not None:
            break

    # Format D 検出: 土地列が「面積」列と「金額/帳簿価額」列に分かれている
    # ヘッダー全行をスキャンして検出する (しまむら型: grid[0] に面積、grid[1] に土地×2)
    format_d = False
    land_area_col = None
    land_value_col = land_col
    if header_end >= 2 and land_col + 1 < n_cols:
        for ri in range(header_end):
            cl = grid[ri][land_col] if land_col < len(grid[ri]) else ""
            cn = grid[ri][land_col + 1] if land_col + 1 < len(grid[ri]) else ""
            # パターン1: 「面積」| 「金額」or「帳簿」
            if "面積" in cl and ("金額" in cn or "帳簿" in cn or "土地" in cn):
                format_d = True
                land_area_col = land_col
                land_value_col = land_col + 1
                break
            # パターン2: 「土地」|「土地」(同じラベルが2列) → 片方が面積、もう片方が簿価
            # 他の行に「面積」があれば判別
            if cl == cn and "土地" in cl:
                for ri2 in range(header_end):
                    cl2 = grid[ri2][land_col] if land_col < len(grid[ri2]) else ""
                    cn2 = grid[ri2][land_col + 1] if land_col + 1 < len(grid[ri2]) else ""
                    if "面積" in cl2:
                        format_d = True
                        land_area_col = land_col
                        land_value_col = land_col + 1
                        break
                    elif "面積" in cn2:
                        format_d = True
                        land_area_col = land_col + 1
                        land_value_col = land_col
                        break
                if format_d:
                    break

    def scaled(v):
        return v * book_scale if v is not None else None

    def get(row, col):
        if col is None or col < 0 or col >= len(row):
            return ""
        return row[col]

    def _set_land_area_from_paren(rec, cell: str):
        """Parse "(12,345)" or "(12,345) (※67)" and set land_area_sqm/leased_area_sqm."""
        a, l = _parse_paren_area(cell)
        if a is not None:
            rec.land_area_sqm = a * area_scale
        if l is not None:
            rec.leased_area_sqm = l * area_scale

    def _set_land_area_from_raw(rec, cell: str):
        """Parse a cell that might be just a number representing area."""
        if not cell:
            return
        stripped = cell.replace("\xa0", " ").strip()
        # Strip parentheses wrapping
        if stripped.startswith("(") or stripped.startswith("（"):
            _set_land_area_from_paren(rec, cell)
            return
        v = _to_num(stripped.split("(")[0])
        if v is not None:
            rec.land_area_sqm = v * area_scale
        # Also check for leased area in parens
        _, l = _parse_paren_area(stripped)
        if l is not None:
            rec.leased_area_sqm = l * area_scale

    # ---- 3. Walk data rows ----
    records: list[FacilityRecord] = []
    for ri in range(header_end, len(grid)):
        row = grid[ri]
        row_origins = origins[ri]

        # Determine if this row is a new data row or a supplement row.
        # 真のデータ行: office セルに有効な値があり、行内のセル数が「ほぼ全部 origin」
        # (TBS型の供給行は origin セルが極端に少ない: 1-3個)
        office_is_origin = row_origins[office_col] if office_col < len(row_origins) else False
        office_text = get(row, office_col)

        def looks_like_office(text: str) -> bool:
            if not text:
                return False
            s = text.strip()
            # 純粋な数値データ (カンマ・ピリオド・空白のみ) は office ではない
            if re.match(r"^[-\-\－－−]?[\d,\.\s]+$", s):
                return False
            # 数字始まりでも日本語/英字が続けば office として許容
            # 例: "161店舗", "22営業所他", "３校舎", "８社", "１ヶ所", "127 Charing Cross",
            #      "15－営業所", "８番らーめん辰口店", "４階建て モデル棟"
            if re.match(r"^[\d１２３４５６７８９０]", s):
                # 数字の後に 3 文字以上の非数値 (日本語 or 英字) があれば office 扱い
                non_digit = re.sub(r"[\d,\.\s\-\－－−()（）\[\]［］]", "", s)
                if len(non_digit) >= 2:
                    return True
                return False
            # マイナス記号始まり = 数値データ
            if re.match(r"^[\-\－－−]", s):
                return False
            # "（東北）..." のような地域名プレフィックスは office の一部として許容
            m = re.match(r"^[\(（]([^\d\)）]{1,10})[\)）]", s)
            if m:
                return True
            # 純粋な括弧数値 "(35,222)" は area 注記 → invalid
            if re.match(r"^[\(（][\d\s,\.]+[\)）]", s):
                return False
            return True

        # 行内 origin セル数を数える: 多数 origin = 新規データ行、少数 = 補足行
        n_origin_in_row = sum(1 for o in row_origins if o)
        n_total_cells = len(row_origins)
        # 「ほぼ全部 origin」の閾値: 全体の半分以上 (補足行は通常1-3セル)
        # 小さいテーブル (4列等) でも 1セルだけ rowspan 継承 (会社名) された行は
        # データ行として扱う必要があるので閾値を n//2 以上で統一
        is_full_data_row = n_origin_in_row >= max(3, (n_total_cells + 1) // 2)

        if is_full_data_row and looks_like_office(office_text):
            # rowspan で会社名等が継承されていても、行内に十分なデータがあれば新規行
            is_new_record = True
        elif is_full_data_row and facility_col is not None and (
            not office_text.strip() or
            re.match(r"^[\-－―−]+(\s*[\(（].*[\)）])?$", office_text.strip())
        ):
            # office_text 空/ダッシュのみ + full data row: facility_col を office として使う
            # (三井物産型: 事業所名セル空、設備の内容セルに「人材開発センター」等が入る)
            # (自重堂型: 事業所名="－ (長崎県松浦市)" のように名無しの賃貸設備)
            fac_text = get(row, facility_col)
            if looks_like_office(fac_text):
                # 元の office_text の括弧内住所 (長崎県松浦市等) は後で _split_office_address が拾う
                # facility_col の値 + 住所で新しい office_text を構成する
                m_addr = re.search(r"[\(（]([^\)）]+)[\)）]", office_text)
                if m_addr:
                    office_text = f"{fac_text} ({m_addr.group(1).strip()})"
                else:
                    office_text = fac_text
                is_new_record = True
            else:
                is_new_record = False
        elif is_full_data_row and company_col is not None and not looks_like_office(office_text):
            # 事業所名が住所風 (street address / 数字始まり) の場合、会社名を office に使う
            # (商船三井 overseas table: 会社名='Daibiru Australia Pty Ltd.', 事業所名='275 George Street')
            comp_text = get(row, company_col)
            if comp_text and looks_like_office(comp_text) and comp_text.strip() not in ('〃',):
                office_text = comp_text
                is_new_record = True
            else:
                is_new_record = False
        elif office_is_origin and looks_like_office(office_text):
            is_new_record = True
        else:
            is_new_record = False

        # 小計/合計 行は supplement として扱わず無視 (8944 福島工場他 area 混入防止)
        if not is_new_record:
            row_text_check = ' '.join(row[:3]).strip()
            if re.search(r'小\s*計|合\s*計|^\s*計\s*$|総\s*計', row_text_check):
                continue
            # col 1 が '小計'/'合計'/'計' だけの場合もスキップ
            if len(row) > 1 and row[1].strip() in ('小計', '合計', '計', '総計', '全店計'):
                continue

        if not is_new_record:
            # Supplement row: inject origin cells into previous record.
            if not records:
                continue
            prev = records[-1]

            # Special case: Toyota-like 1-cell row with paren area.
            # Only applies when the single origin cell is NOT in a Format D area column
            # (Format D area supplement is handled in the general case below).
            origin_indices = [j for j, o in enumerate(row_origins) if o]
            if len(origin_indices) == 1:
                only_j = origin_indices[0]
                cell = get(row, only_j)
                is_format_d_area = format_d and only_j == land_area_col
                # Toyota型: 1セル supplement with (area) notation.
                # 面積/土地列にある場合のみ area として取り扱う。
                # 従業員列 "(3)" のような注記は対象外。
                is_land_col = only_j == land_col or (format_d and only_j == land_value_col)
                is_employees = employees_col is not None and only_j == employees_col
                if (cell and (cell.strip().startswith("(") or cell.strip().startswith("（"))
                        and not is_format_d_area and is_land_col and not is_employees):
                    _set_land_area_from_paren(prev, cell)
                    continue

            # General case: for each origin cell, inject based on column.
            # 補足行の値は「賃借面積」等の注記として扱い、前レコードの main 値は上書きしない。
            for j in origin_indices:
                cell = get(row, j)
                if not cell or not cell.strip():
                    continue
                stripped = cell.replace("\xa0", " ").strip()
                if format_d and j == land_area_col:
                    # Format D supplement: 面積列の補足
                    # 括弧付き値 (8,432) は通常 賃借面積 の注記だが、
                    # prev.land_area_sqm が未設定なら main area として扱う
                    # (あさひ 3333 型: main row は '―', supplement に (area) のみ)
                    if stripped.startswith("(") or stripped.startswith("（"):
                        a, _ = _parse_paren_area(stripped)
                        if a is not None:
                            if prev.land_area_sqm is None:
                                prev.land_area_sqm = a * area_scale
                            else:
                                prev.leased_area_sqm = a * area_scale
                    elif prev.land_area_sqm is None:
                        _set_land_area_from_raw(prev, cell)
                elif j == land_col and not format_d:
                    # 非 Format D: 補足行の土地列は面積注記
                    if stripped.startswith("(") or stripped.startswith("（"):
                        _set_land_area_from_paren(prev, stripped)
                    elif prev.land_area_sqm is None:
                        _set_land_area_from_raw(prev, cell)
                elif format_d and j == land_value_col:
                    if prev.land_book_value_mil is None:
                        v = _to_num(cell.split("(")[0])
                        if v is not None:
                            prev.land_book_value_mil = scaled(v)
                elif j == employees_col:
                    pass
            continue

        # ---- Full data row: extract values ----
        office_label_lower = office_label
        office_is_address = "所在地" in office_label_lower and "事業所" not in office_label_lower and "会社" not in office_label_lower

        if address_col is not None:
            # 事業所名と所在地が別列
            office = office_text.strip()
            addr = get(row, address_col).strip()
        elif office_is_address:
            # ヤマダHD型: office_col が「所在地」列
            office = office_text.strip()
            addr = office_text.strip()
        else:
            # 事業所名セルに "(所在地)" が入っているパターン
            office, addr = _split_office_address(office_text)

        # 地名だけで事業所名が空の場合
        if not office and addr:
            office = ""

        # Land value / area
        land_value = None
        land_area = None
        leased_area = None

        if standalone_area_col is not None:
            # 土地面積独立列型 (ヤマダHD)
            land_text = get(row, land_col)
            if land_text:
                land_value = _to_num(land_text.split("(")[0])
            area_text = get(row, standalone_area_col)
            if area_text:
                a = _to_num(area_text.split("(")[0])
                if a is not None:
                    land_area = a * area_scale
        elif format_d:
            # 土地列が面積/金額に分割 (Air Water型・清水建設型)
            area_text = get(row, land_area_col)
            value_text = get(row, land_value_col)
            # 面積セルのパターン:
            #   "40,285" → area=40285
            #   "(244) 262,586" → area=262586, leased=244 (清水建設型)
            #   "(－) 20,976" → area=20976, leased=None
            if area_text:
                cleaned = area_text.replace("\xa0", " ").strip()
                # 角括弧 [N] を除去 (賃借面積の別表記)
                cleaned_no_bracket = re.sub(r"\[[\d,\.\s\-－]*\]\s*", "", cleaned).strip()
                # 先頭に括弧があり、その後に数値が続く場合: leased + area
                m = re.match(r"^[\(（]\s*([\d,\.\-－\s]*)\s*[\)）]\s*([\d,\.]+)", cleaned_no_bracket)
                if m:
                    leased_raw = m.group(1).strip()
                    area_raw = m.group(2).strip()
                    l_val = _to_num(leased_raw)
                    if l_val is not None:
                        leased_area = l_val * area_scale
                    a_val = _to_num(area_raw)
                    if a_val is not None:
                        land_area = a_val * area_scale
                else:
                    # 通常ケース: 面積だけ or 「N (leased)」
                    a = _to_num(cleaned_no_bracket.split("(")[0].split("（")[0])
                    if a is not None:
                        land_area = a * area_scale
                    _, l = _parse_paren_area(cleaned_no_bracket)
                    if l is not None:
                        leased_area = l * area_scale
            if value_text:
                land_value = _to_num(value_text.split("(")[0])
        else:
            land_text = get(row, land_col)
            if land_text:
                cleaned_lt = land_text.replace("\xa0", " ").strip()
                # Pattern: "(面積) 簿価" — 先頭括弧 + 括弧外に数値
                m_paren_then_val = re.match(
                    r"^[\(（]\s*([\d,\.\-－\s]*)\s*[\)）]\s*([\d,\.]+)", cleaned_lt
                )
                if m_paren_then_val:
                    # 括弧内 = 面積, 括弧外 = 簿価
                    area_raw = m_paren_then_val.group(1).strip()
                    val_raw = m_paren_then_val.group(2).strip()
                    a = _to_num(area_raw)
                    if a is not None:
                        land_area = a * area_scale
                    land_value = _to_num(val_raw)
                else:
                    # Standard: "簿価 (面積)" or "簿価" only
                    head = cleaned_lt.split("(")[0].split("（")[0]
                    land_value = _to_num(head)
                    a, l = _parse_paren_area(land_text)
                    if a is not None:
                        land_area = a * area_scale
                    if l is not None:
                        leased_area = l * area_scale

        # 集計行スキップ: 「合計」「小計」「総合計」やセグメント名だけの行
        office_clean = re.sub(r'\s+', '', office)
        if office_clean in ("合計", "小計", "総合計", "計") or office_clean.endswith("合計") or office_clean.endswith("小計"):
            continue
        # セグメント名だけ (「自動車事業」「金融事業」等) で所在地も無い → 集計行
        # ただし office_col 自体が「事業所名 (セグメント)」のように 事業所名 列で
        # セグメント分けになっている場合はデータ行として保持する (ジーンズメイト 2726 型)
        segment_only_markers = ("事業", "部門", "セグメント")
        office_col_is_pure_segment = "セグメント" in office_label and "事業所" not in office_label
        if (not addr and office_col_is_pure_segment
                and any(office_clean.endswith(mk) for mk in segment_only_markers)):
            continue

        # Country detection
        # 所在地がある場合はそれで判定。ない場合は事業所名で判定。
        # どちらも判定不能なら JP をデフォルト（日本の有報なので）
        if addr and _is_japanese_address(addr):
            country = "JP"
        elif addr and any(fm in addr for fm in FOREIGN_MARKERS):
            country = "FOREIGN"
        elif not addr or not any(h in addr for h in JP_LOCATION_HINTS):
            # addr が空 or 住所として無効 (「店舗」「事業」等) → office で判定
            if office and any(fm in office for fm in FOREIGN_MARKERS):
                country = "FOREIGN"
            else:
                country = "JP"  # 日本の有報なのでデフォルト JP
        else:
            country = "JP"

        rec = FacilityRecord(
            parser=parser_name,
            table_type=table_type,
            office_name=office,
            address_raw=addr,
            country=country,
            segment=get(row, segment_col) or "",
            facility_type=get(row, facility_col) or "",
            land_book_value_mil=scaled(land_value),
            land_area_sqm=land_area,
            leased_area_sqm=leased_area,
            building_book_value_mil=scaled(_to_num(get(row, building_col))),
            machinery_book_value_mil=scaled(_to_num(get(row, machinery_col))),
            total_book_value_mil=scaled(_to_num(get(row, total_col))),
            employees=int(_to_num(get(row, employees_col)) or 0) or None,
        )
        records.append(rec)

    return records


def _parse_setsubi_table_generic_OLD(table: Tag, table_type: str, parser_name: str) -> list[FacilityRecord]:
    """旧パーサー (参考用に残している)。実際には使われない。"""
    raw_rows = table.find_all("tr")
    if len(raw_rows) < 3:
        return []

    is_kilo = _detect_unit_is_kilo_sqm(table)
    area_scale = 1000.0 if is_kilo else 1.0
    book_scale = _detect_book_value_scale(table)

    rows = raw_rows
    h1 = _row_cells(rows[0])

    # h1 に必須キーワードが無いなら rowspan 展開して h1 を復元。
    # TBS/日テレ型: row[0] が rowspan+日付だけ、row[1]/row[2] に列見出しがある
    header_keywords = ("事業所", "会社名", "帳簿価額", "所在地", "土地", "建物")
    has_header = any(any(kw in c for kw in header_keywords) for c in h1)
    if not has_header:
        grid = _expand_rowspan(table)
        if grid and len(grid) >= 3:
            # grid[0]+grid[1]+grid[2] のうちキーワードを含む値を列ごとにマージ
            n_rows_to_merge = min(3, len(grid))
            cols = max(len(grid[i]) for i in range(n_rows_to_merge))
            merged_h1 = []
            for i in range(cols):
                chosen = ""
                # 優先: header_keyword を含むセル
                for gi in range(n_rows_to_merge):
                    v = grid[gi][i] if i < len(grid[gi]) else ""
                    if any(kw in v for kw in header_keywords):
                        chosen = v
                        break
                if not chosen:
                    # キーワードが無ければ、非空の値 (grid の浅い方から)
                    for gi in range(n_rows_to_merge):
                        v = grid[gi][i] if i < len(grid[gi]) else ""
                        if v.strip():
                            chosen = v
                            break
                merged_h1.append(chosen)
            h1 = merged_h1

            # データ行も grid ベースに切り替え (rowspan 展開済み)
            class GridRow:
                def __init__(self, cells):
                    self._cells = cells
                def find_all(self, _tags):
                    class Cell:
                        def __init__(self, text):
                            self._t = text
                        def get_text(self, *a, **k):
                            return self._t
                    return [Cell(c) for c in self._cells]
            rows = [GridRow(row) for row in grid]

    def find_col_in(arr, *needles, exact=False):
        for i, t in enumerate(arr):
            for n in needles:
                if (exact and t == n) or (not exact and n in t):
                    return i
        return None

    office_col = find_col_in(h1, "事業所", "会社名")
    segment_col = find_col_in(h1, "セグメント", "事業内容", "事業別")
    facility_col = find_col_in(h1, "設備の内容", "設備内容")
    employees_col = find_col_in(h1, "従業")
    booklist_col = find_col_in(h1, "帳簿価額")

    # 「主要な設備の状況」h1で帳簿価額列が無いなら設備テーブルでない可能性
    if booklist_col is None:
        return []

    # 1〜2階のサブヘッダー検出
    # TBS/日テレ型は raw の行構造がrowspanで崩れているため、
    # 「数値を含む最初の行」を見つけて、その手前までをサブヘッダー扱いする
    sub_levels: list[list[str]] = []
    data_start = 1
    # 最大5行先まで見る（通常のテーブルなら1-2行）
    for ri in range(1, min(6, len(rows))):
        cells = _row_cells(rows[ri])
        if _looks_like_subheader(cells):
            sub_levels.append(cells)
            data_start = ri + 1
        elif any(re.search(r"[\d,]{2,}", c) for c in cells):
            # 数値を含む → データ行開始
            break
        else:
            # 空行 or 短い注記行 → スキップ
            data_start = ri + 1

    # 最深サブヘッダー（行データの実列を表す）から列を特定
    deepest = sub_levels[-1] if sub_levels else []
    if not deepest:
        return []

    # Format D 検出: 2階サブヘッダーが「面積|金額」だけ → 土地列が2セルに展開されている
    #   sub_levels[0] = [建物, 機械, 土地, リース, その他, 合計]
    #   sub_levels[1] = [面積, 金額]  ← 土地の下のみ
    land_expanded = False
    if len(sub_levels) >= 2:
        second = sub_levels[1]
        if len(second) <= 3 and any("面積" in s for s in second) and any("金額" in s or "百万" in s for s in second):
            land_expanded = True
            # この場合 deepest は1階目（大列）を使う
            deepest = sub_levels[0]

    # 「土地」列の特定: deepest内に「土地」を含む位置
    land_sub = find_col_in(deepest, "土地")
    building_sub = find_col_in(deepest, "建物")
    machinery_sub = find_col_in(deepest, "機械")
    total_sub = find_col_in(deepest, "合計")

    n_main_cols_before = sum(
        1 for i, _ in enumerate(h1) if i < (booklist_col or 0)
    )
    # データ行内インデックス計算: メイン列(office等) + サブ列インデックス
    # Format Dでは land_sub 以降のインデックスが +1 ずれる（土地が2セル化）
    def data_idx(sub_idx: Optional[int]) -> Optional[int]:
        if sub_idx is None:
            return None
        base = n_main_cols_before + sub_idx
        if land_expanded and land_sub is not None and sub_idx > land_sub:
            base += 1  # 土地展開ぶんシフト
        return base

    land_idx = data_idx(land_sub)
    # Format D: land_idx は土地面積セル、土地金額は +1 隣
    land_amount_idx = None
    if land_expanded and land_idx is not None:
        land_amount_idx = land_idx + 1

    building_idx = data_idx(building_sub)
    machinery_idx = data_idx(machinery_sub)
    total_idx = data_idx(total_sub)
    employees_idx = None
    if employees_col is not None:
        sub_len = len(deepest) + (1 if land_expanded else 0)
        employees_idx = n_main_cols_before + sub_len

    records: list[FacilityRecord] = []
    # 前の行の office_name を覚えて「連続重複」を検出する (grid展開時の補足行)
    prev_office_key = None
    i = data_start
    while i < len(rows):
        cells = _row_cells(rows[i])
        if len(cells) < 4:
            # 面積補足行 or 注記
            if records and len(cells) == 1:
                area, leased = _parse_paren_area(cells[0])
                if area is not None:
                    records[-1].land_area_sqm = area * area_scale
                if leased is not None:
                    records[-1].leased_area_sqm = leased * area_scale
            i += 1
            continue

        # office セルから所在地分離
        office_text = cells[office_col] if office_col is not None and office_col < len(cells) else cells[0]
        office, addr = _split_office_address(office_text)

        # 地名のみ（事業所名なし）の場合 office が空になる
        if not office and addr:
            office = ""

        # grid展開された TBS/日テレ型: 同じ office が連続すると面積補足行。
        # その行の土地セルから面積を抜き出して前レコードに追記し、スキップ。
        office_key = (office, addr)
        if prev_office_key is not None and office_key == prev_office_key and records:
            # この行の土地セルを見て面積を抽出
            supp_idx = n_main_cols_before + (land_sub or 0)
            if supp_idx < len(cells):
                supp_cell = cells[supp_idx]
                a, l = _parse_paren_area(supp_cell)
                if a is not None:
                    records[-1].land_area_sqm = a * area_scale
                if l is not None:
                    records[-1].leased_area_sqm = l * area_scale
            i += 1
            continue

        def safe(idx):
            if idx is None or idx >= len(cells):
                return None
            return cells[idx]

        land_text = safe(land_idx)
        land_value = None
        land_area = None
        leased_area = None
        if land_expanded:
            # Format D: land_idx=面積セル, land_amount_idx=金額セル
            area_text = land_text or ""
            amount_text = safe(land_amount_idx) if land_amount_idx is not None else None
            a = _to_num(area_text.split("(")[0]) if area_text else None
            if a is not None:
                land_area = a * area_scale
            # 賃借面積(カッコ内)があれば拾う
            _, l = _parse_paren_area(area_text)
            if l is not None:
                leased_area = l * area_scale
            land_value = _to_num(amount_text) if amount_text else None
        elif land_text:
            head = land_text.split("(")[0]
            land_value = _to_num(head)
            a, l = _parse_paren_area(land_text)
            if a is not None:
                land_area = a * area_scale
            if l is not None:
                leased_area = l * area_scale

        # 全部空ならスキップ（区切り行）
        if land_value is None and not office and not addr:
            i += 1
            continue

        def scaled(v):
            return v * book_scale if v is not None else None

        rec = FacilityRecord(
            parser=parser_name,
            table_type=table_type,
            office_name=office,
            address_raw=addr,
            country="JP" if _is_japanese_address(addr) else "FOREIGN",
            segment=safe(segment_col) or "",
            facility_type=safe(facility_col) or "",
            land_book_value_mil=scaled(land_value),
            land_area_sqm=land_area,
            leased_area_sqm=leased_area,
            building_book_value_mil=scaled(_to_num(safe(building_idx))),
            machinery_book_value_mil=scaled(_to_num(safe(machinery_idx))),
            total_book_value_mil=scaled(_to_num(safe(total_idx))),
            employees=int(_to_num(safe(employees_idx)) or 0) or None,
        )
        records.append(rec)
        prev_office_key = (office, addr)
        i += 1

    return records


def parse_manufacturer(soup: BeautifulSoup) -> list[FacilityRecord]:
    """製造業全般。h4セクションでテーブル種別を判定。"""
    setsubi_h3 = _find_main_setsubi_section(soup)
    if not setsubi_h3:
        return []
    out: list[FacilityRecord] = []
    for h4_text, table in _collect_h4_tables(setsubi_h3):
        if not _is_setsubi_table(table):
            continue
        table_type = _classify_h4(h4_text)
        out.extend(_parse_setsubi_table_generic(table, table_type, "manufacturer"))
    return out


# ----- パーサ: 銀行業 -------------------------------------------------------


def parse_bank(soup: BeautifulSoup) -> list[FacilityRecord]:
    """銀行業。三菱UFJ・三井住友型。

    h1: [会社名|セグメント|店舗名|所在地|設備内容|土地|建物|動産|...|合計|従業員]
    h2: [面積(㎡)|帳簿価額(百万円)] (サブ・2セルのみ)
    最初のデータ行: 全列(h1+追加サブ列)
    後続のデータ行: rowspan されるセル(会社名, セグメント等)が省略 → セル数少なくなる

    所在地から逆算して列マッピングをずらす。
    """
    setsubi_h3 = _find_main_setsubi_section(soup)
    if not setsubi_h3:
        return []
    records: list[FacilityRecord] = []
    for h4_text, table in _collect_h4_tables(setsubi_h3):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        h1 = _row_cells(rows[0])
        h1_text = " ".join(h1)
        if "土地" not in h1_text or "投資予定" in h1_text or "合計" not in h1_text:
            continue

        book_scale = _detect_book_value_scale(table)

        def col_in(arr, *needles):
            for i, t in enumerate(arr):
                for n in needles:
                    if n in t:
                        return i
            return None

        company_col = col_in(h1, "会社名")
        office_col = col_in(h1, "店舗名")
        addr_col = col_in(h1, "所在地")
        facility_col = col_in(h1, "設備の内容", "設備内容")
        land_col = col_in(h1, "土地")
        building_col = col_in(h1, "建物")
        total_col = col_in(h1, "合計")
        employees_col = col_in(h1, "従業")

        if land_col is None or addr_col is None:
            continue

        # サブヘッダー検出
        sub = _row_cells(rows[1])
        has_sub = len(sub) <= 3 and any("面積" in s or "帳簿" in s for s in sub)
        start_row = 2 if has_sub else 1

        # データ行は土地列が「面積1セル + 簿価1セル」に展開されているので
        # 実データ行のセル数 = h1セル数 + 1 (土地が2セル化)。
        # ただし三菱UFJはなぜか土地が同セル「87,878 (9,084)」で1セル → h1セル数と同じ
        # → セル数判定で両対応

        # 各データ行を順次処理。rowspan省略を「会社名」を持続させて補正する
        last_cells: list[str] = []
        for ri in range(start_row, len(rows)):
            cells = _row_cells(rows[ri])
            if len(cells) < 4:
                continue

            n_h1 = len(h1)
            offset = 0
            extended = False
            if len(cells) >= n_h1 + 1:
                # 土地列が展開2セル化されたフルデータ行
                extended = True
                offset = 0
            elif len(cells) == n_h1:
                # 土地が1セル（既に簿価のみ or 簿価(面積)合体）
                offset = 0
            else:
                # rowspan省略行: 先頭セルが欠落
                offset = n_h1 - len(cells)
                if extended:
                    offset += 1  # 展開セルのぶんも含む

            def get(idx):
                if idx is None:
                    return ""
                actual = idx - offset
                if actual < 0 or actual >= len(cells):
                    return ""
                return cells[actual]

            addr_raw = get(addr_col)
            if not addr_raw or addr_raw in ("―", "－", "-"):
                continue
            office_name = get(office_col)

            # 土地セル取得 - extended なら 2セル
            if extended and land_col is not None:
                idx_area = land_col - offset
                idx_value = land_col - offset + 1
                if 0 <= idx_area < len(cells) and 0 <= idx_value < len(cells):
                    land_area = _to_num(cells[idx_area])
                    land_value = _to_num(cells[idx_value])
                else:
                    land_area = None
                    land_value = None
                # 後続列(building等)は +1 オフセット
                def get_after_land(col):
                    if col is None:
                        return ""
                    actual = col - offset + 1
                    if 0 <= actual < len(cells):
                        return cells[actual]
                    return ""
                building_text = get_after_land(building_col)
                total_text = get_after_land(total_col)
                emp_text = get_after_land(employees_col)
            else:
                land_text = get(land_col)
                head = land_text.split("(")[0] if land_text else ""
                land_value = _to_num(head)
                a, _ = _parse_paren_area(land_text or "")
                land_area = a
                building_text = get(building_col)
                total_text = get(total_col)
                emp_text = get(employees_col)

            if land_value is None and land_area is None:
                continue

            def bscale(v):
                return v * book_scale if v is not None else None

            rec = FacilityRecord(
                parser="bank",
                table_type=h4_text or "parent",
                office_name=_norm(office_name),
                address_raw=_norm(addr_raw),
                country="JP" if _is_japanese_address(addr_raw) else "FOREIGN",
                facility_type=get(facility_col),
                land_book_value_mil=bscale(land_value),
                land_area_sqm=land_area,
                building_book_value_mil=bscale(_to_num(building_text)),
                total_book_value_mil=bscale(_to_num(total_text)),
                employees=int(_to_num(emp_text) or 0) or None,
            )
            records.append(rec)
    return records


# ----- パーサ: 陸運業（鉄道型） ---------------------------------------------


def parse_railway(soup: BeautifulSoup) -> list[FacilityRecord]:
    """陸運業。JR型バリアント:

    JR東日本: row[0]=[名称|所在地|土地|建物], row[1]=[面積㎡|簿価|面積㎡|簿価]
              データ6セル [名称,所在地,土地面積,土地簿価,建物面積,建物簿価]
    JR東海:   row[0]=[区分|所在地|土地|建物], row[1]=[面積㎡|帳簿価額|帳簿価額]  ←3セル
              データ5セル [名称,所在地,土地面積,土地簿価,建物簿価]

    railway パーサで取れない子会社系テーブル(千㎡形式)は extract_facilities の
    フォールバックで manufacturer パーサが補完する。
    """
    setsubi_h3 = _find_main_setsubi_section(soup)
    if not setsubi_h3:
        return []
    records: list[FacilityRecord] = []
    for h4_text, table in _collect_h4_tables(setsubi_h3):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        h1 = _row_cells(rows[0])
        h1_join = " ".join(h1)
        if not ("土地" in h1_join and "建物" in h1_join and ("所在地" in h1_join or "区分" in h1_join)):
            continue
        if "投資予定" in h1_join or "完了予定" in h1_join:
            continue
        sub = _row_cells(rows[1])
        if not any("面積" in s or "帳簿" in s for s in sub):
            continue

        book_scale = _detect_book_value_scale(table)

        # サブヘッダーから建物に面積列があるか判定
        # JR東日本: ['面積(㎡)','帳簿価額','面積(㎡)','帳簿価額']
        # JR東海:   ['面積(㎡)','帳簿価額','帳簿価額']
        building_has_area = (
            sum(1 for s in sub if "面積" in s) >= 2
            or len(sub) >= 4
        )

        for ri in range(2, len(rows)):
            cells = _row_cells(rows[ri])
            min_len = 6 if building_has_area else 5
            if len(cells) < min_len:
                continue
            name = cells[0]
            addr = cells[1]
            if not addr:
                continue
            land_area = _to_num(cells[2])
            land_value = _to_num(cells[3])
            if building_has_area:
                building_area = _to_num(cells[4])
                building_value = _to_num(cells[5])
            else:
                building_area = None
                building_value = _to_num(cells[4])

            def bscale(v):
                return v * book_scale if v is not None else None

            rec = FacilityRecord(
                parser="railway",
                table_type=h4_text or "parent",
                office_name=_norm(name),
                address_raw=_norm(addr),
                country="JP" if _is_japanese_address(addr) else "FOREIGN",
                land_book_value_mil=bscale(land_value),
                land_area_sqm=land_area,
                building_book_value_mil=bscale(building_value),
                building_floor_area_sqm=building_area,
            )
            records.append(rec)
    return records


# ----- パーサ: 不動産業 -----------------------------------------------------


def parse_real_estate(soup: BeautifulSoup) -> list[FacilityRecord]:
    """不動産業。物件単位の表を抽出。2フォーマット対応:

    Format MEC (三菱地所型):
      h1: [名称, 所在地, 建物, 土地, その他, 合計]
      h2: [規模, 延面積㎡, 帳簿価額, 竣工, 面積㎡, 帳簿価額, 帳簿価額, 帳簿価額]
      data: [名称, 所在地, 規模, 建物延面積, 建物簿価, 竣工, 土地面積, 土地簿価, その他, 合計]

    Format MFR (三井不動産型):
      h1: [会社名, 名称(所在地), 用途, 構造, 竣工, 建物延床面積, 土地面積, 帳簿価額]
      h2: [建物, 土地, その他, 合計]
      data: [会社名, 名称(所在地), 用途, 構造, 竣工, 建物延床, 土地面積, 建物簿価, 土地簿価, その他, 合計]
    """
    setsubi_h3 = _find_main_setsubi_section(soup)
    if not setsubi_h3:
        return []
    records: list[FacilityRecord] = []

    for h4_text, table in _collect_h4_tables(setsubi_h3):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        h1 = _row_cells(rows[0])
        h2 = _row_cells(rows[1])
        h1_join = " ".join(h1)

        if "土地" not in h1_join or "建物" not in h1_join:
            continue
        if "投資予定" in h1_join or "完了予定" in h1_join or "賃借料" in h1_join:
            continue

        book_scale = _detect_book_value_scale(table)

        # フォーマット判別
        is_mec = "竣工" in " ".join(h2) and "面積" in " ".join(h2)
        is_mfr = "竣工" in h1_join and "土地面積" in h1_join

        if is_mec:
            records.extend(_parse_real_estate_mec(rows, book_scale))
        elif is_mfr:
            records.extend(_parse_real_estate_mfr(rows, book_scale))
    return records


def _parse_real_estate_mec(rows: list[Tag], book_scale: float = 1.0) -> list[FacilityRecord]:
    """三菱地所型: row[0]=[名称,所在地,建物,土地,その他,合計]、row[1]がサブ。"""
    out: list[FacilityRecord] = []
    bs = lambda v: v * book_scale if v is not None else None
    for ri in range(2, len(rows)):
        cells = _row_cells(rows[ri])
        if len(cells) < 8:
            continue
        try:
            name = cells[0]
            addr = cells[1]
            building_area = _to_num(cells[3])
            building_value = _to_num(cells[4])
            acquired_text = cells[5]
            land_area = _to_num(cells[6])
            land_value = _to_num(cells[7])
            total_value = _to_num(cells[9]) if len(cells) > 9 else None
            year_m = re.search(r"(\d{4})", acquired_text or "")
            acquired_year = int(year_m.group(1)) if year_m else None

            if not addr or not name:
                continue
            out.append(FacilityRecord(
                parser="real_estate",
                table_type="property",
                office_name=_norm(name),
                address_raw=_norm(addr),
                country="JP" if _is_japanese_address(addr) else "FOREIGN",
                land_book_value_mil=bs(land_value),
                land_area_sqm=land_area,
                building_book_value_mil=bs(building_value),
                building_floor_area_sqm=building_area,
                total_book_value_mil=bs(total_value),
                acquired_year=acquired_year,
            ))
        except (ValueError, IndexError):
            continue
    return out


def _parse_real_estate_mfr(rows: list[Tag], book_scale: float = 1.0) -> list[FacilityRecord]:
    """三井不動産型: data 11セル [会社名, 名称(所在地), 用途, 構造, 竣工, 建物延床, 土地面積, 建物簿価, 土地簿価, その他, 合計]"""
    out: list[FacilityRecord] = []
    bs = lambda v: v * book_scale if v is not None else None
    for ri in range(2, len(rows)):
        cells = _row_cells(rows[ri])
        if len(cells) < 11:
            continue
        try:
            company = cells[0]
            name_addr = cells[1]
            use_type = cells[2]
            acquired_text = cells[4]
            building_area = _to_num(cells[5])
            land_area = _to_num(cells[6])
            building_value = _to_num(cells[7])
            land_value = _to_num(cells[8])
            total_value = _to_num(cells[10])

            # name_addr = "丸の内三井ビルディング （東京都千代田区）"
            name, addr = _split_office_address(name_addr)

            year_m = re.search(r"(\d{4})", acquired_text or "")
            acquired_year = int(year_m.group(1)) if year_m else None

            # ヘッダー区切り行（②その他, ①賃貸用建物等）スキップ
            if not addr and not name:
                continue
            if not addr:
                continue

            out.append(FacilityRecord(
                parser="real_estate",
                table_type="property",
                office_name=_norm(name),
                address_raw=_norm(addr),
                country="JP" if _is_japanese_address(addr) else "FOREIGN",
                facility_type=_norm(use_type),
                land_book_value_mil=bs(land_value),
                land_area_sqm=land_area,
                building_book_value_mil=bs(building_value),
                building_floor_area_sqm=building_area,
                total_book_value_mil=bs(total_value),
                acquired_year=acquired_year,
            ))
        except (ValueError, IndexError):
            continue
    return out


# ----- ディスパッチャー -----------------------------------------------------


# 統一 grid-based parser (parse_manufacturer) で全業種に対応するため
# 業種別パーサは廃止。複雑な構造（阪急阪神HD のような コングロマリット
# 鉄道会社）でも parse_manufacturer の方が正確に処理できる。
PARSER_MAP: dict[str, Callable[[BeautifulSoup], list[FacilityRecord]]] = {}


def select_parser(industry: str) -> Callable[[BeautifulSoup], list[FacilityRecord]]:
    return PARSER_MAP.get(industry, parse_manufacturer)


def extract_facilities(zip_path: str | Path, industry: Optional[str] = None) -> list[FacilityRecord]:
    """設備データを抽出。industry = JPX 33業種名。"""
    html = _load_setsubi_html(zip_path)
    if html is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    parser = select_parser(industry or "")
    records = parser(soup)
    # フォールバック: 専門パーサで0件なら manufacturer で再試行
    if not records and parser is not parse_manufacturer:
        records = parse_manufacturer(soup)
    return records


# ----- CLI ------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_setsubi.py <edinet_zip> [industry]")
        sys.exit(1)
    industry = sys.argv[2] if len(sys.argv) > 2 else None
    records = extract_facilities(sys.argv[1], industry)
    print(json.dumps([asdict(r) for r in records], ensure_ascii=False, indent=2))
    print(f"\n--- {len(records)} records ---", file=sys.stderr)
    jp = [r for r in records if r.country == "JP"]
    print(f"  JP: {len(jp)}, with land_area: {sum(1 for r in jp if r.land_area_sqm)}", file=sys.stderr)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
