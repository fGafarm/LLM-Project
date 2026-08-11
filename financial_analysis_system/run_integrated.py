#!/usr/bin/env python3
"""
PORTA方式 統合分析システム v4 - 詳細レポート版

改善点:
- 全セクション使用（制限なし）
- 全XBRL項目使用
- num_predict=4000で長文出力
- 日本語詳細プロンプト
"""

import sys
import os
import re
import argparse
import json
import zipfile
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


# ============================================================
# 設定
# ============================================================
class Config:
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "gemma2:27b"
    CHUNK_SIZE = 2000
    CHUNK_OVERLAP = 150
    MAX_WORKERS = 4
    NUM_CTX = 8192
    NUM_PREDICT = 4000  # 長文出力用に増加
    TEMPERATURE = 0.3


def count_tokens(text: str) -> int:
    if HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except:
            pass
    return max(1, len(text) // 3)


# ============================================================
# XBRLタグ定義（完全版 - 省略せず全部）
# ============================================================
XBRL_TAG_DEFINITIONS = {
    # ==================== P/L項目 ====================
    'revenue': [
        ('NetSales', 1), ('NetSalesIFRS', 1), ('Revenue', 2), ('RevenueIFRS', 2),
        ('Sales', 3), ('SalesIFRS', 3), ('OperatingRevenue', 4), ('TotalRevenue', 5),
        ('OrdinaryIncomeBNK', 1), ('OrdinaryIncomeSEC', 1), ('OrdinaryIncomeINS', 1),
    ],
    'cost_of_sales': [
        ('CostOfSales', 1), ('CostOfSalesIFRS', 1), ('CostOfGoodsSold', 2), ('CostOfRevenue', 3),
    ],
    'gross_profit': [
        ('GrossProfit', 1), ('GrossProfitIFRS', 1), ('GrossMargin', 2),
    ],
    'equity_in_earnings': [
        ('EquityInEarningsOfAffiliates', 1), ('ShareOfProfitLossOfInvestmentsAccountedForUsingEquityMethodIFRS', 1),
        ('ShareOfProfitOfInvestmentsAccountedForUsingEquityMethod', 2), ('EquityMethodIncome', 3),
    ],
    'selling_expenses': [
        ('SellingExpensesIFRS', 1), ('SellingExpenses', 2), ('SellingExpense', 3),
    ],
    'research_development': [
        ('ResearchAndDevelopmentExpenditureRecognizedAsExpenseDuringPeriodIFRS', 1),
        ('ResearchAndDevelopmentExpenses', 2), ('ResearchAndDevelopmentExpense', 3), ('RAndDExpenses', 4),
    ],
    'general_admin': [
        ('GeneralAndAdministrativeExpensesIFRS', 1), ('GeneralAndAdministrativeExpenses', 2),
        ('AdministrativeExpenses', 3),
    ],
    'selling_general_admin': [
        ('SellingGeneralAndAdministrativeExpenses', 1), ('SGA', 2), ('OperatingExpenses', 3),
        ('GeneralAndAdministrativeExpenses', 4), ('SellingAndAdministrativeExpenses', 5), ('SGAExpenses', 6),
        ('SellingExpensesIFRS', 2), ('GeneralAndAdministrativeExpensesIFRS', 3),
    ],
    'other_operating_income': [
        ('OtherOperatingIncomeIFRS', 1), ('OtherOperatingIncome', 2), ('OtherOperatingRevenue', 3),
    ],
    'other_operating_expenses': [
        ('OtherOperatingExpensesIFRS', 1), ('OtherOperatingExpenses', 2), ('OtherOperatingCosts', 3),
    ],
    'operating_income': [
        ('OperatingIncome', 1), ('OperatingProfitLossIFRS', 1), ('OperatingProfit', 2),
        ('BusinessProfit', 3), ('BusinessProfitIFRS', 3), ('OperatingIncomeOrLoss', 4),
    ],
    'finance_income': [
        ('FinanceIncomeIFRS', 1), ('FinanceIncome', 2), ('FinancialIncome', 3),
    ],
    'finance_costs': [
        ('FinanceCostsIFRS', 1), ('FinanceCosts', 2), ('FinancialCosts', 3), ('FinanceExpenses', 4),
    ],
    'non_operating_income': [
        ('NonOperatingIncome', 1), ('NonOperatingRevenue', 2), ('OtherIncome', 3),
        ('FinanceIncomeIFRS', 1), ('OtherOperatingIncomeIFRS', 2),
    ],
    'interest_income': [
        ('InterestIncomeNOI', 0), ('InterestIncome', 1), ('InterestIncomeIFRS', 1),
        ('InterestAndDividendIncome', 2), ('FinanceIncomeIFRS', 2),
    ],
    'dividend_income': [
        ('DividendIncome', 1), ('DividendIncomeIFRS', 1), ('DividendsReceived', 2),
    ],
    'non_operating_expenses': [
        ('NonOperatingExpenses', 1), ('NonOperatingCosts', 2), ('OtherExpenses', 3),
        ('FinanceCostsIFRS', 1), ('OtherOperatingExpensesIFRS', 2),
    ],
    'interest_expense': [
        ('InterestExpensesNOE', 0), ('InterestExpense', 1), ('InterestExpensesIFRS', 1),
        ('InterestExpenses', 2), ('InterestCosts', 3), ('FinanceCostsIFRS', 2),
    ],
    'ordinary_income': [
        ('OrdinaryIncome', 1), ('OrdinaryProfit', 2), ('RecurringProfit', 3),
    ],
    'extraordinary_income': [
        ('ExtraordinaryIncome', 1), ('ExtraordinaryGain', 2), ('SpecialIncome', 3),
    ],
    'gain_on_sale_fixed_assets': [
        ('GainOnSaleOfFixedAssets', 1), ('GainOnDisposalOfFixedAssets', 2), ('GainOnSalesOfNoncurrentAssets', 3),
    ],
    'gain_on_sale_securities': [
        ('GainOnSaleOfSecurities', 1), ('GainOnSalesOfInvestmentSecurities', 2), ('GainOnSaleOfInvestments', 3),
    ],
    'extraordinary_loss': [
        ('ExtraordinaryLoss', 1), ('ExtraordinaryLosses', 2), ('SpecialLoss', 3),
    ],
    'loss_on_sale_fixed_assets': [
        ('LossOnSaleOfFixedAssets', 1), ('LossOnDisposalOfFixedAssets', 2), ('LossOnSalesOfNoncurrentAssets', 3),
    ],
    'impairment_loss': [
        ('ImpairmentLoss', 1), ('ImpairmentLosses', 2), ('ImpairmentOfAssets', 3),
    ],
    'restructuring_loss': [
        ('RestructuringLoss', 1), ('RestructuringCosts', 2), ('RestructuringExpenses', 3),
    ],
    'income_before_taxes': [
        ('IncomeBeforeIncomeTaxes', 1), ('ProfitLossBeforeTaxIFRS', 1), ('ProfitBeforeTax', 2), ('IncomeBeforeTax', 3),
    ],
    'income_taxes': [
        ('IncomeTaxes', 1), ('IncomeTaxExpenseIFRS', 1), ('TaxExpense', 2), ('IncomeTaxExpense', 3),
    ],
    'income_taxes_current': [
        ('IncomeTaxesCurrent', 1), ('CurrentIncomeTax', 2), ('CurrentTaxExpense', 3),
    ],
    'income_taxes_deferred': [
        ('IncomeTaxesDeferred', 1), ('DeferredIncomeTax', 2), ('DeferredTaxExpense', 3),
    ],
    'net_income': [
        ('ProfitLossAttributableToOwnersOfParent', 0), ('ProfitLossAttributableToOwnersOfParentIFRS', 0),
        ('NetIncome', 2), ('NetProfit', 3), ('ProfitAttributableToOwnersOfParent', 4),
        ('ProfitLoss', 5), ('ProfitLossIFRS', 5),
    ],
    'net_income_non_controlling': [
        ('ProfitLossAttributableToNonControllingInterests', 1), ('ProfitLossAttributableToNonControllingInterestsIFRS', 1),
        ('NetIncomeLossAttributableToNoncontrollingInterest', 2), ('MinorityInterests', 3),
    ],
    'comprehensive_income': [
        ('ComprehensiveIncome', 1), ('ComprehensiveIncomeIFRS', 1), ('TotalComprehensiveIncome', 2),
    ],
    'comprehensive_income_parent': [
        ('ComprehensiveIncomeAttributableToOwnersOfParent', 1), ('ComprehensiveIncomeAttributableToOwnersOfTheParent', 0),
        ('ComprehensiveIncomeAttributableToOwnersOfParentIFRS', 1),
    ],
    'comprehensive_income_nci': [
        ('ComprehensiveIncomeAttributableToNonControllingInterests', 1),
        ('ComprehensiveIncomeAttributableToNonControllingInterestsIFRS', 1),
    ],
    'other_comprehensive_income': [
        ('OtherComprehensiveIncome', 1), ('OtherComprehensiveIncomeIFRS', 1),
    ],
    'unrealized_gain_securities': [
        ('ValuationDifferenceOnAvailableForSaleSecurities', 1), ('UnrealizedGainLossOnSecurities', 2),
        ('NetChangeInFairValueOfEquityInstrumentsDesignatedAsMeasuredAtFairValueThroughOtherComprehensiveIncomeNetOfTaxItemsThatWillNotBeReclassifiedToProfitOrLossOCIIFRS', 1),
    ],
    'foreign_currency_translation': [
        ('ForeignCurrencyTranslationAdjustment', 1),
        ('ExchangeDifferencesOnTranslationOfForeignOperationsNetOfTaxItemsThatMayBeReclassifiedToProfitOrLossOCIIFRS', 1),
        ('TranslationAdjustments', 2),
    ],
    'remeasurement_pension': [
        ('RemeasurementsOfDefinedBenefitPlans', 1),
        ('RemeasurementsOfDefinedBenefitPlansNetOfTaxItemsThatWillNotBeReclassifiedToProfitOrLossOCIIFRS', 1),
        ('ActuarialGainsLosses', 2),
    ],
    'eps_basic': [
        ('BasicEarningsPerShare', 1), ('BasicEarningsLossPerShareIFRS', 1), ('EarningsPerShare', 2),
    ],
    'eps_diluted': [
        ('DilutedEarningsPerShare', 1), ('DilutedEarningsLossPerShareIFRS', 1),
    ],
    'bps': [
        ('BookValuePerShare', 1), ('NetAssetsPerShare', 2),
    ],
    'dividend_per_share': [
        ('DividendPerShare', 1), ('DividendsPerShare', 2), ('CashDividendPerShare', 3),
    ],

    # ==================== B/S項目 ====================
    'cash_and_deposits': [
        ('CashAndDeposits', 1), ('CashAndCashEquivalents', 2), ('Cash', 3),
        ('CashOnHandAndInBanks', 4), ('CashAndBankDeposits', 5), ('CashAndDueFromBanks', 6),
        ('CashAndDepositsBNK', 7), ('Deposits', 8),
    ],
    'notes_accounts_receivable': [
        ('NotesAndAccountsReceivableTrade', 1), ('NotesAndAccountsReceivableTradeAndContractAssets', 2),
        ('TradeAndOtherReceivables', 3), ('ReceivablesFromContractsWithCustomers', 4), ('TradeReceivables', 5),
        ('NotesReceivableAccountsReceivableFromCompletedConstructionContractsAndOther', 6),
    ],
    'accounts_receivable': [
        ('AccountsReceivableTrade', 1), ('AccountsReceivable', 2), ('TradeAccountsReceivable', 3),
        ('TradeReceivables', 4), ('ReceivablesTrade', 5), ('AccountsReceivableNet', 6), ('AccountsReceivableOther', 7),
    ],
    'notes_receivable': [
        ('NotesReceivableTrade', 1), ('NotesReceivable', 2), ('TradeNotesReceivable', 3),
        ('BillsReceivable', 4), ('NotesReceivableNet', 5),
    ],
    'securities': [
        ('Securities', 1), ('MarketableSecurities', 2), ('ShortTermInvestmentSecurities', 3),
        ('TradingSecurities', 4), ('SecuritiesCA', 5), ('HeldToMaturitySecurities', 6),
    ],
    'merchandise_finished_goods': [
        ('MerchandiseAndFinishedGoods', 1), ('Merchandise', 2), ('FinishedGoods', 3),
        ('FinishedProducts', 4), ('MerchandiseAndFinishedGoodsNet', 5), ('ProductsAndMerchandise', 6),
    ],
    'work_in_process': [
        ('WorkInProcess', 1), ('WorkInProgress', 2), ('GoodsInProcess', 3),
        ('ConstructionInProgressCA', 4), ('SemiFinishedGoods', 5), ('WorkInProcessNet', 6),
    ],
    'raw_materials': [
        ('RawMaterials', 1), ('RawMaterialsAndSupplies', 2), ('MaterialsAndSupplies', 3),
        ('RawMaterialsNet', 4), ('Materials', 5),
    ],
    'supplies': [
        ('Supplies', 1), ('StoresAndSupplies', 2), ('SuppliesNet', 3), ('OtherSupplies', 4), ('ConsumableSupplies', 5),
    ],
    'inventories': [
        ('Inventories', 1), ('TotalInventories', 2), ('InventoriesTotal', 3),
        ('InventoriesNet', 4), ('InventoriesIFRS', 5), ('MerchandiseAndFinishedGoods', 6), ('RealEstateForSale', 7),
    ],
    'advance_payments': [
        ('AdvancePayments', 2), ('PrepaidExpenses', 3), ('Prepayments', 4),
        ('AdvancesAndPrepayments', 5), ('AdvancePaymentsOther', 6),
    ],
    'short_term_loans_receivable': [
        ('ShortTermLoansReceivable', 1), ('LoansReceivable', 2),
        ('ShortTermLoansReceivableFromSubsidiariesAndAffiliates', 3),
        ('CurrentPortionOfLongTermLoansReceivable', 4), ('LoansReceivableCurrent', 5),
    ],
    'accrued_income': [
        ('AccruedIncome', 1), ('AccruedRevenue', 2), ('AccruedInterestIncome', 3),
        ('AccruedIncomeOther', 4), ('UnbilledReceivables', 5),
    ],
    'deferred_tax_assets_current': [
        ('DeferredTaxAssets', 1), ('DeferredTaxAssetsCurrent', 2), ('CurrentDeferredTaxAssets', 3),
        ('DeferredIncomeTaxesCurrent', 4), ('DeferredTaxAssetsCA', 5),
    ],
    'other_current_assets': [
        ('OtherCurrentAssets', 1), ('OtherCA', 2), ('SundryCurrentAssets', 3),
        ('OtherReceivables', 4), ('OtherCurrentAssetsNet', 5), ('MiscellaneousCurrentAssets', 6),
    ],
    'allowance_doubtful_accounts_current': [
        ('AllowanceForDoubtfulAccountsCA', 1), ('AllowanceForDoubtfulAccounts', 2),
        ('AllowanceForBadDebts', 3), ('ProvisionForDoubtfulReceivables', 4),
        ('AllowanceForCreditLosses', 5), ('ReserveForBadDebts', 6),
    ],
    'property_plant_equipment': [
        ('PropertyPlantAndEquipment', 1), ('PropertyPlantAndEquipmentNet', 2),
        ('TotalPropertyPlantAndEquipment', 3), ('NetPropertyPlantAndEquipment', 4),
        ('TangibleFixedAssets', 5), ('TangibleAssets', 6), ('PropertyPlantEquipment', 7), ('PropertyPlantAndEquipmentIFRS', 8),
    ],
    'buildings_structures': [
        ('BuildingsAndStructures', 1), ('BuildingsAndStructuresNet', 2), ('Buildings', 3),
        ('BuildingsNet', 4), ('Structures', 5), ('BuildingsAndImprovements', 6), ('PropertyBuildings', 7),
    ],
    'machinery_equipment': [
        ('MachineryAndEquipment', 1), ('MachineryEquipmentAndVehicles', 2), ('MachineryAndEquipmentNet', 3),
        ('Machinery', 4), ('MachineryAndVehicles', 5), ('ProductionEquipment', 6), ('MachineryEquipmentAndVehiclesNet', 7),
    ],
    'tools_furniture_fixtures': [
        ('ToolsFurnitureAndFixtures', 1), ('ToolsFurnitureAndFixturesNet', 2), ('FurnitureAndFixtures', 3),
        ('EquipmentAndFixtures', 4), ('OtherEquipment', 5), ('ToolsAndEquipment', 6),
    ],
    'land': [
        ('Land', 1), ('LandNet', 2), ('LandAndBuildings', 3), ('PropertyLand', 4), ('LandHeldForDevelopment', 5),
    ],
    'leased_assets': [
        ('LeasedAssets', 1), ('LeasedAssetsNet', 2), ('RightOfUseAssets', 3),
        ('FinanceLeasedAssets', 4), ('CapitalizedLeaseAssets', 5), ('LeaseRightOfUseAssets', 6),
    ],
    'construction_in_progress': [
        ('ConstructionInProgress', 1), ('ConstructionInProgressPPE', 2), ('AssetsUnderConstruction', 3),
        ('ConstructionInProgressNet', 4), ('PropertyUnderConstruction', 5),
    ],
    'accumulated_depreciation': [
        ('AccumulatedDepreciation', 1), ('AccumulatedDepreciationPPE', 2), ('TotalAccumulatedDepreciation', 3),
        ('AccumulatedDepreciationAndAmortization', 4), ('DepreciationAccumulated', 5),
    ],
    'intangible_assets': [
        ('IntangibleAssets', 1), ('TotalIntangibleAssets', 2), ('IntangibleAssetsNet', 3),
        ('NetIntangibleAssets', 4), ('IntangibleFixedAssets', 5), ('OtherIntangibleAssets', 6), ('IntangibleAssetsIFRS', 7),
    ],
    'goodwill': [
        ('Goodwill', 1), ('GoodwillNet', 2), ('GoodwillGross', 3),
        ('ConsolidatedGoodwill', 4), ('GoodwillOnAcquisition', 5), ('PurchasedGoodwill', 6),
    ],
    'software': [
        ('Software', 1), ('SoftwareNet', 2), ('ComputerSoftware', 3),
        ('SoftwareAndOther', 4), ('CapitalizedSoftware', 5), ('SoftwareAssets', 6),
    ],
    'customer_related_assets': [
        ('CustomerRelationships', 1), ('CustomerRelatedAssets', 2), ('CustomerRelatedIntangibleAssets', 3),
        ('CustomerLists', 4), ('CustomerContracts', 5), ('CustomerRelationshipsNet', 6),
    ],
    'technology_based_assets': [
        ('TechnologyBasedAssets', 1), ('TechnologyAssets', 2), ('DevelopedTechnology', 3),
        ('TechnicalAssets', 4), ('TechnologyRelatedIntangibleAssets', 5), ('AcquiredTechnology', 6),
    ],
    'trademarks': [
        ('Trademarks', 1), ('TrademarksNet', 2), ('TradeNames', 3),
        ('TrademarksAndTradeNames', 4), ('BrandAssets', 5), ('TrademarksGross', 6),
    ],
    'patents': [
        ('Patents', 1), ('PatentsNet', 2), ('PatentRights', 3),
        ('PatentsAndLicenses', 4), ('IndustrialPropertyRights', 5), ('IntellectualPropertyRights', 6),
    ],
    'rights': [
        ('Rights', 1), ('OtherRights', 2), ('MiningRights', 3),
        ('FisheryRights', 4), ('UtilityRights', 5), ('ConcessionRights', 6),
    ],
    'leasehold_rights': [
        ('LeaseholdRight', 1), ('LeaseholdRights', 2), ('LeaseRights', 3),
        ('RightOfLease', 4), ('LandLeaseRights', 5),
    ],
    'investments_other_assets': [
        ('InvestmentsAndOtherAssets', 1), ('TotalInvestmentsAndOtherAssets', 2),
        ('InvestmentsAndOtherAssetsNet', 3), ('OtherNonCurrentAssets', 4), ('LongTermInvestments', 5), ('OtherAssets', 6),
    ],
    'investment_securities': [
        ('InvestmentSecurities', 1), ('TotalInvestmentSecurities', 2), ('InvestmentSecuritiesNet', 3),
        ('InvestmentsInSecurities', 4), ('AvailableForSaleSecurities', 5),
        ('LongTermInvestmentSecurities', 6), ('OtherFinancialAssets', 7),
    ],
    'shares_of_subsidiaries_associates': [
        ('InvestmentsInSubsidiariesAndAffiliates', 1), ('SharesOfSubsidiariesAndAssociates', 2),
        ('InvestmentInAssociates', 3), ('EquityMethodInvestments', 4),
        ('InvestmentsAccountedForUsingEquityMethod', 5), ('InvestmentsInAffiliates', 6),
    ],
    'long_term_loans_receivable': [
        ('LongTermLoansReceivable', 1), ('LoansReceivableNoncurrent', 2),
        ('LongTermLoansReceivableFromSubsidiariesAndAffiliates', 3), ('LongTermLoans', 4),
        ('LoansToRelatedParties', 5), ('OtherLoansReceivable', 6),
    ],
    'long_term_prepaid_expenses': [
        ('LongTermPrepaidExpenses', 1), ('PrepaidExpensesNoncurrent', 2), ('DeferredCharges', 3),
        ('LongTermPrepayments', 4), ('OtherPrepaidExpenses', 5),
    ],
    'deferred_tax_assets_noncurrent': [
        ('DeferredTaxAssets', 1), ('DeferredTaxAssetsNoncurrent', 2), ('NoncurrentDeferredTaxAssets', 3),
        ('DeferredIncomeTaxesNoncurrent', 4), ('DeferredTaxAssetsNCA', 5), ('LongTermDeferredTaxAssets', 6),
    ],
    'net_defined_benefit_asset': [
        ('NetDefinedBenefitAsset', 1), ('RetirementBenefitAsset', 2), ('PensionAsset', 3),
        ('PrepaidPensionCost', 4), ('DefinedBenefitAssets', 5), ('EmployeeBenefitAssets', 6),
    ],
    'guarantee_deposits': [
        ('GuaranteeDeposits', 1), ('LeaseDeposits', 2), ('SecurityDeposits', 3),
        ('RefundableDeposits', 4), ('DepositsPaid', 5), ('RentalDeposits', 6),
    ],
    'membership': [
        ('Memberships', 1), ('GolfClubMemberships', 2), ('ClubMemberships', 3),
        ('MembershipDeposits', 4), ('OtherMemberships', 5),
    ],
    'other_investments': [
        ('OtherInvestmentsAndOtherAssets', 1), ('OtherInvestments', 2), ('OtherNoncurrentAssets', 3),
        ('SundryAssets', 4), ('OtherAssetsNoncurrent', 5), ('MiscellaneousAssets', 6),
    ],
    'allowance_doubtful_accounts_noncurrent': [
        ('AllowanceForDoubtfulAccountsIAOA', 1), ('AllowanceForDoubtfulAccountsNoncurrent', 2),
        ('AllowanceForBadDebtsNoncurrent', 3), ('ProvisionForDoubtfulReceivablesNoncurrent', 4),
        ('AllowanceForCreditLossesNoncurrent', 5),
    ],
    'notes_accounts_payable': [
        ('NotesAndAccountsPayableTrade', 1), ('TradeAndOtherPayables', 2), ('TradePayables', 3),
        ('NotesAndAccountsPayable', 4), ('PayablesToSuppliers', 5), ('AccountsAndNotesPayable', 6),
    ],
    'accounts_payable': [
        ('AccountsPayableTrade', 1), ('AccountsPayable', 2), ('TradeAccountsPayable', 3),
        ('TradePayables', 4), ('PayablesTrade', 5), ('AccountsPayableOther', 6),
    ],
    'notes_payable': [
        ('NotesPayableTrade', 1), ('NotesPayable', 2), ('TradeNotesPayable', 3),
        ('BillsPayable', 4), ('NotesPayableOther', 5),
    ],
    'short_term_borrowings': [
        ('ShortTermLoansPayable', 1), ('ShortTermBorrowings', 2), ('ShortTermDebt', 3),
        ('ShortTermBankLoans', 4), ('BorrowingsCurrent', 5), ('ShortTermLoans', 6),
    ],
    'current_portion_long_term_debt': [
        ('CurrentPortionOfLongTermLoansPayable', 1), ('CurrentPortionOfLongTermDebt', 2),
        ('LongTermDebtCurrentPortion', 3), ('CurrentMaturitiesOfLongTermDebt', 4),
        ('CurrentPortionOfBonds', 5), ('LongTermLoansPayableWithinOneYear', 6),
    ],
    'commercial_papers': [
        ('CommercialPapers', 1), ('CommercialPaper', 2), ('ShortTermCommercialPaper', 3),
        ('CP', 4), ('CommercialPapersPayable', 5),
    ],
    'bonds_payable_current': [
        ('BondsPayableCurrent', 1), ('CurrentPortionOfBondsPayable', 2), ('BondsDueWithinOneYear', 3),
        ('BondsRedeemableWithinOneYear', 4), ('ShortTermBonds', 5),
    ],
    'income_taxes_payable': [
        ('IncomeTaxesPayable', 1), ('AccruedIncomeTaxes', 2), ('CurrentTaxLiabilities', 3),
        ('IncomeTaxPayable', 4), ('TaxesPayable', 5), ('CorporateTaxPayable', 6),
    ],
    'accrued_expenses': [
        ('AccruedExpenses', 1), ('AccruedLiabilities', 2), ('AccrualsAndDeferredIncome', 3),
        ('OtherAccruedExpenses', 4), ('AccruedExpensesAndOtherCurrentLiabilities', 5),
    ],
    'accrued_bonuses': [
        ('AccruedBonuses', 1), ('AccruedBonusesToEmployees', 2), ('BonusesPayable', 3),
        ('AccruedEmployeeBonuses', 4), ('UnpaidBonuses', 5),
    ],
    'advances_received': [
        ('AdvancesReceived', 1), ('AdvanceReceipts', 2), ('DeferredRevenue', 3),
        ('ContractLiabilities', 4), ('UnearnedRevenue', 5), ('CustomerAdvances', 6),
    ],
    'deposits_received': [
        ('DepositsReceived', 1), ('DepositsFromCustomers', 2), ('CustomerDeposits', 3),
        ('RefundableSalesDeposits', 4), ('DepositsReceivedOther', 5),
    ],
    'provision_for_bonuses': [
        ('ProvisionForBonuses', 1), ('ProvisionForEmployeeBonuses', 2), ('BonusProvision', 3),
        ('AccruedBonusesProvision', 4), ('ProvisionForBonusesCA', 5),
    ],
    'provision_product_warranties': [
        ('ProvisionForProductWarranties', 1), ('ProductWarrantyProvision', 2), ('WarrantyProvision', 3),
        ('ProvisionForWarranties', 4), ('ProductWarrantyReserve', 5), ('AccruedProductWarranties', 6),
    ],
    'other_current_liabilities': [
        ('OtherCurrentLiabilities', 1), ('OtherCL', 2), ('SundryCurrentLiabilities', 3),
        ('OtherPayables', 4), ('OtherCurrentLiabilitiesNet', 5), ('MiscellaneousCurrentLiabilities', 6),
    ],
    'bonds_payable': [
        ('BondsPayable', 1), ('Bonds', 2), ('CorporateBonds', 3),
        ('BondsAndDebentures', 4), ('ConvertibleBonds', 5), ('BondsPayableNoncurrent', 6), ('SeniorNotes', 7),
    ],
    'long_term_borrowings': [
        ('LongTermLoansPayable', 1), ('LongTermDebt', 2), ('LongTermBorrowings', 3),
        ('LongTermDebtNoncurrent', 4), ('BorrowingsNoncurrent', 5), ('LongTermBankLoans', 6), ('LoansPayableNoncurrent', 7),
    ],
    'deferred_tax_liabilities': [
        ('DeferredTaxLiabilities', 1), ('DeferredTaxLiabilitiesNoncurrent', 2),
        ('NoncurrentDeferredTaxLiabilities', 3), ('DeferredIncomeTaxLiabilities', 4),
        ('DeferredTaxLiabilitiesNet', 5), ('LongTermDeferredTaxLiabilities', 6),
    ],
    'net_defined_benefit_liability': [
        ('NetDefinedBenefitLiability', 1), ('RetirementBenefitLiability', 2), ('PensionLiability', 3),
        ('DefinedBenefitObligations', 4), ('EmployeeBenefitLiabilities', 5),
        ('AccruedPensionCosts', 6), ('ProvisionForRetirementBenefits', 7),
    ],
    'provision_directors_retirement': [
        ('ProvisionForDirectorsRetirementBenefits', 1), ('ProvisionForRetirementBenefitsForDirectors', 2),
        ('DirectorsRetirementBenefits', 3), ('ReserveForDirectorsRetirement', 4),
        ('ProvisionForOfficersRetirementBenefits', 5),
    ],
    'provision_environment': [
        ('ProvisionForEnvironmentalMeasures', 1), ('EnvironmentalProvision', 2),
        ('ProvisionForEnvironmentalRemediation', 3), ('EnvironmentalLiabilities', 4),
        ('ReserveForEnvironmentalCosts', 5),
    ],
    'asset_retirement_obligations': [
        ('AssetRetirementObligations', 1), ('AssetRetirementObligation', 2), ('DecommissioningProvision', 3),
        ('ProvisionForAssetRetirement', 4), ('AssetRetirementObligationsNoncurrent', 5), ('ARO', 6),
    ],
    'long_term_accounts_payable': [
        ('LongTermAccountsPayableOther', 1), ('LongTermAccountsPayable', 2), ('OtherPayablesNoncurrent', 3),
        ('AccruedLiabilitiesNoncurrent', 4), ('LongTermPayables', 5),
    ],
    'other_noncurrent_liabilities': [
        ('OtherNoncurrentLiabilities', 1), ('OtherLongTermLiabilities', 2), ('OtherLiabilitiesNoncurrent', 3),
        ('SundryNoncurrentLiabilities', 4), ('MiscellaneousNoncurrentLiabilities', 5), ('OtherFixedLiabilities', 6),
    ],
    'shareholders_equity': [
        ('ShareholdersEquity', 1), ('TotalShareholdersEquity', 2), ('StockholdersEquity', 3),
        ('TotalStockholdersEquity', 4), ('EquityAttributableToOwnersOfParent', 5),
        ('OwnersEquity', 6), ('ShareCapitalAndReserves', 7),
    ],
    'capital_stock': [
        ('CapitalStock', 1), ('CommonStock', 2), ('ShareCapital', 3),
        ('IssuedCapital', 4), ('PaidInCapital', 5), ('StatedCapital', 6), ('CapitalStockCommon', 7),
    ],
    'capital_surplus': [
        ('CapitalSurplus', 1), ('AdditionalPaidInCapital', 2), ('SharePremium', 3),
        ('PaidInCapitalInExcessOfPar', 4), ('CapitalReserve', 5), ('TotalCapitalSurplus', 6),
    ],
    'legal_capital_surplus': [
        ('LegalCapitalSurplus', 1), ('CapitalReserve', 2), ('StatutoryCapitalSurplus', 3),
        ('LegalReserve', 4), ('CapitalReserveLegal', 5),
    ],
    'other_capital_surplus': [
        ('OtherCapitalSurplus', 1), ('OtherCapitalReserves', 2), ('VoluntaryCapitalSurplus', 3),
        ('AdditionalCapitalSurplus', 4), ('OtherPaidInCapital', 5),
    ],
    'retained_earnings': [
        ('RetainedEarnings', 1), ('RetainedEarningsSurplus', 2), ('AccumulatedEarnings', 3),
        ('EarnedSurplus', 4), ('TotalRetainedEarnings', 5), ('RetainedProfits', 6), ('AccumulatedProfits', 7),
    ],
    'legal_retained_earnings': [
        ('LegalRetainedEarnings', 1), ('EarnedSurplusReserve', 2), ('StatutoryRetainedEarnings', 3),
        ('LegalEarnedReserve', 4), ('RetainedEarningsReserve', 5),
    ],
    'other_retained_earnings': [
        ('OtherRetainedEarnings', 1), ('VoluntaryRetainedEarnings', 2), ('UnappropriatedRetainedEarnings', 3),
        ('GeneralReserve', 4), ('RetainedEarningsBroughtForward', 5), ('OtherEarnedSurplus', 6),
    ],
    'treasury_stock': [
        ('TreasuryStock', 1), ('TreasuryShares', 2), ('TreasuryStockAtCost', 3),
        ('OwnSharesHeld', 4), ('RepurchasedShares', 5), ('TreasuryStockCommon', 6),
    ],
    'accumulated_other_comprehensive_income': [
        ('AccumulatedOtherComprehensiveIncome', 1), ('TotalAccumulatedOtherComprehensiveIncome', 2),
        ('OtherComprehensiveIncome', 3), ('AccumulatedOCI', 4),
        ('OtherReserves', 5), ('ValuationAndTranslationAdjustments', 6),
    ],
    'valuation_difference_securities': [
        ('ValuationDifferenceOnAvailableForSaleSecurities', 1), ('UnrealizedGainOnSecurities', 2),
        ('UnrealizedGainLossOnAvailableForSaleSecurities', 3), ('NetUnrealizedGainOnSecurities', 4),
        ('FairValueReserve', 5), ('AvailableForSaleFinancialAssetsReserve', 6),
    ],
    'deferred_gains_hedges': [
        ('DeferredGainsOrLossesOnHedges', 1), ('DeferredHedgeGainLoss', 2), ('CashFlowHedgeReserve', 3),
        ('HedgingReserve', 4), ('DeferredGainsOnHedges', 5),
    ],
    'land_revaluation_difference': [
        ('RevaluationReserveLand', 1), ('LandRevaluationDifference', 2), ('RevaluationSurplus', 3),
        ('LandRevaluationExcess', 4), ('RevaluationReserve', 5),
    ],
    'foreign_currency_translation_adjustment': [
        ('ForeignCurrencyTranslationAdjustment', 1), ('ForeignCurrencyTranslationAdjustments', 2),
        ('CumulativeTranslationAdjustments', 3), ('TranslationReserve', 4),
        ('ForeignExchangeReserve', 5), ('CurrencyTranslationDifferences', 6),
    ],
    'remeasurement_defined_benefit_plans': [
        ('RemeasurementsOfDefinedBenefitPlans', 1), ('RemeasurementOfNetDefinedBenefitLiability', 2),
        ('ActuarialGainsAndLosses', 3), ('RemeasurementsOfPostEmploymentBenefitObligations', 4),
        ('PensionAdjustments', 5),
    ],
    'subscription_rights': [
        ('SubscriptionRightsToShares', 1), ('StockAcquisitionRights', 2), ('WarrantsOutstanding', 3),
        ('StockOptions', 4), ('ShareBasedPaymentReserve', 5),
    ],
    'non_controlling_interests': [
        ('NonControllingInterests', 1), ('MinorityInterests', 2), ('NonControllingInterest', 3),
        ('MinorityShareholdersEquity', 4), ('InterestOfMinorityShareholders', 5),
        ('EquityAttributableToNonControllingInterests', 6),
    ],
    'current_assets': [
        ('CurrentAssets', 1), ('TotalCurrentAssets', 2), ('CurrentAssetsTotal', 3),
        ('CurrentAssetsIFRS', 4), ('CurrentAssetsUS', 5), ('CurrentAssetsCA', 6),
    ],
    'non_current_assets': [
        ('NoncurrentAssets', 1), ('NonCurrentAssets', 2), ('TotalNoncurrentAssets', 3),
        ('FixedAssets', 4), ('TotalFixedAssets', 5), ('NoncurrentAssetsIFRS', 6),
    ],
    'total_assets': [
        ('TotalAssets', 1), ('Assets', 2), ('TotalAssetsIFRS', 3),
        ('TotalAssetsUS', 4), ('ConsolidatedTotalAssets', 5), ('AssetsTotal', 6),
    ],
    'current_liabilities': [
        ('CurrentLiabilities', 1), ('TotalCurrentLiabilities', 2), ('CurrentLiabilitiesTotal', 3),
        ('CurrentLiabilitiesIFRS', 4), ('CurrentLiabilitiesUS', 5), ('CurrentLiabilitiesCL', 6),
    ],
    'non_current_liabilities': [
        ('NoncurrentLiabilities', 1), ('NonCurrentLiabilities', 2), ('TotalNoncurrentLiabilities', 3),
        ('LongTermLiabilities', 4), ('TotalLongTermLiabilities', 5), ('NoncurrentLiabilitiesIFRS', 6),
    ],
    'total_liabilities': [
        ('Liabilities', 1), ('TotalLiabilities', 2), ('LiabilitiesTotal', 3),
        ('TotalLiabilitiesIFRS', 4), ('TotalLiabilitiesUS', 5),
    ],
    'total_equity': [
        ('NetAssets', 1), ('TotalNetAssets', 2), ('TotalEquity', 3), ('Equity', 4),
        ('EquityAttributableToOwnersOfParent', 5), ('TotalEquityIFRS', 6),
    ],
    'total_liabilities_and_equity': [
        ('LiabilitiesAndNetAssets', 1), ('TotalLiabilitiesAndNetAssets', 2),
        ('LiabilitiesAndEquity', 3), ('TotalLiabilitiesAndEquity', 4),
        ('TotalLiabilitiesAndShareholdersEquity', 5),
    ],

    # ==================== C/F項目 ====================
    'operating_cf': [
        ('NetCashProvidedByUsedInOperatingActivities', 1), ('CashFlowsFromUsedInOperatingActivities', 2),
        ('CashFlowsFromOperatingActivities', 3), ('NetCashFromOperatingActivities', 4),
        ('OperatingActivitiesNetCash', 5), ('CashProvidedByOperatingActivities', 6),
    ],
    'income_before_taxes_cf': [
        ('IncomeBeforeIncomeTaxes', 1), ('ProfitLossBeforeTax', 2), ('ProfitBeforeTax', 3),
        ('IncomeBeforeTaxes', 4), ('EarningsBeforeIncomeTaxes', 5),
    ],
    'depreciation': [
        ('DepreciationAndAmortizationOpeCF', 1), ('DepreciationAndAmortization', 2), ('Depreciation', 3),
        ('DepreciationExpense', 4), ('DepreciationAmortization', 5), ('DepreciationAndAmortizationIFRS', 6),
    ],
    'amortization_goodwill': [
        ('AmortizationOfGoodwillOpeCF', 1), ('AmortizationOfGoodwill', 2), ('GoodwillAmortization', 3),
        ('GoodwillImpairment', 4), ('ImpairmentOfGoodwill', 5),
    ],
    'impairment_loss_cf': [
        ('ImpairmentLossOpeCF', 1), ('ImpairmentLoss', 2), ('ImpairmentOfAssets', 3),
        ('AssetImpairmentLoss', 4), ('WriteDownOfAssets', 5),
    ],
    'increase_allowance_cf': [
        ('IncreaseDecreaseInProvisionForDoubtfulAccountsOpeCF', 1),
        ('IncreaseDecreaseInAllowanceForDoubtfulAccounts', 2), ('ProvisionForBadDebts', 3),
        ('ChangeInAllowanceForDoubtfulAccounts', 4), ('BadDebtExpense', 5),
    ],
    'interest_dividend_income_cf': [
        ('InterestAndDividendsIncomeOpeCF', 1), ('InterestAndDividendIncome', 2),
        ('FinanceIncome', 3), ('InterestIncome', 4), ('DividendIncome', 5),
    ],
    'interest_expense_cf': [
        ('InterestExpensesOpeCF', 1), ('InterestExpense', 2), ('FinanceCosts', 3),
        ('InterestPaid', 4), ('InterestOnBorrowings', 5),
    ],
    'equity_in_earnings_cf': [
        ('ShareOfProfitOfEntitiesAccountedForUsingEquityMethodOpeCF', 1),
        ('EquityInEarningsOfAffiliates', 2), ('ShareOfProfitOfAssociates', 3),
        ('IncomeFromEquityMethodInvestments', 4), ('EquityMethodIncome', 5),
    ],
    'gain_loss_sale_fixed_assets_cf': [
        ('GainOnSalesOfNoncurrentAssetsOpeCF', 1), ('LossOnSalesOfNoncurrentAssetsOpeCF', 2),
        ('GainLossOnDisposalOfFixedAssets', 3), ('GainLossOnSaleOfPropertyPlantAndEquipment', 4),
        ('ProfitLossOnDisposalOfAssets', 5),
    ],
    'loss_retirement_fixed_assets_cf': [
        ('LossOnRetirementOfNoncurrentAssetsOpeCF', 1), ('LossOnDisposalOfFixedAssets', 2),
        ('LossOnDisposalOfPropertyPlantAndEquipment', 3), ('RetirementOfAssets', 4), ('WriteOffOfFixedAssets', 5),
    ],
    'gain_loss_sale_securities_cf': [
        ('GainOnSalesOfInvestmentSecuritiesOpeCF', 1), ('LossOnSalesOfInvestmentSecuritiesOpeCF', 2),
        ('GainLossOnSaleOfSecurities', 3), ('GainLossOnDisposalOfInvestments', 4), ('ProfitLossOnSaleOfInvestments', 5),
    ],
    'loss_valuation_securities_cf': [
        ('LossOnValuationOfInvestmentSecuritiesOpeCF', 1), ('LossOnValuationOfSecurities', 2),
        ('ImpairmentOfInvestmentSecurities', 3), ('WriteDownOfInvestments', 4), ('UnrealizedLossOnSecurities', 5),
    ],
    'decrease_increase_receivables_cf': [
        ('DecreaseIncreaseInNotesAndAccountsReceivableTradeOpeCF', 1),
        ('DecreaseIncreaseInTradeReceivables', 2), ('IncreaseDecreaseInTradeReceivables', 3),
        ('ChangeInAccountsReceivable', 4), ('IncreaseDecreaseInAccountsReceivable', 5),
    ],
    'decrease_increase_inventories_cf': [
        ('DecreaseIncreaseInInventoriesOpeCF', 1), ('DecreaseIncreaseInInventories', 2),
        ('IncreaseDecreaseInInventories', 3), ('ChangeInInventories', 4), ('InventoryChange', 5),
    ],
    'increase_decrease_payables_cf': [
        ('IncreaseDecreaseInNotesAndAccountsPayableTradeOpeCF', 1),
        ('IncreaseDecreaseInTradePayables', 2), ('IncreaseDecreaseInAccountsPayable', 3),
        ('ChangeInAccountsPayable', 4), ('IncreaseDecreaseInPayables', 5),
    ],
    'subtotal_cf': [
        ('SubtotalOpeCF', 1), ('CashGeneratedFromOperations', 2), ('SubtotalBeforeInterestAndTaxes', 3),
        ('OperatingCashFlowBeforeChangesInWorkingCapital', 4), ('Subtotal', 5),
    ],
    'interest_received_cf': [
        ('InterestAndDividendsIncomeReceivedOpeCF', 1), ('InterestAndDividendsReceived', 2),
        ('InterestReceived', 3), ('CashReceivedFromInterest', 4), ('InterestAndDividendReceived', 5),
    ],
    'interest_paid_cf': [
        ('InterestExpensesPaidOpeCF', 1), ('InterestPaid', 2), ('CashPaidForInterest', 3),
        ('InterestPayments', 4), ('InterestExpensesPaid', 5),
    ],
    'income_taxes_paid_cf': [
        ('IncomeTaxesPaidOpeCF', 1), ('IncomeTaxesPaid', 2), ('TaxesPaid', 3),
        ('CashPaidForTaxes', 4), ('IncomeTaxPayments', 5),
    ],
    'income_taxes_refund_cf': [
        ('IncomeTaxesRefundOpeCF', 1), ('IncomeTaxesRefunded', 2), ('TaxRefunds', 3),
        ('RefundOfIncomeTaxes', 4), ('IncomeTaxRefund', 5),
    ],
    'investing_cf': [
        ('NetCashProvidedByUsedInInvestingActivities', 1), ('CashFlowsFromUsedInInvestingActivities', 2),
        ('CashFlowsFromInvestingActivities', 3), ('NetCashFromInvestingActivities', 4),
        ('InvestingActivitiesNetCash', 5), ('CashUsedInInvestingActivities', 6),
    ],
    'purchase_ppe_cf': [
        ('PurchaseOfPropertyPlantAndEquipmentInvCF', 1), ('PurchaseOfPropertyPlantAndEquipment', 2),
        ('PaymentsForAcquisitionOfPropertyPlantAndEquipment', 3), ('CapitalExpenditures', 4),
        ('AcquisitionOfPropertyPlantAndEquipment', 5), ('AdditionsToPropertyPlantAndEquipment', 6),
    ],
    'proceeds_sale_ppe_cf': [
        ('ProceedsFromSalesOfPropertyPlantAndEquipmentInvCF', 1),
        ('ProceedsFromSaleOfPropertyPlantAndEquipment', 2),
        ('ProceedsFromDisposalOfPropertyPlantAndEquipment', 3),
        ('ProceedsFromSaleOfFixedAssets', 4), ('SaleOfPropertyPlantAndEquipment', 5),
    ],
    'purchase_intangibles_cf': [
        ('PurchaseOfIntangibleAssetsInvCF', 1), ('PurchaseOfIntangibleAssets', 2),
        ('PaymentsForAcquisitionOfIntangibleAssets', 3), ('AcquisitionOfIntangibleAssets', 4),
        ('AdditionsToIntangibleAssets', 5),
    ],
    'proceeds_sale_intangibles_cf': [
        ('ProceedsFromSalesOfIntangibleAssetsInvCF', 1), ('ProceedsFromSaleOfIntangibleAssets', 2),
        ('ProceedsFromDisposalOfIntangibleAssets', 3), ('SaleOfIntangibleAssets', 4), ('DisposalOfIntangibleAssets', 5),
    ],
    'purchase_investment_securities_cf': [
        ('PurchaseOfInvestmentSecuritiesInvCF', 1), ('PurchaseOfInvestmentSecurities', 2),
        ('PaymentsForAcquisitionOfInvestments', 3), ('AcquisitionOfInvestmentSecurities', 4),
        ('PurchaseOfMarketableSecurities', 5),
    ],
    'proceeds_sale_investment_securities_cf': [
        ('ProceedsFromSalesAndRedemptionOfInvestmentSecuritiesInvCF', 1),
        ('ProceedsFromSaleOfInvestmentSecurities', 2), ('ProceedsFromDisposalOfInvestments', 3),
        ('SaleOfInvestmentSecurities', 4), ('ProceedsFromRedemptionOfSecurities', 5),
    ],
    'purchase_shares_subsidiaries_cf': [
        ('PurchaseOfSharesOfSubsidiariesResultingInChangeInScopeOfConsolidationInvCF', 1),
        ('PaymentsForAcquisitionOfSubsidiaries', 2), ('AcquisitionOfBusinesses', 3),
        ('BusinessAcquisitions', 4), ('CashPaidForAcquisitions', 5), ('PurchaseOfSubsidiaries', 6),
    ],
    'proceeds_sale_subsidiaries_cf': [
        ('ProceedsFromSalesOfSharesOfSubsidiariesResultingInChangeInScopeOfConsolidationInvCF', 1),
        ('ProceedsFromDisposalOfSubsidiaries', 2), ('ProceedsFromSaleOfSubsidiaries', 3),
        ('DisposalOfSubsidiaries', 4), ('SaleOfBusinesses', 5),
    ],
    'payments_loans_cf': [
        ('PaymentsOfLoansReceivableInvCF', 1), ('PaymentsForLoansReceivable', 2), ('LoansAdvanced', 3),
        ('IncreasesInLoansReceivable', 4), ('AdvancesToRelatedParties', 5),
    ],
    'collection_loans_cf': [
        ('CollectionOfLoansReceivableInvCF', 1), ('ProceedsFromCollectionOfLoansReceivable', 2),
        ('LoansCollected', 3), ('DecreasesInLoansReceivable', 4), ('RepaymentOfLoansReceivable', 5),
    ],
    'other_investing_cf': [
        ('OtherInvCF', 1), ('OtherInvestingCashFlow', 2), ('OtherInvestingActivities', 3),
        ('NetOtherInvestingActivities', 4), ('MiscellaneousInvestingActivities', 5),
    ],
    'financing_cf': [
        ('NetCashProvidedByUsedInFinancingActivities', 1), ('CashFlowsFromUsedInFinancingActivities', 2),
        ('CashFlowsFromFinancingActivities', 3), ('NetCashFromFinancingActivities', 4),
        ('FinancingActivitiesNetCash', 5), ('CashUsedInFinancingActivities', 6),
    ],
    'net_increase_short_term_borrowings_cf': [
        ('NetIncreaseDecreaseInShortTermLoansPayableFinCF', 1),
        ('NetIncreaseDecreaseInShortTermBorrowings', 2), ('ChangeInShortTermBorrowings', 3),
        ('IncreaseDecreaseInShortTermDebt', 4), ('NetChangeInShortTermLoans', 5),
    ],
    'proceeds_long_term_borrowings_cf': [
        ('ProceedsFromLongTermLoansPayableFinCF', 1), ('ProceedsFromLongTermBorrowings', 2),
        ('ProceedsFromBorrowings', 3), ('ProceedsFromIssuanceOfLongTermDebt', 4), ('BorrowingsProceeds', 5),
    ],
    'repayments_long_term_borrowings_cf': [
        ('RepaymentsOfLongTermLoansPayableFinCF', 1), ('RepaymentsOfLongTermBorrowings', 2),
        ('RepaymentsOfBorrowings', 3), ('RepaymentsOfLongTermDebt', 4), ('BorrowingsRepayments', 5),
    ],
    'proceeds_issuance_bonds_cf': [
        ('ProceedsFromIssuanceOfBondsFinCF', 1), ('ProceedsFromIssuanceOfBonds', 2),
        ('ProceedsFromBondIssuance', 3), ('IssuanceOfBonds', 4), ('BondProceeds', 5),
    ],
    'redemption_bonds_cf': [
        ('RedemptionOfBondsFinCF', 1), ('RepaymentsOfBonds', 2), ('RedemptionOfBondsPayable', 3),
        ('BondRepayments', 4), ('RepurchaseOfBonds', 5),
    ],
    'proceeds_issuance_stock_cf': [
        ('ProceedsFromIssuanceOfStockFinCF', 1), ('ProceedsFromIssuanceOfShares', 2),
        ('ProceedsFromIssuanceOfCommonStock', 3), ('StockIssuanceProceeds', 4), ('ProceedsFromEquityIssuance', 5),
    ],
    'purchase_treasury_stock_cf': [
        ('PurchaseOfTreasuryStockFinCF', 1), ('PurchaseOfTreasuryShares', 2),
        ('PaymentsForAcquisitionOfTreasuryShares', 3), ('RepurchaseOfCommonStock', 4),
        ('TreasuryStockPurchase', 5), ('ShareBuyback', 6),
    ],
    'disposal_treasury_stock_cf': [
        ('ProceedsFromSalesOfTreasuryStockFinCF', 1), ('ProceedsFromDisposalOfTreasuryShares', 2),
        ('SaleOfTreasuryStock', 3), ('ProceedsFromTreasuryStockSale', 4), ('TreasuryStockDisposal', 5),
    ],
    'dividends_paid': [
        ('CashDividendsPaidFinCF', 1), ('DividendsPaidToOwnersOfParent', 2), ('DividendsPaid', 3),
        ('PaymentOfDividends', 4), ('CashDividendsPaid', 5), ('DividendsPayments', 6),
    ],
    'dividends_paid_nci': [
        ('CashDividendsPaidToMinorityShareholdersFinCF', 1),
        ('DividendsPaidToNonControllingInterests', 2), ('DividendsToMinorityInterests', 3),
        ('NonControllingInterestDividends', 4), ('MinorityDividendsPaid', 5),
    ],
    'repayments_lease_obligations_cf': [
        ('RepaymentsOfLeaseObligationsFinCF', 1), ('PaymentsOfLeaseLiabilities', 2),
        ('RepaymentOfLeaseLiabilities', 3), ('LeasePayments', 4), ('FinanceLeasePayments', 5),
    ],
    'other_financing_cf': [
        ('OtherFinCF', 1), ('OtherFinancingCashFlow', 2), ('OtherFinancingActivities', 3),
        ('NetOtherFinancingActivities', 4), ('MiscellaneousFinancingActivities', 5),
    ],
    'effect_exchange_rate_cf': [
        ('EffectOfExchangeRateChangeOnCashAndCashEquivalents', 1),
        ('EffectOfExchangeRateChangesOnCashAndCashEquivalents', 2),
        ('ForeignExchangeEffect', 3), ('ExchangeRateEffect', 4),
        ('EffectOfForeignExchangeRates', 5), ('TranslationAdjustment', 6),
    ],
    'net_increase_decrease_cash_cf': [
        ('NetIncreaseDecreaseInCashAndCashEquivalents', 1),
        ('IncreaseDecreaseInCashAndCashEquivalents', 2), ('NetChangeInCashAndCashEquivalents', 3),
        ('ChangeInCashAndCashEquivalents', 4), ('NetCashFlow', 5), ('TotalCashFlow', 6),
    ],
    'cash_beginning_cf': [
        ('CashAndCashEquivalentsAtBeginningOfPeriod', 1), ('CashAndCashEquivalentsAtBeginningOfYear', 2),
        ('CashAndCashEquivalentsBeginningOfPeriod', 3), ('BeginningCashAndCashEquivalents', 4),
        ('CashAtBeginningOfPeriod', 5), ('OpeningCashBalance', 6),
    ],
    'cash_ending_cf': [
        ('CashAndCashEquivalentsAtEndOfPeriod', 1), ('CashAndCashEquivalentsAtEndOfYear', 2),
        ('CashAndCashEquivalents', 3), ('CashAndCashEquivalentsEndOfPeriod', 4),
        ('EndingCashAndCashEquivalents', 5), ('CashAtEndOfPeriod', 6), ('ClosingCashBalance', 7),
    ],
    'increase_cash_from_merger_cf': [
        ('IncreaseDecreaseInCashAndCashEquivalentsResultingFromMerger', 1),
        ('CashAcquiredThroughMerger', 2), ('CashFromBusinessCombinations', 3),
        ('CashAcquiredInMergers', 4), ('MergerCashEffect', 5),
    ],
    'increase_cash_from_consolidation_cf': [
        ('IncreaseDecreaseInCashAndCashEquivalentsResultingFromChangeOfScopeOfConsolidation', 1),
        ('EffectOfChangesInConsolidationScope', 2), ('CashFromNewlyConsolidatedSubsidiaries', 3),
        ('ConsolidationScopeChange', 4), ('ChangeInScopeOfConsolidation', 5),
    ],
    'free_cash_flow': [
        ('FreeCashFlow', 1), ('FCF', 2), ('FreeCashFlowToFirm', 3),
        ('OperatingCashFlowLessCapex', 4), ('CashFlowAfterCapex', 5),
    ],

    # ==================== 従業員 ====================
    'employee_count': [
        ('NumberOfEmployees', 1), ('NumberOfEmployeesIFRS', 2),
    ],
    'employee_count_consolidated': [
        ('NumberOfEmployeesConsolidatedMember', 1), ('ConsolidatedNumberOfEmployees', 2),
    ],
    'employee_count_non_consolidated': [
        ('NumberOfEmployeesNonConsolidatedMember', 1), ('NonConsolidatedNumberOfEmployees', 2),
    ],
    'temporary_employee_count': [
        ('AverageNumberOfTemporaryWorkers', 1), ('NumberOfTemporaryEmployees', 2), ('TemporaryEmployees', 3),
    ],
    'average_age': [
        ('AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees', 1),
        ('AverageAgeYears', 2), ('AverageAge', 3), ('AverageAgeOfEmployees', 4),
    ],
    'average_tenure': [
        ('AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees', 1),
        ('AverageLengthOfServiceYears', 2), ('AverageYearsOfService', 3), ('AverageTenure', 4),
    ],
    'average_salary': [
        ('AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees', 1),
        ('AverageAnnualSalary', 2), ('AverageSalary', 3), ('AverageWage', 4),
    ],
    'union_members': [
        ('NumberOfUnionMembers', 1), ('LaborUnionMembers', 2),
    ],

    # ==================== 役員 ====================
    'executive_compensation_total': [
        ('TotalCompensationPaidToDirectorsAndAuditors', 1), ('TotalRemunerationToDirectors', 2),
        ('TotalExecutiveCompensation', 3),
    ],
    'directors_compensation': [
        ('CompensationPaidToDirectors', 1), ('DirectorsCompensation', 2), ('RemunerationToDirectors', 3),
    ],
    'auditors_compensation': [
        ('CompensationPaidToCorporateAuditors', 1), ('AuditorsCompensation', 2), ('RemunerationToAuditors', 3),
    ],
    'outside_directors_compensation': [
        ('CompensationPaidToOutsideDirectors', 1), ('OutsideDirectorsCompensation', 2),
    ],
    'number_of_directors': [
        ('NumberOfDirectors', 1), ('DirectorsCount', 2),
    ],
    'number_of_auditors': [
        ('NumberOfCorporateAuditors', 1), ('AuditorsCount', 2),
    ],
    'number_of_outside_directors': [
        ('NumberOfOutsideDirectors', 1), ('OutsideDirectorsCount', 2),
    ],
    'number_of_outside_auditors': [
        ('NumberOfOutsideCorporateAuditors', 1),
    ],
    'number_of_independent_directors': [
        ('NumberOfIndependentDirectors', 1),
    ],
    'stock_based_compensation': [
        ('StockBasedCompensationExpense', 1), ('ShareBasedPaymentExpense', 2), ('StockOptionExpense', 3),
    ],

    # ==================== 株式 ====================
    'total_shares_issued': [
        ('TotalNumberOfIssuedShares', 1), ('IssuedShares', 2), ('SharesIssued', 3), ('NumberOfSharesIssued', 4),
    ],
    'total_shares_authorized': [
        ('TotalNumberOfAuthorizedShares', 1), ('AuthorizedShares', 2), ('AuthorizedCapitalStock', 3),
    ],
    'treasury_shares_count': [
        ('NumberOfTreasuryStock', 1), ('TreasurySharesNumber', 2), ('NumberOfTreasuryShares', 3),
    ],
    'shares_outstanding': [
        ('NumberOfSharesOutstanding', 1), ('SharesOutstanding', 2),
    ],
    'voting_rights': [
        ('TotalNumberOfVotingRights', 1), ('VotingRightsTotal', 2),
    ],
    'foreign_ownership_ratio': [
        ('ForeignShareholdingRatio', 1), ('RatioOfShareholdingByForeigners', 2), ('ForeignOwnershipPercentage', 3),
    ],
    'individual_ownership_ratio': [
        ('IndividualShareholdingRatio', 1), ('RatioOfShareholdingByIndividuals', 2),
    ],
    'financial_institution_ownership': [
        ('FinancialInstitutionShareholdingRatio', 1),
    ],
    'stock_price_high': [
        ('HighestStockPrice', 1), ('HighPriceOfStock', 2),
    ],
    'stock_price_low': [
        ('LowestStockPrice', 1), ('LowPriceOfStock', 2),
    ],

    # ==================== 企業情報 ====================
    'consolidated_subsidiaries_count': [
        ('NumberOfConsolidatedSubsidiaries', 1), ('ConsolidatedSubsidiariesCount', 2), ('NumberOfSubsidiaries', 3),
    ],
    'equity_method_affiliates_count': [
        ('NumberOfAffiliatesAccountedForByEquityMethod', 1),
        ('EquityMethodAffiliatesCount', 2), ('NumberOfEquityMethodAffiliates', 3),
    ],
    'domestic_subsidiaries_count': [
        ('NumberOfDomesticConsolidatedSubsidiaries', 1),
    ],
    'overseas_subsidiaries_count': [
        ('NumberOfOverseasConsolidatedSubsidiaries', 1), ('ForeignSubsidiariesCount', 2),
    ],
    'employees_overseas_ratio': [
        ('RatioOfOverseasEmployees', 1),
    ],
    'overseas_sales_ratio': [
        ('RatioOfOverseasSales', 1), ('OverseasSalesRatio', 2),
    ],

    # ==================== 監査 ====================
    'audit_fee': [
        ('AuditFee', 1), ('RemunerationForAuditCertification', 2),
        ('AuditFeesForCertification', 3), ('FeesForAuditServices', 4),
    ],
    'non_audit_fee': [
        ('NonAuditFee', 1), ('RemunerationForNonAuditServices', 2), ('FeesForNonAuditServices', 3),
    ],
    'audit_fee_parent': [
        ('AuditFeeToSubmittingCompany', 1),
    ],
    'audit_fee_consolidated': [
        ('AuditFeeConsolidated', 1),
    ],
    'total_audit_fee': [
        ('TotalAuditFee', 1),
    ],

    # ==================== セグメント ====================
    'segment_count': [
        ('NumberOfReportableSegments', 1), ('NumberOfOperatingSegments', 2),
    ],
    'japan_sales': [
        ('SalesRevenueJapan', 1), ('DomesticSales', 2), ('JapanSegmentSales', 3),
    ],
    'overseas_sales': [
        ('SalesRevenueOverseas', 1), ('OverseasSales', 2), ('ForeignSales', 3),
    ],
    'north_america_sales': [
        ('SalesRevenueNorthAmerica', 1), ('NorthAmericaSales', 2),
    ],
    'europe_sales': [
        ('SalesRevenueEurope', 1), ('EuropeSales', 2),
    ],
    'asia_sales': [
        ('SalesRevenueAsia', 1), ('AsiaSales', 2),
    ],
    'china_sales': [
        ('SalesRevenueChina', 1), ('ChinaSales', 2),
    ],

    # ==================== 指標 ====================
    'ebitda': [
        ('EBITDA', 1), ('EarningsBeforeInterestTaxesDepreciationAndAmortization', 2),
    ],
    'ebit': [
        ('EBIT', 1), ('EarningsBeforeInterestAndTaxes', 2),
    ],
    'gross_margin': [
        ('GrossMarginRatio', 1), ('GrossProfitMargin', 2),
    ],
    'operating_margin': [
        ('OperatingMarginRatio', 1), ('OperatingProfitMargin', 2),
    ],
    'net_margin': [
        ('NetProfitMarginRatio', 1), ('NetIncomeMargin', 2),
    ],
    'roe': [
        ('ReturnOnEquity', 1), ('ROE', 2),
    ],
    'roa': [
        ('ReturnOnAssets', 1), ('ROA', 2),
    ],
    'roic': [
        ('ReturnOnInvestedCapital', 1), ('ROIC', 2),
    ],
    'asset_turnover': [
        ('TotalAssetsTurnover', 1), ('AssetTurnoverRatio', 2),
    ],
    'inventory_turnover': [
        ('InventoryTurnover', 1), ('InventoryTurnoverRatio', 2),
    ],
    'receivables_turnover': [
        ('AccountsReceivableTurnover', 1), ('ReceivablesTurnoverRatio', 2),
    ],
    'debt_equity_ratio': [
        ('DebtEquityRatio', 1), ('DebtToEquityRatio', 2),
    ],
    'interest_coverage_ratio': [
        ('InterestCoverageRatio', 1), ('TimesInterestEarned', 2),
    ],
    'payout_ratio': [
        ('DividendPayoutRatio', 1), ('PayoutRatio', 2),
    ],

    # ==================== 設備投資 ====================
    'capital_expenditure': [
        ('CapitalExpenditures', 1), ('CapitalExpenditure', 2),
        ('PaymentsForPurchaseOfPropertyPlantAndEquipment', 3), ('AdditionsToPropertyPlantAndEquipment', 4),
    ],
    'planned_capital_expenditure': [
        ('PlannedCapitalExpenditure', 1), ('CapitalExpenditurePlan', 2),
    ],
    'depreciation_total': [
        ('TotalDepreciationAndAmortization', 1), ('DepreciationAndAmortizationTotal', 2),
    ],
    'depreciation_ppe': [
        ('DepreciationPropertyPlantAndEquipment', 1), ('DepreciationOfTangibleAssets', 2),
    ],
    'amortization_intangibles': [
        ('AmortizationOfIntangibleAssets', 1), ('IntangiblesAmortization', 2),
    ],
    'rd_expenditure': [
        ('ResearchAndDevelopmentExpenditure', 1), ('RAndDExpenses', 2), ('ResearchAndDevelopmentCosts', 3),
    ],

    # ==================== 会社名 ====================
    'company_name': [
        ('CompanyNameCoverPage', 1), ('FilerNameInJapaneseDEI', 2),
    ],
}

# B/S項目（instant）
INSTANT_ITEMS = {
    'cash_and_deposits', 'notes_accounts_receivable', 'accounts_receivable', 'notes_receivable',
    'securities', 'merchandise_finished_goods', 'work_in_process', 'raw_materials', 'supplies',
    'inventories', 'advance_payments', 'short_term_loans_receivable', 'accrued_income',
    'deferred_tax_assets_current', 'other_current_assets', 'allowance_doubtful_accounts_current',
    'property_plant_equipment', 'buildings_structures', 'machinery_equipment', 'tools_furniture_fixtures',
    'land', 'leased_assets', 'construction_in_progress', 'accumulated_depreciation',
    'intangible_assets', 'goodwill', 'software', 'customer_related_assets', 'technology_based_assets',
    'trademarks', 'patents', 'rights', 'leasehold_rights', 'investments_other_assets',
    'investment_securities', 'shares_of_subsidiaries_associates', 'long_term_loans_receivable',
    'long_term_prepaid_expenses', 'deferred_tax_assets_noncurrent', 'net_defined_benefit_asset',
    'guarantee_deposits', 'membership', 'other_investments', 'allowance_doubtful_accounts_noncurrent',
    'notes_accounts_payable', 'accounts_payable', 'notes_payable', 'short_term_borrowings',
    'current_portion_long_term_debt', 'commercial_papers', 'bonds_payable_current', 'income_taxes_payable',
    'accrued_expenses', 'accrued_bonuses', 'advances_received', 'deposits_received',
    'provision_for_bonuses', 'provision_product_warranties', 'other_current_liabilities',
    'bonds_payable', 'long_term_borrowings', 'deferred_tax_liabilities', 'net_defined_benefit_liability',
    'provision_directors_retirement', 'provision_environment', 'asset_retirement_obligations',
    'long_term_accounts_payable', 'other_noncurrent_liabilities', 'shareholders_equity',
    'capital_stock', 'capital_surplus', 'legal_capital_surplus', 'other_capital_surplus',
    'retained_earnings', 'legal_retained_earnings', 'other_retained_earnings', 'treasury_stock',
    'accumulated_other_comprehensive_income', 'valuation_difference_securities', 'deferred_gains_hedges',
    'land_revaluation_difference', 'foreign_currency_translation_adjustment',
    'remeasurement_defined_benefit_plans', 'subscription_rights', 'non_controlling_interests',
    'current_assets', 'non_current_assets', 'total_assets', 'current_liabilities',
    'non_current_liabilities', 'total_liabilities', 'total_equity', 'total_liabilities_and_equity',
    'employee_count', 'employee_count_consolidated', 'employee_count_non_consolidated',
    'temporary_employee_count', 'bps', 'total_shares_issued', 'total_shares_authorized',
    'treasury_shares_count', 'shares_outstanding', 'voting_rights',
    'consolidated_subsidiaries_count', 'equity_method_affiliates_count',
    'domestic_subsidiaries_count', 'overseas_subsidiaries_count',
    'number_of_directors', 'number_of_auditors', 'number_of_outside_directors',
    'number_of_outside_auditors', 'number_of_independent_directors', 'segment_count',
}

# B/S項目（instant）
INSTANT_ITEMS = {
    'cash_and_deposits', 'notes_accounts_receivable', 'accounts_receivable', 'notes_receivable',
    'securities', 'merchandise_finished_goods', 'work_in_process', 'raw_materials', 'supplies',
    'inventories', 'advance_payments', 'short_term_loans_receivable', 'accrued_income',
    'deferred_tax_assets_current', 'other_current_assets', 'allowance_doubtful_accounts_current',
    'property_plant_equipment', 'buildings_structures', 'machinery_equipment', 'tools_furniture_fixtures',
    'land', 'leased_assets', 'construction_in_progress', 'accumulated_depreciation',
    'intangible_assets', 'goodwill', 'software', 'customer_related_assets', 'technology_based_assets',
    'trademarks', 'patents', 'rights', 'leasehold_rights', 'investments_other_assets',
    'investment_securities', 'shares_of_subsidiaries_associates', 'long_term_loans_receivable',
    'long_term_prepaid_expenses', 'deferred_tax_assets_noncurrent', 'net_defined_benefit_asset',
    'guarantee_deposits', 'membership', 'other_investments', 'allowance_doubtful_accounts_noncurrent',
    'notes_accounts_payable', 'accounts_payable', 'notes_payable', 'short_term_borrowings',
    'current_portion_long_term_debt', 'commercial_papers', 'bonds_payable_current', 'income_taxes_payable',
    'accrued_expenses', 'accrued_bonuses', 'advances_received', 'deposits_received',
    'provision_for_bonuses', 'provision_product_warranties', 'other_current_liabilities',
    'bonds_payable', 'long_term_borrowings', 'deferred_tax_liabilities', 'net_defined_benefit_liability',
    'provision_directors_retirement', 'provision_environment', 'asset_retirement_obligations',
    'long_term_accounts_payable', 'other_noncurrent_liabilities', 'shareholders_equity',
    'capital_stock', 'capital_surplus', 'legal_capital_surplus', 'other_capital_surplus',
    'retained_earnings', 'legal_retained_earnings', 'other_retained_earnings', 'treasury_stock',
    'accumulated_other_comprehensive_income', 'valuation_difference_securities', 'deferred_gains_hedges',
    'land_revaluation_difference', 'foreign_currency_translation_adjustment',
    'remeasurement_defined_benefit_plans', 'subscription_rights', 'non_controlling_interests',
    'current_assets', 'non_current_assets', 'total_assets', 'current_liabilities',
    'non_current_liabilities', 'total_liabilities', 'total_equity', 'total_liabilities_and_equity',
    'employee_count', 'employee_count_consolidated', 'employee_count_non_consolidated',
    'temporary_employee_count', 'bps', 'total_shares_issued', 'total_shares_authorized',
    'treasury_shares_count', 'shares_outstanding', 'voting_rights',
    'consolidated_subsidiaries_count', 'equity_method_affiliates_count',
    'domestic_subsidiaries_count', 'overseas_subsidiaries_count',
    'number_of_directors', 'number_of_auditors', 'number_of_outside_directors',
    'number_of_outside_auditors', 'number_of_independent_directors', 'segment_count',
}


# ============================================================
# XBRL解析
# ============================================================
def extract_xbrl_from_zip(zip_path: Path) -> Dict[str, Any]:
    print(f"  📦 XBRL ZIP読み込み中: {zip_path.name}")
    
    xbrl_content = None
    xbrl_filename = None
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('.xbrl'):
                    if 'PublicDoc' in name or 'public' in name.lower():
                        content = zf.read(name)
                        if xbrl_content is None or len(content) > len(xbrl_content):
                            xbrl_content = content
                            xbrl_filename = name
            
            if xbrl_content is None:
                for name in zf.namelist():
                    if name.endswith('.xbrl'):
                        content = zf.read(name)
                        if xbrl_content is None or len(content) > len(xbrl_content):
                            xbrl_content = content
                            xbrl_filename = name
    except Exception as e:
        print(f"  ❌ ZIP読み込みエラー: {e}")
        return {}
    
    if xbrl_content is None:
        print(f"  ❌ XBRLファイルが見つかりません")
        return {}
    
    print(f"  📄 解析中: {xbrl_filename}")
    
    soup = None
    for encoding in ['utf-8', 'shift_jis', 'cp932', 'euc-jp']:
        try:
            soup = BeautifulSoup(xbrl_content.decode(encoding), 'html.parser')
            break
        except:
            continue
    
    if soup is None:
        try:
            soup = BeautifulSoup(xbrl_content, 'html.parser')
        except Exception as e:
            print(f"  ❌ XMLパースエラー: {e}")
            return {}
    
    extracted = {}
    all_elements = soup.find_all(True)
    
    duration_patterns = ['CurrentYearDuration', 'CurrentYearDuration_ConsolidatedMember']
    instant_patterns = ['CurrentYearInstant', 'CurrentYearInstant_ConsolidatedMember']
    
    for metric_name, tag_list in XBRL_TAG_DEFINITIONS.items():
        context_patterns = instant_patterns if metric_name in INSTANT_ITEMS else duration_patterns
        
        best_value = None
        best_priority = 999
        
        for tag_name, priority in tag_list:
            for elem in all_elements:
                elem_name = elem.name or ''
                
                if tag_name.lower() not in elem_name.lower():
                    continue
                
                context_ref = elem.get('contextref', '')
                
                context_match = False
                for pattern in context_patterns:
                    if pattern in context_ref:
                        context_match = True
                        break
                
                if not context_match:
                    continue
                
                if 'NonConsolidated' in context_ref:
                    continue
                
                value_text = elem.get_text(strip=True)
                if not value_text:
                    continue
                
                try:
                    value = float(value_text.replace(',', ''))
                    if priority < best_priority:
                        best_value = value
                        best_priority = priority
                except ValueError:
                    if metric_name == 'company_name':
                        best_value = value_text
                        break
        
        if best_value is not None:
            extracted[metric_name] = best_value
    
    # 計算指標
    if 'net_income' in extracted and 'total_equity' in extracted and extracted['total_equity'] != 0:
        extracted['roe_calc'] = round((extracted['net_income'] / extracted['total_equity']) * 100, 2)
    
    if 'net_income' in extracted and 'total_assets' in extracted and extracted['total_assets'] != 0:
        extracted['roa_calc'] = round((extracted['net_income'] / extracted['total_assets']) * 100, 2)
    
    if 'total_equity' in extracted and 'total_assets' in extracted and extracted['total_assets'] != 0:
        extracted['equity_ratio_calc'] = round((extracted['total_equity'] / extracted['total_assets']) * 100, 2)
    
    if 'operating_income' in extracted and 'revenue' in extracted and extracted['revenue'] != 0:
        extracted['operating_margin_calc'] = round((extracted['operating_income'] / extracted['revenue']) * 100, 2)
    
    if 'gross_profit' in extracted and 'revenue' in extracted and extracted['revenue'] != 0:
        extracted['gross_margin_calc'] = round((extracted['gross_profit'] / extracted['revenue']) * 100, 2)
    
    if 'net_income' in extracted and 'revenue' in extracted and extracted['revenue'] != 0:
        extracted['net_margin_calc'] = round((extracted['net_income'] / extracted['revenue']) * 100, 2)
    
    if 'operating_cf' in extracted and 'purchase_ppe_cf' in extracted:
        extracted['free_cash_flow_calc'] = extracted['operating_cf'] + extracted['purchase_ppe_cf']
    
    print(f"  ✅ 抽出完了: {len(extracted)}項目")
    
    return extracted


# ============================================================
# PDF処理
# ============================================================
def extract_text_from_pdf(pdf_path: Path) -> dict:
    print(f"  📄 PDF読み込み中: {pdf_path.name}")
    
    if not HAS_PDFPLUMBER:
        print("  ❌ pdfplumberがインストールされていません")
        return None
    
    pages = []
    total_chars = 0
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            print(f"  📑 総ページ数: {len(pdf.pages)}")
            
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                text = re.sub(r'[ \t]+', ' ', text)
                text = re.sub(r'\n{3,}', '\n\n', text)
                text = text.strip()
                
                if text:
                    pages.append({'page_num': i + 1, 'text': text, 'char_count': len(text)})
                    total_chars += len(text)
    except Exception as e:
        print(f"  ❌ PDF読み込みエラー: {e}")
        return None
    
    print(f"  ✅ 抽出完了: {len(pages)}ページ, {total_chars:,}文字")
    return {'file_name': pdf_path.name, 'total_pages': len(pages), 'total_chars': total_chars, 'pages': pages}


def split_into_chunks(pdf_data: dict, chunk_size: int = 2000, overlap: int = 150) -> list:
    print(f"  📦 チャンク分割中 (サイズ: {chunk_size}トークン)")
    
    full_text = "\n\n".join([p['text'] for p in pdf_data['pages']])
    paragraphs = re.split(r'\n{2,}', full_text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    chunk_id = 0
    
    for para in paragraphs:
        para_tokens = count_tokens(para)
        
        if current_tokens + para_tokens > chunk_size and current_chunk:
            chunks.append({'chunk_id': chunk_id, 'text': current_chunk.strip(), 'token_count': current_tokens})
            chunk_id += 1
            overlap_text = current_chunk[-overlap*3:] if len(current_chunk) > overlap*3 else ""
            current_chunk = overlap_text + "\n\n" + para
            current_tokens = count_tokens(current_chunk)
        else:
            current_chunk += "\n\n" + para
            current_tokens += para_tokens
    
    if current_chunk.strip():
        chunks.append({'chunk_id': chunk_id, 'text': current_chunk.strip(), 'token_count': count_tokens(current_chunk)})
    
    print(f"  ✅ {len(chunks)}チャンクに分割完了")
    return chunks


# ============================================================
# LLM呼び出し
# ============================================================
def call_ollama(prompt: str, model: str = None, num_predict: int = None) -> dict:
    model = model or Config.OLLAMA_MODEL
    num_predict = num_predict or Config.NUM_PREDICT
    url = f"{Config.OLLAMA_BASE_URL}/api/generate"
    
    payload = {
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": Config.TEMPERATURE, "num_predict": num_predict, "num_ctx": Config.NUM_CTX}
    }
    
    try:
        response = requests.post(url, json=payload, timeout=600)  # タイムアウト延長
        response.raise_for_status()
        result = response.json()
        return {'response': result.get('response', ''), 'input_tokens': result.get('prompt_eval_count', 0), 'output_tokens': result.get('eval_count', 0)}
    except Exception as e:
        return {'response': f'エラー: {str(e)}', 'input_tokens': 0, 'output_tokens': 0}


def extract_first_json(text: str) -> Optional[str]:
    if not text:
        return None
    text = re.sub(r'```(?:json)?', '', text, flags=re.IGNORECASE).replace('```', '')
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return None


# ============================================================
# PORTA Phase 1
# ============================================================
FINANCIAL_SECTIONS = [
    "表紙・目次", "企業の概況", "事業の状況", "経営成績の分析", "財政状態の分析",
    "キャッシュ・フローの状況", "設備の状況", "提出会社の状況", "経理の状況",
    "連結財務諸表", "個別財務諸表", "株式の状況", "配当政策", "コーポレートガバナンス",
    "事業等のリスク", "経営上の重要な契約", "研究開発活動", "その他",
]


def analyze_chunk_qualitative(chunk: dict, xbrl_brief: str, model: str = None) -> dict:
    model = model or Config.OLLAMA_MODEL
    
    prompt = f"""あなたは日本企業の有価証券報告書を分析する専門家です。
以下のテキストを分析し、JSON形式で回答してください。

【重要】数値は生成しないでください。定性的な情報のみ抽出してください。

【タスク】
1. このテキストが属するセクションを特定
2. 60-110文字の日本語要約を作成（数値なし）
3. 増減要因、リスク、見通しを抽出（数値なし）

【セクション選択肢】
{', '.join(FINANCIAL_SECTIONS)}

【テキスト】
{chunk['text'][:2200]}

【回答形式 - JSON のみ】
{{"section": "セクション名", "summary": "要約", "drivers": ["要因1", "要因2"], "risks": ["リスク1", "リスク2"], "outlook": ["見通し1", "見通し2"], "importance": "high/medium/low"}}

JSON:"""

    result = call_ollama(prompt, model, num_predict=600)
    
    js = extract_first_json(result['response'])
    if js:
        try:
            parsed = json.loads(js)
            return {
                'section': parsed.get('section', 'その他'), 'summary': parsed.get('summary', ''),
                'drivers': parsed.get('drivers') or [], 'risks': parsed.get('risks') or [],
                'outlook': parsed.get('outlook') or [], 'importance': parsed.get('importance', 'medium'),
            }
        except:
            pass
    
    return {'section': 'その他', 'summary': chunk['text'][:90] + '...', 'drivers': [], 'risks': [], 'outlook': [], 'importance': 'medium'}


def process_chunk_wrapper(args):
    chunk, xbrl_brief, model, idx, total = args
    result = analyze_chunk_qualitative(chunk, xbrl_brief, model)
    chunk.update(result)
    return idx, chunk


# ============================================================
# PORTA Phase 2
# ============================================================
def generate_section_summary(section_name: str, chunks: list, model: str = None) -> dict:
    model = model or Config.OLLAMA_MODEL
    
    combined = "\n".join([f"- {c.get('summary', '')}" for c in chunks if c.get('summary')][:100])  # 増加
    
    drivers, risks, outlook = [], [], []
    for c in chunks:
        drivers.extend(c.get('drivers') or [])
        risks.extend(c.get('risks') or [])
        outlook.extend(c.get('outlook') or [])
    
    def uniq(xs):
        seen = set()
        return [x for x in xs if x and x.strip() and x not in seen and not seen.add(x)][:15]  # 増加
    
    drivers, risks, outlook = uniq(drivers), uniq(risks), uniq(outlook)
    
    prompt = f"""あなたは日本企業の有価証券報告書を分析する専門家です。
「{section_name}」セクションの要約を統合してください。

【重要】数値は生成しないでください。

【要約一覧】
{combined[:4000]}

【抽出された増減要因】
{chr(10).join(f'- {d}' for d in drivers) if drivers else 'なし'}

【抽出されたリスク】
{chr(10).join(f'- {r}' for r in risks) if risks else 'なし'}

【抽出された見通し】
{chr(10).join(f'- {o}' for o in outlook) if outlook else 'なし'}

【回答形式 - JSON のみ、日本語で】
{{"summary": "統合要約（200-300字）", "key_points": ["ポイント1", "ポイント2", "ポイント3", "ポイント4", "ポイント5"], "investment_insight": "投資判断への示唆（100字以上）"}}

JSON:"""

    result = call_ollama(prompt, model, num_predict=800)  # 増加
    
    js = extract_first_json(result['response'])
    if js:
        try:
            p = json.loads(js)
            return {
                'section': section_name,
                'summary': p.get('summary', ''),
                'key_points': p.get('key_points') or [],
                'investment_insight': p.get('investment_insight', ''),
                'drivers': drivers,
                'risks': risks,
                'outlook': outlook,
                'chunk_count': len(chunks)
            }
        except:
            pass
    
    return {'section': section_name, 'summary': combined[:300], 'key_points': [], 'investment_insight': '', 'drivers': drivers, 'risks': risks, 'outlook': outlook, 'chunk_count': len(chunks)}


# ============================================================
# XBRL全項目フォーマット（改善版）
# ============================================================
def format_xbrl_full(xbrl: Dict[str, Any]) -> str:
    """全XBRL項目を構造化して出力"""
    
    # カテゴリ別にグループ化
    categories = {
        'P/L（損益計算書）': [
            ('revenue', '売上高'), ('cost_of_sales', '売上原価'), ('gross_profit', '売上総利益'),
            ('selling_general_admin', '販管費'), ('operating_income', '営業利益'),
            ('ordinary_income', '経常利益'), ('income_before_taxes', '税引前利益'),
            ('income_taxes', '法人税等'), ('net_income', '当期純利益'),
            ('net_income_non_controlling', '非支配株主利益'), ('comprehensive_income', '包括利益'),
            ('eps_basic', '1株当たり利益'), ('eps_diluted', '希薄化後EPS'),
        ],
        'B/S（貸借対照表）- 資産': [
            ('current_assets', '流動資産合計'), ('cash_and_deposits', '現金預金'),
            ('notes_accounts_receivable', '売上債権'), ('inventories', '棚卸資産'),
            ('non_current_assets', '固定資産合計'), ('property_plant_equipment', '有形固定資産'),
            ('intangible_assets', '無形固定資産'), ('goodwill', 'のれん'),
            ('investment_securities', '投資有価証券'), ('total_assets', '総資産'),
        ],
        'B/S（貸借対照表）- 負債・純資産': [
            ('current_liabilities', '流動負債合計'), ('short_term_borrowings', '短期借入金'),
            ('non_current_liabilities', '固定負債合計'), ('long_term_borrowings', '長期借入金'),
            ('bonds_payable', '社債'), ('total_liabilities', '負債合計'),
            ('shareholders_equity', '株主資本'), ('capital_stock', '資本金'),
            ('retained_earnings', '利益剰余金'), ('treasury_stock', '自己株式'),
            ('total_equity', '純資産合計'),
        ],
        'C/F（キャッシュフロー）': [
            ('operating_cf', '営業CF'), ('investing_cf', '投資CF'), ('financing_cf', '財務CF'),
            ('depreciation', '減価償却費'), ('capital_expenditure', '設備投資'),
            ('free_cash_flow_calc', 'フリーCF'), ('cash_ending_cf', '期末現金残高'),
        ],
        '収益性指標': [
            ('roe_calc', 'ROE'), ('roa_calc', 'ROA'), ('operating_margin_calc', '営業利益率'),
            ('gross_margin_calc', '売上総利益率'), ('net_margin_calc', '純利益率'),
            ('equity_ratio_calc', '自己資本比率'),
        ],
        '株式・従業員': [
            ('employee_count', '従業員数'), ('average_salary', '平均年収'),
            ('total_shares_issued', '発行済株式数'), ('treasury_shares_count', '自己株式数'),
            ('dividend_per_share', '1株配当'), ('bps', '1株純資産'),
        ],
    }
    
    lines = []
    ratio_keys = {'roe_calc', 'roa_calc', 'equity_ratio_calc', 'operating_margin_calc', 
                  'gross_margin_calc', 'net_margin_calc', 'foreign_ownership_ratio', 'overseas_sales_ratio'}
    per_share_keys = {'eps_basic', 'eps_diluted', 'bps', 'dividend_per_share'}
    count_keys = {'employee_count', 'total_shares_issued', 'treasury_shares_count', 'shares_outstanding',
                  'number_of_directors', 'consolidated_subsidiaries_count'}
    
    for category, items in categories.items():
        category_lines = []
        for key, label in items:
            val = xbrl.get(key)
            if val is None:
                continue
            try:
                fv = float(val)
                if key in ratio_keys:
                    category_lines.append(f"  - {label}: {fv:.2f}%")
                elif key in per_share_keys:
                    category_lines.append(f"  - {label}: {fv:.2f}円")
                elif key in count_keys:
                    category_lines.append(f"  - {label}: {fv:,.0f}")
                else:
                    val_million = fv / 1_000_000
                    if abs(val_million) >= 10000:
                        category_lines.append(f"  - {label}: {val_million/100:.1f}億円")
                    elif abs(val_million) >= 1:
                        category_lines.append(f"  - {label}: {val_million:,.0f}百万円")
                    else:
                        category_lines.append(f"  - {label}: {fv:,.0f}円")
            except:
                pass
        
        if category_lines:
            lines.append(f"\n【{category}】")
            lines.extend(category_lines)
    
    return "\n".join(lines) if lines else "（データなし）"


def format_xbrl_brief(xbrl: Dict[str, Any]) -> str:
    """主要指標のみ"""
    items = [
        ('revenue', '売上高'), ('operating_income', '営業利益'), ('net_income', '純利益'),
        ('total_assets', '総資産'), ('total_equity', '純資産'),
        ('operating_cf', '営業CF'), ('investing_cf', '投資CF'), ('financing_cf', '財務CF'),
        ('roe_calc', 'ROE'), ('roa_calc', 'ROA'), ('equity_ratio_calc', '自己資本比率'),
    ]
    
    lines = []
    ratio_keys = {'roe_calc', 'roa_calc', 'equity_ratio_calc', 'operating_margin_calc'}
    
    for key, label in items:
        val = xbrl.get(key)
        if val is None:
            continue
        try:
            fv = float(val)
            if key in ratio_keys:
                lines.append(f"- {label}: {fv:.2f}%")
            else:
                val_million = fv / 1_000_000
                if abs(val_million) >= 10000:
                    lines.append(f"- {label}: {val_million/100:.1f}億円")
                else:
                    lines.append(f"- {label}: {val_million:,.0f}百万円")
        except:
            pass
    
    return "\n".join(lines) if lines else "（データなし）"


# ============================================================
# PORTA Phase 3 - 詳細レポート生成（改善版）
# ============================================================
def generate_final_report(company_name: str, xbrl: Dict[str, Any], section_summaries: List[Dict], model: str = None) -> str:
    model = model or Config.OLLAMA_MODEL
    
    # 全XBRL項目
    xbrl_full = format_xbrl_full(xbrl)
    
    # 全セクション（制限なし）
    section_text = "\n\n".join([
        f"### {s['section']}（{s.get('chunk_count', 0)}チャンク）\n{s.get('summary', '')}\n主要ポイント: {', '.join(s.get('key_points', []))}\n投資示唆: {s.get('investment_insight', '')}"
        for s in section_summaries
    ])
    
    # 全リスクと見通しを集約
    all_risks = []
    all_outlook = []
    all_drivers = []
    for s in section_summaries:
        all_risks.extend(s.get('risks', []))
        all_outlook.extend(s.get('outlook', []))
        all_drivers.extend(s.get('drivers', []))
    
    def uniq(xs):
        seen = set()
        return [x for x in xs if x and x.strip() and x not in seen and not seen.add(x)][:20]
    
    all_risks = uniq(all_risks)
    all_outlook = uniq(all_outlook)
    all_drivers = uniq(all_drivers)
    
    prompt = f"""あなたは機関投資家向けの詳細な投資分析レポートを作成する専門アナリストです。
以下の情報を基に、{company_name}の包括的な投資判断レポートを日本語で作成してください。

【重要な指示】
1. 必ず日本語で記述してください
2. XBRLの数値は正確に引用してください
3. 各セクションを詳細に記述してください（合計5000字以上を目標）
4. 定性情報と定量情報を組み合わせて分析してください
5. 投資家の意思決定に役立つ具体的な示唆を含めてください

【XBRL財務データ（{len(xbrl)}項目抽出済み）】
{xbrl_full}

【有価証券報告書分析（{len(section_summaries)}セクション）】
{section_text[:6000]}

【集約されたリスク要因】
{chr(10).join(f'- {r}' for r in all_risks) if all_risks else 'なし'}

【集約された見通し・機会】
{chr(10).join(f'- {o}' for o in all_outlook) if all_outlook else 'なし'}

【集約された増減要因】
{chr(10).join(f'- {d}' for d in all_drivers) if all_drivers else 'なし'}

【出力形式 - 以下の構成で詳細に記述】

# {company_name} 投資分析レポート

## 1. エグゼクティブサマリー（400字以上）
企業概要、業績ハイライト、投資判断の要点を簡潔にまとめる

## 2. 財務分析

### 2.1 収益性分析
売上高、営業利益、純利益の推移と利益率について詳細に分析

### 2.2 財務健全性分析
資産・負債構成、自己資本比率、有利子負債について分析

### 2.3 キャッシュフロー分析
営業CF、投資CF、財務CFの状況とフリーキャッシュフローを分析

### 2.4 投資指標
ROE、ROA、EPSなどの投資指標を分析

## 3. 事業分析

### 3.1 事業概要と強み
主要事業の概要と競争優位性

### 3.2 市場環境と競合状況
業界動向と競合他社との比較

### 3.3 成長戦略
中長期的な成長戦略と施策

## 4. リスク分析

### 4.1 事業リスク
主要な事業リスクと対応策

### 4.2 財務リスク
財務面でのリスク要因

### 4.3 外部環境リスク
規制・為替・市場環境のリスク

## 5. ガバナンス・ESG

### 5.1 コーポレートガバナンス
取締役会構成、監査体制

### 5.2 ESGへの取り組み
環境・社会・ガバナンスの取り組み

## 6. 業績見通しと投資判断

### 6.1 短期見通し（1年）
今期の業績予想と注目点

### 6.2 中長期見通し（3-5年）
中長期的な成長シナリオ

### 6.3 投資判断
総合的な投資判断と推奨

### 6.4 モニタリングポイント
今後注視すべき指標・イベント

---

レポート:"""

    result = call_ollama(prompt, model, num_predict=Config.NUM_PREDICT)
    return result.get('response', '分析生成に失敗しました')


# ============================================================
# ファイル検索
# ============================================================
def find_files(folder: Path) -> tuple:
    zips = list(folder.glob("*.zip"))
    pdfs = list(folder.glob("*.pdf"))
    zip_file = max(zips, key=lambda x: x.stat().st_size) if zips else None
    pdf_file = max(pdfs, key=lambda x: x.stat().st_size) if pdfs else None
    return zip_file, pdf_file


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 60)
    print("🌳 PORTA方式 統合分析システム v4 - 詳細レポート版")
    print(f"   タグ定義数: {len(XBRL_TAG_DEFINITIONS)}項目")
    print(f"   出力トークン: {Config.NUM_PREDICT}")
    print("=" * 60)
    
    parser = argparse.ArgumentParser(description='PORTA方式 統合分析 - 詳細版')
    parser.add_argument('--folder', '-f', required=True, help='分析フォルダ')
    parser.add_argument('--model', '-m', default='gemma2:27b', help='Ollamaモデル')
    parser.add_argument('--workers', '-w', type=int, default=4, help='並列処理数')
    parser.add_argument('--output', '-o', default=None, help='出力先')
    parser.add_argument('--num-predict', type=int, default=4000, help='出力トークン数')
    
    args = parser.parse_args()
    
    folder = Path(args.folder).expanduser()
    if not folder.exists():
        print(f"❌ フォルダが見つかりません: {folder}")
        return 1
    
    Config.OLLAMA_MODEL = args.model
    Config.MAX_WORKERS = args.workers
    Config.NUM_PREDICT = args.num_predict
    
    if 'llama2' in args.model.lower():
        Config.NUM_CTX = 4096
    elif 'gemma2' in args.model.lower() or 'qwen' in args.model.lower():
        Config.NUM_CTX = 8192
    
    output_dir = Path(args.output).expanduser() if args.output else (folder / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n📂 フォルダ: {folder}")
    print(f"📋 モデル: {Config.OLLAMA_MODEL}")
    print(f"📋 出力トークン: {Config.NUM_PREDICT}")
    print(f"📋 並列: {Config.MAX_WORKERS}")
    
    zip_file, pdf_file = find_files(folder)
    
    if not zip_file:
        print(f"\n❌ XBRLファイル（.zip）が見つかりません")
        return 1
    
    print(f"\n📁 XBRL: {zip_file.name}")
    print(f"📁 PDF:  {pdf_file.name if pdf_file else 'なし'}")
    
    # Phase 0
    print(f"\n{'='*60}")
    print("📊 Phase 0: XBRL読み込み")
    print("="*60)
    
    xbrl_flat = extract_xbrl_from_zip(zip_file)
    company_name = xbrl_flat.get('company_name', folder.name)
    
    # 主要指標表示
    print(f"\n  📈 主要指標:")
    for key, label in [('revenue', '売上高'), ('operating_income', '営業利益'), ('net_income', '純利益'),
                       ('total_assets', '総資産'), ('total_equity', '純資産'), ('roe_calc', 'ROE'), ('roa_calc', 'ROA')]:
        if key in xbrl_flat:
            val = xbrl_flat[key]
            if key in ['roe_calc', 'roa_calc', 'equity_ratio_calc']:
                print(f"    - {label}: {val:.2f}%")
            else:
                val_m = val / 1_000_000
                print(f"    - {label}: {val_m/100:.1f}億円" if val_m >= 10000 else f"    - {label}: {val_m:,.0f}百万円")
    
    # Phase 1
    print(f"\n{'='*60}")
    print("📖 Phase 1: PDF定性分析")
    print("="*60)
    
    chunks = []
    section_summaries = []
    
    if pdf_file and pdf_file.exists():
        pdf_data = extract_text_from_pdf(pdf_file)
        
        if pdf_data:
            chunks = split_into_chunks(pdf_data, Config.CHUNK_SIZE, Config.CHUNK_OVERLAP)
            
            print(f"\n🏷️ チャンク分析中 ({len(chunks)}チャンク)...")
            
            xbrl_brief = format_xbrl_brief(xbrl_flat)
            tasks = [(chunk, xbrl_brief, Config.OLLAMA_MODEL, i, len(chunks)) for i, chunk in enumerate(chunks)]
            processed_chunks = [None] * len(chunks)
            
            with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
                futures = {executor.submit(process_chunk_wrapper, task): task[3] for task in tasks}
                
                completed = 0
                for future in as_completed(futures):
                    idx, result = future.result()
                    processed_chunks[idx] = result
                    completed += 1
                    if completed % 10 == 0:
                        print(f"    ... {completed}/{len(chunks)} 完了")
            
            chunks = processed_chunks
            print(f"  ✅ チャンク分析完了")
            
            # Phase 2
            print(f"\n{'='*60}")
            print("📊 Phase 2: セクション統合")
            print("="*60)
            
            section_groups = {}
            for chunk in chunks:
                section = chunk.get('section', 'その他')
                section_groups.setdefault(section, []).append(chunk)
            
            print(f"  📁 検出セクション数: {len(section_groups)}")
            
            for section_name, section_chunks in section_groups.items():
                summary = generate_section_summary(section_name, section_chunks, Config.OLLAMA_MODEL)
                section_summaries.append(summary)
                print(f"    - {section_name}: {len(section_chunks)}チャンク")
    
    # Phase 3
    print(f"\n{'='*60}")
    print("🤖 Phase 3: 詳細レポート生成")
    print(f"   使用セクション数: {len(section_summaries)}")
    print(f"   使用XBRL項目数: {len(xbrl_flat)}")
    print("="*60)
    
    final_report = generate_final_report(company_name, xbrl_flat, section_summaries, Config.OLLAMA_MODEL)
    print("  ✅ レポート生成完了")
    
    # 保存
    print(f"\n{'='*60}")
    print("💾 結果保存")
    print("="*60)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    result_data = {
        'company_name': company_name, 
        'folder': str(folder), 
        'xbrl_file': str(zip_file),
        'pdf_file': str(pdf_file) if pdf_file else None, 
        'model_used': Config.OLLAMA_MODEL,
        'num_predict': Config.NUM_PREDICT,
        'xbrl_item_count': len(xbrl_flat),
        'section_count': len(section_summaries),
        'xbrl_flat': xbrl_flat,
        'section_summaries': section_summaries,
        'final_report': final_report,
        'generated_at': datetime.now().isoformat(),
    }
    
    json_path = output_dir / f"porta_detailed_{company_name}_{timestamp}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)
    
    md_path = output_dir / f"porta_detailed_{company_name}_{timestamp}.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(final_report)
    
    print(f"  📁 JSON: {json_path.name}")
    print(f"  📁 MD:   {md_path.name}")
    
    print(f"\n{'='*60}")
    print("🎉 完了！")
    print("="*60)
    print(f"  企業名: {company_name}")
    print(f"  抽出XBRL: {len(xbrl_flat)}項目")
    print(f"  分析セクション: {len(section_summaries)}セクション")
    print(f"  出力: {md_path}")
    
    print(f"\n📊 最終レポート:\n")
    print(final_report)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())