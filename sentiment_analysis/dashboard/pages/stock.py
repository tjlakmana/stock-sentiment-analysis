"""
Stock Workspace — per-ticker research page at /stock/<TICKER>.

Single scrollable page. No tabs. All sections visible on load.
"""
from __future__ import annotations

import math

import dash
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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

# ── Message Density chart config ─────────────────────────────────────────

_TF_OPTIONS = [
    {"label": "1D", "value": "1D"},
    {"label": "5D", "value": "5D"},
    {"label": "1M", "value": "1M"},
    {"label": "3M", "value": "3M"},
    {"label": "6M", "value": "6M"},
    {"label": "1Y", "value": "1Y"},
]

_TF_CONFIG: dict[str, dict] = {
    "1D":  {"days": 1,   "bucket": "hour",  "price_trunc": None},
    "5D":  {"days": 5,   "bucket": "day",   "price_trunc": None},
    "1M":  {"days": 30,  "bucket": "day",   "price_trunc": None},
    "3M":  {"days": 90,  "bucket": "week",  "price_trunc": "week"},
    "6M":  {"days": 180, "bucket": "week",  "price_trunc": "week"},
    "1Y":  {"days": 365, "bucket": "month", "price_trunc": "month"},
}

_SENT_BAR_COLOR: dict[str, str] = {
    "Bullish":          "rgba(0, 230, 118, 0.55)",
    "Somewhat Bullish": "rgba(105, 240, 174, 0.55)",
    "Neutral":          "rgba(130, 177, 255, 0.55)",
    "Somewhat Bearish": "rgba(255, 138, 128, 0.55)",
    "Bearish":          "rgba(255, 82, 82, 0.55)",
}

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


# ── Density chart builders ────────────────────────────────────────────────

def _empty_density_fig(msg: str = "No data available") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        plot_bgcolor="#131722",
        paper_bgcolor="#0e1117",
        height=360,
        margin={"l": 50, "r": 50, "t": 20, "b": 40},
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": msg,
            "xref": "paper", "yref": "paper",
            "x": 0.5, "y": 0.5,
            "showarrow": False,
            "font": {"size": 15, "color": "#555"},
        }],
    )
    return fig


def _build_density_chart(ticker: str, tf: str) -> go.Figure:
    cfg = _TF_CONFIG.get(tf, _TF_CONFIG["1M"])
    days = cfg["days"]
    bucket = cfg["bucket"]
    price_trunc = cfg["price_trunc"]

    if price_trunc is None:
        price_sql = f"""
            SELECT snapshot_date::date AS bucket, price
            FROM ticker_snapshot_history
            WHERE ticker = :ticker
              AND snapshot_date >= CURRENT_DATE - {days}
            ORDER BY bucket
        """
    else:
        price_sql = f"""
            SELECT DATE_TRUNC('{price_trunc}', snapshot_date::timestamp)::date AS bucket,
                   AVG(price) AS price
            FROM ticker_snapshot_history
            WHERE ticker = :ticker
              AND snapshot_date >= CURRENT_DATE - {days}
            GROUP BY bucket
            ORDER BY bucket
        """
    price_df = query_df(price_sql, {"ticker": ticker})

    art_sql = f"""
        WITH bucketed AS (
            SELECT
                DATE_TRUNC('{bucket}', ingested_at) AS bucket,
                title,
                sentiment_label,
                source_name,
                ROW_NUMBER() OVER (
                    PARTITION BY DATE_TRUNC('{bucket}', ingested_at)
                    ORDER BY ingested_at DESC
                ) AS rn
            FROM rss_articles
            WHERE :ticker = ANY(tickers)
              AND ingested_at >= NOW() - INTERVAL '{days} days'
        )
        SELECT
            bucket,
            COUNT(*) AS article_count,
            MAX(CASE WHEN rn = 1 THEN title END) AS top_title,
            MAX(CASE WHEN rn = 1 THEN sentiment_label END) AS top_sentiment,
            MAX(CASE WHEN rn = 1 THEN source_name END) AS top_source
        FROM bucketed
        GROUP BY bucket
        ORDER BY bucket
    """
    art_df = query_df(art_sql, {"ticker": ticker})

    if price_df.empty and art_df.empty:
        return _empty_density_fig(f"No price or news data found for {ticker}.")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if not price_df.empty:
        fig.add_trace(
            go.Scatter(
                x=price_df["bucket"],
                y=price_df["price"],
                name="Price",
                line={"color": "#82b1ff", "width": 2},
                mode="lines",
                hovertemplate="$%{y:,.2f}<extra>Price</extra>",
            ),
            secondary_y=False,
        )

    if not art_df.empty:
        hover_texts = []
        for _, row in art_df.iterrows():
            cnt   = int(row.get("article_count") or 0)
            title = str(row.get("top_title") or "—")
            if len(title) > 72:
                title = title[:72] + "…"
            sent  = str(row.get("top_sentiment") or "—").strip()
            src   = str(row.get("top_source") or "—").replace("_", " ").title()
            more  = f" <i>(+{cnt - 1} more)</i>" if cnt > 1 else ""
            hover_texts.append(
                f"<b>{cnt} article{'s' if cnt != 1 else ''}</b>{more}<br>"
                f"{title}<br>"
                f"<span style='opacity:.7'>{sent} · {src}</span>"
            )

        bar_colors = [
            _SENT_BAR_COLOR.get(str(s).strip(), "rgba(130, 177, 255, 0.55)")
            for s in art_df["top_sentiment"].fillna("Neutral")
        ]

        fig.add_trace(
            go.Bar(
                x=art_df["bucket"],
                y=art_df["article_count"],
                name="Articles",
                marker_color=bar_colors,
                hovertext=hover_texts,
                hovertemplate="%{hovertext}<extra>Articles</extra>",
            ),
            secondary_y=True,
        )

    fig.update_layout(
        plot_bgcolor="#131722",
        paper_bgcolor="#0e1117",
        font={"color": "#cdd6f4", "family": "Inter, system-ui, sans-serif", "size": 12},
        legend={
            "orientation": "h", "y": 1.06, "x": 0,
            "bgcolor": "rgba(0,0,0,0)", "font": {"size": 12},
        },
        hovermode="x unified",
        margin={"l": 60, "r": 60, "t": 10, "b": 40},
        height=360,
        bargap=0.35,
        xaxis={"gridcolor": "#1a1f35", "linecolor": "#282a36", "tickcolor": "#282a36"},
    )
    fig.update_yaxes(
        title_text="Price ($)",
        secondary_y=False,
        gridcolor="#1a1f35",
        tickfont={"color": "#82b1ff"},
        title_font={"color": "#82b1ff"},
        tickprefix="$",
        linecolor="#282a36",
    )
    fig.update_yaxes(
        title_text="Articles",
        secondary_y=True,
        gridcolor="rgba(0,0,0,0)",
        tickfont={"color": "#00c896"},
        title_font={"color": "#00c896"},
        linecolor="#282a36",
    )
    return fig


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


def _ratio_health_color(v, good_above: float) -> str:
    v = _safe(v)
    if v is None: return ""
    return "#00e676" if v >= good_above else "#ff5252"


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


def _highlight_group(title: str, rows: list) -> html.Div:
    """Compact label-value stat group for Financial Highlights and Technical Indicators."""
    stat_rows = []
    for label, value, color, tooltip in rows:
        kwargs = {"title": tooltip} if tooltip else {}
        stat_rows.append(
            html.Div(className="sw-stat-row", children=[
                html.Span(label, className="sw-stat-label"),
                html.Span(
                    value,
                    className="sw-stat-value",
                    style={"color": color} if color else {},
                    **kwargs,
                ),
            ])
        )
    return html.Div(className="sw-highlight-group", children=[
        html.Div(title, className="sw-highlight-group-title"),
        *stat_rows,
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
    # SECTION 2b — Message Density vs. Price
    # ─────────────────────────────────────────────────────────────────────

    density_section = html.Div(className="sw-section", children=[
        html.Div(className="sw-density-header", children=[
            html.Div("Message Density vs. Price", className="sw-section-title"),
            dcc.RadioItems(
                id="stock-tf-radio",
                options=_TF_OPTIONS,
                value="1M",
                inline=True,
                className="sw-tf-radio",
                inputStyle={"display": "none"},
            ),
        ]),
        dcc.Graph(
            id="stock-density-chart",
            style={"height": "360px"},
            config={"displayModeBar": False, "responsive": True},
            figure=_empty_density_fig("Loading…"),
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

    fh_valuation = _highlight_group("Valuation", [
        ("PEG Ratio",    _fmt_ratio(peg_ratio, decimals=2),     "", "PEG Ratio — P/E divided by earnings growth rate. Below 1.0 may signal undervaluation relative to growth."),
        ("Price / Sales", _fmt_ratio(price_to_sales, decimals=2), "", "Price-to-Sales ratio. Lower values suggest cheaper revenue relative to market cap."),
        ("Price / Book",  _fmt_ratio(price_to_book, decimals=2),  "", "Price-to-Book ratio. Compares market value to the book value of assets."),
        ("EPS (TTM)",     _fmt_ratio(eps_ttm, decimals=2),         "", "Earnings Per Share over the trailing twelve months."),
    ])

    fh_profitability = _highlight_group("Profitability", [
        ("Gross Margin",     _fmt_pct(gross_margin),     _sign_color(gross_margin),     "Revenue minus cost of goods sold, as a % of revenue."),
        ("Operating Margin", _fmt_pct(operating_margin), _sign_color(operating_margin), "Operating income as a % of revenue. Reflects operational efficiency."),
        ("Net Margin",       _fmt_pct(net_margin),       _sign_color(net_margin),       "Net income as a % of revenue after all expenses."),
        ("ROE",              _fmt_pct(roe),               _sign_color(roe),              "Return on Equity — net income as a % of shareholders' equity."),
        ("ROA",              _fmt_pct(roa),               _sign_color(roa),              "Return on Assets — how efficiently the company uses its assets to generate profit."),
    ])

    fh_health = _highlight_group("Financial Health", [
        ("Current Ratio",  _fmt_ratio(current_ratio, decimals=2),  _ratio_health_color(current_ratio, 1.5),  "Current assets divided by current liabilities. Above 1.5 is generally healthy."),
        ("Quick Ratio",    _fmt_ratio(quick_ratio,   decimals=2),  _ratio_health_color(quick_ratio, 1.0),    "Liquid assets divided by current liabilities. Above 1.0 means short-term debts can be covered without selling inventory."),
        ("Debt / Equity",  _fmt_ratio(debt_to_equity, decimals=2), "",                                        "Total debt relative to shareholders' equity. Lower is generally safer."),
    ])

    fh_growth = _highlight_group("Growth", [
        ("EPS This Year",  _fmt_pct(eps_growth_this_year), _sign_color(eps_growth_this_year), "Earnings per share growth estimate for the current fiscal year."),
        ("EPS Next Year",  _fmt_pct(eps_growth_next_year), _sign_color(eps_growth_next_year), "Earnings per share growth estimate for the next fiscal year."),
        ("EPS 5Y (CAGR)",  _fmt_pct(eps_growth_5y),        _sign_color(eps_growth_5y),        "5-year compound annual EPS growth rate estimate."),
    ])

    fh_income = _highlight_group("Income", [
        ("Dividend Yield", _fmt_pct(dividend_yield), "", "Annual dividend as a % of share price."),
        ("Perf Week",      _fmt_pct(perf_week, decimals=2),  _sign_color(perf_week),  "Price performance over the past week."),
        ("Perf Month",     _fmt_pct(perf_month, decimals=2), _sign_color(perf_month), "Price performance over the past month."),
    ])

    financial_highlights = _section("Financial Highlights", [
        html.Div(className="sw-highlight-grid", children=[
            fh_valuation, fh_profitability, fh_health, fh_growth, fh_income,
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 7 — Technical Indicators
    # ─────────────────────────────────────────────────────────────────────

    ti_trend = _highlight_group("Trend", [
        ("SMA 20 (%)",  _fmt_pct(sma_20_pct,  decimals=2), _sign_color(sma_20_pct),  "Price vs. 20-day simple moving average. Positive = above SMA (bullish short-term)."),
        ("SMA 50 (%)",  _fmt_pct(sma_50_pct,  decimals=2), _sign_color(sma_50_pct),  "Price vs. 50-day simple moving average. Positive = above SMA (bullish medium-term)."),
        ("SMA 200 (%)", _fmt_pct(sma_200_pct, decimals=2), _sign_color(sma_200_pct), "Price vs. 200-day simple moving average. Positive = above SMA (bullish long-term)."),
    ])

    ti_momentum = _highlight_group("Momentum", [
        ("RSI (14)", _fmt_ratio(rsi_14, decimals=1), rsi_color if rsi_lbl else "",
         "Relative Strength Index (14-day). Below 30 = oversold, above 70 = overbought."),
        ("Beta",     _fmt_ratio(beta, decimals=2),   "",
         "Sensitivity to market movements. Above 1.0 = more volatile than the market."),
    ])

    ti_volatility = _highlight_group("Volatility", [
        ("ATR", _fmt_atr(atr), "", "Average True Range — average daily price movement in dollars over the past 14 days."),
        ("Beta", _fmt_ratio(beta, decimals=2), "", "Market volatility coefficient. 2.0 = moves ~2× as much as the market."),
    ])

    ti_trading = _highlight_group("Trading", [
        ("Avg Volume",    _fmt_volume(avg_volume),     "", "Average daily trading volume."),
        ("Rel Volume",    _fmt_relvol(rel_volume),     _relvol_color(rel_volume), "Today's volume relative to the average. Above 1.0x = unusual activity."),
    ])

    ti_52wk = _highlight_group("52 Week Range", [
        ("vs. 52W High", _fmt_pct(week_52_high, decimals=2), _sign_color(week_52_high), "Current price relative to the 52-week high. Negative = below the high."),
        ("vs. 52W Low",  _fmt_pct(week_52_low,  decimals=2), _sign_color(week_52_low),  "Current price relative to the 52-week low. Positive = above the low."),
    ])

    ti_short = _highlight_group("Short Interest", [
        ("Float Short",  _fmt_pct(float_short, decimals=2), "", "Percentage of the float that is currently sold short. High values may indicate bearish sentiment."),
        ("Short Ratio",  _fmt_short_ratio(short_ratio),     "", "Days-to-cover ratio — how many days of average volume it would take to cover all short positions."),
    ])

    technical_indicators = _section("Technical Indicators", [
        html.Div(className="sw-highlight-grid", children=[
            ti_trend, ti_momentum, ti_volatility, ti_trading, ti_52wk, ti_short,
        ]),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 8 — Insider Activity
    # ─────────────────────────────────────────────────────────────────────

    ia_ownership = _highlight_group("Ownership", [
        ("Insider Own",      _fmt_pct(insider_own, decimals=2), "", "Percentage of shares held by company insiders (officers, directors, 10%+ holders)."),
        ("Institutional Own", _fmt_pct(inst_own,   decimals=2), "", "Percentage of shares held by institutional investors (funds, ETFs, pension plans)."),
    ])

    insider_activity = _section("Insider Activity", [
        html.Div(className="sw-highlight-grid", children=[ia_ownership]),
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
        density_section,
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


@callback(
    Output("stock-density-chart", "figure"),
    Input("stock-tf-radio",       "value"),
    State("stock-ticker-store",   "data"),
)
def _update_density_chart(tf, ticker):
    if not ticker:
        raise PreventUpdate
    return _build_density_chart(ticker, tf or "1M")
