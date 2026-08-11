"""
S&P 500 ticker list generator.
Wikipedia から最新の S&P 500 構成銘柄を取得して tickers ファイルに保存する。

Usage:
    python sp500_tickers.py              # sp500.txt に保存
    python sp500_tickers.py --output custom.txt
"""

import re
import argparse
from pathlib import Path
from urllib.request import Request, urlopen


def fetch_sp500_tickers() -> list[str]:
    """Fetch S&P 500 tickers from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = Request(url, headers={"User-Agent": "StockFlow Research Bot"})

    with urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")

    # Parse the first table (current constituents)
    # Look for ticker symbols in the table rows
    # Pattern: <td>...<a ...>TICKER</a>...</td> in the first column
    tickers = []

    # Find the wikitable
    table_match = re.search(r'<table[^>]*class="[^"]*wikitable[^"]*sortable[^"]*"[^>]*>(.*?)</table>',
                            html, re.DOTALL)
    if not table_match:
        raise ValueError("Could not find S&P 500 table on Wikipedia")

    table_html = table_match.group(1)

    # Extract tickers from first <td> of each <tr>
    rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
    for row in rows:
        # First <td> contains the ticker
        td_match = re.search(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if td_match:
            td_content = td_match.group(1)
            # Extract text, removing HTML tags
            ticker = re.sub(r'<[^>]+>', '', td_content).strip()
            # Validate: should be 1-5 uppercase letters (possibly with dots like BRK.B)
            if re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', ticker):
                tickers.append(ticker)

    return sorted(set(tickers))


# Hardcoded fallback list (top companies, in case Wikipedia fetch fails)
SP500_CORE = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AFL",
    "AIG", "AMAT", "AMD", "AMGN", "AMP", "AMZN", "ANET", "ANSS", "AON", "APD",
    "APH", "AVGO", "AXP", "BA", "BAC", "BDX", "BK", "BKNG", "BLK", "BMY",
    "BSX", "C", "CAT", "CB", "CDNS", "CEG", "CHTR", "CI", "CL", "CMCSA",
    "CME", "COF", "COP", "COST", "CRM", "CSCO", "CTAS", "CVS", "CVX", "D",
    "DD", "DE", "DHR", "DIS", "DUK", "ECL", "EL", "EMR", "EOG", "ETN",
    "EW", "EXC", "F", "FDX", "FI", "FTNT", "GD", "GE", "GILD", "GM",
    "GOOG", "GOOGL", "GPN", "GS", "HAL", "HCA", "HD", "HON", "HUM", "IBM",
    "ICE", "IDXX", "INTC", "INTU", "ISRG", "ITW", "JCI", "JNJ", "JPM", "KDP",
    "KEY", "KHC", "KLAC", "KMB", "KO", "LHX", "LIN", "LLY", "LMT", "LOW",
    "LRCX", "MA", "MAR", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT", "MET",
    "META", "MMC", "MMM", "MO", "MPC", "MRK", "MRNA", "MS", "MSCI", "MSFT",
    "MSI", "MU", "NEE", "NEM", "NFLX", "NKE", "NOC", "NOW", "NSC", "NVDA",
    "NVS", "ORCL", "OXY", "PANW", "PARA", "PAYX", "PCAR", "PEP", "PFE", "PG",
    "PGR", "PH", "PLTR", "PM", "PNC", "PSA", "PSX", "PYPL", "QCOM", "REGN",
    "ROP", "ROST", "RTX", "SBUX", "SCHW", "SHW", "SLB", "SNPS", "SO", "SPG",
    "SPGI", "SRE", "SYK", "SYY", "T", "TDG", "TFC", "TGT", "TJX", "TMO",
    "TMUS", "TRV", "TSLA", "TSN", "TT", "TXN", "UNH", "UNP", "UPS", "USB",
    "V", "VICI", "VLO", "VRSK", "VRTX", "VZ", "WBA", "WBD", "WEC", "WELL",
    "WFC", "WM", "WMT", "XOM", "ZBH", "ZTS",
]


def main():
    parser = argparse.ArgumentParser(description="Generate S&P 500 ticker list")
    parser.add_argument("--output", type=str, default="sp500.txt")
    args = parser.parse_args()

    print("Fetching S&P 500 tickers from Wikipedia...")
    try:
        tickers = fetch_sp500_tickers()
        print(f"  Found {len(tickers)} tickers from Wikipedia")
    except Exception as e:
        print(f"  Wikipedia fetch failed: {e}")
        print(f"  Using hardcoded core list ({len(SP500_CORE)} tickers)")
        tickers = SP500_CORE

    outpath = Path(__file__).parent / args.output
    with open(outpath, "w") as f:
        for t in tickers:
            f.write(t + "\n")

    print(f"Saved {len(tickers)} tickers to {outpath}")


if __name__ == "__main__":
    main()
