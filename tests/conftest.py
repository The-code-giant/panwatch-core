"""共用 pytest fixtures。

默认情况下所有通知发送函数被替换为 no-op，避免单测误发通知。
传入 --notify 参数会报错；真实通知不属于单元测试入口。

导入期隔离（G4）：这个文件是 pytest 在收集任何测试模块之前最先加载的东西,所以
下面这几行环境变量覆盖必须留在文件最顶部、其它任何 import 之前 —— 尤其是任何
可能触发 `import src.config` 或 `from src.web.database import ...` 的 import。
一旦某个测试模块在自己的模块级别做了这类 import,`src/web/database.py` 的
`create_engine()`（模块级副作用）和 `src/config.py` 里 `Settings.model_config`
的构造就已经把 DATA_DIR / TICKERKEEP_ENV_FILE 读进去了；等到某个 fixture 才去 setattr
或 monkeypatch 就已经晚了。所以这里不用 fixture，直接在模块顶层、导入其它任何
东西之前完成:
  - DATA_DIR 指向一个每次 pytest 会话都新建的一次性目录,而不是仓库里的 ./data —
    这样 src/web/database.py 建的 SQLite 引擎、以及 chart_renderer/universe/
    intraday_event_gate/settings 里那些同样读 DATA_DIR 的缓存文件,都落在这个一次性
    目录,不会碰真实安装数据。
  - TICKERKEEP_ENV_FILE 指向同一个一次性目录里一个必然不存在的文件名,这样
    pydantic-settings 的 Settings() 在测试期间不会从 CWD 加载真实 .env（不存在的
    env_file 对 pydantic-settings 来说是安全的空操作,不是错误）。
测试无条件覆盖外部安装路径；真实通知不属于单元测试入口。
"""

from __future__ import annotations

import os
import sys
import tempfile

if "src.config" in sys.modules or "src.web.database" in sys.modules:
    raise RuntimeError("TickerKeep configuration was imported before pytest isolation")
# Inspect names only; never load, print, or preserve inherited installation secrets.
# Tests that need credentials/providers must install their own synthetic values.
_INSTALLATION_PREFIXES = (
    "AUTH_", "AI_", "NOTIFY_", "OPENAI_", "ANTHROPIC_", "AZURE_", "GOOGLE_",
    "GEMINI_", "TELEGRAM_", "APPRISE_", "WEBHOOK_", "ALPACA_", "IBKR_",
    "BINANCE_", "TRADINGAGENTS_", "MARKETDATA_", "YFINANCE_", "FINNHUB_",
    "POLYGON_", "TUSHARE_", "FMP_", "ALPHA_VANTAGE_", "LANGCHAIN_", "LANGSMITH_",
)
_INSTALLATION_KEYS = {
    "JWT_SECRET", "DATABASE_URL", "DATABASE_DSN", "SQLALCHEMY_DATABASE_URI",
    "CA_CERT_FILE", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
}
for _env_name in tuple(os.environ):
    _upper_name = _env_name.upper()
    if (_upper_name.startswith(_INSTALLATION_PREFIXES)
            or _upper_name in _INSTALLATION_KEYS
            or _upper_name.endswith(("_API_KEY", "_API_TOKEN", "_ACCESS_TOKEN"))):
        os.environ.pop(_env_name, None)
_PYTEST_DISPOSABLE_DIR = tempfile.mkdtemp(prefix="tickerkeep-pytest-")
os.environ["DATA_DIR"] = _PYTEST_DISPOSABLE_DIR
os.environ["TICKERKEEP_ENV_FILE"] = os.path.join(
    _PYTEST_DISPOSABLE_DIR, ".env.never-created-so-never-loaded"
)
os.environ["DISABLE_SCHEDULERS"] = "1"
os.environ["UPDATE_CHECK_DISABLE"] = "1"

from unittest.mock import AsyncMock

import pytest


def pytest_itemcollected(item):
    """用测试函数的中文 docstring 替换 pytest -v 输出中的节点名。"""
    doc = (item.function.__doc__ or "").strip().split("\n")[0]
    if doc:
        item._nodeid = f"{item.parent.nodeid}::{doc}"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--notify",
        action="store_true",
        default=False,
        help="Unsupported: real notifications are prohibited in unit tests",
    )


def pytest_configure(config):
    if config.getoption("--notify"):
        raise pytest.UsageError("--notify is disabled; unit tests must use mocked notifications")


@pytest.fixture(autouse=True)
def _suppress_notifications(monkeypatch):
    """自动屏蔽所有测试通知发送。"""
    # patch NotifierManager.notify / notify_with_result
    monkeypatch.setattr(
        "src.core.notifier.NotifierManager.notify",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.notifier.NotifierManager.notify_with_result",
        AsyncMock(return_value={"success": True, "suppressed": True}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _mock_stock_link_platform(monkeypatch):
    """避免 stock_link 模块访问数据库读取平台设置。"""
    monkeypatch.setattr(
        "src.core.stock_link.get_platform",
        lambda: "xueqiu",
    )


@pytest.fixture(autouse=True)
def _clear_market_caches():
    """清空采集层内存缓存,避免用例间互相污染(K线/报价/资金流等现按 TTL 缓存)。"""
    from src.collectors import kline_collector
    from src.web.api import market as market_api

    def _clear():
        kline_collector.clear_kline_cache()
        market_api.clear_indices_cache()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True, scope="session")
def _ensure_db_schema():
    """确保本次测试的临时 DB 引擎已建表。

    少数用例直接用 SessionLocal 传给 async 接口(只读查询),CI 全新环境的
    data/tickerkeep.db 无表会报 'no such table: stocks'。这里在会话开始时幂等建表
    与各用例自建的内存库互不影响；安装数据不参与测试。
    """
    from src.web import models  # noqa: F401  注册所有 ORM 模型到 Base.metadata
    from src.web.database import Base, engine

    Base.metadata.create_all(engine)
    yield


# ---------------------------------------------------------------------------
# 共用工厂 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> dict:
    """模拟盘账户数据。"""
    return {
        "id": 1,
        "name": "测试账户",
        "initial_capital": 100_000.0,
        "current_capital": 100_000.0,
    }


@pytest.fixture
def mock_signal() -> dict:
    """模拟策略信号。"""
    return {
        "strategy": "trend_follow",
        "symbol": "002837",
        "market": "CN",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "趋势向上突破",
    }
