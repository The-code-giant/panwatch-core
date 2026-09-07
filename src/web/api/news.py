"""News API - driven by the configured news data sources."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import Stock, DataSource
from src.collectors.news_collector import NewsCollector, NewsItem
from src.core.source_labels import label_for

router = APIRouter()


class NewsItemResponse(BaseModel):
    source: str
    source_label: str
    publisher: str = ""
    external_id: str
    title: str
    content: str
    publish_time: str
    symbols: list[str]
    importance: int
    url: str = ""


@router.get("", response_model=list[NewsItemResponse])
async def get_news(
    symbols: str = Query(default="", description="Symbols, comma separated"),
    names: str = Query(default="", description="Stock names, comma separated (preferred over symbols)"),
    hours: int = Query(default=168, ge=1, le=720, description="Look-back window in hours (default 7 days)"),
    limit: int = Query(default=50, ge=1, le=200, description="Max items"),
    filter_related: bool = Query(default=True, description="Only keep news related to the watchlist"),
    source: str = Query(default="", description="Source filter, comma separated vendor keys (yfinance/google_news)"),
    db: Session = Depends(get_db),
):
    """
    News list built from the configured news data sources.

    - symbols: filter by symbol (comma separated); empty = every watchlist stock
    - names: filter by stock name (the frontend passes names, which are more stable)
    - hours: look-back window
    - limit: max items
    - filter_related: only keep items related to the watchlist
    """
    # Every watchlist stock (used for matching)
    all_stocks = db.query(Stock).all()
    stock_map = {s.symbol: s.name for s in all_stocks}
    name_to_symbol = {s.name: s.symbol for s in all_stocks}

    # Resolve stocks - names take precedence
    if names:
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        symbol_list = [name_to_symbol.get(n) for n in name_list if name_to_symbol.get(n)]
        passed_symbol_names = {name_to_symbol.get(n, ""): n for n in name_list if name_to_symbol.get(n)}
    elif symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        passed_symbol_names = {s: stock_map.get(s, s) for s in symbol_list}
    else:
        symbol_list = list(stock_map.keys())
        passed_symbol_names = stock_map

    if not symbol_list:
        return []

    source_filters = {s.strip() for s in source.split(",") if s.strip()} if source else set()

    # Match keywords: symbol + stock name
    keywords = set(symbol_list)
    for sym in symbol_list:
        if sym in stock_map:
            keywords.add(stock_map[sym])

    # Collector from the configured sources; pass the name map to avoid a second DB round-trip
    collector = NewsCollector.from_database()
    news_items = await collector.fetch_all(
        symbols=symbol_list,
        since_hours=hours,
        symbol_names=passed_symbol_names,
    )

    def is_related(item: NewsItem) -> bool:
        """Is the item about one of the requested stocks?"""
        if item.symbols and any(s in symbol_list for s in item.symbols):
            return True
        text = item.title + (item.content or "")
        return any(kw in text for kw in keywords)

    result = []
    for item in news_items:
        if source_filters and item.source not in source_filters:
            continue
        if filter_related and not is_related(item):
            continue

        # Tag the matched stocks
        matched_symbols = []
        text = item.title + (item.content or "")
        for sym, name in stock_map.items():
            if sym in symbol_list and (sym in text or name in text):
                matched_symbols.append(sym)

        publisher = getattr(item, "publisher", "") or ""
        result.append(NewsItemResponse(
            source=item.source,
            source_label=label_for(item.source, publisher),
            publisher=publisher,
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            publish_time=item.publish_time.strftime("%Y-%m-%d %H:%M"),
            symbols=matched_symbols or item.symbols,
            importance=item.importance,
            url=item.url,
        ))

        if len(result) >= limit:
            break

    return result


@router.get("/sources")
def get_news_sources(db: Session = Depends(get_db)):
    """Configured news data sources."""
    data_sources = (
        db.query(DataSource)
        .filter(DataSource.type == "news")
        .order_by(DataSource.priority)
        .all()
    )

    return [
        {
            "id": ds.provider,
            "name": ds.name,
            "enabled": ds.enabled,
            "priority": ds.priority,
        }
        for ds in data_sources
    ]
