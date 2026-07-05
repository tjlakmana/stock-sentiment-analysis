"""
Screener page — Finviz-style dense ticker table with real-time price/sentiment.
"""
from __future__ import annotations

import math

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from loguru import logger

from sentiment_analysis.dashboard.db import now_et, query_df
from sentiment_analysis.ingestion.finviz_ingestor import is_market_hours

dash.register_page(__name__, path="/screener", name="Screener", title="Screener")

PAGE_SIZE = 25

# ── Company / sector data ──────────────────────────────────────────────────

COMPANY_NAMES: dict[str, str] = {
    # Technology
    "AAPL":  "Apple Inc",               "MSFT":  "Microsoft Corp",
    "NVDA":  "NVIDIA Corp",             "AVGO":  "Broadcom Inc",
    "AMD":   "Advanced Micro Devices",  "INTC":  "Intel Corp",
    "QCOM":  "Qualcomm Inc",            "CSCO":  "Cisco Systems",
    "TXN":   "Texas Instruments",       "ADBE":  "Adobe Inc",
    "CRM":   "Salesforce Inc",          "NOW":   "ServiceNow Inc",
    "AMAT":  "Applied Materials",       "LRCX":  "Lam Research",
    "MU":    "Micron Technology",       "ADI":   "Analog Devices",
    "KLAC":  "KLA Corp",                "PANW":  "Palo Alto Networks",
    "SNPS":  "Synopsys Inc",            "CDNS":  "Cadence Design",
    "FTNT":  "Fortinet Inc",            "IBM":   "IBM Corp",
    "ACN":   "Accenture PLC",           "FICO":  "Fair Isaac Corp",
    "ANSS":  "ANSYS Inc",               "IT":    "Gartner Inc",
    "LDOS":  "Leidos Holdings",         "SAIC":  "SAIC Inc",
    "BAH":   "Booz Allen Hamilton",     "CACI":  "CACI International",
    "DXC":   "DXC Technology",          "WEX":   "WEX Inc",
    "FIS":   "Fidelity Natl Info",      "FISV":  "Fiserv Inc",
    "GPN":   "Global Payments",         "INTU":  "Intuit Inc",
    "MSCI":  "MSCI Inc",                "EPAM":  "EPAM Systems",
    "CTSH":  "Cognizant Tech",          "PLTR":  "Palantir Technologies",
    "CRWD":  "CrowdStrike Holdings",    "SNOW":  "Snowflake Inc",
    "MDB":   "MongoDB Inc",             "DDOG":  "Datadog Inc",
    "ZS":    "Zscaler Inc",             "NET":   "Cloudflare Inc",
    "HUBS":  "HubSpot Inc",             "TEAM":  "Atlassian Corp",
    "WDAY":  "Workday Inc",             "VEEV":  "Veeva Systems",
    "TTD":   "Trade Desk Inc",          "RBLX":  "Roblox Corp",
    "U":     "Unity Software",          "HOOD":  "Robinhood Markets",
    # Communication
    "GOOGL": "Alphabet Inc",            "META":  "Meta Platforms",
    "NFLX":  "Netflix Inc",             "T":     "AT&T Inc",
    "VZ":    "Verizon Communications",  "CMCSA": "Comcast Corp",
    "CHTR":  "Charter Communications",  "TMUS":  "T-Mobile US",
    "PARA":  "Paramount Global",        "WBD":   "Warner Bros Discovery",
    "FOX":   "Fox Corp",                "DIS":   "Walt Disney Co",
    "ROKU":  "Roku Inc",                "SNAP":  "Snap Inc",
    "PINS":  "Pinterest Inc",           "SPOT":  "Spotify Technology",
    # Healthcare
    "UNH":   "UnitedHealth Group",      "JNJ":   "Johnson & Johnson",
    "LLY":   "Eli Lilly & Co",          "MRK":   "Merck & Co",
    "ABBV":  "AbbVie Inc",              "TMO":   "Thermo Fisher",
    "ABT":   "Abbott Laboratories",     "DHR":   "Danaher Corp",
    "BSX":   "Boston Scientific",       "ELV":   "Elevance Health",
    "CI":    "Cigna Group",             "HUM":   "Humana Inc",
    "MCK":   "McKesson Corp",           "CVS":   "CVS Health",
    "VRTX":  "Vertex Pharmaceuticals",  "REGN":  "Regeneron Pharma",
    "GILD":  "Gilead Sciences",         "AMGN":  "Amgen Inc",
    "BIIB":  "Biogen Inc",              "MRNA":  "Moderna Inc",
    "ILMN":  "Illumina Inc",            "IDXX":  "IDEXX Laboratories",
    "ZTS":   "Zoetis Inc",              "BDX":   "Becton Dickinson",
    "EW":    "Edwards Lifesciences",    "STE":   "Steris PLC",
    "RMD":   "ResMed Inc",              "ISRG":  "Intuitive Surgical",
    "DXCM":  "Dexcom Inc",              "HOLX":  "Hologic Inc",
    "PODD":  "Insulet Corp",            "A":     "Agilent Technologies",
    "MTD":   "Mettler-Toledo",          "IQV":   "IQVIA Holdings",
    "CNC":   "Centene Corp",            "MOH":   "Molina Healthcare",
    # Finance
    "JPM":   "JPMorgan Chase",          "BAC":   "Bank of America",
    "WFC":   "Wells Fargo",             "MS":    "Morgan Stanley",
    "GS":    "Goldman Sachs",           "C":     "Citigroup Inc",
    "USB":   "U.S. Bancorp",            "PNC":   "PNC Financial",
    "TFC":   "Truist Financial",        "COF":   "Capital One Financial",
    "AIG":   "American Intl Group",     "MMC":   "Marsh McLennan",
    "AON":   "Aon PLC",                 "CB":    "Chubb Ltd",
    "V":     "Visa Inc",                "MA":    "Mastercard Inc",
    "AXP":   "American Express",        "PYPL":  "PayPal Holdings",
    "SQ":    "Block Inc",               "AFRM":  "Affirm Holdings",
    "UPST":  "Upstart Holdings",        "SOFI":  "SoFi Technologies",
    "NU":    "Nu Holdings",             "COIN":  "Coinbase Global",
    "MSTR":  "MicroStrategy",           "RIOT":  "Riot Platforms",
    "MARA":  "Marathon Digital",        "HUT":   "Hut 8 Corp",
    "SPGI":  "S&P Global Inc",          "BLK":   "BlackRock Inc",
    "MCO":   "Moody's Corp",            "ICE":   "Intercontinental Exchange",
    "CME":   "CME Group",               "NDAQ":  "Nasdaq Inc",
    "SCHW":  "Charles Schwab",          "BX":    "Blackstone Inc",
    "KKR":   "KKR & Co",                "APO":   "Apollo Global Mgmt",
    # Energy
    "XOM":   "Exxon Mobil",             "CVX":   "Chevron Corp",
    "COP":   "ConocoPhillips",          "EOG":   "EOG Resources",
    "PXD":   "Pioneer Natural Res",     "DVN":   "Devon Energy",
    "SLB":   "SLB (Schlumberger)",      "HAL":   "Halliburton Co",
    "BKR":   "Baker Hughes",            "MPC":   "Marathon Petroleum",
    "PSX":   "Phillips 66",             "VLO":   "Valero Energy",
    "HES":   "Hess Corp",               "WMB":   "Williams Companies",
    "KMI":   "Kinder Morgan",           "OKE":   "ONEOK Inc",
    "SRE":   "Sempra",
    # Consumer
    "AMZN":  "Amazon.com Inc",          "TSLA":  "Tesla Inc",
    "WMT":   "Walmart Inc",             "HD":    "Home Depot",
    "COST":  "Costco Wholesale",        "LOW":   "Lowe's Companies",
    "TGT":   "Target Corp",             "NKE":   "Nike Inc",
    "MCD":   "McDonald's Corp",         "SBUX":  "Starbucks Corp",
    "YUM":   "Yum! Brands",             "CMG":   "Chipotle Mexican Grill",
    "DG":    "Dollar General",          "DLTR":  "Dollar Tree",
    "ROST":  "Ross Stores",             "TJX":   "TJX Companies",
    "PG":    "Procter & Gamble",        "KO":    "Coca-Cola Co",
    "PEP":   "PepsiCo Inc",             "MDLZ":  "Mondelez Intl",
    "CL":    "Colgate-Palmolive",       "GIS":   "General Mills",
    "HSY":   "Hershey Co",              "KHC":   "Kraft Heinz Co",
    "MO":    "Altria Group",            "PM":    "Philip Morris Intl",
    "BABA":  "Alibaba Group",           "EBAY":  "eBay Inc",
    "ETSY":  "Etsy Inc",                "ABNB":  "Airbnb Inc",
    "UBER":  "Uber Technologies",       "LYFT":  "Lyft Inc",
    "DASH":  "DoorDash Inc",            "RIVN":  "Rivian Automotive",
    "LCID":  "Lucid Group",             "GM":    "General Motors",
    "F":     "Ford Motor Co",           "STLA":  "Stellantis NV",
    "DKNG":  "DraftKings Inc",
    # Industrial
    "HON":   "Honeywell Intl",          "CAT":   "Caterpillar Inc",
    "UNP":   "Union Pacific",           "UPS":   "United Parcel Service",
    "FDX":   "FedEx Corp",              "NSC":   "Norfolk Southern",
    "RTX":   "RTX Corp",                "LMT":   "Lockheed Martin",
    "NOC":   "Northrop Grumman",        "GD":    "General Dynamics",
    "BA":    "Boeing Co",               "GE":    "GE Aerospace",
    "RSG":   "Republic Services",       "WM":    "Waste Management",
    "MMM":   "3M Co",                   "EMR":   "Emerson Electric",
    "ETN":   "Eaton Corp",              "ROP":   "Roper Technologies",
    "IR":    "Ingersoll Rand",          "CARR":  "Carrier Global",
    "OTIS":  "Otis Worldwide",          "FAST":  "Fastenal Co",
    "DE":    "Deere & Co",              "PCAR":  "PACCAR Inc",
    "ROK":   "Rockwell Automation",     "PH":    "Parker Hannifin",
    "DOV":   "Dover Corp",              "XYL":   "Xylem Inc",
    # Materials
    "LIN":   "Linde PLC",               "APD":   "Air Products",
    "ECL":   "Ecolab Inc",              "SHW":   "Sherwin-Williams",
    "PPG":   "PPG Industries",          "DD":    "DuPont de Nemours",
    "DOW":   "Dow Inc",                 "NEM":   "Newmont Corp",
    "FCX":   "Freeport-McMoRan",        "VMC":   "Vulcan Materials",
    "MLM":   "Martin Marietta",         "NUE":   "Nucor Corp",
    "STLD":  "Steel Dynamics",          "CF":    "CF Industries",
    "MOS":   "Mosaic Co",               "FMC":   "FMC Corp",
    # Utilities
    "NEE":   "NextEra Energy",          "DUK":   "Duke Energy",
    "SO":    "Southern Co",             "D":     "Dominion Energy",
    "AEP":   "American Electric Power", "EXC":   "Exelon Corp",
    "XEL":   "Xcel Energy",             "ES":    "Eversource Energy",
    "ETR":   "Entergy Corp",            "FE":    "FirstEnergy Corp",
    "PCG":   "PG&E Corp",               "EIX":   "Edison Intl",
    "AWK":   "American Water Works",    "PPL":   "PPL Corp",
    "AES":   "AES Corp",
    # Real Estate
    "AMT":   "American Tower",          "CCI":   "Crown Castle",
    "EQIX":  "Equinix Inc",             "PSA":   "Public Storage",
    "SPG":   "Simon Property Group",    "O":     "Realty Income",
    "WELL":  "Welltower Inc",           "DLR":   "Digital Realty",
    "PLD":   "Prologis Inc",            "VICI":  "VICI Properties",
    "ARE":   "Alexandria Real Estate",  "EQR":   "Equity Residential",
    "AVB":   "AvalonBay Communities",   "IRM":   "Iron Mountain",
    "SBAC":  "SBA Communications",
    # ETFs / Macro
    "SPY":   "SPDR S&P 500 ETF",        "QQQ":   "Invesco QQQ ETF",
    "IWM":   "iShares Russell 2000",    "DIA":   "SPDR Dow Jones ETF",
    "GLD":   "SPDR Gold Shares",        "TLT":   "iShs 20+ Yr Treasury",
    "HYG":   "iShs HY Corp Bond",       "VTI":   "Vanguard Total Mkt",
    "XLF":   "Financial Select SPDR",   "XLK":   "Technology Select SPDR",
    "XLE":   "Energy Select SPDR",      "XLV":   "Health Care Select SPDR",
    "XLI":   "Industrial Select SPDR",  "XLY":   "Cons Discr Select SPDR",
    "XLP":   "Cons Staples Select SPDR","XLU":   "Utilities Select SPDR",
    "XLB":   "Materials Select SPDR",   "XLRE":  "Real Estate Select SPDR",
    "XLC":   "Comm Services SPDR",      "GME":   "GameStop Corp",
    "AMC":   "AMC Entertainment",
}

_SECTOR_MAP: dict[str, str] = {
    # Technology
    "AAPL": "Technology",  "MSFT": "Technology",  "NVDA": "Technology",
    "AVGO": "Technology",  "AMD":  "Technology",   "INTC": "Technology",
    "QCOM": "Technology",  "CSCO": "Technology",   "TXN":  "Technology",
    "ADBE": "Technology",  "CRM":  "Technology",   "NOW":  "Technology",
    "AMAT": "Technology",  "LRCX": "Technology",   "MU":   "Technology",
    "ADI":  "Technology",  "KLAC": "Technology",   "PANW": "Technology",
    "SNPS": "Technology",  "CDNS": "Technology",   "FTNT": "Technology",
    "IBM":  "Technology",  "ACN":  "Technology",   "FICO": "Technology",
    "ANSS": "Technology",  "IT":   "Technology",   "LDOS": "Technology",
    "SAIC": "Technology",  "BAH":  "Technology",   "CACI": "Technology",
    "DXC":  "Technology",  "WEX":  "Technology",   "FIS":  "Technology",
    "FISV": "Technology",  "GPN":  "Technology",   "INTU": "Technology",
    "MSCI": "Technology",  "EPAM": "Technology",   "CTSH": "Technology",
    "PLTR": "Technology",  "CRWD": "Technology",   "SNOW": "Technology",
    "MDB":  "Technology",  "DDOG": "Technology",   "ZS":   "Technology",
    "NET":  "Technology",  "HUBS": "Technology",   "TEAM": "Technology",
    "WDAY": "Technology",  "VEEV": "Technology",   "TTD":  "Technology",
    "RBLX": "Technology",  "U":    "Technology",
    # Communication
    "GOOGL": "Communication", "META":  "Communication", "NFLX":  "Communication",
    "T":     "Communication", "VZ":    "Communication", "CMCSA": "Communication",
    "CHTR":  "Communication", "TMUS":  "Communication", "PARA":  "Communication",
    "WBD":   "Communication", "FOX":   "Communication", "DIS":   "Communication",
    "ROKU":  "Communication", "SNAP":  "Communication", "PINS":  "Communication",
    "SPOT":  "Communication", "HOOD":  "Communication",
    # Healthcare
    "UNH":  "Healthcare",  "JNJ":  "Healthcare",   "LLY":  "Healthcare",
    "MRK":  "Healthcare",  "ABBV": "Healthcare",   "TMO":  "Healthcare",
    "ABT":  "Healthcare",  "DHR":  "Healthcare",   "BSX":  "Healthcare",
    "ELV":  "Healthcare",  "CI":   "Healthcare",   "HUM":  "Healthcare",
    "MCK":  "Healthcare",  "CVS":  "Healthcare",   "VRTX": "Healthcare",
    "REGN": "Healthcare",  "GILD": "Healthcare",   "AMGN": "Healthcare",
    "BIIB": "Healthcare",  "MRNA": "Healthcare",   "ILMN": "Healthcare",
    "IDXX": "Healthcare",  "ZTS":  "Healthcare",   "BDX":  "Healthcare",
    "EW":   "Healthcare",  "STE":  "Healthcare",   "RMD":  "Healthcare",
    "ISRG": "Healthcare",  "DXCM": "Healthcare",   "HOLX": "Healthcare",
    "PODD": "Healthcare",  "A":    "Healthcare",   "MTD":  "Healthcare",
    "IQV":  "Healthcare",  "CNC":  "Healthcare",   "MOH":  "Healthcare",
    # Finance
    "JPM":  "Finance",     "BAC":  "Finance",      "WFC":  "Finance",
    "MS":   "Finance",     "GS":   "Finance",      "C":    "Finance",
    "USB":  "Finance",     "PNC":  "Finance",      "TFC":  "Finance",
    "COF":  "Finance",     "AIG":  "Finance",      "MMC":  "Finance",
    "AON":  "Finance",     "CB":   "Finance",      "V":    "Finance",
    "MA":   "Finance",     "AXP":  "Finance",      "PYPL": "Finance",
    "SQ":   "Finance",     "AFRM": "Finance",      "UPST": "Finance",
    "SOFI": "Finance",     "NU":   "Finance",      "COIN": "Finance",
    "MSTR": "Finance",     "RIOT": "Finance",      "MARA": "Finance",
    "HUT":  "Finance",     "SPGI": "Finance",      "BLK":  "Finance",
    "MCO":  "Finance",     "ICE":  "Finance",      "CME":  "Finance",
    "NDAQ": "Finance",     "SCHW": "Finance",      "BX":   "Finance",
    "KKR":  "Finance",     "APO":  "Finance",
    # Energy
    "XOM":  "Energy",      "CVX":  "Energy",       "COP":  "Energy",
    "EOG":  "Energy",      "PXD":  "Energy",       "DVN":  "Energy",
    "SLB":  "Energy",      "HAL":  "Energy",       "BKR":  "Energy",
    "MPC":  "Energy",      "PSX":  "Energy",       "VLO":  "Energy",
    "HES":  "Energy",      "WMB":  "Energy",       "KMI":  "Energy",
    "OKE":  "Energy",      "SRE":  "Energy",
    # Consumer
    "AMZN": "Consumer",    "TSLA": "Consumer",     "WMT":  "Consumer",
    "HD":   "Consumer",    "COST": "Consumer",     "LOW":  "Consumer",
    "TGT":  "Consumer",    "NKE":  "Consumer",     "MCD":  "Consumer",
    "SBUX": "Consumer",    "YUM":  "Consumer",     "CMG":  "Consumer",
    "DG":   "Consumer",    "DLTR": "Consumer",     "ROST": "Consumer",
    "TJX":  "Consumer",    "PG":   "Consumer",     "KO":   "Consumer",
    "PEP":  "Consumer",    "MDLZ": "Consumer",     "CL":   "Consumer",
    "GIS":  "Consumer",    "HSY":  "Consumer",     "KHC":  "Consumer",
    "MO":   "Consumer",    "PM":   "Consumer",     "BABA": "Consumer",
    "EBAY": "Consumer",    "ETSY": "Consumer",     "ABNB": "Consumer",
    "UBER": "Consumer",    "LYFT": "Consumer",     "DASH": "Consumer",
    "RIVN": "Consumer",    "LCID": "Consumer",     "GM":   "Consumer",
    "F":    "Consumer",    "STLA": "Consumer",     "DKNG": "Consumer",
    "GME":  "Consumer",    "AMC":  "Consumer",
    # Industrial
    "HON":  "Industrial",  "CAT":  "Industrial",   "UNP":  "Industrial",
    "UPS":  "Industrial",  "FDX":  "Industrial",   "NSC":  "Industrial",
    "RTX":  "Industrial",  "LMT":  "Industrial",   "NOC":  "Industrial",
    "GD":   "Industrial",  "BA":   "Industrial",   "GE":   "Industrial",
    "RSG":  "Industrial",  "WM":   "Industrial",   "MMM":  "Industrial",
    "EMR":  "Industrial",  "ETN":  "Industrial",   "ROP":  "Industrial",
    "IR":   "Industrial",  "CARR": "Industrial",   "OTIS": "Industrial",
    "FAST": "Industrial",  "DE":   "Industrial",   "PCAR": "Industrial",
    "ROK":  "Industrial",  "PH":   "Industrial",   "DOV":  "Industrial",
    "XYL":  "Industrial",
    # Materials
    "LIN":  "Materials",   "APD":  "Materials",    "ECL":  "Materials",
    "SHW":  "Materials",   "PPG":  "Materials",    "DD":   "Materials",
    "DOW":  "Materials",   "NEM":  "Materials",    "FCX":  "Materials",
    "VMC":  "Materials",   "MLM":  "Materials",    "NUE":  "Materials",
    "STLD": "Materials",   "CF":   "Materials",    "MOS":  "Materials",
    "FMC":  "Materials",
    # Utilities
    "NEE":  "Utilities",   "DUK":  "Utilities",    "SO":   "Utilities",
    "D":    "Utilities",   "AEP":  "Utilities",    "EXC":  "Utilities",
    "XEL":  "Utilities",   "ES":   "Utilities",    "ETR":  "Utilities",
    "FE":   "Utilities",   "PCG":  "Utilities",    "EIX":  "Utilities",
    "AWK":  "Utilities",   "PPL":  "Utilities",    "AES":  "Utilities",
    # Real Estate
    "AMT":  "Real Estate", "CCI":  "Real Estate",  "EQIX": "Real Estate",
    "PSA":  "Real Estate", "SPG":  "Real Estate",  "O":    "Real Estate",
    "WELL": "Real Estate", "DLR":  "Real Estate",  "PLD":  "Real Estate",
    "VICI": "Real Estate", "ARE":  "Real Estate",  "EQR":  "Real Estate",
    "AVB":  "Real Estate", "IRM":  "Real Estate",  "SBAC": "Real Estate",
}

_COUNTRY_MAP: dict[str, str] = {
    # China
    "BABA": "China", "BIDU": "China", "JD":   "China", "PDD":  "China",
    "NIO":  "China", "XPEV": "China", "LI":   "China", "TCOM": "China",
    "VIPS": "China", "TME":  "China", "BILI": "China", "IQ":   "China",
    "DIDI": "China", "BOSS": "China", "NTES": "China",
    # Canada
    "SHOP": "Canada", "CNI": "Canada", "CP":  "Canada", "TD":  "Canada",
    "RY":   "Canada", "BMO": "Canada", "SU":  "Canada", "ENB": "Canada",
    # UK
    "BP":   "UK", "HSBC": "UK", "AZN":   "UK", "GSK":   "UK",
    "VOD":  "UK", "UL":   "UK", "BTI":   "UK", "DEO":   "UK",
    "LIN":  "UK", "LNVGY":"UK",
    # Europe
    "ASML": "Netherlands",
    "TSM":  "Taiwan",
    "SE":   "Singapore",
    "MELI": "Argentina",
    "NU":   "Brazil",
    "NVO":  "Denmark",
    "TTE":  "France",
    "SAP":  "Germany",   "SIE": "Germany",
    "NESN": "Switzerland", "RHHBY": "Switzerland",
    "RIO":  "Australia", "BHP": "Australia",
    "ACN":  "Ireland",   "CRH": "Ireland", "STX": "Ireland",
}

_COUNTRY_CODE: dict[str, str] = {
    "USA":         "USA", "China":       "CN",  "UK":          "UK",
    "Canada":      "CA",  "Netherlands": "NL",  "Taiwan":      "TW",
    "Singapore":   "SG",  "Argentina":   "AR",  "Brazil":      "BR",
    "Denmark":     "DK",  "France":      "FR",  "Germany":     "DE",
    "Switzerland": "CH",  "Australia":   "AU",  "Ireland":     "IE",
}

# ── Options ────────────────────────────────────────────────────────────────

SIGNAL_OPTIONS = [
    {"label": "All",               "value": "all"},
    {"label": "Bullish Sentiment", "value": "bullish"},
    {"label": "Bearish Sentiment", "value": "bearish"},
    {"label": "Unusual Volume",    "value": "unusual_volume"},
    {"label": "Most Articles",     "value": "most_articles"},
    {"label": "Sentiment Spike",   "value": "spike"},
]

ORDER_OPTIONS = [
    {"label": "Avg Sentiment",  "value": "avg_sentiment"},
    {"label": "Price",          "value": "price"},
    {"label": "Change %",       "value": "change_pct"},
    {"label": "Volume",         "value": "volume"},
    {"label": "Market Cap",     "value": "market_cap"},
    {"label": "Article Count",  "value": "article_count"},
]

SECTOR_OPTIONS = [
    {"label": "Any Sector",    "value": "all"},
    {"label": "Technology",    "value": "Technology"},
    {"label": "Healthcare",    "value": "Healthcare"},
    {"label": "Finance",       "value": "Finance"},
    {"label": "Energy",        "value": "Energy"},
    {"label": "Consumer",      "value": "Consumer"},
    {"label": "Industrial",    "value": "Industrial"},
    {"label": "Materials",     "value": "Materials"},
    {"label": "Utilities",     "value": "Utilities"},
    {"label": "Real Estate",   "value": "Real Estate"},
    {"label": "Communication", "value": "Communication"},
]

MKTCAP_OPTIONS = [
    {"label": "Any",                "value": "all"},
    {"label": "Mega  (>$200B)",     "value": "mega"},
    {"label": "Large  ($10B–$200B)","value": "large"},
    {"label": "Mid  ($2B–$10B)",    "value": "mid"},
    {"label": "Small  ($200M–$2B)", "value": "small"},
    {"label": "Micro  (<$200M)",    "value": "micro"},
]

COUNTRY_OPTIONS = [
    {"label": "Any Country", "value": "all"},
    {"label": "USA",         "value": "USA"},
    {"label": "China",       "value": "China"},
    {"label": "UK",          "value": "UK"},
    {"label": "Canada",      "value": "Canada"},
]

PRICE_OPTIONS = [
    {"label": "Any Price",   "value": "all"},
    {"label": "Under $5",    "value": "u5"},
    {"label": "$5 – $20",    "value": "5_20"},
    {"label": "$20 – $50",   "value": "20_50"},
    {"label": "$50 – $100",  "value": "50_100"},
    {"label": "$100 – $500", "value": "100_500"},
    {"label": "Over $500",   "value": "o500"},
]

CHG_OPTIONS = [
    {"label": "Any Change",  "value": "all"},
    {"label": "Up 5%+",      "value": "up5"},
    {"label": "Up 2–5%",     "value": "up2_5"},
    {"label": "Up 0–2%",     "value": "up0_2"},
    {"label": "Down 0–2%",   "value": "dn0_2"},
    {"label": "Down 2–5%",   "value": "dn2_5"},
    {"label": "Down 5%+",    "value": "dn5"},
]

VOLUME_OPTIONS = [
    {"label": "Any Volume",  "value": "all"},
    {"label": "Under 500K",  "value": "u500k"},
    {"label": "500K – 1M",   "value": "500k_1m"},
    {"label": "1M – 5M",     "value": "1m_5m"},
    {"label": "Over 5M",     "value": "o5m"},
]

AVG_VOL_OPTIONS = [
    {"label": "Any Avg Vol", "value": "all"},
    {"label": "Under 1M",    "value": "u1m"},
    {"label": "1M – 5M",     "value": "1m_5m"},
    {"label": "Over 5M",     "value": "o5m"},
]

SENT_LABEL_OPTIONS = [
    {"label": "Any Sentiment",    "value": "all"},
    {"label": "Bullish",          "value": "Bullish"},
    {"label": "Somewhat Bullish", "value": "Somewhat Bullish"},
    {"label": "Neutral",          "value": "Neutral"},
    {"label": "Somewhat Bearish", "value": "Somewhat Bearish"},
    {"label": "Bearish",          "value": "Bearish"},
]

SENT_SCORE_OPTIONS = [
    {"label": "Any Score",           "value": "all"},
    {"label": "Strong Bull  (>0.6)", "value": "sbull"},
    {"label": "Bull  (0.3–0.6)",     "value": "bull"},
    {"label": "Neutral  (−0.3–0.3)", "value": "neut"},
    {"label": "Bear  (−0.6–−0.3)",   "value": "bear"},
    {"label": "Strong Bear  (<−0.6)","value": "sbear"},
]

TIME_WINDOW_OPTIONS = [
    {"label": "1 Hour",   "value": "1hr"},
    {"label": "4 Hours",  "value": "4hr"},
    {"label": "24 Hours", "value": "24hr"},
]

MIN_ARTICLES_OPTIONS = [
    {"label": "Any",    "value": 0},
    {"label": "2+",     "value": 2},
    {"label": "5+",     "value": 5},
    {"label": "10+",    "value": 10},
    {"label": "25+",    "value": 25},
]

TREND_OPTIONS = [
    {"label": "Any Trend", "value": "all"},
    {"label": "Improving", "value": "improving"},
    {"label": "Declining", "value": "declining"},
    {"label": "Stable",    "value": "stable"},
]

SPIKE_OPTIONS = [
    {"label": "Any",       "value": "all"},
    {"label": "Has Spike", "value": "spike"},
    {"label": "No Spike",  "value": "no_spike"},
]

# ── SQL ───────────────────────────────────────────────────────────────────

_SCREENER_SQL = """
    WITH latest AS (
        SELECT DISTINCT ON (ticker)
            ticker,
            avg_sentiment,
            article_count,
            bullish_count,
            bearish_count,
            neutral_count,
            momentum,
            calculated_at
        FROM ticker_sentiment_summary
        WHERE "window" = :window
        ORDER BY ticker, calculated_at DESC
    ),
    recent_spikes AS (
        SELECT DISTINCT ticker
        FROM sentiment_spikes
        WHERE detected_at > NOW() - INTERVAL '2 hours'
    )
    SELECT
        l.ticker,
        COALESCE(l.avg_sentiment,  0)   AS avg_sentiment,
        COALESCE(l.article_count,  0)   AS article_count,
        COALESCE(l.bullish_count,  0)   AS bullish_count,
        COALESCE(l.bearish_count,  0)   AS bearish_count,
        COALESCE(l.neutral_count,  0)   AS neutral_count,
        l.momentum,
        l.calculated_at,
        p.price,
        p.change_pct,
        p.volume,
        p.market_cap,
        p.pre_market_price,
        p.post_market_price,
        CASE WHEN rs.ticker IS NOT NULL THEN TRUE ELSE FALSE END AS has_spike
    FROM latest l
    LEFT JOIN ticker_prices p  ON l.ticker = p.ticker
    LEFT JOIN recent_spikes rs ON l.ticker = rs.ticker
    WHERE l.article_count >= :min_articles
"""


def _fetch_data(window: str, min_articles: int) -> pd.DataFrame:
    diag = query_df("""
        SELECT
            (SELECT COUNT(*) FROM ticker_sentiment_summary)                 AS tss_total,
            (SELECT COUNT(DISTINCT "window") FROM ticker_sentiment_summary) AS tss_windows,
            (SELECT string_agg(DISTINCT "window", ', ' ORDER BY "window")
               FROM ticker_sentiment_summary)                               AS tss_window_values,
            (SELECT COUNT(*) FROM ticker_sentiment_summary
               WHERE "window" = :window)                                    AS tss_for_window,
            (SELECT COUNT(*) FROM ticker_prices)                            AS price_rows
    """, {"window": window})
    if not diag.empty:
        r = diag.iloc[0]
        logger.info(
            f"[screener] DB snapshot — tss_total={r.tss_total}, "
            f"windows={r.tss_window_values!r}, "
            f"tss_for_window({window!r})={r.tss_for_window}, "
            f"price_rows={r.price_rows}"
        )

    df = query_df(_SCREENER_SQL, {"window": window, "min_articles": min_articles})
    logger.info(
        f"[screener] _fetch_data(window={window!r}, min_articles={min_articles}) "
        f"→ {len(df)} rows"
    )
    return df


# ── Render helpers ────────────────────────────────────────────────────────

_SENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#131c38", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#351212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#280808", "color": "#ff5252", "bd": "#d50000"},
}


def _score_to_label(score: float | None) -> str:
    if score is None or math.isnan(float(score)):
        return "Neutral"
    s = float(score)
    if s >= 0.35:    return "Bullish"
    elif s >= 0.15:  return "Somewhat Bullish"
    elif s > -0.15:  return "Neutral"
    elif s > -0.35:  return "Somewhat Bearish"
    return "Bearish"


def _score_color(score: float | None) -> str:
    label = _score_to_label(score)
    return _SENT_STYLE.get(label, {}).get("color", "#888888")


def _badge(score) -> html.Span:
    label = _score_to_label(score)
    s = _SENT_STYLE.get(label, {})
    return html.Span(
        label,
        style={
            "background":   s.get("bg",    "#141414"),
            "color":        s.get("color", "#444"),
            "border":       f"1px solid {s.get('bd', '#282828')}",
            "borderRadius": "12px",
            "padding":      "2px 8px",
            "fontSize":     "11px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
            "maxWidth":     "130px",
            "overflow":     "hidden",
            "textOverflow": "ellipsis",
            "display":      "inline-block",
        },
    )


def _safe(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fmt_price(v) -> str:
    v = _safe(v)
    return f"${v:,.2f}" if v is not None else "—"


def _fmt_chg(v) -> tuple[str, str]:
    v = _safe(v)
    if v is None:
        return "—", "#888888"
    color = "#00e676" if v >= 0 else "#ff5252"
    sign  = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%", color


def _fmt_volume(v) -> str:
    v = _safe(v)
    if v is None:
        return "—"
    if v >= 1e9:  return f"{v/1e9:.2f}B"
    if v >= 1e6:  return f"{v/1e6:.2f}M"
    if v >= 1e3:  return f"{v/1e3:.1f}K"
    return str(int(v))


def _fmt_mktcap(v) -> str:
    v = _safe(v)
    if v is None:
        return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _trend_icon(momentum) -> html.Span:
    m = str(momentum or "").lower()
    if m == "improving": return html.Span("↑", style={"color": "#00e676", "fontWeight": "700"})
    if m == "declining": return html.Span("↓", style={"color": "#ff5252", "fontWeight": "700"})
    return html.Span("→", style={"color": "#555555"})


def _pct(num, denom) -> str:
    try:
        n, d = int(num), int(denom)
        return f"{n/d*100:.0f}%" if d else "—"
    except (TypeError, ValueError):
        return "—"


# ── Table column definitions ──────────────────────────────────────────────
# (label, width, text-align)
_OV_COLS = [
    ("#",         "35px",  "right"),
    ("Ticker",    "70px",  "left"),
    ("Company",   "160px", "left"),
    ("Country",   "62px",  "center"),
    ("Mkt Cap",   "88px",  "right"),
    ("Price",     "80px",  "right"),
    ("Chg %",     "72px",  "right"),
    ("Volume",    "88px",  "right"),
    ("Articles",  "70px",  "right"),
    ("Sentiment", "136px", "left"),
    ("Score",     "66px",  "right"),
]

_SENT_COLS = [
    ("#",          "36px",  "right"),
    ("Ticker",     "70px",  "left"),
    ("Sentiment",  "130px", "left"),
    ("Score",      "60px",  "right"),
    ("Bull %",     "64px",  "right"),
    ("Bear %",     "64px",  "right"),
    ("Neutral %",  "78px",  "right"),
    ("Articles",   "66px",  "right"),
    ("Last Upd",   "108px", "center"),
    ("Trend",      "56px",  "center"),
]

# ── Table renderers ───────────────────────────────────────────────────────

def _no_results(colspan: int) -> list:
    return [html.Tr([html.Td(
        "No tickers match current filters.",
        colSpan=colspan,
        style={"padding": "32px", "textAlign": "center",
               "color": "#555", "fontSize": "14px"},
    )])]


def _render_overview_rows(df: pd.DataFrame, page: int) -> list:
    if df.empty:
        return _no_results(len(_OV_COLS))

    offset  = (page - 1) * PAGE_SIZE
    page_df = df.iloc[offset : offset + PAGE_SIZE]
    rows: list = []

    for local_i, (_, r) in enumerate(page_df.iterrows()):
        global_i = offset + local_i
        ticker   = r["ticker"]
        company  = COMPANY_NAMES.get(ticker, "")
        _cntry   = _COUNTRY_MAP.get(ticker, "USA")
        country  = _COUNTRY_CODE.get(_cntry, _cntry[:2].upper())
        score    = _safe(r.get("avg_sentiment"))
        color    = _score_color(score)
        chg_text, chg_color = _fmt_chg(r.get("change_pct"))

        price_children: list = [
            html.Span(_fmt_price(r.get("price")),
                      style={"fontSize": "12px", "fontWeight": "600", "color": "#e0e0e0"}),
        ]
        if not is_market_hours():
            pre  = _safe(r.get("pre_market_price"))
            post = _safe(r.get("post_market_price"))
            ext  = pre or post
            lbl  = "Pre" if pre else ("Post" if post else None)
            if ext and lbl:
                price_children.append(
                    html.Span(f"{lbl}: {_fmt_price(ext)}",
                              style={"fontSize": "10px", "color": "#666", "display": "block",
                                     "lineHeight": "1.1"}),
                )

        rows.append(html.Tr([
            html.Td(str(global_i + 1), className="scr-td scr-td-num scr-dim"),
            html.Td(html.A(ticker, href=f"/?keyword={ticker}", className="scr-ticker"),
                    className="scr-td"),
            html.Td(company, className="scr-td scr-company"),
            html.Td(country, className="scr-td scr-td-country"),
            html.Td(_fmt_mktcap(r.get("market_cap")), className="scr-td scr-td-num"),
            html.Td(price_children, className="scr-td scr-td-num"),
            html.Td(chg_text, className="scr-td scr-td-num",
                    style={"color": chg_color}),
            html.Td(_fmt_volume(r.get("volume")), className="scr-td scr-td-num"),
            html.Td(str(int(r.get("article_count", 0))), className="scr-td scr-td-num"),
            html.Td(_badge(score), className="scr-td"),
            html.Td(
                f"{score:+.2f}" if score is not None else "—",
                className="scr-td scr-td-num scr-mono",
                style={"color": color},
            ),
        ], className="scr-tr"))
    return rows


def _render_sentiment_rows(df: pd.DataFrame, page: int) -> list:
    if df.empty:
        return _no_results(len(_SENT_COLS))

    offset  = (page - 1) * PAGE_SIZE
    page_df = df.iloc[offset : offset + PAGE_SIZE]
    rows: list = []

    for local_i, (_, r) in enumerate(page_df.iterrows()):
        global_i = offset + local_i
        cnt      = int(r.get("article_count", 0))
        score    = _safe(r.get("avg_sentiment"))
        color    = _score_color(score)
        ticker   = r["ticker"]

        last_upd = ""
        try:
            ts = pd.Timestamp(r.get("calculated_at"))
            if ts is not pd.NaT:
                last_upd = ts.strftime("%m-%d %H:%M")
        except Exception:
            pass

        rows.append(html.Tr([
            html.Td(str(global_i + 1), className="scr-td scr-td-num scr-dim"),
            html.Td(html.A(ticker, href=f"/?keyword={ticker}", className="scr-ticker"),
                    className="scr-td"),
            html.Td(_badge(score), className="scr-td"),
            html.Td(
                f"{score:+.2f}" if score is not None else "—",
                className="scr-td scr-td-num scr-mono",
                style={"color": color},
            ),
            html.Td(_pct(r.get("bullish_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#00e676"}),
            html.Td(_pct(r.get("bearish_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#ff5252"}),
            html.Td(_pct(r.get("neutral_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#888888"}),
            html.Td(str(cnt), className="scr-td scr-td-num"),
            html.Td(last_upd, className="scr-td scr-td-center scr-dim"),
            html.Td(_trend_icon(r.get("momentum")),
                    className="scr-td scr-td-center",
                    style={"fontSize": "15px"}),
        ], className="scr-tr"))
    return rows


# ── Pagination ────────────────────────────────────────────────────────────

def _render_pagination(current_page: int, total_pages: int) -> list:
    if total_pages <= 1:
        return []

    items: list = [
        html.Span(
            f"Page {current_page} of {total_pages}",
            style={"fontSize": "11px", "color": "#555", "marginRight": "4px",
                   "whiteSpace": "nowrap"},
        ),
        html.Button(
            "← Prev",
            id={"type": "scr-pgbtn", "page": "prev"},
            className="page-btn",
            disabled=current_page <= 1,
            n_clicks=0,
        ),
    ]

    # Sliding window: up to 5 numbered buttons
    n       = total_pages
    start_p = max(1, min(current_page - 2, n - 4))
    end_p   = min(n, start_p + 4)

    for p in range(start_p, end_p + 1):
        items.append(html.Button(
            str(p),
            id={"type": "scr-pgbtn", "page": p},
            className="page-btn page-btn-active" if p == current_page else "page-btn",
            n_clicks=0,
        ))

    items.append(html.Button(
        "Next →",
        id={"type": "scr-pgbtn", "page": "next"},
        className="page-btn",
        disabled=current_page >= total_pages,
        n_clicks=0,
    ))
    return items


# ── Filter / sort helpers ─────────────────────────────────────────────────

def _apply_signal(df: pd.DataFrame, signal: str) -> pd.DataFrame:
    if signal == "bullish":
        return df[df["avg_sentiment"] >= 0.15]
    if signal == "bearish":
        return df[df["avg_sentiment"] <= -0.15]
    if signal == "unusual_volume":
        if df["volume"].isna().all():
            return df
        median_v = df["volume"].median()
        return df[df["volume"].fillna(0) > median_v * 2] if median_v else df
    if signal == "spike":
        return df[df["has_spike"] == True]
    return df


def _apply_sector_filter(df: pd.DataFrame, sector: str) -> pd.DataFrame:
    if not sector or sector == "all":
        return df
    mapped = df["ticker"].map(_SECTOR_MAP).fillna("")
    return df[mapped == sector]


def _apply_country_filter(df: pd.DataFrame, country: str) -> pd.DataFrame:
    if not country or country == "all":
        return df
    mapped = df["ticker"].map(_COUNTRY_MAP).fillna("USA")
    return df[mapped == country]


def _apply_mktcap_filter(df: pd.DataFrame, mktcap: str) -> pd.DataFrame:
    mc = df["market_cap"].fillna(0)
    if mktcap == "mega":
        return df[mc > 200_000_000_000]
    if mktcap == "large":
        return df[(mc >= 10_000_000_000) & (mc <= 200_000_000_000)]
    if mktcap == "mid":
        return df[(mc >= 2_000_000_000) & (mc < 10_000_000_000)]
    if mktcap == "small":
        return df[(mc >= 200_000_000) & (mc < 2_000_000_000)]
    if mktcap == "micro":
        return df[(mc > 0) & (mc < 200_000_000)]
    return df


def _apply_price_filter(df: pd.DataFrame, price: str) -> pd.DataFrame:
    if not price or price == "all":
        return df
    v = df["price"].fillna(0)
    masks = {
        "u5":      v < 5,
        "5_20":    (v >= 5)   & (v < 20),
        "20_50":   (v >= 20)  & (v < 50),
        "50_100":  (v >= 50)  & (v < 100),
        "100_500": (v >= 100) & (v < 500),
        "o500":    v >= 500,
    }
    mask = masks.get(price)
    return df[mask] if mask is not None else df


def _apply_chg_pct_filter(df: pd.DataFrame, chg: str) -> pd.DataFrame:
    if not chg or chg == "all":
        return df
    v = df["change_pct"].fillna(0)
    masks = {
        "up5":   v >= 5,
        "up2_5": (v >= 2)  & (v < 5),
        "up0_2": (v >= 0)  & (v < 2),
        "dn0_2": (v > -2)  & (v < 0),
        "dn2_5": (v >= -5) & (v < -2),
        "dn5":   v <= -5,
    }
    mask = masks.get(chg)
    return df[mask] if mask is not None else df


def _apply_volume_filter(df: pd.DataFrame, vol: str) -> pd.DataFrame:
    if not vol or vol == "all":
        return df
    v = df["volume"].fillna(0)
    masks = {
        "u500k":   v < 500_000,
        "500k_1m": (v >= 500_000)   & (v < 1_000_000),
        "1m_5m":   (v >= 1_000_000) & (v < 5_000_000),
        "o5m":     v >= 5_000_000,
    }
    mask = masks.get(vol)
    return df[mask] if mask is not None else df


def _apply_avg_volume_filter(df: pd.DataFrame, avg_vol: str) -> pd.DataFrame:
    if not avg_vol or avg_vol == "all" or "avg_volume" not in df.columns:
        return df
    v = df["avg_volume"].fillna(0)
    masks = {
        "u1m":   v < 1_000_000,
        "1m_5m": (v >= 1_000_000) & (v < 5_000_000),
        "o5m":   v >= 5_000_000,
    }
    mask = masks.get(avg_vol)
    return df[mask] if mask is not None else df


def _apply_sent_label_filter(df: pd.DataFrame, label: str) -> pd.DataFrame:
    if not label or label == "all":
        return df
    df2 = df.copy()
    df2["_lbl"] = df2["avg_sentiment"].apply(_score_to_label)
    return df2[df2["_lbl"] == label].drop(columns=["_lbl"])


def _apply_sent_score_filter(df: pd.DataFrame, score: str) -> pd.DataFrame:
    if not score or score == "all":
        return df
    v = df["avg_sentiment"].fillna(0)
    masks = {
        "sbull": v > 0.6,
        "bull":  (v >= 0.3)  & (v <= 0.6),
        "neut":  (v > -0.3)  & (v < 0.3),
        "bear":  (v >= -0.6) & (v <= -0.3),
        "sbear": v < -0.6,
    }
    mask = masks.get(score)
    return df[mask] if mask is not None else df


def _apply_trend_filter(df: pd.DataFrame, trend: str) -> pd.DataFrame:
    if not trend or trend == "all":
        return df
    m = df["momentum"].fillna("").str.lower()
    if trend == "improving":
        return df[m == "improving"]
    if trend == "declining":
        return df[m == "declining"]
    if trend == "stable":
        return df[~m.isin(["improving", "declining"])]
    return df


def _apply_spike_filter(df: pd.DataFrame, spike: str) -> pd.DataFrame:
    if not spike or spike == "all":
        return df
    if spike == "spike":
        return df[df["has_spike"] == True]
    if spike == "no_spike":
        return df[df["has_spike"] != True]
    return df


def _apply_sort(df: pd.DataFrame, signal: str, order: str, sort_dir: str) -> pd.DataFrame:
    asc = (sort_dir == "asc")
    if signal == "most_articles":
        return df.sort_values("article_count", ascending=False, na_position="last")
    col_map = {
        "avg_sentiment": "avg_sentiment",
        "price":         "price",
        "change_pct":    "change_pct",
        "volume":        "volume",
        "market_cap":    "market_cap",
        "article_count": "article_count",
    }
    col = col_map.get(order, "avg_sentiment")
    if col in df.columns:
        return df.sort_values(col, ascending=asc, na_position="last")
    return df


# ── Layout constants ──────────────────────────────────────────────────────

_SH = {"height": "36px"}

_SORT_BTN_STYLE = {
    "background":   "#1a1a1a",
    "color":        "#888888",
    "border":       "1px solid #2a2a2a",
    "borderRadius": "4px",
    "height":       "36px",
    "width":        "36px",
    "fontSize":     "16px",
    "cursor":       "pointer",
    "fontFamily":   "inherit",
    "flexShrink":   "0",
}

_REFRESH_BTN_STYLE = {
    **_SORT_BTN_STYLE,
    "width": "40px",
    "color": "#00d4ff",
}

# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        # ── Intervals & stores ─────────────────────────────────────────────
        dcc.Interval(id="screener-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="screener-sort-dir",    data="desc"),
        dcc.Store(id="screener-page",        data=1),
        dcc.Store(id="screener-filter-tab",  data="descriptive"),

        # ── Top filter bar ─────────────────────────────────────────────────
        html.Div(
            className="filter-bar",
            style={"flexWrap": "nowrap", "marginBottom": "8px"},
            children=[
                dbc.Select(
                    id="screener-signal",
                    options=SIGNAL_OPTIONS,
                    value="all",
                    className="filter-select",
                    style={"minWidth": "165px", **_SH},
                ),
                dbc.Select(
                    id="screener-order",
                    options=ORDER_OPTIONS,
                    value="avg_sentiment",
                    className="filter-select",
                    style={"minWidth": "150px", **_SH},
                ),
                html.Button(
                    "↓",
                    id="screener-sort-dir-btn",
                    n_clicks=0,
                    style=_SORT_BTN_STYLE,
                    title="Toggle sort direction",
                ),
                dcc.Input(
                    id="screener-search",
                    placeholder="🔍  Search ticker…",
                    debounce=True,
                    className="filter-input",
                    style={"height": "36px", "flex": "1", "minWidth": "120px"},
                ),
                html.Button(
                    "↻",
                    id="screener-refresh-btn",
                    n_clicks=0,
                    style=_REFRESH_BTN_STYLE,
                    title="Refresh now",
                ),
            ],
        ),

        # ── Collapsible filter panel ───────────────────────────────────────
        html.Div(
            style={"marginBottom": "8px"},
            children=[
                html.Button(
                    "▾ Filters",
                    id="screener-filter-btn",
                    n_clicks=0,
                    style={
                        "background":  "transparent",
                        "border":      "none",
                        "color":       "#888888",
                        "fontSize":    "12px",
                        "cursor":      "pointer",
                        "fontFamily":  "inherit",
                        "padding":     "4px 0",
                    },
                ),
            ],
        ),
        dbc.Collapse(
            id="screener-filter-collapse",
            is_open=False,
            children=[
                html.Div(
                    style={"background": "#101010", "border": "1px solid #1c1c1c",
                           "borderRadius": "6px", "padding": "14px 18px 10px",
                           "marginBottom": "10px"},
                    children=[
                        # ── Filter tab buttons ────────────────────────────
                        html.Div(
                            style={"display": "flex", "gap": "0",
                                   "borderBottom": "1px solid #252525",
                                   "marginBottom": "12px"},
                            children=[
                                html.Button(
                                    "Descriptive",
                                    id="screener-ftab-desc",
                                    n_clicks=0,
                                    className="scr-ftab scr-ftab-active",
                                ),
                                html.Button(
                                    "Sentiment",
                                    id="screener-ftab-sent",
                                    n_clicks=0,
                                    className="scr-ftab",
                                ),
                            ],
                        ),

                        # ── Descriptive panel (always in DOM) ─────────────
                        html.Div(
                            id="screener-ftab-desc-panel",
                            children=[
                                # Row 1: Sector · Market Cap · Country
                                html.Div(
                                    className="filter-row",
                                    children=[
                                        html.Div([
                                            html.Label("Sector",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-sector",
                                                options=SECTOR_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Market Cap",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-mktcap",
                                                options=MKTCAP_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Country",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-country",
                                                options=COUNTRY_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                    ],
                                ),
                                # Row 2: Price · Change% · Volume
                                html.Div(
                                    className="filter-row",
                                    children=[
                                        html.Div([
                                            html.Label("Price ($)",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-price",
                                                options=PRICE_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Change %",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-chg-pct",
                                                options=CHG_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Volume",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-volume",
                                                options=VOLUME_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                    ],
                                ),
                                # Row 3: Average Volume
                                html.Div(
                                    className="filter-row",
                                    children=[
                                        html.Div([
                                            html.Label("Average Volume",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-avg-volume",
                                                options=AVG_VOL_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                    ],
                                ),
                            ],
                        ),

                        # ── Sentiment panel (always in DOM, hidden by default) ─
                        html.Div(
                            id="screener-ftab-sent-panel",
                            style={"display": "none"},
                            children=[
                                # Row 1: Sentiment label · Score · Time Window
                                html.Div(
                                    className="filter-row",
                                    children=[
                                        html.Div([
                                            html.Label("Sentiment",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-sent-label",
                                                options=SENT_LABEL_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Sentiment Score",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-sent-score",
                                                options=SENT_SCORE_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Time Window",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-window",
                                                options=TIME_WINDOW_OPTIONS,
                                                value="4hr",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                    ],
                                ),
                                # Row 2: Min Articles · Trend · Spike Alert
                                html.Div(
                                    className="filter-row",
                                    children=[
                                        html.Div([
                                            html.Label("Min Articles",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-min-articles",
                                                options=MIN_ARTICLES_OPTIONS,
                                                value=0,
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Trend",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-trend",
                                                options=TREND_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                        html.Div([
                                            html.Label("Spike Alert",
                                                       className="filter-label"),
                                            dbc.Select(
                                                id="screener-spike",
                                                options=SPIKE_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={**_SH},
                                            ),
                                        ], className="filter-item"),
                                    ],
                                ),
                            ],
                        ),
                        # ── Reset button ──────────────────────────────────
                        html.Div(
                            style={"display": "flex", "justifyContent": "flex-end",
                                   "marginTop": "12px"},
                            children=[
                                html.Button(
                                    "Reset Filters",
                                    id="screener-reset-btn",
                                    n_clicks=0,
                                    style={
                                        "background":   "transparent",
                                        "border":       "1px solid #2e2e2e",
                                        "color":        "#666",
                                        "fontSize":     "12px",
                                        "padding":      "5px 14px",
                                        "borderRadius": "4px",
                                        "cursor":       "pointer",
                                        "fontFamily":   "inherit",
                                        "transition":   "border-color 0.12s, color 0.12s",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),

        html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 10px", "opacity": "1"}),

        # ── Info bar: count (left) + pagination (right) ────────────────────
        html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "alignItems": "center", "marginBottom": "10px", "padding": "0 2px"},
            children=[
                html.Span(id="screener-count", className="count-label"),
                html.Div(id="screener-pagination", className="pagination-bar",
                         style={"padding": "0", "justifyContent": "flex-end"}),
            ],
        ),

        # ── Results tabs ───────────────────────────────────────────────────
        dbc.Tabs(
            id="screener-result-tabs",
            active_tab="tab-overview",
            children=[
                dbc.Tab(label="Overview", tab_id="tab-overview", children=[
                    html.Div(
                        className="articles-table",
                        style={"overflowX": "auto", "padding": "0"},
                        children=[html.Table(
                            className="scr-table",
                            children=[
                                html.Thead(html.Tr([
                                    html.Th(lbl, style={"width": w, "textAlign": a})
                                    for lbl, w, a in _OV_COLS
                                ])),
                                html.Tbody(id="screener-overview-rows"),
                            ],
                        )],
                    ),
                ]),
                dbc.Tab(label="Sentiment Detail", tab_id="tab-sentiment", children=[
                    html.Div(
                        className="articles-table",
                        style={"overflowX": "auto", "padding": "0"},
                        children=[html.Table(
                            className="scr-table",
                            children=[
                                html.Thead(html.Tr([
                                    html.Th(lbl, style={"width": w, "textAlign": a})
                                    for lbl, w, a in _SENT_COLS
                                ])),
                                html.Tbody(id="screener-sentiment-rows"),
                            ],
                        )],
                    ),
                ]),
            ],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("screener-interval", "interval"),
    Input("screener-interval",  "n_intervals"),
)
def _update_interval(_):
    return 60_000 if is_market_hours() else 300_000


@callback(
    Output("screener-sort-dir",     "data"),
    Output("screener-sort-dir-btn", "children"),
    Input("screener-sort-dir-btn",  "n_clicks"),
    State("screener-sort-dir",      "data"),
    prevent_initial_call=True,
)
def _toggle_sort_dir(_, current):
    new_dir = "asc" if current == "desc" else "desc"
    icon    = "↑" if new_dir == "asc" else "↓"
    return new_dir, icon


@callback(
    Output("screener-filter-collapse", "is_open"),
    Output("screener-filter-btn",      "children"),
    Input("screener-filter-btn",       "n_clicks"),
    State("screener-filter-collapse",  "is_open"),
    prevent_initial_call=True,
)
def _toggle_filter_panel(_, is_open):
    new_open = not is_open
    label    = "▴ Filters" if new_open else "▾ Filters"
    return new_open, label


@callback(
    Output("screener-ftab-desc-panel", "style"),
    Output("screener-ftab-sent-panel", "style"),
    Output("screener-ftab-desc",       "className"),
    Output("screener-ftab-sent",       "className"),
    Input("screener-ftab-desc",        "n_clicks"),
    Input("screener-ftab-sent",        "n_clicks"),
    prevent_initial_call=True,
)
def _switch_filter_tab(_, __):
    triggered = ctx.triggered_id
    if triggered == "screener-ftab-sent":
        return (
            {"display": "none"}, {"display": "block"},
            "scr-ftab", "scr-ftab scr-ftab-active",
        )
    return (
        {"display": "block"}, {"display": "none"},
        "scr-ftab scr-ftab-active", "scr-ftab",
    )


_ALL_FILTER_INPUTS = [
    Input("screener-signal",      "value"),
    Input("screener-order",       "value"),
    Input("screener-sort-dir",    "data"),
    Input("screener-search",      "value"),
    Input("screener-sector",      "value"),
    Input("screener-mktcap",      "value"),
    Input("screener-country",     "value"),
    Input("screener-price",       "value"),
    Input("screener-chg-pct",     "value"),
    Input("screener-volume",      "value"),
    Input("screener-avg-volume",  "value"),
    Input("screener-sent-label",  "value"),
    Input("screener-sent-score",  "value"),
    Input("screener-window",      "value"),
    Input("screener-min-articles","value"),
    Input("screener-trend",       "value"),
    Input("screener-spike",       "value"),
]


@callback(
    Output("screener-page", "data"),
    *_ALL_FILTER_INPUTS,
    prevent_initial_call=True,
)
def _reset_screener_page(*_):
    return 1


@callback(
    Output("screener-sector",       "value"),
    Output("screener-mktcap",       "value"),
    Output("screener-country",      "value"),
    Output("screener-price",        "value"),
    Output("screener-chg-pct",      "value"),
    Output("screener-volume",       "value"),
    Output("screener-avg-volume",   "value"),
    Output("screener-sent-label",   "value"),
    Output("screener-sent-score",   "value"),
    Output("screener-window",       "value"),
    Output("screener-min-articles", "value"),
    Output("screener-trend",        "value"),
    Output("screener-spike",        "value"),
    Output("screener-signal",       "value"),
    Output("screener-order",        "value"),
    Output("screener-search",       "value"),
    Input("screener-reset-btn",     "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_):
    return ("all", "all", "all", "all", "all", "all", "all",
            "all", "all", "4hr", 0, "all", "all",
            "all", "avg_sentiment", "")


@callback(
    Output("screener-page", "data", allow_duplicate=True),
    Input({"type": "scr-pgbtn", "page": ALL}, "n_clicks"),
    State("screener-page", "data"),
    prevent_initial_call=True,
)
def _handle_screener_page_click(n_clicks_list, current_page):
    if not any(n for n in (n_clicks_list or [])):
        raise PreventUpdate
    triggered = ctx.triggered_id
    if triggered is None:
        raise PreventUpdate
    page_val = triggered["page"]
    cur = int(current_page or 1)
    if page_val == "prev":
        return max(1, cur - 1)
    if page_val == "next":
        return cur + 1
    return int(page_val)


@callback(
    Output("screener-overview-rows",  "children"),
    Output("screener-sentiment-rows", "children"),
    Output("screener-count",          "children"),
    Output("screener-pagination",     "children"),
    Input("screener-interval",        "n_intervals"),
    Input("screener-refresh-btn",     "n_clicks"),
    *_ALL_FILTER_INPUTS,
    Input("screener-page",            "data"),
    Input("url",                      "pathname"),
)
def _update_screener(n, refresh_clicks,
                     signal, order, sort_dir, search,
                     sector, mktcap, country, price, chg_pct, volume, avg_volume,
                     sent_label, sent_score, window, min_articles, trend, spike,
                     page, pathname):
    if pathname not in (None, "/screener"):
        raise PreventUpdate

    signal       = signal    or "all"
    order        = order     or "avg_sentiment"
    sort_dir     = sort_dir  or "desc"
    window       = window    or "4hr"
    min_articles = int(min_articles or 0)
    page         = max(1, int(page or 1))

    df = _fetch_data(window, min_articles)

    if df.empty:
        empty_msg = [html.Div(
            "No data yet — articles are being collected. Check back in a few minutes.",
            style={"padding": "32px", "textAlign": "center",
                   "color": "#555", "fontSize": "14px"},
        )]
        return empty_msg, empty_msg, "0 tickers", []

    # ── Apply filters ──────────────────────────────────────────────────────
    df = _apply_signal(df, signal)

    if search:
        df = df[df["ticker"].str.upper().str.startswith(search.strip().upper())]

    df = _apply_sector_filter(df, sector or "all")
    df = _apply_country_filter(df, country or "all")
    df = _apply_mktcap_filter(df, mktcap or "all")
    df = _apply_price_filter(df, price or "all")
    df = _apply_chg_pct_filter(df, chg_pct or "all")
    df = _apply_volume_filter(df, volume or "all")
    df = _apply_avg_volume_filter(df, avg_volume or "all")
    df = _apply_sent_label_filter(df, sent_label or "all")
    df = _apply_sent_score_filter(df, sent_score or "all")
    df = _apply_trend_filter(df, trend or "all")
    df = _apply_spike_filter(df, spike or "all")

    # ── Sort ───────────────────────────────────────────────────────────────
    df = _apply_sort(df, signal, order, sort_dir)
    df = df.reset_index(drop=True)

    # ── Pagination ─────────────────────────────────────────────────────────
    total       = len(df)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page        = min(page, total_pages)
    start       = (page - 1) * PAGE_SIZE + 1
    end         = min(page * PAGE_SIZE, total)
    time_str    = now_et().strftime("%H:%M ET")

    if total == 0:
        count_el = html.Span("0 tickers matched", style={"color": "#555"})
    else:
        count_el = html.Span([
            html.Span(f"Showing {start:,}–{end:,} of {total:,} tickers",
                      style={"color": "#888"}),
            html.Span(f"  ·  Updated {time_str}", style={"color": "#3a3a3a"}),
        ])

    overview_rows  = _render_overview_rows(df, page)
    sentiment_rows = _render_sentiment_rows(df, page)
    pagination     = _render_pagination(page, total_pages)

    return overview_rows, sentiment_rows, count_el, pagination
