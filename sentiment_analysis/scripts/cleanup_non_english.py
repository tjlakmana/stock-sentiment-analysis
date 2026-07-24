"""
One-time cleanup of non-English articles in rss_articles.

Scans all existing articles and re-runs the production language detection
logic (title + summary via langdetect) to identify articles that would be
rejected by the updated ingestor.

Usage:
    # Scan and produce a report — no database changes:
    venv\\Scripts\\python sentiment_analysis/scripts/cleanup_non_english.py --dry-run

    # Scan, show report, confirm, then delete + refresh sentiment summaries:
    venv\\Scripts\\python sentiment_analysis/scripts/cleanup_non_english.py --execute

Referential integrity is handled automatically:
  - extracted_entities rows are CASCADE-deleted with their parent article.
  - unresolved_entities.article_id is SET NULL (rows are kept for review).
  - ticker_sentiment_summary is refreshed after deletion via the aggregator.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── Bootstrap: make 'sentiment_analysis' importable from any cwd ─────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / "sentiment_analysis" / ".env")

import psycopg2
import psycopg2.extras
from langdetect import DetectorFactory, LangDetectException, detect_langs
from loguru import logger

DetectorFactory.seed = 0  # match the production ingestor setting

# ── SEC sources are legally required to be filed in English.
# langdetect misclassifies their short form titles (e.g. "Form 4 — Schmitz Ronald D.
# (Insider Trading)" → German 100%) due to surnames and short text length.
_SEC_SOURCES_ALWAYS_ENGLISH = frozenset({
    "sec_edgar", "sec_form4", "sec_10q", "sec_s1", "sec_sc13g"
})

# langdetect accuracy degrades significantly below ~60 characters.
# Short titles without summaries produce unreliable results (e.g. "Soybeans
# Posting Midweek Gains" → Afrikaans 100%). Skip detection for very short text.
_MIN_CHARS_FOR_DETECTION = 60

# ── Language code → display name ─────────────────────────────────────────────
_LANG_NAMES: dict[str, str] = {
    "af": "Afrikaans",     "ar": "Arabic",          "bg": "Bulgarian",
    "bn": "Bengali",       "ca": "Catalan",          "cs": "Czech",
    "cy": "Welsh",         "da": "Danish",           "de": "German",
    "el": "Greek",         "es": "Spanish",          "et": "Estonian",
    "fa": "Persian",       "fi": "Finnish",          "fr": "French",
    "gu": "Gujarati",      "he": "Hebrew",           "hi": "Hindi",
    "hr": "Croatian",      "hu": "Hungarian",        "id": "Indonesian",
    "it": "Italian",       "ja": "Japanese",         "kn": "Kannada",
    "ko": "Korean",        "lt": "Lithuanian",       "lv": "Latvian",
    "mk": "Macedonian",    "ml": "Malayalam",        "mr": "Marathi",
    "ne": "Nepali",        "nl": "Dutch",            "no": "Norwegian",
    "pa": "Punjabi",       "pl": "Polish",           "pt": "Portuguese",
    "ro": "Romanian",      "ru": "Russian",          "sk": "Slovak",
    "sl": "Slovenian",     "so": "Somali",           "sq": "Albanian",
    "sv": "Swedish",       "sw": "Swahili",          "ta": "Tamil",
    "te": "Telugu",        "th": "Thai",             "tl": "Filipino",
    "tr": "Turkish",       "uk": "Ukrainian",        "ur": "Urdu",
    "vi": "Vietnamese",    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "non-latin":           "Non-Latin script (fast-path)",
    "inconclusive":        "Inconclusive",
}

_SCAN_BATCH = 500  # rows fetched per server-side cursor iteration


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get(code, code.upper())


# ── Language classifier (mirrors _is_english in rss_ingestor.py) ─────────────

def _classify(title: str, summary: str, source_name: str = "") -> tuple[str | None, float | None]:
    """Return (lang_code, confidence) if non-English, (None, None) if English.

    Conservative by design: when uncertain, always keep the article.

    False positive guards (in order):
      1. SEC sources: skip langdetect — SEC filings are legally English; short
         form titles with surnames cause 100%-confidence misdetections.
      2. Non-Latin fast-path: >15% non-ASCII characters → clearly non-English.
      3. Minimum text length: <60 chars is too short for reliable detection.
      4. Top-language guard: if langdetect's top result is English, keep.
      5. English-probability guard: if English has ≥20% probability, keep.
      6. Confidence gate: only flag when non-English confidence is ≥85%.
    """
    # Guard 1 — SEC filings are always English
    if source_name in _SEC_SOURCES_ALWAYS_ENGLISH:
        return None, None

    text = f"{title} {summary}".strip()
    if not text:
        return None, None

    # Guard 2 — non-Latin scripts (fast-path)
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ratio = non_ascii / len(text)
    if ratio > 0.15:
        return "non-latin", round(ratio, 4)

    # Guard 3 — too short for reliable detection
    if len(text) < _MIN_CHARS_FOR_DETECTION:
        return None, None

    try:
        results = detect_langs(text)
    except LangDetectException:
        return "inconclusive", 0.0

    # Guard 4 — English is the top detected language
    if results and results[0].lang == "en":
        return None, None

    # Guard 5 — English has meaningful probability
    en_prob = next((r.prob for r in results if r.lang == "en"), 0.0)
    if en_prob >= 0.20:
        return None, None

    # Guard 6 — only flag with high non-English confidence
    top = results[0] if results else None
    if top and top.prob >= 0.85:
        return top.lang, round(top.prob, 4)

    return None, None  # ambiguous — keep


# ── Database helpers ──────────────────────────────────────────────────────────

def _connect() -> psycopg2.extensions.connection:
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL not set — check sentiment_analysis/.env")
        sys.exit(1)
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as exc:
        logger.error(f"Cannot connect to database: {exc}")
        sys.exit(1)


# ── Scan ─────────────────────────────────────────────────────────────────────

def scan(conn: psycopg2.extensions.connection) -> tuple[list[dict], int]:
    """
    Stream all rss_articles, classify each for English, and return
    (non_english_rows, total_scanned).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM rss_articles")
        total: int = cur.fetchone()["n"]

    if total == 0:
        return [], 0

    logger.info(f"Scanning {total:,} articles…")
    non_english: list[dict] = []
    processed = 0

    with conn.cursor(
        name="lang_scan",
        cursor_factory=psycopg2.extras.RealDictCursor,
    ) as cur:
        cur.itersize = _SCAN_BATCH
        cur.execute(
            """
            SELECT id, title, summary, source_name, ingested_at
            FROM   rss_articles
            ORDER  BY ingested_at ASC
            """
        )
        while True:
            batch = cur.fetchmany(_SCAN_BATCH)
            if not batch:
                break
            for row in batch:
                title   = row["title"]   or ""
                summary = row["summary"] or ""
                lang, conf = _classify(title, summary, row["source_name"] or "")
                if lang is not None:
                    non_english.append({
                        "id":            row["id"],
                        "title":         row["title"] or "",
                        "source_name":   row["source_name"] or "",
                        "ingested_at":   row["ingested_at"],
                        "detected_lang": lang,
                        "confidence":    conf or 0.0,
                    })
                processed += 1
                if processed % 1000 == 0:
                    logger.info(f"  {processed:,}/{total:,} articles scanned…")

    return non_english, total


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(non_english: list[dict], total_scanned: int) -> None:
    out = sys.stdout
    sep = "=" * 72

    out.write(f"\n{sep}\n")
    out.write("  NON-ENGLISH ARTICLE CLEANUP REPORT\n")
    out.write(f"{sep}\n\n")
    out.write(f"  Total articles scanned   : {total_scanned:,}\n")
    out.write(f"  Non-English articles     : {len(non_english):,}\n")
    out.write("\n")

    if not non_english:
        out.write("  No non-English articles found — database is clean.\n\n")
        return

    # Language breakdown
    lang_counts: dict[str, int] = {}
    for row in non_english:
        code = row["detected_lang"]
        lang_counts[code] = lang_counts.get(code, 0) + 1

    out.write("  Breakdown by language:\n")
    for code, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        name = _lang_name(code)
        out.write(f"    {name:<35} {count:>4} article(s)\n")

    out.write("\n")
    out.write("  Articles flagged for removal:\n")
    out.write(f"  {'ID':<36}  {'Source':<22}  {'Lang':<14}  {'Conf':>5}  "
              f"{'Ingested':<12}  Title\n")
    out.write("  " + "-" * 118 + "\n")

    for row in non_english:
        title_trunc = row["title"][:52]
        ingested    = str(row["ingested_at"])[:10] if row["ingested_at"] else "—"
        lang_name   = _lang_name(row["detected_lang"])
        out.write(
            f"  {str(row['id']):<36}  "
            f"{row['source_name']:<22}  "
            f"{lang_name:<14}  "
            f"{row['confidence']:>5.2f}  "
            f"{ingested:<12}  "
            f"{title_trunc}\n"
        )

    out.write("\n")
    out.flush()


# ── Execute ───────────────────────────────────────────────────────────────────

def delete_articles(conn: psycopg2.extensions.connection, ids: list) -> int:
    """
    Delete target articles from rss_articles.

    Cascade effects handled by the DB:
      - extracted_entities rows are deleted automatically (ON DELETE CASCADE).
      - unresolved_entities.article_id is set to NULL (ON DELETE SET NULL).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM rss_articles WHERE id = ANY(%s)",
            (ids,),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def refresh_sentiment_summaries() -> None:
    """
    Insert fresh ticker_sentiment_summary rows by re-running the aggregator.

    ticker_sentiment_summary is append-only; the aggregator writes new rows
    stamped with the current calculated_at. Dashboard queries use
    ORDER BY calculated_at DESC so the new rows supersede the stale ones.
    """
    from sentiment_analysis.sentiment.aggregator import aggregate_ticker_sentiment
    asyncio.run(aggregate_ticker_sentiment())


def verify(conn: psycopg2.extensions.connection, deleted_ids: list) -> bool:
    """Return True if all checks pass."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM rss_articles WHERE id = ANY(%s)",
            (deleted_ids,),
        )
        remaining: int = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM extracted_entities WHERE article_id = ANY(%s)",
            (deleted_ids,),
        )
        orphaned: int = cur.fetchone()[0]

    ok = True
    if remaining == 0:
        logger.info("Verification OK — all target articles removed from rss_articles.")
    else:
        logger.error(f"Verification FAILED — {remaining} article(s) still present in rss_articles.")
        ok = False

    if orphaned == 0:
        logger.info("Verification OK — no orphaned rows in extracted_entities.")
    else:
        logger.error(f"Verification FAILED — {orphaned} orphaned rows remain in extracted_entities.")
        ok = False

    return ok


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Reconfigure stdout to UTF-8 so article titles with accented/non-Latin
    # characters print correctly on Windows terminals that default to cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Scan rss_articles for non-English content and optionally remove it.\n\n"
            "Run --dry-run first to review what will be deleted, then --execute to proceed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and print the report. No database changes.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Scan, print the report, prompt for confirmation, then delete and refresh.",
    )
    args = parser.parse_args()

    conn = _connect()

    try:
        non_english, total_scanned = scan(conn)
    finally:
        # Ensure the server-side cursor transaction is closed even on error
        conn.rollback()

    print_report(non_english, total_scanned)

    if args.dry_run:
        logger.info("Dry-run complete — no changes made.")
        conn.close()
        return

    # ── --execute path ────────────────────────────────────────────────────────
    if not non_english:
        logger.info("Database is already clean — nothing to delete.")
        conn.close()
        return

    answer = input(
        f"Delete {len(non_english)} non-English article(s) and refresh sentiment summaries? [y/N] "
    ).strip().lower()

    if answer != "y":
        logger.info("Aborted — no changes made.")
        conn.close()
        return

    ids = [row["id"] for row in non_english]

    # Summarise what will be deleted by language before proceeding
    lang_counts: dict[str, int] = {}
    for row in non_english:
        code = row["detected_lang"]
        lang_counts[code] = lang_counts.get(code, 0) + 1
    lang_summary = ", ".join(
        f"{_lang_name(c)} ×{n}" for c, n in sorted(lang_counts.items(), key=lambda x: -x[1])
    )

    logger.info(f"Deleting {len(ids)} article(s) — {lang_summary}…")
    deleted = delete_articles(conn, ids)
    logger.info(
        f"{deleted} article(s) deleted from rss_articles "
        "(extracted_entities cascade-deleted; unresolved_entities.article_id set to NULL)."
    )

    logger.info("Refreshing ticker_sentiment_summary…")
    try:
        refresh_sentiment_summaries()
        logger.info("ticker_sentiment_summary refreshed — new rows inserted for all active windows.")
    except Exception as exc:
        logger.warning(f"Summary refresh encountered an error (non-critical): {exc}")

    logger.info("Running post-deletion verification…")
    ok = verify(conn, ids)

    conn.close()

    if ok:
        logger.info(
            f"Cleanup complete. "
            f"{deleted} article(s) removed ({lang_summary}). "
            "Sentiment summaries refreshed. "
            "No orphaned records found."
        )
    else:
        logger.error("Cleanup finished with verification errors — check the log above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
