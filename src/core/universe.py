"""Tradable universe: US (NYSE, NYSE American, Arca, Nasdaq, Cboe BZX) and Canada (TSX, TSXV).

Rows come from ``marketdata.universe.fetch_us_listings`` / ``fetch_ca_listings`` (Nasdaq
Trader symbol directory and the TMX company directory) and are cached as JSON under
``DATA_DIR/universe_cache.json`` for 24 h. A market whose fetch fails keeps its previous
rows (per-market merge). ``is_tradable`` FAILS OPEN when a market has no rows so a
directory outage never hides stocks from search, discovery or paper trading.

Row shape: ``{symbol, yahoo_symbol, exchange_symbol, name, market, exchange, is_etf}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_TTL_S = 86400
MARKETS: tuple[str, ...] = ("US", "CA")

# Primary exchanges rank before the junior/venture boards in search results.
_EXCHANGE_RANK: dict[str, int] = {
    "NYSE": 0, "NASDAQ": 0, "TSX": 0,
    "NYSE American": 1, "NYSE Arca": 1, "Cboe BZX": 1, "IEX": 1, "TSXV": 1,
}

_lock = threading.RLock()
_rows: list[dict] = []
_by_key: dict[tuple[str, str], dict] = {}
_loaded_ts: float = 0.0
_refreshed: dict[str, float] = {}
_refresh_thread: threading.Thread | None = None


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


def cache_file() -> str:
    return os.path.join(_data_dir(), "universe_cache.json")


def _legacy_stock_list_cache() -> str:
    return os.path.join(_data_dir(), "stock_list_cache.json")


# --------------------------------------------------------------------------- state


def _install(rows: list[dict], refreshed: dict[str, float], ts: float) -> None:
    global _rows, _by_key, _loaded_ts, _refreshed
    idx: dict[tuple[str, str], dict] = {}
    for r in rows:
        mkt = str(r.get("market") or "").upper()
        sym = str(r.get("symbol") or "").upper()
        if not mkt or not sym:
            continue
        idx[(mkt, sym)] = r
        ex = str(r.get("exchange_symbol") or "").upper()
        if ex and (mkt, ex) not in idx:
            idx[(mkt, ex)] = r
    with _lock:
        _rows = list(rows)
        _by_key = idx
        _loaded_ts = ts
        _refreshed = dict(refreshed)


def _listing_to_row(listing: Any) -> dict:
    return {
        "symbol": str(getattr(listing, "symbol", "") or "").upper(),
        "yahoo_symbol": str(getattr(listing, "yahoo_symbol", "") or "").upper(),
        "exchange_symbol": str(getattr(listing, "exchange_symbol", "") or "").upper(),
        "name": str(getattr(listing, "name", "") or ""),
        "market": str(getattr(listing, "market", "") or "").upper(),
        "exchange": str(getattr(listing, "exchange", "") or ""),
        "is_etf": bool(getattr(listing, "is_etf", False)),
    }


def _read_cache() -> dict | None:
    path = cache_file()
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # corrupt file: ignore, a refresh rewrites it
        logger.warning("universe cache unreadable (%s): %s", path, e)
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    return payload


def _write_cache(rows: list[dict], refreshed: dict[str, float], ts: float) -> None:
    path = cache_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": CACHE_VERSION, "ts": ts, "refreshed": refreshed, "rows": rows}, f)
    os.replace(tmp, path)
    legacy = _legacy_stock_list_cache()
    try:
        if os.path.exists(legacy):
            os.remove(legacy)
            logger.info("removed stale %s", legacy)
    except Exception:
        pass


def load() -> list[dict]:
    """Load rows from the JSON cache into memory (no network). Returns the rows (may be empty)."""
    payload = _read_cache()
    if payload is None:
        return list(_rows)
    refreshed = {str(k).upper(): float(v) for k, v in (payload.get("refreshed") or {}).items()
                 if isinstance(v, (int, float))}
    _install([r for r in payload["rows"] if isinstance(r, dict)], refreshed, float(payload.get("ts") or 0.0))
    return list(_rows)


def is_loaded() -> bool:
    return bool(_rows)


def is_stale(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if not _rows:
        return True
    return (now - _loaded_ts) > CACHE_TTL_S


# --------------------------------------------------------------------------- refresh


def _fetch_market(market: str) -> list[dict]:
    from marketdata import universe as pkg_universe

    fn = getattr(pkg_universe, "fetch_us_listings" if market == "US" else "fetch_ca_listings", None)
    if fn is None:
        raise RuntimeError(f"marketdata.universe has no fetcher for {market}")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    listings = fn(proxy=proxy)
    rows = [_listing_to_row(x) for x in listings or []]
    rows = [r for r in rows if r["symbol"] and r["market"] == market]
    if not rows:
        raise RuntimeError(f"{market} directory returned no rows")
    return rows


def refresh_blocking(force: bool = False) -> dict:
    """Refresh every market from the directories, merging per market (a failed market keeps its
    last good rows). Returns ``stats()`` plus ``errors``. Skipped when fresh unless ``force``."""
    now = time.time()
    if not force and _rows and not is_stale(now):
        return {**stats(), "skipped": True, "errors": {}}
    if not _rows:
        load()
    current = {m: [r for r in _rows if r.get("market") == m] for m in MARKETS}
    refreshed = dict(_refreshed)
    errors: dict[str, str] = {}
    for m in MARKETS:
        try:
            rows = _fetch_market(m)
            current[m] = rows
            refreshed[m] = now
            logger.info("universe %s: %d listings", m, len(rows))
        except Exception as e:
            errors[m] = str(e)
            logger.warning("universe %s refresh failed, keeping %d cached rows: %s",
                           m, len(current[m]), e)
    merged: list[dict] = []
    for m in MARKETS:
        merged.extend(current[m])
    if merged:
        # Keep any rows for markets outside MARKETS untouched (none today, but harmless).
        extra = [r for r in _rows if r.get("market") not in MARKETS]
        merged.extend(extra)
        ts = now if len(errors) < len(MARKETS) else _loaded_ts
        _install(merged, refreshed, ts or now)
        try:
            _write_cache(merged, refreshed, ts or now)
        except Exception as e:
            logger.warning("universe cache write failed: %s", e)
    return {**stats(), "skipped": False, "errors": errors}


async def refresh() -> dict:
    return await asyncio.to_thread(refresh_blocking, True)


def warm_up() -> None:
    """Startup hook: load the cache; when empty or stale, refresh in a background thread."""
    global _refresh_thread
    try:
        load()
    except Exception as e:
        logger.warning("universe load failed: %s", e)
    if not is_stale():
        logger.info("universe loaded from cache: %s", stats())
        return
    with _lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_thread = threading.Thread(
            target=lambda: refresh_blocking(force=True), name="universe-refresh", daemon=True
        )
        _refresh_thread.start()


# --------------------------------------------------------------------------- queries


def _norm_symbol(market: str, symbol: str) -> tuple[str, str]:
    mkt = (market or "").strip().upper()
    sym = (symbol or "").strip().upper()
    if mkt == "US":
        sym = sym.replace("_", ".").replace("-", ".")
    elif mkt == "CA":
        base, _, suffix = sym.rpartition(".")
        if suffix in ("TO", "V") and base:
            sym = base.replace("_", "-").replace(".", "-") + "." + suffix
    return mkt, sym


def _market_has_rows(market: str) -> bool:
    return any(r.get("market") == market for r in _rows)


def lookup(market: str, symbol: str) -> dict | None:
    mkt, sym = _norm_symbol(market, symbol)
    return _by_key.get((mkt, sym))


def is_tradable(market: str, symbol: str) -> bool:
    """True when the symbol is in the directory for its market. FAIL-OPEN: a market with no
    cached rows (directory down, first boot) answers True. Non-equity markets are always True."""
    mkt = (market or "").strip().upper()
    if mkt not in MARKETS:
        return True
    if not _market_has_rows(mkt):
        return True
    return lookup(mkt, symbol) is not None


def lookup_name(symbol: str, market: str | None = None) -> str | None:
    if market:
        row = lookup(market, symbol)
        return row.get("name") if row else None
    for m in MARKETS:
        row = lookup(m, symbol)
        if row:
            return row.get("name")
    return None


def yahoo_symbol(market: str, symbol: str) -> str:
    row = lookup(market, symbol)
    if row and row.get("yahoo_symbol"):
        return row["yahoo_symbol"]
    from marketdata import Symbol

    try:
        return Symbol.parse((symbol or "").strip().upper(), (market or "").upper() or None).to_yfinance()
    except Exception:
        return (symbol or "").strip().upper()


_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _rank(row: dict, q: str) -> int | None:
    """0 exact symbol/exchange_symbol, 1 symbol prefix, 2 name word prefix, 3 name substring,
    4 symbol substring; None when no match."""
    sym = row.get("symbol") or ""
    ex = row.get("exchange_symbol") or ""
    ysym = row.get("yahoo_symbol") or ""
    name_l = (row.get("name") or "").lower()
    if q in (sym, ex, ysym):
        return 0
    base = sym.rpartition(".")[0] if row.get("market") == "CA" and "." in sym else sym
    if base == q or ex.replace("-", ".") == q.replace("-", "."):
        return 0
    if sym.startswith(q) or ex.startswith(q) or base.startswith(q):
        return 1
    ql = q.lower()
    if any(w.startswith(ql) for w in _WORD_RE.findall(name_l)):
        return 2
    if ql in name_l:
        return 3
    if q in sym or q in ex:
        return 4
    return None


def search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """Rank rows for ``query``; ties: primary exchanges before junior boards, stocks before ETFs."""
    q = (query or "").strip().upper()
    if not q:
        return []
    mkt = (market or "").strip().upper()
    scored: list[tuple[int, int, int, str, dict]] = []
    for r in _rows:
        if mkt and r.get("market") != mkt:
            continue
        rank = _rank(r, q)
        if rank is None:
            continue
        scored.append((rank, _EXCHANGE_RANK.get(r.get("exchange") or "", 2),
                       1 if r.get("is_etf") else 0, r.get("symbol") or "", r))
    scored.sort(key=lambda t: (t[0], t[1], t[2], len(t[3]), t[3]))
    out: list[dict] = []
    for _, _, _, _, r in scored[: max(int(limit or 0), 0)]:
        out.append({"symbol": r.get("symbol"), "name": r.get("name") or r.get("symbol"),
                    "market": r.get("market"), "exchange": r.get("exchange") or "",
                    "is_etf": bool(r.get("is_etf"))})
    return out


def stats() -> dict:
    by_market: dict[str, int] = {}
    by_exchange: dict[str, int] = {}
    etfs = 0
    for r in _rows:
        by_market[r.get("market") or "?"] = by_market.get(r.get("market") or "?", 0) + 1
        ex = r.get("exchange") or "?"
        by_exchange[ex] = by_exchange.get(ex, 0) + 1
        if r.get("is_etf"):
            etfs += 1
    return {
        "count": len(_rows),
        "by_market": by_market,
        "by_exchange": by_exchange,
        "etfs": etfs,
        "loaded_at": _loaded_ts or None,
        "refreshed": dict(_refreshed),
        "stale": is_stale(),
    }


def reset() -> None:
    """Test helper: drop the in-memory state (does not touch the cache file)."""
    _install([], {}, 0.0)
