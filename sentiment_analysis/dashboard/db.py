"""
Synchronous database helpers for Dash callbacks.
All functions use a NullPool psycopg2 engine so Dash can safely call them
from synchronous callback threads without connection-pool leaks.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytz
import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

_ET = pytz.timezone("America/New_York")


def now_et() -> datetime:
    """Return the current time in Eastern Time (handles EST/EDT automatically)."""
    return datetime.now(_ET)


from sentiment_analysis.config import settings

_engine = None

_ACTIVE_SOURCES = (
    "'rss:pr_newswire','rss:globe_newswire_finance','rss:globe_newswire_ma',"
    "'rss:sec_edgar','rss:sec_form4','rss:sec_10q','rss:sec_s1','rss:sec_sc13g',"
    "'rss:fda'"
)


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.sync_database_url,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=8000",
            },
        )
    return _engine


def query_df(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    """Execute a read-only query and return a DataFrame; returns empty DF on error."""
    try:
        with _get_engine().connect() as conn:
            result = conn.execute(text(sql).bindparams(**(params or {})))
            rows = result.fetchall()
            cols = list(result.keys())
            return pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        logger.error(f"[db] query error: {exc}\nparams={params}\nSQL={sql.strip()[:400]}")
        return pd.DataFrame()


def execute_write(sql: str, params: dict | None = None) -> bool:
    """Execute a write statement (INSERT/UPDATE/DELETE); returns True on success."""
    try:
        with _get_engine().begin() as conn:
            conn.execute(text(sql).bindparams(**(params or {})))
        return True
    except Exception as exc:
        logger.error(f"[db] write error: {exc}")
        return False


def watchlist_tickers() -> list[str]:
    """Return all watched tickers in insertion order."""
    df = query_df("SELECT ticker FROM watchlist ORDER BY created_at ASC")
    return df["ticker"].tolist() if not df.empty else []


def watchlist_add(ticker: str) -> bool:
    """Add a ticker; silently ignores duplicates. Returns True on success."""
    return execute_write(
        "INSERT INTO watchlist (ticker) VALUES (:ticker) ON CONFLICT (ticker) DO NOTHING",
        {"ticker": ticker.upper()},
    )


def watchlist_remove(ticker: str) -> bool:
    """Remove a ticker from the watchlist. Returns True on success."""
    return execute_write(
        "DELETE FROM watchlist WHERE ticker = :ticker",
        {"ticker": ticker.upper()},
    )


def query_status() -> str:
    """Return 'Running', 'Degraded', or 'Error' based on recent ingestion logs."""
    df = query_df(f"""
        SELECT DISTINCT ON (source)
            source,
            error_message
        FROM   ingestion_log
        WHERE  run_at > NOW() - INTERVAL '30 minutes'
          AND  source IN ({_ACTIVE_SOURCES})
        ORDER  BY source, run_at DESC
    """)
    if df.empty:
        return "Error"
    error_count = int(df["error_message"].fillna("").str.strip().ne("").sum())
    if error_count > 2:
        return "Error"
    if error_count > 0:
        return "Degraded"
    return "Running"
