"""Market headline vendors: publisher RSS/Atom feeds (CNBC, MarketWatch, Financial Post, BNN
Bloomberg, Globe and Mail, Investing.com). Market level: ``symbols`` is always empty."""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import FlashNews
from marketdata.vendors.base import FlashNewsVendor
from marketdata.vendors.news import _EPOCH, _UA, _strip_html

logger = logging.getLogger(__name__)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

_FLASH_IMPORTANT_RE = re.compile(
    r"\b(Fed|FOMC|rate (hike|cut)|inflation|CPI|PCE|jobs report|payrolls|tariff|recession|GDP|"
    r"Bank of Canada|BoC|halts?|plunge|surge|crash)\b",
    re.IGNORECASE,
)


def _flash_importance(title: str) -> int:
    return 2 if _FLASH_IMPORTANT_RE.search(title or "") else 0


def _parse_date(s: str) -> datetime:
    """RFC 2822 (RSS) via ``parsedate_to_datetime``, else ISO 8601 (Atom), else EPOCH."""
    s = (s or "").strip()
    if not s:
        return _EPOCH
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return _EPOCH


def _parse_rss_item(item: ET.Element, source: str) -> FlashNews | None:
    title = (item.findtext("title") or "").strip()
    if not title:
        return None
    link = (item.findtext("link") or "").strip()
    guid = (item.findtext("guid") or "").strip()
    desc = _strip_html(item.findtext("description") or "")
    pub = _parse_date(item.findtext("pubDate") or "")
    external_id = guid or link or hashlib.sha1(title.encode("utf-8")).hexdigest()
    return FlashNews(
        source=source,
        external_id=external_id,
        title=title,
        content=desc[:500],
        publish_time=pub,
        symbols=[],
        importance=_flash_importance(title),
        url=link,
    )


def _atom_link(entry: ET.Element) -> str:
    for link_el in entry.findall(f"{_ATOM_NS}link"):
        href = link_el.get("href") or ""
        rel = link_el.get("rel")
        if href and (rel is None or rel == "alternate"):
            return href
    return ""


def _parse_atom_entry(entry: ET.Element, source: str) -> FlashNews | None:
    title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
    if not title:
        return None
    link = _atom_link(entry)
    guid = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
    body = entry.findtext(f"{_ATOM_NS}summary") or entry.findtext(f"{_ATOM_NS}content") or ""
    desc = _strip_html(body)
    pub_raw = (entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published") or "").strip()
    pub = _parse_date(pub_raw)
    external_id = guid or link or hashlib.sha1(title.encode("utf-8")).hexdigest()
    return FlashNews(
        source=source,
        external_id=external_id,
        title=title,
        content=desc[:500],
        publish_time=pub,
        symbols=[],
        importance=_flash_importance(title),
        url=link,
    )


def _parse_feed(text: str, *, source: str) -> list[FlashNews]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    rss_items = root.findall("./channel/item")
    if rss_items:
        return [a for a in (_parse_rss_item(it, source) for it in rss_items) if a is not None]
    entries = root.findall(f"{_ATOM_NS}entry")
    return [a for a in (_parse_atom_entry(e, source) for e in entries) if a is not None]


class RssFlashNewsVendor(FlashNewsVendor):
    """Base class for a single-feed publisher: subclasses set ``feed_url``, ``host_key`` and
    ``label`` (used as the fetch's ``log_label``); ``name``/``supports_markets`` from ``Vendor``."""

    feed_url: str = ""
    host_key: str = ""
    label: str = ""

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        # Engine quirk: Request.limit always arrives under config["days"] (engine.py builds
        # call_config as {**src.config, "days": req.limit, **req.extra}), so flash_news reads
        # its item limit from "days" rather than a dedicated key.
        limit = int(config.get("days") or 50)
        feed_url = config.get("feed_url") or self.feed_url
        if not feed_url:
            return []
        text = market_get(
            feed_url,
            host_key=self.host_key,
            headers={
                "User-Agent": _UA,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
            min_interval_s=2.0,
            timeout=10,
            retries=1,
            parse="text",
            log_label=self.label or self.name,
        )
        if not text:
            return []
        items = _parse_feed(text, source=self.name)
        items.sort(key=lambda a: a.publish_time, reverse=True)
        return items[:limit]


class CnbcFlashNewsVendor(RssFlashNewsVendor):
    name = "cnbc"
    supports_markets = {"US"}
    feed_url = "https://www.cnbc.com/id/100003114/device/rss/rss.html"
    host_key = "cnbc.com"
    label = "CNBC Top News"


class MarketWatchFlashNewsVendor(RssFlashNewsVendor):
    name = "marketwatch"
    supports_markets = {"US"}
    feed_url = "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"
    host_key = "feeds.content.dowjones.io"
    label = "MarketWatch MarketPulse"


class FinancialPostFlashNewsVendor(RssFlashNewsVendor):
    name = "financial_post"
    supports_markets = {"CA"}
    feed_url = "https://financialpost.com/feed"
    host_key = "financialpost.com"
    label = "Financial Post"


class BnnBloombergFlashNewsVendor(RssFlashNewsVendor):
    name = "bnn_bloomberg"
    supports_markets = {"CA"}
    feed_url = "https://www.bnnbloomberg.ca/arc/outboundfeeds/rss/?outputType=xml"
    host_key = "bnnbloomberg.ca"
    label = "BNN Bloomberg"


class GlobeAndMailFlashNewsVendor(RssFlashNewsVendor):
    name = "globe_and_mail"
    supports_markets = {"CA"}
    feed_url = "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/?outputType=xml"
    host_key = "theglobeandmail.com"
    label = "Globe and Mail Business"


class InvestingComFlashNewsVendor(RssFlashNewsVendor):
    name = "investing_com"
    supports_markets = {"US", "CA"}
    feed_url = "https://www.investing.com/rss/news_25.rss"
    host_key = "investing.com"
    label = "Investing.com Stock News"
