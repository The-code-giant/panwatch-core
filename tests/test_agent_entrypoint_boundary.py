"""真实生产入口(analyze/_run_tradingagents_sync)的 mocked 边界回归测试(round 5)。

REVIEW-04.md §C 明确拒绝 `tests/test_agent_portfolio_boundary.py` 里用
`inspect.getsource(TradingAgentsAgent.analyze)` 做字符串子串断言来"证明"生产入口安全——
那只证明了源码长得像接了线,证明不了真的执行到那条接线时行为正确、也证明不了
不受支持的市场真的会在触碰 provider/model 之前退出。

本模块改为直接驱动真实的 `TradingAgentsAgent.analyze()`(生产入口,BaseAgent.run() 内部
真正调用的那个),并把 `_run_tradingagents_sync`、AIClient 的全部模型调用方法、
collector/provider 入口 (`get_market_data`)、以及 analyze() 路径上会触碰真实数据库的
每一个调用点 (`check_budget` / `get_analysis` / `save_analysis` / `save_suggestion` /
`_collect_toolkit_diagnostic` / `analysis_detail_markdown`) 全部在调用 analyze() 之前
用 monkeypatch 换成 spy/mock。`build_portfolio_context` 则用 `MagicMock(wraps=...)`
包一层真实实现,既记录调用参数、又保留功能性验证(该模块不重新实现它)。

覆盖两件事(对应 REVIEW-04.md §C1/§C2):
1. 同一 symbol 在 US/CA 各有仓位时,`analyze()` 真的把"当前分析标的自己的市场"
   (`stock.market.value`)当作 `stock_market=` 传给 `build_portfolio_context`,US 那次
   分析拿到的 portfolio 上下文只反映 US 仓位,CA 那次只反映 CA 仓位——不是重新实现
   隔离逻辑去验证,而是从 analyze() 真实产出的 `portfolio_context_text`(即将喂给
   `_run_tradingagents_sync` 的那份文本)里读出来。
2. 市场未启用(disabled/`MarketCode.UNKNOWN` 占位符)时,`analyze()` 必须在触碰
   `_run_tradingagents_sync` / AIClient 的任何模型方法 / `build_ta_llm_config` /
   `check_budget` / `build_portfolio_context` / 任何数据库读写 / `get_market_data`
   之前就直接退出——本模块断言所有这些 spy 的调用次数为 0,而不是断言某段源码文本
   存在。§C3 要求的这个闸门原来不存在,已在 `src/agents/tradingagents/agent.py` 里
   新增(`TradingAgentsUnsupportedMarket` + `analyze()` 顶部的 `is_enabled` 检查)。

不连网、不依赖真实数据库、不构造/运行任何 TradingAgentsGraph、不发起任何真实
model/provider 调用；自动断网 fixture 照抄 tests/test_agent_portfolio_boundary.py 的
既有先例。TradingAgentsAgent 实例强制 `_available = True`,绕开对 `tradingagents`
这个重量级可选依赖是否真的装了的探测——本模块只关心 PanWatch 自己这段入口代码。
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agents.base import AccountInfo, AgentContext, PortfolioInfo, PositionInfo
from src.agents.tradingagents.agent import (
    TradingAgentsAgent,
    TradingAgentsUnsupportedMarket,
)
from src.agents.tradingagents.portfolio_context import (
    build_portfolio_context as _real_build_portfolio_context,
)
from src.config import StockConfig
from src.models.market import MarketCode


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed),照抄
    tests/test_agent_portfolio_boundary.py 的既有手法。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


class _NoOpConfig:
    """`analyze()` 从不读 `context.config`(只读 `context.ai_client` /
    `context.portfolio` / `context.model_label` / 动态属性 `_trace_id` /
    `_force_refresh`)。这里给个哑对象占位,刻意不构造真实 `Settings()`——
    它默认会从 CWD 读 `.env`(可能含真实密钥),本模块被明确禁止读 `.env`。"""

    watchlist: list = []


def _make_position(
    *,
    market: MarketCode,
    market_code: str,
    symbol: str,
    cost_price: float,
    quantity: int,
    account_id: int = 1,
    account_name: str = "Test Account",
) -> PositionInfo:
    return PositionInfo(
        account_id=account_id,
        account_name=account_name,
        stock_id=1,
        symbol=symbol,
        name=symbol,
        market=market,
        cost_price=cost_price,
        quantity=quantity,
        trading_style="swing",
        market_code=market_code,
    )


def _make_ai_client_spy() -> Any:
    """构造一个假 AIClient:只暴露 `build_ta_llm_config` 会读的静态属性
    (model/base_url/api_key/used_model_label,均为普通字符串,不是 Mock,否则
    `.strip()` 这类字符串方法会被 MagicMock 吞掉),同时给全部 5 个真实模型调用
    方法都装 spy——一旦被调用立刻抛错,证明生产入口全程没有发起过真实模型调用。
    """

    def _blow_up(*a, **k):
        raise AssertionError("real AIClient model call must never happen in this test")

    client = MagicMock(name="ai_client_spy")
    client.model = "fake-deep-model"
    client.base_url = "http://ai.invalid.test"
    client.api_key = "fake-api-key"
    client.used_model_label = ""
    for method in ("chat", "chat_multi", "chat_with_tools", "chat_stream", "list_models"):
        setattr(client, method, MagicMock(side_effect=_blow_up))
    return client


def _install_entrypoint_spies(monkeypatch, *, run_sync_decision: str = "HOLD") -> dict[str, MagicMock]:
    """在 `src.agents.tradingagents.agent` 命名空间里,给 analyze() 路径上每一个
    provider/DB/model 调用点打 spy,BEFORE 任何 analyze() 调用。返回 dict 供断言。

    - `_run_tradingagents_sync`:整个 TradingAgentsGraph/LLM 执行的唯一入口,换成
      纯内存 MagicMock,绝不导入/构造真实 `TradingAgentsGraph`。
    - `build_portfolio_context`:`MagicMock(wraps=真实实现)`,记录调用参数的同时
      仍跑真实隔离逻辑,供功能性断言。
    - `check_budget` / `get_analysis` / `save_analysis` / `save_suggestion` /
      `_collect_toolkit_diagnostic` / `analysis_detail_markdown`:analyze() 路径上
      每一个会碰真实数据库(`SessionLocal()`)的调用点,统统换成不碰 DB 的 spy。
    - `get_market_data`:agent.py 里唯一的 collector/provider 分发点(虽然只在
      `collect()` 里被调用,`analyze()` 不直接用它,但仍装 spy 作为"确无 provider
      分发"的额外证据)。
    """
    spies: dict[str, MagicMock] = {}

    run_sync_mock = MagicMock(
        return_value={"decision": run_sync_decision, "final_state": {}, "cost_usd": 0.02}
    )
    monkeypatch.setattr(TradingAgentsAgent, "_run_tradingagents_sync", run_sync_mock)
    spies["run_sync"] = run_sync_mock

    build_portfolio_context_spy = MagicMock(wraps=_real_build_portfolio_context)
    monkeypatch.setattr(
        "src.agents.tradingagents.agent.build_portfolio_context", build_portfolio_context_spy
    )
    spies["build_portfolio_context"] = build_portfolio_context_spy

    check_budget_mock = MagicMock(return_value={"exceeded": False, "used": 0.0})
    monkeypatch.setattr("src.agents.tradingagents.agent.check_budget", check_budget_mock)
    spies["check_budget"] = check_budget_mock

    def _get_analysis_blow_up(*a, **k):
        raise AssertionError("get_analysis (real DB read) must not be called in this test")

    get_analysis_mock = MagicMock(side_effect=_get_analysis_blow_up)
    monkeypatch.setattr("src.agents.tradingagents.agent.get_analysis", get_analysis_mock)
    spies["get_analysis"] = get_analysis_mock

    save_analysis_mock = MagicMock(return_value=None)
    monkeypatch.setattr("src.agents.tradingagents.agent.save_analysis", save_analysis_mock)
    spies["save_analysis"] = save_analysis_mock

    save_suggestion_mock = MagicMock(return_value=None)
    monkeypatch.setattr("src.core.suggestion_pool.save_suggestion", save_suggestion_mock)
    spies["save_suggestion"] = save_suggestion_mock

    diagnostic_mock = MagicMock(return_value={"summary": {}, "recent": []})
    monkeypatch.setattr(
        TradingAgentsAgent, "_collect_toolkit_diagnostic", staticmethod(diagnostic_mock)
    )
    spies["collect_toolkit_diagnostic"] = diagnostic_mock

    analysis_link_mock = MagicMock(return_value="")
    monkeypatch.setattr(
        "src.core.analysis_link.analysis_detail_markdown", analysis_link_mock
    )
    spies["analysis_detail_markdown"] = analysis_link_mock

    def _get_market_data_blow_up(*a, **k):
        raise AssertionError("get_market_data (real provider dispatch) must not be called")

    get_market_data_mock = MagicMock(side_effect=_get_market_data_blow_up)
    monkeypatch.setattr("src.agents.tradingagents.agent.get_market_data", get_market_data_mock)
    spies["get_market_data"] = get_market_data_mock

    return spies


def _build_agent() -> TradingAgentsAgent:
    """构造 TradingAgentsAgent 并强制标记为可用,绕开对重量级可选依赖
    `tradingagents` 是否真的装了的探测——本模块只驱动 PanWatch 自己这段入口代码,
    从不导入/构造真实 `TradingAgentsGraph`。"""
    agent = TradingAgentsAgent()
    agent._available = True
    agent._import_error = ""
    return agent


def _assert_zero_provider_and_model_calls(spies: dict[str, MagicMock], ai_client: Any) -> None:
    for name, spy in spies.items():
        assert spy.call_count == 0, f"spy '{name}' should not have been called, but was"
    for method in ("chat", "chat_multi", "chat_with_tools", "chat_stream", "list_models"):
        assert getattr(ai_client, method).call_count == 0, (
            f"AIClient.{method} should not have been called, but was"
        )


# ---------------------------------------------------------------------------
# 1) 真实驱动 analyze():同一 symbol 跨 US/CA 市场,stock_market 反映标的自身市场
# ---------------------------------------------------------------------------


def test_analyze_passes_instruments_own_market_and_isolates_same_symbol_holdings(monkeypatch):
    """真实调用生产入口 TradingAgentsAgent.analyze():同一 symbol 在 US/CA 各有一笔
    仓位时,US 标的的分析只应看到 build_portfolio_context 收到 stock_market="US"、
    渲染出的持仓上下文只反映 US 仓位的数量/均价;CA 标的的分析同理只看到 CA 那份——
    这段验证直接读 analyze() 真实产出的 portfolio_context_text(即将喂给
    _run_tradingagents_sync 的那份文本),而不是重新实现或只查源码文本。"""
    symbol = "SHOP"
    us_pos = _make_position(
        market=MarketCode.US, market_code="US", symbol=symbol, cost_price=10.0, quantity=100
    )  # cost 1000
    ca_pos = _make_position(
        market=MarketCode.CA, market_code="CA", symbol=symbol, cost_price=50.0, quantity=4
    )  # cost 200
    account = AccountInfo(id=1, name="Test", available_funds=0.0, positions=[us_pos, ca_pos])
    portfolio = PortfolioInfo(accounts=[account])

    ai_client = _make_ai_client_spy()
    spies = _install_entrypoint_spies(monkeypatch)
    agent = _build_agent()

    # --- US 标的分析 ---
    stock_us = StockConfig(symbol=symbol, name="Shopify", market=MarketCode.US)
    context_us = AgentContext(
        ai_client=ai_client, notifier=MagicMock(), config=_NoOpConfig(), portfolio=portfolio
    )
    context_us._force_refresh = True  # 跳过同日缓存的 DB 查询路径

    import asyncio

    result_us = asyncio.run(
        agent.analyze(context_us, {"stock": stock_us, "quote": {"current_price": 12.0}})
    )
    assert result_us.agent_name == "tradingagents"

    assert spies["build_portfolio_context"].call_args.kwargs["stock_market"] == "US"
    assert spies["run_sync"].call_args.kwargs["market"] == "US"
    us_text = spies["run_sync"].call_args.kwargs["portfolio_context_text"]
    assert "Quantity: 100 shares" in us_text
    assert "Average cost: 10.00" in us_text
    assert "Quantity: 4 shares" not in us_text
    assert "Average cost: 50.00" not in us_text

    # --- CA 标的分析(同一 symbol,不同市场) ---
    stock_ca = StockConfig(symbol=symbol, name="Shopify Canada", market=MarketCode.CA)
    context_ca = AgentContext(
        ai_client=ai_client, notifier=MagicMock(), config=_NoOpConfig(), portfolio=portfolio
    )
    context_ca._force_refresh = True

    result_ca = asyncio.run(
        agent.analyze(context_ca, {"stock": stock_ca, "quote": {"current_price": 55.0}})
    )
    assert result_ca.agent_name == "tradingagents"

    assert spies["build_portfolio_context"].call_args.kwargs["stock_market"] == "CA"
    assert spies["run_sync"].call_args.kwargs["market"] == "CA"
    ca_text = spies["run_sync"].call_args.kwargs["portfolio_context_text"]
    assert "Quantity: 4 shares" in ca_text
    assert "Average cost: 50.00" in ca_text
    assert "Quantity: 100 shares" not in ca_text
    assert "Average cost: 10.00" not in ca_text

    # 两次真实分析全程都没有碰过真实模型方法(_run_tradingagents_sync 被 mock 掉了)
    for method in ("chat", "chat_multi", "chat_with_tools", "chat_stream", "list_models"):
        assert getattr(ai_client, method).call_count == 0
    assert spies["get_market_data"].call_count == 0
    assert spies["run_sync"].call_count == 2


# ---------------------------------------------------------------------------
# 2) 市场未启用(MarketCode.UNKNOWN 占位符)→ analyze() 必须在任何 provider/model
#    调用之前退出
# ---------------------------------------------------------------------------


def test_analyze_rejects_unknown_market_before_any_provider_or_model_call(monkeypatch):
    """`MarketCode.UNKNOWN` 是一个"不是已知市场"的类型占位符,`is_enabled()` 对它
    永远返回 False(与 ENABLED_MARKETS 环境变量无关,见 src/models/market.py 顶部
    注释)。传入这样的标的时,analyze() 必须在触碰 _run_tradingagents_sync / AIClient
    的任何模型方法 / build_ta_llm_config(经 check_budget 间接验证同一路径未走到)
    / build_portfolio_context / 任何数据库读写 / get_market_data 之前就直接退出,
    抛出 TradingAgentsUnsupportedMarket——本测试断言的是"调用次数为 0",不是断言
    某段源码文本存在。"""
    stock_unknown = StockConfig(symbol="ZZZZ", name="Unknown Co", market=MarketCode.UNKNOWN)

    ai_client = _make_ai_client_spy()
    spies = _install_entrypoint_spies(monkeypatch)
    agent = _build_agent()

    context = AgentContext(
        ai_client=ai_client, notifier=MagicMock(), config=_NoOpConfig(), portfolio=PortfolioInfo()
    )
    context._force_refresh = True

    import asyncio

    with pytest.raises(TradingAgentsUnsupportedMarket):
        asyncio.run(agent.analyze(context, {"stock": stock_unknown, "quote": {}}))

    _assert_zero_provider_and_model_calls(spies, ai_client)


# ---------------------------------------------------------------------------
# 3) 已退役市场(CN)显式禁用 → 同样在任何 provider/model 调用之前退出
# ---------------------------------------------------------------------------


def test_analyze_rejects_disabled_market_before_any_provider_or_model_call(monkeypatch):
    """CN 是否"未启用"本来取决于部署的 ENABLED_MARKETS 环境变量,为了不依赖测试
    运行环境的实际配置,这里显式把 src.models.market.ENABLED_MARKETS 打成不含 CN
    的元组(is_enabled() 每次调用都从模块全局读取,monkeypatch 模块属性对它生效)。
    在这个显式配置下,CN 标的的分析必须在任何 provider/model 调用之前就退出,
    与 MarketCode.UNKNOWN 那条路径共享同一处闸门实现(src.models.market.is_enabled),
    而不是 agent.py 里另起一份硬编码市场名单。"""
    monkeypatch.setattr("src.models.market.ENABLED_MARKETS", ("US", "CA"))

    stock_cn = StockConfig(symbol="600519", name="Kweichow Moutai", market=MarketCode.CN)

    ai_client = _make_ai_client_spy()
    spies = _install_entrypoint_spies(monkeypatch)
    agent = _build_agent()

    context = AgentContext(
        ai_client=ai_client, notifier=MagicMock(), config=_NoOpConfig(), portfolio=PortfolioInfo()
    )
    context._force_refresh = True

    import asyncio

    with pytest.raises(TradingAgentsUnsupportedMarket):
        asyncio.run(agent.analyze(context, {"stock": stock_cn, "quote": {}}))

    _assert_zero_provider_and_model_calls(spies, ai_client)
