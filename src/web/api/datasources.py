"""Data source admin API."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import DataSource

logger = logging.getLogger(__name__)

router = APIRouter()


# Data source type labels (shown in the admin UI)
TYPE_LABELS = {
    "news": "News",
    "flash_news": "Market Headlines",
    "events": "Corporate Events",
    "fundamentals": "Fundamentals",
    "dividend": "Dividends",
    "holders": "Holders & Insiders",
    "filings": "Filings",
    "quote": "Real-time Quotes",
    "kline": "Daily Bars",
    "chart": "Chart Rendering",
}


class DataSourceCreate(BaseModel):
    name: str
    type: str  # one of TYPE_LABELS
    provider: str
    config: dict = {}
    enabled: bool = True
    priority: int = 0
    supports_batch: bool = False
    test_symbols: list[str] = []


class DataSourceUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    provider: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    priority: int | None = None
    supports_batch: bool | None = None
    test_symbols: list[str] | None = None


class DataSourceResponse(BaseModel):
    id: int
    name: str
    type: str
    type_label: str = ""
    provider: str
    config: dict
    enabled: bool
    priority: int
    supports_batch: bool = False
    test_symbols: list[str] = []

    class Config:
        from_attributes = True


# Types served by the marketdata package engines (chart is rendered in-process instead)
_ENGINE_ATTACHED_TYPES = {
    "news",
    "quote",
    "kline",
    "events",
    "flash_news",
    "fundamentals",
    "dividend",
    "holders",
    "filings",
}


def _is_orphan(type_: str, provider: str) -> bool:
    """True when (type, provider) is neither a package vendor nor a seeded row.

    Mirrors the orphan rule in server.reconcile_data_sources (legal = package | seeds).
    """
    from marketdata import PACKAGE_VENDORS_BY_TYPE
    from server import _seed_providers_by_type

    legal = PACKAGE_VENDORS_BY_TYPE.get(type_, frozenset()) | _seed_providers_by_type().get(type_, set())
    return provider not in legal


def _to_response(source: DataSource, health_map: dict | None = None) -> dict:
    """Serialise a row; ``health_map`` is {provider: metrics}, missing -> health=None."""
    health = (health_map or {}).get(source.provider)
    return {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "config": source.config or {},
        "enabled": source.enabled,
        "priority": source.priority,
        "supports_batch": source.supports_batch or False,
        "test_symbols": source.test_symbols or [],
        "engine_attached": source.type in _ENGINE_ATTACHED_TYPES,
        "health": health,
        "is_orphan": _is_orphan(source.type, source.provider),
    }


@router.get("")
def list_datasources(type: str | None = None, db: Session = Depends(get_db)):
    """List data sources, optionally filtered by type."""
    query = db.query(DataSource)
    if type:
        query = query.filter(DataSource.type == type)
    sources = query.order_by(DataSource.type, DataSource.priority, DataSource.id).all()
    from src.core.marketdata_client import get_market_data
    health_map = get_market_data().health()
    return [_to_response(s, health_map) for s in sources]


@router.get("/types")
def get_datasource_types():
    """List the known data source types."""
    return [{"type": k, "label": v} for k, v in TYPE_LABELS.items()]


@router.post("/reset-to-seed")
def reset_datasources_to_seed(db: Session = Depends(get_db)):
    """Gentle reconcile: insert missing seeds, delete orphan rows, keep user customisations."""
    from server import reconcile_data_sources

    summary = reconcile_data_sources(db)
    logger.info(f"Data source reconcile finished: {summary}")
    return summary


@router.get("/{source_id}")
def get_datasource(source_id: int, db: Session = Depends(get_db)):
    """Get one data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return _to_response(source)


@router.post("")
def create_datasource(data: DataSourceCreate, db: Session = Depends(get_db)):
    """Create a data source."""
    source = DataSource(
        name=data.name,
        type=data.type,
        provider=data.provider,
        config=data.config,
        enabled=data.enabled,
        priority=data.priority,
        supports_batch=data.supports_batch,
        test_symbols=data.test_symbols,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info(f"Created data source: {source.name} ({source.provider})")
    return _to_response(source)


@router.put("/{source_id}")
def update_datasource(
    source_id: int, data: DataSourceUpdate, db: Session = Depends(get_db)
):
    """Update a data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(source, key, value)

    db.commit()
    db.refresh(source)
    logger.info(f"Updated data source: {source.name}")
    return _to_response(source)


@router.delete("/{source_id}")
def delete_datasource(source_id: int, db: Session = Depends(get_db)):
    """Delete a data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    db.delete(source)
    db.commit()
    logger.info(f"Deleted data source: {source.name}")
    return {"ok": True, "message": f"Deleted {source.name}"}


@router.post("/{source_id}/test")
async def test_datasource(source_id: int, db: Session = Depends(get_db)):
    """Run a connectivity test against one data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    from src.core.data_collector import get_collector_manager

    manager = get_collector_manager()
    manager.clear_logs()

    result = await manager.test_source(source)

    # Do not use success/data as top-level keys: ResponseWrapperMiddleware would unwrap
    # them as a business response and drop the metadata (see src/web/response.py).
    return {
        "test_passed": result.success,
        "source_name": source.name,
        "source_type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "supports_batch": source.supports_batch or False,
        "test_symbols": source.test_symbols or [],
        "count": result.count,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "items": result.data,
        "logs": manager.get_logs(),
    }
