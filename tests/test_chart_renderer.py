"""matplotlib K 线渲染器(替代 Playwright 截图)单元测试。"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, timedelta

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from src.collectors import chart_renderer as cr  # noqa: E402
from src.collectors.kline_collector import KlineCollector, KlineData  # noqa: E402


def _synthetic_bars(n: int, start: date = date(2025, 1, 1)) -> list[KlineData]:
    """确定性的合成日线(工作日),带缓慢趋势与波动。"""
    rng = np.random.default_rng(42)
    bars: list[KlineData] = []
    price = 100.0
    d = start
    while len(bars) < n:
        if d.weekday() < 5:
            drift = 0.15 * np.sin(len(bars) / 17.0)
            close = max(1.0, price + drift + rng.normal(0, 1.2))
            high = max(price, close) + abs(rng.normal(0, 0.6))
            low = min(price, close) - abs(rng.normal(0, 0.6))
            bars.append(
                KlineData(
                    date=d.isoformat(),
                    open=round(price, 2),
                    close=round(close, 2),
                    high=round(high, 2),
                    low=round(max(low, 0.5), 2),
                    volume=float(int(1_000_000 + rng.integers(0, 500_000))),
                )
            )
            price = close
        d += timedelta(days=1)
    return bars


# ---------------------------------------------------------------------------
# render_png(纯函数)
# ---------------------------------------------------------------------------


def test_render_png_returns_png_bytes():
    """200 根合成 K 线 → 返回以 PNG 魔数开头的字节串。"""
    png = cr.render_png(_synthetic_bars(200), symbol="AAPL", name="Apple", period="daily")
    assert isinstance(png, (bytes, bytearray))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10_000


def test_render_png_accepts_dict_bars_and_config():
    """dict 形式的 K 线与自定义尺寸/指标配置同样可渲染。"""
    bars = [b.__dict__ for b in _synthetic_bars(60)]
    png = cr.render_png(
        bars,
        symbol="SHOP.TO",
        name="Shopify",
        period="weekly",
        config={"width": 640, "height": 480, "dpi": 72, "indicators": ["ma20", "rsi"]},
    )
    assert png is not None and png[:4] == b"\x89PNG"


def test_render_png_too_few_bars_returns_none():
    """不足 30 根 K 线返回 None(不画一张没有意义的图)。"""
    assert cr.render_png(_synthetic_bars(20), symbol="AAPL", name="Apple", period="daily") is None
    assert cr.render_png([], symbol="AAPL", name="Apple", period="daily") is None


def test_render_png_skips_corrupt_bars():
    """含非数值/NaN 的坏行被跳过,其余照常渲染。"""
    bars: list = list(_synthetic_bars(40))
    bars.insert(5, {"date": "x", "open": None, "high": 1, "low": 1, "close": 1, "volume": 1})
    bars.insert(9, {"date": "y", "open": float("nan"), "high": 1, "low": 1, "close": 1, "volume": 1})
    png = cr.render_png(bars, symbol="T", name="T", period="daily")
    assert png is not None


# ---------------------------------------------------------------------------
# 指标辅助函数
# ---------------------------------------------------------------------------


def test_indicator_helpers_shapes_and_values():
    """_sma/_ema/_rsi/_macd/_boll 输出与输入等长,并满足基本数值性质。"""
    x = np.array([float(i) for i in range(1, 61)])
    sma = cr._sma(x, 20)
    assert sma.shape == x.shape
    assert np.isnan(sma[18]) and np.isclose(sma[19], np.mean(x[:20]))
    ema = cr._ema(x, 12)
    assert ema.shape == x.shape and not np.isnan(ema).any()
    rsi = cr._rsi(x, 14)
    assert rsi.shape == x.shape and np.isnan(rsi[13]) and np.isclose(rsi[-1], 100.0)
    macd, sig, hist = cr._macd(x)
    assert macd.shape == sig.shape == hist.shape == x.shape
    assert np.allclose(hist, macd - sig, equal_nan=True)
    mid, up, lo = cr._boll(x, 20, 2.0)
    assert np.allclose(mid[19:], sma[19:])
    assert np.all(up[19:] >= mid[19:]) and np.all(lo[19:] <= mid[19:])


# ---------------------------------------------------------------------------
# 重采样
# ---------------------------------------------------------------------------


def test_weekly_resample_reduces_bar_count():
    """周线重采样(W-FRI)后根数约为日线的 1/5,月线更少,且 OHLC 语义正确。"""
    daily = _synthetic_bars(250)
    weekly = cr.resample_bars(daily, "weekly")
    monthly = cr.resample_bars(daily, "monthly")
    assert 45 <= len(weekly) <= 55
    assert 10 <= len(monthly) <= 13
    assert len(monthly) < len(weekly) < len(daily)
    # 第一根周线:open 取该周首日 open,high 为该周最高
    first_week_days = [b for b in daily if date.fromisoformat(b.date) <= date(2025, 1, 3)]
    assert weekly[0]["open"] == first_week_days[0].open
    assert weekly[0]["high"] == max(b.high for b in first_week_days)
    assert weekly[0]["close"] == first_week_days[-1].close
    assert weekly[0]["volume"] == sum(b.volume for b in first_week_days)
    # 日线原样返回
    assert len(cr.resample_bars(daily, "daily")) == len(daily)


# ---------------------------------------------------------------------------
# ChartRenderer.capture / capture_batch / cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    """把 CHART_DIR 指向临时目录。"""
    target = tmp_path / "charts"
    monkeypatch.setattr(cr, "CHART_DIR", target)
    return target


@pytest.fixture
def fake_klines(monkeypatch):
    """monkeypatch KlineCollector.get_klines,返回合成日线并记录调用参数。"""
    calls: list[tuple[str, int]] = []

    def _fake(self, symbol, days=60):
        calls.append((symbol, days))
        # 1200 business days ~ 57 months, so the monthly resample still clears MIN_BARS.
        return _synthetic_bars(min(days, 1200))

    monkeypatch.setattr(KlineCollector, "get_klines", _fake)
    return calls


def test_capture_writes_png_file(chart_dir, fake_klines):
    """capture 走 KlineCollector 拉数并把 PNG 写入 CHART_DIR,文件名含周期与时间戳。"""
    renderer = cr.ChartRenderer(config={"width": 800, "height": 600})
    image = asyncio.run(renderer.capture("BRK.B", "Berkshire", market="US"))
    assert image is not None
    assert isinstance(image, cr.ChartImage)
    assert image.exists
    assert image.symbol == "BRK.B" and image.market == "US" and image.period == "daily"
    assert os.path.dirname(image.filepath) == str(chart_dir)
    assert os.path.basename(image.filepath).startswith("BRK_B_daily_")
    assert image.filepath.endswith(".png")
    with open(image.filepath, "rb") as fh:
        assert fh.read(4) == b"\x89PNG"
    assert fake_klines == [("BRK.B", 220)]
    asyncio.run(renderer.close())


def test_capture_weekly_requests_more_history(chart_dir, fake_klines):
    """周线请求 900 天历史,月线 3000 天。"""
    renderer = cr.ChartRenderer()
    weekly = asyncio.run(renderer.capture("AAPL", "Apple", market="US", period="weekly"))
    monthly = asyncio.run(renderer.capture("AAPL", "Apple", market="US", period="monthly"))
    assert weekly is not None and weekly.period == "weekly"
    assert monthly is not None and monthly.period == "monthly"
    assert fake_klines == [("AAPL", 900), ("AAPL", 3000)]


def test_capture_returns_none_on_short_history(chart_dir, monkeypatch):
    """历史不足 30 根时返回 None 且不写文件。"""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _synthetic_bars(10))
    renderer = cr.ChartRenderer()
    assert asyncio.run(renderer.capture("NEW", "New Listing", market="US")) is None
    assert not chart_dir.exists() or list(chart_dir.glob("*.png")) == []


def test_capture_returns_none_on_fetch_error(chart_dir, monkeypatch):
    """数据源抛异常时返回 None,不向上传播。"""

    def _boom(self, symbol, days=60):
        raise RuntimeError("source down")

    monkeypatch.setattr(KlineCollector, "get_klines", _boom)
    renderer = cr.ChartRenderer()
    assert asyncio.run(renderer.capture("AAPL", "Apple", market="US")) is None


def test_capture_batch_accepts_dicts_and_objects(chart_dir, fake_klines):
    """capture_batch 同时接受 dict 与带属性的对象(market 可为 MarketCode)。"""
    from src.models.market import MarketCode

    class _Stock:
        symbol = "SHOP.TO"
        name = "Shopify"
        market = MarketCode.CA

    renderer = cr.ChartRenderer()
    images = asyncio.run(
        renderer.capture_batch(
            [{"symbol": "AAPL", "name": "Apple", "market": "US"}, _Stock(), {"symbol": ""}],
            period="daily",
            provider="tickerkeep",
        )
    )
    assert [i.symbol for i in images] == ["AAPL", "SHOP.TO"]
    assert images[1].market == "CA"
    assert all(i.exists for i in images)


def test_cleanup_old_screenshots_removes_stale_files(chart_dir):
    """cleanup_old_screenshots 只删除超过 max_age_hours 的 PNG。"""
    chart_dir.mkdir(parents=True)
    old = chart_dir / "OLD_daily_20200101_000000.png"
    fresh = chart_dir / "NEW_daily_20990101_000000.png"
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    stale = time.time() - 48 * 3600
    os.utime(old, (stale, stale))

    removed = cr.ChartRenderer().cleanup_old_screenshots(max_age_hours=24)
    assert removed == 1
    assert not old.exists() and fresh.exists()


def test_cleanup_handles_missing_dir(chart_dir):
    """目录不存在时 cleanup 静默返回 0。"""
    assert not chart_dir.exists()
    assert cr.ChartRenderer().cleanup_old_screenshots() == 0


def test_chart_screenshot_alias_and_default_config():
    """ChartScreenshot 是 ChartImage 的别名;默认配置与传入配置合并。"""
    assert cr.ChartScreenshot is cr.ChartImage
    renderer = cr.ChartRenderer(config={"bars": 90})
    assert renderer.config["bars"] == 90
    assert renderer.config["width"] == 1280 and renderer.config["dpi"] == 100
    assert renderer._plan("daily") == (220, 90)
    assert renderer._plan("weekly") == (900, 150)
    assert renderer._plan("monthly") == (3000, 120)
