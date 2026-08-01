"""
Module: text_preprocessor.py
Purpose: Clean and normalise raw RSS article text into lemmatised tokens for NLP downstream processing
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Text preprocessing pipeline for financial news articles.

Cleans raw HTML/RSS article text into a normalised, lemmatised string
suitable for topic modelling, keyword indexing, and NLP feature extraction.

Phase 4 (FinBERT) will use the original title/summary fields directly,
not this cleaned output — FinBERT works best on naturally-phrased text.
cleaned_text is optimised for bag-of-words and embedding pre-processing.
"""
from __future__ import annotations

import html
import re
import ssl
import unicodedata
from typing import List

import nltk
from nltk.corpus import stopwords
from nltk.corpus import wordnet as wn
from nltk.stem import WordNetLemmatizer
from nltk.tokenize import word_tokenize


def _ensure_nltk_data() -> None:
    """
    Download required NLTK corpora if not already present on this machine.

    SSL verification is disabled temporarily because corporate networks with SSL
    inspection present self-signed certificates that fail NLTK's default HTTPS
    verification.  The original context is always restored in the finally block.
    """
    # Disable cert verification for corporate networks with SSL inspection
    _orig = ssl._create_default_https_context
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        required = [
            ("tokenizers/punkt_tab", "punkt_tab"),
            ("corpora/stopwords", "stopwords"),
            ("corpora/wordnet", "wordnet"),
            ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
            ("corpora/omw-1.4", "omw-1.4"),
        ]
        for path, pkg in required:
            try:
                nltk.data.find(path)
            except LookupError:
                nltk.download(pkg, quiet=True)
    finally:
        ssl._create_default_https_context = _orig


_ensure_nltk_data()

# ============================================================
# FINANCIAL DOMAIN VOCABULARY
# ============================================================

# Financial terms that must survive NLTK stopword removal.
# Standard NLTK stopwords include common English words; several finance-specific
# terms (e.g. "high", "low", "hold", "buy", "sell") happen to appear on that list.
# We whitelist them here so they are not stripped from cleaned_text, which would
# otherwise lose important market-direction signals before embedding/indexing.
_FINANCIAL_KEEP: frozenset[str] = frozenset({
    "bull", "bear", "short", "long", "squeeze", "rally", "crash",
    "merger", "acquisition", "earnings", "revenue", "guidance",
    "upgrade", "downgrade", "dividend", "buyback", "split", "ipo",
    "profit", "loss", "debt", "equity", "yield", "margin", "growth",
    "outlook", "forecast", "target", "beat", "miss", "stake",
    "volatility", "momentum", "breakout", "support", "resistance",
    "overweight", "underweight", "hold", "buy", "sell", "strong",
    "weak", "high", "low", "rise", "fall", "gain", "drop", "surge",
    "plunge", "rebound", "pullback", "correction", "recovery",
    "inflation", "rate", "fed", "fiscal", "trade", "tariff", "sanction",
    "bankruptcy", "default", "restructuring", "divestiture",
    "spinoff", "spin-off", "filing", "quarterly", "annual",
})

# ── Contraction expansion (financial text rarely uses these but let's be safe)
_CONTRACTIONS: dict[str, str] = {
    "won't": "will not", "can't": "cannot", "don't": "do not",
    "doesn't": "does not", "isn't": "is not", "aren't": "are not",
    "wasn't": "was not", "weren't": "were not", "hasn't": "has not",
    "haven't": "have not", "hadn't": "had not", "didn't": "did not",
    "wouldn't": "would not", "shouldn't": "should not",
    "couldn't": "could not", "it's": "it is", "that's": "that is",
    "there's": "there is", "they're": "they are", "we're": "we are",
    "you're": "you are", "i'm": "i am", "i've": "i have",
    "i'll": "i will", "i'd": "i would",
}
_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CONTRACTIONS) + r")\b",
    re.IGNORECASE,
)

# ── NLTK objects (instantiated once at module level) ────────────────────────
_LEMMATIZER = WordNetLemmatizer()
_STOPWORDS: frozenset[str] = (
    frozenset(stopwords.words("english")) - _FINANCIAL_KEEP
)

# ── Compiled regexes ────────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALPHA_RE = re.compile(r"[^a-z0-9\s]")
_CASHTAG_RE = re.compile(r"\$[A-Z]{1,5}\b")


def _penn_to_wn(tag: str) -> str:
    """
    Convert a Penn Treebank POS tag to its WordNet equivalent for lemmatisation.

    NLTK's pos_tag() returns Penn Treebank tags (JJ, VB, RB, NN…) while
    WordNetLemmatizer.lemmatize() expects WordNet part-of-speech constants.
    Defaulting unmapped tags to NOUN (wn.NOUN) is safe for financial text where
    most content words are nouns (companies, products, metrics).

    Args:
        tag: Penn Treebank POS tag string (e.g. 'JJ', 'VBD', 'NNS').

    Returns:
        str: WordNet POS constant (wn.ADJ, wn.VERB, wn.ADV, or wn.NOUN).
    """
    if tag.startswith("J"):
        return wn.ADJ
    if tag.startswith("V"):
        return wn.VERB
    if tag.startswith("R"):
        return wn.ADV
    return wn.NOUN  # default


class TextPreprocessor:
    """
    Stateless financial text cleaner and lemmatiser.

    Instantiate once at the module level and call .preprocess() for each article.
    The instance carries no state so it is safe to use from multiple coroutines
    via asyncio.to_thread().

    Example:
        preprocessor = TextPreprocessor()
        cleaned = preprocessor.preprocess("Apple Inc. (AAPL) beat earnings estimates.")
        # → "apple inc aapl beat earning estimate"
    """

    def preprocess(self, text: str) -> str:
        """
        Run the full preprocessing pipeline on a raw article title + summary.

        Pipeline stages:
          1. _clean()      — strip HTML, normalise unicode, expand contractions, lowercase
          2. _tokenise()   — NLTK word_tokenize (handles punctuation correctly)
          3. _filter()     — remove stopwords (but keep financial terms), drop single-chars
          4. _lemmatise()  — reduce to base forms via WordNet (run → running → run)

        Args:
            text: Raw article text (may contain HTML entities, unicode, contractions).

        Returns:
            str: Space-joined string of cleaned tokens, or '' if input is blank.
        """
        if not text or not text.strip():
            return ""

        text = self._clean(text)
        tokens = self._tokenise(text)
        tokens = self._filter(tokens)
        tokens = self._lemmatise(tokens)
        return " ".join(tokens)

    # ── Private pipeline stages ─────────────────────────────────────────────

    def _clean(self, text: str) -> str:
        # 1. Unescape HTML entities (&amp; → &, &lt; → <, etc.)
        text = html.unescape(text)
        # 2. Strip HTML tags
        text = _HTML_TAG_RE.sub(" ", text)
        # 3. Unicode NFKC normalisation (e.g. full-width chars → ASCII)
        text = unicodedata.normalize("NFKC", text)
        # 4. Encode to ASCII, drop characters that can't be represented
        text = text.encode("ascii", errors="ignore").decode("ascii")
        # 5. Expand contractions
        text = _CONTRACTION_RE.sub(
            lambda m: _CONTRACTIONS[m.group(0).lower()], text
        )
        # 6. Lowercase
        text = text.lower()
        # 7. Remove non-alphanumeric (keep spaces)
        text = _NON_ALPHA_RE.sub(" ", text)
        # 8. Collapse whitespace
        text = _MULTI_SPACE_RE.sub(" ", text).strip()
        return text

    def _tokenise(self, text: str) -> List[str]:
        return word_tokenize(text)

    def _filter(self, tokens: List[str]) -> List[str]:
        return [
            t for t in tokens
            if (
                len(t) > 1                       # drop single chars
                and not t.isdigit()              # drop bare numbers
                and t not in _STOPWORDS          # standard stopwords
                or t in _FINANCIAL_KEEP          # always keep finance terms
            )
        ]

    def _lemmatise(self, tokens: List[str]) -> List[str]:
        tagged = nltk.pos_tag(tokens)
        return [
            _LEMMATIZER.lemmatize(tok, _penn_to_wn(tag))
            for tok, tag in tagged
        ]
