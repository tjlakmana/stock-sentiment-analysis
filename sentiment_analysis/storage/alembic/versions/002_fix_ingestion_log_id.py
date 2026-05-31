"""Fix ingestion_log.id auto-increment

Migration 001 created the id column via sa.Sequence() which generates
CREATE SEQUENCE DDL but does not attach DEFAULT nextval(...) to the column,
leaving id as bare INTEGER NOT NULL with no server-side default.

This migration attaches the sequence as the column default and advances it
past any rows that were manually inserted during debugging.

Revision ID: 002
Revises: 001
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the sequence (idempotent — safe to re-run)
    op.execute("CREATE SEQUENCE IF NOT EXISTS ingestion_log_id_seq START 1")

    # Attach it as the column default
    op.execute("""
        ALTER TABLE ingestion_log
        ALTER COLUMN id SET DEFAULT nextval('ingestion_log_id_seq')
    """)

    # Mark the sequence as owned by the column so it's dropped with the table
    op.execute(
        "ALTER SEQUENCE ingestion_log_id_seq OWNED BY ingestion_log.id"
    )

    # Advance the sequence past the current maximum id so the next INSERT
    # doesn't collide with any rows that already exist in the table
    op.execute("""
        SELECT setval(
            'ingestion_log_id_seq',
            COALESCE((SELECT MAX(id) FROM ingestion_log), 0) + 1,
            false
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE ingestion_log ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS ingestion_log_id_seq")
