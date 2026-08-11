#!/usr/bin/env python3
"""
PORTA v10.1 - 品質改善版 + 再開機能対応

===============================================================================
v10.1の改善点（2026-01-31）
===============================================================================
【再開機能】（新規）
- バッチ処理の進捗を batch_progress.json に保存
- PCシャットダウン後も途中から再開可能
- メニュー「7. 進捗状況/再開/クリア」から管理
- メニュー「8. 全企業一括処理」で大規模バッチ実行

【日経225優先処理】（新規）
- メニュー「9. 日経225優先処理」
- 日経225銘柄のみ処理 or 日経225を先に全企業処理
- nikkei225_codes.txt から銘柄リストを読み込み

v10の品質問題を修正:

【修正1: XBRLタグマッピングの拡張】
- 売上高タグを3種類→16種類に拡張（IFRS/JGAAP/USGAAP対応）
- 営業利益タグを2種類→9種類に拡張
- 10社中6社で発生していた主要KPI欠損を解消

【修正2: 企業コンテキストの厳格化】
- プロンプトに分析対象企業を明示
- 他社情報の混入を防止する制約を追加
- 企業固有性チェックルールを追加

【修正3: 品質検証の厳格化】
- 主要KPI（売上高・営業利益）欠損時のConfidenceスコアを最大50%に制限
- 高Confidenceでも実質的に問題があるケースを解消

===============================================================================
v10の設計思想（継承）
===============================================================================
v9.6.1（質問応答方式 + 計算改善）をベースに、v9.5.11の優れた機能を追加統合。
一切の機能削除なし。

【v9.6.1から完全継承】
- 質問応答方式（SECTION_QUESTIONS、21質問）
- analyze_section_qa関数
- _build_xbrl_summary関数
- process_section_qa_mode関数
- XBRLチェック機構（レポート内数値とXBRLの整合性検証＆自動修正）
- 投資銀行指標（EBITDA, FCF, ROIC, Net Debt/EBITDA）
- 時系列テーブル（5年分）
- D/Eレシオ修正（倍率として計算、×100しない）
- 配当計算改善（配当総額÷株式数）
- 誤字後処理
- QA_NUM_CTX等の設定

【v9.5.11から追加統合】
- BOILERPLATE_PATTERNS（定型文除外）
- score_event_text（イベントスコアリング）
- pick_candidate_lines_with_context（キーワードベース候補行抽出）
- IMPORTANT_KEYWORDS（重要キーワードリスト）
- JSON抽出方式（make_extraction_prompt）
- 抽出ログ保存（ExtractionLogger完全版）
- is_boilerplate関数
- インタラクティブメニュー完全版

【v10で追加】
- ハイブリッド抽出モード（質問応答 + JSON抽出の併用）
- 抽出モード選択機能

===============================================================================
使い方
===============================================================================
python Run_integrated_v10.py --company 1301 --year 2022 --industry food
python Run_integrated_v10.py  # インタラクティブモード
"""

import sys
import os
import re
import argparse
import json
import zipfile

# UTF-8 output for Windows console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass
import requests
import subprocess
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from functools import lru_cache

import warnings
warnings.filterwarnings("ignore")

# ============================================================
# グローバル変数（企業コンテキスト管理）
# ============================================================
_current_company_context: Optional[str] = None

# ============================================================
# logging設定
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 依存ライブラリ
# ============================================================
try:
    from lxml import etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False


# ============================================================
# 設定（v9.6.1 + v9.5.11統合）
# ============================================================
class Config:
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "gemma2:9b"        # 抽出用（JSON安定）
    OLLAMA_MODEL_FINAL = "qwen3:14b"  # 最終レポート用（日本語品質）

    SECTIONS_BASE = Path(r"E:\PDF\本番用PDF分割")  # Yuho_splitter_v4の出力先（本番用）
    XBRL_BASE = Path(r"E:\PDF\PDF+XBRL")
    PROJECT_DIR = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\backend")

    COMPANY_SPREADSHEET = "All_company"
    COMPANY_TAB = "Company"
    TAXONOMY_SPREADSHEET = "StockFlow企業データ"
    TAXONOMY_TAB = "taxonomy_config2"

    TAG_CACHE_FILE = Path("./xbrl_tags_cache.json")

    # ★ v9.6.1: 質問応答用の設定
    QA_NUM_CTX = 20000       # 質問応答用コンテキスト（大きめ）
    QA_NUM_PREDICT = 3000    # 質問応答用出力（詳細な商材情報のため増加）
    QA_TEMPERATURE = 0.0     # 決定論的動作（同じ入力→同じ出力）

    # ★ v9.5.11: チャンク設定
    CHUNK_SIZE = 7000
    CHUNK_OVERLAP = 250
    
    # LLM設定
    EXTRACT_NUM_CTX = 8192
    EXTRACT_NUM_PREDICT = 1200
    FINAL_NUM_CTX = 16000
    FINAL_NUM_PREDICT = 6000

    TEMPERATURE = 0.0        # JSON抽出は決定論的に
    FINAL_TEMPERATURE = 0.3  # 最終レポートは少し柔軟に（0.55→0.3に下げて安定性向上）

    USE_REFLECTION = False
    VERIFY_NUMBERS = True
    MAX_RETRIES = 1
    RETRY_DELAY = 0.5

    # ★ v9.5.11: MDA全文投入設定
    MDA_NO_COMPRESS = True
    MDA_FULLTEXT_LIMIT = 60000
    MDA_CHUNK_SIZE = 6000
    MDA_CHUNK_OVERLAP = 500

    # ★ v9.5.11: 抽出ログ設定
    SAVE_EXTRACTION_LOGS = True
    LOG_RAW_TEXT = True
    LOG_CHUNKS = True
    LOG_LLM_RESPONSE = True

    # ★ v10.2: PDF filtering (Yuho_splitter_v4 output)
    ENABLE_PDF_FILTERING = True  # Set to False to read all PDFs (old behavior)

    # ★ v10: 抽出モード選択
    # "qa" = 質問応答方式（v9.6.1）
    # "json" = JSON抽出方式（v9.5.11）
    # "hybrid" = 両方併用（v10新機能）
    EXTRACTION_MODE = "hybrid"

    # ★ v10.1: 進捗管理（再開機能）
    PROGRESS_FILE = Path("./batch_progress.json")

    # ★ v10.1: 日経225優先処理
    NIKKEI225_FILE = Path("./nikkei225_codes.txt")


# ============================================================
# ★ v10.1: 日経225銘柄リスト
# ============================================================
def load_nikkei225_codes() -> set:
    """日経225銘柄コードを読み込み"""
    codes = set()
    if Config.NIKKEI225_FILE.exists():
        with open(Config.NIKKEI225_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                code = line.strip()
                if code:
                    codes.add(code)
    return codes


def sort_companies_nikkei_first(companies: List['Company'], nikkei_codes: set = None) -> List['Company']:
    """
    日経225銘柄を先頭に、それ以外を後ろにソート
    日経225内では証券コード順
    """
    if nikkei_codes is None:
        nikkei_codes = load_nikkei225_codes()

    nikkei = []
    others = []

    for c in companies:
        if c.code in nikkei_codes:
            nikkei.append(c)
        else:
            others.append(c)

    # 各グループ内で証券コード順にソート
    nikkei.sort(key=lambda x: x.code)
    others.sort(key=lambda x: x.code)

    return nikkei + others


# ============================================================
# ★ v10.1: 進捗管理クラス（再開機能対応）
# ============================================================
class BatchProgressTracker:
    """
    バッチ処理の進捗を管理し、中断後の再開を可能にする
    PCシャットダウン時でも進捗を保持
    """

    def __init__(self, progress_file: Path = None):
        self.progress_file = progress_file or Config.PROGRESS_FILE
        self.progress = self._load_progress()

    def _load_progress(self) -> Dict:
        """進捗ファイルを読み込み"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"進捗ファイル読み込みエラー: {e}")
        return {}

    def _save_progress(self):
        """進捗をファイルに保存（即座に永続化）"""
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.progress, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"進捗ファイル保存エラー: {e}")

    def get_batch_key(self, year: str, doc_type: str) -> str:
        """バッチ識別キーを生成"""
        return f"{year}_{doc_type}"

    def start_batch(self, year: str, doc_type: str, companies: List['Company'],
                    force_restart: bool = False) -> Tuple[List['Company'], int]:
        """
        バッチ処理を開始/再開

        Returns:
            Tuple[残りの企業リスト, スキップした企業数]
        """
        batch_key = self.get_batch_key(year, doc_type)

        if force_restart or batch_key not in self.progress:
            # 新規開始
            self.progress[batch_key] = {
                "started_at": datetime.now().isoformat(),
                "total_companies": len(companies),
                "target_codes": [c.code for c in companies],  # ★ 対象企業コードを保存
                "completed": [],
                "skipped": [],
                "errors": [],
                "status": "running"
            }
            self._save_progress()
            return companies, 0

        # 再開：完了済み企業をスキップ
        batch = self.progress[batch_key]
        completed_codes = set(batch.get("completed", []))
        skipped_codes = set(batch.get("skipped", []))
        error_codes = set(batch.get("errors", []))

        all_processed = completed_codes | skipped_codes | error_codes

        remaining = [c for c in companies if c.code not in all_processed]
        skipped_count = len(all_processed)

        if remaining:
            batch["status"] = "running"
            batch["resumed_at"] = datetime.now().isoformat()
            self._save_progress()

        return remaining, skipped_count

    def mark_completed(self, year: str, doc_type: str, company_code: str,
                       status: str = "success", result: Dict = None):
        """
        企業の処理完了を記録（即座にファイル保存）
        """
        batch_key = self.get_batch_key(year, doc_type)

        if batch_key not in self.progress:
            self.progress[batch_key] = {
                "started_at": datetime.now().isoformat(),
                "completed": [],
                "skipped": [],
                "errors": [],
                "status": "running"
            }

        batch = self.progress[batch_key]

        if status == "success":
            if company_code not in batch["completed"]:
                batch["completed"].append(company_code)
        elif status == "skipped":
            if company_code not in batch["skipped"]:
                batch["skipped"].append(company_code)
        else:  # error
            if company_code not in batch["errors"]:
                batch["errors"].append(company_code)

        batch["last_processed"] = company_code
        batch["last_processed_at"] = datetime.now().isoformat()

        # ★ 即座にファイル保存（PCシャットダウン対策）
        self._save_progress()

    def finish_batch(self, year: str, doc_type: str):
        """バッチ処理完了を記録"""
        batch_key = self.get_batch_key(year, doc_type)

        if batch_key in self.progress:
            self.progress[batch_key]["status"] = "completed"
            self.progress[batch_key]["completed_at"] = datetime.now().isoformat()
            self._save_progress()

    def get_batch_status(self, year: str = None, doc_type: str = None) -> Dict:
        """バッチ処理の状態を取得"""
        if year and doc_type:
            batch_key = self.get_batch_key(year, doc_type)
            return self.progress.get(batch_key, {})
        return self.progress

    def has_incomplete_batch(self) -> Optional[Tuple[str, str, Dict]]:
        """未完了のバッチがあるか確認"""
        for batch_key, batch in self.progress.items():
            if batch.get("status") == "running":
                parts = batch_key.split("_", 1)
                if len(parts) == 2:
                    return parts[0], parts[1], batch
        return None

    def clear_batch(self, year: str, doc_type: str):
        """特定のバッチ進捗をクリア"""
        batch_key = self.get_batch_key(year, doc_type)
        if batch_key in self.progress:
            del self.progress[batch_key]
            self._save_progress()

    def clear_all(self):
        """全ての進捗をクリア"""
        self.progress = {}
        if self.progress_file.exists():
            self.progress_file.unlink()


# グローバル進捗トラッカー
progress_tracker = BatchProgressTracker()


# ============================================================
# ★ v9.5.11: BOILERPLATE_PATTERNS（定型文除外）
# ============================================================
BOILERPLATE_PATTERNS = [
    # 沿革・歴史
    r'昭和\d+年',
    r'平成\d+年',
    r'大正\d+年',
    r'明治\d+年',
    r'\d{4}年\d+月.*設立',
    r'創業以来',
    r'前身である',
    
    # 注記・脚注
    r'注\d+[）\)]',
    r'^\s*\(\d+\)\s*$',
    r'※\d+',
    r'\*\d+',
    
    # 財務諸表の定型文
    r'金額は.*単位未満.*切り捨て',
    r'記載金額は.*四捨五入',
    r'該当事項.*ありません',
    r'連結子会社.*含めた',
    r'重要な会計方針',
    r'会計基準.*に基づき',
    
    # ガバナンス定型
    r'取締役会.*決議',
    r'監査役会.*設置',
    r'株主総会.*開催',
    r'定款.*定め',
    
    # リスク定型文（MDA誤マッチ防止）
    r'影響を及ぼす可能性があります',
    r'悪影響を与える可能性',
    r'リスクが存在します',
]

BOILERPLATE_REGEX = re.compile('|'.join(BOILERPLATE_PATTERNS), re.IGNORECASE)


def is_boilerplate(text: str) -> bool:
    """定型文かどうかを判定"""
    if not text:
        return True
    return bool(BOILERPLATE_REGEX.search(text))


# ============================================================
# ★ v9.5.11: 重要キーワードリスト
# ============================================================
IMPORTANT_KEYWORDS = [
    "増加", "減少", "増減", "要因", "影響", "見通し", "予想", "リスク", "対応",
    "為替", "価格", "単価", "数量", "ミックス", "在庫", "需要", "供給", "原価",
    "コスト", "費用", "原材料", "人件費", "物流", "エネルギー",
    "増収", "減収", "増益", "減益", "好調", "不調", "伸長", "堅調", "低迷",
    "売上", "利益", "損失", "改善", "悪化",
    "%", "億円", "百万円", "前年比", "前期比", "増", "減",
    "セグメント", "部門", "事業",
    "M&A", "買収", "売却", "統合", "新製品", "新規", "撤退", "閉鎖",
    "設備投資", "減損", "訴訟", "災害", "リストラ",
]


# ============================================================
# ★ v9.5.11: イベントスコアリング
# ============================================================
def score_event_text(text: str, current_year: int = None) -> float:
    """イベントテキストのスコアリング（高いほど重要）"""
    score = 1.0
    
    # ペナルティ: 歴史的な記述
    if re.search(r'(昭和|平成|大正|明治)\d+年', text):
        score -= 0.5
    if re.search(r'\d{4}年.*設立', text):
        score -= 0.5
    if '創業' in text or '前身' in text:
        score -= 0.3
    
    # ペナルティ: 定型文
    if is_boilerplate(text):
        score -= 0.4
    
    # ボーナス: 具体的なイベント
    event_keywords = ['M&A', '買収', '売却', '統合', '新製品', '新規事業', 
                      '撤退', '閉鎖', '設備投資', '減損', '訴訟', '災害',
                      'リストラ', '再編', '経営陣', '交代']
    if any(kw in text for kw in event_keywords):
        score += 0.3
    
    # ボーナス: 当期の記述
    if current_year:
        if f'{current_year}年' in text or '当期' in text or '今期' in text:
            score += 0.2
    
    # ボーナス: 具体的な金額
    if re.search(r'\d+\.?\d*[億万]円', text):
        score += 0.2
    
    return max(0, min(1.5, score))


# ============================================================
# セクション優先度
# ============================================================
SECTION_PRIORITY = {
    "03_MDA": 1,
    "02_経営戦略_リスク": 2,
    "05_セグメント": 3,
    "01_会社概要": 4,
    "07_その他": 5,
    "06_ガバナンス": 6,
    "04_財務三表": 7,
}

GOVERNANCE_NOISE_WORDS = [
    "取締役", "監査役", "報酬", "株主総会", "定款", "補欠", "選任",
    "内部監査", "コンプライアンス", "内部統制", "監査等委員",
    "自己株式", "買付", "ストックオプション", "新株予約権",
]


# ============================================================
# 業種別プロンプトテンプレート
# ============================================================
INDUSTRY_PROMPTS = {
    "food": {
        "name": "食品・水産",
        "focus_points": [
            "原料価格（魚価・穀物・油脂）の影響",
            "為替・燃料・物流コストの変動",
            "値上げ転嫁の進捗（量販向け/外食向け）",
            "在庫評価・廃棄ロス",
            "養殖事業（設備投資・歩留まり・疾病リスク）",
        ],
        "key_metrics": ["粗利率", "原価率", "在庫回転", "物流費率"],
        "key_questions": [
            "魚価・原料価格の変動は利益にどう影響したか？",
            "値上げはどの程度転嫁できているか？",
            "在庫の増減理由は何か？",
            "養殖事業の採算性は改善しているか？",
        ],
    },
    "manufacturing": {
        "name": "製造業",
        "focus_points": [
            "設備稼働率と固定費負担",
            "原材料・部品調達コスト",
            "為替影響（輸出比率）",
            "研究開発投資の効率性",
            "減価償却負担と設備投資計画",
        ],
        "key_metrics": ["営業利益率", "設備投資/減価償却比率", "研究開発費率"],
        "key_questions": [
            "稼働率の変化は利益にどう影響したか？",
            "為替の影響は売上・利益にどの程度か？",
            "設備投資の回収見通しは？",
        ],
    },
    "retail": {
        "name": "小売・サービス",
        "focus_points": [
            "既存店売上高の推移",
            "客数・客単価の分解",
            "人件費率・賃上げ影響",
            "出店/退店計画",
            "EC比率と物流コスト",
        ],
        "key_metrics": ["既存店成長率", "人件費率", "販管費率"],
        "key_questions": [
            "既存店の客数・客単価はどう推移したか？",
            "人件費上昇の影響は？",
            "EC事業の採算性は？",
        ],
    },
    "it": {
        "name": "IT・通信",
        "focus_points": [
            "ARR/MRR成長率",
            "顧客獲得コスト（CAC）と回収期間",
            "解約率（チャーンレート）",
            "エンジニア採用・人件費",
            "クラウドインフラコスト",
        ],
        "key_metrics": ["売上成長率", "粗利率", "営業利益率"],
        "key_questions": [
            "顧客獲得コストは回収できているか？",
            "解約率の推移は？",
            "人件費の増加ペースは売上成長に見合っているか？",
        ],
    },
    "finance": {
        "name": "金融",
        "focus_points": [
            "金利環境と利鞘",
            "与信コスト・不良債権比率",
            "自己資本比率・規制対応",
            "手数料収入の多様化",
            "システム投資負担",
        ],
        "key_metrics": ["ROE", "自己資本比率", "経費率"],
        "key_questions": [
            "金利環境の変化は利鞘にどう影響したか？",
            "与信コストの増減理由は？",
            "手数料収入は成長しているか？",
        ],
    },
    "all": {
        "name": "一般",
        "focus_points": [
            "売上成長の持続性",
            "利益率の変動要因",
            "キャッシュフロー創出力",
            "財務健全性",
            "競争優位性の源泉",
        ],
        "key_metrics": ["営業利益率", "ROE", "自己資本比率"],
        "key_questions": [
            "売上の増減要因は何か？",
            "利益率が変動した主因は？",
            "キャッシュフローの質は？",
        ],
    },
}

INVESTOR_QUESTIONS = """
【投資家が知りたい核心的な問い】
1. 何が利益率を押し下げている？（価格/数量/ミックス/原価/固定費）
2. 会社はどの変数をコントロールできる？できない？
3. 改善のKPIは何か？
4. "これが起きたら投資失敗"の条件は？
5. 逆に"勝ち筋"は何で、いつ数字に出る？
6. 経営陣は課題を正しく認識しているか？
"""


# ============================================================
# ★ v9.6.1: セクション別質問セット（完全版）
# ============================================================
SECTION_QUESTIONS = {
    "01_会社概要": {
        "name": "会社概要",
        "questions": [
            {
                "id": "employee_change",
                "question": "従業員数の増減はありますか？増減の理由（採用強化、リストラ、事業拡大など）も含めて説明してください。",
                "focus": "従業員"
            },
        ]
    },
    "02_経営戦略_リスク": {
        "name": "経営戦略・リスク",
        "questions": [
            {
                "id": "risks",
                "question": "会社が認識している主要なリスクは何ですか？各リスクに対する対応策も含めて説明してください。",
                "focus": "リスク"
            },
            {
                "id": "mid_term_plan",
                "question": "中期経営計画や経営方針の要点は何ですか？数値目標（売上、利益、ROEなど）があれば含めてください。",
                "focus": "中計"
            },
            {
                "id": "competitive_advantage",
                "question": "会社の競争優位性・強みは何ですか？市場シェアや競合との違いについても説明してください。",
                "focus": "競争力"
            },
            # === GS MD Level: 投資判断に必要な追加質問 ===
            {
                "id": "management_track_record",
                "question": """経営陣の実績・実行力について説明してください。

【抽出すべき情報】
1. 過去の中計目標の達成率（達成/未達の実績）
2. 主要な経営判断とその結果（M&A、事業再編、新規参入など）
3. 資本効率改善への取り組み結果（ROE改善実績など）
4. 株主還元方針の実行状況
5. 経営陣の交代・刷新があればその背景

【回答フォーマット】
- 中計達成実績: 前中計（2019-2021）では売上目標90%、利益目標105%達成 (P.xx)
- 主要な経営判断: 2020年にA社買収、シナジー効果+50億円実現 (P.xx)""",
                "focus": "経営実績"
            },
            {
                "id": "market_position",
                "question": """業界内での競争ポジションについて説明してください。

【抽出すべき情報】
1. 市場シェア（具体的な%）と順位
2. 競合他社との差別化ポイント
3. 参入障壁・スイッチングコスト
4. 業界全体の成長率・トレンド
5. 新規参入者・代替品の脅威

【回答フォーマット】
- 市場シェア: 国内XX市場でシェア25%、第2位 (P.xx)
- 競合: A社（30%）、C社（15%）と比較して○○で差別化 (P.xx)""",
                "focus": "競争環境"
            },
            {
                "id": "capital_allocation",
                "question": """資本配分の優先順位について説明してください。

【抽出すべき情報】
1. 成長投資（M&A、設備投資、R&D）の優先度と金額
2. 株主還元（配当、自社株買い）の方針と目標
3. 財務健全性（負債削減、自己資本充実）の方針
4. 今後3-5年の投資計画の概要

【回答フォーマット】
- 成長投資: 今後3年で累計1,000億円のM&A・設備投資を計画 (P.xx)
- 株主還元: 配当性向40%目標、DOE3%以上を維持 (P.xx)""",
                "focus": "資本配分"
            },
        ]
    },
    "03_MDA": {
        "name": "経営成績分析（MDA）",
        "questions": [
            # === 売上・利益分析 ===
            {
                "id": "revenue_drivers",
                "question": """売上高の増減要因は何ですか？セグメント別・製品別・地域別に具体的な理由と金額・割合を説明してください。

【重要な回答ルール】
1. 抽象的な表現（「新製品の販売拡大」「需要増加」など）のみでは不十分
2. 必ず以下のいずれかを含めること:
   - 具体的な製品名・サービス名（例: 「○○シリーズの販売好調」）
   - 具体的な金額（例: 「××億円の増収」）
   - 具体的な数量・台数（例: 「販売台数○○万台増」）
   - 具体的な割合（例: 「売上の○○%を占める」）
3. 「プラス 不明」「マイナス 不明」といった回答は避け、テキスト中の数値を必ず抽出すること
4. セグメント別・製品別・地域別の内訳があれば必ず記載すること

【回答フォーマット例】
- 水産商事セグメント: 冷凍エビの販売増（+120億円、全体の65%）により増収 (P.12)
- 食品セグメント: 新製品「○○シリーズ」の販売好調（+50億円）、既存品の価格改定効果（+30億円）(P.13)""",
                "focus": "売上"
            },
            {
                "id": "profit_drivers",
                "question": """営業利益・経常利益の増減要因は何ですか？原価、販管費、為替、一過性要因など、具体的な理由と金額を説明してください。

【重要な回答ルール】
1. 抽象的な表現（「コスト削減」「効率化」など）のみでは不十分
2. 必ず以下のいずれかを含めること:
   - 具体的な金額（例: 「原材料費+○○億円の増加」）
   - 具体的な割合（例: 「販管費率○○%改善」）
   - 具体的な要因（例: 「燃料費高騰により+○○億円」）
3. 「プラス 不明」「マイナス 不明」といった回答は避け、テキスト中の数値を必ず抽出すること
4. 原価・販管費・為替・一過性要因など、要因別の内訳があれば必ず記載すること

【回答フォーマット例】
- 原材料費: 魚粉価格高騰により原価+80億円増加 (P.14)
- 販管費: 物流費削減により-20億円改善 (P.14)
- 為替影響: 円安により営業外収益+15億円 (P.15)""",
                "focus": "利益"
            },
            # === コスト・価格 ===
            {
                "id": "cost_structure",
                "question": "原価・コスト構造はどう変化しましたか？原材料費、人件費、物流費、エネルギーコストなど、主要コストの増減理由と金額を説明してください。",
                "focus": "コスト"
            },
            {
                "id": "price_pass_through",
                "question": "値上げ・価格転嫁の状況はどうですか？値上げの実施状況、顧客への転嫁率、販売数量への影響を説明してください。",
                "focus": "価格"
            },
            {
                "id": "forex_impact",
                "question": "為替の影響はどの程度ですか？為替レートの変動、売上・利益への影響額、海外売上比率を説明してください。",
                "focus": "為替"
            },
            {
                "id": "inventory_change",
                "question": "在庫の増減理由は何ですか？在庫金額の変化、適正在庫との比較、在庫評価損などを説明してください。",
                "focus": "在庫"
            },
            # === 商材・製品情報 ===
            {
                "id": "product_portfolio",
                "question": """主力製品・商材・サービスの販売状況を教えてください。

【必ず抽出すべき情報】
1. 製品別・商材別の売上高または販売数量（例: 「○○シリーズ: 売上高1,200億円」「販売台数: 150万台」）
2. 製品別の前年比増減（例: 「前年比+15%」「前年比-20万台」）
3. 売上構成比（例: 「全体の35%を占める」）
4. 新製品・主力商品の名称と販売動向
5. 値上げ・価格改定の状況と影響

【業種別の抽出例】
- 自動車: 車種別販売台数、EV/HEV比率、地域別販売数
- 食品・飲料: ブランド別売上、新商品の売上寄与、カテゴリ別シェア
- 小売: 店舗数、既存店売上高、EC売上比率
- 化学: 製品群別売上、半導体材料比率、用途別内訳

【回答フォーマット例】
- ビール類: 売上高2,500億円（前年比-3%）、「スーパードライ」が主力 [P.12]
- 飲料: 売上高800億円（前年比+5%）、「三ツ矢サイダー」好調 [P.13]
- 海外: 売上高1,200億円（前年比+15%）、アジア向け輸出増 [P.14]""",
                "focus": "製品・商材"
            },
            # === セグメント ===
            {
                "id": "segment_performance",
                "question": """各セグメント（事業部門）の業績と主力商材を教えてください。

【必ず回答に含めること】
1. セグメント別の売上高・営業利益と前年比
2. 各セグメントの主力製品・サービス名
3. 好調・不調の理由（具体的な製品名や金額を含む）
4. 注力分野・成長分野の説明

【回答フォーマット例】
- 国内酒類事業: 売上高2,736億円（-3%）、主力はビール類、新ジャンル減少が主因 (P.12)
- 国際事業: 売上高705億円（+42%）、北米「サッポロプレミアム」好調、M&A効果 (P.13)""",
                "focus": "セグメント"
            },
            # === key_events（v9.6.1修正版）===
            {
                "id": "key_events",
                "question": """今期に発生した重要なイベントを列挙してください。

【探すべきイベントの例】
- M&A（買収・売却・統合）
- 新製品・新サービスの投入
- 新規事業への参入・撤退
- 工場・設備の新設・閉鎖・移転
- 大型の設備投資・減損
- 訴訟・規制対応
- 災害・事故・リコール
- 重要な契約の締結・解除
- 経営陣の交代
- 組織再編・人員削減

【禁止事項】
- 「売上が○%増加」「利益が○億円」などの業績数値は書かない
- 「好調に推移」「堅調」などの定性的業績評価は書かない
- 単なる数字のサマリーは書かない

【回答形式】
各イベントについて:
- イベント名（具体的に）
- 発生時期
- 業績への影響（記載があれば）

イベントの記載がない場合は「重要なイベントの記載なし」と答えてください。""",
                "focus": "イベント"
            },
            # === 投資・財務 ===
            {
                "id": "capex",
                "question": """設備投資の状況を教えてください。

【必ず抽出すべき情報】
1. 設備投資総額（例: 「設備投資1,500億円」「有形固定資産取得800億円」）
2. 前年比増減（例: 「前年比+20%」「前年比-100億円」）
3. 主要な投資内容:
   - 工場・生産設備（新設、増強、更新）
   - 研究開発施設
   - IT・デジタル投資
   - 物流・倉庫
   - 店舗・販売網
4. セグメント別・地域別の投資配分
5. 来期以降の投資計画

【回答フォーマット例】
- 設備投資総額: 1,200億円（前年比+15%） [P.25]
- 内訳: 半導体製造装置500億円、研究開発施設200億円、物流拠点100億円 [P.26]
- 来期計画: 1,500億円（EV関連に重点投資） [P.27]""",
                "focus": "設備投資"
            },
            {
                "id": "depreciation",
                "question": "減価償却費はいくらですか？前年比の増減と主な要因を説明してください。",
                "focus": "減価償却"
            },
            {
                "id": "rd_expense",
                "question": """研究開発費の状況を教えてください。

【必ず抽出すべき情報】
1. 研究開発費総額（例: 「研究開発費1,100億円」「R&D費用800億円」）
2. 対売上高比率（例: 「売上高比率5.2%」）
3. 前年比増減（例: 「前年比+8%」「前年比+50億円」）
4. 主要な研究開発テーマ:
   - 新製品・新技術開発
   - 次世代技術（AI、EV、半導体、バイオなど）
   - 環境・省エネ技術
5. セグメント別の研究開発費配分
6. 研究開発の成果（特許取得、新製品発売など）

【業種別の抽出例】
- 自動車: EV・自動運転・電池技術への投資額
- 電機: 半導体・AI・ロボティクスへの投資
- 製薬: パイプライン、臨床試験段階、上市予定
- 化学: 機能性材料、環境対応製品の開発

【回答フォーマット例】
- 研究開発費: 1,100億円（売上高比5.5%、前年比+10%） [P.30]
- 主要テーマ: 次世代半導体500億円、AI技術200億円、環境技術150億円 [P.31]
- 成果: 新製品「○○」発売、特許取得○件 [P.32]""",
                "focus": "研究開発"
            },
            {
                "id": "dividend_shareholder",
                "question": "配当・株主還元の方針はどうですか？配当金額、配当性向、自社株買いの状況を説明してください。",
                "focus": "株主還元"
            },
            {
                "id": "financial_position",
                "question": "財務体質はどうですか？自己資本比率、有利子負債、ネットキャッシュの状況を説明してください。",
                "focus": "財務体質"
            },
            # === 見通し ===
            {
                "id": "guidance_outlook",
                "question": "経営者の今後の見通し・業績予想はどうですか？来期の売上・利益予想、成長戦略、懸念事項を説明してください。",
                "focus": "見通し"
            },
            # === 感応度分析（GSレベル）===
            {
                "id": "forex_sensitivity",
                "question": "為替の感応度はどうですか？為替が1円変動した場合の売上高・営業利益への影響額、為替ヘッジの状況を説明してください。",
                "focus": "為替感応度"
            },
            {
                "id": "raw_material_sensitivity",
                "question": "原材料価格の感応度はどうですか？主要原材料（魚介類、原油、穀物など）の価格変動が業績にどう影響するか、価格転嫁の状況を含めて説明してください。",
                "focus": "原材料感応度"
            },
        ]
    },
    "05_セグメント": {
        "name": "セグメント情報",
        "questions": [
            {
                "id": "segment_revenue",
                "question": """各セグメントの売上高（外部顧客への売上）はいくらですか？前年比の増減率も必ず含めて説明してください。

【🚨 絶対禁止事項 - 以下は重大な品質不合格となる 🚨】
❌ 全社合計売上高をそのままセグメント売上高として使用すること → 全セグメントが同じ金額になるのは明らかな誤り
❌ 全社YoY%をそのままセグメントYoY%として使用すること → 各セグメントは異なる成長率を持つ
❌ セグメント固有の数値が見つからない場合に推測や穴埋めをすること → 「N/A」と記載せよ

【重要な抽出ルール】
1. セグメントごとに固有の売上高金額を探して抽出すること（各セグメントは異なる金額のはず）
2. セグメントごとに「前年比+XX%」「前期比+XX%」「昨年比+XX%」などの記載を探して、そのまま抽出すること
3. テキスト中に明記されている前年比率をそのまま使用すること（計算で求めないこと）
4. 各セグメントの売上高・前年比率は通常異なる値になる（全セグメントが同じになる場合は誤り）
5. 前年比が明記されていない場合のみ「前年比: 不明」と記載すること
6. セグメント固有の数値が見つからない場合は「N/A」と記載すること（全社数値で埋めない）

【回答フォーマット例】
- 水産商事セグメント: 売上高 120,796百万円（前年比+2.3%）[P.25]
- 食品セグメント: 売上高 96,883百万円（前年比+5.1%）[P.25]
- 鰹・鮪セグメント: 売上高 34,295百万円（前年比-1.2%）[P.25]

【誤りの例 - これは不合格】
❌ 国内酒類事業: 売上高 4,784億円 ← 全社合計と同じ
❌ 国際事業: 売上高 4,784億円 ← 全社合計と同じ（明らかに誤り）""",
                "focus": "セグメント売上"
            },
            {
                "id": "segment_profit",
                "question": """各セグメントの営業利益（またはセグメント利益）はいくらですか？前年比の増減率も必ず含めて説明してください。

【🚨 絶対禁止事項 - 以下は重大な品質不合格となる 🚨】
❌ 全社合計営業利益をそのままセグメント利益として使用すること → 全セグメントが同じ金額になるのは明らかな誤り
❌ 全社YoY%をそのままセグメントYoY%として使用すること → 各セグメントは異なる成長率を持つ
❌ セグメント固有の数値が見つからない場合に推測や穴埋めをすること → 「N/A」と記載せよ

【重要な抽出ルール】
1. セグメントごとに固有の営業利益金額を探して抽出すること（各セグメントは異なる金額のはず）
2. セグメントごとに「前年比+XX%」「前期比+XX%」「昨年比+XX%」などの記載を探して、そのまま抽出すること
3. テキスト中に明記されている前年比率をそのまま使用すること（計算で求めないこと）
4. 各セグメントの利益・前年比率は通常異なる値になる（全セグメントが同じになる場合は誤り）
5. 前年比が明記されていない場合のみ「前年比: 不明」と記載すること
6. セグメント固有の数値が見つからない場合は「N/A」と記載すること（全社数値で埋めない）

【回答フォーマット例】
- 水産商事セグメント: 営業利益 5,150百万円（前年比+67.9%）[P.26]
- 食品セグメント: 営業利益 1,046百万円（前年比+12.3%）[P.26]
- 鰹・鮪セグメント: 営業利益 △500百万円（前年比-45.6%）[P.26]

【誤りの例 - これは不合格】
❌ 国内酒類事業: 営業利益 101億円 ← 全社合計と同じ
❌ 国際事業: 営業利益 101億円 ← 全社合計と同じ（明らかに誤り）""",
                "focus": "セグメント利益"
            },
            {
                "id": "segment_margin",
                "question": "各セグメントの営業利益率（売上高営業利益率）は何%ですか？前年比の増減も含めて説明してください。",
                "focus": "セグメント利益率"
            },
            {
                "id": "geographic_breakdown",
                "question": "地域別の売上高・利益はどうなっていますか？日本、アジア、北米、欧州など地域別の業績を説明してください。",
                "focus": "地域別"
            },
        ]
    },
}


# ============================================================
# フォーマッタ
# ============================================================
def fmt_yen(value, unit: str = "円") -> str:
    if value is None:
        return "N/A"
    try:
        v = float(value)
        oku = v / 1e8
        if abs(oku) >= 100:
            return f"{oku:,.1f}億{unit}"
        elif abs(v) >= 1e6:
            return f"{v/1e6:,.0f}百万{unit}"
        else:
            return f"{v:,.0f}{unit}"
    except:
        return "N/A"


def fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except:
        return "N/A"


def fmt_change(current, previous) -> str:
    if current is None or previous is None or previous == 0:
        return "N/A"
    try:
        change = ((float(current) - float(previous)) / abs(float(previous))) * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.1f}%"
    except:
        return "N/A"


def normalize_page(p: Any) -> str:
    if isinstance(p, list):
        p = p[0] if p else ""
    s = str(p or "").strip()
    if not s or s.lower() in ("none", "null", "nan", ""):
        return "P.?"
    s = s.replace("p.", "P.").replace("p", "P").strip()
    if s in ("?", "P.?", "P.? ", "不明", "N/A"):
        return "P.?"
    s = re.sub(r"P\.P\.", "P.", s)
    if s.startswith("P."):
        m = re.search(r"P\.(\d+)", s)
        return f"P.{m.group(1)}" if m else "P.?"
    if s.isdigit():
        return f"P.{s}"
    m = re.search(r"(\d+)", s)
    return f"P.{m.group(1)}" if m else "P.?"


def normalize_impact(impact: Any) -> str:
    s = str(impact or "").strip().lower()
    if s in ("+", "プラス", "plus", "positive", "増加", "改善", "好影響"):
        return "+"
    if s in ("-", "マイナス", "minus", "negative", "減少", "悪化", "悪影響"):
        return "-"
    return "?"


def impact_to_display(impact: str) -> str:
    if impact == "+":
        return "プラス"
    if impact == "-":
        return "マイナス"
    return "不明"


# ============================================================
# XBRLタグ定義
# ============================================================
@dataclass
class XBRLTagDef:
    field_type: str
    field_name: str
    tag_name: str
    priority: int
    industry: str = "all"
    excluded_keywords: List[str] = field(default_factory=list)
    required_keywords: List[str] = field(default_factory=list)
    is_active: bool = True

    @property
    def is_instant(self) -> bool:
        return self.field_type in ('bs', 'stock', 'employee', 'corporate')


class XBRLTagManager:
    def __init__(self):
        self.tags: Dict[str, List[XBRLTagDef]] = defaultdict(list)
        self.loaded_from = None

    def load_from_sheets(self) -> bool:
        if not HAS_GSPREAD:
            return False

        sa_json_path = os.environ.get("GOOGLE_SA_JSON", "keys/google_sa.json")
        if not Path(sa_json_path).is_absolute():
            sa_json_path = Config.PROJECT_DIR / sa_json_path
        else:
            sa_json_path = Path(sa_json_path)

        if not sa_json_path.exists():
            return False

        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly"
            ]
            creds = Credentials.from_service_account_file(str(sa_json_path), scopes=scopes)
            gc = gspread.authorize(creds)

            logger.info(f"Google Sheets接続: {Config.TAXONOMY_SPREADSHEET}")
            spreadsheet = gc.open(Config.TAXONOMY_SPREADSHEET)
            worksheet = spreadsheet.worksheet(Config.TAXONOMY_TAB)

            all_values = worksheet.get_all_values()
            if not all_values:
                return False

            headers = [h.lower().strip() for h in all_values[0]]

            try:
                idx = {
                    'field_type': headers.index('field_type'),
                    'field_name': headers.index('field_name'),
                    'tag_name': headers.index('tag_name'),
                    'priority': headers.index('priority'),
                    'industry': headers.index('industry'),
                    'excluded_keywords': headers.index('excluded_keywords'),
                    'required_keywords': headers.index('required_keywords'),
                    'is_active': headers.index('is_active'),
                }
            except ValueError as e:
                logger.error(f"カラムなし: {e}")
                return False

            self.tags.clear()
            count = 0

            for row in all_values[1:]:
                if len(row) <= max(idx.values()):
                    continue

                if row[idx['is_active']].strip().upper() != 'TRUE':
                    continue

                field_name = row[idx['field_name']].strip()
                tag_name = row[idx['tag_name']].strip()
                if not field_name or not tag_name:
                    continue

                try:
                    priority = int(row[idx['priority']])
                except:
                    priority = 99

                excluded_str = row[idx['excluded_keywords']].strip()
                excluded = [k.strip().lower() for k in excluded_str.split(',') if k.strip()] if excluded_str else []
                required_str = row[idx['required_keywords']].strip()
                required = [k.strip().lower() for k in required_str.split(',') if k.strip()] if required_str else []

                self.tags[field_name].append(XBRLTagDef(
                    field_type=row[idx['field_type']].strip(),
                    field_name=field_name,
                    tag_name=tag_name,
                    priority=priority,
                    industry=row[idx['industry']].strip() or 'all',
                    excluded_keywords=excluded,
                    required_keywords=required,
                    is_active=True
                ))
                count += 1

            for field_name in self.tags:
                self.tags[field_name].sort(key=lambda t: t.priority)

            logger.info(f"XBRLタグ: {count}件")
            self.loaded_from = 'google_sheets'
            self._save_cache()
            return True

        except Exception as e:
            logger.error(f"Sheets読み込みエラー: {e}")
            return False

    def load_from_cache(self) -> bool:
        if not Config.TAG_CACHE_FILE.exists():
            return False
        try:
            with open(Config.TAG_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.tags.clear()
            for field_name, tag_list in data.get('tags', {}).items():
                for t in tag_list:
                    self.tags[field_name].append(XBRLTagDef(
                        field_type=t['field_type'],
                        field_name=t['field_name'],
                        tag_name=t['tag_name'],
                        priority=t['priority'],
                        industry=t.get('industry', 'all'),
                        excluded_keywords=t.get('excluded_keywords', []),
                        required_keywords=t.get('required_keywords', []),
                        is_active=True
                    ))
            self.loaded_from = 'cache'
            return True
        except:
            return False

    def _save_cache(self):
        try:
            data = {
                'tags': {
                    fn: [{
                        'field_type': t.field_type, 'field_name': t.field_name,
                        'tag_name': t.tag_name, 'priority': t.priority,
                        'industry': t.industry, 'excluded_keywords': t.excluded_keywords,
                        'required_keywords': t.required_keywords
                    } for t in tl]
                    for fn, tl in self.tags.items()
                },
                'saved_at': datetime.now().isoformat()
            }
            with open(Config.TAG_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except:
            pass

    def load_fallback(self):
        FALLBACK = {
            # P/L項目（v10.1.1: Summary版タグを最優先に追加）
            'revenue': [
                # IFRS Summary版（経営指標等の推移 - 最優先）
                ('RevenueIFRSSummaryOfBusinessResults', 1),
                # 日本基準 Summary版
                ('NetSalesSummaryOfBusinessResults', 2),
                ('OperatingRevenueSummaryOfBusinessResults', 2),
                # IFRS 通常版
                ('RevenueIFRS', 3),
                ('RevenuesIFRS', 3),
                ('Revenue', 3),
                ('RevenueFromOperationsIFRS', 3),
                # 日本基準 通常版
                ('NetSales', 4),
                ('SalesJPCRPCOR', 4),
                # 米国基準
                ('SalesRevenueNet', 5),
                ('Revenues', 5),
                ('SalesRevenueGoodsNet', 5),
                ('SalesRevenueServicesNet', 5),
                # その他
                ('OperatingRevenues', 6),
                ('OperatingRevenue1', 6),
                ('OperatingRevenue', 6),
                ('NetSalesAndOperatingRevenue', 6),
                ('Sales', 7),
            ],
            # 営業利益タグを拡張
            'operating_income': [
                # IFRS（最優先）
                ('OperatingProfitLossIFRS', 1),
                ('ProfitLossFromOperatingActivitiesIFRS', 1),
                ('OperatingIncomeIFRS', 1),
                ('OperatingIncomeLossIFRS', 1),
                # 日本基準 Summary版
                ('OperatingIncomeLossSummaryOfBusinessResults', 2),
                ('OperatingIncomeSummaryOfBusinessResults', 2),
                # 日本基準 通常版
                ('OperatingIncome', 3),
                ('OperatingIncomeLoss', 3),
                # その他
                ('OperatingProfitLoss', 4),
                ('IncomeFromOperations', 4),
                ('OperatingProfit', 4),
            ],
            'ordinary_income': [('OrdinaryIncome', 1), ('OrdinaryIncomeLoss', 2)],
            'net_income': [('ProfitLossAttributableToOwnersOfParent', 0), ('ProfitLoss', 2), ('NetIncome', 3)],
            'cost_of_sales': [('CostOfSales', 1)],
            'gross_profit': [('GrossProfit', 1)],
            'sga_expense': [('SellingGeneralAndAdministrativeExpenses', 1)],
            
            # B/S項目
            'total_assets': [('TotalAssets', 1), ('Assets', 2)],
            'total_equity': [
                ('EquityAttributableToOwnersOfParent', 0),
                ('StockholdersEquity', 1),
                ('ShareholdersEquity', 2),
                ('NetAssets', 5),
            ],
            'net_assets': [('NetAssets', 1)],
            'total_liabilities': [('TotalLiabilities', 1), ('Liabilities', 2)],
            'cash_and_deposits': [('CashAndDeposits', 1), ('CashAndCashEquivalents', 2)],
            'short_term_loans': [('ShortTermLoansPayable', 1), ('ShortTermBorrowings', 2)],
            'long_term_loans': [('LongTermLoansPayable', 1), ('LongTermDebt', 2)],
            'bonds_payable': [('BondsPayable', 1)],
            
            # C/F項目
            'operating_cf': [('NetCashProvidedByUsedInOperatingActivities', 1), ('CashFlowsFromUsedInOperatingActivities', 2)],
            'investing_cf': [('NetCashProvidedByUsedInInvestingActivities', 1), ('CashFlowsFromUsedInInvestingActivities', 2)],
            'financing_cf': [('NetCashProvidedByUsedInFinancingActivities', 1), ('CashFlowsFromUsedInFinancingActivities', 2)],
            'depreciation': [('DepreciationAndAmortization', 1), ('Depreciation', 2)],
            'capex': [('PurchaseOfPropertyPlantAndEquipment', 1)],
            
            # 配当関連
            'dividend_per_share': [('DividendPerShare', 1), ('CashDividendsPerShare', 2)],
            'total_dividend': [('DividendsPaidToOwnersOfParent', 1), ('DividendsPaid', 2)],
            'dividends_paid': [('PaymentsOfDividends', 1)],
            
            # 株式数
            'shares_outstanding': [('NumberOfSharesIssued', 1), ('TotalNumberOfIssuedShares', 2)],
            
            # R&D
            'rd_expense': [('ResearchAndDevelopmentExpenses', 1)],
            
            # 従業員
            'employee_count': [('NumberOfEmployees', 1)],
        }
        BS_FIELDS = {'total_assets', 'total_equity', 'net_assets', 'total_liabilities', 
                     'cash_and_deposits', 'short_term_loans', 'long_term_loans', 'bonds_payable',
                     'employee_count', 'shares_outstanding'}
        CF_FIELDS = {'operating_cf', 'investing_cf', 'financing_cf', 'depreciation', 
                     'capex', 'dividends_paid'}
        
        self.tags.clear()
        for fn, tl in FALLBACK.items():
            if fn in BS_FIELDS:
                ft = 'bs'
            elif fn in CF_FIELDS:
                ft = 'cf'
            else:
                ft = 'pl'
            for tn, pr in tl:
                self.tags[fn].append(XBRLTagDef(ft, fn, tn, pr, 'all', [], [], True))
        self.loaded_from = 'fallback'

    def load(self, force_reload: bool = False) -> bool:
        if self.tags and not force_reload:
            return True
        if self.load_from_sheets():
            return True
        if not force_reload and self.load_from_cache():
            return True
        self.load_fallback()
        return True

    def get_tags_for_field(self, field_name: str, industry: str = 'all') -> List[XBRLTagDef]:
        tags = self.tags.get(field_name, [])
        if not tags:
            return []
        if industry == 'all':
            result = [t for t in tags if t.industry == 'all']
        else:
            result = [t for t in tags if t.industry == industry] + [t for t in tags if t.industry == 'all']
        result.sort(key=lambda t: t.priority)
        return result

    def get_all_field_names(self) -> List[str]:
        return list(self.tags.keys())


tag_manager = XBRLTagManager()


# ============================================================
# セクション定義
# ============================================================
SECTION_MAPPING = {
    "01_会社概要": {
        "name": "企業概況",
        "extract_focus": "会社概要、主要指標の推移、沿革、従業員数",
    },
    "02_経営戦略_リスク": {
        "name": "経営戦略・リスク",
        "extract_focus": "中期経営計画、経営方針、サステナビリティ、リスク要因",
    },
    "03_MDA": {
        "name": "経営成績分析",
        "extract_focus": "業績の増減要因、セグメント別動向、今後の見通し",
    },
    "04_財務三表": {
        "name": "財務諸表",
        "extract_focus": "B/S・P/L・CFの主要項目と増減",
    },
    "05_セグメント": {
        "name": "セグメント情報",
        "extract_focus": "事業別・地域別の売上と利益、増減要因",
    },
    "06_ガバナンス": {
        "name": "ガバナンス",
        "extract_focus": "取締役構成、報酬、大株主、ストックオプション",
    },
    "07_その他": {
        "name": "その他情報",
        "extract_focus": "設備投資、研究開発、関係会社、訴訟",
    },
}

# ★ v10.2: PDFフィルタリング設定 - DEPRECATED (replaced by BM25 in v10.3)
# SECTION_PDF_FILTERS = {...}


# ============================================================
# 企業データクラス
# ============================================================
@dataclass
class Company:
    code: str
    name: str
    edinet_code: Optional[str] = None
    industry: str = "all"
# ============================================================
# 業種推定
# ============================================================
def infer_industry_from_name(name: str) -> str:
    n = (name or "").replace("株式会社", "").replace("　", "").replace(" ", "")
    if any(k in n for k in ["水産", "食品", "冷凍", "畜産", "製粉", "飲料", "食", "極洋"]):
        return "food"
    if any(k in n for k in ["銀行", "証券", "保険", "信託", "リース", "フィナンシャル"]):
        return "finance"
    if any(k in n for k in ["ソフト", "システム", "テクノロジー", "通信", "ネット", "クラウド"]):
        return "it"
    if any(k in n for k in ["ストア", "百貨店", "小売", "ドラッグ", "外食", "フードサービス"]):
        return "retail"
    if any(k in n for k in ["工業", "製作所", "機械", "電機", "化学", "金属", "自動車"]):
        return "manufacturing"
    return "all"


def _get_sa_creds():
    if not HAS_GSPREAD:
        return None
    sa_path = os.environ.get("GOOGLE_SA_JSON", "keys/google_sa.json")
    if not Path(sa_path).is_absolute():
        sa_path = Config.PROJECT_DIR / sa_path
    else:
        sa_path = Path(sa_path)
    if not sa_path.exists():
        return None
    return Credentials.from_service_account_file(
        str(sa_path),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly"
        ]
    )


def load_companies_from_sheets() -> List[Company]:
    if not HAS_GSPREAD:
        return []
    creds = _get_sa_creds()
    if not creds:
        return []
    try:
        gc = gspread.authorize(creds)
        ws = gc.open(Config.COMPANY_SPREADSHEET).worksheet(Config.COMPANY_TAB)
        vals = ws.get_all_values()
        if not vals:
            return []
        hdrs = [h.strip() for h in vals[0]]
        ti = hdrs.index("ticker")
        ni = hdrs.index("company_name")
        ei = hdrs.index("edinet_code") if "edinet_code" in hdrs else -1
        ii = hdrs.index("industry") if "industry" in hdrs else -1

        companies = []
        for row in vals[1:]:
            if len(row) <= max(ti, ni):
                continue
            t = str(row[ti]).strip()
            n = str(row[ni]).strip()
            if t and n and len(t) >= 4:
                ind = str(row[ii]).strip() if ii >= 0 and len(row) > ii else "all"
                if not ind or ind == "all":
                    ind = infer_industry_from_name(n)
                companies.append(Company(
                    code=t, name=n,
                    edinet_code=str(row[ei]).strip() if ei >= 0 and len(row) > ei else None,
                    industry=ind
                ))
        logger.info(f"企業リスト: {len(companies)}社")
        return companies
    except Exception as e:
        logger.warning(f"企業リスト読み込みエラー: {e}")
        return []


def load_companies_from_sections() -> List[Company]:
    companies = {}
    if not Config.SECTIONS_BASE.exists():
        return []
    for folder in Config.SECTIONS_BASE.iterdir():
        if folder.is_dir():
            parts = folder.name.split("_", 1)
            if len(parts) >= 2:
                code, name = parts[0], parts[1]
                companies[code] = Company(code=code, name=name, industry=infer_industry_from_name(name))
    return sorted(companies.values(), key=lambda c: c.code)


def search_companies(companies: List[Company], keyword: str) -> List[Company]:
    kw = keyword.lower()
    return [c for c in companies if kw in c.code.lower() or kw in c.name.lower()]


def get_available_companies_with_sections(year: str = None, doc_type: str = None) -> List[Company]:
    companies = []
    if not Config.SECTIONS_BASE.exists():
        return companies
    for folder in Config.SECTIONS_BASE.iterdir():
        if not folder.is_dir():
            continue
        parts = folder.name.split("_", 1)
        if len(parts) < 2:
            continue
        if year and doc_type:
            if not (folder / f"{year}_{doc_type}").exists():
                continue
        elif year:
            if not any(f.name.startswith(year) for f in folder.iterdir() if f.is_dir()):
                continue
        ind = infer_industry_from_name(parts[1])
        companies.append(Company(code=parts[0], name=parts[1], industry=ind))
    return sorted(companies, key=lambda c: c.code)


# ============================================================
# Ollama呼び出し
# ============================================================
def call_ollama(prompt: str, model: str = None, num_predict: int = None,
                temperature: float = None, num_ctx: int = None) -> dict:
    model = model or Config.OLLAMA_MODEL
    num_predict = num_predict or Config.EXTRACT_NUM_PREDICT
    num_ctx = num_ctx or Config.EXTRACT_NUM_CTX
    temperature = temperature if temperature is not None else Config.TEMPERATURE

    # Qwen3の思考モード無効化
    if "qwen" in model.lower() and not prompt.startswith("/no_think"):
        prompt = "/no_think\n" + prompt

    url = f"{Config.OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
        "keep_alive": -1  # モデルをメモリに保持（アンロード防止）
    }

    for attempt in range(Config.MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=600)
            resp.raise_for_status()
            response_text = resp.json().get('response', '')
            # Qwen3の<think>タグを除去
            response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL)
            return {'response': response_text.strip(), 'success': True}
        except Exception:
            if attempt < Config.MAX_RETRIES:
                time.sleep(Config.RETRY_DELAY)
    return {'response': '', 'success': False}


def extract_first_json(text: str) -> Optional[str]:
    """
    Extract first valid JSON object from text

    Improved version:
    - Removes naive text.replace("'", '"') which breaks string literals
    - Properly handles strings with braces inside them
    """
    if not text:
        return None

    # Remove markdown code blocks
    text = re.sub(r'```(?:json)?', '', text, flags=re.IGNORECASE).replace('```', '')

    # Remove trailing commas (safe operation)
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    start = text.find('{')
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        char = text[i]

        # Handle escape sequences
        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        # Handle string boundaries
        if char == '"':
            in_string = not in_string
            continue

        # Only count braces outside of strings
        if not in_string:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        try:
                            # Try cleaning control characters
                            cleaned = re.sub(r'[\x00-\x1f\x7f]', '', candidate)
                            json.loads(cleaned)
                            return cleaned
                        except:
                            continue
    return None


def call_ollama_json(prompt: str, model: str = None, **kwargs) -> Tuple[Optional[Dict], str]:
    result = call_ollama(prompt, model, **kwargs)
    if not result.get('success'):
        return None, ""

    raw = result.get('response', '')
    js = extract_first_json(raw)

    if js:
        try:
            return json.loads(js), raw
        except:
            pass

    if Config.MAX_RETRIES >= 1 and raw:
        retry_prompt = f"以下をJSON形式に修正してください:\n{raw[:700]}\n\nJSON:"
        retry_result = call_ollama(retry_prompt, model, num_predict=350, num_ctx=2048)
        if retry_result.get('success'):
            js = extract_first_json(retry_result.get('response', ''))
            if js:
                try:
                    return json.loads(js), raw
                except:
                    pass

    return {"numbers": [], "facts": [], "drivers": [], "risks": []}, raw


# ============================================================
# XBRL抽出
# ============================================================
def extract_xbrl_from_zip(zip_path: Path, industry: str = 'all') -> Dict[str, Any]:
    logger.info(f"XBRL読み込み: {zip_path.name}")
    tag_manager.load()

    xbrl_content = None
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('.xbrl') and 'PublicDoc' in name:
                    xbrl_content = zf.read(name)
                    break
            if xbrl_content is None:
                for name in zf.namelist():
                    if name.endswith('.xbrl'):
                        xbrl_content = zf.read(name)
                        break
    except Exception as e:
        logger.error(f"ZIP読み込みエラー: {e}")
        return {}

    if xbrl_content is None:
        return {}

    if HAS_LXML:
        return _extract_xbrl_lxml(xbrl_content, industry)
    elif HAS_BS4:
        return _extract_xbrl_bs4(xbrl_content, industry)
    return {}


def _extract_xbrl_lxml(xbrl_content: bytes, industry: str = 'all') -> Dict[str, Any]:
    try:
        parser = etree.XMLParser(recover=True, huge_tree=True)
        root = etree.fromstring(xbrl_content, parser=parser)
    except Exception as e:
        logger.error(f"XML解析エラー: {e}")
        return {}

    context_index = defaultdict(list)
    for elem in root.iter():
        ctx = elem.get('contextRef')
        if ctx:
            context_index[ctx].append(elem)

    duration_patterns = ['CurrentYearDuration_ConsolidatedMember', 'CurrentYearDuration', 'CurrentYTDDuration']
    instant_patterns = ['CurrentYearInstant_ConsolidatedMember', 'CurrentYearInstant']

    def find_contexts(patterns):
        matched = []
        for ctx_name in context_index.keys():
            if 'NonConsolidated' in ctx_name:
                continue
            for i, p in enumerate(patterns):
                if p in ctx_name:
                    matched.append((ctx_name, i))
                    break
        matched.sort(key=lambda x: x[1])
        return [m[0] for m in matched]

    duration_contexts = find_contexts(duration_patterns) or [c for c in context_index.keys() if 'NonConsolidated' not in c]
    instant_contexts = find_contexts(instant_patterns) or [c for c in context_index.keys() if 'NonConsolidated' not in c]

    extracted = {}

    for field_name in tag_manager.get_all_field_names():
        tags = tag_manager.get_tags_for_field(field_name, industry)
        if not tags:
            continue

        is_instant = tags[0].is_instant
        target_contexts = instant_contexts if is_instant else duration_contexts

        best_value, best_priority = None, 999

        for tag_def in tags:
            if best_value is not None and best_priority <= tag_def.priority:
                continue

            tag_local = tag_def.tag_name.split(':')[-1]

            for ctx_name in target_contexts:
                if best_value is not None and best_priority <= tag_def.priority:
                    break
                for elem in context_index.get(ctx_name, []):
                    if not elem.tag:
                        continue
                    try:
                        local_name = etree.QName(elem.tag).localname
                    except:
                        continue
                    if local_name != tag_local:
                        continue

                    match_str = f"{str(elem.tag)} {ctx_name}".lower()
                    if any(kw in match_str for kw in tag_def.excluded_keywords):
                        continue
                    if tag_def.required_keywords and not any(kw in match_str for kw in tag_def.required_keywords):
                        continue

                    value_text = (elem.text or '').strip()
                    if not value_text:
                        continue

                    try:
                        value = float(value_text.replace(',', ''))
                        if tag_def.priority < best_priority:
                            best_value = value
                            best_priority = tag_def.priority
                    except ValueError:
                        pass

        if best_value is not None:
            extracted[field_name] = best_value

    _calculate_derived_metrics(extracted)
    logger.info(f"XBRL抽出完了: {len(extracted)}項目")
    return extracted


def _extract_xbrl_bs4(xbrl_content: bytes, industry: str = 'all') -> Dict[str, Any]:
    try:
        soup = BeautifulSoup(xbrl_content, 'lxml-xml')
    except:
        soup = BeautifulSoup(xbrl_content, 'html.parser')

    all_elements = soup.find_all(True)
    duration_patterns = ['CurrentYearDuration', 'CurrentYTDDuration']
    instant_patterns = ['CurrentYearInstant']

    def matches_context(ctx, patterns):
        if not ctx or 'NonConsolidated' in ctx:
            return False
        return any(p in ctx for p in patterns)

    extracted = {}

    for field_name in tag_manager.get_all_field_names():
        tags = tag_manager.get_tags_for_field(field_name, industry)
        if not tags:
            continue

        is_instant = tags[0].is_instant
        patterns = instant_patterns if is_instant else duration_patterns

        best_value, best_priority = None, 999

        for tag_def in tags:
            if best_value is not None and best_priority <= tag_def.priority:
                continue

            tag_local = tag_def.tag_name.split(':')[-1].lower()

            for elem in all_elements:
                elem_local = (elem.name or '').split(':')[-1].lower()
                if elem_local != tag_local:
                    continue

                ctx = elem.get('contextref', '')
                if not matches_context(ctx, patterns):
                    continue

                match_str = f"{elem.name} {ctx}".lower()
                if any(kw in match_str for kw in tag_def.excluded_keywords):
                    continue
                if tag_def.required_keywords and not any(kw in match_str for kw in tag_def.required_keywords):
                    continue

                value_text = elem.get_text(strip=True)
                if not value_text:
                    continue

                try:
                    value = float(value_text.replace(',', ''))
                    if tag_def.priority < best_priority:
                        best_value = value
                        best_priority = tag_def.priority
                except:
                    pass

        if best_value is not None:
            extracted[field_name] = best_value

    _calculate_derived_metrics(extracted)
    return extracted


# ============================================================
# 派生指標計算（v9.6.1完全版）
# ============================================================
def _calculate_derived_metrics(extracted: Dict):
    """派生財務指標を計算（v9.6.1完全版: D/Eレシオ修正、配当計算改善）"""
    
    # === 利益率 ===
    if 'net_income' in extracted and 'total_equity' in extracted and extracted['total_equity']:
        extracted['roe_calc'] = round((extracted['net_income'] / extracted['total_equity']) * 100, 2)
    
    if 'net_income' in extracted and 'total_assets' in extracted and extracted['total_assets']:
        extracted['roa_calc'] = round((extracted['net_income'] / extracted['total_assets']) * 100, 2)
    
    if 'total_equity' in extracted and 'total_assets' in extracted and extracted['total_assets']:
        extracted['equity_ratio_calc'] = round((extracted['total_equity'] / extracted['total_assets']) * 100, 2)
    
    if 'operating_income' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['operating_margin_calc'] = round((extracted['operating_income'] / extracted['revenue']) * 100, 2)
    
    if 'gross_profit' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['gross_margin_calc'] = round((extracted['gross_profit'] / extracted['revenue']) * 100, 2)
    
    if 'net_income' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['net_margin_calc'] = round((extracted['net_income'] / extracted['revenue']) * 100, 2)
    
    # === EBITDA ===
    op_income = extracted.get('operating_income')
    depreciation = extracted.get('depreciation') or extracted.get('depreciation_cf')
    if op_income and depreciation:
        extracted['ebitda_calc'] = op_income + abs(depreciation)
        if extracted.get('revenue'):
            extracted['ebitda_margin_calc'] = round((extracted['ebitda_calc'] / extracted['revenue']) * 100, 2)
    
    # === FCF（フリーキャッシュフロー）===
    op_cf = extracted.get('operating_cf')
    inv_cf = extracted.get('investing_cf')
    capex = extracted.get('capex') or extracted.get('purchase_ppe_cf')
    
    if op_cf is not None:
        if inv_cf is not None:
            extracted['fcf_calc'] = op_cf + inv_cf
        elif capex is not None:
            extracted['fcf_calc'] = op_cf - abs(capex)
    
    # === 有利子負債 ===
    short_loans = extracted.get('short_term_loans', 0) or 0
    long_loans = extracted.get('long_term_loans', 0) or 0
    bonds = extracted.get('bonds_payable', 0) or 0
    
    interest_bearing = short_loans + long_loans + bonds
    if interest_bearing > 0:
        extracted['interest_bearing_debt_calc'] = interest_bearing
    
    # === Net Debt ===
    cash = extracted.get('cash_and_deposits') or extracted.get('cash_end') or 0
    if 'interest_bearing_debt_calc' in extracted:
        extracted['net_debt_calc'] = extracted['interest_bearing_debt_calc'] - cash
        
        # Net Debt/EBITDA
        if 'ebitda_calc' in extracted and extracted['ebitda_calc'] > 0:
            extracted['net_debt_ebitda_calc'] = round(extracted['net_debt_calc'] / extracted['ebitda_calc'], 2)
    
    # === D/Eレシオ（v9.6.1修正: 倍率として計算、×100しない）===
    equity = extracted.get('total_equity') or extracted.get('shareholders_equity')
    if 'interest_bearing_debt_calc' in extracted and equity and equity > 0:
        # ★ 倍率として計算（%ではない）
        extracted['de_ratio_calc'] = round(extracted['interest_bearing_debt_calc'] / equity, 2)
    
    # === ROIC（投下資本利益率）===
    op_income = extracted.get('operating_income')
    if op_income:
        nopat = op_income * 0.7  # 税率30%仮定
        equity = extracted.get('total_equity') or extracted.get('shareholders_equity')
        debt = extracted.get('interest_bearing_debt_calc')
        if equity and debt:
            invested_capital = equity + debt
            if invested_capital > 0:
                extracted['roic_calc'] = round((nopat / invested_capital) * 100, 2)
    
    # === 配当性向（v9.6.1修正: 複数ソース対応）===
    total_div = extracted.get('total_dividend')
    if not total_div:
        # CFから取得（通常マイナス値）
        dividends_paid = extracted.get('dividends_paid')
        if dividends_paid:
            total_div = abs(dividends_paid)
    
    net_income = extracted.get('net_income')
    if total_div and net_income and net_income > 0:
        extracted['payout_ratio_calc'] = round((total_div / net_income) * 100, 2)
    
    # === 1株配当の計算（v9.6.1追加）===
    if not extracted.get('dividend_per_share'):
        shares = extracted.get('shares_outstanding') or extracted.get('total_shares')
        if total_div and shares and shares > 0:
            extracted['dividend_per_share_calc'] = round(total_div / shares, 2)


def _calculate_cagr(historical: Dict, field: str, years: int = 5) -> Optional[float]:
    """CAGR（年平均成長率）を計算"""
    sorted_years = sorted([y for y in historical.keys()], reverse=True)
    if len(sorted_years) < 2:
        return None
    
    latest_year = sorted_years[0]
    
    for y in sorted_years:
        if latest_year - y >= years - 1:
            oldest_year = y
            break
    else:
        oldest_year = sorted_years[-1]
    
    latest_val = historical.get(latest_year, {}).get(field)
    oldest_val = historical.get(oldest_year, {}).get(field)
    
    if not latest_val or not oldest_val or oldest_val <= 0:
        return None
    
    n = latest_year - oldest_year
    if n <= 0:
        return None
    
    try:
        cagr = ((latest_val / oldest_val) ** (1 / n) - 1) * 100
        return round(cagr, 1)
    except:
        return None


# ============================================================
# XBRLストア読み込み
# ============================================================
XBRL_STORE_PATH = Path(__file__).parent / "xbrl_store"


def _recalculate_derived_metrics(xbrl: Dict) -> Dict:
    """xbrl_storeから読み込んだデータに派生指標を再計算"""
    _calculate_derived_metrics(xbrl)
    return xbrl


def load_historical_xbrl_from_store(company_code: str, company_name: str = None) -> Dict[int, Dict]:
    possible_paths = [
        XBRL_STORE_PATH,
        Path("./xbrl_store"),
        Path(__file__).parent / "xbrl_store",
        Path.cwd() / "xbrl_store",
    ]

    for store_path in possible_paths:
        if not store_path.exists():
            continue

        company_dirs = list(store_path.glob(f"{company_code}_*"))
        if not company_dirs:
            continue

        company_dir = company_dirs[0]
        years_data = {}

        for json_file in company_dir.glob("20*.json"):
            if "_raw_tags" in json_file.name:
                continue
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    year = int(data.get("fiscal_year", json_file.stem))
                    xbrl_data = data.get("data", {})
                    xbrl_data = _recalculate_derived_metrics(xbrl_data)
                    years_data[year] = xbrl_data
            except Exception as e:
                logger.warning(f"XBRL読み込みエラー ({json_file}): {e}")

        if years_data:
            logger.info(f"  📂 xbrl_store: {len(years_data)}年度分ロード ({sorted(years_data.keys())})")
            return years_data

    return {}


# ============================================================
# LocalRAGDB
# ============================================================
class LocalRAGDB:
    def __init__(self, db_path: str = "./rag_db"):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.db_path / "index.json"
        self.index = self._load_index()

    def _load_index(self) -> Dict:
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {'companies': {}}

    def _save_index(self):
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

    def add_xbrl(self, company: str, year: int, xbrl_data: Dict):
        if company not in self.index['companies']:
            self.index['companies'][company] = {'xbrl': {}, 'reports': []}
        self.index['companies'][company]['xbrl'][str(year)] = xbrl_data
        self._save_index()

    def get_historical_xbrl(self, company: str, years: int = 5) -> Dict[int, Dict]:
        if company not in self.index['companies']:
            return {}
        xbrl_data = self.index['companies'][company].get('xbrl', {})
        sorted_years = sorted(xbrl_data.keys(), reverse=True)[:years]
        return {int(y): xbrl_data[y] for y in sorted_years}


# ============================================================
# PDF抽出
# ============================================================
def extract_text_from_pdf_with_pages(pdf_path: Path) -> List[Tuple[int, str]]:
    """
    Extract text from PDF with page numbers

    キャッシュは _current_company_context に依存するため、
    企業が切り替わると自動的に無効化される

    Args:
        pdf_path: Path to PDF file

    Returns:
        List of (page_number, text) tuples
    """
    return _extract_text_from_pdf_cached(pdf_path, _current_company_context)

@lru_cache(maxsize=128)
def _extract_text_from_pdf_cached(pdf_path: Path, company_context: Optional[str]) -> List[Tuple[int, str]]:
    """
    内部キャッシュ関数（企業コンテキスト付き）

    Args:
        pdf_path: Path to PDF file
        company_context: 企業コンテキスト（企業コード_年度）

    Returns:
        List of (page_number, text) tuples
    """
    if not HAS_PDFPLUMBER or not pdf_path.exists():
        return []
    try:
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                text = re.sub(r'[ \t]+', ' ', text).strip()
                if text:
                    pages.append((i + 1, text))
        return pages
    except Exception as e:
        logger.warning(f"PDF読み込みエラー ({pdf_path.name}): {e}")
        return []


# ============================================================
# ★ v9.5.11: 抽出ログ保存（完全版）
# ============================================================
class ExtractionLogger:
    def __init__(self, output_dir: Path, company_code: str, year: str):
        self.log_dir = output_dir / "extraction_logs" / f"{year}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.company_code = company_code
        self.year = year
    
    def save_section_log(self, section_key: str, log_data: Dict):
        if not Config.SAVE_EXTRACTION_LOGS:
            return
        
        log_path = self.log_dir / f"{section_key}_extraction.json"
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"    📝 ログ保存: {log_path.name}")
        except Exception as e:
            logger.warning(f"    ⚠️ ログ保存失敗: {e}")
    
    def save_raw_text(self, section_key: str, pages: List[Tuple[int, str]]):
        if not Config.SAVE_EXTRACTION_LOGS or not Config.LOG_RAW_TEXT:
            return
        
        txt_path = self.log_dir / f"{section_key}_raw.txt"
        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                for page_num, text in pages:
                    f.write(f"{'='*60}\n")
                    f.write(f"[P.{page_num}]\n")
                    f.write(f"{'='*60}\n")
                    f.write(text)
                    f.write("\n\n")
        except Exception as e:
            logger.warning(f"    ⚠️ 生テキスト保存失敗: {e}")


_current_extraction_logger: Optional[ExtractionLogger] = None
# ============================================================
# ★ v10.3: Question-based PDF selection with BM25
# ============================================================
import math
from collections import Counter
import unicodedata


def tokenize(text: str) -> List[str]:
    """
    日本語対応トークナイザー

    - 日本語文字（ひらがな、カタカナ、漢字）は2-gramで分割
    - 英数字はスペース区切り
    - 記号は除去

    Args:
        text: 入力テキスト

    Returns:
        トークンリスト
    """
    if not text:
        return []

    tokens = []
    current_word = []

    for char in text:
        # 日本語文字判定
        if '\u3040' <= char <= '\u309F' or \
           '\u30A0' <= char <= '\u30FF' or \
           '\u4E00' <= char <= '\u9FFF':
            # 蓄積中の英数字をフラッシュ
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []
            # 日本語文字はそのまま追加（2-gramは後で作る）
            tokens.append(char)
        # 英数字
        elif char.isalnum():
            current_word.append(char)
        # 区切り文字
        else:
            if current_word:
                tokens.append(''.join(current_word))
                current_word = []

    # 残りをフラッシュ
    if current_word:
        tokens.append(''.join(current_word))

    # 日本語部分を2-gramに変換
    result = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        # 1文字の日本語の場合、次と組み合わせて2-gram
        if len(token) == 1 and ('\u3040' <= token <= '\u309F' or
                                 '\u30A0' <= token <= '\u30FF' or
                                 '\u4E00' <= token <= '\u9FFF'):
            if i + 1 < len(tokens):
                next_token = tokens[i + 1]
                if len(next_token) == 1 and ('\u3040' <= next_token <= '\u309F' or
                                              '\u30A0' <= next_token <= '\u30FF' or
                                              '\u4E00' <= next_token <= '\u9FFF'):
                    result.append(token + next_token)
                    i += 1
                else:
                    result.append(token)
            else:
                result.append(token)
        else:
            result.append(token)
        i += 1

    return result


def normalize_text(text: str) -> str:
    """
    テキスト正規化（重複検出用）

    - Unicode正規化（NFKC）で全角/半角を統一
    - 句読点・記号を除去
    - 空白を正規化

    Args:
        text: 入力テキスト

    Returns:
        正規化されたテキスト
    """
    if not text:
        return ""

    # Unicode正規化（全角→半角、合成文字の分解など）
    normalized = unicodedata.normalize('NFKC', text)

    # 句読点・記号を除去（日本語文字、英数字、スペースのみ残す）
    cleaned = ''.join(
        char for char in normalized
        if char.isalnum() or char.isspace() or
           '\u3040' <= char <= '\u309F' or  # ひらがな
           '\u30A0' <= char <= '\u30FF' or  # カタカナ
           '\u4E00' <= char <= '\u9FFF'     # 漢字
    )

    # 連続する空白を1つに
    cleaned = ' '.join(cleaned.split())

    return cleaned


class BM25:
    """
    BM25 ranking function for PDF selection

    Parameters:
        k1: term frequency saturation parameter (default: 1.5)
        b: length normalization parameter (default: 0.75)
    """
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs = []
        self.idf = {}
        self.doc_len = []
        self.avgdl = 0
        self.docs = []

    def fit(self, docs: List[str]):
        """
        Build BM25 index from documents

        Args:
            docs: List of document texts
        """
        self.docs = docs
        self.doc_len = [len(tokenize(doc)) for doc in docs]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0

        # Calculate document frequencies
        df = defaultdict(int)
        for doc in docs:
            words = set(tokenize(doc))
            for word in words:
                df[word] += 1

        # Calculate IDF
        num_docs = len(docs)
        self.idf = {
            word: math.log((num_docs - freq + 0.5) / (freq + 0.5) + 1.0)
            for word, freq in df.items()
        }

        # Store term frequencies for each document
        self.doc_freqs = []
        for doc in docs:
            word_counts = Counter(tokenize(doc))
            self.doc_freqs.append(word_counts)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        Search for top-k documents matching the query

        Args:
            query: Query text
            top_k: Number of results to return

        Returns:
            List of (doc_index, score) tuples
        """
        query_words = tokenize(query)
        scores = []

        for idx, (doc_freq, doc_len) in enumerate(zip(self.doc_freqs, self.doc_len)):
            score = 0.0
            for word in query_words:
                if word not in doc_freq:
                    continue

                freq = doc_freq[word]
                idf = self.idf.get(word, 0)

                # BM25 formula
                numerator = freq * (self.k1 + 1)
                denominator = freq + self.k1 * (1 - self.b + self.b * (doc_len / self.avgdl))
                score += idf * (numerator / denominator)

            scores.append((idx, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


def load_manifest(section_folder: Path) -> Dict:
    """
    Load manifest.json from section folder

    Returns:
        Manifest dict or empty dict if not found
    """
    manifest_path = section_folder / "manifest.json"
    if not manifest_path.exists():
        return {}

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"  ⚠️ manifest読み込み失敗: {e}")
        return {}


def select_pdfs_by_question(
    question: str,
    manifest: Dict,
    section_id: str,
    top_k: int = 3,
    min_guaranteed: int = 1
) -> List[str]:
    """
    Select PDFs using BM25 search based on question

    Args:
        question: Question text
        manifest: Manifest dictionary
        section_id: Section ID (e.g., "02_経営戦略_リスク")
        top_k: Number of PDFs to select
        min_guaranteed: Minimum number of PDFs to include (always include first N)

    Returns:
        List of selected PDF paths
    """
    if not manifest or "sections" not in manifest:
        return []

    # Filter sections by section_id
    sections = [s for s in manifest["sections"] if s["section_id"] == section_id]
    if not sections:
        return []

    # Always include first min_guaranteed PDFs
    guaranteed = sections[:min_guaranteed]
    remaining = sections[min_guaranteed:]

    if not remaining:
        return [s["pdf_path"] for s in guaranteed]

    # Build BM25 index from text_head + headings
    docs = []
    for s in remaining:
        text = s.get("text_head", "") + " " + " ".join(s.get("headings", []))
        docs.append(text)

    # Search
    bm25 = BM25()
    bm25.fit(docs)
    results = bm25.search(question, top_k=top_k - min_guaranteed)

    # Combine guaranteed + search results
    selected = list(guaranteed)
    for idx, score in results:
        selected.append(remaining[idx])

    return [s["pdf_path"] for s in selected]


# ============================================================
# ★ v10.2: PDF filtering (Yuho_splitter_v4 output) - DEPRECATED
# Replaced by BM25-based question-driven selection in v10.3
# ============================================================


# ============================================================
# ★ v9.5.11: キーワードベース候補行抽出
# ============================================================
def pick_candidate_lines_with_context(text: str, context_lines: int = 2) -> str:
    """キーワードベースで重要な行を抽出（コンテキスト付き）"""
    lines = text.split('\n')
    selected_indices = set()
    
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean or is_boilerplate(line_clean):
            continue
        has_keyword = any(kw in line_clean for kw in IMPORTANT_KEYWORDS)
        has_number = bool(re.search(r'[\d,]+\.?\d*[%億万]', line_clean))
        if has_keyword or has_number:
            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                selected_indices.add(j)
    
    if not selected_indices:
        return '\n'.join(lines[:50])
    
    result_lines = []
    sorted_indices = sorted(selected_indices)
    prev_idx = -10
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            result_lines.append("\n--- (中略) ---\n")
        result_lines.append(lines[idx])
        prev_idx = idx
    return '\n'.join(result_lines)


def chunk_text_by_paragraph(text: str, max_chars: int = 6000, overlap: int = 500) -> List[str]:
    """段落ベースでテキストをチャンク分割"""
    paragraphs = re.split(r'\n\n+', text)
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        para_len = len(para)
        if current_length + para_len > max_chars and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            if overlap > 0 and current_chunk:
                overlap_text = current_chunk[-1]
                current_chunk = [overlap_text] if len(overlap_text) < overlap else []
                current_length = len('\n\n'.join(current_chunk))
            else:
                current_chunk = []
                current_length = 0
        current_chunk.append(para)
        current_length += para_len + 2
    
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
    return chunks if chunks else [text[:max_chars]]


# ============================================================
# ★ v9.5.11: JSON抽出プロンプト
# ============================================================
def make_extraction_prompt(text: str, section_name: str, industry_template: Dict, company_name: str = None, company_code: str = None) -> str:
    # 企業コンテキスト制約の追加
    company_context_warning = ""
    if company_name or company_code:
        company_context_warning = f"""
⚠️ 重要な制約:
- 分析対象企業: {company_name if company_name else f'証券コード{company_code}'}
- この企業に直接関係する情報のみを抽出すること
- 他の企業の情報を含めないこと
- 下記の出力形式はあくまで例であり、実際のテキストから抽出した情報を記載すること

"""

    return f"""以下は{section_name}セクションのテキストです。JSON形式で情報を抽出してください。
{company_context_warning}
【抽出対象】
1. numbers: 具体的な数値（売上高、利益、成長率、金額など）
2. facts: 重要な事実（イベント、施策、変化など。業績数字の単なる繰り返しは不要）
3. drivers: 業績変動の要因（何が増減したか、なぜか）
4. risks: リスクと対応策

【業種フォーカス: {industry_template['name']}】
{chr(10).join('- ' + p for p in industry_template['focus_points'][:3])}

【注意】
- ページ番号は[P.数字]形式で記載
- impactは"+"（プラス）、"-"（マイナス）、"?"（不明）
- 具体的な金額・割合・変化率を含める
- 定型文（沿革、注記、ガバナンス）は除外
- テキストに記載されている情報のみを抽出すること

【テキスト】
{text}

【出力形式（例）】
{{
  "numbers": [{{"item": "売上高", "value": "10000", "unit": "億円", "yoy": "+5.0%", "page": "P.3"}}],
  "facts": [{{"content": "新製品を発売", "page": "P.7"}}],
  "drivers": [{{"factor": "主力製品の販売増加", "impact": "+", "amount": "約100億円", "page": "P.4"}}],
  "risks": [{{"risk": "為替変動リスク", "response": "ヘッジ取引を実施", "page": "P.15"}}]
}}

JSON:"""


# ============================================================
# ★ v9.5.11: JSON抽出方式でのセクション処理
# ============================================================
def process_section_json_extraction(pages: List[Tuple[int, str]], section_key: str,
                                     xbrl: Dict, prev_xbrl: Dict, industry: str, company_name: str = None, company_code: str = None) -> Dict:
    """JSON抽出方式でセクションを処理（v9.5.11方式 + v10.2: 企業コンテキスト追加）"""
    global _current_extraction_logger
    section_info = SECTION_MAPPING.get(section_key, {"name": section_key})
    industry_template = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])

    # v10.2: 企業コンテキストをグローバル変数から取得
    if company_name is None and _current_extraction_logger:
        company_code = _current_extraction_logger.company_code

    full_text = "\n\n".join([f"[P.{p}]\n{t}" for p, t in pages])
    total_chars = len(full_text)

    if _current_extraction_logger:
        _current_extraction_logger.save_raw_text(section_key, pages)

    # 財務三表はXBRLで代替
    if section_key == "04_財務三表":
        merged = make_financial_section_from_xbrl(xbrl or {})
        if _current_extraction_logger:
            _current_extraction_logger.save_section_log(section_key, {"mode": "xbrl_substitute", "extracted": merged})
        return {"section_key": section_key, "section_name": section_info["name"], "extracted": merged,
                "total_pages": len(pages), "total_chars": total_chars, "mode": "xbrl_substitute"}

    # MDAは全文投入
    if section_key == "03_MDA" and Config.MDA_NO_COMPRESS:
        text_to_process = full_text[:Config.MDA_FULLTEXT_LIMIT] if total_chars > Config.MDA_FULLTEXT_LIMIT else full_text
        chunks = chunk_text_by_paragraph(text_to_process, max_chars=Config.MDA_CHUNK_SIZE, overlap=Config.MDA_CHUNK_OVERLAP)
    else:
        compressed = pick_candidate_lines_with_context(full_text)
        chunks = chunk_text_by_paragraph(compressed, Config.CHUNK_SIZE, Config.CHUNK_OVERLAP) if len(compressed) > Config.CHUNK_SIZE else [compressed]

    all_extracted = {"numbers": [], "facts": [], "drivers": [], "risks": []}
    chunk_logs = []

    for i, chunk in enumerate(chunks):
        prompt = make_extraction_prompt(chunk, section_info["name"], industry_template, company_name, company_code)
        extracted, raw_response = call_ollama_json(prompt, Config.OLLAMA_MODEL,
            num_predict=Config.EXTRACT_NUM_PREDICT, num_ctx=Config.EXTRACT_NUM_CTX, temperature=Config.TEMPERATURE)
        if extracted:
            for key in all_extracted.keys():
                if key in extracted:
                    all_extracted[key].extend(extracted.get(key, []))
        if Config.LOG_CHUNKS or Config.LOG_LLM_RESPONSE:
            chunk_logs.append({"chunk_index": i + 1, "chunk_text": chunk[:1000], "llm_raw_response": raw_response[:2000] if raw_response else "", "extracted": extracted})
    
    # イベントスコアリング適用
    current_year = int(_current_extraction_logger.year) if _current_extraction_logger else None
    for fact in all_extracted.get("facts", []):
        if isinstance(fact, dict):
            fact["_score"] = score_event_text(fact.get("content", ""), current_year)
    all_extracted["facts"] = sorted([f for f in all_extracted.get("facts", []) if isinstance(f, dict)], key=lambda x: x.get("_score", 0), reverse=True)[:10]
    
    # ドライバー重複除去
    seen_drivers = set()
    unique_drivers = []
    for d in all_extracted.get("drivers", []):
        if not isinstance(d, dict):
            continue
        factor = (d.get("factor") or "")[:50]
        if factor and factor not in seen_drivers:
            seen_drivers.add(factor)
            d["page"] = normalize_page(d.get("page"))
            d["impact"] = normalize_impact(d.get("impact"))
            unique_drivers.append(d)
    all_extracted["drivers"] = unique_drivers[:15]
    
    if _current_extraction_logger:
        _current_extraction_logger.save_section_log(section_key, {"mode": "json_extraction", "total_chars": total_chars,
            "num_chunks": len(chunks), "chunks": chunk_logs if Config.LOG_CHUNKS else [],
            "merged_result": all_extracted, "merged_counts": {k: len(v) for k, v in all_extracted.items()}})
    
    return {"section_key": section_key, "section_name": section_info["name"], "extracted": all_extracted,
            "total_pages": len(pages), "total_chars": total_chars, "num_chunks": len(chunks), "mode": "json_extraction"}


# ============================================================
# ★ v9.6.1: 質問応答方式でのセクション処理
# ============================================================
def analyze_section_qa(section_key: str, section_text: str, xbrl: Dict,
                       prev_xbrl: Dict, industry: str, company_name: str = None, company_code: str = None) -> Dict:
    """セクションを読んで質問に答えさせる（v9.6.1方式 + v10.1: 企業コンテキスト追加）"""
    section_config = SECTION_QUESTIONS.get(section_key)
    if not section_config:
        return {"section_key": section_key, "answers": [], "skipped": True}

    # v10.1: 企業コンテキストをグローバル変数から取得
    if company_name is None and _current_extraction_logger:
        company_code = _current_extraction_logger.company_code
        # company_nameは取得できないが、company_codeがあれば警告を出す

    xbrl_summary = _build_xbrl_summary(xbrl, prev_xbrl)

    max_text_len = min(len(section_text), Config.QA_NUM_CTX * 2)
    truncated_text = section_text[:max_text_len]

    # v10.1: 企業固有制約の追加
    company_context_warning = ""
    if company_name or company_code:
        company_context_warning = f"""
⚠️ 重要な制約:
- 分析対象企業: {company_name if company_name else f'証券コード{company_code}'}
- この企業に直接関係する情報のみを抽出すること
- 他の企業（極洋、トヨタ、ソニー、任天堂等）の情報を含めないこと
- 業種に合わない情報（水産業でない企業に「ホタテ」等）は除外すること
"""

    answers = []

    for q in section_config["questions"]:
        logger.info(f"      質問: {q['id']}...")

        prompt = f"""以下のテキストを読んで質問に答えてください。
{company_context_warning}
【テキスト】
{truncated_text}

【参考データ】
{xbrl_summary}

【質問】
{q['question']}

【回答ルール】
- テキストに書いてある内容だけで答える
- 数値・金額・割合を含める
- ページ番号[P.X]を付ける
- 分析対象企業に関係のない情報は含めない

回答:"""

        result = call_ollama(
            prompt, 
            Config.OLLAMA_MODEL,
            num_predict=Config.QA_NUM_PREDICT,
            num_ctx=Config.QA_NUM_CTX,
            temperature=Config.QA_TEMPERATURE
        )
        
        answer_text = ""
        if result.get('success'):
            answer_text = result.get('response', '')
            
            refusal_patterns = ['申し訳', 'できません', 'PDFファイル', 'テキストのみ', '読み取ることができ']
            if any(p in answer_text[:100] for p in refusal_patterns):
                logger.warning(f"        ⚠️ 回答拒否検出、リトライ...")
                
                retry_text = section_text[:8000]
                retry_prompt = f"""テキスト:
{retry_text}

質問: {q['question']}

上記テキストから該当する内容を箇条書きで回答。ページ番号[P.X]も記載。

回答:"""
                retry_result = call_ollama(
                    retry_prompt,
                    Config.OLLAMA_MODEL,
                    num_predict=1500,
                    num_ctx=12000,
                    temperature=0.3
                )
                if retry_result.get('success'):
                    retry_answer = retry_result.get('response', '')
                    if not any(p in retry_answer[:100] for p in refusal_patterns):
                        answer_text = retry_answer
                        logger.info(f"        ✅ リトライ成功")
        
        answers.append({
            "question_id": q['id'],
            "question": q['question'],
            "focus": q['focus'],
            "answer": answer_text if answer_text else "（回答取得失敗）",
        })
    
    return {
        "section_key": section_key,
        "section_name": section_config["name"],
        "answers": answers,
        "text_length": len(section_text),
    }


def _build_xbrl_summary(xbrl: Dict, prev_xbrl: Dict) -> str:
    """XBRLデータのサマリを作成"""
    lines = []
    
    items = [
        ('revenue', '売上高'),
        ('operating_income', '営業利益'),
        ('ordinary_income', '経常利益'),
        ('net_income', '純利益'),
        ('operating_margin_calc', '営業利益率'),
        ('gross_margin_calc', '粗利率'),
        ('operating_cf', '営業CF'),
    ]
    
    for key, label in items:
        val = xbrl.get(key)
        prev_val = prev_xbrl.get(key) if prev_xbrl else None
        
        if val is not None:
            if key.endswith('_calc'):
                display = f"{val:.1f}%"
            else:
                display = fmt_yen(val)
            
            yoy = fmt_change(val, prev_val) if prev_val else "前年データなし"
            lines.append(f"- {label}: {display} (前年比: {yoy})")
    
    return "\n".join(lines) if lines else "XBRLデータなし"


# ============================================================
# ★ v9.6.1: 質問応答方式でのセクション処理（ラッパー）
# ============================================================
def process_section_qa_mode(pages: List[Tuple[int, str]], section_key: str,
                            xbrl: Dict, prev_xbrl: Dict, industry: str,
                            company_name: str = None, company_code: str = None) -> Dict:
    """質問応答方式でセクションを処理（v9.6.1方式 + v10.1: 企業コンテキスト）"""
    global _current_extraction_logger
    section_info = SECTION_MAPPING.get(section_key, {"name": section_key})
    
    full_text = "\n\n".join([f"[P.{p}]\n{t}" for p, t in pages])
    total_chars = len(full_text)
    
    if _current_extraction_logger:
        _current_extraction_logger.save_raw_text(section_key, pages)
    
    # 財務三表はXBRLで代替
    if section_key == "04_財務三表":
        merged = make_financial_section_from_xbrl(xbrl or {})
        if _current_extraction_logger:
            _current_extraction_logger.save_section_log(section_key, {
                "mode": "xbrl_substitute",
                "extracted": merged,
            })
        return {
            "section_key": section_key,
            "section_name": section_info["name"],
            "extracted": merged,
            "qa_answers": [],
            "total_pages": len(pages),
            "total_chars": total_chars,
            "mode": "xbrl_substitute",
        }
    
    # 質問応答対象セクション
    if section_key in SECTION_QUESTIONS:
        qa_result = analyze_section_qa(section_key, full_text, xbrl, prev_xbrl, industry, company_name, company_code)
        
        drivers = []
        facts = []
        risks = []
        
        for ans in qa_result.get("answers", []):
            if not isinstance(ans, dict):
                continue
            qid = ans.get("question_id", "")
            answer = ans.get("answer", "")
            
            if qid in ["revenue_drivers", "profit_drivers"]:
                drivers.append({
                    "factor": answer[:500],
                    "impact": "?",
                    "page": "QA",
                })
            elif qid == "key_events":
                facts.append({
                    "content": answer[:500],
                    "page": "QA",
                })
            elif qid == "risks":
                risks.append({
                    "risk": answer[:500],
                    "response": "",
                    "page": "QA",
                })
        
        extracted = {
            "numbers": [],
            "facts": facts,
            "drivers": drivers,
            "risks": risks,
        }
        
        if _current_extraction_logger:
            _current_extraction_logger.save_section_log(section_key, {
                "mode": "qa",
                "qa_answers": qa_result.get("answers", []),
                "extracted": extracted,
                "text_length": total_chars,
            })
        
        return {
            "section_key": section_key,
            "section_name": section_info["name"],
            "extracted": extracted,
            "qa_answers": qa_result.get("answers", []),
            "total_pages": len(pages),
            "total_chars": total_chars,
            "mode": "qa",
        }
    
    # その他のセクションは空で返す
    empty_extracted = {"numbers": [], "facts": [], "drivers": [], "risks": []}
    
    if _current_extraction_logger:
        _current_extraction_logger.save_section_log(section_key, {
            "mode": "skipped",
            "extracted": empty_extracted,
            "text_length": total_chars,
        })
    
    return {
        "section_key": section_key,
        "section_name": section_info["name"],
        "extracted": empty_extracted,
        "qa_answers": [],
        "total_pages": len(pages),
        "total_chars": total_chars,
        "mode": "skipped",
    }


# ============================================================
# ★ v10: ハイブリッドモードでのセクション処理
# ============================================================
def process_section_hybrid(pages: List[Tuple[int, str]], section_key: str,
                           xbrl: Dict, prev_xbrl: Dict, industry: str,
                           company_name: str = None, company_code: str = None) -> Dict:
    """ハイブリッドモード: JSON抽出 + 質問応答を併用（v10.1: 企業コンテキスト）"""
    global _current_extraction_logger
    section_info = SECTION_MAPPING.get(section_key, {"name": section_key})
    
    full_text = "\n\n".join([f"[P.{p}]\n{t}" for p, t in pages])
    total_chars = len(full_text)
    
    if _current_extraction_logger:
        _current_extraction_logger.save_raw_text(section_key, pages)
    
    # 財務三表はXBRLで代替
    if section_key == "04_財務三表":
        merged = make_financial_section_from_xbrl(xbrl or {})
        return {
            "section_key": section_key,
            "section_name": section_info["name"],
            "extracted": merged,
            "qa_answers": [],
            "total_pages": len(pages),
            "total_chars": total_chars,
            "mode": "xbrl_substitute",
        }
    
    # ステップ1: JSON抽出（v9.5.11方式 + v10.2: 企業コンテキスト）
    json_result = process_section_json_extraction(pages, section_key, xbrl, prev_xbrl, industry, company_name, company_code)
    json_extracted = json_result.get("extracted", {})
    
    # ステップ2: 質問応答（v9.6.1方式）- 該当セクションのみ
    qa_answers = []
    if section_key in SECTION_QUESTIONS:
        qa_result = analyze_section_qa(section_key, full_text, xbrl, prev_xbrl, industry, company_name, company_code)
        qa_answers = qa_result.get("answers", [])
    
    # 両方の結果をマージ
    merged_extracted = {
        "numbers": json_extracted.get("numbers", []),
        "facts": json_extracted.get("facts", []),
        "drivers": json_extracted.get("drivers", []),
        "risks": json_extracted.get("risks", []),
    }
    
    # QAからの追加情報をマージ（重複しないように）
    for ans in qa_answers:
        qid = ans.get("question_id", "")
        answer = ans.get("answer", "")

        if qid in ["revenue_drivers", "profit_drivers"] and answer:
            # 既存ドライバーと重複チェック（正規化して比較）
            answer_normalized = normalize_text(answer)[:100]
            existing_factors = {
                normalize_text(d.get("factor", ""))[:100]
                for d in merged_extracted["drivers"]
            }
            if answer_normalized not in existing_factors:
                merged_extracted["drivers"].append({
                    "factor": answer[:500],
                    "impact": "?",
                    "page": "QA",
                    "source": "qa",
                })
    
    if _current_extraction_logger:
        _current_extraction_logger.save_section_log(section_key, {
            "mode": "hybrid",
            "json_result": json_extracted,
            "qa_answers": qa_answers,
            "merged_result": merged_extracted,
            "text_length": total_chars,
        })
    
    return {
        "section_key": section_key,
        "section_name": section_info["name"],
        "extracted": merged_extracted,
        "qa_answers": qa_answers,
        "total_pages": len(pages),
        "total_chars": total_chars,
        "mode": "hybrid",
    }


# ============================================================
# 財務三表はXBRLで代替
# ============================================================
def make_financial_section_from_xbrl(xbrl: Dict[str, Any]) -> Dict:
    numbers = []
    mapping = [
        ("revenue", "売上高", "億円"),
        ("cost_of_sales", "売上原価", "億円"),
        ("gross_profit", "売上総利益", "億円"),
        ("sga_expense", "販管費", "億円"),
        ("operating_income", "営業利益", "億円"),
        ("ordinary_income", "経常利益", "億円"),
        ("net_income", "純利益", "億円"),
        ("total_assets", "総資産", "億円"),
        ("total_equity", "純資産", "億円"),
        ("operating_cf", "営業CF", "億円"),
        ("investing_cf", "投資CF", "億円"),
        ("financing_cf", "財務CF", "億円"),
        ("roe_calc", "ROE", "%"),
        ("roa_calc", "ROA", "%"),
        ("operating_margin_calc", "営業利益率", "%"),
        ("gross_margin_calc", "粗利率", "%"),
        ("net_margin_calc", "純利益率", "%"),
        ("equity_ratio_calc", "自己資本比率", "%"),
    ]

    for key, label, unit in mapping:
        val = xbrl.get(key)
        if val is None:
            continue

        if unit == "億円":
            display_val = f"{val / 1e8:.1f}"
        else:
            display_val = f"{val:.2f}"

        numbers.append({
            "item": label,
            "value": display_val,
            "unit": unit,
            "yoy": "N/A",
            "page": "XBRL"
        })

    return {
        "numbers": numbers,
        "facts": [{"content": "財務三表の主要数値はXBRLから取得", "page": "XBRL"}],
        "drivers": [],
        "risks": [],
    }


# ============================================================
# ★ v9.6.1: XBRL数値チェック機構
# ============================================================
def _calculate_segment_totals(segment_checks: List[Dict], xbrl: Dict) -> Dict:
    """セグメント数値の合計を計算して全社と比較"""
    result = {}

    # 売上高の合計
    segment_revenues = [c['report_raw'] for c in segment_checks if c['item'] == '売上高']
    if segment_revenues and len(segment_revenues) >= 2:
        total_segment_revenue = sum(segment_revenues)
        company_revenue = xbrl.get('revenue')
        if company_revenue:
            diff = abs(total_segment_revenue - company_revenue)
            diff_pct = (diff / company_revenue) * 100
            result['revenue'] = {
                'segment_total': total_segment_revenue,
                'company_total': company_revenue,
                'diff': diff,
                'diff_pct': diff_pct,
                'match': diff_pct < 1.0,  # 1%未満を一致とみなす
            }

    # 営業利益の合計
    segment_op_incomes = [c['report_raw'] for c in segment_checks if c['item'] == '営業利益']
    if segment_op_incomes and len(segment_op_incomes) >= 2:
        total_segment_op = sum(segment_op_incomes)
        company_op = xbrl.get('operating_income')
        if company_op:
            diff = abs(total_segment_op - company_op)
            diff_pct = (diff / abs(company_op)) * 100
            result['operating_income'] = {
                'segment_total': total_segment_op,
                'company_total': company_op,
                'diff': diff,
                'diff_pct': diff_pct,
                'match': diff_pct < 5.0,  # 5%未満を一致とみなす（調整が入るため）
            }

    return result


def check_numbers_against_xbrl(report_text: str, xbrl: Dict) -> Dict:
    """レポート内の数値とXBRLの整合性をチェック（全社KPIとセグメント別）"""
    issues = []
    company_wide_checks = []
    segment_checks = []

    # セグメント関連キーワード（拡張版）
    segment_keywords = ['セグメント', '水産商事', '食品', '鰹・鮪', '鰹鮪', '物流', 'ロジスティクス',
                        '冷凍食品', '常温食品', '海外', '国内', '部門', '事業部']

    # 全社KPIセクションのキーワード
    company_wide_keywords = ['数字で見る', 'スコアボード', '全社', '連結']
    
    check_items = [
        {
            'xbrl_key': 'revenue',
            'label': '売上高',
            'patterns': [
                r'売上高[：:は]?\s*([\d,]+\.?\d*)\s*(億|百万)',
            ],
            'unit_multiplier': {'億': 1e8, '百万': 1e6},
        },
        {
            'xbrl_key': 'operating_income',
            'label': '営業利益',
            'patterns': [
                r'営業利益[：:は]?\s*([\d,]+\.?\d*)\s*(億|百万)',
            ],
            'unit_multiplier': {'億': 1e8, '百万': 1e6},
        },
        {
            'xbrl_key': 'net_income',
            'label': '純利益',
            'patterns': [
                r'純利益[：:は]?\s*([\d,]+\.?\d*)\s*(億|百万)',
                r'当期純利益[：:は]?\s*([\d,]+\.?\d*)\s*(億|百万)',
            ],
            'unit_multiplier': {'億': 1e8, '百万': 1e6},
        },
    ]
    
    for item in check_items:
        xbrl_value = xbrl.get(item['xbrl_key'])
        if xbrl_value is None:
            continue
        
        for pattern in item['patterns']:
            for match in re.finditer(pattern, report_text):
                try:
                    num_str = match.group(1)
                    unit = match.group(2)
                    num = float(num_str.replace(',', ''))
                    multiplier = item['unit_multiplier'].get(unit, 1)
                    report_value = num * multiplier
                    
                    start = max(0, match.start() - 200)
                    end = min(len(report_text), match.end() + 100)
                    context = report_text[start:end]

                    # セグメント関連かどうかを判定
                    is_segment = any(kw in context for kw in segment_keywords)
                    # 全社KPIセクションかどうかを判定
                    is_company_wide = any(kw in context for kw in company_wide_keywords)

                    tolerance = abs(xbrl_value) * 0.01
                    diff = abs(report_value - xbrl_value)

                    check_result = {
                        'item': item['label'],
                        'report_value': f"{num}{unit}",
                        'xbrl_value': fmt_yen(xbrl_value),
                        'xbrl_raw': xbrl_value,
                        'report_raw': report_value,
                        'match': diff <= tolerance,
                        'is_segment': is_segment,
                        'is_company_wide': is_company_wide,
                        'context_snippet': context[:80] + '...' if len(context) > 80 else context,
                    }

                    # 全社KPIのみをチェック対象にする
                    if is_company_wide or (not is_segment and '数字で見る' not in report_text[:match.start()]):
                        company_wide_checks.append(check_result)
                        if diff > tolerance:
                            issues.append({
                                'item': item['label'],
                                'report_value': f"{num}{unit}",
                                'xbrl_value': fmt_yen(xbrl_value),
                                'xbrl_raw': xbrl_value,
                                'diff_pct': f"{(diff / abs(xbrl_value)) * 100:.1f}%",
                            })
                    elif is_segment:
                        # セグメント数値は別で記録（チェックはしない）
                        segment_checks.append(check_result)
                except:
                    pass

    # セグメント合計値の計算（可能なら）
    segment_summary = _calculate_segment_totals(segment_checks, xbrl)

    return {
        'company_wide_checks': company_wide_checks,
        'segment_checks': segment_checks,
        'segment_summary': segment_summary,
        'issues': issues,
        'has_issues': len(issues) > 0,
    }


def fix_numbers_in_report(report_text: str, xbrl: Dict) -> str:
    """レポート内の数値をXBRLの正確な値で置換"""
    if xbrl.get('revenue'):
        rev_oku = xbrl['revenue'] / 1e8
        report_text = re.sub(
            r'売上高[：:]\s*[\d,]+\.?\d*億円',
            f'売上高: {rev_oku:,.1f}億円',
            report_text
        )
    
    if xbrl.get('operating_income'):
        op_oku = xbrl['operating_income'] / 1e8
        report_text = re.sub(
            r'営業利益[：:]\s*[\d,]+\.?\d*億円',
            f'営業利益: {op_oku:,.1f}億円',
            report_text
        )
    
    if xbrl.get('net_income'):
        ni_oku = xbrl['net_income'] / 1e8
        report_text = re.sub(
            r'純利益[：:]\s*[\d,]+\.?\d*億円',
            f'純利益: {ni_oku:,.1f}億円',
            report_text
        )
    
    return report_text
# ============================================================
# 最終レポート生成（v9.6.1完全版 + v9.5.11統合）
# ============================================================
def generate_final_report_v10(company_name: str, company_code: str, year: int,
                               xbrl: Dict, prev_xbrl: Dict, section_extracts: List[Dict],
                               industry: str, historical_xbrl: Dict = None) -> Tuple[str, Dict]:
    """最終レポート生成（v10: v9.6.1 + v9.5.11統合）"""
    industry_template = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])
    historical_xbrl = historical_xbrl or {}
    
    # YoY計算（主要指標の前年差を事前計算）
    rev_yoy = fmt_change(xbrl.get('revenue'), prev_xbrl.get('revenue') if prev_xbrl else None)
    op_yoy = fmt_change(xbrl.get('operating_income'), prev_xbrl.get('operating_income') if prev_xbrl else None)
    ni_yoy = fmt_change(xbrl.get('net_income'), prev_xbrl.get('net_income') if prev_xbrl else None)

    # セグメント数値チェック用に全社売上高を明示
    xbrl_revenue = f"{xbrl.get('revenue')/1e8:,.1f}億円" if xbrl.get('revenue') else "N/A"

    # 主要指標の絶対差・前年差を計算（用語統一用）
    def calc_abs_change(current, previous):
        """絶対差とパーセンテージポイント差を計算（高精度）"""
        if current is None or previous is None:
            return None, None
        # 丸め誤差を防ぐため、差分を先に計算してから丸める
        diff = round(current - previous, 1)
        return diff, f"{current:.1f}% (前年: {previous:.1f}%, 差分: {diff:+.1f}pt)"

    equity_ratio_current = xbrl.get('equity_ratio_calc')
    equity_ratio_prev = prev_xbrl.get('equity_ratio_calc') if prev_xbrl else None
    equity_ratio_abs_change, equity_ratio_change_text = calc_abs_change(equity_ratio_current, equity_ratio_prev)

    def fmt_d(val, u="億円"): return f"{val/1e8:,.1f}{u}" if val else "N/A"
    def fmt_p(val): return f"{val:.1f}%" if val else "N/A"
    def fmt_r(val): return f"{val:.2f}x" if val else "N/A"
    
    # CAGR計算
    rev_cagr = _calculate_cagr(historical_xbrl, 'revenue', 5) if historical_xbrl else None
    op_cagr = _calculate_cagr(historical_xbrl, 'operating_income', 5) if historical_xbrl else None
    ni_cagr = _calculate_cagr(historical_xbrl, 'net_income', 5) if historical_xbrl else None
    
    # 配当情報
    dps = xbrl.get('dividend_per_share') or xbrl.get('dividend_per_share_calc')
    payout = xbrl.get('payout_ratio') or xbrl.get('payout_ratio_calc')
    
    # 投資銀行指標
    ebitda = xbrl.get('ebitda_calc')
    fcf = xbrl.get('fcf_calc')
    net_debt = xbrl.get('net_debt_calc')
    net_debt_ebitda = xbrl.get('net_debt_ebitda_calc')
    de_ratio = xbrl.get('de_ratio_calc')
    roa = xbrl.get('roa_calc')
    roic = xbrl.get('roic_calc')
    ebitda_margin = xbrl.get('ebitda_margin_calc')

    # 用語統一のための明示的定義
    cash_and_equivalents = xbrl.get('cash_and_deposits')
    interest_bearing_debt = xbrl.get('interest_bearing_debt_calc')

    # コスト構造分析（XBRLから自動計算）
    cost_structure_analysis = ""
    if xbrl.get('gross_margin_calc') and prev_xbrl:
        prev_gross_margin = prev_xbrl.get('gross_margin_calc')
        if prev_gross_margin:
            gm_change = xbrl.get('gross_margin_calc') - prev_gross_margin
            cost_structure_analysis += f"- 粗利率: {xbrl.get('gross_margin_calc'):.1f}% (前年: {prev_gross_margin:.1f}%, {gm_change:+.1f}pt) (XBRL)\n"

    if xbrl.get('sga_expense') and xbrl.get('revenue'):
        sga_ratio = (xbrl.get('sga_expense') / xbrl.get('revenue')) * 100
        cost_structure_analysis += f"- 販管費率: {sga_ratio:.1f}% (XBRL)\n"
        if prev_xbrl and prev_xbrl.get('sga_expense') and prev_xbrl.get('revenue'):
            prev_sga_ratio = (prev_xbrl.get('sga_expense') / prev_xbrl.get('revenue')) * 100
            sga_change = sga_ratio - prev_sga_ratio
            cost_structure_analysis += f"  前年比: {sga_change:+.1f}pt (XBRL)\n"

    if not cost_structure_analysis:
        cost_structure_analysis = "（XBRL計算データ不足）"

    # 営業CFの説明（運転資本の影響を分析）
    operating_cf_analysis = ""
    op_cf = xbrl.get('operating_cf')
    net_income = xbrl.get('net_income')

    if op_cf is not None and net_income is not None:
        if op_cf < 0 and net_income > 0:
            # 営業CFがマイナスで純利益がプラスの場合
            diff = abs(op_cf) + abs(net_income)

            # 運転資本の内訳を確認（取得可能な場合のみ記載）
            working_capital_details = []
            accounts_receivable_inc = xbrl.get('increase_decrease_in_trade_receivables')
            inventory_inc = xbrl.get('increase_decrease_in_inventories')

            if accounts_receivable_inc is not None or inventory_inc is not None:
                if accounts_receivable_inc and accounts_receivable_inc < 0:  # マイナス=増加（CFから見て）
                    working_capital_details.append(f"売掛金増加: {fmt_d(abs(accounts_receivable_inc))} (XBRL)")
                if inventory_inc and inventory_inc < 0:
                    working_capital_details.append(f"在庫増加: {fmt_d(abs(inventory_inc))} (XBRL)")

                detail_text = "\n".join(f"  - {d}" for d in working_capital_details)

                operating_cf_analysis = f"""
【営業CFマイナスの分析】
営業CF: {fmt_d(op_cf)}、純利益: {fmt_d(net_income)}
→ 純利益がプラスにも関わらず営業CFがマイナス（差額: {fmt_d(diff)}）
→ 運転資本増加（売掛金・在庫増加による現金流出）が主因 (XBRL)
{detail_text}"""
            else:
                # XBRLから内訳が取得できない場合は推定せず、事実のみ記載
                operating_cf_analysis = f"""
【営業CFマイナスの分析】
営業CF: {fmt_d(op_cf)}、純利益: {fmt_d(net_income)}
→ 純利益がプラスにも関わらず営業CFがマイナス（差額: {fmt_d(diff)}）
→ 運転資本変動の詳細はXBRLタグ未取得 (XBRL)"""

    # FCF計算の詳細説明
    fcf_explanation = ""
    if fcf:
        fcf_explanation = f"FCF: {fmt_d(fcf)} (XBRL)"
    else:
        inv_cf = xbrl.get('investing_cf')
        capex = xbrl.get('capex')

        # 投資CFからFCFを概算
        if op_cf is not None and inv_cf is not None:
            estimated_fcf = op_cf + inv_cf
            fcf_explanation = f"FCF（概算）: {fmt_d(estimated_fcf)} = 営業CF{fmt_d(op_cf)} + 投資CF{fmt_d(inv_cf)} (XBRL)"
        elif op_cf is not None and capex is not None:
            estimated_fcf = op_cf - abs(capex)
            fcf_explanation = f"FCF（概算）: {fmt_d(estimated_fcf)} = 営業CF{fmt_d(op_cf)} － Capex{fmt_d(abs(capex))} (XBRL)"
        elif op_cf is not None:
            fcf_explanation = f"FCF算出不可（Capex未取得、営業CF={fmt_d(op_cf)}） (XBRL)"
        else:
            fcf_explanation = "FCF算出不可（営業CF未取得） (XBRL)"
    
    scoreboard = f"""【収益性】
- 売上高: {fmt_d(xbrl.get('revenue'))} ({rev_yoy}) (XBRL)
- 営業利益: {fmt_d(xbrl.get('operating_income'))} ({op_yoy}) (XBRL)
- EBITDA: {fmt_d(ebitda)} / EBITDAマージン: {fmt_p(ebitda_margin)} (XBRL)
- 純利益: {fmt_d(xbrl.get('net_income'))} ({ni_yoy}) (XBRL)
- 粗利率: {fmt_p(xbrl.get('gross_margin_calc'))} / 営業利益率: {fmt_p(xbrl.get('operating_margin_calc'))} (XBRL)

【キャッシュフロー】
- 営業CF: {fmt_d(xbrl.get('operating_cf'))} (XBRL)
- {fcf_explanation}

【財務健全性】
- 自己資本比率: {equity_ratio_change_text if equity_ratio_change_text else fmt_p(xbrl.get('equity_ratio_calc'))} (XBRL)
- 現金及び現金同等物（Cash & Equivalents）: {fmt_d(cash_and_equivalents)} (XBRL)
- 有利子負債（Interest-bearing Debt）: {fmt_d(interest_bearing_debt)} (XBRL)
- Net Debt（有利子負債－現金）: {fmt_d(net_debt)} (XBRL)
- Net Debt/EBITDA: {fmt_r(net_debt_ebitda)} (XBRL)
- D/Eレシオ: {fmt_r(de_ratio)} (XBRL)

【資本効率】
- ROE: {fmt_p(xbrl.get('roe_calc'))} / ROA: {fmt_p(roa)} / ROIC: {fmt_p(roic)} (XBRL)

【株主還元】
- 1株配当: {f'{dps:,.0f}円' if dps else 'N/A'} (XBRL)
- 配当性向: {fmt_p(payout)} (XBRL)

【成長性（CAGR）】
- 売上高CAGR: {fmt_p(rev_cagr) if rev_cagr else 'N/A'}
- 営業利益CAGR: {fmt_p(op_cagr) if op_cagr else 'N/A'}
- 純利益CAGR: {fmt_p(ni_cagr) if ni_cagr else 'N/A'}"""

    # 時系列テーブル生成
    def build_historical_table(historical: Dict, current_year: int) -> str:
        if not historical:
            return "（時系列データなし）"
        
        years = sorted([y for y in historical.keys() if y <= current_year], reverse=True)[:5]
        if len(years) < 2:
            return "（時系列データ不足）"
        
        lines = ["| 項目 | " + " | ".join(str(y) for y in years) + " |"]
        lines.append("|" + "---|" * (len(years) + 1))
        
        metrics = [
            ('revenue', '売上高', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('operating_income', '営業利益', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('ebitda_calc', 'EBITDA', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('net_income', '純利益', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('operating_margin_calc', '営業利益率', lambda v: f"{v:.1f}%" if v else "-"),
            ('roe_calc', 'ROE', lambda v: f"{v:.1f}%" if v else "-"),
            ('roic_calc', 'ROIC', lambda v: f"{v:.1f}%" if v else "-"),
            ('equity_ratio_calc', '自己資本比率', lambda v: f"{v:.1f}%" if v else "-"),
            ('operating_cf', '営業CF', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('fcf_calc', 'FCF', lambda v: f"{v/1e8:.1f}億" if v else "-"),
            ('net_debt_ebitda_calc', 'Net Debt/EBITDA', lambda v: f"{v:.1f}x" if v else "-"),
        ]
        
        for key, label, formatter in metrics:
            values = []
            for y in years:
                data = historical.get(y, {})
                val = data.get(key)
                values.append(formatter(val))
            lines.append(f"| {label} | " + " | ".join(values) + " |")
        
        return "\n".join(lines)
    
    historical_table = build_historical_table(historical_xbrl, year)

    # 質問応答の結果を整理
    qa_results = {}
    for section in section_extracts:
        for ans in section.get('qa_answers', []):
            if not isinstance(ans, dict):
                continue
            qid = ans.get('question_id', '')
            answer = ans.get('answer', '')
            if answer and not answer.startswith('（'):
                qa_results[qid] = answer
    
    def get_qa(key):
        return qa_results.get(key, "")

    # JSON抽出の結果を整理
    all_drivers, all_facts, all_risks = [], [], []
    for section in section_extracts:
        ext = section.get('extracted', {})
        all_drivers.extend(ext.get('drivers', []))
        all_facts.extend(ext.get('facts', []))
        all_risks.extend(ext.get('risks', []))
    
    def format_drivers(drivers, limit=10):
        lines = []
        for d in drivers[:limit]:
            factor = (d.get('factor', '') or '')[:150]
            if d.get('source') == 'qa':
                continue  # QAソースはスキップ（別途表示）
            impact = impact_to_display(d.get('impact', '?'))
            page = d.get('page', 'P.?')
            amount = (d.get('amount', '') or '')
            if not is_boilerplate(factor):
                lines.append(f"- {factor} ({impact}) {amount} ({page})")
        return '\n'.join(lines) if lines else "（JSON抽出なし）"

    def format_facts(facts, limit=5):
        """イベントをフォーマット（重複排除付き）"""
        lines = []
        seen_contents = set()
        for f in facts:
            if len(lines) >= limit:
                break
            content = (f.get('content', '') or '')[:200]
            page = f.get('page', 'P.?')
            # 重複チェック（最初の50文字で判定）
            content_key = content[:50].lower().strip()
            if content_key and content_key not in seen_contents and not is_boilerplate(content):
                seen_contents.add(content_key)
                lines.append(f"- {content} ({page})")
        return '\n'.join(lines) if lines else "（抽出なし）"
    
    def format_risks(risks, limit=5):
        lines = []
        for r in risks[:limit]:
            risk = (r.get('risk', '') or '')[:100]
            response = (r.get('response', '') or '')[:100]
            page = r.get('page', 'P.?')
            lines.append(f"- {risk} → {response} ({page})")
        return '\n'.join(lines) if lines else "（抽出なし）"

    # セグメント・商材情報
    segment_revenue = get_qa('segment_revenue')
    segment_profit = get_qa('segment_profit')
    geographic = get_qa('geographic_breakdown')
    product_portfolio = get_qa('product_portfolio')

    segment_detail = ""
    if segment_revenue or segment_profit or geographic or product_portfolio:
        segment_detail = f"""【主力製品・商材の販売状況】
{product_portfolio if product_portfolio else "（データなし）"}

【セグメント別売上】
{segment_revenue if segment_revenue else "（データなし）"}

【セグメント別利益】
{segment_profit if segment_profit else "（データなし）"}

【地域別業績】
{geographic if geographic else "（データなし）"}"""
    
    # コスト・価格・為替情報
    cost_info = get_qa('cost_structure')
    price_info = get_qa('price_pass_through')
    forex_info = get_qa('forex_impact')
    inventory_info = get_qa('inventory_change')
    
    cost_section = f"""【コスト構造の変化（XBRL自動計算）】
{cost_structure_analysis}

【コスト構造の変化（PDF抽出）】
{cost_info if cost_info else "（PDF記載なし）"}

【価格転嫁の状況】
{price_info if price_info else "（データなし）"}

【為替の影響】
{forex_info if forex_info else "（データなし）"}

【在庫の状況】
{inventory_info if inventory_info else "（データなし）"}"""
    
    # 投資・財務情報
    capex_info = get_qa('capex')
    depreciation_info = get_qa('depreciation')
    rd_info = get_qa('rd_expense')
    financial_info = get_qa('financial_position')
    dividend_info = get_qa('dividend_shareholder')
    
    investment_section = ""
    if capex_info or rd_info or financial_info:
        investment_section = f"""【設備投資】
{capex_info if capex_info else "（データなし）"}

【減価償却】
{depreciation_info if depreciation_info else "（データなし）"}

【研究開発】
{rd_info if rd_info else "（データなし）"}

【財務体質】
{financial_info if financial_info else "（データなし）"}

【株主還元】
{dividend_info if dividend_info else "（データなし）"}"""
    
    # 戦略・見通し
    mid_term = get_qa('mid_term_plan')
    competitive = get_qa('competitive_advantage')
    outlook = get_qa('guidance_outlook')
    employee = get_qa('employee_change')

    # GS MD Level: 追加質問
    management_track = get_qa('management_track_record')
    market_position = get_qa('market_position')
    capital_allocation = get_qa('capital_allocation')

    strategy_section = ""
    if mid_term or competitive or outlook:
        strategy_section = f"""【中期経営計画】
{mid_term if mid_term else "（データなし）"}

【競争優位性】
{competitive if competitive else "（データなし）"}

【今後の見通し】
{outlook if outlook else "（データなし）"}

【従業員の状況】
{employee if employee else "（データなし）"}"""

    # GS MD Level: 投資家向け追加情報
    gs_md_section = ""
    if management_track or market_position or capital_allocation:
        gs_md_section = f"""【経営陣の実績・実行力】
{management_track if management_track else "（データなし）"}

【業界内競争ポジション】
{market_position if market_position else "（データなし）"}

【資本配分の優先順位】
{capital_allocation if capital_allocation else "（データなし）"}"""
    
    # 感応度分析
    forex_sensitivity = get_qa('forex_sensitivity')
    raw_material_sensitivity = get_qa('raw_material_sensitivity')

    prompt = f"""あなたは有価証券報告書の「詳細業績レポート」を書く編集者です。
以下の情報を基に、詳細で投資家が知りたい情報を網羅したレポートを作成してください。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【重要: 分析対象企業】
企業名: {company_name}
証券コード: {company_code}
対象年度: {year}年
業種: {industry_template['name']}

⚠️ この企業の情報のみを使用してください
⚠️ 他の企業（極洋、トヨタ自動車、ソニー、任天堂等）の情報を絶対に混入させないこと
⚠️ 提供された【スコアボード】【質問応答】【JSON抽出】のデータのみを使用すること
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【絶対ルール】
1. 数値は「スコアボード」のものをそのまま使う（変換禁止）
2. PDF由来の記述には (P.xx) タグ、XBRL由来には (XBRL) タグを付ける
3. 「買い」「売り」などの投資判断は書かない
4. 情報がない項目は「（データなし）」として簡潔に記載（「記載なし」は使わない）
5. 時系列推移テーブルはそのままコピーする
6. 【用語統一厳守】以下の用語を絶対に混同しない：
   - 現金及び現金同等物（Cash & Equivalents） ≠ ネットキャッシュ
   - 有利子負債（Interest-bearing Debt）
   - Net Debt = 有利子負債 － 現金及び現金同等物
7. 【前年差計算厳守】前年差は必ず時系列テーブルの数値から計算する
   - 例：自己資本比率が34.4%→32.3%なら「-2.1pt」
   - スコアボードに既に前年差が含まれている場合はそれを使う
8. 【矛盾禁止】レポート内で数値の矛盾を絶対に起こさない
9. 【推定禁止】「推定」「と推定」などの表現と「（データなし）」を併記してはいけない
   - 根拠がある場合: 「運転資本増加が主因 (XBRL)」
   - 根拠がない場合: 「（データなし）」
   - 禁止例: 「運転資本増加が主因と推定 (データなし)」
10. 【企業固有性厳守】{company_name}に直接関係のない事業内容や数値を記載しないこと
    - 禁止例: 水産業でない企業に「ホタテ輸出」を記載
    - 禁止例: ゲーム会社に「自動車部品」を記載

【スコアボード（XBRL）】※この数値をそのまま使う
{scoreboard}

【時系列推移（XBRL）】※このテーブルをそのままコピー
{historical_table}

【JSON抽出されたドライバー（PDF）】
{format_drivers(all_drivers, 10)}

【JSON抽出されたイベント（PDF）】
{format_facts(all_facts, 5)}

【JSON抽出されたリスク（PDF）】
{format_risks(all_risks, 5)}

【売上高の増減要因（質問応答）】
{get_qa('revenue_drivers') if get_qa('revenue_drivers') else "（分析データなし）"}

【利益の増減要因（質問応答）】
{get_qa('profit_drivers') if get_qa('profit_drivers') else "（分析データなし）"}

【セグメント別業績（質問応答）】
{get_qa('segment_performance') if get_qa('segment_performance') else "（分析データなし）"}

{segment_detail}

【コスト・価格・為替】
{cost_section if cost_section else "（分析データなし）"}

【今期の重要イベント（質問応答）】
{get_qa('key_events') if get_qa('key_events') else "（分析データなし）"}

【リスクと対応（質問応答）】
{get_qa('risks') if get_qa('risks') else "（分析データなし）"}

【投資・財務】
{investment_section if investment_section else "（分析データなし）"}

{operating_cf_analysis if operating_cf_analysis else ""}

【経営戦略・見通し】
{strategy_section if strategy_section else "（分析データなし）"}

【GS MD Level: 投資家向け追加分析】
{gs_md_section if gs_md_section else "（分析データなし）"}

【感応度分析】
【為替感応度】
{forex_sensitivity if forex_sensitivity else "（データなし）"}

【原材料価格感応度】
{raw_material_sensitivity if raw_material_sensitivity else "（データなし）"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【出力フォーマット】

# {company_name}｜{year}年 詳細業績レポート（v10）

## 数字で見る{year}年
（スコアボードの数値を箇条書きで。1株配当・配当性向も含める。全てに(XBRL)タグ）

## 📈 時系列推移（XBRL）
（時系列推移テーブルをそのままコピー）

## 今年起きたこと（トップ3）
（重要イベントから3つ選んで簡潔に。各項目に(P.xx)タグ。業績数字ではなく具体的なイベントを記載）

## 業績変化の理由

### 売上高 {rev_yoy}の要因
（売上高分析から主要な要因を記載。(P.xx)タグ）

### 営業利益 {op_yoy}の要因
（利益分析から主要な要因を記載。(P.xx)タグ）

### コスト構造の変化
（XBRL自動計算結果を必ず含める。粗利率・販管費率の前年差など。PDF抽出がある場合は追加。）

### 為替・価格転嫁の影響
（為替影響、値上げ状況を記載。データなければ「（データなし）」）

## セグメント別ハイライト
【🚨 重大な禁止事項 - 違反は即レポート不合格 🚨】
1. **全社売上高（{xbrl_revenue}）を全セグメントにコピーすることは絶対に禁止**
   - 違反例: 全セグメントで「売上高: 4,784億円」となっている → これは全社数値のコピー
   - 正しい例: 各セグメントは独自の売上高を持つ（国内酒類 2,736億円, 国際 705億円, 食品飲料 1,356億円 など）
2. **全社YoY（{rev_yoy}）を全セグメントにコピーすることは絶対に禁止**
   - 違反例: 全セグメントで「前年比 +1.8%」となっている → これは全社数値の流用
   - 正しい例: 各セグメントは独自の成長率を持つ（国内 -3%, 国際 +42%, 食品 +2% など）
3. セグメント数値は「セグメント別業績（質問応答）」から**各セグメント固有の数値**を取得する
4. セグメント別の数値が取得できない場合は「N/A」と記載（全社数値で埋めない）
5. 推定や穴埋めは行わない
6. 「好調」「不調」の判断は必ず根拠となる数値とページを併記する
7. (P.xx)タグは実際にその情報が記載されているページのみ使用

【検証チェック】生成後に自己チェックせよ:
- ❌ 全セグメントで同じ売上高金額になっていないか？ → 全社数値のコピーを疑え
- ❌ 全セグメントで同じYoY%になっていないか？ → 全社数値の流用を疑え
- ✅ 各セグメントが異なる売上高・YoY%を持っているか？ → 正しい抽出

【記載フォーマット】
- **セグメント名**:
  - 売上高: XX億円（+YY%） または N/A
  - 営業利益: XX億円（+YY%） または N/A
  - **評価**: 好調/不調/横ばい（判断根拠） (P.xx)
  - 要因: 具体的な理由 (P.xx)

## 投資・財務の状況

### 設備投資・研究開発
（設備投資額、R&D費、主な投資内容。(P.xx)タグ）

### 財務体質・キャッシュフロー
（自己資本比率、Net Debt/EBITDA、FCF推移など。(XBRL)タグ）
【重要】営業CFがマイナスで純利益がプラスの場合、運転資本増加の理由を必ず説明する
【財務の因果関係】
- 運転資本増加 = 売掛金・在庫が増える = 現金が流出する = 営業CFを悪化させる
- 「売上増→売掛金/在庫増→現金流出→営業CFマイナス」の流れで説明
- 誤った表現「現金の増加が営業CFを悪化」は絶対に使わない

### 株主還元
（配当金額、配当性向、自社株買いの状況。(P.xx)または(XBRL)タグ）

## 感応度分析

### 為替感応度
（為替変動の業績への影響。データなければ「記載なし」）

### 原材料価格感応度
（原材料価格変動の業績への影響。データなければ「記載なし」）

## 経営戦略・今後の見通し

### 中期経営計画のポイント
（数値目標、重点施策など。(P.xx)タグ）

### 来期の見通し
（経営者の業績予想、成長戦略。(P.xx)タグ）

## リスクと対応
（リスク→対応の形式で主要なものを記載。(P.xx)タグ）

## 🏦 投資家向け追加分析（GS MD Level）

### 経営陣の実績・実行力
（過去の中計達成率、主要経営判断の結果、ROE改善実績など。データがあれば記載。なければ「（データなし）」）

### 業界内競争ポジション
（市場シェア、競合との差別化、参入障壁など。データがあれば記載。なければ「（データなし）」）

### 資本配分の優先順位
（成長投資 vs 株主還元 vs 財務健全性のバランス。データがあれば記載。なければ「（データなし）」）

### 注目すべきポイント（Bulls/Bears）
【ポジティブ要因】
- 提供データから読み取れる強み・好材料を2-3点

【リスク・懸念材料】
- 提供データから読み取れるリスク・課題を2-3点

レポート:"""

    result = call_ollama(
        prompt,
        Config.OLLAMA_MODEL_FINAL,
        num_predict=12000,  # Increased for detailed content with product info
        num_ctx=32000,      # Increased context for more input data
        temperature=Config.FINAL_TEMPERATURE
    )
    
    report_text = result.get('response', 'レポート生成失敗')
    
    # 誤字修正
    typo_fixes = {
        '楥績': '業績',
        '楥約': '業績',
        '売り上げ高': '売上高',
    }
    for wrong, correct in typo_fixes.items():
        report_text = report_text.replace(wrong, correct)
    
    # 数値チェック
    number_check = check_numbers_against_xbrl(report_text, xbrl)

    if number_check['has_issues']:
        logger.warning(f"  ⚠️ 数値の不整合を検出: {len(number_check['issues'])}件")
        for issue in number_check['issues']:
            logger.warning(f"    - {issue['item']}: レポート={issue['report_value']}, XBRL={issue['xbrl_value']}")

        report_text = fix_numbers_in_report(report_text, xbrl)
        logger.info("  ✅ 数値を自動修正しました")
    else:
        logger.info("  ✅ 数値チェック: OK")

    # セグメントハルシネーション検出
    report_text, hallucination_detected = detect_and_warn_segment_hallucination(report_text, xbrl)
    if hallucination_detected:
        number_check['segment_hallucination_detected'] = True
        logger.warning("  🚨 セグメントハルシネーションを検出しました")

    return report_text, number_check


def detect_and_warn_segment_hallucination(report_text: str, xbrl: Dict) -> Tuple[str, bool]:
    """セグメント売上高・前年比が全社合計と同一のハルシネーションを検出して警告を追加"""
    hallucination_detected = False
    warnings = []

    company_revenue = xbrl.get('revenue')
    company_revenue_prev = xbrl.get('revenue_prev')

    # 全社YoY計算
    company_yoy = None
    if company_revenue and company_revenue_prev and company_revenue_prev > 0:
        company_yoy = (company_revenue - company_revenue_prev) / company_revenue_prev * 100

    # セグメント別ハイライトセクションを抽出
    segment_section_match = re.search(
        r'## セグメント別ハイライト(.*?)(?=## |---|\Z)',
        report_text,
        re.DOTALL
    )
    if not segment_section_match:
        return report_text, False

    segment_section = segment_section_match.group(1)

    # ==== パターン1: 売上高が全社合計と同一 ====
    if company_revenue:
        company_revenue_oku = company_revenue / 1e8

        # セグメント別の売上高を抽出
        segment_pattern = r'\*\*([^*]+(?:事業|セグメント|部門))\*\*:\s*\n-\s*売上高:\s*([\d,\.]+)億円'
        matches = re.findall(segment_pattern, segment_section)

        # フォールバック
        if len(matches) < 2:
            fallback_pattern = r'\n\*\*([^*\n]+)\*\*:\s*\n-\s*売上高:\s*([\d,\.]+)億円'
            matches = re.findall(fallback_pattern, segment_section)
            matches = [(name, rev) for name, rev in matches
                       if name.strip() not in ['評価', '要因', '概要', 'ハイライト', '詳細']]

        if len(matches) >= 2:
            hallucinated_segments = []
            for seg_name, seg_revenue_str in matches:
                try:
                    seg_revenue = float(seg_revenue_str.replace(',', ''))
                    if abs(seg_revenue - company_revenue_oku) / company_revenue_oku < 0.01:
                        hallucinated_segments.append(seg_name.strip())
                except ValueError:
                    continue

            if len(hallucinated_segments) >= 2:
                hallucination_detected = True
                warnings.append(f"""**問題1: 売上高の複製**
以下のセグメントの売上高が全社合計（{company_revenue_oku:,.1f}億円）と同一値です:
{chr(10).join(f'- {seg}' for seg in hallucinated_segments)}""")

    # ==== パターン2: 前年比（YoY）が全セグメントで同一 ====
    # 売上高の前年比を抽出: （前年比: -14.0%） or （+10.2%） or （-5%）
    yoy_pattern = r'売上高[^(]*[（\(](?:前年比)?[:\s]*([+\-]?\d+\.?\d*)[%％][）\)]'
    yoy_matches = re.findall(yoy_pattern, segment_section)

    if len(yoy_matches) >= 3:  # 3つ以上のセグメントがある場合
        try:
            yoy_values = [float(y) for y in yoy_matches]
            unique_yoys = set(yoy_values)

            # 全セグメントが同じYoYを持つ → ハルシネーションの可能性大
            if len(unique_yoys) == 1:
                single_yoy = list(unique_yoys)[0]

                # さらに、全社YoYと一致するか確認
                is_company_yoy_copy = False
                if company_yoy is not None and abs(single_yoy - company_yoy) < 0.5:
                    is_company_yoy_copy = True

                hallucination_detected = True
                if is_company_yoy_copy:
                    warnings.append(f"""**問題2: 前年比（YoY）の複製**
全{len(yoy_matches)}セグメントの前年比が同一（{single_yoy:+.1f}%）で、全社YoY（{company_yoy:+.1f}%）と一致しています。
各セグメントは通常異なる成長率を持つため、これはLLMによる全社数値のコピーです。""")
                else:
                    warnings.append(f"""**問題2: 前年比（YoY）の複製**
全{len(yoy_matches)}セグメントの前年比が同一（{single_yoy:+.1f}%）です。
各セグメントは通常異なる成長率を持つため、これは誤りの可能性が高いです。""")
        except ValueError:
            pass

    # 警告メッセージを挿入
    if warnings:
        warning_section = f"""

---

## 🚨 品質警告: セグメントデータのハルシネーション検出

{chr(10).join(warnings)}

**原因**: LLMがセグメント固有の値を抽出できず、全社値をコピーした可能性が高いです。

**対応**:
- 「業績変化の理由」セクションの記述に正しい前年比が記載されている場合があります
- 原本PDFの「セグメント情報」を直接参照してください

"""
        insertion_point = report_text.find('## 📊 Phase 1: 品質検証レポート')
        if insertion_point != -1:
            report_text = report_text[:insertion_point] + warning_section + report_text[insertion_point:]
        else:
            report_text += warning_section

    return report_text, hallucination_detected


# ============================================================
# ファイル検索
# ============================================================
def find_section_folder(company_code: str, year: str = None, doc_type: str = None) -> Optional[Path]:
    if not company_code:
        return None
    company_folders = list(Config.SECTIONS_BASE.glob(f"{company_code}_*"))
    if not company_folders:
        return None
    company_folder = company_folders[0]

    if year and doc_type:
        target = company_folder / f"{year}_{doc_type}"
        if target.exists():
            return target

    subfolders = sorted([f for f in company_folder.iterdir() if f.is_dir()], reverse=True)
    return subfolders[0] if subfolders else None


def find_xbrl_zip(company_code: str, year: str, doc_type: str) -> Optional[Path]:
    xbrl_folder = Config.XBRL_BASE / year / doc_type
    if not xbrl_folder.exists():
        return None
    zips = list(xbrl_folder.glob(f"{company_code}_*.zip"))
    return max(zips, key=lambda x: x.stat().st_size) if zips else None


# ============================================================
# 単一企業処理
# ============================================================
def process_single_company(company_code: str, company_name: str, year: str, doc_type: str,
                           industry: str, output_base: Path, rag_db: LocalRAGDB,
                           xbrl_path: Path = None) -> Dict:
    result = {
        'company_code': company_code,
        'company_name': company_name,
        'year': year,
        'doc_type': doc_type,
        'industry': industry,
        'status': 'pending',
        'xbrl_items': 0,
        'sections': 0,
        'error': None,
        'output_files': [],
        'processing_time': 0,
        'number_check': None,
    }

    start_time = time.time()

    try:
        print(f"  📁 セクションフォルダ検索...")
        section_folder = find_section_folder(company_code, year, doc_type)
        if not section_folder or not section_folder.exists():
            result['status'] = 'skipped'
            result['error'] = 'セクションフォルダなし'
            return result
        print(f"    {section_folder}")

        output_dir = output_base / f"{company_code}_{company_name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"  📊 XBRL読み込み...")
        xbrl = {}
        if xbrl_path and xbrl_path.exists():
            xbrl = extract_xbrl_from_zip(xbrl_path, industry=industry)
        else:
            found_xbrl = find_xbrl_zip(company_code, year, doc_type)
            if found_xbrl:
                xbrl = extract_xbrl_from_zip(found_xbrl, industry=industry)

        result['xbrl_items'] = len(xbrl)

        if xbrl:
            print(f"  📊 XBRL: {len(xbrl)}項目")
            print(f"    売上: {fmt_yen(xbrl.get('revenue'))}, 営業利益: {fmt_yen(xbrl.get('operating_income'))}")
            try:
                rag_db.add_xbrl(company_name, int(year), xbrl)
            except:
                pass
        else:
            print(f"  ⚠️ XBRLなし")

        # 過去データ読み込み
        historical_xbrl = load_historical_xbrl_from_store(company_code, company_name)
        if not historical_xbrl:
            historical_xbrl = rag_db.get_historical_xbrl(company_name, years=5)
            if historical_xbrl:
                logger.info(f"  📁 RAG DB: {len(historical_xbrl)}年度分")

        # historical_xbrlに現在年度があれば、それを使う
        current_year_int = int(year)
        if current_year_int in historical_xbrl:
            stored_xbrl = historical_xbrl[current_year_int]
            if stored_xbrl.get('roe_calc') is not None:
                xbrl = stored_xbrl
                logger.info(f"  📂 xbrl_store({current_year_int})から派生指標を取得")
        elif xbrl:
            historical_xbrl[current_year_int] = xbrl

        # 派生指標再計算
        if xbrl:
            _calculate_derived_metrics(xbrl)
            logger.info(f"  📊 派生指標: EBITDA={xbrl.get('ebitda_calc')}, FCF={xbrl.get('fcf_calc')}, ROIC={xbrl.get('roic_calc')}")
        
        for year_key, year_data in historical_xbrl.items():
            _calculate_derived_metrics(year_data)

        prev_year = current_year_int - 1
        prev_xbrl = historical_xbrl.get(prev_year, {})
        if prev_xbrl:
            logger.info(f"  📈 前年({prev_year})データあり")
        else:
            logger.warning(f"  ⚠️ 前年({prev_year})データなし")

        print(f"  📖 PDF読み込み中...")

        # ★ v10.3: manifest.json読み込み（質問ベース選択用）
        manifest = load_manifest(section_folder)

        section_pages = {}
        for section_key in SECTION_MAPPING.keys():
            # Yuho_splitter_v4形式: セクションフォルダ内の全PDFを読み込み
            section_dir = section_folder / section_key
            if section_dir.exists() and section_dir.is_dir():
                # フォルダ内の全PDFを番号順にソート
                pdf_files = sorted(section_dir.glob("*.pdf"))

                # ★ v10.3: 質問ベースPDF選択（manifest有効時のみ）
                if Config.ENABLE_PDF_FILTERING and manifest:
                    section_question = SECTION_MAPPING[section_key].get("extract_focus", "")

                    # セクション別にtop_kとmin_guaranteedを調整
                    if section_key == "05_セグメント":
                        # セグメントは詳細が必要なので多めに読む
                        top_k_val = 5
                        min_guaranteed_val = 2
                    elif section_key == "02_経営戦略_リスク":
                        # 経営戦略は多数のPDFを持つことが多い（例: 35個）
                        # GS MDレベルの質問（経営実績、市場シェア、資本配分）に対応するため増加
                        top_k_val = 8
                        min_guaranteed_val = 3
                    elif section_key == "03_MDA":
                        # MDAは業績分析の核心部分
                        top_k_val = 6
                        min_guaranteed_val = 2
                    else:
                        top_k_val = 3
                        min_guaranteed_val = 1

                    selected_paths = select_pdfs_by_question(
                        section_question,
                        manifest,
                        section_key,
                        top_k=top_k_val,
                        min_guaranteed=min_guaranteed_val
                    )

                    # パスからPDFファイルオブジェクトに変換
                    if selected_paths:
                        selected_pdfs = []
                        for rel_path in selected_paths:
                            pdf_file = section_folder / rel_path
                            if pdf_file.exists():
                                selected_pdfs.append(pdf_file)
                    else:
                        # Fallback: 最初の3個
                        selected_pdfs = pdf_files[:3] if len(pdf_files) >= 3 else pdf_files
                else:
                    selected_pdfs = pdf_files  # フィルタ無効の場合は全PDF読み込み

                if selected_pdfs:
                    all_pages = []
                    for pdf_file in selected_pdfs:
                        pages = extract_text_from_pdf_with_pages(pdf_file)
                        if pages:
                            # ページ番号をグローバルに再採番
                            offset = len(all_pages)
                            all_pages.extend([(p + offset, text) for p, text in pages])

                    if all_pages:
                        section_pages[section_key] = all_pages
                        total_chars = sum(len(t) for _, t in all_pages)
                        if Config.ENABLE_PDF_FILTERING:
                            method = "manifest" if manifest else "fallback"
                            print(f"    ✅ {section_key}: {len(selected_pdfs)}/{len(pdf_files)}ファイル選択 ({method}), {len(all_pages)}p, {total_chars:,}文字")
                        else:
                            print(f"    ✅ {section_key}: {len(pdf_files)}ファイル, {len(all_pages)}p, {total_chars:,}文字")
            else:
                # 旧形式（単一PDFファイル）との互換性維持
                pdf_path = section_folder / f"{section_key}.pdf"
                if pdf_path.exists():
                    pages = extract_text_from_pdf_with_pages(pdf_path)
                    if pages:
                        section_pages[section_key] = pages
                        total_chars = sum(len(t) for _, t in pages)
                        print(f"    ✅ {section_key}: {len(pages)}p, {total_chars:,}文字")

        result['sections'] = len(section_pages)

        if not section_pages:
            result['status'] = 'skipped'
            result['error'] = 'PDFなし'
            return result

        print(f"  🔍 分析中（モード: {Config.EXTRACTION_MODE}）...")
        
        global _current_extraction_logger
        if Config.SAVE_EXTRACTION_LOGS:
            _current_extraction_logger = ExtractionLogger(output_dir, company_code, year)
        
        section_extracts = []
        for i, (section_key, pages) in enumerate(section_pages.items()):
            sec_start = time.time()
            print(f"    [{i+1}/{len(section_pages)}] {section_key}...", end=" ", flush=True)

            # 抽出モードに応じて処理を分岐（v10.1: 企業コンテキスト追加）
            if Config.EXTRACTION_MODE == "json":
                extracted = process_section_json_extraction(pages, section_key, xbrl, prev_xbrl, industry)
            elif Config.EXTRACTION_MODE == "qa":
                extracted = process_section_qa_mode(pages, section_key, xbrl, prev_xbrl, industry, company_name, company_code)
            else:  # hybrid
                extracted = process_section_hybrid(pages, section_key, xbrl, prev_xbrl, industry, company_name, company_code)
            
            section_extracts.append(extracted)

            mode = extracted.get('mode', 'unknown')
            ext = extracted.get('extracted', {})
            qa_count = len(extracted.get('qa_answers', []))
            d_count = len(ext.get('drivers', []))
            f_count = len(ext.get('facts', []))
            print(f"✅ mode={mode}, D:{d_count} F:{f_count} QA:{qa_count} ({time.time()-sec_start:.1f}秒)")

        # Phase 1: Validation Framework
        print(f"  🔍 Phase 1: 品質検証中...")
        validation_start = time.time()

        # Merge all section extracts into a unified structure for validation
        merged_for_validation = {
            "company_overview": {},
            "financial_highlights": {},
            "business_risks": [],
            "md_and_a": {},
            "segment_analysis": {"segments": []},
            "esg_governance": {},
            "notes": {}
        }

        # Extract data from section_extracts
        for sec_extract in section_extracts:
            section_key = sec_extract.get("section_key", "")
            extracted = sec_extract.get("extracted", {})

            if section_key == "01_会社概要":
                merged_for_validation["company_overview"] = extracted
            elif section_key == "02_経営戦略_リスク":
                risks = extracted.get("risks", [])
                merged_for_validation["business_risks"].extend(risks)
            elif section_key == "03_MDA":
                merged_for_validation["md_and_a"] = {
                    "revenue_drivers": extracted.get("drivers", []),
                    "profit_drivers": extracted.get("drivers", []),
                }
            elif section_key == "05_セグメント":
                # Extract segment data from QA answers (segment_revenue, segment_profit)
                qa_answers = sec_extract.get("qa_answers", [])

                segment_revenue_data = None
                segment_profit_data = None

                for qa in qa_answers:
                    if qa.get("question_id") == "segment_revenue":
                        segment_revenue_data = qa.get("answer", "")
                    elif qa.get("question_id") == "segment_profit":
                        segment_profit_data = qa.get("answer", "")

                # Parse segment revenue data (simple regex extraction)
                if segment_revenue_data:
                    import re
                    # Pattern: **セグメント名:** 数値億円 (前年比 +X.X%)
                    pattern = r'\*\*(.+?):\*\*\s*([\d,]+)億円\s*\(前年比\s*([+\-][\d.]+)%\)'
                    matches = re.findall(pattern, segment_revenue_data)

                    for seg_name, revenue_str, yoy_str in matches:
                        seg_name = seg_name.strip()
                        try:
                            revenue = float(revenue_str.replace(',', '')) * 1e8  # Convert 億円 to 円
                            yoy_pct = float(yoy_str)

                            merged_for_validation["segment_analysis"]["segments"].append({
                                "segment_name": seg_name,
                                "revenue": revenue,
                                "revenue_yoy_pct": yoy_pct,
                            })
                        except ValueError:
                            pass  # Skip if parsing fails
            elif section_key == "06_ガバナンス":
                merged_for_validation["esg_governance"] = extracted
            elif section_key == "07_その他":
                merged_for_validation["notes"] = extracted

        # Add XBRL financial highlights
        if xbrl:
            merged_for_validation["financial_highlights"] = {
                "revenue": {
                    "current": xbrl.get("revenue"),
                    "previous": prev_xbrl.get("revenue") if prev_xbrl else None,
                    "yoy_pct": ((xbrl.get("revenue") - prev_xbrl.get("revenue")) / prev_xbrl.get("revenue") * 100) if (xbrl.get("revenue") and prev_xbrl and prev_xbrl.get("revenue")) else None,
                    "source": "XBRL"
                },
                "operating_profit": {
                    "current": xbrl.get("operating_income"),
                    "previous": prev_xbrl.get("operating_income") if prev_xbrl else None,
                    "yoy_pct": ((xbrl.get("operating_income") - prev_xbrl.get("operating_income")) / prev_xbrl.get("operating_income") * 100) if (xbrl.get("operating_income") and prev_xbrl and prev_xbrl.get("operating_income")) else None,
                    "source": "XBRL"
                },
                "net_profit": {
                    "current": xbrl.get("net_income"),
                    "previous": prev_xbrl.get("net_income") if prev_xbrl else None,
                    "yoy_pct": ((xbrl.get("net_income") - prev_xbrl.get("net_income")) / prev_xbrl.get("net_income") * 100) if (xbrl.get("net_income") and prev_xbrl and prev_xbrl.get("net_income")) else None,
                    "source": "XBRL"
                },
                "total_assets": {
                    "current": xbrl.get("total_assets"),
                    "previous": prev_xbrl.get("total_assets") if prev_xbrl else None,
                },
                "total_equity": {
                    "current": xbrl.get("total_equity"),
                    "previous": prev_xbrl.get("total_equity") if prev_xbrl else None,
                },
                "operating_cash_flow": {
                    "current": xbrl.get("operating_cf"),
                    "previous": prev_xbrl.get("operating_cf") if prev_xbrl else None,
                },
            }

        # Run Phase 1 validation
        validation_results, confidence_score = run_phase1_validation(
            merged_for_validation,
            xbrl_data=xbrl,
            historical_data=None  # Could be added later if historical analysis is needed
        )

        print(f"    ✅ 完了: Confidence={confidence_score.overall_score:.1f}% ({confidence_score.confidence_level.value}) ({time.time()-validation_start:.1f}秒)")

        print(f"  📝 最終レポート生成中...")
        report_start = time.time()
        final_report, number_check = generate_final_report_v10(
            company_name, company_code, int(year),
            xbrl, prev_xbrl, section_extracts, industry, historical_xbrl
        )
        print(f"    ✅ 完了 ({time.time()-report_start:.1f}秒)")

        result['number_check'] = number_check

        print(f"  💾 ファイル保存中...")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        result_data = {
            'company_code': company_code,
            'company_name': company_name,
            'year': year,
            'doc_type': doc_type,
            'industry': industry,
            'xbrl': xbrl,
            'historical_xbrl': historical_xbrl,
            'section_extracts': section_extracts,
            'final_report': final_report,
            'number_check': number_check,
            'phase1_validation': {
                'validation_results': [
                    {
                        'layer': r.layer.value,
                        'passed': r.passed,
                        'issues': r.issues,
                        'warnings': r.warnings,
                        'details': r.details
                    }
                    for r in validation_results
                ],
                'confidence_score': {
                    'completeness_score': confidence_score.completeness_score,
                    'xbrl_coverage_score': confidence_score.xbrl_coverage_score,
                    'validation_score': confidence_score.validation_score,
                    'citation_score': confidence_score.citation_score,
                    'overall_score': confidence_score.overall_score,
                    'confidence_level': confidence_score.confidence_level.value
                }
            },
            'model': Config.OLLAMA_MODEL,
            'final_model': Config.OLLAMA_MODEL_FINAL,
            'extraction_mode': Config.EXTRACTION_MODE,
            'version': 'v10',
        }

        json_path = output_dir / f"porta10_{company_code}_{year}_{doc_type}_{timestamp}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)

        md_path = output_dir / f"porta10_{company_code}_{year}_{doc_type}_{timestamp}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {company_name} 業績レポート（v10）\n\n")
            f.write(f"**年度**: {year} / **種別**: {doc_type} / **業種**: {industry}\n\n")
            f.write(f"**生成モデル**: {Config.OLLAMA_MODEL_FINAL} / **抽出モード**: {Config.EXTRACTION_MODE}\n\n")
            f.write("---\n\n")
            f.write(final_report)

            # Phase 1 Quality Report
            phase1_report = generate_phase1_quality_report(validation_results, confidence_score)
            f.write(phase1_report)

            if number_check:
                f.write("\n\n---\n\n")
                f.write("## 📊 数値整合性チェック\n\n")

                # 全社KPIチェック
                company_checks = number_check.get('company_wide_checks', [])
                if company_checks:
                    f.write("### 全社KPI整合性\n\n")
                    f.write("| 項目 | XBRL（連結） | レポート | 一致 |\n")
                    f.write("|------|-------------|----------|------|\n")
                    for c in company_checks:
                        status = "✅" if c.get('match') else "❌"
                        f.write(f"| {c['item']} | {c['xbrl_value']} | {c['report_value']} | {status} |\n")
                else:
                    f.write("### 全社KPI整合性\n\nチェック対象の数値なし\n")

                # セグメント合計チェック
                segment_summary = number_check.get('segment_summary', {})
                if segment_summary:
                    f.write("\n### セグメント合計整合性\n\n")
                    f.write("| 項目 | セグメント合計 | 全社（連結） | 差分 | 一致 |\n")
                    f.write("|------|---------------|-------------|------|------|\n")
                    for item_name, item_data in segment_summary.items():
                        label = '売上高' if item_name == 'revenue' else '営業利益'
                        seg_total = fmt_yen(item_data['segment_total'])
                        co_total = fmt_yen(item_data['company_total'])
                        diff_pct = f"{item_data['diff_pct']:.1f}%"
                        status = "✅" if item_data['match'] else "⚠️"
                        f.write(f"| {label} | {seg_total} | {co_total} | {diff_pct} | {status} |\n")
                    f.write("\n*差分は全社調整・セグメント間取引消去等による\n")

        result['output_files'] = [str(json_path), str(md_path)]
        result['status'] = 'success'

    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
        logger.error(f"処理エラー ({company_code}): {e}")
        import traceback
        traceback.print_exc()

    finally:
        _current_extraction_logger = None

    result['processing_time'] = time.time() - start_time
    return result


# ============================================================
# 企業コンテキストのクリーンアップ
# ============================================================
def cleanup_company_context():
    """
    企業処理後のクリーンアップ

    複数企業を連続処理する際、前の企業のデータが混入しないよう
    グローバル状態をクリアします。
    """
    global _current_company_context, _current_extraction_logger

    # PDFキャッシュ用の企業コンテキストをクリア
    _current_company_context = None

    # ExtractionLoggerをクリア（念のため）
    _current_extraction_logger = None

    logger.debug("  🧹 企業コンテキストをクリア")


# ============================================================
# 一括処理（v10.1: 再開機能対応）
# ============================================================
def process_multiple_companies(companies: List[Company], year: str, doc_type: str,
                               output_base: Path, rag_db: LocalRAGDB,
                               force_industry: str = None,
                               force_restart: bool = False) -> List[Dict]:
    """
    複数企業を一括処理（再開機能対応）

    Args:
        force_restart: True の場合、進捗をリセットして最初から開始
    """
    results = []
    original_total = len(companies)

    # ★ v10.1: 進捗チェック＆再開
    remaining_companies, skipped_count = progress_tracker.start_batch(
        year, doc_type, companies, force_restart=force_restart
    )

    if skipped_count > 0:
        print(f"\n{'='*60}")
        print(f"🔄 前回の続きから再開: {skipped_count}社完了済み → 残り{len(remaining_companies)}社")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"🚀 一括処理開始: {original_total}社（v10.1 / モード: {Config.EXTRACTION_MODE}）")
        print(f"{'='*60}")

    if not remaining_companies:
        print("✅ 全ての企業が処理済みです")
        return results

    total = len(remaining_companies)
    processed_in_session = 0

    for i, company in enumerate(remaining_companies):
        current_num = skipped_count + i + 1
        print(f"\n[{current_num}/{original_total}] {company.code} {company.name}")
        print("-" * 40)

        # ★追加: 企業コンテキストを設定
        global _current_company_context
        _current_company_context = f"{company.code}_{year}"

        industry = force_industry if force_industry else company.industry

        try:
            result = process_single_company(
                company_code=company.code,
                company_name=company.name,
                year=year,
                doc_type=doc_type,
                industry=industry,
                output_base=output_base,
                rag_db=rag_db
            )

            results.append(result)
            processed_in_session += 1

            status_icon = "✅" if result['status'] == 'success' else "⏭️" if result['status'] == 'skipped' else "❌"
            print(f"  {status_icon} {result['status']}: {result['processing_time']:.1f}秒")

            # ★ v10.1: 進捗を即座に保存（PCシャットダウン対策）
            progress_tracker.mark_completed(year, doc_type, company.code, result['status'], result)

        except KeyboardInterrupt:
            print(f"\n\n⚠️ 中断されました（{processed_in_session}社処理済み）")
            print(f"   次回実行時に {company.code} から再開できます")
            raise

        except Exception as e:
            logger.error(f"企業処理エラー: {company.code} - {e}")
            progress_tracker.mark_completed(year, doc_type, company.code, "error")

        # ★追加: 企業処理後のクリーンアップ
        cleanup_company_context()

    # バッチ完了を記録
    progress_tracker.finish_batch(year, doc_type)

    return results


def print_summary(results: List[Dict], total_time: float):
    print(f"\n{'='*60}")
    print("📊 処理結果サマリー")
    print(f"{'='*60}")

    success = [r for r in results if r['status'] == 'success']
    skipped = [r for r in results if r['status'] == 'skipped']
    errors = [r for r in results if r['status'] == 'error']

    print(f"  ✅ 成功: {len(success)}社")
    print(f"  ⏭️ スキップ: {len(skipped)}社")
    print(f"  ❌ エラー: {len(errors)}社")
    print(f"  ⏱️ 合計時間: {total_time:.1f}秒")


# ============================================================
# インタラクティブメニュー（完全版 + v10.1再開機能）
# ============================================================
def interactive_menu():
    print("\n" + "=" * 60)
    print("🌳 PORTA v10.1 - 完全統合版（再開機能対応）")
    print("=" * 60)

    print("\n📊 XBRLタグ定義読み込み中...")
    tag_manager.load()
    print(f"  読み込み元: {tag_manager.loaded_from}")

    print("\n📋 企業リスト読み込み中...")
    all_companies = load_companies_from_sheets() or load_companies_from_sections()
    available_companies = get_available_companies_with_sections()
    print(f"  全企業: {len(all_companies)}社, セクションあり: {len(available_companies)}社")
    print(f"  使用モデル: {Config.OLLAMA_MODEL} / {Config.OLLAMA_MODEL_FINAL}")
    print(f"  抽出モード: {Config.EXTRACTION_MODE}")

    # ★ v10.1: 未完了バッチがあるかチェック
    incomplete = progress_tracker.has_incomplete_batch()
    if incomplete:
        year, doc_type, batch = incomplete
        completed = len(batch.get("completed", []))
        total = batch.get("total_companies", "?")
        print(f"\n⚠️ 未完了のバッチがあります: {year}年 {doc_type} ({completed}/{total}社完了)")

    # 日経225コード読み込み
    nikkei_codes = load_nikkei225_codes()
    nikkei_count = len([c for c in available_companies if c.code in nikkei_codes])
    print(f"  日経225: {nikkei_count}社（セクションあり）")

    while True:
        print("\n" + "-" * 40)
        print("【メインメニュー】")
        print("-" * 40)
        print("  1. 単一企業を処理")
        print("  2. 複数企業を選択して処理")
        print("  3. テスト実行（3社）")
        print("  4. モデル変更")
        print("  5. 抽出モード変更")
        print("  6. 抽出ログ設定")
        print("  7. 🔄 進捗状況/再開/クリア")
        print("  8. 🚀 全企業一括処理")
        print("  9. 📈 日経225優先処理")
        print("  0. 終了")

        choice = input("\n選択 [0-9]: ").strip()

        if choice == '0':
            print("\n👋 終了します")
            return 0

        elif choice == '1':
            menu_single_company(all_companies, available_companies)

        elif choice == '2':
            menu_multiple_companies(all_companies, available_companies)

        elif choice == '3':
            menu_test_companies(available_companies, 3)

        elif choice == '4':
            print(f"\n現在のモデル:")
            print(f"  1. 抽出用: {Config.OLLAMA_MODEL}")
            print(f"  2. 最終レポート用: {Config.OLLAMA_MODEL_FINAL}")
            print("\n変更対象:")
            print("  1. 抽出用モデル")
            print("  2. 最終レポート用モデル")
            print("  3. 両方同じモデルに")
            target = input("選択 [1-3]: ").strip()
            
            print("\nモデル選択:")
            print("  1. gemma2:9b (軽量・高速)")
            print("  2. gemma2:27b (高品質)")
            print("  3. qwen3:14b (日本語強い)")
            print("  4. qwen3:30b (最高品質・遅い)")
            m = input("選択 [1-4]: ").strip()
            model_map = {'1': 'gemma2:9b', '2': 'gemma2:27b', '3': 'qwen3:14b', '4': 'qwen3:30b'}
            
            if m in model_map:
                new_model = model_map[m]
                if target == '1':
                    Config.OLLAMA_MODEL = new_model
                    print(f"  → 抽出用: {new_model}")
                elif target == '2':
                    Config.OLLAMA_MODEL_FINAL = new_model
                    print(f"  → 最終レポート用: {new_model}")
                else:
                    Config.OLLAMA_MODEL = new_model
                    Config.OLLAMA_MODEL_FINAL = new_model
                    print(f"  → 両方: {new_model}")

        elif choice == '5':
            print(f"\n現在の抽出モード: {Config.EXTRACTION_MODE}")
            print("\n選択:")
            print("  1. json (v9.5.11方式: キーワード抽出→JSON)")
            print("  2. qa (v9.6.1方式: 質問応答)")
            print("  3. hybrid (v10方式: 両方併用)")
            m = input("選択 [1-3]: ").strip()
            mode_map = {'1': 'json', '2': 'qa', '3': 'hybrid'}
            if m in mode_map:
                Config.EXTRACTION_MODE = mode_map[m]
                print(f"  → {Config.EXTRACTION_MODE}")

        elif choice == '6':
            print(f"\n現在の設定:")
            print(f"  SAVE_EXTRACTION_LOGS: {Config.SAVE_EXTRACTION_LOGS}")
            print(f"  LOG_RAW_TEXT: {Config.LOG_RAW_TEXT}")
            print(f"  LOG_CHUNKS: {Config.LOG_CHUNKS}")
            print(f"  LOG_LLM_RESPONSE: {Config.LOG_LLM_RESPONSE}")
            toggle = input("SAVE_EXTRACTION_LOGS切り替え？ [y/n]: ").strip().lower()
            if toggle == 'y':
                Config.SAVE_EXTRACTION_LOGS = not Config.SAVE_EXTRACTION_LOGS
                print(f"  → {Config.SAVE_EXTRACTION_LOGS}")

        elif choice == '7':
            resume_info = menu_progress_management()
            if resume_info:
                year, doc_type, force_restart = resume_info
                # ★ 保存された対象企業コードを使用して再開
                batch_status = progress_tracker.get_batch_status(year, doc_type)
                target_codes = batch_status.get("target_codes", [])

                if target_codes:
                    # 保存されたコードから企業リストを再構築
                    all_available = get_available_companies_with_sections(year, doc_type)
                    code_to_company = {c.code: c for c in all_available}
                    targets = [code_to_company[code] for code in target_codes if code in code_to_company]
                    print(f"📋 保存された対象企業: {len(targets)}社")
                else:
                    # 旧形式（target_codesなし）：total_companiesから推測
                    total = batch_status.get("total_companies", 0)
                    all_available = get_available_companies_with_sections(year, doc_type)
                    if total <= 230:  # おそらく日経225
                        targets = [c for c in all_available if c.code in nikkei_codes]
                        print(f"📋 日経225と推測: {len(targets)}社")
                    else:
                        targets = all_available
                        print(f"📋 全企業: {len(targets)}社")

                if targets:
                    execute_full_batch(targets, year, doc_type, force_restart=force_restart)
                else:
                    print(f"❌ {year}年 {doc_type} の対象企業が見つかりません")

        elif choice == '8':
            menu_full_batch_processing(all_companies, available_companies)

        elif choice == '9':
            menu_nikkei225_processing(available_companies, nikkei_codes)

    return 0


def menu_progress_management():
    """★ v10.1: 進捗管理メニュー"""
    print("\n" + "-" * 40)
    print("【進捗管理】")
    print("-" * 40)

    # 現在の進捗を表示
    all_progress = progress_tracker.get_batch_status()

    if not all_progress:
        print("  進捗データがありません")
    else:
        print("\n📊 バッチ処理の進捗:")
        for batch_key, batch in all_progress.items():
            completed = len(batch.get("completed", []))
            skipped = len(batch.get("skipped", []))
            errors = len(batch.get("errors", []))
            total = batch.get("total_companies", "?")
            status = batch.get("status", "unknown")
            status_icon = "✅" if status == "completed" else "🔄" if status == "running" else "❓"

            print(f"\n  {status_icon} {batch_key}")
            print(f"     完了: {completed}/{total}, スキップ: {skipped}, エラー: {errors}")
            print(f"     開始: {batch.get('started_at', '?')[:19]}")
            if batch.get("last_processed"):
                print(f"     最終処理: {batch.get('last_processed')} ({batch.get('last_processed_at', '')[:19]})")

    print("\n操作:")
    print("  1. 未完了バッチを再開")
    print("  2. 特定バッチの進捗をクリア")
    print("  3. 全ての進捗をクリア")
    print("  0. 戻る")

    sub_choice = input("\n選択 [0-3]: ").strip()

    if sub_choice == '1':
        incomplete = progress_tracker.has_incomplete_batch()
        if incomplete:
            year, doc_type, batch = incomplete
            print(f"\n🔄 {year}年 {doc_type} を再開します...")
            # 再開処理は menu_full_batch_processing で行う
            return (year, doc_type, False)  # force_restart=False
        else:
            print("未完了のバッチはありません")

    elif sub_choice == '2':
        if all_progress:
            print("\nクリアするバッチを選択:")
            keys = list(all_progress.keys())
            for i, key in enumerate(keys):
                print(f"  {i+1}. {key}")
            idx = input("番号: ").strip()
            try:
                selected_key = keys[int(idx) - 1]
                parts = selected_key.split("_", 1)
                if len(parts) == 2:
                    progress_tracker.clear_batch(parts[0], parts[1])
                    print(f"  ✅ {selected_key} の進捗をクリアしました")
            except:
                print("  ❌ 無効な選択")

    elif sub_choice == '3':
        confirm = input("全ての進捗をクリアしますか？ [y/n]: ").strip().lower()
        if confirm == 'y':
            progress_tracker.clear_all()
            print("  ✅ 全ての進捗をクリアしました")


def menu_full_batch_processing(all_companies: List[Company], available_companies: List[Company]):
    """★ v10.1: 全企業一括処理メニュー"""
    print("\n" + "-" * 40)
    print("【全企業一括処理】")
    print("-" * 40)

    # 未完了バッチがあるかチェック
    incomplete = progress_tracker.has_incomplete_batch()
    if incomplete:
        year, doc_type, batch = incomplete
        completed = len(batch.get("completed", []))
        total = batch.get("total_companies", "?")
        print(f"\n⚠️ 未完了のバッチがあります: {year}年 {doc_type}")
        print(f"   進捗: {completed}/{total}社完了")
        print("\n選択:")
        print("  1. 続きから再開")
        print("  2. 最初からやり直す")
        print("  3. 別の年度/種別を処理")
        print("  0. 戻る")

        sub_choice = input("\n選択 [0-3]: ").strip()

        if sub_choice == '0':
            return
        elif sub_choice == '1':
            # 続きから再開
            execute_full_batch(available_companies, year, doc_type, force_restart=False)
            return
        elif sub_choice == '2':
            # 最初からやり直す
            execute_full_batch(available_companies, year, doc_type, force_restart=True)
            return
        elif sub_choice == '3':
            pass  # 新規バッチ選択へ

    # 新規バッチ設定
    year, doc_type = select_year_and_type()
    if year == 'back':
        return

    # 対象企業数を表示
    targets = get_available_companies_with_sections(year, doc_type)
    print(f"\n📋 対象企業: {len(targets)}社")

    # 既存の進捗があるかチェック
    existing = progress_tracker.get_batch_status(year, doc_type)
    if existing:
        completed = len(existing.get("completed", []))
        print(f"   (既に{completed}社完了済み)")

        print("\n選択:")
        print("  1. 続きから再開")
        print("  2. 最初からやり直す")
        print("  0. 戻る")

        sub_choice = input("\n選択 [0-2]: ").strip()
        if sub_choice == '0':
            return
        elif sub_choice == '1':
            execute_full_batch(targets, year, doc_type, force_restart=False)
        elif sub_choice == '2':
            execute_full_batch(targets, year, doc_type, force_restart=True)
    else:
        confirm = input(f"\n{len(targets)}社を処理します。開始しますか？ [y/n]: ").strip().lower()
        if confirm == 'y':
            execute_full_batch(targets, year, doc_type, force_restart=True)


def execute_full_batch(companies: List[Company], year: str, doc_type: str,
                       force_restart: bool = False):
    """全企業一括処理を実行"""
    output_base = Path("./output_v10")
    output_base.mkdir(parents=True, exist_ok=True)
    rag_db = LocalRAGDB("./rag_db")

    total_start = time.time()

    try:
        results = process_multiple_companies(
            companies=companies,
            year=year,
            doc_type=doc_type,
            output_base=output_base,
            rag_db=rag_db,
            force_industry="all",
            force_restart=force_restart
        )
        print_summary(results, time.time() - total_start)

    except KeyboardInterrupt:
        print("\n\n⚠️ 処理が中断されました")
        print("   次回実行時に「7. 進捗状況/再開/クリア」から再開できます")

    input("\nEnterで続行...")


def menu_nikkei225_processing(available_companies: List[Company], nikkei_codes: set):
    """★ v10.1: 日経225優先処理メニュー"""
    print("\n" + "-" * 40)
    print("【日経225優先処理】")
    print("-" * 40)

    # 日経225企業を抽出
    nikkei_companies = [c for c in available_companies if c.code in nikkei_codes]
    other_companies = [c for c in available_companies if c.code not in nikkei_codes]

    print(f"\n📊 対象企業:")
    print(f"  日経225: {len(nikkei_companies)}社")
    print(f"  その他: {len(other_companies)}社")
    print(f"  合計: {len(available_companies)}社")

    print("\n処理モード:")
    print("  1. 日経225のみ処理")
    print("  2. 日経225を先に、その後全企業")
    print("  3. 日経225テスト（10社）")
    print("  0. 戻る")

    sub_choice = input("\n選択 [0-3]: ").strip()

    if sub_choice == '0':
        return

    year, doc_type = select_year_and_type()
    if year == 'back':
        return

    if sub_choice == '1':
        # 日経225のみ
        targets = [c for c in get_available_companies_with_sections(year, doc_type)
                   if c.code in nikkei_codes]
        print(f"\n📈 日経225のみ: {len(targets)}社")

    elif sub_choice == '2':
        # 日経225優先（全企業）
        all_targets = get_available_companies_with_sections(year, doc_type)
        targets = sort_companies_nikkei_first(all_targets, nikkei_codes)
        nikkei_in_targets = len([c for c in targets if c.code in nikkei_codes])
        print(f"\n📈 日経225優先: 日経225 {nikkei_in_targets}社 → その他 {len(targets) - nikkei_in_targets}社")

    elif sub_choice == '3':
        # 日経225テスト（10社）
        targets = [c for c in get_available_companies_with_sections(year, doc_type)
                   if c.code in nikkei_codes][:10]
        print(f"\n📈 日経225テスト: {len(targets)}社")

    else:
        return

    # 既存の進捗確認
    existing = progress_tracker.get_batch_status(year, doc_type)
    if existing and existing.get("status") == "running":
        completed = len(existing.get("completed", []))
        print(f"\n⚠️ 既存の進捗あり: {completed}社完了済み")
        print("  1. 続きから再開")
        print("  2. 最初からやり直す")
        resume_choice = input("\n選択 [1-2]: ").strip()
        force_restart = (resume_choice == '2')
    else:
        confirm = input(f"\n{len(targets)}社を処理します。開始しますか？ [y/n]: ").strip().lower()
        if confirm != 'y':
            return
        force_restart = True

    execute_full_batch(targets, year, doc_type, force_restart=force_restart)


def menu_single_company(all_companies: List[Company], available_companies: List[Company]):
    print("\n【単一企業処理】")
    query = input("企業コードまたは名前（戻る: b）: ").strip()
    if query.lower() == 'b':
        return

    matches = search_companies(all_companies, query)
    if not matches:
        print("❌ 見つかりません")
        return

    if len(matches) == 1:
        selected = matches[0]
    else:
        print(f"\n{len(matches)}件:")
        for i, c in enumerate(matches[:20]):
            print(f"  {i+1}. {c.code} {c.name}")
        idx = input("番号: ").strip()
        try:
            selected = matches[int(idx) - 1]
        except:
            return

    year, doc_type = select_year_and_type()
    if year == 'back':
        return

    industry = select_industry(selected.industry)
    execute_processing([selected], year, doc_type, industry)


def menu_multiple_companies(all_companies: List[Company], available_companies: List[Company]):
    print("\n【複数企業処理】")
    query = input("企業コード（カンマ区切り）: ").strip()
    if not query:
        return

    codes = [c.strip() for c in query.split(',')]
    company_dict = {c.code: c for c in all_companies}
    selected = [company_dict[code] for code in codes if code in company_dict]

    if not selected:
        return

    year, doc_type = select_year_and_type()
    if year == 'back':
        return

    industry = select_industry("all")
    execute_processing(selected, year, doc_type, industry)


def menu_test_companies(available_companies: List[Company], n: int):
    print(f"\n【テスト実行: {n}社】")

    year, doc_type = select_year_and_type()
    if year == 'back':
        return

    filtered = get_available_companies_with_sections(year, doc_type)
    targets = filtered[:n]

    if not targets:
        print("❌ 処理可能な企業がありません")
        return

    print(f"\n処理対象:")
    for c in targets:
        print(f"  {c.code} {c.name} ({c.industry})")

    execute_processing(targets, year, doc_type, "all")


def select_year_and_type() -> Tuple[str, str]:
    year = input("\n年度（デフォルト: 2022）: ").strip() or '2022'
    if year.lower() == 'b':
        return 'back', ''

    print("書類種別（1: 有報, 2: 四半期）")
    doc = input("種別 [1/2]: ").strip()
    doc_type = '四半期' if doc == '2' else '有報'

    return year, doc_type


def select_industry(default: str) -> str:
    print(f"\n業種（デフォルト: {default}）:")
    print("  1. all  2. food  3. manufacturing  4. retail  5. it  6. finance")
    choice = input("選択 [1-6]: ").strip()
    industries = {'1': 'all', '2': 'food', '3': 'manufacturing', '4': 'retail', '5': 'it', '6': 'finance'}
    return industries.get(choice, default)


def execute_processing(companies: List[Company], year: str, doc_type: str, industry: str):
    total_start = time.time()

    output_base = Path("./output_v10")
    output_base.mkdir(parents=True, exist_ok=True)
    rag_db = LocalRAGDB("./rag_db")

    if len(companies) == 1:
        company = companies[0]
        print(f"\n{'='*60}")
        print(f"🚀 処理開始: {company.code} {company.name}")
        print(f"   モデル: {Config.OLLAMA_MODEL} / 抽出モード: {Config.EXTRACTION_MODE}")
        print(f"{'='*60}")

        result = process_single_company(
            company_code=company.code,
            company_name=company.name,
            year=year,
            doc_type=doc_type,
            industry=industry,
            output_base=output_base,
            rag_db=rag_db
        )

        print(f"\n{'='*60}")
        print("🎉 完了！")
        print(f"{'='*60}")
        print(f"  ステータス: {result['status']}")
        print(f"  XBRL: {result['xbrl_items']}項目, セクション: {result['sections']}個")
        print(f"  処理時間: {result['processing_time']:.1f}秒")
        
        if result.get('number_check'):
            nc = result['number_check']
            if nc.get('has_issues'):
                print(f"  ⚠️ 数値チェック: {len(nc['issues'])}件の修正あり")
            else:
                print(f"  ✅ 数値チェック: OK")
        
        print(f"{'='*60}")

    else:
        results = process_multiple_companies(
            companies=companies,
            year=year,
            doc_type=doc_type,
            output_base=output_base,
            rag_db=rag_db,
            force_industry=industry
        )
        print_summary(results, time.time() - total_start)

    input("\nEnterで続行...")


# ============================================================
# メイン
# ============================================================
def main():
    if len(sys.argv) == 1:
        return interactive_menu()

    parser = argparse.ArgumentParser(description='PORTA v10 - 完全統合版')
    parser.add_argument('--company', '-c', help='企業コード')
    parser.add_argument('--year', '-y', default='2022', help='年度')
    parser.add_argument('--type', '-t', default='有報', help='書類種別')
    parser.add_argument('--model', '-m', default='gemma2:9b', help='抽出モデル')
    parser.add_argument('--final-model', default='qwen3:14b', help='最終レポートモデル')
    parser.add_argument('--industry', default='all', help='業種')
    parser.add_argument('--mode', default='hybrid', choices=['json', 'qa', 'hybrid'], help='抽出モード')
    parser.add_argument('--test', type=int, metavar='N', help='テスト（N社）')
    parser.add_argument('--no-logs', action='store_true', help='抽出ログ無効')

    args = parser.parse_args()
    Config.OLLAMA_MODEL = args.model
    Config.OLLAMA_MODEL_FINAL = args.final_model
    Config.EXTRACTION_MODE = args.mode
    if args.no_logs:
        Config.SAVE_EXTRACTION_LOGS = False

    tag_manager.load()

    output_base = Path("./output_v10")
    output_base.mkdir(parents=True, exist_ok=True)
    rag_db = LocalRAGDB("./rag_db")

    all_companies = load_companies_from_sheets() or load_companies_from_sections()
    company_dict = {c.code: c for c in all_companies}

    targets = []

    if args.test:
        targets = get_available_companies_with_sections(args.year, args.type)[:args.test]
    elif args.company:
        if args.company in company_dict:
            targets.append(company_dict[args.company])

    if not targets:
        print("❌ 処理対象がありません")
        return 1

    print(f"\n🌳 PORTA v10 - 処理開始（モデル: {Config.OLLAMA_MODEL} / モード: {Config.EXTRACTION_MODE}）")

    total_start = time.time()

    if len(targets) == 1:
        result = process_single_company(
            targets[0].code, targets[0].name, args.year, args.type,
            args.industry, output_base, rag_db
        )
        print(f"\n完了: {result['status']} ({result['processing_time']:.1f}秒)")
        if result.get('number_check', {}).get('has_issues'):
            print(f"  ⚠️ 数値修正あり")
    else:
        results = process_multiple_companies(
            targets, args.year, args.type, output_base, rag_db, args.industry
        )
        print_summary(results, time.time() - total_start)

    return 0


# ============================================================================
# Phase 1: GS MD-Level Validation Framework
# ============================================================================

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import re


class ValidationLayer(Enum):
    """Multi-Layer Validation (L1-L6)"""
    L1_FORMAT = "L1_FORMAT"              # Schema & data type validation
    L2_ARITHMETIC = "L2_ARITHMETIC"      # Arithmetic consistency
    L3_CROSS_REF = "L3_CROSS_REF"        # PDF vs XBRL cross-reference
    L4_HISTORICAL = "L4_HISTORICAL"      # YoY anomaly detection
    L5_SEMANTIC = "L5_SEMANTIC"          # LLM-as-Judge logical consistency
    L6_HUMAN = "L6_HUMAN"                # Human review (placeholder)


class ConfidenceLevel(Enum):
    """Confidence-Based Routing"""
    HIGH = "HIGH"      # >= 80% → Auto-approve
    MEDIUM = "MEDIUM"  # 60-79% → Require review
    LOW = "LOW"        # < 60% → Re-extract or flag


@dataclass
class ValidationResult:
    """Single validation result"""
    layer: ValidationLayer
    passed: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfidenceScore:
    """Confidence scoring with weighted components"""
    completeness_score: float = 0.0      # 30% weight
    xbrl_coverage_score: float = 0.0     # 30% weight
    validation_score: float = 0.0        # 25% weight
    citation_score: float = 0.0          # 15% weight

    @property
    def overall_score(self) -> float:
        """Calculate weighted overall confidence score"""
        return (
            self.completeness_score * 0.30 +
            self.xbrl_coverage_score * 0.30 +
            self.validation_score * 0.25 +
            self.citation_score * 0.15
        )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Map overall score to confidence level"""
        score = self.overall_score
        if score >= 80:
            return ConfidenceLevel.HIGH
        elif score >= 60:
            return ConfidenceLevel.MEDIUM
        else:
            return ConfidenceLevel.LOW


@dataclass
class Citation:
    """Citation with source attribution"""
    value: str
    source: str          # "XBRL" or "PDF"
    page_number: Optional[int] = None
    filing_date: Optional[str] = None
    section: Optional[str] = None


class ValidationFramework:
    """Multi-layer validation framework for GS MD-level quality"""

    def __init__(self):
        self.validation_results: List[ValidationResult] = []

    def validate_l1_format(self, extracted: Dict) -> ValidationResult:
        """L1: Format validation - schema and data types"""
        issues = []
        warnings = []

        # Required sections check
        required_sections = [
            "company_overview", "financial_highlights", "business_risks",
            "md_and_a", "segment_analysis", "esg_governance"
        ]

        for section in required_sections:
            if section not in extracted or not extracted[section]:
                issues.append(f"Missing required section: {section}")

        # Financial highlights numeric validation
        if "financial_highlights" in extracted:
            fh = extracted["financial_highlights"]
            numeric_fields = ["revenue", "operating_profit", "net_profit",
                            "total_assets", "total_equity", "operating_cash_flow"]

            for field in numeric_fields:
                current = fh.get(field, {}).get("current")
                previous = fh.get(field, {}).get("previous")

                if current is None:
                    warnings.append(f"Missing current year value for {field}")
                elif not isinstance(current, (int, float)):
                    issues.append(f"Invalid data type for {field}.current: {type(current)}")

                if previous is None:
                    warnings.append(f"Missing previous year value for {field}")

        # Segment analysis validation
        if "segment_analysis" in extracted:
            segments = extracted["segment_analysis"].get("segments", [])
            if len(segments) == 0:
                warnings.append("No segment data found")
            else:
                for i, seg in enumerate(segments):
                    if not seg.get("segment_name"):
                        issues.append(f"Segment {i+1} missing segment_name")
                    if seg.get("revenue") is None:
                        warnings.append(f"Segment '{seg.get('segment_name', i+1)}' missing revenue")

        return ValidationResult(
            layer=ValidationLayer.L1_FORMAT,
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            details={"total_sections": len(extracted)}
        )

    def validate_l2_arithmetic(self, extracted: Dict) -> ValidationResult:
        """L2: Arithmetic consistency - segment totals, YoY calculations"""
        issues = []
        warnings = []
        details = {}

        # Segment revenue consistency check
        if "segment_analysis" in extracted and "financial_highlights" in extracted:
            segments = extracted["segment_analysis"].get("segments", [])
            total_revenue = extracted["financial_highlights"].get("revenue", {}).get("current")

            if segments and total_revenue is not None:
                segment_revenue_sum = sum(
                    seg.get("revenue", 0) or 0
                    for seg in segments
                    if seg.get("revenue") is not None
                )

                if segment_revenue_sum > 0:
                    deviation_pct = abs(segment_revenue_sum - total_revenue) / total_revenue * 100
                    details["segment_revenue_sum"] = segment_revenue_sum
                    details["total_revenue"] = total_revenue
                    details["deviation_pct"] = round(deviation_pct, 2)

                    if deviation_pct > 5:
                        issues.append(
                            f"Segment revenue sum ({segment_revenue_sum:,.0f}) deviates "
                            f"{deviation_pct:.1f}% from total revenue ({total_revenue:,.0f})"
                        )
                    elif deviation_pct > 2:
                        warnings.append(
                            f"Minor segment revenue deviation: {deviation_pct:.1f}%"
                        )

        # YoY calculation validation
        if "financial_highlights" in extracted:
            fh = extracted["financial_highlights"]

            for field in ["revenue", "operating_profit", "net_profit"]:
                data = fh.get(field, {})
                current = data.get("current")
                previous = data.get("previous")
                yoy_pct = data.get("yoy_pct")

                if current is not None and previous is not None and previous != 0:
                    expected_yoy = (current - previous) / previous * 100

                    if yoy_pct is not None:
                        diff = abs(expected_yoy - yoy_pct)
                        if diff > 1.0:
                            issues.append(
                                f"{field} YoY%: Reported {yoy_pct:.1f}% vs "
                                f"Calculated {expected_yoy:.1f}% (diff: {diff:.1f}%)"
                            )

        # Segment-level YoY uniqueness check (prevent company-wide YoY reuse)
        if "segment_analysis" in extracted:
            segments = extracted["segment_analysis"].get("segments", [])
            yoy_values = [seg.get("revenue_yoy_pct") for seg in segments if seg.get("revenue_yoy_pct") is not None]

            if len(yoy_values) > 1:
                unique_yoy = set(yoy_values)

                # Check if ALL segments have identical YoY (major issue)
                if len(unique_yoy) == 1:
                    single_yoy = list(unique_yoy)[0]

                    # Check if this matches company-wide revenue YoY
                    company_yoy = extracted.get("financial_highlights", {}).get("revenue", {}).get("yoy_pct")

                    if company_yoy is not None and abs(single_yoy - company_yoy) < 0.1:
                        issues.append(
                            f"🚨 CRITICAL: All {len(yoy_values)} segments have identical YoY ({single_yoy:.1f}%), "
                            f"which matches company-wide revenue YoY ({company_yoy:.1f}%). "
                            "This indicates company-wide YoY was incorrectly copied to all segments. "
                            "Each segment must have its own unique growth rate."
                        )
                    else:
                        warnings.append(
                            f"All {len(yoy_values)} segments have identical YoY ({single_yoy:.1f}%). "
                            "Verify if this is accurate or if segment-specific data is missing."
                        )

                # Check if >30% duplicates (warning)
                elif len(unique_yoy) < len(yoy_values) * 0.7:
                    warnings.append(
                        f"Suspicious: {len(yoy_values) - len(unique_yoy)} segments share identical YoY values. "
                        "Each segment should have unique YoY%, not company-wide values."
                    )

            # 🚨 NEW: Check if all segment revenues are identical (hallucination detection)
            revenue_values = [seg.get("revenue") for seg in segments if seg.get("revenue") is not None and seg.get("revenue") > 0]

            if len(revenue_values) >= 2:
                unique_revenues = set(revenue_values)

                # Check if ALL segments have identical revenue (major hallucination)
                if len(unique_revenues) == 1:
                    single_revenue = list(unique_revenues)[0]

                    # Check if this matches company-wide total revenue
                    company_revenue = extracted.get("financial_highlights", {}).get("revenue", {}).get("current")

                    if company_revenue is not None:
                        # Allow 1% tolerance for rounding
                        if abs(single_revenue - company_revenue) / company_revenue < 0.01:
                            issues.append(
                                f"🚨 CRITICAL HALLUCINATION: All {len(revenue_values)} segments have identical revenue "
                                f"({single_revenue:,.0f}), which equals the company total ({company_revenue:,.0f}). "
                                "The LLM incorrectly copied the company total to each segment instead of extracting "
                                "segment-specific values. Each segment must have its own unique revenue."
                            )
                        else:
                            issues.append(
                                f"🚨 HALLUCINATION DETECTED: All {len(revenue_values)} segments have identical revenue "
                                f"({single_revenue:,.0f}). This is highly unlikely - each segment should have different revenue. "
                                "Verify if segment-specific data was properly extracted."
                            )

                # Check if majority of segments share the same revenue (suspicious)
                elif len(unique_revenues) < len(revenue_values) * 0.5:
                    from collections import Counter
                    revenue_counts = Counter(revenue_values)
                    most_common_revenue, count = revenue_counts.most_common(1)[0]
                    warnings.append(
                        f"Suspicious: {count} out of {len(revenue_values)} segments have identical revenue "
                        f"({most_common_revenue:,.0f}). Each segment typically has different revenue."
                    )

        return ValidationResult(
            layer=ValidationLayer.L2_ARITHMETIC,
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            details=details
        )

    def validate_l3_cross_reference(
        self,
        extracted: Dict,
        xbrl_data: Optional[Dict] = None
    ) -> ValidationResult:
        """L3: Cross-reference validation - PDF extraction vs XBRL data"""
        issues = []
        warnings = []
        details = {"xbrl_available": xbrl_data is not None}

        if xbrl_data is None:
            warnings.append("XBRL data not available for cross-reference validation")
            return ValidationResult(
                layer=ValidationLayer.L3_CROSS_REF,
                passed=True,
                issues=issues,
                warnings=warnings,
                details=details
            )

        # Compare key financial metrics with XBRL
        if "financial_highlights" in extracted:
            fh = extracted["financial_highlights"]

            # Revenue comparison
            pdf_revenue = fh.get("revenue", {}).get("current")
            xbrl_revenue = xbrl_data.get("revenue")

            if pdf_revenue and xbrl_revenue:
                deviation_pct = abs(pdf_revenue - xbrl_revenue) / xbrl_revenue * 100
                details["revenue_deviation_pct"] = round(deviation_pct, 2)

                if deviation_pct > 1.0:
                    issues.append(
                        f"Revenue mismatch: PDF={pdf_revenue:,.0f} vs XBRL={xbrl_revenue:,.0f} "
                        f"(deviation: {deviation_pct:.2f}%)"
                    )

            # Operating profit comparison
            pdf_op = fh.get("operating_profit", {}).get("current")
            xbrl_op = xbrl_data.get("operating_income")

            if pdf_op and xbrl_op:
                deviation_pct = abs(pdf_op - xbrl_op) / abs(xbrl_op) * 100
                details["operating_profit_deviation_pct"] = round(deviation_pct, 2)

                if deviation_pct > 1.0:
                    issues.append(
                        f"Operating profit mismatch: PDF={pdf_op:,.0f} vs XBRL={xbrl_op:,.0f} "
                        f"(deviation: {deviation_pct:.2f}%)"
                    )

            # Net profit comparison
            pdf_net = fh.get("net_profit", {}).get("current")
            xbrl_net = xbrl_data.get("net_income")

            if pdf_net and xbrl_net:
                deviation_pct = abs(pdf_net - xbrl_net) / abs(xbrl_net) * 100
                details["net_profit_deviation_pct"] = round(deviation_pct, 2)

                if deviation_pct > 1.0:
                    issues.append(
                        f"Net profit mismatch: PDF={pdf_net:,.0f} vs XBRL={xbrl_net:,.0f} "
                        f"(deviation: {deviation_pct:.2f}%)"
                    )

        return ValidationResult(
            layer=ValidationLayer.L3_CROSS_REF,
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            details=details
        )

    def validate_l4_historical(
        self,
        extracted: Dict,
        historical_data: Optional[List[Dict]] = None
    ) -> ValidationResult:
        """L4: Historical continuity - YoY anomaly detection"""
        issues = []
        warnings = []
        details = {}

        if historical_data is None or len(historical_data) == 0:
            warnings.append("Historical data not available for continuity check")
            return ValidationResult(
                layer=ValidationLayer.L4_HISTORICAL,
                passed=True,
                issues=issues,
                warnings=warnings,
                details=details
            )

        # Check for unusual YoY changes (>50% without explanation)
        if "financial_highlights" in extracted:
            fh = extracted["financial_highlights"]

            for field in ["revenue", "operating_profit", "net_profit"]:
                yoy_pct = fh.get(field, {}).get("yoy_pct")

                if yoy_pct is not None and abs(yoy_pct) > 50:
                    # Check if business_risks section mentions restructuring/M&A
                    risks_text = str(extracted.get("business_risks", "")).lower()
                    mda_text = str(extracted.get("md_and_a", "")).lower()

                    has_explanation = any(
                        keyword in risks_text or keyword in mda_text
                        for keyword in ["買収", "合併", "事業譲渡", "再編", "m&a", "統合"]
                    )

                    if not has_explanation:
                        warnings.append(
                            f"{field} shows unusual YoY change ({yoy_pct:+.1f}%) without "
                            "clear explanation in risks/MD&A sections"
                        )

                    details[f"{field}_large_change"] = round(yoy_pct, 1)

        return ValidationResult(
            layer=ValidationLayer.L4_HISTORICAL,
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            details=details
        )

    def validate_l5_semantic(self, extracted: Dict, model: str = None) -> ValidationResult:
        """L5: Semantic validity - LLM-as-Judge for logical consistency"""
        issues = []
        warnings = []
        details = {}

        # Simple rule-based semantic checks (full LLM-as-Judge would require additional API calls)

        # Check 1: Revenue drivers vs segment analysis consistency
        if "md_and_a" in extracted and "segment_analysis" in extracted:
            drivers = extracted["md_and_a"].get("revenue_drivers", [])
            segments = extracted["segment_analysis"].get("segments", [])

            if drivers and segments:
                driver_keywords = set()
                for d in drivers:
                    factor = d.get("factor", "").lower()
                    driver_keywords.update(factor.split())

                segment_names = {seg.get("segment_name", "").lower() for seg in segments}

                # Check if driver mentions align with segment performance
                mentioned_segments = sum(
                    1 for seg_name in segment_names
                    if any(word in driver_keywords for word in seg_name.split())
                )

                if len(segments) > 0 and mentioned_segments == 0:
                    warnings.append(
                        "Revenue drivers do not mention any specific segments. "
                        "Consider adding segment-specific driver analysis."
                    )

        # Check 2: Risk materiality vs business impact
        if "business_risks" in extracted:
            risks = extracted["business_risks"]
            high_risks = [r for r in risks if r.get("materiality", "").lower() == "高"]

            if len(high_risks) > 10:
                warnings.append(
                    f"Unusually high number of '高' materiality risks ({len(high_risks)}). "
                    "Verify if all are truly high-impact."
                )

            details["high_materiality_risks"] = len(high_risks)

        # Check 3: Guidance vs historical performance consistency
        if "md_and_a" in extracted and "financial_highlights" in extracted:
            guidance = extracted["md_and_a"].get("next_year_guidance", {})
            fh = extracted["financial_highlights"]

            guidance_revenue_yoy = guidance.get("revenue_yoy_pct")
            historical_revenue_yoy = fh.get("revenue", {}).get("yoy_pct")

            if guidance_revenue_yoy and historical_revenue_yoy:
                change_in_trend = guidance_revenue_yoy - historical_revenue_yoy

                if abs(change_in_trend) > 20:
                    warnings.append(
                        f"Large shift in revenue growth trajectory: Historical {historical_revenue_yoy:+.1f}% → "
                        f"Guidance {guidance_revenue_yoy:+.1f}% (change: {change_in_trend:+.1f}pp). "
                        "Verify if MD&A explains this shift."
                    )

        return ValidationResult(
            layer=ValidationLayer.L5_SEMANTIC,
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            details=details
        )

    def run_all_validations(
        self,
        extracted: Dict,
        xbrl_data: Optional[Dict] = None,
        historical_data: Optional[List[Dict]] = None
    ) -> List[ValidationResult]:
        """Run all validation layers (L1-L5)"""
        results = []

        results.append(self.validate_l1_format(extracted))
        results.append(self.validate_l2_arithmetic(extracted))
        results.append(self.validate_l3_cross_reference(extracted, xbrl_data))
        results.append(self.validate_l4_historical(extracted, historical_data))
        results.append(self.validate_l5_semantic(extracted))

        self.validation_results = results
        return results


class ConfidenceScorer:
    """Calculate confidence scores based on validation results and data quality"""

    @staticmethod
    def calculate_completeness_score(extracted: Dict) -> float:
        """Calculate completeness score (30% weight)"""
        total_sections = 7  # company_overview, financial_highlights, business_risks, md_and_a, segment_analysis, esg_governance, notes
        present_sections = 0

        section_checks = {
            "company_overview": lambda e: bool(e.get("company_overview")),
            "financial_highlights": lambda e: bool(e.get("financial_highlights", {}).get("revenue")),
            "business_risks": lambda e: len(e.get("business_risks", [])) > 0,
            "md_and_a": lambda e: bool(e.get("md_and_a", {}).get("revenue_drivers")),
            "segment_analysis": lambda e: len(e.get("segment_analysis", {}).get("segments", [])) > 0,
            "esg_governance": lambda e: bool(e.get("esg_governance")),
            "notes": lambda e: bool(e.get("notes")),
        }

        for section, check_func in section_checks.items():
            if check_func(extracted):
                present_sections += 1

        return (present_sections / total_sections) * 100

    @staticmethod
    def calculate_xbrl_coverage_score(
        extracted: Dict,
        xbrl_data: Optional[Dict] = None
    ) -> float:
        """Calculate XBRL coverage score (30% weight) - v10.1: 主要KPI欠損時の厳格化"""
        if xbrl_data is None:
            return 0.0  # No XBRL data available

        # v10.1: 主要KPI（売上高・営業利益）の欠損を重視
        critical_fields = ["revenue", "operating_income"]
        critical_covered = sum(1 for field in critical_fields if xbrl_data.get(field) is not None)

        # 主要KPIが1つでも欠損している場合は大幅減点
        if critical_covered < len(critical_fields):
            # 売上高または営業利益が欠損 → 最大50%まで
            other_fields = ["net_income", "total_assets", "total_equity"]
            other_covered = sum(1 for field in other_fields if xbrl_data.get(field) is not None)
            return min((other_covered / len(other_fields)) * 50, 50)

        # 主要KPIが揃っている場合は通常計算
        all_fields = ["revenue", "operating_income", "net_income", "total_assets", "total_equity"]
        covered = sum(1 for field in all_fields if xbrl_data.get(field) is not None)
        total = len(all_fields)

        return (covered / total) * 100

    @staticmethod
    def calculate_validation_score(validation_results: List[ValidationResult]) -> float:
        """Calculate validation score (25% weight)"""
        if not validation_results:
            return 0.0

        passed_count = sum(1 for r in validation_results if r.passed)
        total_count = len(validation_results)

        # Deduct points for issues
        total_issues = sum(len(r.issues) for r in validation_results)
        issue_penalty = min(total_issues * 5, 30)  # Max 30% penalty

        base_score = (passed_count / total_count) * 100
        return max(base_score - issue_penalty, 0)

    @staticmethod
    def calculate_citation_score(extracted: Dict) -> float:
        """Calculate citation score (15% weight)"""
        # Check if key financial numbers have source attribution
        citation_count = 0
        total_numbers = 0

        if "financial_highlights" in extracted:
            fh = extracted["financial_highlights"]
            for field in ["revenue", "operating_profit", "net_profit"]:
                data = fh.get(field, {})
                if data.get("current") is not None:
                    total_numbers += 1
                    if data.get("source"):
                        citation_count += 1

        if total_numbers == 0:
            return 0.0

        return (citation_count / total_numbers) * 100

    @classmethod
    def calculate_confidence(
        cls,
        extracted: Dict,
        validation_results: List[ValidationResult],
        xbrl_data: Optional[Dict] = None
    ) -> ConfidenceScore:
        """Calculate overall confidence score"""
        return ConfidenceScore(
            completeness_score=cls.calculate_completeness_score(extracted),
            xbrl_coverage_score=cls.calculate_xbrl_coverage_score(extracted, xbrl_data),
            validation_score=cls.calculate_validation_score(validation_results),
            citation_score=cls.calculate_citation_score(extracted)
        )


class CitationManager:
    """Manage source attribution for all financial numbers"""

    def __init__(self):
        self.citations: Dict[str, Citation] = {}

    def add_citation(
        self,
        field_name: str,
        value: str,
        source: str,
        page_number: Optional[int] = None,
        filing_date: Optional[str] = None,
        section: Optional[str] = None
    ):
        """Add citation for a field"""
        self.citations[field_name] = Citation(
            value=value,
            source=source,
            page_number=page_number,
            filing_date=filing_date,
            section=section
        )

    def get_citation(self, field_name: str) -> Optional[Citation]:
        """Get citation for a field"""
        return self.citations.get(field_name)

    def generate_citation_footnotes(self) -> str:
        """Generate markdown footnotes for all citations"""
        if not self.citations:
            return ""

        lines = ["\n## 出典・引用元\n"]

        for field_name, citation in sorted(self.citations.items()):
            if citation.source == "XBRL":
                lines.append(f"- **{field_name}**: {citation.value} (出典: XBRL)")
            elif citation.source == "PDF":
                page_info = f"p.{citation.page_number}" if citation.page_number else "PDF"
                section_info = f" - {citation.section}" if citation.section else ""
                lines.append(f"- **{field_name}**: {citation.value} (出典: {page_info}{section_info})")

        return "\n".join(lines)


def run_phase1_validation(
    extracted: Dict,
    xbrl_data: Optional[Dict] = None,
    historical_data: Optional[List[Dict]] = None
) -> Tuple[List[ValidationResult], ConfidenceScore]:
    """Run Phase 1 validation and return results with confidence score"""

    framework = ValidationFramework()
    validation_results = framework.run_all_validations(extracted, xbrl_data, historical_data)

    scorer = ConfidenceScorer()
    confidence = scorer.calculate_confidence(extracted, validation_results, xbrl_data)

    return validation_results, confidence


def generate_phase1_quality_report(
    validation_results: List[ValidationResult],
    confidence: ConfidenceScore
) -> str:
    """Generate Phase 1 quality report in markdown format"""

    lines = [
        "\n---\n",
        "## 📊 Phase 1: 品質検証レポート\n",
        f"**Overall Confidence**: {confidence.overall_score:.1f}% ({confidence.confidence_level.value})\n",
        "",
        "### Confidence Score Breakdown",
        f"- **Completeness**: {confidence.completeness_score:.1f}% (weight: 30%)",
        f"- **XBRL Coverage**: {confidence.xbrl_coverage_score:.1f}% (weight: 30%)",
        f"- **Validation**: {confidence.validation_score:.1f}% (weight: 25%)",
        f"- **Citation**: {confidence.citation_score:.1f}% (weight: 15%)",
        "",
        "### Validation Results (L1-L5)\n"
    ]

    for result in validation_results:
        status_icon = "✅" if result.passed else "❌"
        lines.append(f"#### {status_icon} {result.layer.value}")

        if result.issues:
            lines.append("\n**Issues:**")
            for issue in result.issues:
                lines.append(f"- ❌ {issue}")

        if result.warnings:
            lines.append("\n**Warnings:**")
            for warning in result.warnings:
                lines.append(f"- ⚠️ {warning}")

        if result.details:
            lines.append("\n**Details:**")
            for key, value in result.details.items():
                lines.append(f"- {key}: {value}")

        lines.append("")

    # Routing recommendation based on confidence level
    lines.append("### 推奨アクション\n")

    if confidence.confidence_level == ConfidenceLevel.HIGH:
        lines.append("- ✅ **HIGH Confidence (≥80%)**: Auto-approve for publication")
    elif confidence.confidence_level == ConfidenceLevel.MEDIUM:
        lines.append("- ⚠️ **MEDIUM Confidence (60-79%)**: Require analyst review before publication")
    else:
        lines.append("- ❌ **LOW Confidence (<60%)**: Re-extract or flag for manual investigation")

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())

