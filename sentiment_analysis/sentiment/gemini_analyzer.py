"""
Gemini-based sentiment analysis engine.

Uses gemini-2.5-flash with structured output (response_schema=list[ArticleSentiment])
to analyze batches of up to 50 financial news articles in a single API request.
Retries up to 3 times on error with a 10-second delay between attempts.
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


class GeminiAnalyzer:
    """
    Wraps the Google Gemini API client.
    Instantiate once as a module-level singleton; client is lazy-loaded.
    """

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    def analyze_batch(self, articles: list[dict]) -> list[dict]:
        """
        Analyze up to 50 articles in a single Gemini request with structured output.

        Each dict must contain:
            article_id (UUID), ticker (str), headline (str), cleaned_text (str)

        Returns a list of result dicts:
            article_id, sentiment_label, sentiment_score, sentiment_confidence
        """
        if not articles:
            return []

        if not settings.gemini_api_key:
            logger.warning("[gemini] GEMINI_API_KEY not configured — skipping batch.")
            return []

        id_to_uuid: dict[int, UUID] = {
            i: art["article_id"] for i, art in enumerate(articles)
        }

        batch_input = [
            {
                "id":       i,
                "ticker":   art.get("ticker", ""),
                "headline": art.get("headline", ""),
                "text":     (art.get("cleaned_text") or "")[:500],
            }
            for i, art in enumerate(articles)
        ]

        prompt = self._build_prompt(batch_input)

        for attempt in range(3):
            try:
                from google.genai import types
                response = self._get_client().models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=list[ArticleSentiment],
                        temperature=0.1,
                    ),
                )
                return self._parse_response(response, id_to_uuid)
            except Exception as exc:
                if attempt < 2:
                    logger.warning(
                        f"[gemini] Attempt {attempt + 1}/3 failed: {exc}. Retrying in 10s..."
                    )
                    time.sleep(10)
                else:
                    logger.warning(
                        f"[gemini] All 3 attempts failed for batch of {len(articles)} "
                        "articles. Skipping batch."
                    )
        return []

    def _build_prompt(self, batch_input: list[dict]) -> str:
        articles_json = json.dumps(batch_input, ensure_ascii=False, indent=2)
        return (
            "You are a financial news sentiment analyzer. For each article "
            "assess the likely market impact from an equity investor's perspective.\n\n"
            "For each article return:\n"
            "  id: echo back the article's id field exactly\n"
            "  label: one of \"positive\", \"negative\", \"neutral\", \"mixed\"\n"
            "  score: float from -1.0 (extremely bearish) to +1.0 (extremely bullish)\n\n"
            "Focus on earnings, M&A, FDA decisions, regulatory actions, "
            "guidance revisions, and macro events.\n\n"
            f"Articles:\n{articles_json}"
        )

    def _parse_response(self, response, id_to_uuid: dict[int, UUID]) -> list[dict]:
        items: list[ArticleSentiment] = []
        try:
            if response.parsed is not None:
                items = response.parsed
            else:
                raw = json.loads(response.text)
                items = [ArticleSentiment(**item) for item in raw]
        except Exception as exc:
            logger.warning(f"[gemini] Response parse failed: {exc}")
            return []

        results: list[dict] = []
        for item in items:
            try:
                article_id = id_to_uuid.get(item.id)
                if article_id is None:
                    continue
                score      = max(-1.0, min(1.0, float(item.score)))
                confidence = compute_confidence(score)
                results.append({
                    "article_id":           article_id,
                    "sentiment_label":      score_to_label(score),
                    "sentiment_score":      score,
                    "sentiment_confidence": confidence,
                })
            except (ValueError, KeyError, AttributeError) as exc:
                logger.debug(f"[gemini] Skipping malformed item: {exc}")

        return results
