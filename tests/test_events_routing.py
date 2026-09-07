"""事件取数走 marketdata 包路由测试(MarketDataEventsCollector,无 CN 门禁)"""
import asyncio
from datetime import datetime, timedelta, timezone

import src.collectors.events_collector as ec


def _md_event(**kw):
    from marketdata.types import EventItem as MdEventItem

    base = dict(
        source="yfinance",
        external_id="AAPL:earnings:2026-10-29",
        event_type="earnings",
        title="Apple earnings",
        publish_time=datetime.now(timezone.utc),
        symbols=["AAPL"],
        importance=3,
        url="https://finance.yahoo.com/quote/AAPL",
    )
    base.update(kw)
    return MdEventItem(**base)


def test_collector_source_and_alias():
    """新采集器 source="marketdata",旧类名 EastMoneyEventsCollector 仍作为别名可用。"""
    assert ec.MarketDataEventsCollector.source == "marketdata"
    assert ec.EastMoneyEventsCollector is ec.MarketDataEventsCollector
    assert isinstance(ec.EventsCollector.from_database().collectors[0], ec.MarketDataEventsCollector)


def test_fetch_events_uses_marketdata(monkeypatch):
    """走 marketdata 包的 events(market=None、aware now、since_days 透传),转换为 PanWatch EventItem。"""
    captured: dict = {}

    class _MD:
        def events(self, symbols, *, market=None, since_days=7, now=None, ahead_days=30):
            captured.update(symbols=list(symbols), market=market, since_days=since_days,
                            now=now, ahead_days=ahead_days)
            return [_md_event()]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    out = asyncio.run(ec.MarketDataEventsCollector().fetch_events(["AAPL", "SHOP.TO"], since_days=14))

    assert captured["symbols"] == ["AAPL", "SHOP.TO"]
    assert captured["market"] is None
    assert captured["since_days"] == 14
    assert captured["now"] is not None and captured["now"].tzinfo is not None
    assert captured["ahead_days"] == 30
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, ec.EventItem)
    assert item.source == "yfinance"
    assert item.external_id == "AAPL:earnings:2026-10-29"
    assert item.event_type == "earnings"
    assert item.symbols == ["AAPL"]
    assert item.importance == 3


def test_fetch_events_no_cn_gate(monkeypatch):
    """不再按 is_enabled("CN") 门禁:美股/加股照常取数,模块里也没有 is_enabled。"""
    assert not hasattr(ec, "is_enabled")
    calls = {"n": 0}

    class _MD:
        def events(self, symbols, **kw):
            calls["n"] += 1
            return [_md_event(symbols=["SHOP.TO"], external_id="SHOP.TO:earnings")]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())
    out = asyncio.run(ec.MarketDataEventsCollector().fetch_events(["SHOP.TO"]))
    assert calls["n"] == 1 and len(out) == 1


def test_fetch_events_no_symbols_returns_empty(monkeypatch):
    """symbols 为空时短路返回空列表,不触达 marketdata。"""

    def _boom():
        raise AssertionError("must not call get_market_data")

    monkeypatch.setattr(ec, "get_market_data", _boom)
    assert asyncio.run(ec.MarketDataEventsCollector().fetch_events([])) == []


def test_fetch_events_applies_since_filter_and_dedupes(monkeypatch):
    """兼容旧 since 参数:按 since 精确重过滤,并按 (source, external_id) 去重、按时间倒序。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=3)

    class _MD:
        def events(self, symbols, **kw):
            return [
                _md_event(external_id="NEW", publish_time=now),
                _md_event(external_id="NEW", publish_time=now),  # duplicate
                _md_event(external_id="OLD", publish_time=now - timedelta(days=10)),
                _md_event(external_id="FUTURE", publish_time=now + timedelta(days=5)),
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())
    out = asyncio.run(ec.MarketDataEventsCollector().fetch_events(["AAPL"], since=since))
    assert [e.external_id for e in out] == ["FUTURE", "NEW"]


def test_fetch_events_failsoft_records_last_error(monkeypatch):
    """包层抛错时返回 [] 并记录 last_error,不向上抛异常。"""

    class _MD:
        def events(self, symbols, **kw):
            raise RuntimeError("vendor down")

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())
    c = ec.MarketDataEventsCollector()
    assert asyncio.run(c.fetch_events(["AAPL"])) == []
    assert "vendor down" in (c.last_error or "")


def test_events_collector_fetch_all_passes_window(monkeypatch):
    """EventsCollector.fetch_all 把 since_days 透传给包,并返回宿主 EventItem。"""
    captured: dict = {}

    class _MD:
        def events(self, symbols, *, market=None, since_days=7, now=None, ahead_days=30):
            captured["since_days"] = since_days
            return [_md_event()]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())
    out = asyncio.run(ec.EventsCollector.from_database().fetch_all(symbols=["AAPL"], since_days=30))
    assert captured["since_days"] == 30
    assert len(out) == 1 and isinstance(out[0], ec.EventItem)
