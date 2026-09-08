"""Financial statement text for the TradingAgents fundamentals analyst.

The agent's ``collect()`` pulls one ``marketdata.Fundamentals`` row per symbol (Yahoo Finance
valuation / TTM figures, SEC EDGAR XBRL facts for US filers). ``build_financial_from_fundamentals``
flattens that row into the dict shape the toolkit adapter caches under ``financial``; the
``render_*`` helpers turn it into the text served for ``get_fundamentals`` /
``get_income_statement`` / ``get_balance_sheet`` / ``get_cashflow`` tool calls.

Conventions:
- ``None`` fields are omitted from ``indicators`` (never rendered as a fake number);
  renderers state explicitly which metrics are unavailable from the source.
- Monetary values are absolute native currency (``currency`` field; USD / CAD).
- Percent fields are already in percent units (``roe=12.5`` means 12.5%).
"""

from __future__ import annotations

from typing import Any

SOURCE_HEADER = "[Financial data from TickerKeep (Yahoo Finance / SEC EDGAR)]"

# (indicator label, Fundamentals attribute, kind) — kind: "money" | "pct" | "ratio" | "count"
_INDICATOR_SPECS: tuple[tuple[str, str, str], ...] = (
    ("Total Revenue", "revenue", "money"),
    ("Net Income", "net_profit", "money"),
    ("EPS (TTM)", "eps", "ratio"),
    ("Book Value / Share", "bps", "ratio"),
    ("Gross Margin (%)", "gross_margin", "pct"),
    ("Net Margin (%)", "net_margin", "pct"),
    ("ROE (%)", "roe", "pct"),
    ("Revenue YoY (%)", "revenue_yoy", "pct"),
    ("Net Income YoY (%)", "net_profit_yoy", "pct"),
    ("Market Cap", "total_market_value", "money"),
    ("P/E (TTM)", "pe_ttm", "ratio"),
    ("Forward P/E", "pe_static", "ratio"),
    ("P/B", "pb", "ratio"),
    ("P/S (TTM)", "ps_ttm", "ratio"),
    ("Dividend Yield (%)", "dividend_yield", "pct"),
    ("Shares Outstanding", "total_shares", "count"),
    ("Float", "float_shares", "count"),
    ("Operating Cash Flow", "operating_cash_flow", "money"),
    ("Free Cash Flow", "free_cash_flow", "money"),
    ("Total Debt", "total_debt", "money"),
    ("Total Cash", "total_cash", "money"),
)

_KIND_BY_LABEL: dict[str, str] = {label: kind for label, _attr, kind in _INDICATOR_SPECS}

_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Income": ("Total Revenue", "Net Income", "EPS (TTM)", "Gross Margin (%)", "Net Margin (%)"),
    "Growth": ("Revenue YoY (%)", "Net Income YoY (%)"),
    "Profitability": ("ROE (%)", "Gross Margin (%)", "Net Margin (%)"),
    "Valuation": ("Market Cap", "P/E (TTM)", "Forward P/E", "P/B", "P/S (TTM)", "Dividend Yield (%)"),
    "Balance Sheet": ("Book Value / Share", "Total Debt", "Total Cash", "Shares Outstanding", "Float"),
    "Cash Flow": ("Operating Cash Flow", "Free Cash Flow"),
}


def empty_financial() -> dict[str, Any]:
    """The "unavailable" shape: same keys as a populated dict, nothing inside."""
    return {"periods": [], "currency": "", "indicators": {}, "categories": {}}


def has_financial_data(data: dict | None) -> bool:
    """True when ``data`` carries at least one indicator value."""
    return bool(data) and bool(data.get("indicators"))


def build_financial_from_fundamentals(f: Any) -> dict[str, Any]:
    """Flatten a ``marketdata.Fundamentals`` row (or ``None``) into the cached ``financial`` dict.

    Returns::

        {
            "periods": ["2025-06-28"],          # f.report_date, or "TTM" when unknown
            "currency": "USD",
            "indicators": {"Total Revenue": {"2025-06-28": 391035000000.0}, ...},
            "categories": {"Income": {...}, "Valuation": {...}, ...},
        }

    Indicators whose source value is ``None`` are omitted. ``None`` input -> ``empty_financial()``.
    """
    if f is None:
        return empty_financial()
    period = str(getattr(f, "report_date", "") or "").strip() or "TTM"
    currency = str(getattr(f, "currency", "") or "").strip().upper()

    indicators: dict[str, dict[str, float]] = {}
    for label, attr, _kind in _INDICATOR_SPECS:
        v = getattr(f, attr, None)
        if v is None or isinstance(v, bool):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv != fv:  # NaN
            continue
        indicators[label] = {period: fv}

    categories: dict[str, dict[str, dict[str, float]]] = {}
    for cat, labels in _CATEGORIES.items():
        rows = {lbl: indicators[lbl] for lbl in labels if lbl in indicators}
        if rows:
            categories[cat] = rows

    return {
        "periods": [period],
        "currency": currency,
        "indicators": indicators,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_money(v: float, currency: str) -> str:
    av = abs(v)
    if av >= 1e12:
        s = f"{v / 1e12:.2f}T"
    elif av >= 1e9:
        s = f"{v / 1e9:.2f}B"
    elif av >= 1e6:
        s = f"{v / 1e6:.2f}M"
    elif av >= 1e3:
        s = f"{v / 1e3:.2f}K"
    else:
        s = f"{v:.2f}"
    return f"{s} {currency}".strip()


def _fmt_count(v: float) -> str:
    av = abs(v)
    if av >= 1e9:
        return f"{v / 1e9:.2f}B"
    if av >= 1e6:
        return f"{v / 1e6:.2f}M"
    if av >= 1e3:
        return f"{v / 1e3:.2f}K"
    return f"{v:,.0f}"


def _fmt_value(label: str, v: float, currency: str) -> str:
    kind = _KIND_BY_LABEL.get(label, "ratio")
    if kind == "money":
        return _fmt_money(v, currency)
    if kind == "pct":
        return f"{v:.2f}%"
    if kind == "count":
        return _fmt_count(v)
    return f"{v:.2f}"


def _period_line(data: dict) -> str:
    periods = [str(p) for p in (data.get("periods") or []) if p]
    return f"Reporting period: {' | '.join(periods) if periods else 'n/a'}"


def _render_rows(data: dict, labels: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Return (rendered lines for available labels, missing labels)."""
    ind = data.get("indicators") or {}
    periods = [str(p) for p in (data.get("periods") or [])]
    currency = str(data.get("currency") or "")
    lines: list[str] = []
    missing: list[str] = []
    for label in labels:
        vals = ind.get(label)
        if not vals:
            missing.append(label)
            continue
        cells = []
        for p in periods or list(vals.keys()):
            v = vals.get(p)
            cells.append(_fmt_value(label, float(v), currency) if v is not None else "n/a")
        lines.append(f"- {label}: {' | '.join(cells)}")
    return lines, missing


def _missing_line(missing: list[str]) -> str:
    return f"Not available from this source: {', '.join(missing)}" if missing else ""


def _unavailable(section: str) -> str:
    return f"{SOURCE_HEADER}\n[No {section} data available for this symbol. Do NOT invent numbers.]"


def render_fundamentals_summary(data: dict | None) -> str:
    """Overview served for ``get_fundamentals``: income, growth, profitability, valuation."""
    if not has_financial_data(data):
        return _unavailable("financial")
    assert data is not None
    lines = [SOURCE_HEADER, _period_line(data)]
    if data.get("currency"):
        lines.append(f"Currency: {data['currency']}")
    lines.append("")
    for cat in ("Income", "Growth", "Profitability", "Valuation", "Balance Sheet", "Cash Flow"):
        rows, _missing = _render_rows(data, _CATEGORIES[cat])
        if rows:
            lines.append(f"{cat}:")
            lines.extend(rows)
    all_labels = tuple(label for label, _a, _k in _INDICATOR_SPECS)
    _rows, missing = _render_rows(data, all_labels)
    if missing:
        lines.append("")
        lines.append(_missing_line(missing))
    lines.append("")
    lines.append(
        "Note: These are REAL figures from Yahoo Finance / SEC EDGAR (TTM or latest reported "
        "period). Ground revenue, profitability, leverage and valuation claims in these numbers. "
        "Do NOT invent additional numbers."
    )
    return "\n".join(lines)


def render_income_statement(data: dict | None) -> str:
    """Text served for ``get_income_statement``."""
    if not has_financial_data(data):
        return _unavailable("income statement")
    assert data is not None
    lines = [SOURCE_HEADER, "Income statement (TTM / latest reported period)", _period_line(data), ""]
    rows, missing = _render_rows(
        data,
        ("Total Revenue", "Net Income", "EPS (TTM)", "Gross Margin (%)", "Net Margin (%)",
         "Revenue YoY (%)", "Net Income YoY (%)"),
    )
    lines.extend(rows)
    if missing:
        lines.append(_missing_line(missing))
    lines.append(
        "Line items not provided by this source: operating income, interest expense, income tax. "
        "Do NOT invent them."
    )
    return "\n".join(lines)


def render_balance_sheet(data: dict | None) -> str:
    """Text served for ``get_balance_sheet``. States what the source does not carry."""
    if not has_financial_data(data):
        return _unavailable("balance sheet")
    assert data is not None
    lines = [SOURCE_HEADER, "Balance sheet snapshot (latest available)", _period_line(data), ""]
    rows, missing = _render_rows(
        data,
        ("Book Value / Share", "Total Debt", "Total Cash", "Shares Outstanding", "Float", "ROE (%)", "P/B"),
    )
    lines.extend(rows)
    if missing:
        lines.append(_missing_line(missing))
    lines.append(
        "Not available from this source: total assets, total liabilities, shareholders' equity, "
        "current ratio, goodwill. Do NOT invent them — reason about leverage from Total Debt vs "
        "Total Cash and book value per share only."
    )
    return "\n".join(lines)


def render_cashflow(data: dict | None) -> str:
    """Text served for ``get_cashflow``. States what the source does not carry."""
    if not has_financial_data(data):
        return _unavailable("cash flow")
    assert data is not None
    lines = [SOURCE_HEADER, "Cash flow statement (TTM)", _period_line(data), ""]
    rows, missing = _render_rows(data, ("Operating Cash Flow", "Free Cash Flow", "Net Income"))
    lines.extend(rows)
    if missing:
        lines.append(_missing_line(missing))
    lines.append(
        "Not available from this source: capital expenditure, investing cash flow, financing cash "
        "flow, dividends paid, buybacks. Do NOT invent them."
    )
    return "\n".join(lines)
