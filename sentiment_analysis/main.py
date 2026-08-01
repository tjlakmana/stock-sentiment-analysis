"""
Module: main.py
Purpose: Application entry point — starts the ingestion pipeline and optionally the Dash monitoring dashboard
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Phase 2 entry point — starts the ingestion pipeline and monitoring dashboard.

Usage
-----
From the project root (``c:/.../Stock Sentiment/``):

    python -m sentiment_analysis.main          # pipeline + dashboard
    python -m sentiment_analysis.main --no-ui  # pipeline only

Environment
-----------
Copy ``sentiment_analysis/.env.example`` to ``sentiment_analysis/.env``
(or the project root) and fill in your values before running.

Alembic (first-time setup)
--------------------------
    cd sentiment_analysis
    alembic upgrade head
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from loguru import logger

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.scheduler import start_scheduler
from sentiment_analysis.storage.database import async_engine
from sentiment_analysis.storage.models import Base


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

async def _init_db() -> None:
    """
    Create all tables that don't yet exist.

    In production, prefer ``alembic upgrade head`` for proper migration tracking.
    This auto-create is a convenience for development / first run.
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema verified / created.")


# ---------------------------------------------------------------------------
# Schema patch — idempotent column additions for Railway cold starts
# ---------------------------------------------------------------------------

def run_migrations() -> None:
    """
    Apply idempotent schema patches needed for Railway cold-start compatibility.

    Uses ``ADD COLUMN IF NOT EXISTS`` and ``CREATE TABLE IF NOT EXISTS`` so every
    statement is safe to run on an already-up-to-date schema.  This supplements
    Alembic rather than replacing it — proper Alembic migrations track version
    history; this function handles columns/tables that may have been added by
    ``Base.metadata.create_all`` without the server_default that Alembic would set.

    Raises:
        sqlalchemy.exc.OperationalError: If the database connection fails.
    """
    from sqlalchemy import create_engine, text

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)

    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS company_name VARCHAR;"
        ))
        conn.execute(text(
            "ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS sector VARCHAR;"
        ))
        conn.execute(text(
            "ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS country VARCHAR;"
        ))
        conn.execute(text(
            "ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS exchange VARCHAR;"
        ))
        conn.execute(text(
            "ALTER TABLE rss_articles ADD COLUMN IF NOT EXISTS primary_ticker VARCHAR(20);"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id         SERIAL PRIMARY KEY,
                ticker     VARCHAR(20) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_watchlist_ticker UNIQUE (ticker)
            );
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_watchlist_ticker ON watchlist (ticker);"
        ))
        # ── Alerts tables (migrations 019–021) ────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alerts (
                id            SERIAL PRIMARY KEY,
                ticker        VARCHAR(20)  NOT NULL,
                alert_type    VARCHAR(20)  NOT NULL,
                operator      VARCHAR(5)   NOT NULL,
                threshold     FLOAT        NOT NULL,
                is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
                condition_met BOOLEAN      NOT NULL DEFAULT FALSE,
                last_seen_at  TIMESTAMPTZ           DEFAULT NOW(),
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_alerts_ticker ON alerts (ticker);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_alerts_is_active ON alerts (is_active);"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id            SERIAL PRIMARY KEY,
                alert_id      INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
                ticker        VARCHAR(20)  NOT NULL,
                trigger_value FLOAT        NOT NULL,
                triggered_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                message       TEXT         NOT NULL DEFAULT ''
            );
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_alert_history_alert_id ON alert_history (alert_id);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_alert_history_triggered_at ON alert_history (triggered_at);"
        ))
        # Patch columns that may exist from a create_all run without server_default
        conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS condition_met BOOLEAN NOT NULL DEFAULT FALSE;"
        ))
        conn.execute(text(
            "ALTER TABLE alerts ALTER COLUMN condition_met SET DEFAULT FALSE;"
        ))
        conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ DEFAULT NOW();"
        ))
        conn.execute(text(
            "ALTER TABLE alerts ALTER COLUMN last_seen_at SET DEFAULT NOW();"
        ))
        conn.execute(text(
            "ALTER TABLE alert_history ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '';"
        ))
        # ── Fundamental & Technical columns (migration 015) ───────────────
        _float_cols = [
            "pe_ratio", "forward_pe", "peg_ratio", "price_to_sales", "price_to_book",
            "dividend_yield", "eps_ttm", "roe", "debt_to_equity",
            "current_ratio", "quick_ratio", "rel_volume", "rsi_14",
            "sma_20_pct", "sma_50_pct", "sma_200_pct",
            "week_52_high_pct", "week_52_low_pct",
        ]
        for col in _float_cols:
            conn.execute(text(
                f"ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS {col} FLOAT;"
            ))
        conn.execute(text(
            "ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS avg_volume BIGINT;"
        ))
        # ── Profitability & Beta columns (migration 016) ──────────────────
        for col in ("roa", "gross_margin", "operating_margin", "net_margin", "beta"):
            conn.execute(text(
                f"ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS {col} FLOAT;"
            ))
        # ── Tier 1 screener fields (migration 017) ────────────────────────
        _tier1_cols = [
            "float_short", "short_ratio",
            "perf_week", "perf_month", "perf_quart", "perf_year", "perf_ytd",
            "analyst_recom", "target_price",
            "eps_growth_this_year", "eps_growth_next_year", "eps_growth_5y",
            "eps_growth_qoq", "sales_growth_qoq",
            "atr", "insider_own", "inst_own",
        ]
        for col in _tier1_cols:
            conn.execute(text(
                f"ALTER TABLE ticker_prices ADD COLUMN IF NOT EXISTS {col} FLOAT;"
            ))
        conn.commit()
    logger.info("[startup] schema columns verified/added")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline() -> None:
    """
    Initialise the database, launch the scheduler, and keep the event loop alive.

    The asyncio event loop must be running before this is called; use
    ``asyncio.run(_run_pipeline())`` from the synchronous entry point.
    The scheduler runs jobs as asyncio coroutines — ``asyncio.sleep`` keeps the
    loop spinning between job firings without busy-waiting.

    Raises:
        KeyboardInterrupt: Caught internally; triggers clean scheduler shutdown.
    """
    await _init_db()
    scheduler = await start_scheduler()

    logger.info("Ingestion pipeline running.  Press Ctrl+C to stop.")
    try:
        # Keep the event loop alive; scheduler jobs run as async coroutines
        while True:
            await asyncio.sleep(30)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler.shutdown(wait=False)
        await async_engine.dispose()
        logger.info("Pipeline stopped cleanly.")


# ---------------------------------------------------------------------------
# Dashboard subprocess
# ---------------------------------------------------------------------------

def _launch_dashboard() -> subprocess.Popen:
    """
    Start the Plotly Dash dashboard as a detached child process on port 8050.

    A subprocess is used instead of running in the same process because Dash's
    development server calls ``signal.signal`` which conflicts with APScheduler's
    asyncio event loop.  The child inherits stdout/stderr so any startup crash
    (e.g. missing dash_bootstrap_components) prints directly to the terminal.

    Returns:
        subprocess.Popen: The running dashboard process (call .terminate() to stop).
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "sentiment_analysis.dashboard.dash_app"],
        # Inherit parent's stdout/stderr so any startup crash prints to the terminal
        stdout=None,
        stderr=None,
    )
    logger.info(f"Dashboard started (PID {proc.pid})  →  http://localhost:8050")
    return proc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    CLI entry point for the full pipeline.

    Parses ``--no-ui`` flag, configures loguru (console + rotating file),
    runs schema migration, optionally starts the dashboard subprocess, then
    launches the async ingestion pipeline.  The dashboard process is always
    terminated cleanly on exit, even if the pipeline crashes.

    Args:
        (none — reads sys.argv via argparse)
    """
    parser = argparse.ArgumentParser(description="Stock Sentiment Phase 2 Pipeline")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Run the ingestion pipeline without launching the dashboard",
    )
    args = parser.parse_args()

    # Configure loguru: human-readable console output + rotating log file
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — {message}"
        ),
        colorize=True,
    )
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.add(
        str(log_dir / "ingestion.log"),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} — {message}",
    )

    logger.info("=" * 60)
    logger.info("Stock Sentiment Analysis — Phase 2 starting")
    logger.info(f"  DB URL   : {settings.database_url[:50]}…")
    logger.info(f"  Log level: {settings.log_level}")
    logger.info(f"  Watchlist: {', '.join(settings.ticker_watchlist)}")
    logger.info("=" * 60)

    run_migrations()

    dashboard_proc: subprocess.Popen | None = None

    if not args.no_ui:
        dashboard_proc = _launch_dashboard()

    try:
        asyncio.run(_run_pipeline())
    finally:
        if dashboard_proc is not None:
            dashboard_proc.terminate()
            try:
                dashboard_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dashboard_proc.kill()
            logger.info("Dashboard process terminated.")


if __name__ == "__main__":
    main()
