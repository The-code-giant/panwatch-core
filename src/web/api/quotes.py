import asyncio
import logging
import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.marketdata_client import md_quote_rows
from src.models.market import MarketCode
from src.models.market import default_market, is_enabled

logger = logging.getLogger(__name__)
router = APIRouter()


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    code = (market or default_market()).strip().upper()
    try:
        mc = MarketCode(code)
    except ValueError:
        raise HTTPException(400, f"Unsupported market: {market}")
    if not is_enabled(mc):
        raise HTTPException(400, f"Market not enabled: {code}")
    return mc


def _classify_market(raw: str) -> tuple[MarketCode | None, str, str | None]:
    """Classify a raw market string for the batch endpoint.

    Returns (market_code_or_None, normalized_code_string, unsupported_reason_or_None).
    Never raises: an unknown or disabled market is reported via the reason instead,
    so one bad item can't abort the whole batch.
    """
    code = (raw or "").strip().upper()
    if not code:
        code = default_market()
    try:
        mc = MarketCode(code)
    except ValueError:
        return None, code, "unknown_market"
    if not is_enabled(mc):
        return None, code, "market_not_enabled"
    return mc, code, None


def _prev_close_or_none(value) -> float | None:
    """Canonical daily-P&L basis for the batch rows (same boundary as
    ``src.web.api.accounts._usable_prev_close``): a finite, strictly positive number,
    else ``None``. The client computes daily P&L from this field ONLY and never
    reconstructs the previous close from a rounded ``change_pct``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        prev = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(prev) or prev <= 0:
        return None
    return prev


def _quote_to_response(
    symbol: str,
    market: str,
    quote: dict | None,
    *,
    supported: bool,
    unsupported_reason: str | None,
) -> dict:
    if not quote:
        return {
            "symbol": symbol,
            "market": market,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "pe_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
            "supported": supported,
            "unsupported_reason": unsupported_reason,
        }

    return {
        "symbol": symbol,
        "market": market,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": _prev_close_or_none(quote.get("prev_close")),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "pe_ratio": quote.get("pe_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
        "supported": supported,
        "unsupported_reason": unsupported_reason,
    }


@router.get("/{symbol}")
async def get_quote(symbol: str, market: str = ""):
    """获取单只股票实时行情"""
    market_code = _parse_market(market)
    rows = await asyncio.to_thread(md_quote_rows, [symbol], market_code.value)
    if not rows:
        raise HTTPException(404, "Quote not found")
    quote_map = {item.get("symbol"): item for item in rows}
    quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "Quote not found")
    return _quote_to_response(
        symbol, market_code.value, quote, supported=True, unsupported_reason=None
    )


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票实时行情

    An unsupported item (unknown or disabled market) never aborts the batch: it is
    echoed back as an unsupported row and every other item is still resolved.
    """
    if not payload.items:
        return []

    # Classify every item up front; group only supported items for a per-market fetch.
    classified: list[tuple[MarketCode | None, str, str | None]] = []
    market_items: dict[MarketCode, list[str]] = {}
    for item in payload.items:
        market_code, normalized, reason = _classify_market(item.market)
        classified.append((market_code, normalized, reason))
        if market_code is not None and reason is None:
            market_items.setdefault(market_code, []).append(item.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        try:
            rows = await asyncio.to_thread(md_quote_rows, symbols, market_code.value)
            quotes_by_market[market_code] = {item.get("symbol"): item for item in rows}
        except Exception as e:
            # A fetch failure for one market must not abort the others: fall back to
            # null-price rows for this market only.
            logger.warning(f"Batch quote fetch failed for market {market_code.value}: {e}")
            quotes_by_market[market_code] = {}

    results = []
    for item, (market_code, normalized, reason) in zip(payload.items, classified):
        if market_code is None or reason is not None:
            results.append(
                _quote_to_response(
                    item.symbol,
                    normalized,
                    None,
                    supported=False,
                    unsupported_reason=reason,
                )
            )
            continue
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(
            _quote_to_response(
                item.symbol,
                market_code.value,
                quote,
                supported=True,
                unsupported_reason=None,
            )
        )

    return results
