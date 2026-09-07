"""真实生产入口的持仓市场隔离边界回归测试(round 4)。

REVIEW-03.md 的 P1 之二:`src/agents/tradingagents/agent.py` 里真正调用
`build_portfolio_context` 的地方(`TradingAgentsAgent.analyze`)必须把当前分析标的
的市场(`stock.market.value`)当作 `stock_market=` 传进去,否则同一 symbol 在不同市场
的持仓会互相污染该标的的推荐上下文。旧的边界测试只 spy 了一个纯内存 getter,无法证明
"生产调用点真的传了市场"这件事。

本模块做两件事:
1) 用 `inspect.getsource(TradingAgentsAgent.analyze)` 直接检查真实源码——调用
   `build_portfolio_context(` 的那个代码块里必须出现 `stock_market=`。这是对"生产入口
   是否真的接线"这件事本身的证据,而不是对某个可能已经过时的行为断言。
2) 直接调用真实的 `build_portfolio_context`(不重新实现)验证市场隔离在功能上确实生效:
   同一 symbol 在 US/CA 各有仓位时,只传 `stock_market="US"` 的调用只能看到 US 那份
   仓位的数量/均价/盈亏,反之亦然;不受支持市场的仓位不会污染其他标的的
   per-instrument 数字,但总敞口仍然被完整披露。

WHY THE GETSOURCE FALLBACK(而非驱动真实 agent 入口):
`TradingAgentsAgent.analyze()` 往下会同步阻塞调用 `_run_tradingagents_sync`,那会真正
构造 TauricResearch/TradingAgents 的 `TradingAgentsGraph` 并跑一整套 LLM
debate/propagator 流程——要在这一层安全地把每一个 provider/model 调用点都
monkeypatch 掉,风险是遗漏某个内部调用路径而意外触发真实模型/网络调用,这正是本轮
"绝不允许启动真实 agent"的红线。任务说明书本身也预留了这个后备方案:
"test build_portfolio_context DIRECTLY 并额外断言 agent.py 传了 stock_market"。
因此本模块采用该后备方案，REPORT: 本文件选择了 getsource 后备方案，而不是驱动真实
agent 入口。

本模块不连网、不依赖真实数据库、不构造/运行任何 TradingAgentsGraph、不发起任何真实
model/provider 调用;所有 PositionInfo/AccountInfo/PortfolioInfo 均在内存中直接构造。
自动断网 fixture 照抄 tests/test_position_support_boundary.py 的既有先例。
"""

from __future__ import annotations

import inspect
import socket

import pytest

from src.agents.base import AccountInfo, PortfolioInfo, PositionInfo
from src.agents.tradingagents.agent import TradingAgentsAgent
from src.agents.tradingagents.portfolio_context import build_portfolio_context
from src.models.market import MarketCode


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed),照抄
    tests/test_position_support_boundary.py 的既有手法。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _make_position(
    *,
    market: MarketCode,
    market_code: str,
    symbol: str,
    cost_price: float,
    quantity: int,
    account_id: int = 1,
    account_name: str = "Test Account",
    name: str | None = None,
) -> PositionInfo:
    return PositionInfo(
        account_id=account_id,
        account_name=account_name,
        stock_id=1,
        symbol=symbol,
        name=name or symbol,
        market=market,
        cost_price=cost_price,
        quantity=quantity,
        trading_style="swing",
        market_code=market_code,
    )


# ---------------------------------------------------------------------------
# 1) 生产入口真的接线了 stock_market=(对源码本身的静态证据)
# ---------------------------------------------------------------------------


def test_agent_analyze_passes_stock_market_into_build_portfolio_context():
    """真实入口 TradingAgentsAgent.analyze 调用 build_portfolio_context 时,调用点源码
    里必须显式出现 `stock_market=`(值为 `stock.market.value`),否则同一 symbol 跨市场
    的持仓会互相污染当前标的的推荐上下文——这是 REVIEW-03.md P1 之二要求补上的接线。"""
    source = inspect.getsource(TradingAgentsAgent.analyze)

    assert "build_portfolio_context(" in source, (
        "TradingAgentsAgent.analyze 不再调用 build_portfolio_context,"
        "持仓上下文注入被移除了"
    )

    call_start = source.index("build_portfolio_context(")
    # 调用点通常在几行内结束(参数不多),截取足够长的窗口覆盖整个调用块。
    call_block = source[call_start : call_start + 400]
    assert "stock_market=" in call_block, (
        "build_portfolio_context( 调用块里没有传 stock_market=,"
        f"同一 symbol 跨市场的持仓会互相污染。调用块原文:\n{call_block}"
    )


# ---------------------------------------------------------------------------
# 2) 直接调用真实 build_portfolio_context:同 symbol 跨市场隔离在功能上确实生效
# ---------------------------------------------------------------------------


def test_same_symbol_us_ca_context_reflects_only_requested_market():
    """同一 symbol 在 US 与 CA 各有仓位:传 stock_market="US" 构建的 context 只反映
    US 那份持仓的数量/均价,绝不包含 CA 那份的数字;反之传 "CA" 时同理。直接调用真实
    build_portfolio_context(而非重新实现),验证 agent.py 传入的 stock_market 参数
    确实在功能上起到了隔离作用。"""
    symbol = "SHOP"
    us_pos = _make_position(
        market=MarketCode.US, market_code="US", symbol=symbol, cost_price=10.0, quantity=100
    )  # cost 1000
    ca_pos = _make_position(
        market=MarketCode.CA, market_code="CA", symbol=symbol, cost_price=50.0, quantity=4
    )  # cost 200

    account = AccountInfo(id=1, name="Test", available_funds=0.0, positions=[us_pos, ca_pos])
    portfolio = PortfolioInfo(accounts=[account])

    us_context = build_portfolio_context(portfolio, symbol, current_price=12.0, stock_market="US")
    ca_context = build_portfolio_context(portfolio, symbol, current_price=55.0, stock_market="CA")

    assert "Quantity: 100 shares" in us_context
    assert "Average cost: 10.00" in us_context
    assert "Quantity: 4 shares" not in us_context
    assert "Average cost: 50.00" not in us_context

    assert "Quantity: 4 shares" in ca_context
    assert "Average cost: 50.00" in ca_context
    assert "Quantity: 100 shares" not in ca_context
    assert "Average cost: 10.00" not in ca_context


def test_unsupported_holding_excluded_from_instrument_sizing_but_total_exposure_reported():
    """不受支持市场(CN)的仓位绝不能污染另一个标的(AAPL/US)的 per-instrument
    sizing/P&L 区块;但账户总览里的总持仓成本必须仍然完整包含这笔仓位的成本,并披露
    一句明确的"未纳入分析"敞口声明——不受支持不等于零风险,也不等于被悄悄丢弃。"""
    us_pos = _make_position(
        market=MarketCode.US, market_code="US", symbol="AAPL", cost_price=100.0, quantity=10
    )  # cost 1000
    cn_pos = _make_position(
        market=MarketCode.CN, market_code="CN", symbol="600519", cost_price=1500.0, quantity=2
    )  # cost 3000

    account = AccountInfo(id=1, name="Test", available_funds=500.0, positions=[us_pos, cn_pos])
    portfolio = PortfolioInfo(accounts=[account])

    context = build_portfolio_context(portfolio, "AAPL", current_price=110.0, stock_market="US")
    lowered = context.lower()

    # Holding 区块只反映 AAPL 自己的 10 股/100.00 均价,不掺入 CN 那笔仓位的数字。
    assert "Quantity: 10 shares" in context
    assert "Average cost: 100.00" in context
    assert "Quantity: 2 shares" not in context

    # 总量数字仍然完整(1000 + 3000 = 4000.00),并披露未纳入分析的敞口(3000.00)。
    assert "Total position cost basis: 4000.00" in context
    assert ("unanalysed exposure" in lowered) or ("unanalyzed exposure" in lowered)
    assert "3000.00" in context
    assert "zero risk" not in lowered
    assert "no risk" not in lowered
