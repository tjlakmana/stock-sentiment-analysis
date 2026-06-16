"""
Groq-based sentiment analysis engine.

Uses llama-3.3-70b-versatile with JSON mode to analyze batches of up to 50
financial news articles in a single API request. Retries up to 3 times on
error with a 10-second delay between attempts.
"""
from __future__ import annotations

import json
import time
from typing import Literal
from uuid import UUID

from loguru import logger
from pydantic import BaseModel

from sentiment_analysis.config import settings


class ArticleSentiment(BaseModel):
    """Pydantic schema for a single article sentiment result."""
    id: int
    label: Literal["positive", "negative", "neutral", "mixed"]
    score: float  # -1.0 (bearish) → +1.0 (bullish)


def compute_confidence(score: float) -> float:
    """Distance from neutral, scaled to 0–1 (0.5 raw → 1.0 confidence)."""
    return min(abs(score) * 2.0, 1.0)


def score_to_label(score: float) -> str:
    if score >= 0.35:
        return "Bullish"
    elif score >= 0.15:
        return "Somewhat Bullish"
    elif score > -0.15:
        return "Neutral"
    elif score > -0.35:
        return "Somewhat Bearish"
    else:
        return "Bearish"


class GroqAnalyzer:
    """
    Wraps the Groq sync client.
    Instantiate once as a module-level singleton; client is lazy-loaded.
    """

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    def analyze_batch(self, articles: list[dict]) -> list[dict]:
        """
        Send up to 100 articles in a single Groq request.

        Each dict in ``articles`` must contain:
            article_id (UUID), ticker (str), headline (str), cleaned_text (str).

        Returns a list of result dicts (one per article):
            article_id, sentiment_label, sentiment_score, sentiment_confidence.
        All articles with a valid score are returned regardless of confidence.
        """
        if not articles:
            return []

        if not settings.groq_api_key:
            logger.warning("[groq] GROQ_API_KEY not configured — skipping batch.")
            return []

        # Sequential index → UUID (model uses int ids to keep JSON compact)
        id_to_uuid: dict[int, UUID] = {
            i: art["article_id"] for i, art in enumerate(articles)
        }

        batch_input = [
            {
                "id": i,
                "ticker": art.get("ticker", ""),
                "headline": art.get("headline", ""),
                "text": (art.get("cleaned_text") or "")[:500],
            }
            for i, art in enumerate(articles)
        ]

        prompt = self._build_prompt(batch_input)

        for attempt in range(3):
            try:
                response = self._get_client().chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=4096,
                )
                return self._parse_response(response, id_to_uuid)
            except Exception as exc:
                if attempt < 2:
                    logger.warning(
                        f"[groq] Attempt {attempt + 1}/3 failed: {exc}. Retrying in 10s..."
                    )
                    time.sleep(10)
                else:
                    logger.warning(
                        f"[groq] All 3 attempts failed for batch of {len(articles)} articles. "
                        "Skipping batch."
                    )
        return []

    def _build_prompt(self, batch_input: list[dict]) -> str:
        articles_json = json.dumps(batch_input, ensure_ascii=False, indent=2)
        return (
            "You are a financial news sentiment analyzer. For each article "
            "assess the likely market impact from an equity investor's perspective.\n\n"
            "Return a JSON object with a single key \"results\" containing an array — "
            "one element per article — with:\n"
            "  id: echo back the article's id field exactly\n"
            "  label: one of \"positive\", \"negative\", \"neutral\", \"mixed\"\n"
            "  score: float from -1.0 (extremely bearish) to +1.0 (extremely bullish)\n\n"
            "Focus on earnings, M&A, FDA decisions, regulatory actions, "
            "guidance revisions, and macro events.\n\n"
            f"Articles:\n{articles_json}"
        )

    def _parse_response(self, response, id_to_uuid: dict[int, UUID]) -> list[dict]:
        try:
            content = response.choices[0].message.content
            data = json.loads(content)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for key in ("results", "articles", "sentiments"):
                    if key in data and isinstance(data[key], list):
                        items = data[key]
                        break
                else:
                    items = next((v for v in data.values() if isinstance(v, list)), [])
            else:
                items = []
        except Exception as exc:
            logger.warning(f"[groq] Response parse failed: {exc}")
            return []

        results: list[dict] = []
        for item in items:
            try:
                idx = int(item.get("id", -1))
                article_id = id_to_uuid.get(idx)
                if article_id is None:
                    continue

                score = float(item.get("score", 0.0))
                score = max(-1.0, min(1.0, score))
                confidence = compute_confidence(score)

                results.append({
                    "article_id": article_id,
                    "sentiment_label": score_to_label(score),
                    "sentiment_score": score,
                    "sentiment_confidence": confidence,
                })
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug(f"[groq] Skipping malformed item: {exc}")

        return results
