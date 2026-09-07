"""End-to-end coverage of every TradingAgents data route for a US symbol (AAPL).

Every upstream tool call must be served from PanWatch data, not from the vendor's own
network calls:
- get_stock_data        -> kline CSV
- get_indicators        -> one refined indicator block
- get_news / get_global_news -> events (or an explicit "do not fabricate" fallback)
- get_fundamentals      -> real figures from the Fundamentals row
- get_balance_sheet     -> book value, debt, cash (and an honest list of what is missing)
- get_cashflow          -> operating and free cash flow
- get_income_statement  -> revenue, net income, margins

Each assertion checks the ticker/company, a real number, and that fallbacks tell the model
not to invent data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.tradingagents.toolkit_adapter import (
    _serve_from_panwatch,
    panwatch_data_context,
)


class _StockMt:
    """Apple mock (US market)."""
    name = "Apple Inc."
    symbol = "AAPL"
    market = type("M", (), {"value": "US"})()


def _quote_mt():
    return {
        "name": "Apple Inc.",
        "current_price": 1480.50,
        "change_pct": 1.20,
        "open_price": 1465.00,
        "high_price": 1485.00,
        "low_price": 1460.00,
        "prev_close": 1463.00,
        "volume": 1_500_000,
        "turnover": 2_220_750_000,
        "pe_ratio": 24.5,
        "total_market_value": 1_860_000_000_000,
        "circulating_market_value": 1_860_000_000_000,
        "turnover_rate": 0.12,
        "industry": "Consumer Electronics",
    }


def _klines_mt():
    return [
        type("K", (), {"date": "2026-05-15", "open": 1460, "high": 1485, "low": 1455, "close": 1480.50, "volume": 1_500_000})(),
        type("K", (), {"date": "2026-05-14", "open": 1450, "high": 1470, "low": 1445, "close": 1463.00, "volume": 1_200_000})(),
    ]


def _events_mt():
    e1 = MagicMock()
    e1.title = "Apple Inc. Q1 2026 revenue up 12% year over year"
    e1.publish_time = "2026-04-28"
    e2 = MagicMock()
    e2.title = "Apple Inc. notice of 2026 annual shareholder meeting"
    e2.publish_time = "2026-04-15"
    return [e1, e2]


def _financial_mt():
    """The cached ``financial`` dict as ``build_financial_from_fundamentals`` produces it."""
    from types import SimpleNamespace

    from src.agents.tradingagents.financial_data import build_financial_from_fundamentals

    return build_financial_from_fundamentals(
        SimpleNamespace(
            report_date="2026-06-27",
            currency="USD",
            revenue=391_035_000_000.0,
            net_profit=93_736_000_000.0,
            eps=6.08,
            bps=4.38,
            roe=147.25,
            gross_margin=46.21,
            net_margin=23.97,
            revenue_yoy=6.10,
            net_profit_yoy=7.20,
            total_market_value=3_450_000_000_000.0,
            pe_ttm=36.65,
            pe_static=32.10,
            pb=52.30,
            ps_ttm=8.82,
            dividend_yield=0.44,
            total_shares=15_204_100_000.0,
            float_shares=15_180_000_000.0,
            operating_cash_flow=118_254_000_000.0,
            free_cash_flow=108_807_000_000.0,
            total_debt=106_629_000_000.0,
            total_cash=61_801_000_000.0,
        )
    )


def _technical_mt():
    t = MagicMock()
    t.ma5 = 1475.20
    t.ma10 = 1468.00
    t.ma20 = 1455.50
    t.ma60 = 1420.00
    t.macd_dif = 8.50
    t.macd_dea = 6.20
    t.macd_hist = 2.30
    t.macd_cross = "golden cross"
    t.rsi6 = 65.0
    t.rsi12 = 60.0
    t.rsi24 = 55.0
    t.rsi_status = "firm"
    t.kdj_k = 75.0
    t.kdj_d = 70.0
    t.kdj_j = 85.0
    t.kdj_status = "strong"
    t.boll_upper = 1500.0
    t.boll_mid = 1460.0
    t.boll_lower = 1420.0
    t.boll_status = "above middle band"
    t.volume_ratio = 1.2
    t.volume_trend = "mild volume expansion"
    t.trend = "bullish alignment"
    return t


def _full_ctx(extras=None):
    ctx = {
        "stock": _StockMt(),
        "quote": _quote_mt(),
        "klines": _klines_mt(),
        "events": _events_mt(),
        "financial": _financial_mt(),
        "technical": _technical_mt(),
    }
    if extras:
        ctx.update(extras)
    return ctx


# ============================================================
# 1. get_stock_data → K 线 CSV
# ============================================================

def test_get_stock_data_returns_kline_csv_for_maotai():
    """get_stock_data 工具:返回Apple K 线 CSV(含日期/收盘价)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_stock_data", "AAPL", {})
    assert "AAPL" in result
    assert "Apple Inc." in result
    assert "2026-05-15" in result
    assert "1480.5" in result  # 收盘价


# ============================================================
# 2. get_indicators → 单指标精炼(不重复 K 线 CSV)
# ============================================================

def test_get_indicators_macd_returns_macd_values_only():
    """get_indicators(symbol, 'macd', ...) 只返回 MACD 数值,不返回 K 线 CSV"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "AAPL", {},
            args=("AAPL", "macd", "2026-05-17", 30),
        )
    assert "MACD" in result
    assert "8.5" in result  # DIF
    assert "golden cross" in result
    # 不应该是完整 K 线 CSV(那是 5008 字)
    assert len(result) < 1000


def test_get_indicators_rsi_returns_rsi_values():
    """get_indicators(symbol, 'rsi', ...) 返回 RSI 6/12/24 + 状态"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "AAPL", {},
            args=("AAPL", "rsi", "2026-05-17", 30),
        )
    assert "RSI" in result
    assert "65" in result  # RSI(6)
    assert "firm" in result


def test_get_indicators_kdj_returns_kdj_values():
    """get_indicators(symbol, 'kdj', ...) 返回 K/D/J 值"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "AAPL", {},
            args=("AAPL", "kdj", "2026-05-17", 30),
        )
    assert "KDJ" in result
    assert "75" in result and "70" in result and "85" in result


def test_get_indicators_boll_returns_band_values():
    """get_indicators(symbol, 'boll', ...) 返回布林带上/中/下轨"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "AAPL", {},
            args=("AAPL", "boll", "2026-05-17", 30),
        )
    assert "1500" in result  # upper
    assert "1460" in result  # mid
    assert "1420" in result  # lower


def test_get_indicators_no_repeat_full_csv():
    """关键:即使被调 8 次不同 indicator,内容也是 8 份精炼报告而非 8 份相同 K 线 CSV"""
    with panwatch_data_context(_full_ctx()):
        macd = _serve_from_panwatch("get_indicators", "AAPL", {}, args=("AAPL", "macd"))
        rsi = _serve_from_panwatch("get_indicators", "AAPL", {}, args=("AAPL", "rsi"))
        boll = _serve_from_panwatch("get_indicators", "AAPL", {}, args=("AAPL", "boll"))
    # 三次返回应该差异显著
    assert macd != rsi != boll
    # 每个都应小于 1k 字符(K 线 CSV 是 5k+)
    assert max(len(macd), len(rsi), len(boll)) < 1000


# ============================================================
# 3. get_news / get_global_news → 公告事件
# ============================================================

def test_get_news_returns_company_announcements():
    """get_news 返回Apple真实公告标题"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_news", "AAPL", {})
    assert "Apple Inc." in result
    assert "revenue up 12% year over year" in result or "annual shareholder meeting" in result


def test_get_global_news_with_empty_events_blocks_unrelated_news():
    """get_global_news 在没事件时返回 fallback,明确禁止 LLM 拉无关全球新闻"""
    with panwatch_data_context(_full_ctx({"events": []})):
        result = _serve_from_panwatch("get_global_news", "AAPL", {})
    assert "DO NOT pull unrelated global news" in result
    assert "AAPL" in result


# ============================================================
# 4. get_fundamentals → 真实财务摘要
# ============================================================

def test_get_fundamentals_returns_real_financial_numbers():
    """get_fundamentals 返回真实营收/净利润/ROE(而非空 fallback)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_fundamentals", "AAPL", {})
    assert "AAPL" in result
    assert "Apple Inc." in result
    # 真实财务数据
    assert "[Financial data from PanWatch (Yahoo Finance / SEC EDGAR)]" in result
    assert "Total Revenue" in result and "391" in result      # 391.04B revenue
    assert "Gross Margin (%)" in result and "46.21" in result
    assert "Do NOT invent additional numbers." in result


def test_get_fundamentals_fallback_when_no_financial():
    """没 financial 数据时降级到 quote 轻量基本面(不能是空文本)"""
    with panwatch_data_context(_full_ctx({"financial": None})):
        result = _serve_from_panwatch("get_fundamentals", "AAPL", {})
    assert "Lightweight Fundamentals" in result
    # quote 真实数据
    assert "24.5" in result  # PE
    assert "0.12" in result  # 换手率


# ============================================================
# 5. get_balance_sheet → 真实资产负债
# ============================================================

def test_get_balance_sheet_returns_real_equity_and_leverage():
    """get_balance_sheet 返回真实净资产 + 资产负债率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_balance_sheet", "AAPL", {})
    assert "Balance sheet snapshot" in result
    assert "Book Value / Share" in result and "4.38" in result
    assert "Total Debt" in result and "Total Cash" in result
    # The source has no balance sheet: it must say so rather than invent one.
    assert "Not available from this source: total assets" in result


# ============================================================
# 6. get_cashflow → 真实经营现金流
# ============================================================

def test_get_cashflow_returns_real_operating_cashflow():
    """get_cashflow 返回真实经营现金流量净额(800 亿)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "AAPL", {})
    assert "Cash flow statement (TTM)" in result
    assert "Operating Cash Flow" in result and "118" in result   # 118.25B
    assert "Free Cash Flow" in result and "108" in result        # 108.81B


def test_get_cashflow_does_not_match_capital_flow_branch():
    """关键 bug 回归:cashflow 不能被路由到"资金流"分支
    (上次 bug:method 含 'flow' 字串就误判为资金流向)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "AAPL", {})
    # The retired capital-flow branch must never catch "cashflow" (it matches on "flow").
    assert "capital flow" not in result.lower()
    assert "Cash flow statement" in result


# ============================================================
# 7. get_income_statement → 真实利润表
# ============================================================

def test_get_income_statement_returns_real_revenue_and_profit():
    """get_income_statement 返回真实营业收入 + 净利润 + 毛利率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_income_statement", "AAPL", {})
    assert "Income statement (TTM / latest reported period)" in result
    assert "Total Revenue" in result and "391" in result
    assert "Net Income" in result and "93" in result
    assert "Line items not provided by this source" in result


# ============================================================
# 8. Stock metadata header — 公司名永远在,LLM 不会瞎编
# ============================================================

def test_all_tools_include_stock_metadata_header():
    """所有工具的输出都带 [Stock Metadata] 公司名信息"""
    methods = [
        "get_stock_data", "get_news", "get_global_news",
        "get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement",
    ]
    with panwatch_data_context(_full_ctx()):
        for m in methods:
            args = ("AAPL", "macd") if m == "get_indicators" else ("AAPL",)
            result = _serve_from_panwatch(m, "AAPL", {}, args=args)
            assert "Apple Inc." in result or "AAPL" in result, f"{m} 缺少公司元信息"
            assert "Stock Metadata" in result or "Technical Indicator" in result, f"{m} 缺少 metadata header"
