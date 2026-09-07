"""Single choke point for every Yahoo Finance access in the package.

Nothing else in ``marketdata`` imports ``yfinance`` or hits ``query1``/``query2`` (the raw
``YahooKlineVendor`` in ``vendors/kline.py`` is the documented last resort and is the only
exception). Every library call goes through :func:`call`, which mirrors ``http.market_get``:
throttle, shared circuit breaker under host key ``"yfinance"``, thread-pool timeout,
``YFRateLimitError`` -> immediate 5 minute trip, any failure -> ``VendorError`` + ``record_error``.
The Data Sources Test button (``capture_errors``) bypasses the breaker exactly as ``market_get`` does.

No ``datetime.now()`` in here: time windows are the caller's job.

Test seams: ``make_ticker`` (module attribute), and the hooks ``ticker_factory``,
``screen_fn``, ``search_fn``, ``download_fn`` (``None`` = real yfinance). See
``tests/_yf_fakes.py`` for ``FakeTicker`` / ``patch_yf``.
"""

from __future__ import annotations

import logging
import math
import os
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime, timezone
from typing import Any, Callable, TypeVar

from marketdata import http
from marketdata.cache import TTLCache
from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import Bar

logger = logging.getLogger(__name__)

T = TypeVar("T")

HOST_KEY = "yfinance"
_THROTTLE_S = 0.15
_RATE_LIMIT_COOLDOWN_S = 300.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Test hooks (None = real yfinance)
# ---------------------------------------------------------------------------
ticker_factory: Callable[[str], Any] | None = None
screen_fn: Callable[..., Any] | None = None
search_fn: Callable[..., Any] | None = None
download_fn: Callable[..., Any] | None = None

_ticker_cache = TTLCache(default_ttl_sec=300.0, max_size=512)
_info_cache = TTLCache(default_ttl_sec=21600.0, max_size=2048)
_executor: ThreadPoolExecutor | None = None
_applied_proxy: str | None = None
_proxy_applied_once = False


# ---------------------------------------------------------------------------
# Library access
# ---------------------------------------------------------------------------

def import_yfinance():
    """Lazy import; ``ImportError`` -> ``VendorError`` so the Engine fails over."""
    try:
        import yfinance as yf  # noqa: WPS433 (the one allowed yfinance import)
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise VendorError("yfinance is not installed (pip install yfinance)") from e
    return yf


def to_yahoo_symbol(sym: Symbol) -> str:
    """Delegates to ``Symbol.to_yfinance`` (BRK.B / BRK_B -> BRK-B, CTC.A.TO -> CTC-A.TO)."""
    return sym.to_yfinance()


def make_ticker(ysym: str):
    """Module-level ticker factory (TEST SEAM): ``yf.Ticker(ysym)`` unless ``ticker_factory`` is set."""
    if ticker_factory is not None:
        return ticker_factory(ysym)
    return import_yfinance().Ticker(ysym)


def get_ticker(sym: Symbol | str):
    """Ticker for ``sym`` with the proxy applied; 300 s object cache keyed by the Yahoo symbol so
    fundamentals, holders, events and dividends share one object (and its internal caches)."""
    ysym = to_yahoo_symbol(sym) if isinstance(sym, Symbol) else str(sym).strip()
    cached = _ticker_cache.get(ysym)
    if cached is not None:
        return cached
    apply_proxy({})
    try:
        t = make_ticker(ysym)
    except VendorError:
        raise
    except Exception as e:
        # Constructing the Ticker is part of the vendor call: surface it the way `call`
        # surfaces a fetch failure so the Engine fails over instead of raising a raw error.
        raise VendorError(f"Yahoo ticker {ysym}: {type(e).__name__}: {e}") from e
    _ticker_cache.set(ysym, t)
    return t


def apply_proxy(config: dict | None) -> None:
    """``yf.set_config(proxy=...)`` from ``config["proxy"]`` or ``HTTPS_PROXY``/``HTTP_PROXY``.
    Memoised: only calls into yfinance when the effective proxy changes (or once, to clear it)."""
    global _applied_proxy, _proxy_applied_once
    proxy = ""
    if config:
        proxy = str(config.get("proxy") or "").strip()
    if not proxy:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy = proxy.strip()
    if _proxy_applied_once and proxy == (_applied_proxy or ""):
        return
    if ticker_factory is not None and not proxy:
        # Tests with a fake ticker never need the library configured.
        _applied_proxy, _proxy_applied_once = proxy, True
        return
    try:
        yf = import_yfinance()
        # yfinance 1.7 moved this to `yf.config.network.proxy`; `set_config` still works
        # but emits a DeprecationWarning, so only fall back to it on older releases.
        cfg = getattr(yf, "config", None)
        network = getattr(cfg, "network", None) if cfg is not None else None
        if network is not None and hasattr(network, "proxy"):
            network.proxy = proxy or None
        elif hasattr(yf, "set_config"):
            yf.set_config(proxy=proxy or None)
    except VendorError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"yfinance proxy configuration failed: {e}")
    _applied_proxy, _proxy_applied_once = proxy, True


def configure(proxy: str | None = None) -> None:
    """Convenience for callers that carry a bare proxy string (discovery, index strip)."""
    apply_proxy({"proxy": proxy or ""})


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf")
    return _executor


def _is_rate_limit(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name == "YFRateLimitError":
        return True
    msg = str(exc).lower()
    return "too many requests" in msg or "rate limit" in msg or " 429" in f" {msg}"


def call(label: str, fn: Callable[[], T], *, symbol: str = "", timeout: float = 15.0) -> T:
    """Run one yfinance call under throttle + breaker + timeout. Raises ``VendorError`` on any failure."""
    probing = http.is_probing()
    tag = f"{label} {symbol}".strip()
    if not probing and http.breaker_blocked(HOST_KEY):
        raise VendorError("Yahoo Finance circuit breaker open")
    http.throttle(HOST_KEY, _THROTTLE_S)
    fut: Future = _pool().submit(fn)
    try:
        result = fut.result(timeout=timeout)
    except FutureTimeoutError:
        msg = f"{tag}: timeout after {timeout}s"
        if not probing:
            http.breaker_failure(HOST_KEY, label)
        http.record_error(msg)
        raise VendorError(msg)
    except Exception as e:
        if _is_rate_limit(e):
            if not probing:
                http.breaker_trip(HOST_KEY, cooldown_s=_RATE_LIMIT_COOLDOWN_S)
            msg = f"{tag}: Yahoo rate limit (429)"
            http.record_error(msg)
            raise VendorError("Yahoo rate limit (429)") from e
        if not probing:
            http.breaker_failure(HOST_KEY, label)
        msg = f"{tag}: {type(e).__name__}: {e}"
        http.record_error(msg)
        raise VendorError(msg) from e
    http.breaker_success(HOST_KEY)
    return result


# ---------------------------------------------------------------------------
# Helpers over the library
# ---------------------------------------------------------------------------

def yahoo_period(days: int) -> str:
    """days -> Yahoo ``period`` enum (no period1/period2, so no dependency on the current time)."""
    if days <= 5:
        return "5d"
    if days <= 22:
        return "1mo"
    if days <= 66:
        return "3mo"
    if days <= 130:
        return "6mo"
    if days <= 260:
        return "1y"
    if days <= 520:
        return "2y"
    if days <= 1300:
        return "5y"
    return "max"


def _is_nan(v: Any) -> bool:
    try:
        return v is None or (isinstance(v, float) and math.isnan(v)) or (hasattr(v, "__float__") and math.isnan(float(v)))
    except (TypeError, ValueError):
        return False


def _f(v: Any) -> float | None:
    if _is_nan(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_utc(ts: Any) -> datetime:
    """pandas Timestamp / datetime / date / epoch (s or ms) / ISO string -> aware UTC; EPOCH on failure."""
    if ts is None:
        return _EPOCH
    try:
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)
        if isinstance(ts, date):
            return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            if _is_nan(ts):
                return _EPOCH
            v = float(ts)
            if v > 1e11:  # milliseconds
                v /= 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(ts).strip()
        if not s:
            return _EPOCH
        if s.isdigit():
            return to_utc(int(s))
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except Exception:
        return _EPOCH


def _to_record_value(v: Any) -> Any:
    if v is None or _is_nan(v):
        return None
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime().strftime("%Y-%m-%d")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalar
        try:
            return v.item()
        except Exception:
            return v
    return v


def df_records(df: Any) -> list[dict]:
    """DataFrame / Series -> list of plain dicts (index included as a column; NaN -> None;
    Timestamps -> ``YYYY-MM-DD``). ``None``/empty -> ``[]``."""
    if df is None:
        return []
    try:
        if hasattr(df, "to_frame") and not hasattr(df, "columns"):
            df = df.to_frame()
        if len(df) == 0:
            return []
        frame = df.reset_index()
        out: list[dict] = []
        for row in frame.to_dict(orient="records"):
            out.append({str(k): _to_record_value(v) for k, v in row.items()})
        return out
    except Exception as e:
        logger.debug(f"df_records failed: {e}")
        return []


def history(ysym: str, *, days: int, interval: str = "1d") -> list[Bar]:
    """Daily bars via ``Ticker.history``; ``Adj Close`` preferred, NaN rows skipped, last ``days``."""
    t = get_ticker(ysym)
    period = yahoo_period(max(int(days or 1), 1))

    def _fetch():
        return t.history(period=period, interval=interval, auto_adjust=False, actions=False)

    df = call("Yahoo history", _fetch, symbol=ysym, timeout=20.0)
    return bars_from_df(df, days=days)


def bars_from_df(df: Any, *, days: int = 0) -> list[Bar]:
    """Convert a yfinance history DataFrame to ``list[Bar]`` (matching the raw chart vendor)."""
    if df is None or len(df) == 0:
        return []
    cols = {str(c).lower(): c for c in df.columns}
    c_open, c_high, c_low, c_close, c_vol = (cols.get(k) for k in ("open", "high", "low", "close", "volume"))
    c_adj = cols.get("adj close")
    if not all([c_open, c_high, c_low, c_close]):
        return []
    out: list[Bar] = []
    for idx, row in df.iterrows():
        o, h, low, c = _f(row[c_open]), _f(row[c_high]), _f(row[c_low]), _f(row[c_close])
        if o is None or h is None or low is None or c is None:
            continue
        if c_adj is not None:
            adj = _f(row[c_adj])
            if adj is not None:
                c = adj
        v = _f(row[c_vol]) if c_vol is not None else None
        d = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        date_str = d.strftime("%Y-%m-%d") if isinstance(d, (datetime, date)) else str(d)[:10]
        out.append(Bar(date=date_str, open=o, close=c, high=h, low=low, volume=v or 0.0))
    if days and days > 0 and len(out) > days:
        out = out[-days:]
    return out


_FAST_KEYS = {
    "last": ("last_price", "lastPrice"),
    "prev_close": ("previous_close", "previousClose", "regular_market_previous_close"),
    "open": ("open",),
    "high": ("day_high", "dayHigh"),
    "low": ("day_low", "dayLow"),
    "volume": ("last_volume", "lastVolume"),
}


def fast_quote(ysym: str) -> dict | None:
    """``{last, prev_close, open, high, low, volume}`` from ``fast_info``; ``None`` when no last price.
    FastInfo is a lazy key-normalising mapping where ``.get`` returns ``None``: use ``[]`` and catch."""
    t = get_ticker(ysym)

    def _fetch() -> dict | None:
        fi = t.fast_info
        out: dict[str, float | None] = {}
        for field, keys in _FAST_KEYS.items():
            val = None
            for k in keys:
                try:
                    v = fi[k]
                except (KeyError, TypeError, AttributeError):
                    continue
                val = _f(v)
                if val is not None:
                    break
            out[field] = val
        return out if out.get("last") else None

    return call("Yahoo quote", _fetch, symbol=ysym)


def info(ysym: str) -> dict:
    """``Ticker.info`` via a 6 h cache; ``{}`` on failure (never raises)."""
    cached = _info_cache.get(ysym)
    if cached is not None:
        return cached
    t = get_ticker(ysym)
    try:
        data = call("Yahoo info", lambda: t.info, symbol=ysym, timeout=20.0)
    except VendorError as e:
        logger.debug(f"yfinance info {ysym} failed: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    _info_cache.set(ysym, data)
    return data


def equity_query(operator: str, operands: list) -> dict:
    """Build a screener query in the library-free dict form ``{"operator", "operands"}``
    (operands may nest further dicts). ``screen`` converts it to ``yfinance.EquityQuery``
    right before the library call, so vendors never import yfinance and tests receive the
    plain dict through ``screen_fn``."""
    return {"operator": operator, "operands": list(operands)}


def _to_library_query(query: Any) -> Any:
    if not isinstance(query, dict):
        return query
    eq_cls = import_yfinance().EquityQuery
    operands = [_to_library_query(op) if isinstance(op, dict) else op for op in query.get("operands") or []]
    return eq_cls(str(query.get("operator")), operands)


def screen(query: Any, *, sort_field: str | None = None, sort_asc: bool | None = None,
           size: int = 100, offset: int = 0) -> list[dict]:
    """``yf.screen`` (predefined id string, ``EquityQuery``, or the dict form built by
    :func:`equity_query`) -> list of quote dicts."""
    apply_proxy({})

    def _fetch():
        kw: dict[str, Any] = {"size": int(size), "offset": int(offset)}
        if sort_field:
            kw["sortField"] = sort_field
        if sort_asc is not None:
            kw["sortAsc"] = bool(sort_asc)
        if screen_fn is not None:
            res = screen_fn(query, **kw)
        else:
            res = import_yfinance().screen(_to_library_query(query), **kw)
        if isinstance(res, dict):
            return res.get("quotes") or []
        return list(res or [])

    label = f"Yahoo screen {query if isinstance(query, str) else 'query'}"
    return call(label, _fetch, timeout=20.0)


def search(query: str, *, max_results: int = 20) -> list[dict]:
    """``yfinance.Search(query).quotes`` -> list of quote dicts."""
    apply_proxy({})

    def _fetch():
        if search_fn is not None:
            res = search_fn(query, max_results=max_results)
            return list(res or [])
        yf = import_yfinance()
        s = yf.Search(query, max_results=max_results, news_count=0, lists_count=0,
                      include_cb=False, include_nav_links=False, include_research=False)
        return list(getattr(s, "quotes", None) or [])

    return call("Yahoo search", _fetch, symbol=query)


def download_last(symbols: list[str] | tuple[str, ...], period: str = "5d") -> dict[str, dict]:
    """One batched ``yf.download`` -> ``{sym: {last, prev_close}}``; symbols without data are omitted."""
    syms = [str(s).strip() for s in symbols if str(s).strip()]
    if not syms:
        return {}
    apply_proxy({})

    def _fetch():
        if download_fn is not None:
            return download_fn(syms, period=period)
        yf = import_yfinance()
        return yf.download(syms, period=period, interval="1d", group_by="ticker", progress=False,
                           threads=False, auto_adjust=False)

    raw = call("Yahoo download", _fetch, symbol=",".join(syms[:5]), timeout=30.0)
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if isinstance(v, dict) and v.get("last")}
    return _last_from_download(raw, syms)


def _last_from_download(df: Any, syms: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if df is None or len(df) == 0:
        return out
    for s in syms:
        try:
            if hasattr(df.columns, "levels") and s in df.columns.get_level_values(0):
                sub = df[s]
            elif len(syms) == 1 and "Close" in df.columns:
                sub = df
            else:
                continue
            closes = sub["Close"].dropna()
            if len(closes) == 0:
                continue
            last = _f(closes.iloc[-1])
            prev = _f(closes.iloc[-2]) if len(closes) >= 2 else None
            if last is None:
                continue
            out[s] = {"last": last, "prev_close": prev}
        except Exception:
            continue
    return out


def reset_caches() -> None:
    """Clear the ticker/info caches and the yfinance breaker (tests, Test button)."""
    global _applied_proxy, _proxy_applied_once
    _ticker_cache.clear()
    _info_cache.clear()
    _applied_proxy, _proxy_applied_once = None, False
    http.reset_circuit_breaker(HOST_KEY)
