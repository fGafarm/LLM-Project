#!/usr/bin/env python3
"""
統合分析エンジン
- 数値DB（Supabase/JSON）から検証済み数値を取得
- テキストDB（ChromaDB）から関連テキストをRAG検索
- LLMで統合分析（数値は固定、解釈のみLLM）
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class AnalysisContext:
    """分析コンテキスト"""
    company_name: str
    edinet_code: str
    fiscal_year: str
    
    # 数値DB（検証済み）
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    # テキストDB（RAG検索結果）
    relevant_texts: List[Dict[str, Any]] = field(default_factory=list)
    
    # RAPTOR要約
    executive_summary: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """分析結果"""
    query: str
    answer: str
    
    # 参照情報
    metrics_used: List[str] = field(default_factory=list)
    texts_used: List[str] = field(default_factory=list)
    
    # メタデータ
    confidence: str = "medium"  # high/medium/low
    caveats: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class IntegratedAnalyzer:
    """統合分析エンジン"""
    
    def __init__(self, 
                 numeric_db_path: Path = None,
                 chromadb_path: Path = None,
                 ollama_base_url: str = "http://localhost:11434",
                 ollama_model: str = "gemma2:27b"):
        
        self.numeric_db_path = numeric_db_path
        self.chromadb_path = chromadb_path
        self.ollama_base_url = ollama_base_url
        self.ollama_model = ollama_model
        
        # キャッシュ
        self._numeric_cache: Dict[str, Dict] = {}
        self._chromadb_client = None
    
    # ========================================
    # 数値DB操作
    # ========================================
    def load_metrics(self, edinet_code: str, fiscal_year: str) -> Optional[Dict]:
        """検証済み数値を読み込み"""
        
        cache_key = f"{edinet_code}_{fiscal_year}"
        
        if cache_key in self._numeric_cache:
            return self._numeric_cache[cache_key]
        
        if self.numeric_db_path:
            # JSONファイルから読み込み
            json_path = self.numeric_db_path / f"{edinet_code}_{fiscal_year}_metrics.json"
            
            if json_path.exists():
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._numeric_cache[cache_key] = data
                    return data
        
        return None
    
    def get_validated_metric(self, metrics: Dict, metric_name: str) -> Optional[Dict]:
        """検証済み数値を取得（エラーのものは除外）"""
        
        if metric_name not in metrics:
            return None
        
        metric = metrics[metric_name]
        
        if isinstance(metric, dict):
            # ステータスがerrorの場合はNoneを返す
            if metric.get('status') == 'error':
                return None
            return metric
        
        return {'value': metric, 'unit': '', 'status': 'unknown'}
    
    # ========================================
    # テキストDB操作
    # ========================================
    def search_texts(self, query: str, company: str = None, 
                    n_results: int = 5) -> List[Dict]:
        """ChromaDBから関連テキストを検索"""
        
        if self.chromadb_path is None:
            return []
        
        try:
            from pipeline_text.text_pipeline import ChromaDBWriter
            
            writer = ChromaDBWriter(self.chromadb_path)
            results = writer.search(query, n_results=n_results, company=company)
            return results
        
        except Exception as e:
            print(f"⚠️ テキスト検索エラー: {e}")
            return []
    
    def load_raptor_tree(self, company_name: str) -> Optional[Dict]:
        """RAPTORツリーを読み込み"""
        
        if self.numeric_db_path is None:
            return None
        
        # JSONファイルを検索
        safe_name = company_name.replace(' ', '_')[:50]
        json_path = self.numeric_db_path / f"{safe_name}_raptor_tree.json"
        
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        return None
    
    # ========================================
    # コンテキスト構築
    # ========================================
    def build_context(self, edinet_code: str, fiscal_year: str,
                     query: str = None) -> AnalysisContext:
        """分析コンテキストを構築"""
        
        context = AnalysisContext(
            company_name="",
            edinet_code=edinet_code,
            fiscal_year=fiscal_year,
        )
        
        # 数値ロード
        metrics = self.load_metrics(edinet_code, fiscal_year)
        if metrics:
            context.company_name = metrics.get('company_name', '')
            context.metrics = metrics
        
        # RAPTORツリーロード
        if context.company_name:
            tree = self.load_raptor_tree(context.company_name)
            if tree:
                root = tree.get('level_2_root', {})
                context.executive_summary = root.get('executive_summary', '')
                context.strengths = root.get('strengths', [])
                context.weaknesses = root.get('weaknesses', [])
        
        # クエリに関連するテキストを検索
        if query:
            context.relevant_texts = self.search_texts(
                query, company=context.company_name, n_results=5
            )
        
        return context
    
    # ========================================
    # 分析実行
    # ========================================
    def analyze(self, query: str, edinet_code: str, 
               fiscal_year: str) -> AnalysisResult:
        """クエリに対して統合分析を実行"""
        
        # コンテキスト構築
        context = self.build_context(edinet_code, fiscal_year, query)
        
        # プロンプト構築
        prompt = self._build_analysis_prompt(query, context)
        
        # LLM呼び出し
        response = self._call_llm(prompt)
        
        # 結果構築
        result = AnalysisResult(
            query=query,
            answer=response,
            metrics_used=list(context.metrics.keys()) if context.metrics else [],
            texts_used=[t.get('id', '') for t in context.relevant_texts],
        )
        
        return result
    
    def _build_analysis_prompt(self, query: str, 
                               context: AnalysisContext) -> str:
        """分析用プロンプトを構築"""
        
        # 数値部分（検証済みのみ）
        metrics_text = self._format_metrics(context.metrics)
        
        # テキスト部分
        texts_text = "\n".join([
            f"- {t.get('metadata', {}).get('section', '不明')}: {t.get('document', '')[:200]}..."
            for t in context.relevant_texts[:3]
        ])
        
        # RAPTOR要約
        summary_text = context.executive_summary or "（要約なし）"
        
        prompt = f"""あなたは財務分析の専門家です。
以下の情報に基づいて、ユーザーの質問に答えてください。

【企業】{context.company_name} ({context.edinet_code})
【年度】{context.fiscal_year}

【検証済み財務数値】
{metrics_text}

【関連テキスト（有価証券報告書より）】
{texts_text}

【企業概要】
{summary_text}

【強み】
{chr(10).join(f'- {s}' for s in context.strengths) if context.strengths else '（データなし）'}

【リスク・課題】
{chr(10).join(f'- {w}' for w in context.weaknesses) if context.weaknesses else '（データなし）'}

---

【重要なルール】
1. 上記の「検証済み財務数値」に記載された数値のみを使用してください
2. 数値が「（データなし）」の項目は、推測や概算で補わないでください
3. テキスト情報から数値を引用しないでください（信頼性が低いため）
4. 不明な点は「データがないため不明です」と明記してください

---

【ユーザーの質問】
{query}

【回答】"""
        
        return prompt
    
    def _format_metrics(self, metrics: Dict) -> str:
        """数値を整形"""
        
        if not metrics:
            return "（数値データなし）"
        
        lines = []
        
        # 主要項目
        key_metrics = [
            ('revenue', '売上高'),
            ('operating_income', '営業利益'),
            ('net_income', '当期純利益'),
            ('total_assets', '総資産'),
            ('total_equity', '純資産'),
            ('roe', 'ROE'),
            ('employee_count', '従業員数'),
        ]
        
        for key, label in key_metrics:
            if key in metrics:
                metric = metrics[key]
                if isinstance(metric, dict):
                    if metric.get('status') != 'error' and metric.get('value') is not None:
                        value = metric['value']
                        unit = metric.get('unit', '')
                        
                        # 数値フォーマット
                        if isinstance(value, float):
                            if abs(value) >= 1_000_000:
                                formatted = f"{value/1_000_000:,.1f}百万"
                            else:
                                formatted = f"{value:,.0f}"
                        else:
                            formatted = str(value)
                        
                        lines.append(f"- {label}: {formatted}{unit}")
                    else:
                        lines.append(f"- {label}: （データなし）")
        
        return "\n".join(lines) if lines else "（数値データなし）"
    
    def _call_llm(self, prompt: str) -> str:
        """LLM呼び出し"""
        
        import requests
        
        url = f"{self.ollama_base_url}/api/generate"
        
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 2048,
                "num_ctx": 8192,
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()
            return response.json().get('response', '')
        except Exception as e:
            return f"エラー: {e}"
    
    # ========================================
    # レポート生成
    # ========================================
    def generate_report(self, edinet_code: str, fiscal_year: str) -> str:
        """総合レポートを生成"""
        
        context = self.build_context(edinet_code, fiscal_year)
        
        report = f"""# {context.company_name} 財務分析レポート

**企業コード:** {edinet_code}
**年度:** {fiscal_year}
**作成日:** {datetime.now().strftime('%Y-%m-%d')}

---

## エグゼクティブサマリー

{context.executive_summary or '（要約データなし）'}

---

## 検証済み財務数値

{self._format_metrics(context.metrics)}

---

## 強み

{chr(10).join(f'- {s}' for s in context.strengths) if context.strengths else '（データなし）'}

---

## リスク・課題

{chr(10).join(f'- {w}' for w in context.weaknesses) if context.weaknesses else '（データなし）'}

---

*このレポートは数値パイプライン（XBRL抽出+検証）とテキストパイプライン（RAPTOR）を統合して自動生成されました。*
*数値は検証済みのもののみを使用しています。*
"""
        
        return report


# ============================================================
# 使用例
# ============================================================
if __name__ == "__main__":
    from pathlib import Path
    
    desktop = Path.home() / "Desktop"
    output_dir = desktop / "financial_analysis_output"
    
    analyzer = IntegratedAnalyzer(
        numeric_db_path=output_dir,
        chromadb_path=Path.home() / "chromadb_financial",
    )
    
    # テスト分析
    # result = analyzer.analyze(
    #     query="トヨタの収益性と今後の見通しは？",
    #     edinet_code="E00014",
    #     fiscal_year="2024"
    # )
    # print(result.answer)