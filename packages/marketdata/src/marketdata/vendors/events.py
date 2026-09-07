"""Corporate events: earnings (future estimates + reported), ex-dividend, and stock splits.

``YFinanceEventsVendor`` makes three independently guarded yfinance calls per symbol
(``t.calendar``, ``t.get_earnings_dates(...)``, and ``t.dividends``/``t.splits`` together) so a
failure in one never drops events built from the others; a per-symbol failure never kills the
whole batch either. ``NasdaqCalendarVendor`` is date-driven (``config["dates"]``) and keeps only
rows for the requested symbols.

No time-window filtering happens here: ``MarketData.events(..., now=...)`` applies the
``since_days``/``ahead_days`` window itself after every vendor has returned.
"""

from __future__ import annotations

import logging

from marketdata.errors import VendorError
from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import EventItem
from marketdata.vendors import yf_adapter
from marketdata.vendors.base import EventsVendor

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_FORM_URL = "https://finance.yahoo.com/quote/{ysym}/analysis"
_HIST_URL = "https://finance.yahoo.com/quote/{ysym}/history"


def _fmt_num(v, *, decimals: int = 0) -> str:
    """Missing -> "N/A"; otherwise a thousands-separated fixed-point string."""
    if v is None:
        return "N/A"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if fv != fv:  # NaN
        return "N/A"
    return f"{fv:,.{decimals}f}"


def _split_ratio(value) -> str:
    """yfinance split factor (4.0 for a 4-for-1 split, 0.5 for a 1-for-2 reverse split) -> "4:1" / "1:2"."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not v or v != v:
        return "N/A"
    if v >= 1:
        return f"{v:g}:1"
    return f"1:{(1.0 / v):g}"


class YFinanceEventsVendor(EventsVendor):
    name = "yfinance"
    supports_markets = {"US", "CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        earnings_limit = int(config.get("earnings_limit") or 8)
        history_limit = int(config.get("history_limit") or 8)
        collected: list[EventItem] = []
        for sym in symbols:
            try:
                collected.extend(self._fetch_one(sym, earnings_limit=earnings_limit, history_limit=history_limit))
            except Exception as e:  # pragma: no cover - defensive, per-symbol isolation
                logger.warning(f"[events/yfinance] {sym.code}: unexpected {type(e).__name__}: {e}")

        by_id: dict[str, EventItem] = {}
        for ev in collected:
            by_id.setdefault(ev.external_id, ev)
        result = list(by_id.values())
        result.sort(key=lambda e: e.publish_time, reverse=True)
        return result

    def _fetch_one(self, sym: Symbol, *, earnings_limit: int, history_limit: int) -> list[EventItem]:
        code = sym.code
        ysym = yf_adapter.to_yahoo_symbol(sym)
        t = yf_adapter.get_ticker(sym)
        events: list[EventItem] = []

        # --- 1. calendar: the single next earnings estimate (EPS + revenue) ---
        try:
            calendar = yf_adapter.call("Yahoo calendar", lambda: t.calendar, symbol=code)
        except VendorError as e:
            logger.info(f"[events/yfinance] {code} calendar failed: {e}")
            calendar = None
        if calendar:
            events.extend(self._calendar_events(code, ysym, calendar))

        # --- 2. get_earnings_dates: reported history + any additional future rows ---
        try:
            edates = yf_adapter.call(
                "Yahoo earnings dates", lambda: t.get_earnings_dates(limit=earnings_limit), symbol=code,
            )
        except VendorError as e:
            logger.info(f"[events/yfinance] {code} earnings dates failed: {e}")
            edates = None
        if edates is not None:
            events.extend(self._earnings_date_events(code, ysym, edates))

        # --- 3. dividends + splits: one guarded call covering both properties ---
        try:
            hist = yf_adapter.call(
                "Yahoo dividends/splits", lambda: {"dividends": t.dividends, "splits": t.splits}, symbol=code,
            )
        except VendorError as e:
            logger.info(f"[events/yfinance] {code} dividends/splits failed: {e}")
            hist = None
        if hist is not None:
            events.extend(self._dividend_events(code, ysym, hist.get("dividends"), history_limit))
            events.extend(self._split_events(code, ysym, hist.get("splits"), history_limit))

        return events

    @staticmethod
    def _calendar_events(code: str, ysym: str, calendar: dict) -> list[EventItem]:
        raw_dates = calendar.get("Earnings Date")
        if not raw_dates:
            return []
        d = raw_dates[0] if isinstance(raw_dates, (list, tuple)) else raw_dates
        if d is None:
            return []
        date_str = yf_adapter.to_utc(d).strftime("%Y-%m-%d")
        eps = calendar.get("Earnings Average")
        revenue = calendar.get("Revenue Average")
        title = f"{code} earnings: est. EPS {_fmt_num(eps, decimals=2)}, est. revenue {_fmt_num(revenue)}"
        return [EventItem(
            source="yfinance", external_id=f"{code}:earnings:{date_str}", event_type="earnings",
            title=title, publish_time=yf_adapter.to_utc(d), symbols=[code], importance=3,
            url=_FORM_URL.format(ysym=ysym),
        )]

    @staticmethod
    def _earnings_date_events(code: str, ysym: str, edates) -> list[EventItem]:
        records = yf_adapter.df_records(edates)
        out: list[EventItem] = []
        for row in records:
            date_str = row.get("Earnings Date")
            if not date_str:
                continue
            date_str = str(date_str)[:10]
            reported = row.get("Reported EPS")
            est = row.get("EPS Estimate")
            surprise = row.get("Surprise(%)")
            if reported is None:
                title = f"{code} earnings: est. EPS {_fmt_num(est, decimals=2)}, est. revenue N/A"
                importance = 3
            else:
                surprise_val = 0.0 if surprise is None else float(surprise)
                title = (f"{code} reported EPS {_fmt_num(reported, decimals=2)} vs est. "
                        f"{_fmt_num(est, decimals=2)} ({surprise_val:+.1f}%)")
                importance = 2 if abs(surprise_val) >= 10 else 1
            out.append(EventItem(
                source="yfinance", external_id=f"{code}:earnings:{date_str}", event_type="earnings",
                title=title, publish_time=yf_adapter.to_utc(date_str), symbols=[code], importance=importance,
                url=_FORM_URL.format(ysym=ysym),
            ))
        return out

    @staticmethod
    def _dividend_events(code: str, ysym: str, dividends, limit: int) -> list[EventItem]:
        if dividends is None:
            return []
        records = yf_adapter.df_records(dividends)
        out: list[EventItem] = []
        for row in records[-limit:] if limit > 0 else records:
            date_str = row.get("Date")
            if not date_str:
                continue
            dps = row.get("Dividends")
            out.append(EventItem(
                source="yfinance", external_id=f"{code}:dividend:{date_str}", event_type="dividend",
                title=f"{code} ex-dividend {_fmt_num(dps, decimals=2)}/share",
                publish_time=yf_adapter.to_utc(date_str), symbols=[code], importance=1,
                url=_HIST_URL.format(ysym=ysym),
            ))
        return out

    @staticmethod
    def _split_events(code: str, ysym: str, splits, limit: int) -> list[EventItem]:
        if splits is None:
            return []
        records = yf_adapter.df_records(splits)
        out: list[EventItem] = []
        for row in records[-limit:] if limit > 0 else records:
            date_str = row.get("Date")
            if not date_str:
                continue
            ratio = row.get("Stock Splits")
            out.append(EventItem(
                source="yfinance", external_id=f"{code}:split:{date_str}", event_type="split",
                title=f"{code} stock split {_split_ratio(ratio)}",
                publish_time=yf_adapter.to_utc(date_str), symbols=[code], importance=3,
                url=_HIST_URL.format(ysym=ysym),
            ))
        return out


class NasdaqCalendarVendor(EventsVendor):
    """Nasdaq's public earnings calendar: one JSON page per requested date, filtered to the
    symbols asked for. Seed-disabled by default; ``config["dates"]`` comes from
    ``MarketData.events(..., now=...)`` (the next 14 ISO dates); no dates -> ``[]``."""

    name = "nasdaq"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        dates = config.get("dates") or ()
        if not dates:
            return []
        requested = {s.code.strip().upper() for s in symbols}
        if not requested:
            return []
        out: list[EventItem] = []
        for date_str in dates:
            payload = market_get(
                f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}",
                host_key="api.nasdaq.com",
                headers={"User-Agent": _UA, "Accept": "application/json", "Origin": "https://www.nasdaq.com"},
                min_interval_s=0.5, timeout=10, retries=1, parse="json",
                log_label="Nasdaq earnings calendar",
            )
            if not payload:
                continue
            rows = ((payload.get("data") or {}).get("rows") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if symbol not in requested:
                    continue
                out.append(self._row_event(symbol, date_str, row))
        return out

    @staticmethod
    def _row_event(symbol: str, date_str: str, row: dict) -> EventItem:
        name = str(row.get("name") or "").strip()
        eps_forecast = row.get("epsForecast")
        title = f"{symbol} earnings"
        if name:
            title += f" ({name})"
        if eps_forecast not in (None, ""):
            title += f": est. EPS {eps_forecast}"
        return EventItem(
            source="nasdaq", external_id=f"{symbol}:earnings:{date_str}", event_type="earnings",
            title=title, publish_time=yf_adapter.to_utc(date_str), symbols=[symbol], importance=3,
            url=f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/earnings",
        )
