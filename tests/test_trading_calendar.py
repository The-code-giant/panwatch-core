"""交易日历(exchange_calendars + 固定休市表降级)与非交易日通知守卫单元测试。"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.core import trading_calendar as tc
from src.models.market import MARKETS, MarketCode


@pytest.fixture(autouse=True)
def _reset_calendar():
    """每个用例前后清空日历缓存,避免互相污染。"""
    tc.reset_cache()
    yield
    tc.reset_cache()


@pytest.fixture
def no_xcals(monkeypatch):
    """模拟未安装 exchange_calendars(import 抛 ImportError)。"""
    monkeypatch.setitem(sys.modules, "exchange_calendars", None)
    tc.reset_cache()
    yield
    tc.reset_cache()


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


def test_周末不是交易日_无需日历():
    """周末即使没有日历也判为非交易日(零依赖、永远准确)。"""
    assert tc.is_trading_day(MarketCode.US, date(2026, 8, 8)) is False  # 周六
    assert tc.is_trading_day(MarketCode.US, date(2026, 8, 9)) is False  # 周日
    assert tc.is_trading_day(MarketCode.CA, date(2026, 8, 8)) is False


def test_加密与黄金全天候():
    """CRYPTO/GOLD 周末与节假日都开市。"""
    assert tc.is_trading_day(MarketCode.CRYPTO, date(2026, 8, 8)) is True
    assert tc.is_trading_day(MarketCode.GOLD, date(2026, 12, 25)) is True


def test_us_ca_holidays_from_exchange_calendar():
    """US/CA 节假日由 exchange_calendars(XNYS/XTSE)识别,两市场互不串扰。"""
    pytest.importorskip("exchange_calendars")
    # Independence Day observed (Fri 2026-07-03): NYSE closed, TSX open
    assert tc.is_trading_day(MarketCode.US, date(2026, 7, 3)) is False
    assert tc.is_trading_day(MarketCode.CA, date(2026, 7, 3)) is True
    # Canada Day (Wed 2026-07-01): TSX closed, NYSE open
    assert tc.is_trading_day(MarketCode.CA, date(2026, 7, 1)) is False
    assert tc.is_trading_day(MarketCode.US, date(2026, 7, 1)) is True
    # Shared holiday (Good Friday 2026-04-03) closes both; a plain weekday is open on both
    assert tc.is_trading_day("US", date(2026, 4, 3)) is False
    assert tc.is_trading_day("CA", date(2026, 4, 3)) is False
    assert tc.is_trading_day("US", date(2026, 4, 6)) is True
    assert tc.is_trading_day("CA", date(2026, 4, 6)) is True
    # 日历确实被构建并缓存
    assert set(tc._CALENDARS) == {"US", "CA"}


def test_日历缓存按市场复用():
    """同一市场重复查询复用已构建的日历对象,不重复构建。"""
    pytest.importorskip("exchange_calendars")
    first = tc._get_calendar("US", date(2026, 3, 2))
    again = tc._get_calendar(MarketCode.US, date(2026, 9, 1))
    assert first is again
    assert tc._CAL_WINDOWS["US"] == (2025, 2027)


def test_超出窗口时按查询日期重建():
    """查询日期落在缓存窗口之外时,围绕该日期重建日历而不是报错。"""
    pytest.importorskip("exchange_calendars")
    tc._get_calendar("US", date(2026, 3, 2))
    assert tc.is_trading_day("US", date(2030, 1, 2)) is True  # 周三
    assert tc._CAL_WINDOWS["US"] == (2029, 2031)


def test_fallback_when_exchange_calendars_missing(no_xcals, caplog):
    """未安装 exchange_calendars 时降级为固定休市表,并只记一次告警。"""
    caplog.set_level("WARNING", logger="src.core.trading_calendar")
    assert tc._get_calendar("US", date(2026, 7, 3)) is None
    # 固定表仍能识别 NYSE/TSX 节假日
    assert tc.is_trading_day(MarketCode.US, date(2026, 7, 3)) is False
    assert tc.is_trading_day(MarketCode.CA, date(2026, 7, 3)) is True
    assert tc.is_trading_day(MarketCode.CA, date(2026, 7, 1)) is False
    assert tc.is_trading_day(MarketCode.US, date(2026, 7, 1)) is True
    assert tc.is_trading_day("US", date(2026, 4, 6)) is True
    # 周末照样拦住
    assert tc.is_trading_day("US", date(2026, 8, 8)) is False
    # 固定表未覆盖的年份:只判周末(宁可多跑)
    assert tc.is_trading_day("US", date(2030, 1, 1)) is True  # 周二,元旦但表里没有
    assert tc._CALENDARS == {}
    warnings = [r for r in caplog.records if "exchange_calendars not installed" in r.getMessage()]
    assert len(warnings) == 1


def test_refresh_blocking_builds_both_calendars():
    """refresh_blocking() 预热 US 与 CA 两份日历;reset_cache() 清空。"""
    pytest.importorskip("exchange_calendars")
    assert tc.refresh_blocking() is True
    assert set(tc._CALENDARS) == {"US", "CA"}
    tc.reset_cache()
    assert tc._CALENDARS == {}
    assert tc._CAL_WINDOWS == {}


def test_refresh_blocking_false_when_missing(no_xcals):
    """没有 exchange_calendars 时 refresh 返回 False 但不抛异常。"""
    assert tc.refresh_blocking() is False
    assert tc._CALENDARS == {}


def test_异步刷新不阻塞():
    """refresh() 走 to_thread,结果与同步版一致。"""
    pytest.importorskip("exchange_calendars")
    assert asyncio.run(tc.refresh()) is True
    assert "US" in tc._CALENDARS


def test_接受字符串市场码与datetime():
    """market 接受字符串,日期接受 datetime(按市场时区归到当地日)。"""
    # 2026-07-04 02:00 UTC 在纽约还是 7/3 22:00 → Independence Day (observed) 休市
    dt = datetime(2026, 7, 4, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert tc.is_trading_day("US", dt) is False
    # naive datetime 直接取日期
    assert tc.is_trading_day("US", datetime(2026, 7, 6, 10, 0)) is True


def test_any_market_trading_day():
    """周末所有市场全休 → False;工作日至少一个已启用市场开市 → True。"""
    assert tc.any_market_trading_day(date(2026, 8, 8)) is False  # 周六
    assert tc.any_market_trading_day(date(2026, 8, 10)) is True  # 周一
    # Canada Day: TSX closed but NYSE open → still True
    assert tc.any_market_trading_day(date(2026, 7, 1)) is True
    # Good Friday closes both enabled markets → False
    assert tc.any_market_trading_day(date(2026, 4, 3)) is False


# ---------------------------------------------------------------------------
# session_close / next_trading_day / previous_trading_day
# ---------------------------------------------------------------------------


def test_session_close_early_close():
    """感恩节次日(2026-11-27)NYSE 13:00 提前收盘;普通交易日 16:00;休市日 None。"""
    pytest.importorskip("exchange_calendars")
    early = tc.session_close("US", date(2026, 11, 27))
    assert early is not None
    assert early.tzinfo is not None
    assert early.utcoffset() == datetime(2026, 11, 27, tzinfo=ZoneInfo("America/New_York")).utcoffset()
    assert (early.hour, early.minute) == (13, 0)
    assert early.date() == date(2026, 11, 27)

    normal = tc.session_close("US", date(2026, 11, 30))
    assert normal is not None and (normal.hour, normal.minute) == (16, 0)

    assert tc.session_close("US", date(2026, 11, 26)) is None  # Thanksgiving
    assert tc.session_close("US", date(2026, 11, 28)) is None  # Saturday
    assert tc.session_close("CA", date(2026, 7, 1)) is None  # Canada Day


def test_session_close_fallback_without_calendar(no_xcals):
    """无日历时退回市场配置的最后一个时段结束时间;休市日仍为 None。"""
    close = tc.session_close("US", date(2026, 11, 30))
    expected_end = max(s.end for s in MARKETS[MarketCode.US].sessions)
    assert close is not None
    assert close.time() == expected_end
    assert close.tzinfo is not None
    assert tc.session_close("US", date(2026, 11, 26)) is None


def test_next_and_previous_trading_day():
    """next/previous 严格跳过周末与节假日,且跨市场各自使用自己的日历。"""
    pytest.importorskip("exchange_calendars")
    # 2026-07-02 (Thu) → 7/3 NYSE closed, weekend → next is Mon 7/6
    assert tc.next_trading_day("US", date(2026, 7, 2)) == date(2026, 7, 6)
    # TSX is open on 7/3
    assert tc.next_trading_day("CA", date(2026, 7, 2)) == date(2026, 7, 3)
    # Canada Day: CA skips 7/1, US doesn't
    assert tc.next_trading_day("CA", date(2026, 6, 30)) == date(2026, 7, 2)
    assert tc.next_trading_day("US", date(2026, 6, 30)) == date(2026, 7, 1)
    # previous: Mon 7/6 → US Thu 7/2, CA Fri 7/3
    assert tc.previous_trading_day("US", date(2026, 7, 6)) == date(2026, 7, 2)
    assert tc.previous_trading_day("CA", date(2026, 7, 6)) == date(2026, 7, 3)
    # From a weekend
    assert tc.next_trading_day("US", date(2026, 8, 8)) == date(2026, 8, 10)
    assert tc.previous_trading_day("US", date(2026, 8, 9)) == date(2026, 8, 7)
    # Thanksgiving week
    assert tc.next_trading_day("US", date(2026, 11, 25)) == date(2026, 11, 27)
    assert tc.previous_trading_day("US", date(2026, 11, 27)) == date(2026, 11, 25)


def test_next_previous_fallback_without_calendar(no_xcals):
    """无日历时逐日游走,依然跳过周末与固定表节假日。"""
    assert tc.next_trading_day("US", date(2026, 7, 2)) == date(2026, 7, 6)
    assert tc.next_trading_day("CA", date(2026, 6, 30)) == date(2026, 7, 2)
    assert tc.previous_trading_day("US", date(2026, 7, 6)) == date(2026, 7, 2)
    assert tc.next_trading_day(MarketCode.CRYPTO, date(2026, 8, 8)) == date(2026, 8, 9)


# ---------------------------------------------------------------------------
# is_trading_time 复用交易日历(一处修复,全线受益)
# ---------------------------------------------------------------------------


def test_交易时段判断在节假日返回False():
    """Independence Day (observed) 的 10:00 处在时段区间内,但不是交易日 → 非交易时间。"""
    md = MARKETS[MarketCode.US]
    holiday_10am = datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert md.is_trading_time(holiday_10am) is False


def test_交易时段判断在正常交易日返回True():
    """交易日 10:00 在时段内 → 交易中。"""
    md = MARKETS[MarketCode.US]
    trading_10am = datetime(2026, 7, 6, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    assert md.is_trading_time(trading_10am) is True


def test_交易日的非时段时间返回False():
    """交易日的 08:00 不在时段内 → 非交易时间。"""
    md = MARKETS[MarketCode.US]
    before_open = datetime(2026, 7, 6, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    assert md.is_trading_time(before_open) is False


# ---------------------------------------------------------------------------
# 模拟盘定时通知的非交易日守卫(用户报告的 bug)
# ---------------------------------------------------------------------------


def _patch_notifiers(monkeypatch) -> dict[str, int]:
    """把两个通知函数替换成计数器,用于断言是否被调用。"""
    calls = {"premarket": 0, "summary": 0}

    async def _fake_premarket():
        calls["premarket"] += 1

    async def _fake_summary():
        calls["summary"] += 1

    monkeypatch.setattr(
        "src.core.paper_trading_notifier.send_premarket_plan", _fake_premarket
    )
    monkeypatch.setattr(
        "src.core.paper_trading_notifier.send_daily_summary", _fake_summary
    )
    return calls


def test_周末不发盘前计划和日终摘要(monkeypatch):
    """周末两条模拟盘定时通知都必须跳过 —— 这是用户报告的 bug。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    saturday = datetime(2026, 8, 8, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = PaperTradingScheduler(timezone="America/New_York")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_全市场节假日不发盘前计划和日终摘要(monkeypatch):
    """Good Friday(US/CA 同时休市)同样跳过。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    holiday = datetime(2026, 4, 3, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: holiday)

    sched = PaperTradingScheduler(timezone="America/New_York")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_交易日照常发盘前计划和日终摘要(monkeypatch):
    """交易日不受守卫影响,通知照常发送。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    monday = datetime(2026, 8, 10, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = PaperTradingScheduler(timezone="America/New_York")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 1, "summary": 1}


# ---------------------------------------------------------------------------
# 机会刷新的非交易日守卫(周末重算全市场只是白烧资源)
# ---------------------------------------------------------------------------


def test_周末跳过机会刷新(monkeypatch):
    """周末不重算机会池 —— 行情没变,扫全市场纯属浪费。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 0}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="America/New_York")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 0


def test_交易日照常刷新机会(monkeypatch):
    """交易日机会刷新不受守卫影响。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 3, "snapshot_date": "2026-08-10"}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    monday = datetime(2026, 8, 10, 9, 15, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = ContextMaintenanceScheduler(timezone="America/New_York")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 1


def test_手动刷新机会不受非交易日守卫影响(monkeypatch):
    """手动触发是用户显式意图,周末也必须能跑。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 1}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="America/New_York")
    asyncio.run(sched.refresh_opportunities_once())

    assert calls["n"] == 1
