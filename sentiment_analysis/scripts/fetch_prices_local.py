"""
Local Finviz price fetcher.

Connects to Railway PostgreSQL using RAILWAY_DATABASE_URL and fetches
intraday prices from Finviz Elite every 60 seconds, upserting results
into the ticker_prices table.

Run from project root:
    venv\\Scripts\\python sentiment_analysis/scripts/fetch_prices_local.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────

_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_PATH)

RAILWAY_DATABASE_URL: str = os.getenv("RAILWAY_DATABASE_URL", "").strip()
FINVIZ_TOKEN:         str = os.getenv("FINVIZ_TOKEN", "").strip()
POLL_INTERVAL:        int = 60  # seconds between full runs

FINVIZ_BASE = "https://elite.finviz.com/quote_export"

# Single-letter tickers and known SEC filing artifacts.
_INVALID_TICKERS: frozenset[str] = frozenset({
    "K", "C", "A", "T", "F", "M", "R", "L", "V", "D", "W", "N", "X", "S",
    "O", "E", "H", "G", "B", "P", "I", "J", "Q", "U", "Y", "Z",
})

# Runtime skip list — blocked after 3 consecutive failures.
_SKIP_TICKERS: set[str]       = set()
_FAIL_COUNTS:  dict[str, int] = {}
_SKIP_AFTER = 3

# ── SQL ───────────────────────────────────────────────────────────────────

_TICKER_QUERY = """
    SELECT DISTINCT unnest(tickers) AS ticker
    FROM rss_articles
    WHERE ingested_at > NOW() - INTERVAL '24 hours'
      AND tickers IS NOT NULL
      AND array_length(tickers, 1) > 0
"""

_UPSERT_SQL = """
    INSERT INTO ticker_prices
        (ticker, price, change_pct, volume,
         market_cap, pre_market_price, post_market_price, updated_at)
    VALUES
        (%(ticker)s, %(price)s, %(change_pct)s, %(volume)s,
         %(market_cap)s, %(pre_market_price)s, %(post_market_price)s, %(updated_at)s)
    ON CONFLICT (ticker) DO UPDATE SET
        price             = EXCLUDED.price,
        change_pct        = EXCLUDED.change_pct,
        volume            = EXCLUDED.volume,
        market_cap        = EXCLUDED.market_cap,
        pre_market_price  = EXCLUDED.pre_market_price,
        post_market_price = EXCLUDED.post_market_price,
        updated_at        = EXCLUDED.updated_at
"""

# ── Database ──────────────────────────────────────────────────────────────

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(dsn=RAILWAY_DATABASE_URL)


def _fetch_tickers(conn: psycopg2.extensions.connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(_TICKER_QUERY)
        rows = cur.fetchall()
    all_tickers = [row[0] for row in rows if row[0]][:200]
    return [
        t for t in all_tickers
        if len(t) >= 2
        and t not in _INVALID_TICKERS
        and t not in _SKIP_TICKERS
    ]


def _upsert_prices(
    conn: psycopg2.extensions.connection, rows: list[dict]
) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, _UPSERT_SQL, rows)
    conn.commit()

# ── Finviz ────────────────────────────────────────────────────────────────

def _get_quote(session: requests.Session, ticker: str) -> dict | None:
    url = f"{FINVIZ_BASE}?t={ticker}&p=i1&auth={FINVIZ_TOKEN}"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            return _record_failure(ticker, "empty CSV")

        last  = df.iloc[-1]
        first = df.iloc[0]

        close_price = float(last.get("Close", 0) or 0)
        if close_price == 0:
            return _record_failure(ticker, "zero close price")

        open_price = float(first.get("Open", close_price) or close_price)
        change_pct = (
            (close_price - open_price) / open_price * 100
            if open_price > 0 else 0.0
        )

        _FAIL_COUNTS.pop(ticker, None)
        return {
            "ticker":             ticker,
            "price":              round(close_price, 4),
            "change_pct":         round(change_pct, 2),
            "volume":             int(last.get("Volume", 0) or 0),
            "market_cap":         None,
            "pre_market_price":   None,
            "post_market_price":  None,
            "updated_at":         datetime.now(timezone.utc),
        }

    except requests.HTTPError as exc:
        return _record_failure(ticker, f"HTTP {exc.response.status_code}")
    except Exception as exc:
        return _record_failure(ticker, str(exc))


def _record_failure(ticker: str, reason: str) -> None:
    _FAIL_COUNTS[ticker] = _FAIL_COUNTS.get(ticker, 0) + 1
    if _FAIL_COUNTS[ticker] >= _SKIP_AFTER:
        _SKIP_TICKERS.add(ticker)
        print(f"  [skip] {ticker}: blocked after {_SKIP_AFTER} failures ({reason})")
    else:
        print(f"  [warn] {ticker}: {reason}")
    return None

# ── Main loop ─────────────────────────────────────────────────────────────

def _run_once(
    conn: psycopg2.extensions.connection, session: requests.Session
) -> None:
    tickers = _fetch_tickers(conn)
    print(f"[local-finviz] Found {len(tickers)} tickers — fetching prices...")

    results: list[dict] = []
    for ticker in tickers:
        row = _get_quote(session, ticker)
        if row:
            results.append(row)
        time.sleep(1.5)

    if results:
        _upsert_prices(conn, results)

    print(
        f"[local-finviz] Fetched {len(results)}/{len(tickers)} tickers "
        f"— next run in {POLL_INTERVAL}s"
    )


def main() -> None:
    if not RAILWAY_DATABASE_URL:
        print(f"ERROR: RAILWAY_DATABASE_URL not set in {_ENV_PATH}")
        sys.exit(1)
    if not FINVIZ_TOKEN:
        print(f"ERROR: FINVIZ_TOKEN not set in {_ENV_PATH}")
        sys.exit(1)

    print(f"[local-finviz] Starting  —  poll every {POLL_INTERVAL}s")
    print(f"[local-finviz] Loaded .env from {_ENV_PATH}")
    print("[local-finviz] Press Ctrl+C to stop.\n")

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })

    conn = _connect()

    try:
        while True:
            try:
                _run_once(conn, session)
            except psycopg2.OperationalError as exc:
                print(f"[local-finviz] DB connection lost ({exc}) — reconnecting...")
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _connect()
            except Exception as exc:
                print(f"[local-finviz] Run error: {exc}")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[local-finviz] Stopped.")
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
