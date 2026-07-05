"""
Momentum Scanner — high-velocity movers with sentiment overlay.
"""
from __future__ import annotations

import math

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, MATCH, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df
from sentiment_analysis.ingestion.finviz_ingestor import is_market_hours

dash.register_page(__name__, path="/momentum", name="Momentum", title="Momentum Scanner")

try:
    from sentiment_analysis.dashboard.pages.screener import COMPANY_NAMES
except ImportError:
    COMPANY_NAMES: dict[str, str] = {}

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

_HEADLINES_SQL = """
    SELECT title, url, ingested_at, tickers
    FROM rss_articles
    WHERE ingested_at > NOW() - INTERVAL '24 hours'
      AND array_length(tickers, 1) > 0
    ORDER BY ingested_at DESC
    LIMIT 500
"""

# ── Filter option defs ────────────────────────────────────────────────────

_MIN_VOL_OPTS = [
    {"label": "Any Vol",  "value": "0"},
    {"label": "100K+",    "value": "100000"},
    {"label": "500K+",    "value": "500000"},
    {"label": "1M+",      "value": "1000000"},
    {"label": "5M+",      "value": "5000000"},
    {"label": "10M+",     "value": "10000000"},
]
_TOP_OPTS = [
    {"label": "Top 10",  "value": "10"},
    {"label": "Top 20",  "value": "20"},
    {"label": "Top 50",  "value": "50"},
    {"label": "Top 100", "value": "100"},
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
            dbc.Select(
                id=id_,
                options=opts,
                value=val,
                className="mom-fsel",
            ),
        ],
    )


# ── Card builders ─────────────────────────────────────────────────────────


def _headlines_panel(headlines: list[dict]) -> html.Div:
    if not headlines:
        return html.Div("No recent headlines",
                        style={"color": "#444", "fontSize": "12px", "paddingTop": "12px"})
    items = []
    for h in headlines:
        ts = ""
        try:
            ts = pd.Timestamp(h["ingested_at"]).strftime("%H:%M")
        except Exception:
            pass
        title = (h.get("title") or "")[:110]
        url   = h.get("url") or "#"
        items.append(html.Div(
            className="mom-headline-item",
            children=[
                html.Span(ts, className="mom-headline-ts"),
                html.A(title, href=url, target="_blank", className="mom-headline-title"),
            ],
        ))
    return html.Div(items)


def _sent_detail_panel(row: dict) -> html.Div:
    score = _safe(row.get("avg_sentiment"))
    label = _score_to_label(score)
    s     = _SENT_STYLE.get(label, {})
    cnt   = int(row.get("article_count") or 0)
    bull  = int(row.get("bullish_count") or 0)
    bear  = int(row.get("bearish_count") or 0)
    neut  = int(row.get("neutral_count") or 0)
    calc  = row.get("calculated_at")
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


def _build_card(i: int, row: dict, headlines: list[dict]) -> html.Div:
    ticker   = str(row.get("ticker", ""))
    company  = COMPANY_NAMES.get(ticker, "")
    score    = _safe(row.get("avg_sentiment"))
    label    = _score_to_label(score)
    s_color  = _SENT_STYLE.get(label, {}).get("color", "#666")
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
                    # Rank badge
                    html.Span(str(i), className="mom-rank"),
                    # Ticker + company
                    html.Div(className="mom-id", children=[
                        html.Span(ticker, className="mom-ticker"),
                        html.Span(company, className="mom-company"),
                    ]),
                    # Data columns
                    _col("Price",    _fmt_price(row.get("price"))),
                    _col("Chg %",    chg_text, chg_color),
                    _col("Volume",   _fmt_volume(row.get("volume"))),
                    # Sentiment score (left-aligned, wider)
                    html.Div(className="mom-col mom-col-sent", children=[
                        html.Span("Sentiment", className="mom-col-label"),
                        html.Div(style={"display": "flex", "alignItems": "center", "gap": "6px"},
                                 children=[
                            html.Span(
                                f"{score:+.2f}" if score is not None else "—",
                                className="mom-col-val",
                                style={"color": s_color},
                            ),
                            _sent_badge(score),
                        ]),
                    ]),
                    _col("Articles", str(int(row.get("article_count", 0)))),
                    # Spacer + progress bar
                    html.Div(style={"flex": "1"}),
                    _progress_bar(score),
                    # Expand button
                    html.Button(
                        "▶",
                        id={"type": "momentum-expand", "ticker": ticker},
                        n_clicks=0,
                        className="mom-expand-btn",
                    ),
                ],
            ),
            # ── Expanded panel (3 columns) ────────────────────────────────
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
                    html.Div(className="mom-panel-section", children=[
                        html.P("News (24h)", className="mom-panel-title"),
                        _headlines_panel(headlines),
                    ]),
                    html.Div(className="mom-panel-section", children=[
                        html.P("Sentiment Detail", className="mom-panel-title"),
                        _sent_detail_panel(row),
                    ]),
                ],
            ),
        ],
    )


# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    style={"padding": "0"},
    children=[
        dcc.Interval(id="momentum-interval", interval=60_000, n_intervals=0),

        # Market status banner
        html.Div(id="momentum-banner", className="mom-banner"),

        # Title bar
        html.Div(className="mom-title-bar", children=[
            html.Span("Momentum Scanner", className="mom-title"),
            html.Span(id="momentum-count", className="mom-count"),
            html.Span(id="momentum-updated", className="mom-updated"),
        ]),

        # Filter bar
        html.Div(className="mom-filter-bar", children=[
            _fsel("MIN VOL",  "momentum-min-vol",   _MIN_VOL_OPTS,   "0"),
            _fsel("REL VOL",  "momentum-rel-vol",   [{"label":"Any","value":"0"}], "0"),
            _fsel("TOP",      "momentum-top",        _TOP_OPTS,       "20"),
            _fsel("MAX $",    "momentum-max-price",  _MAX_PRICE_OPTS, "0"),
            _fsel("SENT",     "momentum-sent",       _SENT_OPTS,      "all"),
            html.Button("↺ Refresh", id="momentum-refresh", className="mom-refresh-btn"),
        ]),

        # Ticker strip
        html.Div(id="momentum-ticker-strip", className="mom-ticker-strip"),

        # Card list
        html.Div(id="momentum-cards", className="mom-cards"),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("momentum-banner", "children"),
    Input("momentum-interval", "n_intervals"),
)
def _update_banner(_):
    open_ = is_market_hours()
    dot_color = "#00e676" if open_ else "#ff9800"
    label     = "Market Open" if open_ else "Market Closed"
    return [
        html.Span("●", style={"color": dot_color, "marginRight": "6px", "fontSize": "10px"}),
        html.Span(label, style={"color": "#888", "fontSize": "12px", "fontWeight": "600"}),
    ]


@callback(
    Output("momentum-ticker-strip", "children"),
    Output("momentum-cards",        "children"),
    Output("momentum-count",        "children"),
    Output("momentum-updated",      "children"),
    Input("momentum-interval",   "n_intervals"),
    Input("momentum-refresh",    "n_clicks"),
    Input("momentum-min-vol",    "value"),
    Input("momentum-top",        "value"),
    Input("momentum-max-price",  "value"),
    Input("momentum-sent",       "value"),
)
def _update_cards(n_interval, n_refresh, min_vol, top, max_price, sent_filter):
    # ── Fetch price + sentiment data ──────────────────────────────────────
    df = query_df(_MOMENTUM_SQL, {})
    if df is None or df.empty:
        empty = html.Div(
            "No price data available. Market data refreshes every minute.",
            style={"padding": "48px", "textAlign": "center", "color": "#444", "fontSize": "13px"},
        )
        return [], [empty], "0 tickers", ""

    # ── Apply filters ─────────────────────────────────────────────────────
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
            label = _score_to_label(score).lower()
            if sent_filter == "bullish":
                return "bullish" in label
            if sent_filter == "bearish":
                return "bearish" in label
            return label == "neutral"
        df = df[df["avg_sentiment"].apply(_matches)]

    try:
        n_top = int(top or 20)
    except (TypeError, ValueError):
        n_top = 20
    df = df.head(n_top)

    if df.empty:
        empty = html.Div(
            "No tickers match the current filters.",
            style={"padding": "48px", "textAlign": "center", "color": "#444", "fontSize": "13px"},
        )
        return [], [empty], "0 tickers", ""

    # ── Fetch headlines (bulk) ────────────────────────────────────────────
    hl_df = query_df(_HEADLINES_SQL, {})
    headlines_map: dict[str, list] = {}
    if hl_df is not None and not hl_df.empty:
        for _, art in hl_df.iterrows():
            tickers = art.get("tickers") or []
            for t in tickers:
                if t not in headlines_map:
                    headlines_map[t] = []
                if len(headlines_map[t]) < 5:
                    headlines_map[t].append(art.to_dict())

    # ── Ticker strip ──────────────────────────────────────────────────────
    strip_items = []
    for _, row in df.head(30).iterrows():
        chg_text, chg_color = _fmt_chg(row.get("change_pct"))
        strip_items.append(html.Div(
            className="mom-strip-badge",
            children=[
                html.Span(str(row["ticker"]), className="mom-strip-ticker"),
                html.Span(chg_text, style={"color": chg_color, "fontSize": "10px"}),
            ],
        ))

    # ── Build cards ───────────────────────────────────────────────────────
    cards = []
    for i, (_, row) in enumerate(df.iterrows(), 1):
        ticker = str(row.get("ticker", ""))
        cards.append(_build_card(i, row.to_dict(), headlines_map.get(ticker, [])))

    updated = f"Updated {now_et().strftime('%H:%M')} ET"
    return strip_items, cards, f"{len(df)} tickers", updated


@callback(
    Output({"type": "momentum-panel",  "ticker": MATCH}, "style"),
    Output({"type": "momentum-expand", "ticker": MATCH}, "children"),
    Input({"type":  "momentum-expand", "ticker": MATCH}, "n_clicks"),
    State({"type":  "momentum-panel",  "ticker": MATCH}, "style"),
    prevent_initial_call=True,
)
def _toggle_card(n_clicks, current_style):
    if not n_clicks:
        raise PreventUpdate
    is_open = (current_style or {}).get("display") not in (None, "none")
    if is_open:
        return {"display": "none"}, "▶"
    return (
        {"display": "grid",
         "gridTemplateColumns": "1fr 1fr 260px",
         "gap": "16px",
         "padding": "12px 16px 16px",
         "borderTop": "1px solid #1c1c1c",
         "background": "#0d0d0d"},
        "▼",
    )
