"""
Ticker sentiment aggregation and spike detection.

Runs after each Gemini batch to:
  1. Aggregate per-ticker avg sentiment for 1-hr / 4-hr / 24-hr windows
     and detect momentum (improving / declining / stable).
  2. Detect article-count spikes (>2x rolling avg per 15-min bucket).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, text

from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import SentimentSpike, TickerSentimentSummary

_WINDOWS: dict[str, str] = {
    "1hr":  "1 hour",
    "4hr":  "4 hours",
    "24hr": "24 hours",
}


async def aggregate_ticker_sentiment() -> None:
    """Write per-ticker sentiment summaries for all three time windows."""
    for window_name, interval in _WINDOWS.items():
        try:
            await _aggregate_window(window_name, interval)
        except Exception as exc:
            logger.exception(f"[aggregator] Window {window_name} failed: {exc}")


async def detect_spikes() -> None:
    """Flag tickers whose current 15-min article count exceeds 2× their rolling avg."""
    async with get_async_session() as session:
        result = await session.execute(text("""
            SELECT
                unnest(tickers)                                           AS ticker,
                date_trunc('hour', ingested_at)
                + (EXTRACT(MINUTE FROM ingested_at)::int / 15)
                  * INTERVAL '15 minutes'                                AS bucket,
                COUNT(*)                                                  AS cnt
            FROM   rss_articles
            WHERE  ingested_at > NOW() - INTERVAL '4 hours'
              AND  cardinality(tickers) > 0
            GROUP  BY ticker, bucket
            ORDER  BY ticker, bucket DESC
        """))
        rows = result.fetchall()

    if not rows:
        return

    ticker_buckets: dict[str, list[int]] = defaultdict(list)
    for ticker, _bucket, cnt in rows:
        ticker_buckets[ticker].append(int(cnt))  # most-recent first (DESC)

    spikes: list[dict] = []
    for ticker, counts in ticker_buckets.items():
        if len(counts) < 3:
            continue
        current = counts[0]
        rolling_avg = sum(counts[1:]) / len(counts[1:])
        if rolling_avg < 1:
            continue
        spike_ratio = current / rolling_avg
        if spike_ratio >= 2.0:
            spikes.append(
                dict(ticker=ticker, article_count=current,
                     rolling_avg=rolling_avg, spike_ratio=spike_ratio)
            )

    if spikes:
        async with get_async_session() as session:
            for s in spikes:
                session.add(SentimentSpike(**s))
        logger.info(f"[aggregator] {len(spikes)} spike(s) recorded.")


# ── Private ──────────────────────────────────────────────────────────────────

async def _aggregate_window(window_name: str, interval: str) -> None:
    window_start = datetime.now(timezone.utc) - _to_timedelta(interval)

    async with get_async_session() as session:
        agg = await session.execute(text(f"""
            SELECT
                unnest(tickers)                                              AS ticker,
                AVG(sentiment_score)                                         AS avg_sentiment,
                COUNT(*)                                                     AS article_count,
                SUM(CASE WHEN sentiment_score >=  0.15 THEN 1 ELSE 0 END)   AS bullish_count,
                SUM(CASE WHEN sentiment_score <= -0.15 THEN 1 ELSE 0 END)   AS bearish_count,
                SUM(CASE WHEN sentiment_score >  -0.15
                          AND sentiment_score <   0.15 THEN 1 ELSE 0 END)   AS neutral_count
            FROM   rss_articles
            WHERE  sentiment_analyzed_at > NOW() - INTERVAL '{interval}'
              AND  sentiment_score IS NOT NULL
              AND  cardinality(tickers) > 0
            GROUP  BY ticker
            HAVING COUNT(*) >= 2
        """))
        rows = agg.fetchall()

        if not rows:
            return

        for row in rows:
            ticker, avg_s, art_cnt, bull, bear, neutral = row

            prev = await session.execute(
                select(TickerSentimentSummary.avg_sentiment)
                .where(
                    TickerSentimentSummary.ticker == ticker,
                    TickerSentimentSummary.window == window_name,
                )
                .order_by(TickerSentimentSummary.calculated_at.desc())
                .limit(1)
            )
            prev_avg = prev.scalar_one_or_none()

            if prev_avg is None:
                momentum = "stable"
            elif float(avg_s) > prev_avg + 0.05:
                momentum = "improving"
            elif float(avg_s) < prev_avg - 0.05:
                momentum = "declining"
            else:
                momentum = "stable"

            session.add(TickerSentimentSummary(
                ticker=ticker,
                window=window_name,
                window_start=window_start,
                avg_sentiment=float(avg_s),
                article_count=int(art_cnt),
                bullish_count=int(bull),
                bearish_count=int(bear),
                neutral_count=int(neutral),
                momentum=momentum,
            ))


def _to_timedelta(interval: str) -> timedelta:
    return timedelta(hours=int(interval.split()[0]))
