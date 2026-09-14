# -*- coding: utf-8 -*-
"""分段裁剪：段文本去掉首尾空白后，起始下标必须跟着右移（标记锚点依赖它）"""
from core.segments import split_at_cuts

NL2 = "\n\n"


def test_split_single_segment():
    assert split_at_cuts("第一段内容", [], 0) == ("第一段内容", 1, 0)


def test_split_returns_start_after_stripped_whitespace():
    content = "甲乙" + NL2 + "丙丁"      # 切点在两段之间，第二段前面是换行
    text, total, start = split_at_cuts(content, [2], 1)
    assert text == "丙丁"
    assert total == 2
    assert start == 4                    # 跳过换行，而不是切点 2
    assert content[start:start + len(text)] == text


def test_split_trims_trailing_whitespace_too():
    content = "甲乙  " + NL2
    text, _total, start = split_at_cuts(content, [], 0)
    assert text == "甲乙"
    assert content[start:start + len(text)] == text


def test_split_multi_segment_bounds():
    content = "甲" * 10 + NL2 + "乙" * 10 + NL2 + "丙" * 10
    cuts = [12, 24]
    for i, expect in enumerate(("甲" * 10, "乙" * 10, "丙" * 10)):
        text, total, start = split_at_cuts(content, cuts, i)
        assert total == 3
        assert text == expect
        assert content[start:start + len(text)] == text


def test_split_out_of_range():
    assert split_at_cuts("abc", [], 5) == (None, 1, 0)
    assert split_at_cuts("abc", [], -1) == (None, 1, 0)
