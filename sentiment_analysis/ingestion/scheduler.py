"""
APScheduler setup for the ingestion pipeline.

One recurring job:
  - RSS poll — every ``settings.rss_poll_interval`` min (default 5)

The module-level singleton preserves in-memory dedup state across scheduler
invocations so the same article is never stored twice.
"""
from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sentiment_analysis.config import settings
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
        trigger=IntervalTrigger(minutes=15),
        id="sentiment_pipeline",
        name="Gemini Sentiment Analysis Pipeline",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=180,
    )

    logger.info(
        f"Scheduler configured — RSS every {settings.rss_poll_interval}m, "
        "NLP every 10m, Sentiment every 15m"
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

    return scheduler
