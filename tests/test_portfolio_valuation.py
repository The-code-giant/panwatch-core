"""持仓估值正确性回归测试(round 4):对真实的 `src.web.api.accounts.get_portfolio_summary`
计算逻辑做端到端断言,绝不重新实现一份"影子算法"。核心回归——缺价仓位不能把自己的完整
成本记为 100% 亏损:优先仓净盈亏应为 +80.88,而不是把未定价 CN 遗留仓位的 7500 成本也
当作亏损扣掉后的 -7419.12。真正的 0.00 盈亏(MSFT)必须原样透出,不能被
`if pnl` 之类的真值判断悄悄擦成 null。

覆盖范围:
1) THE PINNED REGRESSION —— AAPL/NVDA/MSFT/SHOP.TO 全部有价 + 一笔 CN 遗留仓位无价:
   priced-subset `total_pnl == 80.88`,`pnl_basis == "priced_subset"`,
   `valuation_complete is False`,`unpriced_positions == 1`,且绝不等于 -7419.12。
2) 真正的 0.00 盈亏(MSFT)必须原样透出为 `0.0`,而不是 `None`。
3) 全部未定价:`total_pnl`/`total_pnl_pct` 必须是 `None`,绝不能被替换成 0/0.0。
4) 受支持市场"暂时无价"(unavailable)与不受支持市场(unsupported)必须能被
   `valuation_status` 区分,不能混为一谈。
5) 多账户:coverage 计数(`total_positions`/`priced_positions`/`unpriced_positions`/
   `unsupported_positions`)与 priced-subset P&L 必须能跨账户正确汇总。
6) 明确的 CAD/USD 汇率(非 1.0)必须被用于折算 USD 数值;汇率未知的市场
   (不受支持/已下线市场)必须 `cost_usd is None`,被排除在 USD 聚合外,但原币种
   `cost` 必须完整保留,绝不丢数据。
7) 同一 symbol 在两个受支持市场(US/CA)各有一份仓位、价格/币种不同:
   每条仓位必须拿到自己市场的报价,绝不能互相污染
   ——这正是 `accounts.py` 原来 `quotes.get(stock.symbol)` 单键 bug 的回归用例。

隔离说明:本模块绝不连接真实数据库文件(`data/panwatch.db`)。每个用例都在全新的
`sqlite:///:memory:` 引擎(`StaticPool` + `check_same_thread=False`)上建表并插入
真实的 `Account`/`Stock`/`Position` ORM 行,再把这个内存 `Session` 直接作为关键字参数
传给真实的 `get_portfolio_summary(db=...)`——由于 FastAPI 的 `Depends(get_db)` 只是
参数默认值,直接以关键字传参调用普通 Python 函数时完全不会触发那个依赖,也就不会碰
真实 DB 引擎。`_fetch_quotes_for_stocks` 被 monkeypatch 为确定性 fixture;汇率通过
`_fx_known` / `_fx_unknown` 直接给真实的 `_cad_usd_rate_cache` 播种(并把两个 provider
fetcher 都 patch 掉),让真实的 `_resolve_cad_usd_state` / `resolve_fx_snapshot` 走完整路径
而绝不发起真实网络请求(旧的 `get_cad_usd_rate` / `cad_usd_rate_known` lambda seam 已随
Revision 7 的原子化 FX 解析器一起有意移除);此外自动断网 fixture(patch
`socket.socket.connect` / `socket.create_connection`)照抄
`tests/test_position_support_boundary.py` 的既有先例,做 fail-closed 兜底。

NOTE(worker C): `_fetch_quotes_for_stocks` 在本轮修复中被重新按 `(market, symbol)`
二元组为 key(修复原来 `quotes.get(stock.symbol)` 单键导致的跨市场同名标的污染)。
本文件所有 quote fixture 都按 `(market, symbol)` 元组 key 提供,并在 `_quote_stub`
处再次注明。
"""

from __future__ import annotations

import socket
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.accounts as accounts_api


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都立即失败(fail-closed),照抄
    tests/test_position_support_boundary.py 的既有手法。"""

    def _blocked(*a, **k):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# 独立内存 sqlite 隔离:绝不碰真实 data/panwatch.db
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """全新内存 sqlite 引擎 + 真实 ORM 建表,每个用例互不干扰、绝不写真实 DB 文件。"""
    from src.web import models as _models  # noqa: F401  确保所有 ORM 模型注册到 Base.metadata
    from src.web.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _make_account(db, *, name: str = "Test Account", available_funds: float = 0.0):
    from src.web.models import Account

    acc = Account(name=name, available_funds=available_funds, enabled=True)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _make_stock(db, *, symbol: str, name: str, market: str):
    from src.web.models import Stock

    stock = Stock(symbol=symbol, name=name, market=market)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _make_position(db, *, account, stock, cost_price: float, quantity: int, trading_style: str = "swing"):
    from src.web.models import Position

    pos = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=cost_price,
        quantity=quantity,
        trading_style=trading_style,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def _quote_stub(quote_map: dict):
    """构造一个 `_fetch_quotes_for_stocks` 替身,返回值按 `(market, symbol)` 二元组 key。

    NOTE(worker C): 生产代码原来的 bug 是 `quotes.get(stock.symbol)`——单纯按 symbol
    查找,导致同一 symbol 在两个不同市场(如 US 的 SHOP 与 CA 的 SHOP)会互相覆盖/污染
    对方的报价。本轮修复把 `_fetch_quotes_for_stocks` 的返回值重新按
    `(market, symbol)` 元组 key,这里的 stub 严格模拟修复后的形状,好让
    `get_portfolio_summary` 里真实的查找逻辑接受真实的测试。
    """

    def _fetch(stocks):
        out = {}
        for s in stocks:
            key = (s.market, s.symbol)
            if key in quote_map:
                out[key] = quote_map[key]
        return out

    return _fetch


def _fx_known(monkeypatch, *, rate: float = 1.0, source: str = "test fixture"):
    """给真实的 FX 缓存播种一个"真实且在 TTL 内"的汇率,并禁止任何 provider 抓取:
    真实的 `_resolve_cad_usd_state` 因此在不碰网络的情况下返回 status=known。
    这是 Revision 7 之后唯一受支持的"已知汇率"测试 seam(取代过去 patch
    `get_cad_usd_rate` / `cad_usd_rate_known` 两个 lambda 的做法)。"""
    now = time.time()
    monkeypatch.setattr(accounts_api, "_fx_refresh_inflight", False)
    monkeypatch.setattr(
        accounts_api,
        "_cad_usd_rate_cache",
        {"rate": rate, "ts": now, "known": True, "genuine": True, "fetched_at": now, "source": source},
    )

    def _forbidden():
        raise AssertionError("FX provider fetch must not run while the cache is within TTL")

    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_boc", _forbidden)
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_macro", _forbidden)


def _fx_unknown(monkeypatch):
    """从未真实获取过汇率 + 两个 provider 都失败:真实解析器必须报告 unknown / rate None。"""
    monkeypatch.setattr(accounts_api, "_fx_refresh_inflight", False)
    monkeypatch.setattr(
        accounts_api,
        "_cad_usd_rate_cache",
        {"rate": accounts_api._CAD_USD_FALLBACK, "ts": 0, "known": False},
    )
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_macro", lambda: None)


# ---------------------------------------------------------------------------
# 1) + 2) 钉住的回归 fixture:AAPL/NVDA/MSFT/SHOP.TO 有价 + CN 遗留仓位无价
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned_portfolio_result(db_session, monkeypatch):
    """构造 REVIEW-03.md 里钉住的复现场景,调用真实 get_portfolio_summary。

    数字选择说明(与 REVIEW-03.md 给出的示意值的偏差,已在此处说明):
    - AAPL cost 180x50 @204.70 -> pnl = (204.70-180)*50 = +1235.00 (与钉住值一致)
    - NVDA cost 560x20 @484.50 -> pnl = (484.50-560)*20 = -1510.00 (与钉住值一致)
    - MSFT cost 410x10 @410.00 -> pnl = 0.00 (真正的零,真值判断陷阱的靶子)
    - SHOP.TO(CA) 按 REVIEW-03.md 给出的 "90x30 @106.25" 字面计算得到 (106.25-90)*30
      = 487.50,并不等于钉住的 +355.88。为了让 priced-subset 总和精确落在 +80.88
      (1235 - 1510 + 0 + X = 80.88 => X = 355.88),这里改用
      cost_price=100.00, quantity=28, current_price=112.71
      -> (112.71-100)*28 = 355.88,与钉住的 +355.88 精确吻合;CAD_USD 汇率
      mock 为 1.0,所以原币种盈亏 1:1 转换成 USD,不引入汇率误差。
    - CN 600519 cost 1500x5 = 7500 成本,不返回任何报价(unpriced),且 CN 是
      未启用市场(unsupported),用来复现"缺价被算成 100% 亏损"的 bug。
    """
    _fx_known(monkeypatch, rate=1.0)

    acc = _make_account(db_session, name="Main", available_funds=500.0)
    aapl = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    nvda = _make_stock(db_session, symbol="NVDA", name="Nvidia", market="US")
    msft = _make_stock(db_session, symbol="MSFT", name="Microsoft", market="US")
    shop = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    cn_legacy = _make_stock(db_session, symbol="600519", name="Moutai", market="CN")

    _make_position(db_session, account=acc, stock=aapl, cost_price=180.0, quantity=50)
    _make_position(db_session, account=acc, stock=nvda, cost_price=560.0, quantity=20)
    _make_position(db_session, account=acc, stock=msft, cost_price=410.0, quantity=10)
    _make_position(db_session, account=acc, stock=shop, cost_price=100.0, quantity=28)
    _make_position(db_session, account=acc, stock=cn_legacy, cost_price=1500.0, quantity=5)

    quote_map = {
        ("US", "AAPL"): {"current_price": 204.70, "change_pct": None, "prev_close": None},
        ("US", "NVDA"): {"current_price": 484.50, "change_pct": None, "prev_close": None},
        ("US", "MSFT"): {"current_price": 410.00, "change_pct": None, "prev_close": None},
        ("CA", "SHOP.TO"): {"current_price": 112.71, "change_pct": None, "prev_close": None},
        # CN 600519 故意不给报价:unpriced。
    }
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub(quote_map))

    return accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)


def test_pinned_regression_priced_subset_pnl_is_80_88(pinned_portfolio_result):
    """核心 P1 回归:优先仓净盈亏必须是 +80.88(priced-subset),绝不能是把 CN 遗留仓位
    7500 成本当亏损扣掉后的 -7419.12;coverage 计数与 valuation_complete 必须准确。"""
    total = pinned_portfolio_result["total"]

    assert total["total_pnl"] == pytest.approx(80.88, abs=1e-2)
    assert total["total_pnl"] != pytest.approx(-7419.12, abs=1)
    assert total["pnl_basis"] == "priced_subset"
    assert total["valuation_complete"] is False
    assert total["total_positions"] == 5
    assert total["priced_positions"] == 4
    assert total["unpriced_positions"] == 1
    assert total["unsupported_positions"] == 1
    assert total["unavailable_positions"] == 0
    # CN 仓位的汇率未知 -> unpriced_cost 不能被伪造成一个具体数字。
    assert total["unpriced_cost"] is None
    assert total["cost_basis_complete"] is False
    assert total["priced_cost_basis"] == pytest.approx(9000.0 + 11200.0 + 4100.0 + 2800.0)
    assert total["total_market_value"] == pytest.approx(10235.0 + 9690.0 + 4100.0 + 3155.88)


def test_true_zero_pnl_survives_and_is_not_nulled(pinned_portfolio_result):
    """MSFT 的真实 0.00 盈亏必须原样透出为 0.0 且 `is not None`——旧的 `if pnl else None`
    真值判断会把它错误地擦成 null。"""
    positions = pinned_portfolio_result["accounts"][0]["positions"]
    msft_row = next(p for p in positions if p["symbol"] == "MSFT")

    assert msft_row["priced"] is True
    assert msft_row["valuation_status"] == "priced"
    assert msft_row["pnl"] == 0.0
    assert msft_row["pnl"] is not None
    assert msft_row["pnl_pct"] == 0.0
    assert msft_row["pnl_pct"] is not None


# ---------------------------------------------------------------------------
# 3) 全部未定价:total_pnl/total_pnl_pct 必须是 None,不能被替换成 0/0.0
# ---------------------------------------------------------------------------


def test_all_unpriced_positions_yield_null_aggregate_pnl_not_zero(db_session, monkeypatch):
    """所有仓位都没有拿到报价:total_pnl/total_pnl_pct/total_assets 必须是 None,
    绝不能被替换成一个具体的 0 或 0.0(那会被误读成"没有盈亏"而不是"未知")。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=100.0)
    aapl = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    nvda = _make_stock(db_session, symbol="NVDA", name="Nvidia", market="US")
    _make_position(db_session, account=acc, stock=aapl, cost_price=100.0, quantity=10)
    _make_position(db_session, account=acc, stock=nvda, cost_price=200.0, quantity=5)

    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub({}))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = result["total"]

    assert total["priced_positions"] == 0
    assert total["total_pnl"] is None
    assert total["total_pnl_pct"] is None
    assert total["total_assets"] is None
    assert total["valuation_complete"] is False
    assert total["pnl_basis"] == "priced_subset"

    for pos in result["accounts"][0]["positions"]:
        assert pos["priced"] is False
        assert pos["pnl"] is None
        assert pos["pnl_pct"] is None
        assert pos["market_value"] is None
        assert pos["market_value_cny"] is None
        # 原币种成本必须始终存在,不受定价状态影响。
        assert pos["cost"] is not None


# ---------------------------------------------------------------------------
# 4) 受支持但暂时无价(unavailable) vs 不受支持市场(unsupported)
# ---------------------------------------------------------------------------


def test_valuation_status_distinguishes_unavailable_from_unsupported(db_session, monkeypatch):
    """US(受支持市场)缺价 => valuation_status='unavailable';CN(不受支持市场)缺价
    => valuation_status='unsupported'。两者绝不能被混为一谈。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0.0)
    us_stock = _make_stock(db_session, symbol="TSLA", name="Tesla", market="US")
    cn_stock = _make_stock(db_session, symbol="600519", name="Moutai", market="CN")
    _make_position(db_session, account=acc, stock=us_stock, cost_price=200.0, quantity=10)
    _make_position(db_session, account=acc, stock=cn_stock, cost_price=1500.0, quantity=5)

    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub({}))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    positions = result["accounts"][0]["positions"]
    us_row = next(p for p in positions if p["symbol"] == "TSLA")
    cn_row = next(p for p in positions if p["symbol"] == "600519")

    assert us_row["priced"] is False
    assert us_row["valuation_status"] == "unavailable"
    assert cn_row["priced"] is False
    assert cn_row["valuation_status"] == "unsupported"
    assert us_row["valuation_status"] != cn_row["valuation_status"]


# ---------------------------------------------------------------------------
# 5) 多账户:coverage 计数与 priced-subset P&L 跨账户正确汇总
# ---------------------------------------------------------------------------


def test_multiple_accounts_aggregate_coverage_and_priced_subset_pnl(db_session, monkeypatch):
    """两个账户:coverage 计数(总数/已定价/未定价/不受支持)与 priced-subset P&L
    必须正确跨账户汇总,而不是只统计第一个账户。"""
    _fx_known(monkeypatch, rate=1.0)
    acc1 = _make_account(db_session, name="Acc1", available_funds=100.0)
    acc2 = _make_account(db_session, name="Acc2", available_funds=200.0)

    aapl = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    nvda = _make_stock(db_session, symbol="NVDA", name="Nvidia", market="US")
    cn_stock = _make_stock(db_session, symbol="600519", name="Moutai", market="CN")

    _make_position(db_session, account=acc1, stock=aapl, cost_price=100.0, quantity=10)  # cost 1000
    _make_position(db_session, account=acc2, stock=nvda, cost_price=200.0, quantity=5)  # cost 1000
    _make_position(db_session, account=acc2, stock=cn_stock, cost_price=1500.0, quantity=1)  # cost 1500, unsupported

    quote_map = {
        ("US", "AAPL"): {"current_price": 110.0, "change_pct": None, "prev_close": None},  # pnl +100
        ("US", "NVDA"): {"current_price": 190.0, "change_pct": None, "prev_close": None},  # pnl -50
    }
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub(quote_map))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = result["total"]

    assert total["total_positions"] == 3
    assert total["priced_positions"] == 2
    assert total["unpriced_positions"] == 1
    assert total["unsupported_positions"] == 1
    assert total["unavailable_positions"] == 0
    assert total["pnl_basis"] == "priced_subset"
    assert total["total_pnl"] == pytest.approx(100.0 - 50.0)
    assert total["priced_cost_basis"] == pytest.approx(1000.0 + 1000.0)

    acc1_summary = next(a for a in result["accounts"] if a["name"] == "Acc1")
    acc2_summary = next(a for a in result["accounts"] if a["name"] == "Acc2")
    assert acc1_summary["priced_positions"] == 1
    assert acc1_summary["unsupported_positions"] == 0
    assert acc2_summary["priced_positions"] == 1
    assert acc2_summary["unsupported_positions"] == 1


# ---------------------------------------------------------------------------
# 6) 明确的 CAD/USD 汇率 + 汇率未知市场被排除在 USD 聚合外
# ---------------------------------------------------------------------------


def test_distinct_cad_usd_rate_and_unknown_fx_excludes_from_usd_aggregates(db_session, monkeypatch):
    """CA 仓位必须按 mock 的真实汇率(0.73,而非 1.0)折算成 USD;不受支持市场
    (汇率未知)的 cost_usd 必须是 None(绝不能臆造成 1:1),并被排除在 USD 聚合
    (total_cost)之外,但原币种 cost 必须完整保留。"""
    _fx_known(monkeypatch, rate=0.73)
    acc = _make_account(db_session, available_funds=0.0)
    shop = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    cn_stock = _make_stock(db_session, symbol="600519", name="Moutai", market="CN")

    _make_position(db_session, account=acc, stock=shop, cost_price=100.0, quantity=10)  # native cost 1000
    _make_position(db_session, account=acc, stock=cn_stock, cost_price=1500.0, quantity=2)  # native cost 3000, FX unknown

    quote_map = {("CA", "SHOP.TO"): {"current_price": 120.0, "change_pct": None, "prev_close": None}}
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub(quote_map))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    positions = result["accounts"][0]["positions"]
    shop_row = next(p for p in positions if p["symbol"] == "SHOP.TO")
    cn_row = next(p for p in positions if p["symbol"] == "600519")

    assert shop_row["cost"] == pytest.approx(1000.0)
    assert shop_row["cost_usd"] == pytest.approx(1000.0 * 0.73)
    assert shop_row["market_value"] == pytest.approx(1200.0)
    assert shop_row["market_value_cny"] == pytest.approx(1200.0 * 0.73)

    assert cn_row["cost"] == pytest.approx(3000.0)  # 原币种成本永远存在
    assert cn_row["cost_usd"] is None  # 汇率未知,绝不能臆造成 1:1

    total = result["total"]
    assert total["cost_basis_complete"] is False
    # total_cost 是"汇率已知部分的完整 USD 成本",必须排除 CN 那笔。
    assert total["total_cost"] == pytest.approx(1000.0 * 0.73)


# ---------------------------------------------------------------------------
# 7) 同一 symbol 两个受支持市场:_fetch_quotes_for_stocks 键控回归
# ---------------------------------------------------------------------------


def test_same_symbol_two_enabled_markets_no_cross_contamination(db_session, monkeypatch):
    """同一 symbol 在 US 与 CA 各有一份仓位,价格不同:每条仓位必须拿到自己市场的
    报价,绝不能互相污染——这正是 accounts.py 原来 `quotes.get(stock.symbol)`
    单键 bug(按 symbol 而非 (market, symbol) 查找)的回归用例。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0.0)
    us_stock = _make_stock(db_session, symbol="SHOP", name="Shopify US", market="US")
    ca_stock = _make_stock(db_session, symbol="SHOP", name="Shopify CA", market="CA")

    _make_position(db_session, account=acc, stock=us_stock, cost_price=50.0, quantity=10)
    _make_position(db_session, account=acc, stock=ca_stock, cost_price=60.0, quantity=20)

    quote_map = {
        ("US", "SHOP"): {"current_price": 55.0, "change_pct": None, "prev_close": None},
        ("CA", "SHOP"): {"current_price": 65.0, "change_pct": None, "prev_close": None},
    }
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub(quote_map))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    positions = result["accounts"][0]["positions"]
    us_row = next(p for p in positions if p["market"] == "US")
    ca_row = next(p for p in positions if p["market"] == "CA")

    assert us_row["current_price"] == pytest.approx(55.0)
    assert ca_row["current_price"] == pytest.approx(65.0)
    assert us_row["current_price"] != ca_row["current_price"]
    assert us_row["priced"] is True
    assert ca_row["priced"] is True


# ---------------------------------------------------------------------------
# 8) CAD 汇率未知时服务端必须 fail-closed(与客户端 portfolio-valuation.ts 对齐)
# ---------------------------------------------------------------------------


def test_unknown_cad_rate_fails_closed_and_never_invents_a_conversion(db_session, monkeypatch):
    """CAD/USD 汇率从未真正取到(known=False)时,服务端绝不能用兜底常量 0.73 折算:
    CA 仓位的 cost_usd / market_value_cny / pnl 必须为 None,原币种 cost 与
    market_value 必须完整保留,且该仓位不得进入 USD 聚合 —— 与客户端合约一致。"""
    # Never-fetched cache and both providers down: the real resolver reports unknown.
    _fx_unknown(monkeypatch)
    acc = _make_account(db_session, available_funds=0.0)
    shop = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    aapl = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    _make_position(db_session, account=acc, stock=shop, cost_price=100.0, quantity=10)
    _make_position(db_session, account=acc, stock=aapl, cost_price=180.0, quantity=50)

    quote_map = {
        ("CA", "SHOP.TO"): {"current_price": 120.0, "change_pct": None, "prev_close": None},
        ("US", "AAPL"): {"current_price": 204.70, "change_pct": None, "prev_close": None},
    }
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", _quote_stub(quote_map))

    result = accounts_api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    positions = result["accounts"][0]["positions"]
    shop_row = next(p for p in positions if p["symbol"] == "SHOP.TO")
    aapl_row = next(p for p in positions if p["symbol"] == "AAPL")

    # Native figures survive; the invented conversion does not happen.
    assert shop_row["cost"] == pytest.approx(1000.0)
    assert shop_row["market_value"] == pytest.approx(1200.0)
    assert shop_row["cost_usd"] is None
    assert shop_row["market_value_cny"] is None
    assert shop_row["pnl"] is None
    # 0.73 must appear nowhere in this position's USD figures.
    assert shop_row["market_value_cny"] != pytest.approx(1200.0 * 0.73)

    # The USD-denominated US position is unaffected and still valued.
    assert aapl_row["cost_usd"] == pytest.approx(9000.0)
    assert aapl_row["pnl"] == pytest.approx(204.70 * 50 - 9000.0)

    # The CA position is excluded from the USD P&L basis, and that is disclosed.
    total = result["total"]
    assert total["total_pnl"] == pytest.approx(204.70 * 50 - 9000.0)
    assert total["valuation_complete"] is False

    # The rates block must advertise the rate as unknown, never as fresh.
    assert result["exchange_rates"]["fx_status"]["CAD_USD"] == "unknown"
