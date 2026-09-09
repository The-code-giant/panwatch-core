"""进度 SSE 与日志 SSE tail 端点单测（mock 快照/内存库，不依赖真实任务）。"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.agents as agents_api
import src.web.api.logs as logs_api
from src.web.database import Base
from src.web.models import LogEntry


def _parse_events(body: str) -> list[tuple[str, dict]]:
    """把 SSE wire 文本解析成 [(event, data), ...]，跳过心跳注释。"""
    events = []
    for block in body.split("\n\n"):
        lines = [l for l in block.split("\n") if l]
        if not lines or lines[0].startswith(":"):
            continue
        event = next((l.split(": ", 1)[1] for l in lines if l.startswith("event: ")), "")
        data_raw = "\n".join(l.split(": ", 1)[1] for l in lines if l.startswith("data: "))
        events.append((event, json.loads(data_raw) if data_raw else {}))
    return events


async def _drain(resp) -> str:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_progress_sse_push_and_done(monkeypatch):
    """进度 SSE：快照变化才推 progress 事件，终态后推 done 并关流"""
    snapshots = [
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},
        {"trace_id": "t1", "status": "running", "current_stage": "market_analyst"},  # 无变化，不推
        {"trace_id": "t1", "status": "running", "current_stage": "trader"},
        {"trace_id": "t1", "status": "success", "current_stage": None},
    ]
    calls = {"n": 0}

    def fake_get_run_progress(trace_id, db):
        idx = min(calls["n"], len(snapshots) - 1)
        calls["n"] += 1
        return snapshots[idx]

    monkeypatch.setattr(agents_api, "get_run_progress", fake_get_run_progress)
    monkeypatch.setattr(agents_api, "PROGRESS_SSE_POLL_SEC", 0.01)

    async def run():
        resp = await agents_api.stream_run_progress("t1")
        assert resp.media_type == "text/event-stream"
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    kinds = [e for e, _ in events]
    # 4 次快照里只有 3 个不同 payload → 3 条 progress + 1 条 done
    assert kinds == ["progress", "progress", "progress", "done"]
    assert events[0][1]["current_stage"] == "market_analyst"
    assert events[2][1]["status"] == "success"
    assert events[3][1]["status"] == "success"


def test_progress_sse_invalid_trace_id():
    """进度 SSE：非法 trace_id 返回 400"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(agents_api.stream_run_progress("x" * 65))
    assert ei.value.status_code == 400


def _make_log_db(monkeypatch):
    """内存 SQLite + 预置日志行，并替换 SessionLocal。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("src.web.database.SessionLocal", factory)

    db = factory()
    for level, msg in [("INFO", "启动完成"), ("ERROR", "行情拉取失败"), ("INFO", "调度执行")]:
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level=level,
            logger_name="src.core.test",
            message=msg,
        ))
    db.commit()
    db.close()
    return factory


def test_logs_sse_resume_from_last_event_id(monkeypatch):
    """日志 SSE：带 Last-Event-ID 从缺口续推，事件 id 即日志行 id"""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={"last-event-id": "1"})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    ids = [item["id"] for item in logs_events[0]["items"]]
    assert ids == [2, 3]
    # 事件 id 用最后一条日志 id
    assert "id: 3\nevent: logs\n" in body
    # 超时后有 done 收尾
    assert events[-1][0] == "done"


def test_logs_sse_filters(monkeypatch):
    """日志 SSE：level 过滤生效，只推匹配的行"""
    _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.01)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 0.05)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="ERROR", q="", logger_name="", domain="all", since="", last_event_id=1,
        )
        return await _drain(resp)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    assert len(logs_events) == 1
    items = logs_events[0]["items"]
    assert len(items) == 1
    assert items[0]["level"] == "ERROR"
    assert items[0]["message"] == "行情拉取失败"


def test_logs_sse_tail_only_new(monkeypatch):
    """日志 SSE：无 Last-Event-ID 时从当前最新开始，只 tail 增量"""
    factory = _make_log_db(monkeypatch)
    monkeypatch.setattr(logs_api, "LOGS_SSE_POLL_SEC", 0.02)
    monkeypatch.setattr(logs_api, "LOGS_SSE_MAX_DURATION_SEC", 1.0)

    # 真正的竞态：无 Last-Event-ID 时，gen() 用
    # `cursor = await asyncio.to_thread(_current_max_id)` 在线程池里把"当前最新 id"
    # 定为基线游标；这一步和下面"插入新日志"是两个真正并发的一方——游标计算跑在
    # 线程池的线程上，插入跑在事件循环所在线程、同步执行。两者谁先跑完没有任何
    # 天然保证。旧版本用 `await asyncio.sleep(0.3)` 赌线程池能在 300ms 内把游标
    # 算完，负载高时（线程池饥饿/调度延迟）插入会抢在游标计算之前完成，新那行
    # 日志的 id 被当成"基线本身"锁进 cursor，`_fetch_after(cursor)` 用
    # `id > cursor` 一过滤就把它过滤没了——测试断言 0 条而不是 1 条，这正是本文件
    # 顶部记录的 1/3 概率的 flaky（已用 20 次紧凑循环复现，见 W-15 报告）。
    #
    # 修复：不再用时长去猜，而是拦截 gen() 唯一会调用到的
    # `asyncio.to_thread`，直接观察"游标计算这次调用已经跑完"这个真实事件——
    # 第一次调用必然就是 `_current_max_id`（因为本用例 last_event_id=0，
    # `resume_id` 恒为 0，游标只能来自这次 to_thread 调用）。等这个 event
    # set 之后再插入，游标已经确定性地在插入之前落定，race 从根上消失。
    cursor_ready = asyncio.Event()
    to_thread_calls = {"n": 0}
    real_to_thread = asyncio.to_thread

    async def to_thread_spy(func, *args, **kwargs):
        result = await real_to_thread(func, *args, **kwargs)
        to_thread_calls["n"] += 1
        if to_thread_calls["n"] == 1:
            cursor_ready.set()
        return result

    monkeypatch.setattr(asyncio, "to_thread", to_thread_spy)

    async def run():
        request = SimpleNamespace(headers={})
        resp = await logs_api.stream_logs(
            request, level="", q="", logger_name="", domain="all", since="", last_event_id=0,
        )

        received: list[str] = []

        async def consume():
            async for chunk in resp.body_iterator:
                received.append(chunk)

        task = asyncio.create_task(consume())
        # 等基线游标真正算完（真实信号），而不是赌一个时长
        await asyncio.wait_for(cursor_ready.wait(), timeout=3)
        db = factory()
        db.add(LogEntry(
            timestamp=datetime.now(timezone.utc),
            level="WARNING",
            logger_name="src.core.test",
            message="新增日志",
        ))
        db.commit()
        db.close()
        await asyncio.wait_for(task, timeout=3)
        return "".join(received)

    body = asyncio.run(run())
    events = _parse_events(body)
    logs_events = [d for e, d in events if e == "logs"]
    # 只有新插入的那条，存量 3 条不重放
    assert len(logs_events) == 1
    assert [i["message"] for i in logs_events[0]["items"]] == ["新增日志"]
