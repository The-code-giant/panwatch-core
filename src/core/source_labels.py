"""Human-readable labels for data-source vendor names.

``source`` is the vendor key stored on news/filings/holders rows (``yfinance``,
``google_news``, ``cnbc`` ...). ``publisher`` is the outlet that actually wrote a story
(Reuters, Bloomberg ...). ``label_for`` prefers the publisher when one is known.
"""

from __future__ import annotations

SOURCE_LABELS: dict[str, str] = {
    "yfinance": "Yahoo Finance",
    "google_news": "Google News",
    "cnbc": "CNBC",
    "marketwatch": "MarketWatch",
    "financial_post": "Financial Post",
    "bnn_bloomberg": "BNN Bloomberg",
    "globe_and_mail": "Globe and Mail",
    "investing_com": "Investing.com",
    "sec_edgar": "SEC EDGAR",
    "nasdaq": "Nasdaq",
    "yfinance_newswire": "Newswire",
}


def label_for(source: str, publisher: str = "") -> str:
    """Display label for a row: the publisher when known, else the vendor label, else the raw key."""
    pub = (publisher or "").strip()
    if pub:
        return pub
    key = (source or "").strip()
    return SOURCE_LABELS.get(key, key)
