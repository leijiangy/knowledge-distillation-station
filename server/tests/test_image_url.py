# -*- coding: utf-8 -*-
"""配图地址白名单：配图由服务端抓取，放开域名等于把 /api/ingest 变成 SSRF 抓取器"""
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
