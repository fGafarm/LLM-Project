"""
US-GAAP tag → internal field name mapping.

Maps SEC EDGAR companyfacts US-GAAP tags to the same field names
used in the Japanese xbrl_store, enabling code reuse across both pipelines.

Each entry: internal_field_name → [list of US-GAAP tags to try, in priority order]
"""

# === P/L (Income Statement) ===
PL_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "RevenuesNetOfInterestExpense",  # Banks (GS, MS etc.)
        "InterestAndDividendIncomeOperating",  # Banks (FITB, RF, TFC etc.)
        "RealEstateRevenueNet",  # REITs
        "ElectricDomesticRegulatedRevenue",  # Utilities (XEL)
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RegulatedAndUnregulatedOperatingRevenue",  # Utilities (DTE)
        "InterestIncomeOperating",  # Credit cards (SYF)
        "OperatingLeaseLeaseIncome",  # REITs (EQR, CPT etc.)
        "GrossInvestmentIncomeOperating",  # BDC 総投資収益 (FSK/GBDC/MAIN/OBDC/OCSL/TSLX 6/6社で実測 2026-07-11)
        "InvestmentIncomeOperating",  # 投資会社 (防御的追加)
    ],
    "cost_of_sales": [
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfRevenue",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
    ],
    "research_development": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseOperating",
        # --- 拡張カバレッジ: 大企業（TSLA, GOOGL等）が使う集約タグ ---
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
        "InterestExpenseBorrowings",
        "InterestExpenseLongTermDebt",
        "InterestExpenseDebtExcludingAmortization",
        "InterestExpenseOther",
    ],
    "interest_income": [
        "InterestIncomeExpenseNet",
        "InvestmentIncomeInterest",
    ],
    "paid_in_kind_interest": [  # PIK利息（US-GAAP独自・非現金利息費用）
        "PaidInKindInterest",
    ],
    "noncash_interest_expense": [  # 非現金利息費用（PIK含む）
        "NoncashInterestExpense",
        "AmortizationOfDebtDiscountPremium",
    ],
    "investment_income_pik": [  # PIK収入（BDC/CEF）
        "InvestmentIncomePaymentInKindInterest",
        "PaymentInKindInterest",
    ],
    "investment_income_total": [  # 総投資収益（BDC/CEF/投資会社）
        "GrossInvestmentIncomeOperating",  # BDC 6/6社で実在 (pik_income_ratio の分母。2026-07-11)
        "InvestmentIncomeInvestment",
        "InvestmentIncomeInterestAndDividend",
    ],
    "income_before_tax": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "income_tax": [
        "IncomeTaxExpenseBenefit",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "net_income_attributable": [
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLoss",
    ],
    "comprehensive_income": [
        "ComprehensiveIncomeNetOfTax",
        "ComprehensiveIncomeNetOfTaxIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "eps_basic": [
        "EarningsPerShareBasic",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
    ],
}

# === B/S (Balance Sheet) ===
BS_TAGS = {
    # Assets
    "total_assets": [
        "Assets",
    ],
    "current_assets": [
        "AssetsCurrent",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ],
    "short_term_investments": [
        "ShortTermInvestments",
        "AvailableForSaleSecuritiesCurrent",
        "MarketableSecuritiesCurrent",
    ],
    "accounts_receivable": [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
    ],
    "interest_receivable": [  # 未収利息
        "InterestReceivable",
        "InterestReceivableCurrent",
    ],
    "financing_receivable_net": [  # 融資債権残高（BDC・ノンバンク等）
        "FinancingReceivableNet",
        "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
    ],
    "financing_receivable_nonaccrual": [  # 非稼働融資債権
        "FinancingReceivableNonaccrualStatusAmount",
        "FinancingReceivableRecordedInvestmentNonaccrualStatus",
    ],
    "allowance_for_credit_loss": [  # 貸倒引当金（CECL後）
        "AllowanceForCreditLosses",
        "AllowanceForDoubtfulAccountsReceivableCurrent",
        "AllowanceForLoanAndLeaseLosses",
    ],
    "level_3_assets": [  # レベル3資産（公正価値）
        "FairValueMeasurementsNonrecurringValueMeasurementLevel3",
        "AssetsFairValueDisclosure",
    ],
    "inventories": [
        "InventoryNet",
        "InventoryFinishedGoodsNetOfReserves",
    ],
    "other_current_assets": [
        "OtherAssetsCurrent",
        "PrepaidExpenseAndOtherAssetsCurrent",
    ],
    "non_current_assets": [
        "AssetsNoncurrent",
    ],
    "ppe_net": [
        "PropertyPlantAndEquipmentNet",
    ],
    "goodwill": [
        "Goodwill",
    ],
    "intangible_assets": [
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    ],
    "long_term_investments": [
        "LongTermInvestments",
        "AvailableForSaleSecuritiesNoncurrent",
        "MarketableSecuritiesNoncurrent",
    ],
    "other_non_current_assets": [
        "OtherAssetsNoncurrent",
    ],

    # Liabilities
    "total_liabilities": [
        "Liabilities",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "accounts_payable": [
        "AccountsPayableCurrent",
        "AccountsPayable",
    ],
    "short_term_debt": [
        "ShortTermBorrowings",
        "CommercialPaper",
    ],
    "current_portion_long_term_debt": [
        "LongTermDebtCurrent",
    ],
    "accrued_liabilities": [
        "AccruedLiabilitiesCurrent",
    ],
    "interest_payable": [  # 未払利息
        "InterestPayableCurrent",
        "InterestPayable",
    ],
    "deferred_revenue_current": [
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenueCurrent",
    ],
    "other_current_liabilities": [
        "OtherLiabilitiesCurrent",
    ],
    "non_current_liabilities": [
        "LiabilitiesNoncurrent",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "deferred_revenue_non_current": [
        "ContractWithCustomerLiabilityNoncurrent",
        "DeferredRevenueNoncurrent",
    ],
    "other_non_current_liabilities": [
        "OtherLiabilitiesNoncurrent",
    ],

    # Equity
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "common_stock": [
        "CommonStockValue",
        "CommonStocksIncludingAdditionalPaidInCapital",
    ],
    "additional_paid_in_capital": [
        "AdditionalPaidInCapital",
        "AdditionalPaidInCapitalCommonStock",
    ],
    "retained_earnings": [
        "RetainedEarningsAccumulatedDeficit",
    ],
    "treasury_stock": [
        "TreasuryStockValue",
    ],
    "accumulated_other_comprehensive_income": [
        "AccumulatedOtherComprehensiveIncomeLossNetOfTax",
    ],
    "noncontrolling_interests": [
        "MinorityInterest",
        "MinorityInterestInNetAssetsOfConsolidatedEntities",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
    ],
}

# === C/F (Cash Flow Statement) ===
CF_TAGS = {
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "investing_cash_flow": [
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
    ],
    "financing_cash_flow": [
        "NetCashProvidedByUsedInFinancingActivities",
        "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
    "dividends_paid": [
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfOrdinaryDividends",
    ],
    "share_repurchases": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    ],
    "acquisitions": [
        "PaymentsToAcquireBusinessesNetOfCashAcquired",
        "PaymentsToAcquireBusinessesGross",
    ],
    "interest_paid_cf": [  # CF利息支払額（現金ベース）
        "InterestPaidNet",
        "InterestPaid",
    ],
    "interest_received_cf": [  # CF利息受取額
        "InterestReceivedCashFlow",
        "ProceedsFromInterestReceived",
    ],
}

# === Derived metrics (calculated, not from XBRL directly) ===
DERIVED_METRICS = {
    "free_cash_flow": "operating_cash_flow - capex",
    "gross_margin": "gross_profit / revenue * 100",
    "operating_margin": "operating_income / revenue * 100",
    "net_margin": "net_income / revenue * 100",
    "roe": "net_income / total_equity * 100",
    "roa": "net_income / total_assets * 100",
    "equity_ratio": "total_equity / total_assets * 100",
    "debt_equity_ratio": "total_liabilities / total_equity",
    "current_ratio": "current_assets / current_liabilities * 100",
    "ebitda": "operating_income + depreciation_amortization",
    "per": "stock_price / eps_diluted",  # needs market data
    "pbr": "stock_price / (total_equity / shares_outstanding)",  # needs market data
    "dividend_yield": "dividends_per_share / stock_price * 100",  # needs market data
    "payout_ratio": "dividends_paid / net_income * 100",
    # PIK / credit stress (documentation only; logic lives in us_analyzer.calculate_derived_metrics)
    "cash_interest_coverage_ratio": "abs(interest_paid_cf) / interest_expense",  # A1
    "noncash_interest_ratio": "(interest_expense - abs(interest_paid_cf)) / interest_expense * 100",  # A2
    "pik_interest_abs": "paid_in_kind_interest OR (interest_expense - abs(interest_paid_cf))",  # A3
    "accrued_interest_growth": "delta_interest_receivable / interest_income * 100",  # A4
    "interest_coverage_ratio": "operating_income / interest_expense",  # B1
    "cash_icr": "operating_cash_flow / abs(interest_paid_cf)",  # B2
    "short_term_debt_ratio": "(short_term_debt + current_portion_long_term_debt) / total_interest_bearing_debt * 100",  # B4
    "pik_income_ratio": "investment_income_pik / investment_income_total * 100",  # C1 (BDC/CEF)
    "nonaccrual_ratio": "financing_receivable_nonaccrual / financing_receivable_net * 100",  # C4
    "credit_loss_coverage": "allowance_for_credit_loss / financing_receivable_net * 100",  # C3
}

# Combine all tag mappings
ALL_TAGS = {**PL_TAGS, **BS_TAGS, **CF_TAGS}
