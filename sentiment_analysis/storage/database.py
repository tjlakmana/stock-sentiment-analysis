"""
Module: database.py
Purpose: Database engine and session factories for both async pipeline and sync dashboard access
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Two engines are maintained:
  - async_engine  (asyncpg)   — used by the ingestion pipeline (APScheduler jobs)
  - sync_engine   (psycopg2)  — used by the Dash/Streamlit dashboard (read-only queries)

Both are configured from the same DATABASE_URL environment variable.
The sync URL is auto-derived by stripping the '+asyncpg' driver specifier.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from sentiment_analysis.config import settings

# ---------------------------------------------------------------------------
# Async engine — ingestion pipeline
# ---------------------------------------------------------------------------

# pool_size=5: five persistent connections cover the pipeline's concurrent jobs
# (RSS, NLP, sentiment, prices, alerts) without exhausting Railway's connection limit.
# pool_pre_ping=True: issues a SELECT 1 before each checkout — critical on Railway
# where idle connections are silently dropped after ~15 minutes.
async_engine = create_async_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # verify connections before use (handles DB restarts)
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a database session.

    Commits on clean exit; rolls back and re-raises on any exception.

    Usage::

        async with get_async_session() as session:
            session.add(some_orm_object)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Sync engine — Streamlit dashboard (read-only queries via pandas)
# ---------------------------------------------------------------------------

sync_engine = create_engine(
    settings.sync_database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

SyncSessionLocal = sessionmaker(bind=sync_engine, autoflush=False)


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """
    Synchronous context manager that yields a database session.

    Primarily intended for dashboard read queries; commits are safe but
    the dashboard only issues SELECTs.
    """
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
