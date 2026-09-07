"""Shared SEC EDGAR helpers (keyless). Used by the fundamentals, holders and filings vendors.

SEC fair-access rules: a descriptive ``User-Agent`` with a contact address, at most 10
requests per second. The contact comes from the DataSource ``contact_email`` config, else
the ``SEC_CONTACT_EMAIL`` environment variable, else the literal default
``contact@panwatch.local``. Never hard-code a personal address here.
"""

from __future__ import annotations

import os
from typing import Any

from marketdata.cache import TTLCache
from marketdata.http import market_get

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FORM4_ATOM_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}"
    "&type=4&dateb=&owner=include&count=40&output=atom"
)
DEFAULT_CONTACT = "contact@panwatch.local"

_SEC_MIN_INTERVAL_S = 0.12  # < 10 requests/second
_cik_cache = TTLCache(default_ttl_sec=86400.0, max_size=4)


def contact_email(config: dict | None) -> str:
    email = str((config or {}).get("contact_email") or "").strip()
    if not email:
        email = str(os.environ.get("SEC_CONTACT_EMAIL") or "").strip()
    return email or DEFAULT_CONTACT


def sec_headers(config: dict | None) -> dict[str, str]:
    """Headers every SEC request must carry."""
    return {
        "User-Agent": f"PanWatch/1.0 ({contact_email(config)})",
        "Accept-Encoding": "gzip, deflate",
    }


def sec_get(url: str, *, config: dict | None, host_key: str, **kw: Any) -> Any | None:
    """``market_get`` with the SEC headers, throttle and defaults (JSON unless ``parse`` given)."""
    params = {
        "min_interval_s": _SEC_MIN_INTERVAL_S,
        "timeout": 12,
        "retries": 1,
        "parse": "json",
        "log_label": "SEC EDGAR",
    }
    params.update(kw)
    headers = {**sec_headers(config), **(params.pop("headers", None) or {})}
    proxy = (config or {}).get("proxy") or params.pop("proxy", None)
    return market_get(url, host_key=host_key, headers=headers, proxy=proxy or None, **params)


def _load_ticker_map(config: dict | None) -> dict[str, int]:
    cached = _cik_cache.get("map")
    if cached is not None:
        return cached
    payload = sec_get(COMPANY_TICKERS_URL, config=config, host_key="www.sec.gov",
                      log_label="SEC company_tickers")
    mapping: dict[str, int] = {}
    rows = payload.values() if isinstance(payload, dict) else (payload or [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        try:
            cik = int(row.get("cik_str") or row.get("cik") or 0)
        except (TypeError, ValueError):
            continue
        if ticker and cik:
            mapping[ticker] = cik
    if mapping:
        _cik_cache.set("map", mapping)
    return mapping


def resolve_cik(ticker: str, config: dict | None = None) -> int | None:
    """Ticker -> CIK from ``company_tickers.json`` (24 h cache). Tries the SEC spelling
    (``BRK-B``) and the exchange spelling (``BRK.B``). ``None`` when unknown."""
    t = str(ticker or "").strip().upper()
    if not t:
        return None
    mapping = _load_ticker_map(config)
    if not mapping:
        return None
    candidates = [t, t.replace(".", "-"), t.replace("-", "."), t.replace("_", "-"), t.replace("_", ".")]
    for c in candidates:
        cik = mapping.get(c)
        if cik:
            return cik
    return None


def reset_cik_cache() -> None:
    _cik_cache.clear()
