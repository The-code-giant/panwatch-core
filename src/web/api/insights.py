from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List

from sqlalchemy.orm import Session

from src.models.market import MarketCode
from src.core.marketdata_client import md_filings, md_holders, md_quote_rows
from src.collectors.kline_collector import KlineCollector
from src.core.suggestion_pool import get_latest_suggestions
from src.web.api.chat import (
    _build_stock_context,
    _fetch_realtime_context,
    _fetch_technical_context,
    _get_ai_client,
)
from src.collectors.market_http import TTLCache
from src.web.database import get_db
from src.web.models import Stock
import asyncio
import logging
import time
from src.models.market import default_market, is_enabled, normalize_market

logger = logging.getLogger(__name__)

# Announcement interpretation cache (filings do not change; long TTL)
_ANN_CACHE = TTLCache(default_ttl_sec=21600)  # 6h

_CA_FILINGS_NOTE = (
    "SEDAR+ offers no free API; company press releases (CNW, GlobeNewswire) are shown instead."
)

router = APIRouter()


class InsightItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class InsightsBatchRequest(BaseModel):
    items: List[InsightItem]


def _parse_market(market: str) -> MarketCode:
    code = (market or default_market()).strip().upper()
    try:
        mc = MarketCode(code)
    except ValueError:
        raise HTTPException(400, f"Unsupported market: {market}")
    if not is_enabled(mc):
        raise HTTPException(400, f"Market not enabled: {code}")
    return mc


@router.post("/batch")
def insights_batch(payload: InsightsBatchRequest):
    """聚合返回行情 + K线摘要 + 最新建议"""
    if not payload.items:
        return []

    # 1) 批量行情（按市场）
    market_items: dict[MarketCode, list[str]] = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        market_items.setdefault(market_code, []).append(it.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        try:
            items = md_quote_rows(symbols, market_code.value)
        except Exception:
            items = []
        quotes_by_market[market_code] = {item["symbol"]: item for item in items}

    # 2) K线摘要（逐只，带 60s 简易缓存）
    kline_by_symbol: dict[str, dict] = {}
    now = time.time()
    TTL = 60.0
    # module-level cache
    global _KLINE_CACHE
    try:
        _KLINE_CACHE
    except NameError:
        _KLINE_CACHE = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        cache_key = f"{market_code.value}:{it.symbol}"
        cached = _KLINE_CACHE.get(cache_key)
        summary = None
        if cached and (now - cached[0] < TTL):
            summary = cached[1]
        else:
            try:
                collector = KlineCollector(market_code)
                summary = collector.get_kline_summary(it.symbol)
            except Exception:
                summary = {}
            _KLINE_CACHE[cache_key] = (now, summary)
        kline_by_symbol[cache_key] = summary

    # 3) 最新建议（建议池）
    stock_keys = [(it.symbol, _parse_market(it.market).value) for it in payload.items]
    latest_sugs = get_latest_suggestions(stock_keys=stock_keys, include_expired=False)

    # 4) 合并返回
    results = []
    for it in payload.items:
        market_code = _parse_market(it.market)
        quote = quotes_by_market.get(market_code, {}).get(it.symbol)
        results.append({
            "symbol": it.symbol,
            "market": market_code.value,
            "quote": {
                "name": quote.get("name") if quote else None,
                "current_price": quote.get("current_price") if quote else None,
                "change_pct": quote.get("change_pct") if quote else None,
                "open_price": quote.get("open_price") if quote else None,
                "high_price": quote.get("high_price") if quote else None,
                "low_price": quote.get("low_price") if quote else None,
                "volume": quote.get("volume") if quote else None,
                "turnover": quote.get("turnover") if quote else None,
            },
            "kline_summary": kline_by_symbol.get(f"{market_code.value}:{it.symbol}", {}),
            "suggestion": latest_sugs.get(f"{market_code.value}:{it.symbol}"),
        })

    return results


class AddPositionEvalRequest(BaseModel):
    symbol: str
    market: str = ""
    current_quantity: float = Field(0, ge=0, description="当前持仓股数(0=建仓)")
    current_cost: float = Field(0, ge=0, description="当前成本(单价)")
    add_quantity: float = Field(..., gt=0, description="加仓股数")
    add_price: float = Field(..., gt=0, description="加仓价格")
    model_id: int | None = None


_VERDICTS = ("Not Suitable", "Caution", "Suitable")  # longest-first: 'Not Suitable' contains 'Suitable', order matters


def _parse_verdict(text: str) -> str:
    """Roughly parse the verdict label from the AI reply; returns 'Unknown' if no match."""
    head = (text or "")[:160]
    for v in _VERDICTS:
        if v in head:
            return v
    return "Unknown"


async def _fetch_fundamental_context(symbol: str, market: str) -> str:
    """基本面摘要:PE / 换手率 / 市值 / 今日振幅(取自实时行情,失败返回空)。"""
    try:
        mc = MarketCode(normalize_market(market))
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        parts: list[str] = []
        if q.get("pe_ratio") not in (None, 0):
            parts.append(f"P/E {q['pe_ratio']}")
        if q.get("turnover_rate") not in (None, 0):
            parts.append(f"turnover rate {q['turnover_rate']}%")
        if q.get("circulating_market_value"):
            parts.append(f"free-float market cap {q['circulating_market_value']}B")
        if q.get("total_market_value"):
            parts.append(f"total market cap {q['total_market_value']}B")
        hi, lo, pc = q.get("high_price"), q.get("low_price"), q.get("prev_close")
        if hi and lo and pc:
            parts.append(f"today's amplitude {(hi - lo) / pc * 100:.2f}%")
        return ("Fundamentals: " + ", ".join(parts)) if parts else ""
    except Exception as e:
        logger.debug(f"Failed to fetch fundamentals {symbol}: {e}")
        return ""


async def _fetch_message_context(db: Session, symbol: str, market: str) -> str:
    """消息面摘要:近 3 天新闻/公告标题 + 本地最近 AI 建议/分析(失败降级为空)。"""
    parts: list[str] = []
    try:
        from src.collectors.news_collector import NewsCollector

        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        name = stock.name if stock else symbol
        collector = NewsCollector.from_database()
        items = await collector.fetch_all(
            symbols=[symbol], since_hours=72, symbol_names={symbol: name}
        )
        items = sorted(items, key=lambda x: x.publish_time, reverse=True)[:5]
        if items:
            lines = [
                f"- {it.title} ({it.publish_time.strftime('%m-%d')})" for it in items
            ]
            parts.append("Recent news/announcements:\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"Failed to fetch news context {symbol}: {e}")

    try:
        ctx = _build_stock_context(db, symbol, market)
        if ctx:
            parts.append(ctx)
    except Exception:
        pass

    return "\n\n".join(parts)


@router.post("/add-position-eval")
async def add_position_eval(req: AddPositionEvalRequest, db: Session = Depends(get_db)):
    """加仓快速评估:按服务端口径算摊薄成本 + 让 AI 给 适合/谨慎/不适合 结论。"""
    market = _parse_market(req.market).value
    cur_q = max(0.0, float(req.current_quantity or 0))
    cur_c = max(0.0, float(req.current_cost or 0))
    add_q = float(req.add_quantity)
    add_p = float(req.add_price)
    if add_q <= 0 or add_p <= 0:
        raise HTTPException(400, "Add quantity and price must be greater than 0")

    new_q = cur_q + add_q
    new_cost = (cur_q * cur_c + add_q * add_p) / new_q if new_q > 0 else add_p
    is_add = cur_q > 0 and cur_c > 0
    dilute_abs = (cur_c - new_cost) if is_add else 0.0
    dilute_pct = (dilute_abs / cur_c * 100) if is_add and cur_c > 0 else 0.0
    action = "add to position" if is_add else "open position"

    # 上下文:实时行情 + 基本面 + 技术面 + 消息面(新闻/公告/本地观点)
    realtime = await _fetch_realtime_context(req.symbol, market)
    fundamental = await _fetch_fundamental_context(req.symbol, market)
    technical = await _fetch_technical_context(req.symbol, market)
    message = await _fetch_message_context(db, req.symbol, market)

    holding_line = (
        f"Current position {cur_q:.0f} shares, cost basis {cur_c:.3f}"
        if is_add
        else "No current position (this would be a new position)"
    )
    dilute_line = f", cost reduced by {dilute_abs:.3f} ({dilute_pct:.2f}%) vs. current cost" if is_add else ""
    user_content = (
        f"Symbol {market}:{req.symbol}\n"
        f"{holding_line}\n"
        f"Planning to {action} {add_q:.0f} shares @ {add_p:.3f}\n"
        f"Cost basis after {action}: {new_cost:.3f}{dilute_line}\n"
        + (f"{realtime}\n" if realtime else "")
        + (f"{fundamental}\n" if fundamental else "")
        + (f"{technical}\n" if technical else "")
        + (f"{message}\n" if message else "")
        + f"Please assess, based on valuation/fundamentals and news, whether this {action} makes sense."
    )
    system_prompt = (
        "You are a cautious, practical stock trading assistant. Based on the position, price, fundamentals, "
        "technicals, and news information the user provides, "
        f"assess whether this {action} makes sense. Do not fabricate data or promise returns.\n"
        "Output strictly in the following format, concisely:\n"
        "Verdict: Suitable / Caution / Not Suitable (choose one)\n"
        "Reasons:\n- (2-3 points, covering cost dilution, valuation/fundamentals, technicals, and news)\n"
        "Risk: (one sentence on the biggest risk)"
    )

    try:
        client = _get_ai_client(db, req.model_id)
        content = await client.chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI evaluation failed: {e}")

    return {
        "symbol": req.symbol,
        "market": market,
        "action": action,
        "new_cost": round(new_cost, 4),
        "dilute_abs": round(dilute_abs, 4),
        "dilute_pct": round(dilute_pct, 4),
        "total_quantity": new_q,
        "total_invested": round(new_q * new_cost, 2),
        "verdict": _parse_verdict(content),
        "content": content,
    }


# ── Announcement/earnings positive/negative interpretation (Phase B) ──────────────────────────────────────
_ANN_TONES = ("Positive", "Negative", "Neutral")


def _parse_tone(text: str) -> str:
    head = (text or "")[:60]
    for t in _ANN_TONES:
        if t in head:
            return t
    return "Neutral"


async def _fetch_recent_announcements(
    symbol: str, name: str, limit: int = 5, market: str = ""
) -> list[dict]:
    """Recent regulatory filings (SEC EDGAR / CA press releases), falling back to the
    last 7 days of news when no filings come back; [] on failure."""
    try:
        filings = await asyncio.to_thread(md_filings, [symbol], market or None, limit)
    except Exception as e:
        logger.debug(f"Failed to fetch filings {symbol}: {e}")
        filings = []
    if filings:
        out = []
        for f in filings[:limit]:
            filed = str(f.get("filed_at") or "")
            title = f.get("title") or f.get("form_type") or ""
            form = f.get("form_type") or ""
            if form and form not in title:
                title = f"{form}: {title}" if title else form
            out.append(
                {
                    "title": title,
                    "time": filed[:16].replace("T", " "),
                    "content": (f.get("description") or "")[:200],
                }
            )
        return out

    try:
        from src.collectors.news_collector import NewsCollector

        items = await NewsCollector.from_database().fetch_all(
            symbols=[symbol], since_hours=168, symbol_names={symbol: name}
        )
        anns = sorted(items, key=lambda x: x.publish_time, reverse=True)[:limit]
        return [
            {
                "title": a.title,
                "time": a.publish_time.strftime("%Y-%m-%d %H:%M"),
                "content": (a.content or "")[:200],
            }
            for a in anns
        ]
    except Exception as e:
        logger.debug(f"Failed to fetch announcements {symbol}: {e}")
        return []


class AnnouncementEvalRequest(BaseModel):
    symbol: str
    market: str = ""
    model_id: int | None = None


@router.post("/announcement-eval")
async def announcement_eval(req: AnnouncementEvalRequest, db: Session = Depends(get_db)):
    """Recent announcements → AI judges each as Positive/Negative/Neutral + a one-sentence reason. Fallback: use the title if no full text."""
    market = _parse_market(req.market).value
    cache_key = f"{market}:{req.symbol}"
    cached = _ANN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    stock = db.query(Stock).filter(Stock.symbol == req.symbol).first()
    name = stock.name if stock else req.symbol
    anns = await _fetch_recent_announcements(req.symbol, name, market=market)
    if not anns:
        result = {"symbol": req.symbol, "market": market, "items": []}
        _ANN_CACHE.set(cache_key, result, ttl_sec=600)  # short cache when there is no data
        return result

    top = anns[:3]
    listing = "\n".join(
        f"{i + 1}. {a['title']}({a['time']})" + (f" — {a['content']}" if a["content"] else "")
        for i, a in enumerate(top)
    )
    system_prompt = (
        "You are a stock announcement interpretation assistant. For each announcement, judge its "
        "likely impact on the stock price (Positive/Negative/Neutral) and give a one-sentence reason, "
        "based only on the given information — do not fabricate. Strictly one line per item, format: "
        "index|Positive or Negative or Neutral|one-sentence reason"
    )
    user_content = f"Recent announcements for {name}({market}:{req.symbol}):\n{listing}"
    try:
        content = await _get_ai_client(db, req.model_id).chat(
            system_prompt, user_content, temperature=0.2
        )
    except Exception as e:
        raise HTTPException(502, f"AI announcement interpretation failed: {e}")

    tone_map: dict[int, tuple[str, str]] = {}
    for line in (content or "").splitlines():
        parts = line.split("|")
        idx_raw = parts[0].strip().rstrip(".、) ") if parts else ""
        if len(parts) >= 3 and idx_raw.isdigit():
            tone_map[int(idx_raw) - 1] = (_parse_tone(parts[1]), parts[2].strip())

    items = []
    for i, a in enumerate(top):
        tone, note = tone_map.get(i, ("Neutral", ""))
        items.append({"title": a["title"], "time": a["time"], "tone": tone, "summary": note})
    result = {"symbol": req.symbol, "market": market, "items": items}
    _ANN_CACHE.set(cache_key, result)
    return result


# ── Filings and holders (package-backed, read-only) ─────────────────────────
_BREAKDOWN_KEYS = {
    "insiderspercentheld": "insiders_pct",
    "institutionspercentheld": "institutions_pct",
    "institutionsfloatpercentheld": "institutions_float_pct",
    "institutionscount": "institutions_count",
}


def _breakdown_key(holder: str) -> str | None:
    """Map a breakdown row label (``insidersPercentHeld`` or ``Insiders % held``) to its key."""
    raw = (holder or "").strip().lower().replace("%", "percent")
    norm = "".join(ch for ch in raw if ch.isalnum())
    return _BREAKDOWN_KEYS.get(norm)


def _split_holders(rows: list[dict]) -> dict:
    breakdown: dict = {
        "insiders_pct": None,
        "institutions_pct": None,
        "institutions_float_pct": None,
        "institutions_count": None,
    }
    institutions: list[dict] = []
    insider_tx: list[dict] = []
    source = ""
    for r in rows or []:
        kind = r.get("kind")
        if not source and r.get("source"):
            source = str(r["source"])
        if kind == "breakdown":
            key = _breakdown_key(str(r.get("holder") or ""))
            if not key:
                continue
            if key == "institutions_count":
                val = r.get("shares")
                breakdown[key] = int(val) if val is not None else None
            else:
                breakdown[key] = r.get("pct_out")
        elif kind == "institution":
            institutions.append(r)
        elif kind == "insider_tx":
            insider_tx.append(r)
    return {
        "breakdown": breakdown,
        "institutions": institutions,
        "insider_transactions": insider_tx,
        "source": source,
    }


@router.get("/filings")
async def insights_filings(
    symbol: str = Query(default="", description="Symbol, e.g. AAPL or SHOP.TO"),
    market: str = Query(default="", description="Market code (US/CA); blank = detect"),
    limit: int = Query(default=20, ge=1, le=100),
    forms: str = Query(default="", description="Comma separated form_type filter, e.g. 8-K,10-Q"),
):
    """Regulatory filings (SEC EDGAR) or, for Canadian issuers, company press releases."""
    sym = (symbol or "").strip()
    if not sym:
        raise HTTPException(400, "symbol is required")
    mkt = (market or "").strip().upper()
    try:
        items = await asyncio.to_thread(md_filings, [sym], mkt or None, limit)
    except Exception as e:
        logger.warning(f"Filings fetch failed {sym}: {e}")
        items = []
    wanted = {f.strip().upper() for f in forms.split(",") if f.strip()} if forms else set()
    if wanted:
        items = [f for f in items if str(f.get("form_type") or "").upper() in wanted]
    return {
        "symbol": sym,
        "market": mkt,
        "items": items[:limit],
        "note": _CA_FILINGS_NOTE if mkt == "CA" else "",
    }


@router.get("/holders")
async def insights_holders(
    symbol: str = Query(default="", description="Symbol, e.g. AAPL or SHOP.TO"),
    market: str = Query(default="", description="Market code (US/CA); blank = detect"),
):
    """Ownership breakdown, top institutional holders and recent insider transactions."""
    sym = (symbol or "").strip()
    if not sym:
        raise HTTPException(400, "symbol is required")
    mkt = (market or "").strip().upper()
    try:
        rows = await asyncio.to_thread(md_holders, [sym], mkt or None)
    except Exception as e:
        logger.warning(f"Holders fetch failed {sym}: {e}")
        rows = []
    return _split_holders(rows)
