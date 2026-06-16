"""
Central configuration for the stock sentiment analysis system.
All settings are loaded from environment variables via python-dotenv.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Imported here so the default watchlist covers the full S&P 500 without
# requiring a massive env-var string.  ticker_list.py has no project imports,
# so there is no circular dependency.
from sentiment_analysis.ingestion.ticker_list import SP500_TICKERS as _SP500_TICKERS


@dataclass
class Settings:
    """Application settings resolved from the process environment."""

    # ------------------------------------------------------------------ #
    # Database  (resolved in __post_init__)                               #
    # ------------------------------------------------------------------ #
    database_url: str = field(default="")

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # ------------------------------------------------------------------ #
    # Groq (Phase 4 sentiment analysis)                                    #
    # ------------------------------------------------------------------ #
    groq_api_key: str = field(
        default_factory=lambda: os.getenv("GROQ_API_KEY", "")
    )

    # ------------------------------------------------------------------ #
    # Ticker watchlist — defaults to the full S&P 500 + ETF universe     #
    # Override via TICKER_WATCHLIST env var (comma-separated)             #
    # ------------------------------------------------------------------ #
    ticker_watchlist: List[str] = field(
        default_factory=lambda: [
            t.strip().upper()
            for t in os.getenv(
                "TICKER_WATCHLIST",
                ",".join(_SP500_TICKERS),
            ).split(",")
            if t.strip()
        ]
    )

    # ------------------------------------------------------------------ #
    # Scheduler intervals (minutes)                                        #
    # ------------------------------------------------------------------ #
    rss_poll_interval: int = field(
        default_factory=lambda: int(
            os.getenv("RSS_POLL_INTERVAL_MINUTES", "5")
        )
    )

    # ------------------------------------------------------------------ #
    # RSS feed definitions: internal_name -> URL                           #
    # ------------------------------------------------------------------ #
    rss_feeds: Dict[str, str] = field(
        default_factory=lambda: {
            # Newswires
            "pr_newswire":            "https://www.prnewswire.com/rss/news-releases-list.rss",
            "globe_newswire_finance": "https://www.globenewswire.com/RssFeed/subjectcode/15-Financial%20Services",
            "globe_newswire_ma":      "https://www.globenewswire.com/RssFeed/subjectcode/14-Mergers%20and%20Acquisitions",
            # SEC EDGAR
            "sec_edgar": (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom"
            ),
            "sec_form4": (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=4&dateb=&owner=include&count=40&output=atom"
            ),
            # Regulatory
            "fda": (
                "https://www.fda.gov/about-fda/contact-fda/stay-informed"
                "/rss-feeds/press-releases/rss.xml"
            ),
        }
    )

    def __post_init__(self) -> None:
        url = (
            os.environ.get("DATABASE_URL") or
            os.environ.get("POSTGRES_URL") or
            os.environ.get("POSTGRESQL_URL") or
            ""
        )
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not url:
            raise RuntimeError(
                "No database URL found in environment variables.\n"
                "Set DATABASE_URL, POSTGRES_URL, or POSTGRESQL_URL to your\n"
                "PostgreSQL connection string:\n"
                "  postgresql+asyncpg://user:password@host:5432/dbname\n"
                "On Railway: add it in your service's Variables tab."
            )
        self.database_url = url

    @property
    def sync_database_url(self) -> str:
        """
        Synchronous PostgreSQL URL (psycopg2) derived from the async URL.

        Strips the +asyncpg driver specifier so SQLAlchemy uses its default
        psycopg2 dialect for synchronous Streamlit queries.
        """
        return self.database_url.replace("+asyncpg", "")


# Module-level singleton — import this everywhere
settings = Settings()
