"""系统自检(Doctor):一键体检 数据源 / AI / 通知,带中文修复提示。

复用各自现有的 test 逻辑(数据源 manager.test_source、AI AIClient.chat、通知 NotifierManager),
不重造探测;补两件事:① 并发聚合成一块看板 ② 常见错误 → 中文 actionable 修复提示。

通知默认**只校验 URI 配置不真发**(防刷屏);notify_send=True 才真实发送。
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.web.database import SessionLocal

logger = logging.getLogger(__name__)

SLOW_MS = 4000          # 超过算「慢」
PROBE_TIMEOUT_S = 20    # 单项探测超时


def classify_hint(category: str, error: str | None) -> str:
    """错误 → 英文 actionable 修复提示。覆盖自托管最常见的代理/鉴权/配置坑。

    返回值会渲染到前端 SelfCheckModal 的失败项下方（item.hint），属于用户可见文案，
    必须是英文；下面 any(k in e for k in ...) 里匹配的中文子串是用来匹配上游报错文本的，
    不是展示文案，不能翻译。
    """
    e = (error or "").lower()
    if category == "datasource":
        if "database is locked" in e:
            return "SQLite is locked — concurrent scheduling combined with a slow proxy. Reduce concurrency, or speed up or disable the proxy."
        if any(k in e for k in (
            "server disconnected", "timeout", "timed out", "connect", "proxy",
            "ssl", "remote end closed", "read timed out", "connection reset",
        )):
            return "Failed to connect to the quote/news API — all requests go through the http_proxy system proxy. Check whether that proxy can reach the target domain (CN interfaces need a CN egress, Yahoo needs an overseas one), or switch to a trusted egress if this machine is behind a MITM proxy."
        return "Data source unreachable — open the data source settings page for detailed logs, and confirm the provider and endpoint are reachable."
    if category == "ai":
        if any(k in e for k in ("401", "unauthorized", "invalid_api_key", "api key", "incorrect api key", "authentication")):
            return "AI authentication failed — the API key is wrong or expired. Check your provider's api_key."
        if any(k in e for k in ("model", "not found", "does not exist", "404")):
            return "Model not found — check that the model name matches the one your provider expects."
        if any(k in e for k in ("429", "rate limit", "quota", "insufficient", "balance")):
            return "Rate-limited or out of quota — retry later, or check your account balance and quota."
        if any(k in e for k in ("connect", "timeout", "timed out", "proxy", "ssl", "getaddrinfo", "name resolution")):
            return "Can't reach the AI service — check that base_url is correct and whether a proxy is required or being misapplied."
        return "AI call failed — check base_url / api_key / model configuration one by one."
    if category == "notify":
        if any(k in e for k in ("invalid", "unsupported", "scheme", "malformed", "parse", "config")):
            return "Invalid notification configuration — check the channel URL/parameter format (Apprise URI)."
        if any(k in e for k in ("forbidden", "unauthorized", "403", "401", "404", "blocked", "connect", "timeout")):
            return "Notification send failed — check that the webhook address/token is correct and not blocked by the network."
        return "Notifications aren't getting through — check the channel configuration, or use Test on the channels page to send a real message."
    if category == "system":
        if "lock" in e:
            return "SQLite is locked — concurrent scheduling combined with a slow proxy. Reduce concurrency, or speed up or disable the proxy."
        if any(k in e for k in ("disk", "space", "磁盘", "空间")):
            return "Low disk space — clean up old data/logs in the data directory, or expand the disk."
        if any(k in e for k in ("scheduler", "调度", "stopped", "not running")):
            return "Scheduler not running / stopped — restart the service to resume scheduled jobs."
        return error or "System check failed — see the logs."
    return error or "Unknown error — see the logs."


def _item(category: str, key: str, name: str, status: str,
          latency_ms: int, error: str | None = None, note: str | None = None) -> dict:
    return {
        "category": category,
        "key": key,
        "name": name,
        "status": status,  # ok | slow | fail
        "latency_ms": int(latency_ms),
        "error": error,
        "hint": classify_hint(category, error) if status == "fail" else "",
        "note": note,
    }


def _status_for(success: bool, latency_ms: int) -> str:
    if not success:
        return "fail"
    return "slow" if latency_ms > SLOW_MS else "ok"


async def probe_datasource(source) -> dict:
    """复用 collector manager.test_source。"""
    from src.core.data_collector import get_collector_manager

    t0 = time.monotonic()
    try:
        result = await get_collector_manager().test_source(source)
        latency = int(getattr(result, "duration_ms", None) or (time.monotonic() - t0) * 1000)
        return _item("datasource", f"ds:{source.id}", source.name,
                     _status_for(bool(result.success), latency), latency,
                     None if result.success else (result.error or "Test failed"))
    except Exception as e:
        return _item("datasource", f"ds:{source.id}", source.name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_ai_model(model, service) -> dict:
    """复用 AIClient.chat 发一个极短 ping。"""
    from src.core.ai_client import AIClient

    name = model.name or model.model
    t0 = time.monotonic()
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key, model=model.model)
        await client.chat(system_prompt="You are a helpful assistant.",
                          user_content="Say 'OK'.", temperature=0)
        latency = int((time.monotonic() - t0) * 1000)
        return _item("ai", f"ai:{model.id}", name, _status_for(True, latency), latency)
    except Exception as e:
        return _item("ai", f"ai:{model.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_notify_channel(channel, *, send: bool = False) -> dict:
    """默认只校验 URI 配置(add_channel 不通会抛);send=True 才真实发送。"""
    from src.core.notifier import NotifierManager

    name = channel.name or channel.type
    t0 = time.monotonic()
    try:
        notifier = NotifierManager()
        notifier.add_channel(channel.type, channel.config or {})  # URI 非法会抛
        if not send:
            latency = int((time.monotonic() - t0) * 1000)
            return _item("notify", f"nc:{channel.id}", name, "ok", latency,
                         note="Only the configuration format was checked, nothing was actually sent (check \"Include real send\" to send a test message).")
        result = await notifier.notify_with_result(
            title="System Self-Check", content="This is a TickerKeep system self-check test message.", bypass_quiet_hours=True)
        latency = int((time.monotonic() - t0) * 1000)
        ok = bool(result.get("success"))
        return _item("notify", f"nc:{channel.id}", name, _status_for(ok, latency), latency,
                     None if ok else (result.get("error") or "Send failed"))
    except Exception as e:
        return _item("notify", f"nc:{channel.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_db() -> dict:
    """对真实库执行 SELECT 1。"""
    from sqlalchemy import text

    from src.web.database import SessionLocal

    t0 = time.monotonic()
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        latency = int((time.monotonic() - t0) * 1000)
        return _item("system", "sys:db", "Database", _status_for(True, latency), latency)
    except Exception as e:
        return _item("system", "sys:db", "Database", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_disk() -> dict:
    """检查 data 目录所在盘的可用空间。"""
    import os
    import shutil

    from src.web.database import DB_PATH

    t0 = time.monotonic()
    try:
        data_dir = os.path.dirname(os.path.abspath(DB_PATH))
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        note = f"{free_gb:.1f}GB available / {total_gb:.1f}GB total"
        latency = int((time.monotonic() - t0) * 1000)
        if free_gb < 0.2:
            return _item("system", "sys:disk", "Disk Space", "fail", latency,
                         error=f"Severely low disk space ({note})", note=note)
        status = "slow" if free_gb < 1.0 else "ok"
        return _item("system", "sys:disk", "Disk Space", status, latency, note=note)
    except Exception as e:
        return _item("system", "sys:disk", "Disk Space", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_scheduler() -> dict:
    """经 scheduler_registry 看运行中的调度器;注册表空(CLI/未启动)→ 优雅跳过。"""
    from src.core import scheduler_registry

    regs = scheduler_registry.get_all()
    if not regs:
        return _item("system", "sys:scheduler", "Scheduler", "ok", 0,
                     note="No scheduler is running in this process (CLI self-checks skip this item).")
    running: list[str] = []
    stopped: list[str] = []
    jobs = 0
    for name, sched in regs.items():
        try:
            if getattr(sched, "running", False):
                running.append(name)
                jobs += len(sched.get_jobs())
            else:
                stopped.append(name)
        except Exception:
            stopped.append(name)
    if running:
        note = f"{len(running)} scheduler(s) running, {jobs} job(s) total"
        if stopped:
            note += f"; stopped: {', '.join(stopped)}"
        return _item("system", "sys:scheduler", "Scheduler", "ok", 0, note=note)
    return _item("system", "sys:scheduler", "Scheduler", "fail", 0,
                 error=f"Scheduler stopped: {', '.join(stopped)}")


async def _guard(coro, fallback: dict) -> dict:
    """给每个 probe 套超时;探测自身已 try/except,这里只兜超时/异常。"""
    try:
        return await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", PROBE_TIMEOUT_S * 1000, f"Probe timed out (>{PROBE_TIMEOUT_S}s)")
    except Exception as e:  # pragma: no cover - 防御
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", 0, str(e))


def _enumerate(db, include_system: bool = True) -> list[dict]:
    """枚举所有待检项(身份 + ORM 引用),不探测。include_system 加 DB/磁盘/调度 系统基础项。"""
    from src.web.models import AIModel, AIService, DataSource, NotifyChannel

    targets: list[dict] = []
    if include_system:
        targets.append({"category": "system", "key": "sys:db", "name": "Database", "group": None, "_kind": "db"})
        targets.append({"category": "system", "key": "sys:disk", "name": "Disk Space", "group": None, "_kind": "disk"})
        targets.append({"category": "system", "key": "sys:scheduler", "name": "Scheduler", "group": None, "_kind": "sched"})
    for src in db.query(DataSource).filter(DataSource.enabled.is_(True)).all():
        targets.append({"category": "datasource", "key": f"ds:{src.id}", "name": src.name,
                        "group": None, "_kind": "ds", "_obj": src})
    for model in db.query(AIModel).all():
        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if not service:
            continue
        # group = 服务商名,供前端做「服务商 → 模型」两级层级
        targets.append({"category": "ai", "key": f"ai:{model.id}", "name": model.name or model.model,
                        "group": service.name, "_kind": "ai", "_obj": model, "_service": service})
    for ch in db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all():
        targets.append({"category": "notify", "key": f"nc:{ch.id}", "name": ch.name or ch.type,
                        "group": None, "_kind": "nc", "_obj": ch})
    return targets


def _identity(t: dict) -> dict:
    return {"category": t["category"], "key": t["key"], "name": t["name"], "group": t.get("group")}


def _probe_for(t: dict, notify_send: bool):
    kind = t["_kind"]
    if kind == "db":
        return probe_db()
    if kind == "disk":
        return probe_disk()
    if kind == "sched":
        return probe_scheduler()
    if kind == "ds":
        return probe_datasource(t["_obj"])
    if kind == "ai":
        return probe_ai_model(t["_obj"], t["_service"])
    return probe_notify_channel(t["_obj"], send=notify_send)


def list_selfcheck_items(*, db=None, include_system: bool = True) -> list[dict]:
    """只枚举待检项身份(category/key/name/group),不探测;供前端先渲染列表再逐项检查。"""
    own = db is None
    db = db or SessionLocal()
    try:
        return [_identity(t) for t in _enumerate(db, include_system)]
    finally:
        if own:
            db.close()


async def run_selfcheck(*, db=None, notify_send: bool = False, keys=None, include_system: bool = True) -> dict:
    """探测待检项,返回看板。keys 非空时只探测这些 key(供前端逐项更新进度)。"""
    own = db is None
    db = db or SessionLocal()
    try:
        keyset = set(keys) if keys is not None else None
        targets = [t for t in _enumerate(db, include_system) if keyset is None or t["key"] in keyset]
        tasks = [_guard(_probe_for(t, notify_send), _identity(t)) for t in targets]
        items = list(await asyncio.gather(*tasks)) if tasks else []
        summary = {
            "total": len(items),
            "ok": sum(1 for i in items if i["status"] == "ok"),
            "slow": sum(1 for i in items if i["status"] == "slow"),
            "fail": sum(1 for i in items if i["status"] == "fail"),
        }
        return {"items": items, "summary": summary, "notify_send": bool(notify_send)}
    finally:
        if own:
            db.close()
