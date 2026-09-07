from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from marketdata import PACKAGE_VENDORS_BY_TYPE

from src.collectors.kline_collector import KlineCollector
from src.collectors.news_collector import NewsItem
from src.core.marketdata_client import md_holders, md_news, md_stock_data
from src.models.market import MarketCode
from src.models.market import StockData


logger = logging.getLogger(__name__)

# Insider transactions are summarised over a trailing window of roughly six months.
_INSIDER_WINDOW_DAYS = 183

# Holder-breakdown row keys (Yahoo ``majorHoldersBreakdown`` naming, see ``md_holders``).
_BREAKDOWN_INSIDERS = "insidersPercentHeld"
_BREAKDOWN_INSTITUTIONS = "institutionsPercentHeld"
_BREAKDOWN_INSTITUTIONS_COUNT = "institutionsCount"


@dataclass(frozen=True)
class PositionSnapshot:
    has_position: bool
    accounts: list[dict] = field(default_factory=list)
    aggregated: dict | None = None


@dataclass(frozen=True)
class NewsSnapshot:
    hours: int
    items: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class EventsSnapshot:
    days: int
    items: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class SignalPack:
    """Structured per-symbol input for the agents.

    ``holders`` is this symbol's ownership summary (see ``summarise_holders``):
    ``{symbol, insiders_pct, institutions_pct, institutions_count, insider_buys_6m,
    insider_sells_6m, insider_net_shares_6m, top_institutions, last_insider_tx, source}``,
    or ``None`` when holders were not requested or no rows came back.
    """

    symbol: str
    name: str
    market: MarketCode
    computed_at: str
    quote: StockData | None = None
    technical: dict | None = None  # kline_summary
    position: PositionSnapshot | None = None
    news: NewsSnapshot | None = None
    events: EventsSnapshot | None = None
    holders: dict | None = None
    sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _parse_holder_date(raw: str | None) -> datetime | None:
    """Parse a ``YYYY-MM-DD`` holder-row date into an aware UTC datetime (``None`` if blank/invalid)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _transaction_side(transaction: str) -> str:
    """Classify an insider transaction as ``buy`` / ``sell`` / ``other``."""
    text = (transaction or "").lower()
    if "purchase" in text or "buy" in text or "acquisition" in text:
        return "buy"
    if "sale" in text or "sell" in text or "disposition" in text:
        return "sell"
    return "other"


def summarise_holders(symbol: str, rows: list[dict], now: datetime | None = None) -> dict | None:
    """Collapse ``md_holders`` rows for one symbol into the agent-facing summary.

    Args:
        symbol: the symbol the rows belong to (echoed into the summary)
        rows: breakdown / institution / insider_tx rows for that symbol
        now: host-side aware UTC clock; the 6-month insider window is measured back from it

    Returns ``None`` when there are no rows at all.
    """
    if not rows:
        return None
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_INSIDER_WINDOW_DAYS)

    insiders_pct: float | None = None
    institutions_pct: float | None = None
    institutions_count: int | None = None
    institutions: list[dict] = []
    buys = 0
    sells = 0
    net_shares = 0.0
    latest_tx: tuple[datetime, dict] | None = None
    source = ""

    for r in rows:
        if not source and r.get("source"):
            source = str(r.get("source"))
        kind = r.get("kind")
        if kind == "breakdown":
            holder = r.get("holder") or ""
            if holder == _BREAKDOWN_INSIDERS:
                insiders_pct = _to_float(r.get("pct_out"))
            elif holder == _BREAKDOWN_INSTITUTIONS:
                institutions_pct = _to_float(r.get("pct_out"))
            elif holder == _BREAKDOWN_INSTITUTIONS_COUNT:
                count = _to_float(r.get("shares"))
                institutions_count = int(count) if count is not None else None
        elif kind == "institution":
            institutions.append(
                {
                    "holder": r.get("holder") or "",
                    "pct_out": _to_float(r.get("pct_out")),
                    "change_pct": _to_float(r.get("change_pct")),
                }
            )
        elif kind == "insider_tx":
            tx_date = _parse_holder_date(r.get("date"))
            tx = {
                "holder": r.get("holder") or "",
                "transaction": r.get("transaction") or "",
                "shares": _to_float(r.get("shares")),
                "date": (r.get("date") or "")[:10],
            }
            if tx_date is not None and (latest_tx is None or tx_date > latest_tx[0]):
                latest_tx = (tx_date, tx)
            if tx_date is None or tx_date < cutoff or tx_date > now:
                continue
            side = _transaction_side(tx["transaction"])
            shares = tx["shares"] or 0.0
            if side == "buy":
                buys += 1
                net_shares += shares
            elif side == "sell":
                sells += 1
                net_shares -= shares

    institutions.sort(key=lambda x: (x.get("pct_out") or 0.0), reverse=True)

    return {
        "symbol": symbol,
        "insiders_pct": insiders_pct,
        "institutions_pct": institutions_pct,
        "institutions_count": institutions_count,
        "insider_buys_6m": buys,
        "insider_sells_6m": sells,
        "insider_net_shares_6m": net_shares,
        "top_institutions": institutions[:3],
        "last_insider_tx": latest_tx[1] if latest_tx else None,
        "source": source,
    }


def holders_summary_for(pack) -> dict | None:
    """Return the ownership summary carried by ``pack.holders``.

    ``pack.holders`` is normally the summary itself; a ``{symbol: summary}`` map is tolerated.
    """
    holders = getattr(pack, "holders", None) if pack else None
    if not isinstance(holders, dict) or not holders:
        return None
    if "insiders_pct" in holders or "insider_net_shares_6m" in holders:
        return holders
    inner = holders.get(getattr(pack, "symbol", "") or "")
    return inner if isinstance(inner, dict) and inner else None


def ownership_lines(holders: dict | None) -> list[str]:
    """Prompt bullet lines for the Ownership block; empty when there is nothing to say."""
    if not holders:
        return []

    def pct(v) -> str:
        return f"{v:.1f}%" if v is not None else "n/a"

    insiders = _to_float(holders.get("insiders_pct"))
    institutions = _to_float(holders.get("institutions_pct"))
    buys = int(holders.get("insider_buys_6m") or 0)
    sells = int(holders.get("insider_sells_6m") or 0)
    net = _to_float(holders.get("insider_net_shares_6m")) or 0.0
    top = holders.get("top_institutions") or []
    last = holders.get("last_insider_tx") or None
    if insiders is None and institutions is None and not buys and not sells and not top and not last:
        return []

    lines = [
        f"- Ownership: insiders {pct(insiders)}, institutions {pct(institutions)}; "
        f"insider net {net:+,.0f} shares over 6 months ({buys} buys / {sells} sells)"
    ]
    if top:
        t = top[0] or {}
        change = _to_float(t.get("change_pct"))
        change_str = f", {change:+.1f}% position change" if change is not None else ""
        lines.append(
            f"- Top institution: {t.get('holder') or 'unknown'} ({pct(_to_float(t.get('pct_out')))} of shares outstanding{change_str})"
        )
    if last:
        shares = _to_float(last.get("shares"))
        shares_str = f" {shares:,.0f} shares" if shares is not None else ""
        date_str = f" on {last.get('date')}" if last.get("date") else ""
        lines.append(
            f"- Last insider transaction: {last.get('holder') or 'unknown'} — {last.get('transaction') or 'n/a'}{shares_str}{date_str}"
        )
    return lines


class SignalPackBuilder:
    """Build structured inputs for agents.

    This is an in-memory per-run cache to reduce repeated network calls.
    """

    def __init__(self):
        self._quote_cache: dict[tuple[MarketCode, str], StockData | None] = {}
        self._quote_source_cache: dict[tuple[MarketCode, str], str] = {}
        self._tech_cache: dict[tuple[MarketCode, str], dict] = {}
        self._tech_source_cache: dict[tuple[MarketCode, str], str] = {}
        self._news_cache: dict[tuple[MarketCode, str, int], list[NewsItem]] = {}
        self._holders_cache: dict[tuple[MarketCode, str], dict | None] = {}
        self._holders_source_cache: dict[tuple[MarketCode, str], str] = {}
        self._events_cache: dict[tuple[str, int], list[dict]] = {}
        self._events_source_cache: dict[tuple[str, int], str] = {}

    @staticmethod
    def _source_policy(
        source_type: str, *, default_providers: list[str]
    ) -> tuple[list[tuple[str, dict]], bool]:
        """Return (providers, disabled).

        - If DB has no sources of this type: use defaults.
        - If DB has sources but all are disabled: disabled=True.
        - If some enabled: return them ordered by priority.
        """

        try:
            from src.web.database import SessionLocal
            from src.web.models import DataSource

            db = SessionLocal()
            try:
                total = (
                    db.query(DataSource.id)
                    .filter(DataSource.type == source_type)
                    .count()
                )
                if total == 0:
                    return [(p, {}) for p in default_providers], False
                enabled = (
                    db.query(DataSource)
                    .filter(DataSource.type == source_type, DataSource.enabled == True)
                    .order_by(DataSource.priority)
                    .all()
                )
                if not enabled:
                    return [], True
                return [
                    ((r.provider or "").strip(), (r.config or {}))
                    for r in enabled
                    if (r.provider or "").strip()
                ], False
            finally:
                db.close()
        except Exception:
            return [(p, {}) for p in default_providers], False

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _provider_supported(source_type: str, provider: str) -> bool:
        """True when ``provider`` is a vendor the marketdata package implements for ``source_type``."""
        if provider in PACKAGE_VENDORS_BY_TYPE.get(source_type, frozenset()):
            return True
        logger.info(f"SignalPack {source_type}: provider={provider} is not a package vendor, skipped")
        return False

    async def build_for_symbols(
        self,
        *,
        symbols: list[tuple[str, MarketCode, str]],
        include_news: bool,
        news_hours: int,
        portfolio,
        include_technical: bool = True,
        include_events: bool = False,
        events_days: int = 7,
        include_holders: bool = False,
    ) -> dict[str, SignalPack]:
        """Build packs for multiple symbols.

        Args:
            symbols: list of (symbol, market, name)
            include_news: whether to include news snapshot
            news_hours: window for news
            portfolio: AgentContext.portfolio
            include_technical: whether to include the kline summary
            include_events: whether to include event snapshot
            events_days: lookback window in days
            include_holders: whether to include the ownership (holders / insiders) summary
        """

        computed_at = self._now_iso()
        symbol_set = {s for s, _, _ in symbols}
        names_by_symbol = {s: n for s, _, n in symbols if n}

        quote_providers, quote_disabled = self._source_policy(
            "quote", default_providers=["yfinance"]
        )
        kline_providers, kline_disabled = self._source_policy(
            "kline", default_providers=["yfinance"]
        )
        news_providers, news_disabled = self._source_policy(
            "news", default_providers=["yfinance", "google_news"]
        )
        events_providers, events_disabled = self._source_policy(
            "events", default_providers=["yfinance"]
        )
        holders_providers, holders_disabled = self._source_policy(
            "holders", default_providers=["yfinance"]
        )

        # Group symbols per market; every market-level call below iterates this.
        by_market: dict[MarketCode, list[tuple[str, str]]] = {}
        for sym, market, name in symbols:
            by_market.setdefault(market, []).append((sym, name))

        # 1) Quotes (batch per market)
        quote_map: dict[str, StockData | None] = {}
        for market, items in by_market.items():
            missing = [s for s, _ in items if (market, s) not in self._quote_cache]
            if missing:
                if quote_disabled:
                    for sym in missing:
                        self._quote_cache[(market, sym)] = None
                        self._quote_source_cache[(market, sym)] = "disabled"
                else:
                    remaining = set(missing)
                    for provider, cfg in quote_providers:
                        if not remaining:
                            break
                        if not self._provider_supported("quote", provider):
                            continue
                        try:
                            stocks = await asyncio.to_thread(
                                md_stock_data, sorted(remaining), market.value
                            )
                            got = {s.symbol: s for s in stocks}
                            for sym in list(remaining):
                                sd = got.get(sym)
                                if not sd:
                                    continue
                                self._quote_cache[(market, sym)] = sd
                                self._quote_source_cache[(market, sym)] = provider
                                remaining.discard(sym)
                        except Exception as e:
                            logger.warning(
                                f"SignalPack quotes fetch failed ({market.value},{provider}): {e}"
                            )
                            continue

                    for sym in remaining:
                        self._quote_cache[(market, sym)] = None
                        self._quote_source_cache.setdefault(
                            (market, sym), "unavailable"
                        )

            for sym, _ in items:
                quote_map[sym] = self._quote_cache.get((market, sym))
                if (
                    quote_map[sym] is not None
                    and (market, sym) not in self._quote_source_cache
                ):
                    self._quote_source_cache[(market, sym)] = "cache"

        # 2) Technical
        tech_map: dict[str, dict | None] = {}
        if include_technical:
            for sym, market, _ in symbols:
                key = (market, sym)
                if key not in self._tech_cache:
                    if kline_disabled:
                        self._tech_cache[key] = {"error": "Candlestick data source is disabled"}
                        self._tech_source_cache[key] = "disabled"
                    else:
                        last_err = None
                        for provider, cfg in kline_providers:
                            if not self._provider_supported("kline", provider):
                                continue
                            try:
                                collector = KlineCollector(market)
                                self._tech_cache[key] = collector.get_kline_summary(sym)
                                self._tech_source_cache[key] = provider
                                last_err = None
                                break
                            except Exception as e:
                                last_err = e
                                continue
                        if key not in self._tech_cache:
                            self._tech_cache[key] = {
                                "error": str(last_err) if last_err else "Failed to fetch candlesticks"
                            }
                            self._tech_source_cache.setdefault(key, "unavailable")
                tech_map[sym] = self._tech_cache[key]
                if key in self._tech_cache and key not in self._tech_source_cache:
                    self._tech_source_cache[key] = "cache"

        # 3) News (one package call per market)
        news_by_symbol: dict[str, list[dict]] = {}
        news_source = "skipped"
        if include_news:
            news_source = "disabled" if news_disabled else "marketdata"
            news_supported = any(
                p in PACKAGE_VENDORS_BY_TYPE.get("news", frozenset())
                for p, _ in news_providers
            )
            if not news_disabled and not news_supported:
                for provider, _ in news_providers:
                    self._provider_supported("news", provider)
                news_source = "unavailable"
            for market, items in by_market.items():
                market_syms = sorted(s for s, _ in items)
                key = (market, ",".join(market_syms), int(news_hours))
                if key not in self._news_cache:
                    if news_disabled or not news_supported:
                        self._news_cache[key] = []
                    else:
                        try:
                            names = {s: names_by_symbol[s] for s in market_syms if s in names_by_symbol}
                            self._news_cache[key] = await asyncio.to_thread(
                                md_news, market_syms, int(news_hours), names, market.value
                            )
                        except Exception as e:
                            logger.warning(f"SignalPack news fetch failed ({market.value}): {e}")
                            self._news_cache[key] = []

                for it in self._news_cache[key]:
                    # attach to each symbol
                    for sym in it.symbols or []:
                        if sym not in symbol_set:
                            continue
                        news_by_symbol.setdefault(sym, []).append(
                            {
                                "source": it.source,
                                "publisher": getattr(it, "publisher", "") or "",
                                "external_id": it.external_id,
                                "title": it.title,
                                "time": it.publish_time.strftime("%Y-%m-%d %H:%M"),
                                "importance": it.importance,
                                "url": it.url,
                            }
                        )

        # 4) Holders / insiders (one package call per market)
        holders_map: dict[str, dict | None] = {}
        if include_holders:
            now_utc = datetime.now(timezone.utc)
            holders_supported = any(
                p in PACKAGE_VENDORS_BY_TYPE.get("holders", frozenset())
                for p, _ in holders_providers
            )
            for market, items in by_market.items():
                missing = [s for s, _ in items if (market, s) not in self._holders_cache]
                if missing:
                    if holders_disabled:
                        for sym in missing:
                            self._holders_cache[(market, sym)] = None
                            self._holders_source_cache[(market, sym)] = "disabled"
                    elif not holders_supported:
                        for provider, _ in holders_providers:
                            self._provider_supported("holders", provider)
                        for sym in missing:
                            self._holders_cache[(market, sym)] = None
                            self._holders_source_cache[(market, sym)] = "unavailable"
                    else:
                        rows: list[dict] = []
                        try:
                            rows = await asyncio.to_thread(
                                md_holders, sorted(missing), market.value
                            )
                        except Exception as e:
                            logger.warning(
                                f"SignalPack holders fetch failed ({market.value}): {e}"
                            )
                        by_symbol: dict[str, list[dict]] = {}
                        for r in rows or []:
                            by_symbol.setdefault(str(r.get("symbol") or "").strip(), []).append(r)
                        upper_index = {k.upper(): k for k in by_symbol}
                        for sym in missing:
                            sym_rows = by_symbol.get(sym) or by_symbol.get(
                                upper_index.get(sym.upper(), ""), []
                            )
                            summary = summarise_holders(sym, sym_rows, now=now_utc)
                            self._holders_cache[(market, sym)] = summary
                            self._holders_source_cache[(market, sym)] = (
                                (summary.get("source") or "marketdata") if summary else "unavailable"
                            )
                for sym, _ in items:
                    holders_map[sym] = self._holders_cache.get((market, sym))
                    if (
                        holders_map[sym] is not None
                        and (market, sym) not in self._holders_source_cache
                    ):
                        self._holders_source_cache[(market, sym)] = "cache"

        # 5) Events
        events_by_symbol: dict[str, list[dict]] = {}
        events_key = (",".join(sorted(symbol_set)), int(events_days))
        if include_events:
            if events_key not in self._events_cache:
                if events_disabled:
                    self._events_cache[events_key] = []
                    self._events_source_cache[events_key] = "disabled"
                else:
                    last_err = None
                    used_provider = ""
                    for provider, cfg in events_providers:
                        if not self._provider_supported("events", provider):
                            continue
                        try:
                            from src.collectors.events_collector import EventsCollector

                            collector = EventsCollector.from_database()
                            items = await collector.fetch_all(
                                symbols=sorted(symbol_set),
                                since_days=int(events_days),
                            )

                            packed: list[dict] = []
                            for it in items:
                                packed.append(
                                    {
                                        "source": it.source,
                                        "external_id": it.external_id,
                                        "event_type": it.event_type,
                                        "title": it.title,
                                        "time": it.publish_time.strftime(
                                            "%Y-%m-%d %H:%M"
                                        ),
                                        "importance": it.importance,
                                        "url": it.url,
                                        "symbols": it.symbols,
                                    }
                                )

                            self._events_cache[events_key] = packed
                            used_provider = provider
                            self._events_source_cache[events_key] = used_provider
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            continue

                    if events_key not in self._events_cache:
                        logger.warning(f"SignalPack events fetch failed: {last_err}")
                        self._events_cache[events_key] = []
                        self._events_source_cache[events_key] = "unavailable"

            for it in self._events_cache.get(events_key, []):
                for sym in it.get("symbols") or []:
                    if sym not in symbol_set:
                        continue
                    events_by_symbol.setdefault(sym, []).append(it)

        # 6) Position
        packs: dict[str, SignalPack] = {}
        for sym, market, name in symbols:
            pos_list = []
            try:
                pos_list = portfolio.get_positions_for_stock(sym, market)
            except Exception:
                pos_list = []

            accounts = []
            for p in pos_list:
                try:
                    accounts.append(
                        {
                            "account_name": getattr(p, "account_name", ""),
                            "cost_price": getattr(p, "cost_price", None),
                            "quantity": getattr(p, "quantity", None),
                            "trading_style": getattr(p, "trading_style", ""),
                        }
                    )
                except Exception:
                    continue

            aggregated = None
            try:
                aggregated = portfolio.get_aggregated_position(sym)
            except Exception:
                aggregated = None

            missing: list[str] = []
            if quote_map.get(sym) is None:
                missing.append("quote")
            if include_technical:
                tech = tech_map.get(sym) or {}
                if not tech or tech.get("error"):
                    missing.append("kline")
            if include_news:
                if not news_by_symbol.get(sym):
                    missing.append("news")
            if include_events:
                if not events_by_symbol.get(sym):
                    missing.append("events")
            if include_holders and not holders_map.get(sym):
                missing.append("holders")

            packs[sym] = SignalPack(
                symbol=sym,
                name=name,
                market=market,
                computed_at=computed_at,
                quote=quote_map.get(sym),
                technical=tech_map.get(sym) if include_technical else None,
                position=PositionSnapshot(
                    has_position=bool(pos_list),
                    accounts=accounts,
                    aggregated=aggregated,
                ),
                news=NewsSnapshot(
                    hours=news_hours, items=news_by_symbol.get(sym, [])[:5]
                )
                if include_news
                else None,
                events=EventsSnapshot(
                    days=int(events_days), items=events_by_symbol.get(sym, [])[:5]
                )
                if include_events
                else None,
                holders=holders_map.get(sym) if include_holders else None,
                sources={
                    "quote": self._quote_source_cache.get((market, sym), "unknown"),
                    "kline": self._tech_source_cache.get((market, sym), "unknown")
                    if include_technical
                    else "skipped",
                    "news": news_source,
                    "events": self._events_source_cache.get(events_key, "unknown")
                    if include_events
                    else "skipped",
                    "holders": self._holders_source_cache.get((market, sym), "unknown")
                    if include_holders
                    else "skipped",
                },
                missing=missing,
            )

        return packs
