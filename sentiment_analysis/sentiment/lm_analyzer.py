"""
Module: lm_analyzer.py
Purpose: Loughran-McDonald financial dictionary sentiment scorer for SEC filing articles
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Loughran-McDonald financial dictionary sentiment analyzer.

Used exclusively for SEC filing articles (8-K, Form 4, 10-Q, S-1, SC 13G).
Runs locally with no API calls or rate limits.
"""
from __future__ import annotations

from pysentiment2 import LM


class LMAnalyzer:
    """
    Loughran-McDonald (LM) financial dictionary wrapper.

    The LM dictionary was built specifically from 10-K filings and has much
    higher precision on SEC filing language than general-purpose dictionaries.
    Words like "liability" and "leverage" are negative in LM but neutral in
    standard sentiment dictionaries.

    Example:
        analyzer = LMAnalyzer()
        result = analyzer.score_text("Revenue increased, but net loss widened.")
        # result = {"label": "negative", "score": -0.05, "confidence": 0.15, ...}
    """

    def __init__(self) -> None:
        """Load the Loughran-McDonald dictionary via pysentiment2."""
        self.lm = LM()

    def score_text(self, text: str) -> dict:
        """
        Score a single text using positive and negative word counts.

        net_score = (positive_count - negative_count) / total_token_count
        Labels: positive if net_score >= 0.15, negative if <= -0.15, else neutral.
        Confidence is scaled so a net_score of 0.33 maps to 1.0 (saturates early
        because LM text rarely uses more than ~1/3 sentiment-laden words).

        Args:
            text: Raw article text (title + summary combined).

        Returns:
            dict: Keys are 'label', 'score', 'confidence', 'positive_words', 'negative_words'.
        """
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
        """
        Score a list of articles.

        Args:
            articles: List of dicts with keys 'id', 'title', 'summary'.

        Returns:
            list[dict]: Each dict has 'id', 'label', 'score', 'confidence'.
        """
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
