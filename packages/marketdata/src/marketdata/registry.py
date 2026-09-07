"""Single source of truth for the legal vendors of every data type in the package.

``MarketData.__init__`` builds each Engine's ``vendors={}`` with ``build_vendors(datatype)``;
``PACKAGE_VENDORS_BY_TYPE`` derives the vendor-name sets from the same ``VENDOR_CLASSES_BY_TYPE``
so the two can never drift apart.

The host (PanWatch ``DataSource`` table) uses this to decide whether a ``(type, provider)`` row is
an orphan: ``legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed providers``.
Discovery and the index strip are market-level, not symbol-based; they do not go through an
Engine or a DataSource row and therefore do not appear here.

Priority order (seeded by the host): quote ``yfinance 0, tencent 10, sina 20, eastmoney 30``;
kline ``yfinance 0, yahoo 10, tencent 20, eastmoney 30``. The Chinese vendors that remain are
legacy (CN/HK) and unreachable for US/CA symbols by ``supports_markets``.
"""

from __future__ import annotations

from marketdata.vendors.dividend import YFinanceDividendVendor
from marketdata.vendors.eastmoney import EastmoneyQuoteVendor
from marketdata.vendors.events import NasdaqCalendarVendor, YFinanceEventsVendor
from marketdata.vendors.filings import SecEdgarFilingsVendor, YFinanceNewswireFilingsVendor
from marketdata.vendors.flash_news import (
    BnnBloombergFlashNewsVendor,
    CnbcFlashNewsVendor,
    FinancialPostFlashNewsVendor,
    GlobeAndMailFlashNewsVendor,
    InvestingComFlashNewsVendor,
    MarketWatchFlashNewsVendor,
)
from marketdata.vendors.fundamentals import SecEdgarFundamentalsVendor, YFinanceFundamentalsVendor
from marketdata.vendors.holders import EdgarForm4HoldersVendor, YFinanceHoldersVendor
from marketdata.vendors.kline import EastmoneyKlineVendor, TencentKlineVendor, YahooKlineVendor
from marketdata.vendors.news import GoogleNewsRssVendor, YFinanceNewsVendor
from marketdata.vendors.sina import SinaQuoteVendor
from marketdata.vendors.tencent import TencentQuoteVendor
from marketdata.vendors.yfinance import YFinanceKlineVendor, YFinanceQuoteVendor

# data type -> {vendor name: vendor class}. Importing a vendor is cheap: optional third-party
# libraries (yfinance) are imported lazily inside the adapter, never at module import time.
VENDOR_CLASSES_BY_TYPE: dict[str, dict[str, type]] = {
    "quote": {
        "yfinance": YFinanceQuoteVendor,
        "tencent": TencentQuoteVendor,
        "sina": SinaQuoteVendor,
        "eastmoney": EastmoneyQuoteVendor,
    },
    "kline": {
        "yfinance": YFinanceKlineVendor,
        "yahoo": YahooKlineVendor,
        "tencent": TencentKlineVendor,
        "eastmoney": EastmoneyKlineVendor,
    },
    "news": {
        "yfinance": YFinanceNewsVendor,
        "google_news": GoogleNewsRssVendor,
    },
    "flash_news": {
        "cnbc": CnbcFlashNewsVendor,
        "marketwatch": MarketWatchFlashNewsVendor,
        "financial_post": FinancialPostFlashNewsVendor,
        "bnn_bloomberg": BnnBloombergFlashNewsVendor,
        "globe_and_mail": GlobeAndMailFlashNewsVendor,
        "investing_com": InvestingComFlashNewsVendor,
    },
    "events": {
        "yfinance": YFinanceEventsVendor,
        "nasdaq": NasdaqCalendarVendor,
    },
    "fundamentals": {
        "yfinance": YFinanceFundamentalsVendor,
        "sec_edgar": SecEdgarFundamentalsVendor,
    },
    "dividend": {
        "yfinance": YFinanceDividendVendor,
    },
    "holders": {
        "yfinance": YFinanceHoldersVendor,
        "sec_edgar": EdgarForm4HoldersVendor,
    },
    "filings": {
        "sec_edgar": SecEdgarFilingsVendor,
        "yfinance_newswire": YFinanceNewswireFilingsVendor,
    },
}

# Legal vendor names per data type (frozen so callers cannot mutate them by accident).
PACKAGE_VENDORS_BY_TYPE: dict[str, frozenset[str]] = {
    datatype: frozenset(classes.keys()) for datatype, classes in VENDOR_CLASSES_BY_TYPE.items()
}


def build_vendors(datatype: str) -> dict[str, object]:
    """Instantiate every vendor of ``datatype`` for Engine injection. Unknown type -> ``{}``."""
    return {name: cls() for name, cls in VENDOR_CLASSES_BY_TYPE.get(datatype, {}).items()}
