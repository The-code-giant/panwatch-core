"""指数取数(K线 + market.py /indices)路由测试"""
import asyncio

import src.collectors.kline_collector as kc
import src.web.api.market as mkt


def test_get_index_klines_uses_marketdata(monkeypatch):
    """get_index_klines 走 md.index_klines(同一 INDEX_SECID 语义),转换为 KlineData。"""
    from marketdata.types import Bar

    captured: dict = {}

    class _MD:
        def index_klines(self, code, *, market, days):
            captured["code"] = code
            captured["market"] = market
            captured["days"] = days
            return [Bar(date="2026-07-01", open=3180.0, close=3200.0, high=3210.0, low=3170.0, volume=1e8)]

    monkeypatch.setattr(kc, "get_market_data", lambda: _MD())

    out = kc.get_index_klines("000001", kc.MarketCode.CN, days=120)

    assert captured == {"code": "000001", "market": "CN", "days": 120}
    assert len(out) == 1 and isinstance(out[0], kc.KlineData)
    assert out[0].date == "2026-07-01" and out[0].close == 3200.0


def test_get_market_indices_uses_marketdata(monkeypatch):
    """/indices: Yahoo is asked for all four indices first; Tencent only fills US rows Yahoo missed."""
    mkt.clear_indices_cache()
    captured: dict = {}

    class _MD:
        def index_quotes(self, tencent_symbols):
            captured["symbols"] = list(tencent_symbols)
            return [
                {
                    "symbol": ".INX",
                    "name": "S&P 500",
                    "current_price": 7718.6,
                    "change_pct": -0.38,
                    "change_amount": -29.11,
                    "prev_close": 7747.71,
                },
            ]

        def yahoo_index_quotes(self, yahoo_symbols):
            captured["yahoo"] = list(yahoo_symbols)
            return [
                {
                    "symbol": "^GSPC",
                    "name": "S&P 500",
                    "current_price": 7718.6,
                    "change_pct": -0.38,
                    "change_amount": -29.11,
                    "prev_close": 7747.71,
                },
                {
                    "symbol": "^GSPTSE",
                    "name": "S&P/TSX Composite index",
                    "current_price": 36513.8,
                    "change_pct": -0.33,
                    "change_amount": -119.3,
                    "prev_close": 36633.1,
                },
            ]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    # spark 取数不是本用例关注点,桩掉避免真实联网(见 test_market_indices_spark.py 专测 spark)。
    monkeypatch.setattr(mkt, "get_index_klines", lambda *a, **k: [])

    out = asyncio.run(mkt.get_market_indices())

    # Yahoo is asked for every index up front...
    assert captured["yahoo"] == ["^GSPC", "^IXIC", "^DJI", "^GSPTSE"]
    # ...and Tencent is only asked for the US rows Yahoo did not answer (IXIC, DJI).
    assert captured["symbols"] == ["usIXIC", "usDJI"]
    assert [i["symbol"] for i in out] == ["INX", "IXIC", "DJI", "GSPTSE"]
    sp = next(i for i in out if i["symbol"] == "INX")
    assert sp["current_price"] == 7718.6 and sp["change_pct"] == -0.38 and sp["market"] == "US"
    tsx = next(i for i in out if i["symbol"] == "GSPTSE")
    assert tsx["current_price"] == 36513.8 and tsx["market"] == "CA"
    # 未命中行情的指数仍返回基本信息占位(current_price=None),匹配逻辑不变
    dji = next(i for i in out if i["symbol"] == "DJI")
    assert dji["current_price"] is None
    assert not any(i["market"] in ("CN", "HK") for i in out)
