# -*- coding: utf-8 -*-
"""/api/search 端点测试（打桩 zhihu.search_zhihu 与 store.keys，不发真实请求、不耗额度）"""
import pytest
from fastapi.testclient import TestClient

from core import cache
from main import app

client = TestClient(app)

ROWS = [
    {"Title": "费曼学习法是什么 - 知乎", "Url": "https://www.zhihu.com/question/1/answer/11",
     "ContentText": "费曼学习法的核心是通过教别人来检验自己……", "AuthorName": "甲"},
    {"Title": "一篇已经保存过的文章", "Url": "https://www.zhihu.com/p/999",
     "ContentText": "正文……", "AuthorName": "乙"},
]


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.content_cache.clear()
    cache.user_cache.clear()
    yield
    cache.content_cache.clear()
    cache.user_cache.clear()


@pytest.fixture
def stub_search(monkeypatch):
    calls = []

    async def fake_search(query, count=3):
        calls.append(query)
        return [dict(row) for row in ROWS]

    monkeypatch.setattr("core.zhihu.search_zhihu", fake_search)
    return calls


@pytest.fixture
def stub_store(monkeypatch):
    async def fake_keys():
        return {"https://www.zhihu.com/p/999"}

    monkeypatch.setattr("core.store.keys", fake_keys)


def test_empty_query_rejected():
    resp = client.get("/api/search", params={"q": "   "})
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "EMPTY_QUERY"


def test_search_returns_metrics_and_excludes_saved(stub_search, stub_store):
    resp = client.get("/api/search", params={"q": "费曼"})
    body = resp.json()
    assert body["ok"] is True
    assert body["total"] == 1  # 已保存全文的那条被排除
    item = body["items"][0]
    assert item["Title"] == "费曼学习法是什么"  # 去掉了「- 知乎」尾巴
    assert item["ContentType"] == "answer"
    assert set(item["metrics"]) == {"approval", "richness", "credibility"}
    assert "combined_score" in item


def test_result_cached_by_query(stub_search, stub_store):
    client.get("/api/search", params={"q": "费曼"})
    client.get("/api/search", params={"q": "费曼"})
    assert len(stub_search) == 1  # 第二次命中共享缓存，不消耗搜索额度
