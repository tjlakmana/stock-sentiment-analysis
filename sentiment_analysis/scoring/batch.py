"""
Batch importance scorer — back-fills importance_score / importance_label
for rss_articles rows where importance_score IS NULL.

Run after applying migration 011:
    python -m sentiment_analysis.scoring.batch
    python -m sentiment_analysis.scoring.batch --batch-size 500 --all
"""
from __future__ import annotations

import argparse
import asyncio

from loguru import logger
from sqlalchemy import select, update

from sentiment_analysis.scoring.scorer import ImportanceScorer
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import RSSArticle


async def run_batch(batch_size: int = 200, rescore_all: bool = False) -> None:
    """
    Score articles that have no importance_score yet (or all, if rescore_all).

    Parameters
    ----------
    batch_size   : articles to process per database round-trip
    rescore_all  : if True, rescore even articles that already have a score
                   (useful after tuning signal weights)
    """
    scorer    = ImportanceScorer()
    total_scored = 0

    while True:
        async with get_async_session() as session:
            q = select(RSSArticle).order_by(RSSArticle.ingested_at.desc())
            if not rescore_all:
                q = q.where(RSSArticle.importance_score == None)  # noqa: E711
            q = q.limit(batch_size)

            result   = await session.execute(q)
            articles = result.scalars().all()

        if not articles:
            break

        updates: list[dict] = []
        for article in articles:
            imp = scorer.score(
                title=article.title or "",
                source_name=article.source_name or "",
                summary=article.summary or "",
            )
            updates.append({
                "id":               article.id,
                "importance_score": imp.score,
                "importance_label": imp.label,
            })

        async with get_async_session() as session:
            for row in updates:
                await session.execute(
                    update(RSSArticle)
                    .where(RSSArticle.id == row["id"])
                    .values(
                        importance_score=row["importance_score"],
                        importance_label=row["importance_label"],
                    )
                )

        total_scored += len(articles)
        logger.info(
            f"[scoring.batch] scored {len(articles)} articles "
            f"(total: {total_scored})"
        )

        if len(articles) < batch_size:
            break

    logger.info(f"[scoring.batch] done — {total_scored} articles scored")


def main() -> None:
    parser = argparse.ArgumentParser(description="Back-fill importance scores")
    parser.add_argument(
        "--batch-size", type=int, default=200,
        help="Articles per DB round-trip (default: 200)",
    )
    parser.add_argument(
        "--all", dest="rescore_all", action="store_true",
        help="Rescore ALL articles, not just unscored ones",
    )
    args = parser.parse_args()
    asyncio.run(run_batch(batch_size=args.batch_size, rescore_all=args.rescore_all))


if __name__ == "__main__":
    main()
