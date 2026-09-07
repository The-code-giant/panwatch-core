"""marketdata: multi-market market-data layer with pluggable, keyless vendors."""

from marketdata.client import MACRO_SYMBOLS, MarketData
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.errors import MarketDataError, VendorError
from marketdata.http import capture_errors, record_error
from marketdata.ports import ConfigProvider, MetricsSink, SourceConfig
from marketdata.registry import PACKAGE_VENDORS_BY_TYPE
from marketdata.symbol import Market, Symbol, canonical_code, from_yfinance
from marketdata.types import (
    Bar,
    DividendItem,
    EventItem,
    FilingItem,
    FlashNews,
    Fundamentals,
    HolderItem,
    HotBoard,
    HotStock,
    NewsArticle,
    Quote,
    Request,
    Response,
)
from marketdata.universe import Listing

__version__ = "0.2.0"

__all__ = [
    "MarketData", "MACRO_SYMBOLS", "Symbol", "Market", "canonical_code", "from_yfinance",
    "Bar", "EventItem", "FlashNews", "Fundamentals", "HotStock", "HotBoard", "NewsArticle",
    "DividendItem", "HolderItem", "FilingItem", "Listing",
    "Quote", "Request", "Response",
    "SourceConfig", "ConfigProvider", "MetricsSink",
    "StaticConfigProvider", "InMemoryMetricsSink",
    "PACKAGE_VENDORS_BY_TYPE",
    "capture_errors", "record_error",
    "MarketDataError", "VendorError", "__version__",
]
