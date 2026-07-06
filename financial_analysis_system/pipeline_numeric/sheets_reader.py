"""
Google Sheets数値取得モジュール
StockFlowのGoogle Sheetsから全財務数値を取得（取捨選択なし）
"""

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import Optional, Dict, Any, List
from pathlib import Path


class SheetsReader:
    """Google Sheetsから財務データを全て取得"""
    
    # タブ名一覧
    SHEET_NAMES = [
        'Headers_detailed_pl',
        'Headers_detailed_bs', 
        'Headers_detailed_cf',
        'Headers_additional',
    ]
    
    # 検索キーのカラム名
    TICKER_COLUMN = 'ticker'
    FISCAL_YEAR_COLUMN = 'fiscal_year'
    
    def __init__(self, credentials_path: str, spreadsheet_name_or_id: str):
        """
        Args:
            credentials_path: Google APIの認証情報JSONパス
            spreadsheet_name_or_id: StockFlowのスプレッドシート名またはID
        """
        self.credentials_path = Path(credentials_path)
        self.spreadsheet_name_or_id = spreadsheet_name_or_id
        self._client = None
        self._spreadsheet = None
    
    def _connect(self):
        """Google Sheetsに接続"""
        if self._client is None:
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                str(self.credentials_path), scope
            )
            self._client = gspread.authorize(creds)
            
            # 名前で開くか、IDで開くか判定
            # IDは通常44文字程度の英数字、名前は日本語を含むことが多い
            try:
                if len(self.spreadsheet_name_or_id) > 30 and self.spreadsheet_name_or_id.isalnum():
                    # IDっぽい場合
                    self._spreadsheet = self._client.open_by_key(self.spreadsheet_name_or_id)
                else:
                    # 名前の場合
                    self._spreadsheet = self._client.open(self.spreadsheet_name_or_id)
                print(f"  ✅ Google Sheets接続: {self._spreadsheet.title}")
            except gspread.exceptions.SpreadsheetNotFound:
                print(f"  ❌ スプレッドシートが見つかりません: {self.spreadsheet_name_or_id}")
                raise
    
    def get_sheet_data(self, sheet_name: str, ticker_code: str, fiscal_year: int) -> Optional[Dict[str, Any]]:
        """
        特定シートから該当企業・年度の全データを取得
        
        Args:
            sheet_name: シート名
            ticker_code: 証券コード
            fiscal_year: 決算年度
        
        Returns:
            該当行の全カラムデータ（辞書形式）
        """
        self._connect()
        
        try:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            records = worksheet.get_all_records()
            
            for record in records:
                record_ticker = str(record.get(self.TICKER_COLUMN, ''))
                record_year = record.get(self.FISCAL_YEAR_COLUMN, 0)
                
                try:
                    record_year = int(record_year) if record_year else 0
                except (ValueError, TypeError):
                    record_year = 0
                
                if record_ticker == str(ticker_code) and record_year == fiscal_year:
                    return record
            
            return None
            
        except gspread.exceptions.WorksheetNotFound:
            print(f"  ⚠️ シート '{sheet_name}' が見つかりません")
            return None
        except Exception as e:
            print(f"  ⚠️ シート '{sheet_name}' 読み込みエラー: {e}")
            return None
    
    def get_all_data(self, ticker_code: str, fiscal_year: int) -> Dict[str, Dict[str, Any]]:
        """
        全シートから該当企業・年度の全データを取得
        
        Args:
            ticker_code: 証券コード
            fiscal_year: 決算年度
        
        Returns:
            {シート名: {カラム名: 値, ...}, ...}
        """
        self._connect()
        
        result = {}
        
        for sheet_name in self.SHEET_NAMES:
            data = self.get_sheet_data(sheet_name, ticker_code, fiscal_year)
            if data:
                result[sheet_name] = data
        
        return result
    
    def get_merged_data(self, ticker_code: str, fiscal_year: int) -> Dict[str, Any]:
        """
        全シートのデータを1つの辞書にマージして取得
        同名カラムは後のシートで上書き（シート名プレフィックス付きも保持）
        
        Args:
            ticker_code: 証券コード
            fiscal_year: 決算年度
        
        Returns:
            全カラムをマージした辞書
        """
        all_data = self.get_all_data(ticker_code, fiscal_year)
        
        merged = {
            'ticker_code': ticker_code,
            'fiscal_year': fiscal_year,
        }
        
        # シート名プレフィックス付きで全データ保持
        for sheet_name, data in all_data.items():
            # プレフィックスなしでマージ（後勝ち）
            for key, value in data.items():
                merged[key] = value
            
            # プレフィックス付きでも保持（重複対策）
            sheet_prefix = sheet_name.replace('Headers_detailed_', '').replace('Headers_', '')
            for key, value in data.items():
                merged[f"{sheet_prefix}_{key}"] = value
        
        return merged
    
    def get_flat_metrics(self, ticker_code: str, fiscal_year: int) -> Dict[str, Any]:
        """
        数値データのみを抽出してフラットな辞書で返す
        （タグ情報、コンテキスト情報は除外）
        
        Args:
            ticker_code: 証券コード
            fiscal_year: 決算年度
        
        Returns:
            数値フィールドのみの辞書
        """
        merged = self.get_merged_data(ticker_code, fiscal_year)
        
        # タグ、コンテキスト、プライオリティ以外のカラムを抽出
        exclude_suffixes = ('_tag', '_context', '_priority', '_tag_2', '_context_2', '_priority_2', '_tag_3', '_context_3', '_priority_3')
        
        result = {}
        for key, value in merged.items():
            # 除外対象のサフィックスをチェック
            if any(key.endswith(suffix) for suffix in exclude_suffixes):
                continue
            
            # プレフィックス付きの重複は除外（元のキーを優先）
            if '_' in key and key.split('_', 1)[0] in ('pl', 'bs', 'cf', 'additional'):
                continue
            
            result[key] = value
        
        return result
    
    def get_all_sheets_raw(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        全シートの全データを取得（フィルタなし）
        
        Returns:
            {シート名: [行データのリスト], ...}
        """
        self._connect()
        
        result = {}
        
        for sheet_name in self.SHEET_NAMES:
            try:
                worksheet = self._spreadsheet.worksheet(sheet_name)
                records = worksheet.get_all_records()
                result[sheet_name] = records
                print(f"  ✅ {sheet_name}: {len(records)}行")
            except gspread.exceptions.WorksheetNotFound:
                print(f"  ⚠️ シート '{sheet_name}' が見つかりません")
                result[sheet_name] = []
            except Exception as e:
                print(f"  ⚠️ シート '{sheet_name}' 読み込みエラー: {e}")
                result[sheet_name] = []
        
        return result
    
    def get_all_years(self, ticker_code: str) -> List[int]:
        """特定企業の全年度を取得"""
        self._connect()
        
        years_found = set()
        
        for sheet_name in self.SHEET_NAMES:
            try:
                worksheet = self._spreadsheet.worksheet(sheet_name)
                records = worksheet.get_all_records()
                
                for record in records:
                    if str(record.get(self.TICKER_COLUMN, '')) == str(ticker_code):
                        year = record.get(self.FISCAL_YEAR_COLUMN, 0)
                        try:
                            year = int(year) if year else 0
                        except (ValueError, TypeError):
                            year = 0
                        if year > 0:
                            years_found.add(year)
            except Exception:
                continue
        
        return sorted(years_found)
    
    def get_all_tickers(self) -> List[str]:
        """全証券コードを取得"""
        self._connect()
        
        tickers = set()
        
        # PLシートから取得
        try:
            worksheet = self._spreadsheet.worksheet('Headers_detailed_pl')
            records = worksheet.get_all_records()
            
            for record in records:
                ticker = str(record.get(self.TICKER_COLUMN, ''))
                if ticker:
                    tickers.add(ticker)
        except Exception as e:
            print(f"  ⚠️ 証券コード一覧取得エラー: {e}")
        
        return sorted(tickers)
    
    def get_company_name(self, ticker_code: str) -> Optional[str]:
        """企業名を取得"""
        self._connect()
        
        # PLシートから取得（name_jaカラム）
        try:
            worksheet = self._spreadsheet.worksheet('Headers_detailed_pl')
            records = worksheet.get_all_records()
            
            for record in records:
                if str(record.get(self.TICKER_COLUMN, '')) == str(ticker_code):
                    return record.get('name_ja', '') or record.get('company_name', '')
        except Exception:
            pass
        
        # BSシートからも試行（company_nameカラム）
        try:
            worksheet = self._spreadsheet.worksheet('Headers_detailed_bs')
            records = worksheet.get_all_records()
            
            for record in records:
                if str(record.get(self.TICKER_COLUMN, '')) == str(ticker_code):
                    return record.get('company_name', '')
        except Exception:
            pass
        
        return None


# テスト用
if __name__ == "__main__":
    import json
    
    # 設定（実際のパスに変更必要）
    CREDENTIALS_PATH = "path/to/credentials.json"
    SPREADSHEET_ID = "your-spreadsheet-id"
    
    reader = SheetsReader(CREDENTIALS_PATH, SPREADSHEET_ID)
    
    # トヨタ2024年度の全データ取得
    print("=== 全データ取得テスト ===")
    all_data = reader.get_all_data("7203", 2024)
    
    for sheet_name, data in all_data.items():
        print(f"\n{sheet_name}: {len(data)}カラム")
        # 最初の10カラムだけ表示
        for i, (key, value) in enumerate(data.items()):
            if i >= 10:
                print(f"  ... 他{len(data) - 10}カラム")
                break
            print(f"  {key}: {value}")
    
    # 数値のみ取得
    print("\n=== 数値のみ取得テスト ===")
    flat = reader.get_flat_metrics("7203", 2024)
    print(f"数値カラム数: {len(flat)}")
    print(json.dumps(flat, indent=2, ensure_ascii=False, default=str)[:2000])