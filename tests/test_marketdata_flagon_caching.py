"""marketdata 包接入下,外层 K 线缓存/冷却机制仍应生效。

kline_collector._fetch_all_sources 内层取数走 marketdata 包(get_market_data().klines()),
但外层的 _KLINE_CACHE/_get_fetch_lock/_FAIL_UNTIL 与取数路径无关——不管里面换了哪条
路径,都不应重复联网。这里 mock 包层,验证外层机制在新路径下依然成立。
"""

from __future__ import annotations

from src.collectors import kline_collector as kc
from src.models.market import MarketCode

from marketdata.types import Bar


def _mk_bars(n: int) -> list[Bar]:
    """造 n 根 marketdata.types.Bar,供假包层返回。"""
    return [
        Bar(date=f"2026-01-{(i % 28) + 1:02d}", open=10.0, close=10.0, high=11.0, low=9.0, volume=100.0 + i)
        for i in range(n)
    ]


class _FakeMarketData:
    """假的 marketdata.MarketData,只实现 klines(),记录调用次数。"""

    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.calls = 0

    def klines(self, symbol: str, market: str, days: int, min_count: int) -> list[Bar]:
        self.calls += 1
        return list(self.bars)


def test_flagon_kline_cache_within_ttl(monkeypatch):
    """走 marketdata 包下,TTL 内第二次取数仍应命中 _KLINE_CACHE,不重复调用包。"""
    fake = _FakeMarketData(_mk_bars(30))
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.US)
    out1 = col.get_klines("AAPL", days=20)
    out2 = col.get_klines("AAPL", days=20)

    assert fake.calls == 1, f"第二次应命中缓存,实际调用包 {fake.calls} 次"
    assert len(out1) == 20
    assert len(out2) == 20


def test_flagon_kline_insufficient_bars_triggers_cooldown(monkeypatch):
    """包返回条数不足 need 时应固化失败冷却,冷却窗口内不再调用包。"""
    fake = _FakeMarketData(_mk_bars(5))
    monkeypatch.setattr(kc, "get_market_data", lambda: fake)

    col = kc.KlineCollector(MarketCode.US)
    out1 = col.get_klines("AAPL", days=100)  # 只拿到 5 < need(100) → 冷却
    out2 = col.get_klines("AAPL", days=100)  # 冷却窗口内,应直接服务缓存,不再调用包

    assert fake.calls == 1, f"冷却窗口内不应重复调用包,实际 {fake.calls} 次"
    assert len(out1) == 5
    assert len(out2) == 5
