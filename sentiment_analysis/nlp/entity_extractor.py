"""
Named entity extraction and ticker resolution.

Three-pass matching for each article:
  Pass 1 — cashtag scan on raw text  ($AAPL → AAPL)
  Pass 2 — exact ticker scan on raw text (bare uppercase token in SP500 set)
  Pass 3 — spaCy NER on raw text + company-name fuzzy mapping via TickerMapper

Passes 1 & 2 reuse the existing extract_tickers() logic from ticker_list.py.
Pass 3 extends coverage using spaCy ORG/PERSON/GPE entities.
"""
from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from loguru import logger

from sentiment_analysis.ingestion.ticker_list import extract_tickers
from sentiment_analysis.nlp.ticker_mapper import TickerMapper

# Cashtag regex for Pass 1
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")

# Entities whose text is almost always a false positive for tickers
_ENTITY_BLOCKLIST: frozenset[str] = frozenset({
    "the", "a", "an", "inc", "corp", "ltd", "llc", "plc", "co",
    "sec", "fda", "epa", "doj", "ftc", "cdc", "nih", "fed",
    "nasdaq", "nyse", "dow", "s&p", "etf", "ipo", "ceo", "cfo",
    "coo", "cto", "cmo", "vp", "svp", "evp",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "q1", "q2", "q3", "q4",
    "us", "usa", "uk", "eu", "china", "russia", "india",
    "new york", "washington", "california", "texas",
    "wall street", "main street",
    # SEC EDGAR form type tokens — appear as NER entities when spaCy parses
    # titles like "Current Report (8-K)", "Form 4", "Form S-1", etc.
    "filer", "exhibits", "exhibit", "item", "registrant",
    "election of directors", "certain officers",
    "material modifications to rights of security holders",
    "other events", "financial statements",
    "amendment to articles of incorporation",
    "departure of directors or certain officers",
    "entry into a material definitive agreement",
    "results of operations and financial condition",
    "kb", "annual report", "press release",
    "globe newswire", "pr newswire", "business wire",
    "pacific time", "eastern time", "central time",
    # SEC filing form names / numbers that look like tickers
    "8-k", "10-k", "10-q", "s-1", "s-3", "s-4", "s-11",
    "4", "13f", "13g", "13d", "sc 13g", "sc 13d",
    "form 4", "form 8-k", "form 10-k", "form 10-q",
    "form s-1", "form s-3", "form sc 13g",
    "def 14a", "proxy", "prospectus",
    # Common false-positive single tokens from SEC boilerplate
    "section", "rule", "act", "exchange", "commission",
    "company", "corporation", "limited", "shares", "common stock",
    "class a", "class b", "class c", "series a", "series b",
    "amendment", "agreement", "indenture", "note", "notes",
})


class EntityExtractor:
    """
    Stateful extractor: holds the spaCy model in memory after first load.
    Instantiate once per pipeline run and reuse across articles.
    """

    def __init__(self, mapper: TickerMapper) -> None:
        self._mapper = mapper
        self._nlp = None  # lazy-loaded

    # ── Public API ──────────────────────────────────────────────────────────

    def extract(
        self,
        title: str,
        summary: str,
        article_id: UUID,
    ) -> tuple[list[dict], list[str], list[dict]]:
        """
        Run all three extraction passes on an article.

        Returns:
            resolved   — list of dicts ready for ExtractedEntity ORM objects
            new_tickers — deduplicated list of ticker strings found
            unresolved — list of dicts ready for UnresolvedEntity queue
        """
        # Combine fields; keep original casing for NER
        full_text = f"{title or ''} {summary or ''}".strip()
        if not full_text:
            return [], [], []

        # Passes 1 & 2: cashtag + exact SP500 match (existing logic)
        # Drop single-char results — these are almost always SEC form artifacts
        # (e.g. "K" extracted from "8-K", "D" from "Form D").
        p1_p2_tickers = [t for t in extract_tickers(full_text) if len(t) >= 2]

        # Pass 3: spaCy NER → TickerMapper
        resolved: list[dict] = []
        unresolved: list[dict] = []
        pass3_tickers: list[str] = []

        nlp = self._get_nlp()
        if nlp is not None:
            doc = nlp(full_text[:1_000_000])  # spaCy has a text size limit
            seen_entities: set[str] = set()

            for ent in doc.ents:
                if ent.label_ not in ("ORG", "PERSON", "GPE"):
                    continue

                entity_text = ent.text.strip()
                if not entity_text or len(entity_text) <= 1:
                    continue
                if entity_text.lower() in _ENTITY_BLOCKLIST:
                    continue
                if entity_text in seen_entities:
                    continue
                seen_entities.add(entity_text)

                ticker, confidence = self._mapper.get_ticker(entity_text)

                if ticker:
                    resolved.append({
                        "article_id": article_id,
                        "entity_text": entity_text,
                        "entity_type": ent.label_,
                        "ticker": ticker,
                        "confidence": confidence,
                    })
                    pass3_tickers.append(ticker)
                else:
                    unresolved.append({
                        "entity_text": entity_text,
                        "entity_type": ent.label_,
                        "article_id": article_id,
                    })

        all_tickers = list(dict.fromkeys(p1_p2_tickers + pass3_tickers))
        return resolved, all_tickers, unresolved

    # ── Private helpers ─────────────────────────────────────────────────────

    def _get_nlp(self):
        """
        Lazy-load en_core_web_sm; returns None if unavailable.
        Catches ImportError (missing shared libs like libz.so.1), OSError
        (model not installed), and any other failure so the pipeline
        degrades gracefully to Pass 1+2 (cashtag + exact ticker scan).
        """
        if self._nlp is None:
            try:
                import spacy  # may raise ImportError if libz.so.1 missing
                self._nlp = spacy.load("en_core_web_sm")
                logger.info("[entity_extractor] en_core_web_sm loaded successfully.")
            except ImportError as exc:
                logger.warning(
                    f"[entity_extractor] spaCy import failed (missing shared lib?): {exc}. "
                    "NER disabled — falling back to cashtag/ticker-list extraction only."
                )
                self._nlp = False  # sentinel: don't retry
            except OSError as exc:
                logger.warning(
                    f"[entity_extractor] en_core_web_sm not found: {exc}. "
                    "NER disabled — falling back to cashtag/ticker-list extraction only."
                )
                self._nlp = False
            except Exception as exc:
                logger.warning(
                    f"[entity_extractor] Failed to load spaCy model: {exc}. "
                    "NER disabled — falling back to cashtag/ticker-list extraction only."
                )
                self._nlp = False
        return self._nlp if self._nlp is not False else None
