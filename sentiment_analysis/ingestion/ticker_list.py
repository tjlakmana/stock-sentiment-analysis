"""
S&P 500 constituent tickers, common ETFs, and a three-pass extraction utility.

`extract_tickers(text)` is the public API used by both the Twitter and RSS
ingestors to tag records with relevant ticker symbols.

Three-pass extraction strategy
-------------------------------
Pass 1  $CASHTAG     — explicit ``$AAPL`` / ``$BRK.B`` format (Twitter / SA)
Pass 2  UPPERCASE    — bare uppercase tokens (``AAPL``, ``NVDA``) already in
                       the text, cross-referenced against the known ticker set
Pass 3  Company name — case-insensitive word-boundary search for company names
                       (``Apple``, ``Tesla``, ``Nvidia``) mapped to their ticker
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Full S&P 500 + common ETF / mega-cap ticker universe
# ---------------------------------------------------------------------------

SP500_TICKERS: List[str] = [
    # A
    "A", "AAL", "AAP", "AAPL", "ABBV", "ABC", "ABMD", "ABT", "ACN", "ADBE",
    "ADI", "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ",
    "AJG", "AKAM", "ALB", "ALGN", "ALK", "ALL", "ALLE", "AMAT", "AMCR", "AMD",
    "AME", "AMGN", "AMP", "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA",
    "APD", "APH", "APTV", "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXP",
    "AZO",
    # B
    "BA", "BAC", "BALL", "BAX", "BBWI", "BBY", "BDX", "BEN", "BIIB", "BIO",
    "BK", "BKNG", "BKR", "BLK", "BMY", "BR", "BSX", "BWA", "BXP",
    # C
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI",
    "CINF", "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC",
    "CNP", "COF", "COO", "COP", "COST", "CPB", "CPRT", "CPT", "CRL", "CRM",
    "CSCO", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA", "CVS", "CVX", "CZR",
    # D
    "D", "DAL", "DD", "DE", "DFS", "DG", "DGX", "DHI", "DHR", "DIS", "DISH",
    "DLR", "DLTR", "DOV", "DOW", "DPZ", "DRI", "DTE", "DUK", "DVA", "DVN",
    "DXC", "DXCM",
    # E
    "EA", "EBAY", "ECL", "ED", "EFX", "EIX", "EL", "EMN", "EMR", "EOG",
    "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN", "ETR", "ETSY", "EVRG",
    "EW", "EXC", "EXPD", "EXPE", "EXR",
    # F
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FIS", "FISV",
    "FITB", "FLT", "FMC", "FOX", "FOXA", "FRT", "FTNT", "FTV",
    # G
    "GD", "GE", "GILD", "GIS", "GL", "GLW", "GM", "GNRC", "GOOG", "GOOGL",
    "GPC", "GPN", "GPS", "GRMN", "GS", "GWW",
    # H
    "HAL", "HAS", "HBAN", "HCA", "HD", "HES", "HIG", "HII", "HLT", "HOLX",
    "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUM", "HWM",
    # I
    "IBM", "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU",
    "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ",
    # J
    "J", "JBHT", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    # K
    "K", "KEY", "KEYS", "KHC", "KIM", "KLAC", "KMB", "KMI", "KMX", "KO", "KR",
    # L
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNC",
    "LNT", "LOW", "LRCX", "LUMN", "LUV", "LVS", "LW", "LYB", "LYV",
    # M
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRO", "MS", "MSCI", "MSFT",
    "MSI", "MTB", "MTCH", "MTD", "MU",
    # N
    "NCLH", "NDAQ", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW", "NRG",
    "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWL", "NWS", "NWSA", "NXPI",
    # O
    "O", "OKE", "OMC", "ON", "ORCL", "ORLY", "OXY",
    # P
    "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEAK", "PEG", "PEP", "PFE",
    "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PKI", "PLD", "PM", "PNC", "PNR",
    "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PWR", "PXD",
    "PYPL",
    # Q
    "QCOM", "QRVO",
    # R
    "RCL", "RE", "REG", "REGN", "RF", "RHI", "RJF", "RL", "RMD", "ROK",
    "ROL", "ROP", "ROST", "RSG", "RTX",
    # S
    "SBAC", "SBUX", "SCHW", "SEE", "SHW", "SJM", "SLB", "SNA", "SNPS", "SO",
    "SPG", "SPGI", "SRE", "STE", "STT", "STX", "STZ", "SWK", "SWKS", "SYF",
    "SYK", "SYY",
    # T
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TJX", "TMO", "TMUS", "TPR", "TRMB", "TROW", "TRV", "TSCO", "TSLA", "TSN",
    "TT", "TTWO", "TXN", "TXT", "TYL",
    # U
    "UA", "UAA", "UAL", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI",
    "USB",
    # V
    "V", "VFC", "VLO", "VMC", "VNO", "VRSK", "VRSN", "VRTX", "VTR", "VTRS",
    "VZ",
    # W
    "WAB", "WAT", "WBA", "WBD", "WDAY", "WDC", "WEC", "WELL", "WFC", "WHR",
    "WM", "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
    # X–Z
    "XEL", "XOM", "XRAY", "XYL", "YUM", "ZBH", "ZBRA", "ZION", "ZTS",
    # ------------------------------------------------------------------ #
    # Broad-market & sector ETFs                                           #
    # ------------------------------------------------------------------ #
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "LQD",
    "VTI", "VOO", "VEA", "VWO", "EFA", "EEM",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    # ------------------------------------------------------------------ #
    # High-profile tickers frequently mentioned in financial media         #
    # ------------------------------------------------------------------ #
    "PLTR", "RIVN", "LCID", "SOFI", "GME", "AMC", "BB", "NOK",
    "COIN", "HOOD", "ROKU", "SNAP", "UBER", "LYFT", "ABNB", "DASH",
    "PINS", "TWTR", "SHOP", "SQ", "PYPL", "AFRM", "UPST",
]

# Frozen set for O(1) membership tests during extraction
SP500_TICKER_SET: frozenset = frozenset(SP500_TICKERS)

# ---------------------------------------------------------------------------
# Pass 1 & 2 — compiled regex patterns
# ---------------------------------------------------------------------------

# Explicit $CASHTAG format — highest confidence (common on Twitter / SeekingAlpha)
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")

# Bare uppercase token — cross-referenced against SP500_TICKER_SET
# Catches inline tickers like "AAPL" in "Apple (AAPL) reports..."
_BARE_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Tokens that look like tickers but aren't — filtered out in Pass 2
_STOP_WORDS: frozenset = frozenset({
    "A", "I", "AN", "OR", "AND", "THE", "IN", "ON", "AT", "BY", "FOR",
    "OF", "TO", "IS", "ARE", "WAS", "BE", "AS", "IF", "UP", "DO",
    "US", "UK", "EU", "UN", "CEO", "CFO", "COO", "IPO", "ETF", "SEC",
    "FED", "GDP", "CPI", "EPS", "PEG", "PE", "TTM", "YTD", "QoQ", "YoY",
    "AI", "ML", "API", "LLC", "INC", "LTD", "NYSE", "NASDAQ", "OTC",
    "FDIC", "IMF", "WTO", "Q1", "Q2", "Q3", "Q4", "YE", "H1", "H2",
})

# ---------------------------------------------------------------------------
# Pass 3 — company name → ticker map
# ---------------------------------------------------------------------------
# Keys are the lowercase company name as it typically appears in news prose.
# Word-boundary regex patterns are pre-compiled below for performance.
# Ordering within the dict doesn't matter; seen-set deduplication handles
# aliases that resolve to the same ticker (e.g. "google" and "alphabet").

_COMPANY_TICKER_MAP: Dict[str, str] = {
    # ---- Mega-cap tech -------------------------------------------------- #
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta platforms": "META",
    "meta": "META",
    "nvidia": "NVDA",
    "netflix": "NFLX",
    "adobe": "ADBE",
    "salesforce": "CRM",
    "oracle": "ORCL",
    "ibm": "IBM",
    "cisco": "CSCO",
    "servicenow": "NOW",
    "palantir": "PLTR",
    # ---- EV / Automotive ------------------------------------------------ #
    "tesla": "TSLA",
    "rivian": "RIVN",
    "lucid motors": "LCID",
    "lucid group": "LCID",
    "general motors": "GM",
    "ford motor": "F",
    "stellantis": "STLA",
    # ---- Consumer / platform tech --------------------------------------- #
    "paypal": "PYPL",
    "shopify": "SHOP",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "uber": "UBER",
    "lyft": "LYFT",
    "airbnb": "ABNB",
    "doordash": "DASH",
    "pinterest": "PINS",
    "spotify": "SPOT",
    "roku": "ROKU",
    "zoom": "ZM",
    "snapchat": "SNAP",
    "roblox": "RBLX",
    "unity": "U",
    # ---- Semiconductors ------------------------------------------------- #
    "intel": "INTC",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "texas instruments": "TXN",
    "micron technology": "MU",
    "micron": "MU",
    "amd": "AMD",
    "arm holdings": "ARM",
    "marvell": "MRVL",
    "on semiconductor": "ON",
    # ---- Finance / banking ---------------------------------------------- #
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "berkshire hathaway": "BRK.B",
    "blackrock": "BLK",
    "citigroup": "C",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "american express": "AXP",
    "visa": "V",
    "mastercard": "MA",
    "charles schwab": "SCHW",
    "coinbase": "COIN",
    "fidelity national": "FIS",
    # ---- Energy --------------------------------------------------------- #
    "exxonmobil": "XOM",
    "exxon mobil": "XOM",
    "exxon": "XOM",
    "chevron": "CVX",
    "conocophillips": "COP",
    "pioneer natural": "PXD",
    "occidental": "OXY",
    "schlumberger": "SLB",
    "halliburton": "HAL",
    # ---- Healthcare / pharma -------------------------------------------- #
    "pfizer": "PFE",
    "abbvie": "ABBV",
    "merck": "MRK",
    "eli lilly": "LLY",
    "moderna": "MRNA",
    "unitedhealth": "UNH",
    "cvs health": "CVS",
    "johnson & johnson": "JNJ",
    "bristol myers": "BMY",
    "amgen": "AMGN",
    "gilead": "GILD",
    "biogen": "BIIB",
    "regeneron": "REGN",
    "illumina": "ILMN",
    # ---- Industrial / aerospace ----------------------------------------- #
    "boeing": "BA",
    "caterpillar": "CAT",
    "honeywell": "HON",
    "lockheed martin": "LMT",
    "raytheon": "RTX",
    "northrop grumman": "NOC",
    "general dynamics": "GD",
    "union pacific": "UNP",
    "fedex": "FDX",
    "united parcel service": "UPS",
    "3m": "MMM",
    # ---- Telecom -------------------------------------------------------- #
    "verizon": "VZ",
    "at&t": "T",
    "t-mobile": "TMUS",
    "comcast": "CMCSA",
    "charter communications": "CHTR",
    # ---- Retail / consumer ---------------------------------------------- #
    "walmart": "WMT",
    "costco": "COST",
    "nike": "NKE",
    "starbucks": "SBUX",
    "mcdonald's": "MCD",
    "mcdonalds": "MCD",
    "mcdonald": "MCD",
    "walt disney": "DIS",
    "disney": "DIS",
    "home depot": "HD",
    "lowe's": "LOW",
    "lowes": "LOW",
    "procter & gamble": "PG",
    "procter and gamble": "PG",
    "coca-cola": "KO",
    "coca cola": "KO",
    "pepsico": "PEP",
    "pepsi": "PEP",
    "colgate": "CL",
    "kimberly-clark": "KMB",
}

# Pre-compile one regex per company name for Pass 3.
# re.escape handles apostrophes, hyphens, & and other punctuation in names.
_COMPANY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE), ticker)
    for name, ticker in _COMPANY_TICKER_MAP.items()
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_tickers(text: str) -> List[str]:
    """
    Extract stock ticker symbols from free-form text.

    Three-pass strategy (highest → lowest confidence):

    Pass 1 — ``$AAPL`` / ``$BRK.B`` cashtag format.
             Common on Twitter and SeekingAlpha headlines.

    Pass 2 — Bare uppercase tokens (``AAPL``, ``NVDA``, ``TSLA``) that are
             already present in the text, cross-referenced against the known
             S&P 500 + ETF universe and filtered through a stop-word list.
             Catches inline tickers written as ``Apple (AAPL) reports…``

    Pass 3 — Case-insensitive company name matching using pre-compiled
             word-boundary patterns.  Catches the most common RSS pattern
             where articles mention ``Apple``, ``Tesla``, ``Nvidia`` without
             ever printing the ticker symbol.

    Deduplication via a seen-set preserves first-occurrence order and
    prevents the same ticker being added by multiple passes or aliases.

    Args:
        text: Raw article headline, summary, or tweet text.

    Returns:
        Ordered, deduplicated list of ticker symbols found in *text*.
    """
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    # ------------------------------------------------------------------
    # Pass 1 — explicit $CASHTAG tokens
    # ------------------------------------------------------------------
    for match in _CASHTAG_RE.finditer(text):
        ticker = match.group(1).upper()
        if ticker not in seen:
            found.append(ticker)
            seen.add(ticker)

    # ------------------------------------------------------------------
    # Pass 2 — bare uppercase tokens cross-referenced with known universe
    # ------------------------------------------------------------------
    for match in _BARE_TICKER_RE.finditer(text):
        ticker = match.group(1).upper()
        if (
            ticker in SP500_TICKER_SET
            and ticker not in _STOP_WORDS
            and ticker not in seen
        ):
            found.append(ticker)
            seen.add(ticker)

    # ------------------------------------------------------------------
    # Pass 3 — company name mentions (case-insensitive, word boundaries)
    # ------------------------------------------------------------------
    for pattern, ticker in _COMPANY_PATTERNS:
        if ticker not in seen and pattern.search(text):
            found.append(ticker)
            seen.add(ticker)

    return found
