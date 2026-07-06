"""
数値比較・検証モジュール
PDFから直接数値を抽出し、Google Sheetsの数値と比較
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
from pathlib import Path

# PDF読み込み用
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False


class MatchStatus(Enum):
    """比較結果ステータス"""
    EXACT_MATCH = "exact_match"
    CLOSE_MATCH = "close_match"
    MISMATCH = "mismatch"
    PDF_ONLY = "pdf_only"
    XBRL_ONLY = "xbrl_only"
    BOTH_MISSING = "both_missing"


class ConfidenceLevel(Enum):
    """信頼度レベル"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class ComparisonResult:
    """1項目の比較結果"""
    metric_name: str
    xbrl_value: Optional[Any]
    pdf_value: Optional[Any]
    final_value: Optional[Any]
    status: MatchStatus
    confidence: ConfidenceLevel
    difference_pct: Optional[float] = None
    decision_reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'metric_name': self.metric_name,
            'xbrl_value': self.xbrl_value,
            'pdf_value': self.pdf_value,
            'final_value': self.final_value,
            'status': self.status.value,
            'confidence': self.confidence.value,
            'difference_pct': self.difference_pct,
            'decision_reason': self.decision_reason
        }


class NumericComparator:
    """数値比較エンジン"""
    
    TOLERANCE_EXACT = 0.01
    TOLERANCE_CLOSE = 5.0
    
    def __init__(self, tolerance_exact: float = None, tolerance_close: float = None):
        if tolerance_exact:
            self.TOLERANCE_EXACT = tolerance_exact
        if tolerance_close:
            self.TOLERANCE_CLOSE = tolerance_close
    
    def _is_numeric(self, value: Any) -> bool:
        if value is None or value == '':
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                cleaned = value.replace(',', '').replace('%', '').strip()
                float(cleaned)
                return True
            except (ValueError, TypeError):
                return False
        return False
    
    def _to_float(self, value: Any) -> Optional[float]:
        if value is None or value == '':
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                cleaned = value.replace(',', '').replace('%', '').strip()
                return float(cleaned)
            except (ValueError, TypeError):
                return None
        return None
    
    def compare_value(self, metric_name: str, xbrl_value: Any, pdf_value: Any) -> ComparisonResult:
        xbrl_num = self._to_float(xbrl_value)
        pdf_num = self._to_float(pdf_value)
        
        if xbrl_num is None and pdf_num is None:
            return ComparisonResult(
                metric_name=metric_name,
                xbrl_value=xbrl_value,
                pdf_value=pdf_value,
                final_value=None,
                status=MatchStatus.BOTH_MISSING,
                confidence=ConfidenceLevel.UNKNOWN,
                decision_reason="両方のソースにデータなし"
            )
        
        if xbrl_num is not None and pdf_num is None:
            return ComparisonResult(
                metric_name=metric_name,
                xbrl_value=xbrl_value,
                pdf_value=pdf_value,
                final_value=xbrl_num,
                status=MatchStatus.XBRL_ONLY,
                confidence=ConfidenceLevel.MEDIUM,
                decision_reason="XBRLのみにデータあり → XBRLを採用"
            )
        
        if xbrl_num is None and pdf_num is not None:
            return ComparisonResult(
                metric_name=metric_name,
                xbrl_value=xbrl_value,
                pdf_value=pdf_value,
                final_value=pdf_num,
                status=MatchStatus.PDF_ONLY,
                confidence=ConfidenceLevel.LOW,
                decision_reason="PDFのみにデータあり → PDFを採用（要確認）"
            )
        
        diff_pct = self._calc_difference_pct(xbrl_num, pdf_num)
        
        if diff_pct <= self.TOLERANCE_EXACT:
            return ComparisonResult(
                metric_name=metric_name,
                xbrl_value=xbrl_value,
                pdf_value=pdf_value,
                final_value=xbrl_num,
                status=MatchStatus.EXACT_MATCH,
                confidence=ConfidenceLevel.HIGH,
                difference_pct=diff_pct,
                decision_reason=f"完全一致（差異{diff_pct:.3f}%）"
            )
        
        if diff_pct <= self.TOLERANCE_CLOSE:
            return ComparisonResult(
                metric_name=metric_name,
                xbrl_value=xbrl_value,
                pdf_value=pdf_value,
                final_value=xbrl_num,
                status=MatchStatus.CLOSE_MATCH,
                confidence=ConfidenceLevel.MEDIUM,
                difference_pct=diff_pct,
                decision_reason=f"許容誤差内（差異{diff_pct:.2f}%）→ XBRLを採用"
            )
        
        return ComparisonResult(
            metric_name=metric_name,
            xbrl_value=xbrl_value,
            pdf_value=pdf_value,
            final_value=xbrl_num,
            status=MatchStatus.MISMATCH,
            confidence=ConfidenceLevel.LOW,
            difference_pct=diff_pct,
            decision_reason=f"不一致（差異{diff_pct:.2f}%）→ XBRLを採用（要確認）"
        )
    
    def _calc_difference_pct(self, val1: float, val2: float) -> float:
        if val1 == 0 and val2 == 0:
            return 0.0
        base = max(abs(val1), abs(val2))
        if base == 0:
            return 100.0
        return abs(val1 - val2) / base * 100
    
    def compare_all(self, xbrl_data: Dict[str, Any], pdf_data: Dict[str, Any]) -> List[ComparisonResult]:
        results = []
        all_keys = set(xbrl_data.keys()) | set(pdf_data.keys())
        exclude_suffixes = ('_tag', '_context', '_priority', '_tag_2', '_context_2', '_priority_2', '_tag_3', '_context_3', '_priority_3')
        
        for key in sorted(all_keys):
            if any(key.endswith(suffix) for suffix in exclude_suffixes):
                continue
            
            xbrl_val = xbrl_data.get(key)
            pdf_val = pdf_data.get(key)
            
            if not self._is_numeric(xbrl_val) and not self._is_numeric(pdf_val):
                continue
            
            result = self.compare_value(key, xbrl_val, pdf_val)
            results.append(result)
        
        return results
    
    def generate_summary(self, results: List[ComparisonResult]) -> Dict[str, Any]:
        summary = {
            'total_metrics': len(results),
            'exact_match': 0,
            'close_match': 0,
            'mismatch': 0,
            'xbrl_only': 0,
            'pdf_only': 0,
            'both_missing': 0,
            'high_confidence': 0,
            'medium_confidence': 0,
            'low_confidence': 0,
            'issues': []
        }
        
        for r in results:
            summary[r.status.value] += 1
            
            if r.confidence == ConfidenceLevel.HIGH:
                summary['high_confidence'] += 1
            elif r.confidence == ConfidenceLevel.MEDIUM:
                summary['medium_confidence'] += 1
            elif r.confidence == ConfidenceLevel.LOW:
                summary['low_confidence'] += 1
            
            if r.status == MatchStatus.MISMATCH:
                summary['issues'].append({
                    'metric': r.metric_name,
                    'xbrl': r.xbrl_value,
                    'pdf': r.pdf_value,
                    'diff_pct': r.difference_pct,
                    'decision': r.decision_reason
                })
        
        if summary['total_metrics'] > 0:
            summary['confidence_score'] = (
                summary['high_confidence'] * 1.0 +
                summary['medium_confidence'] * 0.5 +
                summary['low_confidence'] * 0.2
            ) / summary['total_metrics']
        else:
            summary['confidence_score'] = 0.5
        
        return summary
    
    def get_verified_values(self, results: List[ComparisonResult]) -> Dict[str, Any]:
        verified = {}
        for r in results:
            if r.final_value is not None:
                verified[r.metric_name] = {
                    'value': r.final_value,
                    'confidence': r.confidence.value,
                    'status': r.status.value,
                    'xbrl_value': r.xbrl_value,
                    'pdf_value': r.pdf_value,
                    'difference_pct': r.difference_pct,
                    'note': r.decision_reason
                }
        return verified


class PDFNumericExtractor:
    """PDFから直接数値を抽出"""
    
    UNIT_MULTIPLIERS = {
        '円': 0.000001,
        '千円': 0.001,
        '万円': 0.01,
        '百万円': 1,
        '億円': 100,
        '兆円': 1000000,
    }
    
    def extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """
        PDFファイルから直接テキストを読み込んで数値を抽出
        financial_raptor.pyと同じ方式
        """
        if not HAS_PDFPLUMBER:
            print("  ❌ pdfplumberがインストールされていません")
            print("     pip install pdfplumber でインストールしてください")
            return {}
        
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            print(f"  ❌ PDFファイルが見つかりません: {pdf_path}")
            return {}
        
        print(f"  📄 PDF読み込み中: {pdf_path.name}")
        
        all_text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                print(f"  📑 総ページ数: {total_pages}")
                
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    all_text += text + "\n"
                    
                    if (i + 1) % 50 == 0:
                        print(f"    ... {i + 1}ページ処理済み")
                
                print(f"  ✅ PDF読み込み完了: {len(all_text):,}文字")
        
        except Exception as e:
            print(f"  ❌ PDF読み込みエラー: {e}")
            return {}
        
        return self.extract_from_text(all_text)
    
    def extract_from_text(self, text: str) -> Dict[str, Any]:
        """テキストから数値を抽出"""
        results = {}
        
        patterns = [
            # 売上系
            (r'売上高[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'revenue'),
            (r'売上収益[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'revenue'),
            (r'営業収益[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'revenue'),
            # 利益系
            (r'営業利益[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'operating_income'),
            (r'経常利益[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'ordinary_income'),
            (r'当期純利益[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'net_income'),
            (r'親会社.*に帰属する.*純利益[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'net_income'),
            (r'税引前.*利益[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'income_before_taxes'),
            # 資産系
            (r'総資産[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'total_assets'),
            (r'資産合計[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'total_assets'),
            (r'純資産[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'total_equity'),
            (r'自己資本[：:\s]*([\d,\.]+)\s*(百万円|億円|兆円|円)', 'shareholders_equity'),
            # CF系
            (r'営業活動.*キャッシュ・フロー[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'operating_cf'),
            (r'投資活動.*キャッシュ・フロー[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'investing_cf'),
            (r'財務活動.*キャッシュ・フロー[：:\s]*([\d,\.\-]+)\s*(百万円|億円|兆円|円)', 'financing_cf'),
            # 比率系
            (r'ROE[：:\s]*([\d,\.]+)\s*[%％]', 'roe'),
            (r'自己資本.*利益率[：:\s]*([\d,\.]+)\s*[%％]', 'roe'),
            (r'ROA[：:\s]*([\d,\.]+)\s*[%％]', 'roa'),
            (r'総資産.*利益率[：:\s]*([\d,\.]+)\s*[%％]', 'roa'),
            (r'自己資本比率[：:\s]*([\d,\.]+)\s*[%％]', 'equity_ratio'),
            # 従業員系
            (r'従業員数[：:\s]*([\d,]+)\s*(人|名)', 'employee_count'),
            (r'平均年齢[：:\s]*([\d,\.]+)\s*(歳)', 'average_age'),
            (r'平均.*給与[：:\s]*([\d,]+)\s*(円|千円|万円)', 'average_salary'),
        ]
        
        for pattern, field_name in patterns:
            if field_name in results:
                continue
            
            match = re.search(pattern, text)
            if match:
                try:
                    groups = match.groups()
                    value_str = groups[0].replace(',', '')
                    value = float(value_str)
                    
                    if len(groups) >= 2:
                        unit = groups[1]
                        if unit in self.UNIT_MULTIPLIERS:
                            value = value * self.UNIT_MULTIPLIERS[unit]
                    
                    results[field_name] = value
                except (ValueError, IndexError):
                    continue
        
        print(f"  ✅ {len(results)}項目の数値を抽出")
        for k, v in results.items():
            print(f"    - {k}: {v:,.0f}" if isinstance(v, float) and v > 100 else f"    - {k}: {v}")
        
        return results
    
    def extract_from_raptor_output(self, raptor_json: Dict) -> Dict[str, Any]:
        """RAPTOR出力JSONから数値を抽出（後方互換性）"""
        all_text = ""
        tree = raptor_json.get('tree', raptor_json)
        
        root = tree.get('level_2_root', {})
        if root.get('executive_summary'):
            all_text += str(root['executive_summary']) + "\n"
        
        for section in tree.get('level_1_sections', []):
            if section.get('summary'):
                all_text += str(section['summary']) + "\n"
        
        for chunk in tree.get('level_0_chunks', []):
            if chunk.get('text'):
                all_text += str(chunk['text']) + "\n"
        
        return self.extract_from_text(all_text)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        extractor = PDFNumericExtractor()
        results = extractor.extract_from_pdf(pdf_path)
        
        print("\n" + "=" * 60)
        print("抽出結果")
        print("=" * 60)
        for k, v in results.items():
            print(f"  {k}: {v}")
    else:
        print("使用法: python numeric_comparator.py <PDFファイルパス>")