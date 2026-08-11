"""
EDINETコードリストから上場企業リストを抽出
- 金融庁EDINETから最新のコードリストをダウンロード
- 上場企業のみ抽出
- ticker（4桁）とcompany_name（企業名）をExcel/CSV出力
"""

import os
import sys
import io
import zipfile

# 依存パッケージチェック
MISSING_PACKAGES = []

try:
    import requests
except ImportError:
    MISSING_PACKAGES.append("requests")

try:
    import pandas as pd
except ImportError:
    MISSING_PACKAGES.append("pandas")

try:
    import openpyxl
except ImportError:
    MISSING_PACKAGES.append("openpyxl")

if MISSING_PACKAGES:
    print("⚠️ 必要なパッケージがインストールされていません")
    print(f"  python -m pip install {' '.join(MISSING_PACKAGES)}")
    sys.exit(1)

from pathlib import Path
from datetime import datetime

# =============================================================================
# 設定
# =============================================================================

# EDINETコードリストURL（公式）
EDINET_CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

# 出力先
OUTPUT_DIR = Path(r"C:\Users\shun nabeno\Desktop\PDF")
OUTPUT_EXCEL = OUTPUT_DIR / "edinet_listed_companies.xlsx"
OUTPUT_CSV = OUTPUT_DIR / "edinet_listed_companies.csv"

# =============================================================================
# メイン処理
# =============================================================================

def download_edinet_codelist() -> pd.DataFrame:
    """EDINETコードリストをダウンロードしてDataFrameで返す"""
    print("EDINETコードリストをダウンロード中...")
    print(f"  URL: {EDINET_CODELIST_URL}")
    
    try:
        response = requests.get(EDINET_CODELIST_URL, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ ダウンロード失敗: {e}")
        sys.exit(1)
    
    # ZIPを展開
    print("  ZIPを展開中...")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # CSVファイルを探す
        csv_files = [f for f in zf.namelist() if f.endswith('.csv')]
        if not csv_files:
            print("❌ ZIPにCSVファイルが見つかりません")
            sys.exit(1)
        
        csv_filename = csv_files[0]
        print(f"  ファイル: {csv_filename}")
        
        # CSVを読み込み（Shift-JIS、ヘッダーは2行目から）
        with zf.open(csv_filename) as f:
            # Shift-JISでデコード
            content = f.read().decode('cp932')
            
            # 1行目はメタ情報なのでスキップ、2行目がヘッダー
            df = pd.read_csv(
                io.StringIO(content),
                skiprows=1,  # 1行目スキップ
                encoding='utf-8',  # 既にデコード済み
                dtype=str  # 全て文字列で読み込み
            )
    
    print(f"  全{len(df)}件取得")
    return df


def extract_listed_companies(df: pd.DataFrame) -> pd.DataFrame:
    """上場企業のみ抽出してticker, company_nameに整形"""
    
    # カラム名を確認
    print("\n利用可能なカラム:")
    for i, col in enumerate(df.columns):
        print(f"  {i}: {col}")
    
    # 上場区分でフィルタ
    # カラム名は「上場区分」または類似の名前
    listing_col = None
    for col in df.columns:
        if '上場' in col and '区分' in col:
            listing_col = col
            break
    
    if listing_col is None:
        # カラム名が見つからない場合、インデックスで指定
        # 通常は13番目のカラム（0始まり）
        print("\n⚠️ '上場区分'カラムが見つかりません。カラム構造を確認します...")
        listing_col = df.columns[13] if len(df.columns) > 13 else None
    
    print(f"\n上場区分カラム: {listing_col}")
    
    # 上場区分の値を確認
    if listing_col:
        print(f"上場区分の値: {df[listing_col].unique()[:10]}")
    
    # 上場企業のみ抽出
    # 「上場」という文字を含む行
    if listing_col:
        listed_df = df[df[listing_col].str.contains('上場', na=False)]
    else:
        print("❌ 上場区分カラムが特定できません")
        sys.exit(1)
    
    print(f"上場企業: {len(listed_df)}件")
    
    # 証券コードカラムを探す
    sec_code_col = None
    for col in df.columns:
        if '証券コード' in col or 'secCode' in col.lower():
            sec_code_col = col
            break
    
    if sec_code_col is None:
        # カラム名が見つからない場合
        sec_code_col = df.columns[5] if len(df.columns) > 5 else None
    
    print(f"証券コードカラム: {sec_code_col}")
    
    # 提出者名カラムを探す
    name_col = None
    for col in df.columns:
        if '提出者名' in col or '企業名' in col:
            name_col = col
            break
    
    if name_col is None:
        name_col = df.columns[6] if len(df.columns) > 6 else None
    
    print(f"提出者名カラム: {name_col}")
    
    # EDINETコードカラムを探す
    edinet_code_col = None
    for col in df.columns:
        if 'EDINET' in col.upper() or 'ＥＤＩＮＥＴコード' in col:
            edinet_code_col = col
            break
    
    if edinet_code_col is None:
        edinet_code_col = df.columns[0] if len(df.columns) > 0 else None
    
    print(f"EDINETコードカラム: {edinet_code_col}")
    
    # 必要なカラムを抽出
    result_df = listed_df[[sec_code_col, name_col, edinet_code_col]].copy()
    result_df.columns = ['ticker_raw', 'company_name', 'edinet_code']
    
    # 証券コードを4桁に正規化（5桁の場合は末尾の0を削除）
    def normalize_ticker(ticker):
        if pd.isna(ticker) or str(ticker).strip() == '':
            return None
        ticker = str(ticker).strip()
        # 5桁の場合、末尾が0なら削除
        if len(ticker) == 5 and ticker.endswith('0'):
            return ticker[:4]
        # 4桁ならそのまま
        if len(ticker) == 4:
            return ticker
        # その他（3桁以下など）はそのまま
        return ticker
    
    result_df['ticker'] = result_df['ticker_raw'].apply(normalize_ticker)
    
    # 証券コードがないものは除外
    result_df = result_df[result_df['ticker'].notna()]
    result_df = result_df[result_df['ticker'] != '']
    
    # 重複除去（同じtickerとcompany_nameの組み合わせ）
    result_df = result_df.drop_duplicates(subset=['ticker', 'company_name'])
    
    # 最終的なカラム構成
    final_df = result_df[['ticker', 'company_name', 'edinet_code']].copy()
    final_df = final_df.sort_values('ticker').reset_index(drop=True)
    
    print(f"\n最終結果: {len(final_df)}社")
    
    return final_df


def save_results(df: pd.DataFrame):
    """結果をExcelとCSVで保存"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Excel出力
    print(f"\nExcel出力: {OUTPUT_EXCEL}")
    df.to_excel(OUTPUT_EXCEL, index=False, sheet_name='上場企業リスト')
    
    # CSV出力（UTF-8 BOM付き）
    print(f"CSV出力: {OUTPUT_CSV}")
    df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    
    print("\n✅ 保存完了")


def show_summary(df: pd.DataFrame):
    """サマリーを表示"""
    print("\n" + "=" * 60)
    print("📊 抽出結果サマリー")
    print("=" * 60)
    print(f"総数: {len(df)}社")
    
    # ティッカー範囲別の集計
    ranges = {
        '1000番台': (1000, 1999),
        '2000番台': (2000, 2999),
        '3000番台': (3000, 3999),
        '4000番台': (4000, 4999),
        '5000番台': (5000, 5999),
        '6000番台': (6000, 6999),
        '7000番台': (7000, 7999),
        '8000番台': (8000, 8999),
        '9000番台': (9000, 9999),
    }
    
    print("\nティッカー番号帯別:")
    df['ticker_int'] = pd.to_numeric(df['ticker'], errors='coerce')
    for name, (start, end) in ranges.items():
        count = len(df[(df['ticker_int'] >= start) & (df['ticker_int'] <= end)])
        if count > 0:
            print(f"  {name}: {count}社")
    
    # サンプル表示
    print("\nサンプル（最初の10社）:")
    for _, row in df.head(10).iterrows():
        print(f"  {row['ticker']} {row['company_name']}")


def main():
    print("\n" + "=" * 60)
    print("EDINETコードリストから上場企業リスト抽出")
    print("=" * 60)
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n実行モードを選択してください:\n")
    print("  1. 全上場企業リストを取得")
    print("  2. 現在のGoogle Sheetsリストと比較")
    print("  3. 終了")
    print()
    
    choice = input("番号を入力 [1-3]: ").strip()
    
    if choice == "1":
        # 1. EDINETコードリストをダウンロード
        raw_df = download_edinet_codelist()
        
        # 2. 上場企業のみ抽出
        listed_df = extract_listed_companies(raw_df)
        
        # 3. サマリー表示
        show_summary(listed_df)
        
        # 4. 保存
        save_results(listed_df)
        
        print("\n処理完了！")
    
    elif choice == "2":
        print("\n現在のGoogle Sheetsリストとの比較機能は未実装です")
        print("まず「1」で全リストを取得してください")
    
    elif choice == "3":
        print("\n終了します")
        return
    
    else:
        print("1〜3の番号を入力してください")
    
    # 続けるか確認
    print()
    again = input("続けて実行しますか？ [y/N]: ").strip().lower()
    if again == "y":
        main()


if __name__ == "__main__":
    main()