"""
Rule-based sentiment analyzer for SEC EDGAR filings.

Simple keyword matching — going concern check first, then form-specific rules.
Always returns a result; defaults to Neutral 0.0 when nothing matches.
"""
from __future__ import annotations

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
    def score_article(self, source_name: str, title: str, summary: str) -> dict:
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
