"""
Primary Company Scorer — post-extraction stage.

Takes the candidate tickers already found by the 3-pass extraction system
and determines which single company the article is *primarily about*.

Returns a ScoringResult with:
  - primary_ticker : str | None  (None if no company clears the threshold)
  - winner_score   : int
  - all_scores     : dict[str, int]
  - reason         : human-readable string for logging

Scoring weights
---------------
+60  Company name appears in title
+60  Ticker symbol appears in title
+30  Company or ticker mentioned in first ~200 chars of summary
+20  Company mentioned more than 3 times in full text
+15  Cashtag detected ($TICKER)
 +5  Mentioned only once (very slight signal)

Threshold
---------
If the highest score < CONFIDENCE_THRESHOLD (default 70), primary_ticker = NULL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

CONFIDENCE_THRESHOLD = 70

# Legal suffixes to strip when building a short company name for matching.
_SUFFIX_PATTERN = re.compile(
    r"\s+(inc\.?|corp\.?|ltd\.?|llc\.?|plc\.?|co\.?|group|holdings?|"
    r"technologies|technology|corporation|limited|international|inc)$",
    re.IGNORECASE,
)


def _short_name(company_name: str) -> str:
    """Return the company name minus common legal suffixes, lowercased."""
    return _SUFFIX_PATTERN.sub("", company_name).strip().lower()


@dataclass
class ScoringResult:
    primary_ticker: Optional[str]
    winner_score: int
    all_scores: dict[str, int] = field(default_factory=dict)
    reason: str = ""


class PrimaryCompanyScorer:
    """
    Stateless scorer — safe to share across threads.

    Instantiate once as a module-level singleton in nlp/pipeline.py.
    """

    def score(
        self,
        title: str,
        summary: str,
        new_tickers: list[str],
        resolved_dicts: list[dict],
        company_names: dict[str, str],
        threshold: int = CONFIDENCE_THRESHOLD,
    ) -> ScoringResult:
        """
        Score each candidate ticker and return the primary company.

        Parameters
        ----------
        title          : article headline (original case)
        summary        : article body / summary (original case)
        new_tickers    : candidate tickers from the extraction pipeline
        resolved_dicts : dicts from EntityResolutionPipeline.extract() — used
                         to know which entity text NER matched for each ticker
        company_names  : ticker → company_name from ticker_prices table
        threshold      : minimum score to assign a primary_ticker
        """
        if not new_tickers:
            return ScoringResult(None, 0, {}, "No candidates extracted")

        full_text    = f"{title} {summary}"
        full_lower   = full_text.lower()
        title_lower  = title.lower()
        first_para   = (summary or "")[:200].lower()

        # Build ticker → set of known name variants (lower-cased)
        ticker_variants: dict[str, set[str]] = {t: set() for t in new_tickers}

        for d in resolved_dicts:
            t = d.get("ticker")
            if t in ticker_variants:
                et = (d.get("entity_text") or "").strip()
                if et and et.lower() != t.lower():
                    ticker_variants[t].add(et.lower())

        for t in new_tickers:
            raw = company_names.get(t, "")
            if raw:
                ticker_variants[t].add(raw.lower())
                short = _short_name(raw)
                if short and short != raw.lower() and len(short) >= 4:
                    ticker_variants[t].add(short)

        scores: dict[str, int] = {}

        for ticker in new_tickers:
            s = 0
            t_lower   = ticker.lower()
            variants  = ticker_variants.get(ticker, set())
            cashtag   = f"${ticker}"

            # ── Count total mentions in full text ─────────────────────────
            count = len(re.findall(rf"\b{re.escape(t_lower)}\b", full_lower))
            for v in variants:
                if v and len(v) >= 4:
                    count += full_lower.count(v)

            # ── Apply scoring rules ───────────────────────────────────────

            # +60  Company name in title
            if any(v and len(v) >= 4 and v in title_lower for v in variants):
                s += 60

            # +60  Ticker symbol in title (word boundary)
            if re.search(rf"\b{re.escape(t_lower)}\b", title_lower):
                s += 60

            # +30  Company or ticker in first paragraph
            ticker_in_para = bool(
                re.search(rf"\b{re.escape(t_lower)}\b", first_para)
            )
            name_in_para = any(v and len(v) >= 4 and v in first_para
                               for v in variants)
            if ticker_in_para or name_in_para:
                s += 30

            # +20  Mentioned more than 3 times
            if count > 3:
                s += 20

            # +15  Cashtag present (original case, anywhere in text)
            if cashtag in full_text:
                s += 15

            # +5   Mentioned only once
            if count == 1:
                s += 5

            scores[ticker] = s

        if not scores:
            return ScoringResult(None, 0, {}, "No scores computed")

        winner      = max(scores, key=lambda t: scores[t])
        winner_score = scores[winner]

        if winner_score < threshold:
            return ScoringResult(
                None,
                winner_score,
                scores,
                f"No sufficiently relevant company detected "
                f"(best: {winner}={winner_score})",
            )

        return ScoringResult(
            winner,
            winner_score,
            scores,
            f"Winner: {winner}",
        )
