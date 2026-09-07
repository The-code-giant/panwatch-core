"""Corporate events collector (host side).

Thin wrapper over the ``marketdata`` package: ``MarketData.events`` returns earnings
(past and upcoming), ex-dividend and split dates for US/CA symbols. The host keeps its own
``EventItem`` dataclass so downstream consumers (SignalPack, context builder, agents) are
insulated from the package types.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class EventItem:
    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime  # the event date (aware UTC) - past or future
    symbols: list[str]
    importance: int
    url: str


def get_market_data():
    """Lazy import to avoid import cycles at module load (and to keep it monkeypatchable)."""
    from src.core.marketdata_client import get_market_data as _g

    return _g()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """Normalise naive datetimes to UTC so comparisons with the package's aware times work."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dedupe_sorted(items: list[EventItem]) -> list[EventItem]:
    items.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)
    seen: set[tuple[str, str]] = set()
    uniq: list[EventItem] = []
    for ev in items:
        key = (ev.source, ev.external_id)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ev)
    return uniq


class MarketDataEventsCollector:
    """Corporate events via the ``marketdata`` package (all enabled markets, no CN gate)."""

    source = "marketdata"

    def __init__(self, **_legacy_kwargs):
        # Legacy keyword arguments (timeout_s/proxy/...) are accepted and ignored so old
        # DataSource configs and call sites keep constructing the collector.
        self.last_error: str | None = None

    async def fetch_events(
        self,
        symbols: list[str] | None = None,
        *,
        since: datetime | None = None,
        since_days: int | None = None,
        ahead_days: int = 30,
        page_size: int = 50,  # legacy, unused
    ) -> list[EventItem]:
        """Events for ``symbols`` in ``[now - since_days, now + ahead_days]``.

        ``since`` (a datetime) is honoured for backwards compatibility: it is converted to a
        day window and then re-applied exactly on the results.
        """
        symbols_list = [s for s in (symbols or []) if s]
        if not symbols_list:
            return []

        now = _utcnow()
        if since_days is None:
            if since is not None:
                since_days = max(1, (now - _aware(since)).days + 1)
            else:
                since_days = 7
        since_days = max(int(since_days), 0)

        self.last_error = None
        try:
            md_items = await asyncio.to_thread(
                get_market_data().events,
                symbols_list,
                market=None,
                since_days=since_days,
                now=now,
                ahead_days=int(ahead_days),
            )
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Events fetch failed for {symbols_list}: {self.last_error}")
            return []

        result: list[EventItem] = []
        since_aware = _aware(since) if since is not None else None
        for it in md_items:
            if since_aware is not None and _aware(it.publish_time) < since_aware:
                continue
            result.append(
                EventItem(
                    source=it.source,
                    external_id=it.external_id,
                    event_type=it.event_type,
                    title=it.title,
                    publish_time=it.publish_time,
                    symbols=list(it.symbols or []),
                    importance=int(it.importance or 0),
                    url=it.url or "",
                )
            )
        return _dedupe_sorted(result)


# Backwards-compatible alias: older call sites constructed the Eastmoney collector directly.
EastMoneyEventsCollector = MarketDataEventsCollector


class EventsCollector:
    """Aggregate events collector.

    The package already fans out to every enabled events vendor for the symbol's market, so a
    single ``MarketDataEventsCollector`` covers all providers; ``from_database`` is kept for
    call-site compatibility (it never needs the DataSource rows any more).
    """

    def __init__(self, collectors: list[MarketDataEventsCollector] | None = None):
        self.collectors = collectors or [MarketDataEventsCollector()]

    @classmethod
    def from_database(cls) -> "EventsCollector":
        return cls(collectors=[MarketDataEventsCollector()])

    async def fetch_all(
        self,
        *,
        symbols: list[str] | None = None,
        since_days: int = 7,
        ahead_days: int = 30,
    ) -> list[EventItem]:
        since_days = max(int(since_days), 1)

        async def fetch_one(c) -> list[EventItem]:
            try:
                return await c.fetch_events(
                    symbols=symbols, since_days=since_days, ahead_days=ahead_days
                )
            except Exception as e:
                logger.warning(f"Events collector failed: {e}")
                return []

        results = await asyncio.gather(*[fetch_one(c) for c in self.collectors])
        all_items: list[EventItem] = []
        for items in results:
            all_items.extend(items)
        return _dedupe_sorted(all_items)


__all__ = [
    "EventItem",
    "EventsCollector",
    "MarketDataEventsCollector",
    "EastMoneyEventsCollector",
    "get_market_data",
]
