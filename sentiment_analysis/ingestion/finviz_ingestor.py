"""
Finviz Elite price ingestor.

Fetches 1-minute intraday data from the Finviz Elite CSV export endpoint.
Requires a FINVIZ_TOKEN environment variable (Finviz Elite subscription).

Endpoint: https://elite.finviz.com/quote_export?t=TICKER&p=i1&auth=TOKEN
  p=i1 — 1-minute intraday, real-time data
  Returns CSV: Date, Time, Open, High, Low, Close, Volume
"""
from __future__ import annotations

import time
from datetime import datetime
from io import StringIO

import pytz
import requests
import pandas as pd
from loguru import logger

_ET = pytz.timezone("America/New_York")

# Single-letter tickers and known SEC filing artifacts (not tradeable equities).
_INVALID_TICKERS: frozenset[str] = frozenset({
    "K", "C", "A", "T", "F", "M", "R", "L", "V", "D", "W", "N", "X", "S",
    "O", "E", "H", "G", "B", "P", "I", "J", "Q", "U", "Y", "Z",
})

# Runtime skip list — after _SKIP_AFTER_FAILURES consecutive empty responses
# the ticker is blocked for the lifetime of the process.
_SKIP_TICKERS:  set[str]       = set()
_FAIL_COUNTS:   dict[str, int] = {}
_SKIP_AFTER_FAILURES = 3


def is_market_hours() -> bool:
    """Return True during NYSE regular trading hours (Mon–Fri 09:30–16:00 ET)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


class FinvizIngestor:
    """
    Fetches current price data from Finviz Elite for a list of tickers.
    Requests are sequential with a 0.5 s delay to respect shared-account limits.
    """

    BASE_URL = "https://elite.finviz.com/quote_export"

    def __init__(self, auth_token: str) -> None:
        self.auth_token = auth_token
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    # ── Public API ────────────────────────────────────────────────────────

    def get_quotes_batch(self, tickers: list[str]) -> list[dict]:
        """
        Fetch prices for all tickers, returning one dict per success.
        Skips single-char, known-invalid, and runtime-failed tickers.
        """
        filtered = [
            t for t in tickers
            if len(t) >= 2
            and t not in _INVALID_TICKERS
            and t not in _SKIP_TICKERS
        ]
        skipped = len(tickers) - len(filtered)
        if skipped:
            logger.debug(f"[finviz] Skipped {skipped} invalid/blocked tickers.")

        results: list[dict] = []
        for ticker in filtered:
            row = self.get_quote(ticker)
            if row:
                results.append(row)
            time.sleep(0.5)

        logger.info(f"[finviz] Fetched {len(results)}/{len(filtered)} tickers.")
        return results

    def get_quote(self, ticker: str) -> dict | None:
        """
        Fetch the latest 1-minute bar for a single ticker.
        Returns a price dict or None on failure.
        """
        url = f"{self.BASE_URL}?t={ticker}&p=i1&auth={self.auth_token}"
        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()

            df = pd.read_csv(StringIO(resp.text))
            if df.empty:
                return self._record_failure(ticker, "empty CSV")

            # Latest bar = most recent price
            last  = df.iloc[-1]
            first = df.iloc[0]

            close_price = float(last.get("Close", 0) or 0)
            if close_price == 0:
                return self._record_failure(ticker, "zero close price")

            open_price = float(first.get("Open", close_price) or close_price)
            change_pct = (
                (close_price - open_price) / open_price * 100
                if open_price > 0 else 0.0
            )

            # Reset failure counter on success
            _FAIL_COUNTS.pop(ticker, None)

            result = {
                "ticker":            ticker,
                "price":             round(close_price, 4),
                "change_pct":        round(change_pct, 2),
                "volume":            int(last.get("Volume", 0) or 0),
                "market_cap":        None,
                "pre_market_price":  None,
                "post_market_price": None,
                "updated_at":        datetime.now(_ET),
            }
            logger.debug(
                f"[finviz] {ticker}: ${close_price:.2f} "
                f"({'+'if change_pct >= 0 else ''}{change_pct:.2f}%)"
            )
            return result

        except requests.HTTPError as exc:
            return self._record_failure(ticker, str(exc))
        except Exception as exc:
            return self._record_failure(ticker, str(exc))

    # ── Private helpers ───────────────────────────────────────────────────

    def _record_failure(self, ticker: str, reason: str) -> None:
        _FAIL_COUNTS[ticker] = _FAIL_COUNTS.get(ticker, 0) + 1
        if _FAIL_COUNTS[ticker] >= _SKIP_AFTER_FAILURES:
            _SKIP_TICKERS.add(ticker)
            logger.debug(
                f"[finviz] {ticker}: added to skip list after "
                f"{_SKIP_AFTER_FAILURES} failures ({reason})."
            )
        else:
            logger.debug(f"[finviz] {ticker}: {reason}")
        return None
