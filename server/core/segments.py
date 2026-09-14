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


def segment_slice(content: str, cuts: list, index: int, images=None):
    """取第 index 段及其配图；返回 (段文本, 总段数, 段内配图)。

    配图按它在全文中的位置**归属到唯一一段**（落在本段切点区间内的才算），
    段内下标相对段文本首字符；夹在段首尾空白里的夹到 0 / len —
    既不重复出现在相邻两段，也不会凭空消失。
    """
    text, total, start, raw_start, raw_end = _slice(content, cuts, index)
    if text is None:
        return None, total, []
    figs = []
    for x in images or []:
        if not isinstance(x, dict):
            continue
        pos, url = x.get("pos"), x.get("url")
        if not isinstance(pos, int) or isinstance(pos, bool) or not url:
            continue
        inside = raw_start <= pos < raw_end
        if not inside and raw_end == len(content) and pos == len(content):
            inside = True          # 文末的图（它的位置恰好在全文长度处）
        if not inside:
            continue
        figs.append({"url": url, "pos": max(0, min(pos - start, len(text)))})
    return text, total, figs


def image_urls(images) -> list:
    """配图列表 → 纯 URL 列表（兼容早期只存 URL 字符串的形态）"""
    out = []
    for x in images or []:
        u = x.get("url") if isinstance(x, dict) else x
        if isinstance(u, str) and u:
            out.append(u)
    return out


def image_pos(images, url: str) -> Optional[int]:
    """取某张配图在正文中的字符位置（早期保存的没有记录，返回 None）"""
    for x in images or []:
        if isinstance(x, dict) and x.get("url") == url and isinstance(x.get("pos"), int):
            return x["pos"]
    return None


def image_context(content: str, pos, window: int = 400):
    """图片位置前后的原文，各取一段并向两侧对齐到换行边界。

    抓取时记录了图片的字符下标，所以能给模型真正的上下文（图片就插在这两段之间），
    而不是让它猜。位置缺失时返回空串，解释就只能依据标题与主旨。
    """
    if not isinstance(pos, int) or isinstance(pos, bool) or not content:
        return "", ""
    p = max(0, min(pos, len(content)))
    a = max(0, p - window)
    b = min(len(content), p + window)
    before, after = content[a:p], content[p:b]
    if a > 0:
        cut = before.find("\n")
        if cut >= 0:
            before = before[cut + 1:]
    if b < len(content):
        cut = after.rfind("\n")
        if cut >= 0:
            after = after[:cut]
    return before.strip(), after.strip()
