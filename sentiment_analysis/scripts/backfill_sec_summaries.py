"""
Backfill SEC article summaries and clean titles for existing database records.

For each SEC article whose summary is NULL or shorter than 50 characters:
  - Enriches summary via _build_sec_summary() (fetches EDGAR for Form 4 / 8-K;
    builds metadata string for 10-Q / S-1 / SC 13G)
  - Cleans title via _clean_sec_title() — removes CIK numbers and role suffixes
  - Resets cleaned_text = NULL so the NLP pipeline regenerates it from the
    enriched summary before the sentiment pipeline re-scores
  - Resets sentiment_analyzed_at = NULL so the article is re-queued

Usage:
    venv\\Scripts\\python sentiment_analysis/scripts/backfill_sec_summaries.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# ── Bootstrap: make 'sentiment_analysis' importable from any cwd ─────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "sentiment_analysis" / ".env")

import psycopg2
import psycopg2.extras
from loguru import logger

from sentiment_analysis.ingestion.rss_ingestor import (
    _build_sec_summary,
    _clean_sec_title,
)

# rss_articles.source_name stores the key WITHOUT the 'rss:' prefix
_SEC_SOURCES = ("sec_edgar", "sec_form4", "sec_10q", "sec_s1", "sec_sc13g")

BATCH_SIZE  = 20
BATCH_DELAY = 1.0  # seconds between commits to respect SEC rate limits

# Raw EDGAR titles contain a CIK like (0000789019); cleaned titles do not
_HAS_CIK = re.compile(r"\(\d{7,10}\)")


def _get_connection() -> psycopg2.extensions.connection:
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set — check sentiment_analysis/.env")
        sys.exit(1)
    # Strip asyncpg driver specifier so psycopg2 can parse the URL
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        return psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        logger.error(f"Cannot connect to database: {exc}")
        sys.exit(1)


def main() -> None:
    conn = _get_connection()
    conn.autocommit = False

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM rss_articles
            WHERE source_name IN %s
              AND (summary IS NULL OR LENGTH(summary) < 50)
            """,
            (tuple(_SEC_SOURCES),),
        )
        total: int = cur.fetchone()["total"]

    if total == 0:
        logger.info("No SEC articles need backfilling — all summaries are already enriched.")
        conn.close()
        return

    logger.info(f"Found {total} SEC articles to backfill.")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, source_name, url, title, summary
            FROM rss_articles
            WHERE source_name IN %s
              AND (summary IS NULL OR LENGTH(summary) < 50)
            ORDER BY ingested_at ASC
            """,
            (tuple(_SEC_SOURCES),),
        )
        rows = cur.fetchall()

    processed = 0
    errors    = 0

    for row in rows:
        source_name: str = row["source_name"]

        # _build_sec_summary expects a dict that supports .get() like feedparser entries
        entry = {
            "link":    row["url"]     or "",
            "title":   row["title"]   or "",
            "summary": row["summary"] or "",
        }

        new_summary: str | None = None
        try:
            new_summary = _build_sec_summary(source_name, entry)
        except Exception as exc:
            processed += 1
            errors    += 1
            logger.warning(
                f"[{processed}/{total}] Enrichment failed for {row['id']} "
                f"({source_name}): {exc}"
            )

        # Only reformat the title when it still contains a raw CIK number.
        # A title without a CIK has already been cleaned — leave it alone to
        # avoid double-formatting (e.g. "Form 4 — Form 4 — Company").
        raw_title: str = row["title"] or ""
        new_title = (
            _clean_sec_title(raw_title, source_name)
            if _HAS_CIK.search(raw_title)
            else raw_title
        )

        with conn.cursor() as upd:
            upd.execute(
                """
                UPDATE rss_articles
                SET summary               = COALESCE(%s, summary),
                    title                 = %s,
                    cleaned_text          = NULL,
                    sentiment_analyzed_at = NULL
                WHERE id = %s
                """,
                (new_summary, new_title, row["id"]),
            )

        processed += 1

        if processed % BATCH_SIZE == 0:
            conn.commit()
            logger.info(f"Backfilling article {processed}/{total}...")
            if processed < total:
                time.sleep(BATCH_DELAY)

    # Commit any remainder that didn't fill a full batch
    conn.commit()

    logger.info(
        f"Backfill complete — {processed}/{total} articles processed "
        f"({errors} enrichment errors, {processed - errors} updated successfully)."
    )
    conn.close()


if __name__ == "__main__":
    main()
