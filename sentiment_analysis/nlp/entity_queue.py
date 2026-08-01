"""
Module: entity_queue.py
Purpose: Persist entities that could not be mapped to a ticker into a frequency-ranked review queue
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Unresolved entity queue backed by the unresolved_entities PostgreSQL table.

When an entity extracted from an article cannot be mapped to a known ticker
it is inserted here for manual review.  Duplicate entities (same entity_text
while status='pending') increment the frequency counter instead of inserting a
new row, so high-signal entities bubble to the top of the review queue.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentiment_analysis.storage.models import UnresolvedEntity


async def add_or_increment(
    session: AsyncSession,
    entity_text: str,
    entity_type: str,
    article_id: UUID,
) -> None:
    """
    Upsert an unresolved entity into the review queue.

    Called within an existing open session (from pipeline.py) so we don't
    open a nested transaction.  The caller is responsible for committing.

    If a pending row with the same entity_text already exists its frequency
    is incremented.  Otherwise a new row is inserted.
    """
    result = await session.execute(
        select(UnresolvedEntity)
        .where(UnresolvedEntity.entity_text == entity_text)
        .where(UnresolvedEntity.status == "pending")
        .limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.frequency += 1
    else:
        session.add(
            UnresolvedEntity(
                entity_text=entity_text,
                entity_type=entity_type,
                article_id=article_id,
                frequency=1,
                status="pending",
            )
        )
