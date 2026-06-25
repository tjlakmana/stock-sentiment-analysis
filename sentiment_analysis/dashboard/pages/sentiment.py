"""
Sentiment overview page — market-wide sentiment dashboard.

Sections:
  Top    — Gauge, Breakdown, Activity Stats (3 cards)
  Middle — Top Bullish, Top Bearish, Volume Spikes (3 columns)
  Bottom — Donut distribution, Hourly article volume (2 charts)
"""
from __future__ import annotations

import math

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df

dash.register_page(__name__, path="/sentiment", name="Sentiment", title="Sentiment")

# ── SQL ───────────────────────────────────────────────────────────────────

_OVERVIEW_SQL = """
    SELECT
        COUNT(*)                                                          AS total,
        COUNT(*) FILTER (WHERE sentiment_score IS NOT NULL)               AS scored,
        COALESCE(AVG(sentiment_score) FILTER (WHERE sentiment_score IS NOT NULL), 0)
                                                                          AS avg_score
    FROM rss_articles
    WHERE ingested_at > NOW() - INTERVAL '24 hours'
"""

_BREAKDOWN_SQL = """
    SELECT sentiment_label, COUNT(*) AS cnt
    FROM rss_articles
    WHERE ingested_at > NOW() - INTERVAL '24 hours'
      AND sentiment_label IS NOT NULL
      AND sentiment_label != ''
    GROUP BY sentiment_label
"""

_ACTIVE_TICKERS_SQL = """
    SELECT COUNT(DISTINCT ticker) AS active_tickers
    FROM (
        SELECT unnest(tickers) AS ticker
        FROM rss_articles
        WHERE ingested_at > NOW() - INTERVAL '24 hours'
          AND tickers IS NOT NULL
          AND array_length(tickers, 1) > 0
    ) t
    WHERE ticker IS NOT NULL AND ticker != ''
"""

_TICKER_SENTIMENT_SQL = """
    SELECT DISTINCT ON (ticker)
        ticker, avg_sentiment, article_count
    FROM ticker_sentiment_summary
    WHERE "window" = '24hr'
      AND article_count >= 2
    ORDER BY ticker, calculated_at DESC
"""

_SPIKES_SQL = """
    SELECT ticker, article_count, rolling_avg, spike_ratio, detected_at
    FROM sentiment_spikes
    WHERE detected_at > NOW() - INTERVAL '24 hours'
    ORDER BY detected_at DESC
    LIMIT 10
"""

_HOURLY_SQL = """
    SELECT
        date_trunc('hour', ingested_at AT TIME ZONE 'America/New_York') AS hour,
        CASE
            WHEN sentiment_label IN ('Bullish', 'Somewhat Bullish') THEN 'bullish'
            WHEN sentiment_label IN ('Bearish', 'Somewhat Bearish') THEN 'bearish'
            ELSE 'neutral'
        END AS sent_group,
        COUNT(*) AS cnt
    FROM rss_articles
    WHERE ingested_at > NOW() - INTERVAL '24 hours'
    GROUP BY 1, 2
    ORDER BY 1
"""

# ── Style constants ───────────────────────────────────────────────────────

_CARD = {
    "background":   "#111111",
    "border":       "1px solid #1c1c1c",
    "borderRadius": "8px",
    "padding":      "20px",
}

_PANEL = {
    "background":   "#111111",
    "border":       "1px solid #1c1c1c",
    "borderRadius": "8px",
    "overflow":     "hidden",
}

_PLOTLY_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#888888", family="Inter, -apple-system, sans-serif", size=11),
)

_SENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#131c38", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#351212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#280808", "color": "#ff5252", "bd": "#d50000"},
}

# ── Helpers ───────────────────────────────────────────────────────────────

def _safe(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _score_color(score: float | None) -> str:
    if score is None: return "#555555"
    if score >= 0.15: return "#00e676"
    if score > -0.15: return "#888888"
    return "#ff5252"


def _score_label(score: float | None) -> str:
    if score is None:    return "No Data"
    if score >= 0.35:    return "Bullish"
    if score >= 0.15:    return "Somewhat Bullish"
    if score > -0.15:    return "Neutral"
    if score > -0.35:    return "Somewhat Bearish"
    return "Bearish"


def _badge(score) -> html.Span:
    s     = _safe(score)
    label = _score_label(s)
    st    = _SENT_STYLE.get(label, {"bg": "#1a1a1a", "color": "#555", "bd": "#333"})
    return html.Span(label, style={
        "background":   st["bg"],
        "color":        st["color"],
        "border":       f"1px solid {st['bd']}",
        "borderRadius": "12px",
        "padding":      "3px 9px",
        "fontSize":     "11px",
        "fontWeight":   "600",
        "whiteSpace":   "nowrap",
    })


def _section_title(text: str) -> html.Div:
    return html.Div(text, style={
        "fontSize":      "10px",
        "fontWeight":    "700",
        "color":         "#555555",
        "textTransform": "uppercase",
        "letterSpacing": "0.8px",
        "marginBottom":  "12px",
    })


def _panel_header(text: str) -> html.Div:
    return html.Div(text, style={
        "fontSize":    "12px",
        "fontWeight":  "700",
        "color":       "#aaaaaa",
        "padding":     "14px 16px 10px",
        "borderBottom": "1px solid #1c1c1c",
    })


def _divider_row(children, last: bool = False) -> html.Div:
    return html.Div(children, style={
        "display":      "flex",
        "alignItems":   "center",
        "gap":          "10px",
        "padding":      "9px 16px",
        "borderBottom": "none" if last else "1px solid #161616",
    })

# ── Chart builders ────────────────────────────────────────────────────────

def _build_gauge(score: float) -> go.Figure:
    color = _score_color(score)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        # domain pushes the gauge arc into the top 90% of the figure so the
        # number lands below the arc instead of overlapping it.
        domain={"x": [0, 1], "y": [0.1, 1.0]},
        number={
            "font":        {"size": 20, "color": color, "family": "Inter, sans-serif"},
            "valueformat": "+.2f",
        },
        gauge={
            "axis": {
                "range":    [-1, 1],
                "tickvals": [-1, -0.5, 0, 0.5, 1],
                "ticktext": ["-1.0", "-0.5", "0", "+0.5", "+1.0"],
                "tickfont": {"size": 10, "color": "#444444"},
                "tickwidth": 1,
                "tickcolor": "#333333",
            },
            "bar":        {"color": color, "thickness": 0.28},
            "bgcolor":    "#1a1a1a",
            "borderwidth": 0,
            "steps": [
                {"range": [-1.0, -0.15], "color": "#1e0808"},
                {"range": [-0.15, 0.15], "color": "#141414"},
                {"range": [0.15,  1.0],  "color": "#081e0f"},
            ],
            "threshold": {
                "line":      {"color": color, "width": 3},
                "thickness": 0.85,
                "value":     score,
            },
        },
    ))
    fig.update_layout(
        **_PLOTLY_BASE,
        height=220,
        margin=dict(l=24, r=24, t=20, b=20),
    )
    return fig


def _build_donut(breakdown_df: pd.DataFrame) -> go.Figure:
    bull = bear = neut = 0
    for _, row in breakdown_df.iterrows():
        lbl = str(row.get("sentiment_label", "")).strip()
        cnt = int(row.get("cnt", 0))
        if lbl in ("Bullish", "Somewhat Bullish"):
            bull += cnt
        elif lbl in ("Bearish", "Somewhat Bearish"):
            bear += cnt
        else:
            neut += cnt

    if bull + bear + neut == 0:
        fig = go.Figure()
        fig.add_annotation(text="No data yet", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color="#555555", size=13))
        fig.update_layout(**_PLOTLY_BASE, height=230, margin=dict(l=10, r=10, t=24, b=10))
        return fig

    fig = go.Figure(go.Pie(
        labels=["Bullish", "Bearish", "Neutral"],
        values=[bull, bear, neut],
        hole=0.62,
        marker=dict(
            colors=["#00e676", "#ff5252", "#444444"],
            line=dict(color="#111111", width=2),
        ),
        textinfo="label+percent",
        textfont=dict(size=11, color="#e0e0e0"),
        insidetextorientation="horizontal",
        showlegend=False,
        hovertemplate="%{label}: %{value:,} articles (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        **_PLOTLY_BASE,
        height=230,
        margin=dict(l=10, r=10, t=24, b=10),
    )
    return fig


def _build_hourly(hourly_df: pd.DataFrame) -> go.Figure:
    if hourly_df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No data yet", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color="#555555", size=13))
        fig.update_layout(**_PLOTLY_BASE, height=230, margin=dict(l=30, r=10, t=24, b=40))
        return fig

    pivot = (
        hourly_df
        .groupby(["hour", "sent_group"])["cnt"]
        .sum()
        .unstack(fill_value=0)
        .sort_index()
    )

    try:
        x_labels = [pd.Timestamp(h).strftime("%H:%M") for h in pivot.index]
    except Exception:
        x_labels = [str(h) for h in pivot.index]

    fig = go.Figure()
    for col, color, name in [
        ("bullish", "#00e676", "Bullish"),
        ("neutral", "#444444", "Neutral"),
        ("bearish", "#ff5252", "Bearish"),
    ]:
        y_vals = pivot[col].values if col in pivot.columns else [0] * len(x_labels)
        fig.add_trace(go.Bar(
            name=name, x=x_labels, y=y_vals,
            marker_color=color, marker_opacity=0.85,
            hovertemplate=f"{name}: %{{y}} articles<extra></extra>",
        ))

    fig.update_layout(
        **_PLOTLY_BASE,
        height=230,
        barmode="stack",
        xaxis=dict(showgrid=False, tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(gridcolor="#1c1c1c", tickfont=dict(size=10)),
        legend=dict(
            orientation="h", y=1.12, x=1, xanchor="right",
            font=dict(size=10), bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=30, r=10, t=30, b=50),
    )
    return fig

# ── Card renderers ────────────────────────────────────────────────────────

def _gauge_card(avg_score: float | None) -> html.Div:
    score = avg_score if avg_score is not None else 0.0
    color = _score_color(score)
    label = _score_label(score)
    return html.Div(style=_CARD, children=[
        _section_title("Market Sentiment · 24h"),
        dcc.Graph(
            figure=_build_gauge(score),
            config={"displayModeBar": False},
            style={"height": "220px"},
        ),
        html.Div(
            style={"textAlign": "center", "marginTop": "6px"},
            children=[
                html.Span(f"{score:+.2f}", style={
                    "fontSize": "22px", "fontWeight": "700", "color": color,
                }),
                html.Span(f" · {label}", style={
                    "fontSize": "13px", "color": "#555555", "marginLeft": "8px",
                }),
            ],
        ),
    ])


def _breakdown_card(breakdown_df: pd.DataFrame, total: int) -> html.Div:
    counts: dict[str, int] = {}
    for _, row in breakdown_df.iterrows():
        lbl = str(row.get("sentiment_label", "")).strip()
        counts[lbl] = int(row.get("cnt", 0))

    bull = counts.get("Bullish", 0) + counts.get("Somewhat Bullish", 0)
    bear = counts.get("Bearish", 0) + counts.get("Somewhat Bearish", 0)
    neut = counts.get("Neutral", 0)

    def _pct(n: int) -> str:
        return f"{n / total * 100:.0f}%" if total else "—"

    def _row(dot_color, label, count, pct, last=False):
        return html.Div(
            style={
                "display": "flex", "alignItems": "center", "gap": "10px",
                "padding": "11px 0",
                "borderBottom": "none" if last else "1px solid #1a1a1a",
            },
            children=[
                html.Div(style={
                    "width": "8px", "height": "8px", "borderRadius": "50%",
                    "background": dot_color, "flexShrink": "0",
                }),
                html.Span(label,          style={"fontSize": "13px", "color": "#aaaaaa", "flex": "1"}),
                html.Span(f"{count:,}",   style={"fontSize": "13px", "color": "#e0e0e0", "fontWeight": "600"}),
                html.Span(pct,            style={"fontSize": "11px", "color": "#555555", "width": "36px", "textAlign": "right"}),
            ],
        )

    return html.Div(style=_CARD, children=[
        _section_title("Sentiment Breakdown · 24h"),
        _row("#00e676", "Bullish",  bull, _pct(bull)),
        _row("#ff5252", "Bearish",  bear, _pct(bear)),
        _row("#888888", "Neutral",  neut, _pct(neut), last=True),
    ])


def _activity_card(total: int, scored: int, active_tickers: int) -> html.Div:
    rate = f"{scored / total * 100:.0f}%" if total else "—"

    def _row(label, value, color="#e0e0e0", last=False):
        return html.Div(
            style={
                "display": "flex", "justifyContent": "space-between",
                "alignItems": "center", "padding": "11px 0",
                "borderBottom": "none" if last else "1px solid #1a1a1a",
            },
            children=[
                html.Span(label, style={"fontSize": "13px", "color": "#aaaaaa"}),
                html.Span(str(value), style={"fontSize": "14px", "fontWeight": "700", "color": color}),
            ],
        )

    return html.Div(style=_CARD, children=[
        _section_title("Activity Stats · 24h"),
        _row("Total articles",  f"{total:,}"),
        _row("Articles scored", f"{scored:,}"),
        _row("Scoring rate",    rate,                  "#00d4ff"),
        _row("Active tickers",  f"{active_tickers:,}", "#00d4ff", last=True),
    ])

# ── List / spike renderers ────────────────────────────────────────────────

def _ticker_list(header: str, df: pd.DataFrame, ascending: bool) -> html.Div:
    top5 = df.sort_values("avg_sentiment", ascending=ascending).head(5) if not df.empty else df

    rows: list = [_panel_header(header)]
    if top5.empty:
        rows.append(html.Div("No data yet", style={
            "padding": "20px 16px", "color": "#444", "fontSize": "13px",
        }))
    else:
        items = list(top5.iterrows())
        for idx, (_, r) in enumerate(items):
            score = _safe(r.get("avg_sentiment"))
            color = _score_color(score)
            cnt   = int(r.get("article_count", 0))
            last  = (idx == len(items) - 1)
            rows.append(_divider_row(last=last, children=[
                html.A(r["ticker"], href=f"/?keyword={r['ticker']}", style={
                    "color": "#00d4ff", "textDecoration": "none",
                    "fontWeight": "700", "fontSize": "13px",
                    "width": "52px", "flexShrink": "0",
                }),
                html.Div(_badge(score), style={"flex": "1"}),
                html.Span(
                    f"{score:+.2f}" if score is not None else "—",
                    style={"fontFamily": "monospace", "fontSize": "12px", "color": color},
                ),
                html.Span(f"{cnt}", style={"fontSize": "10px", "color": "#444", "flexShrink": "0"}),
            ]))

    return html.Div(style=_PANEL, children=rows)


def _spikes_panel(spikes_df: pd.DataFrame) -> html.Div:
    rows: list = [_panel_header("🔥 Volume Spikes · 24h")]
    if spikes_df.empty:
        rows.append(html.Div("No unusual activity detected", style={
            "padding": "20px 16px", "color": "#444", "fontSize": "13px",
        }))
    else:
        items = list(spikes_df.iterrows())
        for idx, (_, r) in enumerate(items):
            ratio = _safe(r.get("spike_ratio"))
            ratio_text = f"{ratio:.1f}x normal volume" if ratio is not None else "— normal volume"
            try:
                ts = pd.Timestamp(r.get("detected_at")).strftime("%H:%M ET")
            except Exception:
                ts = "—"
            last = (idx == len(items) - 1)
            rows.append(_divider_row(last=last, children=[
                html.A(r["ticker"], href=f"/?keyword={r['ticker']}", style={
                    "color": "#00d4ff", "textDecoration": "none",
                    "fontWeight": "700", "fontSize": "13px",
                    "width": "52px", "flexShrink": "0",
                }),
                html.Span(ratio_text, style={
                    "fontSize": "12px", "color": "#ff9800", "flex": "1",
                }),
                html.Span(ts, style={"fontSize": "10px", "color": "#444", "flexShrink": "0"}),
            ]))

    return html.Div(style=_PANEL, children=rows)


def _chart_card(title: str, graph_id: str) -> html.Div:
    return html.Div(style=_CARD, children=[
        _section_title(title),
        dcc.Graph(id=graph_id, config={"displayModeBar": False}, style={"height": "230px"}),
    ])

# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        dcc.Interval(id="sent-interval", interval=60_000, n_intervals=0),

        # Header
        html.Div(
            style={
                "display": "flex", "justifyContent": "space-between",
                "alignItems": "center", "marginBottom": "16px",
            },
            children=[
                html.Span("Sentiment Overview", style={
                    "fontSize": "16px", "fontWeight": "600", "color": "#e0e0e0",
                }),
                html.Span(id="sent-updated", className="last-refresh"),
            ],
        ),

        # Top row — 3 equal cards
        html.Div(
            id="sent-top-row",
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 1fr 1fr",
                "alignItems": "start",
                "gap": "16px",
                "marginBottom": "16px",
            },
        ),

        # Middle row — 3 equal columns
        html.Div(
            id="sent-mid-row",
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 1fr 1fr",
                "alignItems": "start",
                "gap": "16px",
                "marginBottom": "16px",
            },
        ),

        # Bottom row — donut (1/3) + hourly bar (2/3)
        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 2fr",
                "gap": "16px",
            },
            children=[
                _chart_card("Sentiment Distribution · 24h", "sent-donut"),
                _chart_card("Article Volume by Hour · 24h", "sent-hourly"),
            ],
        ),
    ],
)

# ── Callback ──────────────────────────────────────────────────────────────

@callback(
    Output("sent-top-row",  "children"),
    Output("sent-mid-row",  "children"),
    Output("sent-donut",    "figure"),
    Output("sent-hourly",   "figure"),
    Output("sent-updated",  "children"),
    Input("sent-interval",  "n_intervals"),
    State("url",            "pathname"),
)
def _update(n, pathname):
    if pathname not in (None, "/sentiment"):
        raise PreventUpdate

    # ── Fetch ─────────────────────────────────────────────────────────────
    overview_df  = query_df(_OVERVIEW_SQL)
    breakdown_df = query_df(_BREAKDOWN_SQL)
    tickers_df   = query_df(_ACTIVE_TICKERS_SQL)
    ticker_df    = query_df(_TICKER_SENTIMENT_SQL)
    spikes_df    = query_df(_SPIKES_SQL)
    hourly_df    = query_df(_HOURLY_SQL)

    # ── Parse overview ────────────────────────────────────────────────────
    total = scored = 0
    avg_score = None
    if not overview_df.empty:
        r         = overview_df.iloc[0]
        total     = int(r.get("total",  0))
        scored    = int(r.get("scored", 0))
        avg_score = _safe(r.get("avg_score"))

    active_tickers = 0
    if not tickers_df.empty:
        active_tickers = int(tickers_df.iloc[0].get("active_tickers", 0))

    # ── Build sections ────────────────────────────────────────────────────
    top_row = [
        _gauge_card(avg_score),
        _breakdown_card(breakdown_df, total),
        _activity_card(total, scored, active_tickers),
    ]

    mid_row = [
        _ticker_list("🟢 Top Bullish Tickers",  ticker_df, ascending=False),
        _ticker_list("🔴 Top Bearish Tickers",  ticker_df, ascending=True),
        _spikes_panel(spikes_df),
    ]

    updated = "· " + now_et().strftime("Updated %H:%M ET")
    return top_row, mid_row, _build_donut(breakdown_df), _build_hourly(hourly_df), updated
