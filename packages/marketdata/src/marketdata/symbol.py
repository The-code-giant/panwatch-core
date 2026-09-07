"""Cross-market symbol value object: one place to normalise codes per market and per vendor.

Conventions
- US canonical form is the exchange form (``BRK.B``); Yahoo uses ``BRK-B``.
- CA canonical form is the Yahoo form (``SHOP.TO`` / ``XYZ.V``); share classes use a
  hyphen in the base (``CTC-A.TO``). Legacy rows may carry ``CTC.A.TO`` or Eastmoney's
  ``BRK_B``; ``canonical_code`` folds those into the canonical form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    CN = "CN"
    HK = "HK"
    US = "US"
    CA = "CA"  # Canada: Yahoo form SHOP.TO / RY.TO / XYZ.V (also .NE / .CN)
    CRYPTO = "CRYPTO"
    GOLD = "GOLD"


_CN_RE = re.compile(r"^[036]\d{5}$")   # 6 digits, leading 0/3/6
_HK_RE = re.compile(r"^\d{5}$")        # 5 digits
# Canada: ticker + Yahoo exchange suffix (.TO TSX / .V TSXV / .NE NEO / .CN CSE). Checked before US.
_CA_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}\.(TO|V|NE|CN)$")
# US: uppercase ticker, may contain a dot or hyphen (BRK.B / BF-B); leading-dot index codes (.DJI) too.
_US_RE = re.compile(r"^(?:[A-Z][A-Z0-9.\-]{0,9}|\.[A-Z]{2,6})$")
_CRYPTO_RE = re.compile(r"^[A-Z0-9]{2,10}-USD$")  # BTC-USD, ETH-USD

_CA_SUFFIXES = ("TO", "V", "NE", "CN")


def _detect_market(code: str) -> Market:
    c = code.strip().upper()
    if c == "XAUUSD":
        return Market.GOLD
    if _CRYPTO_RE.match(c):
        return Market.CRYPTO
    if _CN_RE.match(c):
        return Market.CN
    if _HK_RE.match(c):
        return Market.HK
    if _CA_RE.match(c):
        return Market.CA
    if _US_RE.match(c):
        return Market.US
    # Fallback: six digits -> CN, everything else -> US
    return Market.CN if c.isdigit() and len(c) == 6 else Market.US


def _cn_exchange(code: str) -> str:
    """SH / SZ / BJ, same rule as src/core/cn_symbol.get_cn_exchange."""
    if code.startswith("920") or code.startswith(("83", "87", "88")):
        return "bj"
    if code.startswith(("5", "6")) or code.startswith("900"):
        return "sh"
    return "sz"


def _split_ca(code: str) -> tuple[str, str]:
    """``CTC.A.TO`` -> (``CTC.A``, ``TO``); no recognised suffix -> (code, "")."""
    if "." in code:
        base, _, suffix = code.rpartition(".")
        if suffix.upper() in _CA_SUFFIXES:
            return base, suffix.upper()
    return code, ""


def from_yfinance(ysym: str, market: str | Market) -> str:
    """Yahoo symbol -> canonical code for ``market`` (US: ``BRK-B`` -> ``BRK.B``; CA unchanged)."""
    mkt = market if isinstance(market, Market) else Market(str(market).upper())
    s = str(ysym or "").strip().upper()
    if mkt == Market.US:
        if s.startswith("^") or s.startswith("."):
            return s
        return s.replace("-", ".")
    return s


def canonical_code(raw: str, market: str | Market | None = None) -> str:
    """Fold legacy spellings into the canonical form (upper/strip; US ``_``/``-`` -> ``.``;
    CA base ``.`` -> ``-``). Other markets: upper/strip only."""
    s = str(raw or "").strip().upper()
    if not s:
        return s
    mkt = market if isinstance(market, Market) else (Market(str(market).upper()) if market else _detect_market(s))
    if mkt == Market.US:
        if s.startswith("^") or s.startswith("."):
            return s
        return s.replace("_", ".").replace("-", ".")
    if mkt == Market.CA:
        base, suffix = _split_ca(s)
        if suffix:
            return base.replace("_", "-").replace(".", "-") + "." + suffix
        return s
    return s


@dataclass(frozen=True)
class Symbol:
    market: Market
    code: str

    @classmethod
    def parse(cls, raw: str, market: str | None = None) -> "Symbol":
        code = raw.strip()
        if market:
            return cls(Market(market), code)
        return cls(_detect_market(code), code)

    def to_tencent(self) -> str:
        if self.market == Market.HK:
            return f"hk{self.code}"
        if self.market == Market.US:
            return f"us{self.code}"
        return _cn_exchange(self.code) + self.code

    def to_yfinance(self) -> str:
        """Yahoo symbol. US ``BRK.B``/``BRK_B`` -> ``BRK-B`` (index codes starting with "."
        unchanged); CA ``CTC.A.TO`` -> ``CTC-A.TO``; HK zero-padded ``.HK``; GOLD -> ``GC=F``."""
        if self.market == Market.HK:
            return f"{int(self.code):04d}.HK" if self.code.isdigit() else f"{self.code}.HK"
        if self.market == Market.GOLD:
            return "GC=F"  # COMEX gold future tracks spot; XAUUSD=X is not served by Yahoo
        code = self.code.strip()
        if self.market == Market.US:
            if code.startswith(".") or code.startswith("^"):
                return code
            return code.replace("_", "-").replace(".", "-")
        if self.market == Market.CA:
            base, suffix = _split_ca(code)
            if suffix:
                return base.replace("_", "-").replace(".", "-") + "." + suffix
            return code
        return code  # CRYPTO already Yahoo form; CN is blocked by vendor.supports_markets

    def to_eastmoney_secid(self) -> str:
        if self.market == Market.HK:
            return f"116.{self.code}"
        if self.market == Market.US:
            return f"105.{self.code}"
        return f"{'1' if _cn_exchange(self.code) == 'sh' else '0'}.{self.code}"
