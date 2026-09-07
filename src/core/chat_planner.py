"""Planning pilot —— plan-driven orchestration for "comprehensive portfolio diagnosis".

Scope is deliberately narrow: covers only a single scenario (comprehensive portfolio
diagnosis). Once that intent is recognized, it goes plan-driven: the LLM generates a
structured plan (per-position analysis → portfolio risk → summary recommendations) →
the plan is pushed to the frontend via the SSE `plan` event → executed step by step
(each step reuses existing tools/LLM) → on step failure, replan (capped at 1 retry;
beyond that, summarize directly including the failure info).

This is a **pilot**: it validates the value of "plan-driven" over a fixed pipeline,
without over-generalizing. The orchestration function injects the tool executor
(execute_tool) and the SSE stream (stream) as dependencies, making it easy to fully
mock in unit tests.
"""

import json
import logging
import re
from src.models.market import default_market

logger = logging.getLogger(__name__)

# Trigger phrases: an explicit hit goes plan-driven (a simple heuristic, good enough for the pilot)
_PLANNING_TRIGGERS = (
    "全面诊断",
    "诊断我的持仓",
    "诊断一下我的持仓",
    "持仓诊断",
    "组合诊断",
    "全面体检",
    "持仓体检",
    "全面分析我的持仓",
)


def should_use_planning(content: str) -> bool:
    """Determine whether the user's input hits the "comprehensive portfolio diagnosis" scenario."""
    if not content:
        return False
    text = content.replace(" ", "")
    return any(t in text for t in _PLANNING_TRIGGERS)


_PLAN_SYSTEM = (
    "You are a portfolio diagnosis planning assistant. Based on the user's holdings, "
    "produce a structured diagnosis plan. Output JSON only, in the form:"
    '{"steps":[{"title":"Analyze NVIDIA(NVDA)","action":"analyze_stock",'
    '"params":{"symbol":"NVDA","market":"US"}},'
    '{"title":"Overall portfolio risk","action":"portfolio_risk"}]}. '
    "Valid action values: analyze_stock (one per holding, params carry symbol/market), "
    "portfolio_risk (portfolio-level risk). "
    "Do not include a summary step — the summary is appended automatically by the system."
)


def _plan_messages(portfolio_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"My holdings are as follows, please produce a diagnosis plan:\n{portfolio_text}"},
    ]


def _replan_messages(
    portfolio_text: str, failed_title: str, error: str
) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"My holdings:\n{portfolio_text}\n\n"
                f'The step「{failed_title}」in the previous plan failed to execute ({error}), '
                "please produce a new executable diagnosis plan (skip or replace the failed step)."
            ),
        },
    ]


def parse_plan(text: str) -> list[dict] | None:
    """Fault-tolerant parsing of the step list from the LLM's plan text.

    Supports: plain JSON, ```json fenced blocks, explanatory text before/after,
    truncated tails, and other common messy output.
    Returns None on parse failure (the caller falls back to a default plan).
    """
    if not text:
        return None

    blob = None
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if m:
        blob = m.group(1)
    else:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            blob = text[min(candidates):]

    if not blob:
        return None

    data = None
    for attempt in (blob, blob[: max(blob.rfind("]"), blob.rfind("}")) + 1]):
        try:
            data = json.loads(attempt)
            break
        except Exception:
            continue
    if data is None:
        return None

    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan")
    if not isinstance(data, list) or not data:
        return None
    return data


def build_default_plan(portfolio_text: str) -> list[dict]:
    """Fallback default plan when the LLM plan is unavailable (portfolio risk only; summary is appended by the system)."""
    return [{"title": "Overall portfolio risk assessment", "action": "portfolio_risk"}]


def normalize_steps(steps: list[dict], start_id: int = 1) -> list[dict]:
    """Normalize steps: fill in id/title/action/params/status. Filter out summarize (the summary is done automatically by the system)."""
    out = []
    sid = start_id
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = s.get("action") or "portfolio_risk"
        if action == "summarize":
            continue
        out.append(
            {
                "id": sid,
                "title": s.get("title") or f"Step {sid}",
                "action": action,
                "params": s.get("params") or {},
                "status": "pending",
            }
        )
        sid += 1
    return out


def _steps_public(steps: list[dict]) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "status": s["status"]} for s in steps]


async def _publish_plan(stream, steps: list[dict], status: str, current=None) -> None:
    data = {"status": status, "steps": _steps_public(steps)}
    if current is not None:
        data["current"] = current
    await stream.publish("plan", data)


_STEP_SYSTEM = "You are a senior investment research analyst. Based on the given data, provide a concise, well-supported analysis (within 150 words)."
_SUMMARY_SYSTEM = (
    "You are a senior investment advisor. Based on the analysis results from each step, "
    "give a comprehensive portfolio diagnosis conclusion: overall health, main risks, "
    "and actionable rebalancing recommendations. Use bullet points, be concise and well-supported."
)


async def _execute_step(db, ai_client, execute_tool, step: dict, portfolio_text: str) -> str:
    """Execute a single plan step and return its analysis text."""
    action = step["action"]
    if action == "analyze_stock":
        p = step.get("params") or {}
        symbol = p.get("symbol", "")
        market = p.get("market") or default_market()
        tech = await execute_tool(db, "get_technical_analysis", {"symbol": symbol, "market": market})
        sug = await execute_tool(db, "get_stock_suggestions", {"symbol": symbol, "market": market})
        msgs = [
            {"role": "system", "content": _STEP_SYSTEM},
            {
                "role": "user",
                "content": f"Analyze the holding「{step['title']}」.\nTechnicals:\n{tech}\n\nAI suggestions:\n{sug}",
            },
        ]
        return await ai_client.chat_multi(msgs, temperature=0.4)

    # portfolio_risk and any other unknown action: treat uniformly as portfolio risk
    msgs = [
        {"role": "system", "content": _STEP_SYSTEM},
        {"role": "user", "content": f"Assess the overall risk of the following portfolio:\n{portfolio_text}"},
    ]
    return await ai_client.chat_multi(msgs, temperature=0.4)


def _summary_messages(results: list[tuple[str, str]]) -> list[dict]:
    body = "\n\n".join(f"【{title}】\n{res}" for title, res in results)
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": f"Here are the diagnosis results from each step, please summarize them:\n\n{body}"},
    ]


async def run_portfolio_diagnosis(db, stream, ai_client, execute_tool) -> str:
    """Plan-driven orchestration for "comprehensive portfolio diagnosis"; returns the final
    summary text (already pushed via the SSE stream).

    Args:
        db: DB session.
        stream: SSEStream (must support async publish(event, data)).
        ai_client: AI client (chat_multi / chat_stream).
        execute_tool: async (db, name, args) -> str tool executor.
    """
    await stream.publish("plan", {"status": "planning", "steps": []})

    portfolio_text = await execute_tool(db, "get_portfolio", {})

    # 1) Generate the plan (fall back to the default plan on failure/parse error)
    steps = None
    try:
        raw = await ai_client.chat_multi(_plan_messages(portfolio_text), temperature=0.3)
        steps = parse_plan(raw)
    except Exception:
        logger.warning("Failed to generate diagnosis plan, falling back to default plan", exc_info=True)
    if not steps:
        steps = build_default_plan(portfolio_text)
    steps = normalize_steps(steps)
    if not steps:
        steps = normalize_steps(build_default_plan(portfolio_text))

    await _publish_plan(stream, steps, status="running")

    # 2) Execute step by step, replan on failure (capped at 1 retry)
    results: list[tuple[str, str]] = []
    replanned = False
    i = 0
    while i < len(steps):
        step = steps[i]
        step["status"] = "running"
        await _publish_plan(stream, steps, status="running", current=step["id"])
        try:
            res = await _execute_step(db, ai_client, execute_tool, step, portfolio_text)
            step["status"] = "done"
            results.append((step["title"], res))
        except Exception as e:  # noqa: BLE001
            if not replanned:
                replanned = True
                logger.info("Step「%s」failed, triggering replan: %s", step["title"], e)
                try:
                    raw = await ai_client.chat_multi(
                        _replan_messages(portfolio_text, step["title"], str(e)),
                        temperature=0.3,
                    )
                    new_steps = parse_plan(raw)
                except Exception:
                    new_steps = None
                if new_steps:
                    steps = steps[:i] + normalize_steps(new_steps, start_id=step["id"])
                    await _publish_plan(stream, steps, status="running")
                    continue  # retry from the current position with the new plan
            # Already replanned once or replanning failed: mark as failed and continue to summary with the failure info
            step["status"] = "failed"
            results.append((step["title"], f"(This step failed to execute: {e})"))
        await _publish_plan(stream, steps, status="running")
        i += 1

    # 3) Summarize (stream tokens)
    summary = ""
    try:
        parts: list[str] = []
        async for kind, payload in ai_client.chat_stream(
            _summary_messages(results), temperature=0.4
        ):
            if kind == "token":
                parts.append(payload)
                await stream.publish("token", {"text": payload})
        summary = "".join(parts)
    except Exception as e:  # noqa: BLE001 — fall back to non-streaming if streaming summary fails
        logger.warning("Streaming summary failed, falling back to non-streaming: %s", e)
        try:
            summary = await ai_client.chat_multi(_summary_messages(results), temperature=0.4)
            await stream.publish("token", {"text": summary})
        except Exception:
            summary = "Sorry, the diagnosis summary failed."
            await stream.publish("token", {"text": summary})

    await _publish_plan(stream, steps, status="done")
    return summary
