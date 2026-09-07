"""Agent catalog and kind helpers.

Workflow agents are user-facing, schedulable pipelines.
Capability agents are internal/manual tools and should not be auto-scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass


AGENT_KIND_WORKFLOW = "workflow"
AGENT_KIND_CAPABILITY = "capability"

WORKFLOW_AGENT_NAMES: tuple[str, ...] = (
    "premarket_outlook",
    "intraday_monitor",
    "daily_report",
)

CAPABILITY_AGENT_NAMES: tuple[str, ...] = (
    "news_digest",
    "chart_analyst",
)


def infer_agent_kind(agent_name: str | None) -> str:
    name = (agent_name or "").strip()
    if name in CAPABILITY_AGENT_NAMES:
        return AGENT_KIND_CAPABILITY
    return AGENT_KIND_WORKFLOW


def is_workflow_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_WORKFLOW


def is_capability_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_CAPABILITY


@dataclass(frozen=True)
class AgentSeedSpec:
    name: str
    display_name: str
    description: str
    enabled: bool
    schedule: str
    execution_mode: str
    kind: str
    visible: bool
    lifecycle_status: str = "active"
    replaced_by: str = ""
    display_order: int = 0
    config: dict | None = None


AGENT_SEED_SPECS: tuple[AgentSeedSpec, ...] = (
    AgentSeedSpec(
        name="premarket_outlook",
        display_name="Premarket Outlook",
        description="Synthesizes yesterday's analysis and overnight information before the open to preview today's outlook",
        enabled=False,
        # App timezone is America/Vancouver: US session runs 06:30-13:00 local.
        schedule="45 5 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=10,
    ),
    AgentSeedSpec(
        name="intraday_monitor",
        display_name="Intraday Monitor",
        description="Real-time monitoring during trading hours, AI judges whether a signal is worth flagging",
        enabled=False,
        # Every 5 minutes across the US session (06:30-13:00 America/Vancouver); the agent
        # itself skips runs outside the exact trading window.
        schedule="*/5 6-13 * * 1-5",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=20,
        config={
            "event_only": True,
            "price_alert_threshold": 3.0,
            "volume_alert_ratio": 2.0,
            "stop_loss_warning": -5.0,
            "take_profit_warning": 10.0,
            "throttle_minutes": 30,
        },
    ),
    AgentSeedSpec(
        name="daily_report",
        display_name="Post-market Review",
        description="Generates a daily post-market report after close: market recap, per-stock review, and tomorrow's watch list",
        enabled=True,
        # 30 minutes after the US close (13:00 America/Vancouver).
        schedule="30 13 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=30,
    ),
    AgentSeedSpec(
        name="news_digest",
        display_name="News Digest (capability)",
        description="Internal capability: provides news fetching, dedup, and topic clustering; not independently scheduled",
        enabled=False,
        schedule="",
        execution_mode="batch",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="premarket_outlook,daily_report,intraday_monitor",
        display_order=110,
        config={
            "since_hours": 12,
            "fallback_since_hours": 24,
        },
    ),
    AgentSeedSpec(
        name="chart_analyst",
        display_name="Technical Analysis (capability)",
        description="Internal capability: on-demand chart image analysis triggered from the detail page; not independently scheduled",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="intraday_monitor,daily_report,premarket_outlook",
        display_order=120,
    ),
    AgentSeedSpec(
        name="tradingagents",
        display_name="TradingAgents Deep Analysis",
        description="Multi-agent investment decision framework (fundamentals/sentiment/news/technicals + bull-bear debate + risk control + PM). "
        "~3-5 min per run, ~$0.05 (deepseek-chat). Ships disabled with no schedule; this installation's own controls show its current state.",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=40,
        config={
            "analyst_types": ["market", "social", "news", "fundamentals"],
            "debate_rounds": 1,
            "monthly_budget_usd": 10.0,
            "over_budget_action": "reject",
            "cache_ttl_hours": 12,
            "output_language": "English",
            "deep_model": "",       # 留空走默认 AI Service 的 model;可填如 "claude-sonnet-4"
            "quick_model": "",      # 留空 = deep_model;可填便宜模型如 "deepseek-chat"
            "timeout_minutes": 15,
            "emit_paper_trading_signal": False,  # 是否把 BUY 决策写入 StrategySignalRun
                                                  # 驱动模拟盘自动开仓 (默认关,需用户主动启用)
        },
    ),
)

