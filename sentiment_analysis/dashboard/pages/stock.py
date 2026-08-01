"""
Module: stock.py
Purpose: Dash page providing a per-ticker research workspace at /stock/<TICKER>
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Stock Workspace — per-ticker research page at /stock/<TICKER>.

Single scrollable page. No tabs. All sections visible on load.
"""
from __future__ import annotations

import math

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import (
    query_df,
    watchlist_add,
    watchlist_remove,
    watchlist_tickers,
)
from sentiment_analysis.dashboard.formatters import (
    _fmt_mktcap,
    _fmt_pct,
    _fmt_ratio,
    _fmt_relvol,
)

dash.register_page(
    __name__,
    path_template="/stock/<ticker>",
    name="Stock",
    title="Stock Workspace",
)

# ── SQL ───────────────────────────────────────────────────────────────────

_PRICE_SQL = """
    SELECT price, change_pct, volume, updated_at, company_name, sector,
           country, exchange, market_cap, pre_market_price, post_market_price,
           pe_ratio, forward_pe, rel_volume, rsi_14,
           peg_ratio, price_to_sales, price_to_book, dividend_yield, eps_ttm,
           roe, roa, gross_margin, operating_margin, net_margin,
           debt_to_equity, current_ratio, quick_ratio,
           eps_growth_this_year, eps_growth_next_year, eps_growth_5y,
           beta, sma_20_pct, sma_50_pct, sma_200_pct,
           week_52_high_pct, week_52_low_pct, avg_volume, atr,
           float_short, short_ratio, insider_own, inst_own,
           perf_week, perf_month
    FROM ticker_prices
    WHERE ticker = :ticker
"""

_SENTIMENT_SQL = """
    SELECT avg_sentiment, article_count, bullish_count, bearish_count,
           neutral_count, momentum, calculated_at
    FROM ticker_sentiment_summary
    WHERE ticker = :ticker AND "window" = '24hr'
    ORDER BY calculated_at DESC
    LIMIT 1
"""

_SEC_FILINGS_SQL = """
    SELECT title, source_name, COALESCE(published_at, ingested_at) AS filed_at, url
    FROM rss_articles
    WHERE source_name IN ('sec_edgar','sec_form4','sec_10q','sec_s1','sec_sc13g')
      AND (:ticker = ANY(tickers) OR primary_ticker = :ticker)
    ORDER BY COALESCE(published_at, ingested_at) DESC
    LIMIT 15
"""

_SEC_FORM_LABELS: dict[str, str] = {
    "sec_edgar":  "8-K",
    "sec_form4":  "Form 4",
    "sec_10q":    "10-Q",
    "sec_s1":     "S-1",
    "sec_sc13g":  "SC 13G",
}

_RECENT_NEWS_SQL = """
    SELECT title, source_name, ingested_at, sentiment_label, url
    FROM rss_articles
    WHERE :ticker = ANY(tickers)
      AND ingested_at > NOW() - INTERVAL '7 days'
      AND sentiment_score IS NOT NULL
    ORDER BY
        (primary_ticker = :ticker) DESC,
        ingested_at DESC
    LIMIT 20
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



def _fmt_volume(v) -> str:
    v = _safe(v)
    if v is None:   return "—"
    if v >= 1e9:    return f"{v/1e9:.2f}B"
    if v >= 1e6:    return f"{v/1e6:.2f}M"
    if v >= 1e3:    return f"{v/1e3:.1f}K"
    return str(int(v))


def _fmt_ts(val) -> str:
    try:
        return pd.Timestamp(val).strftime("%b %d, %Y  %I:%M %p ET")
    except Exception:
        return "—"


def _fmt_relative_time(val) -> str:
    """
    Convert a timestamp to a human-readable relative time string.
    Used in the Recent News section so readers see '15 minutes ago' instead
    of a full datetime. Handles both tz-aware and tz-naive timestamps.
    """
    try:
        ts = pd.Timestamp(val)
        now = pd.Timestamp.now(tz=ts.tzinfo)
        secs = max(int((now - ts).total_seconds()), 0)
        if secs < 60:
            return "Just now"
        if secs < 3600:
            m = secs // 60
            return f"{m} minute ago" if m == 1 else f"{m} minutes ago"
        if secs < 86400:
            h = secs // 3600
            return f"{h} hour ago" if h == 1 else f"{h} hours ago"
        if secs < 172800:
            return "Yesterday"
        d = secs // 86400
        if d < 7:
            return f"{d} days ago"
        return ts.strftime("%b %d")
    except Exception:
        return "Unknown Time"


def _score_label(score: float | None) -> str:
    if score is None:  return "Neutral"
    if score >= 0.35:  return "Bullish"
    if score >= 0.15:  return "Somewhat Bullish"
    if score > -0.15:  return "Neutral"
    if score > -0.35:  return "Somewhat Bearish"
    return "Bearish"


_SENT_COLOR: dict[str, str] = {
    "Bullish":          "#00e676",
    "Somewhat Bullish": "#69f0ae",
    "Neutral":          "#82b1ff",
    "Somewhat Bearish": "#ff8a80",
    "Bearish":          "#ff5252",
}

_SENT_BG: dict[str, str] = {
    "Bullish":          "#0d3324",
    "Somewhat Bullish": "#092b18",
    "Neutral":          "#131c38",
    "Somewhat Bearish": "#351212",
    "Bearish":          "#280808",
}

_SENT_BORDER: dict[str, str] = {
    "Bullish":          "#00c853",
    "Somewhat Bullish": "#00e676",
    "Neutral":          "#3d5afe",
    "Somewhat Bearish": "#ff5252",
    "Bearish":          "#d50000",
}

# Maps the 5-category sentiment labels to 3 colored dot emojis for the news badge.
_SENT_DOT: dict[str, str] = {
    "Bullish":          "🟢",
    "Somewhat Bullish": "🟢",
    "Neutral":          "🟡",
    "Somewhat Bearish": "🔴",
    "Bearish":          "🔴",
}


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
        "&allow_symbol_change=false&studies=%5B%5D"
    )


# ── UI building blocks ────────────────────────────────────────────────────

def _section(title: str, children: list) -> html.Div:
    return html.Div(className="sw-section", children=[
        html.Div(title, className="sw-section-title"),
        *[c for c in children if c is not None],
    ])


def _coming_soon(title: str) -> html.Div:
    return _section(title, [
        html.Div(className="sw-coming-soon", children=[
            html.Div("Coming Soon", className="sw-cs-label"),
            html.Div(
                f"{title} data will appear here when available.",
                className="sw-cs-sub",
            ),
        ]),
    ])


def _metric_card(
    label: str,
    value: str,
    sub: str = "",
    icon: str = "",
    value_color: str = "",
    status: str = "",
    status_color: str = "",
    tooltip: str = "",
) -> html.Div:
    """
    Reusable metric display card for Key Metrics, Financial Highlights,
    and Technical Indicators sections.

    Args:
        label:        Uppercase label above the value (e.g. "P/E Ratio").
        value:        Primary display string (e.g. "23.50" or "Bullish").
        sub:          Optional secondary line below the value.
        icon:         Optional icon character rendered above the label.
        value_color:  CSS color applied to the value text (e.g. "#00e676").
        status:       Optional status badge text (e.g. "Oversold").
        status_color: CSS color for the status badge text.
        tooltip:      Plain-English description shown on hover.
    """
    children = []
    if icon:
        children.append(html.Span(icon, className="sw-metric-icon"))
    children.append(html.Div(label, className="sw-metric-label"))
    children.append(
        html.Div(
            value,
            className="sw-metric-value",
            style={"color": value_color} if value_color else {},
        )
    )
    if status:
        children.append(
            html.Span(
                status,
                className="sw-metric-status",
                style={"color": status_color} if status_color else {},
            )
        )
    if sub:
        children.append(html.Div(sub, className="sw-metric-sub"))

    extra = {"data-tooltip": tooltip} if tooltip else {}
    return html.Div(className="sw-metric-card", children=children, **extra)


def _rsi_status(v) -> tuple[str, str]:
    """Return (status_label, color) for an RSI value. Empty strings when neutral."""
    v = _safe(v)
    if v is None:
        return "", ""
    if v < 30:
        return "Oversold", "#00e676"
    if v > 70:
        return "Overbought", "#ff5252"
    return "", ""


def _relvol_color(v) -> str:
    """Return a CSS color reflecting how unusual today's volume is vs. average."""
    v = _safe(v)
    if v is None:
        return ""
    if v >= 2.0:
        return "#00d4ff"
    if v >= 1.5:
        return "#82b1ff"
    if v < 0.5:
        return "#555"
    return ""


def _sent_badge(label: str) -> html.Span:
    return html.Span(
        label,
        style={
            "background":   _SENT_BG.get(label, "#141414"),
            "color":        _SENT_COLOR.get(label, "#888"),
            "border":       f"1px solid {_SENT_BORDER.get(label, '#282828')}",
            "borderRadius": "12px",
            "padding":      "3px 12px",
            "fontSize":     "13px",
            "fontWeight":   "600",
        },
    )


def _news_sentiment_badge(label: str) -> html.Span:
    """
    Compact news-list sentiment badge. Shows a colored dot emoji followed by
    the sentiment label. Falls back to Neutral when label is missing or unknown.
    Reusable in any section that needs a compact per-article sentiment indicator.
    """
    resolved = label if label in _SENT_DOT else "Neutral"
    dot   = _SENT_DOT[resolved]
    color = _SENT_COLOR.get(resolved, _SENT_COLOR["Neutral"])
    return html.Span(
        f"{dot} {resolved}",
        className="sw-news-badge",
        style={"color": color},
    )


def _sign_color(v) -> str:
    v = _safe(v)
    if v is None: return ""
    if v > 0:     return "#00e676"
    if v < 0:     return "#ff5252"
    return "#888"


def _beta_color(v) -> str:
    v = _safe(v)
    if v is None: return "#444"
    if v < 0.5:  return "#82b1ff"
    if v <= 1.5: return "#888"
    if v <= 2.5: return "#ffab40"
    return "#ff5252"


def _peg_color(v) -> str:
    v = _safe(v)
    if v is None: return ""
    return "#00e676" if v < 1.0 else "#ffab40" if v < 2.0 else "#ff5252"


def _ps_color(v) -> str:
    v = _safe(v)
    if v is None: return ""
    return "#00e676" if v < 2.0 else "#ffab40" if v < 5.0 else "#ff5252"


def _pb_color(v) -> str:
    v = _safe(v)
    if v is None: return ""
    return "#00e676" if v < 1.5 else "#ffab40" if v < 3.0 else "#ff5252"


def _div_color(v) -> str:
    v = _safe(v)
    if v is None: return ""
    return "#00e676" if v >= 3.0 else "#ffab40" if v >= 1.0 else "#888"


def _fmt_atr(v) -> str:
    v = _safe(v)
    return f"${v:.2f}" if v is not None else "—"


def _fmt_date(val) -> str:
    try:
        return pd.Timestamp(val).strftime("%b %d, %Y")
    except Exception:
        return "—"


def _fmt_short_ratio(v) -> str:
    v = _safe(v)
    return f"{v:.1f}d" if v is not None else "—"


# ── Visual component builders ─────────────────────────────────────────────

def _vis_group(title: str, children: list) -> html.Div:
    return html.Div(className="sw-vis-group", children=[
        html.Div(title, className="sw-highlight-group-title"),
        *[c for c in children if c is not None],
    ])


def _vis_bar(label: str, value, max_val: float, tooltip: str = "") -> html.Div:
    """Signed horizontal progress bar. Bar length = |value| / max_val; color encodes sign."""
    v = _safe(value)
    if v is None:
        bar_pct, display, color = 0, "—", "#2a2e42"
    else:
        bar_pct = min(abs(v), max_val) / max_val * 100
        sign    = "+" if v > 0 else ""
        display = f"{sign}{v:.1f}%"
        color   = "#00e676" if v >= 0 else "#ff5252"
    kwargs = {"title": tooltip} if tooltip else {}
    return html.Div(className="sw-vis-bar-row", children=[
        html.Span(label, className="sw-vis-bar-label"),
        html.Div(className="sw-vis-bar-track", children=[
            html.Div(className="sw-vis-bar-fill",
                     style={"width": f"{bar_pct:.1f}%", "background": color}),
        ]),
        html.Span(display, className="sw-vis-bar-value",
                  style={"color": color if v is not None else "#444"}),
    ], **kwargs)


def _health_bar(label: str, value, max_val: float,
                good_above: float | None = None,
                good_below: float | None = None,
                threshold_at: float | None = None,
                tooltip: str = "") -> html.Div:
    """Progress bar for ratio metrics with an optional threshold marker."""
    v = _safe(value)
    if v is None:
        bar_pct, display, color = 0, "—", "#2a2e42"
    else:
        bar_pct = min(abs(v), max_val) / max_val * 100
        display = f"{v:.2f}"
        if good_above is not None:
            color = "#00e676" if v >= good_above else "#ff5252"
        elif good_below is not None:
            color = "#00e676" if v <= good_below else "#ff8a80"
        else:
            color = "#82b1ff"

    thresh_els = []
    if threshold_at is not None and v is not None:
        t_pct = min(threshold_at, max_val) / max_val * 100
        thresh_els = [html.Div(className="sw-vis-bar-threshold",
                               style={"left": f"{t_pct:.1f}%"})]

    kwargs = {"title": tooltip} if tooltip else {}
    return html.Div(className="sw-vis-bar-row", children=[
        html.Span(label, className="sw-vis-bar-label"),
        html.Div(className="sw-vis-bar-track", children=[
            html.Div(className="sw-vis-bar-fill",
                     style={"width": f"{bar_pct:.1f}%", "background": color}),
            *thresh_els,
        ]),
        html.Span(display, className="sw-vis-bar-value",
                  style={"color": color if v is not None else "#444"}),
    ], **kwargs)


def _val_chip(label: str, value: str, color: str = "", tooltip: str = "") -> html.Div:
    return html.Div(className="sw-val-chip", title=tooltip, children=[
        html.Div(label, className="sw-val-chip-label"),
        html.Div(value, className="sw-val-chip-value",
                 style={"color": color} if color else {}),
    ])


def _rsi_gauge_fig(value) -> go.Figure:
    v = _safe(value)
    if v is None:
        v, gauge_color = 50, "#2a2e42"
    elif v < 30:
        gauge_color = "#00e676"
    elif v > 70:
        gauge_color = "#ff5252"
    else:
        gauge_color = "#82b1ff"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=v,
        number={"font": {"size": 32, "color": "#cdd6f4", "family": "Inter"}},
        gauge={
            "axis": {
                "range": [0, 100],
                "tickwidth": 1,
                "tickcolor": "#333",
                "tickvals": [0, 30, 70, 100],
                "ticktext": ["0", "30", "70", "100"],
                "tickfont": {"size": 10, "color": "#555"},
            },
            "bar": {"color": gauge_color, "thickness": 0.2},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 30],   "color": "rgba(0, 230, 118, 0.12)"},
                {"range": [30, 70],  "color": "rgba(130, 177, 255, 0.07)"},
                {"range": [70, 100], "color": "rgba(255, 82, 82, 0.12)"},
            ],
        },
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=170,
        margin={"t": 30, "b": 0, "l": 20, "r": 20},
        font={"family": "Inter, system-ui"},
    )
    return fig


def _sma_row(label: str, pct_val, tooltip: str = "") -> html.Div:
    v = _safe(pct_val)
    if v is None:
        direction, badge_text, pct_str = "na", "N/A", "—"
    elif v >= 0:
        direction, badge_text, pct_str = "above", "ABOVE", f"+{v:.1f}%"
    else:
        direction, badge_text, pct_str = "below", "BELOW", f"{v:.1f}%"

    return html.Div(className="sw-sma-row", title=tooltip, children=[
        html.Span(label, className="sw-sma-label"),
        html.Span(badge_text, className=f"sw-sma-badge sw-sma-badge--{direction}"),
        html.Span(pct_str,    className=f"sw-sma-pct   sw-sma-pct--{direction}"),
    ])


def _range_indicator(price_val, week_52_high_pct_val, week_52_low_pct_val) -> html.Div:
    p     = _safe(price_val)
    h_pct = _safe(week_52_high_pct_val)
    l_pct = _safe(week_52_low_pct_val)

    if p is None or h_pct is None or l_pct is None:
        return html.Div("Data unavailable", className="sw-vis-na",
                        style={"padding": "20px 0"})
    try:
        low52  = p / (1 + l_pct / 100)
        high52 = p / (1 + h_pct / 100)
        rng    = high52 - low52
        pos    = max(2.0, min(98.0, (p - low52) / rng * 100)) if rng > 0 else 50.0
    except ZeroDivisionError:
        return html.Div("Data unavailable", className="sw-vis-na",
                        style={"padding": "20px 0"})

    return html.Div(className="sw-range-indicator", children=[
        html.Div(className="sw-range-track", children=[
            html.Div(className="sw-range-fill", style={"width": f"{pos:.1f}%"}),
            html.Div(className="sw-range-marker", style={"left": f"{pos:.1f}%"}),
        ]),
        html.Div(className="sw-range-labels", children=[
            html.Span(f"${low52:.2f}",   className="sw-range-low"),
            html.Span(f"{pos:.0f}% of range", className="sw-range-pos"),
            html.Span(f"${high52:.2f}",  className="sw-range-high"),
        ]),
    ])


def _relvol_visual(rel_vol_val, avg_vol_val) -> html.Div:
    rv = _safe(rel_vol_val)
    av = _safe(avg_vol_val)
    if rv is None:
        return html.Div("—", className="sw-vis-na")
    bar_pct = min(rv, 3.0) / 3.0 * 100
    color   = _relvol_color(rv) or "#888"
    children: list = [
        html.Div(className="sw-vis-bar-row", children=[
            html.Span("Rel. Volume", className="sw-vis-bar-label"),
            html.Div(className="sw-vis-bar-track", children=[
                html.Div(className="sw-vis-bar-fill",
                         style={"width": f"{bar_pct:.1f}%", "background": color}),
                html.Div(className="sw-vis-bar-marker", style={"left": "33.3%"}),
            ]),
            html.Span(f"{rv:.2f}×", className="sw-vis-bar-value",
                      style={"color": color}),
        ]),
    ]
    if av is not None:
        children.append(html.Div(f"Avg: {_fmt_volume(av)}", className="sw-vis-sub"))
    return html.Div(children=children)


def _beta_visual(beta_val) -> html.Div:
    v = _safe(beta_val)
    color   = _beta_color(v)
    bar_pct = min(abs(v), 3.0) / 3.0 * 100 if v is not None else 0
    display = f"{v:.2f}" if v is not None else "—"
    return html.Div(className="sw-vis-bar-row",
                    title="Sensitivity to market movements. 1.0 = moves with the market.", children=[
        html.Span("Beta", className="sw-vis-bar-label"),
        html.Div(className="sw-vis-bar-track", children=[
            html.Div(className="sw-vis-bar-fill",
                     style={"width": f"{bar_pct:.1f}%", "background": color}),
            html.Div(className="sw-vis-bar-marker", style={"left": "33.3%"}),
        ]),
        html.Span(display, className="sw-vis-bar-value", style={"color": color}),
    ])


def _ownership_donut(pct_raw, label: str, color: str) -> html.Div:
    v = _safe(pct_raw)
    if v is None:
        return html.Div(className="sw-donut-card", children=[
            html.Div(className="sw-donut-empty-inner", children=[
                html.Div("—", className="sw-donut-na-value"),
            ]),
            html.Div(label, className="sw-donut-label"),
        ])
    remaining = max(0.0, 100.0 - v)
    fig = go.Figure(go.Pie(
        values=[v, remaining],
        labels=[label, "Other"],
        hole=0.65,
        marker_colors=[color, "#1a1f35"],
        marker_line={"width": 0},
        textinfo="none",
        hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
        sort=False,
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=180,
        margin={"t": 5, "b": 5, "l": 5, "r": 5},
        showlegend=False,
        annotations=[{
            "text": f"{v:.1f}%",
            "x": 0.5, "y": 0.52,
            "font": {"size": 24, "color": "#cdd6f4", "family": "Inter, system-ui"},
            "showarrow": False,
        }],
    )
    return html.Div(className="sw-donut-card", children=[
        dcc.Graph(figure=fig,
                  config={"displayModeBar": False, "responsive": True},
                  style={"height": "180px"}),
        html.Div(label, className="sw-donut-label"),
    ])


# ── Full page renderer ────────────────────────────────────────────────────

def _render_page(ticker: str) -> html.Div:
    price_df = query_df(_PRICE_SQL,          {"ticker": ticker})
    sent_df  = query_df(_SENTIMENT_SQL,      {"ticker": ticker})
    news_df  = query_df(_RECENT_NEWS_SQL,    {"ticker": ticker})
    sec_df   = query_df(_SEC_FILINGS_SQL,    {"ticker": ticker})

    p = price_df.iloc[0].to_dict() if not price_df.empty else {}
    s = sent_df.iloc[0].to_dict()  if not sent_df.empty  else {}

    # ── Derived price fields ──────────────────────────────────────────────
    price        = _safe(p.get("price"))
    chg          = _safe(p.get("change_pct"))
    company      = str(p.get("company_name") or "")
    sector       = str(p.get("sector")       or "")
    country      = str(p.get("country")      or "")
    exchange_val = str(p.get("exchange")     or "")
    market_cap   = p.get("market_cap")
    volume       = p.get("volume")
    updated_at   = p.get("updated_at")
    pe_ratio     = p.get("pe_ratio")
    forward_pe   = p.get("forward_pe")
    rel_volume   = p.get("rel_volume")
    rsi_14       = p.get("rsi_14")

    # Financial Highlights fields
    peg_ratio             = p.get("peg_ratio")
    price_to_sales        = p.get("price_to_sales")
    price_to_book         = p.get("price_to_book")
    dividend_yield        = p.get("dividend_yield")
    eps_ttm               = p.get("eps_ttm")
    roe                   = p.get("roe")
    roa                   = p.get("roa")
    gross_margin          = p.get("gross_margin")
    operating_margin      = p.get("operating_margin")
    net_margin            = p.get("net_margin")
    debt_to_equity        = p.get("debt_to_equity")
    current_ratio         = p.get("current_ratio")
    quick_ratio           = p.get("quick_ratio")
    eps_growth_this_year  = p.get("eps_growth_this_year")
    eps_growth_next_year  = p.get("eps_growth_next_year")
    eps_growth_5y         = p.get("eps_growth_5y")
    perf_week             = p.get("perf_week")
    perf_month            = p.get("perf_month")

    # Technical Indicators fields
    beta          = p.get("beta")
    sma_20_pct    = p.get("sma_20_pct")
    sma_50_pct    = p.get("sma_50_pct")
    sma_200_pct   = p.get("sma_200_pct")
    week_52_high  = p.get("week_52_high_pct")
    week_52_low   = p.get("week_52_low_pct")
    avg_volume    = p.get("avg_volume")
    atr           = p.get("atr")
    float_short   = p.get("float_short")
    short_ratio   = p.get("short_ratio")

    # Insider Activity fields
    insider_own = p.get("insider_own")
    inst_own    = p.get("inst_own")

    chg_str, chg_color = _fmt_chg(chg)
    upd_str = _fmt_ts(updated_at)

    # ── Derived sentiment fields ──────────────────────────────────────────
    avg_score  = _safe(s.get("avg_sentiment"))
    art_count  = int(s.get("article_count", 0))
    bull_count = int(s.get("bullish_count", 0))
    bear_count = int(s.get("bearish_count", 0))
    neut_count = int(s.get("neutral_count", 0))
    momentum   = str(s.get("momentum") or "stable").capitalize() if s else "—"
    sent_label = _score_label(avg_score)
    sent_color = _SENT_COLOR.get(sent_label, "#888")
    total      = art_count or 1
    bull_pct   = bull_count / total * 100
    bear_pct   = bear_count / total * 100
    neut_pct   = neut_count / total * 100
    mom_color  = ("#00e676" if momentum == "Improving"
                  else "#ff5252" if momentum == "Declining" else "#555")

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 1 — Header
    # ─────────────────────────────────────────────────────────────────────

    # Metadata line: Exchange • Sector • Country (omit any field that is empty)
    meta_parts = [p for p in [exchange_val, sector, country] if p]
    meta_str   = " • ".join(meta_parts)

    in_watchlist = ticker in set(watchlist_tickers())
    wl_label = "⭐ Watching" if in_watchlist else "☆ Watch"
    wl_cls   = "sw-wl-btn sw-wl-btn-active" if in_watchlist else "sw-wl-btn"

    header = html.Div(className="sw-header", children=[
        html.Div(className="sw-header-top", children=[
            dcc.Link("← Screener", href="/screener", className="stock-back-link"),
            html.Div(className="sw-header-top-right", children=[
                html.Button(
                    wl_label,
                    id="stock-wl-btn",
                    className=wl_cls,
                    n_clicks=0,
                ),
                html.Div(className="sw-price-block", children=[
                    html.Span(_fmt_price(price), className="sw-price"),
                    html.Span(chg_str, className="sw-chg",
                              style={"color": chg_color}),
                ]),
            ]),
        ]),
        html.Div(className="sw-header-body", children=[
            html.Span(ticker,  className="stock-ticker-hero"),
            html.Span(company, className="stock-company-name",
                      id="stock-company-name"),
        ]),
        html.Div(className="sw-header-foot", children=[
            html.Span(meta_str, className="sw-header-meta") if meta_str else None,
            html.Span(
                f"Updated {upd_str}" if upd_str != "—" else "",
                className="sw-updated",
            ),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 2 — Price Chart
    # ─────────────────────────────────────────────────────────────────────

    chart = _section("Price Chart", [
        html.Div(
            style={"width": "100%", "height": "520px"},
            children=[html.Iframe(
                src=_tv_src(ticker),
                style={"width": "100%", "height": "100%",
                       "border": "none", "display": "block"},
            )],
        ),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 3 — Key Metrics
    # ─────────────────────────────────────────────────────────────────────

    rsi_lbl, rsi_color = _rsi_status(rsi_14)

    metrics = _section("Key Metrics", [
        html.Div(className="sw-metric-grid", children=[
            _metric_card(
                "Current Price",
                _fmt_price(price),
                tooltip="Current market price per share.",
            ),
            _metric_card(
                "Daily Change",
                chg_str if chg is not None else "N/A",
                value_color=chg_color if chg is not None else "#555",
                tooltip=(
                    "How much the price has moved today compared to "
                    "yesterday's closing price."
                ),
            ),
            _metric_card(
                "Market Cap",
                _fmt_mktcap(market_cap),
                tooltip=(
                    "Total market value of all outstanding shares. "
                    "Calculated as price × total shares outstanding."
                ),
            ),
            _metric_card(
                "P/E Ratio",
                _fmt_ratio(pe_ratio, decimals=2),
                sub="Trailing 12 months",
                tooltip=(
                    "Price-to-Earnings ratio (trailing 12 months). "
                    "Shows how much investors pay per dollar of earnings. "
                    "Lower is generally cheaper relative to current earnings."
                ),
            ),
            _metric_card(
                "Forward P/E",
                _fmt_ratio(forward_pe, decimals=2),
                sub="Next year estimates",
                tooltip=(
                    "Expected P/E based on next year's estimated earnings. "
                    "Often lower than the trailing P/E for growing companies."
                ),
            ),
            _metric_card(
                "Relative Volume",
                _fmt_relvol(rel_volume),
                sub="vs. avg daily volume",
                value_color=_relvol_color(rel_volume),
                tooltip=(
                    "Today's volume compared to the stock's average daily volume. "
                    "Above 1.0x means more activity than usual. "
                    "Spikes often signal a news event or earnings release."
                ),
            ),
            _metric_card(
                "RSI (14)",
                _fmt_ratio(rsi_14, decimals=1),
                status=rsi_lbl,
                status_color=rsi_color,
                tooltip=(
                    "Relative Strength Index over 14 days. "
                    "Below 30 may indicate oversold conditions (potential buy). "
                    "Above 70 may indicate overbought conditions (potential sell)."
                ),
            ),
            _metric_card(
                "24h Sentiment",
                sent_label if avg_score is not None else "N/A",
                sub=f"Score {avg_score:+.3f}" if avg_score is not None else "",
                value_color=sent_color if avg_score is not None else "#555",
                tooltip=(
                    "Average news sentiment from the past 24 hours, "
                    "based on FinBERT analysis of articles mentioning this ticker."
                ),
            ),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 4 — Recent News
    # ─────────────────────────────────────────────────────────────────────

    news_rows: list = []
    for _, n in news_df.iterrows():
        title    = str(n.get("title")           or "").strip()
        url      = str(n.get("url")             or "#").strip()
        source   = str(n.get("source_name")     or "").strip() or "Unknown Source"
        sent_lbl = str(n.get("sentiment_label") or "").strip()
        rel_ts   = _fmt_relative_time(n.get("ingested_at"))

        if not title:
            continue

        news_rows.append(html.Div(className="sw-news-row", children=[
            html.A(
                title,
                href=url,
                target="_blank",
                rel="noopener noreferrer",
                className="sw-news-title",
            ),
            html.Div(className="sw-news-footer", children=[
                html.Div(className="sw-news-byline", children=[
                    html.Span(source, className="sw-news-source"),
                    html.Span(" · ", className="sw-news-sep"),
                    html.Span(rel_ts,  className="sw-news-ts"),
                ]),
                _news_sentiment_badge(sent_lbl),
            ]),
        ]))

    news_section = _section("Recent News", [
        html.Div(
            news_rows if news_rows else [
                html.Div(className="sw-news-empty", children=[
                    html.Div("No recent news", className="sw-news-empty-title"),
                    html.Div(
                        "No articles mentioning this ticker have been found "
                        "in the past 7 days.",
                        className="sw-news-empty-sub",
                    ),
                ]),
            ],
            className="sw-news-list",
        ),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 5 — Sentiment Analytics
    # ─────────────────────────────────────────────────────────────────────

    def _bar_row(label: str, count: int, pct: float, color: str) -> html.Div:
        return html.Div(className="sw-bar-row", children=[
            html.Span(label, className="sw-bar-label"),
            html.Div(className="sw-bar-track", children=[
                html.Div(className="sw-bar-fill",
                         style={"width": f"{pct:.1f}%",
                                "background": color}),
            ]),
            html.Span(f"{count}  ({pct:.0f}%)", className="sw-bar-count"),
        ])

    sentiment_section = _section("Sentiment Analytics", [
        html.Div(className="sw-sent-grid", children=[
            html.Div(className="sw-sent-overall", children=[
                html.Div("24h Sentiment", className="sw-metric-label"),
                _sent_badge(sent_label),
                html.Div(
                    f"Score: {avg_score:+.4f}" if avg_score is not None else "Score: —",
                    className="sw-metric-sub",
                    style={"marginTop": "8px"},
                ),
            ]),
            html.Div(className="sw-sent-breakdown", children=[
                _bar_row("Bullish", bull_count, bull_pct, "#00e676"),
                _bar_row("Neutral", neut_count, neut_pct, "#82b1ff"),
                _bar_row("Bearish", bear_count, bear_pct, "#ff5252"),
                html.Div(
                    f"Based on {art_count} article{'s' if art_count != 1 else ''}"
                    " in the past 24 hours",
                    className="sw-bar-footnote",
                ),
            ]),
            html.Div(className="sw-sent-trend", children=[
                html.Div("Momentum", className="sw-metric-label"),
                html.Div(momentum,   className="sw-metric-value",
                         style={"color": mom_color}),
                html.Div("Sentiment direction trend",
                         className="sw-metric-sub"),
            ]),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 6 — Financial Highlights
    # ─────────────────────────────────────────────────────────────────────

    financial_highlights = _section("Financial Highlights", [
        html.Div(className="sw-fh-grid", children=[
            _vis_group("Profitability", [
                _vis_bar("Gross Margin",     gross_margin,      100, "Revenue minus COGS as a % of revenue."),
                _vis_bar("Operating Margin", operating_margin,   50, "Operating income as a % of revenue. Reflects operational efficiency."),
                _vis_bar("Net Margin",       net_margin,         50, "Net income as a % of revenue after all expenses."),
                _vis_bar("ROE",              roe,                50, "Return on Equity — net income as a % of shareholders' equity."),
                _vis_bar("ROA",              roa,                25, "Return on Assets — how efficiently the company uses its assets to generate profit."),
            ]),
            _vis_group("Growth & Performance", [
                _vis_bar("EPS This Year", eps_growth_this_year, 100, "EPS growth estimate for the current fiscal year."),
                _vis_bar("EPS Next Year", eps_growth_next_year, 100, "EPS growth estimate for the next fiscal year."),
                _vis_bar("EPS 5Y CAGR",  eps_growth_5y,        100, "5-year compound annual EPS growth rate estimate."),
                html.Div(className="sw-vis-divider"),
                _vis_bar("Perf Week",  perf_week,  20, "Price performance over the past week."),
                _vis_bar("Perf Month", perf_month, 30, "Price performance over the past month."),
            ]),
            _vis_group("Financial Health", [
                _health_bar("Current Ratio", current_ratio,  4.0,
                            good_above=1.5, threshold_at=1.5,
                            tooltip="Above 1.5 is generally healthy."),
                _health_bar("Quick Ratio",   quick_ratio,    3.0,
                            good_above=1.0, threshold_at=1.0,
                            tooltip="Above 1.0: short-term debts covered without selling inventory."),
                _health_bar("Debt / Equity", debt_to_equity, 5.0,
                            good_below=1.0, threshold_at=1.0,
                            tooltip="Total debt relative to equity. Lower is generally safer."),
            ]),
            _vis_group("Valuation", [
                html.Div(className="sw-val-chips", children=[
                    _val_chip("PEG Ratio", _fmt_ratio(peg_ratio,      2), _peg_color(peg_ratio),      "P/E ÷ earnings growth. Below 1.0 may signal undervaluation."),
                    _val_chip("P / Sales", _fmt_ratio(price_to_sales, 2), _ps_color(price_to_sales),  "Price-to-Sales. Lower suggests cheaper revenue relative to market cap."),
                    _val_chip("P / Book",  _fmt_ratio(price_to_book,  2), _pb_color(price_to_book),   "Market value vs. book value of assets."),
                    _val_chip("Div Yield", _fmt_pct(dividend_yield),      _div_color(dividend_yield), "Annual dividend as a % of share price."),
                    _val_chip("EPS (TTM)", _fmt_ratio(eps_ttm,         2), _sign_color(eps_ttm),       "Earnings Per Share over the trailing twelve months."),
                ]),
            ]),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 7 — Technical Indicators
    # ─────────────────────────────────────────────────────────────────────

    technical_indicators = _section("Technical Indicators", [
        html.Div(className="sw-ti-grid", children=[
            _vis_group("Momentum — RSI (14)", [
                dcc.Graph(
                    figure=_rsi_gauge_fig(rsi_14),
                    config={"displayModeBar": False, "responsive": True},
                    style={"height": "170px"},
                ),
                html.Div(
                    rsi_lbl if rsi_lbl else "Neutral Zone",
                    className="sw-rsi-label",
                    style={"color": rsi_color if rsi_color else "#82b1ff"},
                ),
                html.Div(className="sw-rsi-zones", children=[
                    html.Span("Oversold < 30",   className="sw-rsi-zone-label sw-rsi-oversold"),
                    html.Span("Overbought > 70", className="sw-rsi-zone-label sw-rsi-overbought"),
                ]),
            ]),
            _vis_group("Trend", [
                _sma_row("SMA 20",  sma_20_pct,  "Price vs. 20-day SMA. Positive = above (bullish short-term)."),
                _sma_row("SMA 50",  sma_50_pct,  "Price vs. 50-day SMA. Positive = above (bullish medium-term)."),
                _sma_row("SMA 200", sma_200_pct, "Price vs. 200-day SMA. Positive = above (bullish long-term)."),
            ]),
            _vis_group("Volatility", [
                _beta_visual(beta),
                html.Div(className="sw-vis-divider"),
                html.Div(className="sw-vis-bar-row", children=[
                    html.Span("ATR", className="sw-vis-bar-label"),
                    html.Div(style={"flex": 1}),
                    html.Span(_fmt_atr(atr), className="sw-vis-bar-value",
                              style={"color": "#cdd6f4"},
                              title="Average True Range — avg daily price movement over 14 days."),
                ]),
            ]),
            _vis_group("52-Week Range", [
                _range_indicator(price, week_52_high, week_52_low),
            ]),
            _vis_group("Trading", [
                _relvol_visual(rel_volume, avg_volume),
                html.Div(className="sw-vis-divider"),
                html.Div(className="sw-vis-bar-row", children=[
                    html.Span("Volume", className="sw-vis-bar-label"),
                    html.Div(style={"flex": 1}),
                    html.Span(_fmt_volume(volume), className="sw-vis-bar-value",
                              style={"color": "#cdd6f4"},
                              title="Today's trading volume."),
                ]),
            ]),
            _vis_group("Short Interest", [
                _vis_bar("Float Short", float_short, 50,
                         "% of float sold short. High values may indicate bearish sentiment."),
                html.Div(className="sw-vis-bar-row", children=[
                    html.Span("Short Ratio", className="sw-vis-bar-label"),
                    html.Div(style={"flex": 1}),
                    html.Span(_fmt_short_ratio(short_ratio), className="sw-vis-bar-value",
                              style={"color": "#cdd6f4"},
                              title="Days-to-cover — how many days of avg volume to cover short positions."),
                ]),
            ]),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 8 — Insider Activity
    # ─────────────────────────────────────────────────────────────────────

    has_ownership = _safe(insider_own) is not None or _safe(inst_own) is not None

    insider_activity = _section("Insider Activity", [
        html.Div(className="sw-ownership-grid", children=[
            _ownership_donut(inst_own,    "Institutional", "#82b1ff"),
            _ownership_donut(insider_own, "Insider",       "#00e676"),
        ]) if has_ownership else html.Div(className="sw-news-empty", children=[
            html.Div("No ownership data",       className="sw-news-empty-title"),
            html.Div("Ownership percentages are not available for this ticker.",
                     className="sw-news-empty-sub"),
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 9 — SEC Filings
    # ─────────────────────────────────────────────────────────────────────

    sec_rows: list = []
    for _, f in sec_df.iterrows():
        title     = str(f.get("title")       or "").strip()
        source    = str(f.get("source_name") or "").strip()
        url       = str(f.get("url")         or "#").strip()
        filed_at  = _fmt_date(f.get("filed_at"))
        form_lbl  = _SEC_FORM_LABELS.get(source, source.upper())

        if not title:
            continue

        sec_rows.append(html.Div(className="sw-sec-row", children=[
            html.Span(form_lbl, className="sw-sec-type"),
            html.Div(className="sw-sec-body", children=[
                html.A(
                    title,
                    href=url,
                    target="_blank",
                    rel="noopener noreferrer",
                    className="sw-sec-title",
                ),
                html.Span(filed_at, className="sw-sec-date"),
            ]),
        ]))

    sec_filings = _section("SEC Filings", [
        html.Div(
            sec_rows if sec_rows else [
                html.Div(className="sw-news-empty", children=[
                    html.Div("No recent filings", className="sw-news-empty-title"),
                    html.Div(
                        "No SEC filings for this ticker have been ingested yet.",
                        className="sw-news-empty-sub",
                    ),
                ]),
            ],
            className="sw-sec-list",
        ),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 10 — AI Insights (hidden — implementation pending)
    # ─────────────────────────────────────────────────────────────────────
    # ai_insights = _coming_soon("AI Insights")

    return html.Div([
        header,
        metrics,
        chart,
        news_section,
        sentiment_section,
        financial_highlights,
        technical_indicators,
        insider_activity,
        sec_filings,
        # ai_insights,
    ])


# ── Layout ────────────────────────────────────────────────────────────────

def layout(ticker: str = "", **kwargs) -> html.Div:
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return html.Div(
            "No ticker specified.",
            className="page-content",
            style={"padding": "24px", "color": "#555"},
        )

    return html.Div(
        className="page-content",
        children=[
            dcc.Store(id="stock-ticker-store", data=ticker),
            dcc.Interval(id="stock-interval", interval=60_000, n_intervals=0),
            html.Div(id="stock-page-body", children=_render_page(ticker)),
        ],
    )


# ── Callbacks ─────────────────────────────────────────────────────────────

@callback(
    Output("stock-page-body",  "children"),
    Input("stock-interval",    "n_intervals"),
    State("stock-ticker-store", "data"),
    Input("url",               "pathname"),
    prevent_initial_call=True,
)
def _refresh_page(_, ticker, pathname):
    if not pathname or not pathname.startswith("/stock/"):
        raise PreventUpdate
    if not ticker:
        raise PreventUpdate
    return _render_page(ticker)


@callback(
    Output("stock-wl-btn", "children"),
    Output("stock-wl-btn", "className"),
    Input("stock-wl-btn",          "n_clicks"),
    State("stock-ticker-store",    "data"),
    prevent_initial_call=True,
)
def _toggle_watchlist(_, ticker):
    if not ticker:
        raise PreventUpdate
    watched = set(watchlist_tickers())
    if ticker in watched:
        watchlist_remove(ticker)
        return "☆ Watch", "sw-wl-btn"
    else:
        watchlist_add(ticker)
        return "⭐ Watching", "sw-wl-btn sw-wl-btn-active"
