"""
SQLAlchemy ORM models for the sentiment analysis pipeline.

Schema is deliberately TimescaleDB-compatible: TIMESTAMPTZ columns on every
table and GIN indexes on the `tickers` arrays allow Phase 5 to convert
`tweets` and `rss_articles` to hypertables with a single ALTER TABLE call.
"""
from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


class Tweet(Base):
    """Raw tweet record ingested from the Twitter/X v2 Recent Search endpoint."""

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
    """Raw RSS article record from any of the configured news feeds."""

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

    __table_args__ = (
        Index("ix_rss_articles_tickers", "tickers", postgresql_using="gin"),
        Index("ix_rss_articles_ingested_at", "ingested_at"),
        Index("ix_rss_articles_source_name", "source_name"),
        Index("ix_rss_articles_sentiment_analyzed_at", "sentiment_analyzed_at"),
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

    def __repr__(self) -> str:
        return f"<TickerPrice {self.ticker} ${self.price}>"


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
