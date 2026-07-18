"""
Company Resolver — maps a Company-classified entity to a ticker symbol.

Resolution signals (in priority order):
  1. explicit_ticker  — $AAPL, NASDAQ:MSFT, NYSE:IBM, Ticker: TSLA, (AAPL)
  2. company_dictionary — TickerMapper exact or fuzzy match

The resolver is conservative: it returns (None, 0.0, "none") rather than a
wrong ticker when confidence is insufficient.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sentiment_analysis.entity_resolution.ticker_mapper import TickerMapper

# ── Explicit ticker patterns (highest priority, bypass classification) ─────────
_CASHTAG_RE      = re.compile(r"\$([A-Z]{1,5})\b")
_EXCHANGE_PRE    = re.compile(r"\b(?:NASDAQ|NYSE|AMEX|CBOE|OTC):\s*([A-Z]{1,5})\b", re.IGNORECASE)
_TICKER_COLON    = re.compile(r"\bTicker:\s*([A-Z]{1,5})\b", re.IGNORECASE)
_PAREN_TICKER    = re.compile(r"\(([A-Z]{2,5})\)")   # (AAPL) in press releases
_NASDAQ_FULL     = re.compile(
    r"Nasdaq[:\s]+(?:Global Select Market|Capital Market):\s*([A-Z]{1,5})"
)

# Minimum ticker length — avoids treating "A" (Agilent) as a reliable explicit match
_MIN_TICKER_LEN = 2


@dataclass
class ResolutionResult:
    ticker: Optional[str]
    confidence: float      # raw mapper confidence (0.0 – 1.0)
    matched_by: str        # "explicit_ticker" | "company_dictionary" | "none"


class CompanyResolver:
    """
    Resolves a Company entity name to a ticker symbol.

    Instantiate once; pass explicit_tickers dict (from scan_explicit) to each
    resolve() call so that the pre-scanned signals can inform per-entity scoring.
    """

    def __init__(self, mapper: Optional[TickerMapper] = None) -> None:
        self._mapper = mapper or TickerMapper(fuzzy_threshold=0.82)

    # ── Public API ────────────────────────────────────────────────────────────

    def scan_explicit(self, full_text: str) -> dict[str, str]:
        """
        Scan raw article text for explicit ticker references.

        Returns {ticker: source_label} for every pattern match found.
        Call this once per article; pass the result to every resolve() call.
        """
        found: dict[str, str] = {}
        for pattern, label in (
            (_CASHTAG_RE,   "cashtag"),
            (_EXCHANGE_PRE, "exchange_prefix"),
            (_TICKER_COLON, "ticker_colon"),
            (_PAREN_TICKER, "paren_ticker"),
            (_NASDAQ_FULL,  "nasdaq_full"),
        ):
            for m in pattern.finditer(full_text):
                t = m.group(1).upper()
                if len(t) >= _MIN_TICKER_LEN:
                    found.setdefault(t, label)  # first-found source wins
        return found

    def resolve(
        self,
        entity_text: str,
        explicit_tickers: dict[str, str],
    ) -> ResolutionResult:
        """
        Resolve one Company entity to a ticker.

        Resolution order:
          1. Entity text is itself a known explicit ticker (e.g. "AAPL" in $AAPL)
          2. TickerMapper lookup — exact then fuzzy
          3. If dict-matched ticker was also seen explicitly → boost to explicit

        Returns ResolutionResult with ticker=None when no confident match exists.
        """
        upper = entity_text.upper().strip()

        # 1. Entity text IS the explicit ticker
        if upper in explicit_tickers and len(upper) >= _MIN_TICKER_LEN:
            return ResolutionResult(
                ticker=upper,
                confidence=1.0,
                matched_by="explicit_ticker",
            )

        # 2. Dictionary lookup (exact → normalised → fuzzy)
        ticker, conf = self._mapper.get_ticker(entity_text)
        if ticker:
            # Upgrade source label if the resolved ticker was also seen explicitly
            if ticker in explicit_tickers:
                return ResolutionResult(
                    ticker=ticker,
                    confidence=min(1.0, conf + 0.05),
                    matched_by="explicit_ticker",
                )
            return ResolutionResult(
                ticker=ticker,
                confidence=conf,
                matched_by="company_dictionary",
            )

        return ResolutionResult(ticker=None, confidence=0.0, matched_by="none")
