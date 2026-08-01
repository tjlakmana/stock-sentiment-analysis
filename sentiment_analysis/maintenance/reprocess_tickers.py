"""
Module: reprocess_tickers.py
Purpose: Reprocess tickers[] and primary_ticker for all articles after ticker-extraction quality fixes
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Reprocess tickers[] and primary_ticker for existing articles after the
ticker-extraction quality fixes applied to ticker_list.py:

  Fix 1 — single-char tokens now blocked in Pass 2 (len >= 2 guard)
  Fix 2 — phantom company-name mappings removed ("gap" → GAP, "unity" → U)
  Fix 3 — SEC filing codes stripped before scanning ("8-K" no longer produces K)

For each article this script:

  1. Runs the FIXED extract_tickers() on title + summary.
  2. Identifies tickers currently in tickers[] that the fixed extractor no
     longer produces from that text — these are false positives to remove.
     NOTE: tickers added by the NLP entity_extractor (via TickerMapper) are
     preserved if they are not in the false-positive categories.
  3. Updates tickers[] in the DB.
  4. If primary_ticker was one of the removed tickers:
     - Sets primary_ticker = NULL.
     - Resets cleaned_text = NULL so the NLP scheduler re-runs the primary
       company scorer on the next cycle (scheduler checks cleaned_text IS NULL).
  5. Prints a before/after report.

Safe to run multiple times (idempotent — re-running after the first pass will
find no changes because the fixed extractor and the stored array already agree).

Usage:
    cd "c:/Users/talak/Downloads/Stock Sentiment"
    python -m sentiment_analysis.maintenance.reprocess_tickers
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from collections import defaultdict
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path("sentiment_analysis/.env"))

import psycopg2
import psycopg2.extras

# Import the FIXED extractor — this module is already patched, so running it
# here produces the corrected ticker set.
from sentiment_analysis.nlp.ticker_list import extract_tickers

# ── Constants ────────────────────────────────────────────────────────────────

DB_URL = os.environ["DATABASE_URL"].replace("+asyncpg", "")

# Tickers we know are always false positives regardless of text content.
# These came from phantom entries in _COMPANY_TICKER_MAP that have been removed.
_ALWAYS_FALSE_POSITIVES: frozenset[str] = frozenset({"GAP", "U"})

# The single-char tickers that exist in SP500_TICKER_SET.
# These are only valid when produced by the fixed extractor (i.e. via cashtag
# or a genuine company-name match like "kellogg" → K).  Any appearance of
# these in tickers[] that the fixed extractor does NOT reproduce from the
# article's own title+summary is a false positive we should remove.
_SINGLE_CHAR_TICKERS: frozenset[str] = frozenset(
    {"A", "C", "D", "F", "J", "K", "L", "O", "T", "V"}
)

# Batch size for DB reads
_BATCH = 500


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(DB_URL)


def _snapshot_before(cur: psycopg2.extensions.cursor) -> dict:
    """Capture key counts before any changes."""
    stats: dict = {}

    cur.execute("SELECT COUNT(*) FROM rss_articles")
    stats["total"] = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM rss_articles WHERE 'GAP' = ANY(tickers)"
    )
    stats["has_GAP"] = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM rss_articles WHERE 'U' = ANY(tickers)"
    )
    stats["has_U"] = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM rss_articles
        WHERE EXISTS (SELECT 1 FROM unnest(tickers) t WHERE length(t) = 1)
    """)
    stats["has_single_char"] = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM rss_articles WHERE 'K' = ANY(tickers)
    """)
    stats["has_K"] = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM rss_articles WHERE primary_ticker IS NOT NULL
    """)
    stats["has_primary"] = cur.fetchone()[0]

    return stats


def _snapshot_after(cur: psycopg2.extensions.cursor) -> dict:
    return _snapshot_before(cur)


def reprocess(dry_run: bool = False) -> None:
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    print("=" * 72)
    print("TICKER REPROCESSING — before/after validation")
    print("=" * 72)
    if dry_run:
        print("DRY RUN — no changes will be written\n")

    before = _snapshot_before(cur)
    print(
        f"Before  total={before['total']}  GAP={before['has_GAP']}  U={before['has_U']}  "
        f"single_char={before['has_single_char']}  K={before['has_K']}  "
        f"has_primary={before['has_primary']}"
    )

    # ── Collect all articles (batched) ────────────────────────────────────────
    cur.execute("""
        SELECT id, title, summary, tickers, primary_ticker, cleaned_text
        FROM rss_articles
        ORDER BY id
    """)

    rows = cur.fetchall()
    print(f"Loaded {len(rows)} articles\n")

    articles_updated = 0
    tickers_removed: dict[str, int] = defaultdict(int)  # ticker → count removed
    primary_nulled = 0
    nlp_reset = 0

    update_stmts: list[tuple] = []  # (tickers, primary_ticker, cleaned_text, id)

    for row in rows:
        article_id: int = row["id"]
        title: str = row["title"] or ""
        summary: str = row["summary"] or ""
        old_tickers: list[str] = list(row["tickers"] or [])
        old_primary: Optional[str] = row["primary_ticker"]
        has_cleaned: bool = row["cleaned_text"] is not None

        # ── Determine which tickers the fixed extractor produces ──────────────
        fixed_from_text = set(extract_tickers(f"{title} {summary}"))

        # ── Identify false positives ──────────────────────────────────────────
        to_remove: set[str] = set()

        for t in old_tickers:
            # Always-false phantom tickers
            if t in _ALWAYS_FALSE_POSITIVES:
                to_remove.add(t)
                continue
            # Single-char tickers not reproduced by fixed extractor
            if t in _SINGLE_CHAR_TICKERS and t not in fixed_from_text:
                to_remove.add(t)

        if not to_remove:
            continue  # nothing to do for this article

        # ── Build updated tickers array ───────────────────────────────────────
        new_tickers = [t for t in old_tickers if t not in to_remove]

        # ── Determine primary_ticker fate ─────────────────────────────────────
        new_primary = old_primary
        reset_cleaned = False

        if old_primary in to_remove:
            new_primary = None
            primary_nulled += 1
            # Reset cleaned_text so the NLP scheduler re-runs the primary
            # company scorer on the next cycle.
            if has_cleaned:
                reset_cleaned = True
                nlp_reset += 1

        # ── Accumulate stats ──────────────────────────────────────────────────
        articles_updated += 1
        for t in to_remove:
            tickers_removed[t] += 1

        new_cleaned = None if reset_cleaned else row["cleaned_text"]
        update_stmts.append((new_tickers, new_primary, new_cleaned, article_id))

    # ── Write updates ─────────────────────────────────────────────────────────
    if not dry_run and update_stmts:
        update_cur = conn.cursor()
        psycopg2.extras.execute_batch(
            update_cur,
            """
            UPDATE rss_articles
            SET    tickers       = %s,
                   primary_ticker = %s,
                   cleaned_text  = %s
            WHERE  id = %s
            """,
            update_stmts,
            page_size=200,
        )
        conn.commit()
        update_cur.close()

    # ── After snapshot ────────────────────────────────────────────────────────
    after = _snapshot_after(cur)

    # ── Report ────────────────────────────────────────────────────────────────
    print("=" * 72)
    print("CHANGES SUMMARY")
    print("=" * 72)
    print(f"Articles with tickers[] modified : {articles_updated}")
    print(f"  (dry_run — DB unchanged)        " if dry_run else "  (committed to DB)")
    print()
    print("Tickers removed (by count):")
    for ticker, cnt in sorted(tickers_removed.items(), key=lambda x: -x[1]):
        print(f"  {ticker:<8} removed from {cnt} articles")
    print()
    print(f"primary_ticker set to NULL       : {primary_nulled}")
    print(f"cleaned_text reset (NLP re-queue): {nlp_reset}")
    print()

    print("=" * 72)
    print("BEFORE / AFTER COUNTS")
    print("=" * 72)
    rows_fmt = [
        ("Total articles",          before["total"],          after["total"]),
        ("Articles with GAP",        before["has_GAP"],        after["has_GAP"]),
        ("Articles with U",          before["has_U"],          after["has_U"]),
        ("Articles with any 1-char", before["has_single_char"],after["has_single_char"]),
        ("Articles with K",          before["has_K"],          after["has_K"]),
        ("Articles with primary_ticker", before["has_primary"], after["has_primary"]),
    ]
    header = f"{'Metric':<35} {'Before':>8} {'After':>8} {'Delta':>8}"
    print(header)
    print("-" * len(header))
    for label, b, a in rows_fmt:
        delta = a - b
        sign = "+" if delta > 0 else ""
        print(f"  {label:<33} {b:>8} {a:>8} {sign}{delta:>7}")

    if nlp_reset > 0:
        print()
        print(
            f"NOTE: {nlp_reset} articles had primary_ticker nulled and cleaned_text reset.\n"
            f"      The NLP scheduler will re-score primary_ticker on its next cycle\n"
            f"      (runs every 10 min; triggers automatically while the app is running)."
        )

    cur.close()
    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    reprocess(dry_run=dry)
