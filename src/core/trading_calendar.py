"""Trading calendar: answers "is the market open on this day?".

Complements ``MarketDef.is_trading_time()`` (which answers "are we inside a
session right now"). Scheduled jobs such as the pre-market plan and the daily
summary run *outside* the session window by design, so they can only be
guarded by "is today a trading day".

Data sources
- **US / CA**: ``exchange_calendars`` (XNYS / XTSE), built lazily per market for a
  three-year window (last year .. next year) and cached in memory. It knows the
  exchange holidays *and* early closes (e.g. the half day after Thanksgiving).
- **Fallback**: when ``exchange_calendars`` is not installed, or the requested
  date falls outside the built window, a fixed weekday-holiday table
  (``_MARKET_HOLIDAYS``, current and next year) is used.
- **CRYPTO / GOLD**: always open.

Degradation principle
When nothing better is known, fall back to "weekends only". Sending one extra
notification is a nuisance; silently skipping a whole trading day is an
incident.

Concurrency
The synchronous helpers never touch the network. Building a calendar is pure
CPU (tens of milliseconds); ``refresh()`` pre-builds both calendars in a
worker thread so the first scheduled job does not pay that cost on the event
loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_FALLBACK_TZ = "America/Vancouver"

# Market code -> exchange_calendars calendar name.
_CAL_CODES: dict[str, str] = {"US": "XNYS", "CA": "XTSE"}

# Built calendars, keyed by market code, with the (start_year, end_year) window each covers.
_CALENDARS: dict[str, object] = {}
_CAL_WINDOWS: dict[str, tuple[int, int]] = {}
_XCALS_MISSING_LOGGED = False

# Upper bound for the day-by-day walk in next/previous_trading_day when the
# calendar is unavailable. No exchange closes for more than a couple of weeks.
_MAX_WALK_DAYS = 30

# Fixed exchange holiday tables (fallback when exchange_calendars is unavailable).
# Weekends are handled separately; only weekday closures are listed. Covers the
# current and the next calendar year; extend when the year rolls over.
_NYSE_HOLIDAYS: frozenset[date] = frozenset({
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # Martin Luther King Jr. Day
    date(2027, 2, 15),   # Presidents' Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth (observed)
    date(2027, 7, 5),    # Independence Day (observed)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas (observed)
})

_TSX_HOLIDAYS: frozenset[date] = frozenset({
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 2, 16),   # Family Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 18),   # Victoria Day
    date(2026, 7, 1),    # Canada Day
    date(2026, 8, 3),    # Civic Holiday
    date(2026, 9, 7),    # Labour Day
    date(2026, 10, 12),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    date(2026, 12, 28),  # Boxing Day (observed)
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 2, 15),   # Family Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 24),   # Victoria Day
    date(2027, 7, 1),    # Canada Day
    date(2027, 8, 2),    # Civic Holiday
    date(2027, 9, 6),    # Labour Day
    date(2027, 10, 11),  # Thanksgiving
    date(2027, 12, 27),  # Christmas (observed)
    date(2027, 12, 28),  # Boxing Day (observed)
})

_MARKET_HOLIDAYS: dict[str, frozenset[date]] = {
    "US": _NYSE_HOLIDAYS,
    "CA": _TSX_HOLIDAYS,
}


# ── cache management ────────────────────────────────────────────────────────


def reset_cache() -> None:
    """Drop every built calendar (config change or tests)."""
    global _XCALS_MISSING_LOGGED
    _CALENDARS.clear()
    _CAL_WINDOWS.clear()
    _XCALS_MISSING_LOGGED = False


def _to_market_code(market):
    """Normalise a MarketCode / string into a MarketCode; ``None`` when unknown."""
    from src.models.market import MarketCode

    if isinstance(market, MarketCode):
        return market
    try:
        return MarketCode(str(market).strip().upper())
    except ValueError:
        return None


def _market_key(market) -> str:
    code = _to_market_code(market)
    return code.value if code is not None else str(market or "").strip().upper()


def _market_tz(code) -> ZoneInfo:
    from src.models.market import MARKETS

    md = MARKETS.get(code) if code else None
    return md.get_tz() if md else ZoneInfo(_FALLBACK_TZ)


def _now_in_market_tz(code) -> datetime:
    """Current time in the market's zone. Separate function so tests can inject it."""
    return datetime.now(_market_tz(code))


def _resolve_date(code, d: date | datetime | None) -> date:
    """Normalise the input into the market-local calendar date."""
    if d is None:
        return _now_in_market_tz(code).date()
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            d = d.astimezone(_market_tz(code))
        return d.date()
    return d


def _get_calendar(market, ref: date | None = None):
    """Return the exchange_calendars calendar for ``market`` (built lazily, cached).

    The calendar covers Jan 1 of the year before ``ref`` through Dec 31 of the
    year after. ``ref`` defaults to today in the market's zone; a cached
    calendar is reused as long as it covers ``ref``'s year, otherwise it is
    rebuilt around ``ref``. Returns ``None`` when the market has no exchange
    calendar, when ``exchange_calendars`` is not installed (logged once), or
    when building fails.
    """
    global _XCALS_MISSING_LOGGED

    key = _market_key(market)
    cal_name = _CAL_CODES.get(key)
    if cal_name is None:
        return None

    if ref is None:
        ref = _now_in_market_tz(_to_market_code(key)).date()

    cached = _CALENDARS.get(key)
    window = _CAL_WINDOWS.get(key)
    if cached is not None and window and window[0] <= ref.year <= window[1]:
        return cached

    try:
        import exchange_calendars as xcals
    except ImportError:
        if not _XCALS_MISSING_LOGGED:
            logger.warning(
                "[trading calendar] exchange_calendars not installed; "
                "falling back to the fixed holiday table"
            )
            _XCALS_MISSING_LOGGED = True
        return None

    start_year, end_year = ref.year - 1, ref.year + 1
    try:
        cal = xcals.get_calendar(
            cal_name, start=f"{start_year}-01-01", end=f"{end_year}-12-31"
        )
    except Exception as e:  # noqa: BLE001 - never let a calendar bug block a job
        logger.warning("[trading calendar] failed to build %s: %s", cal_name, e)
        return None

    _CALENDARS[key] = cal
    _CAL_WINDOWS[key] = (start_year, end_year)
    logger.info(
        "[trading calendar] %s (%s) built for %s..%s", key, cal_name, start_year, end_year
    )
    return cal


def refresh_blocking() -> bool:
    """Build the US and CA calendars now. Returns True only when both are ready."""
    ok = True
    for key in _CAL_CODES:
        if _get_calendar(key) is None:
            ok = False
    return ok


async def refresh() -> bool:
    """Async wrapper around ``refresh_blocking`` (worker thread, event loop stays free)."""
    return await asyncio.to_thread(refresh_blocking)


# ── queries ─────────────────────────────────────────────────────────────────


def _is_session(cal, target: date) -> bool | None:
    """``cal.is_session`` guarded against out-of-bounds dates (``None`` = unknown)."""
    try:
        return bool(cal.is_session(target.isoformat()))
    except Exception:  # noqa: BLE001 - DateOutOfBounds and friends
        return None


def is_trading_day(market, d: date | datetime | None = None) -> bool:
    """Whether the given market is open on the given day.

    Args:
        market: ``MarketCode`` or market code string (US / CA / CRYPTO / GOLD).
        d: Target date; ``None`` means today in the market's zone. A tz-aware
           ``datetime`` is first converted into the market's zone.
    """
    from src.models.market import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)

    # Crypto trades 24/7 and spot gold nearly so: never closed for a weekend.
    if code in (MarketCode.CRYPTO, MarketCode.GOLD):
        return True

    # Weekends: every session market is closed. Zero dependencies, always right.
    if target.weekday() >= 5:
        return False

    # Exchange calendar (holidays + early closes) when available.
    cal = _get_calendar(code, target)
    if cal is not None:
        known = _is_session(cal, target)
        if known is not None:
            return known

    # Fixed holiday table for the session markets.
    holidays = _MARKET_HOLIDAYS.get(code.value if code else "")
    if holidays is not None:
        return target not in holidays

    # Unknown market / no table: weekends only.
    return True


def session_close(market, d: date | datetime | None = None) -> datetime | None:
    """Tz-aware local close time for ``d`` (early closes honoured).

    Returns ``None`` when ``d`` is not a session. Without an exchange calendar
    the close falls back to the market's configured last session end, provided
    the day is a trading day per ``is_trading_day``.
    """
    from src.models.market import MARKETS

    code = _to_market_code(market)
    if code is None:
        return None
    target = _resolve_date(code, d)

    cal = _get_calendar(code, target)
    if cal is not None:
        known = _is_session(cal, target)
        if known is False:
            return None
        if known:
            try:
                ts = cal.session_close(target.isoformat())
                return ts.tz_convert(cal.tz).to_pydatetime()
            except Exception as e:  # noqa: BLE001
                logger.debug("[trading calendar] session_close(%s, %s) failed: %s", code, target, e)

    if not is_trading_day(code, target):
        return None
    md = MARKETS.get(code)
    if not md or not md.sessions:
        return None
    end = max(s.end for s in md.sessions)
    return datetime.combine(target, end, tzinfo=md.get_tz())


def _walk(market, start: date, step: int) -> date:
    """Step day by day from ``start`` (exclusive) until a trading day is found."""
    cur = start
    for _ in range(_MAX_WALK_DAYS):
        cur = cur + timedelta(days=step)
        if is_trading_day(market, cur):
            return cur
    return cur


def _session_from_calendar(code, anchor: date, direction: str) -> date | None:
    cal = _get_calendar(code, anchor)
    if cal is None:
        return None
    try:
        ts = cal.date_to_session(anchor.isoformat(), direction=direction)
        return ts.date()
    except Exception:  # noqa: BLE001 - out of bounds -> walk instead
        return None


def next_trading_day(market, d: date | datetime | None = None) -> date:
    """First trading day strictly after ``d`` (``d`` defaults to today in market tz)."""
    from src.models.market import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)
    if code in (MarketCode.CRYPTO, MarketCode.GOLD):
        return target + timedelta(days=1)
    found = _session_from_calendar(code, target + timedelta(days=1), "next")
    if found is not None:
        return found
    return _walk(code, target, +1)


def previous_trading_day(market, d: date | datetime | None = None) -> date:
    """Last trading day strictly before ``d`` (``d`` defaults to today in market tz)."""
    from src.models.market import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)
    if code in (MarketCode.CRYPTO, MarketCode.GOLD):
        return target - timedelta(days=1)
    found = _session_from_calendar(code, target - timedelta(days=1), "previous")
    if found is not None:
        return found
    return _walk(code, target, -1)


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    """True when any enabled equity market (EQUITY_MARKETS) is open on ``d``."""
    from src.models.market import EQUITY_MARKETS, MarketCode

    return any(is_trading_day(MarketCode(m), d) for m in EQUITY_MARKETS)
