"""
Entity Resolution Pipeline — production-ready orchestrator.

Drop-in replacement for the legacy EntityExtractor.  The extract() method
has an identical signature and return-type contract so nlp/pipeline.py
needs only its import line changed.

Five-stage pipeline
-------------------
1. Explicit ticker scan  — $AAPL, NASDAQ:MSFT, Ticker: TSLA (bypasses NER)
2. spaCy NER             — extracts all named-entity spans from title + summary
3. Entity classification — maps each span to the 18-type taxonomy
4. Company resolution    — ticker lookup via multi-signal CompanyResolver
5. Result assembly       — same (resolved, new_tickers, unresolved) tuple format

False-positive reduction
------------------------
- Only entities classified as "Company" or "ETF" reach ticker resolution.
- GovernmentAgency, Person, Country, Disease, Drug, Animal, etc. are dropped.
- The resolver prefers NO ticker over a wrong ticker (conservative fuzzy threshold).
- Explicit tickers from scan_explicit() are added even when spaCy misses them.
"""
from __future__ import annotations

from uuid import UUID

from loguru import logger

from sentiment_analysis.entity_resolution.classifier import EntityClassifier
from sentiment_analysis.entity_resolution.company_resolver import CompanyResolver
from sentiment_analysis.entity_resolution.confidence import resolution_confidence
from sentiment_analysis.entity_resolution.ner import NERExtractor
from sentiment_analysis.entity_resolution.ticker_mapper import TickerMapper

# Types that can produce a ticker assignment
_TICKER_ELIGIBLE: frozenset[str] = frozenset({"Company", "ETF"})


class EntityResolutionPipeline:
    """
    Multi-stage entity resolution engine.

    Instantiate once (module-level singleton in nlp/pipeline.py).
    The spaCy model loads lazily on the first extract() call.

    Parameters
    ----------
    fuzzy_threshold : float
        Minimum rapidfuzz score (0–1) for fuzzy company-name matching.
        Default 0.82 is slightly stricter than the legacy 0.80 to reduce FPs.
    """

    def __init__(self, fuzzy_threshold: float = 0.82) -> None:
        self._ner        = NERExtractor()
        self._classifier = EntityClassifier()
        self._resolver   = CompanyResolver(TickerMapper(fuzzy_threshold))

    def extract(
        self,
        title: str,
        summary: str,
        article_id: UUID,
    ) -> tuple[list[dict], list[str], list[dict]]:
        """
        Run the full entity resolution pipeline on one article.

        Parameters
        ----------
        title      : article headline
        summary    : article body / summary
        article_id : UUID of the RSSArticle row (propagated to output dicts)

        Returns
        -------
        resolved    : list[dict]  — ExtractedEntity-ready dicts with keys:
                      article_id, entity_text, entity_type, ticker, confidence
        new_tickers : list[str]   — deduplicated tickers found in this article
        unresolved  : list[dict]  — UnresolvedEntity-ready dicts with keys:
                      entity_text, entity_type, article_id
                      (Company entities with no ticker match — potential gaps)
        """
        full_text = f"{title or ''} {summary or ''}".strip()
        if not full_text:
            return [], [], []

        # ── Stage 1: Explicit ticker scan ─────────────────────────────────
        explicit_tickers = self._resolver.scan_explicit(full_text)

        # ── Stage 2: spaCy NER ────────────────────────────────────────────
        spans = self._ner.extract(title or "", summary or "")

        resolved:     list[dict] = []
        unresolved:   list[dict] = []
        seen_tickers: set[str]   = set()

        # ── Stages 3–4: Classify → resolve each NER span ─────────────────
        for span in spans:
            entity_type = self._classifier.classify(span.text, span.label)

            if entity_type not in _TICKER_ELIGIBLE:
                logger.debug(
                    f"[entity_resolution] {span.text!r} → {entity_type} "
                    f"(skipped — not ticker-eligible)"
                )
                continue

            result = self._resolver.resolve(span.text, explicit_tickers)

            if result.ticker:
                if result.ticker not in seen_tickers:
                    seen_tickers.add(result.ticker)
                    conf = resolution_confidence(result.matched_by, result.confidence)
                    resolved.append({
                        "article_id":  article_id,
                        "entity_text": span.text,
                        "entity_type": entity_type,
                        "ticker":      result.ticker,
                        "confidence":  conf,
                    })
            else:
                # Company we couldn't map → send to unresolved queue
                unresolved.append({
                    "entity_text": span.text,
                    "entity_type": entity_type,
                    "article_id":  article_id,
                })

        # ── Stage 1 follow-up: explicit tickers not captured by NER ───────
        # e.g. "$NVDA" in text but NER didn't produce an "NVDA" ORG entity.
        # Validate each explicit ticker through the classifier before accepting.
        # Prevents government acronyms (FDA, SEC) and other non-company
        # abbreviations matched by _PAREN_TICKER from being promoted to tickers.
        for ticker in explicit_tickers:
            if ticker in seen_tickers:
                continue
            entity_type = self._classifier.classify(ticker, "ORG")
            if entity_type not in _TICKER_ELIGIBLE:
                logger.debug(
                    f"[entity_resolution] explicit {ticker!r} rejected "
                    f"(classified as {entity_type})"
                )
                continue
            seen_tickers.add(ticker)
            resolved.append({
                "article_id":  article_id,
                "entity_text": ticker,
                "entity_type": entity_type,
                "ticker":      ticker,
                "confidence":  1.0,
            })

        new_tickers = list(seen_tickers)

        if new_tickers or unresolved:
            logger.debug(
                f"[entity_resolution] article {article_id}: "
                f"{len(new_tickers)} tickers, {len(unresolved)} unresolved"
            )

        return resolved, new_tickers, unresolved
