"""Vendor contract: one vendor = "how one source is fetched + parsed into the standard types".
No fallback inside a vendor; the Engine handles failover."""

from __future__ import annotations

from abc import ABC, abstractmethod

from marketdata.symbol import Symbol


class Vendor(ABC):
    #: registry name, aligned with SourceConfig.vendor / DataSource.provider
    name: str = ""
    #: supported markets (empty set = all); the Engine filters by market
    supports_markets: set[str] = set()

    @abstractmethod
    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        """Fetch and parse. Raise on failure (the Engine fails over); return [] when there is no data."""
        ...


class QuoteVendor(Vendor):
    """Quote vendor: fetch returns list[Quote]."""


class KlineVendor(Vendor):
    """Kline vendor: fetch returns list[Bar]. Single symbol."""


class EventsVendor(Vendor):
    """Events vendor: fetch returns list[EventItem]. Batch (many symbols)."""


class FlashNewsVendor(Vendor):
    """Market headline vendor: fetch returns list[FlashNews]. Market level, symbols may be empty."""


class NewsVendor(Vendor):
    """News vendor: fetch returns list[NewsArticle], per symbol."""


class FundamentalsVendor(Vendor):
    """Fundamentals vendor: fetch returns list[Fundamentals]. Per symbol (batch)."""


class DividendVendor(Vendor):
    """Dividend vendor: fetch returns list[DividendItem]. Per symbol (full history each)."""


class HoldersVendor(Vendor):
    """Holders vendor: fetch returns list[HolderItem] (breakdown / institution / insider_tx rows)."""


class FilingsVendor(Vendor):
    """Filings vendor: fetch returns list[FilingItem]. Per symbol (batch)."""
