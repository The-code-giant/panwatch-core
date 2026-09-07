"""Discovery collector: hot stocks / sector boards / board constituents via the marketdata
package, filtered through the tradable universe.

Every rank asks the vendor for ``limit * 2`` rows, drops rows that fail ``is_tradable``
(module-level name so tests can monkeypatch ``discovery_collector.is_tradable``), and
slices to ``limit``. ``universe.is_tradable`` fails open when a market has no rows, so a
directory outage never empties discovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core import universe
from src.models.market import default_market

logger = logging.getLogger(__name__)

# Monkeypatchable seam (tests set ``discovery_collector.is_tradable = lambda m, s: True``).
is_tradable = universe.is_tradable


@dataclass(frozen=True)
class HotStock:
    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None


@dataclass(frozen=True)
class HotBoard:
    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


def get_market_data():
    """Lazy import to avoid import cycles at module load (and so tests can monkeypatch)."""
    from src.core.marketdata_client import get_market_data as _g

    return _g()


def _to_hot_stock(it) -> HotStock:
    return HotStock(
        symbol=it.symbol,
        market=it.market,
        name=it.name,
        price=it.price,
        change_pct=it.change_pct,
        turnover=it.turnover,
        volume=it.volume,
    )


def _filter_tradable(items, limit: int) -> list[HotStock]:
    """Map vendor rows to HotStock, drop non-tradable symbols (module-global seam), slice."""
    out: list[HotStock] = []
    for it in items or []:
        symbol = (getattr(it, "symbol", "") or "").strip()
        market = (getattr(it, "market", "") or "").strip().upper()
        if not symbol:
            continue
        if not is_tradable(market, symbol):
            continue
        out.append(_to_hot_stock(it))
        if len(out) >= limit:
            break
    return out


class DiscoveryCollector:
    """Discovery ranks for the enabled equity markets (US / CA) through the marketdata package."""

    def __init__(self, *, proxy: str | None = None):
        self.proxy = proxy

    async def fetch_hot_stocks(
        self,
        *,
        market: str = "",
        mode: str = "turnover",
        limit: int = 20,
    ) -> list[HotStock]:
        import asyncio as _aio

        limit = max(int(limit or 0), 1)
        pkg_items = await _aio.to_thread(
            get_market_data().hot_stocks,
            market=market or default_market(),
            mode=mode,
            limit=limit * 2,
            proxy=self.proxy,
        )
        return _filter_tradable(pkg_items, limit)

    async def fetch_hot_boards(
        self,
        *,
        market: str = "",
        mode: str = "gainers",
        limit: int = 12,
    ) -> list[HotBoard]:
        import asyncio as _aio

        limit = max(int(limit or 0), 1)
        pkg_items = await _aio.to_thread(
            get_market_data().hot_boards,
            market=market or default_market(),
            mode=mode,
            limit=limit,
            proxy=self.proxy,
        )
        return [
            HotBoard(
                code=it.code,
                name=it.name,
                change_pct=it.change_pct,
                change_amount=it.change_amount,
                turnover=it.turnover,
            )
            for it in (pkg_items or [])
        ][:limit]

    async def fetch_board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
    ) -> list[HotStock]:
        import asyncio as _aio

        limit = max(int(limit or 0), 1)
        pkg_items = await _aio.to_thread(
            get_market_data().board_stocks,
            board_code=board_code,
            mode=mode,
            limit=limit * 2,
            proxy=self.proxy,
        )
        return _filter_tradable(pkg_items, limit)


# Backwards-compatible alias (older imports).
EastMoneyDiscoveryCollector = DiscoveryCollector
