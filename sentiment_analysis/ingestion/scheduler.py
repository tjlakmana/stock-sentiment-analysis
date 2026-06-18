"""
APScheduler setup for the ingestion pipeline.

Jobs:
  - RSS poll         — every settings.rss_poll_interval min (default 5)
  - NLP pipeline     — every 10 min
  - Sentiment        — every 5 min
  - Price ingestor   — every 1 min, 24/7 (Finviz returns last close outside hours)
"""
from __future__ import annotations

import asyncio
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.finviz_ingestor import FinvizIngestor
from sentiment_analysis.ingestion.rss_ingestor import RSSIngestor
from sentiment_analysis.nlp.pipeline import run_nlp_pipeline
from sentiment_analysis.sentiment.pipeline import run_sentiment_pipeline

_rss_ingestor: Optional[RSSIngestor] = None


def _get_rss() -> RSSIngestor:
    global _rss_ingestor
    if _rss_ingestor is None:
        _rss_ingestor = RSSIngestor()
    return _rss_ingestor


async def _job_rss() -> None:
    """Scheduled job: poll all configured RSS feeds."""
    await _get_rss().run()


async def _job_nlp() -> None:
    """Scheduled job: run NLP preprocessing on unprocessed articles."""
    await run_nlp_pipeline()


async def _job_sentiment() -> None:
    """Scheduled job: run Gemini sentiment analysis on NLP-processed articles."""
    await run_sentiment_pipeline()


async def _job_prices() -> None:
    """Scheduled job: fetch price data for tickers mentioned in recent articles."""
    if not settings.finviz_token:
        logger.warning("[finviz] FINVIZ_TOKEN not set — skipping price fetch.")
        return

    # Fetch tickers that appeared in articles over the last 24 hours
    from sentiment_analysis.storage.database import get_async_session
    from sqlalchemy import text as _text

    async with get_async_session() as session:
        result = await session.execute(_text(
            "SELECT DISTINCT unnest(tickers) AS ticker "
            "FROM rss_articles "
            "WHERE ingested_at > NOW() - INTERVAL '24 hours' "
            "  AND tickers IS NOT NULL AND array_length(tickers, 1) > 0 "
            "ORDER BY ticker LIMIT 200"
        ))
        tickers = [row[0] for row in result.fetchall() if row[0]]

    if not tickers:
        logger.debug("[finviz] No active tickers in last 24h — skipping price fetch.")
        return

    price_data = await asyncio.to_thread(
        FinvizIngestor(settings.finviz_token).get_quotes_batch, tickers
    )

    if price_data:
        await _store_prices(price_data)


async def _store_prices(price_data: list[dict]) -> None:
    """Upsert price rows into ticker_prices (one row per ticker, updated in place)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from sentiment_analysis.storage.database import get_async_session
    from sentiment_analysis.storage.models import TickerPrice

    async with get_async_session() as session:
        for row in price_data:
            stmt = (
                pg_insert(TickerPrice)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={k: v for k, v in row.items() if k != "ticker"},
                )
            )
            await session.execute(stmt)

    logger.debug(f"[price] Upserted {len(price_data)} price records.")


def build_scheduler() -> AsyncIOScheduler:
    """
    Construct an ``AsyncIOScheduler`` with the RSS ingestion job registered.

    Not started here — call ``scheduler.start()`` once the event loop is running.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _job_rss,
        trigger=IntervalTrigger(minutes=settings.rss_poll_interval),
        id="rss_ingestor",
        name="RSS Feed Ingestion",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    scheduler.add_job(
        _job_nlp,
        trigger=IntervalTrigger(minutes=10),
        id="nlp_pipeline",
        name="NLP Preprocessing Pipeline",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        _job_sentiment,
        trigger=IntervalTrigger(minutes=5),
        id="sentiment_pipeline",
        name="Gemini Sentiment Analysis Pipeline",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=180,
    )

    scheduler.add_job(
        _job_prices,
        trigger=IntervalTrigger(minutes=1),
        id="price_ingestor",
        name="Price Data Fetcher",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    logger.info(
        f"Scheduler configured — RSS every {settings.rss_poll_interval}m, "
        "NLP every 10m, Sentiment every 5m, Finviz prices every 1m"
    )
    return scheduler


async def start_scheduler() -> AsyncIOScheduler:
    """Build, start, and immediately fire both jobs once."""
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started — running initial ingestion pass.")

    await _job_rss()
    await _job_nlp()
    await _job_sentiment()
    await _job_prices()

    return scheduler
