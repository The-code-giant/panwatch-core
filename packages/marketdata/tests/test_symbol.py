from marketdata.symbol import Market, Symbol


def test_parse_detects_market():
    assert Symbol.parse("600519").market == Market.CN
    assert Symbol.parse("000001").market == Market.CN
    assert Symbol.parse("00700").market == Market.HK
    assert Symbol.parse("AAPL").market == Market.US


def test_parse_respects_explicit_market():
    assert Symbol.parse("00700", "HK").market == Market.HK
    assert Symbol.parse("600519", "CN").code == "600519"


def test_to_tencent():
    assert Symbol.parse("600519").to_tencent() == "sh600519"
    assert Symbol.parse("000001").to_tencent() == "sz000001"
    assert Symbol.parse("920001").to_tencent() == "bj920001"
    assert Symbol.parse("00700", "HK").to_tencent() == "hk00700"
    assert Symbol.parse("AAPL").to_tencent() == "usAAPL"


def test_to_yfinance():
    assert Symbol.parse("00700", "HK").to_yfinance() == "0700.HK"
    assert Symbol.parse("AAPL").to_yfinance() == "AAPL"


def test_parse_detects_canada():
    """Canadian Yahoo-form symbols (.TO / .V / .NE / .CN) are CA and are checked before the US regex."""
    assert Symbol.parse("SHOP.TO").market == Market.CA
    assert Symbol.parse("RY.TO").market == Market.CA
    assert Symbol.parse("XYZ.V").market == Market.CA
    assert Symbol.parse("SHHI.NE").market == Market.CA
    assert Symbol.parse("shop.to").market == Market.CA
    assert Symbol.parse("SHOP.TO", "CA").market == Market.CA


def test_parse_us_allows_dot_and_hyphen():
    """US tickers may contain a dot or hyphen (BRK.B / BF-B) and index codes keep their leading dot."""
    assert Symbol.parse("BRK.B").market == Market.US
    assert Symbol.parse("BF-B").market == Market.US
    assert Symbol.parse(".DJI").market == Market.US
    assert Symbol.parse("BTC-USD").market == Market.CRYPTO  # crypto still wins over the US regex


def test_to_yfinance_canada_unchanged():
    """CA symbols are already in Yahoo form, so to_yfinance returns them unchanged."""
    assert Symbol.parse("SHOP.TO").to_yfinance() == "SHOP.TO"
    assert Symbol.parse("XYZ.V").to_yfinance() == "XYZ.V"


def test_to_yfinance_us_share_classes():
    """US share classes map to Yahoo's hyphen form (BRK.B / BRK_B -> BRK-B) and index codes keep their dot."""
    assert Symbol.parse("BRK.B").to_yfinance() == "BRK-B"
    assert Symbol.parse("BRK_B", "US").to_yfinance() == "BRK-B"
    assert Symbol.parse("BF-B").to_yfinance() == "BF-B"
    assert Symbol.parse(".DJI").to_yfinance() == ".DJI"


def test_to_yfinance_canada_share_classes():
    """CA share classes map the base's dot to a hyphen and keep the exchange suffix (CTC.A.TO -> CTC-A.TO)."""
    assert Symbol.parse("CTC.A.TO").to_yfinance() == "CTC-A.TO"
    assert Symbol.parse("CTC-A.TO").to_yfinance() == "CTC-A.TO"
    assert Symbol.parse("BAM.A.TO", "CA").to_yfinance() == "BAM-A.TO"


def test_from_yfinance_and_canonical_code():
    """from_yfinance restores the US exchange form; canonical_code folds legacy spellings per market."""
    from marketdata.symbol import canonical_code, from_yfinance
    assert from_yfinance("BRK-B", "US") == "BRK.B"
    assert from_yfinance("AAPL", "US") == "AAPL"
    assert from_yfinance("^GSPC", "US") == "^GSPC"
    assert from_yfinance("CTC-A.TO", "CA") == "CTC-A.TO"
    assert canonical_code("brk_b", "US") == "BRK.B"
    assert canonical_code("BRK-B", "US") == "BRK.B"
    assert canonical_code("CTC.A.TO", "CA") == "CTC-A.TO"
    assert canonical_code(" shop.to ") == "SHOP.TO"
    assert canonical_code("btc-usd") == "BTC-USD"
