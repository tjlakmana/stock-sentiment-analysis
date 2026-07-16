"""
Momentum — stock detail page: TradingView chart + news/sentiment columns.
"""
from __future__ import annotations

import concurrent.futures
import math

import dash
import pandas as pd
import plotly.graph_objects as go
import pytz
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df

dash.register_page(__name__, path="/momentum", name="Momentum", title="Momentum Scanner")

try:
    from sentiment_analysis.dashboard.pages.news import _source_chip
except ImportError:
    def _source_chip(source: str):  # type: ignore[misc]
        return html.Span(
            (source[:3] or "—").upper(),
            style={"fontSize": "10px", "color": "#555", "background": "#1c1c1c",
                   "border": "1px solid #282828", "borderRadius": "3px",
                   "padding": "1px 5px", "flexShrink": "0"},
        )

_ET = pytz.timezone("America/New_York")

# ── Constants ─────────────────────────────────────────────────────────────

_TIMEOUT = 10

# ── SQL ───────────────────────────────────────────────────────────────────

_PRICE_SQL = """
    SELECT price, change_pct, updated_at,
           company_name, sector, country, exchange
    FROM ticker_prices
    WHERE ticker = :ticker
"""

_HEADLINES_SQL = """
    SELECT title, source_name, ingested_at, sentiment_label, url
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '2 days'
    ORDER BY ingested_at DESC
    LIMIT 15
"""

_SENTIMENT_SQL = """
    SELECT avg_sentiment, article_count, bullish_count, bearish_count,
           neutral_count, calculated_at
    FROM ticker_sentiment_summary
    WHERE ticker = :ticker
      AND "window" = '24hr'
    ORDER BY calculated_at DESC
    LIMIT 1
"""

_SPARKLINE_SQL = """
    SELECT avg_sentiment, calculated_at
    FROM ticker_sentiment_summary
    WHERE ticker = :ticker
      AND "window" = '24hr'
      AND calculated_at > NOW() - INTERVAL '48 hours'
    ORDER BY calculated_at ASC
"""

# ── Sentiment styling ─────────────────────────────────────────────────────

_SENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#111827", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#2d1212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#1e0808", "color": "#ff5252", "bd": "#d50000"},
}

# ── Helpers ───────────────────────────────────────────────────────────────


def _safe(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _pct(num, denom) -> str:
    try:
        n, d = int(num), int(denom)
        return f"{n / d * 100:.0f}%" if d else "—"
    except (TypeError, ValueError):
        return "—"


def _score_to_label(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Neutral"
    if math.isnan(s): return "Neutral"
    if s >= 0.35:     return "Bullish"
    if s >= 0.15:     return "Somewhat Bullish"
    if s > -0.15:     return "Neutral"
    if s > -0.35:     return "Somewhat Bearish"
    return "Bearish"


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
        "&allow_symbol_change=true&studies=%5B%5D"
    )


def _to_et(dt_val):
    dt = pd.Timestamp(dt_val)
    if dt.tzinfo is None:
        return dt.tz_localize("UTC").tz_convert(_ET)
    return dt.tz_convert(_ET)


def _fmt_ts_hl(dt_val) -> str:
    try:
        dt = _to_et(dt_val)
        if dt.date() == now_et().date():
            return f"Today {dt.strftime('%I:%M%p')}"
        return dt.strftime("%b %d %I:%M%p")
    except Exception:
        return ""


def _fmt_ts_price(dt_val) -> str:
    try:
        dt = _to_et(dt_val)
        return dt.strftime("%b %-d • %I:%M%p ET")
    except Exception:
        try:
            dt = _to_et(dt_val)
            return dt.strftime("%b %d • %I:%M%p ET").replace(" 0", " ")
        except Exception:
            return "—"


def _build_infobar(ticker: str, df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []

    row      = df.iloc[0]
    price    = _safe(row.get("price"))
    chg_pct  = _safe(row.get("change_pct"))

    price_str = f"{price:.2f}" if price is not None else "—"

    if price is not None and chg_pct is not None:
        chg_dollar = price * chg_pct / 100
        if chg_pct >= 0:
            chg_str = f"+${chg_dollar:.2f} ({chg_pct:.2f}%)"
        else:
            chg_str = f"-${abs(chg_dollar):.2f} ({chg_pct:.2f}%)"
        chg_col = "#00ff88" if chg_pct >= 0 else "#ff4444"
    else:
        chg_str = "—"
        chg_col = "#888888"

    dt_str       = _fmt_ts_price(row.get("updated_at"))
    company_name = str(row.get("company_name") or ticker)
    sector       = str(row.get("sector")   or "")
    country      = str(row.get("country")  or "")
    exchange     = str(row.get("exchange") or "")
    tags         = " · ".join(x for x in [sector, country, exchange] if x)

    return [
        html.Div(
            style={"display": "flex", "alignItems": "center", "padding": "12px 0"},
            children=[
                html.Span(price_str, style={
                    "fontSize": "36px", "fontWeight": "bold", "color": "white",
                }),
                html.Div(
                    style={"marginLeft": "15px"},
                    children=[
                        html.Div(dt_str,  style={"fontSize": "13px", "color": "#888888"}),
                        html.Div(chg_str, style={"fontSize": "14px", "color": chg_col}),
                    ],
                ),
            ],
        ),
        html.Div(company_name, style={
            "fontSize": "14px", "fontWeight": "600",
            "color": "#cccccc", "marginBottom": "2px",
        }),
        html.Div(tags, style={"fontSize": "12px", "color": "#666666", "marginBottom": "8px"}),
    ]


def _parallel_queries(ticker: str) -> dict[str, pd.DataFrame]:
    queries = {
        "hl":    (_HEADLINES_SQL, {"ticker": ticker}),
        "sent":  (_SENTIMENT_SQL, {"ticker": ticker}),
        "spark": (_SPARKLINE_SQL, {"ticker": ticker}),
    }
    results: dict[str, pd.DataFrame] = {k: pd.DataFrame() for k in queries}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fs = {pool.submit(query_df, sql, p): k for k, (sql, p) in queries.items()}
        done, _ = concurrent.futures.wait(fs, timeout=_TIMEOUT)
        for f in done:
            try:
                results[fs[f]] = f.result()
            except Exception:
                pass
    return results

# ── Panel builders ────────────────────────────────────────────────────────


def _build_headlines(hl_df: pd.DataFrame) -> list:
    if hl_df.empty:
        return [html.Div("No recent news for this ticker",
                         style={"color": "#444", "fontSize": "12px", "padding": "16px 0"})]
    items = []
    for row in hl_df.to_dict("records"):
        label    = row.get("sentiment_label") or "Neutral"
        bd_color = _SENT_STYLE.get(label, {}).get("bd", "#282828")
        items.append(html.Div(
            className="mom-hl-item",
            style={"borderLeft": f"3px solid {bd_color}"},
            children=[
                html.Div(className="mom-hl-meta", children=[
                    html.Span(_fmt_ts_hl(row.get("ingested_at")), className="mom-hl-ts"),
                    _source_chip(row.get("source_name") or ""),
                ]),
                html.A((row.get("title") or "")[:100],
                       href=row.get("url") or "#",
                       target="_blank",
                       className="mom-hl-title"),
            ],
        ))
    return items


def _build_sparkline(spark_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not spark_df.empty:
        y = spark_df["avg_sentiment"].fillna(0)
        fig.add_trace(go.Scatter(
            x=spark_df["calculated_at"], y=y,
            mode="lines",
            line=dict(color="#00ff88", width=1.5),
            fill="tozeroy", fillcolor="rgba(0,255,136,0.06)",
            hovertemplate="%{x|%H:%M}<br>%{y:.3f}<extra></extra>",
        ))
        fig.add_hline(y=0, line_color="#252525", line_width=1)
    fig.update_layout(
        paper_bgcolor="#0d0d0d", plot_bgcolor="#0d0d0d",
        height=100, margin=dict(l=0, r=0, t=4, b=0),
        showlegend=False,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig


def _build_sentiment(sent_df: pd.DataFrame, spark_df: pd.DataFrame) -> list:
    if sent_df.empty:
        return [html.Div("No sentiment data available",
                         style={"color": "#444", "fontSize": "12px", "padding": "16px 0"})]
    row   = sent_df.iloc[0]
    score = _safe(row.get("avg_sentiment"))
    label = _score_to_label(score)
    s     = _SENT_STYLE.get(label, {})
    cnt   = int(row.get("article_count") or 0)
    bull  = int(row.get("bullish_count")  or 0)
    bear  = int(row.get("bearish_count")  or 0)
    neut  = int(row.get("neutral_count")  or 0)
    calc_str = "—"
    try:
        calc_str = _to_et(row.get("calculated_at")).strftime("%H:%M ET")
    except Exception:
        pass

    def _stat(lbl, val, color="#888"):
        return html.Div(className="mom-stat-row", children=[
            html.Span(lbl, className="mom-stat-lbl"),
            html.Span(val, className="mom-stat-val", style={"color": color}),
        ])

    return [
        html.Div(className="mom-sent-header", children=[
            html.Span(label, style={
                "background":   s.get("bg",    "#141414"),
                "color":        s.get("color", "#666"),
                "border":       f"1px solid {s.get('bd', '#282828')}",
                "borderRadius": "12px", "padding": "4px 16px",
                "fontSize": "14px", "fontWeight": "700",
            }),
            html.Div(f"{score:+.2f}" if score is not None else "—",
                     className="mom-sent-score",
                     style={"color": s.get("color", "#888")}),
        ]),
        html.Div(className="mom-stat-grid", children=[
            _stat("Bullish", f"{_pct(bull, cnt)}  ({bull} articles)", "#00ff88"),
            _stat("Bearish", f"{_pct(bear, cnt)}  ({bear} articles)", "#ff4444"),
            _stat("Neutral", f"{_pct(neut, cnt)}  ({neut} articles)", "#888"),
            _stat("Total",   f"{cnt} articles"),
            _stat("Updated", calc_str),
        ]),
        html.Div(className="mom-sparkline-wrap", children=[
            dcc.Graph(figure=_build_sparkline(spark_df),
                      config={"displayModeBar": False},
                      style={"height": "100px"}),
        ]),
    ]


# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    style={"padding": "0", "background": "#0d0d0d"},
    children=[
        dcc.Store(id="mom-ticker-store", data="AAPL"),

        # Ticker search bar
        html.Div(className="mom-search-wrap", children=[
            html.Span("🔍", className="mom-search-icon"),
            dcc.Input(
                id="mom-search-input",
                type="text",
                placeholder="Search ticker (e.g. AAPL)...",
                debounce=True,
                className="mom-search-input",
                n_submit=0,
            ),
        ]),

        # Info bar — price, change, company name, tags
        html.Div(
            id="mom-infobar",
            style={"padding": "0 16px"},
        ),

        # TradingView chart (built-in price bar, OHLC, volume)
        html.Div(style={"width": "100%", "height": "600px"}, children=[
            html.Iframe(
                id="mom-tv-iframe",
                src="",
                style={"width": "100%", "height": "100%",
                       "border": "none", "display": "block"},
            ),
        ]),

        # Two-column: news (60%) + sentiment (40%)
        html.Div(className="mom-two-col", children=[
            html.Div(className="mom-headlines-col", children=[
                html.Div("NEWS HEADLINES", className="mom-sect-title"),
                html.Div(id="mom-headlines", className="mom-headlines-list"),
            ]),
            html.Div(className="mom-sentiment-col", children=[
                html.Div("SENTIMENT ANALYSIS", className="mom-sect-title"),
                html.Div(id="mom-sentiment"),
            ]),
        ]),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("mom-ticker-store", "data"),
    Output("mom-search-input", "value"),
    Input("mom-search-input",  "value"),
    State("mom-ticker-store",  "data"),
    prevent_initial_call=True,
)
def _set_ticker(search_val, current_ticker):
    ticker = (search_val or "").strip().upper()
    if not ticker or ticker == (current_ticker or "").upper():
        raise PreventUpdate
    return ticker, ticker


@callback(
    Output("mom-infobar", "children"),
    Input("mom-ticker-store", "data"),
)
def _update_infobar(ticker):
    if not ticker:
        raise PreventUpdate
    ticker = ticker.strip().upper()
    try:
        df = query_df(_PRICE_SQL, {"ticker": ticker})
    except Exception:
        df = pd.DataFrame()
    return _build_infobar(ticker, df)


@callback(
    Output("mom-tv-iframe", "src"),
    Input("mom-ticker-store", "data"),
)
def _update_tv(ticker):
    return _tv_src((ticker or "AAPL").strip().upper())


@callback(
    Output("mom-headlines", "children"),
    Output("mom-sentiment", "children"),
    Input("mom-ticker-store", "data"),
)
def _load_detail(ticker):
    if not ticker:
        raise PreventUpdate
    ticker = ticker.strip().upper()
    r = _parallel_queries(ticker)
    return _build_headlines(r["hl"]), _build_sentiment(r["sent"], r["spark"])
