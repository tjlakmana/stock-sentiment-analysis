"""
Momentum — Finviz-style stock detail page with volume chart.
"""
from __future__ import annotations

import concurrent.futures
import math

import dash
import pandas as pd
import plotly.graph_objects as go
import pytz
from dash import ALL, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df

dash.register_page(__name__, path="/momentum", name="Momentum", title="Momentum Scanner")

try:
    from sentiment_analysis.dashboard.pages.screener import COMPANY_NAMES
except ImportError:
    COMPANY_NAMES: dict[str, str] = {}

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

_TIMEOUT    = 10
_TF_LABELS  = ["1M", "3M", "5M", "15M", "30M", "1H", "D", "W", "M"]
_TF_MAP     = {"1M": "1", "3M": "3", "5M": "5", "15M": "15", "30M": "30",
               "1H": "60", "D": "D", "W": "W", "M": "M"}
_DEFAULT_TF = "D"

_SECTOR_MAP: dict[str, str] = {
    "AAPL": "Technology",          "MSFT": "Technology",
    "NVDA": "Technology",          "AMD":  "Technology",
    "INTC": "Technology",          "AVGO": "Technology",
    "QCOM": "Technology",          "CSCO": "Technology",
    "ADBE": "Technology",          "CRM":  "Technology",
    "PANW": "Technology",          "CRWD": "Technology",
    "SNOW": "Technology",          "MDB":  "Technology",
    "DDOG": "Technology",          "ZS":   "Technology",
    "NET":  "Technology",          "FTNT": "Technology",
    "PLTR": "Technology",          "IBM":  "Technology",
    "GOOGL": "Communication",      "META": "Communication",
    "NFLX": "Communication",       "DIS":  "Communication",
    "T":    "Communication",       "VZ":   "Communication",
    "CMCSA": "Communication",
    "AMZN": "Consumer Cyclical",   "TSLA": "Consumer Cyclical",
    "HD":   "Consumer Cyclical",   "NKE":  "Consumer Cyclical",
    "WMT":  "Consumer Defensive",  "PG":   "Consumer Defensive",
    "KO":   "Consumer Defensive",  "PEP":  "Consumer Defensive",
    "COST": "Consumer Defensive",
    "JPM":  "Financial",           "BAC":  "Financial",
    "V":    "Financial",           "MA":   "Financial",
    "GS":   "Financial",
    "JNJ":  "Healthcare",          "UNH":  "Healthcare",
    "LLY":  "Healthcare",          "ABBV": "Healthcare",
    "MRK":  "Healthcare",          "TMO":  "Healthcare",
    "XOM":  "Energy",              "CVX":  "Energy",
}

_EXCHANGE_MAP: dict[str, str] = {
    "AAPL": "NASDAQ", "MSFT": "NASDAQ", "NVDA": "NASDAQ", "GOOGL": "NASDAQ",
    "META": "NASDAQ", "AMZN": "NASDAQ", "TSLA": "NASDAQ", "INTC": "NASDAQ",
    "AMD":  "NASDAQ", "CSCO": "NASDAQ", "QCOM": "NASDAQ", "ADBE": "NASDAQ",
    "CRM":  "NASDAQ", "NFLX": "NASDAQ", "AVGO": "NASDAQ", "COST": "NASDAQ",
    "PEP":  "NASDAQ", "CRWD": "NASDAQ", "PLTR": "NASDAQ", "SNOW": "NASDAQ",
    "MDB":  "NASDAQ", "DDOG": "NASDAQ", "ZS":   "NASDAQ", "NET":  "NASDAQ",
    "PANW": "NASDAQ", "FTNT": "NASDAQ",
}

# ── SQL ───────────────────────────────────────────────────────────────────

_STRIP_SQL = """
    SELECT ticker, price, change_pct
    FROM ticker_prices
    ORDER BY ABS(change_pct) DESC
    LIMIT 10
"""

_TICKER_INFO_SQL = """
    SELECT ticker, price, change_pct, volume, market_cap, updated_at
    FROM ticker_prices
    WHERE ticker = :ticker
    LIMIT 1
"""

_NEWS_BANNER_SQL = """
    SELECT title, ingested_at
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
    ORDER BY ingested_at DESC
    LIMIT 1
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


def _tv_src(ticker: str, tf: str = "D") -> str:
    interval = _TF_MAP.get(tf, "D")
    return (
        "https://www.tradingview.com/widgetembed/"
        f"?symbol={ticker.upper()}"
        f"&interval={interval}"
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


def _fmt_ts_price(dt_val) -> str:
    try:
        dt = _to_et(dt_val)
        return dt.strftime("%b %d • %I:%M%p ET")
    except Exception:
        return "—"


def _fmt_ts_banner(dt_val) -> str:
    try:
        dt = _to_et(dt_val)
        return dt.strftime("%b %d %I:%M %p ET")
    except Exception:
        return ""


def _fmt_ts_hl(dt_val) -> str:
    try:
        dt = _to_et(dt_val)
        if dt.date() == now_et().date():
            return f"Today {dt.strftime('%I:%M%p')}"
        return dt.strftime("%b %d %I:%M%p")
    except Exception:
        return ""


def _parallel_queries(ticker: str) -> dict[str, pd.DataFrame]:
    queries = {
        "info":   (_TICKER_INFO_SQL, {"ticker": ticker}),
        "banner": (_NEWS_BANNER_SQL, {"ticker": ticker}),
        "hl":     (_HEADLINES_SQL,   {"ticker": ticker}),
        "sent":   (_SENTIMENT_SQL,   {"ticker": ticker}),
        "spark":  (_SPARKLINE_SQL,   {"ticker": ticker}),
    }
    results: dict[str, pd.DataFrame] = {k: pd.DataFrame() for k in queries}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        fs = {pool.submit(query_df, sql, p): k for k, (sql, p) in queries.items()}
        done, _ = concurrent.futures.wait(fs, timeout=_TIMEOUT)
        for f in done:
            try:
                results[fs[f]] = f.result()
            except Exception:
                pass
    return results

# ── Panel builders ────────────────────────────────────────────────────────


def _build_infobar(ticker: str, row: dict) -> list:
    price   = _safe(row.get("price"))
    chg_pct = _safe(row.get("change_pct"))
    chg_dollar = None
    if price is not None and chg_pct is not None:
        prev       = price / (1 + chg_pct / 100)
        chg_dollar = price - prev

    price_str = f"{price:,.2f}" if price is not None else "—"
    ts_str    = _fmt_ts_price(row.get("updated_at"))

    chg_str = "—"
    chg_col = "#888"
    if chg_dollar is not None and chg_pct is not None:
        sign    = "+" if chg_dollar >= 0 else "-"
        chg_str = f"{sign}{abs(chg_dollar):.2f} ({abs(chg_pct):.2f}%)"
        chg_col = "#00ff88" if chg_dollar >= 0 else "#ff4444"

    sector   = _SECTOR_MAP.get(ticker, "—")
    exchange = _EXCHANGE_MAP.get(ticker, "—")

    return [
        html.Div(className="mom-ib-top", children=[
            html.Span(ticker, className="mom-ib-ticker"),
            html.Span(COMPANY_NAMES.get(ticker, ""), className="mom-ib-company"),
            html.Div(
                style={"marginLeft": "auto", "display": "flex", "alignItems": "center"},
                children=[
                    html.Span(price_str, style={
                        "fontSize": "36px", "fontWeight": "bold",
                        "color": "white", "fontFamily": "monospace",
                    }),
                    html.Div(style={"marginLeft": "12px"}, children=[
                        html.Div(ts_str,    style={"fontSize": "13px", "color": "#888888"}),
                        html.Div(chg_str,   style={"fontSize": "14px", "color": chg_col}),
                    ]),
                ],
            ),
        ]),
        html.Div(className="mom-ib-tags", children=[
            html.Span(sector,   className="mom-tag-pill"),
            html.Span("·", style={"color": "#2a2a2a", "padding": "0 4px"}),
            html.Span("USA",    className="mom-tag-pill"),
            html.Span("·", style={"color": "#2a2a2a", "padding": "0 4px"}),
            html.Span(exchange, className="mom-tag-pill"),
        ]),
    ]


def _build_banner(row: dict) -> list:
    title = (row.get("title") or "")[:120]
    ts    = _fmt_ts_banner(row.get("ingested_at"))
    return [
        html.Span("⭐", style={"marginRight": "6px", "flexShrink": "0"}),
        html.Span(ts, style={"color": "#555", "fontSize": "11px", "marginRight": "10px",
                             "flexShrink": "0", "whiteSpace": "nowrap"}),
        html.Span(title, style={"color": "#00d4ff", "fontSize": "12px",
                                "overflow": "hidden", "textOverflow": "ellipsis",
                                "whiteSpace": "nowrap"}),
    ]


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
        dcc.Store(id="mom-ticker-store", data=None),
        dcc.Store(id="mom-tf-store",     data=_DEFAULT_TF),
        dcc.Interval(id="mom-init", interval=1, n_intervals=0, max_intervals=1),

        # Search bar
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

        # Ticker strip (scrollable, clicking loads ticker)
        html.Div(id="mom-strip", className="mom-ticker-strip"),

        # Info bar
        html.Div(id="mom-infobar", className="mom-infobar"),

        # Latest news banner
        html.Div(id="mom-news-banner", className="mom-news-banner"),

        # Timeframe buttons
        html.Div(className="mom-tf-bar", children=[
            html.Button(
                label,
                id={"type": "mom-tf-btn", "tf": label},
                n_clicks=0,
                className="mom-tf-btn mom-tf-active" if label == _DEFAULT_TF else "mom-tf-btn",
            )
            for label in _TF_LABELS
        ]),

        # TradingView chart (550px shows candlesticks + built-in volume bars)
        html.Div(style={"width": "100%", "height": "550px"}, children=[
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
    Output("mom-strip",        "children"),
    Output("mom-ticker-store", "data"),
    Input("mom-init",          "n_intervals"),
)
def _init_page(_):
    """Load ticker strip and default ticker on page mount."""
    try:
        df = query_df(_STRIP_SQL)
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty:
        return [], "AAPL"

    items = []
    for _, row in df.iterrows():
        ticker  = str(row["ticker"])
        chg     = _safe(row.get("change_pct"))
        chg_str = (f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%") if chg is not None else "—"
        chg_col = "#00ff88" if (chg or 0) >= 0 else "#ff4444"
        items.append(html.Button(
            children=[
                html.Span(ticker,  className="mom-strip-ticker"),
                html.Span(chg_str, style={"color": chg_col, "fontSize": "10px"}),
            ],
            id={"type": "mom-strip-btn", "ticker": ticker},
            n_clicks=0,
            className="mom-strip-badge",
        ))

    return items, str(df.iloc[0]["ticker"])


@callback(
    Output("mom-ticker-store", "data",  allow_duplicate=True),
    Output("mom-search-input", "value"),
    Input({"type": "mom-strip-btn", "ticker": ALL}, "n_clicks"),
    Input("mom-search-input",  "n_submit"),
    Input("mom-search-input",  "value"),
    State("mom-ticker-store",  "data"),
    prevent_initial_call=True,
)
def _set_ticker(_strip_clicks, _n_submit, search_val, current_ticker):
    triggered = ctx.triggered_id
    if triggered == "mom-search-input":
        ticker = (search_val or "").strip().upper()
        if not ticker or ticker == (current_ticker or "").upper():
            raise PreventUpdate
        return ticker, ticker
    if isinstance(triggered, dict) and triggered.get("type") == "mom-strip-btn":
        ticker = triggered["ticker"]
        if ticker == current_ticker:
            raise PreventUpdate
        return ticker, ticker
    raise PreventUpdate


@callback(
    Output("mom-tv-iframe", "src"),
    Input("mom-ticker-store", "data"),
    Input("mom-tf-store",     "data"),
)
def _update_tv(ticker, tf):
    return _tv_src((ticker or "AAPL").strip().upper(), tf or _DEFAULT_TF)


@callback(
    Output("mom-tf-store", "data"),
    Output({"type": "mom-tf-btn", "tf": ALL}, "className"),
    Input({"type":  "mom-tf-btn", "tf": ALL}, "n_clicks"),
    State({"type":  "mom-tf-btn", "tf": ALL}, "id"),
    prevent_initial_call=True,
)
def _set_tf(_clicks, ids):
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        raise PreventUpdate
    selected = triggered["tf"]
    classes  = [
        "mom-tf-btn mom-tf-active" if id_["tf"] == selected else "mom-tf-btn"
        for id_ in ids
    ]
    return selected, classes


@callback(
    Output("mom-infobar",     "children"),
    Output("mom-news-banner", "children"),
    Output("mom-headlines",   "children"),
    Output("mom-sentiment",   "children"),
    Input("mom-ticker-store", "data"),
)
def _load_detail(ticker):
    if not ticker:
        raise PreventUpdate
    ticker = ticker.strip().upper()
    r = _parallel_queries(ticker)

    infobar = (
        _build_infobar(ticker, r["info"].iloc[0].to_dict())
        if not r["info"].empty
        else [html.Div(ticker, className="mom-ib-ticker",
                       style={"padding": "16px", "color": "#00d4ff",
                              "fontSize": "28px", "fontWeight": "700"})]
    )

    banner = (
        _build_banner(r["banner"].iloc[0].to_dict())
        if not r["banner"].empty
        else [html.Span("No recent news", style={"color": "#444", "fontSize": "12px"})]
    )

    return (
        infobar,
        banner,
        _build_headlines(r["hl"]),
        _build_sentiment(r["sent"], r["spark"]),
    )
