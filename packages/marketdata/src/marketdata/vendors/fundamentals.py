"""Fundamentals vendors: Yahoo ``Ticker.info`` (US, CA) and SEC EDGAR XBRL companyfacts (US).

Both emit ``Fundamentals`` with absolute native-currency money fields (``currency`` says
which). A field the source does not carry stays ``None``; nothing is fabricated. Per-symbol
failures never kill the batch: the symbol is skipped and the rest still returns.

``YFinanceFundamentalsVendor`` reads the 6 h-cached ``yf_adapter.info`` mapping, so the quote
vendor, holders vendor and this one share one ``Ticker.info`` download.

``SecEdgarFundamentalsVendor`` derives the reporting fields from the latest 10-K / 10-Q facts.
Valuation fields (P/E, P/B, market cap, ...) need a price and are left ``None`` here; the
Engine ranks this vendor below Yahoo so it only fills the reporting half when Yahoo is down.
ROE is computed from the latest period net income over period-end equity: for a 10-Q that is
a quarterly figure over an instant balance, i.e. a period mismatch that callers must treat as
indicative only (annualise with care).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import Fundamentals
from marketdata.vendors import _sec, yf_adapter
from marketdata.vendors.base import FundamentalsVendor

logger = logging.getLogger(__name__)


def _f(v: Any) -> float | None:
    """Lenient float: ``None``/NaN/inf/non-numeric -> ``None``."""
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return fv


def _pct(v: Any) -> float | None:
    """Ratio (0.25) -> percent (25.0)."""
    fv = _f(v)
    return None if fv is None else fv * 100.0


def _ratio_pct(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den * 100.0


def _yoy_pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100.0


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------


class YFinanceFundamentalsVendor(FundamentalsVendor):
    name = "yfinance"
    supports_markets = {"US", "CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        yf_adapter.apply_proxy(config)
        out: list[Fundamentals] = []
        for sym in symbols:
            try:
                item = self._fetch_one(sym)
            except VendorError as e:
                logger.info(f"[fundamentals/yfinance] {sym.code}: {e}")
                continue
            if item is not None:
                out.append(item)
        return out

    def _fetch_one(self, sym: Symbol) -> Fundamentals | None:
        ysym = yf_adapter.to_yahoo_symbol(sym)
        info = yf_adapter.info(ysym)
        if not info:
            return None
        return self.map_info(sym, info)

    @staticmethod
    def map_info(sym: Symbol, info: dict) -> Fundamentals | None:
        """``Ticker.info`` -> ``Fundamentals``; ``None`` when the mapping carries neither a
        price nor a market cap (delisted / unknown symbol)."""
        price = _f(info.get("regularMarketPrice"))
        market_cap = _f(info.get("marketCap"))
        if price is None and market_cap is None:
            return None
        if price is None:
            price = _f(info.get("currentPrice"))

        float_shares = _f(info.get("floatShares"))
        circulating = float_shares * price if (float_shares is not None and price is not None) else None

        dividend_yield: float | None = None
        annual_rate = _f(info.get("trailingAnnualDividendRate"))
        if annual_rate is not None and price:
            dividend_yield = annual_rate / price * 100.0
        else:
            dividend_yield = _f(info.get("dividendYield"))

        report_date = ""
        mrq = info.get("mostRecentQuarter")
        if mrq not in (None, "", 0):
            dt = yf_adapter.to_utc(mrq)
            if dt.year > 1970:
                report_date = dt.strftime("%Y-%m-%d")

        return Fundamentals(
            symbol=sym.code,
            market=sym.market.value,
            name=str(info.get("shortName") or info.get("longName") or "").strip(),
            pe_ttm=_f(info.get("trailingPE")),
            pe_static=_f(info.get("forwardPE")),  # documented reuse: forward P/E in the "static" slot
            pb=_f(info.get("priceToBook")),
            ps_ttm=_f(info.get("priceToSalesTrailing12Months")),
            total_market_value=market_cap,
            circulating_market_value=circulating,
            dividend_yield=dividend_yield,
            total_shares=_f(info.get("sharesOutstanding")),
            float_shares=float_shares,
            eps=_f(info.get("trailingEps")),
            bps=_f(info.get("bookValue")),
            roe=_pct(info.get("returnOnEquity")),
            revenue=_f(info.get("totalRevenue")),
            net_profit=_f(info.get("netIncomeToCommon")),
            gross_margin=_pct(info.get("grossMargins")),
            net_margin=_pct(info.get("profitMargins")),
            revenue_yoy=_pct(info.get("revenueGrowth")),
            net_profit_yoy=_pct(info.get("earningsGrowth")),
            report_date=report_date,
            currency=str(info.get("currency") or "").strip().upper(),
            operating_cash_flow=_f(info.get("operatingCashflow")),
            free_cash_flow=_f(info.get("freeCashflow")),
            total_debt=_f(info.get("totalDebt")),
            total_cash=_f(info.get("totalCash")),
        )


# ---------------------------------------------------------------------------
# SEC EDGAR XBRL companyfacts
# ---------------------------------------------------------------------------

_REPORT_FORMS = {"10-K", "10-Q"}
_REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
_EPS_CONCEPTS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")


def _parse_date(s: Any) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _fact_entries(facts: dict, taxonomy: str, concept: str) -> list[dict]:
    """All unit entries for ``taxonomy/concept`` flattened across units (USD, shares, USD/shares)."""
    node = ((facts.get(taxonomy) or {}).get(concept) or {}).get("units") or {}
    out: list[dict] = []
    for unit, rows in node.items():
        for row in rows or []:
            if isinstance(row, dict):
                out.append({**row, "_unit": unit})
    return out


def _duration_days(row: dict) -> int | None:
    end = _parse_date(row.get("end"))
    start = _parse_date(row.get("start"))
    if end is None or start is None:
        return None
    return (end - start).days


def _expected_duration(fp: str) -> int:
    return 365 if str(fp or "").upper() == "FY" else 91


def _select_latest(entries: list[dict]) -> dict | None:
    """Latest 10-K/10-Q fact by ``end``; among facts sharing that ``end`` prefer the duration
    matching the fiscal period (a quarter for Qn, a year for FY) so 9-month YTD rows in a Q3
    10-Q are not mistaken for the quarter; ties -> latest ``filed``."""
    cands = [r for r in entries if str(r.get("form") or "").upper() in _REPORT_FORMS and _parse_date(r.get("end"))]
    if not cands:
        return None
    max_end = max(_parse_date(r["end"]) for r in cands)
    same_end = [r for r in cands if _parse_date(r["end"]) == max_end]

    def rank(r: dict) -> tuple:
        dur = _duration_days(r)
        mismatch = 0 if dur is None else abs(dur - _expected_duration(r.get("fp")))
        return (mismatch, -_ordinal(r.get("filed")))

    same_end.sort(key=rank)
    return same_end[0]


def _ordinal(s: Any) -> int:
    d = _parse_date(s)
    return d.toordinal() if d else 0


def _prior_year_value(entries: list[dict], latest: dict) -> float | None:
    """Value of the same fiscal period one year earlier (same ``fp``, ``fy - 1``, comparable
    duration); falls back to the fact whose ``end`` is ~1 year before ``latest.end``."""
    fp = str(latest.get("fp") or "").upper()
    fy = latest.get("fy")
    exp = _duration_days(latest)
    end = _parse_date(latest.get("end"))
    best: tuple[int, float] | None = None
    for r in entries:
        if str(r.get("form") or "").upper() not in _REPORT_FORMS:
            continue
        val = _f(r.get("val"))
        r_end = _parse_date(r.get("end"))
        if val is None or r_end is None or end is None:
            continue
        dur = _duration_days(r)
        if exp is not None and dur is not None and abs(dur - exp) > 20:
            continue
        gap = abs((end - r_end).days - 365)
        if gap > 45:
            continue
        # Prefer the fact that explicitly carries the prior fiscal year / same period.
        score = gap
        if fy is not None and r.get("fy") == (fy - 1 if isinstance(fy, int) else None) and str(r.get("fp") or "").upper() == fp:
            score -= 1000
        if best is None or score < best[0]:
            best = (score, val)
    return best[1] if best else None


class SecEdgarFundamentalsVendor(FundamentalsVendor):
    name = "sec_edgar"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        out: list[Fundamentals] = []
        for sym in symbols:
            cik = _sec.resolve_cik(sym.code, config)
            if not cik:
                logger.info(f"[fundamentals/sec_edgar] {sym.code}: CIK unresolved")
                continue
            payload = _sec.sec_get(_sec.COMPANYFACTS_URL.format(cik=cik), config=config,
                                   host_key="data.sec.gov", symbol=sym.code,
                                   log_label="SEC companyfacts")
            if not isinstance(payload, dict):
                continue
            item = self.map_companyfacts(sym, payload)
            if item is not None:
                out.append(item)
        return out

    @staticmethod
    def map_companyfacts(sym: Symbol, payload: dict) -> Fundamentals | None:
        facts = payload.get("facts") or {}
        if not facts:
            return None

        def latest(taxonomy: str, *concepts: str) -> tuple[dict | None, list[dict]]:
            """Across alternative concepts, the one whose latest 10-K/10-Q fact is most recent wins
            (issuers migrate concepts over time, e.g. ``Revenues`` -> the ASC 606 concept; the
            first-listed concept is preferred only on an equal period end)."""
            best: tuple[dict, list[dict]] | None = None
            for concept in concepts:
                entries = _fact_entries(facts, taxonomy, concept)
                chosen = _select_latest(entries)
                if chosen is None:
                    continue
                if best is None or _parse_date(chosen["end"]) > _parse_date(best[0]["end"]):
                    best = (chosen, entries)
            return best if best else (None, [])

        rev_fact, rev_entries = latest("us-gaap", *_REVENUE_CONCEPTS)
        ni_fact, ni_entries = latest("us-gaap", "NetIncomeLoss")
        eps_fact, _ = latest("us-gaap", *_EPS_CONCEPTS)
        eq_fact, _ = latest("us-gaap", "StockholdersEquity")
        gp_fact, _ = latest("us-gaap", "GrossProfit")
        sh_fact, _ = latest("dei", "EntityCommonStockSharesOutstanding")

        if rev_fact is None and ni_fact is None and eps_fact is None:
            return None

        revenue = _f(rev_fact.get("val")) if rev_fact else None
        net_profit = _f(ni_fact.get("val")) if ni_fact else None
        eps = _f(eps_fact.get("val")) if eps_fact else None
        equity = _f(eq_fact.get("val")) if eq_fact else None
        gross = _f(gp_fact.get("val")) if gp_fact else None
        shares = _f(sh_fact.get("val")) if sh_fact else None

        def same_period(a: dict | None, b: dict | None) -> bool:
            """Ratios are only meaningful over the same reporting period (same ``end``, same duration)."""
            if not a or not b or str(a.get("end") or "")[:10] != str(b.get("end") or "")[:10]:
                return False
            da, db = _duration_days(a), _duration_days(b)
            return da is None or db is None or abs(da - db) <= 20

        bps = (equity / shares) if (equity is not None and shares) else None
        # Period mismatch: period net income (quarter for a 10-Q) over period-end equity.
        roe = _ratio_pct(net_profit, equity) if same_period(ni_fact, eq_fact) or (
            ni_fact and eq_fact and str(ni_fact.get("end"))[:10] == str(eq_fact.get("end"))[:10]) else None
        gross_margin = _ratio_pct(gross, revenue) if same_period(gp_fact, rev_fact) else None
        net_margin = _ratio_pct(net_profit, revenue) if same_period(ni_fact, rev_fact) else None
        revenue_yoy = _yoy_pct(revenue, _prior_year_value(rev_entries, rev_fact)) if rev_fact else None
        net_profit_yoy = _yoy_pct(net_profit, _prior_year_value(ni_entries, ni_fact)) if ni_fact else None

        anchor = rev_fact or ni_fact or eps_fact
        report_date = str(anchor.get("end") or "")[:10] if anchor else ""

        return Fundamentals(
            symbol=sym.code,
            market=sym.market.value,
            name=str(payload.get("entityName") or "").strip(),
            total_shares=shares,
            eps=eps,
            bps=bps,
            roe=roe,
            revenue=revenue,
            net_profit=net_profit,
            gross_margin=gross_margin,
            net_margin=net_margin,
            revenue_yoy=revenue_yoy,
            net_profit_yoy=net_profit_yoy,
            report_date=report_date,
            currency="USD",
        )
