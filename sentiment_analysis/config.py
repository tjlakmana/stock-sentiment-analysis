"""
Central configuration for the stock sentiment analysis system.
All settings are loaded from environment variables via python-dotenv.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """Application settings resolved from the process environment."""

    # ------------------------------------------------------------------ #
    # Twitter / X API                                                      #
    # ------------------------------------------------------------------ #
    twitter_bearer_token: str = field(
        default_factory=lambda: os.getenv("TWITTER_BEARER_TOKEN", "")
    )

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
    twitter_poll_interval: int = field(
        default_factory=lambda: int(
            os.getenv("TWITTER_POLL_INTERVAL_MINUTES", "2")
        )
    )
    rss_poll_interval: int = field(
        default_factory=lambda: int(
            os.getenv("RSS_POLL_INTERVAL_MINUTES", "5")
        )
    )

    # ------------------------------------------------------------------ #
    # Financial keywords for Twitter search queries                        #
    # ------------------------------------------------------------------ #
    financial_keywords: List[str] = field(
        default_factory=lambda: [
            "earnings",
            "revenue",
            "guidance",
            "upgrade",
            "downgrade",
            "beat",
            "miss",
            "short",
            "squeeze",
        ]
    )

    # ------------------------------------------------------------------ #
    # RSS feed definitions: internal_name -> URL                           #
    # ------------------------------------------------------------------ #
    rss_feeds: Dict[str, str] = field(
        default_factory=lambda: {
            "investing_com": "https://www.investing.com/rss/news.rss",
            "cnbc_top_news": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "marketwatch": "http://feeds.marketwatch.com/marketwatch/topstories",
            "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
            "seeking_alpha": "https://seekingalpha.com/feed.xml",
            "benzinga": "https://www.benzinga.com/feed",
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
