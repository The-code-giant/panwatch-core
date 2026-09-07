"""Intraday Monitor Agent - monitors positions in real time and lets the AI judge whether an alert is warranted"""

import json
import logging
import re
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.kline_collector import KlineCollector
from src.core.analysis_history import get_latest_analysis, get_analysis
from src.core.context_builder import ContextBuilder
from src.core.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.core.suggestion_pool import save_suggestion
from src.core.signals import SignalPackBuilder
from src.core.signals.signal_pack import holders_summary_for, ownership_lines
from src.core.signals.structured_output import try_parse_action_json
from src.models.market import MarketCode, StockData, MARKETS
from src.models.market import market_display_name

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """Determine whether the given market is currently in a trading session."""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    """Display label from the market model (US / Canada / Crypto / Gold)."""
    return market_display_name(market)


# Standardized trading recommendations
SUGGESTION_TYPES = {
    "Open position": "buy",  # open a new position
    "Add": "add",  # increase an existing position
    "Reduce": "reduce",  # decrease the position
    "Close out": "sell",  # sell the entire position
    "Hold": "hold",  # maintain the current position
    "Watch": "watch",  # no action for now
}

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "intraday_monitor.txt"


class IntradayMonitorAgent(BaseAgent):
    """
    Intraday Monitor Agent

    Features:
    - Single-stock mode: analyzes one stock at a time and sends a separate notification for each
    - AI-driven judgment: stock data is sent to the AI, which decides whether it is worth an alert
    - Notification throttling: the same stock will not be notified again within a short window
    - Technical analysis: includes K-line data and technical indicators
    """

    name = "intraday_monitor"
    display_name = "Intraday Monitor"
    description = "Monitors positions in real time during trading hours; AI decides whether there is a signal worth flagging"

    def __init__(
        self,
        throttle_minutes: int = 30,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        event_only: bool = True,
        price_alert_threshold: float = 3.0,
        volume_alert_ratio: float = 2.0,
        stop_loss_warning: float = -5.0,
        take_profit_warning: float = 10.0,
    ):
        """
        Args:
            throttle_minutes: minimum interval between notifications for the same stock (minutes)
            bypass_throttle: whether to skip throttling (for testing)
            bypass_market_hours: whether to skip the trading-hours gate (manual analysis only)
            price_alert_threshold: |change %| above this threshold counts as a price anomaly (%)
            volume_alert_ratio: volume ratio above this threshold counts as a volume spike
            stop_loss_warning: unrealized loss beyond this threshold triggers a stop-loss warning (%)
            take_profit_warning: unrealized gain beyond this threshold triggers a take-profit alert (%)
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.event_only = event_only
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.stop_loss_warning = stop_loss_warning
        self.take_profit_warning = take_profit_warning

    async def collect(self, context: AgentContext) -> dict:
        """Collect real-time quotes + K-line data + historical analysis"""
        if not context.watchlist:
            logger.warning("Watchlist is empty; skipping intraday monitoring")
            return {"stocks": [], "stock_data": None}

        # SignalPack: unified structured input (quote/technical/position)
        stock_config = context.watchlist[0] if context.watchlist else None
        market = stock_config.market if stock_config else MarketCode.CN
        symbol = stock_config.symbol if stock_config else ""
        name = stock_config.name if stock_config else symbol

        # Gate on the trading hours of the stock's own market (not on any market being open globally)
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = f"{market_label(market)} is currently outside trading hours; execution skipped"
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_holders=True,
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=True,
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

        # Fetch historical analysis (to give the AI more context)
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the intraday analysis prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # Helper: safely fetch a numeric value, converting None to a default
        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "No stock data available"

        # Get position info across all accounts
        positions = context.portfolio.get_positions_for_stock(stock.symbol, stock.market)
        style_labels = {"short": "Short-term", "swing": "Swing", "long": "Long-term"}

        lines = []
        lines.append(f"## Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # Stock quote
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        change_amount = safe_num(stock.change_amount)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## Stock Quote")
        lines.append(f"- Stock: {stock.name} ({stock.symbol})")
        lines.append(f"- Current price: {current_price:.2f}")
        lines.append(f"- Change %: {change_pct:+.2f}%")
        lines.append(f"- Change amount: {change_amount:+.2f}")
        lines.append(f"- Open: {open_price:.2f}")
        lines.append(f"- High: {high_price:.2f}")
        lines.append(f"- Low: {low_price:.2f}")
        lines.append(f"- Prev close: {prev_close:.2f}")
        if volume > 0:
            lines.append(f"- Volume: {volume:.0f} lots")
        if turnover > 0:
            lines.append(f"- Turnover: {turnover / 10000:.0f} (x10k)")

        # System thresholds (help the AI make a more consistent “alert / no alert” judgment)
        # The price-anomaly threshold is adaptive relative to the stock's own volatility (ATR%); the fixed threshold acts as a floor/fallback.
        from src.core.intraday_event_gate import (
            DEFAULT_ATR_K,
            adaptive_price_threshold,
            is_abnormal_move,
        )

        kline_for_atr = data.get("kline_summary") or {}
        atr_pct = kline_for_atr.get("atr_pct")
        adaptive_threshold = adaptive_price_threshold(
            atr_pct, self.price_alert_threshold, DEFAULT_ATR_K
        )

        lines.append("\n## System Thresholds")
        if atr_pct is not None and atr_pct > 0:
            lines.append(
                f"- Price anomaly: |change %| >= max(fixed threshold {self.price_alert_threshold:.1f}%, "
                f"{DEFAULT_ATR_K:g}×ATR%={atr_pct:.2f}%)={adaptive_threshold:.2f}%"
                f" (adaptive to the stock's own volatility; the fixed threshold is the floor)"
            )
        else:
            lines.append(
                f"- Price anomaly: |change %| >= {self.price_alert_threshold:.1f}%"
                f" (ATR unavailable, falling back to the fixed threshold)"
            )
        lines.append(f"- Volume anomaly: volume ratio >= {self.volume_alert_ratio:.1f}")
        lines.append(f"- Stop-loss warning: unrealized loss <= {self.stop_loss_warning:.1f}%")
        lines.append(f"- Take-profit alert: unrealized gain >= {self.take_profit_warning:.1f}%")
        price_hit = (
            "Triggered"
            if is_abnormal_move(
                change_pct,
                atr_pct,
                k=DEFAULT_ATR_K,
                fixed_threshold=self.price_alert_threshold,
            )
            else "Not triggered"
        )
        lines.append(f"- Current change %: {change_pct:+.2f}% ({price_hit})")

        symbol_ctx = data.get("symbol_context") or {}
        quality = (symbol_ctx.get("data_quality") or {})
        if quality:
            lines.append(
                f"- Context quality: {quality.get('score', 0)} (real-time news: {quality.get('realtime_news_count', 0)}, extended news: {quality.get('extended_news_count', 0)}, historical news: {quality.get('history_news_count', 0)})"
            )

        layered_news = symbol_ctx.get("news") or {}
        realtime_news = layered_news.get("realtime") or []
        extended_news = layered_news.get("extended") or []
        history_news = layered_news.get("history") or []
        if realtime_news or extended_news or history_news:
            lines.append("\n## News & Event Context")
            chosen = realtime_news or extended_news or history_news
            for item in chosen[:3]:
                lines.append(
                    f"- [{item.get('time')}] {item.get('title')} ({item.get('source')})"
                )
            hist_topic = (layered_news.get("history_topic") or {}).get("summary")
            if hist_topic:
                lines.append(f"- Historical news theme: {hist_topic}")

        kline_history = symbol_ctx.get("kline_history") or {}
        if kline_history.get("available"):
            lines.append("\n## Historical K-line Background")
            lines.append(
                f"- Historical return: 5D {format_num(kline_history.get('ret_5d'), 1)}% / 20D {format_num(kline_history.get('ret_20d'), 1)}% / 60D {format_num(kline_history.get('ret_60d'), 1)}%"
            )
            if kline_history.get("volatility_20d") is not None:
                lines.append(
                    f"- Volatility (20D std dev): {format_num(kline_history.get('volatility_20d'), 2)}%"
                )
            if kline_history.get("breakout_state") and kline_history.get("breakout_state") != "none":
                lines.append(f"- Breakout state: {kline_history.get('breakout_state')}")

        # K-line and technical indicators
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## Technical Analysis")

            # Basic trend
            lines.append(f"- Trend: {kline.get('trend', 'N/A')}")
            lines.append(
                f"- Last 5 days: {kline.get('recent_5_up', 0)} up / {5 - kline.get('recent_5_up', 0)} down"
            )
            lines.append(
                f"- 5D change: {format_num(kline.get('change_5d'))}% | 20D change: {format_num(kline.get('change_20d'))}%"
            )

            # MACD
            macd_info = f"MACD: {kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f" ({kline.get('macd_cross_days')} days ago)"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get("rsi_status")
            rsi6 = kline.get("rsi6")
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6): {rsi6:.1f} ({rsi_status})")

            # KDJ
            kdj_status = kline.get("kdj_status")
            kdj_k, kdj_d, kdj_j = (
                kline.get("kdj_k"),
                kline.get("kdj_d"),
                kline.get("kdj_j"),
            )
            if kdj_status and kdj_k is not None:
                lines.append(
                    f"- KDJ: K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f} ({kdj_status})"
                )

            # Bollinger Bands
            boll_status = kline.get("boll_status")
            boll_upper, boll_lower = kline.get("boll_upper"), kline.get("boll_lower")
            if boll_status and boll_upper is not None:
                lines.append(
                    f"- Bollinger Bands: upper={format_num(boll_upper)} lower={format_num(boll_lower)} ({boll_status})"
                )

            # Volume
            volume_trend = kline.get("volume_trend")
            volume_ratio = kline.get("volume_ratio")
            if volume_trend:
                vol_info = f"Volume: {volume_trend}"
                if volume_ratio:
                    vol_info += f" (volume ratio={volume_ratio:.2f})"
                lines.append(f"- {vol_info}")
                if volume_ratio:
                    vol_hit = (
                        "Triggered" if volume_ratio >= self.volume_alert_ratio else "Not triggered"
                    )
                    lines.append(f"- Volume ratio threshold check: {vol_hit}")

            # Volatility (ATR): the stock's own volatility baseline, used to judge "anomaly vs. normal fluctuation"
            atr_val = kline.get("atr")
            atr_pct_val = kline.get("atr_pct")
            if atr_pct_val is not None:
                atr_line = f"Volatility: ATR={format_num(atr_val)} (ATR%={format_num(atr_pct_val)}%)"
                atr_line += (
                    f", today's change {change_pct:+.2f}% is "
                    + (
                        "above"
                        if abs(change_pct) >= adaptive_threshold
                        else "within"
                    )
                    + f" the adaptive anomaly threshold of {adaptive_threshold:.2f}%"
                )
                lines.append(f"- {atr_line}")

            # Moving averages
            lines.append(
                f"- MA5: {format_num(kline.get('ma5'))} | MA10: {format_num(kline.get('ma10'))} | MA20: {format_num(kline.get('ma20'))} | MA60: {format_num(kline.get('ma60'))}"
            )

        if kline and not kline.get("error"):
            # Multi-level support/resistance
            support_m, resistance_m = kline.get("support_m"), kline.get("resistance_m")
            if support_m and resistance_m:
                lines.append(
                    f"- Medium-term support: {format_num(support_m)} | Medium-term resistance: {format_num(resistance_m)}"
                )

            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- Short-term support: {format_num(support_s)} | Short-term resistance: {format_num(resistance_s)}"
                )

            # K-line pattern
            kline_pattern = kline.get("kline_pattern")
            if kline_pattern:
                lines.append(f"- K-line pattern: {kline_pattern}")

            # Amplitude
            amplitude = kline.get("amplitude")
            amplitude_avg5 = kline.get("amplitude_avg5")
            if amplitude is not None:
                amp_info = f"Today's amplitude: {amplitude:.2f}%"
                if amplitude_avg5 is not None:
                    amp_info += f" (5D avg: {amplitude_avg5:.2f}%)"
                lines.append(f"- {amp_info}")

        # Ownership (insiders / institutions) when the pack carries a holder summary
        pack = data.get("signal_pack")
        owner_lines = ownership_lines(holders_summary_for(pack))
        if owner_lines:
            lines.append("\n## Ownership")
            lines.extend(owner_lines)

        # Account funds
        lines.append(f"\n## Account Funds")
        lines.append(f"- Total available funds: {context.portfolio.total_available_funds:.0f}")
        for acc in context.portfolio.accounts:
            lines.append(f"  - {acc.name}: {acc.available_funds:.0f}")
        constraints = symbol_ctx.get("constraints") or {}
        if constraints:
            lines.append(
                f"- Single-stock position ratio: {safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}% ({constraints.get('risk_budget_hint', 'normal')})"
            )
        memory = symbol_ctx.get("memory") or {}
        if memory:
            lines.append(
                f"- Historical context memory: avg quality score over the last {memory.get('window_days', 30)} days is {safe_num(memory.get('avg_quality_score'), 0):.1f}, trend {memory.get('quality_trend', 'flat')}"
            )
            if memory.get("latest_history_topic"):
                lines.append(f"- Historical memory theme: {memory.get('latest_history_topic')}")

        # Position info across accounts
        if positions:
            lines.append(f"\n## Positions ({len(positions)} account(s))")
            for i, pos in enumerate(positions, 1):
                cost_price = safe_num(pos.cost_price, 1)
                pnl_pct = (
                    (current_price - cost_price) / cost_price * 100
                    if cost_price > 0
                    else 0
                )
                style_label = style_labels.get(pos.trading_style, "Swing")
                market_value = current_price * pos.quantity
                # Find the available funds for the matching account
                acc_funds = 0
                for acc in context.portfolio.accounts:
                    if acc.id == pos.account_id:
                        acc_funds = acc.available_funds
                        break

                lines.append(f"\n### Position {i}: {pos.account_name}")
                lines.append(f"- Trading style: {style_label}")
                lines.append(f"- Cost price: {cost_price:.2f}")
                lines.append(f"- Quantity: {pos.quantity} shares")
                lines.append(f"- Position value: {market_value:.0f}")
                pnl_note = ""
                if pnl_pct <= self.stop_loss_warning:
                    pnl_note = " (stop-loss warning triggered)"
                elif pnl_pct >= self.take_profit_warning:
                    pnl_note = " (take-profit alert triggered)"
                lines.append(f"- Unrealized P&L: {pnl_pct:+.1f}%{pnl_note}")
                lines.append(f"- Account available: {acc_funds:.0f}")
        else:
            lines.append("\n## No Position (Watch Only)")
            lines.append(f"- Ample available funds; consider opening a position")

        # Historical analysis context (helps the AI make a better judgment)
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")

        if daily_analysis or premarket_analysis:
            lines.append("\n## Historical Analysis Reference")

            if daily_analysis:
                # Trim to the part relevant to the current stock (max 300 chars)
                content = (
                    daily_analysis[:300] + "..."
                    if len(daily_analysis) > 300
                    else daily_analysis
                )
                lines.append(f"\n### Yesterday's After-Hours Analysis Summary")
                lines.append(content)

            if premarket_analysis:
                content = (
                    premarket_analysis[:300] + "..."
                    if len(premarket_analysis) > 300
                    else premarket_analysis
                )
                lines.append(f"\n### Today's Pre-Market Analysis Summary")
                lines.append(content)

        lines.append("\nBased on the technical analysis, capital flow, and historical analysis, provide a clear trading recommendation.")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestion(self, content: str) -> dict:
        """
        Parse the trading recommendation from the AI response

        Returns:
            {
                "action": "hold",  # buy/add/reduce/sell/hold/watch
                "action_label": "Hold",
                "signal": "...",
                "reason": "...",
                "should_alert": True
            }
        """
        result = {
            "action": "watch",
            "action_label": "Watch",
            "signal": "",
            "reason": "",
            "should_alert": False,
        }

        # 1) Prefer JSON output (structured mode)
        obj = try_parse_action_json(content) or self._try_parse_loose_json(content)
        if obj:
            action = (obj.get("action") or "watch").strip()
            result["action"] = action
            result["action_label"] = (
                obj.get("action_label") or result["action_label"]
            ).strip()[:20]
            result["signal"] = (obj.get("signal") or "").strip()[:60]
            result["reason"] = (obj.get("reason") or "").strip()[:160]
            result["should_alert"] = action in {
                "buy",
                "add",
                "reduce",
                "sell",
                "alert",
                "avoid",
            }
            result["triggers"] = (
                obj.get("triggers") if isinstance(obj.get("triggers"), list) else []
            )
            result["invalidations"] = (
                obj.get("invalidations")
                if isinstance(obj.get("invalidations"), list)
                else []
            )
            result["risks"] = (
                obj.get("risks") if isinstance(obj.get("risks"), list) else []
            )
            return result

        # Check whether no alert is needed
        if "[No alert needed]" in content or "[无需提醒]" in content:
            result["should_alert"] = False
            result["action"] = "hold"
            result["action_label"] = "Hold"
            return result

        # Extract recommendation type (search full text)
        for label, action in SUGGESTION_TYPES.items():
            if label in content:
                result["action"] = action
                result["action_label"] = label
                break

        # Extract signal (supports multiple formats)
        signal_patterns = [
            r"「Signal」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*Signal\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Signal\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in signal_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["signal"] = match.group(1).strip()[:50]
                break

        # Extract recommendation text (supports multiple formats)
        suggest_patterns = [
            r"「Recommendation」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*Recommendation\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Recommendation\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in suggest_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                suggest_text = match.group(1).strip()
                # Extract the action type from the recommendation text
                for label, action in SUGGESTION_TYPES.items():
                    if label in suggest_text:
                        result["action"] = action
                        result["action_label"] = label
                        break
                # If signal is empty, use the recommendation text as the signal
                if not result["signal"]:
                    result["signal"] = suggest_text[:50]
                break

        # Extract reason (supports multiple formats)
        reason_patterns = [
            r"「Reason」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*Reason\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"Reason\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["reason"] = match.group(1).strip()[:100]
                break

        # If no signal or reason was extracted, fall back to the leading part of the content
        if not result["signal"] and not result["reason"]:
            # Strip markdown formatting and take the first 100 characters
            clean_content = re.sub(r"\*\*|##|#", "", content).strip()
            # Skip the "no alert needed" case
            if not clean_content.startswith("[No alert needed]") and not clean_content.startswith("[无需提醒]"):
                result["reason"] = clean_content[:100]

        # Final should_alert determination: only alert on a clear Open position/Add/Reduce/Close out
        result["should_alert"] = result["action"] in {"buy", "add", "reduce", "sell"}
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """Loosely parse JSON output, as a fallback to tolerate malformed model responses."""
        raw = (text or "").strip()
        if not raw:
            return None

        # Handle a leading "json" line
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()

        # Strip a fenced code block
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()

        # Try direct parsing first; on failure, extract the first JSON object fragment
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if not isinstance(obj, dict):
            return None

        # If none of the key fields are present, do not treat this as a suggestion JSON
        keys = {"action", "action_label", "signal", "reason", "triggers", "invalidations", "risks"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, raw_content: str
    ) -> str:
        """When the model returns JSON, build human-readable notification content."""
        action_label = suggestion.get("action_label") or "Watch"
        signal = suggestion.get("signal") or "No clear new signal"
        reason = suggestion.get("reason") or "Please judge carefully in light of price action and your risk policy."
        triggers = (
            suggestion.get("triggers")
            if isinstance(suggestion.get("triggers"), list)
            else []
        )
        invalidations = (
            suggestion.get("invalidations")
            if isinstance(suggestion.get("invalidations"), list)
            else []
        )
        risks = (
            suggestion.get("risks") if isinstance(suggestion.get("risks"), list) else []
        )
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        lines = [
            f"{stock.name} ({stock.symbol})",
            f"Price: {price}  Change: {chg}",
            f"Recommendation: {action_label}",
            f"Signal: {signal}",
            f"Reason: {reason}",
        ]
        if triggers:
            lines.append("Trigger conditions:")
            lines.extend([f"- {str(x)}" for x in triggers[:3]])
        if invalidations:
            lines.append("Invalidation conditions:")
            lines.extend([f"- {str(x)}" for x in invalidations[:3]])
        if risks:
            lines.append("Risk notes:")
            lines.extend([f"- {str(x)}" for x in risks[:3]])
        # If this wasn't pure JSON, attach a brief excerpt of the raw text for reference
        if not (try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content)):
            brief = re.sub(r"\s+", " ", (raw_content or "").strip())[:200]
            if brief:
                lines.append(f"Note: {brief}")
        return "\n".join(lines)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI analysis and determination of whether an alert is needed"""
        # Skip outside trading hours
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"[{self.display_name}] Skipped",
                content=data.get("skip_reason", "Execution skipped"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")

        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"[{self.display_name}] No data",
                content="No stock data was retrieved",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)

        # Log the full prompt for debugging
        logger.info(f"=== Prompt for {stock.symbol} ===\n{user_content}")

        raw_content = await context.ai_client.chat(system_prompt, user_content)

        # Log the AI response
        logger.info(f"=== AI Response for {stock.symbol} ===\n{raw_content}")

        # Parse the trading recommendation
        suggestion = self._parse_suggestion(raw_content)
        content = raw_content
        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        quality_score = (
            (data.get("symbol_context") or {}).get("data_quality", {}).get("score")
        )
        # For JSON/JSON-like output, convert it to human-readable notification text so raw JSON is never pushed directly to a channel
        if try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content):
            content = self._format_human_readable_content(stock, suggestion, raw_content)

        # Save to the suggestion pool (including the prompt context)
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=6,  # Intraday suggestions are valid for 6 hours
            prompt_context=user_content,  # Save the prompt context
            ai_response=raw_content,  # Save the raw AI response
            stock_market=stock.market.value,
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "kline_meta": {
                    "computed_at": (data.get("kline_summary") or {}).get("computed_at"),
                    "asof": (data.get("kline_summary") or {}).get("asof"),
                },
                "event_gate": data.get("event_gate"),
                "analysis_date": analysis_date,
                "context_quality_score": quality_score,
                "plan": {
                    "triggers": suggestion.get("triggers")
                    if isinstance(suggestion, dict)
                    else [],
                    "invalidations": suggestion.get("invalidations")
                    if isinstance(suggestion, dict)
                    else [],
                    "risks": suggestion.get("risks")
                    if isinstance(suggestion, dict)
                    else [],
                },
            },
        )
        for horizon in (1, 5):
            save_agent_prediction_outcome(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                stock_market=stock.market.value,
                prediction_date=analysis_date,
                horizon_days=horizon,
                action=suggestion.get("action") or "watch",
                action_label=suggestion.get("action_label") or "Watch",
                confidence=(float(quality_score) / 100.0)
                if quality_score is not None
                else None,
                trigger_price=getattr(stock, "current_price", None),
                meta={
                    "source": "intraday_monitor",
                    "reason": suggestion.get("reason", ""),
                    "signal": suggestion.get("signal", ""),
                },
            )

        save_agent_context_run(
            agent_name=self.name,
            stock_symbol=stock.symbol,
            analysis_date=analysis_date,
            context_payload={
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
            },
            quality={"score": quality_score or 0},
        )

        # Build the title
        title = f"[{self.display_name}] {stock.name} {stock.change_pct:+.2f}%"

        # Append AI model info
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        # Sharp-move linkage: asynchronously trigger deep TradingAgents analysis once the threshold is met (disabled by default)
        try:
            from src.agents.tradingagents.auto_trigger import try_auto_trigger
            try_auto_trigger(stock, source_agent=self.name)
        except Exception:
            logger.exception("TA linkage trigger failed; continuing to return the intraday result")

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Check whether a notification is needed"""
        # Skipped results are never notified
        if result.raw_data.get("skipped"):
            return False

        # AI determined no alert is needed
        if not result.raw_data.get("should_alert", True):
            logger.info(
                f"AI determined no alert is needed: {result.raw_data.get('stock', {}).get('symbol')}"
            )
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data:
            return False

        symbol = stock_data.get("symbol")
        if not symbol:
            return False

        # Check throttling (can be skipped in test mode)
        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(
                    f"Notification throttled: {symbol} was already notified within the last {self.throttle_minutes} minutes"
                )
                return False
        else:
            logger.info(f"Skipping throttle check (test mode): {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """Check whether a notification can be sent (not throttled)"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            if not record:
                return True

            # Compare using UTC to avoid issues from container/deployment timezone changes
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """Update the throttle record"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                # Check whether it is a new day
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )

            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        Single-stock mode execution: analyzes only the specified stock

        Used for real-time monitoring scenarios, where each stock is analyzed and notified independently
        """
        # Filter down to only the specified stock
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("stock_data"):
                return None

            # The event gate is only a contextual signal and does not block AI analysis.
            # Product strategy: suggestions keep refreshing continuously; noise is controlled downstream by should_alert + throttle.
            if self.event_only:
                try:
                    from src.core.intraday_event_gate import check_and_update

                    stock = data.get("stock_data")
                    kline_summary = data.get("kline_summary")
                    decision = check_and_update(
                        symbol=stock_symbol,
                        change_pct=getattr(stock, "change_pct", None),
                        volume_ratio=(kline_summary or {}).get("volume_ratio"),
                        kline_summary=kline_summary,
                        price_threshold=self.price_alert_threshold,
                        volume_threshold=self.volume_alert_ratio,
                    )
                    data["event_gate"] = {
                        "reasons": decision.reasons,
                        "should_analyze": bool(decision.should_analyze),
                    }
                except Exception as e:
                    logger.debug(f"Event gate error, continuing analysis: {e}")

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(
                        f"Agent [{self.display_name}] notification sent: {stock_symbol}"
                    )
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
                else:
                    notify_error = notify_result.get("error") or "Unknown error"
                    result.raw_data["notify_error"] = notify_error
                    logger.error(
                        f"Agent [{self.display_name}] notification failed: {stock_symbol} - {notify_error}"
                    )
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
