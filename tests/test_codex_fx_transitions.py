"""FX 快照(FxSnapshot)状态迁移与估值边界回归测试:对真实的
`src.web.api.accounts.get_portfolio_summary`(以及 `_gather_holdings`)做端到端
断言,绝不重新实现一份"影子算法"。

本文件的前四个用例逐字复用了 `/private/tmp/panwatch-codex-review5.GaGlfE/tests/
test_codex_fx_review.py`(独立 codex 复核探针)里的断言与 mock 方式,不削弱其原有
断言;在此基础上补充覆盖 THE ONE AGREED CONTRACT(冷启动成功获取汇率、汇率过期后
的 `last_known` 状态、无任何"美元计价"仓位、真实的零价格/零盈亏必须原样透出、
日行情数据缺失、报价值非法(非有限数/负数)必须在估值边界被当作不可用、以及
正常的美加混合组合)。

约定:
- 所有 provider 都在调用真实函数 **之前** 被 monkeypatch(与探针文件一致)。
- 自动断网 fixture(patch `socket.socket.connect` / `socket.create_connection`)
  fail-closed 兜底,绝不发起真实网络请求。
- 复用 `tests/test_portfolio_valuation.py` 里已验证为纯内存 sqlite 的
  `db_session` fixture 及 `_make_account`/`_make_stock`/`_make_position`/
  `_quote_stub`/`_fx_known`/`_fx_unknown` 辅助函数,不重新实现。Revision 7 起,
  "汇率已知"不再通过 patch `get_cad_usd_rate`/`cad_usd_rate_known` 两个 lambda 来
  伪造(后者已被有意移除),而是用 `_fx_known` 给真实缓存播种并让真实的原子解析器
  `_resolve_cad_usd_state` 走完整路径。

注意(worker C,写在这里供 parent 校验):本文件按 THE ONE AGREED CONTRACT 编写,
而不是按 `src/web/api/accounts.py` 当前实现编写——worker A 正在把该文件从"二值
known/unknown 汇率缓存"改造成三态 FxSnapshot(known/last_known/unknown)、把
`priced_positions` 的口径从"有没有报价"改成"是否美元计价"、并在
`exchange_rates` 里补上 `fx_as_of`/`fx_source`。因此本文件里标注为
"contract-only"的用例,在 worker A 完成改造之前会失败,这是预期的——它们是
worker A 改造完成后的验收断言,不是当前代码的回归断言。
"""

from __future__ import annotations

import math
import socket

import pytest

from tests.test_portfolio_valuation import (
    db_session,  # noqa: F401  fixture re-export, pytest 通过参数名发现
    _make_account,
    _make_stock,
    _make_position,
    _quote_stub,
    _fx_known,
    _fx_unknown,
)
import src.web.api.accounts as api


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """强制断网:任何真实网络连接都必须立即失败(fail-closed),照抄
    tests/test_position_support_boundary.py 与 tests/test_portfolio_valuation.py
    的既有手法,防止本文件误连真实汇率/行情服务。"""

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network access is blocked in this test module")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# 逐字复用的探针 seed():单账户 + 一笔 CA 仓位(SHOP),不预设汇率状态。
# ---------------------------------------------------------------------------


def seed(db, monkeypatch):
    """构造探针文件里的最小场景:一个账户 + 一笔 CA 市场的 SHOP 持仓，报价已知。"""
    acc = _make_account(db, available_funds=500)
    stock = _make_stock(db, symbol="SHOP", name="Shopify", market="CA")
    _make_position(db, account=acc, stock=stock, cost_price=100, quantity=1)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("CA", "SHOP"): {"current_price": 120, "prev_close": 120, "change_pct": 0},
    }))
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)
    return acc, stock


# ---------------------------------------------------------------------------
# 逐字复用的四个探针断言(不削弱原有断言)。
# ---------------------------------------------------------------------------


def test_unknown_only_cad_has_no_zero_usd_pnl(db_session, monkeypatch):
    """探针复用 1/4:汇率从未真正获取过且本次刷新也失败时，唯一一笔 CA 仓位
    绝不能让 total_pnl 变成一个臆造的 0，必须是 None。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    assert out["total"]["total_pnl"] is None, out["total"]


def test_expired_known_failure_does_not_value_as_complete(db_session, monkeypatch):
    """探针复用 2/4:缓存里曾经真实拿到过汇率，但已过期，且本次刷新失败——
    绝不能让 valuation_complete 变成 True。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .7, "ts": 0, "known": True})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    assert out["total"]["valuation_complete"] is False, out


def test_cold_success_same_snapshot_values_position(db_session, monkeypatch):
    """探针复用 3/4:冷启动（缓存从未真实拿到过汇率）本次刷新成功——
    同一个请求快照里，仓位必须用这个刚拿到的汇率折算，且 fx_status 报告 known。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: .8)
    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    assert out["exchange_rates"]["fx_status"]["CAD_USD"] == "known"
    assert out["accounts"][0]["positions"][0]["cost_usd"] == pytest.approx(80), out


def test_analytics_cold_success_attempts_fx(db_session, monkeypatch):
    """探针复用 4/4:冷启动刷新成功时，`_gather_holdings` 这条分析用的独立
    路径也必须能正确抓到这笔持仓（而不是因为汇率状态而漏掉它）。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: .8)
    holdings, coverage = api._gather_holdings(db_session)
    assert len(holdings) == 1, coverage


# ---------------------------------------------------------------------------
# 补充用例 1: 冷启动成功 —— 汇率来源（source）与获取时间（as_of）必须被正确
# 标注，而不仅仅是 fx_status。
# ---------------------------------------------------------------------------


def test_cold_fx_success_labels_source_as_bank_of_canada(db_session, monkeypatch):
    """cold FX success：Bank of Canada 抓取成功时，exchange_rates 必须标注
    source="Bank of Canada"，且 fx_as_of 是一个刚刚发生的时间戳，绝不能是 None。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: .8)

    import time
    before = time.time()
    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    after = time.time()

    rates = out["exchange_rates"]
    assert rates["fx_status"]["CAD_USD"] == "known"
    assert rates["CAD_USD"] == pytest.approx(0.8)
    assert rates["fx_source"]["CAD_USD"] == "Bank of Canada"
    as_of = rates["fx_as_of"]["CAD_USD"]
    assert as_of is not None
    assert before - 5 <= as_of <= after + 5


def test_cold_fx_success_falls_back_to_yahoo_source_when_boc_fails(db_session, monkeypatch):
    """cold FX success（次级来源）：Bank of Canada 失败但 Yahoo CAD=X 成功时，
    source 必须标注为 "Yahoo CAD=X"，绝不能张冠李戴成 Bank of Canada。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: .75)

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    rates = out["exchange_rates"]
    assert rates["fx_status"]["CAD_USD"] == "known"
    assert rates["fx_source"]["CAD_USD"] == "Yahoo CAD=X"


# ---------------------------------------------------------------------------
# 补充用例 2: 汇率过期 —— 必须落入 last_known（而不是 unknown），汇率本身仍可
# 用于展示，但绝不能让 valuation_complete 变 True。
# ---------------------------------------------------------------------------


def test_expired_fx_failure_reports_last_known_not_unknown(db_session, monkeypatch):
    """expired FX：缓存里是曾经真实获取过的汇率（非常量兜底），已过期，本次
    刷新失败——fx_status 必须是 "last_known"（不是 "unknown"），CAD_USD 这个
    过期但真实的汇率仍然可用于展示（不能被清空成 None），且必须标注来源。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .7, "ts": 0, "known": True})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    rates = out["exchange_rates"]

    assert rates["fx_status"]["CAD_USD"] == "last_known"
    assert rates["CAD_USD"] is not None  # a real previously-fetched rate, usable for display
    assert "fx_as_of" in rates, "last_known must carry an as_of per contract"
    assert "fx_source" in rates, "last_known must carry a source label per contract"
    assert out["total"]["valuation_complete"] is False


# ---------------------------------------------------------------------------
# 补充用例 3: 从未获取过汇率，本次刷新也失败 —— 必须是真正的 unknown，
# CAD_USD 绝不能是那个臆造的 0.73 常量兜底值。
# ---------------------------------------------------------------------------


def test_never_fetched_fx_failure_reports_unknown_with_null_rate(db_session, monkeypatch):
    """unknown FX：从未真实拿到过汇率，本次刷新也失败——fx_status 必须是
    "unknown"，且 exchange_rates.CAD_USD 必须是 None，绝不能让那个 0.73 的
    常量兜底值伪装成一个真实汇率出现在响应里。唯一一笔 CA 仓位因此不是
    美元计价仓位：priced_positions 必须是 0，total_pnl 必须是 None。"""
    seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    rates = out["exchange_rates"]
    total = out["total"]

    assert rates["fx_status"]["CAD_USD"] == "unknown"
    assert rates["CAD_USD"] is None, "the invented 0.73 fallback must never appear in the response"
    assert total["priced_positions"] == 0
    assert total["total_pnl"] is None
    assert total["total_pnl_pct"] is None
    assert total["valuation_complete"] is False


# ---------------------------------------------------------------------------
# 补充用例 4: 有报价但汇率未知的 CA 仓位——"有没有报价"(quoted)与
# "是否美元计价"(priced_positions)必须是两个不同的口径，不能混为一谈。
# ---------------------------------------------------------------------------


def test_quoted_ca_position_with_unknown_fx_is_not_counted_as_priced(db_session, monkeypatch):
    """CA 仓位已经拿到了真实报价（quoted），但汇率未知——它不是"美元计价"仓位:
    `priced_positions` 必须排除它；如果响应里存在 `quoted_positions` 这个"有无
    报价"的独立计数，它必须把这笔仓位算进去。原币种市值/成本必须完整保留。"""
    acc, stock = seed(db_session, monkeypatch)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {"rate": .73, "ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = out["total"]
    pos = out["accounts"][0]["positions"][0]

    assert total["priced_positions"] == 0, "FX-unknown CA position must not count as USD-valued"
    if "quoted_positions" in total:
        assert total["quoted_positions"] == 1, "it DID receive a genuine quote"
    assert pos["cost"] == pytest.approx(100.0), "native cost always survives"
    assert pos["cost_usd"] is None, "no invented 1:1 conversion"
    assert pos["pnl"] is None


# ---------------------------------------------------------------------------
# 补充用例 5: 真实的零价格必须原样透出为已定价，绝不能被当作"无价"漏掉。
# ---------------------------------------------------------------------------


def test_true_zero_price_survives_as_priced_not_missing(db_session, monkeypatch):
    """true zero price：现价恰好为 0.0（真实的零，不是缺失）——该仓位仍然
    必须是 priced=True/valuation_status="priced"，market_value 必须是 0.0
    且 `is not None`，pnl 必须按 (0-cost)*qty 精确计算（全额浮亏），不能因为
    `if current_price` 之类的真值判断把它错当成"没有报价"。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0)
    stock = _make_stock(db_session, symbol="ZERO", name="ZeroCo", market="US")
    _make_position(db_session, account=acc, stock=stock, cost_price=10.0, quantity=5)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("US", "ZERO"): {"current_price": 0.0, "prev_close": None, "change_pct": None},
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]

    assert pos["priced"] is True
    assert pos["valuation_status"] == "priced"
    assert pos["market_value"] == 0.0
    assert pos["market_value"] is not None
    assert pos["pnl"] == pytest.approx(-50.0)


# ---------------------------------------------------------------------------
# 补充用例 6: 真实的零盈亏必须原样透出为 0.0，且聚合层面的"0.0"与
# "没有任何美元计价仓位时的 None"必须能被区分开。
# ---------------------------------------------------------------------------


def test_true_zero_gain_survives_and_is_not_confused_with_no_priced_positions(db_session, monkeypatch):
    """true zero gain：现价恰好等于成本价，单笔仓位盈亏必须是 0.0（`is not None`）
    ——聚合层面的 total_pnl 也必须是 0.0，而不是被误判成"没有任何美元计价仓位"
    时该出现的 None。这两种"看起来都是 0/None"的情形绝不能被混淆。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0)
    stock = _make_stock(db_session, symbol="FLAT", name="FlatCo", market="US")
    _make_position(db_session, account=acc, stock=stock, cost_price=100.0, quantity=10)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("US", "FLAT"): {"current_price": 100.0, "prev_close": None, "change_pct": None},
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = out["total"]
    pos = out["accounts"][0]["positions"][0]

    assert pos["pnl"] == 0.0
    assert pos["pnl"] is not None
    assert pos["pnl_pct"] == 0.0
    assert pos["pnl_pct"] is not None
    assert total["total_pnl"] == 0.0
    assert total["total_pnl"] is not None
    assert total["priced_positions"] == 1  # this is what distinguishes it from the "nothing priced" None case


# ---------------------------------------------------------------------------
# 补充用例 7: 日行情缺失（没有 prev_close）——daily_pnl 必须是 None，
# 绝不能抛异常或臆造一个数字，且不能影响该仓位本身"已定价"的状态。
# ---------------------------------------------------------------------------


def test_missing_daily_quote_prev_close_yields_null_daily_pnl_not_error(db_session, monkeypatch):
    """missing daily quote data：报价里有现价，但没有昨收（prev_close 缺失/为
    None）——daily_pnl/daily_pnl_pct 必须是 None，不能抛异常，且该仓位仍然
    priced=True（现价本身是有的，只是缺日内基准）。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0)
    stock = _make_stock(db_session, symbol="NODAILY", name="NoDailyCo", market="US")
    _make_position(db_session, account=acc, stock=stock, cost_price=50.0, quantity=2)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("US", "NODAILY"): {"current_price": 60.0, "change_pct": None},  # prev_close 键完全缺失
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]

    assert pos["priced"] is True
    assert pos["daily_pnl"] is None
    assert pos["daily_pnl_pct"] is None
    assert pos["pnl"] == pytest.approx(20.0)  # (60-50)*2, unaffected by the missing daily baseline


# ---------------------------------------------------------------------------
# 补充用例 8: 报价值非法(非有限数/负数)必须在估值边界被当作"不可用"，
# 绝不能被当成一个真实价格参与计算或让服务端抛异常。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_price",
    [float("nan"), float("inf"), float("-inf"), -5.0],
    ids=["nan", "inf", "neg_inf", "negative"],
)
def test_malformed_non_finite_or_negative_price_is_treated_as_unavailable(db_session, monkeypatch, bad_price):
    """malformed/non-finite/negative quote values：现价是 NaN/Infinity/负数
    等非法值时，该仓位必须被当作"不可用"处理（valuation_status != "priced"，
    priced=False，market_value/pnl 均为 None），绝不能把非法值当成真实价格
    参与任何计算，也绝不能让服务端抛异常。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db_session, available_funds=0)
    stock = _make_stock(db_session, symbol="BAD", name="BadCo", market="US")
    _make_position(db_session, account=acc, stock=stock, cost_price=10.0, quantity=1)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("US", "BAD"): {"current_price": bad_price, "prev_close": None, "change_pct": None},
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = out["total"]
    pos = out["accounts"][0]["positions"][0]

    assert pos["priced"] is False
    assert pos["valuation_status"] != "priced"
    assert pos["market_value"] is None
    assert pos["pnl"] is None
    assert pos["cost"] == pytest.approx(10.0)  # native cost still survives
    assert total["priced_positions"] == 0
    # No NaN/Infinity may leak into any numeric total field.
    for key, value in total.items():
        if isinstance(value, float):
            assert math.isfinite(value), f"total[{key!r}] is non-finite: {value!r}"


# ---------------------------------------------------------------------------
# 补充用例 9: 正常的美加混合组合 —— 汇率已知、两笔仓位都已美元计价时，
# 端到端的健康路径必须精确无误。
# ---------------------------------------------------------------------------


def test_normal_mixed_us_and_ca_portfolio_computes_correctly(db_session, monkeypatch):
    """normal mixed US+CA portfolio：汇率已知（非过期）、一笔 US 仓位 + 一笔 CA
    仓位都拿到了真实报价——两笔仓位都应计入 priced_positions，
    valuation_complete 必须是 True，total_pnl 必须精确等于两笔仓位盈亏之和
    （CA 那笔按已知汇率折算），exchange_rates 必须标注 fx_status=known。"""
    _fx_known(monkeypatch, rate=0.75)
    acc = _make_account(db_session, available_funds=200.0)
    us_stock = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    ca_stock = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    _make_position(db_session, account=acc, stock=us_stock, cost_price=100.0, quantity=10)  # cost 1000
    _make_position(db_session, account=acc, stock=ca_stock, cost_price=60.0, quantity=20)  # cost 1200 CAD

    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("US", "AAPL"): {"current_price": 110.0, "prev_close": None, "change_pct": None},  # pnl +100 USD
        ("CA", "SHOP.TO"): {"current_price": 65.0, "prev_close": None, "change_pct": None},  # native pnl +100 CAD
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    total = out["total"]
    rates = out["exchange_rates"]

    assert total["priced_positions"] == 2
    assert total["valuation_complete"] is True
    assert total["pnl_basis"] == "complete"
    # US pnl +100 (rate 1.0) + CA pnl (65-60)*20*0.75 = +75 USD => +175 total.
    assert total["total_pnl"] == pytest.approx(100.0 + 75.0)
    assert rates["fx_status"]["CAD_USD"] == "known"
    assert rates["CAD_USD"] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 仪表盘敞口:未启用市场与未知汇率都不得被 1:1 折算进 invested_cost
# ---------------------------------------------------------------------------


def test_dashboard_invested_cost_excludes_unconvertible_and_retired_markets(db_session, monkeypatch):
    """dashboard 的 invested_cost 只能包含"币种可换算"的持仓:退市市场(CN)绝不能按
    1:1 计入,汇率未知的 CA 也不能计入;两者都必须计入 excluded 计数并把
    invested_cost_complete 置为 False,而原币种成本在组合页保持不变。"""
    import src.web.api.dashboard as dash
    import src.web.api.accounts as accounts_api

    acc = _make_account(db_session, available_funds=0.0)
    us = _make_stock(db_session, symbol="AAPL", name="Apple", market="US")
    ca = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    cn = _make_stock(db_session, symbol="600519", name="Moutai", market="CN")
    _make_position(db_session, account=acc, stock=us, cost_price=180.0, quantity=50)   # 9000 USD
    _make_position(db_session, account=acc, stock=ca, cost_price=90.0, quantity=30)    # 2700 CAD
    _make_position(db_session, account=acc, stock=cn, cost_price=1500.0, quantity=5)   # 7500 CNY

    # FX never obtainable => CA is unconvertible; CN is retired regardless of FX.
    monkeypatch.setattr(accounts_api, "_cad_usd_rate_cache", {"rate": 0.73, "ts": 0, "known": False})
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(accounts_api, "_fetch_cad_usd_macro", lambda: None)

    snapshot = accounts_api.resolve_fx_snapshot()
    assert snapshot.status == "unknown"

    # Only the USD position may contribute; 7500 (CN at 1:1) and 2700 (CA, no FX) must not.
    total = 0.0
    excluded = 0
    for market, cost in (("US", 9000.0), ("CA", 2700.0), ("CN", 7500.0)):
        rate = accounts_api.fx_rate_for_market(market, snapshot) if dash.is_enabled(market) else None
        if rate is None:
            excluded += 1
            continue
        total += cost * rate

    assert total == pytest.approx(9000.0), "only the USD position is convertible"
    assert total != pytest.approx(31800.0), "CN must not be folded in at 1:1"
    assert excluded == 2


# ---------------------------------------------------------------------------
# Revision 7 —— DAILY BASIS 契约:`prev_close` 是唯一的、权威的日内盈亏基准。
# 服务端必须在 summary 的 `quotes` map、每条 position、以及 /quotes/batch 行里原样
# 透出 `prev_close`(有限且 > 0,否则 null);daily_pnl 只能由 prev_close 计算,绝不
# 从四舍五入过的 change_pct 反推;真实的 0 现价是有效数据(-100% 的一天),不是"未知"。
# ---------------------------------------------------------------------------


def _one_us_position(db, monkeypatch, *, quote: dict, cost_price: float = 100.0, quantity: int = 1):
    """一笔 US 仓位 + 指定报价;汇率 known(US 无需换算,但 exchange_rates 块仍需要快照)。"""
    _fx_known(monkeypatch, rate=1.0)
    acc = _make_account(db, available_funds=0.0)
    stock = _make_stock(db, symbol="DAILY", name="DailyCo", market="US")
    _make_position(db, account=acc, stock=stock, cost_price=cost_price, quantity=quantity)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({("US", "DAILY"): quote}))
    return acc, stock


def test_summary_quotes_map_carries_prev_close_as_canonical_daily_basis(db_session, monkeypatch):
    """summary 的 `quotes["MARKET:SYMBOL"]` 必须恰好包含 current_price / change_pct /
    prev_close 三个键,且 prev_close 原样透出(有限且 > 0),供客户端直接作为日内基准。"""
    _one_us_position(db_session, monkeypatch, quote={"current_price": 101.0, "prev_close": 99.5, "change_pct": 1.51})

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    entry = out["quotes"]["US:DAILY"]

    assert set(entry.keys()) == {"current_price", "change_pct", "prev_close"}
    assert entry["prev_close"] == pytest.approx(99.5)
    assert entry["current_price"] == pytest.approx(101.0)
    assert entry["change_pct"] == pytest.approx(1.51)


def test_rounded_change_pct_is_never_used_to_reconstruct_daily_basis(db_session, monkeypatch):
    """rounded change_pct:供应商给的 change_pct 已四舍五入到 0.0(实际 +0.0049%),
    若从 change_pct 反推昨收会得到 daily_pnl = 0;服务端必须用真实 prev_close 计算
    出 +0.49,且把真实 prev_close 原样透出给客户端。"""
    _one_us_position(
        db_session,
        monkeypatch,
        quote={"current_price": 100.0, "prev_close": 99.9951, "change_pct": 0.0},
        cost_price=90.0,
        quantity=100,
    )

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]

    assert pos["prev_close"] == pytest.approx(99.9951)
    assert pos["daily_pnl"] == pytest.approx((100.0 - 99.9951) * 100, abs=0.006)  # +0.49, not 0
    assert pos["daily_pnl"] != 0.0
    assert pos["daily_pnl_pct"] == pytest.approx((100.0 - 99.9951) / 99.9951 * 100, abs=0.006)
    assert out["quotes"]["US:DAILY"]["prev_close"] == pytest.approx(99.9951)
    assert out["total"]["total_daily_pnl"] == pytest.approx(0.49, abs=0.006)
    assert out["total"]["daily_pnl_complete"] is True


def test_true_zero_price_with_prev_close_is_a_valid_minus_100_percent_day(db_session, monkeypatch):
    """true zero price(Codex `zero` fixture):cost 100 x1,现价 0,昨收 100,
    change_pct -100 —— 服务端 daily_pnl 必须是 -100(不是 null,不是 0),
    daily_pnl_pct -100,total_daily_pnl -100,daily_pnl_complete True;quotes map 里
    current_price 是 0.0、prev_close 是 100.0,客户端据此能得到同样的 -100。"""
    _one_us_position(
        db_session,
        monkeypatch,
        quote={"current_price": 0, "prev_close": 100, "change_pct": -100},
        cost_price=100.0,
        quantity=1,
    )

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]
    entry = out["quotes"]["US:DAILY"]

    assert pos["priced"] is True
    assert pos["current_price"] == 0.0
    assert pos["prev_close"] == pytest.approx(100.0)
    assert pos["daily_pnl"] == pytest.approx(-100.0)
    assert pos["daily_pnl"] is not None
    assert pos["daily_pnl_pct"] == pytest.approx(-100.0)
    assert total["total_daily_pnl"] == pytest.approx(-100.0)
    assert total["daily_pnl_positions"] == 1
    assert total["daily_pnl_complete"] is True
    assert entry["current_price"] == 0.0
    assert entry["prev_close"] == pytest.approx(100.0)


def test_genuine_zero_daily_pnl_survives_as_zero_not_null(db_session, monkeypatch):
    """genuine zero daily P&L:现价恰好等于昨收 —— daily_pnl 必须是 0.0 且
    `is not None`,total_daily_pnl 也是 0.0(不是"没有日内数据"时的 None),
    daily_pnl_complete True。"""
    _one_us_position(
        db_session,
        monkeypatch,
        quote={"current_price": 100.0, "prev_close": 100.0, "change_pct": 0.0},
        cost_price=80.0,
        quantity=3,
    )

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]

    assert pos["daily_pnl"] == 0.0
    assert pos["daily_pnl"] is not None
    assert pos["daily_pnl_pct"] == 0.0
    assert total["total_daily_pnl"] == 0.0
    assert total["total_daily_pnl"] is not None
    assert total["daily_pnl_positions"] == 1
    assert total["daily_pnl_complete"] is True
    # Unrealized P&L is unaffected: (100-80)*3.
    assert pos["pnl"] == pytest.approx(60.0)


@pytest.mark.parametrize(
    "quote",
    [
        {"current_price": 60.0},  # prev_close key entirely absent
        {"current_price": 60.0, "prev_close": None, "change_pct": 2.0},
        {"current_price": 60.0, "prev_close": 0, "change_pct": None},
        {"current_price": 60.0, "prev_close": -1.0, "change_pct": None},
        {"current_price": 60.0, "prev_close": float("nan"), "change_pct": None},
        {"current_price": 60.0, "prev_close": float("inf"), "change_pct": None},
        {"current_price": 60.0, "prev_close": "n/a", "change_pct": None},
    ],
    ids=["absent", "null", "zero", "negative", "nan", "inf", "non_numeric"],
)
def test_missing_or_unusable_prev_close_yields_null_daily_pnl_never_fabricated(db_session, monkeypatch, quote):
    """missing previous close:prev_close 缺失/None/0/负数/NaN/inf/非数值时,
    该仓位 daily_pnl / daily_pnl_pct 必须是 None,quotes map 与 position 里的
    prev_close 必须是 None,total_daily_pnl 必须是 None(不是臆造的 0),
    daily_pnl_complete False;仓位本身仍然 priced(现价是有的)。"""
    _one_us_position(db_session, monkeypatch, quote=quote, cost_price=50.0, quantity=2)

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]

    assert pos["priced"] is True
    assert pos["prev_close"] is None
    assert pos["daily_pnl"] is None
    assert pos["daily_pnl_pct"] is None
    assert out["quotes"]["US:DAILY"]["prev_close"] is None
    assert total["total_daily_pnl"] is None
    assert total["daily_pnl_positions"] == 0
    assert total["daily_pnl_complete"] is False
    assert pos["pnl"] == pytest.approx(20.0)  # unrealized P&L unaffected


def test_absent_daily_data_with_quotes_disabled_leaves_everything_unknown(db_session, monkeypatch):
    """absent daily data / stale price:include_quotes=False 时服务端没有任何本轮报价
    —— `quotes` 必须是空 dict(客户端据此保留自己的 last_known 价格,而不是把
    null 当作"供应商说不可用"),position 不能被定价,daily_pnl 为 None,
    total_daily_pnl 为 None,绝不臆造 0。"""
    _one_us_position(db_session, monkeypatch, quote={"current_price": 60.0, "prev_close": 59.0, "change_pct": 1.7})

    out = api.get_portfolio_summary(account_id=None, include_quotes=False, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]

    assert out["quotes"] == {}
    assert pos["priced"] is False
    assert pos["price_status"] == "unavailable"
    assert pos["current_price"] is None
    assert pos["prev_close"] is None
    assert pos["daily_pnl"] is None
    assert total["total_daily_pnl"] is None
    assert total["daily_pnl_complete"] is False


def test_nan_change_pct_never_leaks_into_serialized_quotes(db_session, monkeypatch):
    """malformed change_pct:供应商 change_pct 为 NaN 时,summary 的 quotes map 与
    position 里必须序列化为 None(NaN 不是合法 JSON),而 daily_pnl 仍由真实
    prev_close 正常计算,不受 change_pct 污染。"""
    _one_us_position(
        db_session,
        monkeypatch,
        quote={"current_price": 102.0, "prev_close": 100.0, "change_pct": float("nan")},
        cost_price=100.0,
        quantity=5,
    )

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]

    assert pos["change_pct"] is None
    assert out["quotes"]["US:DAILY"]["change_pct"] is None
    assert pos["daily_pnl"] == pytest.approx(10.0)
    assert pos["daily_pnl_pct"] == pytest.approx(2.0)


def test_stale_fx_still_yields_daily_pnl_but_valuation_is_not_complete(db_session, monkeypatch):
    """stale FX:CA 仓位在 last_known(真实但过期)汇率下,daily_pnl 仍按该汇率折算
    成 USD(数据是真实的,只是过期),daily_pnl_complete True;但 valuation_complete
    必须是 False,fx_status 报告 last_known,绝不把过期汇率包装成 fresh。"""
    monkeypatch.setattr(api, "_fx_refresh_inflight", False)
    monkeypatch.setattr(api, "_cad_usd_rate_cache", {
        "rate": .7, "ts": 0, "known": True, "genuine": True, "fetched_at": 1000, "source": "Bank of Canada",
    })
    monkeypatch.setattr(api, "_fetch_cad_usd_boc", lambda: None)
    monkeypatch.setattr(api, "_fetch_cad_usd_macro", lambda: None)
    acc = _make_account(db_session, available_funds=0.0)
    stock = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    _make_position(db_session, account=acc, stock=stock, cost_price=100.0, quantity=10)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("CA", "SHOP.TO"): {"current_price": 110.0, "prev_close": 105.0, "change_pct": 4.76},
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]
    rates = out["exchange_rates"]

    assert rates["fx_status"]["CAD_USD"] == "last_known"
    assert rates["fx_as_of"]["CAD_USD"] == 1000
    assert rates["fx_source"]["CAD_USD"] == "Bank of Canada"
    assert pos["fx_status"] == "last_known"
    assert pos["prev_close"] == pytest.approx(105.0)
    assert pos["daily_pnl"] == pytest.approx((110.0 - 105.0) * 10 * 0.7)  # 35.0 USD at the stale rate
    assert total["daily_pnl_complete"] is True
    assert total["valuation_complete"] is False
    assert total["pnl_basis"] == "last_known"


def test_unknown_fx_ca_position_has_null_daily_pnl_and_incomplete_daily_coverage(db_session, monkeypatch):
    """unknown FX:CA 仓位有真实报价和昨收,但汇率未知 —— USD 口径的 daily_pnl 必须
    是 None(不能 1:1 臆造),prev_close 仍原样透出(原币种数据是真实的),
    daily_pnl_complete False,total_daily_pnl None。"""
    _fx_unknown(monkeypatch)
    acc = _make_account(db_session, available_funds=0.0)
    stock = _make_stock(db_session, symbol="SHOP.TO", name="Shopify", market="CA")
    _make_position(db_session, account=acc, stock=stock, cost_price=100.0, quantity=10)
    monkeypatch.setattr(api, "_fetch_quotes_for_stocks", _quote_stub({
        ("CA", "SHOP.TO"): {"current_price": 110.0, "prev_close": 105.0, "change_pct": 4.76},
    }))

    out = api.get_portfolio_summary(account_id=None, include_quotes=True, db=db_session)
    pos = out["accounts"][0]["positions"][0]
    total = out["total"]

    assert pos["quoted"] is True
    assert pos["priced"] is False
    assert pos["prev_close"] == pytest.approx(105.0)
    assert pos["daily_pnl"] is None
    assert out["quotes"]["CA:SHOP.TO"]["prev_close"] == pytest.approx(105.0)
    assert total["total_daily_pnl"] is None
    assert total["daily_pnl_complete"] is False


# ---------------------------------------------------------------------------
# /quotes/batch 行:prev_close 遵守同一道日内基准边界(有限且 > 0,否则 null)。
# 纯函数 `_quote_to_response`,不触发任何网络。
# ---------------------------------------------------------------------------


def test_quotes_batch_row_carries_prev_close_and_keeps_true_zero_price():
    """/quotes/batch 行:prev_close 100 与 current_price 0 必须都原样透出(真实的
    -100% 一天),行键集合包含 prev_close。"""
    import src.web.api.quotes as quotes_api

    row = quotes_api._quote_to_response(
        "ZERO", "US", {"current_price": 0.0, "prev_close": 100.0, "change_pct": -100.0},
        supported=True, unsupported_reason=None,
    )
    assert row["prev_close"] == pytest.approx(100.0)
    assert row["current_price"] == 0.0
    assert row["change_pct"] == pytest.approx(-100.0)
    assert {"symbol", "market", "current_price", "change_pct", "prev_close", "supported"} <= set(row)


@pytest.mark.parametrize(
    "bad_prev",
    [None, 0, 0.0, -3.0, float("nan"), float("inf"), "n/a", True],
    ids=["none", "zero_int", "zero_float", "negative", "nan", "inf", "non_numeric", "bool"],
)
def test_quotes_batch_row_prev_close_is_null_when_unusable(bad_prev):
    """/quotes/batch 行:prev_close 缺失/0/负数/NaN/inf/非数值/布尔 => None,
    绝不把一个不能当除数的值当作日内基准送给客户端。"""
    import src.web.api.quotes as quotes_api

    row = quotes_api._quote_to_response(
        "X", "US", {"current_price": 10.0, "prev_close": bad_prev, "change_pct": None},
        supported=True, unsupported_reason=None,
    )
    assert row["prev_close"] is None
    assert row["current_price"] == pytest.approx(10.0)

    missing = quotes_api._quote_to_response("X", "US", None, supported=True, unsupported_reason=None)
    assert missing["prev_close"] is None
