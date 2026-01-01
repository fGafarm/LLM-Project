#!/usr/bin/env python3
"""
数値パイプライン - XBRL数値抽出 + 異常値検知
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from datetime import datetime
from enum import Enum


class ValidationStatus(Enum):
    """検証ステータス"""
    VALID = "valid"           # 正常
    WARNING = "warning"       # 警告（範囲外だが許容）
    ERROR = "error"          # エラー（明らかな異常）
    MISSING = "missing"      # 欠損


@dataclass
class NumericValue:
    """検証済み数値"""
    value: Optional[float]
    unit: str
    source: str  # "xbrl", "manual", "estimated"
    status: ValidationStatus
    message: str = ""
    raw_value: Optional[str] = None  # 元の値（デバッグ用）
    
    def to_dict(self) -> dict:
        return {
            'value': self.value,
            'unit': self.unit,
            'source': self.source,
            'status': self.status.value,
            'message': self.message,
        }


@dataclass
class FinancialMetrics:
    """企業の財務指標（検証済み）"""
    edinet_code: str
    company_name: str
    fiscal_year: str
    fiscal_period: str
    
    # 損益計算書
    revenue: Optional[NumericValue] = None
    operating_income: Optional[NumericValue] = None
    ordinary_income: Optional[NumericValue] = None
    net_income: Optional[NumericValue] = None
    
    # 貸借対照表
    total_assets: Optional[NumericValue] = None
    total_equity: Optional[NumericValue] = None
    total_liabilities: Optional[NumericValue] = None
    
    # キャッシュフロー
    cf_operating: Optional[NumericValue] = None
    cf_investing: Optional[NumericValue] = None
    cf_financing: Optional[NumericValue] = None
    
    # 財務指標
    eps: Optional[NumericValue] = None
    bps: Optional[NumericValue] = None
    roe: Optional[NumericValue] = None
    roa: Optional[NumericValue] = None
    
    # その他
    employee_count: Optional[NumericValue] = None
    average_age: Optional[NumericValue] = None
    average_salary: Optional[NumericValue] = None
    r_and_d_expense: Optional[NumericValue] = None
    capex: Optional[NumericValue] = None
    dividend_per_share: Optional[NumericValue] = None
    
    # メタデータ
    validation_summary: dict = field(default_factory=dict)
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())


class NumericValidator:
    """数値の異常値検知"""
    
    # 検証ルール定義
    RULES = {
        'revenue': {
            'min': 0,
            'max': 100_000_000_000_000,  # 100兆円
            'unit': '百万円',
            'allow_negative': False,
        },
        'operating_income': {
            'min': -10_000_000_000_000,
            'max': 10_000_000_000_000,
            'unit': '百万円',
            'allow_negative': True,
        },
        'net_income': {
            'min': -10_000_000_000_000,
            'max': 10_000_000_000_000,
            'unit': '百万円',
            'allow_negative': True,
        },
        'total_assets': {
            'min': 0,
            'max': 1_000_000_000_000_000,
            'unit': '百万円',
            'allow_negative': False,
        },
        'employee_count': {
            'min': 1,
            'max': 10_000_000,  # 1000万人
            'unit': '人',
            'allow_negative': False,
        },
        'average_age': {
            'min': 18,
            'max': 70,
            'unit': '歳',
            'allow_negative': False,
        },
        'average_salary': {
            'min': 1_000_000,      # 100万円
            'max': 100_000_000,    # 1億円
            'unit': '円',
            'allow_negative': False,
        },
        'roe': {
            'min': -200,
            'max': 200,
            'unit': '%',
            'allow_negative': True,
        },
        'roa': {
            'min': -100,
            'max': 100,
            'unit': '%',
            'allow_negative': True,
        },
        'eps': {
            'min': -100_000,
            'max': 100_000,
            'unit': '円',
            'allow_negative': True,
        },
    }
    
    @classmethod
    def validate(cls, metric_name: str, value: Any, unit: str = "") -> NumericValue:
        """数値を検証してNumericValueを返す"""
        
        # 値がNoneまたは空の場合
        if value is None or value == "":
            return NumericValue(
                value=None,
                unit=unit,
                source="xbrl",
                status=ValidationStatus.MISSING,
                message="値が欠損しています",
            )
        
        # 数値に変換
        try:
            if isinstance(value, str):
                # カンマ、スペース、△を処理
                cleaned = value.replace(',', '').replace(' ', '').replace('△', '-').replace('▲', '-')
                numeric_value = float(cleaned)
            else:
                numeric_value = float(value)
        except (ValueError, TypeError):
            return NumericValue(
                value=None,
                unit=unit,
                source="xbrl",
                status=ValidationStatus.ERROR,
                message=f"数値に変換できません: {value}",
                raw_value=str(value),
            )
        
        # ルールがある場合は検証
        if metric_name in cls.RULES:
            rule = cls.RULES[metric_name]
            
            # 負の値チェック
            if not rule['allow_negative'] and numeric_value < 0:
                return NumericValue(
                    value=numeric_value,
                    unit=unit or rule['unit'],
                    source="xbrl",
                    status=ValidationStatus.WARNING,
                    message=f"負の値は想定外です: {numeric_value}",
                    raw_value=str(value),
                )
            
            # 範囲チェック
            if numeric_value < rule['min'] or numeric_value > rule['max']:
                return NumericValue(
                    value=numeric_value,
                    unit=unit or rule['unit'],
                    source="xbrl",
                    status=ValidationStatus.ERROR,
                    message=f"範囲外の値です（{rule['min']}〜{rule['max']}）: {numeric_value}",
                    raw_value=str(value),
                )
            
            unit = unit or rule['unit']
        
        # 正常
        return NumericValue(
            value=numeric_value,
            unit=unit,
            source="xbrl",
            status=ValidationStatus.VALID,
            message="",
            raw_value=str(value),
        )
    
    @classmethod
    def validate_yoy_change(cls, current: float, previous: float, 
                           metric_name: str, threshold: float = 5.0) -> tuple[bool, str]:
        """前年比の妥当性チェック"""
        
        if previous == 0:
            if current == 0:
                return True, ""
            return False, "前年が0のため比較不可"
        
        ratio = current / previous
        
        # 極端な変化をチェック
        if ratio > threshold or ratio < (1 / threshold):
            return False, f"前年比が異常です（{ratio:.2f}倍）。単位や桁の確認が必要です。"
        
        return True, ""


class NumericPipeline:
    """数値パイプライン - XBRL抽出 + 検証 + DB保存"""
    
    def __init__(self, config=None):
        from config import config as default_config
        self.config = config or default_config
        self.validator = NumericValidator()
    
    def extract_from_xbrl(self, edinet_code: str, year: int) -> Optional[FinancialMetrics]:
        """XBRLから財務指標を抽出"""
        
        # TODO: 既存のxbrl_analysis_local_llm.pyの抽出ロジックを統合
        # ここではスキーマだけ定義
        
        metrics = FinancialMetrics(
            edinet_code=edinet_code,
            company_name="",
            fiscal_year=str(year),
            fiscal_period="FY",
        )
        
        return metrics
    
    def extract_from_google_sheets(self, edinet_code: str, 
                                   spreadsheet_id: str) -> Optional[FinancialMetrics]:
        """Google Sheetsから財務指標を取得（既存のStockFlow連携）"""
        
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            
            # 認証
            creds = Credentials.from_service_account_file(
                str(self.config.GOOGLE_SHEETS_CREDENTIALS),
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
            client = gspread.authorize(creds)
            
            # データ取得
            sheet = client.open_by_key(spreadsheet_id).sheet1
            records = sheet.get_all_records()
            
            # EDINETコードでフィルタ
            for record in records:
                if record.get('edinet_code') == edinet_code:
                    return self._record_to_metrics(record)
            
            return None
            
        except Exception as e:
            print(f"Google Sheets読み込みエラー: {e}")
            return None
    
    def _record_to_metrics(self, record: dict) -> FinancialMetrics:
        """レコードをFinancialMetricsに変換（検証付き）"""
        
        metrics = FinancialMetrics(
            edinet_code=record.get('edinet_code', ''),
            company_name=record.get('company_name', ''),
            fiscal_year=str(record.get('fiscal_year', '')),
            fiscal_period=record.get('fiscal_period', ''),
        )
        
        # 各項目を検証しながら設定
        field_mapping = {
            'revenue': 'revenue',
            'operating_income': 'operating_income',
            'net_income': 'net_income',
            'total_assets': 'total_assets',
            'total_equity': 'total_equity',
            'employee_count': 'employee_count',
            'roe': 'roe',
            'roa': 'roa',
        }
        
        validation_summary = {
            'valid': 0,
            'warning': 0,
            'error': 0,
            'missing': 0,
            'errors': [],
        }
        
        for sheet_col, metric_name in field_mapping.items():
            raw_value = record.get(sheet_col)
            validated = self.validator.validate(metric_name, raw_value)
            setattr(metrics, metric_name, validated)
            
            # サマリー更新
            validation_summary[validated.status.value] += 1
            if validated.status in [ValidationStatus.ERROR, ValidationStatus.WARNING]:
                validation_summary['errors'].append({
                    'field': metric_name,
                    'status': validated.status.value,
                    'message': validated.message,
                    'value': raw_value,
                })
        
        metrics.validation_summary = validation_summary
        
        return metrics
    
    def save_to_json(self, metrics: FinancialMetrics, output_dir: Path) -> Path:
        """検証済みメトリクスをJSONで保存"""
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{metrics.edinet_code}_{metrics.fiscal_year}_metrics.json"
        output_path = output_dir / filename
        
        # dataclassをdictに変換
        data = asdict(metrics)
        
        # NumericValueをdictに変換
        for key, value in data.items():
            if isinstance(value, dict) and 'status' in value:
                # NumericValueのstatus enumを文字列に
                if hasattr(value['status'], 'value'):
                    value['status'] = value['status'].value
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        
        print(f"💾 数値データ保存: {output_path}")
        return output_path
    
    def generate_validation_report(self, metrics: FinancialMetrics) -> str:
        """検証レポートを生成"""
        
        summary = metrics.validation_summary
        
        report = f"""
# 数値検証レポート

**企業:** {metrics.company_name} ({metrics.edinet_code})
**年度:** {metrics.fiscal_year} {metrics.fiscal_period}
**検証日時:** {metrics.extracted_at}

## 検証サマリー

| ステータス | 件数 |
|-----------|------|
| ✅ 正常 | {summary.get('valid', 0)} |
| ⚠️ 警告 | {summary.get('warning', 0)} |
| ❌ エラー | {summary.get('error', 0)} |
| ❓ 欠損 | {summary.get('missing', 0)} |

"""
        
        if summary.get('errors'):
            report += "## 検出された問題\n\n"
            for err in summary['errors']:
                icon = "⚠️" if err['status'] == 'warning' else "❌"
                report += f"- {icon} **{err['field']}**: {err['message']} (値: {err['value']})\n"
        
        return report


# ============================================================
# 使用例
# ============================================================
if __name__ == "__main__":
    # テスト
    validator = NumericValidator()
    
    # 正常値
    print("=== 正常値テスト ===")
    result = validator.validate('average_age', 42.5)
    print(f"平均年齢 42.5歳: {result.status.value}")
    
    result = validator.validate('average_salary', 7_500_000)
    print(f"平均給与 750万円: {result.status.value}")
    
    # 異常値（RAPTORで発生した問題をシミュレート）
    print("\n=== 異常値テスト ===")
    result = validator.validate('average_age', 70224)  # 70,224歳
    print(f"平均年齢 70,224歳: {result.status.value} - {result.message}")
    
    result = validator.validate('average_salary', 380793)  # 38万円
    print(f"平均給与 380,793円: {result.status.value} - {result.message}")
    
    result = validator.validate('employee_count', -100)  # 負の従業員数
    print(f"従業員数 -100人: {result.status.value} - {result.message}")