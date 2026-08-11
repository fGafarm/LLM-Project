#!/usr/bin/env python3
"""
包括的XBRLタグパッチ: 全企業のraw_tagsから全バリエーションを使って
xbrl_storeを最大限に充実させる。
Net値を優先、Gross値をフォールバックとして使用。
既存値も上書き（より正確なタグが見つかった場合）。
"""

import json
import os
import sys
import glob
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

STORE_DIR = Path(r'c:\Users\shun nabeno\Desktop\Local LLM Project\financial_analysis_system\xbrl_store')

# ==========================================
# 全バリエーションタグマッピング
# priority低い = 優先（Netが最優先）
# ==========================================

COMPREHENSIVE_TAGS = {
    # =============================================================
    # B/S 流動資産 — 「その他(流動)」を最小化するために全勘定科目を網羅
    # =============================================================
    'electronically_recorded_monetary_claims': [
        # 電子記録債権 (25.2%, 966社)
        'ElectronicallyRecordedMonetaryClaimsOperatingCA',
        'ElectronicallyRecordedMonetaryClaimsCA',
    ],
    'contract_assets': [
        # 契約資産 (23.5%, 900社)
        'ContractAssets',
        'ContractAssetsCA',
    ],
    'short_term_investment_securities': [
        # 有価証券(流動) (27.5%, 1053社)
        'ShortTermInvestmentSecurities',
        'MarketableSecuritiesCA',
    ],
    'accounts_receivable_other': [
        # 未収入金 (12.3%, 471社)
        'AccountsReceivableOther',
        'AccountsReceivableOtherCA',
    ],
    'prepaid_expenses': [
        # 前払費用 (11.9%, 456社)
        'PrepaidExpenses',
        'PrepaidExpensesCA',
    ],
    'advance_payments_trade': [
        # 前渡金/前払金 (6.2%, 238社)
        'AdvancePaymentsTrade',
        'AdvancePaymentsTradeCA',
        'AdvancePaymentsOtherCA',
    ],
    'short_term_loans_receivable': [
        # 短期貸付金 (5.2%, 200社)
        'ShortTermLoansReceivable',
        'ShortTermLoansReceivableCA',
    ],
    'income_taxes_receivable': [
        # 未収還付法人税等 (8.8%, 337社)
        'IncomeTaxesReceivable',
        'IncomeTaxesReceivableCA',
        'IncomeTaxesReceivableCAIFRS',
    ],
    'real_estate_for_sale': [
        # 販売用不動産 (6.5%, 248社) — 不動産・建設業
        'RealEstateForSale',
        'RealEstateForSaleCA',
        'RealEstateForSaleCNS',
    ],
    'real_estate_for_sale_in_process': [
        # 仕掛販売用不動産 (3.1%, 120社)
        'RealEstateForSaleInProcess',
        'RealEstateForSaleInProcessCA',
    ],
    'costs_on_uncompleted_construction': [
        # 未成工事支出金 (6.7%, 256社) — 建設業
        'CostsOnUncompletedConstructionContractsCNS',
        'CostsOnUncompletedConstructionContractsAndOtherCNS',
        'CostsOnUncompletedConstructionContracts',
    ],
    'completed_construction_receivables': [
        # 完成工事未収入金 (3.2%, 122社) — 建設業
        'AccountsReceivableFromCompletedConstructionContractsCNS',
        'NotesReceivableAccountsReceivableFromCompletedConstructionContractsAndOtherCNS',
    ],
    'operating_loans': [
        # 営業貸付金 — 金融・不動産業
        'OperatingLoansCA',
        'OperatingLoans',
    ],
    'lease_investment_assets': [
        # リース投資資産 (2.8%, 108社)
        'LeaseInvestmentAssetsCA',
        'LeaseReceivablesAndInvestmentAssetsCA',
    ],
    'finished_goods': [
        # 製品 (4.2%, 161社) — 商品及び製品と分離報告の場合
        'FinishedGoods',
        'FinishedGoodsCA',
    ],
    'deferred_assets': [
        # 繰延資産 (10.1%, 387社)
        'DeferredAssets',
    ],

    # ===== B/S 有形固定資産 =====
    'buildings': [
        # Net値（帳簿価額）優先
        'BuildingsAndStructuresNet',
        'BuildingsNet',
        'BuildingsAndAccompanyingFacilitiesNet',
        # IFRS
        'BuildingsAndStructuresIFRS',
        # Gross値（取得価額）フォールバック - Netがない企業用
        'BuildingsAndStructures',
        'Buildings',
        'BuildingsAndAccompanyingFacilities',
    ],
    'machinery_equipment_net': [
        # Net値優先
        'MachineryEquipmentAndVehiclesNet',
        'MachineryAndEquipmentNet',
        # 工具器具備品を含む合算タグ
        'MachineryVehiclesToolsFurnitureAndFixturesNet',
        # IFRS
        'MachineryAndVehiclesIFRS',
        # Gross値フォールバック
        'MachineryEquipmentAndVehicles',
        'MachineryVehiclesToolsFurnitureAndFixtures',
    ],
    'tools_furniture_net': [
        # 工具器具備品（machineryと分離されている場合）
        'ToolsFurnitureAndFixturesNet',
        'ToolsFurnitureAndFixturesIFRS',
        'ToolsFurnitureAndFixtures',
    ],
    'vessels_net': [
        'VesselsNet',
        'Vessels',
        'ShipsNet',
        'Ships',
    ],
    'vehicles_net': [
        'VehiclesNet',
        'Vehicles',
    ],
    'lease_assets_ppe_net': [
        'LeaseAssetsNetPPE',
        'LeaseAssetsNet',
        # Gross値フォールバック
        'LeaseAssetsPPE',
        'LeaseAssets',
    ],
    'construction_in_progress': [
        'ConstructionInProgress',
        'ConstructionInProgressPPE',
        'ConstructionInProgressIFRS',
    ],
    'other_ppe_net': [
        'OtherNetPPE',
        'OtherPropertyPlantAndEquipmentNet',
        # Gross値フォールバック
        'OtherPPE',
    ],

    # ===== B/S 無形固定資産 =====
    'software': [
        'Software',
        'SoftwareIA',
        'SoftwareIFRS',
    ],
    'lease_assets_ia': [
        'LeaseAssetsIA',
        'LeaseAssetsNetIA',
    ],
    'software_in_progress': [
        'SoftwareInProgress',
    ],
    'other_intangible_assets': [
        'OtherIA',
        'OtherIntangibleAssets',
    ],

    # ===== B/S 投資その他の資産 =====
    'investment_securities': [
        'InvestmentSecurities',
        'InvestmentSecuritiesIOA',
    ],
    'investments_and_other_assets': [
        'InvestmentsAndOtherAssets',
    ],
    'subsidiary_stocks': [
        'StocksOfSubsidiariesAndAffiliates',
        'InvestmentsAccountedForUsingEquityMethod',
        'InvestmentsAccountedForUsingEquityMethodIFRS',
    ],
    'long_term_loans_receivable': [
        'LongTermLoansReceivable',
        'LongTermLoansReceivableIOA',
    ],
    'retirement_benefit_assets': [
        'NetDefinedBenefitAsset',
        'RetirementBenefitAsset',
    ],
    'other_investments_other_assets': [
        'OtherIOA',
        'OtherInvestmentsAndOtherAssets',
    ],
    'allowance_doubtful_accounts_investments': [
        'AllowanceForDoubtfulAccountsIOAByGroup',
        'AllowanceForDoubtfulAccountsIOA',
    ],
    'guarantee_deposits': [
        # 差入保証金・敷金保証金 (11.7%+13.7%, ~1000社)
        'GuaranteeDepositsIOA',
        'LeaseAndGuaranteeDeposits',
        'LeaseDepositsIOA',
    ],
    'long_term_prepaid_expenses': [
        # 長期前払費用 (9.2%, 351社)
        'LongTermPrepaidExpenses',
        'LongTermPrepaidExpensesIOA',
    ],
    'insurance_funds': [
        # 保険積立金 (5.0%, 192社)
        'InsuranceFunds',
        'InsuranceFundsIOA',
    ],
    'investments_in_capital': [
        # 出資金 (3.5%, 134社)
        'InvestmentsInCapital',
        'InvestmentsInCapitalIOA',
        'InvestmentsInCapitalOfSubsidiariesAndAffiliates',
    ],
    'deferred_tax_assets_ioa': [
        # 繰延税金資産(投資その他) (64.3%, 2465社) — J-GAAP固有
        'DeferredTaxAssetsIOA',
    ],

    # ===== B/S 流動資産 (既存) =====
    'allowance_doubtful_accounts_ca': [
        'AllowanceForDoubtfulAccountsCA',
        'AllowanceForDoubtfulAccountsCurrentAssets',
    ],
    'accounts_receivable': [
        'AccountsReceivableTrade',
        'AccountsReceivableTradeAndContractAssets',
    ],
    'notes_receivable': [
        'NotesReceivableTrade',
    ],
    'trade_receivables': [
        # 結合タグ（受取手形+売掛金）
        'NotesAndAccountsReceivableTradeAndContractAssets',
        'NotesAndAccountsReceivableTrade',
    ],
    'merchandise': [
        'MerchandiseAndFinishedGoods',
        'Merchandise',
        'MerchandiseAndFinishedGoodsCAIFRS',
    ],
    'work_in_progress': [
        'WorkInProcess',
        'WorkInProcessCAIFRS',
    ],
    'raw_materials': [
        'RawMaterialsAndSupplies',
        'RawMaterials',
        'RawMaterialsAndSuppliesCAIFRS',
    ],
    'supplies': [
        'Supplies',
    ],
    'inventories': [
        'Inventories',
        'InventoriesCAIFRS',
    ],
    'other_current_assets': [
        'OtherCA',
        'OtherCurrentAssets',
        'OtherCurrentAssetsCAIFRS',
    ],
    'financial_assets_current': [
        'FinancialAssetsCLIFRS',
        'OtherFinancialAssetsCAIFRS',
    ],

    # =============================================================
    # B/S 流動負債 — 「その他(流動負債)」を最小化
    # =============================================================
    'electronically_recorded_obligations': [
        # 電子記録債務 (21.3%, 817社)
        'ElectronicallyRecordedObligationsOperatingCL',
        'ElectronicallyRecordedObligationsCL',
        'ElectronicallyRecordedObligationsFacilitiesCL',
    ],
    'accrued_consumption_taxes': [
        # 未払消費税等 (18.0%, 692社)
        'AccruedConsumptionTaxes',
        'AccruedConsumptionTaxesCL',
    ],
    'deposits_received': [
        # 預り金 (10.0%, 382社)
        'DepositsReceived',
        'DepositsReceivedCL',
    ],
    'provision_directors_bonuses': [
        # 役員賞与引当金 (28.7%, 1100社)
        'ProvisionForDirectorsBonuses',
        'ProvisionForDirectorsBonusesCL',
    ],
    'asset_retirement_obligations_cl': [
        # 資産除去債務(流動) (13.6%, 521社)
        'AssetRetirementObligationsCL',
    ],
    'construction_payables': [
        # 工事未払金等 (建設業, 111社)
        'NotesPayableAccountsPayableForConstructionContractsAndOtherCNS',
        'AccountsPayableForConstructionContractsCNS',
    ],
    'advances_received_construction': [
        # 未成工事受入金 (建設業, 165社)
        'AdvancesReceivedOnUncompletedConstructionContractsCNS',
        'AdvancesReceivedOnUncompletedConstructionContracts',
    ],
    'provision_construction_warranties': [
        # 完成工事補償引当金 (建設業, 196社)
        'ProvisionForWarrantiesForCompletedConstruction',
        'ProvisionForWarrantiesForCompletedConstructionCL',
    ],
    'provision_construction_loss': [
        # 工事損失引当金 (建設業, 260社)
        'ProvisionForLossOnConstructionContracts',
        'ProvisionForLossOnConstructionContractsCL',
    ],
    'notes_payable_facilities': [
        # 設備関係支払手形 (3.5%, 134社)
        'NotesPayableFacilities',
        'NotesPayableFacilitiesCL',
    ],
    'lease_obligations_current': [
        'LeaseObligationsCL',
        'LeaseObligationsCurrentLiabilities',
        'LeaseLiabilitiesCLIFRS',
    ],
    'provision_bonuses': [
        'ProvisionForBonuses',
        'ProvisionForBonusesCL',
    ],
    'accounts_payable_other': [
        'AccountsPayableOther',
        'AccountsPayableOtherCL',
    ],
    'accounts_payable_trade': [
        'AccountsPayableTrade',
    ],
    'advances_received': [
        'AdvancesReceived',
        'AdvancesReceivedCL',
        'ContractLiabilitiesCL',
        'ContractLiabilities',
    ],
    'accrued_expenses': [
        'AccruedExpenses',
    ],
    'commercial_paper': [
        'CommercialPapersLiabilities',
        'CommercialPapersCL',
    ],
    'other_current_liabilities': [
        'OtherCL',
        'OtherCurrentLiabilitiesOther',
        'OtherCurrentLiabilitiesCLIFRS',
    ],
    'income_taxes_payable': [
        'IncomeTaxesPayable',
        'IncomeTaxesPayableCLIFRS',
    ],
    'short_term_loans': [
        'ShortTermLoansPayable',
        'ShortTermBorrowingsCLIFRS',
    ],
    'current_portion_long_term': [
        'CurrentPortionOfLongTermLoansPayable',
        'CurrentPortionOfBonds',
    ],

    # =============================================================
    # B/S 固定負債 — 「その他(固定負債)」を最小化
    # =============================================================
    'lease_obligations_non_current': [
        'LeaseObligationsNCL',
        'LeaseObligationsNoncurrentLiabilities',
        'LeaseLiabilitiesNCLIFRS',
    ],
    'bonds_payable': [
        'BondsPayable',
        'BondsPayableNCL',
    ],
    'bonds_and_borrowings_ncl': [
        'BondsAndBorrowingsNCLIFRS',
    ],
    'long_term_loans': [
        'LongTermLoansPayable',
        'LongTermBorrowingsNCLIFRS',
    ],
    'retirement_benefit_liability': [
        'NetDefinedBenefitLiability',
        'RetirementBenefitLiability',
    ],
    'asset_retirement_obligations': [
        'AssetRetirementObligationsNCL',
        'AssetRetirementObligations',
    ],
    'provision_directors_retirement': [
        'ProvisionForDirectorsRetirementBenefits',
        'ProvisionForDirectorsRetirementBenefitsNCL',
    ],
    'long_term_accounts_payable_other': [
        # 長期未払金 (11.6%, 443社)
        'LongTermAccountsPayableOther',
        'LongTermAccountsPayableOtherNCL',
    ],
    'long_term_guarantee_deposited': [
        # 長期預り保証金 (4.8%, 184社)
        'LongTermGuaranteeDeposited',
        'LongTermGuaranteeDepositedNCL',
    ],
    'provision_environmental': [
        # 環境対策引当金 (5.2%, 199社)
        'ProvisionForEnvironmentalMeasuresNCL',
        'ProvisionForEnvironmentalMeasures',
    ],
    'provision_share_based_remuneration': [
        # 株式報酬引当金 (6.1%, 233社)
        'ProvisionForShareBasedRemunerationNCL',
        'ProvisionForShareBasedRemunerationForDirectorsAndOtherOfficersNCL',
    ],
    'deferred_tax_liabilities': [
        'DeferredTaxLiabilities',
        'DeferredTaxLiabilitiesIFRS',
    ],
    'deferred_tax_assets': [
        'DeferredTaxAssets',
        'DeferredTaxAssetsIFRS',
    ],
    'other_non_current_liabilities': [
        'OtherNCL',
        'OtherNoncurrentLiabilitiesOther',
        'OtherNonCurrentLiabilitiesNCLIFRS',
    ],
    'financial_liabilities_current': [
        'FinancialLiabilitiesCLIFRS',
        'OtherFinancialLiabilitiesCLIFRS',
    ],
    'financial_liabilities_non_current': [
        'FinancialLiabilitiesNCLIFRS',
        'OtherFinancialLiabilitiesNCLIFRS',
    ],

    # =============================================================
    # B/S 純資産 — 「その他包括利益累計額」の内訳を網羅
    # =============================================================
    'accumulated_other_comprehensive': [
        'AccumulatedOtherComprehensiveIncomeLossSE',
        'ValuationAndTranslationAdjustments',
        'OtherComponentsOfEquityIFRS',
    ],
    'valuation_difference_securities': [
        # 有価証券評価差額金 (75.3%, 2889社)
        'ValuationDifferenceOnAvailableForSaleSecurities',
        'ValuationDifferenceOnAvailableForSaleSecuritiesSE',
    ],
    'foreign_currency_translation': [
        # 為替換算調整勘定 (56.7%, 2176社)
        'ForeignCurrencyTranslationAdjustment',
        'ForeignCurrencyTranslationAdjustmentSE',
    ],
    'deferred_gains_losses_hedges': [
        # 繰延ヘッジ損益 (27.8%, 1067社)
        'DeferredGainsOrLossesOnHedges',
        'DeferredGainsOrLossesOnHedgesSE',
    ],
    'revaluation_reserve_land': [
        # 土地再評価差額金 (12.0%, 461社)
        'RevaluationReserveForLand',
        'RevaluationReserveForLandSE',
    ],
    'remeasurements_defined_benefit': [
        # 退職給付に係る調整累計額 (47.7%, 1829社)
        'RemeasurementsOfDefinedBenefitPlans',
        'RemeasurementsOfDefinedBenefitPlansSE',
    ],
    'subscription_rights': [
        # 新株予約権 (36.4%, 1395社)
        'SubscriptionRightsToShares',
        'SubscriptionRightsToSharesSE',
    ],

    # ===== B/S IFRS固有 =====
    'right_of_use_assets': [
        'RightOfUseAssetsIFRS',
    ],
    'financial_assets_non_current': [
        'FinancialAssetsNCAIFRS',
        'OtherFinancialAssetsNCAIFRS',
    ],
    'other_non_current_assets': [
        'OtherNonCurrentAssetsNCAIFRS',
    ],

    # ===== C/F 営業活動 =====
    'depreciation_amortization_cf': [
        'DepreciationAndAmortizationOpeCF',
        'DepreciationAndAmortizationSGA',
        'DepreciationAndAmortizationOpeCFIFRS',
    ],
    'impairment_loss_cf': [
        'ImpairmentLossOpeCF',
        'ImpairmentLossOnNonCurrentAssetsOpeCF',
        'ImpairmentLossOpeCFIFRS',
    ],
    'equity_method_earnings_cf': [
        'EquityInEarningsLossesOfAffiliatesOpeCF',
        'ShareOfProfitLossOfInvestmentsAccountedForUsingEquityMethodOpeCF',
        'ShareOfProfitLossOfInvestmentsAccountedForUsingEquityMethodOpeCFIFRS',
    ],
    'decrease_increase_receivables_cf': [
        'DecreaseIncreaseInNotesAndAccountsReceivableTradeOpeCF',
        'DecreaseIncreaseInTradeReceivablesOpeCF',
        'DecreaseIncreaseInAccountsReceivableTradeAndContractAssetsOpeCF',
        'DecreaseIncreaseInTradeAndOtherReceivablesOpeCFIFRS',
    ],
    'decrease_increase_inventories_cf': [
        'DecreaseIncreaseInInventoriesOpeCF',
        'DecreaseIncreaseInInventoriesOpeCFIFRS',
    ],
    'increase_decrease_payables_cf': [
        'IncreaseDecreaseInNotesAndAccountsPayableTradeOpeCF',
        'IncreaseDecreaseInTradePayablesOpeCF',
        'IncreaseDecreaseInTradeAndOtherPayablesOpeCFIFRS',
    ],
    'subtotal_operating_cf': [
        'SubtotalOpeCF',
    ],
    'interest_paid_cf': [
        'InterestExpensesPaidOpeCFFinCF',  # 79.9%, 3066社 — 最頻出
        'InterestExpensesPaidOpeCF',
        'InterestPaidOpeCF',
        'InterestPaidOpeCFIFRS',
    ],
    'interest_received_cf': [
        'InterestAndDividendsIncomeReceivedOpeCFInvCF',  # 78.6%, 3014社
        'InterestAndDividendIncomeReceivedOpeCF',
        'InterestAndDividendsIncomeReceivedOpeCF',
        'InterestIncomeReceivedOpeCF',
        'InterestReceivedOpeCFIFRS',
    ],
    'dividends_received_cf': [
        'DividendsIncomeReceivedOpeCF',
        'DividendsReceivedOpeCFIFRS',
    ],
    'income_taxes_paid_cf': [
        'IncomeTaxesPaidOpeCF',
        'IncomeTaxesPaidRefundOpeCF',
        'IncomeTaxesPaidOpeCFIFRS',
    ],

    # ===== C/F 投資活動 =====
    'purchase_ppe': [
        'PurchaseOfPropertyPlantAndEquipmentInvCF',  # 75.7%, 2904社
        'PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssetsInvCF',
        'PurchaseOfNoncurrentAssetsInvCF',
        'PurchaseOfPropertyPlantAndEquipmentInvCFIFRS',
    ],
    'purchase_intangibles': [
        'PurchaseOfIntangibleAssetsInvCF',  # 67.4%, 2586社
        'PurchaseOfIntangibleAssetsInvCFIFRS',
    ],
    'purchase_investments': [
        'PurchaseOfInvestmentSecuritiesInvCF',  # 67.7%, 2598社
        'PurchaseOfInvestmentSecuritiesInvCFIFRS',
    ],
    'proceeds_sales_ppe_cf': [
        'ProceedsFromSalesOfPropertyPlantAndEquipmentInvCF',
        'ProceedsFromSalesOfPropertyPlantAndEquipmentAndIntangibleAssetsInvCF',
        'ProceedsFromSalesOfPropertyPlantAndEquipmentInvCFIFRS',
    ],
    'proceeds_sales_investments_cf': [
        'ProceedsFromSalesOfInvestmentSecuritiesInvCF',
        'ProceedsFromSalesAndRedemptionOfInvestmentSecuritiesInvCF',
    ],
    'purchase_subsidiary_shares_cf': [
        'PurchaseOfStocksOfSubsidiariesAndAffiliatesInvCF',
        'PurchaseOfInvestmentsInSubsidiariesResultingInChangeInScopeOfConsolidationInvCF',
    ],

    # ===== C/F 財務活動 =====
    'dividends_paid_nci_cf': [
        'DividendsPaidToNonControllingInterestsFinCF',
        'DividendsPaidToMinorityShareholdersFinCF',
    ],
    'repayment_lease_obligations_cf': [
        'RepaymentsOfLeaseObligationsFinCF',
        'PaymentsOfLeaseObligationsFinCF',
        'RepaymentsOfLeaseLiabilitiesFinCFIFRS',
    ],
    'proceeds_long_term_loans_cf': [
        'ProceedsFromLongTermLoansPayableFinCF',
        'ProceedsFromLongTermBorrowingsFinCFIFRS',
    ],
    'repayment_long_term_loans_cf': [
        'RepaymentOfLongTermLoansPayableFinCF',
        'RepaymentsOfLongTermBorrowingsFinCFIFRS',
    ],
    'net_change_short_term_loans_cf': [
        'NetIncreaseDecreaseInShortTermLoansPayableFinCF',
        'NetIncreaseDecreaseInShortTermBorrowingsFinCFIFRS',
    ],
    'proceeds_bonds_cf': [
        'ProceedsFromIssuanceOfBondsFinCF',
        'ProceedsFromIssuanceOfBondsFinCFIFRS',
    ],
    'redemption_bonds_cf': [
        'RedemptionOfBondsFinCF',
        'RedemptionOfBondsFinCFIFRS',
    ],
    'dividends_paid': [
        'CashDividendsPaidFinCF',  # 75.1%, 2879社
        'DividendsPaidFinCF',
        'DividendsPaidFinCFIFRS',
        'DividendsFromSurplus',
    ],
    'proceeds_borrowings': [
        'ProceedsFromShortTermLoansPayableFinCF',
        'ProceedsFromShortTermBorrowingsFinCFIFRS',
    ],
    'repayments_borrowings': [
        'RepaymentOfShortTermLoansPayableFinCF',
        'RepaymentsOfShortTermBorrowingsFinCFIFRS',
    ],
    'effect_exchange_rate_cf': [
        'EffectOfExchangeRateChangeOnCashAndCashEquivalents',
        'EffectOfExchangeRateChangesOnCashAndCashEquivalentsIFRS',
    ],

    # ===== P/L =====
    'non_controlling_profit': [
        'ProfitLossAttributableToNonControllingInterests',
        'ProfitLossAttributableToNonControllingInterestsIFRS',
    ],
    'income_before_tax': [
        'IncomeBeforeIncomeTaxes',
        'ProfitLossBeforeTaxIFRS',
    ],

    # ===== 株主構成 =====
    'financial_institutions_pct': [
        'PercentageOfShareholdingsFinancialInstitutions',
        'PercentageOfTotalShareholdingFinancialInstitutions',
    ],
    'foreign_corporations_pct': [
        'PercentageOfShareholdingsForeignCorporationsEtc',
        'PercentageOfShareholdingsForeignNationalsEtc',
        'PercentageOfTotalShareholdingForeignCorporationsEtc',
    ],
    'individuals_pct': [
        'PercentageOfShareholdingsIndividualsAndOthers',
        'PercentageOfTotalShareholdingIndividualsAndOthers',
    ],
    'domestic_corporations_pct': [
        'PercentageOfShareholdingsOtherCorporations',
        'PercentageOfTotalShareholdingOtherCorporations',
    ],
    'treasury_pct': [
        'PercentageOfShareholdingsTreasuryStock',
        'PercentageOfTotalShareholdingTreasuryStock',
    ],
    'employee_count': [
        'NumberOfEmployees',
        'NumberOfEmployeesDEI',
    ],
}


def patch_store_file(store_file: Path, raw_tags_file: Path) -> bool:
    """1年分のデータをパッチ"""
    with open(store_file, 'r', encoding='utf-8') as f:
        store = json.load(f)
    data = store.get('data', {})

    with open(raw_tags_file, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    tags = raw.get('tags', {})

    changed = False

    for field, tag_candidates in COMPREHENSIVE_TAGS.items():
        best_val = None
        for tag_name in tag_candidates:
            if tag_name in tags:
                td = tags[tag_name]
                val = td.get('value') if isinstance(td, dict) else None
                if isinstance(val, (int, float)):
                    best_val = val
                    break  # 最優先タグが見つかったので終了

        if best_val is not None:
            if data.get(field) != best_val:
                data[field] = best_val
                changed = True
        # best_val が None の場合は既存値を保持（削除しない）

    if changed:
        store['data'] = data
        with open(store_file, 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

    return changed


def main():
    raw_files = sorted(STORE_DIR.glob('*/*_raw_tags.json'))
    print(f"包括的タグパッチ: {len(raw_files)} ファイルを処理")

    updated = 0
    errors = 0

    for i, rf in enumerate(raw_files):
        if (i + 1) % 5000 == 0:
            print(f"  進捗: {i+1}/{len(raw_files)} (更新:{updated})")

        year = rf.stem.split('_')[0]
        store_file = rf.parent / f'{year}.json'
        if not store_file.exists():
            continue

        try:
            if patch_store_file(store_file, rf):
                updated += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ERROR {rf}: {e}")

    print(f"\nDone! Updated: {updated}, Errors: {errors}, Total: {len(raw_files)}")


if __name__ == '__main__':
    main()
