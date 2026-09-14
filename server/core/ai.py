# -*- coding: utf-8 -*-
"""DeepSeek 调用层（学习会话：分段 / 初始解释 / 选区解释 / 问答 / 配图解释）。

设计依据（docs/学习会话设计-定稿.md 第十节）：
- 分段：Mayer 分段原则——AI 只定切点、不改文字，学习者控制节奏
- 初始解释前置：认知负荷理论工作示范效应（对低知识水平学习者有益）
- 不代劳：Bastani et al. PNAS 2025——无约束给答案有负效应；追问只指出断点
"""
import base64
import json
import re

import httpx

from .config import settings

_TIMEOUT = 180.0
_MODEL = "deepseek-chat"
# 配图解释要能看图：DeepSeek 的视觉能力在 deepseek-flash 上（见官方 Vision 指南）
_VISION_MODEL = "deepseek-flash"
_IMG_MAX_BYTES = 8 * 1024 * 1024


class AIError(Exception):
    pass


def _extract_json(raw: str) -> dict:
    """从模型输出中提取 JSON 对象（兼容 markdown 围栏、前后说明文字、尾部多余内容）。

    解析失败时抛错，错误信息包含总长度与首尾片段（便于定位）。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    # 从第一个 { 起解析"第一个完整的 JSON 对象"（容忍尾部多余的说明文字）
    idx = text.find("{")
    if idx >= 0:
        try:
            data, _end = json.JSONDecoder().raw_decode(text[idx:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise AIError(
        f"模型输出不是合法 JSON（共 {len(text)} 字符）。"
        f"开头：{text[:200]} ｜ 结尾：{text[-200:]}"
    )


async def _chat(messages: list, temperature: float = 0.3, max_tokens: int = 2400,
                json_mode: bool = False, model: str | None = None) -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise AIError("DEEPSEEK_API_KEY 未配置。")
    payload = {
        "model": model or _MODEL,
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


def _paragraph_spans(content: str) -> list:
    """按换行切自然段，返回 [(start, end), ...]（每个自然段不含换行符）"""
    return [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", content)]


def _compose_cuts(paras: list, prefer_ends: set, total_len: int, max_chars: int) -> list:
    """把自然段组合成段并输出字符切点。

    - 硬约束：每段不超过 max_chars（在自然段边界切）
    - 软偏好：模型给出的语义边界（长度过半时提前切）
    """
    cuts = []
    start = 0
    for i, (para_start, para_end) in enumerate(paras):
        seg_len = para_end - start
        over_limit = seg_len >= max_chars
        preferred = i in prefer_ends and seg_len >= max_chars * 0.5
        if over_limit or preferred:
            if 0 < para_end < total_len:
                cuts.append(para_end)
            start = para_end
    return cuts


async def segment_article(title: str, content: str, max_chars: int = 600) -> dict:
    """把全文划分为适合逐段精读的小段。返回 {summary, cuts}（切点为字符位置）。

    实现要点：把自然段编号后交给模型做「语义分组」（它擅长的），字符位置由后端
    精确计算——模型不擅长数数，直接输出字符偏移会幻觉性跑偏。
    """
    paras = _paragraph_spans(content)
    if len(paras) <= 1:
        return {"summary": "", "cuts": []}
    listing = "\n".join(f"[{i}] {content[s:e]}" for i, (s, e) in enumerate(paras))
    prompt = (
        "下面是一篇知乎文章的全文，已按自然段编号（[0]、[1]、…）。\n\n"
        "请把它划分为适合逐段精读的小段，并给出全文主旨。\n\n"
        "划分规则：\n"
        "- 一段是一个完整的意思单元，目标是读者一次读完能理解\n"
        "- 参考粒度：一篇 3000 字的文章通常切成 6~10 段（每段约 400~600 字）\n"
        "- 只允许在自然段之间划分，不要在自然段内部切\n"
        "- 不要切得太碎（不要把每个自然段单独成段）\n\n"
        "只返回 JSON："
        '{"summary": "一句全文主旨", "breaks": [结束自然段的编号数组]}\n\n'
        '例如 "breaks": [2, 5] 表示：第 1 段 = 自然段 0~2，第 2 段 = 自然段 3~5，'
        "第 3 段 = 自然段 6 到末尾。\n"
        "breaks 从小到大、不重复、不包含最后一个自然段的编号。\n\n"
        f"标题：{title}\n\n正文：\n{listing}"
    )
    raw = await _chat(
        [{"role": "system", "content": "你是严谨的中文阅读教练，只输出要求的 JSON。"},
         {"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=2000, json_mode=True,
    )
    data = _extract_json(raw)
    prefer_ends = set()
    for value in data.get("breaks") or []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(paras) - 1:
            prefer_ends.add(n)
    cuts = _compose_cuts(paras, prefer_ends, len(content), max_chars)
    return {"summary": str(data.get("summary") or "").strip(), "cuts": cuts}


_EXPLAIN_SYSTEM = ("你是一位耐心、克制的中文精读教练。你的解释要准确、平实、不夸张。"
                   "涉及数学公式时，行内公式用 $…$ 包裹、独立成行的公式用 $$…$$ 包裹；"
                   "不要输出裸露的 LaTeX 命令（前端按这两个分隔符渲染公式）。")

_VISION_SYSTEM = ("你是一位耐心、克制的中文精读教练。学生把文章里的插图拿给你看，"
                  "你只说明这张图本身、以及该看它哪里；不替他把正文的推理讲完。")


def _sniff_mime(raw: bytes) -> str:
    """按文件头判断图片格式（知乎 CDN 有时不给 content-type）"""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def fetch_image_data_url(url: str) -> str:
    """把配图抓成 base64 data URL。

    不走「让模型自己去下载」那条路：知乎 CDN 有防盗链，服务端带空 Referer 抓更稳，
    也能顺手卡住大小。调用方必须先校验 URL 属于这篇文章，否则会变成任意 URL 抓取器。
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Referer": ""})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AIError(f"配图下载失败：{exc}") from exc
    raw = resp.content or b""
    if not raw:
        raise AIError("配图内容为空。")
    if len(raw) > _IMG_MAX_BYTES:
        raise AIError("配图过大，暂不支持解释。")
    mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
    if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        mime = _sniff_mime(raw)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


async def explain_image(title: str, summary: str, before: str, after: str,
                        data_url: str) -> str:
    """解释一张原文配图：结合它在原文中的上下文（图片插在这两段之间），说清它是什么、该看哪里"""
    if before or after:
        where = ("这张图在原文中的位置：下面是它的上文与下文，图片就插在这两段之间。\n"
                 + (f"【上文结尾处】\n…{before}\n\n" if before else "")
                 + (f"【下文开头处】\n{after}…\n\n" if after else ""))
    else:
        where = "（这张图在原文中的位置没有记录，只能依据标题与主旨理解）\n\n"
    prompt = (
        "用户正在逐段精读一篇文章，把文中的一张配图拿给你看。\n\n"
        f"文章标题：{title}\n"
        f"全文主旨：{summary or '（未知）'}\n\n"
        + where
        + "要求：\n"
        "1. 结合上下文说清这张图是什么：示意图 / 流程图 / 图表 / 截图 / 照片，以及它在讲什么\n"
        "2. 再说该看哪里：关键元素、标注或坐标轴在表达什么；是图表就只描述趋势与量级，"
        "不替读者下结论\n"
        "3. 点明它与上下文的关系（例如它正好对应上文哪一句、展开或印证了什么）\n"
        "4. 不复述正文、不扩展到图外内容；不超过 250 字，平实的中文\n"
        "5. 图太模糊或看不出内容时直接说明看不清，不要猜"
    )
    return (await _chat([
        {"role": "system", "content": _VISION_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ], temperature=0.4, max_tokens=1100, model=_VISION_MODEL)).strip()


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


# ---- 自测（读完之后的检测环节） ----

_QUIZ_SYSTEM = (
    "你是出题助手。你只输出 JSON，不输出任何解释性文字。"
    "题目必须严格依据给定的原文，不得引入原文没有的事实。"
)


async def quiz_summary(title: str, summary: str, content: str) -> str:
    """自测开头的全文概括（从学习记录重新进入时，先靠它把内容回忆起来）"""
    prompt = (
        "用户刚读完下面这篇文章，现在要开始自测。先写一段概括，帮他把内容回忆起来。\n\n"
        f"标题：{title}\n"
        f"（已有主旨：{summary or '（未知）'}）\n\n"
        f"全文：\n{content}\n\n"
        "要求：\n"
        "1. 说清这篇文章的论证脉络：从什么问题出发、用什么论据、得到什么结论\n"
        "2. 平实的中文，一段话，不超过 200 字\n"
        "3. 不添加原文之外的评价，不做推荐"
    )
    return (await _chat([{"role": "user", "content": prompt}],
                        temperature=0.3, max_tokens=900)).strip()


async def quiz_questions(title: str, summary: str, content: str, count: int = 5) -> dict:
    """自测题目：模型自己决定考知识记忆还是概念辨析，以及每题用判断还是选择。

    约束（由 _normalize_questions 校验，不信任模型自述）：
    - 只收 judgment（判断）/ choice（选择）两种题型；choice 恰好 4 个选项、answer 为 0-3 下标
    - judgment 的 answer 只收 true / false
    - 没有解析的题丢弃——宁可少出，不出错题
    """
    prompt = (
        "用户刚读完下面这篇文章，现在要自测他是否真的记住了。请出题。\n\n"
        f"标题：{title}\n"
        f"全文主旨：{summary or '（未知）'}\n\n"
        f"全文：\n{content}\n\n"
        "出题要求：\n"
        f"1. 共 {count} 道题，从不同段落取材，覆盖全文而不是集中在开头\n"
        "2. 每道题你自己判断考什么：knowledge（知识记忆——原文直接给出的事实、定义、结论）"
        "或 concept（概念辨析——容易混淆的两个概念、或某个结论的适用条件）\n"
        "3. 每道题你自己判断用什么题型：judgment（判断题，考一个陈述的真假）"
        "或 choice（选择题，四选一）。哪种最能测出是否真懂就用哪种\n"
        "4. 干扰项要用原文里真实出现的相近概念，不能是明显荒谬的选项\n"
        "5. 每题给一段 explanation：说明为什么是这个答案、容易错在哪。"
        "不复述原文，不扩展到原文之外\n\n"
        "只输出 JSON：\n"
        '{"questions": [\n'
        '  {"kind": "judgment", "focus": "knowledge", "stem": "题干（一个陈述）", '
        '"answer": true, "explanation": "解析"},\n'
        '  {"kind": "choice", "focus": "concept", "stem": "题干（一个问题）", '
        '"options": ["选项A", "选项B", "选项C", "选项D"], "answer": 0, "explanation": "解析"}\n'
        "]}\n"
        "judgment 的 answer 是 true 或 false；choice 的 answer 是正确选项下标（0 开始）。"
    )
    raw = await _chat([{"role": "system", "content": _QUIZ_SYSTEM},
                       {"role": "user", "content": prompt}],
                      temperature=0.5, max_tokens=3000, json_mode=True)
    return _normalize_questions(_extract_json(raw).get("questions"), count)


_TRUE_WORDS = {"true", "对", "正确", "是", "yes"}
_FALSE_WORDS = {"false", "错", "错误", "否", "不对", "no"}


def _normalize_questions(raw, count: int) -> dict:
    """把模型输出规整成可用的题目；任何字段不完整的题直接丢弃"""
    questions = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if not stem or not explanation:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        focus = str(item.get("focus") or "").strip().lower()
        if focus not in ("knowledge", "concept"):
            focus = "knowledge"
        q = {"kind": kind, "focus": focus, "stem": stem, "explanation": explanation}
        if kind == "judgment":
            answer = item.get("answer")
            if isinstance(answer, bool):
                q["answer"] = answer
            elif isinstance(answer, str) and answer.strip().lower() in _TRUE_WORDS:
                q["answer"] = True
            elif isinstance(answer, str) and answer.strip().lower() in _FALSE_WORDS:
                q["answer"] = False
            else:
                continue        # 认不出的答案整题丢弃，不能默认成「错」
        elif kind == "choice":
            options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
            answer = item.get("answer")
            if len(options) != 4 or not isinstance(answer, int) or isinstance(answer, bool):
                continue
            if not 0 <= answer <= 3:
                continue
            q["options"] = options
            q["answer"] = answer
        else:
            continue
        questions.append(q)
        if len(questions) >= count:
            break
    return {"questions": questions}


async def quiz_explain(title: str, summary: str, question: str, chosen: str,
                       correct: str, explanation: str) -> str:
    """答错时的讲解：针对用户选的那个说清它错在哪，而不是笼统地复述正确答案"""
    prompt = (
        "用户自测时答错了一道题。请给他讲解。\n\n"
        f"标题：{title}\n"
        f"全文主旨：{summary or '（未知）'}\n\n"
        f"题目：{question}\n"
        f"正确答案：{correct}\n"
        f"用户的选择：{chosen}\n"
        f"（出题时准备的解析：{explanation}）\n\n"
        "要求：\n"
        "1. 先说清用户选的那个为什么不对——它错在哪个概念上，不要只说「错了」\n"
        "2. 再说明正确答案成立的理由\n"
        "3. 平实的中文，不超过 200 字；不延伸成一篇讲解，不替用户重读原文"
    )
    return (await _chat([{"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=1000)).strip()


# ---- 复习（一段概括性文字） ----

async def review_summary(title: str, summary: str, content: str,
                         weak_points: list | None = None,
                         questions: list | None = None) -> str:
    """复习用的概括：讲清全文脉络，并针对本人的薄弱点指出易错与不易懂处。

    通用版（weak_points 与 questions 都为空）：只据原贴内容，帮用户理解容易卡住的地方。
    个性化版：把最近一轮答错/跳过的题拼进 prompt——复习的价值就在"针对你"。
    """
    weak_points = weak_points or []
    questions = questions or []
    prompt = (
        "用户正在复习一篇他读过的文章。请写一段概括性文字。\n\n"
        f"标题：{title}\n"
        f"全文主旨：{summary or '（未知）'}\n\n"
        f"全文：\n{content}\n\n"
    )
    if weak_points:
        lines = []
        for w in weak_points:
            kind = "跳过未答" if w.get("skipped") else "答错了"
            lines.append(f"- [{kind}] 题目：{w.get('stem') or ''}\n"
                         f"  正确答案：{w.get('answer') or ''}"
                         + (f"\n  他选了：{w.get('chosen') or ''}" if w.get("chosen") else ""))
        prompt += ("他刚做完自测，下面这些题他答错或跳过了——**这些就是他的薄弱点**：\n"
                   + "\n".join(lines) + "\n\n")
    if questions:
        prompt += ("他读这篇文章时还问过下面这些问题（说明他当时卡在这里）：\n"
                   + "\n".join(f"- {q}" for q in questions) + "\n\n")
    prompt += (
        "写作要求：\n"
        "1. 先说清这篇文章的脉络：从什么问题出发、用什么论据、得到什么结论\n"
        "2. 再针对上面那些薄弱点（如果有）讲清楚：这些地方为什么容易错、"
        "原文里对应的是哪一层意思、正确的理解应该抓住什么\n"
        "3. 如果没有薄弱点信息，就着重讲这篇文章里最容易卡住读者的地方是什么\n"
        "4. 平实的中文，连续的一段话，不超过 350 字\n"
        "5. 不列条目、不加标题、不复述原文整句，不替用户重新读一遍"
    )
    return (await _chat([{"role": "user", "content": prompt}],
                        temperature=0.4, max_tokens=1200)).strip()
