"""市场范围隔离回归测试:禁用市场(CN/HK)不应压制启用市场(US/CA)的正常读写路径。

覆盖四层:
1) 批量报价接口 POST /api/quotes/batch —— 混合批量、全禁用批量、未知/空市场代码、
   单市场抓取异常不应连累其他市场。
2) 策略信号读路径 list_strategy_signals —— 未指定 market 时按 ENABLED_MARKETS 过滤，
   显式传入禁用市场时返回空结果。
3) Agent 调度输入 load_watchlist_for_agent —— 跳过市场无效/未启用的自选股，不再把
   未知市场兜底成 CN。

本模块不连网、不调用真实行情供应商、不依赖真实数据库中的既有数据：
- 第 1 部分只搭建裸 FastAPI(仅挂 quotes 路由)+ TestClient，并 monkeypatch 掉
  `src.web.api.quotes.md_quote_rows`（quotes.py 内该名称的导入点）。
- 第 2、3 部分把 `src.core.strategy_engine` / `src.core.strategy_catalog` / `server`
  模块内部引用的 `SessionLocal` 替换成绑定到全新内存 sqlite 引擎的 sessionmaker
  （与 tests/test_discovery_api.py、tests/test_factor_calibration_loop.py、
  tests/test_datasource_reconcile.py 中"独立内存 sqlite，不碰真实 data/tickerkeep.db"
  的隔离手法一致）。

准确说明：本模块确实会 `from src.web.database import Base`（仅为拿到 ORM 元数据建表），
该导入会让 `src/web/database.py` 在模块级构造 `engine` 对象，但 SQLAlchemy 的
`create_engine` 是惰性的——不建立连接、不创建也不写入 `data/tickerkeep.db`。真正被查询
的 `SessionLocal` 已全部换成绑定内存引擎的 sessionmaker。请注意 `tests/conftest.py`
的 session 级 `_ensure_db_schema` fixture 会对"当前源码树相对路径"下的 db 执行
`create_all`，因此产品迁移类验证应在一次性副本中运行（见 docs/product/PROGRESS.md）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.quotes as quotes


# ---------------------------------------------------------------------------
# 1) POST /api/quotes/batch —— 禁用市场不应压制启用市场的正常报价
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """裸 FastAPI + quotes 路由(无 DB 依赖,不引入真实 app/中间件)。"""
    app = FastAPI()
    app.include_router(quotes.router, prefix="/api/quotes")
    return TestClient(app)


def test_batch_mixed_disabled_market_does_not_suppress_enabled_quotes(monkeypatch, client):
    """核心 P0 回归:混合批量(CN 遗留代码 + US/CA 有效代码)中,CN 被禁用不应压制 US/CA 的正常报价,且响应保持请求顺序。"""
    calls: list[tuple[tuple, str]] = []

    def _fake_md_quote_rows(symbols, market):
        calls.append((tuple(symbols), market))
        return [{"symbol": s, "name": f"{s} Inc", "current_price": 100.0 + i}
                for i, s in enumerate(symbols)]

    monkeypatch.setattr(quotes, "md_quote_rows", _fake_md_quote_rows)

    payload = {
        "items": [
            {"symbol": "600519", "market": "CN"},
            {"symbol": "AAPL", "market": "US"},
            {"symbol": "SHOP.TO", "market": "CA"},
        ]
    }
    r = client.post("/api/quotes/batch", json=payload)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["symbol"] for row in rows] == ["600519", "AAPL", "SHOP.TO"]

    cn_row, us_row, ca_row = rows
    assert cn_row["supported"] is False
    assert cn_row["unsupported_reason"] == "market_not_enabled"
    assert cn_row["market"] == "CN"
    assert cn_row["current_price"] is None

    assert us_row["supported"] is True
    assert us_row["current_price"] is not None
    assert ca_row["supported"] is True
    assert ca_row["current_price"] is not None

    fetched_markets = {m for _, m in calls}
    assert "CN" not in fetched_markets
    assert {"US", "CA"} <= fetched_markets


def test_batch_all_disabled_markets_never_calls_fetch(monkeypatch, client):
    """全部禁用市场(CN、HK)的批量请求:仍返回 200 和两条 unsupported 行,行情抓取函数从未被调用(0 次)。"""
    calls: list[tuple] = []

    def _spy(symbols, market):
        calls.append((symbols, market))
        return []

    monkeypatch.setattr(quotes, "md_quote_rows", _spy)

    payload = {
        "items": [
            {"symbol": "600519", "market": "CN"},
            {"symbol": "00700", "market": "HK"},
        ]
    }
    r = client.post("/api/quotes/batch", json=payload)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2
    assert all(row["supported"] is False for row in rows)
    assert all(row["unsupported_reason"] == "market_not_enabled" for row in rows)
    assert all(row["current_price"] is None for row in rows)
    assert len(calls) == 0


def test_batch_unknown_market_code_and_empty_string_default(monkeypatch, client):
    """未知/畸形市场代码(如 ZZ)标记为 unknown_market;空字符串市场归一化为默认市场且视为 supported。"""
    from src.models.market import default_market

    def _fake_md_quote_rows(symbols, market):
        return [{"symbol": s, "name": f"{s} Inc", "current_price": 50.0} for s in symbols]

    monkeypatch.setattr(quotes, "md_quote_rows", _fake_md_quote_rows)

    payload = {
        "items": [
            {"symbol": "FAKE", "market": "ZZ"},
            {"symbol": "AAPL", "market": ""},
        ]
    }
    r = client.post("/api/quotes/batch", json=payload)
    assert r.status_code == 200, r.text
    rows = r.json()
    zz_row, empty_row = rows

    assert zz_row["supported"] is False
    assert zz_row["unsupported_reason"] == "unknown_market"
    assert zz_row["market"] == "ZZ"
    assert zz_row["current_price"] is None

    assert empty_row["supported"] is True
    assert empty_row["unsupported_reason"] is None
    assert empty_row["market"] == default_market()
    assert empty_row["current_price"] is not None


def test_batch_one_enabled_market_fetch_failure_does_not_abort_other_market(monkeypatch, client):
    """一个启用市场(CA)的行情抓取异常,不应中断另一个启用市场(US)的正常返回;CA 行降级为 supported=True 但 current_price=None。"""

    def _fake_md_quote_rows(symbols, market):
        if market == "CA":
            raise RuntimeError("provider down")
        return [{"symbol": s, "name": f"{s} Inc", "current_price": 200.0} for s in symbols]

    monkeypatch.setattr(quotes, "md_quote_rows", _fake_md_quote_rows)

    payload = {
        "items": [
            {"symbol": "AAPL", "market": "US"},
            {"symbol": "SHOP.TO", "market": "CA"},
        ]
    }
    r = client.post("/api/quotes/batch", json=payload)
    assert r.status_code == 200, r.text
    us_row, ca_row = r.json()

    assert us_row["supported"] is True
    assert us_row["current_price"] is not None

    assert ca_row["supported"] is True
    assert ca_row["current_price"] is None


# ---------------------------------------------------------------------------
# 2) list_strategy_signals —— 发现榜单读路径按 ENABLED_MARKETS 隔离
# ---------------------------------------------------------------------------

import src.core.strategy_catalog as strategy_catalog  # noqa: E402
import src.core.strategy_engine as strategy_engine  # noqa: E402
from src.models.market import ENABLED_MARKETS  # noqa: E402


@pytest.fixture
def mem_session_factory(monkeypatch):
    """独立内存 sqlite(不碰真实 data/tickerkeep.db):把 strategy_engine/strategy_catalog
    模块内部引用的 SessionLocal 换成绑定到本地全新引擎的 sessionmaker。"""
    from src.web import models as _models  # noqa: F401  确保所有 ORM 模型注册到 Base.metadata
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(strategy_engine, "SessionLocal", session_factory)
    monkeypatch.setattr(strategy_catalog, "SessionLocal", session_factory)
    return session_factory


def _seed_signal_run(db, *, symbol, market, snapshot, strategy_code="trend_follow"):
    from src.web.models import StrategySignalRun

    db.add(
        StrategySignalRun(
            snapshot_date=snapshot,
            stock_symbol=symbol,
            stock_market=market,
            strategy_code=strategy_code,
            score=50.0,
            rank_score=50.0,
        )
    )


def test_list_strategy_signals_no_market_filters_to_enabled_markets(mem_session_factory):
    """未指定 market 时,list_strategy_signals 在混合 US/CA/CN/HK 快照里只返回 stock_market 属于 ENABLED_MARKETS 的行。"""
    db = mem_session_factory()
    snapshot = "2026-09-01"
    try:
        for i, mkt in enumerate(("US", "CA", "CN", "HK")):
            _seed_signal_run(db, symbol=f"SIG{i}", market=mkt, snapshot=snapshot)
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="", snapshot_date=snapshot)
    markets = {item["stock_market"] for item in res["items"]}
    assert markets <= set(ENABLED_MARKETS)
    assert markets == {"US", "CA"}
    assert res["count"] == 2


def test_list_strategy_signals_explicit_disabled_market_returns_empty(mem_session_factory):
    """显式传入已禁用市场(CN)时,list_strategy_signals 返回 count=0 / items=[] 的空结果形状。"""
    db = mem_session_factory()
    snapshot = "2026-09-01"
    try:
        _seed_signal_run(db, symbol="SIGCN", market="CN", snapshot=snapshot)
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="CN", snapshot_date=snapshot)
    assert res["count"] == 0
    assert res["items"] == []


# ---------------------------------------------------------------------------
# 3) server.load_watchlist_for_agent —— 调度输入跳过无效/禁用市场,不再兜底成 CN
# ---------------------------------------------------------------------------

import server  # noqa: E402  已有先例:tests/test_datasource_reconcile.py 同样直接 import server


@pytest.fixture
def mem_server_session_factory(monkeypatch):
    """独立内存 sqlite(不碰真实 data/tickerkeep.db):把 server 模块内部引用的 SessionLocal
    换成绑定到本地全新引擎的 sessionmaker。"""
    from src.web import models as _models  # noqa: F401
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(server, "SessionLocal", session_factory)
    return session_factory


def test_load_watchlist_for_agent_skips_disabled_and_unknown_markets(mem_server_session_factory):
    """load_watchlist_for_agent 跳过市场无效(未知代码)或未启用(CN)的自选股,只保留启用市场(US)的股票,且不再把未知市场兜底成 CN。"""
    from src.models.market import MarketCode
    from src.web.models import Stock, StockAgent

    db = mem_server_session_factory()
    agent_name = "premarket_outlook"
    try:
        us_stock = Stock(symbol="AAPL", name="Apple", market="US")
        cn_stock = Stock(symbol="600519", name="Moutai", market="CN")
        xx_stock = Stock(symbol="ZZZZ", name="Unknown Market", market="XX")
        db.add_all([us_stock, cn_stock, xx_stock])
        db.commit()

        for s in (us_stock, cn_stock, xx_stock):
            db.add(StockAgent(stock_id=s.id, agent_name=agent_name))
        db.commit()
    finally:
        db.close()

    result = server.load_watchlist_for_agent(agent_name)

    assert [c.symbol for c in result] == ["AAPL"]
    assert result[0].market == MarketCode.US
    assert not any(c.symbol == "600519" for c in result)
    assert not any(c.symbol == "ZZZZ" for c in result)
    assert not any(c.market == MarketCode.CN for c in result)
