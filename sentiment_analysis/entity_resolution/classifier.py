"""
Entity Classifier — maps entity text + spaCy label to our 18-type taxonomy.

Only entities classified as "Company" or "ETF" proceed to ticker resolution.
All others are dropped, preventing false positives from diseases, government
agencies, people, countries, and other non-traded entities.

Taxonomy:
    Company, GovernmentAgency, Organization, Person, Drug, Disease,
    MedicalCondition, Animal, Country, City, Technology, Product,
    Currency, Exchange, ETF, Commodity, EconomicIndicator, Other
"""
from __future__ import annotations

# ── Government agencies (U.S. federal + major international regulators) ────────
_GOVERNMENT_AGENCIES: frozenset[str] = frozenset({
    # U.S. securities & financial
    "sec", "cftc", "finra", "fdic", "occ", "cfpb", "frb", "fed",
    "federal reserve", "federal reserve bank",
    "securities and exchange commission",
    "commodity futures trading commission",
    "financial industry regulatory authority",
    "federal deposit insurance corporation",
    "consumer financial protection bureau",
    "office of the comptroller of the currency",
    # U.S. health & safety
    "fda", "cdc", "nih", "cms", "hhs", "epa", "osha",
    "food and drug administration",
    "centers for disease control",
    "national institutes of health",
    "centers for medicare",
    "department of health and human services",
    "environmental protection agency",
    # U.S. law enforcement & intelligence
    "fbi", "cia", "nsa", "dea", "atf",
    "department of justice", "doj",
    "attorney general",
    # U.S. defense & homeland
    "dod", "dhs", "pentagon", "tsa", "cbp", "ice",
    "department of defense",
    "department of homeland security",
    "transportation security administration",
    # U.S. trade & commerce
    "ftc", "sba", "usda", "usps", "noaa", "nws", "nasa", "fema",
    "federal trade commission",
    "small business administration",
    # U.S. energy & resources
    "doe", "doi",
    "department of energy", "department of the interior",
    # U.S. legislative / executive
    "congress", "senate", "house of representatives", "white house",
    "state department", "treasury", "irs",
    "department of agriculture",
    # Courts
    "supreme court", "circuit court", "district court",
    # International financial / regulatory
    "imf", "world bank", "who", "wto", "nato", "un", "united nations",
    "ecb", "boe", "boj", "pboc", "rbi",
    "european central bank",
    "bank of england", "bank of japan",
    "peoples bank of china", "bank of canada",
    "international monetary fund",
    "world health organization",
    "world trade organization",
    "bank for international settlements", "bis",
    # Prefix-based catch (see _is_government_agency)
})

# ── Known non-company organizations ──────────────────────────────────────────
_NON_COMPANY_ORGS: frozenset[str] = frozenset({
    "red cross", "american red cross",
    "greenpeace", "amnesty international",
    "world wildlife fund", "wwf",
    "doctors without borders", "msf",
    "afl-cio", "teamsters", "seiu", "aft", "nea",
    "national rifle association", "nra",
    "american civil liberties union", "aclu",
    "american cancer society",
    "american heart association",
    "national football league", "nfl",
    "national basketball association", "nba",
    "major league baseball", "mlb",
    "national hockey league", "nhl",
    "international olympic committee", "ioc",
})

# ── Disease / pathogen keywords ───────────────────────────────────────────────
_DISEASE_WORDS: frozenset[str] = frozenset({
    "disease", "syndrome", "disorder", "cancer", "tumor", "tumour",
    "carcinoma", "lymphoma", "leukemia", "melanoma", "sarcoma",
    "virus", "viral", "infection", "infectious", "pathogen",
    "bacteria", "bacterium", "bacterial",
    "screwworm", "worm", "parasite", "infestation",
    "flu", "influenza", "pneumonia", "tuberculosis", "tb",
    "covid", "sars", "mers", "ebola", "malaria", "dengue", "zika",
    "hepatitis", "hiv", "aids", "polio",
    "alzheimer", "parkinson", "diabetes", "arthritis",
    "leprosy", "plague", "cholera", "typhoid",
    "rabies", "anthrax", "smallpox", "monkeypox",
    "scabies", "ringworm", "tapeworm", "hookworm",
})

# ── Drug / pharmaceutical keywords ───────────────────────────────────────────
_DRUG_WORDS: frozenset[str] = frozenset({
    "drug", "vaccine", "medication", "medicine", "pill", "capsule",
    "therapy", "treatment", "inhibitor", "blocker",
    "agonist", "antagonist", "antibody", "antigen",
    "biologic", "biosimilar", "generic",
    "chemotherapy", "immunotherapy", "radiotherapy",
    "antiviral", "antibiotic", "antifungal",
})

# Pharma naming convention suffixes → drug (e.g. "pembrolizumab", "imatinib")
_DRUG_SUFFIXES: tuple[str, ...] = (
    "mab", "zumab", "xumab", "lizumab", "umab",     # monoclonal antibodies
    "nib", "tinib", "afinib",                         # kinase inhibitors
    "tide", "peptide",                                # peptides
    "pril", "sartan", "olol", "statin",               # cardiovascular
    "mycin", "cillin", "oxacin", "floxacin",          # antibiotics
    "azole", "conazole",                              # antifungals
    "vir", "virin", "navir",                          # antivirals
    "umab", "kinase",
)

# ── Animal keywords ───────────────────────────────────────────────────────────
_ANIMAL_WORDS: frozenset[str] = frozenset({
    "screwworm", "worm", "maggot", "larvae", "larva",
    "beetle", "moth", "caterpillar", "aphid", "locust", "grasshopper",
    "tick", "mite", "louse", "flea",
    "bovine", "cattle", "swine", "poultry", "livestock",
    "deer", "elk", "moose", "bear", "wolf", "coyote", "raccoon",
    "bird", "avian", "fish", "whale", "dolphin", "seal",
})

# ── Exchange names ────────────────────────────────────────────────────────────
_EXCHANGE_NAMES: frozenset[str] = frozenset({
    "nasdaq", "nyse", "amex", "cboe", "otc", "otcbb", "otc markets",
    "pink sheets",
    "london stock exchange", "lse",
    "toronto stock exchange", "tsx",
    "australian securities exchange", "asx",
    "hong kong exchanges", "hkex",
    "euronext",
    "tokyo stock exchange", "tse", "jpx",
    "shanghai stock exchange", "sse",
    "shenzhen stock exchange", "szse",
    "deutsche boerse", "xetra",
    "singapore exchange", "sgx",
})

# ── ETF product families ──────────────────────────────────────────────────────
_ETF_KEYWORDS: frozenset[str] = frozenset({
    "etf", "etn",
    "spdr", "ishares", "invesco qqq", "invesco",
    "powershares", "direxion", "proshares",
    "vanguard etf", "schwab etf",
})

# ── Economic indicators ───────────────────────────────────────────────────────
_ECONOMIC_INDICATORS: frozenset[str] = frozenset({
    "gdp", "cpi", "ppi", "pce", "nfp", "ism", "pmi",
    "unemployment rate", "inflation rate", "interest rate",
    "federal funds rate", "fed funds rate", "overnight rate",
    "consumer price index", "producer price index",
    "gross domestic product",
    "non-farm payroll", "nonfarm payroll",
    "jobless claims", "trade balance", "current account",
    "money supply", "m1", "m2",
    "yield curve", "treasury yield",
})

# ── Currencies ────────────────────────────────────────────────────────────────
_CURRENCY_NAMES: frozenset[str] = frozenset({
    "dollar", "euro", "pound", "sterling", "yen", "yuan",
    "renminbi", "won", "franc", "krona", "rupee", "peso",
    "real", "ruble", "lira", "shekel", "dirham", "riyal",
    "bitcoin", "ethereum", "cryptocurrency", "crypto", "stablecoin",
    "usd", "eur", "gbp", "jpy", "cny", "cnh", "chf", "cad", "aud",
    "nzd", "hkd", "sgd", "inr", "krw", "brl", "rub", "mxn",
    "btc", "eth", "usdt", "usdc",
})

# ── Commodities ───────────────────────────────────────────────────────────────
_COMMODITY_NAMES: frozenset[str] = frozenset({
    "gold", "silver", "platinum", "palladium", "copper", "aluminum",
    "iron ore", "steel", "nickel", "cobalt", "lithium",
    "crude oil", "brent crude", "wti crude", "wti oil", "brent oil",
    "natural gas", "lng", "gasoline",
    "corn", "wheat", "soybeans", "soybean", "cotton",
    "coffee", "sugar", "cocoa", "orange juice", "lumber",
    "lean hogs", "live cattle", "feeder cattle",
})

# ── Technology products (not companies) ──────────────────────────────────────
_TECH_PRODUCT_KEYWORDS: frozenset[str] = frozenset({
    "iphone", "ipad", "macbook", "apple watch", "airpods",
    "galaxy", "pixel phone",
    "windows", "macos", "android", "ios", "linux",
    "chatgpt", "gemini", "copilot", "claude", "gpt-4", "gpt4",
    "artificial intelligence", "machine learning", "deep learning",
    "blockchain", "cloud computing", "edge computing",
    "semiconductor chip", "processor", "gpu", "cpu",
})

# ── Government-agency name prefixes ──────────────────────────────────────────
_GOV_PREFIXES: tuple[str, ...] = (
    "department of ",
    "office of ",
    "bureau of ",
    "national institute",
    "national board",
    "national commission",
    "federal bureau",
    "federal agency",
    "federal reserve",
    "ministry of ",
    "minister of ",
)


class EntityClassifier:
    """
    Classifies a named entity into our 18-type taxonomy using spaCy label
    and lexical signals from curated lists.

    The classify() method is stateless and O(1) for all list checks
    (frozenset membership).
    """

    def classify(self, entity_text: str, spacy_label: str) -> str:
        """
        Return the entity type for entity_text.

        Parameters
        ----------
        entity_text : str
            Raw entity surface form as returned by spaCy.
        spacy_label : str
            The spaCy NER label (ORG, PERSON, GPE, etc.).
        """
        norm = entity_text.lower().strip()

        # ── High-confidence spaCy signals ─────────────────────────────────
        if spacy_label == "PERSON":
            return "Person"
        if spacy_label == "GPE":
            return "Country"   # countries and cities both block ticker assignment

        # ── Lexical classification (most specific first) ─────────────────

        if self._is_government_agency(norm):
            return "GovernmentAgency"

        if norm in _NON_COMPANY_ORGS:
            return "Organization"

        if norm in _EXCHANGE_NAMES:
            return "Exchange"

        if self._token_matches(norm, _CURRENCY_NAMES):
            return "Currency"

        if self._token_matches(norm, _COMMODITY_NAMES):
            return "Commodity"

        if self._token_matches(norm, _ECONOMIC_INDICATORS):
            return "EconomicIndicator"

        # Disease before animal (screwworm disease, viral infection, etc.)
        if self._token_matches(norm, _DISEASE_WORDS):
            return "Disease"

        if self._is_drug(norm):
            return "Drug"

        if self._token_matches(norm, _ANIMAL_WORDS):
            return "Animal"

        if self._etf_match(norm):
            return "ETF"

        if self._token_matches(norm, _TECH_PRODUCT_KEYWORDS):
            return "Technology"

        # ── ORG that passed all filters → Company candidate ───────────────
        if spacy_label == "ORG":
            return "Company"

        # ── Catch-all ─────────────────────────────────────────────────────
        return "Other"

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_government_agency(norm: str) -> bool:
        if norm in _GOVERNMENT_AGENCIES:
            return True
        return any(norm.startswith(p) for p in _GOV_PREFIXES)

    @staticmethod
    def _is_drug(norm: str) -> bool:
        words = set(norm.split())
        if words & _DRUG_WORDS:
            return True
        return any(norm.endswith(sfx) for sfx in _DRUG_SUFFIXES)

    @staticmethod
    def _etf_match(norm: str) -> bool:
        words = set(norm.split())
        return bool(words & _ETF_KEYWORDS)

    @staticmethod
    def _token_matches(norm: str, lookup: frozenset[str]) -> bool:
        """True if norm is in lookup OR any word from norm is in lookup."""
        if norm in lookup:
            return True
        words = set(norm.split())
        return bool(words & lookup)
