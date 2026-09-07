"""CAD→USD 汇率:Bank of Canada Valet 优先,失败退 macro(CAD=X 取倒数),再退常量。"""

from __future__ import annotations

import pytest

from src.web.api import accounts


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """每个用例从空缓存开始。"""
    monkeypatch.setattr(accounts, "_cad_usd_rate_cache", {"rate": accounts._CAD_USD_FALLBACK, "ts": 0})


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _boc_payload(v: str):
    return {"observations": [{"d": "2026-09-04", "FXUSDCAD": {"v": v}}]}


def test_valet_success_inverts_usdcad(monkeypatch):
    """Valet 返回 FXUSDCAD(每美元多少加元)时取倒数作为 CAD→USD,并写入缓存。"""
    captured: dict = {}

    def fake_get(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp(_boc_payload("1.3700"))

    monkeypatch.setattr(accounts.httpx, "get", fake_get)

    def _macro_boom(*a, **k):
        raise AssertionError("macro fallback must not be called when Valet succeeds")

    monkeypatch.setattr(accounts, "_fetch_cad_usd_macro", _macro_boom)

    rate = accounts.get_cad_usd_rate()
    assert rate == pytest.approx(1 / 1.37)
    assert "bankofcanada.ca/valet/observations/FXUSDCAD/json" in captured["url"]
    assert captured["params"] == {"recent": 1}
    assert accounts._cad_usd_rate_cache["rate"] == pytest.approx(1 / 1.37)
    assert accounts._cad_usd_rate_cache["ts"] > 0
    assert not hasattr(accounts, "_YAHOO_FX_URL")


def test_valet_failure_falls_back_to_macro(monkeypatch):
    """Valet 抛错时退到 get_market_data().macro(("CAD=X",)),同样取倒数。"""

    def _boom(*a, **k):
        raise RuntimeError("valet offline")

    monkeypatch.setattr(accounts.httpx, "get", _boom)
    captured: dict = {}

    class _MD:
        def macro(self, symbols):
            captured["symbols"] = tuple(symbols)
            return [{"symbol": "CAD=X", "name": "USD/CAD", "current_price": 1.25, "prev_close": 1.24,
                     "change_amount": 0.01, "change_pct": 0.8}]

    import src.core.marketdata_client as mc
    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())

    rate = accounts.get_cad_usd_rate()
    assert captured["symbols"] == ("CAD=X",)
    assert rate == pytest.approx(0.8)
    assert accounts._cad_usd_rate_cache["rate"] == pytest.approx(0.8)


def test_valet_out_of_band_falls_back_to_macro(monkeypatch):
    """Valet 结果超出 0.3-1.5 合理区间时视为无效,退到 macro。"""
    monkeypatch.setattr(accounts.httpx, "get", lambda url, **kw: _Resp(_boc_payload("0.01")))

    class _MD:
        def macro(self, symbols):
            return [{"symbol": "CAD=X", "current_price": 1.40}]

    import src.core.marketdata_client as mc
    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())
    assert accounts.get_cad_usd_rate() == pytest.approx(1 / 1.40)


def test_both_sources_fail_uses_constant_and_backs_off(monkeypatch):
    """Valet 与 macro 都失败时返回常量,并记录退避时间戳(不会每次估值都重试)。"""

    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(accounts.httpx, "get", _boom)

    class _MD:
        def macro(self, symbols):
            raise RuntimeError("yahoo offline")

    import src.core.marketdata_client as mc
    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())

    rate = accounts.get_cad_usd_rate()
    assert rate == accounts._CAD_USD_FALLBACK
    ts = accounts._cad_usd_rate_cache["ts"]
    assert ts > 0

    # Within the backoff window the sources are not hit again.
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        raise RuntimeError("offline")

    monkeypatch.setattr(accounts.httpx, "get", _count)
    assert accounts.get_cad_usd_rate() == accounts._CAD_USD_FALLBACK
    assert calls["n"] == 0


def test_macro_missing_symbol_uses_constant(monkeypatch):
    """macro 未返回 CAD=X 行(缺失符号被省略)时同样退到常量。"""
    monkeypatch.setattr(accounts.httpx, "get", lambda url, **kw: _Resp({"observations": []}))

    class _MD:
        def macro(self, symbols):
            return []

    import src.core.marketdata_client as mc
    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())
    assert accounts.get_cad_usd_rate() == accounts._CAD_USD_FALLBACK
