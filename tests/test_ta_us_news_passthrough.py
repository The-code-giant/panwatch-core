"""get_news 路由:ticker 透传上游(Yahoo),自由文本行业/主题词才走 Google News 关键词搜索。

回归 bug:0.3.0 新闻分析师对美股 ticker(BABA)调 get_news,原逻辑只判 `not is_tickerkeep_routable`,
把美股 ticker 也送进关键词搜索 → 搜不到 → 返回空结果,美股拿不到个股新闻。
修复:关键词分支用 `_looks_like_keyword_query`(不是 1-12 位 ticker 字符才算关键词),
纯 ticker(AAPL / SHOP.TO / BRK-B / ^GSPC)落到上游透传。
"""

from __future__ import annotations

from src.agents.tradingagents import toolkit_adapter as tk


def test_looks_like_keyword_query_distinguishes_ticker_from_query():
    """ticker(含 .TO / -B / ^ / = 形式)不算关键词;含空格/非 ASCII 的短语才算。"""
    for ticker in ("BABA", "NVDA", "aapl", "SHOP.TO", "BRK-B", "BRK.B", "^GSPC", "CL=F", "600519", "00700"):
        assert tk._looks_like_keyword_query(ticker) is False, ticker
    for query in ("electric vehicles", "汽车行业", "新能源汽车", "AI chips demand", "semiconductor industry outlook"):
        assert tk._looks_like_keyword_query(query) is True, query
    assert tk._looks_like_keyword_query("") is False
    assert tk._looks_like_keyword_query("ABCDEFGHIJKLM") is True  # 13 chars > ticker max


def test_us_ticker_get_news_passes_through_to_upstream(monkeypatch):
    """美股 get_news(BABA)→ 走上游 vendor(Yahoo),不进关键词搜索。"""
    calls = {"keyword": 0, "upstream": 0}

    def fake_keyword(_sym, market=None):
        calls["keyword"] += 1
        return "KEYWORD NEWS"

    def fake_upstream(_method, *_a, **_k):
        calls["upstream"] += 1
        return "UPSTREAM YAHOO NEWS for BABA"

    monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
    monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

    out = tk._patched_route_to_vendor("get_news", "BABA", "2026-06-15", "2026-06-22")

    assert calls["upstream"] == 1
    assert calls["keyword"] == 0
    assert "UPSTREAM" in str(out)


def test_ca_ticker_get_news_passes_through_to_upstream(monkeypatch):
    """加股 get_news(SHOP.TO)→ 同样透传上游,不被 '.' 误判为关键词。"""
    calls = {"keyword": 0, "upstream": 0}
    monkeypatch.setattr(tk, "_serve_keyword_news", lambda *_a, **_k: calls.__setitem__("keyword", calls["keyword"] + 1))
    monkeypatch.setattr(tk, "_real_route_to_vendor", lambda *_a, **_k: (calls.__setitem__("upstream", calls["upstream"] + 1), "UPSTREAM SHOP")[1])

    out = tk._patched_route_to_vendor("get_news", "SHOP.TO", "2026-06-15", "2026-06-22")
    assert calls == {"keyword": 0, "upstream": 1}
    assert "UPSTREAM SHOP" in str(out)


def test_keyword_query_get_news_goes_to_google_news(monkeypatch):
    """自由文本主题词(electric vehicles / 汽车行业)→ 走 Google News 关键词搜索,不透传上游。"""
    for query in ("electric vehicles", "汽车行业"):
        calls = {"keyword": [], "upstream": 0}

        def fake_keyword(sym, market=None):
            calls["keyword"].append((sym, market))
            return f'[Industry/theme news "{sym}" (from Google News, 1 items)]\n- [2026-06-20] EV sales climb'

        def fake_upstream(_method, *_a, **_k):
            calls["upstream"] += 1
            return "UPSTREAM"

        monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
        monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

        out = tk._patched_route_to_vendor("get_news", query, "2026-06-15", "2026-06-22")

        assert calls["keyword"] == [(query, "US")], query  # 无 context stock → 默认市场
        assert calls["upstream"] == 0
        assert "from Google News" in str(out)
