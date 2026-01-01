# Financial Analysis System

有価証券報告書の分析システム - 数値とテキストを分離したアーキテクチャ

## 🏗️ システム構成

```
financial_analysis_system/
├── README.md                    # このファイル
├── config.py                    # 共通設定
│
├── pipeline_numeric/            # 数値パイプライン（XBRL）
│   ├── __init__.py
│   ├── xbrl_extractor.py       # XBRL数値抽出
│   ├── validator.py            # 異常値検知
│   └── db_writer.py            # Supabase書き込み
│
├── pipeline_text/               # テキストパイプライン（PDF/RAPTOR）
│   ├── __init__.py
│   ├── pdf_extractor.py        # PDF読み込み
│   ├── raptor_builder.py       # RAPTORツリー構築
│   └── chromadb_writer.py      # ChromaDB書き込み
│
├── analyzer/                    # 統合分析エンジン
│   ├── __init__.py
│   ├── query_engine.py         # クエリ処理
│   └── report_generator.py     # レポート生成
│
└── utils/                       # ユーティリティ
    ├── __init__.py
    ├── ollama_client.py        # Ollama API
    └── parallel.py             # 並列処理
```

## 📊 データフロー

### 1. 数値パイプライン（信頼性重視）
```
XBRL/EDINET API
    ↓
xbrl_extractor.py（構造化抽出）
    ↓
validator.py（異常値検知・単位統一）
    ↓
Supabase（数値DB）
    ↓
固定値として分析に使用
```

### 2. テキストパイプライン（解釈重視）
```
有価証券報告書PDF
    ↓
pdf_extractor.py（テキスト抽出）
    ↓
raptor_builder.py（階層要約）
    ↓
ChromaDB（ベクトルDB）
    ↓
RAG検索で分析に使用
```

### 3. 統合分析
```
ユーザークエリ
    ↓
┌───────────────────────────────────┐
│         統合分析エンジン            │
│                                   │
│  数値DB参照 ← 売上、利益など固定値  │
│  テキストDB検索 ← 関連セクション    │
│  LLM分析 ← 数値+テキストで解釈     │
└───────────────────────────────────┘
    ↓
分析レポート（数値は検証済み、解釈はLLM）
```

## 🔧 主要コンポーネント

### 数値パイプライン
- **目的**: 正確な財務数値の抽出・保存
- **データソース**: XBRL（EDINETから取得済み）
- **検証ルール**: 異常値検知、単位統一、前年比チェック
- **保存先**: Supabase（既存のStockFlowインフラ活用）

### テキストパイプライン
- **目的**: 定性情報の階層的要約・検索
- **データソース**: 有価証券報告書PDF
- **処理**: RAPTOR（再帰的要約）
- **保存先**: ChromaDB（ベクトル検索）

### 統合分析エンジン
- **役割分担**:
  - 数値 → DBから固定値を取得（LLMに生成させない）
  - テキスト → RAG検索で関連セクションを取得
  - LLM → 数値とテキストを組み合わせて解釈・説明

## 📈 異常値検知ルール

| 項目 | 正常範囲 | 異常時の処理 |
|------|---------|-------------|
| 平均年齢 | 20〜70歳 | 再抽出・手動確認 |
| 平均給与 | 100万〜3000万円 | 単位確認 |
| 売上高前年比 | 0.5〜2.0倍 | 特殊要因確認 |
| 営業利益率 | -50%〜50% | セグメント確認 |
| ROE | -100%〜100% | 計算根拠確認 |

## 🚀 使用方法

```python
from financial_analysis_system import FinancialAnalyzer

# 初期化
analyzer = FinancialAnalyzer()

# 数値パイプライン実行
analyzer.run_numeric_pipeline(edinet_code="E00014", year=2024)

# テキストパイプライン実行
analyzer.run_text_pipeline(pdf_path="Toyota2024.pdf")

# 統合分析
result = analyzer.analyze(
    query="トヨタの2024年度の業績と今後の見通しは？",
    edinet_code="E00014"
)
```

## 📝 論文での位置づけ

このシステムは以下の貢献を主張できる：

1. **数値とテキストの分離**: LLMの幻覚問題を構造的に回避
2. **Financial RAPTOR**: 有価証券報告書に特化した階層的要約
3. **異常値検知**: 財務データ特有のバリデーション
4. **ハイブリッドRAG**: 構造化データとベクトル検索の統合