"""Holders vendors: the three Yahoo row kinds (NaN handling, transaction classification) and the SEC Form 4 Atom feed."""

from __future__ import annotations

import numpy as np
import pandas as pd
from _yf_fakes import FakeTicker, patch_yf

import marketdata.vendors._sec as sec
from marketdata.errors import VendorError
from marketdata.symbol import Symbol
from marketdata.vendors.holders import EdgarForm4HoldersVendor, YFinanceHoldersVendor

_MAJOR = pd.DataFrame(
    {"Value": [0.0166, 0.6263, 0.6369, 6874]},
    index=pd.Index(["insidersPercentHeld", "institutionsPercentHeld", "institutionsFloatPercentHeld", "institutionsCount"],
                   name="Breakdown"),
)
_INST = pd.DataFrame({
    "Date Reported": [pd.Timestamp("2024-06-30"), pd.Timestamp("2024-06-30")],
    "Holder": ["Vanguard Group Inc", "Blackrock Inc."],
    "pctHeld": [0.0855, np.nan],
    "Shares": [1_300_000_000, 1_000_000_000],
    "Value": [270_000_000_000, np.nan],
    "pctChange": [0.0123, -0.005],
})
_INSIDER = pd.DataFrame({
    "Shares": [10_000, 5_000, 200],
    "Value": [2_000_000, np.nan, 40_000],
    "URL": ["", "", ""],
    "Text": ["Sale at price 200.00 per share.", "Purchase at price 190.00 per share.", "Stock Award(Grant) at price 0.00 per share."],
    "Insider": ["COOK TIMOTHY D", "LEVINSON ARTHUR D", "ADAMS KATHERINE L"],
    "Position": ["Chief Executive Officer", "Director", "General Counsel"],
    "Transaction": ["Sale at price 200.00 per share.", "Purchase at price 190.00 per share.", "Stock Award(Grant) at price 0.00 per share."],
    "Start Date": [pd.Timestamp("2024-10-02"), pd.Timestamp("2024-09-15"), pd.Timestamp("2024-08-01")],
    "Ownership": ["D", "I", "D"],
})


def test_yfinance_holders_three_kinds(monkeypatch):
    """major_holders -> breakdown (% x100 / count as shares), institutional_holders -> institution
    (pct x100, NaN -> None), insider_transactions -> insider_tx (Purchase / Sale / first word)."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker(
        "AAPL", major_holders=_MAJOR, institutional_holders=_INST, insider_transactions=_INSIDER)})
    out = YFinanceHoldersVendor().fetch([Symbol.parse("AAPL")], {})
    assert all(h.symbol == "AAPL" and h.source == "yfinance" for h in out)

    breakdown = [h for h in out if h.kind == "breakdown"]
    assert [h.holder for h in breakdown] == ["Insiders % held", "Institutions % held",
                                             "Institutions % of float held", "Institutions count"]
    assert abs(breakdown[0].pct_out - 1.66) < 1e-9 and abs(breakdown[1].pct_out - 62.63) < 1e-9
    assert breakdown[3].shares == 6874 and breakdown[3].pct_out is None

    inst = [h for h in out if h.kind == "institution"]
    assert [h.holder for h in inst] == ["Vanguard Group Inc", "Blackrock Inc."]
    assert inst[0].date == "2024-06-30" and abs(inst[0].pct_out - 8.55) < 1e-9
    assert abs(inst[0].change_pct - 1.23) < 1e-9 and inst[0].shares == 1_300_000_000
    assert inst[1].pct_out is None and inst[1].value is None and abs(inst[1].change_pct + 0.5) < 1e-9

    tx = [h for h in out if h.kind == "insider_tx"]
    assert [h.transaction for h in tx] == ["Sale", "Purchase", "Stock"]
    assert tx[0].holder == "COOK TIMOTHY D" and tx[0].date == "2024-10-02" and tx[0].position == "Chief Executive Officer"
    assert tx[0].ownership == "D" and tx[0].shares == 10_000 and tx[0].value == 2_000_000
    assert tx[1].value is None and tx[1].ownership == "I"


def test_yfinance_holders_insider_limit_and_partial_failure(monkeypatch):
    """insider_limit caps insider rows; a failing table drops only that kind, the others still return."""
    class NoMajor(FakeTicker):
        @property
        def major_holders(self):
            raise RuntimeError("boom")

    patch_yf(monkeypatch, tickers={"SHOP.TO": NoMajor(
        "SHOP.TO", institutional_holders=_INST, insider_transactions=_INSIDER)})
    out = YFinanceHoldersVendor().fetch([Symbol.parse("SHOP.TO")], {"insider_limit": 1})
    kinds = [h.kind for h in out]
    assert "breakdown" not in kinds
    assert kinds.count("institution") == 2 and kinds.count("insider_tx") == 1
    assert out[-1].holder == "COOK TIMOTHY D"


def test_yfinance_holders_empty_tables(monkeypatch):
    """None / empty tables yield [] rather than raising."""
    patch_yf(monkeypatch, tickers={"AAPL": FakeTicker("AAPL", institutional_holders=pd.DataFrame())})
    assert YFinanceHoldersVendor().fetch([Symbol.parse("AAPL")], {}) == []


_ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>AAPL Form 4 filings</title>
  <entry>
    <title>4 - Cook Timothy D (0001214128) (Reporting)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019324000101/0000320193-24-000101-index.htm"/>
    <category scheme="https://www.sec.gov/" label="form type" term="4"/>
    <updated>2024-10-03T18:31:02-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0000320193-24-000101</id>
  </entry>
  <entry>
    <title>4/A - Levinson Arthur D (0001214156) (Reporting)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019324000099/0000320193-24-000099-index.htm"/>
    <category scheme="https://www.sec.gov/" label="form type" term="4/A"/>
    <updated>2024-09-16T17:02:44-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0000320193-24-000099</id>
  </entry>
</feed>
"""


def test_edgar_form4_atom(monkeypatch):
    """Form 4 Atom entries become insider_tx rows: holder from the title, date = updated[:10], url from the link."""
    calls: list = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        return _ATOM

    monkeypatch.setattr(sec, "market_get", fake_get)
    out = EdgarForm4HoldersVendor().fetch([Symbol.parse("AAPL")], {"contact_email": "ops@example.org"})
    assert len(out) == 2
    a, b = out
    assert a.kind == "insider_tx" and a.source == "sec_edgar" and a.symbol == "AAPL"
    assert a.holder == "Cook Timothy D" and a.date == "2024-10-03" and a.transaction == "Form 4"
    assert a.url.endswith("0000320193-24-000101-index.htm") and a.position == "Reporting"
    assert b.holder == "Levinson Arthur D" and b.transaction == "Form 4/A" and b.date == "2024-09-16"
    url, kw = calls[0]
    assert "CIK=AAPL" in url and "type=4" in url and "output=atom" in url
    assert kw["headers"]["User-Agent"] == "TickerKeep/1.0 (ops@example.org)" and kw["parse"] == "text"


def test_edgar_form4_symbol_spelling_and_failures(monkeypatch):
    """BRK.B is sent as BRK-B; a failed download yields [] and invalid XML raises VendorError."""
    seen: list[str] = []

    def fake_get(url, **kw):
        seen.append(url)
        return None

    monkeypatch.setattr(sec, "market_get", fake_get)
    assert EdgarForm4HoldersVendor().fetch([Symbol.parse("BRK.B")], {}) == []
    assert "CIK=BRK-B" in seen[0]

    monkeypatch.setattr(sec, "market_get", lambda url, **kw: "<not xml")
    try:
        EdgarForm4HoldersVendor().fetch([Symbol.parse("AAPL")], {})
    except VendorError:
        pass
    else:
        raise AssertionError("invalid XML must raise VendorError")
