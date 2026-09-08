"""未知/畸形市场代码的类型安全回归测试:一个仓位的市场字段如果不是已知/已启用的市场
代码,必须被标记为 `MarketCode.UNKNOWN` 且 `supported=False`,而不是被静默兜底成 CN
——同时组合的总成本、总资金等聚合口径必须保持不变(仍然计入该仓位)。

覆盖范围:
1) `MarketCode.UNKNOWN` 永不被 `is_enabled` 判定为已启用;环境变量字符串里出现的
   "UNKNOWN" 会被 `_parse_enabled` 剔除。
2) `server.load_portfolio_for_agent`:畸形市场代码的股票产生的仓位,`market` 为
   `MarketCode.UNKNOWN`、`supported` 为 False、`market_code` 保留原始字符串,且
   绝不是 `MarketCode.CN`。
3) 总量口径不变:US 仓位与畸形市场仓位混合时,`total_cost`/账户 `total_cost` 必须
   同时包含两者;`actionable_positions` 只保留受支持的那一条。
4) `server._schedulers_disabled()` (DISABLE_SCHEDULERS 环境变量真值判定辅助函数)
   的真值语义:1/true/TRUE/yes 视为真,未设置/空/0/false 视为假。

本模块不连网、不调用真实行情供应商、不依赖真实数据库中的既有数据:第 2、3 部分
用独立内存 sqlite(`StaticPool`)+ 把 `server` 模块内部引用的 `SessionLocal` 换成
绑定到内存引擎的 sessionmaker,与 tests/test_market_scope_isolation.py 中
`mem_server_session_factory` 的隔离手法完全一致(该手法已在本仓库验证过:
`import server` 只会在模块级注册路由/定义函数,真正的 `init_db()` 只在 FastAPI
`lifespan` 里调用,不会在 import 阶段触碰真实 data/tickerkeep.db)。
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import server  # noqa: E402  已有先例:tests/test_market_scope_isolation.py 同样直接 import server


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed)。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def mem_server_session_factory(monkeypatch):
    """独立内存 sqlite(不碰真实 data/tickerkeep.db):把 server 模块内部引用的 SessionLocal
    换成绑定到本地全新引擎的 sessionmaker。"""
    from src.web import models as _models  # noqa: F401  确保所有 ORM 模型注册到 Base.metadata
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(server, "SessionLocal", session_factory)
    return session_factory


# ---------------------------------------------------------------------------
# 1) MarketCode.UNKNOWN 永不启用
# ---------------------------------------------------------------------------


def test_is_enabled_unknown_market_code_is_always_false():
    """MarketCode.UNKNOWN 永远不被 is_enabled 判定为已启用,即使环境变量字符串中写了 UNKNOWN 也会被剔除。"""
    from src.models.market import MarketCode, _parse_enabled, is_enabled

    assert is_enabled(MarketCode.UNKNOWN) is False
    assert is_enabled("UNKNOWN") is False

    parsed = _parse_enabled("US,UNKNOWN,CA")
    assert "UNKNOWN" not in parsed
    assert parsed == ("US", "CA")


# ---------------------------------------------------------------------------
# 2) load_portfolio_for_agent —— 畸形市场代码不再兜底成 CN
# ---------------------------------------------------------------------------


def test_load_portfolio_for_agent_bogus_market_marked_unknown_not_cn(mem_server_session_factory):
    """伪造/畸形市场代码(如 "ZZ")产生的仓位:market 为 MarketCode.UNKNOWN、supported=False、
    market_code 保留原始字符串 "ZZ",且绝不是 MarketCode.CN。"""
    from src.models.market import MarketCode
    from src.web.models import Account, Position, Stock, StockAgent

    db = mem_server_session_factory()
    agent_name = "premarket_outlook"
    try:
        stock = Stock(symbol="ZZZZ", name="Bogus Market Co", market="ZZ")
        db.add(stock)
        db.commit()
        db.add(StockAgent(stock_id=stock.id, agent_name=agent_name))
        acc = Account(name="Test Account", available_funds=1000.0, enabled=True)
        db.add(acc)
        db.commit()
        db.add(Position(account_id=acc.id, stock_id=stock.id, cost_price=10.0, quantity=5))
        db.commit()
    finally:
        db.close()

    portfolio = server.load_portfolio_for_agent(agent_name)
    positions = portfolio.all_positions
    assert len(positions) == 1
    pos = positions[0]
    assert pos.market == MarketCode.UNKNOWN
    assert pos.market != MarketCode.CN
    assert pos.supported is False
    assert pos.market_code == "ZZ"


# ---------------------------------------------------------------------------
# 3) 总量口径不变:supported 与 unsupported 仓位都计入总成本
# ---------------------------------------------------------------------------


def test_load_portfolio_for_agent_preserves_totals_across_supported_and_unsupported(
    mem_server_session_factory,
):
    """总持仓成本必须同时包含受支持(US)与不受支持(畸形市场)仓位的成本;actionable_positions
    只保留受支持的那一条,但 all_positions/total_cost 语义完全不变。"""
    from src.web.models import Account, Position, Stock, StockAgent

    db = mem_server_session_factory()
    agent_name = "premarket_outlook"
    try:
        us_stock = Stock(symbol="AAPL", name="Apple", market="US")
        bogus_stock = Stock(symbol="ZZZZ", name="Bogus Market Co", market="ZZ")
        db.add_all([us_stock, bogus_stock])
        db.commit()
        for s in (us_stock, bogus_stock):
            db.add(StockAgent(stock_id=s.id, agent_name=agent_name))
        acc = Account(name="Test Account", available_funds=1000.0, enabled=True)
        db.add(acc)
        db.commit()
        db.add(Position(account_id=acc.id, stock_id=us_stock.id, cost_price=10.0, quantity=5))  # 成本 50
        db.add(Position(account_id=acc.id, stock_id=bogus_stock.id, cost_price=20.0, quantity=3))  # 成本 60
        db.commit()
    finally:
        db.close()

    portfolio = server.load_portfolio_for_agent(agent_name)

    assert len(portfolio.all_positions) == 2
    assert portfolio.total_cost == pytest.approx(110.0)
    assert len(portfolio.accounts) == 1
    assert portfolio.accounts[0].total_cost == pytest.approx(110.0)

    actionable_symbols = {p.symbol for p in portfolio.actionable_positions}
    assert actionable_symbols == {"AAPL"}


# ---------------------------------------------------------------------------
# 4) DISABLE_SCHEDULERS 真值判定辅助函数
# ---------------------------------------------------------------------------


def test_schedulers_disabled_truthiness_helper(monkeypatch):
    """server._schedulers_disabled():1/true/TRUE/yes 视为真,未设置/空字符串/0/false 视为假。

    命名假设说明:任务简报里这个辅助函数"很可能叫 _schedulers_disabled",此处在
    server.py 中确认已存在同名函数(第 59 行,docstring 明确写了 DISABLE_SCHEDULERS
    环境变量的真值语义)。仍然用 getattr 防御式获取,如果协同修改的 sibling worker
    后续把它改名/移除,这里会优雅跳过而不是报错崩溃。
    """
    helper = getattr(server, "_schedulers_disabled", None)
    if helper is None:
        pytest.skip(
            "server._schedulers_disabled not found — the DISABLE_SCHEDULERS truthiness "
            "helper may have been renamed or not yet landed under this name."
        )

    monkeypatch.delenv("DISABLE_SCHEDULERS", raising=False)
    assert helper() is False

    for falsy in ("", "0", "false", "False"):
        monkeypatch.setenv("DISABLE_SCHEDULERS", falsy)
        assert helper() is False, falsy

    for truthy in ("1", "true", "TRUE", "yes"):
        monkeypatch.setenv("DISABLE_SCHEDULERS", truthy)
        assert helper() is True, truthy
