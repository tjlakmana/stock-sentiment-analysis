"""
Watchlist page — personal dashboard for tracked companies.
Route: /watchlist
"""
from __future__ import annotations

import math

import dash
import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import (
    now_et,
    query_df,
    watchlist_add,
    watchlist_remove,
    watchlist_tickers,
)

dash.register_page(__name__, path="/watchlist", name="Watchlist", title="Watchlist")

# ── SQL ───────────────────────────────────────────────────────────────────

_WATCHLIST_SQL = """
    SELECT
        w.ticker,
        w.created_at                              AS added_at,
        COALESCE(p.company_name, w.ticker)        AS company_name,
        p.price,
        p.change_pct,
        p.updated_at,
        s.avg_sentiment,
        COALESCE(s.article_count, 0)              AS article_count,
        COALESCE(n.news_count, 0)                 AS news_count_24h
    FROM watchlist w
    LEFT JOIN ticker_prices p ON w.ticker = p.ticker
    LEFT JOIN LATERAL (
        SELECT avg_sentiment, article_count
        FROM   ticker_sentiment_summary
        WHERE  ticker = w.ticker AND "window" = '24hr'
        ORDER  BY calculated_at DESC
        LIMIT  1
    ) s ON TRUE
    LEFT JOIN LATERAL (
        SELECT COUNT(*) AS news_count
        FROM   rss_articles
        WHERE  primary_ticker = w.ticker
          AND  ingested_at > NOW() - INTERVAL '24 hours'
    ) n ON TRUE
    ORDER BY w.created_at ASC
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


def _score_label(score: float | None) -> str:
    if score is None:   return "—"
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
    "—":                "#444",
}


# ── Table renderer ────────────────────────────────────────────────────────

def _render_table(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div(className="wl-empty", children=[
            html.Div("⭐", className="wl-empty-icon"),
            html.Div("Your watchlist is empty", className="wl-empty-title"),
            html.Div(
                "Add companies from the Screener or any Company Research page.",
                className="wl-empty-sub",
            ),
        ])

    rows: list = []
    for _, r in df.iterrows():
        ticker   = str(r["ticker"])
        company  = str(r.get("company_name") or ticker)
        score    = _safe(r.get("avg_sentiment"))
        label    = _score_label(score)
        sent_clr = _SENT_COLOR.get(label, "#444")
        chg_str, chg_clr = _fmt_chg(r.get("change_pct"))
        news_ct  = int(r.get("news_count_24h", 0))

        upd_str = "—"
        try:
            upd_str = pd.Timestamp(r.get("updated_at")).strftime("%m-%d %H:%M")
        except Exception:
            pass

        rows.append(html.Tr(className="wl-tr", children=[
            # Remove button
            html.Td(
                html.Button(
                    "★",
                    id={"type": "wl-remove", "ticker": ticker},
                    className="wl-star-btn wl-star-active",
                    n_clicks=0,
                    title=f"Remove {ticker} from watchlist",
                ),
                className="wl-td wl-td-center",
            ),
            # Ticker link
            html.Td(
                dcc.Link(ticker, href=f"/stock/{ticker}", className="wl-ticker"),
                className="wl-td",
            ),
            # Company
            html.Td(company, className="wl-td wl-company"),
            # Price
            html.Td(_fmt_price(r.get("price")), className="wl-td wl-mono"),
            # Change %
            html.Td(chg_str, className="wl-td wl-mono",
                    style={"color": chg_clr}),
            # Sentiment label
            html.Td(label, className="wl-td",
                    style={"color": sent_clr, "fontWeight": "600"}),
            # Sentiment score
            html.Td(
                f"{score:+.3f}" if score is not None else "—",
                className="wl-td wl-mono",
                style={"color": sent_clr},
            ),
            # News 24h
            html.Td(str(news_ct), className="wl-td wl-mono"),
            # Last updated
            html.Td(upd_str, className="wl-td wl-dim"),
        ]))

    return html.Div(
        className="articles-table",
        style={"overflowX": "auto"},
        children=[html.Table(
            className="wl-table",
            children=[
                html.Thead(html.Tr([
                    html.Th(h, className="wl-th", style={"textAlign": a})
                    for h, a in [
                        ("★",          "center"),
                        ("Ticker",     "left"),
                        ("Company",    "left"),
                        ("Price",      "right"),
                        ("Chg %",      "right"),
                        ("Sentiment",  "left"),
                        ("Score",      "right"),
                        ("News 24h",   "right"),
                        ("Updated",    "center"),
                    ]
                ])),
                html.Tbody(rows),
            ],
        )],
    )


def _render_body() -> html.Div:
    df       = query_df(_WATCHLIST_SQL)
    count    = len(df)
    time_str = now_et().strftime("%H:%M ET")

    return html.Div([
        html.Div(className="wl-header", children=[
            html.Div(className="wl-header-left", children=[
                html.Span("Watchlist", className="wl-title"),
                html.Span(
                    f"{count} compan{'ies' if count != 1 else 'y'}",
                    className="wl-count",
                ),
            ]),
            html.Span(f"Updated {time_str}", className="wl-updated"),
        ]),
        _render_table(df),
    ])


# ── Layout ────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        dcc.Interval(id="wl-interval", interval=60_000, n_intervals=0),
        dcc.Store(id="wl-store", data=0),
        html.Div(id="wl-body", children=_render_body()),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("wl-store", "data"),
    Input({"type": "wl-remove", "ticker": ALL}, "n_clicks"),
    State("wl-store", "data"),
    prevent_initial_call=True,
)
def _handle_remove(n_clicks_list, version):
    if not any(n for n in (n_clicks_list or [])):
        raise PreventUpdate
    triggered = ctx.triggered_id
    if triggered is None:
        raise PreventUpdate
    watchlist_remove(triggered["ticker"])
    return (version or 0) + 1


@callback(
    Output("wl-body", "children"),
    Input("wl-interval", "n_intervals"),
    Input("wl-store",    "data"),
    Input("url",         "pathname"),
)
def _refresh_watchlist(_, __, pathname):
    if pathname not in (None, "/watchlist"):
        raise PreventUpdate
    return _render_body()
