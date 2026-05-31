"""
SQLAlchemy ORM models for the sentiment analysis pipeline.

Schema is deliberately TimescaleDB-compatible: TIMESTAMPTZ columns on every
table and GIN indexes on the `tickers` arrays allow Phase 5 to convert
`tweets` and `rss_articles` to hypertables with a single ALTER TABLE call.
"""
from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    Column,
    DateTime,
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

    __table_args__ = (
        Index("ix_rss_articles_tickers", "tickers", postgresql_using="gin"),
        Index("ix_rss_articles_ingested_at", "ingested_at"),
        Index("ix_rss_articles_source_name", "source_name"),
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
