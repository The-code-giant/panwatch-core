"""PanWatch 统一服务入口 - Web 后台 + Agent 调度"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn

from src.web.database import init_db, SessionLocal
from src.web.models import (
    AgentConfig,
    Stock,
    StockAgent,
    AIService,
    AIModel,
    NotifyChannel,
    AppSettings,
    DataSource,
    EntryCandidate,
    MarketScanSnapshot,
    StrategySignalRun,
)
from src.web.log_handler import DBLogHandler
from src.config import Settings, AppConfig, StockConfig
from src.models.market import MarketCode, is_enabled
from src.core.ai_client import AIClient
from src.core.ai_failover import build_failover_client
from src.core.notifier import NotifierManager
from src.core.scheduler import AgentScheduler
from src.core.price_alert_scheduler import PriceAlertScheduler
from src.core.paper_trading_scheduler import PaperTradingScheduler
from src.core.context_scheduler import ContextMaintenanceScheduler
from src.core.agent_runs import record_agent_run
from src.core.log_context import install_log_record_factory, log_context
from src.core.agent_catalog import (
    AGENT_SEED_SPECS,
    AGENT_KIND_WORKFLOW,
)
from src.core.strategy_catalog import ensure_strategy_catalog
from src.agents.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
from src.agents.daily_report import DailyReportAgent
from src.agents.news_digest import NewsDigestAgent
from src.agents.chart_analyst import ChartAnalystAgent
from src.agents.intraday_monitor import IntradayMonitorAgent
from src.agents.premarket_outlook import PremarketOutlookAgent
from src.agents.tradingagents import TradingAgentsAgent

logger = logging.getLogger(__name__)

# 全局 scheduler 实例，供 agents API 调用
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None
paper_trading_scheduler: PaperTradingScheduler | None = None
context_maintenance_scheduler: ContextMaintenanceScheduler | None = None


def _schedulers_disabled() -> bool:
    """True if env var DISABLE_SCHEDULERS is set to a truthy value (1/true/yes,
    case-insensitive). Used at startup to skip starting every scheduler, for
    fixture/UI verification runs that must not touch real data or run agents."""
    return os.environ.get("DISABLE_SCHEDULERS", "").strip().lower() in ("1", "true", "yes")


def apply_proxy_env(proxy: str | None) -> None:
    """统一更新进程环境变量代理,让所有 httpx 默认 Client (trust_env=True) 走该代理。

    传空字符串 / None 时清除环境变量(取消代理)。
    NO_PROXY 默认含 localhost / 回环地址,避免本地访问绕一圈。
    """
    p = (proxy or "").strip()
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        logger.info(f"HTTP/HTTPS 代理已应用: {p}")
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        logger.info("HTTP/HTTPS 代理已清除")


def setup_proxy():
    """启动时把已配置的 HTTP 代理桥接到环境变量。

    优先级:
    1. 已存在的 HTTP_PROXY / HTTPS_PROXY 环境变量(用户显式覆盖,不动)
    2. app_settings.http_proxy(UI 配置)
    3. .env 中的 http_proxy(Settings.http_proxy)
    """
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        logger.info(
            f"沿用现有环境变量代理: HTTP_PROXY={os.environ.get('HTTP_PROXY', '')} "
            f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY', '')}"
        )
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        return

    proxy = ""
    try:
        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            if setting and setting.value:
                proxy = setting.value.strip()
        finally:
            db.close()
    except Exception:
        pass

    if not proxy:
        proxy = (Settings().http_proxy or "").strip()

    if proxy:
        apply_proxy_env(proxy)


def setup_ssl():
    """设置 SSL 证书环境（企业代理环境）"""
    settings = Settings()
    ca_cert = settings.ca_cert_file
    if not ca_cert or not os.path.exists(ca_cert):
        return

    import certifi

    bundle_path = os.path.join(os.path.dirname(__file__), "data", "ca-bundle.pem")
    os.makedirs(os.path.dirname(bundle_path), exist_ok=True)

    need_rebuild = not os.path.exists(bundle_path) or os.path.getmtime(
        ca_cert
    ) > os.path.getmtime(bundle_path)

    if need_rebuild:
        with open(bundle_path, "w") as out:
            with open(certifi.where(), "r") as f:
                out.write(f.read())
            out.write("\n")
            with open(ca_cert, "r") as f:
                out.write(f.read())

    os.environ["SSL_CERT_FILE"] = bundle_path
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_path
    logger.info(f"SSL 证书已加载: {bundle_path}")


def setup_logging():
    """配置日志: 控制台 + 数据库

    分级策略:
    - root logger 始终 DEBUG,所有日志都会传播到 handler
    - 控制台 handler 按 LOG_LEVEL 过滤(默认 INFO),并丢弃 httpx 等三方库的 < WARNING 噪音
    - DB handler 始终 DEBUG 全量收录,UI 日志板永远可以看到包括心跳/httpx 请求在内的完整记录
    """
    console_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    console_level = getattr(logging, console_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    install_log_record_factory()

    # reload/server restart 时避免重复 handler 导致日志放大。
    for h in list(root.handlers):
        if isinstance(h, DBLogHandler) or getattr(h, "_panwatch_console", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # 控制台输出: 按 LOG_LEVEL 过滤,且丢弃三方库的低级别噪音
    console = logging.StreamHandler()
    console._panwatch_console = True  # type: ignore[attr-defined]
    console.setLevel(console_level)
    console.addFilter(_ConsoleNoiseFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # 数据库持久化: 始终全量收录,UI 日志板可查 DEBUG
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)

    # uvicorn 默认给自己挂了 stderr handler 并且 propagate=False,导致 access log
    # 走自己的链路(`INFO: 127.0.0.1 - "GET /api/..."`)不被我们的 filter 拦截。
    # 改成清空自己的 handler + propagate 到 root,让 _ConsoleNoiseFilter 生效。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)

    # 三方库的 DEBUG 是库内部 trace,不是业务日志。实测 yfinance 一家就占了 log_entries
    # 的 58%(故障转移时疯狂重试),连同 httpcore 的 26k 条把业务日志挤出全局上限,
    # 并让每一轮 flush 都触发一次清理 → SQLite 写锁争用。默认压到 INFO;
    # 需要排查库内部行为时设 LOG_LIB_DEBUG=1 恢复。
    if os.environ.get("LOG_LIB_DEBUG", "").lower() not in ("1", "true", "yes"):
        for name in ("yfinance", "peewee", "httpcore", "httpx", "urllib3", "matplotlib"):
            logging.getLogger(name).setLevel(logging.INFO)


class _ConsoleNoiseFilter(logging.Filter):
    """控制台 handler 过滤器: 三方库的 INFO/DEBUG 不进 stdout,WARNING+ 仍然显示。
    DB handler 不挂这个过滤器,UI 日志板能看到完整请求记录。

    uvicorn.access 是每条请求的 access log(`INFO: 127.0.0.1 - "GET /api/..." 200 OK`),
    属于底层心跳;uvicorn / uvicorn.error 是应用级日志(启动、报错),保留。"""

    _NOISY_PREFIXES = ("httpx", "httpcore", "urllib3", "apscheduler", "uvicorn.access")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        name = record.name or ""
        for prefix in self._NOISY_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                return False
        return True


def seed_sample_stocks():
    """首次启动时添加示例股票"""
    db = SessionLocal()
    try:
        # 只在没有任何股票时才添加示例
        if db.query(Stock).count() > 0:
            return

        samples = [
            {"symbol": "AAPL", "name": "Apple", "market": "US"},
            {"symbol": "NVDA", "name": "NVIDIA", "market": "US"},
            {"symbol": "MSFT", "name": "Microsoft", "market": "US"},
            {"symbol": "SHOP.TO", "name": "Shopify", "market": "CA"},
            {"symbol": "RY.TO", "name": "Royal Bank of Canada", "market": "CA"},
            {"symbol": "TD.TO", "name": "Toronto-Dominion Bank", "market": "CA"},
        ]
        samples = [s for s in samples if is_enabled(s["market"])]
        for s in samples:
            db.add(Stock(**s))
        db.commit()
        logger.info(f"Seeded {len(samples)} sample stocks (first start)")
    finally:
        db.close()


def canonicalise_us_symbols():
    """Startup migration: fold legacy US spellings (Eastmoney ``BRK_B``, Yahoo ``BRK-B``) into the
    exchange form ``BRK.B`` in ``stocks``, ``entry_candidates`` and ``market_scan_snapshots``.
    Idempotent; a row whose canonical twin already exists is deleted rather than duplicated."""
    from marketdata import canonical_code

    db = SessionLocal()
    try:
        total = 0
        for model, sym_col, mkt_col, label in (
            (Stock, "symbol", "market", "stocks"),
            (EntryCandidate, "stock_symbol", "stock_market", "entry_candidates"),
            (MarketScanSnapshot, "stock_symbol", "stock_market", "market_scan_snapshots"),
        ):
            rows = db.query(model).filter(getattr(model, mkt_col) == "US").all()
            changed = 0
            seen: set[tuple] = set()
            for r in rows:
                raw = getattr(r, sym_col) or ""
                canon = canonical_code(raw, "US")
                if not canon:
                    continue
                key_extra = tuple(
                    getattr(r, c) for c in ("snapshot_date",) if hasattr(r, c)
                )
                if canon == raw:
                    seen.add((canon,) + key_extra)
                    continue
                twin = (canon,) + key_extra
                if twin in seen or db.query(model).filter(
                    getattr(model, sym_col) == canon, getattr(model, mkt_col) == "US",
                    *([getattr(model, "snapshot_date") == r.snapshot_date] if hasattr(model, "snapshot_date") else []),
                ).first() is not None:
                    db.delete(r)
                else:
                    setattr(r, sym_col, canon)
                    seen.add(twin)
                changed += 1
            if changed:
                db.commit()
                total += changed
                logger.info(f"{label}: canonicalised {changed} US symbols")
        if total:
            logger.info(f"US symbol canonicalisation done: {total} rows")
    finally:
        db.close()


def backfill_stock_names_english():
    """启动时补齐 stocks.name 为英文：与 seed_agents() 中「已存在也始终同步」的做法一致，
    对每一行都用 _english_name() 重新计算 name，不一致才写回；幂等，可安全每次启动重跑。"""
    from src.web.stock_list import _english_name

    db = SessionLocal()
    try:
        stocks = db.query(Stock).all()
        changed = 0
        for s in stocks:
            new_name = _english_name(s.symbol, s.name or "")
            if new_name != s.name:
                s.name = new_name
                changed += 1
        if changed:
            db.commit()
            logger.info(f"已将 {changed} 条股票名称回填为英文")
    finally:
        db.close()


def backfill_candidate_names_english():
    """启动时补齐 entry_candidates / market_scan_snapshots / strategy_signal_runs 的 stock_name 为英文。
    这三张表由每日市场扫描/策略计算持续重写，写入路径已用 _english_name() 兜底（见
    entry_candidates.py / strategy_engine.py），此处只负责把「已存在」的旧行一次性纠正；
    幂等，可安全每次启动重跑，不会与写入路径冲突。
    注意:优先查覆盖表本身(而非只调用 _english_name),这样即使某行早先已回退成裸代码
    (不含中文,_english_name 不会再触发覆盖),之后补充的覆盖表新条目也能在重跑时补齐。"""
    from src.core import universe
    from src.web.stock_list import _english_name

    def _best_name(symbol: str, current: str) -> str:
        # Prefer the directory name when the row is a bare symbol or still carries CJK.
        if not current or current.upper() == (symbol or "").upper() or _english_name(symbol, current) != current:
            listed = universe.lookup_name(symbol)
            if listed:
                return listed
        return _english_name(symbol, current)

    db = SessionLocal()
    try:
        total_changed = 0
        for model, label in (
            (EntryCandidate, "entry_candidates"),
            (MarketScanSnapshot, "market_scan_snapshots"),
            (StrategySignalRun, "strategy_signal_runs"),
        ):
            rows = db.query(model).all()
            changed = 0
            for r in rows:
                new_name = _best_name(r.stock_symbol, r.stock_name or r.stock_symbol)
                if new_name != r.stock_name:
                    r.stock_name = new_name
                    changed += 1
            if changed:
                db.commit()
                total_changed += changed
            logger.info(f"{label}: 已回填 {changed} 条股票名称为英文")
        if total_changed:
            logger.info(f"候选池/信号表股票名称英文回填完成,共 {total_changed} 条")
    finally:
        db.close()


def seed_agents():
    """初始化内置 Agent 配置"""
    db = SessionLocal()
    for spec in AGENT_SEED_SPECS:
        existing = db.query(AgentConfig).filter(AgentConfig.name == spec.name).first()
        if not existing:
            db.add(
                AgentConfig(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    kind=spec.kind,
                    visible=spec.visible,
                    lifecycle_status=spec.lifecycle_status,
                    replaced_by=spec.replaced_by,
                    display_order=spec.display_order,
                    enabled=spec.enabled,
                    schedule=spec.schedule,
                    execution_mode=spec.execution_mode,
                    config=spec.config or {},
                )
            )
        else:
            # 始终同步 execution_mode（确保代码中的定义生效）
            existing.execution_mode = spec.execution_mode or "batch"
            # 同步 display_name 和 description
            existing.display_name = spec.display_name or existing.display_name
            existing.description = spec.description or existing.description
            existing.kind = spec.kind
            existing.visible = bool(spec.visible)
            existing.lifecycle_status = spec.lifecycle_status or "active"
            existing.replaced_by = spec.replaced_by or ""
            existing.display_order = int(spec.display_order or 0)

            # capability 强制不参与调度，避免旧配置继续触发。
            if spec.kind != AGENT_KIND_WORKFLOW:
                existing.enabled = False
                existing.schedule = ""

            # 仅在用户未配置时补齐默认 config
            if spec.config and (not existing.config):
                existing.config = spec.config
            # 对已存在配置做“向前兼容”的字段补齐（不覆盖用户已有值）
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


# Data-source seed rows (shared by seed_data_sources / reconcile_data_sources).
# Upsert-only target keyed by (type, provider); orphan deletion lives in reconcile_data_sources.
# Two tables from docs/data-sources: price data (quote / kline / chart) and the information
# layer (news, headlines, events, fundamentals, dividends, holders, filings). Test symbols
# are AAPL (US) and SHOP.TO (Canada). SEC rows carry ``contact_email``; Yahoo rows carry ``proxy``.
DATA_SOURCE_SEEDS: list[dict] = [
        # ---- Price data --------------------------------------------------------------
        {
            "name": "Yahoo Finance Quotes",
            "type": "quote",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Yahoo Finance real-time quotes for US, Canada, crypto and gold (no key); enriched with name, P/E and market cap from the ticker profile.",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Tencent Quotes",
            "type": "quote",
            "provider": "tencent",
            "config": {
                "description": "Tencent qt.gtimg quotes: US fallback (legacy CN/HK), no key.",
            },
            "enabled": True,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Sina Quotes",
            "type": "quote",
            "provider": "sina",
            "config": {
                "description": "Sina real-time quotes: US last resort (legacy HK), no key, no proxy.",
            },
            "enabled": True,
            "priority": 20,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "EastMoney Quotes",
            "type": "quote",
            "provider": "eastmoney",
            "config": {
                "description": "EastMoney push2 quotes (legacy CN only; unreachable for US/CA symbols).",
            },
            "enabled": True,
            "priority": 30,
            "supports_batch": False,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Yahoo Finance Daily Bars",
            "type": "kline",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Yahoo Finance daily bars via the yfinance library (US, Canada, crypto, gold; no key). Primary kline source.",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Yahoo Chart API Daily Bars",
            "type": "kline",
            "provider": "yahoo",
            "config": {
                "proxy": "",
                "description": "Raw Yahoo chart v8 daily bars (US, Canada, crypto, gold; no key, no crumb). Last resort when the yfinance library fails.",
            },
            "enabled": True,
            "priority": 10,
            "supports_batch": False,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Tencent Daily Bars",
            "type": "kline",
            "provider": "tencent",
            "config": {
                "description": "Tencent daily bars: US fallback (legacy CN/HK), no key.",
            },
            "enabled": True,
            "priority": 20,
            "supports_batch": False,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "EastMoney Daily Bars",
            "type": "kline",
            "provider": "eastmoney",
            "config": {
                "description": "EastMoney daily bars (legacy CN/HK only; unreachable for US/CA symbols).",
            },
            "enabled": True,
            "priority": 30,
            "supports_batch": False,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "PanWatch Chart Renderer",
            "type": "chart",
            "provider": "panwatch",
            "config": {
                "bars": 160,
                "width": 1280,
                "height": 900,
                "dpi": 100,
                "description": "Candlestick charts rendered from PanWatch's own daily bars (matplotlib): MA20/MA50, Bollinger, volume, RSI, MACD. No browser, no external site.",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        # ---- Information layer -------------------------------------------------------
        {
            "name": "Yahoo Finance News",
            "type": "news",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Company news and press releases from Yahoo Finance (US, Canada, crypto, gold; no key).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Google News RSS",
            "type": "news",
            "provider": "google_news",
            "config": {
                "description": "Google News RSS search by company name (US and Canada editions; no key).",
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": False,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "CNBC Top News",
            "type": "flash_news",
            "provider": "cnbc",
            "config": {
                "feed_url": "",
                "description": "CNBC top news RSS feed (US market headlines).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "MarketWatch MarketPulse",
            "type": "flash_news",
            "provider": "marketwatch",
            "config": {
                "feed_url": "",
                "description": "MarketWatch MarketPulse RSS feed (US market headlines).",
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "Financial Post",
            "type": "flash_news",
            "provider": "financial_post",
            "config": {
                "feed_url": "",
                "description": "Financial Post RSS feed (Canadian market headlines).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "BNN Bloomberg",
            "type": "flash_news",
            "provider": "bnn_bloomberg",
            "config": {
                "feed_url": "",
                "description": "BNN Bloomberg RSS feed (Canadian market headlines).",
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "Globe and Mail Business",
            "type": "flash_news",
            "provider": "globe_and_mail",
            "config": {
                "feed_url": "",
                "description": "Globe and Mail business RSS feed (Canada). Disabled by default; verify the feed URL before enabling.",
            },
            "enabled": False,
            "priority": 10,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "Investing.com Stock News",
            "type": "flash_news",
            "provider": "investing_com",
            "config": {
                "feed_url": "",
                "description": "Investing.com stock-market news RSS (US and Canada). Disabled by default fallback.",
            },
            "enabled": False,
            "priority": 20,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "Yahoo Finance Calendar",
            "type": "events",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Earnings dates (past and upcoming, with EPS estimate and surprise), ex-dividend dates and splits from Yahoo Finance (US, Canada).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Nasdaq Earnings Calendar",
            "type": "events",
            "provider": "nasdaq",
            "config": {
                "description": "Nasdaq.com earnings calendar (US only, browser User-Agent required). Disabled by default fallback.",
            },
            "enabled": False,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Yahoo Finance Fundamentals",
            "type": "fundamentals",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Valuation and financial snapshot from the Yahoo Finance ticker profile (US, Canada): P/E, P/B, market cap, margins, growth, cash flow.",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "SEC EDGAR XBRL",
            "type": "fundamentals",
            "provider": "sec_edgar",
            "config": {
                "contact_email": "",
                "description": "Latest 10-K/10-Q facts from SEC EDGAR company facts (US only). SEC requires a descriptive User-Agent with a contact address.",
            },
            "enabled": True,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Yahoo Finance Dividends",
            "type": "dividend",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Dividend history and the next announced ex-dividend date from Yahoo Finance (US, Canada).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "Yahoo Finance Holders",
            "type": "holders",
            "provider": "yfinance",
            "config": {
                "proxy": "",
                "description": "Ownership breakdown, top institutional holders and insider transactions from Yahoo Finance (US, Canada).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL", "SHOP.TO"],
        },
        {
            "name": "SEC EDGAR Form 4",
            "type": "holders",
            "provider": "sec_edgar",
            "config": {
                "contact_email": "",
                "description": "Insider transactions from SEC EDGAR Form 4 filings (US only).",
            },
            "enabled": True,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "SEC EDGAR Filings",
            "type": "filings",
            "provider": "sec_edgar",
            "config": {
                "contact_email": "",
                "description": "Recent filings (8-K, 10-K, 10-Q, Form 4, 13D/G, S-1, DEF 14A, 6-K, 20-F, 40-F) from SEC EDGAR (US only).",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Newswire Press Releases (Canada)",
            "type": "filings",
            "provider": "yfinance_newswire",
            "config": {
                "proxy": "",
                "description": "Company press releases (CNW, GlobeNewswire) via Yahoo Finance for Canadian listings. SEDAR+ offers no free API.",
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": True,
            "test_symbols": ["SHOP.TO"],
        },
]


# Kept as quote/kline fallbacks after the English migration; never the primary for US/CA.
_LEGACY_FALLBACK_PROVIDERS = frozenset({"tencent", "sina", "eastmoney", "yahoo"})


def seed_data_sources(db=None) -> list[dict]:
    """初始化预置数据源(按 type+provider 只增不删的 upsert)。

    db 为 None 时自建独立 session 并自行 commit/close(兼容旧调用方式);
    传入 db 时复用调用方 session,不 commit/close,交由调用方统一处理
    (供 reconcile_data_sources 在同一事务里接着做删孤儿)。

    匹配键用 (type, provider) 而非 name:type+provider 才是数据源的稳定身份，
    name 只是展示文案，需要能在代码里改文案(如中文改英文)时也同步覆盖旧行的
    display name，而不是被当成"新种子"插出一条重复记录(与 seed_agents() 里
    「已存在也始终同步 display 字段」的做法一致)。

    返回本次新增(缺失被补齐)的种子记录摘要列表 [{"name","type","provider"}, ...]。
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    seeded_missing: list[dict] = []
    for source_data in DATA_SOURCE_SEEDS:
        existing = (
            db.query(DataSource)
            .filter(
                DataSource.type == source_data["type"],
                DataSource.provider == source_data["provider"],
            )
            .first()
        )
        if existing:
            # 始终同步展示名(代码里改了文案要覆盖旧行,而不是留着旧值)
            if existing.name != source_data["name"]:
                existing.name = source_data["name"]
            # 更新已存在记录的新字段（保留用户可能修改的配置）
            if existing.supports_batch != source_data.get("supports_batch", False):
                existing.supports_batch = source_data.get("supports_batch", False)
            if not existing.test_symbols:  # 只在空时更新
                existing.test_symbols = source_data.get("test_symbols", [])
            # Upgraded databases carry the pre-migration seed order (tencent 0, yfinance 10),
            # which makes Tencent the primary for US symbols. Move rows toward the seed order
            # only in the direction that restores the yfinance primary: raise legacy fallbacks
            # (tencent/sina/eastmoney/yahoo) that sit below their seed value, lower a yfinance
            # row that sits above its seed value. A user demotion of a fallback is kept as-is.
            if (
                source_data["provider"] in _LEGACY_FALLBACK_PROVIDERS
                and existing.priority < source_data["priority"]
            ) or (
                source_data["provider"] == "yfinance"
                and existing.priority > source_data["priority"]
            ):
                existing.priority = source_data["priority"]
        else:
            db.add(DataSource(**source_data))
            seeded_missing.append(
                {
                    "name": source_data["name"],
                    "type": source_data["type"],
                    "provider": source_data["provider"],
                }
            )

    if owns_session:
        db.commit()
        db.close()

    return seeded_missing


def _seed_providers_by_type() -> dict[str, set[str]]:
    """从 DATA_SOURCE_SEEDS 推导每个 type 当前合法的 provider 集合。"""
    result: dict[str, set[str]] = {}
    for source_data in DATA_SOURCE_SEEDS:
        result.setdefault(source_data["type"], set()).add(source_data["provider"])
    return result


def _dedupe_data_sources(db) -> list[dict]:
    """按 (type, provider) 折叠重复行,只保留 id 最小的一条(最早写入/最可能带用户状态)。

    历史上 seed_data_sources() 曾按 (name, provider) 匹配旧行;当 name 文案在代码里改动
    (如中文种子改英文)而旧行还没来得及被同一次 upsert 覆盖时,会把旧行当成"缺失种子"
    多插一条,导致 (type, provider) 出现重复。现在匹配键已改为 (type, provider) 本身
    不会再产生新的重复,但这里加一道自愈:每次启动检查一遍,把历史遗留的重复行清掉。
    幂等:没有重复时什么也不做。
    """
    rows = db.query(DataSource).order_by(DataSource.type, DataSource.provider, DataSource.id).all()
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault((row.type, row.provider), []).append(row)

    removed: list[dict] = []
    for (dtype, provider), group_rows in groups.items():
        if len(group_rows) <= 1:
            continue
        keep, *extras = group_rows  # 已按 id 升序排列,第一条即最小 id
        for extra in extras:
            removed.append(
                {"id": extra.id, "kept_id": keep.id, "type": dtype, "provider": provider, "name": extra.name}
            )
            db.delete(extra)
    return removed


def reconcile_data_sources(db) -> dict:
    """数据源表温和对账:去重 + 补缺失默认 + 删孤儿,保留用户有效自定义/凭证。

    孤儿判定: legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 内该 type 的 provider 集合;
    DB 行 (type, provider) 不在 legal(type) 内即孤儿。news/chart 等非引擎类型(包内集合为空)的合法性完全由 seed 决定。

    只删孤儿行/重复行,其余行(含用户改过 config/priority/enabled 的自定义行)原样保留。
    """
    from marketdata import PACKAGE_VENDORS_BY_TYPE

    deduped = _dedupe_data_sources(db)
    seeded_missing = seed_data_sources(db)
    seed_providers_by_type = _seed_providers_by_type()

    deleted: list[dict] = []
    for row in db.query(DataSource).all():
        legal = PACKAGE_VENDORS_BY_TYPE.get(row.type, frozenset()) | seed_providers_by_type.get(row.type, set())
        if row.provider not in legal:
            deleted.append(
                {"id": row.id, "type": row.type, "provider": row.provider, "name": row.name}
            )
            db.delete(row)

    if deduped:
        logger.info(f"数据源对账: 折叠重复数据源 {len(deduped)} 条: {deduped}")
    if seeded_missing:
        logger.info(f"数据源对账: 补齐缺失默认 {len(seeded_missing)} 条: {seeded_missing}")
    if deleted:
        logger.info(f"数据源对账: 删除孤儿数据源 {len(deleted)} 条: {deleted}")

    db.commit()
    return {"deduped": deduped, "deleted": deleted, "seeded_missing": seeded_missing}


def seed_strategies():
    """初始化策略目录。"""
    ensure_strategy_catalog()
    logger.info("策略目录初始化完成")


def load_watchlist_for_agent(agent_name: str) -> list[StockConfig]:
    """从数据库加载某个 Agent 关联的自选股"""
    db = SessionLocal()
    try:
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = [sa.stock_id for sa in stock_agents]
        if not stock_ids:
            return []

        # 绑定优先：只要绑定了 Agent，就纳入执行范围
        stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
        result = []
        skipped = 0
        for s in stocks:
            try:
                market = MarketCode(s.market)
            except ValueError:
                # Unknown market code: never silently reclassify (e.g. as CN); skip it.
                skipped += 1
                continue
            if not is_enabled(market):
                # Retired/disabled market: exclude from scheduled agent inputs.
                skipped += 1
                continue
            result.append(
                StockConfig(
                    symbol=s.symbol,
                    name=s.name,
                    market=market,
                )
            )
        if skipped:
            logger.debug(
                f"load_watchlist_for_agent({agent_name}): skipped {skipped} stock(s) "
                "with unknown or disabled markets"
            )
        return result
    finally:
        db.close()


def load_portfolio_for_agent(agent_name: str) -> PortfolioInfo:
    """从数据库加载某个 Agent 关联股票的持仓信息（包括多账户）"""
    from src.web.models import Account, Position

    db = SessionLocal()
    try:
        # 获取 Agent 关联的股票 ID
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = set(sa.stock_id for sa in stock_agents)
        if not stock_ids:
            return PortfolioInfo()

        # 获取所有启用的账户
        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            # 获取该账户中属于关联股票的持仓
            positions = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id.in_(stock_ids),
                )
                .all()
            )

            position_infos = []
            for pos in positions:
                stock = pos.stock
                if not stock:
                    continue
                raw_market = stock.market
                try:
                    market = MarketCode(raw_market)
                except ValueError:
                    # Unknown market code: never silently reclassify (e.g. as CN);
                    # keep the position (totals stay correct) but mark it unsupported.
                    market = MarketCode.UNKNOWN

                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                        market_code=raw_market,
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def load_portfolio_for_stock(stock_id: int) -> PortfolioInfo:
    """从数据库加载单只股票的持仓信息"""
    from src.web.models import Account, Position

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return PortfolioInfo()

        raw_market = stock.market
        try:
            market = MarketCode(raw_market)
        except ValueError:
            # Unknown market code: never silently reclassify (e.g. as CN);
            # keep the position (totals stay correct) but mark it unsupported.
            market = MarketCode.UNKNOWN

        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id == stock_id,
                )
                .first()
            )

            position_infos = []
            if pos:
                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                        market_code=raw_market,
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def _get_proxy() -> str:
    """从 app_settings 获取 http_proxy"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def _get_app_setting(key: str) -> str:
    """从 app_settings 获取配置（不存在返回空字符串）"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == key).first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def resolve_ai_model(
    agent_name: str, stock_agent_id: int | None = None
) -> tuple[AIModel | None, AIService | None]:
    """解析 AI 模型: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)
    返回 (model, service) 元组"""
    db = SessionLocal()
    try:
        model_id = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.ai_model_id:
                model_id = sa.ai_model_id

        # 2. agent 级别默认
        if not model_id:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.ai_model_id:
                model_id = agent.ai_model_id

        # 3. 系统默认
        if not model_id:
            default_model = db.query(AIModel).filter(AIModel.is_default == True).first()
            if default_model:
                model_id = default_model.id

        # 4. 回退：取第一个
        if not model_id:
            first_model = db.query(AIModel).first()
            if first_model:
                model_id = first_model.id

        if not model_id:
            return None, None

        model = db.query(AIModel).filter(AIModel.id == model_id).first()
        if not model:
            return None, None

        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if model:
            db.expunge(model)
        if service:
            db.expunge(service)
        return model, service
    finally:
        db.close()


def resolve_notify_channels(
    agent_name: str, stock_agent_id: int | None = None
) -> list[NotifyChannel]:
    """解析通知渠道: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)"""
    db = SessionLocal()
    try:
        channel_ids = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.notify_channel_ids:
                channel_ids = sa.notify_channel_ids

        # 2. agent 级别默认
        if channel_ids is None:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.notify_channel_ids:
                channel_ids = agent.notify_channel_ids

        # 3. 按 id 列表查询或取系统默认
        if channel_ids:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.id.in_(channel_ids),
                    NotifyChannel.enabled == True,
                )
                .all()
            )
        else:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.is_default == True,
                    NotifyChannel.enabled == True,
                )
                .all()
            )

        for ch in channels:
            db.expunge(ch)
        return channels
    finally:
        db.close()


def _build_notifier(channels: list[NotifyChannel]) -> NotifierManager:
    """根据解析后的渠道列表构建 NotifierManager"""
    settings = Settings()
    # allow UI override via app_settings
    quiet_hours = _get_app_setting("notify_quiet_hours") or settings.notify_quiet_hours
    retry_attempts_raw = _get_app_setting("notify_retry_attempts")
    backoff_raw = _get_app_setting("notify_retry_backoff_seconds")
    overrides_raw = (
        _get_app_setting("notify_dedupe_ttl_overrides")
        or settings.notify_dedupe_ttl_overrides
    )

    try:
        retry_attempts = (
            int(retry_attempts_raw)
            if retry_attempts_raw
            else settings.notify_retry_attempts
        )
    except Exception:
        retry_attempts = settings.notify_retry_attempts
    try:
        retry_backoff_seconds = (
            float(backoff_raw) if backoff_raw else settings.notify_retry_backoff_seconds
        )
    except Exception:
        retry_backoff_seconds = settings.notify_retry_backoff_seconds

    from src.core.notify_policy import NotifyPolicy, parse_dedupe_overrides

    policy = NotifyPolicy(
        timezone=settings.app_timezone,
        quiet_hours=quiet_hours,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        dedupe_ttl_overrides=parse_dedupe_overrides(overrides_raw),
    )

    notifier = NotifierManager(policy=policy)
    for ch in channels:
        notifier.add_channel(ch.type, ch.config or {})
    return notifier


def _build_ai_client(model: AIModel | None, service: AIService | None, proxy: str):
    """根据解析后的 model+service 构建带 failover 的 AI 客户端。

    主候选沿用四级路由选定的 model+service;备选由 build_failover_client 从库里
    其余模型按优先级补齐。返回的 FailoverAIClient 与 AIClient 接口兼容,可原地替换。
    """
    return build_failover_client(model, service, proxy)


def build_context(agent_name: str, stock_agent_id: int | None = None) -> AgentContext:
    """为指定 Agent 构建运行上下文"""
    settings = Settings()
    watchlist = load_watchlist_for_agent(agent_name)
    portfolio = load_portfolio_for_agent(agent_name)
    proxy = _get_proxy() or settings.http_proxy

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    ai_client = _build_ai_client(model, service, proxy)
    channels = resolve_notify_channels(agent_name, stock_agent_id)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=watchlist)
    return AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        notify_policy=getattr(notifier, "policy", None),
    )


# Agent 注册表
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    "premarket_outlook": PremarketOutlookAgent,
    "news_digest": NewsDigestAgent,
    "chart_analyst": ChartAnalystAgent,
    "intraday_monitor": IntradayMonitorAgent,
    "tradingagents": TradingAgentsAgent,
}


def build_scheduler() -> AgentScheduler:
    """构建调度器并注册已启用的 Agent"""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # 设置 context 构建函数（每次执行时动态获取最新配置）
    sched.set_context_builder(build_context)

    db = SessionLocal()
    try:
        agent_configs = (
            db.query(AgentConfig)
            .filter(
                AgentConfig.enabled == True,
                AgentConfig.kind == AGENT_KIND_WORKFLOW,
            )
            .all()
        )
        for cfg in agent_configs:
            agent_cls = AGENT_REGISTRY.get(cfg.name)
            if not agent_cls:
                logger.warning(f"Agent {cfg.name} 未在 AGENT_REGISTRY 中注册")
                continue
            if not cfg.schedule:
                logger.info(f"Agent {cfg.name} 未设置调度计划，跳过")
                continue

            agent_kwargs = cfg.config or {}
            try:
                agent_instance = (
                    agent_cls(**agent_kwargs) if agent_kwargs else agent_cls()
                )
            except TypeError:
                agent_instance = agent_cls()
            sched.register(
                agent_instance,
                schedule=cfg.schedule,
                execution_mode=cfg.execution_mode or "batch",
            )
    finally:
        db.close()

    return sched


def reload_scheduler() -> bool:
    """重载调度器（用于配置导入/批量修改后立即生效）"""
    global scheduler
    if _schedulers_disabled():
        logger.warning(
            "DISABLE_SCHEDULERS is set - scheduler reload skipped (fixture/verification mode)"
        )
        return False
    try:
        current = globals().get("scheduler")
        if current:
            try:
                current.shutdown()
            except Exception:
                pass
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Agent 调度器已重载")
        return True
    except Exception as e:
        logger.error(f"Agent 调度器重载失败: {e}")
        return False


def _log_trigger_info(
    agent_name: str,
    stocks: list,
    model: AIModel | None,
    service: AIService | None,
    channels: list[NotifyChannel],
):
    """打印 Agent 触发时的上下文信息"""
    stock_names = ", ".join(
        f"{s.name}({s.symbol})" if hasattr(s, "symbol") else str(s) for s in stocks
    )
    ai_info = f"{service.name}/{model.model}" if model and service else "未配置"
    channel_info = ", ".join(ch.name for ch in channels) if channels else "无"
    logger.info(
        f"[触发] Agent={agent_name} | 股票=[{stock_names}] | AI={ai_info} | 通知=[{channel_info}]"
    )


def get_agent_execution_mode(agent_name: str) -> str:
    """获取 Agent 的执行模式"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.execution_mode if agent and agent.execution_mode else "batch"
    finally:
        db.close()


def get_agent_config(agent_name: str) -> dict:
    """获取 Agent 的配置参数"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.config if agent and agent.config else {}
    finally:
        db.close()


async def trigger_agent(agent_name: str) -> str:
    """手动触发 Agent 执行（根据执行模式处理）"""
    start = time.monotonic()
    trace_id = f"man-{agent_name}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} has no registered implementation")

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent",
        tags={"trigger_source": "manual"},
    ):
        watchlist = load_watchlist_for_agent(agent_name)
        logger.info(
            f"[watchlist] Agent={agent_name} count={len(watchlist)} symbols={[s.symbol for s in watchlist]}"
        )
        if not watchlist:
            return f"Agent {agent_name} has no associated watchlist stocks"

        model, service = resolve_ai_model(agent_name)
        channels = resolve_notify_channels(agent_name)
        _log_trigger_info(agent_name, watchlist, model, service, channels)

        context = build_context(agent_name)
        execution_mode = get_agent_execution_mode(agent_name)
        agent_config = get_agent_config(agent_name)

        # 根据配置初始化 Agent
        if agent_config:
            agent = agent_cls(**agent_config)
        else:
            agent = agent_cls()

        try:
            if execution_mode == "single" and hasattr(agent, "run_single"):
                # 单只模式：逐只股票分析
                results = []
                for stock in watchlist:
                    result = await agent.run_single(context, stock.symbol)
                    if result:
                        results.append(f"{stock.name}: {result.content[:100]}...")
                msg = "\n\n".join(results) if results else "No abnormal activity"
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=msg,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    model_label=context.model_label,
                )
                return msg
            else:
                # 批量模式：所有股票一起分析
                result = await agent.run(context)
                raw = result.raw_data or {}
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=result.content,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    notify_attempted=(
                        "notified" in raw
                        or "notify_error" in raw
                        or "notify_skipped" in raw
                    ),
                    notify_sent=bool(raw.get("notified", False)),
                    model_label=context.model_label,
                )
                return result.content
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise


async def trigger_agent_for_stock(
    agent_name: str,
    stock,
    stock_agent_id: int | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    suppress_notify: bool = False,
    trace_id: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """手动触发 Agent 执行（单只股票）"""
    start = time.monotonic()
    trace_id = trace_id or f"man-{agent_name}-{stock.symbol}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} has no registered implementation")

    settings = Settings()
    proxy = _get_proxy() or settings.http_proxy

    try:
        market = MarketCode(stock.market)
    except ValueError:
        # Unknown market code: never silently reclassify (e.g. as CN). This is
        # a manual, explicit single-stock trigger, so still run it rather than
        # skipping — just mark the market as unknown and warn loudly.
        market = MarketCode.UNKNOWN
        logger.warning(
            f"trigger_agent_for_stock: unknown market {stock.market!r} for stock "
            f"{stock.symbol!r} (id={stock.id}); running with market=UNKNOWN"
        )

    stock_config = StockConfig(
        symbol=stock.symbol,
        name=stock.name,
        market=market,
    )

    # 加载该股票的持仓信息
    portfolio = load_portfolio_for_stock(stock.id)

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    channels = [] if suppress_notify else resolve_notify_channels(agent_name, stock_agent_id)
    _log_trigger_info(agent_name, [stock], model, service, channels)

    ai_client = _build_ai_client(model, service, proxy)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=[stock_config])
    context = AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        suppress_notify=suppress_notify,
    )
    # 暴露 trace_id / force_refresh 给 agent(供 TradingAgents 进度反馈 + 缓存控制使用)。
    # AgentContext 不强制声明此字段,通过 setattr 注入,其他 agent 不受影响。
    setattr(context, "_trace_id", trace_id)
    setattr(context, "_force_refresh", force_refresh)

    # 创建 agent，支持手动触发参数。TradingAgents 等新 agent 从 AgentConfig 读 config。
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    elif agent_name == "tradingagents":
        # 从 AgentConfig.config 读取实例化参数
        agent_kwargs = get_agent_config(agent_name) or {}
        try:
            agent = agent_cls(**agent_kwargs)
        except TypeError:
            agent = agent_cls()
    else:
        agent = agent_cls()

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent_for_stock",
        tags={"trigger_source": "manual", "stock_symbol": stock.symbol},
    ):
        try:
            result = await agent.run(context)
            raw = result.raw_data or {}
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=result.content,
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                notify_attempted=(
                    "notified" in raw
                    or "notify_error" in raw
                    or "notify_skipped" in raw
                ),
                notify_sent=bool(raw.get("notified", False)),
                model_label=context.model_label,
            )
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise

    # 返回详细结果
    skipped = bool(result.raw_data.get("skipped", False))
    should_alert = bool(
        result.raw_data.get("should_alert", False if skipped else True)
    )
    return {
        "code": 0 if not skipped else 1001001,
        "success": not skipped,
        "message": result.content if skipped else "ok",
        "title": result.title,
        "content": result.content,
        "should_alert": should_alert,
        "notified": result.raw_data.get("notified", False),
        "skipped": skipped,
    }


@asynccontextmanager
async def lifespan(app):
    """应用生命周期: 初始化 + 启动调度器"""
    init_db()
    setup_logging()
    # OTel 导出(可选,默认关闭):仅当配置了 OTEL_EXPORTER_OTLP_ENDPOINT 且装了
    # opentelemetry SDK 时启用,否则静默 no-op,不影响现有部署。
    try:
        from src.core.otel import init_otel

        init_otel()
    except Exception as e:  # 兜底:OTel 初始化异常绝不阻断服务启动
        logger.warning(f"OTel 初始化跳过: {e}")
    setup_proxy()  # 设置进程 env 代理(HTTP_PROXY/NO_PROXY);所有 httpx(trust_env=True)据此走代理
    setup_ssl()

    # 从环境变量初始化认证（Docker 部署用）
    from src.web.api.auth import init_auth_from_env

    db = SessionLocal()
    try:
        if init_auth_from_env(db):
            logger.info("已从环境变量初始化认证账号")
    finally:
        db.close()

    seed_agents()
    try:
        db = SessionLocal()
        try:
            reconcile_data_sources(db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"数据源对账失败,跳过(不阻断启动): {e}")
    seed_strategies()
    seed_sample_stocks()
    try:
        canonicalise_us_symbols()
    except Exception as e:
        logger.warning(f"US symbol canonicalisation failed, skipped (does not block startup): {e}")
    try:
        backfill_stock_names_english()
    except Exception as e:
        logger.warning(f"股票名称英文回填失败,跳过(不阻断启动): {e}")
    try:
        backfill_candidate_names_english()
    except Exception as e:
        logger.warning(f"候选池/信号表股票名称英文回填失败,跳过(不阻断启动): {e}")

    # 启动时回填历史 TradingAgents 决策到建议池(stock_suggestions)
    # 早期 TA 运行没写建议池,这次启动一次性补齐,让「AI 建议」面板能看到。
    # 幂等:已存在不重复写;每次启动重跑代价极低(只查最近 7 天 + dedupe)。
    try:
        from src.agents.tradingagents.backfill import backfill_tradingagents_suggestions
        backfill_tradingagents_suggestions(days=7)
    except Exception as e:
        logger.warning(f"TradingAgents 建议回填失败,跳过: {e}")

    # Tradable universe (US + TSX directories): load the cache, refresh in the background when stale.
    try:
        from src.core import universe

        universe.warm_up()
    except Exception as e:
        logger.warning(f"Universe warm-up skipped: {e}")

    # Trading-calendar warm-up. US/CA use a fixed holiday table (no network); the CN
    # calendar fetch inside refresh() is a no-op unless CN is enabled, so this is cheap.
    try:
        from src.core.trading_calendar import refresh as refresh_trading_calendar

        asyncio.create_task(refresh_trading_calendar())
    except Exception as e:
        logger.warning(f"交易日历预热调度失败(降级为只判周末): {e}")

    global scheduler, price_alert_scheduler, paper_trading_scheduler, context_maintenance_scheduler
    if _schedulers_disabled():
        logger.warning("DISABLE_SCHEDULERS is set - no schedulers started (fixture/verification mode)")
    else:
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Agent 调度器已启动")
        try:
            settings = Settings()
            price_alert_scheduler = PriceAlertScheduler(
                timezone=settings.app_timezone,
                interval_seconds=60,
            )
            price_alert_scheduler.start()
            logger.info("价格提醒调度器已启动")
        except Exception as e:
            logger.error(f"价格提醒调度器启动失败: {e}")
        try:
            settings = Settings()
            paper_trading_scheduler = PaperTradingScheduler(
                timezone=settings.app_timezone,
                interval_seconds=60,
            )
            paper_trading_scheduler.start()
            logger.info("模拟盘调度器已启动")
        except Exception as e:
            logger.error(f"模拟盘调度器启动失败: {e}")
        try:
            settings = Settings()
            context_maintenance_scheduler = ContextMaintenanceScheduler(
                timezone=settings.app_timezone,
                eval_interval_hours=6,
                snapshot_retention_days=180,
                outcome_retention_days=365,
            )
            context_maintenance_scheduler.start()
            logger.info("上下文维护调度器已启动")
        except Exception as e:
            logger.error(f"上下文维护调度器启动失败: {e}")
        # MCP 调用日志保留期清理:每日 04:00 清理超期审计记录
        try:
            from src.web.api.mcp import prune_mcp_logs

            scheduler.scheduler.add_job(
                prune_mcp_logs,
                "cron",
                hour=4,
                minute=0,
                id="mcp_log_retention",
                replace_existing=True,
            )
            logger.info("MCP 日志保留期清理任务已注册")
        except Exception as e:
            logger.error(f"MCP 日志清理任务注册失败: {e}")
        # Tradable universe: refresh the US + TSX directories daily at 05:30 local.
        try:
            from src.core import universe

            scheduler.scheduler.add_job(
                lambda: universe.refresh_blocking(force=True),
                "cron",
                hour=5,
                minute=30,
                id="universe_refresh",
                replace_existing=True,
            )
            logger.info("Universe refresh job registered (05:30 daily)")
        except Exception as e:
            logger.error(f"Universe refresh job registration failed: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent 调度器已关闭")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("价格提醒调度器已关闭")
    if paper_trading_scheduler:
        paper_trading_scheduler.shutdown()
        logger.info("模拟盘调度器已关闭")
    if context_maintenance_scheduler:
        context_maintenance_scheduler.shutdown()
        logger.info("上下文维护调度器已关闭")


# 模块级 app 实例，供 uvicorn reload 使用
from src.web.app import app  # noqa: E402

app.router.lifespan_context = lifespan

# 生产环境静态文件服务
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi import Request
    from fastapi.responses import FileResponse

    # SPA 路由：所有非 API 请求返回 index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str, request: Request):
        import mimetypes
        from src.web.static_site import accepts_gzip, resolve_static_page

        file_path, status = resolve_static_page(static_dir, path)
        if file_path is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        headers = {"Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
        if status == 200 and path.startswith("assets/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif status == 200 and file_path.suffix in {".jpg", ".png", ".webp", ".woff2"}:
            headers["Cache-Control"] = "public, max-age=86400"
        media_type = mimetypes.guess_type(file_path.name)[0]
        compressed = file_path.with_name(file_path.name + ".gz")
        if accepts_gzip(request.headers.get("accept-encoding", "")) and compressed.is_file():
            headers["Content-Encoding"] = "gzip"
            return FileResponse(compressed, status_code=status, media_type=media_type, headers=headers)
        return FileResponse(file_path, status_code=status, media_type=media_type, headers=headers)

    logger.info(f"静态文件服务已启用: {static_dir}")


if __name__ == "__main__":
    print("PanWatch starting: http://127.0.0.1:8000")
    print("API docs: http://127.0.0.1:8000/docs")
    # 生产(Docker `python server.py`)不应开 reload:uvicorn 文件监听会多起一个 reloader
    # 子进程、浪费资源,且监听 data/ 写入易误触发重启。本地热重载用 `make dev-api`
    # (uvicorn --reload),或显式设 DEV_RELOAD=1。
    _dev_reload = os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=_dev_reload,
        reload_dirs=["src", "."] if _dev_reload else None,
        reload_excludes=["data/*", "frontend/*", ".claude/*"] if _dev_reload else None,
    )
