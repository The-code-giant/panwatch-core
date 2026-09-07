"""Markets: US + Canada as the enabled equity markets (CN/HK retired).

Docstrings are in English to match the product's English rebrand, a deliberate
deviation from the Chinese-docstring convention used by the older test modules.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from src.models import market as market_model
from src.models.market import (
    ENABLED_MARKETS,
    EQUITY_MARKETS,
    MARKETS,
    MarketCode,
    default_market,
    is_enabled,
    market_display_name,
    normalize_market,
)


# ---------------------------------------------------------------------------
# Single source of truth
# ---------------------------------------------------------------------------


def test_enabled_markets_default():
    """ENABLED_MARKETS defaults to US, CA, CRYPTO, GOLD; EQUITY_MARKETS is the US/CA subset; default is US."""
    assert ENABLED_MARKETS == ("US", "CA", "CRYPTO", "GOLD")
    assert EQUITY_MARKETS == ("US", "CA")
    assert default_market() == "US"


def test_is_enabled_accepts_code_and_enum():
    """is_enabled works for both MarketCode and strings (case-insensitive) and rejects CN/HK."""
    assert is_enabled("US") and is_enabled("ca") and is_enabled(MarketCode.CRYPTO) and is_enabled("GOLD")
    assert not is_enabled("CN") and not is_enabled(MarketCode.HK) and not is_enabled("") and not is_enabled(None)


def test_parse_enabled_env_override():
    """The env parser keeps order, drops unknown codes, and falls back to the default when empty."""
    assert market_model._parse_enabled("CA,US") == ("CA", "US")
    assert market_model._parse_enabled(" us , nope , crypto ") == ("US", "CRYPTO")
    assert market_model._parse_enabled("") == ("US", "CA", "CRYPTO", "GOLD")
    assert market_model._parse_enabled(None) == ("US", "CA", "CRYPTO", "GOLD")


def test_normalize_market_falls_back_to_default():
    """normalize_market maps disabled/unknown codes to the default market and keeps enabled ones."""
    assert normalize_market("ca") == "CA"
    assert normalize_market("CN") == "US"
    assert normalize_market("") == "US"
    assert normalize_market(MarketCode.US) == "US"
    assert normalize_market("CRYPTO", allowed=ENABLED_MARKETS) == "CRYPTO"


def test_market_display_names():
    """Display names follow the shared label contract (US, Canada, Crypto, Gold)."""
    assert market_display_name("US") == "US"
    assert market_display_name(MarketCode.CA) == "Canada"
    assert market_display_name("CRYPTO") == "Crypto"
    assert market_display_name("GOLD") == "Gold"
    assert market_display_name("XX") == "XX"


# ---------------------------------------------------------------------------
# Canada market definition and symbol patterns
# ---------------------------------------------------------------------------


def test_ca_market_definition():
    """CA is Toronto time, one 09:30-16:00 session, named Canada."""
    md = MARKETS[MarketCode.CA]
    assert md.name == "Canada"
    assert md.timezone == "America/Toronto"
    assert len(md.sessions) == 1
    assert md.sessions[0].start.strftime("%H:%M") == "09:30"
    assert md.sessions[0].end.strftime("%H:%M") == "16:00"


@pytest.mark.parametrize("symbol", ["SHOP.TO", "RY.TO", "TD.TO", "XYZ.V", "CNQ.TO", "A.TO", "BAM-A.TO"])
def test_ca_symbol_pattern_accepts_yahoo_form(symbol):
    """The CA symbol pattern accepts Yahoo-form TSX / TSX Venture symbols."""
    assert re.match(MARKETS[MarketCode.CA].symbol_pattern, symbol)


@pytest.mark.parametrize("symbol", ["SHOP", "600519", "00700", "shop.to", "SHOP.TOO", "BTC-USD"])
def test_ca_symbol_pattern_rejects_other_forms(symbol):
    """The CA symbol pattern rejects US tickers, numeric CN/HK codes, lowercase and crypto."""
    assert not re.match(MARKETS[MarketCode.CA].symbol_pattern, symbol)


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "BF-B", "NVDA", "GOOGL"])
def test_us_symbol_pattern_allows_dot_and_hyphen(symbol):
    """The US pattern is loosened to allow dots and hyphens (BRK.B, BF-B)."""
    assert re.match(MARKETS[MarketCode.US].symbol_pattern, symbol)


def test_marketdata_symbol_detects_ca_before_us():
    """The marketdata package detects CA for .TO/.V codes before the US regex and keeps Yahoo form."""
    from marketdata.symbol import Market, Symbol

    for code in ("SHOP.TO", "RY.TO", "XYZ.V"):
        sym = Symbol.parse(code)
        assert sym.market == Market.CA
        assert sym.to_yfinance() == code
    assert Symbol.parse("BRK.B").market == Market.US
    assert Symbol.parse("BF-B").market == Market.US


def test_marketdata_yahoo_vendors_support_ca():
    """Both Yahoo vendors advertise CA so the engine routes CA quotes and klines to Yahoo with no other change."""
    from marketdata.vendors.kline import YahooKlineVendor
    from marketdata.vendors.yfinance import YFinanceQuoteVendor

    assert "CA" in YFinanceQuoteVendor.supports_markets
    assert "CA" in YahooKlineVendor.supports_markets


def test_engine_routes_ca_kline_to_yahoo_only(monkeypatch):
    """With the seeded kline sources, a CA request skips Tencent/Stooq/EastMoney and lands on Yahoo."""
    import marketdata.vendors.kline as kv
    from marketdata import MarketData, SourceConfig, StaticConfigProvider

    called = []

    def _fake(url, **k):
        called.append(url)
        return {"chart": {"result": [{"timestamp": [1782864000], "indicators": {"quote": [
            {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]}]}}]}}

    monkeypatch.setattr(kv, "market_get", _fake)
    cfg = StaticConfigProvider({"kline": [
        SourceConfig(vendor="tencent", priority=0, enabled=True, config={}),
        SourceConfig(vendor="eastmoney", priority=5, enabled=True, config={}),
        SourceConfig(vendor="stooq", priority=15, enabled=True, config={}),
        SourceConfig(vendor="yahoo", priority=20, enabled=True, config={}),
    ]})
    bars = MarketData(config=cfg).klines("SHOP.TO", market="CA", days=30)
    assert len(bars) == 1 and bars[0].close == 1.5
    assert len(called) == 1 and "finance.yahoo.com" in called[0] and called[0].endswith("/SHOP.TO")


# ---------------------------------------------------------------------------
# Discovery normalisation
# ---------------------------------------------------------------------------


def test_discovery_normalize_market():
    """discovery._normalize_market defaults to US, keeps CA, and maps CN/HK/unknown back to US."""
    from src.web.api.discovery import _normalize_market

    assert _normalize_market("") == "US"
    assert _normalize_market(None) == "US"
    assert _normalize_market("ca") == "CA"
    assert _normalize_market("US") == "US"
    assert _normalize_market("CN") == "US"
    assert _normalize_market("HK") == "US"
    assert _normalize_market("XX") == "US"


def test_dashboard_to_market_only_enabled():
    """dashboard._to_market accepts ALL/US/CA and maps CN/HK to ALL."""
    from src.web.api.dashboard import _to_market

    assert _to_market("") == "ALL"
    assert _to_market("ca") == "CA"
    assert _to_market("CN") == "ALL"
    assert _to_market("HK") == "ALL"


def test_api_parse_market_rejects_retired_markets():
    """quotes/klines/insights _parse_market default to US and reject CN/HK with HTTP 400."""
    from fastapi import HTTPException

    from src.web.api import insights, klines, quotes

    for mod in (quotes, klines, insights):
        assert mod._parse_market("") == MarketCode.US
        assert mod._parse_market("ca") == MarketCode.CA
        assert mod._parse_market("CRYPTO") == MarketCode.CRYPTO
        with pytest.raises(HTTPException) as ei:
            mod._parse_market("CN")
        assert ei.value.status_code == 400


def test_market_status_endpoint_hides_retired_markets():
    """/stocks/markets/status lists only enabled markets (no CN/HK rows)."""
    from src.web.api.stocks import get_market_status

    rows = get_market_status()
    codes = {r["market"] if "market" in r else r.get("code") for r in rows}
    joined = " ".join(str(r) for r in rows)
    assert "'CN'" not in joined and "'HK'" not in joined
    assert "US" in joined and "CA" in joined
    assert len(rows) == len(ENABLED_MARKETS)


# ---------------------------------------------------------------------------
# Paper trading allocation across enabled markets
# ---------------------------------------------------------------------------


def test_paper_allocation_defaults_and_disabled_markets():
    """Allocations are keyed by the enabled equity markets; legacy CN/HK weights become zero and re-normalise."""
    from src.core.paper_trading_engine import (
        ALL_MARKETS,
        DEFAULT_ALLOCATIONS,
        market_allocations_or_default,
        normalize_allocations,
    )

    assert ALL_MARKETS == ("US", "CA")
    assert DEFAULT_ALLOCATIONS == {"US": 0.6, "CA": 0.4}
    assert normalize_allocations({"CN": 0.5, "HK": 0.3, "US": 0.2}) == {"US": 1.0, "CA": 0.0}
    assert normalize_allocations({"US": 0.6, "CA": 0.4}) == {"US": 0.6, "CA": 0.4}
    acc = SimpleNamespace(market_allocations={"CN": 0.5, "HK": 0.5})
    assert market_allocations_or_default(acc) == {"US": 0.6, "CA": 0.4}


# ---------------------------------------------------------------------------
# Stock search: universe directory first, yfinance Search top-up
# ---------------------------------------------------------------------------


_YAHOO_QUOTES = [
    {"symbol": "SHOP", "shortname": "Shopify Inc.", "exchange": "NMS", "quoteType": "EQUITY"},
    {"symbol": "SHOP.TO", "shortname": "SHOPIFY INC", "longname": "Shopify Inc.", "exchange": "TOR", "quoteType": "EQUITY"},
    {"symbol": "SHHI.NE", "shortname": "NINEPOINT SHOPIFY ETF", "exchange": "NEO", "quoteType": "ETF"},
    {"symbol": "307.F", "shortname": "Shopify Inc.", "exchange": "FRA", "quoteType": "EQUITY"},
    {"symbol": "0700.HK", "shortname": "Tencent", "exchange": "HKG", "quoteType": "EQUITY"},
    {"symbol": "600519.SS", "shortname": "Moutai", "exchange": "SHH", "quoteType": "EQUITY"},
    {"symbol": "SHOP260117C00100000", "shortname": "opt", "exchange": "OPR", "quoteType": "OPTION"},
    {"symbol": "XYZ.V", "shortname": "Venture Co", "exchange": "VAN", "quoteType": "EQUITY"},
    {"symbol": "ABC.CN", "shortname": "CSE Co", "exchange": "CNQ", "quoteType": "EQUITY"},
    {"symbol": "IBM", "shortname": "IBM", "exchange": "NYQ", "quoteType": "EQUITY"},
    {"symbol": "BRK-B", "shortname": "Berkshire Hathaway Inc.", "exchange": "NYQ", "quoteType": "EQUITY"},
]


@pytest.fixture
def empty_universe():
    """Search tests run against an empty universe (fail-open) unless they install rows."""
    from src.core import universe

    universe.reset()
    yield universe
    universe.reset()


def _install_rows(universe, rows: list[dict]) -> None:
    import time

    full = []
    for r in rows:
        full.append({
            "symbol": r["symbol"], "yahoo_symbol": r.get("yahoo_symbol") or r["symbol"],
            "exchange_symbol": r.get("exchange_symbol") or r["symbol"], "name": r["name"],
            "market": r["market"], "exchange": r.get("exchange") or "NYSE",
            "is_etf": bool(r.get("is_etf")),
        })
    universe._install(full, {"US": time.time(), "CA": time.time()}, time.time())


def test_yahoo_search_mapping_exchanges_and_types(empty_universe):
    """Exchange → market mapping: TOR/VAN → CA (CSE/NEO dropped), US exchanges → US with BRK-B folded to BRK.B, only EQUITY/ETF, never CN/HK."""
    from src.web.stock_list import YAHOO_CA_EXCHANGES, map_yahoo_search_quotes

    assert YAHOO_CA_EXCHANGES == {"TOR", "VAN"}
    rows = map_yahoo_search_quotes(_YAHOO_QUOTES, "", limit=20)
    by_symbol = {r["symbol"]: r["market"] for r in rows}
    assert by_symbol == {"SHOP": "US", "SHOP.TO": "CA", "XYZ.V": "CA", "IBM": "US", "BRK.B": "US"}
    assert all(r["market"] in ("US", "CA") for r in rows)
    ca_only = map_yahoo_search_quotes(_YAHOO_QUOTES, "CA", limit=1)
    assert [r["symbol"] for r in ca_only] == ["SHOP.TO"]


def test_yahoo_search_mapping_filters_through_universe(empty_universe):
    """When the universe is loaded, mapped rows not in the directory are dropped and directory metadata is attached."""
    from src.web.stock_list import map_yahoo_search_quotes

    _install_rows(empty_universe, [
        {"symbol": "SHOP", "name": "Shopify Inc.", "market": "US", "exchange": "NASDAQ"},
        {"symbol": "BRK.B", "yahoo_symbol": "BRK-B", "name": "Berkshire Hathaway", "market": "US"},
        {"symbol": "SHOP.TO", "exchange_symbol": "SHOP", "name": "Shopify Inc.", "market": "CA", "exchange": "TSX"},
    ])
    rows = map_yahoo_search_quotes(_YAHOO_QUOTES, "", limit=20)
    assert [r["symbol"] for r in rows] == ["SHOP", "SHOP.TO", "BRK.B"]
    assert rows[0]["exchange"] == "NASDAQ" and rows[0]["is_etf"] is False
    assert rows[1]["market"] == "CA"


def test_yfinance_search_uses_adapter(monkeypatch, empty_universe):
    """_yfinance_search calls yf_adapter.search with max(limit*2, 20) and maps the quotes; errors -> []."""
    from marketdata.vendors import yf_adapter
    from src.web import stock_list

    captured = {}

    def _fake_search(query, *, max_results=20):
        captured["query"] = query
        captured["max_results"] = max_results
        return _YAHOO_QUOTES

    monkeypatch.setattr(yf_adapter, "search", _fake_search)
    rows = stock_list._yfinance_search("shopify", "CA", limit=5)
    assert captured == {"query": "shopify", "max_results": 20}
    assert [r["symbol"] for r in rows] == ["SHOP.TO", "XYZ.V"]
    assert stock_list._yfinance_search("shopify", "US", limit=30) and captured["max_results"] == 60

    def _boom(query, *, max_results=20):
        raise RuntimeError("offline")

    monkeypatch.setattr(yf_adapter, "search", _boom)
    assert stock_list._yfinance_search("shopify", "US", limit=5) == []
    assert stock_list._yfinance_search("", "US", limit=5) == []


def test_search_stocks_universe_first_then_yfinance_topup(monkeypatch, empty_universe):
    """search_stocks: universe rows first; yfinance tops up when short (q >= 2 chars); CN/HK never return rows."""
    from src.web import stock_list

    _install_rows(empty_universe, [
        {"symbol": "SHOP", "name": "Shopify Inc.", "market": "US", "exchange": "NASDAQ"},
        {"symbol": "SHOP.TO", "exchange_symbol": "SHOP", "name": "Shopify Inc.", "market": "CA", "exchange": "TSX"},
        {"symbol": "IBM", "name": "International Business Machines", "market": "US", "exchange": "NYSE"},
        {"symbol": "XYZ.V", "exchange_symbol": "XYZ", "name": "Venture Co", "market": "CA", "exchange": "TSXV"},
    ])
    calls = []

    def _fake_yf(query, market="", limit=20):
        calls.append((query, market, limit))
        return stock_list.map_yahoo_search_quotes(_YAHOO_QUOTES, market, limit)

    monkeypatch.setattr(stock_list, "_yfinance_search", _fake_yf)

    ca = stock_list.search_stocks("shop", "CA")
    # Universe row first, then the yfinance top-up (SHOP.TO de-duplicated, XYZ.V is in the universe).
    assert [r["symbol"] for r in ca] == ["SHOP.TO", "XYZ.V"]
    assert ("shop", "CA", 20) in calls

    us = stock_list.search_stocks("shop", "US", limit=5)
    # Universe: SHOP; top-up adds IBM (in the universe), SHOP de-duplicated, BRK.B dropped (not in the universe).
    assert [r["symbol"] for r in us] == ["SHOP", "IBM"]

    calls.clear()
    assert [r["symbol"] for r in stock_list.search_stocks("ibm", "US", limit=1)] == ["IBM"]
    assert calls == []  # already full -> no yfinance call
    one = stock_list.search_stocks("i", "US")
    assert [r["symbol"] for r in one] == ["IBM", "SHOP"]  # symbol prefix before name-word prefix ("Inc.")
    assert calls == []  # a single character never hits yfinance

    assert stock_list.search_stocks("600519", "CN") == []
    assert stock_list.search_stocks("00700", "HK") == []
    assert stock_list.search_stocks("", "US") == []


def test_search_stocks_all_markets_merges_crypto_gold(monkeypatch, empty_universe):
    """market="" merges universe + yfinance + the crypto/gold static list; CRYPTO/GOLD markets bypass the universe."""
    from src.web import stock_list

    monkeypatch.setattr(stock_list, "_yfinance_search", lambda q, market="", limit=20: [])
    rows = stock_list.search_stocks("sol", "")
    assert {(r["symbol"], r["market"]) for r in rows} == {("SOL-USD", "CRYPTO")}
    assert stock_list.search_stocks("gold", "GOLD") == [{"symbol": "XAUUSD", "name": "Gold Spot", "market": "GOLD"}]
    assert stock_list.search_stocks("PEPE-USD", "CRYPTO")[0] == {"symbol": "PEPE-USD", "name": "PEPE", "market": "CRYPTO"}


def test_english_name_resolves_cjk_via_universe(empty_universe):
    """_english_name keeps ASCII names, resolves CJK names from the universe, else falls back to the symbol."""
    from src.web.stock_list import _english_name

    assert _english_name("AAPL", "Apple") == "Apple"
    assert _english_name("AAPL", "苹果") == "AAPL"
    _install_rows(empty_universe, [{"symbol": "AAPL", "name": "Apple Inc.", "market": "US", "exchange": "NASDAQ"}])
    assert _english_name("AAPL", "苹果") == "Apple Inc."
    assert _english_name("ZZZZ", "未知") == "ZZZZ"


def test_refresh_stock_list_returns_universe_stats(monkeypatch, empty_universe):
    """refresh_stock_list forces a universe refresh and returns its stats dict."""
    from src.core import universe
    from src.web import stock_list

    captured = {}

    def _fake_refresh(force=False):
        captured["force"] = force
        return {"count": 3, "by_market": {"US": 2, "CA": 1}, "refreshed": {"US": 1.0, "CA": 2.0}}

    monkeypatch.setattr(universe, "refresh_blocking", _fake_refresh)
    assert stock_list.refresh_stock_list()["count"] == 3
    assert captured == {"force": True}


def test_stock_list_module_has_no_legacy_fetchers():
    """The legacy EastMoney/akshare/raw-Yahoo seams are gone from stock_list."""
    from src.web import stock_list

    for name in ("_realtime_search", "_cached_search", "get_stock_list", "_yahoo_search",
                 "YAHOO_SEARCH_URL", "HEADERS", "YAHOO_HEADERS", "_resolve_proxy",
                 "_ENGLISH_NAME_OVERRIDES", "httpx"):
        assert not hasattr(stock_list, name), name


# ---------------------------------------------------------------------------
# FX (USD base) and index strip
# ---------------------------------------------------------------------------


def test_fx_rate_for_market_usd_base():
    """US converts at 1.0, CA at the CAD→USD rate, everything else at 1.0; the rates payload exposes CAD_USD."""
    from src.web.api import accounts

    assert accounts.fx_rate_for_market("US", 0.73) == 1.0
    assert accounts.fx_rate_for_market("CA", 0.73) == 0.73
    assert accounts.fx_rate_for_market("CRYPTO", 0.73) == 1.0
    assert accounts.fx_rate_for_market("HK", 0.73) == 1.0
    payload = accounts.exchange_rates_payload(0.73)
    assert payload["CAD_USD"] == 0.73 and payload["base_currency"] == "USD"
    assert not any("CNY" in k for k in payload)


def test_get_cad_usd_rate_fail_soft(monkeypatch):
    """With both rate sources down the CAD→USD rate falls back to the cached constant and backs off."""
    from src.web.api import accounts

    def _boom(*a, **k):
        raise RuntimeError("offline")

    # Both sources must be stubbed: Bank of Canada (httpx) AND the Yahoo macro fallback,
    # otherwise the test reaches the live network and gets a real rate.
    monkeypatch.setattr(accounts.httpx, "get", _boom)
    monkeypatch.setattr(accounts, "_fetch_cad_usd_macro", _boom)
    monkeypatch.setattr(accounts, "_cad_usd_rate_cache", {"rate": accounts._CAD_USD_FALLBACK, "ts": 0})
    rate = accounts.get_cad_usd_rate()
    assert rate == accounts._CAD_USD_FALLBACK
    assert accounts._cad_usd_rate_cache["ts"] > 0  # backoff recorded


def test_index_strip_is_us_and_tsx_only():
    """The index strip is S&P 500, Nasdaq Composite, Dow Jones and S&P/TSX Composite; no CN/HK indices."""
    import src.web.api.market as mkt

    names = [i["name"] for i in mkt.MARKET_INDICES]
    assert names == ["S&P 500", "Nasdaq Composite", "Dow Jones", "S&P/TSX Composite"]
    assert {i["market"] for i in mkt.MARKET_INDICES} == {"US", "CA"}
    tsx = next(i for i in mkt.MARKET_INDICES if i["symbol"] == "GSPTSE")
    assert tsx["tencent_symbol"] is None and tsx["yahoo_symbol"] == "^GSPTSE"


def test_market_indices_tencent_failure_falls_back_to_yahoo(monkeypatch):
    """If Tencent raises, every index is fetched from Yahoo (fail-soft, no exception)."""
    import src.web.api.market as mkt

    mkt.clear_indices_cache()

    class _MD:
        def index_quotes(self, tencent_symbols):
            raise RuntimeError("tencent down")

        def yahoo_index_quotes(self, yahoo_symbols):
            return [{"symbol": s, "name": s, "current_price": 100.0, "change_pct": 1.0,
                     "change_amount": 1.0, "prev_close": 99.0} for s in yahoo_symbols]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", lambda *a, **k: [])
    out = asyncio.run(mkt.get_market_indices())
    assert [i["symbol"] for i in out] == ["INX", "IXIC", "DJI", "GSPTSE"]
    assert all(i["current_price"] == 100.0 for i in out)
    mkt.clear_indices_cache()


# ---------------------------------------------------------------------------
# Defaults that used to be CN
# ---------------------------------------------------------------------------


def test_default_schedules_follow_us_session_in_vancouver():
    """Seeded agent schedules follow the US session in America/Vancouver (05:45 / every 5 min 06-13 / 13:30)."""
    from src.core.agent_catalog import AGENT_SEED_SPECS

    by_name = {s.name: s.schedule for s in AGENT_SEED_SPECS}
    assert by_name["premarket_outlook"] == "45 5 * * 1-5"
    assert by_name["intraday_monitor"] == "*/5 6-13 * * 1-5"
    assert by_name["daily_report"] == "30 13 * * 1-5"


def test_timezone_default_is_vancouver(monkeypatch):
    """The app timezone defaults to America/Vancouver when TZ/APP_TIMEZONE are unset."""
    from src.core import timezone as tzmod

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    assert str(tzmod._get_app_tz()) == "America/Vancouver"


def test_orm_market_column_default_is_us():
    """ORM market columns default to the deployment default market (US), not CN."""
    from src.web import models

    assert models.DEFAULT_MARKET == "US"
    assert models.PaperTradingPosition.__table__.columns["stock_market"].default.arg == "US"
    assert models.StrategySignalRun.__table__.columns["stock_market"].default.arg == "US"
    assert models.EntryCandidate.__table__.columns["stock_market"].default.arg == "US"


def test_prune_script_is_list_only_by_default():
    """scripts/prune_disabled_markets.py lists rows by default and only deletes with --yes."""
    from pathlib import Path

    src = Path("scripts/prune_disabled_markets.py").read_text()
    assert '"--yes"' in src
    assert "if not args.yes:" in src
    assert "Re-run with --yes" in src
