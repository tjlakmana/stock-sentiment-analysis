"""
Module: finbert_analyzer.py
Purpose: FinBERT-based local sentiment analysis for financial news articles (primary analyzer)
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

FinBERT-based financial news sentiment analyzer.

Model: peyterho/finbert-macro-sentiment
Architecture: BERT fine-tuned on financial macro news (3-class: positive / neutral / negative)

Design:
  - Singleton: model and tokenizer load exactly once per process (lazy on first call)
  - Batch inference: articles grouped into mini-batches for efficiency
  - Thread-safe: double-checked lock prevents duplicate loads under concurrency
  - Device: CUDA if available, CPU otherwise
  - Score: positive_probability − negative_probability  → (−1.0 … +1.0)
  - Confidence: max class probability
  - Fallback: caller is responsible for catching exceptions and switching to Gemini
"""
from __future__ import annotations

import threading
import time
from uuid import UUID

from loguru import logger

# ── Model identifier ──────────────────────────────────────────────────────────

_MODEL_NAME = "peyterho/finbert-macro-sentiment"

# ── Score-to-label thresholds ─────────────────────────────────────────────────
# Outer thresholds are configurable via Settings / env vars.
# Inner ("somewhat") thresholds are fixed at the same values used by Gemini.

_DEFAULT_POS_THRESHOLD: float = 0.35   # score ≥ this  → "Bullish"
_DEFAULT_NEG_THRESHOLD: float = 0.35   # score ≤ -this → "Bearish"
_SOMEWHAT_POS:          float = 0.15
_SOMEWHAT_NEG:          float = -0.15

# ── Inference tuning ──────────────────────────────────────────────────────────
# 16 texts per mini-batch is safe for Railway CPU (512 MB–1 GB RAM).
# Increase to 32 if your deployment has ≥ 2 GB RAM or a GPU.

_DEFAULT_BATCH_SIZE: int = 16

# Module-level lock prevents two threads from loading the model simultaneously.
_load_lock = threading.Lock()


# ── Label conversion ──────────────────────────────────────────────────────────

def _score_to_label(
    score: float,
    pos_threshold: float,
    neg_threshold: float,
) -> str:
    """Map a score in (−1..+1) to a 5-tier sentiment label."""
    if score >=  pos_threshold: return "Bullish"
    if score >=  _SOMEWHAT_POS: return "Somewhat Bullish"
    if score >   _SOMEWHAT_NEG: return "Neutral"
    if score > -neg_threshold:  return "Somewhat Bearish"
    return "Bearish"


# ── Analyzer ──────────────────────────────────────────────────────────────────

class FinBERTAnalyzer:
    """
    Singleton FinBERT analyzer.

    Instantiate once at the module level (or via the pipeline singleton getter).
    The model loads lazily on the first call to score_batch(); subsequent calls
    reuse the already-loaded model with no re-initialization overhead.

    Example::

        analyzer = FinBERTAnalyzer()
        results  = analyzer.score_batch(articles)  # model loads here, once
        results2 = analyzer.score_batch(more)      # reuses loaded model
    """

    def __init__(
        self,
        pos_threshold: float = _DEFAULT_POS_THRESHOLD,
        neg_threshold: float = _DEFAULT_NEG_THRESHOLD,
        batch_size:    int   = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self.pos_threshold = pos_threshold
        self.neg_threshold = neg_threshold
        self.batch_size    = batch_size

        # Set by _load(); None until first score_batch() call
        self._tokenizer = None
        self._model     = None
        self._device    = None
        self._id2label: dict[int, str] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def score_batch(self, articles: list[dict]) -> list[dict]:
        """
        Score a batch of articles with FinBERT.

        Input dict keys (per article):
            article_id   (UUID)  — propagated to result unchanged
            headline     (str)   — article title
            cleaned_text (str)   — preprocessed body text

        Output dict keys (per article):
            article_id, sentiment_label, sentiment_score, sentiment_confidence,
            positive_probability, neutral_probability, negative_probability,
            model_name

        Raises any exception from the model; the pipeline catches and falls
        back to Gemini.
        """
        if not articles:
            return []

        self._ensure_loaded()

        texts = [self._build_text(a) for a in articles]
        ids   = [a["article_id"] for a in articles]

        t0        = time.perf_counter()
        prob_rows = self._infer_batched(texts)
        elapsed   = time.perf_counter() - t0

        logger.info(f"[finbert] Scored {len(articles)} articles in {elapsed:.1f}s")

        return [
            self._to_result(article_id, probs)
            for article_id, probs in zip(ids, prob_rows)
        ]

    # ── Model loading ─────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Lazy double-checked load — thread-safe, loads only once."""
        if self._model is not None:
            return
        with _load_lock:
            if self._model is None:
                self._load()

    def _load(self) -> None:
        """Download (first run) or load from cache (subsequent runs) the model."""
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        logger.info(f"[finbert] Loading model {_MODEL_NAME!r}...")
        t0 = time.perf_counter()

        self._tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        self._model     = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)

        # Device selection
        if torch.cuda.is_available():
            self._device = torch.device("cuda")
            logger.info("[finbert] GPU detected — using CUDA")
        else:
            self._device = torch.device("cpu")
            logger.info("[finbert] No GPU detected — using CPU")

        self._model.to(self._device)
        self._model.eval()

        # Normalise label map to lowercase for robust key lookup
        self._id2label = {
            int(k): v.lower()
            for k, v in self._model.config.id2label.items()
        }

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[finbert] Model loaded successfully in {elapsed:.1f}s  "
            f"| labels={list(self._id2label.values())}"
        )
        self._warmup()

    def _warmup(self) -> None:
        """Single inference to prime the model before first real batch."""
        try:
            self._infer_batched(["Market conditions remain broadly stable."])
            logger.info("[finbert] Warm-up complete.")
        except Exception as exc:
            logger.warning(f"[finbert] Warm-up skipped (non-fatal): {exc}")

    # ── Inference ─────────────────────────────────────────────────────────────

    def _infer_batched(self, texts: list[str]) -> list[dict[str, float]]:
        """Split texts into mini-batches and run inference on each."""
        results: list[dict[str, float]] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start : start + self.batch_size]
            results.extend(self._infer_chunk(chunk))
        return results

    def _infer_chunk(self, texts: list[str]) -> list[dict[str, float]]:
        """Tokenize + forward pass for one mini-batch of texts."""
        import torch

        enc = self._tokenizer(
            texts,
            return_tensors="pt",
            truncation=True,
            padding=True,
            max_length=512,
        )
        enc = {k: v.to(self._device) for k, v in enc.items()}

        with torch.no_grad():
            logits = self._model(**enc).logits
            probs  = torch.nn.functional.softmax(logits, dim=-1).cpu().tolist()

        # Map integer class index → lowercase label name
        return [
            {self._id2label[i]: p for i, p in enumerate(row)}
            for row in probs
        ]

    # ── Text preparation ──────────────────────────────────────────────────────

    def _build_text(self, article: dict) -> str:
        """
        Combine headline and body into a single classifier input.

        Pre-truncates to 600 characters before tokenisation; the tokenizer's
        max_length=512 token limit is the hard cap.
        """
        headline = (article.get("headline") or article.get("title") or "").strip()
        body     = (article.get("cleaned_text") or "").strip()
        text     = f"{headline}. {body}" if body else headline
        return text[:600]

    # ── Result construction ───────────────────────────────────────────────────

    def _to_result(self, article_id: UUID, probs: dict[str, float]) -> dict:
        """
        Convert raw class probabilities into the standardised result dict.

        sentiment_score = positive_probability − negative_probability
        confidence      = max(positive, neutral, negative)
        """
        positive = probs.get("positive", 0.0)
        negative = probs.get("negative", 0.0)
        neutral  = probs.get("neutral",  0.0)

        score      = positive - negative
        confidence = max(positive, negative, neutral)
        label      = _score_to_label(score, self.pos_threshold, self.neg_threshold)

        return {
            "article_id":           article_id,
            "sentiment_label":      label,
            "sentiment_score":      round(score,      4),
            "sentiment_confidence": round(confidence, 4),
            "positive_probability": round(positive,   4),
            "neutral_probability":  round(neutral,    4),
            "negative_probability": round(negative,   4),
            "model_name":           _MODEL_NAME,
        }
