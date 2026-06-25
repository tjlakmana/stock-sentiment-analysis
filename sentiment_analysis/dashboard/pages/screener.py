"""
Screener page — ranked ticker list with real-time price and sentiment data.
"""
from __future__ import annotations

import math
from urllib.parse import parse_qs

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from loguru import logger

from sentiment_analysis.dashboard.db import now_et, query_df
from sentiment_analysis.ingestion.finviz_ingestor import is_market_hours

dash.register_page(__name__, path="/screener", name="Screener", title="Screener")

# ── Options ───────────────────────────────────────────────────────────────

SIGNAL_OPTIONS = [
    {"label": "All",               "value": "all"},
    {"label": "Bullish Sentiment", "value": "bullish"},
    {"label": "Bearish Sentiment", "value": "bearish"},
    {"label": "Unusual Volume",    "value": "unusual_volume"},
    {"label": "Most Articles",     "value": "most_articles"},
    {"label": "Sentiment Spike",   "value": "spike"},
]

ORDER_OPTIONS = [
    {"label": "Avg Sentiment",  "value": "avg_sentiment"},
    {"label": "Price",          "value": "price"},
    {"label": "Change %",       "value": "change_pct"},
    {"label": "Volume",         "value": "volume"},
    {"label": "Market Cap",     "value": "market_cap"},
    {"label": "Article Count",  "value": "article_count"},
]

SECTOR_OPTIONS = [
    {"label": "All Sectors",         "value": "all"},
    {"label": "Technology",          "value": "Technology"},
    {"label": "Healthcare",          "value": "Healthcare"},
    {"label": "Financials",          "value": "Financials"},
    {"label": "Consumer Discretionary", "value": "Consumer Discretionary"},
    {"label": "Consumer Staples",    "value": "Consumer Staples"},
    {"label": "Communication Services", "value": "Communication Services"},
    {"label": "Industrials",         "value": "Industrials"},
    {"label": "Energy",              "value": "Energy"},
    {"label": "Materials",           "value": "Materials"},
    {"label": "Real Estate",         "value": "Real Estate"},
    {"label": "Utilities",           "value": "Utilities"},
]

MKTCAP_OPTIONS = [
    {"label": "All",                "value": "all"},
    {"label": "Large  (>$10B)",     "value": "large"},
    {"label": "Mid  ($2B–$10B)",    "value": "mid"},
    {"label": "Small  (<$2B)",      "value": "small"},
]

TIME_WINDOW_OPTIONS = [
    {"label": "1 Hour",  "value": "1hr"},
    {"label": "4 Hours", "value": "4hr"},
    {"label": "24 Hours","value": "24hr"},
]

MIN_ARTICLES_OPTIONS = [
    {"label": "Any articles",    "value": 0},
    {"label": "Min 5 articles",  "value": 5},
    {"label": "Min 10 articles", "value": 10},
    {"label": "Min 25 articles", "value": 25},
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
        p.price,
        p.change_pct,
        p.volume,
        p.market_cap,
        p.pre_market_price,
        p.post_market_price,
        CASE WHEN rs.ticker IS NOT NULL THEN TRUE ELSE FALSE END AS has_spike
    FROM latest l
    LEFT JOIN ticker_prices p  ON l.ticker = p.ticker
    LEFT JOIN recent_spikes rs ON l.ticker = rs.ticker
    WHERE l.article_count >= :min_articles
"""


def _fetch_data(window: str, min_articles: int) -> pd.DataFrame:
    # ── Diagnostics: log intermediate counts so Railway logs show where data stops ──
    diag = query_df("""
        SELECT
            (SELECT COUNT(*) FROM ticker_sentiment_summary)              AS tss_total,
            (SELECT COUNT(DISTINCT "window") FROM ticker_sentiment_summary) AS tss_windows,
            (SELECT string_agg(DISTINCT "window", ', ' ORDER BY "window")
               FROM ticker_sentiment_summary)                              AS tss_window_values,
            (SELECT COUNT(*) FROM ticker_sentiment_summary
               WHERE "window" = :window)                                   AS tss_for_window,
            (SELECT COUNT(*) FROM ticker_prices)                         AS price_rows
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
    if s >= 0.35:   return "Bullish"
    elif s >= 0.15: return "Somewhat Bullish"
    elif s > -0.15: return "Neutral"
    elif s > -0.35: return "Somewhat Bearish"
    return "Bearish"


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
            "padding":      "3px 9px",
            "fontSize":     "11px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
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


# ── Table renderers ───────────────────────────────────────────────────────

def _render_overview_rows(df: pd.DataFrame) -> list:
    if df.empty:
        return [html.Div("No tickers match current filters.", className="no-results",
                         style={"padding": "24px", "textAlign": "center", "color": "#555"})]
    rows = []
    for _, r in df.iterrows():
        chg_text, chg_color = _fmt_chg(r.get("change_pct"))

        price_cell = html.Div([
            html.Span(_fmt_price(r.get("price")), style={"fontSize": "13px", "fontWeight": "600"}),
        ], style={"lineHeight": "1.2"})

        # Pre/post market shown when outside market hours
        if not is_market_hours():
            pre  = _safe(r.get("pre_market_price"))
            post = _safe(r.get("post_market_price"))
            ext_price = pre or post
            ext_label = "Pre" if pre else ("Post" if post else None)
            if ext_price and ext_label:
                price_cell = html.Div([
                    html.Span(_fmt_price(r.get("price")), style={"fontSize": "13px", "fontWeight": "600"}),
                    html.Span(f"{ext_label}: {_fmt_price(ext_price)}",
                              style={"fontSize": "10px", "color": "#888", "display": "block"}),
                ], style={"lineHeight": "1.3"})

        rows.append(html.Div(
            className="screener-row",
            children=[
                html.A(
                    r["ticker"],
                    href=f"/?keyword={r['ticker']}",
                    className="screener-col-ticker screener-ticker-link",
                ),
                html.Div(price_cell,     className="screener-col-price"),
                html.Span(chg_text,      className="screener-col-chg",
                          style={"color": chg_color}),
                html.Span(_fmt_volume(r.get("volume")),   className="screener-col-vol"),
                html.Span(_fmt_mktcap(r.get("market_cap")), className="screener-col-mktcap"),
                html.Span(str(int(r.get("article_count", 0))), className="screener-col-articles"),
                html.Div(_badge(r.get("avg_sentiment")), className="screener-col-sentiment"),
                html.Div(_trend_icon(r.get("momentum")), className="screener-col-trend"),
            ],
        ))
    return rows


def _render_sentiment_rows(df: pd.DataFrame) -> list:
    if df.empty:
        return [html.Div("No tickers match current filters.", className="no-results",
                         style={"padding": "24px", "textAlign": "center", "color": "#555"})]
    rows = []
    for _, r in df.iterrows():
        cnt = int(r.get("article_count", 0))
        last_upd = ""
        try:
            ts = pd.Timestamp(r.get("calculated_at"))
            if ts is not pd.NaT:
                last_upd = ts.strftime("%m-%d %H:%M")
        except Exception:
            pass

        rows.append(html.Div(
            className="screener-row",
            children=[
                html.A(
                    r["ticker"],
                    href=f"/?keyword={r['ticker']}",
                    className="screener-col-ticker screener-ticker-link",
                ),
                html.Div(_badge(r.get("avg_sentiment")), className="screener-col-sentiment"),
                html.Span(_pct(r.get("bullish_count"),  cnt), className="screener-col-bull"),
                html.Span(_pct(r.get("bearish_count"),  cnt), className="screener-col-bear"),
                html.Span(_pct(r.get("neutral_count"),  cnt), className="screener-col-neut"),
                html.Span(str(cnt),                           className="screener-col-articles"),
                html.Span(last_upd,                           className="screener-col-lastupd"),
                html.Div(_trend_icon(r.get("momentum")),      className="screener-col-trend"),
            ],
        ))
    return rows


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
    return df  # "all" or "most_articles" (sorting handled separately)


def _apply_mktcap_filter(df: pd.DataFrame, mktcap: str) -> pd.DataFrame:
    if mktcap == "large":
        return df[df["market_cap"].fillna(0) > 10_000_000_000]
    if mktcap == "mid":
        mask = (df["market_cap"].fillna(0) >= 2_000_000_000) & \
               (df["market_cap"].fillna(0) <= 10_000_000_000)
        return df[mask]
    if mktcap == "small":
        mask = (df["market_cap"].fillna(0) > 0) & \
               (df["market_cap"].fillna(0) < 2_000_000_000)
        return df[mask]
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
    "width":     "40px",
    "color":     "#00d4ff",
}

_OVERVIEW_HEADER = html.Div(className="screener-header", children=[
    html.Span("Ticker",     className="screener-col-ticker"),
    html.Span("Price",      className="screener-col-price"),
    html.Span("Chg %",      className="screener-col-chg"),
    html.Span("Volume",     className="screener-col-vol"),
    html.Span("Mkt Cap",    className="screener-col-mktcap"),
    html.Span("Articles",   className="screener-col-articles"),
    html.Span("Sentiment",  className="screener-col-sentiment"),
    html.Span("Trend",      className="screener-col-trend"),
])

_SENTIMENT_HEADER = html.Div(className="screener-header", children=[
    html.Span("Ticker",     className="screener-col-ticker"),
    html.Span("Sentiment",  className="screener-col-sentiment"),
    html.Span("Bull %",     className="screener-col-bull"),
    html.Span("Bear %",     className="screener-col-bear"),
    html.Span("Neutral %",  className="screener-col-neut"),
    html.Span("Articles",   className="screener-col-articles"),
    html.Span("Last Upd",   className="screener-col-lastupd"),
    html.Span("Trend",      className="screener-col-trend"),
])

# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        # ── Intervals & stores ─────────────────────────────────────────────
        dcc.Interval(id="screener-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="screener-sort-dir", data="desc"),

        # ── Top filter bar ─────────────────────────────────────────────────
        html.Div(
            className="filter-bar",
            style={"flexWrap": "nowrap", "marginBottom": "8px"},
            children=[
                dbc.Select(
                    id="screener-signal",
                    options=SIGNAL_OPTIONS,
                    value="all",
                    className="filter-select",
                    style={"minWidth": "165px", **_SH},
                ),
                dbc.Select(
                    id="screener-order",
                    options=ORDER_OPTIONS,
                    value="avg_sentiment",
                    className="filter-select",
                    style={"minWidth": "150px", **_SH},
                ),
                html.Button(
                    "↓",
                    id="screener-sort-dir-btn",
                    n_clicks=0,
                    style=_SORT_BTN_STYLE,
                    title="Toggle sort direction",
                ),
                dcc.Input(
                    id="screener-search",
                    placeholder="🔍  Search ticker…",
                    debounce=True,
                    className="filter-input",
                    style={"height": "36px", "flex": "1", "minWidth": "120px"},
                ),
                html.Button(
                    "↻",
                    id="screener-refresh-btn",
                    n_clicks=0,
                    style=_REFRESH_BTN_STYLE,
                    title="Refresh now",
                ),
            ],
        ),

        # ── Collapsible filter panel ───────────────────────────────────────
        html.Div(
            style={"marginBottom": "8px"},
            children=[
                html.Button(
                    "▾ Filters",
                    id="screener-filter-btn",
                    n_clicks=0,
                    style={
                        "background":   "transparent",
                        "border":       "none",
                        "color":        "#888888",
                        "fontSize":     "12px",
                        "cursor":       "pointer",
                        "fontFamily":   "inherit",
                        "padding":      "4px 0",
                    },
                ),
            ],
        ),
        dbc.Collapse(
            id="screener-filter-collapse",
            is_open=False,
            children=[
                html.Div(
                    style={"background": "#101010", "border": "1px solid #1c1c1c",
                           "borderRadius": "6px", "padding": "12px 16px",
                           "marginBottom": "10px"},
                    children=[
                        dbc.Tabs([
                            dbc.Tab(label="Descriptive", children=[
                                html.Div(
                                    style={"display": "flex", "gap": "12px",
                                           "marginTop": "10px", "flexWrap": "wrap"},
                                    children=[
                                        html.Div([
                                            html.Label("Sector", className="filter-label"),
                                            dbc.Select(
                                                id="screener-sector",
                                                options=SECTOR_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={"minWidth": "200px", **_SH},
                                            ),
                                        ]),
                                        html.Div([
                                            html.Label("Market Cap", className="filter-label"),
                                            dbc.Select(
                                                id="screener-mktcap",
                                                options=MKTCAP_OPTIONS,
                                                value="all",
                                                className="filter-select",
                                                style={"minWidth": "160px", **_SH},
                                            ),
                                        ]),
                                    ],
                                ),
                            ]),
                            dbc.Tab(label="Sentiment", children=[
                                html.Div(
                                    style={"marginTop": "12px"},
                                    children=[
                                        html.Div(
                                            style={"display": "flex", "gap": "12px",
                                                   "flexWrap": "wrap", "marginBottom": "14px"},
                                            children=[
                                                html.Div([
                                                    html.Label("Min Articles", className="filter-label"),
                                                    dbc.Select(
                                                        id="screener-min-articles",
                                                        options=MIN_ARTICLES_OPTIONS,
                                                        value=0,
                                                        className="filter-select",
                                                        style={"minWidth": "140px", **_SH},
                                                    ),
                                                ]),
                                                html.Div([
                                                    html.Label("Time Window", className="filter-label"),
                                                    dbc.Select(
                                                        id="screener-window",
                                                        options=TIME_WINDOW_OPTIONS,
                                                        value="4hr",
                                                        className="filter-select",
                                                        style={"minWidth": "130px", **_SH},
                                                    ),
                                                ]),
                                            ],
                                        ),
                                        html.Label("Sentiment Range",
                                                   className="filter-label",
                                                   style={"marginBottom": "6px",
                                                          "display": "block"}),
                                        dcc.RangeSlider(
                                            id="screener-sentiment-range",
                                            min=-1.0, max=1.0, step=0.05,
                                            value=[-1.0, 1.0],
                                            marks={
                                                -1.0: {"label": "-1.0", "style": {"color": "#ff5252"}},
                                                -0.5: {"label": "-0.5", "style": {"color": "#ff8a80"}},
                                                 0.0: {"label": "0",    "style": {"color": "#888"}},
                                                 0.5: {"label": "+0.5", "style": {"color": "#69f0ae"}},
                                                 1.0: {"label": "+1.0", "style": {"color": "#00e676"}},
                                            },
                                            tooltip={"placement": "bottom", "always_visible": False},
                                        ),
                                    ],
                                ),
                            ]),
                        ], style={"borderBottom": "none"}),
                    ],
                ),
            ],
        ),

        html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 10px", "opacity": "1"}),

        # ── Count bar ──────────────────────────────────────────────────────
        html.Div(className="count-bar", children=[
            html.Span(id="screener-count",   className="count-label"),
            html.Span(id="screener-updated", className="last-refresh"),
        ]),

        # ── Results tabs ───────────────────────────────────────────────────
        dbc.Tabs(
            id="screener-result-tabs",
            children=[
                dbc.Tab(label="Overview", children=[
                    html.Div(className="articles-table", children=[
                        _OVERVIEW_HEADER,
                        html.Div(id="screener-overview-rows"),
                    ]),
                ]),
                dbc.Tab(label="Sentiment Detail", children=[
                    html.Div(className="articles-table", children=[
                        _SENTIMENT_HEADER,
                        html.Div(id="screener-sentiment-rows"),
                    ]),
                ]),
            ],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("screener-interval", "interval"),
    Input("screener-interval", "n_intervals"),
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
    icon    = "↑" if new_dir == "asc" else "↓"
    return new_dir, icon


@callback(
    Output("screener-filter-collapse", "is_open"),
    Output("screener-filter-btn",      "children"),
    Input("screener-filter-btn",       "n_clicks"),
    State("screener-filter-collapse",  "is_open"),
    prevent_initial_call=True,
)
def _toggle_filter_panel(_, is_open):
    new_open = not is_open
    label    = "▴ Filters" if new_open else "▾ Filters"
    return new_open, label


@callback(
    Output("screener-overview-rows",  "children"),
    Output("screener-sentiment-rows", "children"),
    Output("screener-count",          "children"),
    Output("screener-updated",        "children"),
    Input("screener-interval",        "n_intervals"),
    Input("screener-refresh-btn",     "n_clicks"),
    Input("screener-signal",          "value"),
    Input("screener-order",           "value"),
    Input("screener-sort-dir",        "data"),
    Input("screener-search",          "value"),
    Input("screener-mktcap",          "value"),
    Input("screener-sentiment-range", "value"),
    Input("screener-min-articles",    "value"),
    Input("screener-window",          "value"),
    State("url",                      "pathname"),
)
def _update_screener(n, refresh_clicks, signal, order, sort_dir, search,
                     mktcap, sent_range, min_articles, window, pathname):
    if pathname not in (None, "/screener"):
        raise PreventUpdate

    signal       = signal       or "all"
    order        = order        or "avg_sentiment"
    sort_dir     = sort_dir     or "desc"
    min_articles = int(min_articles or 0)
    window       = window       or "4hr"
    sent_range   = sent_range   or [-1.0, 1.0]

    df = _fetch_data(window, min_articles)

    if df.empty:
        empty_msg = [html.Div(
            "No data yet — articles are being collected. Check back in a few minutes.",
            className="no-results",
            style={"padding": "32px", "textAlign": "center",
                   "color": "#555", "fontSize": "14px"},
        )]
        return empty_msg, empty_msg, "0 tickers", "· No data"

    # ── Apply filters ──────────────────────────────────────────────────────
    df = _apply_signal(df, signal)

    if search:
        df = df[df["ticker"].str.upper().str.startswith(search.strip().upper())]

    if mktcap and mktcap != "all":
        df = _apply_mktcap_filter(df, mktcap)

    lo, hi = float(sent_range[0]), float(sent_range[1])
    df = df[(df["avg_sentiment"] >= lo) & (df["avg_sentiment"] <= hi)]

    # ── Sort ───────────────────────────────────────────────────────────────
    df = _apply_sort(df, signal, order, sort_dir)

    total   = len(df)
    updated = "· " + now_et().strftime("Updated %H:%M ET")
    count   = f"{total:,} ticker{'s' if total != 1 else ''}"

    overview_rows  = _render_overview_rows(df)
    sentiment_rows = _render_sentiment_rows(df)

    return overview_rows, sentiment_rows, count, updated
