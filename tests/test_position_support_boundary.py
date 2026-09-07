"""持仓支持边界回归测试:`PositionInfo.supported` 必须是只读计算属性(由 `market_code`
或 `market` 经 `is_enabled` 派生),不能通过构造函数传入;`PortfolioInfo` 的按标的查询
辅助方法（`get_positions_for_stock` / `get_aggregated_position` / `has_position`）必须
支持按市场隔离同名标的，且默认只返回可操作(actionable)仓位；总量口径（`total_cost`、
`total_available_funds`、`all_positions`）必须始终包含不受支持的仓位；`PortfolioInfo`
新增的 `unsupported_positions` / `unsupported_cost` 必须精确反映被排除在外的仓位；
不受支持的仓位在按标的查询时绝不应触发任何真实行情/AI/交易调用。

覆盖范围:
1) 直接构造 `PositionInfo`：`market_code="CN"`/`"ZZ"` 在零显式标记的情况下 fail-closed
   为 `supported=False`；`US` 为 `True`；`supported=` 不能作为构造参数传入
   （`pytest.raises(TypeError)`）。
2) 总量口径不变：US/CA/CN/未知混合组合中，`total_cost`、账户 `total_cost`、
   `total_available_funds`、`len(all_positions)` 包含全部仓位；`actionable_positions`
   只保留 US/CA；`unsupported_cost` 精确等于被排除仓位成本之和，且
   actionable 成本之和 + `unsupported_cost` == `total_cost`。
3) 同名标的跨市场隔离：US 与 CA 各持有同一 symbol 时互不污染。
4) 不受支持市场（CN）从按标的推断输入中被排除，但 `actionable_only=False` 仍能取回。
5) 不受支持仓位的按标的查询绝不触发任何真实行情供应商调用
   （monkeypatch `src.core.marketdata_client.md_quote_rows` 为立即抛错的 spy）。
6) `build_portfolio_context`（`src/agents/tradingagents/portfolio_context.py`）渲染出的
   文本，在存在不受支持仓位时，总量数字仍完整，且包含明确的"未纳入分析"声明，绝不能
   把这部分敞口描述成现金或零风险。

本模块不连网、不调用真实行情供应商、不依赖真实数据库：所有 PositionInfo/AccountInfo/
PortfolioInfo 都直接在内存中构造，绝不 import `src.web.database` / `SessionLocal`。
自动断网 fixture 与断网手法照抄 tests/test_market_scope_unknown.py /
tests/test_market_scope_isolation.py 的既有先例。
"""

from __future__ import annotations

import socket

import pytest

from src.agents.base import AccountInfo, PortfolioInfo, PositionInfo
from src.models.market import MarketCode


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed)。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _make_position(
    *,
    account_id: int = 1,
    account_name: str = "Test Account",
    stock_id: int = 1,
    symbol: str = "AAPL",
    name: str = "Apple",
    market: MarketCode = MarketCode.US,
    market_code: str = "",
    cost_price: float = 10.0,
    quantity: int = 5,
    trading_style: str = "swing",
) -> PositionInfo:
    return PositionInfo(
        account_id=account_id,
        account_name=account_name,
        stock_id=stock_id,
        symbol=symbol,
        name=name,
        market=market,
        cost_price=cost_price,
        quantity=quantity,
        trading_style=trading_style,
        market_code=market_code,
    )


# ---------------------------------------------------------------------------
# 1) supported 是只读计算属性,fail-closed
# ---------------------------------------------------------------------------


def test_supported_is_fail_closed_computed_property_for_unenabled_or_bogus_market():
    """直接构造 PositionInfo(market_code="CN") 与 market_code="ZZ" 时,即使没有任何人显式传
    supported 标记,supported 也必须 fail-closed 为 False;market_code="US" 则为 True。"""
    cn_pos = _make_position(symbol="600519", market=MarketCode.CN, market_code="CN")
    zz_pos = _make_position(symbol="ZZZZ", market=MarketCode.UNKNOWN, market_code="ZZ")
    us_pos = _make_position(symbol="AAPL", market=MarketCode.US, market_code="US")

    assert cn_pos.supported is False
    assert zz_pos.supported is False
    assert us_pos.supported is True


def test_supported_cannot_be_set_via_constructor():
    """PositionInfo(..., supported=True) 必须直接 TypeError——supported 不是一个可写字段。"""
    with pytest.raises(TypeError):
        PositionInfo(
            account_id=1,
            account_name="Test Account",
            stock_id=1,
            symbol="AAPL",
            name="Apple",
            market=MarketCode.US,
            cost_price=10.0,
            quantity=5,
            market_code="US",
            supported=True,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# 2) 总量口径不变 + unsupported_cost 精确
# ---------------------------------------------------------------------------


def test_totals_preserved_across_supported_and_unsupported_positions():
    """US + CA + CN + 未知市场混合组合:total_cost/账户 total_cost/total_available_funds/
    len(all_positions) 必须包含全部仓位;actionable_positions 只保留 US/CA;
    unsupported_cost 精确等于被排除仓位成本之和,且 actionable 成本之和 + unsupported_cost
    == total_cost。"""
    us_pos = _make_position(
        symbol="AAPL", market=MarketCode.US, market_code="US", cost_price=10.0, quantity=5
    )  # cost 50
    ca_pos = _make_position(
        symbol="SHOP.TO", market=MarketCode.CA, market_code="CA", cost_price=30.0, quantity=2
    )  # cost 60
    cn_pos = _make_position(
        symbol="600519", market=MarketCode.CN, market_code="CN", cost_price=20.0, quantity=3
    )  # cost 60
    zz_pos = _make_position(
        symbol="ZZZZ", market=MarketCode.UNKNOWN, market_code="ZZ", cost_price=5.0, quantity=4
    )  # cost 20

    account = AccountInfo(
        id=1,
        name="Test Account",
        available_funds=1000.0,
        positions=[us_pos, ca_pos, cn_pos, zz_pos],
    )
    portfolio = PortfolioInfo(accounts=[account])

    assert len(portfolio.all_positions) == 4
    assert portfolio.total_cost == pytest.approx(50.0 + 60.0 + 60.0 + 20.0)
    assert portfolio.accounts[0].total_cost == pytest.approx(190.0)
    assert portfolio.total_available_funds == pytest.approx(1000.0)

    actionable_symbols = {p.symbol for p in portfolio.actionable_positions}
    assert actionable_symbols == {"AAPL", "SHOP.TO"}
    actionable_cost = sum(p.cost_value for p in portfolio.actionable_positions)
    assert actionable_cost == pytest.approx(110.0)

    unsupported_cost = portfolio.unsupported_cost
    unsupported_symbols = {p.symbol for p in portfolio.unsupported_positions}
    assert unsupported_symbols == {"600519", "ZZZZ"}
    assert unsupported_cost == pytest.approx(80.0)

    assert actionable_cost + unsupported_cost == pytest.approx(portfolio.total_cost)


# ---------------------------------------------------------------------------
# 3) 同名标的跨市场隔离
# ---------------------------------------------------------------------------


def test_same_symbol_cross_market_isolation():
    """同一 symbol 在 US 与 CA 各持有一份:get_positions_for_stock/get_aggregated_position
    按 market 精确隔离,has_position 对无持仓的市场返回 False。"""
    symbol = "SHOP"
    us_pos = _make_position(
        symbol=symbol, market=MarketCode.US, market_code="US", cost_price=10.0, quantity=5
    )  # cost 50
    ca_pos = _make_position(
        symbol=symbol, market=MarketCode.CA, market_code="CA", cost_price=30.0, quantity=2
    )  # cost 60

    account = AccountInfo(id=1, name="Test Account", available_funds=1000.0, positions=[us_pos, ca_pos])
    portfolio = PortfolioInfo(accounts=[account])

    us_only = portfolio.get_positions_for_stock(symbol, "US")
    assert len(us_only) == 1
    assert us_only[0].market == MarketCode.US

    aggregated = portfolio.get_aggregated_position(symbol, "US")
    assert aggregated is not None
    assert aggregated["total_quantity"] == 5
    assert aggregated["total_cost"] == pytest.approx(50.0)

    assert portfolio.has_position(symbol, "CA") is True
    assert portfolio.has_position(symbol, "GOLD") is False
    assert portfolio.has_position("NOPE", "US") is False


# ---------------------------------------------------------------------------
# 4) 不受支持市场从推断输入中排除,但 actionable_only=False 仍能取回
# ---------------------------------------------------------------------------


def test_unsupported_position_excluded_from_inference_input_by_default():
    """CN 仓位默认 (actionable_only=True) 从 get_positions_for_stock 中被排除,
    has_position 为 False;actionable_only=False 时仍能取回该记录,不丢失数据。"""
    symbol = "600519"
    cn_pos = _make_position(symbol=symbol, market=MarketCode.CN, market_code="CN")
    account = AccountInfo(id=1, name="Test Account", available_funds=0.0, positions=[cn_pos])
    portfolio = PortfolioInfo(accounts=[account])

    assert portfolio.get_positions_for_stock(symbol, "CN") == []
    assert portfolio.has_position(symbol, "CN") is False

    full = portfolio.get_positions_for_stock(symbol, "CN", actionable_only=False)
    assert len(full) == 1
    assert full[0].symbol == symbol
    assert portfolio.has_position(symbol, "CN", actionable_only=False) is True


# ---------------------------------------------------------------------------
# 5) 不受支持仓位的按标的查询绝不触发真实行情供应商调用
# ---------------------------------------------------------------------------


def test_unsupported_position_lookup_never_calls_market_data_provider(monkeypatch):
    """CN 仓位的 get_positions_for_stock/get_aggregated_position/has_position 调用,
    绝不应触发 src.core.marketdata_client.md_quote_rows(纯内存筛选,无需拉取行情)。"""
    import src.core.marketdata_client as marketdata_client

    def _spy(*args, **kwargs):
        raise AssertionError("md_quote_rows must never be called for unsupported positions")

    monkeypatch.setattr(marketdata_client, "md_quote_rows", _spy)

    symbol = "600519"
    cn_pos = _make_position(symbol=symbol, market=MarketCode.CN, market_code="CN")
    account = AccountInfo(id=1, name="Test Account", available_funds=0.0, positions=[cn_pos])
    portfolio = PortfolioInfo(accounts=[account])

    # 默认 actionable_only=True 路径
    assert portfolio.get_positions_for_stock(symbol, "CN") == []
    assert portfolio.get_aggregated_position(symbol, "CN") is None
    assert portfolio.has_position(symbol, "CN") is False

    # actionable_only=False 路径同样不应触发行情调用
    assert len(portfolio.get_positions_for_stock(symbol, "CN", actionable_only=False)) == 1
    assert portfolio.get_aggregated_position(symbol, "CN", actionable_only=False) is not None
    assert portfolio.has_position(symbol, "CN", actionable_only=False) is True


# ---------------------------------------------------------------------------
# 6) build_portfolio_context 渲染:总量完整 + 明确的未纳入分析声明
# ---------------------------------------------------------------------------


def test_build_portfolio_context_reports_complete_totals_and_unanalysed_exposure():
    """存在不受支持(CN)仓位时,build_portfolio_context 渲染的文本:
    (a) 仍然报告包含该仓位在内的完整总持仓成本数字;
    (b) 包含一句明确的"未纳入分析的敞口"声明;
    (c) 绝不能把这部分敞口描述成现金或零风险。

    NOTE(worker C): 本测试键定的措辞是大小写不敏感的子串 "unanalysed"（或英式拼写
    "unanalyzed"）/"excluded" 搭配 "exposure"/"position"/"holding" 类词，以及一定要出现
    完整持仓成本的数字文本。sibling worker 若最终选用略有出入的具体句子，只要仍然明确
    传达"这部分仓位没有被本次分析覆盖"的语义，这个宽松匹配应该仍然通过；但如果该声明
    整个消失，这个测试必须失败。"""
    from src.agents.tradingagents.portfolio_context import build_portfolio_context

    us_pos = _make_position(
        symbol="AAPL", market=MarketCode.US, market_code="US", cost_price=10.0, quantity=5
    )  # cost 50
    cn_pos = _make_position(
        symbol="600519",
        name="Moutai",
        market=MarketCode.CN,
        market_code="CN",
        cost_price=20.0,
        quantity=3,
    )  # cost 60

    account = AccountInfo(
        id=1, name="Test Account", available_funds=1000.0, positions=[us_pos, cn_pos]
    )
    portfolio = PortfolioInfo(accounts=[account])

    text = build_portfolio_context(portfolio, "AAPL", current_price=15.0)
    lowered = text.lower()

    # (a) 完整总量数字仍然出现(50 + 60 = 110.00)
    total_cost = portfolio.total_cost
    assert total_cost == pytest.approx(110.0)
    assert f"{total_cost:.2f}" in text

    # (b) 明确的"未纳入分析"声明:宽松匹配未分析/排除类措辞 + 敞口/仓位类名词
    mentions_unanalysed = ("unanalysed" in lowered) or ("unanalyzed" in lowered) or (
        "excluded" in lowered
    ) or ("not analyz" in lowered) or ("not include" in lowered and "analys" in lowered)
    mentions_exposure_noun = any(
        word in lowered for word in ("exposure", "position", "holding")
    )
    assert mentions_unanalysed and mentions_exposure_noun, (
        "expected an explicit unanalysed-exposure statement in the rendered "
        f"portfolio context, got:\n{text}"
    )

    # (c) 绝不能把这部分敞口描述成现金或零风险
    assert "zero risk" not in lowered
    assert "no risk" not in lowered
    # 不应该把这部分仓位说成是现金(那是完全不同的资产类别语义)
    forbidden_cash_phrases = ("treated as cash", "counted as cash", "is cash", "as cash")
    assert not any(phrase in lowered for phrase in forbidden_cash_phrases)
