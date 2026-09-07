"""Market index strip API (public, no auth)."""
import asyncio
import logging
import time
from fastapi import APIRouter

from src.collectors.kline_collector import get_index_klines
from src.models.market import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """Lazy import so a missing package / import cycle cannot break module load."""
    from src.core.marketdata_client import get_market_data as _g

    return _g()

# Index strip: S&P 500, Nasdaq Composite, Dow Jones, S&P/TSX Composite.
# Yahoo (yfinance adapter, raw chart v8 fallback) is the primary source for all four; Tencent
# (usINX / usIXIC / usDJI) is asked only for US rows Yahoo could not fill. Sparklines go
# through get_index_klines (Yahoo-first for US/CA).
# response_symbol: the symbol Tencent echoes back (used to match its quotes).
MARKET_INDICES = [
    {"symbol": "INX", "name": "S&P 500", "market": "US", "tencent_symbol": "usINX", "response_symbol": ".INX", "yahoo_symbol": "^GSPC"},
    {"symbol": "IXIC", "name": "Nasdaq Composite", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC", "yahoo_symbol": "^IXIC"},
    {"symbol": "DJI", "name": "Dow Jones", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI", "yahoo_symbol": "^DJI"},
    {"symbol": "GSPTSE", "name": "S&P/TSX Composite", "market": "CA", "tencent_symbol": None, "response_symbol": None, "yahoo_symbol": "^GSPTSE"},
]

# Whole-response cache: 60 s (prices must stay fresh).
_INDICES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_INDICES_CACHE_TTL_S = 60

# Sparkline (last 20 closes) cache: daily bars change once a day, 30 min is plenty. Without
# it every 60 s response expiry re-pays four index kline fetches. Empty results are cached too.
_SPARK_CACHE: dict[str, tuple[float, list[float]]] = {}
_SPARK_TTL_S = 1800


def clear_indices_cache() -> None:
    """Clear the response / spark caches (test isolation)."""
    _INDICES_CACHE.clear()
    _SPARK_CACHE.clear()


def _spark_for(idx: dict) -> list[float]:
    """Last 20 closes for the home-page sparkline (30 min cache).

    Fail-soft: a bad market code, fetch error or unknown index yields [] and never
    affects the quote body. US/CA indices come from marketdata.index_klines (Yahoo).
    """
    now = time.time()
    hit = _SPARK_CACHE.get(idx["symbol"])
    if hit and now - hit[0] < _SPARK_TTL_S:
        return hit[1]
    try:
        market_code = MarketCode(idx["market"])
        klines = get_index_klines(idx["symbol"], market_code, days=20)
        spark = [k.close for k in klines] if klines else []
    except Exception as e:
        logger.debug(f"Index spark fetch failed {idx['symbol']}: {e}")
        spark = []
    _SPARK_CACHE[idx["symbol"]] = (now, spark)
    return spark


@router.get("/indices")
async def get_market_indices():
    """Main market indices (public data, no auth)."""
    now = time.time()
    cached = _INDICES_CACHE.get("indices")
    if cached and now - cached[0] < _INDICES_CACHE_TTL_S:
        return cached[1]

    md = get_market_data()

    # Yahoo first for all four indices. Fail-soft: an exception leaves quote_map empty and
    # Tencent fills whatever US rows it can below.
    quote_map: dict = {}
    yahoo_symbols = [idx["yahoo_symbol"] for idx in MARKET_INDICES if idx.get("yahoo_symbol")]
    if yahoo_symbols:
        try:
            for q in await asyncio.to_thread(md.yahoo_index_quotes, yahoo_symbols):
                quote_map[q["symbol"]] = q
        except Exception as e:
            logger.error(f"Failed to fetch market indices from Yahoo: {e}")

    # Tencent: only the US indices Yahoo did not return.
    tencent_needed = [
        idx["tencent_symbol"] for idx in MARKET_INDICES
        if idx.get("tencent_symbol") and not quote_map.get(idx.get("yahoo_symbol") or "")
    ]
    if tencent_needed:
        try:
            for q in md.index_quotes(tencent_needed):
                quote_map[q["symbol"]] = q
        except Exception as e:
            logger.error(f"Failed to fetch market indices from Tencent: {e}")

    # Sparks in parallel (free while cached; cold start costs the slowest single fetch)
    sparks = await asyncio.gather(
        *[asyncio.to_thread(_spark_for, idx) for idx in MARKET_INDICES],
        return_exceptions=True,
    )
    spark_map = {
        idx["symbol"]: (sp if isinstance(sp, list) else [])
        for idx, sp in zip(MARKET_INDICES, sparks)
    }

    result = []
    for idx in MARKET_INDICES:
        # Match Yahoo by yahoo_symbol, else Tencent by response_symbol
        quote = None
        if idx.get("yahoo_symbol"):
            quote = quote_map.get(idx["yahoo_symbol"])
        if not quote and idx.get("response_symbol"):
            quote = quote_map.get(idx["response_symbol"])
        spark = spark_map.get(idx["symbol"], [])

        if quote:
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"],
                "change_pct": quote["change_pct"],
                "change_amount": quote["change_amount"],
                "prev_close": quote["prev_close"],
                "spark": spark,
            })
        else:
            # No quote: still return the row so the strip keeps its shape
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": None,
                "change_pct": None,
                "change_amount": None,
                "prev_close": None,
                "spark": spark,
            })

    _INDICES_CACHE["indices"] = (now, result)
    return result
