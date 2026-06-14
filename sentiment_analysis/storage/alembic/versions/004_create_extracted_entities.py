"""Create extracted_entities table

Stores the named entities extracted by spaCy NER that were successfully
resolved to a ticker symbol.  One row per entity per article.

Revision ID: 004
Revises: 003
Create Date: 2026-06-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extracted_entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "article_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rss_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_text", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_extracted_entities_article_id", "extracted_entities", ["article_id"]
    )
    op.create_index(
        "ix_extracted_entities_ticker", "extracted_entities", ["ticker"]
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_entities_ticker", "extracted_entities")
    op.drop_index("ix_extracted_entities_article_id", "extracted_entities")
    op.drop_table("extracted_entities")
