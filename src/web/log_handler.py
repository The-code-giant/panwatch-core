"""Custom logging handler that writes log entries to SQLite.

设计要点(这些约束是为了修一次"整个 web 服务被日志卡死"的故障,改动前请先读):

1. `emit()` 由 `logging.Handler.handle()` 在**持有 handler 全局锁**的情况下调用。
   任何在 emit() 里做的 DB I/O 都会让**所有**打日志的线程(包括 asyncio 事件循环)
   一起阻塞。所以 emit() 只做「append 到内存 buffer」,绝不碰数据库。
2. 真正的写库/清理都在单独的常驻后台线程里做,并且在**释放 buffer 锁之后**执行。
3. 后台线程自己产生的日志(sqlalchemy.engine 等)必须丢弃,否则 flush 会自我投喂。
"""

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import or_, text

from src.web.database import SessionLocal
from src.web.models import LogEntry

MAX_LOG_ENTRIES_TOTAL = 120_000
MAX_INFRA_LOG_ENTRIES = 30_000
MAX_BUFFERED_ENTRIES = 2_000
BUFFER_SIZE = 80
FLUSH_INTERVAL = 1.0  # seconds
CLEANUP_EVERY_FLUSHES = 10
MAX_DELETE_PER_PASS = 20_000  # 单次清理最多删多少行,避免一个巨型事务锁库
INFRA_LOGGER_PREFIXES = (
    "httpx",
    "httpcore",
    "urllib3",
    "uvicorn.access",
    "sqlalchemy.engine",
    # yfinance 在故障转移时会刷出海量 DEBUG(实测占了日志表的 58%),
    # 必须纳入 infra 配额,否则它会把业务日志挤出全局上限。
    "yfinance",
    "peewee",
)

_ACTIVE_HANDLER = None

# 后台 flush 线程自产的日志不能再进 buffer,否则写库时 sqlalchemy 的日志
# 会被本 handler 收走 → 下一轮 flush 又产生日志 → 无限自我投喂。
_IN_FLUSH = threading.local()


def get_log_handler_stats() -> dict:
    """Get runtime health stats of DB log handler."""
    h = _ACTIVE_HANDLER
    if not h:
        return {
            "enabled": False,
            "pending_entries": 0,
            "dropped_entries": 0,
            "flush_errors": 0,
            "last_flush_error": "",
            "last_flush_at": "",
        }
    with h._lock:
        return {
            "enabled": True,
            "pending_entries": len(h._buffer),
            "dropped_entries": h._dropped_entries,
            "flush_errors": h._flush_errors,
            "last_flush_error": h._last_flush_error,
            "last_flush_at": h._last_flush_at.isoformat() if h._last_flush_at else "",
        }


class DBLogHandler(logging.Handler):
    """Buffered logging handler that writes to the log_entries table.

    emit() 是纯内存操作;写库由 `_worker` 常驻线程负责。
    """

    def __init__(self, level=logging.DEBUG):
        super().__init__(level)
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._dropped_entries = 0
        self._flush_errors = 0
        self._last_flush_error = ""
        self._last_flush_at = None
        self._flush_count = 0
        global _ACTIVE_HANDLER
        _ACTIVE_HANDLER = self
        # 常驻单线程,而不是每秒 new 一个 threading.Timer(旧实现会无限造线程)。
        self._thread = threading.Thread(
            target=self._worker, name="DBLogHandlerFlush", daemon=True
        )
        self._thread.start()

    # ---------- 热路径:必须廉价 ----------

    def emit(self, record: logging.LogRecord):
        if getattr(_IN_FLUSH, "active", False):
            return  # flush 线程自产日志,丢弃以免自我投喂
        try:
            tags = getattr(record, "tags", {})
            if not isinstance(tags, dict):
                tags = {}
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc),
                "level": record.levelname,
                # Persist original module logger name; UI maps to Chinese for display
                "logger_name": getattr(record, "name", ""),
                "message": self.format(record),
                "trace_id": str(getattr(record, "trace_id", "") or "")[:64],
                "run_id": str(getattr(record, "run_id", "") or "")[:64],
                "agent_name": str(getattr(record, "agent_name", "") or "")[:64],
                "event": str(getattr(record, "event", "") or "")[:64],
                "tags": tags,
                "notify_status": str(getattr(record, "notify_status", "") or "")[:32],
                "notify_reason": str(getattr(record, "notify_reason", "") or "")[:255],
            }
            with self._lock:
                if len(self._buffer) >= MAX_BUFFERED_ENTRIES:
                    overflow = len(self._buffer) - MAX_BUFFERED_ENTRIES + 1
                    del self._buffer[:overflow]
                    self._dropped_entries += overflow
                self._buffer.append(entry)
                should_wake = (
                    record.levelno >= logging.ERROR or len(self._buffer) >= BUFFER_SIZE
                )
            # 只唤醒后台线程,写库不在这里做(见模块 docstring 第 1 条)。
            if should_wake:
                self._wake.set()
        except Exception:
            # Avoid recursion if logging path fails
            pass

    # ---------- 后台线程 ----------

    def _worker(self):
        while not self._stopping.is_set():
            self._wake.wait(FLUSH_INTERVAL)
            self._wake.clear()
            self._flush()

    def _take_batch(self) -> list[dict]:
        with self._lock:
            if not self._buffer:
                return []
            entries = self._buffer
            self._buffer = []
            return entries

    def _flush(self):
        entries = self._take_batch()
        if not entries:
            return
        _IN_FLUSH.active = True
        try:
            db = SessionLocal()
            try:
                db.bulk_insert_mappings(LogEntry, entries)
                db.commit()
                self._last_flush_at = datetime.now(timezone.utc)
                self._flush_count += 1
                if self._flush_count % CLEANUP_EVERY_FLUSHES == 0:
                    self._cleanup(db)
            finally:
                db.close()
        except Exception as e:
            self._flush_errors += 1
            self._last_flush_error = str(e)[:500]
        finally:
            _IN_FLUSH.active = False

    def _cleanup(self, db):
        """Retention policy: prioritize preserving business logs.

        用带 LIMIT 的子查询删除,避免把几万个 id 拉回 Python 再拼成巨型 IN (...)。
        """
        # 1) cap infrastructure noise first
        infra_filters = [LogEntry.logger_name.startswith(p) for p in INFRA_LOGGER_PREFIXES]
        infra_count = db.query(LogEntry).filter(or_(*infra_filters)).count()
        if infra_count > MAX_INFRA_LOG_ENTRIES:
            overflow = min(infra_count - MAX_INFRA_LOG_ENTRIES, MAX_DELETE_PER_PASS)
            like_sql = " OR ".join(
                f"logger_name LIKE '{p}%'" for p in INFRA_LOGGER_PREFIXES
            )
            db.execute(
                text(
                    f"DELETE FROM log_entries WHERE id IN ("
                    f"SELECT id FROM log_entries WHERE {like_sql} "
                    f"ORDER BY id ASC LIMIT :n)"
                ),
                {"n": overflow},
            )
            db.commit()

        # 2) global hard cap
        total = db.query(LogEntry).count()
        if total > MAX_LOG_ENTRIES_TOTAL:
            db.execute(
                text(
                    "DELETE FROM log_entries WHERE id <= ("
                    "SELECT id FROM log_entries ORDER BY id DESC LIMIT 1 OFFSET :cap)"
                ),
                {"cap": MAX_LOG_ENTRIES_TOTAL},
            )
            db.commit()

    def close(self):
        self._stopping.set()
        self._wake.set()
        try:
            self._thread.join(timeout=5)
        except Exception:
            pass
        self._flush()
        super().close()
