"""yf_adapter: the single Yahoo choke point (breaker, 429 trip, timeout, symbol mapping, proxy)."""

from __future__ import annotations

import time

import pytest

import marketdata.http as mh
from _yf_fakes import FakeTicker, make_history_df, patch_yf
from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.vendors import yf_adapter as ya


@pytest.fixture(autouse=True)
def _reset():
    ya.reset_caches()
    mh.reset_circuit_breaker()
    yield
    ya.reset_caches()
    mh.reset_circuit_breaker()


def test_call_returns_result_and_resets_breaker(monkeypatch):
    """A successful call returns fn's value and leaves the yfinance breaker closed."""
    monkeypatch.setattr(mh, "throttle", lambda *a, **k: None)
    assert ya.call("x", lambda: 42) == 42
    assert not mh.breaker_blocked("yfinance")


def test_breaker_opens_after_three_failures(monkeypatch):
    """Three consecutive failures open the shared yfinance breaker and the fourth call is refused without running fn."""
    monkeypatch.setattr(mh, "throttle", lambda *a, **k: None)

    def boom():
        raise RuntimeError("nope")

    for _ in range(3):
        with pytest.raises(VendorError):
            ya.call("test", boom, symbol="AAPL")
    assert mh.breaker_blocked("yfinance")
    ran = []
    with pytest.raises(VendorError, match="circuit breaker open"):
        ya.call("test", lambda: ran.append(1))
    assert ran == []


def test_rate_limit_trips_immediately(monkeypatch):
    """A YFRateLimitError trips the breaker on the first failure with the 5 minute cooldown."""
    monkeypatch.setattr(mh, "throttle", lambda *a, **k: None)

    class YFRateLimitError(Exception):
        pass

    def boom():
        raise YFRateLimitError("Too Many Requests")

    with pytest.raises(VendorError, match="rate limit"):
        ya.call("test", boom)
    assert mh.breaker_blocked("yfinance")
    with mh._BREAKER_LOCK:
        st = mh._breaker["yfinance"]
    assert st["open_until"] - time.time() > 250


def test_timeout_raises_vendor_error(monkeypatch):
    """A call exceeding the timeout raises VendorError with the label and symbol in the message."""
    monkeypatch.setattr(mh, "throttle", lambda *a, **k: None)

    def slow():
        time.sleep(0.5)
        return 1

    with pytest.raises(VendorError, match="Yahoo history AAPL: timeout"):
        ya.call("Yahoo history", slow, symbol="AAPL", timeout=0.05)


def test_probing_bypasses_breaker(monkeypatch):
    """Inside capture_errors (the Test button) an open breaker is ignored and the failure reason is captured."""
    monkeypatch.setattr(mh, "throttle", lambda *a, **k: None)
    mh.breaker_trip("yfinance", 300)
    with mh.capture_errors() as errs:
        assert ya.call("test", lambda: "ok") == "ok"
        with pytest.raises(VendorError):
            ya.call("test", lambda: (_ for _ in ()).throw(ValueError("bad")), symbol="X")
    assert any("bad" in e for e in errs)


def test_symbol_mapping():
    """to_yahoo_symbol maps US share classes to hyphens and CA share classes to hyphenated bases."""
    assert ya.to_yahoo_symbol(Symbol.parse("BRK.B")) == "BRK-B"
    assert ya.to_yahoo_symbol(Symbol.parse("BRK_B", "US")) == "BRK-B"
    assert ya.to_yahoo_symbol(Symbol.parse("CTC.A.TO")) == "CTC-A.TO"
    assert ya.to_yahoo_symbol(Symbol.parse("SHOP.TO")) == "SHOP.TO"
    assert ya.to_yahoo_symbol(Symbol.parse("BTC-USD")) == "BTC-USD"


def test_proxy_applied_from_config(monkeypatch):
    """apply_proxy passes config["proxy"] to yf.set_config once and memoises the value."""
    calls = []

    class _YF:
        @staticmethod
        def set_config(proxy=None):
            calls.append(proxy)

    monkeypatch.setattr(ya, "import_yfinance", lambda: _YF)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    ya.apply_proxy({"proxy": "http://p:1"})
    ya.apply_proxy({"proxy": "http://p:1"})
    assert calls == ["http://p:1"]
    ya.apply_proxy({"proxy": ""})
    assert calls[-1] is None


def test_get_ticker_is_cached(monkeypatch):
    """get_ticker returns the same object for the same Yahoo symbol within the cache TTL."""
    calls = patch_yf(monkeypatch, tickers=lambda ysym: FakeTicker(ysym))
    a = ya.get_ticker(Symbol.parse("AAPL"))
    b = ya.get_ticker("AAPL")
    assert a is b and calls["tickers"] == ["AAPL"]


def test_history_prefers_adj_close_and_skips_nan(monkeypatch):
    """history converts the frame to Bars using Adj Close, skips NaN rows and keeps the last `days`."""
    df = make_history_df([
        ("2026-07-01", 10, 12, 9, 11, 100, 10.5),
        ("2026-07-02", float("nan"), 12, 9, 11, 100),
        ("2026-07-03", 11, 13, 10, 12, 200, 11.8),
        ("2026-07-06", 12, 14, 11, 13, 300, 12.9),
    ])
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", history_df=df)})
    bars = ya.history("AAPL", days=2)
    assert [b.date for b in bars] == ["2026-07-03", "2026-07-06"]
    assert bars[0].close == 11.8 and bars[1].volume == 300.0


def test_info_returns_empty_on_failure(monkeypatch):
    """info never raises: a failing Ticker.info yields {} and is not cached."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", raise_on_info=RuntimeError("x"))})
    assert ya.info("AAPL") == {}


def test_fast_quote_reads_fast_info_keys(monkeypatch):
    """fast_quote reads FastInfo by subscript and returns None when there is no last price."""
    patch_yf(monkeypatch, tickers={
        "AAPL": FakeTicker("AAPL", fast_info={"last_price": 150.0, "previous_close": 148.0, "open": 149.0,
                                              "day_high": 151.0, "day_low": 147.0, "last_volume": 1000}),
        "NOPE": FakeTicker("NOPE", fast_info={}),
    })
    q = ya.fast_quote("AAPL")
    assert q == {"last": 150.0, "prev_close": 148.0, "open": 149.0, "high": 151.0, "low": 147.0, "volume": 1000.0}
    assert ya.fast_quote("NOPE") is None


def test_download_last_and_to_utc(monkeypatch):
    """download_last returns {sym: {last, prev_close}} and to_utc handles ISO, epoch and date inputs."""
    from datetime import date, datetime, timezone
    patch_yf(monkeypatch, download={"^VIX": {"last": 15.2, "prev_close": 15.0}, "CAD=X": {"last": None}})
    assert ya.download_last(["^VIX", "CAD=X", "GC=F"]) == {"^VIX": {"last": 15.2, "prev_close": 15.0}}
    assert ya.to_utc("2026-09-04T17:44:11Z") == datetime(2026, 9, 4, 17, 44, 11, tzinfo=timezone.utc)
    assert ya.to_utc(1782777600) == datetime(2026, 6, 30, 0, 0, tzinfo=timezone.utc)
    assert ya.to_utc(date(2026, 11, 3)) == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert ya.to_utc("garbage") == ya._EPOCH


def test_df_records_converts_index_nan_and_timestamps():
    """df_records includes the index as a column, maps NaN to None and Timestamps to ISO dates."""
    import pandas as pd
    df = pd.DataFrame({"pctHeld": [0.1, float("nan")], "Date Reported": pd.to_datetime(["2026-06-30", "2026-03-31"])},
                      index=pd.Index(["A", "B"], name="Holder"))
    rows = ya.df_records(df)
    assert rows == [{"Holder": "A", "pctHeld": 0.1, "Date Reported": "2026-06-30"},
                    {"Holder": "B", "pctHeld": None, "Date Reported": "2026-03-31"}]
    assert ya.df_records(None) == [] and ya.df_records(pd.DataFrame()) == []
