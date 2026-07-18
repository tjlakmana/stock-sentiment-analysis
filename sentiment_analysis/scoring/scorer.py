"""
Importance scorer — assigns a 0–100 score to each news article.

Design
------
score = clamp(source_base + sum(group_contributions) + penalty, 0, 100)

  source_base         : fixed starting point by data source (signals.SOURCE_BASE)
  group_contributions : each SignalGroup sums its matching signal boosts, then
                        is capped at group.cap before being added to the total
  penalty             : sum of all matching penalty signals, capped at PENALTY_CAP

Groups contribute independently, so a single article can score from multiple
categories (e.g. an M&A + earnings surprise gets both boosts). Within a group,
all matching signals stack up to the cap, rewarding articles that match several
indicators for the same event (e.g. "FDA approved" + "accelerated approval").

To extend with LLM scoring later, subclass ImportanceScorer and override
score() to post-process the ScoringResult from super().score().
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sentiment_analysis.scoring.signals import (
    DEFAULT_BASE,
    PENALTY_CAP,
    PENALTY_SIGNALS,
    SIGNAL_GROUPS,
    SOURCE_BASE,
    score_to_label,
)

# Maximum number of characters from summary to include in signal matching.
# Enough to cover lede + first paragraph; long summaries risk false-positive
# penalty signal matches in unrelated surrounding text.
_SUMMARY_WINDOW = 600


@dataclass
class ScoringResult:
    """
    Result of scoring one article.

    Attributes
    ----------
    score   : 0–100 importance score
    label   : "High" (≥70), "Medium" (≥40), or "Low" (<40)
    signals : matched signal keywords for transparency / debugging
    """
    score:   int
    label:   str
    signals: list[str] = field(default_factory=list)


class ImportanceScorer:
    """
    Deterministic importance scorer for trading news articles.

    Instantiate once and reuse — the scorer is stateless and thread-safe.

    Usage
    -----
    scorer = ImportanceScorer()
    result = scorer.score(title="...", source_name="pr_newswire")
    print(result.score, result.label)   # e.g. 85, "High"
    print(result.signals)               # e.g. ["fda approved", "accelerated approval"]
    """

    def score(
        self,
        title:       str,
        source_name: str,
        summary:     str = "",
    ) -> ScoringResult:
        """
        Score one article.

        Parameters
        ----------
        title       : article headline (raw or cleaned)
        source_name : internal source key, e.g. "sec_edgar", "pr_newswire"
        summary     : article body/summary (first 600 chars used to limit
                      false-positive penalty matches in long text)

        Returns
        -------
        ScoringResult
        """
        # Build the search text: full title + beginning of summary.
        # Lower-casing is done once here to avoid repeated conversion.
        truncated_summary = (summary or "")[:_SUMMARY_WINDOW]
        text = f"{title or ''} {truncated_summary}".lower()

        base         = SOURCE_BASE.get(source_name, DEFAULT_BASE)
        total_boost  = 0
        matched:  list[str] = []

        # ── Apply positive signal groups ───────────────────────────────────
        for group in SIGNAL_GROUPS:
            group_boost = 0
            for sig in group.signals:
                if sig.keyword in text:
                    group_boost += sig.boost
                    matched.append(sig.keyword)
            if group_boost > 0:
                total_boost += min(group_boost, group.cap)

        # ── Apply penalty signals ──────────────────────────────────────────
        penalty = 0
        for sig in PENALTY_SIGNALS:
            if sig.keyword in text:
                penalty += sig.boost          # sig.boost is negative
                matched.append(sig.keyword)
        penalty = max(penalty, PENALTY_CAP)   # floor: penalties can't exceed -35

        raw   = base + total_boost + penalty
        score = max(0, min(100, raw))

        return ScoringResult(
            score=score,
            label=score_to_label(score),
            signals=matched,
        )
