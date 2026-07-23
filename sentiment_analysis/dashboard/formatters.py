"""
Shared display formatters for the dashboard.

All formatters return strings ready for the UI.
Values are expected to be stored as absolute numbers in the database
(e.g. market_cap in absolute dollars, not millions).
"""
from __future__ import annotations

import math


def _fmt_mktcap(v: object) -> str:
    """
    Format market cap in Finviz style.

    Finviz always uses the B denomination regardless of scale — trillion-dollar
    companies are shown as e.g. "5064.70B", not "5.06T".  Values below $1B
    fall to M, then K, then raw integer.  Two decimal places throughout.
    No dollar-sign prefix (matches Finviz screener table).
    """
    try:
        f = float(v)
        if math.isnan(f) or f <= 0:
            return "—"
    except (TypeError, ValueError):
        return "—"

    if f >= 1_000_000_000:
        return f"{f / 1_000_000_000:.2f}B"
    if f >= 1_000_000:
        return f"{f / 1_000_000:.2f}M"
    if f >= 1_000:
        return f"{f / 1_000:.2f}K"
    return str(int(f))
