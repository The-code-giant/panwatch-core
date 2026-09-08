"""Filings vendors: SEC submissions parallel arrays (form filter, limit, URL) and the Canadian press-release stand-in."""

from __future__ import annotations

from _yf_fakes import FakeTicker, patch_yf, utc

import marketdata.vendors._sec as sec
from marketdata.symbol import Symbol
from marketdata.vendors.filings import SecEdgarFilingsVendor, YFinanceNewswireFilingsVendor

_TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}

_SUBMISSIONS = {
    "cik": "320193",
    "filings": {"recent": {
        "form": ["4", "8-K", "10-Q", "S-8", "SC 13G/A", "10-K"],
        "filingDate": ["2024-11-05", "2024-10-31", "2024-08-02", "2024-07-15", "2024-02-14", "2023-11-03"],
        "accessionNumber": ["0000320193-24-000110", "0000320193-24-000108", "0000320193-24-000081",
                            "0000320193-24-000070", "0001193125-24-036000", "0000320193-23-000106"],
        "primaryDocument": ["xslF345X05/wk-form4_1730841234.xml", "aapl-20241031.htm", "aapl-20240629.htm",
                            "d123.htm", "", "aapl-20230930.htm"],
        "primaryDocDescription": ["FORM 4", "8-K", "10-Q", "S-8", "", "10-K"],
        "reportDate": ["2024-11-01", "2024-10-31", "2024-06-29", "", "2023-12-31", "2023-09-30"],
    }},
}


def _dispatcher(calls: list):
    def fake_get(url, **kw):
        calls.append((url, kw))
        if url == sec.COMPANY_TICKERS_URL:
            return _TICKERS
        if "submissions/CIK0000320193" in url:
            return _SUBMISSIONS
        return None
    return fake_get


def test_sec_filings_default_forms(monkeypatch):
    """Only the default market-moving forms are kept (S-8 and SC 13G/A dropped), newest first,
    with the archive URL, title "FORM: description", filed_at at 00:00 UTC and report_date."""
    sec.reset_cik_cache()
    calls: list = []
    monkeypatch.setattr(sec, "market_get", _dispatcher(calls))
    out = SecEdgarFilingsVendor().fetch([Symbol.parse("AAPL")], {"days": 50})
    assert [f.form_type for f in out] == ["4", "8-K", "10-Q", "10-K"]
    f = out[1]
    assert f.source == "sec_edgar" and f.symbol == "AAPL" and f.external_id == "0000320193-24-000108"
    assert f.title == "8-K: 8-K" and f.description == "8-K" and f.report_date == "2024-10-31"
    assert f.filed_at == utc(2024, 10, 31)
    assert f.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000108/aapl-20241031.htm"
    assert out[0].title == "4: FORM 4"
    headers = calls[-1][1]["headers"]
    assert headers["User-Agent"].startswith("TickerKeep/1.0 (")


def test_sec_filings_forms_config_and_limit(monkeypatch):
    """config["forms"] narrows the set and config["days"] (the Engine's limit) caps the count."""
    sec.reset_cik_cache()
    monkeypatch.setattr(sec, "market_get", _dispatcher([]))
    out = SecEdgarFilingsVendor().fetch([Symbol.parse("AAPL")], {"forms": ["8-K", "10-Q", "10-K"], "days": 2})
    assert [f.form_type for f in out] == ["8-K", "10-Q"]


def test_sec_filings_unresolved_or_failed(monkeypatch):
    """Unknown ticker -> [] without a submissions call; failed download -> []."""
    sec.reset_cik_cache()
    calls: list = []
    monkeypatch.setattr(sec, "market_get", _dispatcher(calls))
    assert SecEdgarFilingsVendor().fetch([Symbol.parse("ZZZZ")], {}) == []
    assert not any("submissions" in u for u, _ in calls)

    sec.reset_cik_cache()
    monkeypatch.setattr(sec, "market_get", lambda url, **kw: _TICKERS if url == sec.COMPANY_TICKERS_URL else None)
    assert SecEdgarFilingsVendor().fetch([Symbol.parse("AAPL")], {}) == []


_PR = [
    {"id": "pr-1", "content": {
        "title": "Shopify Announces Third-Quarter 2024 Financial Results", "pubDate": "2024-11-12T12:00:00Z",
        "summary": "<p>Revenue grew 26%</p>", "contentType": "PRESS_RELEASE",
        "provider": {"displayName": "CNW Group"}, "canonicalUrl": {"url": "https://www.newswire.ca/x"}}},
    {"id": "pr-2", "content": {
        "title": "Shopify to Report Q3 Results", "pubDate": "2024-10-20T12:00:00Z",
        "contentType": "PRESS_RELEASE", "provider": {"displayName": "GlobeNewswire"},
        "clickThroughUrl": {"url": "https://www.globenewswire.com/y"}}},
    {"id": "skip", "content": {"title": "", "pubDate": "2024-10-01T00:00:00Z"}},
]


def test_yfinance_newswire_press_releases(monkeypatch):
    """The Canadian vendor asks Yahoo for the press-release tab and maps rows to form_type press_release."""
    t = FakeTicker("SHOP.TO", news={"press releases": _PR, "news": [{"content": {"title": "not a PR"}}]})
    patch_yf(monkeypatch, tickers={"SHOP.TO": t})
    out = YFinanceNewswireFilingsVendor().fetch([Symbol.parse("SHOP.TO")], {"days": 50})
    assert t.calls == [("get_news", {"count": 30, "tab": "press releases"})]
    assert [f.external_id for f in out] == ["pr-1", "pr-2"]
    f = out[0]
    assert f.source == "yfinance_newswire" and f.symbol == "SHOP.TO" and f.form_type == "press_release"
    assert f.title.startswith("Shopify Announces") and f.description == "Revenue grew 26%"
    assert f.filed_at == utc(2024, 11, 12, 12, 0) and f.url == "https://www.newswire.ca/x"
    assert out[1].url == "https://www.globenewswire.com/y"


def test_yfinance_newswire_limit_and_failure(monkeypatch):
    """The Engine limit ("days") caps rows; a failing symbol never kills the batch."""
    class Boom(FakeTicker):
        def get_news(self, count=10, tab="news"):
            raise RuntimeError("boom")

    patch_yf(monkeypatch, tickers={
        "BAD.TO": Boom("BAD.TO"), "SHOP.TO": FakeTicker("SHOP.TO", news={"press releases": _PR}),
    })
    out = YFinanceNewswireFilingsVendor().fetch([Symbol.parse("BAD.TO"), Symbol.parse("SHOP.TO")], {"days": 1})
    assert [f.external_id for f in out] == ["pr-1"]
