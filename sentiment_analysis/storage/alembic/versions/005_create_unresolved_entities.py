"""Create unresolved_entities table

Review queue for named entities that could not be automatically mapped to a
ticker symbol.  Duplicate entity texts (same entity_text + status='pending')
increment frequency rather than inserting a new row.

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 005
Revises: 004
Create Date: 2026-06-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unresolved_entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_text", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column(
            "article_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rss_articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("frequency", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_ticker", sa.String(20), nullable=True),
    )
    op.create_index(
        "ix_unresolved_entities_status", "unresolved_entities", ["status"]
    )
    op.create_index(
        "ix_unresolved_entities_entity_text",
        "unresolved_entities",
        ["entity_text"],
    )


def downgrade() -> None:
    op.drop_index("ix_unresolved_entities_entity_text", "unresolved_entities")
    op.drop_index("ix_unresolved_entities_status", "unresolved_entities")
    op.drop_table("unresolved_entities")
