"""请求 / 响应 / 行情数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Request:
    """一次数据请求。frozen=True 便于做缓存键。"""

    symbols: tuple[str, ...] = ()
    market: str = "CN"
    timeframe: str = "day"
    limit: int = 120
    since_hours: int = 12
    extra: tuple[tuple[str, Any], ...] = ()

    def cache_key(self, datatype: str) -> str:
        sym = ",".join(self.symbols)
        extra = ",".join(f"{k}={v}" for k, v in self.extra)
        return f"{datatype}|{self.market}|{self.timeframe}|{self.limit}|{self.since_hours}|{sym}|{extra}"


@dataclass
class Quote:
    """标准化实时报价。字段对齐 _parse_tencent_line 的产出。"""

    symbol: str
    market: str
    current_price: float
    name: str = ""
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe_ratio: float | None = None
    circulating_market_value: float | None = None
    total_market_value: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Bar:
    """标准化日K(对齐 TickerKeep KlineData:date/open/close/high/low/volume)。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0


@dataclass(frozen=True)
class HotStock:
    """热门/异动股(对齐 TickerKeep src/collectors/discovery_collector.HotStock)。"""

    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None


@dataclass(frozen=True)
class HotBoard:
    """热门板块(对齐 TickerKeep src/collectors/discovery_collector.HotBoard)。"""

    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass
class EventItem:
    """Corporate event. For events ``publish_time`` IS the event date (00:00 UTC), past or future."""

    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


@dataclass
class Fundamentals:
    """标准化基本面/财务数据(按 symbol)。估值类字段/财报类字段来源不同、可能分批到位,
    拿不到的字段一律 None,不伪造。"""

    symbol: str
    market: str
    name: str = ""
    # —— 估值类 ——
    pe_ttm: float | None = None                    # 市盈率(TTM)
    pe_static: float | None = None                  # 市盈率(静态)
    pb: float | None = None                         # 市净率
    ps_ttm: float | None = None                     # 市销率(TTM)
    total_market_value: float | None = None         # market cap, absolute native currency
    circulating_market_value: float | None = None   # float market cap, absolute native currency
    dividend_yield: float | None = None             # 股息率(%)
    total_shares: float | None = None               # 总股本(股)
    float_shares: float | None = None                # 流通股本(股)
    # —— 财报类 ——
    eps: float | None = None                        # 每股收益
    bps: float | None = None                        # 每股净资产
    roe: float | None = None                        # 净资产收益率(%)
    revenue: float | None = None                    # 营业收入
    net_profit: float | None = None                 # 归母净利润
    gross_margin: float | None = None               # 毛利率(%)
    net_margin: float | None = None                 # 净利率(%)
    revenue_yoy: float | None = None                # 营收同比增长(%)
    net_profit_yoy: float | None = None             # 净利润同比增长(%)
    report_date: str = ""                           # reporting period, raw string (no date parsing)
    currency: str = ""                              # native currency of all monetary fields (USD / CAD)
    operating_cash_flow: float | None = None        # operating cash flow (TTM, native currency)
    free_cash_flow: float | None = None             # free cash flow (TTM, native currency)
    total_debt: float | None = None                 # total debt (native currency)
    total_cash: float | None = None                 # total cash (native currency)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DividendItem:
    """Dividend row (per symbol, newest first). ``progress``: "paid" | "announced"."""

    ex_date: str
    symbol: str
    dividend_per_share: float | None = None  # cash dividend per share (native currency)
    transfer_ratio: float | None = None      # legacy CN field, always None for US/CA
    bonus_ratio: float | None = None         # legacy CN field, always None for US/CA
    progress: str = ""                       # "paid" | "announced"


@dataclass
class FlashNews:
    """Market headline from a publisher RSS feed (CNBC, MarketWatch, Financial Post, BNN). Market level; symbols empty."""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class NewsArticle:
    """Symbol news (aligned with TickerKeep src/collectors/news_collector.NewsItem).
    ``source`` is the vendor name (yfinance / google_news); ``publisher`` is the outlet
    that wrote the story (Reuters, Bloomberg, ...)."""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""
    publisher: str = ""


@dataclass
class HolderItem:
    """Ownership row. ``kind``: "breakdown" (insiders % / institutions % / institutions count),
    "institution" (one institutional holder), "insider_tx" (one insider transaction)."""

    symbol: str
    kind: str  # "breakdown" | "institution" | "insider_tx"
    holder: str
    date: str = ""
    shares: float | None = None
    value: float | None = None
    pct_out: float | None = None       # percent of shares outstanding (0-100)
    change_pct: float | None = None    # change in position, percent
    transaction: str = ""              # Purchase / Sale / ... (insider_tx)
    position: str = ""                 # insider role (insider_tx)
    ownership: str = ""                # D (direct) / I (indirect) (insider_tx)
    url: str = ""
    source: str = ""


@dataclass
class FilingItem:
    """Regulatory filing (SEC EDGAR) or, for Canada, a company press release."""

    source: str
    external_id: str
    symbol: str
    form_type: str
    title: str
    filed_at: datetime
    url: str
    description: str = ""
    report_date: str = ""


@dataclass
class Response:
    """Engine 返回:承载 payload + 命中的 vendor/延迟。"""

    ok: bool
    data: Any = None
    error: str = ""
    vendor: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        if self.data is None:
            return True
        if isinstance(self.data, (list, tuple, dict, set)) and len(self.data) == 0:
            return True
        return False
