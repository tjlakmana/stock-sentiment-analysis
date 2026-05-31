"""
RSS feed ingestor using feedparser.

Polls all feeds defined in config.settings.rss_feeds every N minutes.
Each feed is processed independently — a failure on one feed has no effect
on the others.  One IngestionLog row is written per feed per poll cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Set

import feedparser
from loguru import logger
from sqlalchemy import select

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.ticker_list import extract_tickers
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import IngestionLog, RSSArticle


def _parse_published(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """
    Return a timezone-aware datetime from a feedparser entry's date fields.

    feedparser parses dates into `published_parsed` / `updated_parsed` as
    `time.struct_time` 9-tuples.  We prefer `published_parsed` but fall back
    to `updated_parsed` when the former is absent.
    """
    for field in ("published_parsed", "updated_parsed"):
        val = entry.get(field)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


class RSSIngestor:
    """
    Polls all configured RSS feeds and persists new articles to the database.

    A single instance should be reused across scheduler runs to preserve
    the in-memory URL dedup set across poll cycles.
    """

    def __init__(self) -> None:
        # In-memory dedup: article URLs seen since process start
        self._seen_urls: Set[str] = set()

    async def run(self) -> None:
        """Poll all feeds.  Feed failures are caught and logged individually."""
        for source_name, feed_url in settings.rss_feeds.items():
            await self._process_feed(source_name, feed_url)

    async def _process_feed(self, source_name: str, feed_url: str) -> None:
        """
        Fetch and store new articles from a single RSS feed.

        Args:
            source_name: Short internal name (e.g. ``"reuters_business"``).
                         Used as the source_name column value and in the
                         ingestion_log ``source`` column as ``rss:<name>``.
            feed_url:    Full URL of the RSS feed.
        """
        log_source = f"rss:{source_name}"
        records_fetched: int = 0
        records_stored: int = 0
        error_message: Optional[str] = None

        try:
            logger.info(f"[{log_source}] Fetching {feed_url}")
            feed = feedparser.parse(feed_url)

            if feed.bozo and not feed.entries:
                # feedparser sets bozo=True on parse errors; still try if
                # there are partial entries, bail only when list is empty.
                error_message = str(
                    getattr(feed, "bozo_exception", None) or "Feed parse error"
                )
                logger.warning(f"[{log_source}] Parse error: {error_message}")
                # Fall through to write the error log row
                return

            records_fetched = len(feed.entries)
            new_articles: List[RSSArticle] = []

            async with get_async_session() as session:
                for entry in feed.entries:
                    url: str = (entry.get("link") or "").strip()
                    if not url:
                        continue

                    # Fast-path dedup
                    if url in self._seen_urls:
                        continue

                    # DB dedup — relies on the UNIQUE constraint on url
                    existing = await session.execute(
                        select(RSSArticle.id)
                        .where(RSSArticle.url == url)
                        .limit(1)
                    )
                    if existing.scalar_one_or_none() is not None:
                        self._seen_urls.add(url)
                        continue

                    title: str = entry.get("title", "")
                    summary: str = entry.get("summary", "")
                    published_at = _parse_published(entry)

                    # Extract tickers from the combined title + summary text
                    tickers = extract_tickers(f"{title} {summary}")

                    raw_payload = {
                        "title": title,
                        "summary": summary,
                        "url": url,
                        "published": entry.get("published", ""),
                        "source": source_name,
                        "tags": [
                            t.get("term", "")
                            for t in entry.get("tags", [])
                            if isinstance(t, dict)
                        ],
                    }

                    new_articles.append(
                        RSSArticle(
                            title=title,
                            summary=summary,
                            url=url,
                            published_at=published_at,
                            source_name=source_name,
                            tickers=tickers,
                            raw_json=raw_payload,
                        )
                    )
                    self._seen_urls.add(url)

                if new_articles:
                    session.add_all(new_articles)
                    records_stored = len(new_articles)

                session.add(
                    IngestionLog(
                        source=log_source,
                        records_fetched=records_fetched,
                        records_stored=records_stored,
                        error_message=error_message,
                    )
                )

            logger.info(
                f"[{log_source}] Stored {records_stored}/{records_fetched} "
                "new articles."
            )

        except Exception as exc:
            error_message = str(exc)
            logger.exception(f"[{log_source}] Unexpected error: {exc}")
            # Best-effort log write so the dashboard shows the failure
            try:
                async with get_async_session() as session:
                    session.add(
                        IngestionLog(
                            source=log_source,
                            records_fetched=records_fetched,
                            records_stored=records_stored,
                            error_message=error_message,
                        )
                    )
            except Exception:
                pass  # Prevent logging errors from cascading
