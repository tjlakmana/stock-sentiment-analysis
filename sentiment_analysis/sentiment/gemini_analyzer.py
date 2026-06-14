"""
Gemini-based sentiment analysis engine.

Professor's batching method: all articles in ONE API request, structured
output via response_schema=list[ArticleSentiment]. Model: gemini-2.5-flash.
"""
from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from loguru import logger
from pydantic import BaseModel

from sentiment_analysis.config import settings


class ArticleSentiment(BaseModel):
    """Pydantic schema passed to Gemini as response_schema."""
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
    Wraps the google-genai sync client.
    Instantiate once as a module-level singleton; client is lazy-loaded.
    """

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx
            from google import genai
            # bypass corporate/intermediate SSL cert issues (same env as RSS feeds)
            http_client = httpx.Client(verify=False)
            self._client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=genai.types.HttpOptions(httpx_client=http_client),
            )
        return self._client

    # ── Public API ──────────────────────────────────────────────────────────

    def analyze_batch(self, articles: list[dict]) -> list[dict]:
        """
        Send up to 50 articles in a single Gemini request.

        Each dict in ``articles`` must contain:
            article_id (UUID), ticker (str), headline (str), cleaned_text (str).

        Returns a list of result dicts (one per confident article):
            article_id, sentiment_label, sentiment_score, sentiment_confidence.
        Articles with confidence < 0.3 (|score| < 0.15) are omitted.
        """
        if not articles:
            return []

        if not settings.gemini_api_key:
            logger.warning("[gemini] GEMINI_API_KEY not configured — skipping batch.")
            return []

        # Sequential index → UUID (Gemini schema uses int ids)
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

        try:
            from google import genai as genai_module
            response = self._get_client().models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_module.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=list[ArticleSentiment],
                ),
            )
            return self._parse_response(response, id_to_uuid)

        except Exception as exc:
            logger.exception(f"[gemini] API error: {exc}")
            return []

    # ── Private helpers ─────────────────────────────────────────────────────

    def _build_prompt(self, batch_input: list[dict]) -> str:
        articles_json = json.dumps(batch_input, ensure_ascii=False, indent=2)
        return (
            "You are a financial news sentiment analyzer. For each article "
            "assess the likely market impact from an equity investor's perspective.\n\n"
            "Return a JSON array — one element per article — with:\n"
            "  id: echo back the article's id field exactly\n"
            "  label: one of \"positive\", \"negative\", \"neutral\", \"mixed\"\n"
            "  score: float from -1.0 (extremely bearish) to +1.0 (extremely bullish)\n\n"
            "Focus on earnings, M&A, FDA decisions, regulatory actions, "
            "guidance revisions, and macro events.\n\n"
            f"Articles:\n{articles_json}"
        )

    def _parse_response(
        self,
        response,
        id_to_uuid: dict[int, UUID],
    ) -> list[dict]:
        # Try .parsed (pydantic objects) then fall back to raw JSON text
        try:
            raw = getattr(response, "parsed", None)
            if raw is None:
                raw = json.loads(response.text)
            items: list[dict] = [
                item if isinstance(item, dict) else item.model_dump()
                for item in raw
            ]
        except Exception as exc:
            logger.warning(f"[gemini] Response parse failed: {exc}")
            return []

        results: list[dict] = []
        for item in items:
            try:
                idx = int(item.get("id", -1))
                article_id = id_to_uuid.get(idx)
                if article_id is None:
                    continue

                score = float(item.get("score", 0.0))
                score = max(-1.0, min(1.0, score))  # clamp to valid range
                confidence = compute_confidence(score)

                if confidence < 0.3:
                    continue  # near-neutral — not worth storing

                results.append({
                    "article_id": article_id,
                    "sentiment_label": score_to_label(score),
                    "sentiment_score": score,
                    "sentiment_confidence": confidence,
                })
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug(f"[gemini] Skipping malformed item: {exc}")

        return results
