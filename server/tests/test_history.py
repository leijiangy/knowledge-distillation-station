# -*- coding: utf-8 -*-
"""学习记录的折叠逻辑：open 埋点 → 记录列表（每篇取最后读到的那一段）"""
import asyncio

import httpx

from core import reading_store
from core.reading_store import collapse_history


def rec(key: str, at: int, seg: int = 0) -> dict:
    return {"article_key": key, "at": at, "seg_index": seg}


def test_single_open_shows_record():
    assert collapse_history([rec("a", 100, 3)]) == [{"article_key": "a", "seg_index": 3, "at": 100}]


def test_latest_open_wins_for_segment():
    rows = [rec("a", 100, 1), rec("a", 300, 7)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 7, "at": 300}]


def test_latest_open_keeps_version_and_segment_identity():
    row = {"article_key": "a", "at": 300, "seg_index": 7,
           "git_commit": "a" * 40,
           "segment_id": "00000000-0000-4000-8000-000000000007"}
    assert collapse_history([row]) == [row]


def test_sorted_by_recency():
    rows = [rec("a", 100), rec("b", 300), rec("c", 200)]
    assert [r["article_key"] for r in collapse_history(rows)] == ["b", "c", "a"]


def test_articles_are_independent():
    rows = [rec("a", 100, 2), rec("b", 200, 5), rec("a", 300, 4)]
    got = {r["article_key"]: r["seg_index"] for r in collapse_history(rows)}
    assert got == {"a": 4, "b": 5}


def test_ignores_rows_without_key_and_empty_input():
    rows = [{"at": 100}, rec("a", 50)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 0, "at": 50}]
    assert collapse_history([]) == []
    assert collapse_history(None) == []


def test_list_recent_opened_falls_back_for_legacy_columns(monkeypatch):
    calls = []

    class Client:
        async def get(self, _url, params):
            calls.append(dict(params))
            request = httpx.Request("GET", "https://example.invalid/reading_events")
            if len(calls) == 1:
                return httpx.Response(
                    400, request=request,
                    json={"message": "column reading_events.git_commit does not exist"},
                )
            return httpx.Response(
                200, request=request,
                json=[{"article_key": "a", "seg_index": 2, "at": 100}],
            )

    monkeypatch.setattr(reading_store, "_check_config", lambda: None)
    monkeypatch.setattr(reading_store, "_get_client", lambda: Client())
    rows = asyncio.run(reading_store.list_recent_opened("u"))
    assert rows == [{"article_key": "a", "seg_index": 2, "at": 100}]
    assert calls[0]["select"] == "article_key,git_commit,segment_id,seg_index,at"
    assert calls[1]["select"] == "article_key,seg_index,at"
