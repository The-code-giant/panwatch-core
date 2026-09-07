"""Stock link builder: turns (symbol, market, platform) into a quote-page URL.

Global setting key: ``stock_link_platform`` (default ``yahoo``).
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from src.web.database import SessionLocal
from src.web.models import AppSettings

logger = logging.getLogger(__name__)

# Supported platforms {code: display name}. Yahoo Finance covers US, Canada, crypto and
# gold; Xueqiu is kept as an opt-in alternative (US/HK/CN only).
PLATFORMS = {
    "yahoo": "Yahoo Finance",
    "xueqiu": "Xueqiu",
}

DEFAULT_PLATFORM = "yahoo"
SETTING_KEY = "stock_link_platform"


def get_platform() -> str:
    """Read the configured platform code from AppSettings."""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
        value = (row.value if row and row.value else DEFAULT_PLATFORM).strip().lower()
        return value if value in PLATFORMS else DEFAULT_PLATFORM
    finally:
        db.close()


def stock_url(symbol: str, market: str, platform: str = "") -> str:
    """Build the quote-page URL.

    Args:
        symbol: e.g. "AAPL", "SHOP.TO", "BTC-USD", "XAUUSD"
        market: market code, e.g. "US", "CA", "CRYPTO", "GOLD"
        platform: platform code; read from the global setting when empty
    """
    if not platform:
        platform = get_platform()

    m = (market or "").upper()

    if platform == "xueqiu" and m in ("US", "HK", "CN"):
        return _xueqiu_url(symbol, m)

    # Default (and fallback for any market Xueqiu cannot serve): Yahoo Finance.
    return _yahoo_url(symbol, m)


def stock_link_markdown(symbol: str, market: str, platform: str = "") -> str:
    """Markdown link: [SHOP.TO.CA](https://finance.yahoo.com/quote/SHOP.TO)"""
    code = f"{symbol}.{market}"
    url = stock_url(symbol, market, platform)
    return f"[{code}]({url})"


# ---------------------------------------------------------------------------
# Per-platform URL builders
# ---------------------------------------------------------------------------

def _yahoo_url(symbol: str, market: str) -> str:
    """Yahoo Finance quote page. Symbols are already in Yahoo form for US/CA/CRYPTO;
    spot gold maps to the COMEX front-month future, matching the quote vendor."""
    if market == "GOLD" and symbol.upper() == "XAUUSD":
        ysym = "GC=F"
    elif market == "HK" and symbol.isdigit():
        ysym = f"{int(symbol):04d}.HK"
    else:
        ysym = symbol
    return f"https://finance.yahoo.com/quote/{quote(ysym, safe='.-=^')}"


def _xueqiu_url(symbol: str, market: str) -> str:
    if market in ("US", "HK"):
        return f"https://xueqiu.com/S/{symbol}"
    # Legacy CN A-shares (only reachable when the platform is explicitly set to xueqiu)
    from src.core.cn_symbol import get_cn_prefix
    prefix = get_cn_prefix(symbol, upper=True)
    return f"https://xueqiu.com/S/{prefix}{symbol}"
