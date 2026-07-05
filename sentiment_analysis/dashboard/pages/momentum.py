"""
Momentum Scanner — high-velocity movers with sentiment overlay.
"""
from __future__ import annotations

import concurrent.futures
import math

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, MATCH, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df
from sentiment_analysis.ingestion.finviz_ingestor import is_market_hours

dash.register_page(__name__, path="/momentum", name="Momentum", title="Momentum Scanner")

try:
    from sentiment_analysis.dashboard.pages.screener import COMPANY_NAMES
except ImportError:
    COMPANY_NAMES: dict[str, str] = {}

try:
    from sentiment_analysis.dashboard.pages.news import _source_chip
except ImportError:
    def _source_chip(source: str):  # type: ignore[misc]
        return html.Span(source[:3].upper() if source else "—",
                         style={"fontSize": "10px", "color": "#555"})

# ── Constants ─────────────────────────────────────────────────────────────

_PER_PAGE = 10
_MAX_TOP  = 25
_TIMEOUT  = 10  # seconds for DB queries

# ── SQL ───────────────────────────────────────────────────────────────────

_MOMENTUM_SQL = """
    WITH latest_sent AS (
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
        WHERE "window" = '24hr'
        ORDER BY ticker, calculated_at DESC
    )
    SELECT
        p.ticker,
        p.price,
        p.change_pct,
        p.volume,
        p.market_cap,
        COALESCE(s.avg_sentiment,  0) AS avg_sentiment,
        COALESCE(s.article_count,  0) AS article_count,
        COALESCE(s.bullish_count,  0) AS bullish_count,
        COALESCE(s.bearish_count,  0) AS bearish_count,
        COALESCE(s.neutral_count,  0) AS neutral_count,
        s.momentum,
        s.calculated_at
    FROM ticker_prices p
    LEFT JOIN latest_sent s ON p.ticker = s.ticker
    WHERE p.volume IS NOT NULL
      AND p.change_pct IS NOT NULL
    ORDER BY ABS(p.change_pct) DESC NULLS LAST
"""

# Per-ticker lazy headline fetch (called on card expand)
_TICKER_HEADLINES_SQL = """
    SELECT title, url, source_name, ingested_at, sentiment_label
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '2 days'
    ORDER BY ingested_at DESC
    LIMIT 5
"""

# ── Filter options ────────────────────────────────────────────────────────

_MIN_VOL_OPTS = [
    {"label": "Any Vol",  "value": "0"},
    {"label": "100K+",    "value": "100000"},
    {"label": "500K+",    "value": "500000"},
    {"label": "1M+",      "value": "1000000"},
    {"label": "5M+",      "value": "5000000"},
    {"label": "10M+",     "value": "10000000"},
]
_TOP_OPTS = [
    {"label": "Top 5",   "value": "5"},
    {"label": "Top 10",  "value": "10"},
    {"label": "Top 20",  "value": "20"},
    {"label": "Top 25",  "value": "25"},
]
_MAX_PRICE_OPTS = [
    {"label": "Any $",   "value": "0"},
    {"label": "< $10",   "value": "10"},
    {"label": "< $25",   "value": "25"},
    {"label": "< $50",   "value": "50"},
    {"label": "< $100",  "value": "100"},
    {"label": "< $500",  "value": "500"},
]
_SENT_OPTS = [
    {"label": "All Sent",  "value": "all"},
    {"label": "Bullish",   "value": "bullish"},
    {"label": "Bearish",   "value": "bearish"},
    {"label": "Neutral",   "value": "neutral"},
]

# ── Sentiment styling ─────────────────────────────────────────────────────

_SENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#111827", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#2d1212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#1e0808", "color": "#ff5252", "bd": "#d50000"},
}

# ── Helpers ───────────────────────────────────────────────────────────────


def _safe_query(sql: str, params: dict | None = None) -> pd.DataFrame | None:
    """Run query_df in a thread; return None on timeout or error."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(query_df, sql, params or {})
        try:
            return future.result(timeout=_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return None
        except Exception:
            return None


def _score_to_label(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Neutral"
    if math.isnan(s):  return "Neutral"
    if s >= 0.35:      return "Bullish"
    if s >= 0.15:      return "Somewhat Bullish"
    if s > -0.15:      return "Neutral"
    if s > -0.35:      return "Somewhat Bearish"
    return "Bearish"


def _safe(v):
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
    return f"{'+'if v>=0 else ''}{v:.2f}%", color


def _fmt_volume(v) -> str:
    v = _safe(v)
    if v is None:   return "—"
    if v >= 1e9:    return f"{v/1e9:.1f}B"
    if v >= 1e6:    return f"{v/1e6:.1f}M"
    if v >= 1e3:    return f"{v/1e3:.0f}K"
    return str(int(v))


def _pct(num, denom) -> str:
    try:
        n, d = int(num), int(denom)
        return f"{n/d*100:.0f}%" if d else "—"
    except (TypeError, ValueError):
        return "—"


def _sent_badge(score) -> html.Span:
    label = _score_to_label(score)
    s = _SENT_STYLE.get(label, {})
    return html.Span(
        label,
        style={
            "background":   s.get("bg",    "#141414"),
            "color":        s.get("color", "#666"),
            "border":       f"1px solid {s.get('bd', '#282828')}",
            "borderRadius": "10px",
            "padding":      "2px 9px",
            "fontSize":     "11px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
        },
    )


def _art_sent_badge(label: str | None) -> html.Span | None:
    if not label:
        return None
    s = _SENT_STYLE.get(label, {})
    if not s:
        return None
    return html.Span(
        label,
        style={
            "background":   s.get("bg",    "#141414"),
            "color":        s.get("color", "#666"),
            "border":       f"1px solid {s.get('bd', '#282828')}",
            "borderRadius": "8px",
            "padding":      "1px 7px",
            "fontSize":     "10px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
            "flexShrink":   "0",
        },
    )


def _progress_bar(score) -> html.Div:
    s = _safe(score) or 0.0
    label = _score_to_label(s)
    color = _SENT_STYLE.get(label, {}).get("color", "#333")
    pct = max(0, min(100, (s + 1) / 2 * 100))
    return html.Div(
        className="mom-progress-wrap",
        children=[
            html.Div(className="mom-progress-fill",
                     style={"width": f"{pct:.0f}%", "background": color}),
        ],
    )


def _tv_mini_src(ticker: str) -> str:
    return (
        "https://www.tradingview.com/widgetembed/"
        f"?symbol={ticker.upper()}"
        "&interval=5"
        "&theme=dark"
        "&style=1"
        "&locale=en"
        "&toolbar_bg=%23131722"
        "&enable_publishing=false"
        "&hide_top_toolbar=true"
        "&hide_legend=true"
        "&save_image=false"
        "&hide_side_toolbar=true"
        "&allow_symbol_change=false"
    )


def _fsel(label: str, id_: str, opts, val) -> html.Div:
    return html.Div(
        className="mom-fsel-wrap",
        children=[
            html.Span(label, className="mom-fsel-label"),
            dbc.Select(id=id_, options=opts, value=val, className="mom-fsel"),
        ],
    )


# ── Card & panel builders ─────────────────────────────────────────────────


def _headlines_panel(headlines: list[dict]) -> html.Div:
    if not headlines:
        return html.Div("No recent articles",
                        style={"color": "#444", "fontSize": "12px", "paddingTop": "8px"})
    items = []
    for h in headlines:
        ts = ""
        try:
            dt = pd.Timestamp(h["ingested_at"])
            ts = f"{dt.strftime('%b')} {dt.day} {dt.strftime('%H:%M')} ET"
        except Exception:
            pass
        title      = (h.get("title") or "")[:80]
        url        = h.get("url") or "#"
        source     = h.get("source_name") or ""
        sent_badge = _art_sent_badge(h.get("sentiment_label") or "")

        row_children = [
            _source_chip(source),
            html.Span(ts, className="mom-headline-ts"),
            html.A(title, href=url, target="_blank", className="mom-headline-title"),
        ]
        if sent_badge:
            row_children.append(sent_badge)

        items.append(html.Div(className="mom-headline-item", children=row_children))
    return html.Div(items)


def _sent_detail_panel(row: dict) -> html.Div:
    score    = _safe(row.get("avg_sentiment"))
    label    = _score_to_label(score)
    s        = _SENT_STYLE.get(label, {})
    cnt      = int(row.get("article_count") or 0)
    bull     = int(row.get("bullish_count") or 0)
    bear     = int(row.get("bearish_count") or 0)
    neut     = int(row.get("neutral_count") or 0)
    calc     = row.get("calculated_at")
    calc_str = "—"
    if calc is not None:
        try:
            calc_str = pd.Timestamp(calc).strftime("%H:%M")
        except Exception:
            pass

    def _drow(lbl, val, color="#555"):
        return html.Div(className="mom-detail-row", children=[
            html.Span(lbl, className="mom-detail-key"),
            html.Span(val, className="mom-detail-val", style={"color": color}),
        ])

    return html.Div([
        html.Div(className="mom-detail-header", children=[
            html.Span(
                label,
                style={
                    "background":   s.get("bg",    "#141414"),
                    "color":        s.get("color", "#666"),
                    "border":       f"1px solid {s.get('bd','#282828')}",
                    "borderRadius": "12px",
                    "padding":      "3px 14px",
                    "fontSize":     "12px",
                    "fontWeight":   "700",
                },
            ),
            html.Div(
                f"{score:+.3f}" if score is not None else "—",
                style={"fontSize": "22px", "fontWeight": "700",
                       "color": s.get("color", "#888"),
                       "fontFamily": "monospace", "marginTop": "6px"},
            ),
        ]),
        _drow("Bullish",  f"{bull} ({_pct(bull, cnt)})", "#00e676"),
        _drow("Bearish",  f"{bear} ({_pct(bear, cnt)})", "#ff5252"),
        _drow("Neutral",  f"{neut} ({_pct(neut, cnt)})", "#888"),
        _drow("Articles", str(cnt)),
        _drow("Updated",  calc_str),
    ])


def _build_card(i: int, row: dict) -> html.Div:
    """Build a single ticker card. Headlines are lazy-loaded on expand."""
    ticker  = str(row.get("ticker", ""))
    company = COMPANY_NAMES.get(ticker, "")
    score   = _safe(row.get("avg_sentiment"))
    label   = _score_to_label(score)
    s_color = _SENT_STYLE.get(label, {}).get("color", "#666")
    chg_text, chg_color = _fmt_chg(row.get("change_pct"))

    def _col(lbl, val, color="#888") -> html.Div:
        return html.Div(className="mom-col", children=[
            html.Span(lbl, className="mom-col-label"),
            html.Span(val, className="mom-col-val", style={"color": color}),
        ])

    return html.Div(
        className="mom-card",
        children=[
            # ── Collapsed header ──────────────────────────────────────────
            html.Div(
                className="mom-card-header",
                children=[
                    html.Span(str(i), className="mom-rank"),
                    html.Div(className="mom-id", children=[
                        html.Span(ticker, className="mom-ticker"),
                        html.Span(company, className="mom-company"),
                    ]),
                    _col("Price",   _fmt_price(row.get("price"))),
                    _col("Chg %",   chg_text, chg_color),
                    _col("Volume",  _fmt_volume(row.get("volume"))),
                    html.Div(className="mom-col mom-col-sent", children=[
                        html.Span("Sentiment", className="mom-col-label"),
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "6px"},
                            children=[
                                html.Span(
                                    f"{score:+.2f}" if score is not None else "—",
                                    className="mom-col-val",
                                    style={"color": s_color},
                                ),
                                _sent_badge(score),
                            ],
                        ),
                    ]),
                    _col("Articles", str(int(row.get("article_count", 0)))),
                    html.Div(style={"flex": "1"}),
                    _progress_bar(score),
                    html.Button(
                        "▶",
                        id={"type": "momentum-expand", "ticker": ticker},
                        n_clicks=0,
                        className="mom-expand-btn",
                    ),
                ],
            ),
            # ── Expanded panel (3 columns, lazy) ──────────────────────────
            html.Div(
                id={"type": "momentum-panel", "ticker": ticker},
                className="mom-card-panel",
                style={"display": "none"},
                children=[
                    html.Div(className="mom-panel-section", children=[
                        html.P("Intraday (5m)", className="mom-panel-title"),
                        html.Iframe(
                            src=_tv_mini_src(ticker),
                            style={"width": "100%", "height": "220px",
                                   "border": "none", "borderRadius": "4px"},
                        ),
                    ]),
                    # Headlines placeholder — filled lazily by _toggle_card
                    html.Div(className="mom-panel-section", children=[
                        html.P("News Headlines", className="mom-panel-title"),
                        html.Div(
                            id={"type": "momentum-headlines", "ticker": ticker},
                            children=html.Div(
                                "Loading...",
                                style={"color": "#2a2a2a", "fontSize": "11px",
                                       "paddingTop": "6px"},
                            ),
                        ),
                    ]),
                    html.Div(className="mom-panel-section", children=[
                        html.P("Sentiment Detail", className="mom-panel-title"),
                        _sent_detail_panel(row),
                    ]),
                ],
            ),
        ],
    )


def _msg(text: str, color: str = "#444") -> html.Div:
    return html.Div(text,
                    style={"padding": "48px", "textAlign": "center",
                           "color": color, "fontSize": "13px"})


# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    style={"padding": "0"},
    children=[
        dcc.Interval(id="momentum-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="momentum-page",  data=0),
        dcc.Store(id="momentum-total", data=0),

        # Market status banner
        html.Div(id="momentum-banner", className="mom-banner"),

        # Title bar
        html.Div(className="mom-title-bar", children=[
            html.Span("Momentum Scanner", className="mom-title"),
            html.Span(id="momentum-count",   className="mom-count"),
            html.Span(id="momentum-updated", className="mom-updated"),
        ]),

        # Filter bar
        html.Div(className="mom-filter-bar", children=[
            _fsel("MIN VOL", "momentum-min-vol",   _MIN_VOL_OPTS,   "0"),
            _fsel("REL VOL", "momentum-rel-vol",   [{"label":"Any","value":"0"}], "0"),
            _fsel("TOP",     "momentum-top",        _TOP_OPTS,       "10"),
            _fsel("MAX $",   "momentum-max-price",  _MAX_PRICE_OPTS, "0"),
            _fsel("SENT",    "momentum-sent",       _SENT_OPTS,      "all"),
            html.Button("↺ Refresh", id="momentum-refresh", className="mom-refresh-btn"),
        ]),

        # Ticker strip
        html.Div(id="momentum-ticker-strip", className="mom-ticker-strip"),

        # Card list with loading spinner
        dcc.Loading(
            type="circle",
            color="#00d4ff",
            style={"minHeight": "120px"},
            children=html.Div(id="momentum-cards", className="mom-cards"),
        ),

        # Pagination (static buttons, dynamic text/disabled state)
        html.Div(
            className="mom-pagination",
            children=[
                html.Button("← Prev", id="momentum-prev",
                            className="mom-page-btn", disabled=True),
                html.Span("", id="momentum-page-info", className="mom-page-info"),
                html.Button("Next →", id="momentum-next",
                            className="mom-page-btn", disabled=True),
            ],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("momentum-banner", "children"),
    Input("momentum-interval", "n_intervals"),
)
def _update_banner(_):
    open_ = is_market_hours()
    dot   = "#00e676" if open_ else "#ff9800"
    label = "Market Open" if open_ else "Market Closed"
    return [
        html.Span("●", style={"color": dot, "marginRight": "6px", "fontSize": "10px"}),
        html.Span(label, style={"color": "#888", "fontSize": "12px", "fontWeight": "600"}),
    ]


@callback(
    Output("momentum-ticker-strip", "children"),
    Output("momentum-cards",        "children"),
    Output("momentum-count",        "children"),
    Output("momentum-updated",      "children"),
    Output("momentum-total",        "data"),
    Output("momentum-page-info",    "children"),
    Output("momentum-prev",         "disabled"),
    Output("momentum-next",         "disabled"),
    Input("momentum-interval",    "n_intervals"),
    Input("momentum-refresh",     "n_clicks"),
    Input("momentum-min-vol",     "value"),
    Input("momentum-top",         "value"),
    Input("momentum-max-price",   "value"),
    Input("momentum-sent",        "value"),
    Input("momentum-page",        "data"),
)
def _update_cards(_, _refresh, min_vol, top, max_price, sent_filter, page):
    page = page or 0

    # ── Fetch with timeout ────────────────────────────────────────────────
    df = _safe_query(_MOMENTUM_SQL)
    if df is None:
        err = html.Div([
            html.Div("⚠ Query timed out",
                     style={"color": "#ff9800", "fontWeight": "600", "marginBottom": "4px"}),
            html.Div("Too many tickers — try reducing the TOP filter",
                     style={"color": "#555", "fontSize": "12px"}),
        ], style={"padding": "48px", "textAlign": "center"})
        return [], [err], "—", "", 0, "", True, True

    if df.empty:
        return [], [_msg("No price data available. Market data refreshes every minute.")], \
               "0 tickers", "", 0, "", True, True

    # ── Filters ───────────────────────────────────────────────────────────
    try:
        mv = int(min_vol or 0)
        if mv > 0:
            df = df[df["volume"].fillna(0) >= mv]
    except (TypeError, ValueError):
        pass

    try:
        mp = float(max_price or 0)
        if mp > 0:
            df = df[df["price"].fillna(9999) < mp]
    except (TypeError, ValueError):
        pass

    if sent_filter and sent_filter != "all":
        def _matches(score):
            lbl = _score_to_label(score).lower()
            if sent_filter == "bullish":  return "bullish" in lbl
            if sent_filter == "bearish":  return "bearish" in lbl
            return lbl == "neutral"
        df = df[df["avg_sentiment"].apply(_matches)]

    try:
        n_top = min(int(top or 10), _MAX_TOP)
    except (TypeError, ValueError):
        n_top = 10
    df = df.head(n_top)

    if df.empty:
        return [], [_msg("No tickers match the current filters.")], \
               "0 tickers", "", 0, "", True, True

    # ── Pagination ────────────────────────────────────────────────────────
    total       = len(df)
    total_pages = max(1, math.ceil(total / _PER_PAGE))
    page        = max(0, min(page, total_pages - 1))
    start       = page * _PER_PAGE
    page_df     = df.iloc[start : start + _PER_PAGE]

    # ── Ticker strip (all tickers, up to 25) ──────────────────────────────
    strip_items = []
    for _, row in df.iterrows():
        chg_text, chg_color = _fmt_chg(row.get("change_pct"))
        strip_items.append(html.Div(
            className="mom-strip-badge",
            children=[
                html.Span(str(row["ticker"]), className="mom-strip-ticker"),
                html.Span(chg_text, style={"color": chg_color, "fontSize": "10px"}),
            ],
        ))

    # ── Cards (current page only) ─────────────────────────────────────────
    cards = [_build_card(start + i + 1, row.to_dict())
             for i, (_, row) in enumerate(page_df.iterrows())]

    page_info = f"Page {page + 1} / {total_pages}"
    updated   = f"Updated {now_et().strftime('%H:%M')} ET"
    prev_dis  = page == 0
    next_dis  = page >= total_pages - 1

    return strip_items, cards, f"{total} tickers", updated, total, page_info, prev_dis, next_dis


@callback(
    Output("momentum-page", "data"),
    Input("momentum-prev",      "n_clicks"),
    Input("momentum-next",      "n_clicks"),
    Input("momentum-min-vol",   "value"),
    Input("momentum-top",       "value"),
    Input("momentum-max-price", "value"),
    Input("momentum-sent",      "value"),
    Input("momentum-refresh",   "n_clicks"),
    State("momentum-page",  "data"),
    State("momentum-total", "data"),
    prevent_initial_call=True,
)
def _change_page(prev, nxt, mv, top, mp, sf, refresh, page, total):
    triggered = ctx.triggered_id
    if triggered in {"momentum-min-vol", "momentum-top", "momentum-max-price",
                     "momentum-sent", "momentum-refresh"}:
        return 0
    page        = page or 0
    total_pages = max(1, math.ceil((total or 0) / _PER_PAGE))
    if triggered == "momentum-prev":
        return max(0, page - 1)
    if triggered == "momentum-next":
        return min(total_pages - 1, page + 1)
    return page


@callback(
    Output({"type": "momentum-panel",     "ticker": MATCH}, "style"),
    Output({"type": "momentum-headlines", "ticker": MATCH}, "children"),
    Output({"type": "momentum-expand",    "ticker": MATCH}, "children"),
    Input({"type":  "momentum-expand",    "ticker": MATCH}, "n_clicks"),
    State({"type":  "momentum-panel",     "ticker": MATCH}, "style"),
    State({"type":  "momentum-expand",    "ticker": MATCH}, "id"),
    prevent_initial_call=True,
)
def _toggle_card(n_clicks, current_style, expand_id):
    if not n_clicks:
        raise PreventUpdate

    is_open = (current_style or {}).get("display") not in (None, "none")
    if is_open:
        return {"display": "none"}, no_update, "▶"

    ticker = expand_id["ticker"]

    # Lazy-fetch headlines for this ticker only
    hl_df = _safe_query(_TICKER_HEADLINES_SQL, {"ticker": ticker})
    if hl_df is None:
        hl_content = html.Div(
            "Headlines unavailable (query timed out)",
            style={"color": "#ff9800", "fontSize": "11px", "paddingTop": "6px"},
        )
    else:
        hl_content = _headlines_panel(hl_df.to_dict("records") if not hl_df.empty else [])

    panel_style = {
        "display":             "grid",
        "gridTemplateColumns": "1fr 1fr 260px",
        "gap":                 "16px",
        "padding":             "12px 16px 16px",
        "borderTop":           "1px solid #1c1c1c",
        "background":          "#0d0d0d",
    }
    return panel_style, hl_content, "▼"
