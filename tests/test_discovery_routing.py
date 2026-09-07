"""发现榜单取数路由测试:统一走 marketdata 包,并经可交易股票池 is_tradable 过滤。"""
import asyncio

import pytest

import src.collectors.discovery_collector as dc
from src.core import universe


@pytest.fixture
def tradable_all(monkeypatch):
    """默认让所有代码都可交易(不依赖本地股票池缓存)。"""
    monkeypatch.setattr(dc, "is_tradable", lambda m, s: True)


def _md_stock(symbol="AAPL", market="US", name="Apple Inc.", price=200.0,
              change_pct=1.23, turnover=999999.0, volume=1000.0):
    from marketdata.types import HotStock as MdHotStock

    return MdHotStock(symbol=symbol, market=market, name=name, price=price,
                      change_pct=change_pct, turnover=turnover, volume=volume)


def test_collector_alias_kept():
    """DiscoveryCollector 为新类名,EastMoneyDiscoveryCollector 别名仍指向同一类。"""
    assert dc.EastMoneyDiscoveryCollector is dc.DiscoveryCollector


def test_fetch_hot_stocks_uses_marketdata(monkeypatch, tradable_all):
    """fetch_hot_stocks 走 marketdata 包的 hot_stocks(请求 limit*2),转换为 HotStock,透传 proxy,并切到 limit。"""
    captured: dict = {}

    class _MD:
        def hot_stocks(self, *, market="US", mode="turnover", limit=20, proxy=None):
            captured.update(market=market, mode=mode, limit=limit, proxy=proxy)
            return [_md_stock(symbol=f"S{i}") for i in range(limit)]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.DiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_stocks(market="US", mode="losers", limit=20))

    assert captured == {"market": "US", "mode": "losers", "limit": 40,
                        "proxy": "http://market-scan-proxy:1080"}
    assert len(out) == 20
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "S0" and item.market == "US" and item.name == "Apple Inc."
    assert item.price == 200.0 and item.change_pct == 1.23
    assert item.turnover == 999999.0 and item.volume == 1000.0


def test_fetch_hot_stocks_defaults_market(monkeypatch, tradable_all):
    """未指定 market 时使用 default_market()(US)。"""
    captured: dict = {}

    class _MD:
        def hot_stocks(self, **kw):
            captured.update(kw)
            return []

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())
    assert asyncio.run(dc.DiscoveryCollector().fetch_hot_stocks(limit=5)) == []
    assert captured["market"] == "US" and captured["limit"] == 10


def test_fetch_hot_stocks_drops_non_tradable(monkeypatch):
    """不在股票池中的代码被丢弃(is_tradable 为模块级可替换名),其余保序并切到 limit。"""
    class _MD:
        def hot_stocks(self, **kw):
            return [_md_stock(symbol="AAPL"), _md_stock(symbol="OTCX"),
                    _md_stock(symbol="MSFT"), _md_stock(symbol="NVDA")]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())
    monkeypatch.setattr(dc, "is_tradable", lambda m, s: s != "OTCX")
    out = asyncio.run(dc.DiscoveryCollector().fetch_hot_stocks(market="US", limit=2))
    assert [x.symbol for x in out] == ["AAPL", "MSFT"]


def test_fetch_hot_stocks_fails_open_when_universe_empty(monkeypatch):
    """股票池为空(目录未加载)时 universe.is_tradable 放行所有代码。"""
    class _MD:
        def hot_stocks(self, **kw):
            return [_md_stock(symbol="AAPL"), _md_stock(symbol="OTCX")]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())
    monkeypatch.setattr(dc, "is_tradable", universe.is_tradable)
    universe.reset()
    try:
        out = asyncio.run(dc.DiscoveryCollector().fetch_hot_stocks(market="US", limit=5))
    finally:
        universe.reset()
    assert [x.symbol for x in out] == ["AAPL", "OTCX"]


def test_fetch_hot_boards_uses_marketdata(monkeypatch, tradable_all):
    """fetch_hot_boards 走 marketdata 包的 hot_boards,转换为本模块 HotBoard,且透传 proxy。"""
    from marketdata.types import HotBoard as MdHotBoard

    captured: dict = {}

    class _MD:
        def hot_boards(self, *, market="US", mode="gainers", limit=12, proxy=None):
            captured.update(market=market, mode=mode, limit=limit, proxy=proxy)
            return [MdHotBoard(code="US_SECTOR_technology", name="Technology",
                               change_pct=2.5, change_amount=1.1, turnover=None)]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())

    collector = dc.DiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_hot_boards(market="US", mode="gainers", limit=12))

    assert captured == {"market": "US", "mode": "gainers", "limit": 12,
                        "proxy": "http://market-scan-proxy:1080"}
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotBoard)
    assert item.code == "US_SECTOR_technology" and item.name == "Technology"
    assert item.change_pct == 2.5 and item.change_amount == 1.1 and item.turnover is None


def test_fetch_board_stocks_uses_marketdata_and_filters(monkeypatch):
    """fetch_board_stocks 走 marketdata 包的 board_stocks(请求 limit*2),经 is_tradable 过滤,透传 proxy。"""
    captured: dict = {}

    class _MD:
        def board_stocks(self, *, board_code, mode="gainers", limit=20, proxy=None):
            captured.update(board_code=board_code, mode=mode, limit=limit, proxy=proxy)
            return [_md_stock(symbol="NVDA", name="NVIDIA", price=150.0, change_pct=3.3,
                              turnover=55555.0, volume=200.0),
                    _md_stock(symbol="JUNK")]

    monkeypatch.setattr(dc, "get_market_data", lambda: _MD())
    monkeypatch.setattr(dc, "is_tradable", lambda m, s: s != "JUNK")

    collector = dc.DiscoveryCollector(proxy="http://market-scan-proxy:1080")
    out = asyncio.run(collector.fetch_board_stocks(board_code="US_SECTOR_technology",
                                                   mode="losers", limit=20))

    assert captured == {"board_code": "US_SECTOR_technology", "mode": "losers", "limit": 40,
                        "proxy": "http://market-scan-proxy:1080"}
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, dc.HotStock)
    assert item.symbol == "NVDA" and item.market == "US" and item.name == "NVIDIA"
    assert item.price == 150.0 and item.change_pct == 3.3
    assert item.turnover == 55555.0 and item.volume == 200.0
