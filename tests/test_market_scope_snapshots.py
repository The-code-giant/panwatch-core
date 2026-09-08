"""市场范围隔离回归测试(第二轮,补齐第一轮遗漏面):快照读路径与统计聚合都必须
按 ENABLED_MARKETS 隔离,且"最新快照日期"的判定本身也必须限定在启用市场范围内
——一个只包含禁用市场(CN/HK)的更新快照,绝不能压制一个更早的、包含启用市场
(US/CA)数据的快照。

覆盖范围:
1) `src.core.strategy_engine.list_strategy_signals` 与
   `src.core.entry_candidates.list_entry_candidates`(第一轮只测了前者,这里两个
   读路径都覆盖):最新快照解析、显式历史快照、显式禁用/启用市场。
2) `src.core.strategy_engine.get_strategy_stats`:覆盖率/top_signals/regimes/
   portfolio_risk/by_market 都应限定在启用市场;`market="ALL"` 的
   `StrategyWeight`/`StrategyWeightHistory` 行必须继续可读(不按市场过滤),
   `weight_map` 的 ALL 兜底继续生效——这是第一轮的回归线。
3) `src.core.strategy_engine.list_market_regime_snapshots` 的 `scope` 参数:
   "active" 排除 CN/HK,"all" 返回完整历史且每项带 `supported` 标记。

本模块不连网、不调用真实行情供应商、不依赖真实数据库中的既有数据:所有用例
用独立内存 sqlite(`StaticPool`)+ 替换 `strategy_engine`/`strategy_catalog`/
`entry_candidates` 模块内部引用的 `SessionLocal`,与 tests/test_market_scope_isolation.py
的隔离手法一致。`from src.web.database import Base` 只为拿到 ORM 元数据建表；
`create_engine` 是惰性的，不建立连接、不触碰真实 `data/tickerkeep.db`。
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.core.entry_candidates as entry_candidates
import src.core.strategy_catalog as strategy_catalog
import src.core.strategy_engine as strategy_engine
from src.core.timezone import utc_now
from src.models.market import ENABLED_MARKETS


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed)。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def mem_session_factory(monkeypatch):
    """独立内存 sqlite(不碰真实 data/tickerkeep.db):把 strategy_engine/strategy_catalog/
    entry_candidates 模块内部引用的 SessionLocal 换成绑定到本地全新引擎的 sessionmaker。"""
    from src.web import models as _models  # noqa: F401  确保所有 ORM 模型注册到 Base.metadata
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(strategy_engine, "SessionLocal", session_factory)
    monkeypatch.setattr(strategy_catalog, "SessionLocal", session_factory)
    monkeypatch.setattr(entry_candidates, "SessionLocal", session_factory)
    return session_factory


def _seed_signal_run(db, *, symbol, market, snapshot, strategy_code="trend_follow", status="active"):
    from src.web.models import StrategySignalRun

    row = StrategySignalRun(
        snapshot_date=snapshot,
        stock_symbol=symbol,
        stock_market=market,
        strategy_code=strategy_code,
        status=status,
        score=60.0,
        rank_score=60.0,
    )
    db.add(row)
    return row


def _seed_entry_candidate(db, *, symbol, market, snapshot, status="active"):
    from src.web.models import EntryCandidate

    row = EntryCandidate(
        stock_symbol=symbol,
        stock_market=market,
        snapshot_date=snapshot,
        status=status,
        score=60.0,
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# 1) list_strategy_signals / list_entry_candidates —— 快照日期解析与市场范围
# ---------------------------------------------------------------------------


def test_list_strategy_signals_newer_disabled_only_snapshot_does_not_suppress_older_enabled(
    mem_session_factory,
):
    """核心回归:更新的纯禁用市场(HK/CN)快照不应压制更早的、含启用市场(US/CA)数据的快照。"""
    db = mem_session_factory()
    try:
        _seed_signal_run(db, symbol="HKX", market="HK", snapshot="2026-09-05")
        _seed_signal_run(db, symbol="CNX", market="CN", snapshot="2026-09-05")
        _seed_signal_run(db, symbol="AAPL", market="US", snapshot="2026-09-04")
        _seed_signal_run(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-04")
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-04"
    assert res["count"] == 2
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL", "SHOP.TO"}


def test_list_entry_candidates_newer_disabled_only_snapshot_does_not_suppress_older_enabled(
    mem_session_factory,
):
    """核心回归:更新的纯禁用市场(HK/CN)快照不应压制更早的、含启用市场(US/CA)数据的快照。"""
    db = mem_session_factory()
    try:
        _seed_entry_candidate(db, symbol="HKX", market="HK", snapshot="2026-09-05")
        _seed_entry_candidate(db, symbol="CNX", market="CN", snapshot="2026-09-05")
        _seed_entry_candidate(db, symbol="AAPL", market="US", snapshot="2026-09-04")
        _seed_entry_candidate(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-04")
        db.commit()
    finally:
        db.close()

    res = entry_candidates.list_entry_candidates(market="", status="all", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-04"
    assert res["count"] == 2
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL", "SHOP.TO"}


def test_list_strategy_signals_distinct_us_and_ca_snapshot_dates(mem_session_factory):
    """US 与 CA 拥有各自不同的快照日期时,未指定 market 只应返回全局最新那一天的行(此处即 US)。"""
    db = mem_session_factory()
    try:
        _seed_signal_run(db, symbol="AAPL", market="US", snapshot="2026-09-03")
        _seed_signal_run(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-01")
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-03"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL"}


def test_list_entry_candidates_distinct_us_and_ca_snapshot_dates(mem_session_factory):
    """US 与 CA 拥有各自不同的快照日期时,未指定 market 只应返回全局最新那一天的行(此处即 US)。"""
    db = mem_session_factory()
    try:
        _seed_entry_candidate(db, symbol="AAPL", market="US", snapshot="2026-09-03")
        _seed_entry_candidate(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-01")
        db.commit()
    finally:
        db.close()

    res = entry_candidates.list_entry_candidates(market="", status="all", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-03"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL"}


def test_list_strategy_signals_explicit_snapshot_date_honoured_and_still_scoped(mem_session_factory):
    """显式传入历史 snapshot_date 时该日期被直接采用(不会被重新解析为最新),但市场范围过滤依然生效。"""
    db = mem_session_factory()
    try:
        _seed_signal_run(db, symbol="AAPL", market="US", snapshot="2026-09-01")
        _seed_signal_run(db, symbol="CNX", market="CN", snapshot="2026-09-01")
        _seed_signal_run(db, symbol="MSFT", market="US", snapshot="2026-09-05")
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="", snapshot_date="2026-09-01")
    assert res["snapshot_date"] == "2026-09-01"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL"}


def test_list_entry_candidates_explicit_snapshot_date_honoured_and_still_scoped(mem_session_factory):
    """显式传入历史 snapshot_date 时该日期被直接采用(不会被重新解析为最新),但市场范围过滤依然生效。"""
    db = mem_session_factory()
    try:
        _seed_entry_candidate(db, symbol="AAPL", market="US", snapshot="2026-09-01")
        _seed_entry_candidate(db, symbol="CNX", market="CN", snapshot="2026-09-01")
        _seed_entry_candidate(db, symbol="MSFT", market="US", snapshot="2026-09-05")
        db.commit()
    finally:
        db.close()

    res = entry_candidates.list_entry_candidates(market="", status="all", snapshot_date="2026-09-01")
    assert res["snapshot_date"] == "2026-09-01"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"AAPL"}


def test_list_strategy_signals_explicit_disabled_market_returns_empty(mem_session_factory):
    """显式传入已禁用市场(CN)时返回空结果形状:count=0、items=[]。"""
    db = mem_session_factory()
    try:
        _seed_signal_run(db, symbol="CNX", market="CN", snapshot="2026-09-01")
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="CN", snapshot_date="2026-09-01")
    assert res["count"] == 0
    assert res["items"] == []


def test_list_entry_candidates_explicit_disabled_market_returns_empty(mem_session_factory):
    """显式传入已禁用市场(CN)时返回空结果形状:count=0、items=[]。"""
    db = mem_session_factory()
    try:
        _seed_entry_candidate(db, symbol="CNX", market="CN", snapshot="2026-09-01")
        db.commit()
    finally:
        db.close()

    res = entry_candidates.list_entry_candidates(market="CN", status="all", snapshot_date="2026-09-01")
    assert res["count"] == 0
    assert res["items"] == []


def test_list_strategy_signals_explicit_enabled_market_resolves_latest_within_that_market(
    mem_session_factory,
):
    """显式传入启用市场(CA)时,"最新快照日期"应只在 CA 范围内解析,不应被全局更晚的 US 快照带偏。"""
    db = mem_session_factory()
    try:
        _seed_signal_run(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-01")
        _seed_signal_run(db, symbol="AAPL", market="US", snapshot="2026-09-05")
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_strategy_signals(market="CA", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-01"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"SHOP.TO"}


def test_list_entry_candidates_explicit_enabled_market_resolves_latest_within_that_market(
    mem_session_factory,
):
    """显式传入启用市场(CA)时,"最新快照日期"应只在 CA 范围内解析,不应被全局更晚的 US 快照带偏。"""
    db = mem_session_factory()
    try:
        _seed_entry_candidate(db, symbol="SHOP.TO", market="CA", snapshot="2026-09-01")
        _seed_entry_candidate(db, symbol="AAPL", market="US", snapshot="2026-09-05")
        db.commit()
    finally:
        db.close()

    res = entry_candidates.list_entry_candidates(market="CA", status="all", snapshot_date="")
    assert res["snapshot_date"] == "2026-09-01"
    assert res["count"] == 1
    assert {i["stock_symbol"] for i in res["items"]} == {"SHOP.TO"}


# ---------------------------------------------------------------------------
# 2) get_strategy_stats —— 覆盖率/榜单/regime/风险画像限定在启用市场,
#    market="ALL" 的权重行不受影响
# ---------------------------------------------------------------------------


def test_get_strategy_stats_scopes_coverage_top_signals_regimes_and_risk_to_enabled_markets(
    mem_session_factory,
):
    """get_strategy_stats:覆盖率/top_signals/regimes/portfolio_risk/by_market 都应限定在 ENABLED_MARKETS
    之内,更新的纯禁用市场快照不应成为"最新快照",且响应应带非零的 excluded_by_scope 与 market_scope。"""
    from src.web.models import (
        MarketRegimeSnapshot,
        PortfolioRiskSnapshot,
        StrategyOutcome,
        StrategySignalRun,
    )

    db = mem_session_factory()
    try:
        # 更新的纯禁用市场快照:不应被判定为"最新快照"
        _seed_signal_run(db, symbol="HKX", market="HK", snapshot="2026-09-05")
        db.commit()

        rows = {}
        for sym, mkt in (("AAPL", "US"), ("SHOP.TO", "CA"), ("CNX", "CN"), ("HKY", "HK")):
            row = StrategySignalRun(
                snapshot_date="2026-09-04",
                stock_symbol=sym,
                stock_market=mkt,
                strategy_code="trend_follow",
                status="active",
                score=60.0,
                rank_score=60.0,
            )
            db.add(row)
            rows[mkt] = row
        db.commit()

        for mkt in ("US", "CA", "CN", "HK"):
            db.add(MarketRegimeSnapshot(snapshot_date="2026-09-04", market=mkt))
            db.add(PortfolioRiskSnapshot(snapshot_date="2026-09-04", market=mkt))
        db.commit()

        now = utc_now()
        for mkt, row in rows.items():
            db.add(
                StrategyOutcome(
                    signal_run_id=row.id,
                    strategy_code="trend_follow",
                    snapshot_date="2026-09-04",
                    stock_symbol=row.stock_symbol,
                    stock_market=mkt,
                    horizon_days=1,
                    target_date="2026-09-05",
                    outcome_return_pct=1.0,
                    outcome_status="evaluated",
                    created_at=now,
                )
            )
        db.commit()
    finally:
        db.close()

    res = strategy_engine.get_strategy_stats(days=45)

    assert res["coverage"]["snapshot_date"] == "2026-09-04"
    assert res["coverage"]["total_signals"] == 2
    assert res["coverage"]["active_signals"] == 2

    top_symbols = {x["stock_symbol"] for x in res["top_signals"]}
    assert top_symbols == {"AAPL", "SHOP.TO"}

    regime_markets = {x["market"] for x in res["regimes"]}
    assert regime_markets == {"US", "CA"}

    risk_markets = {x["market"] for x in res["portfolio_risk"]}
    assert risk_markets == {"US", "CA"}

    by_market_markets = {x["market"] for x in res["by_market"]}
    assert "CN" not in by_market_markets
    assert "HK" not in by_market_markets

    assert set(res["market_scope"]) == set(ENABLED_MARKETS)
    assert res["excluded_by_scope"]["signals"] > 0
    assert res["excluded_by_scope"]["outcomes"] > 0


def test_get_strategy_stats_all_market_weight_rows_still_readable_and_fallback_applies(
    mem_session_factory,
):
    """回归守护(第一轮):market="ALL" 的 StrategyWeight/StrategyWeightHistory 行不应被按市场过滤掉,
    weight_map 的 (code, "ALL") 兜底继续对没有专属市场权重的 (strategy, market) 生效。"""
    from src.web.models import StrategyOutcome, StrategySignalRun, StrategyWeight, StrategyWeightHistory

    db = mem_session_factory()
    try:
        row = StrategySignalRun(
            snapshot_date="2026-09-04",
            stock_symbol="AAPL",
            stock_market="US",
            strategy_code="trend_follow",
            status="active",
            score=60.0,
            rank_score=60.0,
        )
        db.add(row)
        db.commit()

        now = utc_now()
        db.add(
            StrategyOutcome(
                signal_run_id=row.id,
                strategy_code="trend_follow",
                snapshot_date="2026-09-04",
                stock_symbol="AAPL",
                stock_market="US",
                horizon_days=1,
                target_date="2026-09-05",
                outcome_return_pct=2.0,
                outcome_status="evaluated",
                created_at=now,
            )
        )
        # 没有 (trend_follow, US) 专属权重行,只有 ALL 兜底行。
        db.add(StrategyWeight(strategy_code="trend_follow", market="ALL", regime="default", weight=1.35))
        db.add(
            StrategyWeightHistory(
                strategy_code="trend_follow",
                market="ALL",
                regime="default",
                old_weight=1.0,
                new_weight=1.35,
                created_at=now,
            )
        )
        db.commit()
    finally:
        db.close()

    res = strategy_engine.get_strategy_stats(days=45)

    us_rows = [x for x in res["by_strategy"] if x["strategy_code"] == "trend_follow" and x["market"] == "US"]
    assert us_rows, "expected an outcome-derived US row for trend_follow"
    assert us_rows[0]["current_weight"] == pytest.approx(1.35)

    assert res["weight_updates"]["changed"] >= 1


# ---------------------------------------------------------------------------
# 3) list_market_regime_snapshots —— scope="active" / "all"
# ---------------------------------------------------------------------------


def test_list_market_regime_snapshots_scope_active_excludes_disabled_markets(mem_session_factory):
    """scope="active"(默认语义)时,只返回 ENABLED_MARKETS 内的市场,响应带 scope 与 market_scope。"""
    from src.web.models import MarketRegimeSnapshot

    db = mem_session_factory()
    try:
        for mkt in ("US", "CA", "CN", "HK"):
            db.add(MarketRegimeSnapshot(snapshot_date="2026-09-04", market=mkt))
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_market_regime_snapshots(snapshot_date="2026-09-04", scope="active")
    markets = {x["market"] for x in res["items"]}
    assert markets == {"US", "CA"}
    assert res.get("scope") == "active"
    assert set(res.get("market_scope") or []) == set(ENABLED_MARKETS)
    assert all(x.get("supported") is True for x in res["items"])


def test_list_market_regime_snapshots_scope_all_returns_full_history_with_supported_flag(
    mem_session_factory,
):
    """scope="all" 时返回完整历史(含 CN/HK),但每一项都带 supported 标记以区分是否可操作。"""
    from src.web.models import MarketRegimeSnapshot

    db = mem_session_factory()
    try:
        for mkt in ("US", "CA", "CN", "HK"):
            db.add(MarketRegimeSnapshot(snapshot_date="2026-09-04", market=mkt))
        db.commit()
    finally:
        db.close()

    res = strategy_engine.list_market_regime_snapshots(snapshot_date="2026-09-04", scope="all")
    markets = {x["market"] for x in res["items"]}
    assert markets == {"US", "CA", "CN", "HK"}
    assert res.get("scope") == "all"

    supported_map = {x["market"]: x.get("supported") for x in res["items"]}
    assert supported_map["US"] is True
    assert supported_map["CA"] is True
    assert supported_map["CN"] is False
    assert supported_map["HK"] is False
