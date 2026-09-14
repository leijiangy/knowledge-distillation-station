# -*- coding: utf-8 -*-
"""分段与配图位置换算（纯函数，便于测试）。

配图的位置是「原文里的字符下标」，由书签抓取时记录（见 app.js 的 buildBookmarklet）。
左栏显示的是**某一段**，所以要先换算成段内下标；解释配图时要取它位置前后的原文。
这些换算一旦偏一位，标记锚点和配图都会错位，所以单独成模块并覆盖测试。
"""
from typing import Optional


def _slice(content: str, cuts: list, index: int):
    """取第 index 段的原始区间与去空白后的文本；返回 (文本, 总段数, 文本起点, 区间起, 区间止)"""
    bounds = [0] + list(cuts or []) + [len(content)]
    total = max(0, len(bounds) - 1)
    if index < 0 or index >= total:
        return None, total, 0, 0, 0
    raw_start, raw_end = bounds[index], bounds[index + 1]
    raw = content[raw_start:raw_end]
    lead = len(raw) - len(raw.lstrip())
    return raw.strip(), total, raw_start + lead, raw_start, raw_end


def split_at_cuts(content: str, cuts: list, index: int):
    """按切点取第 index 段；返回 (段文本 or None, 总段数, 段文本在全文中的起始下标)。

    段首尾的空白一律去除，所以起始下标要跟着右移，否则配图位置会偏。
    """
    text, total, start, _raw_start, _raw_end = _slice(content, cuts, index)
    return text, total, start