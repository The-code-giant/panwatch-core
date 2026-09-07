"""PanWatch <-> marketdata wiring: DB config port, process singleton, dict/NewsItem adapters.

- DbConfigProvider: maps the DataSource table to marketdata SourceConfig (ConfigProvider port).
- get_market_data(): process-level singleton (stateless vendors + DB-backed config port).
- md_quote_rows(): MarketData.quotes -> list[dict] (same shape as the old orchestrator output).
- md_news()/md_news_by_keyword(): MarketData.news/news_by_keyword -> host NewsItem.
- md_holders()/md_filings(): HolderItem/FilingItem -> plain dicts for the API and prompts.
"""

from __future__ import annotations

import logging

from marketdata import MarketData, Quote, SourceConfig

logger = logging.getLogger(__name__)


class DbConfigProvider:
    """ConfigProvider 端口实现:从 DataSource 表按 priority 读某类型的启用源。"""

    def _query_rows(self, datatype: str) -> list:
        from src.web.database import SessionLocal
        from src.web.models import DataSource

        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == datatype, DataSource.enabled == True)  # noqa: E712
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        return [
            SourceConfig(
                vendor=r.provider,
                priority=r.priority,
                enabled=True,
                config=r.config or {},
                supports_batch=bool(r.supports_batch),
            )
            for r in self._query_rows(datatype)
        ]


_md: MarketData | None = None


def get_market_data() -> MarketData:
    """进程级单例。vendor 无状态、配置现查 DB,故无需失效钩子。"""
    global _md
    if _md is None:
        _md = MarketData(config=DbConfigProvider())
    return _md


def reset_market_data() -> None:
    """测试或热重载时重置单例。"""
    global _md
    _md = None


def _quote_to_row(q: Quote) -> dict:
    """marketdata.Quote → 旧 orchestrator 同形 dict。"""
    return {
        "symbol": q.symbol,
        "name": q.name,
        "market": q.market,
        "current_price": q.current_price,
        "change_pct": q.change_pct,
        "change_amount": q.change_amount,
        "prev_close": q.prev_close,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "volume": q.volume,
        "turnover": q.turnover,
        "turnover_rate": q.turnover_rate,
        "volume_ratio": q.volume_ratio,
        "pe_ratio": q.pe_ratio,
        "circulating_market_value": q.circulating_market_value,
        "total_market_value": q.total_market_value,
    }


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """批量报价,返回 list[dict](与旧 orchestrator 输出同形)。

    同步函数;async 调用方用 `await asyncio.to_thread(md_quote_rows, ...)`。
    """
    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [_quote_to_row(q) for q in quotes]


def _article_to_newsitem(a):
    """marketdata.NewsArticle → host NewsItem(同名字段直拷)。

    lazy import 避免与 news_collector 的模块级循环引用(news_collector 会
    在模块级 import 本模块的 md_news)。
    """
    from src.collectors.news_collector import NewsItem

    return NewsItem(
        source=a.source,
        external_id=a.external_id,
        title=a.title,
        content=a.content,
        publish_time=a.publish_time,
        symbols=a.symbols,
        importance=a.importance,
        url=a.url,
        publisher=getattr(a, "publisher", "") or "",
    )


def md_news(
    symbols: list[str],
    since_hours: int = 2,
    names: dict[str, str] | None = None,
    market: str | None = None,
) -> list:
    """Aggregated symbol news -> list[NewsItem] (same shape as NewsCollector.fetch_all).

    ``market`` is passed through so the package groups symbols by market (``None`` lets it
    detect the market per symbol). The host supplies ``now`` (aware UTC); the package never
    reads the clock itself. Sync; async callers use ``asyncio.to_thread``.
    """
    from datetime import datetime, timezone

    arts = get_market_data().news(
        list(symbols or []), market=market, since_hours=since_hours, names=names,
        now=datetime.now(timezone.utc),
    )
    return [_article_to_newsitem(a) for a in arts]


def md_news_by_keyword(keyword: str, market: str = "US") -> list:
    """Keyword (sector / theme) news via Google News RSS -> list[NewsItem]. Sync."""
    arts = get_market_data().news_by_keyword(keyword, market=market)
    return [_article_to_newsitem(a) for a in arts]


def _holder_to_dict(h) -> dict:
    return {
        "symbol": h.symbol,
        "kind": h.kind,
        "holder": h.holder,
        "date": h.date or "",
        "shares": h.shares,
        "value": h.value,
        "pct_out": h.pct_out,
        "change_pct": h.change_pct,
        "transaction": h.transaction or "",
        "position": h.position or "",
        "ownership": h.ownership or "",
        "url": h.url or "",
        "source": h.source or "",
    }


def md_holders(symbols: list[str], market: str | None = None) -> list[dict]:
    """Ownership rows (breakdown / institution / insider_tx) -> list[dict]. Sync."""
    syms = list(symbols or [])
    if not syms:
        return []
    return [_holder_to_dict(h) for h in get_market_data().holders(syms, market=market)]


def _filing_to_dict(f) -> dict:
    filed = f.filed_at
    return {
        "source": f.source,
        "external_id": f.external_id,
        "symbol": f.symbol,
        "form_type": f.form_type,
        "title": f.title,
        "filed_at": filed.isoformat() if hasattr(filed, "isoformat") else str(filed or ""),
        "url": f.url or "",
        "description": f.description or "",
        "report_date": f.report_date or "",
    }


def md_filings(symbols: list[str], market: str | None = None, limit: int = 50) -> list[dict]:
    """Regulatory filings (SEC EDGAR) or CA press releases -> list[dict] with ISO ``filed_at``. Sync."""
    syms = list(symbols or [])
    if not syms:
        return []
    return [_filing_to_dict(f) for f in get_market_data().filings(syms, market=market, limit=limit)]


def md_stock_data(symbols: list[str], market: str) -> list:
    """返回 list[StockData](旧 AkshareCollector.get_stock_data 同形)。同步。"""
    from src.models.market import MarketCode, StockData

    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [StockData(
        symbol=q.symbol, name=q.name or "", market=MarketCode(q.market),
        current_price=q.current_price or 0.0, change_pct=q.change_pct or 0.0,
        change_amount=q.change_amount or 0.0, volume=q.volume or 0.0,
        turnover=q.turnover or 0.0, open_price=q.open_price or 0.0,
        high_price=q.high_price or 0.0, low_price=q.low_price or 0.0,
        prev_close=q.prev_close or 0.0) for q in quotes]
