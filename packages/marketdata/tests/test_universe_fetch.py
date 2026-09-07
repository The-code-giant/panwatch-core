"""universe.py: Nasdaq Trader / TMX directory parsing and the two fetchers (URL-keyed market_get dispatcher)."""

from __future__ import annotations

import pytest

import marketdata.universe as uni
from marketdata.errors import VendorError

_NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ZAZZT|Nasdaq Test Symbol|Q|Y|N|100|N|N
AGNCN|AGNC Investment Corp. - Depositary Shares Each Representing a 1/1,000th Interest in a Share of 7.00% Series C Fixed-To-Floating Rate Cumulative Redeemable Preferred Stock|G|N|N|100|N|N
ACABW|Atlantic Coastal Acquisition Corp. II - Warrant|G|N|N|100|N|N
File Creation Time: 0904202521:31|||||||
"""

_OTHER = """ACT Symbol|Security Name|Exchange|CUSIP|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B|N|084670702|N|100|N|BRK=B
SPY|SPDR S&P 500 ETF Trust|P|78462F103|Y|100|N|SPY
BAC$B|Bank of America Corporation Depositary Shares 6.000% Non-Cumulative Preferred Stock Series GG|N|060505682|N|100|N|BAC-B
ACP+|Abrdn Income Credit Strategies Fund Rights|N|00344M209|N|100|N|ACP+
NYT|New York Times Company (The) Common Stock|N|650111107|N|100|Y|NYT
XYZU|Some SPAC Units|A|000000000|N|100|N|XYZU
TSM|Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares|N|874039100|N|100|N|TSM
ZZZ|Unknown Exchange Co|X|000000001|N|100|N|ZZZ
File Creation Time: 0904202521:31|||||||
"""

_TMX = {"results": [
    {"symbol": "CTC", "name": "Canadian Tire Corporation, Limited", "instruments": [
        {"symbol": "CTC", "name": "Canadian Tire Corporation, Limited"},
        {"symbol": "CTC.A", "name": "Canadian Tire Corporation, Limited Class A Non-Voting Shares"},
    ]},
    {"symbol": "BCE", "name": "BCE Inc.", "instruments": [
        {"symbol": "BCE", "name": "BCE Inc."},
        {"symbol": "BCE.PR.A", "name": "BCE Inc. First Preferred Shares Series AA"},
        {"symbol": "BCE.WT", "name": "BCE Inc. Warrants"},
        {"symbol": "BCE.DB.A", "name": "BCE Inc. Debentures"},
    ]},
    {"symbol": "XIU", "name": "iShares S&P/TSX 60 Index ETF", "instruments": [
        {"symbol": "XIU", "name": "iShares S&P/TSX 60 Index ETF"}]},
    {"symbol": "BPO", "name": "Brookfield Office Properties", "instruments": [
        {"symbol": "BPO.PR.A", "name": "Brookfield Office Properties Pref"}]},
    {"symbol": "REI", "name": "RioCan Real Estate Investment Trust", "instruments": [
        {"symbol": "REI.UN", "name": "RioCan Real Estate Investment Trust"}]},
    {"symbol": "SHOP", "name": "Shopify Inc."},  # no instruments[] -> issuer symbol used
]}


def test_parse_nasdaq_listed():
    """Test issues, preferreds and warrants are dropped; ETF flag mapped; exchange NASDAQ."""
    rows = uni.parse_nasdaq_listed(_NASDAQ)
    assert [r.symbol for r in rows] == ["AAPL", "QQQ"]
    aapl, qqq = rows
    assert aapl.exchange == "NASDAQ" and aapl.market == "US" and not aapl.is_etf
    assert aapl.yahoo_symbol == "AAPL" and aapl.exchange_symbol == "AAPL"
    assert aapl.name == "Apple Inc. - Common Stock"
    assert qqq.is_etf


def test_parse_other_listed():
    """BRK.B keeps the exchange spelling with Yahoo BRK-B; $/+ symbols, units, test issues and unknown
    exchanges are dropped; ADRs kept; exchange codes mapped to names."""
    rows = uni.parse_other_listed(_OTHER)
    by = {r.symbol: r for r in rows}
    assert list(by) == ["BRK.B", "SPY", "TSM"]
    assert by["BRK.B"].yahoo_symbol == "BRK-B" and by["BRK.B"].exchange_symbol == "BRK.B"
    assert by["BRK.B"].exchange == "NYSE" and not by["BRK.B"].is_etf
    assert by["SPY"].exchange == "NYSE Arca" and by["SPY"].is_etf
    assert by["TSM"].exchange == "NYSE"


def test_parse_tmx_directory():
    """instruments[] are flattened; PR/WT/DB series dropped; .A/.UN kept in Yahoo spelling with .TO/.V;
    ETF flag from the name; issuers without instruments use their own symbol."""
    rows = uni.parse_tmx_directory(_TMX, "tsx")
    assert [r.symbol for r in rows] == ["CTC.TO", "CTC-A.TO", "BCE.TO", "XIU.TO", "REI-UN.TO", "SHOP.TO"]
    ctca = rows[1]
    assert ctca.exchange_symbol == "CTC.A" and ctca.yahoo_symbol == "CTC-A.TO"
    assert ctca.market == "CA" and ctca.exchange == "TSX" and not ctca.is_etf
    assert rows[3].is_etf and not rows[2].is_etf
    v = uni.parse_tmx_directory({"results": [{"symbol": "XYZ", "name": "Xyz Corp", "instruments": [{"symbol": "XYZ", "name": "Xyz Corp"}]}]}, "tsxv")
    assert v[0].symbol == "XYZ.V" and v[0].exchange == "TSXV"
    with pytest.raises(ValueError):
        uni.parse_tmx_directory(_TMX, "cse")


def _dispatcher(monkeypatch, responses: dict, calls: list | None = None):
    def fake_get(url, **kw):
        if calls is not None:
            calls.append((url, kw))
        return responses.get(url)
    monkeypatch.setattr(uni, "market_get", fake_get)


def test_fetch_us_listings(monkeypatch):
    """Both files are downloaded and merged; one failing file is tolerated; both failing raises."""
    calls: list = []
    _dispatcher(monkeypatch, {uni.NASDAQ_LISTED_URL: _NASDAQ, uni.OTHER_LISTED_URL: _OTHER}, calls)
    rows = uni.fetch_us_listings()
    assert [r.symbol for r in rows] == ["AAPL", "QQQ", "BRK.B", "SPY", "TSM"]
    assert {c[1]["host_key"] for c in calls} == {"www.nasdaqtrader.com"}
    assert all(c[1]["parse"] == "text" for c in calls)

    _dispatcher(monkeypatch, {uni.NASDAQ_LISTED_URL: _NASDAQ})
    assert [r.symbol for r in uni.fetch_us_listings()] == ["AAPL", "QQQ"]

    _dispatcher(monkeypatch, {})
    with pytest.raises(VendorError):
        uni.fetch_us_listings()


def test_fetch_ca_listings(monkeypatch):
    """TSX and TSXV directories are fetched with the right exchange in the URL and merged; proxy is passed through."""
    calls: list = []
    tsxv = {"results": [{"symbol": "ABC", "name": "Abc Ventures", "instruments": [{"symbol": "ABC", "name": "Abc Ventures"}]}]}
    _dispatcher(monkeypatch, {
        uni.TMX_DIRECTORY_URL.format(exchange="tsx"): _TMX,
        uni.TMX_DIRECTORY_URL.format(exchange="tsxv"): tsxv,
    }, calls)
    rows = uni.fetch_ca_listings(proxy="http://proxy:8080")
    assert rows[-1].symbol == "ABC.V" and rows[-1].exchange == "TSXV"
    assert "SHOP.TO" in {r.symbol for r in rows}
    assert all(c[1]["proxy"] == "http://proxy:8080" and c[1]["parse"] == "json" for c in calls)
    assert [c[0] for c in calls] == [uni.TMX_DIRECTORY_URL.format(exchange="tsx"), uni.TMX_DIRECTORY_URL.format(exchange="tsxv")]

    _dispatcher(monkeypatch, {})
    with pytest.raises(VendorError):
        uni.fetch_ca_listings()
