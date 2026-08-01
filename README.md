# Stock Sentiment Analysis Dashboard

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Deployed on Railway](https://img.shields.io/badge/Deployed%20on-Railway-7B2CF7?logo=railway&logoColor=white)

A real-time financial news intelligence platform that continuously ingests articles from 28 RSS feeds, extracts stock tickers using a multi-pass NLP pipeline, scores sentiment with FinBERT (local) and Gemini (fallback), and presents everything through a multi-page Plotly Dash dashboard — all deployed as a single service on Railway.

---

## Overview

Stock Sentiment Analysis Dashboard is an end-to-end pipeline that transforms raw financial news into structured, ticker-tagged sentiment signals for equity traders and researchers. Every five minutes the system polls financial news outlets, press release wires, SEC EDGAR filings, regulatory agencies, and technology publications. Each article is run through a three-pass ticker extraction system and a five-stage entity resolution engine (cashtag scan → spaCy NER → EntityClassifier → CompanyResolver → TickerMapper fuzzy match), then scored for sentiment by a FinBERT model fine-tuned on financial macro news, with Gemini 2.5 Flash as an automatic fallback.

The project was built as an internship research project at Penn State University under Dr. Kaamran Raahemifar. The goal was to produce a production-grade, self-hosted alternative to expensive Bloomberg terminal sentiment feeds — one that any researcher or retail investor can run on a $5/month Railway instance connected to a PostgreSQL database. SEC filings receive specialised treatment: Form 4 insider trades, 8-K events, and 10-Q reports are enriched by fetching the actual EDGAR documents and summarising them before scoring.

The dashboard provides five live pages: a filterable news feed, a Finviz-style stock screener backed by Finviz Elite price data, a TradingView chart embed, a personal watchlist, and a full alert system that fires on price crossings, sentiment shifts, breaking news, or article-volume spikes — all with edge-detection to prevent duplicate firings.

---

## Live Demo

The application is deployed on [Railway](https://railway.app). The service starts with `python -m sentiment_analysis.main`, which launches the ingestion pipeline and the Dash dashboard as a child process on port 8050.

---

## Screenshots

![Dashboard Screenshot](docs/screenshot.png)

---

## Features

### Ingestion Pipeline
- **28 RSS/Atom feeds** polled every 5 minutes (configurable): financial news, press release wires, SEC EDGAR (8-K, Form 4, 10-Q, S-1, SC 13G), FDA, Federal Reserve, FTC, technology outlets, and healthcare/biotech
- **SEC EDGAR enrichment**: fetches Form 4 XML and 8-K documents directly from EDGAR to generate human-readable summaries before scoring
- **English-only filtering**: two-stage check (non-ASCII character ratio + langdetect confidence ≥ 0.80) rejects non-English articles before they enter the pipeline
- **Two-layer deduplication**: in-memory URL set (fast path) + database UNIQUE constraint (persistent)
- **Ingestion audit log**: one `ingestion_log` row per source per poll cycle for dashboard health monitoring

### NLP Pipeline
- **Three-pass ticker extraction**: cashtag scan (`$AAPL`) → exact S&P 500 token match → company name fuzzy match via TickerMapper
- **Five-stage entity resolution engine**: cashtag/exchange-prefix scan → spaCy NER (`en_core_web_sm`) → 18-type EntityClassifier (filters out government agencies, diseases, drugs, currencies, etc.) → CompanyResolver → TickerMapper (500+ company entries, rapidfuzz fuzzy match)
- **Primary company scorer**: scores candidate tickers by title placement, cashtag presence, and mention frequency to identify the single company an article is primarily about
- **NLTK text preprocessing**: tokenisation, lemmatisation, stop-word removal, financial domain vocabulary handling

### Sentiment Analysis
- **FinBERT** (`peyterho/finbert-macro-sentiment`): primary analyser for all non-SEC articles — batched inference, CUDA if available, CPU otherwise, lazy-loaded singleton
- **Google Gemini 2.5 Flash**: automatic fallback when FinBERT raises an exception, structured JSON output via `response_schema=list[ArticleSentiment]`, batches up to 50 articles per request, 3 retries
- **SEC Analyzer**: rule-based keyword scorer for SEC filings — going-concern detection (highest priority), form-specific rules for 8-K, Form 4, 10-Q, S-1, and SC 13G
- **Loughran-McDonald (LM) Analyzer**: financial-domain dictionary scorer available for SEC text
- **5-tier sentiment labels**: Bullish / Somewhat Bullish / Neutral / Somewhat Bearish / Bearish, with numeric scores in [−1.0, +1.0]

### Aggregation & Alerts
- **Per-ticker rolling summaries**: 1hr / 4hr / 24hr windows with average sentiment, article counts, momentum (improving / declining / stable, ±0.05 dead-band)
- **Article-volume spike detection**: 15-minute bucketed counts, fires when current bucket exceeds 2× rolling average with ≥ 3 historical buckets
- **Four alert types**: `price` (crossover), `sentiment` (score threshold), `breaking_news` (new article), `volume_spike` (>2× rolling average)
- **Edge-detection model**: alerts fire only on False→True transitions, preventing repeated firings while a condition holds
- **Alert history**: immutable firing log with trigger value and message, cascades on alert deletion

### Dashboard (Plotly Dash, port 8050)
- **5-page multi-page Dash app** with dark-mode Bootstrap theme and sidebar navigation
- **Pipeline status indicator** in topbar: Running / Degraded / Error based on recent ingestion logs, refreshes every 30 seconds
- **News Feed** (`/`): paginated live article stream (50 per page), filterable by source and ticker, colour-coded sentiment labels
- **Screener** (`/screener`): Finviz-style dense table with 40+ fundamental, technical, and performance columns; sector/country/exchange filters; paginated (25 per page); add/remove watchlist from row
- **Charts** (`/charts`): full-screen TradingView Advanced Chart embed with symbol search
- **Watchlist** (`/watchlist`): live price and 24hr sentiment summary for personally tracked tickers; add/remove tickers inline
- **Alerts** (`/alerts`): create/edit/toggle/delete alert rules; live history of every firing; summary cards (total / active / triggered today)
- **Stock Workspace** (`/stock/<TICKER>`): per-ticker research page with price header, pre/post-market prices, key metrics, financial highlights, technical indicators, short interest, ownership, analyst data, price performance, and a 24hr sentiment summary

### Price Data
- **Finviz Elite bulk screener ingestor**: six screener export views merged on ticker, covering 40+ columns including fundamentals (P/E, PEG, P/S, P/B, EPS, ROE, ROA, margins, debt ratios), technicals (RSI, SMA distances, ATR, relative volume), short interest, analyst recommendations, and EPS/sales growth
- **Live price updates** every 1 minute via APScheduler
- **Daily snapshot history** (`ticker_snapshot_history`): one row per ticker per calendar day (ET), ON CONFLICT DO NOTHING so subsequent runs that day are no-ops

### Infrastructure
- **APScheduler** with 6 scheduled jobs: RSS (every 5 min), NLP (every 10 min), Sentiment (every 5 min), Prices (every 1 min), Alert evaluation (every 1 min), Cleanup (daily midnight ET)
- **2-day rolling data retention**: cleanup job deletes rows older than 2 days from `rss_articles`, `extracted_entities`, `ticker_sentiment_summary`, `sentiment_spikes`, and `ingestion_log`
- **Async pipeline** (asyncpg + SQLAlchemy 2.x async) for ingestion; synchronous engine (psycopg2 + NullPool) for Dash callbacks
- **Alembic migrations**: 21 tracked migrations from initial schema through the full alert system
- **Rotating log files**: `logs/ingestion.log`, 10 MB rotation, 7-day retention, gzip compression

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| **Web framework** | Plotly Dash | ≥ 2.18 |
| **UI components** | dash-bootstrap-components | ≥ 1.6 |
| **Charts** | Plotly | ≥ 5.22 |
| **Data manipulation** | pandas | ≥ 2.2 |
| **Async ORM** | SQLAlchemy asyncio | ≥ 2.0 |
| **Async DB driver** | asyncpg | ≥ 0.29 |
| **Sync DB driver** | psycopg2-binary | ≥ 2.9 |
| **Schema migrations** | Alembic | ≥ 1.13 |
| **Task scheduler** | APScheduler | ≥ 3.10 |
| **RSS parsing** | feedparser | ≥ 6.0 |
| **Language detection** | langdetect | ≥ 1.0.9 |
| **NLP / NER** | spaCy (`en_core_web_sm`) | ≥ 3.7 |
| **Text preprocessing** | NLTK | ≥ 3.8 |
| **Fuzzy matching** | rapidfuzz | ≥ 3.0 |
| **Sentiment — FinBERT** | transformers + torch | ≥ 4.40 / ≥ 2.2 |
| **Sentiment — Gemini** | google-genai | ≥ 1.0 |
| **Sentiment — LM dict** | pysentiment2 | ≥ 0.1.1 |
| **HTTP** | requests | ≥ 2.28 |
| **Logging** | loguru | ≥ 0.7 |
| **Environment** | python-dotenv | ≥ 1.0 |
| **Timezone** | pytz | ≥ 2024.1 |
| **WSGI server** | gunicorn | ≥ 21.0 |
| **Runtime** | Python | 3.11+ |

---

## Data Sources

### Financial News
| Feed | Source |
|---|---|
| CNBC Finance | `cnbc.com` RSS |
| CNBC Earnings | `cnbc.com` RSS |
| Yahoo Finance | `finance.yahoo.com` RSS |
| MarketWatch | `marketwatch.com` RSS |
| WSJ — Markets | `feeds.a.dj.com` RSS |
| WSJ — Business | `feeds.a.dj.com` RSS |
| Financial Times — Markets | `ft.com` RSS |
| Bloomberg Markets | `bloomberg.com` RSS |
| Motley Fool | `fool.com` RSS |

### Press Releases
| Feed | Source |
|---|---|
| PR Newswire | `prnewswire.com` RSS |
| GlobeNewswire — Financial Services | `globenewswire.com` RSS |
| GlobeNewswire — M&A | `globenewswire.com` RSS |
| GlobeNewswire — Earnings | `globenewswire.com` RSS |
| GlobeNewswire — Company Announcements | `globenewswire.com` RSS |

### Regulatory / SEC EDGAR
| Feed | Form |
|---|---|
| SEC EDGAR | 8-K (current events) |
| SEC EDGAR | Form 4 (insider transactions) |
| SEC EDGAR | 10-Q (quarterly reports) |
| SEC EDGAR | S-1 (IPO registrations) |
| SEC EDGAR | SC 13G (large investor positions) |
| FDA | Press releases |
| Federal Reserve | All press releases |
| Federal Reserve | Monetary policy statements |
| FTC | Press releases |

### Technology & Healthcare
| Feed | Source |
|---|---|
| TechCrunch | `techcrunch.com` RSS |
| Ars Technica | `arstechnica.com` RSS |
| VentureBeat | `venturebeat.com` RSS |
| ZDNet | `zdnet.com` RSS |
| FierceBiotech | `fiercebiotech.com` RSS |
| STAT News | `statnews.com` RSS |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         APScheduler (async)                         │
│  RSS every 5min · NLP every 10min · Sentiment every 5min           │
│  Prices every 1min · Alerts every 1min · Cleanup daily midnight ET  │
└────────────────┬────────────────────────────────────────────────────┘
                 │
    ┌────────────▼────────────┐
    │    RSS Ingestor          │  feedparser · langdetect · SEC enrichment
    │  28 feeds → rss_articles │  URL dedup · ticker extraction (3-pass)
    └────────────┬─────────────┘
                 │ cleaned_text IS NULL
    ┌────────────▼────────────┐
    │    NLP Pipeline          │  NLTK preprocessing · spaCy NER
    │    (every 10 min)        │  EntityClassifier · CompanyResolver
    │                          │  TickerMapper (rapidfuzz) · primary_company_scorer
    └────────────┬─────────────┘
                 │ sentiment_analyzed_at IS NULL
    ┌────────────▼────────────┐
    │  Sentiment Pipeline      │  SEC articles → SECAnalyzer (rule-based)
    │    (every 5 min)         │  Others → FinBERT (primary) → Gemini (fallback)
    │                          │  → ticker_sentiment_summary · sentiment_spikes
    └────────────┬─────────────┘
                 │
    ┌────────────▼────────────┐
    │   Alert Evaluator        │  price · sentiment · breaking_news · volume_spike
    │    (every 1 min)         │  Edge-detection (condition_met) → alert_history
    └────────────┬─────────────┘
                 │
    ┌────────────▼────────────┐
    │  Finviz Elite Ingestor   │  6 screener export views merged on ticker
    │    (every 1 min)         │  40+ columns → ticker_prices
    │                          │  Daily snapshot → ticker_snapshot_history
    └────────────┬─────────────┘
                 │
    ┌────────────▼────────────┐
    │   Plotly Dash Dashboard  │  port 8050 · 5 pages + Stock Workspace
    │   (subprocess of main)   │  NullPool psycopg2 · Bootstrap dark theme
    └─────────────────────────┘
```

---

## Prerequisites

Before installing, make sure you have:

- **Python 3.11+** — the pipeline uses `asyncpg` async/await patterns that require 3.11+
- **PostgreSQL 14+** — all data is stored in PostgreSQL; TimescaleDB is supported but not required
- **Git**
- **Finviz Elite subscription** — required for real-time price data (`FINVIZ_TOKEN`). Without it, the price ingestor logs a warning and skips; the rest of the pipeline still runs
- **Google Gemini API key** — used only as a fallback when FinBERT fails. Optional but recommended

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/tjlakmana/stock-sentiment.git
cd stock-sentiment
```

### 2. Create a virtual environment

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note for CPU-only Railway deployments:** To halve the PyTorch image size (~200 MB vs ~700 MB), add the following line to the top of `requirements.txt` before the torch entry:
> ```
> --extra-index-url https://download.pytorch.org/whl/cpu
> ```

### 4. Download the spaCy model

```bash
python -m spacy download en_core_web_sm
```

### 5. Set up PostgreSQL

Create a database for the project:

```sql
CREATE DATABASE sentiment_db;
```

### 6. Configure environment variables

```bash
cp sentiment_analysis/.env.example sentiment_analysis/.env
```

Edit `sentiment_analysis/.env` and fill in your values (see [Environment Variables](#environment-variables) below).

### 7. Run Alembic migrations

Apply all 21 schema migrations:

```bash
alembic -c sentiment_analysis/storage/alembic/alembic.ini upgrade head
```

### 8. Run the application

```bash
python -m sentiment_analysis.main
```

The ingestion pipeline starts immediately. The Dash dashboard launches as a subprocess and is available at `http://localhost:8050`.

To run the pipeline without the dashboard:

```bash
python -m sentiment_analysis.main --no-ui
```

---

## Environment Variables

Copy `sentiment_analysis/.env.example` to `sentiment_analysis/.env`. The application reads from `DATABASE_URL`, `POSTGRES_URL`, or `POSTGRESQL_URL` — whichever is set first. On Railway, `DATABASE_URL` is injected automatically.

| Variable | Description | Required | Default |
|---|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string. Format: `postgresql+asyncpg://user:pass@host:5432/db`. Railway and Heroku `postgres://` URIs are normalised automatically. | **Yes** | — |
| `POSTGRES_URL` | Alternative name for the database URL (checked if `DATABASE_URL` is absent) | No | — |
| `POSTGRESQL_URL` | Alternative name for the database URL (checked last) | No | — |
| `GEMINI_API_KEY` | Google Gemini API key for the LLM sentiment fallback. Obtain from [aistudio.google.com](https://aistudio.google.com/app/apikey). If absent, Gemini fallback is skipped and articles that FinBERT cannot score are marked analyzed without a sentiment. | No | `""` |
| `FINVIZ_TOKEN` | Finviz Elite auth token for the bulk screener export. Required for price data. If absent, the price ingestor logs a warning and skips every cycle. | No | `""` |
| `LOG_LEVEL` | Loguru log level for console output | No | `INFO` |
| `RSS_POLL_INTERVAL_MINUTES` | Minutes between RSS feed ingestion cycles | No | `5` |
| `FINBERT_POSITIVE_THRESHOLD` | Score ≥ threshold → `"Bullish"`. Score = `positive_prob − negative_prob` in [−1, +1]. | No | `0.35` |
| `FINBERT_NEGATIVE_THRESHOLD` | Score ≤ −threshold → `"Bearish"` | No | `0.35` |
| `TICKER_WATCHLIST` | Comma-separated list of tickers the pipeline monitors. | No | Full S&P 500 + ETF universe |

---

## Running Locally

**Full application (pipeline + dashboard):**
```bash
python -m sentiment_analysis.main
```

**Pipeline only (no dashboard):**
```bash
python -m sentiment_analysis.main --no-ui
```

**Dashboard only** (if the pipeline is running elsewhere):
```bash
python -m sentiment_analysis.dashboard.dash_app
```

**Ingestion monitoring dashboard** (Streamlit, optional):
```bash
streamlit run sentiment_analysis/dashboard/dashboard.py
```

**Log output** is written to both `stderr` (colourised) and `logs/ingestion.log` (rotating, 10 MB, 7-day retention, gzip).

---

## Project Structure

```
stock-sentiment/
│
├── sentiment_analysis/               # Main Python package
│   ├── __init__.py
│   ├── config.py                     # All settings from environment variables
│   ├── main.py                       # Entry point: pipeline + dashboard subprocess
│   ├── .env.example                  # Environment variable template
│   │
│   ├── ingestion/                    # Data collection
│   │   ├── rss_ingestor.py           # RSS polling, SEC enrichment, deduplication
│   │   ├── finviz_ingestor.py        # Finviz Elite bulk screener price fetch
│   │   └── scheduler.py              # APScheduler job definitions (6 jobs)
│   │
│   ├── nlp/                          # Text processing
│   │   ├── text_preprocessor.py      # NLTK tokenisation, lemmatisation, stop-words
│   │   ├── ticker_list.py            # S&P 500 tickers + 3-pass extraction
│   │   ├── ticker_mapper.py          # 500+ company-name → ticker fuzzy map
│   │   ├── ticker_overrides.json     # Manual company → ticker overrides
│   │   ├── entity_extractor.py       # Legacy 3-pass extractor
│   │   ├── entity_queue.py           # Unresolved entity persistence
│   │   ├── primary_company_scorer.py # Selects the primary ticker per article
│   │   └── pipeline.py               # NLP orchestrator
│   │
│   ├── entity_resolution/            # Production NER pipeline
│   │   ├── pipeline.py               # 5-stage orchestrator
│   │   ├── ner.py                    # spaCy NER extractor (en_core_web_sm)
│   │   ├── classifier.py             # 18-type entity taxonomy
│   │   ├── company_resolver.py       # Explicit ticker scan + TickerMapper lookup
│   │   ├── ticker_mapper.py          # Re-export from nlp/
│   │   └── confidence.py             # Classification + resolution confidence scores
│   │
│   ├── sentiment/                    # Sentiment scoring
│   │   ├── finbert_analyzer.py       # FinBERT (primary, local, batched)
│   │   ├── gemini_analyzer.py        # Gemini 2.5 Flash (fallback, batched up to 50)
│   │   ├── sec_analyzer.py           # Rule-based scorer for SEC filings
│   │   ├── lm_analyzer.py            # Loughran-McDonald financial dictionary
│   │   ├── aggregator.py             # Rolling window summaries + spike detection
│   │   └── pipeline.py               # Sentiment orchestrator (routing + storage)
│   │
│   ├── dashboard/                    # Plotly Dash web application
│   │   ├── dash_app.py               # App instance, layout, sidebar, topbar
│   │   ├── dashboard.py              # Streamlit ingestion monitor (separate)
│   │   ├── db.py                     # Sync DB helpers (NullPool psycopg2)
│   │   ├── formatters.py             # Market cap, ratio, percentage formatters
│   │   └── pages/
│   │       ├── news.py               # /           — live article feed
│   │       ├── screener.py           # /screener   — Finviz-style ticker table
│   │       ├── charts.py             # /charts     — TradingView embed
│   │       ├── watchlist.py          # /watchlist  — personal ticker list
│   │       ├── alerts.py             # /alerts     — alert rule management
│   │       └── stock.py              # /stock/<T>  — per-ticker workspace
│   │
│   ├── storage/                      # Database layer
│   │   ├── database.py               # Async engine + session factory
│   │   ├── models.py                 # SQLAlchemy ORM models (12 tables)
│   │   └── alembic/
│   │       ├── alembic.ini           # Alembic configuration
│   │       ├── env.py                # Migration environment
│   │       └── versions/             # 21 migration scripts (001 – 021)
│   │
│   ├── scripts/                      # One-time administrative scripts
│   │   ├── backfill_primary_ticker.py
│   │   ├── backfill_sec_summaries.py
│   │   └── cleanup_non_english.py
│   │
│   └── maintenance/                  # Ongoing maintenance utilities
│       └── reprocess_tickers.py
│
├── logs/                             # Rotating log files (auto-created)
├── Procfile                          # Railway / Heroku process definition
├── railway.json                      # Railway build and deploy configuration
├── requirements.txt                  # Python dependencies
└── README.md
```

---

## Dashboard Pages

### News Feed — `/`
The default landing page. Shows a live paginated stream of all ingested articles (50 per page), filterable by source feed and ticker. Each article card shows the source label, ingestion time, ticker tags, headline, article summary, and a colour-coded sentiment badge (Bullish / Somewhat Bullish / Neutral / Somewhat Bearish / Bearish). Refreshes automatically on a 30-second interval.

### Screener — `/screener`
A dense, data-rich ticker table in the style of the Finviz screener. Displays 25 tickers per page across 40+ columns including price, change %, market cap, sector, P/E, Forward P/E, PEG, P/S, P/B, EPS, ROE, ROA, margins, RSI, SMA distances, relative volume, ATR, short interest, analyst recommendation, and 24hr average sentiment. Filterable by sector, country, exchange, and ticker. Add or remove any ticker to the personal watchlist directly from the table row.

### Charts — `/charts`
Full-screen TradingView Advanced Chart widget embedded via iframe. Supports symbol search, multiple chart types, drawing tools, technical indicator overlays, and comparison mode. Renders in dark theme.

### Watchlist — `/watchlist`
Personal dashboard for actively tracked companies. Each watched ticker shows company name, live price, change %, last update time, 24hr average sentiment score, and total article count from the last 24 hours. Add new tickers by typing a symbol; remove them inline. Data refreshes every 30 seconds.

### Alerts — `/alerts`
Full alert rule management interface. Supports four alert types:
- **Price** — fires when the live price crosses a threshold (`>` or `<`)
- **Sentiment** — fires when the 24hr average sentiment score crosses a threshold
- **Breaking News** — fires once for each new article mentioning the ticker since the rule was last checked
- **Volume Spike** — fires when a ticker's 15-minute article volume exceeds 2× its rolling average

Summary cards show total alerts, currently active alerts, and the count triggered today. The recent history table shows every firing with ticker, alert type, trigger message, and timestamp. Alert rules can be paused, resumed, edited, or deleted inline.

### Stock Workspace — `/stock/<TICKER>`
A per-ticker research page accessible from the screener or by URL. Displays a price header (current price, change %, volume, pre/post-market prices, company name, sector, country, exchange, market cap), then five sections below:
- **Key Metrics**: P/E, Forward P/E, PEG, P/S, P/B, Dividend Yield, EPS TTM, Relative Volume, RSI 14, Market Cap
- **Financial Highlights**: ROE, ROA, Gross Margin, Operating Margin, Net Margin, Debt/Equity, Current Ratio, Quick Ratio
- **Technical Indicators**: SMA20/50/200 distances, 52W High/Low distances, Average Volume, ATR, Beta
- **Short Interest & Ownership**: Float Short, Short Ratio, Insider Ownership, Institutional Ownership, Analyst Recommendation, Analyst Target Price
- **Performance**: EPS growth (this year / next year / 5yr), Sales growth QoQ, Price performance (week / month)
- **24hr Sentiment Summary**: average sentiment score, article count, bullish/bearish/neutral breakdown, momentum

---

## Deployment to Railway

### 1. Create a Railway account and project

Sign up at [railway.app](https://railway.app), create a new project, and add your repository.

### 2. Provision a PostgreSQL database

In the Railway project dashboard, click **+ New** → **Database** → **PostgreSQL**. Railway will inject a `DATABASE_URL` environment variable automatically — the application handles the `postgres://` → `postgresql+asyncpg://` normalisation.

### 3. Set environment variables

In your Railway service's **Variables** tab, add:

| Variable | Value |
|---|---|
| `GEMINI_API_KEY` | Your Google Gemini API key |
| `FINVIZ_TOKEN` | Your Finviz Elite auth token |
| `LOG_LEVEL` | `INFO` |
| `RSS_POLL_INTERVAL_MINUTES` | `5` |

Railway injects `DATABASE_URL` from the linked PostgreSQL service automatically.

### 4. Run migrations

Railway does not run Alembic automatically. After your first deploy, open the Railway shell for your service and run:

```bash
alembic -c sentiment_analysis/storage/alembic/alembic.ini upgrade head
```

Alternatively, the application calls `run_migrations()` on startup, which applies idempotent `ADD COLUMN IF NOT EXISTS` patches — this is sufficient for Railway cold starts.

### 5. Deploy

Push to your linked branch. Railway builds with **Nixpacks** and starts the service using:

```
python -m sentiment_analysis.main
```

The service restarts automatically on failure (up to 3 retries, as configured in `railway.json`).

### 6. Access the dashboard

Railway exposes the Dash application (port 8050) via HTTPS at your service's public domain. Navigate to the URL shown in the Railway dashboard.

---

## Database

The application uses **PostgreSQL 14+**. All timestamps are stored as `TIMESTAMPTZ` (timezone-aware). GIN indexes on `ARRAY(String)` columns enable efficient `WHERE 'AAPL' = ANY(tickers)` queries.

### Tables

| Table | Description |
|---|---|
| `rss_articles` | Core table — one row per ingested article with NLP and sentiment columns |
| `ingestion_log` | Audit log — one row per source per poll cycle |
| `extracted_entities` | Named entities resolved to a ticker (CASCADE on article delete) |
| `unresolved_entities` | Company names that could not be mapped to a ticker, frequency-ranked |
| `ticker_sentiment_summary` | Per-ticker rolling aggregates: 1hr / 4hr / 24hr windows |
| `sentiment_spikes` | Recorded article-volume spikes (>2× rolling average) |
| `ticker_prices` | Latest price + fundamentals + technicals snapshot per ticker (upserted) |
| `ticker_snapshot_history` | One price snapshot per ticker per calendar day (append-only) |
| `watchlist` | User's personal watchlist — one row per tracked ticker |
| `alerts` | Alert rules (price / sentiment / breaking_news / volume_spike) |
| `alert_history` | Immutable log of every alert firing (CASCADE on alert delete) |
| `tweets` | Legacy Phase 1 table (Twitter ingestion retired; table preserved) |

### Migrations

Schema is managed with Alembic. 21 migrations cover the full history from initial schema through the alert system:

```
001  Initial schema (tweets, rss_articles, ingestion_log)
002  Fix ingestion_log.id auto-increment
003  Add cleaned_text to rss_articles
004  Create extracted_entities
005  Create unresolved_entities
006  Add sentiment columns to rss_articles
007  Create ticker_sentiment_summary
008  Create sentiment_spikes
009  Create ticker_prices
010  Add company info columns to ticker_prices
011  Add importance_score to rss_articles
012  Drop importance_score
013  Add primary_ticker to rss_articles
014  Create watchlist
015  Add fundamental and technical columns to ticker_prices
016  Add ROA, margins, beta to ticker_prices
017  Add Tier 1 screener fields (short interest, performance, analyst, ATR, ownership)
018  Create ticker_snapshot_history
019  Create alerts and alert_history
020  Add condition_met to alerts
021  Add last_seen_at to alerts
```

Apply all migrations:
```bash
alembic -c sentiment_analysis/storage/alembic/alembic.ini upgrade head
```

---

## Contributing

1. **Fork** the repository
2. **Create a branch** for your feature or fix: `git checkout -b feature/my-feature`
3. **Make your changes**, keeping logic and SQL queries intact
4. **Run the pipeline locally** to verify nothing is broken
5. **Commit** with a descriptive message
6. **Open a Pull Request** targeting `main`

Please keep PRs focused — one feature or fix per PR. For large changes, open an issue first to discuss the approach.


---

## Author

**Tjoet Aliya Lakmana**  
Penn State University  
Internship project under **Dr. Kaamran Raahemifar**

[GitHub](https://github.com/tjlakmana) · [Email](mailto:ta.lakmana@gmail.com)
