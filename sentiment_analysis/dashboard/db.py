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
        with _get_engine().begin() as conn:
            return pd.read_sql(text(sql), conn, params=params or {})
    except Exception as exc:
        print(f"[db] query error: {exc}")
        return pd.DataFrame()


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
