"""统一 HTTP 工具:走系统代理(trust_env=True)+ 按 host 节流 + 退避重试 + 来源标记。

默认 trust_env=True —— 遵循进程 env 的 HTTP_PROXY/NO_PROXY(宿主按 UI 的 http_proxy 设置统一注入);
没配代理时即直连。个别调用可用 proxy= 显式覆盖。
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("fetch_source", default="")


@contextmanager
def fetch_source(name: str):
    """标注取数来源,写入失败日志便于定位触发方。"""
    token = _FETCH_SOURCE.set(name or "")
    try:
        yield
    finally:
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# 失败原因收集:默认 None = 不收集(生产热路径零开销)。数据源"测试"按钮用 capture_errors()
# 包住取数调用,把 market_get / vendor 的真实失败原因收上来透到 UI,而不是只显示"无数据"。
_ERROR_SINK: contextvars.ContextVar[list | None] = contextvars.ContextVar("md_error_sink", default=None)


@contextmanager
def capture_errors():
    """进入后,market_get / record_error 的失败原因会被收集到 yield 出的 list。"""
    errs: list[str] = []
    token = _ERROR_SINK.set(errs)
    try:
        yield errs
    finally:
        _ERROR_SINK.reset(token)


def record_error(msg: str) -> None:
    """把一条失败原因写入当前 capture_errors 上下文(无上下文则忽略)。
    供 vendor 自己 catch 异常(如 yfinance 走库、不经 market_get)时也能上报真因。"""
    sink = _ERROR_SINK.get()
    if sink is not None and msg:
        sink.append(msg)


_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """保证对同一 host 的请求间隔 ≥ min_interval_s。"""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


# 熔断器:某个 host 连续失败到阈值后,在冷却期内直接返回 None,不再发起请求、不再 sleep。
# 背景:market_get 是同步的(httpx.Client + time.sleep),被事件循环上的调度任务调用时,
# 一个挂掉的数据源会用"重试 × 退避 × 多标的"把整个 web 服务卡死几十秒。熔断后单次成本降到 0。
_BREAKER_LOCK = threading.Lock()
_breaker: dict[str, dict[str, float]] = {}

_BREAKER_THRESHOLD = 3          # 连续失败多少次后打开熔断
_BREAKER_BASE_COOLDOWN = 60.0   # 首次冷却秒数
_BREAKER_MAX_COOLDOWN = 300.0   # 冷却上限(指数退避)


def _breaker_blocked(host_key: str) -> bool:
    """熔断打开且仍在冷却期内 → True(调用方应立即放弃)。"""
    with _BREAKER_LOCK:
        st = _breaker.get(host_key)
        return bool(st and time.time() < st.get("open_until", 0.0))


def _breaker_on_failure(host_key: str, label: str) -> None:
    with _BREAKER_LOCK:
        st = _breaker.setdefault(host_key, {"fails": 0.0, "open_until": 0.0, "cooldown": 0.0})
        st["fails"] += 1
        if st["fails"] < _BREAKER_THRESHOLD:
            return
        cooldown = min(
            _BREAKER_MAX_COOLDOWN,
            (st["cooldown"] * 2) if st["cooldown"] else _BREAKER_BASE_COOLDOWN,
        )
        st["cooldown"] = cooldown
        st["open_until"] = time.time() + cooldown
    logger.warning(
        f"{label} 连续失败 {_BREAKER_THRESHOLD} 次,熔断 {cooldown:.0f}s(host={host_key})"
    )


def _breaker_on_success(host_key: str) -> None:
    with _BREAKER_LOCK:
        if host_key in _breaker:
            _breaker.pop(host_key, None)


def reset_circuit_breaker(host_key: str | None = None) -> None:
    """手动清除熔断状态(数据源"测试"按钮 / 单测用)。不传则全部清除。"""
    with _BREAKER_LOCK:
        if host_key is None:
            _breaker.clear()
        else:
            _breaker.pop(host_key, None)


def _breaker_trip(host_key: str, cooldown_s: float) -> None:
    """Open the breaker immediately for ``cooldown_s`` seconds (rate-limit responses)."""
    with _BREAKER_LOCK:
        st = _breaker.setdefault(host_key, {"fails": 0.0, "open_until": 0.0, "cooldown": 0.0})
        st["fails"] = float(_BREAKER_THRESHOLD)
        st["cooldown"] = float(cooldown_s)
        st["open_until"] = time.time() + float(cooldown_s)
    logger.warning(f"{host_key} rate limited, circuit breaker open for {cooldown_s:.0f}s")


# Public breaker / probe API for library-call vendors (yfinance goes through yf_adapter,
# not market_get, but must share the same breaker and the same "Test button bypass").

def is_probing() -> bool:
    """True inside ``capture_errors()`` (the Data Sources Test button): callers bypass the breaker."""
    return _ERROR_SINK.get() is not None


def breaker_blocked(host_key: str) -> bool:
    """True when the breaker for ``host_key`` is open and still cooling down."""
    return _breaker_blocked(host_key)


def breaker_failure(host_key: str, label: str) -> None:
    """Record one failure; opens the breaker after ``_BREAKER_THRESHOLD`` consecutive failures."""
    _breaker_on_failure(host_key, label)


def breaker_success(host_key: str) -> None:
    """Record one success; resets the breaker for ``host_key``."""
    _breaker_on_success(host_key)


def breaker_trip(host_key: str, cooldown_s: float = 300.0) -> None:
    """Open the breaker immediately for ``cooldown_s`` seconds."""
    _breaker_trip(host_key, cooldown_s)


def market_get(
    url: str,
    *,
    host_key: str,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "text",   # "text" | "json" | "content"
    encoding: str | None = None,
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,
    follow_redirects: bool = True,
    verify: bool = True,
    proxy: str | None = None,
) -> Any | None:
    """走系统代理(env)+ 按 host 节流 + 退避重试。成功返回解析结果,失败返回 None 并打带来源日志。

    proxy: 显式代理,仅在给了值时传给 httpx.Client 覆盖 env 代理;不传则遵循 trust_env(env)。
    """
    label = log_label or host_key
    # capture_errors() 生效时说明是用户主动"测试数据源",绕过熔断让他能立刻重试。
    probing = _ERROR_SINK.get() is not None
    if not probing and _breaker_blocked(host_key):
        sym = f" symbol={symbol}" if symbol else ""
        logger.debug(f"{label} 熔断中,跳过{sym}{source_suffix()}")
        return None

    effective_proxy = proxy
    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
                **({"proxy": effective_proxy} if effective_proxy else {}),
            ) as client:
                resp = client.get(url, params=params)
                if raise_for_status:
                    resp.raise_for_status()
                _breaker_on_success(host_key)
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                if encoding:
                    return resp.content.decode(encoding, errors="ignore")
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        if not probing:
            _breaker_on_failure(host_key, label)
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} 获取失败{sym}: {last_err}{source_suffix()}")
        record_error(f"{label}{sym}: {type(last_err).__name__}: {last_err}")
    return None


def market_post(
    url: str,
    *,
    host_key: str,
    json: Any = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 1,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "json",   # "json" | "text" | "content"
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,
    follow_redirects: bool = True,
    verify: bool = True,
    proxy: str | None = None,
) -> Any | None:
    """POST mirror of ``market_get`` (JSON body): same throttle, breaker, retries and
    ``record_error`` reporting. Returns the parsed body on success, ``None`` on failure."""
    label = log_label or host_key
    probing = _ERROR_SINK.get() is not None
    if not probing and _breaker_blocked(host_key):
        sym = f" symbol={symbol}" if symbol else ""
        logger.debug(f"{label} circuit breaker open, skipping{sym}{source_suffix()}")
        return None

    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
                **({"proxy": proxy} if proxy else {}),
            ) as client:
                resp = client.post(url, json=json)
                if raise_for_status:
                    resp.raise_for_status()
                _breaker_on_success(host_key)
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        if not probing:
            _breaker_on_failure(host_key, label)
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} POST failed{sym}: {last_err}{source_suffix()}")
        record_error(f"{label}{sym}: {type(last_err).__name__}: {last_err}")
    return None
