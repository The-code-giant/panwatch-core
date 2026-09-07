"""Market model: codes, sessions, symbol patterns, and the single source of truth
for which markets are enabled in this deployment.

Enabled markets come from the ``ENABLED_MARKETS`` environment variable
(comma separated, default ``US,CA,CRYPTO,GOLD``). Every market list in the
backend must read ``ENABLED_MARKETS`` / ``EQUITY_MARKETS`` from here instead of
hard-coding codes, so retired markets (CN, HK) stay unreachable from defaults,
filters, allocations and prompts without their code having to be deleted.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class MarketCode(str, Enum):
    CN = "CN"  # Mainland China A-shares (retired; kept for gated legacy code)
    HK = "HK"  # Hong Kong (retired; kept for gated legacy code)
    US = "US"  # United States (NYSE / Nasdaq / AMEX)
    CA = "CA"  # Canada (TSX / TSX Venture, Yahoo symbol form SHOP.TO / XYZ.V)
    CRYPTO = "CRYPTO"  # Crypto (24/7)
    GOLD = "GOLD"  # Spot gold
    UNKNOWN = "UNKNOWN"  # Typed placeholder for a stored market string that is not a
    # known code. Never a tradable market: must never appear in ENABLED_MARKETS,
    # EQUITY_MARKETS, or _SESSION_MARKETS, and must never be enabled via env var.


# Markets that trade in sessions and can hold positions (as opposed to the
# 24/7 watchlist-only extras CRYPTO and GOLD). Order matters: the first enabled
# entry becomes ``default_market()``.
_SESSION_MARKETS: tuple[str, ...] = ("US", "CA", "CN", "HK")
_DEFAULT_ENABLED = "US,CA,CRYPTO,GOLD"


def _parse_enabled(raw: str | None) -> tuple[str, ...]:
    out: list[str] = []
    for part in (raw or _DEFAULT_ENABLED).split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code == MarketCode.UNKNOWN.value:
            # UNKNOWN is a typed placeholder for "not a known market" — never
            # a real market, so it can never be enabled even via env var.
            continue
        try:
            MarketCode(code)
        except ValueError:
            continue
        if code not in out:
            out.append(code)
    return tuple(out) or tuple(_DEFAULT_ENABLED.split(","))


ENABLED_MARKETS: tuple[str, ...] = _parse_enabled(os.environ.get("ENABLED_MARKETS"))
EQUITY_MARKETS: tuple[str, ...] = tuple(m for m in ENABLED_MARKETS if m in _SESSION_MARKETS) or ("US",)


def is_enabled(market) -> bool:
    """True if ``market`` (MarketCode or code string) is enabled in this deployment."""
    code = market.value if isinstance(market, MarketCode) else str(market or "").strip().upper()
    return code in ENABLED_MARKETS


def default_market() -> str:
    """The default equity market code (first enabled equity market, normally ``US``)."""
    return EQUITY_MARKETS[0]


def market_display_name(market) -> str:
    """Human label for a market code (``US`` → "US", ``CA`` → "Canada", ``CRYPTO`` → "Crypto")."""
    code = market.value if isinstance(market, MarketCode) else str(market or "").strip().upper()
    try:
        return MARKETS[MarketCode(code)].name
    except (ValueError, KeyError):
        return code


def normalize_market(value, *, allowed: tuple[str, ...] | None = None) -> str:
    """Normalize a market code string; unknown/disabled codes fall back to ``default_market()``."""
    code = value.value if isinstance(value, MarketCode) else str(value or "").strip().upper()
    pool = allowed if allowed is not None else EQUITY_MARKETS
    return code if code in pool else default_market()


@dataclass
class TradingSession:
    """One trading session"""
    start: time
    end: time


@dataclass
class MarketDef:
    """Market definition"""
    code: MarketCode
    name: str
    timezone: str
    sessions: list[TradingSession]
    symbol_pattern: str  # regex used to validate the symbol format

    def get_tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def is_trading_time(self, dt: datetime | None = None) -> bool:
        """Whether the given time falls inside a trading session"""
        if dt is None:
            dt = datetime.now(self.get_tz())
        else:
            dt = dt.astimezone(self.get_tz())

        # Never trading on a non-trading day (weekend / exchange holiday).
        # Lazy import: trading_calendar depends on MarketCode/MARKETS from this module.
        from src.core.trading_calendar import is_trading_day

        if not is_trading_day(self.code, dt.date()):
            return False

        current_time = dt.time()
        return any(
            session.start <= current_time <= session.end
            for session in self.sessions
        )


# Predefined market definitions. Retired markets (CN, HK) remain defined so
# legacy code paths can be gated on ``is_enabled`` rather than deleted.
MARKETS: dict[MarketCode, MarketDef] = {
    MarketCode.CN: MarketDef(
        code=MarketCode.CN,
        name="A-shares",
        timezone="Asia/Shanghai",
        sessions=[
            TradingSession(time(9, 30), time(11, 30)),
            TradingSession(time(13, 0), time(15, 0)),
        ],
        symbol_pattern=r"^[036]\d{5}$",
    ),
    MarketCode.HK: MarketDef(
        code=MarketCode.HK,
        name="HK stocks",
        timezone="Asia/Hong_Kong",
        sessions=[
            TradingSession(time(9, 30), time(12, 0)),
            TradingSession(time(13, 0), time(16, 0)),
        ],
        symbol_pattern=r"^\d{5}$",
    ),
    MarketCode.US: MarketDef(
        code=MarketCode.US,
        name="US",
        timezone="America/New_York",
        sessions=[
            TradingSession(time(9, 30), time(16, 0)),
        ],
        # Uppercase ticker, may contain a dot or hyphen (BRK.B, BF-B).
        symbol_pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$",
    ),
    MarketCode.CA: MarketDef(
        code=MarketCode.CA,
        name="Canada",
        timezone="America/Toronto",
        sessions=[
            TradingSession(time(9, 30), time(16, 0)),
        ],
        # Yahoo form: TSX ``.TO``, TSX Venture ``.V`` (also NEO ``.NE`` / CSE ``.CN``).
        symbol_pattern=r"^[A-Z][A-Z0-9.\-]{0,7}\.(TO|V|NE|CN)$",
    ),
    MarketCode.CRYPTO: MarketDef(
        code=MarketCode.CRYPTO,
        name="Crypto",
        timezone="UTC",
        # Crypto trades 24/7; no closed session.
        sessions=[
            TradingSession(time(0, 0), time(23, 59, 59)),
        ],
        symbol_pattern=r"^[A-Z0-9]{2,10}-USD$",
    ),
    MarketCode.GOLD: MarketDef(
        code=MarketCode.GOLD,
        name="Gold",
        timezone="UTC",
        # Spot gold trades nearly around the clock (Sunday evening to Friday evening); approximated as 24/7.
        sessions=[
            TradingSession(time(0, 0), time(23, 59, 59)),
        ],
        symbol_pattern=r"^XAUUSD$",
    ),
}


@dataclass
class StockData:
    """Normalized quote data"""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float       # change %
    change_amount: float    # change amount
    volume: float           # volume
    turnover: float         # turnover (source currency)
    open_price: float
    high_price: float
    low_price: float
    prev_close: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class IndexData:
    """Market index data"""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float
    change_amount: float
    volume: float
    turnover: float
    timestamp: datetime = field(default_factory=datetime.now)
