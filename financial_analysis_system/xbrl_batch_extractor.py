#!/usr/bin/env python3
"""
XBRL Batch Extractor v1.1 - 拡張版（全主要財務データ対応）

【取得データ一覧】
■ P/L（損益計算書）: 18項目
  - 売上高、売上原価、売上総利益、販管費
  - 営業利益、営業外収益/費用、経常利益
  - 特別利益/損失、税引前利益、法人税等、純利益
  - 包括利益

■ B/S（貸借対照表）: 30項目
  - 資産: 総資産、流動資産、現金預金、売掛金、棚卸資産、固定資産、のれん等
  - 負債: 流動負債、買掛金、短期借入、固定負債、長期借入、社債、退職給付等
  - 純資産: 株主資本、資本金、資本剰余金、利益剰余金、自己株式等

■ CF（キャッシュフロー）: 12項目
  - 営業CF、減価償却、投資CF、設備投資、財務CF、借入/返済、配当支払
  - 現金及び現金同等物期末残高

■ その他: 8項目
  - 従業員数、発行済株式数、EPS、希薄化EPS、BPS、配当金、研究開発費、設備投資

■ 派生指標（自動計算）: 20+項目
  - 収益性: ROE, ROA, 各種利益率
  - 安全性: 自己資本比率, D/Eレシオ, 流動比率
  - 効率性: 総資産回転率, 棚卸回転日数, CCC
  - CF: FCF, 営業CFマージン, CAPEX/減価償却

使い方:
  # 単一企業・複数年度
  python xbrl_batch_extractor.py --company 1301 --years 2020,2021,2022,2023

  # 複数企業・複数年度
  python xbrl_batch_extractor.py --companies 1301,2802,2914 --years 2021,2022,2023

  # 全企業・指定年度
  python xbrl_batch_extractor.py --all --years 2022,2023

  # 指定フォルダ内のZIPを全て処理
  python xbrl_batch_extractor.py --scan-folder "E:\\PDF\\PDF+XBRL"

出力:
  xbrl_store/
  ├── 1301_極洋/
  │   ├── 2021.json
  │   ├── 2022.json
  │   └── summary.json  # 全年度のサマリ＋トレンド
  ...
"""

import sys
import os
import re
import json
import zipfile
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field
import logging

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
    logger.warning("lxmlがインストールされていません。pip install lxml")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# 検証・学習モジュール
try:
    from xbrl_validator import (
        FinancialStatementValidator,
        TagLearningManager,
        validate_and_learn
    )
    HAS_VALIDATOR = True
except ImportError:
    HAS_VALIDATOR = False
    logger.warning("xbrl_validatorモジュールが見つかりません。検証機能は無効です。")

# ============================================================
# 設定
# ============================================================
class Config:
    # XBRLファイルの場所 (env vars override for CI / cross-platform)
    XBRL_BASE = Path(os.environ.get("PDF_ROOT") or r"E:\PDF\PDF+XBRL")

    # 出力先
    OUTPUT_BASE = Path(os.environ.get("XBRL_STORE") or "./xbrl_store")

    # Google Sheets設定（taxonomy読み込み用）
    PROJECT_DIR = Path(
        os.environ.get("BACKEND_DIR")
        or os.environ.get("PROJECT_ROOT", r"C:\Users\shun nabeno\Desktop\Local LLM Project") + "/backend"
    )
    TAXONOMY_SPREADSHEET = "StockFlow企業データ"
    TAXONOMY_TAB = "taxonomy_config2"
    
    # 企業リスト
    COMPANY_SPREADSHEET = "All_company"
    COMPANY_TAB = "Company"


# ============================================================
# XBRLタグ定義（拡張版 - 全主要財務データ）
# ============================================================
FALLBACK_TAGS = {
    # ========== P/L項目（損益計算書）Duration context ==========
    'revenue': [
        ('jpcrp_cor:NetSalesSummaryOfBusinessResults', 1),
        ('jpcrp_cor:RevenueIFRSSummaryOfBusinessResults', 1),  # IFRS（日立、ユニクロ、SBG、三菱商事等）
        ('jpcrp_cor:OperatingRevenuesIFRSKeyFinancialData', 1),  # IFRS（トヨタ等）
        ('jpcrp_cor:SalesAndFinancialServicesRevenueIFRSKeyFinancialData', 1),  # IFRS（ソニー等）
        ('jpcrp_cor:SalesAndFinancialServicesRevenueIFRS', 1),  # IFRS（ソニー等、別バリエーション）
        ('jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP（ソニー等）
        ('jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults', 1),  # 鉄道/電力/ガス/小売の一部 (JR東/イオン等)
        ('jpcrp_cor:OperatingRevenue2SummaryOfBusinessResults', 1),  # 同上 別バリエーション
        ('jpcrp_cor:InsuranceRevenueIFRSKeyFinancialData', 1),  # 保険業 IFRS (SOMPO HD等)
        ('jpcrp_cor:InsuranceRevenueIFRSSummaryOfBusinessResults', 1),  # 保険業 別パターン
        ('jpigp_cor:RevenueIFRS', 1),  # IFRS財務諸表本体（日立等）
        ('jpigp_cor:NetSalesIFRS', 1),  # IFRS財務諸表本体（SBG等）
        ('jpigp_cor:InsuranceRevenueIFRS', 1),  # 保険業 IFRS本体
        ('jpigp_cor:OperatingRevenueIFRS', 1),  # 鉄道/電力/ガス IFRS本体
        ('jppfs_cor:NetSales', 2),
        ('jppfs_cor:Revenue', 3),
        ('jppfs_cor:OperatingRevenue1', 3),  # 鉄道/電力/ガス JGAAP本体
        ('jppfs_cor:OperatingRevenue2', 3),
        ('jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults', 4),  # 銀行業等：経常収益（優先度下げ：一般企業での誤マッチ防止）
        ('jppfs_cor:OrdinaryIncomeBNK', 4),  # 銀行業：経常収益
        # 2026-07-06 追加 (yuho_audit の NO_REVENUE 実測113件から特定):
        ('jpcrp_cor:NetSalesOfCompletedConstructionContractsSummaryOfBusinessResults', 2),  # 建設業: 完成工事高 (1950日本電設/1777川崎設備 で実測)
        ('jppfs_cor:NetSalesOfCompletedConstructionContractsCNS', 3),  # 建設業 財務諸表本体
        ('jppfs_cor:NetSalesOfCompletedConstructionContracts', 3),  # 同 別バリエーション
        ('jppfs_cor:OperatingIncomeINS', 4),  # 保険業: 経常収益 (T&D 3.48兆/第一生命11.3兆/かんぽ5.6兆 で実測。銀行の経常収益と同じ扱い)
        ('ifrs-full:Revenue', 5),
    ],
    'cost_of_sales': [
        ('jpigp_cor:CostOfSalesIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CostOfSales', 2),
        ('ifrs-full:CostOfSales', 3),
    ],
    'gross_profit': [
        ('jpigp_cor:GrossProfitIFRS', 1),  # IFRS財務諸表本体（日立、ユニクロ等）
        ('jppfs_cor:GrossProfit', 2),
        ('ifrs-full:GrossProfit', 3),
    ],
    # 営業総利益（小売業等で使用、売上総利益+その他営業収入）
    'operating_gross_profit': [
        ('jppfs_cor:OperatingGrossProfit', 1),
        ('jppfs_cor:GrossProfitOnSales', 2),  # 売上総利益（別名）
    ],
    'selling_general_admin': [
        ('jpigp_cor:SellingGeneralAndAdministrativeExpensesIFRS', 1),  # IFRS財務諸表本体（日立、ユニクロ等）
        ('jppfs_cor:SellingGeneralAndAdministrativeExpenses', 2),
    ],
    'finance_income': [  # 金融収益（IFRS）
        ('jpigp_cor:FinanceIncomeIFRS', 1),  # IFRS財務諸表本体（推測）
    ],
    'finance_costs': [  # 金融費用（IFRS）
        ('jpigp_cor:FinanceCostsIFRS', 1),  # IFRS財務諸表本体（推測）
    ],
    'other_income': [  # その他の収益
        ('jpigp_cor:OtherIncomeIFRS', 1),  # IFRS財務諸表本体（推測）
    ],
    'other_expenses': [  # その他の費用
        ('jpigp_cor:OtherExpensesIFRS', 1),  # IFRS財務諸表本体（推測）
    ],
    'operating_income': [
        ('jpcrp_cor:OperatingIncomeSummaryOfBusinessResults', 1),
        ('jpcrp_cor:OperatingProfitLossIFRSKeyFinancialData', 1),  # IFRS（ソニー等）
        ('jpcrp_cor:OperatingProfitLossIFRS', 1),  # IFRS（ソニー等、財務諸表本体）
        ('jpigp_cor:OperatingProfitLossIFRS', 1),  # IFRS財務諸表本体（ユニクロ等）
        ('jpcrp_cor:OperatingIncomeLossUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults', 1),  # 銀行業等：経常利益を営業利益として扱う
        ('jppfs_cor:OrdinaryIncome', 1),  # 銀行業：経常利益（注：revenueでも使用しているが優先度で制御）
        ('jppfs_cor:OperatingIncome', 2),
        ('ifrs-full:OperatingIncome', 3),
    ],
    'non_operating_income': [
        ('jppfs_cor:NonOperatingIncome', 1),
    ],
    'non_operating_expenses': [
        ('jppfs_cor:NonOperatingExpenses', 1),
    ],
    'interest_expenses': [  # 純粋な支払利息のみ（為替差損・手数料・評価損等は含めない）
        ('jpigp_cor:InterestExpensesIFRS', 1),                                              # IFRS純粋利息（注記）
        ('jppfs_cor:InterestExpensesNOE', 1),                                               # J-GAAP非銀行 営業外費用
        ('jppfs_cor:InterestExpenses', 2),                                                  # J-GAAP一般
        ('jpigp_cor:FinancialLiabilitiesMeasuredAtAmortizedCostInterestExpensesIFRS', 3),   # IFRS借入金等利息のみ
        ('jpigp_cor:BorrowingsInterestExpensesIFRS', 3),                                    # IFRS借入金利息（一部企業）
        ('ifrs-full:InterestExpense', 4),
        # ⚠️ FinanceCostsIFRS / FinanceCosts は「金融費用総額」(為替・評価損等含む)
        # → interest_expenses には絶対に入れない。finance_costs フィールドで別途管理。
    ],
    'interest_expenses_lease': [  # IFRS リース負債への支払利息（参考情報）
        ('jpigp_cor:LeaseObligationsInterestExpensesIFRS', 1),
    ],
    'interest_income_pl': [  # 受取利息（非金融業, 営業外収益）
        ('jppfs_cor:InterestIncomeNOI', 1),
        ('jppfs_cor:InterestIncome', 2),
        ('jpigp_cor:FinanceIncomeIFRS', 3),
        ('ifrs-full:InterestIncome', 4),
    ],
    'ordinary_income': [
        ('jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults', 1),
        ('jppfs_cor:OrdinaryIncome', 2),
    ],
    'extraordinary_income': [
        ('jppfs_cor:ExtraordinaryIncome', 1),
    ],
    'extraordinary_loss': [
        ('jppfs_cor:ExtraordinaryLoss', 1),
    ],
    'income_before_tax': [
        ('jpcrp_cor:ProfitLossBeforeTaxIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:ProfitLossBeforeTaxUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:IncomeBeforeIncomeTaxes', 2),
        ('ifrs-full:ProfitLossBeforeTax', 3),
    ],
    'income_taxes': [
        ('jpigp_cor:IncomeTaxExpenseIFRS', 1),  # IFRS財務諸表本体（日立等）
        ('jppfs_cor:IncomeTaxes', 2),
    ],
    'net_income': [
        ('jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults', 1),
        ('jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpigp_cor:ProfitLossAttributableToOwnersOfParentIFRS', 1),  # IFRS財務諸表本体（親会社持分）
        ('jpigp_cor:ProfitLossIFRS', 2),  # IFRS財務諸表本体（税引後利益合計）
        ('jppfs_cor:ProfitLossAttributableToOwnersOfParent', 3),
        ('jppfs_cor:ProfitLoss', 4),
        ('ifrs-full:ProfitLossAttributableToOwnersOfParent', 5),
    ],
    'comprehensive_income': [
        ('jpcrp_cor:ComprehensiveIncomeAttributableToOwnersOfParentIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:ComprehensiveIncomeUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:ComprehensiveIncome', 2),
    ],
    
    # ========== B/S項目（貸借対照表）Instant context ==========
    # 資産の部
    'total_assets': [
        ('jpcrp_cor:TotalAssetsSummaryOfBusinessResults', 1),
        ('jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpigp_cor:AssetsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:TotalAssets', 2),
        ('ifrs-full:Assets', 3),
    ],
    'current_assets': [
        ('jpigp_cor:CurrentAssetsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CurrentAssets', 2),
        ('ifrs-full:CurrentAssets', 3),
    ],
    'cash_and_deposits': [
        ('jpigp_cor:CashAndCashEquivalentsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CashAndDeposits', 2),
    ],
    'trade_receivables': [  # 売掛金（IFRS/US-GAAP）
        ('jpigp_cor:TradeAndOtherReceivablesCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NotesAndAccountsReceivableTrade', 2),
    ],
    'notes_receivable': [
        ('jppfs_cor:NotesReceivableTrade', 1),  # 受取手形のみ（単独表記の場合）
    ],
    'accounts_receivable': [
        ('jppfs_cor:AccountsReceivableTrade', 1),
    ],
    'other_current_assets': [  # その他流動資産
        ('jpigp_cor:OtherCurrentAssetsCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:OtherCurrentAssets', 2),
    ],
    'accrued_income_receivable': [  # 未収収益（利息含む、分離不能なのでproxyとして使用）
        ('jppfs_cor:AccruedIncomeCA', 1),
        ('jppfs_cor:AccruedIncome', 2),
    ],
    'allowance_for_doubtful_current': [  # 貸倒引当金（流動）
        ('jppfs_cor:AllowanceForDoubtfulAccountsCA', 1),
        ('jpigp_cor:AllowanceForDoubtfulAccountsCAIFRS', 2),
    ],
    'allowance_for_doubtful_non_current': [  # 貸倒引当金（固定）
        ('jppfs_cor:AllowanceForDoubtfulAccountsNCA', 1),
        ('jpigp_cor:AllowanceForDoubtfulAccountsNCAIFRS', 2),
    ],
    'allowance_for_doubtful_total': [  # 貸倒引当金（総額タグがあれば）
        ('jppfs_cor:AllowanceForDoubtfulAccounts', 1),
    ],
    'inventories': [
        ('jpigp_cor:InventoriesCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:Inventories', 2),
        ('ifrs-full:Inventories', 3),
    ],
    'merchandise': [
        ('jpigp_cor:MerchandiseAndFinishedGoodsCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:MerchandiseAndFinishedGoods', 2),
    ],
    'work_in_progress': [
        ('jppfs_cor:WorkInProcess', 1),
    ],
    'raw_materials': [
        ('jpigp_cor:RawMaterialsCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:RawMaterialsAndSupplies', 2),
    ],
    'supplies': [  # 貯蔵品
        ('jpigp_cor:SuppliesAndOtherCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:Supplies', 2),
    ],
    'non_current_assets': [
        ('jpigp_cor:NonCurrentAssetsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NoncurrentAssets', 2),
        ('ifrs-full:NoncurrentAssets', 3),
    ],
    'deferred_tax_assets': [  # 繰延税金資産
        ('jpigp_cor:DeferredTaxAssetsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:DeferredTaxAssets', 2),
    ],
    'other_non_current_assets': [  # その他固定資産
        ('jpigp_cor:OtherNonCurrentAssetsNCAIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:OtherNoncurrentAssets', 2),
    ],
    'right_of_use_assets': [  # 使用権資産（リース）
        ('jpigp_cor:RightOfUseAssetsIFRS', 1),  # IFRS財務諸表本体
    ],
    'financial_assets_current': [  # その他の金融資産（流動）
        ('jpigp_cor:OtherFinancialAssetsCAIFRS', 1),  # IFRS財務諸表本体
    ],
    'financial_assets_non_current': [  # その他の金融資産（固定）
        ('jpigp_cor:OtherFinancialAssetsNCAIFRS', 1),  # IFRS財務諸表本体
    ],
    'property_plant_equipment': [
        ('jpigp_cor:PropertyPlantAndEquipmentIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:PropertyPlantAndEquipment', 2),
        ('ifrs-full:PropertyPlantAndEquipment', 3),
    ],
    'land': [
        ('jppfs_cor:Land', 1),
    ],
    'buildings': [
        ('jppfs_cor:BuildingsAndStructuresNet', 1),  # 帳簿価額（減価償却後）
    ],
    'intangible_assets': [
        ('jpigp_cor:IntangibleAssetsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:IntangibleAssets', 2),
        ('ifrs-full:IntangibleAssetsOtherThanGoodwill', 3),
    ],
    'goodwill': [
        ('jppfs_cor:Goodwill', 1),
        ('ifrs-full:Goodwill', 2),
    ],
    'investments': [
        ('jpigp_cor:InvestmentsAccountedForUsingEquityMethodIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:InvestmentSecurities', 2),
    ],
    
    # 負債の部
    'current_liabilities': [
        ('jpigp_cor:TotalCurrentLiabilitiesIFRS', 1),  # IFRS財務諸表本体（日立、ユニクロ等）
        ('jpigp_cor:CurrentLiabilitiesIFRS', 1),  # IFRS財務諸表本体（推測、一部企業）
        ('jppfs_cor:CurrentLiabilities', 2),
        ('ifrs-full:CurrentLiabilities', 3),
    ],
    'trade_payables': [  # 買掛金（IFRS/US-GAAP）
        ('jpigp_cor:TradeAndOtherPayablesCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NotesAndAccountsPayableTrade', 2),
    ],
    'notes_payable': [
        ('jppfs_cor:NotesPayableTrade', 1),  # 支払手形のみ（単独表記の場合）
    ],
    'accounts_payable': [
        ('jppfs_cor:AccountsPayableTrade', 1),
    ],
    'other_current_liabilities': [  # その他流動負債
        ('jpigp_cor:OtherCurrentLiabilitiesCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:OtherCurrentLiabilities', 2),
    ],
    'short_term_loans': [
        ('jpigp_cor:BorrowingsCLIFRS', 1),  # IFRS財務諸表本体（短期借入金）
        ('jpigp_cor:InterestBearingLiabilitiesCLIFRS', 1),  # IFRS財務諸表本体（有利子負債-流動）
        ('jppfs_cor:ShortTermLoansPayable', 2),
    ],
    'current_portion_long_term': [
        ('jpigp_cor:CurrentPortionOfLongTermDebtCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CurrentPortionOfLongTermLoansPayable', 2),
    ],
    'accrued_expenses': [
        ('jpigp_cor:AccruedExpensesCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:AccruedExpenses', 2),
    ],
    'income_taxes_payable': [
        ('jpigp_cor:IncomeTaxesPayableCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:IncomeTaxesPayable', 2),
    ],
    'non_current_liabilities': [
        ('jpigp_cor:NonCurrentLabilitiesIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NoncurrentLiabilities', 2),
        ('ifrs-full:NoncurrentLiabilities', 3),
    ],
    'long_term_loans': [
        ('jpigp_cor:LongTermDebtNCLIFRS', 1),  # IFRS財務諸表本体（長期借入金）
        ('jpigp_cor:InterestBearingLiabilitiesNCLIFRS', 1),  # IFRS財務諸表本体（有利子負債-固定）
        ('jppfs_cor:LongTermLoansPayable', 2),
    ],
    'bonds_payable': [
        ('jppfs_cor:BondsPayable', 1),
    ],
    'retirement_benefit_liability': [
        ('jpigp_cor:RetirementBenefitLiabilityNCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NetDefinedBenefitLiability', 2),
    ],
    'deferred_tax_liabilities': [  # 繰延税金負債
        ('jpigp_cor:DeferredTaxLiabilitiesIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:DeferredTaxLiabilities', 2),
    ],
    'other_non_current_liabilities': [  # その他固定負債
        ('jpigp_cor:OtherNonCurrentLiabilitiesNCLIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:OtherNoncurrentLiabilities', 2),
    ],
    'financial_liabilities_current': [  # その他の金融負債（流動）
        ('jpigp_cor:OtherFinancialLiabilitiesCLIFRS', 1),  # IFRS財務諸表本体
    ],
    'financial_liabilities_non_current': [  # その他の金融負債（固定）
        ('jpigp_cor:OtherFinancialLiabilitiesNCLIFRS', 1),  # IFRS財務諸表本体
    ],
    'warranty_liability': [  # 製品保証引当金
        ('jpigp_cor:LiabilitiesForQualityAssuranceCLIFRS', 1),  # IFRS財務諸表本体
    ],
    'total_liabilities': [
        ('jpigp_cor:LiabilitiesIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:Liabilities', 2),
        ('ifrs-full:Liabilities', 3),
    ],
    
    # 純資産の部
    'total_equity': [
        ('jpcrp_cor:NetAssetsSummaryOfBusinessResults', 1),
        ('jpcrp_cor:EquityIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:EquityIncludingPortionAttributableToNonControllingInterestIFRSSummaryOfBusinessResults', 1),  # IFRS（非支配持分含む）
        ('jpcrp_cor:EquityIncludingPortionAttributableToNonControllingInterestUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP（非支配持分含む）
        ('jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults', 1),  # IFRS（親会社持分）
        ('jpcrp_cor:EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP（親会社持分）
        ('jpigp_cor:EquityIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NetAssets', 2),
        ('ifrs-full:Equity', 3),
    ],
    'shareholders_equity': [
        ('jpigp_cor:EquityAttributableToOwnersOfParentIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:ShareholdersEquity', 2),
    ],
    'capital_stock': [
        ('jpigp_cor:ShareCapitalIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CapitalStock', 2),
        ('ifrs-full:IssuedCapital', 3),
    ],
    'capital_surplus': [
        ('jpigp_cor:CapitalSurplusIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:CapitalSurplus', 2),
    ],
    'retained_earnings': [
        ('jpigp_cor:RetainedEarningsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:RetainedEarnings', 2),
        ('ifrs-full:RetainedEarnings', 3),
    ],
    'treasury_stock': [
        ('jppfs_cor:TreasuryStock', 1),
    ],
    'accumulated_other_comprehensive': [
        ('jpigp_cor:OtherComponentsOfEquityIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:AccumulatedOtherComprehensiveIncome', 2),
    ],
    'non_controlling_interests': [
        ('jpigp_cor:NonControllingInterestsIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:NonControllingInterests', 2),
        ('ifrs-full:NonControllingInterests', 3),
    ],
    # 非支配株主に帰属する利益（純利益検証用）
    'non_controlling_profit': [
        ('jpigp_cor:ProfitLossAttributableToNonControllingInterestsIFRS', 1),  # IFRS
        ('jppfs_cor:ProfitLossAttributableToNonControllingInterests', 2),
        ('ifrs-full:ProfitLossAttributableToNonControllingInterests', 3),
    ],
    # 評価換算差額等（日本GAAP純資産構成用）
    'valuation_adjustments': [
        ('jppfs_cor:ValuationAndTranslationAdjustments', 1),
        ('jppfs_cor:ValuationDifferenceOnAvailableForSaleSecurities', 2),  # その他有価証券評価差額金
    ],
    # 新株予約権（日本GAAP純資産構成用）
    'subscription_rights': [
        ('jppfs_cor:SubscriptionRightsToShares', 1),
    ],
    # 繰延資産（資産内訳用）
    'deferred_assets': [
        ('jppfs_cor:DeferredAssets', 1),
    ],

    # ========== CF項目（キャッシュフロー）Duration context ==========
    'operating_cf': [
        ('jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:CashFlowsFromUsedInOperatingActivitiesUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:NetCashProvidedByUsedInOperatingActivities', 2),
        ('ifrs-full:CashFlowsFromUsedInOperatingActivities', 3),
    ],
    'depreciation_cf': [
        ('jpigp_cor:DepreciationAndAmortizationOpeCFIFRS', 1),  # IFRS財務諸表本体
        ('jpigp_cor:DepreciationAndAmortizationOfIntangibleAssetsOpeCFIFRS', 1),  # IFRS財務諸表本体（別名）
        ('jpigp_cor:DepreciationAndAmortizationOperatingExpensesIFRS', 1),  # IFRS P/L項目
        ('jppfs_cor:DepreciationAndAmortizationOpeCF', 2),
    ],
    'interest_paid_cf': [  # 利息の支払額
        ('jpigp_cor:InterestPaidOpeCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:InterestPaidOpeCF', 2),
    ],
    'interest_received_cf': [  # 利息の受取額
        ('jpigp_cor:InterestReceivedOpeCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:InterestReceivedOpeCF', 2),
    ],
    'dividends_received_cf': [  # 配当金の受取額
        ('jpigp_cor:DividendsReceivedOpeCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:DividendsReceivedOpeCF', 2),
    ],
    'income_taxes_paid_cf': [  # 法人税等の支払額
        ('jpigp_cor:IncomeTaxesPaidOpeCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:IncomeTaxesPaidOpeCF', 2),
    ],
    'investing_cf': [
        ('jpcrp_cor:CashFlowsFromUsedInInvestingActivitiesIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:CashFlowsFromUsedInInvestingActivitiesUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:NetCashProvidedByUsedInInvestingActivities', 2),
        ('ifrs-full:CashFlowsFromUsedInInvestingActivities', 3),
    ],
    'purchase_ppe': [
        ('jpigp_cor:AdditionsToFixedAssetsExcludingEquipmentLeasedToOthersInvCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:PurchaseOfPropertyPlantAndEquipmentInvCF', 2),
    ],
    'purchase_intangibles': [
        ('jpigp_cor:AdditionsToIntangibleAssetsInvCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:PurchaseOfIntangibleAssetsInvCF', 2),
    ],
    'purchase_investments': [
        ('jppfs_cor:PurchaseOfInvestmentSecuritiesInvCF', 1),
    ],
    'financing_cf': [
        ('jpcrp_cor:CashFlowsFromUsedInFinancingActivitiesIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:CashFlowsFromUsedInFinancingActivitiesUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:NetCashProvidedByUsedInFinancingActivities', 2),
        ('ifrs-full:CashFlowsFromUsedInFinancingActivities', 3),
    ],
    'proceeds_borrowings': [
        ('jpigp_cor:ProceedsFromLongTermDebtFinCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:ProceedsFromLongTermLoansPayableFinCF', 2),
    ],
    'repayments_borrowings': [
        ('jpigp_cor:PaymentsOfLongTermDebtFinCFIFRS', 1),  # IFRS財務諸表本体
        ('jppfs_cor:RepaymentOfLongTermLoansPayableFinCF', 2),
    ],
    'dividends_paid': [
        ('jpigp_cor:DividendsPaidToOwnersOfParentFinCFIFRS', 1),  # IFRS財務諸表本体
        ('jpigp_cor:DividendsPaidFinCFIFRS', 1),  # IFRS財務諸表本体（全体）
        ('jppfs_cor:CashDividendsPaidFinCF', 2),  # ★ J-GAAP（MUFG,キーエンス等で確認）
        ('jppfs_cor:DividendsPaidFinCF', 3),  # J-GAAP旧タグ（フォールバック）
        ('jppfs_cor:DividendsFromSurplus', 4),  # J-GAAP剰余金配当（CF以外）
    ],
    'cash_end': [
        ('jpcrp_cor:CashAndCashEquivalentsIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:CashAndCashEquivalentsUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jppfs_cor:CashAndCashEquivalents', 2),
        ('ifrs-full:CashAndCashEquivalents', 3),
    ],
    
    # ========== 株式・従業員データ ==========
    'shares_outstanding': [
        ('jpcrp_cor:NumberOfIssuedSharesAsOfFilingDateIssuedSharesTotalNumberOfSharesEtc', 1),
    ],
    'employee_count': [
        ('jpcrp_cor:NumberOfEmployeesIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:NumberOfEmployees', 2),
    ],
    'average_temp_employees': [
        ('jpcrp_cor:AverageNumberOfTemporaryWorkersIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:AverageNumberOfTemporaryWorkers', 2),
    ],

    # ========== 1株当たり指標 ==========
    'eps': [
        ('jpcrp_cor:BasicEarningsLossPerShareIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:BasicEarningsLossPerShareUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults', 2),
    ],
    'diluted_eps': [
        ('jpcrp_cor:DilutedEarningsLossPerShareIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:DilutedEarningsLossPerShareUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:DilutedEarningsPerShareSummaryOfBusinessResults', 2),
    ],
    'bps': [
        ('jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults', 1),
        ('jpcrp_cor:EquityAttributableToOwnersOfParentPerShareIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:EquityAttributableToOwnersOfParentPerShareUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP (Toyota)
        ('jpcrp_cor:StockholdersEquityPerShareOfCommonStockUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP (Sony)
    ],
    'dividend_per_share': [
        ('jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults', 1),  # EDINETタクソノミ定義（実データ0社マッチ）
        ('jpcrp_cor:DividendPerShareDividendsOfSurplus', 2),  # 剰余金配当1株配当（一部企業で存在）
        # ★ 大半の企業はタグ不在 → L1363の計算フォールバック(dividends_paid/shares_issued)で補完
    ],

    # ========== 財務比率（XBRL提供値） ==========
    'roe': [  # ROE（XBRL提供値、roe_calcとは別に取得）
        ('jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:RateOfReturnOnEquityUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:RateOfReturnOnEquitySummaryOfBusinessResults', 2),  # 日本GAAP
    ],
    'per': [  # PER（株価収益率）
        ('jpcrp_cor:PriceEarningsRatioIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:PriceEarningsRatioUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:PriceEarningsRatioSummaryOfBusinessResults', 2),  # 日本GAAP
    ],
    'equity_ratio': [  # 自己資本比率（XBRL提供値、equity_ratio_calcとは別に取得）
        ('jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults', 1),  # IFRS
        ('jpcrp_cor:EquityToAssetRatioUSGAAPSummaryOfBusinessResults', 1),  # US-GAAP
        ('jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults', 2),  # 日本GAAP
    ],

    # ========== 研究開発・設備投資 ==========
    'rd_expenses': [
        ('jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities', 1),  # 研究開発活動から
        ('jpcrp_cor:ResearchAndDevelopmentExpenses', 2),
    ],
    'capex': [
        ('jpcrp_cor:CapitalExpendituresOverviewOfCapitalExpendituresEtc', 1),  # 設備投資の概要から
        ('jpcrp_cor:CapitalExpendituresSummary', 2),
    ],

    # ========== 監査報酬 ==========
    'audit_fees_reporting_company': [
        ('jpcrp_cor:AuditFeesReportingCompany', 1),
    ],
    'audit_fees_subsidiaries': [
        ('jpcrp_cor:AuditFeesConsolidatedSubsidiaries', 1),
    ],
    'audit_fees_total': [
        ('jpcrp_cor:AuditFeesTotal', 1),
    ],
    'audit_fees_network_firms_total': [
        ('jpcrp_cor:AuditFeesTotalNetworkFirms', 1),
    ],
    'audit_fees_subsidiaries_network_firms': [
        ('jpcrp_cor:AuditFeesConsolidatedSubsidiariesNetworkFirms', 1),
    ],
    'non_audit_fees_reporting_company': [
        ('jpcrp_cor:NonAuditFeesReportingCompany', 1),
    ],
    'non_audit_fees_subsidiaries': [
        ('jpcrp_cor:NonAuditFeesConsolidatedSubsidiaries', 1),
    ],
    'non_audit_fees_total': [
        ('jpcrp_cor:NonAuditFeesTotal', 1),
    ],
    'non_audit_fees_network_firms_reporting_company': [
        ('jpcrp_cor:NonAuditFeesReportingCompanyNetworkFirms', 1),
    ],
    'non_audit_fees_network_firms_subsidiaries': [
        ('jpcrp_cor:NonAuditFeesConsolidatedSubsidiariesNetworkFirms', 1),
    ],
    'non_audit_fees_network_firms_total': [
        ('jpcrp_cor:NonAuditFeesTotalNetworkFirms', 1),
    ],

    # ========== 役員報酬 ==========
    'directors_count': [
        ('jpcrp_cor:NumberOfDirectorsAndOtherOfficersRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_fixed_remuneration': [
        ('jpcrp_cor:FixedRemunerationRemunerationByCategoryOfDirectorsAndOtherOfficers', 1),
        ('jpcrp_cor:FixedRemunerationRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_bonus': [
        ('jpcrp_cor:BonusRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_total_remuneration': [
        ('jpcrp_cor:TotalAmountOfRemunerationEtcRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_performance_remuneration': [
        ('jpcrp_cor:PerformanceBasedRemunerationRemunerationByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_share_compensation': [
        ('jpcrp_cor:ShareCompensationRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'directors_stock_plan': [
        ('jpcrp_cor:PhantomRestrictedStockPlanRemunerationEtcByCategoryOfDirectorsAndOtherOfficers', 1),
    ],
    'individual_director_remuneration_group': [
        ('jpcrp_cor:TotalAmountOfRemunerationEtcPaidByGroupRemunerationEtcPaidByGroupToEachDirectorOrOtherOfficer', 1),
    ],

    # ========== 株主・株式関連 ==========
    'net_income_to_sales_ratio': [  # 売上高純利益率
        ('jpcrp_cor:NetIncomeToSalesBelongingToShareholdersSummaryOfBusinessResults', 1),
    ],
    'shares_issued': [
        ('jpcrp_cor:NumberOfSharesIssuedSharesVotingRights', 1),
    ],
    'voting_rights_count': [
        ('jpcrp_cor:NumberOfVotingRightsIssuedSharesVotingRights', 1),
    ],
    'shares_held': [
        ('jpcrp_cor:NumberOfSharesHeld', 1),
    ],
    'treasury_shares': [
        ('jpcrp_cor:SharesOfTreasuryStockTreasurySharesEtc', 1),
        ('jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc', 1),
    ],
    'treasury_shares_in_own_name': [
        ('jpcrp_cor:NumberOfSharesHeldInOwnNameTreasurySharesEtc', 1),
    ],
    'shareholding_ratio': [
        ('jpcrp_cor:ShareholdingRatio', 1),
    ],
    'treasury_shareholding_ratio': [
        ('jpcrp_cor:ShareholdingRatioTreasurySharesEtc', 1),
    ],
    'shareholders_total': [
        ('jpcrp_cor:NumberOfShareholdersTotal', 1),
    ],
    'shareholders_financial_institutions': [
        ('jpcrp_cor:NumberOfShareholdersFinancialInstitutions', 1),
    ],
    'shareholders_individuals': [
        ('jpcrp_cor:NumberOfShareholdersIndividualsAndOthers', 1),
    ],
    'shareholders_foreign_corporations': [
        ('jpcrp_cor:NumberOfShareholdersForeignInvestorsOtherThanIndividuals', 1),
    ],
    'shareholders_foreign_individuals': [
        ('jpcrp_cor:NumberOfShareholdersForeignIndividualInvestors', 1),
    ],
    'shareholders_domestic_corporations': [
        ('jpcrp_cor:NumberOfShareholdersOtherCorporations', 1),
    ],
    'shareholding_pct_financial_institutions': [
        ('jpcrp_cor:PercentageOfShareholdingsFinancialInstitutions', 1),
    ],
    'shareholding_pct_individuals': [
        ('jpcrp_cor:PercentageOfShareholdingsIndividualsAndOthers', 1),
    ],
    'shareholding_pct_foreign_corporations': [
        ('jpcrp_cor:PercentageOfShareholdingsForeignersOtherThanIndividuals', 1),
    ],
    'shares_per_unit': [
        ('jpcrp_cor:NumberOfSharesConstitutingOneUnit', 1),
    ],
    'investment_securities_issues_not_listed': [
        ('jpcrp_cor:NumberOfIssuesSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_issues_listed': [
        ('jpcrp_cor:NumberOfIssuesSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_carrying_amount_not_listed': [
        ('jpcrp_cor:CarryingAmountSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_carrying_amount_listed': [
        ('jpcrp_cor:CarryingAmountSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_acquisition_cost_not_listed': [
        ('jpcrp_cor:TotalAcquisitionCostForIncreasedSharesSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_acquisition_cost_listed': [
        ('jpcrp_cor:TotalAcquisitionCostForIncreasedSharesSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_sale_amount_not_listed': [
        ('jpcrp_cor:TotalSaleAmountForDecreasedSharesSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_sale_amount_listed': [
        ('jpcrp_cor:TotalSaleAmountForDecreasedSharesSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_issues_increased_not_listed': [
        ('jpcrp_cor:NumberOfIssuesWhoseNumberOfSharesIncreasedSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_issues_increased_listed': [
        ('jpcrp_cor:NumberOfIssuesWhoseNumberOfSharesIncreasedSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_issues_decreased_not_listed': [
        ('jpcrp_cor:NumberOfIssuesWhoseNumberOfSharesDecreasedSharesNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],
    'investment_securities_issues_decreased_listed': [
        ('jpcrp_cor:NumberOfIssuesWhoseNumberOfSharesDecreasedSharesOtherThanThoseNotListedInvestmentSharesHeldForPurposesOtherThanPureInvestmentReportingCompany', 1),
    ],

    # ========== 銀行業固有項目 ==========
    # P/L項目（Duration context）
    'interest_income_bank': [  # 資金運用収益
        ('jppfs_cor:InterestIncomeOIBNK', 1),
    ],
    'interest_on_loans_bank': [  # 貸出金利息
        ('jppfs_cor:InterestOnLoansAndDiscountsOIBNK', 1),
    ],
    'interest_on_securities_bank': [  # 有価証券利息配当金
        ('jppfs_cor:InterestAndDividendsOnSecuritiesOIBNK', 1),
    ],
    'interest_expense_bank': [  # 資金調達費用
        ('jppfs_cor:InterestExpensesOEBNK', 1),
    ],
    'interest_on_deposits_expense_bank': [  # 預金利息
        ('jppfs_cor:InterestOnDepositsOEBNK', 1),
    ],
    'interest_on_borrowings_expense_bank': [  # 借用金利息
        ('jppfs_cor:InterestOnBorrowingsAndRediscountsOEBNK', 1),
    ],
    'fees_and_commissions_income_bank': [  # 役務取引等収益
        ('jppfs_cor:FeesAndCommissionsOIBNK', 1),
    ],
    'fees_and_commissions_expense_bank': [  # 役務取引等費用
        ('jppfs_cor:FeesAndCommissionsPaymentsOEBNK', 1),
    ],
    'trading_income_bank': [  # 特定取引収益
        ('jppfs_cor:TradingIncomeOIBNK', 1),
    ],
    'trading_expenses_bank': [  # 特定取引費用
        ('jppfs_cor:TradingExpensesOEBNK', 1),
    ],
    'other_operating_income_bank': [  # その他業務収益
        ('jppfs_cor:OtherOperatingIncomeOIBNK', 1),
    ],
    'other_operating_expenses_bank': [  # その他業務費用
        ('jppfs_cor:OtherOperatingExpensesOEBNK', 1),
    ],
    'general_and_admin_expenses_bank': [  # 営業経費
        ('jppfs_cor:GeneralAndAdministrativeExpensesOEBNK', 1),
    ],

    # B/S項目（Instant context）
    'cash_due_from_banks': [  # 現金預け金
        ('jppfs_cor:CashAndDueFromBanksAssetsBNK', 1),
    ],
    'call_loans_bank': [  # コールローン
        ('jppfs_cor:CallLoansAndBillsBoughtAssetsBNK', 1),
    ],
    'receivables_under_resale_agreements': [  # 買現先勘定
        ('jppfs_cor:ReceivablesUnderResaleAgreementsAssetsBNK', 1),
    ],
    'receivables_under_securities_borrowing': [  # 買入金銭債権
        ('jppfs_cor:ReceivablesUnderSecuritiesBorrowingTransactionsAssetsBNK', 1),
    ],
    'monetary_claims_bought': [  # 買入金銭債権
        ('jppfs_cor:MonetaryClaimsBoughtAssetsBNK', 1),
    ],
    'trading_assets_bank': [  # 特定取引資産
        ('jppfs_cor:TradingAssetsAssetsBNK', 1),
    ],
    'money_held_in_trust': [  # 金銭の信託
        ('jppfs_cor:MoneyHeldInTrustAssetsBNK', 1),
    ],
    'securities_bank': [  # 有価証券
        ('jppfs_cor:SecuritiesAssetsBNK', 1),
    ],
    'loans_and_bills_bank': [  # 貸出金
        ('jppfs_cor:LoansAndBillsDiscountedAssetsBNK', 1),
    ],
    'foreign_exchanges_bank': [  # 外国為替（資産）
        ('jppfs_cor:ForeignExchangesAssetsBNK', 1),
    ],
    'other_assets_bank': [  # その他資産
        ('jppfs_cor:OtherAssetsAssetsBNK', 1),
    ],
    'tangible_fixed_assets_bank': [  # 有形固定資産
        ('jppfs_cor:TangibleFixedAssetsAssetsBNK', 1),
    ],
    'intangible_fixed_assets_bank': [  # 無形固定資産
        ('jppfs_cor:IntangibleFixedAssetsAssetsBNK', 1),
    ],
    'deferred_tax_assets_bank': [  # 繰延税金資産
        ('jppfs_cor:DeferredTaxAssetsAssetsBNK', 1),
    ],
    'customers_liabilities_for_acceptances_and_guarantees': [  # 支払承諾見返
        ('jppfs_cor:CustomersLiabilitiesForAcceptancesAndGuaranteesAssetsBNK', 1),
    ],
    'reserve_for_possible_loan_losses': [  # 貸倒引当金
        ('jppfs_cor:ReserveForPossibleLoanLossesAssetsBNK', 1),
    ],

    # 負債
    'deposits_bank': [  # 預金
        ('jppfs_cor:DepositsLiabilitiesBNK', 1),
    ],
    'negotiable_certificates_of_deposit': [  # 譲渡性預金
        ('jppfs_cor:NegotiableCertificatesOfDepositLiabilitiesBNK', 1),
    ],
    'call_money_bank': [  # コールマネー
        ('jppfs_cor:CallMoneyAndBillsSoldLiabilitiesBNK', 1),
    ],
    'payables_under_repurchase_agreements': [  # 売現先勘定
        ('jppfs_cor:PayablesUnderRepurchaseAgreementsLiabilitiesBNK', 1),
    ],
    'payables_under_securities_lending': [  # 売渡手形
        ('jppfs_cor:PayablesUnderSecuritiesLendingTransactionsLiabilitiesBNK', 1),
    ],
    'trading_liabilities_bank': [  # 特定取引負債
        ('jppfs_cor:TradingLiabilitiesLiabilitiesBNK', 1),
    ],
    'borrowed_money_bank': [  # 借用金
        ('jppfs_cor:BorrowedMoneyLiabilitiesBNK', 1),
    ],
    'foreign_exchanges_liabilities_bank': [  # 外国為替（負債）
        ('jppfs_cor:ForeignExchangesLiabilitiesBNK', 1),
    ],
    'bonds_payable_bank': [  # 社債
        ('jppfs_cor:BondsPayableLiabilitiesBNK', 1),
    ],
    'other_liabilities_bank': [  # その他負債
        ('jppfs_cor:OtherLiabilitiesLiabilitiesBNK', 1),
    ],
    'reserve_for_bonuses_bank': [  # 賞与引当金
        ('jppfs_cor:ReserveForBonusesLiabilitiesBNK', 1),
    ],
    'reserve_for_directors_bonuses_bank': [  # 役員賞与引当金
        ('jppfs_cor:ReserveForDirectorsBonusesLiabilitiesBNK', 1),
    ],
    'reserve_for_retirement_benefits_bank': [  # 退職給付引当金
        ('jppfs_cor:ReserveForRetirementBenefitsLiabilitiesBNK', 1),
    ],
    'deferred_tax_liabilities_bank': [  # 繰延税金負債
        ('jppfs_cor:DeferredTaxLiabilitiesLiabilitiesBNK', 1),
    ],
    'acceptances_and_guarantees': [  # 支払承諾
        ('jppfs_cor:AcceptancesAndGuaranteesLiabilitiesBNK', 1),
    ],

    # CF項目（Duration context）
    'net_change_in_loans_cf_bank': [  # 貸出金の純増減（CF）
        ('jppfs_cor:NetDecreaseIncreaseInLoansAndBillsDiscountedOpeCFBNK', 1),
    ],
    'net_change_in_deposits_cf_bank': [  # 預金の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInDepositOpeCFBNK', 1),
    ],
    'net_change_in_negotiable_certificates_of_deposit_cf': [  # 譲渡性預金の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInNegotiableCertificatesOfDepositOpeCFBNK', 1),
    ],
    'net_change_in_call_loans_cf_bank': [  # コールローン等の純増減（CF）
        ('jppfs_cor:NetDecreaseIncreaseInCallLoansOpeCFBNK', 1),
    ],
    'net_change_in_call_money_cf_bank': [  # コールマネー等の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInCallMoneyOpeCFBNK', 1),
    ],
    'net_change_in_borrowed_money_cf_bank': [  # 借用金の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInBorrowedMoneyOpeCFBNK', 1),
    ],
    'net_change_in_foreign_exchanges_cf_bank': [  # 外国為替（資産）の純増減（CF）
        ('jppfs_cor:NetDecreaseIncreaseInForeignExchangesAssetsOpeCFBNK', 1),
    ],
    'net_change_in_foreign_exchanges_liabilities_cf_bank': [  # 外国為替（負債）の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInForeignExchangesLiabilitiesOpeCFBNK', 1),
    ],
    'net_change_in_trading_assets_cf_bank': [  # 特定取引資産の純増減（CF）
        ('jppfs_cor:NetDecreaseIncreaseInTradingAssetsOpeCFBNK', 1),
    ],
    'net_change_in_trading_liabilities_cf_bank': [  # 特定取引負債の純増減（CF）
        ('jppfs_cor:NetIncreaseDecreaseInTradingLiabilitiesOpeCFBNK', 1),
    ],
}

# Instant contextを使うフィールド（B/S項目）
INSTANT_FIELDS = {
    # 資産
    'total_assets', 'current_assets', 'non_current_assets',
    'cash_and_deposits', 'notes_receivable', 'accounts_receivable',
    'trade_receivables', 'other_current_assets',
    'inventories', 'merchandise', 'work_in_progress', 'raw_materials', 'supplies',
    'property_plant_equipment', 'land', 'buildings',
    'intangible_assets', 'goodwill', 'investments',
    'deferred_tax_assets', 'other_non_current_assets',
    'right_of_use_assets', 'financial_assets_current', 'financial_assets_non_current',
    # 負債
    'current_liabilities', 'non_current_liabilities', 'total_liabilities',
    'notes_payable', 'accounts_payable', 'trade_payables', 'other_current_liabilities',
    'short_term_loans', 'current_portion_long_term', 'accrued_expenses', 'income_taxes_payable',
    'long_term_loans', 'bonds_payable', 'retirement_benefit_liability',
    'deferred_tax_liabilities', 'other_non_current_liabilities',
    'financial_liabilities_current', 'financial_liabilities_non_current', 'warranty_liability',
    # 純資産
    'total_equity', 'shareholders_equity', 'capital_stock', 'capital_surplus',
    'retained_earnings', 'treasury_stock', 'accumulated_other_comprehensive',
    'non_controlling_interests',
    # その他
    'cash_end', 'employee_count', 'shares_outstanding',
    # 株主・株式関連
    'treasury_shares', 'treasury_shares_in_own_name', 'shareholding_ratio', 'treasury_shareholding_ratio',
    'shareholders_total', 'shareholders_financial_institutions', 'shareholders_individuals',
    'shareholders_foreign_corporations', 'shareholders_foreign_individuals', 'shareholders_domestic_corporations',
    'shareholding_pct_financial_institutions', 'shareholding_pct_individuals', 'shareholding_pct_foreign_corporations',
    'shares_per_unit', 'shares_issued', 'voting_rights_count', 'shares_held',
    # 投資有価証券
    'investment_securities_carrying_amount_not_listed', 'investment_securities_carrying_amount_listed',
    'investment_securities_issues_not_listed', 'investment_securities_issues_listed',
    # 銀行業B/S項目（資産）
    'cash_due_from_banks', 'call_loans_bank', 'receivables_under_resale_agreements',
    'receivables_under_securities_borrowing', 'monetary_claims_bought', 'trading_assets_bank',
    'money_held_in_trust', 'securities_bank', 'loans_and_bills_bank', 'foreign_exchanges_bank',
    'other_assets_bank', 'tangible_fixed_assets_bank', 'intangible_fixed_assets_bank',
    'deferred_tax_assets_bank', 'customers_liabilities_for_acceptances_and_guarantees',
    'reserve_for_possible_loan_losses',
    # 銀行業B/S項目（負債）
    'deposits_bank', 'negotiable_certificates_of_deposit', 'call_money_bank',
    'payables_under_repurchase_agreements', 'payables_under_securities_lending',
    'trading_liabilities_bank', 'borrowed_money_bank', 'foreign_exchanges_liabilities_bank',
    'bonds_payable_bank', 'other_liabilities_bank', 'reserve_for_bonuses_bank',
    'reserve_for_directors_bonuses_bank', 'reserve_for_retirement_benefits_bank',
    'deferred_tax_liabilities_bank', 'acceptances_and_guarantees',
}


# ============================================================
# XBRL抽出関数
# ============================================================
def extract_xbrl_from_zip(zip_path: Path) -> Dict[str, Any]:
    """ZIPファイルからXBRLを抽出"""
    
    xbrl_content = None
    xbrl_filename = None
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # PublicDoc内の.xbrlを優先
            for name in zf.namelist():
                if name.endswith('.xbrl') and 'PublicDoc' in name:
                    xbrl_content = zf.read(name)
                    xbrl_filename = name
                    break
            
            # なければ任意の.xbrl
            if xbrl_content is None:
                for name in zf.namelist():
                    if name.endswith('.xbrl'):
                        xbrl_content = zf.read(name)
                        xbrl_filename = name
                        break
    except Exception as e:
        logger.error(f"ZIP読み込みエラー ({zip_path.name}): {e}")
        return {}
    
    if xbrl_content is None:
        logger.warning(f"XBRLファイルなし: {zip_path.name}")
        return {}
    
    logger.debug(f"  XBRL: {xbrl_filename}")
    
    if HAS_LXML:
        return _extract_with_lxml(xbrl_content)
    elif HAS_BS4:
        # bs4経路は _raw_tags を返さないため保存側の Interim-only ガードが無効化され、
        # 半期→年度混入 (S0 T4事故) がこの経路では再発しうる。単体のみフォールバックも無い。
        # lxml が正: この警告が出る環境は要修復 (pip install lxml)
        logger.warning(f"⚠️ lxml未導入のためbs4フォールバックで抽出: {zip_path.name} — Interimガード・単体フォールバック無効。lxml導入を推奨")
        return _extract_with_bs4(xbrl_content)
    else:
        logger.error("lxmlまたはbs4が必要です")
        return {}


def _extract_with_lxml(xbrl_content: bytes, _allow_nonconsolidated: bool = False) -> Dict[str, Any]:
    """lxmlを使ったXBRL抽出 - 全タグ取得版"""
    
    try:
        parser = etree.XMLParser(recover=True, huge_tree=True)
        root = etree.fromstring(xbrl_content, parser=parser)
    except Exception as e:
        logger.error(f"XML解析エラー: {e}")
        return {}
    
    # contextRef別にエレメントをインデックス化
    context_index = defaultdict(list)
    for elem in root.iter():
        ctx = elem.get('contextRef')
        if ctx:
            context_index[ctx].append(elem)
    
    # 優先度付きコンテキストパターン
    # 半期報告書 (jpcrp040300-ssr-001) は CurrentYear* ではなく Interim* を使う
    duration_patterns = [
        ('CurrentYearDuration_ConsolidatedMember', 1),
        ('CurrentYearDuration', 2),
        ('CurrentYTDDuration', 3),
        ('CurrentInterimDuration_ConsolidatedMember', 4),
        ('CurrentInterimDuration', 5),
        ('InterimDuration_ConsolidatedMember', 6),
        ('InterimDuration', 7),
    ]
    instant_patterns = [
        ('CurrentYearInstant_ConsolidatedMember', 1),
        ('CurrentYearInstant', 2),
        ('CurrentInterimInstant_ConsolidatedMember', 3),
        ('CurrentInterimInstant', 4),
        ('InterimInstant_ConsolidatedMember', 5),
        ('InterimInstant', 6),
    ]
    
    def find_best_contexts(patterns, is_instant=False, allow_nonconsolidated=False):
        """パターンにマッチするコンテキストを優先度順に返す"""
        matched = []
        for ctx_name in context_index.keys():
            if 'NonConsolidated' in ctx_name and not allow_nonconsolidated:
                continue
            # 前年同期データ (Prior1*, Prior2*, ...) を除外
            if 'Prior' in ctx_name:
                continue
            # セグメント別 (_xxxMember) を除外。ただし _ConsolidatedMember は許可
            if '_Member' in ctx_name and 'ConsolidatedMember' not in ctx_name:
                continue
            # Instant/Duration判定
            if is_instant and 'Duration' in ctx_name:
                continue
            if not is_instant and 'Instant' in ctx_name:
                continue
            
            for pattern, priority in patterns:
                if pattern in ctx_name:
                    matched.append((ctx_name, priority))
                    break
        matched.sort(key=lambda x: x[1])
        return [m[0] for m in matched]
    
    duration_contexts = find_best_contexts(duration_patterns, is_instant=False,
                                           allow_nonconsolidated=_allow_nonconsolidated)
    instant_contexts = find_best_contexts(instant_patterns, is_instant=True,
                                          allow_nonconsolidated=_allow_nonconsolidated)

    # フォールバック
    if not duration_contexts:
        duration_contexts = [c for c in context_index.keys()
                           if ('NonConsolidated' not in c or _allow_nonconsolidated) and 'Instant' not in c]
    if not instant_contexts:
        instant_contexts = [c for c in context_index.keys()
                          if ('NonConsolidated' not in c or _allow_nonconsolidated) and 'Duration' not in c]
    
    extracted = {}
    raw_tags = {}  # 全タグを保存
    
    # ★★★ 全タグを取得 ★★★
    seen_tags = set()
    for ctx_name in list(duration_contexts) + list(instant_contexts):
        for elem in context_index.get(ctx_name, []):
            if not elem.tag:
                continue
            
            try:
                local_name = etree.QName(elem.tag).localname
                namespace = etree.QName(elem.tag).namespace or ""
            except:
                continue
            
            # フルタグ名
            full_tag = f"{namespace}:{local_name}" if namespace else local_name
            
            # 既に取得済みならスキップ（優先度の高いコンテキストを優先）
            if local_name in seen_tags:
                continue
            
            value_text = (elem.text or '').strip()
            if not value_text:
                continue
            
            # 数値かどうか判定
            try:
                value = float(value_text.replace(',', ''))
                seen_tags.add(local_name)
                
                # unitRef と decimals を取得
                unit_ref = elem.get('unitRef', '')
                decimals = elem.get('decimals', '')
                
                # 全タグを保存
                raw_tags[local_name] = {
                    'value': value,
                    'unit': unit_ref,
                    'decimals': decimals,
                    'context': ctx_name,
                    'full_tag': full_tag
                }
            except ValueError:
                # 数値でない場合はテキストとして保存
                if local_name not in seen_tags:
                    seen_tags.add(local_name)
                    raw_tags[local_name] = {
                        'value': value_text,
                        'type': 'text',
                        'context': ctx_name,
                        'full_tag': full_tag
                    }
    
    # ★★★ 既知タグを標準フィールドにマッピング ★★★
    for field_name, tag_list in FALLBACK_TAGS.items():
        is_instant = field_name in INSTANT_FIELDS
        target_contexts = instant_contexts if is_instant else duration_contexts
        
        best_value = None
        best_priority = 999
        
        for tag_full, priority in tag_list:
            if best_value is not None and best_priority <= priority:
                continue
            
            tag_local = tag_full.split(':')[-1]
            
            # raw_tagsから探す
            if tag_local in raw_tags:
                tag_data = raw_tags[tag_local]
                if isinstance(tag_data.get('value'), (int, float)):
                    if priority < best_priority:
                        best_value = tag_data['value']
                        best_priority = priority
        
        if best_value is not None:
            extracted[field_name] = best_value
    
    # 派生指標を計算
    _calculate_derived_metrics(extracted)

    # raw_tagsも保存（未知タグを後で分析可能）
    extracted['_raw_tags'] = raw_tags
    extracted['_raw_tags_count'] = len(raw_tags)

    # 単体のみ企業フォールバック: 連結優先で抽出した結果コアの財務数値が全滅している場合
    # (株式数などの非財務のみ)、財務が *_NonConsolidatedMember にしか無い単体のみ提出者
    # (例: 9872北恵の半期は汎用 InterimDuration に非財務のみ、P/L・B/Sは単体コンテキスト)。
    # 単体コンテキストを許可して一度だけ再抽出し、改善した時だけ採用する (連結企業は不変)。
    _CORE_FIELDS = ('revenue', 'operating_income', 'ordinary_income', 'net_income', 'total_assets', 'total_equity')
    if (not _allow_nonconsolidated
            and all(extracted.get(k) is None for k in _CORE_FIELDS)
            and any('NonConsolidated' in c for c in context_index)):
        retry = _extract_with_lxml(xbrl_content, _allow_nonconsolidated=True)
        if any(retry.get(k) is not None for k in _CORE_FIELDS):
            logger.info("  単体のみ提出者と判定 → NonConsolidatedコンテキストで再抽出を採用")
            return retry

    return extracted


def _extract_with_bs4(xbrl_content: bytes) -> Dict[str, Any]:
    """BeautifulSoupを使ったXBRL抽出（フォールバック）"""
    
    try:
        soup = BeautifulSoup(xbrl_content, 'lxml-xml')
    except:
        soup = BeautifulSoup(xbrl_content, 'html.parser')
    
    duration_patterns = ['CurrentYearDuration', 'CurrentYTDDuration']
    instant_patterns = ['CurrentYearInstant']
    
    def matches_context(ctx, patterns):
        if not ctx or 'NonConsolidated' in ctx:
            return False
        return any(p in ctx for p in patterns)
    
    all_elements = soup.find_all(True)
    extracted = {}
    
    for field_name, tag_list in FALLBACK_TAGS.items():
        is_instant = field_name in INSTANT_FIELDS
        patterns = instant_patterns if is_instant else duration_patterns
        
        best_value = None
        best_priority = 999
        
        for tag_full, priority in tag_list:
            if best_value is not None and best_priority <= priority:
                continue
            
            tag_local = tag_full.split(':')[-1].lower()
            
            for elem in all_elements:
                elem_local = (elem.name or '').split(':')[-1].lower()
                if elem_local != tag_local:
                    continue
                
                ctx = elem.get('contextref', '')
                if not matches_context(ctx, patterns):
                    continue
                
                value_text = elem.get_text(strip=True)
                if not value_text:
                    continue
                
                try:
                    value = float(value_text.replace(',', ''))
                    if priority < best_priority:
                        best_value = value
                        best_priority = priority
                except:
                    pass
        
        if best_value is not None:
            extracted[field_name] = best_value
    
    _calculate_derived_metrics(extracted)
    return extracted


def _calculate_derived_metrics(extracted: Dict):
    """派生指標を計算（拡張版）"""
    
    # ========== 収益性指標 ==========
    # ROE = 純利益 / 純資産
    if 'net_income' in extracted and 'total_equity' in extracted and extracted['total_equity']:
        extracted['roe_calc'] = round((extracted['net_income'] / extracted['total_equity']) * 100, 2)
    
    # ROA = 純利益 / 総資産
    if 'net_income' in extracted and 'total_assets' in extracted and extracted['total_assets']:
        extracted['roa_calc'] = round((extracted['net_income'] / extracted['total_assets']) * 100, 2)

    # 営業利益計算（IFRSでタグがない場合: 売上総利益 - 販管費）
    if ('operating_income' not in extracted or extracted.get('operating_income') is None):
        if 'gross_profit' in extracted and 'selling_general_admin' in extracted:
            if extracted['gross_profit'] and extracted['selling_general_admin']:
                extracted['operating_income'] = extracted['gross_profit'] - extracted['selling_general_admin']

    # 営業利益率 = 営業利益 / 売上高
    if 'operating_income' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['operating_margin_calc'] = round((extracted['operating_income'] / extracted['revenue']) * 100, 2)
    
    # 粗利率 = 売上総利益 / 売上高
    if 'gross_profit' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['gross_margin_calc'] = round((extracted['gross_profit'] / extracted['revenue']) * 100, 2)
    
    # 純利益率 = 純利益 / 売上高
    if 'net_income' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['net_margin_calc'] = round((extracted['net_income'] / extracted['revenue']) * 100, 2)
    
    # 経常利益率 = 経常利益 / 売上高
    if 'ordinary_income' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['ordinary_margin_calc'] = round((extracted['ordinary_income'] / extracted['revenue']) * 100, 2)
    
    # 販管費率 = 販管費 / 売上高
    if 'selling_general_admin' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['sga_ratio_calc'] = round((extracted['selling_general_admin'] / extracted['revenue']) * 100, 2)
    
    # 原価率 = 売上原価 / 売上高
    if 'cost_of_sales' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['cost_ratio_calc'] = round((extracted['cost_of_sales'] / extracted['revenue']) * 100, 2)
    
    # ========== 安全性指標 ==========
    # 自己資本比率 = 純資産 / 総資産
    if 'total_equity' in extracted and 'total_assets' in extracted and extracted['total_assets']:
        extracted['equity_ratio_calc'] = round((extracted['total_equity'] / extracted['total_assets']) * 100, 2)
    
    # 負債比率 = 負債 / 純資産
    if 'total_liabilities' in extracted and 'total_equity' in extracted and extracted['total_equity']:
        extracted['debt_equity_ratio_calc'] = round((extracted['total_liabilities'] / extracted['total_equity']) * 100, 2)
    
    # 流動比率 = 流動資産 / 流動負債
    if 'current_assets' in extracted and 'current_liabilities' in extracted and extracted['current_liabilities']:
        extracted['current_ratio_calc'] = round((extracted['current_assets'] / extracted['current_liabilities']) * 100, 2)
    
    # 有利子負債（short_term_loans + long_term_loans + bonds_payable + current_portion_long_term）
    interest_bearing_debt = 0
    for key in ['short_term_loans', 'long_term_loans', 'bonds_payable', 'current_portion_long_term']:
        if key in extracted and extracted[key]:
            interest_bearing_debt += extracted[key]
    if interest_bearing_debt > 0:
        extracted['interest_bearing_debt_calc'] = interest_bearing_debt
        
        # D/Eレシオ = 有利子負債 / 純資産
        if 'total_equity' in extracted and extracted['total_equity']:
            extracted['de_ratio_calc'] = round((interest_bearing_debt / extracted['total_equity']) * 100, 2)
        
        # ネットD/Eレシオ = (有利子負債 - 現金) / 純資産
        if 'cash_and_deposits' in extracted and 'total_equity' in extracted and extracted['total_equity']:
            net_debt = interest_bearing_debt - extracted['cash_and_deposits']
            extracted['net_de_ratio_calc'] = round((net_debt / extracted['total_equity']) * 100, 2)
    
    # ========== 効率性指標 ==========
    # 総資産回転率 = 売上高 / 総資産
    if 'revenue' in extracted and 'total_assets' in extracted and extracted['total_assets']:
        extracted['asset_turnover_calc'] = round(extracted['revenue'] / extracted['total_assets'], 2)
    
    # 棚卸資産: コンポーネントから合計を補完
    if 'inventories' not in extracted or not extracted.get('inventories'):
        inv_sum = sum(extracted.get(k, 0) or 0 for k in ['merchandise', 'work_in_progress', 'raw_materials', 'supplies'])
        if inv_sum > 0:
            extracted['inventories'] = inv_sum

    # 棚卸資産回転率 = 売上高 / 棚卸資産
    # 回転率が0に丸まる零細売上 (2656ベクター等) でのゼロ除算、revenue=None でのTypeErrorを防御
    if (extracted.get('revenue') or 0) > 0 and 'inventories' in extracted and extracted['inventories']:
        extracted['inventory_turnover_calc'] = round(extracted['revenue'] / extracted['inventories'], 2)
        # 棚卸資産回転日数
        if extracted['inventory_turnover_calc'] > 0:
            extracted['inventory_days_calc'] = round(365 / extracted['inventory_turnover_calc'], 1)

    # 売上債権回転率 = 売上高 / 売上債権
    receivables = 0
    if 'trade_receivables' in extracted and extracted['trade_receivables']:
        receivables = extracted['trade_receivables']  # 結合タグ優先
    else:
        if 'notes_receivable' in extracted and extracted['notes_receivable']:
            receivables += extracted['notes_receivable']
        if 'accounts_receivable' in extracted and extracted['accounts_receivable']:
            receivables += extracted['accounts_receivable']
    if receivables > 0 and (extracted.get('revenue') or 0) > 0:
        extracted['receivables_turnover_calc'] = round(extracted['revenue'] / receivables, 2)
        if extracted['receivables_turnover_calc'] > 0:
            extracted['receivables_days_calc'] = round(365 / extracted['receivables_turnover_calc'], 1)
    
    # 仕入債務回転率 = 売上原価 / 仕入債務
    payables = 0
    if 'trade_payables' in extracted and extracted['trade_payables']:
        payables = extracted['trade_payables']  # 結合タグ優先
    else:
        if 'notes_payable' in extracted and extracted['notes_payable']:
            payables += extracted['notes_payable']
        if 'accounts_payable' in extracted and extracted['accounts_payable']:
            payables += extracted['accounts_payable']
    if payables > 0 and (extracted.get('cost_of_sales') or 0) > 0:
        extracted['payables_turnover_calc'] = round(extracted['cost_of_sales'] / payables, 2)
        if extracted['payables_turnover_calc'] > 0:
            extracted['payables_days_calc'] = round(365 / extracted['payables_turnover_calc'], 1)
    
    # CCC（キャッシュコンバージョンサイクル）= 売上債権回転日数 + 棚卸資産回転日数 - 仕入債務回転日数
    if all(key in extracted for key in ['receivables_days_calc', 'inventory_days_calc', 'payables_days_calc']):
        extracted['ccc_calc'] = round(
            extracted['receivables_days_calc'] + 
            extracted['inventory_days_calc'] - 
            extracted['payables_days_calc'], 1
        )
    
    # ========== キャッシュフロー指標 ==========
    # FCF = 営業CF + 投資CF（投資CFは通常マイナス）
    if 'operating_cf' in extracted and 'investing_cf' in extracted:
        extracted['fcf_calc'] = extracted['operating_cf'] + extracted['investing_cf']
    
    # 営業CFマージン = 営業CF / 売上高
    if 'operating_cf' in extracted and 'revenue' in extracted and extracted['revenue']:
        extracted['ocf_margin_calc'] = round((extracted['operating_cf'] / extracted['revenue']) * 100, 2)
    
    # CAPEX / 減価償却
    if 'purchase_ppe' in extracted and 'depreciation_cf' in extracted and extracted['depreciation_cf']:
        # purchase_ppeは通常マイナス値
        capex = abs(extracted['purchase_ppe'])
        extracted['capex_depreciation_ratio_calc'] = round((capex / extracted['depreciation_cf']) * 100, 2)
    
    # ========== 投資指標 ==========
    # PER用（株価は外部データなので計算不可、EPSのみ）
    # PBR用（株価は外部データなので計算不可、BPSのみ）

    # 1株当たり配当金計算（タグがない場合: 配当総額 / 発行済株式数）
    if ('dividend_per_share' not in extracted or extracted.get('dividend_per_share') is None):
        if 'dividends_paid' in extracted and 'shares_issued' in extracted:
            if extracted['dividends_paid'] and extracted['shares_issued']:
                # dividends_paidは通常負の値なので絶対値を取る
                extracted['dividend_per_share'] = abs(extracted['dividends_paid']) / extracted['shares_issued']

    # 配当性向 = 配当 / EPS
    if 'dividend_per_share' in extracted and 'eps' in extracted and extracted['eps']:
        extracted['payout_ratio_calc'] = round((extracted['dividend_per_share'] / extracted['eps']) * 100, 2)

    # ========== PIK / 信用ストレス指標 ==========
    # 統一支払利息の決定 (狭義のみ):
    # 1. interest_expenses (純粋利息: J-GAAP InterestExpensesNOE / IFRS InterestExpensesIFRS / Borrowings系)
    # 2. リース利息のsum (構成要素から推定)
    # 3. interest_expense_bank (銀行業)
    # ⚠️ finance_costs は使わない（為替差損・partnership loss・評価損等を含むため PIK計算が破綻する）
    pl_interest = extracted.get('interest_expenses')
    source = None
    if pl_interest is not None:
        source = 'narrow'
    if pl_interest is None:
        # リース利息と組合せ可能なら sum (借入金タグが別途取得済みの場合のみ意味あり)
        lease = extracted.get('interest_expenses_lease')
        if lease is not None and pl_interest is not None:
            pl_interest = pl_interest + lease
            source = 'narrow_with_lease'
    if pl_interest is None:
        pl_interest = extracted.get('interest_expense_bank')
        if pl_interest is not None:
            source = 'bank'

    if pl_interest is not None:
        extracted['interest_expense_unified'] = pl_interest
        extracted['interest_expense_source'] = source

    # source 不明の場合は利息関連の派生指標をクリア（旧データ残骸の除去）
    if source is None:
        for k in ('interest_expense_unified', 'interest_expense_source',
                  'cash_interest_coverage_ratio_calc', 'noncash_interest_ratio_calc',
                  'pik_interest_abs_calc', 'interest_coverage_ratio_calc',
                  'cash_icr_calc', 'pik_estimation_quality',
                  'interest_paid_cf_abs'):
            if k in extracted:
                del extracted[k]

    cf_interest = extracted.get('interest_paid_cf')
    if cf_interest is not None:
        cf_interest_abs = abs(cf_interest)
        extracted['interest_paid_cf_abs'] = cf_interest_abs

    # A1: 現金利息カバー率 = |CF利息支払額| / PL支払利息
    if pl_interest and cf_interest is not None and pl_interest > 0:
        extracted['cash_interest_coverage_ratio_calc'] = round(abs(cf_interest) / pl_interest, 3)

        # A2: 非現金利息比率 = (PL - |CF|) / PL
        noncash = pl_interest - abs(cf_interest)
        extracted['noncash_interest_ratio_calc'] = round((noncash / pl_interest) * 100, 2)

        # A3: PIK利息絶対額 (差分導出)
        if noncash > 0:
            extracted['pik_interest_abs_calc'] = noncash
        extracted['pik_estimation_quality'] = 'derived'  # JPは常に差分導出
    elif pl_interest is not None:
        extracted['pik_estimation_quality'] = 'unavailable'  # CF利息支払額なし

    # B1: インタレスト・カバレッジ = EBIT / 支払利息（EBIT ≒ 営業利益）
    if pl_interest and extracted.get('operating_income') and pl_interest > 0:
        extracted['interest_coverage_ratio_calc'] = round(extracted['operating_income'] / pl_interest, 2)

    # B2: 現金ICR = 営業CF / |CF利息支払額|
    if cf_interest is not None and extracted.get('operating_cf') and abs(cf_interest) > 0:
        extracted['cash_icr_calc'] = round(extracted['operating_cf'] / abs(cf_interest), 2)

    # B3: Net Debt / EBITDA
    # EBITDA = 営業利益 + 減価償却費
    ebitda_calc = None
    if extracted.get('operating_income') and extracted.get('depreciation_cf'):
        ebitda_calc = extracted['operating_income'] + extracted['depreciation_cf']
        extracted['ebitda_calc'] = ebitda_calc
    if ebitda_calc and ebitda_calc > 0 and extracted.get('interest_bearing_debt_calc'):
        cash = extracted.get('cash_and_deposits', 0) or 0
        net_debt = extracted['interest_bearing_debt_calc'] - cash
        extracted['net_debt_ebitda_calc'] = round(net_debt / ebitda_calc, 2)

    # B4: 短期借入比率 = (1年以内返済負債) / 総有利子負債
    ibd = extracted.get('interest_bearing_debt_calc')
    if ibd and ibd > 0:
        short_term = (extracted.get('short_term_loans') or 0) + (extracted.get('current_portion_long_term') or 0)
        if short_term > 0:
            extracted['short_term_debt_ratio_calc'] = round((short_term / ibd) * 100, 2)

    # A4: 未収利息（proxy: 未収収益）の変化は年次比較が必要なので、
    # ここでは絶対値のみ保持。YoY計算は上位パイプラインで実施。

    # 貸倒引当金合計（B/S資産側）: 総額タグがあればそれを、無ければ流動+固定の絶対値合計
    allowance_total = extracted.get('allowance_for_doubtful_total')
    if allowance_total is not None:
        extracted['allowance_for_doubtful_total_calc'] = abs(allowance_total)
    else:
        cur = extracted.get('allowance_for_doubtful_current') or 0
        non_cur = extracted.get('allowance_for_doubtful_non_current') or 0
        if cur or non_cur:
            extracted['allowance_for_doubtful_total_calc'] = abs(cur) + abs(non_cur)


# ============================================================
# ZIPファイル検索
# ============================================================
def find_xbrl_zips(company_code: str, years: List[str], base_path: Path) -> List[Tuple[str, Path]]:
    """
    指定企業・年度のXBRL ZIPを検索
    Returns: [(year, zip_path), ...]
    """
    results = []

    for year in years:
        # 有報を優先
        for doc_type in ['有報', '四半期']:
            folder = base_path / year / doc_type
            if not folder.exists():
                continue

            # 企業コードで始まるZIPを検索
            zips = list(folder.glob(f"{company_code}_*.zip"))
            if zips:
                results.append((year, pick_latest_zip(zips)))
                break  # 有報があれば四半期は不要

        # 半期報告書は年度ドキュメントと独立に別エントリで返す。
        # 保存側が半期を {year}_Q2.json へ振り替えるので年度データは汚さない。
        # (2026-07-03 の 5942/9872 取込漏れの根治: 半期フォルダが年度モードで一切
        #  スキャンされず、正しくラベルされた半期ZIPが誰にも読まれなかった)
        interim_folder = base_path / year / '半期'
        if interim_folder.exists():
            zips = list(interim_folder.glob(f"{company_code}_*.zip"))
            if zips:
                results.append((year, pick_latest_zip(zips)))

    return results


def scan_all_zips(base_path: Path, years: List[str] = None) -> Dict[str, List[Tuple[str, Path]]]:
    """
    指定フォルダ内の全ZIPをスキャン
    Returns: {company_code: [(year, zip_path), ...], ...}
    """
    company_zips = defaultdict(list)

    if years is None:
        # 全年度をスキャン
        year_folders = [f for f in base_path.iterdir() if f.is_dir() and f.name.isdigit()]
    else:
        year_folders = [base_path / y for y in years if (base_path / y).exists()]

    for year_folder in year_folders:
        year = year_folder.name

        # 半期も含める: 企業の「発見」に使われるリスト。半期しか提出していない企業
        # (非3月決算の中間期など) も batch_process の対象に乗せる
        for doc_type in ['有報', '四半期', '半期']:
            doc_folder = year_folder / doc_type
            if not doc_folder.exists():
                continue

            for zip_path in doc_folder.glob("*.zip"):
                # ファイル名から企業コードを抽出（例: 1301_極洋_有報_2022.zip）
                # 英数字コード (303A 等の新規IPO) も対象 (\d のみだと全員スキャン漏れ)
                match = re.match(r'^([0-9A-Z]{4,5})_', zip_path.name)
                if match:
                    company_code = match.group(1)
                    company_zips[company_code].append((year, zip_path))

    return company_zips


# ============================================================
# 四半期レポート用関数
# ============================================================
def determine_quarter(filename: str, fy_end_month: int = 3) -> Optional[Tuple[int, int]]:
    """
    四半期ZIPファイル名から決算年度と四半期番号を判定

    Args:
        filename: ZIPファイル名 (例: 7203_トヨタ自動車株式会社_20230630_1Q_S100RIZN.zip)
        fy_end_month: 決算月 (デフォルト3=3月決算)

    Returns:
        (fiscal_year, quarter) e.g., (2024, 1) or None if cannot determine
    """
    # ファイル名から日付を抽出: _YYYYMMDD_
    date_match = re.search(r'_(\d{8})_', filename)
    if not date_match:
        return None

    date_str = date_match.group(1)
    period_end_year = int(date_str[:4])
    period_end_month = int(date_str[4:6])

    # 決算年度を判定: 期末月以降なら翌年度
    if period_end_month > fy_end_month:
        fiscal_year = period_end_year + 1
    else:
        fiscal_year = period_end_year

    # 四半期番号を判定
    quarter = ((period_end_month - fy_end_month - 1) % 12) // 3 + 1

    # 半期報告書: EDINET の文書日付表記が会計年度末日 (例 20260331) になるケースがある。
    # ZIP内部 instance は中間期末 (例 2025-09-30) を持つので、ここでは fiscal_year=YYYY, Q2 として返す。
    # 命名は旧 `_半期_` と daily_update T4以降の `_半期報告書_` / `_訂正半期報告書_` の両方に対応
    # (`_四半期_` は前が「四」なのでこの正規表現に一致しない)。
    # 注意: 非3月決算企業を四半期モードで処理する場合は --fy-end-month をその企業の決算月に
    # 合わせること (日次経路は年度モード+Interim-onlyガードで処理されるためこの制約を受けない)
    if re.search(r'_(訂正)?半期(報告書)?_', filename) and period_end_month == fy_end_month:
        return (period_end_year, 2)

    if quarter < 1 or quarter > 3:
        return None

    return (fiscal_year, quarter)


def find_quarterly_zips(company_code: str, years: List[str], base_path: Path,
                        fy_end_month: int = 3) -> List[Tuple[str, Path]]:
    """
    指定企業の四半期ZIPを検索

    Returns: [(output_key, zip_path), ...] e.g., [("2024_Q1", zip_path), ...]
    """
    results = []

    for year in years:
        for doc_folder_name in ['四半期', '半期']:
            folder = base_path / year / doc_folder_name
            if not folder.exists():
                continue

            zips = list(folder.glob(f"{company_code}_*.zip"))
            for zip_path in zips:
                qinfo = determine_quarter(zip_path.name, fy_end_month)
                if qinfo is None:
                    logger.warning(f"  ⚠️ 四半期判定失敗: {zip_path.name}")
                    continue

                fiscal_year, quarter = qinfo
                output_key = f"{fiscal_year}_Q{quarter}"
                results.append((output_key, zip_path))

    # output_keyでソート (2020_Q1, 2020_Q2, ...)
    results.sort(key=lambda x: x[0])
    return results


def scan_all_quarterly_zips(base_path: Path, years: List[str] = None,
                            fy_end_month: int = 3) -> Dict[str, List[Tuple[str, Path]]]:
    """
    四半期ZIPを全企業スキャン
    Returns: {company_code: [(output_key, zip_path), ...], ...}
    """
    company_zips = defaultdict(list)

    if years is None:
        year_folders = [f for f in base_path.iterdir() if f.is_dir() and f.name.isdigit()]
    else:
        year_folders = [base_path / y for y in years if (base_path / y).exists()]

    for year_folder in year_folders:
        for doc_folder_name in ['四半期', '半期']:
            doc_folder = year_folder / doc_folder_name
            if not doc_folder.exists():
                continue

            for zip_path in doc_folder.glob("*.zip"):
                match = re.match(r'^([0-9A-Z]{4,5})_', zip_path.name)
                if not match:
                    continue

                company_code = match.group(1)
                qinfo = determine_quarter(zip_path.name, fy_end_month)
                if qinfo is None:
                    continue

                fiscal_year, quarter = qinfo
                output_key = f"{fiscal_year}_Q{quarter}"
                company_zips[company_code].append((output_key, zip_path))

    # 各企業のZIPをソート
    for code in company_zips:
        company_zips[code].sort(key=lambda x: x[0])

    return company_zips


# ============================================================
# --skip-existing の docID ベース判定 (POSTMORTEM_2026-07-05 R4)
# ============================================================
DOCID_PATTERN = re.compile(r'(S10[0-9][0-9A-Z]{4})')  # EDINET docID (例 S100YOAB)


def extract_docid(name: str) -> Optional[str]:
    """ファイル名/ソース名から EDINET docID を取り出す。短信(TDnet)由来などは None。"""
    m = DOCID_PATTERN.search(name or "")
    return m.group(1) if m else None


def pick_latest_zip(zips: List[Path]) -> Path:
    """同一企業・同一年フォルダ内から docID 降順 (最新提出優先) で1本選ぶ。

    旧実装の「最大サイズ」選択は (a) 差分のみで小さい訂正報告書が永遠に選ばれない、
    (b) 逆に大きい旧年度の訂正が本物の有報をシャドーイングする実害があった
    (レビュー実測: KDDI 9433 の 2026.json に FY2025 訂正データが固定、恒久STALE 26社)。
    docID はほぼ提出順で昇順のため、最新 docID = 最新提出。docID 無しはサイズで後順。
    """
    return max(zips, key=lambda z: (extract_docid(z.name) or "", z.stat().st_size))


def should_skip_existing(existing_json: Path, zip_name: str) -> Tuple[bool, str]:
    """--skip-existing の判定。「ファイルが存在する=正しい」を仮定しない (壊れ残骸の自己防衛防止)。

    skipする  : 同一docIDを抽出済み / 既存の方が新しいdocID (古いZIPでのダウングレード防止)
                / docID不明だが既存が健全 (従来挙動)
    再抽出する: 既存JSONが壊れている / tanshin由来 (EDINETの方が網羅的)
                / ZIPのdocIDの方が新しい (訂正有報・訂正半期の反映)
    """
    try:
        data = json.loads(existing_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, "既存JSONが壊れている → 再抽出"
    if data.get("source") == "tanshin":
        return False, "既存tanshin → EDINETで上書き"
    # 意味的な壊れ残骸 (parseはできるがコア財務が全滅) にブロック権を与えない (POSTMORTEM R4)
    data_block = data.get("data") or {}
    if not any(data_block.get(k) is not None for k in
               ('revenue', 'operating_income', 'ordinary_income', 'net_income', 'total_assets', 'total_equity')):
        return False, "既存のコア財務が全滅 (壊れ残骸) → 再抽出"
    new_id = extract_docid(zip_name)
    old_id = extract_docid(str(data.get("source_file") or ""))
    if new_id is None or old_id is None:
        return True, "既存データあり → スキップ"
    if new_id == old_id:
        return True, f"同一docID {new_id} 抽出済み → スキップ"
    if new_id > old_id:  # docIDはほぼ提出順で昇順 (audit_yuho_coverage.py と同じ仮定)
        return False, f"新docID {new_id} > 既存 {old_id} (訂正版) → 再抽出"
    return True, f"既存 {old_id} の方が新しい → スキップ (ダウングレード防止)"


def process_company_quarterly(company_code: str, years: List[str],
                              company_names: Dict[str, str], output_base: Path,
                              skip_existing: bool = False,
                              fy_end_month: int = 3) -> Dict:
    """四半期ZIPを処理して xbrl_store に保存"""

    results = {
        "company_code": company_code,
        "quarters_processed": [],
        "quarters_failed": [],
        "errors": [],
    }

    # 四半期ZIPを検索
    zips = find_quarterly_zips(company_code, years, Config.XBRL_BASE, fy_end_month)

    if not zips:
        results["errors"].append("四半期ZIPファイルが見つかりません")
        return results

    # 企業名を取得
    company_name = company_names.get(company_code)
    if not company_name and zips:
        company_name = get_company_name_from_zip(zips[0][1])

    logger.info(f"\n📦 {company_code} {company_name}: 四半期 {len(zips)}件")

    company_dir = output_base / f"{company_code}_{company_name}"
    company_dir.mkdir(parents=True, exist_ok=True)

    for output_key, zip_path in zips:
        try:
            # --skip-existing: docID比較で判定 (同一docID→skip / 壊れ・tanshin・訂正版→再抽出)
            if skip_existing:
                existing_json = company_dir / f"{output_key}.json"
                if existing_json.exists():
                    do_skip, reason = should_skip_existing(existing_json, zip_path.name)
                    if do_skip:
                        logger.info(f"  ⏭️ {output_key}: {reason}")
                        results["quarters_processed"].append(output_key)
                        continue
                    logger.info(f"  🔄 {output_key}: {reason}")

            logger.info(f"  📄 {output_key}: {zip_path.name}")

            # XBRL抽出（既存関数をそのまま利用）
            xbrl_data = extract_xbrl_from_zip(zip_path)

            if not xbrl_data:
                results["quarters_failed"].append(output_key)
                results["errors"].append(f"{output_key}: XBRL抽出失敗")
                continue

            # raw_tagsを分離
            raw_tags = xbrl_data.pop('_raw_tags', {})
            raw_tags_count = xbrl_data.pop('_raw_tags_count', 0)

            # 四半期JSON保存
            year_file = company_dir / f"{output_key}.json"
            save_data = {
                "company_code": company_code,
                "company_name": company_name,
                "fiscal_year": output_key.split('_')[0],  # "2024"
                "quarter": int(output_key.split('_Q')[1]),  # 1, 2, or 3
                "period": output_key,  # "2024_Q1"
                "report_type": "quarterly",
                "extracted_at": datetime.now().isoformat(),
                "source_file": zip_path.name,
                "raw_tags_count": raw_tags_count,
                "data": xbrl_data
            }

            with open(year_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            logger.info(f"  💾 保存: {year_file}")

            # raw_tagsも保存
            if raw_tags:
                raw_file = company_dir / f"{output_key}_raw_tags.json"
                raw_save_data = {
                    "company_code": company_code,
                    "company_name": company_name,
                    "fiscal_year": output_key.split('_')[0],
                    "quarter": int(output_key.split('_Q')[1]),
                    "period": output_key,
                    "extracted_at": datetime.now().isoformat(),
                    "source_file": zip_path.name,
                    "tags_count": len(raw_tags),
                    "tags": raw_tags
                }
                with open(raw_file, 'w', encoding='utf-8') as f:
                    json.dump(raw_save_data, f, ensure_ascii=False, indent=2)

            # 主要数値ログ
            rev = xbrl_data.get('revenue')
            op = xbrl_data.get('operating_income')
            raw_count = sum(1 for k, v in xbrl_data.items() if not k.endswith('_calc') and v is not None)
            calc_count = sum(1 for k, v in xbrl_data.items() if k.endswith('_calc') and v is not None)

            if rev:
                logger.info(f"    売上: {rev/1e8:.1f}億円" + (f", 営業利益: {op/1e8:.1f}億円" if op else ""))
            logger.info(f"    抽出: {raw_count}項目 + 派生{calc_count}項目 = 計{raw_count + calc_count}項目")

            results["quarters_processed"].append(output_key)

        except Exception as e:
            results["quarters_failed"].append(output_key)
            results["errors"].append(f"{output_key}: {str(e)}")
            logger.error(f"  ❌ {output_key}: {e}")

    return results


def batch_process_quarterly(companies: List[str], years: List[str], output_base: Path,
                            skip_existing: bool = False, skip: int = 0,
                            fy_end_month: int = 3) -> List[Dict]:
    """四半期レポートのバッチ処理"""

    company_names = load_company_list_from_sheets()

    results = []
    total = len(companies)

    logger.info(f"\n{'='*60}")
    logger.info(f"🚀 四半期XBRL バッチ抽出開始: {total}社")
    if skip > 0:
        logger.info(f"⏭️ 最初の{skip}社をスキップ")
    logger.info(f"{'='*60}")

    for i, company_code in enumerate(companies):
        if i < skip:
            continue
        logger.info(f"\n[{i+1}/{total}] 処理中...")
        result = process_company_quarterly(
            company_code, years, company_names, output_base,
            skip_existing=skip_existing, fy_end_month=fy_end_month
        )
        results.append(result)

    # サマリー
    success = sum(1 for r in results if r["quarters_processed"])
    failed = sum(1 for r in results if not r["quarters_processed"] and r["errors"])
    total_quarters = sum(len(r["quarters_processed"]) for r in results)

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 四半期バッチ完了")
    logger.info(f"  成功: {success}社 ({total_quarters}四半期)")
    logger.info(f"  失敗: {failed}社")
    logger.info(f"{'='*60}")

    return results


# ============================================================
# 企業情報取得
# ============================================================
def get_company_name_from_zip(zip_path: Path) -> str:
    """ZIPファイル名から企業名を取得"""
    # 例: 1301_極洋_有報_2022.zip → 極洋
    parts = zip_path.stem.split('_')
    if len(parts) >= 2:
        return parts[1]
    return "Unknown"


def load_company_list_from_sheets() -> Dict[str, str]:
    """Google Sheetsから企業リストを取得"""
    if not HAS_GSPREAD:
        return {}
    
    sa_path = os.environ.get("GOOGLE_SA_JSON", "keys/google_sa.json")
    if not Path(sa_path).is_absolute():
        sa_path = Config.PROJECT_DIR / sa_path
    else:
        sa_path = Path(sa_path)
    
    if not sa_path.exists():
        return {}
    
    try:
        creds = Credentials.from_service_account_file(
            str(sa_path),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly"
            ]
        )
        gc = gspread.authorize(creds)
        ws = gc.open(Config.COMPANY_SPREADSHEET).worksheet(Config.COMPANY_TAB)
        vals = ws.get_all_values()
        
        if not vals:
            return {}
        
        hdrs = [h.strip() for h in vals[0]]
        ti = hdrs.index("ticker")
        ni = hdrs.index("company_name")
        
        return {str(row[ti]).strip(): str(row[ni]).strip() 
                for row in vals[1:] if len(row) > max(ti, ni)}
    except Exception as e:
        logger.warning(f"企業リスト読み込みエラー: {e}")
        return {}


# ============================================================
# JSON保存・読み込み
# ============================================================
def save_xbrl_json(company_code: str, company_name: str, year: str,
                   xbrl_data: Dict, source_file: str, output_base: Path,
                   validator: 'FinancialStatementValidator' = None,
                   learner: 'TagLearningManager' = None,
                   force_interim: bool = False):
    """XBRLデータをJSONで保存（検証・学習機能付き）

    force_interim: 半期フォルダ由来のZIP。コンテキスト推定に関わらず {year}_Q2.json へ保存する
    (監査人異動注記等で CurrentYear* コンテキストを含む半期が年度ファイルを汚染した事故の根治)。
    """

    # ディレクトリ作成
    company_dir = output_base / f"{company_code}_{company_name}"
    company_dir.mkdir(parents=True, exist_ok=True)

    # raw_tagsを分離
    raw_tags = xbrl_data.pop('_raw_tags', {})
    raw_tags_count = xbrl_data.pop('_raw_tags_count', 0)

    # 検証実行
    validation_report = None
    new_tags = []

    if HAS_VALIDATOR and validator is not None:
        try:
            validation_report, new_tags = validate_and_learn(
                xbrl_data, raw_tags, company_code, company_name, year,
                validator, learner
            )
        except Exception as e:
            logger.warning(f"  ⚠️ 検証エラー: {e}")

    # S0 T4 ガード: Interim* コンテキストのみのドキュメント (半期報告書) を年度ファイルとして
    # 保存しない。daily_update の docType 誤ラベルで半期ZIPが年度抽出に流れ、売上が前年比
    # 約-50%になる事故 (2026-05〜06, 591社) の再発防止。半期は {year}_Q2.json へ振替保存する。
    # 注: コンテキスト推定は「半期なのに CurrentYear* を含む」ケース (監査人異動注記・IFRS
    # TextBlock等、実測5社) をすり抜けるため、フォルダ由来の force_interim が最優先。
    contexts = {t.get('context', '') for t in raw_tags.values() if isinstance(t, dict)}
    has_annual_ctx = any(c.startswith('CurrentYear') for c in contexts)
    has_interim_ctx = any(c.startswith(('Interim', 'CurrentInterim')) for c in contexts)
    is_interim_doc = has_interim_ctx and not has_annual_ctx

    # 年度別JSON（標準フィールドのみ）
    if force_interim or is_interim_doc:
        file_stem = f"{year}_Q2"
        if force_interim and not is_interim_doc:
            logger.warning(f"  ⚠️ 半期フォルダ由来 (コンテキストは年度風) → フォルダ情報を優先し {file_stem}.json に保存 ({source_file})")
        else:
            logger.warning(f"  ⚠️ Interim-onlyコンテキスト → 年度でなく半期として保存: {file_stem}.json ({source_file})")
    else:
        file_stem = str(year)
    year_file = company_dir / f"{file_stem}.json"

    # スパース上書きガード: 訂正報告書等の部分XBRLが、既存の充実したEDINETデータを
    # 丸ごと置換して劣化させる経路を遮断する (「古い/薄いデータが良いデータを上書きしない」)
    _CORE = ('revenue', 'operating_income', 'ordinary_income', 'net_income', 'total_assets', 'total_equity')
    if year_file.exists():
        try:
            _old = json.loads(year_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _old = None
        if _old and _old.get("source") != "tanshin":
            _old_core = sum(1 for k in _CORE if (_old.get("data") or {}).get(k) is not None)
            _new_core = sum(1 for k in _CORE if xbrl_data.get(k) is not None)
            if _new_core < _old_core and _old_core >= 2:
                logger.warning(
                    f"  🛑 上書き拒否: 新抽出のコア項目 {_new_core} < 既存 {_old_core} "
                    f"({source_file} はスパースな訂正/部分文書の疑い) → 既存 {file_stem}.json を保持")
                return year_file, None, []

    save_data = {
        "company_code": company_code,
        "company_name": company_name,
        "fiscal_year": year,
        "extracted_at": datetime.now().isoformat(),
        "source_file": source_file,
        "raw_tags_count": raw_tags_count,
        "data": xbrl_data
    }

    # 検証結果を追加
    if validation_report:
        save_data["validation"] = validation_report
        score = validation_report.get('overall_score', 0)
        warnings_count = len(validation_report.get('warnings', []))
        errors_count = len(validation_report.get('errors', []))
        logger.info(f"  ✅ 検証スコア: {score}% (警告:{warnings_count}, エラー:{errors_count})")

    with open(year_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    logger.info(f"  💾 保存: {year_file}")

    # raw_tags（全タグ）を別ファイルに保存
    if raw_tags:
        raw_file = company_dir / f"{file_stem}_raw_tags.json"
        raw_save_data = {
            "company_code": company_code,
            "company_name": company_name,
            "fiscal_year": year,
            "extracted_at": datetime.now().isoformat(),
            "source_file": source_file,
            "tags_count": len(raw_tags),
            "tags": raw_tags
        }
        with open(raw_file, 'w', encoding='utf-8') as f:
            json.dump(raw_save_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  📋 全タグ保存: {raw_file} ({len(raw_tags)}タグ)")

    # 新タグ発見の報告
    if new_tags:
        logger.info(f"  🆕 新タグ発見: {len(new_tags)}個")

    return year_file, validation_report, new_tags


def update_company_summary(company_code: str, company_name: str, output_base: Path):
    """企業のサマリーJSONを更新"""
    
    company_dir = output_base / f"{company_code}_{company_name}"
    if not company_dir.exists():
        return
    
    # 全年度のJSONを読み込み (年度ファイルのみ。_Q*/_raw_tags/_statements は対象外)
    years_data = {}
    for json_file in company_dir.glob("20*.json"):  # 2020.json, 2021.json, etc.
        if any(s in json_file.name for s in ("_raw_tags", "_statements", "_Q")):
            continue
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # fiscal_year は経路により int (tanshin由来) / str (有報由来) が混在するため
                # str に正規化 (混在すると sorted()/max() が TypeError でバッチ全体を殺す)
                year = str(data.get("fiscal_year") or json_file.stem)
                years_data[year] = data.get("data", {})
        except:
            pass
    
    if not years_data:
        return
    
    # サマリー作成
    summary = {
        "company_code": company_code,
        "company_name": company_name,
        "updated_at": datetime.now().isoformat(),
        "available_years": sorted(years_data.keys(), reverse=True),
        "latest_year": max(years_data.keys()),
        "years_count": len(years_data),
        "trend": {}
    }
    
    # 主要指標のトレンドを作成（拡張版）
    trend_keys = [
        # P/L
        'revenue', 'gross_profit', 'operating_income', 'ordinary_income', 'net_income',
        # 利益率
        'gross_margin_calc', 'operating_margin_calc', 'net_margin_calc', 'sga_ratio_calc',
        # 収益性
        'roe_calc', 'roa_calc',
        # 安全性
        'equity_ratio_calc', 'current_ratio_calc', 'de_ratio_calc',
        # 効率性
        'asset_turnover_calc', 'inventory_days_calc', 'ccc_calc',
        # CF
        'operating_cf', 'fcf_calc', 'ocf_margin_calc',
        # 1株指標
        'eps', 'bps', 'dividend_per_share',
    ]
    
    for key in trend_keys:
        trend = {}
        for year, data in sorted(years_data.items()):
            if key in data:
                trend[year] = data[key]
        if trend:
            summary["trend"][key] = trend
    
    # サマリー保存
    summary_file = company_dir / "summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logger.info(f"  📊 サマリー更新: {summary_file}")


def load_xbrl_store(company_code: str, output_base: Path) -> Dict[int, Dict]:
    """
    保存済みXBRLデータを読み込み
    Returns: {year: xbrl_data, ...}
    """
    # 企業フォルダを検索
    company_dirs = list(output_base.glob(f"{company_code}_*"))
    if not company_dirs:
        return {}
    
    company_dir = company_dirs[0]
    years_data = {}
    
    for json_file in company_dir.glob("20*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                year = int(data.get("fiscal_year", json_file.stem))
                years_data[year] = data.get("data", {})
        except:
            pass
    
    return years_data


# ============================================================
# バッチ処理
# ============================================================
def process_company(company_code: str, years: List[str],
                    company_names: Dict[str, str], output_base: Path,
                    validator: 'FinancialStatementValidator' = None,
                    learner: 'TagLearningManager' = None,
                    skip_existing: bool = False) -> Dict:
    """単一企業の複数年度を処理（検証・学習機能付き）"""

    results = {
        "company_code": company_code,
        "years_processed": [],
        "years_failed": [],
        "errors": [],
        "validations": {},
        "new_tags_found": []
    }

    # ZIPファイルを検索
    zips = find_xbrl_zips(company_code, years, Config.XBRL_BASE)

    if not zips:
        results["errors"].append(f"ZIPファイルが見つかりません")
        return results

    # 企業名を取得
    company_name = company_names.get(company_code)
    if not company_name and zips:
        company_name = get_company_name_from_zip(zips[0][1])

    logger.info(f"\n📦 {company_code} {company_name}: {len(zips)}年度")

    for year, zip_path in zips:
        try:
            # 半期ZIPは save_xbrl_json の Interim-only ガードで {year}_Q2.json に保存されるため
            # skip判定の対象ファイルもそちらに合わせる (`_四半期_` はこの正規表現に一致しない)
            is_interim_zip = bool(re.search(r'_(訂正)?半期(報告書)?_', zip_path.name))

            # --skip-existing: docID比較で判定 (POSTMORTEM R4: ファイル存在ベース禁止)
            # 社名表記ゆれの二重フォルダを全部確認する (先頭1つしか見ない旧バグの修正)
            if skip_existing:
                target_stem = f"{year}_Q2" if is_interim_zip else str(year)
                skip_this, reason = False, ""
                for existing_dir in sorted(output_base.glob(f"{company_code}_*")):
                    existing_json = existing_dir / f"{target_stem}.json"
                    if existing_json.exists():
                        do_skip, reason = should_skip_existing(existing_json, zip_path.name)
                        if do_skip:
                            skip_this = True
                            break
                    # 旧dailyの誤ラベル半期ZIP (名前は訂正有報等) はガードで {year}_Q2 に保存済み。
                    # 同一docIDが _Q2 側に居れば再処理不要 (docID一致のみ。年度ZIPをQ2がブロックはしない)
                    if not is_interim_zip:
                        q2_json = existing_dir / f"{year}_Q2.json"
                        if q2_json.exists():
                            try:
                                q2_src = str(json.loads(q2_json.read_text(encoding="utf-8")).get("source_file") or "")
                            except (json.JSONDecodeError, OSError):
                                q2_src = ""
                            zid = extract_docid(zip_path.name)
                            if zid and zid == extract_docid(q2_src):
                                skip_this = True
                                reason = f"同一docID {zid} は半期として抽出済み ({year}_Q2) → スキップ"
                                break
                if skip_this:
                    logger.info(f"  ⏭️ {target_stem}: {reason}")
                    # skip は years_processed と分離: 全skip企業の summary.json を毎回
                    # 書き換えて mtime ベースの r2_sync 差分判定を汚染しないため
                    results.setdefault("years_skipped", []).append(year)
                    continue
                if reason:
                    logger.info(f"  🔄 {target_stem}: {reason}")

            logger.info(f"  📄 {year}: {zip_path.name}")

            # XBRL抽出
            xbrl_data = extract_xbrl_from_zip(zip_path)

            if not xbrl_data:
                results["years_failed"].append(year)
                results["errors"].append(f"{year}: XBRL抽出失敗")
                continue

            # 保存（検証・学習付き）。半期フォルダ由来は保存先を {year}_Q2 に固定
            year_file, validation_report, new_tags = save_xbrl_json(
                company_code, company_name, year,
                xbrl_data, zip_path.name, output_base,
                validator, learner,
                force_interim=is_interim_zip,
            )

            results["years_processed"].append(year)

            # 検証結果を保存
            if validation_report:
                results["validations"][year] = {
                    "score": validation_report.get('overall_score', 0),
                    "warnings": len(validation_report.get('warnings', [])),
                    "errors": len(validation_report.get('errors', []))
                }

            # 新タグを記録
            if new_tags:
                results["new_tags_found"].extend(new_tags)

            # 主要数値をログ出力
            rev = xbrl_data.get('revenue')
            op = xbrl_data.get('operating_income')
            ni = xbrl_data.get('net_income')

            # 取得項目数をカウント
            raw_count = sum(1 for k, v in xbrl_data.items() if not k.endswith('_calc') and v is not None)
            calc_count = sum(1 for k, v in xbrl_data.items() if k.endswith('_calc') and v is not None)

            if rev:
                logger.info(f"    売上: {rev/1e8:.1f}億円, 営業利益: {op/1e8:.1f}億円, 純利益: {ni/1e8:.1f}億円" if op and ni else f"    売上: {rev/1e8:.1f}億円")
            logger.info(f"    抽出: {raw_count}項目 + 派生{calc_count}項目 = 計{raw_count + calc_count}項目")

        except Exception as e:
            results["years_failed"].append(year)
            results["errors"].append(f"{year}: {str(e)}")
            # ログ無しの握り潰しはサイレント失敗の温床 (2656のゼロ除算が3run気付かれなかった)
            logger.error(f"  ❌ {year}: {e} ({zip_path.name})")

    # サマリー更新 (1社のサマリ不整合でバッチ全体を止めない)
    if results["years_processed"]:
        try:
            update_company_summary(company_code, company_name, output_base)
        except Exception as e:
            logger.warning(f"  ⚠️ summary更新失敗 (継続): {company_code} {e}")

    return results


def batch_process(companies: List[str], years: List[str], output_base: Path,
                  enable_validation: bool = True, skip: int = 0,
                  skip_existing: bool = False) -> List[Dict]:
    """複数企業のバッチ処理（検証・学習機能付き）

    Args:
        skip: 最初のN社をスキップ（途中再開用）
        skip_existing: Trueの場合、既存のJSONがある年度をスキップ
    """

    # 企業名リストを取得
    company_names = load_company_list_from_sheets()

    # 検証・学習モジュールの初期化
    validator = None
    learner = None

    if enable_validation and HAS_VALIDATOR:
        try:
            validator = FinancialStatementValidator()
            learner = TagLearningManager(output_base / "learned_tags.json")

            # 既知タグを設定（FALLBACK_TAGSから抽出）
            known_tags = set()
            for tags in FALLBACK_TAGS.values():
                for tag_full, _ in tags:
                    tag_local = tag_full.split(':')[-1]
                    known_tags.add(tag_local)
            learner.set_known_tags(known_tags)

            logger.info("✅ 検証・学習モジュール有効化")
        except Exception as e:
            logger.warning(f"⚠️ 検証・学習モジュール初期化エラー: {e}")

    results = []
    total = len(companies)
    total_new_tags = []

    logger.info(f"\n{'='*60}")
    logger.info(f"🚀 XBRL バッチ抽出開始: {total}社 × {len(years)}年度")
    if skip > 0:
        logger.info(f"⏭️ 最初の{skip}社をスキップ（途中再開）")
    logger.info(f"{'='*60}")

    for i, company_code in enumerate(companies):
        if i < skip:
            continue  # スキップ
        logger.info(f"\n[{i+1}/{total}] 処理中...")
        result = process_company(company_code, years, company_names, output_base,
                               validator, learner, skip_existing=skip_existing)
        results.append(result)

        # 新タグを集計
        if result.get("new_tags_found"):
            total_new_tags.extend(result["new_tags_found"])

    # 学習データの最終保存
    if learner:
        learner.save()

        # 学習レポート生成
        report_file = output_base / "tag_learning_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(learner.generate_report(), f, ensure_ascii=False, indent=2)
        logger.info(f"📋 学習レポート保存: {report_file}")

    # サマリー出力
    # 全skip (=既に最新) は成功扱い。真の失敗 = 抽出も skip も無かった企業
    success = sum(1 for r in results if r["years_processed"] or r.get("years_skipped"))
    failed = sum(1 for r in results if not r["years_processed"] and not r.get("years_skipped"))

    # 検証スコア集計
    if enable_validation:
        all_scores = []
        for r in results:
            for year, val in r.get("validations", {}).items():
                all_scores.append(val.get("score", 0))
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 処理完了")
    logger.info(f"  成功: {success}社")
    logger.info(f"  失敗: {failed}社")
    if enable_validation and all_scores:
        logger.info(f"  平均検証スコア: {avg_score:.1f}%")
    if total_new_tags:
        unique_new_tags = set(total_new_tags)
        logger.info(f"  新タグ発見: {len(unique_new_tags)}種類")
    logger.info(f"{'='*60}")

    return results


# ============================================================
# インタラクティブモード
# ============================================================
def interactive_mode():
    """インタラクティブなメニューモード"""

    print("\n" + "=" * 60)
    print("XBRL Batch Extractor - インタラクティブモード")
    print("=" * 60)

    # モード選択
    print("\n【モード選択】")
    print("1. 単一企業を処理")
    print("2. 複数企業を処理")
    print("3. テストモード（指定企業・出力先test）")
    print("4. 全企業を処理")
    print("5. ヘルプを表示")
    print("0. 終了")

    mode = input("\n選択してください (0-5): ").strip()

    if mode == "0":
        print("\n終了します。")
        return

    elif mode == "5":
        print("\n" + "=" * 60)
        print("【ヘルプ】")
        print("=" * 60)
        print("\nコマンドライン使用例:")
        print("  python xbrl_batch_extractor.py --company 1301 --years 2020,2021,2022,2023")
        print("  python xbrl_batch_extractor.py --companies 1301,2802 --years 2022,2023")
        print("  python xbrl_batch_extractor.py --scan-folder \"E:\\PDF\\PDF+XBRL\"")
        print("\nオプション:")
        print("  --company, -c   : 単一企業コード")
        print("  --companies     : 複数企業コード（カンマ区切り）")
        print("  --years, -y     : 年度（デフォルト: 2020,2021,2022,2023,2024）")
        print("  --all           : 全企業を処理")
        print("  --scan-folder   : フォルダ内のZIPを全てスキャン")
        print("  --output, -o    : 出力先（デフォルト: ./xbrl_store）")
        return

    # 企業コード入力
    companies = []
    if mode == "1":
        print("\n【企業コード入力】")
        code = input("企業コードを入力してください (例: 1301): ").strip()
        if code:
            companies = [code]

    elif mode == "2":
        print("\n【企業コード入力】")
        codes = input("企業コードを入力してください（カンマ区切り、例: 1301,7203,6758）: ").strip()
        if codes:
            companies = [c.strip() for c in codes.split(',')]

    elif mode == "3":
        print("\n【テストモード】")
        codes = input("テスト企業コードを入力してください（カンマ区切り、例: 7203,6758）: ").strip()
        if codes:
            companies = [c.strip() for c in codes.split(',')]

    elif mode == "4":
        print("\n【全企業処理】")
        confirm = input("全企業を処理します。よろしいですか？ (y/n): ").strip().lower()
        if confirm != 'y':
            print("\nキャンセルしました。")
            return
        # Google Sheetsから全企業取得
        company_names = load_company_list_from_sheets()
        companies = list(company_names.keys())
        if not companies:
            print("\n[ERROR] 企業リストを取得できませんでした。")
            return
        print(f"\n[OK] {len(companies)}社を取得しました。")

    else:
        print("\n[ERROR] 無効な選択です。")
        return

    if not companies and mode != "4":
        print("\n[ERROR] 企業コードが入力されていません。")
        return

    # 年度選択
    print("\n【年度選択】")
    print("1. 直近1年 (2024)")
    print("2. 直近3年 (2022,2023,2024)")
    print("3. 直近5年 (2020,2021,2022,2023,2024)")
    print("4. カスタム（手動入力）")

    year_mode = input("\n選択してください (1-4): ").strip()

    if year_mode == "1":
        years = ["2024"]
    elif year_mode == "2":
        years = ["2022", "2023", "2024"]
    elif year_mode == "3":
        years = ["2020", "2021", "2022", "2023", "2024"]
    elif year_mode == "4":
        year_input = input("年度を入力してください（カンマ区切り、例: 2020,2021,2022）: ").strip()
        if year_input:
            years = [y.strip() for y in year_input.split(',')]
        else:
            print("\n[ERROR] 年度が入力されていません。")
            return
    else:
        print("\n[ERROR] 無効な選択です。")
        return

    # 出力先選択
    if mode == "3":
        output_base = Path("./xbrl_store_test")
        print(f"\n【出力先】テストモード: {output_base}")
    else:
        print("\n【出力先選択】")
        print("1. デフォルト (./xbrl_store)")
        print("2. カスタム（手動入力）")

        output_mode = input("\n選択してください (1-2): ").strip()

        if output_mode == "1":
            output_base = Path("./xbrl_store")
        elif output_mode == "2":
            output_path = input("出力先パスを入力してください: ").strip()
            if output_path:
                output_base = Path(output_path)
            else:
                print("\n[ERROR] 出力先が入力されていません。")
                return
        else:
            print("\n[ERROR] 無効な選択です。")
            return

    output_base.mkdir(parents=True, exist_ok=True)

    # 確認
    print("\n" + "=" * 60)
    print("【設定確認】")
    print("=" * 60)
    print(f"企業数: {len(companies)}社")
    if len(companies) <= 10:
        print(f"企業コード: {', '.join(companies)}")
    print(f"年度: {', '.join(years)}")
    print(f"出力先: {output_base}")
    print("=" * 60)

    confirm = input("\n処理を開始しますか？ (y/n): ").strip().lower()

    if confirm == 'y':
        # バッチ処理開始
        batch_process(companies, years, output_base)
    else:
        print("\nキャンセルしました。")


# ============================================================
# HTML配当テキストブロックから1株当たり配当額を抽出
# ============================================================
def _parse_dividend_html(html: str) -> float:
    """NotesRegardingDividendTextBlock HTMLから年間1株当たり配当額を抽出"""
    try:
        # テーブルからセルを抽出
        cells = re.findall(r'<td[^>]*>(.*?)</td>', html, re.DOTALL | re.IGNORECASE)
        clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\xa0', ' ') for c in cells]

        # ヘッダーから「1株当たり配当額」列のインデックスを特定
        # 表記ゆれ: 1株当たり / 1株あたり / １株当たり（全角/半角、漢字/ひらがな）
        dps_col = None
        cols_per_row = 0
        for i, c in enumerate(clean):
            c_norm = c.replace('\n', '').replace(' ', '')
            if any(k in c_norm for k in ['1株当', '１株当', '1株あたり', '１株あたり']):
                dps_col = i
                break

        if dps_col is None:
            return None

        # テーブルのカラム数を推定（ヘッダー行 = 決議の前までのセル数）
        for i, c in enumerate(clean):
            if '決議' in c or '決 議' in c:
                # 次の「決議」or数値が来るまでがヘッダー行
                for j in range(i + 1, len(clean)):
                    if any(k in clean[j] for k in ['定時', '取締役', '臨時', '20']) and '株式' not in clean[j]:
                        cols_per_row = j - i
                        break
                break

        if cols_per_row <= 0:
            # フォールバック: dps_colの位置からカラム数推定
            cols_per_row = dps_col + 3  # 基準日+効力発生日が後ろに2列

        # DPS列の値を全行から収集して合算（中間+期末）
        total_dps = 0.0
        count = 0
        for idx in range(dps_col, len(clean), cols_per_row):
            val_str = clean[idx].replace(',', '').replace('，', '').replace('円', '').strip()
            try:
                val = float(val_str)
                total_dps += val
                count += 1
            except (ValueError, IndexError):
                continue

        return total_dps if count > 0 else None
    except Exception:
        return None


def _parse_cf_html_dividend(cf_html: str) -> float:
    """StatementOfCashFlowsTextBlock HTMLから配当金の支払額(円単位)を抽出"""
    if not cf_html:
        return None
    try:
        # 単位判定: 千円 or 百万円
        unit_mult = 1e6
        unit_m = re.search(r'単位[：:]\s*(百万円|千円)', cf_html)
        if unit_m:
            unit_mult = 1e6 if '百万' in unit_m.group(1) else 1e3
        elif '千円' in cf_html and '百万' not in cf_html:
            unit_mult = 1e3

        cells = re.findall(r'<td[^>]*>(.*?)</td>', cf_html, re.DOTALL)
        clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\xa0', ' ') for c in cells]

        for i, c in enumerate(clean):
            if '配当金の支払' in c and '受取' not in c and '少数' not in c and '非支配' not in c:
                # 前後セルから当期の数値を取得
                candidates = []
                for j in [i - 1, i + 1, i - 2, i + 2, i - 3, i + 3]:
                    if 0 <= j < len(clean):
                        val_str = clean[j].replace(',', '').replace('△', '-').replace('－', '0').replace('―', '0').strip()
                        try:
                            val = float(val_str)
                            candidates.append((abs(j - i), val))
                        except ValueError:
                            continue
                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    return candidates[0][1] * unit_mult
        return None
    except Exception:
        return None


# ============================================================
# 配当パッチ: 既存raw_tagsから配当データのみ再マッチング
# ============================================================
def patch_dividends_from_raw_tags(store_path: Path):
    """既存raw_tagsから配当関連タグを再マッチングしてxbrl_storeを更新"""
    DIVIDEND_TAGS = FALLBACK_TAGS.get('dividends_paid', [])
    DPS_TAGS = FALLBACK_TAGS.get('dividend_per_share', [])

    raw_files = sorted(store_path.glob('*/*_raw_tags.json'))
    logger.info(f"配当パッチ: {len(raw_files)} ファイルを処理")

    updated = 0
    for rf in raw_files:
        with open(rf, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        tags = raw_data.get('tags', {})

        year = rf.stem.split('_')[0]
        store_file = rf.parent / f'{year}.json'
        if not store_file.exists():
            continue

        with open(store_file, 'r', encoding='utf-8') as f:
            store = json.load(f)
        data = store.get('data', {})

        changed = False

        # dividends_paid 再マッチング（優先順: XBRLタグ → DividendsSSIFRS → CF HTML）
        if data.get('dividends_paid') is None:
            for tag_full, priority in DIVIDEND_TAGS:
                tag_local = tag_full.split(':')[-1]
                if tag_local in tags:
                    td = tags[tag_local]
                    if isinstance(td.get('value'), (int, float)):
                        data['dividends_paid'] = td['value']
                        changed = True
                        break

        # DividendsSSIFRS（株主資本等変動計算書、IFRS企業の一部で使用）
        if data.get('dividends_paid') is None:
            ss_tag = tags.get('DividendsSSIFRS', {})
            if isinstance(ss_tag.get('value'), (int, float)):
                data['dividends_paid'] = ss_tag['value']
                changed = True

        # CF HTMLテキストブロックから配当金の支払額を抽出（個別/連結/IFRS全バリアント）
        if data.get('dividends_paid') is None:
            cf_textblock_keys = [
                'StatementOfCashFlowsTextBlock',
                'ConsolidatedStatementOfCashFlowsTextBlock',
                'ConsolidatedStatementOfCashFlowsIFRSTextBlock',
            ]
            cf_extracted = False
            cf_has_finance_section = False
            for cf_key in cf_textblock_keys:
                cf_html = tags.get(cf_key, {})
                cf_val = cf_html.get('value', '') if isinstance(cf_html, dict) else ''
                if not isinstance(cf_val, str) or not cf_val:
                    continue
                div_yen = _parse_cf_html_dividend(cf_val)
                if div_yen is not None and div_yen != 0:
                    data['dividends_paid'] = div_yen
                    data['dividends_paid_source'] = f'cf_html_{cf_key}'
                    changed = True
                    cf_extracted = True
                    break
                # 財務活動セクションはあるが配当金の支払なし → 無配確認
                clean_cf = re.sub(r'<[^>]+>', ' ', cf_val)
                if '財務活動' in clean_cf:
                    cf_has_finance_section = True

            # CF確認無配: 財務活動セクション存在 + 配当金の支払なし → dividends_paid=0
            if not cf_extracted and cf_has_finance_section:
                data['dividends_paid'] = 0
                data['dividends_paid_source'] = 'cf_confirmed_zero'
                data['dividend_per_share'] = 0.0
                changed = True

        # IFRS配当ノートで無配確認（NotesRegardingDividendTextBlockに加えてIFRS版もチェック）
        if data.get('dividends_paid') is None:
            for notes_key in ['NotesRegardingDividendTextBlock',
                              'NotesDividendsConsolidatedFinancialStatementsIFRSTextBlock']:
                nt = tags.get(notes_key, {})
                nt_val = nt.get('value', '') if isinstance(nt, dict) else str(nt)
                if any(kw in str(nt_val) for kw in ['該当事項はありません', '該当事項なし',
                                                      '配当を行っておりません', '無配', '配当していない']):
                    data['dividends_paid'] = 0
                    data['dividends_paid_source'] = f'notes_confirmed_zero_{notes_key}'
                    data['dividend_per_share'] = 0.0
                    changed = True
                    break

        # dividend_per_share: タグ直接 or 計算フォールバック
        if data.get('dividend_per_share') is None:
            for tag_full, priority in DPS_TAGS:
                tag_local = tag_full.split(':')[-1]
                if tag_local in tags:
                    td = tags[tag_local]
                    if isinstance(td.get('value'), (int, float)):
                        data['dividend_per_share'] = td['value']
                        changed = True
                        break

        if data.get('dividend_per_share') is None and data.get('dividends_paid') and data.get('shares_issued'):
            data['dividend_per_share'] = abs(data['dividends_paid']) / data['shares_issued']
            changed = True

        # shares_issuedが無い古い年度: EPS+純利益から推定 → DPS計算
        if data.get('dividend_per_share') is None and data.get('dividends_paid') and data.get('eps') and data.get('net_income'):
            eps = data['eps']
            ni = data['net_income']
            if eps > 0 and ni > 0:
                estimated_shares = ni / eps
                data['dividend_per_share'] = abs(data['dividends_paid']) / estimated_shares
                changed = True

        # HTMLテキストブロックから1株当たり配当額を抽出（数値タグがない企業向け）
        if data.get('dividend_per_share') is None:
            notes = tags.get('NotesRegardingDividendTextBlock', {})
            html = notes.get('value', '') if isinstance(notes, dict) else ''
            if isinstance(html, str) and '<table' in html.lower() and '該当事項' not in html:
                dps_from_html = _parse_dividend_html(html)
                if dps_from_html is not None and dps_from_html > 0:
                    data['dividend_per_share'] = dps_from_html
                    data['dividend_per_share_source'] = 'html_text_block'
                    changed = True

        # payout_ratio_calc
        if data.get('payout_ratio_calc') is None and data.get('dividend_per_share') and data.get('eps'):
            data['payout_ratio_calc'] = round((data['dividend_per_share'] / data['eps']) * 100, 2)
            changed = True

        if changed:
            store['data'] = data
            with open(store_file, 'w', encoding='utf-8') as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
            updated += 1

    logger.info(f"配当パッチ完了: {updated}/{len(raw_files)} ファイル更新")


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='XBRL Batch Extractor')
    parser.add_argument('--company', '-c', help='企業コード（単一）')
    parser.add_argument('--companies', help='企業コード（カンマ区切り）')
    parser.add_argument('--years', '-y', default='2020,2021,2022,2023,2024',
                        help='年度（カンマ区切り）')
    parser.add_argument('--all', action='store_true', help='全企業を処理')
    parser.add_argument('--scan-folder', help='指定フォルダをスキャン')
    parser.add_argument('--output', '-o', default='./xbrl_store', help='出力先')
    parser.add_argument('--skip', type=int, default=0, help='最初のN社をスキップ（途中再開用）')
    parser.add_argument('--skip-existing', action='store_true',
                        help='既存JSONがある年度をスキップ（差分処理用）')
    parser.add_argument('--patch-dividends', action='store_true',
                        help='既存raw_tagsから配当データのみ再マッチング・更新')
    parser.add_argument('--quarterly', action='store_true',
                        help='四半期報告書モード（四半期ZIPのみ処理）')
    parser.add_argument('--fy-end-month', type=int, default=3,
                        help='決算月（デフォルト: 3=3月決算）')

    args = parser.parse_args()

    # ★ 配当パッチモード: raw_tagsから配当データだけ再マッチング
    if args.patch_dividends:
        patch_dividends_from_raw_tags(Path(args.output))
        return

    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)

    years = [y.strip() for y in args.years.split(',')]

    # ★ 四半期モード
    if args.quarterly:
        fy_end = args.fy_end_month
        if args.scan_folder or args.all:
            # 全企業スキャン
            company_zips = scan_all_quarterly_zips(Config.XBRL_BASE, years, fy_end)
            companies = list(company_zips.keys())
            logger.info(f"四半期スキャン: {len(companies)}社発見")
            if companies:
                batch_process_quarterly(companies, years, output_base,
                                        skip_existing=args.skip_existing,
                                        skip=args.skip, fy_end_month=fy_end)
        elif args.companies:
            companies = [c.strip() for c in args.companies.split(',')]
            batch_process_quarterly(companies, years, output_base,
                                    skip_existing=args.skip_existing,
                                    skip=args.skip, fy_end_month=fy_end)
        elif args.company:
            batch_process_quarterly([args.company], years, output_base,
                                    skip_existing=args.skip_existing,
                                    fy_end_month=fy_end)
        else:
            logger.error("--quarterly には --company, --companies, --all のいずれかが必要です")
        return

    if args.scan_folder:
        # フォルダスキャンモード
        scan_path = Path(args.scan_folder)
        logger.info(f"📂 フォルダスキャン: {scan_path}")

        company_zips = scan_all_zips(scan_path, years)
        companies = list(company_zips.keys())
        logger.info(f"  発見: {len(companies)}社")

        if companies:
            batch_process(companies, years, output_base, skip=args.skip,
                          skip_existing=args.skip_existing)

    elif args.all:
        # 全企業モード（Google Sheetsから取得）
        company_names = load_company_list_from_sheets()
        companies = list(company_names.keys())

        if companies:
            batch_process(companies, years, output_base, skip=args.skip,
                          skip_existing=args.skip_existing)
        else:
            logger.error("企業リストを取得できませんでした")

    elif args.companies:
        # 複数企業モード
        companies = [c.strip() for c in args.companies.split(',')]
        batch_process(companies, years, output_base, skip=args.skip,
                      skip_existing=args.skip_existing)

    elif args.company:
        # 単一企業モード
        batch_process([args.company], years, output_base, skip=args.skip,
                      skip_existing=args.skip_existing)
    
    else:
        # インタラクティブモード
        interactive_mode()


if __name__ == "__main__":
    main()