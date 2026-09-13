# -*- coding: utf-8 -*-
"""DeepSeek 调用层（学习会话：分段 / 初始解释 / 选区解释 / 问答）。

设计依据（docs/学习会话设计-定稿.md 第十节）：
- 分段：Mayer 分段原则——AI 只定切点、不改文字，学习者控制节奏
- 初始解释前置：认知负荷理论工作示范效应（对低知识水平学习者有益）
- 不代劳：Bastani et al. PNAS 2025——无约束给答案有负效应；追问只指出断点
"""
import json

import httpx

from .config import settings

_TIMEOUT = 180.0
_MODEL = "deepseek-chat"


class AIError(Exception):
    pass


async def _chat(messages: list, temperature: float = 0.3, max_tokens: int = 2400,
                json_mode: bool = False) -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise AIError("DEEPSEEK_API_KEY 未配置。")
    payload = {
        "model": _MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_API}/chat/completions",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise AIError(f"模型调用失败：{exc}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("模型返回结构异常。") from exc


def normalize_cuts(raw_cuts, total_len: int, max_chars: int = 1200) -> list:
    """校验并修正切点：整数、界内、去重、排序；超长段按字数强制补齐切点。

    返回严格递增、均在 (0, total_len) 内的切点数组。
    """
    if total_len <= 0:
        return []
    hard_limit = max(400, int(max_chars * 1.5))
    candidates = []
    for value in raw_cuts or []:
        try:
            pos = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < pos < total_len:
            candidates.append(pos)
    cuts: list = []
    prev = 0
    for pos in sorted(set(candidates)) + [total_len]:
        while pos - prev > hard_limit:
            prev += max_chars
            cuts.append(prev)
        if pos - prev > 0 and pos < total_len:
            cuts.append(pos)
        prev = pos
    return cuts


def split_segments(content: str, cuts: list) -> list:
    """按切点把全文切成分段（文本本身不做任何改写）"""
    bounds = [0] + list(cuts) + [len(content)]
    return [content[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]


async def segment_article(title: str, content: str, max_chars: int = 1200) -> dict:
    """把全文划分为适合逐段精读的小段。返回 {summary, cuts}（切点为字符位置）。"""
    prompt = (
        "把下面这篇知乎内容划分为适合逐段精读的小段，并给出全文主旨。\n\n"
        "硬性要求：\n"
        "1. 只输出切点，不改写原文，不引用原文句子\n"
        f"2. 切点优先落在自然段/小节的边界上；每段尽量不超过 {max_chars} 字\n"
        "3. cuts 是切点的字符位置数组（相对全文的 0-based 字符索引，从 0 数起），"
        "不含 0、不含全文长度；[c1,c2,...] 表示分段为 [0,c1)、[c1,c2)、…、[cN,末)\n"
        "4. 切点严格递增、不重复\n\n"
        '只返回 JSON：{"summary": "一句全文主旨", "cuts": [整数数组]}\n\n'
        f"标题：{title}\n\n正文：\n{content}"
    )
    raw = await _chat(
        [{"role": "system", "content": "你是严谨的中文阅读教练，只输出要求的 JSON。"},
         {"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=3000, json_mode=True,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIError("模型未返回合法 JSON。") from exc
    return {"summary": str(data.get("summary") or "").strip(),
            "cuts": data.get("cuts") or []}


_EXPLAIN_SYSTEM = "你是一位耐心、克制的中文精读教练。你的解释要准确、平实、不夸张。"


async def explain_segment(title: str, summary: str, segment_text: str,
                          seg_hint: str = "") -> str:
    """某段的初始解释（先补共识，再讲这段说什么、在全文的作用）"""
    prompt = (
        "用户正在逐段精读一篇知乎文章，现在读到下面这一段。请给出这一段的「初始解释」。\n\n"
        f"全文主旨：{summary or '（未知）'}\n"
        f"标题：{title}\n\n"
        f"当前段落原文：\n{segment_text}\n\n"
        "写解释的要求：\n"
        "1. 先补共识：读者默认应该知道、但新手可能不知道的背景概念，用一两句话点明；"
        "如果本段没有这类概念就直接跳过这部分\n"
        "2. 再讲这一段在说什么：核心论点/信息是什么，以及它对全文起什么作用\n"
        "3. 平实的中文，不用列表堆砌，不超过 300 字\n"
        "4. 只解释，不替读者下结论，不扩展到原文之外的内容\n"
        + (f"\n补充提示：{seg_hint}" if seg_hint else "")
    )
    return (await _chat([{"role": "system", "content": _EXPLAIN_SYSTEM},
                         {"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=1200)).strip()


async def explain_selection(title: str, summary: str, segment_text: str,
                            selection: str) -> str:
    """选区解释（共享）：解释用户划出的这一小段文字"""
    prompt = (
        "用户正在精读一篇文章，划出了下面这段文字，想要一个解释。\n\n"
        f"全文主旨：{summary or '（未知）'}\n"
        f"所在段落：\n{segment_text}\n\n"
        f"用户划出的内容：\n{selection}\n\n"
        "要求：\n"
        "1. 解释这段文字在说什么、为什么这样说（必要时点明它依赖的前置概念）\n"
        "2. 结合所在段落理解，但不要复述整段\n"
        "3. 不超过 200 字，平实的中文\n"
        "4. 只解释，不延伸、不替读者下结论"
    )
    return (await _chat([{"role": "system", "content": _EXPLAIN_SYSTEM},
                         {"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=900)).strip()


async def answer_question(question: str, summary: str, anchor_text: str,
                          context_text: str = "") -> str:
    """私有提问的回答：直接回答；涉及推理的部分只指出断点、不代劳"""
    prompt = (
        "用户正在精读一篇文章，提出了一个问题。\n\n"
        f"全文主旨：{summary or '（未知）'}\n"
        + (f"所在段落：\n{context_text}\n\n" if context_text else "")
        + (f"用户针对的内容：\n{anchor_text}\n\n" if anchor_text else "")
        + f"用户的问题：{question}\n\n"
        "回答要求：\n"
        "1. 直接回答用户的问题，简洁、平实\n"
        "2. 如果问题涉及推理论证：先指出关键的那一步或断点在哪里，引导用户自己完成推导，"
        "不要在用户没意识到的情况下替他推导完\n"
        "3. 只回答这一个问题，不延伸成一篇讲解；不超过 300 字"
    )
    return (await _chat([{"role": "system", "content": _EXPLAIN_SYSTEM},
                         {"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=1200)).strip()
