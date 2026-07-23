"""
Finviz Elite bulk screener ingestor.

Fetches fundamental, technical, ownership, and performance data for all tickers
by querying six Finviz Elite screener export views and merging on Ticker.

Views used
----------
v=111  Overview         — core price data + P/E
v=121  Valuation/Growth — valuation ratios + EPS/sales growth
v=131  Ownership        — insider/inst ownership, short interest, avg volume
v=141  Performance      — weekly to yearly returns, relative volume
v=161  Financials       — dividend yield, margins, ROE/ROA, debt ratios
v=171  Technical        — beta, ATR, SMAs, RSI, 52-week range

Each view is fetched and validated independently before merging.  v=111 is the
base; all others are left-joined so no ticker from the base set is ever lost.

Fields unavailable in any Finviz bulk export
---------------------------------------------
eps_ttm, eps_growth_qoq, sales_growth_qoq, analyst_recom, target_price.
These remain NULL in the database until an alternative data source is wired in.
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

# ---------------------------------------------------------------------------
# Sector name normalisation
# ---------------------------------------------------------------------------

_SECTOR_NORMALIZE: dict[str, str] = {
    "Financial":              "Finance",
    "Consumer Defensive":     "Consumer",
    "Consumer Cyclical":      "Consumer",
    "Industrials":            "Industrial",
    "Basic Materials":        "Materials",
    "Communication Services": "Communication",
}

# ---------------------------------------------------------------------------
# N/A sentinel values Finviz uses when a field has no data
# ---------------------------------------------------------------------------

_NA = frozenset({"-", "nan", "None", "N/A", "", "0.00%"})

# ---------------------------------------------------------------------------
# Columns that appear in v=111 (the base view) AND recur in secondary views.
# These are dropped from secondary frames before the left-join so base values
# are never overwritten.
# ---------------------------------------------------------------------------

_SECONDARY_DROP = frozenset({"No.", "Price", "Change", "Volume", "Market Cap"})

# ---------------------------------------------------------------------------
# Columns we parse from each view, keyed by view ID.
#
# Any column listed here that is absent from the fetched CSV triggers a WARNING
# so that Finviz header renames surface immediately rather than silently
# producing an entire column of NULLs.
# ---------------------------------------------------------------------------

_EXPECTED_COLS: dict[int, frozenset[str]] = {
    # v=111  Overview
    111: frozenset({
        "Company", "Sector", "Country", "Market Cap",
        "P/E", "Price", "Change", "Volume",
    }),
    # v=121  Valuation / Growth
    121: frozenset({
        "Forward P/E", "PEG", "P/S", "P/B",
        "EPS Growth This Year",
        "EPS Growth Next Year",
        "EPS Growth Past 5 Years",
    }),
    # v=131  Ownership / Short Interest
    131: frozenset({
        "Insider Ownership", "Institutional Ownership",
        "Short Float", "Short Ratio", "Average Volume",
    }),
    # v=141  Performance
    141: frozenset({
        "Performance (Week)",
        "Performance (Month)",
        "Performance (Quarter)",
        "Performance (Year)",
        "Performance (YTD)",
        "Relative Volume",
    }),
    # v=161  Financials
    161: frozenset({
        "Dividend Yield",
        "Return on Assets", "Return on Equity",
        "Current Ratio", "Quick Ratio", "LT Debt/Equity",
        "Gross Margin", "Operating Margin", "Profit Margin",
    }),
    # v=171  Technical
    171: frozenset({
        "Beta", "Average True Range",
        "20-Day Simple Moving Average",
        "50-Day Simple Moving Average",
        "200-Day Simple Moving Average",
        "52-Week High", "52-Week Low",
        "Relative Strength Index (14)",
    }),
}

# ---------------------------------------------------------------------------
# Ticker filter sets (shared between bulk and per-ticker code paths)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_market_cap(s: object) -> int | None:
    """
    Convert the Market Cap value from the v=111 export to an integer.

    Finviz's bulk export returns Market Cap as a plain float in millions
    (e.g. 37693.37 for a $37.7 B company).  Strings with B/M/K suffixes
    are also handled in case the format ever changes.
    """
    text = str(s).strip() if s is not None else ""
    if not text or text in _NA:
        return None
    try:
        if text.endswith("B"):
            return int(float(text[:-1]) * 1_000_000_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        return int(float(text.replace(",", "")))
    except (ValueError, TypeError):
        return None


def _parse_avg_volume(s: object) -> int | None:
    """
    Parse Average Volume from v=131 and convert to actual shares.

    The v=131 export reports Average Volume in thousands
    (e.g. 54953.86 for AAPL whose actual avg volume is ~54.9 M shares).
    Multiplying by 1 000 restores the true share count.
    """
    v = _parse_float(s)
    return int(v * 1_000) if v is not None else None


def _parse_float(s: object) -> float | None:
    """Parse a plain numeric string.  Returns None for any N/A sentinel."""
    text = str(s).strip() if s is not None else ""
    if not text or text in _NA:
        return None
    try:
        return float(text.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_pct(s: object) -> float | None:
    """
    Parse a percentage string such as '15.30%' or '-5.23%' and return the
    numeric value (15.30 or -5.23).  Returns None for any N/A sentinel.
    """
    text = str(s).strip() if s is not None else ""
    if not text or text in _NA:
        return None
    try:
        return float(text.replace("%", "").replace(",", ""))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Per-view column validation
# ---------------------------------------------------------------------------


def _validate_view(df: pd.DataFrame, v: int) -> None:
    """
    Check every column listed in _EXPECTED_COLS[v] against the columns that
    are actually present in df.

    Missing columns trigger a WARNING; all-present triggers a DEBUG log.
    This runs immediately after each CSV is fetched so header renames are
    caught before any data is parsed or written.
    """
    expected = _EXPECTED_COLS.get(v, frozenset())
    if not expected:
        return
    actual  = frozenset(df.columns)
    missing = expected - actual
    if missing:
        logger.warning(
            "[finviz] v={v} — {n} expected column(s) not found in CSV: {cols}".format(
                v=v, n=len(missing), cols=", ".join(sorted(missing))
            )
        )
    else:
        logger.debug(
            "[finviz] v={v} — all {n} expected columns present.".format(
                v=v, n=len(expected)
            )
        )


# ---------------------------------------------------------------------------
# Market hours helper (used by scheduler)
# ---------------------------------------------------------------------------


def is_market_hours() -> bool:
    """Return True during NYSE regular trading hours (Mon–Fri 09:30–16:00 ET)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


# ---------------------------------------------------------------------------
# Ingestor
# ---------------------------------------------------------------------------


class FinvizIngestor:
    """
    Fetches Finviz Elite data for all tickers.

    Two code paths:
      fetch_all_prices()  — bulk screener export (6 views merged, all tickers)
      get_quotes_batch()  — per-ticker 1-minute intraday CSV (price only)
    """

    _BULK_VIEWS = [111, 121, 131, 141, 161, 171]
    _BULK_BASE  = "https://elite.finviz.com/export.ashx"
    BASE_URL    = "https://elite.finviz.com/quote_export"

    def __init__(self, auth_token: str) -> None:
        self.auth_token = auth_token
        self._session   = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all_prices(self) -> list[dict]:
        """
        Fetch prices and fundamentals for all tickers via the Finviz Elite
        bulk screener export.

        Ingestion flow
        --------------
        1. Fetch each of the six export views independently via _fetch_view().
           _fetch_view() validates the CSV columns immediately on receipt and
           logs a WARNING for any expected column that is absent.
        2. Abort if v=111 (the base view) is empty — it carries price, volume,
           and the Ticker index that drives the merge.
        3. Left-join each secondary view onto the v=111 base frame.  Columns
           that already exist in the merged frame are skipped so the base
           values for Price, Change, Volume and Market Cap are never overwritten.
           If a secondary view is empty (fetch failed), those columns are NULL
           for every ticker this cycle and a WARNING is emitted.
        4. Iterate the merged frame row-by-row, apply the correct parser to
           each column, and append to the results list.  Rows that raise an
           exception during parsing are skipped silently.

        Fields unavailable in any Finviz bulk export (always NULL)
        -----------------------------------------------------------
        - eps_ttm           (not in any export view)
        - eps_growth_qoq    (only past-5-year EPS growth is available)
        - sales_growth_qoq  (only past-5-year sales growth is available)
        - analyst_recom     (not in any export view)
        - target_price      (not in any export view)
        """
        # Step 1 — fetch and validate each view independently
        frames: dict[int, pd.DataFrame] = {
            v: self._fetch_view(v) for v in self._BULK_VIEWS
        }

        # Step 2 — abort if the base view is empty
        if frames[111].empty:
            logger.warning(
                "[finviz] Base view v=111 returned no data — aborting bulk fetch."
            )
            return []

        # Step 3 — left-join secondary views onto v=111
        merged = frames[111].set_index("Ticker")
        for v in [121, 131, 141, 161, 171]:
            if frames[v].empty:
                logger.warning(
                    f"[finviz] v={v} returned no data — "
                    "affected columns will be NULL this cycle."
                )
                continue
            other = (
                frames[v]
                .drop(columns=list(_SECONDARY_DROP), errors="ignore")
                .set_index("Ticker")
            )
            new_cols = [c for c in other.columns if c not in merged.columns]
            if new_cols:
                merged = merged.join(other[new_cols], how="left")

        logger.info(
            f"[finviz] Merged frame: {len(merged)} tickers "
            f"x {len(merged.columns)} columns."
        )

        # Step 4 — parse each row into a ticker_prices-compatible dict
        results: list[dict] = []
        for ticker, row in merged.iterrows():
            try:
                raw_sector = str(row.get("Sector", "") or "")
                results.append({
                    # ── Core — v=111 (Overview) ───────────────────────────
                    # CSV cols: Company, Sector, Country, Market Cap,
                    #           P/E, Price, Change, Volume
                    "ticker":            str(ticker),
                    "company_name":      str(row.get("Company", "") or "") or None,
                    "price":             float(row["Price"]),
                    "change_pct":        float(str(row["Change"]).replace("%", "")),
                    "volume":            int(row["Volume"]),
                    "market_cap":        _parse_market_cap(row.get("Market Cap")),
                    "sector":            _SECTOR_NORMALIZE.get(raw_sector, raw_sector) or None,
                    "country":           str(row.get("Country", "") or "") or None,
                    "exchange":          str(row.get("Exchange", "") or "") or None,
                    "pre_market_price":  None,
                    "post_market_price": None,
                    "updated_at":        datetime.now(_ET),

                    # ── Valuation / Growth — v=111 + v=121 ───────────────
                    # v=111 CSV col: "P/E"
                    # v=121 CSV cols: "Forward P/E", "PEG", "P/S", "P/B",
                    #   "EPS Growth This Year", "EPS Growth Next Year",
                    #   "EPS Growth Past 5 Years"
                    "pe_ratio":             _parse_float(row.get("P/E")),
                    "forward_pe":           _parse_float(row.get("Forward P/E")),
                    "peg_ratio":            _parse_float(row.get("PEG")),
                    "price_to_sales":       _parse_float(row.get("P/S")),
                    "price_to_book":        _parse_float(row.get("P/B")),
                    "eps_growth_this_year": _parse_pct(row.get("EPS Growth This Year")),
                    "eps_growth_next_year": _parse_pct(row.get("EPS Growth Next Year")),
                    "eps_growth_5y":        _parse_pct(row.get("EPS Growth Past 5 Years")),

                    # ── Financials — v=161 ────────────────────────────────
                    # CSV cols: "Dividend Yield", "Return on Assets",
                    #   "Return on Equity", "Current Ratio", "Quick Ratio",
                    #   "LT Debt/Equity", "Gross Margin", "Operating Margin",
                    #   "Profit Margin"
                    "dividend_yield":   _parse_pct(row.get("Dividend Yield")),
                    "roe":              _parse_pct(row.get("Return on Equity")),
                    "roa":              _parse_pct(row.get("Return on Assets")),
                    "debt_to_equity":   _parse_float(row.get("LT Debt/Equity")),
                    "current_ratio":    _parse_float(row.get("Current Ratio")),
                    "quick_ratio":      _parse_float(row.get("Quick Ratio")),
                    "gross_margin":     _parse_pct(row.get("Gross Margin")),
                    "operating_margin": _parse_pct(row.get("Operating Margin")),
                    "net_margin":       _parse_pct(row.get("Profit Margin")),

                    # ── Ownership / Short Interest — v=131 ────────────────
                    # CSV cols: "Insider Ownership", "Institutional Ownership",
                    #   "Short Float", "Short Ratio",
                    #   "Average Volume"  (reported in thousands → ×1 000)
                    "insider_own":  _parse_pct(row.get("Insider Ownership")),
                    "inst_own":     _parse_pct(row.get("Institutional Ownership")),
                    "float_short":  _parse_pct(row.get("Short Float")),
                    "short_ratio":  _parse_float(row.get("Short Ratio")),
                    "avg_volume":   _parse_avg_volume(row.get("Average Volume")),

                    # ── Performance — v=141 ───────────────────────────────
                    # CSV cols: "Performance (Week)", "Performance (Month)",
                    #   "Performance (Quarter)", "Performance (Year)",
                    #   "Performance (YTD)", "Relative Volume"
                    "perf_week":  _parse_pct(row.get("Performance (Week)")),
                    "perf_month": _parse_pct(row.get("Performance (Month)")),
                    "perf_quart": _parse_pct(row.get("Performance (Quarter)")),
                    "perf_year":  _parse_pct(row.get("Performance (Year)")),
                    "perf_ytd":   _parse_pct(row.get("Performance (YTD)")),
                    "rel_volume": _parse_float(row.get("Relative Volume")),

                    # ── Technical — v=171 ─────────────────────────────────
                    # CSV cols: "Beta", "Average True Range",
                    #   "20-Day Simple Moving Average",
                    #   "50-Day Simple Moving Average",
                    #   "200-Day Simple Moving Average",
                    #   "52-Week High", "52-Week Low",
                    #   "Relative Strength Index (14)"
                    "beta":             _parse_float(row.get("Beta")),
                    "atr":              _parse_float(row.get("Average True Range")),
                    "sma_20_pct":       _parse_pct(row.get("20-Day Simple Moving Average")),
                    "sma_50_pct":       _parse_pct(row.get("50-Day Simple Moving Average")),
                    "sma_200_pct":      _parse_pct(row.get("200-Day Simple Moving Average")),
                    "week_52_high_pct": _parse_pct(row.get("52-Week High")),
                    "week_52_low_pct":  _parse_pct(row.get("52-Week Low")),
                    "rsi_14":           _parse_float(row.get("Relative Strength Index (14)")),

                    # ── Not available in any Finviz bulk export ───────────
                    "eps_ttm":          None,
                    "eps_growth_qoq":   None,
                    "sales_growth_qoq": None,
                    "analyst_recom":    None,
                    "target_price":     None,
                })
            except Exception:
                continue

        logger.info(f"[finviz] Parsed {len(results)} ticker rows for upsert.")
        return results

    def get_quotes_batch(self, tickers: list[str]) -> list[dict]:
        """
        Fetch 1-minute intraday prices for a list of tickers.
        Returns one price dict per success; skips invalid and blocked tickers.
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

    def _fetch_view(self, v: int) -> pd.DataFrame:
        """
        Fetch a single Finviz screener export view, validate its columns,
        and return the parsed DataFrame.  Returns an empty DataFrame on failure.

        Column validation runs immediately after the CSV is parsed so that
        any header rename is surfaced before the merge and upsert steps.
        """
        url = f"{self._BULK_BASE}?v={v}&auth={self.auth_token}"
        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(StringIO(resp.text))
            if df.empty:
                logger.warning(f"[finviz] v={v}: CSV parsed but is empty.")
                return pd.DataFrame()
            _validate_view(df, v)
            logger.debug(f"[finviz] v={v}: fetched {len(df)} rows.")
            return df
        except Exception as exc:
            logger.warning(f"[finviz] v={v}: fetch failed — {exc}")
            return pd.DataFrame()

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
