"""Add cleaned_text column to rss_articles

Stores the NLP-preprocessed (tokenised, lemmatised, stopword-filtered)
version of each article's title+summary.  NULL means the article has not
yet been processed by the Phase 3 NLP pipeline.

Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Revision ID: 003
Revises: 002
Create Date: 2026-06-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rss_articles",
        sa.Column("cleaned_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rss_articles", "cleaned_text")
