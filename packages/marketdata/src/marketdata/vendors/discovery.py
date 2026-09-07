"""Discovery (market level, not symbol based): most actives / gainers / losers and sector boards
for the US and Canada.

US hot stocks come from Yahoo's predefined screeners (``most_actives`` / ``day_gainers`` /
``day_losers``) filtered to the listed exchanges, with the Nasdaq stock screener as fallback.
Canadian hot stocks come from a Yahoo ``EquityQuery`` (region ``ca``, exchange TOR/VAN, volume,
price and market-cap floors) with TMX's ``getMarketMovers`` GraphQL as fallback. Boards are the
eleven US sector ETFs (one batched ``download_last``); their constituents are a sector screen.
Canada has no board source (``hot_boards(market="CA") -> []``; the API keeps its synthetic buckets).

OTC / CSE / NEO never appear: the Yahoo rows are filtered by exchange code, the Nasdaq screener
lists only NASDAQ/NYSE/AMEX, and the TMX feed is per exchange (TSX / TSXV).

Not a ``Vendor`` subclass (no Engine failover, no DataSource row). 5 minute cache per query.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from marketdata.cache import TTLCache
from marketdata.errors import VendorError
from marketdata.http import market_get, market_post
from marketdata.symbol import Market, from_yfinance
from marketdata.types import HotBoard, HotStock
from marketdata.vendors import yf_adapter

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_MODES = {"turnover", "gainers", "losers"}
_PREDEFINED = {"turnover": "most_actives", "gainers": "day_gainers", "losers": "day_losers"}

US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS"}
CA_EXCHANGES = {"TOR", "VAN"}
_QUOTE_TYPES = {"EQUITY", "ETF"}

US_SECTORS: list[tuple[str, str, str]] = [
    ("technology", "Technology", "XLK"),
    ("financial-services", "Financial Services", "XLF"),
    ("healthcare", "Healthcare", "XLV"),
    ("consumer-cyclical", "Consumer Cyclical", "XLY"),
    ("industrials", "Industrials", "XLI"),
    ("communication-services", "Communication Services", "XLC"),
    ("consumer-defensive", "Consumer Defensive", "XLP"),
    ("energy", "Energy", "XLE"),
    ("basic-materials", "Basic Materials", "XLB"),
    ("real-estate", "Real Estate", "XLRE"),
    ("utilities", "Utilities", "XLU"),
]
_SECTOR_PREFIX = "US_SECTOR_"
_SECTOR_BY_SLUG = {slug: (name, etf) for slug, name, etf in US_SECTORS}

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
TMX_GRAPHQL_URL = "https://app-money.tmx.com/graphql"

# Pinned against the live endpoint (introspection is disabled): ``locale`` is not an argument
# of ``getMarketMovers`` and both variables are non-null ``String!``.
GETMARKETMOVERS_QUERY = (
    "query getMarketMovers($statExchange: String!, $sortOrder: String!) {"
    " getMarketMovers(statExchange: $statExchange, sortOrder: $sortOrder) {"
    " symbol name price percentChange volume } }"
)


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace("$", "").replace("%", "").replace(",", "")
        if not v or v.upper() in {"N/A", "NA", "--"}:
            return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return None if fv != fv else fv


def _norm_mode(mode: str | None) -> str:
    m = str(mode or "turnover").strip().lower()
    if m == "hot":
        return "gainers"
    return m if m in _MODES else "turnover"


def _screen_size(limit: int) -> int:
    return min(250, max(int(limit or 0), 100))


def _sort_key(mode: str) -> tuple[Callable[[HotStock], float], bool]:
    if mode == "gainers":
        return (lambda s: s.change_pct if s.change_pct is not None else float("-inf")), True
    if mode == "losers":
        return (lambda s: s.change_pct if s.change_pct is not None else float("inf")), False
    return (lambda s: s.turnover if s.turnover is not None else -1.0), True


def _sorted(stocks: list[HotStock], mode: str, limit: int) -> list[HotStock]:
    key, reverse = _sort_key(mode)
    out = sorted(stocks, key=key, reverse=reverse)
    return out[:limit] if limit and limit > 0 else out


def _yahoo_row(row: dict, market: str) -> HotStock | None:
    if not isinstance(row, dict):
        return None
    ysym = str(row.get("symbol") or "").strip()
    if not ysym:
        return None
    symbol = from_yfinance(ysym, Market.US) if market == "US" else ysym.upper()
    price = _f(row.get("regularMarketPrice"))
    volume = _f(row.get("regularMarketVolume"))
    turnover = price * volume if (price is not None and volume is not None) else None
    return HotStock(
        symbol=symbol, market=market,
        name=str(row.get("shortName") or row.get("longName") or "").strip(),
        price=price, change_pct=_f(row.get("regularMarketChangePercent")),
        turnover=turnover, volume=volume,
    )


def _yahoo_rows(rows: list[dict], market: str, exchanges: set[str]) -> list[HotStock]:
    out: list[HotStock] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("quoteType") or "").upper() not in _QUOTE_TYPES:
            continue
        if str(row.get("exchange") or "").upper() not in exchanges:
            continue
        hs = _yahoo_row(row, market)
        if hs is not None:
            out.append(hs)
    return out


class DiscoveryVendor:
    """Hot stocks / sector boards / board constituents for US and CA. 5 minute cache."""

    _cache = TTLCache(default_ttl_sec=300.0, max_size=256)

    # ------------------------------------------------------------------ hot stocks

    def hot_stocks(self, *, market: str = "US", mode: str = "turnover", limit: int = 20,
                   proxy: str | None = None) -> list[HotStock]:
        market = (market or "US").upper()
        mode = _norm_mode(mode)
        if market not in ("US", "CA"):
            return []
        size = _screen_size(limit)
        key = f"hot|{market}|{mode}|{size}"
        rows = self._cache.get(key)
        if rows is None:
            yf_adapter.configure(proxy)
            rows = self._us_hot(mode, size, proxy) if market == "US" else self._ca_hot(mode, size, proxy)
            if rows:
                self._cache.set(key, rows)
        return _sorted(rows, mode, limit)

    def _us_hot(self, mode: str, size: int, proxy: str | None) -> list[HotStock]:
        try:
            raw = yf_adapter.screen(_PREDEFINED[mode], size=size)
            rows = _yahoo_rows(raw, "US", US_EXCHANGES)
        except VendorError as e:
            logger.info(f"[discovery] Yahoo {_PREDEFINED[mode]} failed: {e}")
            rows = []
        if rows:
            return rows
        return self._nasdaq_screener(mode, size, proxy)

    @staticmethod
    def _nasdaq_screener(mode: str, size: int, proxy: str | None = None) -> list[HotStock]:
        payload = market_get(
            NASDAQ_SCREENER_URL, host_key="api.nasdaq.com",
            params={"tableonly": "true", "limit": str(size), "download": "true"},
            headers={"User-Agent": _UA, "Accept": "application/json", "Origin": "https://www.nasdaq.com"},
            min_interval_s=0.5, timeout=15, retries=1, parse="json", proxy=proxy,
            log_label="Nasdaq stock screener",
        )
        rows = ((payload or {}).get("data") or {}).get("rows") or [] if isinstance(payload, dict) else []
        out: list[HotStock] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol or any(ch in symbol for ch in "$+=^#"):
                continue
            price = _f(row.get("lastsale"))
            volume = _f(row.get("volume"))
            turnover = price * volume if (price is not None and volume is not None) else None
            out.append(HotStock(
                symbol=symbol.replace("/", "."), market="US", name=str(row.get("name") or "").strip(),
                price=price, change_pct=_f(row.get("pctchange")), turnover=turnover, volume=volume,
            ))
        return _sorted(out, mode, size)

    def _ca_hot(self, mode: str, size: int, proxy: str | None) -> list[HotStock]:
        operands: list = [
            yf_adapter.equity_query("eq", ["region", "ca"]),
            yf_adapter.equity_query("is-in", ["exchange", "TOR", "VAN"]),
            yf_adapter.equity_query("gt", ["dayvolume", 50_000]),
            yf_adapter.equity_query("gte", ["intradayprice", 0.5]),
        ]
        if mode in ("gainers", "losers"):
            operands.append(yf_adapter.equity_query("gte", ["intradaymarketcap", 50_000_000]))
        query = yf_adapter.equity_query("and", operands)
        if mode == "turnover":
            sort_field, sort_asc = "dayvolume", False
        elif mode == "gainers":
            sort_field, sort_asc = "percentchange", False
        else:
            sort_field, sort_asc = "percentchange", True
        try:
            raw = yf_adapter.screen(query, sort_field=sort_field, sort_asc=sort_asc, size=size)
            rows = _yahoo_rows(raw, "CA", CA_EXCHANGES)
        except VendorError as e:
            logger.info(f"[discovery] Yahoo CA screen ({mode}) failed: {e}")
            rows = []
        if rows:
            return rows
        order = "asc" if mode == "losers" else "desc"
        return self._tmx_market_movers("TSX", order, proxy) + self._tmx_market_movers("TSXV", order, proxy)

    @staticmethod
    def _tmx_market_movers(exchange: str, order: str, proxy: str | None = None) -> list[HotStock]:
        exchange = str(exchange or "TSX").upper()
        suffix = "V" if exchange == "TSXV" else "TO"
        payload = market_post(
            TMX_GRAPHQL_URL, host_key="app-money.tmx.com",
            json={"operationName": "getMarketMovers",
                  "variables": {"statExchange": exchange, "sortOrder": order},
                  "query": GETMARKETMOVERS_QUERY},
            headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json",
                     "locale": "en"},
            timeout=15, retries=1, parse="json", proxy=proxy, log_label="TMX market movers",
        )
        rows = ((payload or {}).get("data") or {}).get("getMarketMovers") or [] if isinstance(payload, dict) else []
        out: list[HotStock] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            exch_sym = str(row.get("symbol") or "").strip().upper()
            if not exch_sym:
                continue
            price = _f(row.get("price"))
            volume = _f(row.get("volume"))
            turnover = price * volume if (price is not None and volume is not None) else None
            out.append(HotStock(
                symbol=f"{exch_sym.replace('.', '-')}.{suffix}", market="CA",
                name=str(row.get("name") or "").strip(), price=price,
                change_pct=_f(row.get("percentChange")), turnover=turnover, volume=volume,
            ))
        return out

    # ------------------------------------------------------------------ boards

    def hot_boards(self, *, market: str = "US", mode: str = "gainers", limit: int = 12,
                   proxy: str | None = None) -> list[HotBoard]:
        market = (market or "US").upper()
        mode = _norm_mode(mode)
        if market != "US":
            return []
        # One key for every mode: the ETF download is mode independent, sorting happens below.
        key = f"boards|{market}"
        boards = self._cache.get(key)
        if boards is None:
            yf_adapter.configure(proxy)
            boards = self._us_sector_boards()
            if boards:
                self._cache.set(key, boards)
        reverse = mode != "losers"
        missing = float("-inf") if reverse else float("inf")
        out = sorted(boards, key=lambda b: b.change_pct if b.change_pct is not None else missing, reverse=reverse)
        return out[:limit] if limit and limit > 0 else out

    @staticmethod
    def _us_sector_boards() -> list[HotBoard]:
        etfs = [etf for _, _, etf in US_SECTORS]
        try:
            prices = yf_adapter.download_last(etfs, period="5d")
        except VendorError as e:
            logger.info(f"[discovery] sector ETF download failed: {e}")
            return []
        out: list[HotBoard] = []
        for slug, name, etf in US_SECTORS:
            px = prices.get(etf) or {}
            last, prev = _f(px.get("last")), _f(px.get("prev_close"))
            if last is None:
                continue
            change_amount = (last - prev) if prev is not None else None
            change_pct = (change_amount / prev * 100.0) if (change_amount is not None and prev) else None
            out.append(HotBoard(code=f"{_SECTOR_PREFIX}{slug}", name=name, change_pct=change_pct,
                                change_amount=change_amount, turnover=None))
        return out

    def board_stocks(self, *, board_code: str, mode: str = "gainers", limit: int = 20,
                     proxy: str | None = None) -> list[HotStock]:
        code = str(board_code or "").strip()
        mode = _norm_mode(mode)
        if not code.upper().startswith(_SECTOR_PREFIX):
            return []
        slug = code[len(_SECTOR_PREFIX):].lower()
        sector = _SECTOR_BY_SLUG.get(slug)
        if sector is None:
            return []
        size = _screen_size(limit)
        key = f"board_stocks|{code.upper()}|{mode}|{size}"
        rows = self._cache.get(key)
        if rows is None:
            yf_adapter.configure(proxy)
            rows = self._sector_screen(sector[0], mode, size)
            if rows:
                self._cache.set(key, rows)
        return _sorted(rows, mode, limit)

    @staticmethod
    def _sector_screen(sector_name: str, mode: str, size: int) -> list[HotStock]:
        query = yf_adapter.equity_query("and", [
            yf_adapter.equity_query("eq", ["region", "us"]),
            yf_adapter.equity_query("eq", ["sector", sector_name]),
            yf_adapter.equity_query("gte", ["intradaymarketcap", 2_000_000_000]),
            yf_adapter.equity_query("gt", ["dayvolume", 100_000]),
        ])
        if mode == "turnover":
            sort_field, sort_asc = "dayvolume", False
        elif mode == "gainers":
            sort_field, sort_asc = "percentchange", False
        else:
            sort_field, sort_asc = "percentchange", True
        try:
            raw = yf_adapter.screen(query, sort_field=sort_field, sort_asc=sort_asc, size=size)
        except VendorError as e:
            logger.info(f"[discovery] sector screen {sector_name} failed: {e}")
            return []
        return _yahoo_rows(raw, "US", US_EXCHANGES)

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()


_SECTOR_CODE_RE = re.compile(r"^US_SECTOR_[a-z\-]+$")


def is_sector_code(code: str) -> bool:
    """True for the ``US_SECTOR_<slug>`` board codes this vendor serves."""
    return bool(_SECTOR_CODE_RE.match(str(code or "")))
