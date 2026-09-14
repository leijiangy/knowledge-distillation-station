# -*- coding: utf-8 -*-
"""学习记录的折叠逻辑（软删除）：open / dismissed 事件流 → 记录列表"""
from core.reading_store import collapse_history


def ev(key: str, event: str, at: int, seg: int = 0) -> dict:
    return {"article_key": key, "event": event, "at": at, "seg_index": seg}


def test_open_shows_record():
    rows = [ev("a", "open", 100, 3)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 3, "at": 100}]


def test_dismissed_hides_record():
    rows = [ev("a", "open", 100), ev("a", "dismissed", 200)]
    assert collapse_history(rows) == []


def test_reopen_after_dismiss_brings_record_back():
    rows = [ev("a", "open", 100), ev("a", "dismissed", 200), ev("a", "open", 300, 5)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 5, "at": 300}]


def test_same_second_prefers_dismissed():
    # 时间戳精度到秒：同秒既有 open 又有 dismissed 时按隐藏处理（两种到达顺序都要成立）
    assert collapse_history([ev("a", "open", 100), ev("a", "dismissed", 100)]) == []
    assert collapse_history([ev("a", "dismissed", 100), ev("a", "open", 100)]) == []


def test_latest_open_wins_for_segment():
    rows = [ev("a", "open", 100, 1), ev("a", "open", 300, 7)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 7, "at": 300}]


def test_sorted_by_recency_and_independent_per_article():
    rows = [ev("a", "open", 100), ev("b", "open", 300), ev("a", "dismissed", 400)]
    assert collapse_history(rows) == [{"article_key": "b", "seg_index": 0, "at": 300}]


def test_ignores_rows_without_key_and_empty_input():
    rows = [{"event": "open", "at": 100}, ev("a", "open", 50)]
    assert collapse_history(rows) == [{"article_key": "a", "seg_index": 0, "at": 50}]
    assert collapse_history([]) == []
    assert collapse_history(None) == []
