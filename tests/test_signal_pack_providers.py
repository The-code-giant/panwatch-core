"""SignalPack provider gating tests.

The quote / kline / events / news blocks only run a configured provider when the marketdata
package implements a vendor of that name (``PACKAGE_VENDORS_BY_TYPE``); unknown providers are
skipped, and the defaults are ``yfinance``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from marketdata import PACKAGE_VENDORS_BY_TYPE

from src.core.signals import signal_pack
from src.core.signals.signal_pack import SignalPackBuilder
from src.models.market import MarketCode, StockData


class _Portfolio:
    def get_positions_for_stock(self, symbol, market=None, *, actionable_only=True):
        return []

    def get_aggregated_position(self, symbol, market=None, *, actionable_only=True):
        return None


def _stock(symbol: str) -> StockData:
    return StockData(
        symbol=symbol,
        name=symbol,
        market=MarketCode.US,
        current_price=100.0,
        change_pct=1.0,
        change_amount=1.0,
        open_price=99.0,
        high_price=101.0,
        low_price=98.0,
        prev_close=99.0,
        volume=1000,
        turnover=100000.0,
    )


def _policy(providers_by_type: dict[str, list[str]]):
    def fake(source_type, *, default_providers):
        provs = providers_by_type.get(source_type, default_providers)
        return [(p, {}) for p in provs], False

    return staticmethod(fake)


def _build(monkeypatch, providers_by_type: dict[str, list[str]], **kwargs):
    monkeypatch.setattr(SignalPackBuilder, "_source_policy", _policy(providers_by_type))
    return asyncio.run(
        SignalPackBuilder().build_for_symbols(
            symbols=[("AAPL", MarketCode.US, "Apple")],
            include_news=kwargs.pop("include_news", False),
            news_hours=24,
            portfolio=_Portfolio(),
            **kwargs,
        )
    )


def test_package_vendor_sets_include_yfinance():
    """PACKAGE_VENDORS_BY_TYPE 的 quote/kline/events 均包含 yfinance，且已无 capital_flow 等退役类型。"""
    for t in ("quote", "kline", "events"):
        assert "yfinance" in PACKAGE_VENDORS_BY_TYPE[t]
    for retired in ("capital_flow", "dragon_tiger", "margin", "shareholders", "northbound"):
        assert retired not in PACKAGE_VENDORS_BY_TYPE


def test_yfinance_quote_row_accepted(monkeypatch):
    """quote provider 为 yfinance 时调用 md_stock_data 并采纳其返回；sources.quote 记为 yfinance。"""
    calls: list[tuple[list[str], str]] = []

    def fake_stock_data(symbols, market):
        calls.append((list(symbols), market))
        return [_stock(s) for s in symbols]

    monkeypatch.setattr(signal_pack, "md_stock_data", fake_stock_data)
    packs = _build(monkeypatch, {"quote": ["yfinance"]}, include_technical=False)
    assert calls == [(["AAPL"], "US")]
    assert packs["AAPL"].quote is not None and packs["AAPL"].quote.current_price == 100.0
    assert packs["AAPL"].sources["quote"] == "yfinance"
    assert "quote" not in packs["AAPL"].missing


def test_unknown_quote_provider_skipped(monkeypatch):
    """quote provider 不是包内 vendor（如 bloomberg_terminal）时跳过，不调用 md_stock_data，quote 记为 unavailable。"""
    called = []
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: called.append(1) or [])
    packs = _build(monkeypatch, {"quote": ["bloomberg_terminal"]}, include_technical=False)
    assert called == []
    assert packs["AAPL"].quote is None
    assert packs["AAPL"].sources["quote"] == "unavailable"
    assert "quote" in packs["AAPL"].missing


def test_unknown_provider_then_yfinance_falls_through(monkeypatch):
    """provider 列表 [unknown, yfinance] 时跳过前者并由 yfinance 命中。"""
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [_stock(s) for s in syms])
    packs = _build(monkeypatch, {"quote": ["some_legacy", "yfinance"]}, include_technical=False)
    assert packs["AAPL"].quote is not None
    assert packs["AAPL"].sources["quote"] == "yfinance"


def test_yfinance_kline_row_accepted(monkeypatch):
    """kline provider 为 yfinance 时走 KlineCollector.get_kline_summary；未知 provider 则跳过并标记 kline 缺失。"""

    class _FakeKline:
        def __init__(self, market):
            self.market = market

        def get_kline_summary(self, symbol):
            return {"trend": "Bullish alignment", "ma5": 1.0}

    monkeypatch.setattr(signal_pack, "KlineCollector", _FakeKline)
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [])

    packs = _build(monkeypatch, {"kline": ["yfinance"]}, include_technical=True)
    assert packs["AAPL"].technical == {"trend": "Bullish alignment", "ma5": 1.0}
    assert packs["AAPL"].sources["kline"] == "yfinance"
    assert "kline" not in packs["AAPL"].missing

    packs = _build(monkeypatch, {"kline": ["some_legacy"]}, include_technical=True)
    assert packs["AAPL"].technical.get("error")
    assert packs["AAPL"].sources["kline"] == "unavailable"
    assert "kline" in packs["AAPL"].missing


def test_events_accept_yfinance(monkeypatch):
    """events provider 为 yfinance 时调用 EventsCollector 并把事件挂到对应标的；未知 provider 则跳过。"""
    import src.collectors.events_collector as ec

    class _Item:
        source = "yfinance"
        external_id = "e1"
        event_type = "earnings"
        title = "Q3 earnings"
        publish_time = datetime(2026, 9, 1, 12, 0)
        importance = 2
        url = ""
        symbols = ["AAPL"]

    class _FakeEvents:
        calls = 0

        @classmethod
        def from_database(cls):
            return cls()

        async def fetch_all(self, *, symbols=None, since_days=7):
            _FakeEvents.calls += 1
            return [_Item()]

    monkeypatch.setattr(ec, "EventsCollector", _FakeEvents)
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [])

    packs = _build(monkeypatch, {"events": ["yfinance"]}, include_technical=False, include_events=True, events_days=3)
    assert _FakeEvents.calls == 1
    assert packs["AAPL"].events is not None
    assert [e["title"] for e in packs["AAPL"].events.items] == ["Q3 earnings"]
    assert packs["AAPL"].sources["events"] == "yfinance"

    _FakeEvents.calls = 0
    packs = _build(monkeypatch, {"events": ["eastmoney_legacy"]}, include_technical=False, include_events=True)
    assert _FakeEvents.calls == 0
    assert packs["AAPL"].events.items == []
    assert packs["AAPL"].sources["events"] == "unavailable"


def test_news_grouped_per_market(monkeypatch):
    """新闻按市场分组：每个市场调用一次 md_news(market=...)，并携带 publisher。"""
    from src.collectors.news_collector import NewsItem

    calls: list[tuple[list[str], int, dict, str]] = []

    def fake_news(symbols, since_hours, names, market):
        calls.append((list(symbols), since_hours, dict(names or {}), market))
        return [
            NewsItem(source="yfinance", external_id=f"n-{s}", title=f"{s} news", content="",
                     publish_time=datetime(2026, 9, 1, 9, 30), symbols=[s], importance=1,
                     url="", publisher="Reuters")
            for s in symbols
        ]

    monkeypatch.setattr(signal_pack, "md_news", fake_news)
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [])
    monkeypatch.setattr(SignalPackBuilder, "_source_policy", _policy({}))
    packs = asyncio.run(
        SignalPackBuilder().build_for_symbols(
            symbols=[("AAPL", MarketCode.US, "Apple"), ("SHOP.TO", MarketCode.CA, "Shopify")],
            include_news=True,
            news_hours=48,
            portfolio=_Portfolio(),
            include_technical=False,
        )
    )
    assert sorted(c[3] for c in calls) == ["CA", "US"]
    assert all(c[1] == 48 for c in calls)
    assert {c[3]: c[2] for c in calls} == {"US": {"AAPL": "Apple"}, "CA": {"SHOP.TO": "Shopify"}}
    assert packs["AAPL"].news.items[0]["publisher"] == "Reuters"
    assert packs["SHOP.TO"].news.items[0]["title"] == "SHOP.TO news"
