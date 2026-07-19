"""
NLP preprocessing pipeline orchestrator.

Processes unprocessed rss_articles (cleaned_text IS NULL) in batches of 50:
  1. TextPreprocessor  — cleans and lemmatises title+summary → cleaned_text
  2. EntityExtractor   — three-pass ticker extraction + spaCy NER
  3. DB write          — update cleaned_text, merge tickers, persist entities
  4. entity_queue      — enqueue entities that could not be mapped to a ticker

Runs silently in the background; all errors are caught per-article so one bad
article never aborts the entire batch.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from sqlalchemy import select

from sentiment_analysis.entity_resolution.pipeline import EntityResolutionPipeline
from sentiment_analysis.nlp.entity_queue import add_or_increment
from sentiment_analysis.nlp.primary_company_scorer import PrimaryCompanyScorer
from sentiment_analysis.nlp.text_preprocessor import TextPreprocessor
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import ExtractedEntity, RSSArticle, TickerPrice

# Module-level singletons — loaded once, reused across scheduler invocations
_preprocessor: Optional[TextPreprocessor] = None
_extractor: Optional[EntityResolutionPipeline] = None
_scorer: Optional[PrimaryCompanyScorer] = None


def _get_preprocessor() -> TextPreprocessor:
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = TextPreprocessor()
    return _preprocessor


def _get_extractor() -> EntityResolutionPipeline:
    global _extractor
    if _extractor is None:
        _extractor = EntityResolutionPipeline()
    return _extractor


def _get_scorer() -> PrimaryCompanyScorer:
    global _scorer
    if _scorer is None:
        _scorer = PrimaryCompanyScorer()
    return _scorer


def _log_scoring(title: str, scores: dict[str, int], primary_ticker, winner_score: int, reason: str) -> None:
    lines = [
        "----------------------------------------",
        f"Title:\n{title}",
        "",
        "Candidates",
    ]
    for t, s in sorted(scores.items(), key=lambda x: -x[1]):
        lines.append(f"  {t} = {s}")
    lines.append("")
    if primary_ticker:
        lines.append(f"Winner\n  {primary_ticker}")
        lines.append(f"\nConfidence\n  {winner_score}")
    else:
        lines.append("Winner\n  NULL")
        lines.append(f"\nReason\n  {reason}")
    lines.append("----------------------------------------")
    logger.debug("[primary_scorer]\n" + "\n".join(lines))


async def run_nlp_pipeline(batch_size: int = 50) -> None:
    """
    Entry point called by the scheduler every 10 minutes.

    Fetches up to ``batch_size`` articles where cleaned_text IS NULL,
    processes each, and writes results back to the database.
    """
    # ── Fetch unprocessed articles + company name lookup ───────────────────
    async with get_async_session() as session:
        result = await session.execute(
            select(RSSArticle)
            .where(RSSArticle.cleaned_text == None)  # noqa: E711
            .order_by(RSSArticle.ingested_at.desc())
            .limit(batch_size)
        )
        articles = result.scalars().all()

        # Load ticker → company_name for the primary company scorer
        price_result = await session.execute(
            select(TickerPrice.ticker, TickerPrice.company_name)
            .where(TickerPrice.company_name != None)  # noqa: E711
        )
        company_names: dict[str, str] = {
            row.ticker: row.company_name
            for row in price_result
            if row.company_name
        }

    if not articles:
        logger.debug("[nlp] No unprocessed articles.")
        return

    preprocessor = _get_preprocessor()
    extractor    = _get_extractor()
    scorer       = _get_scorer()
    processed    = 0
    errors       = 0

    for article in articles:
        try:
            full_text = f"{article.title or ''} {article.summary or ''}".strip()

            # ── Step 1: Text preprocessing (CPU-bound → thread) ───────────
            cleaned = await asyncio.to_thread(preprocessor.preprocess, full_text)

            # ── Step 2: Entity extraction (CPU-bound → thread) ────────────
            resolved_dicts, new_tickers, unresolved_dicts = await asyncio.to_thread(
                extractor.extract,
                article.title or "",
                article.summary or "",
                article.id,
            )

            # ── Step 3: Primary company scoring (CPU-bound → thread) ──────
            scoring_result = await asyncio.to_thread(
                scorer.score,
                article.title or "",
                article.summary or "",
                new_tickers,
                resolved_dicts,
                company_names,
            )
            _log_scoring(
                article.title or "",
                scoring_result.all_scores,
                scoring_result.primary_ticker,
                scoring_result.winner_score,
                scoring_result.reason,
            )

            # ── Step 4: Write results ──────────────────────────────────────
            async with get_async_session() as session:
                # Re-fetch article within this session to get a managed object
                db_article = await session.get(RSSArticle, article.id)
                if db_article is None:
                    continue

                db_article.cleaned_text  = cleaned or ""
                db_article.primary_ticker = scoring_result.primary_ticker

                # Merge newly found tickers into the existing tickers array
                existing_tickers = set(db_article.tickers or [])
                if new_tickers:
                    existing_tickers.update(new_tickers)
                    db_article.tickers = list(existing_tickers)

                # Persist resolved entities
                for d in resolved_dicts:
                    session.add(ExtractedEntity(
                        article_id=d["article_id"],
                        entity_text=d["entity_text"],
                        entity_type=d["entity_type"],
                        ticker=d["ticker"],
                        confidence=d["confidence"],
                    ))

                # Enqueue unresolved entities
                for d in unresolved_dicts:
                    await add_or_increment(
                        session,
                        d["entity_text"],
                        d["entity_type"],
                        d["article_id"],
                    )

            processed += 1

        except Exception as exc:
            errors += 1
            logger.exception(
                f"[nlp] Error processing article {article.id}: {exc}"
            )

    logger.info(
        f"[nlp] Batch complete — processed {processed}/{len(articles)} articles"
        + (f", {errors} errors" if errors else "")
    )
