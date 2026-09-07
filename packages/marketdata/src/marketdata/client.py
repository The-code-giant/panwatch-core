"""Object-style entry point: inject a ConfigProvider (+ optional MetricsSink) and call
``quotes()`` / ``klines()`` / ``news()`` / ... Every symbol-based type goes through an Engine
(priority failover + TTL cache + metrics); ``news`` aggregates across vendors; discovery, the
index strip and ``macro`` are market-level and bypass the Engine.

The package never calls ``datetime.now()``: every time window is applied here from a
caller-supplied ``now`` (no ``now`` -> no filtering).
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.registry import build_vendors
from marketdata.symbol import Symbol
from marketdata.types import (
    DividendItem,
    EventItem,
    FilingItem,
    FlashNews,
    Fundamentals,
    HolderItem,
    HotBoard,
    HotStock,
    NewsArticle,
    Quote,
    Request,
)
from marketdata.vendors import yf_adapter
from marketdata.vendors.discovery import DiscoveryVendor
from marketdata.vendors.news import GoogleNewsRssVendor, _canon_url, _norm_title

logger = logging.getLogger(__name__)

# Eastmoney index secids (legacy CN/HK): index and stock secid prefixes differ, so they are mapped
# explicitly. US indices have no Eastmoney secid.
INDEX_SECID: dict[str, str] = {
    "000300": "1.000300",   # CSI 300
    "000001": "1.000001",   # SSE Composite
    "399001": "0.399001",   # SZSE Component
    "399006": "0.399006",   # ChiNext
    "HSI": "100.HSI",       # Hang Seng
}

# Raw Tencent index symbols (legacy fallback for index_klines).
INDEX_TENCENT: dict[str, str] = {
    "000001": "sh000001",
    "399001": "sz399001",
    "399006": "sz399006",
    "000300": "sh000300",
    "HSI": "hkHSI",
    "IXIC": "usIXIC",
    "DJI": "usDJI",
    "INX": "usINX",
}

# Yahoo index symbols: primary source for US/CA index bars and quotes (long history, no crumb).
INDEX_YAHOO: dict[str, str] = {
    "INX": "^GSPC",
    ".INX": "^GSPC",
    "GSPC": "^GSPC",
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    ".IXIC": "^IXIC",
    "DJI": "^DJI",
    ".DJI": "^DJI",
    "GSPTSE": "^GSPTSE",
    "TSX": "^GSPTSE",
}

INDEX_NAMES: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^GSPTSE": "S&P/TSX Composite",
    "^VIX": "VIX",
    "^TNX": "US 10Y Yield",
    "CAD=X": "USD/CAD",
    "CL=F": "WTI Crude",
    "GC=F": "Gold",
}

MACRO_SYMBOLS: tuple[str, ...] = (
    "^VIX", "^TNX", "CAD=X", "CL=F", "GC=F", "^GSPC", "^IXIC", "^DJI", "^GSPTSE",
)


def _group_by_market(symbols, market: str | None) -> dict[str, list[Symbol]]:
    groups: dict[str, list[Symbol]] = {}
    for raw in symbols:
        sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
        groups.setdefault(sym.market.value, []).append(sym)
    return groups


class MarketData:
    def __init__(self, config: ConfigProvider, metrics: MetricsSink | None = None):
        self.config = config
        self.metrics = metrics or InMemoryMetricsSink()

        def _engine(datatype: str, ttl: float) -> Engine:
            return Engine(datatype=datatype, vendors=build_vendors(datatype), config=config,
                          metrics=self.metrics, cache=TTLCache(default_ttl_sec=ttl), default_ttl=ttl)

        self._quote_engine = _engine("quote", 5.0)
        self._kline_engine = _engine("kline", 0.0)
        self._events_engine = _engine("events", 3600.0)
        # flash_news is market level (symbols always empty) but still uses the Engine for
        # failover / cache / health; publisher feeds refresh every few minutes.
        self._flash_news_engine = _engine("flash_news", 120.0)
        self._fundamentals_engine = _engine("fundamentals", 21600.0)
        self._dividend_engine = _engine("dividend", 86400.0)
        self._holders_engine = _engine("holders", 21600.0)
        self._filings_engine = _engine("filings", 3600.0)
        # discovery: market level, not symbol based -> no Engine / DataSource row.
        self._discovery = DiscoveryVendor()
        # news aggregates across every enabled vendor (merge + dedupe), which is not the
        # Engine's failover model; it borrows build_vendors for the instances only.
        self._news_vendors = build_vendors("news")
        self._news_cache = TTLCache(default_ttl_sec=60.0)
        self._macro_cache = TTLCache(default_ttl_sec=300.0)

    # ------------------------------------------------------------------ prices

    def klines(self, symbol: str, *, market: str, days: int = 120, min_count: int = 1) -> list:
        """Daily bars by priority (a source with fewer than ``min_count`` bars is skipped; if all
        are short the longest wins). Not cached in the package (the host caches)."""
        req = Request(symbols=(symbol,), market=market, timeframe="day", limit=days,
                      extra=(("days", days),))
        resp = self._kline_engine.fetch(req, min_count=min_count, cache_ttl_sec=0)
        return resp.data or []

    def quotes(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Quote]:
        """Batch quotes. Symbols may span markets: without ``market`` they are detected and grouped."""
        out: list[Quote] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._quote_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        """Raw Tencent index symbols (sh000001 / hkHSI / usDJI ...), bypassing ``Symbol.parse``."""
        from marketdata.vendors.tencent import fetch_raw
        return fetch_raw(list(tencent_symbols)) if tencent_symbols else []

    def yahoo_index_quotes(self, yahoo_symbols: list[str], *, proxy: str | None = None) -> list[dict]:
        """Light quotes for raw Yahoo symbols (^GSPC / ^GSPTSE / CAD=X ...): ``yf_adapter.fast_quote``
        first, raw chart v8 meta as the fallback. Symbols without data are skipped (fail-soft).
        Rows: symbol/name/current_price/prev_close/change_amount/change_pct/currency/volume/turnover."""
        from marketdata.vendors.kline import fetch_yahoo_quote_raw
        if proxy:
            yf_adapter.configure(proxy)
        out: list[dict] = []
        for ysym in yahoo_symbols or []:
            ysym = str(ysym)
            q = self._adapter_quote(ysym)
            if q is None:
                try:
                    q = fetch_yahoo_quote_raw(ysym, proxy=proxy)
                except Exception as e:  # pragma: no cover - defensive
                    record_error(f"yahoo index {ysym}: {type(e).__name__}: {e}")
                    q = None
            if q:
                out.append(q)
        return out

    @staticmethod
    def _adapter_quote(ysym: str) -> dict | None:
        try:
            fq = yf_adapter.fast_quote(ysym)
        except VendorError as e:
            logger.debug(f"yfinance quote {ysym} failed: {e}")
            return None
        if not fq or not fq.get("last"):
            return None
        last = float(fq["last"])
        prev = fq.get("prev_close")
        chg = round(last - prev, 4) if prev else 0.0
        pct = round(chg / prev * 100.0, 2) if prev else 0.0
        return {
            "symbol": ysym,
            "name": INDEX_NAMES.get(ysym, ysym),
            "current_price": last,
            "prev_close": prev,
            "change_amount": chg,
            "change_pct": pct,
            "currency": "",
            "volume": fq.get("volume") or 0.0,
            "turnover": 0.0,
        }

    def index_klines(self, code: str, *, market: str, days: int = 120) -> list:
        """Index bars: US/CA via Yahoo (adapter first, raw chart v8 fallback); CN/HK via Eastmoney
        secid then raw Tencent (legacy). Unknown code -> ``[]``."""
        c = str(code).strip()
        ysym = INDEX_YAHOO.get(c) or INDEX_YAHOO.get(c.upper())
        if ysym:
            try:
                bars = yf_adapter.history(ysym, days=days)
            except VendorError as e:
                logger.debug(f"yfinance index {ysym} failed: {e}")
                bars = []
            if bars:
                return bars
            from marketdata.vendors.kline import fetch_yahoo_kline_raw
            bars = fetch_yahoo_kline_raw(ysym, days)
            if bars:
                return bars
        secid = INDEX_SECID.get(c) or INDEX_SECID.get(c.upper())
        if secid:
            from marketdata.vendors.kline import fetch_eastmoney_kline
            bars = fetch_eastmoney_kline(secid, days)
            if bars:
                return bars
        tsym = INDEX_TENCENT.get(c) or INDEX_TENCENT.get(c.upper())
        if tsym:
            from marketdata.vendors.kline import fetch_tencent_kline_raw
            return fetch_tencent_kline_raw(tsym, days)
        return []

    def macro(self, symbols: tuple[str, ...] | list[str] = MACRO_SYMBOLS) -> list[dict]:
        """Macro strip (VIX, 10Y, USD/CAD, WTI, gold, indices): one batched ``download_last`` with a
        per-symbol ``fast_quote`` retry; 5 min cache; missing symbols omitted. Rows have the same
        shape as ``yahoo_index_quotes``."""
        syms = [str(s) for s in symbols if str(s).strip()]
        if not syms:
            return []
        key = "macro|" + ",".join(syms)
        cached = self._macro_cache.get(key)
        if cached is not None:
            return list(cached)
        try:
            batch = yf_adapter.download_last(syms, period="5d")
        except VendorError as e:
            logger.debug(f"macro download failed: {e}")
            batch = {}
        out: list[dict] = []
        for s in syms:
            row = batch.get(s)
            if row and row.get("last"):
                last = float(row["last"])
                prev = row.get("prev_close")
                chg = round(last - prev, 4) if prev else 0.0
                pct = round(chg / prev * 100.0, 2) if prev else 0.0
                out.append({"symbol": s, "name": INDEX_NAMES.get(s, s), "current_price": last,
                            "prev_close": prev, "change_amount": chg, "change_pct": pct,
                            "currency": "", "volume": 0.0, "turnover": 0.0})
                continue
            q = self._adapter_quote(s)
            if q:
                out.append(q)
        if out:
            self._macro_cache.set(key, out)
        return list(out)

    # ------------------------------------------------------------------ information

    def events(self, symbols: list[str], *, market: str | None = None, since_days: int = 7,
               now: datetime | None = None, ahead_days: int = 30) -> list[EventItem]:
        """Corporate events (earnings past and future, ex-dividend, splits). Symbols are grouped by
        market. Vendors never filter by time; when ``now`` is given the window
        ``now - since_days <= publish_time <= now + ahead_days`` is applied here."""
        out: list[EventItem] = []
        dates: tuple[str, ...] = ()
        if now is not None:
            dates = tuple((now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14))
        for mkt, syms in _group_by_market(symbols, market).items():
            extra: tuple = (("since_days", since_days), ("ahead_days", ahead_days))
            if dates:
                extra = extra + (("dates", dates),)
            req = Request(symbols=tuple(s.code for s in syms), market=mkt,
                          since_hours=since_days * 24, extra=extra)
            resp = self._events_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        if now is not None:
            lo = now - timedelta(days=max(int(since_days), 0))
            hi = now + timedelta(days=max(int(ahead_days), 0))
            out = [e for e in out if lo <= _aware(e.publish_time, now) <= hi]
        out.sort(key=lambda e: (e.publish_time, e.importance), reverse=True)
        return out

    def flash_news(self, *, market: str = "US", limit: int = 50, keyword: str | None = None) -> list[FlashNews]:
        """Market headlines (publisher RSS). Market level; ``keyword`` filters title/content locally."""
        req = Request(symbols=(), market=market, limit=limit)
        resp = self._flash_news_engine.fetch(req)
        data = resp.data or []
        if keyword:
            kw = keyword.lower()
            data = [x for x in data if kw in (x.title or "").lower() or kw in (x.content or "").lower()]
        return data[:limit] if limit else data

    def news(
        self,
        symbols: list[str],
        *,
        market: str | None = None,
        since_hours: int = 2,
        names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[NewsArticle]:
        """Symbol news, aggregated across every enabled vendor (merge + dedupe), not failover.

        Symbols are grouped by detected market and ``sources_for("news", market)`` is walked per
        group in priority order. Dedupe keys: ``id:`` (external_id), ``url:`` (canonical URL),
        ``title:`` (normalised title); the first hit in priority order wins. Sorted newest first.
        The ``since_hours`` window is applied only when ``now`` is given. 60 s cache per group."""
        out: list[NewsArticle] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            out.extend(self._news_for_market(mkt, syms, since_hours=since_hours, names=names))

        seen: set[str] = set()
        deduped: list[NewsArticle] = []
        for a in out:
            keys = [f"id:{a.external_id}" if a.external_id else ""]
            cu = _canon_url(a.url) if a.url else ""
            if cu:
                keys.append(f"url:{cu}")
            nt = _norm_title(a.title) if a.title else ""
            if nt:
                keys.append(f"title:{nt}")
            keys = [k for k in keys if k]
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            deduped.append(a)

        deduped.sort(key=lambda a: a.publish_time, reverse=True)
        if now is not None:
            cutoff = now - timedelta(hours=since_hours)
            deduped = [a for a in deduped if _aware(a.publish_time, now) >= cutoff]
        return deduped

    def _news_for_market(self, market: str, syms: list[Symbol], *, since_hours: int,
                         names: dict[str, str] | None) -> list[NewsArticle]:
        codes = tuple(s.code for s in syms)
        name_sig = hashlib.sha1(repr(sorted((names or {}).items())).encode()).hexdigest()[:8]
        key = f"news|{market}|{since_hours}|{','.join(codes)}|{name_sig}"
        cached = self._news_cache.get(key)
        if cached is not None:
            return list(cached)

        srcs = sorted(self.config.sources_for("news", market), key=lambda s: s.priority)
        articles: list[NewsArticle] = []
        for src in srcs:
            if not src.enabled:
                continue
            vendor = self._news_vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue
            call_config = {**(src.config or {}), "symbol_names": names or {}, "since_hours": since_hours}
            t0 = time.monotonic()
            try:
                got = vendor.fetch(syms, call_config) or []
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error=str(e))
                record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                continue
            latency = int((time.monotonic() - t0) * 1000)
            self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                ok=bool(got), count=len(got), latency_ms=latency,
                                error="" if got else "empty")
            articles.extend(got)
        if articles:
            self._news_cache.set(key, articles)
        return list(articles)

    def news_by_keyword(self, keyword: str, *, market: str = "US") -> list[NewsArticle]:
        """Free-text news search (theme / industry keyword) via Google News RSS. Single source."""
        return GoogleNewsRssVendor.fetch_by_keyword(keyword, market=market)

    def fundamentals(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Fundamentals]:
        """Batch fundamentals per symbol, grouped by market (same pattern as ``quotes``)."""
        out: list[Fundamentals] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._fundamentals_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dividend(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[DividendItem]:
        """Dividend history per symbol (newest first), grouped by market."""
        out: list[DividendItem] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._dividend_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def holders(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[HolderItem]:
        """Ownership rows (breakdown / institutions / insider transactions) per symbol, grouped by market."""
        out: list[HolderItem] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._holders_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def filings(self, symbols: list[str | Symbol], *, market: str | None = None, limit: int = 50) -> list[FilingItem]:
        """Regulatory filings (SEC EDGAR) or Canadian press releases per symbol, newest first.
        ``limit`` reaches the vendor as ``config["days"]`` (Engine convention)."""
        out: list[FilingItem] = []
        for mkt, syms in _group_by_market(symbols, market).items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt, limit=limit)
            resp = self._filings_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        out.sort(key=lambda f: f.filed_at, reverse=True)
        return out

    # ------------------------------------------------------------------ misc

    def health(self) -> dict[str, dict]:
        """In-memory health snapshot per vendor (success rate / p50 latency / last error)."""
        return self.metrics.snapshot()

    def hot_stocks(self, **kw) -> list[HotStock]:
        """Most active / gainers / losers (market level, bypasses the Engine)."""
        return self._discovery.hot_stocks(**kw)

    def hot_boards(self, **kw) -> list[HotBoard]:
        """Sector boards (US sector ETFs; market level, bypasses the Engine)."""
        return self._discovery.hot_boards(**kw)

    def board_stocks(self, **kw) -> list[HotStock]:
        """Constituents of a sector board (market level, bypasses the Engine)."""
        return self._discovery.board_stocks(**kw)


def _aware(dt: datetime, ref: datetime) -> datetime:
    """Make ``dt`` comparable with ``ref`` (naive vs aware mismatch never raises)."""
    if (dt.tzinfo is None) == (ref.tzinfo is None):
        return dt
    if ref.tzinfo is not None:
        return dt.replace(tzinfo=ref.tzinfo)
    return dt.replace(tzinfo=None)
