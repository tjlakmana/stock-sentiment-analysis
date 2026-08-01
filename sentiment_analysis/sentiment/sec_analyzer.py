"""
Module: sec_analyzer.py
Purpose: Rule-based keyword sentiment analyzer for SEC EDGAR filings that runs locally with no API calls
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Rule-based sentiment analyzer for SEC EDGAR filings.

Simple keyword matching — going concern check first, then form-specific rules.
Always returns a result; defaults to Neutral 0.0 when nothing matches.
"""
from __future__ import annotations

# ============================================================
# KEYWORD LISTS
# ============================================================

# Going-concern language is the highest-priority signal — any appearance in any
# filing type is an immediate strong-bearish override.  These exact phrases are
# required by SEC disclosure rules when auditors have doubts about a company's
# ability to continue operations.
_GOING_CONCERN = (
    "going concern",
    "substantial doubt",
    "ability to continue",
    "significant doubt",
    "liquidity concerns",
)

_8K_BULLISH = (
    "acquisition", "acquires", "merger", "agreement",
    "partnership", "collaboration", "contract awarded",
    "dividend", "buyback", "share repurchase",
    "expanded", "launched", "approved",
)

_8K_BEARISH = (
    "bankruptcy", "delisting", "impairment", "write-down",
    "restructuring", "investigation", "class action",
    "material weakness", "restatement", "going concern",
    "layoffs", "workforce reduction",
)

_10Q_BULLISH = (
    "revenue increased", "profit increased", "beat",
    "exceeded", "record revenue", "raised guidance",
)

_10Q_BEARISH = (
    "revenue decreased", "net loss", "missed",
    "below expectations", "lowered guidance",
    "impairment", "material weakness",
)

_NEUTRAL = {"label": "neutral", "score": 0.0, "confidence": 0.60}


def _contains(text: str, phrases: tuple) -> bool:
    return any(p in text for p in phrases)


class SECAnalyzer:
    """
    Rule-based sentiment analyzer tuned for SEC EDGAR filing sources.

    All methods are synchronous and produce a result dict immediately —
    there are no external API calls, rate limits, or model loading delays.
    This makes SEC articles fast to process and always available.

    Result dict format:
        {"label": str, "score": float, "confidence": float}

    Example:
        analyzer = SECAnalyzer()
        result = analyzer.score_article(
            source_name="rss:sec_edgar",
            title="Company files Chapter 11 bankruptcy",
            summary="...",
        )
        # result == {"label": "bearish", "score": -0.70, "confidence": 0.80}
    """

    def score_article(self, source_name: str, title: str, summary: str) -> dict:
        """
        Score a single SEC filing article with form-specific keyword rules.

        Args:
            source_name: Feed identifier (e.g. 'rss:sec_edgar', 'rss:sec_form4').
            title: Article headline from the RSS feed.
            summary: Article body text.

        Returns:
            dict: Keys are 'label' (str), 'score' (float), 'confidence' (float).
        """
        text = ((title or "") + " " + (summary or "")).lower()

        # Going concern — highest priority, overrides all form-specific rules
        if _contains(text, _GOING_CONCERN):
            return {"label": "bearish", "score": -0.90, "confidence": 0.95}

        src = source_name.removeprefix("rss:")

        if src == "sec_form4":
            return self._form4(text)
        elif src == "sec_edgar":
            return self._8k(text)
        elif src in ("sec_10q", "sec_10k"):
            return self._10q(text)
        else:
            # S-1, SC 13G, unknown → Neutral
            return _NEUTRAL

    def score_batch(self, articles: list) -> list:
        """
        Score a list of SEC articles.

        Args:
            articles: List of dicts with keys 'id', 'source_name', 'title', 'summary'.

        Returns:
            list[dict]: Each dict has 'id' plus the keys from score_article().
        """
        results = []
        for a in articles:
            r = self.score_article(
                a.get("source_name", ""),
                a.get("title", ""),
                a.get("summary", ""),
            )
            results.append({"id": a["id"], **r})
        return results

    def _form4(self, text: str) -> dict:
        if "bought" in text:
            return {"label": "bullish", "score": +0.75, "confidence": 0.85}
        if "sold" in text:
            return {"label": "bearish", "score": -0.65, "confidence": 0.85}
        return _NEUTRAL

    def _8k(self, text: str) -> dict:
        if _contains(text, _8K_BEARISH):
            return {"label": "bearish", "score": -0.70, "confidence": 0.80}
        if _contains(text, _8K_BULLISH):
            return {"label": "bullish", "score": +0.60, "confidence": 0.80}
        return _NEUTRAL

    def _10q(self, text: str) -> dict:
        if _contains(text, _10Q_BEARISH):
            return {"label": "bearish", "score": -0.60, "confidence": 0.80}
        if _contains(text, _10Q_BULLISH):
            return {"label": "bullish", "score": +0.50, "confidence": 0.80}
        return _NEUTRAL
