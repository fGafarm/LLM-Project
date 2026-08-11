#!/usr/bin/env python3
"""
PORTA v10 - 完全統合版（v10.0 → v10.1改良版）

===============================================================================
v10.1の改良点
===============================================================================
A. BM25の日本語対応
   - tokenize()関数を実装（2-gram/3-gram + 英数字単位保持）
   - doc.split()依存を完全排除

B. BM25 PDF選択の確実な統合
   - すべてのセクション処理パスで select_pdfs_by_question を使用
   - manifest未検出時のフォールバック（全PDF読み込み）を明示

C. extract_first_json の堅牢化
   - text.replace("'", '"') の削除
   - 文字列リテラル内の {} を考慮したブレース追跡
   - JSON解析失敗時の詳細ログ

D. hybrid モード重複除去の改善
   - normalize_text() 関数（全角/半角統一、句読点統一、小文字化）
   - normalize_page() / normalize_impact() の適用
   - is_boilerplate() による定型文フィルタ

E. (オプション) PDF抽出キャッシュ
   - functools.lru_cache による extract_text_from_pdf_with_pages のメモ化

F. (オプション) ログの一貫性向上
   - 統一されたログフォーマット

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
- BM25ベースのPDF選択（manifest.json活用）

===============================================================================
使い方
===============================================================================
python Run_integrated_v10.py --company 1301 --year 2022 --industry food --mode hybrid
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
from collections import defaultdict, Counter
from functools import lru_cache
import math
import unicodedata

import warnings
warnings.filterwarnings("ignore")

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
# 設定（v9.6.1 + v9.5.11統合 + v10.1改良）
# ============================================================
class Config:
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "gemma2:9b"        # 抽出用（JSON安定）
    OLLAMA_MODEL_FINAL = "qwen3:14b"  # 最終レポート用（日本語品質）

    SECTIONS_BASE = Path(r"E:\PDF\sections_test")  # Yuho_splitter_v4の出力先（テスト用）
    XBRL_BASE = Path(r"E:\PDF\PDF+XBRL")
    PROJECT_DIR = Path(r"C:\Users\shun nabeno\Desktop\Local LLM Project\backend")

    COMPANY_SPREADSHEET = "All_company"
    COMPANY_TAB = "Company"
    TAXONOMY_SPREADSHEET = "StockFlow企業データ"
    TAXONOMY_TAB = "taxonomy_config2"

    TAG_CACHE_FILE = Path("./xbrl_tags_cache.json")

    # ★ v9.6.1: 質問応答用の設定
    QA_NUM_CTX = 16000       # 質問応答用コンテキスト（大きめ）
    QA_NUM_PREDICT = 2000    # 質問応答用出力
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

    # ★ v10.3: PDF filtering (Yuho_splitter_v4 output + BM25)
    ENABLE_PDF_FILTERING = True  # Set to False to read all PDFs (old behavior)

    # ★ v10: 抽出モード選択
    # "qa" = 質問応答方式（v9.6.1）
    # "json" = JSON抽出方式（v9.5.11）
    # "hybrid" = 両方併用（v10新機能）
    EXTRACTION_MODE = "hybrid"

    # ★ v10.1: PDF抽出キャッシュ（最大128ファイルまでメモリに保持）
    ENABLE_PDF_CACHE = True
    PDF_CACHE_SIZE = 128


# ============================================================
# ★ v10.1: 日本語対応トークナイザー
# ============================================================
def tokenize(text: str, n: int = 2) -> List[str]:
    """
    日本語と英語を適切に処理するトークナイザー

    Args:
        text: 入力テキスト
        n: n-gram（日本語用、デフォルト2）

    Returns:
        トークンのリスト
    """
    if not text:
        return []

    tokens = []
    i = 0
    text_len = len(text)

    while i < text_len:
        char = text[i]

        # 英数字（連続する英数字を1トークンとして扱う）
        if char.isalnum() or char in ('.', ','):
            # 英数字の連続を取得
            j = i
            while j < text_len and (text[j].isalnum() or text[j] in ('.', ',')):
                j += 1
            token = text[i:j].lower()
            if token and not token.replace('.', '').replace(',', '').isspace():
                tokens.append(token)
            i = j
            continue

        # 重要な単位記号は保持
        if char in ('億', '万', '円', '%', '±'):
            tokens.append(char)
            i += 1
            continue

        # 空白・記号はスキップ
        if char.isspace() or unicodedata.category(char).startswith('P'):
            i += 1
            continue

        # 日本語文字（ひらがな、カタカナ、漢字）→ n-gram
        if '\u3040' <= char <= '\u309F' or \
           '\u30A0' <= char <= '\u30FF' or \
           '\u4E00' <= char <= '\u9FFF':
            # n-gram生成（デフォルト2-gram）
            if i + n <= text_len:
                ngram = text[i:i+n]
                tokens.append(ngram)
            i += 1
        else:
            # その他の文字はスキップ
            i += 1

    return tokens


# ============================================================
# ★ v10.1: テキスト正規化（重複検出用）
# ============================================================
def normalize_text(text: str) -> str:
    """
    テキストを正規化して重複検出しやすくする

    - 全角→半角変換（英数字・記号）
    - 小文字化
    - 連続空白を1つに
    - 句読点の統一（、→,  。→.）
    """
    if not text:
        return ""

    # 全角→半角（英数字と一部記号）
    text = unicodedata.normalize('NFKC', text)

    # 小文字化
    text = text.lower()

    # 句読点統一
    text = text.replace('、', ',').replace('。', '.')

    # 連続空白を1つに
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


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
        ]
    },
    "03_MDA": {
        "name": "経営成績分析（MDA）",
        "questions": [
            # === 売上・利益分析 ===
            {
                "id": "revenue_drivers",
                "question": "売上高の増減要因は何ですか？セグメント別・製品別・地域別に具体的な理由と金額・割合を説明してください。",
                "focus": "売上"
            },
            {
                "id": "profit_drivers",
                "question": "営業利益・経常利益の増減要因は何ですか？原価、販管費、為替、一過性要因など、具体的な理由と金額を説明してください。",
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
            # === セグメント ===
            {
                "id": "segment_performance",
                "question": "各セグメント（事業部門）の業績はどうでしたか？好調・不調のセグメントとその理由を説明してください。",
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
- 組織再編・経営陣の交代

【除外すべきもの】
- 歴史的な記述（創業、沿革、過去の設立経緯など）
- 定型的なリスク記述（「影響を及ぼす可能性があります」など）
- ガバナンス定型文（取締役会決議、株主総会開催など）
- 会計処理の変更（新会計基準の適用など）

【回答形式】
各イベントごとに以下を含めてください：
- イベントの内容（具体的に）
- 業績への影響（金額があれば記載）
- 発生時期（○月、第○四半期など）""",
                "focus": "イベント"
            },
            # === 見通し ===
            {
                "id": "outlook",
                "question": "来期の業績見通しや経営方針はどうですか？前提条件（為替レート、原材料価格など）と主な増減要因を説明してください。",
                "focus": "見通し"
            },
        ]
    },
    "05_セグメント": {
        "name": "セグメント情報",
        "questions": [
            {
                "id": "segment_overview",
                "question": "各セグメント（事業）の役割・特徴は何ですか？主要製品・サービス、顧客層、市場環境を説明してください。",
                "focus": "事業内容"
            },
            {
                "id": "segment_sales",
                "question": "各セグメントの売上高の増減要因は何ですか？数量・価格・新製品・市場環境などの影響を説明してください。",
                "focus": "売上"
            },
            {
                "id": "segment_profit",
                "question": "各セグメントの営業利益の増減要因は何ですか？原価率、販管費、固定費負担などの影響を説明してください。",
                "focus": "利益"
            },
        ]
    },
    "07_その他": {
        "name": "その他の重要情報",
        "questions": [
            {
                "id": "capex",
                "question": "設備投資の内容と金額は？新工場・設備の建設、既存設備の更新、IT投資など、主要な投資内容を説明してください。",
                "focus": "設備投資"
            },
            {
                "id": "rd",
                "question": "研究開発の重点テーマと投資額は？新製品開発、技術革新、研究体制などについて説明してください。",
                "focus": "研究開発"
            },
        ]
    },
}


# ============================================================
# ★ v9.6.1: セクションマッピング（質問ベースPDF選択用）
# ============================================================
SECTION_MAPPING = {
    "01_会社概要": {
        "name": "会社概要",
        "extract_focus": "会社概要 主要な経営指標 沿革 従業員数 事業所",
        "subsection_map": {},
    },
    "02_経営戦略_リスク": {
        "name": "経営方針・リスク",
        "extract_focus": "経営方針 経営計画 対処すべき課題 目標とする経営指標 事業等のリスク サステナビリティ",
        "subsection_map": {},
    },
    "03_MDA": {
        "name": "経営成績・財政状態",
        "extract_focus": "経営成績 売上高 営業利益 経常利益 当期純利益 セグメント 業績変動要因 見通し キャッシュフロー 財政状態",
        "subsection_map": {},
    },
    "04_財務三表": {
        "name": "財務諸表",
        "extract_focus": "",  # XBRLから取得するためPDF読み込み不要
        "subsection_map": {},
    },
    "05_セグメント": {
        "name": "セグメント情報",
        "extract_focus": "セグメント情報 事業別 売上高 営業利益 セグメント別業績 部門別",
        "subsection_map": {},
    },
    "06_ガバナンス": {
        "name": "コーポレート・ガバナンス",
        "extract_focus": "コーポレート・ガバナンス 取締役 監査役 報酬 大株主 ストックオプション",
        "subsection_map": {},
    },
    "07_その他": {
        "name": "その他",
        "extract_focus": "設備投資 研究開発 関係会社 訴訟 重要事象",
        "subsection_map": {},
    },
}


# ============================================================
# ★ v10.3: BM25-based Question-Driven PDF Selection
# ============================================================

class BM25:
    """
    BM25 ranking function for PDF selection (v10.1: 日本語対応版)

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

        # ★ v10.1: tokenize()を使用（doc.split()廃止）
        tokenized_docs = [tokenize(doc) for doc in docs]
        self.doc_len = [len(tokens) for tokens in tokenized_docs]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0

        # Calculate document frequencies
        df = defaultdict(int)
        for tokens in tokenized_docs:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                df[token] += 1

        # Calculate IDF
        num_docs = len(docs)
        self.idf = {
            token: math.log((num_docs - freq + 0.5) / (freq + 0.5) + 1.0)
            for token, freq in df.items()
        }

        # Store term frequencies for each document
        self.doc_freqs = []
        for tokens in tokenized_docs:
            token_counts = Counter(tokens)
            self.doc_freqs.append(token_counts)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        Search for top-k documents matching the query

        Args:
            query: Query text
            top_k: Number of results to return

        Returns:
            List of (doc_index, score) tuples
        """
        # ★ v10.1: tokenize()を使用（query.split()廃止）
        query_tokens = tokenize(query)
        scores = []

        for idx, (doc_freq, doc_len) in enumerate(zip(self.doc_freqs, self.doc_len)):
            score = 0.0
            for token in query_tokens:
                if token not in doc_freq:
                    continue

                freq = doc_freq[token]
                idf = self.idf.get(token, 0)

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
    Select PDFs using BM25 search based on question (v10.1: 統合確認済み)

    Args:
        question: Question text (e.g., SECTION_MAPPING[section_id]["extract_focus"])
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


# ============================================================
# Ollama操作
# ============================================================
def call_ollama(prompt: str, model: str = None, num_ctx: int = None,
                num_predict: int = None, temperature: float = None) -> str:
    """Ollama APIを呼び出してテキスト生成"""
    if model is None:
        model = Config.OLLAMA_MODEL
    if num_ctx is None:
        num_ctx = Config.EXTRACT_NUM_CTX
    if num_predict is None:
        num_predict = Config.EXTRACT_NUM_PREDICT
    if temperature is None:
        temperature = Config.TEMPERATURE

    url = f"{Config.OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": temperature,
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")
    except Exception as e:
        logger.error(f"  ❌ Ollama呼び出し失敗: {e}")
        return ""


def call_ollama_json(prompt: str, model: str = None, num_ctx: int = None,
                     num_predict: int = None, temperature: float = None) -> Optional[Dict]:
    """
    Ollama APIを呼び出してJSON形式のレスポンスを取得

    v10.1改良: extract_first_json() の堅牢化により、
    より多様なLLM出力からJSON抽出が可能に
    """
    response_text = call_ollama(prompt, model, num_ctx, num_predict, temperature)
    if not response_text:
        return None

    json_str = extract_first_json(response_text)
    if not json_str:
        logger.warning("  ⚠️ JSON抽出失敗")
        return None

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"  ⚠️ JSON解析失敗: {e}")
        return None


def extract_first_json(text: str) -> Optional[str]:
    """
    テキストから最初のJSONオブジェクトを抽出（v10.1改良版）

    改良点:
    - text.replace("'", '"') を削除（valid JSONを破壊する可能性があるため）
    - 文字列リテラル内の {} を考慮したブレース追跡
    - より詳細なエラーログ
    """
    if not text:
        return None

    # コードブロック記号除去
    text = re.sub(r'```(?:json)?', '', text, flags=re.IGNORECASE).replace('```', '')

    # 末尾のカンマ除去（trailing commas）
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    start = text.find('{')
    if start < 0:
        return None

    # ブレース追跡（文字列リテラル内を考慮）
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError as e:
                    # 詳細ログ（デバッグ用）
                    logger.debug(f"  JSON解析失敗（位置: {i}）: {e}")
                    logger.debug(f"  候補: {candidate[:200]}...")
                    # 次の { を探して再試行
                    next_start = text.find('{', i + 1)
                    if next_start > 0:
                        # 再帰的に次の候補を探す
                        return extract_first_json(text[next_start:])
                    return None

    return None


# ============================================================
# PDF読み込み（v10.1: キャッシュ追加）
# ============================================================
if Config.ENABLE_PDF_CACHE:
    @lru_cache(maxsize=Config.PDF_CACHE_SIZE)
    def extract_text_from_pdf_with_pages(pdf_path: Path) -> List[Tuple[int, str]]:
        """
        PDFからページごとにテキスト抽出（キャッシュ有効）

        Returns:
            List of (page_number, text) tuples
        """
        return _extract_text_from_pdf_with_pages_impl(pdf_path)
else:
    def extract_text_from_pdf_with_pages(pdf_path: Path) -> List[Tuple[int, str]]:
        """
        PDFからページごとにテキスト抽出（キャッシュ無効）

        Returns:
            List of (page_number, text) tuples
        """
        return _extract_text_from_pdf_with_pages_impl(pdf_path)


def _extract_text_from_pdf_with_pages_impl(pdf_path: Path) -> List[Tuple[int, str]]:
    """PDF抽出の実装（内部関数）"""
    if not HAS_PDFPLUMBER:
        logger.error("  ❌ pdfplumber未インストール")
        return []

    try:
        pages_data = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text:
                    pages_data.append((page_num, text))
        return pages_data
    except Exception as e:
        logger.error(f"  ❌ PDF読み込み失敗 ({pdf_path.name}): {e}")
        return []


# ============================================================
# XBRL操作
# ============================================================
def load_tag_cache() -> Dict[str, Dict[str, str]]:
    """XBRLタグキャッシュ読み込み"""
    if not Config.TAG_CACHE_FILE.exists():
        return {}
    try:
        with open(Config.TAG_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def save_tag_cache(cache: Dict[str, Dict[str, str]]):
    """XBRLタグキャッシュ保存"""
    try:
        with open(Config.TAG_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"  ⚠️ タグキャッシュ保存失敗: {e}")


def get_taxonomy_mapping() -> Dict[str, str]:
    """Google Sheetsからタクソノミマッピング取得"""
    if not HAS_GSPREAD:
        return {}

    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        credentials = Credentials.from_service_account_file(
            Config.PROJECT_DIR / 'credentials.json',
            scopes=scopes
        )
        gc = gspread.authorize(credentials)
        sheet = gc.open(Config.TAXONOMY_SPREADSHEET).worksheet(Config.TAXONOMY_TAB)
        rows = sheet.get_all_values()

        mapping = {}
        for row in rows[1:]:
            if len(row) >= 2:
                key = row[0].strip()
                tag = row[1].strip()
                if key and tag:
                    mapping[key] = tag

        logger.info(f"  ✅ タクソノミマッピング取得: {len(mapping)}件")
        return mapping

    except Exception as e:
        logger.warning(f"  ⚠️ タクソノミマッピング取得失敗: {e}")
        return {}


def extract_xbrl_data(xbrl_zip_path: Path, taxonomy_mapping: Dict[str, str] = None) -> Dict[str, Any]:
    """XBRLファイルから財務データ抽出"""
    if not HAS_LXML:
        return {}

    if taxonomy_mapping is None:
        taxonomy_mapping = {}

    # タグキャッシュ読み込み
    tag_cache = load_tag_cache()
    cache_key = xbrl_zip_path.stem

    # キャッシュヒット判定
    if cache_key in tag_cache:
        logger.info(f"  📦 XBRLタグキャッシュ使用")
        cached_tags = tag_cache[cache_key]
    else:
        cached_tags = {}

    try:
        with zipfile.ZipFile(xbrl_zip_path) as z:
            xbrl_files = [f for f in z.namelist() if f.endswith('.xbrl')]
            if not xbrl_files:
                return {}

            xbrl_content = z.read(xbrl_files[0])
            root = etree.fromstring(xbrl_content)

            # 名前空間取得
            nsmap = root.nsmap
            jpcrp_ns = nsmap.get('jpcrp')
            jppfs_ns = nsmap.get('jppfs')
            ix_ns = nsmap.get('ix') or nsmap.get('ixbrl')

            data = {}

            # タクソノミマッピングから検索
            for key, tag in taxonomy_mapping.items():
                if key in cached_tags:
                    xpath_expr = cached_tags[key]
                else:
                    # タグから名前空間を推定
                    if tag.startswith('jpcrp'):
                        ns = jpcrp_ns
                    elif tag.startswith('jppfs'):
                        ns = jppfs_ns
                    else:
                        ns = jpcrp_ns

                    tag_local = tag.split(':')[-1]
                    xpath_expr = f".//*[local-name()='{tag_local}']"
                    cached_tags[key] = xpath_expr

                elements = root.xpath(xpath_expr)
                if elements:
                    val = elements[0].text
                    if val:
                        data[key] = val.strip()

            # キャッシュ更新
            if cache_key not in tag_cache:
                tag_cache[cache_key] = cached_tags
                save_tag_cache(tag_cache)

            return data

    except Exception as e:
        logger.error(f"  ❌ XBRL解析失敗: {e}")
        return {}


# ============================================================
# ★ v9.6.1: XBRLサマリー構築関数
# ============================================================
def _build_xbrl_summary(xbrl: Dict, prev_xbrl: Dict) -> str:
    """
    XBRLデータから財務サマリーを構築

    質問応答プロンプトに含めることで、LLMが正確な数値を参照できるようにする。
    """
    def fmt(val, suffix=""):
        if not val:
            return "N/A"
        try:
            num = float(val)
            if abs(num) >= 100000000:
                return f"{num / 100000000:.1f}億円{suffix}"
            elif abs(num) >= 10000:
                return f"{num / 10000:.1f}万円{suffix}"
            else:
                return f"{num:.0f}円{suffix}"
        except:
            return str(val)

    def fmt_pct(val):
        if not val:
            return "N/A"
        try:
            num = float(val)
            return f"{num:.1f}%"
        except:
            return str(val)

    def yoy_change(current, previous):
        if not current or not previous:
            return ""
        try:
            curr = float(current)
            prev = float(previous)
            if prev == 0:
                return ""
            change_pct = (curr - prev) / abs(prev) * 100
            sign = "+" if change_pct > 0 else ""
            return f" ({sign}{change_pct:.1f}% YoY)"
        except:
            return ""

    summary = []
    summary.append("【XBRL財務データ】")

    # 売上高
    revenue = xbrl.get("revenue")
    revenue_prev = prev_xbrl.get("revenue")
    if revenue:
        summary.append(f"売上高: {fmt(revenue)}{yoy_change(revenue, revenue_prev)}")

    # 営業利益
    op_profit = xbrl.get("operating_profit")
    op_profit_prev = prev_xbrl.get("operating_profit")
    if op_profit:
        summary.append(f"営業利益: {fmt(op_profit)}{yoy_change(op_profit, op_profit_prev)}")

    # 経常利益
    ordinary_profit = xbrl.get("ordinary_profit")
    ordinary_profit_prev = prev_xbrl.get("ordinary_profit")
    if ordinary_profit:
        summary.append(f"経常利益: {fmt(ordinary_profit)}{yoy_change(ordinary_profit, ordinary_profit_prev)}")

    # 当期純利益
    net_profit = xbrl.get("net_profit")
    net_profit_prev = prev_xbrl.get("net_profit")
    if net_profit:
        summary.append(f"当期純利益: {fmt(net_profit)}{yoy_change(net_profit, net_profit_prev)}")

    # ROE
    roe = xbrl.get("roe")
    if roe:
        summary.append(f"ROE: {fmt_pct(roe)}")

    # 自己資本比率
    equity_ratio = xbrl.get("equity_ratio")
    if equity_ratio:
        summary.append(f"自己資本比率: {fmt_pct(equity_ratio)}")

    # 営業CF
    operating_cf = xbrl.get("operating_cf")
    if operating_cf:
        summary.append(f"営業CF: {fmt(operating_cf)}")

    # 投資CF
    investing_cf = xbrl.get("investing_cf")
    if investing_cf:
        summary.append(f"投資CF: {fmt(investing_cf)}")

    # FCF
    fcf = xbrl.get("fcf")
    if fcf:
        summary.append(f"FCF: {fmt(fcf)}")

    return "\n".join(summary)


# ============================================================
# ★ v9.6.1: 質問応答方式のセクション分析
# ============================================================
def analyze_section_qa(
    section_key: str,
    full_text: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str
) -> Dict:
    """
    質問応答方式でセクションを分析（v9.6.1）

    Returns:
        {
            "section_key": str,
            "answers": [
                {
                    "question_id": str,
                    "question": str,
                    "answer": str,
                    "source": "qa"
                },
                ...
            ]
        }
    """
    section_config = SECTION_QUESTIONS.get(section_key)
    if not section_config:
        return {"section_key": section_key, "answers": []}

    questions = section_config.get("questions", [])
    if not questions:
        return {"section_key": section_key, "answers": []}

    # XBRLサマリー構築
    xbrl_summary = _build_xbrl_summary(xbrl, prev_xbrl)

    # 業種別フォーカスポイント
    industry_info = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])
    industry_focus = "\n".join([f"- {p}" for p in industry_info["focus_points"]])

    answers = []

    for q in questions:
        qid = q["id"]
        question_text = q["question"]
        focus = q.get("focus", "")

        prompt = f"""あなたは有価証券報告書の分析者です。以下のテキストから質問に答えてください。

# 財務データ（参考）
{xbrl_summary}

# 業種別フォーカスポイント
{industry_focus}

# 質問
{question_text}

# テキスト
{full_text[:15000]}

# 回答指示
- 質問に直接答えてください（前置きなし）
- 具体的な数値・金額・割合があれば必ず含めてください
- 情報が見つからない場合は「データなし」と答えてください
- 推測や憶測は避け、テキスト内の事実のみを記載してください
"""

        response = call_ollama(
            prompt,
            model=Config.OLLAMA_MODEL,
            num_ctx=Config.QA_NUM_CTX,
            num_predict=Config.QA_NUM_PREDICT,
            temperature=Config.QA_TEMPERATURE
        )

        if response and response.strip() and "データなし" not in response:
            answers.append({
                "question_id": qid,
                "question": question_text,
                "answer": response.strip(),
                "source": "qa"
            })

    return {
        "section_key": section_key,
        "answers": answers
    }


# ============================================================
# ★ v9.5.11: JSON抽出プロンプト生成
# ============================================================
def make_extraction_prompt(section_key: str, text: str, xbrl: Dict, prev_xbrl: Dict, industry: str) -> str:
    """JSON抽出用のプロンプト生成（v9.5.11）"""
    section_name = SECTION_MAPPING.get(section_key, {}).get("name", section_key)
    focus = SECTION_MAPPING.get(section_key, {}).get("extract_focus", "")

    # XBRLサマリー
    xbrl_summary = _build_xbrl_summary(xbrl, prev_xbrl)

    # 業種別フォーカス
    industry_info = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])
    industry_focus = "\n".join([f"- {p}" for p in industry_info["focus_points"]])

    prompt = f"""あなたは有価証券報告書の分析者です。以下のテキストから重要な情報を抽出してください。

# 分析対象セクション
{section_name}

# 抽出フォーカス
{focus}

# 財務データ（参考）
{xbrl_summary}

# 業種別フォーカスポイント
{industry_focus}

# テキスト
{text}

# 抽出指示
以下のJSON形式で出力してください：

{{
  "numbers": [
    {{"metric": "売上高", "value": "2535.8億円", "change": "+1.8%", "page": 3}},
    {{"metric": "営業利益", "value": "63.9億円", "change": "+37.3%", "page": 3}}
  ],
  "facts": [
    {{"fact": "中国向けホタテ輸出が約50億円増加", "page": 4}},
    {{"fact": "国産クロマグロ養殖事業の出荷体制が安定化", "page": 7}}
  ],
  "drivers": [
    {{"factor": "中国向けホタテ輸出増加", "impact": "プラス", "amount": "約50億円", "page": 4}},
    {{"factor": "原材料高騰・海上運賃上昇", "impact": "マイナス", "amount": "N/A", "page": 2}}
  ],
  "risks": [
    {{"risk": "食品の安全性の問題", "response": "品質保証体制の構築と維持管理", "page": 4}}
  ]
}}

# 重要な注意事項
- 具体的な数値・金額があれば必ず含めてください
- ページ番号は推定値でも構いません
- 情報がない項目は空配列 [] で返してください
- 必ずvalid JSONで出力してください（コメント不要）
"""
    return prompt


# ============================================================
# ★ v9.5.11: JSON抽出方式のセクション分析
# ============================================================
def process_section_json_extraction(
    pages: List[Tuple[int, str]],
    section_key: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str
) -> Dict:
    """
    JSON抽出方式でセクションを分析（v9.5.11）

    Returns:
        {
            "section_key": str,
            "extracted": {
                "numbers": [...],
                "facts": [...],
                "drivers": [...],
                "risks": [...]
            }
        }
    """
    full_text = "\n\n".join([f"[ページ {p}]\n{text}" for p, text in pages])

    # キーワードベース候補行抽出（MDAセクションは全文、その他はフィルタリング）
    if section_key == "03_MDA" and Config.MDA_NO_COMPRESS and len(full_text) < Config.MDA_FULLTEXT_LIMIT:
        compressed_text = full_text
    else:
        compressed_text = pick_candidate_lines_with_context(full_text)

    prompt = make_extraction_prompt(section_key, compressed_text, xbrl, prev_xbrl, industry)
    result = call_ollama_json(
        prompt,
        model=Config.OLLAMA_MODEL,
        num_ctx=Config.EXTRACT_NUM_CTX,
        num_predict=Config.EXTRACT_NUM_PREDICT,
        temperature=Config.TEMPERATURE
    )

    if not result:
        return {
            "section_key": section_key,
            "extracted": {
                "numbers": [],
                "facts": [],
                "drivers": [],
                "risks": []
            }
        }

    return {
        "section_key": section_key,
        "extracted": {
            "numbers": result.get("numbers", []),
            "facts": result.get("facts", []),
            "drivers": result.get("drivers", []),
            "risks": result.get("risks", [])
        }
    }


# ============================================================
# ★ v10: ハイブリッドモード（JSON抽出 + 質問応答）
# ============================================================
def normalize_page(page_val) -> str:
    """ページ番号を正規化"""
    if not page_val:
        return "N/A"
    page_str = str(page_val).strip().lower()
    if page_str in ["n/a", "na", "?", "qa", "unknown"]:
        return "N/A"
    # 数値のみ抽出
    match = re.search(r'\d+', page_str)
    if match:
        return match.group(0)
    return "N/A"


def normalize_impact(impact_val) -> str:
    """impactを正規化（プラス/マイナス/?）"""
    if not impact_val:
        return "?"
    impact_str = str(impact_val).strip().lower()
    if "プラス" in impact_str or "plus" in impact_str or "+" in impact_str or "増" in impact_str:
        return "プラス"
    if "マイナス" in impact_str or "minus" in impact_str or "-" in impact_str or "減" in impact_str:
        return "マイナス"
    return "?"


def process_section_hybrid(
    pages: List[Tuple[int, str]],
    section_key: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str
) -> Dict:
    """
    ハイブリッドモード：JSON抽出 + 質問応答の両方を実行し、マージ（v10.1改良版）

    v10.1改良点:
    - normalize_text() による重複検出の改善
    - normalize_page() / normalize_impact() の適用
    - is_boilerplate() による定型文フィルタリング

    Returns:
        {
            "section_key": str,
            "extracted": {
                "numbers": [...],
                "facts": [...],
                "drivers": [...],
                "risks": [...]
            },
            "qa_answers": [...]
        }
    """
    # Step 1: JSON抽出（v9.5.11）
    json_result = process_section_json_extraction(pages, section_key, xbrl, prev_xbrl, industry)
    json_extracted = json_result.get("extracted", {})

    # Step 2: 質問応答（v9.6.1） - 該当セクションのみ
    full_text = "\n\n".join([f"[ページ {p}]\n{text}" for p, text in pages])
    qa_answers = []
    if section_key in SECTION_QUESTIONS:
        qa_result = analyze_section_qa(section_key, full_text, xbrl, prev_xbrl, industry)
        qa_answers = qa_result.get("answers", [])

    # Step 3: マージ（重複除去）
    merged_extracted = {
        "numbers": json_extracted.get("numbers", []),
        "facts": json_extracted.get("facts", []),
        "drivers": json_extracted.get("drivers", []),
        "risks": json_extracted.get("risks", []),
    }

    # QA情報を追加（重複チェック強化 - v10.1）
    for ans in qa_answers:
        qid = ans.get("question_id", "")
        answer = ans.get("answer", "")

        if not answer or is_boilerplate(answer):
            continue

        # revenue_drivers / profit_drivers → drivers
        if qid in ["revenue_drivers", "profit_drivers"] and answer:
            # 正規化してから重複チェック
            answer_normalized = normalize_text(answer)
            existing_factors_normalized = {
                normalize_text(d.get("factor", ""))
                for d in merged_extracted["drivers"]
            }

            # 最初の100文字で重複判定（正規化後）
            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_factors_normalized):
                merged_extracted["drivers"].append({
                    "factor": answer[:500],  # 元のテキストを保存
                    "impact": normalize_impact("?"),
                    "amount": "N/A",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # cost_structure / price_pass_through / forex_impact → drivers
        elif qid in ["cost_structure", "price_pass_through", "forex_impact"] and answer:
            answer_normalized = normalize_text(answer)
            existing_factors_normalized = {
                normalize_text(d.get("factor", ""))
                for d in merged_extracted["drivers"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_factors_normalized):
                merged_extracted["drivers"].append({
                    "factor": answer[:500],
                    "impact": normalize_impact("?"),
                    "amount": "N/A",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # inventory_change → drivers
        elif qid == "inventory_change" and answer:
            answer_normalized = normalize_text(answer)
            existing_factors_normalized = {
                normalize_text(d.get("factor", ""))
                for d in merged_extracted["drivers"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_factors_normalized):
                merged_extracted["drivers"].append({
                    "factor": f"在庫変動: {answer[:400]}",
                    "impact": normalize_impact("?"),
                    "amount": "N/A",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # segment_performance / segment_sales / segment_profit → drivers
        elif qid in ["segment_performance", "segment_sales", "segment_profit"] and answer:
            answer_normalized = normalize_text(answer)
            existing_factors_normalized = {
                normalize_text(d.get("factor", ""))
                for d in merged_extracted["drivers"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_factors_normalized):
                merged_extracted["drivers"].append({
                    "factor": answer[:500],
                    "impact": normalize_impact("?"),
                    "amount": "N/A",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # key_events → facts
        elif qid == "key_events" and answer:
            # イベント情報をスコアリング
            event_score = score_event_text(answer)
            if event_score > 0.5:  # スコアが0.5以上なら採用
                answer_normalized = normalize_text(answer)
                existing_facts_normalized = {
                    normalize_text(f.get("fact", ""))
                    for f in merged_extracted["facts"]
                }

                answer_prefix = answer_normalized[:100]
                if not any(answer_prefix in existing for existing in existing_facts_normalized):
                    merged_extracted["facts"].append({
                        "fact": answer[:500],
                        "page": normalize_page("QA"),
                        "source": "qa",
                    })

        # risks → risks
        elif qid == "risks" and answer:
            answer_normalized = normalize_text(answer)
            existing_risks_normalized = {
                normalize_text(r.get("risk", ""))
                for r in merged_extracted["risks"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_risks_normalized):
                merged_extracted["risks"].append({
                    "risk": answer[:500],
                    "response": "N/A",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # mid_term_plan / competitive_advantage → facts
        elif qid in ["mid_term_plan", "competitive_advantage"] and answer:
            answer_normalized = normalize_text(answer)
            existing_facts_normalized = {
                normalize_text(f.get("fact", ""))
                for f in merged_extracted["facts"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_facts_normalized):
                merged_extracted["facts"].append({
                    "fact": answer[:500],
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # outlook → facts
        elif qid == "outlook" and answer:
            answer_normalized = normalize_text(answer)
            existing_facts_normalized = {
                normalize_text(f.get("fact", ""))
                for f in merged_extracted["facts"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_facts_normalized):
                merged_extracted["facts"].append({
                    "fact": f"見通し: {answer[:400]}",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # employee_change → facts
        elif qid == "employee_change" and answer:
            answer_normalized = normalize_text(answer)
            existing_facts_normalized = {
                normalize_text(f.get("fact", ""))
                for f in merged_extracted["facts"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_facts_normalized):
                merged_extracted["facts"].append({
                    "fact": f"従業員: {answer[:400]}",
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

        # capex / rd → facts
        elif qid in ["capex", "rd"] and answer:
            answer_normalized = normalize_text(answer)
            existing_facts_normalized = {
                normalize_text(f.get("fact", ""))
                for f in merged_extracted["facts"]
            }

            answer_prefix = answer_normalized[:100]
            if not any(answer_prefix in existing for existing in existing_facts_normalized):
                merged_extracted["facts"].append({
                    "fact": answer[:500],
                    "page": normalize_page("QA"),
                    "source": "qa",
                })

    return {
        "section_key": section_key,
        "extracted": merged_extracted,
        "qa_answers": qa_answers
    }


# ============================================================
# ★ v9.6.1: QAモード（質問応答のみ）
# ============================================================
def process_section_qa_mode(
    pages: List[Tuple[int, str]],
    section_key: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str
) -> Dict:
    """
    QAモード：質問応答のみを実行（v9.6.1）

    Returns:
        {
            "section_key": str,
            "qa_answers": [...]
        }
    """
    full_text = "\n\n".join([f"[ページ {p}]\n{text}" for p, text in pages])

    if section_key in SECTION_QUESTIONS:
        qa_result = analyze_section_qa(section_key, full_text, xbrl, prev_xbrl, industry)
        return {
            "section_key": section_key,
            "qa_answers": qa_result.get("answers", [])
        }
    else:
        return {
            "section_key": section_key,
            "qa_answers": []
        }


# ============================================================
# ★ v10.1: セクション処理のエントリーポイント（BM25統合確認済み）
# ============================================================
def process_section(
    section_folder: Path,
    section_key: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    industry: str,
    extraction_mode: str
) -> Dict:
    r"""
    セクション処理のエントリーポイント（v10.1改良版）

    v10.1改良点:
    - BM25-based PDF selectionの確実な統合
    - manifest未検出時のフォールバック（全PDF読み込み）

    Args:
        section_folder: セクションフォルダのパス（例: E:\PDF\sections_test\1301_株式会社　極洋\2022_有報）
        section_key: セクションID（例: "02_経営戦略_リスク"）
        xbrl: 当期XBRLデータ
        prev_xbrl: 前期XBRLデータ
        industry: 業種コード
        extraction_mode: "qa" | "json" | "hybrid"

    Returns:
        処理結果の辞書
    """
    # manifest.json読み込み
    manifest = load_manifest(section_folder)

    # セクション別ディレクトリ
    section_dir = section_folder / section_key

    if section_dir.exists() and section_dir.is_dir():
        # ★ v10.1: BM25-based PDF selection
        if Config.ENABLE_PDF_FILTERING and manifest:
            section_question = SECTION_MAPPING[section_key].get("extract_focus", "")

            # セクション別top_k調整
            if section_key == "05_セグメント":
                top_k_val = 5
                min_guaranteed_val = 2
            elif section_key in ["02_経営戦略_リスク", "03_MDA"]:
                top_k_val = 4
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

            if selected_paths:
                # 選択されたPDFのみ読み込み
                all_pages = []
                for pdf_path_rel in selected_paths:
                    pdf_path = section_folder / pdf_path_rel
                    if pdf_path.exists():
                        pages = extract_text_from_pdf_with_pages(pdf_path)
                        if pages:
                            offset = len(all_pages)
                            all_pages.extend([(p + offset, text) for p, text in pages])

                if all_pages:
                    logger.info(f"  ✅ {section_key}: {len(selected_paths)}ファイル選択, {len(all_pages)}ページ読み込み")
                else:
                    logger.warning(f"  ⚠️ {section_key}: PDF読み込み失敗、スキップ")
                    return {"section_key": section_key, "extracted": {}, "qa_answers": []}
            else:
                # manifest検出できなかった場合のフォールバック: 全PDF読み込み
                logger.warning(f"  ⚠️ {section_key}: manifest未検出、全PDF読み込み")
                pdf_files = sorted(section_dir.glob("*.pdf"))
                all_pages = []
                for pdf_file in pdf_files:
                    pages = extract_text_from_pdf_with_pages(pdf_file)
                    if pages:
                        offset = len(all_pages)
                        all_pages.extend([(p + offset, text) for p, text in pages])

                if not all_pages:
                    logger.warning(f"  ⚠️ {section_key}: PDF読み込み失敗、スキップ")
                    return {"section_key": section_key, "extracted": {}, "qa_answers": []}
        else:
            # PDF filtering無効時: 全PDF読み込み
            pdf_files = sorted(section_dir.glob("*.pdf"))
            all_pages = []
            for pdf_file in pdf_files:
                pages = extract_text_from_pdf_with_pages(pdf_file)
                if pages:
                    offset = len(all_pages)
                    all_pages.extend([(p + offset, text) for p, text in pages])

            if not all_pages:
                logger.warning(f"  ⚠️ {section_key}: PDF読み込み失敗、スキップ")
                return {"section_key": section_key, "extracted": {}, "qa_answers": []}

            logger.info(f"  ✅ {section_key}: {len(pdf_files)}ファイル, {len(all_pages)}ページ読み込み")
    else:
        # 旧形式（単一PDF）への後方互換性
        pdf_path = section_folder / f"{section_key}.pdf"
        if pdf_path.exists():
            all_pages = extract_text_from_pdf_with_pages(pdf_path)
            if not all_pages:
                logger.warning(f"  ⚠️ {section_key}: PDF読み込み失敗、スキップ")
                return {"section_key": section_key, "extracted": {}, "qa_answers": []}
            logger.info(f"  ✅ {section_key}: {len(all_pages)}ページ読み込み（旧形式）")
        else:
            logger.warning(f"  ⚠️ {section_key}: PDFが見つかりません、スキップ")
            return {"section_key": section_key, "extracted": {}, "qa_answers": []}

    # モード別処理
    if extraction_mode == "hybrid":
        return process_section_hybrid(all_pages, section_key, xbrl, prev_xbrl, industry)
    elif extraction_mode == "qa":
        return process_section_qa_mode(all_pages, section_key, xbrl, prev_xbrl, industry)
    elif extraction_mode == "json":
        return process_section_json_extraction(all_pages, section_key, xbrl, prev_xbrl, industry)
    else:
        logger.error(f"  ❌ 不明な抽出モード: {extraction_mode}")
        return {"section_key": section_key, "extracted": {}, "qa_answers": []}


# ============================================================
# ★ v10.1: レポート生成（v9.6.1ベース、v10統合）
# ============================================================
def generate_final_report_v10(
    company_name: str,
    year: int,
    doc_type: str,
    industry: str,
    xbrl: Dict,
    prev_xbrl: Dict,
    section_results: List[Dict],
    extraction_mode: str
) -> str:
    """
    最終レポート生成（v10.1統合版）

    - v9.6.1のレポート形式を維持
    - v9.5.11のJSON抽出結果も統合
    - v10のハイブリッドモード対応
    """
    # XBRLサマリー
    xbrl_summary = _build_xbrl_summary(xbrl, prev_xbrl)

    # 業種別情報
    industry_info = INDUSTRY_PROMPTS.get(industry, INDUSTRY_PROMPTS["all"])
    industry_name = industry_info["name"]
    industry_focus = "\n".join([f"- {p}" for p in industry_info["focus_points"]])

    # セクション別情報の整理
    section_summaries = []
    for result in section_results:
        section_key = result.get("section_key", "")
        section_name = SECTION_MAPPING.get(section_key, {}).get("name", section_key)

        summary_parts = []
        summary_parts.append(f"### {section_name}")

        # JSON抽出結果
        extracted = result.get("extracted", {})
        if extracted:
            numbers = extracted.get("numbers", [])
            facts = extracted.get("facts", [])
            drivers = extracted.get("drivers", [])
            risks = extracted.get("risks", [])

            if numbers:
                summary_parts.append("\n**数値情報:**")
                for n in numbers[:5]:
                    metric = n.get("metric", "")
                    value = n.get("value", "")
                    change = n.get("change", "")
                    page = normalize_page(n.get("page", ""))
                    summary_parts.append(f"- {metric}: {value} {change} (P.{page})")

            if facts:
                summary_parts.append("\n**重要事実:**")
                for f in facts[:5]:
                    fact = f.get("fact", "")
                    page = normalize_page(f.get("page", ""))
                    summary_parts.append(f"- {fact} (P.{page})")

            if drivers:
                summary_parts.append("\n**業績変動要因:**")
                for d in drivers[:5]:
                    factor = d.get("factor", "")
                    impact = normalize_impact(d.get("impact", "?"))
                    amount = d.get("amount", "N/A")
                    page = normalize_page(d.get("page", ""))
                    if amount != "N/A":
                        summary_parts.append(f"- {factor} ({impact}) {amount} (P.{page})")
                    else:
                        summary_parts.append(f"- {factor} ({impact}) (P.{page})")

            if risks:
                summary_parts.append("\n**リスク:**")
                for r in risks[:3]:
                    risk = r.get("risk", "")
                    response = r.get("response", "")
                    page = normalize_page(r.get("page", ""))
                    if response != "N/A":
                        summary_parts.append(f"- {risk}: {response} (P.{page})")
                    else:
                        summary_parts.append(f"- {risk} (P.{page})")

        # QA結果
        qa_answers = result.get("qa_answers", [])
        if qa_answers:
            summary_parts.append("\n**質問応答結果:**")
            for ans in qa_answers[:3]:
                qid = ans.get("question_id", "")
                answer = ans.get("answer", "")
                if answer:
                    summary_parts.append(f"- [{qid}] {answer[:200]}...")

        if len(summary_parts) > 1:
            section_summaries.append("\n".join(summary_parts))

    sections_text = "\n\n".join(section_summaries)

    # プロンプト生成
    prompt = f"""あなたはプロの財務アナリストです。以下の情報をもとに、投資銀行品質の業績分析レポートを作成してください。

# 会社情報
- 会社名: {company_name}
- 年度: {year}
- 種別: {doc_type}
- 業種: {industry_name}

# 財務データ（XBRL）
{xbrl_summary}

# セクション別抽出情報
{sections_text}

# 業種別フォーカスポイント
{industry_focus}

# レポート作成指示

以下の形式で、投資家が読みやすい日本語レポートを作成してください：

---
# {company_name} 業績レポート（v10）

**年度**: {year} / **種別**: {doc_type} / **業種**: {industry_name}

**生成モデル**: {Config.OLLAMA_MODEL_FINAL} / **抽出モード**: {extraction_mode}

---

# {company_name}｜{year}年 詳細業績レポート（v10）

## 数字で見る{year}年
- 売上高: XXX億円 (+X.X%) (XBRL)
- 営業利益: XX億円 (+XX.X%) (XBRL)
- EBITDA: XX億円 / EBITDAマージン: X.X% (XBRL)
- 純利益: XX億円 (+XX.X%) (XBRL)
- 粗利率: XX.X% / 営業利益率: X.X% (XBRL)
- 営業CF: XX億円 (XBRL)
- FCF: XX億円（Capex XX億円） (XBRL)
- 自己資本比率: XX.X% (前年: XX.X%, 差分: ±X.Xpt) (XBRL)
- 現金及び現金同等物（Cash & Equivalents）: XX億円 (XBRL)
- 有利子負債（Interest-bearing Debt）: XXX億円 (XBRL)
- Net Debt（有利子負債－現金）: XXX億円 (XBRL)
- Net Debt/EBITDA: X.XXx (XBRL)
- D/Eレシオ: X.XXx (XBRL)
- ROE: XX.X% / ROA: X.X% / ROIC: X.X% (XBRL)
- 1株配当: XXX円 (XBRL)
- 配当性向: XX.X% (XBRL)

## 📈 時系列推移（XBRL）
| 項目 | {year} | {year-1} | {year-2} | {year-3} |
|---|---|---|---|---|
| 売上高 | XXX億 | XXX億 | XXX億 | XXX億 |
| 営業利益 | XX億 | XX億 | XX億 | XX億 |
| EBITDA | XX億 | XX億 | XX億 | XX億 |
| 純利益 | XX億 | XX億 | XX億 | XX億 |
| 営業利益率 | X.X% | X.X% | X.X% | X.X% |
| ROE | XX.X% | XX.X% | XX.X% | XX.X% |
| ROIC | X.X% | X.X% | X.X% | X.X% |
| 自己資本比率 | XX.X% | XX.X% | XX.X% | XX.X% |
| 営業CF | XX億 | XX億 | XX億 | XX億 |
| FCF | XX億 | XX億 | XX億 | XX億 |
| Net Debt/EBITDA | X.Xx | X.Xx | X.Xx | X.Xx |

## 今年起きたこと（トップ3）
1. XXX (プラス/マイナス) XXX億円 (P.X)
2. XXX (P.X)
3. XXX (P.X)

## 業績変化の理由

### 売上高 +X.X%の要因
- XXX (P.X)
- XXX (P.X)

### 営業利益 +XX.X%の要因
- XXX (P.X)
- XXX (P.X)

### コスト構造の変化
- 粗利率: XX.X% (前年: XX.X%, +X.Xpt) (XBRL)
- XXX (P.X)

### 為替・価格転嫁の影響
- XXX (P.X)

## セグメント別ハイライト
- **XXXセグメント**:
  - 売上高: +X.X% (XXX億円)
  - 営業利益: +XX.X% (XX億円)
  - **好調/不調**: XXX (P.X)

## 投資・財務の状況

### 設備投資・研究開発
- 設備投資額: XXX億円 (データなし/XBRL)
- 研究開発費: XXX億円 (データなし/XBRL)
- 主な投資内容: XXX (P.X)

### 財務体質・キャッシュフロー
- 自己資本比率: XX.X% (前年比±X.Xpt) (XBRL)
- Net Debt/EBITDA: X.XXx (XBRL)
- 営業CF: XX億円 (XBRL)
- 営業CFの増減理由: XXX (P.X)

### 株主還元
- 1株配当: XXX円 (XBRL)
- 配当性向: XX.X% (XBRL)
- 自社株買い: XXX (データなし/P.X)

## 感応度分析

### 為替感応度
- XXX (データなし/P.X)

### 原材料価格感応度
- XXX (データなし/P.X)

## 経営戦略・今後の見通し

### 中期経営計画のポイント
- XXX (P.X)

### 来期の見通し
- XXX (データなし/P.X)

## リスクと対応
1. **XXX** (P.X)
   - **対応策**: XXX

---

## 📊 数値整合性チェック

### 全社KPI整合性

| 項目 | XBRL（連結） | レポート | 一致 |
|------|-------------|----------|------|
| 売上高 | XXX億円 | XXX億 | ✅/❌ |
| 営業利益 | XX億円 | XX億 | ✅/❌ |
| 純利益 | XX億円 | XX億 | ✅/❌ |

---

# 重要な制約（必ず守ること）

## 1. 数値の取り扱い
- **XBRLデータを最優先**で使用してください
- XBRLにない数値は「データなし」「N/A」と明記してください
- **絶対に数値を推測しないでください**
- 営業CFがマイナスの場合、「-XX億円」と明記してください（プラス表記しない）
- FCF算出不可の場合は「FCF算出不可（Capex未取得、営業CF=XX億円）」と記載してください

## 2. セグメント数値の扱い
- セグメント別の売上高・営業利益は、必ずセグメント情報から取得してください
- **会社全体のYoY（前年比）をセグメントに流用してはいけません**
- セグメント数値が取得できない場合は「N/A」と明記してください
- セグメントの好調/不調は、セグメント情報に基づいて判断してください

例（正しい書き方）:
- **水産商事セグメント**:
  - 売上高: +1.6% (1,207.96億円)
  - 営業利益: +67.9% (51.5億円)
  - **好調**: 中国向けホタテ輸出と北米現地販売の回復、国内加工品需要の増加 (P.2)

例（誤った書き方 - 絶対NG）:
- **水産商事セグメント**:
  - 売上高: +1.8% ← 会社全体の数値を流用（NG）

## 3. ページ参照
- すべての事実・数値には必ず「(P.X)」または「(XBRL)」を付けてください
- ページ番号が不明の場合は「(データなし)」と明記してください

## 4. 用語の統一
- 「億円」単位で統一してください（例: 2535.8億円）
- 「前年比」「YoY」を統一してください（例: +1.8%）
- 「営業CF」「投資CF」「FCF」の略語を使用してください

## 5. データなしの表現
- 「データなし」「N/A」「算出不可」を明確に区別してください
- 例:
  - XBRLタグがない → 「データなし」
  - PDFにも記載なし → 「N/A」
  - 計算に必要な要素がない → 「算出不可（理由）」

## 6. EBITDA計算
- EBITDA = 営業利益 + 減価償却費
- 減価償却費が取得できない場合は「EBITDA算出不可（減価償却費未取得）」と記載

## 7. D/Eレシオ計算
- D/Eレシオ = 有利子負債 ÷ 自己資本（倍率）
- **×100してはいけません**（例: 1.01x が正しい、101% は誤り）

## 8. 配当計算
- 1株配当 = 配当総額 ÷ 発行済株式数
- 配当性向 = 配当総額 ÷ 純利益 × 100

## 9. 推定表現の禁止（営業CF分析）
- **「推定 (データなし)」の組み合わせは絶対禁止**
- 営業CFの増減理由が不明の場合は、「営業CFの増減理由: データなし」と明記
- 推測・憶測を含む表現（「～と推定」「～と考えられる」）は、データがない場合は使用しないでください

例（正しい書き方）:
- 営業CF: -11.3億円 (XBRL)
- **営業CFマイナスの原因**:
  - 純利益が46.3億円（プラス）にもかかわらず、営業CFがマイナス（差額: 57.6億円）
  - **運転資本増加**（売掛金・在庫増加による現金流出）が主因と推定 (XBRL)

例（誤った書き方 - 絶対NG）:
- 営業CF: -11.3億円 (XBRL)
- **営業CFマイナスの原因**: 運転資本増加が主因と推定 (データなし) ← 矛盾

## 10. 出力形式
- マークダウン形式で出力してください
- 表は必ず「|」で区切ってください
- 見出しは「#」「##」「###」を使用してください

---
"""

    logger.info(f"  📝 最終レポート生成中...")
    report = call_ollama(
        prompt,
        model=Config.OLLAMA_MODEL_FINAL,
        num_ctx=Config.FINAL_NUM_CTX,
        num_predict=Config.FINAL_NUM_PREDICT,
        temperature=Config.FINAL_TEMPERATURE
    )

    if not report:
        logger.error("  ❌ レポート生成失敗")
        return ""

    # 簡易的な誤字修正
    report = report.replace("営営業", "営業")
    report = report.replace("利益益", "利益")
    report = report.replace("売上上", "売上")

    return report


# ============================================================
# メイン処理
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="PORTA v10 - 完全統合版")
    parser.add_argument("--company", help="会社コード（例: 1301）")
    parser.add_argument("--year", type=int, help="年度（例: 2022）")
    parser.add_argument("--type", default="有報", help="種別（例: 有報）")
    parser.add_argument("--industry", default="food", help="業種（例: food）")
    parser.add_argument("--mode", default=Config.EXTRACTION_MODE,
                        help="抽出モード（qa/json/hybrid、デフォルト: hybrid）")

    args = parser.parse_args()

    # インタラクティブモード
    if not args.company or not args.year:
        print("=" * 80)
        print("PORTA v10 - インタラクティブモード")
        print("=" * 80)
        print("種別: 1=有報, 2=四半期, 3=半期, 4=訂正有報")
        print()

        company_code = input("会社コード（例: 1301）: ").strip()
        year = int(input("年度（例: 2022）: ").strip())

        # 種別の選択（数字対応）
        doc_type_input = input("種別（1-4、またはEnterで有報）: ").strip()
        doc_type_map = {
            "1": "有報",
            "2": "四半期",
            "3": "半期",
            "4": "訂正有報",
        }
        if doc_type_input in doc_type_map:
            doc_type = doc_type_map[doc_type_input]
        elif doc_type_input == "":
            doc_type = "有報"
        else:
            doc_type = doc_type_input

        industry = input("業種（例: food、デフォルト: all）: ").strip().lower() or "all"
        mode = input("抽出モード（qa/json/hybrid、デフォルト: hybrid）: ").strip().lower() or "hybrid"
    else:
        company_code = args.company
        year = args.year
        doc_type = args.type
        industry = args.industry.lower()
        mode = args.mode.lower()

    # セクションフォルダの特定
    # パターン: {SECTIONS_BASE}/{company_code}_{company_name}/{year}_{doc_type}
    section_base_folders = list(Config.SECTIONS_BASE.glob(f"{company_code}_*"))
    if not section_base_folders:
        logger.error(f"  ❌ セクションフォルダが見つかりません: {company_code}")
        return

    company_folder = section_base_folders[0]
    company_name = company_folder.name.replace(f"{company_code}_", "")

    section_folder = company_folder / f"{year}_{doc_type}"
    if not section_folder.exists():
        logger.error(f"  ❌ セクションフォルダが見つかりません: {section_folder}")
        return

    logger.info(f"  📂 セクションフォルダ: {section_folder}")

    # XBRLデータ読み込み
    logger.info(f"  📊 XBRLデータ読み込み中...")
    taxonomy_mapping = get_taxonomy_mapping()

    xbrl_zip = Config.XBRL_BASE / f"{company_code}_{year}_{doc_type}.zip"
    prev_xbrl_zip = Config.XBRL_BASE / f"{company_code}_{year-1}_{doc_type}.zip"

    xbrl = extract_xbrl_data(xbrl_zip, taxonomy_mapping) if xbrl_zip.exists() else {}
    prev_xbrl = extract_xbrl_data(prev_xbrl_zip, taxonomy_mapping) if prev_xbrl_zip.exists() else {}

    logger.info(f"  ✅ XBRL: {len(xbrl)}件, 前期XBRL: {len(prev_xbrl)}件")

    # セクション処理
    logger.info(f"  🔍 セクション処理開始（モード: {mode}）...")
    section_results = []

    for section_key in SECTION_MAPPING.keys():
        logger.info(f"  📖 {section_key} 処理中...")
        result = process_section(
            section_folder,
            section_key,
            xbrl,
            prev_xbrl,
            industry,
            mode
        )
        section_results.append(result)

    # レポート生成
    logger.info(f"  📝 最終レポート生成中...")
    report = generate_final_report_v10(
        company_name,
        year,
        doc_type,
        industry,
        xbrl,
        prev_xbrl,
        section_results,
        mode
    )

    # レポート保存
    output_dir = Path(f"./financial_analysis_system/output_v10_{mode}")
    output_dir.mkdir(exist_ok=True, parents=True)

    company_output_dir = output_dir / f"{company_code}_{company_name}"
    company_output_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = company_output_dir / f"porta10_{company_code}_{year}_{doc_type}_{timestamp}.md"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)

    logger.info(f"  ✅ レポート保存: {output_file}")
    print("\n" + "=" * 80)
    print(report)
    print("=" * 80)


if __name__ == "__main__":
    main()
