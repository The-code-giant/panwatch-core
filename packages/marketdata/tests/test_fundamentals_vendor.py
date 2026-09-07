"""Fundamentals vendors: Yahoo info mapping (incl. dividend rule and currency) and SEC XBRL companyfacts."""

from __future__ import annotations

import pytest
from _yf_fakes import FakeTicker, patch_yf

import marketdata.vendors._sec as sec
from marketdata.symbol import Symbol
from marketdata.vendors import yf_adapter
from marketdata.vendors.fundamentals import SecEdgarFundamentalsVendor, YFinanceFundamentalsVendor

_INFO = {
    "shortName": "Apple Inc.", "longName": "Apple Inc. (long)", "currency": "USD",
    "regularMarketPrice": 200.0, "marketCap": 3_000_000_000_000,
    "trailingPE": 30.0, "forwardPE": 27.5, "priceToBook": 45.0, "priceToSalesTrailing12Months": 7.5,
    "floatShares": 15_000_000_000, "sharesOutstanding": 15_200_000_000,
    "trailingAnnualDividendRate": 1.0, "dividendYield": 0.42,
    "trailingEps": 6.5, "bookValue": 4.4, "returnOnEquity": 1.5,
    "totalRevenue": 390_000_000_000, "netIncomeToCommon": 95_000_000_000,
    "grossMargins": 0.46, "profitMargins": 0.24, "revenueGrowth": 0.05, "earningsGrowth": 0.08,
    "mostRecentQuarter": 1719619200,  # 2024-06-29 00:00 UTC
    "operatingCashflow": 110_000_000_000, "freeCashflow": 100_000_000_000,
    "totalDebt": 100_000_000_000, "totalCash": 60_000_000_000,
}


@pytest.fixture(autouse=True)
def _reset():
    yf_adapter.reset_caches()
    sec.reset_cik_cache()
    yield
    yf_adapter.reset_caches()
    sec.reset_cik_cache()


def test_yfinance_info_mapping(monkeypatch):
    """Every Fundamentals field is mapped from Ticker.info: ratios in percent, money absolute, report_date ISO."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", info=_INFO)})
    out = YFinanceFundamentalsVendor().fetch([Symbol.parse("AAPL")], {})
    assert len(out) == 1
    f = out[0]
    assert f.symbol == "AAPL" and f.market == "US" and f.name == "Apple Inc."
    assert f.pe_ttm == 30.0 and f.pe_static == 27.5 and f.pb == 45.0 and f.ps_ttm == 7.5
    assert f.total_market_value == 3_000_000_000_000
    assert f.circulating_market_value == 15_000_000_000 * 200.0
    assert f.total_shares == 15_200_000_000 and f.float_shares == 15_000_000_000
    assert f.eps == 6.5 and f.bps == 4.4
    assert abs(f.roe - 150.0) < 1e-9
    assert f.revenue == 390_000_000_000 and f.net_profit == 95_000_000_000
    assert abs(f.gross_margin - 46.0) < 1e-9 and abs(f.net_margin - 24.0) < 1e-9
    assert abs(f.revenue_yoy - 5.0) < 1e-9 and abs(f.net_profit_yoy - 8.0) < 1e-9
    assert f.report_date == "2024-06-29"
    assert f.currency == "USD"
    assert f.operating_cash_flow == 110_000_000_000 and f.free_cash_flow == 100_000_000_000
    assert f.total_debt == 100_000_000_000 and f.total_cash == 60_000_000_000


def test_yfinance_dividend_yield_rule(monkeypatch):
    """Yield is trailingAnnualDividendRate / price * 100 when the rate exists, else Yahoo's dividendYield as-is."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", info=_INFO)})
    f = YFinanceFundamentalsVendor().fetch([Symbol.parse("AAPL")], {})[0]
    assert abs(f.dividend_yield - 0.5) < 1e-9  # 1.0 / 200 * 100

    info2 = {k: v for k, v in _INFO.items() if k != "trailingAnnualDividendRate"}
    patch_yf(monkeypatch, tickers={"MSFT": FakeTicker("MSFT", info=info2)})
    f2 = YFinanceFundamentalsVendor().fetch([Symbol.parse("MSFT")], {})[0]
    assert f2.dividend_yield == 0.42


def test_yfinance_ca_symbol_and_currency(monkeypatch):
    """A Canadian symbol keeps its market and native currency; the Yahoo symbol is the CA spelling."""
    info = {**_INFO, "currency": "CAD", "shortName": "Shopify Inc."}
    calls = patch_yf(monkeypatch, tickers={"SHOP.TO": FakeTicker("SHOP.TO", info=info)})
    out = YFinanceFundamentalsVendor().fetch([Symbol.parse("SHOP.TO")], {})
    assert calls["tickers"] == ["SHOP.TO"]
    assert out[0].market == "CA" and out[0].currency == "CAD" and out[0].name == "Shopify Inc."


def test_yfinance_skips_without_price_or_cap_and_survives_failure(monkeypatch):
    """Rows with neither price nor market cap are skipped; a failing info never kills the batch."""
    patch_yf(monkeypatch, tickers={
        "NOPE": FakeTicker("NOPE", info={"shortName": "Nothing"}),
        "BAD": FakeTicker("BAD", raise_on_info=RuntimeError("boom")),
        "AAPL": FakeTicker("AAPL", info=_INFO),
    })
    out = YFinanceFundamentalsVendor().fetch([Symbol.parse("NOPE"), Symbol.parse("BAD"), Symbol.parse("AAPL")], {})
    assert [f.symbol for f in out] == ["AAPL"]


def test_yfinance_missing_fields_stay_none(monkeypatch):
    """Fields absent from info are None, never fabricated; NaN is treated as missing."""
    info = {"regularMarketPrice": 10.0, "trailingPE": float("nan"), "currency": "USD"}
    patch_yf(monkeypatch, tickers={"XYZ": FakeTicker("XYZ", info=info)})
    f = YFinanceFundamentalsVendor().fetch([Symbol.parse("XYZ")], {})[0]
    assert f.pe_ttm is None and f.total_market_value is None and f.revenue is None
    assert f.dividend_yield is None and f.report_date == "" and f.name == ""


# ---------------------------------------------------------------------------
# SEC EDGAR
# ---------------------------------------------------------------------------

_TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc"}}


def _usd(val, start, end, fy, fp, form, filed):
    return {"val": val, "start": start, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed}


_FACTS = {
    "entityName": "Apple Inc.",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 15_000_000_000, "end": "2024-06-29", "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-02"},
        ]}}},
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _usd(80_000_000_000, "2023-04-02", "2023-07-01", 2023, "Q3", "10-Q", "2023-08-04"),
                _usd(230_000_000_000, "2022-09-25", "2023-07-01", 2023, "Q3", "10-Q", "2023-08-04"),  # 9-month YTD
                _usd(383_000_000_000, "2022-10-01", "2023-09-30", 2023, "FY", "10-K", "2023-11-03"),
                _usd(84_000_000_000, "2024-03-31", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),
                _usd(250_000_000_000, "2023-10-01", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),  # 9-month YTD
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                _usd(19_000_000_000, "2023-04-02", "2023-07-01", 2023, "Q3", "10-Q", "2023-08-04"),
                _usd(21_000_000_000, "2024-03-31", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),
                _usd(60_000_000_000, "2023-10-01", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),
            ]}},
            "GrossProfit": {"units": {"USD": [
                _usd(42_000_000_000, "2024-03-31", "2024-06-29", 2024, "Q3", "10-Q", "2024-08-02"),
            ]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"val": 1.40, "start": "2024-03-31", "end": "2024-06-29", "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-02"},
            ]}},
            "StockholdersEquity": {"units": {"USD": [
                {"val": 66_000_000_000, "end": "2024-06-29", "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-02"},
                {"val": 70_000_000_000, "end": "2024-09-28", "fy": 2024, "fp": "FY", "form": "8-K", "filed": "2024-10-31"},  # not a 10-K/10-Q
            ]}},
        },
    },
}


def _sec_dispatcher(calls: list):
    def fake_get(url, **kw):
        calls.append((url, kw))
        if url == sec.COMPANY_TICKERS_URL:
            return _TICKERS
        if "companyfacts/CIK0000320193" in url:
            return _FACTS
        return None
    return fake_get


def test_sec_companyfacts_mapping(monkeypatch):
    """Latest 10-Q facts are picked by end date with the quarter (not the 9-month YTD) duration;
    margins, BPS, ROE and YoY are derived; valuation fields stay None; currency USD."""
    calls: list = []
    monkeypatch.setattr(sec, "market_get", _sec_dispatcher(calls))
    out = SecEdgarFundamentalsVendor().fetch([Symbol.parse("AAPL")], {"contact_email": "ops@example.org"})
    assert len(out) == 1
    f = out[0]
    assert f.symbol == "AAPL" and f.market == "US" and f.name == "Apple Inc." and f.currency == "USD"
    assert f.revenue == 84_000_000_000 and f.net_profit == 21_000_000_000 and f.eps == 1.40
    assert f.report_date == "2024-06-29"
    assert f.total_shares == 15_000_000_000
    assert abs(f.bps - 66_000_000_000 / 15_000_000_000) < 1e-9
    assert abs(f.roe - 21 / 66 * 100) < 1e-9
    assert abs(f.gross_margin - 50.0) < 1e-9 and abs(f.net_margin - 25.0) < 1e-9
    assert abs(f.revenue_yoy - 5.0) < 1e-9
    assert abs(f.net_profit_yoy - (21 - 19) / 19 * 100) < 1e-9
    assert f.pe_ttm is None and f.total_market_value is None and f.pb is None
    # SEC headers: contact from config, never a hard-coded personal address.
    headers = calls[0][1]["headers"]
    assert headers["User-Agent"] == "PanWatch/1.0 (ops@example.org)"


def test_sec_cik_resolution_brk_b(monkeypatch):
    """BRK.B resolves through the SEC spelling BRK-B; an unknown ticker yields [] without a facts call."""
    calls: list = []
    monkeypatch.setattr(sec, "market_get", _sec_dispatcher(calls))
    assert sec.resolve_cik("BRK.B", {}) == 1067983
    assert sec.resolve_cik("BRK-B", {}) == 1067983
    assert sec.resolve_cik("AAPL", {}) == 320193
    assert sec.resolve_cik("ZZZZ", {}) is None
    # one download only (24 h cache)
    assert sum(1 for u, _ in calls if u == sec.COMPANY_TICKERS_URL) == 1
    out = SecEdgarFundamentalsVendor().fetch([Symbol.parse("ZZZZ")], {})
    assert out == []
    assert not any("companyfacts" in u for u, _ in calls)


def test_sec_default_contact_and_env(monkeypatch):
    """The User-Agent contact falls back to SEC_CONTACT_EMAIL, then to the neutral default."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    assert sec.sec_headers({})["User-Agent"] == "PanWatch/1.0 (contact@panwatch.local)"
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "sec@example.org")
    assert sec.sec_headers({})["User-Agent"] == "PanWatch/1.0 (sec@example.org)"
    assert sec.sec_headers({"contact_email": "cfg@example.org"})["User-Agent"] == "PanWatch/1.0 (cfg@example.org)"


def test_sec_facts_download_failure_is_empty(monkeypatch):
    """A failed companyfacts download (market_get -> None) yields [] so the Engine can fail over."""
    def fake_get(url, **kw):
        return _TICKERS if url == sec.COMPANY_TICKERS_URL else None
    monkeypatch.setattr(sec, "market_get", fake_get)
    assert SecEdgarFundamentalsVendor().fetch([Symbol.parse("AAPL")], {}) == []
