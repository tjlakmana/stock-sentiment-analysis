"""
Company-name-to-ticker mapping with fuzzy matching support.

Builds a unified lookup table from three layers:
1. Base map from ticker_list._COMPANY_TICKER_MAP (common short names)
2. Extended map (full legal names, abbreviations, subsidiaries)
3. Manual overrides from nlp/ticker_overrides.json

Fuzzy matching via rapidfuzz is applied when exact lookup fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from rapidfuzz import process as fuzz_process, fuzz as fuzz_scorer
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False
    logger.warning("rapidfuzz not installed; fuzzy ticker matching disabled.")

# Path to the overrides file (same directory as this module)
_OVERRIDES_PATH = Path(__file__).parent / "ticker_overrides.json"

# Legal-suffix tokens to strip when normalizing for fuzzy comparison
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|corp\.?|corporation|incorporated|ltd\.?|limited|llc\.?|"
    r"l\.p\.?|plc\.?|co\.?|company|group|holdings?|technologies?|"
    r"systems?|solutions?|services?|enterprises?|partners?|international|"
    r"global|worldwide|america|americas|bancorp|financial|bancshares)\b",
    re.IGNORECASE,
)

# Extended company → ticker mapping (full legal names + common variants)
_EXTENDED_MAP: dict[str, str] = {
    # ── Mega-cap tech ──────────────────────────────────────────────────
    "apple inc": "AAPL", "apple computer": "AAPL",
    "microsoft corporation": "MSFT", "microsoft corp": "MSFT",
    "amazon.com": "AMZN", "amazon.com inc": "AMZN",
    "alphabet inc": "GOOGL", "alphabet": "GOOGL",
    "google llc": "GOOGL", "google inc": "GOOGL",
    "meta platforms": "META", "meta platforms inc": "META",
    "facebook inc": "META",
    "nvidia corporation": "NVDA", "nvidia corp": "NVDA",
    "tesla inc": "TSLA", "tesla motors": "TSLA",
    # ── Semiconductors ────────────────────────────────────────────────
    "advanced micro devices": "AMD", "advanced micro devices inc": "AMD",
    "intel corporation": "INTC", "intel corp": "INTC",
    "qualcomm incorporated": "QCOM", "qualcomm inc": "QCOM",
    "broadcom inc": "AVGO", "broadcom corporation": "AVGO",
    "texas instruments incorporated": "TXN",
    "texas instruments inc": "TXN",
    "applied materials inc": "AMAT",
    "lam research corporation": "LRCX", "lam research corp": "LRCX",
    "kla corporation": "KLAC", "kla corp": "KLAC",
    "micron technology inc": "MU", "micron technology": "MU",
    "analog devices inc": "ADI",
    "microchip technology inc": "MCHP",
    "monolithic power systems inc": "MPWR",
    "marvell technology inc": "MRVL",
    "on semiconductor corporation": "ON",
    "skyworks solutions inc": "SWKS",
    "qorvo inc": "QRVO",
    "entegris inc": "ENTG",
    "synopsys inc": "SNPS",
    "cadence design systems inc": "CDNS",
    # ── Software & cloud ──────────────────────────────────────────────
    "salesforce inc": "CRM", "salesforce.com inc": "CRM",
    "oracle corporation": "ORCL", "oracle corp": "ORCL",
    "servicenow inc": "NOW",
    "adobe inc": "ADBE", "adobe systems": "ADBE",
    "intuit inc": "INTU",
    "palo alto networks inc": "PANW",
    "crowdstrike holdings inc": "CRWD",
    "fortinet inc": "FTNT",
    "workday inc": "WDAY",
    "veeva systems inc": "VEEV",
    "datadog inc": "DDOG",
    "snowflake inc": "SNOW",
    "mongodb inc": "MDB",
    "cloudflare inc": "NET",
    "zscaler inc": "ZS",
    "okta inc": "OKTA",
    "hubspot inc": "HUBS",
    "twilio inc": "TWLO",
    "zendesk inc": "ZEN",
    "paycom software inc": "PAYC",
    "costar group inc": "CSGP",
    "roper technologies inc": "ROP",
    "ansys inc": "ANSS",
    "tyler technologies inc": "TYL",
    "cognizant technology solutions": "CTSH",
    "cognizant technology solutions corporation": "CTSH",
    "automatic data processing inc": "ADP",
    "fidelity national information services": "FIS",
    "fidelity national information services inc": "FIS",
    "fiserv inc": "FISV",
    "ss&c technologies holdings inc": "SSNC",
    "verisk analytics inc": "VRSK",
    "fair isaac corporation": "FICO",
    "msci inc": "MSCI",
    "s&p global inc": "SPGI",
    "intercontinental exchange inc": "ICE",
    "nasdaq inc": "NDAQ",
    "cboe global markets inc": "CBOE",
    "cme group inc": "CME",
    "moody's corporation": "MCO",
    # ── Fintech & payments ────────────────────────────────────────────
    "visa inc": "V", "visa international": "V",
    "mastercard incorporated": "MA", "mastercard inc": "MA",
    "paypal holdings inc": "PYPL",
    "block inc": "SQ",
    "affirm holdings inc": "AFRM",
    "coinbase global inc": "COIN",
    # ── Telecom ───────────────────────────────────────────────────────
    "at&t inc": "T",
    "verizon communications inc": "VZ",
    "t-mobile us inc": "TMUS",
    "comcast corporation": "CMCSA",
    "charter communications inc": "CHTR",
    "lumen technologies inc": "LUMN",
    # ── Banks & finance ───────────────────────────────────────────────
    "jpmorgan chase & co": "JPM", "jpmorgan chase and co": "JPM",
    "bank of america corporation": "BAC",
    "wells fargo & company": "WFC", "wells fargo and company": "WFC",
    "citigroup inc": "C", "citibank": "C",
    "goldman sachs group inc": "GS",
    "morgan stanley": "MS",
    "blackrock inc": "BLK",
    "charles schwab corporation": "SCHW",
    "american express company": "AXP",
    "capital one financial corporation": "COF",
    "u.s. bancorp": "USB",
    "pnc financial services group inc": "PNC",
    "truist financial corporation": "TFC",
    "state street corporation": "STT",
    "northern trust corporation": "NTRS",
    "t. rowe price group inc": "TROW",
    "raymond james financial inc": "RJF",
    "ameriprise financial inc": "AMP",
    "principal financial group inc": "PFG",
    "lincoln national corporation": "LNC",
    "unum group": "UNM",
    "aflac incorporated": "AFL",
    "progressive corporation": "PGR",
    "travelers companies inc": "TRV",
    "allstate corporation": "ALL",
    "hartford financial services group inc": "HIG",
    "chubb limited": "CB",
    "aon plc": "AON",
    "marsh & mclennan companies inc": "MMC",
    "american international group inc": "AIG",
    "metlife inc": "MET",
    "prudential financial inc": "PRU",
    # ── Healthcare & pharma ───────────────────────────────────────────
    "unitedhealth group incorporated": "UNH",
    "unitedhealth group inc": "UNH",
    "johnson & johnson": "JNJ",
    "pfizer inc": "PFE",
    "merck & co inc": "MRK",
    "eli lilly and company": "LLY", "eli lilly and co": "LLY",
    "abbvie inc": "ABBV",
    "amgen inc": "AMGN",
    "bristol-myers squibb company": "BMY",
    "bristol myers squibb company": "BMY",
    "gilead sciences inc": "GILD",
    "biogen inc": "BIIB",
    "regeneron pharmaceuticals inc": "REGN",
    "moderna inc": "MRNA",
    "vertex pharmaceuticals incorporated": "VRTX",
    "danaher corporation": "DHR",
    "thermo fisher scientific inc": "TMO",
    "abbott laboratories": "ABT",
    "medtronic plc": "MDT", "medtronic": "MDT",
    "stryker corporation": "SYK",
    "boston scientific corporation": "BSX",
    "intuitive surgical inc": "ISRG",
    "edwards lifesciences corporation": "EW",
    "becton dickinson and company": "BDX",
    "baxter international inc": "BAX",
    "idexx laboratories inc": "IDXX",
    "resmed inc": "RMD",
    "dexcom inc": "DXCM",
    "insulet corporation": "PODD",
    "align technology inc": "ALGN",
    "hologic inc": "HOLX",
    "cigna group": "CI", "cigna corporation": "CI",
    "elevance health inc": "ELV",
    "centene corporation": "CNC",
    "molina healthcare inc": "MOH",
    "humana inc": "HUM",
    "hca healthcare inc": "HCA",
    "universal health services inc": "UHS",
    "iqvia holdings inc": "IQV",
    "labcorp": "LH", "laboratory corporation of america": "LH",
    "quest diagnostics incorporated": "DGX",
    "mckesson corporation": "MCK",
    "amerisourcebergen corporation": "ABC",
    "cardinal health inc": "CAH",
    "henry schein inc": "HSIC",
    "illumina inc": "ILMN",
    "waters corporation": "WAT",
    "agilent technologies inc": "A",
    "mettler-toledo international inc": "MTD",
    "bio-techne corporation": "TECH",
    "alnylam pharmaceuticals inc": "ALNY",
    "incyte corporation": "INCY",
    "exelixis inc": "EXEL",
    "neurocrine biosciences inc": "NBIX",
    "jazz pharmaceuticals plc": "JAZZ",
    "catalent inc": "CTLT",
    "west pharmaceutical services inc": "WST",
    "steris plc": "STE",
    # ── Consumer staples & discretionary ─────────────────────────────
    "procter & gamble company": "PG", "procter and gamble company": "PG",
    "walmart inc": "WMT", "wal-mart stores inc": "WMT",
    "amazon": "AMZN",
    "the home depot inc": "HD", "home depot inc": "HD",
    "lowe's companies inc": "LOW", "lowes companies inc": "LOW",
    "costco wholesale corporation": "COST",
    "target corporation": "TGT",
    "dollar general corporation": "DG",
    "dollar tree inc": "DLTR",
    "ross stores inc": "ROST",
    "tjx companies inc": "TJX",
    "nike inc": "NKE",
    "starbucks corporation": "SBUX",
    "mcdonald's corporation": "MCD",
    "yum! brands inc": "YUM", "yum brands inc": "YUM",
    "chipotle mexican grill inc": "CMG",
    "darden restaurants inc": "DRI",
    "marriott international inc": "MAR",
    "hilton worldwide holdings inc": "HLT",
    "royal caribbean cruises ltd": "RCL",
    "carnival corporation": "CCL",
    "booking holdings inc": "BKNG",
    "airbnb inc": "ABNB",
    "expedia group inc": "EXPE",
    "ebay inc": "EBAY",
    "etsy inc": "ETSY",
    "coca-cola company": "KO", "the coca-cola company": "KO",
    "pepsico inc": "PEP",
    "mondelez international inc": "MDLZ",
    "philip morris international inc": "PM",
    "altria group inc": "MO",
    "colgate-palmolive company": "CL",
    "kimberly-clark corporation": "KMB",
    "general mills inc": "GIS",
    "conagra brands inc": "CAG",
    "kraft heinz company": "KHC",
    "campbell soup company": "CPB",
    "church & dwight co inc": "CHD",
    "clorox company": "CLX",
    "estee lauder companies inc": "EL",
    "ulta beauty inc": "ULTA",
    # ── Industrials ───────────────────────────────────────────────────
    "general electric company": "GE", "ge aerospace": "GE",
    "honeywell international inc": "HON",
    "3m company": "MMM", "minnesota mining and manufacturing": "MMM",
    "caterpillar inc": "CAT",
    "deere & company": "DE",
    "emerson electric co": "EMR",
    "illinois tool works inc": "ITW",
    "parker hannifin corporation": "PH",
    "eaton corporation plc": "ETN",
    "dover corporation": "DOV",
    "rockwell automation inc": "ROK",
    "rexnord corporation": "RXN",
    "xylem inc": "XYL",
    "fortive corporation": "FTV",
    "danaher corporation": "DHR",
    "trane technologies plc": "TT",
    "carrier global corporation": "CARR",
    "otis worldwide corporation": "OTIS",
    "johnson controls international plc": "JCI",
    "raytheon technologies corporation": "RTX",
    "rtx corporation": "RTX",
    "lockheed martin corporation": "LMT",
    "northrop grumman corporation": "NOC",
    "general dynamics corporation": "GD",
    "l3harris technologies inc": "LHX",
    "boeing company": "BA",
    "airbus se": "EADSY",
    "textron inc": "TXT",
    "transdigm group incorporated": "TDG",
    "spirit aerosystems holdings inc": "SPR",
    "heico corporation": "HEI",
    "moog inc": "MOG.A",
    "union pacific corporation": "UNP",
    "csx corporation": "CSX",
    "norfolk southern corporation": "NSC",
    "kansas city southern": "KSU",
    "united parcel service inc": "UPS",
    "fedex corporation": "FDX",
    "old dominion freight line inc": "ODFL",
    "j.b. hunt transport services inc": "JBHT",
    "waste management inc": "WM",
    "republic services inc": "RSG",
    "cintas corporation": "CTAS",
    "automatic data processing": "ADP",
    "paychex inc": "PAYX",
    "robert half international inc": "RHI",
    "manpowergroup inc": "MAN",
    "copart inc": "CPRT",
    "w.w. grainger inc": "GWW",
    "fastenal company": "FAST",
    "genuine parts company": "GPC",
    "paccar inc": "PCAR",
    # ── Energy ────────────────────────────────────────────────────────
    "exxon mobil corporation": "XOM", "exxonmobil corporation": "XOM",
    "chevron corporation": "CVX",
    "conocophillips": "COP",
    "eog resources inc": "EOG",
    "pioneer natural resources company": "PXD",
    "diamondback energy inc": "FANG",
    "devon energy corporation": "DVN",
    "hess corporation": "HES",
    "schlumberger limited": "SLB", "slb": "SLB",
    "halliburton company": "HAL",
    "baker hughes company": "BKR",
    "occidental petroleum corporation": "OXY",
    "marathon petroleum corporation": "MPC",
    "phillips 66": "PSX",
    "valero energy corporation": "VLO",
    "williams companies inc": "WMB",
    "kinder morgan inc": "KMI",
    "oneok inc": "OKE",
    "coterra energy inc": "CTRA",
    "antero resources corporation": "AR",
    "range resources corporation": "RRC",
    # ── Materials ─────────────────────────────────────────────────────
    "freeport-mcmoran inc": "FCX",
    "newmont corporation": "NEM",
    "nucor corporation": "NUE",
    "steel dynamics inc": "STLD",
    "vulcan materials company": "VMC",
    "martin marietta materials inc": "MLM",
    "linde plc": "LIN",
    "air products and chemicals inc": "APD",
    "sherwin-williams company": "SHW",
    "ppg industries inc": "PPG",
    "celanese corporation": "CE",
    "eastman chemical company": "EMN",
    "ecolab inc": "ECL",
    "international flavors & fragrances inc": "IFF",
    "corning incorporated": "GLW",
    "ball corporation": "BALL",
    "sealed air corporation": "SEE",
    # ── Utilities ─────────────────────────────────────────────────────
    "nextera energy inc": "NEE",
    "duke energy corporation": "DUK",
    "southern company": "SO",
    "dominion energy inc": "D",
    "american electric power company inc": "AEP",
    "exelon corporation": "EXC",
    "xcel energy inc": "XEL",
    "eversource energy": "ES",
    "consolidated edison inc": "ED",
    "sempra": "SRE",
    "dte energy company": "DTE",
    "public service enterprise group inc": "PEG",
    "entergy corporation": "ETR",
    "ameren corporation": "AEE",
    "cms energy corporation": "CMS",
    "evergy inc": "EVRG",
    "avangrid inc": "AGR",
    "american water works company inc": "AWK",
    # ── Real estate ───────────────────────────────────────────────────
    "prologis inc": "PLD",
    "public storage": "PSA",
    "welltower inc": "WELL",
    "simon property group inc": "SPG",
    "equinix inc": "EQIX",
    "digital realty trust inc": "DLR",
    "realty income corporation": "O",
    "extra space storage inc": "EXR",
    "camden property trust": "CPT",
    # ── Media & entertainment ─────────────────────────────────────────
    "walt disney company": "DIS", "the walt disney company": "DIS",
    "netflix inc": "NFLX",
    "spotify technology": "SPOT",
    "warner bros. discovery inc": "WBD",
    "paramount global": "PARA",
    "fox corporation": "FOX",
    "charter communications inc": "CHTR",
    "live nation entertainment inc": "LYV",
    "iheartmedia inc": "IHRT",
    # ── ETFs (for completeness) ───────────────────────────────────────
    "spdr s&p 500 etf trust": "SPY",
    "invesco qqq trust": "QQQ",
    "ishares russell 2000 etf": "IWM",
    "spdr gold shares": "GLD",
    "ishares 20+ year treasury bond etf": "TLT",
}


def _normalize(text: str) -> str:
    """Lowercase, strip legal suffixes and punctuation for matching."""
    text = text.lower().strip()
    text = re.sub(r"'s\b", "", text)           # remove possessives
    text = _LEGAL_SUFFIXES.sub(" ", text)       # strip Inc., Corp., etc.
    text = re.sub(r"[^\w\s]", " ", text)        # remove punctuation
    text = re.sub(r"\s+", " ", text).strip()    # normalize whitespace
    return text


def _build_full_map() -> dict[str, str]:
    """Merge base map, extended map, and overrides into one normalized dict."""
    # Import base map from ticker_list to avoid duplication
    try:
        from sentiment_analysis.ingestion.ticker_list import _COMPANY_TICKER_MAP as _base
        base = {k.lower(): v for k, v in _base.items()}
    except ImportError:
        base = {}

    merged: dict[str, str] = {}
    merged.update(base)
    merged.update({k.lower(): v for k, v in _EXTENDED_MAP.items()})

    # Load manual overrides (JSON keys already lowercase by convention)
    if _OVERRIDES_PATH.exists():
        try:
            overrides = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            for k, v in overrides.items():
                if k.startswith("_"):   # skip comment keys
                    continue
                merged[k.lower()] = v
        except Exception as exc:
            logger.warning(f"[ticker_mapper] Failed to load overrides: {exc}")

    return merged


# Module-level caches built once at import time
_FULL_MAP: dict[str, str] = _build_full_map()

# Pre-normalised key → ticker for fast exact lookup on normalized input
_NORM_MAP: dict[str, str] = {_normalize(k): v for k, v in _FULL_MAP.items()}

# Keys list for rapidfuzz search (built once, reused for all fuzzy calls)
_NORM_KEYS: list[str] = list(_NORM_MAP.keys())


class TickerMapper:
    """
    Maps entity text (company names, abbreviations) to ticker symbols.

    Lookup order:
      1. Exact match against the raw key
      2. Exact match against the normalised key (strips legal suffixes etc.)
      3. Fuzzy match via rapidfuzz (configurable threshold)
    """

    def __init__(self, fuzzy_threshold: float = 0.80) -> None:
        self._threshold = int(fuzzy_threshold * 100)  # rapidfuzz uses 0-100

    def get_ticker(self, entity_text: str) -> tuple[Optional[str], float]:
        """
        Return ``(ticker, confidence)`` for the entity, or ``(None, 0.0)`` if
        no match meets the threshold.

        confidence is 1.0 for exact matches, 0.0–1.0 for fuzzy matches.
        """
        # Pass 1: raw exact match
        raw = entity_text.lower().strip()
        if raw in _FULL_MAP:
            return _FULL_MAP[raw], 1.0

        # Pass 2: normalised exact match
        norm = _normalize(entity_text)
        if not norm:
            return None, 0.0
        if norm in _NORM_MAP:
            return _NORM_MAP[norm], 1.0

        # Pass 3: fuzzy match
        if not _RAPIDFUZZ:
            return None, 0.0

        result = fuzz_process.extractOne(
            norm,
            _NORM_KEYS,
            scorer=fuzz_scorer.token_sort_ratio,
            score_cutoff=self._threshold,
        )
        if result:
            best_key, score, _ = result
            return _NORM_MAP[best_key], round(score / 100, 3)

        return None, 0.0
