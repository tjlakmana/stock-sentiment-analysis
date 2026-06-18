"""
Yahoo Finance price ingestor.

Fetches real-time price data for a list of tickers concurrently using
yfinance's fast_info. Results are upserted into the ticker_prices table
by the scheduler.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pytz
from loguru import logger

_ET       = pytz.timezone("America/New_York")
_WORKERS  = 12

# Single-letter tickers and SEC filing artifacts that are not tradeable equities.
# Single letters appear when the NLP pipeline extracts "K" from "8-K", "D" from
# "Form D", etc.  The explicit set covers the few single-letter NYSE/NASDAQ tickers
# that are real but almost never mentioned in SEC filings as companies.
_INVALID_TICKERS: frozenset[str] = frozenset({
    # Single-letter SEC form artifacts
    "K", "C", "A", "T", "F", "M", "R", "L", "V", "D", "W", "N", "X", "S",
    "O", "E", "H", "G", "B", "P", "I", "J", "Q", "U", "Y", "Z",
})

# Runtime skip list — populated when yfinance returns no usable data for a
# ticker across multiple calls.  Persists for the lifetime of the process so
# the same bad ticker is not retried every minute.
_SKIP_TICKERS: set[str] = set()
# Track consecutive failures per ticker before adding to the skip list.
_FAIL_COUNTS:  dict[str, int] = {}
_SKIP_AFTER_FAILURES = 3


def is_market_hours() -> bool:
    """Return True during NYSE regular trading hours (Mon–Fri 09:30–16:00 ET)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


class PriceIngestor:
    """
    Fetches current price snapshots for a list of tickers.
    Uses a thread pool so that yfinance HTTP calls run in parallel.
    """

    def get_current_prices(self, tickers: list[str]) -> list[dict]:
        """
        Return one dict per successfully fetched ticker:
            ticker, price, change_pct, volume, market_cap,
            pre_market_price, post_market_price, updated_at
        """
        if not tickers:
            return []

        filtered = [
            t for t in tickers
            if len(t) >= 2
            and t not in _INVALID_TICKERS
            and t not in _SKIP_TICKERS
        ]
        skipped = len(tickers) - len(filtered)
        if skipped:
            logger.debug(f"[price] Skipped {skipped} invalid/blocked tickers.")

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {pool.submit(self._fetch_one, t): t for t in filtered}
            for future in as_completed(futures):
                row = future.result()
                if row:
                    results.append(row)

        logger.info(f"[price] Fetched {len(results)}/{len(filtered)} tickers.")
        return results

    def _fetch_one(self, ticker: str) -> dict | None:
        try:
            import yfinance as yf
            fi = yf.Ticker(ticker).fast_info

            price = getattr(fi, "last_price", None)
            if price is None or math.isnan(float(price)):
                _FAIL_COUNTS[ticker] = _FAIL_COUNTS.get(ticker, 0) + 1
                if _FAIL_COUNTS[ticker] >= _SKIP_AFTER_FAILURES:
                    _SKIP_TICKERS.add(ticker)
                    logger.debug(f"[price] {ticker}: added to skip list after {_SKIP_AFTER_FAILURES} failures.")
                return None

            price = float(price)
            prev  = float(getattr(fi, "previous_close", price) or price)
            chg   = (price - prev) / prev * 100 if prev else 0.0

            def _int(v):
                return int(v) if v is not None and not math.isnan(float(v)) else None

            def _flt(v):
                return round(float(v), 4) if v is not None and not math.isnan(float(v)) else None

            return {
                "ticker":            ticker,
                "price":             round(price, 4),
                "change_pct":        round(chg,   4),
                "volume":            _int(getattr(fi, "last_volume",       None)),
                "market_cap":        _int(getattr(fi, "market_cap",        None)),
                "pre_market_price":  _flt(getattr(fi, "pre_market_price",  None)),
                "post_market_price": _flt(getattr(fi, "post_market_price", None)),
                "updated_at":        datetime.now(_ET),
            }
            # Reset fail count on success
            _FAIL_COUNTS.pop(ticker, None)
        except Exception as exc:
            _FAIL_COUNTS[ticker] = _FAIL_COUNTS.get(ticker, 0) + 1
            if _FAIL_COUNTS[ticker] >= _SKIP_AFTER_FAILURES:
                _SKIP_TICKERS.add(ticker)
                logger.debug(f"[price] {ticker}: added to skip list after {_SKIP_AFTER_FAILURES} failures.")
            else:
                logger.debug(f"[price] {ticker}: {exc}")
            return None
