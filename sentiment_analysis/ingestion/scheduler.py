"""
APScheduler setup for the ingestion pipeline.

Jobs:
  - RSS poll         — every settings.rss_poll_interval min (default 5)
  - NLP pipeline     — every 10 min
  - Sentiment        — every 5 min
  - Price ingestor   — every 1 min, 24/7 (Finviz returns last close outside hours)
  - Cleanup          — on startup + daily at midnight ET, deletes rows older than 2 days
"""
from __future__ import annotations

import asyncio
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.finviz_ingestor import FinvizIngestor
from sentiment_analysis.ingestion.rss_ingestor import RSSIngestor
from sentiment_analysis.nlp.pipeline import run_nlp_pipeline
from sentiment_analysis.sentiment.pipeline import run_sentiment_pipeline

_rss_ingestor: Optional[RSSIngestor] = None

PERMANENT_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
    "TSLA", "JPM", "V", "WMT", "JNJ", "XOM", "BAC",
    "UNH", "MA", "HD", "CVX", "PG", "LLY", "ABBV",
    "NFLX", "AMD", "INTC", "CSCO", "ADBE", "QCOM",
    "TXN", "CRM", "ORCL", "IBM", "GE", "CAT", "HON",
    "BA", "RTX", "GS", "MS", "WFC", "C", "USB",
    "SPY", "QQQ", "IWM", "DIA", "VXX",
]


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
    """Scheduled job: bulk-fetch all stock prices from Finviz Elite screener export."""
    if not settings.finviz_token:
        logger.warning("[finviz] FINVIZ_TOKEN not set — skipping price fetch.")
        return

    price_data = await asyncio.to_thread(
        FinvizIngestor(settings.finviz_token).fetch_all_prices
    )

    logger.info(f"[finviz] Bulk fetch: {len(price_data)} tickers updated")

    if price_data:
        await _store_prices(price_data)
        await _store_snapshot_history()


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


async def _store_snapshot_history() -> None:
    """
    Insert one row per ticker into ticker_snapshot_history for today (ET).

    Runs a single INSERT … SELECT FROM ticker_prices so all ~11k tickers are
    handled in one round-trip.  ON CONFLICT DO NOTHING means every subsequent
    ingestor run on the same calendar day is a silent, instant no-op.

    Called from _job_prices() after _store_prices() commits successfully.
    """
    from sqlalchemy import text as _text

    from sentiment_analysis.storage.database import get_async_session

    sql = _text("""
        INSERT INTO ticker_snapshot_history (
            ticker, snapshot_date,
            price, market_cap,
            pe, forward_pe, peg, price_book, price_sales,
            gross_margin, net_margin, roe, roa,
            current_ratio, debt_equity,
            eps_growth_this_year, eps_growth_next_year, eps_growth_5y,
            rsi_14, sma_20_pct, sma_50_pct, sma_200_pct,
            atr, rel_volume,
            short_float, short_ratio
        )
        SELECT
            ticker,
            (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date,
            price,       market_cap,
            pe_ratio,    forward_pe,  peg_ratio, price_to_book, price_to_sales,
            gross_margin, net_margin, roe,       roa,
            current_ratio, debt_to_equity,
            eps_growth_this_year, eps_growth_next_year, eps_growth_5y,
            rsi_14, sma_20_pct, sma_50_pct, sma_200_pct,
            atr, rel_volume,
            float_short, short_ratio
        FROM ticker_prices
        ON CONFLICT (ticker, snapshot_date) DO NOTHING
    """)

    async with get_async_session() as session:
        result = await session.execute(sql)
        await session.commit()

    inserted = result.rowcount
    if inserted > 0:
        logger.info(f"[snapshot] Inserted {inserted} new daily snapshots.")
    else:
        logger.debug("[snapshot] Daily snapshots already recorded for today — skipped.")


async def _job_cleanup() -> None:
    """Delete rows older than 2 days from all pipeline tables."""
    from sentiment_analysis.storage.database import get_async_session
    from sqlalchemy import text as _text

    _TABLES = [
        ("rss_articles",             "ingested_at"),
        ("extracted_entities",       "created_at"),
        ("ticker_sentiment_summary", "calculated_at"),
        ("sentiment_spikes",         "detected_at"),
        ("ingestion_log",            "run_at"),
    ]

    async with get_async_session() as session:
        total_deleted = 0
        for table, col in _TABLES:
            result = await session.execute(_text(
                f"DELETE FROM {table} WHERE {col} < NOW() - INTERVAL '2 days'"
            ))
            total_deleted += result.rowcount
        await session.commit()

    logger.info(f"[cleanup] Deleted {total_deleted} rows older than 2 days.")


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

    scheduler.add_job(
        _job_cleanup,
        trigger=CronTrigger(hour=0, minute=0, timezone="America/New_York"),
        id="cleanup",
        name="Daily Data Cleanup (2-day retention)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    logger.info(
        f"Scheduler configured — RSS every {settings.rss_poll_interval}m, "
        "NLP every 10m, Sentiment every 5m, Finviz prices every 1m, "
        "Cleanup daily at midnight ET"
    )
    return scheduler


async def start_scheduler() -> AsyncIOScheduler:
    """Build, start, and immediately fire all jobs once."""
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started — running initial pass.")

    await _job_cleanup()
    await _job_rss()
    await _job_nlp()
    await _job_sentiment()
    await _job_prices()

    return scheduler
