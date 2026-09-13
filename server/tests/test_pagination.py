# -*- coding: utf-8 -*-
"""分页协议解析测试（NextOffset 是 String，缺失/非法一律停止翻页）"""
from core.zhihu import parse_next_offset


def test_string_offset_parsed():
    assert parse_next_offset({"NextOffset": "50", "IsEnd": False}) == 50


def test_int_offset_tolerated():
    assert parse_next_offset({"NextOffset": 50}) == 50


def test_missing_returns_none():
    assert parse_next_offset({"IsEnd": False}) is None
    assert parse_next_offset({}) is None


def test_invalid_returns_none():
    assert parse_next_offset({"NextOffset": "abc"}) is None
    assert parse_next_offset({"NextOffset": ""}) is None
    assert parse_next_offset({"NextOffset": None}) is None


def test_non_dict_returns_none():
    assert parse_next_offset(None) is None
    assert parse_next_offset("50") is None
