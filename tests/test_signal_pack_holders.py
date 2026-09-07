"""SignalPack holders (ownership) block tests.

- ``include_holders=True`` calls ``md_holders`` once per market and stores a per-symbol summary on
  ``pack.holders`` with the agreed shape.
- Insider buys/sells/net shares only count transactions inside the trailing 6-month window.
- ``include_holders=False`` leaves ``pack.holders`` None; ``include_capital_flow`` is gone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.core.signals import signal_pack
from src.core.signals.signal_pack import SignalPackBuilder, summarise_holders
from src.models.market import MarketCode


class _Portfolio:
    def get_positions_for_stock(self, symbol, market=None, *, actionable_only=True):
        return []

    def get_aggregated_position(self, symbol, market=None, *, actionable_only=True):
        return None


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _rows_for(symbol: str) -> list[dict]:
    return [
        {"symbol": symbol, "kind": "breakdown", "holder": "insidersPercentHeld", "date": "", "shares": None,
         "value": None, "pct_out": 1.25, "change_pct": None, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "breakdown", "holder": "institutionsPercentHeld", "date": "", "shares": None,
         "value": None, "pct_out": 61.4, "change_pct": None, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "breakdown", "holder": "institutionsCount", "date": "", "shares": 4123,
         "value": None, "pct_out": None, "change_pct": None, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "institution", "holder": "Vanguard Group", "date": "2026-06-30", "shares": 1000,
         "value": None, "pct_out": 8.9, "change_pct": 0.4, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "institution", "holder": "BlackRock", "date": "2026-06-30", "shares": 900,
         "value": None, "pct_out": 7.1, "change_pct": -0.2, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "institution", "holder": "State Street", "date": "2026-06-30", "shares": 500,
         "value": None, "pct_out": 4.0, "change_pct": 0.0, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        {"symbol": symbol, "kind": "institution", "holder": "Tiny Fund", "date": "2026-06-30", "shares": 5,
         "value": None, "pct_out": 0.1, "change_pct": 0.0, "transaction": "", "position": "",
         "ownership": "", "url": "", "source": "yfinance"},
        # Insider transactions: two inside the 6-month window, one just outside it
        {"symbol": symbol, "kind": "insider_tx", "holder": "Jane CEO", "date": _days_ago(10), "shares": 5000,
         "value": None, "pct_out": None, "change_pct": None, "transaction": "Sale", "position": "CEO",
         "ownership": "D", "url": "", "source": "sec_edgar"},
        {"symbol": symbol, "kind": "insider_tx", "holder": "Bob CFO", "date": _days_ago(90), "shares": 2000,
         "value": None, "pct_out": None, "change_pct": None, "transaction": "Purchase", "position": "CFO",
         "ownership": "D", "url": "", "source": "sec_edgar"},
        {"symbol": symbol, "kind": "insider_tx", "holder": "Old Director", "date": _days_ago(400), "shares": 99999,
         "value": None, "pct_out": None, "change_pct": None, "transaction": "Sale", "position": "Director",
         "ownership": "D", "url": "", "source": "sec_edgar"},
    ]


def _run_builder(monkeypatch, symbols, *, include_holders: bool):
    calls: list[tuple[list[str], str | None]] = []

    def fake_holders(syms, market=None):
        calls.append((list(syms), market))
        rows: list[dict] = []
        for s in syms:
            rows.extend(_rows_for(s))
        return rows

    monkeypatch.setattr(signal_pack, "md_holders", fake_holders)
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [])
    monkeypatch.setattr(
        SignalPackBuilder,
        "_source_policy",
        staticmethod(lambda source_type, *, default_providers: ([(p, {}) for p in default_providers], False)),
    )
    builder = SignalPackBuilder()
    packs = asyncio.run(
        builder.build_for_symbols(
            symbols=symbols,
            include_news=False,
            news_hours=24,
            portfolio=_Portfolio(),
            include_technical=False,
            include_events=False,
            include_holders=include_holders,
        )
    )
    return packs, calls


def test_holders_summary_shape(monkeypatch):
    """include_holders=True 时 pack.holders 为约定形状的持股摘要（内部人/机构占比、机构数、前三大机构、最近内部人交易、source）。"""
    packs, calls = _run_builder(
        monkeypatch,
        [("AAPL", MarketCode.US, "Apple"), ("SHOP.TO", MarketCode.CA, "Shopify")],
        include_holders=True,
    )

    # One md_holders call per market, symbols grouped by market
    assert sorted(calls) == [(["AAPL"], "US"), (["SHOP.TO"], "CA")]

    h = packs["AAPL"].holders
    assert h is not None
    assert set(h) >= {
        "insiders_pct", "institutions_pct", "institutions_count", "insider_buys_6m", "insider_sells_6m",
        "insider_net_shares_6m", "top_institutions", "last_insider_tx", "source",
    }
    assert h["insiders_pct"] == 1.25
    assert h["institutions_pct"] == 61.4
    assert h["institutions_count"] == 4123
    assert [t["holder"] for t in h["top_institutions"]] == ["Vanguard Group", "BlackRock", "State Street"]
    assert h["top_institutions"][0] == {"holder": "Vanguard Group", "pct_out": 8.9, "change_pct": 0.4}
    assert h["last_insider_tx"] == {
        "holder": "Jane CEO", "transaction": "Sale", "shares": 5000.0, "date": _days_ago(10),
    }
    assert h["source"] == "yfinance"
    assert packs["AAPL"].sources["holders"] == "yfinance"
    assert "holders" not in packs["AAPL"].missing
    assert packs["SHOP.TO"].holders is not None and packs["SHOP.TO"].holders["symbol"] == "SHOP.TO"


def test_holders_six_month_window(monkeypatch):
    """内部人买卖统计只计入最近 6 个月内的交易；净股数 = 买入 - 卖出。"""
    packs, _ = _run_builder(monkeypatch, [("AAPL", MarketCode.US, "Apple")], include_holders=True)
    h = packs["AAPL"].holders
    assert h["insider_buys_6m"] == 1
    assert h["insider_sells_6m"] == 1
    # 2000 purchased - 5000 sold; the 99,999-share sale 400 days ago is outside the window
    assert h["insider_net_shares_6m"] == -3000.0


def test_summarise_holders_window_uses_supplied_clock():
    """summarise_holders 以宿主传入的 UTC 时钟为基准计算 6 个月窗口。"""
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        {"symbol": "X", "kind": "insider_tx", "holder": "A", "date": "2026-08-15", "shares": 100,
         "transaction": "Purchase", "source": "sec_edgar"},
        {"symbol": "X", "kind": "insider_tx", "holder": "B", "date": "2026-01-15", "shares": 100,
         "transaction": "Sale", "source": "sec_edgar"},
        {"symbol": "X", "kind": "insider_tx", "holder": "C", "date": "2026-04-15", "shares": 40,
         "transaction": "Sale", "source": "sec_edgar"},
        {"symbol": "X", "kind": "insider_tx", "holder": "D", "date": "not-a-date", "shares": 40,
         "transaction": "Sale", "source": "sec_edgar"},
    ]
    s = summarise_holders("X", rows, now=now)
    assert s["insider_buys_6m"] == 1
    assert s["insider_sells_6m"] == 1  # 2026-01-15 is more than 6 months before 2026-09-01
    assert s["insider_net_shares_6m"] == 60.0
    assert s["last_insider_tx"]["holder"] == "A"
    assert s["insiders_pct"] is None and s["top_institutions"] == []
    assert summarise_holders("X", [], now=now) is None


def test_include_holders_false_leaves_none(monkeypatch):
    """include_holders=False（默认）时不调用 md_holders，pack.holders 为 None，sources.holders 为 skipped。"""
    packs, calls = _run_builder(monkeypatch, [("AAPL", MarketCode.US, "Apple")], include_holders=False)
    assert calls == []
    assert packs["AAPL"].holders is None
    assert packs["AAPL"].sources["holders"] == "skipped"
    assert "holders" not in packs["AAPL"].missing


def test_holders_fetch_failure_is_soft(monkeypatch):
    """md_holders 抛异常时不影响构建：pack.holders 为 None，missing 含 holders，sources.holders 为 unavailable。"""

    def boom(syms, market=None):
        raise RuntimeError("holders down")

    monkeypatch.setattr(signal_pack, "md_holders", boom)
    monkeypatch.setattr(signal_pack, "md_stock_data", lambda syms, market: [])
    monkeypatch.setattr(
        SignalPackBuilder,
        "_source_policy",
        staticmethod(lambda source_type, *, default_providers: ([(p, {}) for p in default_providers], False)),
    )
    packs = asyncio.run(
        SignalPackBuilder().build_for_symbols(
            symbols=[("AAPL", MarketCode.US, "Apple")],
            include_news=False,
            news_hours=24,
            portfolio=_Portfolio(),
            include_technical=False,
            include_holders=True,
        )
    )
    assert packs["AAPL"].holders is None
    assert "holders" in packs["AAPL"].missing
    assert packs["AAPL"].sources["holders"] == "unavailable"


def test_include_capital_flow_no_longer_accepted():
    """build_for_symbols 不再接受 include_capital_flow 参数；SignalPack 不再有 capital_flow 字段。"""
    with pytest.raises(TypeError):
        asyncio.run(
            SignalPackBuilder().build_for_symbols(
                symbols=[("AAPL", MarketCode.US, "Apple")],
                include_news=False,
                news_hours=24,
                portfolio=_Portfolio(),
                include_capital_flow=True,
            )
        )
    assert "capital_flow" not in signal_pack.SignalPack.__dataclass_fields__
    assert "holders" in signal_pack.SignalPack.__dataclass_fields__
