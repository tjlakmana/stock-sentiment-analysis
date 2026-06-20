"""
Named entity extraction and ticker resolution.

Three-pass matching for each article:
  Pass 1 — cashtag scan on raw text  ($AAPL → AAPL)
  Pass 2 — exact ticker scan on raw text (bare uppercase token in SP500 set)
  Pass 3 — title-case phrase extraction + TickerMapper fuzzy matching

Passes 1 & 2 reuse the existing extract_tickers() logic from ticker_list.py.
Pass 3 splits the text into title-case word sequences and tries to match
each against the 500+ company name map in TickerMapper.
"""
from __future__ import annotations

import re
from uuid import UUID

from loguru import logger

from sentiment_analysis.ingestion.ticker_list import extract_tickers
from sentiment_analysis.nlp.ticker_mapper import TickerMapper

# Pass 3: title-case phrases that may be company names.
# Matches sequences of 1–6 title-case words joined by spaces, & or -.
_TITLE_PHRASE_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{1,}(?:[\s&\-][A-Z][a-zA-Z]{1,}){0,5})\b"
)

# Tokens that are never meaningful company name candidates.
_CANDIDATE_BLOCKLIST: frozenset[str] = frozenset({
    "the", "a", "an", "inc", "corp", "ltd", "llc", "plc", "co",
    "sec", "fda", "epa", "doj", "ftc", "cdc", "nih", "fed",
    "nasdaq", "nyse", "dow", "etf", "ipo", "ceo", "cfo",
    "coo", "cto", "cmo", "vp", "svp", "evp",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "q1", "q2", "q3", "q4",
    "us", "usa", "uk", "eu", "china", "russia", "india",
    "new york", "washington", "california", "texas",
    "wall street", "main street",
    # SEC form tokens
    "filer", "exhibits", "exhibit", "item", "registrant",
    "8-k", "10-k", "10-q", "s-1", "s-3", "s-4", "s-11",
    "13f", "13g", "13d", "sc 13g", "sc 13d",
    "form", "def 14a", "proxy", "prospectus",
    "section", "rule", "act", "exchange", "commission",
    "company", "corporation", "limited", "shares", "common stock",
    "class a", "class b", "class c", "series a", "series b",
    "amendment", "agreement", "indenture", "note", "notes",
    "annual report", "press release",
    "globe newswire", "pr newswire", "business wire",
    "pacific time", "eastern time", "central time",
})


def _extract_candidates(text: str) -> list[str]:
    """Extract title-case phrases as company name candidates for Pass 3."""
    seen: set[str] = set()
    candidates: list[str] = []
    for m in _TITLE_PHRASE_RE.finditer(text):
        phrase = m.group(1).strip()
        if len(phrase) <= 2 or phrase.lower() in _CANDIDATE_BLOCKLIST:
            continue
        if phrase not in seen:
            seen.add(phrase)
            candidates.append(phrase)
    return candidates


class EntityExtractor:
    """
    Ticker extractor — three passes, no external NLP models required:
      1. Cashtag scan  ($AAPL → AAPL)
      2. Exact SP500 token match  (via extract_tickers from ticker_list.py)
      3. Title-case phrase → TickerMapper fuzzy match (500+ company names)
    """

    def __init__(self, mapper: TickerMapper) -> None:
        self._mapper = mapper

    def extract(
        self,
        title: str,
        summary: str,
        article_id: UUID,
    ) -> tuple[list[dict], list[str], list[dict]]:
        """
        Run all three passes on an article.

        Returns:
            resolved    — list of dicts ready for ExtractedEntity ORM objects
            new_tickers — deduplicated list of ticker strings found
            unresolved  — list of dicts ready for UnresolvedEntity queue
        """
        full_text = f"{title or ''} {summary or ''}".strip()
        if not full_text:
            return [], [], []

        # Pass 1 + 2: cashtag ($AAPL) + exact SP500 scan.
        # Drop single-char results — SEC form artifacts (K from 8-K, etc.).
        p1_p2_tickers = [t for t in extract_tickers(full_text) if len(t) >= 2]

        # Pass 3: company name fuzzy matching via TickerMapper.
        resolved: list[dict] = []
        unresolved: list[dict] = []
        pass3_tickers: list[str] = []
        seen_tickers: set[str] = set(p1_p2_tickers)

        for candidate in _extract_candidates(full_text):
            ticker, confidence = self._mapper.get_ticker(candidate)
            if ticker:
                if ticker not in seen_tickers:
                    seen_tickers.add(ticker)
                    pass3_tickers.append(ticker)
                resolved.append({
                    "article_id":  article_id,
                    "entity_text": candidate,
                    "entity_type": "ORG",
                    "ticker":      ticker,
                    "confidence":  confidence,
                })
            else:
                unresolved.append({
                    "entity_text": candidate,
                    "entity_type": "ORG",
                    "article_id":  article_id,
                })

        all_tickers = list(dict.fromkeys(p1_p2_tickers + pass3_tickers))
        return resolved, all_tickers, unresolved
