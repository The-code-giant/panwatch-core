"""账户和持仓管理 API"""
import logging
import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from datetime import datetime, timedelta, timezone

from src.web.database import get_db
from src.web.models import Account, PriceAlertRule, Position, Stock
from src.core.marketdata_client import md_quote_rows
from src.collectors.market_http import TTLCache
from src.models.market import ENABLED_MARKETS, MarketCode, default_market, is_enabled

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# FX: base currency is USD. US positions convert at 1.0, Canadian positions at the
# CAD→USD rate; every other enabled market is treated as 1.0. The rate comes from the
# Bank of Canada Valet API (FXUSDCAD, CAD per USD, inverted), then Yahoo's CAD=X via
# the marketdata macro strip. Cached for an hour with a short backoff after a failed
# fetch.
#
# There is NO constant fallback in any valuation or response. Each request resolves
# the rate exactly once, AFTER the bounded refresh, into an immutable ``FxSnapshot``
# (``resolve_fx_snapshot``) that is threaded explicitly through position valuation,
# account/grand totals, the analytics holdings gatherer and response serialization —
# so positions, totals and the advertised rate always describe the same moment.
#
# ATOMICITY: the cache is only ever read and rebound under ``_fx_cond``. The single
# internal resolver ``_resolve_cad_usd_state()`` decides the WHOLE state (rate,
# known/genuine, fetched_at, source) in one place and returns it as one frozen
# ``_FxCacheState`` object; ``resolve_fx_snapshot`` derives the snapshot from that
# ONE object and performs no further global reads, so a concurrent refresh can never
# pair one request's rate with another request's provenance. The refresh itself is
# bounded single-flight: one leader fetches outside the lock, followers wait a bounded
# time for its outcome, and a failed (or older) refresh never overwrites a newer
# successful state.
# ``FxSnapshot.status`` is one of:
#   known       — a genuine fetch succeeded in this request, or the cache holds a
#                 genuinely fetched rate that is still within TTL.
#   last_known  — the cache holds a REAL previously fetched rate, but it has expired
#                 and this request's bounded refresh failed. It may be used for
#                 display/conversion, labelled with ``as_of``/``source``, but it can
#                 never make a valuation complete.
#   unknown     — no genuine rate has ever been obtained. ``rate`` is None and no
#                 CAD figure is ever converted.
# ---------------------------------------------------------------------------
BASE_CURRENCY = "USD"
# Legacy placeholder returned ONLY by the legacy ``get_cad_usd_rate()`` accessor while
# no genuine rate has ever been fetched (kept for callers/tests that predate the
# snapshot). It is never a conversion rate: ``resolve_fx_snapshot`` maps a never-fetched
# cache to ``unknown``/``None`` and no response ever carries this number.
_CAD_USD_FALLBACK = 0.73
# Cache storage. A plain module-level dict (tests seed partial dicts and read
# ``["rate"]``/``["ts"]`` back), rebound WHOLESALE under ``_fx_cond`` and never
# mutated in place. Nothing outside ``_resolve_cad_usd_state`` may read it for a
# valuation decision — it is decoded into one frozen ``_FxCacheState`` there. Keys
# (all read tolerantly):
#   rate       — cached value (the placeholder until the first genuine fetch)
#   ts         — validity basis for the TTL / failure backoff
#   known      — ``rate`` is genuine AND current (last attempt succeeded, within TTL)
#   genuine    — ``rate`` came from a real fetch at some point (survives failed refreshes)
#   fetched_at — epoch seconds of the fetch that produced ``rate`` (None if never)
#   source     — provider label of that fetch (None if never)
_cad_usd_rate_cache: dict = {
    "rate": _CAD_USD_FALLBACK,
    "ts": 0,
    "known": False,
    "genuine": False,
    "fetched_at": None,
    "source": None,
}
# Guards every read/rebind of ``_cad_usd_rate_cache`` and the single-flight flag.
# Never held across a network fetch or any unrelated request work.
_fx_cond = threading.Condition()
_fx_refresh_inflight = False
EXCHANGE_RATE_TTL = 3600  # 1 hour
# Back off after a failed fetch: without this every portfolio valuation would retry a
# 5 s synchronous request while the rate source is unreachable.
EXCHANGE_RATE_FAIL_RETRY = 300  # 5 minutes
# Upper bound a follower waits for the in-flight leader refresh before proceeding
# with the (expired) cache state it can already see. Covers both fetchers' timeouts.
EXCHANGE_RATE_REFRESH_WAIT = 12.0  # seconds
_BOC_FX_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
_FX_SANITY = (0.3, 1.5)  # CAD→USD sanity band

FxStatus = Literal["known", "last_known", "unknown"]


@dataclass(frozen=True)
class FxSnapshot:
    """The CAD→USD rate as resolved ONCE for a request, frozen so every consumer
    (position valuation, account/grand totals, ``_gather_holdings``, the
    ``exchange_rates`` block) sees the identical rate and provenance."""

    status: FxStatus
    rate: float | None  # None iff status == "unknown"
    as_of: float | None  # epoch seconds of the fetch that produced ``rate``
    source: str | None  # e.g. "Bank of Canada", "Yahoo CAD=X"

    @property
    def available(self) -> bool:
        """A rate exists for conversion (``known`` or ``last_known``)."""
        return self.rate is not None


FX_UNKNOWN = FxSnapshot(status="unknown", rate=None, as_of=None, source=None)


@dataclass(frozen=True)
class _FxCacheState:
    """One immutable, internally consistent decode of ``_cad_usd_rate_cache`` — the
    single object ``_resolve_cad_usd_state`` returns. ``rate``/``known``/``genuine``/
    ``fetched_at``/``source`` all describe the SAME cache version."""

    rate: float | None
    ts: float
    known: bool
    genuine: bool
    fetched_at: float | None
    source: str | None


def _fx_state_from_cache(cache: dict) -> _FxCacheState:
    """Tolerant decode of one cache version (tests seed partial dicts)."""
    known = bool(cache.get("known", False))
    try:
        ts = float(cache.get("ts", 0) or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return _FxCacheState(
        rate=cache.get("rate", _CAD_USD_FALLBACK),
        ts=ts,
        known=known,
        genuine=bool(cache.get("genuine", known)),
        fetched_at=cache.get("fetched_at"),
        source=cache.get("source"),
    )


def _fx_state_as_of(state: _FxCacheState, now: float) -> _FxCacheState:
    """Expiry normalization of ONE decoded cache version at decision time ``now``.

    The stored ``known`` flag only records that the LAST fetch succeeded; whether that
    success is still current is a function of the TTL. A state may therefore assert
    ``known`` only while its version is within ``EXCHANGE_RATE_TTL``; an expired
    version keeps its rate / provenance / ``genuine`` flag untouched (a usable stale
    value is never discarded) but drops ``known`` so it is reported as ``last_known``
    (real-but-expired) or, when never genuinely fetched, ``unknown``. Pure: derives a
    new frozen object from the given one and reads no global."""
    if state.known and now - state.ts >= EXCHANGE_RATE_TTL:
        return replace(state, known=False)
    return state


def _fx_proxy() -> str | None:
    try:
        from src.core.notifier import get_global_proxy

        proxy = (get_global_proxy() or "").strip()
        if proxy:
            return proxy
    except Exception:
        pass
    try:
        from src.config import Settings

        return (Settings().http_proxy or "").strip() or None
    except Exception:
        return None


def _in_band(rate: float) -> bool:
    lo, hi = _FX_SANITY
    return lo < rate < hi


def _fetch_cad_usd_boc() -> float | None:
    """Bank of Canada Valet: latest FXUSDCAD observation (CAD per USD) -> CAD→USD."""
    resp = httpx.get(
        _BOC_FX_URL,
        params={"recent": 1},
        timeout=5,
        headers={"User-Agent": "Mozilla/5.0"},
        proxy=_fx_proxy(),
    )
    payload = resp.json()
    observations = payload.get("observations") or []
    if not observations:
        return None
    usd_cad = float(((observations[-1] or {}).get("FXUSDCAD") or {}).get("v") or 0.0)
    if usd_cad <= 0:
        return None
    return 1.0 / usd_cad


def _fetch_cad_usd_macro() -> float | None:
    """Yahoo CAD=X (CAD per USD) through the marketdata macro strip -> CAD→USD."""
    from src.core.marketdata_client import get_market_data

    rows = get_market_data().macro(("CAD=X",))
    for row in rows or []:
        if row.get("symbol") == "CAD=X" and row.get("current_price"):
            usd_cad = float(row["current_price"])
            if usd_cad > 0:
                return 1.0 / usd_cad
    return None


def _fetch_cad_usd_rate() -> tuple[float, str] | None:
    """Try the providers in order OUTSIDE any lock; ``(rate, label)`` on the first
    in-band success, ``None`` when every provider failed. Module-level names are
    looked up at call time (tests patch the fetchers)."""
    for label, fetch in (("Bank of Canada", _fetch_cad_usd_boc), ("Yahoo CAD=X", _fetch_cad_usd_macro)):
        try:
            rate = fetch()
        except Exception as e:
            logger.warning(f"Failed to fetch CAD/USD rate from {label}: {e}")
            continue
        if rate is None:
            logger.warning(f"No CAD/USD rate from {label}")
            continue
        if _in_band(rate):
            return rate, label
        logger.warning(f"CAD/USD rate from {label} out of band, ignoring: {rate}")
    return None


def _resolve_cad_usd_state() -> _FxCacheState:
    """THE single FX resolver: bounded refresh (TTL, then failure backoff, single-
    flight) and return the ENTIRE resulting cache state as one frozen object.

    Every decision about the returned state is made under ``_fx_cond`` against one
    cache version, so the returned rate, known/genuine flags and provenance always
    belong together. The network fetch runs outside the lock; concurrent callers that
    also find the cache expired wait (bounded by ``EXCHANGE_RATE_REFRESH_WAIT``) for
    the leader's outcome instead of fetching in parallel. Before writing, the leader
    re-checks the cache: a failed refresh never overwrites a state rebound meanwhile,
    and a successful one never overwrites a NEWER successful state.

    On success the state is ``known``+``genuine`` with fresh provenance; on failure
    the previous rate and its provenance are retained, ``known`` drops to False (so
    it is never asserted after a failed refresh) while ``genuine`` survives so a
    real-but-expired rate is reported as ``last_known`` rather than ``unknown``.

    INVARIANT: every state returned from here asserts ``known`` only if its version is
    within ``EXCHANGE_RATE_TTL`` at the moment of the decision (``_fx_state_as_of``).
    In particular a follower whose bounded wait timed out, or whose leader aborted
    without rebinding the cache, sees the same expired version that triggered the
    refresh and reports it as ``last_known`` (genuine) / ``unknown`` (never fetched),
    never as ``known`` — while keeping that version's rate/fetched_at/source intact
    and without fetching a second time.
    """
    global _cad_usd_rate_cache, _fx_refresh_inflight

    with _fx_cond:
        observed = _cad_usd_rate_cache
        state = _fx_state_from_cache(observed)
        if time.time() - state.ts < EXCHANGE_RATE_TTL:
            return state
        if _fx_refresh_inflight:
            # Follower: wait (bounded) for the leader, then return whatever single
            # version the cache holds afterwards — never fetch a second time. The
            # wait may end by timeout (leader still fetching) or because the leader
            # aborted without rebinding the cache: in both cases the version seen
            # here is the same EXPIRED one that triggered the refresh, and its stored
            # ``known=True`` must not be trusted. Normalize expiry against the TTL
            # under the lock, on the single version read here, so the returned state
            # is ``known`` only for a genuine in-TTL success, ``last_known`` for a
            # real-but-expired rate and ``unknown`` for a never-fetched cache — with
            # rate/fetched_at/source still belonging to that one version.
            _fx_cond.wait_for(lambda: not _fx_refresh_inflight, timeout=EXCHANGE_RATE_REFRESH_WAIT)
            return _fx_state_as_of(_fx_state_from_cache(_cad_usd_rate_cache), time.time())
        _fx_refresh_inflight = True

    try:
        fetched = _fetch_cad_usd_rate()
    except BaseException:
        _release_fx_refresh()
        raise

    with _fx_cond:
        try:
            now = time.time()
            current = _cad_usd_rate_cache
            if current is not observed:
                # The cache was rebound while we were fetching. Never clobber it with
                # a failure, nor with a rate older than the one it now holds.
                current_state = _fx_state_from_cache(current)
                if fetched is None or (current_state.fetched_at or 0) >= now:
                    return _fx_state_as_of(current_state, now)
            if fetched is not None:
                rate, label = fetched
                _cad_usd_rate_cache = {
                    "rate": rate,
                    "ts": now,
                    "known": True,
                    "genuine": True,
                    "fetched_at": now,
                    "source": label,
                }
                logger.info(f"Updated CAD/USD rate from {label}: {rate:.4f}")
            else:
                logger.warning(
                    "CAD/USD rate refresh failed; %s",
                    "keeping the last genuine rate as last_known"
                    if state.genuine
                    else "no genuine rate has ever been fetched",
                )
                _cad_usd_rate_cache = {
                    "rate": state.rate if state.rate is not None else _CAD_USD_FALLBACK,
                    "ts": now - EXCHANGE_RATE_TTL + EXCHANGE_RATE_FAIL_RETRY,
                    "known": False,
                    "genuine": state.genuine,
                    "fetched_at": state.fetched_at,
                    "source": state.source,
                }
            return _fx_state_from_cache(_cad_usd_rate_cache)
        finally:
            _fx_refresh_inflight = False
            _fx_cond.notify_all()


def _release_fx_refresh() -> None:
    """Clear the single-flight flag and wake followers (leader aborted)."""
    global _fx_refresh_inflight
    with _fx_cond:
        _fx_refresh_inflight = False
        _fx_cond.notify_all()


def get_cad_usd_rate() -> float:
    """Legacy accessor: thin wrapper over ``_resolve_cad_usd_state`` returning only the
    rate VALUE.

    While no genuine rate has ever been fetched this is the placeholder constant, so
    the return value alone says nothing about provenance. Anything that values a
    position or serializes a response must use ``resolve_fx_snapshot()`` instead,
    which reports such a cache as ``unknown`` (rate ``None``)."""
    rate = _resolve_cad_usd_state().rate
    return float(rate if rate is not None else _CAD_USD_FALLBACK)


def _usable_rate(value) -> float | None:
    """A valid FX rate is a finite, strictly positive number (mirrors ``toRateOrNull``
    in ``frontend/src/lib/portfolio-valuation.ts``)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rate) or rate <= 0:
        return None
    return rate


def _usable_price(value) -> float | None:
    """Valuation-boundary price filter (mirrors ``toPriceOrNull`` in
    ``frontend/src/lib/portfolio-valuation.ts``): only a finite, NON-NEGATIVE number is
    a price — a genuine 0 survives; NaN/inf/negative/non-numeric are 'unavailable'."""
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price < 0:
        return None
    return price


def _usable_prev_close(value) -> float | None:
    """Daily-basis filter: the previous close is the canonical daily P&L input and is
    usable only as a finite, STRICTLY POSITIVE number (it is a divisor). Absent /
    non-finite / ``<= 0`` => ``None`` and daily P&L is unknown, never invented.
    Unlike ``_usable_price`` a zero is NOT usable here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        prev = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(prev) or prev <= 0:
        return None
    return prev


def _finite_or_none(value) -> float | None:
    """Serialization guard for informational quote fields (``change_pct``): a finite
    number or ``None`` — NaN/inf must never leak into a JSON response."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _snapshot_from_state(state: _FxCacheState) -> FxSnapshot:
    """Derive the request snapshot from ONE cache state object (no global reads)."""
    current = _usable_rate(state.rate)
    if current is None:
        return FX_UNKNOWN
    if state.known:
        return FxSnapshot(status="known", rate=current, as_of=state.fetched_at, source=state.source)
    if state.genuine:
        return FxSnapshot(status="last_known", rate=current, as_of=state.fetched_at, source=state.source)
    return FX_UNKNOWN


def resolve_fx_snapshot() -> FxSnapshot:
    """Resolve the CAD→USD rate for ONE request.

    Calls ``_resolve_cad_usd_state()`` exactly once — the bounded refresh (TTL /
    backoff / single-flight) happens there — and builds the snapshot from the ONE
    frozen state object it returns. No other global is read, so positions, totals
    and the advertised rate can never come from different cache versions. A failed
    refresh is never reported as ``known``: a retained real-but-expired rate is
    ``last_known``, a never-fetched cache is ``unknown`` (rate ``None``)."""
    return _snapshot_from_state(_resolve_cad_usd_state())


def _coerce_fx(fx: "FxSnapshot | float | None") -> FxSnapshot:
    """Accept the snapshot (the contract) or, for legacy callers, a bare CAD→USD
    number they vouch for (treated as ``known`` with no provenance) / ``None``
    (unknown). A legacy ``None`` never falls back to the module cache: no downstream
    function may re-derive FX from a global."""
    if isinstance(fx, FxSnapshot):
        return fx
    if fx is None:
        return FX_UNKNOWN
    rate = _usable_rate(fx)
    if rate is None:
        return FX_UNKNOWN
    return FxSnapshot(status="known", rate=rate, as_of=None, source=None)


def fx_rate_for_market(market: str | None, fx: "FxSnapshot | float | None") -> float | None:
    """Conversion factor from the market's trading currency into USD (the base), taken
    ONLY from the request's ``fx`` snapshot.

    US/USD markets (and every other enabled market) are always 1.0. A CA position
    converts at ``fx.rate`` — present for ``known`` and ``last_known``, ``None`` for
    ``unknown``. Returns ``None`` when the rate is unknown; nothing is ever invented.
    This mirrors the client contract in ``frontend/src/lib/portfolio-valuation.ts``:
    an unknown FX rate yields null USD figures while native cost/market value survive.
    Callers decide separately whether the market is enabled at all.
    """
    code = (market or "").strip().upper()
    if code == "CA":
        return _coerce_fx(fx).rate
    return 1.0


def exchange_rates_payload(fx: "FxSnapshot | float | None") -> dict:
    """Rates block returned by the portfolio endpoints, serialized from the SAME
    snapshot that valued the positions (no second fetch, no re-read of the cache).

    Contract: ``CAD_USD`` is the Canadian rate (base currency is USD, so US needs no
    entry) and is ``null`` whenever ``fx_status.CAD_USD`` is ``"unknown"`` — a number
    is never supplied without provenance. ``fx_status`` / ``fx_as_of`` / ``fx_source``
    are per-currency dicts: status is ``known`` | ``last_known`` | ``unknown``;
    ``as_of`` is the epoch-seconds time of the fetch that produced the rate; ``source``
    its provider label. A ``last_known`` rate is real but expired and must be shown as
    such, never as fresh."""
    snap = _coerce_fx(fx)
    return {
        "base_currency": BASE_CURRENCY,
        "CAD_USD": snap.rate,
        "fx_status": {"CAD_USD": snap.status},
        "fx_as_of": {"CAD_USD": snap.as_of},
        "fx_source": {"CAD_USD": snap.source},
    }


# ========== Pydantic Models ==========

class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    enabled: bool

    class Config:
        from_attributes = True


class PositionCreate(BaseModel):
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str | None = None  # short: 短线, swing: 波段, long: 长线


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    sort_order: int
    trading_style: str | None
    # 关联信息
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    class Config:
        from_attributes = True


class PositionReorderItem(BaseModel):
    id: int
    sort_order: int


class PositionReorderRequest(BaseModel):
    items: list[PositionReorderItem]


# ========== Account Endpoints ==========

@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(db: Session = Depends(get_db)):
    """获取所有账户"""
    return db.query(Account).order_by(Account.id).all()


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """获取单个账户"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")
    return account


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    """创建账户"""
    account = Account(name=data.name, available_funds=data.available_funds)
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"创建账户: {account.name}")
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    """更新账户"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    if data.name is not None:
        account.name = data.name
    if data.available_funds is not None:
        account.available_funds = data.available_funds
    if data.enabled is not None:
        account.enabled = data.enabled

    db.commit()
    db.refresh(account)
    logger.info(f"更新账户: {account.name}")
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """删除账户（会同时删除该账户的所有持仓）"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    db.delete(account)
    db.commit()
    logger.info(f"删除账户: {account.name}")
    return {"success": True}


# ========== Position Endpoints ==========

@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    account_id: int | None = None,
    stock_id: int | None = None,
    db: Session = Depends(get_db)
):
    """获取持仓列表，可按账户或股票筛选"""
    query = db.query(Position)
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)

    positions = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()).all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "sort_order": pos.sort_order or 0,
            "trading_style": pos.trading_style,
            "account_name": pos.account.name if pos.account else None,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
        })
    return result


@router.post("/positions", response_model=PositionResponse)
def create_position(data: PositionCreate, db: Session = Depends(get_db)):
    """创建持仓"""
    # 检查账户和股票是否存在
    account = db.query(Account).filter(Account.id == data.account_id).first()
    if not account:
        raise HTTPException(400, "Account not found")

    stock = db.query(Stock).filter(Stock.id == data.stock_id).first()
    if not stock:
        raise HTTPException(400, "Stock not found")

    # 检查是否已存在该账户的该股票持仓
    existing = db.query(Position).filter(
        Position.account_id == data.account_id,
        Position.stock_id == data.stock_id,
    ).first()
    if existing:
        raise HTTPException(400, f"Account {account.name} already has a position in {stock.name}. Edit the existing position instead.")

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == data.account_id
    ).scalar() or 0

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        sort_order=int(max_order) + 1,
        trading_style=data.trading_style,
    )
    db.add(position)
    db.commit()
    db.refresh(position)

    logger.info(f"创建持仓: {account.name} - {stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db)):
    """更新持仓"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "Position not found")

    if data.cost_price is not None:
        position.cost_price = data.cost_price
    if data.quantity is not None:
        position.quantity = data.quantity
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
    if data.trading_style is not None:
        # 空字符串表示清空，设为 None
        position.trading_style = data.trading_style if data.trading_style else None

    db.commit()
    db.refresh(position)

    logger.info(f"更新持仓: {position.account.name} - {position.stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db)):
    """删除持仓"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "Position not found")

    db.delete(position)
    db.commit()
    logger.info(f"删除持仓: {position.account.name} - {position.stock.name}")
    return {"success": True}


@router.put("/positions/reorder/batch")
def reorder_positions(data: PositionReorderRequest, db: Session = Depends(get_db)):
    """批量更新持仓排序"""
    if not data.items:
        return {"updated": 0}
    ids = [int(x.id) for x in data.items]
    rows = db.query(Position).filter(Position.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in data.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


# ========== Portfolio Summary ==========

def _pnl_basis(complete: bool, unpriced: int) -> str:
    """Shared basis label (same enum as the client): ``complete`` when every position
    is USD-valued with a KNOWN rate; ``priced_subset`` when some position is not
    USD-valued at all; ``last_known`` when everything is USD-valued but at least one
    conversion used a real-but-expired (``last_known``) rate."""
    if complete:
        return "complete"
    return "priced_subset" if unpriced > 0 else "last_known"


@router.get("/portfolio/summary")
def get_portfolio_summary(
    account_id: int | None = None,
    include_quotes: bool = True,
    db: Session = Depends(get_db),
):
    """
    获取持仓汇总信息

    Args:
        account_id: 可选，指定账户ID。不指定则汇总所有账户

    Returns:
        accounts: 账户列表及各账户持仓明细
        total: 所有账户汇总

    Valuation contract (see docs/product/REVIEW-03.md; identical to the client in
    ``frontend/src/lib/portfolio-valuation.ts``): a position is USD-VALUED iff its
    market is enabled AND it has a finite, non-negative price AND an FX rate is
    available for its market (US is always 1.0; CA needs a ``known`` or
    ``last_known`` CAD→USD rate from this request's single ``FxSnapshot``). Only
    USD-valued positions contribute to ``priced_cost_basis``, the USD
    ``total_market_value`` and ``total_pnl``; ``priced_positions`` counts exactly
    them (``quoted_positions`` counts native quotes). A position with no price
    contributes to cost/coverage accounting but NEVER to market value or P&L, so
    ``total_pnl``/``total_pnl_pct``/``total_assets`` are ``None`` (never an invented
    0) when nothing is USD-valued, and are the PRICED-SUBSET result whenever any
    position lacks a price/rate. ``valuation_complete`` is true only when EVERY
    position is USD-valued at a ``known`` rate — a ``last_known`` rate always leaves
    it false. Every "is this present" check below is ``is not None`` — never bare
    truthiness — so a genuine 0.0 survives instead of being erased to null. A
    position whose market is not in ``ENABLED_MARKETS`` (retired/unsupported, e.g.
    legacy CN rows) never gets an invented 1:1 FX conversion: its USD cost is None
    and it never enters a USD aggregate, though its native cost is always retained.

    Daily basis: ``prev_close`` (finite and > 0, else ``None``) is the CANONICAL daily
    input and is emitted per position AND in the serialized ``quotes`` map, so the
    client computes daily P&L from it directly and never reconstructs it from a
    rounded ``change_pct``. ``daily_pnl`` is ``None`` only when ``prev_close`` is
    unusable (or the position is not USD-valued); a genuine zero current price with a
    usable ``prev_close`` is a valid -100% day. ``daily_pnl_complete`` is true only
    when every position produced a daily P&L.
    """
    # 获取账户
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.enabled == True).all()
    else:
        accounts = db.query(Account).filter(Account.enabled == True).all()

    # FX resolved ONCE for this request (after the bounded refresh) and threaded through
    # every valuation and the serialized rates block below. Returned even with no
    # accounts so the response shape stays constant.
    fx = resolve_fx_snapshot()

    if not accounts:
        empty_coverage = {
            "total_positions": 0,
            "quoted_positions": 0,
            "priced_positions": 0,
            "unpriced_positions": 0,
            "unsupported_positions": 0,
            "unavailable_positions": 0,
            "fresh_positions": 0,
            "last_known_positions": 0,
            "fx_unknown_positions": 0,
            "daily_pnl_positions": 0,
            "daily_pnl_complete": True,
            "unpriced_cost": 0.0,
            "valuation_complete": True,
            "pnl_basis": "complete",
        }
        return {
            "accounts": [],
            "total": {
                "total_market_value": 0.0,
                "priced_cost_basis": 0.0,
                "total_pnl": None,  # no priced positions at all: unknown, never invented
                "total_pnl_pct": None,
                "total_cost": 0.0,
                "cost_basis_complete": True,
                "available_funds": 0.0,
                "total_assets": 0.0,  # no accounts/positions at all: 0 market value + 0 funds, a known number
                "total_assets_complete": True,
                "market_scope": list(ENABLED_MARKETS),
                **empty_coverage,
            },
            "exchange_rates": exchange_rates_payload(fx),
            "quotes": {},
        }

    # 获取所有相关股票
    all_stock_ids = set()
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}

    # 获取实时行情（可选）。仅对启用市场抓取；未启用/退役市场（如遗留 CN 持仓）
    # 永不请求行情，直接落入下面的 "unsupported" 分支。
    quotes = _fetch_quotes_for_stocks(stocks) if include_quotes else {}

    # 计算各账户持仓
    account_summaries = []
    grand_total_market_value = 0.0
    grand_total_cost = 0.0  # USD cost where FX is known (complete-where-known)
    grand_priced_cost_basis = 0.0  # USD cost of USD-VALUED positions only
    grand_available_funds = 0.0
    grand_daily_pnl = 0.0
    grand_daily_pnl_positions = 0
    grand_total_positions = 0
    grand_quoted_positions = 0  # has a usable native quote (FX not required)
    grand_priced_positions = 0  # USD-valued: usable quote AND FX rate available
    grand_fresh_positions = 0  # USD-valued at a KNOWN rate
    grand_last_known_positions = 0  # USD-valued at a real-but-expired rate
    grand_fx_unknown_positions = 0  # quoted, enabled market, but no FX rate
    grand_unsupported_positions = 0
    grand_unavailable_positions = 0
    grand_unpriced_cost = 0.0
    grand_unpriced_cost_known = True
    grand_cost_basis_complete = True

    for acc in accounts:
        positions_data = []
        acc_market_value = 0.0
        acc_total_cost = 0.0  # USD cost where FX known
        acc_priced_cost_basis = 0.0
        acc_daily_pnl = 0.0
        acc_daily_pnl_positions = 0
        acc_total_positions = 0
        acc_quoted_positions = 0
        acc_priced_positions = 0
        acc_fresh_positions = 0
        acc_last_known_positions = 0
        acc_fx_unknown_positions = 0
        acc_unsupported_positions = 0
        acc_unavailable_positions = 0
        acc_unpriced_cost = 0.0
        acc_unpriced_cost_known = True
        acc_cost_basis_complete = True

        positions_sorted = sorted(
            list(acc.positions or []),
            key=lambda p: (int(getattr(p, "sort_order", 0) or 0), int(p.id)),
        )
        for pos in positions_sorted:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            market_enabled = is_enabled(stock.market)
            quote = quotes.get((stock.market, stock.symbol)) if market_enabled else None
            # Valuation boundary: only a finite, non-negative number is a price. A
            # NaN/inf/negative quote is treated exactly like a missing one.
            current_price = _usable_price(quote.get("current_price")) if quote else None
            quoted = current_price is not None
            change_pct = _finite_or_none(quote.get("change_pct")) if (quote and quoted) else None
            # Daily basis: ``prev_close`` is the CANONICAL daily input (finite and > 0,
            # else None). It is emitted per position and in the ``quotes`` map so the
            # client never has to reconstruct it from a rounded ``change_pct``.
            prev_close = _usable_prev_close(quote.get("prev_close")) if quote else None

            # FX into USD (base currency) from this request's single snapshot.
            # Unknown rate / unsupported market => no conversion is invented; the
            # USD-denominated fields stay None and this position never enters a USD
            # aggregate. Native cost is always retained regardless.
            is_foreign = (stock.market or "").upper() == "CA"
            rate = fx_rate_for_market(stock.market, fx) if market_enabled else None
            if not market_enabled:
                pos_fx_status = "unknown"
            elif is_foreign:
                pos_fx_status = fx.status
            else:
                pos_fx_status = "known"

            # USD-valued: enabled market AND usable price AND FX rate available.
            priced = quoted and rate is not None
            if not market_enabled:
                valuation_status = "unsupported"
            elif priced:
                valuation_status = "priced"
            else:
                valuation_status = "unavailable"

            acc_total_positions += 1
            if quoted:
                acc_quoted_positions += 1
            if priced:
                acc_priced_positions += 1
                if pos_fx_status == "known":
                    acc_fresh_positions += 1
                else:
                    acc_last_known_positions += 1
            elif market_enabled:
                acc_unavailable_positions += 1
                if quoted:
                    acc_fx_unknown_positions += 1
            else:
                acc_unsupported_positions += 1

            cost = pos.cost_price * pos.quantity  # native currency, always present
            cost_usd = cost * rate if rate is not None else None  # None when FX unknown

            if cost_usd is not None:
                acc_total_cost += cost_usd
            else:
                acc_cost_basis_complete = False

            # Native-currency market value needs no FX, so it survives even when the
            # rate is unknown (mirrors portfolio-valuation.ts). Only the USD figures
            # below require a rate.
            market_value = current_price * pos.quantity if quoted else None
            market_value_usd = None
            current_price_usd = None
            pnl = None
            pnl_pct = None
            daily_pnl = None
            daily_pnl_pct = None

            if priced:
                market_value_usd = market_value * rate
                current_price_usd = current_price * rate
                pnl = market_value_usd - cost_usd
                pnl_pct = (pnl / cost_usd * 100) if cost_usd > 0 else None
                acc_priced_cost_basis += cost_usd
                acc_market_value += market_value_usd
                # A genuine zero current price is VALID daily data: 0 vs prev_close 100
                # is a -100% day, not "unknown". Only a missing/unusable prev_close
                # leaves daily P&L None.
                if prev_close is not None:
                    daily_pnl = (current_price - prev_close) * pos.quantity * rate
                    daily_pnl_pct = (current_price - prev_close) / prev_close * 100
                    acc_daily_pnl += daily_pnl
                    acc_daily_pnl_positions += 1
            else:
                # Not USD-valued: contributes to unpriced-cost coverage, never to
                # market value/P&L.
                if cost_usd is not None:
                    acc_unpriced_cost += cost_usd
                else:
                    acc_unpriced_cost_known = False

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "sort_order": pos.sort_order or 0,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "current_price_cny": round(current_price_usd, 2) if current_price_usd is not None else None,
                "change_pct": change_pct,
                # Canonical daily basis (native currency): finite > 0, else None.
                "prev_close": prev_close,
                "cost": round(cost, 2),
                # cost_usd is the accurate name for this USD-converted cost; cost_cny is kept
                # alongside it only for API compatibility with market_value_cny/current_price_cny.
                "cost_cny": round(cost_usd, 2) if cost_usd is not None else None,
                "cost_usd": round(cost_usd, 2) if cost_usd is not None else None,
                "market_value": round(market_value, 2) if market_value is not None else None,
                "market_value_cny": round(market_value_usd, 2) if market_value_usd is not None else None,
                "pnl": round(pnl, 2) if pnl is not None else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None,
                "exchange_rate": rate if (is_foreign and rate is not None) else None,
                # `priced` means USD-VALUED (price AND rate), matching `priced_positions`;
                # `quoted` is the weaker "has a usable native quote".
                "priced": priced,
                "quoted": quoted,
                "valuation_status": valuation_status,
                # This request's own quote fetch either had a usable price ("fresh") or
                # didn't ("unavailable") — the server has no notion of a client-retained
                # stale price, so "last_known" is never emitted here; it's a client-only
                # price state. FX staleness is carried by `fx_status` instead.
                "price_status": "fresh" if quoted else "unavailable",
                "fx_status": pos_fx_status,
            })

        acc_unpriced_positions = acc_total_positions - acc_priced_positions
        # Complete only when EVERY position is USD-valued at a KNOWN rate. A position
        # that is quoted but whose FX rate is unknown is excluded from the basis, and a
        # last_known rate values it but can never make the valuation complete.
        acc_valuation_complete = acc_fresh_positions == acc_total_positions
        if acc_priced_positions > 0:
            acc_pnl = acc_market_value - acc_priced_cost_basis
            acc_pnl_pct = (acc_pnl / acc_priced_cost_basis * 100) if acc_priced_cost_basis > 0 else None
        else:
            # Nothing USD-valued: any full-portfolio P&L figure would be
            # invented, so render unknown rather than a false 0.
            acc_pnl = None
            acc_pnl_pct = None
        if acc_priced_positions > 0 or acc_total_positions == 0:
            # Either some held position is USD-valued, or there are no positions
            # at all (acc_market_value is then 0.0) — either way total assets
            # is a genuinely known number, never an invented placeholder.
            acc_total_assets = acc_market_value + acc.available_funds
        else:
            # There ARE positions but none of them are USD-valued: assets are
            # unknown, not a false 0.
            acc_total_assets = None
        # Daily P&L is unknown (never an invented 0) when holdings exist but none
        # of them produced one; an empty account has a genuine 0.
        acc_total_daily_pnl = None if (acc_daily_pnl_positions == 0 and acc_total_positions > 0) else acc_daily_pnl

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "priced_cost_basis": round(acc_priced_cost_basis, 2),
            "total_cost": round(acc_total_cost, 2),
            "cost_basis_complete": acc_cost_basis_complete,
            "total_pnl": round(acc_pnl, 2) if acc_pnl is not None else None,
            "total_pnl_pct": round(acc_pnl_pct, 2) if acc_pnl_pct is not None else None,
            "total_daily_pnl": round(acc_total_daily_pnl, 2) if acc_total_daily_pnl is not None else None,
            "total_assets": round(acc_total_assets, 2) if acc_total_assets is not None else None,
            "total_assets_complete": acc_valuation_complete,
            "total_positions": acc_total_positions,
            "quoted_positions": acc_quoted_positions,
            "priced_positions": acc_priced_positions,
            "unpriced_positions": acc_unpriced_positions,
            "unsupported_positions": acc_unsupported_positions,
            "unavailable_positions": acc_unavailable_positions,
            "fresh_positions": acc_fresh_positions,
            "last_known_positions": acc_last_known_positions,
            "fx_unknown_positions": acc_fx_unknown_positions,
            "daily_pnl_positions": acc_daily_pnl_positions,
            "daily_pnl_complete": acc_daily_pnl_positions == acc_total_positions,
            "unpriced_cost": round(acc_unpriced_cost, 2) if acc_unpriced_cost_known else None,
            "valuation_complete": acc_valuation_complete,
            "pnl_basis": _pnl_basis(acc_valuation_complete, acc_unpriced_positions),
            "positions": positions_data,
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_total_cost
        grand_priced_cost_basis += acc_priced_cost_basis
        grand_available_funds += acc.available_funds
        grand_daily_pnl += acc_daily_pnl
        grand_daily_pnl_positions += acc_daily_pnl_positions
        grand_total_positions += acc_total_positions
        grand_quoted_positions += acc_quoted_positions
        grand_priced_positions += acc_priced_positions
        grand_fresh_positions += acc_fresh_positions
        grand_last_known_positions += acc_last_known_positions
        grand_fx_unknown_positions += acc_fx_unknown_positions
        grand_unsupported_positions += acc_unsupported_positions
        grand_unavailable_positions += acc_unavailable_positions
        if acc_unpriced_cost_known:
            grand_unpriced_cost += acc_unpriced_cost
        else:
            grand_unpriced_cost_known = False
        if not acc_cost_basis_complete:
            grand_cost_basis_complete = False

    grand_unpriced_positions = grand_total_positions - grand_priced_positions
    # Same rule as per-account: complete only when every position is USD-valued at a
    # KNOWN rate, never merely 'nothing unpriced'.
    grand_valuation_complete = grand_fresh_positions == grand_total_positions
    if grand_priced_positions > 0:
        grand_pnl = grand_total_market_value - grand_priced_cost_basis
        grand_pnl_pct = (grand_pnl / grand_priced_cost_basis * 100) if grand_priced_cost_basis > 0 else None
    else:
        grand_pnl = None
        grand_pnl_pct = None
    if grand_priced_positions > 0 or grand_total_positions == 0:
        # Some held position is USD-valued, or no positions at all anywhere (grand
        # market value is then 0.0) — total assets is genuinely known.
        grand_total_assets = grand_total_market_value + grand_available_funds
    else:
        # Positions exist but none are USD-valued: assets unknown, not a false 0.
        grand_total_assets = None
    grand_total_daily_pnl = (
        None if (grand_daily_pnl_positions == 0 and grand_total_positions > 0) else grand_daily_pnl
    )

    # 构建 quotes 字典（用于前端股票列表显示），键为 "MARKET:SYMBOL"（与前端
    # `quotes[`${pos.market}:${pos.symbol}`]` 的读取方式对齐，避免同 symbol
    # 跨市场撞车覆盖）。价格经过同一道估值边界过滤(非有限/负数 => null)。
    # ``prev_close`` is carried authoritatively (finite > 0, else null) as the
    # canonical daily-P&L input; ``change_pct`` is informational only (finite or null)
    # and must never be used to reconstruct the previous close.
    quotes_dict = {}
    if include_quotes:
        for (market, symbol), quote in quotes.items():
            quotes_dict[f"{market}:{symbol}"] = {
                "current_price": _usable_price(quote.get("current_price")),
                "change_pct": _finite_or_none(quote.get("change_pct")),
                "prev_close": _usable_prev_close(quote.get("prev_close")),
            }

    return {
        "accounts": account_summaries,
        "total": {
            "total_market_value": round(grand_total_market_value, 2),
            "priced_cost_basis": round(grand_priced_cost_basis, 2),
            "total_cost": round(grand_total_cost, 2),
            "cost_basis_complete": grand_cost_basis_complete,
            "total_pnl": round(grand_pnl, 2) if grand_pnl is not None else None,
            "total_pnl_pct": round(grand_pnl_pct, 2) if grand_pnl_pct is not None else None,
            "total_daily_pnl": round(grand_total_daily_pnl, 2) if grand_total_daily_pnl is not None else None,
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2) if grand_total_assets is not None else None,
            "total_assets_complete": grand_valuation_complete,
            "total_positions": grand_total_positions,
            "quoted_positions": grand_quoted_positions,
            "priced_positions": grand_priced_positions,
            "unpriced_positions": grand_unpriced_positions,
            "unsupported_positions": grand_unsupported_positions,
            "unavailable_positions": grand_unavailable_positions,
            "fresh_positions": grand_fresh_positions,
            "last_known_positions": grand_last_known_positions,
            "fx_unknown_positions": grand_fx_unknown_positions,
            "daily_pnl_positions": grand_daily_pnl_positions,
            "daily_pnl_complete": grand_daily_pnl_positions == grand_total_positions,
            "unpriced_cost": round(grand_unpriced_cost, 2) if grand_unpriced_cost_known else None,
            "valuation_complete": grand_valuation_complete,
            "pnl_basis": _pnl_basis(grand_valuation_complete, grand_unpriced_positions),
            # Server-authoritative enabled-market list, so the client never has to
            # hardcode/guess which markets this deployment actually supports.
            "market_scope": list(ENABLED_MARKETS),
        },
        # Serialized from the SAME snapshot that valued every position above.
        "exchange_rates": exchange_rates_payload(fx),
        "quotes": quotes_dict,  # 可选：返回行情数据
    }


def _fetch_quotes_for_stocks(stocks: list[Stock]) -> dict:
    """获取股票列表的实时行情，键为 (market, symbol) 二元组，避免不同市场共用
    同一 symbol 时互相覆盖。仅对启用市场（``is_enabled``）发起抓取；未启用/
    退役市场（如遗留 CN/HK 持仓）从不触发行情供应商调用。"""
    if not stocks:
        return {}

    # 按市场分组
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        if not is_enabled(market):
            continue
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]
        try:
            items = md_quote_rows(symbols, market_code.value)
            for item in items:
                quotes[(market, item["symbol"])] = item
        except Exception as e:
            logger.error(f"获取 {market} 行情失败: {e}")

    return quotes


# 组合基准/归因结果缓存:重建全持仓 NAV 很贵(逐只拉 K 线),按持仓指纹缓存结果。
# 持仓变动即失效(指纹变);失败/空结果不缓存,避免把瞬时故障冻住 10 分钟。
_PORTFOLIO_RESULT_CACHE = TTLCache(default_ttl_sec=600.0)


def _holdings_signature(db: Session) -> str:
    """启用账户持仓的稳定指纹(stock_id + 合并后数量);仅查 DB,不拉行情/K 线。"""
    rows = (
        db.query(Position.stock_id, Position.quantity)
        .join(Account, Account.id == Position.account_id)
        .filter(Account.enabled == True)  # noqa: E712
        .all()
    )
    agg: dict[int, float] = {}
    for sid, qty in rows:
        agg[sid] = agg.get(sid, 0.0) + (qty or 0)
    return ";".join(f"{sid}:{agg[sid]:g}" for sid in sorted(agg))


def _valuation_coverage(analysed: int, excluded: int, fx_stale: bool = False) -> dict:
    """Additive ``valuation`` coverage block shared by the four analytics endpoints
    (REVIEW-04 §A). States how many distinct held instruments actually entered the
    analysis versus were excluded for lacking a price/FX rate, so a partial result
    can never be silently mistaken for a complete one. ``fx_stale`` marks that at
    least one analysed holding was converted at a ``last_known`` (real but expired)
    CAD→USD rate: the figures are usable but the valuation is NOT complete."""
    complete = excluded == 0 and not fx_stale
    block = {
        "analysed_holdings": analysed,
        "excluded_unpriced_holdings": excluded,
        "valuation_complete": complete,
        "basis": _pnl_basis(complete, excluded),
    }
    if fx_stale:
        block["fx_status"] = "last_known"
    notes = []
    if excluded:
        notes.append(
            "Excluded holdings are not valued at cost and not counted as zero; their "
            "quantity and cost basis are unchanged in the underlying portfolio. Figures "
            "above reflect only the priced subset."
        )
    if fx_stale:
        notes.append(
            "Canadian holdings were converted at the last known CAD/USD rate, which has "
            "expired and could not be refreshed; treat USD figures as approximate."
        )
    if notes:
        block["note"] = " ".join(notes)
    return block


def _coverage_fingerprint(holdings: list[dict]) -> str:
    """Identity fingerprint of the exact priced holdings that entered analysis (by
    market:symbol). This is deliberately distinct from ``_holdings_signature`` (which
    fingerprints ALL DB position rows, priced or not): it changes whenever WHICH
    holdings are actually priced changes — e.g. an FX outage clears, or a quote that
    was missing comes back — even when the underlying position rows are untouched.
    Folding it into the cache key means a cached result can never be served with
    coverage that doesn't match the holdings it was actually computed from. (The
    endpoints additionally fold the coverage's FX marker into the key, so a result
    computed at a known rate is never served as complete once the rate goes stale.)"""
    return ",".join(sorted(f"{h['market']}:{h['symbol']}" for h in holdings))


def _needs_fx(stocks: list[Stock]) -> bool:
    """Whether any held instrument is priced in a non-USD currency (currently only the
    CA market), i.e. whether a CAD→USD rate is required to value the set at all."""
    return any((s.market or "").strip().upper() == "CA" for s in stocks)


def _gather_holdings(db: Session) -> tuple[list[dict], dict]:
    """汇总所有启用账户的真实持仓为统一列表(USD 市值/浮盈 + fx),多账户同股合并。

    Returns ``(holdings, coverage)`` computed from the SAME query/snapshot — an
    explicit, request-local return contract (never a module-global side channel,
    which would race across concurrent requests). ``coverage`` is the additive
    ``valuation`` block (see ``_valuation_coverage``) describing how many distinct
    held instruments were analysed vs. excluded for lacking a price/FX rate. The FX
    rate is the same request-local ``FxSnapshot`` the summary endpoint uses (resolved
    once, after the bounded refresh), so a cold-but-recoverable rate is recovered here
    too rather than silently excluding every Canadian holding.
    """
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    stock_ids = {p.stock_id for acc in accounts for p in acc.positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    # FX is only needed when a non-USD (CA) instrument is held; a USD-only portfolio
    # never touches the rate source (US always converts at 1.0 regardless of ``fx``).
    fx = resolve_fx_snapshot() if _needs_fx(stocks) else FX_UNKNOWN

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    priced_keys: set[tuple[str, str]] = set()
    unpriced_keys: set[tuple[str, str]] = set()
    fx_stale_used = False
    for acc in accounts:
        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue
            # Only USD-valued holdings (enabled market, finite non-negative price, FX
            # rate available) can be analysed. An unpriced holding must NOT be valued
            # at its cost (that invents a flat valuation and a 0.00 P&L from an absent
            # price); it is excluded from analysis and reported through the coverage
            # block instead.
            market_enabled = is_enabled(stock.market)
            rate = fx_rate_for_market(stock.market, fx) if market_enabled else None
            key = (stock.market, stock.symbol)
            quote = quotes.get(key) if market_enabled else None
            price = _usable_price(quote.get("current_price")) if quote else None
            if price is None or rate is None:
                unpriced_keys.add(key)
                continue
            priced_keys.add(key)
            if (stock.market or "").upper() == "CA" and fx.status == "last_known":
                fx_stale_used = True
            cost_usd = pos.cost_price * pos.quantity * rate
            mv_usd = price * pos.quantity * rate
            pnl_usd = mv_usd - cost_usd
            if key in seen:  # 多账户同一标的合并
                h = seen[key]
                h["quantity"] += pos.quantity
                h["market_value"] += mv_usd
                h["unrealized_pnl"] += pnl_usd
            else:
                h = {
                    "symbol": stock.symbol,
                    "market": stock.market,
                    "name": stock.name,
                    "quantity": pos.quantity,
                    "fx": rate,
                    "market_value": mv_usd,
                    "unrealized_pnl": pnl_usd,
                    "strategy_code": pos.trading_style or "",
                }
                seen[key] = h
                out.append(h)
    coverage = _valuation_coverage(len(priced_keys), len(unpriced_keys), fx_stale=fx_stale_used)
    return out, coverage


@router.get("/portfolio/diagnostics")
def portfolio_diagnostics(db: Session = Depends(get_db)):
    """真实持仓组合诊断:集中度(HHI)/最大单仓/市场分布/风险提示(只读)。"""
    from src.core.portfolio_diagnostics import diagnose_positions

    holdings, coverage = _gather_holdings(db)
    result = diagnose_positions(holdings)
    result["valuation"] = coverage
    return result


@router.get("/portfolio/benchmark")
def portfolio_benchmark(
    days: int = 60, benchmark: str = "", db: Session = Depends(get_db)
):
    """真实持仓组合 vs 基准:超额收益/信息比率/相对回撤 + 归一化净值曲线。"""
    from src.core.portfolio_benchmark import (
        DEFAULT_BENCHMARK,
        build_portfolio_benchmark,
    )

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        # No positions at all in any enabled account: genuinely empty, not merely
        # unpriced. Cheap DB-only bail — skips the quote fetch below.
        return {"empty": True, "reason": "no_holdings", "valuation": _valuation_coverage(0, 0)}

    # Coverage depends on live price/FX availability, which `sig` (position rows
    # only) cannot see — so holdings/coverage are gathered BEFORE the cache lookup
    # on every call, cache hit or miss. See `_coverage_fingerprint` for why.
    holdings, coverage = _gather_holdings(db)
    if not holdings:
        # Positions exist but none are currently priced: this is NOT "no holdings" —
        # that would silently imply nothing to show, when in fact everything held is
        # excluded from analysis. Report it as such via the coverage block.
        return {"empty": True, "reason": "all_unpriced", "valuation": coverage}

    fx_marker = coverage.get("fx_status", "known")
    ckey = f"bench:{days}:{bcode}:{sig}:{_coverage_fingerprint(holdings)}:{fx_marker}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    res = build_portfolio_benchmark(holdings, days=days, benchmark_code=bcode)
    if not res:
        # 失败/数据不足不缓存,下轮可重试(由 K 线负缓存兜住打爆)
        return {"empty": True, "reason": "insufficient_data", "valuation": coverage}
    res = {**res, "valuation": coverage}
    _PORTFOLIO_RESULT_CACHE.set(ckey, res)
    return res


@router.get("/portfolio/todos")
def portfolio_todos(db: Session = Depends(get_db)):
    """首页空态待办:持仓但未设提醒 / 提醒即将到期(可行动,盘后也不空)。"""
    todos: list[dict] = []
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    held_ids = {p.stock_id for acc in accounts for p in acc.positions}
    if held_ids:
        ruled = {
            r.stock_id
            for r in db.query(PriceAlertRule)
            .filter(PriceAlertRule.enabled == True, PriceAlertRule.stock_id.in_(held_ids))  # noqa: E712
            .all()
        }
        for sid in held_ids - ruled:
            stock = db.query(Stock).filter(Stock.id == sid).first()
            if stock:
                todos.append(
                    {
                        "type": "no_alert",
                        "symbol": stock.symbol,
                        "market": stock.market,
                        "message": f"{stock.name} is held but has no price alert set",
                    }
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    expiring = (
        db.query(PriceAlertRule)
        .filter(
            PriceAlertRule.enabled == True,  # noqa: E712
            PriceAlertRule.expire_at.isnot(None),
            PriceAlertRule.expire_at >= now,
            PriceAlertRule.expire_at <= soon,
        )
        .all()
    )
    for r in expiring:
        stock = db.query(Stock).filter(Stock.id == r.stock_id).first()
        todos.append(
            {
                "type": "alert_expiring",
                "symbol": stock.symbol if stock else "",
                "market": stock.market if stock else default_market(),
                "message": f"{(r.name or 'Alert')} is expiring soon",
            }
        )

    return {"todos": todos[:10], "count": len(todos)}


@router.get("/portfolio/attribution")
def portfolio_attribution(days: int = 60, benchmark: str = "", db: Session = Depends(get_db)):
    """近 days 日各持仓对组合收益的贡献(谁拖累/贡献),降序。"""
    from src.core.portfolio_benchmark import DEFAULT_BENCHMARK, build_attribution

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"items": [], "valuation": _valuation_coverage(0, 0)}

    holdings, coverage = _gather_holdings(db)
    if not holdings:
        return {"items": [], "valuation": coverage}

    fx_marker = coverage.get("fx_status", "known")
    ckey = f"attr:{days}:{bcode}:{sig}:{_coverage_fingerprint(holdings)}:{fx_marker}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    items = build_attribution(holdings, days=days, benchmark_code=bcode)
    result = {"items": items, "valuation": coverage}
    if items:  # 空结果不缓存,下轮可重试
        _PORTFOLIO_RESULT_CACHE.set(ckey, result)
    return result


@router.post("/portfolio/ai-review")
async def portfolio_ai_review(model_id: int | None = None, db: Session = Depends(get_db)):
    """组合 AI 体检:诊断+基准+归因 → 叙述结论 + 调仓建议(只读,不下单)。"""
    from src.core.portfolio_benchmark import build_attribution, build_portfolio_benchmark
    from src.core.portfolio_diagnostics import diagnose_positions
    from src.web.api.chat import _get_ai_client

    holdings, coverage = _gather_holdings(db)
    if not holdings:
        reason = "all_unpriced" if coverage["excluded_unpriced_holdings"] else "no_holdings"
        return {"empty": True, "reason": reason, "valuation": coverage}

    diag = diagnose_positions(holdings)
    bench = build_portfolio_benchmark(holdings, days=60) or {}
    attr = build_attribution(holdings, days=60)
    top = attr[:3]
    worst = list(reversed(attr[-3:])) if len(attr) > 3 else []

    lines = [
        f"Holdings {diag['position_count']}, total market value {diag['total_market_value']:.0f}, unrealized P&L {diag['total_unrealized_pnl']:.0f}",
        f"Concentration HHI {diag['hhi']}, largest single position {diag['max_weight'] * 100:.0f}%",
    ]
    # The model must never be told a partial valuation is a complete one. `coverage`
    # comes from the SAME _gather_holdings snapshot as `holdings` itself — no second,
    # independently-queried count that could drift out of sync with it.
    excluded = coverage["excluded_unpriced_holdings"]
    if excluded:
        lines.append(
            f"NOTE: this covers only the priced subset of the portfolio; {excluded} "
            f"holding(s) have no current price and are excluded. Their cost and quantity "
            f"are unchanged; treat the figures above as partial, not a full valuation."
        )
    if bench.get("excess_return") is not None:
        lines.append(
            f"Last 60 days vs {bench.get('benchmark_label', 'benchmark')}: excess return {bench['excess_return']}%"
            f"(portfolio {bench.get('portfolio_return')}% / benchmark {bench.get('benchmark_return')}%),"
            f"relative drawdown {bench.get('relative_drawdown')}%"
        )
    if diag.get("by_market"):
        lines.append("Market distribution: " + ", ".join(f"{k} {v:.0f}" for k, v in diag["by_market"].items()))
    if diag.get("alerts"):
        lines.append("Risk alerts: " + "; ".join(diag["alerts"]))
    if top:
        lines.append("Top contributors: " + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in top))
    if worst:
        lines.append("Biggest drags: " + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in worst))

    system_prompt = (
        "You are a steady, conservative portfolio advisor. Based on the given portfolio diagnostics/benchmark "
        "comparison/stock attribution, give a brief health check + actionable rebalancing suggestions. "
        "Read-only analysis only: no order placement, no return guarantees. Strict format:\n"
        "Assessment: one-line summary\nSuggestions:\n- (2-3 specific, actionable items)\nRisks: one-line biggest risk"
    )
    user_content = "Portfolio overview:\n" + "\n".join(lines)
    try:
        content = await _get_ai_client(db, model_id).chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI health check failed: {e}")

    return {
        "content": content,
        "top": top,
        "worst": worst,
        "diagnostics": diag,
        "benchmark": bench,
        "valuation": coverage,
    }
