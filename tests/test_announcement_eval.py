"""公告利好利空解读(Phase B)。"""

from __future__ import annotations

import asyncio

from src.web.api import insights
from src.web.database import SessionLocal


class _FakeAIClient:
    def __init__(self, reply):
        self._reply = reply

    async def chat(self, system_prompt, user_content, temperature=0.2):
        return self._reply


def test_parse_tone():
    """利好/利空/中性 解析(子串安全)。"""
    assert insights._parse_tone("Positive,业绩超预期") == "Positive"
    assert insights._parse_tone("偏Negative") == "Negative"
    assert insights._parse_tone("影响Neutral") == "Neutral"
    assert insights._parse_tone("看不出") == "Neutral"


def test_announcement_eval_maps_tone_per_item(monkeypatch):
    """逐条公告映射 AI 判定的利好/利空。"""
    insights._ANN_CACHE.clear()

    async def fake_fetch(symbol, name, limit=5, market=""):
        return [
            {"title": "中标重大项目", "time": "2026-06-18 09:00", "content": ""},
            {"title": "股东拟减持", "time": "2026-06-17 16:00", "content": ""},
        ]

    monkeypatch.setattr(insights, "_fetch_recent_announcements", fake_fetch)
    monkeypatch.setattr(
        insights,
        "_get_ai_client",
        lambda db, mid=None: _FakeAIClient("1|Positive|中标利好业绩\n2|Negative|减持承压"),
    )

    req = insights.AnnouncementEvalRequest(symbol="AAPL", market="US")
    db = SessionLocal()
    try:
        res = asyncio.run(insights.announcement_eval(req, db))
    finally:
        db.close()

    assert len(res["items"]) == 2
    assert res["items"][0]["tone"] == "Positive"
    assert res["items"][1]["tone"] == "Negative"


def test_announcement_eval_empty(monkeypatch):
    """无公告时返回空列表,不调 AI。"""
    insights._ANN_CACHE.clear()

    async def fake_fetch(symbol, name, limit=5, market=""):
        return []

    called = {"ai": 0}

    def fake_ai(db, mid=None):
        called["ai"] += 1
        return _FakeAIClient("")

    monkeypatch.setattr(insights, "_fetch_recent_announcements", fake_fetch)
    monkeypatch.setattr(insights, "_get_ai_client", fake_ai)

    req = insights.AnnouncementEvalRequest(symbol="000001", market="US")
    db = SessionLocal()
    try:
        res = asyncio.run(insights.announcement_eval(req, db))
    finally:
        db.close()
    assert res["items"] == []
    assert called["ai"] == 0
