"""Yahoo Finance quote and kline vendors (US / CA / CRYPTO / GOLD; HK legacy) via ``yf_adapter``.

Stateless. The library is imported lazily inside the adapter; a missing library raises
``VendorError`` so the Engine fails over. Units are raw native currency (market caps are
absolute, not Tencent's 亿 scale); consumers format them.
"""

from __future__ import annotations

import logging

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.symbol import Symbol
from marketdata.types import Bar, Quote
from marketdata.vendors import yf_adapter
from marketdata.vendors.base import KlineVendor, QuoteVendor

logger = logging.getLogger(__name__)


def _yf_ticker(sym: Symbol) -> str:
    return yf_adapter.to_yahoo_symbol(sym)


def _num(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _enrich_from_info(q: Quote, info: dict) -> None:
    """Fill name / valuation / liquidity fields from ``Ticker.info``. Missing keys stay None."""
    if not info:
        return
    q.name = str(info.get("shortName") or info.get("longName") or q.name or "")
    pe = _num(info.get("trailingPE"))
    if pe is not None:
        q.pe_ratio = pe
    mcap = _num(info.get("marketCap"))
    if mcap is not None:
        q.total_market_value = mcap
    float_shares = _num(info.get("floatShares"))
    last = _num(q.current_price)
    vol = _num(q.volume)
    if float_shares and last:
        q.circulating_market_value = float_shares * last
    if float_shares and vol is not None:
        q.turnover_rate = vol / float_shares * 100.0
    if last and vol is not None:
        q.turnover = last * vol
    avg_vol = _num(info.get("averageVolume"))
    if avg_vol and vol is not None:
        q.volume_ratio = vol / avg_vol


class YFinanceQuoteVendor(QuoteVendor):
    name = "yfinance"
    supports_markets = {"US", "CA", "HK", "CRYPTO", "GOLD"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        yf_adapter.apply_proxy(config or {})
        out: list[Quote] = []
        for s in symbols:
            ysym = _yf_ticker(s)
            try:
                fq = yf_adapter.fast_quote(ysym)
            except VendorError as e:
                logger.debug(f"yfinance quote {s.code} failed: {e}")
                continue
            if not fq or not fq.get("last"):
                record_error(f"yfinance {ysym}: empty (no last price; Yahoo unreachable, throttled, or proxy needed)")
                continue
            last = float(fq["last"])
            prev = fq.get("prev_close")
            chg = last - prev if prev else 0.0
            pct = (chg / prev * 100) if prev else 0.0
            q = Quote(
                symbol=s.code, market=s.market.value, name="",
                current_price=last, prev_close=prev,
                open_price=fq.get("open"), high_price=fq.get("high"), low_price=fq.get("low"),
                change_amount=chg, change_pct=pct, volume=fq.get("volume"),
            )
            # Enrichment is best effort: an ``info`` failure never drops the quote.
            try:
                _enrich_from_info(q, yf_adapter.info(ysym))
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"yfinance info enrichment {s.code} skipped: {e}")
            out.append(q)
        return out


class YFinanceKlineVendor(KlineVendor):
    """Daily bars via ``yf_adapter.history`` (library path; the raw chart v8 vendor is the last resort)."""

    name = "yfinance"
    supports_markets = {"US", "CA", "HK", "CRYPTO", "GOLD"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        try:
            days = int((config or {}).get("days") or 60)
        except (TypeError, ValueError):
            days = 60
        yf_adapter.apply_proxy(config or {})
        return yf_adapter.history(_yf_ticker(sym), days=days)
