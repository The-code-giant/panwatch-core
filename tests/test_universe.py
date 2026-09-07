"""可交易股票池(src.core.universe):缓存往返、按市场合并、fail-open、符号规范化、搜索排序。"""

from __future__ import annotations

import json
import os
import time

import pytest

from marketdata.universe import Listing
from src.core import universe


def _us(symbol: str, name: str, exchange: str = "NASDAQ", *, is_etf: bool = False,
        yahoo: str | None = None, exchange_symbol: str | None = None) -> Listing:
    return Listing(
        symbol=symbol,
        yahoo_symbol=yahoo or symbol.replace(".", "-"),
        exchange_symbol=exchange_symbol or symbol,
        name=name,
        market="US",
        exchange=exchange,
        is_etf=is_etf,
    )


def _ca(symbol: str, name: str, exchange: str = "TSX", *, is_etf: bool = False,
        exchange_symbol: str | None = None) -> Listing:
    base = symbol.rpartition(".")[0]
    return Listing(
        symbol=symbol,
        yahoo_symbol=symbol,
        exchange_symbol=exchange_symbol or base.replace("-", "."),
        name=name,
        market="CA",
        exchange=exchange,
        is_etf=is_etf,
    )


US_ROWS = [
    _us("AAPL", "Apple Inc."),
    _us("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE", yahoo="BRK-B", exchange_symbol="BRK.B"),
    _us("SPY", "SPDR S&P 500 ETF Trust", "NYSE Arca", is_etf=True),
]
CA_ROWS = [
    _ca("SHOP.TO", "Shopify Inc."),
    _ca("CTC-A.TO", "Canadian Tire Corporation Class A", exchange_symbol="CTC.A"),
    _ca("XYZ.V", "Venture Co", "TSXV"),
]


@pytest.fixture
def temp_universe(tmp_path, monkeypatch):
    """每个用例独立的 DATA_DIR + 空的内存状态 + 可替换的目录抓取函数。"""
    import marketdata.universe as pkg

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    universe.reset()
    state = {"US": list(US_ROWS), "CA": list(CA_ROWS), "fail": set()}

    def _fetch(market):
        def _fn(proxy=None):
            if market in state["fail"]:
                raise RuntimeError(f"{market} directory down")
            return list(state[market])
        return _fn

    monkeypatch.setattr(pkg, "fetch_us_listings", _fetch("US"))
    monkeypatch.setattr(pkg, "fetch_ca_listings", _fetch("CA"))
    yield state
    universe.reset()


def test_refresh_writes_cache_and_load_round_trips(temp_universe, tmp_path):
    """refresh_blocking 写入 DATA_DIR/universe_cache.json,reset 后 load 可完整读回。"""
    stats = universe.refresh_blocking(force=True)
    assert stats["count"] == 6 and stats["errors"] == {} and stats["skipped"] is False
    path = os.path.join(str(tmp_path), "universe_cache.json")
    assert os.path.exists(path)
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["version"] == universe.CACHE_VERSION
    assert set(payload["refreshed"]) == {"US", "CA"}

    universe.reset()
    assert not universe.is_loaded()
    rows = universe.load()
    assert len(rows) == 6 and universe.is_loaded()
    assert universe.lookup("US", "AAPL")["name"] == "Apple Inc."
    assert universe.lookup("CA", "SHOP.TO")["exchange"] == "TSX"
    assert not universe.is_stale()


def test_refresh_removes_legacy_stock_list_cache(temp_universe, tmp_path):
    """首次成功写入时删除遗留的 data/stock_list_cache.json。"""
    legacy = tmp_path / "stock_list_cache.json"
    legacy.write_text("{}")
    universe.refresh_blocking(force=True)
    assert not legacy.exists()


def test_refresh_skipped_when_fresh_unless_forced(temp_universe):
    """未过期且非 force 时跳过刷新;force=True 总是重新抓取。"""
    universe.refresh_blocking(force=True)
    temp_universe["US"] = [_us("MSFT", "Microsoft")]
    assert universe.refresh_blocking(force=False)["skipped"] is True
    assert universe.lookup("US", "AAPL") is not None
    universe.refresh_blocking(force=True)
    assert universe.lookup("US", "AAPL") is None and universe.lookup("US", "MSFT") is not None


def test_per_market_merge_keeps_last_good_rows_on_failure(temp_universe):
    """某市场抓取失败时保留其上次成功的行,其他市场照常更新,errors 记录失败市场。"""
    universe.refresh_blocking(force=True)
    temp_universe["fail"].add("CA")
    temp_universe["US"] = [_us("MSFT", "Microsoft")]
    stats = universe.refresh_blocking(force=True)
    assert "CA" in stats["errors"] and "US" not in stats["errors"]
    assert stats["by_market"] == {"US": 1, "CA": 3}
    assert universe.lookup("CA", "SHOP.TO") is not None
    assert universe.lookup("US", "MSFT") is not None
    assert universe.lookup("US", "AAPL") is None


def test_is_tradable_fails_open_without_rows(temp_universe):
    """无任何行(或该市场无行)时 is_tradable 返回 True;有行时严格按目录判断;非股票市场恒 True。"""
    assert universe.is_tradable("US", "WHATEVER") is True
    assert universe.is_tradable("CA", "ANY.TO") is True
    temp_universe["fail"].add("CA")
    universe.refresh_blocking(force=True)
    assert universe.is_tradable("US", "AAPL") is True
    assert universe.is_tradable("US", "ZZZZ") is False
    assert universe.is_tradable("CA", "ANY.TO") is True  # CA has no rows -> fail-open
    assert universe.is_tradable("CRYPTO", "BTC-USD") is True


def test_brk_b_spellings_all_resolve(temp_universe):
    """BRK_B / BRK.B / BRK-B 均命中同一条美股记录,yahoo_symbol 为 BRK-B。"""
    universe.refresh_blocking(force=True)
    for spelling in ("BRK.B", "BRK-B", "BRK_B", "brk.b"):
        row = universe.lookup("US", spelling)
        assert row is not None and row["symbol"] == "BRK.B", spelling
        assert universe.is_tradable("US", spelling)
    assert universe.yahoo_symbol("US", "BRK_B") == "BRK-B"
    assert universe.lookup_name("BRK_B") == "Berkshire Hathaway Inc. Class B"


def test_ca_exchange_spelling_resolves(temp_universe):
    """CTC.A.TO / CTC.A / CTC-A.TO 均命中 CTC-A.TO;yahoo_symbol 为 CTC-A.TO。"""
    universe.refresh_blocking(force=True)
    for spelling in ("CTC.A.TO", "CTC-A.TO", "CTC_A.TO", "CTC.A"):
        row = universe.lookup("CA", spelling)
        assert row is not None and row["symbol"] == "CTC-A.TO", spelling
    assert universe.yahoo_symbol("CA", "CTC.A.TO") == "CTC-A.TO"
    assert universe.lookup_name("SHOP.TO", "CA") == "Shopify Inc."
    assert universe.lookup_name("NOPE.TO") is None


def test_yahoo_symbol_falls_back_to_symbol_parse(temp_universe):
    """目录中不存在的代码用 marketdata.Symbol 推导 Yahoo 形式。"""
    universe.refresh_blocking(force=True)
    assert universe.yahoo_symbol("US", "BF.B") == "BF-B"
    assert universe.yahoo_symbol("CA", "RY.TO") == "RY.TO"


def test_search_ranking(temp_universe):
    """搜索排序:精确 > 代码前缀 > 名称词前缀 > 名称子串 > 代码子串。"""
    temp_universe["US"] = [
        _us("CAPL", "CrossAmerica Partners LP", "NYSE"),
        _us("SNAP", "Snap Inc.", "NYSE"),
        _us("AAPL", "Apple Inc."),
        _us("APLE", "Apple Hospitality REIT", "NYSE"),
        _us("AP", "Ampco-Pittsburgh Corporation", "NYSE"),
    ]
    temp_universe["CA"] = []
    temp_universe["fail"].add("CA")
    universe.refresh_blocking(force=True)
    rows = universe.search("ap", "US", limit=10)
    assert [r["symbol"] for r in rows] == ["AP", "APLE", "AAPL", "SNAP", "CAPL"]
    assert rows[0] == {"symbol": "AP", "name": "Ampco-Pittsburgh Corporation", "market": "US",
                       "exchange": "NYSE", "is_etf": False}
    assert universe.search("ap", "US", limit=2) == rows[:2]
    assert universe.search("", "US") == []


def test_search_ties_primary_exchange_then_stocks_before_etfs(temp_universe):
    """同一等级内:主板(NYSE/NASDAQ/TSX)先于 American/Arca/BZX/TSXV,股票先于 ETF。"""
    temp_universe["US"] = [
        _us("XYA", "Alpha Co", "NYSE American"),
        _us("XYC", "Gamma ETF", "NYSE", is_etf=True),
        _us("XYD", "Delta Co", "NYSE"),
        _us("XYB", "Beta Co", "NASDAQ"),
    ]
    temp_universe["CA"] = [
        _ca("XYZ.V", "Venture Co", "TSXV"),
        _ca("XYW.TO", "Toronto Co", "TSX"),
    ]
    universe.refresh_blocking(force=True)
    assert [r["symbol"] for r in universe.search("XY", "US")] == ["XYB", "XYD", "XYC", "XYA"]
    assert [r["symbol"] for r in universe.search("XY", "CA")] == ["XYW.TO", "XYZ.V"]
    both = universe.search("XY", "")
    assert len(both) == 6 and {r["market"] for r in both} == {"US", "CA"}


def test_stats_counts(temp_universe):
    """stats 返回总数、按市场/交易所计数、ETF 数与刷新时间。"""
    before = time.time()
    universe.refresh_blocking(force=True)
    s = universe.stats()
    assert s["count"] == 6
    assert s["by_market"] == {"US": 3, "CA": 3}
    assert s["by_exchange"] == {"NASDAQ": 1, "NYSE": 1, "NYSE Arca": 1, "TSX": 2, "TSXV": 1}
    assert s["etfs"] == 1
    assert s["stale"] is False
    assert s["refreshed"]["US"] >= before and s["refreshed"]["CA"] >= before
