"""daily_report market-index fetch tests.

- Every market tries Yahoo first (``md.yahoo_index_quotes``): US = ^GSPC/^IXIC/^DJI, CA = ^GSPTSE.
- Tencent (``md.index_quotes``) is only a US fallback for rows Yahoo did not return.
- Retired markets (CN/HK) return an empty list and never call marketdata.
"""

from __future__ import annotations

import asyncio

from src.agents import daily_report
from src.models.market import IndexData, MarketCode

_YAHOO_ROWS = {
    "^GSPC": {"symbol": "^GSPC", "name": "S&P 500", "current_price": 7718.6, "change_pct": -0.38,
              "change_amount": -29.11, "prev_close": 7747.71, "currency": "USD"},
    "^IXIC": {"symbol": "^IXIC", "name": "Nasdaq Composite", "current_price": 26506.99, "change_pct": -0.29,
              "change_amount": -77.07, "prev_close": 26584.06, "currency": "USD"},
    "^DJI": {"symbol": "^DJI", "name": "Dow Jones", "current_price": 46000.5, "change_pct": 0.12,
             "change_amount": 55.1, "prev_close": 45945.4, "currency": "USD"},
    "^GSPTSE": {"symbol": "^GSPTSE", "name": "S&P/TSX Composite index", "current_price": 36513.8,
                "change_pct": -0.33, "change_amount": -119.3, "prev_close": 36633.1, "currency": "CAD"},
}

_TENCENT_ROWS = {
    "usINX": {"symbol": ".INX", "name": "S&P 500", "current_price": 7700.0, "change_pct": -0.5,
              "change_amount": -30.0, "prev_close": 7730.0, "volume": 2531375980.0, "turnover": 0.0},
    "usIXIC": {"symbol": ".IXIC", "name": "Nasdaq", "current_price": 26500.0, "change_pct": -0.3,
               "change_amount": -77.0, "prev_close": 26577.0, "volume": 5604391920.0, "turnover": 0.0},
    "usDJI": {"symbol": ".DJI", "name": "Dow Jones", "current_price": 45990.0, "change_pct": 0.1,
              "change_amount": 50.0, "prev_close": 45940.0, "volume": 400000000.0, "turnover": 0.0},
}


class _FakeMarketData:
    def __init__(self, yahoo_available: set[str] | None = None, yahoo_raises: bool = False):
        self.yahoo_available = set(_YAHOO_ROWS) if yahoo_available is None else yahoo_available
        self.yahoo_raises = yahoo_raises
        self.calls: list[list[str]] = []
        self.yahoo_calls: list[list[str]] = []

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        self.calls.append(list(tencent_symbols))
        return [_TENCENT_ROWS[s] for s in tencent_symbols if s in _TENCENT_ROWS]

    def yahoo_index_quotes(self, yahoo_symbols: list[str]) -> list[dict]:
        self.yahoo_calls.append(list(yahoo_symbols))
        if self.yahoo_raises:
            raise RuntimeError("yahoo down")
        return [_YAHOO_ROWS[s] for s in yahoo_symbols if s in self.yahoo_available]


def test_us_uses_yahoo_first(monkeypatch):
    """US indices go through md.yahoo_index_quotes (^GSPC/^IXIC/^DJI) first; Tencent is not called when Yahoo returns every row."""
    fake_md = _FakeMarketData()
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert fake_md.yahoo_calls == [["^GSPC", "^IXIC", "^DJI"]]
    assert fake_md.calls == []
    assert len(indices) == 3
    assert all(isinstance(i, IndexData) for i in indices)
    assert [i.symbol for i in indices] == ["^GSPC", "^IXIC", "^DJI"]
    assert indices[0].name == "S&P 500"
    assert indices[0].market == MarketCode.US
    assert indices[0].current_price == 7718.6
    assert indices[0].change_pct == -0.38
    assert indices[0].change_amount == -29.11
    assert indices[0].volume == 0.0  # Yahoo meta has no volume/turnover; defaults to 0 instead of KeyError
    assert indices[0].turnover == 0.0


def test_us_tencent_fallback_only_for_missing_rows(monkeypatch):
    """When Yahoo misses some US rows, only those rows are fetched from Tencent (mapped raw symbols); Yahoo rows are kept."""
    fake_md = _FakeMarketData(yahoo_available={"^GSPC"})
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert fake_md.yahoo_calls == [["^GSPC", "^IXIC", "^DJI"]]
    assert fake_md.calls == [["usIXIC", "usDJI"]]
    assert [i.symbol for i in indices] == ["^GSPC", ".IXIC", ".DJI"]
    assert indices[0].current_price == 7718.6  # Yahoo row kept
    assert indices[1].current_price == 26500.0  # Tencent fallback row
    assert indices[1].volume == 5604391920.0


def test_us_yahoo_failure_falls_back_to_tencent(monkeypatch):
    """If Yahoo raises for US, every US index comes from Tencent (fail-soft, no exception)."""
    fake_md = _FakeMarketData(yahoo_raises=True)
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert fake_md.calls == [["usINX", "usIXIC", "usDJI"]]
    assert [i.symbol for i in indices] == [".INX", ".IXIC", ".DJI"]


def test_ca_uses_yahoo_only(monkeypatch):
    """The Canadian index (S&P/TSX Composite) goes through md.yahoo_index_quotes; Tencent is never called for CA, even when Yahoo returns nothing."""
    fake_md = _FakeMarketData()
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.CA))

    assert fake_md.calls == []
    assert fake_md.yahoo_calls == [["^GSPTSE"]]
    assert len(indices) == 1
    assert indices[0].market == MarketCode.CA
    assert indices[0].symbol == "^GSPTSE"
    assert indices[0].current_price == 36513.8
    assert indices[0].turnover == 0.0

    empty_md = _FakeMarketData(yahoo_available=set())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: empty_md)
    assert asyncio.run(agent._fetch_index_for_market(MarketCode.CA)) == []
    assert empty_md.calls == []


def test_retired_markets_return_empty(monkeypatch):
    """Retired markets (CN/HK) return an empty list and never touch marketdata; the CN Tencent index map is gone."""
    fake_md = _FakeMarketData()
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    assert asyncio.run(agent._fetch_index_for_market(MarketCode.HK)) == []
    assert asyncio.run(agent._fetch_index_for_market(MarketCode.CN)) == []
    assert fake_md.calls == []
    assert fake_md.yahoo_calls == []
    assert not hasattr(daily_report, "_CN_INDEX_TENCENT_SYMBOLS")
    assert MarketCode.CN not in daily_report._INDEX_YAHOO_BY_MARKET
