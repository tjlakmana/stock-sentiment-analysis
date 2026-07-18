"""
Sentiment analysis pipeline orchestrator.

Picks up articles whose NLP preprocessing is done (cleaned_text IS NOT NULL)
but whose sentiment analysis has not yet run (sentiment_analyzed_at IS NULL),
routes them to the appropriate analyzer, stores results, then triggers
aggregation and spike detection.

Routing:
  SEC filings (sec_edgar, sec_form4, sec_10q, sec_s1, sec_sc13g)
      → SECAnalyzer  (rule-based, local, always stores a result)
  All other articles
      → FinBERTAnalyzer  PRIMARY  (peyterho/finbert-macro-sentiment, local, batched)
      → GeminiAnalyzer   FALLBACK (gemini-2.5-flash, only if FinBERT raises)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import update as sa_update
from sqlalchemy import select

from sentiment_analysis.config import settings
from sentiment_analysis.sentiment.aggregator import (
    aggregate_ticker_sentiment,
    detect_spikes,
)
from sentiment_analysis.sentiment.finbert_analyzer import FinBERTAnalyzer
from sentiment_analysis.sentiment.gemini_analyzer import GeminiAnalyzer, score_to_label
from sentiment_analysis.sentiment.sec_analyzer import SECAnalyzer
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import RSSArticle

_SEC_SOURCES = frozenset({"sec_edgar", "sec_form4", "sec_10q", "sec_s1", "sec_sc13g"})

# Applied only to Gemini fallback results (FinBERT always stores)
_GEMINI_CONFIDENCE_MIN = 0.1

_finbert_analyzer: Optional[FinBERTAnalyzer] = None
_gemini_analyzer:  Optional[GeminiAnalyzer]  = None
_sec_analyzer:     Optional[SECAnalyzer]      = None


def _get_finbert_analyzer() -> FinBERTAnalyzer:
    global _finbert_analyzer
    if _finbert_analyzer is None:
        _finbert_analyzer = FinBERTAnalyzer(
            pos_threshold=settings.finbert_positive_threshold,
            neg_threshold=settings.finbert_negative_threshold,
        )
    return _finbert_analyzer


def _get_gemini_analyzer() -> GeminiAnalyzer:
    global _gemini_analyzer
    if _gemini_analyzer is None:
        _gemini_analyzer = GeminiAnalyzer()
    return _gemini_analyzer


def _get_sec_analyzer() -> SECAnalyzer:
    global _sec_analyzer
    if _sec_analyzer is None:
        _sec_analyzer = SECAnalyzer()
    return _sec_analyzer


async def run_sentiment_pipeline(batch_size: int = 100) -> None:
    """
    Analyze one batch of articles.

    SEC articles are always stored (rule-based, no threshold).
    FinBERT results are always stored (every article gets a score).
    Gemini is used only as fallback if FinBERT raises an exception.
    Articles that still have no result after both analyzers are still marked
    analyzed (sentiment_analyzed_at=now) to prevent infinite re-queuing.
    """
    # 1. Fetch batch: NLP done, sentiment not yet run
    async with get_async_session() as session:
        result = await session.execute(
            select(RSSArticle)
            .where(
                RSSArticle.cleaned_text.is_not(None),
                RSSArticle.sentiment_analyzed_at.is_(None),
            )
            .order_by(RSSArticle.ingested_at.desc())
            .limit(batch_size)
        )
        articles = result.scalars().all()

    if not articles:
        logger.debug("[sentiment] No unanalyzed articles — pipeline idle.")
        return

    # 2. Split by source type
    sec_articles   = [a for a in articles if a.source_name in _SEC_SOURCES]
    other_articles = [a for a in articles if a.source_name not in _SEC_SOURCES]

    results: list[dict] = []

    # 3a. SEC filings → SECAnalyzer (rule-based + LM, local, no API, unchanged)
    if sec_articles:
        logger.info(
            f"[sentiment] Analyzing {len(sec_articles)} SEC articles via SEC Analyzer."
        )
        sec_batch = [
            {
                "id":          a.id,
                "source_name": a.source_name,
                "title":       a.title or "",
                "summary":     a.summary or a.cleaned_text or "",
            }
            for a in sec_articles
        ]
        for r in _get_sec_analyzer().score_batch(sec_batch):
            results.append({
                "article_id":           r["id"],
                "sentiment_label":      score_to_label(r["score"]),
                "sentiment_score":      r["score"],
                "sentiment_confidence": r["confidence"],
            })

        counts = {}
        for a in sec_articles:
            counts[a.source_name] = counts.get(a.source_name, 0) + 1
        logger.info(
            "[sentiment] SEC breakdown: "
            f"{counts.get('sec_form4', 0)} Form4, "
            f"{counts.get('sec_edgar', 0)} 8-K, "
            f"{counts.get('sec_10q',   0)} 10-Q, "
            f"{counts.get('sec_s1',    0)} S-1, "
            f"{counts.get('sec_sc13g', 0)} SC13G scored."
        )

    # 3b. Non-SEC articles → FinBERT primary, Gemini fallback
    if other_articles:
        logger.info(f"[sentiment] Analyzing {len(other_articles)} articles via FinBERT.")
        finbert_batch = [
            {
                "article_id":   a.id,
                "headline":     a.title or "",
                "cleaned_text": a.cleaned_text or "",
            }
            for a in other_articles
        ]

        finbert_results: Optional[list[dict]] = None
        try:
            finbert_results = await asyncio.to_thread(
                _get_finbert_analyzer().score_batch, finbert_batch
            )
        except Exception as exc:
            logger.error(f"[finbert] FinBERT inference failed: {exc}")
            logger.info("[finbert] Falling back to Gemini.")

        if finbert_results is not None:
            results.extend(finbert_results)
        else:
            # Gemini fallback — only reached when FinBERT raises
            if not settings.gemini_api_key:
                logger.warning(
                    "[gemini] GEMINI_API_KEY not set — Gemini fallback unavailable. "
                    f"{len(other_articles)} articles will be skipped this cycle."
                )
            else:
                logger.info(
                    f"[gemini] Running Gemini fallback for {len(other_articles)} articles."
                )
                gemini_batch = [
                    {
                        "article_id":   a.id,
                        "ticker":       ", ".join(a.tickers or []),
                        "headline":     a.title or "",
                        "cleaned_text": a.cleaned_text or "",
                    }
                    for a in other_articles
                ]
                gemini_raw = await asyncio.to_thread(
                    _get_gemini_analyzer().analyze_batch, gemini_batch
                )
                for r in gemini_raw:
                    if r["sentiment_confidence"] >= _GEMINI_CONFIDENCE_MIN:
                        results.append(r)

    now = datetime.now(timezone.utc)
    analyzed_ids: set[UUID] = set()

    # 4. Store results
    if results:
        async with get_async_session() as session:
            for r in results:
                art = await session.get(RSSArticle, r["article_id"])
                if art is None:
                    continue
                art.sentiment_label       = r["sentiment_label"]
                art.sentiment_score       = r["sentiment_score"]
                art.sentiment_confidence  = r["sentiment_confidence"]
                art.sentiment_analyzed_at = now
                analyzed_ids.add(r["article_id"])

        logger.info(f"[sentiment] Stored {len(results)} results.")

    # 5. Mark remaining articles as analyzed (prevents infinite re-queue)
    skipped = [a.id for a in articles if a.id not in analyzed_ids]
    if skipped:
        async with get_async_session() as session:
            await session.execute(
                sa_update(RSSArticle)
                .where(RSSArticle.id.in_(skipped))
                .values(sentiment_analyzed_at=now)
            )

    # 6. Aggregate per-ticker sentiment and detect article-count spikes
    await aggregate_ticker_sentiment()
    await detect_spikes()
