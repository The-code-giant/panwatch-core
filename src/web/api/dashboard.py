"""首页聚合 API（轻量版：不包含机会消息中心）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.strategy_engine import get_strategy_stats, list_strategy_signals
from src.web.api.chat import _get_ai_client
from src.web.database import get_db
from src.web.stock_list import _english_name
from src.web.models import (
    AnalysisHistory,
    EntryCandidate,
    LogEntry,
    MarketScanSnapshot,
    NewsTopicSnapshot,
    Position,
    Stock,
)

router = APIRouter()


def _format_datetime(dt) -> str:
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


def _to_market(market: str) -> str:
    m = (market or "ALL").strip().upper()
    return m if m == "ALL" or m in EQUITY_MARKETS else "ALL"


def _action_priority(item: dict) -> int:
    action = str(item.get("action") or "").lower()
    if action == "buy":
        return 3
    if action == "add":
        return 2
    if action in ("watch", "hold"):
        return 1
    return 0


def _group_signals(items: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in items or []:
        key = f"{row.get('stock_market') or default_market()}:{row.get('stock_symbol') or ''}"
        if ":" == key[-1]:
            continue
        prev = grouped.get(key)
        if not prev:
            row["strategy_count"] = 1
            grouped[key] = row
            continue
        prev["strategy_count"] = int(prev.get("strategy_count") or 1) + 1
        choose_next = False
        prev_active = str(prev.get("status") or "inactive") == "active"
        cur_active = str(row.get("status") or "inactive") == "active"
        if cur_active and not prev_active:
            choose_next = True
        elif _action_priority(row) > _action_priority(prev):
            choose_next = True
        elif float(row.get("rank_score") or 0) > float(prev.get("rank_score") or 0):
            choose_next = True
        if choose_next:
            row["strategy_count"] = int(prev.get("strategy_count") or 1)
            grouped[key] = row
    return list(grouped.values())


def _summarize_topics(raw_topics) -> list[dict]:
    out: list[dict] = []
    topics = raw_topics if isinstance(raw_topics, list) else []
    for item in topics:
        if isinstance(item, dict):
            name = str(item.get("topic") or item.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "score": float(item.get("score") or 0.0),
                    "sentiment": str(item.get("sentiment") or "neutral"),
                }
            )
        elif isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"name": text, "score": 0.0, "sentiment": "neutral"})
        if len(out) >= 8:
            break
    return out


def _load_latest_insights(db: Session) -> list[dict]:
    out = []
    agents = (
        ("premarket_outlook", "盘前分析"),
        ("daily_report", "收盘复盘"),
        ("news_digest", "新闻速递"),
    )
    for agent_name, label in agents:
        row = (
            db.query(AnalysisHistory)
            .filter(AnalysisHistory.agent_name == agent_name)
            .order_by(
                AnalysisHistory.analysis_date.desc(),
                AnalysisHistory.updated_at.desc(),
                AnalysisHistory.id.desc(),
            )
            .first()
        )
        if not row:
            continue
        out.append(
            {
                "id": int(row.id),
                "agent_name": agent_name,
                "agent_label": label,
                "analysis_date": row.analysis_date or "",
                "title": row.title or "",
                "updated_at": _format_datetime(row.updated_at),
            }
        )
    return out


@router.get("/overview")
def get_dashboard_overview(
    market: str = Query("ALL", description="市场过滤: ALL/CN/HK/US"),
    action_limit: int = Query(6, ge=3, le=20),
    risk_limit: int = Query(6, ge=3, le=20),
    days: int = Query(45, ge=7, le=365),
    db: Session = Depends(get_db),
):
    mkt = _to_market(market)
    market_filter = "" if mkt == "ALL" else mkt

    stats = get_strategy_stats(days=days)
    coverage = stats.get("coverage") if isinstance(stats.get("coverage"), dict) else {}
    snapshot_date = str(coverage.get("snapshot_date") or "")

    # Action list: unheld, active, executable first.
    unheld = list_strategy_signals(
        market=market_filter,
        status="active",
        min_score=55,
        limit=max(30, int(action_limit) * 10),
        snapshot_date=snapshot_date,
        source_pool="all",
        holding="unheld",
        strategy_code="",
        risk_level="all",
        include_payload=False,
    )
    grouped_unheld = _group_signals(list(unheld.get("items") or []))
    executable = [
        x
        for x in grouped_unheld
        if str(x.get("action") or "").lower() in ("buy", "add")
        and (x.get("entry_low") is not None or x.get("entry_high") is not None)
    ]
    executable.sort(key=lambda x: float(x.get("rank_score") or 0.0), reverse=True)
    if len(executable) < int(action_limit):
        remaining = sorted(
            [x for x in grouped_unheld if x not in executable],
            key=lambda x: (
                0 if str(x.get("status") or "") == "active" else 1,
                -float(x.get("rank_score") or 0.0),
            ),
        )
        executable.extend(remaining[: max(0, int(action_limit) - len(executable))])
    action_items = executable[: int(action_limit)]

    # Risk list: held symbols with risk flags.
    held = list_strategy_signals(
        market=market_filter,
        status="all",
        min_score=0,
        limit=max(40, int(risk_limit) * 12),
        snapshot_date=snapshot_date,
        source_pool="all",
        holding="held",
        strategy_code="",
        risk_level="all",
        include_payload=False,
    )
    grouped_held = _group_signals(list(held.get("items") or []))
    risk_items: list[dict] = []
    for row in grouped_held:
        flags: list[str] = []
        if bool(row.get("constrained")):
            flags.append("组合约束")
        if str(row.get("risk_level") or "").lower() == "high":
            flags.append("高风险")
        if str(row.get("status") or "").lower() != "active":
            flags.append("非活跃状态")
        if float(row.get("rank_score") or 0.0) < 68:
            flags.append("信号转弱")
        if not flags:
            continue
        risk_items.append({**row, "risk_flags": flags})
    risk_items.sort(
        key=lambda x: (
            -len(list(x.get("risk_flags") or [])),
            float(x.get("rank_score") or 0.0),
        )
    )
    risk_items = risk_items[: int(risk_limit)]

    # Portfolio quick stats (DB-only, no实时行情请求).
    positions = (
        db.query(Position, Stock)
        .join(Stock, Position.stock_id == Stock.id)
        .all()
    )
    by_market: dict[str, dict] = {}
    invested_cost = 0.0
    # ONE immutable FX snapshot per request (see accounts.resolve_fx_snapshot). A cost
    # whose currency we cannot convert is EXCLUDED and counted, never multiplied by a
    # placeholder rate — that would manufacture a USD figure from an unknown rate.
    fx_snapshot = resolve_fx_snapshot() if positions else None
    excluded_unconvertible = 0
    for pos, stock in positions:
        market_code = (stock.market or default_market()).strip().upper() or default_market()
        # A retired/disabled market's currency is not tracked, so fx_rate_for_market's
        # 1.0 for "not CA" must NOT be applied to it — that would convert a legacy
        # holding at 1:1. Enablement is the caller's check, per that helper's contract.
        fx = fx_rate_for_market(market_code, fx_snapshot) if is_enabled(market_code) else None
        if fx is None:
            excluded_unconvertible += 1
            continue
        cost = float(pos.cost_price or 0.0) * float(pos.quantity or 0) * fx
        invested_cost += cost
        bucket = by_market.setdefault(
            market_code,
            {"market": market_code, "positions": 0, "invested_cost": 0.0},
        )
        bucket["positions"] += 1
        bucket["invested_cost"] += cost
    watchlist_count = int((db.query(func.count(Stock.id)).scalar() or 0))
    from src.web.models import Account  # local import to avoid circular import at module import time

    total_available = float(
        db.query(func.coalesce(func.sum(Account.available_funds), 0.0))
        .filter(Account.enabled.is_(True))
        .scalar()
        or 0.0
    )

    # Market pulse from latest market scan snapshot (stable even without外网).
    pulse_query = db.query(MarketScanSnapshot)
    if snapshot_date:
        pulse_query = pulse_query.filter(MarketScanSnapshot.snapshot_date == snapshot_date)
    else:
        latest_pulse = (
            db.query(MarketScanSnapshot.snapshot_date)
            .order_by(MarketScanSnapshot.snapshot_date.desc())
            .first()
        )
        if latest_pulse:
            pulse_query = pulse_query.filter(MarketScanSnapshot.snapshot_date == latest_pulse[0])
    if market_filter:
        pulse_query = pulse_query.filter(MarketScanSnapshot.stock_market == market_filter)
    pulse_rows = (
        pulse_query.order_by(MarketScanSnapshot.score_seed.desc(), MarketScanSnapshot.updated_at.desc())
        .limit(18)
        .all()
    )
    hot_stocks = []
    for row in pulse_rows:
        quote = row.quote if isinstance(row.quote, dict) else {}
        hot_stocks.append(
            {
                "symbol": row.stock_symbol,
                "market": row.stock_market,
                "name": _english_name(row.stock_symbol, row.stock_name or row.stock_symbol),
                "score_seed": round(float(row.score_seed or 0.0), 2),
                "change_pct": quote.get("change_pct"),
                "turnover": quote.get("turnover"),
                "source": row.source or "market_scan",
            }
        )

    latest_topic = (
        db.query(NewsTopicSnapshot)
        .order_by(NewsTopicSnapshot.snapshot_date.desc(), NewsTopicSnapshot.id.desc())
        .first()
    )
    hot_topics = _summarize_topics(latest_topic.topics if latest_topic else [])

    # 3-day strategy win rate.
    rows_3d = [
        x for x in (stats.get("by_strategy") or []) if int(x.get("horizon_days") or 0) == 3
    ]
    sample_3d = sum(int(x.get("sample_size") or 0) for x in rows_3d)
    wins_3d = sum(int(x.get("wins") or 0) for x in rows_3d)
    win_rate_3d = round((wins_3d / sample_3d) * 100.0, 2) if sample_3d > 0 else None

    latest_history_updated_at = (
        db.query(func.max(AnalysisHistory.updated_at)).scalar()
    )
    latest_entry_snapshot = (
        db.query(EntryCandidate.snapshot_date)
        .order_by(EntryCandidate.snapshot_date.desc())
        .first()
    )
    latest_market_scan_snapshot = (
        db.query(MarketScanSnapshot.snapshot_date)
        .order_by(MarketScanSnapshot.snapshot_date.desc())
        .first()
    )
    error_24h = int(
        db.query(func.count(LogEntry.id))
        .filter(
            LogEntry.timestamp >= (datetime.now(timezone.utc) - timedelta(hours=24)),
            LogEntry.level.in_(("ERROR", "CRITICAL")),
        )
        .scalar()
        or 0
    )

    top_strategy_rows = sorted(
        list(stats.get("by_strategy") or []),
        key=lambda x: (
            int(x.get("sample_size") or 0),
            float(x.get("win_rate") or 0.0),
            float(x.get("avg_return_pct") or 0.0),
        ),
        reverse=True,
    )[:8]

    return {
        "generated_at": _format_datetime(datetime.now(timezone.utc)),
        "market": mkt,
        "snapshot_date": snapshot_date,
        "data_freshness": {
            "strategy_snapshot_date": snapshot_date,
            "entry_snapshot_date": latest_entry_snapshot[0] if latest_entry_snapshot else "",
            "market_scan_snapshot_date": latest_market_scan_snapshot[0]
            if latest_market_scan_snapshot
            else "",
            "latest_history_updated_at": _format_datetime(latest_history_updated_at),
        },
        "kpis": {
            "watchlist_count": watchlist_count,
            "positions_count": len(positions),
            "available_funds": round(total_available, 2),
            "invested_cost": round(float(invested_cost), 2),
            "total_assets_estimate": round(float(total_available + invested_cost), 2),
            # Disclosure: these two figures cover only positions whose currency could
            # be converted. Holdings excluded here keep their native cost on the
            # portfolio page; they are not zero and not cash.
            "invested_cost_complete": excluded_unconvertible == 0,
            "excluded_unconvertible_positions": int(excluded_unconvertible),
            "executable_opportunities": len(action_items),
            "risk_positions": len(risk_items),
            "win_rate_3d": win_rate_3d,
            "win_sample_3d": sample_3d,
            "errors_24h": error_24h,
        },
        "portfolio": {
            "positions_count": len(positions),
            "watchlist_count": watchlist_count,
            "available_funds": round(total_available, 2),
            "invested_cost": round(float(invested_cost), 2),
            "by_market": sorted(list(by_market.values()), key=lambda x: x["market"]),
        },
        "action_center": {
            "opportunities": action_items,
            "risk_items": risk_items,
        },
        "market_pulse": {
            "hot_stocks": hot_stocks,
            "hot_topics": hot_topics,
        },
        "strategy": {
            "coverage": stats.get("coverage") or {},
            "factor_stats": stats.get("factor_stats") or {},
            "by_market": stats.get("by_market") or [],
            "top_by_strategy": top_strategy_rows,
        },
        "insights": _load_latest_insights(db),
    }


from src.models.market import EQUITY_MARKETS, default_market, is_enabled
from src.web.api.accounts import fx_rate_for_market, resolve_fx_snapshot

logger = logging.getLogger(__name__)


# ── 今日必读 AI 策展(Phase C)────────────────────────────────────────────
class CurateCandidate(BaseModel):
    type: str
    symbol: str = ""
    name: str = ""
    market: str = ""
    signal: str = ""
    change_pct: float | None = None


class CurateRequest(BaseModel):
    candidates: list[CurateCandidate]
    model_id: int | None = None


@router.post("/curate")
async def curate_today(req: CurateRequest, db: Session = Depends(get_db)):
    """把首页候选事件交 AI 排序+精炼,返回 [{index, importance, why}];AI 失败按原序兜底。"""
    cands = req.candidates[:20]
    if not cands:
        return {"items": []}

    listing = "\n".join(
        f"{i}. [{c.type}] {c.name or c.symbol}"
        + (f" {c.change_pct:+.2f}%" if c.change_pct is not None else "")
        + (f" {c.signal}" if c.signal else "")
        for i, c in enumerate(cands)
    )
    system_prompt = (
        "You are a stock-monitoring assistant. From the user's candidate events for today, pick out the "
        "most noteworthy ones and rank them by importance, focusing on: triggered alerts, large position "
        "moves, and portfolio risk. "
        "Output one line per item only, format: index|importance (0-100 integer)|one-sentence reason it's "
        "worth reading. No explanations, no fabrication."
    )
    user_content = f"Today's candidates (all from this user's holdings/watchlist/alerts/opportunities):\n{listing}"

    items: list[dict] = []
    try:
        content = await _get_ai_client(db, req.model_id).chat(
            system_prompt, user_content, temperature=0.3
        )
        for line in (content or "").splitlines():
            parts = line.split("|")
            idx_raw = parts[0].strip().rstrip(".、) ")
            if len(parts) >= 3 and idx_raw.isdigit():
                i = int(idx_raw)
                if 0 <= i < len(cands):
                    try:
                        imp = int(re.sub(r"[^0-9]", "", parts[1]) or 0)
                    except Exception:
                        imp = 0
                    items.append({"index": i, "importance": imp, "why": parts[2].strip()})
    except Exception as e:
        logger.debug(f"curate AI failed, falling back to original order: {e}")

    if not items:  # Fallback: original order + decreasing importance
        items = [
            {"index": i, "importance": max(0, 100 - i * 5), "why": c.signal or ""}
            for i, c in enumerate(cands)
        ]
    items.sort(key=lambda x: x["importance"], reverse=True)
    return {"items": items}


@router.get("/brief")
def get_brief(type: str = Query("eod", description="premarket | eod"), db: Session = Depends(get_db)):
    """盘前/盘后 AI 简报(复用 premarket_outlook / daily_report agent 的最新报告)。"""
    agent = "premarket_outlook" if type == "premarket" else "daily_report"
    label = "盘前分析" if type == "premarket" else "收盘复盘"
    row = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.agent_name == agent)
        .order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .first()
    )
    if not row:
        return {"empty": True, "type": type, "agent_label": label}
    return {
        "type": type,
        "agent_label": label,
        "title": row.title or "",
        "content": row.content or "",
        "date": row.analysis_date or "",
        "updated_at": _format_datetime(row.updated_at),
    }
