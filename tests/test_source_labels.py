"""Source label lookups used by the news API and the agents."""
from src.core.source_labels import SOURCE_LABELS, label_for


def test_every_vendor_has_label():
    """每个信息层 vendor 键都有英文显示名。"""
    for key in ("yfinance", "google_news", "cnbc", "marketwatch", "financial_post",
                "bnn_bloomberg", "globe_and_mail", "investing_com", "sec_edgar", "nasdaq",
                "yfinance_newswire"):
        assert key in SOURCE_LABELS
        assert SOURCE_LABELS[key] and not any("一" <= ch <= "鿿" for ch in SOURCE_LABELS[key])


def test_publisher_wins_over_vendor():
    """给了 publisher 时优先显示 publisher。"""
    assert label_for("yfinance", "Reuters") == "Reuters"
    assert label_for("google_news", "  Bloomberg ") == "Bloomberg"


def test_vendor_label_then_raw_key():
    """无 publisher 时用 vendor 显示名,未知键原样返回。"""
    assert label_for("yfinance") == "Yahoo Finance"
    assert label_for("sec_edgar", "") == "SEC EDGAR"
    assert label_for("some_new_feed") == "some_new_feed"
    assert label_for("") == ""
