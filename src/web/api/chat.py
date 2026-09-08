"""AI chat API endpoints."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.ai_failover import FailoverAIClient, build_failover_client
from src.core.chat_planner import run_portfolio_diagnosis, should_use_planning
from src.core.sse import SSEStream, chat_stream_hub
from src.models.market import MarketCode
from src.web.database import SessionLocal, get_db
from src.web.models import (
    AIModel,
    AIService,
    AnalysisHistory,
    ChatConversation,
    ChatMessage,
    PaperTradingPosition,
    Position,
    Stock,
    StockSuggestion,
)
from src.models.market import EQUITY_MARKETS, default_market, normalize_market

_DEFAULT_MARKET = default_market()
_MARKET_ARG_DESC = "Market code: " + "/".join(EQUITY_MARKETS)

logger = logging.getLogger(__name__)
router = APIRouter()

SYSTEM_PROMPT = """You are TickerKeep's AI investment assistant.

You can use tools to fetch the user's investment data. When a question involves specific data, proactively call the relevant tool to get it instead of asking the user to provide it themselves.

Rules:
- Proactively call tools when you need data — don't ask the user for it
- Base your answers on the real-time data returned by the tools; never fabricate prices or other specific figures
- Give a clear opinion with reasoning
- Note the risks whenever you give buy/sell suggestions
- Always respond in English
- Keep responses concise and avoid redundancy"""

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Tool Definitions ────────────────

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Fetches the user's live and paper-trading positions. Used to answer position-related questions (e.g. is the position healthy, should it be rebalanced, what's the P&L, etc.).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "Fetches real-time quote data for a stock (price, change percentage, volume, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol, e.g. NVDA or SHOP.TO"},
                    "market": {"type": "string", "description": _MARKET_ARG_DESC, "default": _DEFAULT_MARKET},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "Fetches technical analysis for a stock (trend, MACD, RSI, support level, resistance level, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol"},
                    "market": {"type": "string", "description": _MARKET_ARG_DESC, "default": _DEFAULT_MARKET},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "Fetches recent AI suggestions and analysis reports for a stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock symbol"},
                    "market": {"type": "string", "description": _MARKET_ARG_DESC, "default": _DEFAULT_MARKET},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "Fetches the user's watchlist (self-selected stocks).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _build_watchlist_context(db: Session) -> str:
    """Builds the user's watchlist."""
    stocks = db.query(Stock).order_by(Stock.sort_order.asc()).all()
    if not stocks:
        return "The user has no watchlist stocks yet."
    lines = [f"- {s.name}({s.market}:{s.symbol})" for s in stocks]
    return "Watchlist:\n" + "\n".join(lines)


async def _execute_tool(db: Session, name: str, args: dict) -> str:
    """Executes a tool call and returns the result text."""
    try:
        if name == "get_portfolio":
            result = _build_portfolio_context(db)
            return result or "The user has no positions."
        elif name == "get_stock_quote":
            symbol = args.get("symbol", "")
            market = args.get("market") or _DEFAULT_MARKET
            result = await _fetch_realtime_context(symbol, market)
            return result or f"Failed to fetch quote data for {market}:{symbol}."
        elif name == "get_technical_analysis":
            symbol = args.get("symbol", "")
            market = args.get("market") or _DEFAULT_MARKET
            result = await _fetch_technical_context(symbol, market)
            return result or f"Failed to fetch technical analysis data for {market}:{symbol}."
        elif name == "get_stock_suggestions":
            symbol = args.get("symbol", "")
            market = args.get("market") or _DEFAULT_MARKET
            result = _build_stock_context(db, symbol, market)
            return result or f"No AI suggestions available for {market}:{symbol}."
        elif name == "get_watchlist":
            return _build_watchlist_context(db)
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        logger.error(f"Tool execution failed {name}: {e}")
        return f"Tool execution error: {e}"


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str


def _get_ai_client(db: Session, model_id: int | None = None) -> FailoverAIClient:
    """Gets an AI client with failover support (the primary model still uses the
    three-tier selection; fallbacks are filled in from the DB).

    All direct callers — the chat tool loop, the portfolio diagnosis, etc. — go
    through this entry point. When the primary model times out, is rate-limited,
    or goes down, it automatically falls back to a secondary model. The model
    actually used is recorded in used_model_label, which can be surfaced in the
    done event for transparent display on the frontend. The returned
    FailoverAIClient is interface-compatible with AIClient and can be swapped in
    as a drop-in replacement.
    """
    model = None
    service = None
    if model_id:
        model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712
    if not model:
        model = db.query(AIModel).first()
    if model:
        service = db.query(AIService).filter(AIService.id == model.service_id).first()
    return build_failover_client(model, service, db=db)


def _build_stock_context(db: Session, symbol: str, market: str) -> str:
    """Builds a context summary for the bound stock."""
    parts = []

    # Recent suggestions
    suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = []
        for s in suggestions:
            lines.append(f"- [{s.agent_label or s.agent_name}] {s.action_label}: {s.signal or s.reason or ''}")
        parts.append("Recent AI suggestions:\n" + "\n".join(lines))

    # Recent analysis report
    histories = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.stock_symbol == symbol)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        h = histories[0]
        content_preview = (h.content or "")[:500]
        parts.append(f"Recent analysis ({h.agent_name}, {h.analysis_date}):\n{content_preview}")

    if not parts:
        return ""
    return "\n\n".join(parts)


def _build_portfolio_context(db: Session) -> str:
    """Builds a summary of all the user's positions."""
    lines: list[str] = []

    # Live positions
    positions = db.query(Position).all()
    if positions:
        real_lines = []
        for p in positions:
            stock = db.query(Stock).filter(Stock.id == p.stock_id).first()
            if not stock:
                continue
            real_lines.append(
                f"- {stock.name}({stock.market}:{stock.symbol}) "
                f"{p.quantity} shares, cost {p.cost_price}, style {p.trading_style or 'swing'}"
            )
        if real_lines:
            lines.append("Live positions:\n" + "\n".join(real_lines))

    # Paper trading positions
    paper_positions = (
        db.query(PaperTradingPosition)
        .filter(PaperTradingPosition.status == "open")
        .all()
    )
    if paper_positions:
        paper_lines = []
        for pp in paper_positions:
            pnl_str = f"unrealized P&L {pp.unrealized_pnl:.1f}" if pp.unrealized_pnl else ""
            paper_lines.append(
                f"- {pp.stock_name or pp.stock_symbol}({pp.stock_market}:{pp.stock_symbol}) "
                f"{pp.quantity} shares, entry price {pp.entry_price}"
                f"{f', stop loss {pp.stop_loss}' if pp.stop_loss else ''}"
                f"{f', target {pp.target_price}' if pp.target_price else ''}"
                f"{f', {pnl_str}' if pnl_str else ''}"
            )
        if paper_lines:
            lines.append("Paper trading positions:\n" + "\n".join(paper_lines))

    if not lines:
        return ""
    return "\n\n".join(lines)


async def _fetch_realtime_context(symbol: str, market: str) -> str:
    """Asynchronously fetches real-time quote data."""
    try:
        from src.core.marketdata_client import md_quote_rows
        from src.models.market import MarketCode

        mc = MarketCode(normalize_market(market))
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        price = q.get("current_price", "--")
        change = q.get("change_pct", "--")
        volume = q.get("volume", "--")
        name = q.get("name", symbol)
        return f"Real-time quote: {name} ({market}:{symbol}) price {price}, change {change}%, volume {volume}"
    except Exception as e:
        logger.debug(f"Failed to fetch real-time quote: {e}")
        return ""


async def _fetch_technical_context(symbol: str, market: str) -> str:
    """Fetches a technical analysis summary."""
    try:
        from src.core.data_collector import DataCollector

        collector = DataCollector()
        summary = await asyncio.to_thread(
            collector.get_kline_summary, symbol, market
        )
        if not summary or summary.get("error"):
            return ""
        s = summary.get("summary", {})
        trend = s.get("trend", "--")
        macd = s.get("macd_status", "--")
        rsi = s.get("rsi_14", "--")
        support = s.get("support_level", "--")
        resistance = s.get("resistance_level", "--")
        return f"Technical analysis: trend {trend}, MACD {macd}, RSI {rsi}, support {support}, resistance {resistance}"
    except Exception as e:
        logger.debug(f"Failed to fetch technical analysis: {e}")
        return ""


@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="Stock symbol"),
    market: str = Query("", description="Market"),
    db: Session = Depends(get_db),
):
    """Generates recommended questions based on the stock's current state (pure template, no AI call)."""
    questions: list[str] = []

    # Look up the most recent suggestion
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"Is the latest \"{label}\" signal reliable? What's the entry timing like?")
        elif action in ("sell", "reduce"):
            questions.append(f"The latest suggestion is \"{label}\" — should I act on it now?")
        elif action == "alert":
            questions.append("What's behind the recent unusual-movement alert? Should I be watching it?")

    # Check positions (Position links to Stock via stock_id)
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("Should I keep holding the current position or consider trimming it?")
    else:
        questions.append("Is now a good time to open a position?")

    # Generic questions
    questions.append("Analyze the recent trend and key support/resistance levels")
    questions.append("Any news or events worth watching?")

    return {"questions": questions[:5]}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


def _save_user_message(db: Session, conv: ChatConversation, content: str) -> ChatMessage:
    """Saves the user message and generates a conversation title if needed (shared by the streaming and non-streaming paths)."""
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=content,
    )
    db.add(user_msg)

    # Update conversation title (use the first 20 chars of the first message)
    if not conv.title:
        conv.title = content[:20]

    db.commit()
    db.refresh(user_msg)
    return user_msg


async def _build_messages_for_ai(db: Session, conv: ChatConversation) -> list[dict]:
    """Builds the full messages payload sent to the model (system prompt + history +
    data context; shared by the streaming and non-streaming paths)."""
    messages_for_ai: list[dict] = []

    # System prompt
    system_content = SYSTEM_PROMPT

    # Bound stock hint
    if conv.stock_symbol and conv.stock_market:
        system_content += f"\n\nStock linked to this conversation: {conv.stock_market}:{conv.stock_symbol}"

    # Frontend page snapshot (passed in when the conversation was created)
    if conv.initial_context:
        system_content += "\n\n--- User page snapshot (at conversation creation) ---\n" + conv.initial_context

    messages_for_ai.append({"role": "system", "content": system_content})

    # History messages
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    recent = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    for m in recent:
        if m.role in ("user", "assistant"):
            messages_for_ai.append({"role": m.role, "content": m.content})

    # Inject base context (positions + quote/suggestions for the bound stock)
    context_parts: list[str] = []

    # User positions
    portfolio_ctx = _build_portfolio_context(db)
    if portfolio_ctx:
        context_parts.append(portfolio_ctx)

    # Real-time data for the bound stock
    if conv.stock_symbol and conv.stock_market:
        realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
        if realtime:
            context_parts.append(realtime)
        technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
        if technical:
            context_parts.append(technical)
        stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market)
        if stock_ctx:
            context_parts.append(stock_ctx)

    if context_parts:
        # Append the context to the system message
        messages_for_ai[0]["content"] += "\n\n--- Current Data ---\n" + "\n\n".join(context_parts)

    return messages_for_ai


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """Sends a message and gets the AI reply (non-streaming; kept for compatibility and as a fallback)."""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        _save_user_message(db, conv, body.content)
        messages_for_ai = await _build_messages_for_ai(db, conv)

        # Call the AI (with tool use, to fetch more data on demand; automatic failover if the primary model fails)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                    )
                except Exception:
                    # Model doesn't support tool use → fall back to chat_multi directly
                    logger.info("Tool use unavailable, falling back to plain chat")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    break

                if not response_msg.tool_calls:
                    ai_response = response_msg.content or ""
                    break

                # Execute tool calls
                messages_for_ai.append({
                    "role": "assistant",
                    "content": response_msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in response_msg.tool_calls
                    ],
                })

                for tc in response_msg.tool_calls:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info(f"Tool call: {tc.function.name}({tool_args})")
                    result = await _execute_tool(db, tc.function.name, tool_args)
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                ai_response = response_msg.content or "Sorry, too many processing rounds — please simplify your question and try again."

        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            ai_response = f"Sorry, the AI service is temporarily unavailable: {e}"

        # Save the AI reply
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)

        # Update conversation timestamp
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()


# ──────────────── SSE 流式对话 ────────────────
#
# 事件分型（均带自增 id，供 Last-Event-ID 续推）：
# - meta:            {stream_id, conversation_id, user_message_id} 首条，供断线重连定位流
# - token:           {text} 增量文本；工具调用轮的过渡性文本也会流出，前端在收到
#                    tool_call_start 时应清空当前缓冲（最终落库的只有末轮回答）
# - tool_call_start: {name, arguments} 模型决定调用工具（前端可视化"正在查询…"）
# - tool_result:     {name, ok, preview} 工具执行完成（preview 截断，完整结果只进模型上下文）
# - done:            {message_id, content, created_at} 最终回答（已落库）
# - error:           {message} AI 服务异常（错误文案同样落库，行为与非流式端点一致）
#
# 生成任务与 SSE 连接解耦：任务往 SSEStream 缓冲推事件，连接断开不影响生成与落库；
# 前端可用 GET /chat/streams/{stream_id} + Last-Event-ID 续推。

TOOL_RESULT_PREVIEW_CHARS = 200


async def _run_chat_stream_task(conversation_id: int, stream: SSEStream) -> None:
    """Runs chat generation in the background (tool loop + token stream), pushing events into the stream."""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            await stream.publish("error", {"message": "Conversation not found"})
            return

        messages_for_ai = await _build_messages_for_ai(db, conv)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""

        # P2 pilot: detect a "full portfolio diagnosis" intent → route to the plan-driven
        # flow (reuses the tool executor, pushes plan events to the frontend)
        latest_user = next(
            (m.get("content") or "" for m in reversed(messages_for_ai) if m.get("role") == "user"),
            "",
        )
        if should_use_planning(latest_user):
            try:
                ai_response = await run_portfolio_diagnosis(
                    db, stream, ai_client, _execute_tool
                )
            except Exception as e:
                logger.error(f"Plan-driven diagnosis failed: {e}")
                ai_response = f"Sorry, portfolio diagnosis failed: {e}"
                await stream.publish("error", {"message": str(e)})
        else:
            try:
                final_msg: dict | None = None
                for _round in range(MAX_TOOL_ROUNDS):
                    final_msg = None
                    try:
                        async for kind, payload in ai_client.chat_stream(
                            messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                        ):
                            if kind == "token":
                                await stream.publish("token", {"text": payload})
                            else:
                                final_msg = payload
                    except Exception:
                        # Model doesn't support tool use / streaming → fall back to plain chat (same strategy as the non-streaming endpoint)
                        logger.info("Streaming tool use unavailable, falling back to plain chat")
                        ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                        await stream.publish("token", {"text": ai_response})
                        break

                    tool_calls = (final_msg or {}).get("tool_calls") or []
                    if not tool_calls:
                        ai_response = (final_msg or {}).get("content") or ""
                        break

                    # Tool calls present: append the assistant message + tool results to the context and continue to the next round
                    messages_for_ai.append({
                        "role": "assistant",
                        "content": (final_msg or {}).get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in tool_calls
                        ],
                    })
                    for tc in tool_calls:
                        try:
                            tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            tool_args = {}
                        logger.info(f"Tool call(stream): {tc['name']}({tool_args})")
                        await stream.publish(
                            "tool_call_start", {"name": tc["name"], "arguments": tool_args}
                        )
                        result = await _execute_tool(db, tc["name"], tool_args)
                        await stream.publish(
                            "tool_result",
                            {
                                "name": tc["name"],
                                "ok": not result.startswith("Tool execution error"),
                                "preview": (result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                            },
                        )
                        messages_for_ai.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                else:
                    ai_response = (final_msg or {}).get("content") or "Sorry, too many processing rounds — please simplify your question and try again."

            except Exception as e:
                logger.error(f"AI streaming chat failed: {e}")
                ai_response = f"Sorry, the AI service is temporarily unavailable: {e}"
                await stream.publish("error", {"message": str(e)})

        # Persist to DB (regardless of whether the connection is still open, the result is always saved)
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        await stream.publish("done", {
            "message_id": assistant_msg.id,
            "content": ai_response,
            "created_at": str(assistant_msg.created_at or ""),
            # The actual model label used (may not be the primary model after failover), for transparent display on the frontend
            "model_label": getattr(ai_client, "used_model_label", ""),
        })
    except Exception as e:
        logger.error(f"Chat streaming task exception: {e}")
        try:
            await stream.publish("error", {"message": str(e)})
        except Exception:
            pass
    finally:
        await stream.finish()
        db.close()


def _sse_response(stream: SSEStream, after_seq: int = 0) -> StreamingResponse:
    """Wraps an SSEStream as a text/event-stream response (the response-wrapping middleware passes this content type straight through)."""
    return StreamingResponse(
        stream.subscribe(after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable buffering in reverse proxies like nginx so events are delivered in real time
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    body: SendMessageBody,
):
    """Sends a message and streams the AI reply back via SSE (token stream + visible tool-call process).

    The non-streaming endpoint POST /messages is left unchanged; the frontend falls back to it if streaming fails.
    """
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        user_msg = _save_user_message(db, conv, body.content)
        user_message_id = user_msg.id
    finally:
        db.close()

    stream = chat_stream_hub.create()
    # The meta event goes first: announces stream_id so the client can resume via GET /chat/streams/{stream_id} after a disconnect
    await stream.publish("meta", {
        "stream_id": stream.stream_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
    })
    # The generation task runs independently and is not aborted if this response connection is dropped
    asyncio.create_task(_run_chat_stream_task(conversation_id, stream))
    return _sse_response(stream)


@router.get("/streams/{stream_id}")
async def resume_message_stream(
    stream_id: str,
    request: Request,
    last_event_id: int = Query(0, ge=0, description="Last event sequence number received before the disconnect"),
):
    """Reconnect after a disconnect: resume from the buffer using Last-Event-ID (header takes priority, query param as fallback)."""
    stream = chat_stream_hub.get(stream_id)
    if not stream:
        raise HTTPException(404, "Stream not found or expired")
    header_id = request.headers.get("last-event-id", "")
    after_seq = int(header_id) if header_id.isdigit() else last_event_id
    return _sse_response(stream, after_seq=after_seq)
