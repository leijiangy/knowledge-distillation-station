# -*- coding: utf-8 -*-
"""分段裁剪与配图位置换算：偏移偏一位就会让配图与标记锚错地方"""
from core.segments import (image_context, image_pos, image_urls, segment_slice,
                           split_at_cuts)


# ---- 分段：起始下标必须跟着 strip 一起右移 ----
def test_split_single_segment():
    assert split_at_cuts("第一段内容", [], 0) == ("第一段内容", 1, 0)


def test_split_returns_start_after_stripped_whitespace():
    content = "甲乙\n\n丙丁"          # 切点在两段之间，第二段前面是换行
    text, total, start = split_at_cuts(content, [2], 1)
    assert text == "丙丁"
    assert total == 2
    assert start == 4                 # 跳过 "\n\n"，不是切点 2 —— 差这两位配图就错位
    assert content[start:start + len(text)] == text


def test_split_trims_trailing_whitespace_too():
    content = "甲乙  \n\n"
    text, _total, start = split_at_cuts(content, [], 0)
    assert text == "甲乙"
    assert content[start:start + len(text)] == text


def test_split_out_of_range():
    assert split_at_cuts("abc", [], 5) == (None, 1, 0)
    assert split_at_cuts("abc", [], -1) == (None, 1, 0)


# ---- 配图归属：按切点区间确定，且只属于一段 ----
def test_segment_slice_keeps_image_inside_segment():
    content = "甲" * 20 + "\n\n" + "乙" * 20 + "\n\n" + "丙" * 20
    text, total, figs = segment_slice(content, [22, 44], 0, [{"url": "u", "pos": 10}])
    assert total == 3
    assert text == "甲" * 20
    assert figs == [{"url": "u", "pos": 10}]


def test_image_is_never_assigned_to_two_segments():
    content = "甲" * 20 + "\n\n" + "乙" * 20 + "\n\n" + "丙" * 20
    cuts = [22, 44]
    imgs = [{"url": "u", "pos": 25}]
    hits = [segment_slice(content, cuts, i, imgs)[2] for i in range(3)]
    assert sum(1 for h in hits if h) == 1, "同一张图只能出现一次，否则相邻两段会重复"
    # 25 落在第二段区间 [22,44) 内；该段文本起点就是 22 → 段内下标 3
    assert hits[1] == [{"url": "u", "pos": 3}]


def test_image_in_boundary_whitespace_is_clamped():
    content = "甲" * 20 + "\n\n" + "乙" * 20
    # 位置 21 在第一段的尾随换行里（该段文本只有 20 字）→ 夹到段尾而不是丢掉
    text, _total, figs = segment_slice(content, [22], 0, [{"url": "u", "pos": 21}])
    assert text == "甲" * 20
    assert figs == [{"url": "u", "pos": 20}]


def test_image_at_article_end_belongs_to_last_segment():
    content = "甲" * 20 + "\n\n" + "乙" * 20
    _text, _total, figs = segment_slice(content, [22], 1, [{"url": "u", "pos": len(content)}])
    assert figs == [{"url": "u", "pos": 20}]


def test_segment_slice_skips_legacy_and_invalid_images():
    imgs = ["旧形态字符串.jpg", {"url": "u"}, {"url": "v", "pos": True}, None, {"pos": 9}]
    assert segment_slice("甲" * 30, [], 0, imgs)[2] == []


def test_segment_slice_out_of_range():
    assert segment_slice("abc", [], 9, []) == (None, 1, [])


# ---- 配图列表的两种形态 ----
def test_image_urls_accepts_both_shapes():
    assert image_urls(["a.jpg", {"url": "b.jpg", "pos": 3}]) == ["a.jpg", "b.jpg"]
    assert image_urls(None) == []


def test_image_pos_only_for_recorded_positions():
    imgs = [{"url": "a.jpg", "pos": 7}, "b.jpg", {"url": "c.jpg", "pos": None}]
    assert image_pos(imgs, "a.jpg") == 7
    assert image_pos(imgs, "b.jpg") is None      # 早期形态没有位置
    assert image_pos(imgs, "c.jpg") is None


# ---- 解释配图时的上下文 ----
def test_context_splits_at_image_position():
    content = "上文第一句。\n上文第二句。\n下文第一句。\n下文第二句。"
    pos = content.index("下文第一句")
    before, after = image_context(content, pos, window=100)
    assert before.endswith("上文第二句。")
    assert after.startswith("下文第一句。")
    flat = (before + after).replace("\n", "")
    assert flat in content.replace("\n", "")     # 上下文都来自原文，没有拼接出来的内容


def test_context_window_is_bounded():
    content = "前" * 20 + "后" * 20        # 图片位置取在两段之间
    before, after = image_context(content, 20, window=7)
    assert before == "前" * 7             # 位置前 window 个字符（这里没有换行可对齐）
    assert after == "后" * 7


def test_context_without_position_or_content():
    assert image_context("abc", None) == ("", "")
    assert image_context("", 0) == ("", "")
    assert image_context("abc", True) == ("", "")        # bool 不是合法位置
