"""
US Financial Analysis System - Configuration
"""
from pathlib import Path

# === Paths ===
PROJECT_ROOT = Path(__file__).parent
US_XBRL_STORE = PROJECT_ROOT / "us_xbrl_store"
OUTPUT_DIR = PROJECT_ROOT / "output"

# SEC EDGAR raw data (downloaded externally)
EDGAR_DATA_DIR = Path("E:/PDF/US Company")
COMPANYFACTS_DIR = EDGAR_DATA_DIR / "companyfacts"
COMPANY_TICKERS_FILE = EDGAR_DATA_DIR / "company_tickers.json"
FILING_DIR = EDGAR_DATA_DIR / "10-K"  # 10-K filings stored here

# === SEC EDGAR API ===
SEC_EMAIL = "fuchouwachouwa@gmail.com"
SEC_USER_AGENT = f"StockFlow Research {SEC_EMAIL}"
SEC_BASE_URL = "https://data.sec.gov"
SEC_EFTS_URL = "https://efts.sec.gov/LATEST"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

# Rate limit: SEC requires max 10 requests/sec
SEC_RATE_LIMIT = 0.12  # seconds between requests (≈8 req/sec, safe margin)

# === Analysis ===
# LLM settings (same as Japanese system)
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_NUM_CTX = 32000

# Target years for analysis
TARGET_YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

# === US-GAAP → Internal field mapping ===
# Mirrors the Japanese xbrl_store field names for compatibility
# See us_tag_mapping.py for the full mapping
