"""把 TickerKeep Provider 体系适配进 TradingAgents 数据流。

TradingAgents 上游(0.2.x)默认通过 `tradingagents.dataflows.interface.route_to_vendor`
把数据请求路由到 yfinance / alpha_vantage 等 vendor。**没有公开 toolkit 注入入口**。

我们的策略:**monkeypatch route_to_vendor**。当 LangGraph 节点调用 `get_stockstats_*`
等方法时,我们的 patch 检测 symbol 是 legacy A 股代码(6 位数字)就走 TickerKeep 缓存数据,
港股先试上游再兜底,其余(US/CA 字母 ticker)放行到上游默认 vendor(yfinance 等)。

这避免:
- TradingAgents 用 yfinance 拉 legacy CN/HK 标的拉不到
- 重复请求外部 API(TickerKeep 已有缓存的 quote/kline 直接复用)

也保留:
- US/CA 走上游 yfinance vendor 不变;行业/主题关键词新闻走 Google News
- 用户可关闭 patch 走原生路径

TickerKeep 缓存里的 `financial`(Yahoo Finance / SEC EDGAR 财务摘要)和 `holders`
(内部人/机构持股)对所有市场统一提供。

注意:本模块对上游 TradingAgents API 有强依赖,如上游重构 route_to_vendor 接口
需要同步更新。已通过 `tradingagents` 软依赖 + try/except 优雅降级。
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from contextlib import contextmanager
from typing import Any
from src.models.market import default_market

logger = logging.getLogger(__name__)


# 缓存:在 patch 上下文里把 TickerKeep 拉好的数据塞这里,patch 命中时直接返回。
# 用 ContextVar 而非模块级 dict:深度分析跑在 asyncio.to_thread worker 线程,
# to_thread 会 copy_context(),每个并发任务拿到独立副本 —— 避免两只标的并发
# 分析时互相覆盖数据(广汽 601238 的报告混入赛力斯 601127 的 K线/新闻)。
_TICKERKEEP_DATA: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_TA_TICKERKEEP_DATA", default={}
)

# 跟随当前请求的 trace_id;toolkit hit/miss 日志归属到这次分析
_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_TA_TRACE_ID", default=""
)


def _cache() -> dict[str, Any]:
    """读当前 context 的 TickerKeep 数据快照(并发隔离)。"""
    return _TICKERKEEP_DATA.get()


@contextmanager
def tickerkeep_data_context(data: dict[str, Any], trace_id: str = ""):
    """在调用 TradingAgents 的代码块周围用本 context manager 注入数据。

    Args:
        data: 含 stock / quote / klines / events / fundamentals / holders / financial 的字典
        trace_id: 本次分析的 trace_id,用于把 toolkit 命中日志归属到该运行

    退出 context 时还原数据。基于 ContextVar,并发任务(及其 to_thread worker)
    互不干扰。
    """
    token = _TICKERKEEP_DATA.set(dict(data))
    tid_token = _CURRENT_TRACE_ID.set(trace_id or "")
    try:
        yield
    finally:
        _TICKERKEEP_DATA.reset(token)
        _CURRENT_TRACE_ID.reset(tid_token)


def _emit_toolkit_log(level: str, action: str, method_name: str, symbol: str, **extra):
    """把 toolkit hit/miss/passthrough 写进同 trace_id 的日志,前端可在弹窗看到。"""
    from src.core.log_context import log_context

    trace_id = _CURRENT_TRACE_ID.get()
    if not trace_id:
        # 没 trace_id 也打普通日志(可在日志中心按 logger 过滤)
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")
        return
    with log_context(
        trace_id=trace_id,
        agent_name="tradingagents",
        event="ta_toolkit",
        tags={"action": action, "method": method_name, "symbol": symbol, **extra},
    ):
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")


def is_a_share(symbol: str) -> bool:
    """A 股代码判定:6 位纯数字。"""
    return bool(symbol) and len(symbol) == 6 and symbol.isdigit()


def is_hk_share(symbol: str) -> bool:
    """港股代码判定:5 位纯数字(00241/00700/...)。"""
    return bool(symbol) and len(symbol) == 5 and symbol.isdigit()


def is_tickerkeep_routable(symbol: str) -> bool:
    """该 ticker 是否应该走 TickerKeep 数据(而不是上游 yfinance)。

    A 股(6 位数字)yfinance 拉不到,港股(5 位数字)yfinance 也要 .HK 后缀,
    都需要 TickerKeep 兜底。美股(字母 ticker)继续走 yfinance。
    """
    return is_a_share(symbol) or is_hk_share(symbol)


_TICKER_RE = re.compile(r"[A-Z0-9.\-^=]{1,12}")


def _looks_like_keyword_query(symbol: str) -> bool:
    """True when ``symbol`` is a free-text industry/theme query ("electric vehicles",
    "汽车行业") rather than a ticker (AAPL / SHOP.TO / BRK-B / ^GSPC / CL=F).

    Ticker = 1-12 chars of ``[A-Z0-9.\\-^=]`` after upper-casing; anything else (spaces,
    non-ASCII, long phrases) is treated as a keyword query and served via Google News.
    Empty string is not a query.
    """
    s = str(symbol or "").strip()
    if not s:
        return False
    return not _TICKER_RE.fullmatch(s.upper())


def hk_symbol_to_yfinance(symbol: str) -> str:
    """港股 TickerKeep 5 位代码 → yfinance 格式。

    阿里健康 00241 → 0241.HK
    腾讯 00700 → 0700.HK
    yfinance 港股是 4 位数字 + .HK 后缀。
    """
    if not is_hk_share(symbol):
        return symbol
    # 去掉首位 0(00241 → 0241),保留 4 位
    s = symbol.lstrip("0")
    if len(s) > 4:
        s = s[-4:]
    return s.zfill(4) + ".HK"


def _yfinance_response_has_data(text: str) -> bool:
    """启发式判断 yfinance 返回是否包含真实数据。

    yfinance 拿不到数据时返回类似:"No data found for symbol 'XXX' between ..."
    或返回极短的空表头。
    """
    if not text:
        return False
    t = str(text).strip()
    if len(t) < 50:
        return False
    low = t.lower()
    if any(kw in low for kw in (
        "no data found",
        "no data available",
        "symbol may be delisted",
        "no information available",
    )):
        return False
    return True


# 上游 tool 文件用 `from tradingagents.dataflows.interface import route_to_vendor`,
# 这是 import-time binding,每个模块持有 **原函数引用**。
# 只 patch 源头模块属性不够 —— 必须把每个 import site 的 module-level
# 名字都替换掉,所有调用才会走我们的拦截。
_ROUTE_TO_VENDOR_IMPORT_SITES = (
    "tradingagents.agents.utils.fundamental_data_tools",
    "tradingagents.agents.utils.news_data_tools",
    "tradingagents.agents.utils.core_stock_tools",
    "tradingagents.agents.utils.technical_indicators_tools",
)


# patch 引用计数:多个并发深度分析共享同一次安装,第一个进入者保存真
# route_to_vendor 并装到所有 import site,最后一个退出才恢复。数据隔离靠
# _TICKERKEEP_DATA(ContextVar),patch 本身只需进程级安装一次 —— 消除原先
# "A 退出时把全局恢复成 B 的 _patched"的嵌套竞态。
_patch_lock = threading.Lock()
_patch_refcount = 0
_patch_saved_sites: list[tuple[Any, str, Any]] = []  # (module, attr_name, original_value)
_real_route_to_vendor = None  # 真 route_to_vendor(走上游 vendor 时用)


def _patched_route_to_vendor(method_name: str, *args, **kwargs):
    """模块级无状态 patch:A 股走 TickerKeep(读 _cache()),港股先试上游再兜底,其余放行。

    与上游 route_to_vendor(method, *args, **kwargs) 完全同签名。上游所有 toolkit
    都用 positional 传 ticker:
      route_to_vendor("get_fundamentals", ticker, curr_date)
      route_to_vendor("get_news", ticker, start_date, end_date)
      route_to_vendor("get_stock_data", symbol, ...)
      route_to_vendor("get_global_news", curr_date, look_back_days, limit)  # 无 symbol

    无任何实例状态:symbol 来自调用参数,数据来自 _cache()(当前 context),
    所以多个并发任务共享同一个 _patched 也不会串台。
    """
    symbol = ""
    # 大多数 method 第一个 positional 就是 ticker/symbol(get_global_news 等例外)
    if args and isinstance(args[0], str) and not args[0][:4].isdigit():
        # 第一个参数是 ticker(601127)而非日期(2026-...)
        if not (len(args[0]) >= 8 and args[0][4] in "-/"):
            symbol = args[0]
    # 兜底:再看 kwargs
    if not symbol:
        symbol = kwargs.get("symbol") or kwargs.get("ticker") or ""

    # 没拿到 symbol 时(如 get_global_news),用 cache 里的标的兜底,
    # 拦截"全局新闻"类调用避免拉到无关 Yahoo 鞋类/汽油新闻。
    if not symbol:
        cached_stock = _cache().get("stock")
        cached_symbol = getattr(cached_stock, "symbol", "") if cached_stock else ""
        if is_tickerkeep_routable(cached_symbol):
            symbol = cached_symbol

    # A 股:yfinance/finnhub 拉不到,直接走 TickerKeep
    if is_a_share(symbol) and _cache():
        try:
            result = _serve_from_tickerkeep(method_name, symbol, kwargs, args=args)
            _emit_toolkit_log(
                "info", "HIT", method_name, symbol,
                chars=len(result),
                snippet=str(result)[:4000],
                source="tickerkeep",
                extra_args=_args_summary(args),
            )
            return result
        except NotImplementedError:
            _emit_toolkit_log(
                "info", "MISS", method_name, symbol,
                reason="TickerKeep 未实现该 method,放行到上游",
            )
        except Exception as e:
            _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
            return f"[TickerKeep error: {e}]"

    # 港股:先把 ticker 转成 yfinance 格式(00241 → 0241.HK)试上游,
    # yfinance 返回有数据就用,无数据(No data found / 极短返回)fallback 到 TickerKeep。
    if is_hk_share(symbol):
        yf_symbol = hk_symbol_to_yfinance(symbol)
        new_args = list(args)
        # 替换第一个 positional ticker(如果它就是当前 symbol)
        for i, a in enumerate(new_args):
            if isinstance(a, str) and a == symbol:
                new_args[i] = yf_symbol
                break
        try:
            upstream_result = _real_route_to_vendor(method_name, *new_args, **kwargs)
        except Exception as e:
            upstream_result = ""
            logger.warning(f"[TA toolkit] HK upstream {method_name}({yf_symbol}) 失败: {e}")
        upstream_str = str(upstream_result) if upstream_result is not None else ""

        if _yfinance_response_has_data(upstream_str):
            # 走上游 vendor 拿到数据 = PASSTHROUGH,只是 source 标记转格式
            _emit_toolkit_log(
                "info", "PASSTHROUGH", method_name, symbol,
                chars=len(upstream_str),
                snippet=upstream_str[:4000],
                source=f"upstream HK(→{yf_symbol})",
                extra_args=_args_summary(args),
            )
            return upstream_result

        # yfinance 没数据 → fallback 到 TickerKeep = HIT(TickerKeep 兜底提供数据)
        if _cache():
            try:
                result = _serve_from_tickerkeep(method_name, symbol, kwargs, args=args)
                _emit_toolkit_log(
                    "info", "HIT", method_name, symbol,
                    chars=len(result),
                    snippet=str(result)[:4000],
                    source="tickerkeep HK fallback",
                    extra_args=_args_summary(args),
                )
                return result
            except NotImplementedError:
                pass
            except Exception as e:
                _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
                return f"[TickerKeep error: {e}]"
        # 港股两边都没 = ERROR
        _emit_toolkit_log(
            "warning", "ERROR", method_name, symbol,
            chars=len(upstream_str), snippet=upstream_str[:4000],
            source=f"upstream HK(→{yf_symbol}) + tickerkeep 均空",
            error="HK no data from either source",
        )
        return upstream_result

    # 行业/主题新闻:get_news 的 query 不是 ticker(含空格/非 ASCII 的检索词) → Google News
    # 关键词搜索,替代只认 ticker 的上游 vendor。
    if symbol and "news" in method_name.lower() and not is_tickerkeep_routable(symbol) and _looks_like_keyword_query(symbol):
        try:
            result = _serve_keyword_news(symbol, market=_cached_market())
            _emit_toolkit_log(
                "info", "HIT", method_name, symbol,
                chars=len(result), snippet=result[:4000],
                source="tickerkeep keyword news (Google News)", extra_args=_args_summary(args),
            )
            return result
        except Exception as e:
            _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
            return f"[Keyword news search failed for \"{symbol}\": {e}]"

    # 美股 / 其他:直接走上游 vendor。
    # 降级兜底:上游某些工具依赖外部 key/服务(FRED 无 key、polymarket SSL、未配置 vendor 等),
    # 失败会抛异常拖垮整个深度分析。这里捕获并返回空 —— 单个工具缺数据 ≠ 整轮失败。
    try:
        upstream_result = _real_route_to_vendor(method_name, *args, **kwargs)
    except Exception as e:
        logger.warning(f"[TA toolkit] 上游 {method_name} 失败,降级返回空(不中断分析): {e}")
        _emit_toolkit_log(
            "warning", "DEGRADE", method_name, symbol or "(none)",
            error=str(e)[:200], extra_args=_args_summary(args),
        )
        return ""
    upstream_str = str(upstream_result) if upstream_result is not None else ""
    action_label = "PASSTHROUGH" if not is_a_share(symbol) else "FALLTHROUGH"
    _emit_toolkit_log(
        "info", action_label, method_name, symbol or "(none)",
        chars=len(upstream_str),
        snippet=upstream_str[:4000],
        source="upstream",
        extra_args=_args_summary(args),
    )
    return upstream_result


@contextmanager
def patch_route_to_vendor():
    """Monkeypatch tradingagents.dataflows.interface.route_to_vendor + 所有 import sites。

    当请求 A 股代码时,从 _TICKERKEEP_DATA(当前 context)返回 TickerKeep 已拉的数据。
    非 A 股放行到原函数。

    引用计数 + 锁:并发的多个深度分析共享同一次安装,第一个进入者装、最后一个
    退出才卸载,_real_route_to_vendor 永远保存真函数 —— 消除嵌套 patch 链错乱。

    如果 tradingagents 库未安装,本 context manager 是 no-op,不抛异常。
    """
    global _patch_refcount, _real_route_to_vendor

    try:
        from tradingagents.dataflows import interface as ta_interface
    except ImportError:
        logger.warning("[TA toolkit] tradingagents 未安装,跳过 monkeypatch")
        yield
        return

    if not hasattr(ta_interface, "route_to_vendor"):
        logger.warning(
            "[TA toolkit] route_to_vendor 不存在 (上游 API 可能变更),"
            "走默认 vendor 路径"
        )
        yield
        return

    # 同时接管 load_ohlcv:新上游 get_verified_market_snapshot 绕过 route_to_vendor
    # 直连 yfinance,A股/港股拉不到会 NoMarketDataError(永久安装,非 TickerKeep 标的透传)。
    _ensure_load_ohlcv_patched()

    import importlib
    with _patch_lock:
        if _patch_refcount == 0:
            # 第一个进入者:保存真函数并装到源头 + 所有 import sites
            # (`from ... import route_to_vendor` 是 import-time binding,只 patch
            # 源头不够 — 工具模块持有的原引用不变)
            _real_route_to_vendor = ta_interface.route_to_vendor
            _patch_saved_sites.clear()
            ta_interface.route_to_vendor = _patched_route_to_vendor
            _patch_saved_sites.append((ta_interface, "route_to_vendor", _real_route_to_vendor))
            for module_path in _ROUTE_TO_VENDOR_IMPORT_SITES:
                try:
                    mod = importlib.import_module(module_path)
                except ImportError:
                    continue
                if hasattr(mod, "route_to_vendor"):
                    _patch_saved_sites.append((mod, "route_to_vendor", mod.route_to_vendor))
                    mod.route_to_vendor = _patched_route_to_vendor
                    logger.debug(f"[TA toolkit] patched route_to_vendor in {module_path}")
        _patch_refcount += 1

    try:
        yield
    finally:
        with _patch_lock:
            _patch_refcount -= 1
            if _patch_refcount <= 0:
                _patch_refcount = 0
                for mod, attr, orig in _patch_saved_sites:
                    setattr(mod, attr, orig)
                _patch_saved_sites.clear()


# ---------------------------------------------------------------------------
# load_ohlcv 接管
# 新上游 get_verified_market_snapshot → market_data_validator.load_ohlcv 直连 yfinance,
# 不经 route_to_vendor。A股(无 .SS)/港股(无 .HK)yfinance 拉不到 → NoMarketDataError,
# 整个 TradingAgents 分析失败。这里把 A股/港股的 load_ohlcv 改走 TickerKeep K线;
# 非 TickerKeep 标的(美股)透传原生 yfinance,故进程级永久安装安全、无需卸载。
# ---------------------------------------------------------------------------
_LOAD_OHLCV_PATCHED = False
_real_load_ohlcv: Any = None
_LOAD_OHLCV_IMPORT_SITES = (
    "tradingagents.dataflows.market_data_validator",
    "tradingagents.dataflows.interface",
)


def _build_tickerkeep_ohlcv_df(symbol: str, curr_date: str):
    """用 TickerKeep K线构建与原生 load_ohlcv 同结构的 DataFrame(Date/Open/High/Low/Close/Volume)。"""
    import pandas as pd

    from src.collectors.kline_collector import KlineCollector
    from src.models.market import MarketCode

    market = MarketCode.CN if is_a_share(symbol) else MarketCode.HK
    klines = KlineCollector(market).get_klines(symbol, days=750)
    if not klines:
        return None
    df = pd.DataFrame(
        [
            {
                "Date": k.date,
                "Open": k.open,
                "High": k.high,
                "Low": k.low,
                "Close": k.close,
                "Volume": k.volume,
            }
            for k in klines
        ]
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    if curr_date:
        try:
            df = df[df["Date"] <= pd.to_datetime(curr_date)]
        except Exception:
            pass
    return df.reset_index(drop=True)


def _tickerkeep_load_ohlcv(symbol: str, curr_date: str, *args, **kwargs):
    """A股/港股走 TickerKeep K线;美股等非 TickerKeep 标的放行原生 yfinance。

    A股/港股 TickerKeep 拉空时**不回退 yfinance**(A股/港股在 Yahoo 无数据 + 限流,回退只会
    把"K线获取失败"变成误导的"Yahoo no rows"),直接抛 NoMarketDataError 报清晰错。
    """
    if is_tickerkeep_routable(symbol):
        df = None
        try:
            df = _build_tickerkeep_ohlcv_df(symbol, curr_date)
        except Exception as e:
            logger.warning(f"[TA toolkit] load_ohlcv TickerKeep 取数异常 symbol={symbol}: {e}")
        if df is not None and not df.empty:
            _emit_toolkit_log("info", "tickerkeep", "load_ohlcv", symbol, rows=int(len(df)))
            return df
        _emit_toolkit_log("warning", "miss", "load_ohlcv", symbol)
        # A股/港股不回退 yahoo:抛 TA 期望的 NoMarketDataError,报清晰真因
        try:
            from tradingagents.dataflows.errors import NoMarketDataError
            raise NoMarketDataError(
                symbol, symbol,
                "TickerKeep K线获取失败(A股/港股不回退 Yahoo,请检查代理分流/数据源是否可达)",
            )
        except ImportError:
            raise RuntimeError(
                f"TickerKeep K线获取失败 symbol={symbol}(A股/港股不回退 Yahoo,请检查代理/数据源)"
            )
    return _real_load_ohlcv(symbol, curr_date, *args, **kwargs)


def _ensure_load_ohlcv_patched() -> None:
    """进程级幂等安装 load_ohlcv 补丁(含所有 import sites);非 TickerKeep 标的透传,无需卸载。"""
    global _LOAD_OHLCV_PATCHED, _real_load_ohlcv
    if _LOAD_OHLCV_PATCHED:
        return
    try:
        from tradingagents.dataflows import stockstats_utils
    except ImportError:
        return
    if not hasattr(stockstats_utils, "load_ohlcv"):
        return
    import importlib

    with _patch_lock:
        if _LOAD_OHLCV_PATCHED:
            return
        _real_load_ohlcv = stockstats_utils.load_ohlcv
        stockstats_utils.load_ohlcv = _tickerkeep_load_ohlcv
        for module_path in _LOAD_OHLCV_IMPORT_SITES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            if getattr(mod, "load_ohlcv", None) is not None:
                mod.load_ohlcv = _tickerkeep_load_ohlcv
                logger.debug(f"[TA toolkit] patched load_ohlcv in {module_path}")
        _LOAD_OHLCV_PATCHED = True
        logger.info("[TA toolkit] load_ohlcv 已接管(A股/港股走 TickerKeep,防 yfinance NoMarketData)")


def _args_summary(args: tuple) -> str:
    """把 positional args 简短打印,放进日志 extra_args 便于区分 get_indicators 多次调用。

    e.g. ("601238", "macd", "2026-05-17", 30) → "macd, 2026-05-17, 30"(跳过 symbol)
    """
    if not args:
        return ""
    parts = []
    for i, a in enumerate(args):
        if i == 0 and isinstance(a, str) and len(a) == 6 and a.isdigit():
            continue  # 跳过 symbol(已单独显示)
        s = str(a)
        if len(s) > 40:
            s = s[:40] + "..."
        parts.append(s)
    return ", ".join(parts)


def _stock_meta_header(symbol: str) -> str:
    """渲染标的元信息(公司名/市场/价格),作为所有工具返回的前缀。

    A 股 ticker 不在 yfinance/finnhub 数据集,LLM 不能从 ticker 反查公司名,
    必须显式告诉它"601127 = 赛力斯",否则会瞎编(如把 601127 当中国平安)。
    """
    stock = _cache().get("stock")
    quote = _cache().get("quote") or {}

    name = ""
    market = default_market()
    industry = ""
    if stock is not None:
        name = getattr(stock, "name", "") or ""
        market_obj = getattr(stock, "market", None)
        market = getattr(market_obj, "value", str(market_obj or default_market()))
    if not name and isinstance(quote, dict):
        name = quote.get("name") or ""
    if isinstance(quote, dict):
        industry = quote.get("industry") or ""

    from src.agents.tradingagents.portfolio_context import _MARKET_LABELS
    market_label = _MARKET_LABELS.get(str(market).upper(), market)
    cur_price = _attr(quote, "current_price", "") or _attr(quote, "price", "")
    change_pct = _attr(quote, "change_pct", "")

    lines = [
        f"[Stock Metadata] symbol={symbol}, name={name or 'N/A'}, market={market_label}",
    ]
    if industry:
        lines.append(f"  Industry: {industry}")
    if cur_price:
        try:
            lines.append(
                f"  Current price: {float(cur_price):.2f}"
                + (f" ({float(change_pct):+.2f}%)" if change_pct != "" else "")
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        "  IMPORTANT: DO NOT guess the company from the ticker code alone — use the name above."
    )
    return "\n".join(lines)


def _serve_from_tickerkeep(method_name: str, symbol: str, kwargs: dict, args: tuple = ()) -> str:
    """从 _cache()(当前 context 的数据)构造 TradingAgents 期望的数据格式(CSV / JSON 字符串)。

    上游各 vendor 方法返回类型不一,通常是 str(已格式化的 CSV/表格/JSON)。
    本函数尽量兼容常见 method_name。**未识别的 method 返回空串,触发上游默认 vendor。**

    所有分支都以「标的元信息」开头,避免 LLM 在 A 股 ticker 上瞎编公司名。
    """
    method = (method_name or "").lower()
    header = _stock_meta_header(symbol)

    # 1a) 单指标查询:get_indicators(symbol, indicator_name, curr_date, look_back_days)
    # 上游对每个技术指标(macd/rsi/kdj/boll/...)各调一次,8 次返回相同 K线 CSV 是浪费。
    # 我们按 indicator 名返回简短的"该指标当前值 + 简要解读",避免重复污染上下文。
    if "indicator" in method:
        if args and len(args) >= 2:
            indicator = str(args[1]).lower()
            return f"{header}\n\n{_render_single_indicator(indicator, symbol)}"
        # 没传 indicator 参数:降级到 K 线 CSV
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No data available for indicators on {symbol}]"

    # 1b) K 线 / 股价完整 CSV:get_stockstats / get_yfin_data / get_stock_data
    if any(k in method for k in (
        "stockstats", "yfin", "ohlcv", "kline", "price", "stock_data",
    )):
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No kline data available from TickerKeep for {symbol}]"

    # 2) 持股/内部人交易:get_insider_transactions / get_insider_sentiment / *capital* / *flow*
    #    注意:不要匹配 "cashflow" / "cash_flow",那是现金流量表(见 5)
    if "insider" in method or "capital" in method or ("flow" in method and "cash" not in method):
        return f"{header}\n\n{_serve_holders(symbol)}"

    # 3) 公告/事件/新闻:get_finnhub_news / get_news / get_events / get_global_news
    if any(k in method for k in ("news", "event", "announce")):
        events = _cache().get("events") or []
        if events:
            return f"{header}\n\n{_events_to_text(events, limit=20)}"
        return (
            f"{header}\n\n[No company-specific news/events available for {symbol}. "
            "DO NOT pull unrelated global news as a substitute — focus the analysis "
            "on the metadata above and other tool outputs.]"
        )

    # 4) 基本面 / 财报:有 marketdata Fundamentals(Yahoo Finance / SEC EDGAR)时返回完整指标,
    #    否则 fallback 到 quote 轻量数据
    from src.agents.tradingagents.financial_data import has_financial_data

    financial = _cache().get("financial")
    has_fin = has_financial_data(financial)
    if "fundamental" in method or "financial" in method:
        if has_fin:
            from src.agents.tradingagents.financial_data import render_fundamentals_summary
            return f"{header}\n\n{render_fundamentals_summary(financial)}"
        return f"{header}\n\n{_quote_to_lightweight_fundamentals(symbol)}"
    if "income" in method:
        if has_fin:
            from src.agents.tradingagents.financial_data import render_income_statement
            return f"{header}\n\n{render_income_statement(financial)}"
        return (
            f"{header}\n\n[Income statement not available for {symbol}. "
            "Avoid invented revenue/earnings numbers.]"
        )
    if "balance" in method or "sheet" in method:
        if has_fin:
            from src.agents.tradingagents.financial_data import render_balance_sheet
            return f"{header}\n\n{render_balance_sheet(financial)}"
        return (
            f"{header}\n\n[Balance sheet not available for {symbol}. "
            "Avoid invented assets/liabilities numbers.]"
        )
    # 5) 现金流量表
    if "cashflow" in method or "cash_flow" in method:
        if has_fin:
            from src.agents.tradingagents.financial_data import render_cashflow
            return f"{header}\n\n{render_cashflow(financial)}"
        return (
            f"{header}\n\n[Cash flow statement not available for {symbol}. "
            "Avoid invented cash flow numbers.]"
        )

    # 未识别:让上游走默认 vendor
    raise NotImplementedError(f"no tickerkeep backing for {method_name}")


def _cached_market() -> str:
    """Market code of the stock in the current context (falls back to the default market)."""
    stock = _cache().get("stock")
    market_obj = getattr(stock, "market", None) if stock is not None else None
    market = getattr(market_obj, "value", market_obj)
    return str(market or default_market()).upper()


def _serve_keyword_news(keyword: str, market: str | None = None) -> str:
    """Live industry/theme keyword search via Google News RSS, formatted for the LLM.

    Used when get_news's query is a theme phrase ("electric vehicles", "汽车行业") rather
    than a ticker. ``md_news_by_keyword`` is sync; call it directly.
    """
    from src.core.marketdata_client import md_news_by_keyword
    from src.core.source_labels import label_for

    mkt = (market or _cached_market()).upper()
    items = md_news_by_keyword(keyword, market=mkt)
    if not items:
        return (
            f"[No industry/theme news found for \"{keyword}\" (Google News). Base your analysis on "
            "stock-specific news + metadata, do not fabricate industry news.]"
        )
    lines = [f"[Industry/theme news \"{keyword}\" (from Google News, {len(items)} items)]"]
    for it in items[:15]:
        ts = _attr(it, "publish_time", "")
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%Y-%m-%d %H:%M")
        title = _attr(it, "title", "") or ""
        outlet = label_for(_attr(it, "source", ""), _attr(it, "publisher", ""))
        suffix = f" ({outlet})" if outlet else ""
        lines.append(f"- [{ts}] {title}{suffix}")
    return "\n".join(lines)


_HOLDERS_NOT_PUBLISHED = (
    "[Capital-flow data is not published for US/CA markets; "
    "see insider/institutional ownership instead]"
)


def _serve_holders(symbol: str) -> str:
    """Ownership text (breakdown / top institutions / recent insider transactions).

    Reads ``holders`` from the current context (rows from ``md.holders`` — objects — or
    ``md_holders`` — dicts); when the key is absent, fetches live via ``md_holders``.
    No rows -> the "not published" line (US/CA have no capital-flow feed).
    """
    cache = _cache()
    rows = cache.get("holders")
    if rows is None:
        try:
            from src.core.marketdata_client import md_holders

            rows = md_holders([symbol], market=_cached_market())
        except Exception as e:
            logger.warning(f"[TA toolkit] md_holders({symbol}) failed: {e}")
            rows = []
    return _holders_to_text(rows)


def _holders_to_text(rows, *, institutions: int = 10, insider_txs: int = 15) -> str:
    """Format ``HolderItem``-shaped rows (objects or dicts) into an ownership summary."""
    rows = list(rows or [])
    if not rows:
        return _HOLDERS_NOT_PUBLISHED

    breakdown = [r for r in rows if _attr(r, "kind", "") == "breakdown"]
    inst = [r for r in rows if _attr(r, "kind", "") == "institution"]
    tx = [r for r in rows if _attr(r, "kind", "") == "insider_tx"]

    sources = {str(_attr(r, "source", "") or "") for r in rows}
    from src.core.source_labels import label_for

    src_label = " / ".join(sorted(label_for(s) for s in sources if s)) or "TickerKeep"
    lines = [f"[Ownership data from TickerKeep ({src_label})]"]

    if breakdown:
        parts = []
        for r in breakdown:
            holder = str(_attr(r, "holder", "") or "")
            pct = _attr(r, "pct_out", None)
            shares = _attr(r, "shares", None)
            if pct not in (None, ""):
                parts.append(f"{holder}: {float(pct):.2f}%")
            elif shares not in (None, ""):
                parts.append(f"{holder}: {_fmt_shares(shares)}")
        if parts:
            lines.append("- Breakdown: " + "; ".join(parts))

    if inst:
        lines.append(f"Top institutional holders ({min(len(inst), institutions)} of {len(inst)}):")
        for r in inst[:institutions]:
            holder = str(_attr(r, "holder", "") or "")
            bits = []
            shares = _attr(r, "shares", None)
            if shares not in (None, ""):
                bits.append(f"{_fmt_shares(shares)} shares")
            pct = _attr(r, "pct_out", None)
            if pct not in (None, ""):
                bits.append(f"{float(pct):.2f}% of shares out")
            chg = _attr(r, "change_pct", None)
            if chg not in (None, ""):
                bits.append(f"change {float(chg):+.2f}%")
            date = str(_attr(r, "date", "") or "")
            if date:
                bits.append(f"as of {date}")
            lines.append(f"- {holder}: {', '.join(bits)}" if bits else f"- {holder}")

    if tx:
        buys = sum(1 for r in tx if str(_attr(r, "transaction", "")).lower().startswith("purchase"))
        sells = sum(1 for r in tx if str(_attr(r, "transaction", "")).lower().startswith("sale"))
        lines.append(
            f"Recent insider transactions ({min(len(tx), insider_txs)} of {len(tx)}; "
            f"{buys} purchases / {sells} sales):"
        )
        for r in tx[:insider_txs]:
            holder = str(_attr(r, "holder", "") or "")
            position = str(_attr(r, "position", "") or "")
            who = f"{holder} ({position})" if position else holder
            action = str(_attr(r, "transaction", "") or "transaction")
            bits = []
            shares = _attr(r, "shares", None)
            if shares not in (None, ""):
                bits.append(f"{_fmt_shares(shares)} shares")
            value = _attr(r, "value", None)
            if value not in (None, ""):
                bits.append(f"value {_fmt_shares(value)}")
            date = str(_attr(r, "date", "") or "")
            prefix = f"[{date}] " if date else ""
            detail = f" ({', '.join(bits)})" if bits else ""
            lines.append(f"- {prefix}{who}: {action}{detail}")

    if len(lines) == 1:
        return _HOLDERS_NOT_PUBLISHED
    lines.append(
        "Note: US/CA markets publish no main-force capital-flow feed; read insider "
        "buying/selling and institutional concentration instead. Do NOT invent flow numbers."
    )
    return "\n".join(lines)


def _fmt_shares(v) -> str:
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    av = abs(fv)
    if av >= 1e9:
        return f"{fv / 1e9:.2f}B"
    if av >= 1e6:
        return f"{fv / 1e6:.2f}M"
    if av >= 1e3:
        return f"{fv / 1e3:.1f}K"
    return f"{fv:,.0f}"


def _render_single_indicator(indicator: str, symbol: str) -> str:
    """按 indicator 名(macd/rsi/kdj/boll/...)返回该指标当前值,而不是全 K 线 CSV。

    数据源:KlineCollector.get_technical_indicators 已经算好的 dataclass。
    """
    tech = _cache().get("technical")
    if not tech:
        # 没预计算时,fallback 到 K 线 CSV(让 LLM 自己算)
        klines = _cache().get("klines") or []
        if klines:
            return (
                f"[Indicator query: {indicator}] (no precomputed value, "
                f"returning raw K-line CSV for self-calculation)\n\n"
                f"{_klines_to_csv(klines[-30:])}"  # 仅 30 条够
            )
        return f"[No data available for indicator '{indicator}' on {symbol}]"

    ind = indicator.lower()
    lines = [f"[Technical Indicator: {indicator.upper()}] for {symbol}"]
    handled = False

    def _g(name):
        return _attr(tech, name, None)

    if "macd" in ind:
        dif, dea, hist = _g("macd_dif"), _g("macd_dea"), _g("macd_hist")
        cross = _g("macd_cross") or ""
        lines.append(f"- DIF: {dif} | DEA: {dea} | Hist: {hist}")
        if cross:
            lines.append(f"- Latest cross: {cross}")
        handled = True
    if "rsi" in ind:
        lines.append(f"- RSI(6): {_g('rsi6')} | RSI(12): {_g('rsi12')} | RSI(24): {_g('rsi24')}")
        st = _g("rsi_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "kdj" in ind:
        lines.append(f"- K: {_g('kdj_k')} | D: {_g('kdj_d')} | J: {_g('kdj_j')}")
        st = _g("kdj_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "boll" in ind:
        lines.append(
            f"- Upper: {_g('boll_upper')} | Mid: {_g('boll_mid')} | Lower: {_g('boll_lower')}"
        )
        st = _g("boll_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if any(x in ind for x in ("ma", "sma", "ema")) and not handled:
        lines.append(
            f"- MA5: {_g('ma5')} | MA10: {_g('ma10')} | MA20: {_g('ma20')} | MA60: {_g('ma60')}"
        )
        trend = _g("trend")
        if trend:
            lines.append(f"- Trend: {trend}")
        handled = True
    if any(x in ind for x in ("vol", "volume")):
        lines.append(f"- Volume ratio: {_g('volume_ratio')} | Trend: {_g('volume_trend')}")
        handled = True

    if not handled:
        # 未识别的指标:倾倒全部技术指标摘要
        lines.append("(indicator name not specifically recognized — returning full snapshot)")
        for attr in (
            "ma5", "ma10", "ma20", "ma60",
            "macd_dif", "macd_dea", "macd_hist", "macd_cross",
            "rsi6", "rsi12", "rsi24", "rsi_status",
            "kdj_k", "kdj_d", "kdj_j", "kdj_status",
            "boll_upper", "boll_mid", "boll_lower", "boll_status",
            "volume_ratio", "volume_trend", "trend",
        ):
            v = _g(attr)
            if v is not None and v != "":
                lines.append(f"- {attr}: {v}")
    return "\n".join(lines)


def _quote_to_lightweight_fundamentals(symbol: str) -> str:
    """从 quote 拉"轻量基本面"(市值/PE/换手率/成交额),给 LLM 一些真实数据。"""
    quote = _cache().get("quote") or {}
    if not isinstance(quote, dict):
        return f"[No lightweight fundamentals available for {symbol}]"

    lines = ["[Lightweight Fundamentals (from TickerKeep real-time quote)]"]
    fields = [
        ("PE ratio", "pe_ratio"),
        ("Total market cap", "total_market_value"),
        ("Circulating market cap", "circulating_market_value"),
        ("Turnover rate (%)", "turnover_rate"),
        ("Current price", "current_price"),
        ("Today change (%)", "change_pct"),
        ("Today open", "open_price"),
        ("Today high", "high_price"),
        ("Today low", "low_price"),
        ("Prev close", "prev_close"),
        ("Volume (shares)", "volume"),
        ("Turnover", "turnover"),
    ]
    has_any = False
    for label, key in fields:
        v = quote.get(key)
        if v is not None and v != "":
            lines.append(f"- {label}: {v}")
            has_any = True
    if not has_any:
        return f"[No lightweight fundamentals available for {symbol}]"
    lines.append("")
    lines.append(
        "Note: This is real-time market data, NOT a substitute for full financial "
        "statements. Use it as a sanity check (e.g. valuation level via P/E, liquidity "
        "via turnover) rather than as the basis for revenue/earnings claims."
    )
    return "\n".join(lines)


def _klines_to_csv(klines) -> str:
    """KlineData list → CSV 字符串。

    TradingAgents 上游期望:date,open,high,low,close,volume
    """
    if not klines:
        return "date,open,high,low,close,volume\n"
    lines = ["date,open,high,low,close,volume"]
    for k in klines:
        date_v = getattr(k, "date", None) or (k.get("date") if isinstance(k, dict) else "")
        open_v = _attr(k, "open")
        high_v = _attr(k, "high")
        low_v = _attr(k, "low")
        close_v = _attr(k, "close")
        vol_v = _attr(k, "volume")
        lines.append(f"{date_v},{open_v},{high_v},{low_v},{close_v},{vol_v}")
    return "\n".join(lines)


def _events_to_text(events, limit: int = 20) -> str:
    if not events:
        return "No recent announcements/events"
    out = []
    for ev in events[:limit]:
        title = getattr(ev, "title", None) or (
            ev.get("title") if isinstance(ev, dict) else str(ev)
        )
        ts = getattr(ev, "publish_time", None) or (
            ev.get("publish_time") if isinstance(ev, dict) else ""
        )
        out.append(f"- [{ts}] {title}")
    return "\n".join(out)


def _attr(obj, name, default=""):
    if hasattr(obj, name):
        v = getattr(obj, name)
        return v if v is not None else default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default
