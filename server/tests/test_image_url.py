# -*- coding: utf-8 -*-
"""配图地址白名单及带图文章保存：图片 URL 不能覆盖文章键"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import main
from core.zhihu import clean_image_url as _clean_image_url


def test_accepts_zhihu_cdn():
    for url in ("https://pic1.zhimg.com/v2-abc.jpg",
                "https://picx.zhimg.com/50/v2-abc_l.jpg",
                "https://www.zhihu.com/some.png",
                "https://zhimg.com/v2-abc.jpg"):
        assert _clean_image_url(url) == url


def test_rejects_other_hosts_and_internal_addresses():
    for url in ("http://169.254.169.254/latest/meta-data/",     # 云元数据（经典 SSRF 目标）
                "http://127.0.0.1:8000/admin",
                "https://evil.com/zhimg.com.jpg",               # 域名里含 zhimg 但不是子域
                "https://zhimg.com.evil.com/x.jpg",
                "file:///etc/passwd",
                "ftp://zhimg.com/x.jpg"):
        assert _clean_image_url(url) == ""


def test_rejects_empty_and_overlong():
    assert _clean_image_url("") == ""
    assert _clean_image_url(None) == ""
    assert _clean_image_url("https://pic1.zhimg.com/" + "a" * 600) == ""


def test_is_image_url_detects_image_hosts():
    """知乎点开大图时地址栏会变成图片地址——那种地址不能当成文章地址保存"""
    from core.zhihu import is_image_url
    assert is_image_url("https://pic1.zhimg.com/v2-abc.jpg")
    assert is_image_url("https://pic4.zhimg.com/v2-abc_1")
    assert is_image_url("https://zhimg.com/x.webp")
    assert not is_image_url("https://www.zhihu.com/answer/123")
    assert not is_image_url("https://zhuanlan.zhihu.com/p/123")
    assert not is_image_url("https://evil.com/zhimg.com/x.jpg")
    assert not is_image_url("")
    assert not is_image_url(None)


def test_ingest_images_keep_article_url_as_storage_key(monkeypatch):
    article_url = "https://www.zhihu.com/answer/123456789"
    cases = [
        [{"url": "https://pic1.zhimg.com/v2-valid.jpg", "pos": 12}],
        [
            {"url": "https://pic1.zhimg.com/v2-valid.jpg", "pos": 12},
            {"url": "https://evil.com/not-allowed.jpg", "pos": 24},
        ],
    ]
    for images in cases:
        get = AsyncMock(return_value=None)
        begin = AsyncMock(return_value={"id": "00000000-0000-4000-8000-000000000099",
                                        "state": "queued"})
        monkeypatch.setattr(main, "require_account", lambda _request: ("42", None))
        monkeypatch.setattr(main, "_content_repo", object())
        monkeypatch.setattr(main.store, "get", get)
        monkeypatch.setattr(main.store, "begin_content_update", begin)
        request = SimpleNamespace(headers={}, json=AsyncMock(return_value={
            "url": article_url,
            "title": "带图文章",
            "content": "这是用于验证文章地址不会被配图地址覆盖的正文。" * 10,
            "images": images,
            "expected_current_commit": None,
            "idempotency_key": "00000000-0000-4000-8000-000000000001",
        }))

        result = asyncio.run(main.ingest(request))
        payload = json.loads(result.body)

        assert payload["ok"] is True
        assert get.await_args.args == (article_url,)
        assert begin.await_args.kwargs["article_key"] == article_url
        assert begin.await_args.kwargs["candidate_json"]["source"]["url"] == article_url
