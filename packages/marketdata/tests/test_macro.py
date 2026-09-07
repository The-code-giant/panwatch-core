"""MarketData.macro: one batched download with per-symbol retry, 5 min cache, missing symbols omitted."""

from __future__ import annotations

from _yf_fakes import FakeTicker, patch_yf
from marketdata import MACRO_SYMBOLS, MarketData, StaticConfigProvider


def _md() -> MarketData:
    return MarketData(config=StaticConfigProvider({}))


def test_macro_symbols_constant():
    """MACRO_SYMBOLS lists VIX, 10Y, USD/CAD, WTI, gold and the four indices in that order."""
    assert MACRO_SYMBOLS == ("^VIX", "^TNX", "CAD=X", "CL=F", "GC=F", "^GSPC", "^IXIC", "^DJI", "^GSPTSE")


def test_macro_batch_with_names_and_changes(monkeypatch):
    """Rows come from download_last with names from INDEX_NAMES and change amount/percent from prev_close."""
    calls = patch_yf(monkeypatch, download={"^VIX": {"last": 15.5, "prev_close": 15.0},
                                            "CAD=X": {"last": 1.36, "prev_close": 1.35}})
    out = _md().macro(("^VIX", "CAD=X"))
    assert calls["download"] == [["^VIX", "CAD=X"]]
    assert [r["symbol"] for r in out] == ["^VIX", "CAD=X"]
    assert out[0]["name"] == "VIX" and out[0]["change_amount"] == 0.5 and out[0]["change_pct"] == 3.33
    assert out[1]["name"] == "USD/CAD" and out[1]["current_price"] == 1.36


def test_macro_retries_missing_with_fast_quote_and_omits_failures(monkeypatch):
    """A symbol missing from the batch is retried with fast_quote; one still missing is omitted."""
    patch_yf(monkeypatch,
             download={"^VIX": {"last": 15.5, "prev_close": 15.0}},
             tickers={"GC=F": FakeTicker("GC=F", fast_info={"last_price": 2400.0, "previous_close": 2380.0}),
                      "CL=F": FakeTicker("CL=F", fast_info={})})
    out = _md().macro(("^VIX", "GC=F", "CL=F"))
    assert [r["symbol"] for r in out] == ["^VIX", "GC=F"]
    assert out[1]["name"] == "Gold" and out[1]["current_price"] == 2400.0


def test_macro_is_cached(monkeypatch):
    """A second call within 5 minutes does not download again."""
    calls = patch_yf(monkeypatch, download={"^VIX": {"last": 15.5, "prev_close": 15.0}})
    md = _md()
    md.macro(("^VIX",))
    md.macro(("^VIX",))
    assert len(calls["download"]) == 1


def test_macro_download_failure_falls_back_per_symbol(monkeypatch):
    """When the batched download raises, each symbol is still tried through fast_quote."""
    from marketdata.errors import VendorError

    def _boom(symbols, period="5d"):
        raise VendorError("down")

    patch_yf(monkeypatch, download=_boom,
             tickers={"^TNX": FakeTicker("^TNX", fast_info={"last_price": 4.1, "previous_close": 4.0})})
    out = _md().macro(("^TNX",))
    assert len(out) == 1 and out[0]["name"] == "US 10Y Yield"
