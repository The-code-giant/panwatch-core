"""Filings vendors: SEC EDGAR submissions (US) and Yahoo press releases for Canada.

``SecEdgarFilingsVendor`` resolves the CIK, reads ``filings.recent`` (parallel arrays) from
the submissions API, keeps the forms in ``config["forms"]`` (default: the market-moving
set below) and returns the first ``config["days"]`` (the Engine's name for ``limit``; 50 by
default) rows, newest first. ``filed_at`` is the filing date at 00:00 UTC.

``YFinanceNewswireFilingsVendor`` is the Canadian stand-in: SEDAR+ offers no free API, so the
company's own press releases (CNW, GlobeNewswire, Business Wire via Yahoo's "press releases"
tab) are shown as ``form_type="press_release"`` rows.

Per-symbol failures never kill the batch.
"""

from __future__ import annotations

import logging

from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.types import FilingItem
from marketdata.vendors import _sec, yf_adapter
from marketdata.vendors.base import FilingsVendor
from marketdata.vendors.news import YFinanceNewsVendor

logger = logging.getLogger(__name__)

DEFAULT_FORMS = ["8-K", "10-K", "10-Q", "4", "SC 13D", "SC 13G", "S-1", "DEF 14A", "6-K", "20-F", "40-F"]
_DEFAULT_LIMIT = 50
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"


def _limit_from(config: dict) -> int:
    try:
        n = int(config.get("days") or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else _DEFAULT_LIMIT


class SecEdgarFilingsVendor(FilingsVendor):
    name = "sec_edgar"
    supports_markets = {"US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        forms = {str(f).strip().upper() for f in (config.get("forms") or DEFAULT_FORMS) if str(f).strip()}
        limit = _limit_from(config)
        out: list[FilingItem] = []
        for sym in symbols:
            cik = _sec.resolve_cik(sym.code, config)
            if not cik:
                logger.info(f"[filings/sec_edgar] {sym.code}: CIK unresolved")
                continue
            payload = _sec.sec_get(_sec.SUBMISSIONS_URL.format(cik=cik), config=config,
                                   host_key="data.sec.gov", symbol=sym.code,
                                   log_label="SEC submissions")
            if not isinstance(payload, dict):
                continue
            out.extend(self.map_submissions(sym.code, cik, payload, forms=forms, limit=limit))
        return out

    @staticmethod
    def map_submissions(code: str, cik: int, payload: dict, *, forms: set[str], limit: int) -> list[FilingItem]:
        recent = ((payload.get("filings") or {}).get("recent") or {})
        form_list = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descs = recent.get("primaryDocDescription") or []
        report_dates = recent.get("reportDate") or []

        def at(seq: list, i: int) -> str:
            return str(seq[i] or "").strip() if i < len(seq) and seq[i] is not None else ""

        out: list[FilingItem] = []
        for i in range(len(form_list)):
            form = at(form_list, i).upper()
            if forms and form not in forms:
                continue
            accession = at(accessions, i)
            if not accession:
                continue
            filed = at(dates, i)
            document = at(docs, i)
            folder = accession.replace("-", "")
            url = (_ARCHIVE_URL.format(cik=cik, accession=folder, document=document) if document
                   else _INDEX_URL.format(cik=cik, accession=folder))
            description = at(descs, i)
            out.append(FilingItem(
                source="sec_edgar", external_id=accession, symbol=code, form_type=form,
                title=f"{form}: {description or form}", filed_at=yf_adapter.to_utc(filed[:10]),
                url=url, description=description, report_date=at(report_dates, i),
            ))
            if len(out) >= limit:
                break
        out.sort(key=lambda f: f.filed_at, reverse=True)
        return out


class YFinanceNewswireFilingsVendor(FilingsVendor):
    name = "yfinance_newswire"
    supports_markets = {"CA"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        yf_adapter.apply_proxy(config)
        count = int(config.get("count") or 30)
        limit = _limit_from(config)
        out: list[FilingItem] = []
        for sym in symbols:
            code = sym.code
            try:
                t = yf_adapter.get_ticker(sym)
                items = yf_adapter.call(
                    "Yahoo press releases",
                    lambda t=t: t.get_news(count=count, tab="press releases"),
                    symbol=code,
                )
            except VendorError as e:
                logger.info(f"[filings/yfinance_newswire] {code}: {e}")
                continue
            rows: list[FilingItem] = []
            for item in items or []:
                article = YFinanceNewsVendor._parse_item(item, code)
                if article is None:
                    continue
                rows.append(FilingItem(
                    source="yfinance_newswire", external_id=article.external_id, symbol=code,
                    form_type="press_release", title=article.title, filed_at=article.publish_time,
                    url=article.url, description=article.content, report_date="",
                ))
            rows.sort(key=lambda f: f.filed_at, reverse=True)
            out.extend(rows[:limit])
        return out
