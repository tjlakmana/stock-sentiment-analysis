"""
Charts page — TradingView advanced chart with sentiment overlay panels.
"""
from __future__ import annotations

import math

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df

dash.register_page(__name__, path="/charts", name="Charts", title="Charts")

# ── Timeframe config ──────────────────────────────────────────────────────

_TIMEFRAMES = [
    ("1M",  "1"),
    ("3M",  "3"),
    ("5M",  "5"),
    ("15M", "15"),
    ("30M", "30"),
    ("1H",  "60"),
    ("D",   "D"),
    ("W",   "W"),
    ("M",   "M"),
]

_DEFAULT_TICKER = "AAPL"
_DEFAULT_TF     = "D"

# ── Plotly base style ─────────────────────────────────────────────────────

_PLOTLY_BASE = dict(
    paper_bgcolor="#0f0f0f",
    plot_bgcolor="#0f0f0f",
    font={"family": "Inter, sans-serif", "color": "#888888"},
    margin=dict(l=48, r=16, t=36, b=32),
    xaxis=dict(
        gridcolor="#1a1a1a",
        linecolor="#252525",
        tickfont={"size": 10, "color": "#555"},
    ),
    yaxis=dict(
        gridcolor="#1a1a1a",
        linecolor="#252525",
        tickfont={"size": 10, "color": "#555"},
    ),
)

# ── SQL ───────────────────────────────────────────────────────────────────

_HIST_SQL = """
    SELECT
        date_trunc('hour', calculated_at) AS hour,
        AVG(avg_sentiment)                AS avg_sentiment
    FROM ticker_sentiment_summary
    WHERE ticker = :ticker
      AND calculated_at > NOW() - INTERVAL '7 days'
    GROUP BY 1
    ORDER BY 1
"""

_VOL_SQL = """
    SELECT
        date_trunc('hour', ingested_at) AS hour,
        COUNT(*)                         AS article_count
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '7 days'
    GROUP BY 1
    ORDER BY 1
"""

_LATEST_SQL = """
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
    WHERE ticker = :ticker
    ORDER BY ticker, calculated_at DESC
"""

_LAST_ARTICLE_SQL = """
    SELECT MAX(ingested_at) AS last_article
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '24 hours'
"""

# ── Helpers ───────────────────────────────────────────────────────────────

_SENT_STYLE = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#131c38", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#351212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#280808", "color": "#ff5252", "bd": "#d50000"},
}


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


def _trend_arrow(momentum) -> str:
    m = str(momentum or "").lower()
    if m == "improving": return "↑"
    if m == "declining": return "↓"
    return "→"


def _trend_color(momentum) -> str:
    m = str(momentum or "").lower()
    if m == "improving": return "#00e676"
    if m == "declining": return "#ff5252"
    return "#555555"


def _pct(num, denom) -> str:
    try:
        n, d = int(num), int(denom)
        return f"{n / d * 100:.0f}%" if d else "—"
    except (TypeError, ValueError):
        return "—"


# ── TradingView ───────────────────────────────────────────────────────────

def _tv_src(ticker: str, interval: str) -> str:
    return (
        "https://www.tradingview.com/widgetembed/"
        "?frameElementId=tradingview_chart"
        f"&symbol={ticker.upper()}"
        f"&interval={interval}"
        "&theme=dark"
        "&style=1"
        "&locale=en"
        "&toolbar_bg=%23131722"
        "&enable_publishing=false"
        "&hide_top_toolbar=false"
        "&hide_legend=false"
        "&save_image=false"
        "&calendar=false"
        "&hide_side_toolbar=0"
        "&withdateranges=1"
    )


# ── Chart builders ────────────────────────────────────────────────────────

def _sentiment_history_fig(df: pd.DataFrame, ticker: str) -> go.Figure:
    fig = go.Figure()
    empty_layout = dict(
        **_PLOTLY_BASE, height=270,
        title=dict(text=f"Sentiment Score — {ticker.upper()}",
                   font={"size": 12, "color": "#555"}, x=0.01, xanchor="left"),
    )
    if df.empty:
        fig.update_layout(**empty_layout)
        return fig

    x = df["hour"].tolist()
    y = [_safe(v) or 0.0 for v in df["avg_sentiment"].tolist()]

    fig.add_trace(go.Scatter(
        x=x, y=[max(0.0, v) for v in y],
        fill="tozeroy",
        fillcolor="rgba(0,230,118,0.12)",
        line=dict(color="#00e676", width=1.5),
        name="Bullish",
        hovertemplate="%{x|%m-%d %H:%M}<br>Score: %{y:+.3f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=[min(0.0, v) for v in y],
        fill="tozeroy",
        fillcolor="rgba(255,82,82,0.12)",
        line=dict(color="#ff5252", width=1.5),
        name="Bearish",
        hovertemplate="%{x|%m-%d %H:%M}<br>Score: %{y:+.3f}<extra></extra>",
    ))

    fig.update_layout(
        **_PLOTLY_BASE,
        height=270,
        showlegend=False,
        title=dict(text=f"Sentiment Score — {ticker.upper()}",
                   font={"size": 12, "color": "#888"}, x=0.01, xanchor="left"),
        yaxis=dict(
            **_PLOTLY_BASE["yaxis"],
            zeroline=True,
            zerolinecolor="#333333",
            zerolinewidth=1,
            range=[-1.05, 1.05],
        ),
    )
    return fig


def _article_volume_fig(df: pd.DataFrame, ticker: str) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.update_layout(
            **_PLOTLY_BASE, height=270,
            title=dict(text=f"Article Volume — {ticker.upper()}",
                       font={"size": 12, "color": "#555"}, x=0.01, xanchor="left"),
        )
        return fig

    fig.add_trace(go.Bar(
        x=df["hour"].tolist(),
        y=df["article_count"].astype(int).tolist(),
        marker_color="#00d4ff",
        marker_opacity=0.70,
        name="Articles",
        hovertemplate="%{x|%m-%d %H:%M}<br>Articles: %{y}<extra></extra>",
    ))
    fig.update_layout(
        **_PLOTLY_BASE,
        height=270,
        showlegend=False,
        bargap=0.18,
        title=dict(text=f"Article Volume — {ticker.upper()}",
                   font={"size": 12, "color": "#888"}, x=0.01, xanchor="left"),
    )
    return fig


def _breakdown_children(
    latest: "pd.Series | None",
    last_art,
    ticker: str,
) -> list:
    if latest is None:
        return [html.Div(
            f"No sentiment data for {ticker}.",
            style={"padding": "24px", "color": "#555", "fontSize": "13px"},
        )]

    score = _safe(latest.get("avg_sentiment"))
    label = _score_to_label(score)
    s     = _SENT_STYLE.get(label, {})
    cnt   = int(latest.get("article_count") or 0)
    bull  = int(latest.get("bullish_count") or 0)
    bear  = int(latest.get("bearish_count") or 0)
    neut  = int(latest.get("neutral_count") or 0)
    mom   = str(latest.get("momentum") or "")
    calc  = latest.get("calculated_at")

    last_art_str = "—"
    if last_art is not None:
        try:
            last_art_str = pd.Timestamp(last_art).strftime("%H:%M ET")
        except Exception:
            pass

    calc_str = "—"
    if calc is not None:
        try:
            calc_str = pd.Timestamp(calc).strftime("%m-%d %H:%M")
        except Exception:
            pass

    def _row(lbl, val, color="#aaaaaa"):
        return html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "padding": "5px 0", "borderBottom": "1px solid #181818"},
            children=[
                html.Span(lbl, style={"fontSize": "12px", "color": "#555"}),
                html.Span(val, style={"fontSize": "12px", "color": color,
                                      "fontWeight": "600"}),
            ],
        )

    arrow       = _trend_arrow(mom)
    arrow_color = _trend_color(mom)

    return [
        html.Div(
            "Sentiment Breakdown",
            style={"fontSize": "11px", "color": "#444", "fontWeight": "700",
                   "textTransform": "uppercase", "letterSpacing": "0.07em",
                   "padding": "10px 0 8px"},
        ),
        html.Div(
            style={"textAlign": "center", "padding": "8px 0 12px"},
            children=[
                html.Span(
                    label,
                    style={
                        "background":   s.get("bg", "#141414"),
                        "color":        s.get("color", "#888"),
                        "border":       f"1px solid {s.get('bd', '#282828')}",
                        "borderRadius": "14px",
                        "padding":      "5px 18px",
                        "fontSize":     "13px",
                        "fontWeight":   "700",
                    },
                ),
                html.Div(
                    f"{score:+.3f}" if score is not None else "—",
                    style={"fontSize": "28px", "fontWeight": "700",
                           "color": s.get("color", "#888"),
                           "marginTop": "10px", "fontFamily": "monospace"},
                ),
            ],
        ),
        html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 4px", "opacity": 1}),
        _row("Bullish",     f"{bull} ({_pct(bull, cnt)})",  "#00e676"),
        _row("Bearish",     f"{bear} ({_pct(bear, cnt)})",  "#ff5252"),
        _row("Neutral",     f"{neut} ({_pct(neut, cnt)})",  "#888888"),
        _row("Total (24h)", str(cnt)),
        _row("Momentum",    f"{arrow} {mom or 'Stable'}",   arrow_color),
        _row("Last update", calc_str),
        _row("Last article", last_art_str),
    ]


# ── Layout helpers ────────────────────────────────────────────────────────

def _tf_btn(label: str, tf: str, active: bool = False) -> html.Button:
    cls = "charts-tf-btn" + (" charts-tf-active" if active else "")
    return html.Button(label, id={"type": "charts-tf-btn", "tf": tf},
                       n_clicks=0, className=cls)


# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        dcc.Interval(id="charts-interval", interval=300_000, n_intervals=0),
        dcc.Store(id="charts-timeframe", data=_DEFAULT_TF),

        # Top bar
        html.Div(
            style={"display": "flex", "alignItems": "center", "gap": "12px",
                   "marginBottom": "14px", "flexWrap": "wrap"},
            children=[
                dcc.Input(
                    id="charts-ticker",
                    value=_DEFAULT_TICKER,
                    placeholder="Ticker…",
                    debounce=True,
                    style={
                        "background":    "#141414",
                        "border":        "1px solid #252525",
                        "color":         "#e0e0e0",
                        "borderRadius":  "5px",
                        "padding":       "7px 12px",
                        "fontSize":      "14px",
                        "fontWeight":    "700",
                        "width":         "100px",
                        "textTransform": "uppercase",
                        "fontFamily":    "inherit",
                        "outline":       "none",
                    },
                ),
                html.Div(
                    style={"display": "flex", "gap": "4px"},
                    children=[_tf_btn(lbl, tf, tf == _DEFAULT_TF)
                              for lbl, tf in _TIMEFRAMES],
                ),
                html.Span(
                    id="charts-updated",
                    style={"fontSize": "11px", "color": "#3a3a3a",
                           "marginLeft": "auto"},
                ),
            ],
        ),

        # TradingView iframe
        html.Iframe(
            id="tradingview-chart",
            src=_tv_src(_DEFAULT_TICKER, _DEFAULT_TF),
            style={
                "width":         "100%",
                "height":        "520px",
                "border":        "none",
                "borderRadius":  "8px",
                "display":       "block",
                "marginBottom":  "16px",
            },
        ),

        # Three panels
        html.Div(
            style={"display": "grid",
                   "gridTemplateColumns": "1fr 1fr 300px",
                   "gap": "16px",
                   "alignItems": "start"},
            children=[
                html.Div(
                    className="articles-table",
                    style={"padding": "0", "overflow": "hidden"},
                    children=[dcc.Graph(id="charts-sentiment-hist",
                                       config={"displayModeBar": False})],
                ),
                html.Div(
                    className="articles-table",
                    style={"padding": "0", "overflow": "hidden"},
                    children=[dcc.Graph(id="charts-article-vol",
                                       config={"displayModeBar": False})],
                ),
                html.Div(
                    className="articles-table",
                    style={"padding": "0 16px 14px"},
                    children=[html.Div(id="charts-breakdown")],
                ),
            ],
        ),
    ],
)

# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("charts-timeframe", "data"),
    Output({"type": "charts-tf-btn", "tf": ALL}, "className"),
    Input({"type": "charts-tf-btn", "tf": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _set_timeframe(n_clicks_list):
    triggered = ctx.triggered_id
    if not triggered:
        raise PreventUpdate
    active_tf = triggered["tf"]
    classes = [
        "charts-tf-btn charts-tf-active" if tf == active_tf else "charts-tf-btn"
        for _, tf in _TIMEFRAMES
    ]
    return active_tf, classes


@callback(
    Output("tradingview-chart",     "src"),
    Output("charts-sentiment-hist", "figure"),
    Output("charts-article-vol",    "figure"),
    Output("charts-breakdown",      "children"),
    Output("charts-updated",        "children"),
    Input("charts-ticker",          "value"),
    Input("charts-timeframe",       "data"),
    Input("charts-interval",        "n_intervals"),
    Input("url",                    "pathname"),
)
def _update_charts(ticker, timeframe, _n, pathname):
    if pathname not in (None, "/charts"):
        raise PreventUpdate

    ticker    = (ticker or "AAPL").strip().upper()[:6] or "AAPL"
    timeframe = timeframe or _DEFAULT_TF

    tv_src    = _tv_src(ticker, timeframe)
    hist_fig  = _sentiment_history_fig(query_df(_HIST_SQL, {"ticker": ticker}), ticker)
    vol_fig   = _article_volume_fig(query_df(_VOL_SQL, {"ticker": ticker}), ticker)

    latest_df = query_df(_LATEST_SQL, {"ticker": ticker})
    last_df   = query_df(_LAST_ARTICLE_SQL, {"ticker": ticker})

    latest_row = latest_df.iloc[0] if not latest_df.empty else None
    last_art   = last_df.iloc[0]["last_article"] if not last_df.empty else None

    breakdown = _breakdown_children(latest_row, last_art, ticker)
    updated   = "Updated " + now_et().strftime("%H:%M ET")

    return tv_src, hist_fig, vol_fig, breakdown, updated
