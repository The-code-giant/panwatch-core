"""持仓覆盖率(valuation coverage)契约回归测试:`_gather_holdings` 必须在同一次查询/
快照内返回 (holdings, coverage) 二元组(显式返回值,不是模块级共享字典),四个分析型
端点(diagnostics/benchmark/attribution/ai-review)必须在响应中附加同一份 `valuation`
覆盖块,且绝不能在遗漏持仓后暗示结果是完整的。

覆盖范围:
1) 混合已定价/未定价持仓 —— coverage 精确反映 analysed/excluded 计数与 basis。
2) 空组合(未启用账户下无任何持仓)—— 四个端点都返回一致、"天然完整"的 coverage。
3) 全部未定价的组合 —— 绝不能被误报成 "no_holdings"(那会掩盖"其实持有、只是排除在
   分析之外"的事实);诊断/基准/归因/AI 体检都应报告 excluded_unpriced_holdings > 0。
4) 缓存命中与未命中的 benchmark/attribution 响应必须暴露相同的 coverage。
5) 并发回归:两个持有不同持仓的"请求"通过 `concurrent.futures.ThreadPoolExecutor`
   强制交叠执行 `_gather_holdings`,断言各自只拿到自己的 coverage,绝不会串号
   ——这正是 REVIEW-04 明确禁止的"模块级字典在并发请求间竞态"的回归证明。

本模块不连网、不调用真实行情供应商、不依赖真实数据库:自动断网 fixture 照抄
tests/test_position_support_boundary.py 的既有先例;`_fetch_quotes_for_stocks` 与
`get_cad_usd_rate` 全部 monkeypatch 为纯内存假实现;DB 用全新内存 sqlite
(StaticPool)。accounts.py 的所有路由函数都以显式 `db: Session = Depends(get_db)`
参数接收会话,从不在内部调用 `SessionLocal()`(与 tests/test_portfolio_result_cache.py
的既有测试手法一致),因此本模块不需要、也不去 monkeypatch `SessionLocal` ——那样做
只会是无意义的空操作,与本仓库"不要假装验证了未真正验证的东西"的准则相悖。
"""

from __future__ import annotations

import asyncio
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.web import models as M
from src.web.api import accounts as accounts_api
from src.web.database import Base


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed)。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def db(monkeypatch):
    """全新内存 sqlite(StaticPool),并清空组合结果缓存,避免跨用例污染。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # get_cad_usd_rate 会在 `_gather_holdings` 中被无条件调用(即使组合为空),固定为常量
    # 以避免依赖真实网络/BOC/雅虎源;本模块的场景均使用 US 市场,FX 恒为 1.0,与该常量
    # 是否被使用无关,只保证它绝不触发网络调用。
    monkeypatch.setattr(accounts_api, "get_cad_usd_rate", lambda: 0.73)
    accounts_api._PORTFOLIO_RESULT_CACHE.clear()
    try:
        yield session
    finally:
        session.close()
        accounts_api._PORTFOLIO_RESULT_CACHE.clear()


def _add_position(db, symbol: str, market: str, qty: float, cost_price: float = 1.0, account=None):
    """在(可选指定的)启用账户下新增一条真实持仓;不传 account 则复用/新建默认账户。"""
    acc = account
    if acc is None:
        acc = db.query(M.Account).first()
        if not acc:
            acc = M.Account(name="t", available_funds=0, enabled=True)
            db.add(acc)
            db.flush()
    st = M.Stock(symbol=symbol, name=symbol, market=market)
    db.add(st)
    db.flush()
    db.add(M.Position(account_id=acc.id, stock_id=st.id, cost_price=cost_price, quantity=qty))
    db.commit()
    return acc


def _quotes_by_symbol(priced: dict[str, float]):
    """构造一个 `_fetch_quotes_for_stocks` 假实现:只有 `priced` 里列出的 symbol 有报价,
    其余(包括未在字典中出现的、以及显式排除的)都视为未定价(键不存在)。"""

    def _fake(stocks):
        out = {}
        for s in stocks:
            if s.symbol in priced:
                out[(s.market, s.symbol)] = {
                    "current_price": priced[s.symbol],
                    "change_pct": 0.0,
                    "prev_close": priced[s.symbol],
                }
        return out

    return _fake


# ---------------------------------------------------------------------------
# 1) 混合已定价 / 未定价
# ---------------------------------------------------------------------------


def test_gather_holdings_mixed_priced_and_unpriced(db, monkeypatch):
    """混合已定价/未定价持仓:coverage 精确反映 analysed=1、excluded=1,basis 为
    priced_subset,且带有明确的"未按成本计价/未计零"说明。"""
    _add_position(db, "AAPL", "US", 10, cost_price=100.0)
    _add_position(db, "MSFT", "US", 5, cost_price=200.0)
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0}))

    holdings, coverage = accounts_api._gather_holdings(db)

    assert [h["symbol"] for h in holdings] == ["AAPL"]
    assert coverage["analysed_holdings"] == 1
    assert coverage["excluded_unpriced_holdings"] == 1
    assert coverage["valuation_complete"] is False
    assert coverage["basis"] == "priced_subset"
    assert set(coverage.keys()) == {
        "analysed_holdings",
        "excluded_unpriced_holdings",
        "valuation_complete",
        "basis",
        "note",
    }
    assert "not valued at cost" in coverage["note"]
    assert "not counted as zero" in coverage["note"]

    # 四个端点都必须携带同一份 coverage,而不是各自重新算出不一致的版本。
    diag = accounts_api.portfolio_diagnostics(db=db)
    assert diag["valuation"] == coverage
    assert diag["position_count"] == 1  # 只统计已定价的那 1 条


# ---------------------------------------------------------------------------
# 2) 空组合
# ---------------------------------------------------------------------------


def test_empty_portfolio_coverage_is_trivially_complete(db, monkeypatch):
    """无任何启用账户持仓时,四个端点的 coverage 都应是"天然完整"(0 已分析、0 排除、
    complete=True),而不是缺失或矛盾。"""
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({}))
    empty_cov = {
        "analysed_holdings": 0,
        "excluded_unpriced_holdings": 0,
        "valuation_complete": True,
        "basis": "complete",
    }

    holdings, coverage = accounts_api._gather_holdings(db)
    assert holdings == []
    assert coverage == empty_cov

    diag = accounts_api.portfolio_diagnostics(db=db)
    assert diag["valuation"] == empty_cov

    bench = accounts_api.portfolio_benchmark(db=db)
    assert bench == {"empty": True, "reason": "no_holdings", "valuation": empty_cov}

    attr = accounts_api.portfolio_attribution(db=db)
    assert attr == {"items": [], "valuation": empty_cov}

    review = asyncio.run(accounts_api.portfolio_ai_review(db=db))
    assert review == {"empty": True, "reason": "no_holdings", "valuation": empty_cov}


# ---------------------------------------------------------------------------
# 3) 全部未定价
# ---------------------------------------------------------------------------


def test_all_unpriced_portfolio_is_not_reported_as_no_holdings(db, monkeypatch):
    """全部持仓都拿不到价格时,绝不能报告成 "no_holdings"(那会掩盖"其实持有、只是被排除
    在分析之外"的事实);四个端点都必须显式报告 excluded_unpriced_holdings > 0。"""
    _add_position(db, "ZZZZ", "US", 10, cost_price=50.0)
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({}))

    holdings, coverage = accounts_api._gather_holdings(db)
    assert holdings == []
    assert coverage["analysed_holdings"] == 0
    assert coverage["excluded_unpriced_holdings"] == 1
    assert coverage["valuation_complete"] is False
    assert coverage["basis"] == "priced_subset"
    assert "not valued at cost" in coverage["note"]
    assert "not counted as zero" in coverage["note"]
    expected_cov = coverage

    diag = accounts_api.portfolio_diagnostics(db=db)
    assert diag["valuation"] == expected_cov
    assert diag["position_count"] == 0  # 诊断只描述已定价子集,但 coverage 说明了原因

    bench = accounts_api.portfolio_benchmark(db=db)
    assert bench == {"empty": True, "reason": "all_unpriced", "valuation": expected_cov}

    attr = accounts_api.portfolio_attribution(db=db)
    assert attr == {"items": [], "valuation": expected_cov}

    review = asyncio.run(accounts_api.portfolio_ai_review(db=db))
    assert review == {"empty": True, "reason": "all_unpriced", "valuation": expected_cov}


# ---------------------------------------------------------------------------
# 4) 缓存命中 / 未命中的一致性
# ---------------------------------------------------------------------------


def test_benchmark_cache_hit_and_miss_expose_same_coverage(db, monkeypatch):
    """benchmark 端点第一次(未命中,真实计算)与第二次(命中缓存)必须返回完全相同的
    valuation coverage,不能一个完整一个不完整。"""
    _add_position(db, "AAPL", "US", 10, cost_price=100.0)
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0}))

    import src.core.portfolio_benchmark as pb

    calls = {"n": 0}

    def fake_build(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return {"excess_return": 4.2}

    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(db=db)
    r2 = accounts_api.portfolio_benchmark(db=db)
    assert calls["n"] == 1, "第二次应命中缓存,不应重新构建"
    assert r1 == r2
    assert r1["valuation"] == {
        "analysed_holdings": 1,
        "excluded_unpriced_holdings": 0,
        "valuation_complete": True,
        "basis": "complete",
    }


def test_attribution_cache_hit_and_miss_expose_same_coverage(db, monkeypatch):
    """attribution 端点缓存命中/未命中同样必须暴露一致的 coverage。"""
    _add_position(db, "AAPL", "US", 10, cost_price=100.0)
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0}))

    import src.core.portfolio_benchmark as pb

    calls = {"n": 0}

    def fake_attr(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return [{"symbol": "AAPL", "contribution_pct": 1.0}]

    monkeypatch.setattr(pb, "build_attribution", fake_attr)

    r1 = accounts_api.portfolio_attribution(db=db)
    r2 = accounts_api.portfolio_attribution(db=db)
    assert calls["n"] == 1, "第二次应命中缓存,不应重新构建"
    assert r1 == r2
    assert r1["valuation"]["valuation_complete"] is True


def test_benchmark_cache_busts_when_priced_subset_changes_without_position_change(db, monkeypatch):
    """持仓行(数量/股票)本身不变,但"哪些能定价"发生变化(例如某只股票的报价从缺失
    恢复)时,缓存必须失效,不能把旧的、口径不一致的 coverage 继续提供给新请求。"""
    _add_position(db, "AAPL", "US", 10, cost_price=100.0)
    _add_position(db, "MSFT", "US", 5, cost_price=200.0)

    import src.core.portfolio_benchmark as pb

    calls = {"n": 0}

    def fake_build(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return {"excess_return": float(calls["n"])}

    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    # 第一次:MSFT 缺报价,只有 AAPL 被分析。
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0}))
    r1 = accounts_api.portfolio_benchmark(db=db)
    assert r1["valuation"]["analysed_holdings"] == 1
    assert r1["valuation"]["excluded_unpriced_holdings"] == 1

    # 第二次:持仓行完全没变,但 MSFT 的报价恢复了 —— 覆盖范围变了,必须重新计算,
    # 而不是原样返回第一次那份"MSFT 被排除"的缓存结果。
    monkeypatch.setattr(
        accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0, "MSFT": 210.0})
    )
    r2 = accounts_api.portfolio_benchmark(db=db)
    assert calls["n"] == 2, "覆盖范围变化后应重新计算,不能命中旧缓存"
    assert r2["valuation"]["analysed_holdings"] == 2
    assert r2["valuation"]["excluded_unpriced_holdings"] == 0
    assert r2["valuation"]["valuation_complete"] is True


# ---------------------------------------------------------------------------
# 5) 并发回归:一个请求绝不能拿到另一个请求的 coverage
# ---------------------------------------------------------------------------


def _make_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_concurrent_requests_never_receive_each_others_coverage(monkeypatch):
    """核心并发回归(REVIEW-04 §A):两个"请求"持有不同的组合,通过线程池强制交叠执行
    `_gather_holdings`,断言各自只拿到自己的 coverage —— 这正是"模块级字典在并发请求间
    竞态"这一被禁止方案会失败、而显式 (holdings, coverage) 返回值不会失败的地方。"""
    db_a = _make_memory_db()
    db_b = _make_memory_db()
    try:
        # 组合 A:1 只全部能定价。组合 B:1 只完全无法定价。
        _add_position(db_a, "AAPL", "US", 10, cost_price=100.0)
        _add_position(db_b, "ZZZZ", "US", 10, cost_price=50.0)

        monkeypatch.setattr(accounts_api, "get_cad_usd_rate", lambda: 0.73)

        barrier = threading.Barrier(2, timeout=5)

        def fake_fetch(stocks):
            # 强制两个线程在各自的 `_gather_holdings` 内部同时"在途",制造真实交叠,
            # 而不是先后顺序执行、掩盖潜在的共享状态竞态。
            barrier.wait()
            out = {}
            for s in stocks:
                if s.symbol == "AAPL":
                    out[(s.market, s.symbol)] = {
                        "current_price": 150.0,
                        "change_pct": 0.0,
                        "prev_close": 150.0,
                    }
                # ZZZZ: 故意不放入报价字典 -> 未定价
            return out

        monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", fake_fetch)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(accounts_api._gather_holdings, db_a)
            fut_b = pool.submit(accounts_api._gather_holdings, db_b)
            holdings_a, coverage_a = fut_a.result(timeout=5)
            holdings_b, coverage_b = fut_b.result(timeout=5)

        assert [h["symbol"] for h in holdings_a] == ["AAPL"]
        assert coverage_a == {
            "analysed_holdings": 1,
            "excluded_unpriced_holdings": 0,
            "valuation_complete": True,
            "basis": "complete",
        }

        assert holdings_b == []
        assert coverage_b["analysed_holdings"] == 0
        assert coverage_b["excluded_unpriced_holdings"] == 1
        assert coverage_b["valuation_complete"] is False
        assert coverage_b["basis"] == "priced_subset"
    finally:
        db_a.close()
        db_b.close()


# ---------------------------------------------------------------------------
# 6) 惰性 FX 路径(`_needs_fx`):纯美元账本不需要、也绝不能触发汇率 provider
# ---------------------------------------------------------------------------


def test_usd_only_holdings_are_complete_without_any_fx_provider(db, monkeypatch):
    """纯 USD 账本:`_gather_holdings` 必须得到完整 coverage(analysed=2、excluded=0、
    complete=True、basis complete),每条持仓 fx 为 1.0;整个过程绝不能调用
    `resolve_fx_snapshot` / 任一汇率 provider —— 即使汇率缓存处于"从未获取过且已过期"、
    一旦解析就会去抓取的状态。"""
    _add_position(db, "AAPL", "US", 10, cost_price=100.0)
    _add_position(db, "MSFT", "US", 5, cost_price=200.0)
    monkeypatch.setattr(
        accounts_api, "_fetch_quotes_for_stocks", _quotes_by_symbol({"AAPL": 150.0, "MSFT": 210.0})
    )
    # A cache that WOULD trigger a provider fetch if anything resolved FX.
    monkeypatch.setattr(accounts_api, "_fx_refresh_inflight", False)
    monkeypatch.setattr(
        accounts_api, "_cad_usd_rate_cache", {"rate": accounts_api._CAD_USD_FALLBACK, "ts": 0, "known": False}
    )

    def _forbidden(*_a, **_k):
        raise AssertionError("a USD-only book must never resolve FX or touch a rate provider")

    monkeypatch.setattr(accounts_api, "resolve_fx_snapshot", _forbidden)
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_boc", _forbidden)
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_macro", _forbidden)

    stocks = db.query(M.Stock).all()
    assert accounts_api._needs_fx(stocks) is False

    holdings, coverage = accounts_api._gather_holdings(db)

    assert sorted(h["symbol"] for h in holdings) == ["AAPL", "MSFT"]
    assert all(h["fx"] == 1.0 for h in holdings)
    assert coverage == {
        "analysed_holdings": 2,
        "excluded_unpriced_holdings": 0,
        "valuation_complete": True,
        "basis": "complete",
    }
    # The analytics endpoints ride the same lazy path: still no FX resolution.
    diag = accounts_api.portfolio_diagnostics(db=db)
    assert diag["valuation"] == coverage
    assert diag["position_count"] == 2


def test_needs_fx_is_true_only_when_a_ca_instrument_is_held(db):
    """`_needs_fx` 判定契约:任何 CA 市场持仓(不区分大小写/首尾空格)都需要汇率;
    纯 US 或空集合不需要。"""
    assert accounts_api._needs_fx([]) is False
    _add_position(db, "AAPL", "US", 1)
    assert accounts_api._needs_fx(db.query(M.Stock).all()) is False
    _add_position(db, "SHOP.TO", "CA", 1)
    assert accounts_api._needs_fx(db.query(M.Stock).all()) is True
    assert accounts_api._needs_fx([M.Stock(symbol="X", name="X", market=" ca ")]) is True
