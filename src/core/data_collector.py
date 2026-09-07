"""统一数据源管理器"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from marketdata import PACKAGE_VENDORS_BY_TYPE, capture_errors

from src.web.database import SessionLocal
from src.web.models import DataSource
from src.models.market import MarketCode
from src.models.market import default_market

logger = logging.getLogger(__name__)

# Upper bound on how many configured test_symbols a data-source "Test" exercises (keeps a
# pasted-in long list from hammering the vendor). 10 covers every seeded configuration.
_TEST_SYMBOL_LIMIT = 10

# Default symbols / names used by the Test button when a source has no test_symbols.
_DEFAULT_TEST_SYMBOLS = ["AAPL", "SHOP.TO"]
_DEFAULT_TEST_NAMES = {
    "AAPL": "Apple",
    "SHOP.TO": "Shopify",
    "MSFT": "Microsoft",
    "RY.TO": "Royal Bank of Canada",
}


@dataclass
class CollectorResult:
    """采集结果"""

    success: bool
    data: Any = None
    count: int = 0
    duration_ms: int = 0
    error: str = ""
    source_name: str = ""
    source_provider: str = ""


@dataclass
class CollectorLog:
    """采集日志"""

    timestamp: datetime
    source_name: str
    source_type: str
    action: str  # "start" / "success" / "error"
    message: str
    duration_ms: int = 0
    count: int = 0


class DataCollectorManager:
    """
    统一数据源管理器

    提供统一的数据采集接口，支持：
    - 从数据库配置加载数据源
    - 记录采集日志
    - 批量/单个采集
    """

    # 数据源类型 -> (provider -> 采集器工厂)
    COLLECTOR_FACTORIES: dict[str, dict[str, Callable]] = {}

    def __init__(self):
        self.logs: list[CollectorLog] = []
        self._register_collectors()

    def _register_collectors(self):
        """Register host-side collector factories (package-backed types need none)."""
        from src.collectors.kline_collector import KlineCollector

        self.COLLECTOR_FACTORIES = {
            "kline": {
                "tencent": lambda cfg: ("tencent", KlineCollector),
            },
            "chart": {
                "panwatch": lambda cfg: ("panwatch", cfg),
            },
        }

    def _log(
        self,
        source_name: str,
        source_type: str,
        action: str,
        message: str,
        duration_ms: int = 0,
        count: int = 0,
    ):
        """记录日志"""
        log = CollectorLog(
            timestamp=datetime.now(),
            source_name=source_name,
            source_type=source_type,
            action=action,
            message=message,
            duration_ms=duration_ms,
            count=count,
        )
        self.logs.append(log)

        # 同时输出到 logger:error 走 WARNING；start/success 是底层心跳,降到 DEBUG。
        # UI 日志板始终从 self.logs 读完整记录,不受这里影响。
        if action == "error":
            logger.warning(f"[{source_name}] {message}")
        else:
            logger.debug(f"[{source_name}] {message}")

    def get_logs(self) -> list[dict]:
        """获取日志（用于 UI 展示）"""
        return [
            {
                "timestamp": log.timestamp.strftime("%H:%M:%S"),
                "source_name": log.source_name,
                "source_type": log.source_type,
                "action": log.action,
                "message": log.message,
                "duration_ms": log.duration_ms,
                "count": log.count,
            }
            for log in self.logs
        ]

    def clear_logs(self):
        """清空日志"""
        self.logs = []

    def get_enabled_sources(self, source_type: str) -> list[DataSource]:
        """获取指定类型的已启用数据源"""
        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == source_type, DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def get_source_by_id(self, source_id: int) -> DataSource | None:
        """根据 ID 获取数据源"""
        db = SessionLocal()
        try:
            return db.query(DataSource).filter(DataSource.id == source_id).first()
        finally:
            db.close()

    def _get_stock_names(self, symbols: list[str]) -> dict[str, str]:
        """获取股票代码到名称的映射"""
        from src.web.models import Stock

        default_names = dict(_DEFAULT_TEST_NAMES)

        db = SessionLocal()
        try:
            stocks = db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
            result = {s.symbol: s.name for s in stocks}

            # 对于数据库中没有的股票，使用默认名称
            for symbol in symbols:
                if symbol not in result and symbol in default_names:
                    result[symbol] = default_names[symbol]

            return result
        except Exception as e:
            logger.warning(f"Failed to look up stock names: {e}")
            return {s: default_names.get(s, s) for s in symbols if s in default_names}
        finally:
            db.close()

    async def collect_news(
        self, symbols: list[str], hours: int = 12
    ) -> CollectorResult:
        """采集新闻（使用所有已启用的新闻数据源）"""
        from src.collectors.news_collector import NewsCollector

        start_time = datetime.now()
        self._log("News", "news", "start", f"Collecting news for {len(symbols)} symbol(s)")

        try:
            collector = NewsCollector.from_database()
            news_list = await collector.fetch_all(symbols=symbols, since_hours=hours)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "News",
                "news",
                "success",
                f"Collected {len(news_list)} item(s)",
                duration_ms=duration_ms,
                count=len(news_list),
            )

            return CollectorResult(
                success=True,
                data=news_list,
                count=len(news_list),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("News", "news", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_kline(
        self, symbol: str, market: str = "", days: int = 60
    ) -> CollectorResult:
        """采集 K 线数据"""
        from src.collectors.kline_collector import KlineCollector
        from src.models.market import MarketCode

        market = market or default_market()
        start_time = datetime.now()
        self._log("Daily bars", "kline", "start", f"Fetching bars for {symbol}")

        try:
            market_code = MarketCode(market)
            collector = KlineCollector(market_code)
            summary = collector.get_kline_summary(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if summary.get("error"):
                self._log(
                    "Daily bars",
                    "kline",
                    "error",
                    summary["error"],
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error=summary["error"], duration_ms=duration_ms
                )

            self._log(
                "Daily bars",
                "kline",
                "success",
                f"Fetched, last close {summary.get('last_close', 'N/A')}",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=summary,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("Daily bars", "kline", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_quote(self, symbols: list[str]) -> CollectorResult:
        """采集实时行情"""
        from src.core.marketdata_client import md_stock_data

        start_time = datetime.now()
        self._log("Quotes", "quote", "start", f"Fetching quotes for {len(symbols)} symbol(s)")

        try:
            stocks = await asyncio.to_thread(md_stock_data, symbols, default_market())

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "Quotes",
                "quote",
                "success",
                f"Fetched {len(stocks)} quote(s)",
                duration_ms=duration_ms,
                count=len(stocks),
            )

            return CollectorResult(
                success=True,
                data=stocks,
                count=len(stocks),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("Quotes", "quote", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_source(self, source: DataSource) -> CollectorResult:
        """测试单个数据源"""
        test_symbols = list(source.test_symbols or _DEFAULT_TEST_SYMBOLS)

        start_time = datetime.now()
        self._log(
            source.name,
            source.type,
            "start",
            f"Test started, symbols: {','.join(test_symbols)}",
        )

        try:
            # 收集 vendor/market_get 的真实失败原因,失败时透到 UI(而不是笼统的"无数据")
            with capture_errors() as errs:
                result = await self._test_source_impl(source, test_symbols)
            if not result.success and errs:
                # 去重保序 + 截断,拼成真因;若原本已有更具体的 error(如"provider 无对应 vendor")保留在前
                seen: dict[str, None] = {}
                for m in errs:
                    seen.setdefault(m, None)
                detail = "; ".join(list(seen)[:8])
                generic = {"", "No data", "No quotes returned", "No bars returned",
                           "No news returned", "No headlines returned", "No fundamentals returned",
                           "No dividends returned", "No holder data returned", "No filings returned",
                           "No events returned"}
                result.error = detail if (result.error or "") in generic else f"{result.error}; cause: {detail}"
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if result.success:
                self._log(
                    source.name,
                    source.type,
                    "success",
                    f"Test passed, {result.count} item(s)",
                    duration_ms=duration_ms,
                    count=result.count,
                )
            else:
                self._log(
                    source.name,
                    source.type,
                    "error",
                    result.error,
                    duration_ms=duration_ms,
                )

            result.duration_ms = duration_ms
            result.source_name = source.name
            result.source_provider = source.provider
            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                source.name, source.type, "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                source_name=source.name,
                source_provider=source.provider,
            )

    async def _test_source_impl(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Dispatch a data-source test by type."""
        if source.type == "news":
            return await self._test_news_source(source, test_symbols)

        elif source.type == "kline":
            return await self._test_kline_source(source, test_symbols)

        elif source.type == "quote":
            return await self._test_quote_source(source, test_symbols)

        elif source.type == "chart":
            return await self._test_chart_source(source, test_symbols)

        elif source.type == "events":
            return await self._test_events_source(source, test_symbols)

        elif source.type == "flash_news":
            return await self._test_flash_news_source(source)

        elif source.type == "fundamentals":
            return await self._test_fundamentals_source(source)

        elif source.type == "dividend":
            return await self._test_dividend_source(source)

        elif source.type == "holders":
            return await self._test_holders_source(source)

        elif source.type == "filings":
            return await self._test_filings_source(source)

        return CollectorResult(
            success=False, error=f"Unsupported data source type: {source.type}"
        )

    async def _test_chart_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Render one chart PNG through the in-process renderer and return it inline (base64)."""
        import base64

        from src.collectors.chart_renderer import ChartRenderer

        renderer = ChartRenderer(config=source.config or {})
        try:
            symbol = test_symbols[0] if test_symbols else _DEFAULT_TEST_SYMBOLS[0]
            names = self._get_stock_names([symbol])
            image = await renderer.capture(
                symbol,
                names.get(symbol, symbol),
                market=default_market(),
                provider=source.provider,
            )
            if image and image.exists:
                with open(image.filepath, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode("utf-8")
                return CollectorResult(
                    success=True,
                    data={"image": f"data:image/png;base64,{img_base64}"},
                    count=1,
                )
            return CollectorResult(success=False, error="Chart rendering failed")
        finally:
            await renderer.close()

    async def _test_events_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Corporate events through a single-vendor package engine.

        Uses a wide window (365 days back, 90 ahead) so a quiet quarter does not read as a
        broken source; this only validates connectivity and shape.
        """
        from datetime import timezone

        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._EVENTS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (events)",
            )

        syms = list(test_symbols or [])[:_TEST_SYMBOL_LIMIT]
        if not syms:
            return CollectorResult(success=False, error="Configure test symbols first")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"events": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )
        lookback_days = 365
        try:
            items = md.events(
                syms, now=datetime.now(timezone.utc), since_days=lookback_days, ahead_days=90
            )
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": (i.symbols[0] if i.symbols else ""),
                    "title": i.title[:80],
                    "date": i.publish_time.strftime("%Y-%m-%d"),
                    "event_type": i.event_type,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else f"No events returned (lookback={lookback_days}d, ahead=90d)",
        )

    # Vendors the package registers per type (authoritative: marketdata.PACKAGE_VENDORS_BY_TYPE).
    # A provider outside the set has no implementation, so the test reports that instead of
    # building an engine that cannot run.
    _NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["news"]
    _KLINE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["kline"]
    _QUOTE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["quote"]
    _EVENTS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["events"]
    _FLASH_NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["flash_news"]
    _FUNDAMENTALS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["fundamentals"]
    _DIVIDEND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dividend"]
    _HOLDERS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["holders"]
    _FILINGS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["filings"]

    async def _test_kline_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Test one kline provider through a single-vendor package engine (no fallback chain)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider, Symbol

        if source.provider not in self._KLINE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (kline)",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"kline": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        results = []
        first_error = ""
        for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
            market = Symbol.parse(symbol).market.value
            try:
                bars = md.klines(symbol, market=market, days=30)
                if bars:
                    last = bars[-1]
                    results.append(
                        {
                            "symbol": symbol,
                            "last_close": last.close,
                            "last_date": last.date,
                            "count": len(bars),
                        }
                    )
                elif not first_error:
                    first_error = "No data"
            except Exception as e:
                if not first_error:
                    first_error = str(e)

        return CollectorResult(
            success=len(results) > 0,
            data=results,
            count=len(results),
            error="" if results else (first_error or "No bars returned"),
        )

    async def _test_quote_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Test one quote provider through a single-vendor package engine (no fallback chain)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        from src.core.marketdata_client import _quote_to_row

        if source.provider not in self._QUOTE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (quote)",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"quote": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            quotes = md.quotes(list(test_symbols[:_TEST_SYMBOL_LIMIT]))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        rows = [_quote_to_row(q) for q in quotes]
        return CollectorResult(
            success=len(rows) > 0,
            data=[
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "price": row["current_price"],
                    "change_pct": row["change_pct"],
                }
                for row in rows
            ],
            count=len(rows),
            error="" if rows else "No quotes returned",
        )

    async def _test_news_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Test one news provider through a single-vendor package engine.

        News is per symbol; the vendor gets the symbol -> name map so keyword-style searches
        (Google News) work. ``capture_errors`` in ``test_source`` surfaces the vendor's real
        failure reason.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (news)",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        names = self._get_stock_names(test_symbols)

        try:
            # Package publish_time is aware UTC, so ``now`` must be aware too.
            from datetime import timezone
            news = md.news(
                list(test_symbols[:_TEST_SYMBOL_LIMIT]),
                market=None,
                names=names,
                now=datetime.now(timezone.utc),
            )
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(news) > 0,
            data=[
                {
                    "title": n.title[:60],
                    "time": n.publish_time.strftime("%m-%d %H:%M"),
                }
                for n in news[:10]
            ],
            count=len(news),
            error="" if news else "No news returned",
        )

    async def _test_flash_news_source(self, source: DataSource) -> CollectorResult:
        """Test one market-headlines provider through a single-vendor package engine.

        Headlines are market level (no symbols); the request targets the vendor's first
        supported market so a Canada-only feed is not asked for US news.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider
        from marketdata.registry import VENDOR_CLASSES_BY_TYPE

        if source.provider not in self._FLASH_NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (flash_news)",
            )

        vendor_cls = VENDOR_CLASSES_BY_TYPE.get("flash_news", {}).get(source.provider)
        supported = sorted(getattr(vendor_cls, "supports_markets", None) or [])
        market = supported[0] if supported else default_market()

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"flash_news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.flash_news(market=market, limit=20)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "title": i.title[:80],
                    "time": i.publish_time.strftime("%m-%d %H:%M"),
                    "symbols": i.symbols,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "No headlines returned",
        )

    async def _test_fundamentals_source(self, source: DataSource) -> CollectorResult:
        """Test one fundamentals provider through a single-vendor package engine.

        Fundamentals are per symbol; the source must carry explicit test_symbols.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FUNDAMENTALS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (fundamentals)",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="Configure test symbols first")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "fundamentals": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.fundamentals(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "pe_ttm": i.pe_ttm,
                    "pb": i.pb,
                    "roe": i.roe,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "No fundamentals returned",
        )

    async def _test_dividend_source(self, source: DataSource) -> CollectorResult:
        """Test one dividend provider through a single-vendor package engine (per symbol)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DIVIDEND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (dividend)",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="Configure test symbols first")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"dividend": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.dividend(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "ex_date": i.ex_date,
                    "dividend_per_share": i.dividend_per_share,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "No dividends returned",
        )

    async def _test_holders_source(self, source: DataSource) -> CollectorResult:
        """Test one holders provider (ownership breakdown / institutions / insider transactions)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._HOLDERS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (holders)",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="Configure test symbols first")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"holders": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.holders(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "kind": i.kind,
                    "holder": i.holder,
                    "date": i.date,
                    "shares": i.shares,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "No holder data returned",
        )

    async def _test_filings_source(self, source: DataSource) -> CollectorResult:
        """Test one filings provider (SEC EDGAR forms, or Canadian press releases)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FILINGS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} has no vendor in the marketdata package (filings)",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="Configure test symbols first")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"filings": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.filings(syms, limit=20)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "form_type": i.form_type,
                    "title": i.title[:80],
                    "filed_at": i.filed_at.strftime("%Y-%m-%d") if hasattr(i.filed_at, "strftime") else str(i.filed_at),
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "No filings returned",
        )


# 全局单例
_manager: DataCollectorManager | None = None


def get_collector_manager() -> DataCollectorManager:
    """获取全局数据源管理器"""
    global _manager
    if _manager is None:
        _manager = DataCollectorManager()
    return _manager
