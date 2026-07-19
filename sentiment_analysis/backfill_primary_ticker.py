"""
One-time backfill: compute primary_ticker for all existing rss_articles.

Run from the project root:
    python -m sentiment_analysis.backfill_primary_ticker

Safe to re-run — skips articles that already have primary_ticker set.
After completion this file can be deleted.
"""
from __future__ import annotations

import sys

from loguru import logger
from sqlalchemy import create_engine, text

from sentiment_analysis.config import settings
from sentiment_analysis.nlp.primary_company_scorer import PrimaryCompanyScorer

BATCH_SIZE = 500


def main() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | {message}"
        ),
    )

    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    engine   = create_engine(sync_url)
    scorer   = PrimaryCompanyScorer()

    with engine.connect() as conn:

        # ── Company name lookup (loaded once) ─────────────────────────────
        company_names: dict[str, str] = {
            row.ticker: row.company_name
            for row in conn.execute(text(
                "SELECT ticker, company_name "
                "FROM ticker_prices "
                "WHERE company_name IS NOT NULL"
            ))
            if row.company_name
        }
        logger.info(f"Loaded {len(company_names)} company names")

        # ── Pre-load IDs of articles that need backfilling ────────────────
        # Filter to articles that have candidate tickers and no primary_ticker
        # yet.  Pre-loading IDs prevents offset drift as rows get updated.
        id_rows = conn.execute(text("""
            SELECT id::text AS id_str
            FROM rss_articles
            WHERE primary_ticker IS NULL
              AND tickers IS NOT NULL
              AND array_length(tickers, 1) > 0
            ORDER BY ingested_at DESC
        """)).fetchall()

        all_ids = [r.id_str for r in id_rows]
        total   = len(all_ids)
        logger.info(f"Articles to process: {total}")

        if total == 0:
            logger.info("Nothing to do — backfill already complete.")
            return

        assigned   = 0
        null_count = 0
        processed  = 0
        errors     = 0

        for batch_start in range(0, total, BATCH_SIZE):
            batch_ids = all_ids[batch_start : batch_start + BATCH_SIZE]

            try:
                # ── Fetch article rows for this batch ─────────────────────
                articles = conn.execute(
                    text("""
                        SELECT id::text AS id_str, title, summary, tickers
                        FROM rss_articles
                        WHERE id::text = ANY(:ids)
                    """),
                    {"ids": batch_ids},
                ).fetchall()

                # ── Fetch resolved entities for this batch ────────────────
                entity_rows = conn.execute(
                    text("""
                        SELECT article_id::text AS aid,
                               entity_text, entity_type, ticker, confidence
                        FROM extracted_entities
                        WHERE article_id::text = ANY(:ids)
                    """),
                    {"ids": batch_ids},
                ).fetchall()

                entities_by_article: dict[str, list[dict]] = {}
                for e in entity_rows:
                    entities_by_article.setdefault(e.aid, []).append({
                        "article_id":  e.aid,
                        "entity_text": e.entity_text,
                        "entity_type": e.entity_type,
                        "ticker":      e.ticker,
                        "confidence":  e.confidence,
                    })

                # ── Score each article ────────────────────────────────────
                updates: list[dict] = []
                for a in articles:
                    result = scorer.score(
                        title          = a.title   or "",
                        summary        = a.summary or "",
                        new_tickers    = list(a.tickers or []),
                        resolved_dicts = entities_by_article.get(a.id_str, []),
                        company_names  = company_names,
                    )
                    updates.append({
                        "id_str":         a.id_str,
                        "primary_ticker": result.primary_ticker,
                    })
                    if result.primary_ticker:
                        assigned += 1
                    else:
                        null_count += 1

                # ── Batch-update primary_ticker ───────────────────────────
                conn.execute(
                    text("""
                        UPDATE rss_articles
                        SET primary_ticker = :primary_ticker
                        WHERE id::text = :id_str
                    """),
                    updates,
                )
                conn.commit()

                processed += len(articles)
                pct = processed / total * 100
                logger.info(
                    f"  {processed:>6}/{total}  ({pct:.1f}%)  "
                    f"assigned={assigned}  null={null_count}"
                )

            except Exception as exc:
                errors += 1
                logger.error(
                    f"Batch {batch_start}–{batch_start + BATCH_SIZE} failed: {exc}"
                )
                conn.rollback()

    logger.info(
        f"Backfill complete — "
        f"assigned={assigned}  null={null_count}  errors={errors}"
    )


if __name__ == "__main__":
    main()
