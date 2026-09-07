"""Ownership vendors: Yahoo holders tables (US, CA) and the SEC EDGAR Form 4 Atom feed (US).

Both emit ``HolderItem`` rows of three kinds:

- ``breakdown``   one row per ``major_holders`` line (insiders %, institutions %, institutions
                  float %, institutions count);
- ``institution`` one row per ``institutional_holders`` line;
- ``insider_tx``  one row per insider transaction (Yahoo ``insider_transactions`` or one
                  EDGAR Form 4 filing).

The three Yahoo tables are fetched under separate guards so one failing table never drops
the others; per-symbol failures never kill the batch. NaN cells become ``None``.
"""

from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from typing import Any

from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import HolderItem
from marketdata.vendors import _sec, yf_adapter
from marketdata.vendors.base import HoldersVendor

logger = logging.getLogger(__name__)

_DEFAULT_INSIDER_LIMIT = 50

_BREAKDOWN_LABELS = {
    "insiderspercentheld": "Insiders % held",
    "institutionspercentheld": "Institutions % held",
    "institutionsfloatpercentheld": "Institutions % of float held",
    "institutionscount": "Institutions count",
}
_BREAKDOWN_PCT = {"insiderspercentheld", "institutionspercentheld", "institutionsfloatpercentheld"}

_PURCHASE_RE = re.compile(r"purchase|\bbuy\b|\bbought\b", re.IGNORECASE)
_SALE_RE = re.compile(r"\bsale\b|\bsold\b", re.IGNORECASE)


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(fv) else fv


def _s(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none"} else s


def _get(row: dict, *keys: str) -> Any:
    """Case/space-insensitive column lookup (``Date Reported`` vs ``dateReported``)."""
    norm = {re.sub(r"[\s_]+", "", str(k)).lower(): v for k, v in row.items()}
    for k in keys:
        v = norm.get(re.sub(r"[\s_]+", "", k).lower())
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------


class YFinanceHoldersVendor(HoldersVendor):
    name = "yfinance"
    supports_markets = {"US", "CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        yf_adapter.apply_proxy(config)
        insider_limit = int(config.get("insider_limit") or _DEFAULT_INSIDER_LIMIT)
        out: list[HolderItem] = []
        for sym in symbols:
            try:
                out.extend(self._fetch_one(sym, insider_limit=insider_limit))
            except VendorError as e:
                logger.info(f"[holders/yfinance] {sym.code}: {e}")
        return out

    def _fetch_one(self, sym: Symbol, *, insider_limit: int) -> list[HolderItem]:
        code = sym.code
        t = yf_adapter.get_ticker(sym)
        rows: list[HolderItem] = []

        for label, attr, mapper in (
            ("Yahoo major holders", "major_holders", self.breakdown_rows),
            ("Yahoo institutional holders", "institutional_holders", self.institution_rows),
            ("Yahoo insider transactions", "insider_transactions", None),
        ):
            try:
                table = yf_adapter.call(label, lambda attr=attr: getattr(t, attr), symbol=code)
            except VendorError as e:
                logger.info(f"[holders/yfinance] {code} {attr} failed: {e}")
                continue
            if mapper is None:
                rows.extend(self.insider_rows(code, table, limit=insider_limit))
            else:
                rows.extend(mapper(code, table))
        return rows

    @staticmethod
    def breakdown_rows(code: str, table: Any) -> list[HolderItem]:
        out: list[HolderItem] = []
        for row in yf_adapter.df_records(table):
            value = _f(_get(row, "Value"))
            label_key = next((k for k in row if str(k) != "Value"), None)
            key = re.sub(r"[\s_]+", "", _s(row.get(label_key))).lower() if label_key else ""
            if not key or key not in _BREAKDOWN_LABELS:
                continue
            if key in _BREAKDOWN_PCT:
                out.append(HolderItem(symbol=code, kind="breakdown", holder=_BREAKDOWN_LABELS[key],
                                      pct_out=None if value is None else value * 100.0, source="yfinance"))
            else:
                out.append(HolderItem(symbol=code, kind="breakdown", holder=_BREAKDOWN_LABELS[key],
                                      shares=value, source="yfinance"))
        return out

    @staticmethod
    def institution_rows(code: str, table: Any) -> list[HolderItem]:
        out: list[HolderItem] = []
        for row in yf_adapter.df_records(table):
            holder = _s(_get(row, "Holder"))
            if not holder:
                continue
            pct = _f(_get(row, "pctHeld", "% Out"))
            chg = _f(_get(row, "pctChange"))
            out.append(HolderItem(
                symbol=code, kind="institution", holder=holder,
                date=_s(_get(row, "Date Reported"))[:10],
                shares=_f(_get(row, "Shares")), value=_f(_get(row, "Value")),
                pct_out=None if pct is None else pct * 100.0,
                change_pct=None if chg is None else chg * 100.0,
                source="yfinance",
            ))
        return out

    @staticmethod
    def insider_rows(code: str, table: Any, *, limit: int) -> list[HolderItem]:
        out: list[HolderItem] = []
        records = yf_adapter.df_records(table)
        if limit > 0:
            records = records[:limit]
        for row in records:
            holder = _s(_get(row, "Insider"))
            if not holder:
                continue
            text = _s(_get(row, "Text"))
            transaction_raw = _s(_get(row, "Transaction"))
            if _PURCHASE_RE.search(text):
                transaction = "Purchase"
            elif _SALE_RE.search(text):
                transaction = "Sale"
            else:
                transaction = transaction_raw.split()[0] if transaction_raw else ""
            out.append(HolderItem(
                symbol=code, kind="insider_tx", holder=holder,
                date=_s(_get(row, "Start Date"))[:10],
                shares=_f(_get(row, "Shares")), value=_f(_get(row, "Value")),
                transaction=transaction, position=_s(_get(row, "Position")),
                ownership=_s(_get(row, "Ownership")), url=_s(_get(row, "URL")),
                source="yfinance",
            ))
        return out


# ---------------------------------------------------------------------------
# SEC EDGAR Form 4 (Atom)
# ---------------------------------------------------------------------------

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_TITLE_RE = re.compile(r"^\s*(?P<form>4(?:/A)?)\s*-\s*(?P<rest>.*)$")
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _parse_title(title: str) -> tuple[str, str]:
    """``"4 - Cook Timothy D (0001214128) (Reporting)"`` -> (``"4"``, ``"Cook Timothy D"``)."""
    m = _TITLE_RE.match(title or "")
    form, rest = (m.group("form"), m.group("rest")) if m else ("", title or "")
    rest = rest.strip()
    while True:
        stripped = _PAREN_RE.sub("", rest)
        if stripped == rest:
            break
        rest = stripped
    return form, rest.strip()


class EdgarForm4HoldersVendor(HoldersVendor):
    name = "sec_edgar"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        out: list[HolderItem] = []
        for sym in symbols:
            ticker = sym.code.strip().upper().replace("_", "-").replace(".", "-")
            text = _sec.sec_get(_sec.FORM4_ATOM_URL.format(ticker=ticker), config=config,
                                host_key="www.sec.gov", parse="text", symbol=sym.code,
                                log_label="SEC Form 4 feed")
            if not text:
                continue
            out.extend(self.parse_atom(sym.code, text))
        return out

    @staticmethod
    def parse_atom(code: str, text: str) -> list[HolderItem]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise VendorError(f"SEC Form 4 feed {code}: invalid XML: {e}") from e
        out: list[HolderItem] = []
        for entry in root.findall("a:entry", _ATOM_NS):
            title = (entry.findtext("a:title", default="", namespaces=_ATOM_NS) or "").strip()
            form, holder = _parse_title(title)
            category = entry.find("a:category", _ATOM_NS)
            term = (category.get("term") if category is not None else "") or form
            if not holder:
                continue
            updated = (entry.findtext("a:updated", default="", namespaces=_ATOM_NS) or "").strip()
            link = entry.find("a:link", _ATOM_NS)
            url = (link.get("href") if link is not None else "") or ""
            position = ""
            m = re.search(r"\b(Reporting|Issuer|Filer)\b", title)
            if m:
                position = m.group(1)
            # The Atom feed carries no shares / value / D-I flag: those need the filing itself.
            out.append(HolderItem(
                symbol=code, kind="insider_tx", holder=holder, date=updated[:10],
                transaction=f"Form {term}" if term else "Form 4", position=position,
                url=url, source="sec_edgar",
            ))
        return out
