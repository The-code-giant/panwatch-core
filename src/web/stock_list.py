"""Stock search over the tradable universe (US + Canada) plus Yahoo top-up.

Search paths (never returns rows for markets that are not enabled):
- US / CA: ``src.core.universe`` (Nasdaq Trader + TMX directories, cached locally) first;
  when the universe answer is short and the query has at least two characters, the
  yfinance ``Search`` endpoint tops the list up (rows are still filtered through the
  universe when it is loaded, so OTC / CSE / NEO / foreign listings never surface).
- CRYPTO / GOLD: static list + ``XXX-USD`` pass-through.

Rows are ``{symbol, name, market}`` (universe rows also carry ``exchange`` / ``is_etf``).
"""

from __future__ import annotations

import logging
import re

from src.core import universe
from src.models.market import EQUITY_MARKETS, is_enabled

logger = logging.getLogger(__name__)

# CRYPTO/GOLD have no directory; a static list covers the common names and any
# "XXX-USD" code passes straight through (see _search_crypto_gold).
_CRYPTO_GOLD_STATIC: list[dict] = [
    {"symbol": "BTC-USD", "name": "Bitcoin", "market": "CRYPTO"},
    {"symbol": "ETH-USD", "name": "Ethereum", "market": "CRYPTO"},
    {"symbol": "SOL-USD", "name": "Solana", "market": "CRYPTO"},
    {"symbol": "BNB-USD", "name": "BNB", "market": "CRYPTO"},
    {"symbol": "XRP-USD", "name": "XRP", "market": "CRYPTO"},
    {"symbol": "DOGE-USD", "name": "Dogecoin", "market": "CRYPTO"},
    {"symbol": "ADA-USD", "name": "Cardano", "market": "CRYPTO"},
    {"symbol": "AVAX-USD", "name": "Avalanche", "market": "CRYPTO"},
    {"symbol": "LINK-USD", "name": "Chainlink", "market": "CRYPTO"},
    {"symbol": "DOT-USD", "name": "Polkadot", "market": "CRYPTO"},
    {"symbol": "XAUUSD", "name": "Gold Spot", "market": "GOLD"},
]

_CJK_RE = re.compile(r"[一-鿿]")


def _english_name(symbol: str, name: str) -> str:
    """Never let a CJK name reach the DB / UI: resolve it from the universe directory,
    falling back to the bare symbol. Non-CJK names pass through unchanged."""
    if name and _CJK_RE.search(name):
        sym = (symbol or "").strip()
        return universe.lookup_name(sym) or sym
    return name


# Yahoo Finance ``exchange`` codes for the two enabled equity markets. Only TSX / TSXV
# are Canadian tradable venues here (CSE ``CNQ`` and NEO are excluded by design).
YAHOO_CA_EXCHANGES = {"TOR", "VAN"}
YAHOO_US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BATS"}
YAHOO_QUOTE_TYPES = {"EQUITY", "ETF"}


def yahoo_market_for_exchange(exchange: str) -> str | None:
    """Map a Yahoo ``exchange`` code to a PanWatch market (CA / US), or None if neither."""
    ex = (exchange or "").strip().upper()
    if ex in YAHOO_CA_EXCHANGES:
        return "CA"
    if ex in YAHOO_US_EXCHANGES:
        return "US"
    return None


def map_yahoo_search_quotes(quotes: list[dict], market: str = "", limit: int = 20) -> list[dict]:
    """Pure mapping of yfinance ``Search().quotes`` rows -> [{symbol, name, market}].

    Keeps only EQUITY/ETF rows on US or Canadian exchanges and only enabled markets.
    US symbols are folded to the canonical PanWatch form (``BRK-B`` -> ``BRK.B``) via
    ``marketdata.from_yfinance``. When the universe is loaded, rows that are not in the
    directory for their market are dropped (``universe.is_tradable`` fails open when a
    market has no rows). ``market`` restricts to a single market when given.
    """
    from marketdata import from_yfinance

    want = (market or "").strip().upper()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for q in quotes or []:
        if not isinstance(q, dict):
            continue
        if (q.get("quoteType") or "").strip().upper() not in YAHOO_QUOTE_TYPES:
            continue
        mkt = yahoo_market_for_exchange(q.get("exchange") or "")
        if mkt is None or not is_enabled(mkt):
            continue
        if want and mkt != want:
            continue
        raw = (q.get("symbol") or "").strip().upper()
        if not raw:
            continue
        try:
            symbol = from_yfinance(raw, mkt) if mkt == "US" else raw
        except Exception:
            symbol = raw
        if not symbol:
            continue
        if not universe.is_tradable(mkt, symbol):
            continue
        name = (q.get("shortname") or q.get("longname") or symbol).strip()
        key = (mkt, symbol)
        if key in seen:
            continue
        seen.add(key)
        row = {"symbol": symbol, "name": _english_name(symbol, name), "market": mkt}
        listing = universe.lookup(mkt, symbol)
        if listing:
            row["exchange"] = listing.get("exchange") or ""
            row["is_etf"] = bool(listing.get("is_etf"))
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _yfinance_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """yfinance ``Search`` top-up (US + Canada). Fail-soft: any error -> ``[]``."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        from marketdata.vendors import yf_adapter

        quotes = yf_adapter.search(q, max_results=max(limit * 2, 20))
    except Exception as e:
        logger.warning("yfinance search failed for %r: %s", q, e)
        return []
    return map_yahoo_search_quotes(quotes or [], market, limit)


def _search_crypto_gold(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """CRYPTO/GOLD static list search + pass-through: any "XXX-USD" code can be added."""
    q = query.strip().upper()
    if not q:
        return []
    if market and market not in ("CRYPTO", "GOLD"):
        return []

    results = []
    for s in _CRYPTO_GOLD_STATIC:
        if market and s["market"] != market:
            continue
        if q in s["symbol"] or q in s["name"].upper():
            results.append(s)

    # Pass-through: a "SOL-USD" style code is allowed even when not in the static list.
    if not market or market == "CRYPTO":
        if re.match(r"^[A-Z0-9]{2,10}-USD$", q) and not any(r["symbol"] == q for r in results):
            results.insert(0, {"symbol": q, "name": q.split("-")[0], "market": "CRYPTO"})

    return results[:limit]


def _merge(base: list[dict], extra: list[dict], limit: int) -> list[dict]:
    seen = {(r.get("market"), r.get("symbol")) for r in base}
    for r in extra:
        if len(base) >= limit:
            break
        key = (r.get("market"), r.get("symbol"))
        if key in seen:
            continue
        base.append(r)
        seen.add(key)
    return base


def search_stocks(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """Search stocks: universe directory first, yfinance top-up when short."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(int(limit or 0), 1)

    # CRYPTO/GOLD never touch the directories.
    if market in ("CRYPTO", "GOLD"):
        return _search_crypto_gold(q, market, limit)

    # A market that is not enabled never returns rows.
    if market and market not in EQUITY_MARKETS:
        return []

    results = list(universe.search(q, market, limit))
    if len(results) < limit and len(q) >= 2:
        results = _merge(results, _yfinance_search(q, market, limit), limit)
    if market == "":
        results = _merge(results, _search_crypto_gold(q, "", limit), limit)
    return results[:limit]


def refresh_stock_list() -> dict:
    """Force a directory refresh of the tradable universe; returns ``universe.stats()``
    (plus ``errors`` / ``skipped`` from the refresh)."""
    return universe.refresh_blocking(force=True)
