"""
Sentiment analysis pipeline orchestrator.

Picks up articles whose NLP preprocessing is done (cleaned_text IS NOT NULL)
but whose sentiment analysis has not yet run (sentiment_analyzed_at IS NULL),
sends them to Gemini in one batch, stores the results, then triggers
aggregation and spike detection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import update as sa_update

from sentiment_analysis.sentiment.aggregator import (
    aggregate_ticker_sentiment,
    detect_spikes,
)
from sentiment_analysis.sentiment.gemini_analyzer import GeminiAnalyzer
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import RSSArticle
from sqlalchemy import select

_analyzer: Optional[GeminiAnalyzer] = None


def _get_analyzer() -> GeminiAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = GeminiAnalyzer()
    return _analyzer


async def run_sentiment_pipeline(batch_size: int = 50) -> None:
    """
    Analyze one batch of articles.

    Articles that are skipped (low confidence or API error) are still marked
    with sentiment_analyzed_at = NOW() so they are not re-queued endlessly.
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

    logger.info(f"[sentiment] Analyzing {len(articles)} articles via Gemini.")

    # 2. Prepare batch input for the analyzer
    batch_input = [
        {
            "article_id": article.id,
            "ticker": ", ".join(article.tickers or []),
            "headline": article.title or "",
            "cleaned_text": article.cleaned_text or "",
        }
        for article in articles
    ]

    # 3. Run Gemini (sync client wrapped in thread)
    analyzer = _get_analyzer()
    results = await asyncio.to_thread(analyzer.analyze_batch, batch_input)

    now = datetime.now(timezone.utc)
    analyzed_ids: set[UUID] = set()

    # 4. Store high-confidence sentiment results
    if results:
        async with get_async_session() as session:
            for r in results:
                art = await session.get(RSSArticle, r["article_id"])
                if art is None:
                    continue
                art.sentiment_label = r["sentiment_label"]
                art.sentiment_score = r["sentiment_score"]
                art.sentiment_confidence = r["sentiment_confidence"]
                art.sentiment_analyzed_at = now
                analyzed_ids.add(r["article_id"])

        logger.info(f"[sentiment] Stored {len(results)} results.")

    # 5. Mark remaining articles as analyzed (prevents infinite retry)
    skipped = [a.id for a in articles if a.id not in analyzed_ids]
    if skipped:
        async with get_async_session() as session:
            await session.execute(
                sa_update(RSSArticle)
                .where(RSSArticle.id.in_(skipped))
                .values(sentiment_analyzed_at=now)
            )

    # 6. Aggregate and detect spikes
    await aggregate_ticker_sentiment()
    await detect_spikes()
