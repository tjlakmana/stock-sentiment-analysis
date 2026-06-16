"""
Loughran-McDonald financial dictionary sentiment analyzer.

Used exclusively for SEC filing articles (8-K, Form 4, 10-Q, S-1, SC 13G).
Runs locally with no API calls or rate limits.
"""
from __future__ import annotations

from pysentiment2 import LM


class LMAnalyzer:
    def __init__(self) -> None:
        self.lm = LM()

    def score_text(self, text: str) -> dict:
        tokens = self.lm.tokenize(text)
        score  = self.lm.get_score(tokens)

        positive = score["Positive"]
        negative = score["Negative"]
        total    = len(tokens)

        if total == 0:
            return {"label": "neutral", "score": 0.0, "confidence": 0.0,
                    "positive_words": 0, "negative_words": 0}

        net_score = float(positive - negative) / total

        if net_score >= 0.15:
            label = "positive"
        elif net_score <= -0.15:
            label = "negative"
        else:
            label = "neutral"

        confidence = min(abs(net_score) * 3, 1.0)

        return {
            "label":          label,
            "score":          round(net_score, 4),
            "confidence":     round(confidence, 4),
            "positive_words": int(positive),
            "negative_words": int(negative),
        }

    def score_batch(self, articles: list) -> list:
        results = []
        for article in articles:
            text   = f"{article.get('title', '')} {article.get('summary', '')}"
            result = self.score_text(text)
            results.append({
                "id":         article["id"],
                "label":      result["label"],
                "score":      result["score"],
                "confidence": result["confidence"],
            })
        return results
