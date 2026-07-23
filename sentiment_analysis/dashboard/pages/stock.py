"""
Stock Workspace — per-ticker research page at /stock/<TICKER>.

Single scrollable page. No tabs. All sections visible on load.
"""
from __future__ import annotations

import math

import dash
import pandas as pd
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import (
    query_df,
    watchlist_add,
    watchlist_remove,
    watchlist_tickers,
)
from sentiment_analysis.dashboard.formatters import _fmt_mktcap

dash.register_page(
    __name__,
    path_template="/stock/<ticker>",
    name="Stock",
    title="Stock Workspace",
)

# ── SQL ───────────────────────────────────────────────────────────────────

_PRICE_SQL = """
    SELECT price, change_pct, volume, updated_at, company_name, sector,
           country, exchange, market_cap, pre_market_price, post_market_price
    FROM ticker_prices
    WHERE ticker = :ticker
"""

_SENTIMENT_SQL = """
    SELECT avg_sentiment, article_count, bullish_count, bearish_count,
           neutral_count, momentum, calculated_at
    FROM ticker_sentiment_summary
    WHERE ticker = :ticker AND "window" = '24hr'
    ORDER BY calculated_at DESC
    LIMIT 1
"""

_RECENT_NEWS_SQL = """
    SELECT title, source_name, ingested_at, sentiment_label, url
    FROM rss_articles
    WHERE primary_ticker = :ticker
      AND ingested_at > NOW() - INTERVAL '7 days'
    ORDER BY ingested_at DESC
    LIMIT 20
"""

# ── Helpers ───────────────────────────────────────────────────────────────

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
        return "—", "#666"
    color = "#00e676" if v >= 0 else "#ff5252"
    sign  = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%", color



def _fmt_volume(v) -> str:
    v = _safe(v)
    if v is None:   return "—"
    if v >= 1e9:    return f"{v/1e9:.2f}B"
    if v >= 1e6:    return f"{v/1e6:.2f}M"
    if v >= 1e3:    return f"{v/1e3:.1f}K"
    return str(int(v))


def _fmt_ts(val) -> str:
    try:
        return pd.Timestamp(val).strftime("%b %d, %Y  %I:%M %p ET")
    except Exception:
        return "—"


def _score_label(score: float | None) -> str:
    if score is None:  return "Neutral"
    if score >= 0.35:  return "Bullish"
    if score >= 0.15:  return "Somewhat Bullish"
    if score > -0.15:  return "Neutral"
    if score > -0.35:  return "Somewhat Bearish"
    return "Bearish"


_SENT_COLOR: dict[str, str] = {
    "Bullish":          "#00e676",
    "Somewhat Bullish": "#69f0ae",
    "Neutral":          "#82b1ff",
    "Somewhat Bearish": "#ff8a80",
    "Bearish":          "#ff5252",
}

_SENT_BG: dict[str, str] = {
    "Bullish":          "#0d3324",
    "Somewhat Bullish": "#092b18",
    "Neutral":          "#131c38",
    "Somewhat Bearish": "#351212",
    "Bearish":          "#280808",
}

_SENT_BORDER: dict[str, str] = {
    "Bullish":          "#00c853",
    "Somewhat Bullish": "#00e676",
    "Neutral":          "#3d5afe",
    "Somewhat Bearish": "#ff5252",
    "Bearish":          "#d50000",
}


def _tv_src(ticker: str) -> str:
    return (
        "https://www.tradingview.com/widgetembed/"
        f"?symbol={ticker.upper()}"
        "&interval=D"
        "&theme=dark&style=1&locale=en"
        "&toolbar_bg=%23131722"
        "&enable_publishing=false"
        "&hide_top_toolbar=false&hide_legend=false"
        "&save_image=false&hide_side_toolbar=false"
        "&allow_symbol_change=false&studies=%5B%5D"
    )


# ── UI building blocks ────────────────────────────────────────────────────

def _section(title: str, children: list) -> html.Div:
    return html.Div(className="sw-section", children=[
        html.Div(title, className="sw-section-title"),
        *[c for c in children if c is not None],
    ])


def _coming_soon(title: str) -> html.Div:
    return _section(title, [
        html.Div(className="sw-coming-soon", children=[
            html.Div("Coming Soon", className="sw-cs-label"),
            html.Div(
                f"{title} data will appear here when available.",
                className="sw-cs-sub",
            ),
        ]),
    ])


def _metric(label: str, value: str, sub: str = "") -> html.Div:
    return html.Div(className="sw-metric-card", children=[
        html.Div(label, className="sw-metric-label"),
        html.Div(value, className="sw-metric-value"),
        html.Div(sub,   className="sw-metric-sub") if sub else None,
    ])


def _sent_badge(label: str) -> html.Span:
    return html.Span(
        label,
        style={
            "background":   _SENT_BG.get(label, "#141414"),
            "color":        _SENT_COLOR.get(label, "#888"),
            "border":       f"1px solid {_SENT_BORDER.get(label, '#282828')}",
            "borderRadius": "12px",
            "padding":      "3px 12px",
            "fontSize":     "13px",
            "fontWeight":   "600",
        },
    )


# ── Full page renderer ────────────────────────────────────────────────────

def _render_page(ticker: str) -> html.Div:
    price_df = query_df(_PRICE_SQL,       {"ticker": ticker})
    sent_df  = query_df(_SENTIMENT_SQL,   {"ticker": ticker})
    news_df  = query_df(_RECENT_NEWS_SQL, {"ticker": ticker})

    p = price_df.iloc[0].to_dict() if not price_df.empty else {}
    s = sent_df.iloc[0].to_dict()  if not sent_df.empty  else {}

    # ── Derived price fields ──────────────────────────────────────────────
    price        = _safe(p.get("price"))
    chg          = _safe(p.get("change_pct"))
    company      = str(p.get("company_name") or "")
    sector       = str(p.get("sector")       or "")
    country      = str(p.get("country")      or "")
    exchange_val = str(p.get("exchange")     or "")
    market_cap   = p.get("market_cap")
    volume       = p.get("volume")
    updated_at   = p.get("updated_at")

    chg_str, chg_color = _fmt_chg(chg)
    upd_str = _fmt_ts(updated_at)

    # ── Derived sentiment fields ──────────────────────────────────────────
    avg_score  = _safe(s.get("avg_sentiment"))
    art_count  = int(s.get("article_count", 0))
    bull_count = int(s.get("bullish_count", 0))
    bear_count = int(s.get("bearish_count", 0))
    neut_count = int(s.get("neutral_count", 0))
    momentum   = str(s.get("momentum") or "stable").capitalize() if s else "—"
    sent_label = _score_label(avg_score)
    sent_color = _SENT_COLOR.get(sent_label, "#888")
    total      = art_count or 1
    bull_pct   = bull_count / total * 100
    bear_pct   = bear_count / total * 100
    neut_pct   = neut_count / total * 100
    mom_color  = ("#00e676" if momentum == "Improving"
                  else "#ff5252" if momentum == "Declining" else "#555")

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 1 — Header
    # ─────────────────────────────────────────────────────────────────────

    tags = [t for t in [sector, exchange_val, country] if t]

    in_watchlist = ticker in set(watchlist_tickers())
    wl_label = "⭐ Watching" if in_watchlist else "☆ Watch"
    wl_cls   = "sw-wl-btn sw-wl-btn-active" if in_watchlist else "sw-wl-btn"

    header = html.Div(className="sw-header", children=[
        html.Div(className="sw-header-top", children=[
            dcc.Link("← Screener", href="/screener", className="stock-back-link"),
            html.Div(className="sw-header-top-right", children=[
                html.Button(
                    wl_label,
                    id="stock-wl-btn",
                    className=wl_cls,
                    n_clicks=0,
                ),
                html.Div(className="sw-price-block", children=[
                    html.Span(_fmt_price(price), className="sw-price"),
                    html.Span(chg_str, className="sw-chg",
                              style={"color": chg_color}),
                ]),
            ]),
        ]),
        html.Div(className="sw-header-body", children=[
            html.Span(ticker,  className="stock-ticker-hero"),
            html.Span(company, className="stock-company-name",
                      id="stock-company-name"),
        ]),
        html.Div(className="sw-header-foot", children=[
            html.Div(
                [html.Span(t, className="sw-tag") for t in tags],
                className="sw-tag-row",
            ) if tags else None,
            html.Span(
                f"Updated {upd_str}" if upd_str != "—" else "",
                className="sw-updated",
            ),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 2 — Price Chart
    # ─────────────────────────────────────────────────────────────────────

    chart = _section("Price Chart", [
        html.Div(
            style={"width": "100%", "height": "520px"},
            children=[html.Iframe(
                src=_tv_src(ticker),
                style={"width": "100%", "height": "100%",
                       "border": "none", "display": "block"},
            )],
        ),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 3 — Key Metrics
    # ─────────────────────────────────────────────────────────────────────

    metrics = _section("Key Metrics", [
        html.Div(className="sw-metric-grid", children=[
            _metric("Price",          _fmt_price(price),
                    chg_str if chg is not None else ""),
            _metric("Market Cap",     _fmt_mktcap(market_cap)),
            _metric("Volume",         _fmt_volume(volume)),
            _metric("24h Sentiment",  sent_label,
                    f"Score {avg_score:+.3f}" if avg_score is not None else "—"),
            _metric("Article Count",  str(art_count)),
            _metric("Momentum",       momentum),
            _metric("Sector",         sector       or "—"),
            _metric("Exchange",       exchange_val or "—"),
            _metric("Country",        country      or "—"),
            _metric("Last Updated",   upd_str),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 4 — Recent News
    # ─────────────────────────────────────────────────────────────────────

    news_rows: list = []
    for _, n in news_df.iterrows():
        title    = str(n.get("title")          or "")
        url      = str(n.get("url")            or "#")
        source   = str(n.get("source_name")   or "")
        sent_lbl = str(n.get("sentiment_label") or "")
        sc       = _SENT_COLOR.get(sent_lbl, "#555")
        ts = _fmt_ts(n.get("ingested_at"))

        news_rows.append(html.Div(className="sw-news-row", children=[
            html.Div(className="sw-news-meta", children=[
                html.Span(source, className="sw-news-source"),
                html.Span(ts,     className="sw-news-ts"),
                html.Span(sent_lbl, className="sw-news-sent",
                          style={"color": sc}) if sent_lbl else None,
            ]),
            html.A(title, href=url, target="_blank",
                   rel="noopener noreferrer",
                   className="sw-news-title"),
        ]))

    news_section = _section("Recent News", [
        html.Div(
            news_rows or [
                html.Div("No recent articles.",
                         style={"color": "#444", "fontSize": "13px",
                                "padding": "24px 0"})
            ],
            className="sw-news-list",
        ),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 5 — Sentiment Analytics
    # ─────────────────────────────────────────────────────────────────────

    def _bar_row(label: str, count: int, pct: float, color: str) -> html.Div:
        return html.Div(className="sw-bar-row", children=[
            html.Span(label, className="sw-bar-label"),
            html.Div(className="sw-bar-track", children=[
                html.Div(className="sw-bar-fill",
                         style={"width": f"{pct:.1f}%",
                                "background": color}),
            ]),
            html.Span(f"{count}  ({pct:.0f}%)", className="sw-bar-count"),
        ])

    sentiment_section = _section("Sentiment Analytics", [
        html.Div(className="sw-sent-grid", children=[
            html.Div(className="sw-sent-overall", children=[
                html.Div("24h Sentiment", className="sw-metric-label"),
                _sent_badge(sent_label),
                html.Div(
                    f"Score: {avg_score:+.4f}" if avg_score is not None else "Score: —",
                    className="sw-metric-sub",
                    style={"marginTop": "8px"},
                ),
            ]),
            html.Div(className="sw-sent-breakdown", children=[
                _bar_row("Bullish", bull_count, bull_pct, "#00e676"),
                _bar_row("Neutral", neut_count, neut_pct, "#82b1ff"),
                _bar_row("Bearish", bear_count, bear_pct, "#ff5252"),
                html.Div(
                    f"Based on {art_count} article{'s' if art_count != 1 else ''}"
                    " in the past 24 hours",
                    className="sw-bar-footnote",
                ),
            ]),
            html.Div(className="sw-sent-trend", children=[
                html.Div("Momentum", className="sw-metric-label"),
                html.Div(momentum,   className="sw-metric-value",
                         style={"color": mom_color}),
                html.Div("Sentiment direction trend",
                         className="sw-metric-sub"),
            ]),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 6 — Company Information
    # ─────────────────────────────────────────────────────────────────────

    info_rows = [
        ("Company",  company      or "—"),
        ("Ticker",   ticker),
        ("Sector",   sector       or "—"),
        ("Exchange", exchange_val or "—"),
        ("Country",  country      or "—"),
    ]

    co_info = _section("Company Information", [
        html.Div(className="sw-info-table", children=[
            html.Div(className="sw-info-row", children=[
                html.Span(k, className="sw-info-key"),
                html.Span(v, className="sw-info-val"),
            ])
            for k, v in info_rows
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # Placeholder sections 7–11
    # ─────────────────────────────────────────────────────────────────────

    return html.Div([
        header,
        chart,
        metrics,
        news_section,
        sentiment_section,
        co_info,
        _coming_soon("Financial Highlights"),
        _coming_soon("Technical Indicators"),
        _coming_soon("Insider Activity"),
        _coming_soon("Analyst Ratings"),
        _coming_soon("SEC Filings"),
    ])


# ── Layout ────────────────────────────────────────────────────────────────

def layout(ticker: str = "", **kwargs) -> html.Div:
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return html.Div(
            "No ticker specified.",
            className="page-content",
            style={"padding": "24px", "color": "#555"},
        )

    return html.Div(
        className="page-content",
        children=[
            dcc.Store(id="stock-ticker-store", data=ticker),
            dcc.Interval(id="stock-interval", interval=60_000, n_intervals=0),
            html.Div(id="stock-page-body", children=_render_page(ticker)),
        ],
    )


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("stock-page-body",  "children"),
    Input("stock-interval",    "n_intervals"),
    State("stock-ticker-store", "data"),
    Input("url",               "pathname"),
    prevent_initial_call=True,
)
def _refresh_page(_, ticker, pathname):
    if not pathname or not pathname.startswith("/stock/"):
        raise PreventUpdate
    if not ticker:
        raise PreventUpdate
    return _render_page(ticker)


@callback(
    Output("stock-wl-btn", "children"),
    Output("stock-wl-btn", "className"),
    Input("stock-wl-btn",          "n_clicks"),
    State("stock-ticker-store",    "data"),
    prevent_initial_call=True,
)
def _toggle_watchlist(_, ticker):
    if not ticker:
        raise PreventUpdate
    watched = set(watchlist_tickers())
    if ticker in watched:
        watchlist_remove(ticker)
        return "☆ Watch", "sw-wl-btn"
    else:
        watchlist_add(ticker)
        return "⭐ Watching", "sw-wl-btn sw-wl-btn-active"
