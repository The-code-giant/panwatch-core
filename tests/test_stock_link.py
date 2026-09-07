"""tests for src/core/stock_link.py

Every test passes ``platform`` explicitly so the database is never read.
"""

from __future__ import annotations

from src.core.stock_link import DEFAULT_PLATFORM, PLATFORMS, stock_link_markdown, stock_url


class TestPlatforms:
    def test_default_platform_is_yahoo(self):
        """Stock links default to Yahoo Finance, which serves US, Canada, crypto and gold"""
        assert DEFAULT_PLATFORM == "yahoo"
        assert "yahoo" in PLATFORMS


class TestStockUrl:
    def test_us_yahoo(self):
        """Stock link: US ticker on Yahoo"""
        assert stock_url("AAPL", "US", platform="yahoo") == "https://finance.yahoo.com/quote/AAPL"

    def test_us_dotted_ticker(self):
        """Stock link: US ticker with a dot (BRK.B) is passed through"""
        assert stock_url("BRK.B", "US", platform="yahoo") == "https://finance.yahoo.com/quote/BRK.B"

    def test_ca_yahoo(self):
        """Stock link: Canadian .TO symbol on Yahoo"""
        assert stock_url("SHOP.TO", "CA", platform="yahoo") == "https://finance.yahoo.com/quote/SHOP.TO"

    def test_ca_venture(self):
        """Stock link: TSX Venture .V symbol on Yahoo"""
        assert stock_url("XYZ.V", "CA", platform="yahoo") == "https://finance.yahoo.com/quote/XYZ.V"

    def test_crypto_and_gold(self):
        """Stock link: crypto keeps its -USD form; spot gold maps to the COMEX future"""
        assert stock_url("BTC-USD", "CRYPTO", platform="yahoo") == "https://finance.yahoo.com/quote/BTC-USD"
        assert stock_url("XAUUSD", "GOLD", platform="yahoo") == "https://finance.yahoo.com/quote/GC=F"

    def test_xueqiu_us_still_works(self):
        """Stock link: Xueqiu remains selectable for US"""
        assert stock_url("AAPL", "US", platform="xueqiu") == "https://xueqiu.com/S/AAPL"

    def test_xueqiu_falls_back_to_yahoo_for_ca(self):
        """Stock link: Xueqiu has no Canadian pages, so CA falls back to Yahoo even when Xueqiu is set"""
        assert stock_url("SHOP.TO", "CA", platform="xueqiu") == "https://finance.yahoo.com/quote/SHOP.TO"

    def test_market_case_insensitive(self):
        """Stock link: market code is case-insensitive"""
        assert stock_url("SHOP.TO", "ca", platform="yahoo") == "https://finance.yahoo.com/quote/SHOP.TO"


class TestStockLinkMarkdown:
    def test_us(self):
        """Markdown link: US format"""
        assert stock_link_markdown("AAPL", "US", platform="yahoo") == "[AAPL.US](https://finance.yahoo.com/quote/AAPL)"

    def test_ca(self):
        """Markdown link: CA format"""
        assert stock_link_markdown("RY.TO", "CA", platform="yahoo") == "[RY.TO.CA](https://finance.yahoo.com/quote/RY.TO)"
