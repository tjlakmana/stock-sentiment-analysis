"""
APScheduler setup for the ingestion pipeline.

Two recurring jobs:
  - Twitter poll  — every ``settings.twitter_poll_interval`` minutes (default 2)
  - RSS poll      — every ``settings.rss_poll_interval`` minutes (default 5)

Module-level singletons for the ingestors preserve the in-memory dedup sets
across scheduler invocations, so we never store the same tweet or article twice
even if the scheduler fires slightly early or late.
"""
from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.rss_ingestor import RSSIngestor
from sentiment_analysis.ingestion.twitter_ingestor import TwitterIngestor

# Module-level singletons — created once and reused by every job invocation
_twitter_ingestor: Optional[TwitterIngestor] = None
_rss_ingestor: Optional[RSSIngestor] = None


def _get_twitter() -> TwitterIngestor:
    global _twitter_ingestor
    if _twitter_ingestor is None:
        _twitter_ingestor = TwitterIngestor()
    return _twitter_ingestor


def _get_rss() -> RSSIngestor:
    global _rss_ingestor
    if _rss_ingestor is None:
        _rss_ingestor = RSSIngestor()
    return _rss_ingestor


async def _job_twitter() -> None:
    """Scheduled job wrapper for the Twitter ingestor."""
    await _get_twitter().run()


async def _job_rss() -> None:
    """Scheduled job wrapper for the RSS ingestor."""
    await _get_rss().run()


def build_scheduler() -> AsyncIOScheduler:
    """
    Construct an ``AsyncIOScheduler`` with both ingestion jobs registered.

    The scheduler is *not* started here; call ``scheduler.start()`` when the
    asyncio event loop is running.

    Returns:
        A fully configured but not-yet-started ``AsyncIOScheduler``.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _job_twitter,
        trigger=IntervalTrigger(minutes=settings.twitter_poll_interval),
        id="twitter_ingestor",
        name="Twitter/X Ingestion",
        replace_existing=True,
        max_instances=1,       # prevent overlapping runs
        misfire_grace_time=30, # seconds; skip if missed by more than this
    )

    scheduler.add_job(
        _job_rss,
        trigger=IntervalTrigger(minutes=settings.rss_poll_interval),
        id="rss_ingestor",
        name="RSS Feed Ingestion",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    logger.info(
        f"Scheduler configured — "
        f"Twitter every {settings.twitter_poll_interval}m, "
        f"RSS every {settings.rss_poll_interval}m"
    )
    return scheduler


async def start_scheduler() -> AsyncIOScheduler:
    """
    Build and start the scheduler, then immediately fire both jobs once.

    The immediate first run ensures data starts flowing without waiting for
    the first interval to elapse after process start.

    Returns:
        The running ``AsyncIOScheduler`` instance.
    """
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started — running initial ingestion pass.")

    # Fire immediately (don't await — let them run concurrently)
    await _job_twitter()
    await _job_rss()

    return scheduler
