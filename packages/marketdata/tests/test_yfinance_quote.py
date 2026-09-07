"""YFinanceQuoteVendor: fast_info prices enriched from the cached Ticker.info."""

from __future__ import annotations

from _yf_fakes import FakeTicker, patch_yf
from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.vendors import yf_adapter
from marketdata.vendors.yfinance import YFinanceQuoteVendor

_FAST = {"last_price": 150.0, "previous_close": 148.0, "open": 149.0,
         "day_high": 151.0, "day_low": 147.0, "last_volume": 1000}
_INFO = {"shortName": "Apple Inc.", "trailingPE": 30.5, "marketCap": 2_300_000_000_000,
         "floatShares": 15_000_000_000, "averageVolume": 500}


def test_yfinance_quote_parses_prices(monkeypatch):
    """Prices come from fast_info and change amount/percent are derived from the previous close."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", fast_info=_FAST)})
    out = YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
    assert len(out) == 1
    q = out[0]
    assert q.symbol == "AAPL" and q.market == "US" and q.current_price == 150.0
    assert round(q.change_pct, 4) == round((150.0 - 148.0) / 148.0 * 100, 4)
    assert q.open_price == 149.0 and q.high_price == 151.0 and q.low_price == 147.0 and q.volume == 1000.0


def test_yfinance_quote_enriched_from_info(monkeypatch):
    """name, P/E, market cap, float cap, turnover rate, turnover and volume ratio are filled from info in raw currency units."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", fast_info=_FAST, info=_INFO)})
    q = YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})[0]
    assert q.name == "Apple Inc." and q.pe_ratio == 30.5
    assert q.total_market_value == 2_300_000_000_000
    assert q.circulating_market_value == 15_000_000_000 * 150.0
    assert abs(q.turnover_rate - 1000 / 15_000_000_000 * 100) < 1e-12
    assert q.turnover == 150.0 * 1000
    assert q.volume_ratio == 2.0


def test_yfinance_quote_survives_info_failure(monkeypatch):
    """An info failure never drops the quote: prices are returned with the enrichment fields left empty."""
    patch_yf(monkeypatch, tickers={"SHOP.TO": FakeTicker("SHOP.TO", fast_info=_FAST, raise_on_info=RuntimeError("x"))})
    out = YFinanceQuoteVendor().fetch([Symbol.parse("SHOP.TO")], {})
    assert len(out) == 1 and out[0].market == "CA" and out[0].name == "" and out[0].pe_ratio is None


def test_yfinance_quote_skips_symbol_without_price(monkeypatch):
    """A symbol with no last price is skipped and the reason is captured for the Test button."""
    import marketdata.http as mh
    patch_yf(monkeypatch, tickers={"NOPE": FakeTicker("NOPE", fast_info={}), "AAPL": FakeTicker("AAPL", fast_info=_FAST)})
    with mh.capture_errors() as errs:
        out = YFinanceQuoteVendor().fetch([Symbol.parse("NOPE"), Symbol.parse("AAPL")], {})
    assert [q.symbol for q in out] == ["AAPL"]
    assert any("NOPE" in e for e in errs)


def test_yfinance_quote_missing_lib_raises(monkeypatch):
    """When yfinance cannot be imported the adapter raises VendorError and the Engine fails over."""
    yf_adapter.reset_caches()
    monkeypatch.setattr(yf_adapter, "ticker_factory", None)

    def _no_lib():
        raise VendorError("yfinance is not installed")

    monkeypatch.setattr(yf_adapter, "import_yfinance", _no_lib)
    try:
        YFinanceQuoteVendor().fetch([Symbol.parse("AAPL")], {})
    except VendorError:
        pass
    else:
        raise AssertionError("expected VendorError")
