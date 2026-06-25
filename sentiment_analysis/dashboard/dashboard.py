"""
Ingestion Monitoring Dashboard
================================
Real-time Streamlit dashboard for pipeline health and raw feed visibility.

Tabs
----
1. Live Feed        — press releases + regulatory filings side-by-side
2. Ingestion Health — time-series chart + per-source health table
3. Ticker Coverage  — volume by ticker, summary stats, source pie
4. Raw Data Explorer — filterable paginated table with CSV export

Connects to PostgreSQL via a synchronous SQLAlchemy engine (psycopg2) so
that it plays nicely with Streamlit's synchronous execution model.

Run with:
    streamlit run sentiment_analysis/dashboard/dashboard.py
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Optional

import pytz

_ET = pytz.timezone("America/New_York")

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from streamlit_autorefresh import st_autorefresh

# Active ingestion sources — used to filter ingestion_log in every query so
# that stale rows from removed ingestors are never surfaced in the dashboard.
_ACTIVE_SOURCES_SQL = (
    "'rss:pr_newswire','rss:globe_newswire_finance','rss:globe_newswire_ma',"
    "'rss:sec_edgar','rss:sec_form4','rss:fda'"
)

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sentiment — Ingestion Monitor",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Auto-refresh (every 30 seconds; count drives a cache-bust key)
# ---------------------------------------------------------------------------

_refresh_count = st_autorefresh(interval=30_000, key="dashboard_autorefresh")

# ---------------------------------------------------------------------------
# Colour palette for tickers (consistent hash → colour assignment)
# ---------------------------------------------------------------------------

_PALETTE = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
    "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
    "#F0A500", "#A8E6CF", "#FFD3B6", "#DCEDC1", "#B5EAD7",
]


def _ticker_color(ticker: str) -> str:
    """Deterministic colour for a ticker symbol."""
    return _PALETTE[hash(ticker) % len(_PALETTE)]


# ---------------------------------------------------------------------------
# DB engine — created once per Streamlit server process
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_engine():
    """
    Build the synchronous SQLAlchemy engine, cached for the lifetime of the
    Streamlit server process (survives reruns and browser refreshes).

    NullPool: every connect() call opens a fresh TCP connection and closes it
    on exit — no pool state to go stale across reruns.

    Two hard timeouts prevent the Streamlit page from dimming indefinitely
    when PostgreSQL is slow or unreachable:
      connect_timeout      — psycopg2 gives up on the TCP handshake after 5 s
      statement_timeout    — PostgreSQL cancels any query that runs > 5 s and
                             raises an exception, which _query() catches and
                             displays as a warning instead of a frozen page.
    """
    from sentiment_analysis.config import settings
    return create_engine(
        settings.sync_database_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
        },
    )


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _query(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    """
    Execute a read-only SQL query and return the result as a DataFrame.

    Uses engine.begin() so each call gets a fresh connection with an explicit
    transaction that commits (or rolls back on error) on exit — no leftover
    transaction state is ever returned to the engine.

    Returns an empty DataFrame on any error so tabs degrade gracefully.
    """
    try:
        with _get_engine().begin() as conn:
            return pd.read_sql(text(sql), conn, params=params or {})
    except Exception as exc:
        st.warning(f"Database error: {exc}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Pipeline status (used in header)
# ---------------------------------------------------------------------------

def _pipeline_status() -> str:
    """
    Derive pipeline status from the most recent run per source in the last
    30 minutes.

    Uses DISTINCT ON so that a transient error on an earlier run doesn't
    permanently latch the status to "Degraded" once later runs succeed.

    Returns
    -------
    "Running"  — latest run for every active source was clean
    "Degraded" — more than one source's latest run recorded an error
    "Error"    — no ingestion logs at all in the last 30 minutes
    """
    df = _query(f"""
        SELECT DISTINCT ON (source)
               source,
               run_at        AS last_run,
               error_message AS last_error
        FROM   ingestion_log
        WHERE  run_at > NOW() - INTERVAL '30 minutes'
          AND  source IN ({_ACTIVE_SOURCES_SQL})
        ORDER  BY source, run_at DESC
    """)
    if df.empty:
        return "Error"
    return "Degraded" if df["last_error"].notna().sum() > 1 else "Running"


# ---------------------------------------------------------------------------
# HEADER BAR
# ---------------------------------------------------------------------------

def render_header() -> None:
    now = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S ET")
    status = _pipeline_status()
    color = {"Running": "#28a745", "Degraded": "#fd7e14", "Error": "#dc3545"}.get(
        status, "#6c757d"
    )

    col_title, col_time, col_status = st.columns([4, 3, 1])
    with col_title:
        st.markdown("## 📡 Stock Sentiment — Ingestion Monitor")
    with col_time:
        st.markdown(f"**{now}**")
    with col_status:
        st.markdown(
            f"<div style='text-align:right; font-size:1.1rem; font-weight:600; "
            f"color:{color}'>● {status}</div>",
            unsafe_allow_html=True,
        )
    st.divider()


# ---------------------------------------------------------------------------
# TAB 1 — LIVE FEED
# ---------------------------------------------------------------------------

def render_live_feed() -> None:
    col_press, col_reg = st.columns(2)

    # ---- Press Releases --------------------------------------------------
    with col_press:
        st.subheader("Press Releases  (last 50)")
        press_df = _query("""
            SELECT
                to_char(ingested_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS ingested,
                source_name                                                    AS source,
                array_to_string(tickers, ', ')                                 AS tickers,
                title                                                          AS headline,
                COALESCE(sentiment_label, '')                                  AS sentiment,
                sentiment_score                                                AS score
            FROM   rss_articles
            WHERE  source_name IN ('pr_newswire', 'globe_newswire_finance', 'globe_newswire_ma')
            ORDER  BY ingested_at DESC NULLS LAST
            LIMIT  50
        """)
        if press_df.empty:
            st.info("No press release articles ingested yet.")
        else:
            st.dataframe(press_df, use_container_width=True, height=480, hide_index=True)

    # ---- Regulatory Filings ----------------------------------------------
    with col_reg:
        st.subheader("Regulatory Filings  (last 50)")
        reg_df = _query("""
            SELECT
                to_char(ingested_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS ingested,
                source_name                                                    AS source,
                array_to_string(tickers, ', ')                                 AS tickers,
                title                                                          AS headline,
                COALESCE(sentiment_label, '')                                  AS sentiment,
                sentiment_score                                                AS score
            FROM   rss_articles
            WHERE  source_name IN ('sec_edgar', 'fda')
            ORDER  BY ingested_at DESC NULLS LAST
            LIMIT  50
        """)
        if reg_df.empty:
            st.info("No regulatory filings ingested yet.")
        else:
            st.dataframe(reg_df, use_container_width=True, height=480, hide_index=True)


# ---------------------------------------------------------------------------
# TAB 2 — INGESTION HEALTH
# ---------------------------------------------------------------------------

def render_ingestion_health() -> None:
    # ---- Time-series chart -----------------------------------------------
    st.subheader("Records Ingested — 15-min buckets (last 24 h)")
    ts_df = _query(f"""
        SELECT
            date_trunc('hour', run_at)
            + (EXTRACT(MINUTE FROM run_at)::int / 15) * INTERVAL '15 minutes'
                                        AS bucket,
            source,
            SUM(records_stored)         AS stored
        FROM   ingestion_log
        WHERE  run_at > NOW() - INTERVAL '24 hours'
          AND  source IN ({_ACTIVE_SOURCES_SQL})
        GROUP  BY bucket, source
        ORDER  BY bucket
    """)
    if ts_df.empty:
        st.info("No ingestion data in the last 24 hours.")
    else:
        fig = px.line(
            ts_df,
            x="bucket",
            y="stored",
            color="source",
            markers=True,
            title="Records stored per 15-minute window",
            labels={"bucket": "Time (ET)", "stored": "Records", "source": "Source"},
        )
        fig.update_layout(legend_title_text="Source", height=380)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ---- Per-source health table -----------------------------------------
    st.subheader("Source Health Summary (last 24 h)")
    health_df = _query(f"""
        SELECT
            source,
            MAX(run_at) AT TIME ZONE 'America/New_York'  AS last_run_et,
            SUM(records_fetched)             AS total_fetched,
            SUM(records_stored)              AS total_stored,
            MAX(error_message)               AS last_error
        FROM   ingestion_log
        WHERE  run_at > NOW() - INTERVAL '24 hours'
          AND  source IN ({_ACTIVE_SOURCES_SQL})
        GROUP  BY source
        ORDER  BY source
    """)

    if health_df.empty:
        st.info("No ingestion logs yet.")
        return

    # Identify stale sources (0 records stored in the last 30 minutes)
    stale_df = _query(f"""
        SELECT source
        FROM   ingestion_log
        WHERE  run_at > NOW() - INTERVAL '30 minutes'
          AND  source IN ({_ACTIVE_SOURCES_SQL})
        GROUP  BY source
        HAVING SUM(records_stored) = 0
    """)
    stale_sources: set = (
        set(stale_df["source"].tolist()) if not stale_df.empty else set()
    )

    # Streamlit doesn't support row-level styling via .style easily, so we
    # add an explicit "alert" column instead.
    health_df["alert"] = health_df["source"].apply(
        lambda s: "⚠ stale" if s in stale_sources else ""
    )

    st.dataframe(health_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# TAB 3 — TICKER COVERAGE
# ---------------------------------------------------------------------------

def render_ticker_coverage() -> None:
    # ---- Summary metrics -------------------------------------------------
    stats_df = _query("""
        SELECT
            (SELECT COUNT(*)
             FROM   rss_articles
             WHERE  ingested_at > NOW() - INTERVAL '24 hours')            AS articles_today,
            (SELECT COUNT(DISTINCT source_name)
             FROM   rss_articles
             WHERE  ingested_at > NOW() - INTERVAL '24 hours')            AS active_sources,
            (SELECT COUNT(DISTINCT t)
             FROM (
                 SELECT unnest(tickers) AS t
                 FROM   rss_articles
                 WHERE  ingested_at > NOW() - INTERVAL '24 hours'
             ) sub
            )                                                              AS unique_tickers
    """)
    if not stats_df.empty:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Articles Today", int(stats_df["articles_today"].iloc[0] or 0))
        c2.metric("Active Sources Today", int(stats_df["active_sources"].iloc[0] or 0))
        c3.metric("Unique Tickers Today", int(stats_df["unique_tickers"].iloc[0] or 0))

    st.divider()

    # ---- Ticker volume bar chart -----------------------------------------
    st.subheader("Mentions by Ticker — last 24 h  (top 30)")
    vol_df = _query("""
        SELECT
            unnest(tickers) AS ticker,
            source_name     AS source,
            COUNT(*)        AS volume
        FROM   rss_articles
        WHERE  ingested_at > NOW() - INTERVAL '24 hours'
        GROUP  BY ticker, source
        ORDER  BY volume DESC
        LIMIT  60
    """)

    if vol_df.empty:
        st.info("No ticker data yet.")
    else:
        top_tickers = (
            vol_df.groupby("ticker")["volume"]
            .sum()
            .nlargest(30)
            .index.tolist()
        )
        chart_df = vol_df[vol_df["ticker"].isin(top_tickers)]
        fig = px.bar(
            chart_df,
            x="ticker",
            y="volume",
            color="source",
            barmode="stack",
            title="Ticker Mention Volume",
            labels={"ticker": "Ticker", "volume": "Mentions", "source": "Source"},
        )
        fig.update_layout(xaxis_tickangle=-45, height=420)
        st.plotly_chart(fig, use_container_width=True)

    # ---- RSS source pie --------------------------------------------------
    st.subheader("RSS Volume Share — last 24 h")
    rss_vol_df = _query("""
        SELECT source_name, COUNT(*) AS articles
        FROM   rss_articles
        WHERE  ingested_at > NOW() - INTERVAL '24 hours'
        GROUP  BY source_name
        ORDER  BY articles DESC
    """)

    if rss_vol_df.empty:
        st.info("No RSS articles yet.")
    else:
        fig2 = px.pie(
            rss_vol_df,
            names="source_name",
            values="articles",
            title="Share of RSS Article Volume by Source",
            hole=0.35,
        )
        fig2.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig2, use_container_width=True)

    # ---- Sentiment summary (Phase 4) ------------------------------------
    st.divider()
    st.subheader("24-hr Sentiment Summary  (tickers with ≥ 2 articles)")
    sent_df = _query("""
        SELECT DISTINCT ON (ticker)
            ticker,
            ROUND(avg_sentiment::numeric, 3) AS avg_sentiment,
            article_count,
            bullish_count,
            bearish_count,
            neutral_count,
            momentum
        FROM   ticker_sentiment_summary
        WHERE  "window" = '24hr'
          AND  calculated_at > NOW() - INTERVAL '2 hours'
        ORDER  BY ticker, calculated_at DESC
    """)

    if sent_df.empty:
        st.info("Sentiment data not yet available. The pipeline runs every 15 minutes.")
    else:
        def _signal(score) -> str:
            if score is None or (hasattr(score, '__float__') and score != score):
                return "⬜"
            s = float(score)
            if s >= 0.35:   return "🟢"
            elif s >= 0.15: return "🔼"
            elif s > -0.15: return "⚪"
            elif s > -0.35: return "🔽"
            else:           return "🔴"

        sent_df.insert(0, "signal", sent_df["avg_sentiment"].apply(_signal))
        st.dataframe(sent_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# TAB 4 — RAW DATA EXPLORER
# ---------------------------------------------------------------------------

def render_raw_explorer() -> None:
    source_options = [
        "rss:pr_newswire",
        "rss:globe_newswire_finance",
        "rss:globe_newswire_ma",
        "rss:sec_edgar",
        "rss:sec_form4",
        "rss:fda",
    ]

    col_src, col_start, col_end = st.columns([2, 1, 1])
    with col_src:
        selected = st.selectbox("Source", source_options, key="explorer_source")
    with col_start:
        start = st.date_input(
            "From",
            value=datetime.now(_ET).date() - timedelta(days=1),
            key="explorer_start",
        )
    with col_end:
        end = st.date_input(
            "To",
            value=datetime.now(_ET).date(),
            key="explorer_end",
        )

    page_size = 50
    page = st.number_input("Page", min_value=1, value=1, step=1, key="explorer_page")
    offset = (int(page) - 1) * page_size

    start_ts = start.isoformat()
    end_ts = (end + timedelta(days=1)).isoformat()

    feed_name = selected.replace("rss:", "")
    df = _query(
        """
        SELECT id::text,
               to_char(ingested_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') AS ingested,
               array_to_string(tickers, ', ') AS tickers,
               title,
               summary,
               url,
               to_char(published_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD') AS published,
               raw_json::text AS raw
        FROM   rss_articles
        WHERE  source_name = :src
          AND  ingested_at BETWEEN :start AND :end
        ORDER  BY ingested_at DESC NULLS LAST
        LIMIT  :lim OFFSET :off
        """,
        {
            "src": feed_name,
            "start": start_ts,
            "end": end_ts,
            "lim": page_size,
            "off": offset,
        },
    )

    if df.empty:
        st.info("No records found for the selected filters.")
        return

    # Display table (hide the raw JSON column — it's in the expander below)
    display_cols = [c for c in df.columns if c != "raw"]
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
    )

    # CSV export
    csv_bytes = df[display_cols].to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Export to CSV",
        data=csv_bytes,
        file_name=f"{selected}_{start}_{end}.csv",
        mime="text/csv",
    )

    # Raw JSON viewer for the first record
    if "raw" in df.columns and len(df) > 0:
        with st.expander("Raw JSON — first record"):
            st.code(df["raw"].iloc[0], language="json")


# ---------------------------------------------------------------------------
# TAB 5 — ENTITY QUEUE
# ---------------------------------------------------------------------------

def render_entity_queue() -> None:
    # ---- Summary stats --------------------------------------------------
    stats_df = _query("""
        SELECT
            status,
            COUNT(*)        AS entities,
            SUM(frequency)  AS total_mentions
        FROM   unresolved_entities
        GROUP  BY status
        ORDER  BY status
    """)

    if not stats_df.empty:
        pending_row = stats_df[stats_df["status"] == "pending"]
        resolved_row = stats_df[stats_df["status"] == "resolved"]
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Pending Review",
            int(pending_row["entities"].iloc[0]) if not pending_row.empty else 0,
        )
        c2.metric(
            "Total Mentions",
            int(pending_row["total_mentions"].iloc[0]) if not pending_row.empty else 0,
        )
        c3.metric(
            "Resolved",
            int(resolved_row["entities"].iloc[0]) if not resolved_row.empty else 0,
        )

    st.divider()

    # ---- Pending entities table -----------------------------------------
    st.subheader("Pending Unresolved Entities  (top 100 by frequency)")
    pending_df = _query("""
        SELECT
            entity_text                                                    AS entity,
            entity_type                                                    AS type,
            frequency,
            to_char(created_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI')  AS first_seen
        FROM   unresolved_entities
        WHERE  status = 'pending'
        ORDER  BY frequency DESC, created_at DESC
        LIMIT  100
    """)

    if pending_df.empty:
        st.info(
            "No pending entities. Either the NLP pipeline has not run yet, "
            "or all entities have been resolved."
        )
    else:
        st.dataframe(pending_df, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Entity type breakdown ------------------------------------------
    st.subheader("Entity Type Breakdown")
    type_df = _query("""
        SELECT
            entity_type                 AS type,
            COUNT(*)                    AS entities,
            SUM(frequency)              AS mentions
        FROM   unresolved_entities
        WHERE  status = 'pending'
        GROUP  BY entity_type
        ORDER  BY mentions DESC
    """)
    if not type_df.empty:
        st.dataframe(type_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# TAB 6 — SENTIMENT ALERTS
# ---------------------------------------------------------------------------

def render_alerts() -> None:
    # ---- Summary metrics -------------------------------------------------
    spike_df = _query("""
        SELECT COUNT(*) AS spike_count
        FROM   sentiment_spikes
        WHERE  detected_at > NOW() - INTERVAL '24 hours'
    """)
    bullish_df = _query("""
        SELECT unnest(tickers) AS ticker, AVG(sentiment_score) AS avg_score
        FROM   rss_articles
        WHERE  sentiment_analyzed_at > NOW() - INTERVAL '24 hours'
          AND  sentiment_score > 0
          AND  cardinality(tickers) > 0
        GROUP  BY ticker
        HAVING COUNT(*) >= 3
        ORDER  BY avg_score DESC
        LIMIT  1
    """)
    bearish_df = _query("""
        SELECT unnest(tickers) AS ticker, AVG(sentiment_score) AS avg_score
        FROM   rss_articles
        WHERE  sentiment_analyzed_at > NOW() - INTERVAL '24 hours'
          AND  sentiment_score < 0
          AND  cardinality(tickers) > 0
        GROUP  BY ticker
        HAVING COUNT(*) >= 3
        ORDER  BY avg_score ASC
        LIMIT  1
    """)

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Volume Spikes (24 h)",
        int(spike_df["spike_count"].iloc[0]) if not spike_df.empty else 0,
    )
    c2.metric(
        "Most Bullish Ticker",
        bullish_df["ticker"].iloc[0] if not bullish_df.empty else "—",
    )
    c3.metric(
        "Most Bearish Ticker",
        bearish_df["ticker"].iloc[0] if not bearish_df.empty else "—",
    )

    st.divider()

    # ---- Volume spike table ---------------------------------------------
    st.subheader("Article-Volume Spikes — last 24 h")
    spikes_tbl = _query("""
        SELECT
            ticker,
            to_char(detected_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS detected,
            article_count,
            ROUND(rolling_avg::numeric, 1)   AS rolling_avg,
            ROUND(spike_ratio::numeric, 1)   AS spike_ratio
        FROM   sentiment_spikes
        WHERE  detected_at > NOW() - INTERVAL '24 hours'
        ORDER  BY detected_at DESC, spike_ratio DESC
        LIMIT  50
    """)
    if spikes_tbl.empty:
        st.info("No volume spikes detected in the last 24 hours.")
    else:
        st.dataframe(spikes_tbl, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Extreme sentiment articles -------------------------------------
    st.subheader("Extreme Sentiment Articles — last 24 h")
    col_bull, col_bear = st.columns(2)

    with col_bull:
        st.markdown("**Most Bullish**")
        bull_art = _query("""
            SELECT
                to_char(ingested_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS ingested,
                array_to_string(tickers, ', ')                                 AS tickers,
                title                                                          AS headline,
                ROUND(sentiment_score::numeric, 2)                            AS score
            FROM   rss_articles
            WHERE  sentiment_analyzed_at > NOW() - INTERVAL '24 hours'
              AND  sentiment_score >= 0.35
            ORDER  BY sentiment_score DESC
            LIMIT  20
        """)
        if bull_art.empty:
            st.info("No bullish articles yet.")
        else:
            st.dataframe(bull_art, use_container_width=True, height=350, hide_index=True)

    with col_bear:
        st.markdown("**Most Bearish**")
        bear_art = _query("""
            SELECT
                to_char(ingested_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS ingested,
                array_to_string(tickers, ', ')                                 AS tickers,
                title                                                          AS headline,
                ROUND(sentiment_score::numeric, 2)                            AS score
            FROM   rss_articles
            WHERE  sentiment_analyzed_at > NOW() - INTERVAL '24 hours'
              AND  sentiment_score <= -0.35
            ORDER  BY sentiment_score ASC
            LIMIT  20
        """)
        if bear_art.empty:
            st.info("No bearish articles yet.")
        else:
            st.dataframe(bear_art, use_container_width=True, height=350, hide_index=True)


# ---------------------------------------------------------------------------
# Layout assembly
# ---------------------------------------------------------------------------

render_header()

tab_feed, tab_health, tab_tickers, tab_explorer, tab_queue, tab_alerts = st.tabs(
    [
        "📰 Live Feed",
        "🩺 Ingestion Health",
        "📊 Ticker Coverage",
        "🔍 Raw Data Explorer",
        "🧩 Entity Queue",
        "🚨 Sentiment Alerts",
    ]
)

with tab_feed:
    try:
        render_live_feed()
    except Exception as _exc:
        st.exception(_exc)

with tab_health:
    try:
        render_ingestion_health()
    except Exception as _exc:
        st.exception(_exc)

with tab_tickers:
    try:
        render_ticker_coverage()
    except Exception as _exc:
        st.exception(_exc)

with tab_explorer:
    try:
        render_raw_explorer()
    except Exception as _exc:
        st.exception(_exc)

with tab_queue:
    try:
        render_entity_queue()
    except Exception as _exc:
        st.exception(_exc)

with tab_alerts:
    try:
        render_alerts()
    except Exception as _exc:
        st.exception(_exc)
