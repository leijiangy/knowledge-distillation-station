# -*- coding: utf-8 -*-
"""三指标纯函数测试"""
from core.analyze import analyze_items, approval, credibility, metrics_for, richness

SAMPLE = {
    "ContentType": "answer",
    "Url": "https://www.zhihu.com/answer/123",
    "LikeCount": 282,
    "CommentCount": 21,
    "FavoriteCount": 462,
    "Title": "统计学有没有很接近纯数前沿的理论？",
    "Summary": "这个问题可以从三个层面来回答。\n第一，统计学的数学基础是测度论与概率论，"
               "参考 https://arxiv.org/abs/xxxx 的讨论；第二，非参数统计与经验过程理论……",
    "Author": {"Name": "某作者", "Headline": "统计学研究者"},
}


def test_approval_prefers_favorite():
    high_fav = approval({"FavoriteCount": 1000, "LikeCount": 0, "CommentCount": 0})
    high_like = approval({"FavoriteCount": 0, "LikeCount": 1000, "CommentCount": 0})
    assert high_fav["score"] > high_like["score"]


def test_approval_zero_safe():
    assert approval({})["score"] == 0.0


def test_richness_rewards_length_and_links():
    thin = richness({"Title": "短", "Summary": "很短"})
    thick = richness(SAMPLE)
    assert thick["score"] > thin["score"]
    assert any("引用链接" in b for b in thick["basis"])


def test_credibility_lists_basis():
    result = credibility(SAMPLE)
    assert result["score"] > 0
    assert result["basis"]
    assert credibility({})["basis"] == ["公开信号不足"]


def test_analyze_items_keeps_original_fields():
    enriched = analyze_items([SAMPLE])
    assert enriched[0]["Title"] == SAMPLE["Title"]
    assert set(enriched[0]["metrics"].keys()) == {"approval", "richness", "credibility"}
    assert "score" in metrics_for(SAMPLE)["approval"]
