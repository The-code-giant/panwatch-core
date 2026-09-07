"""YFinanceKlineVendor: daily bars through the adapter for US, CA, CRYPTO and GOLD."""

from __future__ import annotations

import pytest

from _yf_fakes import FakeTicker, make_history_df, patch_yf
from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import Bar
from marketdata.vendors.yfinance import YFinanceKlineVendor


def _df():
    return make_history_df([
        ("2026-07-01", 180.0, 185.0, 179.0, 184.0, 1000000),
        ("2026-07-02", 182.0, 186.0, 181.0, 185.5, 1200000),
        ("2026-07-03", 185.0, 188.0, 184.0, 187.0, 900000),
    ])


def test_kline_us_maps_symbol_and_bars(monkeypatch):
    """A US share class is requested from Yahoo as BRK-B and the frame becomes Bars in date order."""
    calls = patch_yf(monkeypatch, tickers={"BRK-B": FakeTicker("BRK-B", history_df=_df())})
    out = YFinanceKlineVendor().fetch([Symbol.parse("BRK.B")], {"days": 60})
    assert calls["tickers"] == ["BRK-B"]
    assert len(out) == 3 and isinstance(out[0], Bar)
    assert out[0].date == "2026-07-01" and out[0].close == 184.0 and out[2].volume == 900000.0


def test_kline_ca_uses_yahoo_period_from_days(monkeypatch):
    """The Canadian symbol is passed through unchanged and days maps to the Yahoo period enum."""
    t = FakeTicker("SHOP.TO", history_df=_df())
    patch_yf(monkeypatch, tickers={"SHOP.TO": t})
    out = YFinanceKlineVendor().fetch([Symbol.parse("SHOP.TO")], {"days": 120})
    assert len(out) == 3
    assert t.calls[0] == ("history", {"period": "6mo", "interval": "1d", "auto_adjust": False, "actions": False})


def test_kline_truncates_to_days(monkeypatch):
    """Only the last `days` bars are returned."""
    patch_yf(monkeypatch, tickers={"BTC-USD": FakeTicker("BTC-USD", history_df=_df())})
    out = YFinanceKlineVendor().fetch([Symbol.parse("BTC-USD")], {"days": 2})
    assert [b.date for b in out] == ["2026-07-02", "2026-07-03"]


def test_kline_failure_raises_vendor_error(monkeypatch):
    """A library failure surfaces as VendorError so the Engine fails over to the next vendor."""
    patch_yf(monkeypatch, tickers={"GC=F": FakeTicker("GC=F", raise_on_history=RuntimeError("down"))})
    with pytest.raises(VendorError):
        YFinanceKlineVendor().fetch([Symbol.parse("XAUUSD")], {"days": 30})


def test_kline_empty_frame_returns_empty(monkeypatch):
    """An empty history frame yields [] rather than an exception."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL")})
    assert YFinanceKlineVendor().fetch([Symbol.parse("AAPL")], {"days": 30}) == []
