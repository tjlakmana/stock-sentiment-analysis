"""
Screener page — Finviz-style dense ticker table with real-time price data.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import pytz
import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from loguru import logger

from sentiment_analysis.dashboard.db import (
    now_et,
    query_df,
    watchlist_add,
    watchlist_remove,
    watchlist_tickers,
)
from sentiment_analysis.ingestion.finviz_ingestor import is_market_hours

dash.register_page(__name__, path="/screener", name="Screener", title="Screener")

PAGE_SIZE = 25

# Country abbreviations for the display column.
# Keys are country strings stored in ticker_prices.country (from Finviz).
# Filters use the raw DB value directly; this dict is display-only.
_COUNTRY_DISPLAY: dict[str, str] = {
    "USA":           "USA", "United States":  "USA",
    "China":         "CN",  "United Kingdom": "UK",
    "Canada":        "CA",  "Netherlands":    "NL",
    "Taiwan":        "TW",  "Singapore":      "SG",
    "Argentina":     "AR",  "Brazil":         "BR",
    "Denmark":       "DK",  "France":         "FR",
    "Germany":       "DE",  "Switzerland":    "CH",
    "Australia":     "AU",  "Ireland":        "IE",
    "Japan":         "JP",  "South Korea":    "KR",
    "India":         "IN",  "Israel":         "IL",
    "Sweden":        "SE",  "Hong Kong":      "HK",
    "Mexico":        "MX",  "South Africa":   "ZA",
    "Belgium":       "BE",  "Spain":          "ES",
    "Italy":         "IT",
}

# ── Options ────────────────────────────────────────────────────────────────

ORDER_OPTIONS = [
    {"label": "Market Cap", "value": "market_cap"},
    {"label": "P/E Ratio",  "value": "pe_ratio"},
    {"label": "Price",      "value": "price"},
    {"label": "Change %",   "value": "change_pct"},
    {"label": "Volume",     "value": "volume"},
]

SECTOR_OPTIONS = [
    {"label": "Any Sector",    "value": "all"},
    {"label": "Technology",    "value": "Technology"},
    {"label": "Healthcare",    "value": "Healthcare"},
    {"label": "Finance",       "value": "Finance"},
    {"label": "Energy",        "value": "Energy"},
    {"label": "Consumer",      "value": "Consumer"},
    {"label": "Industrial",    "value": "Industrial"},
    {"label": "Materials",     "value": "Materials"},
    {"label": "Utilities",     "value": "Utilities"},
    {"label": "Real Estate",   "value": "Real Estate"},
    {"label": "Communication", "value": "Communication"},
]

MKTCAP_OPTIONS = [
    {"label": "Any",                "value": "all"},
    {"label": "Mega  (>$200B)",     "value": "mega"},
    {"label": "Large  ($10B–$200B)","value": "large"},
    {"label": "Mid  ($2B–$10B)",    "value": "mid"},
    {"label": "Small  ($200M–$2B)", "value": "small"},
    {"label": "Micro  (<$200M)",    "value": "micro"},
]

COUNTRY_OPTIONS = [
    {"label": "Any Country", "value": "all"},
    {"label": "USA",         "value": "USA"},
    {"label": "China",       "value": "China"},
    {"label": "UK",          "value": "UK"},
    {"label": "Canada",      "value": "Canada"},
]

PRICE_OPTIONS = [
    {"label": "Any Price",   "value": "all"},
    {"label": "Under $5",    "value": "u5"},
    {"label": "$5 – $20",    "value": "5_20"},
    {"label": "$20 – $50",   "value": "20_50"},
    {"label": "$50 – $100",  "value": "50_100"},
    {"label": "$100 – $500", "value": "100_500"},
    {"label": "Over $500",   "value": "o500"},
]

CHG_OPTIONS = [
    {"label": "Any Change",  "value": "all"},
    {"label": "Up 5%+",      "value": "up5"},
    {"label": "Up 2–5%",     "value": "up2_5"},
    {"label": "Up 0–2%",     "value": "up0_2"},
    {"label": "Down 0–2%",   "value": "dn0_2"},
    {"label": "Down 2–5%",   "value": "dn2_5"},
    {"label": "Down 5%+",    "value": "dn5"},
]

VOLUME_OPTIONS = [
    {"label": "Any Volume",  "value": "all"},
    {"label": "Under 500K",  "value": "u500k"},
    {"label": "500K – 1M",   "value": "500k_1m"},
    {"label": "1M – 5M",     "value": "1m_5m"},
    {"label": "Over 5M",     "value": "o5m"},
]

AVG_VOL_OPTIONS = [
    {"label": "Any Avg Vol", "value": "all"},
    {"label": "Under 1M",    "value": "u1m"},
    {"label": "1M – 5M",     "value": "1m_5m"},
    {"label": "Over 5M",     "value": "o5m"},
]

INDUSTRY_OPTIONS = [
    {"label": "Any Industry", "value": "all"},
]

EXCHANGE_OPTIONS = [
    {"label": "Any Exchange", "value": "all"},
    {"label": "NYSE",         "value": "NYSE"},
    {"label": "NASDAQ",       "value": "NASDAQ"},
    {"label": "AMEX",         "value": "AMEX"},
]

NEWS_TIME_OPTIONS = [
    {"label": "Any Time",                "value": "any"},
    {"label": "Today",                   "value": "today"},
    {"label": "Today (After Market)",    "value": "today_aft"},
    {"label": "Since Yesterday",         "value": "since_yesterday"},
    {"label": "Since Yesterday's Close", "value": "since_yest_close"},
    {"label": "Yesterday",               "value": "yesterday"},
    {"label": "Yesterday (After Market)","value": "yesterday_aft"},
    {"label": "Last 5 Minutes",          "value": "5min"},
    {"label": "Last 30 Minutes",         "value": "30min"},
    {"label": "Last Hour",               "value": "1hr"},
    {"label": "Last 24 Hours",           "value": "24hr"},
    {"label": "Last Month",              "value": "1mo"},
    {"label": "Custom...",               "value": "custom"},
]

_NEWS_CAT_LABELS = [
    ("breaking",   "Breaking News"),
    ("earnings",   "Earnings"),
    ("sec",        "SEC Filings"),
    ("press",      "Press Releases"),
    ("fda",        "FDA"),
    ("insider",    "Insider Trading"),
    ("upgrade",    "Analyst Upgrades"),
    ("downgrade",  "Analyst Downgrades"),
    ("ma",         "M&A"),
    ("ipo",        "IPO"),
    ("macro",      "Macro"),
    ("crypto",     "Crypto"),
]

_NEWS_CAT_KEYWORDS: dict[str, str] = {
    "breaking":  "breaking",
    "earnings":  "earnings",
    "sec":       "SEC filing",
    "press":     "press release",
    "fda":       "FDA",
    "insider":   "insider",
    "upgrade":   "upgrade",
    "downgrade": "downgrade",
    "ma":        "acquisition",
    "ipo":       "IPO",
    "macro":     "federal reserve",
    "crypto":    "crypto",
}

# ── SQL ───────────────────────────────────────────────────────────────────

_SCREENER_SQL = """
    SELECT
        ticker,
        company_name,
        sector,
        country,
        price,
        change_pct,
        volume,
        market_cap,
        pre_market_price,
        post_market_price,
        pe_ratio,
        forward_pe,
        peg_ratio,
        price_to_sales,
        price_to_book,
        dividend_yield,
        eps_ttm,
        roe,
        debt_to_equity,
        current_ratio,
        quick_ratio,
        avg_volume,
        rel_volume,
        rsi_14,
        sma_20_pct,
        sma_50_pct,
        sma_200_pct,
        week_52_high_pct,
        week_52_low_pct,
        roa,
        gross_margin,
        operating_margin,
        net_margin,
        beta,
        float_short,
        short_ratio,
        perf_week,
        perf_month,
        perf_quart,
        perf_year,
        perf_ytd,
        analyst_recom,
        target_price,
        eps_growth_this_year,
        eps_growth_next_year,
        eps_growth_5y,
        eps_growth_qoq,
        sales_growth_qoq,
        atr,
        insider_own,
        inst_own
    FROM ticker_prices
"""


def _fetch_data() -> pd.DataFrame:
    df = query_df(_SCREENER_SQL)
    logger.info(f"[screener] _fetch_data → {len(df)} rows")
    return df


# ── Render helpers ────────────────────────────────────────────────────────


def _safe(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fmt_price(v) -> str:
    v = _safe(v)
    return f"${v:,.2f}" if v is not None else "—"


def _fmt_chg(v) -> tuple[str, str]:
    v = _safe(v)
    if v is None:
        return "—", "#888888"
    color = "#00e676" if v >= 0 else "#ff5252"
    sign  = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%", color


def _fmt_volume(v) -> str:
    v = _safe(v)
    if v is None:
        return "—"
    if v >= 1e9:  return f"{v/1e9:.2f}B"
    if v >= 1e6:  return f"{v/1e6:.2f}M"
    if v >= 1e3:  return f"{v/1e3:.1f}K"
    return str(int(v))


def _fmt_mktcap(v) -> str:
    v = _safe(v)
    if v is None:
        return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _fmt_pe(v) -> str:
    v = _safe(v)
    if v is None or v <= 0:
        return "—"
    return f"{v:.2f}"


# ── Table column definitions ──────────────────────────────────────────────
# (label, width, text-align)
_OV_COLS = [
    ("★",       "32px",  "center"),
    ("#",       "35px",  "right"),
    ("Ticker",  "72px",  "left"),
    ("Company", "200px", "left"),
    ("Sector",  "140px", "left"),
    ("Country", "64px",  "center"),
    ("Mkt Cap", "100px", "right"),
    ("P/E",     "64px",  "right"),
    ("Price",   "90px",  "right"),
    ("Chg %",   "76px",  "right"),
    ("Volume",  "100px", "right"),
]

# ── Table renderers ───────────────────────────────────────────────────────

def _no_results(colspan: int) -> list:
    return [html.Tr([html.Td(
        "No tickers match current filters.",
        colSpan=colspan,
        style={"padding": "32px", "textAlign": "center",
               "color": "#555", "fontSize": "14px"},
    )])]


def _render_overview_rows(df: pd.DataFrame, page: int,
                          watched: set[str] | None = None) -> list:
    if df.empty:
        return _no_results(len(_OV_COLS))

    watched  = watched or set()
    offset   = (page - 1) * PAGE_SIZE
    page_df  = df.iloc[offset : offset + PAGE_SIZE]
    rows: list = []

    for local_i, (_, r) in enumerate(page_df.iterrows()):
        global_i   = offset + local_i
        ticker     = r["ticker"]
        is_watched = ticker in watched
        company    = str(r.get("company_name") or "") or ticker
        sector     = str(r.get("sector") or "") or "—"
        raw_ctry   = str(r.get("country") or "USA")
        country    = _COUNTRY_DISPLAY.get(
            raw_ctry,
            raw_ctry[:3].upper() if len(raw_ctry) > 3 else raw_ctry,
        )
        chg_text, chg_color = _fmt_chg(r.get("change_pct"))

        price_children: list = [
            html.Span(_fmt_price(r.get("price")),
                      style={"fontSize": "12px", "fontWeight": "600", "color": "#e0e0e0"}),
        ]
        if not is_market_hours():
            pre  = _safe(r.get("pre_market_price"))
            post = _safe(r.get("post_market_price"))
            ext  = pre or post
            lbl  = "Pre" if pre else ("Post" if post else None)
            if ext and lbl:
                price_children.append(
                    html.Span(f"{lbl}: {_fmt_price(ext)}",
                              style={"fontSize": "10px", "color": "#666",
                                     "display": "block", "lineHeight": "1.1"}),
                )

        rows.append(html.Tr([
            html.Td(
                html.Button(
                    "⭐" if is_watched else "☆",
                    id={"type": "scr-star", "ticker": ticker},
                    className="scr-star-btn" + (" scr-star-active" if is_watched else ""),
                    n_clicks=0,
                    title=f"{'Remove from' if is_watched else 'Add to'} watchlist",
                ),
                className="scr-td scr-td-center",
            ),
            html.Td(str(global_i + 1), className="scr-td scr-td-num scr-dim"),
            html.Td(
                dcc.Link(ticker, href=f"/stock/{ticker}", className="scr-ticker"),
                className="scr-td",
            ),
            html.Td(company, className="scr-td scr-company"),
            html.Td(sector,  className="scr-td scr-dim", style={"fontSize": "12px"}),
            html.Td(country, className="scr-td scr-td-center scr-dim"),
            html.Td(_fmt_mktcap(r.get("market_cap")), className="scr-td scr-td-num"),
            html.Td(_fmt_pe(r.get("pe_ratio")),       className="scr-td scr-td-num scr-dim"),
            html.Td(price_children, className="scr-td scr-td-num"),
            html.Td(chg_text, className="scr-td scr-td-num",
                    style={"color": chg_color}),
            html.Td(_fmt_volume(r.get("volume")), className="scr-td scr-td-num"),
        ], className="scr-tr"))
    return rows


# ── Pagination ────────────────────────────────────────────────────────────

def _render_pagination(current_page: int, total_pages: int) -> list:
    if total_pages <= 1:
        return []

    items: list = [
        html.Span(
            f"Page {current_page} of {total_pages}",
            style={"fontSize": "11px", "color": "#555", "marginRight": "4px",
                   "whiteSpace": "nowrap"},
        ),
        html.Button(
            "← Prev",
            id={"type": "scr-pgbtn", "page": "prev"},
            className="page-btn",
            disabled=current_page <= 1,
            n_clicks=0,
        ),
    ]

    n       = total_pages
    start_p = max(1, min(current_page - 2, n - 4))
    end_p   = min(n, start_p + 4)

    for p in range(start_p, end_p + 1):
        items.append(html.Button(
            str(p),
            id={"type": "scr-pgbtn", "page": p},
            className="page-btn page-btn-active" if p == current_page else "page-btn",
            n_clicks=0,
        ))

    items.append(html.Button(
        "Next →",
        id={"type": "scr-pgbtn", "page": "next"},
        className="page-btn",
        disabled=current_page >= total_pages,
        n_clicks=0,
    ))
    return items


# ── Filter / sort helpers ─────────────────────────────────────────────────

def _apply_sector_filter(df: pd.DataFrame, sector: str) -> pd.DataFrame:
    if not sector or sector == "all" or "sector" not in df.columns:
        return df
    return df[df["sector"].fillna("") == sector]


def _apply_country_filter(df: pd.DataFrame, country: str) -> pd.DataFrame:
    if not country or country == "all" or "country" not in df.columns:
        return df
    return df[df["country"].fillna("USA") == country]


def _apply_mktcap_filter(df: pd.DataFrame, mktcap: str) -> pd.DataFrame:
    mc = df["market_cap"].fillna(0)
    if mktcap == "mega":
        return df[mc > 200_000_000_000]
    if mktcap == "large":
        return df[(mc >= 10_000_000_000) & (mc <= 200_000_000_000)]
    if mktcap == "mid":
        return df[(mc >= 2_000_000_000) & (mc < 10_000_000_000)]
    if mktcap == "small":
        return df[(mc >= 200_000_000) & (mc < 2_000_000_000)]
    if mktcap == "micro":
        return df[(mc > 0) & (mc < 200_000_000)]
    return df


def _apply_price_filter(df: pd.DataFrame, price: str) -> pd.DataFrame:
    if not price or price == "all":
        return df
    v = df["price"].fillna(0)
    masks = {
        "u5":      v < 5,
        "5_20":    (v >= 5)   & (v < 20),
        "20_50":   (v >= 20)  & (v < 50),
        "50_100":  (v >= 50)  & (v < 100),
        "100_500": (v >= 100) & (v < 500),
        "o500":    v >= 500,
    }
    mask = masks.get(price)
    return df[mask] if mask is not None else df


def _apply_chg_pct_filter(df: pd.DataFrame, chg: str) -> pd.DataFrame:
    if not chg or chg == "all":
        return df
    v = df["change_pct"].fillna(0)
    masks = {
        "up5":   v >= 5,
        "up2_5": (v >= 2)  & (v < 5),
        "up0_2": (v >= 0)  & (v < 2),
        "dn0_2": (v > -2)  & (v < 0),
        "dn2_5": (v >= -5) & (v < -2),
        "dn5":   v <= -5,
    }
    mask = masks.get(chg)
    return df[mask] if mask is not None else df


def _apply_volume_filter(df: pd.DataFrame, vol: str) -> pd.DataFrame:
    if not vol or vol == "all":
        return df
    v = df["volume"].fillna(0)
    masks = {
        "u500k":   v < 500_000,
        "500k_1m": (v >= 500_000)   & (v < 1_000_000),
        "1m_5m":   (v >= 1_000_000) & (v < 5_000_000),
        "o5m":     v >= 5_000_000,
    }
    mask = masks.get(vol)
    return df[mask] if mask is not None else df


def _apply_avg_volume_filter(df: pd.DataFrame, avg_vol: str) -> pd.DataFrame:
    if not avg_vol or avg_vol == "all" or "avg_volume" not in df.columns:
        return df
    v = df["avg_volume"].fillna(0)
    masks = {
        "u1m":   v < 1_000_000,
        "1m_5m": (v >= 1_000_000) & (v < 5_000_000),
        "o5m":   v >= 5_000_000,
    }
    mask = masks.get(avg_vol)
    return df[mask] if mask is not None else df


def _apply_range_filter(
    df: pd.DataFrame,
    col: str,
    min_val,
    max_val,
    scale: float = 1.0,
) -> pd.DataFrame:
    """Filter df so col >= min_val and col <= max_val (either can be None = no bound).
    scale multiplies the user-entered values before comparison (e.g. M → raw for volume)."""
    if col not in df.columns:
        return df
    v = pd.to_numeric(df[col], errors="coerce")
    if min_val is not None:
        try:
            df = df[v >= float(min_val) * scale]
            v = pd.to_numeric(df[col], errors="coerce")
        except (TypeError, ValueError):
            pass
    if max_val is not None:
        try:
            df = df[v <= float(max_val) * scale]
        except (TypeError, ValueError):
            pass
    return df


def _apply_dd_filter(
    df: pd.DataFrame,
    col: str,
    val: str | None,
    range_map: dict,
) -> pd.DataFrame:
    """Apply a dropdown-based range filter using a predefined (lo, hi) map."""
    if not val or val == "any" or val not in range_map:
        return df
    lo, hi = range_map[val]
    return _apply_range_filter(df, col, lo, hi)


_ET = pytz.timezone("America/New_York")


def _time_bounds(value: str) -> tuple[datetime, datetime] | None:
    now = datetime.now(_ET)
    today_4am  = now.replace(hour=4,  minute=0, second=0, microsecond=0)
    today_4pm  = now.replace(hour=16, minute=0, second=0, microsecond=0)
    yest       = now - timedelta(days=1)
    yest_4am   = yest.replace(hour=4,  minute=0, second=0, microsecond=0)
    yest_4pm   = yest.replace(hour=16, minute=0, second=0, microsecond=0)
    yest_start = yest.replace(hour=0,  minute=0, second=0, microsecond=0)
    bounds: dict[str, tuple[datetime, datetime]] = {
        "today":            (today_4am, now),
        "today_aft":        (today_4pm, now),
        "since_yesterday":  (yest_start, now),
        "since_yest_close": (yest_4pm,  now),
        "yesterday":        (yest_4am,  today_4am),
        "yesterday_aft":    (yest_4pm,  today_4am),
        "5min":             (now - timedelta(minutes=5),  now),
        "30min":            (now - timedelta(minutes=30), now),
        "1hr":              (now - timedelta(hours=1),    now),
        "24hr":             (now - timedelta(hours=24),   now),
        "1mo":              (now - timedelta(days=30),    now),
    }
    return bounds.get(value)


def _fetch_news_tickers(
    time_val: str,
    keywords_raw: str,
    custom_state: dict,
) -> set[str] | None:
    """Returns set of primary_ticker values matching the news filter, or None (no filter)."""
    cs = custom_state or {}
    has_cats = bool(cs.get("cats"))
    has_dates = bool(cs.get("date_from") or cs.get("date_to"))
    has_kw = bool(keywords_raw and keywords_raw.strip())
    has_time = bool(time_val and time_val not in ("any", "custom"))

    if not has_time and not has_kw and not has_cats and not has_dates:
        return None

    conditions: list[str] = ["primary_ticker IS NOT NULL"]
    params: dict = {}

    if time_val == "custom":
        if cs.get("date_from"):
            conditions.append("published_at >= :news_start")
            params["news_start"] = cs["date_from"]
        if cs.get("date_to"):
            conditions.append("published_at < :news_end")
            try:
                end_d = datetime.strptime(cs["date_to"], "%Y-%m-%d").date()
                params["news_end"] = (datetime.combine(end_d, datetime.min.time())
                                      + timedelta(days=1)).isoformat()
            except ValueError:
                params["news_end"] = cs["date_to"]
    elif has_time:
        bounds = _time_bounds(time_val)
        if bounds:
            start, end = bounds
            conditions.append("published_at >= :news_start AND published_at <= :news_end")
            params["news_start"] = start.isoformat()
            params["news_end"]   = end.isoformat()

    if has_kw:
        kw = keywords_raw.strip()
        kw_safe = re.sub(r"[%_]", r"\\\g<0>", kw)
        conditions.append("(title ILIKE :kw OR summary ILIKE :kw)")
        params["kw"] = f"%{kw_safe}%"

    if has_cats:
        cat_parts: list[str] = []
        for i, cat in enumerate(cs["cats"]):
            kw = _NEWS_CAT_KEYWORDS.get(cat)
            if kw:
                pkey = f"cat_kw_{i}"
                cat_parts.append(f"(title ILIKE :{pkey} OR summary ILIKE :{pkey})")
                params[pkey] = f"%{kw}%"
        if cat_parts:
            conditions.append(f"({' OR '.join(cat_parts)})")

    where = " AND ".join(conditions)
    sql = f"SELECT DISTINCT primary_ticker FROM rss_articles WHERE {where}"
    try:
        df = query_df(sql, params)
        return set(df["primary_ticker"].dropna().str.upper())
    except Exception:
        logger.exception("[screener] _fetch_news_tickers failed")
        return None


def _apply_sort(df: pd.DataFrame, order: str, sort_dir: str) -> pd.DataFrame:
    col_map = {
        "price":      "price",
        "change_pct": "change_pct",
        "volume":     "volume",
        "market_cap": "market_cap",
        "pe_ratio":   "pe_ratio",
    }
    col = col_map.get(order, "market_cap")
    if col in df.columns:
        return df.sort_values(col, ascending=(sort_dir == "asc"), na_position="last")
    return df


# ── Layout constants ──────────────────────────────────────────────────────

_SH = {"height": "36px"}

_SORT_BTN_STYLE = {
    "background":   "#1a1a1a",
    "color":        "#888888",
    "border":       "1px solid #2a2a2a",
    "borderRadius": "4px",
    "height":       "36px",
    "width":        "36px",
    "fontSize":     "16px",
    "cursor":       "pointer",
    "fontFamily":   "inherit",
    "flexShrink":   "0",
}

_REFRESH_BTN_STYLE = {
    **_SORT_BTN_STYLE,
    "width": "40px",
    "color": "#00d4ff",
}

_RESET_BTN_STYLE = {
    "background":   "transparent",
    "border":       "1px solid #2e2e2e",
    "color":        "#666",
    "fontSize":     "12px",
    "padding":      "5px 14px",
    "borderRadius": "4px",
    "cursor":       "pointer",
    "fontFamily":   "inherit",
}

_FTAB_IDS    = ["desc", "fund", "tech", "news"]
_FTAB_LABELS = ["Descriptive", "Fundamental", "Technical", "News"]


def _fi(label: str, select_id: str, options: list, value) -> html.Div:
    return html.Div([
        html.Label(label, className="filter-label"),
        dbc.Select(id=select_id, options=options, value=value,
                   className="filter-select", style={**_SH}),
    ], className="filter-item")


# ── Screener dropdown option sets & range maps ────────────────────────────

def _dd(label: str, value: str, disabled: bool = False) -> dict:
    d: dict = {"label": label, "value": value}
    if disabled:
        d["disabled"] = True
    return d

def _soon(*rows: tuple[str, str]) -> list[dict]:
    """Option list where all non-Any entries are disabled (backend pending)."""
    return [_dd("Any", "any")] + [_dd(l, v, disabled=True) for l, v in rows]

# P/E Ratio (and Forward P/E share the same options/map)
_OPT_PE: list[dict] = [
    _dd("Any","any"), _dd("Under 5","u5"), _dd("Under 10","u10"),
    _dd("Under 15","u15"), _dd("Under 20","u20"), _dd("Under 30","u30"),
    _dd("Under 50","u50"), _dd("Over 5","o5"), _dd("Over 10","o10"),
    _dd("Over 20","o20"), _dd("Over 30","o30"),
    _dd("5 – 10","5t10"), _dd("10 – 20","10t20"),
    _dd("20 – 30","20t30"), _dd("30 – 50","30t50"), _dd("50 +","50p"),
]
_MAP_PE: dict = {
    "u5":(None,5),"u10":(None,10),"u15":(None,15),"u20":(None,20),
    "u30":(None,30),"u50":(None,50),
    "o5":(5,None),"o10":(10,None),"o20":(20,None),"o30":(30,None),
    "5t10":(5,10),"10t20":(10,20),"20t30":(20,30),"30t50":(30,50),"50p":(50,None),
}

_OPT_PEG: list[dict] = [
    _dd("Any","any"), _dd("Under 0.5","u05"), _dd("Under 1","u1"),
    _dd("Under 2","u2"), _dd("Under 3","u3"),
    _dd("1 – 2","1t2"), _dd("2 – 3","2t3"),
    _dd("Over 1","o1"), _dd("Over 2","o2"), _dd("Over 3","o3"),
]
_MAP_PEG: dict = {
    "u05":(None,.5),"u1":(None,1),"u2":(None,2),"u3":(None,3),
    "1t2":(1,2),"2t3":(2,3),"o1":(1,None),"o2":(2,None),"o3":(3,None),
}

_OPT_PS: list[dict] = [
    _dd("Any","any"), _dd("Under 1","u1"), _dd("Under 2","u2"),
    _dd("Under 5","u5"), _dd("Under 10","u10"),
    _dd("1 – 2","1t2"), _dd("2 – 5","2t5"), _dd("5 – 10","5t10"),
    _dd("Over 1","o1"), _dd("Over 2","o2"), _dd("Over 5","o5"), _dd("Over 10","o10"),
]
_MAP_PS: dict = {
    "u1":(None,1),"u2":(None,2),"u5":(None,5),"u10":(None,10),
    "1t2":(1,2),"2t5":(2,5),"5t10":(5,10),
    "o1":(1,None),"o2":(2,None),"o5":(5,None),"o10":(10,None),
}

_OPT_PB: list[dict] = [
    _dd("Any","any"), _dd("Under 1","u1"), _dd("Under 2","u2"),
    _dd("Under 3","u3"), _dd("Under 5","u5"),
    _dd("1 – 2","1t2"), _dd("2 – 5","2t5"),
    _dd("Over 1","o1"), _dd("Over 2","o2"), _dd("Over 5","o5"),
]
_MAP_PB: dict = {
    "u1":(None,1),"u2":(None,2),"u3":(None,3),"u5":(None,5),
    "1t2":(1,2),"2t5":(2,5),"o1":(1,None),"o2":(2,None),"o5":(5,None),
}

_OPT_EPS: list[dict] = [
    _dd("Any","any"), _dd("Positive","pos"), _dd("Negative","neg"),
    _dd("Over $1","o1"), _dd("Over $5","o5"),
    _dd("Over $10","o10"), _dd("Over $20","o20"),
    _dd("Under $0","u0"),
]
_MAP_EPS: dict = {
    "pos":(0,None),"neg":(None,0),"u0":(None,0),
    "o1":(1,None),"o5":(5,None),"o10":(10,None),"o20":(20,None),
}

_OPT_ROE: list[dict] = [
    _dd("Any","any"), _dd("Positive","pos"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"),
    _dd("Over 15%","o15"), _dd("Over 20%","o20"),
    _dd("Over 25%","o25"), _dd("Over 30%","o30"),
    _dd("Negative","neg"),
]
_MAP_ROE: dict = {
    "pos":(0,None),"neg":(None,0),
    "o5":(5,None),"o10":(10,None),"o15":(15,None),
    "o20":(20,None),"o25":(25,None),"o30":(30,None),
}

_OPT_DE: list[dict] = [
    _dd("Any","any"), _dd("Under 0.25","u025"), _dd("Under 0.5","u05"),
    _dd("Under 1","u1"), _dd("Under 2","u2"),
    _dd("Over 1","o1"), _dd("Over 2","o2"), _dd("Over 3","o3"),
]
_MAP_DE: dict = {
    "u025":(None,.25),"u05":(None,.5),"u1":(None,1),"u2":(None,2),
    "o1":(1,None),"o2":(2,None),"o3":(3,None),
}

_OPT_CR: list[dict] = [
    _dd("Any","any"), _dd("Under 1","u1"),
    _dd("1 – 2","1t2"), _dd("2 – 3","2t3"),
    _dd("Over 1","o1"), _dd("Over 1.5","o15"),
    _dd("Over 2","o2"), _dd("Over 3","o3"),
]
_MAP_CR: dict = {
    "u1":(None,1),"1t2":(1,2),"2t3":(2,3),
    "o1":(1,None),"o15":(1.5,None),"o2":(2,None),"o3":(3,None),
}

_OPT_QR: list[dict] = [
    _dd("Any","any"), _dd("Under 1","u1"),
    _dd("Over 1","o1"), _dd("Over 1.5","o15"), _dd("Over 2","o2"),
]
_MAP_QR: dict = {
    "u1":(None,1),"o1":(1,None),"o15":(1.5,None),"o2":(2,None),
}

_OPT_DY: list[dict] = [
    _dd("Any","any"), _dd("Over 0.5%","o05"), _dd("Over 1%","o1"),
    _dd("Over 2%","o2"), _dd("Over 3%","o3"),
    _dd("Over 4%","o4"), _dd("Over 5%","o5"),
    _dd("Over 8%","o8"), _dd("Over 10%","o10"),
]
_MAP_DY: dict = {
    "o05":(.5,None),"o1":(1,None),"o2":(2,None),"o3":(3,None),
    "o4":(4,None),"o5":(5,None),"o8":(8,None),"o10":(10,None),
}

# Disabled-only options (data not yet available)
_OPT_EV = _soon(
    ("Under 5","u5"),("Under 10","u10"),("Under 15","u15"),
    ("Over 5","o5"),("Over 10","o10"),("Over 20","o20"),
)
_OPT_MACD = _soon(
    ("Bullish Crossover","bc"),("Bearish Crossover","brc"),
    ("Above Signal","abs"),("Below Signal","bls"),
    ("Positive","pos"),("Negative","neg"),
)

# ROA — sourced from Finviz bulk export "ROA" column (%)
_OPT_ROA: list[dict] = [
    _dd("Any","any"), _dd("Positive","pos"), _dd("Negative","neg"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"),
    _dd("Over 15%","o15"), _dd("Over 20%","o20"),
]
_MAP_ROA: dict = {
    "pos":(0,None),"neg":(None,0),
    "o5":(5,None),"o10":(10,None),"o15":(15,None),"o20":(20,None),
}

# Shared margin options — used by Gross Margin, Op. Margin, Net Margin (all in %)
_OPT_MARGIN: list[dict] = [
    _dd("Any","any"), _dd("Positive","pos"), _dd("Negative","neg"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"), _dd("Over 15%","o15"),
    _dd("Over 20%","o20"), _dd("Over 30%","o30"), _dd("Over 40%","o40"),
]
_MAP_MARGIN: dict = {
    "pos":(0,None),"neg":(None,0),
    "o5":(5,None),"o10":(10,None),"o15":(15,None),
    "o20":(20,None),"o30":(30,None),"o40":(40,None),
}

# Beta — sourced from Finviz bulk export "Beta" column
_OPT_BETA: list[dict] = [
    _dd("Any","any"),
    _dd("Under 0.5","u05"), _dd("Under 1","u1"),
    _dd("1 – 1.5","1t15"),
    _dd("Over 1","o1"), _dd("Over 1.5","o15"), _dd("Over 2","o2"),
]
_MAP_BETA: dict = {
    "u05":(None,0.5),"u1":(None,1),
    "1t15":(1,1.5),
    "o1":(1,None),"o15":(1.5,None),"o2":(2,None),
}

# Technical
_OPT_RSI: list[dict] = [
    _dd("Any","any"),
    _dd("Oversold (< 30)","os"),
    _dd("30 – 50","30t50"),
    _dd("50 – 70","50t70"),
    _dd("Overbought (> 70)","ob"),
    _dd("Not Extreme (30–70)","ne"),
]
_MAP_RSI: dict = {
    "os":(None,30),"30t50":(30,50),"50t70":(50,70),"ob":(70,None),"ne":(30,70),
}

_OPT_SMA: list[dict] = [
    _dd("Any","any"),
    _dd("Price above SMA","abv"),
    _dd("Price below SMA","blw"),
    _dd("5%+ above SMA","a5"),
    _dd("10%+ above SMA","a10"),
    _dd("20%+ above SMA","a20"),
    _dd("5%+ below SMA","b5"),
    _dd("10%+ below SMA","b10"),
    _dd("20%+ below SMA","b20"),
]
_MAP_SMA: dict = {
    "abv":(0,None),"blw":(None,0),
    "a5":(5,None),"a10":(10,None),"a20":(20,None),
    "b5":(None,-5),"b10":(None,-10),"b20":(None,-20),
}

_OPT_AVGVOL: list[dict] = [
    _dd("Any","any"),
    _dd("Under 100K","u100k"), _dd("Under 500K","u500k"), _dd("Under 1M","u1m"),
    _dd("Over 100K","o100k"), _dd("Over 500K","o500k"),
    _dd("Over 1M","o1m"), _dd("Over 5M","o5m"), _dd("Over 10M","o10m"),
]
_MAP_AVGVOL: dict = {
    "u100k":(None,100_000),"u500k":(None,500_000),"u1m":(None,1_000_000),
    "o100k":(100_000,None),"o500k":(500_000,None),
    "o1m":(1_000_000,None),"o5m":(5_000_000,None),"o10m":(10_000_000,None),
}

_OPT_RELVOL: list[dict] = [
    _dd("Any","any"),
    _dd("Under 0.5","u05"), _dd("Under 1","u1"),
    _dd("Over 0.5","o05"), _dd("Over 1","o1"),
    _dd("Over 1.5","o15"), _dd("Over 2","o2"), _dd("Over 3","o3"),
]
_MAP_RELVOL: dict = {
    "u05":(None,.5),"u1":(None,1),
    "o05":(.5,None),"o1":(1,None),"o15":(1.5,None),"o2":(2,None),"o3":(3,None),
}

# week_52_high_pct: negative = % below 52W high (−5 means 5% below high)
_OPT_52H: list[dict] = [
    _dd("Any","any"),
    _dd("New 52W High","new"),
    _dd("Within 5%","w5"), _dd("Within 10%","w10"), _dd("Within 20%","w20"),
    _dd("10%+ Below High","d10"), _dd("20%+ Below High","d20"),
    _dd("30%+ Below High","d30"), _dd("50%+ Below High","d50"),
]
_MAP_52H: dict = {
    "new":(0,None),"w5":(-5,None),"w10":(-10,None),"w20":(-20,None),
    "d10":(None,-10),"d20":(None,-20),"d30":(None,-30),"d50":(None,-50),
}

# week_52_low_pct: positive = % above 52W low (50 means 50% above low)
_OPT_52L: list[dict] = [
    _dd("Any","any"),
    _dd("Near 52W Low (< 5%)","n5"), _dd("Within 10%","w10"), _dd("Within 20%","w20"),
    _dd("10%+ Above Low","a10"), _dd("30%+ Above Low","a30"),
    _dd("50%+ Above Low","a50"), _dd("100%+ Above Low","a100"),
]
_MAP_52L: dict = {
    "n5":(None,5),"w10":(None,10),"w20":(None,20),
    "a10":(10,None),"a30":(30,None),"a50":(50,None),"a100":(100,None),
}

# ── Tier 1 option sets ────────────────────────────────────────────────────

_OPT_FLOAT_SHORT: list[dict] = [
    _dd("Any","any"),
    _dd("Under 5%","u5"), _dd("Under 10%","u10"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"),
    _dd("Over 15%","o15"), _dd("Over 20%","o20"),
    _dd("Over 25%","o25"), _dd("Over 30%","o30"),
]
_MAP_FLOAT_SHORT: dict = {
    "u5":(None,5),"u10":(None,10),
    "o5":(5,None),"o10":(10,None),"o15":(15,None),
    "o20":(20,None),"o25":(25,None),"o30":(30,None),
}

_OPT_SHORT_RATIO: list[dict] = [
    _dd("Any","any"),
    _dd("Under 1","u1"), _dd("Under 3","u3"),
    _dd("Over 1","o1"), _dd("Over 3","o3"),
    _dd("Over 5","o5"), _dd("Over 10","o10"),
]
_MAP_SHORT_RATIO: dict = {
    "u1":(None,1),"u3":(None,3),
    "o1":(1,None),"o3":(3,None),"o5":(5,None),"o10":(10,None),
}

# Shared by all five performance windows (week, month, quarter, year, YTD)
_OPT_PERF: list[dict] = [
    _dd("Any","any"),
    _dd("Up 5%+","u5"), _dd("Up 10%+","u10"),
    _dd("Up 20%+","u20"), _dd("Up 30%+","u30"), _dd("Up 50%+","u50"),
    _dd("Down 5%+","d5"), _dd("Down 10%+","d10"), _dd("Down 20%+","d20"),
    _dd("Flat (−5% to +5%)","flat"),
]
_MAP_PERF: dict = {
    "u5":(5,None),"u10":(10,None),"u20":(20,None),"u30":(30,None),"u50":(50,None),
    "d5":(None,-5),"d10":(None,-10),"d20":(None,-20),
    "flat":(-5,5),
}

_OPT_RECOM: list[dict] = [
    _dd("Any","any"),
    _dd("Strong Buy","sb"),
    _dd("Buy or Better","b"),
    _dd("Hold or Better","boh"),
    _dd("Sell or Worse","s"),
    _dd("Strong Sell","ss"),
]
_MAP_RECOM: dict = {
    "sb":(None,1.5),"b":(None,2.5),"boh":(None,3.5),
    "s":(3.5,None),"ss":(4.5,None),
}

# target_upside is a computed column (target_price − price) / price * 100
_OPT_TARGET: list[dict] = [
    _dd("Any","any"),
    _dd("Over 5% upside","o5"), _dd("Over 10% upside","o10"),
    _dd("Over 20% upside","o20"), _dd("Over 30% upside","o30"),
    _dd("Over 50% upside","o50"),
    _dd("Downside risk","down"),
]
_MAP_TARGET: dict = {
    "o5":(5,None),"o10":(10,None),"o20":(20,None),
    "o30":(30,None),"o50":(50,None),
    "down":(None,0),
}

# Shared by all five EPS/Sales growth metrics (stored as %)
_OPT_GROWTH: list[dict] = [
    _dd("Any","any"),
    _dd("Positive","pos"), _dd("Negative","neg"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"),
    _dd("Over 15%","o15"), _dd("Over 20%","o20"),
    _dd("Over 25%","o25"), _dd("Over 30%","o30"), _dd("Over 50%","o50"),
    _dd("Under 0%","u0"),
]
_MAP_GROWTH: dict = {
    "pos":(0,None),"neg":(None,0),"u0":(None,0),
    "o5":(5,None),"o10":(10,None),"o15":(15,None),
    "o20":(20,None),"o25":(25,None),"o30":(30,None),"o50":(50,None),
}

_OPT_ATR: list[dict] = [
    _dd("Any","any"),
    _dd("Under $0.50","u050"), _dd("Under $1","u1"),
    _dd("Under $2","u2"), _dd("Under $5","u5"),
    _dd("Over $0.50","o050"), _dd("Over $1","o1"),
    _dd("Over $2","o2"), _dd("Over $5","o5"), _dd("Over $10","o10"),
]
_MAP_ATR: dict = {
    "u050":(None,.5),"u1":(None,1),"u2":(None,2),"u5":(None,5),
    "o050":(.5,None),"o1":(1,None),"o2":(2,None),"o5":(5,None),"o10":(10,None),
}

_OPT_INSIDER_OWN: list[dict] = [
    _dd("Any","any"),
    _dd("Over 5%","o5"), _dd("Over 10%","o10"),
    _dd("Over 20%","o20"), _dd("Over 30%","o30"),
    _dd("Under 5%","u5"),
]
_MAP_INSIDER_OWN: dict = {
    "o5":(5,None),"o10":(10,None),"o20":(20,None),"o30":(30,None),
    "u5":(None,5),
}

_OPT_INST_OWN: list[dict] = [
    _dd("Any","any"),
    _dd("Over 25%","o25"), _dd("Over 50%","o50"),
    _dd("Over 60%","o60"), _dd("Over 70%","o70"), _dd("Over 80%","o80"),
    _dd("Under 50%","u50"),
]
_MAP_INST_OWN: dict = {
    "o25":(25,None),"o50":(50,None),"o60":(60,None),
    "o70":(70,None),"o80":(80,None),"u50":(None,50),
}


def _scr_dd(label: str, dd_id: str, opts: list, soon: bool = False) -> html.Div:
    """Compact label-over-dropdown cell for the screener filter grid."""
    return html.Div([
        html.Span(label, className="scr-dd-lbl"),
        dbc.Select(
            id=dd_id, options=opts, value="any",
            className="scr-dd-sel" + (" scr-dd-sel--soon" if soon else ""),
        ),
    ], className="scr-dd-cell")


def _scr_sep(label: str) -> html.Div:
    """Full-width section divider for the screener filter grid."""
    return html.Div(label, className="scr-dd-sep")

# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        # ── Stores ────────────────────────────────────────────────────────
        dcc.Interval(id="screener-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="screener-sort-dir",      data="desc"),
        dcc.Store(id="screener-page",          data=1),
        dcc.Store(id="watchlist-store",        data=0),
        dcc.Store(id="scr-news-custom-state",  data={}),

        # ── Top toolbar: Sort By | ↓ | Search | ↻ ─────────────────────────
        html.Div(
            className="filter-bar",
            style={"flexWrap": "nowrap", "marginBottom": "8px"},
            children=[
                dbc.Select(
                    id="screener-order",
                    options=ORDER_OPTIONS,
                    value="market_cap",
                    className="filter-select",
                    style={"minWidth": "150px", **_SH},
                ),
                html.Button("↓", id="screener-sort-dir-btn", n_clicks=0,
                            style=_SORT_BTN_STYLE, title="Toggle sort direction"),
                dcc.Input(
                    id="screener-search",
                    placeholder="🔍  Search ticker…",
                    debounce=True,
                    className="filter-input",
                    style={"height": "36px", "flex": "1", "minWidth": "120px"},
                ),
                html.Button("↻", id="screener-refresh-btn", n_clicks=0,
                            style=_REFRESH_BTN_STYLE, title="Refresh now"),
            ],
        ),

        # ── Filter panel (always visible) ─────────────────────────────────
        html.Div(className="scr-filter-panel", children=[

            # Tab strip
            html.Div(className="scr-ftab-strip", children=[
                html.Button(
                    lbl,
                    id=f"screener-ftab-{sid}",
                    n_clicks=0,
                    className=("scr-ftab scr-ftab-active"
                               if sid == "desc" else "scr-ftab"),
                )
                for sid, lbl in zip(_FTAB_IDS, _FTAB_LABELS)
            ]),

            # ── Descriptive ───────────────────────────────────────────────
            html.Div(id="screener-ftab-desc-panel", children=[
                html.Div(className="filter-row", children=[
                    _fi("Sector",   "screener-sector",   SECTOR_OPTIONS,   "all"),
                    _fi("Industry", "screener-industry", INDUSTRY_OPTIONS, "all"),
                    _fi("Country",  "screener-country",  COUNTRY_OPTIONS,  "all"),
                    _fi("Exchange", "screener-exchange", EXCHANGE_OPTIONS, "all"),
                ]),
                html.Div(className="filter-row", children=[
                    _fi("Market Cap", "screener-mktcap",    MKTCAP_OPTIONS, "all"),
                    _fi("Price ($)",  "screener-price",     PRICE_OPTIONS,  "all"),
                    _fi("Change %",   "screener-chg-pct",   CHG_OPTIONS,    "all"),
                    _fi("Volume",     "screener-volume",    VOLUME_OPTIONS, "all"),
                ]),
                html.Div(className="filter-row", children=[
                    _fi("Avg Volume", "screener-avg-volume", AVG_VOL_OPTIONS, "all"),
                ]),
            ]),

            # ── Fundamental ───────────────────────────────────────────────
            html.Div(
                id="screener-ftab-fund-panel",
                style={"display": "none"},
                className="scr-dd-grid",
                children=[
                    _scr_sep("Valuation"),
                    _scr_dd("P/E Ratio",    "scr-pe",       _OPT_PE),
                    _scr_dd("Forward P/E",  "scr-fpe",      _OPT_PE),
                    _scr_dd("PEG Ratio",    "scr-peg",      _OPT_PEG),
                    _scr_dd("Price / Sales","scr-ps",        _OPT_PS),
                    _scr_dd("Price / Book", "scr-pb",        _OPT_PB),
                    _scr_dd("EV / EBITDA",  "scr-evebitda",  _OPT_EV,      soon=True),

                    _scr_sep("Profitability"),
                    _scr_dd("EPS (ttm)",      "scr-eps", _OPT_EPS),
                    _scr_dd("ROE",            "scr-roe", _OPT_ROE),
                    _scr_dd("ROA",            "scr-roa", _OPT_ROA),
                    _scr_dd("Gross Margin",   "scr-gm",  _OPT_MARGIN),
                    _scr_dd("Op. Margin",     "scr-om",  _OPT_MARGIN),
                    _scr_dd("Net Margin",     "scr-pm",  _OPT_MARGIN),

                    _scr_sep("Financial Health"),
                    _scr_dd("Debt / Equity", "scr-de", _OPT_DE),
                    _scr_dd("Current Ratio", "scr-cr", _OPT_CR),
                    _scr_dd("Quick Ratio",   "scr-qr", _OPT_QR),

                    _scr_sep("Income"),
                    _scr_dd("Dividend Yield", "scr-dy", _OPT_DY),

                    _scr_sep("Growth"),
                    _scr_dd("EPS Gr. This Yr",  "scr-eps-gy",      _OPT_GROWTH),
                    _scr_dd("EPS Gr. Next Yr",  "scr-eps-gny",     _OPT_GROWTH),
                    _scr_dd("EPS Gr. (5Y)",     "scr-eps-g5y",     _OPT_GROWTH),
                    _scr_dd("EPS Gr. (QoQ)",    "scr-eps-gqoq",    _OPT_GROWTH),
                    _scr_dd("Sales Gr. (QoQ)",  "scr-sales-gqoq",  _OPT_GROWTH),

                    _scr_sep("Ownership"),
                    _scr_dd("Insider Own",      "scr-insider-own",  _OPT_INSIDER_OWN),
                    _scr_dd("Inst. Own",        "scr-inst-own",     _OPT_INST_OWN),

                    _scr_sep("Analyst"),
                    _scr_dd("Analyst Rec.",     "scr-recom",        _OPT_RECOM),
                    _scr_dd("Target Upside",    "scr-target",       _OPT_TARGET),
                ],
            ),

            # ── Technical ─────────────────────────────────────────────────
            html.Div(
                id="screener-ftab-tech-panel",
                style={"display": "none"},
                className="scr-dd-grid",
                children=[
                    _scr_sep("Trend  —  % price is above (+) / below (−) its moving average"),
                    _scr_dd("SMA 20",  "scr-sma20",  _OPT_SMA),
                    _scr_dd("SMA 50",  "scr-sma50",  _OPT_SMA),
                    _scr_dd("SMA 200", "scr-sma200", _OPT_SMA),

                    _scr_sep("Momentum"),
                    _scr_dd("RSI (14)", "scr-rsi",  _OPT_RSI),
                    _scr_dd("MACD",     "scr-macd", _OPT_MACD, soon=True),

                    _scr_sep("Volume"),
                    _scr_dd("Avg Volume",  "scr-avgvol", _OPT_AVGVOL),
                    _scr_dd("Rel. Volume", "scr-relvol", _OPT_RELVOL),

                    _scr_sep("Price"),
                    _scr_dd("52W High", "scr-52h",  _OPT_52H),
                    _scr_dd("52W Low",  "scr-52l",  _OPT_52L),
                    _scr_dd("Beta",     "scr-beta", _OPT_BETA),
                    _scr_dd("ATR ($)",  "scr-atr",  _OPT_ATR),

                    _scr_sep("Short Interest"),
                    _scr_dd("Float Short",  "scr-float-short",  _OPT_FLOAT_SHORT),
                    _scr_dd("Short Ratio",  "scr-short-ratio",  _OPT_SHORT_RATIO),

                    _scr_sep("Performance"),
                    _scr_dd("Perf Week",    "scr-perf-w",   _OPT_PERF),
                    _scr_dd("Perf Month",   "scr-perf-m",   _OPT_PERF),
                    _scr_dd("Perf Quarter", "scr-perf-q",   _OPT_PERF),
                    _scr_dd("Perf Year",    "scr-perf-y",   _OPT_PERF),
                    _scr_dd("Perf YTD",     "scr-perf-ytd", _OPT_PERF),
                ],
            ),

            # ── News ─────────────────────────────────────────────────────
            html.Div(id="screener-ftab-news-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    # Latest News time range
                    html.Div([
                        html.Label("Latest News", className="filter-label"),
                        dbc.Select(
                            id="screener-news-time",
                            options=NEWS_TIME_OPTIONS,
                            value="any",
                            className="filter-select",
                            style={**_SH},
                        ),
                    ], className="filter-item"),
                    # Keywords
                    html.Div([
                        html.Label("News Keywords", className="filter-label"),
                        dcc.Input(
                            id="screener-news-keywords",
                            placeholder="Search headlines…",
                            debounce=True,
                            className="filter-input scr-news-kw-input",
                            style={"height": "36px", "width": "100%"},
                        ),
                    ], className="filter-item scr-news-kw-item"),
                ]),
                # Custom time range panel
                html.Div(id="scr-news-custom-panel", style={"display": "none"},
                         className="scr-ncp", children=[
                    html.Div(className="scr-ncp-header", children=[
                        html.Span("Custom Date Range & Categories",
                                  className="scr-ncp-title"),
                    ]),
                    html.Div(className="scr-ncp-body", children=[
                        dcc.Checklist(
                            id="scr-news-cats",
                            options=[{"label": lbl, "value": val}
                                     for val, lbl in _NEWS_CAT_LABELS],
                            value=[],
                            className="scr-ncp-checklist",
                            labelClassName="scr-ncp-check-label",
                            inputClassName="scr-ncp-check-input",
                            inline=True,
                        ),
                    ]),
                    html.Div(className="scr-ncp-dates", children=[
                        html.Div([
                            html.Label("From", className="filter-label"),
                            dcc.DatePickerSingle(
                                id="scr-news-date-from",
                                display_format="YYYY-MM-DD",
                                placeholder="YYYY-MM-DD",
                                className="scr-ncp-dp",
                            ),
                        ], className="scr-ncp-date-item"),
                        html.Div([
                            html.Label("To", className="filter-label"),
                            dcc.DatePickerSingle(
                                id="scr-news-date-to",
                                display_format="YYYY-MM-DD",
                                placeholder="YYYY-MM-DD",
                                className="scr-ncp-dp",
                            ),
                        ], className="scr-ncp-date-item"),
                    ]),
                    html.Div(className="scr-ncp-btns", children=[
                        html.Button("Select All", id="scr-news-select-all", n_clicks=0,
                                    className="scr-ncp-btn"),
                        html.Div(style={"flex": "1"}),
                        html.Button("Cancel", id="scr-news-cancel", n_clicks=0,
                                    className="scr-ncp-btn scr-ncp-btn-cancel"),
                        html.Button("Apply",  id="scr-news-apply",  n_clicks=0,
                                    className="scr-ncp-btn scr-ncp-btn-apply"),
                    ]),
                ]),
            ]),

            # Reset button
            html.Div(
                style={"display": "flex", "justifyContent": "flex-end",
                       "marginTop": "12px"},
                children=[html.Button("Reset Filters", id="screener-reset-btn",
                                      n_clicks=0, style=_RESET_BTN_STYLE)],
            ),
        ]),

        html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 10px", "opacity": "1"}),

        html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "alignItems": "center", "marginBottom": "10px", "padding": "0 2px"},
            children=[
                html.Span(id="screener-count", className="count-label"),
                html.Div(id="screener-pagination", className="pagination-bar",
                         style={"padding": "0", "justifyContent": "flex-end"}),
            ],
        ),

        html.Div(
            className="articles-table",
            style={"overflowX": "auto", "padding": "0"},
            children=[html.Table(
                className="scr-table",
                children=[
                    html.Thead(html.Tr([
                        html.Th(lbl, style={"width": w, "textAlign": a})
                        for lbl, w, a in _OV_COLS
                    ])),
                    html.Tbody(id="screener-overview-rows"),
                ],
            )],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("screener-interval", "interval"),
    Input("screener-interval",  "n_intervals"),
)
def _update_interval(_):
    return 60_000 if is_market_hours() else 300_000


@callback(
    Output("screener-sort-dir",     "data"),
    Output("screener-sort-dir-btn", "children"),
    Input("screener-sort-dir-btn",  "n_clicks"),
    State("screener-sort-dir",      "data"),
    prevent_initial_call=True,
)
def _toggle_sort_dir(_, current):
    new_dir = "asc" if current == "desc" else "desc"
    return new_dir, "↑" if new_dir == "asc" else "↓"


@callback(
    *[Output(f"screener-ftab-{t}-panel", "style") for t in _FTAB_IDS],
    *[Output(f"screener-ftab-{t}", "className") for t in _FTAB_IDS],
    *[Input(f"screener-ftab-{t}", "n_clicks") for t in _FTAB_IDS],
    prevent_initial_call=True,
)
def _switch_filter_tab(*_):
    active  = (ctx.triggered_id or "screener-ftab-desc").replace("screener-ftab-", "")
    styles  = [{} if t == active else {"display": "none"} for t in _FTAB_IDS]
    classes = ["scr-ftab scr-ftab-active" if t == active else "scr-ftab"
               for t in _FTAB_IDS]
    return styles + classes


_ALL_FILTER_INPUTS = [
    Input("screener-order",         "value"),
    Input("screener-sort-dir",      "data"),
    Input("screener-search",        "value"),
    Input("screener-sector",        "value"),
    Input("screener-mktcap",        "value"),
    Input("screener-country",       "value"),
    Input("screener-price",         "value"),
    Input("screener-chg-pct",       "value"),
    Input("screener-volume",        "value"),
    Input("screener-avg-volume",    "value"),
    Input("screener-news-time",     "value"),
    Input("screener-news-keywords", "value"),
    Input("scr-news-custom-state",  "data"),
    # Fundamental dropdowns
    Input("scr-pe",  "value"), Input("scr-fpe", "value"),
    Input("scr-peg", "value"), Input("scr-ps",  "value"),
    Input("scr-pb",  "value"), Input("scr-eps", "value"),
    Input("scr-roe", "value"), Input("scr-de",  "value"),
    Input("scr-cr",  "value"), Input("scr-qr",  "value"),
    Input("scr-dy",  "value"),
    # Technical dropdowns
    Input("scr-rsi",    "value"), Input("scr-sma20",  "value"),
    Input("scr-sma50",  "value"), Input("scr-sma200", "value"),
    Input("scr-avgvol", "value"), Input("scr-relvol", "value"),
    Input("scr-52h",    "value"), Input("scr-52l",    "value"),
    Input("scr-beta",   "value"),
    # Profitability dropdowns (migration 016)
    Input("scr-roa",    "value"), Input("scr-gm",     "value"),
    Input("scr-om",     "value"), Input("scr-pm",     "value"),
    # Tier 1 screener fields (migration 017)
    Input("scr-float-short",  "value"), Input("scr-short-ratio",  "value"),
    Input("scr-perf-w",       "value"), Input("scr-perf-m",       "value"),
    Input("scr-perf-q",       "value"), Input("scr-perf-y",       "value"),
    Input("scr-perf-ytd",     "value"),
    Input("scr-recom",        "value"), Input("scr-target",       "value"),
    Input("scr-eps-gy",       "value"), Input("scr-eps-gny",      "value"),
    Input("scr-eps-g5y",      "value"), Input("scr-eps-gqoq",     "value"),
    Input("scr-sales-gqoq",   "value"),
    Input("scr-atr",          "value"),
    Input("scr-insider-own",  "value"), Input("scr-inst-own",     "value"),
]


@callback(
    Output("screener-page", "data"),
    *_ALL_FILTER_INPUTS,
    prevent_initial_call=True,
)
def _reset_screener_page(*_):
    return 1


@callback(
    Output("screener-sector",        "value"),
    Output("screener-mktcap",        "value"),
    Output("screener-country",       "value"),
    Output("screener-price",         "value"),
    Output("screener-chg-pct",       "value"),
    Output("screener-volume",        "value"),
    Output("screener-avg-volume",    "value"),
    Output("screener-industry",      "value"),
    Output("screener-exchange",      "value"),
    Output("screener-order",         "value"),
    Output("screener-search",        "value"),
    Output("screener-news-time",     "value"),
    Output("screener-news-keywords", "value"),
    Output("scr-news-custom-state",  "data"),
    # Fundamental dropdowns
    Output("scr-pe",  "value"), Output("scr-fpe", "value"),
    Output("scr-peg", "value"), Output("scr-ps",  "value"),
    Output("scr-pb",  "value"), Output("scr-eps", "value"),
    Output("scr-roe", "value"), Output("scr-de",  "value"),
    Output("scr-cr",  "value"), Output("scr-qr",  "value"),
    Output("scr-dy",  "value"),
    # Technical dropdowns
    Output("scr-rsi",    "value"), Output("scr-sma20",  "value"),
    Output("scr-sma50",  "value"), Output("scr-sma200", "value"),
    Output("scr-avgvol", "value"), Output("scr-relvol", "value"),
    Output("scr-52h",    "value"), Output("scr-52l",    "value"),
    Output("scr-beta",   "value"),
    # Profitability dropdowns (migration 016)
    Output("scr-roa",    "value"), Output("scr-gm",     "value"),
    Output("scr-om",     "value"), Output("scr-pm",     "value"),
    # Tier 1 screener fields (migration 017)
    Output("scr-float-short",  "value"), Output("scr-short-ratio",  "value"),
    Output("scr-perf-w",       "value"), Output("scr-perf-m",       "value"),
    Output("scr-perf-q",       "value"), Output("scr-perf-y",       "value"),
    Output("scr-perf-ytd",     "value"),
    Output("scr-recom",        "value"), Output("scr-target",       "value"),
    Output("scr-eps-gy",       "value"), Output("scr-eps-gny",      "value"),
    Output("scr-eps-g5y",      "value"), Output("scr-eps-gqoq",     "value"),
    Output("scr-sales-gqoq",   "value"),
    Output("scr-atr",          "value"),
    Output("scr-insider-own",  "value"), Output("scr-inst-own",     "value"),
    Input("screener-reset-btn", "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_):
    return (
        "all", "all", "all", "all", "all", "all", "all",
        "all", "all", "market_cap", "", "any", "", {},
        # 41 × "any" for all dropdown filters
        *( ["any"] * 41 ),
    )


@callback(
    Output("screener-page", "data", allow_duplicate=True),
    Input({"type": "scr-pgbtn", "page": ALL}, "n_clicks"),
    State("screener-page", "data"),
    prevent_initial_call=True,
)
def _handle_screener_page_click(n_clicks_list, current_page):
    if not any(n for n in (n_clicks_list or [])):
        raise PreventUpdate
    triggered = ctx.triggered_id
    if triggered is None:
        raise PreventUpdate
    page_val = triggered["page"]
    cur = int(current_page or 1)
    if page_val == "prev":
        return max(1, cur - 1)
    if page_val == "next":
        return cur + 1
    return int(page_val)


@callback(
    Output("watchlist-store", "data"),
    Input({"type": "scr-star", "ticker": ALL}, "n_clicks"),
    State("watchlist-store", "data"),
    prevent_initial_call=True,
)
def _toggle_screener_star(n_clicks_list, version):
    if not any(n for n in (n_clicks_list or [])):
        raise PreventUpdate
    triggered = ctx.triggered_id
    if triggered is None:
        raise PreventUpdate
    ticker  = triggered["ticker"]
    watched = set(watchlist_tickers())
    if ticker in watched:
        watchlist_remove(ticker)
    else:
        watchlist_add(ticker)
    return (version or 0) + 1


@callback(
    Output("screener-overview-rows", "children"),
    Output("screener-count",         "children"),
    Output("screener-pagination",    "children"),
    Input("screener-interval",       "n_intervals"),
    Input("screener-refresh-btn",    "n_clicks"),
    *_ALL_FILTER_INPUTS,
    Input("screener-page",           "data"),
    Input("url",                     "pathname"),
    Input("watchlist-store",         "data"),
)
def _update_screener(n, refresh_clicks,
                     order, sort_dir, search,
                     sector, mktcap, country, price, chg_pct, volume, avg_volume,
                     news_time, news_keywords, news_custom_state,
                     pe, fpe, peg, ps, pb, eps, roe, de, cr, qr, dy,
                     rsi, sma20, sma50, sma200, avgvol, relvol, h52, l52,
                     beta, roa, gm, om, pm,
                     float_short, short_ratio,
                     perf_w, perf_m, perf_q, perf_y, perf_ytd,
                     recom, target,
                     eps_gy, eps_gny, eps_g5y, eps_gqoq, sales_gqoq,
                     atr,
                     insider_own, inst_own,
                     page, pathname, _wl_version):
    if pathname not in (None, "/screener"):
        raise PreventUpdate

    order    = order    or "market_cap"
    sort_dir = sort_dir or "desc"
    page     = max(1, int(page or 1))

    df = _fetch_data()

    if df.empty:
        empty_msg = [html.Div(
            "No data yet — price data is being collected. Check back in a few minutes.",
            style={"padding": "32px", "textAlign": "center",
                   "color": "#555", "fontSize": "14px"},
        )]
        return empty_msg, "0 tickers", []

    if search:
        df = df[df["ticker"].str.upper().str.startswith(search.strip().upper())]

    df = _apply_sector_filter(df, sector or "all")
    df = _apply_country_filter(df, country or "all")
    df = _apply_mktcap_filter(df, mktcap or "all")
    df = _apply_price_filter(df, price or "all")
    df = _apply_chg_pct_filter(df, chg_pct or "all")
    df = _apply_volume_filter(df, volume or "all")
    df = _apply_avg_volume_filter(df, avg_volume or "all")

    # Fundamental dropdown filters
    df = _apply_dd_filter(df, "pe_ratio",       pe,     _MAP_PE)
    df = _apply_dd_filter(df, "forward_pe",     fpe,    _MAP_PE)
    df = _apply_dd_filter(df, "peg_ratio",      peg,    _MAP_PEG)
    df = _apply_dd_filter(df, "price_to_sales", ps,     _MAP_PS)
    df = _apply_dd_filter(df, "price_to_book",  pb,     _MAP_PB)
    df = _apply_dd_filter(df, "eps_ttm",        eps,    _MAP_EPS)
    df = _apply_dd_filter(df, "roe",            roe,    _MAP_ROE)
    df = _apply_dd_filter(df, "debt_to_equity", de,     _MAP_DE)
    df = _apply_dd_filter(df, "current_ratio",  cr,     _MAP_CR)
    df = _apply_dd_filter(df, "quick_ratio",    qr,     _MAP_QR)
    df = _apply_dd_filter(df, "dividend_yield", dy,     _MAP_DY)
    # Technical dropdown filters
    df = _apply_dd_filter(df, "rsi_14",           rsi,    _MAP_RSI)
    df = _apply_dd_filter(df, "sma_20_pct",       sma20,  _MAP_SMA)
    df = _apply_dd_filter(df, "sma_50_pct",       sma50,  _MAP_SMA)
    df = _apply_dd_filter(df, "sma_200_pct",      sma200, _MAP_SMA)
    df = _apply_dd_filter(df, "avg_volume",       avgvol, _MAP_AVGVOL)
    df = _apply_dd_filter(df, "rel_volume",       relvol, _MAP_RELVOL)
    df = _apply_dd_filter(df, "week_52_high_pct", h52,    _MAP_52H)
    df = _apply_dd_filter(df, "week_52_low_pct",  l52,    _MAP_52L)
    df = _apply_dd_filter(df, "beta",             beta,   _MAP_BETA)
    df = _apply_dd_filter(df, "roa",              roa,    _MAP_ROA)
    df = _apply_dd_filter(df, "gross_margin",     gm,     _MAP_MARGIN)
    df = _apply_dd_filter(df, "operating_margin", om,     _MAP_MARGIN)
    df = _apply_dd_filter(df, "net_margin",       pm,     _MAP_MARGIN)
    # Short Interest
    df = _apply_dd_filter(df, "float_short",  float_short, _MAP_FLOAT_SHORT)
    df = _apply_dd_filter(df, "short_ratio",  short_ratio, _MAP_SHORT_RATIO)
    # Performance
    df = _apply_dd_filter(df, "perf_week",  perf_w,   _MAP_PERF)
    df = _apply_dd_filter(df, "perf_month", perf_m,   _MAP_PERF)
    df = _apply_dd_filter(df, "perf_quart", perf_q,   _MAP_PERF)
    df = _apply_dd_filter(df, "perf_year",  perf_y,   _MAP_PERF)
    df = _apply_dd_filter(df, "perf_ytd",   perf_ytd, _MAP_PERF)
    # Analyst
    df = _apply_dd_filter(df, "analyst_recom", recom, _MAP_RECOM)
    if "target_price" in df.columns and "price" in df.columns:
        _p = pd.to_numeric(df["price"], errors="coerce").replace(0, float("nan"))
        _t = pd.to_numeric(df["target_price"], errors="coerce")
        df["target_upside"] = (_t - _p) / _p * 100
    df = _apply_dd_filter(df, "target_upside", target, _MAP_TARGET)
    # EPS / Sales Growth
    df = _apply_dd_filter(df, "eps_growth_this_year", eps_gy,     _MAP_GROWTH)
    df = _apply_dd_filter(df, "eps_growth_next_year", eps_gny,    _MAP_GROWTH)
    df = _apply_dd_filter(df, "eps_growth_5y",        eps_g5y,    _MAP_GROWTH)
    df = _apply_dd_filter(df, "eps_growth_qoq",       eps_gqoq,   _MAP_GROWTH)
    df = _apply_dd_filter(df, "sales_growth_qoq",     sales_gqoq, _MAP_GROWTH)
    # Volatility
    df = _apply_dd_filter(df, "atr",         atr,         _MAP_ATR)
    # Ownership
    df = _apply_dd_filter(df, "insider_own", insider_own, _MAP_INSIDER_OWN)
    df = _apply_dd_filter(df, "inst_own",    inst_own,    _MAP_INST_OWN)

    news_tickers = _fetch_news_tickers(
        news_time or "any", news_keywords or "", news_custom_state or {}
    )
    if news_tickers is not None:
        df = df[df["ticker"].isin(news_tickers)]

    df = _apply_sort(df, order, sort_dir)
    df = df.reset_index(drop=True)

    total       = len(df)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page        = min(page, total_pages)
    start       = (page - 1) * PAGE_SIZE + 1
    end         = min(page * PAGE_SIZE, total)
    time_str    = now_et().strftime("%H:%M ET")

    if total == 0:
        count_el = html.Span("0 tickers matched", style={"color": "#555"})
    else:
        count_el = html.Span([
            html.Span(f"Showing {start:,}–{end:,} of {total:,} tickers",
                      style={"color": "#888"}),
            html.Span(f"  ·  Updated {time_str}", style={"color": "#3a3a3a"}),
        ])

    watched = set(watchlist_tickers())
    return (
        _render_overview_rows(df, page, watched),
        count_el,
        _render_pagination(page, total_pages),
    )


# ── News filter callbacks ──────────────────────────────────────────────────

@callback(
    Output("scr-news-custom-panel", "style"),
    Input("screener-news-time", "value"),
    prevent_initial_call=True,
)
def _open_panel_on_custom(value):
    if value == "custom":
        return {}
    return {"display": "none"}


@callback(
    Output("scr-news-cats", "value"),
    Input("scr-news-select-all", "n_clicks"),
    prevent_initial_call=True,
)
def _select_all_news(_):
    return [val for val, _ in _NEWS_CAT_LABELS]


@callback(
    Output("scr-news-custom-state", "data"),
    Output("scr-news-custom-panel", "style", allow_duplicate=True),
    Input("scr-news-apply", "n_clicks"),
    State("scr-news-cats",      "value"),
    State("scr-news-date-from", "date"),
    State("scr-news-date-to",   "date"),
    prevent_initial_call=True,
)
def _apply_custom(_, cats, date_from, date_to):
    state = {
        "cats":      cats or [],
        "date_from": date_from,
        "date_to":   date_to,
    }
    return state, {"display": "none"}


@callback(
    Output("screener-news-time",    "value"),
    Output("scr-news-custom-panel", "style", allow_duplicate=True),
    Input("scr-news-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def _cancel_custom(_):
    return "any", {"display": "none"}
