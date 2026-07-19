"""
Screener page — Finviz-style dense ticker table with real-time price/sentiment.
"""
from __future__ import annotations

import math

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
    {"label": "Avg Sentiment",  "value": "avg_sentiment"},
    {"label": "Price",          "value": "price"},
    {"label": "Change %",       "value": "change_pct"},
    {"label": "Volume",         "value": "volume"},
    {"label": "Market Cap",     "value": "market_cap"},
    {"label": "Article Count",  "value": "article_count"},
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

SENT_LABEL_OPTIONS = [
    {"label": "Any Sentiment",    "value": "all"},
    {"label": "Bullish",          "value": "Bullish"},
    {"label": "Somewhat Bullish", "value": "Somewhat Bullish"},
    {"label": "Neutral",          "value": "Neutral"},
    {"label": "Somewhat Bearish", "value": "Somewhat Bearish"},
    {"label": "Bearish",          "value": "Bearish"},
]

SENT_SCORE_OPTIONS = [
    {"label": "Any Score",           "value": "all"},
    {"label": "Strong Bull  (>0.6)", "value": "sbull"},
    {"label": "Bull  (0.3–0.6)",     "value": "bull"},
    {"label": "Neutral  (−0.3–0.3)", "value": "neut"},
    {"label": "Bear  (−0.6–−0.3)",   "value": "bear"},
    {"label": "Strong Bear  (<−0.6)","value": "sbear"},
]

TIME_WINDOW_OPTIONS = [
    {"label": "1 Hour",   "value": "1hr"},
    {"label": "4 Hours",  "value": "4hr"},
    {"label": "24 Hours", "value": "24hr"},
]

MIN_ARTICLES_OPTIONS = [
    {"label": "Any",    "value": 0},
    {"label": "2+",     "value": 2},
    {"label": "5+",     "value": 5},
    {"label": "10+",    "value": 10},
    {"label": "25+",    "value": 25},
]

TREND_OPTIONS = [
    {"label": "Any Trend", "value": "all"},
    {"label": "Improving", "value": "improving"},
    {"label": "Declining", "value": "declining"},
    {"label": "Stable",    "value": "stable"},
]

SPIKE_OPTIONS = [
    {"label": "Any",       "value": "all"},
    {"label": "Has Spike", "value": "spike"},
    {"label": "No Spike",  "value": "no_spike"},
]

# ── SQL ───────────────────────────────────────────────────────────────────

_SCREENER_SQL = """
    WITH latest AS (
        SELECT DISTINCT ON (ticker)
            ticker,
            avg_sentiment,
            article_count,
            bullish_count,
            bearish_count,
            neutral_count,
            momentum,
            calculated_at
        FROM ticker_sentiment_summary
        WHERE "window" = :window
        ORDER BY ticker, calculated_at DESC
    ),
    recent_spikes AS (
        SELECT DISTINCT ticker
        FROM sentiment_spikes
        WHERE detected_at > NOW() - INTERVAL '2 hours'
    )
    SELECT
        l.ticker,
        COALESCE(l.avg_sentiment,  0)   AS avg_sentiment,
        COALESCE(l.article_count,  0)   AS article_count,
        COALESCE(l.bullish_count,  0)   AS bullish_count,
        COALESCE(l.bearish_count,  0)   AS bearish_count,
        COALESCE(l.neutral_count,  0)   AS neutral_count,
        l.momentum,
        l.calculated_at,
        p.company_name,
        p.sector,
        p.country,
        p.price,
        p.change_pct,
        p.volume,
        p.market_cap,
        p.pre_market_price,
        p.post_market_price,
        CASE WHEN rs.ticker IS NOT NULL THEN TRUE ELSE FALSE END AS has_spike
    FROM latest l
    LEFT JOIN ticker_prices p   ON l.ticker = p.ticker
    LEFT JOIN recent_spikes rs  ON l.ticker = rs.ticker
    WHERE l.article_count >= :min_articles
"""


def _fetch_data(window: str, min_articles: int) -> pd.DataFrame:
    diag = query_df("""
        SELECT
            (SELECT COUNT(*) FROM ticker_sentiment_summary)                 AS tss_total,
            (SELECT COUNT(DISTINCT "window") FROM ticker_sentiment_summary) AS tss_windows,
            (SELECT string_agg(DISTINCT "window", ', ' ORDER BY "window")
               FROM ticker_sentiment_summary)                               AS tss_window_values,
            (SELECT COUNT(*) FROM ticker_sentiment_summary
               WHERE "window" = :window)                                    AS tss_for_window,
            (SELECT COUNT(*) FROM ticker_prices)                            AS price_rows
    """, {"window": window})
    if not diag.empty:
        r = diag.iloc[0]
        logger.info(
            f"[screener] DB snapshot — tss_total={r.tss_total}, "
            f"windows={r.tss_window_values!r}, "
            f"tss_for_window({window!r})={r.tss_for_window}, "
            f"price_rows={r.price_rows}"
        )

    df = query_df(_SCREENER_SQL, {"window": window, "min_articles": min_articles})
    logger.info(
        f"[screener] _fetch_data(window={window!r}, min_articles={min_articles}) "
        f"→ {len(df)} rows"
    )
    return df


# ── Render helpers ────────────────────────────────────────────────────────

_SENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#131c38", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#351212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#280808", "color": "#ff5252", "bd": "#d50000"},
}


def _score_to_label(score: float | None) -> str:
    if score is None or math.isnan(float(score)):
        return "Neutral"
    s = float(score)
    if s >= 0.35:    return "Bullish"
    elif s >= 0.15:  return "Somewhat Bullish"
    elif s > -0.15:  return "Neutral"
    elif s > -0.35:  return "Somewhat Bearish"
    return "Bearish"


def _score_color(score: float | None) -> str:
    label = _score_to_label(score)
    return _SENT_STYLE.get(label, {}).get("color", "#888888")


def _badge(score) -> html.Span:
    label = _score_to_label(score)
    s = _SENT_STYLE.get(label, {})
    return html.Span(
        label,
        style={
            "background":   s.get("bg",    "#141414"),
            "color":        s.get("color", "#444"),
            "border":       f"1px solid {s.get('bd', '#282828')}",
            "borderRadius": "12px",
            "padding":      "2px 8px",
            "fontSize":     "11px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
            "maxWidth":     "130px",
            "overflow":     "hidden",
            "textOverflow": "ellipsis",
            "display":      "inline-block",
        },
    )


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


def _trend_icon(momentum) -> html.Span:
    m = str(momentum or "").lower()
    if m == "improving": return html.Span("↑", style={"color": "#00e676", "fontWeight": "700"})
    if m == "declining": return html.Span("↓", style={"color": "#ff5252", "fontWeight": "700"})
    return html.Span("→", style={"color": "#555555"})


def _pct(num, denom) -> str:
    try:
        n, d = int(num), int(denom)
        return f"{n/d*100:.0f}%" if d else "—"
    except (TypeError, ValueError):
        return "—"


# ── Table column definitions ──────────────────────────────────────────────
# (label, width, text-align)
_OV_COLS = [
    ("★",         "32px",  "center"),
    ("#",         "35px",  "right"),
    ("Ticker",    "70px",  "left"),
    ("Company",   "150px", "left"),
    ("Country",   "58px",  "center"),
    ("Mkt Cap",   "82px",  "right"),
    ("Price",     "80px",  "right"),
    ("Chg %",     "68px",  "right"),
    ("Volume",    "82px",  "right"),
    ("Articles",  "64px",  "right"),
    ("Sentiment", "150px", "center"),
    ("Score",     "60px",  "right"),
]

_SENT_COLS = [
    ("#",          "36px",  "right"),
    ("Ticker",     "70px",  "left"),
    ("Sentiment",  "130px", "left"),
    ("Score",      "60px",  "right"),
    ("Bull %",     "64px",  "right"),
    ("Bear %",     "64px",  "right"),
    ("Neutral %",  "78px",  "right"),
    ("Articles",   "66px",  "right"),
    ("Last Upd",   "108px", "center"),
    ("Trend",      "56px",  "center"),
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
        raw_ctry   = str(r.get("country") or "USA")
        country    = _COUNTRY_DISPLAY.get(
            raw_ctry,
            raw_ctry[:3].upper() if len(raw_ctry) > 3 else raw_ctry,
        )
        score      = _safe(r.get("avg_sentiment"))
        color      = _score_color(score)
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
            html.Td(country, className="scr-td scr-td-center scr-dim"),
            html.Td(_fmt_mktcap(r.get("market_cap")), className="scr-td scr-td-num"),
            html.Td(price_children, className="scr-td scr-td-num"),
            html.Td(chg_text, className="scr-td scr-td-num",
                    style={"color": chg_color}),
            html.Td(_fmt_volume(r.get("volume")), className="scr-td scr-td-num"),
            html.Td(str(int(r.get("article_count", 0))), className="scr-td scr-td-num"),
            html.Td(_badge(score), className="scr-td",
                    style={"textAlign": "center", "paddingLeft": "20px"}),
            html.Td(
                f"{score:+.2f}" if score is not None else "—",
                className="scr-td scr-td-num scr-mono",
                style={"color": color},
            ),
        ], className="scr-tr"))
    return rows


def _render_sentiment_rows(df: pd.DataFrame, page: int) -> list:
    if df.empty:
        return _no_results(len(_SENT_COLS))

    offset  = (page - 1) * PAGE_SIZE
    page_df = df.iloc[offset : offset + PAGE_SIZE]
    rows: list = []

    for local_i, (_, r) in enumerate(page_df.iterrows()):
        global_i = offset + local_i
        cnt      = int(r.get("article_count", 0))
        score    = _safe(r.get("avg_sentiment"))
        color    = _score_color(score)
        ticker   = r["ticker"]

        last_upd = ""
        try:
            ts = pd.Timestamp(r.get("calculated_at"))
            if ts is not pd.NaT:
                last_upd = ts.strftime("%m-%d %H:%M")
        except Exception:
            pass

        rows.append(html.Tr([
            html.Td(str(global_i + 1), className="scr-td scr-td-num scr-dim"),
            html.Td(
                dcc.Link(ticker, href=f"/stock/{ticker}", className="scr-ticker"),
                className="scr-td",
            ),
            html.Td(_badge(score), className="scr-td"),
            html.Td(
                f"{score:+.2f}" if score is not None else "—",
                className="scr-td scr-td-num scr-mono",
                style={"color": color},
            ),
            html.Td(_pct(r.get("bullish_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#00e676"}),
            html.Td(_pct(r.get("bearish_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#ff5252"}),
            html.Td(_pct(r.get("neutral_count"), cnt),
                    className="scr-td scr-td-num", style={"color": "#888888"}),
            html.Td(str(cnt), className="scr-td scr-td-num"),
            html.Td(last_upd, className="scr-td scr-td-center scr-dim"),
            html.Td(_trend_icon(r.get("momentum")),
                    className="scr-td scr-td-center",
                    style={"fontSize": "15px"}),
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

def _apply_signal(df: pd.DataFrame, signal: str) -> pd.DataFrame:
    if signal == "bullish":
        return df[df["avg_sentiment"] >= 0.15]
    if signal == "bearish":
        return df[df["avg_sentiment"] <= -0.15]
    if signal == "unusual_volume":
        if df["volume"].isna().all():
            return df
        median_v = df["volume"].median()
        return df[df["volume"].fillna(0) > median_v * 2] if median_v else df
    if signal == "spike":
        return df[df["has_spike"] == True]
    return df


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


def _apply_sent_label_filter(df: pd.DataFrame, label: str) -> pd.DataFrame:
    if not label or label == "all":
        return df
    df2 = df.copy()
    df2["_lbl"] = df2["avg_sentiment"].apply(_score_to_label)
    return df2[df2["_lbl"] == label].drop(columns=["_lbl"])


def _apply_sent_score_filter(df: pd.DataFrame, score: str) -> pd.DataFrame:
    if not score or score == "all":
        return df
    v = df["avg_sentiment"].fillna(0)
    masks = {
        "sbull": v > 0.6,
        "bull":  (v >= 0.3)  & (v <= 0.6),
        "neut":  (v > -0.3)  & (v < 0.3),
        "bear":  (v >= -0.6) & (v <= -0.3),
        "sbear": v < -0.6,
    }
    mask = masks.get(score)
    return df[mask] if mask is not None else df


def _apply_trend_filter(df: pd.DataFrame, trend: str) -> pd.DataFrame:
    if not trend or trend == "all":
        return df
    m = df["momentum"].fillna("").str.lower()
    if trend == "improving":
        return df[m == "improving"]
    if trend == "declining":
        return df[m == "declining"]
    if trend == "stable":
        return df[~m.isin(["improving", "declining"])]
    return df


def _apply_spike_filter(df: pd.DataFrame, spike: str) -> pd.DataFrame:
    if not spike or spike == "all":
        return df
    if spike == "spike":
        return df[df["has_spike"] == True]
    if spike == "no_spike":
        return df[df["has_spike"] != True]
    return df


def _apply_sort(df: pd.DataFrame, signal: str, order: str, sort_dir: str) -> pd.DataFrame:
    asc = (sort_dir == "asc")
    if signal == "most_articles":
        return df.sort_values("article_count", ascending=False, na_position="last")
    col_map = {
        "avg_sentiment": "avg_sentiment",
        "price":         "price",
        "change_pct":    "change_pct",
        "volume":        "volume",
        "market_cap":    "market_cap",
        "article_count": "article_count",
    }
    col = col_map.get(order, "avg_sentiment")
    if col in df.columns:
        return df.sort_values(col, ascending=asc, na_position="last")
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

_FTAB_IDS    = ["desc", "fund", "tech", "sent", "news", "ai"]
_FTAB_LABELS = ["Descriptive", "Fundamental", "Technical", "Sentiment", "News", "AI"]


def _fi(label: str, select_id: str, options: list, value) -> html.Div:
    return html.Div([
        html.Label(label, className="filter-label"),
        dbc.Select(id=select_id, options=options, value=value,
                   className="filter-select", style={**_SH}),
    ], className="filter-item")


def _ph(label: str) -> html.Div:
    return html.Div([
        html.Label(label, className="filter-label scr-ph-label"),
        dbc.Select(
            options=[{"label": "Coming Soon", "value": "all"}],
            value="all", disabled=True,
            className="filter-select scr-ph-select",
            style={**_SH},
        ),
    ], className="filter-item")

# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        # ── Stores ────────────────────────────────────────────────────────
        dcc.Interval(id="screener-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="screener-sort-dir",  data="desc"),
        dcc.Store(id="screener-page",      data=1),
        dcc.Store(id="watchlist-store",    data=0),

        # ── Top toolbar: Sort By | ↓ | Search | ↻ ─────────────────────────
        html.Div(
            className="filter-bar",
            style={"flexWrap": "nowrap", "marginBottom": "8px"},
            children=[
                dbc.Select(
                    id="screener-order",
                    options=ORDER_OPTIONS,
                    value="avg_sentiment",
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

            # ── Fundamental (placeholders) ────────────────────────────────
            html.Div(id="screener-ftab-fund-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    _ph("P/E"), _ph("Forward P/E"), _ph("PEG"), _ph("EPS Growth"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("Revenue Growth"), _ph("ROE"), _ph("ROA"), _ph("Gross Margin"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("Operating Margin"), _ph("Debt / Equity"),
                    _ph("Current Ratio"),    _ph("Dividend Yield"),
                ]),
            ]),

            # ── Technical (placeholders) ──────────────────────────────────
            html.Div(id="screener-ftab-tech-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    _ph("RSI"), _ph("SMA 20"), _ph("SMA 50"), _ph("SMA 200"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("52W High"), _ph("52W Low"), _ph("ATR"), _ph("Beta"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("Gap Up"), _ph("Gap Down"),
                ]),
            ]),

            # ── Sentiment ─────────────────────────────────────────────────
            html.Div(id="screener-ftab-sent-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    _fi("Sentiment",    "screener-sent-label",   SENT_LABEL_OPTIONS,   "all"),
                    _fi("Score Range",  "screener-sent-score",   SENT_SCORE_OPTIONS,   "all"),
                    _fi("Time Window",  "screener-window",       TIME_WINDOW_OPTIONS,  "4hr"),
                    _fi("Min Articles", "screener-min-articles", MIN_ARTICLES_OPTIONS, 0),
                ]),
                html.Div(className="filter-row", children=[
                    _fi("Trend",       "screener-trend", TREND_OPTIONS, "all"),
                    _fi("Spike Alert", "screener-spike", SPIKE_OPTIONS, "all"),
                    _ph("Most Bullish"),
                    _ph("Most Bearish"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("Highest Article Count"),
                    _ph("Largest Sentiment Change"),
                ]),
            ]),

            # ── News (placeholders) ───────────────────────────────────────
            html.Div(id="screener-ftab-news-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    _ph("Breaking News"), _ph("Earnings"),
                    _ph("SEC Filings"),   _ph("Press Releases"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("FDA"),              _ph("Insider Trading"),
                    _ph("Analyst Upgrades"), _ph("Analyst Downgrades"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("M&A"), _ph("IPO"), _ph("Macro"), _ph("Crypto"),
                ]),
            ]),

            # ── AI (placeholders) ─────────────────────────────────────────
            html.Div(id="screener-ftab-ai-panel", style={"display": "none"}, children=[
                html.Div(className="filter-row", children=[
                    _ph("AI Rating"), _ph("AI Confidence"),
                    _ph("Catalyst Type"), _ph("Catalyst Strength"),
                ]),
                html.Div(className="filter-row", children=[
                    _ph("High Conviction"),      _ph("Watchlist Candidate"),
                    _ph("Momentum Opportunity"), _ph("Risk Level"),
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

        dbc.Tabs(
            id="screener-result-tabs",
            active_tab="tab-overview",
            children=[
                dbc.Tab(label="Overview", tab_id="tab-overview", children=[
                    html.Div(
                        className="articles-table",
                        style={"overflowX": "auto", "padding": "0"},
                        children=[html.Table(
                            className="scr-table",
                            children=[
                                html.Thead(html.Tr([
                                    html.Th(lbl, style={
                                        "width": w, "textAlign": a,
                                        **({"paddingLeft": "20px"} if lbl == "Sentiment" else {}),
                                    })
                                    for lbl, w, a in _OV_COLS
                                ])),
                                html.Tbody(id="screener-overview-rows"),
                            ],
                        )],
                    ),
                ]),
                dbc.Tab(label="Sentiment Detail", tab_id="tab-sentiment", children=[
                    html.Div(
                        className="articles-table",
                        style={"overflowX": "auto", "padding": "0"},
                        children=[html.Table(
                            className="scr-table",
                            children=[
                                html.Thead(html.Tr([
                                    html.Th(lbl, style={
                                        "width": w, "textAlign": a,
                                        **({"paddingLeft": "20px"} if lbl == "Sentiment" else {}),
                                    })
                                    for lbl, w, a in _SENT_COLS
                                ])),
                                html.Tbody(id="screener-sentiment-rows"),
                            ],
                        )],
                    ),
                ]),
            ],
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
    Input("screener-order",        "value"),
    Input("screener-sort-dir",     "data"),
    Input("screener-search",       "value"),
    Input("screener-sector",       "value"),
    Input("screener-mktcap",       "value"),
    Input("screener-country",      "value"),
    Input("screener-price",        "value"),
    Input("screener-chg-pct",      "value"),
    Input("screener-volume",       "value"),
    Input("screener-avg-volume",   "value"),
    Input("screener-sent-label",   "value"),
    Input("screener-sent-score",   "value"),
    Input("screener-window",       "value"),
    Input("screener-min-articles", "value"),
    Input("screener-trend",        "value"),
    Input("screener-spike",        "value"),
]


@callback(
    Output("screener-page", "data"),
    *_ALL_FILTER_INPUTS,
    prevent_initial_call=True,
)
def _reset_screener_page(*_):
    return 1


@callback(
    Output("screener-sector",       "value"),
    Output("screener-mktcap",       "value"),
    Output("screener-country",      "value"),
    Output("screener-price",        "value"),
    Output("screener-chg-pct",      "value"),
    Output("screener-volume",       "value"),
    Output("screener-avg-volume",   "value"),
    Output("screener-sent-label",   "value"),
    Output("screener-sent-score",   "value"),
    Output("screener-window",       "value"),
    Output("screener-min-articles", "value"),
    Output("screener-trend",        "value"),
    Output("screener-spike",        "value"),
    Output("screener-industry",     "value"),
    Output("screener-exchange",     "value"),
    Output("screener-order",        "value"),
    Output("screener-search",       "value"),
    Input("screener-reset-btn",     "n_clicks"),
    prevent_initial_call=True,
)
def _reset_filters(_):
    return ("all", "all", "all", "all", "all", "all", "all",
            "all", "all", "4hr", 0, "all", "all",
            "all", "all", "avg_sentiment", "")


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
    Output("screener-overview-rows",  "children"),
    Output("screener-sentiment-rows", "children"),
    Output("screener-count",          "children"),
    Output("screener-pagination",     "children"),
    Input("screener-interval",        "n_intervals"),
    Input("screener-refresh-btn",     "n_clicks"),
    *_ALL_FILTER_INPUTS,
    Input("screener-page",            "data"),
    Input("url",                      "pathname"),
    Input("watchlist-store",          "data"),
)
def _update_screener(n, refresh_clicks,
                     order, sort_dir, search,
                     sector, mktcap, country, price, chg_pct, volume, avg_volume,
                     sent_label, sent_score, window, min_articles, trend, spike,
                     page, pathname, _wl_version):
    if pathname not in (None, "/screener"):
        raise PreventUpdate

    order        = order    or "avg_sentiment"
    sort_dir     = sort_dir or "desc"
    window       = window   or "4hr"
    min_articles = int(min_articles or 0)
    page         = max(1, int(page or 1))

    df = _fetch_data(window, min_articles)

    if df.empty:
        empty_msg = [html.Div(
            "No data yet — articles are being collected. Check back in a few minutes.",
            style={"padding": "32px", "textAlign": "center",
                   "color": "#555", "fontSize": "14px"},
        )]
        return empty_msg, empty_msg, "0 tickers", []

    if search:
        df = df[df["ticker"].str.upper().str.startswith(search.strip().upper())]

    df = _apply_sector_filter(df, sector or "all")
    df = _apply_country_filter(df, country or "all")
    df = _apply_mktcap_filter(df, mktcap or "all")
    df = _apply_price_filter(df, price or "all")
    df = _apply_chg_pct_filter(df, chg_pct or "all")
    df = _apply_volume_filter(df, volume or "all")
    df = _apply_avg_volume_filter(df, avg_volume or "all")
    df = _apply_sent_label_filter(df, sent_label or "all")
    df = _apply_sent_score_filter(df, sent_score or "all")
    df = _apply_trend_filter(df, trend or "all")
    df = _apply_spike_filter(df, spike or "all")

    df = _apply_sort(df, "all", order, sort_dir)
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
        _render_sentiment_rows(df, page),
        count_el,
        _render_pagination(page, total_pages),
    )
