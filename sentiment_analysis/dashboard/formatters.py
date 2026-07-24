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


def _fmt_ratio(v: object, decimals: int = 2) -> str:
    """
    Format a ratio or multiple (P/E, PEG, Forward P/E, etc.) to a fixed
    number of decimal places. Returns "N/A" for missing or non-numeric values.

    Reusable across Key Metrics, Financial Highlights, and Technical Indicators.
    """
    try:
        f = float(v)
        if math.isnan(f):
            return "N/A"
    except (TypeError, ValueError):
        return "N/A"
    return f"{f:.{decimals}f}"


def _fmt_pct(v: object, decimals: int = 1, signed: bool = False) -> str:
    """
    Format a percentage already stored as a percentage value
    (e.g. 3.5 stored → '3.5%'). Returns "N/A" for missing values.
    Pass signed=True to prepend '+' on positive values.

    Reusable across Financial Highlights and Technical Indicators.
    """
    try:
        f = float(v)
        if math.isnan(f):
            return "N/A"
    except (TypeError, ValueError):
        return "N/A"
    prefix = "+" if signed and f > 0 else ""
    return f"{prefix}{f:.{decimals}f}%"


def _fmt_relvol(v: object) -> str:
    """
    Format relative volume as a multiplier (e.g. 1.80x).
    Returns "N/A" for missing values.

    Reusable across Key Metrics and Technical Indicators.
    """
    try:
        f = float(v)
        if math.isnan(f):
            return "N/A"
    except (TypeError, ValueError):
        return "N/A"
    return f"{f:.2f}x"
