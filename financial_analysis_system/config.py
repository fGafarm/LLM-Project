#!/usr/bin/env python3
"""
Financial Analysis System - 共通設定
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional


def get_desktop_path() -> Path:
    """Windows日本語環境対応のデスクトップパス取得"""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "デスクトップ",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "デスクトップ",
    ]
    for path in candidates:
        if path.exists():
            return path
    return home / "Desktop"


@dataclass
class Config:
    """システム全体の設定"""
    
    # ============================================================
    # パス設定
    # ============================================================
    DESKTOP_PATH: Path = get_desktop_path()
    PDF_DIR: Path = DESKTOP_PATH / "PDF"
    EDINET_DIR: Path = DESKTOP_PATH / "EDINET_DL"
    OUTPUT_DIR: Path = DESKTOP_PATH / "financial_analysis_output"
    CHROMADB_DIR: Path = Path.home() / "chromadb_financial"
    
    # ============================================================
    # Ollama設定
    # ============================================================
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma2:27b"
    OLLAMA_MODEL_FAST: str = "gemma2:9b"  # 高速処理用
    
    # ============================================================
    # 並列処理設定
    # ============================================================
    MAX_WORKERS: int = 3  # 並列ワーカー数
    BATCH_SIZE: int = 5   # バッチサイズ
    
    # ============================================================
    # チャンク設定
    # ============================================================
    CHUNK_SIZE: int = 2000  # トークン数（1000→2000に増加）
    CHUNK_OVERLAP: int = 200
    
    # ============================================================
    # 数値パイプライン設定（Google Sheets）
    # ============================================================
    # Google Sheets認証
    GOOGLE_SHEETS_CREDENTIALS: Path = Path.home() / "credentials" / "service-account.json"
    
    # StockFlow既存スプレッドシート（タブ追加で対応）
    STOCKFLOW_SPREADSHEET_ID: str = ""  # 既存のIDを設定
    
    # タブ名
    SHEET_TAB_NUMERIC: str = "numeric_data"      # 数値データ
    SHEET_TAB_RAPTOR: str = "raptor_summaries"   # RAPTOR要約
    SHEET_TAB_VALIDATION: str = "validation_log" # 検証ログ
    
    # ============================================================
    # 異常値検知ルール
    # ============================================================
    VALIDATION_RULES = {
        'average_age': {'min': 20, 'max': 70, 'unit': '歳'},
        'average_salary': {'min': 1_000_000, 'max': 30_000_000, 'unit': '円'},
        'revenue_yoy_ratio': {'min': 0.3, 'max': 3.0, 'unit': '倍'},
        'operating_margin': {'min': -0.5, 'max': 0.5, 'unit': '%'},
        'roe': {'min': -1.0, 'max': 1.0, 'unit': '%'},
        'employee_count': {'min': 1, 'max': 1_000_000, 'unit': '人'},
    }
    
    # ============================================================
    # 財務指標定義（XBRLから抽出する項目）
    # ============================================================
    CORE_METRICS = [
        # 損益計算書
        'revenue',              # 売上高
        'operating_income',     # 営業利益
        'ordinary_income',      # 経常利益
        'net_income',          # 当期純利益
        
        # 貸借対照表
        'total_assets',        # 総資産
        'total_equity',        # 純資産
        'total_liabilities',   # 負債合計
        
        # キャッシュフロー
        'cf_operating',        # 営業CF
        'cf_investing',        # 投資CF
        'cf_financing',        # 財務CF
        
        # 財務指標
        'eps',                 # 1株当たり利益
        'bps',                 # 1株当たり純資産
        'roe',                 # 自己資本利益率
        'roa',                 # 総資産利益率
        
        # その他
        'employee_count',      # 従業員数
        'r_and_d_expense',     # 研究開発費
        'capex',               # 設備投資額
        'dividend_per_share',  # 1株当たり配当
    ]
    
    # ============================================================
    # RAPTORセクション定義（有価証券報告書用）
    # ============================================================
    FINANCIAL_SECTIONS = [
        "表紙・目次",
        "企業の概況",
        "事業の状況",
        "経営成績の分析",
        "財政状態の分析",
        "キャッシュ・フローの状況",
        "設備の状況",
        "提出会社の状況",
        "経理の状況",
        "連結財務諸表",
        "個別財務諸表",
        "株式の状況",
        "配当政策",
        "コーポレートガバナンス",
        "事業等のリスク",
        "経営上の重要な契約",
        "研究開発活動",
        "その他",
    ]


# グローバル設定インスタンス
config = Config()