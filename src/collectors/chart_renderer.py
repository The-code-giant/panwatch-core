"""Candlestick chart renderer (matplotlib, headless).

Replaces the Playwright quote-site screenshot collector. Bars come from
``KlineCollector`` (the same source every other consumer uses), the image is
drawn locally with the Agg backend, and the resulting PNG is handed to the
multimodal chart-analyst agent exactly like the old screenshot was.

Layout (top to bottom, height ratios 5 : 1.2 : 1.2 : 1.2):
    1. candles + MA20 / MA50 / Bollinger(20, 2)
    2. volume, coloured by candle direction
    3. RSI(14) with 30 / 70 guides
    4. MACD(12, 26, 9) line / signal / histogram

``render_png`` is pure (no clock, no I/O) so it can be unit-tested with
synthetic bars; ``capture`` does the fetching, file naming and writing.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from src.models.market import MarketCode, default_market

logger = logging.getLogger(__name__)

# Where rendered charts land. Module-level so tests can monkeypatch it.
CHART_DIR = Path(tempfile.gettempdir()) / "tickerkeep_charts"

DEFAULT_CONFIG: dict[str, Any] = {
    "bars": 160,
    "width": 1280,
    "height": 900,
    "indicators": ["ma20", "ma50", "boll", "volume", "rsi", "macd"],
    "dpi": 100,
}

# period -> (days to request from KlineCollector, bars to show after resampling)
_PERIOD_PLAN: dict[str, tuple[int, int]] = {
    "daily": (220, 160),
    "weekly": (900, 150),
    "monthly": (3000, 120),
}

MIN_BARS = 30

COLOR_UP = "#16a34a"
COLOR_DOWN = "#dc2626"
_COLOR_MA20 = "#f59e0b"
_COLOR_MA50 = "#3b82f6"
_COLOR_BOLL = "#8b5cf6"
_COLOR_RSI = "#0ea5e9"
_COLOR_MACD = "#2563eb"
_COLOR_SIGNAL = "#f97316"
_COLOR_GRID = "#e5e7eb"
_COLOR_TEXT = "#374151"


@dataclass
class ChartImage:
    """A rendered chart on disk."""

    symbol: str
    name: str
    market: str
    filepath: str
    period: str = "daily"  # daily / weekly / monthly
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def exists(self) -> bool:
        return os.path.exists(self.filepath)


# Backwards-compatible name used by the chart-analyst agent.
ChartScreenshot = ChartImage


# ── numpy indicator helpers ─────────────────────────────────────────────────


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    """Simple moving average; NaN until ``n`` samples are available."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or x.size < n:
        return out
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (csum[n:] - csum[:-n]) / n
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    """Exponential moving average seeded with the first sample."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if n <= 0 or x.size == 0:
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder RSI; NaN for the first ``n`` samples."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    if x.size <= n:
        return out
    delta = np.diff(x)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:n].mean()
    avg_loss = loss[:n].mean()

    def _rs_to_rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[n] = _rs_to_rsi(avg_gain, avg_loss)
    for i in range(n + 1, x.size):
        avg_gain = (avg_gain * (n - 1) + gain[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + loss[i - 1]) / n
        out[i] = _rs_to_rsi(avg_gain, avg_loss)
    return out


def _macd(
    x: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD line, signal line and histogram."""
    x = np.asarray(x, dtype=float)
    macd = _ema(x, fast) - _ema(x, slow)
    sig = _ema(macd, signal)
    return macd, sig, macd - sig


def _boll(
    x: np.ndarray, n: int = 20, k: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger bands: (middle, upper, lower)."""
    x = np.asarray(x, dtype=float)
    mid = _sma(x, n)
    std = np.full(x.shape, np.nan)
    if x.size >= n:
        windows = np.lib.stride_tricks.sliding_window_view(x, n)
        std[n - 1:] = windows.std(axis=1)
    return mid, mid + k * std, mid - k * std


# ── bar normalisation / resampling ──────────────────────────────────────────


def _bar_get(bar: Any, key: str, default: Any = None) -> Any:
    if isinstance(bar, dict):
        return bar.get(key, default)
    return getattr(bar, key, default)


def _to_arrays(bars: Iterable[Any]) -> dict[str, np.ndarray]:
    """Turn KlineData objects / dicts into float arrays plus date labels."""
    dates: list[str] = []
    o: list[float] = []
    h: list[float] = []
    l: list[float] = []
    c: list[float] = []
    v: list[float] = []
    for bar in bars:
        try:
            oo = float(_bar_get(bar, "open"))
            hh = float(_bar_get(bar, "high"))
            ll = float(_bar_get(bar, "low"))
            cc = float(_bar_get(bar, "close"))
        except (TypeError, ValueError):
            continue
        if not all(np.isfinite([oo, hh, ll, cc])):
            continue
        vol = _bar_get(bar, "volume", 0.0)
        try:
            vol = float(vol or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        dates.append(str(_bar_get(bar, "date", "")))
        o.append(oo)
        h.append(hh)
        l.append(ll)
        c.append(cc)
        v.append(vol)
    return {
        "date": np.asarray(dates, dtype=object),
        "open": np.asarray(o, dtype=float),
        "high": np.asarray(h, dtype=float),
        "low": np.asarray(l, dtype=float),
        "close": np.asarray(c, dtype=float),
        "volume": np.asarray(v, dtype=float),
    }


def resample_bars(bars: list[Any], period: str) -> list[dict[str, Any]]:
    """Aggregate daily bars into weekly (W-FRI) or month-end bars.

    ``daily`` (or anything unknown) returns the bars unchanged as dicts.
    """
    arrays = _to_arrays(bars)
    n = arrays["close"].size
    if n == 0:
        return []
    rows = [
        {
            "date": arrays["date"][i],
            "open": arrays["open"][i],
            "high": arrays["high"][i],
            "low": arrays["low"][i],
            "close": arrays["close"][i],
            "volume": arrays["volume"][i],
        }
        for i in range(n)
    ]
    if period not in ("weekly", "monthly"):
        return rows

    import pandas as pd

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    if df.empty:
        return []
    rule = "W-FRI" if period == "weekly" else "ME"
    agg = (
        df.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "close"])
    )
    out: list[dict[str, Any]] = []
    for ts, row in agg.iterrows():
        out.append(
            {
                "date": ts.strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return out


# ── matplotlib ──────────────────────────────────────────────────────────────


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _load_mpl():
    """Import matplotlib with a writable config dir and the Agg backend.

    ``MPLCONFIGDIR`` must be set before the first import, otherwise matplotlib
    probes ``~/.config`` (read-only inside the container) and falls back to a
    fresh temp dir on every process start, rebuilding the font cache each time.
    """
    if not os.environ.get("MPLCONFIGDIR"):
        mpl_dir = Path(_data_dir()) / "mpl"
        try:
            mpl_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(mpl_dir)
        except OSError:
            pass  # matplotlib will pick its own temp dir
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    return matplotlib, plt


def _style_axis(ax) -> None:
    ax.grid(True, color=_COLOR_GRID, linewidth=0.6)
    ax.tick_params(colors=_COLOR_TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(_COLOR_GRID)


def render_png(
    bars: list[Any],
    *,
    symbol: str,
    name: str,
    period: str = "daily",
    config: dict[str, Any] | None = None,
) -> bytes | None:
    """Draw the four-panel chart and return PNG bytes.

    Pure: no clock, no file I/O. Returns ``None`` when fewer than
    ``MIN_BARS`` usable bars are supplied.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    data = _to_arrays(bars)
    n = data["close"].size
    if n < MIN_BARS:
        return None

    indicators = {str(i).lower() for i in (cfg.get("indicators") or [])}
    dpi = int(cfg.get("dpi") or 100)
    width = int(cfg.get("width") or 1280)
    height = int(cfg.get("height") or 900)

    opens, highs, lows, closes, vols = (
        data["open"], data["high"], data["low"], data["close"], data["volume"]
    )
    dates = data["date"]
    x = np.arange(n)
    up = closes >= opens
    colors = np.where(up, COLOR_UP, COLOR_DOWN)

    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if n >= 2 else last_close
    chg = (last_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
    title = f"{name} ({symbol}) · {period} · last close {last_close:.2f} ({chg:+.2f}%)"

    _, plt = _load_mpl()
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="white")
    try:
        gs = fig.add_gridspec(4, 1, height_ratios=[5, 1.2, 1.2, 1.2], hspace=0.08)
        ax_price = fig.add_subplot(gs[0])
        ax_vol = fig.add_subplot(gs[1], sharex=ax_price)
        ax_rsi = fig.add_subplot(gs[2], sharex=ax_price)
        ax_macd = fig.add_subplot(gs[3], sharex=ax_price)

        # 1. candles
        body_w = 0.7
        ax_price.vlines(x, lows, highs, color=colors, linewidth=0.9)
        body_bottom = np.minimum(opens, closes)
        body_h = np.abs(closes - opens)
        price_span = float(highs.max() - lows.min()) or 1.0
        body_h = np.where(body_h < price_span * 0.0015, price_span * 0.0015, body_h)
        ax_price.bar(x, body_h, bottom=body_bottom, width=body_w, color=colors, linewidth=0)

        if "ma20" in indicators:
            ax_price.plot(x, _sma(closes, 20), color=_COLOR_MA20, linewidth=1.0, label="MA20")
        if "ma50" in indicators:
            ax_price.plot(x, _sma(closes, 50), color=_COLOR_MA50, linewidth=1.0, label="MA50")
        if "boll" in indicators:
            mid, upper, lower = _boll(closes, 20, 2.0)
            ax_price.plot(x, upper, color=_COLOR_BOLL, linewidth=0.7, linestyle="--", label="BOLL(20,2)")
            ax_price.plot(x, lower, color=_COLOR_BOLL, linewidth=0.7, linestyle="--")
            mask = ~np.isnan(upper)
            if mask.any():
                ax_price.fill_between(x[mask], lower[mask], upper[mask], color=_COLOR_BOLL, alpha=0.06)
        ax_price.set_title(title, loc="left", fontsize=11, color=_COLOR_TEXT, fontweight="bold")
        ax_price.legend(loc="upper left", fontsize=7, frameon=False)
        ax_price.set_ylabel("Price", fontsize=8, color=_COLOR_TEXT)
        _style_axis(ax_price)
        plt.setp(ax_price.get_xticklabels(), visible=False)

        # 2. volume
        ax_vol.bar(x, vols, width=body_w, color=colors, linewidth=0, alpha=0.85)
        ax_vol.set_ylabel("Volume", fontsize=8, color=_COLOR_TEXT)
        _style_axis(ax_vol)
        plt.setp(ax_vol.get_xticklabels(), visible=False)

        # 3. RSI
        rsi = _rsi(closes, 14)
        ax_rsi.plot(x, rsi, color=_COLOR_RSI, linewidth=1.0)
        ax_rsi.axhline(70, color=COLOR_DOWN, linewidth=0.7, linestyle="--")
        ax_rsi.axhline(30, color=COLOR_UP, linewidth=0.7, linestyle="--")
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_yticks([30, 50, 70])
        ax_rsi.set_ylabel("RSI(14)", fontsize=8, color=_COLOR_TEXT)
        _style_axis(ax_rsi)
        plt.setp(ax_rsi.get_xticklabels(), visible=False)

        # 4. MACD
        macd, signal, hist = _macd(closes, 12, 26, 9)
        hist_colors = np.where(np.nan_to_num(hist) >= 0, COLOR_UP, COLOR_DOWN)
        ax_macd.bar(x, np.nan_to_num(hist), width=body_w, color=hist_colors, linewidth=0, alpha=0.6)
        ax_macd.plot(x, macd, color=_COLOR_MACD, linewidth=1.0, label="MACD")
        ax_macd.plot(x, signal, color=_COLOR_SIGNAL, linewidth=1.0, label="Signal")
        ax_macd.axhline(0, color=_COLOR_TEXT, linewidth=0.5)
        ax_macd.legend(loc="upper left", fontsize=7, frameon=False)
        ax_macd.set_ylabel("MACD(12,26,9)", fontsize=8, color=_COLOR_TEXT)
        _style_axis(ax_macd)

        # shared x axis: ~8 date labels
        step = max(1, n // 8)
        ticks = list(range(0, n, step))
        if ticks[-1] != n - 1:
            ticks.append(n - 1)
        ax_macd.set_xticks(ticks)
        ax_macd.set_xticklabels([str(dates[i])[:10] for i in ticks], rotation=0, fontsize=7)
        ax_price.set_xlim(-1, n)

        fig.subplots_adjust(left=0.06, right=0.99, top=0.95, bottom=0.05)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
        return buf.getvalue()
    finally:
        plt.close(fig)


# ── renderer ────────────────────────────────────────────────────────────────


class ChartRenderer:
    """Fetches bars and renders candlestick charts to PNG files."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config: dict[str, Any] = {**DEFAULT_CONFIG, **(config or {})}

    # -- data ---------------------------------------------------------------

    def _plan(self, period: str) -> tuple[int, int]:
        days, show = _PERIOD_PLAN.get(period, _PERIOD_PLAN["daily"])
        if period == "daily":
            try:
                show = int(self.config.get("bars") or show)
            except (TypeError, ValueError):
                pass
        return days, show

    async def _fetch_bars(self, symbol: str, market: str, days: int) -> list[Any]:
        from src.collectors.kline_collector import KlineCollector

        collector = KlineCollector(MarketCode(market))
        result = await asyncio.to_thread(collector.get_klines, symbol, days=days)
        if inspect.isawaitable(result):
            result = await result
        return list(result or [])

    # -- public API ---------------------------------------------------------

    async def capture(
        self,
        symbol: str,
        name: str,
        market: str = "",
        period: str = "daily",
        provider: str = "tickerkeep",
    ) -> ChartImage | None:
        """Render one chart. Returns ``None`` on any failure (logged)."""
        market = (market or default_market()).upper()
        period = period if period in _PERIOD_PLAN else "daily"
        days, show = self._plan(period)

        try:
            raw = await self._fetch_bars(symbol, market, days)
        except Exception as e:  # noqa: BLE001 - data source failures must not crash the agent
            logger.error("Chart data fetch failed %s(%s): %s", name, symbol, e)
            return None

        bars = resample_bars(raw, period)
        if len(bars) > show:
            bars = bars[-show:]
        if len(bars) < MIN_BARS:
            logger.warning(
                "Not enough bars to render %s(%s) %s: %d < %d",
                name, symbol, period, len(bars), MIN_BARS,
            )
            return None

        try:
            png = await asyncio.to_thread(
                render_png, bars, symbol=symbol, name=name, period=period, config=self.config
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Chart render failed %s(%s): %s", name, symbol, e)
            return None
        if not png:
            return None

        now = datetime.now()
        chart_dir = CHART_DIR
        try:
            chart_dir.mkdir(parents=True, exist_ok=True)
            filepath = chart_dir / f"{symbol.replace('.', '_')}_{period}_{now.strftime('%Y%m%d_%H%M%S')}.png"
            filepath.write_bytes(png)
        except OSError as e:
            logger.error("Chart write failed %s(%s): %s", name, symbol, e)
            return None

        logger.info("Chart rendered: %s(%s) %s -> %s", name, symbol, period, filepath)
        return ChartImage(
            symbol=symbol,
            name=name,
            market=market,
            filepath=str(filepath),
            period=period,
            timestamp=now,
        )

    async def capture_batch(
        self,
        stocks: list[Any],
        period: str = "daily",
        provider: str = "tickerkeep",
    ) -> list[ChartImage]:
        """Render charts for a list of stocks (dicts or objects with symbol/name/market)."""
        results: list[ChartImage] = []
        for stock in stocks:
            symbol = _bar_get(stock, "symbol", "") or ""
            name = _bar_get(stock, "name", "") or symbol
            market = _bar_get(stock, "market", "") or ""
            if hasattr(market, "value"):
                market = market.value
            if not symbol:
                continue
            image = await self.capture(
                symbol=str(symbol),
                name=str(name),
                market=str(market) or default_market(),
                period=period,
                provider=provider,
            )
            if image:
                results.append(image)
        return results

    def cleanup_old_screenshots(self, max_age_hours: int = 24) -> int:
        """Delete charts older than ``max_age_hours``. Returns the number removed."""
        chart_dir = CHART_DIR
        if not chart_dir.exists():
            return 0
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        cleaned = 0
        for path in chart_dir.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    cleaned += 1
            except OSError as e:
                logger.debug("Chart cleanup failed %s: %s", path, e)
        if cleaned:
            logger.info("Removed %d stale chart(s)", cleaned)
        return cleaned

    async def close(self) -> None:
        """Nothing to release; kept for interface parity with the old collector."""
        return None
