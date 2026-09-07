"""YFinanceDividendVendor: paid history newest first, limit, announced ex-date from the calendar."""

from __future__ import annotations

import pandas as pd
from _yf_fakes import FakeTicker, make_series, patch_yf

from marketdata.symbol import Symbol
from marketdata.vendors.dividend import YFinanceDividendVendor

_DIVS = make_series([("2024-02-09", 0.24), ("2024-05-10", 0.25), ("2024-08-12", 0.25), ("2024-11-08", 0.25)], "Dividends")


def test_dividends_newest_first_paid(monkeypatch):
    """Paid rows are newest first, amounts are per share, legacy CN ratios stay None."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", dividends=_DIVS)})
    out = YFinanceDividendVendor().fetch([Symbol.parse("AAPL")], {})
    assert [d.ex_date for d in out] == ["2024-11-08", "2024-08-12", "2024-05-10", "2024-02-09"]
    d = out[0]
    assert d.symbol == "AAPL" and d.dividend_per_share == 0.25 and d.progress == "paid"
    assert d.transfer_ratio is None and d.bonus_ratio is None


def test_dividends_limit(monkeypatch):
    """config["limit"] caps the number of paid rows (default 20)."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", dividends=_DIVS)})
    out = YFinanceDividendVendor().fetch([Symbol.parse("AAPL")], {"limit": 2})
    assert [d.ex_date for d in out] == ["2024-11-08", "2024-08-12"]
    many = make_series([(f"2020-{m:02d}-01", 0.1) for m in range(1, 13)] + [(f"2021-{m:02d}-01", 0.1) for m in range(1, 13)], "Dividends")
    patch_yf(monkeypatch, tickers={"MSFT": FakeTicker("MSFT", dividends=many)})
    assert len(YFinanceDividendVendor().fetch([Symbol.parse("MSFT")], {})) == 20


def test_dividends_announced_from_calendar(monkeypatch):
    """A calendar ex-dividend date later than the newest paid row is prepended as "announced"."""
    cal = {"Ex-Dividend Date": pd.Timestamp("2025-02-10").date(), "Dividend Date": pd.Timestamp("2025-02-20").date()}
    patch_yf(monkeypatch, tickers={"SHOP.TO": FakeTicker("SHOP.TO", dividends=_DIVS, calendar=cal)})
    out = YFinanceDividendVendor().fetch([Symbol.parse("SHOP.TO")], {})
    assert out[0].progress == "announced" and out[0].ex_date == "2025-02-10"
    assert out[0].dividend_per_share is None and out[0].symbol == "SHOP.TO"
    assert out[1].ex_date == "2024-11-08" and out[1].progress == "paid"


def test_dividends_calendar_not_later_is_ignored(monkeypatch):
    """A calendar ex-date that is not newer than the last paid row adds nothing."""
    cal = {"Ex-Dividend Date": pd.Timestamp("2024-11-08").date()}
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", dividends=_DIVS, calendar=cal)})
    out = YFinanceDividendVendor().fetch([Symbol.parse("AAPL")], {})
    assert all(d.progress == "paid" for d in out) and len(out) == 4


def test_dividends_empty_and_failure(monkeypatch):
    """No dividends -> [] for that symbol; a failing symbol never kills the batch."""
    class Boom(FakeTicker):
        @property
        def dividends(self):
            raise RuntimeError("boom")

    patch_yf(monkeypatch, tickers={
        "NODIV": FakeTicker("NODIV"), "BAD": Boom("BAD"), "AAPL": FakeTicker("AAPL", dividends=_DIVS),
    })
    out = YFinanceDividendVendor().fetch([Symbol.parse("NODIV"), Symbol.parse("BAD"), Symbol.parse("AAPL")], {})
    assert {d.symbol for d in out} == {"AAPL"}
