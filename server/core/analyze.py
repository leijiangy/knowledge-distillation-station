# -*- coding: utf-8 -*-
"""三指标计算（纯函数，口径见企划书 D2：分开展示，不合成综合分）。

- 认可度：接口三计数（赞/评/藏）对数缩放加权，收藏权重最高
- 信息量：摘要长度 + 结构信号 + 引用密度启发式
- 准确性（v1）：基于公开信号估算（作者信息完整度、内容类型、外部引用），
  并返回 basis 命中的信号清单保证透明；完整版将接入搜索接口认证标交叉验证
"""
import math
import re

URL_PATTERN = re.compile(r"https?://[^\s，。）)]+")


def _log_score(value: int | float, cap: float = 1000.0) -> float:
    """对数缩放：0 → 0；cap → 100（收藏数/赞同数这类长尾分布用对数更合理）"""
    if value is None or value <= 0:
        return 0.0
    return round(100.0 * math.log1p(value) / math.log1p(cap), 1)


def approval(item: dict) -> dict:
    """认可度：收藏 0.5 + 赞同 0.3 + 评论 0.2"""
    favorite = int(item.get("FavoriteCount") or 0)
    like = int(item.get("LikeCount") or 0)
    comment = int(item.get("CommentCount") or 0)
    score = 0.5 * _log_score(favorite) + 0.3 * _log_score(like) + 0.2 * _log_score(comment)
    return {
        "score": round(min(100.0, score), 1),
        "basis": [f"收藏 {favorite}", f"赞同 {like}", f"评论 {comment}"],
    }


def richness(item: dict) -> dict:
    """信息量：摘要厚度 + 结构信号 + 引用密度"""
    summary = str(item.get("Summary") or "")
    title = str(item.get("Title") or "")
    basis = []

    length_score = min(70.0, len(summary) / 3.0)  # 约 210 字到顶
    basis.append(f"摘要 {len(summary)} 字")

    structure_bonus = 0.0
    if len(summary) > 80 and ("\n" in summary or "。" in summary[:200]):
        structure_bonus += 8.0
    if re.search(r"[一二三四五六七八九十]、|\d[.、)]", summary):
        structure_bonus += 7.0  # 有分点/编号结构
        basis.append("含分点结构")

    links = URL_PATTERN.findall(summary)
    link_bonus = min(15.0, 5.0 * len(links))
    if links:
        basis.append(f"含 {len(links)} 个引用链接")

    if len(title) > 15:
        structure_bonus += 5.0

    score = min(100.0, length_score + structure_bonus + link_bonus)
    return {"score": round(score, 1), "basis": basis}


def credibility(item: dict) -> dict:
    """准确性 v1：公开信号代理（透明列出依据）"""
    basis = []
    score = 0.0
    author = item.get("Author") or {}
    if author.get("Name"):
        score += 40.0
        basis.append("作者信息完整")
    if author.get("Headline"):
        score += 15.0
        basis.append("作者有签名介绍")
    content_type = str(item.get("ContentType") or "")
    if content_type == "article":
        score += 10.0
        basis.append("知乎专栏文章")
    summary = str(item.get("Summary") or "")
    if URL_PATTERN.search(summary):
        score += 25.0
        basis.append("含可核验的外部引用")
    if not basis:
        basis.append("公开信号不足")
    return {
        "score": round(min(100.0, score), 1),
        "basis": basis,
        "note": "v1 基于公开信号估算，完整版将接入认证标交叉验证",
    }


def metrics_for(item: dict) -> dict:
    """单条收藏的三指标（分开展示，不合成）"""
    return {
        "approval": approval(item),
        "richness": richness(item),
        "credibility": credibility(item),
    }


# 综合分权重（仅用于排序，不在卡片上展示；调整需在企划书变更记录登记）
COMBINED_WEIGHTS = {"approval": 0.4, "richness": 0.35, "credibility": 0.25}


def combined_score(metrics: dict) -> float:
    """排序用综合分 = 三指标加权平均（0.4 / 0.35 / 0.25）"""
    total = 0.0
    for kind, weight in COMBINED_WEIGHTS.items():
        metric = metrics.get(kind) or {}
        total += float(metric.get("score") or 0) * weight
    return round(total, 1)


def analyze_items(items: list) -> list:
    """批量附加三指标；不改动原始字段"""
    result = []
    for item in items:
        enriched = dict(item)
        enriched["metrics"] = metrics_for(item)
        result.append(enriched)
    return result
