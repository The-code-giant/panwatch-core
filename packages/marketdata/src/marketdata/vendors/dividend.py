"""Dividend history vendor: Yahoo ``Ticker.dividends`` (US, CA).

Emits the last ``limit`` (default 20) cash dividends per symbol, newest first, as
``DividendItem(progress="paid")``. When ``Ticker.calendar`` announces an ex-dividend date
later than the newest paid row, an ``announced`` row is prepended (amount unknown -> ``None``).
The legacy CN ``transfer_ratio`` / ``bonus_ratio`` fields are always ``None`` for US/CA.

Per-symbol failures never kill the batch. No ``datetime.now()`` here: "later" is judged
against the newest paid ex-date, not the wall clock.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import DividendItem
from marketdata.vendors import yf_adapter
from marketdata.vendors.base import DividendVendor

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(fv) else fv


class YFinanceDividendVendor(DividendVendor):
    name = "yfinance"
    supports_markets = {"US", "CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        yf_adapter.apply_proxy(config)
        limit = int(config.get("limit") or _DEFAULT_LIMIT)
        out: list[DividendItem] = []
        for sym in symbols:
            try:
                out.extend(self._fetch_one(sym, limit=limit))
            except VendorError as e:
                logger.info(f"[dividend/yfinance] {sym.code}: {e}")
        return out

    def _fetch_one(self, sym: Symbol, *, limit: int) -> list[DividendItem]:
        code = sym.code
        t = yf_adapter.get_ticker(sym)
        dividends = yf_adapter.call("Yahoo dividends", lambda: t.dividends, symbol=code)
        rows = self.paid_rows(code, dividends, limit=limit)

        # The calendar is a separate, optional call: its failure never drops the history.
        try:
            calendar = yf_adapter.call("Yahoo calendar", lambda: t.calendar, symbol=code)
        except VendorError as e:
            logger.debug(f"[dividend/yfinance] {code} calendar failed: {e}")
            calendar = None
        announced = self.announced_row(code, calendar, rows)
        if announced is not None:
            rows.insert(0, announced)
        return rows

    @staticmethod
    def paid_rows(code: str, dividends: Any, *, limit: int) -> list[DividendItem]:
        records = yf_adapter.df_records(dividends)
        out: list[DividendItem] = []
        for row in records:
            ex_date = str(row.get("Date") or "")[:10]
            if not ex_date:
                continue
            dps = _f(row.get("Dividends"))
            if dps is None or dps <= 0:
                continue
            out.append(DividendItem(ex_date=ex_date, symbol=code, dividend_per_share=dps,
                                    transfer_ratio=None, bonus_ratio=None, progress="paid"))
        out.sort(key=lambda d: d.ex_date, reverse=True)
        if limit > 0:
            out = out[:limit]
        return out

    @staticmethod
    def announced_row(code: str, calendar: Any, paid: list[DividendItem]) -> DividendItem | None:
        if not isinstance(calendar, dict):
            return None
        raw = calendar.get("Ex-Dividend Date")
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
            if raw is None:
                return None
        dt = yf_adapter.to_utc(raw)
        if dt.year <= 1970:
            return None
        ex_date = dt.strftime("%Y-%m-%d")
        newest_paid = paid[0].ex_date if paid else ""
        if ex_date <= newest_paid:
            return None
        return DividendItem(ex_date=ex_date, symbol=code, dividend_per_share=None,
                            transfer_ratio=None, bonus_ratio=None, progress="announced")
