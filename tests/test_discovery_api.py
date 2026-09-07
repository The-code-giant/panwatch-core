"""发现 API(/api/discovery):losers 模式、US_SECTOR_ 路由到 vendor、CA_ 合成板块、未知代码 404。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.discovery as discovery
from src.collectors.discovery_collector import HotBoard, HotStock
from src.web import models as M  # noqa: F401  确保模型注册到 Base.metadata
from src.web.database import Base, get_db


def _stock(symbol: str, market: str, change_pct: float, turnover: float) -> HotStock:
    return HotStock(symbol=symbol, market=market, name=f"{symbol} Inc", price=10.0,
                    change_pct=change_pct, turnover=turnover, volume=turnover / 10.0)


class FakeCollector:
    """记录调用并返回固定数据的采集器替身。"""

    calls: list[tuple] = []
    boards: dict[str, list[HotBoard]] = {}
    sector_rows: list[HotStock] = []

    def __init__(self, *, proxy=None):
        self.proxy = proxy

    async def fetch_hot_stocks(self, *, market="", mode="turnover", limit=20):
        FakeCollector.calls.append(("hot_stocks", market, mode, limit))
        rows = [
            _stock("AAA", market, 5.0, 100.0),
            _stock("BBB", market, -3.0, 300.0),
            _stock("CCC", market, 1.0, 200.0),
        ]
        if mode == "losers":
            rows = sorted(rows, key=lambda r: r.change_pct)
        return rows[:limit]

    async def fetch_hot_boards(self, *, market="", mode="gainers", limit=12):
        FakeCollector.calls.append(("hot_boards", market, mode, limit))
        return list(FakeCollector.boards.get(market, []))[:limit]

    async def fetch_board_stocks(self, *, board_code, mode="gainers", limit=20):
        FakeCollector.calls.append(("board_stocks", board_code, mode, limit))
        return list(FakeCollector.sector_rows)[:limit]


@pytest.fixture
def client(monkeypatch):
    """内存 SQLite + 假采集器 + 清空模块缓存的 TestClient。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(discovery.router, prefix="/api/discovery")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    FakeCollector.calls = []
    FakeCollector.boards = {
        "US": [HotBoard(code="US_SECTOR_technology", name="Technology", change_pct=1.2,
                        change_amount=0.5, turnover=None)],
        "CA": [],
    }
    FakeCollector.sector_rows = [_stock("NVDA", "US", 4.0, 900.0), _stock("AAPL", "US", 2.0, 800.0)]
    monkeypatch.setattr(discovery, "DiscoveryCollector", FakeCollector)
    monkeypatch.setattr(discovery, "_resolve_proxy", lambda: "")
    discovery._cache.clear()
    yield TestClient(app)
    discovery._cache.clear()


def test_hot_stocks_accepts_losers(client):
    """GET /stocks?mode=losers 返回 200,模式原样透传给采集器,结果按跌幅排序。"""
    r = client.get("/api/discovery/stocks", params={"market": "US", "mode": "losers", "limit": 5})
    assert r.status_code == 200, r.text
    assert ("hot_stocks", "US", "losers", 5) in FakeCollector.calls
    assert [x["symbol"] for x in r.json()] == ["BBB", "CCC", "AAA"]
    assert all(x["market"] == "US" for x in r.json())


def test_hot_stocks_rejects_unknown_mode(client):
    """不支持的 mode 返回 400。"""
    r = client.get("/api/discovery/stocks", params={"market": "US", "mode": "weird"})
    assert r.status_code == 400


def test_boards_vendor_for_us_synthetic_for_ca(client):
    """/boards:US 返回 vendor 的 US_SECTOR_ 板块;CA 无 vendor 板块时回退为合成 CA_ 桶。"""
    us = client.get("/api/discovery/boards", params={"market": "US", "mode": "losers"})
    assert us.status_code == 200, us.text
    assert [b["code"] for b in us.json()] == ["US_SECTOR_technology"]
    assert ("hot_boards", "US", "losers", 12) in FakeCollector.calls

    ca = client.get("/api/discovery/boards", params={"market": "CA", "mode": "gainers"})
    assert ca.status_code == 200, ca.text
    codes = [b["code"] for b in ca.json()]
    assert codes and all(c.startswith("CA_") for c in codes)
    assert "CA_GAINERS" in codes and "CA_TURNOVER" in codes
    assert ("hot_boards", "CA", "gainers", 12) in FakeCollector.calls
    assert ("hot_stocks", "CA", "gainers", 120) in FakeCollector.calls


def test_board_stocks_sector_code_routed_to_vendor(client):
    """US_SECTOR_technology 交给 vendor 的 board_stocks,市场来自代码前缀,mode=losers 透传。"""
    r = client.get("/api/discovery/boards/US_SECTOR_technology/stocks",
                   params={"mode": "losers", "limit": 10})
    assert r.status_code == 200, r.text
    assert ("board_stocks", "US_SECTOR_technology", "losers", 10) in FakeCollector.calls
    assert [x["symbol"] for x in r.json()] == ["NVDA", "AAPL"]
    assert all(x["market"] == "US" for x in r.json())
    assert not any(c[0] == "hot_stocks" for c in FakeCollector.calls)


def test_board_stocks_synthetic_ca_bucket(client):
    """CA_GAINERS 为合成桶:从 CA 热门池按涨幅排序,不调用 vendor 的 board_stocks。"""
    r = client.get("/api/discovery/boards/CA_GAINERS/stocks", params={"limit": 10})
    assert r.status_code == 200, r.text
    assert [x["symbol"] for x in r.json()] == ["AAA", "CCC", "BBB"]
    assert all(x["market"] == "CA" for x in r.json())
    assert any(c[0] == "hot_stocks" and c[1] == "CA" for c in FakeCollector.calls)
    assert not any(c[0] == "board_stocks" for c in FakeCollector.calls)


def test_board_stocks_unknown_code_404(client):
    """非 {MKT}_ 前缀(如遗留东财 BK 代码、已退役市场)的板块代码返回 404。"""
    for code in ("BK0500", "CN_SECTOR_bank", "HK_GAINERS", "XX_SECTOR_technology"):
        r = client.get(f"/api/discovery/boards/{code}/stocks")
        assert r.status_code == 404, code
    assert not any(c[0] == "board_stocks" for c in FakeCollector.calls)


def test_board_stocks_vendor_failure_503(client, monkeypatch):
    """vendor 板块成分抓取异常时返回 503(英文提示)。"""
    async def _boom(self, *, board_code, mode="gainers", limit=20):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(FakeCollector, "fetch_board_stocks", _boom)
    r = client.get("/api/discovery/boards/US_SECTOR_energy/stocks")
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]
