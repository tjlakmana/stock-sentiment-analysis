"""
Module: rss_ingestor.py
Purpose: Poll RSS feeds, deduplicate articles, extract tickers, and persist to the database
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

RSS feed ingestor using feedparser.

Polls all feeds defined in config.settings.rss_feeds every N minutes.
Each feed is processed independently — a failure on one feed has no effect
on the others.  One IngestionLog row is written per feed per poll cycle.
"""
from __future__ import annotations

import re
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional, Set

import feedparser
from langdetect import DetectorFactory, LangDetectException, detect_langs
from loguru import logger
from sqlalchemy import select

from sentiment_analysis.config import settings
from sentiment_analysis.nlp.ticker_list import extract_tickers
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import IngestionLog, RSSArticle

DetectorFactory.seed = 0  # deterministic language detection across runs

# Opener with relaxed SSL for public RSS feeds whose TLS chains include
# intermediate CA certs without Basic Constraints marked critical — rejected by
# Python 3.12+ strict OpenSSL but harmless for unauthenticated feed reads.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_FEED_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_ssl_ctx)
)


def _is_english(title: str, summary: str = "") -> bool:
    """Return False if the combined title + summary text is not English.

    Two-stage check:
    1. Fast-path: reject when >15% of characters are non-ASCII (blocks CJK,
       Arabic, Cyrillic without a library call).
    2. langdetect on the combined text, requiring English confidence >= 0.80.
       Rejects and logs when detection is inconclusive or the top language is
       not English above the threshold.
    """
    text = f"{title} {summary}".strip() or title
    if not text:
        return True

    non_ascii = sum(1 for c in text if ord(c) > 127)
    if non_ascii / len(text) > 0.15:
        return False

    try:
        results = detect_langs(text)
    except LangDetectException:
        logger.info(f"Skipped — language detection inconclusive: {title[:60]}")
        return False

    for r in results:
        if r.lang == "en" and r.prob >= 0.80:
            return True

    top = results[0] if results else None
    lang = top.lang if top else "unknown"
    conf = f"{top.prob:.2f}" if top else "n/a"
    logger.info(f"Skipped non-English article — lang={lang} conf={conf}: {title[:60]}")
    return False


def _parse_published(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """
    Return a timezone-aware datetime from a feedparser entry's date fields.

    feedparser parses dates into `published_parsed` / `updated_parsed` as
    `time.struct_time` 9-tuples.  We prefer `published_parsed` but fall back
    to `updated_parsed` when the former is absent.
    """
    for field in ("published_parsed", "updated_parsed"):
        val = entry.get(field)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


# ── SEC EDGAR enrichment ──────────────────────────────────────────────────────

_SEC_SOURCES = frozenset({"sec_edgar", "sec_form4", "sec_10q", "sec_s1", "sec_sc13g"})

_FILED_RE   = re.compile(r'Filed:\s*(\d{4}-\d{2}-\d{2})')
_HTML_TAGS  = re.compile(r'<[^>]+>')
_ARCHIVE_RE = re.compile(r'href="(/Archives/edgar/data/[^"]+)"', re.IGNORECASE)

# Display label and optional suffix for each SEC source
_FORM_DISPLAY_MAP: dict[str, tuple[str, str]] = {
    'sec_edgar':  ('8-K',    ''),
    'sec_form4':  ('Form 4', ' (Insider Trading)'),
    'sec_10q':    ('10-Q',   ''),
    'sec_s1':     ('S-1',    ''),
    'sec_sc13g':  ('SC 13G', ' (Large Investor)'),
}


def _sec_company_name(raw_title: str) -> str:
    """Extract company name from an EDGAR RSS title, stripping form type and CIK.

    Handles all form types including those with hyphens (8-K) or spaces (SC 13G).
    """
    clean = _HTML_TAGS.sub('', raw_title).strip()
    # Remove trailing role qualifiers like (Filer), (Issuer) and CIK like (0000789019)
    clean = re.sub(r'(\s*\(\d+\)|\s*\([A-Za-z]+\))+\s*$', '', clean).strip()
    # Everything after the first " - " separator is the company name
    parts = re.split(r'\s+-\s+', clean, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else clean


def _clean_sec_title(raw_title: str, source_name: str) -> str:
    """Format an EDGAR RSS title cleanly, removing CIK numbers and role suffixes.

    Examples:
      "4 - Microsoft Corp (0000789019) (Issuer)"  → "Form 4 — Microsoft Corp (Insider Trading)"
      "8-K - Apple Inc (0000320193) (Filer)"      → "8-K — Apple Inc"
    """
    company = _sec_company_name(raw_title)
    form_str, suffix = _FORM_DISPLAY_MAP.get(source_name, ('SEC Filing', ''))
    return f'{form_str} — {company}{suffix}'


def _sec_fetch(url: str, max_kb: int = 64) -> str:
    """Fetch an EDGAR URL with the shared SSL-bypassing opener."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; FeedFetcher/1.0)'},
        )
        with _FEED_OPENER.open(req, timeout=8) as resp:
            return resp.read(max_kb * 1024).decode('utf-8', errors='replace')
    except Exception:
        return ''


def _main_doc_url(index_html: str, doc_type: str) -> str:
    """Return the primary document URL from an EDGAR index page."""
    # Scan <tr> blocks; pick the first row that contains <td>doc_type</td>
    # and an /Archives/ href.
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', index_html, re.DOTALL | re.IGNORECASE):
        if re.search(fr'<td[^>]*>\s*{re.escape(doc_type)}\s*</td>', row, re.IGNORECASE):
            m = _ARCHIVE_RE.search(row)
            if m:
                return f'https://www.sec.gov{m.group(1)}'
    # Fallback: first .htm link in /Archives/
    m = re.search(r'href="(/Archives/edgar/data/[^"]+\.htm)"', index_html, re.IGNORECASE)
    return f'https://www.sec.gov{m.group(1)}' if m else ''


def _items_from_8k(doc_html: str) -> list[str]:
    """Extract Item headings from an 8-K filing document."""
    text = _HTML_TAGS.sub(' ', doc_html)
    text = re.sub(r'\s+', ' ', text)
    items: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'\bItem\s+(\d+\.\d+)\s+([^.]{5,100}\.?)', text, re.IGNORECASE):
        num  = m.group(1)
        desc = m.group(2).strip().rstrip('.')
        if num not in seen and desc:
            seen.add(num)
            items.append(f'Item {num} {desc}')
        if len(items) >= 8:
            break
    return items


def _form4_summary(xml: str, fallback_company: str) -> str:
    """Build a human-readable summary from Form 4 XML.

    Output: "Insider [name] bought/sold [X] shares of [company] at $[price] per share."
    """
    def _v(tag: str) -> str:
        for pat in (
            fr'<{tag}[^>]*>\s*<value>\s*([^<]+?)\s*</value>',
            fr'<{tag}[^>]*>\s*([^<\s][^<]*?)\s*</{tag}>',
        ):
            mm = re.search(pat, xml, re.IGNORECASE)
            if mm:
                return mm.group(1).strip()
        return ''

    issuer  = _v('issuerName') or fallback_company
    ticker  = _v('issuerTradingSymbol')
    owner   = _v('rptOwnerName') or 'An insider'
    shares  = _v('transactionShares')
    price   = _v('transactionPricePerShare')
    tx_code = _v('transactionCode')           # P=Purchase, S=Sale (open-market)
    ad_code = _v('transactionAcquiredDisposedCode')  # A=Acquired, D=Disposed

    if tx_code == 'P':
        action = 'bought'
    elif tx_code == 'S':
        action = 'sold'
    elif ad_code == 'A':
        action = 'bought'
    elif ad_code == 'D':
        action = 'sold'
    else:
        action = 'transacted in'

    company = issuer + (f' [{ticker}]' if ticker else '')
    parts = [f'Insider {owner} {action}']
    if shares:
        try:
            parts.append(f'{float(shares):,.0f} shares')
        except ValueError:
            parts.append(f'{shares} shares')
    parts.append(f'of {company}')
    if price:
        try:
            parts.append(f'at ${float(price):.2f} per share')
        except ValueError:
            pass
    return ' '.join(parts) + '.'


def _extract_text(html: str, max_chars: int = 1000) -> str:
    """Strip HTML tags and return the first max_chars of meaningful text."""
    text = _HTML_TAGS.sub(' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def _build_sec_summary(source_name: str, entry) -> str:
    """Return an enriched summary for an SEC EDGAR RSS entry."""
    url         = (_HTML_TAGS.sub('', entry.get('link') or '')).strip()
    raw_summary = _HTML_TAGS.sub(' ', entry.get('summary', '')).strip()
    company     = _sec_company_name(entry.get('title', ''))

    date_m    = _FILED_RE.search(raw_summary)
    date_part = f' on {date_m.group(1)}' if date_m else ''

    # ── 10-Q / S-1 / SC 13G: metadata-only summaries, no document fetch ──
    if source_name == 'sec_10q':
        return f'{company} filed quarterly report (10-Q){date_part}.'
    if source_name == 'sec_s1':
        return f'{company} filed IPO registration (S-1){date_part}.'
    if source_name == 'sec_sc13g':
        return f'Large investor position filing for {company}{date_part}.'

    # ── Form 4 and 8-K: fetch filing index to extract content ────────────
    if not url:
        return f'{company} filed an SEC document{date_part}.'

    time.sleep(0.5)
    index_html = _sec_fetch(url)
    if not index_html:
        return f'{company} filed an SEC document{date_part}.'

    # ── Form 4: extract insider transaction from XML ───────────────────────
    if source_name == 'sec_form4':
        xml_m = re.search(
            r'href="(/Archives/edgar/data/[^"]+\.xml)"',
            index_html, re.IGNORECASE,
        )
        if xml_m:
            time.sleep(0.5)
            xml = _sec_fetch(f'https://www.sec.gov{xml_m.group(1)}', max_kb=32)
            if xml:
                return _form4_summary(xml, company)
        return f'Insider filing (Form 4) for {company}{date_part}.'

    # ── 8-K: extract filing items ──────────────────────────────────────────
    if source_name == 'sec_edgar':
        main_url = _main_doc_url(index_html, '8-K')
        items: list[str] = []
        if main_url:
            time.sleep(0.5)
            doc = _sec_fetch(main_url, max_kb=64)
            if doc:
                items = _items_from_8k(doc)
        base = f'{company} filed 8-K{date_part}'
        if items:
            return base + ' reporting ' + '; '.join(items) + '.'
        return base + '.'

    return f'{company} filed an SEC document{date_part}.'


# ─────────────────────────────────────────────────────────────────────────────


class RSSIngestor:
    """
    Polls all configured RSS feeds and persists new articles to the database.

    A single instance should be reused across scheduler runs to preserve
    the in-memory URL dedup set across poll cycles.
    """

    def __init__(self) -> None:
        # In-memory dedup: article URLs seen since process start
        self._seen_urls: Set[str] = set()

    async def run(self) -> None:
        """Poll all feeds.  Feed failures are caught and logged individually."""
        for source_name, feed_url in settings.rss_feeds.items():
            await self._process_feed(source_name, feed_url)

    async def _process_feed(self, source_name: str, feed_url: str) -> None:
        """
        Fetch and store new articles from a single RSS feed.

        Args:
            source_name: Short internal name (e.g. ``"sec_edgar"``).
                         Used as the source_name column value and in the
                         ingestion_log ``source`` column as ``rss:<name>``.
            feed_url:    Full URL of the RSS/Atom feed.
        """
        log_source = f"rss:{source_name}"
        records_fetched: int = 0
        records_stored: int = 0
        error_message: Optional[str] = None

        try:
            logger.info(f"[{log_source}] Fetching {feed_url}")
            # Pre-fetch bytes with our urllib opener so we control the SSL
            # context and User-Agent.  Passing raw bytes to feedparser triggers
            # its more lenient fallback XML parser — important for feeds like
            # SEC EDGAR whose Atom XML uses non-standard encoding declarations.
            req = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; FeedFetcher/1.0)"},
            )
            with _FEED_OPENER.open(req, timeout=20) as resp:
                raw_bytes = resp.read()
            feed = feedparser.parse(raw_bytes)

            if feed.bozo:
                bozo_msg = str(getattr(feed, "bozo_exception", None) or "Feed parse error")
                if not feed.entries:
                    error_message = bozo_msg
                    logger.warning(f"[{log_source}] Parse error (no entries): {bozo_msg}")
                    async with get_async_session() as session:
                        session.add(IngestionLog(
                            source=log_source,
                            records_fetched=0,
                            records_stored=0,
                            error_message=error_message,
                        ))
                    return
                logger.warning(
                    f"[{log_source}] Malformed feed, processing {len(feed.entries)} entries: {bozo_msg}"
                )

            records_fetched = len(feed.entries)
            new_articles: List[RSSArticle] = []

            async with get_async_session() as session:
                for entry in feed.entries:
                    try:
                        url: str = (entry.get("link") or "").strip()
                        if not url:
                            continue

                        # Fast-path dedup
                        if url in self._seen_urls:
                            continue

                        # DB dedup — relies on the UNIQUE constraint on url
                        existing = await session.execute(
                            select(RSSArticle.id)
                            .where(RSSArticle.url == url)
                            .limit(1)
                        )
                        if existing.scalar_one_or_none() is not None:
                            self._seen_urls.add(url)
                            continue

                        title: str = entry.get("title", "")
                        summary: str = entry.get("summary", "")
                        published_at = _parse_published(entry)

                        if not _is_english(title, summary):
                            continue

                        # Clean SEC titles: remove CIK numbers, role suffixes,
                        # and format consistently ("Form 4 — Microsoft Corp (Insider Trading)").
                        if source_name in _SEC_SOURCES:
                            title = _clean_sec_title(title, source_name)

                        # For SEC feeds replace the sparse RSS summary with enriched content
                        # so the sentiment model has meaningful text to score.
                        if source_name in _SEC_SOURCES:
                            try:
                                summary = _build_sec_summary(source_name, entry)
                                logger.debug(f"[{log_source}] SEC enriched: {summary[:80]}")
                            except Exception as _e:
                                logger.warning(f"[{log_source}] SEC enrichment failed: {_e}")

                        # Extract tickers from the combined title + summary text
                        tickers = extract_tickers(f"{title} {summary}")

                        raw_payload = {
                            "title": title,
                            "summary": summary,
                            "url": url,
                            "published": entry.get("published", ""),
                            "source": source_name,
                            "tags": [
                                t.get("term", "")
                                for t in entry.get("tags", [])
                                if isinstance(t, dict)
                            ],
                        }

                        new_articles.append(
                            RSSArticle(
                                title=title,
                                summary=summary,
                                url=url,
                                published_at=published_at,
                                source_name=source_name,
                                tickers=tickers,
                                raw_json=raw_payload,
                            )
                        )
                        self._seen_urls.add(url)
                    except Exception as _entry_err:
                        logger.debug(f"[{log_source}] Skipping malformed entry: {_entry_err}")

                if new_articles:
                    session.add_all(new_articles)
                    records_stored = len(new_articles)

                session.add(
                    IngestionLog(
                        source=log_source,
                        records_fetched=records_fetched,
                        records_stored=records_stored,
                        error_message=error_message,
                    )
                )

            logger.info(
                f"[{log_source}] Stored {records_stored}/{records_fetched} "
                "new articles."
            )

        except Exception as exc:
            error_message = str(exc)
            logger.exception(f"[{log_source}] Unexpected error: {exc}")
            # Best-effort log write so the dashboard shows the failure
            try:
                async with get_async_session() as session:
                    session.add(
                        IngestionLog(
                            source=log_source,
                            records_fetched=records_fetched,
                            records_stored=records_stored,
                            error_message=error_message,
                        )
                    )
            except Exception:
                pass  # Prevent logging errors from cascading
