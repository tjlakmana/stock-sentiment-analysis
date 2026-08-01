"""
Module: env.py
Purpose: Alembic environment — wires the ORM metadata and DATABASE_URL to online and offline migrations
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Alembic migration environment.

Supports both offline (SQL script generation) and online (live DB) modes.
Uses a synchronous psycopg2 engine for migrations so that greenlet C
extensions and libstdc++ are not required on the build host.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Ensure the project root (parent of sentiment_analysis/) is on sys.path
# so that `from sentiment_analysis.config import settings` resolves correctly.
# env.py lives at: sentiment_analysis/storage/alembic/env.py
# Project root is: parents[3]
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sentiment_analysis.config import settings  # noqa: E402
from sentiment_analysis.storage.models import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------
config = context.config

# Build a synchronous URL for migrations regardless of what the app uses.
# asyncpg is an async-only driver and cannot be used with the sync engine.
_sync_url = (
    settings.database_url
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    .replace("postgresql://",         "postgresql+psycopg2://")
)
config.set_main_option("sqlalchemy.url", _sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — emit SQL without connecting
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Generate a SQL script instead of applying migrations live."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — apply migrations to the live database
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    """Apply migrations via a synchronous psycopg2 connection."""
    connectable = create_engine(_sync_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
