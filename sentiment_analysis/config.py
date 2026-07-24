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
    # Gemini (sentiment analysis)                                          #
    # ------------------------------------------------------------------ #
    gemini_api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "")
    )

    # ------------------------------------------------------------------ #
    # Finviz Elite (real-time price data)                                  #
    # ------------------------------------------------------------------ #
    finviz_token: str = field(
        default_factory=lambda: os.getenv("FINVIZ_TOKEN", "")
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
    # FinBERT sentiment thresholds                                         #
    # score = positive_prob − negative_prob  (range −1 … +1)              #
    # score ≥  pos_threshold  → "Bullish"                                 #
    # score ≤ −neg_threshold  → "Bearish"                                 #
    # ------------------------------------------------------------------ #
    finbert_positive_threshold: float = field(
        default_factory=lambda: float(os.getenv("FINBERT_POSITIVE_THRESHOLD", "0.35"))
    )
    finbert_negative_threshold: float = field(
        default_factory=lambda: float(os.getenv("FINBERT_NEGATIVE_THRESHOLD", "0.35"))
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
    # Organized by category for maintainability.                           #
    # All URLs validated as reachable public RSS/Atom feeds.               #
    # ------------------------------------------------------------------ #
    rss_feeds: Dict[str, str] = field(
        default_factory=lambda: {
            # ── FINANCIAL NEWS ──────────────────────────────────────────
            "cnbc_finance": (
                "https://www.cnbc.com/id/100003114/device/rss/rss.html"
            ),
            "cnbc_earnings": (
                "https://www.cnbc.com/id/15839135/device/rss/rss.html"
            ),
            "yahoo_finance_news": (
                "https://finance.yahoo.com/news/rssindex"
            ),
            "marketwatch_news": (
                "https://feeds.marketwatch.com/marketwatch/marketpulse/"
            ),
            "wsj_markets": (
                "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"
            ),
            "wsj_business": (
                "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"
            ),
            "ft_markets": (
                "https://www.ft.com/markets?format=rss"
            ),
            "bloomberg_markets": (
                "https://feeds.bloomberg.com/markets/news.rss"
            ),
            "motley_fool": (
                "https://www.fool.com/feeds/index.aspx?id=fool-headlines"
            ),

            # ── PRESS RELEASES ──────────────────────────────────────────
            "pr_newswire": (
                "https://www.prnewswire.com/rss/news-releases-list.rss"
            ),
            "globe_newswire_finance": (
                "https://www.globenewswire.com/RssFeed/subjectcode/15-Financial%20Services"
            ),
            "globe_newswire_ma": (
                "https://www.globenewswire.com/RssFeed/subjectcode/14-Mergers%20and%20Acquisitions"
            ),
            "globe_newswire_earnings": (
                "https://www.globenewswire.com/RssFeed/subjectcode/05-Earnings"
            ),
            "globe_newswire_company": (
                "https://www.globenewswire.com/RssFeed/subjectcode/02-Company%20Announcement"
            ),

            # ── REGULATORY / SEC EDGAR ───────────────────────────────────
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
            "fda": (
                "https://www.fda.gov/about-fda/contact-fda/stay-informed"
                "/rss-feeds/press-releases/rss.xml"
            ),
            "fed_reserve": (
                "https://www.federalreserve.gov/feeds/press_all.xml"
            ),
            "fed_reserve_monetary": (
                "https://www.federalreserve.gov/feeds/press_monetary.xml"
            ),
            "ftc_news": (
                "https://www.ftc.gov/feeds/press-release.xml"
            ),

            # ── TECHNOLOGY ───────────────────────────────────────────────
            "techcrunch": (
                "https://techcrunch.com/feed/"
            ),
            "arstechnica": (
                "https://feeds.arstechnica.com/arstechnica/index"
            ),
            "venturebeat": (
                "https://venturebeat.com/feed/"
            ),
            "zdnet": (
                "https://www.zdnet.com/news/rss.xml"
            ),

            # ── HEALTHCARE / BIOTECH ─────────────────────────────────────
            "fierce_biotech": (
                "https://www.fiercebiotech.com/rss/xml"
            ),
            "stat_news": (
                "https://www.statnews.com/feed/"
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
