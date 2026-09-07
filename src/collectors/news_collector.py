"""News data structure plus a thin aggregating collector shim.

Fetching (Yahoo Finance news, Google News RSS) lives in the marketdata package
(packages/marketdata). This module keeps the ``NewsItem`` dataclass consumers use and a
``NewsCollector`` shim that forwards to the package, so consumers need no changes.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    """One news row."""
    source: str           # vendor key: "yfinance" / "google_news" / a flash-news feed key
    external_id: str      # vendor-side unique id
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)  # related symbols
    importance: int = 0   # 0-3
    url: str = ""         # original link
    publisher: str = ""   # outlet that wrote the story (Reuters, Bloomberg ...)


class NewsCollector:
    """Aggregating news collector: a thin shim over the marketdata package."""

    @classmethod
    def from_database(cls) -> "NewsCollector":
        """Config is read on demand by the package's DbConfigProvider; nothing to load here."""
        return cls()

    async def fetch_all(
        self,
        symbols: list[str] | None = None,
        since_hours: int = 2,
        symbol_names: dict[str, str] | None = None,
        market: str | None = None,
    ) -> list[NewsItem]:
        """News from every enabled news source (aggregation, dedupe and ordering happen in the package).

        Args:
            symbols: stock symbols
            since_hours: window in hours
            symbol_names: symbol -> company name (Google News searches by name)
            market: market code; ``None`` lets the package detect it per symbol

        Returns:
            newest first
        """
        from src.core.marketdata_client import md_news

        return await asyncio.to_thread(md_news, symbols or [], since_hours, symbol_names, market)
