"""
Stock Workspace — per-ticker detail page at /stock/<TICKER>.

Tab structure is designed for future expansion. To add a tab:
  1. Add an entry to _TABS (id_suffix, label).
  2. Write a panel renderer _render_<id_suffix>_panel(ticker).
  3. Add the panel Div to layout().
  4. Add its id to the tab-visibility callback outputs.
No other file ever needs to change — the Screener just links to /stock/{ticker}.
"""
from __future__ import annotations

import math

import dash
import pandas as pd
from dash import Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import query_df

dash.register_page(
    __name__,
    path_template="/stock/<ticker>",
    name="Stock",
    title="Stock Workspace",
)

# ── SQL ───────────────────────────────────────────────────────────────────

_PRICE_SQL = """
    SELECT price, change_pct, updated_at, company_name, sector,
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
    SELECT title, source_name, ingested_at, sentiment_label, url, importance_label
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '3 days'
    ORDER BY ingested_at DESC
    LIMIT 8
"""

# ── Tab registry ──────────────────────────────────────────────────────────
# Add a tab by appending (id_suffix, display_label) here.
_TABS: list[tuple[str, str]] = [
    ("overview",   "Overview"),
    ("news",       "News"),
    ("charts",     "Charts"),
    ("financials", "Financials"),
    ("ai",         "AI Analysis"),
]

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


def _fmt_mktcap(v) -> str:
    v = _safe(v)
    if v is None:    return "—"
    if v >= 1e12:    return f"${v/1e12:.2f}T"
    if v >= 1e9:     return f"${v/1e9:.1f}B"
    if v >= 1e6:     return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _score_label(score: float | None) -> str:
    if score is None:   return "Neutral"
    if score >= 0.35:   return "Bullish"
    if score >= 0.15:   return "Somewhat Bullish"
    if score > -0.15:   return "Neutral"
    if score > -0.35:   return "Somewhat Bearish"
    return "Bearish"


_SENT_COLOR: dict[str, str] = {
    "Bullish":          "#00e676",
    "Somewhat Bullish": "#69f0ae",
    "Neutral":          "#82b1ff",
    "Somewhat Bearish": "#ff8a80",
    "Bearish":          "#ff5252",
}

_IMP_DOT: dict[str, str] = {"High": "🔴", "Medium": "🟡", "Low": "⚪"}


# ── Panel renderers ───────────────────────────────────────────────────────

def _render_overview_panel(ticker: str) -> html.Div:
    price_df = query_df(_PRICE_SQL,    {"ticker": ticker})
    sent_df  = query_df(_SENTIMENT_SQL, {"ticker": ticker})
    news_df  = query_df(_RECENT_NEWS_SQL, {"ticker": ticker})

    cards: list = []

    if not price_df.empty:
        r     = price_df.iloc[0]
        price = _safe(r.get("price"))
        chg   = _safe(r.get("change_pct"))

        chg_color = "#00e676" if (chg or 0) >= 0 else "#ff5252"
        chg_str   = (f"{'+'if (chg or 0)>=0 else ''}{chg:.2f}%"
                     if chg is not None else "—")

        cards.append(html.Div([
            html.Div("Price",         className="stock-card-label"),
            html.Div(_fmt_price(price), className="stock-card-value"),
            html.Div(chg_str,         className="stock-card-sub",
                     style={"color": chg_color}),
        ], className="stock-card"))

        cards.append(html.Div([
            html.Div("Market Cap",                  className="stock-card-label"),
            html.Div(_fmt_mktcap(r.get("market_cap")), className="stock-card-value"),
            html.Div(str(r.get("sector") or "—"),   className="stock-card-sub"),
        ], className="stock-card"))

        cards.append(html.Div([
            html.Div("Exchange",                      className="stock-card-label"),
            html.Div(str(r.get("exchange") or "—"),   className="stock-card-value"),
            html.Div(str(r.get("country") or "—"),    className="stock-card-sub"),
        ], className="stock-card"))

    if not sent_df.empty:
        s     = sent_df.iloc[0]
        score = _safe(s.get("avg_sentiment"))
        label = _score_label(score)
        color = _SENT_COLOR.get(label, "#888")
        total = int(s.get("article_count", 0)) or 1
        bull  = int(s.get("bullish_count", 0))
        bear  = int(s.get("bearish_count", 0))

        cards.append(html.Div([
            html.Div("24h Sentiment", className="stock-card-label"),
            html.Div(label,           className="stock-card-value",
                     style={"color": color, "fontSize": "14px"}),
            html.Div(
                f"{score:+.3f}  ·  {int(s.get('article_count', 0))} articles"
                if score is not None else "—",
                className="stock-card-sub",
            ),
        ], className="stock-card"))

        momentum = str(s.get("momentum") or "stable").capitalize()
        mom_color = ("#00e676" if momentum == "Improving"
                     else "#ff5252" if momentum == "Declining" else "#555")
        cards.append(html.Div([
            html.Div("Trend",    className="stock-card-label"),
            html.Div(momentum,   className="stock-card-value",
                     style={"color": mom_color, "fontSize": "14px"}),
            html.Div(
                f"Bull {bull/total*100:.0f}%  ·  Bear {bear/total*100:.0f}%",
                className="stock-card-sub",
            ),
        ], className="stock-card"))

    if not cards:
        cards = [html.Div(
            f"No data available yet for {ticker}. "
            "The pipeline may still be collecting articles.",
            style={"color": "#444", "fontSize": "13px", "padding": "24px 0",
                   "gridColumn": "1 / -1"},
        )]

    # Recent news preview
    news_rows: list = []
    for _, n in news_df.iterrows():
        imp       = str(n.get("importance_label") or "")
        dot       = _IMP_DOT.get(imp, "")
        title     = str(n.get("title") or "")
        url       = str(n.get("url") or "#")
        sent_lbl  = str(n.get("sentiment_label") or "")
        sent_color = _SENT_COLOR.get(sent_lbl, "#555")
        ts = ""
        try:
            ts = pd.Timestamp(n.get("ingested_at")).strftime("%m-%d %H:%M")
        except Exception:
            pass

        news_rows.append(html.Div([
            html.Span(ts,  style={"color": "#3a3a3a", "fontSize": "10px",
                                  "fontFamily": "monospace", "flexShrink": "0"}),
            html.Span(f"{dot} ", style={"fontSize": "10px", "flexShrink": "0"}),
            html.A(title, href=url, target="_blank", className="stock-news-title"),
            html.Span(sent_lbl, style={"color": sent_color, "fontSize": "10px",
                                       "marginLeft": "auto", "flexShrink": "0"}),
        ], className="stock-news-row"))

    news_section = html.Div([
        html.Div("Recent News", className="stock-section-label"),
        html.Div(
            news_rows or [html.Div("No recent articles.",
                                   style={"color": "#444", "fontSize": "12px",
                                          "padding": "12px 0"})],
            className="stock-news-list",
        ),
    ], className="stock-section")

    return html.Div([
        html.Div(cards, className="stock-card-grid"),
        news_section,
    ])


def _placeholder_panel(icon: str, title: str, description: str) -> html.Div:
    return html.Div([
        html.Div(icon,        className="stock-ph-icon"),
        html.Div(title,       className="stock-ph-title"),
        html.Div(description, className="stock-ph-sub"),
    ], className="stock-ph")


# ── Layout ────────────────────────────────────────────────────────────────

def layout(ticker: str = "", **kwargs) -> html.Div:
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return html.Div(
            "No ticker specified.",
            className="page-content",
            style={"padding": "24px", "color": "#555"},
        )

    price_df = query_df(_PRICE_SQL, {"ticker": ticker})
    company  = (
        str(price_df.iloc[0].get("company_name") or "")
        if not price_df.empty else ""
    )

    return html.Div(
        className="page-content",
        children=[
            dcc.Store(id="stock-ticker-store", data=ticker),
            dcc.Interval(id="stock-interval", interval=60_000, n_intervals=0),
            dcc.Store(id="stock-active-tab",  data="overview"),

            # ── Header ────────────────────────────────────────────────────
            html.Div(className="stock-header", children=[
                dcc.Link("← Screener", href="/screener",
                         className="stock-back-link"),
                html.Span("|", style={"color": "#222", "margin": "0 10px",
                                      "userSelect": "none"}),
                html.Span(ticker,  className="stock-ticker-hero"),
                html.Span(company, id="stock-company-name",
                          className="stock-company-name"),
            ]),

            # ── Tab strip ─────────────────────────────────────────────────
            html.Div(className="stock-tab-strip", children=[
                html.Button(
                    label,
                    id=f"stock-tab-{sid}",
                    n_clicks=0,
                    className=(
                        "stock-tab stock-tab-active"
                        if sid == "overview" else "stock-tab"
                    ),
                )
                for sid, label in _TABS
            ]),

            html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 18px",
                           "opacity": "1"}),

            # ── Tab panels (all in DOM; shown/hidden via style) ────────────
            html.Div(id="stock-panel-overview",
                     children=_render_overview_panel(ticker)),

            html.Div(id="stock-panel-news", style={"display": "none"},
                     children=_placeholder_panel(
                         "📰", "News Feed",
                         "Filtered live feed for this ticker — headline, "
                         "importance badge, sentiment, and source.",
                     )),

            html.Div(id="stock-panel-charts", style={"display": "none"},
                     children=_placeholder_panel(
                         "📈", "Price Charts",
                         "TradingView embed with the ticker pre-loaded, "
                         "technical overlays, and volume profile.",
                     )),

            html.Div(id="stock-panel-financials", style={"display": "none"},
                     children=_placeholder_panel(
                         "📊", "Financials",
                         "Key metrics — revenue, EPS, P/E, margins — "
                         "sourced from SEC filings.",
                     )),

            html.Div(id="stock-panel-ai", style={"display": "none"},
                     children=_placeholder_panel(
                         "🤖", "AI Analysis",
                         "LLM-generated investment thesis, risk summary, "
                         "and catalyst tracking for this ticker.",
                     )),
        ],
    )


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("stock-active-tab", "data"),
    *[Input(f"stock-tab-{sid}", "n_clicks") for sid, _ in _TABS],
    prevent_initial_call=True,
)
def _switch_tab(*_):
    triggered = ctx.triggered_id or ""
    return triggered.replace("stock-tab-", "") or "overview"


@callback(
    *[Output(f"stock-panel-{sid}", "style") for sid, _ in _TABS],
    *[Output(f"stock-tab-{sid}", "className") for sid, _ in _TABS],
    Input("stock-active-tab", "data"),
)
def _update_tab_visibility(active: str) -> list:
    panel_styles = [
        {} if sid == active else {"display": "none"}
        for sid, _ in _TABS
    ]
    tab_classes = [
        "stock-tab stock-tab-active" if sid == active else "stock-tab"
        for sid, _ in _TABS
    ]
    return panel_styles + tab_classes


@callback(
    Output("stock-panel-overview", "children"),
    Output("stock-company-name",   "children"),
    Input("stock-interval",        "n_intervals"),
    State("stock-ticker-store",    "data"),
    Input("url",                   "pathname"),
    prevent_initial_call=True,
)
def _refresh_overview(_, ticker, pathname):
    if not pathname or not pathname.startswith("/stock/"):
        raise PreventUpdate
    if not ticker:
        raise PreventUpdate
    price_df = query_df(_PRICE_SQL, {"ticker": ticker})
    company  = (
        str(price_df.iloc[0].get("company_name") or "")
        if not price_df.empty else ""
    )
    return _render_overview_panel(ticker), company
