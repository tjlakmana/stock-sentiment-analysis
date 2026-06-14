"""
News Feed page — live article stream with filters, manual fetch, and load-more.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import now_et, query_df

dash.register_page(__name__, path="/", name="News", title="News Feed")

# ── Constants ─────────────────────────────────────────────────────────────

PAGE_SIZE = 100

SOURCE_OPTIONS = [
    {"label": "All Sources",                    "value": "all"},
    {"label": "PR Newswire",                    "value": "pr_newswire"},
    {"label": "Globe Newswire — Financial",     "value": "globe_newswire_finance"},
    {"label": "Globe Newswire — M&A",           "value": "globe_newswire_ma"},
    {"label": "SEC — 8-K Major Events",         "value": "sec_edgar"},
    {"label": "SEC — Insider Trading (Form 4)", "value": "sec_form4"},
    {"label": "SEC — Quarterly Reports (10-Q)", "value": "sec_10q"},
    {"label": "SEC — IPO Filings (S-1)",        "value": "sec_s1"},
    {"label": "SEC — Large Investors (SC 13G)", "value": "sec_sc13g"},
    {"label": "FDA Press Releases",             "value": "fda"},
]

SENTIMENT_OPTIONS = [
    {"label": "All Sentiment",    "value": "all"},
    {"label": "Bullish",          "value": "Bullish"},
    {"label": "Somewhat Bullish", "value": "Somewhat Bullish"},
    {"label": "Neutral",          "value": "Neutral"},
    {"label": "Somewhat Bearish", "value": "Somewhat Bearish"},
    {"label": "Bearish",          "value": "Bearish"},
    {"label": "Scored Only",      "value": "_scored"},
    {"label": "Unscored",         "value": "_unscored"},
]

TIME_OPTIONS = [
    {"label": "Last 30 Minutes", "value": "30m"},
    {"label": "Last 1 Hour",     "value": "1h"},
    {"label": "Last 4 Hours",    "value": "4h"},
    {"label": "Last 24 Hours",   "value": "24h"},
    {"label": "Last 7 Days",     "value": "7d"},
    {"label": "All Time",        "value": "all"},
]

REFRESH_OPTIONS = [
    {"label": "Off",    "value": "off"},
    {"label": "10 sec", "value": "10"},
    {"label": "30 sec", "value": "30"},
    {"label": "1 min",  "value": "60"},
    {"label": "5 min",  "value": "300"},
]

_SOURCE_META: dict[str, tuple[str, str]] = {
    "pr_newswire":            ("PRN", "#00d4ff"),
    "globe_newswire_finance": ("GNW", "#9b59b6"),
    "globe_newswire_ma":      ("GNW", "#9b59b6"),
    "sec_edgar":              ("8-K", "#f39c12"),
    "sec_form4":              ("F-4", "#f39c12"),
    "sec_10q":                ("10Q", "#f39c12"),
    "sec_s1":                 ("S-1", "#f39c12"),
    "sec_sc13g":              ("13G", "#f39c12"),
    "fda":                    ("FDA", "#e74c3c"),
}

_SENTIMENT_STYLE: dict[str, dict] = {
    "Bullish":          {"bg": "#0d3324", "color": "#00e676", "bd": "#00c853"},
    "Somewhat Bullish": {"bg": "#092b18", "color": "#69f0ae", "bd": "#00e676"},
    "Neutral":          {"bg": "#131c38", "color": "#82b1ff", "bd": "#3d5afe"},
    "Somewhat Bearish": {"bg": "#351212", "color": "#ff8a80", "bd": "#ff5252"},
    "Bearish":          {"bg": "#280808", "color": "#ff5252", "bd": "#d50000"},
}

_SENT_BORDER: dict[str, str] = {
    "Bullish":          "#00ff88",
    "Somewhat Bullish": "#00ff88",
    "Neutral":          "#888888",
    "Somewhat Bearish": "#ff4444",
    "Bearish":          "#ff4444",
}

_TIME_CLAUSE: dict[str, str] = {
    "30m": "ingested_at > NOW() - INTERVAL '30 minutes'",
    "1h":  "ingested_at > NOW() - INTERVAL '1 hour'",
    "4h":  "ingested_at > NOW() - INTERVAL '4 hours'",
    "24h": "ingested_at > NOW() - INTERVAL '24 hours'",
    "7d":  "ingested_at > NOW() - INTERVAL '7 days'",
}

# ── Categories ────────────────────────────────────────────────────────────

CATEGORIES: dict[str, dict | None] = {
    "🔥 Breaking": None,
    "📈 Earnings": {
        "sources": ["pr_newswire", "globe_newswire_finance"],
        "keywords": ["earnings", "revenue", "EPS", "guidance", "beat", "miss",
                     "quarterly", "annual", "profit", "loss", "results",
                     "Q1", "Q2", "Q3", "Q4"],
    },
    "🏛️ SEC Filings": {
        "sources": ["sec_edgar", "sec_form4", "sec_10q", "sec_s1", "sec_sc13g"],
        "keywords": [],
    },
    "📰 Press Releases": {
        "sources": ["pr_newswire", "globe_newswire_finance", "globe_newswire_ma"],
        "keywords": [],
    },
    "💊 Biotech/FDA": {
        "sources": ["fda", "pr_newswire", "globe_newswire_finance"],
        "keywords": ["FDA", "approval", "clinical", "trial", "drug", "therapy",
                     "biotech", "pharmaceutical", "NDA", "BLA", "IND",
                     "phase 1", "phase 2", "phase 3", "biologics", "vaccine", "treatment"],
    },
    "🏦 M&A": {
        "sources": ["globe_newswire_ma", "pr_newswire"],
        "keywords": ["merger", "acquisition", "acquires", "takeover", "buyout",
                     "deal", "agreement", "combine", "divest", "spinoff",
                     "joint venture", "partnership", "stake"],
    },
    "👤 Insider Trading": {
        "sources": ["sec_form4"],
        "keywords": [],
    },
    "🌍 Macro": {
        "sources": ["globe_newswire_finance"],
        "keywords": ["Federal Reserve", "Fed", "interest rate", "inflation",
                     "CPI", "GDP", "unemployment", "jobs", "payroll",
                     "treasury", "yield", "economic", "macro", "recession",
                     "FOMC", "Powell", "monetary policy"],
    },
    "🚀 IPO": {
        "sources": ["sec_s1", "pr_newswire"],
        "keywords": ["IPO", "initial public offering", "S-1", "listing",
                     "goes public", "debut", "direct listing", "SPAC"],
    },
    "₿ Crypto": {
        "sources": ["pr_newswire"],
        "keywords": ["bitcoin", "crypto", "blockchain", "ethereum",
                     "digital asset", "cryptocurrency", "defi", "NFT",
                     "web3", "BTC", "ETH", "stablecoin", "altcoin"],
    },
}

_CAT_NAMES = list(CATEGORIES.keys())
CATEGORY_OPTIONS = [{"label": cat, "value": cat} for cat in _CAT_NAMES]


def _build_category_clause(cat_name: str) -> tuple[str, dict]:
    """Return (sql_fragment, params) for a single category filter."""
    if cat_name not in CATEGORIES or CATEGORIES[cat_name] is None:
        # Breaking: no extra restriction — fully controlled by the time dropdown
        return "", {}

    cat = CATEGORIES[cat_name]
    conds: list[str] = []
    params: dict = {}

    sources = cat["sources"]
    placeholders = ", ".join(f":_catsrc{i}" for i in range(len(sources)))
    for i, src in enumerate(sources):
        params[f"_catsrc{i}"] = src
    conds.append(f"source_name IN ({placeholders})")

    keywords = cat["keywords"]
    if keywords:
        kw_parts = []
        for i, kw in enumerate(keywords):
            kw_parts.append(f"(title ILIKE :_catkw{i} OR summary ILIKE :_catkw{i})")
            params[f"_catkw{i}"] = f"%{kw}%"
        conds.append(f"({' OR '.join(kw_parts)})")

    return " AND ".join(conds), params


# ── Fetch state (module-level, single-process) ────────────────────────────

_fetch_lock = threading.Lock()   # prevents concurrent fetches
_fetch_done = threading.Event()  # set when background thread finishes


def _run_ingest_thread() -> None:
    """Run the RSS ingestor in a background thread."""
    import asyncio
    try:
        from sentiment_analysis.ingestion.rss_ingestor import RSSIngestor
        asyncio.run(RSSIngestor().run())
    except Exception:
        pass
    finally:
        _fetch_done.set()
        _fetch_lock.release()


# ── Render helpers ────────────────────────────────────────────────────────

def _badge(label: str) -> html.Span:
    s = _SENTIMENT_STYLE.get(label, {})
    return html.Span(
        label or "—",
        style={
            "background":   s.get("bg",    "#141414"),
            "color":        s.get("color", "#444444"),
            "border":       f"1px solid {s.get('bd', '#282828')}",
            "borderRadius": "12px",
            "padding":      "3px 10px",
            "fontSize":     "11px",
            "fontWeight":   "600",
            "whiteSpace":   "nowrap",
            "display":      "inline-block",
        },
    )


def _source_chip(source: str) -> html.Span:
    abbr, color = _SOURCE_META.get(source, (source[:3].upper() if source else "???", "#555555"))
    return html.Span(
        abbr,
        style={
            "background":    f"{color}1e",
            "color":         color,
            "border":        f"1px solid {color}55",
            "borderRadius":  "4px",
            "padding":       "2px 7px",
            "fontSize":      "10px",
            "fontWeight":    "700",
            "letterSpacing": "0.4px",
            "whiteSpace":    "nowrap",
            "fontFamily":    "monospace",
        },
    )


def _score_color(score) -> str:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "#3a3a3a"
    s = float(score)
    if s >= 0.35:   return "#00e676"
    elif s >= 0.15: return "#69f0ae"
    elif s > -0.15: return "#82b1ff"
    elif s > -0.35: return "#ff8a80"
    return "#ff5252"


def _render_row(i: int, row: dict) -> html.Div:
    score = row.get("sentiment_score")
    label = row.get("sentiment_label", "")
    is_nan = score is None or (isinstance(score, float) and math.isnan(score))
    score_val = float(score) if not is_nan else None
    score_text = f"{score_val:+.2f}" if score_val is not None else "—"
    border_color = _SENT_BORDER.get(label, "#333333")

    return html.Div(
        className="article-row",
        style={"borderLeft": f"3px solid {border_color}"},
        children=[
            html.Span(row.get("time_str", ""), className="col-time"),
            _source_chip(row.get("source_name", "")),
            html.Span(row.get("tickers", "") or "", className="col-tickers"),
            html.A(
                (row.get("title") or "")[:150],
                href=row.get("url") or "#",
                target="_blank",
                rel="noopener noreferrer",
                className="headline-link",
            ),
            _badge(label),
            html.Span(
                score_text,
                style={
                    "color":      _score_color(score),
                    "fontFamily": "monospace",
                    "fontSize":   "12px",
                    "textAlign":  "right",
                },
            ),
        ],
    )


def _is_english(title: str) -> bool:
    if not title:
        return True
    non_ascii = sum(1 for c in title if ord(c) > 127)
    return (non_ascii / len(title)) <= 0.15


# ── Query helpers ─────────────────────────────────────────────────────────

_ENGLISH_FILTER = (
    "(octet_length(COALESCE(title,'')) - length(COALESCE(title,''))) "
    "<= length(COALESCE(title,'')) * 0.15"
)


def _build_where(
    keyword, source, sentiment, time_range, category: str | None = None
) -> tuple[str, dict]:
    conds: list[str] = [_ENGLISH_FILTER]
    params: dict = {}

    if keyword:
        conds.append("(title ILIKE :keyword OR summary ILIKE :keyword)")
        params["keyword"] = f"%{keyword}%"

    if source and source != "all":
        conds.append("source_name = :source")
        params["source"] = source

    if sentiment and sentiment not in ("all", None):
        if sentiment == "_scored":
            conds.append("sentiment_score IS NOT NULL")
        elif sentiment == "_unscored":
            conds.append("sentiment_score IS NULL")
        else:
            conds.append("sentiment_label = :sentiment")
            params["sentiment"] = sentiment

    if time_range and time_range != "all":
        clause = _TIME_CLAUSE.get(time_range)
        if clause:
            conds.append(clause)

    if category:
        cat_sql, cat_params = _build_category_clause(category)
        if cat_sql:
            conds.append(f"({cat_sql})")
            params.update(cat_params)

    return (" AND ".join(conds) if conds else "1=1"), params


# ── Layout ────────────────────────────────────────────────────────────────

_TABLE_HEADER = html.Div(
    className="table-header",
    children=[
        html.Span(c, className="th")
        for c in ["Time", "Source", "Tickers", "Headline", "Sentiment", "Score"]
    ],
)

_SELECT_H = {"height": "36px"}

_FETCH_BTN_STYLE = {
    "background":   "#00d4ff",
    "color":        "#000000",
    "border":       "none",
    "borderRadius": "4px",
    "padding":      "0 14px",
    "height":       "36px",
    "fontSize":     "13px",
    "fontWeight":   "600",
    "cursor":       "pointer",
    "whiteSpace":   "nowrap",
    "fontFamily":   "inherit",
    "flexShrink":   "0",
}

_FETCH_BTN_BUSY = {
    **_FETCH_BTN_STYLE,
    "background": "#007a94",
    "cursor":     "wait",
    "opacity":    "0.75",
}

_LOAD_MORE_SHOWN = {"textAlign": "center", "marginTop": "12px"}
_LOAD_MORE_HIDDEN = {"display": "none"}

layout = html.Div(
    className="page-content",
    children=[
        # ── Intervals & stores ────────────────────────────────────────────
        dcc.Interval(id="news-refresh",    interval=30_000, n_intervals=0),
        dcc.Interval(id="news-fetch-poll", interval=2_000,  n_intervals=0, disabled=True),
        dcc.Store(id="news-rows-limit",  data=PAGE_SIZE),
        dcc.Store(id="news-fetch-store", data={"done_at": None}),

        # ── Filter row ────────────────────────────────────────────────────
        html.Div(
            className="filter-bar",
            style={"flexWrap": "nowrap", "marginBottom": "10px"},
            children=[
                dbc.Select(
                    id="news-category",
                    options=CATEGORY_OPTIONS,
                    value="🔥 Breaking",
                    className="filter-select",
                    style={"minWidth": "165px", **_SELECT_H},
                ),
                dbc.Select(
                    id="news-source",
                    options=SOURCE_OPTIONS,
                    value="all",
                    className="filter-select",
                    style={**_SELECT_H},
                ),
                dbc.Select(
                    id="news-sentiment",
                    options=SENTIMENT_OPTIONS,
                    value="all",
                    className="filter-select",
                    style={**_SELECT_H},
                ),
                dbc.Select(
                    id="news-time",
                    options=TIME_OPTIONS,
                    value="4h",  # default for Breaking
                    className="filter-select",
                    style={**_SELECT_H},
                ),
                dcc.Input(
                    id="news-keyword",
                    placeholder="🔍  Search headlines…",
                    debounce=True,
                    className="filter-input",
                    style={"height": "36px", "flex": "1", "minWidth": "140px"},
                ),
                # ── Right side ─────────────────────────────────────────
                html.Button(
                    "⚡ Fetch",
                    id="news-fetch-btn",
                    n_clicks=0,
                    style=_FETCH_BTN_STYLE,
                ),
                dbc.Select(
                    id="news-autorefresh",
                    options=REFRESH_OPTIONS,
                    value="30",
                    className="filter-select",
                    style={"minWidth": "90px", **_SELECT_H},
                ),
            ],
        ),

        # ── Divider ───────────────────────────────────────────────────────
        html.Hr(style={"borderColor": "#1c1c1c", "margin": "0 0 12px", "opacity": "1"}),

        # ── Count bar ─────────────────────────────────────────────────────
        html.Div(
            className="count-bar",
            children=[
                html.Span(id="news-count",   className="count-label"),
                html.Span(id="news-updated", className="last-refresh"),
            ],
        ),

        # ── Article table ─────────────────────────────────────────────────
        html.Div(
            className="articles-table",
            children=[_TABLE_HEADER, html.Div(id="news-rows")],
        ),

        # ── Load More ─────────────────────────────────────────────────────
        html.Div(
            id="news-load-more-wrapper",
            style=_LOAD_MORE_HIDDEN,
            children=[
                html.Button(
                    "Load More",
                    id="news-load-more",
                    n_clicks=0,
                    className="load-more-btn",
                ),
            ],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("news-time", "value"),
    Input("news-category", "value"),
    prevent_initial_call=True,
)
def _set_time_default(category: str):
    """Switch default time window when category changes."""
    return "4h" if category == "🔥 Breaking" else "24h"


@callback(
    Output("news-rows-limit", "data"),
    Input("news-keyword",   "value"),
    Input("news-source",    "value"),
    Input("news-sentiment", "value"),
    Input("news-time",      "value"),
    Input("news-category",  "value"),
    prevent_initial_call=True,
)
def _reset_rows(*_):
    """Reset to first PAGE_SIZE rows when any filter or category changes."""
    return PAGE_SIZE


@callback(
    Output("news-rows-limit", "data", allow_duplicate=True),
    Input("news-load-more", "n_clicks"),
    State("news-rows-limit", "data"),
    prevent_initial_call=True,
)
def _load_more(n_clicks, current_limit):
    """Extend the visible row limit by PAGE_SIZE."""
    return (current_limit or PAGE_SIZE) + PAGE_SIZE


@callback(
    Output("news-refresh", "interval"),
    Output("news-refresh", "disabled"),
    Input("news-autorefresh", "value"),
)
def _set_autorefresh(value: str):
    """Map the auto-refresh selector to the interval component."""
    if value == "off":
        return 30_000, True
    return int(value) * 1_000, False


@callback(
    Output("news-fetch-btn",   "children"),
    Output("news-fetch-btn",   "disabled"),
    Output("news-fetch-btn",   "style"),
    Output("news-fetch-poll",  "disabled"),
    Input("news-fetch-btn",    "n_clicks"),
    prevent_initial_call=True,
)
def _do_fetch(n_clicks):
    """Start a one-shot RSS ingest in a background thread."""
    if not _fetch_lock.acquire(blocking=False):
        raise PreventUpdate  # fetch already in progress

    _fetch_done.clear()
    t = threading.Thread(target=_run_ingest_thread, daemon=True)
    t.start()

    return "⚡ Fetching...", True, _FETCH_BTN_BUSY, False  # enable poll


@callback(
    Output("news-fetch-btn",   "children",  allow_duplicate=True),
    Output("news-fetch-btn",   "disabled",  allow_duplicate=True),
    Output("news-fetch-btn",   "style",     allow_duplicate=True),
    Output("news-fetch-poll",  "disabled",  allow_duplicate=True),
    Output("news-fetch-store", "data"),
    Input("news-fetch-poll",   "n_intervals"),
    prevent_initial_call=True,
)
def _check_fetch(n):
    """Poll until the background fetch thread signals completion."""
    if not _fetch_done.is_set():
        raise PreventUpdate
    _fetch_done.clear()
    done_ts = now_et().isoformat()
    return "⚡ Fetch", False, _FETCH_BTN_STYLE, True, {"done_at": done_ts}


@callback(
    Output("news-rows",              "children"),
    Output("news-count",             "children"),
    Output("news-updated",           "children"),
    Output("news-load-more-wrapper", "style"),
    Input("news-refresh",            "n_intervals"),
    Input("news-category",           "value"),
    Input("news-keyword",            "value"),
    Input("news-source",             "value"),
    Input("news-sentiment",          "value"),
    Input("news-time",               "value"),
    Input("news-rows-limit",         "data"),
    Input("news-fetch-store",        "data"),
    State("url",                     "pathname"),
)
def _update_feed(n, category, keyword, source, sentiment, time_range, rows_limit,
                 fetch_store, pathname):
    """Fetch and render articles; responds to any filter, refresh, or fetch event."""
    if pathname not in (None, "/"):
        raise PreventUpdate

    active_cat = category or "🔥 Breaking"
    limit = int(rows_limit or PAGE_SIZE)
    where, params = _build_where(keyword, source, sentiment, time_range, category=active_cat)

    count_df = query_df(
        f"SELECT COUNT(*) AS total FROM rss_articles WHERE {where}", params
    )
    total = int(count_df["total"].iloc[0]) if not count_df.empty else 0

    df = query_df(f"""
        SELECT
            to_char(ingested_at AT TIME ZONE 'America/New_York', 'MM-DD HH24:MI') AS time_str,
            source_name,
            COALESCE(array_to_string(tickers, ', '), '') AS tickers,
            COALESCE(title,   '')                        AS title,
            COALESCE(url,     '#')                       AS url,
            COALESCE(sentiment_label, '')                AS sentiment_label,
            sentiment_score
        FROM rss_articles
        WHERE {where}
        ORDER BY ingested_at DESC NULLS LAST
        LIMIT {limit}
    """, params)

    count_text = f"Showing {min(total, limit):,} of {total:,} article{'s' if total != 1 else ''} in {active_cat}"
    updated    = "· " + now_et().strftime("Updated %H:%M ET")

    load_more_style = _LOAD_MORE_SHOWN if total > limit else _LOAD_MORE_HIDDEN

    if df.empty:
        rows = [html.Div("No articles match the current filters.", className="no-results")]
        return rows, count_text, updated, _LOAD_MORE_HIDDEN

    records = [r for r in df.to_dict("records") if _is_english(r.get("title", ""))]
    rows = [_render_row(i, r) for i, r in enumerate(records)]

    return rows, count_text, updated, load_more_style


@callback(
    Output("news-category", "options"),
    Input("news-refresh",    "n_intervals"),
    Input("news-fetch-store","data"),
    Input("url",             "pathname"),
    State("news-keyword",    "value"),
    State("news-sentiment",  "value"),
    State("news-time",       "value"),
)
def _update_category_counts(n, fetch_store, pathname, keyword, sentiment, time_range):
    """Refresh category dropdown option labels with live counts."""
    if pathname not in (None, "/"):
        raise PreventUpdate
    opts = []
    for cat_name in _CAT_NAMES:
        where, params = _build_where(keyword, None, sentiment, time_range, category=cat_name)
        df = query_df(f"SELECT COUNT(*) AS total FROM rss_articles WHERE {where}", params)
        count = int(df["total"].iloc[0]) if not df.empty else 0
        label = f"{cat_name}  ({count})" if count else cat_name
        opts.append({"label": label, "value": cat_name})
    return opts
