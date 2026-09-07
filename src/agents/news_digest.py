"""News Digest Agent - News summary for watchlist stocks"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.news_collector import NewsCollector, NewsItem
from src.core.analysis_history import save_analysis
from src.core.cn_symbol import get_cn_prefix
from src.core.suggestion_pool import save_suggestion
from src.core.signals import SignalPackBuilder
from src.core.signals.signal_pack import holders_summary_for
from src.core.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.core.source_labels import label_for
from src.models.market import EQUITY_MARKETS, MarketCode

logger = logging.getLogger(__name__)

# Market headlines with importance below this are not surfaced as "important market news".
_FLASH_NEWS_MIN_IMPORTANCE = 2
_FLASH_NEWS_LIMIT = 20


def get_market_data():
    """Lazy import so an uninstalled package / circular import never breaks module loading."""
    from src.core.marketdata_client import get_market_data as _g

    return _g()


def fetch_market_headlines(markets: tuple[str, ...] | list[str]) -> list[NewsItem]:
    """Market-level headlines (publisher RSS) for each enabled equity market as ``NewsItem`` rows.

    Rows keep the feed key as ``source`` and carry no symbols; only ``importance >= 2`` is kept.
    A feed failure for one market is logged and skipped, never raised.
    """
    out: list[NewsItem] = []
    for m in markets:
        try:
            rows = get_market_data().flash_news(market=m, limit=_FLASH_NEWS_LIMIT)
        except Exception as e:
            logger.warning(f"Market headlines fetch failed ({m}): {e}")
            continue
        for fn in rows or []:
            try:
                if int(getattr(fn, "importance", 0) or 0) < _FLASH_NEWS_MIN_IMPORTANCE:
                    continue
                out.append(
                    NewsItem(
                        source=getattr(fn, "source", "") or "",
                        external_id=getattr(fn, "external_id", "") or "",
                        title=getattr(fn, "title", "") or "",
                        content=getattr(fn, "content", "") or "",
                        publish_time=fn.publish_time,
                        symbols=[],
                        importance=int(getattr(fn, "importance", 0) or 0),
                        url=getattr(fn, "url", "") or "",
                    )
                )
            except Exception as e:
                logger.debug(f"Skipping malformed headline ({m}): {e}")
                continue
    return out

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "news_digest.txt"

# News digest recommendation type mapping (news-driven)
NEWS_ACTION_MAP = {
    "Set alert": {"action": "alert", "label": "Set alert"},
    "Watch": {"action": "watch", "label": "Watch"},
    "Continue holding": {"action": "hold", "label": "Continue holding"},
    "Consider reducing": {"action": "reduce", "label": "Consider reducing"},
    "Avoid for now": {"action": "avoid", "label": "Avoid for now"},
}


class NewsDigestAgent(BaseAgent):
    """News Digest Agent"""

    name = "news_digest"
    display_name = "News Digest"
    description = "Periodically collects news related to your positions and pushes a summary"

    def __init__(self, since_hours: int = 12, fallback_since_hours: int = 24):
        """
        Args:
            since_hours: Fetch news from the last N hours
            fallback_since_hours: When there is no news in the last N hours, automatically
                fall back to a longer time window (avoid an "empty run")
        """
        self.since_hours = since_hours
        self.fallback_since_hours = fallback_since_hours

    def _dedupe_with_db(self, items: list[NewsItem]) -> list[NewsItem]:
        """Deduplicate using the NewsCache table (works across processes/restarts too), to avoid pushing the same news item repeatedly."""
        if not items:
            return []

        from src.web.database import SessionLocal
        from src.web.models import NewsCache

        db = SessionLocal()
        try:
            by_source: dict[str, list[str]] = {}
            for it in items:
                if not it.external_id:
                    continue
                by_source.setdefault(it.source, []).append(it.external_id)

            existing: set[tuple[str, str]] = set()
            for source, ids in by_source.items():
                if not ids:
                    continue
                rows = (
                    db.query(NewsCache.external_id)
                    .filter(NewsCache.source == source, NewsCache.external_id.in_(ids))
                    .all()
                )
                existing.update((source, r[0]) for r in rows)

            new_items: list[NewsItem] = []
            for it in items:
                if it.external_id and (it.source, it.external_id) in existing:
                    continue

                new_items.append(it)
                if it.external_id:
                    # Write to cache table (content moderately truncated to avoid bloat)
                    try:
                        db.add(
                            NewsCache(
                                source=it.source,
                                external_id=it.external_id,
                                title=it.title or "",
                                content=(it.content or "")[:2000],
                                publish_time=it.publish_time,
                                symbols=it.symbols or [],
                                importance=it.importance or 0,
                            )
                        )
                    except Exception:
                        # A single write failure doesn't affect this return
                        pass

            db.commit()
            return new_items
        except Exception as e:
            logger.warning(f"NewsCache deduplication failed, falling back to no deduplication: {e}")
            db.rollback()
            return items
        finally:
            db.close()

    async def collect(self, context: AgentContext) -> dict:
        """Collect news (watchlist-related + important market news)"""
        symbols = [stock.symbol for stock in context.watchlist]

        if not symbols:
            logger.warning("Watchlist is empty, skipping news collection")
            return {"news": [], "related_news": [], "watchlist": []}

        collector = NewsCollector.from_database()
        since_hours_used = self.since_hours
        news_list = await collector.fetch_all(
            symbols=symbols,
            since_hours=self.since_hours,
        )
        if (
            not news_list
            and self.fallback_since_hours
            and self.fallback_since_hours > self.since_hours
        ):
            logger.info(
                f"No news in the last {self.since_hours} hours, falling back to the last {self.fallback_since_hours} hours"
            )
            since_hours_used = self.fallback_since_hours
            news_list = await collector.fetch_all(
                symbols=symbols,
                since_hours=self.fallback_since_hours,
            )

        # Cross-run deduplication: keep only "new news", to avoid the agent appearing to repeat the same content
        news_list = self._dedupe_with_db(news_list)

        # Categorize: watchlist-related + important market news
        related_news = self._filter_related_news(news_list, symbols)
        important_news = [
            n for n in news_list if n.importance >= 2 and n not in related_news
        ]

        # Market headlines (publisher RSS) for each enabled equity market, deduplicated across runs
        try:
            headlines = await asyncio.to_thread(fetch_market_headlines, EQUITY_MARKETS)
            headlines = self._dedupe_with_db(headlines)
            seen_keys = {(n.source, n.external_id) for n in important_news}
            for h in headlines:
                key = (h.source, h.external_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                important_news.append(h)
        except Exception as e:
            logger.warning(f"Market headlines skipped (news_digest continues execution): {e}")

        # Structured signals: supplement with quote/technical/ownership/position data to improve the stability of the "suggestion summary"
        packs = {}
        try:
            builder = SignalPackBuilder()
            sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
            packs = await builder.build_for_symbols(
                symbols=sym_list,
                include_news=False,
                news_hours=self.since_hours,
                portfolio=context.portfolio,
                include_technical=True,
                include_holders=True,
                include_events=True,
                events_days=3,
            )
        except Exception as e:
            logger.warning(f"SignalPack fetch failed (news_digest continues execution): {e}")

        return {
            "news": news_list,  # All news
            "related_news": related_news,  # Watchlist-related
            "important_news": important_news,  # Important market news
            "watchlist": context.watchlist,
            "signal_packs": packs,
            "timestamp": datetime.now().isoformat(),
            "since_hours_used": since_hours_used,
        }

    def _filter_related_news(
        self, news_list: list[NewsItem], symbols: list[str]
    ) -> list[NewsItem]:
        """Filter news related to watchlist stocks"""
        related = []
        for news in news_list:
            # News already tagged with stock
            if news.symbols and any(s in symbols for s in news.symbols):
                related.append(news)
                continue
            # Check if title/content contains the stock symbol
            text = news.title + news.content
            if any(s in text for s in symbols):
                related.append(news)

        return related

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Build the News Digest prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        since_hours_used = data.get("since_hours_used") or self.since_hours
        lines.append(f"## Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## Time window: last {since_hours_used} hours\n")

        # Watchlist (marking positions)
        lines.append("## Watchlist")
        watchlist_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        for stock in context.watchlist:
            pack = packs.get(stock.symbol)
            position = context.portfolio.get_aggregated_position(stock.symbol)

            extra_parts = []
            if pack and pack.quote:
                try:
                    extra_parts.append(
                        f"Price {pack.quote.current_price:.2f}({pack.quote.change_pct:+.2f}%)"
                    )
                except Exception:
                    pass
            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                if tech.get("trend"):
                    extra_parts.append(f"Trend {tech.get('trend')}")
                if tech.get("macd_status"):
                    extra_parts.append(f"MACD {tech.get('macd_status')}")
            holders = holders_summary_for(pack)
            if holders:
                extra_parts.append(self._holders_brief(holders))

            extra = (" | " + " ".join(extra_parts)) if extra_parts else ""
            if position:
                lines.append(
                    f"- {stock.name}({stock.symbol}) [Position {position['total_quantity']} shares]{extra}"
                )
            else:
                lines.append(f"- {stock.name}({stock.symbol}){extra}")

        # Watchlist-related news
        related_news: list[NewsItem] = data.get("related_news", [])
        lines.append(f"\n## Watchlist-Related News ({len(related_news)} items)")
        if related_news:
            for news in related_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- No watchlist-related news at this time")

        # Important market news
        important_news: list[NewsItem] = data.get("important_news", [])
        lines.append(f"\n## Important Market News ({len(important_news)} items)")
        if important_news:
            for news in important_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- No important market news at this time")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    @staticmethod
    def _holders_brief(holders: dict) -> str:
        """One-phrase ownership summary for the watchlist line, e.g. ``Ownership insiders 1.2% / institutions 61.4%, insider net -12,000 sh (6m)``."""

        def pct(v) -> str:
            try:
                return f"{float(v):.1f}%" if v is not None else "n/a"
            except (TypeError, ValueError):
                return "n/a"

        try:
            net = float(holders.get("insider_net_shares_6m") or 0)
        except (TypeError, ValueError):
            net = 0.0
        return (
            f"Ownership insiders {pct(holders.get('insiders_pct'))} / "
            f"institutions {pct(holders.get('institutions_pct'))}, insider net {net:+,.0f} sh (6m)"
        )

    def _format_news_item(
        self, lines: list[str], news: NewsItem, watchlist_map: dict
    ) -> None:
        """Format a single news item"""
        importance_label = ["", "[Minor]", "[Important]", "[Major]"][min(news.importance, 3)]
        time_str = news.publish_time.strftime("%H:%M")
        source_label = label_for(news.source, getattr(news, "publisher", "") or "")

        # Related stock names
        stock_names = []
        for symbol in news.symbols:
            if symbol in watchlist_map:
                stock_names.append(watchlist_map[symbol].name)
        stock_info = f"[{','.join(stock_names)}] " if stock_names else ""

        link = f" ([Source]({news.url}))" if news.url else ""
        lines.append(
            f"- {importance_label} [{source_label} {time_str}] {stock_info}{news.title}{link}"
        )
        if news.content:
            content_brief = news.content[:200] + (
                "..." if len(news.content) > 200 else ""
            )
            lines.append(f"  > {content_brief}")

    def _parse_suggestions(self, content: str, watchlist: list) -> dict[str, dict]:
        """
        Parse per-stock suggestions from the AI response
        Returns: {symbol: {action, action_label, reason, should_alert}}
        """
        suggestions: dict[str, dict] = {}
        if not content or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        name_map: dict[str, str] = {}

        for s in watchlist:
            sym = (getattr(s, "symbol", "") or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym

            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym  # Support stripping leading zeros (e.g. 00700 -> 700)
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym

            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = get_cn_prefix(sym, upper=True)
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym

            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(NEWS_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) First, try matching a symbol inside 「...」/【...】
            m = re.search(
                r"[「【\[]\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*[」】\]]",
                line,
            )
            sym_raw = m.group("sym") if m else ""

            # 2) Then try matching a symbol inside parentheses (e.g. Tencent Holdings(00700))
            if not sym_raw:
                m = re.search(
                    r"\(\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*\)", line
                )
                sym_raw = m.group("sym") if m else ""

            # 3) Then try matching a symbol at the start of the line
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) Fallback: substring containment
            if not sym_raw:
                for k in sorted(symbol_map.keys(), key=len, reverse=True):
                    if k and k in line.upper():
                        sym_raw = k
                        break

            # 5) Fallback: name matching
            if not sym_raw:
                for name, sym in name_map.items():
                    if name and name in line:
                        sym_raw = sym
                        break

            if not sym_raw:
                continue

            sym_key = sym_raw.strip()
            canonical = symbol_map.get(sym_key.upper()) or symbol_map.get(sym_key)
            if not canonical and sym_key.isdigit():
                canonical = symbol_map.get(sym_key)

            if not canonical or canonical not in symbol_set:
                continue

            # Extract reason: take the text following the "suggestion type"
            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[：:：\\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = NEWS_ACTION_MAP.get(
                action_text, {"action": "watch", "label": "Watch"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:140],
                "should_alert": action_info["action"] in ["alert", "reduce", "sell"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (getattr(s, "symbol", "") or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym
            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = get_cn_prefix(sym, upper=True)
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym

        for it in items:
            if not isinstance(it, dict):
                continue
            sym_raw = (it.get("symbol") or "").strip()
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "watch").strip()
            action_label = (it.get("action_label") or "Watch").strip()
            reason = (it.get("reason") or "").strip()
            signal = (it.get("signal") or "").strip()
            suggestions[canonical] = {
                "action": action,
                "action_label": action_label,
                "reason": reason[:160],
                "signal": signal[:60],
                "triggers": it.get("triggers")
                if isinstance(it.get("triggers"), list)
                else [],
                "invalidations": it.get("invalidations")
                if isinstance(it.get("invalidations"), list)
                else [],
                "risks": it.get("risks") if isinstance(it.get("risks"), list) else [],
                "should_alert": action in ["alert", "reduce", "sell"],
            }
        return suggestions

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Notify when there is watchlist-related news or important market news"""
        related_news = result.raw_data.get("related_news", [])
        important_news = result.raw_data.get("important_news", [])

        # Has watchlist-related news
        if related_news:
            return True
        # Has important market news
        if important_news:
            return True
        return False

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """Override analyze: persist to history so the "News Digest" output can be viewed in the UI."""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        if context.model_label:
            idx = content.rfind(TAG_START)
            if idx >= 0:
                content = (
                    content[:idx].rstrip()
                    + f"\n\n---\nAI: {context.model_label}\n\n"
                    + content[idx:]
                )
            else:
                content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        structured = try_extract_tagged_json(content) or {}
        display_content = strip_tagged_json(content)

        stock_items = [
            f"{(s.name or s.symbol).strip()}({s.symbol})"
            for s in context.watchlist[:5]
        ]
        stock_names = ", ".join(stock_items) if stock_items else "No stocks"
        if len(context.watchlist) > 5:
            stock_names += f" and {len(context.watchlist)} more"
        title = f"[{self.display_name}] {stock_names}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # Parse per-stock suggestions and write to the suggestion pool
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
            suggestions = self._parse_suggestions(result.content, context.watchlist)
        result.raw_data["suggestions"] = suggestions
        stock_map = {s.symbol: s for s in context.watchlist}
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if not stock:
                continue
            save_suggestion(
                stock_symbol=symbol,
                stock_name=stock.name,
                action=sug["action"],
                action_label=sug["action_label"],
                signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                reason=sug.get("reason", ""),
                agent_name=self.name,
                agent_label=self.display_name,
                expires_hours=12,
                prompt_context=user_content,
                ai_response=result.content,
                stock_market=stock.market.value,
                meta={
                    "source": "news_digest",
                    "since_hours_used": data.get("since_hours_used", self.since_hours),
                    "related_count": len(data.get("related_news", []) or []),
                    "important_count": len(data.get("important_news", []) or []),
                    "plan": {
                        "triggers": sug.get("triggers")
                        if isinstance(sug.get("triggers"), list)
                        else [],
                        "invalidations": sug.get("invalidations")
                        if isinstance(sug.get("invalidations"), list)
                        else [],
                        "risks": sug.get("risks")
                        if isinstance(sug.get("risks"), list)
                        else [],
                    }
                    if isinstance(sug, dict)
                    else {},
                },
            )

        # Save to history (use "*" to represent global)
        related_news: list[NewsItem] = data.get("related_news", []) or []
        important_news: list[NewsItem] = data.get("important_news", []) or []
        payload_news = []
        for it in (related_news + important_news)[:30]:
            payload_news.append(
                {
                    "source": it.source,
                    "external_id": it.external_id,
                    "title": it.title,
                    "publish_time": it.publish_time.isoformat(),
                    "symbols": it.symbols,
                    "importance": it.importance,
                    "url": it.url,
                }
            )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "since_hours": self.since_hours,
                "since_hours_used": data.get("since_hours_used", self.since_hours),
                "related_count": len(related_news),
                "important_count": len(important_news),
                "news": payload_news,
                "suggestions": suggestions,
                "prompt_context": user_content[:2000],
            },
        )

        return result
