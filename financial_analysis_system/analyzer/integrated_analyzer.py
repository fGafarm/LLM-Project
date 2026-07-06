"""
統合分析エンジン
XBRL（Google Sheets）の数値とPDF（RAPTOR）の分析を統合し、
数値を比較・検証した上で最終結論を出す
全カラム対応（取捨選択なし）
"""

import json
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_numeric.sheets_reader import SheetsReader
from pipeline_numeric.numeric_comparator import (
    NumericComparator, 
    PDFNumericExtractor,
    ComparisonResult,
    MatchStatus,
    ConfidenceLevel
)


@dataclass
class IntegratedAnalysisResult:
    """統合分析結果"""
    company_name: str
    ticker_code: str
    fiscal_year: int
    
    # 生データ
    xbrl_data: Dict[str, Any]          # Google Sheetsから取得した全データ
    pdf_data: Dict[str, Any]           # PDFから抽出した全データ
    
    # 検証済み数値
    verified_metrics: Dict[str, Any]
    
    # 比較結果
    comparison_results: List[ComparisonResult]
    comparison_summary: Dict[str, Any]
    
    # LLM分析
    qualitative_analysis: str      # 定性分析（テキスト）
    quantitative_summary: str      # 定量サマリー
    
    # 最終結論
    final_conclusion: str
    confidence_score: float
    
    # メタデータ
    generated_at: str
    sources_used: List[str]
    warnings: List[str]


class IntegratedAnalyzer:
    """統合分析エンジン"""
    
    def __init__(
        self,
        sheets_credentials: str,
        spreadsheet_name: str,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "gemma2:27b"
    ):
        """
        Args:
            sheets_credentials: Google API認証JSONパス
            spreadsheet_name: StockFlowのスプレッドシート名またはID
            ollama_url: Ollama APIのURL
            ollama_model: 使用するモデル
        """
        self.sheets_reader = SheetsReader(sheets_credentials, spreadsheet_name)
        self.comparator = NumericComparator()
        self.pdf_extractor = PDFNumericExtractor()
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
    
    def analyze(
        self,
        ticker_code: str,
        fiscal_year: int,
        raptor_output_path: Optional[str] = None,
        raptor_output_json: Optional[Dict] = None,
        query: Optional[str] = None
    ) -> IntegratedAnalysisResult:
        """
        統合分析を実行
        
        Args:
            ticker_code: 証券コード
            fiscal_year: 決算年度
            raptor_output_path: RAPTOR出力JSONファイルのパス
            raptor_output_json: RAPTOR出力JSON（直接渡す場合）
            query: 分析クエリ（任意）
        
        Returns:
            IntegratedAnalysisResult
        """
        warnings = []
        sources_used = []
        
        print(f"\n{'='*60}")
        print(f"📊 統合分析: {ticker_code} ({fiscal_year}年度)")
        print(f"{'='*60}")
        
        # 1. XBRLデータ取得（Google Sheets）- 全カラム
        print("\n📥 Step 1: XBRLデータ取得（全カラム）...")
        xbrl_data = self._get_xbrl_data(ticker_code, fiscal_year)
        company_name = ""
        
        if xbrl_data:
            sources_used.append("XBRL (Google Sheets)")
            # 企業名を取得
            company_name = (
                xbrl_data.get('Headers_detailed_pl', {}).get('name_ja', '') or
                xbrl_data.get('Headers_detailed_bs', {}).get('company_name', '') or
                self.sheets_reader.get_company_name(ticker_code) or
                ticker_code
            )
            total_columns = sum(len(sheet_data) for sheet_data in xbrl_data.values())
            print(f"  ✅ 企業名: {company_name}")
            print(f"  ✅ 取得カラム数: {total_columns}")
        else:
            warnings.append("XBRLデータが取得できませんでした")
            print("  ⚠️ XBRLデータなし")
        
        # 2. PDF分析結果の取得・数値抽出
        print("\n📥 Step 2: PDF分析結果から数値抽出...")
        raptor_json = None
        pdf_data = {}
        
        if raptor_output_path:
            raptor_json = self._load_raptor_output(raptor_output_path)
        elif raptor_output_json:
            raptor_json = raptor_output_json
        
        if raptor_json:
            pdf_data = self.pdf_extractor.extract_from_raptor_output(raptor_json)
            sources_used.append("PDF (RAPTOR分析)")
            print(f"  ✅ {len(pdf_data)}項目の数値を抽出")
        else:
            warnings.append("RAPTOR分析結果がありません")
            print("  ⚠️ RAPTOR出力なし")
        
        # 3. 数値比較・検証
        print("\n🔍 Step 3: 数値比較・検証...")
        
        # XBRLデータをフラット化（比較用）
        xbrl_flat = self._flatten_xbrl_data(xbrl_data)
        
        comparison_results = self.comparator.compare_all(xbrl_flat, pdf_data)
        comparison_summary = self.comparator.generate_summary(comparison_results)
        verified_metrics = self.comparator.get_verified_values(comparison_results)
        
        print(f"  📊 比較項目数: {comparison_summary['total_metrics']}")
        print(f"  📊 完全一致: {comparison_summary['exact_match']}")
        print(f"  📊 許容誤差内: {comparison_summary['close_match']}")
        print(f"  ⚠️ 不一致: {comparison_summary['mismatch']}")
        print(f"  📈 信頼度スコア: {comparison_summary['confidence_score']:.2f}")
        
        # 4. LLM分析（定性+定量）
        print("\n🤖 Step 4: LLM統合分析...")
        
        # 定性分析（RAPTORからのテキスト）
        qualitative_analysis = self._extract_qualitative_analysis(raptor_json)
        
        # 定量サマリー生成
        quantitative_summary = self._generate_quantitative_summary(
            verified_metrics, 
            comparison_results,
            company_name
        )
        
        # 最終結論生成
        final_conclusion = self._generate_final_conclusion(
            company_name=company_name,
            verified_metrics=verified_metrics,
            qualitative_analysis=qualitative_analysis,
            comparison_summary=comparison_summary,
            query=query
        )
        
        print("  ✅ 分析完了")
        
        return IntegratedAnalysisResult(
            company_name=company_name,
            ticker_code=ticker_code,
            fiscal_year=fiscal_year,
            xbrl_data=xbrl_data,
            pdf_data=pdf_data,
            verified_metrics=verified_metrics,
            comparison_results=comparison_results,
            comparison_summary=comparison_summary,
            qualitative_analysis=qualitative_analysis,
            quantitative_summary=quantitative_summary,
            final_conclusion=final_conclusion,
            confidence_score=comparison_summary['confidence_score'],
            generated_at=datetime.now().isoformat(),
            sources_used=sources_used,
            warnings=warnings
        )
    
    def _get_xbrl_data(self, ticker_code: str, fiscal_year: int) -> Dict[str, Dict[str, Any]]:
        """XBRLデータを取得（全シート、全カラム）"""
        try:
            return self.sheets_reader.get_all_data(ticker_code, fiscal_year)
        except Exception as e:
            print(f"  ⚠️ XBRL取得エラー: {e}")
            return {}
    
    def _flatten_xbrl_data(self, xbrl_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """シート別データをフラット化"""
        flat = {}
        for sheet_name, data in xbrl_data.items():
            for key, value in data.items():
                flat[key] = value
        return flat
    
    def _load_raptor_output(self, path: str) -> Optional[Dict]:
        """RAPTOR出力JSONを読み込み"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️ RAPTOR出力読み込みエラー: {e}")
            return None
    
    def _extract_qualitative_analysis(self, raptor_json: Optional[Dict]) -> str:
        """RAPTORからの定性分析を抽出"""
        if not raptor_json:
            return "定性情報なし"
        
        # トップレベルの要約を抽出
        summaries = []
        
        def extract_summaries(node, level=0):
            if isinstance(node, dict):
                if 'summary' in node and level <= 2:
                    summaries.append(str(node['summary']))
                if 'children' in node:
                    for child in node['children']:
                        extract_summaries(child, level + 1)
            elif isinstance(node, list):
                for item in node:
                    extract_summaries(item, level)
        
        extract_summaries(raptor_json)
        
        if summaries:
            return "\n\n".join(summaries[:5])  # 上位5つの要約
        return "定性情報なし"
    
    def _generate_quantitative_summary(
        self,
        verified_metrics: Dict[str, Any],
        comparison_results: List[ComparisonResult],
        company_name: str
    ) -> str:
        """定量サマリーを生成"""
        lines = [f"## {company_name} 財務数値サマリー\n"]
        
        # 主要数値（存在するもの全て）
        key_metrics = [
            'revenue', 'operating_income', 'ordinary_income', 'net_income',
            'total_assets', 'total_equity', 'total_liabilities',
            'operating_cf', 'investing_cf', 'financing_cf', 'free_cash_flow',
            'roe', 'roa', 'equity_ratio',
            'employee_count', 'average_age', 'average_salary'
        ]
        
        lines.append("### 主要財務指標\n")
        
        for key in key_metrics:
            if key in verified_metrics:
                v = verified_metrics[key]
                value = v['value']
                conf = v['confidence']
                status = v['status']
                
                # 数値フォーマット
                if key in ['roe', 'roa', 'equity_ratio']:
                    formatted = f"{value:.2f}%"
                elif key == 'employee_count':
                    formatted = f"{int(value):,}人"
                elif key == 'average_age':
                    formatted = f"{value:.1f}歳"
                elif key == 'average_salary':
                    formatted = f"{value:,.0f}円"
                elif value >= 1000000:
                    formatted = f"{value/1000000:,.1f}兆円"
                elif value >= 100:
                    formatted = f"{value/100:,.0f}億円"
                else:
                    formatted = f"{value:,.0f}百万円"
                
                # 信頼度マーク
                conf_mark = "✅" if conf == "high" else "⚠️" if conf == "medium" else "❓"
                lines.append(f"- {key}: {formatted} {conf_mark}")
        
        # 不一致項目
        mismatches = [r for r in comparison_results if r.status == MatchStatus.MISMATCH]
        if mismatches:
            lines.append("\n### ⚠️ 数値不一致項目\n")
            for m in mismatches[:10]:  # 最大10件
                xbrl_str = f"{m.xbrl_value:,.0f}" if isinstance(m.xbrl_value, (int, float)) else str(m.xbrl_value)
                pdf_str = f"{m.pdf_value:,.0f}" if isinstance(m.pdf_value, (int, float)) else str(m.pdf_value)
                lines.append(f"- {m.metric_name}: XBRL={xbrl_str}, PDF={pdf_str} (差異{m.difference_pct:.1f}%)")
        
        return "\n".join(lines)
    
    def _generate_final_conclusion(
        self,
        company_name: str,
        verified_metrics: Dict[str, Any],
        qualitative_analysis: str,
        comparison_summary: Dict[str, Any],
        query: Optional[str] = None
    ) -> str:
        """最終結論を生成（LLM使用）"""
        
        # プロンプト構築
        metrics_text = self._format_metrics_for_prompt(verified_metrics)
        
        prompt = f"""あなたは日本の上場企業を分析する財務アナリストです。
以下の情報を基に、{company_name}の分析結論を日本語で作成してください。

## 検証済み財務数値（XBRL+PDF照合済み）

{metrics_text}

## 定性情報（有価証券報告書より）

{qualitative_analysis[:3000]}

## データ品質情報

- 信頼度スコア: {comparison_summary['confidence_score']:.2f}/1.00
- 完全一致項目: {comparison_summary['exact_match']}
- 許容誤差内: {comparison_summary['close_match']}
- 不一致項目: {comparison_summary['mismatch']}
- 要確認事項: {len(comparison_summary['issues'])}件

{f'## 分析クエリ: {query}' if query else ''}

## 指示

1. 上記の「検証済み財務数値」のみを数値として使用してください
2. 数値が不一致の場合は、その旨を明記してください
3. 定性情報と数値を組み合わせて、バランスの取れた分析をしてください
4. 500字程度で簡潔にまとめてください

分析結論:"""
        
        return self._call_ollama(prompt)
    
    def _format_metrics_for_prompt(self, metrics: Dict[str, Any]) -> str:
        """プロンプト用に数値をフォーマット"""
        lines = []
        for key, v in metrics.items():
            if not isinstance(v, dict) or 'value' not in v:
                continue
            
            value = v['value']
            conf = v.get('confidence', 'unknown')
            
            # 数値フォーマット
            if key in ['roe', 'roa', 'equity_ratio']:
                formatted = f"{value:.2f}%"
            elif value >= 1000000:
                formatted = f"{value/1000000:,.1f}兆円"
            elif value >= 100:
                formatted = f"{value/100:,.0f}億円"
            else:
                formatted = f"{value:,.0f}"
            
            # 信頼度マーク
            mark = "[確定]" if conf == "high" else "[要確認]" if conf == "low" else ""
            lines.append(f"- {key}: {formatted} {mark}")
        
        return "\n".join(lines) if lines else "数値データなし"
    
    def _call_ollama(self, prompt: str) -> str:
        """Ollamaを呼び出し"""
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 1024,
                    }
                },
                timeout=300
            )
            response.raise_for_status()
            return response.json().get('response', '')
        except Exception as e:
            return f"LLM呼び出しエラー: {e}"
    
    def save_result(
        self, 
        result: IntegratedAnalysisResult, 
        output_dir: str
    ) -> Path:
        """分析結果を保存"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # JSON出力（全データ）
        json_path = output_path / f"{result.ticker_code}_{result.fiscal_year}_{timestamp}_full.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'company_name': result.company_name,
                'ticker_code': result.ticker_code,
                'fiscal_year': result.fiscal_year,
                'xbrl_data': result.xbrl_data,
                'pdf_data': result.pdf_data,
                'verified_metrics': result.verified_metrics,
                'comparison_summary': result.comparison_summary,
                'confidence_score': result.confidence_score,
                'sources_used': result.sources_used,
                'warnings': result.warnings,
                'generated_at': result.generated_at,
            }, f, ensure_ascii=False, indent=2, default=str)
        
        # Markdown出力
        md_path = output_path / f"{result.ticker_code}_{result.fiscal_year}_{timestamp}_report.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {result.company_name} ({result.ticker_code}) {result.fiscal_year}年度 分析レポート\n\n")
            f.write(f"生成日時: {result.generated_at}\n")
            f.write(f"信頼度スコア: {result.confidence_score:.2f}/1.00\n\n")
            f.write("---\n\n")
            f.write(result.quantitative_summary)
            f.write("\n\n---\n\n")
            f.write("## 分析結論\n\n")
            f.write(result.final_conclusion)
            f.write("\n\n---\n\n")
            f.write("## 定性分析\n\n")
            f.write(result.qualitative_analysis)
            
            if result.warnings:
                f.write("\n\n---\n\n## ⚠️ 警告\n\n")
                for w in result.warnings:
                    f.write(f"- {w}\n")
        
        print(f"\n📁 保存完了:")
        print(f"  JSON: {json_path.name}")
        print(f"  MD:   {md_path.name}")
        
        return md_path


# テスト用
if __name__ == "__main__":
    # 設定（実際のパスに変更必要）
    analyzer = IntegratedAnalyzer(
        sheets_credentials="path/to/credentials.json",
        spreadsheet_id="your-spreadsheet-id",
        ollama_model="gemma2:27b"
    )
    
    # 分析実行
    result = analyzer.analyze(
        ticker_code="7203",
        fiscal_year=2024,
        raptor_output_path="path/to/raptor_output.json",
        query="トヨタの収益性と今後の見通しは？"
    )
    
    # 結果保存
    analyzer.save_result(result, "output/")