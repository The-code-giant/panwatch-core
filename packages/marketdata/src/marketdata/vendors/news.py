"""Symbol news vendors: Yahoo Finance (via ``yf_adapter``) and Google News RSS.

Both emit ``NewsArticle`` (see ``types.py``). Per-symbol failures never kill the batch: a
symbol that raises is logged and skipped, the rest of the batch still returns. ``[]`` means
"no data"; a real failure raises ``VendorError`` only when *every* symbol failed (so the
Engine can fail over to the next source) via propagating the last error when nothing at all
came back and at least one symbol was attempted.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from marketdata.errors import VendorError
from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import NewsArticle
from marketdata.vendors import yf_adapter
from marketdata.vendors.base import NewsVendor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers (also imported by flash_news.py)
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """Strip tags and unescape entities; collapse whitespace."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


_TITLE_IMPORTANT_RE = re.compile(
    r"earnings|guidance|acqui|merger|takeover|FDA|lawsuit|downgrade|upgrade|dividend|"
    r"buyback|split|bankrupt|SEC|halt|recall",
    re.IGNORECASE,
)


def _title_importance(title: str) -> int:
    """2 when the title carries a market-moving keyword, else 0."""
    return 2 if _TITLE_IMPORTANT_RE.search(title or "") else 0


def _norm_title(title: str) -> str:
    """Lowercase, alphanumerics only, collapsed spaces, first 80 chars (dedupe key)."""
    t = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return re.sub(r"\s+", " ", t)[:80]


def _canon_url(url: str) -> str:
    """Lowercase host, strip ``www.``, drop query/fragment (dedupe key)."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return ""
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{p.scheme.lower()}://{host}{p.path}" if host else ""


def _parse_rfc2822(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------


class YFinanceNewsVendor(NewsVendor):
    """``Ticker.get_news()`` per symbol; parses both the yfinance 1.x nested ``content``
    shape and the legacy flat shape."""

    name = "yfinance"
    supports_markets = {"US", "CA", "CRYPTO", "GOLD"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        out: list[NewsArticle] = []
        count = config.get("count") or 20
        tab = config.get("tab") or "all"
        for sym in symbols:
            code = sym.code
            try:
                t = yf_adapter.get_ticker(sym)
                items = yf_adapter.call(
                    "Yahoo news",
                    lambda t=t, count=count, tab=tab: t.get_news(count=count, tab=tab),
                    symbol=code,
                )
            except VendorError as e:
                # One bad symbol never kills the batch; the adapter already recorded the error.
                logger.debug(f"yfinance news {code} failed: {e}")
                continue
            for item in items or []:
                article = self._parse_item(item, code)
                if article is not None:
                    out.append(article)
        return out

    @staticmethod
    def _parse_item(item: dict, code: str) -> NewsArticle | None:
        if not isinstance(item, dict):
            return None
        content = item.get("content")
        c = content if isinstance(content, dict) else item
        title = (c.get("title") or "").strip()
        if not title:
            return None
        summary = c.get("summary") or c.get("description") or ""
        pub = c.get("pubDate") or c.get("displayTime") or item.get("providerPublishTime")
        provider_obj = c.get("provider")
        provider = ""
        if isinstance(provider_obj, dict):
            provider = provider_obj.get("displayName") or ""
        if not provider:
            pub_field = item.get("publisher")
            provider = pub_field if isinstance(pub_field, str) else ""
        url = ""
        canonical = c.get("canonicalUrl")
        if isinstance(canonical, dict):
            url = canonical.get("url") or ""
        if not url:
            click = c.get("clickThroughUrl")
            if isinstance(click, dict):
                url = click.get("url") or ""
        if not url:
            url = item.get("link") or ""
        raw_id = item.get("id") or c.get("id")
        external_id = str(raw_id) if raw_id else hashlib.sha1((url or title).encode("utf-8")).hexdigest()
        content_type = c.get("contentType") or ""
        importance = max(_title_importance(title), 1 if content_type == "PRESS_RELEASE" else 0)
        return NewsArticle(
            source="yfinance",
            external_id=external_id,
            title=title,
            content=_strip_html(summary)[:300],
            publish_time=yf_adapter.to_utc(pub),
            symbols=[code],
            importance=importance,
            url=url,
            publisher=provider,
        )


# ---------------------------------------------------------------------------
# Google News RSS
# ---------------------------------------------------------------------------

_GOOGLE_NEWS_URL = "https://news.google.com/rss/search"


def _google_locale(market: str) -> dict[str, str]:
    if str(market).upper() == "CA":
        return {"hl": "en-CA", "gl": "CA", "ceid": "CA:en"}
    return {"hl": "en-US", "gl": "US", "ceid": "US:en"}


def _fetch_google_rss(query: str, market: str) -> list[NewsArticle]:
    params = {"q": query, **_google_locale(market)}
    text = market_get(
        _GOOGLE_NEWS_URL,
        host_key="news.google.com",
        params=params,
        headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/xml"},
        min_interval_s=1.0,
        timeout=10,
        retries=1,
        parse="text",
        log_label="Google News RSS",
    )
    if not text:
        return []
    return _parse_google_rss(text)


def _parse_google_rss(text: str) -> list[NewsArticle]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: list[NewsArticle] = []
    for item in root.findall("./channel/item"):
        title_raw = (item.findtext("title") or "").strip()
        if not title_raw:
            continue
        source_el = item.find("source")
        publisher = ""
        if source_el is not None and source_el.text:
            publisher = source_el.text.strip()
        title = title_raw
        if publisher:
            suffix = f" - {publisher}"
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        pub = _parse_rfc2822(pub_raw) or _EPOCH
        desc = _strip_html(item.findtext("description") or "")
        external_id = guid or link or hashlib.sha1(title.encode("utf-8")).hexdigest()
        out.append(
            NewsArticle(
                source="google_news",
                external_id=external_id,
                title=title,
                content=desc[:300],
                publish_time=pub,
                symbols=[],
                importance=_title_importance(title),
                url=link,
                publisher=publisher,
            )
        )
    return out


class GoogleNewsRssVendor(NewsVendor):
    """Google News RSS search, one query per symbol: ``"{name}" stock`` (or ``"{ticker} stock"``
    when no display name is known), localised to the symbol's market."""

    name = "google_news"
    supports_markets = {"US", "CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        out: list[NewsArticle] = []
        names = config.get("symbol_names") or {}
        for sym in symbols:
            code = sym.code
            display = names.get(code)
            query = f'"{display}" stock' if display else f"{code.split('.')[0]} stock"
            items = _fetch_google_rss(query, sym.market.value)
            for a in items[:20]:
                out.append(
                    NewsArticle(
                        source=a.source,
                        external_id=a.external_id,
                        title=a.title,
                        content=a.content,
                        publish_time=a.publish_time,
                        symbols=[code],
                        importance=a.importance,
                        url=a.url,
                        publisher=a.publisher,
                    )
                )
        return out

    @classmethod
    def fetch_by_keyword(cls, keyword: str, market: str = "US") -> list[NewsArticle]:
        """Free-text search (theme / industry keyword), not tied to a single symbol."""
        kw = (keyword or "").strip()
        query = f'"{kw}" stock' if kw else "stock market"
        return _fetch_google_rss(query, market)[:20]
