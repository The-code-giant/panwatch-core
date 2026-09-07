"""Shared yfinance fakes for the package tests (mirrored as tests/_yf_fakes.py in the host).

Usage::

    from _yf_fakes import FakeTicker, make_history_df, patch_yf

    def test_x(monkeypatch):
        patch_yf(monkeypatch, tickers={"AAPL": FakeTicker(info={...})})

``patch_yf`` installs the fakes on ``marketdata.vendors.yf_adapter`` (``ticker_factory``,
``screen_fn``, ``search_fn``, ``download_fn``), clears the adapter caches and resets the
``yfinance`` circuit breaker so tests never leak state into each other. The real ``call``
wrapper stays in place (thread pool + breaker); pass ``bypass_call=True`` to replace it with a
plain ``fn()`` when a test wants raw exceptions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from marketdata.vendors import yf_adapter


def make_history_df(rows: list[tuple]) -> pd.DataFrame:
    """rows: ``(date, open, high, low, close, volume[, adj_close])`` -> yfinance-shaped frame
    (tz-aware ``Date`` index, ``Open High Low Close Adj Close Volume`` columns)."""
    idx = []
    data = {"Open": [], "High": [], "Low": [], "Close": [], "Adj Close": [], "Volume": []}
    for r in rows:
        d, o, h, low, c, v = r[:6]
        adj = r[6] if len(r) > 6 else c
        idx.append(pd.Timestamp(d, tz="America/New_York"))
        data["Open"].append(o)
        data["High"].append(h)
        data["Low"].append(low)
        data["Close"].append(c)
        data["Adj Close"].append(adj)
        data["Volume"].append(v)
    df = pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="Date"))
    return df


def make_earnings_df(rows: list[tuple]) -> pd.DataFrame:
    """rows: ``(date, eps_estimate, reported_eps, surprise_pct)`` -> ``get_earnings_dates`` shape."""
    idx = [pd.Timestamp(r[0], tz="America/New_York") for r in rows]
    df = pd.DataFrame(
        {"EPS Estimate": [r[1] for r in rows], "Reported EPS": [r[2] for r in rows],
         "Surprise(%)": [r[3] for r in rows]},
        index=pd.DatetimeIndex(idx, name="Earnings Date"),
    )
    return df


def make_series(rows: list[tuple], name: str) -> pd.Series:
    """rows: ``(date, value)`` -> tz-aware Series (``dividends`` / ``splits`` shape)."""
    idx = [pd.Timestamp(r[0], tz="America/New_York") for r in rows]
    return pd.Series([r[1] for r in rows], index=pd.DatetimeIndex(idx, name="Date"), name=name, dtype="float64")


class FakeTicker:
    """Minimal stand-in for ``yfinance.Ticker`` exposing what the vendors read."""

    def __init__(
        self,
        ysym: str = "",
        *,
        history_df: pd.DataFrame | None = None,
        fast_info: dict | None = None,
        info: dict | None = None,
        raise_on_history: Exception | None = None,
        raise_on_info: Exception | None = None,
        news: list[dict] | dict[str, list[dict]] | None = None,
        calendar: dict | None = None,
        earnings_dates: pd.DataFrame | None = None,
        dividends: pd.Series | None = None,
        splits: pd.Series | None = None,
        major_holders: pd.DataFrame | None = None,
        institutional_holders: pd.DataFrame | None = None,
        insider_transactions: pd.DataFrame | None = None,
    ):
        self.ticker = ysym
        self._history_df = history_df
        self._fast_info = fast_info or {}
        self._info = info if info is not None else {}
        self._raise_on_history = raise_on_history
        self._raise_on_info = raise_on_info
        self._news = news if news is not None else []
        self._calendar = calendar if calendar is not None else {}
        self._earnings = earnings_dates
        self._dividends = dividends if dividends is not None else pd.Series([], dtype="float64", name="Dividends")
        self._splits = splits if splits is not None else pd.Series([], dtype="float64", name="Stock Splits")
        self._major = major_holders
        self._inst = institutional_holders
        self._insider = insider_transactions
        self.calls: list[tuple[str, dict]] = []

    # --- prices ---
    def history(self, **kw) -> pd.DataFrame:
        self.calls.append(("history", kw))
        if self._raise_on_history is not None:
            raise self._raise_on_history
        if self._history_df is None:
            return pd.DataFrame()
        return self._history_df

    @property
    def fast_info(self) -> dict:
        return self._fast_info

    @property
    def info(self) -> dict:
        if self._raise_on_info is not None:
            raise self._raise_on_info
        return self._info

    # --- news ---
    def get_news(self, count: int = 10, tab: str = "news") -> list[dict]:
        self.calls.append(("get_news", {"count": count, "tab": tab}))
        if isinstance(self._news, dict):
            items = self._news.get(tab) or []
        else:
            items = self._news
        return list(items)[:count]

    # --- events ---
    @property
    def calendar(self) -> dict:
        return self._calendar

    def get_earnings_dates(self, limit: int = 12, offset: int = 0):
        self.calls.append(("get_earnings_dates", {"limit": limit, "offset": offset}))
        if self._earnings is None:
            return None
        return self._earnings.iloc[offset:offset + limit]

    @property
    def dividends(self) -> pd.Series:
        return self._dividends

    @property
    def splits(self) -> pd.Series:
        return self._splits

    # --- holders ---
    @property
    def major_holders(self):
        return self._major

    @property
    def institutional_holders(self):
        return self._inst

    @property
    def insider_transactions(self):
        return self._insider


def patch_yf(
    monkeypatch,
    *,
    tickers: dict[str, FakeTicker] | Callable[[str], Any] | None = None,
    screens: dict[str, list[dict]] | Callable[..., Any] | None = None,
    search: list[dict] | Callable[..., Any] | None = None,
    download: dict[str, dict] | Callable[..., Any] | None = None,
    bypass_call: bool = False,
) -> dict[str, Any]:
    """Install fakes on the adapter and reset its state. Returns a ``calls`` recorder
    ``{"tickers": [ysym...], "screens": [query...], "search": [query...], "download": [symbols...]}``."""
    calls: dict[str, list] = {"tickers": [], "screens": [], "search": [], "download": []}
    yf_adapter.reset_caches()

    if tickers is None:
        tickers = {}

    def _factory(ysym: str):
        calls["tickers"].append(ysym)
        if callable(tickers):
            return tickers(ysym)
        t = tickers.get(ysym)
        if t is None:
            raise KeyError(f"no fake ticker for {ysym}")
        return t

    monkeypatch.setattr(yf_adapter, "ticker_factory", _factory)

    if screens is not None:
        def _screen(query, **kw):
            calls["screens"].append(query)
            if callable(screens):
                return screens(query, **kw)
            key = query if isinstance(query, str) else "query"
            if key not in screens and "*" in screens:
                key = "*"
            return {"quotes": list(screens.get(key) or [])}
        monkeypatch.setattr(yf_adapter, "screen_fn", _screen)
    else:
        monkeypatch.setattr(yf_adapter, "screen_fn", lambda *a, **k: {"quotes": []})

    if search is not None:
        def _search(query, **kw):
            calls["search"].append(query)
            if callable(search):
                return search(query, **kw)
            return list(search)
        monkeypatch.setattr(yf_adapter, "search_fn", _search)
    else:
        monkeypatch.setattr(yf_adapter, "search_fn", lambda *a, **k: [])

    if download is not None:
        def _download(symbols, period="5d"):
            calls["download"].append(list(symbols))
            if callable(download):
                return download(symbols, period=period)
            return {s: download[s] for s in symbols if s in download}
        monkeypatch.setattr(yf_adapter, "download_fn", _download)
    else:
        monkeypatch.setattr(yf_adapter, "download_fn", lambda symbols, period="5d": {})

    if bypass_call:
        monkeypatch.setattr(yf_adapter, "call", lambda label, fn, **k: fn())

    # The throttle would add 0.15 s per call; keep the tests fast.
    monkeypatch.setattr(yf_adapter.http, "throttle", lambda *a, **k: None)
    return calls


UTC = timezone.utc


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)
