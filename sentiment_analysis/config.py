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


@dataclass
class Settings:
    """Application settings resolved from the process environment."""

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/sentiment_db",
        )
    )

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # ------------------------------------------------------------------ #
    # Google Gemini (Phase 4 sentiment analysis)                           #
    # ------------------------------------------------------------------ #
    gemini_api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "")
    )

    # ------------------------------------------------------------------ #
    # Ticker watchlist                                                     #
    # ------------------------------------------------------------------ #
    ticker_watchlist: List[str] = field(
        default_factory=lambda: [
            t.strip().upper()
            for t in os.getenv(
                "TICKER_WATCHLIST",
                "AAPL,TSLA,NVDA,MSFT,AMZN,GOOGL,META,SPY,QQQ",
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
            "sec_10q": (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=10-Q&dateb=&owner=include&count=40&output=atom"
            ),
            "sec_s1": (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=S-1&dateb=&owner=include&count=40&output=atom"
            ),
            "sec_sc13g": (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=SC+13G&dateb=&owner=include&count=40&output=atom"
            ),
            # Regulatory
            "fda": (
                "https://www.fda.gov/about-fda/contact-fda/stay-informed"
                "/rss-feeds/press-releases/rss.xml"
            ),
        }
    )

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
