"""DiscoveryVendor: Yahoo screeners with exchange filtering, Nasdaq / TMX fallbacks, sector boards, cache."""

from __future__ import annotations

import pytest
from _yf_fakes import patch_yf

import marketdata.vendors.discovery as dv
from marketdata.errors import VendorError
from marketdata.types import HotBoard, HotStock


@pytest.fixture(autouse=True)
def _reset_cache():
    dv.DiscoveryVendor.reset_cache()
    yield
    dv.DiscoveryVendor.reset_cache()


def _q(symbol, exchange, price, pct, vol, quote_type="EQUITY", name=None):
    return {"symbol": symbol, "exchange": exchange, "quoteType": quote_type,
            "shortName": name or f"{symbol} Inc", "regularMarketPrice": price,
            "regularMarketChangePercent": pct, "regularMarketVolume": vol}


_US_ROWS = [
    _q("NVDA", "NMS", 120.0, 3.5, 300_000_000),
    _q("BRK-B", "NYQ", 450.0, -0.5, 4_000_000),
    _q("SNDL", "NCM", 2.0, 8.0, 50_000_000),
    _q("OTCX", "PNK", 0.01, 50.0, 900_000_000),     # OTC pink -> dropped
    _q("CRYPTO", "CCC", 1.0, 1.0, 1, quote_type="CRYPTOCURRENCY"),
    _q("SPY", "PCX", 500.0, 0.2, 60_000_000, quote_type="ETF"),
    {"symbol": "", "exchange": "NMS", "quoteType": "EQUITY"},
]


def test_us_hot_stocks_filters_and_maps(monkeypatch):
    """US rows keep only EQUITY/ETF on listed exchanges, map BRK-B -> BRK.B, derive turnover, and sort by mode."""
    calls = patch_yf(monkeypatch, screens={"most_actives": _US_ROWS, "day_gainers": _US_ROWS, "day_losers": _US_ROWS})
    v = dv.DiscoveryVendor()
    out = v.hot_stocks(market="US", mode="turnover", limit=10)
    assert calls["screens"] == ["most_actives"]
    assert [s.symbol for s in out] == ["NVDA", "SPY", "BRK.B", "SNDL"]
    nv = out[0]
    assert isinstance(nv, HotStock) and nv.market == "US" and nv.name == "NVDA Inc"
    assert nv.price == 120.0 and nv.change_pct == 3.5 and nv.volume == 300_000_000
    assert nv.turnover == 120.0 * 300_000_000
    assert [s.symbol for s in v.hot_stocks(market="US", mode="gainers", limit=2)] == ["SNDL", "NVDA"]
    assert [s.symbol for s in v.hot_stocks(market="US", mode="losers", limit=2)] == ["BRK.B", "SPY"]
    assert calls["screens"] == ["most_actives", "day_gainers", "day_losers"]


def test_us_hot_stocks_cache_and_size(monkeypatch):
    """The same market/mode/size is served from the 5-minute cache; size = min(250, max(limit, 100))."""
    sizes: list[int] = []

    def screens(query, **kw):
        sizes.append(kw["size"])
        return {"quotes": _US_ROWS}

    calls = patch_yf(monkeypatch, screens=screens)
    v = dv.DiscoveryVendor()
    v.hot_stocks(market="US", mode="turnover", limit=20)
    v.hot_stocks(market="US", mode="turnover", limit=5)
    assert calls["screens"] == ["most_actives"] and sizes == [100]
    v.hot_stocks(market="US", mode="turnover", limit=300)
    assert sizes == [100, 250]


def test_us_hot_stocks_nasdaq_fallback(monkeypatch):
    """When the Yahoo screen fails or is empty the Nasdaq screener rows are used ($ and % stripped)."""
    def screens(query, **kw):
        raise RuntimeError("yahoo down")

    patch_yf(monkeypatch, screens=screens)
    payload = {"data": {"rows": [
        {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$200.00", "netchange": "1.00",
         "pctchange": "0.50%", "volume": "50,000,000", "marketCap": "3,000,000,000,000"},
        {"symbol": "TSLA", "name": "Tesla, Inc. Common Stock", "lastsale": "$250.00", "netchange": "-5.00",
         "pctchange": "-1.96%", "volume": "90,000,000", "marketCap": "800,000,000,000"},
        {"symbol": "BAC$B", "name": "Preferred", "lastsale": "$25.00", "pctchange": "0.00%", "volume": "1"},
    ]}}
    seen: list = []

    def fake_get(url, **kw):
        seen.append((url, kw))
        return payload

    monkeypatch.setattr(dv, "market_get", fake_get)
    out = dv.DiscoveryVendor().hot_stocks(market="US", mode="turnover", limit=10)
    assert seen[0][0] == dv.NASDAQ_SCREENER_URL and seen[0][1]["params"]["download"] == "true"
    assert [s.symbol for s in out] == ["TSLA", "AAPL"]
    assert out[1].price == 200.0 and out[1].change_pct == 0.5 and out[1].volume == 50_000_000
    assert out[1].turnover == 200.0 * 50_000_000
    assert [s.symbol for s in dv.DiscoveryVendor().hot_stocks(market="US", mode="gainers", limit=10)] == ["AAPL", "TSLA"]


_CA_ROWS = [
    _q("SHOP.TO", "TOR", 100.0, 2.0, 3_000_000, name="Shopify Inc."),
    _q("RY.TO", "TOR", 150.0, -1.0, 2_000_000),
    _q("XYZ.V", "VAN", 0.8, 12.0, 5_000_000),
    _q("HIVE.CN", "CNQ", 3.0, 30.0, 9_000_000),   # CSE -> dropped
    _q("NEO.NE", "NEO", 3.0, 30.0, 9_000_000),    # NEO -> dropped
]


def test_ca_hot_stocks_equity_query(monkeypatch):
    """CA uses an EquityQuery (region ca, TOR/VAN, floors) in dict form; gainers/losers add the market-cap floor;
    only TOR/VAN rows survive and the symbol keeps the Yahoo suffix."""
    queries: list = []

    def screens(query, **kw):
        queries.append((query, kw))
        return {"quotes": _CA_ROWS}

    patch_yf(monkeypatch, screens=screens)
    v = dv.DiscoveryVendor()
    out = v.hot_stocks(market="CA", mode="turnover", limit=10)
    assert [s.symbol for s in out] == ["SHOP.TO", "RY.TO", "XYZ.V"]
    assert out[0].market == "CA" and out[0].name == "Shopify Inc." and out[0].turnover == 100.0 * 3_000_000
    q, kw = queries[0]
    assert q["operator"] == "and" and kw["sortField"] == "dayvolume" and kw["sortAsc"] is False
    ops = q["operands"]
    assert {"operator": "eq", "operands": ["region", "ca"]} in ops
    assert {"operator": "is-in", "operands": ["exchange", "TOR", "VAN"]} in ops
    assert {"operator": "gt", "operands": ["dayvolume", 50_000]} in ops
    assert {"operator": "gte", "operands": ["intradayprice", 0.5]} in ops
    assert not any(o["operands"][0] == "intradaymarketcap" for o in ops)

    assert [s.symbol for s in v.hot_stocks(market="CA", mode="gainers", limit=10)] == ["XYZ.V", "SHOP.TO", "RY.TO"]
    q2, kw2 = queries[1]
    assert {"operator": "gte", "operands": ["intradaymarketcap", 50_000_000]} in q2["operands"]
    assert kw2["sortField"] == "percentchange" and kw2["sortAsc"] is False
    v.hot_stocks(market="CA", mode="losers", limit=10)
    assert queries[2][1]["sortAsc"] is True


def test_ca_hot_stocks_tmx_fallback(monkeypatch):
    """When the Yahoo screen is empty the TMX GraphQL market movers (TSX then TSXV) are used with the pinned query."""
    patch_yf(monkeypatch, screens={"*": []})
    posts: list = []

    def fake_post(url, **kw):
        posts.append((url, kw))
        exch = kw["json"]["variables"]["statExchange"]
        rows = ([{"symbol": "CNQ", "name": "Canadian Natural Resources Limited", "price": 69.78, "percentChange": -1.09, "volume": 34_665_865},
                 {"symbol": "CTC.A", "name": "Canadian Tire", "price": 150.0, "percentChange": 1.5, "volume": 100_000}]
                if exch == "TSX" else
                [{"symbol": "XYZ", "name": "Xyz Ventures", "price": 0.9, "percentChange": 10.0, "volume": 2_000_000}])
        return {"data": {"getMarketMovers": rows}}

    monkeypatch.setattr(dv, "market_post", fake_post)
    out = dv.DiscoveryVendor().hot_stocks(market="CA", mode="turnover", limit=10)
    assert [s.symbol for s in out] == ["CNQ.TO", "CTC-A.TO", "XYZ.V"]
    assert out[0].price == 69.78 and out[0].volume == 34_665_865 and out[0].market == "CA"
    assert [p[1]["json"]["variables"]["statExchange"] for p in posts] == ["TSX", "TSXV"]
    body = posts[0][1]["json"]
    assert body["operationName"] == "getMarketMovers" and body["query"] == dv.GETMARKETMOVERS_QUERY
    assert "locale" not in body["variables"] and body["variables"]["sortOrder"] == "desc"
    assert posts[0][0] == dv.TMX_GRAPHQL_URL and posts[0][1]["host_key"] == "app-money.tmx.com"


def test_hot_stocks_unknown_market_and_empty(monkeypatch):
    """Unsupported markets return [] without any call; every source empty -> []."""
    calls = patch_yf(monkeypatch, screens={"*": []})
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: None)
    monkeypatch.setattr(dv, "market_post", lambda *a, **k: None)
    v = dv.DiscoveryVendor()
    assert v.hot_stocks(market="CN", mode="turnover", limit=10) == []
    assert calls["screens"] == []
    assert v.hot_stocks(market="US", mode="turnover", limit=10) == []
    assert v.hot_stocks(market="CA", mode="gainers", limit=10) == []


def test_us_sector_boards(monkeypatch):
    """hot_boards(US) is one batched download of the 11 sector ETFs -> HotBoard rows sorted by mode; CA -> []."""
    download = {"XLK": {"last": 210.0, "prev_close": 200.0}, "XLE": {"last": 90.0, "prev_close": 100.0},
                "XLF": {"last": 50.0, "prev_close": 50.0}}
    calls = patch_yf(monkeypatch, download=download)
    v = dv.DiscoveryVendor()
    out = v.hot_boards(market="US", mode="gainers", limit=12)
    assert calls["download"] == [[etf for _, _, etf in dv.US_SECTORS]]
    assert [b.code for b in out] == ["US_SECTOR_technology", "US_SECTOR_financial-services", "US_SECTOR_energy"]
    b = out[0]
    assert isinstance(b, HotBoard) and b.name == "Technology" and abs(b.change_pct - 5.0) < 1e-9
    assert b.change_amount == 10.0 and b.turnover is None
    assert [b.code for b in v.hot_boards(market="US", mode="losers", limit=1)] == ["US_SECTOR_energy"]
    assert len(calls["download"]) == 1  # cached
    assert v.hot_boards(market="CA", mode="gainers", limit=12) == []


def test_board_stocks_sector_screen(monkeypatch):
    """board_stocks(US_SECTOR_<slug>) screens region us + sector with the floors and sorts by mode; unknown codes -> []."""
    queries: list = []

    def screens(query, **kw):
        queries.append((query, kw))
        return {"quotes": _US_ROWS}

    patch_yf(monkeypatch, screens=screens)
    v = dv.DiscoveryVendor()
    out = v.board_stocks(board_code="US_SECTOR_technology", mode="gainers", limit=3)
    assert [s.symbol for s in out] == ["SNDL", "NVDA", "SPY"]
    q, kw = queries[0]
    ops = q["operands"]
    assert {"operator": "eq", "operands": ["region", "us"]} in ops
    assert {"operator": "eq", "operands": ["sector", "Technology"]} in ops
    assert {"operator": "gte", "operands": ["intradaymarketcap", 2_000_000_000]} in ops
    assert {"operator": "gt", "operands": ["dayvolume", 100_000]} in ops
    assert kw["sortField"] == "percentchange" and kw["sortAsc"] is False
    assert v.board_stocks(board_code="US_SECTOR_nope", mode="gainers", limit=3) == []
    assert v.board_stocks(board_code="CN_hot", mode="gainers", limit=3) == []
    assert v.board_stocks(board_code="", mode="gainers", limit=3) == []
    assert len(queries) == 1


def test_proxy_is_forwarded_to_adapter(monkeypatch):
    """A proxy string reaches yf_adapter.configure before the screen call."""
    seen: list = []
    monkeypatch.setattr(dv.yf_adapter, "configure", lambda proxy=None: seen.append(proxy))
    patch_yf(monkeypatch, screens={"most_actives": _US_ROWS})
    dv.DiscoveryVendor().hot_stocks(market="US", mode="turnover", limit=5, proxy="http://p:1")
    assert seen == ["http://p:1"]


def test_screen_vendor_error_propagates_to_fallback_only(monkeypatch):
    """A VendorError from the adapter (breaker open) is swallowed into the fallback path, never raised to the caller."""
    def screens(query, **kw):
        raise VendorError("Yahoo Finance circuit breaker open")

    patch_yf(monkeypatch, screens=screens)
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: None)
    assert dv.DiscoveryVendor().hot_stocks(market="US", mode="gainers", limit=5) == []
