"""采集层批量整治 P1:共享 market_http(节流/重试/来源)。

报价等加 TTL 缓存,避免调度任务每轮重复联网触发限流;这里验证 market_http 的重试与来源日志。
"""

from __future__ import annotations

import logging

from src.collectors import market_http


def test_market_get_retries_and_logs_source(monkeypatch, caplog):
    """market_get 失败应退避重试,并在日志带上 [src=...] 调用来源。"""
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(market_http.httpx, "Client", _FakeClient)
    monkeypatch.setattr(market_http.time, "sleep", lambda *_: None)

    with caplog.at_level(logging.WARNING):
        with market_http.fetch_source("unit_src"):
            out = market_http.market_get(
                "http://x", host_key="x", retries=2, log_label="测试"
            )

    assert out is None
    assert calls["n"] == 3, f"应 1 次 + 重试 2 次 = 3 次,实际 {calls['n']}"
    assert any(
        "[src=unit_src]" in r.getMessage() for r in caplog.records
    ), caplog.text
