"""FX 缓存并发原子性回归(移植自 Codex review-6 探针):两个真实的刷新路径被确定性地
交叠执行,断言一个请求的快照绝不会把"旧汇率"和"另一个请求的新鲜状态/来源/时间"
拼成一个从未真实存在过的元组。

探针原文 pause seam 是 `get_cad_usd_rate`;Revision 7 把三次分散的全局读
(`get_cad_usd_rate()` / `cad_usd_rate_known()` / `_cad_usd_rate_cache`)合并成唯一的
内部解析器 `_resolve_cad_usd_state()`(在锁内决定并返回整份不可变状态),
`resolve_fx_snapshot()` 只消费它返回的那一个对象、不再读任何全局。因此 pause seam
随之移到 `_resolve_cad_usd_state`:第一个请求在解析器返回状态之后、快照构建之前暂停,
第二个请求此时真实地刷新缓存 —— 交叠是真实的,不是空转。

不变量(与探针逐字一致,不削弱):快照只能是两个自洽的时点之一
`(.7, "last_known", 1000)` 或 `(.8, "known", 10301)`,绝不能是 `.7` 配上第二个请求
的 `.8` 的 source/as_of。

本文件不连网:自动断网 fixture fail-closed;两个 provider fetcher 均被 monkeypatch。
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import src.web.api.accounts as api


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都必须立即失败(fail-closed),照抄
    tests/test_codex_fx_transitions.py 的既有手法。"""

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _clear_single_flight(monkeypatch):
    """每个用例从"没有在途刷新"开始,避免上一个用例的线程状态泄漏。"""
    monkeypatch.setattr(api, "_fx_refresh_inflight", False)


def _seed_expired_genuine(monkeypatch):
    """缓存:曾经真实获取过的 .7(fetched_at 1000,来源 old fixture source),已过期。"""
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {
        "rate": .7, "ts": 0, "known": True, "genuine": True,
        "fetched_at": 1000, "source": "old fixture source",
    })


def test_snapshot_does_not_pair_old_rate_with_another_requests_fresh_status(monkeypatch):
    """两个真实刷新路径确定性交叠:第一个请求解析出状态后暂停,第二个请求把真实缓存刷新
    成 .8/known/10301;第一个请求的快照必须是自洽的 (.7, last_known, 1000) 或
    (.8, known, 10301),绝不能是 .7 配上第二个请求的来源/时间。"""
    clock = [10000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    _seed_expired_genuine(monkeypatch)
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)
    real_resolver = api._resolve_cad_usd_state
    captured, resume = Event(), Event()

    def paused_resolver():
        # Same window the original probe paused in: the request's refresh is done and
        # its state decided, but the snapshot has not been built yet.
        state = real_resolver()
        captured.set()
        assert resume.wait(3)
        return state

    monkeypatch.setattr(api, "_resolve_cad_usd_state", paused_resolver)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(api.resolve_fx_snapshot)
        try:
            assert captured.wait(3)
            clock[0] += 301  # past the failure backoff
            monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: .8)
            second = real_resolver()  # second request refreshes the real cache
            assert (second.rate, second.known, second.fetched_at, second.source) == (
                .8, True, 10301, "Bank of Canada",
            )
        finally:
            resume.set()
        snap = first.result(timeout=3)
    # Either coherent point-in-time snapshot is acceptable, but never old .7
    # labelled fresh with the second request's .8 source/time.
    assert (snap.rate, snap.status, snap.as_of) in [
        (.7, "last_known", 1000), (.8, "known", 10301),
    ], snap
    # Provenance must belong to the same cache version as the rate.
    assert (snap.rate, snap.source) in [(.7, "old fixture source"), (.8, "Bank of Canada")], snap


def test_failed_refresh_never_clobbers_a_newer_successful_state(monkeypatch):
    """失败的刷新绝不能覆盖更新的成功状态:leader 在抓取期间,另一线程已把缓存换成
    .8/known/10500;leader 两个 provider 都失败 —— 它必须返回并保留那个更新的成功状态,
    而不是把 known 打回 False / 写入退避时间戳。"""
    clock = [10000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    _seed_expired_genuine(monkeypatch)
    newer = {
        "rate": .8, "ts": 10500.0, "known": True, "genuine": True,
        "fetched_at": 10500.0, "source": "Bank of Canada",
    }

    def boc_fails_after_concurrent_success():
        # Simulate a concurrent writer landing a fresh success while we are fetching.
        monkeypatch.setattr(api, "_cad_usd_rate_cache", newer)
        clock[0] = 10600.0
        return None

    monkeypatch.setattr(api, "_fetch_cad_usd_boc", boc_fails_after_concurrent_success)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    state = api._resolve_cad_usd_state()

    assert (state.rate, state.known, state.fetched_at, state.source) == (.8, True, 10500.0, "Bank of Canada")
    assert api._cad_usd_rate_cache is newer, "the failure must not have rebound the cache"
    assert api._fx_refresh_inflight is False
    snap = api.resolve_fx_snapshot()  # within TTL now: no fetch, same coherent state
    assert (snap.rate, snap.status, snap.as_of, snap.source) == (.8, "known", 10500.0, "Bank of Canada")


def test_successful_refresh_does_not_overwrite_a_newer_success(monkeypatch):
    """成功但更旧的刷新也不能覆盖更新的成功状态:leader 抓到 .75,但抓取期间另一线程
    已写入 fetched_at 更晚的 .8 —— 必须保留 .8。"""
    clock = [10000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    _seed_expired_genuine(monkeypatch)
    newer = {
        "rate": .8, "ts": 20000.0, "known": True, "genuine": True,
        "fetched_at": 20000.0, "source": "Yahoo CAD=X",
    }

    def boc_succeeds_late():
        monkeypatch.setattr(api, "_cad_usd_rate_cache", newer)
        clock[0] = 10600.0  # our fetch completes at 10600 < the newer 20000
        return .75

    monkeypatch.setattr(api, "_fetch_cad_usd_boc", boc_succeeds_late)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    state = api._resolve_cad_usd_state()

    assert (state.rate, state.fetched_at, state.source) == (.8, 20000.0, "Yahoo CAD=X")
    assert api._cad_usd_rate_cache is newer
    assert api._fx_refresh_inflight is False


def test_concurrent_expired_callers_single_flight_one_fetch(monkeypatch):
    """single-flight:两个并发请求都发现缓存过期,只允许一次 provider 抓取;follower
    等待 leader 的结果,两者拿到同一份 known 状态。"""
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    calls = {"n": 0}
    entered, release = Event(), Event()

    def slow_boc():
        calls["n"] += 1
        entered.set()
        assert release.wait(3)
        return .8

    monkeypatch.setattr(api, "_fetch_cad_usd_boc", slow_boc)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(api._resolve_cad_usd_state)
        try:
            assert entered.wait(3)  # leader is inside the (paused) fetch
            follower = pool.submit(api._resolve_cad_usd_state)
        finally:
            release.set()
        a = leader.result(timeout=5)
        b = follower.result(timeout=5)

    assert calls["n"] == 1, "the follower must not fetch a second time"
    assert a == b
    assert a.known is True and a.rate == .8 and a.source == "Bank of Canada"
    assert api._fx_refresh_inflight is False


def test_get_cad_usd_rate_is_a_thin_wrapper_over_the_atomic_resolver(monkeypatch):
    """`get_cad_usd_rate()` 只是原子解析器的薄封装:返回同一份状态里的 rate 值,并且
    (为 tests/test_fx_rate.py 这类既有用例)仍把刷新结果写回模块级 dict
    `_cad_usd_rate_cache` 的 "rate"/"ts" 键。"""
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": api._CAD_USD_FALLBACK, "ts": 0})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: .8)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    assert api.get_cad_usd_rate() == pytest.approx(.8)
    assert api._cad_usd_rate_cache["rate"] == pytest.approx(.8)
    assert api._cad_usd_rate_cache["ts"] > 0
    assert api._cad_usd_rate_cache["known"] is True
    assert not hasattr(api, "cad_usd_rate_known"), "split-read accessor was intentionally removed"


# ---------------------------------------------------------------------------
# Codex review 07:follower 的有界等待超时 / leader 中止,绝不能把过期汇率报成 known。
# 三个用例都让真实 leader 在被 Event 卡住的 mock provider 里"仍在抓取",真实 follower
# 以缩短的 EXCHANGE_RATE_REFRESH_WAIT 走完整解析器路径(不 patch 解析器本身)。
# ---------------------------------------------------------------------------


def _seed_expired_genuine_at_1000(monkeypatch):
    """缓存:真实获取过的 .7(fetched_at 1000.0,来源 old fixture source),时钟 10000 时已过期。"""
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {
        "rate": .7, "ts": 1000.0, "known": True, "genuine": True,
        "fetched_at": 1000.0, "source": "old fixture source",
    })


def test_follower_bounded_timeout_reports_expired_genuine_rate_as_last_known(monkeypatch):
    """(复现 REVIEW-07 缺陷)leader 仍在抓取、follower 有界等待超时:follower 必须把
    真实但已过期的 .7 报成 last_known(保留 rate/as_of/source 原样),绝不是 known;
    不能重复抓取(只有一次 provider 调用);leader 释放后拿到 .8/known。"""
    monkeypatch.setattr(api.time, "time", lambda: 10000.0)
    _seed_expired_genuine_at_1000(monkeypatch)
    monkeypatch.setattr(api, "EXCHANGE_RATE_REFRESH_WAIT", .01)
    entered, release = Event(), Event()
    calls = []

    def slow_boc():
        calls.append("boc")
        entered.set()
        assert release.wait(3)
        return .8

    monkeypatch.setattr(api, "_fetch_cad_usd_boc", slow_boc)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        leader = pool.submit(api.resolve_fx_snapshot)
        try:
            assert entered.wait(3)
            follower_state = api._resolve_cad_usd_state()
            follower = api._snapshot_from_state(follower_state)
            assert not leader.done(), "the follower must have returned while the leader is still fetching"
            assert len(calls) == 1, "the follower must not fetch a second time"
        finally:
            release.set()
        fresh = leader.result(timeout=3)
    # The resolver's returned state itself already carries the truth (no reliance on
    # snapshot-layer patching): expired => known False, genuine + tuple preserved.
    assert (follower_state.known, follower_state.genuine) == (False, True)
    assert (follower_state.rate, follower_state.fetched_at, follower_state.source) == (
        .7, 1000.0, "old fixture source",
    )
    assert (follower.rate, follower.status, follower.as_of, follower.source) == (
        .7, "last_known", 1000.0, "old fixture source",
    ), follower
    assert (fresh.rate, fresh.status, fresh.source) == (.8, "known", "Bank of Canada")
    assert api._fx_refresh_inflight is False


def test_follower_after_aborted_leader_never_reports_known(monkeypatch):
    """leader 抓取中途抛出异常(走 `_release_fx_refresh` 路径、缓存未被重绑):被唤醒的
    follower 看到的仍是触发刷新的那份过期版本,必须报 last_known 而不是 known;
    rate/as_of/source 保持原样;single-flight 标志被正确清理。"""
    monkeypatch.setattr(api.time, "time", lambda: 10000.0)
    _seed_expired_genuine_at_1000(monkeypatch)
    monkeypatch.setattr(api, "EXCHANGE_RATE_REFRESH_WAIT", 5.0)  # long: abort, not timeout, must wake it
    entered, follower_waiting, abort = Event(), Event(), Event()
    calls = []

    def fetch_then_abort():
        calls.append("fetch")
        entered.set()
        assert abort.wait(3)
        raise RuntimeError("provider layer blew up mid-refresh")

    monkeypatch.setattr(api, "_fetch_cad_usd_rate", fetch_then_abort)

    real_wait_for = api._fx_cond.wait_for

    def observed_wait_for(predicate, timeout=None):
        follower_waiting.set()
        return real_wait_for(predicate, timeout=timeout)

    monkeypatch.setattr(api._fx_cond, "wait_for", observed_wait_for)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(api._resolve_cad_usd_state)
        try:
            assert entered.wait(3)
            follower = pool.submit(api.resolve_fx_snapshot)
            assert follower_waiting.wait(3), "the follower must actually be parked on the condition"
            assert not follower.done()
        finally:
            abort.set()
        with pytest.raises(RuntimeError):
            leader.result(timeout=3)
        snap = follower.result(timeout=3)

    assert calls == ["fetch"], "the follower must not start its own fetch after the abort"
    assert (snap.rate, snap.status, snap.as_of, snap.source) == (
        .7, "last_known", 1000.0, "old fixture source",
    ), snap
    assert api._fx_refresh_inflight is False
    assert api._cad_usd_rate_cache["known"] is True, "the abort path must not have rewritten the cache"


def test_follower_bounded_timeout_with_never_fetched_cache_is_unknown(monkeypatch):
    """缓存从未真实获取过汇率(占位常量 + known False)且 leader 仍在抓取:超时的
    follower 必须报 unknown(rate None),绝不能把占位常量当成 known 或 last_known。"""
    monkeypatch.setattr(api.time, "time", lambda: 10000.0)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": api._CAD_USD_FALLBACK, "ts": 0, "known": False})
    monkeypatch.setattr(api, "EXCHANGE_RATE_REFRESH_WAIT", .01)
    entered, release = Event(), Event()
    calls = []

    def slow_boc():
        calls.append("boc")
        entered.set()
        assert release.wait(3)
        return .8

    monkeypatch.setattr(api, "_fetch_cad_usd_boc", slow_boc)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        leader = pool.submit(api.resolve_fx_snapshot)
        try:
            assert entered.wait(3)
            follower = api.resolve_fx_snapshot()
            assert not leader.done()
            assert len(calls) == 1
        finally:
            release.set()
        fresh = leader.result(timeout=3)
    assert follower == api.FX_UNKNOWN, follower
    assert follower.rate is None and follower.status == "unknown"
    assert (fresh.rate, fresh.status) == (.8, "known")


def test_expiry_normalization_is_pure_and_keeps_in_ttl_success_known(monkeypatch):
    """`_fx_state_as_of` 的纯函数契约:TTL 内的真实成功保持 known 不变;过期版本只把
    known 降为 False,rate/ts/genuine/fetched_at/source 全部原样;从未获取过的状态原样。"""
    fresh = api._FxCacheState(rate=.8, ts=10000.0, known=True, genuine=True, fetched_at=10000.0, source="Bank of Canada")
    assert api._fx_state_as_of(fresh, 10000.0 + api.EXCHANGE_RATE_TTL - 1) is fresh

    expired = api._fx_state_as_of(fresh, 10000.0 + api.EXCHANGE_RATE_TTL)
    assert expired == api._FxCacheState(rate=.8, ts=10000.0, known=False, genuine=True, fetched_at=10000.0, source="Bank of Canada")
    assert api._snapshot_from_state(expired).status == "last_known"

    never = api._FxCacheState(rate=api._CAD_USD_FALLBACK, ts=0.0, known=False, genuine=False, fetched_at=None, source=None)
    assert api._fx_state_as_of(never, 99999.0) is never
    assert api._snapshot_from_state(never) == api.FX_UNKNOWN
