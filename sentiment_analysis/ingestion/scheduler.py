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


async def _job_evaluate_alerts() -> None:
    """
    Evaluate all active alert rules once per minute.

    State-tracking approach: the `condition_met` column on each alert records
    whether the condition was true at the last evaluation.

    Trigger logic:
      - condition False → True  : fire alert, set condition_met = TRUE
      - condition True  → False : condition cleared, set condition_met = FALSE (no fire)
      - no change               : skip (prevents duplicate firings every minute)

    This means an alert fires exactly once when the condition first becomes true
    and can only fire again after the condition fully clears and re-triggers.
    """
    from sqlalchemy import text as _text

    from sentiment_analysis.storage.database import get_async_session

    # Load all active alerts with their current state
    async with get_async_session() as session:
        result = await session.execute(_text(
            "SELECT id, ticker, alert_type, operator, threshold, condition_met, last_seen_at "
            "FROM alerts WHERE is_active = TRUE"
        ))
        active_alerts = result.fetchall()

    if not active_alerts:
        return

    logger.debug(f"[alerts] Evaluating {len(active_alerts)} active alerts.")

    for alert in active_alerts:
        alert_id      = alert.id
        ticker        = alert.ticker
        alert_type    = alert.alert_type
        operator      = alert.operator
        threshold     = float(alert.threshold) if alert.threshold is not None else 0.0
        condition_met = bool(alert.condition_met)
        last_seen_at  = alert.last_seen_at

        # ── Price / Sentiment — edge-detection on condition_met ───────────
        if alert_type in ("price", "sentiment"):
            current_value: float | None = None

            async with get_async_session() as session:
                if alert_type == "price":
                    row = await session.execute(
                        _text("SELECT price FROM ticker_prices WHERE ticker = :t LIMIT 1"),
                        {"t": ticker},
                    )
                    r = row.fetchone()
                    if r and r.price is not None:
                        current_value = float(r.price)

                else:  # sentiment
                    row = await session.execute(
                        _text("""
                            SELECT AVG(avg_sentiment) AS avg_score
                            FROM ticker_sentiment_summary
                            WHERE ticker = :t
                              AND "window" = '24hr'
                              AND calculated_at > NOW() - INTERVAL '24 hours'
                        """),
                        {"t": ticker},
                    )
                    r = row.fetchone()
                    if r and r.avg_score is not None:
                        current_value = float(r.avg_score)

            if current_value is None:
                continue

            condition_now = (
                (operator == ">" and current_value > threshold) or
                (operator == "<" and current_value < threshold)
            )

            if condition_now and not condition_met:
                if alert_type == "price":
                    message = (
                        f"{ticker} price reached ${current_value:.2f} "
                        f"(Target {operator} ${threshold:.2f})"
                    )
                else:
                    message = (
                        f"{ticker} sentiment reached {current_value:.3f} "
                        f"(Target {operator} {threshold:.3f})"
                    )

                async with get_async_session() as session:
                    await session.execute(_text("""
                        INSERT INTO alert_history (alert_id, ticker, trigger_value, message)
                        VALUES (:alert_id, :ticker, :trigger_value, :message)
                    """), {
                        "alert_id": alert_id, "ticker": ticker,
                        "trigger_value": current_value, "message": message,
                    })
                    await session.execute(_text(
                        "UPDATE alerts SET condition_met = TRUE, updated_at = NOW() WHERE id = :id"
                    ), {"id": alert_id})
                    await session.commit()
                logger.info(f"[alerts] TRIGGERED alert {alert_id}: {message}")

            elif not condition_now and condition_met:
                async with get_async_session() as session:
                    await session.execute(_text(
                        "UPDATE alerts SET condition_met = FALSE, updated_at = NOW() WHERE id = :id"
                    ), {"id": alert_id})
                    await session.commit()
                logger.debug(f"[alerts] Alert {alert_id} ({ticker}) condition cleared — reset.")

        # ── Breaking News — fire once per new article since last_seen_at ──
        elif alert_type == "breaking_news":
            if last_seen_at is None:
                async with get_async_session() as session:
                    await session.execute(_text(
                        "UPDATE alerts SET last_seen_at = NOW(), updated_at = NOW() WHERE id = :id"
                    ), {"id": alert_id})
                    await session.commit()
                continue

            async with get_async_session() as session:
                row = await session.execute(_text("""
                    SELECT title, ingested_at
                    FROM rss_articles
                    WHERE ingested_at > :last_seen
                      AND :ticker = ANY(tickers)
                    ORDER BY ingested_at DESC
                    LIMIT 1
                """), {"last_seen": last_seen_at, "ticker": ticker})
                r = row.fetchone()

            if r:
                title_preview = (r.title or "").strip()[:100]
                message = f'Breaking news: "{title_preview}"'
                async with get_async_session() as session:
                    await session.execute(_text("""
                        INSERT INTO alert_history (alert_id, ticker, trigger_value, message)
                        VALUES (:alert_id, :ticker, :trigger_value, :message)
                    """), {
                        "alert_id": alert_id, "ticker": ticker,
                        "trigger_value": 0.0, "message": message,
                    })
                    await session.execute(_text(
                        "UPDATE alerts SET last_seen_at = :ts, updated_at = NOW() WHERE id = :id"
                    ), {"ts": r.ingested_at, "id": alert_id})
                    await session.commit()
                logger.info(f"[alerts] TRIGGERED breaking_news {alert_id} ({ticker}): {title_preview[:50]}")

        # ── Volume Spike — fire once per new spike since last_seen_at ─────
        elif alert_type == "volume_spike":
            if last_seen_at is None:
                async with get_async_session() as session:
                    await session.execute(_text(
                        "UPDATE alerts SET last_seen_at = NOW(), updated_at = NOW() WHERE id = :id"
                    ), {"id": alert_id})
                    await session.commit()
                continue

            async with get_async_session() as session:
                row = await session.execute(_text("""
                    SELECT detected_at, spike_ratio
                    FROM sentiment_spikes
                    WHERE detected_at > :last_seen
                      AND ticker = :ticker
                    ORDER BY detected_at DESC
                    LIMIT 1
                """), {"last_seen": last_seen_at, "ticker": ticker})
                r = row.fetchone()

            if r:
                message = (
                    f"{ticker} article volume {r.spike_ratio:.1f}× rolling average"
                )
                async with get_async_session() as session:
                    await session.execute(_text("""
                        INSERT INTO alert_history (alert_id, ticker, trigger_value, message)
                        VALUES (:alert_id, :ticker, :trigger_value, :message)
                    """), {
                        "alert_id": alert_id, "ticker": ticker,
                        "trigger_value": float(r.spike_ratio), "message": message,
                    })
                    await session.execute(_text(
                        "UPDATE alerts SET last_seen_at = :ts, updated_at = NOW() WHERE id = :id"
                    ), {"ts": r.detected_at, "id": alert_id})
                    await session.commit()
                logger.info(f"[alerts] TRIGGERED volume_spike {alert_id} ({ticker}): {r.spike_ratio:.1f}×")


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
        _job_evaluate_alerts,
        trigger=IntervalTrigger(minutes=1),
        id="alert_evaluator",
        name="Alert Rule Evaluator",
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
        "Alerts every 1m, Cleanup daily at midnight ET"
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
    await _job_evaluate_alerts()

    return scheduler
