"""Tradable universe: the listed common stocks and ETFs of the US (Nasdaq Trader symbol
directories) and Canada (TMX company directory, TSX + TSXV).

Pure parsing plus ``market_get`` downloads; no caching and no clock here (the host module
``src/core/universe.py`` owns the cache file and the refresh schedule).

Filters (by construction, so OTC, CSE, NEO and CDRs never enter):
- US: ``Test Issue == "N"``; symbols containing ``$ + = ^ #`` dropped (preferreds, warrants,
  units, when-issued); names matching preferred / warrant / right / unit / note / debenture
  dropped; ADRs kept; unknown ``otherlisted`` exchange codes skipped.
- CA: TMX ``instruments[]`` flattened; instrument suffix tokens ``PR PF WT RT DB NT R IR``
  dropped (preferreds, warrants, rights, debentures, notes, instalment receipts); share
  classes ``.A .B .UN .U`` kept and written the Yahoo way (``CTC-A.TO``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from marketdata.errors import VendorError
from marketdata.http import market_get
from marketdata.symbol import Market, Symbol

logger = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
TMX_DIRECTORY_URL = "https://www.tsx.com/json/company-directory/search/{exchange}/%5E*"  # tsx | tsxv

US_EXCHANGE_NAMES = {
    "Q": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE American",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
}

_TMX_EXCHANGES = {"tsx": ("TSX", "TO"), "tsxv": ("TSXV", "V")}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BAD_SYMBOL_CHARS = set("$+=^#")
_BAD_NAME_RE = re.compile(r"Preferred|Warrant|Rights?\b|Units?\b|Notes? due|Debenture", re.IGNORECASE)
_TMX_DROP_TOKENS = {"PR", "PF", "WT", "RT", "DB", "NT", "R", "IR"}
_CA_ETF_RE = re.compile(r"\bETF\b|Index Fund|iShares|Vanguard|Horizons|Global X|BMO .*Fund", re.IGNORECASE)


@dataclass(frozen=True)
class Listing:
    symbol: str            # canonical TickerKeep code (US: BRK.B; CA: CTC-A.TO)
    yahoo_symbol: str      # Yahoo spelling (US: BRK-B; CA: same as symbol)
    exchange_symbol: str   # exchange spelling (US: BRK.B; CA: CTC.A)
    name: str
    market: str            # "US" | "CA"
    exchange: str          # NASDAQ / NYSE / NYSE American / NYSE Arca / Cboe BZX / IEX / TSX / TSXV
    is_etf: bool


# ---------------------------------------------------------------------------
# US: Nasdaq Trader symbol directories
# ---------------------------------------------------------------------------


def _pipe_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """Header + rows of a pipe-delimited Nasdaq Trader file, trailer line dropped."""
    lines = [ln.rstrip("\r") for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip() for h in lines[0].split("|")]
    rows: list[list[str]] = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < len(header):
            continue
        rows.append(cells)
    return header, rows


def _us_ok(symbol: str, name: str) -> bool:
    if not symbol or any(ch in _BAD_SYMBOL_CHARS for ch in symbol):
        return False
    if _BAD_NAME_RE.search(name or ""):
        return False
    return True


def _us_listing(symbol: str, name: str, exchange: str, is_etf: bool) -> Listing:
    code = symbol.strip().upper()
    return Listing(
        symbol=code,
        yahoo_symbol=Symbol(Market.US, code).to_yfinance(),
        exchange_symbol=code,
        name=name.strip(),
        market="US",
        exchange=exchange,
        is_etf=is_etf,
    )


def parse_nasdaq_listed(text: str) -> list[Listing]:
    """``nasdaqlisted.txt`` -> NASDAQ listings (``Symbol|Security Name|Market Category|Test Issue|
    Financial Status|Round Lot Size|ETF|NextShares``)."""
    header, rows = _pipe_rows(text)
    if not header:
        return []
    idx = {h: i for i, h in enumerate(header)}
    i_sym, i_name = idx.get("Symbol"), idx.get("Security Name")
    i_test, i_etf = idx.get("Test Issue"), idx.get("ETF")
    if i_sym is None or i_name is None:
        return []
    out: list[Listing] = []
    for cells in rows:
        if i_test is not None and cells[i_test].upper() != "N":
            continue
        symbol, name = cells[i_sym], cells[i_name]
        if not _us_ok(symbol, name):
            continue
        is_etf = i_etf is not None and cells[i_etf].upper() == "Y"
        out.append(_us_listing(symbol, name, "NASDAQ", is_etf))
    return out


def parse_other_listed(text: str) -> list[Listing]:
    """``otherlisted.txt`` -> NYSE / NYSE American / NYSE Arca / Cboe BZX / IEX listings
    (``ACT Symbol|Security Name|Exchange|CUSIP|ETF|Round Lot Size|Test Issue|NASDAQ Symbol``)."""
    header, rows = _pipe_rows(text)
    if not header:
        return []
    idx = {h: i for i, h in enumerate(header)}
    i_sym, i_name, i_exch = idx.get("ACT Symbol"), idx.get("Security Name"), idx.get("Exchange")
    i_test, i_etf = idx.get("Test Issue"), idx.get("ETF")
    if i_sym is None or i_name is None or i_exch is None:
        return []
    out: list[Listing] = []
    for cells in rows:
        if i_test is not None and cells[i_test].upper() != "N":
            continue
        exchange = US_EXCHANGE_NAMES.get(cells[i_exch].upper())
        if not exchange:
            continue
        symbol, name = cells[i_sym], cells[i_name]
        if not _us_ok(symbol, name):
            continue
        is_etf = i_etf is not None and cells[i_etf].upper() == "Y"
        out.append(_us_listing(symbol, name, exchange, is_etf))
    return out


# ---------------------------------------------------------------------------
# CA: TMX company directory
# ---------------------------------------------------------------------------


def _tmx_instrument_ok(symbol: str) -> bool:
    if not symbol or any(ch in _BAD_SYMBOL_CHARS for ch in symbol):
        return False
    tokens = symbol.upper().split(".")[1:]
    return not any(tok in _TMX_DROP_TOKENS for tok in tokens)


def parse_tmx_directory(payload: dict, exchange: str) -> list[Listing]:
    """TMX ``{"results": [{"symbol","name","instruments":[{"symbol","name"}]}]}`` ->
    listings for ``exchange`` (``"tsx"`` -> TSX/.TO, ``"tsxv"`` -> TSXV/.V)."""
    key = str(exchange or "").strip().lower()
    if key not in _TMX_EXCHANGES:
        raise ValueError(f"unknown TMX exchange {exchange!r} (expected tsx or tsxv)")
    exch_name, suffix = _TMX_EXCHANGES[key]
    results = (payload or {}).get("results") or []
    out: list[Listing] = []
    seen: set[str] = set()
    for issuer in results:
        if not isinstance(issuer, dict):
            continue
        issuer_name = str(issuer.get("name") or "").strip()
        instruments = issuer.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            instruments = [{"symbol": issuer.get("symbol"), "name": issuer_name}]
        for inst in instruments:
            if not isinstance(inst, dict):
                continue
            exch_sym = str(inst.get("symbol") or "").strip().upper()
            if not _tmx_instrument_ok(exch_sym):
                continue
            name = str(inst.get("name") or issuer_name).strip()
            base = exch_sym.replace(".", "-")
            code = f"{base}.{suffix}"
            if code in seen:
                continue
            seen.add(code)
            out.append(Listing(
                symbol=code,
                yahoo_symbol=code,
                exchange_symbol=exch_sym,
                name=name,
                market="CA",
                exchange=exch_name,
                is_etf=bool(_CA_ETF_RE.search(name)),
            ))
    return out


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _get_text(url: str, *, host_key: str, proxy: str | None, label: str) -> str | None:
    return market_get(
        url, host_key=host_key, headers={"User-Agent": _UA, "Accept": "text/plain, */*"},
        timeout=30, retries=1, parse="text", proxy=proxy, log_label=label,
    )


def fetch_us_listings(proxy: str | None = None) -> list[Listing]:
    """Both Nasdaq Trader files; raises ``VendorError`` only when both fail."""
    out: list[Listing] = []
    ok = False
    for url, parser, label in (
        (NASDAQ_LISTED_URL, parse_nasdaq_listed, "Nasdaq Trader nasdaqlisted"),
        (OTHER_LISTED_URL, parse_other_listed, "Nasdaq Trader otherlisted"),
    ):
        text = _get_text(url, host_key="www.nasdaqtrader.com", proxy=proxy, label=label)
        if not text:
            logger.warning(f"[universe] {label}: download failed")
            continue
        rows = parser(text)
        if rows:
            ok = True
        out.extend(rows)
    if not ok:
        raise VendorError("US listings: both Nasdaq Trader symbol directories failed")
    return out


def fetch_ca_listings(proxy: str | None = None) -> list[Listing]:
    """TSX + TSXV company directories; raises ``VendorError`` only when both fail."""
    out: list[Listing] = []
    ok = False
    for exchange in ("tsx", "tsxv"):
        payload = market_get(
            TMX_DIRECTORY_URL.format(exchange=exchange), host_key="www.tsx.com",
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=30, retries=1, parse="json", proxy=proxy, log_label=f"TMX directory {exchange}",
        )
        if not isinstance(payload, dict):
            logger.warning(f"[universe] TMX directory {exchange}: download failed")
            continue
        rows = parse_tmx_directory(payload, exchange)
        if rows:
            ok = True
        out.extend(rows)
    if not ok:
        raise VendorError("CA listings: both TMX company directories failed")
    return out
