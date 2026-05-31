"""
Twitter/X API v2 ingestor.

Polls the Recent Search endpoint for tweets matching stock cashtags combined
with financial keywords.  Uses tweepy v4+ Client (Bearer Token auth).

Key behaviours
--------------
- Deduplicates against an in-memory set (fast path) and the DB (restart-safe).
- Handles TooManyRequests with capped exponential back-off so the scheduler
  job returns cleanly without crashing the process.
- Writes one IngestionLog row per poll cycle regardless of outcome.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import tweepy
import tweepy.errors
from loguru import logger
from sqlalchemy import select

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.ticker_list import extract_tickers
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import IngestionLog, Tweet


class TwitterIngestor:
    """
    Polls the Twitter/X v2 Recent Search endpoint on a scheduled cadence.

    A single instance should be reused across scheduler runs so that the
    in-memory dedup set survives across polls.
    """

    MAX_RESULTS: int = 100          # API max per request
    BACKOFF_INITIAL: int = 60       # seconds before first retry after rate-limit
    BACKOFF_MAX: int = 900          # cap for exponential back-off (~15 min)

    def __init__(self) -> None:
        self._client: Optional[tweepy.Client] = None
        # In-memory dedup: tweet IDs seen since process start
        self._seen_ids: Set[str] = set()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _client_or_raise(self) -> tweepy.Client:
        """Return a cached tweepy Client, creating it on first call."""
        if self._client is None:
            if not settings.twitter_bearer_token:
                raise RuntimeError(
                    "TWITTER_BEARER_TOKEN is not configured. "
                    "Twitter ingestion is disabled."
                )
            self._client = tweepy.Client(
                bearer_token=settings.twitter_bearer_token,
                # We manage back-off ourselves for finer control
                wait_on_rate_limit=False,
            )
        return self._client

    def _build_query(self) -> str:
        """
        Build the Twitter v2 search query.

        Example output::

            ($AAPL OR $TSLA OR $NVDA) (earnings OR revenue OR guidance) lang:en -is:retweet
        """
        cashtags = " OR ".join(f"${t}" for t in settings.ticker_watchlist)
        keywords = " OR ".join(settings.financial_keywords)
        return f"({cashtags}) ({keywords}) lang:en -is:retweet"

    @staticmethod
    def _cashtags_from_entities(entities: Optional[Dict[str, Any]]) -> List[str]:
        """Extract ticker symbols from the tweet `entities.cashtags` API field."""
        if not entities:
            return []
        return [
            ct["tag"].upper()
            for ct in entities.get("cashtags", [])
            if "tag" in ct
        ]

    @staticmethod
    def _to_serialisable(obj: Any) -> Any:
        """Recursively convert tweepy data objects to plain Python types."""
        if isinstance(obj, dict):
            return {k: TwitterIngestor._to_serialisable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [TwitterIngestor._to_serialisable(v) for v in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Execute one poll cycle.

        Steps:
          1. Call the Recent Search endpoint with exponential back-off.
          2. Deduplicate returned tweets.
          3. Persist new tweets and an IngestionLog row.
        """
        records_fetched: int = 0
        records_stored: int = 0
        error_message: Optional[str] = None

        try:
            client = self._client_or_raise()
            query = self._build_query()
            logger.info(f"[twitter] Querying: {query[:100]}…")

            # ---- rate-limited request with back-off ------------------- #
            backoff = self.BACKOFF_INITIAL
            response: Optional[tweepy.Response] = None

            while True:
                try:
                    response = client.search_recent_tweets(
                        query=query,
                        max_results=self.MAX_RESULTS,
                        tweet_fields=[
                            "id",
                            "text",
                            "author_id",
                            "created_at",
                            "public_metrics",
                            "entities",
                        ],
                    )
                    break  # success
                except tweepy.errors.TooManyRequests:
                    logger.warning(
                        f"[twitter] Rate limited — backing off {backoff}s"
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.BACKOFF_MAX)
                except tweepy.errors.TwitterServerError as exc:
                    error_message = f"Twitter server error: {exc}"
                    logger.error(f"[twitter] {error_message}")
                    break

            if response is None or not response.data:
                logger.info("[twitter] No tweets returned this cycle.")
                return

            records_fetched = len(response.data)
            new_tweets: List[Tweet] = []

            async with get_async_session() as session:
                for tweet in response.data:
                    tweet_id = str(tweet.id)

                    # Fast-path dedup
                    if tweet_id in self._seen_ids:
                        continue

                    # DB dedup (handles process restarts)
                    if await session.get(Tweet, tweet_id) is not None:
                        self._seen_ids.add(tweet_id)
                        continue

                    metrics: Dict[str, Any] = tweet.public_metrics or {}
                    entities: Dict[str, Any] = (
                        tweet.entities
                        if isinstance(tweet.entities, dict)
                        else {}
                    )

                    # Merge API-reported cashtags with regex extraction
                    api_tickers = self._cashtags_from_entities(entities)
                    text_tickers = extract_tickers(tweet.text or "")
                    tickers = list(dict.fromkeys(api_tickers + text_tickers))

                    raw_payload = self._to_serialisable({
                        "id": tweet_id,
                        "text": tweet.text,
                        "author_id": str(tweet.author_id),
                        "created_at": tweet.created_at,
                        "public_metrics": metrics,
                        "entities": entities,
                    })

                    new_tweets.append(
                        Tweet(
                            id=tweet_id,
                            text=tweet.text,
                            author_id=str(tweet.author_id) if tweet.author_id else None,
                            created_at=tweet.created_at,
                            retweet_count=metrics.get("retweet_count", 0),
                            like_count=metrics.get("like_count", 0),
                            tickers=tickers,
                            raw_json=raw_payload,
                        )
                    )
                    self._seen_ids.add(tweet_id)

                if new_tweets:
                    session.add_all(new_tweets)
                    records_stored = len(new_tweets)

                session.add(
                    IngestionLog(
                        source="twitter",
                        records_fetched=records_fetched,
                        records_stored=records_stored,
                        error_message=error_message,
                    )
                )

            logger.info(
                f"[twitter] Stored {records_stored}/{records_fetched} new tweets."
            )

        except RuntimeError as exc:
            # Configuration error (missing token) — log and skip gracefully
            logger.warning(f"[twitter] Skipping poll: {exc}")

        except Exception as exc:
            error_message = str(exc)
            logger.exception(f"[twitter] Unexpected error: {exc}")
            # Best-effort log write so the dashboard shows the error
            try:
                async with get_async_session() as session:
                    session.add(
                        IngestionLog(
                            source="twitter",
                            records_fetched=records_fetched,
                            records_stored=records_stored,
                            error_message=error_message,
                        )
                    )
            except Exception:
                pass
