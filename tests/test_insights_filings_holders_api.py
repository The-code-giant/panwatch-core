"""GET /insights/filings 与 /insights/holders:走 md_filings / md_holders,形状与 CA 提示。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api import insights


def _client(monkeypatch, *, filings=None, holders=None, filings_raise=False):
    captured: dict = {}

    def fake_filings(symbols, market=None, limit=50):
        captured["filings"] = {"symbols": list(symbols), "market": market, "limit": limit}
        if filings_raise:
            raise RuntimeError("edgar down")
        return list(filings or [])

    def fake_holders(symbols, market=None):
        captured["holders"] = {"symbols": list(symbols), "market": market}
        return list(holders or [])

    monkeypatch.setattr(insights, "md_filings", fake_filings)
    monkeypatch.setattr(insights, "md_holders", fake_holders)

    app = FastAPI()
    app.include_router(insights.router, prefix="/api/insights")
    return TestClient(app), captured


_FILINGS = [
    {"source": "sec_edgar", "external_id": "0001", "symbol": "AAPL", "form_type": "8-K",
     "title": "Current report", "filed_at": "2026-08-01T00:00:00+00:00",
     "url": "https://sec.gov/x", "description": "", "report_date": "2026-07-31"},
    {"source": "sec_edgar", "external_id": "0002", "symbol": "AAPL", "form_type": "10-Q",
     "title": "Quarterly report", "filed_at": "2026-07-30T00:00:00+00:00",
     "url": "https://sec.gov/y", "description": "", "report_date": "2026-06-30"},
    {"source": "sec_edgar", "external_id": "0003", "symbol": "AAPL", "form_type": "4",
     "title": "Insider form 4", "filed_at": "2026-07-29T00:00:00+00:00",
     "url": "https://sec.gov/z", "description": "", "report_date": ""},
]


def test_filings_returns_items_and_shape(monkeypatch):
    """/filings 返回 {symbol, market, items, note},items 为 md_filings 原样 dict,US 无提示。"""
    client, captured = _client(monkeypatch, filings=_FILINGS)
    resp = client.get("/api/insights/filings", params={"symbol": "AAPL", "market": "US", "limit": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"symbol", "market", "items", "note"}
    assert body["symbol"] == "AAPL" and body["market"] == "US" and body["note"] == ""
    assert [f["form_type"] for f in body["items"]] == ["8-K", "10-Q", "4"]
    assert body["items"][0]["filed_at"] == "2026-08-01T00:00:00+00:00"
    assert captured["filings"] == {"symbols": ["AAPL"], "market": "US", "limit": 10}


def test_filings_forms_filter_is_host_side(monkeypatch):
    """forms=8-K,10-q(大小写不敏感)只保留匹配 form_type 的条目,过滤在宿主侧完成。"""
    client, _ = _client(monkeypatch, filings=_FILINGS)
    resp = client.get("/api/insights/filings", params={"symbol": "AAPL", "forms": "8-K, 10-q"})
    assert resp.status_code == 200
    assert [f["form_type"] for f in resp.json()["items"]] == ["8-K", "10-Q"]


def test_filings_ca_note(monkeypatch):
    """market=CA 时附带 SEDAR+ 无免费 API、改用新闻稿的说明;市场码透传给 md_filings。"""
    client, captured = _client(monkeypatch, filings=[])
    resp = client.get("/api/insights/filings", params={"symbol": "SHOP.TO", "market": "ca"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "CA"
    assert body["note"].startswith("SEDAR+ offers no free API")
    assert "CNW" in body["note"] and "GlobeNewswire" in body["note"]
    assert captured["filings"]["market"] == "CA"


def test_filings_missing_symbol_400(monkeypatch):
    """缺少 symbol 时返回 400。"""
    client, _ = _client(monkeypatch)
    assert client.get("/api/insights/filings").status_code == 400
    assert client.get("/api/insights/filings", params={"symbol": "  "}).status_code == 400


def test_filings_vendor_failure_is_failsoft(monkeypatch):
    """md_filings 抛错时返回空 items 而非 5xx。"""
    client, _ = _client(monkeypatch, filings_raise=True)
    resp = client.get("/api/insights/filings", params={"symbol": "AAPL"})
    assert resp.status_code == 200 and resp.json()["items"] == []


_HOLDERS = [
    {"symbol": "AAPL", "kind": "breakdown", "holder": "insidersPercentHeld", "date": "",
     "shares": None, "value": None, "pct_out": 0.07, "change_pct": None, "transaction": "",
     "position": "", "ownership": "", "url": "", "source": "yfinance"},
    {"symbol": "AAPL", "kind": "breakdown", "holder": "Institutions % held", "date": "",
     "shares": None, "value": None, "pct_out": 61.5, "change_pct": None, "transaction": "",
     "position": "", "ownership": "", "url": "", "source": "yfinance"},
    {"symbol": "AAPL", "kind": "breakdown", "holder": "institutionsFloatPercentHeld", "date": "",
     "shares": None, "value": None, "pct_out": 61.6, "change_pct": None, "transaction": "",
     "position": "", "ownership": "", "url": "", "source": "yfinance"},
    {"symbol": "AAPL", "kind": "breakdown", "holder": "institutionsCount", "date": "",
     "shares": 6543.0, "value": None, "pct_out": None, "change_pct": None, "transaction": "",
     "position": "", "ownership": "", "url": "", "source": "yfinance"},
    {"symbol": "AAPL", "kind": "institution", "holder": "Vanguard Group Inc", "date": "2026-06-30",
     "shares": 1.3e9, "value": 2.5e11, "pct_out": 8.9, "change_pct": 0.4, "transaction": "",
     "position": "", "ownership": "", "url": "", "source": "yfinance"},
    {"symbol": "AAPL", "kind": "insider_tx", "holder": "COOK TIMOTHY D", "date": "2026-08-15",
     "shares": 50000.0, "value": 1.1e7, "pct_out": None, "change_pct": None, "transaction": "Sale",
     "position": "Chief Executive Officer", "ownership": "D", "url": "", "source": "sec_edgar"},
]


def test_holders_splits_rows_by_kind(monkeypatch):
    """/holders 把 md_holders 行按 kind 拆成 breakdown / institutions / insider_transactions,并带 source。"""
    client, captured = _client(monkeypatch, holders=_HOLDERS)
    resp = client.get("/api/insights/holders", params={"symbol": "AAPL", "market": "US"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"breakdown", "institutions", "insider_transactions", "source"}
    assert body["breakdown"] == {
        "insiders_pct": 0.07,
        "institutions_pct": 61.5,
        "institutions_float_pct": 61.6,
        "institutions_count": 6543,
    }
    assert [r["holder"] for r in body["institutions"]] == ["Vanguard Group Inc"]
    assert body["insider_transactions"][0]["transaction"] == "Sale"
    assert body["insider_transactions"][0]["position"] == "Chief Executive Officer"
    assert body["source"] == "yfinance"
    assert captured["holders"] == {"symbols": ["AAPL"], "market": "US"}


def test_holders_empty_and_missing_symbol(monkeypatch):
    """无数据时 breakdown 各值为 None、列表为空;缺 symbol 返回 400。"""
    client, _ = _client(monkeypatch, holders=[])
    resp = client.get("/api/insights/holders", params={"symbol": "AAPL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["breakdown"] == {
        "insiders_pct": None, "institutions_pct": None,
        "institutions_float_pct": None, "institutions_count": None,
    }
    assert body["institutions"] == [] and body["insider_transactions"] == [] and body["source"] == ""
    assert client.get("/api/insights/holders").status_code == 400


def test_recent_announcements_prefers_filings(monkeypatch):
    """_fetch_recent_announcements 优先取 filings(带 form_type 前缀的标题与日期)。"""
    import asyncio

    monkeypatch.setattr(insights, "md_filings", lambda symbols, market=None, limit=50: _FILINGS[:2])
    out = asyncio.run(insights._fetch_recent_announcements("AAPL", "Apple", limit=5, market="US"))
    assert [a["title"] for a in out] == ["8-K: Current report", "10-Q: Quarterly report"]
    assert out[0]["time"] == "2026-08-01 00:00"


def test_recent_announcements_falls_back_to_news(monkeypatch):
    """filings 为空时退回近 7 天新闻。"""
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace

    monkeypatch.setattr(insights, "md_filings", lambda symbols, market=None, limit=50: [])

    class _Collector:
        async def fetch_all(self, **kw):
            return [SimpleNamespace(title="Apple unveils", content="body",
                                    publish_time=datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc))]

    import src.collectors.news_collector as nc
    monkeypatch.setattr(nc.NewsCollector, "from_database", classmethod(lambda cls: _Collector()))
    out = asyncio.run(insights._fetch_recent_announcements("AAPL", "Apple"))
    assert out == [{"title": "Apple unveils", "time": "2026-08-02 09:00", "content": "body"}]
