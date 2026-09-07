"""数据源不可达时不得卡死事件循环 —— 针对一次线上故障的回归测试。

故障复现:东财被拒后,(1) market_get 每轮都全量重试;(2) DBLogHandler.emit 在持有
logging 全局锁的情况下写 SQLite,busy_timeout=30s 一到就把整个 web 服务冻住 30 秒。
"""

import logging
import threading
import time

import pytest

from marketdata import http as md_http


@pytest.fixture(autouse=True)
def _reset_breaker():
    """每个用例前后清空熔断状态,避免相互串味。"""
    md_http.reset_circuit_breaker()
    yield
    md_http.reset_circuit_breaker()


def test_熔断后不再发起请求(monkeypatch):
    """同一 host 连续失败到阈值后,market_get 应立即返回 None 且不再碰网络。"""
    calls = {"n": 0}

    class BoomClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            calls["n"] += 1
            raise RuntimeError("Server disconnected without sending a response.")

    monkeypatch.setattr(md_http.httpx, "Client", BoomClient)
    monkeypatch.setattr(md_http.time, "sleep", lambda *_: None)  # 别真睡退避

    # 阈值是连续 3 次 market_get 失败(每次内部已重试 3 轮)
    for _ in range(md_http._BREAKER_THRESHOLD):
        assert md_http.market_get("https://x.test/a", host_key="x.test") is None

    calls_before = calls["n"]
    assert calls_before > 0

    # 熔断已打开:后续调用必须零网络开销
    for _ in range(10):
        assert md_http.market_get("https://x.test/a", host_key="x.test") is None
    assert calls["n"] == calls_before, "熔断打开后仍在发请求"


def test_熔断在测试数据源时被绕过(monkeypatch):
    """用户点"测试数据源"(capture_errors 生效)时应绕过熔断,允许立刻重试。"""
    calls = {"n": 0}

    class BoomClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(md_http.httpx, "Client", BoomClient)
    monkeypatch.setattr(md_http.time, "sleep", lambda *_: None)

    for _ in range(md_http._BREAKER_THRESHOLD):
        md_http.market_get("https://y.test/a", host_key="y.test")
    n = calls["n"]

    with md_http.capture_errors() as errs:
        md_http.market_get("https://y.test/a", host_key="y.test")
    assert calls["n"] > n, "测试数据源时不应被熔断挡住"
    assert errs, "失败原因应被 capture_errors 收集"


def test_日志_emit_不做数据库写入(monkeypatch):
    """emit() 在 logging 全局锁下执行,必须只入内存 buffer,绝不能同步写库。"""
    from src.web import log_handler as lh

    opened = {"n": 0}
    monkeypatch.setattr(
        lh, "SessionLocal", lambda: opened.__setitem__("n", opened["n"] + 1)
    )

    h = lh.DBLogHandler(level=logging.DEBUG)
    h._stopping.set()          # 停掉后台线程,单独观察 emit 的行为
    h._wake.set()
    h._thread.join(timeout=2)
    h.setFormatter(logging.Formatter("%(message)s"))

    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, None)
    h.emit(rec)  # ERROR 在旧实现里会触发同步 flush

    assert opened["n"] == 0, "emit() 同步开了数据库连接 —— 又会卡住事件循环"
    assert len(h._buffer) == 1


def test_写库慢不阻塞其他线程打日志(monkeypatch):
    """后台 flush 卡在 SQLite 上时,其他线程的 logging 调用必须照常返回。"""
    from src.web import log_handler as lh

    release = threading.Event()

    class SlowSession:
        def bulk_insert_mappings(self, *a, **kw):
            release.wait(timeout=10)   # 模拟 busy_timeout 上的长等待

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(lh, "SessionLocal", SlowSession)

    h = lh.DBLogHandler(level=logging.DEBUG)
    try:
        h.setFormatter(logging.Formatter("%(message)s"))
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", None, None)
        h.handle(rec)                  # 触发后台 flush,它会卡在 SlowSession 上
        time.sleep(0.3)                # 让 flush 线程真的进到 bulk_insert_mappings

        t0 = time.time()
        for _ in range(50):
            h.handle(logging.LogRecord("t", logging.INFO, __file__, 1, "y", None, None))
        elapsed = time.time() - t0

        assert elapsed < 1.0, f"写库阻塞了其他线程打日志({elapsed:.2f}s)"
    finally:
        release.set()
        h._stopping.set()
        h._wake.set()
        h._thread.join(timeout=5)
