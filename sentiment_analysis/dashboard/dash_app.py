"""
Stock Sentiment — Plotly Dash multi-page application (Phase 5A)

Run:
    python -m sentiment_analysis.dashboard.dash_app
    # or via main.py: python -m sentiment_analysis.main
"""
from __future__ import annotations

from datetime import datetime

import dash
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, callback, dcc, html

from sentiment_analysis.ingestion.ticker_list import SP500_TICKERS

# ── App instance ──────────────────────────────────────────────────────────

app = Dash(
    __name__,
    use_pages=True,
    suppress_callback_exceptions=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    title="Stock Sentiment",
)
server = app.server  # expose Flask server for gunicorn/deployment

# Bootstrap 5.3 built-in dark mode — sets CSS variables globally
app.index_string = """<!DOCTYPE html>
<html data-bs-theme="dark">
<head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
</head>
<body>
    {%app_entry%}
    <footer>
        {%config%}
        {%scripts%}
        {%renderer%}
    </footer>
</body>
</html>"""

# ── Sidebar navigation config ─────────────────────────────────────────────

_NAV = [
    ("📰", "News",         "/"),
    ("🔍", "Screener",    "/screener"),
    ("📈", "Charts",      "/charts"),
    ("⭐", "Watchlist",   "/watchlist"),
    ("🚨", "Alerts",      "/alerts"),
]

# Stable IDs derived from label text
def _nav_id(label: str) -> str:
    return f"nav-{label.lower().replace(' ', '-')}"


_SP500_COUNT = len([t for t in SP500_TICKERS if not any(
    t in etf for etf in ("SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT",
                          "HYG", "LQD", "VTI", "VOO", "VEA", "VWO", "EFA",
                          "EEM", "XLF", "XLK", "XLE", "XLV", "XLI", "XLY",
                          "XLP", "XLU", "XLB", "XLRE", "PLTR", "RIVN", "LCID",
                          "SOFI", "GME", "AMC", "BB", "NOK", "COIN", "HOOD",
                          "ROKU", "SNAP", "UBER", "LYFT", "ABNB", "DASH",
                          "PINS", "TWTR", "SHOP", "SQ", "PYPL", "AFRM", "UPST")
)])


def _sidebar() -> html.Div:
    return html.Div(
        className="sidebar",
        children=[
            html.Nav(
                className="nav-menu",
                children=[
                    dcc.Link(
                        id=_nav_id(label),
                        href=path,
                        className="nav-link",
                        children=[
                            html.Span(icon, className="nav-icon"),
                            html.Span(label),
                        ],
                    )
                    for icon, label, path in _NAV
                ],
            ),
            html.Div(
                className="sidebar-footer",
                children=[
                    html.Span("S&P 500", className="watchlist-label"),
                    html.Span(
                        f"{len(SP500_TICKERS)} tickers",
                        className="watchlist-count",
                    ),
                ],
            ),
        ],
    )


# ── Root layout ───────────────────────────────────────────────────────────

app.layout = html.Div(
    className="app-shell",
    children=[
        dcc.Location(id="url"),
        dcc.Interval(id="topbar-interval", interval=30_000, n_intervals=0),
        _sidebar(),
        html.Div(
            className="main-column",
            children=[
                # Topbar
                html.Div(
                    className="topbar",
                    children=[
                        html.Span(id="topbar-time", className="topbar-time"),
                        html.Div(
                            className="topbar-right",
                            children=[
                                html.Span(id="topbar-dot", className="status-dot"),
                                html.Span(id="topbar-status", className="topbar-status-text"),
                            ],
                        ),
                    ],
                ),
                # Page content injected here by Dash Pages
                html.Div(className="page-wrapper", children=[dash.page_container]),
            ],
        ),
    ],
)


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    *[Output(_nav_id(label), "className") for _, label, _ in _NAV],
    Input("url", "pathname"),
)
def _highlight_nav(pathname: str):
    """Highlight the active nav link based on current URL."""
    def _cls(path: str) -> str:
        if path == "/" and pathname in ("/", "", None):
            return "nav-link active"
        if path != "/" and pathname == path:
            return "nav-link active"
        # Highlight Screener when viewing any Stock Workspace page
        if path == "/screener" and (pathname or "").startswith("/stock/"):
            return "nav-link active"
        return "nav-link"
    return [_cls(path) for _, _, path in _NAV]


@callback(
    Output("topbar-time", "children"),
    Output("topbar-dot", "className"),
    Output("topbar-status", "children"),
    Input("topbar-interval", "n_intervals"),
)
def _update_topbar(n: int):
    """Refresh the topbar clock and pipeline status dot every 30 s."""
    from sentiment_analysis.dashboard.db import now_et, query_status

    now = now_et().strftime("%Y-%m-%d  %H:%M ET")
    status = query_status()
    dot_cls = {
        "Running":  "status-dot green",
        "Degraded": "status-dot yellow",
    }.get(status, "status-dot red")
    return now, dot_cls, status


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        # use_reloader=False avoids Werkzeug trying to re-exec the process,
        # which misbehaves when launched as a subprocess on Windows.
        app.run(debug=False, port=8050, host="0.0.0.0", use_reloader=False)
    except Exception:
        import traceback
        traceback.print_exc()
        raise
