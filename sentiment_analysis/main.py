"""
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
# Pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline() -> None:
    """Initialise the DB, start the scheduler, and run until interrupted."""
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
    Start the Streamlit dashboard as a child process.

    Stdout is piped back so the parent can see Streamlit's startup message.
    """
    dashboard_path = Path(__file__).parent / "dashboard" / "dashboard.py"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m", "streamlit",
            "run", str(dashboard_path),
            "--server.port", "8501",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logger.info(
        f"Dashboard started (PID {proc.pid})  →  http://localhost:8501"
    )
    return proc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
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
