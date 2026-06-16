"""
S&P 500 constituent tickers, common ETFs, and a three-pass extraction utility.

`extract_tickers(text)` is the public API used by the RSS ingestor to tag
records with relevant ticker symbols.

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
    "A", "AAL", "AAP", "AAPL", "ABBV", "ABC", "ABMD", "ABT", "ACN", "ACGL",
    "ADBE", "ADI", "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG",
    "AIZ", "AJG", "AKAM", "ALB", "ALGN", "ALK", "ALL", "ALLE", "AMAT", "AMCR",
    "AMD", "AME", "AMGN", "AMP", "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS",
    "APA", "APD", "APH", "APTV", "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK",
    "AXP", "AXON", "AZO",
    # B
    "BA", "BAC", "BALL", "BAX", "BBWI", "BBY", "BCR", "BDX", "BEN", "BF.B",
    "BG", "BIIB", "BIO", "BK", "BKNG", "BKR", "BLK", "BLDR", "BMY", "BR",
    "BRO", "BSX", "BWA", "BX", "BXP",
    # C
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI",
    "CINF", "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC",
    "CNP", "COF", "COO", "COP", "COR", "COST", "CPB", "CPRT", "CPT", "CRL",
    "CRM", "CSCO", "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA",
    "CVS", "CVX", "CZR",
    # D
    "D", "DAL", "DAY", "DD", "DE", "DECK", "DFS", "DG", "DGX", "DHI", "DHR",
    "DIS", "DISH", "DLR", "DLTR", "DOC", "DOV", "DOW", "DPZ", "DRI", "DTE",
    "DUK", "DVA", "DVN", "DXC", "DXCM",
    # E
    "EA", "EBAY", "ECL", "ED", "EFX", "EG", "EIX", "EL", "ELV", "EMN", "EMR",
    "ENPH", "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN", "ETR",
    "ETSY", "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR",
    # F
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FI", "FICO",
    "FIS", "FISV", "FITB", "FLT", "FMC", "FOX", "FOXA", "FRT", "FSLR",
    "FTNT", "FTV",
    # G
    "GD", "GE", "GDDY", "GEHC", "GEN", "GEV", "GILD", "GIS", "GL", "GLW",
    "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GPS", "GRMN", "GS", "GWW",
    # H
    "HAL", "HAS", "HBAN", "HCA", "HD", "HES", "HIG", "HII", "HLT", "HOLX",
    "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM",
    # I
    "IBM", "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU",
    "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ",
    # J
    "J", "JBL", "JBHT", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    # K
    "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KLAC", "KMB", "KMI", "KMX",
    "KO", "KR", "KVUE",
    # L
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNC",
    "LNT", "LOW", "LRCX", "LULU", "LUMN", "LUV", "LVS", "LW", "LYB", "LYV",
    # M
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRO", "MRNA", "MS", "MSCI",
    "MSFT", "MSI", "MTB", "MTCH", "MTD", "MU",
    # N
    "NCLH", "NDAQ", "NDSN", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW",
    "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWL", "NWS", "NWSA",
    "NXPI",
    # O
    "O", "ODFL", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY",
    # P
    "PANW", "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEAK", "PEG", "PEP",
    "PFE", "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PKI", "PLD", "PM",
    "PNC", "PNR", "PNW", "PODD", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX",
    "PTC", "PTVE", "PWR", "PXD", "PYPL",
    # Q
    "QCOM", "QRVO",
    # R
    "RCL", "RE", "REG", "REGN", "RF", "RHI", "RJF", "RL", "RMD", "ROK",
    "ROL", "ROP", "ROST", "RSG", "RTX", "RVTY",
    # S
    "SBAC", "SBUX", "SCHW", "SEE", "SHW", "SJM", "SLB", "SNA", "SNPS",
    "SO", "SOLV", "SPG", "SPGI", "SRE", "STE", "STLD", "STT", "STX", "STZ",
    "SWK", "SWKS", "SYF", "SYK", "SYY",
    # T
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TJX", "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO",
    "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT", "TYL",
    # U
    "UA", "UAA", "UAL", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI",
    "USB",
    # V
    "V", "VFC", "VICI", "VLO", "VMC", "VNO", "VRSK", "VRSN", "VRTX", "VST",
    "VTR", "VTRS", "VZ",
    # W
    "WAB", "WAT", "WBA", "WBD", "WDAY", "WDC", "WEC", "WELL", "WFC", "WHR",
    "WLK", "WM", "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
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

_COMPANY_TICKER_MAP: Dict[str, str] = {
    # ── Mega-cap tech ──────────────────────────────────────────────────── #
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
    "autodesk": "ADSK",
    "cadence design": "CDNS",
    "synopsys": "SNPS",
    "ansys": "ANSS",
    "arista networks": "ANET",
    "f5": "FFIV",
    "godaddy": "GDDY",
    "costar group": "CSGP",
    "epam systems": "EPAM",
    # ── Semiconductors ─────────────────────────────────────────────────── #
    "intel": "INTC",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "texas instruments": "TXN",
    "micron technology": "MU",
    "micron": "MU",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "applied materials": "AMAT",
    "lam research": "LRCX",
    "kla": "KLAC",
    "analog devices": "ADI",
    "microchip technology": "MCHP",
    "nxp semiconductors": "NXPI",
    "skyworks solutions": "SWKS",
    "qorvo": "QRVO",
    "arm holdings": "ARM",
    "marvell": "MRVL",
    "on semiconductor": "ON",
    "monolithic power": "MPWR",
    "teradyne": "TER",
    "enphase energy": "ENPH",
    "first solar": "FSLR",
    # ── Finance / banking ──────────────────────────────────────────────── #
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "jpmorgan chase": "JPM",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "berkshire hathaway": "BRK.B",
    "blackrock": "BLK",
    "blackstone": "BX",
    "citigroup": "C",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "american express": "AXP",
    "visa": "V",
    "mastercard": "MA",
    "charles schwab": "SCHW",
    "fidelity national": "FIS",
    "fidelity national information": "FIS",
    "fiserv": "FI",
    "capital one": "COF",
    "discover financial": "DFS",
    "synchrony financial": "SYF",
    "citizens financial": "CFG",
    "keycorp": "KEY",
    "regions financial": "RF",
    "fifth third bancorp": "FITB",
    "huntington bancshares": "HBAN",
    "mt bank": "MTB",
    "us bancorp": "USB",
    "truist financial": "TFC",
    "pnc financial": "PNC",
    "state street": "STT",
    "northern trust": "NTRS",
    "raymond james": "RJF",
    "ameriprise": "AMP",
    "invesco": "IVZ",
    "t rowe price": "TROW",
    "franklin resources": "BEN",
    "principal financial": "PFG",
    "allstate": "ALL",
    "travelers companies": "TRV",
    "hartford financial": "HIG",
    "chubb": "CB",
    "aon": "AON",
    "marsh mclennan": "MMC",
    "arthur j gallagher": "AJG",
    "cincinnati financial": "CINF",
    "assurant": "AIZ",
    "everest group": "EG",
    "globe life": "GL",
    "lincoln national": "LNC",
    "metlife": "MET",
    "prudential financial": "PRU",
    "loews": "L",
    "wr berkley": "WRB",
    "w r berkley": "WRB",
    "arch capital": "ACGL",
    "intercontinental exchange": "ICE",
    "cboe global markets": "CBOE",
    "cme group": "CME",
    "nasdaq": "NDAQ",
    "marketaxess": "MKTX",
    "msci": "MSCI",
    "s&p global": "SPGI",
    "moody's": "MCO",
    "moodys": "MCO",
    "factset": "FDS",
    "fair isaac": "FICO",
    "verisk analytics": "VRSK",
    "broadridge": "BR",
    "automatic data processing": "ADP",
    "jack henry associates": "JKHY",
    "fidelity national financial": "FNF",
    # ── Energy ─────────────────────────────────────────────────────────── #
    "exxonmobil": "XOM",
    "exxon mobil": "XOM",
    "exxon": "XOM",
    "chevron": "CVX",
    "conocophillips": "COP",
    "pioneer natural": "PXD",
    "pioneer natural resources": "PXD",
    "occidental": "OXY",
    "occidental petroleum": "OXY",
    "schlumberger": "SLB",
    "halliburton": "HAL",
    "baker hughes": "BKR",
    "eog resources": "EOG",
    "diamondback energy": "FANG",
    "devon energy": "DVN",
    "coterra energy": "CTRA",
    "marathon petroleum": "MPC",
    "marathon oil": "MRO",
    "phillips 66": "PSX",
    "valero energy": "VLO",
    "hess": "HES",
    "apache": "APA",
    "oneok": "OKE",
    "targa resources": "TRGP",
    "kinder morgan": "KMI",
    "williams companies": "WMB",
    "eqt": "EQT",
    "coterra": "CTRA",
    "dte energy": "DTE",
    "duke energy": "DUK",
    "dominion energy": "D",
    "southern company": "SO",
    "exelon": "EXC",
    "public service enterprise": "PEG",
    "nextera energy": "NEE",
    "eversource energy": "ES",
    "consolidated edison": "ED",
    "entergy": "ETR",
    "firstenergy": "FE",
    "evergy": "EVRG",
    "cms energy": "CMS",
    "nrg energy": "NRG",
    "nisource": "NI",
    "pinnacle west": "PNW",
    "atmos energy": "ATO",
    "xcel energy": "XEL",
    "wec energy": "WEC",
    "sempra": "SRE",
    "vistra": "VST",
    "constellation energy": "CEG",
    "american electric power": "AEP",
    "ameren": "AEE",
    "edison international": "EIX",
    "air products": "APD",
    "linde": "LIN",
    "cf industries": "CF",
    "mosaic": "MOS",
    # ── Healthcare / pharma ────────────────────────────────────────────── #
    "pfizer": "PFE",
    "abbvie": "ABBV",
    "merck": "MRK",
    "eli lilly": "LLY",
    "moderna": "MRNA",
    "unitedhealth": "UNH",
    "cvs health": "CVS",
    "johnson & johnson": "JNJ",
    "johnson johnson": "JNJ",
    "bristol myers": "BMY",
    "bristol myers squibb": "BMY",
    "amgen": "AMGN",
    "gilead": "GILD",
    "gilead sciences": "GILD",
    "biogen": "BIIB",
    "regeneron": "REGN",
    "illumina": "ILMN",
    "vertex pharmaceuticals": "VRTX",
    "dexcom": "DXCM",
    "abbott": "ABT",
    "becton dickinson": "BDX",
    "baxter": "BAX",
    "edwards lifesciences": "EW",
    "intuitive surgical": "ISRG",
    "stryker": "SYK",
    "boston scientific": "BSX",
    "zimmer biomet": "ZBH",
    "teleflex": "TFX",
    "hologic": "HOLX",
    "resmed": "RMD",
    "insulet": "PODD",
    "idexx laboratories": "IDXX",
    "mettler toledo": "MTD",
    "cooper companies": "COO",
    "steris": "STE",
    "danaher": "DHR",
    "thermo fisher": "TMO",
    "agilent": "A",
    "revvity": "RVTY",
    "solventum": "SOLV",
    "align technology": "ALGN",
    "dentsply sirona": "XRAY",
    "humana": "HUM",
    "cigna": "CI",
    "elevance health": "ELV",
    "centene": "CNC",
    "molina healthcare": "MOH",
    "universal health services": "UHS",
    "hca healthcare": "HCA",
    "davita": "DVA",
    "laboratory corporation": "LH",
    "quest diagnostics": "DGX",
    "mckesson": "MCK",
    "cardinal health": "CAH",
    "cencora": "COR",
    "henry schein": "HSIC",
    "zoetis": "ZTS",
    "incyte": "INCY",
    "biogen": "BIIB",
    "ge healthcare": "GEHC",
    "ge vernova": "GEV",
    # ── Industrial / aerospace ─────────────────────────────────────────── #
    "boeing": "BA",
    "caterpillar": "CAT",
    "honeywell": "HON",
    "lockheed martin": "LMT",
    "raytheon": "RTX",
    "northrop grumman": "NOC",
    "general dynamics": "GD",
    "l3harris technologies": "LHX",
    "leidos": "LDOS",
    "huntington ingalls": "HII",
    "textron": "TXT",
    "transdigm": "TDG",
    "howmet aerospace": "HWM",
    "curtiss wright": "CW",
    "union pacific": "UNP",
    "norfolk southern": "NSC",
    "csx": "CSX",
    "fedex": "FDX",
    "united parcel service": "UPS",
    "united airlines": "UAL",
    "delta air lines": "DAL",
    "southwest airlines": "LUV",
    "american airlines": "AAL",
    "norwegian cruise": "NCLH",
    "royal caribbean": "RCL",
    "carnival": "CCL",
    "3m": "MMM",
    "emerson electric": "EMR",
    "eaton": "ETN",
    "parker hannifin": "PH",
    "illinois tool works": "ITW",
    "dover": "DOV",
    "graco": "GGG",
    "hubbell": "HUBB",
    "rockwell automation": "ROK",
    "xylem": "XYL",
    "idex": "IEX",
    "roper technologies": "ROP",
    "danaher": "DHR",
    "fortive": "FTV",
    "teledyne technologies": "TDY",
    "masco": "MAS",
    "stanley black decker": "SWK",
    "snap on": "SNA",
    "ingersoll rand": "IR",
    "trane technologies": "TT",
    "carrier global": "CARR",
    "otis worldwide": "OTIS",
    "ge aerospace": "GE",
    "pentair": "PNR",
    "ametek": "AME",
    "trimble": "TRMB",
    "generac": "GNRC",
    "wabtec": "WAB",
    "nordson": "NDSN",
    "jacobs solutions": "J",
    "jabil": "JBL",
    "paccar": "PCAR",
    "deere": "DE",
    "cummins": "CMI",
    "caterpillar": "CAT",
    "builders firstsource": "BLDR",
    "d r horton": "DHI",
    "lennar": "LEN",
    "pultegroup": "PHM",
    "nvr": "NVR",
    # ── Telecom ────────────────────────────────────────────────────────── #
    "verizon": "VZ",
    "at&t": "T",
    "t-mobile": "TMUS",
    "t mobile": "TMUS",
    "comcast": "CMCSA",
    "charter communications": "CHTR",
    "juniper networks": "JNPR",
    "motorola solutions": "MSI",
    "amphenol": "APH",
    "te connectivity": "TEL",
    "corning": "GLW",
    "lumentum": "LITE",
    "f5 networks": "FFIV",
    # ── Retail / consumer ──────────────────────────────────────────────── #
    "walmart": "WMT",
    "costco": "COST",
    "amazon": "AMZN",
    "nike": "NKE",
    "starbucks": "SBUX",
    "mcdonald's": "MCD",
    "mcdonalds": "MCD",
    "mcdonald": "MCD",
    "dominos pizza": "DPZ",
    "yum brands": "YUM",
    "darden restaurants": "DRI",
    "chipotle": "CMG",
    "tractor supply": "TSCO",
    "ross stores": "ROST",
    "tjx companies": "TJX",
    "dollar general": "DG",
    "dollar tree": "DLTR",
    "autozone": "AZO",
    "oreilly automotive": "ORLY",
    "carmax": "KMX",
    "best buy": "BBY",
    "target": "TGT",
    "home depot": "HD",
    "lowe's": "LOW",
    "lowes": "LOW",
    "bath body works": "BBWI",
    "ulta beauty": "ULTA",
    "ralph lauren": "RL",
    "tapestry": "TPR",
    "pvh": "PVH",
    "hasbro": "HAS",
    "mattel": "MAT",
    "lululemon": "LULU",
    "deckers outdoor": "DECK",
    "gap": "GAP",
    "ebay": "EBAY",
    "etsy": "ETSY",
    "copart": "CPRT",
    "carmax": "KMX",
    "autonation": "AN",
    # ── Consumer staples / food ────────────────────────────────────────── #
    "procter & gamble": "PG",
    "procter and gamble": "PG",
    "procter gamble": "PG",
    "coca-cola": "KO",
    "coca cola": "KO",
    "pepsico": "PEP",
    "pepsi": "PEP",
    "colgate": "CL",
    "colgate palmolive": "CL",
    "kimberly-clark": "KMB",
    "kimberly clark": "KMB",
    "walt disney": "DIS",
    "disney": "DIS",
    "comcast": "CMCSA",
    "warner bros discovery": "WBD",
    "fox": "FOX",
    "electronic arts": "EA",
    "take two interactive": "TTWO",
    "match group": "MTCH",
    "monster beverage": "MNST",
    "constellation brands": "STZ",
    "molson coors": "TAP",
    "anheuser busch": "BUD",
    "general mills": "GIS",
    "kellogg": "K",
    "kellanova": "K",
    "campbell soup": "CPB",
    "conagra brands": "CAG",
    "smucker": "SJM",
    "mccormick": "MKC",
    "kraft heinz": "KHC",
    "mondelez": "MDLZ",
    "church dwight": "CHD",
    "clorox": "CLX",
    "hormel foods": "HRL",
    "tyson foods": "TSN",
    "lamb weston": "LW",
    "altria": "MO",
    "philip morris": "PM",
    # ── Materials ──────────────────────────────────────────────────────── #
    "dupont": "DD",
    "dow": "DOW",
    "lyondellbasell": "LYB",
    "eastman chemical": "EMN",
    "celanese": "CE",
    "westlake": "WLK",
    "linde": "LIN",
    "ppg industries": "PPG",
    "sherwin williams": "SHW",
    "ecolab": "ECL",
    "international flavors": "IFF",
    "fmc": "FMC",
    "cf industries": "CF",
    "albemarle": "ALB",
    "international paper": "IP",
    "packaging corp": "PKG",
    "ball corporation": "BALL",
    "crown": "CCK",
    "steel dynamics": "STLD",
    "nucor": "NUE",
    "freeport mcmoran": "FCX",
    "newmont": "NEM",
    "vulcan materials": "VMC",
    "martin marietta": "MLM",
    # ── REIT ───────────────────────────────────────────────────────────── #
    "prologis": "PLD",
    "american tower": "AMT",
    "crown castle": "CCI",
    "sbac communications": "SBAC",
    "equinix": "EQIX",
    "digital realty": "DLR",
    "public storage": "PSA",
    "extra space storage": "EXR",
    "avalonbay": "AVB",
    "equity residential": "EQR",
    "essex property": "ESS",
    "mid america apartment": "MAA",
    "udr": "UDR",
    "invitation homes": "INVH",
    "vici properties": "VICI",
    "realty income": "O",
    "federal realty": "FRT",
    "regency centers": "REG",
    "kimco realty": "KIM",
    "simon property": "SPG",
    "boston properties": "BXP",
    "healthpeak properties": "DOC",
    "ventas": "VTR",
    "welltower": "WELL",
    "camden property": "CPT",
    "host hotels": "HST",
    "iron mountain": "IRM",
    "iqvia": "IQV",
    # ── Technology hardware / storage ──────────────────────────────────── #
    "hp": "HPQ",
    "hewlett packard enterprise": "HPE",
    "western digital": "WDC",
    "seagate technology": "STX",
    "netapp": "NTAP",
    "amphenol": "APH",
    "motorola solutions": "MSI",
    "zebra technologies": "ZBRA",
    "keysight technologies": "KEYS",
    "garmin": "GRMN",
    "trimble": "TRMB",
    "gartner": "IT",
    "cognizant": "CTSH",
    "automatic data processing": "ADP",
    "paychex": "PAYX",
    "paycom": "PAYC",
    "intuit": "INTU",
    "tyler technologies": "TYL",
    "fleetcor": "FLT",
    "global payments": "GPN",
    "western union": "WU",
    "jack henry": "JKHY",
    "ss&c technologies": "SSNC",
    # ── E-commerce / fintech ───────────────────────────────────────────── #
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
    "sofi": "SOFI",
    "affirmative": "AFRM",
    "upstart": "UPST",
    # ── Auto / EV ──────────────────────────────────────────────────────── #
    "tesla": "TSLA",
    "rivian": "RIVN",
    "lucid motors": "LCID",
    "lucid group": "LCID",
    "general motors": "GM",
    "ford motor": "F",
    "stellantis": "STLA",
    "aptiv": "APTV",
    # ── Other S&P 500 ──────────────────────────────────────────────────── #
    "news corp": "NWSA",
    "akamai": "AKAM",
    "aflac": "AFL",
    "american international group": "AIG",
    "american water works": "AWK",
    "allegion": "ALLE",
    "axon enterprise": "AXON",
    "archer daniels midland": "ADM",
    "avery dennison": "AVY",
    "brown forman": "BF.B",
    "bunge": "BG",
    "brown brown": "BRO",
    "brown & brown": "BRO",
    "caesars entertainment": "CZR",
    "corteva": "CTVA",
    "ch robinson": "CHRW",
    "charles river": "CRL",
    "centerpoint energy": "CNP",
    "cintas": "CTAS",
    "genuine parts": "GPC",
    "global payments": "GPN",
    "interpublic group": "IPG",
    "las vegas sands": "LVS",
    "live nation": "LYV",
    "lkq": "LKQ",
    "mgm resorts": "MGM",
    "mohawk industries": "MHK",
    "omnicom group": "OMC",
    "old dominion freight": "ODFL",
    "palo alto networks": "PANW",
    "pool corp": "POOL",
    "ppl": "PPL",
    "republic services": "RSG",
    "rollins": "ROL",
    "ross stores": "ROST",
    "verisign": "VRSN",
    "viatris": "VTRS",
    "waste management": "WM",
    "waters": "WAT",
    "west pharmaceutical": "WST",
    "whirlpool": "WHR",
    "wynn resorts": "WYNN",
    "ww grainger": "GWW",
    "willis towers watson": "WTW",
    "fortinet": "FTNT",
    "kenvue": "KVUE",
    "keurig dr pepper": "KDP",
    "live nation entertainment": "LYV",
    "gen digital": "GEN",
    "walgreens boots alliance": "WBA",
    "warner bros": "WBD",
    "waste management": "WM",
    "westrock": "WRK",
    "packaging corporation": "PKG",
    "rollins": "ROL",
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

    Pass 2 — Bare uppercase tokens (``AAPL``, ``NVDA``, ``TSLA``) that are
             already present in the text, cross-referenced against the known
             S&P 500 + ETF universe and filtered through a stop-word list.

    Pass 3 — Case-insensitive company name matching using pre-compiled
             word-boundary patterns.

    Deduplication via a seen-set preserves first-occurrence order.

    Args:
        text: Raw article headline, summary, or tweet text.

    Returns:
        Ordered, deduplicated list of ticker symbols found in *text*.
    """
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    # Pass 1 — explicit $CASHTAG tokens
    for match in _CASHTAG_RE.finditer(text):
        ticker = match.group(1).upper()
        if ticker not in seen:
            found.append(ticker)
            seen.add(ticker)

    # Pass 2 — bare uppercase tokens cross-referenced with known universe
    for match in _BARE_TICKER_RE.finditer(text):
        ticker = match.group(1).upper()
        if (
            ticker in SP500_TICKER_SET
            and ticker not in _STOP_WORDS
            and ticker not in seen
        ):
            found.append(ticker)
            seen.add(ticker)

    # Pass 3 — company name mentions (case-insensitive, word boundaries)
    for pattern, ticker in _COMPANY_PATTERNS:
        if ticker not in seen and pattern.search(text):
            found.append(ticker)
            seen.add(ticker)

    return found
