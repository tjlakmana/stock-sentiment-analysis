"""
Module: models.py
Purpose: SQLAlchemy ORM models defining the PostgreSQL schema for the full sentiment analysis pipeline
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Schema design notes:
  - All timestamp columns use TIMESTAMPTZ (timezone-aware) for correctness across DST
  - GIN indexes on ARRAY(String) `tickers` columns enable efficient array-contains queries
    (e.g. WHERE 'AAPL' = ANY(tickers)) used throughout the pipeline and dashboard
  - The schema is TimescaleDB-compatible so rss_articles can be converted to a
    hypertable with a single ALTER TABLE call in a future phase

Table relationships:
  rss_articles ─┬─< extracted_entities (CASCADE delete)
                └─< alert_history (via alerts.id CASCADE)
  alerts ──────────< alert_history (CASCADE delete)
"""
from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class Tweet(Base):
    """
    Raw tweet record ingested from the Twitter/X v2 Recent Search endpoint.

    Kept for Phase 1 backwards compatibility — the live pipeline no longer
    ingests tweets (API access was restricted), but the table is preserved so
    historical tweet data is not lost.

    Attributes:
        id: Twitter's own string-format tweet ID (primary key).
        text: Full tweet body text.
        author_id: Twitter user ID string of the author.
        created_at: When the tweet was posted (timezone-aware).
        retweet_count: Retweet count at ingestion time.
        like_count: Like count at ingestion time.
        tickers: Array of ticker symbols extracted from tweet text.
        raw_json: Complete Twitter API v2 response object for this tweet.
        ingested_at: When we stored the tweet (server-set default).
    """

    __tablename__ = "tweets"

    id = Column(String, primary_key=True)
    text = Column(Text)
    author_id = Column(String)
    created_at = Column(DateTime(timezone=True))
    retweet_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    tickers = Column(ARRAY(String), default=list)
    raw_json = Column(JSONB)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # GIN index for efficient array-contains queries on tickers
        Index("ix_tweets_tickers", "tickers", postgresql_using="gin"),
        Index("ix_tweets_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Tweet id={self.id} tickers={self.tickers}>"


class RSSArticle(Base):
    """
    Raw RSS article record from any of the configured news feeds.

    Central table of the pipeline: the RSS ingestor writes it, NLP fills
    cleaned_text and tickers, and the sentiment pipeline fills the
    sentiment_* columns.  A NULL cleaned_text means NLP hasn't run yet;
    a NULL sentiment_analyzed_at means sentiment hasn't run yet.

    Attributes:
        id: Server-generated UUID primary key (gen_random_uuid()).
        title: Article headline from the RSS feed.
        summary: Article body or excerpt from the RSS feed.
        url: Canonical article URL — UNIQUE constraint prevents re-ingestion.
        published_at: Publication timestamp from the feed's <pubDate>.
        source_name: Feed identifier matching settings.rss_feeds keys.
        tickers: Ticker symbols extracted by the 3-pass NLP extractor.
        raw_json: Original feedparser entry dict for debugging.
        ingested_at: When we stored the article (server-set default).
        cleaned_text: Lemmatised, stop-word-filtered text for NLP; NULL = unprocessed.
        sentiment_label: Human-readable label ('Bullish' / 'Bearish' / etc.).
        sentiment_score: Float in [-1.0, +1.0]; positive = bullish.
        sentiment_confidence: Model confidence (0.0–1.0).
        sentiment_analyzed_at: When sentiment ran; NULL = not yet analyzed.
        primary_ticker: The single company this article is primarily about, per scorer.
    """

    __tablename__ = "rss_articles"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    title = Column(Text)
    summary = Column(Text)
    url = Column(Text, unique=True, nullable=False)
    published_at = Column(DateTime(timezone=True))
    source_name = Column(String(100))
    tickers = Column(ARRAY(String), default=list)
    raw_json = Column(JSONB)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    cleaned_text = Column(Text, nullable=True)
    sentiment_label = Column(Text, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    sentiment_confidence = Column(Float, nullable=True)
    sentiment_analyzed_at = Column(DateTime(timezone=True), nullable=True)
    primary_ticker = Column(String(20), nullable=True)

    __table_args__ = (
        Index("ix_rss_articles_tickers", "tickers", postgresql_using="gin"),
        Index("ix_rss_articles_ingested_at", "ingested_at"),
        Index("ix_rss_articles_source_name", "source_name"),
        Index("ix_rss_articles_sentiment_analyzed_at", "sentiment_analyzed_at"),
        Index("ix_rss_articles_primary_ticker", "primary_ticker"),
    )

    def __repr__(self) -> str:
        return f"<RSSArticle url={str(self.url)[:60]} tickers={self.tickers}>"


class IngestionLog(Base):
    """
    Audit log written after every ingestion run (one row per source per poll).

    Used by the monitoring dashboard to compute pipeline health, last-run
    times, and records-ingested-per-bucket time-series.
    """

    __tablename__ = "ingestion_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 'twitter' | 'rss:reuters_business' | 'rss:cnbc_top_news' | …
    source = Column(String(100), nullable=False)
    run_at = Column(DateTime(timezone=True), server_default=func.now())
    records_fetched = Column(Integer, default=0)
    records_stored = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ingestion_log_run_at", "run_at"),
        Index("ix_ingestion_log_source", "source"),
    )

    def __repr__(self) -> str:
        return (
            f"<IngestionLog source={self.source} "
            f"stored={self.records_stored} at={self.run_at}>"
        )


class ExtractedEntity(Base):
    """Named entity extracted by spaCy NER that was resolved to a ticker."""

    __tablename__ = "extracted_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rss_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_text = Column(Text, nullable=False)
    entity_type = Column(String(50))      # ORG | PERSON | GPE
    ticker = Column(String(20))
    confidence = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_extracted_entities_article_id", "article_id"),
        Index("ix_extracted_entities_ticker", "ticker"),
    )

    def __repr__(self) -> str:
        return f"<ExtractedEntity {self.entity_text!r} → {self.ticker}>"


class UnresolvedEntity(Base):
    """
    Entity that could not be mapped to a ticker — queued for manual review.

    Duplicate entity_text values (when status='pending') increment frequency
    rather than inserting a new row so the most-mentioned unknowns float up.
    """

    __tablename__ = "unresolved_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_text = Column(Text, nullable=False)
    entity_type = Column(String(50))
    article_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rss_articles.id", ondelete="SET NULL"),
        nullable=True,
    )
    frequency = Column(Integer, default=1)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_ticker = Column(String(20), nullable=True)

    __table_args__ = (
        Index("ix_unresolved_entities_status", "status"),
        Index("ix_unresolved_entities_entity_text", "entity_text"),
    )

    def __repr__(self) -> str:
        return (
            f"<UnresolvedEntity {self.entity_text!r} "
            f"freq={self.frequency} status={self.status}>"
        )


class TickerSentimentSummary(Base):
    """Per-ticker aggregated sentiment for a rolling time window."""

    __tablename__ = "ticker_sentiment_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(Text, nullable=False)
    window = Column(String(10), nullable=False)       # '1hr' | '4hr' | '24hr'
    window_start = Column(DateTime(timezone=True), nullable=False)
    avg_sentiment = Column(Float, nullable=True)
    article_count = Column(Integer, nullable=False, default=0)
    bullish_count = Column(Integer, nullable=False, default=0)
    bearish_count = Column(Integer, nullable=False, default=0)
    neutral_count = Column(Integer, nullable=False, default=0)
    momentum = Column(String(20), nullable=True)      # 'improving' | 'declining' | 'stable'
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_tss_ticker_window", "ticker", "window"),
        Index("ix_tss_calculated_at", "calculated_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<TickerSentimentSummary {self.ticker} {self.window} "
            f"avg={self.avg_sentiment:.3f}>"
        )


class TickerPrice(Base):
    """Latest price snapshot per ticker, refreshed by the price ingestor."""

    __tablename__ = "ticker_prices"

    ticker            = Column(Text,                  primary_key=True)
    price             = Column(Float,                 nullable=True)
    change_pct        = Column(Float,                 nullable=True)
    volume            = Column(BigInteger,            nullable=True)
    market_cap        = Column(BigInteger,            nullable=True)
    pre_market_price  = Column(Float,                 nullable=True)
    post_market_price = Column(Float,                 nullable=True)
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    company_name      = Column(Text,        nullable=True)
    sector            = Column(String(100), nullable=True)
    country           = Column(String(50),  nullable=True)
    exchange          = Column(String(20),  nullable=True)

    # ── Fundamental ───────────────────────────────────────────────────────
    pe_ratio        = Column(Float, nullable=True)
    forward_pe      = Column(Float, nullable=True)
    peg_ratio       = Column(Float, nullable=True)
    price_to_sales  = Column(Float, nullable=True)
    price_to_book   = Column(Float, nullable=True)
    dividend_yield  = Column(Float, nullable=True)   # stored as percent, e.g. 3.5
    eps_ttm         = Column(Float, nullable=True)
    roe             = Column(Float, nullable=True)   # stored as percent, e.g. 15.0
    debt_to_equity  = Column(Float, nullable=True)
    current_ratio   = Column(Float, nullable=True)
    quick_ratio     = Column(Float, nullable=True)

    # ── Technical ─────────────────────────────────────────────────────────
    avg_volume      = Column(BigInteger, nullable=True)
    rel_volume      = Column(Float,      nullable=True)
    rsi_14          = Column(Float,      nullable=True)
    sma_20_pct      = Column(Float,      nullable=True)   # % current price above(+)/below(-) SMA20
    sma_50_pct      = Column(Float,      nullable=True)
    sma_200_pct     = Column(Float,      nullable=True)
    week_52_high_pct = Column(Float,     nullable=True)   # % below 52W high (negative)
    week_52_low_pct  = Column(Float,     nullable=True)   # % above 52W low (positive)
    roa              = Column(Float,     nullable=True)   # Return on Assets (%)
    gross_margin     = Column(Float,     nullable=True)   # Gross Margin (%)
    operating_margin = Column(Float,     nullable=True)   # Operating Margin (%)
    net_margin       = Column(Float,     nullable=True)   # Net Profit Margin (%)
    beta             = Column(Float,     nullable=True)

    # ── Short Interest ────────────────────────────────────────────────────
    float_short      = Column(Float,     nullable=True)   # % of float sold short
    short_ratio      = Column(Float,     nullable=True)   # days to cover

    # ── Price Performance (stored as %) ───────────────────────────────────
    perf_week        = Column(Float,     nullable=True)
    perf_month       = Column(Float,     nullable=True)
    perf_quart       = Column(Float,     nullable=True)
    perf_year        = Column(Float,     nullable=True)
    perf_ytd         = Column(Float,     nullable=True)

    # ── Analyst ───────────────────────────────────────────────────────────
    analyst_recom    = Column(Float,     nullable=True)   # 1=Strong Buy … 5=Strong Sell
    target_price     = Column(Float,     nullable=True)   # consensus price target ($)

    # ── EPS & Sales Growth (stored as %) ──────────────────────────────────
    eps_growth_this_year = Column(Float, nullable=True)
    eps_growth_next_year = Column(Float, nullable=True)
    eps_growth_5y        = Column(Float, nullable=True)
    eps_growth_qoq       = Column(Float, nullable=True)
    sales_growth_qoq     = Column(Float, nullable=True)

    # ── Volatility ────────────────────────────────────────────────────────
    atr              = Column(Float,     nullable=True)   # Average True Range ($)

    # ── Ownership (stored as %) ───────────────────────────────────────────
    insider_own      = Column(Float,     nullable=True)
    inst_own         = Column(Float,     nullable=True)

    def __repr__(self) -> str:
        return f"<TickerPrice {self.ticker} ${self.price}>"


class TickerSnapshotHistory(Base):
    """
    One row per ticker per calendar day (ET).

    Populated by the Finviz bulk ingestor after each successful upsert into
    ticker_prices.  The UNIQUE constraint on (ticker, snapshot_date) ensures
    only the first snapshot of each day is kept; subsequent ingestor runs that
    same day are silent no-ops via ON CONFLICT DO NOTHING.

    ticker_prices continues to represent the live/current snapshot — this table
    is the append-only historical archive used for time-series charts.
    """

    __tablename__ = "ticker_snapshot_history"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    ticker        = Column(Text,    nullable=False)
    snapshot_date = Column(DateTime(timezone=False), nullable=False)   # date only, ET

    # Core price
    price         = Column(Float,      nullable=True)
    market_cap    = Column(BigInteger,  nullable=True)

    # Valuation
    pe            = Column(Float, nullable=True)
    forward_pe    = Column(Float, nullable=True)
    peg           = Column(Float, nullable=True)
    price_book    = Column(Float, nullable=True)
    price_sales   = Column(Float, nullable=True)

    # Profitability
    gross_margin  = Column(Float, nullable=True)
    net_margin    = Column(Float, nullable=True)
    roe           = Column(Float, nullable=True)
    roa           = Column(Float, nullable=True)

    # Financial health
    current_ratio = Column(Float, nullable=True)
    debt_equity   = Column(Float, nullable=True)

    # Growth
    eps_growth_this_year = Column(Float, nullable=True)
    eps_growth_next_year = Column(Float, nullable=True)
    eps_growth_5y        = Column(Float, nullable=True)

    # Technical
    rsi_14        = Column(Float, nullable=True)
    sma_20_pct    = Column(Float, nullable=True)
    sma_50_pct    = Column(Float, nullable=True)
    sma_200_pct   = Column(Float, nullable=True)
    atr           = Column(Float, nullable=True)
    rel_volume    = Column(Float, nullable=True)

    # Short interest
    short_float   = Column(Float, nullable=True)
    short_ratio   = Column(Float, nullable=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Enforce one snapshot per ticker per day
        Index("uq_snapshot_ticker_date", "ticker", "snapshot_date", unique=True),
        Index("ix_snapshot_ticker", "ticker"),
        Index("ix_snapshot_date",   "snapshot_date"),
    )

    def __repr__(self) -> str:
        return f"<TickerSnapshotHistory {self.ticker} {self.snapshot_date} ${self.price}>"


class Watchlist(Base):
    """Single-user watchlist — one row per tracked ticker."""

    __tablename__ = "watchlist"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    ticker     = Column(String(20), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_watchlist_ticker", "ticker"),
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.ticker}>"


class Alert(Base):
    """
    User-defined alert rule for a single ticker.

    Supported alert_type values: 'price' | 'sentiment' | 'breaking_news' | 'volume_spike'
    Supported operator values:   '>'     | '<'

    When is_active is True the scheduler evaluates this rule every minute
    and writes a row to alert_history if the condition fires.

    Edge-detection model (price / sentiment alerts):
      - condition_met tracks whether the condition was TRUE on the *previous* evaluation.
      - Alert fires when condition flips False→True (rising edge only).
      - This prevents repeated firings every minute while the condition holds.

    Attributes:
        id: Auto-increment integer primary key.
        ticker: Stock symbol this alert monitors.
        alert_type: 'price', 'sentiment', 'breaking_news', or 'volume_spike'.
        operator: '>' or '<' for price/sentiment comparisons.
        threshold: The numeric value to compare against.
        is_active: When False the scheduler skips evaluation entirely.
        condition_met: Current condition state (used for edge detection).
        last_seen_at: High-water mark for breaking_news and volume_spike alert types.
        created_at / updated_at: Audit timestamps.
    """

    __tablename__ = "alerts"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    ticker     = Column(String(20), nullable=False)
    alert_type = Column(String(20), nullable=False)   # 'price' | 'sentiment'
    operator   = Column(String(5),  nullable=False)   # '>'     | '<'
    threshold  = Column(Float,      nullable=False)
    is_active     = Column(Boolean, nullable=False, default=True,  server_default=text("TRUE"))
    condition_met = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))  # price/sentiment: True while condition holds
    last_seen_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)   # breaking_news/volume_spike: high-water mark
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_alerts_ticker",    "ticker"),
        Index("ix_alerts_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return (
            f"<Alert #{self.id} {self.ticker} {self.alert_type} "
            f"{self.operator} {self.threshold} active={self.is_active}>"
        )


class AlertHistory(Base):
    """
    Immutable record of every time an alert rule fired.

    alert_id FK → alerts.id with CASCADE delete so removing an alert also
    removes all of its history rows automatically.
    """

    __tablename__ = "alert_history"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    alert_id      = Column(
        Integer,
        ForeignKey("alerts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker        = Column(String(20),             nullable=False)
    trigger_value = Column(Float,                  nullable=False)
    triggered_at  = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    message       = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_alert_history_alert_id",    "alert_id"),
        Index("ix_alert_history_triggered_at", "triggered_at"),
        Index("ix_alert_history_ticker",       "ticker"),
    )

    def __repr__(self) -> str:
        return (
            f"<AlertHistory #{self.id} alert={self.alert_id} "
            f"{self.ticker}={self.trigger_value} at={self.triggered_at}>"
        )


class SentimentSpike(Base):
    """Recorded when a ticker's 15-min article volume exceeds 2× its rolling avg."""

    __tablename__ = "sentiment_spikes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(Text, nullable=False)
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    article_count = Column(Integer, nullable=False)
    rolling_avg = Column(Float, nullable=False)
    spike_ratio = Column(Float, nullable=False)

    __table_args__ = (
        Index("ix_sentiment_spikes_ticker", "ticker"),
        Index("ix_sentiment_spikes_detected_at", "detected_at"),
    )

    def __repr__(self) -> str:
        return f"<SentimentSpike {self.ticker} ratio={self.spike_ratio:.1f}×>"
