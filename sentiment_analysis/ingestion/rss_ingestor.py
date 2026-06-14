"""
RSS feed ingestor using feedparser.

Polls all feeds defined in config.settings.rss_feeds every N minutes.
Each feed is processed independently — a failure on one feed has no effect
on the others.  One IngestionLog row is written per feed per poll cycle.
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional, Set

import feedparser
from loguru import logger
from sqlalchemy import select

from sentiment_analysis.config import settings
from sentiment_analysis.ingestion.ticker_list import extract_tickers
from sentiment_analysis.storage.database import get_async_session
from sentiment_analysis.storage.models import IngestionLog, RSSArticle

# Opener with relaxed SSL for public RSS feeds whose TLS chains include
# intermediate CA certs without Basic Constraints marked critical — rejected by
# Python 3.12+ strict OpenSSL but harmless for unauthenticated feed reads.
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE
_FEED_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_ssl_ctx)
)


def _is_english(title: str) -> bool:
    """Return False if more than 15% of title characters are non-ASCII."""
    if not title:
        return True
    non_ascii = sum(1 for c in title if ord(c) > 127)
    return (non_ascii / len(title)) <= 0.15


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

_SEC_TITLE_RE = re.compile(
    r'^(?P<form>[^\-]+?)\s*-\s*(?P<company>.+?)\s+\((?P<cik>\d+)\)',
    re.IGNORECASE,
)
_FILED_RE    = re.compile(r'Filed:\s*(\d{4}-\d{2}-\d{2})')
_HTML_TAGS   = re.compile(r'<[^>]+>')
_ARCHIVE_RE  = re.compile(r'href="(/Archives/edgar/data/[^"]+)"', re.IGNORECASE)


def _parse_sec_title(raw: str) -> tuple[str, str, str]:
    """Return (form_type, company_name, cik) from an EDGAR RSS title."""
    clean = _HTML_TAGS.sub('', raw).strip()
    m = _SEC_TITLE_RE.match(clean)
    if m:
        return m.group('form').strip(), m.group('company').strip(), m.group('cik')
    return '', clean, ''


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


def _form4_summary(xml: str, fallback_company: str, date_str: str) -> str:
    """Build a readable one-liner from Form 4 XML text."""
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
    owner   = _v('rptOwnerName')
    role    = ('officer'  if re.search(r'<isOfficer>\s*1',  xml) else
               'director' if re.search(r'<isDirector>\s*1', xml) else 'insider')
    o_title = _v('officerTitle')
    shares  = _v('transactionShares')
    price   = _v('transactionPricePerShare')
    code    = _v('transactionAcquiredDisposedCode')

    action = 'purchased' if code == 'A' else 'sold' if code == 'D' else 'transacted in'
    who    = owner or 'An insider'
    if o_title:
        who += f' ({o_title})'

    parts = [f'{who} {action}']
    if shares:
        try:
            parts.append(f'{float(shares):,.0f} shares')
        except ValueError:
            parts.append(f'{shares} shares')
    parts.append(f'of {issuer}' + (f' [{ticker}]' if ticker else ''))
    if price:
        try:
            parts.append(f'at ${float(price):.2f}')
        except ValueError:
            pass
    if date_str:
        parts.append(date_str)
    return ' '.join(parts) + '.'


def _build_sec_summary(source_name: str, entry) -> str:
    """
    Return an enriched natural-language summary for an SEC EDGAR RSS entry.

    Makes up to 2 additional HTTP requests (index page + main document) only
    for 8-K and Form 4 entries.  All other SEC types use title+date only.
    """
    url         = (_HTML_TAGS.sub('', entry.get('link') or '')).strip()
    raw_summary = _HTML_TAGS.sub(' ', entry.get('summary', '')).strip()
    form_type, company, _ = _parse_sec_title(entry.get('title', ''))

    date_m   = _FILED_RE.search(raw_summary)
    filed    = date_m.group(1) if date_m else ''
    date_str = f'on {filed}' if filed else ''

    # Fast path — filing index adds little value for these types
    if source_name == 'sec_10q':
        return f'{company} filed a quarterly report (10-Q) {date_str}'.strip() + '.'
    if source_name == 'sec_s1':
        return f'{company} filed an S-1 registration statement (IPO filing) {date_str}'.strip() + '.'
    if source_name == 'sec_sc13g':
        return f'{company} filed an SC 13G large-investor position disclosure {date_str}'.strip() + '.'

    if not url:
        return f'{company} filed {form_type or "an SEC document"} {date_str}'.strip() + '.'

    index_html = _sec_fetch(url)
    if not index_html:
        return f'{company} filed {form_type or "an SEC document"} {date_str}'.strip() + '.'

    # 8-K: fetch main document and extract Items
    if source_name == 'sec_edgar':
        main_url = _main_doc_url(index_html, '8-K')
        items: list[str] = []
        if main_url:
            doc = _sec_fetch(main_url, max_kb=16)
            if doc:
                items = _items_from_8k(doc)
        base = f'{company} filed an 8-K report {date_str}'.strip()
        if items:
            return base + ' reporting ' + '; '.join(items) + '.'
        return base + '.'

    # Form 4: parse XML document for insider transaction details
    if source_name == 'sec_form4':
        xml_m = re.search(
            r'href="(/Archives/edgar/data/[^"]+\.xml)"',
            index_html, re.IGNORECASE,
        )
        if xml_m:
            xml = _sec_fetch(f'https://www.sec.gov{xml_m.group(1)}', max_kb=32)
            if xml:
                return _form4_summary(xml, company, date_str)
        return f'Insider filing (Form 4) for {company} {date_str}'.strip() + '.'

    return f'{company} filed {form_type or "an SEC document"} {date_str}'.strip() + '.'


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

                        if not _is_english(title):
                            logger.debug(f"[{log_source}] Skipping non-English: {title[:60]!r}")
                            continue

                        # For SEC feeds replace the sparse RSS summary with enriched content
                        # so Gemini has meaningful text to score.
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
