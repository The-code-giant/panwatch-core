"""Technical Analysis Agent - Multimodal candlestick chart analysis"""

import logging
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.chart_renderer import ChartRenderer, ChartImage as ChartScreenshot
from src.core.signals import SignalPackBuilder

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "chart_analyst.txt"


class ChartAnalystAgent(BaseAgent):
    """
    Technical Analysis Agent

    Renders candlestick charts locally (matplotlib) and uses multimodal AI to produce a technical analysis report.
    Requires a Vision-capable AI model (e.g. GPT-4V, GLM-4V, etc.).
    """

    name = "chart_analyst"
    display_name = "Technical Analysis"
    description = "Renders candlestick charts and uses multimodal AI to perform technical analysis"

    def __init__(self, period: str = "daily"):
        """
        Args:
            period: Candlestick period (daily/weekly/monthly)
        """
        self.period = period
        self._renderer: ChartRenderer | None = None

    async def collect(self, context: AgentContext) -> dict:
        """Render candlestick charts for watchlist stocks"""
        if not context.watchlist:
            logger.warning("Watchlist is empty, skipping chart rendering")
            return {"screenshots": [], "watchlist": []}

        # Prepare stock list
        stocks = [
            {
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market.value,
            }
            for stock in context.watchlist
        ]

        # Render charts (provider is always the in-process "panwatch" renderer)
        self._renderer = ChartRenderer()
        try:
            screenshots = await self._renderer.capture_batch(
                stocks, period=self.period, provider="panwatch"
            )

            # Structured signals (quote/technical/position), used to enrich the prompt (failure doesn't affect charts)
            packs = {}
            try:
                builder = SignalPackBuilder()
                sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
                packs = await builder.build_for_symbols(
                    symbols=sym_list,
                    include_news=False,
                    news_hours=12,
                    portfolio=context.portfolio,
                    include_technical=True,
                    include_holders=False,
                    include_events=True,
                    events_days=3,
                )
            except Exception as e:
                logger.warning(f"SignalPack fetch failed (chart_analyst continues execution): {e}")

            # Clean up old chart images
            self._renderer.cleanup_old_screenshots(max_age_hours=24)

            return {
                "screenshots": screenshots,
                "watchlist": context.watchlist,
                "signal_packs": packs,
                "period": self.period,
                "timestamp": datetime.now().isoformat(),
            }
        finally:
            await self._renderer.close()
            self._renderer = None

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the Technical Analysis prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        lines.append(f"## Analysis Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## Candlestick Period: {self._period_label(data.get('period', 'daily'))}\n")

        # Stock list (with position info)
        lines.append("## Stocks to Analyze")
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        packs = data.get("signal_packs", {}) or {}

        if screenshots:
            for i, shot in enumerate(screenshots, 1):
                pack = packs.get(shot.symbol)
                position = context.portfolio.get_aggregated_position(shot.symbol)
                if position:
                    lines.append(
                        f"{i}. {shot.name}({shot.symbol}) - see chart {i}"
                        f" | Position {position['total_quantity']} shares, cost {position['avg_cost']:.2f}"
                    )
                else:
                    lines.append(f"{i}. {shot.name}({shot.symbol}) - see chart {i} | No position")

                # Add structured technical summary (makes multimodal output more stable)
                tech = (pack.technical if pack else None) or {}
                quote = pack.quote if pack else None
                brief_parts = []
                if quote:
                    try:
                        brief_parts.append(
                            f"Price {quote.current_price:.2f} Change {quote.change_pct:+.2f}%"
                        )
                    except Exception:
                        pass
                if tech and not tech.get("error"):
                    if tech.get("trend"):
                        brief_parts.append(f"Trend {tech.get('trend')}")
                    if tech.get("macd_status"):
                        brief_parts.append(f"MACD {tech.get('macd_status')}")
                    if tech.get("rsi_status") and tech.get("rsi6") is not None:
                        try:
                            brief_parts.append(
                                f"RSI {float(tech.get('rsi6')):.1f}({tech.get('rsi_status')})"
                            )
                        except Exception:
                            pass
                    if (
                        tech.get("support_m") is not None
                        and tech.get("resistance_m") is not None
                    ):
                        try:
                            brief_parts.append(
                                f"Mid-term support {float(tech.get('support_m')):.2f}/resistance {float(tech.get('resistance_m')):.2f}"
                            )
                        except Exception:
                            pass
                if brief_parts:
                    lines.append(f"   - Signal: {'; '.join(brief_parts)}")
        else:
            lines.append("- No charts available")

        # Account funds overview
        if context.portfolio.accounts:
            lines.append("\n## Funds Overview")
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(f"- Total available funds: {total_funds:.0f} yuan")
                lines.append(f"- Total position cost: {total_cost:.0f} yuan")

        lines.append(
            "\nPlease perform a technical analysis based on the candlestick charts above, "
            "and give action recommendations considering the current positions."
        )

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _period_label(self, period: str) -> str:
        """Period label"""
        return {
            "daily": "Daily",
            "weekly": "Weekly",
            "monthly": "Monthly",
        }.get(period, period)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """
        Override the analyze method to support multimodal input

        Passes the rendered charts as images to the AI
        """
        system_prompt, user_content = self.build_prompt(data, context)

        # Collect image paths
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        image_paths = [shot.filepath for shot in screenshots if shot.exists]

        if not image_paths:
            logger.warning("No chart images available, skipping analysis")
            content = "Failed to render candlestick charts (not enough price history or the data source is unavailable). Please try again later."
        else:
            # Call the multimodal AI
            logger.info(f"Using {len(image_paths)} chart image(s) for multimodal analysis")
            content = await context.ai_client.chat(
                system_prompt,
                user_content,
                images=image_paths,
            )

        # Build the title
        stock_names = ", ".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" and {len(context.watchlist)} more"
        title = f"[{self.display_name}] {stock_names}"

        # Append AI model info
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
            images=image_paths,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Notify when there are chart images and content"""
        screenshots = result.raw_data.get("screenshots", [])
        return len(screenshots) > 0 and len(result.content) > 50

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        Single-stock mode: analyze only the specified stock

        Used for per-stock analysis scenarios, where each stock is independently
        charted, analyzed, and notified
        """
        # Filter to keep only the specified stock
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("screenshots"):
                return None

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
