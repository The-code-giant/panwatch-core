"""PACKAGE_VENDORS_BY_TYPE is the single authority for legal vendors and must match the Engines."""

from __future__ import annotations

from marketdata import PACKAGE_VENDORS_BY_TYPE
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.registry import VENDOR_CLASSES_BY_TYPE, build_vendors


def test_package_vendors_by_type_content():
    """The registry lists exactly the English keyless vendors plus the legacy CN/HK quote and kline vendors."""
    assert PACKAGE_VENDORS_BY_TYPE == {
        "quote": frozenset({"yfinance", "tencent", "sina", "eastmoney"}),
        "kline": frozenset({"yfinance", "yahoo", "tencent", "eastmoney"}),
        "news": frozenset({"yfinance", "google_news"}),
        "flash_news": frozenset({"cnbc", "marketwatch", "financial_post", "bnn_bloomberg", "globe_and_mail", "investing_com"}),
        "events": frozenset({"yfinance", "nasdaq"}),
        "fundamentals": frozenset({"yfinance", "sec_edgar"}),
        "dividend": frozenset({"yfinance"}),
        "holders": frozenset({"yfinance", "sec_edgar"}),
        "filings": frozenset({"sec_edgar", "yfinance_newswire"}),
    }


def test_retired_types_are_gone():
    """capital_flow, dragon_tiger, margin, shareholders, northbound and stooq are no longer registered."""
    for t in ("capital_flow", "dragon_tiger", "margin", "shareholders", "northbound"):
        assert t not in PACKAGE_VENDORS_BY_TYPE
    assert "stooq" not in PACKAGE_VENDORS_BY_TYPE["kline"]


def test_package_vendors_by_type_matches_actual_engine_registration():
    """Every Engine on a MarketData instance registers exactly the vendors the registry declares."""
    md = MarketData(config=StaticConfigProvider({}))
    engines = {
        "quote": md._quote_engine,
        "kline": md._kline_engine,
        "events": md._events_engine,
        "flash_news": md._flash_news_engine,
        "fundamentals": md._fundamentals_engine,
        "dividend": md._dividend_engine,
        "holders": md._holders_engine,
        "filings": md._filings_engine,
    }
    for datatype, engine in engines.items():
        assert set(engine.vendors.keys()) == PACKAGE_VENDORS_BY_TYPE[datatype]
    assert set(md._news_vendors.keys()) == PACKAGE_VENDORS_BY_TYPE["news"]


def test_build_vendors_instantiates_all_registered_classes():
    """build_vendors instantiates every class and each instance carries its registry name."""
    for datatype, classes in VENDOR_CLASSES_BY_TYPE.items():
        vendors = build_vendors(datatype)
        assert set(vendors.keys()) == set(classes.keys())
        for name, instance in vendors.items():
            assert instance.name == name


def test_vendor_markets_are_us_ca_first():
    """Every information vendor supports US or CA; none is CN-only."""
    for datatype in ("news", "flash_news", "events", "fundamentals", "dividend", "holders", "filings"):
        for name, inst in build_vendors(datatype).items():
            assert inst.supports_markets & {"US", "CA"}, (datatype, name)


def test_build_vendors_unknown_datatype_returns_empty():
    """An unknown data type yields an empty vendor map."""
    assert build_vendors("not_a_real_type") == {}
